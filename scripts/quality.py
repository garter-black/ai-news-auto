#!/usr/bin/env python3
"""生成した記事を書き出す前の品質ゲート。

  (a) 形のチェック（API 不要）: 空欄・かんたん版・タイトル長・英語の残骸・元記事の丸写し
  (b) 事実確認（LLM・bulk）: 記事と元記事を突き合わせ {grounded, score, problems} を返させる
      grounded が false か score < 70 なら不合格

  (c) 読みやすさ（測るのは API 不要）: 文・段落の長さ。形と事実が合格で読みやすさだけ基準外なら「やわらかい不合格」。
      polish() で本文だけ文を分ける書き直しを1回頼み（数値を添えて）、見出し・数字・長さが保たれ再確認も通れば差し替える。
      だめなら元の原稿のまま受理して数値をログに残す（読みやすさだけで記事を落とさない）

web_articles.py は不合格のとき problems を添えて1回だけ作り直し、それでもだめなら諦める。
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import japan_availability  # noqa: E402
import llm  # noqa: E402
import persona  # noqa: E402

TITLE_RANGE = (18, 64)
PASS_SCORE = 70
# 事実確認・文の書き直しに使うモデルの段（速報レーンは待たせないよう quality にする。web_articles.py が切り替える）
CHECK_TIER = "bulk"
# 大きなニュース（速報レーン・主要企業の新モデルなど）を確認しているか（web_articles.py が切り替える）。
# True のとき: 細かい指摘は外科的に直して公開する（落とさない）
MAJOR = False
# 外科的な修正に回す・修正後に受理する事実確認の点数（致命的な誤り＝fatal が無いときだけ）
SURGICAL_MIN_SCORE = 75
# 事実確認に渡す元記事の長さ。生成に渡す長さ（feeds.TEXT_CAP = 12000）＋元記事の情報の見出しより短いと、
# 元記事の後半にある事実（2026-10-28 の Mithril の買収は 9400 字目）を「創作」と誤判定するので、それより長くする
SOURCE_CAP = 13000
ARTICLE_CAP = 9000

BOILERPLATE_RE = re.compile(
    r"\b(Read more|Learn more|Subscribe|Sign up|Sign in|Cookie|Privacy Policy|Terms of (Use|Service)|"
    r"All rights reserved|Share this|Click here|Skip to (main )?content|Table of contents|Back to top|"
    r"Related (posts|articles)|Loading\.\.\.)\b",
    re.IGNORECASE,
)
# 英単語が12語以上続く＝訳し忘れ・コピペの残骸（製品名程度なら12語には届かない）
ENGLISH_RUN_RE = re.compile(r"(?:\b[A-Za-z][A-Za-z'’\-]*\b[\s,;:]+){11,}\b[A-Za-z][A-Za-z'’\-]*\b")
# 文章ソースなのに「動画」前提の言い回しが残っている
VIDEO_LEFTOVER_RE = re.compile(r"動画(内|では|の中で|で示|で紹介|で語ら|の冒頭)|映像では|スライドでは|音声では")

# サイソクが自分で試したように読める書き方（自社の検証データ ownTest が無い記事では不可）。
# 「筆者」は元記事（他社）の書き手のことが多く、サイソクの体験に読めるので使わない（報道が元なら「〇〇の記者」と書く）
FIRST_PERSON_TEST_RE = re.compile(
    r"筆者|"
    r"(?:私たち|私|我々|われわれ|編集部|サイソク(?:編集部)?)(?:が|で|の|も|は)[^。\n]{0,12}?"
    r"(?:試し|テスト|検証し|使ってみ|触ってみ|触った|実機で|計測し|測定し)|"
    r"実際に(?:試して|使って|触って)みた(?:ところ|結果|感想)|手元の(?:環境|PC|パソコン|端末|スマホ)で(?:試|確認|計測)"
)
# FAQ の答えが「分からない」だけのもの（質問として出さない）
FAQ_UNANSWERED_RE = re.compile(r"^(?:元記事(?:では|には)|現時点では|発表では)?[^。]{0,20}(?:書かれていません|言及(?:は)?ありません|記載(?:は)?ありません|分かりません|不明です)")
# 題名の約束（題名にこの言葉があれば、本文に答えが要る）
PROMISE_JAPAN_RE = re.compile(r"日本で(?:も)?使え|日本での(?:使い方|提供|利用)|日本から使|日本(?:で|でも)(?:提供|発売|利用)")
PROMISE_PRICE_RE = re.compile(r"料金|価格|値段|いくら|月額|年額|費用")
PRICE_ANSWER_RE = re.compile(r"\d[\d,.]*\s*(?:円|ドル|万円|億円|ユーロ)|[$＄￥¥]\s?\d|無料|月\d|税込|税別")
PROMISE_HOWTO_RE = re.compile(r"使い方|やり方|手順|設定方法|始め方|申し込み方|申請方法")
HOWTO_ANSWER_RE = re.compile(r"(?m)^\s*(?:\d+[.)．]|[-*・])\s+\S|^#{2,3}\s.*(?:使い方|手順|方法|始め方|やり方|申し込|申請)")
PROMISE_WHEN_RE = re.compile(r"いつから|開始日|発売日|いつ始まる")
WHEN_ANSWER_RE = re.compile(r"\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月|\d{1,2}月(?:上旬|中旬|下旬|中|から)|未定|発表されていません")


def promise_problems(record: dict) -> list[str]:
    """題名・短い題名で約束したこと（日本で使える？／料金／使い方／いつから）に、本文が答えているか（API 不要）"""
    title = f"{record.get('title') or ''} {record.get('seoTitle') or ''}"
    body = record.get("body") or ""
    problems = []
    if PROMISE_JAPAN_RE.search(title):
        heading = japan_availability.JAPAN_HEADING_RE.search(body)
        first = ""
        if heading:
            first = body[heading.end():].strip().split("\n")[0].split("。")[0]
        if heading and japan_availability.UNKNOWN_ANSWER_RE.search(first):
            problems.append(
                "題名で「日本で使えるか」を約束しているのに、本文の「日本で使える？」が「分からない」で始まっている。"
                "「当サイトで確認済み」の欄に答えがあればそれで答え、無ければ題名から日本の話を外す"
            )
        elif "日本" not in body:
            problems.append("題名で「日本で使えるか」を約束しているのに、本文に日本での提供・使い方の説明が無い。答えるか、題名から外す")
    if PROMISE_PRICE_RE.search(title) and not PRICE_ANSWER_RE.search(body):
        problems.append("題名で料金・価格を約束しているのに、本文に金額（円・ドル・無料など）が無い。元記事の金額を書くか、題名から料金の話を外す")
    if PROMISE_HOWTO_RE.search(title) and not HOWTO_ANSWER_RE.search(body):
        problems.append("題名で使い方・手順を約束しているのに、本文に手順（番号つきの手順や「使い方」の章）が無い。手順を書くか、題名から外す")
    if PROMISE_WHEN_RE.search(title) and not WHEN_ANSWER_RE.search(body):
        problems.append("題名で「いつから」を約束しているのに、本文に日付が無い。元記事の日付を書くか、題名から外す")
    return problems


def first_person_problems(record: dict) -> list[str]:
    """サイソクが自分で試したように読める書き方。自社の検証データ（ownTest）がある記事だけ許す"""
    if record.get("ownTest"):
        return []
    text = "\n".join(str(record.get(k) or "") for k in ("title", "lede", "body", "easy")) + "\n" + "\n".join(record.get("highlights") or [])
    m = FIRST_PERSON_TEST_RE.search(text)
    if not m:
        return []
    return [
        f"サイソクが自分で試したように読める書き方がある（「{m.group(0)[:20]}」）。サイソクは試していない。"
        "元記事の書き手が試した結果なら「〇〇（媒体名）の記者が試したところ」と誰が試したかを書く"
    ]


def faq_problems(record: dict) -> list[str]:
    """よくある質問・今日やることの形と禁止表現（API 不要）"""
    problems = []
    faq = record.get("faq") or []
    if faq and not 3 <= len(faq) <= 5:
        problems.append(f"faq は3〜5問（{len(faq)}問）")
    for row in faq:
        if not str(row.get("q", "")).rstrip().endswith(("？", "?")):
            problems.append(f"faq の質問が疑問文になっていない（「{str(row.get('q', ''))[:20]}」）")
        if m := persona.ADVICE_RE.search(str(row.get("a", ""))):
            problems.append(f"faq の答えに儲け話・投資助言の表現がある（「{m.group(0)}」）")
    actions = record.get("todayActions") or []
    if actions and not 2 <= len(actions) <= 3:
        problems.append(f"今日やること（today_actions）は2〜3個（{len(actions)}個）")
    for a in actions:
        if m := persona.ADVICE_RE.search(a):
            problems.append(f"今日やることに儲け話・投資助言の表現がある（「{m.group(0)}」）")
    return problems


FACT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "grounded": {"type": "BOOLEAN"},
        "score": {"type": "INTEGER"},
        "problems": {"type": "ARRAY", "items": {"type": "STRING"}},
        "fatal": {"type": "BOOLEAN"},
    },
    "required": ["grounded", "score", "problems"],
    "propertyOrdering": ["grounded", "score", "problems", "fatal"],
}

# 新しい Web 記事は事実確認と同じ呼び出しで読者への有用性も確認する。
# followup / evergreen が使う既存の FACT_SCHEMA は変更しない。
WEB_FACT_SCHEMA = {
    **FACT_SCHEMA,
    "properties": {**FACT_SCHEMA["properties"], "useful": {"type": "BOOLEAN"}},
    "required": ["grounded", "score", "problems", "fatal", "useful"],
    "propertyOrdering": ["grounded", "score", "problems", "fatal", "useful"],
}

FACT_SYSTEM = """あなたは報道機関の校閲記者です。AIが書いた記事を、元記事（一次情報）と1文ずつ突き合わせて事実確認します。
判定は厳格に、しかし公平に行ってください。出力は指定の JSON だけ。"""

FACT_PROMPT = """# 元記事（一次情報。自動抽出なのでメニュー等の残骸は無視）
<<<
{source}
>>>
{context}

# 確認する記事
<<<
{article}
>>>

# 確認のしかた
- 記事にある数値・日付・価格・固有名詞・発言・対象者・提供状況が、元記事に書かれているか、元記事と矛盾しないかを確かめる。
- 次は問題にしない: 元記事の内容の言い換え・要約、「確認済みの文脈」に書かれた事実（過去の経緯・日付・価格など）、「（編集部注）」と明記した一般的な背景説明（具体的な数値を含まないもの）、
  「元記事では言及なし」「発表されていない」といった記述、読者への一般的な助言（公式情報を確認しよう等）。
- 「[元記事の情報]」の発信元・公開日時に基づく記述（「〇〇の公式ブログによると」「10月26日に発表」など）は元記事に書かれた事実として扱う。
  米国時間と日本時間の時差による1日の日付のずれも問題にしない。
- 広く知られた一般常識・定訳は創作として扱わない（例: SEC＝米証券取引委員会、IPO（上場）前の会社は株式を公開していない、Nasdaq は米国の株式市場）。
- 次は必ず問題にする: 元記事に無い具体的な数値・日付・価格・機能・発言の創作、元記事との矛盾、
  予定や研究段階のものを提供開始済み・実用化済みのように書くこと、効能・効果の断定、特定個人への憶測。
- 「よくある質問」の答えと「今日やること」も記事の一部として同じ基準で確かめる。答えに元記事・確認済みの文脈に無い数字・条件・提供状況があれば必ず問題にする。
  今日やることで、元記事に無い画面の場所・ボタン名・手順を作っていれば問題にする（「公式の設定画面で確認する」のような一般的な行動はよい）。
- 「仕事・お金にどう活かせる」の欄も記事の一部として同じ基準で確かめる。誰に関係するか・明日からできる行動の提案は
  一般的な助言として扱ってよいが、元記事に無い金額・節約額・短縮時間・収入の見込み（自分で計算した数字を含む）、
  提供前のものを今すぐ使えるように書くこと、特定の銘柄・ファンド・物件の売買のすすめ・相場の予想・儲かるといった断定は必ず問題にする。

# 出力
- grounded: 創作・矛盾が1つも無ければ true。1つでもあれば false。
- score: 記事全体が元記事に裏付けられている度合い（0〜100）。軽微な言い過ぎは減点、創作・矛盾は大きく減点。
- problems: 見つけた問題を最大8個。各項目は「記事の該当箇所（短く引用）→ 何が問題か」を1文で。問題が無ければ空配列。
- fatal: 数値・日付・価格・固有名詞・発表内容（何が・いつ・誰に提供されるか）の取り違えや創作が1つでもあれば true。
  言い過ぎ・訳語の選び方・言い回し・補足説明の程度の問題だけなら false。"""

WEB_USEFULNESS_PROMPT = """

# 読者にとっての有用性も確認する
- 文字数や特定の章・表の有無では判定しない。短い速報でも十分に答えていればよい。
- 見出しが約束した内容について、何が起きたかと、元情報から確認できる対象・条件・読者との関係など、理解に必要な具体的事実があるか確認する。
- 大事な条件が元資料にない場合、無理に補わず不明と区別できているかを見る。根拠のない体験談・比較・推測や誇大な断定は不可。
- 編集部の見方は、確認できた事実を根拠とし、事実と区別した解釈であればよい。
- useful: 見出しの疑問に答え、読者が概要を理解できる具体性があれば true。それ以外は false とし、problems に不足を記す。
"""


CONTEXT_BLOCK = """
# 確認済みの文脈（サイソクの過去記事・既存記事の事実。ここにある事実は創作として扱わない）
<<<
{context}
>>>
"""
# 読みやすさの基準（Terra の書き直し: 平均33字・60字超6%・段落48字）
READ_MAX_AVG_SENTENCE = 45
READ_LONG_SENTENCE = 60
READ_MAX_LONG_SHARE = 0.20
READ_MAX_AVG_PARAGRAPH = 120
READ_MAX_PARAGRAPH = 220
# 段落に数えない行: 見出し・表・箇条書き・引用・コード
_NON_PROSE_RE = re.compile(r"^\s*(#|\||[-*+]\s|\d+[.)]\s|>|```|出典[:：])")
_SENTENCE_END_RE = re.compile(r"(?<=[。！？!?])(?![」』）)\]])")


def _plain_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _norm(text: str) -> str:
    return re.sub(r"[\s　、。，．,.・「」『』()（）\-—:：]", "", unicodedata.normalize("NFKC", text or ""))


def copied_sentences(body: str, source: str, min_len: int = 40) -> list[str]:
    """元記事とほぼ同じ文（記号・空白を無視して40字以上一致）"""
    src = _norm(source)
    if not src:
        return []
    found = []
    for sentence in re.split(r"(?<=[。！？])|\n", body or ""):
        clean = _norm(re.sub(r"[#>*|`]", "", sentence))
        if len(clean) >= min_len and clean in src:
            found.append(sentence.strip()[:50])
    return found


def _strip_inline(text: str) -> str:
    """リンク・強調・コードの記号を外した地の文"""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"(\*\*|__|\*|<br\s*/?>)", "", text)
    return re.sub(r"\s+", "", text)


def paragraphs(body: str) -> list[str]:
    """地の文の段落（見出し・表・箇条書き・引用・コードを除く）。空行か地の文以外の行で区切る"""
    found: list[str] = []
    current: list[str] = []
    in_code = False
    for line in (body or "").splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if in_code or not line.strip() or _NON_PROSE_RE.match(line):
            if current:
                found.append("".join(current))
                current = []
            continue
        current.append(_strip_inline(line))
    if current:
        found.append("".join(current))
    return [p for p in found if p]


def readability(body: str) -> dict:
    """文・段落の長さ（決定的。LLM なし）"""
    paras = paragraphs(body)
    sentences = [s for p in paras for s in _SENTENCE_END_RE.split(p) if len(s) >= 2]
    lengths = [len(s) for s in sentences]
    para_lengths = [len(p) for p in paras]
    long_count = sum(1 for n in lengths if n > READ_LONG_SENTENCE)
    return {
        "sentences": len(lengths),
        "avg_sentence": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
        "long_share": round(long_count / len(lengths), 3) if lengths else 0.0,
        "paragraphs": len(para_lengths),
        "avg_paragraph": round(sum(para_lengths) / len(para_lengths), 1) if para_lengths else 0.0,
        "max_paragraph": max(para_lengths, default=0),
    }


def readability_problems(body: str, stats: dict | None = None) -> list[str]:
    """読みやすさの基準外。作り直しの指示に使えるよう、数字と直し方を書く"""
    r = stats or readability(body)
    if not r["sentences"]:
        return []
    problems = []
    if r["avg_sentence"] > READ_MAX_AVG_SENTENCE or r["long_share"] > READ_MAX_LONG_SHARE:
        problems.append(
            f"文が長い（1文の平均 {r['avg_sentence']}字・{READ_LONG_SENTENCE}字超の文が {r['long_share']:.0%} / "
            f"基準 平均{READ_MAX_AVG_SENTENCE}字以下・{READ_LONG_SENTENCE}字超{READ_MAX_LONG_SHARE:.0%}以下）。"
            "1文は50字以内を目安に2文に分け、1文に言いたいことは1つにする（内容と文字数は減らさない）"
        )
    if r["avg_paragraph"] > READ_MAX_AVG_PARAGRAPH or r["max_paragraph"] > READ_MAX_PARAGRAPH:
        problems.append(
            f"段落が長い（平均 {r['avg_paragraph']}字・最長 {r['max_paragraph']}字 / "
            f"基準 平均{READ_MAX_AVG_PARAGRAPH}字以下・最長{READ_MAX_PARAGRAPH}字以下）。"
            "1段落は1〜3文にし、数字が3つ以上並ぶところは箇条書きにする（内容と文字数は減らさない）"
        )
    return problems


def structural_problems(record: dict, kind: str, source_text: str = "") -> list[str]:
    """形のチェック。問題のリスト（空なら合格）"""
    problems: list[str] = []
    body = record.get("body") or ""
    easy = (record.get("easy") or "").strip()
    title = record.get("title") or ""

    if not re.sub(r"(?m)^\s*#{1,6}.*$|\s", "", body):
        problems.append("本文が空です")
    if not re.sub(r"(?m)^\s*#{1,6}.*$|\s", "", easy):
        problems.append("easy_markdown に本文がありません")
    if not easy.startswith(("## ひとことで言うと", "## 概要")):
        problems.append("easy_markdown が「## ひとことで言うと」から始まっていない")
    if not TITLE_RANGE[0] <= len(title) <= TITLE_RANGE[1]:
        problems.append(f"title の長さが不適切（{len(title)}字 / {TITLE_RANGE[0]}〜{TITLE_RANGE[1]}字）")
    if re.search(r"[！!]", f"{title}{record.get('seoTitle') or ''}"):
        problems.append("title / seo_title に感嘆符がある")
    text = f"{body}\n{easy}"
    if m := BOILERPLATE_RE.search(text):
        problems.append(f"Webページの英語の残骸が残っている（「{m.group(0)}」）")
    if m := ENGLISH_RUN_RE.search(re.sub(r"`[^`]*`|\[[^\]]*\]\([^)]*\)", " ", text)):
        problems.append(f"訳されていない英文が残っている（「{m.group(0)[:40]}…」）")
    if record.get("sourceType") == "web" and (m := VIDEO_LEFTOVER_RE.search(text)):
        problems.append(f"文章が元なのに動画前提の表現がある（「{m.group(0)}」）。「元記事」と書く")
    if record.get("sourceType") == "web" and kind != "update" and not record.get("workMoney"):
        problems.append(f"work_money（「{persona.WORK_MONEY_HEADING}」欄）がありません")
    problems += persona.work_money_problems(record.get("workMoney"))
    if record.get("sourceType") == "web" and kind != "update":
        problems += first_person_problems(record)
        problems += promise_problems(record)
        problems += faq_problems(record)
        if m := persona.OLD_VIEWPOINT_RE.search(body):
            problems.append(f"古い立場の小見出し（「{m.group(0).lstrip('# ')}」）がある。立場は「" + "」「".join(persona.VIEWPOINTS) + "」の3つにする")
    if source_text:
        copied = copied_sentences(body, source_text)
        if len(copied) >= 2:
            problems.append(f"元記事の文をそのまま書き写している（{len(copied)}文。例「{copied[0]}」）。自分の言葉で要約する")
    return problems


def _article_for_check(record: dict) -> str:
    parts = [
        f"タイトル: {record.get('title', '')}",
        f"リード: {record.get('lede', '')}",
        "要点: " + " / ".join(record.get("highlights") or []),
    ]
    work_money = record.get("workMoney") or {}
    if work_money.get("points"):
        # 本文より前に置く（ARTICLE_CAP で切られても必ず事実確認に入るように）
        parts.append(f"{persona.WORK_MONEY_HEADING}: " + " / ".join(work_money["points"]))
    if record.get("faq"):
        # 本文より前に置く（ARTICLE_CAP で切られても事実確認に入るように）
        parts.append("よくある質問:\n" + "\n".join(f"Q. {r.get('q', '')} A. {r.get('a', '')}" for r in record["faq"]))
    if record.get("todayActions"):
        parts.append("今日やること: " + " / ".join(record["todayActions"]))
    parts += [
        f"本文:\n{record.get('body', '')}",
        f"かんたん版:\n{record.get('easy', '')}",
    ]
    verdict = record.get("verdict")
    if verdict:
        parts.append(f"判定: 提供状況={verdict.get('status')} 価格={verdict.get('price')} ひとこと={verdict.get('take')}")
    return "\n\n".join(parts)[:ARTICLE_CAP]


def fact_check(record: dict, source_text: str, context: str = "", require_usefulness: bool = False) -> dict:
    """{grounded, score, problems, model}。context は確認済みの事実（過去記事など）。LLM が使えないときは例外"""
    prompt = FACT_PROMPT.format(
        source=source_text[:SOURCE_CAP],
        context=CONTEXT_BLOCK.format(context=context[:3000]) if context.strip() else "",
        article=_article_for_check(record),
    )
    if require_usefulness:
        prompt += WEB_USEFULNESS_PROMPT
    data, model = llm.generate_json(FACT_SYSTEM, prompt, WEB_FACT_SCHEMA if require_usefulness else FACT_SCHEMA,
                                    temperature=0.1, max_output_tokens=4096, tier=CHECK_TIER)
    try:
        score = int(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    problems = [str(p).strip() for p in (data.get("problems") or []) if str(p).strip()][:8]
    grounded = data.get("grounded") is True
    # fatal が無い（古い形の応答）ときは、不合格を致命的とみなす（緩める方向に倒さない）
    fatal = data.get("fatal") is not False if "fatal" in data else not grounded
    result = {"grounded": grounded, "score": max(0, min(100, score)), "problems": problems, "fatal": fatal, "model": model}
    if require_usefulness:
        result["useful"] = data.get("useful") is True
    return result


VIDEO_FIX = {"映像では": "元記事では", "スライドでは": "元記事では", "音声では": "元記事では"}


def fix_video_leftover(record: dict) -> None:
    """文章が元の記事に残った「動画では」などを「元記事では」に機械的に直す（作り直しで無料枠・時間を使わない。
    2026-10-28 の速報3本がこれで1回ずつ作り直しになった）。record をその場で書き換える"""
    if record.get("sourceType") != "web":
        return
    for key in ("body", "easy", "lede"):
        text = record.get(key)
        if not isinstance(text, str) or not VIDEO_LEFTOVER_RE.search(text):
            continue
        text = re.sub(r"動画(内|では|の中で|で示|で紹介|で語ら|の冒頭)", r"元記事\1", text)
        for a, b in VIDEO_FIX.items():
            text = text.replace(a, b)
        record[key] = text


def evaluate(record: dict, source_text: str, kind: str, context: str = "", check_facts: bool = True) -> dict:
    """{ok, soft, score, problems, structural, fact, readability, readability_problems}。形で落ちたときは事実確認を省く（API の節約）。
    soft=True は「形と事実は合格、読みやすさだけが基準外」。polish() で文を分け、だめでも受理してよい。
    形・事実の不合格のときの problems には読みやすさを混ぜない（作り直しで本文が縮み、長さで落ちるのを防ぐ）"""
    fix_video_leftover(record)
    read = readability(record.get("body") or "")
    read_problems = readability_problems("", read)
    base = {"structural": [], "fact": None, "readability": read, "readability_problems": read_problems}
    structural = structural_problems(record, kind, source_text)
    if record.get("sourceType") == "web" and not (source_text or "").strip():
        structural.append("一次資料が空のため記事の内容を確認できません")
    if structural:
        return {**base, "ok": False, "soft": False, "score": None, "problems": structural, "structural": structural}
    if not check_facts:
        return {**base, "ok": not read_problems, "soft": bool(read_problems), "score": None, "problems": read_problems}
    try:
        fact = fact_check(record, source_text, context, require_usefulness=True) if record.get("sourceType") == "web" else fact_check(record, source_text, context)
    except Exception as error:  # noqa: BLE001 - 確認できない記事は出さない
        problem = f"事実確認を実行できなかった（{str(error)[:80]}）"
        return {**base, "ok": False, "soft": False, "score": None, "problems": [problem], "unavailable": True}
    fact_ok = fact["grounded"] and fact["score"] >= PASS_SCORE and fact.get("useful", True)
    problems = fact["problems"] if not fact_ok else read_problems
    if not fact_ok and not problems:
        problems = ["記事の有用性を確認できなかった"] if fact.get("useful") is False else [f"元記事との整合性が低い（score {fact['score']}）"]
    return {**base, "ok": fact_ok and not read_problems, "soft": fact_ok and bool(read_problems), "score": fact["score"],
            "problems": problems, "fact": fact}


POLISH_SCHEMA = {
    "type": "OBJECT",
    "properties": {"body_markdown": {"type": "STRING"}},
    "required": ["body_markdown"],
}

POLISH_SYSTEM = """あなたはニュースサイトの編集者です。記者の原稿を、中身を変えずにスマホで読みやすい文に整えます。
出力は指定の JSON だけ。"""

POLISH_PROMPT = """次の記事本文（Markdown）を、中身を変えずに読みやすく整えてください。

# 今の原稿の数値
{problems}

# 直し方
- 1文は50字以内を目安にする。長い文は2〜3文に分ける。1文に言いたいことは1つ。読点（、）で文をつながない。
  例:「AはBを発表し、Cができるようになったことで、Dの手間が減ると期待されています。」
  →「AはBを発表しました。これでCができるようになります。Dの手間が減りそうです。」
- 1段落は1〜3文。段落の間には空行を入れる。
- 文中に数字が3つ以上並ぶところは箇条書きにする。
- 飾りの誇張語（圧倒的・極限・劇的・強烈 など）は普通の言葉にする。

# 変えてはいけないもの
- 見出し（## と ###）の文言・数・順番。表・リンク・既存の箇条書き。
- 事実・数字・日付・価格・固有名詞・引用。情報を削らない。足さない。
- 文字数はほぼ同じに保つ（{length}字前後。短くしない）。

# 原稿
<<<
{body}
>>>"""

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_HEADING_RE = re.compile(r"^#{2,3} .+$", re.M)
_PROTECTED_HEADING_RE = re.compile(r"^## (概要|ひとことで言うと|自分に関係ある？|まとめ|これまでの流れ|続報)")


def _numbers(text: str) -> set[str]:
    """本文中の数字（リンク先と番号付きリストの番号は除く）"""
    text = re.sub(r"\]\([^)]*\)", "]", text or "")
    text = re.sub(r"^\s*\d+[.)]\s", "", text, flags=re.M)
    return set(_NUM_RE.findall(unicodedata.normalize("NFKC", text)))


def polish(record: dict, source_text: str, kind: str, context: str = "", check_facts: bool = True):
    """読みやすさだけ基準外の記事の本文を、文を分けるだけの書き直しにかける。
    (record, verdict) を返す。見出し・数字・長さが崩れたり、事実確認に落ちたりしたら None"""
    body = record.get("body") or ""
    length = _plain_len(body)
    problems = readability_problems(body)
    prompt = POLISH_PROMPT.format(problems="\n".join(f"- {p}" for p in problems), length=length, body=body)
    data, model = llm.generate_json(POLISH_SYSTEM, prompt, POLISH_SCHEMA, temperature=0.3, max_output_tokens=16384, tier=CHECK_TIER)
    new_body = re.sub(r"^(#{1,6}) +(?:#{1,6} +)+", r"\1 ", str(data.get("body_markdown") or "").strip(), flags=re.M)
    # 見出しは数と階層を保つ。言い回しの手直し（誇張語を外す等）は許すが、決まった章名は変えさせない
    heads_old, heads_new = _HEADING_RE.findall(body), _HEADING_RE.findall(new_body)
    if [h.split(" ")[0] for h in heads_old] != [h.split(" ")[0] for h in heads_new]:
        return None, f"見出しの数・階層が変わった（{len(heads_old)}→{len(heads_new)}個）"
    for a, b in zip(heads_old, heads_new):
        if _PROTECTED_HEADING_RE.match(a) and _norm(a) != _norm(b):
            return None, f"決まった章名が変わった（{a}→{b}）"[:80]
    if _plain_len(new_body) < length * 0.9:
        return None, f"本文が縮んだ（{length}→{_plain_len(new_body)}字）"
    # 「3つあります」のような1桁の数は箇条書きにするときに増えるので見逃す（事実確認は後で通す）
    added = {n for n in _numbers(new_body) - _numbers(body) if len(n) > 1}
    if added:
        return None, f"元に無い数字が増えた（{', '.join(sorted(added)[:5])}）"
    if len(_numbers(body) - _numbers(new_body)) > 2:
        return None, "数字が抜けた"
    polished = {**record, "body": new_body}
    verdict = evaluate(polished, source_text, kind, context, check_facts=check_facts)
    if not (verdict["ok"] or verdict.get("soft")):
        return None, "書き直し後の確認に不合格: " + "; ".join(verdict["problems"][:2])
    before, after = readability(body), verdict["readability"]
    improved = (
        len(verdict["readability_problems"]) < len(problems)
        or after["avg_sentence"] <= before["avg_sentence"] - 3
        or after["avg_paragraph"] <= before["avg_paragraph"] * 0.85
    )
    if not improved:
        return None, f"読みやすさが改善しなかった（{before['avg_sentence']}→{after['avg_sentence']}字）"
    verdict["polished_by"] = model
    return polished, verdict


SURGICAL_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "lede": {"type": "STRING"},
        "highlights": {"type": "ARRAY", "items": {"type": "STRING"}},
        "body_markdown": {"type": "STRING"},
        "easy_markdown": {"type": "STRING"},
        "work_money_points": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["title", "lede", "highlights", "body_markdown", "easy_markdown", "work_money_points"],
}

SURGICAL_SYSTEM = """あなたはニュースサイトのデスクです。校閲で指摘された箇所だけを、最小限の手直しで直します。
出力は指定の JSON だけ。"""

SURGICAL_PROMPT = """校閲記者が次の記事に指摘をしました。指摘された箇所だけを直してください。

# 校閲の指摘
{problems}

# 直し方
- 指摘された文（または文の一部）だけを直す。元記事に無い事実・言い過ぎは削るか、元記事どおりの弱い言い方に直す。
  訳語の指摘は正しい訳語に直す。直せないときはその文を削る。
- 指摘されていない文・見出し（## と ###）・表・リンク・数字は一字も変えない。
- 新しい事実・数字・日付・固有名詞を足さない。
- タイトル・リード・要点（highlights）・かんたん版・「仕事・お金にどう活かせる」（work_money_points）も、指摘があればそこだけ直し、
  なければそのまま返す（元の記事に無ければ空配列）。直せない項目は削る。

# 元記事
<<<
{source}
>>>

# 記事
タイトル: {title}
リード: {lede}
要点: {highlights}
仕事・お金にどう活かせる: {work_money}

本文:
<<<
{body}
>>>

かんたん版:
<<<
{easy}
>>>"""


def surgical_ok(verdict: dict) -> bool:
    """外科的な修正に回せる不合格か（形のチェックは通り、事実確認が致命的でなく SURGICAL_MIN_SCORE 点以上）"""
    fact = verdict.get("fact") or {}
    return bool(fact) and not verdict.get("structural") and not fact.get("fatal") and fact.get("useful", True) and fact.get("score", 0) >= SURGICAL_MIN_SCORE


def surgical_fix(record: dict, source_text: str, kind: str, problems: list[str], context: str = "", check_facts: bool = True):
    """大きなニュースの細かい指摘（言い過ぎ・訳語・補足の程度）を、指摘された文だけ直して受理する。
    (record, verdict) か (None, 理由)。直したあとも致命的でなく SURGICAL_MIN_SCORE 点以上なら、残った細かい指摘は受理する"""
    body = record.get("body") or ""
    prompt = SURGICAL_PROMPT.format(
        problems="\n".join(f"- {p}" for p in problems[:8]), source=(source_text or "")[:SOURCE_CAP],
        title=record.get("title", ""), lede=record.get("lede", ""), highlights=" / ".join(record.get("highlights") or []),
        work_money=" / ".join((record.get("workMoney") or {}).get("points") or []) or "（なし）",
        body=body, easy=record.get("easy", ""),
    )
    data, model = llm.generate_json(SURGICAL_SYSTEM, prompt, SURGICAL_SCHEMA, temperature=0.2, max_output_tokens=16384, tier=CHECK_TIER)
    new_body = re.sub(r"^(#{1,6}) +(?:#{1,6} +)+", r"\1 ", str(data.get("body_markdown") or "").strip(), flags=re.M)
    heads_old, heads_new = _HEADING_RE.findall(body), _HEADING_RE.findall(new_body)
    if [_norm(h) for h in heads_old] != [_norm(h) for h in heads_new]:
        return None, f"見出しが変わった（{len(heads_old)}→{len(heads_new)}個）"
    if _plain_len(new_body) < _plain_len(body) * 0.85:
        return None, f"本文を削りすぎた（{_plain_len(body)}→{_plain_len(new_body)}字）"
    added = {n for n in _numbers(new_body) - _numbers(body) - _numbers(source_text) if len(n) > 1}
    if added:
        return None, f"元に無い数字が増えた（{', '.join(sorted(added)[:5])}）"
    fixed = {**record, "body": new_body}
    for key, field in (("title", "title"), ("lede", "lede"), ("easy_markdown", "easy")):
        value = str(data.get(key) or "").strip()
        if value:
            fixed[field] = value
    highlights = [str(h).strip() for h in data.get("highlights") or [] if str(h).strip()]
    if highlights:
        fixed["highlights"] = highlights[:3]
    if record.get("workMoney"):
        # 指摘で全部削られたら欄ごと無くなり、形のチェックで不合格になる（緩めない）
        points = [str(p).strip() for p in data.get("work_money_points") or [] if str(p).strip()]
        fixed["workMoney"] = persona.clean_work_money({**record["workMoney"], "points": points})
    verdict = evaluate(fixed, source_text, kind, context, check_facts=check_facts)
    verdict["fixed_by"] = model
    if verdict["ok"] or verdict.get("soft"):
        return fixed, verdict
    if MAJOR and surgical_ok(verdict):
        # 残りは細かい指摘だけ（致命的な誤りなし・点数も基準以上）。大きなニュースは落とさない
        return fixed, {**verdict, "ok": True, "accepted_minor": verdict.get("problems") or []}
    return None, "直したあとの確認に不合格: " + "; ".join(verdict.get("problems", [])[:2])


def feedback_block(problems: list[str]) -> str:
    """作り直しのときにプロンプト末尾へ足す指示"""
    lines = "\n".join(f"- {p}" for p in problems[:10])
    return (
        "\n\n# 前回の原稿で見つかった問題（必ず直すこと）\n"
        f"{lines}\n"
        "- 元記事に無い数値・日付・価格・発言は削る。足りない分は元記事の事実の説明を厚くして補い、推測で埋めない。"
    )
