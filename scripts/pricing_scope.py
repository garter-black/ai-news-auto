#!/usr/bin/env python3
"""料金表（毎日更新の料金の段の表・料金の段の図）を出してよい記事か（2026-11-03 オーナー指摘）。

オーナーの指摘: Anthropic の新薬開発の記事に Claude の料金表が出ていた。料金表は、料金が話題か、直接関係する記事だけに出す。
  - 出してよい: 料金・プランの変更の記事（newsType pricing）、新製品・新サービスの記事で題名かリードに料金の言葉があるもの
  - 出さない: 研究・調査・決算・脆弱性・提携・上場書類・人事・イベント・解説などの記事
判定はこの1か所（生成: scripts/web_articles.py・scripts/article_charts.py / 表示: lib/price-slot.ts の pricingRelevant と同じ規則 /
毎日の点検: scripts/owner_rules_audit.py の [A32]）。

  python3 scripts/pricing_scope.py            # 料金表が出てはいけない記事を表示
  python3 scripts/pricing_scope.py --apply    # その記事から料金表（本文の表・pricingProduct・料金の段の図）を外す
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
PRICE_WORD_RE = re.compile(r"料金|価格|値上げ|値下げ|月額|年額|プラン|サブスク|無料|\d[\d,.]*\s?(?:円|ドル)|\$\d|pricing|price|plan\b", re.I)
TABLE_HEADING_RE = re.compile(r"^#{2,3}\s.+のプランと料金（公式の料金と円の目安）\s*$")
ALLOWED_TYPES = ("pricing", "product")


def news_type(record: dict) -> str:
    if record.get("newsType"):
        return record["newsType"]
    import news_types  # noqa: PLC0415

    title = record.get("videoTitle") or record.get("title") or ""
    return news_types.classify(title, f"{record.get('lede') or ''}\n{(record.get('body') or '')[:1500]}",
                               url=record.get("sourceUrl") or "", genre=record.get("genreSlug") or "")


def relevant(record: dict) -> bool:
    """この記事に料金表を出してよいか"""
    kind = news_type(record)
    if kind == "pricing":
        return True
    if kind != "product":
        return False
    # 新製品・新サービスは、題名かリードで料金に触れているときだけ（料金が直接関係する）
    return bool(PRICE_WORD_RE.search(record.get("title") or "") or PRICE_WORD_RE.search(record.get("lede") or ""))


def has_table(record: dict) -> bool:
    return bool(record.get("pricingProduct") or any(c.get("kind") == "ladder" for c in record.get("charts") or [])
                or any(TABLE_HEADING_RE.match(ln) for ln in (record.get("body") or "").split("\n")))


def strip(record: dict) -> bool:
    """料金表を外す（本文の差し込みの表・pricingProduct・pricingCompare・料金の段の図）。外したら True"""
    changed = False
    lines = (record.get("body") or "").split("\n")
    out, i = [], 0
    while i < len(lines):
        if TABLE_HEADING_RE.match(lines[i]):
            changed = True
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("|") or re.match(r"^(出典[:：]|円は)", lines[i].strip())):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    if changed:
        record["body"] = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"
    for key in ("pricingProduct", "pricingCompare"):
        if record.pop(key, None) is not None:
            changed = True
    charts = record.get("charts") or []
    kept = [c for c in charts if c.get("kind") != "ladder"]
    if len(kept) != len(charts):
        changed = True
        if kept:
            record["charts"] = kept
        else:
            record.pop("charts", None)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    n = 0
    for p in sorted(ARTICLES_DIR.glob("*.json")):
        a = json.loads(p.read_text("utf-8"))
        if a.get("type") == "roundup" or not has_table(a) or relevant(a):
            continue
        n += 1
        print(f"{news_type(a):10} {a['slug'][:60]:60} {a.get('title', '')[:40]}")
        if args.apply and strip(a):
            p.write_text(json.dumps(a, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"料金表が出てはいけない記事: {n} 本{'（外しました）' if args.apply else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
