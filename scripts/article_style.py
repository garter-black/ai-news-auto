#!/usr/bin/env python3
"""記事の文体の決まり（1か所だけ）と、公開前の門（2026-11-03 オーナー決定）。

オーナーの指摘: 「幼すぎる表現は本当にやめたい。恥ずかしい」「日本語として変なタイトル」
報告 out/記事の文章レベル比較_2026-11-03.md: 他社（ITmedia・Impress・GIGAZINE）は最初の1文で「今回なにが新しいか」を言い切り、
見出しは0〜1個。サイソクは型どおりの見出しが15個、中身のない章が2割、幼い言い方・機械的に割った文が出ていた。

このファイルが持つもの（ほかのファイルに文面を複製しない）:
  - STYLE_PROMPT: すべての生成の段（通常・速報・幅広い話題・話題のキーワード・動画・まとめ・別の切り口・かんたん版）に渡す文体の決まり
  - autofix(record): プログラムで直せるもの（「元記事」の言い換え・種類に合わない判定カード）
  - problems(record): 公開前の門。1つでもあれば作り直し（quality.structural_problems から呼ぶ。通らなければ公開しない）
  - warnings(record): 記録だけ（作り直しにはしない）
  報告の自動ゲート17項目と、台帳 docs/owner_rules.md の A1〜A6・A18〜A21 をここで守らせる。
  表示の段の章の掃除（中身のない章を出さない）は scripts/section_gate.py / lib/article-sections.ts が受け持つ。

  python3 scripts/article_style.py --slug apple-reports-q4-2026-results   # 今ある記事を門にかけて表示
  python3 scripts/article_style.py --recent 200                           # 直近の記事で門に落ちる割合
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import news_types  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"

# --------------------------------------------------------------------------- #
# 生成に渡す文体の決まり（すべての段で同じものを使う）
# --------------------------------------------------------------------------- #

STYLE_PROMPT = """## サイソクの文体（ITmedia・日経クロステック水準の、伝わりやすい大人の日本語。すべてに優先する）
- 読者は忙しい会社員。です・ます調。詳しい先輩が同僚に説明する温度で、落ち着いて具体的に書く。
- やさしい ≠ 幼い。一問一答（「で、何がすごいの？」）、「すごい」「〜しちゃう」「うれしいのは」「〜なんです」「地味に」「ワクワク」、
  ひらがなの多用、子ども向けの例えは使わない。
- 1文は60字までを目安にする。読点は1文に2つまで使ってよい。「〜があります。〇〇です。」のように1つの文を2つに割らない。
- 専門用語は、初めて出たところで（ ）で短く言い換える。例：CVSS（深刻さを示す点数）。
- リードの1文目で「今回なにが新しいか」を言い切る（誰が・何を・いつ）。2文目に一番大事な数字か条件。
  発表した人を主語にした「〇〇は…を発表しました」だけの1文で始めない。
  ×「Appleは11月2日、第4四半期の決算を発表しました。」 ○「Appleの7〜9月期の売上高は前年同期比12%増の1,145億ドルで、過去最高でした。」
  ×「デジタル庁は、アンケートの結果を公表しました。」 ○「政府職員向けの生成AI「源内」について、利用者の71.7%が「満足」と答えました。」
- リード・3つの要点・本文・まとめ・かんたん版で、同じ文や同じ事実をくり返さない。同じ数字は本文で2〜3回まで。
- 見出しは記事の中身の言葉で、本文1,000字に1つまで。決まった質問の見出し（料金は？／日本で使える？／自分に関係ある？／
  ライバルと比べてどう？／もう使える？／何がすごいの？／数字で見ると？）と「## 概要」は使わない。
- 材料の無い章・欄は作らない。「元記事」「言及なし」「記載なし」「書かれていません」「確認できていません」「判断できません」は書かない。
  大事なのに発表されていないことは、最後に「## まだ分からないこと」の箇条書きで1回だけ（「〇〇はまだ発表されていません」）。
- 宣伝の言葉（「最も変革的」「業界初」「画期的」「革新的」）は地の文で書かない。「〇〇氏は…と説明しました」と誰の言葉かを書く。
- 根拠の無いつなぎ（「〜にとっても重要です」「〜と言えます」「〜の機会が生まれる可能性があります」「注目です」「見逃せない」
  「チェックしておきたい」「判断材料になります」）は書かない。
- 画面の名前・道順は、資料に書かれているものだけを書く（作らない）。資料の画面名が英語だけなら、英語の名前を並べず
  「設定の使用状況の画面」のように普通の言葉で書く。
- 指示文の言葉（「読者が次に取るべき行動」「今回の発表の位置づけ」「今回の取材対象」）を本文に出さない。
  背景知識で補う「（編集部注）」は記事全体で2か所まで（それ以上要るなら、その補足は書かない）。
- 一部改良を「新型」と書かない。前からあるものを「新たに」と書かない。「ざっくり」は使わない（要約は「要約すると、」）。"""

# --------------------------------------------------------------------------- #
# 門の規則
# --------------------------------------------------------------------------- #

HEADING_RE = re.compile(r"(?m)^(#{2,3})\s+(.+?)\s*$")
# 公開前の門（scripts/indexnow.py の JAPAN_VALUE）が見る、日本の読者に関係する見出しの言葉
JAPAN_HEADING_RE = re.compile(r"日本|無料|使い方|料金|始め方|使える")
UNKNOWN_HEADING = "まだ分からないこと"
# 決まった型の見出し（報告の自動ゲート2・台帳 A1）
TEMPLATE_HEADING_RE = re.compile(
    r"^(?:料金は[？?]|料金・提供(?:プラン|状況)は?[？?]?|日本で(?:も)?使える[？?]|自分(?:に|には)?(?:どう)?関係(?:する|ある)[？?]|"
    r"ライバルと比べてどう[？?]|もう(?:使える|買える)[？?].*|取引できる[？?]|スペックと価格は[？?]|数字で見ると.*|何がすごいの[？?]|"
    r"何が発表された[？?]|前と何が違う[？?]|何が変わる[？?]|いつから[？?]|誰が対象[？?]|使ってみるには|概要|詳細|ポイント)$"
)
EASY_TEMPLATE_RE = re.compile(r"^(?:何がすごいの[？?]|自分(?:に|には)?関係(?:ある|する)[？?]|使ってみるには)$")
NONANSWER_RE = re.compile(
    r"元記事|言及(?:は|が)?(?:ありません|なし|されていません)|記載(?:は|が)?(?:ありません|なし)|書かれて(?:い)?ません|"
    r"確認でき(?:ていませ|ませ)ん|判断できません|明らかにされて(?:い)?ません|触れられて(?:い)?ません|案内(?:は|が)?(?:ありません|なし)"
)
LEAK_RE = re.compile(r"読者が次に取るべき|(?:今回の)?発表の位置づけ(?:は|として)|今回の取材対象|ここは編集部注です|以下は編集部の(?:知識|見立て)")
EDITOR_NOTE_RE = re.compile(r"[（(]編集部注[）)]")
SPLIT_RE = re.compile(r"(?:要素|特徴|傾向|行動|ポイント|理由|狙い|背景|課題|違い|メリット|特長|注意点|変化)があります。[^。\n]{2,60}です。")
QA_LINE_RE = re.compile(r"(?:の|なの|んです|でしょ)[？?]\s*$")
CHILDISH_RE = re.compile(r"すごい|スゴい|すごく便利|しちゃ|ちゃいます|ワクワク|うれしいのは|嬉しいのは|地味に|なんです(?!か)|ですよね|なんですよ")
ZAKKURI_RE = re.compile(r"ざっくり")
CHEAP_LABEL_RE = re.compile(r"3秒でわかる|３秒でわかる")
FORCED_DESC_RE = re.compile(
    r"(?:ChatGPT|Claude|Gemini|Copilot|Windows|iPhone)の(?:OpenAI|Anthropic|Google|Microsoft|Apple)|AI半導体(?:大手)?のNVIDIA|半導体大手の|"
    r"タスク管理の(?:アトラシアン|Atlassian)|データ基盤の(?:Databricks|データブリックス)|対話AI「Claude」の|ABEMA運営の|複写機の"
)
PROMO_RE = re.compile(r"最も変革的|業界初|画期的|革新的|最もパワフル|史上最高の|圧倒的な|唯一無二")
ATTRIB_RE = re.compile(r"氏|によると|としています|と説明|と述べ|と紹介|と強調|とうた|と主張|と話|と発表|と位置づけ|と呼|と表現")
FILLER_RE = re.compile(
    r"にとって(?:も)?(?:重要|大切|見逃せない)(?:です|な)|と(?:言|い)え(?:ます|るでしょう)|見逃せません|見逃せない|注目です|"
    r"チェックしておきたい|判断材料(?:に|と)?なります|期待が高まります|目が離せません|気になるところです"
)
MAYBE_RE = re.compile(r"(?:機会|選択肢|可能性)が(?:生まれる|広がる)?可能性があります|可能性があります")
ENGLISH_UI_RE = re.compile(r"\bSettings\s*(?:→|>|＞|›)|(?:→|>|＞|›)\s*Usage\b|Current session|\[Settings\]|「Settings」|「Usage」")
NEW_MODEL_RE = re.compile(r"新型")
MINOR_CHANGE_RE = re.compile(r"一部改良|マイナーチェンジ")
NUMBER_RE = re.compile(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?\s?(?:%|％|円|万円|億円|兆円|ドル|万ドル|億ドル|ユーロ|倍|人|件|社|台|GB|TB|時間)")
QUOTE_RE = re.compile(r"「[^」\n]{0,200}」|『[^』\n]{0,200}』|“[^”\n]{0,200}”|`[^`\n]*`")
TABLE_LINE_RE = re.compile(r"(?m)^\s*\|.*\|\s*$")


def _plain(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text or "")
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[#*>`_|\-\s]", "", text)


def _plain_len(text: str) -> int:
    return len(_plain(text))


def sections(body: str) -> list[tuple[str, str]]:
    """[(見出し, 章の本文)]。見出しより前の部分は見出し "" """
    out: list[tuple[str, str]] = []
    heading, buf = "", []
    for line in (body or "").split("\n"):
        m = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if m:
            out.append((heading, "\n".join(buf)))
            heading, buf = m.group(1), []
        else:
            buf.append(line)
    out.append((heading, "\n".join(buf)))
    return [(h, t) for h, t in out if h or t.strip()]


def headings(body: str) -> list[str]:
    return [m.group(2).strip() for m in HEADING_RE.finditer(body or "")]


def _sentences(text: str) -> list[str]:
    text = re.sub(r"(?m)^\s*(?:#{1,6}|[-*・]|\d+[.)）])\s*", "", text or "")
    return [s.strip() for s in re.split(r"(?<=[。！？!?])\s*|\n+", text) if len(s.strip()) >= 6]


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s　、。，．,.・「」『』()（）:：]", "", unicodedata.normalize("NFKC", text or ""))
    return {t[i:i + 2] for i in range(len(t) - 1)}


def similarity(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / min(len(x), len(y))


def _no_quotes(text: str) -> str:
    return QUOTE_RE.sub("", text or "")


def news_type_of(record: dict) -> str:
    return record.get("newsType") or ""


def max_headings(body: str) -> int:
    """本文の長さで決まる見出しの上限（1,000字につき1つ。報告の自動ゲート3）"""
    return _plain_len(body) // 1000 + 1


# --------------------------------------------------------------------------- #
# 直せるものはプログラムで直す
# --------------------------------------------------------------------------- #


def autofix(record: dict) -> list[str]:
    """プログラムで直せるもの。直した内容の一覧"""
    changes: list[str] = []
    name = (record.get("sourceName") or "").strip() or "発表元"
    for key in ("body", "easy", "lede"):
        text = record.get(key)
        if not isinstance(text, str) or "元記事" not in text:
            continue
        new = re.sub(r"元記事(?:の発表)?によると", f"{name}の発表によると", text)
        new = re.sub(r"元記事(?:で|に)?は", "発表では", new)
        new = re.sub(r"元記事(?:の)?", "発表", new)
        if new != text:
            record[key] = new
            changes.append(f"{key}: 「元記事」を言い換えた")
    for key in ("highlights",):
        vals = record.get(key) or []
        fixed = [re.sub(r"元記事(?:で|に)?は", "発表では", str(v)).replace("元記事", "発表") for v in vals]
        if fixed != vals:
            record[key] = fixed
            changes.append("要点: 「元記事」を言い換えた")
    # 本文の最初の段落がリードと同じ文で始まるとき、その文を外す（リードの続きから始める。報告の自動ゲート13）
    body = record.get("body") or ""
    lede_sents = _sentences(record.get("lede") or "")
    if body and lede_sents:
        parts = re.split(r"(\n\s*\n)", body, maxsplit=1)
        first = parts[0]
        if not first.lstrip().startswith(("#", "|", "-", "*")):
            sents = re.split(r"(?<=。)", first)
            dropped = 0
            while sents and dropped < 2 and sents[0].strip() and any(similarity(sents[0], x) >= 0.8 for x in lede_sents):
                sents.pop(0)
                dropped += 1
            if dropped:
                rest = "".join(sents).strip()
                record["body"] = (rest + "".join(parts[1:])) if rest else "".join(parts[2:]).lstrip("\n")
                changes.append(f"本文の最初の{dropped}文がリードと同じだったので外した")
    # 要点（3つ）のうちリードの文とほぼ同じものは外す（2つ以上残るときだけ。報告の自動ゲート13）
    highlights = [str(h) for h in record.get("highlights") or []]
    if lede_sents and len(highlights) >= 3:
        kept = [h for h in highlights if not any(similarity(h, x) >= 0.9 for x in lede_sents)]
        if 2 <= len(kept) < len(highlights):
            record["highlights"] = kept
            changes.append(f"リードとほぼ同じ要点を{len(highlights) - len(kept)}つ外した")
    # 見出しが多すぎるとき、小見出し（###）を太字の行にし、まだ多ければ後ろの見出しから太字の行にする（中身は残す。報告の自動ゲート3）。
    # 日本の読者に関係する見出し（公開前の門 indexnow の no-japan-value が見る言葉）と「まだ分からないこと」は残す
    body = record.get("body") or ""
    heads = [h for h in headings(body) if not h.startswith(UNKNOWN_HEADING)]
    if len(heads) > max_headings(body) and re.search(r"(?m)^###\s", body):
        body = re.sub(r"(?m)^###\s+(.+?)\s*$", r"**\1**\n", body)
        record["body"] = body
        changes.append("見出しが多いので、小見出し（###）を太字の行にした")
    heads = [h for h in headings(body) if not h.startswith(UNKNOWN_HEADING)]
    limit = max_headings(body)
    if len(heads) > limit:
        keep_japan = next((h for h in heads if JAPAN_HEADING_RE.search(h)), None)
        keep = set(heads[: limit - (1 if keep_japan and keep_japan not in heads[:limit] else 0)])
        if keep_japan:
            keep.add(keep_japan)

        def demote(m: re.Match) -> str:
            text = m.group(1).strip()
            return m.group(0) if text in keep or text.startswith(UNKNOWN_HEADING) else f"**{text}**\n"

        record["body"] = re.sub(r"(?m)^##\s+(.+?)\s*$", demote, body)
        changes.append(f"見出しが多いので、{len(heads) - len(keep)}個を太字の行にした（本文{_plain_len(body)}字で{limit}個まで）")
    key = news_type_of(record)
    if key and record.get("verdict") and not news_types.allows_verdict(key) and key not in ("general", "explainer"):
        record["verdict"] = None
        changes.append(f"判定カードを外した（{news_types.get(key).label}の記事には出さない）")
    return changes


# --------------------------------------------------------------------------- #
# 公開前の門
# --------------------------------------------------------------------------- #


def coded_problems(record: dict) -> list[tuple[str, str]]:
    """公開前の門の指摘と、台帳 docs/owner_rules.md の番号（[A1] など）の組。毎日の点検 owner_rules_audit.py も使う"""
    out: list[tuple[str, str]] = []

    def add(code: str, message: str) -> None:
        if (code, message) not in out:
            out.append((code, message))

    body = record.get("body") or ""
    easy = record.get("easy") or ""
    lede = record.get("lede") or ""
    title = record.get("title") or ""
    heads = headings(body)
    body_nq = _no_quotes(body)

    # 1. 中身のない章（章の本文が短く、答えになっていない）
    for heading, text in sections(body):
        if not heading or heading.startswith(UNKNOWN_HEADING):
            continue
        sents = _sentences(text)
        empty = [x for x in sents if NONANSWER_RE.search(x)]
        if _plain_len(text) < 260 and empty and len(empty) * 2 >= len(sents) and not TABLE_LINE_RE.search(text):
            add("A2", f"中身のない章「{heading}」（答えになっていない）。章ごと消し、大事な未定事項は「## {UNKNOWN_HEADING}」に1行で書く")
    # 2. 決まった型の見出し
    bad = [h for h in heads if TEMPLATE_HEADING_RE.match(h)]
    if bad:
        add("A1", f"決まった型の見出し（{'・'.join('「' + h + '」' for h in bad[:4])}）。その記事でしか使えない中身の言葉にするか、見出しを消す")
    # 3. 見出しが多すぎる
    count = len([h for h in heads if not h.startswith(UNKNOWN_HEADING)])
    if count > max_headings(body):
        add("A18", f"見出しが多すぎる（{count}個 / 本文{_plain_len(body)}字なら{max_headings(body)}個まで）。章をまとめて見出しを減らす")
    # 4. 「元記事」の言葉（autofix の後に残ったもの）
    if len(re.findall(r"元記事", body)) >= 2:
        add("A2", "本文に「元記事」が残っている。「〇〇の発表によると」と書くか消す")
    # 5. 指示文の漏れ
    if m := LEAK_RE.search(body + "\n" + easy):
        add("A3", f"指示文の言葉が本文に出ている（「{m.group(0)}」）")
    # 報告の自動ゲート5（3回以上ではじく）。生成の指示（WEB_SOURCE_NOTE）は背景の補足にこの印を付けさせるので、2回までは認める
    if len(EDITOR_NOTE_RE.findall(body)) >= 3:
        add("A3", "「（編集部注）」が多い。背景の補足は2か所までにし、事実で書けない補足は消す")
    # 中の人の注記と、呼びかけの「〜しましょう」（台帳 A3・A17。判定は表示の段と同じ scripts/section_gate.py のものを使う）
    try:
        import section_gate  # noqa: PLC0415

        sents = section_gate._prose_sentences(f"{body}\n{easy}")  # noqa: SLF001
        if meta := [x for x in sents if section_gate.is_meta(x)]:
            add("A3", f"中の人の注記・指示文が本文に出ている（「{meta[0][:30]}」）。消して、事実だけを書く")
        take = str((record.get("verdict") or {}).get("take") or "") if isinstance(record.get("verdict"), dict) else ""
        if calls := [x for x in sents + ([take] if take else []) if section_gate.calling_out(x)]:
            add("A17", f"呼びかけの「〜しましょう」が残っている（「{calls[0][-20:]}」）。「〜するとよいでしょう」など落ち着いた言い方で書く")
    except (ImportError, AttributeError):
        pass
    # 読者像の3つの立場の小見出しと、見出しの言葉だけ変えた「中身なし」の見出し（「〜は不明」「〜は示されていない」。台帳 A24。
    # 判定は表示の段と同じ scripts/section_gate.py の式）
    try:
        import section_gate  # noqa: PLC0415

        for key, text in (("本文", body), ("かんたん版", easy)):
            if any(section_gate.PERSONA_SUBHEAD_RE.match(ln.strip()) for ln in text.split("\n")):
                add("A24", f"{key}に読者像の立場の小見出し（会社員として／副業・独立を考えている人／お金・資産づくりの面で）。小見出しを作らず、事実がある影響だけ地の文で書く")
        if bad_t := [h for h in heads if section_gate.is_nonanswer_title(h)]:
            add("A24", f"答えになっていない見出し（「{bad_t[0]}」）。未定のことは「## まだ分からないこと」に1行で書き、章は作らない")
    except (ImportError, AttributeError):
        pass
    # 6. 割った文
    if m := SPLIT_RE.search(body_nq):
        add("A4", f"1つの文を2つに割っている（「{m.group(0)[:40]}」）。1文にまとめる")
    # 7. 一問一答の口調
    qa = [ln for ln in body_nq.split("\n") if ln.strip() and not ln.lstrip().startswith(("#", "|")) and QA_LINE_RE.search(ln)]
    if len(qa) >= 2:
        add("A4", f"一問一答の口調（「{qa[0].strip()[:30]}」など{len(qa)}行）。説明文で書く")
    # 8. 幼い言葉（本文・かんたん版・リード。引用の中は除く）
    for key, text in (("本文", body), ("かんたん版", easy), ("リード", lede)):
        if m := CHILDISH_RE.search(_no_quotes(text)):
            add("A4", f"{key}に幼い言い方（「{m.group(0)}」）。落ち着いた大人の言葉にする")
    for key, text in (("本文", body), ("かんたん版", easy), ("リード", lede), ("題名", title)):
        if ZAKKURI_RE.search(text):
            add("A4", f"{key}に「ざっくり」。「要約すると」を使う")
        if CHEAP_LABEL_RE.search(text):
            add("A6", f"{key}に「3秒でわかる」のようなあざとい言葉")
    # 9. 無理な説明つきの社名（題名・リード）
    for key, text in (("題名", title), ("リード", lede)):
        if m := FORCED_DESC_RE.search(text):
            add("A5", f"{key}に説明つきの社名（「{m.group(0)}」）。社名だけにする")
    # 10. 「新型」と「一部改良」
    whole = f"{title}\n{lede}\n{body}"
    if NEW_MODEL_RE.search(whole) and MINOR_CHANGE_RE.search(whole):
        add("A5", "一部改良なのに「新型」と書いている")
    # 11. 宣伝の言葉を地の文で
    for sentence in _sentences(body):
        if (m := PROMO_RE.search(_no_quotes(sentence))) and not ATTRIB_RE.search(sentence):
            add("A3", f"宣伝の言葉「{m.group(0)}」を地の文で書いている。「〇〇は…と説明しています」と誰の言葉かを書く")
            break
    # 12. 根拠のないつなぎ
    fillers = FILLER_RE.findall(_no_quotes(f"{body}\n{easy}"))
    if len(fillers) >= 2:
        add("A3", f"中身のないつなぎの文（「{fillers[0]}」など{len(fillers)}か所）。消すか事実で書く")
    if len(MAYBE_RE.findall(body_nq)) >= 3:
        add("A3", "「可能性があります」が多い（3か所以上）。根拠のある事実だけを書く")
    # 13. リードのくり返し（本文の最初の文・要点）
    first_body = next(iter(_sentences(next((t for h, t in sections(body)), ""))), "")
    lede_sents = _sentences(lede)
    if first_body and lede_sents and any(similarity(first_body, x) >= 0.8 for x in lede_sents):
        add("A19", "本文の最初の文がリードのくり返し。本文はリードに無い新しい事実から始める")
    if lede_sents and re.search(r"(?:業績|決算|結果|注意喚起|お知らせ|概要|内容|方針|計画|報告書|レポート|資料|調査)(?:について|に関する[^。]{0,10})?を"
                                r"(?:発表|公表|公開)しました。?$", lede_sents[0]) \
            and not re.search(r"\d[\d,.]*\s?(?:%|％|円|ドル|億|万|倍|人|件|社|台)", lede_sents[0]) \
            or (lede_sents and len(lede_sents[0]) < 36 and re.search(r"(?:発表|公開|公表)しました。?$", lede_sents[0])):
        add("A19", "リードの1文目が「〇〇を発表しました」で終わり、何が新しいか（中身・数字）を言っていない。1文目で新しい事実を言い切る")
    for h in record.get("highlights") or []:
        if any(similarity(h, s) >= 0.9 for s in lede_sents):
            add("A19", f"要点「{str(h)[:24]}…」がリードの文とほぼ同じ。要点にはリードに無い事実を入れる")
            break
    # 14. 同じ数字のくり返し（表の外）
    prose = TABLE_LINE_RE.sub("", body)
    counts: dict[str, int] = {}
    for n in NUMBER_RE.findall(prose):
        key = re.sub(r"\s", "", n)
        counts[key] = counts.get(key, 0) + 1
    # 報告では「直す」扱い。ひどいくり返し（8回以上。ランクルFJの記事は8回）だけ作り直し、5回以上は注意（warnings）
    rep = [k for k, v in counts.items() if v >= 8]
    if rep:
        add("A19", f"同じ数字のくり返し（「{rep[0]}」が{counts[rep[0]]}回）。本文では2〜3回までにする")
    # 15. 英語の画面名は記録だけ（warnings）。資料が英語の画面しか示さないとき、日本語の道順を作ると「作った手順」になる
    #     （quality.ui_step_problems が止める）。生成には「普通の言葉で書く」と伝えている（STYLE_PROMPT）
    # かんたん版の決まった見出し
    bad_easy = [h for h in headings(easy) if EASY_TEMPLATE_RE.match(h)]
    if bad_easy:
        add("A1", f"かんたん版に決まった型の見出し（「{bad_easy[0]}」）。中身の言葉にするか見出しを消す")
    return out


def problems(record: dict) -> list[str]:
    """公開前の門。1つでもあれば作り直し（指摘はそのまま作り直しのプロンプトに入る）"""
    return [msg for _, msg in coded_problems(record)]


def warnings(record: dict) -> list[str]:
    """記録だけ（作り直しにはしない）"""
    out: list[str] = []
    body = record.get("body") or ""
    counts: dict[str, int] = {}
    for n in NUMBER_RE.findall(TABLE_LINE_RE.sub("", body)):
        counts[re.sub(r"\s", "", n)] = counts.get(re.sub(r"\s", "", n), 0) + 1
    if rep := [k for k, v in counts.items() if 5 <= v < 8]:
        out.append(f"同じ数字が多い（「{rep[0]}」が{counts[rep[0]]}回）")
    fillers = FILLER_RE.findall(_no_quotes(body))
    if len(fillers) == 1:
        out.append(f"つなぎの文が1か所（「{fillers[0]}」）")
    if "元記事" in body:
        out.append("本文に「元記事」が1か所")
    if m := ENGLISH_UI_RE.search(f"{body}\n{record.get('easy') or ''}"):
        out.append(f"英語の画面名（「{m.group(0)}」）")
    if any(h.strip() == "まとめ" for h in headings(body)):
        out.append("「## まとめ」の見出し（次に起きること・読者がやることを書いているか）")
    key = news_type_of(record)
    if key:
        lo, hi = news_types.get(key).length
        n = _plain_len(body)
        if n > hi * 1.4:
            out.append(f"{news_types.get(key).label}の記事としては長い（{n}字 / 目安{hi}字まで）")
    return out


def repair_items(record: dict) -> list[dict]:
    """1文だけ直せば済む文体の問題（報告の自動ゲートの「直す」の項目）。quality.repair_language が安いモデルで1回直す。
    [{"field", "index", "sentence", "issues", "allow"}]。直らなかったものは公開前の門（problems）で作り直しになる"""
    out: list[dict] = []

    def add(field: str, sentence: str, issue: str, index: int | None = None, allow: set | None = None) -> None:
        for x in out:
            if x["field"] == field and x["sentence"] == sentence:
                x["issues"].append(issue)
                return
        out.append({"field": field, "index": index, "sentence": sentence, "issues": [issue], "allow": allow or set()})

    whole = f"{record.get('title') or ''}\n{record.get('lede') or ''}\n{record.get('body') or ''}"
    minor = bool(MINOR_CHANGE_RE.search(whole))
    for field in ("body", "easy", "lede"):
        text = record.get(field) or ""
        for line in text.split("\n"):
            if not line.strip() or line.lstrip().startswith(("#", "|")):
                continue
            for sentence in re.split(r"(?<=[。！？])", line):
                sentence = sentence.strip()
                if len(sentence) < 6:
                    continue
                nq = _no_quotes(sentence)
                if m := CHILDISH_RE.search(nq):
                    add(field, sentence, f"幼い言い方（「{m.group(0)}」）を、落ち着いた大人の言葉にする")
                if (m := PROMO_RE.search(nq)) and not ATTRIB_RE.search(sentence):
                    add(field, sentence, f"宣伝の言葉「{m.group(0)}」を地の文で断定している。「〇〇は…と説明しています」の形にするか、言葉を外す")
                if m := FILLER_RE.search(nq):
                    add(field, sentence, f"中身のないつなぎ（「{m.group(0)}」）を外し、事実だけの文にする")
                if minor and NEW_MODEL_RE.search(sentence):
                    add(field, sentence, "一部改良の話なので「新型」と書かない（「改良」「新しい」などにする）")
                if field != "lede" and QA_LINE_RE.search(sentence):
                    add(field, sentence, "一問一答の口調（「〜の？」）を、説明の文にする")
    lede = record.get("lede") or ""
    first = next(iter(_sentences(lede)), "")
    if first and any("〇〇を発表しました" in p for p in problems({"lede": lede})):
        nums = set(re.findall(r"\d+(?:[.,]\d+)*", lede))
        add("lede", first, "リードの1文目が「〇〇を発表しました」で終わっている。リードの中の事実（数字・中身）を使い、何が新しいかを言い切る1文にする",
            allow=nums)
    return out[:20]


def check(record: dict) -> dict:
    """直す → 門 → 注意 をまとめて（試しの道具・点検用）"""
    fixed = autofix(record)
    return {"fixed": fixed, "problems": problems(record), "warnings": warnings(record)}


# --------------------------------------------------------------------------- #
# 試し
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--slug", action="append", default=[])
    parser.add_argument("--recent", type=int, default=0)
    args = parser.parse_args()
    paths = [ARTICLES_DIR / f"{s}.json" for s in args.slug]
    if args.recent:
        rows = []
        for p in ARTICLES_DIR.glob("*.json"):
            a = json.loads(p.read_text("utf-8"))
            if a.get("sourceType") == "web" and a.get("type") != "roundup":
                rows.append((a.get("generatedAt", ""), p))
        paths += [p for _, p in sorted(rows, reverse=True)[: args.recent]]
    failed = 0
    tally: dict[str, int] = {}
    for p in paths:
        a = json.loads(p.read_text("utf-8"))
        res = check(a)
        if res["problems"]:
            failed += 1
        for prob in res["problems"]:
            k = re.sub(r"（.*", "", prob)[:24]
            tally[k] = tally.get(k, 0) + 1
        if args.slug:
            print(f"== {p.stem}\n  直した: {res['fixed']}\n  門: " + "\n      ".join(res["problems"] or ["合格"]) + f"\n  注意: {res['warnings']}")
    if args.recent:
        print(f"門に落ちる記事: {failed}/{len(paths)}")
        for k, v in sorted(tally.items(), key=lambda x: -x[1]):
            print(f"  {v:4} {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
