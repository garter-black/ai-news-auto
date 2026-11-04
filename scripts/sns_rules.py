#!/usr/bin/env python3
"""SNS 投稿のルールの門番（2026-11-03 オーナー承認のルール）。投稿するすべての経路が、API を呼ぶ直前にここを通す。

  violations = sns_rules.check_post(platform, text, media, article)   # 違反の一覧（空なら OK）
  sns_rules.enforce(platform, text, media, article, source="threads_post")  # 違反なら記録して SnsRuleViolation を投げる（投稿しない）

ルール（オーナー承認）
  1. 投稿は必ず実在の記事について。書き出し → 「要約すると、」＋短い事実2文 → 答えやすい問いかけ → 記事のリンクを本文に
     （X だけはリンクを返信に）。リンクには SNS ごとの utm を付ける。
  2. 画像は 16:9 の横長・写真だけのカード（文字の帯なし）。発表された製品を AI で描いた画像は使わない。動画も 16:9。
  3. 決まった型の投稿・中身のない投稿はしない（記事のない問いかけ、「気軽にコメントください」、「番号でコメント」など）。
  4. 自然な日本語。「ざっくり」・硬い言い回しを使わない。英語の文を残さない。スポーツは日本のスポーツだけ（五輪・世界陸上・W杯・WBC は例外）。
  5. 1つの記事につき投稿は1つ（動画は記事の投稿に添える。別の投稿にしない）。
  6. （2026-11-03 オーナー承認その2）書き出しの1行目は、関係する読者を名指しする呼びかけ
     （「〇〇な人、これは要チェックです」「〇〇の皆さん、朗報です」など）。【速報】は本当の速報の記事だけ
     （画像の「速報」と同じ決まり: article.breaking か、公式発表から60分以内に載せた記事）。
     「先に言うと、〜できない／対象外」「発表からN分で掲載」の書き出しは使わない。
     問いかけは、記事を読まなくても答えられる「読者自身の好み・経験」を聞く（「この機能、使ってみたい？」は×）。
  7. （2026-11-03 オーナー指示）要約の文は、やさしい「です・ます」の短い文。記事の硬い文（「〜する。」「〜である。」
     「〜した。」の言い切りや、「〜を発表。」で終わる文）をそのまま使わない。だれが（主語）を書く。
  8. （2026-11-03 オーナー指示「SNS の投稿は動画のはず」・繰り返しの違反）記事の投稿は**必ず動画つき**。写真・カードだけの投稿は出さない。
     例外は朝のまとめ（kind="digest"。号のシェア画像 share.png）と、（2026-11-03 オーナー承認）災害（地震・津波・噴火・大雨・台風の警報など）や
     遅れると読者に害がある知らせ（is_urgent。記事の disaster / urgent / safetyAlert）。これらは写真のカードですぐ出してよい。動画が間に合わなければ投稿を遅らせ（枠の順番は保つ）、
     作れなければ issue を立てて出さない（scripts/sns_video_followup.py の publish_queued）。下書き（saveToDraft）だけは公開されないので通す。
  9. （2026-11-03 オーナー指摘「可能しました」）壊れた日本語を出さない（な形容詞＋しました・です／ますの重なりなど。GRAMMAR_BREAKS）。
     よく知られた会社に無理な説明を付けない（「AI半導体のNVIDIAが」。headline_normalize と同じ判定）。

違反したら: 投稿しない・ログに出す・GitHub の issue（ラベル sns-rule-violation）を立てるか、開いているものにコメントを足す。
新しく Buffer / Bluesky / Threads の投稿 API を呼ぶ所を作るときは、必ずこの enforce を直前に呼ぶ（tests/test_sns_rules.py が確かめる）。

例外（オーナー承認済みの形。判定をゆるめる種類）
  kind="digest"   朝のまとめ（記事3本の見出し）。問いかけ・要約は無し。画像は号のシェア画像（1200×630）。リンクの決まりは同じ。
  kind="disaster" 地震・津波（AI に文章を書かせない型の文面）。問いかけ・要約は無し。リンクの決まりは同じ。
"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import persona  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
STATE_PATH = ROOT / "state" / "threads_posted.json"
BLUESKY_EXTRA_PATH = ROOT / "state" / "bluesky_extra.json"
ISSUE_LABEL = "sns-rule-violation"
ISSUE_TITLE = "SNSルール違反の投稿を止めました（sns_rules）"
SITE_HOST = "ai-tech-express.jp"

PLATFORMS = ("threads", "x", "facebook", "bluesky")
ALIASES = {"twitter": "x"}
# utm_source の値（threads_post.utm_link と同じ）
UTM_SOURCE = {"threads": "threads", "x": "x", "facebook": "facebook", "bluesky": "bluesky"}
# 文字数の上限（Threads 500・Bluesky 300 書記素・X は全角2の280（URL 23）・Facebook は長文可だが SNS の短い形を守る）
LIMITS = {"threads": 500, "bluesky": 300, "facebook": 600}
X_LIMIT = 280
# 書き出し・要約・問いかけの部分（リンクの行を除く）の目安の上限（本文の形 2026-11-02: 全角120〜150字くらい）
CONTENT_MAX = 200

# 16:9 = 1.778。±8%（1200×630 の 1.905 は通す。4:5・1:1・縦長は通さない）
ASPECT = 16 / 9
ASPECT_TOL = 0.08

SUMMARY = f"{persona.SUMMARY_OPENER}、"
QUESTION_END = ("？", "?")

# ルール3: 決まった型・中身のない呼びかけ・アンケートのふり
BANNED = [
    (re.compile(r"番号で(?:コメント|答え|教え)|番号だけでも"), "「番号でコメント」型の呼びかけ"),
    (re.compile(r"気軽に(?:コメント|教えて|どうぞ)|お気軽に"), "「気軽にコメント」型の呼びかけ"),
    (re.compile(r"コメント(?:で|してもらえ|ください|お待ち|欄で)|教えてください"), "コメントのお願い"),
    (re.compile(r"いいね|フォロー(?:して|お願い)|リポスト|拡散|シェアして|保存して"), "反応のお願い"),
    (re.compile(r"アンケート|投票|AIの問いかけ（"), "アンケート・問いかけだけの投稿"),
    (re.compile(r"(?m)^[①②③④⑤].+\n[①②③④⑤]"), "選択肢を並べた問いかけ（記事のない投稿）"),
    (re.compile(r"ざっくり"), "「ざっくり」"),
    (re.compile(r"(?m)^(?:今は一部の人だけが使えます。|日本からもう使えます。)?主に[^。\n]{1,24}向け。$"), "判定の定型文（「主に◯◯向け。」）"),
]
# ルール6: 書き出しの呼びかけ。読者を名指しする言葉（〜な人・〜の皆さん・〜担当の人・〜ユーザー）＋読点などで呼びかける
CALLOUT_RE = re.compile(
    r"^(?:【速報】)?[^。！？!?\n]{2,30}?(?:人|皆さん|みなさん|方|担当者|ユーザー)(?:[、,]|へ|に(?:は|、)?|は[、,]?|も[、,]?)"
)
CALLOUT_MAX = 40
# ルール6: 使わない書き出し（答えを先に言って、読まなくてよいと思わせる／自分たちの速さの話）
BANNED_OPENERS = [
    (re.compile(r"先に言うと、[^。\n]*?(?:でき(?:ない|ません)|使え(?:ない|ません)|対象外|まだ|ありません|小さめ)"), "「先に言うと、〜できない／対象外」の書き出し"),
    (re.compile(r"(?:公式)?発表から\d+分で(?:掲載|お届け)"), "「発表からN分で掲載」の書き出し"),
]
# ルール6: 記事を読まないと答えられない問い（記事の中身を指す・使ってみたいか聞く）
ARTICLE_BOUND_QUESTION = re.compile(
    r"この(?:ニュース|発表|機能|サービス|製品|モデル|記事|件|話|変更|アプリ|ツール|仕組み|取り組み)|今回の|その(?:機能|発表|サービス)|"
    r"いちばん気になった|どの発表|発表(?:の中|で)|新機能|新モデル|新サービス|使ってみ(?:たい|ます)|試してみ(?:たい|ます)|試したい|試しますか|導入されたら|"
    r"どう思い|気になり|注目して|知っていました|もう試し|の件"
)


# ルール9: 機械で文の終わりを直したときに出る、壊れた日本語（2026-11-03「数コマンドでAIモデルの実行が可能しました」）
# な形容詞・「する」が付かない名詞のあとに直接「し（ます・ました・た）」は付かない（「可能にしました」「必要です」が正しい）
NA_NOUNS = (
    "可能", "不可能", "必要", "不要", "重要", "簡単", "便利", "不便", "困難", "容易", "有効", "無効", "無料", "有料", "十分", "不十分",
    "明らか", "確実", "安全", "危険", "自由", "大切", "好調", "不調", "不可欠", "主流", "最適", "最大", "最小", "最新", "最高", "最低",
    "同様", "同じ", "対象外", "急務", "必須", "高速", "安価", "高価", "豊富", "強力", "手軽", "快適", "正確",
    "活発", "盛ん", "深刻", "顕著", "好評", "不明", "未定", "限定的", "一般的", "本格的", "具体的", "画期的", "現実的", "効果的",
)
GRAMMAR_BREAKS = [
    (re.compile(rf"(?:{'|'.join(sorted(NA_NOUNS, key=len, reverse=True))})し(?:ました|ます|た|ている|ています)"), "な形容詞・名詞に直接「しました」"),
    (re.compile(r"(?:です|ます|ました|でした|ません)(?:です|ました|でした)"), "です・ますが重なっている"),
    (re.compile(r"ます(?:です|ました)|ましたます|でしたます"), "です・ますが重なっている"),
    (re.compile(r"(?<![かくしい])ったです|(?<!な)んだです|(?:した|できた|なった|始めた)です"), "過去形＋「です」"),
    (re.compile(r"ないません|ませんません|しますします|しましたしました"), "語尾が重なっている"),
    (re.compile(r"[をがにへ](?:です|ます|ました)[。]"), "助詞のあとに直接「です・ます」"),
]


def grammar_problems(text: str) -> list[str]:
    """壊れた日本語の言い回し（ルール9）。見つかった所の一覧"""
    out = []
    for pattern, label in GRAMMAR_BREAKS:
        if m := pattern.search(str(text or "")):
            out.append(f"[ルール9] 壊れた日本語（{label}:「{m.group(0)}」）")
    return out


def descriptor_problems(text: str) -> list[str]:
    """よく知られた会社への無理な説明（「AI半導体のNVIDIAが」）。headline_normalize の判定を使う。
    読めなければ不合格（確かめられない検査を合格にしない。2026-11-04 一貫性の点検）"""
    try:
        import headline_normalize as hn  # noqa: PLC0415
    except Exception as error:  # noqa: BLE001
        return [f"[ルール9] 会社名の説明の検査を動かせません（headline_normalize: {type(error).__name__}）"]
    out = []
    for line in str(text or "").splitlines():
        for m in hn._descriptor_matches(line.removeprefix(SUMMARY)):
            out.append(f"[ルール9] よく知られた会社に無理な説明（「{(m.group(2) + 'の' + m.group(3))[:24]}」）。会社名だけにする")
    return out


# 遅れると読者に害がある知らせ（2026-11-03 オーナー承認: 動画を待たずに写真のカードですぐ出してよい）
URGENT_RE = re.compile(
    r"緊急地震速報|震度[1-7１-７５６]|津波(?:警報|注意報|情報)?|噴火(?:警報|警戒|速報)?|火山(?:警報|情報)|大雨(?:特別)?警報|特別警報|"
    r"土砂災害警戒|線状降水帯|記録的短時間大雨|台風\d*号|暴風(?:雨)?警報|高潮警報|避難指示|緊急安全確保|避難勧告"
)


def is_urgent(article: dict | None) -> bool:
    """動画を待たずに出してよい、急ぎの安全の知らせか（災害・警報・遅れると読者に害があるもの）。
    記事の disaster / urgent / safetyAlert、または見出しに地震・津波・噴火・大雨・台風の警報などの言葉がある"""
    article = article or {}
    if article.get("disaster") or article.get("urgent") or article.get("safetyAlert"):
        return True
    return bool(URGENT_RE.search(f"{article.get('title', '')} {article.get('seoTitle', '')}"))


def has_video(media: list[dict] | None) -> bool:
    return any(str(m.get("type") or "") == "video" and m.get("url") for m in media or [])


# ルール7: 要約の文の終わりは「です・ます」（です／ます／でした／ました／ません／でしょう＋ね・よ）
POLITE_END = re.compile(r"(?:です|ます|でした|ました|ません|ませんでした|でしょう)(?:ね|よ)?[。！!]?[」』）)]?$")


# ルール7: 要約に使わない硬い言葉（役所・報道の言い回し）。やさしい言葉に言い換える（threads_post.EASY_WORDS）
HARD_WORDS = re.compile(r"普及・?啓発|啓発|協働|周知|を図る|を図り|に資する|に鑑み|に係る|所要の|講じ|推進する|推進します|展開する|展開します|実施する|実施します|等の|等を|等で")
SUMMARY_SENTENCE_MAX = 70


def summary_sentences(summary: str) -> list[str]:
    """要約のかたまりの文（「要約すると、」を除き、改行と句点で分ける）"""
    body = str(summary or "").strip().removeprefix(SUMMARY)
    return [x.strip() for line in body.splitlines() for x in re.split(r"(?<=[。！!])", line) if x.strip()]


def impolite_sentences(summary: str) -> list[str]:
    """「です・ます」で終わっていない要約の文（ルール7）"""
    return [x for x in summary_sentences(summary) if not POLITE_END.search(x)]


def is_fast_news(article: dict | None) -> bool:
    """【速報】を付けてよい記事か（画像の赤い「速報」と同じ決まり。lib/cover-path.ts の isFastNews）。
    まとめ記事は付けない。主要企業の公式発表の速報（article.breaking）か、公式発表から60分以内に載せた記事（speedMinutes）だけ"""
    article = article or {}
    if article.get("type") == "roundup":
        return False
    minutes = article.get("speedMinutes")
    return bool(article.get("breaking")) or (isinstance(minutes, (int, float)) and not isinstance(minutes, bool))


def question_needs_article(question: str) -> bool:
    """記事を読まないと答えられない問いか（ルール6）"""
    return bool(ARTICLE_BOUND_QUESTION.search(str(question or "")))


def callout_ok(line: str) -> bool:
    """1行目が読者を名指しする呼びかけか（ルール6）"""
    line = str(line or "").strip()
    return bool(line) and len(line) <= CALLOUT_MAX and bool(CALLOUT_RE.match(line))


# 中身のない問い（どの記事にも付く問い）
GENERIC_QUESTION = re.compile(
    r"^(?:みなさん|皆さん|あなた)?(?:は)?(?:どう(?:思い|感じ)ますか|いかがですか|どうですか|気になりますか|知っていましたか|使ってみたいですか|"
    r"試してみたいですか|注目していますか|興味ありますか|興味はありますか)[？?]$"
)
URL_RE = re.compile(r"https?://[^\s<>「」]+|(?<![\w/.])ai-tech-express\.jp/[^\s<>「」]*")
HASHTAG_RE = re.compile(r"(?<!\S)#\S+")
# 英語の文: 5語以上の英単語の並び（製品名は3〜4語までが多い）
ENGLISH_RUN = re.compile(r"[A-Za-z][A-Za-z'’\-]*(?:[ ,]+[A-Za-z][A-Za-z'’\-]*){4,}")
JAPANESE_CHAR = re.compile(r"[぀-ヿ一-鿿ｦ-ﾟ]")
LINK_LINE = re.compile(r"^(?:詳しくはこちら|記事はこちら|続きはこちら)")
# リンクの案内に添える行（動画の案内・音声のクレジット）も、書き出し・要約・問いかけの形の判定からは除く
NOTE_LINE = re.compile(r"^(?:音声：|画像：|公式ページの画面を見ながら、\d+秒|\d+秒の動画で解説)")
# 文字の帯がある画像（見出し入りのカバー・号の縦長画像・以前の縦長カード）
TEXT_BAND_PATHS = [re.compile(r"/cover/[^/?#]+"), re.compile(r"/digest/[^/]+/card\.png")]
DIGEST_SHARE = re.compile(r"/digest/[^/]+/share\.png")
# サイトが返す画像の大きさ（取りに行けないときの予備。lib/card-designs.tsx の p1 と app/cover・digest の share）
KNOWN_SIZES = [
    (re.compile(r"/card/[^/?#]+"), (1200, 675)),
    (re.compile(r"/cover/[^/?#]+"), (1200, 630)),
    (re.compile(r"/digest/[^/]+/share\.png"), (1200, 630)),
    (re.compile(r"/digest/[^/]+/card\.png"), (1080, 1350)),
]


class SnsRuleViolation(RuntimeError):
    """ルール違反で投稿を止めた（Buffer の MutationError と区別できるよう、"Buffer: " では始めない）"""

    def __init__(self, violations: list[str], platform: str = "", source: str = ""):
        self.violations = list(violations)
        super().__init__(f"SNSルール違反のため投稿しません（{platform}/{source}）: " + " / ".join(violations))


def norm_platform(platform: str) -> str:
    p = ALIASES.get(str(platform or "").lower(), str(platform or "").lower())
    if p not in PLATFORMS:
        raise ValueError(f"知らない投稿先: {platform}")
    return p


def x_weight(text: str) -> int:
    """X の文字数（全角2・URL 23）"""
    urls = re.findall(r"https?://\S+", text)
    rest = re.sub(r"https?://\S+", "", text)
    return sum(1 if ord(c) < 0x1100 else 2 for c in rest) + 23 * len(urls)


# ---- 画像・動画の縦横 ----

def _png(head: bytes):
    if head.startswith(b"\x89PNG\r\n\x1a\n") and head[12:16] == b"IHDR":
        return struct.unpack(">II", head[16:24])
    return None


def _jpeg(data: bytes):
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        size = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + size
    return None


def _webp(data: bytes):
    if not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    if chunk == b"VP8 " and len(data) >= 30:
        w, h = struct.unpack("<HH", data[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if chunk == b"VP8L" and len(data) >= 25:
        b = data[21:25]
        return 1 + (((b[1] & 0x3F) << 8) | b[0]), 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
    return None


def _mp4(data: bytes):
    """mp4 の映像トラックの tkhd から幅・高さ（moov が先頭にあるとき。faststart）"""
    best = None
    for m in re.finditer(b"tkhd", data):
        start = m.start() + 4
        version = data[start] if start < len(data) else 0
        # version+flags(4)・作成/更新時刻・トラックID・予約・長さ（v0 は 4 バイトずつ、v1 は時刻と長さが 8 バイト）
        off = start + (4 + 4 + 4 + 4 + 4 + 4 if version == 0 else 4 + 8 + 8 + 4 + 4 + 8) + 8 + 2 + 2 + 2 + 2 + 36
        if off + 8 <= len(data):
            w, h = struct.unpack(">II", data[off:off + 8])
            w, h = w >> 16, h >> 16
            if w and h:
                best = (w, h)
    return best


def measure_bytes(data: bytes):
    return _png(data) or _jpeg(data) or _webp(data) or _mp4(data)


_SIZE_CACHE: dict[str, tuple[int, int] | None] = {}


def fetch_size(url: str, limit: int = 1_048_576) -> tuple[int, int] | None:
    """URL の先頭だけ読んで縦横を測る。測れなければ None"""
    if url in _SIZE_CACHE:
        return _SIZE_CACHE[url]
    size = None
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https":
            req = urllib.request.Request(url, headers={"User-Agent": "saisoku-sns-rules/1.0", "Range": f"bytes=0-{limit - 1}", "X-Saisoku-Internal": "1"})
            with urllib.request.urlopen(req, timeout=20) as res:
                size = measure_bytes(res.read(limit))
    except (OSError, ValueError, urllib.error.URLError):
        size = None
    _SIZE_CACHE[url] = size
    return size


def known_size(url: str) -> tuple[int, int] | None:
    parsed = urllib.parse.urlsplit(str(url or ""))
    if parsed.hostname and parsed.hostname.endswith(SITE_HOST):
        for pattern, size in KNOWN_SIZES:
            if pattern.fullmatch(parsed.path):
                return size
    return None


def media_size(item: dict, measurer=None) -> tuple[int, int] | None:
    if item.get("width") and item.get("height"):
        return int(item["width"]), int(item["height"])
    url = str(item.get("url") or "")
    measured = (measurer or fetch_size)(url) if url else None
    return measured or known_size(url)


def is_text_band(url: str) -> bool:
    path = urllib.parse.urlsplit(str(url or "")).path
    return any(p.fullmatch(path) for p in TEXT_BAND_PATHS)


# ---- 本文 ----

def strip_links(text: str) -> str:
    """リンク・リンク前置きの行・ハッシュタグを除いた中身"""
    lines = []
    for line in str(text or "").splitlines():
        s = URL_RE.sub("", HASHTAG_RE.sub("", line)).strip()
        if LINK_LINE.match(s) or NOTE_LINE.match(s):
            continue
        lines.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def blocks_of(text: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]


def article_paths(article: dict | None, kind: str) -> list[str]:
    """この投稿が指すべきサイトのパス（記事は /articles/<slug>、朝のまとめは号のページ）"""
    article = article or {}
    if kind == "digest":
        link = str(article.get("link") or "")
        path = urllib.parse.urlsplit(link if "://" in link else f"https://{link}").path if link else ""
        return [path] if path else []
    slug = str(article.get("slug") or "")
    return [f"/articles/{slug}"] if slug else []


def links_in(text: str) -> list[str]:
    return [u.rstrip("。、）)") for u in URL_RE.findall(str(text or ""))]


def _url(u: str) -> urllib.parse.SplitResult:
    return urllib.parse.urlsplit(u if "://" in u else f"https://{u}")


def points_to(url: str, paths: list[str]) -> bool:
    parsed = _url(url)
    return bool(parsed.hostname and parsed.hostname.endswith(SITE_HOST) and parsed.path.rstrip("/") in [p.rstrip("/") for p in paths])


def has_utm(url: str, platform: str) -> bool:
    q = urllib.parse.parse_qs(_url(url).query)
    return q.get("utm_source") == [UTM_SOURCE[platform]] and q.get("utm_medium") == ["social"]


def check_link(platform: str, text: str, reply_text: str, links: list[str], paths: list[str]) -> list[str]:
    out = []
    if not paths:
        return ["[ルール1] リンク先の記事が分からない"]
    body_links = [u for u in links_in(text) if points_to(u, paths)]
    reply_links = [u for u in links_in(reply_text) if points_to(u, paths)]
    extra = [u for u in links if u and points_to(u, paths)]
    if platform == "x":
        if body_links:
            out.append("[ルール1] X は記事のリンクを本文ではなく返信に置く")
        if not reply_links:
            out.append("[ルール1] X の返信に記事のリンクがない")
        tagged = reply_links
    else:
        if not body_links:
            out.append("[ルール1] 本文に記事のリンクがない（X 以外はリンクを本文に置く）")
        tagged = body_links + extra
    if (body_links or reply_links) and not any(has_utm(u, platform) for u in tagged):
        out.append(f"[ルール1] 記事のリンクに utm_source={UTM_SOURCE[platform]}&utm_medium=social がない")
    return out


def check_structure(platform: str, content: str) -> list[str]:
    """書き出し → 要約すると、＋事実2文 → 問いかけ"""
    out = []
    blocks = blocks_of(content)
    summary = [b for b in blocks if b.startswith(SUMMARY)]
    count = content.count(persona.SUMMARY_OPENER)
    if not summary:
        out.append(f"[ルール1] 「{SUMMARY}」で始まる要約のかたまりがない")
    elif count > 1:
        out.append(f"[ルール4] 「{persona.SUMMARY_OPENER}」が{count}回ある")
    else:
        sentences = [s for s in re.split(r"(?<=[。！!])", summary[0].removeprefix(SUMMARY)) if s.strip()]
        if not 1 <= len(sentences) <= 3:
            out.append(f"[ルール1] 要約の文が{len(sentences)}つ（短い事実の文を2つ）")
        for bad in impolite_sentences(summary[0]):
            out.append(f"[ルール7] 要約の文が「です・ます」になっていない（「…{bad[-16:]}」）。やさしい です・ます の文にする")
        if m := HARD_WORDS.search(summary[0]):
            out.append(f"[ルール7] 要約に硬い言葉（「{m.group(0)}」）。やさしい言葉に言い換える")
        for long in [x for x in summary_sentences(summary[0]) if len(x) > SUMMARY_SENTENCE_MAX]:
            out.append(f"[ルール7] 要約の文が長すぎる（{len(long)}字 / {SUMMARY_SENTENCE_MAX}字まで）")
    if platform != "x" and (len(blocks) < 3 or blocks[0].startswith(SUMMARY)):
        out.append("[ルール1] 最初に書き出しの行がない")
    question = blocks[-1] if blocks else ""
    if not question.endswith(QUESTION_END):
        out.append("[ルール1] 最後が読者への問いかけ（？）になっていない")
    elif summary and blocks.index(summary[0]) > len(blocks) - 2:
        out.append("[ルール1] 問いかけが要約より前にある")
    elif "\n" in question or len(question) < 10 or GENERIC_QUESTION.match(question) or GENERIC_QUESTION.match(re.sub(r"^[^、]{1,6}、", "", question)):
        out.append(f"[ルール3] 問いかけが決まり文句・中身がない（「{question[:30]}」）")
    if len(content) > CONTENT_MAX:
        out.append(f"[ルール1] 書き出し・要約・問いかけが長すぎる（{len(content)}字 / {CONTENT_MAX}字まで）")
    return out


def check_opener(platform: str, text: str, content: str, article: dict | None) -> list[str]:
    """ルール6: 書き出しの呼びかけ・【速報】の付け方・使わない書き出し・記事を読まないと答えられない問い"""
    out = []
    for pattern, label in BANNED_OPENERS:
        if m := pattern.search(str(text or "")):
            out.append(f"[ルール6] {label}（「{m.group(0)[:24]}」）")
    blocks = blocks_of(content)
    first = blocks[0].splitlines()[0].strip() if blocks else ""
    # X は文字数で書き出しを落とすことがある（要約から始まる形）。書き出しがあるときだけ確かめる
    if first and not (platform == "x" and first.startswith(SUMMARY)):
        if not callout_ok(first):
            out.append(f"[ルール6] 1行目が読者への呼びかけ（「〇〇な人、これは要チェックです」など）になっていない（「{first[:24]}」）")
        if first.startswith("【速報】") and not is_fast_news(article):
            out.append("[ルール6] 速報ではない記事に【速報】を付けている")
    question = blocks[-1] if blocks else ""
    if question.endswith(QUESTION_END) and question_needs_article(question):
        out.append(f"[ルール6] 問いかけが記事を読まないと答えられない（「{question[:30]}」）。読者自身の好み・経験を聞く")
    return out


def check_language(text: str) -> list[str]:
    out = []
    plain = URL_RE.sub("", HASHTAG_RE.sub("", str(text or "")))
    if m := ENGLISH_RUN.search(plain):
        out.append(f"[ルール4] 英語の文が残っている（「{m.group(0)[:40]}」）")
    # 英単語は1語を1つと数え、日本語の文字が英単語の2倍に届かなければ日本語の投稿ではない
    words = len(re.findall(r"[A-Za-z]+", plain))
    japanese = len(JAPANESE_CHAR.findall(plain))
    if (words or japanese) and japanese < 2 * words:
        out.append("[ルール4] 日本語の投稿になっていない")
    if m := persona.STIFF_RE.search(plain):
        out.append(f"[ルール4] 使わない言い回し（「{m.group(0)}」）")
    if m := persona.ADVICE_RE.search(plain):
        out.append(f"[ルール4] 儲け話・投資助言の表現（「{m.group(0)}」）")
    return out


def product_launch(article: dict) -> bool:
    """製品の発表の記事か（scripts/images.py の判定と同じ）。判定できなければ True（製品として扱い、AI の画像を使わせない。
    確かめられない検査を合格にしない。2026-11-04 一貫性の点検）"""
    try:
        import images  # noqa: PLC0415

        return bool(images.product_launch(article))
    except Exception:  # noqa: BLE001
        return True


def uses_article_photo(url: str, article: dict) -> bool:
    """記事の写真を使う画像か（写真だけのカード /card/<slug> か、記事の写真そのもの）"""
    path = urllib.parse.urlsplit(str(url or "")).path
    return path.rstrip("/") == f"/card/{article.get('slug')}" or (bool(article.get("thumbnail")) and str(url) == str(article.get("thumbnail")))


def check_media(platform: str, media: list[dict], article: dict | None, kind: str, measurer=None) -> list[str]:
    out = []
    article = article or {}
    credit = article.get("imageCredit") or {}
    for item in media or []:
        url = str(item.get("url") or "")
        kind_of = "動画" if item.get("type") == "video" else "画像"
        if item.get("type") != "video" and is_text_band(url):
            out.append(f"[ルール2] 文字の帯が入った画像（{urllib.parse.urlsplit(url).path}）。写真だけのカード（/card/）を使う")
            continue
        if item.get("type") != "video" and DIGEST_SHARE.search(urllib.parse.urlsplit(url).path) and kind != "digest":
            out.append("[ルール2] 朝のまとめ以外で号のシェア画像を使っている")
            continue
        if item.get("type") != "video" and credit.get("kind") == "ai" and uses_article_photo(url, article) and product_launch(article):
            out.append("[ルール2] 発表された製品を AI で描いた画像")
        if item.get("card") and platform == "bluesky":
            continue  # Bluesky のリンクカードの小さな画像は Bluesky が切り抜く（元の画像は上で確かめる）
        size = media_size(item, measurer)
        if not size:
            out.append(f"[ルール2] {kind_of}の縦横を確かめられない（{url[:80]}）")
            continue
        ratio = size[0] / max(size[1], 1)
        if abs(ratio - ASPECT) / ASPECT > ASPECT_TOL:
            out.append(f"[ルール2] {kind_of}が 16:9 でない（{size[0]}×{size[1]}）")
    return out


def check_length(platform: str, text: str, reply_text: str) -> list[str]:
    if platform == "x":
        out = []
        if x_weight(text) > X_LIMIT:
            out.append(f"[長さ] X の本文が長すぎる（{x_weight(text)} / {X_LIMIT}）")
        if reply_text and x_weight(reply_text) > X_LIMIT:
            out.append(f"[長さ] X の返信が長すぎる（{x_weight(reply_text)} / {X_LIMIT}）")
        return out
    n = len(str(text or ""))
    return [f"[長さ] {platform} の本文が長すぎる（{n} / {LIMITS[platform]}字）"] if n > LIMITS[platform] else []


def check_sports(article: dict | None, text: str) -> list[str]:
    article = article or {}
    if article.get("genreSlug") != "sports":
        return []
    ok, why = persona.sports_scope(f"{article.get('title', '')} {' '.join(article.get('tags') or [])} {text}")
    return [] if ok else [f"[ルール4] スポーツは日本のスポーツだけ（{why}）"]


class PostingStateUnknown(RuntimeError):
    """投稿の記録を読めない（1記事1投稿を確かめられない → 出さない。RuntimeError なので呼ぶ側は次の回にやり直す）"""


def already_posted(platform: str, article: dict | None, kind: str, state: dict | None = None, extra: dict | None = None) -> bool:
    """この記事をこの SNS にもう出したか（state/threads_posted.json・state/bluesky_extra.json）"""
    slug = str((article or {}).get("slug") or "")
    if not slug or kind == "digest":
        return False
    if state is None:
        try:
            state = json.loads(STATE_PATH.read_text("utf-8"))
        except FileNotFoundError:
            state = {}  # 記録がまだ無い（初めての投稿）
        except (OSError, ValueError) as error:
            # 記録が壊れていて読めない → 出したかを確かめられないので出さない（S20。次の回にやり直す）
            raise PostingStateUnknown(f"投稿の記録 {STATE_PATH.name} を読めません: {type(error).__name__}") from error
    entry = (state.get("posted") or {}).get(slug) or {}
    channels = entry.get("channels") or {}
    if platform == "threads":
        return bool(entry.get("id"))
    if platform == "x":
        return bool(channels.get("twitter"))
    if platform == "facebook":
        return bool(channels.get("facebook"))
    if extra is None:
        try:
            extra = json.loads(BLUESKY_EXTRA_PATH.read_text("utf-8"))
        except FileNotFoundError:
            extra = {}
        except (OSError, ValueError) as error:
            raise PostingStateUnknown(f"Bluesky の記録 {BLUESKY_EXTRA_PATH.name} を読めません: {type(error).__name__}") from error
    return bool(channels.get("bluesky") or (extra.get(slug) or {}).get("uri"))


def check_post(
    platform: str,
    text: str,
    media: list[dict] | None = None,
    article: dict | None = None,
    *,
    kind: str = "article",
    reply_text: str = "",
    links: list[str] | None = None,
    history: bool = False,
    state: dict | None = None,
    measurer=None,
    draft: bool = False,
    pending_video: bool = False,
) -> list[str]:
    """投稿1件のルール違反の一覧（空なら OK）。

    draft: 下書き（公開しない）。動画の決まり（ルール8）は見ない
    pending_video: 動画を付ける前の文面の下見（threads_post が予約の前に文面だけ確かめる）。動画の決まりは見ない。
      実際に Buffer / Bluesky に送るときは必ず pending_video=False で通す（動画が無ければ止まる）

    platform: threads / x（twitter）/ facebook / bluesky
    text: 本文。reply_text: X の返信（リンクを置く所）。links: 本文の外のリンク（Bluesky の facet・リンクカード）
    media: [{"type": "image"|"video", "url": ..., "width"?, "height"?}]
    article: 記事の JSON（slug・title が要る）。kind="digest" のときは {"link": 号のページ, "items": [記事...]}
    history: True ならこの記事をこの SNS にもう出していないかも確かめる（ルール5）
    """
    platform = norm_platform(platform)
    if kind == "article" and (article or {}).get("disaster"):
        kind = "disaster"
    out: list[str] = []
    article = article or None
    if kind == "video_followup":
        out.append("[ルール5] 動画だけの別の投稿は出さない（動画は記事の投稿に添える）")
    if not article:
        out.append("[ルール1] 記事のない投稿（実在する記事についての投稿だけを出す）")
    elif kind == "digest":
        if len(article.get("items") or []) < 2:
            out.append("[ルール1] 朝のまとめに記事が2本未満")
        if "/digest/" not in str(article.get("link") or ""):
            out.append("[ルール1] 朝のまとめのリンク先が号のページ（/digest/<日付>）でない")
        for line in str(text or "").splitlines():
            item = line.strip()
            if item.startswith("・") and (len(item) < 10 or not re.search(r"[\u3040-\u309f\u30a0-\u30fa\u30fc\u4e00-\u9fff]", item[1:])):
                out.append(f"[ルール1] 朝のまとめの見出しが短すぎて中身が分からない（「{item[:20]}」）")
    elif not article.get("slug") or not str(article.get("title") or "").strip():
        out.append("[ルール1] 記事の slug・見出しが分からない")
    elif (ARTICLES_DIR / f"{article['slug']}.json").parent.exists() and not (ARTICLES_DIR / f"{article['slug']}.json").exists() and not article.get("_virtual"):
        out.append(f"[ルール1] 記事が見つからない（{article['slug']}）")
    out += check_link(platform, text, reply_text, links or [], article_paths(article, kind))
    content = strip_links(text)
    if kind not in ("digest", "disaster"):
        out += check_structure(platform, content)
        out += check_opener(platform, text, content, article)
    for pattern, label in BANNED:
        if m := pattern.search(f"{text}\n{reply_text}"):
            out.append(f"[ルール3] {label}（「{m.group(0)[:20]}」）")
    out += check_language(f"{content}\n{strip_links(reply_text)}")
    out += grammar_problems(f"{content}\n{strip_links(reply_text)}")
    if kind != "digest":
        out += descriptor_problems(content)
    # ルール8: 記事の投稿は動画つきだけ（朝のまとめだけ画像）。写真・カードだけの投稿は出さない
    if kind not in ("digest", "disaster") and not is_urgent(article) and not draft and not pending_video and not has_video(media):
        out.append("[ルール8] 動画が付いていない（記事の投稿は必ず動画つき。写真・カードだけでは出さない。動画が間に合わなければ遅らせる）")
    out += check_media(platform, media or [], article, kind, measurer)
    out += check_length(platform, text, reply_text)
    out += check_sports(article, content)
    if history and article and already_posted(platform, article, kind, state):
        out.append("[ルール5] この記事はこの SNS にもう投稿済み（1つの記事につき投稿は1つ）")
    return list(dict.fromkeys(out))


# ---- 違反の記録（ログ・ジョブサマリー・GitHub の issue） ----

def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def report(violations: list[str], platform: str, source: str, text: str, article: dict | None, draft: bool = False) -> None:
    slug = str((article or {}).get("slug") or (article or {}).get("link") or "（記事なし）")
    _log(f"[sns-rules] 投稿を止めました {platform}/{source} {slug}")
    for v in violations:
        _log(f"  - {v}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    lines = [f"### SNSルール違反で止めた投稿（{platform} / {source}）", "", f"記事: {slug}", "", *[f"- {v}" for v in violations], "", "```", str(text)[:600], "```", ""]
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass
    if draft or os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("SNS_RULES_NO_ISSUE") == "1":
        return
    open_issue(lines)


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=60).stdout.strip()


def open_issue(lines: list[str]) -> None:
    """ラベル sns-rule-violation の開いている issue にコメントを足す。無ければ立てる。失敗しても投稿の処理は止めない"""
    run = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    body = "\n".join(lines + [f"実行: {run}"])
    try:
        found = _gh("issue", "list", "--label", ISSUE_LABEL, "--state", "open", "--limit", "1", "--json", "number", "--jq", ".[0].number // empty")
        if found:
            _gh("issue", "comment", found, "--body", body)
            _log(f"[sns-rules] issue #{found} にコメントしました")
            return
        subprocess.run(["gh", "label", "create", ISSUE_LABEL, "--color", "B60205", "--description", "SNSルールの門番が投稿を止めた", "--force"],
                       check=False, capture_output=True, text=True, timeout=60)
        intro = ("SNS 投稿のルールの門番（scripts/sns_rules.py）が、ルールに合わない投稿を止めました。投稿はされていません。\n"
                 "直したら、この issue を閉じてください（次に止めたときは新しい issue が立ちます）。\n\n")
        url = _gh("issue", "create", "--title", ISSUE_TITLE, "--label", ISSUE_LABEL, "--body", intro + body)
        _log(f"[sns-rules] issue を立てました: {url}")
    except Exception as error:  # noqa: BLE001
        _log(f"[sns-rules] issue を立てられませんでした: {type(error).__name__}")


def enforce(
    platform: str,
    text: str,
    media: list[dict] | None = None,
    article: dict | None = None,
    *,
    source: str,
    kind: str = "article",
    reply_text: str = "",
    links: list[str] | None = None,
    history: bool = True,
    draft: bool = False,
    state: dict | None = None,
    measurer=None,
    pending_video: bool = False,
) -> None:
    """投稿 API を呼ぶ直前に呼ぶ。違反があれば記録して SnsRuleViolation を投げる（呼び出し側は投稿しない）。
    pending_video=True は予約の前の文面の下見だけ（API は呼ばない所）。送る直前は必ず False（動画が無ければ止まる）"""
    violations = check_post(platform, text, media, article, kind=kind, reply_text=reply_text, links=links,
                            history=history and not draft, state=state, measurer=measurer, draft=draft, pending_video=pending_video)
    if violations:
        report(violations, norm_platform(platform), source, text, article, draft=draft)
        raise SnsRuleViolation(violations, norm_platform(platform), source)


# ---- Buffer の createPost の入力から、SNS ごとの本文・返信・画像を取り出す ----

def buffer_parts(service: str, post_input: dict) -> tuple[str, str, list[dict], list[str]]:
    """(本文, 返信, 画像・動画, 本文の外のリンク)"""
    service = str(service).lower()
    meta = post_input.get("metadata") or {}
    key = {"x": "twitter"}.get(service, service)
    thread = ((meta.get(key) or {}).get("thread")) or []
    text = (thread[0].get("text") if thread else None) or post_input.get("text") or ""
    replies = [t.get("text") or "" for t in thread[1:]]
    assets = list(post_input.get("assets") or []) + (list(thread[0].get("assets") or []) if thread else [])
    media = []
    for a in assets:
        if "video" in a:
            media.append({"type": "video", **{k: v for k, v in a["video"].items() if k in ("url", "width", "height")}})
        elif "image" in a:
            media.append({"type": "image", **{k: v for k, v in a["image"].items() if k in ("url", "width", "height")}})
    links = []
    for t in thread[1:]:
        att = ((t.get("metadata") or {}).get("threads") or {}).get("linkAttachment") or {}
        if att.get("url"):
            links.append(att["url"])
    fb = ((meta.get("facebook") or {}).get("linkAttachment")) or {}
    if fb.get("url"):
        links.append(fb["url"])
        thumb = (fb.get("thumbnail") or {}).get("url")
        if thumb:
            media.append({"type": "image", "url": thumb})
    return text, "\n".join(replies), media, links


def enforce_buffer(service: str, post_input: dict, article: dict | None, *, source: str, kind: str = "article", history: bool = True, measurer=None,
                   pending_video: bool = False, pausing: bool = False) -> None:
    """Buffer の createPost / editPost の直前に呼ぶ（threads_post.create_post・sns_video_followup.edit_post が呼ぶ）。
    すでにある投稿の中身を変える editPost は history=False（同じ投稿なので「もう投稿済み」とは数えない）"""
    if pausing:
        # 予約中の投稿を下書きに戻すだけ（公開しない）。Buffer は本文の付いた編集しか受け付けないので本文を送るが、内容の決まりは見ない
        if post_input.get("saveToDraft") is not True or post_input.get("mode") or post_input.get("dueAt"):
            raise SnsRuleViolation(["[ルール8] 下書きに戻す編集に、公開・予約の指定が入っている"], norm_platform(service), source)
        return
    text, reply, media, _links = buffer_parts(service, post_input)
    platform = norm_platform(service)
    # X 以外は返信を作らない（リンクは本文）。返信やリンクカードにだけリンクがある形は、本文のリンク無しとして止める
    enforce(platform, text, media, article, source=source, kind=kind, reply_text=reply if platform == "x" else "",
            history=history, draft=bool(post_input.get("saveToDraft")), measurer=measurer, pending_video=pending_video)


# ---- 点検（scripts/sns_audit.py が実際の投稿に使う。記事は本文のリンクから探す） ----

def audit_post(post: dict) -> list[str]:
    platform = norm_platform(post.get("platform") or "")
    text = str(post.get("text") or "")
    replies = "\n".join(post.get("replies") or [])
    urls = links_in(text) + links_in(replies) + list(post.get("links") or [])
    slug, digest = "", ""
    for u in urls:
        path = _url(u).path
        if m := re.fullmatch(r"/articles/([^/]+)/?", path):
            slug = m.group(1)
            break
        if path.startswith("/digest/") or path.rstrip("/") == "/news":
            digest = u
    if post.get("reply"):
        return []
    kind, article = "article", None
    if slug:
        path = ARTICLES_DIR / f"{slug}.json"
        try:
            article = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            article = {"slug": slug, "title": slug, "_virtual": True}
    elif digest or re.search(r"のAIニュース\d本", text):
        kind = "digest"
        article = {"link": digest, "items": [x for x in text.splitlines() if x.startswith("・")]}
    media = [m for m in post.get("media") or [] if m.get("url")]
    return check_post(platform, text, media, article, kind=kind, reply_text=replies if platform == "x" else "",
                      links=list(post.get("links") or []))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="投稿文をルールで確かめる（投稿しない）")
    parser.add_argument("platform")
    parser.add_argument("--slug", default="")
    parser.add_argument("--text", required=True)
    parser.add_argument("--reply", default="")
    args = parser.parse_args()
    art = json.loads((ARTICLES_DIR / f"{args.slug}.json").read_text("utf-8")) if args.slug else None
    for v in check_post(args.platform, args.text.replace("\\n", "\n"), [], art, reply_text=args.reply.replace("\\n", "\n")):
        print(v)
