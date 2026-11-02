"""サイソクニュースの読者像（ペルソナ）と、それに沿った書き方のルール。2026-11-02 オーナー決定。

記事（main.py / web_articles.py / angle_articles.py / evergreen.py）と SNS（threads_hooks.py / threads_post.py /
threads_voice.py）のプロンプト・判定はここを読み込む。文面を各ファイルに複製しない（変えるときはここだけ直す）。
人向けの説明は AGENTS.md「0. 読者（ペルソナ）」と docs/persona-20261102.md。
"""
from __future__ import annotations

import re
import unicodedata

BRAND = "サイソクニュース"
OLD_BRAND = "サイソクAIニュース"  # 旧名。構造化データの alternateName に残す（Google に同じ媒体だと伝える）
TAGLINE = "仕事とお金の“旬”を、どこよりも速く。"

PERSONA = {
    "who": "20〜50代の働く男性（会社員・個人事業主）。今の仕事で成果を出したいだけでなく、副収入・独立・起業・資産づくりも考えている。仕事とお金への意欲が高い。",
    "pains": [
        "忙しくてニュースを追う時間がない",
        "ニュースは多いのに「自分にどう関係するか」「何をすればいいか」が分からない",
        "気づいたときには、もう周りが動いたあと",
    ],
    "promises": [
        "最速（公式発表から最短数分）",
        "取りこぼさない",
        "3行でわかる",
        "動ける（「仕事・お金にどう活かせるか」「明日から何ができるか」まで書く）",
    ],
    "goal": "「サイソクで知れたから、すぐ動けた」",
    "topics": [
        "AI・テック",
        "ビジネス・お金・投資",
        "不動産",
        "起業・副業",
        "税金・補助金など暮らしの制度",
        "スポーツ（集客の入口。日本のスポーツの大きな結果やニュースだけ）",
    ],
    "voice": "頼れる先輩のように、落ち着いて具体的。数字と根拠を出す。あおらない。",
    "never": [
        "根拠のない儲け話",
        "あおる見出し",
        "特定の銘柄・物件を「買うべき」とすすめる個別の投資助言（一般的な情報と判断材料の提示に留める）",
    ],
}

# 伝え方（2026-11-02 オーナー決定「とにかく伝わりやすい文章」）。記事・かんたん版・work_money・SNS・はてなブログの下書きで共通
PLAIN_STYLE_EXAMPLE = (
    "ChatGPTのようなAIは、ふつうは遠くの大きなコンピューター（クラウド）で動いています。"
    "これを、車やロボット、手元の機械の中で直接動かすのが「エッジAI」です。"
)
PLAIN_STYLE = f"""## 伝え方（とにかく伝わりやすく）
- 忙しい会社員に、詳しくて頼れる先輩が話しかけるように書く。です・ます調で、少し会話っぽく（「ざっくり言うと」「つまり」「たとえば」）。
- 1文は短く（平均40字以内が目安）。1文に言いたいことは1つだけ。
- 専門用語は、初めて出たところで普通の言葉で説明するか、普通の言葉に置き換える。
  お手本:「{PLAIN_STYLE_EXAMPLE}」
- 身近な例えや、仕事・暮らしの具体的な場面を1つ入れる（例えで新しい数字や効果を足さない）。
- 合うところでは、最初に「ざっくり言うと」で1〜2文の要約を置く。
- 立場ごとの見方は、本当に関係するときだけ書く: 会社員として仕事で／副業・独立を考えている人／お金・資産づくりの面で。
- 硬い言い回しを使わない: 「〜することが可能です」（→〜できます）、「〜を見据える」、「〜において」（→〜で）、「〜となっております」、
  「〜と言えるでしょう」、「記事では〜への言及はありませんが」（→〜は書かれていません）。
- 変えるのは伝え方だけ。中身と分量は減らさない。事実・数字は元のとおりで、足さない。"""

# 硬い言い回し（はてなブログの下書きの機械チェックに使う。記事の品質ゲートには入れない）
STIFF_RE = re.compile(r"することが可能|を見据え|において|となっております|と言えるでしょう|言及はありません|に関しましては|させていただ")

# 記事生成のシステムプロンプトに足す読者像（main.SYSTEM_INSTRUCTION_BASE の末尾に入る）
SYSTEM_BLOCK = f"""## 読者（サイソクニュースの読者像。すべての原稿はこの人のために書く）
- 誰に: {PERSONA['who']}
- 困りごと: {' / '.join(PERSONA['pains'])}。
- 約束: {' / '.join(PERSONA['promises'])}。目指すのは{PERSONA['goal']}と思ってもらうこと。
- 話し方: {PERSONA['voice']}
- やらないこと: {' / '.join(PERSONA['never'])}。金融商品取引法の投資助言に当たることは書かない。
- 「何が起きたか」で終わらせず、「誰の仕事・お金に、どう関係するか」「明日から何ができるか」まで、元記事で確認できる事実の範囲で書く。
  関係が薄い話題は、無理にこじつけず「関係は小さい」と正直に書く。

""" + PLAIN_STYLE

WORK_MONEY_HEADING = "仕事・お金にどう活かせる？"

# 記事の work_money フィールドの書き方（各プロンプトの末尾に足す）
WORK_MONEY_FIELD = f"""# work_money（記事ページの「{WORK_MONEY_HEADING}」欄）の書き方
読者（働く人・副業や独立や資産づくりを考える人）が「自分はどう動けばいいか」を数秒でつかむための欄。
- relevance: 仕事・お金への関係の強さ。high（仕事のやり方・収入・支出・手続きが具体的に変わる）/ medium（一部の人に関係する）/ low（直接の関係は小さい）。
- points: relevance が high なら2〜3個、medium なら1〜2個、low なら1個。各25〜70字の1文。です・ます調。
  入れる観点（元記事で確かめられるものだけ。全部を入れなくてよい）:
  - 誰が得をするか・影響を受けるか（立場で言う。例「経理の担当者」「個人事業主」「住宅ローンを検討中の人」）
  - お金: 料金・費用・補助額・節約額・期限は、元記事に書かれた数字をそのまま使う
  - 明日からできること: 試す・設定や契約を見直す・申請期限を確かめる・社内で共有する など具体的な行動
  - 副業・独立・資産づくり: 本当に関係するときだけ（こじつけない）
- low のときは「直接の影響は小さい」と正直に書き、確認すべき人がいればその人を示す。
- 禁止: 元記事に無い数字（節約額・短縮時間・収入の見込みを自分で計算・推計しない）、「儲かる」「稼げる」「必ず」「確実」などの儲け話・断定、
  特定の銘柄・ファンド・物件の売買のすすめ、相場の先行き予想、あおり。投資・不動産の話は一般的な情報と判断材料の提示に留める。
- verdict や本文と矛盾させない（提供予定・研究段階のものを「明日から使える」と書かない）。
- この欄と easy_markdown（かんたん版）は、「伝え方」をとくに守る（短い文・専門用語は言い換え・身近な例え・硬い言い回しなし）。
  かんたん版は「## ひとことで言うと」の最初の文を「ざっくり言うと、」で始めてよい。分量は減らさない。"""

# SNS 投稿の方向（threads_hooks.POST_RULES に足す）
SNS_BLOCK = """## 読者に合わせた切り口（サイソクニュースの読者は、仕事とお金に意欲の高い働く人）
- 「何が起きたか」に加えて、「仕事・お金にどう効くか」を1行で伝える（記事の work_money・判定・本文に根拠があるときだけ）。
  例:「これ、副業の作業時間を減らせそうです」「月額料金が下がります」「明日の会議資料づくりで試せます」
- 金額・時間の数字は記事にあるものだけ。節約額や収入を自分で計算しない。
- 投資の話は、特定の銘柄・物件の売買をすすめない。相場の予想をしない。儲け話にしない。
- 伝え方: 詳しい先輩が話しかけるように。短い文で、専門用語は普通の言葉に置き換える（例「エッジAI」→「手元の機械の中で動くAI」）。
  「〜することが可能です」「〜において」などの硬い言い回しは使わない。"""

# 儲け話・投資助言・あおりの言い回し（work_money と SNS の機械チェックに使う。引用でも work_money には書かせない）
ADVICE_RE = re.compile(
    r"買うべき|売るべき|今が買い時|買い時です|今が売り時|仕込み時|買い増し(?:すべき|がおすすめ)|おすすめ銘柄|"
    r"必ず儲|確実に儲|絶対に儲|儲かります|儲かる|稼げます|稼げる|爆益|元本保証|損しない|知らないと損|乗り遅れるな"
)

# 幅広い話題（web_articles.py の --section broad）で先に回すレーンと、レーン内で先に回す話題
PRIORITY_LANES = ("business", "public")  # ビジネス・お金・不動産・起業／税金・補助金などの制度
PRIORITY_TOPIC_RE = re.compile(
    r"投資|NISA|iDeCo|株価|株式|為替|円安|円高|金利|利上げ|利下げ|日銀|日本銀行|物価|インフレ|賃上げ|最低賃金|年収|給与|賞与|"
    r"不動産|住宅|マンション|地価|路線価|家賃|賃貸|住宅ローン|空き家|"
    r"起業|創業|スタートアップ|副業|兼業|フリーランス|個人事業|独立|開業|中小企業|資金調達|IPO|上場|"
    r"税|控除|確定申告|インボイス|ふるさと納税|補助金|助成金|給付|支援金|年金|社会保険|保険料|手当|"
    r"値上げ|値下げ|料金|価格改定|決算|業績|M&A|買収",
    re.IGNORECASE,
)

# 配信用に選ぶ話題（はてなブログの下書き scripts/crosspost_drafts.py など）。スポーツは入れない
PERSONA_GENRES = ("ai", "business", "investment", "real-estate")
# 上のジャンル以外でも、お金・制度の話題（税・補助金・年金など）なら読者像に合う
PERSONA_EXTRA_GENRES = ("public", "lifestyle", "tech-gadget")


def is_persona_topic(article: dict) -> bool:
    """読者像の話題か（AI・テック／ビジネス・お金・投資／不動産／起業・副業／税金・補助金などの制度）。スポーツ・エンタメは除く"""
    genre = article.get("genreSlug") or ""
    if genre in PERSONA_GENRES:
        return True
    text = f"{article.get('title', '')} {article.get('seoTitle', '')} {' '.join(article.get('tags') or [])}"
    return genre in PERSONA_EXTRA_GENRES and bool(PRIORITY_TOPIC_RE.search(text))


def topic_boost(article: dict) -> float:
    """配信で選ぶときの加点（digest.score は AI に +30 する。お金・不動産・起業の話題も同じ土俵に乗せる）"""
    boost = 0.0
    if article.get("genreSlug") in ("business", "investment", "real-estate") or (
        article.get("genreSlug") != "ai" and is_persona_topic(article)
    ):
        boost += 30
    if (article.get("workMoney") or {}).get("relevance") == "high":
        boost += 10
    return boost


# スポーツは集客の入口。日本のスポーツの大きな結果・ニュースだけ、1日このくらいまで
SPORTS_DAILY_CAP = 4

SPORTS_SCOPE_RULE = (
    "スポーツは日本のスポーツだけを扱う: 日本のリーグ・チーム（NPB・Jリーグ・Bリーグ・大相撲・リーグワン・Vリーグ など）と、"
    "海外で活躍する日本人選手（MLBの大谷翔平、欧州サッカーの日本人選手 など）。海外の選手・リーグそのものの話題は扱わない。"
    "例外として、世界的な大会（オリンピック・パラリンピック・世界陸上・FIFAワールドカップ・WBC・ラグビーW杯・世界水泳 など）は"
    "海外選手の話題も扱う。大きな結果やニュースだけにし、小さなお知らせは扱わない。"
)

# 世界的な大会（海外選手の話題でも扱う）
GLOBAL_EVENT_RE = re.compile(
    r"オリンピック|五輪|パラリンピック|世界陸上|世界選手権|ワールドカップ|W杯|(?<![A-Za-z])WBC(?![A-Za-z])|"
    r"ワールド・ベースボール・クラシック|世界水泳|"
    r"(?<![A-Za-z])(?:Olympics?|Paralympics?|World Cup)(?![A-Za-z])",
    re.IGNORECASE,
)
# 日本のスポーツの目印（日本のリーグ・チーム・代表・海外で活躍する主な日本人選手）
JAPAN_SPORTS_RE = re.compile(
    r"日本代表|日本人|日本勢|日本選手|侍ジャパン|なでしこ|サムライブルー|SAMURAI BLUE|全日本|日本一|日本選手権|国内|"
    r"プロ野球|(?<![A-Za-z])NPB(?![A-Za-z])|セ・リーグ|パ・リーグ|日本シリーズ|甲子園|高校野球|"
    r"Jリーグ|(?<![A-Za-z])J[123](?![A-Za-z0-9])|天皇杯|皇后杯|ルヴァン|WEリーグ|Bリーグ|B\.LEAGUE|Vリーグ|SVリーグ|リーグワン|"
    r"大相撲|横綱|大関|箱根駅伝|全日本実業団|"
    r"大谷翔平|山本由伸|佐々木朗希|鈴木誠也|今永昇太|千賀滉大|菊池雄星|ダルビッシュ|吉田正尚|松井裕樹|"
    r"三笘薫|久保建英|遠藤航|冨安健洋|南野拓実|堂安律|鎌田大地|伊東純也|上田綺世|守田英正|"
    r"八村塁|渡邊雄太|河村勇輝|井上尚弥|松山英樹|大坂なおみ|錦織圭|羽生結弦|坂本花織|鍵山優真|"
    r"阿部詩|阿部一二三|北口榛花|角田裕毅|池江璃花子|張本智和|早田ひな|伊藤美誠",
    re.IGNORECASE,
)
# 海外のリーグ・団体（日本の目印が無いときは扱わない）
FOREIGN_SPORTS_RE = re.compile(
    r"(?<![A-Za-z])(?:MLB|NBA|NFL|NHL|MLS|UFC|WWE|PGA|LPGA|ATP|WTA|F1|NASCAR|UEFA)(?![A-Za-z])|メジャーリーグ|大リーグ|"
    r"プレミアリーグ|ラ・?リーガ|セリエA|ブンデスリーガ|リーグ・?アン|チャンピオンズリーグ|スーパーボウル|全米オープン|全英|全豪|ウィンブルドン|"
    r"ツール・ド・フランス|マスターズ|"
    r"ドジャース|ヤンキース|メッツ|カブス|エンゼルス|レッドソックス|パドレス|マリナーズ|ブルージェイズ|"
    r"レイカーズ|ウォリアーズ|セルティックス|レアル・?マドリード|バルセロナ|マンチェスター|リバプール|アーセナル|チェルシー|バイエルン",
    re.IGNORECASE,
)


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def sports_scope(text: str, japanese_source: bool = True) -> tuple[bool, str]:
    """スポーツの話題を扱うか（SPORTS_SCOPE_RULE）。(扱う, 理由)。

    japanese_source は発信元が日本の団体・企業か（JFA・日本卓球協会など。海外のリーグや報道なら False）。
    世界的な大会 → 扱う / 日本のリーグ・代表・日本人選手の目印 → 扱う / 海外リーグ・団体だけ → 扱わない /
    どちらの目印も無い → 発信元が日本なら扱う（日本の競技団体のお知らせ）"""
    t = _nfkc(text)
    if GLOBAL_EVENT_RE.search(t):
        return True, "世界的な大会"
    if JAPAN_SPORTS_RE.search(t):
        return True, "日本のスポーツ"
    if m := FOREIGN_SPORTS_RE.search(t):
        return False, f"海外のスポーツ（{m.group(0)}）で日本の選手・チームの話ではない"
    if japanese_source:
        return True, "日本の団体の発表"
    return False, "日本のスポーツと確認できない"


def clean_work_money(raw: object) -> dict | None:
    """生成された work_money を整える。壊れていれば None（記事ページでは欄を出さない）"""
    if not isinstance(raw, dict):
        return None
    relevance = str(raw.get("relevance", "")).strip()
    if relevance not in ("high", "medium", "low"):
        relevance = "medium"
    points = []
    for p in raw.get("points") or []:
        s = re.sub(r"\s+", " ", str(p or "")).strip().lstrip("・-*　 ").strip()
        if s and s not in points:
            points.append(s[:120])
    if not points:
        return None
    return {"relevance": relevance, "points": points[:3]}


def work_money_problems(work_money: dict | None) -> list[str]:
    """work_money の形と禁止表現のチェック（API 不要。quality.structural_problems から使う）"""
    if not work_money:
        return []
    problems = []
    points = work_money.get("points") or []
    if not 1 <= len(points) <= 3:
        problems.append(f"work_money の points は1〜3個（{len(points)}個）")
    for p in points:
        if len(p) > 90:
            problems.append(f"work_money の1項目が長い（{len(p)}字 / 70字以内）。「{p[:20]}…」")
        if m := ADVICE_RE.search(p):
            problems.append(f"work_money に儲け話・投資助言の表現がある（「{m.group(0)}」）。一般的な情報と判断材料の提示に直す")
    return problems
