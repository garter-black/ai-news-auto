#!/usr/bin/env python3
"""ニュースの種類ごとの型（scripts/news_types.py）で、今ある記事を試しに書き直す（公開しない・記事のファイルを書き換えない）。

本番の流れと同じ部品を使う: 元の資料を取り直す → 確かな事実の束（リンク先も読む）→ 種類の判定と型 → 生成 →
必ず入れる事実の確認（足りなければ作り直し）→ 品質ゲート（事実確認・文体の門。落ちたら作り直し）→ 書き出す前の後処理。
結果は --out の JSON に保存する（前の記事と並べて比べる用）。

  python3 scripts/trial_types.py --slug apple-reports-q4-2026-results --out /tmp/trial.json
  python3 scripts/trial_types.py --slug a --slug b --purpose core --out out.json   # Claude API の段を指定（CI で ANTHROPIC_API_KEY があるとき）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive  # noqa: E402
import article_style  # noqa: E402
import feeds  # noqa: E402
import llm  # noqa: E402
import main as m  # noqa: E402
import news_types  # noqa: E402
import quality  # noqa: E402
import section_gate  # noqa: E402
import web_articles as w  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def log(msg: str) -> None:
    print(msg, flush=True)


def item_from_article(a: dict) -> dict:
    published = datetime.fromisoformat((a.get("sourcePublishedAt") or a.get("publishedAt")).replace("Z", "+00:00"))
    return {
        "id": f"trial-{a['slug']}",
        "url": a["sourceUrl"],
        "title": a.get("videoTitle") or a["title"],
        "source_name": a.get("sourceName") or a.get("channel") or "",
        "source_slug": a.get("channelSlug") or "",
        "published_at": published,
        "image": a.get("thumbnail"),
    }


def lane_of(a: dict) -> str:
    return "ai" if a.get("genreSlug") in ("ai", "", None) else a["genreSlug"]


def run_one(slug: str, purpose: str, attempts: int, check_facts: bool) -> dict:
    old = json.loads((ROOT / "content" / "articles" / f"{slug}.json").read_text("utf-8"))
    item = item_from_article(old)
    lane = lane_of(old)
    log(f"\n=== {slug}（{lane}）: {item['title'][:70]}")
    page = feeds.fetch_article_text(item["url"])
    text = page.get("text") or ""
    log(f"   資料 {len(text)}字")
    arch = archive.Archive() if hasattr(archive, "Archive") else None
    related = arch.related(item["title"], text, item["url"], lane) if arch else []
    related = [r for r in related if r.get("slug") != slug]
    voice_key, voice_text = m.pick_voice()
    genre, system, prompt, schema, kind, context, bundle = w.compose_with_facts(item, text, lane, related, voice_text, page)
    source_text = f"{w.source_header(item)}\n\n{text}"
    history = []
    record = None
    verdict: dict = {}
    model = ""
    for attempt in range(1, attempts + 1):
        started = time.time()
        data, model = llm.generate_json(system, prompt, schema, temperature=0.6, max_output_tokens=32768, tier="write", purpose=purpose)
        record = w.attach_related(w.build_web_record(item, data, page, model, voice_key, bundle), data, related)
        w.pre_gate(record, source_text, context)
        missing = news_types.missing_facts(record.get("newsType") or "", record.get("body") or "", source_text)
        blocked = w.publish_blocked(record)
        if blocked:
            verdict = {"ok": False, "problems": [f"公開前の門: {w.indexnow.PUBLISH_FEEDBACK[blocked]}"]}
        elif missing and attempt < attempts:
            verdict = {"ok": False, "problems": news_types.missing_feedback(record["newsType"], missing)}
        else:
            verdict = quality.evaluate(record, source_text, kind, context, check_facts=check_facts)
        if not verdict.get("ok") and not verdict.get("soft") and verdict.get("fact") is not None and quality.surgical_ok(verdict):
            fixed, info = quality.surgical_fix(record, source_text, kind, verdict.get("problems") or [], context, check_facts=check_facts)
            log(f"   指摘の箇所だけ直す: {'合格' if fixed is not None else info}")
            if fixed is not None:
                record, verdict = fixed, info
        history.append({"attempt": attempt, "model": model, "ok": verdict.get("ok"), "soft": verdict.get("soft"),
                        "score": verdict.get("score"), "problems": verdict.get("problems"), "sec": round(time.time() - started)})
        log(f"   試行{attempt}（{model}）ok={verdict.get('ok')} soft={verdict.get('soft')} score={verdict.get('score')} "
            f"{' | '.join((verdict.get('problems') or [])[:4])[:400]}")
        if verdict.get("ok") or verdict.get("soft"):
            break
        prompt = prompt + quality.feedback_block(verdict.get("problems") or [])
    # 書き出す前の後処理（main.write_article と同じもの。ファイルには書かない）
    record.pop("_facts", None)
    m.headline_normalize.apply(record)
    section_gate.apply(record)
    article_style.autofix(record)
    w.strip_fact_ids(record)
    return {
        "slug": slug,
        "newsType": record.get("newsType"),
        "accepted": bool(verdict.get("ok") or verdict.get("soft")),
        "history": history,
        "style_problems_final": article_style.problems(record),
        "missing_final": [x.label for x in news_types.missing_facts(record.get("newsType") or "", record.get("body") or "", source_text, hard_only=False)],
        "old": {k: old.get(k) for k in ("title", "lede", "highlights", "body", "easy", "verdict", "model")},
        "new": {k: record.get(k) for k in ("title", "seoTitle", "lede", "highlights", "body", "easy", "verdict", "faq", "todayActions",
                                           "workMoney", "model", "newsType")},
        "source_chars": len(text),
        "bundle_groups": [g.label for g in bundle.groups] if bundle else [],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", action="append", required=True)
    ap.add_argument("--purpose", default=None, help="Claude API の段（core / sns など）。無ければ無料モデル")
    ap.add_argument("--attempts", type=int, default=2)
    ap.add_argument("--no-facts", action="store_true", help="事実確認の LLM を呼ばない（文体と型だけ見る）")
    ap.add_argument("--force-model", default="", help="Claude API のどの段でもこのモデルで書く（例 claude-sonnet-5-5。比べるとき用）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.force_model:
        import os  # noqa: PLC0415

        for level in ("LOW", "MID", "TOP"):
            os.environ[f"CLAUDE_API_MODEL_{level}"] = args.force_model
    out = []
    for slug in args.slug:
        try:
            out.append(run_one(slug, args.purpose, args.attempts, not args.no_facts))
        except Exception as error:  # noqa: BLE001
            log(f"   [NG] {slug}: {str(error)[:300]}")
            out.append({"slug": slug, "error": str(error)[:500]})
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", "utf-8")
    ok = sum(1 for r in out if r.get("accepted"))
    log(f"\n合格 {ok}/{len(out)} → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
