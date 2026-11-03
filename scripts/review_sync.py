#!/usr/bin/env python3
"""編集部の記事確認（cloudflare/review.ts が Workers KV の REVIEW_KV に残す記録）を記事 JSON に書き写す。

  r:<slug> の metadata {status, at}
    ok    → 記事 JSON に review = {"status": "ok", "reviewedAt": at, "reviewedBy": "編集部"}（記事ページに「編集部が確認済み」）
    fix   → review = {"status": "fix", "flaggedAt": at, "reviewedBy": "編集部"}（表示はしない）。GitHub に修正依頼の issue を1件作る
    clear → review を外す
毎回すべての記録と記事を突き合わせるので、記事が作り直されて review が消えても次の回で戻る（何度動かしても同じ結果）。
取り下げた記事（JSON が無い）は飛ばす。

  python3 scripts/review_sync.py              # 書き写す（変えた本数を出す）
  python3 scripts/review_sync.py --dry-run    # 変えずに表示だけ

環境変数: CLOUDFLARE_API_TOKEN（Workers KV の読み取り）, CLOUDFLARE_ACCOUNT_ID,
         GH_TOKEN（fix の issue を作るとき。無ければ issue は作らない）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = ROOT / "content" / "articles"
# wrangler.jsonc の REVIEW_KV（saisoku-review）と同じ
REVIEW_KV_ID = os.environ.get("REVIEW_KV_ID", "55e38a39255d4999b85163fabba29006")
SITE = "https://ai-tech-express.jp"
LABEL = "review-fix"


def api(path: str) -> bytes:
    account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/storage/kv/namespaces/{REVIEW_KV_ID}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


def list_records() -> dict[str, dict]:
    out: dict[str, dict] = {}
    cursor = ""
    for _ in range(50):
        query = {"prefix": "r:", "limit": "1000"}
        if cursor:
            query["cursor"] = cursor
        data = json.loads(api("/keys?" + urllib.parse.urlencode(query)))
        for key in data.get("result", []):
            meta = key.get("metadata") or {}
            if meta.get("status") in ("ok", "fix", "clear") and meta.get("at"):
                out[key["name"][2:]] = meta
        cursor = (data.get("result_info") or {}).get("cursor") or ""
        if not cursor:
            break
    return out


def record_value(slug: str) -> dict:
    try:
        return json.loads(api("/values/" + urllib.parse.quote(f"r:{slug}", safe="")))
    except Exception:  # noqa: BLE001 — メモが読めなくても issue は作る
        return {}


def wanted(meta: dict) -> dict | None:
    if meta["status"] == "ok":
        return {"status": "ok", "reviewedAt": meta["at"], "reviewedBy": "編集部"}
    if meta["status"] == "fix":
        return {"status": "fix", "flaggedAt": meta["at"], "reviewedBy": "編集部"}
    return None


def apply(records: dict[str, dict], dry_run: bool) -> tuple[list[str], list[tuple[str, dict, str]]]:
    changed: list[str] = []
    fixes: list[tuple[str, dict, str]] = []
    for slug, meta in sorted(records.items()):
        path = ARTICLES / f"{slug}.json"
        if not path.exists():
            continue
        article = json.loads(path.read_text(encoding="utf-8"))
        want = wanted(meta)
        if meta["status"] == "fix":
            fixes.append((slug, meta, article.get("title", "")))
        if article.get("review") == want or (want is None and "review" not in article):
            continue
        if want is None:
            article.pop("review", None)
        else:
            article["review"] = want
        changed.append(f"{slug} → {meta['status']}")
        if not dry_run:
            path.write_text(json.dumps(article, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed, fixes


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def open_fix_issues(fixes: list[tuple[str, dict, str]], dry_run: bool) -> int:
    if not fixes or not os.environ.get("GH_TOKEN") or dry_run:
        return 0
    gh("label", "create", LABEL, "--color", "c8221a", "--description", "編集部から記事の修正依頼（/review）", "--force")
    existing = json.loads(gh("issue", "list", "--label", LABEL, "--state", "all", "--limit", "500", "--json", "body"))
    bodies = "\n".join(i.get("body") or "" for i in existing)
    made = 0
    for slug, meta, title in fixes:
        marker = f"<!-- review-fix:{slug}:{meta['at']} -->"
        if marker in bodies:
            continue
        note = (record_value(slug).get("note") or "").strip() or "（メモなし）"
        body = (
            f"編集部が記事確認ページ（/review）で「直してほしい」を押しました。\n\n"
            f"- 記事: {SITE}/articles/{slug}?_internal=1\n- 押した時刻: {meta['at']}\n\n"
            f"### メモ\n\n{note}\n\n"
            "直したら content/corrections.json と本文の訂正表示を訂正方針（/corrections）どおりに入れ、"
            "この issue を閉じてください。直したあと、編集部が読み直して「確認済み」にできます。\n\n"
            f"{marker}"
        )
        gh("issue", "create", "--title", f"直してほしい（編集部）: {title[:80]}", "--label", LABEL, "--body", body)
        made += 1
    return made


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not os.environ.get("CLOUDFLARE_API_TOKEN") or not os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
        print("[review_sync] CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID がありません", file=sys.stderr)
        return 1
    records = list_records()
    changed, fixes = apply(records, args.dry_run)
    made = open_fix_issues(fixes, args.dry_run)
    print(f"[review_sync] 記録 {len(records)} 件 / 記事に反映 {len(changed)} 本 / 新しい修正依頼 {made} 件")
    for line in changed:
        print("  " + line)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"changed={len(changed)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
