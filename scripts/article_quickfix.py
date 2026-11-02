#!/usr/bin/env python3
"""公開済みの記事を、LLM を使わずに直す（2026-11-03 の比較調査の「すぐできること」）。

  python3 scripts/article_quickfix.py            # 直す記事の一覧だけ（書き換えない）
  python3 scripts/article_quickfix.py --apply    # 書き換える

- 空の seoTitle を見出しから作る（web_articles.fallback_seo_title）
- まとめ記事の作業用の言葉（「記事1」「時系列で見る発表の流れ」「スラッグ」）を読者向けの言葉に直す（roundup.clean_record_labels）
- 報道が元の記事の「筆者」を「〇〇の記者」に直す（web_articles.fix_press_first_person）
- 判定の「日本から使える？」が unknown で、主役の製品が日本での提供の表（content/japan_availability.json）にあり一般提供中なら、
  表の値で直し、本文の「日本で使える？」の「まだ分かりません」も表の答えにそろえる（japan_availability）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import japan_availability  # noqa: E402
import roundup  # noqa: E402
import web_articles  # noqa: E402

ARTICLES = Path(__file__).resolve().parent.parent / "content" / "articles"


def fix(record: dict) -> list[str]:
    """record をその場で直し、直したことの一覧を返す"""
    done = []
    if not str(record.get("seoTitle") or "").strip() and record.get("title"):
        record["seoTitle"] = web_articles.fallback_seo_title(record["title"])
        done.append(f"seoTitle「{record['seoTitle']}」")
    if record.get("type") == "roundup":
        before = json.dumps(record, ensure_ascii=False)
        roundup.clean_record_labels(record)
        if json.dumps(record, ensure_ascii=False) != before:
            done.append("まとめの作業用の言葉")
        return done
    outlet = record.get("sourceName") or ""
    if str(record.get("disclaimer") or "").startswith("本記事は報道（") and outlet:
        before = record.get("body")
        web_articles.fix_press_first_person(record, outlet)
        if record.get("body") != before:
            done.append(f"筆者→{outlet}の記者")
    verdict = record.get("verdict")
    if verdict and record.get("sourceType") == "web" and record.get("genreSlug") not in ("sports", "public"):
        fixed = japan_availability.apply_to_verdict(verdict, record)
        if fixed is not verdict and fixed.get("japan") != verdict.get("japan"):
            record["verdict"] = fixed
            record["body"] = japan_availability.fix_japan_section(record.get("body") or "", record)
            done.append(f"日本: unknown→{fixed['japan']}（{fixed['japanSource']['name']}）")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    changed = 0
    for path in sorted(ARTICLES.glob("*.json")):
        try:
            record = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError:
            continue
        done = fix(record)
        if not done:
            continue
        changed += 1
        print(f"{path.stem}: {' / '.join(done)}")
        if args.apply:
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[{'apply' if args.apply else 'dry-run'}] {changed} 本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
