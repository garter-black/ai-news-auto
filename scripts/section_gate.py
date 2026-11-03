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

# 読者への答えになっていない「元記事には〜書かれていません」「判断できません」の文（オーナーのルール A2。2026-11-03）。
# 文ごと消す（章に答えが残らなければ章ごと隠れる）。「〇〇はまだ発表されていません」（何が未定かを主語にした答え）は消さない。
# 毎日の見張り scripts/owner_rules_audit.py（NON_ANSWER）も同じ式を使う
SOURCE_NONANSWER_RE = re.compile(
    r"元記事(?:に|で)(?:は|も)?、?[^。\n]{0,30}?(?:書かれて(?:い)?(?:ません|ない)|触れられて(?:い)?(?:ません|ない)|"
    r"(?:記載|言及|明記)(?:は|が)?(?:ありません|ない|なし|されていません|されてい?ない))|"
    r"[はも]、?(?:公式(?:ページ)?に)?記載(?:は|が)?(?:なし|ありません)|判断できません|判断できない状態です"
)
# 文の中のかっこ書きの注記（「（元記事では価格情報は言及なし）」）。かっこごと消す
NONANSWER_PAREN_RE = re.compile(r"[（(](?=[^（）()]*元記事)(?=[^（）()]*(?:言及|記載|明記|書かれて|触れられ))[^（）()]{0,60}[）)]")
# 表の注記の「『公式に記載なし』は〜」。表の空欄に合わせて「空欄」と言う（読者に「記載なし」を見せない。2026-11-03）
EMPTY_TOKEN_RE = re.compile(r"「(?:公式に|料金ページに|元記事に)?記載なし」")

# 読者像の3つの立場の小見出し（「### 会社員として」「### 副業・独立を考えている人」「### お金・資産づくりの面で」。
# 太字の行にされたもの・「会社員として仕事で」も含む）。どの記事にも同じ3つが並び、中身が答えになっていないことが多い
# （2026-11-03 オーナー決定・台帳 A24）。小見出しの行は出さず、その下は事実があって答えになっている文だけ残す。
# 記事の文体の門（scripts/article_style.py）も、この式を読み込めば同じ規則で落とせる。lib/article-sections.ts の PERSONA_SUBHEAD_RE と同じ
PERSONA_SUBHEAD_RE = re.compile(
    r"^(?:#{3,4}\s*|\*\*)(?:会社員として(?:仕事で)?|副業・独立を考えている人|お金・資産づくりの面で)[^\n。]{0,12}?(?:\*\*)?\s*$"
)
# 立場の段落に出がちな「答えにならない」文（事実を含んでいても消す）
PERSONA_FILLER_RE = re.compile(
    r"この立場|立場への|直接(?:的な|の)?(?:影響|関係|優遇|メリット)[^。]{0,16}(?:小さ|な[いく]|ありません|薄|限定的)|強いて言えば|"
    r"優遇措置(?:など)?はありません|収入にどうつながるか|直結(?:する(?:話|もの))?ではありません|直接(?:は)?関係(?:は|し)?(?:ない|ません)"
)
# 見出しの言葉だけ変えた「中身なしの章」の見出し（「比べられる数字は示されていない」「日本向けの提供条件はまだ分からない」
# 「分野ごとの実績数字、前版との比較はなし」。2026-11-03 オーナー決定・台帳 A24）。記事ごとの見出し（sectionTitles）にも、
# 本文の ## 見出しにも当てる。「## まだ分からないこと」（大事な未定事項をまとめる決まった章）は対象外。lib/article-sections.ts と同じ
NONANSWER_TITLE_RE = re.compile(
    r"(?:(?:示されて|書かれて|触れられて|明かされて|明らかにされて|公表されて|発表されて|決まって|分かって|わかって|確認できて|開示されて)(?:い)?ない|まだない|(?:比較|数字|数値|言及|記載|情報|発表|説明|案内|前版との比較)(?:は|も)?(?:なし|無し|ない)|未発表|未定|未公表|非公表|不明|分からない|わからない|未確認)[？?。]?$"
)
UNKNOWN_HEADING_RE = re.compile(r"^まだ分からないこと")


def is_nonanswer_title(title: str) -> bool:
    t = (title or "").strip()
    return bool(t) and not UNKNOWN_HEADING_RE.match(t) and bool(NONANSWER_TITLE_RE.search(t))


# 呼びかけの「〜しましょう」「〜してみましょう」を、落ち着いた言い方（「〜するとよいでしょう」）にする（オーナーのルール A4。2026-11-03
# 「〜してみましょう」が幼い・売り込みに見える）。事実は変えず、文末の言い方だけを変える。lib/article-sections.ts の professionalSentence と同じ規則
_ICHIDAN_I = ("落ち", "用い", "起き", "生き", "でき", "過ぎ", "浴び", "伸び", "延び", "試み", "借り", "見", "居", "着", "似")
_GODAN_SHI_COMPOUND = ("見直", "見通", "手放", "見逃", "見落", "見渡", "問い直", "引き出", "取り消", "打ち出", "乗り出", "洗い出", "割り出", "探し出", "見出")
_GODAN_ROW = {"い": "う", "き": "く", "ぎ": "ぐ", "し": "す", "ち": "つ", "に": "ぬ", "び": "ぶ", "み": "む", "り": "る"}
_E_ROW = set("えけげせぜてでねべぺめれへ")
_SURU_HEAD_RE = re.compile(r"(?:[\u3400-\u9fff々]{2}|[ァ-ヴー]|[A-Za-zＡ-Ｚａ-ｚ0-9０-９]|[をのに])$")
_TE_MASHOU_RE = re.compile(r"([てで])(みましょう|おきましょう|いきましょう)([。！]?)$")
_INTRO_RE = re.compile(r"(見|整理し|確認し|比較し|紹介し)ていきましょう([。！]?)$")
_MASHOU_RE = re.compile(r"ましょう([。！]?)$")


def _dictionary_form(stem: str) -> str | None:
    """「〜ましょう」の前（連用形）を辞書形にする。分からなければ None（言い換えない）"""
    if not stem:
        return None
    if stem.endswith("し"):
        head = stem[:-1]
        if head.endswith(_GODAN_SHI_COMPOUND):
            return head + "す"
        return head + ("する" if (not head or _SURU_HEAD_RE.search(head)) else "す")
    if stem.endswith("じ"):
        return stem + "る"
    if stem.endswith(_ICHIDAN_I) or stem[-1] in _E_ROW:
        return stem + "る"
    if stem[-1] in _GODAN_ROW:
        return stem[:-1] + _GODAN_ROW[stem[-1]]
    return None


def _open_brackets(text: str) -> int:
    depth = 0
    for ch in text:
        if ch in "「『（(":
            depth += 1
        elif ch in "」』）)" and depth > 0:
            depth -= 1
    return depth


def professional(sentence: str) -> str:
    """1文の文末の「〜しましょう」を「〜するとよいでしょう」に（節の前置きの「見ていきましょう」は「見ていきます」）。
    かっこの中（引用）や、言い換え方が分からない文はそのまま"""
    s = sentence.rstrip()
    tail = sentence[len(s):]
    if "ましょう" not in s or _open_brackets(s):
        return sentence
    end = lambda mark: "。" if mark else ""  # noqa: E731（「！」は「。」にそろえる）
    if m := _INTRO_RE.search(s):
        return s[: m.start()] + m.group(1) + "ていきます" + end(m.group(2)) + tail
    if m := _TE_MASHOU_RE.search(s):
        aux = {"みましょう": "みる", "おきましょう": "おく", "いきましょう": "いく"}[m.group(2)]
        return s[: m.start()] + m.group(1) + aux + "とよいでしょう" + end(m.group(3)) + tail
    if m := _MASHOU_RE.search(s):
        base = _dictionary_form(s[: m.start()])
        if base:
            return base + "とよいでしょう" + end(m.group(1)) + tail
    return sentence


def ends_with_call(sentence: str) -> bool:
    """「〜しましょう」で終わる文か（かっこの中の引用は除く）。毎日の見張り（読者に見える文）が使う"""
    s = sentence.rstrip()
    return bool(_MASHOU_RE.search(s)) and not _open_brackets(s)


def calling_out(sentence: str) -> bool:
    """言い換えても「〜しましょう」で終わる文が残るか（生成の門の問題点）"""
    return ends_with_call(professional(sentence))


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


def _is_empty_value(cell: str) -> bool:
    return bool(EMPTY_VALUE_RE.match(re.sub(r"[*_`]", "", cell)))


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _clean_table(lines: list[str]) -> list[str]:
    """値が全部「言及なし」などの行・列を消し、残った「記載なし」「—」のセルは空欄にする（読者に「記載なし」を見せない。2026-11-03）。
    見出し行と区切り行だけになったら表ごと消す"""
    if len(lines) < 2 or not TABLE_SEP_RE.match(lines[1].strip()):
        return lines
    header = _cells(lines[0])
    source_col = len(header) - 1 if header and re.search(r"出典|ソース", header[-1]) else -1
    kept = lines[:2]
    for line in lines[2:]:
        cells = _cells(line)
        values = [c for i, c in enumerate(cells) if i > 0 and i != source_col]
        if values and all(_is_empty_value(v) for v in values):
            continue
        kept.append(line)
    if len(kept) <= 2:
        return []
    rows = [_cells(line) for line in kept[2:]]
    if not any(_is_empty_value(c) for r in rows for c in r[1:]):
        return kept
    width = len(header)
    drop = {i for i in range(1, width) if all(i >= len(r) or _is_empty_value(r[i]) for r in rows)}
    keep_cols = [i for i in range(width) if i not in drop]
    sep = _cells(kept[1])
    out = [_row([header[i] for i in keep_cols]), _row([sep[i] if i < len(sep) else "---" for i in keep_cols])]
    for r in rows:
        out.append(_row(["" if i > 0 and i < len(r) and _is_empty_value(r[i]) else (r[i] if i < len(r) else "") for i in keep_cols]))
    return out


def _tidy(text: str) -> str:
    """文の前の下ごしらえ: かっこ書きの「元記事では言及なし」を消し、表の注記の「『記載なし』」を「空欄」にする"""
    return EMPTY_TOKEN_RE.sub("空欄", NONANSWER_PAREN_RE.sub("", text))


def _keep_sentence(sentence: str) -> bool:
    return not is_meta(sentence) and not SOURCE_NONANSWER_RE.search(sentence)


def _clean_list_item(line: str) -> str | None:
    """「- 価格: 言及なし」のような、値が無いだけの箇条書きを消す"""
    body = _tidy(LIST_RE.sub("", line, count=1).strip())
    m = re.match(r"^[^：:]{1,40}[：:]\s*(.+)$", re.sub(r"[*_`]", "", body))
    if m and EMPTY_VALUE_RE.match(m.group(1).strip()):
        return None
    if EMPTY_VALUE_RE.match(re.sub(r"[*_`]", "", body)):
        return None
    # 「注意点：〜」の見出し語は残し、値の文だけを整える
    label_m = re.match(r"^([^：:。]{1,40}[：:]\s*)(.+)$", body)
    label, value = (label_m.group(1), label_m.group(2)) if label_m else ("", body)
    sentences = [professional(s) for s in split_sentences(value) if _keep_sentence(s)]
    text = "".join(sentences).strip()
    if not text:
        return None
    text = label + text
    prefix = line[: len(line) - len(line.lstrip())] + LIST_RE.match(line).group(0).strip() + " "
    return prefix + text


def _clean_prose(line: str) -> str:
    sentences = [professional(s) for s in split_sentences(_tidy(line)) if _keep_sentence(s) and not is_filler(s)]
    return "".join(sentences).strip()


def _persona_sentence_ok(sentence: str) -> bool:
    s = sentence.strip()
    return bool(s) and _keep_sentence(s) and not is_nonanswer(s) and not FILLER_RE.search(s) and not PERSONA_FILLER_RE.search(s)


def _persona_paragraph(text: str) -> str:
    """立場の段落: 答えにならない文を除き、事実（数字・英字の名前・「」）が残る段落だけ返す（残らなければ空）"""
    kept = "".join(x for x in split_sentences(text) if _persona_sentence_ok(x)).strip()
    return kept if has_fact(kept) else ""


def _drop_persona(lines: list[str]) -> list[str]:
    """立場の小見出しの行を消し、その下（次の見出し・太字の行まで）は事実があって答えになっている文だけ残す（台帳 A24）"""
    out: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if PERSONA_SUBHEAD_RE.match(stripped):
            inside = True
            continue
        if stripped.startswith("#") or re.match(r"^\*\*[^*]+\*\*$", stripped):
            inside = False
        if not inside or not stripped or stripped.startswith("|"):
            out.append(line)
            continue
        if LIST_RE.match(line):
            if _persona_paragraph(LIST_RE.sub("", line, count=1)):
                out.append(line)
            continue
        kept = _persona_paragraph(stripped)
        if kept:
            out.append(kept)
    return out


def _clean_block(text: str) -> str:
    """章の本文（見出しの行を除く）から、注記・つなぎの文と値の無い行を消す"""
    lines = _drop_persona(text.split("\n"))
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


def _clean_data_table(text: str) -> str:
    """確かめ済みの比較表の章（scripts/rivals.py）。文は消さず、表の「公式に記載なし」を空欄にして注記の言い方をそろえるだけ"""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.extend(_clean_table(block))
            continue
        out.append(EMPTY_TOKEN_RE.sub("空欄", lines[i]))
        i += 1
    return "\n".join(out)


def clean_take(take: str) -> str:
    """判定のひとこと: 答えになっていない文を除き、呼びかけの文末をそろえる。全部消えるなら空"""
    return "".join(professional(x) for x in split_sentences(_tidy(take or "")) if _keep_sentence(x)).strip()


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


def _substantive(content: str) -> bool:
    """表があるか、事実のある答えの文・箇条書きが2つ以上あるか（見出しが「〜はない」でも中身がある章は隠さない）"""
    if any(l.strip().startswith("|") for l in content.split("\n")):
        return True
    items = [LIST_RE.sub("", l, count=1) for l in content.split("\n") if LIST_RE.match(l)]
    answers = [x for x in _prose_sentences(content) + items if has_fact(x) and not is_nonanswer(x)]
    return len(answers) >= 2


def clean_markdown(markdown: str, keep_index_headings: bool = False, titles: dict | None = None) -> tuple[str, dict]:
    """(整えた Markdown, {"hidden": [見出し], "dropped": 消した文の数})。
    keep_index_headings=True（生成の段）は、検索に出す条件が見る見出しの章を消さない（中身の文の整理だけ）。
    titles は記事ごとの見出し（記事 JSON の sectionTitles の body か easy）。表示の見出しが「〜はない／未発表」の章は、
    中身（表か、事実のある答えが2つ以上）が無ければ隠す（台帳 A24）"""
    report = {"hidden": [], "dropped": 0}
    titles = titles or {}
    out: list[str] = []
    for head, content in split_sections(markdown):
        if head is None:
            out.append(content.strip())
            continue
        if head == DATA_RIVAL_HEADING:
            out.append(f"## {head}\n\n{_clean_data_table(content).strip()}")
            continue
        before = len(_prose_sentences(content))
        cleaned = _clean_block(content)
        report["dropped"] += max(0, before - len(_prose_sentences(cleaned)))
        protect = keep_index_headings and KEEP_FOR_INDEX_RE.search(head)
        if section_verdict(head, cleaned) == "hide" and not protect:
            report["hidden"].append(head)
            continue
        own = titles.get(head) if isinstance(titles.get(head), str) else ""
        if (is_nonanswer_title(own) or is_nonanswer_title(head)) and not protect and not _substantive(cleaned):
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
        titles = (record.get("sectionTitles") or {}).get(field) if isinstance(record.get("sectionTitles"), dict) else None
        cleaned, report = clean_markdown(text, keep_index_headings=(field == "body"), titles=titles if isinstance(titles, dict) else None)
        if cleaned.strip() != text.strip():
            record[field] = cleaned
            hidden = "・".join(report["hidden"])
            logs.append(f"{field}: 中身のない章 {len(report['hidden'])}個{('（' + hidden + '）') if hidden else ''} / つなぎの文 {report['dropped']}文を削除")
    verdict = record.get("verdict")
    if isinstance(verdict, dict) and isinstance(verdict.get("take"), str):
        take = clean_take(verdict["take"]) or verdict["take"]
        if take != verdict["take"]:
            logs.append(f"判定のひとこと: 「{verdict['take'][-14:]}」→「{take[-16:]}」")
            verdict["take"] = take
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
    take = str((record.get("verdict") or {}).get("take") or "") if isinstance(record.get("verdict"), dict) else ""
    calls = [x for x in _prose_sentences(text) + ([take] if take else []) if calling_out(x)]
    if calls:
        problems.append(f"呼びかけの「〜しましょう」が残っている（「{calls[0][-20:]}」）。「〜するとよいでしょう」など落ち着いた言い方で書く")
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
            cleaned, report = clean_markdown(record.get(field) or "", titles=(record.get("sectionTitles") or {}).get(field))
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
                _, report = clean_markdown(record.get(field) or "", titles=(record.get("sectionTitles") or {}).get(field))
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
