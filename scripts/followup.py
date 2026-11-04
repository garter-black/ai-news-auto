#!/usr/bin/env python3
"""続報で既存記事を育てる（同じ話題の新しいページを増やさない）。

直近14日の記事と同じ話題の新しい発表が来たら:
  1) 安い LLM（bulk）で「同じ話題か / 新しい事実があるか」を判定
  2) 新しい事実があれば「## 続報（M月D日）」の章を生成し、元記事との事実確認に通ったものだけ既存記事に差し込む
  3) updates / updatedAt / sources を記録する（generatedAt＝作成日時は変えない）
まとめ記事と14日より古い記事は対象外（新しい記事として作る）。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dedupe  # noqa: E402
import llm  # noqa: E402
import quality  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
JST = timezone(timedelta(hours=9))
FOLLOWUP_DAYS = 14
MAX_UPDATES_PER_ARTICLE = 5
UPDATE_RANGE = (300, 1100)  # 続報本文の字数（指示は400〜900字）
SOURCE_CAP = 6000

CHECK_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "same_topic": {"type": "BOOLEAN"},
        "adds_new_facts": {"type": "BOOLEAN"},
        "summary_of_new_facts": {"type": "STRING"},
    },
    "required": ["same_topic", "adds_new_facts", "summary_of_new_facts"],
    "propertyOrdering": ["same_topic", "adds_new_facts", "summary_of_new_facts"],
}

CHECK_SYSTEM = "あなたはニュースサイトのデスクです。新しく届いた発表が、既に載せた記事の続報かどうかを判定します。出力は指定の JSON だけ。"

CHECK_PROMPT = """# 既に載せた記事（{published}）
タイトル: {title}
概要: {description}
要点: {highlights}
本文の冒頭:
<<<
{body_head}
>>>

# 新しく届いた発表
発信元: {source_name}
タイトル: {new_title}
本文（抜粋）:
<<<
{new_text}
>>>

# 判定
- same_topic: 同じ製品・サービス・制度・企業の、同じ出来事またはその直接の続き（提供開始・料金決定・対象拡大・延期・不具合など）なら true。
  同じ企業でも別の製品・別の出来事なら false。
- adds_new_facts: same_topic が true で、既存記事に無い具体的な事実（日付・価格・対象・数値・提供状況の変化など）があれば true。
  同じ内容の言い換え・転載・宣伝だけなら false。
- summary_of_new_facts: 新しい事実を1〜3文で（無ければ空文字）。"""

UPDATE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "update_heading": {"type": "STRING"},
        "update_markdown": {"type": "STRING"},
        "easy_update": {"type": "STRING"},
        "revised_description": {"type": "STRING"},
        "revised_highlights": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["update_heading", "update_markdown", "easy_update"],
    "propertyOrdering": ["update_heading", "update_markdown", "easy_update", "revised_description", "revised_highlights"],
}

UPDATE_SYSTEM = """あなたは日経クロステック / ITmedia で10年書いてきたベテランの記者です。
既に公開した記事に「続報」の章を書き足します。新しい一次情報に書かれた事実だけを使い、推測・誇張はしません。
報道記事の敬体（です・ます）。感嘆符・煽り語は使わない。出力は指定の JSON だけ。"""

UPDATE_PROMPT = """# 既に公開している記事
タイトル: {title}
概要: {description}
要点:
{highlights}
本文の冒頭:
<<<
{body_head}
>>>

# 新しい一次情報（続報の材料）
- 発信元: {source_name}
- タイトル: {new_title}
- 公開日: {new_date}
- URL: {url}
- 新しい事実の要約（デスクのメモ）: {summary}
- 本文（抜粋・自動抽出。メニュー等の残骸は無視）:
<<<
{new_text}
>>>

# 書くもの
- update_heading: 続報の見出し（15〜30字）。何が新しく分かったかが分かる平叙文。「続報」「日付」は入れない（自動で付く）。
- update_markdown: 続報の本文（**400〜900字**、Markdown。`##` 見出しは使わない。必要なら `###` は可）。
  - 新しい一次情報に書かれた事実だけを書く。既存記事と何が変わった・加わったのかを先に書く。
  - 冒頭の文で発信元に触れる（例：「{source_name}は〇月〇日、…と発表しました」）。
  - 元の文をそのまま訳したり書き写したりしない。引用は30字程度までの短いフレーズを「」で。
  - 既存記事と矛盾する新事実（価格・日付の変更など）は「当初は…でしたが、…に変わりました」と明示する。
- easy_update: 「かんたん版」に足す続報（100〜250字、です・ます調、専門用語なし）。update_markdown に無い事実は書かない。
- revised_description: 続報を反映した記事の概要（100〜130字）。既存の概要で足りるなら空文字。
- revised_highlights: 続報を反映した要点ちょうど3つ（各40〜60字）。1つ目は、この記事の一番のニュース（題名・リードの1文目と同じ中心の事実）を、リードとは違う言い方で書く（リードの文をそのまま写さない。題名の主な語を入れる）。2つ目と3つ目は、リードに無い事実を入れる。既存のままで良ければ空配列。
  既存の要点の事実と新しい一次情報の事実以外は書かない。"""


# 同じ話題かの判定に使うモデルの段（速報レーンは quality にする。web_articles.py が切り替える）
CHECK_TIER = "bulk"


def _parse_time(value: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def load_target(slug: str, days: int = FOLLOWUP_DAYS) -> tuple[Path, dict] | None:
    """続報を足してよい記事なら (path, data)。まとめ記事・古い記事・続報が多すぎる記事は None"""
    if not slug or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", slug):
        return None
    path = ARTICLES_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("type") == "roundup" or data.get("disaster"):
        return None  # 地震・津波の記事は気象庁の電文だけで書き直す（scripts/quake.py）。AI の続報を足さない
    created = _parse_time(data.get("generatedAt") or data.get("publishedAt") or "")
    if created is None or created < datetime.now(timezone.utc) - timedelta(days=days):
        return None
    if len(data.get("updates") or []) >= MAX_UPDATES_PER_ARTICLE:
        return None
    return path, data


def _body_head(body: str, limit: int = 1800) -> str:
    return (body or "")[:limit]


def check_same_topic(article: dict, item: dict, text: str) -> dict:
    """{same_topic, adds_new_facts, summary_of_new_facts, model}（bulk・小さな JSON）"""
    published = _parse_time(article.get("generatedAt") or "")
    prompt = CHECK_PROMPT.format(
        published=published.astimezone(JST).strftime("%Y-%m-%d") if published else "",
        title=article.get("title", ""),
        description=article.get("description", ""),
        highlights=" / ".join(article.get("highlights") or []),
        body_head=_body_head(article.get("body", ""), 1500),
        source_name=item["source_name"],
        new_title=item["title"],
        new_text=text[:4000],
    )
    data, model = llm.generate_json(CHECK_SYSTEM, prompt, CHECK_SCHEMA, temperature=0.1, max_output_tokens=1024, tier=CHECK_TIER,
                                    purpose="check")
    return {
        "same_topic": bool(data.get("same_topic")),
        "adds_new_facts": bool(data.get("adds_new_facts")),
        "summary_of_new_facts": str(data.get("summary_of_new_facts") or "").strip(),
        "model": model,
    }


def generate_update(article: dict, item: dict, text: str, summary: str, tier: str) -> tuple[dict, str]:
    prompt = UPDATE_PROMPT.format(
        title=article.get("title", ""),
        description=article.get("description", ""),
        highlights="\n".join(f"- {h}" for h in article.get("highlights") or []),
        body_head=_body_head(article.get("body", "")),
        source_name=item["source_name"],
        new_title=item["title"],
        new_date=item["published_at"].astimezone(JST).strftime("%Y-%m-%d"),
        url=item["url"],
        summary=summary or "（なし）",
        new_text=text[:SOURCE_CAP],
    )
    return llm.generate_json(UPDATE_SYSTEM, prompt, UPDATE_SCHEMA, temperature=0.4, max_output_tokens=4096, tier=tier, purpose="core")


def update_problems(data: dict, source_text: str) -> list[str]:
    """続報の形のチェック（API 不要）"""
    problems = []
    body = str(data.get("update_markdown") or "")
    length = quality._plain_len(body)
    if not UPDATE_RANGE[0] <= length <= UPDATE_RANGE[1]:
        problems.append(f"続報の長さが不適切（{length}字 / 400〜900字）")
    if not str(data.get("update_heading") or "").strip():
        problems.append("続報の見出しが空")
    if quality._plain_len(str(data.get("easy_update") or "")) < 60:
        problems.append("かんたん版の続報が短い")
    if re.search(r"^#{1,2}\s", body, re.M):
        problems.append("続報本文に # / ## 見出しがある")
    if m := quality.BOILERPLATE_RE.search(body):
        problems.append(f"Webページの英語の残骸（「{m.group(0)}」）")
    if len(quality.copied_sentences(body, source_text)) >= 2:
        problems.append("元記事の文をそのまま書き写している")
    return problems


def fact_check_update(article: dict, data: dict, source_text: str) -> dict:
    """続報部分だけを新しい一次情報と突き合わせる。既存の要点・概要は確認済みの事実として渡す"""
    pseudo = {
        "title": str(data.get("update_heading") or ""),
        "lede": str(data.get("revised_description") or ""),
        "highlights": [str(h) for h in data.get("revised_highlights") or []],
        "body": str(data.get("update_markdown") or ""),
        "easy": str(data.get("easy_update") or ""),
    }
    known = "\n".join(
        [f"タイトル: {article.get('title', '')}", f"概要: {article.get('description', '')}"]
        + [f"要点: {h}" for h in article.get("highlights") or []]
    )
    return quality.fact_check(pseudo, source_text, context=known)


def _insert_after_first_section(markdown: str, section: str) -> str:
    """最初の章（「## 概要」、古い記事は「## ひとことで言うと」）の直後（無ければ先頭）に差し込む。新しい続報ほど上に来る"""
    text = markdown or ""
    if re.match(r"## (概要|ひとことで言うと)\s*\n", text.lstrip()):
        m = re.search(r"^## (?!概要\s*$|ひとことで言うと)", text, re.M)
        if m:
            return text[: m.start()].rstrip() + "\n\n" + section.strip() + "\n\n" + text[m.start():]
        return text.rstrip() + "\n\n" + section.strip() + "\n"
    return section.strip() + "\n\n" + text.lstrip()


def apply_update(article: dict, data: dict, item: dict, now: datetime | None = None) -> dict:
    """続報を差し込んだ新しいレコード（元の dict は変えない）"""
    now = now or datetime.now(timezone.utc)
    record = json.loads(json.dumps(article, ensure_ascii=False))
    local = now.astimezone(JST)
    label = f"続報（{local.month}月{local.day}日）"
    heading = re.sub(r"^[#\s]+|[\s。]+$", "", str(data.get("update_heading") or "").strip())
    heading = re.sub(r"^続報[:：\s]*", "", heading)
    cite = f"出典: [{item['source_name']}「{item['title'][:60]}」]({item['url']})"
    body_section = f"## {label}：{heading}\n\n{str(data['update_markdown']).strip()}\n\n{cite}"
    record["body"] = _insert_after_first_section(record.get("body", ""), body_section)
    if str(record.get("easy") or "").strip():
        easy_section = f"## {label}\n\n{str(data.get('easy_update') or '').strip()}"
        record["easy"] = _insert_after_first_section(record["easy"], easy_section)

    description = str(data.get("revised_description") or "").strip()
    if 80 <= len(description) <= 140:
        record["description"] = description
    highlights = [str(h).strip() for h in data.get("revised_highlights") or [] if str(h).strip()]
    if len(highlights) == 3:
        record["highlights"] = highlights

    # 出典の一覧（最初の出典も入れておく）
    sources = [s for s in record.get("sources") or [] if isinstance(s, dict) and s.get("url")]
    if not sources:
        first_url = record.get("sourceUrl") or record.get("videoUrl") or ""
        if first_url:
            sources.append({"title": record.get("videoTitle", ""), "url": first_url, "source": record.get("sourceName") or record.get("channel", "")})
    if all(dedupe._norm_url(s["url"]) != dedupe._norm_url(item["url"]) for s in sources):
        sources.append({"title": item["title"], "url": item["url"], "source": item["source_name"]})
    record["sources"] = sources
    record.setdefault("updates", []).append(
        {"at": now.isoformat(), "title": heading, "sourceUrl": item["url"], "sourceName": item["source_name"]}
    )
    record["updatedAt"] = now.isoformat()
    return record
