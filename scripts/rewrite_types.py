#!/usr/bin/env python3
"""今ある記事を、ニュースの種類ごとの型（scripts/news_types.py）で書き直す（2026-11-03 オーナー承認「計画の1段目から」）。

本番の新しい記事と同じ部品を使う: 元の資料を取り直す → 確かな事実の束（リンク先・長い資料の後半）→ 種類の判定と型 → 生成 →
必ず入れる事実の確認（足りなければ作り直し）→ 品質ゲート（事実確認・文体の門。細かい指摘は文だけ直す）→ 書き出す前の後処理。
合格しなかった記事は書き換えない（今の版のまま）。

変えないもの: URL（slug）・見出し（題名。別の作業で直したもの）・公開日時・作った日時・続報（updates と本文の「続報」の章）・
画像・SNS の紹介文・他社との比較表（rivals の章）。資料に無い事実は書かない（品質ゲートの事実確認・数字の照合を通す）。

  python3 scripts/rewrite_types.py --rank 60                 # 書き直す順番（よく読まれている順）を表示するだけ
  python3 scripts/rewrite_types.py --max 10 --apply          # 上から10本を書き直して保存（ANTHROPIC_API_KEY があるとき Sonnet）
  python3 scripts/rewrite_types.py --slug a,b --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive  # noqa: E402
import article_style  # noqa: E402
import feeds  # noqa: E402
import indexnow  # noqa: E402
import llm  # noqa: E402
import main as m  # noqa: E402
import news_types  # noqa: E402
import quality  # noqa: E402
import rivals  # noqa: E402
import section_gate  # noqa: E402
import web_articles as w  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
RETIRED_PATH = ROOT / "content" / "retired.json"
# 書き直した記事から取り替える欄（それ以外は今の記事のまま）
REPLACE = ("lede", "description", "highlights", "body", "easy", "faq", "todayActions", "workMoney", "verdict", "newsType",
           "affiliates", "qualityScore", "model", "writingVoice", "factSources", "charts", "pricingProduct", "pricingCompare", "relatedSlugs")
WRITE_MODEL = os.environ.get("REWRITE_MODEL", "claude-sonnet-5-5")
UPDATE_HEADING_RE = re.compile(r"^##\s+続報")


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# 順番（よく読まれている順）
# --------------------------------------------------------------------------- #


def _retired() -> set[str]:
    try:
        return set((json.loads(RETIRED_PATH.read_text("utf-8")).get("articles") or {}).keys())
    except (OSError, ValueError):
        return set()


def eligible(a: dict, retired: set[str]) -> bool:
    """書き直しの対象: 公式発表が元の通常の記事で、検索に出していて、まだ種類の型で書かれていないもの"""
    if a.get("sourceType") != "web" or a.get("type") == "roundup" or a.get("slug") in retired or a.get("duplicateOf"):
        return False
    if a.get("newsType") or a.get("typesRewrittenAt") or a.get("disaster") or a.get("trendKeyword") or a.get("angleOf"):
        return False
    if not a.get("sourceUrl") or a.get("secondary"):
        return False
    return indexnow.noindex_reason(a) is None


def readership() -> dict[str, float]:
    """記事ごとの読まれ方の点（自前計測の入口の数・Threads/X の表示）。計測が始まったばかりなので、無い記事は0"""
    score: dict[str, float] = {}
    try:
        posted = json.loads((ROOT / "state" / "threads_posted.json").read_text("utf-8")).get("posted") or {}
    except (OSError, ValueError):
        posted = {}
    for slug, row in posted.items():
        views = float(((row.get("metrics") or {}).get("views") or 0)) + float(((row.get("xMetrics") or {}).get("views") or 0))
        score[slug] = score.get(slug, 0.0) + views + 20  # SNS に出した記事は入口が多い
    # 自前計測（記事に外から来た人の入口の数。scripts/inflow_report.py）の直近7日。鍵が無ければ使わない
    try:
        from datetime import timedelta  # noqa: PLC0415

        import inflow_report  # noqa: PLC0415
        from aff_stats import JST, load_token  # noqa: PLC0415

        token = load_token()
        today = datetime.now(JST).date()
        for back in range(7 if token else 0):
            data = inflow_report.fetch(token, (today - timedelta(days=back)).isoformat())
            for row in data.get("rows") or []:
                score[row.get("page", "")] = score.get(row.get("page", ""), 0.0) + 10 * float(row.get("views") or 0)
    except Exception as error:  # noqa: BLE001 - 計測が読めなくても順番は作れる
        log(f"[rank] 自前計測を読めませんでした（{type(error).__name__}）")
    return score


def ranked() -> list[tuple[float, dict]]:
    retired = _retired()
    reads = readership()
    rows = []
    for p in ARTICLES_DIR.glob("*.json"):
        try:
            a = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not eligible(a, retired):
            continue
        # 読まれ方が同じなら新しい記事を先に（検索から来る見込みが高い）
        age_days = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(a["generatedAt"].replace("Z", "+00:00"))).days)
        rows.append((reads.get(a["slug"], 0.0) + max(0.0, 14 - age_days) * 0.5, a))
    rows.sort(key=lambda x: (-x[0], -datetime.fromisoformat(x[1]["generatedAt"].replace("Z", "+00:00")).timestamp()))
    return rows


# --------------------------------------------------------------------------- #
# 書き直し
# --------------------------------------------------------------------------- #


def item_from_article(a: dict) -> dict:
    published = datetime.fromisoformat((a.get("sourcePublishedAt") or a.get("publishedAt")).replace("Z", "+00:00"))
    return {"id": f"rewrite-{a['slug']}", "url": a["sourceUrl"], "title": a.get("videoTitle") or a["title"],
            "source_name": a.get("sourceName") or a.get("channel") or "", "source_slug": a.get("channelSlug") or "",
            "published_at": published, "image": a.get("thumbnail")}


def _sections(body: str) -> list[tuple[str, str]]:
    out, head, buf = [], "", []
    for line in (body or "").split("\n"):
        if line.startswith("## "):
            out.append((head, "\n".join(buf)))
            head, buf = line, []
        else:
            buf.append(line)
    out.append((head, "\n".join(buf)))
    return out


def keep_sections(old_body: str, new_body: str) -> str:
    """今の本文の「続報」の章（先頭側）と、他社との比較表の章（rivals。公式ページの数字で作ったもの）を新しい本文に戻す"""
    updates = [f"{h}\n{t.strip()}" for h, t in _sections(old_body) if UPDATE_HEADING_RE.match(h)]
    rival = [f"{h}\n{t.strip()}" for h, t in _sections(old_body) if h.strip() == f"## {rivals.HEADING}"]
    body = new_body.strip()
    if updates:
        body = "\n\n".join(updates) + "\n\n" + body
    if rival and f"## {rivals.HEADING}" not in body:
        parts = body.split("\n## まだ分からないこと", 1)
        body = parts[0].rstrip() + "\n\n" + rival[0] + ("\n\n## まだ分からないこと" + parts[1] if len(parts) > 1 else "")
    return body


def _write_with(model: str):
    """本文を書く呼び出しだけ、段（core）のどのモデルでも model にする（事実確認・手直しはいつもの配分のまま）"""
    keys = ("CLAUDE_API_MODEL_LOW", "CLAUDE_API_MODEL_MID", "CLAUDE_API_MODEL_TOP")

    class _Ctx:
        def __enter__(self):
            self.saved = {k: os.environ.get(k) for k in keys}
            for k in keys:
                os.environ[k] = model

        def __exit__(self, *exc):
            for k, v in self.saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return _Ctx()


def rewrite(slug: str, attempts: int = 2) -> tuple[dict | None, dict]:
    """(新しい記事 or None, 報告)"""
    path = ARTICLES_DIR / f"{slug}.json"
    old = json.loads(path.read_text("utf-8"))
    item = item_from_article(old)
    lane = "ai" if old.get("genreSlug") in ("ai", "", None) else old["genreSlug"]
    report: dict = {"slug": slug, "history": []}
    page = feeds.fetch_article_text(item["url"])
    text = max([page.get("text") or ""], key=len)
    if len(text) < w.MIN_TEXT_CHARS:
        return None, {**report, "why": f"元の資料が読めない・短い（{len(text)}字）"}
    related = [r for r in archive.Archive().related(item["title"], text, item["url"], lane) if r.get("slug") != slug]
    voice_key, voice_text = m.pick_voice()
    genre, system, prompt, schema, kind, context, bundle = w.compose_with_facts(item, text, lane, related, voice_text, page)
    source_text = f"{w.source_header(item)}\n\n{text}"
    report["newsType"] = item.get("newsType")
    record, verdict = None, {}
    for attempt in range(1, attempts + 1):
        with _write_with(WRITE_MODEL):
            data, model = llm.generate_json(system, prompt, schema, temperature=0.6, max_output_tokens=32768, tier="write", purpose="core")
        if not data.get("is_relevant", True):
            return None, {**report, "why": f"記事にする価値なしと判定（{str(data.get('skip_reason'))[:80]}）"}
        record = w.attach_related(w.build_web_record(item, data, page, model, voice_key, bundle), data, related)
        w.pre_gate(record, source_text, context)
        missing = news_types.missing_facts(record.get("newsType") or "", record.get("body") or "", source_text)
        blocked = w.publish_blocked(record)
        if blocked:
            verdict = {"ok": False, "problems": [f"公開前の門: {indexnow.PUBLISH_FEEDBACK[blocked]}"]}
        elif missing and attempt < attempts:
            verdict = {"ok": False, "problems": news_types.missing_feedback(record["newsType"], missing)}
        else:
            verdict = quality.evaluate(record, source_text, kind, context)
            if not verdict.get("ok") and not verdict.get("soft") and verdict.get("fact") is not None and quality.surgical_ok(verdict):
                fixed, info = quality.surgical_fix(record, source_text, kind, verdict.get("problems") or [], context)
                if fixed is not None:
                    record, verdict = fixed, info
            if verdict.get("soft"):
                polished, info = quality.polish(record, source_text, kind, context)
                if polished is not None:
                    record, verdict = polished, info
        report["history"].append({"attempt": attempt, "model": model, "ok": bool(verdict.get("ok")), "soft": bool(verdict.get("soft")),
                                  "score": verdict.get("score"), "problems": [str(p)[:160] for p in (verdict.get("problems") or [])[:3]]})
        log(f"   試行{attempt}（{model}）ok={verdict.get('ok')} soft={verdict.get('soft')} score={verdict.get('score')} "
            f"{' | '.join(str(p) for p in (verdict.get('problems') or [])[:2])[:240]}")
        if verdict.get("ok") or verdict.get("soft"):
            break
        prompt = prompt + quality.feedback_block(verdict.get("problems") or [])
    if not (verdict.get("ok") or verdict.get("soft")) or record is None:
        return None, {**report, "why": "品質ゲートに合格しなかった（今の版のまま）"}
    record = w.finalize_facts(w.attach_charts(record), source_text)
    # 今の記事に、書き直した欄だけを入れる
    new = json.loads(json.dumps(old, ensure_ascii=False))
    for key in REPLACE:
        if record.get(key) not in (None, "", []):
            new[key] = record[key]
        elif key in ("verdict", "faq", "todayActions", "charts", "pricingProduct", "pricingCompare"):
            new.pop(key, None)  # 種類に合わない判定カード・材料の無い欄は消す
    new["body"] = keep_sections(old.get("body") or "", record["body"])
    new.pop("sectionTitles", None)  # 見出しが変わったので、表示用の言い換えは使わない
    new["sectionsPolishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new["typesRewrittenAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    section_gate.apply(new)
    article_style.autofix(new)
    left = article_style.problems(new)
    if left:
        return None, {**report, "why": f"書き出す前の文体の門: {left[0][:100]}"}
    if indexnow.publish_block_reason(new):
        return None, {**report, "why": f"公開前の門: {indexnow.publish_block_reason(new)}"}
    report.update({"ok": True, "model": record.get("model"), "score": record.get("qualityScore"),
                   "before": {k: old.get(k) for k in ("title", "lede", "body")}, "after": {k: new.get(k) for k in ("title", "lede", "body")}})
    return new, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rank", type=int, default=0, help="順番を表示するだけ")
    ap.add_argument("--max", type=int, default=10)
    ap.add_argument("--slug", default="", help="カンマ区切り")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--budget-sec", type=int, default=3000)
    ap.add_argument("--report", default="", help="結果の JSON（前と後つき）")
    args = ap.parse_args()
    if args.rank:
        for score, a in ranked()[: args.rank]:
            print(f"{score:7.1f}  {a['slug'][:60]:60} {a['title'][:40]}")
        return 0
    slugs = [s for s in args.slug.split(",") if s] or [a["slug"] for _, a in ranked()[: args.max]]
    started = time.time()
    results = []
    done = 0
    for i, slug in enumerate(slugs, 1):
        if time.time() - started > args.budget_sec:
            log("[info] 時間の上限なので、残りは次の回に回します")
            break
        log(f"\n[{i}/{len(slugs)}] {slug}")
        try:
            new, rep = rewrite(slug)
        except Exception as error:  # noqa: BLE001 - 1本の失敗で止めない
            new, rep = None, {"slug": slug, "why": f"失敗: {str(error)[:200]}"}
        if new is None:
            log(f"   [keep] 今の版のまま: {rep.get('why')}")
        elif args.apply:
            (ARTICLES_DIR / f"{slug}.json").write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", "utf-8")
            done += 1
            log(f"   [OK] 書き直した（{rep.get('newsType')} / {rep.get('model')} / score {rep.get('score')}）")
        results.append(rep)
        if args.report:
            Path(args.report).write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", "utf-8")
    log(f"\n[done] 書き直し {done} 本 / 今の版のまま {len(results) - done} 本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
