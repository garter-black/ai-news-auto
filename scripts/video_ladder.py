#!/usr/bin/env python3
"""SNS 動画の「段の下がり方」（fallback ladder）。動画が作れないせいで投稿が止まらないように、必ず合格する動画を1本作る。

2026-11-04 オーナー:「動画がダメなら投稿しない」は「投稿が止まる」ことではない。合格する動画を必ず作る。
上の段から順に試し、最初に合格した段の動画を使う（版ごと。サイト用＝full・SNS 用＝short）。

  L1: 今の型（shorts_v3.make_versions。公式ページの画面＋選択の青＋中身に合わせた型）
  L2: L1 をもう1回（新しく撮り直し・台本も書き直し。前の回の検査で落ちた理由を台本の決まりに足す）
  L3: 公式の写真・図（記事の imageCredit が official の写真と bodyImages）を、ゆっくり寄る動き（Ken Burns）で見せ、
      読み上げと同じ文の日本語の字幕を出す。画面の文字を塗る（DOM の選択の青）ことはしないので、塗りのずれ・行の頭の切れは起きない。
      台本は L1/L2 が書いた台本の場面（画面を指す言い方の無いもの）か、記事の文から機械で作る。AI で描いた画像は使わない（I2）
  L4: 最小でも良い動画: 公式の写真1枚（無ければ無地のブランドのカード）＋要点の短い行（記事の見出し・FAQ の問いをそのまま）＋
      記事の文をそのまま読む九州そらの声＋記事へ誘う温かい締め。LLM を使わずに作れる（全部の LLM が止まっても作れる）

どの段も 16:9 1920x1080・30fps・VOICEVOX 九州そら・-14 LUFS・2つの版（full / short）。
出力は sns_video_followup.run が読む所: OUT/<slug>__v3.mp4（full）と OUT/<slug>__v3short.mp4（short）。
戻り値は shorts_v3.make_versions と同じ形 {"full": report, "short": report}。report には
  report["level"]  = "L1".."L4"（使った段）
  report["ladder"] = [{"level", "ok", "reasons", "seconds", "variants"}]（試した段ごと）
  report["gate"]   = {"ok", "reasons", "warnings", "metrics", "qa": {"geometry", "vision", "version": 1, ...}}

L3/L4 の検査（qa）
  geometry: DOM の塗りが無いので塗りのずれは起きない（作りで満たす）。それでも、文字が安全領域・枠の中・文字の面積の上限・
            文字どうし／文字と画像のかぶり無し・画面の数字はすべて記事にある・字幕をつなぐと読み上げと同じ・
            要点の行と読み上げが同じ話（記事の同じ FAQ の問いと答え、または文字の重なり）を確かめる。
  vision:   書き出した動画のコマ（場面ごと）を Claude API（llm の purpose="video_review" の予算・配分）に画像で見せて確かめる。
            API が使えないときは、L4 に限って「画面の文字がすべて記事の文そのまま（または決まった定型の文）で、上の検査が全部通った」
            ときだけ vision を合格にする（qa["vision_how"]="deterministic-L4"）。L3 は API のレビューが無ければ不合格（→ L4 へ）。

時間: 全体で deadline_s 秒（既定 720 秒＝12分）。L1/L2 は別のプロセスで動かし、時間が来たら止める。
残りが少なければ L2・L3 を飛ばして L4 へ（L4 は2本で 90 秒以内が目安）。

  python3 scripts/video_ladder.py <slug>                       # 段を順に試す（投稿しない・置かない）
  python3 scripts/video_ladder.py <slug> --levels L4 --tts fake  # L4 だけ（声なしの試作）
  python3 scripts/video_ladder.py --plan-check 20              # 直近の SNS 向けの記事 20 本で L3/L4 の計画と検査だけ（描画しない）
  python3 scripts/video_ladder.py --dry-run 20 --levels L3,L4  # 20 本を書き出して段ごとの合格率（投稿しない）
  python3 scripts/video_ladder.py --stats [--alert]            # state/sns_video.json の直近20本の段の内訳（L3/L4 が3割を超えたら issue）
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

LEVELS = ("L1", "L2", "L3", "L4")
VARIANTS = ("full", "short")
QA_VERSION = 1
DEADLINE_S = float(os.environ.get("VIDEO_LADDER_DEADLINE", "720"))
L4_RESERVE_S = 90.0  # L4 の2本（声・描画・検査・レビュー）に残しておく時間
MIN_SECONDS = {"L1": 240.0, "L2": 240.0, "L3": 100.0, "L4": 0.0}  # この段を始めるのに要る残り時間（足りなければ飛ばす）
STATE = ROOT / "state" / "sns_video.json"
FALLBACK_LABEL = "sns-video-fallback"
FALLBACK_SHARE = 0.30
FALLBACK_WINDOW = 20
FALLBACK_MIN = 5  # 記録がこれより少ないときは知らせない（数本で3割はぶれが大きい）

# 長さ（秒）。L3 は今の短い版と同じ、L4 は短い版 15〜30秒
RANGES = {
    ("L3", "short"): (20.0, 45.0), ("L3", "full"): (25.0, 75.0),
    ("L4", "short"): (15.0, 30.0), ("L4", "full"): (15.0, 60.0),
}  # fmt: skip
CPS = 5.0  # 九州そら・速さ1.38 で1秒に約5字（docs/sns-video-v3-20261102.md）

W, H, FPS = 1920, 1080, 30
SAFE = (40, 30, W - 40, H - 30)
STAGE = (243, 245, 249)
INK = (11, 26, 51)
BLUE = (29, 74, 143)
ACCENT = (200, 34, 26)
MUTED = (84, 100, 128)
SOFT = (90, 103, 126)
WHITE = (255, 255, 255)
NAVY_TOP = (11, 26, 51)
NAVY_BOTTOM = (29, 74, 143)
FONT_FAMILY = "Noto Sans JP"
LOGO_FAMILY = "Dela Gothic One"

# 割り付け（x0, y0, x1, y1）
LOGO_XY = (104, 58)
COL = (104, 150, 660, 700)  # 左の列: 小見出し＋要点（つかみは題）
IMG = (720, 64, 1816, 680)  # 右: 公式の写真（16:9）か、無地のブランドのカード
SUB = (104, 712, 1816, 884)  # 下: 読み上げと同じ字幕（1〜2行）
CREDIT = (104, 980, 1816, 1040)  # 一番下: クレジットの小さな1行
RADIUS = 24
TEXT_RATIO_MAX = 0.18  # 1フレームの文字の面積（v3 と同じ上限）

LEAD, GAP, END_HOLD = 0.15, 0.32, 1.0


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# 1. 記事から: 公式の画像・文・要点（LLM なし）
# --------------------------------------------------------------------------- #

_PAREN = re.compile(r"（[^（）]{0,40}）|\([^()]{0,40}\)")
_SPACE = re.compile(r"[\s　]+")
_NOT_NARRATION = re.compile(r"元記事|この抜粋|本記事|この記事|上の表|下の表|上の画像|下の画像|この画像|上の図|下の図|図[0-9０-９]|URL|https?://|#|\*\*|\||→|次のとおり|以下の|下記")
_CONNECTIVE = re.compile(r"^(?:ただし|また|さらに|一方|なお|そのため|これにより|このため|その後|そこで|同社|同氏|同プラン|同ツール|これ[はが]|それ[はが])")
_SCREEN_WORDS = re.compile(r"画面|ここを|ここで|このページ|ページの|表の|グラフ|左の|右の|下の|上の|押す|押し|クリック|選択|カーソル|見てください|ご覧")
_NO_INFO = re.compile(r"(?:記載|言及|説明)(?:は|が)?(?:なく|ありません|無く)|分かりません|わかりません|確認できていません|まだ発表されていません|書かれていません|不明です")
TITLE_SPLIT = re.compile(r"　|(?<=[^A-Za-z0-9.]) (?=[^A-Za-z0-9])|(?<=[」』]) ")
_TITLE_SPEECH = re.compile(r"^見出しは「(.+)」です。$")
_TRAIL_PARTICLE = re.compile(r"(?:は|が|も|を|に|で|と|の|へ|、)$")


def plain(text: str) -> str:
    """照合用: かっこの中・空白を除き、全角数字をそろえる"""
    import video_gate as gate

    return _SPACE.sub("", _PAREN.sub("", gate.norm(str(text or ""))))


def clean_sentence(text: str) -> str:
    """読み上げ・字幕に使う形: かっこの中の補足（英語の略語・言い換え）を除き、空白を詰める"""
    t = _PAREN.sub("", str(text or ""))
    t = re.sub(r"\*\*|__|`", "", t)
    t = re.sub(r"[ \t]+", " ", t).replace("　", " ").strip()
    t = re.sub(r"\s*([、。！？])\s*", r"\1", t)
    return t


def sentences(text: str) -> list[str]:
    out = []
    for part in re.split(r"(?<=[。！？])", str(text or "")):
        part = part.strip()
        if part:
            out.append(part)
    return out


def bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s、。・「」『』（）()!！?？:：,，.．\-–—]", "", plain(text))
    return {t[i : i + 2] for i in range(len(t) - 1)}


def overlap(key: str, speech: str) -> float:
    """要点の行の2文字のまとまりのうち、読み上げにも出てくる割合"""
    k = bigrams(key)
    return len(k & bigrams(speech)) / max(len(k), 1)


def augmented(article: dict) -> dict:
    """照合に使う記事: 本文に FAQ・仕事とお金・判定の一言・今すること を足す（どれも記事のページに出ている文）"""
    extra = []
    for f in article.get("faq") or []:
        if isinstance(f, dict):
            extra += [str(f.get("q", "")), str(f.get("a", ""))]
    extra += [str(x) for x in (article.get("workMoney") or {}).get("points") or []]
    extra += [str(x) for x in article.get("todayActions") or []]
    aug = dict(article)
    aug["body"] = (article.get("body") or "") + "\n\n" + "\n".join(x for x in extra if x)
    return aug


def material(article: dict) -> str:
    import video_gate as gate

    return gate.material_text(augmented(article))


def article_numbers(article: dict) -> set[str]:
    import video_gate as gate

    return gate.number_tokens(material(article)) | {"0"}


def is_ours(url: str) -> bool:
    return "ai-tech-express.jp/img/" in str(url or "")


def official_images(article: dict) -> list[dict]:
    """使ってよい画像（I1/I2）: imageCredit が official のサムネイル（サイトに保存した写し）と、本文の公式の写真・図（bodyImages）。
    AI 画像・素材写真・YouTube のサムネイル・ニュースサイトの写真は使わない"""
    out: list[dict] = []
    credit = article.get("imageCredit") or {}
    focus = article.get("imageFocus") or {}
    if credit.get("kind") == "official":
        url = article.get("thumbnail") or ""
        url = url if is_ours(url) else ""
        if url:
            org = (credit.get("org") or credit.get("provider") or "").strip()
            out.append({"url": url, "org": org, "kind": "photo", "focus": [float(focus.get("x", 0.5)), float(focus.get("y", 0.5))]})
    for b in article.get("bodyImages") or []:
        if not isinstance(b, dict) or not is_ours(b.get("src", "")) or not (b.get("credit") or "").strip():
            continue
        if str(b.get("kind", "")).lower() in ("ai", "stock", "generated"):
            continue
        if any(x["url"] == b["src"] for x in out):
            continue
        out.append({"url": b["src"], "org": str(b["credit"]).strip(), "kind": str(b.get("kind") or "photo"), "focus": [0.5, 0.5]})
    for x in out:
        x["credit"] = f"画像：{x['org']}（公式発表より）" if x["org"] else "画像：公式発表より"
    return out


def subject_of(article: dict) -> str:
    """題の頭の主語（「Googleが…」「Canva、…」）。長い・数字だけ・記号で始まるものは使わない"""
    title = re.sub(r"^【[^】]*】", "", str(article.get("title") or "")).strip()
    m = re.match(r"^([^、。「」『』　\s:：]{2,16}?)(?:、|が)", title)
    if not m:
        return ""
    s = m.group(1).strip()
    if re.fullmatch(r"[0-9０-９.,%％年月日]+", s) or re.search(r"[？?！!]", s):
        return ""
    return s


def polite_ok(sentence: str) -> list[str]:
    """読み上げの文の検査: です・ます・壊れた日本語・英語の文・長さ"""
    import sns_rules

    s = str(sentence or "").strip()
    out = []
    if not s:
        return ["空の文"]
    if not (sns_rules.POLITE_END.search(s) or re.search(r"ください[。！]?$", s)):
        out.append(f"です・ますで終わっていない「{s[-12:]}」")
    out += sns_rules.grammar_problems(s)
    out += sns_rules.descriptor_problems(s)
    if sns_rules.ENGLISH_RUN.search(s):
        out.append(f"英語の文がある「{s[:16]}」")
    if not sns_rules.JAPANESE_CHAR.search(s):
        out.append("日本語ではない")
    return out


def narration_sentence(text: str, max_len: int = 72, first: bool = True) -> str:
    """記事の1文を読み上げに使えるか（です・ます・壊れていない・記事の別の所を指さない・長すぎない・場面の最初なら「ただし」などで始まらない）。
    使えれば整えた文、だめなら空"""
    s = clean_sentence(text)
    if not s or len(s) < 12 or len(s) > max_len or _NOT_NARRATION.search(s) or (first and _CONNECTIVE.search(s)):
        return ""
    if not s.endswith(("。", "！", "？")):
        s += "。"
    return s if not polite_ok(s) else ""


@dataclass
class Beat:
    label: str  # 小見出し（青・小さく）
    key: str  # 要点の行（大きく。記事の見出し・FAQ の問いなどをそのまま）
    speech: str  # 読み上げ（記事の文をそのまま。字幕にも同じ文を出す）
    source: str  # lede / faq / work / script
    image: int = -1  # 使う画像の番号（-1 = 無し）


@dataclass
class SimplePlan:
    level: str
    variant: str
    slug: str
    title: str
    hook_speech: str
    hook_text: str
    beats: list[Beat]
    ending_text: str
    ending_speech: str
    cta: str
    images: list[dict] = field(default_factory=list)
    subject: str = ""
    source_name: str = ""
    summary: str = ""
    length_range: tuple = (15.0, 30.0)
    script_from: str = "article"  # article（機械で）/ llm（L1/L2 の台本の場面）

    @property
    def mode(self) -> str:
        return "image" if self.images else "card"

    def lines(self) -> list[str]:
        return [self.hook_speech] + [b.speech for b in self.beats] + [self.ending_speech]


FAQ_LABELS = [
    (re.compile(r"いつ|時期|発売日|開始"), "いつから？"),
    (re.compile(r"日本"), "日本では？"),
    (re.compile(r"無料|料金|いくら|価格|費用|値段"), "お金の話"),
    (re.compile(r"違う|変わ|前と"), "前と比べると？"),
    (re.compile(r"誰|対象"), "だれが対象？"),
    (re.compile(r"とは|何を|何が|何で|どんな|できる"), "どんなもの？"),
]
FALLBACK_LABELS = ["気になる点", "ほかの注目点", "知っておきたい点"]
FIXED_LABELS = {"注目のニュース", "何があった？", "だれに役立つ？", "続きは記事で", "要点"} | {x for _, x in FAQ_LABELS} | set(FALLBACK_LABELS)

HOOKS = [
    ("{s}の新しいニュースです。", "大事なところを、短くまとめてお伝えします。"),
    ("{s}の話題を、短くお伝えします。", "知っておきたい点を、順番に紹介します。"),
    ("{s}について、知っておきたいニュースです。", "要点をまとめてお伝えします。"),
]
HOOK_GENERIC = ("新しいニュースを、短くお伝えします。", "知っておきたい点を、順番に紹介します。")
HOOK_EYEBROW = "注目のニュース"
ENDINGS = {
    "short": [
        "くわしくは記事で解説しています。投稿のリンクから、ぜひチェックしてみてください。",
        "続きは記事でくわしく解説しています。投稿のリンクから、のぞいてみてください。",
    ],
    "full": [
        "くわしくは、サイソクニュースの記事で解説しています。ぜひ、あわせて読んでみてください。",
        "続きは、サイソクニュースの記事でくわしく紹介しています。あわせて読んでみてください。",
    ],
}
ENDING_COMPACT = "続きは、投稿のリンクの記事で読めます。"
ENDING_TEXT = "くわしくは記事で"
CTA = {"short": "投稿のリンクからどうぞ ↗", "full": "ai-tech-express.jp"}


def _pick(options: list, seed: str):
    return options[zlib.crc32(seed.encode("utf-8")) % len(options)]


def hook_for(article: dict, variant: str) -> str:
    s = subject_of(article)
    first, second = _pick(HOOKS, article.get("slug", "")) if s else HOOK_GENERIC
    first = first.format(s=s)
    return first if variant == "short" else first + second


def fixed_texts(article: dict) -> set[str]:
    """決まった定型の文（記事の文ではないが、この仕組みが決めた言い回し。中身の主張・数字は入らない）"""
    s = subject_of(article)
    out = set(FIXED_LABELS) | {ENDING_TEXT, ENDING_COMPACT, *CTA.values(), *[x for v in ENDINGS.values() for x in v], *HOOK_GENERIC, HOOK_GENERIC[0] + HOOK_GENERIC[1]}
    for a, b in HOOKS:
        out |= {a.format(s=s), a.format(s=s) + b, b}
    return out


def title_key(article: dict, speech: str) -> str:
    """「何があった？」の要点の行: 題を全角の空白で分けた片方（読み上げと重なりの多い方）。長ければハイライトの最初の文節"""
    parts = [p.strip() for p in TITLE_SPLIT.split(str(article.get("title") or "")) if p.strip()]
    sd = set(re.findall(r"\d+(?:\.\d+)?", plain(speech)))
    parts = [p for p in parts if 6 <= len(plain(p)) <= 52 and set(re.findall(r"\d+(?:\.\d+)?", plain(p))) <= sd and key_fit(clean_sentence(p))]
    best = max(parts, key=lambda p: overlap(p, speech), default="")
    if best and overlap(best, speech) >= 0.2:
        return clean_sentence(best)
    for h in article.get("highlights") or []:
        for clause in re.split(r"(?<=[。])", clean_sentence(h)):
            clause = clause.strip().rstrip("。")
            if 6 <= len(clause) <= 28 and overlap(clause, speech) >= 0.3 and set(re.findall(r"\d+(?:\.\d+)?", plain(clause))) <= sd:
                return clause
    return ""


def faq_label(q: str, used: set[str]) -> str:
    for pat, label in FAQ_LABELS:
        if pat.search(q) and label not in used:
            return label
    return next((x for x in FALLBACK_LABELS if x not in used), "")


SKIP_H2 = re.compile(r"概要|何が発表|自分には|仕事・お金|仕事とお金|まとめ|出典|参考|よくある質問|FAQ|ライバル|数字で見る")


def body_sections(body: str) -> list[tuple[str, str, str]]:
    """本文の見出しごとの (大見出し, 小見出し, 最初の段落)"""
    out, h2, h3, para = [], "", "", []

    def flush():
        if para:
            out.append((h2, h3, " ".join(para)))

    for line in str(body or "").splitlines():
        t = line.strip()
        if t.startswith("## "):
            flush()
            h2, h3, para = t[3:].strip(), "", []
        elif t.startswith("### "):
            flush()
            h3, para = t[4:].strip(), []
        elif t and not t.startswith(("-", "|", ">", "!", "*")) and not para:
            para = [t]
    flush()
    return out


def lede_candidates(article: dict) -> list[str]:
    """「何があった？」の読み上げの候補: リードの最初の2文 → 本文の「概要」「何が発表された？」の最初の文 → 投稿文の「要約すると、」の文"""
    out = [narration_sentence(x, max_len=88) for x in sentences(article.get("lede", ""))[:2]]
    for h2, h3, para in body_sections(article.get("body", "")):
        if not h3 and (re.search(r"概要|何が発表", h2) or not h2):
            out += [narration_sentence(x, max_len=88) for x in sentences(para)[:1]]
    for line in str(article.get("threadsPost") or "").splitlines():
        if line.startswith("要約すると、"):
            out.append(narration_sentence(line.removeprefix("要約すると、"), max_len=88))
    return [x for x in dict.fromkeys(out) if x]


def first_beat(article: dict) -> Beat | None:
    """最初の場面（何があった？）: 候補の文のうち、題の片方（か見出しのハイライト）と同じ話のもの"""
    for speech in lede_candidates(article):
        if key := title_key(article, speech):
            return Beat("何があった？", key, speech, "lede")
    return None


def article_beats(article: dict) -> list[Beat]:
    """記事の文から場面を作る（LLM なし）。順番: 何があった？（リード）→ FAQ（問い＝要点・答え＝読み上げ）→
    本文の小見出し（見出し＝要点・その段落の最初の文＝読み上げ）→ 本文の問いの見出し（料金は？・日本で使える？）→ だれに役立つ？"""
    beats: list[Beat] = []
    used_labels: set[str] = set()
    if fb := first_beat(article):
        beats.append(fb)
        used_labels.add("何があった？")
    faq_rows = []
    for f in article.get("faq") or []:
        if not isinstance(f, dict):
            continue
        q = clean_sentence(f.get("q", ""))
        if not q or len(q) > 30 or _NOT_NARRATION.search(q):
            continue
        raw = sentences(f.get("a", ""))
        speech = narration_sentence(raw[0]) if raw else ""  # 答えの最初の文だけ（途中の文は前の文が無いと話が分からない）
        if not speech:
            continue
        k = 0
        nxt = narration_sentence(raw[k + 1], first=False) if k + 1 < len(raw) else ""
        if len(speech) < 24 and nxt and len(speech + nxt) <= 76:
            speech += nxt
        if re.search(r"何が発表|何を発表|とは何|とは？", q) and beats:  # 「何があった？」と同じ話
            continue
        faq_rows.append((1 if _NO_INFO.search(speech) else 0, len(faq_rows), q, speech))
    for _, _, q, speech in sorted(faq_rows):
        label = faq_label(q, used_labels)
        if not label:
            continue
        used_labels.add(label)
        beats.append(Beat(label, q, speech, "faq"))
    for h2, h3, para in body_sections(article.get("body", "")):
        head = clean_sentence(h3 or h2)
        if not head or SKIP_H2.search(h2) or (not h3 and not head.endswith(("？", "?"))) or not (4 <= len(head) <= 28):
            continue
        if _NOT_NARRATION.search(head):
            continue
        sents = sentences(para)
        speech = narration_sentence(sents[0]) if sents else ""
        if not speech or _NO_INFO.search(speech) and h3:
            continue
        if len(speech) < 32 and len(sents) > 1 and (nxt := narration_sentence(sents[1], first=False)) and len(speech + nxt) <= 84:
            speech += nxt  # 最初の文が短い前置き（「〜があります。」）なら、次の文までで1つの話に
        label = ("前と比べると？" if "前と比べると？" not in used_labels else "") if re.search(r"前と|違う|変わ", h2) and h3 else ""
        label = label or faq_label(head, used_labels)
        if not label:
            continue
        used_labels.add(label)
        beats.append(Beat(label, head, speech, "section"))
    for p in (article.get("workMoney") or {}).get("points") or []:
        s = narration_sentence(p, max_len=76)
        m = re.match(r"^(.{4,24}?(?:人|者|方|企業|会社|チーム|部門|担当|事業主|愛好家))(?:は|が|なら|に)、?", clean_sentence(p))
        if s and m and not _NO_INFO.search(s):
            beats.append(Beat("だれに役立つ？", m.group(1), s, "work"))
            break
    # 同じ話（読み上げの重なりが大きい）は後ろを落とす
    out: list[Beat] = []
    for b in beats:
        if any(len(bigrams(b.speech) & bigrams(o.speech)) / max(len(bigrams(b.speech) | bigrams(o.speech)), 1) > 0.45 for o in out):
            continue
        if any(plain(b.key) == plain(o.key) for o in out):
            continue
        out.append(b)
    return out


def title_beats(article: dict) -> list[Beat]:
    """です・ますの文が記事に1つも使えないときの最後の手: 題をそのまま読む（「見出しは「◯◯」です。」）。題の片方ずつ・題の全体の順に候補"""
    title = str(article.get("title") or "")
    parts = [clean_sentence(p) for p in TITLE_SPLIT.split(title) if p.strip()] + [clean_sentence(title)]
    return [Beat("何があった？", p, f"見出しは「{p}」です。", "title") for p in dict.fromkeys(parts) if 6 <= len(plain(p)) <= 64]


def est_seconds(lines: list[str]) -> float:
    chars = sum(len(re.sub(r"[\s、。！？「」]", "", x)) for x in lines)
    return LEAD + chars / CPS + GAP * (len(lines) - 1) + END_HOLD


def choose_beats(beats: list[Beat], hook: str, ending: str, rng: tuple, variant: str) -> list[Beat]:
    """長さの範囲に入るように場面を選ぶ。最初の場面（何があった？）は必ず入れる。
    短い版: そのあと数字のある場面を先に・「まだ発表されていない」の場面は入れない。サイト用: 記事の順・「まだ」は2つまで最後に"""
    _lo, hi = rng
    target = hi - 2.5
    if not beats:
        return []
    head, rest = beats[0], beats[1:]
    if variant == "short":
        rest = sorted((b for b in rest if not _NO_INFO.search(b.speech)), key=lambda b: (b.source != "work", not re.search(r"\d", b.speech), beats.index(b)))
    else:
        info = [b for b in rest if not _NO_INFO.search(b.speech)]
        rest = info + [b for b in rest if _NO_INFO.search(b.speech)][:2]
    chosen = [head]
    for b in rest:
        if est_seconds([hook] + [x.speech for x in chosen + [b]] + [ending]) <= target:
            chosen.append(b)
    return [b for b in beats if b in chosen]


def summary_of(article: dict) -> str:
    """投稿文の要約（threadsPost の「要約すると、」の行があればそれ。無ければリードの最初の文）"""
    for line in str(article.get("threadsPost") or "").splitlines():
        if line.startswith("要約すると"):
            return line.strip()
    lede = [narration_sentence(s) for s in sentences(article.get("lede", ""))]
    lede = [s for s in lede if s]
    return ("要約すると、" + lede[0]) if lede else ""


def build_plan(article: dict, level: str, variant: str, images: list[dict] | None = None, script: dict | None = None) -> SimplePlan:
    """L3/L4 の計画（描画しない）。images=None なら記事の公式の画像（L4 は1枚まで）。script は L1/L2 が書いた台本（L3 で使う）"""
    rng = RANGES[(level, variant)]
    imgs = official_images(article) if images is None else list(images)
    if level == "L4":
        imgs = imgs[:1]
    hook = hook_for(article, variant)
    ending = _pick(ENDINGS[variant], article.get("slug", "") + variant)
    beats, origin = [], "article"
    if level == "L3" and script:
        beats = script_beats(script, article)
        origin = "llm" if beats else "article"
        if beats and (h := script_hook(script, article)):
            hook = h
    if not beats:
        beats = article_beats(article)
    nums, mat = article_numbers(article), plain(material(article))
    beats = [b for b in beats if not beat_problems(b, article, nums, mat)]  # 検査に通る場面だけ（計画が落ちないように先に外す）
    if not beats:
        beats = [b for b in title_beats(article) if not beat_problems(b, article, nums, mat)][:1]
    chosen = choose_beats(beats, hook, ending, rng, variant)
    if variant == "short" and est_seconds([hook] + [b.speech for b in chosen] + [ending]) > rng[1] - 1.5:
        ending = ENDING_COMPACT  # 最初の場面が長いときは、締めを短く
    if est_seconds([hook] + [b.speech for b in chosen] + [ending]) < rng[0] + 1.5 and origin == "article":
        hook = hook_for(article, "full")  # 場面が少なくて短すぎるときは、つかみを2文に
    for i, b in enumerate(chosen):
        b.image = (i + 1) % len(imgs) if imgs and level == "L3" and len(imgs) > 1 else (0 if imgs else -1)
    return SimplePlan(
        level=level, variant=variant, slug=article.get("slug", ""), title=clean_sentence(article.get("title", "")),
        hook_speech=hook, hook_text=clean_sentence(article.get("title", "")), beats=chosen,
        ending_text=ENDING_TEXT, ending_speech=ending, cta=CTA[variant], images=imgs,
        subject=subject_of(article), source_name=clean_sentence(article.get("sourceName", "")),
        summary=summary_of(article), length_range=rng, script_from=origin,
    )  # fmt: skip


def script_hook(script: dict, article: dict) -> str:
    h = clean_sentence((script.get("hook") or {}).get("speech", ""))
    if not h or _SCREEN_WORDS.search(h):
        return ""
    import sns_rules

    if sns_rules.grammar_problems(h) or not (sns_rules.POLITE_END.search(h) or h.endswith(("？", "?"))):
        return ""
    import video_gate as gate

    if gate.numbers_ok(h, article_numbers(article)):
        return ""
    return h


def script_beats(script: dict, article: dict) -> list[Beat]:
    """L1/L2 の台本（LLM）の場面のうち、写真を見せながらでも話が合うもの: 画面を指す言い方が無い・グラフの場面ではない・
    数字はすべて記事にある・です・ます・キーワードと読み上げが同じ話"""
    import video_gate as gate

    nums = article_numbers(article)
    graph = {int((script.get(k) or {}).get("beat") or 0) for k in ("ladder", "compare", "units")}
    graph |= {int(x.get("beat") or 0) for k in ("ladders", "flows") for x in script.get(k) or []}
    out, used = [], set()
    for i, b in enumerate(script.get("beats") or [], 1):
        speech = clean_sentence(b.get("speech", ""))
        key = clean_sentence(b.get("keyword", ""))
        label = clean_sentence(b.get("label", "")) or "ポイント"
        if i in graph or not speech or not key or _SCREEN_WORDS.search(speech) or polite_ok(speech):
            continue
        if gate.numbers_ok(speech + " " + key, nums) or gate.bare_phrase(key) or label in used:
            continue
        kd = set(re.findall(r"\d+(?:\.\d+)?", gate.norm(key)))
        if not kd <= set(re.findall(r"\d+(?:\.\d+)?", gate.norm(speech))) or (not kd and overlap(key, speech) < 0.2):
            continue
        used.add(label)
        out.append(Beat(label, key, speech, "script"))
    return out


def beat_problems(b: Beat, article: dict, nums: set | None = None, mat: str | None = None) -> list[str]:
    """1つの場面の検査（数字は記事にある・要点と読み上げは記事の文そのまま・同じ話・枠に入る・です・ます）"""
    import video_gate as gate

    nums = article_numbers(article) if nums is None else nums
    mat = plain(material(article)) if mat is None else mat
    out = []
    for p in polite_ok(b.speech):
        out.append(f"の読み上げ: {p}")
    if bad := gate.numbers_ok(b.speech, nums):
        out.append(f"の読み上げに記事に無い数字 {', '.join(sorted(set(bad)))}")
    if bad := gate.numbers_ok(b.key, nums):
        out.append(f"の要点に記事に無い数字 {', '.join(sorted(set(bad)))}")
    if b.source != "script" and plain(b.key) not in mat:
        out.append(f"の要点「{b.key[:14]}」が記事にそのまま無い")
    m = _TITLE_SPEECH.match(b.speech) if b.source == "title" else None
    if b.source == "title" and not (m and plain(m.group(1)) in mat):
        out.append("の題の読み上げが記事の題そのままではない")
    elif b.source not in ("script", "title") and plain(b.speech).rstrip("。") not in mat:
        out.append("の読み上げが記事の文そのままではない")
    kd = set(re.findall(r"\d+(?:\.\d+)?", gate.norm(b.key)))
    sd = set(re.findall(r"\d+(?:\.\d+)?", gate.norm(b.speech)))
    if not kd <= sd and b.source not in ("faq", "section"):
        out.append(f"の要点の数字が読み上げに無い（{b.key[:14]}）")
    if b.source not in ("faq", "work", "section", "title") and overlap(b.key, b.speech) < 0.2:
        out.append(f"の要点と読み上げが別の話（{b.key[:14]}）")
    if gate.bare_phrase(b.key) or gate.bare_phrase(b.label) and b.label not in FIXED_LABELS:
        out.append(f"の要点・小見出しが名詞1つだけ（{b.key}）")
    if b.source == "script" and b.label not in FIXED_LABELS and gate.numbers_ok(b.label, nums):
        out.append("の小見出しに記事に無い数字")
    if not key_fit(b.key):
        out.append(f"の要点「{b.key[:12]}」が左の列に入らない")
    for chunk in sub_chunks(b.speech):
        if not fit(chunk, "bold", SUB_SIZES, SUB[2] - SUB[0], 2):
            out.append(f"の字幕「{chunk[:12]}」が字幕の枠に入らない")
    return out


def plan_problems(plan: SimplePlan, article: dict) -> list[str]:
    """描画の前の検査（ネット・声なしで回せる）。空なら合格"""
    import video_gate as gate

    out: list[str] = []
    nums = article_numbers(article)
    mat = plain(material(article))
    fixed = {plain(x) for x in fixed_texts(article)}
    if not plan.beats:
        out.append("使える場面が無い（記事にです・ますの文・FAQ が無い）")
    lo, hi = plan.length_range
    est = est_seconds(plan.lines())
    if not (lo - 3 <= est <= hi + 3):
        out.append(f"読み上げの長さの見込み {est:.0f}秒が {lo:.0f}〜{hi:.0f}秒から外れる")
    for line in plan.lines():
        for p in polite_ok(line) if not line.endswith(("？", "?")) else []:
            out.append(f"読み上げ: {p}")
        if bad := gate.numbers_ok(line, nums):
            out.append(f"読み上げに記事に無い数字 {', '.join(sorted(set(bad)))}")
    seen_keys, seen_labels = set(), set()
    for i, b in enumerate(plan.beats, 1):
        out += [f"場面{i}{p}" for p in beat_problems(b, article, nums, mat)]
        if plain(b.key) in seen_keys:
            out.append(f"場面{i}の要点が前の場面と同じ")
        if b.label in seen_labels:
            out.append(f"場面{i}の小見出し「{b.label}」が前の場面と同じ")
        seen_keys.add(plain(b.key))
        seen_labels.add(b.label)
    if plain(plan.hook_text) not in mat:
        out.append("つかみの題が記事の題そのままではない")
    if plan.script_from == "article" and plain(plan.hook_speech) not in fixed:
        out.append("つかみの読み上げが決まった言い回しではない")
    for img in plan.images:
        if not is_ours(img.get("url", "")):
            out.append(f"サイトに保存していない画像は使わない（{img.get('url', '')[:40]}）")
    if plan.level == "L4" and len(plan.images) > 1:
        out.append("L4 の画像は1枚まで")
    if (article.get("imageCredit") or {}).get("kind") in ("ai", "stock") and any(x["url"] == article.get("thumbnail") for x in plan.images):
        out.append("AI・素材の画像を使っている（I2）")
    out += fit_problems(plan)
    return out


def all_verbatim(plan: SimplePlan, article: dict) -> bool:
    """画面の文字（題・要点・字幕・カード）がすべて記事の文そのままか、決まった定型の文か（L4 の vision の代わりの判定に使う）"""
    mat = plain(material(article))
    fixed = {plain(x) for x in fixed_texts(article)}
    texts = [plan.hook_text, plan.subject, plan.source_name] + [b.key for b in plan.beats] + [b.speech for b in plan.beats]
    texts += [b.label for b in plan.beats] + [plan.hook_speech, plan.ending_speech, plan.ending_text, plan.cta]
    def ok(t: str) -> bool:
        m = _TITLE_SPEECH.match(t or "")
        return not t or plain(t) in mat or plain(t) in fixed or bool(m and plain(m.group(1)) in mat)

    return all(ok(t) for t in texts)


# --------------------------------------------------------------------------- #
# 2. 文字（Pillow）。書体は Noto Sans JP（Black/Bold）とロゴの Dela Gothic One だけ
# --------------------------------------------------------------------------- #

NUM_SPLIT = re.compile(r"(?<![A-Za-z0-9.\-])([$＄]?\d[\d,，.．]*(?:万|億|兆)?\d*(?:円|ドル|%|％|倍|人|件|社|年|月|日|時間|分|秒|GB|TB|万人|kg|W|Wh|Nm)?)")
_PARSER = None


def _phrases(text: str) -> list[str]:
    global _PARSER
    try:
        import budoux

        if _PARSER is None:
            _PARSER = budoux.load_default_japanese_parser()
        parts = _PARSER.parse(text)
    except Exception:  # noqa: BLE001 - budoux が無ければ1文字ずつ
        parts = list(text)
    out = []
    for p in parts:  # 英数字の語の途中では切らない
        if out and re.match(r"^[A-Za-z0-9.\-+]", p) and re.search(r"[A-Za-z0-9.\-+]$", out[-1]):
            out[-1] += p
        else:
            out.append(p)
    return out


def wrap(text: str, fnt, width: int) -> list[str]:
    lines, cur = [], ""
    for p in _phrases(text):
        if fnt.getlength(cur + p) <= width:
            cur += p
            continue
        if cur:
            lines.append(cur)
        cur = ""
        while fnt.getlength(p) > width:  # 1つの文節が幅より長い → 文字で切る
            k = len(p)
            while k > 1 and fnt.getlength(p[:k]) > width:
                k -= 1
            lines.append(p[:k])
            p = p[k:]
        cur = p
    if cur:
        lines.append(cur)
    # 行頭に句読点・閉じかっこを置かない
    for i in range(1, len(lines)):
        while lines[i] and lines[i][0] in "、。」）！？":
            lines[i - 1] += lines[i][0]
            lines[i] = lines[i][1:]
    return [x for x in lines if x]


def fit(text: str, face: str, sizes, width: int, rows: int) -> tuple[int, list[str]] | None:
    import shorts

    width -= 8  # 文字の絵の余白（左右 2px ずつ）と丸めの分
    for size in sizes:
        lines = wrap(text, shorts.font(face, size), width)
        if len(lines) <= rows and all(shorts.font(face, size).getlength(x) <= width + 0.5 for x in lines):
            return size, lines
    return None


def render_lines(lines: list[str], face: str, size: int, color=INK, accent: bool = False, gap: float = 1.3, align: str = "left", width: int | None = None):
    """文字の RGBA（numpy）。accent なら数字を赤に"""
    import numpy as np
    from PIL import Image, ImageDraw

    import shorts

    fnt = shorts.font(face, size)
    asc, desc = fnt.getmetrics()
    lh = int(size * gap)
    w = int(width or max((fnt.getlength(x) for x in lines), default=1)) + 4
    h = lh * (len(lines) - 1) + asc + desc + 4
    img = Image.new("RGBA", (max(w, 1), max(h, 1)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        lw = fnt.getlength(line)
        x = 2 + ((w - 4 - lw) / 2 if align == "center" else 0)
        y = 2 + i * lh
        parts = NUM_SPLIT.split(line) if accent else [line]
        for j, part in enumerate(parts):
            if not part:
                continue
            col = ACCENT if (accent and j % 2 == 1) else color
            d.text((x, y), part, font=fnt, fill=col + (255,))
            x += fnt.getlength(part)
    return np.asarray(img)


# --------------------------------------------------------------------------- #
# 3. 絵の計画（どの時刻に何を出すか）と描画
# --------------------------------------------------------------------------- #

KEY_SIZES = range(58, 38, -2)
HOOK_SIZES = range(52, 34, -2)
SUB_SIZES = range(44, 34, -2)
SUB_MAX = 38  # 字幕1枚の目安の字数（1行。長い文は文節の切れ目で同じくらいの長さに分ける）


def key_fit(text: str) -> tuple[int, list[str]] | None:
    """要点の行の大きさと折り返し: 3行まで（大きい字）→ 4行まで（40〜44px）→ 5行まで（36〜40px。長い題をそのまま出すとき）"""
    cw = COL[2] - COL[0]
    return fit(text, "black", KEY_SIZES, cw, 3) or fit(text, "black", range(44, 38, -2), cw, 4) or fit(text, "black", range(40, 34, -2), cw, 5)


def fit_problems(plan: SimplePlan) -> list[str]:
    """要点・題・字幕が枠に入るか（描画の前に分かる）"""
    out = []
    cw = COL[2] - COL[0]
    if not fit(plan.hook_text, "black", HOOK_SIZES, cw, 5):
        out.append("題が左の列に入らない")
    for i, b in enumerate(plan.beats, 1):
        if not key_fit(b.key):
            out.append(f"場面{i}の要点「{b.key[:12]}」が左の列に入らない")
    for line in plan.lines():
        for chunk in sub_chunks(line):
            if not fit(chunk, "bold", SUB_SIZES, SUB[2] - SUB[0], 2):
                out.append(f"字幕「{chunk[:12]}」が字幕の枠に入らない")
    return out


def sub_chunks(speech: str) -> list[str]:
    """字幕の1枚ずつ: 文ごとに、SUB_MAX 字を超える文は文節の切れ目（読点を優先）で同じくらいの長さに分ける（つなぐと読み上げと同じ）"""
    out = []
    for s in sentences(speech) or [speech]:
        if len(s) <= SUB_MAX:
            out.append(s)
            continue
        ph = _phrases(s)
        cum = [0]
        for x in ph:
            cum.append(cum[-1] + len(x))
        k = math.ceil(len(s) / SUB_MAX)
        cuts, last = [], 0
        for i in range(1, k):
            target = len(s) * i / k

            def cost(j: int) -> float:
                return abs(cum[j] - target) - (3 if ph[j - 1].endswith("、") else 0) + (99 if cum[j] - cum[last] < 8 or len(s) - cum[j] < 8 else 0)

            j = min(range(last + 1, len(ph)), key=cost, default=None)
            if j is None:
                break
            cuts.append(j)
            last = j
        bounds = [0] + cuts + [len(ph)]
        out += ["".join(ph[a:b]) for a, b in zip(bounds, bounds[1:]) if b > a]
    return [x for x in out if x]


@dataclass
class Item:
    role: str
    text: str
    sprite: object  # numpy RGBA
    x: int
    y: int
    t0: float
    t1: float
    box: tuple
    size: int
    color: tuple
    slide: int = 0  # 左から入る距離（px）
    fade: float = 0.3

    def bbox(self) -> list[float]:
        h, w = self.sprite.shape[:2]
        return [float(self.x), float(self.y), float(self.x + w), float(self.y + h)]


@dataclass
class Timeline:
    total: float
    line_times: list[tuple[float, float]]
    items: list[Item]
    segments: list[tuple[float, float, int]]  # (t0, t1, 画像の番号 or -1)
    units: list[dict]
    credits: list[str]
    keyframes: list[tuple[float, str, str]]  # (時刻, 読み上げ, 要点)
    card: list = field(default_factory=list)  # ブランドのカードの文字 (文字, 枠, 大きさ)


def column_items(eyebrow: str, key_text: str, sizes, rows: int, t0: float, t1: float, role: str, accent: bool) -> list[Item]:
    """左の列: 小見出し（青・小）＋大きな文字。縦の中心は右の画像の中心にそろえる"""
    cw = COL[2] - COL[0]
    import shorts

    got = (key_fit(key_text) if role == "keyword" else fit(key_text, "black", sizes, cw, rows)) or (sizes[-1], wrap(key_text, shorts.font("black", sizes[-1]), cw - 8)[:rows])
    size, lines = got
    big = render_lines(lines, "black", size, INK, accent=accent, gap=1.28)
    eb = render_lines([eyebrow], "bold", 26, BLUE) if eyebrow else None
    total_h = big.shape[0] + (eb.shape[0] + 18 if eb is not None else 0)
    cy = (IMG[1] + IMG[3]) // 2
    y = int(min(max(COL[1], cy - total_h / 2), COL[3] - total_h))
    out = []
    if eb is not None:
        out.append(Item("eyebrow", eyebrow, eb, COL[0], y, t0, t1, COL, 26, BLUE, slide=18))
        y += eb.shape[0] + 18
    out.append(Item(role, key_text, big, COL[0], y, t0 + 0.08, t1, COL, size, INK, slide=28))
    return out


def build_timeline(plan: SimplePlan, line_times: list[tuple[float, float]], total: float, credits: dict) -> Timeline:
    items: list[Item] = []
    n = len(plan.beats)
    starts = [a for a, _ in line_times]
    seg_t = [0.0] + [max(0.0, s - 0.12) for s in starts[1:]] + [total]
    # 左の列: つかみ（題）→ 場面ごとの要点 → 締め
    hook_eye = HOOK_EYEBROW
    items += column_items(hook_eye, plan.hook_text, HOOK_SIZES, 5, 0.0, seg_t[1], "hook", accent=False)
    for i, b in enumerate(plan.beats, 1):
        items += column_items(b.label, b.key, KEY_SIZES, 3, seg_t[i], seg_t[i + 1], "keyword", accent=True)
    end_t0 = seg_t[n + 1]
    items += column_items("続きは記事で", plan.ending_text, KEY_SIZES, 2, end_t0, total, "conclusion", accent=False)
    cta = render_lines([plan.cta], "bold", 30, BLUE)
    last = items[-1]
    items.append(Item("cta", plan.cta, cta, COL[0], int(last.y + last.sprite.shape[0] + 22), end_t0 + 0.3, total, COL, 30, BLUE, slide=0))
    # ロゴ（ずっと）
    logo = render_lines(["サイソクニュース"], "logo", 30, INK)
    items.append(Item("logo", "サイソクニュース", logo, LOGO_XY[0], LOGO_XY[1], 0.0, total, (0, 0, IMG[0], COL[1]), 30, INK, fade=0.0))
    # 字幕（読み上げと同じ文。1枚ずつ、字数に比例した時間）
    units = []
    kinds = ["hook"] + ["point"] * n + ["use"]
    captions = [plan.hook_text] + [b.key for b in plan.beats] + [plan.ending_text]
    for li, ((s, e), speech) in enumerate(zip(line_times, plan.lines())):
        chunks = sub_chunks(speech)
        weights = [max(1, len(c)) for c in chunks]
        acc = s
        recs = []
        next_s = line_times[li + 1][0] if li + 1 < len(line_times) else total
        for k, (c, wgt) in enumerate(zip(chunks, weights)):
            c0 = acc
            c1 = s + (e - s) * sum(weights[: k + 1]) / sum(weights)
            if k == len(chunks) - 1:
                c1 = max(c1, min(next_s, e + 0.25))
            acc = c1
            c0v = 0.0 if li == 0 and k == 0 else c0
            size, lines = fit(c, "bold", SUB_SIZES, SUB[2] - SUB[0], 2) or (SUB_SIZES[-1], [c])
            spr = render_lines(lines, "bold", size, INK, gap=1.3, align="center")
            x = int((SUB[0] + SUB[2] - spr.shape[1]) / 2)
            y = int((SUB[1] + SUB[3] - spr.shape[0]) / 2)
            items.append(Item("subtitle", c, spr, x, y, c0v, c1, SUB, size, INK, fade=0.1))
            recs.append({"text": c, "start": round(c0v, 3), "end": round(c1, 3)})
        units.append({"kind": kinds[li], "line": li, "caption": captions[li], "start": round(s, 3), "end": round(e, 3), "chunks": recs})
    # クレジット: 画像の出典は画像を出している間ずっと。最後の場面で声・画像・サイトの1行
    shown = []
    if credits.get("image"):
        spr = render_lines([credits["image"]], "bold", 20, SOFT)
        items.append(Item("credit", credits["image"], spr, CREDIT[0], CREDIT[1] + 8, 0.0, end_t0, CREDIT, 20, SOFT, fade=0.0))
        shown.append(credits["image"])
    tail = "　".join(x for x in [f"音声：{credits['voice']}" if credits.get("voice") else "", credits.get("image", ""), "ai-tech-express.jp"] if x)
    spr = render_lines([tail], "bold", 20, SOFT)
    items.append(Item("credit", tail, spr, CREDIT[0], CREDIT[1] + 8, end_t0, total, CREDIT, 20, SOFT, fade=0.2))
    shown += [x for x in [f"音声：{credits['voice']}" if credits.get("voice") else "", credits.get("image", ""), "ai-tech-express.jp"] if x]
    segments = [(seg_t[0], seg_t[1], 0 if plan.images else -1)]
    segments += [(seg_t[i], seg_t[i + 1], plan.beats[i - 1].image) for i in range(1, n + 1)]
    segments += [(end_t0, total, segments[-1][2])]
    keyframes = [(round((a + b) / 2 + 0.3, 2), sp, cap) for (a, b, _), sp, cap in zip(segments, plan.lines(), captions)]
    return Timeline(total, line_times, items, segments, units, list(dict.fromkeys(shown)), keyframes)


def _mask_round(w: int, h: int, r: int):
    import numpy as np
    from PIL import Image, ImageDraw

    m = Image.new("L", (w * 2, h * 2), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * 2 - 1, h * 2 - 1), r * 2, fill=255)
    return np.asarray(m.resize((w, h), Image.LANCZOS)).astype(np.float32) / 255.0


def _mask_shadow(w: int, h: int, r: int, pad: int = 40):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    m = Image.new("L", (w + pad * 2, h + pad * 2), 0)
    ImageDraw.Draw(m).rounded_rectangle((pad, pad + 10, pad + w, pad + h + 10), r, fill=60)
    return np.asarray(m.filter(ImageFilter.GaussianBlur(18))).astype(np.float32) / 255.0


def load_picture(path: str, size: tuple[int, int], kind: str = "photo"):
    """画像を右の枠の 1.1 倍の大きさに。16:9 に近ければ枠いっぱい（cover）、違えば白い地に全体（contain。図の文字を切らない）"""
    import numpy as np
    from PIL import Image

    w, h = int(size[0] * 1.1), int(size[1] * 1.1)
    img = Image.open(path).convert("RGB")
    ar, want = img.width / img.height, w / h
    if abs(ar - want) / want <= 0.04 and kind != "figure":
        scale = max(w / img.width, h / img.height)
        img = img.resize((max(w, round(img.width * scale)), max(h, round(img.height * scale))), Image.LANCZOS)
        x0, y0 = (img.width - w) // 2, (img.height - h) // 2
        return np.asarray(img.crop((x0, y0, x0 + w, y0 + h)))
    from PIL import ImageEnhance, ImageFilter

    # 縦横比が違う画像は切らずに全体を見せる。余白は同じ画像をぼかして薄くした地（白い帯にしない）
    k = max(w / img.width, h / img.height)
    cover = img.resize((max(w, round(img.width * k)), max(h, round(img.height * k))), Image.LANCZOS)
    cx0, cy0 = (cover.width - w) // 2, (cover.height - h) // 2
    canvas = cover.crop((cx0, cy0, cx0 + w, cy0 + h)).filter(ImageFilter.GaussianBlur(28))
    canvas = Image.blend(ImageEnhance.Brightness(canvas).enhance(1.05), Image.new("RGB", (w, h), WHITE), 0.35)
    scale = min(w * 0.96 / img.width, h * 0.96 / img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    return np.asarray(canvas)


def brand_card(size: tuple[int, int], big: str, small: str, boxes: list | None = None):
    """写真が無いときの右の枠: 紺のグラデーションに、主役の名前（大）と出典（小）。線・帯・枠は描かない。
    boxes に (文字, 画面での枠, 大きさ) を足す（文字の面積の検査用。寄ったときの最大 1.05 倍で数える）"""
    import numpy as np
    from PIL import Image

    w, h = int(size[0] * 1.1), int(size[1] * 1.1)
    t = np.linspace(0, 1, h)[:, None, None]
    grad = (np.array(NAVY_TOP)[None, None, :] * (1 - t) + np.array(NAVY_BOTTOM)[None, None, :] * t).repeat(w, axis=1)
    img = Image.fromarray(grad.astype(np.uint8), "RGB").convert("RGBA")
    got = fit(big, "black", range(96, 48, -4), int(w * 0.78), 2) if big else None
    y_mid = h // 2
    if got:
        spr = render_lines(got[1], "black", got[0], WHITE, gap=1.2, align="center")
        sub = render_lines([small], "bold", 34, (214, 226, 244), align="center") if small else None
        total = spr.shape[0] + (sub.shape[0] + 26 if sub is not None else 0)
        y = int(y_mid - total / 2)
        img.alpha_composite(Image.fromarray(spr), ((w - spr.shape[1]) // 2, y))
        if sub is not None:
            img.alpha_composite(Image.fromarray(sub), ((w - sub.shape[1]) // 2, y + spr.shape[0] + 26))
        if boxes is not None:  # キャンバス（1.1倍）の座標 → 画面の座標（中心から 1/1.1×最大1.05倍）
            cx, cy = (IMG[0] + IMG[2]) / 2, (IMG[1] + IMG[3]) / 2
            k = 1.05 / 1.1
            for text, sp, yy, sz in [(big, spr, y, got[0])] + ([(small, sub, y + spr.shape[0] + 26, 34)] if sub is not None else []):
                x0 = (w - sp.shape[1]) / 2
                bx = [cx + (x0 - w / 2) * k, cy + (yy - h / 2) * k, cx + (x0 + sp.shape[1] - w / 2) * k, cy + (yy + sp.shape[0] - h / 2) * k]
                boxes.append((text, bx, sz))
    return np.asarray(img.convert("RGB"))


def blend(dst, src, x: int, y: int, alpha: float = 1.0) -> None:
    import numpy as np

    h, w = src.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(dst.shape[1], x + w), min(dst.shape[0], y + h)
    if x1 <= x0 or y1 <= y0 or alpha <= 0:
        return
    s = src[y0 - y : y1 - y, x0 - x : x1 - x]
    a = s[..., 3:4].astype(np.float32) / 255.0 * alpha
    region = dst[y0:y1, x0:x1].astype(np.float32)
    dst[y0:y1, x0:x1] = (region * (1 - a) + s[..., :3].astype(np.float32) * a).astype(np.uint8)


def ease(k: float) -> float:
    k = min(1.0, max(0.0, k))
    return k * k * (3 - 2 * k)


class Renderer:
    """1フレームずつ描く（右の画像はゆっくり寄る・場面の切り替えは 0.45秒で重ねて替える・左の文字は左から入る）"""

    def __init__(self, plan: SimplePlan, tl: Timeline, pictures: list):
        import numpy as np

        self.plan, self.tl = plan, tl
        self.pictures = pictures  # 画像ごとの numpy（無ければブランドのカード1枚）
        self.iw, self.ih = IMG[2] - IMG[0], IMG[3] - IMG[1]
        self.round = _mask_round(self.iw, self.ih, RADIUS)[..., None]
        shadow = _mask_shadow(self.iw, self.ih, RADIUS)
        base = np.zeros((H, W, 3), dtype=np.uint8)
        base[:] = STAGE
        pad = 40
        sh = np.zeros(shadow.shape + (4,), dtype=np.uint8)
        sh[..., :3] = (20, 30, 50)
        sh[..., 3] = (shadow * 255).astype(np.uint8)
        blend(base, sh, IMG[0] - pad, IMG[1] - pad)
        self.base = base
        self.total = tl.total

    def run_of(self, k: int) -> tuple[float, float]:
        """同じ画像が続く場面をまとめた時間（ゆっくり寄る動きを場面の切れ目で戻さない）"""
        segs = self.tl.segments
        a = k
        while a > 0 and segs[a - 1][2] == segs[k][2]:
            a -= 1
        b = k
        while b + 1 < len(segs) and segs[b + 1][2] == segs[k][2]:
            b += 1
        return segs[a][0], segs[b][1]

    def picture_at(self, idx: int, t: float, seg: tuple):
        import numpy as np

        src = self.pictures[idx if 0 <= idx < len(self.pictures) else 0]
        a, b = seg[0], seg[1]
        k = ease((t - a) / max(b - a, 0.1))
        img = self.plan.images[idx] if 0 <= idx < len(self.plan.images) else {}
        zmax = 1.05 if img.get("kind") == "figure" or not self.plan.images else 1.08
        s = 1.0 + (zmax - 1.0) * k
        fx, fy = (img.get("focus") or [0.5, 0.5]) if img else (0.5, 0.5)
        sh, sw = src.shape[:2]
        cw, ch = sw / 1.1 / s * 1.0, sh / 1.1 / s * 1.0
        cx = min(max(sw / 2 + (fx - 0.5) * (sw - cw) * k, cw / 2), sw - cw / 2)
        cy = min(max(sh / 2 + (fy - 0.5) * (sh - ch) * k, ch / 2), sh - ch / 2)
        x0, y0 = int(round(cx - cw / 2)), int(round(cy - ch / 2))
        crop = src[max(0, y0) : max(0, y0) + int(ch), max(0, x0) : max(0, x0) + int(cw)]
        try:
            import cv2

            return cv2.resize(crop, (self.iw, self.ih), interpolation=cv2.INTER_AREA)
        except ImportError:  # pragma: no cover
            from PIL import Image

            return np.asarray(Image.fromarray(crop).resize((self.iw, self.ih), Image.BILINEAR))

    def frame(self, i: int, fps: int = FPS):
        import numpy as np

        t = i / fps
        out = self.base.copy()
        segs = self.tl.segments
        cur = next((k for k, s in enumerate(segs) if s[0] <= t < s[1]), len(segs) - 1)
        seg = segs[cur]
        pic = self.picture_at(seg[2], t, self.run_of(cur)).astype(np.float32)
        if cur > 0 and segs[cur - 1][2] != seg[2] and t - seg[0] < 0.45:  # 前の画像から重ねて替える
            prev = self.picture_at(segs[cur - 1][2], t, self.run_of(cur - 1)).astype(np.float32)
            k = ease((t - seg[0]) / 0.45)
            pic = prev * (1 - k) + pic * k
        region = out[IMG[1] : IMG[3], IMG[0] : IMG[2]].astype(np.float32)
        out[IMG[1] : IMG[3], IMG[0] : IMG[2]] = (region * (1 - self.round) + pic * self.round).astype(np.uint8)
        for it in self.tl.items:
            if not (it.t0 - 1e-6 <= t < it.t1):
                continue
            a_in = 1.0 if it.fade <= 0 else ease((t - it.t0) / it.fade)
            a_out = 1.0 if it.fade <= 0 or it.role in ("logo",) else ease((it.t1 - t) / 0.2)
            alpha = min(a_in, a_out)
            dx = int(-it.slide * (1 - a_in))
            blend(out, it.sprite, it.x + dx, it.y, alpha)
        return out


_WORKER: Renderer | None = None


def _render(i: int) -> bytes:
    assert _WORKER is not None
    return _WORKER.frame(i, _WORKER_FPS).tobytes()


_WORKER_FPS = FPS


def encode(renderer: Renderer, audio: Path, out: Path, fps: int = FPS, out_scale: float = 1.0, workers: int | None = None) -> None:
    import shorts

    global _WORKER, _WORKER_FPS
    n = int(round(renderer.total * fps))
    vf = ["-vf", f"scale={int(W * out_scale) // 2 * 2}:{int(H * out_scale) // 2 * 2}:flags=area"] if out_scale != 1.0 else []
    cmd = [
        shorts.ffmpeg_bin(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
        "-i", str(audio), "-map", "0:v", "-map", "1:a", *vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-maxrate", f"{min(8.0, max(2.0, 150.0 / max(renderer.total, 1))):.1f}M", "-bufsize", "16M",
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps * 2), "-keyint_min", str(fps), "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-t", f"{renderer.total:.3f}", "-movflags", "+faststart", str(out),
    ]  # fmt: skip
    workers = workers if workers is not None else max(1, min(6, (os.cpu_count() or 2)))
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin
    try:
        if workers <= 1 or sys.platform == "win32":
            for i in range(n):
                proc.stdin.write(renderer.frame(i, fps).tobytes())
        else:
            import multiprocessing as mp

            _WORKER, _WORKER_FPS = renderer, fps
            with mp.get_context("fork").Pool(workers) as pool:
                for data in pool.imap(_render, range(n), chunksize=6):
                    proc.stdin.write(data)
            _WORKER = None
    finally:
        proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg が失敗しました")


# --------------------------------------------------------------------------- #
# 4. 声と音（九州そら・自作の BGM・-14 LUFS）。shorts_v3 の部品を使う
# --------------------------------------------------------------------------- #


def make_tts_factory(kind: str):
    """速さ → 声の合成器。voicevox は九州そら（shorts_v3.V3_VOICE）。fake は自動テスト用の合成音（九州そらと同じくらいの速さ）"""
    import shorts_v2 as v2
    import shorts_v3 as v3

    if kind == "fake":
        def fake(speed: float):
            tts = v2.FakeTTS(1.0 / CPS * v3.V3_VOICE["speed"] / speed)
            return tts

        return fake, v3.V3_VOICE["speed"]
    return (lambda speed: v3.make_tts(kind, speed)), v3.V3_VOICE["speed"]


def narrate(lines: list[str], tts_kind, work: Path, rng: tuple) -> tuple[list[tuple[float, float]], object, float, dict]:
    """行ごとに合成して並べる。長さが外れたら速さを1回だけ計算し直して読み直す（基準 ±0.1 の範囲）"""
    import numpy as np

    import shorts_v2 as v2
    import shorts_v3 as v3

    factory, base = make_tts_factory(tts_kind) if isinstance(tts_kind, str) else (tts_kind, 1.38)
    if tts_kind == "voicevox" and not v3.wait_voicevox(timeout=90):
        raise RuntimeError("VOICEVOX に繋がりません")
    lo, hi = rng
    speed = base
    for attempt in range(3):
        tts = factory(speed)

        def synth(i: int):
            raw, dst = work / f"n{attempt}_{i}.src.wav", work / f"n{attempt}_{i}.wav"
            tts.synth(v3.speech_for_tts(lines[i]), raw)
            v2._to_48k_trimmed(raw, dst)
            return v2._read_wav(dst)

        with ThreadPoolExecutor(max_workers=4) as pool:
            clips = list(pool.map(synth, range(len(lines))))
        voiced = sum(len(c) for c in clips) / v3.SR
        fixed = LEAD + GAP * (len(lines) - 1) + END_HOLD
        total = voiced + fixed
        if lo + 0.5 <= total <= hi - 0.5:
            break
        want = (lo + 1.5) if total < lo + 0.5 else (hi - 1.5)
        new = min(base + 0.1, max(base - 0.1, speed * voiced / max(want - fixed, 1.0)))
        if abs(new - speed) < 0.005:
            break
        speed = new
    times, t = [], LEAD
    for c in clips:
        times.append((t, t + len(c) / v3.SR))
        t = times[-1][1] + GAP
    total = times[-1][1] + END_HOLD
    samples = np.zeros(int(math.ceil(total * v3.SR)) + 1, dtype=np.float32)
    for (a, _), c in zip(times, clips):
        k = int(a * v3.SR)
        samples[k : k + len(c)] += c[: len(samples) - k]
    voice = {"engine": getattr(tts, "engine", "fake"), "credit": getattr(tts, "credit", ""), "label": getattr(tts, "label", ""), "speed": round(speed, 2)}
    return times, samples, total, voice


def build_audio(voice_samples, total: float, work: Path) -> tuple[Path, dict]:
    """声＋自作の BGM（声の下で自動で下げる。v3 と同じ部品）→ -14 LUFS"""
    import numpy as np

    import shorts_v3 as v3

    n = int(round(total * v3.SR))
    voice = np.zeros(n, dtype=np.float32)
    voice[: min(n, len(voice_samples))] = voice_samples[:n]
    bgm = v3.synth_bgm(total)[:n]
    bgm = np.pad(bgm, (0, n - len(bgm)))
    gain, sep = v3.duck_gain(voice, bgm)
    mix = voice + bgm * gain
    return v3.master(mix, total, work), {"bgm": "自作（synth_bgm・外部音源なし）", **sep}


# --------------------------------------------------------------------------- #
# 5. 1本を作って検査する（L3 / L4）
# --------------------------------------------------------------------------- #


def fetch_image(url: str, dest: Path) -> str:
    """サイトに保存した公式の画像を手元に置く（失敗したら空＝その画像は使わない）"""
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1000:
        return str(dest)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (saisoku video ladder)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        if len(data) < 1000:
            return ""
        dest.write_bytes(data)
        from PIL import Image

        Image.open(dest).verify()
        return str(dest)
    except Exception:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        return ""


def text_records(tl: Timeline) -> list[dict]:
    import shorts
    import video_gate as gate

    out = []
    for it in tl.items:
        face = "logo" if it.role == "logo" else ("black" if it.role in ("hook", "keyword", "conclusion") else "bold")
        out.append({
            "role": it.role, "text": it.text, "bbox": [round(v, 1) for v in it.bbox()], "box": list(it.box),
            "size": it.size, "min_size": {"subtitle": 32, "keyword": 36, "hook": 32, "conclusion": 36, "eyebrow": 20, "cta": 24, "credit": 16, "logo": 22}.get(it.role, 16),
            "contrast": round(gate.contrast(it.color, STAGE), 2), "missing": gate.missing_glyphs(it.text, shorts.font(face, 40)),
            "safe": True, "t0": round(it.t0, 3), "t1": round(it.t1, 3),
        })  # fmt: skip
    for text, bx, size in tl.card:
        out.append({"role": "card", "text": text, "bbox": [round(v, 1) for v in bx], "box": list(IMG), "size": size, "min_size": 28,
                    "contrast": round(gate.contrast(WHITE, NAVY_BOTTOM), 2), "missing": gate.missing_glyphs(text, shorts.font("black", 40)),
                    "safe": True, "t0": 0.0, "t1": round(tl.total, 3)})  # fmt: skip
    return out


def layout_problems(report: dict) -> list[str]:
    """描いた位置の検査（L3/L4 の geometry）: 安全領域・枠・文字の面積・文字どうし／文字と画像のかぶり"""
    import video_gate as gate

    out = []
    texts = report.get("texts") or []
    for t in texts:
        if not gate._inside(t["bbox"], SAFE):
            out.append(f"文字が安全領域の外: {t['role']}「{t['text'][:10]}」")
        if t.get("box") and not gate._inside(t["bbox"], t["box"]):
            out.append(f"文字が枠からはみ出す: {t['role']}「{t['text'][:10]}」")
        if t["role"] != "card" and gate._overlap(t["bbox"], IMG) > 0:
            out.append(f"文字が画像にかぶる: {t['role']}「{t['text'][:10]}」")
    ratio, at = gate.text_ratio_max(texts, float(report.get("total") or 0), (W, H))
    if ratio > TEXT_RATIO_MAX:
        out.append(f"文字が画面の {ratio:.0%} を占める（{at:.1f}秒。上限 {TEXT_RATIO_MAX:.0%}）")
    for i, a in enumerate(texts):
        for b in texts[i + 1 :]:
            if a["role"] == b["role"] == "subtitle" or {a["role"], b["role"]} == {"credit"}:
                continue
            if float(a["t0"]) < float(b["t1"]) - 0.05 and float(b["t0"]) < float(a["t1"]) - 0.05 and gate._overlap(a["bbox"], b["bbox"]) > 4:
                out.append(f"文字どうしがかぶる: 「{a['text'][:8]}」と「{b['text'][:8]}」")
    # 字幕をつなぐと読み上げと同じ（字幕 ↔ 読み上げ）
    for u, line in zip(report.get("units") or [], report.get("speech_lines") or []):
        if plain("".join(c["text"] for c in u.get("chunks") or [])) != plain(line):
            out.append(f"字幕と読み上げが違う（{u.get('kind')}）")
    return out


VISION_SYSTEM = """あなたはニュース動画の厳しい最終チェック係です。横型 1920x1080 の動画のコマ（1枚＝1場面）と、そのときの読み上げ・左の要点を見て、
コマごとに、投稿に出してはいけない重大な問題があるかを判定します。画面の左は小見出しと大きな要点の文字、右は公式発表の写真・図（無いときは紺の無地のカード）、
下は読み上げと同じ字幕、一番下は小さなクレジットです。重大な問題:
1. 文字が切れている・画面や枠からはみ出している・文字どうしが重なって読めない・小さすぎて読めない
2. 左の要点と、読み上げ・字幕が別の話（同じ点を話していない）
3. 写真・図が壊れている・真っ白・ほぼ単色・記事と関係が無い・実在の製品を AI で描いた作り物に見える
4. 日本語が壊れている・不自然で意味が通らない
細かい好み（もっと派手に、など）は問題にしません。ok が false のときは issue に、何がどうだめかを短く日本語で書きます（true のときは空文字）。
すべてのコマについて index 順に答えます。"""

VISION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "frames": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"index": {"type": "INTEGER"}, "ok": {"type": "BOOLEAN"}, "issue": {"type": "STRING"}},
                "required": ["index", "ok", "issue"],
            },
        }
    },
    "required": ["frames"],
}


def keyframes(mp4: Path, tl: Timeline, work: Path, limit: int = 4) -> list[tuple[Path, str]]:
    """場面ごとの真ん中のコマ（最大 limit 枚。つかみ・最初の場面・まん中・締め）"""
    import shorts

    picks = tl.keyframes
    if len(picks) > limit:
        idx = sorted({0, 1, len(picks) // 2, len(picks) - 1})
        picks = [picks[i] for i in idx][:limit]
    out = []
    for k, (t, speech, cap) in enumerate(picks):
        p = work / f"kf{k}.jpg"
        subprocess.run([shorts.ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{min(t, tl.total - 0.1):.2f}", "-i", str(mp4), "-frames:v", "1",
                        "-vf", "scale=960:540", "-q:v", "4", str(p)], check=True)  # fmt: skip
        out.append((p, f"{t:.1f}秒 要点「{cap}」 読み上げ「{speech}」"))
    return out


def contact_sheet(paths: list[Path], out: Path) -> None:
    """場面ごとのコマを2列に並べた見比べ用の画像（Artifacts で動画を開かずに見られる）"""
    from PIL import Image

    ims = [Image.open(p).convert("RGB").resize((480, 270)) for p in paths]
    if not ims:
        return
    cols = 2
    rows = math.ceil(len(ims) / cols)
    sheet = Image.new("RGB", (cols * 492 + 12, rows * 282 + 12), (226, 230, 237))
    for i, im in enumerate(ims):
        sheet.paste(im, (12 + (i % cols) * 492, 12 + (i // cols) * 282))
    sheet.save(out)


def claude_vision(frames_by_variant: dict[str, list[tuple[Path, str]]]) -> tuple[dict | None, str]:
    """Claude API（llm.generate_json_images。purpose=video_review の配分・予算）に、2つの版のコマをまとめて1回で見せる。
    戻り値 ({版: {"ok", "problems"}}, 使ったモデル)。使えなければ (None, 理由)"""
    metas = [(v, path, note) for v, frames in frames_by_variant.items() for path, note in frames]
    if not metas:
        return None, "見るコマが無い"
    try:
        import llm

        images = [path.read_bytes() for _v, path, _n in metas]
        lines = [f"[{k}] {'短い版' if v == 'short' else '全部の版'}・{note}" for k, (v, _p, note) in enumerate(metas)]
        prompt = "コマは上の画像の順（index 0 から）。各コマの情報:\n" + "\n".join(lines) + "\n\n判定を JSON で返してください。"
        data, model = llm.generate_json_images(VISION_SYSTEM, prompt, images, VISION_SCHEMA, purpose="video_review")
    except Exception as error:  # noqa: BLE001 - レビューできないだけ（下の決まりで判定）
        return None, f"{type(error).__name__}: {str(error)[:160]}"
    got = {int(f.get("index", -1)): f for f in data.get("frames") or [] if isinstance(f, dict)}
    out: dict[str, dict] = {v: {"ok": True, "problems": []} for v in frames_by_variant}
    for k, (v, _p, note) in enumerate(metas):
        f = got.get(k)
        if f is None or not f.get("ok"):
            out[v]["ok"] = False
            out[v]["problems"].append(f"{note[:20]}: {str((f or {}).get('issue') or '判定が無い')[:80]}")
    return out, model


def finish_gate(mp4: Path, report: dict, article: dict, profile: str) -> dict:
    """品質ゲート（video_gate.check_video の共通の検査）＋ L3/L4 の描いた位置の検査。qa の vision は review_versions で"""
    import video_gate as gate

    res = gate.check_video(mp4, report, augmented(article), profile) if mp4.exists() else gate.GateResult(ok=False, reasons=["動画ファイルが無い"])
    g = res.to_dict()
    geo = layout_problems(report) if mp4.exists() else ["動画ファイルが無い"]
    lo, hi = report.get("length_range") or (0, 999)
    dur = float((g.get("metrics") or {}).get("duration") or 0)
    if mp4.exists() and not (lo <= dur <= hi):
        g["reasons"].append(f"長さが {lo:.0f}〜{hi:.0f}秒の外（{dur:.1f}秒）")
    g["reasons"] += [f"位置: {p}" for p in geo] + [f"計画: {p}" for p in report.get("plan_problems") or []]
    g["qa"] = {"geometry": not geo and not report.get("plan_problems"), "vision": False, "version": QA_VERSION, "how": "ladder"}
    g["ok"] = not g["reasons"]
    return g


def make_simple(slug: str, level: str, variant: str, out_dir: Path, article: dict, tts="voicevox", profile: str | None = None,
                images: list[dict] | None = None, script: dict | None = None, fps: int = FPS, out_scale: float = 1.0,
                pictures: dict | None = None, workers: int | None = None) -> tuple[dict, "Timeline | None", "SimplePlan"]:
    """L3/L4 の1本: 計画 → 検査 → 声 → 描画 → ゲート（vision はあとで）。例外は投げず report に残す。
    pictures: {url: 手元のパス}（テスト用。無ければダウンロード）"""
    t_start = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{slug}__v3" + ("short" if variant == "short" else "")
    mp4 = out_dir / f"{name}.mp4"
    mp4.unlink(missing_ok=True)
    plan = build_plan(article, level, variant, images=images, script=script)
    report: dict = {"slug": slug, "version": f"ladder-{level}", "design": f"ladder-{level}", "level": level, "variant": variant,
                    "design_name": "公式の画像と字幕" if level == "L3" else "要点と声", "errors": [], "pages": [], "frame": [W, H],
                    "layout": "landscape", "safe": list(SAFE), "fonts": [FONT_FAMILY, LOGO_FAMILY], "length_range": list(plan.length_range)}  # fmt: skip
    tl = None
    try:
        # 画像を手元に（取れない画像は外す。1枚も無ければブランドのカード）
        got = []
        for img in plan.images:
            path = (pictures or {}).get(img["url"]) or fetch_image(img["url"], out_dir / "_images" / (re.sub(r"[^A-Za-z0-9.-]", "_", img["url"].rsplit("/", 1)[-1]) or "img.jpg"))
            if path:
                got.append({**img, "path": path})
        if len(got) != len(plan.images):
            plan = build_plan(article, level, variant, images=[{k: v for k, v in g.items() if k != "path"} for g in got], script=script)
        if level == "L3" and not got:
            raise RuntimeError("公式の画像を取れない（L3 は写真・図が要る）")
        report["plan_problems"] = plan_problems(plan, article)
        if report["plan_problems"]:
            raise RuntimeError("計画の検査に落ちた: " + " / ".join(report["plan_problems"][:3]))
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            times, samples, total, voice = narrate(plan.lines(), tts, work, plan.length_range)
            credits = {"voice": voice.get("credit", ""), "image": got[0]["credit"] if got else ""}
            tl = build_timeline(plan, times, total, credits)
            audio, audio_info = build_audio(samples, total, work)
            size = (IMG[2] - IMG[0], IMG[3] - IMG[1])
            card_boxes: list = []
            pics = [load_picture(g["path"], size, g.get("kind", "photo")) for g in got] or [
                brand_card(size, plan.subject or plan.source_name, f"出典：{plan.source_name}" if plan.subject and plan.source_name else "", card_boxes)]
            tl.card = card_boxes
            encode(Renderer(plan, tl, pics), audio, mp4, fps=fps, out_scale=out_scale, workers=workers)
        report["audio"] = audio_info
        report.update(report_fields(plan, tl, voice, article))
    except Exception as error:  # noqa: BLE001 - 失敗は report に残す（次の段へ）
        import traceback

        report["errors"].append(f"{type(error).__name__}: {error}")
        report["traceback"] = traceback.format_exc()[-1500:]
        log(f"[ladder] {name} {level}: 失敗 {type(error).__name__}: {str(error)[:200]}")
    prof = profile or ("production" if (report.get("voice") or {}).get("engine") == "voicevox" else "preview")
    if report["errors"]:
        report["gate"] = {"ok": False, "reasons": [f"描画: {e}" for e in report["errors"]], "warnings": [], "metrics": {},
                          "qa": {"geometry": False, "vision": False, "version": QA_VERSION, "how": "ladder"}}  # fmt: skip
    else:
        report["gate"] = finish_gate(mp4, report, article, prof)
    report["allVerbatim"] = all_verbatim(plan, article)
    report["timings"] = {"total": round(time.time() - t_start, 1)}
    report["sizeMB"] = round(mp4.stat().st_size / 1e6, 2) if mp4.exists() else 0
    return report, tl, plan


def report_fields(plan: SimplePlan, tl: Timeline, voice: dict, article: dict) -> dict:
    """sns_video_followup・品質ゲートが読む report の欄（v3 と同じ名前）"""
    url = f"https://ai-tech-express.jp/articles/{plan.slug}"
    title = plan.title if len(plan.title) <= 40 else plan.title[:39] + "…"
    image_credit = tl.credits[0] if plan.images and tl.credits else ""
    desc = [plan.summary, "", "▼記事はこちら", url, ""]
    if voice.get("credit"):
        desc.append(f"音声：{voice['credit']}")
    if image_credit:
        desc.append(image_credit)
    desc += ["BGM：サイソクニュース自作", "ai-tech-express.jp"]
    return {
        "company": plan.subject, "title": title, "url": url, "description": "\n".join(desc), "hashtags": ["#サイソク"],
        "script": {
            "title": plan.title, "headline": plan.hook_text,
            "hook": {"caption": plan.hook_text, "speech": plan.hook_speech},
            "points": [{"caption": b.key, "speech": b.speech} for b in plan.beats],
            "use": {"caption": plan.ending_text, "speech": plan.ending_speech}, "numbers": [], "hook_text": "",
            "summary": plan.summary,
        },
        # 投稿文（sns_video_followup.post_report / compose_post）が読む所だけ。場面は simple_script に
        "script_v3": {"hook": {"text": plan.hook_text}, "summary": plan.summary},
        "simple_script": {"level": plan.level, "from": plan.script_from, "hook": plan.hook_speech,
                          "beats": [{"label": b.label, "key": b.key, "speech": b.speech, "source": b.source, "image": b.image} for b in plan.beats],
                          "ending": plan.ending_speech},
        "speech_lines": plan.lines(), "units": tl.units, "total": round(tl.total, 3), "end_start": round(tl.total, 3),
        "allowed_silences": [[0.0, LEAD + 0.05], [round(tl.line_times[-1][1], 3), round(tl.total, 3)]],
        "texts": text_records(tl), "credits_shown": tl.credits, "voice": voice, "image_credit": image_credit,
        "images": [{"url": x["url"], "credit": x["credit"], "kind": x.get("kind", "")} for x in plan.images],
        "fps": FPS, "variant": plan.variant, "template": f"ladder-{plan.level}",
    }  # fmt: skip


def review_versions(level: str, made: dict, article: dict, work: Path, reviewer=None) -> None:
    """版ごとに vision を決める（report["gate"]["qa"]["vision"]）。reviewer(frames_by_variant) → (dict | None, how)"""
    reviewer = reviewer or claude_vision
    frames: dict[str, list] = {}
    for v, (rep, tl, _plan) in made.items():
        mp4 = Path(rep.get("_mp4", ""))
        if rep["gate"].get("ok") is False and rep["gate"]["reasons"] or tl is None or not mp4.exists():
            continue
        try:
            (work / v).mkdir(parents=True, exist_ok=True)
            frames[v] = keyframes(mp4, tl, work / v)
            contact_sheet([p for p, _ in frames[v]], mp4.with_suffix(".sheet.png"))
        except Exception as error:  # noqa: BLE001
            rep["gate"]["reasons"].append(f"コマを取り出せない: {error}")
    verdicts, how = (reviewer(frames) if frames else (None, "見るコマが無い"))
    for v, (rep, _tl, plan) in made.items():
        g = rep["gate"]
        if v not in frames:
            g["ok"] = False
            continue
        verdict = (verdicts or {}).get(v) if isinstance(verdicts, dict) else None
        if isinstance(verdict, dict) and "ok" in verdict:
            g["qa"]["vision"] = bool(verdict.get("ok"))
            g["qa"]["vision_how"] = how
            if not verdict.get("ok"):
                g["reasons"] += [f"画像のレビュー: {p}" for p in (verdict.get("problems") or ["問題あり"])[:4]]
        elif level == "L4" and rep.get("allVerbatim") and g["qa"]["geometry"] and not g["reasons"]:
            # レビューが使えない: L4 は画面の文字がすべて記事の文そのまま（か決まった定型の文）で、検査が全部通ったときだけ合格（docstring）
            g["qa"]["vision"] = True
            g["qa"]["vision_how"] = f"deterministic-L4（レビューなし: {how}）"
        else:
            g["qa"]["vision_how"] = f"レビューできない（{how}）"
            g["reasons"].append(f"画像のレビューができない（{how}）")
        g["ok"] = not g["reasons"] and g["qa"]["geometry"] and g["qa"]["vision"]


def run_simple(level: str, slug: str, out_dir: Path, need: list[str], article: dict, tts="voicevox", profile: str | None = None,
               script: dict | None = None, reviewer=None, text_only: bool = False, **kw) -> dict:
    """L3/L4 を版ごとに作って検査する。戻り値 {版: report}"""
    made = {}
    for v in need:
        rep, tl, plan = make_simple(slug, level, v, out_dir, article, tts=tts, profile=profile, images=[] if text_only else None, script=script, **kw)
        rep["_mp4"] = str(out_dir / f"{slug}__v3{'short' if v == 'short' else ''}.mp4")
        made[v] = (rep, tl, plan)
    review_versions(level, made, article, out_dir / "_frames", reviewer=reviewer)
    return {v: r for v, (r, _t, _p) in made.items()}


# --------------------------------------------------------------------------- #
# 6. L1 / L2（shorts_v3 をそのまま。別のプロセスで時間を区切る）
# --------------------------------------------------------------------------- #


def qa_ok(report: dict, env: dict | None = None) -> bool:
    """sns_video_followup.qa_ok と同じ: 描いた位置（geometry）と画像のレビュー（vision）の両方に合格。SNS_VIDEO_REQUIRE_QA=off で止める"""
    env = os.environ if env is None else env
    if env.get("SNS_VIDEO_REQUIRE_QA", "on") == "off":
        return True
    qa = ((report or {}).get("gate") or {}).get("qa") or {}
    return bool(qa.get("geometry")) and bool(qa.get("vision"))


def passes(report: dict | None, mp4: Path) -> bool:
    return bool(report) and bool((report.get("gate") or {}).get("ok")) and qa_ok(report) and mp4.exists()


def feedback_note(reports: dict | None) -> str:
    """L2 の台本に足す一言: 前の回の検査で落ちた理由（同じ問題を起こさないように）"""
    reasons = []
    for r in (reports or {}).values():
        reasons += [str(x) for x in ((r or {}).get("gate") or {}).get("reasons") or []]
    reasons = [x for x in dict.fromkeys(reasons) if x][:8]
    if not reasons:
        return ""
    return "\n\n# 前の回の動画は、次の理由で検査に落ちました。同じ問題が起きない台本にしてください（証拠の文・キーワード・場面の選び方を変える）\n" + "\n".join(f"- {x[:160]}" for x in reasons)


def run_v3(slug: str, out_dir: Path, tts: str = "voicevox", profile: str | None = None, feedback: str = "", **kw) -> dict:
    """L1/L2: shorts_v3.make_versions をそのまま（feedback があれば、台本の決まりの最後に足す）"""
    import shorts_v3 as v3

    if feedback:
        orig = v3.write_script

        def with_feedback(article, pages, note="", *args, **kwargs):
            return orig(article, pages, (note or "") + feedback, *args, **kwargs)

        v3.write_script = with_feedback  # 別のプロセスの中だけ（isolated）。同じプロセスなら下で戻す
        try:
            return v3.make_versions(slug, out_dir, tts=tts, profile=profile, **kw)
        finally:
            v3.write_script = orig
    return v3.make_versions(slug, out_dir, tts=tts, profile=profile, **kw)


def isolated(fn, timeout: float, *args, **kwargs) -> tuple[dict | None, str]:
    """fn を別のプロセス（自分のプロセスグループ）で動かし、timeout 秒で止める。戻り値 (結果, 失敗の理由)"""
    if not hasattr(os, "fork") or timeout <= 0:
        return (None, "時間切れ（始める前）") if timeout <= 0 else (fn(*args, **kwargs), "")
    fd, path = tempfile.mkstemp(suffix=".json", prefix="ladder_")
    os.close(fd)
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:  # 子
        code = 1
        try:
            os.setpgid(0, 0)
            res = fn(*args, **kwargs)
            Path(path).write_text(json.dumps(res, ensure_ascii=False, default=str), "utf-8")
            code = 0
        except BaseException as error:  # noqa: BLE001
            Path(path).write_text(json.dumps({"__error__": f"{type(error).__name__}: {error}"}, ensure_ascii=False), "utf-8")
        finally:
            sys.stdout.flush()
            os._exit(code)
    try:
        os.setpgid(pid, pid)
    except OSError:
        pass
    end = time.monotonic() + timeout
    while True:
        done, _status = os.waitpid(pid, os.WNOHANG)
        if done:
            break
        if time.monotonic() > end:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pid, sig)
                except OSError:
                    pass
                time.sleep(1.5)
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            Path(path).unlink(missing_ok=True)
            return None, f"時間切れ（{timeout:.0f}秒）"
        time.sleep(0.3)
    try:
        data = json.loads(Path(path).read_text("utf-8") or "null")
    except (OSError, json.JSONDecodeError):
        data = None
    Path(path).unlink(missing_ok=True)
    if isinstance(data, dict) and "__error__" in data:
        return None, data["__error__"]
    return data, "" if data is not None else "結果を読めない"


# --------------------------------------------------------------------------- #
# 7. 段を順に試す
# --------------------------------------------------------------------------- #


def _out_name(slug: str, variant: str) -> str:
    return f"{slug}__v3" + ("short" if variant == "short" else "")


def l1_skip_l2(reports: dict | None) -> str:
    """L1 の失敗が「もう一度やっても同じ」もの（撮れるページが無い・v3 の対象外）なら、L2 を飛ばす理由"""
    why = " ".join(str((r or {}).get("failReason") or " ".join(((r or {}).get("gate") or {}).get("reasons") or [])) for r in (reports or {}).values())
    if re.search(r"撮れるページが無い|撮ってよい公式ページがありません|対象外", why):
        return "撮れる公式ページが無い（もう一度やっても同じ）"
    return ""


def latest_script(dirs: list[Path], slug: str) -> dict | None:
    """L2 → L1 の順に、書いた台本（v3script.json）を探す（L3 の場面に使う）"""
    for d in dirs:
        for p in sorted(d.glob(f"{slug}*.v3script.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                return json.loads(p.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return None


def make_versions(slug: str, out_dir: Path, deadline_s: float = DEADLINE_S, tts: str = "voicevox", profile: str | None = None,
                  levels: tuple | list = LEVELS, runners: dict | None = None, clock=time.monotonic, isolate: bool = True,
                  article: dict | None = None, reviewer=None, **kw) -> dict:
    """段を L1 → L4 の順に試し、版ごとに最初に合格した段の動画を out_dir/<slug>__v3.mp4（full）・__v3short.mp4（short）に置く。
    戻り値 {"full": report, "short": report}（shorts_v3.make_versions と同じ形。report["level"]・report["ladder"]・report["gate"]["qa"]）。
    runners: {"L1": fn(...), ...}（テスト用。fn(slug, level_dir, need, article, prev, remaining) → {版: report}）"""
    import shorts

    t0 = clock()
    out_dir = Path(out_dir)
    a = article or shorts.load_article(slug)
    root = out_dir / "_ladder" / slug
    shutil.rmtree(root, ignore_errors=True)
    for v in VARIANTS:  # 前の回の動画が残っていても使わない
        for ext in (".mp4", ".report.json", ".sheet.png"):
            (out_dir / f"{_out_name(slug, v)}{ext}").unlink(missing_ok=True)
    chosen: dict[str, tuple[str, dict, Path]] = {}
    last: dict[str, dict] = {}
    ladder: list[dict] = []
    prev: dict | None = None
    has_l4 = "L4" in levels
    for level in levels:
        need = [v for v in VARIANTS if v not in chosen]
        if not need:
            break
        remaining = deadline_s - (clock() - t0)
        level_dir = root / level
        level_dir.mkdir(parents=True, exist_ok=True)
        skip = ""
        if level == "L2":
            skip = l1_skip_l2(prev) if prev is not None else ""
        if level == "L3" and not official_images(a):
            skip = "公式の画像が無い（L3 は写真・図が要る）"
        reserve = (L4_RESERVE_S if has_l4 and level != "L4" else 0.0) + (MIN_SECONDS["L3"] if level in ("L1", "L2") and "L3" in levels and official_images(a) else 0.0)
        if not skip and level != "L4" and remaining - reserve < MIN_SECONDS[level]:
            skip = f"時間が足りない（残り {remaining:.0f}秒）"
        if skip:
            ladder.append({"level": level, "ok": False, "skipped": True, "reasons": [skip], "seconds": 0.0, "variants": {}})
            log(f"[ladder] {slug} {level}: 飛ばす（{skip}）")
            continue
        st = clock()
        log(f"[ladder] {slug} {level}: 開始（版 {need}・残り {remaining:.0f}秒）")
        reports, err = run_level(level, slug, level_dir, need, a, prev, remaining - reserve, runners, isolate, tts, profile, reviewer, root, **kw)
        seconds = round(clock() - st, 1)
        got = {}
        for v in need:
            rep = (reports or {}).get(v)
            mp4 = level_dir / f"{_out_name(slug, v)}.mp4"
            ok = passes(rep, mp4)
            got[v] = ok
            if rep:
                last[v] = rep
            if ok:
                chosen[v] = (level, rep, mp4)
        reasons = [err] if err else []
        for v in need:
            if not got.get(v):
                rs = ((last.get(v) or {}).get("gate") or {}).get("reasons") or []
                if (last.get(v) or {}).get("gate", {}).get("ok") and not qa_ok(last.get(v)):
                    rs = ["描いた位置・画像のレビューの検査（qa）が無い・不合格"] + list(rs)
                reasons += [f"{v}: {x}" for x in rs[:3]]
        entry = {"level": level, "ok": all(got.get(v) for v in need), "reasons": reasons[:8], "seconds": seconds, "variants": got}
        ladder.append(entry)
        log(f"[ladder] {slug} {level}: {'合格' if entry['ok'] else '不合格'} {seconds}秒 {got} {reasons[:2]}")
        if level in ("L1", "L2"):
            prev = reports or prev
        if level == "L4":
            # L4 の写真つきが落ちた版は、写真なし（文字とブランドのカード）でもう1回
            need2 = [v for v in need if not got.get(v)]
            if need2 and official_images(a) and (runners is None or "L4text" in runners):
                st = clock()
                dir2 = root / "L4text"
                dir2.mkdir(parents=True, exist_ok=True)
                reports2, err2 = run_level("L4text", slug, dir2, need2, a, prev, deadline_s - (clock() - t0), runners, isolate, tts, profile, reviewer, root, **kw)
                got2 = {}
                for v in need2:
                    rep = (reports2 or {}).get(v)
                    mp4 = dir2 / f"{_out_name(slug, v)}.mp4"
                    got2[v] = passes(rep, mp4)
                    if rep:
                        last[v] = rep
                    if got2[v]:
                        chosen[v] = ("L4", rep, mp4)
                ladder.append({"level": "L4", "mode": "text", "ok": all(got2.values()), "seconds": round(clock() - st, 1), "variants": got2,
                               "reasons": ([err2] if err2 else []) + [f"{v}: {x}" for v in need2 if not got2.get(v) for x in ((last.get(v) or {}).get("gate") or {}).get("reasons", [])[:3]]})  # fmt: skip
    out = {}
    total_s = round(clock() - t0, 1)
    for v in VARIANTS:
        if v in chosen:
            level, rep, mp4 = chosen[v]
            dst = out_dir / f"{_out_name(slug, v)}.mp4"
            shutil.copyfile(mp4, dst)
            sheet = mp4.with_suffix(".sheet.png")
            if sheet.exists():
                shutil.copyfile(sheet, out_dir / f"{_out_name(slug, v)}.sheet.png")
            rep = dict(rep)
            rep["level"] = level
        else:
            rep = dict(last.get(v) or {"slug": slug, "errors": ["どの段でも作れなかった"], "gate": {"ok": False, "reasons": ["どの段でも作れなかった"]}})
            rep["level"] = None
            rep.setdefault("gate", {}).setdefault("qa", {"geometry": False, "vision": False, "version": QA_VERSION})
            rep["gate"]["ok"] = False
            rep["failReason"] = rep.get("failReason") or "どの段でも合格しなかった: " + " / ".join(" ".join(e.get("reasons", [])[:1]) for e in ladder)[:300]
        rep.pop("_mp4", None)
        rep["ladder"] = ladder
        rep.setdefault("timings", {})
        rep["timings"] = {**(rep.get("timings") or {}), "ladder_total": total_s}
        (out_dir / f"{_out_name(slug, v)}.report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), "utf-8")
        out[v] = rep
    log(f"[ladder] {slug}: full={out['full'].get('level')} short={out['short'].get('level')} {total_s}秒")
    return out


def run_level(level: str, slug: str, level_dir: Path, need: list[str], a: dict, prev: dict | None, budget: float, runners: dict | None,
              isolate: bool, tts: str, profile: str | None, reviewer, root: Path, **kw) -> tuple[dict | None, str]:
    """1つの段を動かす。戻り値 ({版: report}, 失敗の理由)"""
    if runners and level in runners:
        try:
            return runners[level](slug=slug, level_dir=level_dir, need=need, article=a, prev=prev, remaining=budget), ""
        except Exception as error:  # noqa: BLE001
            return None, f"{type(error).__name__}: {error}"
    if level in ("L1", "L2"):
        fb = feedback_note(prev) if level == "L2" else ""
        fn_kw = {"tts": tts, "profile": profile, "feedback": fb, **{k: v for k, v in kw.items() if k in ("extra_pages", "template")}}
        if isolate:
            return isolated(run_v3, budget, slug, level_dir, **fn_kw)
        try:
            return run_v3(slug, level_dir, **fn_kw), ""
        except Exception as error:  # noqa: BLE001
            return None, f"{type(error).__name__}: {error}"
    try:
        if level == "L3":
            script = latest_script([root / "L2", root / "L1"], slug)
            return run_simple("L3", slug, level_dir, need, a, tts=tts, profile=profile, script=script, reviewer=reviewer, **kw), ""
        return run_simple("L4", slug, level_dir, need, a, tts=tts, profile=profile, reviewer=reviewer, text_only=(level == "L4text"), **kw), ""
    except Exception as error:  # noqa: BLE001
        return None, f"{type(error).__name__}: {error}"


# --------------------------------------------------------------------------- #
# 8. 段の内訳と知らせ（L3/L4 が直近20本の3割を超えたら issue）
# --------------------------------------------------------------------------- #

_ORDER = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}


def level_of(rec: dict) -> str | None:
    """記録（state/sns_video.json の videos の1件）の段: rec["level"]、無ければ合格した版の段のうち一番下（SNS に出る短い版が下の段なら下の段と数える）"""
    if rec.get("level") in _ORDER:
        return rec["level"]
    levels = [v.get("level") for v in (rec.get("versions") or {}).values() if isinstance(v, dict) and v.get("ok") and v.get("level") in _ORDER]
    return max(levels, key=_ORDER.get) if levels else None


def level_stats(state: dict, last: int = FALLBACK_WINDOW) -> dict:
    """直近 last 本（作り終えた記録・新しい順）の段の内訳。段の無い古い記録（ladder 以前）は数えない"""
    rows = []
    for slug, rec in (state.get("videos") or {}).items():
        if not isinstance(rec, dict) or rec.get("status") == "started":
            continue
        lv = level_of(rec)
        if lv is None and not any("level" in (v or {}) for v in (rec.get("versions") or {}).values()) and "level" not in rec:
            continue
        rows.append((rec.get("at", ""), slug, lv or "none"))
    rows.sort(reverse=True)
    rows = rows[:last]
    by = {k: 0 for k in (*LEVELS, "none")}
    for _, _, lv in rows:
        by[lv] = by.get(lv, 0) + 1
    n = len(rows)
    share = (by["L3"] + by["L4"]) / n if n else 0.0
    return {"n": n, "by_level": by, "fallback_share": round(share, 3), "slugs": [(s, lv) for _, s, lv in rows]}


def fallback_alert(state: dict, env: dict | None = None, run=subprocess.run) -> str:
    """L3/L4 が直近20本の3割を超えたら issue（ラベル sns-video-fallback）を作るか、開いている issue に追記する。GitHub Actions の中だけ"""
    env = os.environ if env is None else env
    st = level_stats(state)
    if st["n"] < FALLBACK_MIN or st["fallback_share"] <= FALLBACK_SHARE:
        return "ok"
    if env.get("GITHUB_ACTIONS") != "true":
        return "skip-local"
    body = (f"直近 {st['n']} 本の SNS 動画のうち、下の段（L3 公式の画像＋字幕／L4 要点と声）で作った動画が {st['fallback_share']:.0%}（基準 {FALLBACK_SHARE:.0%}）。\n\n"
            f"段の内訳: {json.dumps(st['by_level'], ensure_ascii=False)}\n\n"
            + "\n".join(f"- `{s}`: {lv}" for s, lv in st["slugs"])
            + "\n\nL1（公式ページの画面の型）が落ちる理由を `state/sns_video.json` の versions[].reasons・ladder で確かめてください。")  # fmt: skip
    try:
        run(["gh", "label", "create", FALLBACK_LABEL, "--color", "FBCA04", "--description", "SNS 動画が下の段（L3/L4）に頼りすぎている", "--force"],
            check=False, capture_output=True, text=True, timeout=60)  # fmt: skip
        found = run(["gh", "issue", "list", "--label", FALLBACK_LABEL, "--state", "open", "--json", "number", "--limit", "1"], check=False, capture_output=True, text=True, timeout=60)
        rows = json.loads(found.stdout or "[]") if getattr(found, "stdout", "") else []
        if rows:
            run(["gh", "issue", "comment", str(rows[0]["number"]), "--body", body], check=False, capture_output=True, text=True, timeout=60)
            return f"commented #{rows[0]['number']}"
        run(["gh", "issue", "create", "--title", f"SNS 動画が下の段に頼りすぎ（L3/L4 が {st['fallback_share']:.0%}）", "--label", FALLBACK_LABEL, "--body", body],
            check=False, capture_output=True, text=True, timeout=60)  # fmt: skip
        return "created"
    except Exception as error:  # noqa: BLE001
        log(f"[ladder] issue を作れませんでした: {type(error).__name__}")
        return "error"


# --------------------------------------------------------------------------- #
# 9. 直近の記事での確かめ（投稿しない）
# --------------------------------------------------------------------------- #


def recent_slugs(n: int = 20) -> list[str]:
    """直近の SNS 向けの記事（genreSlug が ai か、Threads に出した記事）。新しい順"""
    posted = {}
    try:
        posted = json.loads((ROOT / "state" / "threads_posted.json").read_text("utf-8")).get("posted") or {}
    except (OSError, json.JSONDecodeError):
        pass
    rows = []
    for p in (ROOT / "content" / "articles").glob("*.json"):
        try:
            a = json.loads(p.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if a.get("genreSlug") == "ai" or a.get("slug") in posted:
            rows.append((a.get("publishedAt", ""), a.get("slug")))
    return [s for _, s in sorted(rows, reverse=True)[:n]]


def plan_check(slugs: list[str]) -> list[dict]:
    """L3/L4 の計画と描画の前の検査だけ（ネット・声なし）"""
    import shorts

    out = []
    for slug in slugs:
        a = shorts.load_article(slug)
        row = {"slug": slug, "images": len(official_images(a)), "imageKind": (a.get("imageCredit") or {}).get("kind")}
        for level in ("L3", "L4"):
            for v in VARIANTS:
                if level == "L3" and not official_images(a):
                    row[f"{level}_{v}"] = "対象外（公式の画像なし）"
                    continue
                plan = build_plan(a, level, v)
                probs = plan_problems(plan, a)
                row[f"{level}_{v}"] = "ok" if not probs else probs[:2]
                row[f"{level}_{v}_sec"] = round(est_seconds(plan.lines()), 1)
                row[f"{level}_{v}_beats"] = len(plan.beats)
        textplan = build_plan(a, "L4", "short", images=[])
        row["L4_text"] = "ok" if not plan_problems(textplan, a) else plan_problems(textplan, a)[:2]
        out.append(row)
    return out


def l1_history(n: int = 20) -> dict:
    """state/sns_video.json の直近 n 本の L1（今の型）の結果（作り終えた記録）。qa が付いた版の数も"""
    try:
        state = json.loads(STATE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = sorted(((r.get("at", ""), s, r) for s, r in (state.get("videos") or {}).items() if isinstance(r, dict) and r.get("status") != "started"), reverse=True)[:n]
    ok = sum(1 for _, _, r in rows if r.get("ok"))
    vers = {"full": [0, 0], "short": [0, 0]}
    qa = 0
    reasons: dict[str, int] = {}
    for _, _, r in rows:
        for k, v in (r.get("versions") or {}).items():
            if k in vers:
                vers[k][1] += 1
                vers[k][0] += 1 if v.get("ok") else 0
                qa += 1 if (v.get("qa") or {}).get("geometry") and (v.get("qa") or {}).get("vision") else 0
        if not r.get("ok"):
            key = (r.get("reason") or r.get("status") or "")[:50]
            reasons[key] = reasons.get(key, 0) + 1
    return {"n": len(rows), "ok": ok, "versions": vers, "qa_passed_versions": qa, "fail_reasons": reasons}


def main() -> None:
    parser = argparse.ArgumentParser(description="SNS 動画の段の下がり方（L1→L4）。投稿しない・置かない")
    parser.add_argument("slugs", nargs="*")
    parser.add_argument("--out", default=str(ROOT / "out" / "video_ladder"))
    parser.add_argument("--tts", default=os.environ.get("SHORTS_TTS", "voicevox"), choices=["voicevox", "fake", "edge", "auto"])
    parser.add_argument("--levels", default="L1,L2,L3,L4")
    parser.add_argument("--deadline", type=float, default=DEADLINE_S)
    parser.add_argument("--profile")
    parser.add_argument("--plan-check", type=int, metavar="N", help="直近の N 本で L3/L4 の計画と検査だけ")
    parser.add_argument("--dry-run", type=int, metavar="N", help="直近の N 本を書き出して段ごとの合格率（投稿しない）")
    parser.add_argument("--no-vision", action="store_true", help="画像のレビュー（Claude API）を使わない（L4 は決まりで判定・L3 は不合格）")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--alert", action="store_true")
    parser.add_argument("--history", type=int, metavar="N")
    args = parser.parse_args()
    try:
        import llm

        llm._load_env_local()
    except Exception:  # noqa: BLE001
        pass
    if args.stats or args.alert:
        state = json.loads(STATE.read_text("utf-8")) if STATE.exists() else {}
        print(json.dumps(level_stats(state), ensure_ascii=False, indent=2))
        if args.alert:
            print(fallback_alert(state))
        return
    if args.history:
        print(json.dumps(l1_history(args.history), ensure_ascii=False, indent=2))
        return
    if args.plan_check:
        rows = plan_check(recent_slugs(args.plan_check))
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        bad = [r for r in rows if r["L4_short"] != "ok" or r["L4_full"] != "ok" or r["L4_text"] != "ok"]
        print(f"L4 の計画が検査に通った記事: {len(rows) - len(bad)}/{len(rows)}")
        sys.exit(1 if bad else 0)
    levels = tuple(x.strip() for x in args.levels.split(",") if x.strip())
    reviewer = (lambda frames: (None, "--no-vision")) if args.no_vision else None
    slugs = recent_slugs(args.dry_run) if args.dry_run else args.slugs
    if not slugs:
        parser.error("slug か --dry-run N を指定してください")
    summary = []
    for slug in slugs:
        out = make_versions(slug, Path(args.out), deadline_s=args.deadline, tts=args.tts, profile=args.profile, levels=levels, reviewer=reviewer)
        row = {"slug": slug, **{v: out[v].get("level") for v in VARIANTS}, "ladder": [{k: e.get(k) for k in ("level", "mode", "ok", "skipped", "seconds", "variants", "reasons")} for e in out["full"]["ladder"]]}
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False))
    if summary:
        tried: dict[str, list[int]] = {}
        no_review: dict[str, int] = {}  # 画像のレビューができなかっただけで、ほかの検査には通った版の数
        for row in summary:
            for e in row["ladder"]:
                if e.get("skipped"):
                    continue
                key = e["level"] + ("text" if e.get("mode") == "text" else "")
                t = tried.setdefault(key, [0, 0])
                for v, ok in (e.get("variants") or {}).items():
                    t[1] += 1
                    t[0] += 1 if ok else 0
                    rs = [r for r in e.get("reasons") or [] if r.startswith(f"{v}: ")]
                    if not ok and rs and all("画像のレビューができない" in r for r in rs):
                        no_review[key] = no_review.get(key, 0) + 1
        used = {}
        for row in summary:
            for v in VARIANTS:
                used[f"{v}:{row[v]}"] = used.get(f"{v}:{row[v]}", 0) + 1
        result = {"articles": len(summary), "pass_rate_by_level": {k: f"{a}/{b}" for k, a_b in tried.items() for a, b in [a_b]},
                  "failed_only_for_no_review": no_review, "used": used,
                  "all_produced": all(row["full"] and row["short"] for row in summary), "l1_history": l1_history(20)}  # fmt: skip
        print(json.dumps(result, ensure_ascii=False, indent=2))
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "dryrun.json").write_text(json.dumps({"summary": result, "rows": summary}, ensure_ascii=False, indent=2), "utf-8")
        sys.exit(0 if result["all_produced"] else 1)


if __name__ == "__main__":
    main()
