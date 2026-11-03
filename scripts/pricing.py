#!/usr/bin/env python3
"""AI の料金の段の表と、会社をまたいだ比較表を返す部品（記事・動画の生成から呼ぶ）。

データは content/pricing/<product>.json（scripts/pricing_watch.py が公式ページから毎日作り直す）。
数字はすべて公式ページに書かれたもの。円の目安だけはこの部品が為替から計算する（「目安」と必ず書く）。

  import pricing
  pricing.ladder("claude")                 # Claude の Free〜Ultra・Team の段の表（dict）
  pricing.ladder_markdown("claude")        # そのまま本文に差し込める表（出典・確認日・為替の注記つき）
  pricing.comparison("standard")           # 月20ドル前後のプランを会社をまたいで比べる
  pricing.comparison("flagship", kind="api")
  pricing.company_ladders("google")        # 1社の製品（アプリのプラン・API）すべて
  pricing.find_products(text)              # 文章に出てくる製品（["claude", "chatgpt", ...]）
  pricing.facts_block(text)                # 文章に関係する料金の事実をまとめた、プロンプト用の文字列

  python3 scripts/pricing.py ladder claude
  python3 scripts/pricing.py compare standard
  python3 scripts/pricing.py compare flagship --kind api
  python3 scripts/pricing.py facts "Claude Ultra と ChatGPT Pro の違い"
"""

from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRICING_DIR = ROOT / "content" / "pricing"
FX_PATH = PRICING_DIR / "_fx.json"
CHANGELOG_PATH = PRICING_DIR / "_changelog.json"
CONSUMPTION_TAX = 0.10

# 表を出す順（読者が気にする順）
PLAN_PRODUCTS = ["claude", "chatgpt", "gemini", "copilot", "perplexity"]
API_PRODUCTS = ["claude-api", "openai-api", "gemini-api"]
PRODUCTS = PLAN_PRODUCTS + API_PRODUCTS

# 文章から製品を見つけるための名前（大文字小文字・空白は無視）
ALIASES: dict[str, list[str]] = {
    "claude": ["Claude", "クロード", "Claude Pro", "Claude Max", "Claude Ultra", "Claude Team"],
    "chatgpt": ["ChatGPT", "チャットGPT", "ChatGPT Plus", "ChatGPT Pro", "ChatGPT Go", "ChatGPT Business"],
    "gemini": ["Gemini", "ジェミニ", "Google AI Pro", "Google AI Ultra", "Google AI Plus"],
    "copilot": ["Copilot", "コパイロット", "Microsoft 365"],
    "perplexity": ["Perplexity", "パープレキシティ"],
    "claude-api": ["Claude API", "Anthropic API", "Claude Platform", "Opus 5.5", "Sonnet 5.5", "Haiku 5.5"],
    "openai-api": ["OpenAI API", "GPT-6 Astra", "GPT-6.1 Sol", "GPT-6.1 Terra", "gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra"],
    "gemini-api": ["Gemini API", "Gemini 4 Argon", "Gemini 4 Carbon", "Gemini 3.8 Flash", "AI Studio"],
}

# プランの段（サイソク編集部の分け方。公式の区分ではない）
PLAN_TIERS = {
    "free": "無料",
    "low": "お手ごろ（月1,000〜2,000円前後）",
    "standard": "標準（月20ドル・3,000円前後）",
    "high": "上位（月100ドル前後）",
    "top": "最上位（月200ドル以上）",
    "team": "チーム・法人（1人あたり）",
    "enterprise": "大企業向け",
}
API_TIERS = {
    "flagship": "最上位モデル",
    "mid": "中くらいのモデル",
    "small": "小型・低価格モデル",
}
BILLING_JA = {"monthly": "月払い", "annual": "年払い"}
# 比較表でプラン名の前に付ける製品名（プラン名に会社名が入っているものは付けない）
SHORT = {"claude": "Claude", "chatgpt": "ChatGPT", "perplexity": "Perplexity"}


# --------------------------------------------------------------------------- #
# 読み込み
# --------------------------------------------------------------------------- #


def _read(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def load(product: str, base: Path | None = None) -> dict | None:
    """1製品のデータ（無ければ None）"""
    return _read((base or PRICING_DIR) / f"{product}.json")


def load_all(kind: str | None = None, base: Path | None = None) -> list[dict]:
    names = PLAN_PRODUCTS if kind == "plan" else API_PRODUCTS if kind == "api" else PRODUCTS
    return [d for d in (load(n, base) for n in names) if d]


def load_fx(base: Path | None = None) -> dict | None:
    return _read((base or PRICING_DIR) / "_fx.json")


def changelog(product: str | None = None, limit: int = 20, base: Path | None = None) -> list[dict]:
    """料金の変更の記録（新しい順）"""
    rows = _read((base or PRICING_DIR) / "_changelog.json") or []
    if product:
        rows = [r for r in rows if r.get("product") == product]
    return rows[:limit]


# --------------------------------------------------------------------------- #
# 円の目安（為替・消費税）
# --------------------------------------------------------------------------- #


def valid_rate(rate) -> bool:
    return isinstance(rate, (int, float)) and math.isfinite(rate) and 50 <= rate <= 400


def yen_estimate(amount: float, currency: str, fx: dict | None, tax_included: bool | None, jp_tax: bool) -> dict | None:
    """1つの金額の円の目安。
    - ドル: 為替で円にする。jp_tax（日本の消費税がかかる個人・チーム向けのプラン）なら 10% を足した額も出す
    - 円で税抜きと書かれた価格: 10% を足した額を出す
    - 円で税込み・税の記載なし: 公式の額そのものなので目安は作らない（None）"""
    if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
        return None
    if currency == "USD":
        if not fx or not valid_rate(fx.get("rate")):
            return None
        ex = amount * fx["rate"]
        return {
            "exTax": round(ex),
            "inclTax": round(ex * (1 + CONSUMPTION_TAX)) if jp_tax else None,
            "rate": fx["rate"],
            "rateDate": fx.get("date"),
            "rateSource": fx.get("source"),
            "estimate": True,
        }
    if currency == "JPY" and tax_included is False:
        return {"exTax": round(amount), "inclTax": round(amount * (1 + CONSUMPTION_TAX)), "rate": None, "rateDate": None, "rateSource": None, "estimate": True}
    return None


def api_yen(model: dict, fx: dict | None) -> dict | None:
    """API の 100万トークンあたりの円の目安（税別）"""
    if model.get("currency", "USD") != "USD" or not fx or not valid_rate(fx.get("rate")):
        return None
    out = {}
    for key in ("input", "output", "cacheRead"):
        value = model.get(key)
        if isinstance(value, (int, float)):
            out[key] = round(value * fx["rate"], 2 if value * fx["rate"] < 10 else 1 if value * fx["rate"] < 100 else 0)
    if not out:
        return None
    return {**out, "rate": fx["rate"], "rateDate": fx.get("date"), "rateSource": fx.get("source"), "estimate": True, "taxIncluded": False}


def attach_jpy(data: dict, fx: dict | None) -> dict:
    """製品データの全部の金額に円の目安を付け直す（データを書き換えて返す）"""
    kind = data.get("kind")
    for plan in data.get("plans") or []:
        for price in plan.get("prices") or []:
            jp_tax = price.get("jpTax", kind == "plan")
            est = yen_estimate(price.get("amount"), price.get("currency", "USD"), fx, price.get("taxIncluded"), jp_tax)
            if est:
                price["jpy"] = est
            else:
                price.pop("jpy", None)
    for model in data.get("models") or []:
        est = api_yen(model, fx)
        if est:
            model["jpy"] = est
        else:
            model.pop("jpy", None)
    if fx and valid_rate(fx.get("rate")):
        data["fx"] = {k: fx.get(k) for k in ("base", "quote", "rate", "date", "source", "sourceUrl")}
    return data


# --------------------------------------------------------------------------- #
# 表示の文字
# --------------------------------------------------------------------------- #


def money(amount: float, currency: str) -> str:
    if currency == "JPY":
        return f"{round(amount):,}円"
    if amount == 0:
        return "$0"
    if amount >= 1 and float(amount).is_integer():
        return f"${int(amount):,}"
    # $0.125 は3桁、$0.75・$2.50・$0.10 は2桁（公式ページの書き方に合わせる）
    text = f"{amount:,.3f}"
    return "$" + (text[:-1] if text.endswith("0") else text)


def yen_text(value: float) -> str:
    if value < 1:
        return "1円未満"
    if value < 100:
        text = f"{value:.1f}".rstrip("0").rstrip(".") if value < 10 else f"{round(value)}"
        return f"約{text}円"
    return f"約{round(value / 10) * 10:,}円"


def price_text(price: dict) -> str:
    """「$20/月（月払い）」「$17/月（年払い・一括$200）」「2,900円/月」「4,497円/人・月（年払い・税別）」"""
    cur = price.get("currency", "USD")
    per = price.get("per", "month")
    unit = {"month": "/月", "seat-month": "/人・月", "year": "/年", "seat-year": "/人・年"}.get(per, "")
    head = money(price["amount"], cur) + unit + ("から" if price.get("from") else "")
    extra = []
    if price.get("billing") in BILLING_JA:
        extra.append(BILLING_JA[price["billing"]])
    if price.get("billedAs"):
        extra.append(f"一括{money(price['billedAs'], cur)}")
    if price.get("taxIncluded") is False and cur == "JPY":
        extra.append("税別")
    if price.get("regular") and price["regular"] != price["amount"]:
        extra.append(f"通常{money(price['regular'], cur)}")
    return head + (f"（{'・'.join(extra)}）" if extra else "")


def jpy_text(price: dict) -> str:
    """円の目安の文字（無ければ空）"""
    est = price.get("jpy")
    if not est or price.get("amount") == 0:
        return ""
    if est.get("inclTax") is not None:
        return f"{yen_text(est['inclTax'])}（税込みの目安）"
    return f"{yen_text(est['exTax'])}（税別の目安）"


def fx_note(fx: dict | None, kind: str = "plan") -> str:
    if not fx or not valid_rate(fx.get("rate")):
        return ""
    tax = "API の料金は税別です。" if kind == "api" else "ドルのプランは消費税10%を足しています。"
    return (
        f"円は1ドル={fx['rate']:.2f}円（{fx.get('date', '')}、{fx.get('source', '')}）で計算した目安です。"
        f"{tax}実際の請求額は為替とカード会社の手数料で変わります。"
    )


# --------------------------------------------------------------------------- #
# 段の表・比較表
# --------------------------------------------------------------------------- #


def _source_line(data: dict) -> str:
    urls = [s.get("url") for s in data.get("sources") or [] if s.get("url")]
    return "、".join(urls)


def _main_price(plan: dict) -> dict | None:
    """表の1行に出す代表の金額。円の公式価格 → 月払い → 最初の順"""
    prices = [p for p in plan.get("prices") or [] if isinstance(p.get("amount"), (int, float))]
    if not prices:
        return None
    for pick in (
        lambda p: p.get("currency") == "JPY" and p.get("billing") != "annual",
        lambda p: p.get("currency") == "JPY",
        lambda p: p.get("billing") == "monthly",
    ):
        found = [p for p in prices if pick(p)]
        if found:
            return found[0]
    return prices[0]


def _plan_row(data: dict, plan: dict) -> dict:
    main = _main_price(plan)
    return {
        "product": data["product"],
        "productName": data.get("name"),
        "company": data.get("company"),
        "id": plan.get("id"),
        "plan": plan.get("name"),
        "compareName": f"{SHORT[data['product']]} {plan.get('name')}" if SHORT.get(data["product"]) else plan.get("name"),
        "tier": plan.get("tier"),
        "tierLabel": PLAN_TIERS.get(plan.get("tier"), ""),
        "prices": plan.get("prices") or [],
        "priceText": " / ".join(price_text(p) for p in plan.get("prices") or [] if isinstance(p.get("amount"), (int, float))),
        "mainPrice": main,
        "mainPriceText": price_text(main) if main else "",
        "jpyText": jpy_text(main) if main else "",
        "limits": plan.get("limits") or [],
        "note": plan.get("note", ""),
        "manual": any(p.get("src") == "manual" for p in plan.get("prices") or []),
        "sourceUrl": (main or {}).get("sourceUrl") or plan.get("sourceUrl") or (data.get("sources") or [{}])[0].get("url"),
        "checkedAt": data.get("checkedAt"),
    }


def _model_row(data: dict, model: dict) -> dict:
    jp = model.get("jpy") or {}
    cur = model.get("currency", "USD")

    def cell(key):
        v = model.get(key)
        return money(v, cur) if isinstance(v, (int, float)) else "—"

    return {
        "product": data["product"],
        "productName": data.get("name"),
        "company": data.get("company"),
        "id": model.get("id"),
        "model": model.get("name"),
        "tier": model.get("class"),
        "tierLabel": API_TIERS.get(model.get("class"), ""),
        "context": model.get("tier"),
        "status": model.get("status", "listed"),
        "input": model.get("input"),
        "output": model.get("output"),
        "cacheRead": model.get("cacheRead"),
        "inputText": cell("input"),
        "outputText": cell("output"),
        "cacheReadText": cell("cacheRead"),
        "jpyInputText": yen_text(jp["input"]) if "input" in jp else "",
        "jpyOutputText": yen_text(jp["output"]) if "output" in jp else "",
        "scheduled": model.get("scheduled") or [],
        "notes": model.get("notes") or [],
        "sourceUrl": model.get("sourceUrl") or (data.get("sources") or [{}])[0].get("url"),
        "checkedAt": data.get("checkedAt"),
    }


def ladder(product: str, base: Path | None = None) -> dict | None:
    """1製品の段の表。rows はプラン（kind=plan）か API モデル（kind=api）"""
    data = load(product, base)
    if not data:
        return None
    rows = [_plan_row(data, p) for p in data.get("plans") or []] + [_model_row(data, m) for m in data.get("models") or []]
    return {
        "product": product,
        "name": data.get("name"),
        "company": data.get("company"),
        "kind": data.get("kind"),
        "mode": data.get("mode"),
        "checkedAt": data.get("checkedAt"),
        "updatedAt": data.get("updatedAt"),
        "sources": data.get("sources") or [],
        "taxNote": data.get("taxNote", ""),
        "fx": data.get("fx"),
        "fxNote": fx_note(data.get("fx"), data.get("kind", "plan")),
        "notes": data.get("notes") or [],
        "rows": rows,
    }


def comparison(tier: str, kind: str = "plan", base: Path | None = None) -> dict:
    """会社をまたいだ比較（同じ段のプラン、または同じクラスの API モデル）"""
    rows = []
    checked = []
    fx = None
    for data in load_all(kind, base):
        fx = fx or data.get("fx")
        if kind == "plan":
            picked = [_plan_row(data, p) for p in data.get("plans") or [] if p.get("tier") == tier]
        else:
            # 同じモデルの長い入力の料金などは比較に出さない（primary の行だけ）
            picked = [_model_row(data, m) for m in data.get("models") or [] if m.get("class") == tier and m.get("primary", True)]
        if picked:
            rows += picked
            checked.append(data.get("checkedAt") or "")
    labels = PLAN_TIERS if kind == "plan" else API_TIERS
    return {
        "tier": tier,
        "kind": kind,
        "label": labels.get(tier, tier),
        "rows": rows,
        "checkedAt": min(c for c in checked if c) if any(checked) else None,
        "fx": fx,
        "fxNote": fx_note(fx, kind),
        "note": "段・クラスの分け方はサイソク編集部の目安です（公式の区分ではありません）。",
    }


def company_ladders(company: str, base: Path | None = None) -> list[dict]:
    """1社の製品の段の表すべて（例: google → Gemini のプランと Gemini API）"""
    out = []
    for name in PRODUCTS:
        data = load(name, base)
        if data and data.get("company") == company:
            out.append(ladder(name, base))
    return out


# --------------------------------------------------------------------------- #
# 本文・プロンプト用の文字
# --------------------------------------------------------------------------- #


def _cell(text: str) -> str:
    return (text or "").replace("|", "／").replace("\n", " ").strip()


def ladder_markdown(product: str, base: Path | None = None) -> str:
    """本文に差し込める段の表（マークダウン）。データが無ければ空文字"""
    lad = ladder(product, base)
    if not lad or not lad["rows"]:
        return ""
    lines = []
    if lad["kind"] == "api":
        lines += ["| モデル | 入力（100万トークン） | 出力（100万トークン） | 円の目安（入力／出力・税別） |", "|---|---|---|---|"]
        for r in lad["rows"]:
            if r["status"] != "listed":
                lines.append(f"| {_cell(r['model'])} | 料金ページに記載なし | — | — |")
                continue
            name = r["model"] + (f"（{r['context']}）" if r.get("context") else "")
            yen = f"{r['jpyInputText']}／{r['jpyOutputText']}" if r["jpyInputText"] else "—"
            lines.append(f"| {_cell(name)} | {r['inputText']} | {r['outputText']} | {yen} |")
    else:
        lines += ["| プラン | 料金（公式） | 円の目安 | 主な上限・違い |", "|---|---|---|---|"]
        for r in lad["rows"]:
            lines.append(f"| {_cell(r['plan'])} | {_cell(r['priceText'])} | {_cell(r['jpyText']) or '—'} | {_cell('、'.join(r['limits'])) or '—'} |")
    lines.append("")
    lines.append(f"出典：{lad['name']}の公式ページ（{_source_line(lad)}）／確認日：{lad['checkedAt']}")
    if lad["fxNote"]:
        lines.append(lad["fxNote"])
    return "\n".join(lines)


def comparison_markdown(tier: str, kind: str = "plan", base: Path | None = None) -> str:
    cmp = comparison(tier, kind, base)
    if not cmp["rows"]:
        return ""
    if kind == "api":
        lines = ["| 会社・モデル | 入力（100万トークン） | 出力（100万トークン） | 円の目安（入力／出力・税別） |", "|---|---|---|---|"]
        for r in cmp["rows"]:
            if r["status"] != "listed":
                continue
            name = f"{r['productName']}：{r['model']}" + (f"（{r['context']}）" if r.get("context") else "")
            yen = f"{r['jpyInputText']}／{r['jpyOutputText']}" if r["jpyInputText"] else "—"
            lines.append(f"| {_cell(name)} | {r['inputText']} | {r['outputText']} | {yen} |")
    else:
        lines = ["| サービス・プラン | 料金（公式） | 円の目安 | 主な上限・違い |", "|---|---|---|---|"]
        for r in cmp["rows"]:
            lines.append(
                f"| {_cell(r['compareName'])} | {_cell(r['mainPriceText'])} | {_cell(r['jpyText']) or '—'} | {_cell('、'.join(r['limits'][:2])) or '—'} |"
            )
    sources = sorted({r["sourceUrl"] for r in cmp["rows"] if r.get("sourceUrl")})
    lines += ["", f"出典：各社の公式ページ（{'、'.join(sources)}）／確認日：{cmp['checkedAt']}", cmp["note"]]
    if cmp["fxNote"]:
        lines.append(cmp["fxNote"])
    return "\n".join(lines)


def _squash(text: str) -> str:
    return re.sub(r"[\s\-‐‑–—_・]", "", (text or "")).lower()


# GitHub の Copilot の話か（GitHub Copilot は Microsoft 365 の Copilot と別の契約・料金。2026-11-04 の点検で、GitHub Copilot の記事に
# Microsoft 365 の料金表が出ていた。台帳 A32）。GitHub が出てきて、Microsoft 365 の Copilot を指す言葉が無ければ別の製品とみなす。lib/pricing-ladder.ts と同じ
M365_COPILOT_RE = re.compile(r"microsoft365|m365|microsoftcopilot|copilotpro(?!mpt)")


def is_other_copilot(text: str) -> bool:
    flat = _squash(unicodedata.normalize("NFKC", text or ""))
    return "github" in flat and not M365_COPILOT_RE.search(flat)


def find_products(text: str) -> list[str]:
    """文章に出てくる製品（PRODUCTS の順）"""
    flat = _squash(unicodedata.normalize("NFKC", text or ""))
    other_copilot = is_other_copilot(text)
    return [p for p in PRODUCTS if not (p == "copilot" and other_copilot) and any(_squash(a) in flat for a in ALIASES.get(p, []))]


def facts_block(text: str, base: Path | None = None) -> str:
    """プロンプトに渡す「当サイトで確認済みの料金（日付つき）」の欄。関係する製品が無ければ空文字"""
    parts = []
    for product in find_products(text):
        table = ladder_markdown(product, base)
        if table:
            lad = ladder(product, base)
            parts.append(f"## {lad['name']}（{lad['checkedAt']} 公式ページで確認）\n{table}")
    if not parts:
        return ""
    return "# 当サイトで確認済みの料金（公式ページ・日付つき）\n" + "\n\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("ladder")
    p.add_argument("product", choices=PRODUCTS)
    p.add_argument("--json", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("tier")
    c.add_argument("--kind", choices=["plan", "api"], default="plan")
    c.add_argument("--json", action="store_true")
    f = sub.add_parser("facts")
    f.add_argument("text")
    args = parser.parse_args()
    if args.cmd == "ladder":
        print(json.dumps(ladder(args.product), ensure_ascii=False, indent=1) if args.json else ladder_markdown(args.product))
    elif args.cmd == "compare":
        print(json.dumps(comparison(args.tier, args.kind), ensure_ascii=False, indent=1) if args.json else comparison_markdown(args.tier, args.kind))
    elif args.cmd == "facts":
        print(facts_block(args.text))
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
