#!/usr/bin/env python3
"""記事を作る前に「確かな事実の束」を組み立てる（2026-11-03 の比較調査の1位・大きく作るもの1・2・4・7）。

LLM に覚えている知識で書かせるのではなく、プログラムで集めた確かなデータを渡し、LLM には並べる役だけを任せる。

束に入れるもの（どれも出典と確認日つき）:
- 料金の段の表（scripts/pricing.py の content/pricing/*.json。無ければ当サイトの料金ガイド content/howto/*-pricing.json）
- 当サイトの使い方・料金ガイド（content/howto/*.json。結論・無料でできること・日本での扱い）
- 同じ会社・製品の最近の過去記事（60日以内）の主な数字
- 元記事の注記（「（注1）SBI証券は単独口座数非開示…」のように読み落としやすい但し書き）
- 元記事の中のリンク先（同じ公式ドメインの料金ページ・ヘルプ・資料。お金・制度の記事では公的なページ）を最大2本（scripts/source_links.py）
- 為替（ドルの金額の円の目安。レートと日付つき）
- 計算済みの数字（scripts/calc_facts.py。ペース・年換算・差・割合・1つあたり・円換算。式つき）
日本での提供状況は、今までどおり scripts/japan_availability.py の欄で渡す（ここでは出典の一覧にだけ使う）。

使い方（scripts/web_articles.py の compose）:
  bundle = facts_bundle.build(item, text, related, lane, page)
  prompt += bundle.prompt_block()        # 生成に渡す
  check_context += bundle.check_block()  # 事実確認（quality.fact_check）に渡す
  record["_facts"] = bundle.meta()       # 品質ゲートの数字の照合・出典の照合（書き出す前に外す）
  record["factSources"] = sources(record, bundle)  # 記事末の「出典と更新履歴」に出す

  python3 scripts/facts_bundle.py URL [--lane business]   # その記事の束を表示（試し。LLM は呼ばない）
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive  # noqa: E402
import calc_facts  # noqa: E402
import japan_availability  # noqa: E402
import source_links  # noqa: E402

try:  # 料金データ（content/pricing/*.json）。別の担当が作る。無い環境では料金ガイドで代わりにする
    import pricing  # noqa: E402
except Exception:  # noqa: BLE001
    pricing = None

ROOT = Path(__file__).resolve().parent.parent
HOWTO_DIR = ROOT / "content" / "howto"
ARTICLES_DIR = ROOT / "content" / "articles"
JST = timezone(timedelta(hours=9))
PAST_DAYS = 60
MAX_PAST = 4
PROMPT_CAP = 5000  # 生成に渡す束の長さの上限（無料枠のモデルに収める）
# 欄ごとの長さの上限（プロンプトに出す分）
GROUP_CAP = {"料金": 1800, "他社との比較": 1400, "使い方ガイド": 700, "関連ページ": 800, "公的資料": 1000, "過去記事": 600, "元記事の注記": 600, "為替": 200}
CHECK_CAP = 6000
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

PRICE_TOPIC_RE = re.compile(r"料金|価格|値段|プラン|月額|年額|値上げ|値下げ|サブスク|pricing|price|plans?\b|subscription|tier|\$\s?\d|ドル", re.I)
PLAN_WORDS_RE = re.compile(r"プラン|サブスク|月額|plans?\b|subscription|Pro\b|Plus\b|Ultra\b|Max\b|Team\b|個人向け", re.I)
# お金・税・制度の記事（章立てを「何が変わる→いつから→対象→早見表→確かめ方→FAQ」にし、公的な資料を1本取りに行く）
MONEY_TOPIC_RE = re.compile(
    r"税|控除|年金|給付|補助金|助成金|支援金|手当|保険料|NISA|iDeCo|確定申告|年末調整|源泉|インボイス|社会保険|医療費|"
    r"ふるさと納税|最低賃金|奨学金|児童手当|マイナ(?:ンバー|保険証)|住民税|所得税|消費税|相続|贈与|リファンド"
)
MONEY_LANES = {"public", "investment", "business", "real-estate", "lifestyle"}
COMPANY_NAMES = {
    "anthropic": "Anthropic", "openai": "OpenAI", "google": "Google", "microsoft": "Microsoft", "perplexity": "Perplexity",
    "xai": "xAI", "meta": "Meta", "nvidia": "NVIDIA",
}
FOOTNOTE_RE = re.compile(r"^\s*(?:（注\s?\d+）|\(注\s?\d+\)|注\s?\d+[:：.]|※\s?\d*|\*\s?\d+)\s*\S")


def md(date: str) -> str:
    """2026-11-03 → 11/3"""
    m = re.match(r"\d{4}-(\d{2})-(\d{2})", date or "")
    return f"{int(m.group(1))}/{int(m.group(2))}" if m else (date or "")


@dataclass
class Group:
    key: str
    label: str  # 料金 / 使い方ガイド / 過去記事 / 元記事の注記 / 関連ページ / 公的資料 / 為替
    name: str  # 出典の名前（記事の出典欄にも出す）
    url: str
    checked: str  # YYYY-MM-DD
    body: str
    marks: list[str] = field(default_factory=list)  # 記事にこの言葉があれば「出典を書いた」とみなす
    show: bool = True  # 記事末の出典欄に出すもの（元記事の注記は元記事なので出さない）

    @property
    def cite(self) -> str:
        return f"{self.name}、{md(self.checked)}確認" if self.checked else self.name


@dataclass
class Bundle:
    groups: list[Group] = field(default_factory=list)
    computed: list[calc_facts.Computed] = field(default_factory=list)
    ladder: dict | None = None  # 本文に差し込む料金の段の表 {product, name, markdown, cite}
    money: bool = False
    fx: dict | None = None

    def empty(self) -> bool:
        return not self.groups and not self.computed

    def prompt_block(self) -> str:
        """生成プロンプトに足す欄。束が空なら空文字"""
        if self.empty():
            return ""
        parts = []
        if self.groups:
            parts.append(
                "\n\n# 確認済みの事実の束（元記事のほかに使ってよい事実。日付つき）\n"
                "- 元記事に無い事実でも、この欄にある事実は使ってよい。使った文の直後に（ ）で出典を書く。"
                "例「（Anthropic公式の料金ページ、11/3確認）」「（サイソクの過去記事、10/5）」\n"
                "- この欄にも元記事にも無い事実・数字・価格・日付・発言は書かない。英語の資料は訳して要約する（書き写さない）。\n"
                "- 出典・計算の注記は、同じ段落の中では最後に1回だけ書く（数字ごとに繰り返さない）。\n"
                "- [F1]・[C1] のような欄の記号や「確認済みの事実の束」「計算済みの数字」という欄の名前は本文に書かない（出典は名前と確認日で書く）。"
            )
            for i, g in enumerate(self.groups, 1):
                parts.append(f"## [F{i}] {g.label}：{g.cite}\n{g.body[:GROUP_CAP.get(g.label, 800)]}")
        if self.computed:
            parts.append(
                "# 計算済みの数字（プログラムが計算した。あなたは計算しない）\n"
                "- 割合・差・増減率・ペース・年換算・1つあたり・円への換算は自分で計算しない。使うときは下の結果を「約」も含めて表記どおりに写し、"
                "「（サイソク編集部の計算）」と添える。この欄に無い計算結果は書かない。\n"
                "- 比べている2つの数が本当に同じものか、文脈を見て合うものだけ使う（合わないものは使わない）。\n"
                "- 「来月1日」のような言い方は、下の欄の日付（元記事の日付から数えたもの）で書く。\n"
                + calc_facts.block(self.computed)
                + "\n## 「この数字はどう読む？」の章\n"
                "- 「## まとめ」の直前に「## この数字はどう読む？」の章を置き、計算済みの数字で「何と比べて大きいか小さいか」"
                "「前と比べてどうか」を2〜4文で書く。どの記事にも当てはまる一般論は書かない。"
            )
        if self.ladder:
            parts.append(
                f"# 料金の段の表（プログラムが差し込む）\n"
                f"- 「{self.ladder['name']}のプランと料金」の表（上の [F] の表と同じもの）は、プログラムが本文の料金の章に差し込む。"
                "本文にはプランの一覧表を自分で作らない。表の読み方（どの段が誰向けか）を1〜2文で書くのはよい。"
            )
        text = "\n\n".join(parts)
        if len(text) > PROMPT_CAP and self.groups:
            # 長すぎるときは、後ろの欄（リンク先・過去記事など）の中身から短くする。計算済みの数字と決まりは削らない
            over = len(text) - PROMPT_CAP
            for i in range(len(self.groups), 0, -1):
                if over <= 0:
                    break
                g = self.groups[i - 1]
                body = g.body[:GROUP_CAP.get(g.label, 800)]
                cut = min(over, max(0, len(body) - 200))
                if cut:
                    text = text.replace(body, body[:len(body) - cut].rstrip() + "…", 1)
                    over -= cut
        return text

    def check_block(self) -> str:
        """事実確認に渡す欄（ここにある事実は、出典つきなら創作扱いしない）"""
        if self.empty():
            return ""
        lines = ["当サイトで確認済みの事実の束（記事に出典つきで書いてよい）:"]
        for g in self.groups:
            lines.append(f"[{g.label}：{g.cite}]\n{g.body}")
        if self.computed:
            lines.append("計算済みの数字（サイソク編集部の計算。式つき。記事に写してよい）:\n" + calc_facts.block(self.computed))
        return "\n".join(lines)[:CHECK_CAP]

    def meta(self) -> dict:
        """品質ゲートに渡す中身（record["_facts"]。書き出す前に外す）"""
        return {
            "groups": [{"label": g.label, "name": g.name, "url": g.url, "checked": g.checked, "body": g.body,
                        "marks": g.marks, "show": g.show} for g in self.groups],
            "computed": [c.text for c in self.computed],
            "computedValues": [v for c in self.computed for v in c.values],
            "ladder": bool(self.ladder),
            "money": self.money,
        }


# --------------------------------------------------------------------------- #
# 材料ごとの組み立て
# --------------------------------------------------------------------------- #


def is_money_topic(lane: str, title: str, text: str = "") -> bool:
    """お金・税・制度の記事か（題名と冒頭で判定。本文のついでの言及では判定しない）"""
    return lane in MONEY_LANES and bool(MONEY_TOPIC_RE.search(f"{title} {(text or '')[:200]}"))


@lru_cache(maxsize=1)
def load_fx() -> dict | None:
    """為替（USD→JPY）。料金データの _fx.json → 欧州中央銀行の参照レート（ユーロ経由）の順。取れなければ None"""
    if pricing is not None:
        try:
            fx = pricing.load_fx()
            if fx and pricing.valid_rate(fx.get("rate")):
                return fx
        except Exception:  # noqa: BLE001
            pass
    path = ROOT / "content" / "pricing" / "_fx.json"
    try:
        fx = json.loads(path.read_text("utf-8"))
        if 50 < float(fx.get("rate", 0)) < 400:
            return fx
    except (OSError, ValueError, TypeError):
        pass
    try:
        request = urllib.request.Request(ECB_URL, headers={"User-Agent": "saisoku-facts/1.0"})
        with urllib.request.urlopen(request, timeout=6) as response:
            xml = response.read(200_000).decode("utf-8", "ignore")
        date = re.search(r"time=['\"](\d{4}-\d{2}-\d{2})", xml).group(1)
        usd = float(re.search(r"currency=['\"]USD['\"]\s+rate=['\"]([\d.]+)", xml).group(1))
        jpy = float(re.search(r"currency=['\"]JPY['\"]\s+rate=['\"]([\d.]+)", xml).group(1))
        return {"base": "USD", "quote": "JPY", "rate": round(jpy / usd, 2), "date": date,
                "source": "ECB参照レート（ユーロ経由で計算）", "sourceUrl": ECB_URL}
    except Exception:  # noqa: BLE001 - 為替が取れなくても記事は作る（円の目安を出さないだけ）
        return None


@lru_cache(maxsize=1)
def load_howtos() -> list[dict]:
    out = []
    for path in sorted(HOWTO_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text("utf-8"))
            data["_re"] = re.compile(data["match"]) if data.get("match") else None
        except (OSError, json.JSONDecodeError, re.error):
            continue
        if data.get("_re"):
            out.append(data)
    return out


def matched_howtos(title: str, text: str = "") -> list[dict]:
    """見出し（無ければ元記事の冒頭）に出てくる製品のガイド。同じ製品（tool）のものだけ"""
    for scope in (title, (text or "")[:1200]):
        hits = [g for g in load_howtos() if g["_re"].search(scope or "")]
        if hits:
            tool = hits[0].get("tool")
            return [g for g in hits if g.get("tool") == tool]
    return []


def _usd_month(price: dict) -> float | None:
    if price.get("currency") == "USD" and price.get("per") in ("month", None) and isinstance(price.get("amount"), (int, float)):
        return float(price["amount"])
    return None


def _ladder_rows_from_pricing(lad: dict) -> list[dict]:
    rows = []
    for r in lad.get("rows") or []:
        if "plan" not in r:
            continue
        prices = [p for p in r.get("prices") or [] if _usd_month(p) is not None]
        monthly = next((p for p in prices if p.get("billing") == "monthly"), None) or next(
            (p for p in prices if not p.get("billing")), None)
        rows.append({"plan": r["plan"], "usd_month": _usd_month(monthly) if monthly else None, "limits": r.get("limits") or []})
    return rows


def pricing_groups(title: str, text: str) -> tuple[list[Group], list[dict], dict | None]:
    """料金の段（料金データ → 無ければ料金ガイド）。(束の欄, 計算用の段, 本文に差し込む表)"""
    head = f"{title}\n{(text or '')[:1500]}"
    price_topic = bool(PRICE_TOPIC_RE.search(head))
    groups, rows, insert = [], [], None
    if pricing is not None:
        try:
            in_title = pricing.find_products(title)
            products = in_title or (pricing.find_products((text or "")[:1500]) if price_topic else [])
        except Exception:  # noqa: BLE001
            products = []
        # 新しいモデル（API の名前）の記事では、同じ会社の個人向けプランの表は出さない（「Gemini」の一般名でも当たるため）。
        # プランの話（プラン名・サブスク）が見出し・冒頭にあるときだけ両方
        if not PLAN_WORDS_RE.search(head):
            products = [p for p in products if f"{p}-api" not in products]
        elif not PLAN_WORDS_RE.search(title):
            # 見出しがモデルの話なら、本文に差し込む表は API の料金を先に（個人向けプランの表は束の欄にだけ入れる）
            products = sorted(products, key=lambda p: not p.endswith("-api") or p.removesuffix("-api") not in products)
        for product in products[:2]:
            try:
                lad = pricing.ladder(product)
                table = pricing.ladder_markdown(product)
            except Exception:  # noqa: BLE001
                continue
            if not lad or not table:
                continue
            company = COMPANY_NAMES.get(lad.get("company") or "", lad.get("name") or product)
            src = (lad.get("sources") or [{}])[0]
            g = Group(f"pricing:{product}", "料金", f"{company}公式の料金ページ", src.get("url", ""), lad.get("checkedAt") or "",
                      table, marks=[f"{company}公式", "料金ページ", "出典："])
            groups.append(g)
            if lad.get("kind") != "api" and not rows:
                rows = _ladder_rows_from_pricing(lad)
            # 本文に差し込む表は、見出しに出てくる製品だけ（本文の冒頭にだけ名前が出る別の製品の表を入れない。
            # Canva の記事に Gemini API の表、報奨金の記事に ChatGPT のプランの表が入っていた。2026-11-03）
            if insert is None and price_topic and product in in_title:
                insert = {"product": product, "name": lad.get("name") or product, "markdown": table, "cite": g.cite}
            if not any(x.label == "他社との比較" for x in groups):
                if cmp := comparison_group(lad, title, text):
                    groups.append(cmp)
                    if insert is not None and insert["product"] == product:
                        insert["compare"] = cmp.key.split(":", 1)[1]
    if groups:
        return groups, rows, insert
    # 料金データが無いときは、当サイトの料金ガイドの表で代わりにする
    guide = next((g for g in matched_howtos(title, text) if "料金" in str(g.get("intent", "")) and g.get("pricing")), None)
    if not guide or not price_topic:
        return [], [], None
    fx = load_fx()
    lines = ["| プラン | 料金（公式） | 円の目安（税別） | 主な違い |", "|---|---|---|---|"]
    for row in guide["pricing"]:
        m = re.search(r"\$\s?(\d+(?:\.\d+)?)\s*/\s*(?:シート/)?月", str(row.get("price", "")))
        usd = float(m.group(1)) if m else None
        yen = f"約{calc_facts.fmt_qty(usd * fx['rate'], 'JPY')}" if usd and fx else "—"
        lines.append(f"| {row.get('plan', '')} | {row.get('price', '')} | {yen} | {str(row.get('note', '')).replace('|', '／')} |")
        rows.append({"plan": str(row.get("plan", "")), "usd_month": usd, "limits": [str(row.get("note", ""))]})
    checked = str(guide.get("checkedAt") or "")[:10]
    src_urls = "、".join(s.get("url", "") for s in (guide.get("sources") or [])[:1])
    lines += ["", f"出典：当サイトの料金ガイド（公式ページ {src_urls} をもとに作成）／確認日：{checked}"]
    if fx:
        lines.append(f"円は1ドル={fx['rate']:.2f}円（{fx.get('date', '')}、{fx.get('source', '')}）で計算した税別の目安です。")
    table = "\n".join(lines)
    name = f"当サイトの{guide.get('toolName', '')}料金ガイド"
    g = Group(f"howto-pricing:{guide['slug']}", "料金", name, f"/guides/{guide['slug']}", checked, table,
              marks=["料金ガイド", "当サイト", "出典："])
    return [g], rows, {"product": guide.get("tool"), "name": guide.get("toolName") or "", "markdown": table, "cite": g.cite}


def _squash(text: str) -> str:
    return re.sub(r"[\s\-‐‑–—_・]", "", text or "").lower()


def comparison_group(lad: dict, title: str, text: str) -> Group | None:
    """記事の主役のプラン・モデル（見出し → 元記事の冒頭に名前が出てくる段）と同じ段の他社比較（pricing.comparison_markdown）。
    比べる相手が2社以上いるときだけ"""
    kind = "api" if lad.get("kind") == "api" else "plan"
    name_key = "model" if kind == "api" else "plan"
    tier = None
    for scope in (title, (text or "")[:600]):
        flat = _squash(scope)
        hits = [r for r in lad.get("rows") or [] if r.get(name_key) and r.get("tier") and _squash(r[name_key]) in flat
                and r.get("tier") not in ("free",)]
        if hits:
            hits.sort(key=lambda r: -len(r[name_key]))  # 長い名前を優先（「Max」より「Ultra」…ではなく、より具体的な名前）
            tier = hits[0]["tier"]
            break
    if not tier:
        return None
    try:
        cmp = pricing.comparison(tier, kind)
        table = pricing.comparison_markdown(tier, kind)
    except Exception:  # noqa: BLE001
        return None
    companies = {r.get("company") for r in cmp.get("rows") or []}
    if len(companies) < 2 or not table:
        return None
    return Group(f"compare:{kind}:{tier}", "他社との比較", "各社公式の料金ページ", "", cmp.get("checkedAt") or "", table,
                 marks=["各社", "公式", "出典："])


def howto_group(title: str, text: str, skip_pricing: bool) -> Group | None:
    """使い方・料金ガイドの要点（結論・無料でできること・日本での扱い）。料金の表は pricing_groups が出す"""
    guides = matched_howtos(title, text)
    if not guides:
        return None
    guide = next((g for g in guides if "料金" in str(g.get("intent", ""))), guides[0])
    lines = [f"ガイド: {guide.get('title', '')}（/guides/{guide['slug']}）"]
    lines += [f"- {c}" for c in (guide.get("conclusion") or [])[:3]]
    if not skip_pricing:
        lines += [f"- {r.get('plan')}: {r.get('price')}（{r.get('note', '')}）" for r in (guide.get("pricing") or [])[:5]]
    lines += [f"- 無料でできること: {f}" for f in (guide.get("freeTier") or [])[:2]]
    # 日本での提供は japan_availability の表が正（ガイドの「日本」の欄は古いことがあるので渡さない）
    body = "\n".join(lines)[:700]
    return Group(f"howto:{guide['slug']}", "使い方ガイド", f"当サイトの{guide.get('toolName', '')}ガイド", f"/guides/{guide['slug']}",
                 str(guide.get("checkedAt") or "")[:10], body, marks=["当サイトの", "ガイド"])


def _load_article(slug: str) -> dict:
    try:
        return json.loads((ARTICLES_DIR / f"{slug}.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def past_rows(title: str, text: str, related: list[dict], now: datetime) -> list[dict]:
    """関連する過去記事のうち、60日以内で同じ会社（または見出しの固有名詞が重なる）もの。主な数字の文（要点）つき"""
    company = archive.company_of(f"{title} {(text or '')[:600]}")
    out = []
    for row in related or []:
        at = row.get("at")
        if not isinstance(at, datetime) or (now - at).days > PAST_DAYS:
            continue
        if company != "other" and row.get("company") not in (company, None):
            continue
        data = _load_article(row["slug"])
        facts = [h for h in (data.get("highlights") or []) if re.search(r"\d", h)]
        if not facts:
            continue
        out.append({"slug": row["slug"], "title": row["title"], "at": at, "text": "\n".join(facts[:3])})
        if len(out) >= MAX_PAST:
            break
    return out


def past_group(rows: list[dict]) -> Group | None:
    if not rows:
        return None
    lines = []
    for r in rows:
        lines.append(f"- {r['at'].astimezone(JST):%Y-%m-%d}「{r['title'][:40]}」（/articles/{r['slug']}）: " + " / ".join(r["text"].split("\n")[:2]))
    newest = max(r["at"] for r in rows).astimezone(JST).strftime("%Y-%m-%d")
    return Group("past", "過去記事", "サイソクの過去記事", "", newest, "\n".join(lines)[:800], marks=["過去記事", "/articles/", "以前"])


def footnote_group(item: dict, text: str) -> Group | None:
    """元記事の注記（但し書き）。読み落とすと比べ方を間違えるもの（「SBI証券は単独口座数非開示のため…」）"""
    notes = [ln.strip() for ln in (text or "").split("\n") if FOOTNOTE_RE.match(ln)]
    notes = [n for n in notes if not re.search(r"手数料等およびリスク|登録番号|商号等|加入協会", n)]
    if not notes:
        return None
    body = "\n".join(f"- {n[:220]}" for n in notes[:4])
    return Group("note", "元記事の注記", item.get("source_name") or "元記事", item.get("url", ""), "",
                 body + "\n- 注記は書き写さない。数字を比べる文の近くで、条件（何を数えたか・いつの数か・比べられない相手）を自分の言葉で1〜2文にして添える。", show=False)


DATE_PATTERNS = [
    (re.compile(r"(20\d{2})\s?年\s?(\d{1,2})\s?月\s?(\d{1,2})\s?日"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"令和\s?(\d{1,2})\s?年\s?(\d{1,2})\s?月\s?(\d{1,2})\s?日"), lambda m: (2018 + int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"(20\d{2})[/.-](\d{1,2})[/.-](\d{1,2})"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
]
URL_DATE_RE = re.compile(r"(?<!\d)(20\d{2})(?:/|-)?(\d{2})(?:/|-)?(\d{2})(?!\d)")


def statement_date(item: dict, text: str, page: dict | None = None) -> datetime | None:
    """「来月1日」を数える基準の日（発表・会見の日）。元記事の冒頭 → ページの題名 → URL の日付 → 確かな公開日時の順。
    分からなければ None（そのときは日付を数えない。取り違えるより出さない）"""
    published = item.get("published_at") if isinstance(item.get("published_at"), datetime) else None
    limit = (published or datetime.now(timezone.utc)) + timedelta(days=1)
    import unicodedata  # noqa: PLC0415

    def ok(y, mo, d):
        try:
            when = datetime(y, mo, d, 12, tzinfo=JST)
        except ValueError:
            return None
        return when if limit - timedelta(days=120) <= when <= limit else None

    for scope in (unicodedata.normalize("NFKC", (text or "")[:300]), unicodedata.normalize("NFKC", str((page or {}).get("title") or ""))):
        for pattern, parts in DATE_PATTERNS:
            if (m := pattern.search(scope)) and (when := ok(*parts(m))):
                return when
    for url in (item.get("url") or "", str((page or {}).get("final_url") or "")):
        if (m := URL_DATE_RE.search(url)) and (when := ok(int(m[1]), int(m[2]), int(m[3]))):
            return when
    if published and not item.get("published_guessed") and not item.get("date_from_first_seen"):
        return published
    return None


def link_groups(links: list[dict]) -> list[Group]:
    out = []
    for i, got in enumerate(links, 1):
        label = "公的資料" if got.get("public") else "関連ページ"
        title = f"「{got['title']}」" if got.get("title") else ""
        out.append(Group(f"link:{i}", label, f"{got['name']}{title}", got["url"], got.get("checkedAt", ""), got["excerpt"],
                         marks=[got["name"], source_links.host(got["url"])]))
    return out


def fx_group(fx: dict | None, needed: bool) -> Group | None:
    if not fx or not needed:
        return None
    body = f"1ドル={float(fx['rate']):.2f}円（{fx.get('date', '')}、{fx.get('source', '')}）。円はこのレートで計算した目安で、実際の請求額は為替とカード会社の手数料で変わる。"
    return Group("fx", "為替", fx.get("source") or "為替レート", fx.get("sourceUrl") or "", str(fx.get("date") or ""), body,
                 marks=["1ドル", "為替", "円の目安", "目安"])


# --------------------------------------------------------------------------- #
# まとめて組み立てる
# --------------------------------------------------------------------------- #


def build(item: dict, text: str, related: list[dict] | None = None, lane: str = "ai", page: dict | None = None, *,
          follow_links: bool = True, now: datetime | None = None, log=None, fetcher=None, max_links: int = 2) -> Bundle:
    """1本の記事の「確かな事実の束」。ネットワークに出るのはリンク先（最大2本）と為替（料金データが無いとき）だけ"""
    now = now or datetime.now(timezone.utc)
    title = item.get("title") or ""
    money = is_money_topic(lane, title, text)
    groups: list[Group] = []
    price_groups, ladder_rows, insert = pricing_groups(title, text)
    groups += price_groups
    if g := howto_group(title, text, skip_pricing=bool(price_groups)):
        groups.append(g)
    if g := footnote_group(item, text):
        groups.append(g)
    links = []
    if follow_links and page is not None:
        keywords = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{2,}|[ァ-ヶー]{2,}|[一-龠々]{2,}", title)}
        links = source_links.follow(page, item.get("url", ""), title, item.get("source_name", ""), money=money,
                                    keywords=keywords, fetcher=fetcher, now=now, log=log, max_fetches=max_links)
    groups += link_groups(links)
    past = past_rows(title, text, related or [], now)
    if g := past_group(past):
        groups.append(g)
    need_fx = bool(ladder_rows) or bool(re.search(r"\$\s?\d|\d\s?(?:ドル|USD|dollars?)\b", text or "", re.I))
    fx = load_fx() if need_fx else None
    computed = calc_facts.compute(text or "", fx=fx, ladder_rows=ladder_rows, past_rows=past, now=now,
                                  published=statement_date(item, text, page))
    if g := fx_group(fx, any(c.kind == "yen" for c in computed)):
        groups.append(g)
    return Bundle(groups=groups, computed=computed, ladder=insert, money=money, fx=fx)


# --------------------------------------------------------------------------- #
# 記事への差し込み・出典の一覧
# --------------------------------------------------------------------------- #

PRICE_HEADING_RE = re.compile(r"^##\s+.*(?:料金|価格|値段|いくら|プラン)", re.M)
NEXT_H2_RE = re.compile(r"^##\s", re.M)


def _strip_copied_table(body: str, table: str, name: str) -> tuple[str, int | None]:
    """LLM が束の表を本文に写していたら外す（差し込む表と二重にしない）。(本文, 外した位置)"""
    header = next((ln for ln in table.split("\n") if ln.startswith("| ")), "")
    lines = body.split("\n")
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == header.strip())
    except StopIteration:
        return body, None
    end = start
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    # 表のすぐ後の「出典：」「円は…」の行と、すぐ前の表の名前だけの行も外す
    while end < len(lines) and (not lines[end].strip() or re.match(r"^(出典[:：]|円は)", lines[end].strip())):
        end += 1
    while start > 0 and (not lines[start - 1].strip() or (name and name in lines[start - 1] and len(lines[start - 1]) < 40
                                                         and not lines[start - 1].startswith("## "))):
        start -= 1
    pos = len("\n".join(lines[:start])) + (1 if start else 0)
    return "\n".join(lines[:start] + lines[end:]), pos


def insert_ladder(body: str, bundle: Bundle) -> str:
    """料金の段の表を本文に差し込む（料金の章の終わり → 無ければ「日本で使える？」の前 → 「まとめ」の前）。
    LLM には表を書かせない（数字の写し間違いを防ぐ）。LLM が束の表を写していたら、その場所で差し替える"""
    if not bundle.ladder or not body:
        return body
    table = bundle.ladder["markdown"]
    block = f"### {bundle.ladder['name']}のプランと料金（公式の料金と円の目安）\n\n{table}\n"
    if block.strip() in body:
        return body
    body, pos = _strip_copied_table(body, table, bundle.ladder["name"])
    if pos is None:
        m = PRICE_HEADING_RE.search(body)
        if m:
            nxt = NEXT_H2_RE.search(body, m.end())
            pos = nxt.start() if nxt else len(body)
        else:
            anchor = re.search(r"^##\s*(?:日本で(?:も)?使える|まとめ)", body, re.M)
            pos = anchor.start() if anchor else len(body)
    head, tail = body[:pos].rstrip("\n"), body[pos:].lstrip("\n")
    return f"{head}\n\n{block}\n{tail}" if tail else f"{head}\n\n{block}"


def article_text(record: dict) -> str:
    parts = [record.get("lede") or "", record.get("body") or "", record.get("easy") or "", " ".join(record.get("highlights") or [])]
    parts += [f"{r.get('q', '')} {r.get('a', '')}" for r in record.get("faq") or []]
    parts += list(record.get("todayActions") or [])
    parts += list((record.get("workMoney") or {}).get("points") or [])
    return "\n".join(parts)


def used_groups(record: dict, facts: dict, source_text: str) -> list[dict]:
    """記事が実際に使った束の欄（元記事に無い数字がその欄にある・その欄の出典名が記事にある・料金の表を差し込んだ）"""
    text = article_text(record)
    source_vals = calc_facts.values(source_text)
    article_qs = [q for q in calc_facts.extract(text) if calc_facts.checkable(q)]
    out, credited = [], set()
    for g in facts.get("groups") or []:
        unique = [v for v in calc_facts.values(g["body"]) if not any(abs(v - s) <= 1e-9 for s in source_vals)]
        # 元記事に無い数字で、前の欄（料金の表など）でまだ数えていないものがこの欄にあれば「使った」
        hits = {i for i, q in enumerate(article_qs)
                if i not in credited and unique and calc_facts.supported(q, unique) and not calc_facts.supported(q, source_vals)}
        by_name = bool(g.get("name")) and g["name"] in text
        by_table = g["label"] == "料金" and facts.get("ladder") and "のプランと料金（公式の料金と円の目安）" in (record.get("body") or "")
        if hits or by_name or by_table:
            credited |= hits
            out.append(g)
    return out


def uses_computed(record: dict, facts: dict, source_text: str) -> bool:
    """記事が計算済みの数字（元記事・束の欄に無い計算結果）を使ったか"""
    text = article_text(record)
    if re.search(r"サイソク編集部の計算|編集部の計算", text):
        return True
    known = calc_facts.values(source_text + "\n" + "\n".join(g["body"] for g in facts.get("groups") or []))
    comp = [float(v) for v in facts.get("computedValues") or []]
    return any(calc_facts.checkable(q) and calc_facts.supported(q, comp) and not calc_facts.supported(q, known)
               for q in calc_facts.extract(text))


def sources(record: dict, source_text: str) -> list[dict]:
    """記事末の「出典と更新履歴」に出す、元記事のほかに確かめた資料（「料金：Anthropic公式の料金ページ（11/3確認）」）"""
    facts = record.get("_facts") or {}
    out, seen = [], set()
    for g in used_groups(record, facts, source_text):
        if not g.get("show", True) or (g["label"], g["name"]) in seen:
            continue
        seen.add((g["label"], g["name"]))
        out.append({"label": g["label"], "name": g["name"], "url": g.get("url") or "", "checkedAt": g.get("checked") or ""})
    js = (record.get("verdict") or {}).get("japanSource")
    if js and js.get("name"):
        out.append({"label": "日本での提供", "name": f"{js['name']}の公式ページ", "url": js.get("url", ""), "checkedAt": js.get("checkedAt", "")})
    if facts.get("computed") and uses_computed(record, facts, source_text):
        out.append({"label": "計算", "name": "サイソク編集部の計算（元記事と上の資料の数字から）", "url": "", "checkedAt": ""})
    return out[:8]


def main() -> int:
    import feeds  # noqa: PLC0415

    url = sys.argv[1]
    lane = sys.argv[sys.argv.index("--lane") + 1] if "--lane" in sys.argv else "ai"
    page = feeds.fetch_article_text(url)
    item = {"title": page.get("title") or "", "url": url, "source_name": source_links.site_name(url)}
    related = archive.Archive().related(item["title"], page["text"], url, lane)
    bundle = build(item, page["text"], related, lane, page, log=print)
    print(bundle.prompt_block())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
