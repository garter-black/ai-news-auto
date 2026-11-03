"""
見出し（title・seoTitle）の書き方を、生成のあとに機械でそろえる（LLM は使わない。事実は変えない）。

main.write_article（記事を書き出す1か所）から呼ぶ。過去7日分は
    python3 scripts/headline_normalize.py --days 7 --write
で直す（--write を付けないと、直す前後を表示するだけ）。

そろえ方（2026-11-03 サイト点検「見出しの書き方がそろっていない」）
1. かっこ: 製品名・サービス名は「」。『』は作品名（映画・ゲーム・アニメ・本・曲など）とはっきり分かるときだけ残す。
   英語の引用符 "…" “…” も「」に。「」の中の『』（かぎかっこの中の引用）はそのまま。
2. 区切り: 見出しの前半と後半の区切りは全角の空白1つ。「 – 」「 ― 」「 — 」「 - 」「｜」、
   日本語のすぐ後ろの半角空白、文の途中の「。」を全角の空白に。末尾の「。」は取る。
   英字どうしの間の半角空白（「Gemini 4 Argon」「Apple Watch」）とかっこの中は変えない。
3. 会社名: 海外の主な会社は英字（Microsoft・Google・OpenAI・Apple・Amazon・NVIDIA・Anthropic）。
   日本の会社名は正式な表記のまま（表に入れない）。前後がカタカナのとき（「グーグルマップ」など）は変えない。
   全角の英数字は半角に。
4. 年: 西暦が基本。見出しの「令和N年」は「YYYY年」に直す。ただし元の発表の題名（videoTitle）が令和を使っているときは
   見出しの令和を残す。どちらの場合も、本文で最初に出てくる「令和N年」の後ろに「（YYYY年）」を足す（見出しには足さない）。
5. 長さ: ここでは短くしない。40字を超えると品質ゲート（quality.title_warnings）が警告し、
   サイトの一覧・カードは前半だけを出す（lib/headline.ts。記事ページの見出しと検索用の題名は全文）。
6. 書き方の型（2026-11-03 オーナー指摘「見出しが不自然で幼い。恥ずかしい」）:
   ITmedia NEWS・日経クロステック・Impress Watch のような落ち着いた報道の見出しにする（TITLE_STYLE_PROMPT。
   記事を書くプロンプト・過去の見出しの書き直し scripts/rewrite_titles.py が同じ文を使う）。
   - よく知られた会社に説明を付けた「ChatGPTのOpenAIが」「AI半導体のNVIDIAが」は、説明を機械で外す（strip_known_descriptors）。
   - それでも残る型（「Google開発AI」「AI秘書」「勝手な」「人手なしで」「〜が1本に」「Xが公開、…とは」など）は
     title_problems が返す。main.write_article は見出しだけを書き直し（rewrite_titles.fix_one）、品質ゲートは警告に出す。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = ROOT / "content" / "articles"

TITLE_WARN_LEN = 40
TITLE_HARD_LEN = 56  # これを超える見出しは書き方の不合格（title_problems）。目安は本見出し28〜40字＋短い補足

# --------------------------------------------------------------------------- #
# 1. かっこ
# --------------------------------------------------------------------------- #

# 『』を残す「作品」の目印（見出しの中の言葉）。エンタメのジャンルの記事も作品として扱う
WORK_RE = re.compile(
    r"映画|劇場版|アニメ|ゲーム|漫画|マンガ|コミック|小説|ドラマ|楽曲|新曲|アルバム|シングル|作品|書籍|新刊|絵本|写真集|"
    r"舞台|ミュージカル|番組|主演|声優|主題歌|続編|最新作|シリーズ|PS5|PS4|Switch|Xbox|Steam|配信開始日|公開日|上映"
)


def _depth_map(text: str) -> list[int]:
    """各文字が「」の中か（入れ子の深さ）。『』の判定で「」の中の引用を残すため"""
    depth, out = 0, []
    for ch in text:
        if ch == "「":
            depth += 1
        out.append(depth)
        if ch == "」" and depth > 0:
            depth -= 1
    return out


def unify_brackets(title: str, genre_slug: str = "") -> str:
    # 英語の引用符（“…” と "…"）は「」に。中が空・入れ子のものは触らない
    title = re.sub(r"“([^“”]+)”", r"「\1」", title)
    if title.count('"') == 2:
        title = re.sub(r'"([^"]+)"', r"「\1」", title)
    if "『" not in title:
        return title
    if genre_slug == "entertainment" or WORK_RE.search(title):
        return title
    depth = _depth_map(title)
    chars = list(title)
    stack: list[int] = []
    for i, ch in enumerate(chars):
        if ch == "『" and depth[i] == 0:
            stack.append(i)
        elif ch == "』" and stack and depth[i] == 0:
            start = stack.pop()
            chars[start], chars[i] = "「", "」"
    return "".join(chars)


# --------------------------------------------------------------------------- #
# 2. 区切り
# --------------------------------------------------------------------------- #

def _ascii(ch: str) -> bool:
    return ord(ch) < 0x80


def _outside_brackets(text: str) -> list[bool]:
    """各文字が「」『』（）の外か"""
    depth, out = 0, []
    for ch in text:
        if ch in "「『（(":
            out.append(depth == 0)
            depth += 1
            continue
        if ch in "」』）)" and depth > 0:
            depth -= 1
            out.append(depth == 0)
            continue
        out.append(depth == 0)
    return out


def unify_separators(title: str) -> str:
    t = title.strip()
    # 末尾の句点は取る
    t = re.sub(r"[。．]+$", "", t)
    # ダッシュ・縦棒の区切り（両側に空白があるもの、全角の縦棒）。「GPT-5」「Wi-Fi」の中のハイフンは変えない
    t = re.sub(r"\s+[-–—―]+\s+", "　", t)
    t = re.sub(r"\s*[｜|]\s*", "　", t)
    # 文の途中の句点（「…へ。クラウドと…」）
    t = re.sub(r"。\s*(?=\S)", "　", t)
    # 日本語のすぐ後ろの半角空白（かっこの外だけ）
    outside = _outside_brackets(t)
    chars = list(t)
    for i, ch in enumerate(chars):
        if ch == " " and 0 < i < len(chars) - 1 and outside[i] and not _ascii(chars[i - 1]):
            chars[i] = "　"
    t = "".join(chars)
    # 句読点・かっこの隣の空白は取る。空白の連続は1つに
    t = re.sub(r"[ 　]*([、，」』）])", r"\1", t)
    t = re.sub(r"([、，「『（])[ 　]+", r"\1", t)
    t = re.sub(r"　[ 　]+|[ ]+　", "　", t)
    t = re.sub(r" {2,}", " ", t)
    return t.strip(" 　")


# --------------------------------------------------------------------------- #
# 3. 会社名・英数字
# --------------------------------------------------------------------------- #

# 左が見出しで使う形。右がそろえる前の書き方（カタカナ・表記ゆれ）
COMPANY_FORMS: list[tuple[str, list[str]]] = [
    ("Microsoft", ["マイクロソフト"]),
    ("Google", ["グーグル"]),
    ("OpenAI", ["オープンAI", "オープンエーアイ", "Open AI"]),
    ("Apple", ["アップル"]),
    ("Amazon", ["アマゾン"]),
    ("NVIDIA", ["エヌビディア", "Nvidia", "nVidia"]),
    ("Anthropic", ["アンソロピック", "アンスロピック"]),
    ("ChatGPT", ["Chat GPT", "チャットGPT"]),
    ("YouTube", ["Youtube", "ユーチューブ"]),
]

KATAKANA = r"ァ-ヴー・"
# 地名・ふつうの言葉として使われる形（変えない）
COMPANY_EXCEPT = re.compile(r"アマゾン(川|流域|熱帯|の森)|アップル(パイ|ジュース|サイダー)")


def _fullwidth_alnum_to_half(text: str) -> str:
    text = re.sub(r"[Ａ-Ｚａ-ｚ０-９]", lambda m: unicodedata.normalize("NFKC", m.group(0)), text)
    # 英数字にはさまれた全角のハイフン（「GPT－6」）は半角に
    return re.sub(r"(?<=[A-Za-z0-9])－(?=[A-Za-z0-9])", "-", text)


def unify_companies(title: str) -> str:
    t = _fullwidth_alnum_to_half(title)
    for canonical, variants in COMPANY_FORMS:
        for variant in variants:
            if variant.isascii():
                t = re.sub(rf"(?<![A-Za-z]){re.escape(variant)}(?![A-Za-z])", canonical, t)
                continue
            for m in reversed(list(re.finditer(rf"(?<![{KATAKANA}]){re.escape(variant)}(?![{KATAKANA}])", t))):
                if COMPANY_EXCEPT.match(t, m.start()):
                    continue
                t = t[: m.start()] + canonical + t[m.end():]
    return t


# --------------------------------------------------------------------------- #
# 4. 年
# --------------------------------------------------------------------------- #

REIWA_RE = re.compile(r"令和(\d{1,2}|元)年")


def _reiwa_to_year(n: str) -> int:
    return 2018 + (1 if n == "元" else int(n))


def reiwa_to_seireki(title: str) -> str:
    return REIWA_RE.sub(lambda m: f"{_reiwa_to_year(m.group(1))}年", title)


def add_seireki_in_body(body: str) -> str:
    """本文で最初に出てくる「令和N年」「令和N年度」（それぞれ1回）の後ろに「（YYYY年）」「（YYYY年度）」を足す。
    すでに西暦が添えてあれば何もしない"""
    seen: set[tuple[str, str]] = set()

    def repl(m: re.Match) -> str:
        key = (m.group(1), m.group(2) or "")
        if key in seen:
            return m.group(0)
        seen.add(key)
        if re.match(r"\s*[（(]\s*\d{4}", body[m.end(): m.end() + 8]):
            return m.group(0)
        return f"{m.group(0)}（{_reiwa_to_year(m.group(1))}年{m.group(2) or ''}）"

    return re.sub(r"令和(\d{1,2}|元)年(度)?", repl, body)


# --------------------------------------------------------------------------- #
# まとめ
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# 6. 書き方の型（報道の見出し。説明の押し付け・幼い言い方をしない）
# --------------------------------------------------------------------------- #

# 記事を書くプロンプト（main.py・web_articles.py）と見出しの書き直し（rewrite_titles.py）が使う、見出しの書き方。
# テンプレートに埋め込むので { } は使わない
TITLE_STYLE_PROMPT = """報道記事の見出し。ITmedia NEWS・日経クロステック・Impress Watch のような、落ち着いた自然な日本語にする（最重要）。
  - 長さ: 本見出しは全角28〜40字が目安（最大48字）。補足が要るときだけ、全角の空白1つのあとに短く続ける。
  - 中身: 「今回なにが新しいか」を、数字か固有名詞と一緒に入れる。前からある事実・以前の発表の内容（背景）で見出しを作らない。
    発表文の題名を言い直さない。一部改良を「新型」と書かない。前からあるものを「新たに」「初」と書かない。
  - 形: 主語（会社・団体）＋何をしたか（発表・公開・提供開始・値下げ・買収・提携など）＋要点（数字・対象・影響）。
    「OpenAI、新AIモデル「GPT-6.1 Terra」を公開」「NVIDIAがAI学習の自動復旧ツールを公開」のように、主語のすぐ後ろに「、」か「が」を置く。
  - よく知られた会社（OpenAI・Google・Microsoft・NVIDIA・Apple・Anthropic・Amazon・AWS・Meta・サムスン・ソニー・トヨタ・ホンダ・ソフトバンク など）には説明を付けない。
    「ChatGPTのOpenAI」「ClaudeのAnthropic」「AI半導体のNVIDIA」「Google開発AI」「MicrosoftのAI秘書」のような言い方は禁止。
  - あまり知られていない会社だけ、短い説明を自然に添えてよい（例「米新興のCresta」「セキュリティ企業のSnyk」「豪Atlassian」「オランダ通信大手KPN」）。
  - 製品名・サービス名は「」で囲む。種類が分からない製品だけ短く添える（例「AIコーディングツール「Antigravity」」）。
  - 子どもっぽい言い方・話し言葉は使わない。例: 勝手な／お任せ／〜が1本に／人手なしで／〜しちゃう／すごい／ラクに／AI秘書／AI助手。
    報道の言葉に言い換える（無断の・意図しない／自動で／1つのアプリに統合／自動で再開／AIアシスタント）。
  - 記号: かっこ（「」）のすぐ前後に「、」を置かない（悪い例「…「セキュアSAMBA」、Mac用アプリ…」「OpenAI、「Codex」…」）。
    製品名を主語にして読点で区切るより、会社名を主語にする。区切りは全角の空白1つだけ（空白を重ねない）。末尾に「、」「。」を付けない。
    日本語の中の記号は全角（，: ( ) ? ! の半角を混ぜない）。
  - 「Xが公開、…「Y」とは」のような倒置や、「〜とは」で終わる見出しにしない（解説記事で必要なときだけ「〜とは」を使う）。
  - 専門用語は、ニュースを読む社会人に通じる範囲で使ってよい（API・AIエージェント・生成AI など）。分かりにくい語は短く言い換える。
  - 事実は元の情報のとおり（社名・製品名・数字・日付を変えない・足さない）。誇張・煽り語（衝撃／ついに／ヤバい／必見／知らないと損）と感嘆符は使わない。元記事の題名の直訳にしない。
  良い例:「OpenAI、新AIモデル「GPT-6.1 Terra」を公開　料金は従来の半分に」
  良い例:「NVIDIA、AI学習の自動復旧ツールを公開　障害で止まった学習を自動で再開」
  良い例:「Canva、iPad版「Affinity」を公開　描画・写真編集・レイアウトを1つのアプリに」
  良い例:「kubell、法人向けストレージ「セキュアSAMBA」にMac用アプリ　Finderから直接編集」
  悪い例:「ChatGPTのOpenAIが新AIモデル…」（よく知られた会社への説明の押し付け）
  悪い例:「…止まったAI開発を人手なしで再開」「…AIの勝手な課金操作を防ぐ」（幼い言い方）"""

# 説明を付けない会社（見出しで「〇〇の」を前に置かない）。長い名前を先に並べる
WELL_KNOWN_COMPANIES = sorted({
    "OpenAI", "Google", "Microsoft", "NVIDIA", "Apple", "Anthropic", "Amazon", "AWS", "Meta", "Samsung", "サムスン",
    "サムスン電子", "Sony", "ソニー", "ソニーグループ", "Toyota", "トヨタ", "トヨタ自動車", "Honda", "ホンダ", "本田技研工業",
    "日産", "日産自動車", "パナソニック", "任天堂", "ソフトバンク", "ソフトバンクグループ", "楽天", "楽天グループ", "NTT",
    "NTTドコモ", "KDDI", "Intel", "AMD", "Qualcomm", "IBM", "Oracle", "Salesforce", "Adobe", "Netflix", "Tesla", "テスラ",
    "xAI", "DeepSeek", "YouTube", "LINEヤフー", "Uber", "Spotify", "Arm", "TSMC", "Dell", "Canva",
}, key=len, reverse=True)
_COMPANY_ALT = "|".join(re.escape(c) for c in WELL_KNOWN_COMPANIES)
# 「ChatGPTのOpenAIが」「AI半導体のNVIDIA、」。説明は見出しの頭か区切り（空白・読点）の直後から
DESCRIPTOR_RE = re.compile(
    rf"(^|[　、 ])([^\s　、。「」『』（）()]{{1,14}}?)の({_COMPANY_ALT})(?![A-Za-z0-9ァ-ヴー])(?=[がはもとの、　]|$)"
)
# 「Google開発AI」「Microsoft製AI」
MAKER_AI_RE = re.compile(rf"({_COMPANY_ALT})(?:開発|製)の?AI")
# 幼い言い方・話し言葉・煽り（かっこの外だけで見る。製品名「交換できるくん」などは対象外）
CHILDISH_WORDS = (
    "勝手な", "勝手に", "お任せ", "おまかせ", "が1本に", "が一本に", "人手なしで", "ちゃう", "じゃう", "すごい", "スゴい",
    "ラクに", "らくらく", "楽ちん", "AI秘書", "AI助手", "ヤバ", "衝撃", "ついに", "必見", "知らないと損", "！", "!",
)
# 「NVIDIAが公開、…」の倒置
INVERTED_RE = re.compile(r"^[^　、]{1,24}が(?:公開|発表|開始)、")


def _outside_text(title: str) -> str:
    """「」『』の中身を除いた見出し（製品名の中の言葉で引っかけない）"""
    return re.sub(r"「[^」]*」|『[^』]*』", "「」", title)


# 説明ではなく関係を表す言葉（「競合のGoogle」「傘下のAWS」は意味が変わるので外さない）
RELATION_RE = re.compile(r"(競合|傘下|親会社|子会社|同業|ライバル|提携先|顧客|取引先|株主|相手|大株主|元|前|旧|同じく)$")


def _descriptor_matches(text: str) -> list[re.Match]:
    return [m for m in DESCRIPTOR_RE.finditer(text) if not RELATION_RE.search(m.group(2))]


def strip_known_descriptors(title: str) -> str:
    """よく知られた会社の前の説明（「ChatGPTの」「AI半導体の」）を外す。「ChatGPTのOpenAIが」→「OpenAIが」"""
    for m in reversed(_descriptor_matches(title)):
        title = title[: m.start()] + m.group(1) + m.group(3) + title[m.end():]
    return title


# 見出しの記号の不自然な並び（2026-11-03 オーナー指摘「」、」は大人の文として不自然）。(正規表現, 説明)
PUNCT_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[」』）]、"), "かっこの直後に「、」"),
    (re.compile(r"、[「『（]"), "かっこの直前に「、」"),
    (re.compile(r"[「『（]、|、[」』）]"), "かっこの内側に「、」"),
    (re.compile(r"[ 　]{2,}"), "空白が重なっている"),
    (re.compile(r"[、，,。．]$"), "末尾に句読点"),
    (re.compile(r"^[、。　 ]"), "先頭に句読点・空白"),
    (re.compile(r"(?<=[^\x00-\x7f]),|,(?=[^\x00-\x7f\d])"), "半角の「,」"),
    (re.compile(r"(?<=[^\x00-\x7f]):|:(?=[^\x00-\x7f])"), "半角の「:」"),
    (re.compile(r"(?<=[^\x00-\x7f])[?!]|(?<=[^\x00-\x7f])\(|\)(?=[^\x00-\x7f])"), "半角の記号（? ! ( )）"),
    (re.compile(r"、、|。。|、。|。、"), "句読点が重なっている"),
]


def punctuation_problems(title: str) -> list[str]:
    return [f"記号: {label}" for pattern, label in PUNCT_RULES if pattern.search(title)]


def title_problems(title: str) -> list[str]:
    """見出しの書き方の不合格の理由（空なら合格）。事実は見ない"""
    problems: list[str] = []
    if m := next(iter(_descriptor_matches(title)), None):
        problems.append(f"よく知られた会社に説明を付けている（「{m.group(2)}の{m.group(3)}」）")
    if m := MAKER_AI_RE.search(title):
        problems.append(f"不自然な説明（「{m.group(0)}」）")
    outside = _outside_text(title)
    words = [w for w in CHILDISH_WORDS if w in outside]
    if words:
        problems.append(f"幼い・話し言葉の言い方（{'・'.join(words)}）")
    problems += punctuation_problems(title)
    if INVERTED_RE.search(outside):
        problems.append("「〇〇が公開、…」の倒置")
    if len(title) > TITLE_HARD_LEN:
        problems.append(f"長すぎる（{len(title)}字。最大{TITLE_HARD_LEN}字）")
    return problems


def normalize_title(title: str, record: dict | None = None) -> str:
    """見出し1本をそろえる。record は記事（ジャンル・元の題名を見る）"""
    record = record or {}
    if not title:
        return title
    t = unify_companies(title)
    t = strip_known_descriptors(t)
    t = unify_brackets(t, str(record.get("genreSlug") or ""))
    t = unify_separators(t)
    if not REIWA_RE.search(str(record.get("videoTitle") or "")):
        t = reiwa_to_seireki(t)
    return t


def apply(record: dict) -> list[str]:
    """記事の title・seoTitle（と本文の令和の西暦）をそろえる。変えた項目の説明を返す（record をその場で書き換える）"""
    changes: list[str] = []
    for field in ("title", "seoTitle"):
        before = str(record.get(field) or "")
        after = normalize_title(before, record)
        if after != before:
            record[field] = after
            changes.append(f"{field}: {before} → {after}")
    # リード・説明の頭の「ChatGPTのOpenAIが」も外す（見出しと同じ。本文は変えない）
    for field in ("lede", "description"):
        text = record.get(field)
        if isinstance(text, str):
            after = strip_known_descriptors(text)
            if after != text:
                record[field] = after
                changes.append(f"{field}: よく知られた会社の説明を外した")
    for field in ("body", "easy"):
        text = record.get(field)
        if isinstance(text, str) and REIWA_RE.search(text):
            after = add_seireki_in_body(text)
            if after != text:
                record[field] = after
                changes.append(f"{field}: 令和に西暦を添えた")
    return changes


def title_warnings(record: dict) -> list[str]:
    """品質ゲートの警告（不合格にはしない）。そろえたあとの長さで見る"""
    title = normalize_title(str(record.get("title") or ""), record)
    warnings = [f"見出しの書き方: {p}" for p in title_problems(title) if not p.startswith("長すぎる")]
    if len(title) > TITLE_WARN_LEN:
        warnings.append(f"見出しが{len(title)}字（目安{TITLE_WARN_LEN}字以内）。一覧・カードでは前半だけを出す")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="見出しの書き方をそろえる（過去の記事）")
    parser.add_argument("--days", type=float, default=7, help="何日前までの記事を直すか（掲載日時で判定）")
    parser.add_argument("--write", action="store_true", help="ファイルを書き換える（無ければ表示だけ）")
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    changed = 0
    for path in sorted(ARTICLES.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stamp = record.get("generatedAt") or record.get("publishedAt") or ""
        try:
            when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when < since:
            continue
        changes = apply(record)
        if not changes:
            continue
        changed += 1
        print(f"[{path.stem}]")
        for line in changes:
            print(f"  {line}")
        if args.write:
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{'直した' if args.write else '直す候補'}: {changed}本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
