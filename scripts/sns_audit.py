#!/usr/bin/env python3
"""SNS の実際の投稿を読むだけの点検（何も投稿・削除・変更しない）。

Buffer の送信済み・予約中の投稿（Threads / X / Facebook）と Bluesky の公開フィード（public.api.bsky.app）を読み、
scripts/sns_rules.py があればその判定で、ルール違反の候補を一覧にする。

  python3 scripts/sns_audit.py --days 4 --out out/sns_audit.json   # BUFFER_API_KEY が要る（Bluesky は鍵なし）

投稿の本文は公開済みの SNS の文面だが、ログには件数と違反の要約だけを出し、全文は --out のファイルに書く。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from threads_post import buffer_graphql  # noqa: E402

JST = timezone(timedelta(hours=9))
SERVICES = ("threads", "twitter", "facebook")
BSKY_FEED = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
BSKY_ACTOR = "ai-tech-express.jp"


def utc(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


# 読むフィールドは決め打ち（型の問い合わせは Buffer の回数制限を食うのでしない）。
# scripts/buffer_delivery_monitor.py と scripts/sns_video_followup.py で使えることを確かめたものだけ
POST_FIELDS = (
    "id text status channelId createdAt dueAt sentAt externalLink error { message } assets { type source mimeType } "
    "metadata { __typename ... on ThreadsPostMetadata { thread { text assets { type source mimeType } } } "
    "... on TwitterPostMetadata { thread { text assets { type source mimeType } } } }"
)


def post_fields(key: str) -> str:
    del key
    return POST_FIELDS


def buffer_posts(key: str, since: datetime) -> list[dict]:
    fields = post_fields(key)
    out = []
    for org in buffer_graphql(key, "query { account { organizations { id } } }")["account"]["organizations"]:
        oid = org["id"]
        channels = buffer_graphql(key, f"query {{ channels(input: {{ organizationId: {json.dumps(oid)} }}) {{ id service name }} }}")["channels"]
        selected = {c["id"]: str(c["service"]).lower() for c in channels if str(c.get("service", "")).lower() in SERVICES}
        if not selected:
            continue
        for status in (["sent"], ["scheduled", "sending", "error"]):
            cursor = None
            for _ in range(60):
                after = f", after: {json.dumps(cursor)}" if cursor else ""
                filt = f"status: [{', '.join(status)}], channelIds: {json.dumps(list(selected))}"
                page = buffer_graphql(key, f"query {{ posts(first: 50{after}, input: {{ organizationId: {json.dumps(oid)}, filter: {{ {filt} }} }}) {{ edges {{ node {{ {fields} }} }} pageInfo {{ endCursor hasNextPage }} }} }}")["posts"]
                stop = False
                for edge in page["edges"]:
                    node = edge["node"]
                    when = utc(node.get("sentAt") or node.get("dueAt") or node.get("createdAt"))
                    if status == ["sent"] and when and when < since:
                        stop = True
                        continue
                    node["service"] = selected.get(node.get("channelId"), "")
                    out.append(node)
                if stop or not page["pageInfo"]["hasNextPage"]:
                    break
                cursor = page["pageInfo"]["endCursor"]
    return out


def bluesky_posts(since: datetime) -> list[dict]:
    out, cursor = [], ""
    for _ in range(10):
        url = f"{BSKY_FEED}?actor={BSKY_ACTOR}&limit=100&filter=posts_and_author_threads" + (f"&cursor={cursor}" if cursor else "")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "saisoku-audit/1.0"}), timeout=30) as res:
            data = json.loads(res.read())
        for item in data.get("feed", []):
            post = item["post"]
            when = utc(post["record"].get("createdAt"))
            if when and when >= since:
                out.append(post)
        cursor = data.get("cursor") or ""
        oldest = utc((data.get("feed") or [{}])[-1].get("post", {}).get("record", {}).get("createdAt")) if data.get("feed") else None
        if not cursor or (oldest and oldest < since):
            break
    return out


def thread_items(node: dict) -> list[dict]:
    meta = node.get("metadata") or {}
    return list(meta.get("thread") or [])


def media_of(items: list[dict]) -> list[dict]:
    found = []
    for a in items:
        if not isinstance(a, dict):
            continue
        kind = str(a.get("type") or a.get("__typename") or a.get("mimeType") or "").lower()
        url = a.get("source") or a.get("url") or ""
        media = {"type": "video" if "video" in kind else "image", "url": url}
        for k in ("width", "height"):
            if a.get(k):
                media[k] = a[k]
        image = a.get("image") or a.get("video") or {}
        if isinstance(image, dict):
            for k in ("width", "height"):
                if image.get(k):
                    media[k] = image[k]
        found.append(media)
    return found


def normalize_buffer(node: dict) -> dict:
    items = thread_items(node)
    service = node.get("service") or ""
    platform = {"twitter": "x"}.get(service, service)
    body = node.get("text") or (items[0].get("text") if items else "") or ""
    replies = [i.get("text") or "" for i in items[1:]]
    assets = list(node.get("assets") or []) + [a for i in items[:1] for a in (i.get("assets") or [])]
    meta = node.get("metadata") or {}
    link_att = (meta.get("linkAttachment") or {}) if isinstance(meta, dict) else {}
    return {
        "platform": platform, "status": node.get("status"), "id": node.get("id"),
        "at": node.get("sentAt") or node.get("dueAt") or node.get("createdAt"),
        "url": node.get("externalLink") or "", "text": body, "replies": replies,
        "media": media_of(assets), "links": [link_att.get("url")] if link_att.get("url") else [],
        "error": (node.get("error") or {}).get("message") if isinstance(node.get("error"), dict) else None,
    }


def normalize_bsky(post: dict) -> dict:
    record = post["record"]
    embed = record.get("embed") or {}
    links = [(embed.get("external") or {}).get("uri")] if embed.get("external") else []
    links += [feat.get("uri") for facet in record.get("facets") or [] for feat in facet.get("features") or [] if feat.get("uri")]
    media = []
    view = post.get("embed") or {}
    if (view.get("external") or {}).get("thumb"):
        media.append({"type": "image", "url": view["external"]["thumb"], "card": True})
    for img in view.get("images") or []:
        media.append({"type": "image", "url": img.get("fullsize"), **(img.get("aspectRatio") or {})})
    if "video" in str(view.get("$type", "")):
        media.append({"type": "video", "url": view.get("playlist"), **(view.get("aspectRatio") or {})})
    rkey = post["uri"].rsplit("/", 1)[-1]
    return {
        "platform": "bluesky", "status": "sent", "id": post["uri"], "at": record.get("createdAt"),
        "url": f"https://bsky.app/profile/{BSKY_ACTOR}/post/{rkey}", "text": record.get("text") or "", "replies": [],
        "media": media, "links": [x for x in dict.fromkeys(links) if x], "reply": bool(record.get("reply")),
    }


def judge(post: dict) -> list[str]:
    try:
        import sns_rules  # noqa: PLC0415
    except ImportError:
        return []
    return sns_rules.audit_post(post)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=float, default=4)
    parser.add_argument("--out", default="out/sns_audit.json")
    parser.add_argument("--no-buffer", action="store_true")
    args = parser.parse_args()
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    posts: list[dict] = []
    key = os.environ.get("BUFFER_API_KEY", "").strip()
    buffer_error = ""
    if key and not args.no_buffer:
        try:
            posts += [normalize_buffer(n) for n in buffer_posts(key, since)]
        except RuntimeError as error:  # 回数制限など。Bluesky の分は続けて点検する
            buffer_error = str(error)[:200]
            print(f"[audit] Buffer を読めません（Threads・X・Facebook は未点検）: {buffer_error[:80]}")
    posts += [normalize_bsky(p) for p in bluesky_posts(since)]
    for post in posts:
        post["violations"] = judge(post)
        when = utc(post.get("at"))
        post["atJst"] = when.astimezone(JST).strftime("%m/%d %H:%M") if when else ""
    posts.sort(key=lambda p: p.get("at") or "")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"bufferError": buffer_error, "posts": posts}, ensure_ascii=False, indent=1), "utf-8")
    counts: dict[str, int] = {}
    for p in posts:
        counts[p["platform"]] = counts.get(p["platform"], 0) + 1
    print(f"[audit] {len(posts)} 件 {counts}・違反の候補 {sum(1 for p in posts if p['violations'])} 件 → {args.out}")
    return 1 if buffer_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
