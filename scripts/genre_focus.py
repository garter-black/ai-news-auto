#!/usr/bin/env python3
"""投資・マネー／不動産／エンタメの「扱う中身」と、1日の最低本数（2026-11-03 オーナー決定）。

サイトのメニューにある 投資・マネー（1本）・エンタメ（1本）・不動産（0本）がほぼ空なのに、サイトの説明では
「お金・投資・不動産を扱う」と書いていた（点検の指摘）。オーナーの決定:
  (a) サイトの説明は、いま実際に扱っている中身に合わせて今すぐ書き直す（lib/coverage.ts。定期的に記事がある分野だけ名前を出す）
  (b) 投資・マネー、不動産、エンタメはメニューに残し、質の良い記事で埋める（このファイル）

各ジャンルで扱うもの（サイトの読者像と AdSense の品質に合うものだけ。書き写し・関係のない話題は作らない）:
  投資・マネー … 市場を動かす企業・AI 業界のニュース、NISA・iDeCo・税・手数料の変更（金融庁・国税庁・証券会社・銀行の公式発表）、
                 新しい金融商品・手数料、大手企業の決算（数字つき）。
                 **個別の投資助言・売買のすすめ・取引のやり方の説明は書かない**。短い免責文を付ける（genres.INVESTMENT.disclaimer）。
                 助言の言い回しは品質ゲート（quality.structural_problems → advice_problems）と毎日の見張り（[A22]）で落とす。
  不動産     … 国の資料・政策（国交省、地価公示・路線価、住宅の税制）、大手銀行の住宅ローン金利の改定、
                 不動産テック・不動産での AI 活用、大手デベロッパー・REIT のニュース（数字つき）。
  エンタメ   … ビジネス・テックの目線だけ: 配信サービスの料金・プラン、ゲーム業界の決算・新作の発売、エンタメでの AI 活用、
                 アニメ・音楽の産業の数字、大きなプラットフォームの規約・方針の変更。芸能のゴシップ・番組や作品の告知だけのものは扱わない。

仕組みでの置き場所:
  - 候補の振り分け（route_section）… 官公庁・企業の混ざったフィードから、投資・不動産の話題をそれぞれのレーンへ（web_articles.tag_sections）
  - 生成前の足切り（candidate_problem）… そのジャンルで扱わない候補を LLM を呼ぶ前に落とす（web_articles.broad_prefilter）
  - 検索に出す・公開してよい（entertainment_focus）… エンタメはビジネス・テックの目線のものだけ主題に入れる
    （scripts/indexnow.py の is_focus・lib/indexing.ts。値は lib/index-rules.json に書き出す）
  - 1日の最低本数（DAILY_MIN・boost_lanes）… 質の良い候補があれば、まだ最低本数に届いていないジャンルを先に回す（品質の基準は下げない）
  - 助言の言い回し（advice_problems）… 品質ゲートで作り直させる

  python3 scripts/genre_focus.py                   # 今日のジャンル別の本数と、最低本数に届いていないジャンルを表示
  python3 scripts/genre_focus.py --check-replaced  # 新しい記事が出たら古い記事を取り下げる一回きりの決まりの状態（表示だけ）
  python3 scripts/genre_focus.py --retire-replaced # 同じく、合図の記事があれば取り下げる（auto_run.yml が毎回動かす）
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import notice_gate  # お知らせ・告知・統計の転載の全体の門（2026-11-03・台帳 A26）。このファイルはその上に投資・不動産の決まった知らせを足す

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
STATE_PATH = ROOT / "content" / "state.json"

FOCUS_GENRES = ("investment", "real-estate", "entertainment")
# 投資・マネー／不動産は、文章ソース（公式フィード）でもレーンとして回す（genres.WEB_GENRES）
MONEY_GENRES = ("investment", "real-estate")

# 1日の最低本数（質の良い候補があるときだけ。無い日は 0 本でよい）。GENRE_DAILY_MIN="investment=2,real-estate=2,entertainment=1" で変えられる
DEFAULT_DAILY_MIN = {"investment": 2, "real-estate": 2, "entertainment": 1}


def daily_min() -> dict[str, int]:
    raw = os.environ.get("GENRE_DAILY_MIN", "").strip()
    out = dict(DEFAULT_DAILY_MIN)
    if raw == "off":
        return {k: 0 for k in out}
    for part in raw.split(","):
        key, _, value = part.partition("=")
        if key.strip() in out and value.strip().isdigit():
            out[key.strip()] = int(value.strip())
    return out


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


# --------------------------------------------------------------------------- #
# 各ジャンルの話題の目印
# --------------------------------------------------------------------------- #
# 投資・マネーの話題（候補がこのレーンで扱えるか）
INVESTMENT_TOPIC_RE = re.compile(
    r"(?<![A-Za-z])NISA(?![A-Za-z])|iDeCo|イデコ|つみたて|積立投資|投資信託|投信|信託報酬|(?<![A-Za-z])ETF(?![A-Za-z])|上場投資信託|(?<![A-Za-z])REIT(?![A-Za-z])|リート|ファンド|"
    r"株式|株価|株主|日経平均|(?<![A-Za-z])TOPIX(?![A-Za-z])|東証|プライム市場|グロース市場|上場|(?<![A-Za-z])IPO(?![A-Za-z])|公開買付|(?<![A-Za-z])TOB(?![A-Za-z])|株式分割|自己株式|自社株|配当|"
    r"決算|業績|増収|減収|増益|減益|最高益|営業利益|純利益|売上高|通期|上方修正|下方修正|業績予想|"
    r"金利|利上げ|利下げ|政策金利|金融政策|日銀|日本銀行|国債|債券|為替|円安|円高|ドル円|"
    r"証券|銀行|預金|金融商品|資産運用|運用状況|運用実績|年金積立金|(?<![A-Za-z])GPIF(?![A-Za-z])|暗号資産|"
    r"税制|非課税|課税|控除|手数料|口座|M&A|買収|経営統合|資本業務提携|資本提携|含み損|含み益",
    re.IGNORECASE,
)
# 不動産の話題
REAL_ESTATE_TOPIC_RE = re.compile(
    r"不動産|住宅|マンション|戸建|一戸建|分譲|地価|公示地価|基準地価|路線価|家賃|賃料|賃貸|住宅ローン|フラット35|"
    r"空き家|宅地|宅建|(?<![A-Za-z])REIT(?![A-Za-z])|リート|オフィス(?:ビル|市況|空室)|物流施設|再開発|中古住宅|リノベ|建築費|住宅取得|"
    r"固定資産税|不動産取得税|登記|借地|建物|土地|プロップテック|不動産テック",
    re.IGNORECASE,
)
# エンタメのビジネス・テックの目線（料金・プラン・決算・販売本数・興行収入・市場規模・AI・提携・規約）
ENTERTAINMENT_BUSINESS_RE = re.compile(
    r"決算|業績|売上|営業利益|純利益|増収|減収|増益|減益|過去最高|最高益|通期|上方修正|下方修正|"
    r"販売本数|出荷(?:本数|台数)|累計(?:販売|出荷)|万本|万台|興行収入|興収|動員|市場規模|産業レポート|"
    r"料金|価格改定|値上げ|値下げ|月額|年額|プラン|サブスク|定額|会員数|加入者|有料会員|"
    r"(?<![A-Za-z])AI(?![A-Za-z])|生成AI|人工知能|"
    r"買収|M&A|経営統合|資本(?:業務)?提携|業務提携|子会社化|上場|(?<![A-Za-z])IPO(?![A-Za-z])|"
    r"規約|ポリシー|方針|サービス終了|提供終了|配信終了|規制|著作権|海賊版|"
    r"\d[\d,]*\s*円",
    re.IGNORECASE,
)
# エンタメで扱わないもの（ゴシップ・番組や作品の告知だけ・グッズ・キャンペーン・ゲーム内のイベント）。題名で見る
ENTERTAINMENT_EXCLUDE_RE = re.compile(
    r"熱愛|交際|結婚|離婚|破局|不倫|妊娠|出産|炎上|スキャンダル|私服|衣装|"
    r"予告(?:編)?(?:を)?(?:公開|解禁)|キャスト|キービジュアル|PV(?:を)?公開|主題歌|出演|"
    r"コラボ(?:カフェ|グッズ|アイテム)|グッズ|ポップアップ|プレゼント|キャンペーン|ギフト|配布|"
    r"おすすめ(?:新作|ソフト)|今月の|振り返る|制作秘話|インタビュー|体験版|無料プレイ|セール|"
    r"新作ソフト情報|ゲーム内|周年記念|アップデート(?:を)?配信|"
    r"ラジオ番組|冠協賛|協賛|パートナー決定",
    re.IGNORECASE,
)

# お知らせ・統計の書き写し。全体の門は scripts/notice_gate.py（認可・受章者・議事要旨・決算発表日の予告・啓発・統計の転載）で、
# genre_focus はまずそれを使い（notice_problem）、そのうえで投資・不動産に特有の決まった知らせ（取引所の月次・銘柄の指定・
# 自己株式の取得状況・配当の支払い・行政処分など）と、不動産会社の催し・サステナビリティの知らせだけをここで足す（2026-11-04 に全体の門とそろえた）
ROUTINE_NOTICE_RE = re.compile(
    r"売買状況|マンスリー|ヘッドライン|月報|週報|日報|統計(?:表|データ)|集計結果|"
    r"(?:を|について)更新しました|掲載しました。?$|議事次第|開催について|の開催|説明会|"
    r"取得状況|自己株式の取得状況|制限値幅|監理銘柄|整理銘柄|上場廃止|売買停止|基準値段|気配|"
    r"人事|役員|組織変更|異動|行政処分|登録の取消|業務改善命令|剰余金の配当|配当金(?:の)?支払|動画を(?:公開|掲載)|"
    r"寄稿記事|資料を(?:公表|掲載)|報告書の公表|統合報告書|サステナビリティ|再エネ化|"
    r"店舗の新規開設|イベント|フェア|ナイトガーデン"
)

# 振り分け用の強い目印（混ざったフィードで、その話題がはっきり主役のときだけ動かす）
ROUTE_REAL_ESTATE_RE = re.compile(
    r"不動産|住宅ローン|フラット35|マンション|地価|公示価格|路線価|家賃|賃料|賃貸住宅|空き家|宅地建物|住宅取得|"
    r"住宅(?:政策|税制|市場|着工|価格)|分譲|戸建|(?<![A-Za-z])REIT(?![A-Za-z])|リート|オフィス(?:市況|空室)|プロップテック|不動産テック",
    re.IGNORECASE,
)
ROUTE_INVESTMENT_RE = re.compile(
    r"(?<![A-Za-z])NISA(?![A-Za-z])|iDeCo|イデコ|つみたて投資|積立投資|投資信託|信託報酬|(?<![A-Za-z])ETF(?![A-Za-z])|上場投資信託|"
    r"金融政策|政策金利|利上げ|利下げ|日経平均|(?<![A-Za-z])TOPIX(?![A-Za-z])|株価指数|東証|資本コストや株価|"
    r"金融商品|資産運用|運用状況|運用実績|年金積立金|(?<![A-Za-z])GPIF(?![A-Za-z])|暗号資産|特定口座|金融所得|証券税制|"
    r"決算|業績予想|上方修正|下方修正|配当|自己株式の取得(?!状況)|自社株買い|株式分割|公開買付|(?<![A-Za-z])TOB(?![A-Za-z])|"
    r"含み損|含み益|手数料(?:の)?(?:無料|改定|引き下げ|引下げ|ゼロ)",
    re.IGNORECASE,
)
# エンタメのビジネスの話題（配信サービス・ゲーム機・アニメ・映画の料金・決算など）。テック・ビジネスのフィードからエンタメへ動かす
ROUTE_ENTERTAINMENT_SUBJECT_RE = re.compile(
    r"(?:動画|音楽|アニメ|映像)配信|ストリーミング|Netflix|ネットフリックス|Prime Video|プライム・?ビデオ|Disney\+|ディズニープラス|"
    r"U-NEXT|ABEMA|DAZN|Lemino|Hulu|Spotify|Apple Music|Apple TV|YouTube Premium|YouTube Music|"
    r"ゲーム(?:機|ソフト|事業|会社)|Nintendo Switch|ニンテンドースイッチ|PlayStation|プレイステーション|Xbox|"
    r"アニメ(?:産業|事業|制作)|映画(?:事業|興行)|音楽(?:事業|市場)|"
    r"任天堂|カプコン|スクウェア・エニックス|バンダイナムコ|セガサミー|コナミ|コーエーテクモ|東宝|東映アニメーション|KADOKAWA|エイベックス",
    re.IGNORECASE,
)
ROUTE_ENTERTAINMENT_BUSINESS_RE = re.compile(
    r"料金|価格|値上げ|値下げ|月額|年額|プラン|決算|業績|売上|販売(?:台数|本数)|会員数|加入者|提携|買収|提供終了|サービス終了"
)
# 振り分けてよい元のセクション（官公庁・企業・研究所の混ざったフィード）。エンタメ・スポーツ・科学は動かさない
ROUTABLE_SECTIONS = ("business", "public", "lifestyle", "tech-gadget")


def notice_problem(title: str, source_slug: str = "") -> str | None:
    """お知らせ・統計の書き写しか（理由。当たらなければ None）。全体の門（notice_gate）→ 投資・不動産に特有の知らせ の順"""
    kind = notice_gate.notice_reason({"title": title, "channelSlug": source_slug})
    if kind:
        return f"お知らせ・統計の転載（{notice_gate.LABELS.get(kind, kind)}）"
    if ROUTINE_NOTICE_RE.search(_nfkc(title)):
        return "お知らせ・統計の書き写しになる発表（記事にしない）"
    return None


def route_section(item: dict, section: str | None) -> str | None:
    """候補のセクション（ジャンル slug）を、投資・マネー／不動産の話題なら付け替える。それ以外はそのまま返す。
    題名で見る（概要は補助）。不動産の目印が先（住宅ローン金利・REIT は不動産）"""
    if section not in ROUTABLE_SECTIONS:
        return section
    title = _nfkc(item.get("title", ""))
    if notice_problem(title, item.get("source_slug", "")):
        return section
    if ROUTE_REAL_ESTATE_RE.search(title):
        return "real-estate"
    if ROUTE_ENTERTAINMENT_SUBJECT_RE.search(title) and ROUTE_ENTERTAINMENT_BUSINESS_RE.search(title):
        return "entertainment"  # ゲーム・配信・アニメの会社の決算や料金はエンタメ（ビジネスの目線）
    if ROUTE_INVESTMENT_RE.search(title):
        return "investment"
    return section


def entertainment_focus(title: str, tags: list | tuple | None = None) -> bool:
    """エンタメの記事・候補がビジネス・テックの目線か（料金・決算・販売本数・市場規模・AI・提携・規約）。
    ゴシップ・告知だけの題名は外す。scripts/indexnow.py の is_focus と lib/indexing.ts が同じ判定を使う"""
    t = _nfkc(title)
    if ENTERTAINMENT_EXCLUDE_RE.search(t):
        return False
    text = f"{t} {' '.join(str(x) for x in (tags or []))}"
    return bool(ENTERTAINMENT_BUSINESS_RE.search(text))


def candidate_problem(lane: str, title: str, summary: str = "", source_slug: str = "") -> str | None:
    """生成の前に、そのジャンルで扱わない候補か（理由。扱うなら None）。投資・不動産・エンタメのレーンだけ見る"""
    t = _nfkc(title)
    text = f"{t} {_nfkc(summary)[:300]}"
    if lane in MONEY_GENRES and (why := notice_problem(t, source_slug)):
        return why  # 全体の門＋投資・不動産に特有の知らせ
    if lane == "entertainment" and (kind := notice_gate.notice_reason({"title": t, "channelSlug": source_slug})):
        return f"お知らせ・統計の転載（{notice_gate.LABELS.get(kind, kind)}）"
    if lane in MONEY_GENRES:
        topic = INVESTMENT_TOPIC_RE if lane == "investment" else REAL_ESTATE_TOPIC_RE
        if not topic.search(text):
            return f"{'投資・マネー' if lane == 'investment' else '不動産'}の話題ではない"
        return None
    if lane == "entertainment":
        if ENTERTAINMENT_EXCLUDE_RE.search(t):
            return "サイトの主題外（エンタメの告知・ゴシップ・キャンペーン。ビジネス・テックの目線ではない）"
        if not ENTERTAINMENT_BUSINESS_RE.search(text):
            return "サイトの主題外（エンタメのビジネス・テックの話題ではない。料金・決算・販売本数・AI・規約などがない）"
    return None


# --------------------------------------------------------------------------- #
# 投資助言・売買のすすめ・取引のやり方（品質ゲートと毎日の見張り）
# --------------------------------------------------------------------------- #
ADVICE_RE = re.compile(
    r"買うべき|売るべき|今が買い時|買い時です|買い時と言え|今が売り時|売り時です|仕込み時|買い増し(?:すべき|がおすすめ|を検討しましょう)|"
    r"おすすめ(?:の)?銘柄|注目銘柄|狙い目の?銘柄|有望銘柄|買い推奨|売り推奨|目標株価|押し目買い|押し目を狙|買い場(?!所)|"
    r"利益確定(?:の)?(?:タイミング|しましょう|がおすすめ)|(?<!権)利確(?!定)|損切り(?:ライン|しましょう|がおすすめ)|ナンピン|"
    r"(?:買い|売り)のエントリー|エントリーのタイミング|今すぐ(?:購入|買う|買い)|"
    r"(?:購入|買い付け|投資|乗り換え)(?:を|が)(?:おすすめ|お勧め|推奨)します|"
    r"必ず儲|確実に儲|絶対に儲|儲かります|稼げます|爆益|元本保証|損しない|知らないと損|乗り遅れるな"
)
# 取引のやり方の説明（注文の出し方・売買の手順）。どのジャンルでも書かないもの
TRADE_HOWTO_RE = re.compile(
    r"成行注文|指値注文|逆指値|(?:株|銘柄)の買い方|つなぎ売り(?:の)?(?:やり方|手順|方法|コツ)|信用取引の(?:始め方|やり方)|"
    r"テクニカル分析で(?:買|売)|(?:株|株式|銘柄|投資信託)を(?:買う|売る)(?:手順|タイミング)"
)
# 投資・マネーの記事だけで見るもの（車・家電の「注文方法」、契約の「取引のやり方」は落とさない）
TRADE_HOWTO_MONEY_RE = re.compile(
    r"(?:注文|発注)(?:方法|の出し方|手順)|売買(?:の)?(?:方法|手順|タイミング)|取引(?:の)?(?:手順|やり方)|チャートの見方|"
    r"失敗しないコツ|勝つための|賢い(?:選択基準|選び方)|一般信用と制度信用の(?:使い分け|選び方)"
)
TRADE_HOWTO_GENRES = ("investment",)
# 短い免責文（投資・マネーの記事に必ず付ける。genres.INVESTMENT.disclaimer にこの言い方を含める）
ADVICE_DISCLAIMER_MARK = "投資助言ではありません"
ADVICE_DISCLAIMER_RE = re.compile(r"投資助言(?:ではありません|にも該当しません|に該当しません|を行うものではありません)")


def _record_texts(record: dict) -> list[tuple[str, str]]:
    out = [(k, str(record.get(k) or "")) for k in ("title", "seoTitle", "lede", "description", "body", "easy")]
    out += [("highlights", str(h)) for h in record.get("highlights") or []]
    wm = record.get("workMoney") or {}
    if isinstance(wm, dict):
        out += [("workMoney", str(p)) for p in wm.get("points") or []]
    out += [("faq", f"{r.get('q', '')} {r.get('a', '')}") for r in record.get("faq") or [] if isinstance(r, dict)]
    out += [("todayActions", str(x)) for x in record.get("todayActions") or []]
    verdict = record.get("verdict") or {}
    if isinstance(verdict, dict):
        out.append(("verdict", str(verdict.get("take") or "")))
    return [(k, v) for k, v in out if v]


def advice_problems(record: dict) -> list[str]:
    """個別の投資助言・売買のすすめ・取引のやり方の説明（どのジャンルの記事でも書かない）と、
    投資・マネーの記事の免責文の抜け。品質ゲート（quality.structural_problems）が作り直させる"""
    problems = []
    for field, text in _record_texts(record):
        if m := ADVICE_RE.search(text):
            problems.append(f"{field} に投資助言・売買のすすめの言い回しがある（「{m.group(0)}」）。一般的な情報と判断材料の提示に直す")
            break
    money = record.get("genreSlug") in TRADE_HOWTO_GENRES
    for field, text in _record_texts(record):
        if (m := TRADE_HOWTO_RE.search(text)) or (money and (m := TRADE_HOWTO_MONEY_RE.search(text))):
            problems.append(f"{field} に取引のやり方の説明がある（「{m.group(0)}」）。売買の手順は書かず、制度・数字・影響の説明にする")
            break
    if record.get("genreSlug") == "investment" and not ADVICE_DISCLAIMER_RE.search(str(record.get("disclaimer") or "")):
        problems.append(f"投資・マネーの記事に短い免責文（「{ADVICE_DISCLAIMER_MARK}」）がない")
    return problems


# --------------------------------------------------------------------------- #
# 1日の最低本数
# --------------------------------------------------------------------------- #
def today_counts(state: dict | None = None, now: datetime | None = None, articles_dir: Path | None = None) -> dict[str, int]:
    """今日（日本時間）作って、いま公開中の記事のジャンル別の本数（記事 JSON の genreSlug で数える）"""
    now = now or datetime.now(JST)
    articles_dir = articles_dir or ARTICLES_DIR
    if state is None:
        try:
            state = json.loads(STATE_PATH.read_text("utf-8"))
        except (OSError, ValueError):
            state = {}
    today = now.astimezone(JST).date()
    counts: dict[str, int] = {}
    for value in (state.get("processed_web") or {}).values():
        if not (isinstance(value, dict) and value.get("slug")):
            continue
        try:
            if datetime.fromisoformat(value["at"]).astimezone(JST).date() != today:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        path = articles_dir / f"{value['slug']}.json"
        try:
            genre = json.loads(path.read_text("utf-8")).get("genreSlug") or value.get("section") or ""
        except (OSError, ValueError):
            continue  # 取り下げた記事（JSON が無い）は数えない
        counts[genre] = counts.get(genre, 0) + 1
    return counts


def boost_lanes(lanes, state: dict | None = None, now: datetime | None = None, articles_dir: Path | None = None) -> list[str]:
    """今日まだ最低本数に届いていないジャンルのうち、この回に候補があるレーン（先に回す。足りない本数が多い順）。
    候補が無ければ何もしない（質の低い記事で埋めない）"""
    mins = daily_min()
    counts = today_counts(state, now, articles_dir)
    short = [(mins[g] - counts.get(g, 0), g) for g in FOCUS_GENRES if mins.get(g, 0) > counts.get(g, 0) and lanes.get(g)]
    return [g for _, g in sorted(short, key=lambda x: (-x[0], FOCUS_GENRES.index(x[1])))]


# --------------------------------------------------------------------------- #
# 新しい記事が出たら古い記事を取り下げる、一回きりの決まり（2026-11-04 オーナー承認）
# --------------------------------------------------------------------------- #
# 投資・マネーの唯一の記事は証券会社の動画の要約で、取引のやり方まで書いていた（台帳 A22 に当たる）。今すぐ取り下げると
# メニューの「投資・マネー」が空になって消えるので、新しい質の良い投資・マネーの記事が1本公開された回に取り下げる。
# 取り下げは scripts/retire_articles.py の仕組み（content/retired.json・JSON を消す・関連/まとめ/本文のリンクを付け替え）。
# 転送先: 同じ話題（relevant_re）の公開中の記事があれば 301、無ければ 410。一度取り下げたら（JSON が無ければ）何もしない。
# 動かすのは auto_run.yml の「Retire replaced genre articles」（記事を作った回のコミットに一緒に入る）。
REPLACE_ON_FIRST = [
    {
        "genre": "investment",
        "retire": "gyakuhibu-cross-trade-complete-guide-matome",
        "since": "2026-11-03T14:05:00+00:00",  # この仕組みを入れた時刻。これより後に作った記事だけを「新しい記事」とみなす
        "reason": "investment-advice",
        "relevant_re": r"株主優待|逆日歩|つなぎ売り|信用取引|クロス取引",
    },
]


def _load_live(articles_dir: Path) -> list[dict]:
    out = []
    for path in sorted(articles_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("slug"):
            out.append(data)
    return out


def quality_replacement(article: dict, rule: dict) -> bool:
    """取り下げの合図になる「新しい質の良い記事」か: 同じジャンル・仕組みを入れた後に作った・検索に出せる（公開前の門と同じ基準）・
    お知らせの転載ではない・投資助言の言い回しがない"""
    import indexnow  # noqa: PLC0415 - indexnow が genre_focus を読み込むので、ここで読む

    if article.get("genreSlug") != rule["genre"] or article.get("slug") == rule["retire"]:
        return False
    try:
        made = datetime.fromisoformat(str(article.get("generatedAt") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if made < datetime.fromisoformat(rule["since"]):
        return False
    if indexnow.noindex_reason(article) is not None or indexnow.publish_block_reason(article) is not None:
        return False
    return not notice_gate.is_notice(article) and not advice_problems(article)


def replacement_plan(articles_dir: Path | None = None) -> dict[str, dict]:
    """いま取り下げる記事 {slug: retired.json の項目}。合図の記事がまだ無い・もう取り下げ済みなら空"""
    articles_dir = articles_dir or ARTICLES_DIR
    live = _load_live(articles_dir)
    by_slug = {a["slug"]: a for a in live}
    plan: dict[str, dict] = {}
    for rule in REPLACE_ON_FIRST:
        old = by_slug.get(rule["retire"])
        if not old:
            continue  # もう取り下げた
        fresh = sorted((a for a in live if quality_replacement(a, rule)), key=lambda a: str(a.get("generatedAt")))
        if not fresh:
            continue
        pattern = re.compile(rule["relevant_re"])
        import indexnow  # noqa: PLC0415

        related = [a for a in live if a["slug"] != old["slug"] and indexnow.noindex_reason(a) is None
                   and pattern.search(f"{a.get('title', '')} {' '.join(a.get('tags') or [])} {' '.join(a.get('keywords') or [])}")]
        related.sort(key=lambda a: str(a.get("generatedAt")), reverse=True)
        entry = {"reason": rule["reason"], "title": old.get("title", ""), "channel": old.get("channelSlug", ""),
                 "replacedBy": fresh[0]["slug"]}
        for key in ("sourceUrl", "videoUrl"):
            if old.get(key):
                entry[key] = old[key]
        if related:
            entry["redirect"] = related[0]["slug"]
        plan[old["slug"]] = entry
    return plan


def retire_replaced(apply_changes: bool = False) -> dict[str, dict]:
    """replacement_plan を retire_articles の仕組みで取り下げる（apply_changes=False は表示だけ）"""
    plan = replacement_plan()
    for slug, e in plan.items():
        where = f"/articles/{e['redirect']} へ 301" if e.get("redirect") else "410（同じ話題の記事なし）"
        print(f"[genre] 取り下げ{'' if apply_changes else '（予定）'}: {slug}（{e['title'][:40]}）→ {where} / 合図の新しい記事: {e['replacedBy']}")
    if not plan:
        waiting = [r["retire"] for r in REPLACE_ON_FIRST if (ARTICLES_DIR / f"{r['retire']}.json").exists()]
        print("[genre] 一回きりの取り下げ: " + (f"待機中（新しい質の良い記事がまだ無い）: {', '.join(waiting)}" if waiting else "済み"))
    if plan and apply_changes:
        import retire_articles  # noqa: PLC0415

        now = datetime.now(timezone.utc)
        stats = retire_articles.apply(plan, now)
        again = retire_articles.apply({}, now)  # 入れ子のリンクの付け替え残し
        stats["second_pass"] = again["articles_rewritten"] + again["data_rewritten"]
        print(f"[genre] 取り下げを実行: {json.dumps(stats, ensure_ascii=False)}")
    return plan


def main() -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retire-replaced", action="store_true", help="新しい質の良い記事が出ていれば、置き換える古い記事を取り下げる（一回きり）")
    parser.add_argument("--check-replaced", action="store_true", help="上の取り下げの予定を表示するだけ")
    args = parser.parse_args()
    if args.retire_replaced or args.check_replaced:
        retire_replaced(apply_changes=args.retire_replaced)
        return 0
    counts = today_counts()
    mins = daily_min()
    print("今日のジャンル別（公開中）: " + " / ".join(f"{g} {n}" for g, n in sorted(counts.items(), key=lambda x: -x[1])))
    for g in FOCUS_GENRES:
        print(f"  {g}: {counts.get(g, 0)} 本 / 最低 {mins[g]} 本{'  ← 先に回す' if counts.get(g, 0) < mins[g] else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
