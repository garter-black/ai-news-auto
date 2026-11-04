"""Bluesky（AT Protocol を直接）に記事を出す。Buffer は使わない

出すもの:
- 投稿履歴 state/threads_posted.json の各記録（Threads に出したもの）のうち、まだ Bluesky に出していないもの
  - 速報（breaking）と朝のまとめ（digest）は すぐ
  - 通常の予約は Threads の時刻（dueAt）から DELAY_MIN 分後
- --fill のとき: Threads 側に出すものがなければ、直近 FILL_HOURS 時間の良い記事（検索に出す記事）で
  まだ Bluesky に出していない最新の1本（記録は state/bluesky_extra.json）
1日 PER_DAY 本まで・深夜（1〜5時）は出さない。通常の投稿は1回の実行で1本まで、前の投稿から GAP_MIN 分あける。
結果は channels.bluesky に URI を記録。失敗しても他の SNS には影響しない
2026-11-03〜（オーナー指示「SNS の投稿は動画のはず」・sns_rules ルール8）: 記事の投稿は**動画つきだけ**。動画（state/sns_video.json の
SNS 用）ができるまで待ち、Bluesky の動画（app.bsky.embed.video）で出す。動画が無い記事は出さない（写真のカードでは出さない）。
朝のまとめだけ号のシェア画像のまま。

  python3 scripts/bluesky_post.py --dry-run [--fill]  # 下書きを表示（投稿しない。ログインできなければ画像なしで表示）
  python3 scripts/bluesky_post.py [--fill]            # 投稿（BLUESKY_HANDLE / BLUESKY_APP_PASSWORD が必要）
  python3 scripts/bluesky_post.py --merge FILE [--merge-extra FILE2]  # 手元の記録を最新の state に足す
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import threads_post as tp  # noqa: E402
import sns_rules  # noqa: E402

PDS = "https://bsky.social/xrpc"
DELAY_MIN = 20
PER_DAY = int(os.environ.get("BLUESKY_PER_DAY") or 24)
GAP_MIN = int(os.environ.get("BLUESKY_GAP_MIN") or 40)  # 通常の投稿どうしの間隔（同じ時刻に2本並ばないように）
LOOKBACK_H = 6  # これより古い記録は出さない（取りこぼしを後から大量に出さない。間隔をあける分の待ちは見込む）
FILL_HOURS = 12  # 埋め合わせに使う記事の新しさ（生成から）
FILL_MIN_AGE_MIN = 15  # 生成直後はまだ公開（デプロイ）前のことがあるので待つ
SIMILAR_HOURS = 24  # この時間内に出した投稿と同じ話題の記事は埋め合わせに使わない
EXTRA_PATH = tp.ROOT / "state" / "bluesky_extra.json"
MAX_GRAPHEMES = 300
MAX_BLOB = 1_000_000


def log(message: str) -> None:
    print(message, flush=True)


def xrpc(method: str, body: dict | bytes | None = None, token: str = "", ctype: str = "application/json") -> dict:
    data = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{PDS}/{method}", data=data, method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", ctype)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read() or b"{}")


def login() -> dict:
    handle = os.environ.get("BLUESKY_HANDLE", "").strip()
    password = os.environ.get("BLUESKY_APP_PASSWORD", "").strip()
    if not handle or not password:
        raise RuntimeError("BLUESKY_HANDLE / BLUESKY_APP_PASSWORD がありません")
    return xrpc("com.atproto.server.createSession", {"identifier": handle, "password": password})


def fetch(url: str, limit: int = MAX_BLOB + 1) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "saisoku-bluesky/1.0"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read(limit), res.headers.get_content_type()


def shrink(data: bytes) -> bytes | None:
    """1MB を超える画像（写真入りの PNG など）を JPEG にして 1MB 以下にする。Pillow が無ければ None"""
    try:
        import io

        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return None
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
        for quality in (86, 80, 72, 64):
            buf = io.BytesIO()
            image.save(buf, "JPEG", quality=quality, optimize=True)
            if buf.tell() <= MAX_BLOB:
                return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None
    return None


def upload_thumb(session: dict, urls: list[str], used: list[str] | None = None) -> dict | None:
    """1MB 以下の画像をアップロードして blob を返す。どれも無理なら None（カードは画像なし）。使った URL は used に足す"""
    for url in urls:
        try:
            data, ctype = fetch(url, 8 * 1024 * 1024)
            if not ctype.startswith("image/"):
                continue
            if len(data) > MAX_BLOB:
                data = shrink(data) or b""
                ctype = "image/jpeg"
                if not data:
                    continue
            blob = xrpc("com.atproto.repo.uploadBlob", data, session["accessJwt"], ctype)["blob"]
            if used is not None:
                used.append(url)
            return blob
        except Exception as error:  # noqa: BLE001
            log(f"  [bluesky] 画像を使えません {url}: {error}")
    return None


VIDEO_STATE = tp.ROOT / "state" / "sns_video.json"
VIDEO_SERVICE = "https://video.bsky.app/xrpc"
MAX_VIDEO = 50 * 1024 * 1024


def video_of(slug: str, videos: dict | None = None) -> dict:
    """この記事の SNS 用の動画（{"url", "variant"}）。Threads に付けた版 → 短い版 → 全部の版。無ければ {}"""
    if videos is None:
        try:
            videos = json.loads(VIDEO_STATE.read_text("utf-8")).get("videos") or {}
        except (OSError, ValueError):
            videos = {}
    rec = videos.get(slug) or {}
    urls = rec.get("urls") or {}
    import sns_video_followup as fu  # noqa: PLC0415

    for variant in (rec.get("variant"), "short", "full"):
        version = (rec.get("versions") or {}).get(variant) or {}
        if variant and urls.get(variant) and version.get("ok", True) and fu.qa_ok(version):
            credit = ((((rec.get("versions") or {}).get(variant) or {}).get("post") or {}).get("voice") or {}).get("credit", "")
            return {"url": urls[variant], "variant": variant, "credit": credit}
    return {}


def needs_video(entry: dict) -> bool:
    """動画が要る記録か（朝のまとめ・問いかけ・急ぎの安全の知らせ（災害・警報。2026-11-03 オーナー承認）以外の記事の投稿）"""
    return entry.get("style") not in ("digest", "poll") and not entry.get("urgent")


def upload_video(session: dict, url: str) -> dict:
    """SNS 用の動画を Bluesky の動画サービスに上げて、処理が終わった blob を返す（失敗したら例外）"""
    data, ctype = fetch(url, MAX_VIDEO + 1)
    if len(data) > MAX_VIDEO or not ctype.startswith("video/"):
        raise RuntimeError(f"動画を使えません（{ctype}・{len(data) / 1e6:.1f}MB）")
    pds = next((s.get("serviceEndpoint", "") for s in ((session.get("didDoc") or {}).get("service") or []) if s.get("id") == "#atproto_pds"), "")
    host = urllib.parse.urlsplit(pds).netloc or "bsky.social"
    auth = xrpc(f"com.atproto.server.getServiceAuth?aud={urllib.parse.quote('did:web:' + host)}&lxm=com.atproto.repo.uploadBlob"
                f"&exp={int(datetime.now(timezone.utc).timestamp()) + 1800}", None, session["accessJwt"])["token"]
    name = re.sub(r"[^A-Za-z0-9._-]", "-", url.rsplit("/", 1)[-1])[:80] or "video.mp4"
    req = urllib.request.Request(f"{VIDEO_SERVICE}/app.bsky.video.uploadVideo?did={urllib.parse.quote(session['did'])}&name={urllib.parse.quote(name)}",
                                 data=data, method="POST", headers={"Content-Type": "video/mp4", "Authorization": f"Bearer {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            job = json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as error:
        job = json.loads(error.read() or b"{}")  # 同じ動画を上げ直したとき（already_exists）も jobId が返る
    job = job.get("jobStatus", job)
    for _ in range(60):
        if job.get("blob"):
            return job["blob"]
        if job.get("state") == "JOB_STATE_FAILED" or not job.get("jobId"):
            raise RuntimeError(f"Bluesky の動画の処理に失敗しました: {str(job)[:200]}")
        time.sleep(5)
        with urllib.request.urlopen(f"{VIDEO_SERVICE}/app.bsky.video.getJobStatus?jobId={urllib.parse.quote(job['jobId'])}", timeout=30) as res:
            job = json.loads(res.read() or b"{}").get("jobStatus", {})
    raise RuntimeError("Bluesky の動画の処理が5分で終わりませんでした")


def build_record(text: str, link: str, title: str, desc: str, thumb: dict | None, video: dict | None = None) -> dict:
    """本文＋改行＋リンク。リンクは facet（UTF-8 のバイト位置）とリンクカードの両方で付ける"""
    shown = re.sub(r"^https?://|\?.*$", "", link)  # 表示は短く（リンク先は UTM 付き）
    room = MAX_GRAPHEMES - len(shown) - 1
    text = text.strip()
    if len(text) > room:
        text = text[: room - 1].rstrip() + "…"
    full = f"{text}\n{shown}"
    start = len(f"{text}\n".encode())
    external = {"uri": link, "title": title[:300], "description": desc[:300]}
    if thumb:
        external["thumb"] = thumb
    return {
        "$type": "app.bsky.feed.post",
        "text": full,
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "langs": ["ja"],
        "facets": [{
            "index": {"byteStart": start, "byteEnd": start + len(shown.encode())},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": link}],
        }],
        # 記事の投稿は動画（リンクは本文の facet）。朝のまとめだけリンクカード（号のシェア画像）
        "embed": ({"$type": "app.bsky.embed.video", "video": video, "aspectRatio": {"width": 1920, "height": 1080}, "alt": title[:300]}
                  if video else {"$type": "app.bsky.embed.external", "external": external}),
    }


def create_record(session: dict, record: dict, p: dict, media: list[dict]) -> dict:
    """Bluesky に1件出す。出す直前に SNS ルールの門番（sns_rules）を通す。違反なら SnsRuleViolation（出さない）"""
    sns_rules.enforce("bluesky", record["text"], media, p.get("gate"), source="bluesky_post", kind=p.get("kind", "article"), links=[p["link"]])
    return xrpc("com.atproto.repo.createRecord",
                {"repo": session["did"], "collection": "app.bsky.feed.post", "record": record}, session["accessJwt"])


def plan(key: str, entry: dict) -> dict | None:
    """投稿の中身（本文・リンク・カード）。出せないもの（アンケートなど）は None"""
    style = entry.get("style")
    if style == "poll":
        return None
    if style == "digest":
        import morning_digest as md
        items = []
        for slug in entry.get("items") or []:
            path = tp.ARTICLES_DIR / f"{slug}.json"
            if path.exists():
                items.append(json.loads(path.read_text("utf-8")))
        text = str((entry.get("postPlan") or {}).get("text") or "").strip()
        if not text:
            recorded_at = entry.get("dueAt") or entry.get("postedAt")
            if not items or not recorded_at:
                return None
            text = md.compose(items, datetime.fromisoformat(recorded_at.replace("Z", "+00:00")))
        # リンク先はその日の号のページ（/digest/<YYYYMMDD>。カードの画像も号のシェア画像）。古い記録・号を出せなかった日は /news
        page = str((entry.get("postPlan") or {}).get("link") or "")
        covers = [tp.card_url(items[0])] if items else []  # 写真だけのカード（見出し入りのカバーは使わない。ルール2）
        if "/digest/" in page:
            return {"text": text, "link": tp.utm_link(page, "bluesky", "digest"),
                    "title": f"{text.splitlines()[0]}｜サイソクニュース", "desc": "直近のAIニュースを3本だけ。写真と要約つき。",
                    "images": [f"{page.rstrip('/')}/share.png?v={md.dp.image_rev()}", *covers],
                    "kind": "digest", "gate": {"link": page, "items": items}}
        link = tp.utm_link(f"{tp.site_url()}/news", "bluesky", "digest")
        return {"text": text, "link": link, "title": "AIニュースまとめ｜サイソクニュース",
                "desc": "直近のAIニュースを1分で。", "images": covers, "kind": "digest", "gate": {"link": link, "items": items}}
    path = tp.ARTICLES_DIR / f"{key}.json"
    if not path.exists():
        return None
    article = json.loads(path.read_text("utf-8"))
    # 問いかけは Threads に出したものと同じにする（記録が無ければ話題・主役から作る）
    text = tp.compose(article, [], breaking=bool(entry.get("breaking")), fixed_question=str(entry.get("questionText") or ""),
                    fixed_pattern=str(entry.get("questionPattern") or ""))
    spec = tp.ROOT / "content" / "sns_reposts" / f"{key}.json"
    if spec.exists():  # オーナーに見せて承認をもらった出し直しの文面（Threads と同じ）
        text = json.loads(spec.read_text("utf-8")).get("classic") or text
    text = re.sub(r"\s*👇\s*$", "", text)
    link = tp.utm_link(f"{tp.site_url()}/articles/{key}", "bluesky", entry.get("format") or "classic")
    return {
        "text": text, "link": link, "title": article.get("seoTitle") or article["title"],
        "desc": article.get("description", ""),
        # 写真だけのカード（16:9）だけを使う。見出し入りのカバー・縦横の分からない元の写真は使わない（ルール2）
        "images": [tp.card_url(article)], "gate": article,
    }


def due(entry: dict) -> datetime | None:
    raw = entry.get("dueAt") or entry.get("postedAt")
    if not raw:
        return None
    when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    fast = entry.get("breaking") or entry.get("style") == "digest"
    return when if fast else when + timedelta(minutes=DELAY_MIN)


def load_extra(path: Path | None = None) -> dict:
    """埋め合わせで出した記事の記録 {slug: {uri, at, title}}"""
    path = path or EXTRA_PATH
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_extra(extra: dict, path: Path | None = None) -> None:
    path = path or EXTRA_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(extra, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse(raw: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if raw else None
    except ValueError:
        return None


def sent(state: dict, extra: dict | None = None) -> list[tuple[str, datetime]]:
    """Bluesky に出した記録（キー, 時刻）。Threads 側の記録と埋め合わせの記録の両方"""
    out = [(k, t) for k, e in state["posted"].items() if (t := _parse(e.get("blueskyAt")))]
    out += [(k, t) for k, e in (extra or {}).items() if isinstance(e, dict) and (t := _parse(e.get("at")))]
    return out


def posted_today(state: dict, now: datetime, extra: dict | None = None) -> int:
    today = now.astimezone(tp.JST).date()
    return sum(1 for _, t in sent(state, extra) if t.astimezone(tp.JST).date() == today)


def last_sent(state: dict, extra: dict | None = None) -> datetime | None:
    times = [t for _, t in sent(state, extra)]
    return max(times) if times else None


def fast(entry: dict) -> bool:
    """間隔を待たずにすぐ出すもの（速報・朝のまとめ）"""
    return bool(entry.get("breaking")) or entry.get("style") == "digest"


def pending(state: dict, now: datetime, extra: dict | None = None) -> list[tuple[str, dict]]:
    out = []
    for key, entry in state["posted"].items():
        when = due(entry)
        if not when or (entry.get("channels") or {}).get("bluesky") or entry.get("blueskyFailed"):
            continue
        if key in (extra or {}):  # 埋め合わせで先に出した記事は Threads に出た後も重ねない
            continue
        if entry.get("status") in ("awaiting_video", "skipped_no_video") or (needs_video(entry) and not video_of(key)):
            continue  # 動画ができるまで待つ（写真のカードでは出さない。ルール8）
        if when <= now and now - when <= timedelta(hours=LOOKBACK_H):
            out.append((key, entry))
    return sorted(out, key=lambda kv: due(kv[1]))


TITLE_NOISE = re.compile(r"[\s「」『』【】()（）・、。，．,.！？!?:：|｜\-—〜~\"'“”]")
QUOTED = re.compile(r"[「『\"“]([^」』\"”]{2,40})[」』\"”]")


def _norm(text: str) -> str:
    return TITLE_NOISE.sub("", text or "").lower()


def similar(a: str, b: str) -> bool:
    """同じ話題の題名か（ゆるい判定）: 文字の並びがかなり似ている／「」『』の製品名が同じ"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or SequenceMatcher(None, na, nb).ratio() >= 0.6:
        return True
    return bool({_norm(x) for x in QUOTED.findall(a)} & {_norm(x) for x in QUOTED.findall(b)} - {""})


def _indexable_rule():
    """検索に出す記事の基準（scripts/indexnow.py と同じ）。読み込めなければ簡易版"""
    try:
        import indexnow
        return indexnow.is_indexable, indexnow.duplicate_slugs
    except Exception:  # noqa: BLE001
        def simple(article: dict, dups=frozenset()) -> bool:
            return (len(article.get("body", "")) >= 1500 and article.get("genreSlug") in {"ai", "tech-gadget"}
                    and article.get("slug") not in dups)
        return simple, lambda articles: set()


def load_articles() -> list[dict]:
    out = []
    for path in tp.ARTICLES_DIR.glob("*.json"):
        try:
            article = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(article, dict) and article.get("slug"):
            out.append(article)
    return out


def fill_candidate(state: dict, extra: dict, now: datetime, articles: list[dict] | None = None) -> dict | None:
    """埋め合わせの1本: 直近 FILL_HOURS 時間の検索に出す記事のうち、まだどこにも予定がなく、
    直近 SIMILAR_HOURS 時間に出した投稿と同じ話題でない最新のもの"""
    articles = load_articles() if articles is None else articles
    is_indexable, duplicate_slugs = _indexable_rule()
    dups = duplicate_slugs(articles)
    by_slug = {a["slug"]: a for a in articles}
    taken = set(state.get("posted", {})) | set(state.get("extraPending", {})) | set(extra)
    recent: list[str] = []
    for key, when in sent(state, extra):
        if now - when > timedelta(hours=SIMILAR_HOURS):
            continue
        title = (extra.get(key) or {}).get("title") if key in extra else ""
        title = title or (by_slug.get(key) or {}).get("title") or ""
        if title:
            recent.append(title)
    newest = []
    for article in articles:
        generated = _parse(article.get("generatedAt"))
        if not generated or generated.tzinfo is None:
            continue
        age = now - generated
        if not timedelta(minutes=FILL_MIN_AGE_MIN) <= age <= timedelta(hours=FILL_HOURS):
            continue
        if article["slug"] in taken or article.get("roundupSlug") in taken:
            continue
        if not video_of(article["slug"]):
            continue  # 動画のある記事だけ（ルール8）
        newest.append((generated, article))
    newest.sort(key=lambda x: x[0], reverse=True)
    for _, article in newest:
        if not is_indexable(article, dups):
            continue
        if tp.reserved_for_breaking(article, state, now):  # 速報の即時投稿に任せる（二重投稿を防ぐ）
            continue
        if any(similar(article.get("title", ""), title) for title in recent):
            continue
        return article
    return None


def choose(state: dict, extra: dict, now: datetime, fill: bool, articles: list[dict] | None = None) -> list[tuple[str, dict, str]]:
    """この実行で出すもの [(キー, 記録, 種類)]。種類は mirror（Threads の記録）か fill（埋め合わせ）
    速報・まとめはすぐ（上限内）。通常は1本まで・前の投稿から GAP_MIN 分あける。Threads 側を優先し、なければ埋め合わせ"""
    room = PER_DAY - posted_today(state, now, extra)
    if room <= 0:
        return []
    todo = pending(state, now, extra)
    out = [(k, e, "mirror") for k, e in todo if fast(e)][:room]
    room -= len(out)
    last = now if out else last_sent(state, extra)
    if room <= 0 or (last and now - last < timedelta(minutes=GAP_MIN)):
        return out
    normal = [(k, e) for k, e in todo if not fast(e) and e.get("style") != "poll"]
    if normal:
        return out + [(normal[0][0], normal[0][1], "mirror")]
    if fill:
        article = fill_candidate(state, extra, now, articles)
        if article:
            return out + [(article["slug"], {"article": article}, "fill")]
    return out


def plan_fill(article: dict) -> dict:
    key = article["slug"]
    text = re.sub(r"\s*👇\s*$", "", tp.compose(article, [], breaking=False))
    return {
        "text": text, "link": tp.utm_link(f"{tp.site_url()}/articles/{key}", "bluesky", "fill"),
        "title": article.get("seoTitle") or article["title"], "desc": article.get("description", ""),
        "images": [tp.card_url(article)], "gate": article,
    }


def merge(mine_path: str) -> int:
    mine = json.loads(Path(mine_path).read_text("utf-8"))
    state = tp.load_state()
    added = 0
    for key, entry in mine.get("posted", {}).items():
        target = state.get("posted", {}).get(key) or state.get("extraPending", {}).get(key)
        if target is None:
            continue
        for field in ("blueskyAt", "blueskyFailed"):
            if entry.get(field) and not target.get(field):
                target[field] = entry[field]
        uri = (entry.get("channels") or {}).get("bluesky")
        if uri and not (target.get("channels") or {}).get("bluesky"):
            target.setdefault("channels", {})["bluesky"] = uri
            added += 1
    tp.save_state(state)
    log(f"[bluesky] 投稿履歴に {added} 件を足しました")
    return 0


def merge_extra(mine_path: str) -> int:
    """埋め合わせの記録を最新の state/bluesky_extra.json に足す（和集合。既にあるものは変えない）"""
    mine = load_extra(Path(mine_path))
    extra = load_extra()
    added = 0
    for key, entry in mine.items():
        if key not in extra:
            extra[key] = entry
            added += 1
    if added or not EXTRA_PATH.exists():
        save_extra(extra)
    log(f"[bluesky] 埋め合わせの記録に {added} 件を足しました")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fill", action="store_true", help="Threads 側に出すものがなければ最新の良い記事を出す")
    parser.add_argument("--merge", default="")
    parser.add_argument("--merge-extra", default="")
    parser.add_argument("--slug", default="", help="確認用: この記録を対象にする（--dry-run と一緒に）")
    args = parser.parse_args()
    if args.merge or args.merge_extra:
        if args.merge:
            merge(args.merge)
        if args.merge_extra:
            merge_extra(args.merge_extra)
        return 0
    live = os.environ.get("THREADS_MODE") == "live" and not args.dry_run
    state = tp.load_state()
    extra = load_extra()
    now = datetime.now(timezone.utc)
    if now.astimezone(tp.JST).hour in tp.BREAKING_QUIET_HOURS_JST and live:
        log("[bluesky] 深夜のため出しません")
        return 0
    if args.slug:
        todo = [(args.slug, state["posted"][args.slug], "mirror")]
    else:
        todo = choose(state, extra, now, args.fill)
        log(f"[bluesky] 今日 {posted_today(state, now, extra)}/{PER_DAY} 本・前回 {last_sent(state, extra)}")
        if not todo and not live:  # 下書きは最新の記事を見本にする
            todo = [(k, e, "mirror") for k, e in state["posted"].items() if not e.get("style")][-1:]
            if args.fill and (article := fill_candidate(state, extra, now)):
                todo.append((article["slug"], {"article": article}, "fill"))
    if not todo:
        log("[bluesky] 出すものはありません（上限・間隔・対象なし）")
        return 0
    session = None
    try:
        session = login()
        log(f"[bluesky] ログイン OK: {session.get('handle')} ({session.get('did')})")
    except Exception as error:  # noqa: BLE001  Bluesky の失敗で他を止めない
        log(f"[bluesky] ログイン失敗: {error}")
        if live:
            return 0
    for key, entry, kind in todo:
        try:
            p = plan_fill(entry["article"]) if kind == "fill" else plan(key, entry)
            if not p:
                entry["blueskyFailed"] = "skip"
                continue
            used: list[str] = []
            media: list[dict] = []
            blob = None
            if p.get("kind", "article") != "digest" and not sns_rules.is_urgent(p.get("gate")):
                vid = video_of(key)
                if not vid:
                    log(f"  [bluesky] {key} は動画がまだ無いので出しません（写真のカードでは出さない）")
                    continue
                media = [{"type": "video", "url": vid["url"], "width": 1920, "height": 1080}]
                if session and live:
                    blob = upload_video(session, vid["url"])
                p["link"] = tp.utm_link(f"{tp.site_url()}/articles/{key}", "bluesky", "sns_video", vid["variant"])
                if vid.get("credit") and len(p["text"]) + len(vid["credit"]) + 60 <= MAX_GRAPHEMES:
                    p["text"] = f"{p['text'].rstrip()}\n音声：{vid['credit']}"  # VOICEVOX の規約のクレジット（入るときだけ）
                thumb = None
            else:
                thumb = upload_thumb(session, p["images"], used) if session and (live or args.slug) else None
                media = [{"type": "image", "url": u} for u in used]
            record = build_record(p["text"], p["link"], p["title"], p["desc"], thumb, video=blob)
            if not live:
                log(f"--- {key} ({kind})\n{json.dumps(record, ensure_ascii=False, indent=1)}")
                for v in sns_rules.check_post("bluesky", record["text"], media, p.get("gate"),
                                              kind=p.get("kind", "article"), links=[p["link"]]):
                    log(f"  [check] {v}")
                continue
            if p.get("kind", "article") != "digest" and not sns_rules.is_urgent(p.get("gate")) and not blob:
                raise RuntimeError("動画を Bluesky に上げられませんでした（写真では出しません）")
            res = create_record(session, record, p, media)
            at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if kind == "fill":
                extra[key] = {"uri": res["uri"], "at": at, "title": entry["article"].get("title", "")}
            else:
                entry.setdefault("channels", {})["bluesky"] = res["uri"]
                entry["blueskyAt"] = at
            log(f"  [bluesky] {key} ({kind}): https://bsky.app/profile/{session['handle']}/post/{res['uri'].rsplit('/', 1)[-1]}")
        except Exception as error:  # noqa: BLE001
            log(f"  [bluesky] {key} 失敗: {error}")
            if kind == "fill":  # 同じ記事で毎回失敗し続けないよう、記録して次へ（時刻なし＝本数に数えない）
                extra[key] = {"failed": str(error)[:200], "title": entry["article"].get("title", "")}
            else:
                entry["blueskyFailed"] = str(error)[:200]
    if live:
        tp.save_state(state)
        if any(kind == "fill" for _, _, kind in todo):
            save_extra(extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
