#!/usr/bin/env python3
"""配信用に「直近の主要記事」を選ぶ共通部品（newsletter.py / line_digest.py / crosspost_drafts.py）。

並び順: 大きなニュース（major）→ まとめ記事 → AI ジャンル → 使える判定 → 品質点 → 新しさ。
配信対象は検索対象とは分けて、従来の配信基準を維持する。

  python3 scripts/digest.py --hours 24 --n 5   # 選ばれる記事を表示
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
from indexnow import is_distribution_eligible  # noqa: E402
import persona  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
JST = timezone(timedelta(hours=9))


def site_url() -> str:
    return (os.environ.get("NEXT_PUBLIC_SITE_URL") or "https://ai-tech-express.jp").rstrip("/")


def load_articles() -> list[dict]:
    articles = []
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            articles.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return articles


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def score(article: dict) -> float:
    verdict = article.get("verdict") or {}
    s = 0.0
    if article.get("major"):
        s += 100
    if article.get("type") == "roundup":
        s += 40
    if article.get("genreSlug") == "ai":
        s += 30
    if verdict.get("status") == "available":
        s += 8
    if verdict.get("japan") == "ok":
        s += 4
    s += float(article.get("qualityScore") or 0) / 10
    s += min(len(article.get("body", "")), 6000) / 1000  # 厚い記事を少し優先
    return s


def pick_top(hours: int = 24, n: int = 5, now: datetime | None = None, ai_only: bool = False,
             persona_topics: bool = False) -> list[dict]:
    """persona_topics=True: 読者像の話題（scripts/persona.py の is_persona_topic。スポーツなし）から、お金・不動産・起業の話題も
    AI と同じ重みで選ぶ（はてなブログの下書き用）"""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    picked = []
    seen_roundups: set[str] = set()
    for article in load_articles():
        at = _parse(article.get("generatedAt"))
        if not at or not (since <= at <= now) or not is_distribution_eligible(article):
            continue
        if ai_only and article.get("genreSlug") != "ai":
            continue
        if persona_topics and not persona.is_persona_topic(article):
            continue
        picked.append(article)
    boost = persona.topic_boost if persona_topics else (lambda a: 0.0)
    picked.sort(key=lambda a: (score(a) + boost(a), a.get("generatedAt", "")), reverse=True)
    result = []
    for article in picked:
        # 同じまとめに属する記事は1本だけ（似た話が並ばないように）
        group = article.get("roundupSlug") or article.get("roundupKey") or ""
        if group and group in seen_roundups:
            continue
        if group:
            seen_roundups.add(group)
        result.append(article)
        if len(result) >= n:
            break
    return result


def title_of(article: dict) -> str:
    return str(article.get("seoTitle") or article.get("title") or "").strip()


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip("、。 ") + "…"


def summary_lines(article: dict, limit: int = 70) -> list[str]:
    """2行の要点。highlights を優先し、無ければ lede を2文に分ける"""
    lines = [h for h in (article.get("highlights") or []) if str(h).strip()][:2]
    if len(lines) < 2:
        sentences = [s for s in re.split(r"(?<=。)", str(article.get("lede") or "")) if s.strip()]
        lines += sentences[: 2 - len(lines)]
    return [_clip(str(line), limit) for line in lines]


def article_url(slug: str, source: str, campaign: str = "") -> str:
    q = f"utm_source={source}&utm_medium={'email' if source == 'newsletter' else 'social'}"
    if campaign:
        q += f"&utm_campaign={campaign}"
    return f"{site_url()}/articles/{slug}?{q}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--n", type=int, default=5)
    args = parser.parse_args()
    for a in pick_top(args.hours, args.n):
        print(f"{score(a):6.1f}  {a.get('generatedAt', '')[:16]}  {title_of(a)}")
        for line in summary_lines(a):
            print(f"        - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
