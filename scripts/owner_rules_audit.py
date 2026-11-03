#!/usr/bin/env python3
"""オーナーのルールの毎日の点検（読むだけ。何も投稿・変更しない）。.github/workflows/owner_rules_audit.yml

オーナーが直したこと（docs/owner_rules.md の台帳）が本番で守られているかを、毎日1回まとめて確かめる。
生成・公開・投稿の門番（quality.py・headline_normalize.py・sns_rules.py など）をすり抜けたものや、
門番ができる前に出たものを見つけるための「見張り」。

  python3 scripts/owner_rules_audit.py                 # 本番を見て、違反を表示（終了コードは常に 0）
  python3 scripts/owner_rules_audit.py --issue         # GitHub の issue（ラベル owner-rule-violation）に書く／無ければ閉じる
  python3 scripts/owner_rules_audit.py --no-network    # 記事の JSON だけで確かめる（本番を開かない）
  python3 scripts/owner_rules_audit.py --comment-log ~/.claude/scheduled-tasks/saisoku-threads-replies/history.json
                                                       # 手元だけ: コメントの時刻（10:20〜22:59）

見るもの
  - 新しい記事 --latest 本（既定 20）の JSON と本番のページ（題名・小見出し・札・言葉づかい・画像・広告・AI画像の表示）
  - 固定ページ（/・/about・/editorial-policy・/contact・/news）: 連絡先・オーナーの名前・人の確認の約束
  - SNS: Bluesky の公開フィード（鍵なし・Buffer の回数を使わない）の直近 --sns-days 日の投稿を sns_rules で判定。
    Threads / X / Facebook は同じ文面を Buffer で出すので、Buffer は読まない（回数の節約。必要なら sns_audit.yml）

違反の行は必ず台帳の番号（[A1] など）で始める。台帳とずれていないかは tests/test_owner_rules_ledger.py が確かめる。
自社アクセスとして数えないよう、すべての取得にヘッダー X-Saisoku-Internal: 1 を付ける（AGENTS.md）。広告の画像には触らない。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import section_gate  # noqa: E402（表示の段 lib/article-sections.ts と同じ規則で、読者に見える形にしてから確かめる）

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
RETIRED_PATH = ROOT / "content" / "retired.json"
BASE = "https://ai-tech-express.jp"
UA = "SaisokuOwnerRulesAudit/1.0 (+https://ai-tech-express.jp/contact)"
HEADERS = {"User-Agent": UA, "X-Saisoku-Internal": "1"}
JST = timezone(timedelta(hours=9))
LABEL = "owner-rule-violation"
ISSUE_TITLE = "オーナーのルールに合わないものが本番にあります（毎日の点検）"
CONTACT_EMAIL = "contact@ai-tech-express.jp"
OWNER_NAME = "小室"
OWNER_NAME_PAGES = ("/about", "/editorial-policy")

# この点検が確かめる台帳の番号（docs/owner_rules.md の「点検」列と一致させる。tests/test_owner_rules_ledger.py）
AUDIT_RULES = (
    "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11",
    "I2", "I4", "I5",
    "A1", "A2", "A3", "A4", "A5", "A6", "A10", "A11", "A12", "A17", "A18", "A19", "A22", "A23", "A24", "A25", "A26", "A28",
    "W3", "W5", "W6", "W10", "W11", "W12", "W13", "W17", "W18", "W19", "W20", "W21", "W22", "W23",
)

# ---- 記事の言葉 ----------------------------------------------------------------

# A1: 決まった型の小見出し（2026-11-03「自分に関係ある？」「ライバルと比べてどう？」「もう使える？取引できる？」）
TEMPLATE_HEADINGS = re.compile(r"^(?:自分に関係ある[？?]|ライバルと比べてどう[？?]|もう使える[？?]|取引できる[？?]|もう使える[？?]取引できる[？?]|何がすごいの[？?])$")
# A2: 答えになっていない欄（元記事に無いことを「書かれていません」で埋める）。表示の段・生成の門と同じ式（scripts/section_gate.py）
NON_ANSWER = section_gate.SOURCE_NONANSWER_RE
# A3: 中身のない決まり文句
FILLER = re.compile(r"にとっても重要です|と(?:いえ|言え)(?:ます|るでしょう)|とも(?:いえ|言え)(?:ます|るでしょう)|目が離せません|注目が集まりそうです")
# A4: 幼い言い回し（2026-11-03「幼すぎる表現は本当にやめたい。恥ずかしい」）。「ざっくり」も使わない
CHILDISH = re.compile(r"すごい|スゴい|すっごく|しちゃう|しちゃい|ちゃいます|ちゃった|を作る道具|めっちゃ|ヤバい|やばい|ワクワク|ざっくり")
# W10: 同じ広告の枠・メール登録欄は1ページに1つまで（HTML の属性で数える。見えていない方も数える）
SLOT_ATTR = re.compile(r'data-(aff-pos|ad-position|newsletter-signup)="([^"]*)"')
# W11: 「記載なし」を読者に見せない（表・料金表・判定・本文）
NO_DATA = re.compile(r"記載なし")
# A24: 読者像の3つの立場の小見出し（会社員として／副業・独立を考えている人／お金・資産づくりの面で）が、見出し・太字の行で出ている
PERSONA_HEADING_HTML = re.compile(r"<(?:h[2-4]|strong)[^>]*>\s*(会社員として(?:仕事で)?|副業・独立を考えている人|お金・資産づくりの面で)\s*<")
# A6: 見出しの札があざとい（「3秒でわかる」など）
GIMMICK_LABEL = re.compile(r"\d+秒でわかる|\d+分でわかる|一瞬でわかる|サクッと")

# A5: 有名な会社に無理な説明を付けた題名（「ChatGPTのOpenAI」「AI半導体のNVIDIA」）
FAMOUS = (
    "OpenAI|NVIDIA|Google|Apple|Microsoft|Meta|Amazon|Anthropic|Samsung|サムスン|ソニー|トヨタ|Tesla|テスラ|"
    "Intel|AMD|ソフトバンク|楽天|任天堂|パナソニック|日立|ホンダ|NTT|KDDI|LINEヤフー|Netflix|Adobe|IBM|Qualcomm|Xiaomi"
)
FORCED_DESCRIPTOR = re.compile(rf"(?:^|[　\s、「])([^\s　、。「」『』]{{2,14}})の({FAMOUS})(?=が|は|、|　|\s|の|と|も|$)")
DESCRIPTOR_OK = re.compile(r"(?:傘下|子会社|親会社|米国|米|日本|中国|韓国|台湾|欧州|英国|日本法人)$")
ODD_PUNCT = [
    (re.compile(r"」、"), "「」、」（かぎかっこのすぐ後ろの読点）"),
    (re.compile(r"[、，][　\s]|[　\s][、。]"), "読点・句点の前後の空白"),
    (re.compile(r"、、|。。|！！|？？|[!?！？]{2,}"), "記号の重なり"),
    (re.compile(r"[、。・：:]$"), "末尾の記号"),
    (re.compile(r"。(?=.)"), "見出しの途中の「。」"),
    (re.compile(r"[!！]"), "感嘆符"),
]


def title_problems(title: str) -> list[str]:
    out = []
    title = str(title or "").strip()
    for m in FORCED_DESCRIPTOR.finditer(title):
        if not DESCRIPTOR_OK.search(m.group(1)):
            out.append(f"[A5] 有名な会社に説明を付けた題名（「{m.group(1)}の{m.group(2)}」）")
    for pattern, label in ODD_PUNCT:
        if pattern.search(title):
            out.append(f"[A5] 題名の記号が不自然: {label}")
    return out


def _headings(markdown: str) -> list[str]:
    return [re.sub(r"^#+\s*", "", line).strip() for line in str(markdown or "").splitlines() if re.match(r"^#{2,4}\s", line)]


def _texts(record: dict) -> list[tuple[str, str]]:
    """言葉を確かめる欄（欄の名前, 読者に見える文字）。本文・かんたん版は表示の段と同じ整え方（中身なしの章・答えになっていない文を除く）、
    判定のひとこと・よくある質問・「仕事・お金」の欄は表示と同じ cleanTake を通してから確かめる（本番のページで読者が読むもの）"""
    out = [(k, str(record.get(k) or "")) for k in ("title", "lede", "description")]
    for k in ("body", "easy"):
        text = str(record.get(k) or "")
        out.append((k, section_gate.clean_markdown(text.strip() if k == "easy" else text)[0] if text.strip() else ""))
    out += [("highlights", str(h)) for h in record.get("highlights") or []]
    wm = record.get("workMoney") or {}
    out += [("workMoney", section_gate.clean_take(str(p))) for p in (wm.get("points") or [] if isinstance(wm, dict) else [])]
    out += [("faq", f"{r.get('q', '')} {section_gate.clean_take(str(r.get('a') or ''))}") for r in record.get("faq") or [] if isinstance(r, dict)]
    out += [("todayActions", str(x)) for x in record.get("todayActions") or []]
    v = record.get("verdict") or {}
    if isinstance(v, dict):
        out.append(("verdict", section_gate.clean_take(str(v.get("take") or ""))))
    return [(k, t) for k, t in out if t]


def _usable_title(title: object) -> bool:
    """lib/article-sections.ts の usableTitle と同じ（記事ごとの見出しとして表示に使えるか）"""
    if not isinstance(title, str):
        return False
    t = title.strip()
    return 2 <= len(t) <= 32 and not re.search(r"[<>#\n|]", t) and not t.endswith("。") and not section_gate.is_nonanswer_title(t)


def _snippet(text: str, m: re.Match) -> str:
    return text[max(0, m.start() - 8): m.end() + 8].replace("\n", " ")


def record_problems(record: dict, page_headings: bool = False) -> list[str]:
    """記事の JSON だけで分かる違反（本番を開かない）。page_headings=True のときは、小見出しは本番のページの見出しで数える
    （article_page_problems。表示では記事ごとの見出し・普通の見出しに言い換えているため、JSON の章名では数えない）"""
    out = title_problems(record.get("title") or "")
    if not page_headings:
        titles = record.get("sectionTitles") or {}
        for kind in ("body", "easy"):
            own = (titles.get(kind) or {}) if isinstance(titles, dict) else {}
            shown = section_gate.clean_markdown(str(record.get(kind) or ""), titles=own if isinstance(own, dict) else None)[0]
            for heading in _headings(shown):
                if TEMPLATE_HEADINGS.match(heading) and not _usable_title(own.get(heading)):
                    out.append(f"[A1] 決まった型の小見出し（「{heading}」）")
                display = own.get(heading) if _usable_title(own.get(heading)) else heading
                if section_gate.is_nonanswer_title(display):
                    out.append(f"[A24] 見出しの言葉だけ変えた中身なしの章（「{display}」）")
            for line in shown.splitlines():
                if section_gate.PERSONA_SUBHEAD_RE.match(line.strip()):
                    out.append(f"[A24] 立場の小見出しが残っている（「{line.strip().strip('#* ')}」）")
    for field, text in _texts(record):
        # 型の小見出しの行は [A1] で数えるので、言葉の点検からは外す（同じものを2回数えない）
        text = "\n".join(line for line in text.splitlines() if not TEMPLATE_HEADINGS.match(re.sub(r"^#+\s*", "", line).strip()))
        if m := NON_ANSWER.search(text):
            out.append(f"[A2] 答えになっていない欄（{field}:「{_snippet(text, m)}」）。欄ごと省く")
        if m := FILLER.search(text):
            out.append(f"[A3] 中身のない決まり文句（{field}:「{_snippet(text, m)}」）")
        if m := CHILDISH.search(text):
            out.append(f"[A4] 幼い言い回し（{field}:「{_snippet(text, m)}」）")
        calls = [x for line in text.splitlines() if not line.lstrip().startswith(("#", "|", ">"))
                 for x in section_gate.split_sentences(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line)) if section_gate.ends_with_call(x)]
        if calls:
            out.append(f"[A17] 呼びかけの「〜しましょう」で締めている（{field}:「{calls[0].strip()[-24:]}」）")
        if field in ("verdict", "workMoney", "faq") and (m := NO_DATA.search(text)):
            out.append(f"[W11] 「記載なし」が読者に見える（{field}:「{_snippet(text, m)}」）")
    out += image_record_problems(record)
    out += low_value_problems(record)
    out += type_problems(record)
    out += genre_problems(record)
    return list(dict.fromkeys(out))


def genre_problems(record: dict) -> list[str]:
    """A22: 投資助言・売買のすすめ・取引のやり方を書かない／投資・マネーの記事に短い免責文。
    A23: エンタメはビジネス・テックの目線だけ（作品の告知・ゴシップの記事を作らない）。判定は scripts/genre_focus.py（生成の門と同じ）"""
    try:
        import genre_focus  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    out = [f"[A22] {p}" for p in genre_focus.advice_problems(record)]
    if record.get("genreSlug") == "entertainment" and not genre_focus.entertainment_focus(record.get("title") or "", record.get("tags")):
        out.append("[A23] エンタメの記事がビジネス・テックの目線ではない（作品の告知・ゴシップ。料金・決算・販売本数・AI・規約の話ではない）")
    return out


# ニュースの種類の型（scripts/news_types.py）に切り替えた時刻。これより後に作った Web 記事を A18・A19 で見る
TYPES_CUTOVER = "2026-11-03T13:45:00+00:00"


def type_problems(record: dict) -> list[str]:
    """[A18]・[A19]: 種類ごとの型で書かれているか（見出しの数・リードで新しさを言い切るか・くり返し）。公開前の門 scripts/article_style.py と同じ規則"""
    if record.get("sourceType") != "web" or record.get("type") == "roundup":
        return []
    if str(record.get("generatedAt") or "") < TYPES_CUTOVER:
        return []
    try:
        import article_style  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    out = [] if record.get("newsType") else ["[A18] ニュースの種類の型で書かれていない（newsType が無い）"]
    out += [f"[{code}] {msg}" for code, msg in article_style.coded_problems(record) if code in ("A18", "A19")]
    return out


def image_record_problems(record: dict) -> list[str]:
    credit = record.get("imageCredit") or {}
    if not isinstance(credit, dict) or credit.get("kind") != "ai":
        return []
    try:
        import images  # noqa: PLC0415

        if images.product_launch(record):
            return ["[I2] 発表された製品・車・端末を AI で描いた画像"]
    except Exception:  # noqa: BLE001
        pass
    return []


def low_value_problems(record: dict) -> list[str]:
    """A10: 価値の低い種類（他人の動画の要約・プレスリリースの言い換え・はっきり薄い記事）"""
    try:
        import indexnow  # noqa: PLC0415
        import retire_articles  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    channel = record.get("channelSlug")
    if channel in indexnow.CREATOR_CHANNELS:
        return ["[A10] 他人の動画の要約の記事（新しく作らない種類）"]
    if channel in getattr(indexnow, "WIRE_SOURCES", set()):
        return ["[A10] プレスリリース配信の言い換えの記事（新しく作らない種類）"]
    if retire_articles.is_thin(record):
        return ["[A10] 独自の情報が少ない短い記事"]
    return notice_problems(record)


def notice_problems(record: dict, sns: set[str] | None = None) -> list[str]:
    """A26: お知らせ・告知・統計の転載の記事（認可・受章者・議事要旨・決算発表日の予告・啓発月間・出荷量など）。
    直近7日に SNS に出した記事は投稿のリンクのために残している（7日を過ぎたら retire_articles.py --notice で取り下げる）"""
    try:
        import notice_gate  # noqa: PLC0415
        import retire_articles  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    kind = notice_gate.notice_reason(record)
    if not kind:
        return []
    if sns is None:
        sns = retire_articles.recent_sns(datetime.now(timezone.utc), everywhere=True)
    if record.get("slug") in sns:
        return []
    return [f"[A26] お知らせ・統計の転載の記事（{notice_gate.LABELS[kind]}）が公開されている。"
            "python3 scripts/retire_articles.py --notice --apply で取り下げる"]


# ---- 本番のページ ---------------------------------------------------------------


class PageParser(HTMLParser):
    """小見出し（h1〜h3）と、本文に見える文字（script・style を除く）"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[str] = []
        self.text: list[str] = []
        self._skip = 0
        self._h: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self._skip += 1
        elif tag in ("h1", "h2", "h3"):
            self._h = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("h1", "h2", "h3") and self._h is not None:
            self.headings.append(re.sub(r"\s+", " ", "".join(self._h)).strip())
            self._h = None

    def handle_data(self, data):
        if self._skip:
            return
        self.text.append(data)
        if self._h is not None:
            self._h.append(data)


def parse(html: str) -> PageParser:
    p = PageParser()
    p.feed(html)
    return p


def article_page_problems(html: str, record: dict) -> list[str]:
    """記事のページ（本番）の違反"""
    out = []
    page = parse(html)
    # 小見出しは、読者に見える本番のページの見出しで数える（本文の章名は記事ごとの見出し・普通の見出しに言い換えて表示している）
    gimmick = False
    for h in page.headings:
        if TEMPLATE_HEADINGS.match(h):
            out.append(f"[A1] 決まった型の小見出しがページに出ている（「{h}」）")
        elif CHILDISH.search(h):
            out.append(f"[A4] 小見出しに幼い言い回し（「{h}」）")
        if GIMMICK_LABEL.search(h):
            gimmick = True
            out.append(f"[A6] あざとい札（「{h}」）")
        if section_gate.is_nonanswer_title(h):
            out.append(f"[A24] 見出しの言葉だけ変えた中身なしの章がページに出ている（「{h}」）")
    if m := PERSONA_HEADING_HTML.search(html):
        out.append(f"[A24] 立場の小見出しがページに出ている（「{m.group(1)}」）")
    visible = "".join(page.text)
    if not gimmick and (m := GIMMICK_LABEL.search(visible)):
        out.append(f"[A6] あざとい札がページに出ている（「{_snippet(visible, m)}」）")
    credit = record.get("imageCredit") or {}
    if isinstance(credit, dict) and credit.get("kind") == "ai" and "AI生成" not in visible:
        out.append("[I4] AI の画像なのに「AI生成」の表示がない")
    if not re.search(r'rel="[^"]*sponsored', html):
        out.append("[A11] 記事に広告（PR）が1つもない")
    if OWNER_NAME in html:  # 見えない構造化データ（JSON-LD）も含めて見る
        out.append("[W6] オーナーの名前が記事のページに出ている（/about・/editorial-policy だけ）")
    out += slot_problems(html)
    out += related_problems(html, record)
    out += block_order_problems(html)
    if "WEB SERVICE" in visible:
        out.append("[W20] 広告に写真の代わりの「WEB SERVICE」の板が出ている（頭文字の札にする）")
    if m := NO_DATA.search(visible):
        out.append(f"[W11] 「記載なし」がページに出ている（「{_snippet(visible, m)}」）")
    return list(dict.fromkeys(out))


# A28: 記事ページの欄の並び（要約の欄を2つ出さない・目次は本文の前）
H2_RE = re.compile(r"<h2\b([^>]*)>(.*?)</h2>", re.S)
SUMMARY_DUP = re.compile(r"^(?:要点|ひとことで言うと|要約|ポイント)$")


def block_order_problems(html: str) -> list[str]:
    """A28: 「この記事のポイント」と要約版の「要点」が両方ある／目次が本文（詳細版・要約版）の章より後ろにある"""
    heads = []
    for attrs, inner in H2_RE.findall(html):
        m = re.search(r'\bid="([^"]+)"', attrs)
        heads.append((m.group(1) if m else "", re.sub(r"<[^>]+>", "", inner).strip()))
    out = []
    texts = [h for _, h in heads]
    if "この記事のポイント" in texts and any(SUMMARY_DUP.match(h) for h in texts):
        out.append("[A28] 要約の欄が2つある（「この記事のポイント」と要約版の「要点」）")
    toc = next((i for i, (hid, _) in enumerate(heads) if hid == "toc-heading"), None)
    first_body = next((i for i, (hid, _) in enumerate(heads) if re.match(r"(?:sec|easy)-\d+$", hid)), None)
    if toc is not None and first_body is not None and toc > first_body:
        out.append("[A28] 目次が本文の章より後ろにある（本文の前に置く）")
    return out


# W19: 関連記事・「次に読むならこれ」の欄（記事ページの id で切り出す）と、地震・津波の速報の記事（lib/related.ts の isDisasterArticle と同じ見分け方）
RELATED_BLOCK = re.compile(r'(?:aria-labelledby="related-heading"|id="next-read-heading")(.*?)</section>', re.S)
QUAKE_LINK = re.compile(r'href="/articles/((?:quake|tsunami)-[^"#?]+)"')


def _is_disaster(record: dict) -> bool:
    slug = str(record.get("slug") or "")
    return bool(re.match(r"(?:quake|tsunami)-", slug)) or record.get("channelSlug") == "jma" or str(record.get("title") or "").startswith(("【地震】", "【津波】"))


def related_problems(html: str, record: dict) -> list[str]:
    """W19: 地震・津波の記事とそれ以外の記事を、関連記事・次に読む記事で混ぜない（年金の記事に【地震】が出ていた。2026-11-03）"""
    links = {m for block in RELATED_BLOCK.findall(html) for m in QUAKE_LINK.findall(block)}
    if _is_disaster(record):
        return []
    return [f"[W19] 関連記事に地震・津波の記事が混ざっている（/articles/{slug}）" for slug in sorted(links)]
# A25: 朝のまとめ・週のまとめの題名（ページの data-title-gate の付いた題名）。検索向けの問いの型・説明つきの社名を出さない
TITLE_GATE_HTML = re.compile(r'data-title-gate="[^"]*"[^>]*>\s*([^<]+?)\s*<')
SEO_QUESTION = re.compile(r"とは[？?]|日本で(?:も)?使える[？?]|の買い方|使い方と料金|料金も$|料金と提供状況")


def summary_page_problems(path: str, html: str) -> list[str]:
    """[A25] まとめのページの題名が題名の門番を通っているか（2026-11-03 オーナー決定）"""
    import html as htmllib  # noqa: PLC0415

    titles = [htmllib.unescape(t) for t in TITLE_GATE_HTML.findall(html)]
    out = []
    if not titles:
        out.append(f"[A25] まとめの題名が見つからない（{path}。data-title-gate の印が無い）")
    for t in titles:
        for problem in title_problems(t):
            if "説明を付けた" in problem:
                out.append(f"[A25] まとめの題名に説明つきの社名（{path}:「{t}」）")
        if SEO_QUESTION.search(t):
            out.append(f"[A25] まとめの題名が検索向けの問いの型（{path}:「{t}」）")
    return list(dict.fromkeys(out))


def latest_summary_paths() -> list[str]:
    """同梱の最新の朝のまとめと週のまとめのページ"""
    paths = []
    digests = sorted(p.stem for p in (ROOT / "content" / "digests").glob("*.json") if re.fullmatch(r"\d{8}", p.stem))
    if digests:
        paths.append(f"/digest/{digests[-1]}")
    try:
        weeks = sorted(w.get("id", "") for w in json.loads((ROOT / "content" / "weekly.json").read_text("utf-8")).get("weeks") or [])
    except (OSError, ValueError):
        weeks = []
    if weeks and weeks[-1]:
        paths.append(f"/weekly/{weeks[-1]}")
    return paths


def slot_problems(html: str) -> list[str]:
    """W10: 同じ広告の枠・メール登録欄が1ページに2つ以上（かんたん版と通常版の両方に入れない。2026-11-03）"""
    counts: dict[str, int] = {}
    for kind, value in SLOT_ATTR.findall(html):
        key = "メール配信の登録欄" if kind == "newsletter-signup" else f"広告の枠（{value}）"
        counts[key] = counts.get(key, 0) + 1
    return [f"[W10] {key}が{n}つある（1ページに1つまで）" for key, n in counts.items() if n > 1]


REVIEW_CLAIM = re.compile(
    r"(?:全て|すべて|全部)の記事を(?:人|編集部|編集者|スタッフ)が|全記事を(?:人|編集部|編集者|スタッフ)が|"
    r"(?:人|編集者|編集部)が(?:1本ずつ|一本ずつ|全て|すべて)(?:確認|チェック|読)|確認済み(?:の記事)?の一覧"
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def static_page_problems(path: str, html: str) -> list[str]:
    out = []
    visible = "".join(parse(html).text)
    if path in ("/about", "/editorial-policy") and (m := REVIEW_CLAIM.search(visible)):
        out.append(f"[A12] 人の確認について実態と違う約束（{path}:「{_snippet(visible, m)}」）")
    if path in ("/about", "/contact"):
        if CONTACT_EMAIL not in html:
            out.append(f"[W5] 連絡先 {CONTACT_EMAIL} が {path} に無い")
        others = {e for e in EMAIL.findall(visible) if e.lower() != CONTACT_EMAIL and not e.lower().endswith(("@example.com", "@sentry.io"))}
        if others:
            out.append(f"[W5] {path} に別の連絡先（{', '.join(sorted(others))[:60]}）")
    if path not in OWNER_NAME_PAGES and OWNER_NAME in html:
        out.append(f"[W6] オーナーの名前が {path} に出ている（/about・/editorial-policy だけ）")
    out += slot_problems(html)
    if m := NO_DATA.search(visible):
        out.append(f"[W11] 「記載なし」が {path} に出ている（「{_snippet(visible, m)}」）")
    return out


# ---- 発信元のページ・選び方ガイド（2026-11-03 オーナー決定 2・9） ----

# W17: 報道機関の発信元ページ（/channels/radar-<報道機関>）を「公式発表」と呼ばない・検索に出さない（lib/news-outlets.ts と同じ判定）
OUTLET_OFFICIAL = re.compile(r"公式発表|公式ブログ|プレスリリース|公式サイトを見る")
# W18: 選び方ガイドに別の種類の決まり文句（回線の「速度は公称値」）・「〜しましょう。」の締め
CHOOSE_PAGES = {"/choose/ai-career": re.compile(r"公称値|規格上の最大値|通信速度"), "/choose/dify-hosting": re.compile(r"公称値|規格上の最大値|求人")}
CALL_OUT = re.compile(r"ましょう[。！]")


def outlet_channel(slug: str) -> bool:
    """報道機関の発信元か（lib/news-outlets.ts の isNewsOutletChannel と同じ）"""
    return slug == "trends" or (slug.startswith("radar-") and slug != "radar-official")


def source_page_problems(path: str, html: str) -> list[str]:
    """W17: 報道機関・各社が混ざった発信元ページ"""
    out = []
    if not NOINDEX.search(html):
        out.append(f"[W17] 報道機関の発信元ページ {path} が検索に出る設定（noindex にする）")
    main = html.split("<main", 1)[-1].split("</main>", 1)[0] if "<main" in html else html
    visible = "".join(parse(main).text)
    if path != "/channels/radar-official" and (m := OUTLET_OFFICIAL.search(visible)):
        out.append(f"[W17] 報道機関の発信元ページ {path} が「{m.group(0)}」と名乗っている（「{_snippet(visible, m)}」）")
    desc = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', html)
    if path != "/channels/radar-official" and desc and OUTLET_OFFICIAL.search(desc.group(1)):
        out.append(f"[W17] 報道機関の発信元ページ {path} の説明文が「公式」と名乗っている")
    return out


def choose_page_problems(path: str, html: str) -> list[str]:
    """W18: 選び方ガイド"""
    out = []
    visible = "".join(parse(html).text)
    bad = CHOOSE_PAGES.get(path)
    if bad and (m := bad.search(visible)):
        out.append(f"[W18] {path} に別の種類のガイドの決まり文句（「{_snippet(visible, m)}」）")
    if m := CALL_OUT.search(visible):
        out.append(f"[W18] {path} の文が「〜しましょう」で締まっている（「{_snippet(visible, m)}」）")
    return out


# ---- 図鑑・ハブのページ（2026-11-03 オーナー「内容がすごく薄い」「このページもいらない」） ----

# W13: 図鑑に記事の写真・フリー素材・AI 画像を出さない（Codex に関係のない記事の写真、ChatGPT Work に素材のノートPCが出ていた）
ARTICLE_IMAGE = re.compile(r'<img[^>]+src="[^"]*(?:/img/(?!brand-)[A-Za-z0-9_-]+\.|/cover/|ytimg\.com|unsplash|pexels|pixabay)', re.I)
# W13: 決まった型の問いかけの小見出し（lib/product-substance.ts の TEMPLATE_PRODUCT_HEADINGS と同じ）
PRODUCT_TEMPLATE_HEADINGS = {"何ができる？", "料金は？", "日本で使える？", "どうやって使う？", "これまでと何が違う？", "前のモデルと何が違う？"}
# W13: 一覧に載る製品ページに必要な章（3つ以上）
PRODUCT_SECTIONS = ("料金プラン", "日本での提供状況", "主な機能・仕様", "発表・更新の履歴", "よくある質問")
NOINDEX = re.compile(r'<meta[^>]+name="robots"[^>]+noindex', re.I)
# W12: 終了したページ
RETIRED_TOOL_PAGES = ("/tools/usd-jpy",)


def hub_page_problems(path: str, html: str) -> list[str]:
    """図鑑・ハブのページ（/products・/products/<slug>・/guides・/ai-pricing）の違反"""
    out = []
    page = parse(html)
    visible = "".join(page.text)
    if path.startswith("/products") and (m := ARTICLE_IMAGE.search(html)):
        out.append(f"[W13] 図鑑に記事・素材の写真が出ている（{path}: {m.group(0)[-60:]}）")
    for h in page.headings:
        if h in PRODUCT_TEMPLATE_HEADINGS:
            out.append(f"[W13] 決まった型の問いかけの小見出し（{path}:「{h}」）")
    if m := re.search(r"元記事(?:に|で)は", visible):
        out.append(f"[W13] 「元記事には」が {path} に出ている（「{_snippet(visible, m)}」）")
    if m := NO_DATA.search(visible):
        out.append(f"[W11] 「記載なし」が {path} に出ている（「{_snippet(visible, m)}」）")
    for retired in RETIRED_TOOL_PAGES:
        if f'href="{retired}"' in html:
            out.append(f"[W12] 終了したページ {retired} へのリンクが {path} に残っている")
    return out


def listed_product_problems(path: str, html: str) -> list[str]:
    """図鑑の一覧に載っている製品ページの中身（一覧に載せたのに薄い・検索から外しているのは違反）"""
    out = []
    if NOINDEX.search(html):
        out.append(f"[W13] 一覧に載っている製品ページが noindex（{path}）")
    found = [s for s in PRODUCT_SECTIONS if s in parse(html).headings]
    if len(found) < 3:
        out.append(f"[W13] 一覧に載っている製品ページの中身が薄い（{path}: 章 {len(found)}/5）")
    return out


def product_links(html: str) -> list[str]:
    """図鑑の一覧から製品ページへのリンク"""
    return list(dict.fromkeys(re.findall(r'href="(/products/[a-z0-9-]+)"', html)))


# ---- AI料金表・410 ページ・ヘッダー下の帯（2026-11-03 オーナー決定） ---------------------------------

# W21: AI料金表の為替は1つ（同じ出典・同じ日付）。出典は公式の料金ページ（自社の記事を出典にしない）
FX_VALUE = re.compile(r"1ドル[＝=]\s*(\d{2,3}(?:\.\d+)?)円")


def pricing_page_problems(html: str) -> list[str]:
    visible = "".join(parse(html).text)
    out = []
    rates = sorted({float(x) for x in FX_VALUE.findall(visible)})
    if not rates:
        out.append("[W21] AI料金表に為替（1ドル＝◯円）と日付が出ていない")
    elif len(rates) > 1:
        out.append(f"[W21] AI料金表に為替が{len(rates)}種類ある（{', '.join(f'{r:g}円' for r in rates)}）。サイトで1つにする")
    if "記事で確認" in visible:
        out.append("[W21] AI料金表の出典が自社の記事（「記事で確認」）。公式の料金ページを出典にする")
    if "公式：" not in visible and "公式の料金ページ" not in visible:
        out.append("[W21] AI料金表に公式の料金ページの出典が出ていない")
    return out


def gone_page_problems(status: int, html: str) -> list[str]:
    """W22: 取り下げた記事は 410 のまま、サイトの見た目（ヘッダー・フッター）と近い記事へのリンクを出す"""
    out = []
    if status != 410:
        out.append(f"[W22] 取り下げた記事が 410 になっていない（HTTP {status}）")
        return out
    if "site-header" not in html or "<footer" not in html:
        out.append("[W22] 410 のページにサイトのヘッダー・フッターが無い（素のページ）")
    if 'data-gone-page' in html and not re.search(r'href="/articles/[A-Za-z0-9_-]+"', html):
        out.append("[W22] 410 のページに、今も載っている記事へのリンクが無い")
    if "取り下げました" in "".join(parse(html).text):
        out.append("[W22] 410 のページの言い方が丁寧でない（「取り下げました」）")
    return out


LIVE_LINE = re.compile(r'class="live-line"[\s\S]{0,1500}?</div>\s*</div>')
LIVE_LABEL = re.compile(r'(?:data-live-label="([^"]+)"[^>]*>|<span[^>]*>)\s*(速報|最新)\s*</span>')


def live_line_problems(html: str, by_slug: dict[str, dict]) -> list[str]:
    """W23: ヘッダー下の帯の赤い「速報」は本当の速報だけ（画像の札と同じ判定。sns_rules.is_fast_news）"""
    m = LIVE_LINE.search(html)
    if not m:
        return []
    block = m.group(0)
    label = LIVE_LABEL.search(block)
    link = re.search(r'href="/articles/([A-Za-z0-9_-]+)"', block)
    if not label or not link or label.group(2) != "速報":
        return []
    article = by_slug.get(link.group(1))
    if article is None:
        return []
    try:
        import sns_rules  # noqa: PLC0415

        fast = sns_rules.is_fast_news(article)
    except Exception:  # noqa: BLE001
        minutes = article.get("speedMinutes")
        fast = bool(article.get("breaking")) or isinstance(minutes, (int, float))
    return [] if fast else [f"[W23] 速報ではない記事（{link.group(1)}）にヘッダー下の赤い「速報」が付いている。「最新」にする"]


def retired_sample(limit: int = 2) -> list[str]:
    """W22 の確かめに使う、転送のない取り下げた記事（410 のもの）"""
    try:
        data = json.loads(RETIRED_PATH.read_text("utf-8")).get("articles") or {}
    except (OSError, ValueError):
        return []
    return [f"/articles/{slug}" for slug, e in sorted(data.items()) if isinstance(e, dict) and not e.get("redirect")][:limit]


def fetch(url: str, timeout: int = 30) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=HEADERS)
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.status, res.read(3_000_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, ""
        except Exception:  # noqa: BLE001
            continue
    return 0, ""


# ---- SNS ------------------------------------------------------------------------


def sns_problems(posts: list[dict], judge) -> list[str]:
    """SNS の投稿（sns_audit.normalize_bsky の形）の違反。judge は sns_rules.audit_post"""
    out = []
    seen: dict[str, str] = {}
    for post in posts:
        if post.get("reply"):
            continue
        where = post.get("url") or post.get("id") or ""
        when = str(post.get("at") or "")
        try:
            when = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(JST).strftime("%m/%d %H:%M")
        except ValueError:
            pass
        for v in judge(post) or []:
            out.append(f"{_sns_rule_id(v)} {post.get('platform')} {when}: {v}（{where}）")
        links = " ".join([post.get("text") or "", *(post.get("links") or [])])
        if m := re.search(r"/articles/([A-Za-z0-9_-]+)", links):
            slug = m.group(1)
            if slug in seen and seen[slug] != where:
                out.append(f"[S8] 同じ記事の投稿が2つある（{slug}: {seen[slug]} と {where}）")
            seen.setdefault(slug, where)
    return out


# sns_rules のルールの番号 → 台帳の番号
SNS_RULE_MAP = {
    "ルール1": "[S4]", "ルール2": "[S7]", "ルール3": "[S9]", "ルール4": "[S11]", "ルール5": "[S8]",
    "ルール6": "[S1]", "ルール7": "[S4]", "長さ": "[S11]",
}


def _sns_rule_id(violation: str) -> str:
    m = re.match(r"\[(ルール\d|長さ)\]", violation)
    if not m:
        return "[S11]"
    if m.group(1) == "ルール6":
        if "速報ではない" in violation:
            return "[S2]"
        if "書き出し" in violation and "呼びかけ" not in violation:
            return "[S3]"
        if "問いかけ" in violation:
            return "[S5]"
    if m.group(1) == "ルール1" and "リンク" in violation:
        return "[S6]"
    if m.group(1) == "ルール1" and "問いかけ" in violation:
        return "[S5]"
    if "ざっくり" in violation:
        return "[S10]"
    return SNS_RULE_MAP.get(m.group(1), "[S11]")


# ---- 手元だけ: コメントの時刻 ------------------------------------------------------


def comment_time_ok(hhmm: str) -> bool:
    """O4: コメントは 10:20〜22:59（日本時間）だけ"""
    try:
        h, m = (int(x) for x in str(hhmm).split(":")[:2])
    except ValueError:
        return False
    return (10, 20) <= (h, m) <= (22, 59)


def comment_log_problems(entries: list[dict], since: str = "2026-11-03") -> list[str]:
    return [f"[O4] {e.get('date')} {e.get('time')} に {e.get('sns')} へコメント（10:20〜22:59 だけ）"
            for e in entries if str(e.get("date") or "") >= since and not comment_time_ok(str(e.get("time") or ""))]


# ---- まとめ ---------------------------------------------------------------------


def load_articles() -> list[dict]:
    try:
        retired = set((json.loads(RETIRED_PATH.read_text("utf-8")).get("articles") or {}).keys())
    except (OSError, ValueError):
        retired = set()
    out = []
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("slug") and data["slug"] not in retired and not data.get("duplicateOf"):
            out.append(data)
    out.sort(key=lambda a: str(a.get("publishedAt") or ""), reverse=True)
    return out


def run(latest: int, sns_days: float, network: bool, base: str = BASE) -> dict[str, list[str]]:
    """{場所: [違反...]}"""
    found: dict[str, list[str]] = {}
    articles = load_articles()[:latest]
    pages: dict[str, tuple[int, str]] = {}
    if network:
        urls = [f"/articles/{a['slug']}" for a in articles] + ["/", "/about", "/editorial-policy", "/contact", "/news", "/ai-pricing", "/newsletter"]
        with ThreadPoolExecutor(max_workers=6) as pool:
            pages = dict(zip(urls, pool.map(lambda p: fetch(base + p), urls)))
    for a in articles:
        path = f"/articles/{a['slug']}"
        status, html = pages.get(path, (0, "")) if network else (0, "")
        v = record_problems(a, page_headings=status == 200)
        if network:
            if status != 200:
                v.append(f"[W3] ページが開けない（HTTP {status}）")
            else:
                v += article_page_problems(html, a)
        if v:
            found[path] = list(dict.fromkeys(v))
    # A26: 全部の記事のうち、お知らせ・統計の転載（新しい記事だけでなく、前から残っているものも）
    try:
        import retire_articles  # noqa: PLC0415

        sns = retire_articles.recent_sns(datetime.now(timezone.utc), everywhere=True)
    except Exception:  # noqa: BLE001
        sns = set()
    for a in load_articles():
        if v := notice_problems(a, sns):
            found.setdefault(f"/articles/{a['slug']}", []).extend(x for x in v if x not in found.get(f"/articles/{a['slug']}", []))
    if not network:
        return found
    for path in ("/", "/about", "/editorial-policy", "/contact", "/news", "/ai-pricing", "/newsletter"):
        status, html = pages.get(path, (0, ""))
        v = [f"[W3] ページが開けない（HTTP {status}）"] if status != 200 else static_page_problems(path, html)
        if v:
            found[path] = v
    # W17: 報道機関の発信元ページ（記事にあるものから最大3つ）と、各社が混ざった radar-official。W18: 選び方ガイド
    outlets = sorted({str(a.get("channelSlug") or "") for a in load_articles() if outlet_channel(str(a.get("channelSlug") or ""))})[:3]
    source_paths = [f"/channels/{s}" for s in outlets] + ["/channels/radar-official"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        extra = dict(zip(source_paths + list(CHOOSE_PAGES), pool.map(lambda p: fetch(base + p), source_paths + list(CHOOSE_PAGES))))
    for path, (status, html) in extra.items():
        if status == 404 and path in source_paths:
            continue  # 記事が取り下げられて発信元ページが無くなった
        if status != 200:
            v = [f"[W3] ページが開けない（HTTP {status}）"]
        else:
            v = source_page_problems(path, html) if path in source_paths else choose_page_problems(path, html)
        if v:
            found.setdefault(path, []).extend(v)
    # A25: 朝のまとめ・週のまとめの題名
    summaries = latest_summary_paths()
    with ThreadPoolExecutor(max_workers=2) as pool:
        for path, (status, html) in zip(summaries, pool.map(lambda p: fetch(base + p), summaries)):
            v = [f"[W3] ページが開けない（HTTP {status}）"] if status != 200 else summary_page_problems(path, html)
            if v:
                found.setdefault(path, []).extend(v)
    # W12・W13: 図鑑・ハブのページ
    hubs = ["/products", "/guides", "/ai-pricing"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        hub_pages = dict(zip(hubs, pool.map(lambda p: fetch(base + p), hubs)))
        listed = product_links(hub_pages["/products"][1])[:12] if hub_pages["/products"][0] == 200 else []
        hub_pages.update(dict(zip(listed, pool.map(lambda p: fetch(base + p), listed))))
        retired = dict(zip(RETIRED_TOOL_PAGES, pool.map(lambda p: fetch(base + p), RETIRED_TOOL_PAGES)))
    for path, (status, html) in hub_pages.items():
        v = [f"[W3] ページが開けない（HTTP {status}）"] if status != 200 else hub_page_problems(path, html)
        if status == 200 and path in listed:
            v += listed_product_problems(path, html)
        if v:
            found.setdefault(path, []).extend(x for x in v if x not in found.get(path, []))
    for path, (status, _html) in retired.items():
        if status != 410:
            found.setdefault(path, []).append(f"[W12] 終了したページが 410 になっていない（HTTP {status}）")
    status, home = pages.get("/", (0, ""))
    if status == 200:
        for retired_path in RETIRED_TOOL_PAGES:
            if f'href="{retired_path}"' in home:
                found.setdefault("/", []).append(f"[W12] 終了したページ {retired_path} へのリンクがトップに残っている")
    # W21: AI料金表の為替と出典
    status, html = pages.get("/ai-pricing", (0, ""))
    if status == 200 and (v := pricing_page_problems(html)):
        found.setdefault("/ai-pricing", []).extend(v)
    # W23: ヘッダー下の帯の「速報」
    status, html = pages.get("/", (0, ""))
    if status == 200 and (v := live_line_problems(html, {a["slug"]: a for a in load_articles()})):
        found.setdefault("/", []).extend(v)
    # W22: 取り下げた記事の 410 ページ
    for path in retired_sample():
        status, html = fetch(base + path)
        if v := gone_page_problems(status, html):
            found.setdefault(path, []).extend(v)
    # I5: 画像が読めるか（scripts/check_images.py と同じ判定。新しい記事のページだけ）
    try:
        import check_images  # noqa: PLC0415

        where: dict[str, str] = {}
        for p, (status, html) in pages.items():
            if status == 200 and p.startswith("/articles/"):
                for u in check_images.page_images(html, base + p)[:12]:
                    where.setdefault(u, p)
        for url, problem in check_images.check_urls(list(where)[:150]).items():
            if problem:
                found.setdefault(where[url], []).append(f"[I5] 読めない画像（{problem}: {url[:90]}）")
    except Exception as error:  # noqa: BLE001
        print(f"[owner-audit] 画像の確認をとばしました: {type(error).__name__}", file=sys.stderr)
    # SNS（Bluesky の公開フィード）
    try:
        import sns_audit  # noqa: PLC0415
        import sns_rules  # noqa: PLC0415

        since = datetime.now(timezone.utc) - timedelta(days=sns_days)
        posts = [sns_audit.normalize_bsky(p) for p in sns_audit.bluesky_posts(since)]
        v = sns_problems(posts, sns_rules.audit_post)
        found["SNS（Bluesky の直近の投稿 %d 本）" % len([p for p in posts if not p.get("reply")])] = v
    except Exception as error:  # noqa: BLE001
        found["SNS"] = [f"[S11] SNS の投稿を読めなかった（{type(error).__name__}）"]
    return {k: v for k, v in found.items() if v}


def render(found: dict[str, list[str]], latest: int) -> str:
    total = sum(len(v) for v in found.values())
    by_rule: dict[str, int] = {}
    for v in found.values():
        for line in v:
            rid = re.match(r"\[([A-Z]+\d+)\]", line)
            by_rule[rid.group(1) if rid else "?"] = by_rule.get(rid.group(1) if rid else "?", 0) + 1
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"オーナーのルールの毎日の点検（{now} JST・新しい記事 {latest} 本・固定ページ・Bluesky の直近の投稿）で、**{total} 件**見つかりました。",
        f"番号は台帳 [docs/owner_rules.md](https://github.com/{os.environ.get('GITHUB_REPOSITORY') or 'koyuiw7-pixel/ai-news-auto'}/blob/main/docs/owner_rules.md) の番号です。",
        "",
        "| 番号 | 件数 |",
        "|---|---|",
        *[f"| {k} | {n} |" for k, n in sorted(by_rule.items(), key=lambda x: (-x[1], x[0]))],
        "",
    ]
    for where, v in found.items():
        lines.append(f"**{where}**")
        lines += [f"- {x}" for x in v[:15]]
        if len(v) > 15:
            lines.append(f"- …ほか {len(v) - 15} 件")
        lines.append("")
    lines.append("見張り: scripts/owner_rules_audit.py（.github/workflows/owner_rules_audit.yml）。読むだけで、投稿・変更はしません。違反が無くなると自動で閉じます。")
    return "\n".join(lines)


def _gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60, check=False)


def write_issue(found: dict[str, list[str]], body: str) -> None:
    listed = _gh("issue", "list", "--label", LABEL, "--state", "open", "--limit", "1", "--json", "number", "--jq", ".[0].number // empty")
    number = listed.stdout.strip() if listed.returncode == 0 else ""
    run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    if not found:
        if number:
            _gh("issue", "close", number, "--comment", f"今日の点検では違反はありませんでした。{run_url}")
        return
    body = f"{body}\n\n実行: {run_url}"
    if number:
        _gh("issue", "edit", number, "--body", body)
        _gh("issue", "comment", number, "--body", f"今日の点検: {sum(len(v) for v in found.values())} 件（本文を最新に更新しました）。{run_url}")
        print(f"[owner-audit] issue #{number} を更新しました")
        return
    _gh("label", "create", LABEL, "--color", "B60205", "--description", "オーナーのルール（docs/owner_rules.md）に合わないもの", "--force")
    made = _gh("issue", "create", "--title", ISSUE_TITLE, "--label", LABEL, "--body", body)
    print(f"[owner-audit] issue を立てました: {made.stdout.strip() or made.stderr.strip()[:200]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="オーナーのルールの毎日の点検（読むだけ）")
    parser.add_argument("--latest", type=int, default=20)
    parser.add_argument("--sns-days", type=float, default=1.0)
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--issue", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument("--comment-log", default="")
    args = parser.parse_args()
    if args.comment_log:
        entries = json.loads(Path(args.comment_log).expanduser().read_text("utf-8"))
        bad = comment_log_problems(entries)
        print("\n".join(bad) or "ok: コメントはすべて 10:20〜22:59")
        return 0
    found = run(args.latest, args.sns_days, not args.no_network, args.base)
    body = render(found, args.latest)
    print(body)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(found, ensure_ascii=False, indent=1), "utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(body + "\n")
    if args.issue:
        write_issue(found, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
