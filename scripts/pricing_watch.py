#!/usr/bin/env python3
"""公式の料金ページを毎日読み直し、content/pricing/<product>.json（料金の段の表）を作り直す。

- 製品ごとに専用の読み取り（extract_*）。ページの形が変わって読めないときは、前のデータをそのまま残す
- 空の表・一部だけの表は決して書かない（validate と assert_complete の2段階。前回あったプランが消えたら失敗扱い）
- 金額が変わったら、その場でもう一度読み直して同じ結果のときだけ採用する（表示の揺れ・A/B テスト対策）。
  3倍以上・3分の1以下の大きな変化は、次の回にも同じなら採用する（読み違いで記事を作らない）
- 変わったら content/pricing/_changelog.json に記録し、state/pricing_news.json に「料金が変わった」候補を置く。
  候補は scripts/page_watch.py が他の見張りの候補と同じ流れで web_articles.py に渡す
- 円の目安は ECB（欧州中央銀行）の参照レート（ドル円はユーロ経由の計算）。取れない日は Frankfurter（同じ ECB のデータ）、
  それも無理なら前の日のレートを使う。レートと日付は各ファイルの fx に残す
- 公式ページを機械で読めない製品は mode "manual"（人が公式ページを読んで書いた値。確認日つき）。円の目安だけ更新する

  python3 scripts/pricing_watch.py run              # 全製品を確認して書く
  python3 scripts/pricing_watch.py run --dry-run    # 読むだけ（書かない）
  python3 scripts/pricing_watch.py run --only claude
  python3 scripts/pricing_watch.py status
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pricing  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "pricing_watch.json"
NEWS_PATH = ROOT / "state" / "pricing_news.json"
JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=JPY"
BIG_CHANGE = 3.0
NEWS_KEEP_HOURS = 48
CHANGELOG_KEEP = 300
FAIL_ALERT = 3  # 続けてこの回数読めなかったら、ワークフローを失敗にして気づけるようにする


class ParseError(Exception):
    """ページの形が想定と違う（前のデータを残す）"""


def log(message: str) -> None:
    print(message, flush=True)


# --------------------------------------------------------------------------- #
# 取得
# --------------------------------------------------------------------------- #


def _http(url: str, accept: str = "*/*", timeout: int = 40, headers: dict | None = None) -> str:
    import gzip

    h = {"User-Agent": UA, "Accept": accept, "Accept-Encoding": "gzip", "Accept-Language": "ja,en;q=0.8"}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as res:
        data = res.read(6_000_000)
        enc = (res.headers.get("Content-Encoding") or "").lower()
    if data[:2] == b"\x1f\x8b" or enc == "gzip":
        data = gzip.decompress(data)
    return data.decode("utf-8", "replace")


def fetch(source: dict) -> str:
    """ページの本文。how=html はそのままの HTML、md はマークダウン、jina は読み取りサービス経由のマークダウン"""
    how = source.get("fetch", "html")
    if how == "jina":
        import feeds  # noqa: PLC0415 - 鍵が残高切れ（402）なら鍵なしの無料枠で読む（feeds.fetch_jina）

        data, _ = feeds.fetch_jina(source["url"], timeout=90)
        body = data.decode("utf-8", "replace")
        if "Markdown Content:" in body:
            body = body.split("Markdown Content:", 1)[1]
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
        return "\n".join(line.strip() for line in body.split("\n") if line.strip())
    if how == "md":
        return _http(source["url"], accept="text/markdown,text/plain;q=0.9,*/*;q=0.5")
    return _http(source["url"], accept="text/html,application/xhtml+xml,*/*;q=0.8")


# --------------------------------------------------------------------------- #
# 読み取りの道具
# --------------------------------------------------------------------------- #

MONEY_LINE = re.compile(r"(?:US)?\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")
YEN_RE = re.compile(r"[¥￥]\s?(\d{1,3}(?:,\d{3})+|\d+)")


def text_lines(raw: str) -> list[str]:
    """HTML を見える文字の行に"""
    t = re.sub(r"(?is)<(script|style|noscript|svg|template)\b.*?</\1>", " ", raw)
    t = re.sub(r"(?is)<br\s*/?>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", "\n", t)
    t = html.unescape(t).replace("\xa0", " ").replace("​", "")
    return [re.sub(r"\s+", " ", line).strip() for line in t.split("\n") if line.strip()]


def num(text: str) -> float:
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?", text)
    if not m:
        raise ParseError(f"金額が読めない: {text[:60]}")
    value = float(m.group(1).replace(",", "") + (("." + m.group(2)) if m.group(2) else ""))
    return int(value) if value.is_integer() else value


def usd(text: str) -> float:
    m = re.search(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)", text)
    if not m:
        raise ParseError(f"ドルの金額が読めない: {text[:60]}")
    return num(m.group(1))


def is_money_line(line: str) -> bool:
    return bool(MONEY_LINE.fullmatch(line.strip()))


def md_tables(md: str) -> list[dict]:
    """マークダウンの表（| a | b |）を {header, rows} に"""
    tables, cur = [], None
    for line in md.split("\n"):
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if cur is None:
                cur = {"header": cells, "rows": [], "start": md.find(line)}
            elif all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            else:
                cur["rows"].append(cells)
        elif cur is not None:
            tables.append(cur)
            cur = None
    if cur is not None:
        tables.append(cur)
    return tables


def clean_cell(text: str) -> str:
    text = re.sub(r"<sup>.*?</sup>", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[*_`]+", "", text).strip()


def first_header(lines: list[str], name: str, money_test, start: int = 0, within: int = 10) -> int:
    """name ちょうどの行のうち、すぐ後ろに金額があるもの（ナビや比較表の同じ名前を飛ばす）"""
    for i in range(start, len(lines)):
        if lines[i] == name and any(money_test(lines[j]) for j in range(i + 1, min(len(lines), i + within))):
            return i
    return -1


def strip_marks(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*†‡]", "", text)).strip(" ,")


def iso(d: date) -> str:
    return d.isoformat()


# --------------------------------------------------------------------------- #
# 製品ごとの読み取り（docs は {source key: 本文}。結果は plans か models）
# --------------------------------------------------------------------------- #


def _claude_prices(block: list[str], per: str) -> list[dict]:
    """金額の行と、その前後の説明（英語・日本語のどちらのページでも読む）"""
    out: list[dict] = []
    for k, line in enumerate(block):
        if not is_money_line(line):
            continue
        amount = usd(line)
        ctx = block[k + 1] if k + 1 < len(block) else ""
        prev = block[k - 1] if k else ""
        low = ctx.lower()
        label = f"{line} {ctx}".strip()
        if ("billed up front" in low or "一括請求" in ctx) and out and out[-1].get("billing") == "annual":
            out[-1]["billedAs"] = amount
            out[-1]["label"] += f" {line} {ctx}"
            continue
        if "billed monthly" in low or prev.endswith("月払いの場合は"):
            out.append({"amount": amount, "currency": "USD", "per": per, "billing": "monthly", "label": f"月払いの場合は {line}" if prev.endswith("月払いの場合は") else label})
        elif "annual" in low or "年間" in ctx or "年払い" in ctx:
            out.append({"amount": amount, "currency": "USD", "per": per, "billing": "annual", "label": label})
        elif low.startswith("per month") or ctx.startswith("月額"):
            out.append({"amount": amount, "currency": "USD", "per": per, "billing": "monthly", "label": label})
        elif "free for everyone" in low or "無料で提供" in ctx:
            out.append({"amount": amount, "currency": "USD", "per": per, "label": label})
    for p in out:
        p["taxIncluded"] = False
        p["label"] = re.sub(r"。?月払いの場合は$", "", p["label"]).strip()
    return out


LIMIT_RE = re.compile(r"(?i)\busage\b|5-hour limit|API credits|使用量|5 時間制限|API クレジット")


def _claude_limits(block: list[str]) -> list[str]:
    out = []
    for k, line in enumerate(block):
        if re.fullmatch(r"(?i)usage limits|利用制限", line) or not LIMIT_RE.search(line):
            continue
        text = line
        nxt = block[k + 1] if k + 1 < len(block) else ""
        if nxt and (nxt[0] in ",.（(" or nxt[0].islower()):
            text = text + ("" if nxt[0] in ",.（(" else " ") + nxt
        text = strip_marks(text)
        if text and text not in out:
            out.append(text)
    return out[:4]


def extract_claude(docs: dict, today: date) -> dict:
    lines = text_lines(docs["pricing"])
    plans, pos = [], 0
    spec = [("free", "Free", "free"), ("pro", "Pro", "standard"), ("max", "Max", "high"), ("ultra", "Ultra", "top")]
    heads = []
    for pid, name, tier in spec:
        i = first_header(lines, name, is_money_line, start=pos)
        if i < 0:
            raise ParseError(f"Claude の {name} が見つからない")
        heads.append((pid, name, tier, i))
        pos = i + 1
    for n, (pid, name, tier, i) in enumerate(heads):
        end = heads[n + 1][3] if n + 1 < len(heads) else i + 40
        stop = next((k for k in range(i + 1, min(end, len(lines))) if lines[k] in ("Compare", "Usage limits", "利用制限")), end)
        block = lines[i + 1:stop]
        prices = _claude_prices(block, "month")
        if not prices:
            raise ParseError(f"Claude の {name} の金額が読めない")
        plans.append({"id": pid, "name": name, "tier": tier, "audience": "individual", "prices": prices, "limits": _claude_limits(block)})
    for pid, headers, name in [("team-standard", ("スタンダードシート", "Standard seat"), "Team（スタンダードシート）"),
                               ("team-premium", ("プレミアムシート", "Premium seat"), "Team（プレミアムシート）")]:
        i = max(first_header(lines, h, is_money_line, start=pos, within=5) for h in headers)
        if i < 0:
            raise ParseError(f"Claude の Team {headers[1]} が見つからない")
        block = lines[i + 1:i + 8]
        prices = _claude_prices(block, "seat-month")
        if not prices:
            raise ParseError(f"Claude の Team {headers[1]} の金額が読めない")
        plans.append({"id": pid, "name": name, "tier": "team", "audience": "team", "prices": prices, "limits": _claude_limits(block[:2])})
    joined = "\n".join(lines)
    m = (re.search(r"US\$(\d+)/seat/month, billed annually, (\d+) seat minimum\.[^\n]*", joined)
         or re.search(r"US\$(\d+)/シート/月（年払い、最低(\d+)シート）。[^\n]*", joined))
    if not m:
        raise ParseError("Claude の Enterprise の金額が見つからない")
    seat_api = next((l for l in lines if l in ("Seat price + usage at API rates", "シート料金 + APIレートでの使用量")), "")
    plans.append({
        "id": "enterprise", "name": "Enterprise", "tier": "enterprise", "audience": "enterprise",
        "prices": [{"amount": num(m.group(1)), "currency": "USD", "per": "seat-month", "billing": "annual", "taxIncluded": False, "label": m.group(0)}],
        "limits": [strip_marks(m.group(0))] + ([seat_api] if seat_api else []),
    })
    notes = []
    team = re.search(r"For teams of \d+ to \d+|\d+〜\d+名のチーム向け", joined)
    if team:
        notes.append(f"Team: {team.group(0)}")
    pooled = re.search(r"Up to \$\d+ per month in pooled API credits|月額最大[\d,]+ドルのプールAPIクレジット", joined)
    if pooled:
        notes.append(f"Team: {pooled.group(0)}")
    tax = next((l for l in lines if "include applicable tax" in l or "税金が含まれていません" in l), "")
    return {"plans": plans, "taxNote": tax, "notes": notes}


def extract_claude_api(docs: dict, today: date) -> dict:
    md = docs["pricing"]
    table = next((t for t in md_tables(md) if any("Base input tokens" in h for h in t["header"])), None)
    if not table:
        raise ParseError("Claude API の料金表が見つからない")
    head = [h.lower() for h in table["header"]]

    def col(word):
        for i, h in enumerate(head):
            if word in h:
                return i
        raise ParseError(f"Claude API の列が無い: {word}")

    ci, co, cr, cw = col("base input"), col("output"), col("cache hits"), col("5m cache")
    footnotes = [re.sub(r"^\d+\s+", "", clean_cell(re.sub(r"</?sup>|^\*|\*$", "", l.strip()))) for l in md.split("\n") if re.search(r"<sup>\d", l) and "priced" in l]
    want = [
        ("claude-opus-5-5", "Claude Opus 5.5", "Claude Opus 5.5", None, "flagship", True),
        ("claude-sonnet-5-5", "Claude Sonnet 5.5", "Claude Sonnet 5.5", None, "mid", True),
        ("claude-haiku-5-5", "Claude Haiku 5.5", "Claude Haiku 5.5 (for prompts up to 100,000 tokens)", "入力10万トークンまで", "small", True),
        ("claude-haiku-5-5", "Claude Haiku 5.5", "Claude Haiku 5.5 (for prompts over 100,000 tokens)", "入力10万トークン超", "small", False),
    ]
    rows = {clean_cell(r[0]): r for r in table["rows"] if r}
    models = []
    for mid, name, label, tier, cls, primary in want:
        r = rows.get(label)
        if not r:
            raise ParseError(f"Claude API に {label} が無い")
        short = label.split(" (")[0]
        models.append({
            "id": mid, "name": name, "tier": tier, "class": cls, "primary": primary, "currency": "USD", "per": "1M tokens",
            "input": usd(r[ci]), "output": usd(r[co]), "cacheRead": usd(r[cr]), "cacheWrite": usd(r[cw]),
            "notes": [f for f in footnotes if short in f],
        })
    return {"models": models, "taxNote": "All prices are in USD.（税は含まない）"}


def extract_openai_api(docs: dict, today: date) -> dict:
    md = docs["pricing"]
    at = md.find("Standard pricing data")
    if at < 0:
        raise ParseError("OpenAI API の Standard の表が見つからない")
    tables = [t for t in md_tables(md[at:]) if any("Short context input" in h for h in t["header"])]
    if not tables:
        raise ParseError("OpenAI API の表（Short context）が見つからない")
    table = tables[0]
    head = table["header"]
    idx = {h: i for i, h in enumerate(head)}
    need = ["Short context input", "Short context output", "Short context cached input", "Long context input", "Long context output", "Long context cached input"]
    if any(n not in idx for n in need):
        raise ParseError("OpenAI API の列の名前が変わった")
    m = re.search(r"Short context: ([^.]+)\. Long context: ([^.]+)\.", md)
    short_label = f"入力{m.group(1).replace('input tokens', '').strip()}" if m else "短い入力"
    long_label = f"入力{m.group(2).replace('input tokens', '').strip()}" if m else "長い入力"
    rows = {clean_cell(r[0]): r for r in table["rows"] if r}
    want = [("gpt-6-astra", "GPT-6 Astra", "flagship"), ("gpt-6.1-sol", "GPT-6.1 Sol", "mid"),
            ("gpt-6.1-terra", "GPT-6.1 Terra", "small"), ("gpt-6-luna", "GPT-6 Luna", "small")]
    models = []
    for mid, name, cls in want:
        r = rows.get(mid)
        if not r:
            raise ParseError(f"OpenAI API に {mid} が無い")
        for prefix, label, primary in [("Short", short_label, True), ("Long", long_label, False)]:
            models.append({
                "id": mid, "name": name, "tier": label, "class": cls, "primary": primary, "currency": "USD", "per": "1M tokens",
                "input": usd(r[idx[f"{prefix} context input"]]), "output": usd(r[idx[f"{prefix} context output"]]),
                "cacheRead": usd(r[idx[f"{prefix} context cached input"]]), "notes": [],
            })
    return {"models": models, "taxNote": "Prices per 1M tokens（USD・税は含まない）"}


MONTH = "January|February|March|April|May|June|July|August|September|October|November|December"


def _parse_us_date(text: str) -> date:
    return datetime.strptime(re.sub(r"\s+", " ", text.strip()), "%B %d, %Y").date()


def _gemini_cell(cell: str, today: date) -> tuple[float, list[dict]]:
    """「$2.00 through November 30, 2026. $4.00 starting December 1, 2026.」→ (今日の価格, 予定の変更)"""
    cell = re.sub(r"\s+", " ", cell)
    through = re.search(rf"\$(\d+(?:\.\d+)?) through ((?:{MONTH}) \d{{1,2}}, \d{{4}})", cell)
    starting = re.search(rf"\$(\d+(?:\.\d+)?) starting ((?:{MONTH}) \d{{1,2}}, \d{{4}})", cell)
    if through and starting:
        until, since = _parse_us_date(through.group(2)), _parse_us_date(starting.group(2))
        if today <= until:
            return num(through.group(1)), [{"from": iso(since), "amount": num(starting.group(1))}]
        return num(starting.group(1)), []
    if re.search(r"(?i)prompts|<=|>", cell):
        raise ParseError(f"Gemini API の料金が段階つき（読み方を追加する）: {cell[:80]}")
    return usd(cell), []


def _gemini_section(md: str, model_id: str) -> str:
    m = re.search(rf"(?m)^## [^\n]*\{{% id=\"{re.escape(model_id)}\" %\}}", md)
    if not m:
        return ""
    nxt = re.search(r"(?m)^## ", md[m.end():])
    return md[m.start(): m.end() + (nxt.start() if nxt else len(md))]


def _gemini_standard_rows(section: str) -> dict:
    tab = re.search(r'\{% tab title="Standard" %\}(.*?)\{% /tab %\}', section, re.S)
    if not tab:
        raise ParseError("Gemini API の Standard のタブが無い")
    rows = {}
    for chunk in re.split(r"\n\s*---\s*\n", tab.group(1)):
        items = re.split(r"(?m)^\s*\* ", chunk)
        cells = [re.sub(r"\s+", " ", c).strip() for c in items[1:]]
        if len(cells) >= 3:
            rows[cells[0]] = cells
    return rows


def extract_gemini_api(docs: dict, today: date) -> dict:
    md = docs["pricing"]
    want = [("gemini-4-argon", "Gemini 4 Argon", "flagship"), ("gemini-3.8-flash", "Gemini 3.8 Flash", "small"), ("gemini-4-carbon", "Gemini 4 Carbon", None)]
    models = []
    for mid, name, cls in want:
        section = _gemini_section(md, mid)
        if not section:
            if mid == "gemini-4-carbon":  # 料金ページに載っていない（載ったら自動で読む）
                models.append({"id": mid, "name": name, "tier": None, "class": cls, "primary": True, "currency": "USD", "per": "1M tokens",
                               "status": "not_listed", "notes": ["Gemini API の料金ページに記載なし（確認日時点）"]})
                continue
            raise ParseError(f"Gemini API に {name} の節が無い")
        rows = _gemini_standard_rows(section)
        get = lambda key: next((v for k, v in rows.items() if k.lower().startswith(key)), None)  # noqa: E731
        inp, out, cache = get("input price"), get("output price"), get("context caching price")
        if not inp or not out:
            raise ParseError(f"Gemini API の {name} の入力・出力の行が無い")
        model = {"id": mid, "name": name, "tier": None, "class": cls, "primary": True, "currency": "USD", "per": "1M tokens", "notes": []}
        scheduled: dict[str, dict] = {}
        for key, cells in [("input", inp), ("output", out), ("cacheRead", cache)]:
            if not cells:
                continue
            value, later = _gemini_cell(cells[2], today)
            model[key] = value
            for s in later:
                scheduled.setdefault(s["from"], {"from": s["from"]})[key] = s["amount"]
            if key == "input" and cells[1]:
                model["freeTier"] = cells[1]
        if scheduled:
            model["scheduled"] = sorted(scheduled.values(), key=lambda s: s["from"])
        intro = section.split("{% tabs %}", 1)[0]  # 料金の表より前の注意書きだけ（別の話の囲みを拾わない）
        note = re.search(r"\{% callout type=\"note\" %\}\s*(.*?)\s*\{% /callout %\}", intro, re.S)
        if note:
            model["notes"].append(re.sub(r"\s+", " ", note.group(1)).strip()[:300])
        models.append(model)
    return {"models": models, "taxNote": "Paid Tier, per 1M tokens in USD（税は含まない）"}


def _yen_blocks(block: list[str]) -> list[tuple[float, str, str]]:
    """「￥」「2,900」「JPY / 月」の並び → (金額, 単位の行, 前の行)"""
    out = []
    for k, line in enumerate(block):
        if line in ("￥", "¥") and k + 1 < len(block) and re.fullmatch(r"\d{1,3}(?:,\d{3})*", block[k + 1]):
            unit = block[k + 2] if k + 2 < len(block) else ""
            out.append((num(block[k + 1]), unit, block[k - 1] if k else ""))
    return out


def extract_gemini(docs: dict, today: date) -> dict:
    lines = text_lines(docs["jp"])
    yen_start = lambda s: s in ("￥", "¥")  # noqa: E731
    heads = []
    pos = 0
    for pid, name, tier in [("free", "無料", "free"), ("ai-plus", "Google AI Plus", "low"), ("ai-pro", "Google AI Pro", "standard"), ("ai-ultra", "Google AI Ultra", "top")]:
        i = first_header(lines, name, yen_start, start=pos, within=14)
        if i < 0:
            raise ParseError(f"Gemini の {name} が見つからない")
        heads.append((pid, name, tier, i))
        pos = i + 1
    plans = []
    for n, (pid, name, tier, i) in enumerate(heads):
        end = heads[n + 1][3] if n + 1 < len(heads) else i + 60
        block = lines[i + 1:end]
        found = [b for b in _yen_blocks(block) if "最小構成価格" not in b[2]]
        if not found:
            raise ParseError(f"Gemini の {name} の金額が読めない")
        storage = next((l for l in block if re.search(r"\d+\s?(?:GB|TB) のクラウド ストレージ", l)), "")
        if pid == "ai-ultra":
            for amount, unit, _ in found:
                m = re.search(r":\s*(.+)$", unit)
                if not m:
                    raise ParseError("Gemini の AI Ultra の段の説明が読めない")
                label = m.group(1).strip()
                multiple = re.search(r"(\d+)\s*倍", label)
                plans.append({
                    "id": f"ai-ultra-{multiple.group(1)}x" if multiple else f"ai-ultra-{int(amount)}", "name": f"Google AI Ultra（{label.replace('の使用量上限', '')}）",
                    "tier": "top", "audience": "individual",
                    "prices": [{"amount": amount, "currency": "JPY", "per": "month", "billing": "monthly", "taxIncluded": None, "label": f"￥{amount:,} {unit}"}],
                    "limits": [label] + ([storage] if storage else []),
                })
            continue
        amount, unit, _ = found[0]
        usage = next((l for l in block if "使用量上限" in l), "")
        plans.append({
            "id": pid, "name": name if pid != "free" else "無料", "tier": tier, "audience": "individual",
            "prices": [{"amount": amount, "currency": "JPY", "per": "month", **({"billing": "monthly"} if amount else {}), "taxIncluded": None, "label": f"￥{amount:,} {unit}"}],
            "limits": [x for x in [usage, storage] if x],
        })
    for pid, header, tier in [("enterprise-business", "Business エディション", "team"), ("enterprise-standard", "Standard / Plus エディション", "enterprise")]:
        i = first_header(lines, header, is_money_line, within=8)
        if i < 0:
            raise ParseError(f"Gemini Enterprise の {header} が見つからない")
        k = next(k for k in range(i + 1, i + 8) if is_money_line(lines[k]))
        unit = lines[k + 1] if k + 1 < len(lines) else ""
        plans.append({
            "id": pid, "name": f"Gemini Enterprise {header}", "tier": tier, "audience": "team" if tier == "team" else "enterprise",
            "prices": [{"amount": usd(lines[k]), "currency": "USD", "per": "seat-month", "from": True, "taxIncluded": None, "label": f"最小構成価格: {lines[k]} {unit}"}],
            "limits": [l for l in lines[i + 1:i + 30] if l.startswith("ユーザー数")][:1],
        })
    return {"plans": plans, "taxNote": "Google の日本向けページの円表示（税の扱いはページに記載なし）"}


def extract_copilot(docs: dict, today: date) -> dict:
    lines = text_lines(docs["consumer"])
    yen_month = lambda s: bool(re.fullmatch(r"[¥￥][\d,]+/月", s))  # noqa: E731
    heads, pos = [], 0
    for pid, name, tier in [("m365-personal", "Microsoft 365 Personal", "low"), ("m365-family", "Microsoft 365 Family", "low"), ("m365-premium", "Microsoft 365 Premium", "standard")]:
        i = first_header(lines, name, yen_month, start=pos, within=3)
        if i < 0:
            raise ParseError(f"{name} の金額が見つからない")
        heads.append((pid, name, tier, i))
        pos = i + 1
    plans = []
    for n, (pid, name, tier, i) in enumerate(heads):
        end = heads[n + 1][3] if n + 1 < len(heads) else i + 30
        block = lines[i + 1:end]
        prices = []
        for l in block[:3]:
            if re.fullmatch(r"[¥￥][\d,]+/月", l):
                prices.append({"amount": num(l), "currency": "JPY", "per": "month", "billing": "monthly", "taxIncluded": None, "label": l})
            elif re.fullmatch(r"[¥￥][\d,]+/年", l):
                prices.append({"amount": num(l), "currency": "JPY", "per": "year", "billing": "annual", "taxIncluded": None, "label": l})
        limits = [l for l in block if "Copilot" in l and ("上限" in l or "使用量" in l)][:2]
        users = next((l for l in block if "ユーザー" in l), "")
        plans.append({"id": pid, "name": name, "tier": tier, "audience": "individual", "prices": prices,
                      "limits": ([users] if users else []) + limits})

    def biz_section(md: str, heading: str) -> list[list[str]]:
        parts = re.split(r"(?m)^### ", md)
        return [[l.strip() for l in p.split("\n") if l.strip()] for p in parts if p.split("\n", 1)[0].strip() == heading]

    sections = biz_section(docs["business"], "Microsoft 365 Copilot Business")
    biz_prices = []
    for sec in sections[:4]:
        body = "\n".join(sec)
        unit = next((l for l in sec if l.startswith("ユーザー/月")), "")
        promo = re.search(r"Originally starting from [¥￥]([\d,]+) now starting from [¥￥]([\d,]+)", body)
        amounts = [num(m.group(1)) for m in YEN_RE.finditer(body)]
        if not unit or not amounts:
            continue
        annual = "年払い" in unit
        if any(p["billing"] == ("annual" if annual else "monthly") for p in biz_prices):
            continue
        price = {"amount": num(promo.group(2)) if promo else amounts[0], "currency": "JPY", "per": "seat-month",
                 "billing": "annual" if annual else "monthly", "taxIncluded": False if "消費税は含まれていません" in body else None,
                 "label": (promo.group(0) + " " if promo else f"¥{amounts[0]:,} ") + unit}
        if promo:
            price.update({"regular": num(promo.group(1)), "from": True})
        biz_prices.append(price)
    if len(biz_prices) < 2:
        raise ParseError("Microsoft 365 Copilot Business の年払い・月払いが読めない")
    plans.append({"id": "m365-copilot-business", "name": "Microsoft 365 Copilot Business", "tier": "team", "audience": "team",
                  "prices": biz_prices, "limits": ["利用中の Microsoft 365 プランにアドオン"] if "アドオン" in docs["business"] else []})

    ent = biz_section(docs["enterprise"], "Microsoft 365 Copilot")
    if not ent:
        raise ParseError("Microsoft 365 Copilot（大企業向け）の節が無い")
    body = "\n".join(ent[0])
    unit = next((l for l in ent[0] if l.startswith("ユーザー/月")), "")
    yearly = YEN_RE.search(body)
    monthly = re.search(r"または毎月 [¥￥]([\d,]+) のお支払い", body)
    if not yearly or not unit:
        raise ParseError("Microsoft 365 Copilot（大企業向け）の金額が読めない")
    tax = False if "消費税は含まれていません" in body else None
    ent_prices = [{"amount": num(yearly.group(1)), "currency": "JPY", "per": "seat-month", "billing": "annual", "taxIncluded": tax, "label": f"¥{yearly.group(1)} {unit}"}]
    if monthly:
        ent_prices.append({"amount": num(monthly.group(1)), "currency": "JPY", "per": "seat-month", "billing": "monthly", "taxIncluded": tax, "label": monthly.group(0)})
    plans.append({"id": "m365-copilot", "name": "Microsoft 365 Copilot（大企業向け）", "tier": "enterprise", "audience": "enterprise", "prices": ent_prices, "limits": []})
    return {"plans": plans, "taxNote": "個人向けは税の扱いの記載なし。法人向けは「価格には消費税は含まれていません」（公式ページ）"}


def extract_chatgpt(docs: dict, today: date) -> dict:
    plus = re.search(r"\*\*Price:\*\*\s*\$(\d+)/month \(billed monthly\)", docs["plus"]) or re.search(r"for \$(\d+)/month", docs["plus"])
    if not plus:
        raise ParseError("ChatGPT Plus の金額が読めない")
    plans = [{"id": "plus", "name": "Plus", "tier": "standard", "audience": "individual",
              "prices": [{"amount": num(plus.group(1)), "currency": "USD", "per": "month", "billing": "monthly", "taxIncluded": None, "label": plus.group(0).replace("**", "")}],
              "limits": []}]
    table = next((t for t in md_tables(docs["pro"]) if any("Monthly price" in h for h in t["header"])), None)
    if not table:
        raise ParseError("ChatGPT Pro の表が見つからない")
    head = [clean_cell(h) for h in table["header"]]
    pi = next(i for i, h in enumerate(head) if "Monthly price" in h)
    rows = {clean_cell(r[0]): r for r in table["rows"] if r}
    for name, tier in [("Pro 100", "high"), ("Pro 200", "top"), ("Pro 500", "top")]:
        r = rows.get(name)
        if not r:
            raise ParseError(f"ChatGPT の {name} が表に無い")
        limits = [f"{head[i]}: {clean_cell(r[i])}" for i in range(len(head)) if i not in (0, pi) and i < len(r)]
        plans.append({"id": name.lower().replace(" ", "-"), "name": name, "tier": tier, "audience": "individual",
                      "prices": [{"amount": usd(r[pi]), "currency": "USD", "per": "month", "billing": "monthly", "taxIncluded": None, "label": f"{name} {clean_cell(r[pi])}（Monthly price (USD)）"}],
                      "limits": limits})
    biz = next((t for t in md_tables(docs["business"]) if t["rows"] and any(clean_cell(r[0]) == "Standard seat" for r in t["rows"])), None)
    if not biz:
        raise ParseError("ChatGPT Business の表が見つからない")
    for r in biz["rows"]:
        name = clean_cell(r[0])
        if name not in ("Standard seat", "Premium seat"):
            continue
        cell = clean_cell(r[1])
        mo = re.search(r"Monthly plan: \$(\d+) per user per month", cell)
        an = re.search(r"Annual plan: \$(\d+) per user per month", cell)
        if not mo or not an:
            raise ParseError(f"ChatGPT Business の {name} の金額が読めない")
        pid = "business-standard" if name == "Standard seat" else "business-premium"
        plans.append({"id": pid, "name": f"Business（{'スタンダード' if pid.endswith('standard') else 'プレミアム'}シート）", "tier": "team", "audience": "team",
                      "prices": [{"amount": num(an.group(1)), "currency": "USD", "per": "seat-month", "billing": "annual", "taxIncluded": None, "label": an.group(0) + ", billed annually"},
                                 {"amount": num(mo.group(1)), "currency": "USD", "per": "seat-month", "billing": "monthly", "taxIncluded": None, "label": mo.group(0)}],
                      "limits": [clean_cell(r[2])] if len(r) > 2 else []})
    if not any(p["id"] == "business-premium" for p in plans):
        raise ParseError("ChatGPT Business のプレミアムシートが無い")
    return {"plans": plans, "taxNote": "ドルの価格は OpenAI ヘルプセンター。円の価格は chatgpt.com の日本向け料金ページ（税の扱いの記載なし）"}


# --------------------------------------------------------------------------- #
# 製品の設定
# --------------------------------------------------------------------------- #

SPECS: dict[str, dict] = {
    "claude": {
        "name": "Claude", "company": "anthropic", "kind": "plan",
        "sources": [{"key": "pricing", "url": "https://claude.com/pricing", "title": "Claude 料金ページ（Plans & Pricing）", "fetch": "html"}],
        "extract": extract_claude, "required": ["free", "pro", "max", "ultra", "team-standard", "team-premium", "enterprise"],
        "ascending": ["free", "pro", "max", "ultra"],
    },
    "chatgpt": {
        "name": "ChatGPT", "company": "openai", "kind": "plan",
        "sources": [
            {"key": "plus", "url": "https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus", "title": "What is ChatGPT Plus?（OpenAI ヘルプ）", "fetch": "jina"},
            {"key": "pro", "url": "https://help.openai.com/en/articles/9793128-about-chatgpt-pro-tiers", "title": "About ChatGPT Pro tiers（OpenAI ヘルプ）", "fetch": "jina"},
            {"key": "business", "url": "https://help.openai.com/en/articles/8792828-what-is-chatgpt-business", "title": "What is ChatGPT Business?（OpenAI ヘルプ）", "fetch": "jina"},
        ],
        "manual_sources": [{"url": "https://chatgpt.com/ja-JP/pricing/", "title": "ChatGPT プランと料金（日本向け・円表示）"}],
        "plan_source": lambda pid: "plus" if pid == "plus" else "pro" if pid.startswith("pro") else "business",
        "extract": extract_chatgpt, "required": ["plus", "pro-100", "pro-200", "pro-500", "business-standard", "business-premium"],
        "ascending": ["pro-100", "pro-200", "pro-500"],
    },
    "gemini": {
        "name": "Gemini（Google AI プラン）", "company": "google", "kind": "plan",
        "sources": [{"key": "jp", "url": "https://gemini.google/jp/subscriptions/?hl=ja", "title": "Google AI プラン（日本）", "fetch": "html"}],
        "extract": extract_gemini, "required": ["free", "ai-plus", "ai-pro", "ai-ultra-5x", "ai-ultra-20x", "enterprise-business", "enterprise-standard"],
        "ascending": ["free", "ai-plus", "ai-pro", "ai-ultra-5x", "ai-ultra-20x"],
    },
    "copilot": {
        "name": "Microsoft Copilot（Microsoft 365）", "company": "microsoft", "kind": "plan",
        "sources": [
            {"key": "consumer", "url": "https://www.microsoft.com/ja-jp/microsoft-365/buy/compare-all-microsoft-365-products", "title": "Microsoft 365 の個人・家庭向けプラン比較（日本）", "fetch": "html"},
            {"key": "business", "url": "https://www.microsoft.com/ja-jp/microsoft-365/copilot/business", "title": "Microsoft Copilot の価格（一般法人向け・日本）", "fetch": "jina"},
            {"key": "enterprise", "url": "https://www.microsoft.com/ja-jp/microsoft-365/copilot/enterprise", "title": "Microsoft 365 Copilot の価格（大企業向け・日本）", "fetch": "jina"},
        ],
        "plan_source": lambda pid: {"m365-copilot-business": "business", "m365-copilot": "enterprise"}.get(pid, "consumer"),
        "extract": extract_copilot, "required": ["m365-personal", "m365-family", "m365-premium", "m365-copilot-business", "m365-copilot"],
        "ascending": ["m365-personal", "m365-family", "m365-premium"],
    },
    "perplexity": {
        "name": "Perplexity", "company": "perplexity", "kind": "plan", "manual": True,
        "manual_sources": [{"url": "https://www.perplexity.ai/pro", "title": "Perplexity Pro（料金・日本語表示）"}],
        "required": ["pro", "max"],
    },
    "claude-api": {
        "name": "Claude API", "company": "anthropic", "kind": "api",
        "sources": [{"key": "pricing", "url": "https://platform.claude.com/docs/en/about-claude/pricing.md", "page": "https://platform.claude.com/docs/en/about-claude/pricing", "title": "Claude API Pricing（公式ドキュメント）", "fetch": "md"}],
        "extract": extract_claude_api, "required": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
    },
    "openai-api": {
        "name": "OpenAI API", "company": "openai", "kind": "api",
        "sources": [{"key": "pricing", "url": "https://developers.openai.com/api/docs/pricing.md", "page": "https://developers.openai.com/api/docs/pricing", "title": "OpenAI API Pricing（公式ドキュメント）", "fetch": "md"}],
        "extract": extract_openai_api, "required": ["gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna"],
    },
    "gemini-api": {
        "name": "Gemini API", "company": "google", "kind": "api",
        "sources": [{"key": "pricing", "url": "https://aistudio.google.com/docs/pricing.md", "page": "https://ai.google.dev/gemini-api/docs/pricing", "title": "Gemini Developer API pricing（公式ドキュメント）", "fetch": "md"}],
        "extract": extract_gemini_api, "required": ["gemini-4-argon", "gemini-3.8-flash"],
    },
}


# --------------------------------------------------------------------------- #
# 確かめる（空・一部だけの表を書かない）
# --------------------------------------------------------------------------- #


def items_of(data: dict) -> list[dict]:
    return (data.get("plans") if data.get("kind") == "plan" else data.get("models")) or []


def _ok_number(v, cap: float) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= cap


def _has_price(item: dict, kind: str) -> bool:
    if kind == "api":
        if item.get("status") == "not_listed":
            return True
        return _ok_number(item.get("input"), 10_000) and _ok_number(item.get("output"), 10_000)
    prices = item.get("prices") or []
    return bool(prices) and all(_ok_number(p.get("amount"), 1_000_000 if p.get("currency") == "JPY" else 100_000) for p in prices)


def _main_amount(plan: dict) -> float | None:
    p = pricing._main_price(plan)  # noqa: SLF001
    if not p:
        return None
    return p["amount"] * (150 if p.get("currency") == "USD" else 1)


def validate(spec: dict, data: dict, old: dict | None) -> list[str]:
    """書いてよいか。問題のリスト（空なら書いてよい）"""
    kind = spec["kind"]
    problems = []
    items = items_of(data)
    if not items:
        return ["表が空"]
    ids = {i.get("id") for i in items}
    for rid in spec.get("required", []):
        if rid not in ids:
            problems.append(f"{rid} が無い")
    for item in items:
        if not item.get("id") or not item.get("name"):
            problems.append("名前の無い行がある")
        elif not _has_price(item, kind):
            problems.append(f"{item['id']} の金額が無い・おかしい")
    order = [next((p for p in items if p.get("id") == i), None) for i in spec.get("ascending", [])]
    amounts = [_main_amount(p) for p in order if p]
    if len(amounts) >= 2 and any(a is None or b is None or a >= b for a, b in zip(amounts, amounts[1:])):
        problems.append(f"段の金額の並びがおかしい: {amounts}")
    if old:
        for item in items_of(old):
            auto = kind == "api" or any(p.get("src", "auto") == "auto" for p in item.get("prices") or [])
            if auto and item.get("id") not in ids:
                problems.append(f"前回あった {item.get('id')} が無い（ページの形が変わった？）")
    return problems


def assert_complete(data: dict) -> None:
    """書く直前の最後の確認。空・金額の欠けた行があれば例外（ファイルに触らない）"""
    items = items_of(data)
    if not items:
        raise ValueError(f"{data.get('product')}: 空の表は書かない")
    for item in items:
        if not _has_price(item, data.get("kind", "plan")):
            raise ValueError(f"{data.get('product')}: {item.get('id')} の金額が欠けている表は書かない")


def write_json(path: Path, data) -> bool:
    """中身が変わったときだけ、一時ファイル→置き換えで書く（途中で止まっても壊れたファイルを残さない）"""
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if path.exists() and path.read_text("utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return True


def write_product(data: dict, base: Path | None = None) -> bool:
    assert_complete(data)
    return write_json((base or pricing.PRICING_DIR) / f"{data['product']}.json", data)


# --------------------------------------------------------------------------- #
# 変化を見つける
# --------------------------------------------------------------------------- #


def fingerprint(data: dict | None) -> dict:
    """金額だけの要約（変化の比較用）。キー → 値"""
    out: dict[str, float] = {}
    if not data:
        return out
    for plan in data.get("plans") or []:
        for p in plan.get("prices") or []:
            key = f"{plan.get('name')}｜{p.get('currency')}・{pricing.BILLING_JA.get(p.get('billing'), '-')}・{p.get('per') or '-'}"
            out[key] = p.get("amount")
            if p.get("billedAs"):
                out[key + "・一括"] = p["billedAs"]
    for m in data.get("models") or []:
        name = m.get("name") + (f"（{m['tier']}）" if m.get("tier") else "")
        for k in ("input", "output", "cacheRead"):
            if isinstance(m.get(k), (int, float)):
                out[f"{name}｜{k}"] = m[k]
        for s in m.get("scheduled") or []:
            for k in ("input", "output", "cacheRead"):
                if k in s:
                    out[f"{name}｜{k}（{s['from']}から）"] = s[k]
    return out


def diff(old: dict | None, new: dict) -> list[dict]:
    a, b = fingerprint(old), fingerprint(new)
    out = []
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            out.append({"item": key, "old": a.get(key), "new": b.get(key)})
    return out


def has_auto(data: dict | None) -> bool:
    if not data:
        return False
    if data.get("models"):
        return True
    return any(p.get("src", "auto") == "auto" for plan in data.get("plans") or [] for p in plan.get("prices") or [])


def is_big(changes: list[dict]) -> bool:
    for c in changes:
        o, n = c.get("old"), c.get("new")
        if isinstance(o, (int, float)) and isinstance(n, (int, float)) and o > 0 and n > 0:
            if n / o >= BIG_CHANGE or o / n >= BIG_CHANGE:
                return True
    return False


def merge_manual(new: dict, old: dict | None) -> dict:
    """人が公式ページを読んで書いた値（src: manual）は、自動で読めた表に残す"""
    if not old:
        return new
    plans = {p["id"]: p for p in new.get("plans") or []}
    order = [p["id"] for p in new.get("plans") or []]
    for op in old.get("plans") or []:
        manual = [p for p in op.get("prices") or [] if p.get("src") == "manual"]
        if op.get("manualLimits") and op["id"] in plans:
            target = plans[op["id"]]
            target["manualLimits"] = op["manualLimits"]
            target["limits"] = (target.get("limits") or []) + [x for x in op["manualLimits"] if x not in (target.get("limits") or [])]
        if not manual:
            continue
        if op["id"] in plans:
            plans[op["id"]]["prices"] = manual + [p for p in plans[op["id"]]["prices"]]
        elif all(p.get("src") == "manual" for p in op.get("prices") or []):
            plans[op["id"]] = op
            # 元の並びの位置に戻す（前の行の後ろ）
            prev = [p["id"] for p in old["plans"]]
            at = prev.index(op["id"])
            before = next((pid for pid in reversed(prev[:at]) if pid in order), None)
            order.insert(order.index(before) + 1 if before else 0, op["id"])
    new["plans"] = [plans[i] for i in order]
    return new


# --------------------------------------------------------------------------- #
# 為替
# --------------------------------------------------------------------------- #


def parse_ecb(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    when, rates = None, {}
    for el in root.iter():
        if el.get("time"):
            when = el.get("time")
        if el.get("currency") and el.get("rate"):
            rates[el.get("currency")] = float(el.get("rate"))
    if not when or "USD" not in rates or "JPY" not in rates:
        raise ParseError("ECB のレートが読めない")
    rate = round(rates["JPY"] / rates["USD"], 2)
    if not pricing.valid_rate(rate):
        raise ParseError(f"ドル円がおかしい: {rate}")
    return {"base": "USD", "quote": "JPY", "rate": rate, "date": when, "source": "ECB参照レート（ユーロ経由で計算）", "sourceUrl": ECB_URL}


def parse_frankfurter(text: str) -> dict:
    data = json.loads(text)
    rate = round(float(data["rates"]["JPY"]), 2)
    if not pricing.valid_rate(rate):
        raise ParseError(f"ドル円がおかしい: {rate}")
    return {"base": "USD", "quote": "JPY", "rate": rate, "date": data["date"], "source": "Frankfurter（ECBのデータ）", "sourceUrl": FRANKFURTER_URL}


def get_fx(http=_http, base: Path | None = None) -> dict | None:
    """今日のドル円。取れなければ前回の値（無ければ None）"""
    for url, parse in [(ECB_URL, parse_ecb), (FRANKFURTER_URL, parse_frankfurter)]:
        try:
            return parse(http(url))
        except Exception as error:  # noqa: BLE001
            log(f"[fx] {url} が使えない: {type(error).__name__}: {str(error)[:80]}")
    return pricing.load_fx(base)


# --------------------------------------------------------------------------- #
# 1製品の確認
# --------------------------------------------------------------------------- #


def _sources_meta(spec: dict, today: date, manual_checked: dict | None = None) -> list[dict]:
    out = [{"url": s.get("page", s["url"]), "title": s["title"], "checkedAt": iso(today), "how": "auto"} for s in spec.get("sources", [])]
    for s in spec.get("manual_sources", []):
        out.append({"url": s["url"], "title": s["title"], "checkedAt": (manual_checked or {}).get(s["url"], ""), "how": "manual"})
    return out


def _extract(spec: dict, fetcher, today: date) -> dict:
    docs = {s["key"]: fetcher(s) for s in spec["sources"]}
    return spec["extract"](docs, today)


def build(product: str, spec: dict, parsed: dict, old: dict | None, today: date, fx: dict | None) -> dict:
    manual_checked = {s["url"]: s.get("checkedAt", "") for s in (old or {}).get("sources", []) if s.get("how") == "manual"}
    data = {
        "product": product,
        "name": spec["name"],
        "company": spec["company"],
        "kind": spec["kind"],
        "mode": "auto",
        "checkedAt": iso(today),
        "updatedAt": (old or {}).get("updatedAt") or iso(today),
        "sources": _sources_meta(spec, today, manual_checked),
        "taxNote": parsed.get("taxNote", ""),
        "notes": parsed.get("notes", []),
    }
    if spec["kind"] == "plan":
        data["plans"] = parsed["plans"]
        for plan in data["plans"]:
            for p in plan["prices"]:
                p.setdefault("src", "auto")
            # 行ごとの出典（同じ製品で複数のページを読むとき、その行の金額を読んだページ）
            key = spec["plan_source"](plan["id"]) if spec.get("plan_source") else None
            src = next((x for x in spec.get("sources", []) if x["key"] == key), None) if key else None
            plan["sourceUrl"] = src.get("page", src["url"]) if src else (data["sources"][0]["url"] if data["sources"] else "")
        merge_manual(data, old)
        if any(p.get("src") == "manual" for plan in data["plans"] for p in plan["prices"]):
            data["mode"] = "mixed"
    else:
        data["models"] = parsed["models"]
        for m in data["models"]:
            m.setdefault("status", "listed")
            m.setdefault("sourceUrl", data["sources"][0]["url"])
    return pricing.attach_jpy(data, fx)


def news_item(product: str, data: dict, changes: list[dict], now: datetime, base: Path | None = None) -> dict:
    sig = hashlib.sha1(json.dumps(changes, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:8]
    url = data["sources"][0]["url"] if data.get("sources") else ""

    def fmt(v):
        return "なし" if v is None else str(v)

    lines = [f"- {c['item']}: {fmt(c['old'])} → {fmt(c['new'])}" for c in changes[:20]]
    head = changes[0]
    title = f"{data['name']}の料金が変更: {head['item'].split('｜')[0]} {fmt(head['old'])} → {fmt(head['new'])}"
    content = (
        f"【{data['name']}の公式料金ページ（{url}）で、{now.astimezone(JST):%Y-%m-%d} に料金の変更を確認】\n"
        + "\n".join(lines)
        + "\n\n【変更後の料金の段の表（サイソクの料金データ）】\n"
        + pricing.ladder_markdown(product, base)
    )
    return {
        "id": hashlib.sha1(f"pricing|{product}|{sig}".encode("utf-8")).hexdigest()[:16],
        "url": f"{url.split('#')[0]}#pricing-{now.astimezone(JST):%Y-%m-%d}-{sig[:6]}",
        "title": title[:200],
        "summary": " / ".join(lines[:3])[:600],
        "content": content[:12000],
        "published_at": now.isoformat(timespec="seconds"),
        "published_guessed": True,
        "source_name": f"{data['name']} 料金ページ",
        "source_slug": f"watch-pricing-{product}",
        "lang": "en",
        "entity": data.get("company", ""),
        "detectedAt": now.isoformat(timespec="seconds"),
        "pricingChanges": changes[:20],
    }


def check_product(product: str, *, fetcher=fetch, today: date | None = None, fx: dict | None = None,
                  state: dict | None = None, base: Path | None = None, confirm: bool = True) -> dict:
    """1製品。結果 {status: ok|changed|manual|error|hold|unstable, data, changes, error}（書かない）"""
    spec = SPECS[product]
    today = today or datetime.now(JST).date()
    old = pricing.load(product, base)
    st = state.setdefault(product, {}) if state is not None else {}
    if spec.get("manual"):
        if not old:
            return {"status": "error", "error": "手で書いたデータが無い（content/pricing に置く）", "data": None, "changes": []}
        return {"status": "manual", "data": pricing.attach_jpy(dict(old), fx), "changes": []}
    try:
        parsed = _extract(spec, fetcher, today)
        data = build(product, spec, parsed, old, today, fx)
    except Exception as error:  # noqa: BLE001
        return {"status": "error", "error": f"{type(error).__name__}: {str(error)[:160]}", "data": None, "changes": []}
    problems = validate(spec, data, old)
    if problems:
        return {"status": "error", "error": "；".join(problems)[:300], "data": None, "changes": []}
    # 前のデータに自動で読んだ金額が無い（初回・手で書いた分だけ）ときは基準にするだけで、変更の知らせは出さない
    changes = diff(old, data) if has_auto(old) else []
    if changes and confirm:
        # 表示の揺れ・A/B テストで間違った値を書かないよう、もう一度読んで同じときだけ採用する
        try:
            again = build(product, spec, _extract(spec, fetcher, today), old, today, fx)
        except Exception as error:  # noqa: BLE001
            return {"status": "unstable", "error": f"読み直しに失敗: {str(error)[:120]}", "data": None, "changes": changes}
        if fingerprint(again) != fingerprint(data):
            return {"status": "unstable", "error": "2回読んで金額が違った（表示が揺れている）", "data": None, "changes": changes}
    if changes and is_big(changes):
        sig = hashlib.sha1(json.dumps(fingerprint(data), sort_keys=True).encode("utf-8")).hexdigest()[:12]
        if st.get("hold") != sig:
            st["hold"] = sig
            return {"status": "hold", "error": "大きな変化（3倍以上）。次の回にも同じなら採用する", "data": None, "changes": changes}
    st.pop("hold", None)
    if changes:
        data["updatedAt"] = iso(today)
    elif old:
        data["updatedAt"] = old.get("updatedAt") or iso(today)
    return {"status": "changed" if changes else "ok", "data": data, "changes": changes}


# --------------------------------------------------------------------------- #
# まとめて実行
# --------------------------------------------------------------------------- #


def _load(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def run(only: list[str] | None = None, dry_run: bool = False, fetcher=fetch, http=_http, now: datetime | None = None,
        base: Path | None = None, state_path: Path | None = None, news_path: Path | None = None, confirm: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(JST).date()
    base = base or pricing.PRICING_DIR
    state_path = state_path or STATE_PATH
    news_path = news_path or NEWS_PATH
    state = _load(state_path, {})
    pstate = state.setdefault("products", {})
    fx = get_fx(http, base)
    if fx and not dry_run:
        write_json(base / "_fx.json", fx)
    report = {"date": iso(today), "fx": fx, "products": {}}
    log_rows = []
    news = []
    for product in only or list(SPECS):
        res = check_product(product, fetcher=fetcher, today=today, fx=fx, state=pstate, base=base, confirm=confirm)
        st = pstate.setdefault(product, {})
        status = res["status"]
        report["products"][product] = {"status": status, "error": res.get("error"), "changes": res["changes"]}
        if status in ("ok", "changed", "manual"):
            st.update({"ok": iso(today), "failures": 0})
            st.pop("error", None)
            if not dry_run and res["data"]:
                write_product(res["data"], base)
            if status == "changed":
                for c in res["changes"]:
                    log_rows.append({"date": iso(today), "product": product, "name": res["data"]["name"], **c,
                                     "sourceUrl": res["data"]["sources"][0]["url"]})
                news.append(news_item(product, res["data"], res["changes"], now, base) if not dry_run else {"title": f"{product} 変更"})
            log(f"[pricing] {product}: {status}" + (f"（{len(res['changes'])}件の変更）" if res["changes"] else ""))
        else:
            # 読めない・揺れている・確認待ち: 前のデータを残す（円の目安だけ新しいレートに）
            st["error"] = res.get("error")
            if status == "error":
                st["failures"] = int(st.get("failures", 0)) + 1
            old = pricing.load(product, base)
            if old and fx and not dry_run:
                write_product(pricing.attach_jpy(old, fx), base)
            log(f"[pricing] {product}: {status} — {res.get('error')}（前のデータを残す）")
    if not dry_run:
        if log_rows:
            rows = log_rows + _load(base / "_changelog.json", [])
            write_json(base / "_changelog.json", rows[:CHANGELOG_KEEP])
        if news:
            pending = _load(news_path, {}).get("pending", [])
            have = {p["id"] for p in pending}
            pending += [n for n in news if n["id"] not in have]
            since = now - timedelta(hours=NEWS_KEEP_HOURS)
            pending = [p for p in pending if datetime.fromisoformat(p["detectedAt"]) >= since][-20:]
            write_json(news_path, {"pending": pending})
        state["lastRun"] = now.isoformat(timespec="seconds")
        write_json(state_path, state)
    report["alerts"] = [p for p, s in pstate.items() if int(s.get("failures", 0)) >= FAIL_ALERT]
    report["news"] = [n.get("title") for n in news]
    return report


def news_candidates(now: datetime | None = None, path: Path | None = None) -> list[dict]:
    """page_watch.py 用: まだ新しい「料金が変わった」候補（page_watch の候補と同じ形）"""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=NEWS_KEEP_HOURS)
    out = []
    for p in _load(path or NEWS_PATH, {}).get("pending", []):
        try:
            if datetime.fromisoformat(p["detectedAt"]) >= since:
                out.append(p)
        except (KeyError, ValueError):
            continue
    return out


def status() -> int:
    state = _load(STATE_PATH, {}).get("products", {})
    for product, spec in SPECS.items():
        data = pricing.load(product)
        st = state.get(product, {})
        mode = "manual" if spec.get("manual") else (data or {}).get("mode", "-")
        log(f"- {product}（{mode}）: 確認日 {(data or {}).get('checkedAt', '-')} / 最後に読めた日 {st.get('ok', '-')}"
            + (f" / エラー {st['error']}（{st.get('failures', 0)}回続けて）" if st.get("error") else ""))
    fx = pricing.load_fx()
    if fx:
        log(f"為替: 1ドル={fx['rate']}円（{fx['date']}、{fx['source']}）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--only", action="append", choices=list(SPECS))
    r.add_argument("--report", default="")
    sub.add_parser("status")
    args = parser.parse_args()
    if args.cmd == "run":
        report = run(only=args.only, dry_run=args.dry_run)
        if args.report:
            Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
        if report["alerts"]:
            log(f"[pricing] {FAIL_ALERT}回続けて読めない製品: {', '.join(report['alerts'])}（読み取りを直す）")
            return 1
        return 0
    if args.cmd == "status":
        return status()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
