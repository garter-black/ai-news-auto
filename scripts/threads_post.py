#!/usr/bin/env python3
"""新着記事を Threads に自動投稿する。

記事の見出しと「3秒でわかる要点」から投稿文を組み立てるので、Gemini は呼ばない。
投稿の経路は2つあり、設定されている方を使う（両方あれば Buffer を優先）。

  1. Buffer 経由（BUFFER_API_KEY）
     Buffer に Threads をつないでおけば、Meta の開発者登録や電話番号の認証が要らない。
     Buffer の API は無料プランでも使える。
  2. Threads 公式 API 経由（scripts/threads_auth.py で取得したトークン）

  python3 scripts/threads_post.py --dry-run        # 投稿文を表示するだけ
  python3 scripts/threads_post.py --check-buffer   # Buffer の接続先を確認するだけ（投稿しない）
  python3 scripts/threads_post.py                  # THREADS_MODE=live のときだけ実際に投稿

THREADS_MODE が live 以外（未設定を含む）なら下書きモード。
Actions ではジョブサマリーに投稿文を書き出すだけで、何も公開しない。
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from threads_token import API_BASE, load_token, refresh_if_needed  # noqa: E402
import threads_voice as voice  # noqa: E402
import persona  # noqa: E402
import threads_hooks  # noqa: E402
import question_patterns  # noqa: E402
import sns_rules  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
STATE_PATH = ROOT / "state" / "threads_posted.json"

# 公式上限は500文字。絵文字などを2文字と数える場合に備えて余裕を持たせる
MAX_CHARS = 480
# コンテナ作成から公開まで平均30秒待つよう公式ドキュメントが推奨している
PUBLISH_WAIT_SEC = 30
BUFFER_API = "https://api.buffer.com"


def log(message: str) -> None:
    print(message, flush=True)


def site_url() -> str:
    url = os.environ.get("NEXT_PUBLIC_SITE_URL") or "https://ai-tech-express.jp"
    return url.rstrip("/")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text("utf-8"))
    return {"posted": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# 記事へ誘導するだけの行（完結型の投稿では使わない）と、絵文字
TEASER_RE = re.compile(r"記事で|記事はこちら|続きは|詳しくは|リンク(?:から|で|先)|👇")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")
# 煽り語の行と、書き出しの型の名前だけの行（生成時の書き損じ）も落とす
CLICKBAIT_RE = re.compile(r"衝撃|ヤバ[いすく]|必見|知らないと損|見逃し厳禁")
HOOK_NAME_RE = re.compile(r"^[^\s。、]{1,10}型$")
JAPAN_LINES = {
    "ok": "日本からもう使えます。",
    "partial": "日本からも使えます（英語のみ・一部機能など条件あり）。",
    "no": "今のところ日本では使えません。",
}
STATUS_LINES = {
    "limited": "今は一部の人だけが使えます。",
    "soon": "提供はこれからです。",
    "demo": "研究段階で、まだ一般には使えません。",
}


def clean_lines(text: str) -> list[str]:
    """紹介文を行に分け、記事へ誘導するだけの行と絵文字を落とす"""
    lines = []
    for line in str(text or "").splitlines():
        line = EMOJI_RE.sub("", line).strip()
        if line and not TEASER_RE.search(line) and not CLICKBAIT_RE.search(line) and not HOOK_NAME_RE.match(line):
            lines.append(line)
    return lines


def relevance_line(article: dict) -> str:
    """「自分に関係ある？」の1行（判定から。事実として言えることだけ）"""
    verdict = article.get("verdict") or {}
    status, japan = verdict.get("status"), verdict.get("japan")
    price = str(verdict.get("price") or "").strip()
    line = ""
    if status == "available":
        line = JAPAN_LINES.get(japan, "すでに提供が始まっています。")
    elif status in STATUS_LINES:
        line = STATUS_LINES[status]
    if verdict.get("free") is True and status == "available":
        line += "無料で試せます。"
    elif re.search(r"\d", price):  # 「月20ドル〜」のように具体的な料金だけ（「API従量課金」は伝わりにくい）
        line += f"料金は{price}。"
    audience = str(verdict.get("audience") or "").strip()
    if audience and len(line) < 24:
        line += f"主に{audience}向け。"
    return line or str(verdict.get("take") or "").strip()


def work_line(article: dict, limit: int = 60) -> str:
    """「仕事・お金にどう活かせる？」の1つ目（記事の workMoney。事実確認を通ったものだけ）。関係が小さい記事・長すぎる文は使わない"""
    work_money = article.get("workMoney") or {}
    if work_money.get("relevance") == "low":
        return ""
    point = next((str(p).strip() for p in work_money.get("points") or [] if str(p).strip()), "")
    if not point or len(point) > limit or persona.ADVICE_RE.search(point):
        return ""
    return point


def key_fact(article: dict) -> str:
    """要点のうち、数字の入ったものを優先（数字は記事のまま）"""
    points = [EMOJI_RE.sub("", h).strip() for h in article.get("highlights", []) if str(h).strip()]
    return next((p for p in points if re.search(r"\d", p)), points[0] if points else "")


# ---- 本文の形（2026-11-02 オーナー承認）----
# 画像（16:9 の写真＋見出し）に見出しが入るので、本文で見出しを繰り返さない。空行で区切った3つのかたまり:
#   1. 短い書き出し（書き出しの型のローテーションはそのまま）
#   2. 「要約すると、」＋事実の短い文2つ（1文に1つのこと）
#   3. 読者への問いかけ（自分の仕事・お金・副業・道具について、一言で答えられるもの。「？」で終える）
# 全体で全角120〜150字くらい。記事へのリンクは返信（2/2）に置く。
POST_TARGET_CHARS = 150
POST_MAX_CHARS = 175
HOOK_MAX = 34
# 生成時に記事ごとに書いた紹介文の1行目は、少し長くても書き出しに使う（判定から作る定型の書き出しより記事に合う）
OLD_HOOK_MAX = 40
SENTENCE_MAX = 62
# 要約の1文の目安（これより長い文は、組み立て直すときに選ばれにくくする）
SENTENCE_SHORT = 45
SUMMARY_MAX = 125
QUESTION_MAX = 48
QUESTION_END = ("？", "?")
# 問いかけで使わない呼びかけ（反応のお願い・アンケートのふり）
BAIT_RE = re.compile(r"コメント|いいね|フォロー|リポスト|拡散|シェアして|アンケート|投票")
# 要約に使わない文: 記事の紹介（「〜を解説します」）、読者への指示・推測、出典の前置き
META_RE = re.compile(r"解説|まとめて|まとめた|まとめられ|紹介し|分かりやすく|わかりやすく|記事|お伝えします")
NUDGE_RE = re.compile(
    r"(?:チェック|確認)して(?:みて|おいて)?ください|チェックしてほしい|要チェック|急務|してみては|おすすめ|しておきたい|が分かります|"
    r"(?:そうです|ようです|でしょう|かもしれません|と考えられます|とみられます|見込みです)。$"
)
SOURCE_LEAD_RE = re.compile(r"^[^。、「」]{2,40}?によると、")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[。！？!?])", str(text or "")) if s.strip()]


def post_blocks_of(text: str) -> list[str]:
    """空行で区切ったかたまり（各かたまりの中の空白行は落とす）"""
    return [b.strip() for b in re.split(r"\n\s*\n", str(text or "").strip()) if b.strip()]


def repeats_title(line: str, title: str) -> bool:
    """見出しをほぼそのまま書いた行か（画像に見出しが入るので、本文では繰り返さない）"""
    if not line or not title:
        return False
    return title in line or overlap(line, title) >= max(16, int(len(title) * 0.7))


def post_problems(text: str, article: dict | None = None) -> list[str]:
    """本文が新しい形になっているかの機械チェック（API 不要）。問題がなければ空"""
    article = article or {}
    problems: list[str] = []
    blocks = post_blocks_of(text)
    if len(blocks) != 3:
        problems.append(f"かたまりが3つでない（{len(blocks)}個）")
    if len(blocks) < 2 or not blocks[1].startswith(f"{persona.SUMMARY_OPENER}、"):
        problems.append(f"2つ目のかたまりが「{persona.SUMMARY_OPENER}、」で始まっていない")
    if not str(text).rstrip().endswith(QUESTION_END):
        problems.append("最後が問いかけ（？）で終わっていない")
    if m := persona.STIFF_RE.search(text):
        problems.append(f"使わない言い回し（「{m.group(0)}」）")
    if m := persona.ADVICE_RE.search(text):
        problems.append(f"儲け話・投資助言の表現（「{m.group(0)}」）")
    if blocks and (m := BAIT_RE.search(blocks[-1])):
        problems.append(f"反応のお願い・アンケートのふり（「{m.group(0)}」）")
    if re.search(r"https?://|#\S", text):
        problems.append("URL かハッシュタグが入っている")
    if len(text) > POST_MAX_CHARS:
        problems.append(f"長すぎる（{len(text)}字 / {POST_MAX_CHARS}字まで）")
    title = str(article.get("title") or "").strip()
    if title and any(repeats_title(line, title) for line in str(text).splitlines()):
        problems.append("見出しをそのまま繰り返している")
    return problems


def clean_sentence(sentence: str) -> str:
    s = EMOJI_RE.sub("", str(sentence or "")).strip()
    s = re.sub(r"^【速報】\s*", "", s)
    s = re.sub(r"^(?:結論[：:]|先に言うと、)\s*", "", s)
    s = re.sub(r"^(?:さらに|また|加えて|そのうえ)、?", "", s)  # 前の文に続ける言葉は、文を選び直すと浮くので落とす
    s = SOURCE_LEAD_RE.sub("", s).strip()
    if s and not s.endswith(("。", "！", "？", "!", "?")):
        s += "。"
    return s


# ---- 要約の文を、やさしい「です・ます」にする（2026-11-03 オーナー指示。sns_rules のルール7） ----
# 記事の要点は「〜する。」「〜である。」の硬い言い切りが多いので、そのまま SNS に出さず、文の終わりを直す。
# 直せない文（「〜へ。」など）は使わない。主語（だれが）が無い文には、記事の主役（官庁・会社）を足す
_GODAN_I = {"う": "い", "く": "き", "ぐ": "ぎ", "す": "し", "つ": "ち", "ぬ": "に", "ぶ": "び", "む": "み", "る": "り"}
_GODAN_NEG = {"わ": "い", "か": "き", "が": "ぎ", "さ": "し", "た": "ち", "な": "に", "ば": "び", "ま": "み", "ら": "り"}
_E_I_ROW = set("えけげせぜてでねへべぺめれいきぎしじちぢにひびぴみり")
_GODAN_RU = ("入る", "帰る", "走る", "知る", "限る", "切る", "要る", "減る", "握る", "参る", "焦る", "蹴る", "照る", "散る", "茂る")
_PAST_TTA = {"なった": "なりました", "あった": "ありました", "行った": "行いました", "言った": "言いました", "持った": "持ちました",
             "作った": "作りました", "使った": "使いました", "買った": "買いました", "立った": "立ちました", "上回った": "上回りました",
             "下回った": "下回りました", "伴った": "伴いました", "従った": "従いました", "至った": "至りました", "競った": "競いました"}
_PAST_NDA = {"選んだ": "選びました", "呼んだ": "呼びました", "結んだ": "結びました", "並んだ": "並びました", "進んだ": "進みました",
             "読んだ": "読みました", "組んだ": "組みました", "含んだ": "含みました", "望んだ": "望みました", "頼んだ": "頼みました", "及んだ": "及びました"}
_NOUN_END = re.compile(r"[一-龥ァ-ヶーA-Za-z0-9０-９%％」』）)]$")
_SURU_NOUN_END = re.compile(r"[をにがへ][一-龥ァ-ヶー]{2,}$")
_MEDIA_NAME = re.compile(r"ニュース|新聞|TIMES|Times|Watch|ITmedia|Impress|マイナビ|日経|ブログ|Blog|blog|通信|プレス|PR")


# 硬い言葉 → やさしい言葉（sns_rules.HARD_WORDS と対で使う。上から順に置き換える）
EASY_WORDS = [
    ("の普及・啓発活動", "を知ってもらう取り組み"), ("の普及啓発活動", "を知ってもらう取り組み"), ("の普及・啓発", "の呼びかけ"),
    ("普及・啓発活動", "知ってもらう取り組み"), ("普及啓発活動", "知ってもらう取り組み"), ("普及・啓発", "呼びかけ"), ("普及啓発", "呼びかけ"),
    ("啓発活動", "呼びかけ"), ("啓発", "呼びかけ"), ("と協働して", "と一緒に"), ("と協働し", "と一緒に"), ("協働", "協力"),
    ("の周知", "のお知らせ"), ("周知", "お知らせ"), ("を図ります", "を目指します"), ("を図る", "を目指す"), ("を図り", "を目指し"),
    ("に資する", "に役立つ"), ("に鑑み", "をふまえ"), ("に係る", "に関する"), ("所要の", "必要な"), ("講じます", "取ります"), ("講じる", "取る"),
    ("推進します", "進めます"), ("推進する", "進める"), ("展開します", "進めます"), ("展開する", "進める"),
    ("実施します", "行います"), ("実施する", "行う"), ("等の", "などの"), ("等を", "などを"), ("等で", "などで"),
]


def easy_words(sentence: str) -> str:
    out = str(sentence or "")
    for hard, easy in EASY_WORDS:
        out = out.replace(hard, easy)
    return out


# 「〜を発表」「〜に参入」のように「しました」を付けてよい名詞（2026-11-03「実行が可能しました」の事故のあと、知っている名詞だけにした）。
# な形容詞・ふつうの名詞（可能・必要・最新など。sns_rules.NA_NOUNS）は「です」。どちらか分からない名詞は直さない（その文は使わない）
SURU_NOUNS = (
    "発表", "公開", "開始", "提供", "発売", "導入", "参入", "買収", "採用", "追加", "開発", "搭載", "対応", "更新", "終了", "拡大", "強化",
    "投入", "発行", "設立", "締結", "合意", "決定", "承認", "発見", "確認", "改定", "値上げ", "値下げ", "撤退", "統合", "刷新", "延期",
    "中止", "予告", "計画", "表明", "支援", "連携", "提携", "出資", "調達", "達成", "記録", "突破", "獲得", "受賞", "選出", "就任", "退任",
    "推奨", "警告", "改善", "修正", "廃止", "変更", "移行", "拡張", "開催", "開設", "稼働", "販売", "出荷", "配信", "配布", "公表", "報告",
    "発生", "増加", "減少", "上昇", "下落", "実施", "展開", "推進", "検討", "解説", "紹介", "披露", "公開予定", "リリース", "アップデート",
    "スタート", "ローンチ", "オープン", "サポート", "対策", "協力", "参加", "契約", "申請", "要求", "要請", "発令", "決議", "可決", "成立",
)


def polite_sentence(sentence: str) -> str:
    """文の終わりを「です・ます」にし、硬い言葉をやさしく言い換える。直せなければ空。
    直した文が壊れた日本語（sns_rules.grammar_problems。「可能しました」など）なら使わない（空）"""
    out = _polite_sentence(sentence)
    return "" if out and sns_rules.grammar_problems(out) else out


def _polite_sentence(sentence: str) -> str:
    s = easy_words(str(sentence or "").strip())
    if not s:
        return ""
    if sns_rules.POLITE_END.search(s):
        return s
    body = re.sub(r"[。！!]+$", "", s)
    rules = [
        (r"ではない$", "ではありません"), (r"(?:が|は|も)ない$", lambda m: m.group(0)[0] + "ありません"),
        (r"ていない$", "ていません"), (r"できない$", "できません"),
        (r"であった$", "でした"), (r"だった$", "でした"), (r"である$", "です"), (r"(?<=[^ん])だ$", "です"),
        (r"ていた$", "ていました"), (r"ている$", "ています"), (r"てある$", "てあります"), (r"ておく$", "ておきます"),
        (r"した$", "しました"), (r"できた$", "できました"), (r"する$", "します"), (r"できる$", "できます"),
        (r"なる$", "なります"), (r"ある$", "あります"), (r"(?<=[てで])いる$", "います"),
    ]
    for pattern, repl in rules:
        if re.search(pattern, body):
            return re.sub(pattern, repl, body) + "。"
    for table in (_PAST_TTA, _PAST_NDA):
        for plain, pol in table.items():
            if body.endswith(plain):
                return body[: -len(plain)] + pol + "。"
    if body.endswith("いた") and len(body) >= 3 and re.match(r"[一-龥]", body[-3]):
        return body[:-2] + "きました。"  # 書いた・開いた・届いた（く の過去）
    if body.endswith("いだ") and len(body) >= 3 and re.match(r"[一-龥]", body[-3]):
        return body[:-2] + "ぎました。"
    if body.endswith("た") and len(body) >= 2 and body[-2] in _E_I_ROW:
        return body[:-1] + "ました。"  # 始めた・見た（一段の過去）
    if body.endswith("ない") and len(body) >= 3:
        prev = body[-3]
        if prev in _E_I_ROW:
            return body[:-2] + "ません。"
        if prev in _GODAN_NEG:
            return body[:-3] + _GODAN_NEG[prev] + "ません。"
        return body + "です。"  # 少ない など（形容詞）
    if body.endswith("い") and len(body) >= 2 and not body.endswith(("ない", "たい")):
        return body + "です。"  # 高い・多い（形容詞）
    if body.endswith("る") and len(body) >= 2:
        if body[-2] in _E_I_ROW and not body.endswith(_GODAN_RU):
            return body[:-1] + "ます。"
        return body[:-1] + "ります。"
    if body[-1] in _GODAN_I and len(body) >= 2:
        return body[:-1] + _GODAN_I[body[-1]] + "ます。"
    if _SURU_NOUN_END.search(body):
        noun = re.search(r"[一-龥ァ-ヶー]{2,}$", body).group(0)
        if noun.endswith(sns_rules.NA_NOUNS):
            return body + "です。"  # 「〜が可能」→「〜が可能です」（「可能しました」にしない）
        if noun.endswith(SURU_NOUNS):
            return body + "しました。"  # 「〜を発表」「〜に参入」
        return ""  # 「しました」が付く名詞か分からない → 直さない（壊れた文を作らない）
    if _NOUN_END.search(body):
        return body + "です。"  # 「〜は500ドル」「52カ月連続」
    return ""


def subject_of(article: dict) -> str:
    """記事の主役（だれが）。見出しの頭（「厚生労働省、」「Hondaが」）→ 出典の名前（報道機関の名前は使わない）"""
    title = str(article.get("title") or "")
    m = re.match(r"^([^、，,「」『』\s　]{2,14}?)(?:が|は|、|，)", title)
    if m and not _MEDIA_NAME.search(m.group(1)):
        return m.group(1)
    name = str(article.get("sourceName") or "").strip()
    return name if 2 <= len(name) <= 14 and not _MEDIA_NAME.search(name) else ""


def has_subject(sentence: str, subject: str = "") -> bool:
    head = str(sentence or "")[:24]
    return bool((subject and subject in head) or re.search(r"[^でに]が|は、|は[^、。]{0,3}", head))


def with_subject(sentence: str, article: dict) -> str:
    """主語の無い文に、記事の主役を足す（「厚生労働省が、毎年11月30日を…」）"""
    subject = subject_of(article)
    if not subject or has_subject(sentence, subject):
        return sentence
    return f"{subject}が、{sentence}"


def polite_summary(summary: str, article: dict) -> str:
    """「要約すると、」のかたまりの各文を です・ます にし、1文目に主語を足す。直せない文があれば空"""
    lines = [x for x in sns_rules.summary_sentences(summary)]
    fixed = [polite_sentence(x) for x in lines]
    if not fixed or not all(fixed):
        return ""
    fixed[0] = with_subject(fixed[0], article)
    return f"{persona.SUMMARY_OPENER}、" + "\n".join(fixed)


def fact_pool(article: dict, title: str, hook: str = "", max_len: int = SENTENCE_MAX) -> list[str]:
    """要約に使える事実の文。生成時に作った紹介文（話し言葉）→ 要点 → 説明文の順。数字は記事のまま"""
    raw: list[str] = []
    post = article.get("threadsPost", "")
    blocks = post_blocks_of(post) if int(article.get("threadsStyle") or 1) >= 3 else [post]
    if len(blocks) > 1:  # 新しい形の紹介文は、要約のかたまりだけを材料にする（書き出し・問いかけは事実ではない）
        blocks = [re.sub(r"^要点まとめ[：:]\s*", f"{persona.SUMMARY_OPENER}、", b) for b in blocks]
        blocks = [b for b in blocks if b.startswith(persona.SUMMARY_OPENER)]
    for block in blocks:
        sentences = [x for line in clean_lines(block) for x in split_sentences(line.removeprefix(f"{persona.SUMMARY_OPENER}、"))]
        if len(blocks) == 1 and len(sentences) > 1 and len(sentences[0]) <= HOOK_MAX:
            sentences = sentences[1:]  # 以前の紹介文の最初の文は書き出し（事実の文ではない）
        raw += sentences
    for h in article.get("highlights", []):
        raw += split_sentences(h)
    raw += split_sentences(article.get("description", ""))
    found: list[str] = []
    for sentence in raw:
        # 文の終わりを です・ます に（直せない文は使わない。sns_rules のルール7）
        s = polite_sentence(clean_sentence(sentence))
        if not s or not 8 <= len(s) <= max_len or s.endswith(QUESTION_END) or (hook and (s in hook or hook in s)):
            continue
        if META_RE.search(s) or NUDGE_RE.search(s) or TEASER_RE.search(s) or CLICKBAIT_RE.search(s):
            continue
        if persona.STIFF_RE.search(s) or persona.ADVICE_RE.search(s) or repeats_title(s, title):
            continue
        if any(overlap(s, f) >= 12 or difflib.SequenceMatcher(None, s, f).ratio() >= 0.45 for f in found):
            continue
        found.append(s)
    return found


def summary_block(article: dict, title: str, hook: str, budget: int) -> str:
    """「要約すると、」＋事実の短い文2つ（入らなければ1つ）。文は記事の紹介文・要点から選び、数字・固有名詞は変えない"""
    # 短い文が無い記事は、少し長い文まで広げる（それでも無ければ説明文の1文目）
    pool = fact_pool(article, title, hook)[:8] or fact_pool(article, title, hook, budget - 6)[:8]
    opener = f"{persona.SUMMARY_OPENER}、"
    best: tuple[float, list[str]] | None = None
    for i, first in enumerate(pool):
        for j in [None, *range(i + 1, len(pool))]:
            chosen = [first] if j is None else [first, pool[j]]
            size = len(opener) + sum(len(s) for s in chosen) + len(chosen) - 1
            if size > budget:
                continue
            # 2文を優先。早く出てくる文（紹介文の話し言葉）と、数字のある文を優先する
            cost = i + (j or 0) * 0.5 + (6 if j is None else 0) - (3 if re.search(r"\d", "".join(chosen)) else 0)
            cost += sum(max(0, len(s) - SENTENCE_SHORT) for s in chosen) / 5  # 短い文を優先
            if best is None or cost < best[0]:
                best = (cost, chosen)
    if best is None:
        fallback = clean_sentence(str(article.get("description") or title))
        fallback = split_sentences(fallback)[0] if split_sentences(fallback) else title
        fallback = with_subject(polite_sentence(fallback) or fallback, article)
        return opener + fallback[: max(10, budget - len(opener))]
    chosen = list(best[1])
    first = with_subject(chosen[0], article)
    if len(opener) + len(first) + sum(len(x) + 1 for x in chosen[1:]) <= budget + 8:
        chosen[0] = first
    return opener + "\n".join(chosen)


HOOK_EXAMPLES = {e for hook in threads_hooks.HOOKS for e in hook["examples"]}


# この実行の中で使った呼びかけの言い回し（同じ実行で何本も作るとき、直前と同じ言い回しを続けない）
_RUN_OPENERS: list[str] = []


def recent_openers(limit: int = 3) -> list[str]:
    """直近の投稿で使った呼びかけの言い回しの id（新しい順）。この実行で作った分 → 投稿履歴"""
    try:
        posted = json.loads(STATE_PATH.read_text("utf-8")).get("posted") or {}
    except (OSError, ValueError):
        posted = {}
    history = sorted(posted.values(), key=lambda p: p.get("sentAt") or p.get("dueAt") or p.get("postedAt") or "", reverse=True)
    return (list(reversed(_RUN_OPENERS)) + [p["opener"] for p in history if p.get("opener")])[:limit]


CALLOUT_LLM_MAX = 34  # 【速報】（4字）を足しても sns_rules.CALLOUT_MAX（40字）に入る長さ
WEAK_CALLOUT = re.compile(r"押さえておきたい話|気になる人|テック好き|ニュースを追っている人|これは見てほしい")


def llm_callout(article: dict, title: str, last: list[str]) -> str:
    """（2026-11-03 オーナー指摘「呼びかけが弱い」）1行目の呼びかけを LLM に書かせる。だれに（職種・立場・状況を具体的に）＋
    なぜ今か（記事にある具体的な得・損・変化）を1行で。門番（呼びかけ・文法・無理な説明・使わない書き出し）を通らなければ空"""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY")) or os.environ.get("SNS_LLM_CALLOUT", "1").strip() == "0":
        return ""
    prompt = (
        "次のニュースを SNS に出すときの1行目（読者への呼びかけ）を1つだけ書く。\n"
        f"読者: {persona.PERSONA['who']}\n"
        f"- 全角{CALLOUT_LLM_MAX}字以内の1行。最初に、関係する読者を具体的に名指しする（職種・立場・状況。例「GPUで学習を回す担当者へ。」"
        "「議事録を毎回手で書いている人、」「iPhoneで仕事のメールを読む人に、」）。続けて、なぜ今その人に関係するかを記事の事実から短く言う。\n"
        "- 名指しは「〜な人、」「〜している人へ。」「〜の皆さん、」「〜担当者へ。」のどれかの形にする。\n"
        "- 「AIの裏側の技術が気になる人」「テック好きの皆さん」「押さえておきたい話です」のような、だれにでも当てはまる弱い言い方はしない。\n"
        "- 落ち着いた大人の自然な日本語。煽り（衝撃・ついに・必見・知らないと損・！）、【速報】、「朗報」、よく知られた会社への説明（×「AI半導体のNVIDIA」）は使わない。\n"
        "- 記事に無い事実・数字は書かない。見出しをそのまま繰り返さない。\n\n"
        f"見出し: {title}\n要約: {article.get('description', '')}\n要点: {' / '.join(str(h) for h in article.get('highlights') or [])}"
    )
    schema = {"type": "OBJECT", "properties": {"callout": {"type": "STRING"}}, "required": ["callout"]}
    try:
        import llm  # noqa: PLC0415

        data, _ = llm.generate_json("あなたはニュースサイトのSNS担当の編集者です。自然で正しい日本語だけを書きます。", prompt, schema,
                                    tier="bulk", max_output_tokens=256, purpose="sns")
        line = EMOJI_RE.sub("", str(data.get("callout") or "")).strip().strip("「」\"").splitlines()[0].strip()
    except Exception as error:  # noqa: BLE001
        log(f"  [callout] LLM で呼びかけを作れませんでした: {str(error)[:120]}")
        return ""
    digits = re.findall(r"\d[\d,.]*", line)
    source = " ".join([title, str(article.get("description") or ""), " ".join(str(h) for h in article.get("highlights") or []), str(article.get("body") or "")])
    import headline_normalize  # noqa: PLC0415

    bad = (
        len(line) > CALLOUT_LLM_MAX or not sns_rules.callout_ok(line) or line.startswith(("【速報】", persona.SUMMARY_OPENER))
        or WEAK_CALLOUT.search(line) or "朗報" in line or any(w in line for w in headline_normalize.CHILDISH_WORDS)
        or sns_rules.grammar_problems(line) or sns_rules.descriptor_problems(line) or persona.STIFF_RE.search(line) or persona.ADVICE_RE.search(line)
        or any(p.search(line) for p, _ in sns_rules.BANNED_OPENERS) or repeats_title(line, title)
        or any(d.rstrip(".,") not in source for d in digits) or threads_hooks.callout_id(line) in last
    )
    if bad:
        log(f"  [callout] LLM の呼びかけがチェックを通りません: {line[:50]}")
        return ""
    return line


def first_line_hook(article: dict, recent: list[str], breaking: bool, title: str, use_llm: bool = False) -> str:
    """1行目の書き出し（2026-11-03 オーナー承認その2）: 関係する読者を名指しする呼びかけ。

    生成時の紹介文の1行目が呼びかけになっていて、直前の投稿と同じ言い回しでなければそれを使う。
    ダメなら記事から読者を決めて組み立てる（threads_hooks.callout）。【速報】は本当の速報の記事だけ（sns_rules.is_fast_news）。
    breaking（いますぐ出す速報か）は【速報】の判定には使わない（画像の「速報」と同じ決まりにそろえる）
    """
    last = recent_openers(1)
    blocks = post_blocks_of(article.get("threadsPost", ""))
    old = clean_lines(blocks[0] if int(article.get("threadsStyle") or 1) >= 3 and blocks else article.get("threadsPost", ""))
    line = re.sub(r"^【速報】\s*", "", old[0]) if old else ""
    if len(line) > OLD_HOOK_MAX and len(split_sentences(line)) > 1:
        line = split_sentences(line)[0]
    usable = (
        sns_rules.callout_ok(line) and not line.startswith(persona.SUMMARY_OPENER) and not repeats_title(line, title)
        and line not in HOOK_EXAMPLES and not persona.STIFF_RE.search(line) and not persona.ADVICE_RE.search(line)
        and not any(p.search(line) for p, _ in sns_rules.BANNED_OPENERS)
        and threads_hooks.callout_id(line) not in last
    )
    if usable and WEAK_CALLOUT.search(line):
        usable = False  # だれにでも当てはまる弱い呼びかけは使わない（2026-11-03 オーナー指摘）
    written = llm_callout(article, title, last) if use_llm and not usable else ""
    if usable:
        key, hook = threads_hooks.callout_id(line), line
    elif written:
        key, hook = threads_hooks.callout_id(written), written
    else:
        key, hook = threads_hooks.callout(article, last)
    hook = threads_hooks.with_breaking(hook, article)
    if not sns_rules.callout_ok(hook):
        # 生成の1行目が【速報】を付けると長すぎる場合など
        key, hook = threads_hooks.callout(article, last)
        hook = threads_hooks.with_breaking(hook, article)
    article["_hook"] = "callout"
    article["_opener"] = key
    _RUN_OPENERS.append(key)
    return hook


def compose(
    article: dict,
    recent: list[str] | None = None,
    breaking: bool = False,
    recent_questions: list[str] | None = None,
    use_llm: bool = False,
    fixed_question: str = "",
    fixed_pattern: str = "",
) -> str:
    """投稿文（2026-11-02 の形）。書き出し／要約すると、＋事実2文／読者への問いかけ の3つのかたまり。

    生成時に新しい形で作った紹介文（threadsStyle 3）は、かたまりごとに確かめて使う。足りない・崩れているかたまりは
    記事の要点・判定から組み立て直す（LLM の書き損じや古い記事への備え）。問いかけが無ければ、話題に合う問いを足す。
    breaking=True（速報としていま出す）なら1行目を【速報】にする。地震・津波（disaster）は従来の形のまま。
    """
    if article.get("disaster"):
        return compose_legacy(article, recent, breaking)
    recent, recent_questions = recent or [], recent_questions or []
    title = str(article.get("title") or article.get("seoTitle") or "").strip()
    hook = first_line_hook(article, recent, breaking, title, use_llm=use_llm)
    blocks = post_blocks_of(article.get("threadsPost", "")) if int(article.get("threadsStyle") or 1) >= 3 else []
    # 問いかけ: 決まった問い（Bluesky が Threads と同じ問いを使う）→ 生成時の問いの候補（型つき。回し方で選ぶ）
    # → LLM に問いだけ書かせる → 話題・主役から作る。選んだ型は _question_pattern に残す（台帳 S19）
    question, pattern = (fixed_question, question_patterns.clean(fixed_pattern) or question_patterns.classify(fixed_question)) \
        if question_ok(fixed_question, title) else ("", "")
    if not question:
        choice = question_patterns.pick(question_candidates(article, blocks, title, hook))
        if choice:
            question, pattern = choice["question"], choice["pattern"]
    if not question and use_llm:
        question, pattern = llm_question(article, title)
    if not question:
        question = voice.reader_question(article, topic_of(article), recent_questions)
        pattern = question_patterns.classify(question)
    summary = ""
    generated = next((b for b in blocks if b.startswith(f"{persona.SUMMARY_OPENER}、")), "")
    if generated:
        # 生成時に「要約すると、」を各行の頭に重ねて書いた紹介文（2026-11-03 の実例）は、2行目以降の重複を落とす
        lines = clean_lines(generated)
        lines = lines[:1] + [line.removeprefix(f"{persona.SUMMARY_OPENER}、") for line in lines[1:]]
        candidate = "\n".join(lines)
        sentences = split_sentences(candidate.removeprefix(f"{persona.SUMMARY_OPENER}、"))
        if (
            1 <= len(sentences) <= 3 and len(candidate) <= SUMMARY_MAX
            and not persona.STIFF_RE.search(candidate) and not persona.ADVICE_RE.search(candidate)
            and not any(repeats_title(line, title) for line in lines)
        ):
            # 硬い言い切り（「〜する。」）が混ざっていれば です・ます に直す。直せなければ組み立て直す（ルール7）
            fixable = sns_rules.impolite_sentences(candidate) or sns_rules.HARD_WORDS.search(candidate) or sns_rules.grammar_problems(candidate)
            if fixable:
                # 機械で直すより、使えるなら LLM でやさしい正しい文に書き直す（文法・数字のチェックつき）。だめなら機械で直す（壊れた文は捨てる）
                budget0 = POST_MAX_CHARS - len(hook) - len(question) - 4
                summary = (llm_summary(article, title, candidate, budget0) if use_llm else "") or polite_summary(candidate, article)
            else:
                summary = candidate
            if summary and (sns_rules.grammar_problems(summary) or sns_rules.descriptor_problems(summary)):
                summary = ""  # 壊れた日本語・無理な説明が残る要約は使わない（組み立て直す）
    budget = POST_MAX_CHARS - len(hook) - len(question) - 4
    if not summary or len(summary) > budget:
        rebuilt = summary_block(article, title, hook, min(budget, POST_TARGET_CHARS - len(hook) - len(question) - 4 + 20))
        # 記事の文を組み立て直した要約は、使えるなら軽いモデルでやさしい言葉に書き直す（数字は記事のまま）
        rewritten = llm_summary(article, title, rebuilt, budget) if use_llm else ""
        summary = rewritten or rebuilt
    if sns_rules.descriptor_problems(summary):
        import headline_normalize  # noqa: PLC0415

        summary = "\n".join(headline_normalize.strip_known_descriptors(x) for x in summary.splitlines())
    text = "\n\n".join(b for b in (hook, summary, question) if b)
    article["_question"] = question
    article["_question_pattern"] = pattern or question_patterns.PLAIN
    question_patterns.note_used(article["_question_pattern"])
    return ensure_question(text, article, recent_questions)


def question_pattern_fields(article: dict, question: str) -> dict:
    """投稿の記録に残す問いかけの型（台帳 S19。流入の報告で型ごとに比べる）。問いかけの無い投稿は何も足さない"""
    if not question:
        return {}
    pattern = article.get("_question_pattern") if article.get("_question") == question else ""
    return {"questionPattern": question_patterns.clean(pattern) or question_patterns.classify(question)}


def question_candidates(article: dict, blocks: list[str], title: str, hook: str) -> list[dict]:
    """生成時に書いた問いの候補（型つき）。threadsQuestions（2026-11-04〜）と、紹介文の最後の問い。
    どれも問いのチェック（question_ok）と、怒りを誘う言い方のチェックを通ったものだけ"""
    found: list[dict] = []
    for item in question_patterns.from_generated(article.get("threadsQuestions")):
        found.append(item)
    if len(blocks) >= 2 and len(clean_lines(blocks[-1])) == 1 and clean_lines(blocks[-1])[0] != hook:
        last = clean_lines(blocks[-1])[0]
        known = next((c["pattern"] for c in found if c["question"] == last), "")
        if not known:
            found.insert(0, {"pattern": question_patterns.clean(article.get("threadsQuestionPattern")) or question_patterns.classify(last),
                             "question": last})
    out, seen = [], set()
    for c in found:
        q = c["question"]
        if q in seen or not question_ok(q, title, QUESTION_MAX) or question_patterns.question_problems(q):
            continue
        seen.add(q)
        out.append(c)
    return out


def question_ok(question: str, title: str = "", limit: int = voice.QUESTION_LIMIT) -> bool:
    """問いかけのチェック: 1文で「？」で終わる・短い・反応のお願いや投資助言や硬い言い回しが無い・見出しの繰り返しでない"""
    q = str(question or "").strip()
    return bool(
        q and q.endswith(QUESTION_END) and len(q) <= limit and "\n" not in q and q.count("？") + q.count("?") <= 2
        and not BAIT_RE.search(q) and not persona.ADVICE_RE.search(q) and not persona.STIFF_RE.search(q)
        and not re.search(r"https?://|#\S", q) and not repeats_title(q, title)
        and not sns_rules.question_needs_article(q) and not question_patterns.question_problems(q)
    )


def llm_summary(article: dict, title: str, draft: str, budget: int) -> str:
    """記事の文から組み立てた要約を、やさしい です・ます の短い2文に書き直す（主語・専門用語の言い換えつき）。失敗したら空。
    GEMINI_API_KEY があるときだけ。SNS_LLM_SUMMARY=0 で止める。数字は記事にあるものだけ（無い数字が出たら使わない）"""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY")) or os.environ.get("SNS_LLM_SUMMARY", "1").strip() == "0":
        return ""
    source = " ".join([title, str(article.get("description") or ""), " ".join(str(h) for h in article.get("highlights") or []), str(article.get("threadsPost") or "")])
    prompt = (
        "次のニュースの要約を、SNS の読者向けに2文で書き直す。\n"
        f"読者: {persona.PERSONA['who']}\n"
        "- やさしい「です・ます」の短い文（1文は全角40字くらいまで）。硬い言い切り（〜する。〜である。）は使わない。\n"
        "- 1文目に、だれが（官庁・会社の名前）何をしたかを書く。2文目は読者に関係する一番具体的な事実。\n"
        "- 専門用語は使わないか、ふつうの言葉で言い換える。数字・日付・名前は元の文のとおり。新しい事実や推測は書かない。\n"
        "- 文法の正しい自然な日本語にする（×「実行が可能しました」→○「実行できるようにしました」）。よく知られた会社に説明を付けない（×「AI半導体のNVIDIAが」→○「NVIDIAが」）。\n"
        "- 「要約すると、」は付けない。文ごとに改行する。\n\n"
        f"見出し: {title}\n元の要約: {draft.removeprefix(persona.SUMMARY_OPENER + '、')}\n要点: {' / '.join(str(h) for h in article.get('highlights') or [])}"
    )
    schema = {"type": "OBJECT", "properties": {"summary": {"type": "STRING"}}, "required": ["summary"]}
    try:
        import llm  # noqa: PLC0415

        data, _ = llm.generate_json("あなたはニュースサイトのSNS担当の編集者です。自然で正しい日本語だけを書きます。", prompt, schema, tier="bulk", max_output_tokens=512, purpose="sns")
        text = EMOJI_RE.sub("", str(data.get("summary") or "")).strip().removeprefix(f"{persona.SUMMARY_OPENER}、")
    except Exception as error:  # noqa: BLE001
        log(f"  [summary] LLM で書き直せませんでした: {str(error)[:120]}")
        return ""
    lines = [x for line in text.splitlines() for x in split_sentences(line)]
    candidate = f"{persona.SUMMARY_OPENER}、" + "\n".join(lines)
    digits = re.findall(r"\d[\d,.]*", candidate)
    if (
        not 1 <= len(lines) <= 2 or len(candidate) > budget or sns_rules.impolite_sentences(candidate) or sns_rules.HARD_WORDS.search(candidate)
        or any(len(x) > sns_rules.SUMMARY_SENTENCE_MAX for x in lines)
        or persona.STIFF_RE.search(candidate) or persona.ADVICE_RE.search(candidate) or any(repeats_title(x, title) for x in lines)
        or any(d.rstrip(".,") not in source for d in digits)
        or sns_rules.grammar_problems(candidate) or sns_rules.descriptor_problems(candidate)
    ):
        log(f"  [summary] LLM の要約がチェックを通りません: {candidate[:80]}")
        return ""
    return candidate


def llm_question(article: dict, title: str) -> tuple[str, str]:
    """生成時の問いが無い記事（以前の形の記事など）に、軽いモデルで問いかけの1文だけを書かせる。(問い, 型)。失敗したら ("", "")

    問いかけの型（scripts/question_patterns.py。台帳 S19）から記事に合うものを選ばせる（回し方で少ない型を先に示す）。
    呼び出しは今までどおり1回だけ。GEMINI_API_KEY があるときだけ。SNS_LLM_QUESTION=0 で止める（そのときは話題・主役から作る問いを使う）
    """
    if not os.environ.get("GEMINI_API_KEY") or os.environ.get("SNS_LLM_QUESTION", "1").strip() == "0":
        return "", ""
    order = question_patterns.rotation_order()
    kinds = "\n".join(
        f"  - {k}（{question_patterns.NAMES[k]}）: {p['how']}。合う記事: {p['when']}。例:「{p['examples'][0]}」"
        for k in order for p in question_patterns.PATTERNS if p["key"] == k
    )
    prompt = (
        "次のニュースを読んだ人への問いかけを、1文だけ書く。\n"
        f"読者: {persona.PERSONA['who']}\n"
        "- 記事を読まなくても答えられる、読者自身の好み・経験を聞く短い問いにする。全角35字以内（40字を超えると使えない）。「？」で終える。\n"
        "- 次の型のうち、この記事に自然に合うものを使う（上ほど優先。ただし合わない型は使わない）。どれも合わなければ pattern は plain にして、"
        "ふだん何を使っているか／何を重視するか などのふつうの問いにする。\n"
        f"{kinds}\n"
        "- 二択は、どちらを選んでも恥ずかしくない、両方にもっともな理由があるものだけ。怒りを誘う・決めつける・賛成か反対かを聞く問いにしない。\n"
        "- 「この機能、使ってみたいですか？」「いちばん気になった発表は？」のように、記事の中身を知らないと答えられない問いにしない。話題（製品の分野・お金・暮らし）にはつなげる。新しい事実や数字は書かない。\n"
        "- 「コメントしてね」などの反応のお願い、アンケートのふり、売買のすすめ、硬い言い回し、「ざっくり」は使わない。\n\n"
        f"見出し: {title}\n要約: {article.get('description', '')}\n要点: {' / '.join(str(h) for h in article.get('highlights') or [])}"
    )
    schema = {"type": "OBJECT", "properties": {"pattern": {"type": "STRING", "enum": question_patterns.ALL_KEYS}, "question": {"type": "STRING"}},
              "required": ["pattern", "question"]}
    try:
        import llm  # noqa: PLC0415

        data, _ = llm.generate_json("あなたはニュースサイトのSNS担当の編集者です。", prompt, schema, tier="bulk", max_output_tokens=512)
        question = EMOJI_RE.sub("", str(data.get("question") or "")).strip().strip("「」\"")
        pattern = question_patterns.clean(data.get("pattern")) or question_patterns.classify(question)
    except Exception as error:  # noqa: BLE001  失敗したら話題・主役から作る問いを使う
        log(f"  [question] LLM で問いを作れませんでした: {str(error)[:120]}")
        return "", ""
    if not question_ok(question, title):
        log(f"  [question] LLM の問いがチェックを通りません: {question[:60]}")
        return "", ""
    return question, pattern


def ensure_question(text: str, article: dict, recent_questions: list[str] | None = None) -> str:
    """最後が問いかけでなければ、話題に合う問いを足す（最後の砦）"""
    if str(text).rstrip().endswith(QUESTION_END):
        return text
    question = voice.reader_question(article, topic_of(article), recent_questions or [])
    article["_question"] = question
    article["_question_pattern"] = question_patterns.classify(question)
    return f"{str(text).rstrip()}\n\n{question}"


def compose_legacy(article: dict, recent: list[str] | None = None, breaking: bool = False) -> str:
    """以前の投稿文（完結型）。地震・津波の投稿だけで使う（読者への問いかけは付けない）。

    新しい記事（threadsStyle 2 以上）は生成時に作った紹介文を使い、以前の記事は見出し・要点・判定から組み立てる。
    breaking=True（速報としていま出す）なら1行目に【速報】を付ける。
    """
    lines: list[str] = []
    if int(article.get("threadsStyle") or 1) >= 2:
        lines = clean_lines(article.get("threadsPost", ""))[:5]
    if len(lines) < 2:
        lines = standalone_lines(article, recent or [], breaking)
    if not breaking and not article.get("breaking") and lines[0].startswith("【速報】"):
        # 速報扱いでない記事に【速報】とは書かない（煽りに見えるため）
        lines[0] = lines[0].removeprefix("【速報】").strip() or lines[0]
    # 「【速報】発表から12分で掲載」の1行目は使わない（2026-11-03 オーナー承認その2。sns_rules のルール6）
    if breaking and not lines[0].startswith("【速報】"):
        lines[0] = f"【速報】{lines[0]}"
    return "\n".join(lines)[:MAX_CHARS]


def speed_line(article: dict) -> str:
    """速報の投稿の1行目。公式発表から掲載まで60分以内（記事の speedMinutes があるとき）だけ"""
    minutes = article.get("speedMinutes")
    if isinstance(minutes, (int, float)) and 0 < minutes <= 60:
        return f"【速報】発表から{round(minutes)}分で掲載"
    return ""


def standalone_lines(article: dict, recent: list[str], breaking: bool) -> list[str]:
    """紹介文が古い形の記事向け：書き出し／何の発表か（見出し）／いちばん具体的な事実／自分に関係あるか"""
    title = str(article.get("title") or article.get("seoTitle") or "").strip()
    hook_line = ""
    if not breaking:
        weak = set(weak_hook_keys())
        old = clean_lines(article.get("threadsPost", ""))
        if old and len(old[0]) <= 32 and article.get("threadsHook") not in weak:
            hook_line = old[0]
            article["_hook"] = article.get("threadsHook") or ""
        else:
            hook, first = opening_line(article, recent)
            article["_hook"] = hook
            hook_line = "" if hook == "breaking" else first
    lines = [hook_line, title, key_fact(article), work_line(article), relevance_line(article)]
    found: list[str] = []
    for line in lines:
        if line and line not in found and not (found and line in found[-1]):
            found.append(line)
    return found or [title]


def weak_hook_keys() -> list[str]:
    import threads_hooks  # noqa: PLC0415

    return threads_hooks.weak_hooks()


# 悪いニュース（攻撃・悪用・事故・倒産など）。前向きな書き出しを使わない
NEGATIVE_RE = re.compile(r"悪用|攻撃|脆弱性|詐欺|不正|流出|漏えい|漏洩|障害|不具合|事故|倒産|破たん|被害|訴訟|停止|リコール")
POSITIVE_OPENERS = {"結論：日本からも、もう使えます。", "これ、無料で試せます。", "これ、明日からの仕事で試せます。"}


def openers(article: dict) -> list[tuple[str, str]]:
    """紹介文を持たない記事向けの書き出し候補（型, 1行目）。判定から、事実として言えるものだけを作る。"""
    name = re.split(r"[｜|：:？?]", str(article.get("seoTitle") or ""))[0].strip()
    name = re.sub(r"(の仕組み)?を解説$|とは$|まとめ$|入門$", "", name).strip()
    if not name or len(name) > 18:  # 長い名前を文に入れると不自然になるので「これ」で受ける
        name = "これ"
    verdict = article.get("verdict") or {}
    status, japan, free = verdict.get("status"), verdict.get("japan"), verdict.get("free")
    price, audience = str(verdict.get("price") or ""), str(verdict.get("audience") or "")
    found: list[tuple[str, str]] = []
    # 【速報】は、主要企業の公式発表（breaking）で、作ってから半日以内の記事だけ（煽りに使わない）
    generated = article.get("generatedAt")
    if article.get("breaking") and generated and datetime.now(timezone.utc) - datetime.fromisoformat(generated) < timedelta(hours=12):
        found.append(("breaking", "【速報】"))
    if status == "available":
        found.append(("question", f"{name}、もう試しました？"))
        if japan == "ok":
            found.append(("conclusion", "結論：日本からも、もう使えます。"))
    if status == "limited":
        found.append(("caution", f"{name}、使える人はまだ限られています。"))
    if status in ("soon", "demo"):
        found.append(("future", "使えるようになったら、仕事の進め方が変わりそうな発表です。"))
        found.append(("question", f"{name}、いつ使えるようになるか気になってませんか？"))
    if free is True and status == "available":
        found.append(("free", "これ、無料で試せます。"))
    if free is False and re.search(r"\d", price):
        found.append(("caution", f"使う前に料金だけ確認を。{price}です。"))
    if audience and len(audience) <= 8:
        found.append(("callout", f"{audience}の人、これはチェックしてほしい。"))
    # 読者像（scripts/persona.py）: 仕事・お金にどう効くか。記事の workMoney・判定に根拠があるときだけ
    work_money = article.get("workMoney") or {}
    if work_money.get("relevance") == "high":
        if status == "available" and japan in ("ok", "partial"):
            found.append(("use_case", "これ、明日からの仕事で試せます。"))
        elif not verdict:
            found.append(("use_case", "仕事やお金に、直接かかわる話です。"))
    if re.search(r"\d", price) and re.search(r"円|ドル|無料", price) and len(price) <= 14:
        found.append(("money", f"まず料金から。{price}です。"))
    if not any(key != "breaking" for key, _ in found):
        # 判定の無い記事（お金・制度の話題など）向け。記事の workMoney に根拠があるときだけ
        if work_money.get("relevance") == "low":
            found.append(("conclusion", "先に言うと、直接の影響は小さめです。"))
        elif work_money.get("relevance") in ("high", "medium") and article.get("genreSlug") in voice.MONEY_GENRES:
            options = ["経営や家計に関わる人は、押さえておきたい話です。", "会社のお金や家計に関わる人向けの話です。"]
            found.append(("callout", options[sum(ord(c) for c in article.get("slug", "")) % len(options)]))
    if NEGATIVE_RE.search(f"{article.get('title', '')} {article.get('seoTitle', '')}"):
        # 攻撃・悪用・事故などの話題に「もう試しました？」「無料で試せます」は合わないので使わない
        found = [o for o in found if o[1] not in POSITIVE_OPENERS and not o[1].endswith("もう試しました？")]
    return found


def opening_line(article: dict, recent: list[str], allow_breaking: bool = True) -> tuple[str, str]:
    """直近で使った型を避けて、記事ごとに書き出しを変える"""
    weak = set(weak_hook_keys())
    found = [o for o in openers(article) if allow_breaking or o[0] != "breaking"]
    options = [o for o in found if o[0] not in weak] or found
    if not options:
        options = [("question", "このニュース、もうチェックしました？")]
    fresh = [option for option in options if option[0] not in recent] or options
    # 同じ記事なら毎回同じ結果になるよう、slug から決める
    index = sum(ord(c) for c in article["slug"]) % len(fresh)
    return fresh[index]


# Threads のトピック（1投稿に1つ。Buffer の metadata.threads.topic で付ける＝本文にハッシュタグは入れない）
# 製品名 → 企業名の順に見る（「GoogleのGemini」は Gemini に）。見出しを先に、なければタグで判定
TOPIC_RULES = [
    (r"地震|震度", "地震"),
    (r"津波", "津波"),
    (r"ChatGPT|OpenAI|GPT-|Sora|Codex", "ChatGPT"),
    (r"Claude|Anthropic", "Claude"),
    (r"Gemini|Gemma|NotebookLM|DeepMind", "Gemini"),
    (r"Copilot", "Copilot"),
    (r"DeepSeek", "DeepSeek"),
    (r"NVIDIA|GeForce", "NVIDIA"),
    (r"Apple|iPhone|iPad|Mac|iOS|Vision Pro", "Apple"),
    (r"Google|Android|YouTube|Pixel", "Google"),
    (r"Microsoft|Windows|Azure", "Microsoft"),
    (r"Meta|Llama|Instagram|Facebook", "Meta"),
    (r"AWS|Amazon|Alexa", "Amazon"),
    (r"ゲーム|PS5|Nintendo|Switch|Xbox|Steam", "ゲーム"),
]


def topic_of(article: dict) -> str:
    """Threads のトピック（1つだけ）。主要な製品・企業 → 生成AI → AI の順。50字以内・「.」「&」は使えない"""
    title = f"{article.get('title', '')} {article.get('seoTitle', '')}"
    tags = " ".join(str(t) for t in article.get("tags", []) if t)
    for text in (title, tags):
        for pattern, topic in TOPIC_RULES:
            if re.search(pattern, text):
                return topic
    if article.get("genreSlug") not in (None, "ai"):
        first = re.sub(r"[.&]", "", str((article.get("tags") or [""])[0])).strip()
        return first[:50] or "ニュース"
    return "生成AI" if "生成AI" in f"{title} {tags}" else "AI"


# Threads で反応が取りやすい話題（大手・一般向け）と、取りにくい話題（企業の開発基盤など）
BROAD = re.compile(r"OpenAI|ChatGPT|Google|Gemini|Anthropic|Claude|Apple|iPhone|Meta|Sora|NotebookLM|NVIDIA|GPT|Copilot|画像生成|動画生成|音声")
NICHE = re.compile(r"Azure|SQL|Cosmos|Kubernetes|\.NET|SDK|DevOps|TypeScript|データベース|Foundry|IoT|DB開発|クラウド移行")
WIDE_AUDIENCE = {"一般ユーザー", "ビジネス職", "クリエイター", "デザイナー", "営業職", "経営層"}
# 専門的な記事も投稿する（伝え方を工夫する前提）。スコアは「どれを先に出すか」の順番だけに使う
MIN_SCORE = -99


def score(article: dict) -> int:
    """フォロワーが少ないうちは「誰でも反応できる話題」だけを流す。専門的すぎる発表は記事に任せる。"""
    text = f"{article.get('title', '')} {article.get('seoTitle', '')} {' '.join(article.get('tags', []))}"
    verdict = article.get("verdict") or {}
    value = 0
    if BROAD.search(text):
        value += 3
    if NICHE.search(text):
        value -= 5
    if verdict.get("status") == "available" and verdict.get("japan") != "no":
        value += 2
    if verdict.get("free") is True:
        value += 1
    if verdict.get("audience") in WIDE_AUDIENCE:
        value += 1
    if article.get("type") == "roundup":
        value += 2
    # 有名な製品・企業の話でも、一般向けでもない技術解説は、フォロワーが育つまで見送る
    if not BROAD.search(text) and verdict.get("audience") not in WIDE_AUDIENCE:
        value -= 2
    return value


LEARNING_PATH = ROOT / "state" / "threads_learning.json"


def hook_bonus() -> dict[str, float]:
    """書き出しの型ごとの加点（scripts/threads_learn.py の学習結果から）。

    実績が良い型ほど高く（0〜2点）、まだ2本未満しか試していない型には「試す価値」として1.5点。
    """
    if not LEARNING_PATH.exists():
        return {}
    try:
        hooks = json.loads(LEARNING_PATH.read_text("utf-8")).get("axes", {}).get("hook", {})
    except Exception:
        return {}
    import threads_hooks  # noqa: PLC0415

    tested = {k: v for k, v in hooks.items() if v.get("n", 0) >= 2}
    bonus: dict[str, float] = {}
    if tested:
        scores = [v["score"] for v in tested.values()]
        low, high = min(scores), max(scores)
        for key, value in tested.items():
            bonus[key] = 2.0 * ((value["score"] - low) / (high - low)) if high > low else 1.0
    for key in threads_hooks.HOOK_KEYS:
        if key not in tested:
            bonus[key] = 1.5
    # 十分試して明らかに伸びない型は、ほかの記事より後ろに回す
    for key in threads_hooks.weak_hooks():
        bonus[key] = -1.0
    return bonus


# ---- 通常枠の AI 以外の記事のテスト（2026-11-03 オーナー承認その2。1週間） ----
# 1日9枠のうち2枠（昼 12:15・夜 21:00）は、AI 以外の記事（車・身近な製品・お金・暮らしの制度）を出す。
# 投稿履歴に slotTest（non_ai / ai / non_ai_fallback）を残し、scripts/inflow_report.py が訪問数を比べる
NON_AI_SLOTS_JST = ((12, 15), (21, 0))
NON_AI_TEST_FROM = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)   # 11/4 0:00 日本時間
NON_AI_TEST_UNTIL = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)  # 11/11 0:00 日本時間（この前まで）
NON_AI_GENRES = {"business", "tech-gadget", "public", "investment", "real-estate", "lifestyle"}
# 読者の暮らし・お金に近い話題（AI 以外の枠で先に出す）と、身近でない話題（後に回す）
EVERYDAY_RE = re.compile(
    r"車|クルマ|バイク|自転車|iPhone|iPad|スマホ|Android|Pixel|Galaxy|家電|テレビ|イヤホン|カメラ|ゲーム|"
    r"税|年金|補助|給付|支援金|NISA|iDeCo|証券|口座|住宅|家賃|不動産|電気代|電気料金|ガス|ガソリン|旅行|免税|値上げ|値下げ|物価|"
    r"賃上げ|最低賃金|ボーナス|副業|起業|ローン|保険|ポイント|キャンペーン|料金|発売|新製品"
)
# 買う・払う・もらうに直結する話（発売・料金・補助・税など）はさらに先に
EVERYDAY_STRONG_RE = re.compile(r"発売|新型|値下げ|値上げ|料金|補助|給付|支援金|税|年金|NISA|iDeCo|ローン|キャンペーン|ポイント")
REMOTE_RE = re.compile(r"議事要旨|研究会|認可情報|褒章|叙勲|マッチングイベント|人事|会見を開催|失業率|短観")


def non_ai_test_on(now: datetime | None = None) -> bool:
    if os.environ.get("SNS_NON_AI_SLOTS", "").strip().lower() in ("0", "off", "false"):
        return False
    now = now or datetime.now(timezone.utc)
    return NON_AI_TEST_FROM <= now < NON_AI_TEST_UNTIL


def non_ai_slot(slot: datetime | None, now: datetime | None = None) -> bool:
    """この予約枠は AI 以外の記事のテスト枠か（テスト期間中の 12:15・21:00）"""
    if slot is None or not non_ai_test_on(slot):
        return False
    local = slot.astimezone(JST)
    return (local.hour, local.minute) in NON_AI_SLOTS_JST


def everyday_score(article: dict) -> int:
    """AI 以外の枠での並び順。暮らし・お金に近い話題を先に、身近でない発表（議事要旨・褒章など）を後に"""
    text = f"{article.get('title', '')} {article.get('seoTitle', '')} {' '.join(article.get('tags') or [])}"
    return (3 if EVERYDAY_RE.search(text) else 0) + (2 if EVERYDAY_STRONG_RE.search(text) else 0) - (4 if REMOTE_RE.search(text) else 0)


_VIDEO_CHECK: dict = {}


def video_possible(article: dict) -> bool:
    """動画（shorts_v3）を作れる記事か（撮ってよい公式ページがある）。記事の投稿は動画つきだけなので、作れない記事は SNS の候補にしない。
    動画の仕組みを読み込めない環境（軽いテスト）では判定しない（True）"""
    # 2026-11-04〜: 段（video_ladder）の L4 は記事の文だけで動画を作れるので、公式ページが撮れない記事も候補にする
    return bool(article.get("slug"))


def candidates(state: dict, hours: int, genres: set[str] | None = None) -> list[dict]:
    """直近 hours 時間に生成された未投稿の記事（既定は AI。genres で AI 以外のテスト枠用も）を、Threads で反応が取れそうな順に並べる。"""
    genres = genres or {"ai"}
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    posted = state["posted"]
    articles = []
    for path in ARTICLES_DIR.glob("*.json"):
        article = json.loads(path.read_text("utf-8"))
        if article.get("genreSlug") not in genres or article["slug"] in posted or article["slug"] in (state.get("ruleBlocked") or {}):
            continue
        if datetime.fromisoformat(article["generatedAt"]) < since:
            continue
        # 速報として出す記事は scripts/breaking_post.py に任せる（二重投稿を防ぐ）
        if reserved_for_breaking(article, state):
            continue
        if not sns_rules.is_urgent(article) and not video_possible(article):
            continue  # 動画を作れない記事は SNS に出さない（ルール8: 記事の投稿は動画つきだけ。急ぎの安全の知らせは例外）
        articles.append(article)

    # まとめ記事に統合済みの個別記事は、まとめ側を出せば十分なので重ねて流さない
    roundups = {a["slug"] for a in articles if a.get("type") == "roundup"} | set(posted)
    articles = [a for a in articles if a.get("roundupSlug") not in roundups]
    articles = [a for a in articles if score(a) >= MIN_SCORE]
    if genres == {"ai"}:
        articles.sort(key=lambda a: (score(a), a["generatedAt"]), reverse=True)
    else:
        articles = [a for a in articles if a.get("genreSlug") != "sports" and not a.get("disaster")]
        articles.sort(key=lambda a: (everyday_score(a), a["generatedAt"]), reverse=True)
    return articles


# 速報の即時投稿（scripts/breaking_post.py）。通常の予約枠（SNS_PER_DAY）とは別枠。
# 速報は本物の大きなニュース（主要企業の公式発表・速報レーン）だけなので、すべて出せる本数にする（2026-10-28 オーナー判断）。
# BREAKING_PER_DAY は暴走よけの安全上限（既定15・最大30。速報レーン＋レーダーの1日の安全上限30と同じ）
# 速報は投稿時刻を dueAt に記録するので、前後90分の通常枠は空けられ、1日の総数はほぼ増えない
BREAKING_PER_DAY = max(0, min(30, int(os.environ.get("BREAKING_PER_DAY") or 15)))
# 生成からこの時間以内の速報記事だけ即時に出す（それより古いものは通常の予約に回す）
BREAKING_MAX_AGE_MIN = 90
# 通常の予約が速報記事に手を出さない時間（即時投稿の待ち時間より十分長く）
BREAKING_RESERVE_HOURS = 3
# 深夜（日本時間 1:00〜5:59）は即時投稿しない。その間に作った速報は 6:00 に出す
# （見張り番の Worker が 6:00 に breaking_post.yml を起動し、queued_for_morning の記事を出す。通常の予約には回さない）
BREAKING_QUIET_HOURS_JST = range(1, 6)
BREAKING_MORNING_HOUR_JST = 6
# 朝の分を出せる時間（6:00〜この時刻まで。Worker の起動が遅れても拾えるように）
BREAKING_MORNING_UNTIL_JST = 9


def breaking_today(state: dict, now: datetime | None = None) -> int:
    """今日（日本時間）すでに出した速報の本数"""
    today = (now or datetime.now(timezone.utc)).astimezone(JST).date()
    return sum(
        1 for p in state["posted"].values()
        if p.get("breaking") and p.get("postedAt")
        and datetime.fromisoformat(p["postedAt"]).astimezone(JST).date() == today
    )


def breaking_allowed(state: dict, now: datetime | None = None) -> bool:
    """いま速報を即時投稿してよいか（深夜でない・今日の上限に達していない）"""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(JST).hour not in BREAKING_QUIET_HOURS_JST and breaking_today(state, now) < BREAKING_PER_DAY


def queued_for_morning(article: dict, now: datetime | None = None) -> bool:
    """深夜（日本時間 1〜5時）に出せなかった速報で、朝 6:00 に出す分か。
    対象は前日 23:30〜当日 6:00 に作った記事（23:30〜1:00 は即時投稿がデプロイ待ちで深夜に入った分）。いまが 1:00〜9:00 のときだけ"""
    now = (now or datetime.now(timezone.utc)).astimezone(JST)
    if not (BREAKING_QUIET_HOURS_JST.start <= now.hour < BREAKING_MORNING_UNTIL_JST):
        return False
    morning = now.replace(hour=BREAKING_MORNING_HOUR_JST, minute=0, second=0, microsecond=0)
    start = morning - timedelta(hours=6, minutes=30)
    generated = datetime.fromisoformat(article["generatedAt"]).astimezone(JST)
    return start <= generated < morning


def reserved_for_breaking(article: dict, state: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if not article.get("breaking") or article["slug"] in state["posted"]:
        return False
    if queued_for_morning(article, now):
        # 深夜の速報は朝 6:00 の即時投稿に取っておく（通常の予約で後回しにしない）
        return breaking_today(state, now) < BREAKING_PER_DAY
    age = now - datetime.fromisoformat(article["generatedAt"])
    return age < timedelta(hours=BREAKING_RESERVE_HOURS) and breaking_allowed(state, now)


# 投稿する時間帯（日本時間）。よく見られる時間から順に並べ、1日の本数 SNS_PER_DAY 個だけ使う。
# 凍結を避けるため、本数は週に1〜2本ずつ増やす（GitHub の変数 SNS_PER_DAY で変更）
SLOT_PRIORITY = [(7, 30), (12, 15), (18, 30), (21, 0), (22, 45), (9, 45), (15, 30), (23, 40), (0, 40), (11, 0), (20, 0), (17, 0)]
SNS_PER_DAY = max(1, min(len(SLOT_PRIORITY), int(os.environ.get("SNS_PER_DAY") or 9)))
SLOTS_JST = sorted(SLOT_PRIORITY[:SNS_PER_DAY])
JST = timezone(timedelta(hours=9))
# 朝のまとめ投稿（scripts/morning_digest.py「今朝のAIニュース3本」）が使う枠。通常の予約はこの枠を空けておく
# （1日の本数 SNS_PER_DAY はまとめを含めて数える）。SNS_DIGEST=0 でまとめを止め、通常の投稿に戻す
DIGEST_SLOT_JST = (7, 30)
DIGEST_ON = os.environ.get("SNS_DIGEST", "1").strip() != "0"
# 週2回（火・金の昼）の問いかけ投稿（morning_digest.py --kind poll。Buffer の API は Threads のアンケートに未対応のため）
POLL_SLOT_JST = (12, 15)
POLL_WEEKDAYS_JST: tuple[int, ...] = ()  # 問いかけ投稿は 2026-11-03 停止（12:15 の枠は通常の投稿に戻す）
# 自動処理は約6時間ごとに動くので、次の処理までの枠をまとめて予約する（処理の遅れも見込んで少し長めに）
LOOKAHEAD_HOURS = 8


def free_slots(state: dict, now: datetime | None = None, extra_taken: list[datetime] | None = None) -> list[datetime]:
    """これから LOOKAHEAD_HOURS 時間以内で、まだ予約していない投稿枠（UTC、早い順）。

    手元の投稿履歴（state/threads_posted.json）だけで決まる＝Buffer を呼ばない。過ぎた枠は返さない（取り逃した枠は飛ばす）。
    extra_taken: Buffer に実際に入っている予約の時刻（予約の直前に1回だけ読む。履歴の push に失敗した回の二重予約を防ぐ）
    """
    now = now or datetime.now(timezone.utc)
    taken = [datetime.fromisoformat(p["dueAt"]) for p in state["posted"].values() if p.get("dueAt")]
    taken += list(extra_taken or [])
    base = now.astimezone(JST).replace(second=0, microsecond=0)
    found = []
    for day in range(2):
        for hour, minute in SLOTS_JST:
            if DIGEST_ON and (hour, minute) == DIGEST_SLOT_JST:
                continue
            slot = (base + timedelta(days=day)).replace(hour=hour, minute=minute)
            if DIGEST_ON and (hour, minute) == POLL_SLOT_JST and slot.weekday() in POLL_WEEKDAYS_JST:
                continue
            if slot < base + timedelta(minutes=20) or slot > base + timedelta(hours=LOOKAHEAD_HOURS):
                continue
            # すでに予約した投稿と90分以内に重なる枠は使わない（枠の時刻を変えた日でも詰まりすぎないように）
            if all(abs((slot - t).total_seconds()) >= 90 * 60 for t in taken):
                found.append(slot.astimezone(timezone.utc))
    return found


def next_free_slot(state: dict, now: datetime | None = None) -> datetime | None:
    slots = free_slots(state, now)
    return slots[0] if slots else None


def wait_until_live(url: str, timeout: int) -> bool:
    """公開前の投稿を防ぐ。Cloudflare に自社の公開確認だと識別できるよう UA を付ける。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            request = urllib.request.Request(
                url, method="HEAD", headers={"User-Agent": "SaisokuPublishCheck/1.0 (+https://ai-tech-express.jp)"}
            )
            with urllib.request.urlopen(request, timeout=20) as res:
                if res.status == 200:
                    return True
        except urllib.error.URLError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(15)


def api(method: str, path: str, params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        request = urllib.request.Request(f"{API_BASE}/{path}?{data.decode()}")
    else:
        request = urllib.request.Request(f"{API_BASE}/{path}", data=data, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Threads API {error.code}: {error.read().decode()[:300]}") from error


def meta_existing_post(token: dict, article: dict | None, fetch=None) -> str:
    """Threads 公式 API で、自分の直近の投稿に同じ記事（/articles/<slug>）があればその ID（1記事1投稿。S8・S20）。
    読めなければ RuntimeError（確かめられない → 出さない。2026-11-04 一貫性の点検: Buffer の経路と同じ基準）"""
    slug = str((article or {}).get("slug") or "")
    if not slug:
        return ""
    fetch = fetch or api
    data = fetch("GET", f"{token['user_id']}/threads", {"fields": "id,text,link_attachment_url,timestamp", "limit": 50, "access_token": token["access_token"]})
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise RuntimeError("Threads API: 自分の投稿の一覧を読めません（二重投稿を確かめられないので出しません）")
    needle = f"/articles/{slug}"
    for row in data["data"]:
        row = row or {}
        if needle in f"{row.get('text') or ''} {row.get('link_attachment_url') or ''}":
            return str(row.get("id") or "")
    return ""


def publish(token: dict, text: str, link: str, article: dict | None = None, fetch=None) -> dict:
    """Threads 公式 API で出す（Buffer が無いときの予備）。出す直前にルールの門番を通し、同じ記事の投稿がもう無いかを確かめる"""
    sns_rules.enforce("threads", text, [], article, source="threads_post.publish")
    if existing := meta_existing_post(token, article, fetch):
        log(f"  [threads] 同じ記事の投稿がもうあります（{existing}）。二重に出しません")
        return {"id": existing, "permalink": "", "duplicate": True}
    user_id, access_token = token["user_id"], token["access_token"]
    container = api("POST", f"{user_id}/threads", {
        "media_type": "TEXT",
        "text": text,
        "link_attachment": link,
        "access_token": access_token,
    })
    time.sleep(PUBLISH_WAIT_SEC)

    # 処理が終わっていないと公開に失敗することがあるので、少し待って再試行する
    for attempt in range(1, 4):
        try:
            result = api("POST", f"{user_id}/threads_publish", {
                "creation_id": container["id"],
                "access_token": access_token,
            })
            break
        except RuntimeError as error:
            if attempt == 3:
                raise
            log(f"  [retry {attempt}/3] {error}")
            time.sleep(20)

    detail = api("GET", result["id"], {"fields": "permalink", "access_token": access_token})
    return {"id": result["id"], "permalink": detail.get("permalink", "")}


# 投稿の中身を作る・変える Buffer の操作。sns_rules を通した所（create_post など）だけが gated=True で呼べる
POSTING_MUTATION_RE = re.compile(r"\b(?:createPost|editPost|updatePost|createIdea)\s*\(")


def buffer_graphql(api_key: str, query: str, variables: dict | None = None, gated: bool = False, role: str | None = None) -> dict:
    """Buffer の GraphQL を1回呼ぶ。呼ぶ前に回数の上限・429 の待ち時間を確かめる（scripts/buffer_budget.py）。

    書き込み（mutation）は予約扱いで優先。読むだけは buffer_budget.set_role で monitor にしたスクリプトの分を少なめに抑える。
    429 を受けたら Retry-After の時刻まで止め、BufferRateLimited を投げる（ここでは再試行しない）
    """
    import buffer_budget as bb

    if POSTING_MUTATION_RE.search(query) and not gated:
        raise sns_rules.SnsRuleViolation(["投稿を作る・変える Buffer の操作は sns_rules を通した所からだけ呼べる"], "buffer", "buffer_graphql")
    role = role or bb.current_role(write=query.lstrip().startswith("mutation"))
    bb.check(role)
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = urllib.request.Request(
        BUFFER_API,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as res:
            headers = res.headers
            payload = json.loads(res.read())
    except urllib.error.HTTPError as error:
        text = error.read().decode(errors="replace")
        if error.code == 429 or "RATE_LIMIT_EXCEEDED" in text:
            until = bb.record_429(error.headers, text)
            raise bb.BufferRateLimited(
                f"Buffer API 429 RATE_LIMIT_EXCEEDED: {until.isoformat(timespec='minutes')} まで呼び出しを止めます"
            ) from error
        bb.record(role, error.headers)
        raise RuntimeError(f"Buffer API {error.code}: {text[:300]}") from error
    if any(((e or {}).get("extensions") or {}).get("code") == "RATE_LIMIT_EXCEEDED" for e in payload.get("errors") or []):
        until = bb.record_429(headers, json.dumps(payload.get("errors")))
        raise bb.BufferRateLimited(f"Buffer API RATE_LIMIT_EXCEEDED: {until.isoformat(timespec='minutes')} まで呼び出しを止めます")
    bb.record(role, headers)
    if payload.get("errors"):
        raise RuntimeError(f"Buffer API: {json.dumps(payload['errors'], ensure_ascii=False)[:300]}")
    return payload["data"]


# Buffer の組織とチャンネルの ID（ほとんど変わらない）。毎回の問い合わせ（1回に2回の呼び出し）をやめるため保存しておく
CHANNEL_CACHE = ROOT / "state" / "buffer_channels.json"
CHANNEL_CACHE_DAYS = 7
_account_memo: dict = {}


def buffer_account(api_key: str, refresh: bool = False, gql=None) -> list[dict]:
    """[{id: 組織ID, channels: [{id name displayName service isQueuePaused}]}]。保存から読み、7日より古ければ問い合わせ直す。

    保存・使い回しは BUFFER_LEDGER が設定された実行（GitHub Actions）だけ。手元・テストでは毎回問い合わせる
    """
    import buffer_budget as bb

    gql = gql or buffer_graphql
    now = datetime.now(timezone.utc)
    if not refresh and bb.persist():
        if _account_memo.get("orgs") is not None:
            return _account_memo["orgs"]
        if CHANNEL_CACHE.exists():
            try:
                cached = json.loads(CHANNEL_CACHE.read_text("utf-8"))
                fetched = datetime.fromisoformat(cached["fetchedAt"])
                if now - fetched < timedelta(days=CHANNEL_CACHE_DAYS) and cached.get("orgs"):
                    _account_memo["orgs"] = cached["orgs"]
                    return cached["orgs"]
            except (OSError, ValueError, KeyError, TypeError):
                pass
    orgs = []
    for org in gql(api_key, "query { account { organizations { id } } }")["account"]["organizations"]:
        # ID の型名に依存しないよう、値をクエリに直接埋め込む（JSON文字列としてエスケープ済み）
        data = gql(
            api_key,
            f"query {{ channels(input: {{ organizationId: {json.dumps(org['id'])} }}) "
            "{ id name displayName service isQueuePaused } }",
        )
        orgs.append({"id": org["id"], "channels": data["channels"]})
    if bb.persist() and orgs:
        _account_memo["orgs"] = orgs
        try:
            CHANNEL_CACHE.parent.mkdir(parents=True, exist_ok=True)
            CHANNEL_CACHE.write_text(
                json.dumps({"fetchedAt": now.isoformat(timespec="seconds"), "orgs": orgs}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
    return orgs


def forget_buffer_account() -> None:
    """チャンネルが見つからない・投稿先の ID が通らないときに、保存を捨てて次回問い合わせ直す"""
    _account_memo.clear()
    try:
        CHANNEL_CACHE.unlink()
    except OSError:
        pass


def buffer_org_ids(api_key: str) -> list[str]:
    return [org["id"] for org in buffer_account(api_key)]


def buffer_channels(api_key: str, refresh: bool = False) -> dict[str, dict]:
    """Buffer につながっているチャンネルを、サービス名（threads / twitter / facebook など）ごとに返す（保存を使う）"""
    found: dict[str, dict] = {}
    for org in buffer_account(api_key, refresh=refresh):
        for channel in org["channels"]:
            found.setdefault(str(channel.get("service", "")).lower(), channel)
    return found


def buffer_threads_channel(api_key: str) -> dict | None:
    """Buffer につながっている Threads のチャンネルを探す（IDを手で設定しなくて済むように。保存を使う）"""
    return buffer_channels(api_key).get("threads")


LINK_REPLY = "詳しくはこちら👇"
EASY_NOTE = "（専門用語なしの「かんたん版」もあります）"


def link_reply_text(article: dict) -> str:
    """記事リンクを付ける返信（2/2）の文。短く「詳しくはこちら👇」と、かんたん版があればその一言（2026-11-02）"""
    if not str(article.get("easy") or "").strip():
        return LINK_REPLY
    return f"{LINK_REPLY}\n{EASY_NOTE}"


# X は全角1文字を2と数える280字まで（URLは23字扱い）
X_LIMIT = 280
COMPANY_TAGS = [
    (r"OpenAI|ChatGPT|GPT|Codex|Sora", "#ChatGPT"),
    (r"Claude|Anthropic", "#Claude"),
    (r"Gemini|Google|DeepMind", "#Gemini"),
    (r"Copilot|Microsoft", "#Copilot"),
    (r"NVIDIA|GeForce|RTX", "#NVIDIA"),
]


def x_weight(text: str) -> int:
    return sum(1 if ord(c) < 0x1100 else 2 for c in text)


def compose_x(article: dict, root_text: str) -> str:
    """X の本文。Threads と同じ完結型の本文から、280（全角は2）に収まる行の組み合わせを選び、ハッシュタグを2つ添える。

    入りきらないときは、書き出しの行より中身（何の発表か・事実）を残す。【速報】の行は残す
    """
    lines = clean_lines(root_text)
    text_src = f"{article.get('title', '')} {' '.join(article.get('tags', []))}"
    tag = next((t for pattern, t in COMPANY_TAGS if re.search(pattern, text_src)), "")
    tags = " ".join(t for t in ["#AI", tag] if t) if not article.get("disaster") else "#地震" if "地震" in text_src else ""
    blocks = post_blocks_of(root_text)
    if len(blocks) == 3 and blocks[1].startswith(persona.SUMMARY_OPENER) and blocks[2].endswith(QUESTION_END):
        # 新しい形（書き出し／要約2文／問いかけ）。入らなければ 書き出し → 2文目 の順に削る。【速報】の行は残す
        # 要約と問いかけはルール（sns_rules）で必須なので削らない（入らなければ門番が X への投稿を止める）
        hook, summary, question = blocks
        first = summary.split("\n")[0] if "\n" in summary else summary
        if hook.startswith("【速報】"):
            options = [[hook, summary, question], [hook, first, question], [first, question]]
        else:
            # 1行目の呼びかけ（ルール6）を残す形を先に試す
            options = [[hook, summary, question], [hook, first, question], [summary, question], [first, question]]
        for option in options:
            candidate = "\n\n".join(option)
            if x_weight(candidate) + x_weight(tags) + 2 <= X_LIMIT:
                return f"{candidate}\n\n{tags}"
        return f"{first[:100]}\n\n{tags}"
    if lines and lines[0].startswith("【速報】"):
        options = [lines[:n] for n in range(len(lines), 0, -1)]
    else:
        options = [lines, lines[1:], lines[:3], lines[1:3], lines[:2], lines[1:2], lines[:1]]
    body = ""
    for option in options:
        candidate = "\n".join(option).strip()
        if candidate and x_weight(candidate) + x_weight(tags) + 2 <= X_LIMIT:
            body = candidate
            break
    if not body:
        body = (article.get("seoTitle") or article["title"])[:100]
    return f"{body}\n\n{tags}"


def card_url(article: dict) -> str:
    """投稿に添える図解カード（1080×1350。見出し＋要点3つ。app/card/[slug]/route.tsx）"""
    return f"{site_url()}/card/{article['slug']}"


# ---- 投稿の形式（A/B） ----
# hook_thread: 本文は2〜4行の書き出し＋👇（リンクなし・画像1枚）、詳細（要点・数字・結論・リンク）は自分への返信
# classic: これまでの完結型の本文＋図解カード、返信はリンクだけ
FORMATS = ("hook_thread", "classic")
# 書き出しを差し替えても数字・主語を欠落させない本文。旧本文との比較時に区別する。
HOOK_TEXT_VERSION = 2
# この時刻までは2つの形式を交互に出して比べる（日本時間 11/2 0:00）
AB_UNTIL = datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc)
# A/B 後に勝ちを決めるのに要る1形式あたりの本数
AB_MIN_N = 4


def ab_winner() -> str:
    """A/B の結果（threads_learn.py の postFormat 軸）から平均閲覧の高い形式。足りなければ hook_thread"""
    try:
        axis = json.loads(LEARNING_PATH.read_text("utf-8")).get("axes", {}).get("postFormat", {})
    except Exception:
        return FORMATS[0]
    scored = {k: v for k, v in axis.items() if k in FORMATS and v.get("n", 0) >= AB_MIN_N}
    if len(scored) < 2:
        return FORMATS[0]
    return max(scored, key=lambda k: scored[k]["avgViews"])


def choose_format(state: dict, article: dict, breaking: bool, offset: int = 0, now: datetime | None = None) -> str:
    """投稿の形式を決める。SNS_FORMAT で固定可。地震・津波は従来どおり。

    A/B 中は速報・通常それぞれで交互にする（速報は閲覧数の桁が違うので、別々に数えて偏りを防ぐ）
    """
    # 2026-11-03: hook_thread は止める。記事のリンクを返信に置き（ルール1: X 以外はリンクを本文）、画像に見出し入りのカバー
    # （ルール2: 写真だけのカード）を使うため。A/B も classic の勝ちで終わっている。sns_rules の門番も hook_thread の形は止める
    return "classic"


def normal_index(state: dict) -> int:
    """A/B の対象になった通常投稿の本数（choose_format と同じ数え方。問いかけの有無もこれで決める）"""
    return sum(1 for p in state["posted"].values() if p.get("format") in FORMATS and not p.get("breaking"))


def recent_voice(state: dict, field: str, n: int = 5) -> list[str]:
    """直近の投稿で使った「編集部の一言」や「問いかけ」（同じ言い回しを続けないため）"""
    history = sorted(state["posted"].values(), key=lambda p: p.get("postedAt", ""), reverse=True)
    return [p[field] for p in history if p.get(field)][:n]


def utm_link(link: str, source: str, fmt: str, content: str = "") -> str:
    """SNS ごとの流入を分けるための UTM（utm_source=threads|bluesky|facebook|x, utm_medium=social, utm_campaign=形式）。
    動画など投稿の中身を分けたいときは content（utm_content）を足す"""
    sep = "&" if "?" in link else "?"
    url = f"{link}{sep}utm_source={source}&utm_medium=social&utm_campaign={fmt}"
    return f"{url}&utm_content={urllib.parse.quote(content)}" if content else url


THREADS_LINK_LINE = "詳しくはこちら👇"
THREADS_EASY_NOTE = "（かんたん版あり）"


def with_link(text: str, article: dict, source: str = "threads", fmt: str = "classic", limit: int = MAX_CHARS) -> str:
    """本文の最後（問いかけの後）に、記事へのリンクを1行で足す（2026-11-03 オーナー決定。Threads・Bluesky・Facebook は本文にリンク）。
    上限を超えるときは書き出しのかたまりから削る"""
    url = utm_link(f"{site_url()}/articles/{article['slug']}", source, fmt)
    line = THREADS_LINK_LINE + (THREADS_EASY_NOTE if str(article.get("easy") or "").strip() else "")
    tail = f"\n\n{line}\n{url}"
    body = str(text).rstrip()
    blocks = post_blocks_of(body)
    if len(body) + len(tail) > limit and len(blocks) == 3:
        body = "\n\n".join(blocks[1:])
    if len(body) + len(tail) > limit:
        body = body[: max(0, limit - len(tail) - 1)].rstrip() + "…"
    return body + tail


IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png)(\?|$)", re.I)


def thread_image(article: dict) -> str:
    """hook_thread の本文に添える画像。元記事の写真（製品画像など）が使えるならそれ、なければ報道テロップ型のカバー（1200×630）

    フリー素材・AI 生成の写真は記事と直接関係しない絵なので、見出し入りのカバーの方が伝わる
    """
    credit = article.get("imageCredit") or {}
    thumb = str(article.get("thumbnail") or "")
    if credit.get("kind") == "source" and thumb.startswith("https://") and IMAGE_EXT_RE.search(thumb):
        return thumb
    return f"{site_url()}/cover/{article['slug']}"


def post_image(article: dict, fmt: str) -> str:
    return thread_image(article) if fmt == "hook_thread" else card_url(article)


def short_line(line: str, limit: int) -> str:
    return line if len(line) <= limit else ""


def compose_hook(article: dict, recent: list[str] | None = None, breaking: bool = False) -> str:
    """hook_thread の本文：書き出し／何の発表か／ひとこと（あれば）＋👇。リンク・ハッシュタグなし

    すべて記事の紹介文・見出し・判定から取るので、記事にない主張はしない
    """
    post = clean_lines(article.get("threadsPost", "")) if int(article.get("threadsStyle") or 1) >= 2 else []
    title = str(article.get("title") or article.get("seoTitle") or "").strip()
    head = re.split(r"[\s　]", title)[0] if title else ""
    what = short_line(post[1], 64) if len(post) >= 2 else ""
    what = what or title
    replaced_opening = False
    keep_context_pair = False
    if breaking:
        speed = ""  # 「発表からN分」の書き出しは使わない（sns_rules のルール6）
        article["_hook"] = "breaking"
        if speed:
            first = speed.replace("発表から", "公式発表から").replace("分で掲載", "分でお届け")
            replaced_opening = True
            keep_context_pair = True
        else:
            # 元の2行目だけでは「見つけた数です」などの主語・数字が欠ける。
            first, what = f"【速報】{title}", ""
    else:
        weak = set(weak_hook_keys())
        if post and len(post[0]) <= 34 and article.get("threadsHook") not in weak:
            first = post[0]
            article["_hook"] = article.get("threadsHook") or ""
        else:
            hook, first = opening_line(article, recent or [])
            article["_hook"] = hook
            replaced_opening = True
            if hook == "breaking":
                first = f"【速報】{head}"
    if replaced_opening:
        # 速報の速度表示で元の1行目を上書きするときは、1・2行目を一緒に残す。
        # 入らなければ独立した見出しを使い、文の途中では切らない。
        context = " ".join(post[:2]) if keep_context_pair and len(post) >= 2 else ""
        if first.startswith("【速報】"):
            context = re.sub(r"^【速報】\s*", "", context)
        candidates = [context] if context else []
        candidates += [title, str(article.get("seoTitle") or "").strip()]
        what = next((text for text in candidates if text and x_weight(f"{first}\n{text}") + 4 <= X_LIMIT), title)
        # 装飾的な書き出しより、何の発表か分かる本文を優先する。
        if what and (overlap(first, what) >= 10 or x_weight(f"{first}\n{what}") + 4 > X_LIMIT):
            first = ""
    punch = short_line(post[-1], 36) if len(post) >= 3 else ""
    # 書き出しが判定から作った型（結論・無料）なら、同じことを繰り返さない
    if not punch and (article.get("verdict") or {}).get("status") == "available" and article["_hook"] not in ("conclusion", "free"):
        punch = short_line(relevance_line(article), 30)
    lines: list[str] = []
    for line in [first, what, punch]:
        if line and not any(line in x or x in line or overlap(line, x) >= 10 for x in lines):
            lines.append(line)
    # X（全角2・280）にも収まるよう、入らなければ最後の行から落とす
    while len(lines) > 2 and x_weight("\n".join(lines)) + 4 > X_LIMIT:
        lines.pop()
    lines[-1] = lines[-1].rstrip("。") + "👇"
    return "\n".join(lines)


def overlap(a: str, b: str) -> int:
    """2つの文で共通するいちばん長い部分の文字数"""
    match = difflib.SequenceMatcher(None, a, str(b)).find_longest_match(0, len(a), 0, len(str(b)))
    return match.size


def detail_parts(article: dict) -> tuple[str, list[str], str]:
    """返信（スレッド）の中身：要約1〜2行／要点（数字は記事のまま）／結論の1行"""
    desc = str(article.get("description") or "").strip()
    sentences = [s for s in re.split(r"(?<=。)", desc) if s.strip()]
    summary = sentences[0].strip() if sentences else ""
    # 長すぎる要約と、要点の1つとほぼ同じ要約は省く
    if len(summary) > 70 or any(overlap(summary, b) >= 12 for b in article.get("highlights", [])):
        summary = ""
    bullets = [EMOJI_RE.sub("", str(h)).strip() for h in article.get("highlights", []) if str(h).strip()][:4]
    # 仕事・お金にどう活かせるか（記事の workMoney）を要点の後ろに1つ
    work = work_line(article, 70)
    if work and len(bullets) < 5 and not any(overlap(work, b) >= 12 for b in bullets):
        bullets = bullets[:3] + [f"仕事・お金：{work}"]
    verdict = article.get("verdict") or {}
    if verdict.get("status") == "available" and len(bullets) < 4:
        fact = relevance_line(article).rstrip("。").replace("。", "／")
        if fact and fact not in bullets:
            bullets.append(fact)
    take = str(verdict.get("take") or "").strip()
    return summary, bullets, take


def compose_detail(article: dict) -> str:
    """Threads の返信の文（リンクはリンクカードで付ける）。上限 MAX_CHARS"""
    summary, bullets, take = detail_parts(article)
    blocks = [summary or "ポイントをまとめると：", "\n".join(f"・{b}" for b in bullets), f"→ {take}" if take else ""]
    return "\n\n".join(b for b in blocks if b)[:MAX_CHARS]


def x_weight_url(text: str) -> int:
    """URL を23字と数える X の文字数"""
    urls = re.findall(r"https?://\S+", text)
    return x_weight(re.sub(r"https?://\S+", "", text)) + 23 * len(urls)


def compose_detail_x(article: dict, link: str) -> str:
    """X の返信の文。280（全角2・URL 23）に収まるまで、結論→要約→後ろの要点の順に削る。リンクは必ず残す"""
    summary, bullets, take = detail_parts(article)
    take_line = f"→ {take}" if take else ""
    options = [(summary, bullets, take_line), (summary, bullets, ""), ("", bullets, take_line), ("", bullets, "")]
    options += [("", bullets[:n], "") for n in range(len(bullets) - 1, 0, -1)]
    for s, b, t in options:
        text = "\n\n".join(x for x in [s, "\n".join(f"・{i}" for i in b), t, link] if x)
        if x_weight_url(text) <= X_LIMIT:
            return text
    return f"{(article.get('seoTitle') or article['title'])[:60]}\n{link}"


def publish_extra_channels(
    api_key: str,
    channels: dict[str, dict],
    text: str,
    link: str,
    article: dict,
    due_at: datetime | None,
    draft: bool = False,
    offsets: tuple[int, int] = (12, 27),
    fmt: str = "classic",
    fb_text: str | None = None,
    build_only: bool = False,
) -> dict:
    """X と Facebook にも同じ記事を出す。時刻は Threads から少しずらす（3つ同時に流れないように）
    build_only=True なら Buffer を呼ばず、送る入力（SNS ごと）を返す（動画ができてから sns_video_followup が送る）

    due_at が None ならいますぐ出す（shareNow。通らなければ数分後の予約に切り替える）
    fmt=hook_thread なら X は「書き出し＋画像」→「詳細＋リンク」のスレッド。Facebook は形式を問わず完結型（fb_text）＋リンク
    """
    results: dict[str, str] = {}
    card = card_url(article)
    title = article.get("seoTitle") or article["title"]
    fb_link = utm_link(link, "facebook", fmt)
    # Facebook のリンクカードの画像も写真だけのカード（16:9）。見出し入りのカバーは使わない（ルール2）
    link_card = {"url": fb_link, "title": title, "description": article.get("description", "")[:120], "thumbnail": {"url": card}}
    plans = []
    if "twitter" in channels:
        x_link = utm_link(link, "x", fmt)
        if fmt == "hook_thread":
            x_text = text
            thread = [
                {"text": x_text, "assets": [{"image": {"url": thread_image(article)}}]},
                {"text": compose_detail_x(article, x_link), "assets": []},
            ]
        else:
            x_text = compose_x(article, text)
            thread = [
                {"text": x_text, "assets": [{"image": {"url": card}}]},
                {"text": f"{link_reply_text(article)}\n{x_link}", "assets": []},
            ]
        plans.append(("twitter", offsets[0], {
            "text": x_text,
            "assets": [],
            "metadata": {"twitter": {"thread": thread}},
        }))
    if "facebook" in channels:
        # 2026-11-03 ルール1: Facebook も記事のリンクを本文に入れる（リンクカードだけにしない）
        fb_text = with_link((fb_text or text).strip(), article, "facebook", fmt)
        plans.append(("facebook", offsets[1], {
            "text": fb_text,
            "assets": [],
            "metadata": {"facebook": {"type": "post", "linkAttachment": link_card}},
        }))
    for service, offset, body in plans:
        post_input = {"channelId": channels[service]["id"], "schedulingType": "automatic", **body}
        if draft:
            post_input["mode"] = "addToQueue"
            post_input["saveToDraft"] = True
        elif due_at:
            post_input["mode"] = "customScheduled"
            post_input["dueAt"] = (due_at + timedelta(minutes=offset)).isoformat(timespec="seconds").replace("+00:00", "Z")
        else:
            post_input["mode"] = "shareNow"
        if build_only:
            results[service] = {**post_input, "_offsetMin": offset}
            continue
        try:
            try:
                results[service] = create_post(api_key, post_input, service=service, article=article, source="threads_post.publish_extra_channels")
            except sns_rules.SnsRuleViolation:
                raise
            except RuntimeError as error:
                if post_input["mode"] != "shareNow" or is_buffer_unavailable(error):
                    raise
                log(f"  [{service}] いますぐの投稿が通らないため、{offset}分後の予約にします: {error}")
                post_input["mode"] = "customScheduled"
                post_input["dueAt"] = utc_z(datetime.now(timezone.utc) + timedelta(minutes=max(offset, 1)))
                results[service] = create_post(api_key, post_input, service=service, article=article, source="threads_post.publish_extra_channels")
            log(f"  [{service}] 予約しました: {results[service]}")
        except Exception as error:  # 1つのSNSの失敗で、ほかのSNSの投稿を止めない
            log(f"  [{service}] 失敗: {error}")
            if is_buffer_unavailable(error):
                break  # 上限・429 のときは残りの SNS も呼ばない（ループで再試行しない）
    return results


def is_buffer_unavailable(error: BaseException) -> bool:
    import buffer_budget as bb

    return isinstance(error, bb.BufferUnavailable)


def utc_z(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class DuplicatePost(RuntimeError):
    """同じ記事の投稿が、この SNS の Buffer にもう入っている（予約中・出した）。作らない（2026-11-04 Kimi K3 の二重投稿のあと）"""

    def __init__(self, existing_id: str, service: str, needle: str):
        self.existing_id = existing_id
        super().__init__(f"同じ記事の投稿が {service} にもうあります（{existing_id}・{needle}）。二重に作りません")


# 実行の中で読んだ Buffer の投稿（チャンネル → [(id, 本文)]）。作ったものは足していく（同じ実行で二度作らない）
_BUFFER_RECENT: dict[str, list[tuple[str, str]]] = {}
_BUFFER_RECENT_AT: dict[str, float] = {}
# 読んでからこの秒数を過ぎたら、作る直前に読み直す（同時に動いたほかのワークフローが作った投稿を見落とさない。2026-11-04 一貫性の点検）
RECENT_POSTS_MAX_AGE_SEC = 90
RECENT_POSTS_FIELDS = "id status dueAt text metadata { ... on ThreadsPostMetadata { thread { text } } ... on TwitterPostMetadata { thread { text } } }"


def post_needle(article: dict | None, kind: str) -> str:
    """同じ投稿かを見分ける印（記事のパス・朝のまとめの号のパス）"""
    article = article or {}
    if kind == "digest":
        m = re.search(r"/digest/[^/?#\s]+", str(article.get("link") or ""))
        return m.group(0) if m else ""
    return f"/articles/{article['slug']}" if article.get("slug") else ""


def recent_channel_posts(api_key: str, channel_id: str, gql=None) -> list[tuple[str, str]]:
    """このチャンネルの、前後48時間の予約中・送信中・出した投稿（id, 本文と返信をつないだ文）。実行の中で1回だけ Buffer を読む"""
    if channel_id in _BUFFER_RECENT and time.time() - _BUFFER_RECENT_AT.get(channel_id, time.time()) <= RECENT_POSTS_MAX_AGE_SEC:
        return _BUFFER_RECENT[channel_id]
    gql = gql or buffer_graphql
    now = datetime.now(timezone.utc)
    found: list[tuple[str, str]] = []
    for org_id in buffer_org_ids(api_key):
        flt = (f"status: [scheduled, sending, sent], channelIds: [{json.dumps(channel_id)}], "
               f"startDate: {json.dumps(utc_z(now - timedelta(hours=48)))}, endDate: {json.dumps(utc_z(now + timedelta(hours=48)))}")
        data = gql(api_key, f"query {{ posts(first: 100, input: {{ organizationId: {json.dumps(org_id)}, filter: {{ {flt} }} }}) {{ edges {{ node {{ {RECENT_POSTS_FIELDS} }} }} }} }}")
        for edge in data["posts"]["edges"]:
            node = edge["node"]
            thread = ((node.get("metadata") or {}).get("thread")) or []
            found.append((node.get("id", ""), "\n".join([node.get("text") or ""] + [t.get("text") or "" for t in thread])))
    _BUFFER_RECENT[channel_id] = found
    _BUFFER_RECENT_AT[channel_id] = time.time()
    return found


def ensure_not_duplicate(api_key: str, post_input: dict, service: str, article: dict | None, kind: str, gql=None, allow: list[str] | None = None) -> None:
    """作る直前に、同じ記事の投稿がこの SNS の Buffer にもう無いかを確かめる（1記事1投稿。S8・S20）。
    あれば DuplicatePost。Buffer を読めなければ作らない（BufferUnavailable などをそのまま投げる）。下書きは見ない"""
    needle = post_needle(article, kind)
    channel = str(post_input.get("channelId") or "")
    if not needle or not channel or post_input.get("saveToDraft") or kind == "video_followup":
        return
    for post_id, text in recent_channel_posts(api_key, channel, gql):
        if post_id in (allow or []):
            # オーナーが承認した出し直し（前の投稿は SNS から消した）。その投稿だけは重複と数えない（記録に残す）
            log(f"  [{service}] オーナー承認の出し直し: 消した前の投稿 {post_id} は重複と数えない（{needle}）")
            continue
        if needle in text:
            raise DuplicatePost(post_id, service, needle)


def create_post(api_key: str, post_input: dict, *, service: str, article: dict | None, source: str, kind: str = "article") -> str:
    """Buffer に投稿を1件作り、投稿IDを返す。作る直前に SNS ルールの門番（sns_rules）を通し、
    同じ記事の投稿がこの SNS にもう無いかを Buffer で確かめる（あれば DuplicatePost。2026-11-04 の二重投稿のあと）"""
    allow = list(post_input.pop("_allowDuplicateOf", None) or [])  # オーナー承認の出し直しだけ（消した前の投稿の Buffer の ID）
    sns_rules.enforce_buffer(service, post_input, article, source=source, kind=kind)
    try:
        ensure_not_duplicate(api_key, post_input, service, article, kind, allow=allow)
    except DuplicatePost as dup:
        # もうある投稿をこの投稿として扱う（作らない）。呼び出し側はいつもどおり ID を記録する
        log(f"  [{service}] {dup}（{source}）")
        return dup.existing_id
    data = buffer_graphql(
        api_key,
        """mutation($input: CreatePostInput!) {
          createPost(input: $input) {
            ... on PostActionSuccess { post { id } }
            ... on MutationError { message }
          }
        }""",
        {"input": post_input},
        gated=True,
    )
    result = data["createPost"]
    if "message" in result:
        raise RuntimeError(f"Buffer: {result['message']}")
    needle = post_needle(article, kind)
    if needle and post_input.get("channelId") in _BUFFER_RECENT:
        _BUFFER_RECENT[post_input["channelId"]].append((result["post"]["id"], needle))
    return result["post"]["id"]


def publish_via_buffer(
    api_key: str,
    channel_id: str,
    text: str,
    link: str,
    article: dict,
    due_at: datetime | None = None,
    draft: bool = False,
    fmt: str = "classic",
    build_only: bool = False,
) -> dict:
    """Buffer に予約投稿する。build_only=True なら Buffer を呼ばず {"input": 送る入力} を返す（動画を待つ予約に使う）

    画像を1枚添える。
    classic: 本文（問いかけの後に記事のリンク。with_link）＋カード画像。返信は作らない（2026-11-03 オーナー決定）
    hook_thread: 本文は短い書き出し＋写真（またはカバー）、返信に要約・要点・結論（＋リンクカード）
    """
    cover = f"{site_url()}/cover/{article['slug']}"
    title = article.get("seoTitle") or article["title"]
    reply = compose_detail(article) if fmt == "hook_thread" else link_reply_text(article)
    thread = [{"text": text, "assets": [{"image": {"url": post_image(article, fmt)}}]}]
    # 2026-11-03〜: classic は本文の最後に記事のリンク（with_link）を入れ、返信（2/2）は作らない。画像はそのまま添える
    if fmt != "classic":
        thread.append({
            "text": reply,
            "assets": [],
            "metadata": {
                "threads": {
                    "linkAttachment": {
                        "url": utm_link(link, "threads", fmt),
                        "title": title,
                        "description": article.get("description", "")[:120],
                        "thumbnail": {"url": cover},
                    }
                }
            },
        })
    post_input = {
        "channelId": channel_id,
        "text": text,
        "schedulingType": "automatic",
        "assets": [],
        "metadata": {
            "threads": {
                # トピックを付けると、そのトピックに関心のある人のフィードに載りやすくなる
                "topic": topic_of(article),
                "thread": thread,
            }
        },
    }
    if draft:
        # 動作確認用：公開せず下書きとして保存する
        post_input["mode"] = "addToQueue"
        post_input["saveToDraft"] = True
    elif due_at:
        post_input["mode"] = "customScheduled"
        post_input["dueAt"] = due_at.isoformat(timespec="seconds").replace("+00:00", "Z")
    else:
        post_input["mode"] = "shareNow"
    if build_only:
        return {"input": post_input}
    return {"id": create_post(api_key, post_input, service="threads", article=article, source="threads_post.publish_via_buffer"), "via": "buffer", "permalink": ""}


def queue_for_video(api_key: str, channels: dict[str, dict], threads_text: str, text: str, link: str, article: dict,
                    due_at: datetime | None, fmt: str = "classic", fb_text: str | None = None, offsets: tuple[int, int] = (12, 27)) -> dict:
    """（2026-11-03 オーナー指示「SNS の投稿は動画のはず」）記事の投稿は動画つきだけ。ここでは Buffer を呼ばず、
    Threads・X・Facebook に送る入力を作って文面だけ門番で確かめ（動画の決まりは後で）、「動画待ち」として記録する。
    動画ができたら sns_video_followup.publish_queued が写真を動画に替えて、枠の時刻（過ぎていれば順番を保って直後）に送る。
    動画が作れなければ issue を立てて出さない（写真のカードでは出さない）"""
    threads_input = publish_via_buffer(api_key, channels["threads"]["id"], threads_text, link, article, due_at, fmt=fmt, build_only=True)["input"]
    extra = {k: v for k, v in channels.items() if k in ("twitter", "facebook")}
    inputs = {"threads": threads_input, **publish_extra_channels(api_key, extra, text, link, article, due_at, fmt=fmt, fb_text=fb_text, offsets=offsets, build_only=True)}
    for service, post_input in inputs.items():
        clean = {k: v for k, v in post_input.items() if not k.startswith("_")}
        sns_rules.enforce_buffer(service, clean, article, source="threads_post.queue_for_video", pending_video=True)
    return {
        "id": "", "via": "buffer", "permalink": "", "status": "awaiting_video",
        "pending": {"inputs": inputs, "slot": (due_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "fmt": fmt},
    }


def share_now(
    api_key: str, article: dict, text: str, fmt: str = "classic", fb_text: str | None = None, threads_text: str | None = None
) -> dict:
    """いますぐ Threads・X・Facebook に出す（速報用）。戻り値は投稿履歴にそのまま入れられる形。

    2026-11-03〜: 動画つきだけ（sns_rules ルール8）。ここでは「動画待ち」として記録し、動画ができしだい
    sns_video_followup.publish_queued が shareNow で出す（写真のカードでは出さない）
    """
    link = f"{site_url()}/articles/{article['slug']}"
    channels = buffer_channels(api_key)
    if "threads" not in channels:
        raise RuntimeError("Buffer に Threads のチャンネルが見つかりません")
    now = datetime.now(timezone.utc)
    if sns_rules.is_urgent(article):
        # 災害・警報など遅れると読者に害がある知らせ（2026-11-03 オーナー承認）: 動画を待たず、写真のカードでいますぐ出す
        return share_now_direct(api_key, channels, article, text, link, fmt=fmt, fb_text=fb_text, threads_text=threads_text)
    # 速報も動画つきだけ（ルール8）。動画ができしだい sns_video_followup がすぐ出す（写真では出さない）
    result = queue_for_video(api_key, channels, threads_text or text, text, link, article, None, fmt=fmt, fb_text=fb_text, offsets=(1, 2))
    result["dueAt"] = now.isoformat(timespec="seconds")
    return result


def share_now_direct(api_key: str, channels: dict, article: dict, text: str, link: str, fmt: str = "classic",
                     fb_text: str | None = None, threads_text: str | None = None) -> dict:
    """（急ぎの安全の知らせだけ）Threads・X・Facebook にいますぐ写真のカードで出す。shareNow が通らなければ1分後の予約"""
    now = datetime.now(timezone.utc)
    due: datetime | None = None
    try:
        result = publish_via_buffer(api_key, channels["threads"]["id"], threads_text or text, link, article, fmt=fmt)
    except sns_rules.SnsRuleViolation:
        raise
    except RuntimeError as error:
        if is_buffer_unavailable(error):
            raise
        log(f"  [threads] いますぐの投稿が通らないため、1分後の予約にします: {error}")
        due = now + timedelta(minutes=1)
        result = publish_via_buffer(api_key, channels["threads"]["id"], threads_text or text, link, article, due_at=due, fmt=fmt)
    extra = {k: v for k, v in channels.items() if k in ("twitter", "facebook")}
    result["channels"] = publish_extra_channels(api_key, extra, text, link, article, due_at=due, offsets=(1, 2), fmt=fmt, fb_text=fb_text)
    result["dueAt"] = (due or now).isoformat(timespec="seconds")
    result["urgent"] = True
    return result


def block_article(state: dict, slug: str, error: Exception) -> None:
    """SNS ルールの門番（sns_rules）が止めた記事を記録する（候補から外す。投稿履歴 posted には入れない）"""
    log(f"  [rules] ルール違反のため出しません: {error}")
    state.setdefault("ruleBlocked", {})[slug] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "violations": list(getattr(error, "violations", []) or [str(error)])[:5],
    }
    save_state(state)


def scheduled_in_buffer(api_key: str, channel_id: str, now: datetime | None = None) -> list[dict]:
    """Buffer に入っている、これからの Threads の予約（dueAt・本文）。予約する回だけ1回読む（ページは1枚＝最大50件）"""
    now = now or datetime.now(timezone.utc)
    start, end = now - timedelta(hours=2), now + timedelta(hours=LOOKAHEAD_HOURS + 2)
    out: list[dict] = []
    for org_id in buffer_org_ids(api_key):
        filters = (
            f"status: [scheduled], channelIds: [{json.dumps(channel_id)}], "
            f"startDate: {json.dumps(utc_z(start))}, endDate: {json.dumps(utc_z(end))}"
        )
        data = buffer_graphql(
            api_key,
            f"""query {{ posts(first: 50, input: {{ organizationId: {json.dumps(org_id)}, filter: {{ {filters} }} }}) {{
                edges {{ node {{ id status dueAt text }} }} }} }}""",
        )
        out += [edge["node"] for edge in data["posts"]["edges"]]
    return out


def drop_remote_duplicates(plan: list[tuple[dict, datetime | None]], remote: list[dict]) -> list[tuple[dict, datetime | None]]:
    """Buffer の予約と90分以内に重なる枠・同じ記事がもう入っている枠は使わない（履歴の push に失敗した回の二重予約を防ぐ）"""
    times = []
    for node in remote:
        try:
            times.append(datetime.fromisoformat(str(node.get("dueAt") or "").replace("Z", "+00:00")))
        except ValueError:
            continue
    texts = "\n".join(str(node.get("text") or "") for node in remote)
    kept = []
    for article, slot in plan:
        if f"/articles/{article['slug']}" in texts:
            log(f"[skip] {article['slug']} は Buffer にもう予約されています")
            continue
        if slot and any(abs((slot - t).total_seconds()) < 90 * 60 for t in times if t.tzinfo):
            log(f"[skip] {slot.astimezone(JST):%m/%d %H:%M} の枠は Buffer にもう予約があります")
            continue
        kept.append((article, slot))
    return kept


def write_summary(lines: list[str]) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max", type=int, default=2, help="1回で投稿する最大本数")
    parser.add_argument("--hours", type=int, default=36, help="何時間前までに生成された記事を対象にするか")
    parser.add_argument("--wait", type=int, default=600, help="記事の公開を待つ最大秒数")
    parser.add_argument("--dry-run", action="store_true", help="投稿せず文面だけ表示する")
    parser.add_argument("--check-buffer", action="store_true", help="Buffer の接続先を確認するだけ")
    parser.add_argument("--test-draft", action="store_true", help="投稿形式の確認用に、Buffer へ下書きを作って消す")
    args = parser.parse_args()

    live = os.environ.get("THREADS_MODE") == "live" and not args.dry_run
    state = load_state()
    buffer_key = os.environ.get("BUFFER_API_KEY", "").strip()
    if buffer_key and not buffer_key.isascii():
        # クリップボードの取り違えなどで、キー以外の文字列が登録されているとき
        log("[error] BUFFER_API_KEY の形式が不正です（日本語などが含まれています）。キーを登録し直してください")
        buffer_key = ""

    # 2026-11-03: Buffer の 24時間の上限に当たったため、空き枠と記事を手元の履歴だけで先に決め、
    # 予約するものがある回だけ Buffer を呼ぶ（チャンネルの ID は state/buffer_channels.json に保存して使い回す）
    buffer_channel = None
    extra_channels: dict[str, dict] = {}

    def connect_buffer(refresh: bool = False) -> bool:
        nonlocal buffer_channel, extra_channels
        try:
            all_channels = buffer_channels(buffer_key, refresh=refresh)
            if "threads" not in all_channels and not refresh:
                all_channels = buffer_channels(buffer_key, refresh=True)
        except RuntimeError as error:
            log(f"[warn] Buffer に接続できません: {error}")
            return False
        buffer_channel = all_channels.get("threads")
        extra_channels = {k: v for k, v in all_channels.items() if k in ("twitter", "facebook")}
        if extra_channels:
            log(f"[buffer] ほかの投稿先: {', '.join(extra_channels)}")
        if buffer_channel:
            log(f"[buffer] 投稿先: {buffer_channel.get('displayName') or buffer_channel.get('name')} (Threads)")
        else:
            log("[buffer] Buffer に Threads のチャンネルが見つかりません（Buffer で Threads を接続してください）")
        return bool(buffer_channel)

    if args.check_buffer:
        return 0 if buffer_key and connect_buffer(refresh=True) else 1
    if args.test_draft:
        # 新しい投稿形式（画像つき本文＋リンクは返信）が Buffer に受け付けられるかを、下書きを作って消すことで確かめる
        if not buffer_key or not connect_buffer():
            return 1
        sample = (candidates(state, 24 * 30) or [None])[0]
        if sample is None:
            log("[test] 確認に使える記事がありません")
            return 1
        # 確かめる形式は SNS_FORMAT（未設定なら新しい hook_thread）
        test_fmt = "classic"  # hook_thread は 2026-11-03 に止めた（choose_format）
        test_text = compose_hook(sample) if test_fmt == "hook_thread" else compose(sample)
        log(f"[test] 形式: {test_fmt}")
        # classic は本文だけで完結（問いかけ入り）。hook_thread は編集部の一言と問いかけも入れた形で確かめる
        threads_test = with_link(test_text, sample, "threads", test_fmt) if test_fmt == "classic" else voice.add_voice(
            test_text, voice.editor_comment(sample, []), voice.question_line(sample, topic_of(sample), []), MAX_CHARS
        )
        log(f"[test] Threads の本文:\n{threads_test}")
        result = publish_via_buffer(
            buffer_key, buffer_channel["id"], threads_test, f"{site_url()}/articles/{sample['slug']}", sample, draft=True, fmt=test_fmt
        )
        log(f"[test] 下書きの作成に成功しました: {result['id']}")
        # トピックが Buffer に保存されたかを読み戻して確かめる
        try:
            saved = buffer_graphql(
                buffer_key,
                "query($id: PostId!) { post(input: { id: $id }) { metadata { ... on ThreadsPostMetadata { topic } } } }",
                {"id": result["id"]},
            )
            log(f"[test] トピック: 送った値={topic_of(sample)} / Buffer の保存値={(saved.get('post') or {}).get('metadata')}")
        except RuntimeError as error:
            log(f"[test] トピックの読み戻しに失敗: {error}")
        deleted = buffer_graphql(
            buffer_key,
            """mutation($input: DeletePostInput!) {
              deletePost(input: $input) { __typename ... on MutationError { message } }
            }""",
            {"input": {"id": result["id"]}},
        )
        log(f"[test] 下書きを削除しました: {deleted}")
        # X・Facebook もつながっていれば、同じように下書きを作って消す
        if extra_channels:
            extra = publish_extra_channels(
                buffer_key, extra_channels, test_text, f"{site_url()}/articles/{sample['slug']}", sample, None, draft=True,
                fmt=test_fmt, fb_text=compose(sample),
            )
            for service, post_id in extra.items():
                deleted = buffer_graphql(
                    buffer_key,
                    """mutation($input: DeletePostInput!) {
                      deletePost(input: $input) { __typename ... on MutationError { message } }
                    }""",
                    {"input": {"id": post_id}},
                )
                log(f"[test] {service} の下書きを削除しました: {deleted}")
            log("[test] X 用の本文:\n" + (test_text if test_fmt == "hook_thread" else compose_x(sample, compose(sample))))
        return 0

    token = None
    if live and not buffer_key:
        token = load_token()
        if token is None:
            log("[skip] 投稿経路が未設定です（Buffer の API キー、または scripts/threads_auth.py のトークン）")
            return 0
        # 投稿が無い回でも期限（60日）を延ばし続ける
        token = refresh_if_needed(token)

    # Buffer 経由では、次の自動処理までに来る投稿枠をまとめて予約する。枠が無ければ Buffer を一度も呼ばずに終わる
    slots: list[datetime | None] = free_slots(state) if buffer_key else [None] * args.max
    if not slots:
        log("[done] 直近の投稿枠はすべて予約済みです（Buffer は呼びません）")
        return 0
    history = sorted(state["posted"].values(), key=lambda p: p.get("postedAt", ""), reverse=True)
    recent_hooks = [p["hook"] for p in history if p.get("hook")][:3]
    learned = hook_bonus()

    def rank(articles: list[dict]) -> list[dict]:
        # 実績の良い型（と、まだ試していない型）を先に → 記事の向き不向き
        return sorted(articles, key=lambda a: -(learned.get(a.get("threadsHook") or "", 0.0) + score(a) * 0.3))

    # 新着を優先し、足りない分は直近2週間のまだ投稿していない記事から補う
    fresh = rank(candidates(state, args.hours))
    seen = {a["slug"] for a in fresh}
    backlog = [a for a in rank(candidates(state, 14 * 24)) if a["slug"] not in seen]
    pool = fresh + backlog
    if not fresh and backlog:
        log("[fallback] 新着に Threads 向きの記事が無いため、直近2週間の記事から選びました")

    # AI 以外の記事のテスト枠用（新着 → 直近3日）。暮らし・お金に近い話題から
    other_fresh = candidates(state, args.hours, NON_AI_GENRES)
    other_seen = {a["slug"] for a in other_fresh}
    other_pool = other_fresh + [a for a in candidates(state, 3 * 24, NON_AI_GENRES) if a["slug"] not in other_seen]

    # 枠ごとに1本ずつ選ぶ。直前に使った書き出しの型と同じ型は避ける（同じ型が続くと読み飛ばされるため）
    plan: list[tuple[dict, datetime | None]] = []
    slot_test: dict[str, str] = {}
    for slot in slots:
        test = ""
        if non_ai_slot(slot) and other_pool:
            pick, test = other_pool.pop(0), "non_ai"
        else:
            if not pool:
                break
            pick = next((a for a in pool if a.get("threadsHook") not in recent_hooks), pool[0])
            pool.remove(pick)
            if non_ai_slot(slot):
                test = "non_ai_fallback"  # AI 以外の記事が無かった
            elif non_ai_test_on(slot):
                test = "ai"
        if pick.get("threadsHook"):
            recent_hooks = [pick["threadsHook"], *recent_hooks][:3]
        if test:
            slot_test[pick["slug"]] = test
        plan.append((pick, slot))
    if not plan:
        log("[done] 投稿できる記事がありません")
        return 0
    if live and buffer_key:
        # ここで初めて Buffer を呼ぶ（チャンネルは保存から。Buffer に入っている予約と突き合わせて二重予約を防ぐ）
        if not connect_buffer():
            log("[skip] Buffer を今は呼べないため、この回は予約しません。過ぎていない枠は次の回で予約します")
            return 0
        try:
            remote = scheduled_in_buffer(buffer_key, buffer_channel["id"])
        except RuntimeError as error:
            if is_buffer_unavailable(error):
                log(f"[skip] {error}。過ぎていない枠は次の回で予約します")
                return 0
            log(f"[warn] Buffer の予約一覧を読めませんでした（手元の履歴だけで判断します）: {str(error)[:200]}")
            remote = []
        plan = drop_remote_duplicates(plan, remote)
        if not plan:
            log("[done] Buffer にすでに予約が入っています（手元の履歴に無い分）。二重に予約しません")
            return 0
    for article, slot in plan:
        if slot:
            test = slot_test.get(article["slug"], "")
            log(f"[slot] {slot.astimezone(JST):%m/%d %H:%M} に予約: {article['slug']}" + (f"（テスト枠: {test}）" if test else ""))

    summary = [f"### Threads {'投稿' if live else '下書き（未投稿）'}", ""]
    # 直近の投稿で使った書き出しの型（同じ型を続けないため）
    history = sorted(state["posted"].values(), key=lambda p: p.get("postedAt", ""), reverse=True)
    recent = [p["hook"] for p in history if p.get("hook")][:4]
    recent_comments, recent_questions = recent_voice(state, "comment"), recent_voice(state, "questionText")
    for offset, (article, due_at) in enumerate(plan):
        link = f"{site_url()}/articles/{article['slug']}"
        # 下書きモードでは履歴が増えないので、何本目かを足して交互にする（本番は保存済みの履歴で数える）
        # 公式 API の経路（返信を作れない）は従来の形式のまま
        fmt = choose_format(state, article, breaking=False, offset=0 if live else offset) if buffer_key or not live else "classic"
        classic_text = compose(article, recent, recent_questions=recent_questions, use_llm=True)
        text = compose_hook(article, recent) if fmt == "hook_thread" else classic_text
        hook = article.get("_hook") or article.get("threadsHook") or ""
        if hook:
            recent = [hook, *recent][:4]
        if fmt == "classic":
            # 本文は「書き出し／要約すると、2文／問いかけ」で完結（2026-11-02）。編集部の一言は足さない（形を崩さないため）
            comment, question = "", article.get("_question") or ""
            for problem in post_problems(text, article):
                log(f"  [check] {article['slug']}: {problem}")
            # 2026-11-03〜: Threads は本文の最後に記事のリンク（返信は作らない）
            threads_text = with_link(text, article, "threads", fmt)
        else:
            # hook_thread（以前の A/B の形）: Threads の本文だけに「編集部の一言」と、約半分は「問いかけ」を足す
            comment = voice.editor_comment(article, recent_comments)
            question = ""
            if voice.want_question(normal_index(state) + (0 if live else offset)):
                question = voice.question_line(article, topic_of(article), recent_questions)
            threads_text = voice.add_voice(text, comment, question, MAX_CHARS)
            comment = comment if comment and comment in threads_text else ""
        question = question if question and question in threads_text else ""
        recent_comments = [comment, *recent_comments][:5] if comment else recent_comments
        recent_questions = [question, *recent_questions][:5] if question else recent_questions
        detail = compose_detail(article) if fmt == "hook_thread" else "（なし。リンクは本文。X だけ返信にリンク）"
        log(
            f"--- {article['slug']} [{fmt}] 問いかけ={'あり' if question else 'なし'} トピック={topic_of(article)} "
            f"({len(threads_text)}文字)\n{threads_text}\n[返信]\n{detail}\n[画像] {post_image(article, fmt)}\n{link}\n"
        )
        summary += [f"**{article['slug']}** [{fmt}]", "", "```", threads_text, "---", detail, link, "```", ""]
        if not live:
            continue

        if not wait_until_live(link, args.wait):
            log(f"  [skip] 記事がまだ公開されていません。次回に回します: {link}")
            continue
        try:
            if buffer_channel and sns_rules.is_urgent(article):
                # 災害・警報など急ぎの安全の知らせ（2026-11-03 オーナー承認）: 動画を待たずに写真のカードで予約する
                result = publish_via_buffer(buffer_key, buffer_channel["id"], threads_text, link, article, due_at, fmt=fmt)
                if extra_channels:
                    result["channels"] = publish_extra_channels(buffer_key, extra_channels, text, link, article, due_at, fmt=fmt, fb_text=classic_text)
                result["urgent"] = True
            elif buffer_channel:
                # 記事の投稿は動画つきだけ（ルール8）。ここでは予約せず「動画待ち」にする（動画ができたら sns_video_followup が予約する）
                result = queue_for_video(buffer_key, {"threads": buffer_channel, **extra_channels}, threads_text, text, link, article, due_at,
                                         fmt=fmt, fb_text=classic_text)
            else:
                result = publish(token, threads_text, link, article)
        except sns_rules.SnsRuleViolation as error:
            # ルールの門番が止めた記事は、次の回で同じ違反を繰り返さないよう記録して選ばない（直したら ruleBlocked から消す）
            block_article(state, article["slug"], error)
            continue
        except RuntimeError as error:
            # 1本の失敗でパイプライン全体を止めない。未投稿のまま次回再挑戦する
            log(f"  [error] {error}")
            if is_buffer_unavailable(error):
                log("  [stop] Buffer の上限のため、この回の残りの予約はやめます（過ぎていない枠は次の回で予約）")
                break
            if "channel" in str(error).lower():
                forget_buffer_account()  # 投稿先の ID が古いかもしれないので、次の回に問い合わせ直す
            continue
        state["posted"][article["slug"]] = {
            **result,
            "hook": hook,
            **({"opener": article["_opener"]} if article.get("_opener") else {}),
            **({"slotTest": slot_test[article["slug"]]} if article["slug"] in slot_test else {}),
            "genre": article.get("genreSlug") or "",
            "score": score(article),
            "style": "standalone",
            "image": "card" if fmt == "classic" else "photo" if post_image(article, fmt) != f"{site_url()}/cover/{article['slug']}" else "cover",
            "format": fmt,
            **({"hookTextVersion": HOOK_TEXT_VERSION} if fmt == "hook_thread" else {}),
            "topic": topic_of(article),
            "question": bool(question),
            **({"questionText": question} if question else {}),
            **(question_pattern_fields(article, question)),
            **({"comment": comment} if comment else {}),
            **({"dueAt": due_at.isoformat(timespec="seconds")} if due_at and buffer_channel else {}),
            "postedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        save_state(state)
        log(f"  [posted] {result['permalink'] or result['id'] or '動画待ち（動画ができたら予約）'}")

    write_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
