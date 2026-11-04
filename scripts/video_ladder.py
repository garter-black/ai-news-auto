#!/usr/bin/env python3
"""SNS 動画の段（ladder）。どの段も**同じ合格の基準**: オーナー承認の形（shorts_v3.BASE_LAYOUT「screen-left-headline」:
左に小見出し＋大きな見出し（行ごとのマーカー）、右に公式ページの本物の画面と選択の青・日本語の吹き出し）で、
位置の検査（geometry・マーカー・始まり・見出し）と画像のレビュー（vision）の両方に合格した動画だけを出す（2026-11-04 オーナー承認）。
レビューができない（API も サブスクリプションの CLI も使えない）＝不合格。静止画1枚の動画は作らない。

  L1 : 今の型（shorts_v3.make_versions。LLM の台本。中で QA に落ちたら1回だけ作り直す）
  L2 : L1 をもう1回（新しく撮り直し・前の回の検査で落ちた理由を台本の決まりに足す）
  L3A: 同じ形を LLM なしで作る（記事の文だけの台本）。見出し・小見出しは記事の題・本文の見出し・FAQ から、読み上げは記事の
       です・ますの文から、右は公式ページ（撮れなければ自分の記事ページ）の画面で、記事の数字・名前を含む文に選択の青、
       英語の文には記事の同じ数字・名前を含む文を日本語の吹き出しに。shorts_v3.make_video と同じ描画・同じゲート・同じレビュー

どの段にも通らなければ、その回は「動画なし」。sns_video_followup があとの回で作り直す（通常の投稿は枠から6時間・速報は2時間まで）。

出力は sns_video_followup.run が読む所: OUT/<slug>__v3.mp4（full）と OUT/<slug>__v3short.mp4（short）。
戻り値は shorts_v3.make_versions と同じ形 {"full": report, "short": report}。report には
  report["level"]  = "L1" / "L2" / "L3A"（使った段。どれも通らなければ None）
  report["ladder"] = [{"level", "ok", "reasons", "seconds", "variants"}]
  report["gate"]["qa"] = {"geometry", "vision", "version": 1}（shorts_v3.qa_versions が決める）

  python3 scripts/video_ladder.py <slug>                         # 段を順に試す（投稿しない・置かない）
  python3 scripts/video_ladder.py <slug> --levels L3A            # 記事の文だけの承認の形
  python3 scripts/video_ladder.py --plan-check 20                # 直近 20 本で L3A の台本の検査だけ（自分の記事ページを模した画面で。描画しない）
  python3 scripts/video_ladder.py --stats [--alert]              # 直近20本の段の内訳・承認の形の割合（9割未満なら issue）
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

LEVELS = ("L1", "L2", "L3A")
APPROVED_LEVELS = ("L1", "L2", "L3A")  # どれも承認の形（BASE_LAYOUT）で作る段
BASE_LAYOUT = "screen-left-headline"  # shorts_v3.BASE_LAYOUT と同じ
VARIANTS = ("full", "short")
QA_VERSION = 1
DEADLINE_S = float(os.environ.get("VIDEO_LADDER_DEADLINE", "840"))
L3A_RESERVE_S = 240.0  # L3A（撮影・2本の描画・レビュー）に残しておく時間
MIN_SECONDS = {"L1": 240.0, "L2": 240.0, "L3A": 0.0}  # この段を始めるのに要る残り時間（足りなければ飛ばす）
STATE = ROOT / "state" / "sns_video.json"
FALLBACK_LABEL = "sns-video-fallback"
APPROVED_MIN = 0.90  # 承認の形で作った動画の割合（直近20本）がこれ未満なら知らせる
FALLBACK_WINDOW = 20
FALLBACK_MIN = 5  # 記録がこれより少ないときは知らせない
CPS = 5.0  # 九州そら・速さ1.38 で1秒に約5字
SPEECH_MIN, SPEECH_MAX = 220, 320  # L3A の読み上げの合計（サイト用の版が 40〜80秒に入る）



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



def summary_of(article: dict) -> str:
    """投稿文の要約（threadsPost の「要約すると、」の行があればそれ。無ければリードの最初の文）"""
    for line in str(article.get("threadsPost") or "").splitlines():
        if line.startswith("要約すると"):
            return line.strip()
    lede = [narration_sentence(s) for s in sentences(article.get("lede", ""))]
    lede = [s for s in lede if s]
    return ("要約すると、" + lede[0]) if lede else ""




def _digits(text: str) -> set[str]:
    import video_gate as gate

    return set(re.findall(r"\d+(?:\.\d+)?", gate.norm(text or "")))


def stiff(text: str) -> list[str]:
    """硬い言い回し（video_gate.stiff_problems。2026-11-04 オーナー「作業の担い手」）"""
    import video_gate as gate

    return gate.stiff_problems(text) if hasattr(gate, "stiff_problems") else []


def short_phrase(text: str, max_len: int, min_len: int = 2) -> str:
    """文節（budoux）の切れ目で max_len 字まで。最後の読点・助詞を1つ落とす（「…の」「…は」で終わらない）"""
    out = ""
    for p in _phrases(clean_sentence(text)):
        if len(out + p) > max_len:
            break
        out += p
    out = re.sub(r"[、。・：:\s]+$", "", out)
    out = _TRAIL_PARTICLE.sub("", out)
    out = re.sub(r"[、。・：:\s]+$", "", out)
    return out if len(out) >= min_len else ""


BAD_LABEL_END = re.compile(r"(?:による|によると|について|として|では|での|には|から|まで|より|など|とは|って|の|へ|に|で|と|や|も|を|が|は)$")
BAD_LABEL_START = re.compile(r"^(?:この|その|あの|今回|これ|それ|同社|同氏|また|さらに|ただし|なお|結論)")


def _qless(text: str) -> str:
    t = clean_sentence(text)
    t = re.sub(r"^\s*(?:\d{1,2}|[①-⑩])[.．、)）]\s*", "", t)
    return re.sub(r"(?:ですか|ますか|でしょうか|のか|の)?[？?]+$", "", t).strip()


def topic(text: str, max_len: int = 16, need_marker: bool = False) -> str:
    """文・問いの主題（「…は」「…が」の前）。長ければ「で・と・に・を・の」の後ろへ縮める（例:「JetBrains AirのCopilotクラウド実行に必要な条件は？」→「必要な条件」）"""
    t = _qless(text)
    m = re.match(r"^(.{4,24}?)(?:では|には|も|は|が)、", t) or re.match(r"^(.+?)(?:は|が)(?![a-zA-Z])", t)
    if not m and need_marker:
        return ""
    base = (m.group(1) if m else t).rstrip("。")
    if len(base) > max_len:
        parts = re.split(r"(?:で|と|に|を|の|、)", base)
        tail = parts[-1] if parts else ""
        base = tail if len(tail) >= 4 else ""
    base = base.strip(" 、")
    if base.count("「") != base.count("」"):
        return ""
    return base if 4 <= len(base) <= max_len and not BAD_LABEL_END.search(base) and not BAD_LABEL_START.search(base) else ""


def label_candidates(hint: str, speech: str) -> list[str]:
    """小見出しの候補（切れ端にしない）: ①見出しそのもの（16字まで）②見出し・FAQ の問いの主題 ③読み上げの文の主題"""
    out = []
    h = _qless(hint)
    if h and len(h) <= 16:
        out.append(re.sub(r"(?:は|って|とは)$", "", h))
    out += [topic(hint), topic(speech, need_marker=True)]
    return [c.strip() for c in dict.fromkeys(out) if c and c.count("「") == c.count("」") and 4 <= len(c.strip()) <= 16 and not BAD_LABEL_END.search(c.strip()) and not BAD_LABEL_START.search(c.strip())]


def label_ok(label: str, used: set[str], article: dict) -> bool:
    import video_gate as gate

    key = gate.norm(re.sub(r"[\s　？?]+", "", label)).lower()
    templates = {gate.norm(x).lower() for x in gate.TEMPLATE_LABELS}
    return (2 <= len(label) <= 16 and not gate.bare_phrase(label) and key not in templates and key not in used
            and not re.search(r"[？?]\s*$", label) and not stiff(label) and not gate.numbers_ok(label, article_numbers(article)))  # fmt: skip


NUM_UNIT = re.compile(
    r"(?:約|最大|最小|最高|平均)?[0-9０-９][0-9０-９,，.．]*(?:万|億|兆)?"
    r"(?:倍|%|％|ドル|円|件|人|社|時間|分|秒|年|月|日|GB|TB|メガワット|ギガワット|kg|W|Wh|Nm|世帯|個|つ|回|段階|カ月|か月|週間|台|本|種類|言語|か国|カ国|ユーロ|トークン|行|ページ|ポイント|万人|拠点|モデル)"
)


def keyword_for(speech: str, target: str, ja: str, avoid: set[str]) -> str:
    """大きな見出し（キーワード）: 読み上げにそのままある言い回しで、選択する証拠の文（英語なら吹き出し）にもあるもの。
    数字があれば数字（日付は後回し）、無ければ英字・カタカナの名前を含む文節のまとまり（16字まで・名詞1つだけにしない）"""
    import video_gate as gate

    body = speech.rstrip("。")
    nums = []
    for m in NUM_UNIT.finditer(body):
        kw = m.group(0)
        if len(kw) >= 2 and re.search(r"[^\d.,，．]", kw) and gate.key_fact_ok(kw, "", target, ja) and gate.narration_ok(kw, speech):
            nums.append((1 if re.search(r"(?:[年月日つ個回本台]|段階)$", kw) else 0, kw))
    ph = _phrases(body)
    starts = {0} | {i for i in range(1, len(ph)) if re.search(r"(?:、|は|が|では|には|も|なく)$", ph[i - 1])}
    cands = []
    for i in sorted(starts):
        if BAD_LABEL_START.search(ph[i]):
            continue
        acc = ""
        for j in range(i, len(ph)):
            acc += ph[j]
            if len(acc) > 16:
                break
            c = _TRAIL_PARTICLE.sub("", re.sub(r"[、。・：:\s]+$", "", acc))
            if not (5 <= len(c) <= 16) or re.search(r"[、。！？!?「」]", c) or gate.bare_phrase(c) or stiff(c) or c in avoid or BAD_LABEL_END.search(c):
                continue
            if not (gate.key_fact_ok(c, "", target, ja) or gate.point_ok(c, f"{target} {ja}")) or not gate.narration_ok(c, speech):
                continue
            named = bool(re.search(r"[A-Za-z]{2,}|[ァ-ヴー]{3,}", c))
            cands.append((0 if named else 1, -len(c), i, c))
    nums.sort()
    if nums and nums[0][0] == 0 and nums[0][1] not in avoid:
        return nums[0][1]
    if cands:
        return sorted(cands)[0][3]
    return nums[0][1] if nums and nums[0][1] not in avoid else ""


def block_sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[。！？])|(?<=[.!?])\s+(?=[A-Z0-9\"“])", text or "") if len(p.strip()) >= 12]


def _terms(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9.+\-]{2,}", text or "")} | set(re.findall(r"[ァ-ヴー]{3,}", text or ""))


def page_target(pages: list, speech: str, used: set[str]) -> tuple[str, str, str] | None:
    """読み上げの文の証拠になる画面の文: 公式ページで、記事の数字（全部）と名前を含む文。無ければ自分の記事ページで同じ文。
    戻り値 (target, block_id, ja)。ja は英語の文のときの日本語の吹き出し（読み上げの文から。数字は target にあるものだけ）"""
    import shorts_v3 as v3

    sd, st = _digits(speech), _terms(speech)
    body = plain(speech).rstrip("。")
    best = None
    for p in pages:
        for b in p.blocks:
            for sent in block_sentences(b.text):
                key = plain(sent)
                if key in used:
                    continue
                if p.own:
                    if body and body in key:
                        score = 5.0
                    else:
                        continue
                else:
                    if not (20 <= len(sent) <= 240) or (sd and not sd <= _digits(sent)):
                        continue
                    if v3.is_english(sent):
                        shared = sum(1 for t in st if t in sent.lower())
                        if shared < (1 if sd else 3) or not v3.clause_ok(sent, b.text):
                            continue
                        score = 10.0 * len(sd) + 3 * shared
                    else:
                        ov = overlap(sent, speech)
                        if ov < 0.35 or not v3.ja_phrase_ok(sent.rstrip("。"), b.text):
                            continue
                        score = 10.0 * len(sd) + 20 * ov
                if best is None or score > best[0]:
                    best = (score, sent, b.id)
    if not best:
        return None
    _score, sent, bid = best
    target = sent if v3.is_english(sent) else sent.rstrip("。")
    ja = caption_for(speech, sent) if v3.is_english(sent) else ""
    if v3.is_english(sent) and not ja:
        return None
    return target, bid, ja


def caption_for(speech: str, target: str) -> str:
    """英語の文の日本語の吹き出し: 読み上げの文（か、その読点までのまとまり）で 40 字まで。数字は target にあるものだけ"""
    td = _digits(target)
    body = speech.rstrip("。")
    # 吹き出しは言い切りのまとまりだけ（「…環境「Air」で」のような切れ端にしない）
    cands = [body] + [c.strip() for c in body.split("、")]
    for c in cands:
        if 10 <= len(c) <= 40 and _digits(c) <= td and not stiff(c) and re.search(r"(?:ます|ません|です|でした|ました|できる|ない|れる|せる|る|た)$", c):
            return c
    return ""


def hook_parts(article: dict, pages: list) -> dict | None:
    """つかみ: 読み上げ＝何が起きたか・なぜ大事か（本文の最初の文 → リードの文）、見出し＝記事の題の片方（数字なし）、
    証拠＝画面のいちばん大きな見出し（英語なら題を吹き出しに）"""
    import shorts_v3 as v3
    import video_gate as gate

    terms = gate._subject_terms(article)
    cands = []
    # 3秒で気になる入り（2026-11-04 オーナー「ただの読み聞かせ」）: 投稿の1行目の呼びかけ（読者を名指しする。門番 sns_rules のルール6で確かめた文）
    first = str(article.get("threadsPost") or "").splitlines()[:1]
    if first and not re.search(r"【|先に言うと|発表から\d+分|^\s*要約すると|押さえておきたい|気になる人|要チェック", first[0]) and re.search(r"(?:です|ます)。?$", first[0].strip()):
        cands.append(first[0].strip())
    intro = [para for h2, h3, para in body_sections(article.get("body", "")) if not h2 and not h3]
    for para in intro[:1]:
        cands += sentences(para)[:1]
    cands += sentences(article.get("lede", ""))[:2]
    speeches = []
    for c in cands:
        sp = narration_sentence(c, max_len=70)
        if sp and len(sp) >= 20 and not stiff(sp) and not gate.SUMMARY_OPENING.search(sp) if hasattr(gate, "SUMMARY_OPENING") else sp:
            speeches.append(sp)
    if not speeches:
        return None
    parts = [clean_sentence(p) for p in TITLE_SPLIT.split(str(article.get("title") or "")) if p.strip()]
    texts = []
    for p in parts:
        head = p.split("、")[0]
        for cand in (p, head):
            if 10 <= len(cand) <= 40 and not re.search(r"\d", gate.norm(cand)) and not stiff(cand) and not gate.bare_phrase(cand):
                texts.append(cand)
    named = [t for t in texts if any(x.lower() in t.lower() for x in terms)]
    texts = named + [t for t in texts if t not in named]
    if not texts and terms:
        texts = [f"{terms[-1] if len(terms[-1]) <= 20 else terms[0]}の新しい発表"]
    pick = next(((t, sp) for t in texts for sp in speeches if not terms or any(x.lower() in f"{t} {sp}".lower() for x in terms)), None)
    if not pick:
        return None
    text, speech = pick
    # つかみの左の言葉（見出しが消えたあと、次の場面まで出す。読み上げにそのままある言い回し。数字だけ・名詞1つだけにしない）
    payoff = keyword_for(speech, speech, "", set())
    if not payoff or gate.number_only(payoff) or gate.bare_phrase(payoff):
        return None
    ordered = [p for p in pages if not p.own] + [p for p in pages if p.own]
    for p in ordered:
        hero = v3.hero_block(p)
        if not hero:
            continue
        sent = (block_sentences(hero.text) or [hero.text])[0].strip()
        if not (6 <= len(sent) <= 200):
            continue
        if v3.is_english(sent):
            return {"text": text, "payoff": payoff, "speech": speech, "block": hero.id, "target": sent, "ja": text if not _digits(text) - _digits(sent) else ""}
        return {"text": text, "payoff": payoff, "speech": speech, "block": hero.id, "target": sent.rstrip("。"), "ja": ""}
    return None


def fact_candidates(article: dict) -> list[tuple[str, str, str]]:
    """場面の候補 (読み上げの文, 小見出しの元, 出どころ): リード → FAQ → 本文の小見出し → 本文の問いの見出し → 仕事とお金"""
    out: list[tuple[str, str, str]] = []
    parts = [clean_sentence(p) for p in TITLE_SPLIT.split(str(article.get("title") or "")) if p.strip()]
    for h2, h3, para in body_sections(article.get("body", ""))[:1]:  # 本文の最初の文（何が起きたか・なぜ大事か）
        if not h2 and not h3 and (sp := narration_sentence((sentences(para) or [""])[0], max_len=70)):
            out.append((sp, sp, "intro"))
    for s in sentences(article.get("lede", ""))[:3]:
        if (sp := narration_sentence(s, max_len=70)):
            hint = max(parts, key=lambda p: overlap(p, sp), default="")
            out.append((sp, hint, "lede"))
    for f in article.get("faq") or []:
        if not isinstance(f, dict):
            continue
        raw = sentences(f.get("a", ""))
        sp = narration_sentence(raw[0], max_len=70) if raw else ""
        if not sp:
            continue
        nxt = narration_sentence(raw[1], first=False) if len(raw) > 1 else ""
        if len(sp) < 24 and nxt and len(sp + nxt) <= 70:
            sp += nxt
        out.append((sp, f.get("q", ""), "faq"))
    for h2, h3, para in body_sections(article.get("body", "")):
        head = h3 or (h2 if h2.endswith(("？", "?")) else "")
        if not head or re.search(r"概要|自分には|仕事・お金|仕事とお金|まとめ|出典|よくある質問", h2):
            continue
        sents = sentences(para)
        sp = narration_sentence(sents[0], max_len=70) if sents else ""
        if sp and len(sp) < 30 and len(sents) > 1 and (nxt := narration_sentence(sents[1], first=False)) and len(sp + nxt) <= 70:
            sp += nxt
        if sp:
            out.append((sp, head, "section"))
    for p in (article.get("workMoney") or {}).get("points") or []:
        if (sp := narration_sentence(p, max_len=76)):
            m = re.match(r"^(.{4,20}?(?:人|者|方|企業|会社|チーム|部門|担当|事業主|法人))(?:は|が|なら|に)", clean_sentence(p))
            out.append((sp, m.group(1) if m else "", "work"))
    return out


SOFT_PROBLEM = re.compile(r"^beat\d+\.label は4〜14字|^beats は|読み上げの合計が|^hook\.text は|^hook\.speech は|^beat\d+（これは何？）|speech が長すぎ|^conclusion|speech で数字|speech に英単語")


def template_script(article: dict, pages: list) -> tuple[dict | None, list[str]]:
    """承認の形の台本を記事の文だけで作る（LLM なし）。戻り値 (台本, 問題)。台本が作れなければ (None, 理由)"""
    import shorts_v3 as v3
    import video_gate as gate

    hook = hook_parts(article, pages)
    if not hook:
        return None, ["つかみ（何が起きたか・なぜ大事か）の文か、画面の見出しが無い"]
    used_targets = {plain(hook["target"])}
    used_labels: set[str] = set()
    used_kw: set[str] = set()
    beats: list[dict] = []
    total = len(hook["speech"])
    seen = [hook["speech"]]
    for sp, hint, src in fact_candidates(article):
        if len(beats) >= 6 or total >= SPEECH_MAX - 40:
            break
        if stiff(sp) or any(len(bigrams(sp) & bigrams(o)) / max(len(bigrams(sp) | bigrams(o)), 1) > 0.45 for o in seen):
            continue
        if _NO_INFO.search(sp) and sum(1 for b in beats if _NO_INFO.search(b["speech"])) >= 1:
            continue
        got = page_target(pages, sp, used_targets)
        if not got:
            continue
        target, bid, ja = got
        cands = (["あなたの場合"] if src == "work" and "あなたの場合" not in used_labels else []) + label_candidates(hint, sp)
        terms = gate._subject_terms(article)
        if src in ("lede", "intro") and terms:  # 絵コンテの「GitHubの発表」と同じ形（1本に1回）
            cands.append(f"{terms[0]}の発表")
        label = next((c for c in cands if label_ok(c, used_labels, article) and (c.endswith("の発表") or overlap(c, hook["text"]) < 0.6)), "")
        if not label:
            continue
        kw = keyword_for(sp, target, ja, used_kw | {label})
        if not kw:
            continue
        beat = {"label": label, "keyword": kw, "sub": "", "speech": sp, "block": bid, "target": target, "ja": ja}
        if gate.label_problems({"hook": {}, "beats": [beat]}):
            continue
        beats.append(beat)
        used_targets.add(plain(target))
        used_labels.add(gate.norm(re.sub(r"[\s　？?]+", "", label)).lower())
        used_kw.add(kw)
        seen.append(sp)
        total += len(sp)
    if len(beats) < 3:
        return None, [f"画面の証拠と合う記事の文が足りない（{len(beats)}場面）"]
    # 2026-11-04 オーナー「ただの読み聞かせ」: 2つめの場面の前に「で、何が変わるのか。」の切り替え、締めは投稿の問いかけ（まとめで終わらない）
    turn = "で、何が変わるのか。"
    k = next((i for i in range(1, len(beats)) if len(turn + beats[i]["speech"]) <= 70), None)
    if k is not None:
        beats[k]["speech"] = turn + beats[k]["speech"]
    question = next((ln.strip() for ln in str(article.get("threadsPost") or "").splitlines() if re.search(r"[？?]$", ln.strip()) and 8 <= len(ln.strip()) <= 40), "")
    script = {
        "hook": hook, "beats": beats,
        "compare": {"beat": 0, "base": "", "subject": "", "ratio": ""}, "ladder": {"beat": 0, "subject": "", "rows": []},
        "units": {"beat": 0, "unit": "", "subject": "", "rows": []},
        "conclusion": {"text": "あなたはどうしますか", "speech": question} if question else {"text": "くわしくは記事で", "speech": "くわしくは記事で解説しています。"},
        "short_beats": [], "title": clean_sentence(article.get("title", ""))[:60], "summary": summary_of(article), "hashtags": [],
        "_level": "L3A", "_source": "article",
    }  # fmt: skip
    # 台本の検査（LLM の台本と同じ shorts_v3.script_problems）。場面の問題はその場面を外して、もう一度
    for _ in range(4):
        probs = [p for p in v3.script_problems(script, article, pages) if not SOFT_PROBLEM.search(p)]
        probs += [f"硬い言い回し: {p}" for t in texts_of(script) for p in stiff(t)]
        bad = sorted({int(m.group(1)) for p in probs for m in [re.search(r"beat(\d+)", p)] if m}, reverse=True)
        if not bad:
            break
        for i in bad:
            if 1 <= i <= len(script["beats"]):
                script["beats"].pop(i - 1)
        if len(script["beats"]) < 3:
            return None, probs[:6]
    if any(re.search(r"\bhook\b|つかみ|始まり", p) for p in probs):
        return None, probs[:6]
    return v3.latin_names(script), probs


def texts_of(script: dict) -> list[str]:
    """台本の中で、画面に出る・読み上げる文字（硬い言い回しの検査に使う）"""
    hook, concl = script.get("hook") or {}, script.get("conclusion") or {}
    out = [hook.get("text", ""), hook.get("payoff", ""), hook.get("speech", ""), hook.get("ja", ""), concl.get("text", ""), concl.get("speech", "")]
    for b in script.get("beats") or []:
        out += [b.get(k, "") for k in ("label", "keyword", "sub", "speech", "ja")]
    return [x for x in out if x]


# --------------------------------------------------------------------------- #
# 2. ページ（公式ページ＋自分の記事ページ）。テスト・計画の確かめ用に、記事の文から自分の記事ページを模した画面も作れる
# --------------------------------------------------------------------------- #


def capture(article: dict, slug: str) -> list:
    """撮る画面: 出典の公式ページ（撮れなければ同じ会社の公式ページ）と、自分の記事ページ（証拠の文の予備）。1回でまとめて撮る"""
    import shorts_v2 as v2
    import shorts_v3 as v3

    v3.CAPTURE_FAILS.clear()
    own = f"https://{v3.SITE}/articles/{slug}"
    urls = v3.page_urls(article)
    pages = v3.capture_pages(urls + [own])
    source = v2.official_page_url(article)
    if urls and not (source and any(p.url == source for p in pages)):
        extra = v3.fallback_urls(article, urls)
        if extra:
            pages = v3.capture_pages([p.url for p in pages if not p.own] + extra + [own]) or pages
    return pages


def article_page(article: dict, max_blocks: int = 28):
    """記事の文から、自分の記事ページを模した画面（shorts_v3.Page。1文字ずつの枠つき）。テストと --plan-check 用（ネットなし）"""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    import shorts_v3 as v3

    k = v3.CAPTURE_DPR
    w = v3.CAPTURE_W * k
    font_path = str(ROOT / "assets" / "fonts" / "NotoSansJP-Bold.otf")
    rows: list[tuple[str, int]] = [(clean_sentence(article.get("title", "")), 30), (clean_sentence(article.get("lede", "")), 16)]
    for h2, h3, para in body_sections(article.get("body", "")):
        if h3 or h2:
            rows.append((clean_sentence(h3 or h2), 20))
        rows.append((clean_sentence(para), 15))
    for f in article.get("faq") or []:
        rows += [(clean_sentence(f.get("q", "")), 18), (clean_sentence(f.get("a", "")), 15)]
    rows += [(clean_sentence(p), 15) for p in (article.get("workMoney") or {}).get("points") or []]
    rows = [r for r in rows if r[0]][:max_blocks]
    layout, y = [], 120
    for text, size in rows:
        f = ImageFont.truetype(font_path, size * k)
        lines, cur = [], ""
        for ch in text:
            if f.getlength(cur + ch) > w - 80 * k:
                lines.append(cur)
                cur = ""
            cur += ch
        if cur:
            lines.append(cur)
        layout.append((text, size, y, lines, f))
        y += int(size * 1.6 * len(lines)) + 28
    img = Image.new("RGB", (w, (y + 80) * k), (252, 252, 250))
    d = ImageDraw.Draw(img)
    blocks = []
    for i, (text, size, y0, lines, f) in enumerate(layout, 1):
        chars = []
        for li, line in enumerate(lines):
            yy = (y0 + li * size * 1.6) * k
            d.text((40 * k, yy), line, font=f, fill=(20, 20, 24))
            for j, ch in enumerate(line):
                x0, x1 = 40 * k + f.getlength(line[:j]), 40 * k + f.getlength(line[: j + 1])
                chars.append((ch, (x0, yy, x1, yy + size * 1.3 * k) if ch.strip() else None))
        rect = (40 * k, y0 * k, w - 40 * k, (y0 + size * 1.6 * len(lines)) * k)
        blocks.append(v3.Block(f"1-{i}", text, rect, chars, float(size)))
    return v3.Page(f"https://ai-tech-express.jp/articles/{article.get('slug', '')}", "ai-tech-express.jp", np.asarray(img), blocks, article.get("title", ""), own=True)


# --------------------------------------------------------------------------- #
# 3. L3A: 承認の形を記事の文だけで（描画・ゲート・レビューは shorts_v3 と同じ）
# --------------------------------------------------------------------------- #


def run_template(slug: str, out_dir: Path, need: list[str], article: dict, tts: str = "voicevox", profile: str | None = None,
                 pages: list | None = None, **kw) -> dict:
    """L3A の2本（必要な版だけ）を作り、shorts_v3.qa_versions で位置の検査と画像のレビュー（API → サブスクリプションの CLI）にかける。
    レビューができなければ不合格。戻り値 {版: report}"""
    import shorts_v3 as v3

    out_dir.mkdir(parents=True, exist_ok=True)
    pages = pages if pages is not None else capture(article, slug)
    if not pages:
        why = "撮れるページが無い（" + "・".join(v3.CAPTURE_FAILS[:3]) + "）"
        return {v: {"slug": slug, "errors": [why], "gate": {"ok": False, "reasons": [why]}, "failReason": why, "level": "L3A"} for v in need}
    script, probs = template_script(article, pages)
    if not script:
        why = "記事の文だけの台本を作れない: " + " / ".join(probs[:3])
        return {v: {"slug": slug, "errors": [why], "gate": {"ok": False, "reasons": [why]}, "failReason": why, "level": "L3A"} for v in need}
    (out_dir / f"{slug}.L3A.script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), "utf-8")
    fast = {k: kw[k] for k in ("fps", "out_scale", "workers", "sheet") if k in kw}
    out = {}
    for v in need:
        s = copy.deepcopy(script) if v == "full" else v3.short_script(copy.deepcopy(script))
        out[v] = v3.make_video(slug, out_dir, tts, profile=profile, article=article, pages=pages, script=s, **fast)
    out = v3.qa_versions(slug, out, None)
    for r in out.values():
        r["level"] = "L3A"
        r["script_source"] = "article"
    return out


# --------------------------------------------------------------------------- #
# 4. 合格の基準（どの段も同じ）と L1/L2
# --------------------------------------------------------------------------- #


def qa_ok(report: dict, env: dict | None = None) -> bool:
    """sns_video_followup.qa_ok と同じ基準: 承認の形（BASE_LAYOUT）で、位置の検査（geometry）と画像のレビュー（vision）の両方に合格。
    レビューの代わりに決まりだけで通したもの・承認の形でないものは不合格。SNS_VIDEO_REQUIRE_QA=off で止める（試験用）"""
    env = os.environ if env is None else env
    if env.get("SNS_VIDEO_REQUIRE_QA", "on") == "off":
        return True
    rep = report or {}
    qa = (rep.get("gate") or {}).get("qa") or {}
    if str(qa.get("vision_how") or qa.get("how") or "").startswith("deterministic"):
        return False
    if rep.get("base_layout") and rep.get("base_layout") != BASE_LAYOUT:
        return False
    return bool(qa.get("geometry")) and qa.get("vision") is True


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

# 5. 段を順に試す
# --------------------------------------------------------------------------- #


def _out_name(slug: str, variant: str) -> str:
    return f"{slug}__v3" + ("short" if variant == "short" else "")


def l1_skip_l2(reports: dict | None) -> str:
    """L1 の失敗が「もう一度やっても同じ」もの（撮れるページが無い）なら、L2 を飛ばす理由"""
    why = " ".join(str((r or {}).get("failReason") or " ".join(((r or {}).get("gate") or {}).get("reasons") or [])) for r in (reports or {}).values())
    if re.search(r"撮れるページが無い|撮ってよい公式ページがありません", why):
        return "撮れる公式ページが無い（もう一度やっても同じ）"
    return ""


def make_versions(slug: str, out_dir: Path, deadline_s: float = DEADLINE_S, tts: str = "voicevox", profile: str | None = None,
                  levels: tuple | list = LEVELS, runners: dict | None = None, clock=time.monotonic, isolate: bool = True,
                  article: dict | None = None, **kw) -> dict:
    """段を L1 → L2 → L3A の順に試し、版ごとに最初に合格した段の動画を out_dir/<slug>__v3.mp4（full）・__v3short.mp4（short）に置く。
    戻り値 {"full": report, "short": report}（shorts_v3.make_versions と同じ形。report["level"]・report["ladder"]・report["gate"]["qa"]）。
    どの段も別のプロセスで動かし、deadline_s で止める。runners: {"L1": fn(...), ...}（テスト用。fn(slug, level_dir, need, article, prev, remaining) → {版: report}）"""
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
    for level in levels:
        need = [v for v in VARIANTS if v not in chosen]
        if not need:
            break
        remaining = deadline_s - (clock() - t0)
        level_dir = root / level
        level_dir.mkdir(parents=True, exist_ok=True)
        skip = l1_skip_l2(prev) if level == "L2" and prev is not None else ""
        reserve = L3A_RESERVE_S if level != "L3A" and "L3A" in levels else 0.0
        if not skip and remaining - reserve < MIN_SECONDS.get(level, 0.0):
            skip = f"時間が足りない（残り {remaining:.0f}秒）"
        if not skip and remaining <= 5:
            skip = "時間切れ"
        if skip:
            ladder.append({"level": level, "ok": False, "skipped": True, "reasons": [skip], "seconds": 0.0, "variants": {}})
            log(f"[ladder] {slug} {level}: 飛ばす（{skip}）")
            continue
        st = clock()
        log(f"[ladder] {slug} {level}: 開始（版 {need}・残り {remaining:.0f}秒）")
        reports, err = run_level(level, slug, level_dir, need, a, prev, remaining - reserve, runners, isolate, tts, profile, **kw)
        seconds = round(clock() - st, 1)
        got = {}
        for v in need:
            rep = (reports or {}).get(v)
            mp4 = level_dir / f"{_out_name(slug, v)}.mp4"
            got[v] = passes(rep, mp4)
            if rep:
                last[v] = rep
            if got[v]:
                chosen[v] = (level, rep, mp4)
        reasons = [err] if err else []
        for v in need:
            if not got.get(v):
                rs = ((last.get(v) or {}).get("gate") or {}).get("reasons") or []
                if (last.get(v) or {}).get("gate", {}).get("ok") and not qa_ok(last.get(v)):
                    rs = ["描いた位置・画像のレビューの検査（qa）が無い・不合格・承認の形でない"] + list(rs)
                reasons += [f"{v}: {x}" for x in rs[:3]]
        entry = {"level": level, "ok": all(got.get(v) for v in need), "reasons": reasons[:8], "seconds": seconds, "variants": got}
        ladder.append(entry)
        log(f"[ladder] {slug} {level}: {'合格' if entry['ok'] else '不合格'} {seconds}秒 {got} {reasons[:2]}")
        if level in ("L1", "L2"):
            prev = reports or prev
    out = {}
    total_s = round(clock() - t0, 1)
    for v in VARIANTS:
        if v in chosen:
            level, rep, mp4 = chosen[v]
            shutil.copyfile(mp4, out_dir / f"{_out_name(slug, v)}.mp4")
            sheet = mp4.with_suffix(".sheet.png")
            if sheet.exists():
                shutil.copyfile(sheet, out_dir / f"{_out_name(slug, v)}.sheet.png")
            rep = dict(rep)
            rep["level"] = level
            rep["mp4"] = str(out_dir / f"{_out_name(slug, v)}.mp4")
        else:
            rep = dict(last.get(v) or {"slug": slug, "errors": ["どの段でも作れなかった"], "gate": {"ok": False, "reasons": ["どの段でも作れなかった"]}})
            rep["level"] = None
            rep.setdefault("gate", {}).setdefault("qa", {"geometry": False, "vision": False, "version": QA_VERSION})
            rep["gate"]["ok"] = False
            rep["failReason"] = rep.get("failReason") or "どの段でも合格しなかった（あとの回で作り直す）: " + " / ".join(" ".join(e.get("reasons", [])[:1]) for e in ladder)[:300]
        rep["ladder"] = ladder
        rep["timings"] = {**(rep.get("timings") or {}), "ladder_total": total_s}
        (out_dir / f"{_out_name(slug, v)}.report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), "utf-8")
        out[v] = rep
    log(f"[ladder] {slug}: full={out['full'].get('level')} short={out['short'].get('level')} {total_s}秒")
    return out


def run_level(level: str, slug: str, level_dir: Path, need: list[str], a: dict, prev: dict | None, budget: float, runners: dict | None,
              isolate: bool, tts: str, profile: str | None, **kw) -> tuple[dict | None, str]:
    """1つの段を動かす。戻り値 ({版: report}, 失敗の理由)"""
    if runners and level in runners:
        try:
            return runners[level](slug=slug, level_dir=level_dir, need=need, article=a, prev=prev, remaining=budget), ""
        except Exception as error:  # noqa: BLE001
            return None, f"{type(error).__name__}: {error}"
    if level in ("L1", "L2"):
        fn, args = run_v3, (slug, level_dir)
        fn_kw = {"tts": tts, "profile": profile, "feedback": feedback_note(prev) if level == "L2" else "", **{k: v for k, v in kw.items() if k in ("extra_pages", "template")}}
    elif level == "L3A":
        fn, args = run_template, (slug, level_dir, need, a)
        fn_kw = {"tts": tts, "profile": profile, **{k: v for k, v in kw.items() if k in ("fps", "out_scale", "workers", "sheet", "pages")}}
    else:
        return None, f"知らない段 {level}"
    if isolate:
        return isolated(fn, budget, *args, **fn_kw)
    try:
        return fn(*args, **fn_kw), ""
    except Exception as error:  # noqa: BLE001
        return None, f"{type(error).__name__}: {error}"


# --------------------------------------------------------------------------- #
# 6. 段の内訳・承認の形の割合と知らせ（9割未満なら issue）
# --------------------------------------------------------------------------- #

_ORDER = {"L1": 1, "L2": 2, "L3A": 3, "L3": 4, "L4": 5}
USED = ("ready", "posted", "attached", "article_only", "attach_failed", "uploaded", "dry_run")


def level_of(rec: dict) -> str | None:
    """記録（state/sns_video.json の videos の1件）の段: rec["level"]、無ければ合格した版の段のうち一番下"""
    if rec.get("level") in _ORDER:
        return rec["level"]
    levels = [v.get("level") for v in (rec.get("versions") or {}).values() if isinstance(v, dict) and v.get("ok") and v.get("level") in _ORDER]
    return max(levels, key=_ORDER.get) if levels else None


def _rows(state: dict, last: int) -> list[tuple[str, str, dict]]:
    rows = []
    for slug, rec in (state.get("videos") or {}).items():
        if not isinstance(rec, dict) or rec.get("status") == "started":
            continue
        if "level" not in rec and not any("level" in (v or {}) for v in (rec.get("versions") or {}).values()):
            continue  # 段の仕組みより前の記録は数えない
        rows.append((rec.get("at", ""), slug, rec))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows[:last]


def level_stats(state: dict, last: int = FALLBACK_WINDOW) -> dict:
    """直近 last 本の段の内訳（作り終えた記録・新しい順）"""
    rows = _rows(state, last)
    by: dict[str, int] = {}
    for _, _, rec in rows:
        lv = level_of(rec) or "none"
        by[lv] = by.get(lv, 0) + 1
    return {"n": len(rows), "by_level": by, "slugs": [(s, level_of(r) or "none") for _, s, r in rows]}


def approved(rec: dict) -> bool:
    """承認の形（L1/L2/L3A・BASE_LAYOUT）で作り、合格した動画か"""
    vers = [v for v in (rec.get("versions") or {}).values() if isinstance(v, dict) and v.get("ok")]
    if not vers:
        return False
    layouts = {v.get("base_layout") for v in vers if v.get("base_layout")}
    return level_of(rec) in APPROVED_LEVELS and (not layouts or layouts == {BASE_LAYOUT})


def approved_share(state: dict, last: int = FALLBACK_WINDOW) -> dict:
    """直近 last 本の、作れた動画（合格した版がある記録）のうち承認の形で作った割合（2026-11-04 オーナー承認の目安: 9割以上）"""
    rows = [(s, r) for _, s, r in _rows(state, 10_000) if r.get("ok") or r.get("status") in USED][:last]
    good = [s for s, r in rows if approved(r)]
    n = len(rows)
    return {"n": n, "approved": len(good), "share": round(len(good) / n, 3) if n else None, "min": APPROVED_MIN,
            "not_approved": [(s, level_of(r) or "none") for s, r in rows if s not in good]}  # fmt: skip


def fallback_alert(state: dict, env: dict | None = None, run=subprocess.run) -> str:
    """承認の形で作った動画が直近20本の9割未満なら issue（ラベル sns-video-fallback）を作るか、開いている issue に追記する。GitHub Actions の中だけ"""
    env = os.environ if env is None else env
    st = approved_share(state)
    if st["n"] < FALLBACK_MIN or (st["share"] or 0) >= APPROVED_MIN:
        return "ok"
    if env.get("GITHUB_ACTIONS") != "true":
        return "skip-local"
    body = (f"直近 {st['n']} 本の SNS 動画のうち、承認の形（左の見出し＋右の公式ページの画面。L1/L2/L3A）で作った動画は {st['approved']} 本"
            f"（{st['share']:.0%}。目安 {APPROVED_MIN:.0%} 以上）。\n\n"
            + "\n".join(f"- `{s}`: {lv}" for s, lv in st["not_approved"])
            + "\n\n落ちた理由は `state/sns_video.json` の versions[].reasons・ladder を確かめてください。")  # fmt: skip
    try:
        run(["gh", "label", "create", FALLBACK_LABEL, "--color", "FBCA04", "--description", "SNS 動画が承認の形で作れていない", "--force"],
            check=False, capture_output=True, text=True, timeout=60)  # fmt: skip
        found = run(["gh", "issue", "list", "--label", FALLBACK_LABEL, "--state", "open", "--json", "number", "--limit", "1"], check=False, capture_output=True, text=True, timeout=60)
        rows = json.loads(found.stdout or "[]") if getattr(found, "stdout", "") else []
        if rows:
            run(["gh", "issue", "comment", str(rows[0]["number"]), "--body", body], check=False, capture_output=True, text=True, timeout=60)
            return f"commented #{rows[0]['number']}"
        run(["gh", "issue", "create", "--title", f"SNS 動画が承認の形で作れていない（{st['share']:.0%}）", "--label", FALLBACK_LABEL, "--body", body],
            check=False, capture_output=True, text=True, timeout=60)  # fmt: skip
        return "created"
    except Exception as error:  # noqa: BLE001
        log(f"[ladder] issue を作れませんでした: {type(error).__name__}")
        return "error"


# --------------------------------------------------------------------------- #
# 7. 直近の記事での確かめ（投稿しない）
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




def plan_check(slugs: list[str]) -> list[dict]:
    """L3A の台本を、自分の記事ページを模した画面（article_page）で作って検査するだけ（ネット・声・描画なし）"""
    import shorts

    out = []
    for slug in slugs:
        a = shorts.load_article(slug)
        script, probs = template_script(a, [article_page(a)])
        row = {"slug": slug, "ok": bool(script), "problems": probs[:3]}
        if script:
            row.update({"hook": script["hook"]["text"], "speech": script["hook"]["speech"], "beats": [(b["label"], b["keyword"]) for b in script["beats"]],
                        "chars": len(script["hook"]["speech"]) + sum(len(b["speech"]) for b in script["beats"])})
        out.append(row)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="SNS 動画の段（L1→L2→L3A。どれも承認の形・同じ合格の基準）。投稿しない・置かない")
    parser.add_argument("slugs", nargs="*")
    parser.add_argument("--out", default=str(ROOT / "out" / "video_ladder"))
    parser.add_argument("--tts", default=os.environ.get("SHORTS_TTS", "voicevox"), choices=["voicevox", "fake", "edge", "auto"])
    parser.add_argument("--levels", default=",".join(LEVELS))
    parser.add_argument("--deadline", type=float, default=DEADLINE_S)
    parser.add_argument("--profile")
    parser.add_argument("--plan-check", type=int, metavar="N", help="直近の N 本で L3A の台本の検査だけ")
    parser.add_argument("--dry-run", type=int, metavar="N", help="直近の N 本を書き出して段ごとの合格率（投稿しない）")
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
        print(json.dumps({"levels": level_stats(state), "approved": approved_share(state)}, ensure_ascii=False, indent=2))
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
        print(f"L3A の台本が作れた記事: {sum(r['ok'] for r in rows)}/{len(rows)}")
        return
    levels = tuple(x.strip() for x in args.levels.split(",") if x.strip())
    slugs = recent_slugs(args.dry_run) if args.dry_run else args.slugs
    if not slugs:
        parser.error("slug か --dry-run N を指定してください")
    summary = []
    for slug in slugs:
        out = make_versions(slug, Path(args.out), deadline_s=args.deadline, tts=args.tts, profile=args.profile, levels=levels)
        row = {"slug": slug, **{v: out[v].get("level") for v in VARIANTS},
               "qa": {v: (out[v].get("gate") or {}).get("qa") for v in VARIANTS},
               "ladder": [{k: e.get(k) for k in ("level", "ok", "skipped", "seconds", "variants", "reasons")} for e in out["full"]["ladder"]]}  # fmt: skip
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False))
    tried: dict[str, list[int]] = {}
    for row in summary:
        for e in row["ladder"]:
            if e.get("skipped"):
                continue
            t = tried.setdefault(e["level"], [0, 0])
            for ok in (e.get("variants") or {}).values():
                t[1] += 1
                t[0] += 1 if ok else 0
    result = {"articles": len(summary), "pass_rate_by_level": {k: f"{a}/{b}" for k, (a, b) in tried.items()},
              "all_produced": all(row["full"] and row["short"] for row in summary)}  # fmt: skip
    print(json.dumps(result, ensure_ascii=False, indent=2))
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "dryrun.json").write_text(json.dumps({"summary": result, "rows": summary}, ensure_ascii=False, indent=2), "utf-8")
    sys.exit(0 if result["all_produced"] else 1)


if __name__ == "__main__":
    main()
