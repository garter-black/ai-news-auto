#!/usr/bin/env python3
"""デプロイ直後の「1回目の表示」の速さを本番で確かめる（台帳 W29）。

  python3 scripts/cold_check.py version                 # 今の版（x-saisoku-ver）を出す（デプロイ前に控える）
  python3 scripts/cold_check.py check --previous <版>   # 新しい版に切り替わるのを待ってから測る（deploy_cloudflare.yml）
  python3 scripts/cold_check.py check --n 40            # 手元で測る

サイトマップから選んだページ（毎回入れる決まったページ＋無作為の n 本）を、エッジのキャッシュに無い
URL（?_cold=<乱数>）で開き、最初の1バイトが届くまでの時間（TTFB）を測る。デプロイ直後に読者・検索の
ロボットが初めて開いたときと同じ道を通る（Workers → OpenNext のキャッシュ → 無ければ描画）。

落とす条件（どれか1つで終了コード 1。GitHub Actions のデプロイが赤になり、オーナーに知らせが届く）:
  - x-nextjs-cache: MISS … ビルドで作っておくべきページを、アクセスが来てから作っている（遅さの根本の原因。2026-11-04）
  - 5xx・時間切れ
  - LIMIT 秒を超え、新しい乱数で開き直しても LIMIT 秒を超えた（1回目だけ遅いものは注意として出す）
計測は自社の確かめなので、X-Saisoku-Internal: 1 と ?_internal=1 を付けて計測から外す。標準ライブラリだけで動く。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

LIMIT = 2.0  # 秒。オーナーの目安「どのページも2秒以内」
TIMEOUT = 30.0
# 2026-11-04 の点検で遅かったページ（約53秒・時間切れ）。毎回測る
ALWAYS = ["/", "/articles/openai-gpt-6-astra-comprehensive-guide-matome", "/products/chatgpt-voice"]
HEADERS = {"X-Saisoku-Internal": "1", "User-Agent": "saisoku-cold-check/1.0"}


def site_url() -> str:
    return (os.environ.get("NEXT_PUBLIC_SITE_URL") or "https://ai-tech-express.jp").rstrip("/")


def with_params(path: str, nonce: str | None = None) -> str:
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}_internal=1" + (f"&_cold={nonce}" if nonce else "")


def fetch(base: str, path: str, nonce: str | None = None) -> dict:
    """1回開いて TTFB・状態・キャッシュの印を返す"""
    url = base + with_params(path, nonce)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=TIMEOUT) as res:
            res.read(1)
            ttfb = time.monotonic() - started
            headers = {k.lower(): v for k, v in res.headers.items()}
            res.read()
            status = res.status
    except urllib.error.HTTPError as error:
        ttfb = time.monotonic() - started
        headers = {k.lower(): v for k, v in error.headers.items()}
        status = error.code
    except Exception as error:  # 時間切れ・接続の失敗
        return {"path": path, "status": 0, "ttfb": round(time.monotonic() - started, 2), "cache": "", "ver": "", "error": type(error).__name__}
    return {
        "path": path,
        "status": status,
        "ttfb": round(ttfb, 2),
        "cache": headers.get("x-nextjs-cache", ""),
        "ver": headers.get("x-saisoku-ver", ""),
        "error": "",
    }


def sitemap_paths(xml: str, base: str) -> list[str]:
    """サイトマップの <loc> をサイト内のパスにする（分割されたサイトマップの目次なら子のサイトマップの URL を返す）"""
    out = []
    for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
        if not loc.startswith(base):
            continue
        out.append(loc[len(base):] or "/")
    return out


def load_sitemap(base: str) -> list[str]:
    def get(path: str) -> str:
        with urllib.request.urlopen(urllib.request.Request(base + with_params(path), headers=HEADERS), timeout=TIMEOUT) as res:
            return res.read().decode("utf-8", "replace")

    xml = get("/sitemap.xml")
    paths = sitemap_paths(xml, base)
    if "<sitemapindex" in xml:
        children = paths
        paths = []
        for child in children:
            paths += sitemap_paths(get(child), base)
    return paths


def choose(paths: list[str], n: int, seed: str | None = None) -> list[str]:
    """毎回入れる決まったページ＋サイトマップから無作為に n 本（重ならない）"""
    rest = [p for p in dict.fromkeys(paths) if p not in ALWAYS]
    rnd = random.Random(seed)
    return ALWAYS + rnd.sample(rest, min(n, len(rest)))


def judge(first: dict, again: dict | None, limit: float = LIMIT) -> tuple[str, str]:
    """('fail' | 'warn' | 'ok', 理由)"""
    if first["error"] or first["status"] == 0:
        return "fail", f"開けない（{first['error'] or '接続の失敗'}・{first['ttfb']}秒）"
    if first["status"] >= 400:
        # サイトマップに載るページは 200 で開くはず（404・410 を合格にしない。W3・W25。2026-11-04 一貫性の点検）
        return "fail", f"状態 {first['status']}"
    if first["cache"] == "MISS":
        return "fail", "ビルドで作っておらず、アクセスが来てから作った（x-nextjs-cache: MISS）"
    if first["ttfb"] > limit:
        if again is None or again["error"] or again["ttfb"] > limit:
            second = "" if again is None else f"・開き直しても {again['ttfb']}秒"
            return "fail", f"{first['ttfb']}秒（{limit}秒を超えた{second}）"
        return "warn", f"1回目だけ {first['ttfb']}秒（開き直すと {again['ttfb']}秒。通信の揺れの可能性）"
    return "ok", ""


def wait_for_new_version(base: str, previous: str, seconds: int = 120) -> str:
    deadline = time.monotonic() + seconds
    current = ""
    while time.monotonic() < deadline:
        current = fetch(base, "/", nonce=str(random.randrange(10**12)))["ver"]
        if current and current != previous:
            return current
        time.sleep(5)
    return current


def check(args) -> int:
    base = site_url()
    if args.previous:
        version = wait_for_new_version(base, args.previous)
        print(f"版: {args.previous[:8]} → {version[:8] or '(不明)'}", flush=True)
        if not version or version == args.previous:
            # 新しい版に切り替わっていない → 古い版を測って「合格」にしない（確かめられない検査は不合格）
            print("::error::デプロイのあとも新しい版に切り替わらない（古い版を測ることになるので不合格）", flush=True)
            return 1
    listed = load_sitemap(base)
    if len(listed) < 10:
        print(f"::error::サイトマップから測るページを選べない（{len(listed)} 本）", flush=True)
        return 1
    paths = choose(listed, args.n, args.seed)
    print(f"{len(paths)} ページを、エッジのキャッシュに無い URL で開きます（同時 {args.concurrency}・上限 {args.limit}秒）", flush=True)

    def one(path: str) -> dict:
        first = fetch(base, path, nonce=str(random.randrange(10**12)))
        again = fetch(base, path, nonce=str(random.randrange(10**12))) if first["ttfb"] > args.limit and not first["error"] else None
        verdict, reason = judge(first, again, args.limit)
        return {**first, "verdict": verdict, "reason": reason}

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(one, paths))

    times = sorted(r["ttfb"] for r in results)
    fails = [r for r in results if r["verdict"] == "fail"]
    warns = [r for r in results if r["verdict"] == "warn"]
    median = times[len(times) // 2] if times else 0
    lines = [
        "## デプロイ直後の1回目の表示（台帳 W29）",
        "",
        f"- {len(results)} ページ・最大 {times[-1] if times else 0}秒・中央値 {median}秒・{args.limit}秒超え {sum(t > args.limit for t in times)} 本",
        f"- 落ちた {len(fails)} 本・注意 {len(warns)} 本",
        "",
        "| ページ | 秒 | 状態 | キャッシュ | 判定 |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda r: -r["ttfb"]):
        mark = {"fail": "✗ " + r["reason"], "warn": "△ " + r["reason"], "ok": "✓"}[r["verdict"]]
        lines.append(f"| `{r['path']}` | {r['ttfb']} | {r['status'] or r['error']} | {r['cache'] or '-'} | {mark} |")
    report = "\n".join(lines)
    print(report, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=1)
    for r in warns:
        print(f"::warning::{r['path']} {r['reason']}", flush=True)
    for r in fails:
        print(f"::error::デプロイ直後の表示が遅い・作っていない: {r['path']} {r['reason']}", flush=True)
    return 1 if fails else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version")
    c = sub.add_parser("check")
    c.add_argument("--previous", default="", help="デプロイ前の版。違う版に切り替わるまで待ってから測る")
    c.add_argument("--n", type=int, default=30)
    c.add_argument("--concurrency", type=int, default=4)
    c.add_argument("--limit", type=float, default=LIMIT)
    c.add_argument("--seed", default=None)
    c.add_argument("--json", default="")
    args = parser.parse_args()
    if args.command == "version":
        print(fetch(site_url(), "/", nonce=str(random.randrange(10**12)))["ver"])
        return 0
    return check(args)


if __name__ == "__main__":
    sys.exit(main())
