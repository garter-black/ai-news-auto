#!/usr/bin/env python3
"""サイソクの過去記事から「この話題のこれまで」を探す（API 不要）。

新しい web 記事を作る前に、同じ企業・同じ固有名詞を扱った過去記事（直近90日優先）を最大5本選び、
タイトル・日付・提供状況・価格・要旨をプロンプトに渡す。元記事には無い「流れ」が独自の価値になる。

  .venv/bin/python scripts/archive.py "タイトル"      # 関連する過去記事を試しに探す
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dedupe  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
JST = timezone(timedelta(hours=9))
ARCHIVE_DAYS = 90
MAX_RELATED = 5
MIN_SCORE = 4

# lib/companies.ts の COMPANIES と同じ判定（企業が同じなら加点）
COMPANIES = [
    ("openai", r"OpenAI|ChatGPT|GPT-|Codex|Sora\b|\bo3\b", re.I),
    ("anthropic", r"Anthropic|Claude", re.I),
    ("google", r"Google|グーグル|DeepMind|Gemini|Gemma|Pixel|Android|AlphaFold|Antigravity|Nano Banana|SynthID", re.I),
    ("nvidia", r"NVIDIA|エヌビディア|RTX|CUDA|Blackwell|Vera Rubin|DGX|NVLink|Nemotron", re.I),
    ("microsoft", r"Microsoft|マイクロソフト|Azure|Copilot|GitHub|Visual Studio|Windows|Foundry", re.I),
    ("meta", r"\bMeta\b|米メタ|Llama", 0),
    ("huggingface", r"Hugging ?Face|Transformers\.js|smolagents", re.I),
    ("deepseek", r"DeepSeek", re.I),
    ("xai", r"\bxAI\b|Grok", re.I),
    ("mistral", r"Mistral", re.I),
    ("moonshot", r"Kimi|Moonshot", re.I),
]
_COMPANY_RES = [(slug, re.compile(pattern, flags)) for slug, pattern, flags in COMPANIES]

STATUS_LABEL = {"available": "提供中", "limited": "一部提供", "soon": "提供予定", "demo": "研究・デモ"}
# 本文中の内部リンク [..](/articles/slug)
INTERNAL_LINK_RE = re.compile(r"\[([^\]]+)\]\(/articles/([A-Za-z0-9][A-Za-z0-9_-]*)\)")


def company_of(text: str) -> str:
    """文章中で最も手前に出てくる企業の slug（無ければ other）"""
    best = ("other", 10**9)
    for slug, pattern in _COMPANY_RES:
        if (m := pattern.search(text or "")) and m.start() < best[1]:
            best = (slug, m.start())
    return best[0]


def _when(data: dict) -> datetime | None:
    return dedupe._article_time(data)


def load_archive(days: int = ARCHIVE_DAYS) -> list[dict]:
    """直近 days 日の記事（軽い要約だけ）。新しい順"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        when = _when(data)
        if when is None or when < since or not data.get("title"):
            continue
        keywords = [str(k) for k in (data.get("keywords") or [])][:8]
        rows.append(
            {
                "slug": path.stem,
                "title": str(data["title"]),
                "at": when,
                "genreSlug": data.get("genreSlug") or "ai",
                "type": data.get("type") or "",
                "sourceUrl": str(data.get("sourceUrl") or ""),
                "verdict": data.get("verdict") or None,
                "gist": str(data.get("description") or data.get("lede") or "")[:110],
                "company": company_of(f"{data.get('title', '')} {data.get('channel', '')}"),
                "entities": dedupe.entities(str(data["title"]), *keywords),
                "bigrams": dedupe.bigrams(str(data["title"])),
            }
        )
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows


class Archive:
    def __init__(self, days: int = ARCHIVE_DAYS) -> None:
        self.rows = load_archive(days)
        self.df: dict[str, int] = {}
        for row in self.rows:
            for e in row["entities"]:
                self.df[e] = self.df.get(e, 0) + 1

    def related(self, title: str, text: str = "", url: str = "", genre_slug: str = "", limit: int = MAX_RELATED) -> list[dict]:
        """関連する過去記事（スコア順・最大 limit 本）。弱い一致は返さない"""
        rare = lambda ents: {e for e in ents if self.df.get(e, 0) < dedupe.COMMON_ENTITY_DF * 2}  # noqa: E731
        title_ents = rare(dedupe.entities(title))
        text_ents = rare(dedupe.entities(text[:2500])) - title_ents
        grams = dedupe.bigrams(title)
        company = company_of(f"{title} {text[:600]}")
        now = datetime.now(timezone.utc)
        scored = []
        norm_url = dedupe._norm_url(url) if url else ""
        for row in self.rows:
            if norm_url and row["sourceUrl"] and dedupe._norm_url(row["sourceUrl"]) == norm_url:
                continue
            shared_title = title_ents & row["entities"]
            shared_text = text_ents & row["entities"]
            if not shared_title and len(shared_text) < 2:
                continue
            same_company = company != "other" and row["company"] == company
            score = 3 * len(shared_title) + len(shared_text) + (2 if same_company else 0)
            score += round(6 * dedupe.jaccard(grams, row["bigrams"]))
            if genre_slug and row["genreSlug"] != genre_slug:
                score -= 2
            age_days = (now - row["at"]).days
            score -= age_days // 30  # 新しいほど優先
            if score >= MIN_SCORE:
                scored.append((score, row))
        scored.sort(key=lambda x: (x[0], x[1]["at"]), reverse=True)
        return [row for _, row in scored[:limit]]


def facts_line(row: dict) -> str:
    """プロンプト・事実確認に渡す1行（日付・タイトル・状況・価格・要旨）"""
    date = row["at"].astimezone(JST).strftime("%Y-%m-%d")
    parts = [f"{date}「{row['title']}」"]
    verdict = row.get("verdict") or {}
    if isinstance(verdict, dict) and verdict.get("status"):
        parts.append(f"状況: {STATUS_LABEL.get(verdict['status'], verdict['status'])}")
    if isinstance(verdict, dict) and verdict.get("price") and verdict["price"] != "不明":
        parts.append(f"価格: {verdict['price']}")
    if row.get("gist"):
        parts.append(f"要旨: {row['gist']}")
    return " / ".join(parts)


def prompt_block(rows: list[dict]) -> str:
    """生成プロンプトに足す「サイソクの過去記事（文脈）」。関連記事が無ければ空文字"""
    if not rows:
        return ""
    lines = "\n".join(f"- [{row['slug']}] {facts_line(row)}" for row in rows)
    return f"""

# サイソクの過去記事（文脈）
サイソクが過去に報じた、この話題に関連しそうな記事です（[ ] 内は記事の slug）。
{lines}

## 過去記事の使い方（必ず守る）
- 「## これまでの流れ」の章（日付の箇条書き）は作らない（2026-11-03。過去の日付・順番の取り違えが事実確認で多く見つかったため）。
  今回のニュースと本当につながる過去記事があれば、本文の中で1〜2文だけ触れる（例「Googleは10月に〇〇を始めていました（[短い題名](/articles/slug)）」）。
  一覧の日付は「サイソクが報じた日」なので、発表の日付として書かない。
- 過去記事について書いてよいのは、上の一覧にある事実（日付・タイトル・状況・価格・要旨）だけ。数値や経緯を新たに足さない。
- 過去記事に触れるときは、その記事へのリンクを `[短い題名](/articles/slug)` の形で付ける（一覧にある slug 以外は使わない）。
- つながりが薄い過去記事は使わない。
- 実際に使った過去記事の slug を related_used に入れる（使わなければ空配列）。
- 前の版や過去の発表と本当に比べられる数値（価格・プラン・スペックなど）が複数あれば、コンパクトな Markdown の比較表を1つだけ入れる
  （表の中身は元記事と上の一覧にある事実だけ。「表の使い方」に従い、上に1行の太字の結論、最後の列に「変化」）。比べるものが無ければ表は作らない。"""


def check_block(rows: list[dict]) -> str:
    """事実確認に渡す文脈（ここにある事実は創作扱いしない）"""
    if not rows:
        return ""
    return "\n".join(f"- {facts_line(row)}" for row in rows)


def clean_internal_links(body: str, allowed: set[str]) -> tuple[str, set[str]]:
    """渡していない slug への内部リンクは文字だけ残す。(本文, 実際にリンクした slug)"""
    used: set[str] = set()

    def repl(m: re.Match) -> str:
        if m.group(2) in allowed:
            used.add(m.group(2))
            return m.group(0)
        return m.group(1)

    return INTERNAL_LINK_RE.sub(repl, body or ""), used


def main() -> int:
    archive = Archive()
    print(f"直近{ARCHIVE_DAYS}日の記事 {len(archive.rows)} 件")
    for title in sys.argv[1:]:
        print(f"\n■ {title}")
        for row in archive.related(title):
            print(f"  - {facts_line(row)[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
