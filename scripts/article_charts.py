#!/usr/bin/env python3
"""記事の数字から図（charts）を作る部品。LLM は使わず、記事の表と本文だけから決まった手順で作る。

記事 JSON の任意の欄 `charts`（配列）に入れる。サイトは components/ArticleChart（lib/charts.ts）で、
本文の該当する章の下に、軽いインラインの棒グラフとして出す。

作る図は3種類だけ。どれも「比べられる本物の数字が2つ以上」あるときだけ作る。
  before_after  本文の「前／今回」の表（比べること | 前 | 今回 | 変化）で、両方が同じ単位の1つの数字の行
                 ・「1,400万から1,500万」のような「AからB」の書き方（表の中）
  series        1列目が年・月・年度・四半期の表で、同じ単位の数字が3つ以上並ぶもの
  ladder        料金プランの記事（題名に製品名と料金の言葉）。中身は持たず、表示のときに
                 content/pricing/<製品>.json（公式の料金ページから毎日作る表）から段を引く

数字を作らない約束:
  - 「〜」「超」「未満」「以上」「数千」など幅や下限の書き方の数字は使わない（棒の長さが嘘になる）
  - 単位（円・ドル・%・口座など）と桁（万・億）がそろう数字だけを同じ図に並べる
  - 差が大きすぎて片方の棒が見えなくなるもの（50倍超）は図にしない
  - 値の表示は記事に書いてある文字のまま（棒の長さだけを数字から決める）

使い方:
  python3 scripts/article_charts.py --days 7            # 直近7日の記事を調べて、作れる図を表示（書き込まない）
  python3 scripts/article_charts.py --days 7 --apply    # 記事 JSON の charts 欄を書き換える
  python3 scripts/article_charts.py --slug claude-ultra-plan-price-limits
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = ROOT / "content" / "articles"
PRICING_DIR = ROOT / "content" / "pricing"

MAX_RATIO = 50
MAX_GROUPS = 4

# 単位として認めるもの（長いものを先に）
UNITS = [
    "ポイント", "ユーザー", "項目", "トークン", "口座", "拠点", "施設", "世帯", "時間", "店舗",
    "ドル", "ユーロ", "ルピー", "円", "件", "人", "社", "台", "本", "店", "回", "倍", "個", "枚", "点", "校", "戸",
    "日", "年", "歳", "分", "秒", "行", "%", "GB", "TB", "MB", "W", "tps", "FPS", "fps",
]
COUNT_UNITS = {"口座", "件", "人", "社", "台", "店舗", "拠点", "施設", "世帯", "ユーザー", "校"}
SCALE = {"": 1, "千": 1e3, "万": 1e4, "億": 1e8, "兆": 1e12}
# 幅・下限・あいまいさを表す書き方（棒にしない）
FUZZY = re.compile(r"[〜~～]|超|未満|以上|以下|以内|数[十百千万億]|約?[0-9.]+\s*[-–]\s*[0-9]|前後|程度|ほど|強|弱|上限|最大|最低|から|→|基準|非公表|言及なし|不明|なし|推定|見込")

NUM = r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
VALUE_RE = re.compile(
    rf"^(?P<pre>約|月額?|年額?|1日|毎週)?\s*(?P<cur>\$|US\$|¥|￥)?\s*{NUM}\s*(?P<scale>千|万|億|兆)?\s*(?P<unit>{'|'.join(map(re.escape, UNITS))})?(?P<rest>.*)$"
)
PERIOD_RE = re.compile(r"^\s*(?:\d{4}年(?:度|\d{1,2}月(?:期)?|第?\d四半期|[1-4]Q|上期|下期)?|\d{4}[/-]\d{1,2}|\d{1,2}月|(?:FY|CY)\d{2,4}|令和\d+年度?|平成\d+年度?|\d{4}年\d{1,2}[-〜~]\d{1,2}月期)\s*$")
PERIOD_NOTE = re.compile(r"^(\d{4}年(度|\d{1,2}月)?|\d{1,2}月|\d{1,2}[-〜~]\d{1,2}月期|\d{4}年第?\d四半期|第?\d四半期|前月|前年|前年同月|前期)$")
BEFORE_HEAD = re.compile(r"^(前|以前|旧|従来|変更前|改定前|導入前|前回|前年|昨年|これまで|前モデル)")
AFTER_HEAD = re.compile(r"^(今回|新|変更後|改定後|導入後|今年|本年|今|最新|今回の新型)")


def _clean(cell: str) -> str:
    cell = re.sub(r"\*\*|__|`", "", cell).strip()
    return cell.replace("％", "%").replace("，", ",").replace("．", ".")


def parse_value(cell: str) -> dict | None:
    """表のセル1つから、棒にできる数字を1つ取り出す。取り出せない・あいまいなら None。"""
    text = _clean(cell)
    if not text or len(text) > 40:
        return None
    # 括弧の中は時期などの注記として分ける（「3.0%（8月）」）
    note = None
    m = re.match(r"^(.*?)\s*[（(]([^）)]{1,20})[）)]\s*$", text)
    if m:
        text, note = m.group(1).strip(), m.group(2).strip()
    if FUZZY.search(text):
        return None
    v = VALUE_RE.match(text)
    if not v:
        return None
    rest = v.group("rest").strip()
    # 単位の後ろに数字や別の値が続くもの（「1日36行、週200行」）は使わない
    if rest and (re.search(r"\d", rest) or len(rest) > 6):
        return None
    unit = v.group("unit") or ""
    cur = v.group("cur") or ""
    if cur in ("$", "US$"):
        unit = "ドル" if not unit else unit
    elif cur in ("¥", "￥"):
        unit = "円" if not unit else unit
    if unit in ("年", "日", "分", "秒") and not v.group("scale") and rest:
        return None
    number = float(v.group("num").replace(",", "")) * SCALE[v.group("scale") or ""]
    # 括弧の中が時期（「8月」「2019年」「4-6月期」）なら棒の名前に使い、表示からは外す。それ以外（「推計」など）は表示に残す
    period = note if note and PERIOD_NOTE.match(note) else None
    return {
        "value": number,
        "unit": unit,
        "scale": v.group("scale") or "",
        "display": text if period else _clean(cell),
        "note": period,
    }


def _comparable(a: dict, b: dict) -> bool:
    if a["unit"] != b["unit"]:
        return False
    hi, lo = max(a["value"], b["value"]), min(a["value"], b["value"])
    if hi <= 0:
        return False
    return lo == 0 or hi / lo <= MAX_RATIO


def _tables(body: str):
    """本文の Markdown の表と、その表がある章（直前の ## 見出し）を返す。"""
    lines = body.split("\n")
    heading = None
    i = 0
    while i < len(lines):
        line = lines[i]
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h:
            heading = h.group(1).strip()
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            header = _cells(line)
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(_cells(lines[j]))
                j += 1
            yield heading, header, rows
            i = j
            continue
        i += 1


def _cells(line: str) -> list[str]:
    parts = line.strip().strip("|").split("|")
    return [p.strip() for p in parts]


def _head_label(cell: str, fallback: str) -> str:
    """「前（Gemini 4 Argon）」→「Gemini 4 Argon」、「前」→「前」"""
    text = _clean(cell)
    m = re.match(r"^(?:前|今回|従来|新|旧)[^（(]*[（(]([^）)]{1,24})[）)]$", text)
    if m:
        return m.group(1)
    return text[:24] or fallback


def _delta_unit(unit: str) -> str:
    return unit


def before_after_from_table(heading, header, rows) -> dict | None:
    if len(header) < 3:
        return None
    h1, h2 = _clean(header[1]), _clean(header[2])
    periods = PERIOD_RE.match(h1) and PERIOD_RE.match(h2)
    if not periods and not (BEFORE_HEAD.match(h1) and AFTER_HEAD.match(h2)):
        return None
    groups = []
    for row in rows:
        if len(row) < 3:
            continue
        a, b = parse_value(row[1]), parse_value(row[2])
        if not a or not b or not _comparable(a, b) or a["value"] == b["value"]:
            continue
        groups.append({
            "label": _clean(row[0])[:40],
            "unit": a["unit"],
            "items": [
                {"label": a["note"] or _head_label(header[1], "前"), "value": a["value"], "display": a["display"]},
                {"label": b["note"] or _head_label(header[2], "今回"), "value": b["value"], "display": b["display"], "highlight": True},
            ],
        })
    if not groups:
        return None
    # 同じ単位の行だけを1つの図に（棒の長さを同じ物差しで比べられるように）。いちばん多い単位を選ぶ
    by_unit: dict[str, list] = {}
    for g in groups:
        by_unit.setdefault(g["unit"], []).append(g)
    unit, chosen = max(by_unit.items(), key=lambda kv: len(kv[1]))
    chosen = chosen[:MAX_GROUPS]
    values = [it["value"] for g in chosen for it in g["items"]]
    if max(values) <= 0 or (min(values) > 0 and max(values) / min(values) > MAX_RATIO):
        # 行どうしの差が大きすぎるときは、最初の1行だけ
        chosen = chosen[:1]
    a_label, b_label = _head_label(header[1], "前"), _head_label(header[2], "今回")
    if len(chosen) == 1:
        title = chosen[0]["label"]
    elif (a_label, b_label) == ("前", "今回"):
        title = "主な数字の前と今"
    else:
        title = f"{a_label} → {b_label}"
    return {
        "kind": "before_after",
        "title": title,
        "unit": unit,
        "groups": [{"label": g["label"], "items": g["items"]} for g in chosen],
        "afterHeading": heading,
        "from": "table",
    }


FROM_TO = re.compile(
    rf"(?P<a>\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)\s*(?P<sa>万|億)?\s*(?P<ua>{'|'.join(map(re.escape, UNITS))})?\s*(?:から|→|⇒)\s*(?P<b>\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)\s*(?P<sb>万|億)?\s*(?P<ub>{'|'.join(map(re.escape, UNITS))})?"
)


def _fmt(num: str) -> str:
    n = num.replace(",", "")
    if "." in n:
        return n
    return f"{int(n):,}"


def _unit_in_body(body: str, num: str, scale: str) -> str:
    """本文で数字の直後に付いている単位（「1,500万口座」→「口座」）"""
    for form in {num, _fmt(num), num.replace(",", "")}:
        m = re.search(re.escape(form) + re.escape(scale) + rf"\s*({'|'.join(map(re.escape, UNITS))})", body)
        if m:
            return m.group(1)
    return ""


def _date_before(body: str, num: str, scale: str) -> str | None:
    """「2026年4月に1,400万口座に到達」→「2026年4月」"""
    for form in {_fmt(num), num.replace(",", "")}:
        m = re.search(r"(\d{4}年\d{1,2}月)[^。\n]{0,12}?" + re.escape(form) + re.escape(scale), body)
        if m:
            return m.group(1)
    return None


def before_after_from_to(article: dict) -> dict | None:
    body = article.get("body", "")
    for heading, header, rows in _tables(body):
        for row in rows:
            for cell in row[1:2]:
                text = _clean(cell)
                m = FROM_TO.search(text)
                if not m:
                    continue
                sa, sb = m.group("sa") or m.group("sb") or "", m.group("sb") or ""
                if (m.group("sa") or "") not in ("", sb):
                    continue
                ua = m.group("ua") or m.group("ub") or _unit_in_body(body, m.group("b"), sb)
                ub = m.group("ub") or ua
                if not ua or ua != ub:
                    continue
                a = float(m.group("a").replace(",", "")) * SCALE[sa]
                b = float(m.group("b").replace(",", "")) * SCALE[sb]
                if a == b or a <= 0 or max(a, b) / min(a, b) > MAX_RATIO:
                    continue
                when_a = _date_before(body, m.group("a"), sa)
                if not when_a:
                    # 「いつの数字か」が本文で確かめられないものは、前と今の図にしない（別の物どうしの比較かもしれない）
                    continue
                published = article.get("sourcePublishedAt") or article.get("publishedAt") or ""
                when_b = None
                if re.match(r"\d{4}-\d{2}", published):
                    when_b = f"{int(published[:4])}年{int(published[5:7])}月"
                label = _clean(row[0])
                title = f"{ua}数の変化" if ua in COUNT_UNITS else label
                return {
                    "kind": "before_after",
                    "title": title,
                    "unit": ua,
                    "groups": [{
                        # 表の行の名前（「口座増の期間」）は図の中では意味がずれるので、題と同じにして出さない
                        "label": title,
                        "items": [
                            {"label": when_a, "value": a, "display": f"{_fmt(m.group('a'))}{sa}{ua}"},
                            {"label": f"{when_b}（今回）" if when_b else "今回", "value": b, "display": f"{_fmt(m.group('b'))}{sb}{ub}", "highlight": True},
                        ],
                    }],
                    "afterHeading": heading,
                    "from": "from-to",
                }
    return None


def series_from_table(heading, header, rows) -> dict | None:
    if len(rows) < 3 or len(header) < 2:
        return None
    if not all(len(r) >= 2 and PERIOD_RE.match(_clean(r[0])) for r in rows):
        return None
    points = []
    for r in rows:
        v = parse_value(r[1])
        if not v:
            return None
        points.append({"label": _clean(r[0]), "value": v["value"], "display": v["display"], "unit": v["unit"]})
    units = {p["unit"] for p in points}
    if len(units) != 1:
        return None
    values = [p["value"] for p in points]
    if max(values) <= 0 or (min(values) > 0 and max(values) / min(values) > MAX_RATIO):
        return None
    return {
        "kind": "series",
        "title": _clean(header[1]) or heading or "推移",
        "unit": units.pop(),
        "points": [{k: p[k] for k in ("label", "value", "display")} for p in points[:12]],
        "afterHeading": heading,
        "from": "table",
    }


# 料金の段の図（表示のときに content/pricing から引く）。製品の名前と、プランの名前
LADDER_PRODUCTS = {
    "claude": {"names": ["Claude"], "not": ["Claude Code", "Claude API", "Claude Platform"], "plans": {"free": ["Free", "無料"], "pro": ["Pro"], "max": ["Max"], "ultra": ["Ultra"]}},
    "chatgpt": {"names": ["ChatGPT"], "not": [], "plans": {"free": ["無料", "Free"], "go": ["Go"], "plus": ["Plus"], "pro-200": ["Pro"]}},
    "gemini": {"names": ["Google AI Pro", "Google AI Ultra", "Google AI Plus", "Gemini"], "not": ["Gemini API", "Gemini 4"], "plans": {"ai-plus": ["AI Plus"], "ai-pro": ["AI Pro"], "ai-ultra-5x": ["AI Ultra"]}},
    "perplexity": {"names": ["Perplexity"], "not": [], "plans": {"pro": ["Pro"], "max": ["Max"]}},
}
PRICE_WORDS = re.compile(r"料金|プラン|月額|年額|値上げ|値下げ|価格|\d+ドル|\d+円|サブスク")
# 新しいモデル・API・企業向けの話は、個人向けプランの段の図を出さない
NOT_PLAN_TOPIC = re.compile(r"モデル|API|エンタープライズ|Enterprise|法人|提携")


def ladder_for(article: dict) -> dict | None:
    # SEO 用の題名は「〜とは？料金」の型が多いので、本来の題名と「料金プラン」のタグだけで決める
    title = article.get("title") or ""
    tags = article.get("tags") or []
    if not (PRICE_WORDS.search(title) or "料金プラン" in tags) or NOT_PLAN_TOPIC.search(title):
        return None
    for product, spec in LADDER_PRODUCTS.items():
        if not any(n in title for n in spec["names"]):
            continue
        stripped = title
        for n in spec["not"]:
            stripped = stripped.replace(n, "")
        if not any(n in stripped for n in spec["names"]):
            continue
        highlight = None
        for plan_id, words in spec["plans"].items():
            if any(re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", stripped) for w in words):
                highlight = plan_id
        heading = None
        for line in article.get("body", "").split("\n"):
            h = re.match(r"^##\s+(.+?)\s*$", line)
            if h and re.search(r"料金|価格|値段|いくら|プラン", h.group(1)):
                heading = h.group(1)
                break
        return {"kind": "ladder", "product": product, **({"highlight": highlight} if highlight else {}), "afterHeading": heading}
    return None


def derive_charts(article: dict) -> list[dict]:
    charts: list[dict] = []
    body = article.get("body", "")
    for heading, header, rows in _tables(body):
        c = before_after_from_table(heading, header, rows) or series_from_table(heading, header, rows)
        if c and not any(x["kind"] == c["kind"] for x in charts):
            charts.append(c)
    if not any(c["kind"] == "before_after" for c in charts):
        c = before_after_from_to(article)
        if c:
            charts.append(c)
    ladder = ladder_for(article)
    if ladder:
        charts.append(ladder)
    # 出典は記事の元の発表（数字はすべて本文の表から取っている）
    source = {"name": article.get("sourceName") or article.get("channel") or "", "url": article.get("sourceUrl") or article.get("videoUrl") or ""}
    for c in charts:
        if c["kind"] != "ladder" and source["name"]:
            c["source"] = {k: v for k, v in source.items() if v}
    return charts[:3]


def _recent(days: int):
    cut = datetime.now(timezone.utc) - timedelta(days=days)
    for path in sorted(ARTICLES.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        stamp = record.get("generatedAt") or record.get("publishedAt") or ""
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= cut:
            yield path, record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--slug", action="append")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    targets = (
        [(ARTICLES / f"{s}.json", json.loads((ARTICLES / f"{s}.json").read_text(encoding="utf-8"))) for s in args.slug]
        if args.slug
        else list(_recent(args.days))
    )
    changed = 0
    for path, record in targets:
        charts = derive_charts(record)
        if charts == (record.get("charts") or []):
            continue
        if not charts and "charts" not in record:
            continue
        print(f"{record['slug']}: " + ", ".join(f"{c['kind']}「{c.get('title') or c.get('product')}」" for c in charts) if charts else f"{record['slug']}: （図なし・削除）")
        if args.apply:
            if charts:
                record["charts"] = charts
            else:
                record.pop("charts", None)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changed += 1
    print(f"{'書き換え' if args.apply else '候補'}: {changed}本 / 対象 {len(targets)}本", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
