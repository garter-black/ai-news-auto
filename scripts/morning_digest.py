#!/usr/bin/env python3
"""
朝のまとめ投稿「今朝のAIニュース3本」（毎朝 7:30）と、週2回の問いかけ投稿（火・金 12:15）。日本時間。Threads と X に出す。

  python3 scripts/morning_digest.py --dry-run                      # 朝のまとめの文面と選んだ記事を表示するだけ
  python3 scripts/morning_digest.py --kind poll --dry-run --force  # 問いかけ投稿の文面を表示（曜日・時刻を問わない）
  python3 scripts/morning_digest.py [--kind poll]                  # THREADS_MODE=live のときだけ投稿（Buffer で枠の時刻に予約）
  python3 scripts/morning_digest.py --test-draft [--kind poll]     # Buffer に下書きを作って消す（公開しない）
  python3 scripts/morning_digest.py --merge mine.json              # 投稿の記録をいまの state に足す（push の衝突対策）

問いかけ投稿: Buffer の API は Threads のアンケート（poll）に対応していない（2026-10-29 にスキーマで確認）ため、
「①②③で答えてもらう」問いかけの投稿で代わりにする。その週の話題（トピックの多い AI・気になった記事）から作る

決まりごと:
- 対象は直近 HOURS 時間に作った AI の記事（地震・津波などの災害記事は除く）。速報 → 投稿向きの点数 → Threads の閲覧数の順に選ぶ
- 同じ話題（Threads のトピック）が重ならないように3本選ぶ。2本未満なら出さない
- 本文は見出しを1行ずつ（煽らない）。見出しは「だれが＋何を」（scripts/digest_page.py の short_headline。会社名だけにしない）
- リンクは自分への返信に置く（Threads はリンクカード）。リンク先はその日の号のページ /digest/<YYYYMMDD>（UTM 付き）、
  Threads・X には号の横長画像（/digest/<id>/share.png）を添える。号のデータは投稿前に KV へ置き、出せなければ従来の /news・画像なし
- 7:30 は通常の予約枠から外してある（threads_post.DIGEST_SLOT_JST）ので、1日の本数は増えない
- 1日1本だけ。state/threads_posted.json に digest-YYYYMMDD（style: digest）で記録する
- SNS_DIGEST=0 なら何もしない
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import digest_page as dp  # noqa: E402
import threads_post as tp  # noqa: E402

HOURS = 12
MIN_ITEMS = 2
MAX_ITEMS = 3
ITEM_MAX = dp.ITEM_MAX
# 本文の話題（1つだけ）
TOPIC = "AI"
REPLY_LINES = [
    "ほかの新着もサイトにまとめています👇",
    "昨夜からの新着はこちらから👇",
    "全部の記事はこちらから読めます👇",
]


def log(message: str) -> None:
    print(message, flush=True)


def one_line(article: dict) -> str:
    """見出しを1行に（だれが＋何を）。2026/11/02 に「Google DeepMind」だけになった切り方を直した（digest_page.short_headline）"""
    return dp.short_headline(article)


def views(state: dict, slug: str) -> float:
    metrics = (state["posted"].get(slug) or {}).get("metrics") or {}
    for key in ("views", "impressions", "reach"):
        if key in metrics:
            return float(metrics[key])
    return 0.0


def pick(state: dict, now: datetime) -> list[dict]:
    since = now - timedelta(hours=HOURS)
    found = []
    for path in tp.ARTICLES_DIR.glob("*.json"):
        article = json.loads(path.read_text("utf-8"))
        if article.get("genreSlug") != "ai" or article.get("disaster") or article.get("type") == "roundup":
            continue
        if datetime.fromisoformat(article["generatedAt"]) < since:
            continue
        found.append(article)
    found.sort(
        key=lambda a: (bool(a.get("breaking")), tp.score(a) + min(views(state, a["slug"]) / 200, 3), a["generatedAt"]),
        reverse=True,
    )
    chosen: list[dict] = []
    topics: set[str] = set()
    for article in found:
        topic = tp.topic_of(article)
        if topic in topics and topic not in ("AI", "生成AI"):
            continue
        if any(tp.overlap(one_line(article), one_line(c)) >= 12 for c in chosen):
            continue
        chosen.append(article)
        topics.add(topic)
        if len(chosen) == MAX_ITEMS:
            break
    return chosen


def compose(items: list[dict], now: datetime | None = None) -> str:
    jst = (now or datetime.now(timezone.utc)).astimezone(tp.JST)
    period = "今朝" if jst.hour < 10 else "今日"
    head = f"{period}のAIニュース{len(items)}本（{jst:%Y/%m/%d}）"
    return "\n".join([head, "", *(f"・{one_line(a)}" for a in items)])


def compose_x(items: list[dict], link: str = "", now: datetime | None = None) -> str:
    """X の本文（全角2の280字）。入らなければ後ろの項目から落とす。
    号のページのリンクは本文に入れず返信に置く（2026-11-03 ルール1。x_input が返信を作る）。link は使わない（以前の呼び方のため残す）"""
    del link
    for n in range(len(items), 0, -1):
        text = f"{compose(items[:n], now)}\n\n#AI"
        if tp.x_weight_url(text) <= tp.X_LIMIT:
            return text
    return compose([], now).splitlines()[0].replace("0本", "")


ASSISTANTS = ["ChatGPT", "Gemini", "Claude", "Copilot"]
POLL_ENDINGS = ["番号でコメントしてもらえるとうれしいです。", "番号だけでも大歓迎です。理由もあればぜひ。", "コメントで教えてください。"]


def week_articles(state: dict, now: datetime) -> list[dict]:
    """直近7日の AI 記事（気になる順）"""
    since = now - timedelta(days=7)
    found = []
    for path in tp.ARTICLES_DIR.glob("*.json"):
        article = json.loads(path.read_text("utf-8"))
        if article.get("genreSlug") == "ai" and not article.get("disaster") and article.get("type") != "roundup":
            if datetime.fromisoformat(article["generatedAt"]) >= since:
                found.append(article)
    found.sort(key=lambda a: (bool(a.get("breaking")), tp.score(a) + min(views(state, a["slug"]) / 200, 3)), reverse=True)
    return found


def compose_poll(state: dict, now: datetime) -> str:
    """問いかけ投稿（2〜4択）。火曜は今週の話題、金曜は使い方の話を中心に、週ごとに入れ替える"""
    jst = now.astimezone(tp.JST)
    week = jst.isocalendar()[1]
    articles = week_articles(state, now)
    counts: dict[str, int] = {}
    for a in articles:
        counts[tp.topic_of(a)] = counts.get(tp.topic_of(a), 0) + 1
    ending = POLL_ENDINGS[(week * 2 + (jst.weekday() == 4)) % len(POLL_ENDINGS)]
    headlines: list[str] = []
    topics: set[str] = set()
    for a in articles:
        if tp.topic_of(a) in topics or any(tp.overlap(one_line(a), h) >= 12 for h in headlines):
            continue
        headlines.append(one_line(a))
        topics.add(tp.topic_of(a))
        if len(headlines) == 3:
            break
    # 今週よく話題になった AI（足りなければ定番で埋める）
    assistants = sorted(ASSISTANTS, key=lambda t: -counts.get(t, 0))[:3]
    marks = "①②③④"
    if jst.weekday() != 4 and week % 2 == 0 and len(headlines) >= 2:
        body = "\n".join(f"{marks[i]}{h}" for i, h in enumerate(headlines))
        return f"今週のAIニュースで、いちばん気になったのは？\n\n{body}\n\n{ending}"
    if jst.weekday() != 4:
        return f"仕事で使うならどれ？\n\n{'／'.join(assistants)}\n\n{ending}使い分けている人は、その分け方もぜひ。"
    evergreen = [
        ("ふだんAIをいちばん使う場面は？", ["調べもの", "文章づくり", "プログラミング", "画像・動画づくり"]),
        ("AIの新機能が出たら？", ["すぐ試す", "評判を見てから", "あまり使わない"]),
        ("ふだん使っているAIは？", assistants + ["特に使っていない"]),
    ]
    question, options = evergreen[week % len(evergreen)]
    body = "\n".join(f"{marks[i]}{o}" for i, o in enumerate(options[:4]))
    return f"{question}\n\n{body}\n\n{ending}"


def compose_poll_x(body: str, now: datetime) -> str:
    """日付と全選択肢を残す。結びの省略→長い記事見出しの短縮の順。"""
    heading = f"AIの問いかけ（{now.astimezone(tp.JST):%Y/%m/%d}）"
    parts = body.split("\n\n")
    candidates = [f"{heading}\n{body}\n\n#AI"]
    if len(parts) >= 2:
        candidates.append(f"{heading}\n{parts[0]}\n\n{parts[1]}\n#AI")
        for limit in (30, 24, 18):
            choices = "\n".join(line if len(line) <= limit else line[:limit - 1] + "…" for line in parts[1].splitlines())
            candidates.append(f"{heading}\n{parts[0]}\n\n{choices}\n#AI")
    for text in candidates:
        if tp.x_weight_url(text) <= tp.X_LIMIT:
            return text
    raise RuntimeError("問いかけの全選択肢をXの文字数内に収められません")


def slot_due(slot: tuple[int, int], now: datetime) -> datetime | None:
    """枠の時刻より前に動いたらその時刻に予約、過ぎていたらいますぐ（None）"""
    hour, minute = slot
    when = now.astimezone(tp.JST).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return when.astimezone(timezone.utc) if when - now > timedelta(minutes=3) else None


def due_time(now: datetime) -> datetime | None:
    return slot_due(tp.DIGEST_SLOT_JST, now)


def merge(mine_path: str) -> int:
    mine = json.loads(Path(mine_path).read_text("utf-8"))
    state = tp.load_state()
    added = 0
    for collection in ("extraPending", "posted"):
        for key, entry in mine.get(collection, {}).items():
            if entry.get("style") not in ("digest", "poll"):
                continue
            current = daily_entry(state, key)
            combined = merge_entry(current, entry)
            restore_edition(combined)
            if combined != current:
                store_entry(state, key, combined, save=False)
                added += 1
    tp.save_state(state)
    log(f"[digest] 投稿履歴に {added} 件を足しました")
    return 0


def restore_edition(entry: dict) -> None:
    """push 前に最新の main へ戻したあとも、号のデータ（content/digests）を git に残す"""
    edition = (entry.get("postPlan") or {}).get("edition")
    if entry.get("style") == "digest" and isinstance(edition, dict) and edition.get("id") and not dp.path_of(edition["id"]).exists():
        dp.save(edition)


def delete_post(api_key: str, post_id: str) -> None:
    data = tp.buffer_graphql(
        api_key,
        """mutation($input: DeletePostInput!) { deletePost(input: $input) { __typename ... on MutationError { message } } }""",
        {"input": {"id": post_id}},
    )
    if data["deletePost"].get("message"):
        raise RuntimeError("Buffer: 下書きの削除が拒否されました")


def build(kind: str, state: dict, now: datetime) -> dict | None:
    """投稿の中身（Threads の本文・返信・X の本文・記録用の情報）。出さないなら None"""
    jst = now.astimezone(tp.JST)
    if kind == "poll":
        body = compose_poll(state, now)
        text = f"AIの問いかけ（{jst:%Y/%m/%d}）\n{body}"
        x_text = compose_poll_x(body, now)
        return {
            "key": f"poll-{jst:%Y%m%d}", "text": text, "reply": "", "link": "",
            "x_text": x_text,
            "slot": tp.POLL_SLOT_JST, "items": [],
        }
    items = pick(state, now)
    if len(items) < MIN_ITEMS:
        log(f"[digest] 直近{HOURS}時間の記事が {len(items)} 本しかないため出しません")
        return None
    text = compose(items, now)
    edition = dp.edition(items, now, re.sub(r"（.*$", "", text.splitlines()[0]))
    link = f"{tp.site_url()}/digest/{edition['id']}"
    for a in items:
        log(f"  {a['slug']} (速報={bool(a.get('breaking'))} 点={tp.score(a)} トピック={tp.topic_of(a)})")
    return {
        "key": f"digest-{jst:%Y%m%d}", "text": text,
        "reply": REPLY_LINES[jst.toordinal() % len(REPLY_LINES)] if jst.hour < 10 else REPLY_LINES[0], "link": link,
        "x_text": compose_x(items, tp.utm_link(link, "x", "digest"), now),
        "slot": tp.DIGEST_SLOT_JST, "items": [a["slug"] for a in items],
        # ?v= はデザイン名（デザインを変えた日に古い画像のキャッシュを使わない）
        # SNS は横長（16:9 前後）で統一する（2026-11-03 オーナー判断。縦長はタイムラインで場所を取りすぎる）
        "image": f"{tp.site_url()}/digest/{edition['id']}/share.png?v={dp.image_rev()}", "edition": edition,
    }


def fallback_to_news(post: dict) -> dict:
    """号のページを出せないときは、従来どおり /news へのリンク・画像なしにする"""
    news = f"{tp.site_url()}/news"
    x_text = post["x_text"].replace(tp.utm_link(post["link"], "x", "digest"), tp.utm_link(news, "x", "digest"))
    return {**post, "link": news, "image": "", "x_text": x_text}


def prepare_page(post: dict) -> dict:
    """号のデータを置き（content/digests と KV）、サイトが号の画像を返すまで待つ。

    Buffer への予約より前に行う。号のデータが置けない・画像を確認できないときは /news・画像なしに戻す。
    案 C（lib/digest-design.ts）のときだけ、号ごとに1枚 AI の背景を作る（すでにあれば作らない）。
    """
    edition = post.get("edition")
    if not edition or "/digest/" not in str(post.get("link")):
        return post
    edition = dict(edition)
    if dp.design() == "C" and not edition.get("aiImage"):
        articles = [a for a in (dp.load_article(i["slug"]) for i in edition["items"]) if a]
        try:
            url = dp.ai_background(edition, articles)
        except Exception as error:  # noqa: BLE001  背景が無くても写真で描ける
            log(f"[digest] 背景を作れません ({type(error).__name__})")
            url = None
        if url:
            edition["aiImage"] = url
    dp.save(edition)
    post = {**post, "edition": edition}
    if not dp.publish(edition) or not dp.wait_live(post["image"]):
        log("[digest] 号のページ・画像を確認できないため、リンクは /news・画像なしで出します")
        return fallback_to_news(post)
    # 縦長画像（サイトの号ページ用）もエッジに作っておく（失敗しても投稿は続ける）
    dp.wait_live(post["image"].replace("/share.png", "/card.png"), tries=2, interval=5)
    log(f"[digest] 号のページ: {post['link']}")
    return post


def image_assets(post: dict) -> list[dict]:
    return [{"image": {"url": post["image"]}}] if post.get("image") else []


def link_card(post: dict) -> dict:
    """返信のリンクカード。号のページなら号のシェア画像、従来の /news ならロゴ"""
    if "/digest/" in str(post.get("link")):
        return {
            "title": f"{post['text'].splitlines()[0]}｜サイソクニュース",
            "description": "・".join(i.get("headline", "") for i in (post.get("edition") or {}).get("items", []))[:120]
            or "AI・ビジネス・お金の公式発表を、速く・分かりやすく。",
            "thumbnail": {"url": f"{post['link'].rstrip('/')}/share.png?v={dp.image_rev()}"},
        }
    return {
        "title": "サイソクニュース 新着一覧",
        "description": "AI・ビジネス・お金の公式発表を、速く・分かりやすく。",
        "thumbnail": {"url": f"{tp.site_url()}/logo.png"},
    }


def x_input(channel_id: str, post: dict, when: dict) -> dict:
    """X は本文＋画像、記事（号のページ）のリンクは返信に置く（2026-11-03 ルール1: X だけリンクは返信）"""
    link = tp.utm_link(post["link"], "x", "digest") if post.get("link") else ""
    body = post["x_text"]
    thread = [{"text": body, "assets": image_assets(post)}]
    if link:
        thread.append({"text": f"{post.get('reply') or REPLY_LINES[0]}\n{link}", "assets": []})
    return {"channelId": channel_id, "text": body, "schedulingType": "automatic", "assets": image_assets(post), **when,
            "metadata": {"twitter": {"thread": thread}}}


def gate_article(post: dict) -> dict:
    """ルールの門番（sns_rules）に渡す朝のまとめの中身（号のページと記事）"""
    return {"link": post.get("link") or "", "items": list(post.get("items") or [])}


def gate_service(payload: dict) -> str:
    return "twitter" if "twitter" in (payload.get("metadata") or {}) else "threads"


def without_images(payload: dict) -> dict:
    """画像を外した投稿（Buffer が画像付きを拒否したときの作り直し用）"""
    copy = json.loads(json.dumps(payload))
    copy["assets"] = []
    for item in (copy.get("metadata") or {}).get("threads", {}).get("thread", []):
        item["assets"] = []
    return copy


def create_with_fallback(api_key: str, payload: dict) -> str:
    """画像付きで作る。Buffer がはっきり拒否した（MutationError）ときだけ、画像なしで1回作り直す"""
    gate = {"service": gate_service(payload), "article": payload.pop("_gate", None), "source": "morning_digest", "kind": "digest"}
    try:
        return tp.create_post(api_key, payload, **gate)
    except RuntimeError as error:
        plain = without_images(payload)
        if not str(error).startswith("Buffer: ") or plain == payload:
            raise
        log("[digest] 画像付きの予約が拒否されたため、画像なしで作り直します")
        return tp.create_post(api_key, plain, **gate)


def threads_input(channel_id: str, post: dict, when: dict) -> dict:
    """Threads は本文の最後に号のページのリンク（2026-11-03 ルール1: X 以外はリンクを本文に。返信は作らない）"""
    text = post["text"]
    if post.get("link"):
        text = f"{text.rstrip()}\n\n{post.get('reply') or REPLY_LINES[0]}\n{tp.utm_link(post['link'], 'threads', 'digest')}"
    thread = [{"text": text, "assets": image_assets(post)}]
    return {
        "channelId": channel_id, "text": text, "schedulingType": "automatic", "assets": [], **when,
        "metadata": {"threads": {"topic": TOPIC, "thread": thread}},
    }


ACCEPTED_STATUSES = {"accepted", "scheduled", "sending", "sent"}
REMOTE_FIELDS = "id text status createdAt dueAt sentAt externalLink"


def in_window(kind: str, now: datetime) -> bool:
    """当日だけ回収する。遅い朝まとめも直近12時間の記事から組み直す。"""
    jst = now.astimezone(tp.JST)
    minutes = jst.hour * 60 + jst.minute
    if kind == "poll":
        return jst.weekday() in tp.POLL_WEEKDAYS_JST and 11 * 60 <= minutes < 20 * 60
    return 6 * 60 + 30 <= minutes < 18 * 60


def daily_entry(state: dict, key: str) -> dict:
    return state.get("posted", {}).get(key) or state.get("extraPending", {}).get(key) or {}


def store_entry(state: dict, key: str, entry: dict, save: bool = True) -> None:
    # Threads未成功の準備記録をpostedに置くと、Blueskyが先に配信してしまう。
    threads_status = entry.get("delivery", {}).get("threads", {}).get("status", "accepted")
    if entry.get("id") and threads_status in ACCEPTED_STATUSES:
        state.setdefault("posted", {})[key] = entry
        state.setdefault("extraPending", {}).pop(key, None)
    else:
        state.setdefault("extraPending", {})[key] = entry
        state.setdefault("posted", {}).pop(key, None)
    if save:
        tp.save_state(state)


def merge_entry(current: dict, incoming: dict) -> dict:
    """既存の反応・他媒体を保ち、片側だけ記録された成功も取り込む。"""
    merged = {**incoming, **current}
    for service in ("threads", "twitter"):
        old_id = current.get("id") if service == "threads" else current.get("channels", {}).get(service)
        new_id = incoming.get("id") if service == "threads" else incoming.get("channels", {}).get(service)
        if old_id and new_id and old_id != new_id:
            raise RuntimeError(f"{service} の投稿IDが競合しています。自動で上書きしません")
        if service == "threads" and (old_id or new_id):
            merged["id"] = old_id or new_id
    merged["channels"] = {**incoming.get("channels", {}), **current.get("channels", {})}
    delivery = dict(current.get("delivery", {}))
    for service, info in incoming.get("delivery", {}).items():
        if media_id(current, service) and not media_id(incoming, service):
            continue  # 古い準備journalで、すでに保存した受理結果を巻き戻さない。
        if info.get("updatedAt", "") >= delivery.get(service, {}).get("updatedAt", ""):
            delivery[service] = info
    merged["delivery"] = delivery
    return merged


def buffer_posts(api_key: str, channel_id: str, now: datetime) -> list[dict]:
    """全statusを当日に絞って照合。取得不完全なら新規作成を許可しない。

    Bufferには冪等キーがないため、日付・kindを本文で識別する。
    入口はworkflowのsns-extra concurrencyで必ず直列化する。
    https://developers.buffer.com/guides/efficient-api-usage.html
    """
    start = now.astimezone(tp.JST).replace(hour=0, minute=0, second=0, microsecond=0)
    orgs = tp.buffer_graphql(api_key, "query { account { organizations { id } } }")["account"]["organizations"]
    if not orgs:
        raise RuntimeError("Bufferの組織を確認できません")
    posts: dict[str, dict] = {}
    for org in orgs:
        cursor = None
        for _ in range(5):
            after = f", after: {json.dumps(cursor)}" if cursor else ""
            filters = f"channelIds: [{json.dumps(channel_id)}], startDate: {json.dumps(tp.utc_z(start - timedelta(seconds=1)))}, endDate: {json.dumps(tp.utc_z(start + timedelta(days=1)))}"
            data = tp.buffer_graphql(api_key, f"""query {{ posts(first: 50{after}, input: {{
                organizationId: {json.dumps(org['id'])}, filter: {{ {filters} }}
            }}) {{ edges {{ node {{ {REMOTE_FIELDS} }} }} pageInfo {{ endCursor hasNextPage }} }} }}""")
            page = data["posts"]
            for edge in page["edges"]:
                node = edge["node"]
                posts[node["id"]] = node
            if not page["pageInfo"]["hasNextPage"]:
                break
            next_cursor = page["pageInfo"]["endCursor"]
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError("Buffer投稿一覧のページ送りを確認できません")
            cursor = next_cursor
        else:
            raise RuntimeError("Buffer投稿一覧の上限に達しました。照合を省略して投稿しません")
    return list(posts.values())


def matches_daily(node: dict, kind: str, now: datetime) -> bool:
    """再生成で記事が変わっても同じ日・企画を重複作成しない。旧本文も当日分だけ検知。"""
    text = str(node.get("text") or "")
    stamp = now.astimezone(tp.JST).strftime("%Y/%m/%d")
    if kind == "digest":
        prefix = re.match(r"^(?:今朝|今日)のAIニュース(?:\d+本)?", text)
    else:
        prefix = text.startswith("AIの問いかけ")
    if prefix and f"（{stamp}）" in text.splitlines()[0]:
        return True
    # 旧版の当日投稿をGit記録の欠落時にも再作成しない。別日の再送はしない。
    today = now.astimezone(tp.JST).date()
    dates = []
    for field in ("createdAt", "dueAt", "sentAt"):
        if node.get(field):
            dates.append(datetime.fromisoformat(node[field].replace("Z", "+00:00")).astimezone(tp.JST).date())
    if today not in dates or re.search(r"（\d{4}/\d{2}/\d{2}）", text):
        return False
    if kind == "digest":
        return bool(re.match(r"^(?:今朝|今日)のAIニュース\d+本", text))
    return text.startswith(("今週のAIニュースで、いちばん気になったのは？", "仕事で使うならどれ？", "ふだんAIをいちばん使う場面は？", "AIの新機能が出たら？", "今週いちばん話題になったのは", "ふだん使っているAIは？"))


def media_id(entry: dict, service: str) -> str:
    return str(entry.get("id", "") if service == "threads" else entry.get("channels", {}).get(service, ""))


def record_remote(entry: dict, service: str, node: dict, now: datetime) -> None:
    if service == "threads":
        first_id = not entry.get("id")
        entry["id"] = node["id"]
        entry["permalink"] = node.get("externalLink") or entry.get("permalink", "")
        if first_id:
            # 通信結果不明からの復元でも、朝の準備時刻を実配信時刻と取り違えない。
            # 既知IDの再照合時は時刻を動かさない（Blueskyの再配信候補化を防ぐ）。
            observed_time = node.get("sentAt") or node.get("dueAt") or node.get("createdAt")
            if observed_time:
                entry["dueAt"] = observed_time
            if node.get("createdAt"):
                entry["postedAt"] = node["createdAt"]
    else:
        entry.setdefault("channels", {})[service] = node["id"]
    entry.setdefault("delivery", {})[service] = {
        "status": node.get("status", "accepted"), "updatedAt": now.isoformat(timespec="seconds"),
        **{field: node[field] for field in ("sentAt", "externalLink") if node.get(field)},
    }


def verify(api_key: str, channels: dict, state: dict, key: str, kind: str, now: datetime) -> int:
    """外部・履歴とも読み取りのみ。受付と実配信を分け、未確認を成功にしない。"""
    entry = daily_entry(state, key)
    expected = entry.get("expectedChannels") or ["threads", "twitter"]
    ok = True
    for service in expected:
        if service not in channels:
            log(f"[verify] {service}: channel missing")
            ok = False
            continue
        found = [p for p in buffer_posts(api_key, channels[service]["id"], now)
                 if p["id"] == media_id(entry, service) or matches_daily(p, kind, now)]
        log("[verify] " + json.dumps({"key": key, "channel": service, "posts": [
            {k: p.get(k) for k in ("id", "status", "dueAt", "sentAt", "externalLink")} for p in found
        ]}, ensure_ascii=False))
        ok = ok and len(found) == 1 and found[0].get("status") in ACCEPTED_STATUSES
    return 0 if ok else 1


def deliver(api_key: str, channels: dict, state: dict, post: dict, kind: str, now: datetime) -> int:
    key = post["key"]
    if key != f"{kind}-{now.astimezone(tp.JST):%Y%m%d}" or not post.get("x_text"):
        raise RuntimeError("当日の本文・対象媒体を確定できません")
    due = slot_due(tuple(post["slot"]), now)
    current = daily_entry(state, key)
    entry = {"style": kind, "format": kind, "hook": kind, "question": kind == "poll", "topic": TOPIC,
             "items": post["items"], "via": "buffer", "channels": {}, "permalink": "",
             "dueAt": (due or now).isoformat(timespec="seconds"), "postedAt": now.isoformat(timespec="seconds"),
             **current, "postPlan": post, "expectedChannels": ["threads", "twitter"]}
    store_entry(state, key, entry)
    # 全対象の照合が完了するまでは、どの媒体にも新規作成しない。
    remote = {service: buffer_posts(api_key, channels[service]["id"], now)
              for service in entry["expectedChannels"] if service in channels}
    failed = False
    for service in entry["expectedChannels"]:
        if service not in channels:
            previous = entry.get("delivery", {}).get(service, {}).get("status")
            # チャンネルの一時切断は、前回の受理不明を「未受付」に変える証拠ではない。
            status = previous if previous in ("attempting", "unknown") else "unknown" if media_id(entry, service) else "rejected"
            entry.setdefault("delivery", {})[service] = {"status": status, "updatedAt": now.isoformat(timespec="seconds"), "reason": "channel missing"}
            store_entry(state, key, entry)
            log(f"[{service}] チャンネルが見つかりません")
            failed = True
            continue
        found = [p for p in remote[service] if p["id"] == media_id(entry, service) or matches_daily(p, kind, now)]
        if len(found) > 1:
            entry.setdefault("delivery", {})[service] = {"status": "unknown", "updatedAt": now.isoformat(timespec="seconds"), "reason": "multiple remote matches"}
            store_entry(state, key, entry)
            log(f"[{service}] 同日の投稿候補が複数あります。追加しません")
            failed = True
            continue
        if found:
            if media_id(entry, service) and media_id(entry, service) != found[0]["id"]:
                log(f"[{service}] 保存済みIDとBufferの候補が異なります。追加・上書きしません")
                failed = True
                continue
            record_remote(entry, service, found[0], now)
            store_entry(state, key, entry)
            log(f"[{service}] 既存投稿を照合: {found[0]['id']} ({found[0]['status']})")
            failed = failed or found[0].get("status") not in ACCEPTED_STATUSES
            continue
        previous = entry.get("delivery", {}).get(service, {}).get("status")
        if media_id(entry, service) or previous in ("attempting", "unknown"):
            entry.setdefault("delivery", {})[service] = {"status": "unknown", "updatedAt": now.isoformat(timespec="seconds"), "reason": "previous acceptance unconfirmed"}
            store_entry(state, key, entry)
            log(f"[{service}] 前回の受理結果を確定できません。重複防止のため再作成しません")
            failed = True
            continue
        media_due = due + timedelta(minutes=2) if due and service == "twitter" else due
        when = {"mode": "customScheduled", "dueAt": tp.utc_z(media_due)} if media_due else {"mode": "shareNow"}
        payload = threads_input(channels[service]["id"], post, when) if service == "threads" else x_input(channels[service]["id"], post, when)
        payload["_gate"] = gate_article(post)
        entry.setdefault("delivery", {})[service] = {"status": "attempting", "updatedAt": now.isoformat(timespec="seconds")}
        store_entry(state, key, entry)  # 通信前にも記録。クラッシュ後は照合なしに再作成しない。
        try:
            post_id = create_with_fallback(api_key, payload)
        except Exception as error:  # 片側だけ成功した場合も、そのIDを先に確実に残す
            # createPostのMutationErrorは明確な拒否。それ以外は受理不明として止める。
            status = "rejected" if isinstance(error, RuntimeError) and str(error).startswith("Buffer: ") else "unknown"
            entry["delivery"][service] = {"status": status, "updatedAt": now.isoformat(timespec="seconds")}
            store_entry(state, key, entry)
            log(f"[{service}] 投稿を確認できません ({status}, {type(error).__name__})")
            failed = True
            continue
        record_remote(entry, service, {"id": post_id, "status": "accepted"}, now)
        if service == "threads":
            # 朝の準備後に遅れてThreadsを受理した場合、Blueskyの鮮度判定にも実際の時刻を渡す。
            entry["dueAt"] = (media_due or now).isoformat(timespec="seconds")
            entry["postedAt"] = now.isoformat(timespec="seconds")
        store_entry(state, key, entry)  # 次の媒体へ進む前に受理IDを保存する
        log(f"[{service}] Buffer受理（公開は未確認）: {post_id}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kind", choices=["digest", "poll"], default="digest")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--test-draft", action="store_true", help="Buffer に下書きを作って消す（公開しない）")
    mode.add_argument("--verify", action="store_true", help="当日のBuffer受付・送信状況を読むだけ。履歴も変更しない")
    parser.add_argument("--date", default="", help="対象の日本日付 YYYY-MM-DD。当日以外は公開しない")
    parser.add_argument("--merge", default="")
    parser.add_argument("--force", action="store_true", help="確認用の時刻判定省略。liveでは禁止")
    args = parser.parse_args()
    if args.merge:
        if args.verify or args.dry_run or args.test_draft:
            log("[digest] --merge と確認モードは同時に指定できません")
            return 1
        return merge(args.merge)
    live = os.environ.get("THREADS_MODE") == "live" and not (args.dry_run or args.test_draft or args.verify)
    if args.force and live:
        log("[digest] --force は確認用です（--dry-run か --test-draft と一緒に使う）")
        return 1
    now = datetime.now(timezone.utc)
    jst = now.astimezone(tp.JST)
    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            log("[digest] --date は YYYY-MM-DD で指定してください")
            return 1
        if target != jst.date():
            log("[digest] 対象日が今日ではありません。別日の投稿は自動で再送しません")
            return 1
    state = tp.load_state()
    key = f"{args.kind}-{jst:%Y%m%d}"
    entry = daily_entry(state, key)
    if args.verify:
        api_key = os.environ.get("BUFFER_API_KEY", "").strip()
        if not api_key:
            log("[verify] BUFFER_API_KEY がありません")
            return 1
        try:
            return verify(api_key, tp.buffer_channels(api_key), state, key, args.kind, now)
        except Exception as error:
            log(f"[verify] 照合に失敗しました ({type(error).__name__})")
            return 1
    if args.kind == "poll":
        # 2026-11-03 停止（オーナー判断）: 記事のない定型の問いかけ（「番号でコメント」など）は SNS ルール1・3に反する。
        # 決まった型の文面で狙いが分からず、AI らしさが強い。sns_rules の門番も記事のない投稿を止める。再開はオーナーの承認が要る
        log("[poll] 問いかけ投稿は停止しています（記事のない定型の投稿は出さない。SNS ルール1・3）")
        return 0
    if not tp.DIGEST_ON:
        log("[digest] SNS_DIGEST=0 のため出しません")
        return 0
    if not in_window(args.kind, now) and not args.force:
        log(f"[{args.kind}] いまは出す時間ではありません（{jst:%a %H:%M}）")
        return 0
    try:
        post = dict(entry["postPlan"]) if entry.get("postPlan") and not args.test_draft else build(args.kind, state, now)
        if post is None:
            # 前回一部だけ送れたものを、材料不足の成功終了で隠さない。
            return 1 if entry else 0
        if args.kind == "digest" and jst.hour >= 10:
            for field in ("text", "x_text"):
                post[field] = post[field].replace("今朝のAIニュース", "今日のAIニュース", 1)
            post["reply"] = REPLY_LINES[0]
        if not post.get("x_text"):
            raise RuntimeError("Xに送る本文がありません")
        if args.kind == "digest" and (live or args.test_draft):
            post = prepare_page(post)
        log(f"--- {post['key']}\n{post['text']}\n[返信]\n{post['reply']}\n{post['link']}\n[画像] {post.get('image') or 'なし'}\n[X]\n{post['x_text']}\n")
        tp.write_summary([f"### {post['key']} {'（受付予定・公開は別途確認）' if live else '（下書き・未投稿）'}", "", "```", post["text"], "---", post["reply"], post["link"], "```", ""])
        if not live and not args.test_draft:
            return 0
        api_key = os.environ.get("BUFFER_API_KEY", "").strip()
        if not api_key:
            log("[digest] BUFFER_API_KEY が無いため投稿できません")
            return 1
        channels = tp.buffer_channels(api_key)
        if args.test_draft:
            if "threads" not in channels or "twitter" not in channels:
                log("[test] 対象チャンネルがありません")
                return 1
            draft = {"mode": "addToQueue", "saveToDraft": True}
            created = []
            try:
                gate = {"article": gate_article(post), "source": "morning_digest.test", "kind": "digest"}
                created.append(tp.create_post(api_key, threads_input(channels["threads"]["id"], post, draft), service="threads", **gate))
                created.append(tp.create_post(api_key, x_input(channels["twitter"]["id"], post, draft), service="twitter", **gate))
                log(f"[test] 下書きを作りました: {created}")
            finally:
                for post_id in created:
                    delete_post(api_key, post_id)
            log("[test] 下書きを削除しました")
            return 0
        return deliver(api_key, channels, state, post, args.kind, now)
    except Exception as error:
        # API本文・秘密値をログに残さず、失敗は非ゼロでworkflowへ伝える。
        log(f"[{args.kind}] 確認または投稿に失敗しました ({type(error).__name__})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
