"""
Threads 投稿の「書き出し（1行目）」の型。

同じ型が続くとフィードで読み飛ばされるため、記事ごとに合う型を選び、直近で使った型は避ける。
型は SNS で反応を取りやすい書き出しの定番（問いかけ・数字・ビフォーアフター・逆説・共感・待望 など）を、
サイソクニュースの読者（仕事とお金に意欲の高い働く人。scripts/persona.py）に合わせて調整したもの。
2026-11-02 に「活用型」「お金インパクト型」を足して15型にした（13型はそのまま）。どの型でも「記事にある事実だけ」を使う。
"""
from __future__ import annotations

import json
from pathlib import Path

import persona

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"

# key: 記録用の識別子 / name: 型の名前 / how: 書き方 / when: 使ってよい条件 / examples: 1行目の例
HOOKS: list[dict] = [
    {
        "key": "question",
        "name": "問いかけ型",
        "how": "読者の日常の作業や悩みに寄せて、友人に話しかけるように問う",
        "when": "読者の仕事・生活に直結する機能",
        "examples": ["議事録づくり、まだ手作業でやってますか？", "経費の精算、毎月どのくらい時間をかけてます？"],
    },
    {
        "key": "finally",
        "name": "待望型",
        "how": "待たれていたものが来た高揚感を、短く言い切る",
        "when": "以前から予告・噂されていた機能の正式提供、日本での提供開始、プレビューからの一般提供など、本当に「待望」と言える発表だけ",
        "examples": ["ついに来た。Claudeが日本語の音声で話せるように。", "待ってた人、多いはず。Geminiのあの機能が無料プランにも。"],
    },
    {
        "key": "number",
        "name": "数字インパクト型",
        "how": "記事中でいちばん驚く数字を1つだけ、文頭に置く",
        "when": "性能・速度・価格・規模などに印象的な数字がある",
        "examples": ["処理速度、最大5倍。", "月額0円で、GPT-6クラスが使える。"],
    },
    {
        "key": "before_after",
        "name": "ビフォーアフター型",
        "how": "これまでとこれからを1行で対比する",
        "when": "作業時間・手順・コストがはっきり変わる発表",
        "examples": ["3時間かかってた資料づくりが、5分で終わる。", "これまで3つのツールを行き来してた作業が、1画面で完結。"],
    },
    {
        "key": "contrarian",
        "name": "逆説型",
        "how": "よくある見方をひっくり返して、本当の注目点を示す",
        "when": "派手な見出しの裏に、より重要なポイントがある",
        "examples": ["新モデルの性能より、大事なのは料金のほう。", "「すごいデモ」で終わらせるのはもったいない発表です。"],
    },
    {
        "key": "empathy",
        "name": "あるある共感型",
        "how": "読者が感じている小さな不満を、そのまま言葉にする",
        "when": "多くの人が困っている作業を解決する機能",
        "examples": ["AIの回答、毎回コピペして整えるの地味に面倒なんですよね。", "会議のあと、誰が何を言ったか思い出せない問題。"],
    },
    {
        "key": "conclusion",
        "name": "結論先出し型",
        "how": "読者がいちばん知りたい答えを先に言う",
        "when": "「使えるの？」「無料？」「日本で使える？」への答えがはっきりしている",
        "examples": ["結論：無料プランの人も今日から使えます。", "先に言うと、日本からはまだ使えません。"],
    },
    {
        "key": "callout",
        "name": "呼びかけ型",
        "how": "関係のある人を名指しして、手を止めてもらう",
        "when": "対象の読者がはっきりしている（開発者・動画編集者・営業など）",
        "examples": ["動画編集してる人、これはチェックしてほしい。", "個人事業主の人、確定申告の前にこれだけ確認を。"],
    },
    {
        "key": "surprise",
        "name": "意外な一点型",
        "how": "発表動画の中で、見落とされがちな驚きの事実を挙げる",
        "when": "本文に、見出しになっていない意外な事実がある",
        "examples": ["発表動画でいちばん驚いたのは、最後の30秒でした。", "ほとんど話題になってないけど、これはかなり大きい。"],
    },
    {
        "key": "breaking",
        "name": "速報型",
        "how": "【速報】と付けて、誰が何をしたかだけを言う",
        "when": "大手企業の新モデル・新製品の発表直後など、ニュース性が高い",
        "examples": ["【速報】OpenAIが新しい音声エージェントを公開。", "【速報】Googleが Gemini の新モデルを発表しました。"],
    },
    {
        "key": "caution",
        "name": "注意喚起型",
        "how": "使う前に知っておくべき条件や落とし穴を先に伝える",
        "when": "料金・提供地域・プラン条件に注意点がある",
        "examples": ["これ、使う前に料金だけは確認しておいたほうがいいです。", "話題の新機能、実は対象プランが限られています。"],
    },
    {
        "key": "free",
        "name": "無料型",
        "how": "無料で試せることを最初に伝える",
        "when": "判定で無料で試せる（無料プラン・無料枠・OSS）ときだけ",
        "examples": ["これ、無料で試せます。", "お金をかけずに、今日から触れるAIの新機能です。"],
    },
    {
        "key": "future",
        "name": "変化の予告型",
        "how": "この発表で仕事や日常がどう変わるかを一言で言う",
        "when": "研究・デモ段階でも、将来の変化がはっきり描ける",
        "examples": ["数年後の会議は、たぶんこうなります。", "ロボットが家事をする未来、思ったより近いかもしれません。"],
    },
    {
        "key": "use_case",
        "name": "活用型",
        "how": "記事の事実から、読者の仕事・副業で何に使えるかを一言で言う",
        "when": "本文や work_money に具体的な使い道（作業・職種・手続き）が書かれていて、すでに提供されているときだけ。提供前・研究段階では使わない",
        "examples": ["これ、明日の会議資料づくりで試せます。", "副業で記事を書いている人の下調べが、かなり楽になりそうです。"],
    },
    {
        "key": "money",
        "name": "お金インパクト型",
        "how": "料金・値下げ幅・補助額・期限など、記事にあるお金の数字を最初に言う",
        "when": "記事に金額（料金・値下げ・補助・税額・手数料）がはっきり書かれているときだけ。節約額や収入を自分で計算しない。投資の売買はすすめない",
        "examples": ["月額料金が、1,500円から980円に。", "補助は最大50万円。申請は12月まで。"],
    },
]

HOOK_KEYS = [hook["key"] for hook in HOOKS]
AVOID_RECENT = 4

# 投稿文の版。2 = 投稿だけで用が足りる「完結型」（記事へ誘導するだけの締めをやめた版）。
# threads_post.py は 2 未満の記事の紹介文を使わず、見出し・要点・判定から組み立て直す
POST_STYLE = 2

# 全プロンプト共通の threads_post のルール（prompt_block の先頭に入る）
POST_RULES = """## Threads 紹介文（threads_post）のルール
投稿だけ読めば用が足りる「完結型」にする。記事を開かなくても「何が起きて、自分にどう関係するか」が分かること。
記事へのリンクは返信に付けるので、本文には書かない。
- 2〜4行（全角60〜160字）。1行は短く（全角40字以内が目安）。行の間は改行1つ。
- 1行目: 下の「書き出しの型」から1つ選んで書く（30字以内）。
- 残りの行で次をすべて伝える: 誰が（会社・製品の正体を一言で。例「ChatGPTのOpenAIが」）／何をした（新しいAIモデル・新機能・値下げ など種類を言う）／何が変わる／自分に関係あるか（使える人・料金・日本で使えるか）。
- 数字・日付・価格・製品名は元記事のとおり正確に書く（丸めない・単位を変えない・記事にない数字は書かない）。
- AIに詳しくない人にも分かる言葉にする。専門用語は使わないか、使うなら「＝〇〇のこと」と言い換える。
  専門的な発表も「誰の、どんな作業が、どう楽になるのか」に言い換える。
  例：「Azure SQLのAgent Skills」→「データベースの準備作業を、AIが代わりにやってくれるようになった」
  例：「投機的デコーディング」→「AIの返事が、品質そのままで最大5倍速く返ってくる仕組み」
- 人が書いたような自然な口語にする（「〜となっています」「〜が可能です」の連続を避ける）。
- 締めは事実か、読者が答えやすい短い問いかけ（問いかけは2回に1回まで）。
- 禁止: 「記事で👇」「続きは記事で」「詳しくはリンクで」など記事へ誘導するだけの締め、思わせぶりで中身を隠す書き方、
  煽り語（衝撃／ヤバい／必見／知らないと損）、記事にない事実・誇張、個人への憶測、ハッシュタグ、URL、絵文字。"""

# 「明らかに伸びない型」とみなす条件（threads_learn.py の集計から）。少ない本数では決めつけない
WEAK_MIN_POSTS = 4
WEAK_RATIO = 0.5


def weak_hooks() -> list[str]:
    """十分な本数を試して、閲覧も反応も全体平均の半分に届かない型（避ける・順位を下げる）"""
    path = ROOT / "state" / "threads_learning.json"
    try:
        hooks = json.loads(path.read_text("utf-8")).get("axes", {}).get("hook", {})
    except Exception:
        return []
    total = sum(v.get("n", 0) for v in hooks.values())
    if total < 10:
        return []
    views = sum(v.get("avgViews", 0) * v.get("n", 0) for v in hooks.values()) / total
    eng = sum(v.get("avgEngagement", 0) * v.get("n", 0) for v in hooks.values()) / total
    return [
        key for key, v in hooks.items()
        if key in HOOK_KEYS and v.get("n", 0) >= WEAK_MIN_POSTS
        and v.get("avgViews", 0) < views * WEAK_RATIO and v.get("avgEngagement", 0) < eng * WEAK_RATIO
    ]


def recent_hooks(limit: int = AVOID_RECENT) -> list[str]:
    """直近に生成した記事で使った書き出しの型（新しい順）"""
    found: list[tuple[str, str]] = []
    if not ARTICLES_DIR.exists():
        return []
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            article = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        hook = article.get("threadsHook")
        if hook:
            found.append((article.get("generatedAt", ""), hook))
    found.sort(reverse=True)
    return [hook for _, hook in found[:limit]]


def prompt_block(recent: list[str] | None = None) -> str:
    """生成プロンプトに足す「書き出しの型」の説明"""
    recent = recent if recent is not None else recent_hooks()
    lines = [
        POST_RULES,
        "",
        persona.SNS_BLOCK,
        "",
        "## Threads 紹介文（threads_post）の書き出し",
        "threads_post の1行目は、次の型から、この記事にいちばん合うものを1つ選んで書く。選んだ型の key を threads_hook に入れる。",
        "各型の「使ってよい条件」を満たさない型は選ばない。例はあくまで雰囲気の参考で、そのまま使わない。",
    ]
    if recent:
        names = "、".join(h["name"] for h in HOOKS if h["key"] in recent)
        lines.append(f"直近の投稿で {names} を使ったので、今回はそれ以外から選ぶ（同じ型が続くと読み飛ばされるため）。")
    weak = weak_hooks()
    if weak:
        names = "、".join(h["name"] for h in HOOKS if h["key"] in weak)
        lines.append(f"{names} はこれまでの投稿で明らかに反応が弱いので、ほかに合う型が無いときだけ使う。")
    learned = learned_summary()
    if learned:
        lines.append(learned)
    for hook in HOOKS:
        examples = "／".join(f"「{e}」" for e in hook["examples"])
        lines.append(f"- {hook['key']}（{hook['name']}）: {hook['how']}。使ってよい条件: {hook['when']}。例: {examples}")
    lines.append(
        "どの型でも、人が書いたような自然な口語にする。記事にない事実・誇張は書かない。"
        "「ついに」「待望」は待望型の条件を満たすときだけ使ってよい。"
    )
    return "\n".join(lines)


def learned_summary() -> str:
    """これまでの投稿の反応から分かった、伸びやすい型（scripts/threads_learn.py の結果）"""
    path = ROOT / "state" / "threads_learning.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return ""
    hooks = {k: v for k, v in data.get("axes", {}).get("hook", {}).items() if v.get("n", 0) >= 2}
    if data.get("posts", 0) < 6 or len(hooks) < 2:
        return ""  # データが少ないうちは決めつけない
    names = {h["key"]: h["name"] for h in HOOKS}
    best = list(hooks.items())[:3]
    text = "、".join(f"{names.get(k, k)}（平均閲覧{v['avgViews']}）" for k, v in best)
    untested = [h["name"] for h in HOOKS if h["key"] not in hooks]
    note = f"これまでの投稿では {text} の反応が良い。記事に合うならこれらを優先する。"
    if untested:
        note += f"ただし、まだ試していない型（{'、'.join(untested[:4])}）も、記事に合えば積極的に試す。"
    return note


def clean_hook(value: object) -> str:
    key = str(value or "").strip()
    return key if key in HOOK_KEYS else ""
