#!/usr/bin/env python3
"""製品の記事に「同じクラスの製品と比べると？」の比較表を足す（2026-11-03 オーナー依頼）。

ガジェット・乗り物・自転車・スマホ・PC・カメラ・ゲーム機・AIデバイスなど「製品」の記事で、
同じ分類・同じクラス（価格帯）のライバル製品2〜3つと、主な仕様と公式価格を並べる。
きっかけ: Honda RN0（e-MTB）の記事に、Threads の読者（MTB に詳しい人）から「重さ・モーター・価格をライバルと比べたい」。

事実だけを書く約束
  - ライバルの数字は、各メーカーの公式の製品ページ（できれば日本の公式ページ）から、その場で読み取ったものだけ。
    覚えている知識・販売店・比較サイトの数字は使わない。公式ページに無い項目は「公式に記載なし」。
  - 読み取りは無料の LLM（llm.py の bulk）に任せるが、プログラムが1つずつ確かめる:
      値の根拠の文（quote）が取ったページの文章にそのまま有るか / 値の数字が全部その文に有るか /
      単位が項目に合うか（重さは kg、トルクは Nm…）/ 別物の数字でないか（最大出力に「定格」を入れない）
    合わない値は捨てる（「公式に記載なし」になる）。
  - 公式ページが取れない（404・ボット対策の確認画面・robots.txt で禁止）ライバルは飛ばす。確認画面を突破しようとしない。
  - 比べてわかること（1〜2文）は表の数字だけからプログラムが作る。差は calc_facts.spec_difference で計算し、式を残す。
  - ドルの価格は為替（content/pricing/_fx.json。pricing.py）で円の目安を添える（「目安」と書く）。
  - 書き込む前に品質ゲート（quality.rival_problems）で、表の数字が全部「公式ページの根拠の文」か「計算の式」にあるか確かめる。

ライバルの選び方
  - scripts/rivals_catalog.json の分類ごとの候補（人が確かめた公式ページ）を先に使う
  - 無料の LLM にも、同じ分類・同じクラスで今売っている製品と公式ページを挙げさせる（公式のドメインでなければ使わない）
  - 取れた順に最大3つ。2つに満たなければ表は作らない（1つと比べても「クラスの中で」はわからない）

記事への出し方
  - 本文に「## 同じクラスの製品と比べると？」の章（表・比べてわかること・出典）を差し込む
  - 記事 JSON の rivals 欄に、値・根拠の文・出典 URL・確認日を残す（品質ゲートとサイトの図が使う）
  - charts 欄に kind "rivals" の図（重さ・トルクなど1〜2項目の棒。lib/charts.ts）

  python3 scripts/rivals.py --slug honda-rn0-emtb-eicma-2026            # 試し（書き込まない。前と後を表示）
  python3 scripts/rivals.py --slug honda-rn0-emtb-eicma-2026 --apply    # 記事に書き込む
  python3 scripts/rivals.py --recent-hours 36 --max 3 --apply           # 新しい製品の記事（自動実行・auto_run.yml）
  python3 scripts/rivals.py --days 7 --apply                            # 直近7日の製品の記事
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calc_facts  # noqa: E402
import feeds  # noqa: E402
import facts_bundle  # noqa: E402
import pricing  # noqa: E402
import source_links  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
CATALOG_PATH = Path(__file__).resolve().parent / "rivals_catalog.json"
STATE_PATH = ROOT / "state" / "rivals_checked.json"
JST = timezone(timedelta(hours=9))

HEADING = "同じクラスの製品と比べると？"
MISSING = "公式に記載なし"
MIN_RIVALS = 2
MAX_RIVALS = 3
MAX_CANDIDATES = 6
MIN_FIELDS = 2  # ライバルの公式ページから読み取れた項目（価格を含む）がこれ未満なら使わない
FETCH_TIMEOUT = 12
ARTICLE_BUDGET = 240  # 秒。1本の記事にかける上限
PAGE_CAP = 14000  # LLM に渡すページの文章の上限
QUOTE_CAP = 220

# ボット対策の確認画面・アクセス拒否（突破しない。飛ばす）
BLOCK_RE = re.compile(
    r"cf-chl|challenge-platform|Just a moment\.\.\.|Attention Required|captcha|Access Denied|Pardon Our Interruption|"
    r"Request unsuccessful|verify you are (?:a )?human|bot detection|アクセスが拒否|ロボットではありません",
    re.I,
)
DISCONTINUED_RE = re.compile(r"生産終了|販売終了|販売を終了(?:しました|いたしました)|discontinued|no longer available", re.I)
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
RANGE_SEP_RE = re.compile(r"\s*(?:〜|～|~|-|–|—|から)\s*")
UNIT_NORM = {"kg": ("kg", 1), "g": ("kg", 0.001), "kw": ("W", 1000), "w": ("W", 1), "kwh": ("Wh", 1000), "wh": ("Wh", 1),
             "mah": ("mAh", 1), "ah": ("Ah", 1), "nm": ("Nm", 1), "mm": ("mm", 1), "時間": ("時間", 1), "hours": ("時間", 1),
             "hrs": ("時間", 1), "km": ("km", 1), "インチ": ("インチ", 1), "inch": ("インチ", 1), "型": ("インチ", 1)}


def log(message: str) -> None:
    print(message, flush=True)


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def squash(text: str) -> str:
    """照合用: 全角半角・大文字小文字・空白・区切りの記号の違いをなくす"""
    return re.sub(r"[\s:：、,，。・/|()（）\[\]「」『』*＊\"'“”]", "", nfkc(text).lower())


def numbers(text: str) -> list[float]:
    out = []
    for m in NUM_RE.finditer(nfkc(text)):
        raw = m.group(0)
        # 「1,045,000」は千の区切り。「25,70」のような小数のカンマは使わない
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
            raw = raw.replace(",", "")
        elif "," in raw:
            out += [float(p) for p in raw.split(",") if p and re.fullmatch(r"\d+(?:\.\d+)?", p)]
            continue
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


def today(now: datetime | None = None) -> str:
    return (now or datetime.now(JST)).astimezone(JST).strftime("%Y-%m-%d")


def md(date: str) -> str:
    m = re.match(r"\d{4}-(\d{2})-(\d{2})", date or "")
    return f"{int(m.group(1))}/{int(m.group(2))}" if m else date


# --------------------------------------------------------------------------- #
# 分類・項目・候補（scripts/rivals_catalog.json）
# --------------------------------------------------------------------------- #


def load_catalog(path: Path | None = None) -> dict:
    return json.loads((path or CATALOG_PATH).read_text("utf-8"))


def field_defs(catalog: dict, category: str) -> list[tuple[str, dict]]:
    cat = catalog["categories"][category]
    return [(key, catalog["fields"][key]) for key in cat["fields"] if key in catalog["fields"]]


def guess_category(article: dict, catalog: dict) -> str | None:
    """題名・タグ・キーワードから分類の見当を付ける（LLM の答えが無いときの予備。最初に当たったもの）"""
    text = " ".join([article.get("title") or "", " ".join(article.get("tags") or []), " ".join(article.get("keywords") or [])])
    for key, cat in catalog["categories"].items():
        if cat.get("match") and re.search(cat["match"], text, re.I):
            return key
    return None


def maker_domains(catalog: dict, maker: str) -> set[str]:
    for name, domains in (catalog.get("makers") or {}).items():
        if squash(name) == squash(maker):
            return set(domains)
    return set()


def official_url_ok(url: str, maker: str, catalog: dict) -> bool:
    """LLM が挙げた URL が、そのメーカーの公式のドメインか（販売店・比較サイト・ニュースサイトは不可）"""
    if not re.match(r"https?://", url or ""):
        return False
    host = source_links.host(url)
    base = source_links.base_domain(host)
    known = maker_domains(catalog, maker)
    if known:
        return any(host == d or host.endswith("." + d) or base == d for d in known)
    token = re.sub(r"[^a-z0-9]", "", nfkc(maker).lower())
    return len(token) >= 3 and token in re.sub(r"[^a-z0-9]", "", base)


def region_of(url: str) -> str:
    u = (url or "").lower()
    host = source_links.host(u)
    if re.search(r"/jp(?:/|$)|ja[_-]jp|/ja/|/jp-ja/", u) or host.endswith(".jp"):
        return "日本"
    if re.search(r"/us(?:/|$)|en[_-]us", u):
        return "米国"
    if re.search(r"/uk(?:/|$)|/gb(?:/|$)|en[_-]gb", u):
        return "英国"
    if re.search(r"/de(?:/|$)|de[_-]de", u):
        return "ドイツ"
    return ""


def same_product(a: str, b: str) -> bool:
    return squash(a) == squash(b) or (len(squash(a)) > 4 and (squash(a) in squash(b) or squash(b) in squash(a)))


def name_on_page(name: str, maker: str, text: str) -> bool:
    """ページに製品の名前が出てくるか（メーカー名を除いた言葉の半分以上）"""
    flat = squash(text)
    tokens = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+\-]*|[ァ-ヶー]{2,}|[一-龠々]{2,}", nfkc(name))
              if squash(t) not in (squash(maker), "") and len(t) >= 2]
    if not tokens:
        return squash(name) in flat
    hit = sum(1 for t in tokens if squash(t) in flat)
    return hit >= max(1, (len(tokens) + 1) // 2)


# --------------------------------------------------------------------------- #
# 公式ページを取る（直接だけ。読み取りサービス・確認画面の突破はしない）
# --------------------------------------------------------------------------- #


class Skip(Exception):
    """このライバル（ページ）は使わない"""


_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_allowed(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            data, _ = feeds.fetch(origin + "/robots.txt", timeout=6, accept="text/plain")
            rp.parse(feeds._decode(data).splitlines())
            _robots[origin] = rp
        except Exception:  # noqa: BLE001 - robots.txt が無い・取れないときは止めない
            _robots[origin] = None
    rp = _robots[origin]
    return True if rp is None else rp.can_fetch(feeds.UA, url)


def fetch_official(url: str, timeout: int = FETCH_TIMEOUT) -> dict:
    """公式ページの文章 {text, title, final_url}。取れない・確認画面・robots.txt で禁止なら Skip"""
    if not robots_allowed(url):
        raise Skip("robots.txt で禁止")
    try:
        data, final_url = feeds.fetch(url, timeout=timeout, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")
    except Exception as error:  # noqa: BLE001
        raise Skip(f"取得できない（{str(error)[:60]}）") from error
    html = feeds._decode(data)
    if BLOCK_RE.search(html[:30000]) and len(re.sub(r"<[^>]+>", "", html)) < 20000:
        raise Skip("ボット対策の確認画面（突破しない）")
    parser = feeds._Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        pass
    texts = [feeds._collapse(parser.buffers[k]) for k in ("body", "main", "article")]
    text = max(texts, key=len)
    if len(text) < 200:
        raise Skip("中身がほとんど無い（JavaScript で表示するページ）")
    title = parser.meta.get("og:title", "")
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if not title and m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
    return {"text": text[:200000], "title": title, "final_url": final_url or url}


def page_excerpt(text: str, fields: list[tuple[str, dict]], cap: int = PAGE_CAP) -> str:
    """LLM に渡す部分。長いページは、項目の言葉・単位・価格の行と、その前後の行だけ"""
    if len(text) <= cap:
        return text
    lines = text.split("\n")
    pats = [d.get("labels") or "" for _, d in fields] + [r"価格|円|¥|\$|€|price"]
    units = [re.escape(u) for _, d in fields for u in d.get("units") or [] if len(u) > 1]
    rx = re.compile("|".join([p for p in pats if p] + units), re.I)
    keep: set[int] = set()
    for i, ln in enumerate(lines):
        if rx.search(ln):
            keep |= {i - 1, i, i + 1}
    out, total = [], 0
    for i in sorted(k for k in keep if 0 <= k < len(lines)):
        if total + len(lines[i]) > cap:
            break
        out.append(lines[i])
        total += len(lines[i]) + 1
    return "\n".join(out) or text[:cap]


# --------------------------------------------------------------------------- #
# LLM（無料モデルだけ: llm.py の bulk。Claude は使わない）
# --------------------------------------------------------------------------- #


def default_ask(system: str, prompt: str, schema: dict) -> dict:
    import llm  # noqa: PLC0415 - テストでは読み込まない

    data, model = llm.generate_json(system, prompt, schema, temperature=0.1, max_output_tokens=4096, tier="bulk")
    log(f"   [rivals] LLM: {model}")
    return data or {}


CLASSIFY_SYSTEM = """あなたはニュースサイトの製品担当の編集者です。記事が「1つの製品（形のある商品）」の記事かを判定し、
同じ分類・同じクラス（価格帯）で今売られているライバル製品を挙げます。出力は指定の JSON だけ。"""

CLASSIFY_PROMPT = """# 記事
題名: {title}
タグ: {tags}
リード: {lede}
本文の冒頭:
{body}

# 判定
- is_product: この記事の主役が、仕様（重さ・電池・画面・出力など）を持つ1つの製品（ガジェット・乗り物・自転車・スマホ・PC・カメラ・ゲーム機・AIデバイスなど）の発表・発売・改良・レビューなら true。
  ソフト・アプリ・AIモデル・サービス・料金プラン・ゲームソフト・決算・提携・イベント・まとめ記事・リコールは false。
- category: 次のどれか（合うものが無ければ "other"）: {categories}
- product_name: 主役の製品の正式な名前（記事に出てくる書き方。例「Honda RN0」）
- maker_en: 主役のメーカー名（英語。例 "Honda"）
- rivals: 同じ分類・同じクラス（同じくらいの価格帯・性能帯）で、今メーカーが売っている有名な製品を最大4つ。主役と同じメーカーの製品は入れない。
  name はモデル名まで（例 "Specialized Turbo Levo 4"）。official_url はメーカー公式の製品ページ（日本の公式ページがあればそれ）。
  販売店・比較サイト・ニュースの URL は入れない。確かでない URL は空にする。もう売っていない製品・発表前の製品は入れない。"""

CLASSIFY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_product": {"type": "BOOLEAN"},
        "category": {"type": "STRING"},
        "product_name": {"type": "STRING"},
        "maker_en": {"type": "STRING"},
        "rivals": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"name": {"type": "STRING"}, "maker_en": {"type": "STRING"}, "official_url": {"type": "STRING"}},
                "required": ["name", "maker_en", "official_url"],
            },
        },
    },
    "required": ["is_product", "category", "product_name", "maker_en", "rivals"],
}

EXTRACT_SYSTEM = """あなたは製品の仕様を公式ページから書き写す担当です。ページに書いてあることだけを使い、覚えている知識で補いません。
出力は指定の JSON だけ。"""

EXTRACT_PROMPT = """次の公式ページの文章から、製品「{name}」の仕様を読み取ってください。

# 読み取る項目
{fields}
- price: メーカー希望小売価格・公式の販売価格（ページにあるときだけ）。amount は数字だけ、quote はその価格が書かれた部分。
  「〜から」「本体価格（税抜）」と「税込」の両方があれば税込を選ぶ。

# 決まり
- value は短い日本語で（例「25.70kg」「105Nm」「840Wh」「前29インチ・後27.5インチ」「SHIMANO EP801」）。
- quote は、その値の根拠になるページの文章を、そのまま（変えずに・訳さずに）40〜120字ほど抜き出す。value の数字は全部 quote に入れる。
- ページに無い項目は fields に入れない。推測・換算・計算はしない（Ah を Wh に直さない、など）。
- ページに複数のモデルが並ぶときは「{name}」の値だけ。サイズ違いで重さが違うときは範囲で書いてよい。

# ページ（{url}）
{text}"""

EXTRACT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "product_name_on_page": {"type": "STRING"},
        "fields": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"key": {"type": "STRING"}, "value": {"type": "STRING"}, "quote": {"type": "STRING"}},
                "required": ["key", "value", "quote"],
            },
        },
        "price": {
            "type": "OBJECT",
            "properties": {"amount": {"type": "NUMBER"}, "quote": {"type": "STRING"}},
        },
    },
    "required": ["product_name_on_page", "fields"],
}


def classify(article: dict, catalog: dict, ask=default_ask) -> dict:
    body = re.sub(r"\n{2,}", "\n", article.get("body") or "")[:1800]
    cats = ", ".join(f"{k}（{v['label']}）" for k, v in catalog["categories"].items())
    prompt = CLASSIFY_PROMPT.format(title=article.get("title", ""), tags="、".join(article.get("tags") or []),
                                    lede=article.get("lede", ""), body=body, categories=cats)
    data = ask(CLASSIFY_SYSTEM, prompt, CLASSIFY_SCHEMA) or {}
    if data.get("category") not in catalog["categories"]:
        data["category"] = guess_category(article, catalog) if data.get("is_product") else None
    return data


def fields_prompt(fields: list[tuple[str, dict]]) -> str:
    return "\n".join(f"- {k}（{d['label']}）: {d.get('hint', '')}" for k, d in fields)


# --------------------------------------------------------------------------- #
# 読み取った値を確かめる（LLM の答えをそのまま信じない）
# --------------------------------------------------------------------------- #


def _has_unit(value: str, units: list[str]) -> bool:
    v = nfkc(value).lower()
    for u in units:
        u2 = nfkc(u).lower()
        if u2 in ('"', "″", "×"):
            if u2 in v:
                return True
            continue
        # 「W」が「Wh」の一部として当たらないように、英字の単位は後ろに英字が続かないこと
        if re.search(rf"\d\s*{re.escape(u2)}(?![a-z])", v) if re.match(r"[a-z]", u2) else u2 in v:
            return True
    return False


QTY_ONLY_RE = re.compile(r"^\s*約?\s*[\d.,]+\s*[A-Za-z%]{1,4}\s*$")


def find_quote(value: str, d: dict, page_text: str) -> str | None:
    """LLM の根拠の文がページと1字違うとき: ページの中で、項目の言葉（重量・トルク…）のすぐ後ろ（120字以内）に
    値の数字が全部そろう場所を探し、そのページの文を根拠の文にする（見つからなければ None）"""
    nums = numbers(value)
    if not nums or not d.get("labels"):
        return None
    flat = re.sub(r"\s+", " ", nfkc(page_text))
    for m in re.finditer(d["labels"], flat, re.I):
        start = m.start()
        while start > 0 and m.start() - start < 10 and flat[start - 1] != " ":
            start -= 1  # 「車両重量」の「車両」のような、言葉の頭から
        window = flat[start: m.start() + 120]
        got = numbers(window)
        if all(any(abs(n - g) < 1e-9 for g in got) for n in nums):
            # 値の最後の数字（と、そのすぐ後ろの単位）までで切る
            end = 0
            for t in re.finditer(r"\d+(?:[.,]\d+)*", window):
                if any(abs(n - v) < 1e-9 for v in numbers(t.group(0)) for n in nums):
                    end = t.end()
            tail = re.match(r"[^\s\d]{0,4}", window[end:])
            return window[: end + (tail.end() if tail else 0)].strip()
    return None


def check_value(key: str, d: dict, value: str, quote: str, page_text: str) -> str | None:
    """問題があれば理由、無ければ None"""
    value, quote = (value or "").strip(), (quote or "").strip()
    if not value or not quote:
        return "値か根拠の文が空"
    if len(value) > 80:
        return "値が長すぎる"
    if not d.get("units") and QTY_ONLY_RE.match(nfkc(value)):
        return "名前の項目に数字だけの値"
    if squash(quote) not in squash(page_text):
        return "根拠の文がページに無い"
    qnums = numbers(quote)
    for n in numbers(value):
        if not any(abs(n - q) < 1e-9 for q in qnums):
            return f"値の数字（{n:g}）が根拠の文に無い"
    units = d.get("units") or []
    if units and not _has_unit(value, units):
        return "単位が項目に合わない"
    if units and not numbers(value):
        return "数字が無い"
    if d.get("exclude") and re.search(d["exclude"], quote, re.I):
        return "別の数字（定格など）"
    if d.get("deny") and re.search(d["deny"], quote, re.I) and not re.search(d.get("allow") or r"$^", quote, re.I):
        return "別の部品の数字"
    return None


def verify(raw: dict, page_text: str, fields: list[tuple[str, dict]], url: str, checked: str, log_fn=log) -> dict:
    """LLM の答えのうち、確かめられた値だけ {key: spec}"""
    defs = dict(fields)
    out: dict[str, dict] = {}
    for row in raw.get("fields") or []:
        key = str(row.get("key") or "").strip()
        if key not in defs or key in out:
            continue
        value = nfkc(str(row.get("value") or "")).strip()
        quote = re.sub(r"\s+", " ", nfkc(str(row.get("quote") or ""))).strip()[:QUOTE_CAP]
        why = check_value(key, defs[key], value, quote, page_text)
        if why == "根拠の文がページに無い" and (found := find_quote(value, defs[key], page_text)):
            quote = found[:QUOTE_CAP]
            why = check_value(key, defs[key], value, quote, page_text)
        if why and "根拠の文に無い" in why:
            core = re.sub(r"\s*[（(][^）)]*[）)]", "", value).strip()  # 添え書き（サイズなど）を外せば合うなら、外した値を使う
            if core and core != value and not check_value(key, defs[key], core, quote, page_text):
                value, why = core, None
        if why:
            log_fn(f"   [rivals]   {key}: 使わない（{why}）「{value[:30]}」")
            continue
        out[key] = {"display": value.replace("(", "（").replace(")", "）"), "quote": quote, "url": url, "checkedAt": checked}
    price = raw.get("price") or {}
    if isinstance(price.get("amount"), (int, float)) and price["amount"] > 0 and price.get("quote"):
        if spec := check_price(float(price["amount"]), str(price["quote"]), page_text, url, checked):
            out["price"] = spec
        else:
            log_fn(f"   [rivals]   price: 使わない（根拠の文と合わない）「{price.get('amount')}」")
    return out


def check_price(amount: float, quote: str, page_text: str, url: str, checked: str) -> dict | None:
    quote = re.sub(r"\s+", " ", nfkc(quote)).strip()[:QUOTE_CAP]
    if not quote or squash(quote) not in squash(page_text):
        return None
    if not any(abs(amount - q) < 1e-6 for q in numbers(quote)):
        return None
    region = region_of(url)
    if re.search(r"円|¥|￥|JPY", quote):
        currency = "JPY"
    elif re.search(r"€|EUR", quote):
        currency = "EUR"
    elif re.search(r"£|GBP", quote):
        currency = "GBP"
    elif re.search(r"US\$|USD", quote) or ("$" in quote and region == "米国"):
        currency = "USD"
    else:
        return None  # 通貨が確かでない（カナダドル・豪ドルなど）ものは使わない
    if re.search(r"税込|消費税[^。]{0,8}含|込み|incl", quote, re.I):
        tax = "税込"
    elif re.search(r"税抜|税別|本体価格|excl", quote, re.I):
        tax = "税抜"
    else:
        tax = ""
    amt_text = re.escape(f"{amount:,.0f}") if amount >= 1000 else re.escape(f"{amount:g}")
    from_price = bool(re.search(rf"{amt_text}\s*(?:円|ドル)?\s*(?:〜|～|~|から)|from\s*[$€£]?\s*{amt_text}", quote, re.I))
    return {"amount": amount, "currency": currency, "tax": tax, "region": region, "from": from_price,
            "quote": quote, "url": url, "checkedAt": checked, "display": price_display(amount, currency, tax, region, from_price)}


def price_display(amount: float, currency: str, tax: str, region: str, from_price: bool = False) -> str:
    head = pricing.money(amount, currency) if currency in ("JPY", "USD") else (
        f"€{amount:,.0f}" if currency == "EUR" else f"£{amount:,.0f}")
    note = "・".join(x for x in [region, tax] if x)
    return f"{head}{'〜' if from_price else ''}" + (f"（{note}）" if note else "")


# --------------------------------------------------------------------------- #
# 主役の製品（記事の元の発表 → 足りなければ記事の数字の表）
# --------------------------------------------------------------------------- #

EMPTY_CELL_RE = re.compile(r"言及なし|記載なし|不明|未発表|非公表|未定|—|－|^-$|^$")


def table_specs(article: dict, fields: list[tuple[str, dict]]) -> dict:
    """記事本文の数字の表（| 項目 | 数値 | 出典 |）から、項目に合う行を拾う（品質ゲートを通った記事の表。出典は元の発表）"""
    out: dict[str, dict] = {}
    body = article.get("body") or ""
    checked = (article.get("generatedAt") or article.get("publishedAt") or "")[:10]
    for line in body.split("\n"):
        if not line.startswith("|") or re.match(r"^\|\s*-", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("項目", "比べること"):
            continue
        label, value = cells[0], cells[1]
        if EMPTY_CELL_RE.search(value):
            continue
        for key, d in fields:
            if key in out or not re.search(d.get("labels") or r"$^", label, re.I):
                continue
            if d.get("deny") and re.search(d["deny"], label, re.I) and not re.search(d.get("allow") or r"$^", label, re.I):
                continue
            if d.get("exclude") and re.search(d["exclude"], label, re.I):
                continue
            if d.get("units") and not _has_unit(value, d["units"]):
                continue
            if not d.get("units") and QTY_ONLY_RE.match(nfkc(value)):
                continue
            out[key] = {"display": value, "quote": f"{label}：{value}", "url": article.get("sourceUrl") or "",
                        "checkedAt": checked, "via": "article"}
            break
    return out


def self_product(article: dict, info: dict, fields: list[tuple[str, dict]], *, fetcher, ask, checked: str, log_fn=log) -> dict:
    name = (info.get("product_name") or "").strip()
    if not name or squash(name) not in squash(f"{article.get('title', '')}\n{article.get('body', '')}"):
        m = re.search(r"「([^」]{2,40})」", article.get("title") or "")
        name = m.group(1) if m else name
    url = article.get("sourceUrl") or ""
    specs: dict[str, dict] = {}
    if url:
        try:
            page = fetcher(url)
            raw = ask(EXTRACT_SYSTEM, EXTRACT_PROMPT.format(name=name, fields=fields_prompt(fields), url=url,
                                                            text=page_excerpt(page["text"], fields)), EXTRACT_SCHEMA)
            specs = verify(raw or {}, page["text"], fields, url, checked, log_fn)
        except Skip as why:
            log_fn(f"   [rivals] 元の発表を読めませんでした（{why}）。記事の表を使います")
        except Exception as error:  # noqa: BLE001
            log_fn(f"   [rivals] 元の発表の読み取りに失敗（{str(error)[:80]}）。記事の表を使います")
    for key, spec in table_specs(article, fields).items():
        specs.setdefault(key, spec)
    return {"name": name, "maker": info.get("maker_en") or "", "self": True, "url": url,
            "sourceName": article.get("sourceName") or source_links.site_name(url), "checkedAt": checked, "specs": specs}


# --------------------------------------------------------------------------- #
# ライバル
# --------------------------------------------------------------------------- #


def candidates(info: dict, catalog: dict, category: str, log_fn=log) -> list[dict]:
    seen: list[str] = []
    out = []
    own = info.get("product_name") or ""
    own_maker = info.get("maker_en") or ""
    for r in catalog["categories"][category].get("rivals") or []:
        if same_product(r["name"], own) or (own_maker and squash(r["maker"]) == squash(own_maker)):
            continue
        out.append({**r, "curated": True})
        seen.append(squash(r["name"]))
    for r in info.get("rivals") or []:
        name, maker, url = str(r.get("name") or "").strip(), str(r.get("maker_en") or "").strip(), str(r.get("official_url") or "").strip()
        if not name or any(same_product(name, s) or squash(name) in s or s in squash(name) for s in seen):
            continue
        if same_product(name, own) or (own_maker and squash(maker) == squash(own_maker)):
            continue
        if not official_url_ok(url, maker, catalog):
            log_fn(f"   [rivals] 候補「{name}」: 公式のページでない・URL が無いので使わない（{url[:60]}）")
            continue
        out.append({"name": name, "maker": maker, "url": url, "curated": False})
        seen.append(squash(name))
    return out[:MAX_CANDIDATES]


def rival_product(cand: dict, fields: list[tuple[str, dict]], *, fetcher, ask, checked: str, log_fn=log) -> dict:
    page = fetcher(cand["url"])
    final = page.get("final_url") or cand["url"]
    if not cand.get("curated") and source_links.base_domain(source_links.host(final)) != source_links.base_domain(source_links.host(cand["url"])):
        raise Skip("別のサイトへ転送された")
    if not name_on_page(cand["name"], cand["maker"], page["text"] + " " + page.get("title", "")):
        raise Skip("ページに製品の名前が無い")
    if DISCONTINUED_RE.search(page["text"][:4000]):
        raise Skip("販売終了の表示")
    raw = ask(EXTRACT_SYSTEM, EXTRACT_PROMPT.format(name=cand["name"], fields=fields_prompt(fields), url=final,
                                                    text=page_excerpt(page["text"], fields)), EXTRACT_SCHEMA)
    specs = verify(raw or {}, page["text"], fields, final, checked, log_fn)
    if len(specs) < MIN_FIELDS:
        raise Skip(f"公式ページから読み取れた項目が{len(specs)}つだけ")
    host = source_links.host(final)
    region = region_of(final)
    return {"name": cand["name"], "maker": cand["maker"], "self": False, "url": final,
            "sourceName": (cand.get("sourceName") or f"{cand['maker']}公式") + (f"（{region}）" if region else ""), "host": host,
            "checkedAt": checked, "specs": specs}


# --------------------------------------------------------------------------- #
# 数字にして比べる（表の数字だけ。差は calc_facts で計算して式を残す）
# --------------------------------------------------------------------------- #


def numeric(display: str, units: list[str]) -> dict | None:
    """「約24kg」→ {lo:24, hi:24, unit:"kg", approx:True}。「23.3〜23.7kg」→ 範囲。数が2つ以上で範囲でなければ None"""
    text = nfkc(display)
    text = re.sub(r"\s*[（(][^）)]*[）)]", "", text)  # 添え書き（サイズ・条件）は外す
    unit_rx = "|".join(re.escape(u) for u in sorted({nfkc(u) for u in units if len(u) > 0 and u not in ('"', "″", "×")}, key=len, reverse=True))
    if not unit_rx:
        return None
    m = re.fullmatch(rf"\s*(約|最大|最大で)?\s*(\d+(?:,\d{{3}})*(?:\.\d+)?)\s*(?:({unit_rx})\s*)?(?:{RANGE_SEP_RE.pattern}(\d+(?:,\d{{3}})*(?:\.\d+)?))?\s*({unit_rx})\s*", text, re.I)
    if not m:
        return None
    unit_raw = (m.group(5) or m.group(3) or "").lower()
    norm = UNIT_NORM.get(unit_raw)
    if not norm:
        return None
    lo = float(m.group(2).replace(",", "")) * norm[1]
    hi = float(m.group(4).replace(",", "")) * norm[1] if m.group(4) else lo
    if m.group(1) and m.group(1) != "約":
        return None  # 「最大」は比べない
    return {"lo": min(lo, hi), "hi": max(lo, hi), "unit": norm[0], "approx": m.group(1) == "約", "range": hi != lo}


def _fmt(value: float, unit: str) -> str:
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{text}{unit}"


def compare_sentence(field: dict, me: dict, rivals: list[dict], key: str, computed: list) -> str | None:
    """1つの項目の「比べてわかること」。主役とライバルの値が同じ単位の数のときだけ"""
    units = field.get("units") or []
    mine = me["specs"].get(key)
    a = numeric(mine["display"], units) if mine else None
    if not a or a["range"]:
        return None
    pairs = []
    for r in rivals:
        spec = r["specs"].get(key)
        b = numeric(spec["display"], units) if spec else None
        if b and b["unit"] == a["unit"]:
            pairs.append((r, b))
    if not pairs:
        return None
    small, big = (field.get("words") or ["小さい", "大きい"])[:2]
    v = a["lo"]
    tol = v * (0.05 if a["approx"] else 0.03)
    same, clauses = [], []
    for r, b in pairs:
        if b["hi"] < v - tol or b["lo"] > v + tol:
            if b["range"]:
                word = big if b["hi"] < v else small
                clauses.append(f"{r['name']}（{r['specs'][key]['display']}）より{word}")
                continue
            c = calc_facts.spec_difference(me["name"], v, r["name"], b["lo"], a["unit"])
            computed.append(c)
            word = big if b["lo"] < v else small
            clauses.append(f"{r['name']}より{'約' if a['approx'] else ''}{_fmt(c.values[0], a['unit'])}{word}")
        else:
            same.append(r["name"])
    label = f"{field['label']}（{mine['display']}）"
    parts = []
    if same:
        parts.append(f"{'比べた製品' if len(same) == len(pairs) and len(pairs) > 1 else '・'.join(same)}とほぼ同じ")
    parts += clauses
    if not parts:
        return None
    return f"{label}は" + "で、".join(parts) + "です。"


def takeaway(data: dict, catalog: dict) -> tuple[list[str], list]:
    me = next(p for p in data["products"] if p.get("self"))
    rivals = [p for p in data["products"] if not p.get("self")]
    cat = catalog["categories"][data["category"]]
    order = list(dict.fromkeys(list(cat.get("chart") or []) + list(cat["fields"])))
    computed: list = []
    out = []
    for key in order:
        field = catalog["fields"].get(key) or {}
        if not field.get("compare"):
            continue
        if s := compare_sentence(field, me, rivals, key, computed):
            out.append(s)
        if len(out) >= 2:
            break
    return out, computed


def price_note(data: dict, fx: dict | None, computed: list) -> str:
    me = next(p for p in data["products"] if p.get("self"))
    rival_prices = [(p["name"], p["specs"]["price"]) for p in data["products"] if not p.get("self") and p["specs"].get("price")]
    parts = []
    if not me["specs"].get("price"):
        parts.append(f"{me['name']}の価格は、発表資料には書かれていません（未発表）。")
    if rival_prices:
        listed = "、".join(f"{n}は{s['display']}" for n, s in rival_prices)
        parts.append(f"比べた製品の公式価格は、{listed}です。")
        for n, s in rival_prices:
            if s["currency"] == "USD" and fx and pricing.valid_rate(fx.get("rate")):
                est = pricing.yen_estimate(s["amount"], "USD", fx, None, False)
                if est:
                    yen = calc_facts.fmt_qty(est["exTax"], "JPY")
                    computed.append(calc_facts.Computed("yen", f"{pricing.money(s['amount'], 'USD')} × {fx['rate']:.2f}円 = 約{yen}", [float(est["exTax"])]))
                    parts.append(f"{n}の価格は、1ドル={fx['rate']:.2f}円（{fx.get('date', '')}）で約{yen}の目安です（サイソク編集部の計算）。")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# 本文の章・図
# --------------------------------------------------------------------------- #


def _cell(text: str) -> str:
    return (text or "").replace("|", "／").replace("\n", " ").strip()


def section_markdown(data: dict, catalog: dict) -> str:
    cat = catalog["categories"][data["category"]]
    products = data["products"]
    keys = [k for k in cat["fields"] if any(p["specs"].get(k) for p in products)] + ["price"]
    lines = [f"## {HEADING}", "",
             f"{cat['label']}の中から、同じクラスで今売られている製品と、主な仕様と価格を並べました。"
             "数字はどれも各社の公式ページ・発表資料に書かれているものです。", ""]
    lines.append("| 項目 | " + " | ".join(_cell(p["name"]) for p in products) + " |")
    lines.append("|---" * (len(products) + 1) + "|")
    for key in keys:
        label = "価格（公式）" if key == "price" else catalog["fields"][key]["label"]
        row = []
        for p in products:
            spec = p["specs"].get(key)
            if spec:
                row.append(_cell(spec["display"]))
            elif key == "price" and p.get("self"):
                row.append("未発表")
            else:
                row.append(MISSING)
        lines.append(f"| {label} | " + " | ".join(row) + " |")
    lines.append("")
    if data.get("takeaway"):
        lines += ["要約すると、" + "".join(data["takeaway"]), ""]
    if data.get("priceNote"):
        lines += [data["priceNote"], ""]
    src = "／".join(f"{_cell(p['name'])}＝[{_cell(p['sourceName'])}]({p['url']})" for p in products if p.get("url"))
    lines.append(f"- 出典：{src}（{md(data['checkedAt'])}確認）")
    if any(p["specs"].get("weight") for p in products):
        lines.append("- 重さは、各社が測ったサイズ・装備が違います（各社の公式ページの書き方のまま）。")
    lines.append(f"- 「{MISSING}」は、その会社の公式ページに書かれていない項目です。サイソクでは推測で埋めていません。")
    return "\n".join(lines) + "\n"


SECTION_RE = re.compile(rf"^## {re.escape(HEADING)}\n.*?(?=^## |\Z)", re.M | re.S)
ANCHOR_AFTER_RE = re.compile(r"^##\s*(?:数字で見ると|主な仕様|スペック|仕様)", re.M)
ANCHOR_BEFORE_RE = re.compile(r"^##\s*(?:なぜ今|自分に関係ある|もう使える|日本で(?:も)?使える|まとめ)", re.M)


def remove_section(body: str) -> str:
    return SECTION_RE.sub("", body or "").replace("\n\n\n", "\n\n")


def insert_section(body: str, section: str) -> str:
    """数字の表の章の後ろ（無ければ「なぜ今？」「まとめ」などの前）に差し込む。前の章があれば差し替える"""
    body = remove_section(body)
    m = ANCHOR_AFTER_RE.search(body)
    if m:
        nxt = re.search(r"^## ", body[m.end():], re.M)
        pos = m.end() + nxt.start() if nxt else len(body)
    else:
        a = ANCHOR_BEFORE_RE.search(body)
        pos = a.start() if a else len(body)
    head, tail = body[:pos].rstrip("\n"), body[pos:].lstrip("\n")
    return f"{head}\n\n{section.rstrip()}\n\n{tail}" if tail else f"{head}\n\n{section.rstrip()}\n"


def chart_for(data: dict, catalog: dict) -> dict | None:
    """重さ・トルクなど1〜2項目の棒（lib/charts.ts の rivals）。主役とライバル1つ以上に同じ単位の1つの数があるときだけ"""
    cat = catalog["categories"][data["category"]]
    groups = []
    for key in cat.get("chart") or []:
        field = catalog["fields"].get(key) or {}
        items, unit = [], None
        for p in data["products"]:
            spec = p["specs"].get(key)
            n = numeric(spec["display"], field.get("units") or []) if spec else None
            if not n or n["range"] or (unit and n["unit"] != unit):
                if p.get("self"):
                    break
                continue
            unit = n["unit"]
            items.append({"label": p["name"], "value": n["lo"], "display": spec["display"], **({"highlight": True} if p.get("self") else {})})
        if len(items) >= 2 and items[0].get("highlight"):
            groups.append({"label": field["label"], "unit": unit, "items": items})
        if len(groups) >= 2:
            break
    if not groups:
        return None
    return {"kind": "rivals", "title": "同じクラスの製品との比較", "groups": groups, "afterHeading": HEADING,
            "source": {"name": "各社の公式ページ・発表資料", "checkedAt": md(data["checkedAt"])}}


def fact_sources(data: dict) -> list[dict]:
    return [{"label": "比較", "name": f"{p['name']}：{p['sourceName']}", "url": p["url"], "checkedAt": p["checkedAt"]}
            for p in data["products"] if not p.get("self")]


# --------------------------------------------------------------------------- #
# まとめて組み立てる
# --------------------------------------------------------------------------- #


def build(article: dict, *, catalog: dict | None = None, ask=default_ask, fetcher=fetch_official, fx: dict | None = None,
          now: datetime | None = None, budget: float = ARTICLE_BUDGET, log_fn=log) -> tuple[dict | None, str]:
    """(比較のデータ, 理由)。作れなければ (None, 理由)"""
    catalog = catalog or load_catalog()
    started = time.time()
    checked = today(now)
    info = classify(article, catalog, ask)
    if not info.get("is_product"):
        return None, "製品の記事ではない"
    category = info.get("category")
    if not category:
        return None, "分類が決まらない"
    # 主役の製品が題名に出てこない記事（月のまとめ・業界の動向など）には付けない
    if not name_on_page(str(info.get("product_name") or ""), str(info.get("maker_en") or ""),
                        f"{article.get('title', '')} {article.get('seoTitle', '')}"):
        return None, f"主役の製品「{info.get('product_name')}」が題名に無い（まとめ記事など）"
    fields = field_defs(catalog, category)
    log_fn(f"   [rivals] 製品「{info.get('product_name')}」分類 {category}")
    me = self_product(article, info, fields, fetcher=fetcher, ask=ask, checked=checked, log_fn=log_fn)
    if len([k for k in me["specs"] if k != "price"]) < 1:
        return None, "主役の製品の仕様が読み取れない"
    rivals = []
    skipped = []
    for cand in candidates(info, catalog, category, log_fn):
        if len(rivals) >= MAX_RIVALS:
            break
        if time.time() - started > budget:
            log_fn("   [rivals] 時間の上限のため、残りの候補は見ません")
            break
        if any(squash(cand["maker"]) == squash(r["maker"]) for r in rivals):
            continue  # 同じメーカーの2台目は入れない（いろいろな会社と比べる）
        try:
            r = rival_product(cand, fields, fetcher=fetcher, ask=ask, checked=checked, log_fn=log_fn)
        except Skip as why:
            log_fn(f"   [rivals] 「{cand['name']}」は飛ばす（{why}）")
            skipped.append({"name": cand["name"], "url": cand["url"], "reason": str(why)})
            continue
        except Exception as error:  # noqa: BLE001
            log_fn(f"   [rivals] 「{cand['name']}」は飛ばす（{str(error)[:80]}）")
            skipped.append({"name": cand["name"], "url": cand["url"], "reason": str(error)[:80]})
            continue
        log_fn(f"   [rivals] 「{r['name']}」: {', '.join(r['specs'])}（{r['url'][:80]}）")
        rivals.append(r)
    if len(rivals) < MIN_RIVALS:
        return None, f"公式ページを読めたライバルが{len(rivals)}つだけ"
    data = {"version": 1, "category": category, "categoryLabel": catalog["categories"][category]["label"],
            "checkedAt": checked, "products": [me] + rivals, "skipped": skipped}
    if fx is None:
        fx = facts_bundle.load_fx()
    data["takeaway"], computed = takeaway(data, catalog)
    data["priceNote"] = price_note(data, fx, computed)
    data["computed"] = [{"text": c.text, "values": c.values} for c in computed]
    if fx and any(p["specs"].get("price", {}).get("currency") == "USD" for p in rivals):
        data["fx"] = {k: fx.get(k) for k in ("rate", "date", "source", "sourceUrl")}
    return data, "ok"


def apply(article: dict, data: dict, catalog: dict | None = None) -> dict:
    """記事に章・データ・図・出典を入れる（record を書き換えて返す）"""
    catalog = catalog or load_catalog()
    article["body"] = insert_section(article.get("body") or "", section_markdown(data, catalog))
    article["rivals"] = data
    charts = [c for c in article.get("charts") or [] if c.get("kind") != "rivals"]
    if chart := chart_for(data, catalog):
        charts.append(chart)
    if charts:
        article["charts"] = charts
    else:
        article.pop("charts", None)
    sources = [s for s in article.get("factSources") or [] if s.get("label") != "比較"]
    article["factSources"] = sources + fact_sources(data)
    return article


def attach(article: dict, **kw) -> tuple[bool, str]:
    """生成の流れから呼ぶ: 作れて品質ゲートを通れば記事に入れる。(入れたか, 理由)"""
    import quality  # noqa: PLC0415

    catalog = kw.pop("catalog", None) or load_catalog()
    data, why = build(article, catalog=catalog, **kw)
    if not data:
        return False, why
    trial = apply(json.loads(json.dumps(article)), data, catalog)
    if problems := quality.rival_problems(trial):
        return False, "品質ゲート: " + " / ".join(problems)[:200]
    apply(article, data, catalog)
    return True, "ok"


# --------------------------------------------------------------------------- #
# 実行
# --------------------------------------------------------------------------- #


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    cut = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    state = {k: v for k, v in state.items() if str(v.get("at", "")) >= cut}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def remember(slug: str, result: str) -> None:
    """調べた結果を残す（同じ記事を次の回に調べ直さない。web_articles.attach_rivals からも呼ぶ）"""
    if not slug:
        return
    state = _load_state()
    state[slug] = {"at": datetime.now(timezone.utc).isoformat(), "result": result[:120]}
    _save_state(state)


def targets(args, catalog: dict) -> list[Path]:
    if args.slug:
        return [ARTICLES_DIR / f"{s}.json" for s in args.slug]
    hours = args.recent_hours or args.days * 24
    cut = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for path in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            a = json.loads(path.read_text("utf-8"))
            at = datetime.fromisoformat(str(a.get("generatedAt") or a.get("publishedAt")).replace("Z", "+00:00"))
        except (OSError, ValueError, TypeError):
            continue
        if at < cut or a.get("duplicateOf") or a.get("disaster") or a.get("sourceType") != "web":
            continue
        # 題名・タグが製品の分類に当たるものだけ LLM に聞く（無料枠の節約）
        if guess_category(a, catalog) is None:
            continue
        out.append(path)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--slug", action="append")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--recent-hours", type=int, default=0)
    parser.add_argument("--max", type=int, default=0, help="1回に調べる記事の数（0 は上限なし）")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true", help="前に調べた記事も調べ直す")
    parser.add_argument("--budget", type=int, default=0, help="全体の秒数の上限")
    args = parser.parse_args(argv)
    catalog = load_catalog()
    state = _load_state()
    started = time.time()
    done = added = 0
    for path in targets(args, catalog):
        article = json.loads(path.read_text("utf-8"))
        slug = article["slug"]
        if not args.slug and not args.force and (slug in state or article.get("rivals")):
            continue
        if args.max and done >= args.max:
            break
        if args.budget and time.time() - started > args.budget:
            log("[rivals] 時間の上限のため、ここで終えます")
            break
        done += 1
        log(f"[rivals] {slug}「{article.get('title', '')[:40]}」")
        before = (SECTION_RE.search(article.get("body") or "") or [None])[0]
        try:
            ok, why = attach(article)
        except Exception as error:  # noqa: BLE001 - 1本の失敗で全体を止めない
            ok, why = False, f"失敗（{str(error)[:100]}）"
        log(f"   [rivals] {'追加' if ok else '見送り'}: {why}")
        state[slug] = {"at": datetime.now(timezone.utc).isoformat(), "result": "added" if ok else why[:120]}
        if not ok:
            continue
        added += 1
        print("----- 前 -----\n" + (before or "（比較の章なし）"))
        print("----- 後 -----\n" + SECTION_RE.search(article["body"]).group(0))
        chart = next((c for c in article.get("charts") or [] if c.get("kind") == "rivals"), None)
        print("図: " + (json.dumps(chart, ensure_ascii=False) if chart else "なし"))
        if not args.apply:
            continue
        path.write_text(json.dumps(article, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.apply:
        _save_state(state)
    log(f"[rivals] 調べた {done}本 / 比較を{'追加' if args.apply else '作れた'} {added}本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
