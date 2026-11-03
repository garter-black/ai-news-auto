#!/usr/bin/env python3
"""記事の章（## 見出しの区切り）の「中身なし」「水増し」を取り除く門（2026-11-03 オーナー指摘）。

オーナーの指摘:
  - 「ライバルと比べてどう？」が「元記事には、他社製品との比較は書かれていません。ここは編集部注です。…判断できません。」だけ
    → 比べる材料が本当にあるときだけ出す。無ければ章ごと出さない
  - 「自分に関係ある？」が「〜に関係があります」「〜にとっても重要です」「〜といえます」のくり返し（情報が無い）
  - 章の見出しが毎回同じ定型の質問（「自分に関係ある？」「もう使える？取引できる？」）で機械的

この門は2か所で同じ規則を使う（規則を変えるときは両方を直す。tests/fixtures/section_gate_cases.json で同じ結果になることを確かめている）:
  - 表示: lib/article-sections.ts（今ある記事も、表示の時点で中身なしの章を出さない）
  - 生成: このファイル（新しい記事を書き出す前に、同じ規則で本文を整える。web_articles.py / main.py の write_article）

ここで消すのは「事実を含まない文」と「中身のない章」だけ。数字・固有名詞・日付・価格を含む文は消さない
（言い回しの書き直しは LLM の段 scripts/section_polish.py が受け持ち、事実が残ったかを確かめてから差し替える）。

  python3 scripts/section_gate.py --slug toyota-land-cruiser-fj-options   # 表示で消える文・章を見る（書き込まない）
  python3 scripts/section_gate.py --stats                                 # 全記事で、消える章・文の数
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"

# 中身が少なくても消さない章（記事の骨組み。消すと記事として読めなくなる）
PROTECTED_RE = re.compile(
    r"^(概要|ひとことで言うと|ひとすることで言うと|まとめ|これまでの流れ|続報|何が発表された|いつ、何が発表された|時系列で見る|この動画で語られていること)"
)
# scripts/rivals.py が公式ページの数字で作る比較表（品質ゲート quality.rival_problems で確かめ済み）。触らない
DATA_RIVAL_HEADING = "同じクラスの製品と比べると？"
# 比較の章（材料のある表か数字があるときだけ出す）
RIVAL_RE = re.compile(r"^(ライバルと比べて|競合サービスとの比較|競合との比較|他の選択肢との比較|ほかの選択肢と比べて)|と比べて(どう|みると)")
# 数字の章（数字が無ければ出さない）
NUMBERS_RE = re.compile(r"^(数字で見る|性能・ベンチマーク|この数字はどう読む|動画で示された数)")
# 料金の章（料金・価格の言葉か数字が答えの文に無ければ出さない）
PRICE_HEADING_RE = re.compile(r"^(料金|価格|いくら)")
PRICE_WORD_RE = re.compile(r"[0-9０-９]|無料|有料|月額|年額|円|ドル|ユーロ|価格|料金|課金|プラン|据え置き|値上げ|値下げ")
# 2024年ごろの古いモデル名（比較の相手がこれだと、今の製品との比較として成り立たない）
OUTDATED_MODEL_RE = re.compile(
    r"GPT-4o|GPT-4(?![.\d])|GPT‑4o|Claude 3(?:\.5)?(?![.\d])|Claude3|Gemini 1\.5|Gemini 1\.0|Llama 3(?![.\d])|\bo1(?:-preview|-mini)?\b"
)

# いつでも消す「中の人の注記・指示文の漏れ」（事実を含まない）
META_RE = re.compile(
    r"ここは編集部注です|以下は編集部の(?:知識|見立て)による補足です|"
    r"(?:元記事|元の記事|発表)(?:には|では)、?[^。]{0,30}(?:比較|ライバル|競合)[^。]{0,30}(?:書かれて|ありません|触れられて|示されて)|"
    r"優劣は[^。]{0,30}判断できません|"
    r"(?:前の版|前のモデル|従来)と比べられる数(?:値|字)は[^。]{0,30}(?:ありません|ない)|"
    r"比較表は作りません|"
    r"読者が次に取るべき(?:行動|アクション)(?:があります|は次のとおりです|です)|"
    r"今回の発表の位置づけ(?:は|として)"
)

# 「答えになっていない」文（章の中身の判定に使う。文そのものは消さない。言い換えは section_polish.py）
NONANSWER_RE = re.compile(
    r"書かれて(?:い)?ません|記載(?:は|が)?(?:ありません|ない|なし)|言及(?:は|が)?(?:ありません|なし|されていません)|"
    r"触れられて(?:い)?ません|示されて(?:い)?ません|明らかにされて(?:い)?ません|判断できません|判断はできません|"
    r"分かりません|わかりません|不明です|確認できていません|確認できません|公表されていません|"
    r"案内(?:は|が)?(?:ありません|されていません)|提示(?:は|が)?(?:ありません|なし|されていません)|発表されていません"
)

# 中身のない「つなぎ」の文。事実（数字・英字の名前・「」）を含まないときだけ消す
FILLER_RE = re.compile(
    r"にとって(?:も)?(?:重要|大切|見逃せない|朗報|注目|うれしい)|と(?:い|言)え(?:ます|るでしょう)|"
    r"判断材料(?:に|と)?(?:なります|なる|です)|チェックしておきたい|"
    r"注目(?:の内容です|です|されそうです|が集まりそうです|が集まります|したいところです)|見逃せない|"
    r"影響(?:は|が)(?:薄い|小さい|限定的)(?:です|でしょう|といえます)|直接(?:的な|の)影響は(?:大きく|小さ)|"
    r"期待(?:されます|が高まります|できそうです)|参考になります|強いて言えば|関係する情報です|"
    r"に(?:も)?関係があります|役立つでしょう|気になるところです|目が離せません|"
    r"(?:機会|選択肢)が(?:生まれる|広がる)可能性があります|この立場への直接の影響"
)

# 「事実」の印: 数字・英字の名前・かぎかっこ
FACT_RE = re.compile(r"[0-9０-９]|[A-Za-zＡ-Ｚａ-ｚ]{2,}|[「『]")
# 表・箇条書きの「値が無い」印
EMPTY_VALUE_RE = re.compile(
    r"^(?:元記事(?:では|に)?)?(?:言及なし|記載なし|記載はなし|不明|案内なし|言及はなし|非公表|未公表|公式に記載なし|料金ページに記載なし|—|－|-|–)(?:（[^）]*）)?。?$"
)
TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
DATE_NUM_RE = re.compile(r"\d+(?=\s*(?:年|月|日|時|分))")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def split_sentences(line: str) -> list[str]:
    """1行の地の文を文に分ける（「」の中の句点では分けない）"""
    out: list[str] = []
    buf = ""
    depth = 0
    for ch in line:
        buf += ch
        if ch in "「『（(":
            depth += 1
        elif ch in "」』）)" and depth > 0:
            depth -= 1
        elif ch in "。！？" and depth == 0:
            out.append(buf)
            buf = ""
    if buf.strip():
        out.append(buf)
    return out


def has_fact(text: str) -> bool:
    return bool(FACT_RE.search(text or ""))


def is_meta(sentence: str) -> bool:
    return bool(META_RE.search(sentence))


def is_nonanswer(sentence: str) -> bool:
    return bool(NONANSWER_RE.search(sentence))


def is_filler(sentence: str) -> bool:
    return bool(FILLER_RE.search(sentence)) and not has_fact(sentence)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _clean_table(lines: list[str]) -> list[str]:
    """値が全部「言及なし」などの行を消す。見出し行と区切り行だけになったら表ごと消す"""
    if len(lines) < 2 or not TABLE_SEP_RE.match(lines[1].strip()):
        return lines
    header = _cells(lines[0])
    source_col = len(header) - 1 if header and re.search(r"出典|ソース", header[-1]) else -1
    kept = lines[:2]
    for line in lines[2:]:
        cells = _cells(line)
        values = [c for i, c in enumerate(cells) if i > 0 and i != source_col]
        if values and all(EMPTY_VALUE_RE.match(re.sub(r"[*_`]", "", v)) for v in values):
            continue
        kept.append(line)
    return kept if len(kept) > 2 else []


def _clean_list_item(line: str) -> str | None:
    """「- 価格: 言及なし」のような、値が無いだけの箇条書きを消す"""
    body = LIST_RE.sub("", line, count=1).strip()
    m = re.match(r"^[^：:]{1,40}[：:]\s*(.+)$", re.sub(r"[*_`]", "", body))
    if m and EMPTY_VALUE_RE.match(m.group(1).strip()):
        return None
    if EMPTY_VALUE_RE.match(re.sub(r"[*_`]", "", body)):
        return None
    sentences = [s for s in split_sentences(body) if not is_meta(s)]
    if not sentences:
        return None
    prefix = line[: len(line) - len(line.lstrip())] + LIST_RE.match(line).group(0).strip() + " "
    return prefix + "".join(sentences).strip()


def _clean_prose(line: str) -> str:
    sentences = [s for s in split_sentences(line) if not is_meta(s) and not is_filler(s)]
    return "".join(sentences).strip()


def _clean_block(text: str) -> str:
    """章の本文（見出しの行を除く）から、注記・つなぎの文と値の無い行を消す"""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.extend(_clean_table(block))
            continue
        if not stripped or stripped.startswith(("#", ">", "```", "<")) or re.match(r"^!\[|^\[.*\]\(.*\)$", stripped):
            out.append(line)
        elif LIST_RE.match(line):
            item = _clean_list_item(line)
            if item is not None:
                out.append(item)
        else:
            cleaned = _clean_prose(stripped)
            if cleaned:
                out.append(cleaned)
        i += 1
    # ### 小見出しの下が空になったら小見出しごと消す
    result: list[str] = []
    for idx, line in enumerate(out):
        if line.startswith("### "):
            rest = out[idx + 1:]
            nxt = next((j for j, x in enumerate(rest) if x.startswith("### ")), len(rest))
            if not any(x.strip() for x in rest[:nxt]):
                continue
        result.append(line)
    text = "\n".join(result)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _prose_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith(("#", "|", ">", "```")) or LIST_RE.match(line):
            continue
        sentences += [x for x in split_sentences(s) if x.strip()]
    return sentences


def _numeric_cells(text: str) -> int:
    n = 0
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("|") and not TABLE_SEP_RE.match(s):
            n += sum(1 for c in _cells(s)[1:] if re.search(r"[0-9０-９]", c))
    return n


def _plain_numbers(text: str) -> list[str]:
    """日付・時刻を除いた数字（比較の材料になる数字）"""
    t = _nfkc(text)
    t = DATE_NUM_RE.sub("", t)
    return NUM_RE.findall(t)


def section_verdict(heading: str, content: str) -> str:
    """'keep' / 'hide'。content は _clean_block 済みの章の本文"""
    h = (heading or "").strip()
    if h == DATA_RIVAL_HEADING:
        return "keep"
    if PROTECTED_RE.match(h):
        return "keep"
    structured = [l for l in content.split("\n") if (LIST_RE.match(l) or l.strip().startswith("|")) and l.strip()]
    struct_text = "\n".join(structured)
    sentences = _prose_sentences(content)
    answers = [s for s in sentences if not is_nonanswer(s)]
    nonanswers = [s for s in sentences if is_nonanswer(s)]
    answer_text = "".join(answers)
    if not answers and not structured:
        return "hide"
    if RIVAL_RE.search(h):
        if OUTDATED_MODEL_RE.search(content):
            return "hide"
        if _numeric_cells(content) >= 2 or len(_plain_numbers(answer_text)) >= 2:
            return "keep"
        return "hide"
    if NUMBERS_RE.match(h):
        return "keep" if len(_plain_numbers(answer_text + "\n" + struct_text)) >= 2 else "hide"
    if PRICE_HEADING_RE.search(h) and not PRICE_WORD_RE.search(answer_text + struct_text):
        return "hide"
    # 一般の章: 表・箇条書きが無く、「答えになっていない文」が答えの文と同じかそれより多く、事実のある答えが1文以下なら出さない
    factual = [s for s in answers if has_fact(s)]
    if nonanswers and not structured and len(nonanswers) >= len(answers) and len(factual) <= 1:
        return "hide"
    return "keep"


def split_sections(markdown: str) -> list[tuple[str | None, str]]:
    """[(見出し or None, 本文)]。最初の ## より前は見出し None"""
    parts = re.split(r"(?m)^## ", markdown or "")
    out: list[tuple[str | None, str]] = []
    if parts and parts[0].strip():
        out.append((None, parts[0]))
    for p in parts[1:]:
        head, _, rest = p.partition("\n")
        out.append((head.strip(), rest))
    return out


# 検索に出す条件（lib/article-index.mjs の hasJapanValue・indexnow.py）が見る見出し。生成の段では消さない（表示の段では消す）
KEEP_FOR_INDEX_RE = re.compile(r"日本|無料|使い方|料金|始め方|使える")


def clean_markdown(markdown: str, keep_index_headings: bool = False) -> tuple[str, dict]:
    """(整えた Markdown, {"hidden": [見出し], "dropped": 消した文の数})。
    keep_index_headings=True（生成の段）は、検索に出す条件が見る見出しの章を消さない（中身の文の整理だけ）"""
    report = {"hidden": [], "dropped": 0}
    out: list[str] = []
    for head, content in split_sections(markdown):
        if head is None:
            out.append(content.strip())
            continue
        if head == DATA_RIVAL_HEADING:
            out.append(f"## {head}\n\n{content.strip()}")
            continue
        before = len(_prose_sentences(content))
        cleaned = _clean_block(content)
        report["dropped"] += max(0, before - len(_prose_sentences(cleaned)))
        if section_verdict(head, cleaned) == "hide" and not (keep_index_headings and KEEP_FOR_INDEX_RE.search(head)):
            report["hidden"].append(head)
            continue
        out.append(f"## {head}\n\n{cleaned}" if cleaned else f"## {head}")
    text = "\n\n".join(x for x in out if x)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + ("\n" if markdown.endswith("\n") else ""), report


def apply(record: dict) -> list[str]:
    """記事の本文とかんたん版を整える（生成の後・品質ゲートの前と、書き出しの前）。変えた内容のログを返す"""
    logs = []
    for field in ("body", "easy"):
        text = record.get(field)
        if not isinstance(text, str) or not text.strip():
            continue
        cleaned, report = clean_markdown(text, keep_index_headings=(field == "body"))
        if cleaned.strip() != text.strip():
            record[field] = cleaned
            hidden = "・".join(report["hidden"])
            logs.append(f"{field}: 中身のない章 {len(report['hidden'])}個{('（' + hidden + '）') if hidden else ''} / つなぎの文 {report['dropped']}文を削除")
    return logs


# ------------------------------------------------------------------ #
# 生成時の門（品質ゲートに足す問題点）。報告書「記事の文章レベル比較_2026-11-03」の自動ゲートのうち、本文・かんたん版の分
# ------------------------------------------------------------------ #
CHILDISH_RE = re.compile(r"すごい|しちゃ|ワクワク|うれしいのは|地味に|なんです")
SPLIT_SENTENCE_RE = re.compile(r"(?:要素|特徴|傾向|行動|ポイント|理由|狙い|背景|課題|違い)があります。[^。]{2,40}です。")
SOURCE_WORD_RE = re.compile(r"元記事")
QA_LINE_RE = re.compile(r"(?:の|なの)？$")
TEMPLATE_HEADING_RE = re.compile(r"^## (?:何がすごいの？|自分に関係ある？|もう(?:使える|買える|申し込める)？.*)$", re.M)


def style_problems(record: dict) -> list[str]:
    """書き直しを求める問題（品質ゲートの structural_problems に足す）。事実は見ない"""
    body = record.get("body") or ""
    easy = record.get("easy") or ""
    text = f"{body}\n{easy}"
    problems: list[str] = []
    meta = [s for s in _prose_sentences(text) if is_meta(s)]
    if meta:
        problems.append(f"中の人の注記・指示文が本文に出ている（「{meta[0][:30]}」）。消して、事実だけを書く")
    filler = [s for s in _prose_sentences(text) if FILLER_RE.search(s)]
    if len(filler) >= 3:
        problems.append(f"中身のないつなぎの文が{len(filler)}文ある（例「{filler[0][:30]}」）。"
                        "「〜にとっても重要です」「〜といえます」「〜に関係があります」「判断材料になります」は書かず、具体的な事実で言う")
    if m := SPLIT_SENTENCE_RE.search(text):
        problems.append(f"1つの文を2つに割っている（「{m.group(0)[:40]}」）。1文で言う")
    if m := CHILDISH_RE.search(re.sub(r"(?m)^#+ .*$", "", text)):
        problems.append(f"幼い言い方がある（「{m.group(0)}」）。大人のニュースの言葉で書く")
    qa = [l for l in text.split("\n") if l.strip() and not l.startswith("#") and QA_LINE_RE.search(l.strip())]
    if len(qa) >= 2:
        problems.append("一問一答の口調（「〜の？」で終わる行）がある。地の文で書く")
    return problems


def style_warnings(record: dict) -> list[str]:
    """不合格にはしない注意（品質ゲートの warnings）。
    材料の無い章は、生成の段では検索に出す条件の章（料金・日本で使える など）だけが残る。表示の段（lib/article-sections.ts）が隠すので不合格にはしない"""
    warnings = []
    body = record.get("body") or ""
    if len(SOURCE_WORD_RE.findall(body)) >= 3:
        warnings.append("「元記事」という言葉が多い。「〇〇の発表によると」と出典の名前で書き、不明な点は「〇〇はまだ発表されていません」と書く")
    empty = [h for h, c in split_sections(body) if h and section_verdict(h, _clean_block(c)) == "hide"]
    if empty:
        warnings.append("材料の無い章がある（" + "・".join(empty[:4]) + "）。表示では隠す")
    return warnings


def _load(slug: str) -> dict:
    return json.loads((ARTICLES_DIR / f"{slug}.json").read_text("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args(argv)
    if args.slug:
        record = _load(args.slug)
        for field in ("body", "easy"):
            cleaned, report = clean_markdown(record.get(field) or "")
            print(f"===== {field}: 隠す章 {report['hidden']} / 消す文 {report['dropped']}")
            print(cleaned)
        print("===== 生成時の門:", style_problems(record))
        return 0
    if args.stats:
        from collections import Counter

        hidden: Counter = Counter()
        dropped = 0
        articles = 0
        for path in sorted(ARTICLES_DIR.glob("*.json")):
            record = json.loads(path.read_text("utf-8"))
            changed = False
            for field in ("body", "easy"):
                _, report = clean_markdown(record.get(field) or "")
                hidden.update(f"{field}:{h}" for h in report["hidden"])
                dropped += report["dropped"]
                changed = changed or bool(report["hidden"] or report["dropped"])
            articles += changed
        print(f"変わる記事 {articles} / 消える文 {dropped} / 隠す章 {sum(hidden.values())}")
        for h, n in hidden.most_common(40):
            print(f"  {n:4d} {h}")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
