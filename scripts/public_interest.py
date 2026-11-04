#!/usr/bin/env python3
"""多くの人が読む公共性の高いニュース（情報漏えい・ミサイル・災害・リコール・詐欺の注意喚起・暮らしのお金の変更）。

2026-11-04 オーナー決定（台帳 A39・A40）:
  会社の発表だけでは反応が少ない。働く人に広く読まれるニュース（不正アクセスによる大規模な流出・北朝鮮のミサイル発射・
  災害・大きなリコール・詐欺・税や年金の変更など）も、公式の一次情報があるものは拾う。
  ただし土台の決まりは変えない: 事実は官公庁・本人（会社）・警察・規制当局の発表だけ（報道は出典にしない。W31）。

このファイルが1か所で持つもの:
  1. category()            … 題名が公共性の高い話題のどれに当たるか（breach / missile / disaster / recall / scam / money）
  2. candidate_problem()   … 公共性の候補として扱わない理由（政党の申し入れ・個人の事件・採用・統計の転載・報道が出典）
  3. is_candidate()        … 公式の一次情報で、公共性の高い候補か（web_articles が「大きなニュース」として先に処理する）
  4. breaking_ok()         … 速報（すぐ SNS へ）にしてよい種類か（ミサイル・災害と、官公庁の大規模な流出の発表だけ）
  5. sensitive_problems()  … どの経路の記事にも当てる門（quality.every_path_problems から呼ぶ）:
       煽る言葉・推測の言い方・私人の実名・「分かっていること／分かっていないこと」の区別・政治の中立・公式の安全情報へのリンク
  6. autofix()             … ミサイル・災害の記事の本文の頭に、公式の安全情報（首相官邸・防衛省・国民保護ポータル・気象庁）への
       リンクを決まった形で入れる（LLM に任せない）
  7. focus_topic()         … 検索に出す主題（indexnow.is_focus・lib/indexing.ts）に公共性の高い話題を入れる目印

API を使わない・通信しない。
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


# --------------------------------------------------------------------------- #
# 1. 話題の種類（題名で判定。NFKC 後の文字に当てる）
# --------------------------------------------------------------------------- #
CATEGORY_RES: dict[str, re.Pattern] = {
    # 北朝鮮などのミサイル発射・Jアラート（防衛省「北朝鮮のミサイル等関連情報」・首相官邸の総理指示）
    "missile": re.compile(r"弾道ミサイル|ミサイル(?:の)?発射|ミサイル等関連情報|Jアラート|全国瞬時警報"),
    # 不正アクセス・情報漏えい・ランサムウェア（会社の発表・官公庁の注意喚起）
    "breach": re.compile(
        r"不正アクセス|情報漏え?い|情報漏洩|個人情報.{0,8}(?:流出|漏え?い|漏洩)|(?:流出|漏え?い)の(?:可能性|おそれ|恐れ)|"
        r"ランサムウェア|サイバー攻撃|標的型攻撃|不正ログイン"
    ),
    # 災害（地震・津波の速報そのものは scripts/quake.py の型だけ。A15。ここは大雨・台風・噴火と、被災者の手続き・支援）
    "disaster": re.compile(
        r"大雨|豪雨|線状降水帯|台風第?\d*号?|噴火|特別警報|土砂災害|避難指示|"
        r"(?:地震|豪雨|台風|災害).{0,12}(?:罹災証明|り災証明|被災者|支援|申請|特例)|罹災証明|り災証明"
    ),
    # リコール・製品事故（消費者庁の重大製品事故・国交省のリコールの届出・会社の自主回収）
    "recall": re.compile(r"リコール|自主回収|製品事故|使用(?:を)?中止|回収(?:の)?お知らせ|発火|発煙"),
    # 詐欺・なりすましの注意喚起
    "scam": re.compile(r"詐欺|フィッシング|なりすまし|悪質商法|架空請求|偽(?:の)?(?:サイト|メール|SMS)|サポート詐欺"),
    # 働く人の家計に直接かかわる制度・料金の変更（税・年金・保険料・最低賃金・電気ガス・ガソリン）
    "money": re.compile(
        r"税制改正|定額減税|所得税.{0,6}(?:改正|引き上げ|引き下げ|減税)|年金.{0,8}(?:改定|引き上げ|引き下げ|支給額|改正)|"
        r"保険料.{0,6}(?:引き上げ|引き下げ|改定)|最低賃金|電気(?:・ガス)?(?:料金|代).{0,6}(?:補助|支援|値上げ|値下げ)|"
        r"ガソリン.{0,6}(?:補助|価格|税)|物価高.{0,4}対策|給付金"
    ),
}
# 地震・津波の発生そのものの記事は LLM に書かせない（A15。scripts/quake.py）
QUAKE_REPORT_RE = re.compile(r"(?:震度\d|を震源とする地震|津波(?:警報|注意報)|大津波)")
# 選び方の調整値は scripts/public_interest_rules.json（後から重みを変えやすいように、コードに書き込まない）
RULES_PATH = ROOT / "scripts" / "public_interest_rules.json"
DEFAULT_RULES = {
    "daily_cap": 4,
    "priority": {"missile": 0, "disaster": 1, "breach": 2, "scam": 3, "money": 4, "recall": 5},
    "kinds": {"missile": ["gov"], "disaster": ["gov"], "scam": ["gov"], "money": ["gov"],
              "breach": ["gov", "official", "company"], "recall": ["gov", "official", "company"]},
    "breaking": ["missile", "disaster"],
    "trend_first": True,
    "short_body": 800,
    "min_source_chars": 1200,
}


def load_rules(path: Path | None = None) -> dict:
    """調整値（読めない・壊れているときは既定値。記事づくりを止めない）"""
    try:
        data = json.loads((path or RULES_PATH).read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    return {**DEFAULT_RULES, **{k: v for k, v in data.items() if k in DEFAULT_RULES}}


RULES = load_rules()
# 同じ実行で先に作る順（既定: 命と安全 → 多くの人の個人情報 → 詐欺 → お金 → リコール）
PRIORITY: dict[str, int] = {str(k): int(v) for k, v in RULES["priority"].items()}
# 速報（すぐ SNS へ）にしてよい種類（S2・S15。災害・ミサイルは遅れると読者が困る）
BREAKING_CATEGORIES = tuple(RULES["breaking"])
# 1日に公共性の枠で作る本数（Claude API の月の上限は変えず、この本数で抑える）
DAILY_CAP = int(os.environ.get("PUBLIC_INTEREST_DAILY_CAP") or RULES["daily_cap"])
TREND_FIRST = bool(RULES["trend_first"])
# 流出・リコール・詐欺・お金の候補は、資料（公式の発表の本文）がこの字数より短ければ書かない（本文の下限に届かず、作り直しで費用だけ使うため。
# ミサイル・災害は本文の下限が短いので対象外）
MIN_SOURCE_CHARS = int(RULES["min_source_chars"])


def source_too_short(cat: str, text: str) -> bool:
    return cat not in ("missile", "disaster") and len(text or "") < MIN_SOURCE_CHARS


def category(title: str, summary: str = "") -> str | None:
    """題名が当たる公共性の話題の種類。当たらなければ None。
    概要は見ない（概要の一言で寄贈・見学ツアー・大臣会見が「災害」「流出」になったため。2026-11-04 の試し）"""
    text = _nfkc(title)
    for name, pattern in CATEGORY_RES.items():
        if pattern.search(text):
            return name
    return None


# 種類ごとに、一次情報として扱う発信元の種類（feeds.py の kind）。
# 流出・リコールは会社自身の発表も一次情報。ミサイル・災害・詐欺・制度は官公庁の発表だけ（会社の分析記事・調査会社のコラムは外す）
CATEGORY_KINDS = {str(k): tuple(v) for k, v in RULES["kinds"].items()}


# --------------------------------------------------------------------------- #
# 2. 候補として扱わないもの
# --------------------------------------------------------------------------- #
# 政党・議員の申し入れ・提言・選挙（政治は中立。官房長官が政党の提言を受けた、のような記事は作らない）
POLITICS_RE = re.compile(
    r"自民党|自由民主党|立憲民主党|日本維新の会|維新の会|公明党|国民民主党|共産党|れいわ新選組|参政党|社民党|日本保守党|"
    r"申入れ|申し入れ|緊急提言|提言を受け|選挙|候補者|立候補|政党|議員連盟"
)
# 個人の事件・事故（私人の名前・推測を避ける。会社の発表・官公庁の注意喚起だけを扱う）
PERSONAL_CRIME_RE = re.compile(r"逮捕|容疑者|被告|送検|起訴|判決|殺人|殺害|死亡事故|遺体|行方不明|書類送検|指名手配")
# 採用・会議・訓練・叙勲など、公共の話題の語を含んでも読者の役に立たない告知
NOT_NEWS_RE = re.compile(
    r"採用|募集|職員|任期付|会議|審議会|検討会|委員会|部会|議事|訓練|演習|叙勲|褒章|表彰|講演|セミナー|説明会|研修|"
    r"月間|週間|キャンペーン|ポスター|動画を公開|入札|公募|会見要旨|記者会見|人事|掲載しました|"
    # 定例の統計の公表（A26）
    r"認知・検挙状況|状況等について|統計|速報値|確定値|件数の推移"
)
# 報道機関（W17・W31）。報道が出典の候補は回さない（公式の発表が出てから）
OUTLET_HOST_RE = re.compile(
    r"(?:nhk\.or\.jp|nikkei\.com|asahi\.com|yomiuri\.co\.jp|mainichi\.jp|sankei\.com|jiji\.com|kyodonews|tbs\.co\.jp|"
    r"fnn\.jp|news\.ntv|tv-asahi|itmedia\.co\.jp|mynavi\.jp|impress\.co\.jp|bloomberg|reuters|yahoo\.co\.jp|"
    r"news\.google|gori\.me|9to5|cnn\.|bbc\.)",
    re.I,
)
# 公式の一次情報とみなすフィードの種類（feeds.py の kind）。配信（wire）・他社の告知（derivative）は除く
PRIMARY_KINDS = ("gov", "official", "company")


# 対策の製品・サービスの宣伝（「サイバー攻撃対策サービスを提供開始」「発火しにくい電池」）は事件の発表ではない
PRODUCT_RE = re.compile(r"ソリューション|提供開始|発売|導入|製品化|サービスを|プラットフォーム|対策(?:サービス|製品|ソフト)|と提携|協業|しにくい|を防ぐ|防止機能")
INCIDENT_RE = re.compile(r"注意喚起|お詫び|おわび|お知らせ|発生|被害|判明|可能性|届出|発射|指示|警戒|呼びかけ")


def _outlet_hosts() -> list[str]:
    """速報レーンのレーダーが見る報道機関（scripts/fast_lane_rules.json の radar.outlets）"""
    try:
        rules = json.loads((ROOT / "scripts" / "fast_lane_rules.json").read_text("utf-8"))
        return [str(o["host"]).lower() for o in (rules.get("radar") or {}).get("outlets") or [] if o.get("host")]
    except (OSError, ValueError, KeyError):
        return []


OUTLET_HOSTS = _outlet_hosts()


def is_press_url(url: str) -> bool:
    """報道機関・ニュースブログの URL か（W31: 記事の出典にしない）"""
    from urllib.parse import urlsplit  # noqa: PLC0415

    try:
        host = urlsplit((url or "").strip()).netloc.lower()
    except ValueError:
        return False
    if any(host == h or host.endswith("." + h) for h in OUTLET_HOSTS):
        return True
    return bool(host and OUTLET_HOST_RE.search(host))


def press_source_problems(record: dict) -> list[str]:
    """（2026-11-04 W31 の全経路化）記事の出典が報道機関・ニュースブログ・話題の報道まとめ（trends）なら不合格。
    事実は官公庁・会社自身など公式の一次情報だけ。報道しか無い話題は、公式の発表が出るまで書かない。
    調査会社・情報会社（A37。東京商工リサーチなど）の自社データの記事はオーナーの決定どおり「報道」の札で扱い、ここでは止めない"""
    channel = str(record.get("channelSlug") or "")
    url = str(record.get("sourceUrl") or "")
    if channel == "trends" or (channel.startswith("radar-") and channel != "radar-official") or is_press_url(url):
        return [f"報道機関の記事が出典（{channel or url[:60]}）。事実は公式の一次情報だけ（W31）。公式の発表が出てから書く"]
    return []


def candidate_problem(title: str, summary: str = "", url: str = "") -> str | None:
    """公共性の候補として扱わない理由（文字列）か None"""
    text = _nfkc(f"{title} {summary[:200]}")
    head = _nfkc(title)
    if PRODUCT_RE.search(head) and not INCIDENT_RE.search(head):
        return "対策の製品・サービスの宣伝（事件・注意喚起の発表ではない）"
    if POLITICS_RE.search(text):
        return "政治（政党・申し入れ・選挙）は扱わない"
    if PERSONAL_CRIME_RE.search(text):
        return "個人の事件・事故は扱わない"
    if NOT_NEWS_RE.search(head):
        return "告知（採用・会議・訓練・表彰など）"
    if QUAKE_REPORT_RE.search(head) and not re.search(r"罹災|り災|支援|申請|特例|被災者", head):
        return "地震・津波の発生の記事は scripts/quake.py の型だけ（A15）"
    if url and is_press_url(url):
        return "報道が出典（公式の発表を待つ。W31）"
    return None


def is_candidate(item: dict, kind: str = "") -> bool:
    """公式の一次情報で、公共性の高い候補か（web_articles が大きなニュースとして先に処理する）。
    kind は feeds.feed_kind(item)。配信・他社の告知・報道・レーダーの報道は対象外"""
    kind = kind or item.get("kind") or ""
    if kind not in PRIMARY_KINDS:
        return False
    if item.get("radar") or item.get("secondary"):
        return False
    title, summary = item.get("title") or "", item.get("summary") or ""
    cat = category(title, summary)
    if not cat or kind not in CATEGORY_KINDS.get(cat, ()):
        return False
    return candidate_problem(title, summary, item.get("url") or "") is None


# 手続き・支援の案内（罹災証明の申請・支援金・特例など）。災害の話題でも速報にはしない
PROCEDURE_RE = re.compile(r"罹災証明|り災証明|申請|支援|特例|手続|給付|貸付|相談窓口|受付")


def breaking_ok(item: dict, kind: str = "") -> bool:
    """速報にしてよいか。ミサイル・災害（官公庁の発表）だけ。流出・リコール・詐欺は通常の記事（すぐ出すが【速報】にはしない）"""
    title = _nfkc(item.get("title") or "")
    if PROCEDURE_RE.search(title):
        return False  # 被災者の手続き・支援の案内は急ぎの速報ではない（2026-11-04 の初回で罹災証明の案内が速報になった）
    return (kind or item.get("kind")) == "gov" and category(title, "") in BREAKING_CATEGORIES


def done_today(state: dict, now: datetime | None = None) -> int:
    """今日（JST）公共性の枠で作った本数（processed_web の publicInterest の印）"""
    today = (now or datetime.now(JST)).astimezone(JST).date()
    n = 0
    for rec in (state.get("processed_web") or {}).values():
        if not isinstance(rec, dict) or not rec.get("publicInterest") or not rec.get("slug"):
            continue
        try:
            at = datetime.fromisoformat(str(rec.get("at") or ""))
        except ValueError:
            continue
        if (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(JST).date() == today:
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Googleトレンドの合図（scripts/trends.py が state/trend_signals.json に書く。記事の出典にはしない）
# --------------------------------------------------------------------------- #
SIGNALS_PATH = ROOT / "state" / "trend_signals.json"


def load_signals(path: Path | None = None) -> list[dict]:
    try:
        return list(json.loads((path or SIGNALS_PATH).read_text("utf-8")).get("items") or [])
    except (OSError, ValueError):
        return []


def trend_hit(title: str, signals: list[dict]) -> dict | None:
    """公式の候補の題名が、いま検索で急上昇している話題（キーワード）を含むか。含めばその合図"""
    text = _nfkc(title)
    for sig in signals:
        word = _nfkc(str(sig.get("title") or "")).strip()
        if len(word) >= 2 and word in text:
            return sig
    return None


PROMPT_NOTES = {
    "breach": "不正アクセス・情報漏えいの記事です。発表した組織・漏えいした可能性のある情報の種類と件数・発覚の日・対象者への連絡と問い合わせ先・"
              "読者が今できること（パスワードの変更・不審なメールへの注意など、資料にあるもの）を書く。",
    "missile": "ミサイル発射の記事です。発射の時刻・落下の推定地点・日本の領域や EEZ への飛来の有無・被害の確認状況・政府の指示を、"
               "防衛省・首相官邸の発表のとおりに書く。軍事の評価・今後の予想・外交の意見は書かない。",
    "disaster": "災害の記事です。被害の状況・自治体や国の支援・手続き（罹災証明の申請など）を発表のとおりに書く。被害の大きさを推測しない。",
    "recall": "リコール・製品事故の記事です。対象の製品名・型番・期間・台数・起きた事故・持っている人がすること（使用中止・無償修理の申し込み先）を書く。",
    "scam": "詐欺・なりすましの注意喚起の記事です。手口・被害の傾向（資料にある数字だけ）・見分け方・相談先（警察相談専用電話 #9110・消費者ホットライン 188 など資料にあるもの）を書く。",
    "money": "暮らしのお金の制度・料金の変更の記事です。何が・いつから・誰に・いくら変わるかと、自分の場合の確かめ方を書く。",
}


def prompt_note(cat: str) -> str:
    """公共性の枠の候補に足す書き方（事件・災害・政治。台帳 A39）"""
    return (
        "\n\n## 公共性の高いニュースの書き方（必ず守る）\n"
        f"- {PROMPT_NOTES.get(cat, '')}\n"
        "- 事実は資料（公式の発表）にあることだけ。報道・SNS の情報は書かない。推測・噂・「おそらく」は書かない。\n"
        "- 確かなこと（発表されたこと）と、まだ分かっていないこと（調査中・現時点では公表されていない）を分けて書く。\n"
        "- 私人の名前は書かない。煽る言葉（衝撃・恐怖・パニックなど）を使わず、落ち着いた報道の文体で書く。\n"
        "- 政治は中立に書く（政党・政権を評価しない）。\n"
        "- 未確定のことは「## まだ分からないこと」の章にまとめてよい（この章は見出しの数に数えない）。\n"
        "- 資料が短いときは短い記事でよい。資料に無い背景・一般論で水増ししない。"
    )


# --------------------------------------------------------------------------- #
# 5. どの経路の記事にも当てる門（台帳 A39）
# --------------------------------------------------------------------------- #
SENSATIONAL_RE = re.compile(r"衝撃|恐怖|戦慄|震撼|ヤバ[いイ]|緊迫の|パニック|大惨事|最悪の事態|一触即発|戦争前夜|絶望的|驚愕|閲覧注意")
# 推測の言い方（公式の「可能性があるもの」「推定」は発表の言葉なので許す）
SPECULATION_RE = re.compile(r"おそらく|と思われ|に違いない|ではないか(?:と|。)|憶測|噂では|うわさでは|と推測され|とささやかれ")
# 私人の実名（容疑者・被告の名前、「〇〇さん（34）」の形）
PRIVATE_NAME_RE = re.compile(
    r"[一-龥]{1,4}\s?[一-龥ぁ-ん]{1,4}(?:容疑者|被告(?:人)?|受刑者)|(?:容疑者|被告)\s?[（(]\d{1,3}[）)]|"
    r"[一-龥ぁ-んァ-ン]{1,6}さん\s?[（(]\d{1,3}(?:歳)?[）)]"
)
# 分かっていないことを書いた目印（流出・ミサイル・災害・詐欺の記事は、確かなことと未確定のことを分けて書く）
UNKNOWN_RE = re.compile(
    r"(?:分かって|わかって|明らかになって|明らかにして|確認されて|公表されて|発表されて|示されて)(?:い|おり)?(?:ません|ない)|"
    r"調査中|確認中|分析中|現時点で|現時点では|今後の調査|続報|未定"
)
UNKNOWN_CATEGORIES = ("breach", "missile", "disaster", "scam")
# 対策の手引き・相談先の案内（起きた事件の記事ではない）は「分かっていないこと」の区別を求めない
GUIDE_RE = re.compile(r"対策を公開|対処法|手引き|ガイド|チェックリスト|相談先を|見分け方|注意点を")
# 政治の意見（政党・政権を持ち上げる・攻める言葉）
POLITICAL_OPINION_RE = re.compile(r"暴挙|愚策|失政|弱腰|売国|英断|許しがたい|断じて許|無責任な政府|政府の怠慢|評価すべき|称賛に値")
# ミサイル・災害の記事に必ず置く公式の安全情報
SAFETY_LINKS = {
    "missile": [
        ("首相官邸（北朝鮮のミサイル発射など）", "https://www.kantei.go.jp/"),
        ("防衛省「北朝鮮のミサイル等関連情報」", "https://www.mod.go.jp/j/press/news/index.html"),
        ("内閣官房 国民保護ポータルサイト（弾道ミサイル落下時の行動）", "https://www.kokuminhogo.go.jp/"),
    ],
    "disaster": [
        ("気象庁 防災情報（警報・注意報・キキクル）", "https://www.jma.go.jp/bosai/"),
        ("首相官邸（災害・危機管理情報）", "https://www.kantei.go.jp/"),
        ("内閣府 防災情報のページ", "https://www.bousai.go.jp/"),
    ],
}
SAFETY_HOST_RE = re.compile(r"kantei\.go\.jp|mod\.go\.jp|kokuminhogo\.go\.jp|jma\.go\.jp|bousai\.go\.jp|fdma\.go\.jp")
SAFETY_MARK = "安全に関する公式情報"


def record_category(record: dict) -> str | None:
    """記事の公共性の種類。publicInterest の印（公共性の枠で作った記事）が先。
    印の無い記事（速報レーン・レーダー・書き直しなど別の経路）は題名で見る。ただしミサイル・災害以外は、事件の発表の形
    （発生・被害・判明・可能性・お詫び・注意喚起など）の題名だけ（「ランサムウェア対策の新機能」のような製品の記事を止めない）"""
    mark = record.get("publicInterest")
    if isinstance(mark, str) and mark in CATEGORY_RES:
        return mark
    title = str(record.get("title") or "")
    cat = category(title)
    if cat in ("missile", "disaster"):
        return cat if candidate_problem(title) is None else None
    if cat and INCIDENT_RE.search(_nfkc(title)) and not PRODUCT_RE.search(_nfkc(title)):
        return cat
    return None


def _prose(record: dict) -> str:
    parts = [str(record.get(k) or "") for k in ("title", "seoTitle", "lede", "body", "easy")]
    parts += [str(x) for x in (record.get("highlights") or [])]
    return "\n".join(parts)


def sensitive_problems(record: dict) -> list[str]:
    """公共性の高い話題・事件・政治の記事の門。どの経路の記事にも当てる（quality.every_path_problems）。
    当てはまらない記事（ふつうの製品の発表など）は、私人の実名と政治の意見だけを見る"""
    text = _nfkc(_prose(record))
    body = _nfkc(str(record.get("body") or ""))
    problems: list[str] = []
    cat = record_category(record)
    if m := PRIVATE_NAME_RE.search(text):
        problems.append(f"私人の実名を書かない（「{m.group(0)[:20]}」）。事件・事故は会社・官公庁の発表の範囲で、個人を特定しない")
    if POLITICS_RE.search(text) and (m := POLITICAL_OPINION_RE.search(text)):
        problems.append(f"政治は中立に書く（政党・政権を評価する言葉「{m.group(0)}」を使わない）")
    if not cat:
        return problems
    if m := SENSATIONAL_RE.search(text):
        problems.append(f"不安を煽る言葉を使わない（「{m.group(0)}」）。起きたことと公式の発表を落ち着いて書く")
    if m := SPECULATION_RE.search(text):
        problems.append(f"推測で書かない（「{m.group(0)}」）。公式の発表にあることだけを書き、分からないことは分からないと書く")
    if cat in UNKNOWN_CATEGORIES and body and not GUIDE_RE.search(_nfkc(str(record.get("title") or ""))) and not UNKNOWN_RE.search(body):
        problems.append("確かなことと、まだ分かっていないこと（調査中・現時点では公表されていない など）を分けて書く")
    if cat in SAFETY_LINKS and not SAFETY_HOST_RE.search(body):
        problems.append("ミサイル・災害の記事は、本文に公式の安全情報（首相官邸・防衛省・国民保護ポータル・気象庁）へのリンクを置く")
    return problems


# --------------------------------------------------------------------------- #
# 6. 公式の安全情報のリンクを決まった形で入れる
# --------------------------------------------------------------------------- #
def safety_block(cat: str) -> str:
    links = "・".join(f"[{name}]({url})" for name, url in SAFETY_LINKS[cat])
    return f"> {SAFETY_MARK}: {links}"


def autofix(record: dict) -> list[str]:
    """ミサイル・災害の記事の本文の頭に、公式の安全情報のリンクを入れる（すでにあれば何もしない）。行ったことの説明を返す"""
    cat = record_category(record)
    if cat not in SAFETY_LINKS:
        return []
    body = str(record.get("body") or "")
    if SAFETY_MARK in body:
        return []
    record["body"] = f"{safety_block(cat)}\n\n{body}" if body else safety_block(cat)
    return [f"公式の安全情報のリンクを本文の頭に入れた（{cat}）"]


# --------------------------------------------------------------------------- #
# 7. 検索に出す主題（indexnow.is_focus・lib/indexing.ts。lib/index-rules.json に書き出す）
# --------------------------------------------------------------------------- #
# 公共性の話題を主題に入れるジャンル（社会・行政／暮らし／テクノロジー・ガジェット／ビジネス）
FOCUS_GENRES = ("public", "lifestyle", "tech-gadget", "business")
FOCUS_TOPIC_RE = re.compile("|".join(f"(?:{p.pattern})" for p in CATEGORY_RES.values()))


# ミサイル・災害の記事は公式の発表そのものが短い（防衛省の続報は700字ほど）。水増しせずに出せるよう、本文の下限を下げる
# （地震・津波の型の記事 disaster と同じ考え。ほかの種類は通常の下限 indexnow.THIN_BODY のまま）
SHORT_BODY = int(RULES["short_body"])
SHORT_TOPIC_RE = re.compile(f"(?:{CATEGORY_RES['missile'].pattern})|(?:{CATEGORY_RES['disaster'].pattern})")


def short_ok(article: dict) -> bool:
    """本文の下限を SHORT_BODY に下げてよい記事か（ミサイル・災害の題名。lib/indexing.ts の isShortPublicInterest と同じ）"""
    if (article.get("genreSlug") or "") not in FOCUS_GENRES:
        return False
    return bool(SHORT_TOPIC_RE.search(_nfkc(f"{article.get('title', '')} {article.get('seoTitle', '')}")))


def focus_topic(article: dict) -> bool:
    """公共性の高い話題の記事か（検索に出す主題に入れる。lib/indexing.ts の isPublicInterest と同じ）"""
    if (article.get("genreSlug") or "") not in FOCUS_GENRES:
        return False
    text = _nfkc(f"{article.get('title', '')} {article.get('seoTitle', '')} {' '.join(article.get('tags') or [])}")
    return bool(FOCUS_TOPIC_RE.search(text))
