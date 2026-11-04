"""
SNS 投稿の最後の「問いかけ」の型（2026-11-04 オーナー決定。台帳 S19）。

オーナー: 「いろんな型を試していきたい」。提案（out/コメントが増える投稿_提案_2026-11-04.md）の8つの型をすべて使い、
記事に自然に合う型を選ぶ。合う型が無ければ、今までどおりの「ふつうの問い」（plain）にする。

- 生成のとき（scripts/main.py・web_articles.py・roundup.py が threads_hooks.prompt_block() 経由で受け取る）:
  threads_post の問いかけを型に合わせて書き、threads_questions に「合う型の問い」を2〜3個（違う型）出してもらう。
  LLM の呼び出しは増やさない（記事を書く同じ呼び出しの中で書く）。
- 投稿のとき（scripts/threads_post.py の compose。予約・速報・Bluesky の同じ経路）:
  記事の候補の中から、直近14日でいちばん使っていない型を選ぶ（回し方）。直前に使った型は続けない。
  選んだ型は state/threads_posted.json の各投稿に questionPattern として残す（流入の報告で型ごとに比べる）。
- 「賛否が分かれる度」で記事を選ぶことはしない（2026-11-04 オーナー決定。読者像がゆがむため）。記事の選び方は変えない。

守ること（型を使っても、今までの決まりはすべてそのまま。門番 scripts/sns_rules.py の check_post が投稿の直前に確かめる）:
  記事を読まなくても自分の経験で答えられる問い／どちらを選んでも恥ずかしくない問い／怒りを誘わない（レイジベイトにしない）／
  反応のお願いをしない／記事にない事実を問いの中で言わない。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "threads_posted.json"

PATTERNS = [
    {
        "key": "ab", "name": "二択＋決め手",
        "how": "両方にもっともな理由がある2つのうち、自分ならどちらかを聞く（理由まで書きたくなる二択）",
        "when": "選び方・使い方・見方が2通りに分かれる話題",
        "examples": ["会議で伝わるのは、きれいなスライドと動く試作品のどちらだと感じますか？",
                     "SNSを開いたとき、ふだん見るのはおすすめとフォロー中のどちらですか？"],
    },
    {
        "key": "tradeoff", "name": "便利さと引き換え",
        "how": "得るものと失うものを並べ、自分ならどうするかを聞く",
        "when": "プライバシー・広告・無料と有料・手間と安心など、引き換えがある話題",
        "examples": ["自分の書き込みが検索で見つかるとしたら、公開のままにしますか、外しますか？",
                     "AIの答えに広告が混ざるとしたら、それでも調べものはAIで済ませますか？"],
    },
    {
        "key": "line", "name": "どこまでなら（線引き）",
        "how": "はい・いいえではなく「どこまでなら」を聞く",
        "when": "教育・AIの加工・自動化など、人によって線の引き方が違う話題",
        "examples": ["子どもの宿題にAIを使うなら、どこまで任せてよいと考えますか？",
                     "部屋探しで見る写真、家具をAIで置いた加工はどこまでなら許せますか？"],
    },
    {
        "key": "price", "name": "いくらまでなら（値ごろ感）",
        "how": "自分ならいくらまで出すか・何年使うかを数で聞く",
        "when": "料金・価格・値上げ・プラン変更の話題（投資や売買の判断は聞かない）",
        "examples": ["仕事で使うAIに、自分のお金で毎月いくらまでなら出せますか？",
                     "仕事用のパソコン、自分で買うならいくらまで出せますか？"],
    },
    {
        "key": "count", "name": "自分の実感を数で",
        "how": "「いくつ」「何時間」「どれくらい」で答えられる自分の経験を聞く",
        "when": "ふだんの使い方・時間・回数で語れる話題",
        "examples": ["右クリックのメニューで、ふだん本当に使う項目はいくつありますか？",
                     "職場で生成AIを使うようになって、1週間でどれくらい時間が浮きましたか？"],
    },
    {
        "key": "expert", "name": "使っている人の見立て",
        "how": "1行目で愛好家・使っている人を名指しし、選び方・使い方を聞く（詳しい人が自分の意見を書ける問い）",
        "when": "愛好家や使っている人がいる製品（車・バイク・カメラ・スマホ・ゲーム・仕事の道具・開発ツール）",
        "examples": ["折りたたみスマホ、閉じたままと開いて使う時間、どちらが長いですか？",
                     "ランクルFJを選ぶなら、丸目と標準の顔のどちらにしますか？"],
    },
    {
        "key": "japan", "name": "日本に来たら",
        "how": "海外で始まったもの・日本に来るものについて、自分の暮らしに来たらどうするかを聞く",
        "when": "海外の新しいサービス・日本で始まる予定のもの・日本はまだ対象外のもの",
        "examples": ["運転手のいないタクシーが来たら、ためらわずに乗れますか？",
                     "留守番電話のメッセージ、聞くのと文字で読むのと、どちらが楽ですか？"],
    },
    {
        "key": "future", "name": "仕事の見通し",
        "how": "AIと仕事の話で、自分の仕事がこれからどうなると感じるかを聞く（どちらの立場も否定しない）",
        "when": "AIで仕事・職種・働き方が変わる話題",
        "examples": ["デザインの仕事は、これから減ると感じますか、形が変わると感じますか？",
                     "問い合わせの電話、すぐ出るAIと少し待つ人、どちらにつながってほしいですか？"],
    },
]
KEYS = [p["key"] for p in PATTERNS]
NAMES = {p["key"]: p["name"] for p in PATTERNS}
PLAIN = "plain"  # どの型も自然に合わない記事の、今までどおりのふつうの問い
ALL_KEYS = KEYS + [PLAIN]
NAMES[PLAIN] = "ふつうの問い"
WINDOW_DAYS = 14   # 回し方で数える期間
TARGET_PER_WINDOW = 5  # 2週間で各型5本以上（型を残すか外すかは、型ごとに5本以上出してから決める）
MAX_CANDIDATES = 3

# 怒りを誘う・決めつける・人を責める問い（レイジベイト）。型を使っても出さない
RAGE_RE = re.compile(
    r"ありえない|あり得ない|許せない|おかしい(?:と思|です|ですか)|最悪|炎上|終わった|オワコン|老害|情弱|時代遅れ|遅れている|"
    r"賛成ですか|反対ですか|賛成か反対|どちらが正しい|論破|ムカつ|腹が立|ヤバ"
)

# 型を見分ける目安（生成の印が無い問い＝以前の形の記事・話題から作った問い にだけ使う）。上から順に当てる
_CLASSIFY = [
    ("price", re.compile(r"いくら(?:まで|なら|出|払)|何円|月額いくら|毎月いくら")),
    ("line", re.compile(r"どこまで")),
    ("future", re.compile(r"(?:仕事|職|業務|働き方|職場)[^？?]{0,24}(?:減る|増える|変わる|なくなる|残る)|これから(?:減|増|変わ)")),
    ("japan", re.compile(r"日本(?:に|で|でも)[^？?]{0,12}(?:来|始ま|広が|入っ|出)|が来たら|に来たら")),
    ("tradeoff", re.compile(r"としたら|それでも|引き換え|外しますか|オフにしますか")),
    ("count", re.compile(r"いくつ|何(?:時間|回|個|本|台|年|分|日|人|件|割|枚|社)|どれくらい|どのくらい")),
    ("ab", re.compile(r"どちら|どっち|派ですか")),
]

# この実行の中で選んだ型（同じ実行で何本も作るとき、同じ型を続けない）
_RUN_PATTERNS: list[str] = []


def clean(value: object) -> str:
    """型の印を正規化する。知らない値は空"""
    key = str(value or "").strip().lower()
    return key if key in ALL_KEYS else ""


def classify(question: str) -> str:
    """生成の印が無い問いの型を、言い回しから見分ける（分からなければ plain）"""
    q = str(question or "")
    for key, pattern in _CLASSIFY:
        if pattern.search(q):
            return key
    return PLAIN


def question_problems(question: str) -> list[str]:
    """型の問いとして出してはいけない理由（怒りを誘う・決めつける）"""
    m = RAGE_RE.search(str(question or ""))
    return [f"怒りを誘う・決めつける言い方（「{m.group(0)}」）"] if m else []


def from_generated(value: object, limit: int = MAX_CANDIDATES) -> list[dict]:
    """生成の threads_questions を [{"pattern", "question"}] に整える（型が重複・不明・空のものは落とす）"""
    out: list[dict] = []
    seen: set[str] = set()
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        pattern = clean(item.get("pattern"))
        question = str(item.get("question") or "").strip().strip("「」\"")
        if not pattern or not question or question in {o["question"] for o in out}:
            continue
        if pattern in seen and pattern != PLAIN:
            continue
        seen.add(pattern)
        out.append({"pattern": pattern, "question": question})
        if len(out) >= limit:
            break
    return out


def _when(rec: dict) -> datetime | None:
    for value in (rec.get("sentAt"), rec.get("dueAt"), rec.get("postedAt")):
        try:
            when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return None


def load_posted() -> dict:
    try:
        return json.loads(STATE_PATH.read_text("utf-8")).get("posted") or {}
    except (OSError, ValueError):
        return {}


def history(posted: dict | None = None, now: datetime | None = None, days: int = WINDOW_DAYS) -> list[tuple[datetime, str]]:
    """直近 days 日の投稿で使った型（古い順）。型の印が無い投稿は数えない"""
    posted = load_posted() if posted is None else posted
    now = now or datetime.now(timezone.utc)
    out = []
    for rec in posted.values():
        key = clean(rec.get("questionPattern"))
        when = _when(rec)
        if key and when and now - timedelta(days=days) <= when:
            out.append((when, key))
    return sorted(out)


def usage(posted: dict | None = None, now: datetime | None = None, days: int = WINDOW_DAYS) -> dict[str, int]:
    """型ごとの本数（直近 days 日＋この実行で選んだ分）"""
    counts = {k: 0 for k in KEYS}
    for _, key in history(posted, now, days):
        if key in counts:
            counts[key] += 1
    for key in _RUN_PATTERNS:
        if key in counts:
            counts[key] += 1
    return counts


def last_used(posted: dict | None = None, now: datetime | None = None) -> str:
    """直前の投稿で使った型（この実行で選んだ分が先）"""
    run = [k for k in _RUN_PATTERNS if k in KEYS]
    if run:
        return run[-1]
    hist = [k for _, k in history(posted, now) if k in KEYS]
    return hist[-1] if hist else ""


def rotation_order(posted: dict | None = None, now: datetime | None = None) -> list[str]:
    """回す順: 直近14日で本数の少ない型 → 長く使っていない型 → 一覧の順。直前に使った型はいちばん後ろ"""
    counts = usage(posted, now)
    last_seen: dict[str, int] = {}
    hist = [k for _, k in history(posted, now)] + list(_RUN_PATTERNS)
    for i, key in enumerate(hist):
        last_seen[key] = i
    last = last_used(posted, now)
    return sorted(KEYS, key=lambda k: (k == last, counts[k], last_seen.get(k, -1), KEYS.index(k)))


def pick(candidates: list[dict], posted: dict | None = None, now: datetime | None = None) -> dict | None:
    """記事に合う問いの候補（チェック済み）から、回し方に従って1つ選ぶ。型の問いが無ければ最初のふつうの問い"""
    if not candidates:
        return None
    order = rotation_order(posted, now)
    typed = [c for c in candidates if c.get("pattern") in KEYS]
    if typed:
        return min(typed, key=lambda c: order.index(c["pattern"]))
    return candidates[0]


def note_used(key: str) -> None:
    """この実行で選んだ型を覚える（同じ実行の次の投稿で同じ型を続けない）"""
    if clean(key):
        _RUN_PATTERNS.append(clean(key))


def shortest(posted: dict | None = None, now: datetime | None = None, n: int = 3) -> list[str]:
    """いま投稿の数が少ない型（生成のときに候補へ入れてほしい型）"""
    return rotation_order(posted, now)[:n]


def prompt_block(posted: dict | None = None) -> str:
    """生成プロンプトに足す「問いかけの型」の説明（threads_hooks.prompt_block の中に入る）"""
    lines = [
        "## threads_post の問いかけの型（2026-11-04 オーナー決定。いろいろな型を試す）",
        "threads_post の3つ目のかたまり（問いかけ）は、次の8つの型のうち、この記事に自然に合うものを使って書く。",
    ]
    for p in PATTERNS:
        examples = "／".join(f"「{e}」" for e in p["examples"])
        lines.append(f"- {p['key']}（{p['name']}）: {p['how']}。合う記事: {p['when']}。例: {examples}")
    lines += [
        f"- {PLAIN}（ふつうの問い）: どの型も自然に合わないときだけ。読者自身の好み・経験を聞く、今までどおりの問い。",
        "守ること:",
        "- 記事を読まなくても、自分の経験や好みで1行で答えられる問いにする。二択は、どちらを選んでも恥ずかしくない、両方にもっともな理由があるものだけ。",
        "- 怒りを誘う・人や会社を責める・どちらかを「遅れている」「危ない」と決めつける問いにしない。賛成か反対かは聞かない。"
        "政党・選挙・外交、病気や薬の判断、株や投資の売買の判断は聞かない。",
        "- 問いの中で記事にない事実・数字を言わない。「コメントで教えて」「番号で答えて」などの反応のお願いはしない。"
        "「使ってみたいですか」「どう思いますか」「試しますか」「導入されたら」の形は使わない。",
        f"threads_questions には、この記事に自然に合う型の問いを2〜{MAX_CANDIDATES}個、それぞれ違う型で書く（合う順。1つ目は threads_post の最後の問いと同じ文）。"
        "各要素は pattern（型の英字: " + "・".join(ALL_KEYS) + "）と question（問いの1文・全角40字以内・「？」で終える）。",
        f"自然に合う型が無いときは、{PLAIN} の問い1つだけでよい。無理に型に当てはめない。",
    ]
    few = shortest(posted)
    if few:
        lines.append("いま投稿の数が少ない型: " + "、".join(f"{k}（{NAMES[k]}）" for k in few) + "。記事に自然に合うなら、これらを候補に入れる。")
    return "\n".join(lines)


# 生成の出力の形（記事を書く同じ呼び出しで出してもらう。LLM の呼び出しは増やさない）
SCHEMA_FIELD = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {"pattern": {"type": "STRING", "enum": ALL_KEYS}, "question": {"type": "STRING"}},
        "required": ["pattern", "question"],
        "propertyOrdering": ["pattern", "question"],
    },
}


def add_to_schema(schema: dict) -> dict:
    """記事の出力の形に threads_questions を足す（threads_hook のすぐ後）。同じ形に2回足しても1つだけ"""
    schema["properties"]["threads_questions"] = SCHEMA_FIELD
    for field in ("required", "propertyOrdering"):
        keys = schema.get(field)
        if keys is None or "threads_questions" in keys:
            continue
        at = keys.index("threads_hook") + 1 if "threads_hook" in keys else len(keys)
        keys.insert(at, "threads_questions")
    return schema


def record_fields(data: dict) -> dict:
    """生成の結果から、記事の JSON に残す欄（threadsQuestions。空なら何も足さない）"""
    found = from_generated((data or {}).get("threads_questions"))
    return {"threadsQuestions": found} if found else {}
