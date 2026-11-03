#!/usr/bin/env python3
"""Threads の本文に足す「人らしい一行」。threads_post.py / breaking_post.py から使う（Gemini は呼ばない）。

- editor_comment: サイソク編集部の短い感想（「編集部：」で事実と分ける。誇張しない）。地震・津波には付けない
- question_line: 記事に合った自然な問いかけ（hook_thread の形だけ。通常投稿の約半分。速報・地震には付けない）
- reader_question: 本文の3つ目のかたまり「読者への問いかけ」（2026-11-02〜。紹介文に問いが無いときの代わり）
- 同じ言い回しが続かないよう、直近の投稿で使った文は避ける
- A/B（hook_thread / classic）を公平に保つため、問いかけを付けるかは形式と独立に決める（want_question）
"""
from __future__ import annotations

import re

import persona

PREFIX = "編集部："

# 判定（verdict）から言えることだけ。感想なので言い切りすぎない
COMMENTS = {
    "free": ["無料で試せるのはありがたいところ。", "まず無料で触ってみられるのがいいですね。", "お試しのハードルが低いのは助かります。"],
    "dev": ["これは開発者にはうれしい更新。", "開発の手間が少し減りそうです。", "現場のエンジニアには地味に効きそう。"],
    "business": ["仕事での使い道が広がりそう。", "日々の業務で効いてきそうな更新です。", "職場で使えるかどうか、気になるところ。", "明日の仕事で試せるか、見ておきたい更新です。"],
    # お金・制度・不動産・ビジネスの話題（読者像: 仕事とお金に意欲の高い働く人。売買はすすめない）
    "money": ["家計や仕事のお金に関わる話なので、押さえておきたいところ。", "自分の場合にどう当てはまるか、一度確かめておきたい話です。", "期限や条件は、公式の案内で確認しておくと安心です。"],
    "general": ["普段使いで便利になりそう。", "身近なところで役立ちそうです。", "使い勝手がどう変わるか楽しみです。"],
    "japan_no": ["日本で使えるようになるかが気になります。", "日本での提供に期待したいところ。"],
    "upcoming": ["続報にも注目しています。", "今後の広がりに注目です。", "提供開始の続報を待ちたいところ。"],
    "research": ["実用化までの道のりにも注目したいです。", "研究の段階ですが、今後が気になる話です。"],
    "default": ["地味に便利そうな更新です。", "これは気になる動き。", "どこまで広がるか注目しています。"],
}
QUESTIONS = {
    "topic": ["ふだん{topic}は使っていますか？", "{topic}、最近使いましたか？"],
    "free": ["無料なら試してみますか？", "まず無料で試してみたいですか？"],
    "dev": ["開発で使ってみたいですか？", "もう試した人はいますか？", "チームに入れてみたいと思いますか？"],
    "business": ["仕事で使ってみたいですか？", "職場で導入されたら使いますか？", "業務のどこで使えそうですか？"],
    "money": ["仕事やお金の面で、影響はありそうですか？", "みなさんの仕事には関係ありそうですか？"],
    "general": ["使ってみたいと思いますか？", "普段使いできそうですか？", "みなさんならどう使いますか？"],
    "japan_no": ["日本でも使えたら試したいですか？"],
    "upcoming": ["使えるようになったら試したいですか？", "みなさんはどう思いますか？"],
    "default": ["みなさんはどう思いますか？", "気になった点はありますか？", "使ってみたいと思いますか？"],
}
TOPIC_QUESTION = {"ChatGPT", "Gemini", "Claude", "Copilot"}
DEV_RE = re.compile(r"開発|エンジニア|データ")
BUSINESS = {"ビジネス職", "経営層", "営業職", "企業のIT部門", "マーケター", "個人事業主", "会社員"}
MONEY_GENRES = {"investment", "real-estate", "business", "public"}
GENERAL = {"一般ユーザー", "クリエイター", "デザイナー"}


def _kind(article: dict) -> str:
    verdict = article.get("verdict") or {}
    audience = str(verdict.get("audience") or "")
    if verdict.get("status") == "available" and verdict.get("japan") == "no":
        return "japan_no"
    if verdict.get("free") is True and verdict.get("status") == "available":
        return "free"
    if DEV_RE.search(audience):
        return "dev"
    if audience in BUSINESS:
        return "business"
    if audience in GENERAL:
        return "general"
    if "研究" in audience:
        return "research"
    if verdict.get("status") and verdict.get("status") != "available":
        return "upcoming"
    if article.get("genreSlug") in MONEY_GENRES:
        return "money"
    return "default"


def _pick(options: list[str], seed: str, recent: list[str]) -> str:
    start = sum(ord(c) for c in seed) % len(options)
    ordered = options[start:] + options[:start]
    return next((o for o in ordered if o not in recent), ordered[0])


def editor_comment(article: dict, recent: list[str]) -> str:
    """編集部の一言（1行）。地震・津波は空"""
    if article.get("disaster"):
        return ""
    kind = _kind(article)
    options = COMMENTS.get(kind) or COMMENTS["default"]
    return PREFIX + _pick(options, article["slug"], [r.removeprefix(PREFIX) for r in recent])


def question_line(article: dict, topic: str, recent: list[str]) -> str:
    """記事に合った問いかけ（1行）。地震・津波は空"""
    if article.get("disaster"):
        return ""
    kind = _kind(article)
    options = list(QUESTIONS.get(kind) or QUESTIONS["default"])
    if topic in TOPIC_QUESTION and kind not in ("japan_no",):
        options += [q.format(topic=topic) for q in QUESTIONS["topic"]]
    return _pick(options, article["slug"][::-1], recent)


# 本文の3つ目のかたまり「読者への問いかけ」（2026-11-02 オーナー承認）。生成時の紹介文に問いかけが無いときに使う。
# 読者（仕事・お金・副業・独立に意欲のある20〜50代の働く人）が、自分の仕事・お金・副業・道具について一言で答えられる問い。
# 記事の話題（見出し・タグ）に合う規則を上から探す。問いは読者の状況を聞くだけで、記事にない事実や売買のすすめは書かない。
# 形は混ぜる: どれを使っているか／試したことがあるか／AとBならどちらか／自分の仕事に影響がありそうか
READER_QUESTIONS: list[tuple[str, list[str]]] = [
    (r"円安", ["あなたの会社や取引先では、円安の影響は出ていますか？", "円安で、仕入れや生活費の負担は増えましたか？"]),
    (r"円高", ["円高の動き、あなたの仕事に影響はありそうですか？"]),
    (r"為替|ドル円", ["為替の動き、仕事や家計で気にしていますか？"]),
    (r"NISA|iDeCo|証券|口座", ["証券口座、どこを使っていますか？NISAは始めましたか？", "NISAやiDeCo、もう使っていますか？"]),
    (r"住宅ローン|フラット35", ["住宅ローン、固定と変動のどちらにしていますか？"]),
    (r"金利|利上げ|利下げ|国債", ["金利が上がると、あなたの仕事や家計に影響はありそうですか？", "住宅ローン、固定と変動のどちらにしていますか？"]),
    (r"倒産|破たん|破綻|廃業", ["あなたの取引先で、支払いが遅れがちな会社はありますか？", "あなたの業界でも、倒産や廃業の話を聞くことが増えましたか？"]),
    (r"手形|小切手", ["あなたの会社では、まだ手形を使っていますか？"]),
    (r"値上げ|価格転嫁|販売価格|物価|インフレ", ["あなたの会社では、値上げできていますか？", "仕入れ値が上がった分、販売価格に上乗せできていますか？"]),
    (r"賃上げ|最低賃金|年収|給与|賞与|ボーナス", ["今年、あなたの会社では賃上げがありましたか？"]),
    (r"確定申告|インボイス|控除|ふるさと納税", ["確定申告、自分でやっていますか？税理士に頼んでいますか？", "ふるさと納税、今年はもう使いましたか？"]),
    (r"補助金|助成金|給付|支援金", ["補助金や助成金、仕事で使ったことはありますか？"]),
    (r"税|年金|社会保険|保険料", ["税金や社会保険のこと、自分で調べていますか？専門家に聞いていますか？"]),
    (r"副業|兼業|フリーランス|個人事業|独立|開業|起業|創業|スタートアップ", ["副業や独立、考えたことはありますか？", "いま副業はしていますか？するなら何をしたいですか？"]),
    (r"不動産|マンション|地価|路線価|家賃|賃貸|空き家", ["今の住まい、持ち家ですか？賃貸ですか？", "家を買うなら、新築と中古のどちらを選びますか？"]),
    (r"株価|株式|決算|上場|IPO|M&A|買収", ["株や経済のニュース、ふだんどこで追っていますか？"]),
    (r"(?<![A-Za-z])EV(?![A-Za-z])|電気自動車", ["次に車を買うなら、EVも候補に入りますか？"]),
    (r"マウンテンバイク|e-?MTB|自転車|ロードバイク", ["趣味の道具にかけるお金、何を重視して選びますか？", "休みの日、体を動かすなら何をしますか？"]),
    (r"バイク|二輪|オートバイ", ["バイクに乗るなら、街乗りとツーリングのどっち派ですか？"]),
    (r"ランドクルーザー|クラウン|SUV|新型車|自動車|クルマ|車種|ミニバン|軽自動車", ["車を選ぶとき、見た目と燃費ならどっちを優先しますか？", "次に車を買うなら、新車と中古のどちらにしますか？"]),
    (r"電気代|電力|電気料金|光熱費", ["夏と冬、電気代が高いと感じるのはどっちですか？", "電気代の節約、何かしていますか？"]),
    (r"ガソリン", ["車の燃料代、月にどのくらいかかっていますか？"]),
    (r"免税|旅行|観光|ホテル|航空", ["次の旅行、国内と海外ならどっちに行きたいですか？"]),
    (r"iPhone|iPad|Android|Pixel|Galaxy|スマホ", ["スマホやタブレット、いちばん使うのは仕事と私用のどっちですか？", "スマホは何年ごとに買い替えていますか？"]),
    (r"Windows|(?<![A-Za-z])Mac(?![a-z])|パソコン|PC", ["仕事のパソコン、WindowsとMacのどっちを使っていますか？"]),
    (r"イヤホン|ヘッドホン|スマートウォッチ|テレビ|家電|カメラ", ["家電やガジェット、買う前に何を重視して選びますか？"]),
    (r"ゲーム|PS5|Switch|Xbox|Steam|GeForce NOW", ["ゲームはスマホ派ですか？据え置き機派ですか？"]),
    (r"デザイン|Figma|Canva|Affinity|Photoshop|イラスト", ["資料や画像づくり、ふだん何のアプリを使っていますか？"]),
    (r"動画編集|映像|YouTube|動画生成", ["動画を作るとき、スマホとパソコンのどっちで編集しますか？"]),
    (r"音声|ナレーション|ElevenLabs|音楽", ["仕事中、音楽やラジオを聞く派ですか？"]),
    (r"集客|口コミ|店舗|マーケティング|広告", ["お店や仕事の集客、いまはどこに力を入れていますか？", "お店の口コミ、どのサイトをいちばん気にしていますか？"]),
    (r"セキュリティ|脆弱性|攻撃|不正アクセス|ランサム", ["あなたの会社では、セキュリティの見直しはいつしましたか？", "仕事のパスワード管理、何を使っていますか？"]),
]
TOOL_QUESTIONS = ["{topic}、仕事では何に使っていますか？", "{topic}は無料版と有料版、どちらを使っていますか？", "仕事でいちばん使っているAI、どれですか？"]
# 話題の規則に当たらない記事は、記事そのものの主役（製品・サービス名）で問う（判定から作った一般的な問いは、記事とずれて見えるので使わない）
# 2026-11-03 オーナー承認その2: 記事を読まなくても答えられる「読者自身の好み・経験」を聞く（「{name}、使ってみたい？」は使わない）
SUBJECT_QUESTIONS = [
    "仕事の道具を選ぶとき、何をいちばん重視しますか？",
    "新しいアプリやサービス、試すのは早いほうですか？",
    "仕事で使うツール、無料版と有料版のどちらが多いですか？",
]
# 攻撃・悪用・事故などの話題で「使ってみたい？」とは聞かない
NEGATIVE_RE = re.compile(r"悪用|攻撃|脆弱性|詐欺|不正|流出|漏えい|漏洩|障害|不具合|事故|倒産|破たん|被害|訴訟|停止|リコール")
NEGATIVE_QUESTIONS = ["仕事のパスワード管理、何を使っていますか？", "会社のトラブル対応、いちばん困るのはどんなときですか？"]
GENERIC_QUESTIONS = ["ふだんニュースは、スマホとパソコンのどっちで読みますか？"]
QUESTION_LIMIT = 40


def own_text(article: dict) -> str:
    """記事そのものの言葉（見出し・要約・要点）。タグや判定から足した言葉は含めない"""
    highlights = " ".join(str(h) for h in article.get("highlights") or [])
    return f"{article.get('title', '')} {article.get('description', '')} {highlights}"


def subjects_of(article: dict) -> list[str]:
    """記事の主役（製品・サービス・会社の名前）の候補。見出しから取る（SEO 見出しの名前 → 見出しの「」の中 → 見出しの頭）"""
    found: list[str] = []
    seo = re.split(r"[｜|：:？?]", str(article.get("seoTitle") or ""))[0].strip()
    seo = re.sub(r"(の仕組み)?を解説$|とは$|まとめ$|入門$", "", seo).strip()
    if 2 <= len(seo) <= 24 and not re.search(r"[、。]", seo):
        found.append(seo)
    title = str(article.get("title") or "")
    found += [m for m in re.findall(r"[「『]([^」』]{2,20})[」』]", title)[:1]]
    head = re.split(r"[、，,\s　]", title)[0]
    if 2 <= len(head) <= 12:
        found.append(head)
    return list(dict.fromkeys(found))


def reader_question(article: dict, topic: str, recent: list[str]) -> str:
    """本文の最後に置く、読者への問いかけ（1文・「？」で終わる）。直近の投稿で使った問いは避ける

    話題の規則は、記事の見出し・要約・要点にその言葉があるときだけ使う。無ければ記事の主役の名前で問う。
    """
    text = own_text(article)
    options: list[str] = []
    first_rule = 0  # いちばん合う規則の問いの数（この中で記事ごとに順番を変える）
    for pattern, questions in READER_QUESTIONS:
        if re.search(pattern, text):
            options += [q for q in questions if q not in options]
            first_rule = first_rule or len(options)
    if topic in TOPIC_QUESTION and topic in text and _kind(article) != "japan_no":
        options += [q.format(topic=topic) for q in TOOL_QUESTIONS]
    if not options:
        templates = NEGATIVE_QUESTIONS if NEGATIVE_RE.search(text) else SUBJECT_QUESTIONS
        options = [q for q in templates if len(q) <= QUESTION_LIMIT]
    import sns_rules  # noqa: PLC0415

    options = [q for q in dict.fromkeys(options) if not persona.ADVICE_RE.search(q) and not sns_rules.question_needs_article(q)] or GENERIC_QUESTIONS
    # 話題にいちばん合う規則の問いから、記事ごとに順番を変えて選ぶ（同じ問いが続かないように）
    head = options[: first_rule or len(options)] or options[:1]
    start = sum(ord(c) for c in article.get("slug", "")[::-1]) % len(head)
    ordered = head[start:] + head[:start] + options[len(head):]
    return next((q for q in ordered if q not in recent), ordered[0])


def want_question(index: int) -> bool:
    """通常投稿の何本目か（形式の A/B と同じ数え方）から、問いかけを付けるか。

    形式は index の偶奇で交互なので、問いかけは2本ずつ切り替える（0,0,1,1…）。
    これで hook_thread・classic のどちらにも、問いかけあり・なしが同じ数だけ入る
    """
    return (index // 2) % 2 == 1


def add_voice(text: str, comment: str, question: str, limit: int) -> str:
    """本文の最後に、空行を挟んで「編集部の一言」と「問いかけ」を足す。上限を超えるなら問いかけ→一言の順に諦める"""
    base = text.rstrip()
    if question and base.endswith(("？", "?")):
        question = ""  # もともと問いかけで終わっている本文には重ねない
    for extra in ([comment, question], [comment], [question], []):
        lines = [x for x in extra if x]
        candidate = base + ("\n\n" + "\n".join(lines) if lines else "")
        if len(candidate) <= limit:
            return candidate
    return base
