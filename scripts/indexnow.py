#!/usr/bin/env python3
"""IndexNow で検索エンジンに更新を通知する。

Google Search Console は所有者のログインが必要で登録が進まないが、
IndexNow はアカウント不要で Bing / DuckDuckGo / Yandex などに直接通知できる。
新規サイトが「発見されない」状態を、こちら側だけで動かせる唯一の手段。

  python3 scripts/indexnow.py --all      # 全記事を送信（初回用）
  python3 scripts/indexnow.py --hours 12 # 直近12時間に生成された記事だけ
  python3 scripts/indexnow.py --urls https://ai-tech-express.jp/guides/a,...  # 指定した URL だけ
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import persona  # noqa: E402  読者像（検索に出す話題の基準）
import genre_focus  # noqa: E402  エンタメはビジネス・テックの目線だけ（2026-11-03）

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
HOWTO_DIR = ROOT / "content" / "howto"
PUBLIC_DIR = ROOT / "public"
ENDPOINT = "https://api.indexnow.org/indexnow"
MAX_URLS = 10000  # 1リクエストあたりの上限


def site_url() -> str:
    url = os.environ.get("NEXT_PUBLIC_SITE_URL") or "https://ai-tech-express.jp"
    return url.rstrip("/")


def find_key() -> str | None:
    """public/<key>.txt からキーを読む。ファイル名とその中身が一致している必要がある。"""
    for path in PUBLIC_DIR.glob("*.txt"):
        key = path.stem
        if path.read_text().strip() == key:
            return key
    return None


def latest_guide_timestamp(guide: dict) -> datetime | None:
    """全体確認日を維持した部分更新も拾う。時差を揃え、有効な最新日時を使う。"""
    timestamps = []
    for field in ("checkedAt", "updatedAt", "createdAt"):
        value = guide.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            touched = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            # タイムゾーンのない値は日付を推測しない。他の有効な日時へフォールバックする。
            if touched.tzinfo is not None:
                timestamps.append(touched.astimezone(timezone.utc))
        except (ValueError, OverflowError):
            continue
    return max(timestamps) if timestamps else None


def collect_urls(base: str, hours: int | None) -> list[str]:
    """インデックス対象（noindex_reason が None）の記事URLを集める。

    noindex にしている記事を送っても意味がないため、対象から外す。
    """
    threshold = (
        datetime.now(timezone.utc) - timedelta(hours=hours) if hours else None
    )
    urls = [base, f"{base}/articles", f"{base}/genres/ai"]

    articles = []
    for path in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            articles.append(json.loads(path.read_text("utf-8")))
        except json.JSONDecodeError:
            continue
    dups = duplicate_slugs(articles)
    for article in articles:
        if not is_indexable(article, dups):
            continue
        if threshold:
            try:
                # 続報を足した記事も送り直す
                generated = datetime.fromisoformat(article.get("updatedAt") or article.get("generatedAt", ""))
            except ValueError:
                continue
            if generated < threshold:
                continue
        urls.append(f"{base}/articles/{article['slug']}")

    # 使い方ガイド（/guides/<slug>）。作成・全体確認・部分更新の最新日時で選ぶ。
    guides = []
    for path in sorted(HOWTO_DIR.glob("*.json")):
        try:
            guide = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(guide, dict):
            continue
        touched = latest_guide_timestamp(guide)
        if touched is None:
            continue
        if threshold is None or touched >= threshold:
            guides.append(f"{base}/guides/{guide['slug']}")
    if guides:
        urls += [f"{base}/guides", *guides]

    return urls[:MAX_URLS]


def submit(base: str, key: str, urls: list[str]) -> int:
    payload = {
        "host": base.split("://", 1)[-1],
        "key": key,
        "keyLocation": f"{base}/{key}.txt",
        "urlList": urls,
    }
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status


# lib/indexing.ts の isIndexable と同じ基準（検索に出さない記事は通知しない。条件の説明はそちら）
WIRE_SOURCES = {"prtimes", "prtimes-all", "atpress", "value-press", "kyodo-prwire", "dreamnews"}
# 個人・他媒体の YouTube チャンネル（scripts/channels.py のうち、企業・機関の公式チャンネルではないもの）。
# 他人の動画の内容を要約した記事は、独自の価値が出しにくく「複製されたコンテンツ」（AdSense）や翻案の心配があるので検索に出さない。
# サイト内では読める。公式チャンネル（OpenAI・NVIDIA・楽天証券など）の発表動画は従来どおり対象
CREATOR_CHANNELS = {
    "ai-explained", "two-minute-papers", "bycloud", "matthew-berman",
    "rakumachi", "uraken-fudosan", "yukkuri-fudosan", "mansion-mania", "ienomikata",
    "liberal-arts", "bank-academy", "nikkei",
}


THIN_BODY = 1500
# いつでも主題として扱うジャンル（AI・テック）。お金・不動産・起業・制度は persona.is_persona_topic、スポーツは persona.sports_scope
FOCUS_GENRES = {"ai", "tech-gadget"}
SPORTS_GENRES = {"sports"}
# エンタメはビジネス・テックの目線（料金・決算・販売本数・市場規模・AI・規約）のものだけ主題に入れる（2026-11-03 オーナー決定。scripts/genre_focus.py）
ENTERTAINMENT_GENRES = {"entertainment"}
AI_TOPIC = re.compile(
    r"(?<![A-Za-z])AI(?![a-z])|人工知能|生成|LLM|GPT|Gemini|Claude|Copilot|機械学習|エージェント|ロボット|半導体|NVIDIA",
    re.I,
)
JAPAN_VALUE = re.compile(r"^#{2,3}.*(日本|無料|使い方|料金|始め方|使える)", re.M)
TITLE_NOISE = re.compile(r"[\s「」『』【】()（）・、。！？!?:：|｜\-—〜~\"'“”]")


def _url_key(url: str) -> str:
    return re.sub(r"[?#].*$", "", str(url or "").strip().lower()).rstrip("/")


def duplicate_slugs(articles: list[dict]) -> set[str]:
    """重複記事（lib/article-index.mjs の markDuplicates と同じ）。
    1. duplicateOf（scripts/story.py が付ける）  2. 予備: 同じ題名・出典URLのうち代表以外（本文の長い方、同じなら先に載った方が代表）"""
    keep: set[str] = set()
    out: set[str] = {a.get("slug", "") for a in articles if a.get("duplicateOf") and a.get("duplicateOf") != a.get("slug")}
    items = [a for a in articles if a.get("type") != "roundup" and a.get("slug") not in out and not a.get("angleOf")]
    items.sort(key=lambda a: a.get("generatedAt", ""))
    items.sort(key=lambda a: -len(a.get("body", "")))
    for a in items:
        keys = ["t:" + TITLE_NOISE.sub("", a.get("title", "")).lower()]
        if a.get("sourceUrl"):
            keys.append("u:" + _url_key(a["sourceUrl"]))
        if any(k in keep for k in keys):
            out.add(a.get("slug", ""))
        else:
            keep.update(keys)
    return out


def is_focus(article: dict) -> bool:
    """読者像（scripts/persona.py）の話題か。lib/indexing.ts の isFocus と同じ。
    1. AI・テック（ジャンル ai / tech-gadget、または題名・タグにAI関連語）
    2. 読者像の話題（persona.is_persona_topic: ビジネス・投資・不動産のジャンル、制度・暮らし・テックのジャンルでお金・税・補助金・起業の話題）
    3. エンタメのビジネス・テックの目線（genre_focus.entertainment_focus: 料金・決算・販売本数・市場規模・AI・規約。告知・ゴシップは外す）
    4. 日本のスポーツ（persona.sports_scope。海外のリーグ・選手だけの話題は外す。世界的な大会は海外選手も可）
    それ以外（作品の告知だけのエンタメ・ゲーム、お金に関係しない新商品や研究成果など）は主題外。"""
    text = article.get("title", "") + " " + " ".join(article.get("tags") or [])
    if article.get("genreSlug") in FOCUS_GENRES or AI_TOPIC.search(text):
        return True
    if persona.is_persona_topic(article):
        return True
    if article.get("genreSlug") in ENTERTAINMENT_GENRES:
        return genre_focus.entertainment_focus(article.get("title", ""), article.get("tags"))
    if article.get("genreSlug") in SPORTS_GENRES:
        # 発信元が海外かは記事に残らない。海外のフィードは生成時に sports_scope(japanese_source=False) で落としているので、ここでは目印だけで見る
        return persona.sports_scope(text)[0]
    return False


def noindex_reason(article: dict, dups: set[str] | frozenset = frozenset()) -> str | None:
    if isinstance(article.get("indexable"), bool):
        return None if article["indexable"] else "manual"
    if article.get("type") == "roundup":
        return None
    if article.get("channelSlug") in WIRE_SOURCES:
        return "wire"
    if article.get("channelSlug") in CREATOR_CHANNELS:
        return "creator-video"
    body = article.get("body", "")
    if len(body) < THIN_BODY:
        return "thin"
    if not is_focus(article):
        return "off-focus"
    if not article.get("verdict") and not JAPAN_VALUE.search(body):
        return "no-japan-value"
    if article.get("slug") in dups or (article.get("duplicateOf") and article.get("duplicateOf") != article.get("slug")):
        return "duplicate"
    return None


# ---- 公開前の門（2026-11-03 オーナー承認。AdSense「有用性の低いコンテンツ」への対策） -------------------
# 検索に出せない記事（主題外・日本の読者に役立つ情報がない・薄い・配信の言い換え・他人の動画の要約）は、作っても取り下げるだけなので公開しない。
# 重複（duplicate）は公開後に scripts/story.py が付けて代表へ canonical を向けるので、ここでは止めない。地震・津波の速報（disaster）は短くても出す。
PUBLISH_BLOCK = ("wire", "creator-video", "off-focus", "no-japan-value", "thin")
# 作り直せば直る見込みのあるもの（品質ゲートと同じく、問題点を伝えて作り直す）。それ以外は見送りで確定する
PUBLISH_RETRYABLE = ("thin", "no-japan-value")
PUBLISH_FEEDBACK = {
    "wire": "プレスリリース配信の言い換えは公開しません",
    "creator-video": "個人・他媒体の動画の要約は公開しません",
    "off-focus": "サイトの主題（AI・テック／お金・投資・不動産・起業／制度のお金の話／日本のスポーツ）から外れた話題は公開しません",
    "no-japan-value": (
        "日本の読者に役立つ情報が足りません。日本の読者が自分に関係あるか判断できる章を、資料にある事実だけで書いてください。"
        "見出しは決まった質問（「日本で使える？」「料金は？」）にせず中身の言葉にし、日本・無料・料金・使い方・始め方・使える のどれかを含める"
        "（例「日本で使えるのは12月から」「料金は月200ドルのまま」「日本の売上は1%減」）"
    ),
    "thin": f"本文が短すぎます。元記事にある事実だけで、背景・要点・読者への影響を補い、本文を{THIN_BODY}字以上にしてください",
}


def publish_block_reason(article: dict) -> str | None:
    """公開しない理由（公開してよいなら None）。判定は noindex_reason と同じ（サイトの noindex と食い違わない）"""
    if article.get("disaster") or article.get("type") == "roundup" or isinstance(article.get("indexable"), bool):
        return None
    reason = noindex_reason(article)
    return reason if reason in PUBLISH_BLOCK else None


def candidate_off_focus(lane: str, title: str, summary: str = "") -> bool:
    """生成の前に、幅広い話題の候補がサイトの主題外と分かるか（LLM を使わずに見送る。迷うものは通して公開前の門で見る）。
    レーン（フィードの分野）がジャンルの目安。AI の話題・読者像のジャンル・お金/制度の話題・スポーツ（別の足切りあり）・分野不明は通す"""
    if not lane or lane.startswith("_") or lane in FOCUS_GENRES or lane in SPORTS_GENRES or lane in persona.PERSONA_GENRES:
        return False
    text = f"{title} {summary[:300]}"
    if AI_TOPIC.search(text):
        return False
    if lane in ENTERTAINMENT_GENRES:
        return genre_focus.candidate_problem(lane, title, summary) is not None
    if lane in persona.PERSONA_EXTRA_GENRES and persona.PRIORITY_TOPIC_RE.search(text):
        return False
    return True


# サイト側（lib/indexing.ts）が読む判定の材料。正本はこのファイルと scripts/persona.py で、
# 変えたら `python3 scripts/indexnow.py --export-rules` で書き出す（tests/test_index_rules.py がずれを検出する）
RULES_PATH = ROOT / "lib" / "index-rules.json"


def _js_regex(pattern: re.Pattern) -> dict:
    return {"source": pattern.pattern, "flags": "i" if pattern.flags & re.I else ""}


def index_rules() -> dict:
    return {
        "_note": "scripts/indexnow.py --export-rules で生成。手で直さない（正本は scripts/indexnow.py・scripts/persona.py・scripts/genre_focus.py）",
        "thinBody": THIN_BODY,
        "creatorChannels": sorted(CREATOR_CHANNELS),
        "focusGenres": sorted(FOCUS_GENRES),
        "aiTopic": _js_regex(AI_TOPIC),
        "personaGenres": list(persona.PERSONA_GENRES),
        "personaExtraGenres": list(persona.PERSONA_EXTRA_GENRES),
        "priorityTopic": _js_regex(persona.PRIORITY_TOPIC_RE),
        "entertainmentGenres": sorted(ENTERTAINMENT_GENRES),
        "entertainmentBusiness": _js_regex(genre_focus.ENTERTAINMENT_BUSINESS_RE),
        "entertainmentExclude": _js_regex(genre_focus.ENTERTAINMENT_EXCLUDE_RE),
        "sportsGenres": sorted(SPORTS_GENRES),
        "globalEvent": _js_regex(persona.GLOBAL_EVENT_RE),
        "japanSports": _js_regex(persona.JAPAN_SPORTS_RE),
        "foreignSports": _js_regex(persona.FOREIGN_SPORTS_RE),
    }


def rules_json() -> str:
    return json.dumps(index_rules(), ensure_ascii=False, indent=2) + "\n"


def rules_drift() -> bool:
    """lib/index-rules.json が今の基準とずれているか（サイトと IndexNow の判定が食い違う）"""
    try:
        return RULES_PATH.read_text("utf-8") != rules_json()
    except OSError:
        return True


NEWS_WINDOW = timedelta(days=2)
NEWS_MAX_SOURCE_AGE = timedelta(hours=48)


def _parse(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def news_reason(article: dict, dups: set[str] | frozenset = frozenset(), now: datetime | None = None) -> str | None:
    """ニュース用サイトマップに載せない理由（lib/indexing.ts の newsSitemapReason と同じ）"""
    reason = noindex_reason(article, dups)
    if reason:
        return reason
    if article.get("type") == "roundup":
        return "roundup"
    if article.get("angleOf"):
        return "angle"
    if article.get("late"):
        return "late"
    now = now or datetime.now(timezone.utc)
    published = _parse(article.get("generatedAt") or article.get("publishedAt"))
    if published is None or now - published >= NEWS_WINDOW:
        return "old"
    source = _parse(article.get("sourcePublishedAt") or article.get("publishedAt"))
    if source and published - source > NEWS_MAX_SOURCE_AGE:
        return "stale-source"
    return None


def is_indexable(article: dict, dups: set[str] | frozenset = frozenset()) -> bool:
    return noindex_reason(article, dups) is None


def is_distribution_eligible(article: dict) -> bool:
    """既存の配信選定を維持する。検索の文字数制限とは別の基準。"""
    if isinstance(article.get("indexable"), bool):
        return article["indexable"]
    if article.get("sourceType") != "web" or article.get("type") == "roundup":
        return True
    if article.get("channelSlug") in WIRE_SOURCES:
        return False
    return len(article.get("body", "")) >= 2000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="全記事を送信")
    parser.add_argument("--hours", type=int, default=12, help="直近N時間の記事のみ")
    parser.add_argument("--dry-run", action="store_true", help="送信せず対象を表示")
    parser.add_argument("--urls", default="", help="カンマ区切りの URL だけを送る")
    parser.add_argument("--export-rules", action="store_true", help="サイト側の判定材料 lib/index-rules.json を書き出す")
    args = parser.parse_args()

    if args.export_rules:
        RULES_PATH.write_text(rules_json(), "utf-8")
        print(f"[done] {RULES_PATH.relative_to(ROOT)} を書き出しました")
        return 0
    if rules_drift():
        print("[warn] lib/index-rules.json が scripts/indexnow.py・persona.py の基準とずれています。"
              "python3 scripts/indexnow.py --export-rules で直してください（サイトの noindex と通知対象が食い違います）", file=sys.stderr)

    key = find_key()
    if not key:
        print("[NG] public/<key>.txt が見つかりません", file=sys.stderr)
        return 1

    base = site_url()
    explicit = [u.strip() for u in args.urls.split(",") if u.strip().startswith(base)]
    urls = explicit or collect_urls(base, None if args.all else args.hours)

    print(f"[info] 送信先ホスト: {base} / 対象 {len(urls)} 件")
    for url in urls[:5]:
        print("   ", url)
    if len(urls) > 5:
        print(f"    …ほか {len(urls) - 5} 件")

    if args.dry_run:
        print("[dry-run] 送信しませんでした")
        return 0
    if not explicit and len(urls) <= 3:
        print("[skip] 新規URLがないため送信しません")
        return 0

    try:
        status = submit(base, key, urls)
    except Exception as error:  # noqa: BLE001 - 通知失敗で全体を落とさない
        print(f"[NG] 送信失敗: {error}", file=sys.stderr)
        return 0

    print(f"[done] HTTP {status}（200/202 なら受理）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
