"""Bluesky のプロフィール（表示名・自己紹介・ヘッダー画像）を設定する。アイコン（avatar）など他の項目はそのまま残す

  python3 scripts/bluesky_profile.py --dry-run   # 今のプロフィールと変更内容を表示（書き換えない）
  python3 scripts/bluesky_profile.py             # 書き換える（BLUESKY_HANDLE / BLUESKY_APP_PASSWORD が必要）
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bluesky_post as bp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BANNER = ROOT / "public" / "brand" / "bluesky-banner.png"
DISPLAY_NAME = "サイソクニュース"
DESCRIPTION = """気づけば、周りはもう動いてる
そうならないための最速ニュース⚡
忙しくても、サイソクで「旬」がわかる
何ができるか、どう使うか、何に役立つかが一瞬で
ai-tech-express.jp"""


def get_profile(session: dict) -> tuple[dict, str | None]:
    query = urllib.parse.urlencode({"repo": session["did"], "collection": "app.bsky.actor.profile", "rkey": "self"})
    try:
        res = bp.xrpc(f"com.atproto.repo.getRecord?{query}", token=session["accessJwt"])
    except urllib.error.HTTPError as error:
        if error.code == 400:  # まだプロフィールの記録がない
            return {"$type": "app.bsky.actor.profile"}, None
        raise
    return res.get("value") or {"$type": "app.bsky.actor.profile"}, res.get("cid")


def summary(record: dict) -> dict:
    return {k: (v if k in ("displayName", "description") else "(あり)") for k, v in record.items() if k != "$type"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if len(DESCRIPTION) > 256:
        raise SystemExit("自己紹介が256文字を超えています")
    data = BANNER.read_bytes()
    if len(data) > bp.MAX_BLOB:
        raise SystemExit("ヘッダー画像が1MBを超えています")
    session = bp.login()
    bp.log(f"[profile] ログイン OK: {session.get('handle')}")
    current, cid = get_profile(session)
    bp.log(f"[profile] いま: {json.dumps(summary(current), ensure_ascii=False)}")
    record = dict(current)  # avatar・pinnedPost・labels などはそのまま
    record["$type"] = "app.bsky.actor.profile"
    record["displayName"] = DISPLAY_NAME
    record["description"] = DESCRIPTION
    if args.dry_run:
        bp.log(f"[profile] 変更後（ヘッダー画像は差し替え）: {json.dumps(summary(record), ensure_ascii=False)}")
        return 0
    record["banner"] = bp.xrpc("com.atproto.repo.uploadBlob", data, session["accessJwt"], "image/png")["blob"]
    body = {"repo": session["did"], "collection": "app.bsky.actor.profile", "rkey": "self", "record": record}
    if cid:
        body["swapRecord"] = cid  # 読んだ後に別の場所で変わっていたら上書きしない
    bp.xrpc("com.atproto.repo.putRecord", body, session["accessJwt"])
    bp.log(f"[profile] 更新しました: {json.dumps(summary(record), ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
