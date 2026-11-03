#!/usr/bin/env python3
"""お知らせ・告知・統計の転載の記事を作らない門（2026-11-03 オーナー決定）。

オーナーの決定: 発表をそのまま書き写しただけの記事（お知らせ・告知・統計の転載）は、取り下げて、これからも作らない。
例: たばこの小売定価の認可、褒章の受章者、地ビールの出荷量、決算発表日の予告、議事要旨の公開、毎年の啓発月間など、
読者が「自分にどう関係するか・何をすればいいか」を判断する材料を足していない、決まった手続きの知らせ。
報告 out/サイト全体の質の点検_2026-11-03b.md の1位（AdSense の「有用性の低いコンテンツ」に当たる）。

判定は題名（と出典）だけで行う。迷うものは通す（分析・解説の記事、AI の話題、申し込める制度の案内は止めない）。
  - 公開前の門: scripts/indexnow.py の publish_block_reason（"notice"。作り直しても直らないので見送りで確定）
  - 取り下げ: scripts/retire_articles.py --notice（直近7日に SNS に出した記事は残す）
  - 表示: lib/verdict.ts が lib/notice-rules.json（このファイルから書き出す）で、お知らせの記事に「申し込める／使える」の札を出さない
  - 毎日の見張り: scripts/owner_rules_audit.py（[A26]）

  python3 scripts/notice_gate.py                 # 今ある記事のうち、お知らせ・統計の転載に当たるものを表示
  python3 scripts/notice_gate.py --export-rules  # lib/notice-rules.json を書き出す（tests/test_notice_gate.py がずれを見つける）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
RULES_PATH = ROOT / "lib" / "notice-rules.json"

# 区分ごとの題名の目印（題名だけで判断できる、はっきりした型だけ）
NOTICE_PATTERNS: dict[str, re.Pattern] = {
    # 認可・受章などの一覧の転載（たばこの小売定価の認可、褒章・叙勲の受章者）
    "listing": re.compile(r"認可情報|定価の認可|褒章|受章者|叙勲|紺綬|(?:表彰|受賞)者(?:を|の)(?:発表|公表|決定)|[0-9０-９]+(?:グループ|団体|事業者)を(?:決定|採択|選定)"),
    # 会議の議事要旨・議事録・会見の動画の公開
    "minutes": re.compile(r"議事(?:要旨|録|概要)|会見の(?:動画|映像)を公開|会見の(?:要旨|全文)を公開"),
    # 決算発表日の予告（中身の数字がまだ無い）
    "earnings-date": re.compile(
        r"決算(?:発表)?を\s*\d{1,2}月\d{1,2}日[^。　]{0,12}(?:に|で)?発表(?:へ|予定)|決算発表(?:の)?(?:日|日程|予定日?)(?:を|が)?(?:決定|お知らせ|公表)|"
        r"決算(?:を|の)?発表日"
    ),
    # 毎年の啓発・月間・週間・調査への協力のお願い
    "campaign": re.compile(
        r"啓発|(?:の日|月間|週間)(?:で|を|に)(?:啓発|周知|実施|展開)|ねんきん月間|(?:実態|受給者)調査」?を実施|調査への協力|"
        r"申込(?:み)?受付を終了|受付を終了しました|を随時公開|PDFで確認"
    ),
    # 統計の転載（出荷量・生産・販売の実績、定例の統計・調査の公表）
    "statistics": re.compile(
        r"出荷量|出荷数量|出荷実績|生産量|販売量|販売・生産・輸出実績|生産・販売実績|輸出実績を|人口移動報告|消費動向調査|"
        r"経済構造実態調査|[一二三四]次集計|速報値を公表|確報値を公表|統計Today|家計調査を公表|毎月勤労統計を公表|"
        r"(?:認定|登録|選定)[^。　]{0,20}\d+件を公表|コア指標を公表"
    ),
}
# 止めないもの: 分析・解説を足した記事、AI の話題（AI の利用調査など）、申し込める制度・支援金の案内
ANALYSIS_RE = re.compile(r"分析|解説|読み解|要因|背景|理由|なぜ|指摘|乖離|示唆|見通し|予測|影響")
AI_RE = re.compile(r"(?<![A-Za-z])AI(?![a-z])|人工知能|生成AI|LLM|GPT|Gemini|Claude|Copilot|ChatGPT", re.I)
APPLY_RE = re.compile(r"申請受付|申し込み受付|募集を開始|受付を開始|給付|補助金|助成金|支援金|減税|控除")
# 分析を出すシンクタンク・調査会社の解説記事は、統計の題名でも止めない（ニッセイ基礎研は「〜が分析」と書く）
ANALYSIS_CHANNELS = {"nli-research"}

LABELS = {
    "listing": "認可・受章などの一覧の転載",
    "minutes": "議事要旨・会見の動画の公開",
    "earnings-date": "決算発表日の予告",
    "campaign": "毎年の啓発・手続きのお知らせ",
    "statistics": "統計・実績の数字の転載",
}


def notice_reason(record: dict) -> str | None:
    """お知らせ・告知・統計の転載の区分（当たらなければ None）。題名と出典だけで判断する"""
    if record.get("disaster") or record.get("type") == "roundup":
        return None
    title = str(record.get("title") or "")
    if not title or AI_RE.search(title):
        return None
    for key, pattern in NOTICE_PATTERNS.items():
        if not pattern.search(title):
            continue
        if key == "statistics" and (ANALYSIS_RE.search(title) or record.get("channelSlug") in ANALYSIS_CHANNELS):
            return None
        if key == "campaign" and APPLY_RE.search(title):
            return None
        return key
    return None


def is_notice(record: dict) -> bool:
    return notice_reason(record) is not None


# --------------------------------------------------------------------------- #
# サイト（lib/verdict.ts）が読む材料
# --------------------------------------------------------------------------- #


def _js(pattern: re.Pattern) -> dict:
    return {"source": pattern.pattern, "flags": "i" if pattern.flags & re.I else ""}


def rules() -> dict:
    return {
        "_note": "scripts/notice_gate.py --export-rules で生成。手で直さない（正本は scripts/notice_gate.py）",
        "patterns": {k: _js(v) for k, v in NOTICE_PATTERNS.items()},
        "analysis": _js(ANALYSIS_RE),
        "ai": _js(AI_RE),
        "apply": _js(APPLY_RE),
        "analysisChannels": sorted(ANALYSIS_CHANNELS),
    }


def rules_json() -> str:
    return json.dumps(rules(), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-rules", action="store_true", help="lib/notice-rules.json を書き出す")
    args = parser.parse_args()
    if args.export_rules:
        RULES_PATH.write_text(rules_json(), encoding="utf-8")
        print(f"[notice] → {RULES_PATH.relative_to(ROOT)}")
        return 0
    hits = []
    for path in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if reason := notice_reason(record):
            hits.append((reason, record.get("slug", path.stem), record.get("title", "")))
    for reason, slug, title in sorted(hits):
        print(f"{reason:14} {slug:55} {title}")
    print(f"[notice] {len(hits)} 本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
