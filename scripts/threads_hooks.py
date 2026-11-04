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
import question_patterns

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
        "examples": ["結論：無料プランの人も今日から使えます。", "結論：個人なら無料アカウントで使えます。"],
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
# 3 = 2026-11-02 オーナー承認の形（書き出し／「要約すると、」＋事実2文／読者への問いかけ の3つのかたまり）。
# threads_post.py は 3 の記事の紹介文をかたまりごとに確かめて使い、2 以下の記事は要点・判定から組み立て直す
POST_STYLE = 3

# 全プロンプト共通の threads_post のルール（prompt_block の先頭に入る）
POST_RULES = """## Threads 紹介文（threads_post）のルール
投稿には記事の写真が付くので、本文で見出しを繰り返さない。記事へのリンクは投稿するときに足すので、本文には書かない。
全体で全角120〜150字。次の3つのかたまりを、この順に、空行（改行2つ）で区切って書く。
1. 書き出し（1行・全角30字以内）: この記事に関係する読者を具体的に名指しして呼びかける（呼びかけ型。2026-11-03 オーナー決定）。
   例:「マウンテンバイク好きの人、これは要チェックです。」「コードを書く開発者の皆さん、朗報です。」「電気代が気になる人に、知っておいてほしい話です。」
   【速報】は書かない（付けるかは投稿するときに決める）。「先に言うと、〜できません／対象外」「発表から◯分で掲載」のような書き出しは書かない。見出しをそのまま書かない。
2. 要約（2行）: 「要約すると、」で始め、事実を短い文2つで書く。1文に言いたいことは1つ、1文は全角40字以内が目安。文ごとに改行する。
   誰が（会社名・製品名）・何をしたか・何が変わるか が分かるようにする。よく知られた会社（OpenAI・Google・NVIDIA など）には
   「ChatGPTのOpenAI」「AI半導体のNVIDIA」のような説明を付けない。知られていない会社だけ「米新興の〇〇」のように短く添える。
3. 問いかけ（1文・全角40字以内・「？」で終える）: 記事を読まなくても答えられる、読者自身の好み・経験を聞く問い。
   例:「趣味の道具にかけるお金、何を重視して選びますか？」「夏と冬、電気代が高いと感じるのはどっちですか？」「証券口座、いくつ持っていますか？」
   問いの形は、下の「問いかけの型」（8つ）から記事に自然に合うものを選ぶ（合わなければ、ふだん何を使っているか／何を重視するか などのふつうの問い）。
   「この機能、使ってみたいですか？」「いちばん気になった発表はどれですか？」のように、記事の中身を知らないと答えられない問いにしない。
   記事の話題から外れた問いにしない。問いの中で新しい事実や数字を言わない。
書き方の例（形だけの参考。中身はそのまま使わない）:
円安倒産、まだ止まっていません。

要約すると、10月の「円安」倒産は6件で、52カ月連続です。
1〜10月で67件になり、もう去年1年分を超えました。

あなたの会社や取引先では、円安の影響は出ていますか？
- 数字・日付・価格・製品名は元記事のとおり正確に書く（丸めない・単位を変えない・記事にない数字は書かない）。
- AIに詳しくない人にも分かる言葉にする。専門用語は使わないか、普通の言葉に言い換える。
  専門的な発表も「誰の、どんな作業が、どう楽になるのか」に言い換える。
  例：「Azure SQLのAgent Skills」→「データベースの準備作業を、AIが代行できるようになった」
  例：「投機的デコーディング」→「回答の品質を保ったまま、AIの応答を最大5倍速くする仕組み」
- 人が書いたような自然な口語にする（「〜となっています」「〜が可能です」の連続を避ける）。「ざっくり」は使わない。
  読者は働く大人。落ち着いた言葉で書き、幼い言い方（勝手に／お任せ／〜してくれる／すごい／お絵かき／人手なしで）は使わない。
- 禁止: 「記事で👇」「続きは記事で」「詳しくはリンクで」など記事へ誘導するだけの締め、思わせぶりで中身を隠す書き方、
  煽り語（衝撃／ヤバい／必見／知らないと損）、記事にない事実・誇張、個人への憶測、ハッシュタグ、URL、絵文字、
  「コメントしてね」「いいねで教えて」などの反応のお願い、アンケートのふり、特定の銘柄・物件の売買のすすめ。"""

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
    # 目当ては1本あたりの訪問（threads_learn の avgVisits。古い学習結果なら閲覧）
    metric = "avgVisits" if all("avgVisits" in v for v in hooks.values()) else "avgViews"
    views = sum(v.get(metric, 0) * v.get("n", 0) for v in hooks.values()) / total
    eng = sum(v.get("avgEngagement", 0) * v.get("n", 0) for v in hooks.values()) / total
    return [
        key for key, v in hooks.items()
        if key in HOOK_KEYS and v.get("n", 0) >= WEAK_MIN_POSTS
        and v.get(metric, 0) < views * WEAK_RATIO and v.get("avgEngagement", 0) < eng * WEAK_RATIO
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
        "threads_post の1行目は、必ず呼びかけ型（callout）で書く。threads_hook には callout を入れる。",
        "記事に関係する読者を具体的に名指しする（〇〇好きの人／〇〇を使っている人／〇〇担当の人／〇〇の皆さん など）。",
        "言い回しは記事ごとに変える（「これは要チェックです」「朗報です」（良い知らせのときだけ）「これは見てほしいです」「知っておいてほしい話です」「押さえておきたい話です」）。",
        "以下の型の一覧は、伝える中身の参考（数字・ビフォーアフター・注意点など）。1行目の形は呼びかけにする。",
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
    lines += ["", question_patterns.prompt_block()]
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
    text = "、".join(
        f"{names.get(k, k)}（1本あたりの訪問{v['avgVisits']}）" if "avgVisits" in v else f"{names.get(k, k)}（平均閲覧{v['avgViews']}）"
        for k, v in best
    )
    untested = [h["name"] for h in HOOKS if h["key"] not in hooks]
    note = f"これまでの投稿では {text} の反応が良い。記事に合うならこれらを優先する。"
    if untested:
        note += f"ただし、まだ試していない型（{'、'.join(untested[:4])}）も、記事に合えば積極的に試す。"
    return note


def clean_hook(value: object) -> str:
    key = str(value or "").strip()
    return key if key in HOOK_KEYS else ""


# ---- 1行目の呼びかけ（2026-11-03 オーナー承認その2: 1行目は関係する読者を名指しする） ----
import re  # noqa: E402

import sns_rules  # noqa: E402

# 記事の言葉 → 呼びかける読者（〜の人, 〜の皆さん）。上から順に、見出し → 判定の読者 → タグ・説明文 の順で探す
AUDIENCES: list[tuple[str, tuple[str, str]]] = [
    (r"マウンテンバイク|e-?MTB|自転車|ロードバイク", ("自転車好きの人", "自転車好きの皆さん")),
    (r"バイク|二輪|オートバイ", ("バイク好きの人", "バイク好きの皆さん")),
    (r"ランドクルーザー|クラウン|SUV|新型車|自動車|クルマ|車種|ミニバン|軽自動車|電気自動車|(?<![A-Za-z])EV(?![A-Za-z])", ("車好きの人", "車好きの皆さん")),
    (r"セキュリティ|脆弱性|サイバー攻撃|不正アクセス|ランサム|悪用", ("会社のセキュリティ担当の人", "セキュリティ担当の皆さん")),
    (r"確定申告|インボイス|ふるさと納税|控除", ("確定申告をする人", "確定申告をする皆さん")),
    (r"年金", ("年金が気になる人", "年金が気になる皆さん")),
    (r"補助金|助成金|支援金|給付金", ("事業をしている人", "事業をしている皆さん")),
    (r"NISA|iDeCo|証券|口座数", ("証券口座を持っている人", "投資をしている皆さん")),
    (r"決算|株価|上場|IPO|業績", ("株や業績をチェックしている人", "株や業績を追っている皆さん")),
    (r"住宅ローン|不動産|マンション|家賃|賃貸|地価|リフォーム", ("住まいのお金が気になる人", "家を探している皆さん")),
    (r"副業|フリーランス|個人事業|起業|独立|開業", ("副業や独立を考えている人", "個人で働く皆さん")),
    (r"電気代|電力|ガス代|電気料金|ガソリン|光熱費", ("光熱費が気になる人", "家計を見ている皆さん")),
    (r"免税|旅行|観光|ホテル|航空", ("旅行が好きな人", "旅行好きの皆さん")),
    (r"税|社会保険|保険料|物価|値上げ", ("家計を管理している人", "家計を管理している皆さん")),
    (r"iPhone", ("iPhoneを使っている人", "iPhoneユーザーの皆さん")),
    (r"iPad", ("iPadを使っている人", "iPadユーザーの皆さん")),
    (r"(?<![A-Za-z])Mac(?![a-z])|macOS", ("Macを使っている人", "Macユーザーの皆さん")),
    (r"Android|Pixel|Galaxy", ("Androidスマホを使っている人", "Androidユーザーの皆さん")),
    (r"Windows", ("Windowsを使っている人", "Windowsユーザーの皆さん")),
    (r"イヤホン|ヘッドホン|スマートウォッチ|テレビ|家電|カメラ", ("新しいガジェットが好きな人", "ガジェット好きの皆さん")),
    (r"ゲーム|PS5|Switch|Xbox|Steam|GeForce NOW", ("ゲーム好きの人", "ゲーム好きの皆さん")),
    (r"動画編集|映像制作|YouTube|動画生成", ("動画を作る人", "動画を作る皆さん")),
    (r"デザイン|Figma|Canva|Affinity|Photoshop|イラスト", ("デザインをする人", "デザインをする皆さん")),
    (r"音声|ナレーション|ElevenLabs|音楽|作曲", ("音声や音楽を作る人", "音声や音楽を作る皆さん")),
    (r"ChatGPT|OpenAI", ("ChatGPTを使っている人", "ChatGPTユーザーの皆さん")),
    (r"Gemini|NotebookLM", ("Geminiを使っている人", "Geminiユーザーの皆さん")),
    (r"Claude|Anthropic", ("Claudeを使っている人", "Claudeユーザーの皆さん")),
    (r"Copilot", ("Copilotを使っている人", "Copilotユーザーの皆さん")),
    (r"GitHub|コード|プログラミング|開発者|エンジニア|SDK|ターミナル|Codex|(?<![A-Za-z])API(?![A-Za-z])", ("コードを書く人", "開発者の皆さん")),
    (r"データベース|AWS|Azure|Google Cloud|インフラ|サーバー", ("会社のIT担当の人", "IT担当の皆さん")),
    (r"営業|顧客対応|コールセンター|カスタマーサポート|接客", ("営業や顧客対応をしている人", "営業・接客の皆さん")),
    (r"マーケティング|広告|集客", ("集客を担当している人", "マーケティング担当の皆さん")),
    (r"経営|CEO|社長|経営者", ("経営をしている人", "経営者の皆さん")),
    (r"研修|教育|学生|大学|学校", ("学び直しを考えている人", "学ぶ皆さん")),
    # 2026-11-03 オーナー指摘「呼びかけが弱い」: だれにでも当てはまる言い方（AIの裏側の技術が気になる人）をやめ、仕事の立場で名指しする
    (r"学習|トレーニング|クラスター|GPU", ("GPUでAIを動かしている人", "AIインフラ担当の皆さん")),
    (r"データセンター|電力", ("データセンターの電力や設備を見ている人", "インフラ担当の皆さん")),
    (r"半導体|NVIDIA|チップ", ("AI向けの機器やクラウドを選ぶ人", "AIインフラ担当の皆さん")),
    (r"研究|論文", ("新しい技術を追っている人", "テック好きの皆さん")),
]
# 判定（verdict.audience）→ 読者
VERDICT_AUDIENCES = {
    "開発者": ("コードを書く人", "開発者の皆さん"), "エンジニア": ("コードを書く人", "開発者の皆さん"),
    "企業のIT部門": ("会社のIT担当の人", "IT担当の皆さん"), "デザイナー": ("デザインをする人", "デザインをする皆さん"),
    "クリエイター": ("ものづくりをする人", "クリエイターの皆さん"), "営業職": ("営業や顧客対応をしている人", "営業・接客の皆さん"),
    "経営層": ("経営をしている人", "経営者の皆さん"), "マーケター": ("集客を担当している人", "マーケティング担当の皆さん"),
    "個人事業主": ("個人で仕事をしている人", "個人事業主の皆さん"), "研究者": ("新しい技術を追っている人", "研究者の皆さん"),
}
GENRE_AUDIENCES = {
    "ai": ("AIを仕事で使っている人", "AIを使っている皆さん"),
    "business": ("仕事のニュースを追っている人", "働く皆さん"),
    "public": ("暮らしの制度が気になる人", "働く皆さん"),
    "investment": ("資産づくりを考えている人", "資産づくりを考えている皆さん"),
    "real-estate": ("住まいのお金が気になる人", "家を探している皆さん"),
    "tech-gadget": ("新しいガジェットが好きな人", "ガジェット好きの皆さん"),
    "sports": ("スポーツ好きの人", "スポーツ好きの皆さん"),
}
DEFAULT_AUDIENCE = ("仕事とお金のニュースを追っている人", "働く皆さん")
# 呼びかけの言い回し（id, 形, 皆さんの形を使うか）。同じ言い回しは続けない
CALLOUTS: list[tuple[str, str, bool]] = [
    ("check", "{who}、これは要チェックです。", False),
    ("good", "{who}、朗報です。", True),
    ("see", "{who}、これは見てほしいです。", False),
    ("know", "{who}に、知っておいてほしい話です。", False),
    # ("keep", "{who}は、押さえておきたい話です。") は 2026-11-03 にやめた（オーナー「弱い」）
    ("careful", "{who}は、ここだけ確認を。", False),
]
CALLOUT_TAILS = [
    ("check", re.compile(r"要チェック")), ("good", re.compile(r"朗報")), ("see", re.compile(r"見てほしい|見てください")),
    ("know", re.compile(r"知っておいてほしい|知っておきたい")), ("keep", re.compile(r"押さえておきたい")), ("careful", re.compile(r"確認を|注意")),
]
NEGATIVE_RE = re.compile(r"悪用|攻撃|脆弱性|詐欺|不正|流出|漏えい|漏洩|障害|不具合|事故|倒産|破たん|被害|訴訟|停止|リコール|値上げ|廃止|終了|縮小")


def audience_of(article: dict) -> tuple[str, str]:
    """記事に関係する読者（呼びかけ用。〜の人, 〜の皆さん）"""
    title = f"{article.get('title', '')} {article.get('seoTitle', '')}"
    for pattern, who in AUDIENCES:
        if re.search(pattern, title):
            return who
    verdict = str((article.get("verdict") or {}).get("audience") or "")
    if verdict in VERDICT_AUDIENCES:
        return VERDICT_AUDIENCES[verdict]
    rest = f"{' '.join(str(t) for t in article.get('tags') or [])} {article.get('description', '')}"
    for pattern, who in AUDIENCES:
        if re.search(pattern, rest):
            return who
    return GENRE_AUDIENCES.get(str(article.get("genreSlug") or ""), DEFAULT_AUDIENCE)


def callout_id(line: str) -> str:
    """呼びかけの言い回しの id（同じ言い回しを続けないため）。分からなければ gen"""
    for key, pattern in CALLOUT_TAILS:
        if pattern.search(str(line or "")):
            return key
    return "gen"


def callout(article: dict, recent: list[str] | None = None) -> tuple[str, str]:
    """1行目の呼びかけ（id, 文）。良い知らせだけ「朗報」、悪い知らせ・注意点は「ここだけ確認を」。直前の言い回しは避ける"""
    person, group = audience_of(article)
    verdict = article.get("verdict") or {}
    negative = bool(NEGATIVE_RE.search(f"{article.get('title', '')} {article.get('seoTitle', '')}"))
    positive = not negative and (verdict.get("status") == "available" or verdict.get("free") is True)
    caution = negative or verdict.get("status") == "limited" or verdict.get("japan") == "no"
    options = [
        c for c in CALLOUTS
        if (c[0] != "good" or positive) and (c[0] != "careful" or caution)
    ]
    last = [r for r in (recent or []) if r][:1]
    fresh = [c for c in options if c[0] not in last] or options
    key, form, use_group = fresh[sum(ord(ch) for ch in str(article.get("slug") or "")) % len(fresh)]
    return key, form.format(who=group if use_group else person)


def with_breaking(line: str, article: dict) -> str:
    """本当の速報（sns_rules.is_fast_news）の記事だけ、1行目の頭に【速報】を付ける。ほかの記事からは外す"""
    body = re.sub(r"^【速報】\s*", "", str(line or "")).strip()
    return f"【速報】{body}" if sns_rules.is_fast_news(article) else body
