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

def normalize_title(title: str, record: dict | None = None) -> str:
    """見出し1本をそろえる。record は記事（ジャンル・元の題名を見る）"""
    record = record or {}
    if not title:
        return title
    t = unify_companies(title)
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
    if len(title) > TITLE_WARN_LEN:
        return [f"見出しが{len(title)}字（目安{TITLE_WARN_LEN}字以内）。一覧・カードでは前半だけを出す"]
    return []


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
