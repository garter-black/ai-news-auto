#!/usr/bin/env python3
"""
記事に「外から来た人」の数を、流入元・記事・SNS の投稿ごとに出す（自前計測。毎晩・毎朝の報告用）。

  python3 scripts/inflow_report.py --yesterday        # 昨日（日本時間）
  python3 scripts/inflow_report.py --date 2026-11-03
  python3 scripts/inflow_report.py                    # 今日（途中まで）
  python3 scripts/inflow_report.py --yesterday --top 5
  python3 scripts/inflow_report.py --date 2026-11-03 --json   # 生の JSON

数えるもの: 記事・朝のまとめ・トップページを「そのタブで最初に開いた」1回（外からの入口）。JS が動いた実ブラウザだけ。
数えないもの: ロボット・SNS のリンクプレビュー・自動ブラウザ（HeadlessChrome・webdriver）・Lighthouse・PageSpeed・
  JS の動かないアクセス・自社のテスト（?_internal=1 を付けて開いたブラウザ、ヘッダー X-Saisoku-Internal: 1）。
  同じ人が同じページを30分以内に開き直したものは1回。人数は「日付+IP+UA」のハッシュによる推定（日をまたいで足さない）。
流入元: utm_source があればそれ（threads / x / facebook / bluesky / newsletter / line）、無ければ参照元のサイト
  （google / yahoo / bing / discover / news / direct / other）。

鍵 AFF_STATS_TOKEN は環境変数か .env.local（メインのフォルダ）から読む（scripts/aff_stats.py と同じ）。中身は表示しない。
数えるのは Worker（cloudflare/inflow.ts）、送るのは画面の components/InflowBeacon.tsx。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aff_stats import JST, load_token  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

LABELS = {
    "threads": "Threads", "x": "X", "facebook": "Facebook", "bluesky": "Bluesky", "google": "Google検索",
    "yahoo": "Yahoo!検索", "bing": "Bing", "discover": "Googleアプリ/Discover", "news": "ニュースアプリ",
    "newsletter": "メルマガ", "line": "LINE", "direct": "直接・不明", "other": "その他のサイト",
}
SNS = ("threads", "x", "facebook", "bluesky")


def site() -> str:
    return os.environ.get("SITE_URL", "https://ai-tech-express.jp").rstrip("/")


def fetch(token: str, date: str, fresh: bool = False) -> dict:
    req = urllib.request.Request(
        f"{site()}/api/aff/inflow?date={date}" + ("&fresh=1" if fresh else ""),
        # 自社のアクセスなので、念のため数えないしるしを付ける
        headers={"Authorization": f"Bearer {token}", "User-Agent": "SaisokuInflowReport/1.0", "X-Saisoku-Internal": "1"},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read())


def title_of(page: str, width: int = 26) -> str:
    if page == "_home":
        return "トップページ"
    if page.startswith("digest-"):
        d = page[len("digest-"):]
        return f"朝のまとめ {d[4:6]}/{d[6:8]}" if len(d) == 8 else page
    try:
        data = json.loads((ROOT / "content" / "articles" / f"{page}.json").read_text("utf-8"))
        text = str(data.get("title") or page)
    except (OSError, ValueError):
        text = page
    return text if len(text) <= width else text[: width - 1] + "…"


def jst(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(JST)
    except (TypeError, ValueError):
        return None


def load_state(name: str) -> dict:
    try:
        return json.loads((ROOT / "state" / name).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def views_of(rows: list[dict], page: str, source: str, campaign: str | None = None, content: str | None = None) -> int:
    return sum(
        r["views"] for r in rows
        if r["page"] == page and r["source"] == source
        and (campaign is None or r.get("campaign", "") == campaign)
        and (content is None or r.get("content", "") == content)
    )


def threads_posts(data: dict, posted: dict, videos: dict) -> list[dict]:
    """Threads の投稿ごとの結果。その日に出した投稿と、その日に Threads から訪問があった投稿（前の日の投稿も）"""
    date, rows = data["date"], data.get("rows") or []
    visited = {r["page"] for r in rows if r["source"] == "threads"}
    out = []
    for slug, rec in posted.items():
        when = jst(rec.get("postedAt") or rec.get("sentAt") or "")
        if not when or not (when.date().isoformat() == date or slug in visited):
            continue
        if when.date().isoformat() > date:
            continue
        # 動画の投稿（sns_video）は下の動画の行で数える
        total = views_of(rows, slug, "threads") - views_of(rows, slug, "threads", "sns_video")
        metrics = rec.get("metrics") or {}
        out.append({
            "slug": slug, "at": when, "kind": rec.get("format") or rec.get("style") or "",
            "visits": total, "utm": total - views_of(rows, slug, "threads", ""),
            "impressions": metrics.get("views") or 0,
        })
    for slug, rec in videos.items():
        when = jst(rec.get("postedAt") or "")
        variant = rec.get("variant")
        if rec.get("status") != "posted" or variant not in ("short", "full") or not when:
            continue
        hits = views_of(rows, slug, "threads", "sns_video", variant)
        if when.date().isoformat() == date or hits:
            if when.date().isoformat() > date:
                continue
            out.append({"slug": slug, "at": when, "kind": f"動画・{'短い版' if variant == 'short' else '全部の版'}", "visits": hits, "utm": hits, "impressions": 0})
    return sorted(out, key=lambda p: p["at"])


def show(data: dict, posted: dict, videos: dict, top: int = 3) -> None:
    rows = data.get("rows") or []
    print(f"■ {data['date']}（日本時間）記事などに外から来た人: {data['views']}回（推定 {data['visitors']}人）")
    print("  実ブラウザだけ。ロボット・リンクプレビュー・自動ブラウザ・Lighthouse・自社テストは除いています")
    if not data["views"]:
        print("  まだ記録がありません（計測は 2026-11-03 から）")
        return
    print("  流入元: " + " / ".join(f"{LABELS.get(r['source'], r['source'])} {r['views']}（{r['visitors']}人）" for r in data["bySource"]))
    sns_total = sum(r["views"] for r in data["bySource"] if r["source"] in SNS)
    print(f"  SNS 合計: {sns_total}回")

    print(f"  流入元ごとの人気ページ（上位{top}）:")
    for src in [r["source"] for r in data["bySource"]]:
        pages: dict[str, int] = {}
        for r in rows:
            if r["source"] == src:
                pages[r["page"]] = pages.get(r["page"], 0) + r["views"]
        best = sorted(pages.items(), key=lambda kv: -kv[1])[:top]
        print(f"    {LABELS.get(src, src)}: " + "、".join(f"「{title_of(p, 22)}」{n}" for p, n in best))

    camps: dict[tuple[str, str, str], int] = {}
    for r in rows:
        if r.get("campaign") and r["source"] in SNS:
            k = (r["source"], r["campaign"], r.get("content", ""))
            camps[k] = camps.get(k, 0) + r["views"]
    if camps:
        print("  SNS の投稿の型ごと（utm_campaign）: " + "、".join(
            f"{LABELS.get(s, s)}/{c}{'/' + t if t else ''} {n}" for (s, c, t), n in sorted(camps.items(), key=lambda kv: -kv[1])
        ))

    posts = threads_posts(data, posted, videos)
    if posts:
        print("  Threads の投稿ごと（この日の投稿と、この日に訪問があった投稿）:")
        zero = [p for p in posts if not p["visits"]]
        for p in [p for p in posts if p["visits"]]:
            rate = f" / 表示 {p['impressions']} → {p['visits'] / p['impressions']:.1%}" if p["impressions"] else ""
            other = p["visits"] - p["utm"]
            note = f"（うち投稿のリンク {p['utm']}・参照元だけ {other}）" if other else ""
            print(f"    {p['at']:%m/%d %H:%M} {p['kind'] or '-'}「{title_of(p['slug'])}」 訪問 {p['visits']}{note}{rate}")
        if zero:
            print(f"    訪問0: {len(zero)}本（" + "、".join(f"{p['at']:%H:%M} {p['kind'] or '-'}" for p in zero) + "）")

    video: dict[str, dict[str, int]] = {}
    for r in rows:
        if r.get("campaign") == "sns_video" and r.get("content") in ("short", "full"):
            video.setdefault(r["page"], {"short": 0, "full": 0})[r["content"]] += r["views"]
    if video:
        s = sum(v["short"] for v in video.values())
        f = sum(v["full"] for v in video.values())
        print(f"  動画投稿から: 短い版 {s}回 / 全部の版 {f}回")
        for page, v in sorted(video.items(), key=lambda kv: -(kv[1]["short"] + kv[1]["full"]))[:top]:
            print(f"    「{title_of(page)}」 短い版 {v['short']} / 全部の版 {v['full']}")
    if data.get("truncated"):
        print("  注意: 集計の上限に達しました。全部は数えられていません。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="YYYY-MM-DD（日本時間）")
    parser.add_argument("--yesterday", action="store_true")
    parser.add_argument("--top", type=int, default=3, help="流入元ごとに出すページ数")
    parser.add_argument("--fresh", action="store_true", help="保存済みの集計を使わず数え直す")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    token = load_token()
    if not token:
        print("[inflow] AFF_STATS_TOKEN が見つかりません（環境変数か .env.local に入れてください）", file=sys.stderr)
        return 1
    day = datetime.now(JST).date()
    if args.yesterday:
        day -= timedelta(days=1)
    if args.date:
        day = datetime.strptime(args.date, "%Y-%m-%d").date()
    try:
        data = fetch(token, day.isoformat(), args.fresh)
    except urllib.error.HTTPError as error:
        print(f"[inflow] 取得に失敗しました: HTTP {error.code}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"[inflow] 取得に失敗しました: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    show(data, load_state("threads_posted.json").get("posted") or {}, load_state("sns_video.json").get("videos") or {}, max(1, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
