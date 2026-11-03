#!/usr/bin/env python3
"""
@saisoku637 の自分の投稿に付いたコメント（返信）の一覧。朝のルーティンで返信の下書きを作り、オーナーが承認してから手で返す。

  python3 scripts/threads_inbox.py              # JSON を表示し、state/threads_inbox.json に保存（BUFFER_API_KEY が要る）
  python3 scripts/threads_inbox.py --days 3     # 何日前までの投稿を見るか（既定 7）

取れるもの・取れないもの（2026-10-29 に確認）:
- Buffer の API は投稿ごとのコメント数（metrics の comments）だけを返す。コメントした人・本文は返さない
  （この数には自分のスレッドの返信＝リンクの返信も入るので、threadCount - 1 を引いて他人のコメントだけにする）
- threads.com は robots.txt で全体を Disallow（自動収集は禁止）なので、ページの取得はしない
- Threads 公式 API（threads_read_replies）は Meta の電話番号認証が通らず使えない
→ コメントが付いた投稿（と前回からの増分）を JSON に出し、本文は Chrome で開いて人が確かめる（manual の手順）
- 自動で返信することは一切しない
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import threads_post as tp  # noqa: E402

INBOX_PATH = tp.ROOT / "state" / "threads_inbox.json"
MANUAL = [
    "Chrome で各 url（@saisoku637 の投稿）を開き、ログイン済みのアカウントで返信欄を読む",
    "または https://www.threads.com/activity （アクティビティ → 返信）で直近の返信をまとめて見る",
    "返信の下書きを作ってオーナーに見せ、承認されたものだけを手で投稿する（自動返信はしない）",
]


def fetch(api_key: str, since: datetime) -> list[dict]:
    """Buffer の Threads 投稿のうち since 以降に送ったもの（id・リンク・本文の冒頭・コメント数）"""
    import buffer_budget

    buffer_budget.set_role("monitor")  # 読むだけ（予約の分の回数を残す）
    out: list[dict] = []
    orgs = tp.buffer_graphql(api_key, "query { account { organizations { id } } }")["account"]["organizations"]
    for org in orgs:
        channels = tp.buffer_graphql(
            api_key, f"query {{ channels(input: {{ organizationId: {json.dumps(org['id'])} }}) {{ id service }} }}"
        )["channels"]
        ids = [c["id"] for c in channels if str(c.get("service", "")).lower() == "threads"]
        if not ids:
            continue
        cursor = None
        for _ in range(6):  # 最大 300 件
            after = f", after: {json.dumps(cursor)}" if cursor else ""
            data = tp.buffer_graphql(
                api_key,
                f"""query {{ posts(first: 50{after}, input: {{
                    organizationId: {json.dumps(org['id'])},
                    filter: {{ status: [sent], channelIds: {json.dumps(ids)} }}
                }}) {{
                    edges {{ node {{ id text sentAt externalLink metricsUpdatedAt metrics {{ type value }} metadata {{ ... on ThreadsPostMetadata {{ threadCount }} }} }} }}
                    pageInfo {{ endCursor hasNextPage }}
                }} }}""",
            )["posts"]
            for edge in data["edges"]:
                node = edge["node"]
                sent = node.get("sentAt")
                if sent and datetime.fromisoformat(sent.replace("Z", "+00:00")) >= since:
                    out.append(node)
            if not data["pageInfo"]["hasNextPage"]:
                break
            cursor = data["pageInfo"]["endCursor"]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    result: dict = {"generatedAt": now.isoformat(timespec="seconds"), "method": "buffer-metrics", "manual": MANUAL, "items": []}

    key = os.environ.get("BUFFER_API_KEY", "").strip()
    if not key:
        result["method"] = "manual"
        result["note"] = "BUFFER_API_KEY が無いため、コメント数を取れませんでした。manual の手順で確認してください"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    try:
        posts = fetch(key, now - timedelta(days=args.days))
    except Exception as error:  # noqa: BLE001
        result["method"] = "manual"
        result["note"] = f"Buffer から取得できませんでした: {error}"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    try:
        before = {i["id"]: i.get("comments", 0) for i in json.loads(INBOX_PATH.read_text("utf-8")).get("items", [])}
    except Exception:
        before = {}
    slugs = {v.get("id"): k for k, v in tp.load_state()["posted"].items() if v.get("id")}
    for node in posts:
        metrics = {m["type"]: m["value"] for m in node.get("metrics") or []}
        # Buffer のコメント数には、自分で付けた返信（リンクの返信など、スレッドの2件目以降）も入っている
        own = max(0, int((node.get("metadata") or {}).get("threadCount") or 1) - 1)
        comments = int(float(metrics.get("comments", 0) or 0)) - own
        if comments <= 0:
            continue
        result["items"].append({
            "id": node["id"],
            "slug": slugs.get(node["id"], ""),
            "post": str(node.get("text") or "").split("\n")[0][:80],
            "url": node.get("externalLink") or "",
            "sentAt": node.get("sentAt"),
            "comments": comments,
            # 前回の一覧から増えた数（初回は全部）
            "newComments": max(0, comments - int(before.get(node["id"], 0))),
            "views": metrics.get("views"),
            # Buffer はコメントした人・本文を返さない（manual の手順で確認する）
            "commenter": None,
            "text": None,
            "metricsUpdatedAt": node.get("metricsUpdatedAt"),
        })
    result["items"].sort(key=lambda i: (-i["newComments"], -i["comments"]))
    result["checkedPosts"] = len(posts)
    result["note"] = "コメント数は Buffer の集計（数時間おくれることがある）。本文と相手は url を Chrome で開いて確認する"
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if not args.no_save:
        INBOX_PATH.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
