"""
指定した記事を、いますぐ Threads・X・Facebook に出す（臨時の投稿用）。
GitHub Actions の「Post now」から記事の slug と本文を渡して使う。投稿履歴にも記録する。
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--text", default="", help="Threads・Facebook の本文（X は自動で短くする）")
    parser.add_argument("--delete", default="", help="取り消す Buffer の投稿ID（カンマ区切り。予約中のものだけ消える）")
    args = parser.parse_args()

    key = os.environ.get("BUFFER_API_KEY", "").strip()
    if not key:
        raise SystemExit("BUFFER_API_KEY が未設定です")
    for post_id in [p.strip() for p in args.delete.split(",") if p.strip()]:
        try:
            data = tp.buffer_graphql(key, """mutation($input: DeletePostInput!) {
              deletePost(input: $input) { __typename ... on MutationError { message } } }""", {"input": {"id": post_id}})
            print(f"[post-now] 取り消し {post_id}: {data}")
        except Exception as error:  # noqa: BLE001
            print(f"[post-now] 取り消し失敗 {post_id}: {error}")
    if not args.text:
        return 0
    path = tp.ARTICLES_DIR / f"{args.slug}.json"
    article = json.loads(path.read_text("utf-8"))
    link = f"{tp.site_url()}/articles/{args.slug}"
    text = args.text.replace("\\n", "\n").strip()

    channels = tp.buffer_channels(key)
    due = datetime.now(timezone.utc) + timedelta(minutes=2)
    # 2026-11-03 ルール1: Threads も本文の最後に記事のリンク。中身はルールの門番（sns_rules）が投稿の直前に確かめる
    result = tp.publish_via_buffer(key, channels["threads"]["id"], tp.with_link(text, article, "threads", "classic"), link, article, due_at=due)
    extra = tp.publish_extra_channels(key, channels, text, link, article, due_at=due)

    state = tp.load_state()
    # 手で書いた本文でも、最後の問いの型を言い回しから見分けて残す（台帳 S19。流入の報告で型ごとに比べる）
    last = tp.post_blocks_of(text)[-1].strip() if tp.post_blocks_of(text) else ""
    question = last if last.endswith(tp.QUESTION_END) else ""
    state["posted"][args.slug] = {
        **result, "dueAt": due.isoformat(timespec="seconds"), "channels": extra, "manual": True,
        "question": bool(question), **({"questionText": question} if question else {}), **tp.question_pattern_fields({}, question),
    }
    tp.save_state(state)
    print(f"[post-now] {args.slug}: threads {result.get('id')} / {extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
