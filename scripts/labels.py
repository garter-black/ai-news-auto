"""記事のジャンル（タブ）とタグの付け間違いを直す（2026-11-03 の点検 11。台帳 A30）。

ジャンルはもともと「どのフィード（発信元）から来たか」で決まっていたため、次の付け間違いが出ていた。
- AI の話ではない記事が「AI・テクノロジー」（kubell のストレージの Mac 用アプリ・サムスンのイヤホン・iOS の修正版など）
- Apple の決算が「テクノロジー・ガジェット」（決算は投資・マネー。台帳 A23「大手企業の決算（数字つき）」）
- IPA（独立行政法人）の公表・手引きが「テクノロジー・ガジェット」（国・自治体・公的機関の発表は社会・行政）

ここは「記事の中身（見出し・タグ）」でジャンルを決め直す1か所。記事を作るとき（scripts/web_articles.py の build_web_record）と、
今ある記事の見直し（scripts/label_check.py。Haiku でも確かめる）と、毎日の見張り（scripts/owner_rules_audit.py）が同じ判定を使う。

企業の札（どの企業の発表か）はサイト側の lib/companies.ts（companyOf。見出しの主語を先に見る）。
記事 JSON に companySlug があればそれを優先する（label_check.py が Haiku の確認で直したもの）。

タグは、会社名のカタカナ・英字の混ざり（「マイクロソフト」と「Microsoft」、「アトラシアン」と「Atlassian」）と、
同じ言葉の表記ゆれ（「サイバーセキュリティー」）をそろえる（normalize_tags）。会社名の形は見出しと同じ（headline_normalize.COMPANY_FORMS）。
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genre_focus  # noqa: E402  投資・マネー／不動産／エンタメへの振り分け（route_section）
import headline_normalize as hn  # noqa: E402  会社名の形（COMPANY_FORMS）
import indexnow  # noqa: E402  AI の話題の目印（AI_TOPIC。検索に出す基準と同じ）


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


# 見出しの主語（最初の「、」「が」「は」「と」の前。かっこの中は見ない）
def subject_of(title: str) -> str:
    t = _nfkc(title).strip()
    depth = 0
    for i, ch in enumerate(t[:40]):
        if ch in "「『(（":
            depth += 1
        elif ch in "」』)）" and depth:
            depth -= 1
        elif depth == 0 and ch in "、が":
            return t[:i]
    return ""


# 国・自治体・公的機関（独立行政法人・公的な団体を含む）。見出しの主語がこれなら社会・行政
PUBLIC_BODY_RE = re.compile(
    r"^(?:IPA|情報処理推進機構|デジタル庁|総務省|経済産業省|経産省|財務省|財務相|.{1,4}財務相|内閣府|内閣官房|首相官邸|政府|政府広報|"
    r"金融庁|消費者庁|国税庁|特許庁|文化庁|観光庁|中小企業庁|資源エネルギー庁|個人情報保護委員会|公正取引委員会|"
    r"厚生労働省|厚労省|国土交通省|国交省|文部科学省|文科省|農林水産省|農水省|環境省|法務省|外務省|防衛省|警察庁|消防庁|"
    r"総務省統計局|統計局|.{1,6}経済産業局|.{1,6}財務局|日本年金機構|中小機構|支払基金|JETRO|ジェトロ|NICT|情報通信研究機構|"
    r"東京都|北海道|(?:大阪|京都)府|.{1,3}県|.{1,4}市役所|国立.{1,10}|独立行政法人.{1,12})"
)
# 決算・業績（数字つき）。大手企業の決算は投資・マネー（台帳 A23）
EARNINGS_RE = re.compile(r"決算|売上高|営業利益|純利益|最終利益|増収|減収|増益|減益|最高益|業績予想")
NUMBER_RE = re.compile(r"\d")
# 科学・宇宙（AI の話ではない研究・宇宙開発）
SCIENCE_RE = re.compile(r"NASA|JAXA|宇宙|ロケット|月面|月周回|天文|探査機|衛星|アルテミス|素粒子|量子(?!コンピュータ)")
# ジャンルを動かしてよい元のジャンル（エンタメ・スポーツ・科学・地震などは動かさない）
MOVABLE = ("ai", "tech-gadget", "business", "lifestyle", "public")


# AI の会社・製品の名前（「OpenAI」は indexnow.AI_TOPIC の「AI」の目印に当たらない。英字の後ろの AI は数えないため）
AI_NAMES_RE = re.compile(
    r"OpenAI|Anthropic|DeepMind|DeepSeek|Mistral|(?<![A-Za-z])xAI(?![A-Za-z])|Hugging ?Face|Perplexity|Codex|(?<![A-Za-z])Sora(?![A-Za-z])|"
    r"Grok|Llama|Gemma|Nemotron|Kimi|Moonshot|Sakana|ElevenLabs|Midjourney|Stable Diffusion|Dify|(?<![A-Za-z])MCP(?![A-Za-z])|(?<![A-Za-z])RAG(?![A-Za-z])|"
    r"Siri|Alexa|Bedrock|SageMaker|Vertex|Watsonx|Agentforce|音声合成|音声認識|画像認識|推論",
    re.I,
)


def ai_topic(title: str, tags: list | tuple | None = None) -> bool:
    """AI の話か（見出しとタグ。検索に出す基準 indexnow.AI_TOPIC の目印と、AI の会社・製品の名前）"""
    text = f"{title} {' '.join(str(t) for t in (tags or []))}"
    return bool(indexnow.AI_TOPIC.search(text) or AI_NAMES_RE.search(text))


def genre_fix(record: dict) -> tuple[str, str] | None:
    """ジャンルの付け間違いなら (正しいジャンルの slug, 理由)。合っていれば None。
    見出し・タグだけで決める（本文は見ない。生成の前後とサイトの表示で同じ結果にするため）"""
    genre = str(record.get("genreSlug") or "")
    if genre not in MOVABLE:
        return None
    title = _nfkc(str(record.get("title") or ""))
    tags = [str(t) for t in (record.get("tags") or [])]
    subject = subject_of(title)
    public = bool(PUBLIC_BODY_RE.match(subject or title))
    earnings = bool(EARNINGS_RE.search(title) and NUMBER_RE.search(title))
    if genre == "ai":
        if ai_topic(title, tags):
            return None
        if public:
            return "public", "AI の話ではない国・公的機関の発表（社会・行政）"
        if earnings:
            return "investment", "AI の話ではない決算（投資・マネー）"
        if SCIENCE_RE.search(title):
            return "science", "AI の話ではない科学・宇宙の話題（科学・宇宙）"
        return "tech-gadget", "AI の話ではない製品・サービスの話題（テクノロジー・ガジェット）"
    # 投資・マネー／不動産／エンタメの話題は、そのジャンルへ（候補の振り分けと同じ判定）
    routed = genre_focus.route_section({"title": title}, genre)
    if routed and routed != genre:
        return routed, f"{routed} の話題（genre_focus.route_section）"
    if earnings and not public:
        return "investment", "決算・業績の記事（投資・マネー。台帳 A23）"
    if public and genre != "public":
        return "public", "国・自治体・公的機関の発表（社会・行政）"
    return None


# --------------------------------------------------------------------------- #
# タグ
# --------------------------------------------------------------------------- #
# 会社名（見出しと同じ形にそろえる）と、表記ゆれ
TAG_FORMS: list[tuple[str, list[str]]] = [*hn.COMPANY_FORMS, ("Samsung", ["サムスン", "サムスン電子"]), ("サイバーセキュリティ", ["サイバーセキュリティー"])]
_TAG_MAP = {_nfkc(v).casefold(): canon for canon, variants in TAG_FORMS for v in [canon, *variants]}


def normalize_tags(tags: list | tuple | None) -> list[str]:
    """タグをそろえる（会社名の形・表記ゆれ。同じものは1つに。順番は保つ）"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        tag = _nfkc(str(raw)).strip()
        if not tag:
            continue
        tag = _TAG_MAP.get(tag.casefold(), tag)
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out
