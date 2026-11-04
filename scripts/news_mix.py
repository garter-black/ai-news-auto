#!/usr/bin/env python3
"""1日の記事の配分（2026-11-04 オーナー承認のピボット・台帳 A41）。

読まれないいちばんの理由は「何を選ぶか」だった（直近の約7割が海外の企業向け・開発者向けの発表）。
候補を7つの種類に分け、1日の割合のめやすに近づける:
  障害・情報流出・サポート終了・注意喚起 15% / 料金・制度・お金の変更 20% / 有名ブランドの新製品・日本の発売 25% /
  一般向けの大きな AI の動き 20% / 日本の大手企業の決算・提携 10% / 目を引く技術・研究 5% /
  企業向け・開発者向けの海外の発表 5%まで（上限。大きなニュースは上限の外）

調整値（割合・判定の語・発信元）は scripts/news_mix.json。ここには値を書き込まない。
使い方（scripts/web_articles.py）:
  classify(item)            … 候補の種類
  today_counts(state)       … 今日作った記事の種類ごとの本数（processed_web の mix の印）
  order_key(item, counts)   … レーンの中の並び（今日まだ足りない種類を先に・企業向けは最後）
  over_cap(item, counts)    … 上限のある種類（企業向け・開発者向け）が今日の上限に達したか
API を使わない・通信しない。読めない設定は既定値で動く（記事づくりを止めない）。
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "scripts" / "news_mix.json"
JST = timezone(timedelta(hours=9))


def load_config(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or CONFIG_PATH).read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("daily_basis", 30)
    data.setdefault("order_by_mix", True)
    data.setdefault("categories", {"other": {"label": "そのほか", "share": 0.0}})
    data.setdefault("patterns", {})
    data.setdefault("sources", {})
    return data


CONFIG = load_config()


def _re(name: str) -> re.Pattern | None:
    pattern = (CONFIG.get("patterns") or {}).get(name)
    if not pattern:
        return None
    try:
        return re.compile(pattern, re.I)
    except re.error:
        return None


PATTERNS = {name: _re(name) for name in (CONFIG.get("patterns") or {})}
SOURCES = {name: set(slugs) for name, slugs in (CONFIG.get("sources") or {}).items()}


def _hit(name: str, text: str) -> bool:
    pattern = PATTERNS.get(name)
    return bool(pattern and pattern.search(text))


def classify(item: dict) -> str:
    """候補（フィードの項目）か記事の種類。判定の順: 注意喚起 → 企業向け（発信元・語）→ 一般向け AI → お金 → ブランド → 日本の大手 → 研究 → そのほか"""
    import public_interest  # noqa: PLC0415 - 公共性の判定と同じ語を使う

    title = unicodedata.normalize("NFKC", str(item.get("title") or ""))
    source = str(item.get("source_slug") or item.get("channelSlug") or "")
    # 公共性の種類は、事件・注意喚起の形の題名だけ（「詐欺対策の新機能」「脆弱性の発見」のような製品の話は注意喚起にしない）
    pi_cat = item.get("_public_interest") or item.get("publicInterest") or public_interest.record_category({"title": title})
    if pi_cat in ("breach", "missile", "disaster", "recall", "scam") or _hit("alert", title):
        return "alert"
    consumer_ai = _hit("ai_consumer", title)
    # 企業向け・開発者向けの発信元の記事と、企業向けの語のある題名は、AI の名前があっても企業向け（大きなニュースは上限の外）
    if source in SOURCES.get("enterprise_dev", set()) or _hit("enterprise_dev", title):
        return "enterprise_dev"
    if consumer_ai and source not in SOURCES.get("brand", set()):
        return "ai_consumer"  # 一般向けの AI の新機能・無料の範囲の変更（料金の語があっても AI の動きとして数える）
    if pi_cat == "money" or _hit("money", title):
        return "money"
    if (source in SOURCES.get("brand", set()) or _hit("brand", title)) and (_hit("brand_event", title) or source in SOURCES.get("brand", set())):
        if not consumer_ai or source in SOURCES.get("brand", set()):
            return "brand"
    if consumer_ai:
        return "ai_consumer"
    if _hit("jp_corp", title) and _hit("jp_corp_names", title):
        return "jp_corp"
    if source in SOURCES.get("quirky", set()) or _hit("quirky", title):
        return "quirky"
    return "other"


def targets(basis: int | None = None) -> dict[str, float]:
    basis = int(basis or CONFIG.get("daily_basis") or 30)
    return {name: float(spec.get("share") or 0) * basis for name, spec in (CONFIG.get("categories") or {}).items()}


def cap_of(name: str, basis: int | None = None) -> int | None:
    """1日の上限（cap=true の種類だけ）。割合×本数のめやすを切り上げ、少なくとも1本"""
    spec = (CONFIG.get("categories") or {}).get(name) or {}
    if not spec.get("cap"):
        return None
    return max(1, math.ceil(targets(basis).get(name, 0)))


def today_counts(state: dict, now: datetime | None = None) -> dict[str, int]:
    """今日（JST）作った記事の種類ごとの本数（processed_web の mix の印。印の無い古い記録は数えない）"""
    today = (now or datetime.now(JST)).astimezone(JST).date()
    counts: dict[str, int] = {}
    for rec in (state.get("processed_web") or {}).values():
        if not isinstance(rec, dict) or not rec.get("slug") or not rec.get("mix"):
            continue
        try:
            at = datetime.fromisoformat(str(rec.get("at")))
        except ValueError:
            continue
        if (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(JST).date() == today:
            counts[rec["mix"]] = counts.get(rec["mix"], 0) + 1
    return counts


def deficit(name: str, counts: dict[str, int]) -> float:
    """今日のめやすまでの残り（多いほど先に作る）"""
    return targets().get(name, 0.0) - counts.get(name, 0)


def order_key(item: dict, counts: dict[str, int]) -> tuple:
    """レーンの中の並び。上限のある種類（企業向け・開発者向け）は最後、残りは今日まだ足りない種類から"""
    if not CONFIG.get("order_by_mix", True):
        return (0, 0.0)
    name = item.get("_mix") or classify(item)
    if cap_of(name) is not None:
        return (2, 0.0)
    gap = deficit(name, counts)
    return (0 if gap > 0 else 1, -gap)


def over_cap(item: dict, counts: dict[str, int]) -> bool:
    name = item.get("_mix") or classify(item)
    cap = cap_of(name)
    return cap is not None and counts.get(name, 0) >= cap


def summary(counts: dict[str, int]) -> str:
    parts = []
    for name, target in targets().items():
        if target or counts.get(name):
            cap = cap_of(name)
            parts.append(f"{name} {counts.get(name, 0)}/{target:.0f}{'（上限）' if cap is not None else ''}")
    return " / ".join(parts)
