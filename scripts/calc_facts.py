#!/usr/bin/env python3
"""数字の計算部品（決定的。LLM なし）。

無料の LLM は計算を間違えやすいので、記事に使う「計算した数字」はここで作り、式つきで LLM に渡す。
LLM は計算しない（式の結果をそのまま写すだけ）。品質ゲート（quality.number_problems）は、
記事の数字が「元記事・確認済みの事実の束・計算済みの数字」のどれかにあるかをここの部品で確かめる。

計算するもの（どれも元記事・過去記事・料金表の数字から）:
- ペース: 「約7カ月で100万口座増」→ 1カ月あたり約14.3万口座
- 年換算: 「1-10月累計67件」→ このペースなら年約80件
- 前と比べた差・増減率: 「67件（前年同期56件）」→ 11件増・19.6%増
- 割合: 「負債総額20億7,700万円 … 最大の倒産は負債11億円」→ 53.0%
- 進み具合: 「上限2,000億円 … 累計1,048億円」→ 52.4%
- 1つあたり: 「総額272億円 … 総数1億5,700万株」→ 1株あたり約173円
- 料金の段の「使う量あたりの値段」: Max $100（Pro の5倍）→ Pro 1倍分あたり $20（Pro と同じ）
- 円への換算: $200 × 157.90円 = 約31,580円（為替のレートと日付つきの目安）
- 過去記事との比較: 前回の記事の1,400万口座 → 今回1,500万口座

  python3 scripts/calc_facts.py "本文…"     # 文章から数字を取り出して計算結果を表示（試し）
"""

from __future__ import annotations

import math
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

JST_TZ = timezone(timedelta(hours=9))

# --------------------------------------------------------------------------- #
# 数字の取り出し
# --------------------------------------------------------------------------- #

NUM = r"\d+(?:,\d{3})*(?:\.\d+)?"
# 日本語の数（「20億7,700万」「3万1,580」「1,500万」「14.3万」）
JP_NUM = rf"{NUM}(?:\s?[兆億万千](?:\s?{NUM})?)*"
JP_MULT = {"兆": 1e12, "億": 1e8, "万": 1e4, "千": 1e3}
EN_MULT = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12, "k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9}

MONEY_PREFIX = r"(?P<pre>US\$|\$|＄|USD\s?|¥|￥|€)"
COUNTERS = "件|口座|社|人|名|台|株|店舗|世帯|戸|万人"
UNIT = rf"(?P<unit>円|米ドル|ドル|ユーロ|%|％|パーセント|倍|(?<=\d)x\b|{COUNTERS})"
QTY_RE = re.compile(
    rf"(?:{MONEY_PREFIX}\s?)?(?P<num>{JP_NUM})(?:\s?(?P<en>thousand|million|billion|trillion|bn|[kKmMbB](?![A-Za-z])))?\s?{UNIT}?"
)
APPROX_BEFORE_RE = re.compile(r"(?:約|およそ|ほぼ|概ね|おおむね|推定|最大|最大で|少なくとも|over|about|approximately|nearly|around|~)\s*$", re.I)
APPROX_AFTER_RE = re.compile(r"^\s*(?:前後|程度|ほど|強|弱|超|余り|あまり|近く|以上|未満|以下|超え|台)")
SENTENCE_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")
WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "twenty": 20, "fifty": 50, "hundred": 100, "twice": 2, "double": 2, "triple": 3, "half": 0.5,
}
WORD_MULT_RE = re.compile(r"\b(a|an|one|two|three|four|five|six|seven|eight|nine|ten|twenty|fifty|hundred)\s+(thousand|million|billion|trillion)\b", re.I)
WORD_NUM_RE = re.compile(r"\b(" + "|".join(WORD_NUMBERS) + r")\b", re.I)


@dataclass
class Qty:
    value: float
    unit: str  # JPY / USD / EUR / % / x / 件・口座など / "" （単位なし）
    text: str
    start: int
    end: int
    approx: bool = False
    has_mult: bool = False  # 万・億・million など
    before: str = ""  # 同じ文の直前（最大30字）
    after: str = ""  # 直後（最大24字）
    sentence: str = ""
    para: int = 0  # 何番目の段落か

    @property
    def is_money(self) -> bool:
        return self.unit in ("JPY", "USD", "EUR")


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def parse_jp(num: str) -> float:
    """「20億7,700万」→ 2077000000.0、「3万1,580」→ 31580.0"""
    total, current = 0.0, None
    for n, mult in re.findall(r"(\d[\d,]*(?:\.\d+)?)|([兆億万千])", num):
        if n:
            current = float(n.replace(",", ""))
        else:
            total += (current if current is not None else 1.0) * JP_MULT[mult]
            current = None
    return total + (current or 0.0)


def _unit_of(pre: str | None, unit: str | None) -> str:
    pre = (pre or "").strip()
    if pre in ("$", "＄", "US$", "USD"):
        return "USD"
    if pre in ("¥", "￥"):
        return "JPY"
    if pre == "€":
        return "EUR"
    unit = unit or ""
    if unit == "円":
        return "JPY"
    if unit in ("ドル", "米ドル"):
        return "USD"
    if unit == "ユーロ":
        return "EUR"
    if unit in ("%", "％", "パーセント"):
        return "%"
    if unit in ("倍", "x"):
        return "x"
    if unit == "万人":
        return "人"
    return unit


def extract(text: str) -> list[Qty]:
    """文章の中の数字（単位つきも単位なしも）。日付・年（「2026年」「11月3日」）は単位なしで返る"""
    text = nfkc(text)
    out: list[Qty] = []
    para_starts = [m.start() for m in re.finditer(r"\n\s*\n", text)]
    for m in QTY_RE.finditer(text):
        num = m.group("num")
        value = parse_jp(num)
        en = (m.group("en") or "").lower()
        if en:
            value *= EN_MULT[en]
        unit = _unit_of(m.group("pre"), m.group("unit"))
        if m.group("unit") == "万人":
            value *= 1e4
        # 「2026年」の年・「11月」の月・「3日」の日は単位として扱わない（単位なし＝日付の数字）
        start, end = m.start(), m.end()
        sent_start = max(text.rfind(c, 0, start) for c in "。！？!?\n") + 1
        sent_end_candidates = [i for i in (text.find(c, end) for c in "。！？!?\n") if i >= 0]
        sent_end = min(sent_end_candidates) + 1 if sent_end_candidates else len(text)
        before = text[max(sent_start, start - 30):start]
        after = text[end:end + 6]
        out.append(Qty(
            value=value, unit=unit, text=m.group(0).strip(), start=start, end=end,
            approx=bool(APPROX_BEFORE_RE.search(before) or APPROX_AFTER_RE.search(after)),
            has_mult=bool(re.search(r"[兆億万千]", num) or en or m.group("unit") == "万人"),
            before=before, after=text[end:end + 24], sentence=text[sent_start:sent_end].strip(),
            para=sum(1 for p in para_starts if p < start),
        ))
    return out


def values(text: str) -> list[float]:
    """文章に出てくる数の値すべて（単位を問わない。品質ゲートの「使ってよい数字」用）。英語の数の言葉（two・twice）も含む"""
    found = [q.value for q in extract(text)]
    found += [float(WORD_NUMBERS[w.lower()]) for w in WORD_NUM_RE.findall(text or "")]
    # 「one billion」「a million」「two thousand」
    for word, mult in WORD_MULT_RE.findall(text or ""):
        base = 1.0 if word.lower() in ("a", "an") else float(WORD_NUMBERS.get(word.lower(), 1))
        found.append(base * EN_MULT[mult.lower()])
    return found


# 品質ゲートで確かめる数字（お金・割合・倍・大きな件数）。日付・時間・小さな数は確かめない
CHECK_COUNTERS = {"件", "口座", "社", "人", "名", "台", "株", "店舗", "世帯", "戸"}


def checkable(q: Qty) -> bool:
    if q.value == 0:
        return False
    if q.is_money or q.unit == "%":
        return True
    if q.unit == "x":
        return q.value >= 1.1
    if q.unit in CHECK_COUNTERS:
        return q.value >= 10 or q.has_mult
    return False


def sig_round(value: float, digits: int = 3) -> float:
    if value == 0:
        return 0.0
    return round(value, digits - 1 - int(math.floor(math.log10(abs(value)))))


def supported(q: Qty, allowed: list[float]) -> bool:
    """記事の数字 q が、使ってよい数字のどれかと（丸めの範囲で）一致するか。「約」などの付いた数字は6%まで、ほかは1%まで"""
    tol = 0.06 if q.approx else 0.01
    for a in allowed:
        if abs(q.value - a) <= tol * max(abs(a), 1e-9) or abs(q.value - a) < 1e-9:
            return True
    return False


# --------------------------------------------------------------------------- #
# 表示
# --------------------------------------------------------------------------- #


def _comma(value: float, decimals: int = 0) -> str:
    return f"{value:,.{decimals}f}"


def fmt_num(value: float) -> str:
    """数を日本語の読みやすい形に（3桁の有効数字）。1,234 / 14.3万 / 2,370億 / 1.5兆"""
    v = abs(value)
    sign = "-" if value < 0 else ""
    if v >= 1e12:
        return f"{sign}{sig_round(v / 1e12):g}兆"
    if 1e8 <= v < 1e10 and round((v % 1e8) / 1e4, -2):
        oku, man = int(v // 1e8), round((v % 1e8) / 1e4, -2)
        if man >= 1e4:
            oku, man = oku + 1, 0
        return f"{sign}{oku}億" + (f"{_comma(man)}万" if man else "")
    if v >= 1e8:
        n = sig_round(v / 1e8)
        return f"{sign}{_comma(n, 0) if n >= 100 else f'{n:g}'}億"
    if v >= 1e4:
        n = sig_round(v / 1e4)
        return f"{sign}{_comma(n, 0) if n >= 100 else f'{n:g}'}万"
    if v >= 100:
        return f"{sign}{_comma(round(v))}"
    return f"{sign}{sig_round(v):g}"


def fmt_qty(value: float, unit: str) -> str:
    if unit == "JPY":
        if abs(value) < 1e6:
            return f"{_comma(round(value, -1) if abs(value) >= 1000 else round(value))}円"
        return f"{fmt_num(value)}円"
    if unit == "USD":
        return f"${_comma(value, 2).rstrip('0').rstrip('.')}" if abs(value) < 1e6 else f"{fmt_num(value)}ドル"
    if unit == "EUR":
        return f"{fmt_num(value)}ユーロ"
    if unit == "%":
        return f"{value:.1f}%"
    if unit == "x":
        return f"{sig_round(value):g}倍"
    return f"{fmt_num(value)}{unit}"


def fmt_pct(value: float) -> str:
    return f"{value:+.1f}%".replace("+", "＋").replace("-", "−")


# --------------------------------------------------------------------------- #
# 計算
# --------------------------------------------------------------------------- #


@dataclass
class Computed:
    kind: str  # pace / annual / change / share / progress / per_unit / ladder / yen / past
    text: str  # LLM に渡す1行（結果と式）
    values: list[float] = field(default_factory=list)  # 結果の数（品質ゲートで「使ってよい数字」になる）


PERIOD_RE = re.compile(r"(約)?\s?(\d+(?:\.\d+)?)\s?(カ月|か月|ヶ月|ケ月|ヵ月|カ年|年|週間|日)(?:間)?(?:で|の間に|をかけて)")
MONTHS_PER = {"カ月": 1, "か月": 1, "ヶ月": 1, "ケ月": 1, "ヵ月": 1, "年": 12, "カ年": 12, "週間": 7 / 30.44, "日": 1 / 30.44}
CUMULATIVE_RE = re.compile(r"(?<![\d年])(1)\s?[-－〜~～ー]\s?(\d{1,2})\s?月(?:の)?(?:累計|合計)?")
PREV_RE = re.compile(r"(前年同期|前年同月|前年同時期|前年度|前年|前月|前回|前期|前四半期|昨年|去年)(?:比)?(?:（[^）]{0,12}）|\([^)]{0,12}\))?(?:は|の|が|：|:|、)?\s?$")
TOTAL_LABEL_RE = re.compile(r"(総額|合計|総数|全体|累計額)(?:は|が|：|:)?\s?(?:約)?\s?$")
PART_RE = re.compile(r"最大|うち|そのうち")
GOAL_RE = re.compile(r"(上限|目標|計画|予定総額|取得枠|枠)")
DONE_RE = re.compile(r"(累計|取得済み|これまでに|これまで|達成|到達|突破|取得した|実績)")


def _same_unit(a: Qty, b: Qty) -> bool:
    return a.unit == b.unit and a.unit != ""


def _ctx(q: Qty, n: int = 14) -> str:
    """数字の前の言葉（何の数か）。読点などで区切った最後のまとまり"""
    tail = re.split(r"[、，,（()）]", q.before)[-1].strip()
    return tail[-n:]


def pace(qs: list[Qty]) -> list[Computed]:
    """「約7カ月で100万口座増」→ 1カ月あたり"""
    out = []
    for q in qs:
        if not (q.unit in CHECK_COUNTERS or q.is_money) or q.value <= 0:
            continue
        m = None
        for m in PERIOD_RE.finditer(q.before):
            pass
        if not m or len(q.before) - m.end() > 6:
            continue
        months = float(m.group(2)) * MONTHS_PER[m.group(3)]
        if months <= 0 or months > 240:
            continue
        per = q.value / months
        approx = "約" if m.group(1) else ""
        period = f"{approx}{m.group(2)}{m.group(3)}"
        out.append(Computed(
            "pace",
            f"{period}で{q.text} → 1カ月あたり約{fmt_qty(per, q.unit)}（{q.text} ÷ {sig_round(months):g}カ月）",
            [per],
        ))
    return out


def annual(qs: list[Qty]) -> list[Computed]:
    """「1-10月累計67件」→ このペースなら年約80件"""
    out = []
    for q in qs:
        if not (q.unit in CHECK_COUNTERS or q.is_money):
            continue
        m = None
        for m in CUMULATIVE_RE.finditer(q.before):
            pass
        if not m or len(q.before) - m.end() > 8:
            continue
        last = int(m.group(2))
        if not 1 <= last < 12:
            continue
        year = q.value / last * 12
        out.append(Computed(
            "annual",
            f"1〜{last}月で{q.text} → このペースが続くと1年で約{fmt_qty(year, q.unit)}（{q.text} ÷ {last}カ月 × 12カ月。残りは{12 - last}カ月）",
            [year, float(12 - last)],
        ))
    return out


def changes(qs: list[Qty]) -> list[Computed]:
    """同じ文の「今の数」と「前年・前回の数」→ 差と増減率"""
    out = []
    for i, ref in enumerate(qs):
        if not PREV_RE.search(ref.before):
            continue
        label = PREV_RE.search(ref.before).group(1)
        cur = next((q for q in reversed(qs[:i]) if q.sentence == ref.sentence and _same_unit(q, ref) and q.value != ref.value
                    and not PREV_RE.search(q.before)), None)
        if not cur or ref.value == 0:
            continue
        diff = cur.value - ref.value
        pct = diff / ref.value * 100
        word = "増" if diff > 0 else "減"
        out.append(Computed(
            "change",
            f"{_ctx(cur) or '今回'}{cur.text}と{label}の{ref.text} → {fmt_qty(abs(diff), cur.unit)}{word}、{fmt_pct(pct)}"
            f"（({cur.text} − {ref.text}) ÷ {ref.text}）",
            [abs(diff), abs(pct)],
        ))
    return out


IMPLIED_RE = re.compile(r"^\s?[（(]?\s?(前年同月|前年同期|前年度|前年|前月|前期)比\s?[+＋]?(\d+(?:\.\d+)?)\s?[％%]\s?(増|減)")


def implied_previous(qs: list[Qty]) -> list[Computed]:
    """「20億7,700万円（前年同月比47.0％増）」→ 前年同月は約14億1,300万円（増減率から逆算）"""
    out = []
    for q in qs:
        m = IMPLIED_RE.match(q.after)
        if not m or not (q.is_money or q.unit in CHECK_COUNTERS) or q.value <= 0:
            continue
        rate = float(m.group(2)) / 100 * (1 if m.group(3) == "増" else -1)
        if rate <= -1:
            continue
        prev = q.value / (1 + rate)
        diff = q.value - prev
        out.append(Computed(
            "implied",
            f"{q.text}（{m.group(1)}比{m.group(2)}％{m.group(3)}）→ {m.group(1)}は約{fmt_qty(prev, q.unit)}、差は約{fmt_qty(abs(diff), q.unit)}"
            f"（{q.text} ÷ {1 + rate:g}。増減率からの逆算）",
            [prev, abs(diff)],
        ))
    return out


def shares(qs: list[Qty]) -> list[Computed]:
    """「総額 T … 最大・うち P」（同じ段落・同じ単位）→ 割合"""
    out = []
    for t in qs:
        if not TOTAL_LABEL_RE.search(t.before) or t.value <= 0:
            continue
        for p in qs:
            if p is t or p.para != t.para or not _same_unit(p, t) or not (0 < p.value < t.value):
                continue
            if not PART_RE.search(p.sentence) or p.sentence == t.sentence and not PART_RE.search(p.before):
                continue
            share = p.value / t.value * 100
            out.append(Computed(
                "share",
                f"{TOTAL_LABEL_RE.search(t.before).group(1)}{t.text}のうち{p.text}（{_ctx(p, 10) or '一部'}）→ {share:.1f}%（{p.text} ÷ {t.text}）",
                [share],
            ))
            break
    return out


def progress(qs: list[Qty]) -> list[Computed]:
    """「上限・目標 G … 累計・取得済み D」（同じ単位・同じ段落）→ 進み具合と残り"""
    out = []
    for g in qs:
        if not GOAL_RE.search(g.before[-12:]) or g.value <= 0:
            continue
        for d in qs:
            if d is g or not _same_unit(d, g) or d.para != g.para or not (0 < d.value < g.value):
                continue
            if not DONE_RE.search(d.before[-14:]):
                continue
            pct = d.value / g.value * 100
            out.append(Computed(
                "progress",
                f"{GOAL_RE.search(g.before[-12:]).group(1)}{g.text}に対して{DONE_RE.search(d.before[-14:]).group(1)}{d.text} → {pct:.1f}%、残り{fmt_qty(g.value - d.value, g.unit)}"
                f"（{d.text} ÷ {g.text}）",
                [pct, g.value - d.value],
            ))
            break
    return out


def per_unit(qs: list[Qty]) -> list[Computed]:
    """同じ文（または隣の文）の「総額 M（お金）」と「総数 C（株・口座など）」→ 1つあたり"""
    out = []
    for m_ in qs:
        if not m_.is_money or not re.search(r"総額|金額", m_.before):
            continue
        for c in qs:
            if c.unit not in CHECK_COUNTERS or c.para != m_.para or abs(c.start - m_.start) > 120 or c.value <= 0:
                continue
            if not re.search(r"総数|数", c.before[-10:]):
                continue
            per = m_.value / c.value
            out.append(Computed(
                "per_unit",
                f"{m_.text}で{c.text} → 1{c.unit}あたり約{fmt_qty(per, m_.unit)}（{m_.text} ÷ {c.text}）",
                [per],
            ))
            break
    return out


def yen(usd_amounts: list[tuple[float, str]], fx: dict | None, monthly: set[float] | None = None) -> list[Computed]:
    """ドルの金額 → 円の目安（為替のレート・日付つき）。monthly にある金額（個人向けの月額）は税込み（10%）の目安も出す"""
    if not fx or not isinstance(fx.get("rate"), (int, float)) or not 50 < fx["rate"] < 400:
        return []
    rate, date = float(fx["rate"]), str(fx.get("date") or "")
    src = str(fx.get("source") or "")
    out, seen = [], set()
    for amount, label in usd_amounts:
        if amount <= 0 or amount in seen:
            continue
        seen.add(amount)
        jpy = amount * rate
        line = f"{label or fmt_qty(amount, 'USD')}：{fmt_qty(amount, 'USD')} × {rate:.2f}円 = 約{fmt_qty(jpy, 'JPY')}（税別の目安）"
        vals = [jpy]
        if monthly and amount in monthly:
            line += f"、消費税10%込みで約{fmt_qty(jpy * 1.1, 'JPY')}"
            vals.append(jpy * 1.1)
        out.append(Computed("yen", f"{line}　※1ドル={rate:.2f}円（{date}、{src}）で計算", vals))
        if len(out) >= 6:
            break
    return out


LADDER_REF_RE = re.compile(r"([A-Za-z][A-Za-z0-9 ]*?|[^\s、,（(。.$\d]{2,12}?)\s?の\s?(\d+(?:\.\d+)?)\s?倍")


def ladder(rows: list[dict]) -> list[Computed]:
    """料金の段（[{plan, usd_month, limits}]）で「Xの N 倍」とあるプラン → 1倍分あたりの値段。基準のプランと同じかを比べる"""
    by_name = {}
    for r in rows:
        if isinstance(r.get("usd_month"), (int, float)) and r["usd_month"] > 0:
            by_name[r["plan"]] = r
    out, same = [], []
    for r in rows:
        price = r.get("usd_month")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        for limit in r.get("limits") or []:
            m = LADDER_REF_RE.search(nfkc(limit))
            if not m:
                continue
            ref_name, times = m.group(1).strip(), float(m.group(2))
            base = by_name.get(ref_name) or next((b for n, b in by_name.items() if ref_name and (ref_name in n or n in ref_name)), None)
            if not base or base is r or times <= 0:
                continue
            per = price / times
            base_price = base["usd_month"]
            if abs(per - base_price) < 0.01 * base_price:
                verdict = f"{base['plan']}（{fmt_qty(base_price, 'USD')}/月）と同じ。使える量あたりの値段は変わらない"
                same.append((r["plan"], base["plan"], per))
            elif per < base_price:
                verdict = f"{base['plan']}（{fmt_qty(base_price, 'USD')}/月）より{(1 - per / base_price) * 100:.0f}%安い"
            else:
                verdict = f"{base['plan']}（{fmt_qty(base_price, 'USD')}/月）より{(per / base_price - 1) * 100:.0f}%高い"
            out.append(Computed(
                "ladder",
                f"{r['plan']}：{fmt_qty(price, 'USD')}/月で{base['plan']}の{times:g}倍の使用量 → {base['plan']} 1倍分あたり{fmt_qty(per, 'USD')}"
                f"（{fmt_qty(price, 'USD')} ÷ {times:g}）。{verdict}",
                [per, times],
            ))
            break
    bases = {b for _, b, _ in same}
    if len(same) >= 2 and len(bases) == 1:
        base = bases.pop()
        names = "・".join([base] + [p for p, _, _ in same])
        out.insert(0, Computed(
            "ladder",
            f"{names}は、{base} 1倍分の使用量あたりの値段がどれも{fmt_qty(same[0][2], 'USD')}で同じ（料金 ÷ 使用量の倍率）。"
            "上のプランは「量あたりの割引」ではなく、同じ単価で量を増やすもの",
            [same[0][2]],
        ))
    return out


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[\d\s,.%、。「」『』（）()・\-—:：]", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def past(current: list[Qty], past_rows: list[dict], now: datetime | None = None) -> list[Computed]:
    """過去記事（[{slug, title, at, text}]）の同じ種類の数字と、今回の数字を比べる（件・口座・人などの数だけ）。
    何の数かの言葉（直前の言葉）が似ているものだけ"""
    out, used = [], set()
    for q in current:
        if q.unit not in CHECK_COUNTERS or not q.has_mult and q.value < 1000:
            continue
        label = _bigrams(_ctx(q, 12))
        for row in past_rows:
            for p in extract(row.get("text") or ""):
                if p.unit != q.unit or p.value == q.value or p.value <= 0:
                    continue
                if len(label & _bigrams(_ctx(p, 12))) < 1 or (row["slug"], p.value) in used:
                    continue
                used.add((row["slug"], p.value))
                diff = q.value - p.value
                line = (f"サイソクの過去記事（{row['at']:%Y-%m-%d}「{row['title'][:30]}」）の{p.text} → 今回{q.text}："
                        f"{fmt_qty(abs(diff), q.unit)}{'増' if diff > 0 else '減'}、{fmt_pct(diff / p.value * 100)}")
                vals = [abs(diff), abs(diff / p.value * 100)]
                if now and isinstance(row.get("at"), datetime):
                    months = (now - row["at"]).days / 30.44
                    if months >= 0.5:
                        line += f"。1カ月あたり約{fmt_qty(abs(diff) / months, q.unit)}（{fmt_qty(abs(diff), q.unit)} ÷ {months:.1f}カ月。記事の日付どうしの目安）"
                        vals.append(abs(diff) / months)
                out.append(Computed("past", line, vals))
                break
            if len(out) >= 2:
                return out
    return out


RELATIVE_DATE_RE = re.compile(r"(来月|今月|翌月|先月)\s?(\d{1,2})日|(来年|今年|翌年|昨年)\s?(\d{1,2})月(?:\s?(\d{1,2})日)?|(来年度|今年度)")


ABS_DATE_RE = re.compile(r"(?:(令和|平成)\s?(\d{1,2}|元)年\s?|(20\d\d)年\s?)?(\d{1,2})月\s?(\d{1,2})日")


def _shift_month(y: int, mo: int, shift: int) -> tuple[int, int]:
    mo += shift
    return y + (mo - 1) // 12, (mo - 1) % 12 + 1


def dated_mentions(text: str, published: datetime | None) -> list[tuple["date", int, int]]:
    """文章の中の「日付」を実際の日付にする（2026-11-03）。[(日付, 始まり, 終わり)]（位置は nfkc した文章の中）。
    「令和8年11月9日」「2026年11月9日」「11月9日」（年が無ければ元記事の日付の年。半年以上前になるなら翌年）と、
    「来月1日」「来年4月1日」などの言い方（relative_dates と同じ。元記事の日付から数える。今日から数えない）"""
    from datetime import date  # noqa: PLC0415

    if not isinstance(published, datetime):
        return []
    base = published.astimezone(JST_TZ).date()
    t = nfkc(text)
    out = []
    for m in ABS_DATE_RE.finditer(t):
        month, day = int(m.group(4)), int(m.group(5))
        if m.group(1):
            n = 1 if m.group(2) == "元" else int(m.group(2))
            year = (2018 if m.group(1) == "令和" else 1988) + n
        elif m.group(3):
            year = int(m.group(3))
        else:
            year = base.year
        try:
            when = date(year, month, day)
        except ValueError:
            continue
        if not (m.group(1) or m.group(3)) and (base - when).days > 183:
            when = date(year + 1, month, day)
        out.append((when, m.start(), m.end()))
    for m in RELATIVE_DATE_RE.finditer(t):
        try:
            if m.group(1):
                y, mo = _shift_month(base.year, base.month, {"来月": 1, "翌月": 1, "今月": 0, "先月": -1}[m.group(1)])
                out.append((date(y, mo, int(m.group(2))), m.start(), m.end()))
            elif m.group(3) and m.group(5):
                y = base.year + {"来年": 1, "翌年": 1, "今年": 0, "昨年": -1}[m.group(3)]
                out.append((date(y, int(m.group(4)), int(m.group(5))), m.start(), m.end()))
        except ValueError:
            continue
    return sorted(out, key=lambda x: x[1])


def relative_dates(text: str, published: datetime | None) -> list[Computed]:
    """「来月1日」「来年4月」などを、元記事の日付から実際の日付にする（LLM が今日の日付で読み違えないように）"""
    if not isinstance(published, datetime):
        return []
    base = published.astimezone(JST_TZ)
    out, seen = [], set()
    for m in RELATIVE_DATE_RE.finditer(nfkc(text)):
        word = m.group(0)
        if word in seen:
            continue
        seen.add(word)
        y, mo = base.year, base.month
        if m.group(1):
            shift = {"来月": 1, "翌月": 1, "今月": 0, "先月": -1}[m.group(1)]
            mo += shift
            y += (mo - 1) // 12
            mo = (mo - 1) % 12 + 1
            day = int(m.group(2))
            if not 1 <= day <= 31:
                continue
            when = f"{y}年{mo}月{day}日"
        elif m.group(3):
            y += {"来年": 1, "翌年": 1, "今年": 0, "昨年": -1}[m.group(3)]
            month = int(m.group(4))
            if not 1 <= month <= 12:
                continue
            when = f"{y}年{month}月" + (f"{int(m.group(5))}日" if m.group(5) else "")
        else:
            fy = base.year if base.month >= 4 else base.year - 1
            fy += 1 if m.group(6) == "来年度" else 0
            when = f"{fy}年度（{fy}年4月〜{fy + 1}年3月）"
        out.append(Computed("date", f"元記事の「{word}」＝{when}（元記事の日付 {base:%Y年%m月%d日} から数えた日付。今日の日付から数えない）", []))
        if len(out) >= 4:
            break
    return out


TAIL_RE = re.compile(r"\n\s*(?:あわせて読みたい|関連記事|関連ニュース|人気記事|この記事に関する|おすすめ記事|Related (?:articles|posts|stories)|More from)")


def compute(text: str, *, fx: dict | None = None, ladder_rows: list[dict] | None = None, past_rows: list[dict] | None = None,
            now: datetime | None = None, published: datetime | None = None, limit: int = 10) -> list[Computed]:
    """元記事の文章（と料金の段・過去記事・為替）から、計算済みの数字の一覧を作る（同じ結果は1つにまとめる）"""
    # ページの後ろの「あわせて読みたい」「人気記事」などの囲み（別の記事の数字）は計算に使わない
    text = TAIL_RE.split(text or "", maxsplit=1)[0]
    qs = extract(text)
    found: list[Computed] = relative_dates(text, published)
    found += ladder(ladder_rows or [])
    found += pace(qs) + annual(qs) + changes(qs) + implied_previous(qs) + shares(qs) + progress(qs) + per_unit(qs)
    found += past(qs[:40], past_rows or [], now)
    usd = [(q.value, "") for q in qs if q.unit == "USD" and q.value >= 1]
    monthly = {q.value for q in qs if q.unit == "USD" and re.search(r"月|month|/mo", q.sentence, re.I)}
    for r in ladder_rows or []:
        if isinstance(r.get("usd_month"), (int, float)) and r["usd_month"] > 0:
            usd.append((float(r["usd_month"]), f"{r['plan']}の月額"))
            monthly.add(float(r["usd_month"]))
    # 料金の段にある金額は段の名前つきで先に出す
    usd.sort(key=lambda x: x[1] == "")
    found += yen(usd, fx, monthly)
    out, seen = [], set()
    for c in found:
        key = c.text
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out[:limit]


def block(computed: list[Computed]) -> str:
    """プロンプト・事実確認に渡す「計算済みの数字」の行"""
    return "\n".join(f"- [C{i}] {c.text}" for i, c in enumerate(computed, 1))


def main() -> int:
    text = " ".join(sys.argv[1:]) or sys.stdin.read()
    for c in compute(text):
        print(f"[{c.kind}] {c.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
