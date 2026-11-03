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


def fact_pool(article: dict, title: str, hook: str = "", max_len: int = SENTENCE_MAX) -> list[str]:
    """要約に使える事実の文。生成時に作った紹介文（話し言葉）→ 要点 → 説明文の順。数字は記事のまま"""
    raw: list[str] = []
    post = article.get("threadsPost", "")
    blocks = post_blocks_of(post) if int(article.get("threadsStyle") or 1) >= 3 else [post]
    if len(blocks) > 1:  # 新しい形の紹介文は、要約のかたまりだけを材料にする（書き出し・問いかけは事実ではない）
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
        s = clean_sentence(sentence)
        if not 8 <= len(s) <= max_len or s.endswith(QUESTION_END) or (hook and (s in hook or hook in s)):
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
        return opener + fallback[: max(10, budget - len(opener))]
    return opener + "\n".join(best[1])


HOOK_EXAMPLES = {e for hook in threads_hooks.HOOKS for e in hook["examples"]}


def first_line_hook(article: dict, recent: list[str], breaking: bool, title: str) -> str:
    """1行目の短い書き出し。生成時の紹介文の1行目が短ければそれ、なければ判定から型を選ぶ（直近の型は避ける）"""
    if breaking and not article.get("disaster") and speed_line(article):
        article["_hook"] = "breaking"
        return speed_line(article)
    weak = set(weak_hook_keys())
    blocks = post_blocks_of(article.get("threadsPost", ""))
    old = clean_lines(blocks[0] if int(article.get("threadsStyle") or 1) >= 3 and blocks else article.get("threadsPost", ""))
    line = re.sub(r"^【速報】\s*", "", old[0]) if old else ""
    if len(line) > OLD_HOOK_MAX and len(split_sentences(line)) > 1:
        line = split_sentences(line)[0]  # 改行の無い以前の紹介文は、最初の文を書き出しの候補にする
    usable = (
        line and len(line) <= OLD_HOOK_MAX and article.get("threadsHook") not in weak
        and not line.startswith(persona.SUMMARY_OPENER) and not repeats_title(line, title)
        and line not in HOOK_EXAMPLES  # 型の例をそのまま写した書き出し（記事と合わないことがある）は使わない
        and not persona.STIFF_RE.search(line) and not persona.ADVICE_RE.search(line)
    )
    if usable:
        article["_hook"] = article.get("threadsHook") or ""
        hook = line
    else:
        hook_key, hook = opening_line(article, recent, allow_breaking=False)
        article["_hook"] = hook_key
    if breaking:
        article["_hook"] = "breaking"
        if not hook.startswith("【速報】"):
            head = re.split(r"[、，,\s　]", title)[0] if title else ""
            hook = f"【速報】{head}" if 4 <= len(head) <= 24 and not repeats_title(head, title) else f"【速報】{hook}"
    return hook


def compose(
    article: dict,
    recent: list[str] | None = None,
    breaking: bool = False,
    recent_questions: list[str] | None = None,
    use_llm: bool = False,
    fixed_question: str = "",
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
    hook = first_line_hook(article, recent, breaking, title)
    blocks = post_blocks_of(article.get("threadsPost", "")) if int(article.get("threadsStyle") or 1) >= 3 else []
    # 問いかけ: 決まった問い（Bluesky が Threads と同じ問いを使う）→ 生成時の問い → LLM に問いだけ書かせる → 話題・主役から作る
    question = fixed_question if question_ok(fixed_question, title) else ""
    if not question and len(blocks) >= 2 and len(clean_lines(blocks[-1])) == 1 and clean_lines(blocks[-1])[0] != hook:
        last = clean_lines(blocks[-1])[0]
        question = last if question_ok(last, title, QUESTION_MAX) else ""
    if not question and use_llm:
        question = llm_question(article, title)
    if not question:
        question = voice.reader_question(article, topic_of(article), recent_questions)
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
            summary = candidate
    budget = POST_MAX_CHARS - len(hook) - len(question) - 4
    if not summary or len(summary) > budget:
        summary = summary_block(article, title, hook, min(budget, POST_TARGET_CHARS - len(hook) - len(question) - 4 + 20))
    text = "\n\n".join(b for b in (hook, summary, question) if b)
    article["_question"] = question
    return ensure_question(text, article, recent_questions)


def question_ok(question: str, title: str = "", limit: int = voice.QUESTION_LIMIT) -> bool:
    """問いかけのチェック: 1文で「？」で終わる・短い・反応のお願いや投資助言や硬い言い回しが無い・見出しの繰り返しでない"""
    q = str(question or "").strip()
    return bool(
        q and q.endswith(QUESTION_END) and len(q) <= limit and "\n" not in q and q.count("？") + q.count("?") <= 2
        and not BAIT_RE.search(q) and not persona.ADVICE_RE.search(q) and not persona.STIFF_RE.search(q)
        and not re.search(r"https?://|#\S", q) and not repeats_title(q, title)
    )


def llm_question(article: dict, title: str) -> str:
    """生成時の問いが無い記事（以前の形の記事など）に、軽いモデルで問いかけの1文だけを書かせる。失敗したら空

    GEMINI_API_KEY があるときだけ。SNS_LLM_QUESTION=0 で止める（そのときは話題・主役から作る問いを使う）
    """
    if not os.environ.get("GEMINI_API_KEY") or os.environ.get("SNS_LLM_QUESTION", "1").strip() == "0":
        return ""
    prompt = (
        "次のニュースを読んだ人への問いかけを、1文だけ書く。\n"
        f"読者: {persona.PERSONA['who']}\n"
        "- 読者自身の仕事・お金・副業・使っている道具について、一言で答えられる短い問いにする。全角35字以内（40字を超えると使えない）。「？」で終える。\n"
        "- このニュースの製品・会社・話題に直接つながる問いにする（関係の薄い一般論にしない）。新しい事実や数字は書かない。\n"
        "- 「コメントしてね」などの反応のお願い、アンケートのふり、売買のすすめ、硬い言い回し、「ざっくり」は使わない。\n"
        "例:「あなたの会社や取引先では、円安の影響は出ていますか？」「Pixel 11 Proのカメラ、料理や仕事の写真で使ってみたいですか？」\n\n"
        f"見出し: {title}\n要約: {article.get('description', '')}\n要点: {' / '.join(str(h) for h in article.get('highlights') or [])}"
    )
    schema = {"type": "OBJECT", "properties": {"question": {"type": "STRING"}}, "required": ["question"]}
    try:
        import llm  # noqa: PLC0415

        data, _ = llm.generate_json("あなたはニュースサイトのSNS担当の編集者です。", prompt, schema, tier="bulk", max_output_tokens=512)
        question = EMOJI_RE.sub("", str(data.get("question") or "")).strip().strip("「」\"")
    except Exception as error:  # noqa: BLE001  失敗したら話題・主役から作る問いを使う
        log(f"  [question] LLM で問いを作れませんでした: {str(error)[:120]}")
        return ""
    if not question_ok(question, title):
        log(f"  [question] LLM の問いがチェックを通りません: {question[:60]}")
        return ""
    return question


def ensure_question(text: str, article: dict, recent_questions: list[str] | None = None) -> str:
    """最後が問いかけでなければ、話題に合う問いを足す（最後の砦）"""
    if str(text).rstrip().endswith(QUESTION_END):
        return text
    question = voice.reader_question(article, topic_of(article), recent_questions or [])
    article["_question"] = question
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
    if breaking and speed_line(article) and not article.get("disaster"):
        # 地震・津波（disaster）の投稿では速さを誇らない（記事ページのラベルには出す）
        # 速さが確かな速報は1行目を「【速報】発表から12分で掲載」にする（数字は記事の speedMinutes。見出しには入れない）
        lines[0] = lines[0].removeprefix("【速報】").strip()
        lines = [speed_line(article)] + [line for line in lines if line]
    elif breaking and not lines[0].startswith("【速報】"):
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


def candidates(state: dict, hours: int) -> list[dict]:
    """直近 hours 時間に生成された未投稿のAI記事を、Threads で反応が取れそうな順に並べる。"""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    posted = state["posted"]
    articles = []
    for path in ARTICLES_DIR.glob("*.json"):
        article = json.loads(path.read_text("utf-8"))
        if article.get("genreSlug") != "ai" or article["slug"] in posted or article["slug"] in (state.get("ruleBlocked") or {}):
            continue
        if datetime.fromisoformat(article["generatedAt"]) < since:
            continue
        # 速報として出す記事は scripts/breaking_post.py に任せる（二重投稿を防ぐ）
        if reserved_for_breaking(article, state):
            continue
        articles.append(article)

    # まとめ記事に統合済みの個別記事は、まとめ側を出せば十分なので重ねて流さない
    roundups = {a["slug"] for a in articles if a.get("type") == "roundup"} | set(posted)
    articles = [a for a in articles if a.get("roundupSlug") not in roundups]
    articles = [a for a in articles if score(a) >= MIN_SCORE]
    articles.sort(key=lambda a: (score(a), a["generatedAt"]), reverse=True)
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


def free_slots(state: dict, now: datetime | None = None) -> list[datetime]:
    """これから LOOKAHEAD_HOURS 時間以内で、まだ予約していない投稿枠（UTC、早い順）"""
    now = now or datetime.now(timezone.utc)
    taken = [datetime.fromisoformat(p["dueAt"]) for p in state["posted"].values() if p.get("dueAt")]
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


def publish(token: dict, text: str, link: str, article: dict | None = None) -> dict:
    """Threads 公式 API で出す（Buffer が無いときの予備）。出す直前にルールの門番を通す"""
    sns_rules.enforce("threads", text, [], article, source="threads_post.publish")
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


def buffer_graphql(api_key: str, query: str, variables: dict | None = None, gated: bool = False) -> dict:
    if POSTING_MUTATION_RE.search(query) and not gated:
        raise sns_rules.SnsRuleViolation(["投稿を作る・変える Buffer の操作は sns_rules を通した所からだけ呼べる"], "buffer", "buffer_graphql")
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = urllib.request.Request(
        BUFFER_API,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as res:
            payload = json.loads(res.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Buffer API {error.code}: {error.read().decode()[:300]}") from error
    if payload.get("errors"):
        raise RuntimeError(f"Buffer API: {json.dumps(payload['errors'], ensure_ascii=False)[:300]}")
    return payload["data"]


def buffer_channels(api_key: str) -> dict[str, dict]:
    """Buffer につながっているチャンネルを、サービス名（threads / twitter / facebook など）ごとに返す"""
    found: dict[str, dict] = {}
    orgs = buffer_graphql(api_key, "query { account { organizations { id } } }")
    for org in orgs["account"]["organizations"]:
        data = buffer_graphql(
            api_key,
            f"query {{ channels(input: {{ organizationId: {json.dumps(org['id'])} }}) "
            "{ id name displayName service isQueuePaused } }",
        )
        for channel in data["channels"]:
            found.setdefault(str(channel.get("service", "")).lower(), channel)
    return found


def buffer_threads_channel(api_key: str) -> dict | None:
    """Buffer につながっている Threads のチャンネルを探す（IDを手で設定しなくて済むように）"""
    orgs = buffer_graphql(api_key, "query { account { organizations { id } } }")
    for org in orgs["account"]["organizations"]:
        # ID の型名に依存しないよう、値をクエリに直接埋め込む（JSON文字列としてエスケープ済み）
        data = buffer_graphql(
            api_key,
            f"query {{ channels(input: {{ organizationId: {json.dumps(org['id'])} }}) "
            "{ id name displayName service isQueuePaused } }",
        )
        for channel in data["channels"]:
            if str(channel.get("service", "")).lower() == "threads":
                return channel
    return None


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
            options = [[hook, summary, question], [summary, question], [hook, first, question], [first, question]]
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
        speed = speed_line(article) if not article.get("disaster") else ""
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
) -> dict[str, str]:
    """X と Facebook にも同じ記事を出す。時刻は Threads から少しずらす（3つ同時に流れないように）

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
        try:
            try:
                results[service] = create_post(api_key, post_input, service=service, article=article, source="threads_post.publish_extra_channels")
            except sns_rules.SnsRuleViolation:
                raise
            except RuntimeError as error:
                if post_input["mode"] != "shareNow":
                    raise
                log(f"  [{service}] いますぐの投稿が通らないため、{offset}分後の予約にします: {error}")
                post_input["mode"] = "customScheduled"
                post_input["dueAt"] = utc_z(datetime.now(timezone.utc) + timedelta(minutes=max(offset, 1)))
                results[service] = create_post(api_key, post_input, service=service, article=article, source="threads_post.publish_extra_channels")
            log(f"  [{service}] 予約しました: {results[service]}")
        except Exception as error:  # 1つのSNSの失敗で、ほかのSNSの投稿を止めない
            log(f"  [{service}] 失敗: {error}")
    return results


def utc_z(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def create_post(api_key: str, post_input: dict, *, service: str, article: dict | None, source: str, kind: str = "article") -> str:
    """Buffer に投稿を1件作り、投稿IDを返す。作る直前に SNS ルールの門番（sns_rules）を通す。違反なら SnsRuleViolation"""
    sns_rules.enforce_buffer(service, post_input, article, source=source, kind=kind)
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
) -> dict:
    """Buffer に予約投稿する。

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
    return {"id": create_post(api_key, post_input, service="threads", article=article, source="threads_post.publish_via_buffer"), "via": "buffer", "permalink": ""}


def share_now(
    api_key: str, article: dict, text: str, fmt: str = "classic", fb_text: str | None = None, threads_text: str | None = None
) -> dict:
    """いますぐ Threads・X・Facebook に出す（速報用）。

    Buffer の shareNow を使い、通らなければ1分後の予約に切り替える。戻り値は投稿履歴にそのまま入れられる形
    """
    link = f"{site_url()}/articles/{article['slug']}"
    channels = buffer_channels(api_key)
    if "threads" not in channels:
        raise RuntimeError("Buffer に Threads のチャンネルが見つかりません")
    now = datetime.now(timezone.utc)
    due: datetime | None = None
    try:
        result = publish_via_buffer(api_key, channels["threads"]["id"], threads_text or text, link, article, fmt=fmt)
    except sns_rules.SnsRuleViolation:
        raise
    except RuntimeError as error:
        log(f"  [threads] いますぐの投稿が通らないため、1分後の予約にします: {error}")
        due = now + timedelta(minutes=1)
        result = publish_via_buffer(api_key, channels["threads"]["id"], threads_text or text, link, article, due_at=due, fmt=fmt)
    extra = {k: v for k, v in channels.items() if k in ("twitter", "facebook")}
    result["channels"] = publish_extra_channels(
        api_key, extra, text, link, article, due_at=due, offsets=(1, 2), fmt=fmt, fb_text=fb_text
    )
    result["dueAt"] = (due or now).isoformat(timespec="seconds")
    return result


def block_article(state: dict, slug: str, error: Exception) -> None:
    """SNS ルールの門番（sns_rules）が止めた記事を記録する（候補から外す。投稿履歴 posted には入れない）"""
    log(f"  [rules] ルール違反のため出しません: {error}")
    state.setdefault("ruleBlocked", {})[slug] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "violations": list(getattr(error, "violations", []) or [str(error)])[:5],
    }
    save_state(state)


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

    # Buffer がつながっていれば、毎回接続先を確認してログに残す（下書きモードでも）
    buffer_channel = None
    extra_channels: dict[str, dict] = {}
    if buffer_key:
        try:
            all_channels = buffer_channels(buffer_key)
            buffer_channel = all_channels.get("threads")
            extra_channels = {k: v for k, v in all_channels.items() if k in ("twitter", "facebook")}
            if extra_channels:
                log(f"[buffer] ほかの投稿先: {', '.join(extra_channels)}")
        except RuntimeError as error:
            log(f"[warn] Buffer に接続できません: {error}")
        if buffer_channel:
            log(f"[buffer] 投稿先: {buffer_channel.get('displayName') or buffer_channel.get('name')} (Threads)")
        else:
            log("[buffer] Buffer に Threads のチャンネルが見つかりません（Buffer で Threads を接続してください）")
    if args.check_buffer:
        return 0 if buffer_channel else 1
    if args.test_draft:
        # 新しい投稿形式（画像つき本文＋リンクは返信）が Buffer に受け付けられるかを、下書きを作って消すことで確かめる
        if not buffer_channel:
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
    if live and not buffer_channel:
        token = load_token()
        if token is None:
            log("[skip] 投稿経路が未設定です（Buffer の API キー、または scripts/threads_auth.py のトークン）")
            return 0
        # 投稿が無い回でも期限（60日）を延ばし続ける
        token = refresh_if_needed(token)

    # Buffer 経由では、次の自動処理までに来る投稿枠（1日5枠）をまとめて予約する。枠が無ければ見送る
    slots: list[datetime | None] = free_slots(state) if buffer_channel else [None] * args.max
    if not slots:
        log("[done] 直近の投稿枠はすべて予約済みです")
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

    # 枠ごとに1本ずつ選ぶ。直前に使った書き出しの型と同じ型は避ける（同じ型が続くと読み飛ばされるため）
    plan: list[tuple[dict, datetime | None]] = []
    for slot in slots:
        if not pool:
            break
        pick = next((a for a in pool if a.get("threadsHook") not in recent_hooks), pool[0])
        pool.remove(pick)
        if pick.get("threadsHook"):
            recent_hooks = [pick["threadsHook"], *recent_hooks][:3]
        plan.append((pick, slot))
    if not plan:
        log("[done] 投稿できる記事がありません")
        return 0
    for article, slot in plan:
        if slot:
            log(f"[slot] {slot.astimezone(JST):%m/%d %H:%M} に予約: {article['slug']}")

    summary = [f"### Threads {'投稿' if live else '下書き（未投稿）'}", ""]
    # 直近の投稿で使った書き出しの型（同じ型を続けないため）
    history = sorted(state["posted"].values(), key=lambda p: p.get("postedAt", ""), reverse=True)
    recent = [p["hook"] for p in history if p.get("hook")][:4]
    recent_comments, recent_questions = recent_voice(state, "comment"), recent_voice(state, "questionText")
    for offset, (article, due_at) in enumerate(plan):
        link = f"{site_url()}/articles/{article['slug']}"
        # 下書きモードでは履歴が増えないので、何本目かを足して交互にする（本番は保存済みの履歴で数える）
        # 公式 API の経路（返信を作れない）は従来の形式のまま
        fmt = choose_format(state, article, breaking=False, offset=0 if live else offset) if buffer_channel or not live else "classic"
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
            if buffer_channel:
                result = publish_via_buffer(buffer_key, buffer_channel["id"], threads_text, link, article, due_at, fmt=fmt)
                if extra_channels:
                    result["channels"] = publish_extra_channels(
                        buffer_key, extra_channels, text, link, article, due_at, fmt=fmt, fb_text=classic_text
                    )
            else:
                result = publish(token, threads_text, link, article)
        except sns_rules.SnsRuleViolation as error:
            # ルールの門番が止めた記事は、次の回で同じ違反を繰り返さないよう記録して選ばない（直したら ruleBlocked から消す）
            block_article(state, article["slug"], error)
            continue
        except RuntimeError as error:
            # 1本の失敗でパイプライン全体を止めない。未投稿のまま次回再挑戦する
            log(f"  [error] {error}")
            continue
        state["posted"][article["slug"]] = {
            **result,
            "hook": hook,
            "score": score(article),
            "style": "standalone",
            "image": "card" if fmt == "classic" else "photo" if post_image(article, fmt) != f"{site_url()}/cover/{article['slug']}" else "cover",
            "format": fmt,
            **({"hookTextVersion": HOOK_TEXT_VERSION} if fmt == "hook_thread" else {}),
            "topic": topic_of(article),
            "question": bool(question),
            **({"questionText": question} if question else {}),
            **({"comment": comment} if comment else {}),
            **({"dueAt": due_at.isoformat(timespec="seconds")} if due_at and buffer_channel else {}),
            "postedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        save_state(state)
        log(f"  [posted] {result['permalink'] or result['id']}")

    write_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
