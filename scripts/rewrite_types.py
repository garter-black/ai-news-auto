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
# 品質ゲートに合格しなかった回数（2回落ちた記事は、順番の対象から外す。同じ記事に何度も費用を使わない）
TRIES_PATH = ROOT / "state" / "types_rewrite.json"
MAX_TRIES = 2
# 1本あたりの費用の見込み（Sonnet で書く・確認・作り直しを含む。2026-11-03 の実績 約0.2ドル）。今日使ってよい額の計算に使う
EST_COST = float(os.environ.get("REWRITE_EST_COST_USD", "0.25"))


def budget_allowance(want: int) -> tuple[int, str]:
    """月の予算（記事づくりと同じ配分。llm.api_ledger_summary）から、今日あと何本書き直してよいか。
    1日に使ってよい額 =（予算 − 使った額）÷ 残りの日数。今日すでに使った額を引いた残りで書ける本数（本番の記事を先に守る）"""
    s = llm.api_ledger_summary()
    if llm.api_block_reason("core", summary=s):
        return 0, f"Claude API を使えない（{llm.api_block_reason('core', summary=s)}）"
    daily = max(0.0, float(s["budget"]) - float(s["spent"])) / max(1, int(s.get("days_left") or 1))
    room = daily - float(s.get("spent_today") or 0)
    n = max(0, min(want, int(room // EST_COST)))
    return n, (f"今日使ってよい額 ${daily:.2f}・今日使った額 ${float(s.get('spent_today') or 0):.2f}・残り ${room:.2f} → {n} 本"
               f"（予算 ${float(s['budget']):.0f}・使った額 ${float(s['spent']):.2f}・残り {s.get('days_left')} 日）")


def run_cost() -> float:
    """この回（このジョブの記録ファイルの、この実行の分）で使った額"""
    try:
        data = json.loads(llm._ledger_path().read_text("utf-8"))  # noqa: SLF001
        return float(((data.get("runs") or {}).get(llm._jst_day(), {}).get(llm._run_key()) or {}).get("usd") or 0)  # noqa: SLF001
    except (OSError, ValueError):
        return 0.0


def load_tries() -> dict:
    try:
        return json.loads(TRIES_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_tries(data: dict) -> None:
    TRIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRIES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", "utf-8")
UPDATE_HEADING_RE = re.compile(r"^##\s+続報")
# 編集部の訂正の注記（「**編集部訂正（2026年10月31日）**：…」）。訂正した記事は人が確かめた記事なので、書き直しの対象にしない
CORRECTION_RE = re.compile(r"編集部訂正|【訂正】|訂正（\d{4}年")
CORRECTIONS_PATH = ROOT / "content" / "corrections.json"


def corrected_slugs() -> set[str]:
    try:
        return {str(x.get("slug")) for x in json.loads(CORRECTIONS_PATH.read_text("utf-8")) if isinstance(x, dict)}
    except (OSError, ValueError):
        return set()


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
    # 訂正した記事（content/corrections.json・本文の「編集部訂正」）は書き直さない（人が確かめた中身を LLM で作り直さない）
    if a.get("slug") in corrected_slugs() or CORRECTION_RE.search(a.get("body") or "") or a.get("refreshMode") == "editorial":
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
    tries = load_tries()
    reads = readership()
    rows = []
    for p in ARTICLES_DIR.glob("*.json"):
        try:
            a = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not eligible(a, retired) or int((tries.get(a.get("slug")) or {}).get("failed", 0)) >= MAX_TRIES:
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
    # 訂正の注記の段落は、一字も変えずに先頭へ（2026-11-03: Muse の記事で消えて、訂正の表示と公開前のテストが壊れた）
    notes = [p.strip() for p in re.split(r"\n\s*\n", old_body or "") if CORRECTION_RE.search(p) and not p.lstrip().startswith("#")]
    rival = [f"{h}\n{t.strip()}" for h, t in _sections(old_body) if h.strip() == f"## {rivals.HEADING}"]
    body = new_body.strip()
    if updates:
        body = "\n\n".join(updates) + "\n\n" + body
    if rival and f"## {rivals.HEADING}" not in body:
        parts = body.split("\n## まだ分からないこと", 1)
        body = parts[0].rstrip() + "\n\n" + rival[0] + ("\n\n## まだ分からないこと" + parts[1] if len(parts) > 1 else "")
    if notes:
        body = "\n\n".join(n for n in notes if n not in body) + ("\n\n" if any(n not in body for n in notes) else "") + body
    return body


def keep_verified(old: dict, new: dict) -> None:
    """確かめた欄を書き直しで変えない: 日本で使えるか（verdict.japan）は、日本での提供の表（japanSource つき）か、前の値を使う。
    LLM の新しい見立てで変えない（2026-11-03: Muse の記事で ok が unknown になった）"""
    ov, nv = old.get("verdict") or {}, new.get("verdict")
    if not isinstance(nv, dict) or not isinstance(ov, dict) or not ov.get("japan"):
        return
    if nv.get("japanSource") and nv.get("japan") not in (None, "unknown"):
        return  # 日本での提供の表（content/japan_availability.json）で決まった値
    nv["japan"] = ov["japan"]
    if ov.get("japanSource"):
        nv["japanSource"] = ov["japanSource"]


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
    if slug in corrected_slugs() or CORRECTION_RE.search(old.get("body") or "") or old.get("refreshMode") == "editorial":
        return None, {"slug": slug, "history": [], "why": "訂正した記事なので書き直さない"}
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
        if not str(model or "").startswith("anthropic-api:claude-sonnet"):
            # 書き直しは Sonnet だけで行う（無料モデルに落ちたら、今の版のまま次の回に回す）
            return None, {**report, "why": f"Sonnet が使えなかった（{model}）。次の回に回す"}
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
    keep_verified(old, new)
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
    want = args.max
    if args.apply and os.environ.get("ANTHROPIC_API_KEY"):
        allowed, why = budget_allowance(args.max)
        log(f"[budget] {why}")
        if allowed <= 0:
            log("[budget] 今日は予算に余裕が無いので、書き直さずに終わります（質を下げて無料モデルで書くことはしない）")
            return 0
        want = allowed
    slugs = ([s for s in args.slug.split(",") if s] or [a["slug"] for _, a in ranked()[: want]])[:want]
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
            if "Sonnet が使えなかった" in str(rep.get("why")):
                log("[budget] Sonnet が使えなくなったので、ここで止めます（残りは次の回）")
                results.append(rep)
                break
            if args.apply and "Sonnet が使えなかった" not in str(rep.get("why")):
                tries = load_tries()
                row = tries.setdefault(slug, {"failed": 0})
                row["failed"] = int(row.get("failed", 0)) + 1
                row["why"] = str(rep.get("why"))[:120]
                row["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                save_tries(tries)
        elif args.apply:
            (ARTICLES_DIR / f"{slug}.json").write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", "utf-8")
            done += 1
            log(f"   [OK] 書き直した（{rep.get('newsType')} / {rep.get('model')} / score {rep.get('score')}）")
        results.append(rep)
        if args.report:
            Path(args.report).write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", "utf-8")
    log(f"\n[done] 書き直し {done} 本 / 今の版のまま {len(results) - done} 本 / この回の費用 ${run_cost():.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
