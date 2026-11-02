#!/usr/bin/env python3
"""画面の操作手順（メニューの道順・ボタン名・番号つきの手順）が、根拠のある資料に書かれているかを確かめる（決定的。LLM なし）。

LLM は「設定画面の支払いページから申し込む」のような、それらしい手順を作りがち。サイソクは自分で試していないので、
画面の場所・メニュー名・ボタン名・手順は、元記事・確かな事実の束（取りに行った公式ページ・当サイトのガイド）に
書かれているものだけを書く。

- claims(): 文章の中の「画面の操作の主張」を取り出す
    道順（「設定 > 使用状況」「Settings › Usage」「A → B → C」）・「XX画面のYYページ」・
    「〇〇」ボタン／タブ／メニュー・英語の画面の文字（"Add source" をクリック）・手順の行の「XX画面」「XXページ」
- supported(): 資料（元記事＋束）の中に同じ道順・同じ名前があるか。英語の資料の「Settings > Usage」は
  小さな対訳表（設定＝Settings など）で「設定 > 使用状況」と同じとみなす。道順は順番もそろっていること
- rewrite(): 根拠の無い手順の行・文を「公式ヘルプの手順を確認してください（リンク）」に置き換える（web_articles.build_web_record）
- problems(): 置き換えたあとも残っている根拠の無い手順（品質ゲート。quality.structural_problems）
"""

from __future__ import annotations

import re
import unicodedata

SEP = r"\s?(?:>|＞|→|›|»|＞)\s?"
COMP = r"[「『\"“]?[^\s「」『』\"“”<>＞>→›»、。,()（）|]{1,24}[」』\"”]?"
PATH_RE = re.compile(rf"{COMP}(?:{SEP}{COMP}){{1,5}}")
UI_SUFFIX = r"(?:画面|ページ|メニュー|タブ|ボタン|欄|アイコン|項目|リンク|ダイアログ|ウィンドウ|パネル)"
IMPLICIT_RE = re.compile(rf"([一-龠々ァ-ヶーA-Za-z]{{1,12}})(?:画面|メニュー|タブ|ページ)の[「『\"]?([一-龠々ァ-ヶーぁ-んA-Za-z ]{{1,16}}?)[」』\"]?{UI_SUFFIX}")
LABEL_RE = re.compile(rf"[「『\"“]([^「」『』\"“”\n]{{1,30}})[」』\"”]\s?(?:{UI_SUFFIX}|を(?:クリック|タップ|押|選|オン|オフ))")
EN_QUOTED_RE = re.compile(r"[「『\"“]([A-Z][A-Za-z0-9&'+\- ]{1,30})[」』\"”]")
UI_NOUN_RE = re.compile(rf"([一-龠々ァ-ヶーA-Za-z]{{2,12}}){UI_SUFFIX}")
OP_VERB_RE = re.compile(r"アクセス|クリック|タップ|押し|押す|押して|選択|選ん|選び|選ぶ|開き|開く|開いて|進み|進む|進んで|切り替え|オンに|オフに|ログイン|サインイン|入力|ドラッグ|有効に|無効に|設定する|設定して")
ACCOUNT_UI_RE = re.compile(r"設定|使用状況|利用状況|請求|支払|アカウント|プロフィール|ダッシュボード|管理|マイページ|サブスクリプション|契約")
STEP_LINE_RE = re.compile(r"^\s*(?:\d+[.)．、]|[-*・])\s+")
NUMBERED_RE = re.compile(r"^\s*\d+[.)．、]\s+")
STEP_HEADING_RE = re.compile(r"^#{2,3}\s.*(?:日本で(?:も)?使える|使い方|手順|始め方|申し込|確かめ方|設定方法|やり方|使ってみる|試す|今日やること)")
# 「公式サイトで確かめる」のような一般的な行動（場所を作っていない）
GENERIC_RE = re.compile(r"公式(?:サイト|ページ|ヘルプ|の案内|発表|ブログ)|発表元|窓口|案内ページで確|最新の情報を確")
# 画面の言葉の対訳（英語の資料の道順と日本語の記事の道順を同じとみなす）
GLOSSARY = {
    "settings": ["設定", "せってい"], "usage": ["使用状況", "利用状況", "使用量", "利用量"], "billing": ["請求", "支払い", "お支払い", "支払", "課金"],
    "plan": ["プラン"], "plans": ["プラン"], "account": ["アカウント"], "profile": ["プロフィール"], "subscription": ["サブスクリプション", "契約"],
    "upgrade": ["アップグレード"], "privacy": ["プライバシー"], "security": ["セキュリティ"], "general": ["一般"],
    "features": ["機能"], "capabilities": ["機能"], "connectors": ["コネクタ", "コネクター"], "integrations": ["連携", "統合"],
    "help": ["ヘルプ"], "add": ["追加"], "save": ["保存"], "submit": ["送信"], "cancel": ["キャンセル", "解約"], "home": ["ホーム"],
    "update": ["更新", "アップデート"], "windows update": ["windows update"], "personalization": ["個人用設定"], "system": ["システム"],
    "apps": ["アプリ"], "allow": ["許可"], "approve": ["承認", "許可"], "deny": ["拒否"], "continue": ["続行", "次へ"], "notifications": ["通知"], "language": ["言語"], "data controls": ["データ コントロール", "データコントロール"],
}
JA_TO_KEY = {ja: key for key, words in GLOSSARY.items() for ja in words}
STRIP_SUFFIX_RE = re.compile(rf"{UI_SUFFIX}$")


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def canon(component: str) -> str:
    """道順の1つの段を比べられる形に（かっこ・「画面」などを外し、対訳で英語の言葉にそろえる）"""
    c = re.sub(r"[「」『』\"“”'\s]", "", _nfkc(component)).strip().rstrip(".。:：,、!！?？")
    # 「使用状況で確認」のような、段のあとに続いた言葉（で・を・に・から…）は外す
    c = re.sub(r"(?<=..)(?:で|を|に|から|へ|まで|と|が|は)[ぁ-んァ-ヶ一-龠々ー]*$", "", c)
    c = STRIP_SUFFIX_RE.sub("", c)
    low = c.lower()
    if low in GLOSSARY:
        return low
    return JA_TO_KEY.get(c, low)


def _is_flow(parts: list[str]) -> bool:
    """「作る→直す→質問する」のような流れの説明（どの段もひらがなで終わる＝動詞）。画面の道順ではない"""
    clean = [re.sub(r"[「」『』\"“”\s]", "", p) for p in parts]
    return all(c and re.search(r"[ぁ-ん]$", c) for c in clean)


def _is_numberish(component: str) -> bool:
    return bool(re.fullmatch(r"[\d,.\s約万億円ドル%％件口座人倍年月日〜~+＋−-]+", re.sub(r"[「」『』\"“”]", "", component)))


def _paths(text: str) -> list[list[str]]:
    out = []
    for m in PATH_RE.finditer(_nfkc(text)):
        parts = [p for p in re.split(SEP, m.group(0)) if p.strip()]
        if len(parts) < 2 or any(_is_numberish(p) for p in parts) or _is_flow(parts):
            continue
        out.append([canon(p) for p in parts])
    return out


class Allowed:
    """根拠になる資料（元記事＋束）の道順と名前"""

    def __init__(self, text: str):
        self.raw = _nfkc(text or "").lower()
        self.flat = re.sub(r"\s", "", self.raw)
        self.paths = _paths(text)
        labels = {c for p in self.paths for c in p}
        labels |= {canon(m) for m in re.findall(r"[「『\"“]([^「」『』\"“”\n]{1,30})[」』\"”]", _nfkc(text or ""))}
        self.labels = labels

    def has_label(self, component: str) -> bool:
        key = canon(component)
        if key in self.labels:
            return True
        plain = re.sub(r"\s", "", STRIP_SUFFIX_RE.sub("", re.sub(r"[「」『』\"“”']", "", _nfkc(component)))).lower()
        if len(plain) >= 2 and plain in self.flat:
            return True
        # 英語の資料に、対訳の英語の言葉がある
        return key in GLOSSARY and re.search(rf"\b{re.escape(key)}(?:s|d|ed|ing)?\b", self.raw) is not None

    def has_path(self, seq: list[str]) -> bool:
        n = len(seq)
        return any(p[i:i + n] == seq for p in self.paths for i in range(len(p) - n + 1))


def claims(line: str, step: bool = False) -> list[tuple[str, list[str]]]:
    """1行の中の画面の操作の主張。[(表示用の文字, 道順の段のリスト)]。段が1つなら名前だけの主張"""
    text = _nfkc(line)
    out = []
    for m in PATH_RE.finditer(text):
        parts = [p for p in re.split(SEP, m.group(0)) if p.strip()]
        if len(parts) >= 2 and not any(_is_numberish(p) for p in parts) and not _is_flow(parts):
            out.append((m.group(0), parts))
    for m in IMPLICIT_RE.finditer(text):
        out.append((m.group(0), [m.group(1), m.group(2)]))
    for m in LABEL_RE.finditer(text):
        out.append((m.group(0), [m.group(1)]))
    if OP_VERB_RE.search(text):
        for m in EN_QUOTED_RE.finditer(text):
            out.append((m.group(0), [m.group(1)]))
    if OP_VERB_RE.search(text):
        # 手順の行は「XX画面」「XXページ」すべて、地の文は設定・請求・アカウントなどの管理画面の名前だけ（機能名の「作業画面」などは見ない）
        for m in UI_NOUN_RE.finditer(text):
            if GENERIC_RE.search(m.group(0)) or re.match(r"(?:公式|ウェブ|Web|サイト)", m.group(1)):
                continue
            if step or ACCOUNT_UI_RE.search(m.group(1)):
                out.append((m.group(0), [m.group(1)]))
    return out


def unsupported(line: str, allowed: Allowed, step: bool = False) -> list[str]:
    bad = []
    for shown, parts in claims(line, step):
        if len(parts) >= 2:
            ok = allowed.has_path([canon(p) for p in parts])
        else:
            ok = allowed.has_label(parts[0])
        if not ok:
            bad.append(shown.strip())
    return bad


def _step_lines(markdown: str):
    """(行番号, 行, 手順の行か)。手順の行 = 番号つきの行、または手順の章（日本で使える？・使い方など）の箇条書き"""
    in_steps = False
    for i, line in enumerate((markdown or "").split("\n")):
        if line.startswith("#"):
            in_steps = bool(STEP_HEADING_RE.match(line))
            yield i, line, False
            continue
        step = bool(NUMBERED_RE.match(line)) or (in_steps and bool(STEP_LINE_RE.match(line)))
        yield i, line, step


def help_line(url: str, name: str) -> str:
    return f"公式ヘルプの手順を確認してください（[{name}]({url})）。" if url else "公式ヘルプの手順を確認してください。"


SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？])")


def rewrite(markdown: str, allowed: Allowed, url: str = "", name: str = "公式ヘルプ") -> tuple[str, list[str]]:
    """根拠の無い手順の行・文を「公式ヘルプの手順を確認してください（リンク）」に置き換える。(本文, 置き換えた主張)"""
    lines = (markdown or "").split("\n")
    replaced: list[str] = []
    out: list[str] = []
    for i, line, step in _step_lines(markdown):
        if line.startswith("#") or line.lstrip().startswith("|"):
            out.append(line)
            continue
        bad = unsupported(line, allowed, step)
        if not bad:
            out.append(line)
            continue
        replaced += bad
        if step:
            prefix = STEP_LINE_RE.match(line).group(0) if STEP_LINE_RE.match(line) else "- "
            new = prefix + help_line(url, name)
            # 続けて同じ置き換えになる手順は1行にまとめる
            if out and out[-1].endswith(help_line(url, name)):
                continue
            out.append(new)
        else:
            sentences = [s for s in SENTENCE_SPLIT_RE.split(line) if s]
            kept = [s if not unsupported(s, allowed, False) else help_line(url, name) for s in sentences]
            dedup = [s for j, s in enumerate(kept) if not (j and s == kept[j - 1] == help_line(url, name))]
            out.append("".join(dedup))
    del lines
    return "\n".join(out), replaced


def record_problems(record: dict, allowed: Allowed) -> list[str]:
    """記事（本文・かんたん版・FAQ・今日やること・仕事とお金の欄）に残る根拠の無い画面の操作の主張"""
    bad: list[str] = []
    for key in ("body", "easy"):
        for _, line, step in _step_lines(record.get(key) or ""):
            if not line.startswith("#") and not line.lstrip().startswith("|"):
                bad += unsupported(line, allowed, step)
    for row in record.get("faq") or []:
        bad += unsupported(str(row.get("a") or ""), allowed, False)
    for action in record.get("todayActions") or []:
        bad += unsupported(action, allowed, True)
    for point in (record.get("workMoney") or {}).get("points") or []:
        bad += unsupported(point, allowed, False)
    seen, out = set(), []
    for b in bad:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out
