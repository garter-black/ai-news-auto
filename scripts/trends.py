"""話題のニュース枠: 日本でいま検索・投稿されている話題を集める（ソース収集だけ）.

Googleトレンド（日本・急上昇ワード）の RSS を読み、サイトの軸
（AI・テクノロジー / 投資・マネー / 不動産）に関係する話題だけを残して、
関連報道 2〜4 本を添えた「ソースアイテム」にして返す。記事化は web_articles.py の担当。

取得元（2026-10 時点で動作確認済み）
- Googleトレンド RSS: https://trends.google.co.jp/trending/rss?geo=JP
  （.com でも同内容。1 回 10 件前後、hours/category パラメータは無視される）
- Google ニュース検索 RSS: https://news.google.com/rss/search?q=...&hl=ja&gl=JP&ceid=JP:ja
  （関連報道が 2 本未満のときの補完。リンクは news.google.com の記事ページで、
  HTTP リダイレクトでは発行元 URL に解決できない＝JS 遷移のため、そのまま保持する）
- 補助: Yahoo!リアルタイム検索の「話題のキーワード」（X の投稿ベース）。
  Googleトレンドが 10 件しかなく軸に合う話題がゼロの日が多いための補完。
  ENABLE_YAHOO_REALTIME = False で無効化できる。

フィルタは「迷ったら捨てる」。除外ルールが常に優先する（ルールは下の定数を編集）。

【トレンド記事の編集ルール】
1. 冒頭で「なぜいま話題なのか」を書く。きっかけ（発表・発売・料金改定・障害・制度変更など）を、
   集めた報道に書かれている事実だけで説明する。検索数（約N件）は「Googleトレンドで急上昇」程度に触れてよい。
2. 次に「読者にとって何が変わるのか」を書く。お金・手間・使い方・注意点など、生活や仕事への影響を具体的に。
3. 事実は渡された関連報道（sources）に書かれている内容だけを使う。数値・日付・固有名詞は報道からそのまま取り、
   文中で「〇〇（媒体名）によると」と出典を明示する。報道間で食い違う点は食い違いとして書く。
   報道にない情報を推測で補わない。分からないことは「現時点では発表されていない」と書く。
4. 個人について推測しない。人物の意図・内心・私生活・責任の所在を憶測で書かない。
   人物名は報道で公的な立場として扱われている範囲（企業の発表者など）に限る。
5. 煽らない。「衝撃」「ヤバい」「〇〇終了」「知らないと損」などの釣り見出し・不安を煽る表現は使わない。
   記事タイトルは「何が起きたか」が分かる平叙文にする（本文の章見出しは読者の疑問の形にする）。
6. サイトの「使えるかどうか」の視点（日本で今日から使えるか、料金・対応機種・手順・制限）は、
   話題がサービス・製品・制度など読者が実際に使う/申し込むものの場合だけ入れる。
   当てはまらない話題（相場の動き・業界ニュースなど）では無理に入れない。
7. 投資・マネーの話題では売買の推奨をしない。相場の先行きを断定しない。制度の話は公式情報の確認を促す。
8. 事件・事故・災害・政治・芸能・スポーツの話題に逸れない。関連報道にそうした要素が含まれていても、
   サイトの軸（テクノロジー・お金・住まい）に関係する部分だけを扱う。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

JST = timezone(timedelta(hours=9))

# 記事生成プロンプトにそのまま貼れるよう、上の docstring の編集ルール部分を切り出しておく。
EDITORIAL_RULES = (__doc__ or "").split("【トレンド記事の編集ルール】", 1)[-1].strip()

# ---------------------------------------------------------------------------
# 取得設定
# ---------------------------------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
TRENDS_RSS_URLS = [
    "https://trends.google.co.jp/trending/rss?geo=JP",
    "https://trends.google.com/trending/rss?geo=JP",
]
GNEWS_SEARCH_URL = "https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja"
YAHOO_REALTIME_URL = "https://search.yahoo.co.jp/realtime"
ENABLE_YAHOO_REALTIME = False  # Yahoo!の利用規約上の懸念があるため既定はオフ
HTTP_TIMEOUT = 15
SOURCE_MAX_AGE = timedelta(hours=48)
MIN_SOURCES = 2  # 関連報道がこれ未満の話題は記事にしない（事実の裏付けが薄いため）

HT_NS = "https://trends.google.com/trending/rss"


def _w(word: str) -> str:
    """英字の語を、英字に挟まれていないときだけマッチさせる（"AI" が "MAIL" に当たらないように）."""
    return rf"(?<![A-Za-z]){word}(?![A-Za-z])"


# ---------------------------------------------------------------------------
# 除外ルール（どれか 1 つでも当たれば捨てる。採用ルールより優先）
#   キーワード本体に当たる → 即除外
#   関連報道の見出しの半数以上に当たる → 除外
# ---------------------------------------------------------------------------
EXCLUDE_RULES: dict[str, list[str]] = {
    "芸能・エンタメ": [
        r"芸能", r"俳優", r"女優", r"アイドル", r"歌手", r"声優", r"芸人", r"タレント",
        r"主演", r"出演", r"ドラマ", r"映画", r"レッドカーペット", r"熱愛", r"交際", r"破局",
        r"結婚", r"離婚", r"不倫", r"妊娠", r"出産", r"紅白", r"オリコン", r"ライブ",
        r"コンサート", r"新曲", r"アルバム", r"写真集", r"グラビア", r"バラエティ", r"朝ドラ",
        r"大河", r"ネタバレ", r"相関図", r"アニメ", r"漫画", r"乃木坂", r"日向坂", r"櫻坂",
        r"AKB", r"STARTO", r"ジャニーズ", r"アナウンサー", r"ラヴィット", r"番組",
    ],
    "スポーツ": [
        r"野球", r"甲子園", r"サッカー", _w("J1"), r"Jリーグ", r"W杯", r"ワールドカップ",
        r"五輪", r"オリンピック", r"試合", r"(?<!万能)選手", r"監督", r"優勝", r"大相撲", r"横綱",
        r"大関", r"力士", r"ゴルフ", r"テニス", r"ボクシング", r"格闘", r"RIZIN", r"ドラフト",
        r"沢村賞", r"サイ・ヤング", _w("MLB"), _w("NPB"), _w("NBA"), r"ラグビー", r"フィギュア",
        r"競馬", r"競輪", r"マラソン", r"駅伝", r"日本シリーズ", r"本塁打", r"ホームラン",
        r"投手", r"打者", r"連敗", r"連勝", r"移籍", r"球団", r"コーチ", r"代表戦",
    ],
    "事件・事故・災害": [
        r"逮捕", r"容疑", r"送検", r"起訴", r"判決", r"裁判", r"事件", r"殺人", r"殺害",
        r"刺され", r"暴行", r"強盗", r"窃盗", r"詐欺", r"横領", r"事故", r"死亡", r"死去",
        r"訃報", r"追悼", r"悼む", r"亡くな", r"遺体", r"行方不明", r"火災", r"火事", r"地震",
        r"津波", r"台風", r"大雨", r"洪水", r"土砂", r"噴火", r"被害者", r"被災", r"避難",
        r"重体", r"重傷", r"自殺", r"虐待", r"いじめ", r"わいせつ", r"盗撮", r"性的",
        r"ハラスメント", r"謝罪", r"炎上", r"不祥事",
    ],
    "政治・選挙・国際紛争": [
        r"選挙", r"投票", r"議席", r"政党", r"自民", r"立憲", r"維新の会", r"国民民主",
        r"公明党", r"共産党", r"れいわ", r"参政党", r"総裁選", r"党首", r"支持率", r"政局",
        r"衆院", r"参院", r"国会", r"議員", r"大統領", r"官房長官", r"閣僚", r"政権", r"内閣",
        r"与党", r"野党", r"知事", r"デモ隊", r"抗議デモ", r"戦争", r"紛争", r"ミサイル", r"軍事", r"テロ",
        r"北朝鮮", r"ウクライナ", r"ガザ", r"イスラエル",
    ],
    "健康・宗教などセンシティブ": [
        r"闘病", r"入院", r"手術", r"がん(?:で|と|を|が)", r"病気", r"宗教", r"教団",
        r"旧統一教会", r"差別", r"ヘイト",
    ],
}
# キーワード本体に当たったときだけ除外する語（関連報道には市場ニュースでも普通に出るため）
EXCLUDE_KEYWORD_ONLY: dict[str, list[str]] = {
    "政治・選挙・国際紛争": [r"首相", r"総理", r"大臣", r"聯合ニュース", r"新華社"],
}
# Yahoo!リアルタイム検索のジャンル表示でそのまま除外するもの
EXCLUDE_YAHOO_GENRES = {"スポーツ", "エンタメ"}

# ---------------------------------------------------------------------------
# 採用ルール（軸ごと）
#   strong:      それだけで軸の話題とみなせる語
#   contextual:  (語, 文脈) の組。語に加えて、キーワード＋関連報道のどこかに文脈語があるときだけ有効
#                （Switch・X・LINE・楽天 のように別の意味でもよく出る語用）
# 採点: キーワードに当たれば +3、当たった関連報道の見出し 1 本につき +1。
#       ACCEPT_SCORE 以上で採用、最高点の軸に分類。同点は GENRE_PRIORITY の順。
# ---------------------------------------------------------------------------
_HW_CTX = r"本体|価格|値上げ|値下げ|発売|予約|抽選|スペック|性能|新型|後継機|ハード|在庫|販売"

AXIS_RULES: dict[str, dict[str, list]] = {
    "ai": {  # AI・テクノロジー（ガジェット・アプリ・ネットサービス・通信・生活テックを含む）
        "strong": [
            r"生成AI", _w("AI"), r"人工知能", r"ChatGPT", r"OpenAI", _w(r"GPT-?\d"), r"Gemini",
            r"Anthropic", r"Copilot", r"Perplexity", _w("Grok"), r"DeepSeek", r"Midjourney",
            r"AIエージェント", r"NVIDIA", r"エヌビディア", r"半導体",
            r"iPhone", r"iPad", r"MacBook", r"Apple Watch", r"AirPods", _w(r"iOS\s?\d*"),
            r"Android", r"Xperia", r"AQUOS", r"スマホ", r"スマートフォン", r"Windows",
            r"マイクロソフト", r"Microsoft", r"Google", r"グーグル", r"ChromeOS",
            r"Twitter", r"ツイッター", r"Threads", r"Instagram", r"インスタグラム", r"TikTok",
            r"LINEヤフー", r"LINEMO", r"PayPay", r"楽天モバイル", r"楽天ペイ", r"d払い", r"au PAY",
            r"メルペイ", r"キャッシュレス", r"ドコモ", r"docomo", r"KDDI", r"ソフトバンク",
            r"ahamo", r"povo", r"ワイモバイル", r"通信障害", r"システム障害", r"大規模障害",
            r"つながらない", r"繋がらない", r"ログインできない", r"サブスク", r"アプリ",
            _w("5G"), r"Wi-?Fi", r"Starlink", r"スターリンク", _w("PS5"), _w("PS6"),
            r"PlayStation", r"量子コンピュータ", r"ロボット", r"自動運転", r"電気自動車",
            _w("EV"), r"サイバー攻撃", r"ランサムウェア", r"不正アクセス", r"情報漏え?い",
            r"マイナ保険証", r"Kindle", r"Alexa", _w("AWS"), r"Discord", r"Slack", r"Zoom",
        ],
        "contextual": [
            (_w("Claude"), r"Anthropic|AI|モデル|Code|コード"),
            (r"Pixel", r"Google|グーグル|スマホ|発売|Android"),
            (r"Galaxy", r"Samsung|サムスン|スマホ|発売|Fold|Flip"),
            (_w("Apple") + r"|アップル", r"iPhone|Mac|発表|発売|アプリ|AI|Vision"),
            (_w("X"), r"旧Twitter|旧ツイッター|イーロン|マスク氏|ポスト|新機能|仕様変更|Grok|有料|アカウント"),
            (_w("LINE"), r"LINEヤフー|アプリ|トーク|スタンプ|機能|障害|乗っ取り|アカウント|LINE Pay"),
            (r"Switch|スイッチ", r"任天堂|Nintendo|" + _HW_CTX),
            (r"任天堂|Nintendo", _HW_CTX),
            (r"YouTube|ユーチューブ", r"機能|仕様|プレミアム|障害|アップデート|料金|広告"),
            (r"Netflix|ネットフリックス|Disney\+|ディズニープラス", r"料金|値上げ|プラン|広告|解約"),
            (r"Amazon|アマゾン", r"プライム|セール|Kindle|Alexa|AWS|配送|置き配"),
            (r"楽天", r"モバイル|ペイ|アプリ|楽天市場|通信"),
            (_w("au"), r"通信|料金|プラン|障害|スマホ|KDDI"),
            (r"Steam", r"セール|ゲーム|PC|Valve"),
            (r"テスラ|Tesla", r"EV|自動運転|ロボ|FSD|発売"),
            (r"マイナンバー", r"アプリ|スマホ|iPhone|カード|保険証|デジタル"),
        ],
    },
    "investment": {  # 投資・マネー
        "strong": [
            r"NISA", r"iDeCo", r"イデコ", r"日経平均", _w("TOPIX"), r"株価", r"株式市場", r"東証",
            r"円安", r"円高", r"ドル円", r"為替", r"金利", r"利上げ", r"利下げ", r"日銀",
            r"日本銀行", _w("FRB"), _w("FOMC"), r"インフレ", r"物価", r"投資信託", r"投信",
            r"オルカン", r"S&P\s?500", _w("ETF"), r"配当", r"株主優待", _w("IPO"), r"新規上場",
            r"ビットコイン", r"暗号資産", r"仮想通貨", r"イーサリアム", _w("BTC"), r"金価格",
            r"金相場", r"年金", r"ふるさと納税", r"確定申告", r"楽天証券", r"SBI証券",
            r"マネックス", r"証券口座", r"ポイ活", r"賃上げ", r"ストップ高", r"ストップ安",
            r"PayPay証券", r"預金金利",
        ],
        "contextual": [
            (r"決算", r"株価|増益|減益|最高益|売上|上方修正|下方修正"),
            (r"楽天", r"証券|カード|ポイント|銀行"),
            (r"銀行", r"金利|預金|口座|アプリ|手数料"),
            (r"ポイント", r"還元|改悪|ポイ活|カード|経済圏"),
            (r"増税|減税", r"所得税|消費税|住民税|控除|NISA|年収"),
            (r"NVIDIA|エヌビディア", r"株価|決算|時価総額"),
        ],
    },
    "real-estate": {  # 不動産・住まい
        "strong": [
            r"不動産", r"住宅ローン", r"マンション価格", r"新築マンション", r"中古マンション",
            r"タワマン", r"タワーマンション", r"中古住宅", r"住宅価格", r"地価", r"路線価",
            r"家賃", r"賃貸", r"固定資産税", r"空き家", r"持ち家", _w("REIT"), r"リート",
            r"住宅購入", r"住宅取得", r"フラット35", r"借地", r"マイホーム",
        ],
        "contextual": [
            (r"マンション", r"価格|販売|購入|新築|中古|分譲|管理費|修繕"),
            (r"リフォーム", r"補助金|費用|住宅|省エネ"),
            (r"住宅", r"ローン|価格|購入|補助|省エネ|断熱"),
            (r"金利", r"住宅ローン|フラット35"),  # 住宅ローン金利の話題を投資より不動産に寄せる
        ],
    },
}
ACCEPT_SCORE = 2
GENRE_PRIORITY = ["real-estate", "investment", "ai"]  # 同点時はより具体的な軸を優先

# ---------------------------------------------------------------------------
# HTTP / パース
# ---------------------------------------------------------------------------


def _get(url: str, timeout: int = HTTP_TIMEOUT) -> bytes | None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return resp.read()
    except Exception as exc:  # ネットワーク失敗は全部「取れなかった」で扱う
        print(f"[trends] fetch failed {url}: {exc}", file=sys.stderr)
        return None


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST)


def _parse_traffic(s: str | None) -> int:
    """'50,000+' / '5000+' / '2万+' → int."""
    if not s:
        return 0
    s = s.replace(",", "").replace("+", "").strip()
    m = re.match(r"([\d.]+)\s*(万|千|[KkMm])?", s)
    if not m:
        return 0
    n = float(m.group(1))
    mult = {"万": 10_000, "千": 1_000, "K": 1_000, "k": 1_000, "M": 1_000_000, "m": 1_000_000}
    return int(n * mult.get(m.group(2) or "", 1))


def _ht(el: ET.Element, tag: str) -> str:
    return (el.findtext(f"{{{HT_NS}}}{tag}") or "").strip()


def parse_trends_rss(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    trends = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        pub = _parse_date(item.findtext("pubDate"))
        news = []
        for n in item.findall(f"{{{HT_NS}}}news_item"):
            news.append({
                "title": _ht(n, "news_item_title"),
                "url": _ht(n, "news_item_url"),
                "source": _ht(n, "news_item_source"),
                "published": pub.isoformat() if pub else None,
            })
        trends.append({
            "title": title,
            "traffic": _parse_traffic(_ht(item, "approx_traffic")),
            "traffic_label": _ht(item, "approx_traffic"),
            "published_at": pub,
            "news": [n for n in news if n["title"] and n["url"]],
            "origin": "google",
            "context": [],
        })
    return trends


def fetch_trends() -> list[dict]:
    """Googleトレンド（日本）の急上昇ワード。失敗時は []."""
    for url in TRENDS_RSS_URLS:
        data = _get(url)
        if not data:
            continue
        try:
            trends = parse_trends_rss(data)
        except ET.ParseError as exc:
            print(f"[trends] RSS parse error {url}: {exc}", file=sys.stderr)
            continue
        if trends:
            return trends
    return []


def fetch_yahoo_realtime() -> list[dict]:
    """Yahoo!リアルタイム検索の「話題のキーワード」（X 投稿ベース）。失敗時は []."""
    if not ENABLE_YAHOO_REALTIME:
        return []
    data = _get(YAHOO_REALTIME_URL)
    if not data:
        return []
    m = re.search(rb'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', data, re.S)
    if not m:
        return []
    try:
        buzz = json.loads(m.group(1))["props"]["pageProps"]["pageData"]["buzzTrend"]
    except (ValueError, KeyError, TypeError):
        return []
    ts = buzz.get("buzzTimestamp")
    pub = datetime.fromtimestamp(ts, JST) if ts else datetime.now(JST)
    out, seen = [], set()
    for it in buzz.get("items", []):
        q = (it.get("query") or "").strip()
        if not q or _norm(q) in seen:
            continue
        seen.add(_norm(q))
        out.append({
            "title": q,
            "traffic": int(it.get("tweetCount") or 0),
            "traffic_label": f"{it.get('tweetCount', 0)}件の投稿",
            "published_at": pub,
            "news": [],
            "origin": "yahoo",
            "yahoo_genre": it.get("genre") or "",
            "context": list(it.get("childBuzz") or []),
        })
    return out


# ---------------------------------------------------------------------------
# 分類
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).lower()


def _first_hit(patterns: dict[str, list[str]], text: str) -> tuple[str, str] | None:
    for reason, pats in patterns.items():
        for p in pats:
            m = re.search(p, text, re.I)
            if m:
                return reason, m.group(0)
    return None


def exclusion_reason(keyword: str, context_titles: list[str], yahoo_genre: str = "") -> str | None:
    kw = unicodedata.normalize("NFKC", keyword)
    if yahoo_genre in EXCLUDE_YAHOO_GENRES:
        return f"Yahooジャンル「{yahoo_genre}」"
    hit = _first_hit(EXCLUDE_RULES, kw) or _first_hit(EXCLUDE_KEYWORD_ONLY, kw)
    if hit:
        return f"{hit[0]}（キーワードに「{hit[1]}」）"
    titles = [unicodedata.normalize("NFKC", t) for t in context_titles if t]
    if titles:
        hits = [(t, _first_hit(EXCLUDE_RULES, t)) for t in titles]
        bad = [(t, h) for t, h in hits if h]
        if len(bad) >= max(1, math.ceil(len(titles) / 2)):
            reasons = sorted({h[0] for _, h in bad})
            words = sorted({h[1] for _, h in bad})
            return f"{'/'.join(reasons)}（関連報道{len(bad)}/{len(titles)}本に「{'」「'.join(words)}」）"
    return None


def _axis_hits(rules: dict, text: str, ctx: str) -> list[str]:
    hits = []
    for p in rules["strong"]:
        m = re.search(p, text, re.I)
        if m:
            hits.append(m.group(0))
    for term, need in rules["contextual"]:
        m = re.search(term, text, re.I)
        if m and re.search(need, ctx, re.I):
            hits.append(m.group(0))
    return hits


def score_axes(keyword: str, context_titles: list[str]) -> dict[str, tuple[int, list[str]]]:
    kw = unicodedata.normalize("NFKC", keyword)
    titles = [unicodedata.normalize("NFKC", t) for t in context_titles if t]
    ctx = " ".join([kw, *titles])
    scores = {}
    for slug, rules in AXIS_RULES.items():
        score, words = 0, []
        kh = _axis_hits(rules, kw, ctx)
        if kh:
            score += 3
            words += kh
        for t in titles:
            th = _axis_hits(rules, t, ctx)
            if th:
                score += 1
                words += th
        scores[slug] = (score, sorted(set(words), key=words.index))
    return scores


def classify_explain(trend: dict) -> tuple[str | None, str]:
    """(genre slug or None, 理由). 監査ログ用."""
    keyword = trend["title"]
    titles = [n["title"] for n in trend.get("news", [])] + list(trend.get("context", []))
    ex = exclusion_reason(keyword, titles, trend.get("yahoo_genre", ""))
    if ex:
        return None, f"除外: {ex}"
    scores = score_axes(keyword, titles)
    best = max(GENRE_PRIORITY, key=lambda s: (scores[s][0], -GENRE_PRIORITY.index(s)))
    score, words = scores[best]
    if score < ACCEPT_SCORE:
        top = ", ".join(f"{s}={v[0]}" for s, v in scores.items())
        return None, f"対象外: 軸に合う語が足りない（{top}）"
    return best, f"採用: {best} score={score}（{'・'.join(words[:6])}）"


def classify(trend: dict) -> str | None:
    return classify_explain(trend)[0]


# ---------------------------------------------------------------------------
# 関連報道の収集
# ---------------------------------------------------------------------------


def _gnews_search(query: str, now: datetime) -> list[dict]:
    url = GNEWS_SEARCH_URL.format(q=urllib.parse.quote(f"{query} when:2d"))
    data = _get(url)
    if not data:
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        title = (item.findtext("title") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")].strip()
        pub = _parse_date(item.findtext("pubDate"))
        if not pub or now - pub > SOURCE_MAX_AGE:
            continue
        out.append({
            "title": title,
            "url": (item.findtext("link") or "").strip(),
            "source": source,
            "published": pub.isoformat(),
            "_dt": pub,
        })
    out.sort(key=lambda x: x["_dt"], reverse=True)
    for o in out:
        o.pop("_dt")
    return out


def gather_sources(trend: dict, max_n: int = 4) -> list[dict]:
    """Trends RSS 埋め込みの報道を優先し、2 本未満なら Google ニュース検索（48 時間以内・新しい順）で補う."""
    sources, seen = [], set()

    def add(s: dict) -> None:
        key = _norm(s["title"])[:40]
        if not s.get("url") or key in seen or s["url"] in {x["url"] for x in sources}:
            return
        seen.add(key)
        sources.append({k: s.get(k) for k in ("title", "url", "source", "published")})

    for n in trend.get("news", []):
        add(n)
    if len(sources) < 2:
        for n in _gnews_search(trend["title"], datetime.now(JST)):
            if len(sources) >= max_n:
                break
            add(n)
    return sources[:max_n]


# ---------------------------------------------------------------------------
# ソースアイテム化
# ---------------------------------------------------------------------------


def make_id(keyword: str, when: datetime | None = None) -> str:
    day = (when or datetime.now(JST)).astimezone(JST).strftime("%Y-%m-%d")
    return hashlib.sha1(f"{_norm(keyword)}|{day}".encode("utf-8")).hexdigest()


def audit(trends: list[dict]) -> list[tuple[dict, str | None, str]]:
    return [(t, *classify_explain(t)) for t in trends]


def collect(state: dict, limit: int = 10, trends: list[dict] | None = None,
            log: list | None = None) -> list[dict]:
    """採用した話題をソースアイテムにして返す（traffic 降順）.

    log にリストを渡すと、関連報道の段階で落とした話題とその理由を追記する。
    """
    processed = state.get("processed_web", {}) or {}
    if trends is None:
        trends = fetch_trends() + fetch_yahoo_realtime()
    kept = [(t, g) for t, g, _ in audit(trends) if g]
    kept.sort(key=lambda x: x[0]["traffic"], reverse=True)

    items, used_ids = [], set()
    for trend, genre in kept:
        if len(items) >= limit:
            break
        item_id = make_id(trend["title"], trend.get("published_at"))
        if item_id in processed or item_id in used_ids:
            continue
        sources = gather_sources(trend)
        if len(sources) < MIN_SOURCES:
            if log is not None:
                log.append((trend["title"], f"関連報道が{len(sources)}本しかない"))
            continue
        # 集めた報道で改めて除外チェック（Yahoo 由来など、分類時に報道がなかった話題の保険）
        ex = exclusion_reason(trend["title"], [s["title"] for s in sources])
        if ex:
            if log is not None:
                log.append((trend["title"], f"関連報道で除外: {ex}"))
            continue
        used_ids.add(item_id)
        traffic_txt = f"約{trend['traffic']:,}件の検索" if trend["origin"] == "google" \
            else f"約{trend['traffic']:,}件の投稿"
        label = "Googleトレンド急上昇" if trend["origin"] == "google" else "Yahoo!リアルタイム検索で話題"
        items.append({
            "id": item_id,
            "kind": "trend",
            "url": sources[0]["url"],
            "title": trend["title"],
            "summary": f"{label}（{traffic_txt}）。関連報道: " + " / ".join(s["title"] for s in sources),
            "published_at": trend.get("published_at") or datetime.now(JST),
            "source_name": "Googleトレンド" if trend["origin"] == "google" else "Yahoo!リアルタイム検索",
            "source_slug": "trends",
            "lang": "ja",
            "image": None,
            "genre": genre,
            "sources": sources,
            "traffic": trend["traffic"],
        })
    items.sort(key=lambda x: x["traffic"], reverse=True)
    return items


# ---------------------------------------------------------------------------
# キュー（毎時収集 → 数時間おきの記事生成で使う。API 不要）
#   python scripts/trends.py --enqueue
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
QUEUE_PATH = _ROOT / "state" / "trend_queue.json"  # content/ 以外なら Vercel の再デプロイが走らない
STATE_PATH = _ROOT / "content" / "state.json"
QUEUE_MAX_AGE = timedelta(hours=36)
QUEUE_CAP = 30


def _item_time(item: dict) -> datetime:
    for key in ("published_at", "queued_at"):
        value = item.get(key)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=JST)
        if value:
            try:
                dt = datetime.fromisoformat(str(value))
                return dt if dt.tzinfo else dt.replace(tzinfo=JST)
            except ValueError:
                continue
    return datetime.now(JST)


def load_queue() -> list[dict]:
    """期限切れ（36時間超）を除いたキュー。published_at は datetime に戻して返す。"""
    try:
        raw = json.loads(QUEUE_PATH.read_text("utf-8")).get("items", [])
    except Exception:  # noqa: BLE001 - 無い・壊れているなら空から
        raw = []
    now = datetime.now(JST)
    items = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        item["published_at"] = _item_time(item)
        if now - item["published_at"] <= QUEUE_MAX_AGE:
            items.append(item)
    return items


def save_queue(items: list[dict]) -> None:
    items = sorted(items, key=_item_time, reverse=True)[:QUEUE_CAP]
    data = {
        "updated": datetime.now(JST).isoformat(),
        "items": [{**i, "published_at": _item_time(i).isoformat()} for i in items],
    }
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def remove_from_queue(ids: set[str]) -> None:
    if ids:
        save_queue([i for i in load_queue() if i["id"] not in ids])


def enqueue(state: dict | None = None) -> tuple[int, int]:
    """新しく採用した話題をキューに足す。(追加数, キューの件数)"""
    if state is None:
        try:
            state = json.loads(STATE_PATH.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            state = {}
    queue = load_queue()
    queued = {i["id"] for i in queue}
    # 処理済み・キュー済みの話題は関連報道の取得自体を省く（速くするため）
    skip_state = {"processed_web": {**(state.get("processed_web") or {}), **{q: 1 for q in queued}}}
    now = datetime.now(JST).isoformat()
    raw = fetch_trends() + fetch_yahoo_realtime()
    try:
        save_signals(raw)
    except Exception as error:  # noqa: BLE001 - 合図が書けなくても話題キューは止めない
        print(f"[trends] 合図の書き出しに失敗: {str(error)[:120]}", file=sys.stderr)
    new = [{**i, "queued_at": now} for i in collect(skip_state, limit=10, trends=raw) if i["id"] not in queued]
    save_queue(queue + new)
    return len(new), min(len(queue) + len(new), QUEUE_CAP)


# いま検索で急上昇している話題の「合図」（サイトの軸で絞らない全件）。記事の出典にはしない。
# web_articles が公式の一次情報（官公庁・会社）の候補と照らし、公共性の高い話題（情報漏えい・ミサイル・災害など）を先に回す
# （scripts/public_interest.py・台帳 A40）。報道の本文は取らない（見出しと件数だけ）
SIGNALS_PATH = _ROOT / "state" / "trend_signals.json"
SIGNALS_MAX_AGE = timedelta(hours=24)
SIGNALS_CAP = 60


def save_signals(trends: list[dict], path: Path | None = None) -> int:
    """急上昇ワードを合図として残す（24時間・60件まで。同じ語は新しい方）。中身が同じなら書かない（コミットを増やさない）"""
    path = path or SIGNALS_PATH
    try:
        old = json.loads(path.read_text("utf-8")).get("items", [])
    except Exception:  # noqa: BLE001
        old = []
    now = datetime.now(JST)
    merged: dict[str, dict] = {}
    for sig in old:
        try:
            seen = datetime.fromisoformat(str(sig.get("seen_at")))
        except ValueError:
            continue
        if sig.get("title") and now - seen <= SIGNALS_MAX_AGE:
            merged[sig["title"]] = sig
    for t in trends:
        title = str(t.get("title") or "").strip()
        if not title:
            continue
        prev = merged.get(title)
        merged[title] = {
            "title": title,
            "traffic": max(int(t.get("traffic") or 0), int((prev or {}).get("traffic") or 0)),
            "news": [n.get("title", "") for n in (t.get("news") or [])][:3],
            "seen_at": (prev or {}).get("seen_at") or now.isoformat(),
        }
    items = sorted(merged.values(), key=lambda x: x["traffic"], reverse=True)[:SIGNALS_CAP]
    if [i["title"] for i in items] == [i.get("title") for i in old] and len(items) == len(old):
        return len(items)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"updated": now.isoformat(), "items": items}, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return len(items)


# ---------------------------------------------------------------------------
# 監査用 CLI:  python scripts/trends.py [--selftest]
# ---------------------------------------------------------------------------

SELFTEST_CASES = [  # (キーワード, 関連報道見出し, 期待する genre)
    ("サブスク", ["Meta、パーソナルAIエージェント「Muse」を日本で提供開始"], "ai"),
    ("朝青龍 明徳", ["KDDI モンゴル草原火災の経緯説明と謝罪", "モンゴル副首相、草原火災で賠償請求検討へ"], None),
    ("Switch 2", ["任天堂、Switch 2の本体価格を改定"], "ai"),
    ("スイッチ", ["人気ドラマで主演の俳優がスイッチ"], None),
    ("楽天", ["楽天イーグルス、新監督を発表"], None),
    ("新NISA", [], "investment"),
    ("住宅ローン 金利", ["変動金利、各行が引き上げ"], "real-estate"),
    ("X", ["X、旧Twitterの新機能を発表"], "ai"),
    ("日経平均", ["日経平均が最高値更新"], "investment"),
    ("iPhone", ["iPhone Duo 店頭デモ機で画面不具合", "1台3役の万能選手スマホリング"], "ai"),
]


def _selftest() -> int:
    bad = 0
    for kw, titles, want in SELFTEST_CASES:
        got, why = classify_explain({"title": kw, "news": [{"title": t} for t in titles]})
        ok = got == want
        bad += not ok
        print(f"{'OK ' if ok else 'NG '} {kw!r:18} -> {got!s:12} {why}")
    return 1 if bad else 0


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()
    if "--enqueue" in sys.argv:
        added, total = enqueue()
        print(f"# キューに {added} 件追加（合計 {total} 件）: {QUEUE_PATH}")
        return 0
    g = fetch_trends()
    y = fetch_yahoo_realtime()
    print(f"# Googleトレンド: {len(g)}件 / Yahoo!リアルタイム: {len(y)}件"
          f"（{datetime.now(JST):%Y-%m-%d %H:%M} JST）")
    if not g:
        print("# Googleトレンド RSS が空でした。Yahoo!リアルタイム検索の補完だけで判定します。")
    rows = audit(g + y)
    for t, genre, why in rows:
        mark = "KEEP" if genre else "----"
        print(f"{mark} [{t['origin']:6}] {t['title']}  ({t['traffic_label']})  {why}")
    log: list = []
    items = collect({}, trends=g + y, log=log)
    for title, why in log:
        print(f"DROP {title}  {why}")
    print(f"# 採用 {len(items)}件")
    for it in items:
        print(json.dumps(it, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
