#!/usr/bin/env python3
"""大きなニュースのレーダー（Python 側）。公式フィードが遅れても・載らなくても、大きな話題を取りこぼさないための2つ目の網。

- 判定ルールは scripts/fast_lane_rules.json の radar（Worker の ops/watchdog/src/radar.js と同じ判定。npm test で突き合わせる）
- まとめサイト（Techmeme・Hacker News・日本の IT メディア）は「見つけるため」だけに使う。記事の出典は一次情報
  （公式の発表・SEC の書類）を探して使い、無いときだけ信頼できる報道機関（radar.outlets）を「〇〇の報道によると」で出典にする

  python3 scripts/radar.py coverage                  # 直近の記事の索引 state/coverage.json を書く（Worker が「もう記事があるか」を見る）
  python3 scripts/radar.py missed --hours 30         # 見逃しチェック（前日〜今朝の大きなニュースで、記事が無いもの）
  python3 scripts/radar.py missed --issue --request  # Issue（ラベル missed-news）で知らせ、state/radar_requests.json に足す
                                                     #   他社・まとめの見出し（sources_registry.json の detect_feeds）とも突き合わせ、
                                                     #   他社だけが出した話題も Issue に並べる（--no-gaps で省く。依頼には回さない）
                                                     #   → Worker のレーダーが速報レーンに回す
  python3 scripts/radar.py request URL [--title T]   # 手動で速報レーンに回す（同じく state/radar_requests.json）
  echo '[{"title": "..."}]' | python3 scripts/radar.py classify     # Worker のテスト用（判定の突き合わせ）
  echo '{"title": "...", "urls": [], "records": [...]}' | python3 scripts/radar.py covered   # 同上（記事があるかの判定）
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dedupe  # noqa: E402
import persona  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = Path(__file__).resolve().with_name("fast_lane_rules.json")
ARTICLES_DIR = ROOT / "content" / "articles"
COVERAGE_PATH = ROOT / "state" / "coverage.json"
REQUESTS_PATH = ROOT / "state" / "radar_requests.json"
COVERAGE_KEEP_HOURS = 96
REQUEST_KEEP_HOURS = 48
JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; SaisokuRadar/1.0; +https://ai-tech-express.jp)"

# --------------------------------------------------------------------------- #
# ルール
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def rules() -> dict:
    data = json.loads(RULES_PATH.read_text("utf-8"))["radar"]
    flags = re.IGNORECASE
    data["_re"] = {
        "signal": re.compile(data["signal_re"], flags),
        "exclude": re.compile(data["exclude_re"], flags),
        "ai": re.compile(data["ai_re"], flags),
        "path": re.compile(data["primary_path_re"]),
    }
    data["_entities"] = [(e["name"], re.compile(e["re"], flags), bool(e.get("ai_only"))) for e in data["entities"]]
    data["primary_hosts"] = with_source_hosts(data["primary_hosts"], _source_list())
    data["sources"] = with_radar_sources(data["sources"], _registry().get("radar_sources", []))
    return data


def _registry() -> dict:
    try:
        return json.loads(RULES_PATH.with_name("sources_registry.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def with_radar_sources(sources: list[dict], extra: list[dict]) -> list[dict]:
    """sources_registry.json の radar_sources（追加の報道・まとめ）を足す。radar.js の withRadarSources と同じ。
    公式扱い（primary_hosts）・報道の出典（outlets）には足さない（見つけるためだけ）"""
    have = {s["slug"] for s in sources}
    return list(sources) + [s for s in extra if s.get("slug") and s["slug"] not in have and not s.get("disabled")]


def _source_list() -> list[dict]:
    """速報の発信元（fast_lane_rules.json の sources＋sources_registry.json）。radar.js の withSourceHosts と同じ材料"""
    out = list(json.loads(RULES_PATH.read_text("utf-8")).get("sources", []))
    out += _registry().get("sources", [])
    return out


def with_source_hosts(hosts: list[dict], sources: list[dict]) -> list[dict]:
    """発信元のホストも公式扱いにする（一覧の食い違いで「対象外のURL」にしない）"""
    out = list(hosts)
    for s in sources:
        host = urllib.parse.urlsplit(s.get("url", "")).netloc.lower().removeprefix("www.")
        if host and not any(host == p["host"] or host.endswith("." + p["host"]) for p in out):
            out.append({"host": host, "entity": "*", "name": s.get("name") or host})
    return out


def entities_of(text: str) -> list[str]:
    return [name for name, pattern, _ in rules()["_entities"] if pattern.search(text or "")]


def classify(title: str) -> dict:
    """レーダーに乗せる見出しか。{ok, reason, entities}。理由の文言は radar.js の classify() と同じ"""
    r = rules()
    t = str(title or "").strip()
    ex = r["_re"]["exclude"].search(t)
    if ex:
        return {"ok": False, "reason": f"除外語（{ex.group(0)}）", "entities": []}
    hits = [(name, ai_only) for name, pattern, ai_only in r["_entities"] if pattern.search(t)]
    names = [name for name, _ in hits]
    if not hits:
        return {"ok": False, "reason": "対象の会社・製品がない", "entities": []}
    if all(ai_only for _, ai_only in hits) and not r["_re"]["ai"].search(t):
        return {"ok": False, "reason": "AI の話題ではない", "entities": names}
    if not r["_re"]["signal"].search(t):
        return {"ok": False, "reason": "強い発表の語がない", "entities": names}
    return {"ok": True, "reason": "対象", "entities": names}


# --------------------------------------------------------------------------- #
# 「同じ話題の記事がもうあるか」（radar.js の features / covered と同じ）
# --------------------------------------------------------------------------- #

# 会社・主力製品の名前（これだけ一致しても同じ話題とは言えない）と、見出しの一般語。JS と同じ一覧を使う
ALIASES = set(rules()["aliases"])
VERSIONED_OK = set(rules()["versioned_ok"])
STOP = set(rules()["stop_words"])
VERSION_RE = re.compile(r"(?<![a-z0-9])([a-z]+)[ \-]?(\d+(?:\.\d+)?)(?![0-9])")
BRACKET_RE = re.compile(r"[「『]([^」』]{2,30})[」』]")
KATAKANA_RE = re.compile(r"[ァ-ヴー]{3,}")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+&]*")
IPO_RE = re.compile(r"(?<![A-Za-z])IPO(?![A-Za-z])|(?<![A-Za-z])S-1(?![0-9])|initial public offering|go(?:es|ing)? public|上場|新規株式公開", re.I)
EVENT_PREFIX = "#"


def _squash(text: str) -> str:
    return re.sub(r"[^0-9a-zぁ-んァ-ヴー一-龥]+", "", unicodedata.normalize("NFKC", text or "").lower())


def features(text: str, strong: bool = False) -> set[str]:
    """話題の手がかりになる語。strong=True（レーダーの見出し側）は固有名詞らしい語だけ（大文字を含む英字・型番・括弧・カタカナ）。
    strong=False（記事側）は英字の語をすべて含める"""
    t = unicodedata.normalize("NFKC", text or "")
    low = t.lower()
    out: set[str] = set()
    alphas: set[str] = set()
    for m in VERSION_RE.finditer(low):
        alpha, num = m.group(1), m.group(2)
        if alpha in STOP or (alpha in ALIASES and alpha not in VERSIONED_OK):
            continue
        out.add(alpha + num)
        alphas.add(alpha)
    for m in BRACKET_RE.finditer(t):
        word = _squash(m.group(1))
        if len(word) >= 2 and word not in ALIASES:
            out.add(word)
    for m in KATAKANA_RE.finditer(t):
        word = m.group(0).strip("ー")
        if len(word) >= 3 and word not in STOP and word not in ALIASES:
            out.add(word)
    for m in WORD_RE.finditer(t):
        word = m.group(0)
        lw = word.lower()
        if len(lw) < 3 or lw in STOP or lw in ALIASES or lw.isdigit():
            continue
        if strong and not any(c.isupper() for c in word):
            continue
        if strong and lw in alphas:
            continue  # 「Genie 4」の Genie だけで「Genie 3」の記事と同じ話題にしない
        out.add(lw)
    if IPO_RE.search(t):
        out.add(EVENT_PREFIX + "ipo")
    return out


def _special(token: str) -> bool:
    """1語の一致でも同じ話題とみなす語（2文字以上の名前＋版数の製品名・上場のような1社に1回の出来事）。
    M6・A20 のような1文字の型番は2語目の手がかりにだけ使う"""
    return token.startswith(EVENT_PREFIX) or bool(re.fullmatch(r"[a-z]{2,}\d+(?:\.\d+)?", token))


STALE_DAYS = 45


def stale(url: str, at: datetime | None) -> bool:
    """URL に入っている年月（/2025/10/ など）が記事の時刻より古すぎる（関連記事・前の型の発表へのリンク）"""
    m = re.search(r"/(20\d\d)/(\d{1,2})/", url or "")
    if not m or not at or not 1 <= int(m.group(2)) <= 12:
        return False
    return (at - datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)).days > STALE_DAYS + 31


def norm_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    host = parts.netloc.lower().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}" if host else ""


def url_keys(url: str) -> list[str]:
    """同じページとみなす URL の鍵（正規化した URL と「サイト＋最後の長い部分」。apple.com/newsroom/X と apple.com/jp/newsroom/X を同じにする）"""
    n = norm_url(url)
    if not n:
        return []
    host, *rest = n.split("/")
    parts = [p for p in rest if p]
    last = parts[-1] if parts else ""
    return [n, f"{host}|{last}"] if len(last) >= 12 and re.search(r"[a-z]-[a-z0-9]", last) else [n]


def prepare(records: list[dict]) -> list[dict]:
    """coverage の記録（{slug, at, title, head, text, urls}）に判定用の語を足す。
    head は見出し（記事の題＋元記事の題）、text は見出し＋キーワード"""
    out = []
    for r in records:
        text = str(r.get("text") or "")
        title = str(r.get("title") or text.split(" | ")[0])
        head = str(r.get("head") or title)
        out.append({**r, "_ents": set(entities_of(text)), "_tokens": features(text) | features(head), "_head": features(head),
                    "_heads": [dedupe.bigrams(h) for h in head.split(" | ")],
                    "_urls": {k for u in r.get("urls") or [] if u for k in url_keys(u)}, "_title": title})
    df: dict[str, int] = {}
    for r in out:
        for token in r["_tokens"]:
            df[token] = df.get(token, 0) + 1
    for r in out:
        r["_df"] = df
    return out


def covered(title: str, urls: list[str], records: list[dict]) -> dict | None:
    """同じ話題の記録があれば {slug, reason}。records は prepare() 済み"""
    wanted = {k for u in urls if u for k in url_keys(u)}
    for r in records:
        if wanted & r["_urls"]:
            return {"slug": r.get("slug", ""), "reason": "同じ出典URL"}
    ents = set(entities_of(title))
    strong = features(title, strong=True)
    grams = dedupe.bigrams(title)
    best = None
    for r in records:
        if ents and not (ents & r["_ents"]):
            continue
        # 1語だけの一致は、記事の見出しに出てくる語で、見出しの会社がすべて記事にも出てくるときに限る
        # （キーワードに機能名が並んでいるだけの記事・別の会社との提携の話で「記事あり」にしない）
        shared = strong & r["_tokens"]
        head = strong & r["_head"]
        all_ents = ents <= r["_ents"]
        sim = max([dedupe.jaccard(grams, g) for g in r["_heads"]] or [0.0])
        df = r["_df"]
        why = ""
        if len(shared) >= 2 and head:
            why = f"固有名詞が一致（{'・'.join(sorted(shared)[:3])}）"
        elif all_ents and any(_special(t) for t in head):
            why = f"製品名・出来事が一致（{sorted(t for t in head if _special(t))[0]}）"
        elif all_ents and any(df.get(t, 0) < 3 and len(t) >= 4 for t in head):
            why = f"珍しい固有名詞が一致（{sorted(head)[0]}）"
        elif ents and head and sim >= 0.3:
            why = f"固有名詞＋見出しが類似（{sorted(head)[0]} / {sim:.2f}）"
        elif ents and sim >= dedupe.JACCARD_DUP:
            why = "見出しが類似"
        if why and (best is None or len(shared) > best[0]):
            best = (len(shared), {"slug": r.get("slug", ""), "reason": why})
    return best[1] if best else None


# --------------------------------------------------------------------------- #
# 一次情報を探す（radar.js の pickPrimary と同じ考え方）
# --------------------------------------------------------------------------- #


def _host_match(host: str, base: str) -> bool:
    host = host.lower().removeprefix("www.")
    return host == base or host.endswith("." + base)


def official(url: str, ents: list[str]) -> dict | None:
    """公式の一次情報の URL なら {host, name, entity}"""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not rules()["_re"]["path"].search(parts.path or "/"):
        return None
    for h in rules()["primary_hosts"]:
        if _host_match(parts.netloc, h["host"]) and (h["entity"] == "*" or not ents or h["entity"] in ents):
            return h
    return None


def outlet(url: str) -> dict | None:
    try:
        host = urllib.parse.urlsplit(url).netloc
    except ValueError:
        return None
    # 見つけるためだけの国内の競合（registry の radar_no_cite_hosts）は出典にしない（radar.js の outletOf と同じ）
    if any(_host_match(host, h) for h in _registry().get("radar_no_cite_hosts", [])):
        return None
    for o in rules()["outlets"]:
        if _host_match(host, o["host"]):
            return o
    return None


def blocked(url: str) -> bool:
    try:
        host = urllib.parse.urlsplit(url).netloc
    except ValueError:
        return True
    return any(_host_match(host, b) for b in rules()["blocked_hosts"])


HREF_RE = re.compile(r"""<a\b[^>]*?\bhref=["']([^"'#]+)["']""", re.I)


def links_in(page_html: str, base: str) -> list[str]:
    out = []
    for m in HREF_RE.finditer(page_html[:800_000]):
        try:
            out.append(urllib.parse.urljoin(base, html.unescape(m.group(1)).strip()))
        except ValueError:
            continue
    return list(dict.fromkeys(out))


def pick_primary(candidates: list[str], ents: list[str], title: str, at: datetime | None = None) -> str:
    """公式の URL の中からいちばん良いもの（会社の公式 > SEC、見出しと語の重なりが多いもの。古い年月の URL は除く）"""
    words = features(title) | {w.lower() for w in WORD_RE.findall(title)}
    scored = []
    for url in candidates:
        h = official(url, ents)
        if not h or stale(url, at):
            continue
        slug_words = set(re.findall(r"[a-z0-9]+", urllib.parse.urlsplit(url).path.lower()))
        scored.append(((0 if h["entity"] == "*" else 1), len(words & slug_words), url))
    return max(scored)[2] if scored else ""


def fetch_text(url: str, timeout: int = 15) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "ja,en;q=0.8"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(1_500_000).decode("utf-8", "replace")


def resolve(item: dict) -> dict:
    """{url, sourceName, secondary, via}。一次情報 → 報道機関（outlets）の順。見つからなければ url は空"""
    ents = item.get("entities") or entities_of(item["title"])
    at = item.get("published")
    links = [u for u in item.get("links") or [] if u and not blocked(u)]
    best = pick_primary(links, ents, item["title"], at)
    company = lambda u: bool(u) and official(u, ents)["entity"] != "*"  # noqa: E731
    if not company(best):
        # 会社の公式が見つかるまで記事ページの中のリンクを探す（SEC の書類だけなら次のページも見る）
        found = [best] if best else []
        for link in [u for u in links if not official(u, ents)][:2]:
            try:
                found += links_in(fetch_text(link), link)
            except Exception:  # noqa: BLE001
                continue
            best = pick_primary(found, ents, item["title"], at)
            if company(best):
                break
    if best:
        return {"url": best, "sourceName": official(best, ents)["name"], "secondary": False, "via": "official"}
    # 一次情報が無い: 信頼できる報道機関の記事を出典に（有料の壁がある媒体は最後）
    outs = sorted((u for u in links if outlet(u)), key=lambda u: bool(outlet(u).get("paywall")))
    if outs:
        return {"url": outs[0], "sourceName": outlet(outs[0])["name"], "secondary": True, "via": "outlet"}
    return {"url": "", "sourceName": "", "secondary": False, "via": "none"}


# --------------------------------------------------------------------------- #
# 記事の索引（state/coverage.json）
# --------------------------------------------------------------------------- #


def article_record(data: dict) -> dict:
    parts = [str(data.get("title") or "")]
    if data.get("videoTitle") and data.get("videoTitle") != data.get("title"):
        parts.append(str(data["videoTitle"]))
    words = [str(k) for k in (data.get("keywords") or [])][:10] + [str(t) for t in (data.get("tags") or [])][:8]
    if words:
        parts.append(" / ".join(words))
    urls = [str(data.get("sourceUrl") or "")] + [str(u.get("sourceUrl")) for u in data.get("updates") or [] if isinstance(u, dict) and u.get("sourceUrl")]
    # 続報の見出しも「見出し」に含める（同じ話題の新しい展開を足した記事）
    heads = parts[:2] + [str(u.get("title")) for u in data.get("updates") or [] if isinstance(u, dict) and u.get("title")][:3]
    return {"slug": str(data.get("slug") or ""), "at": str(data.get("generatedAt") or data.get("publishedAt") or ""),
            "title": parts[0], "head": " | ".join(heads)[:400], "text": " | ".join(parts)[:600], "urls": [u for u in urls if u]}


def load_records(hours: int = COVERAGE_KEEP_HOURS, now: datetime | None = None) -> list[dict]:
    since = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    records = []
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        when = dedupe._article_time(data)
        if when is None or when < since:
            continue
        rec = article_record(data)
        rec["slug"] = rec["slug"] or path.stem
        records.append(rec)
    return sorted(records, key=lambda r: r["at"], reverse=True)


def write_coverage() -> int:
    records = load_records()
    COVERAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 中身が変わらなければ書き換えない（コミットを増やさない）
    body = {"hours": COVERAGE_KEEP_HOURS, "items": records}
    text = json.dumps(body, ensure_ascii=False, indent=1) + "\n"
    if not COVERAGE_PATH.exists() or COVERAGE_PATH.read_text("utf-8") != text:
        COVERAGE_PATH.write_text(text, "utf-8")
    print(f"[coverage] 直近{COVERAGE_KEEP_HOURS}時間の記事 {len(records)} 本を state/coverage.json に書きました")
    return 0


# --------------------------------------------------------------------------- #
# 見逃しチェック
# --------------------------------------------------------------------------- #

ET_OFFSET = None


def _eastern(dt_naive: datetime) -> datetime:
    """Techmeme の時刻（米東部時間）を UTC に"""
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        return dt_naive.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return (dt_naive + timedelta(hours=4)).replace(tzinfo=timezone.utc)


def techmeme_river(page: str) -> list[dict]:
    """Techmeme の river（時系列の一覧）。日付の見出し H2 と行ごとの時刻から UTC の時刻を出す"""
    items = []
    day = None
    for m in re.finditer(r"<H2>([A-Za-z]+ \d{1,2}, \d{4})</H2>|<tr class=\"ritem\">([\s\S]*?)</tr>", page, re.I):
        if m.group(1):
            try:
                day = datetime.strptime(m.group(1), "%B %d, %Y")
            except ValueError:
                day = None
            continue
        row = m.group(2)
        t = re.search(r"<td>\s*(\d{1,2}:\d{2}\s*[AP]M)", row)
        links = re.findall(r"<a href=\"([^\"]+)\">([\s\S]*?)</a>", row)
        if not (day and t and links):
            continue
        url, title = links[-1]
        when = _eastern(datetime.strptime(f"{day:%Y-%m-%d} {t.group(1).replace(' ', '')}", "%Y-%m-%d %I:%M%p"))
        title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        items.append({"source": "techmeme", "title": title, "url": url, "links": [url], "published": when})
    return items


def feed_items(src: dict) -> list[dict]:
    import feeds  # noqa: PLC0415

    out = []
    for i in feeds.parse_feed({**src, "lang": src.get("lang", "ja")}):
        out.append({"source": src["slug"], "title": i["title"], "url": i["url"], "links": [i["url"]], "published": i["published_at"]})
    return out


def hn_items(limit: int = 30) -> list[dict]:
    ids = json.loads(fetch_text("https://hacker-news.firebaseio.com/v0/beststories.json"))[:limit]
    ids += [i for i in json.loads(fetch_text("https://hacker-news.firebaseio.com/v0/topstories.json"))[:limit] if i not in ids]
    out = []
    for i in ids:
        try:
            d = json.loads(fetch_text(f"https://hacker-news.firebaseio.com/v0/item/{i}.json"))
        except Exception:  # noqa: BLE001
            continue
        if not d or not d.get("title"):
            continue
        url = d.get("url") or f"https://news.ycombinator.com/item?id={i}"
        out.append({"source": "hn", "title": d["title"], "url": url, "links": [url],
                    "published": datetime.fromtimestamp(int(d.get("time") or 0), timezone.utc)})
    return out


def gather(hours: int, now: datetime) -> tuple[list[dict], list[str]]:
    since = now - timedelta(hours=hours)
    items: list[dict] = []
    errors: list[str] = []
    try:
        items += techmeme_river(fetch_text("https://www.techmeme.com/river"))
    except Exception as error:  # noqa: BLE001
        errors.append(f"techmeme: {error}")
    for src in rules()["sources"]:
        if src["slug"] in ("techmeme", "hn"):
            continue
        try:
            items += feed_items(src)
        except Exception as error:  # noqa: BLE001
            errors.append(f"{src['slug']}: {error}")
    try:
        items += hn_items()
    except Exception as error:  # noqa: BLE001
        errors.append(f"hn: {error}")
    return [i for i in items if i.get("published") and i["published"] >= since], errors


# --------------------------------------------------------------------------- #
# 他社との突き合わせ（毎朝の見逃しチェック。sources_registry.json の detect_feeds・radar_sources・radar.sources）
# --------------------------------------------------------------------------- #

GAP_SIM = 0.3
GAP_MAX_PER_GENRE = 12
OUTLET_SUFFIX_RE = re.compile(r"\s*[（(\[【]?(?:ITmedia [A-Za-z+ ]+|Impress Watch|GIGAZINE|ギズモード・ジャパン|Yahoo!ニュース|CNET Japan|AI Watch|ASCII\.jp)[）)\]】]?\s*$|\s+[-|｜]\s+[^-|｜]{2,30}$")


def html_list_items(src: dict) -> list[dict]:
    """RSS の無い一覧ページ（fastlane.js の parseHtmlList と同じ読み方）。日付が無いので先頭の8本だけ"""
    page = fetch_text(src["url"])
    base = urllib.parse.urlsplit(src["url"])
    link_re = re.compile(src.get("link_re") or ".", re.I)
    out, seen = [], set()
    for m in re.finditer(r"""<a\b[^>]*?\bhref=["']([^"'#?]+)["'][^>]*>([\s\S]*?)</a>""", page[:1_500_000], re.I):
        try:
            u = urllib.parse.urlsplit(urllib.parse.urljoin(src["url"], html.unescape(m.group(1)).strip()))
        except ValueError:
            continue
        if u.netloc != base.netloc or not link_re.search(u.path):
            continue
        h = re.search(r"<h[1-4]\b[^>]*>([\s\S]*?)</h[1-4]>", m.group(2), re.I)
        if not h and src.get("title_from") != "text":
            continue
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h.group(1) if h else m.group(2)))).strip()[:200]
        key = f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}"
        if len(title) < 8 or key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "url": key})
    return out[:8]


def detect_sources() -> list[dict]:
    """突き合わせに使う他社・まとめ（ジャンル付き）。radar の見出し元は AI ジャンル"""
    out = [{**s, "genre": s.get("genre", "ai")} for s in rules()["sources"] if s.get("type") in ("rss", None) and s["slug"] != "hn"]
    have = {s["slug"] for s in out}
    out += [s for s in _registry().get("detect_feeds", []) if s.get("slug") not in have and not s.get("disabled")]
    return out


def gather_detect(hours: int, now: datetime) -> tuple[list[dict], list[str]]:
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    since = now - timedelta(hours=hours)

    def load(src):
        try:
            if src.get("type") == "html":
                return src, [{"source": src["slug"], "title": i["title"], "url": i["url"], "links": [i["url"]], "published": now, "undated": True}
                             for i in html_list_items(src)], ""
            return src, feed_items(src), ""
        except Exception as error:  # noqa: BLE001
            return src, [], f"{src['slug']}: {str(error)[:60]}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(load, detect_sources()))
    items, errors = [], []
    for src, got, error in results:
        if error:
            errors.append(error)
        for i in got:
            if i.get("published") and i["published"] >= since and "ai-tech-express.jp" not in i["url"]:
                items.append({**i, "genre": src.get("genre", "ai"), "outlet": src.get("name", src["slug"]), "topics": bool(src.get("topics"))})
    return items, errors


def _clean_title(title: str) -> str:
    return OUTLET_SUFFIX_RE.sub("", str(title or "")).strip()


def _same_head(a: str, b: str) -> bool:
    if dedupe.jaccard(dedupe.bigrams(a), dedupe.bigrams(b)) >= GAP_SIM:
        return True
    shared = features(a, strong=True) & features(b, strong=True)
    return len(shared) >= 2


def competitor_gaps(items: list[dict], records: list[dict]) -> list[dict]:
    """他社が出してサイソクに記事が無い話題（同じ話題はまとめて、出した社の数で並べる）"""
    prepared = prepare(records)
    ours = [h for r in records for h in str(r.get("head") or r.get("title") or "").split(" | ") if h]
    r_ = rules()["_re"]
    clusters: list[dict] = []
    for it in sorted(items, key=lambda i: i["published"]):
        title = _clean_title(it["title"])
        if not title:
            continue
        if it["genre"] == "ai" and not (r_["ai"].search(title) or entities_of(title)):
            continue
        if it["genre"] == "sports" and not persona.sports_scope(title)[0]:
            continue  # スポーツは日本のスポーツと世界的な大会だけ（scripts/persona.py）
        hit = next((c for c in clusters if c["genre"] == it["genre"] and (set(it["links"]) & c["links"] or _same_head(title, c["title"]))), None)
        if hit:
            hit["outlets"].add(it["outlet"])
            hit["links"] |= set(it["links"])
            hit["topics"] = hit["topics"] or it["topics"]
            hit["also"].append({"title": title, "outlet": it["outlet"], "url": it["url"]})
            continue
        clusters.append({"title": title, "url": it["url"], "genre": it["genre"], "outlets": {it["outlet"]}, "links": set(it["links"]),
                         "topics": it["topics"], "published": it["published"], "also": [], "undated": it.get("undated", False)})
    out = []
    for c in clusters:
        ai_big = c["genre"] == "ai" and classify(c["title"])["ok"]
        if not (c["topics"] or len(c["outlets"]) >= 2 or ai_big):
            continue
        heads = [c["title"]] + [a["title"] for a in c["also"]]
        if any(covered(h, list(c["links"]), prepared) for h in heads):
            continue
        if any(_same_head(h, o) for h in heads for o in ours):
            continue
        out.append(c)
    out.sort(key=lambda c: (-len(c["outlets"]), not c["topics"], c["published"]))
    kept, per = [], {}
    for c in out:
        if per.get(c["genre"], 0) >= GAP_MAX_PER_GENRE:
            continue
        per[c["genre"]] = per.get(c["genre"], 0) + 1
        kept.append(c)
    return kept


GENRE_LABEL = {"ai": "AI・テック", "business": "ビジネス・経済", "investment": "投資・お金", "real-estate": "不動産", "public": "国内・政策",
               "sports": "スポーツ", "tech-gadget": "ガジェット"}


def gaps_markdown(gaps: list[dict], n_items: int) -> str:
    lines = ["", f"### 他社が出して、サイソクに記事が無い話題（他社・まとめの見出し {n_items} 件と突き合わせ）", "",
             "記事にするときは、必ず一次情報（公式の発表・官公庁・企業のリリース）を探して出典にする。他社の記事を書き直さない。", ""]
    if not gaps:
        return "\n".join(lines + ["- なし"]) + "\n"
    by: dict[str, list[dict]] = {}
    for g in gaps:
        by.setdefault(g["genre"], []).append(g)
    for genre, items in by.items():
        lines.append(f"**{GENRE_LABEL.get(genre, genre)}**")
        for g in items:
            when = "日付なし" if g.get("undated") else g["published"].astimezone(JST).strftime("%m/%d %H:%M")
            mark = "（主要ニュース）" if g["topics"] else ""
            lines.append(f"- {g['title'][:110]}{mark} — {len(g['outlets'])}社: {'、'.join(sorted(g['outlets']))[:80]}（{when}） {g['url']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def judged_records() -> list[dict]:
    """速報レーンで扱って記事にしなかった話題（記事化の価値なし・重複と判定済み。state/fast_lane.json）。毎朝くり返し知らせない"""
    try:
        acks = json.loads((ROOT / "state" / "fast_lane.json").read_text("utf-8")).get("items", {})
    except (OSError, ValueError):
        return []
    return [{"slug": f"見送り済み:{a.get('outcome')}", "title": str(a.get("title") or ""), "text": str(a.get("title") or ""), "urls": [a.get("url", "")]}
            for a in acks.values()
            if a.get("outcome") in ("skipped", "duplicate") and a.get("url") and not str(a.get("reason", "")).startswith("major gave up")]


def find_missed(items: list[dict], records: list[dict], resolve_links: bool = True) -> tuple[list[dict], dict]:
    """大きなニュースで記事が無いもの（同じ話題はまとめる）と、件数の内訳"""
    prepared = prepare(records)
    stats = {"items": len(items), "candidates": 0, "covered": 0, "merged": 0}
    stories: list[dict] = []
    for item in sorted(items, key=lambda i: i["published"]):
        verdict = classify(item["title"])
        if not verdict["ok"]:
            continue
        stats["candidates"] += 1
        item = {**item, "entities": verdict["entities"]}
        hit = covered(item["title"], item["links"], prepared)
        if hit:
            stats["covered"] += 1
            continue
        mine = prepare([
            {"slug": f"#{n}", "title": s["title"], "head": " | ".join([s["title"]] + [a["title"] for a in s["also"]]),
             "text": " | ".join([s["title"]] + [a["title"] for a in s["also"]]), "urls": s["links"] + [u for a in s["also"] for u in a["links"]]}
            for n, s in enumerate(stories)
        ])
        same = covered(item["title"], item["links"], mine)
        if same:
            stories[int(same["slug"][1:])]["also"].append(item)
            stats["merged"] += 1
            continue
        stories.append({**item, "also": []})
    if resolve_links:
        kept = []
        for s in stories:
            # 一次情報はまとめた見出しすべての出典から探す（日本の報道に公式のリンクがあることが多い）
            s["primary"] = resolve({**s, "links": s["links"] + [u for a in s["also"] for u in a["links"]]})
            if s["primary"].get("url") and covered(s["title"], [s["primary"]["url"]], prepared):
                stats["covered"] += 1 + len(s["also"])  # 出典の URL で見ると記事あり
                continue
            kept.append(s)
        stories = kept
    return stories, stats


def markdown(stories: list[dict], stats: dict, errors: list[str], hours: int, now: datetime) -> str:
    lines = [f"### 見逃しチェック（{now.astimezone(JST):%Y-%m-%d %H:%M} JST・直近{hours}時間）", ""]
    lines.append(
        f"見た見出し {stats['items']} 件 → 大きなニュースの候補 {stats['candidates']} 件 → 記事あり {stats['covered']} 件 / "
        f"同じ話題 {stats['merged']} 件 → **記事が無い話題 {len(stories)} 件**"
    )
    lines.append("")
    for n, s in enumerate(stories, 1):
        p = s.get("primary") or {}
        when = s["published"].astimezone(JST).strftime("%m/%d %H:%M")
        lines.append(f"{n}. **{s['title'][:140]}**（{s['source']} {when}）")
        lines.append(f"   - 見つけた見出し: {s['url']}")
        if p.get("url"):
            kind = "一次情報" if not p.get("secondary") else "報道（一次情報が見つからない）"
            lines.append(f"   - 出典の候補（{kind}・{p['sourceName']}）: {p['url']}")
        else:
            lines.append("   - 出典の候補: 見つからず（公式発表・信頼できる報道のリンクなし）")
        for a in s["also"][:3]:
            lines.append(f"   - 同じ話題: {a['title'][:90]}（{a['source']}）")
    if errors:
        lines += ["", "取得できなかった情報源: " + " / ".join(e[:80] for e in errors)]
    return "\n".join(lines) + "\n"


def load_requests() -> dict:
    try:
        data = json.loads(REQUESTS_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("items", [])
    return data


def add_requests(entries: list[dict], now: datetime) -> int:
    """state/radar_requests.json に足す（Worker のレーダーが読んで速報レーンに回す）。古いものは消す"""
    data = load_requests()
    since = now - timedelta(hours=REQUEST_KEEP_HOURS)
    kept = []
    for e in data["items"]:
        try:
            if datetime.fromisoformat(e["requestedAt"]) >= since:
                kept.append(e)
        except (KeyError, ValueError):
            continue
    have = {norm_url(e["url"]) for e in kept}
    added = 0
    for e in entries:
        if norm_url(e["url"]) in have:
            continue
        kept.append({**e, "requestedAt": now.isoformat(timespec="seconds")})
        have.add(norm_url(e["url"]))
        added += 1
    REQUESTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REQUESTS_PATH.write_text(json.dumps({"items": kept}, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return added


def github(path: str, method: str = "GET", body: dict | None = None):
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        raise RuntimeError("GITHUB_TOKEN / GITHUB_REPOSITORY がありません")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}", method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "saisoku-radar",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response) if response.status != 204 else None


def open_issue(title: str, body: str) -> str:
    """同じ題の Issue が開いていればコメントを足す。無ければ立てる（ラベル missed-news。オーナーにメールが届く）"""
    issues = github("/issues?state=open&labels=missed-news&per_page=20") or []
    for issue in issues:
        if issue.get("title") == title:
            github(f"/issues/{issue['number']}/comments", "POST", {"body": body})
            return issue["html_url"]
    return github("/issues", "POST", {"title": title, "body": body, "labels": ["missed-news"]})["html_url"]


def missed(args) -> int:
    now = datetime.now(timezone.utc)
    items, errors = gather(args.hours, now)
    records = load_records(args.hours + 72, now)
    stories, stats = find_missed(items, records + judged_records())
    report = markdown(stories, stats, errors, args.hours, now)
    gaps: list[dict] = []
    if not args.no_gaps:
        try:
            detected, derrors = gather_detect(args.hours, now)
            gaps = competitor_gaps(detected, records)
            report += gaps_markdown(gaps, len(detected))
            if derrors:
                report += "\n取得できなかった他社の見出し: " + " / ".join(e[:60] for e in derrors) + "\n"
        except Exception as error:  # noqa: BLE001
            report += f"\n他社との突き合わせに失敗: {str(error)[:120]}\n"
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    if not stories and not gaps:
        return 0
    if args.request and stories:
        entries = [
            {"url": s["primary"]["url"], "title": s["title"][:200], "sourceName": s["primary"]["sourceName"],
             "secondary": s["primary"]["secondary"], "via": f"missed-check:{s['source']}", "found": s["url"]}
            for s in stories if (s.get("primary") or {}).get("url")
        ][: args.max_requests]
        added = add_requests(entries, now)
        print(f"[missed] 速報レーンへの依頼を {added} 件 state/radar_requests.json に足しました（Worker のレーダーが数分以内に回します）")
    if args.issue:
        title = f"【見逃しチェック】{now.astimezone(JST):%Y-%m-%d} 記事が無い大きなニュース {len(stories)} 件・他社だけの話題 {len(gaps)} 件"
        tail = ("\n\n---\n出典の候補があるものは、自動で速報レーンに回しています（state/radar_requests.json）。"
                "記事になったら、この Issue は閉じてください。判定のルール: scripts/fast_lane_rules.json の radar")
        try:
            print(f"[missed] Issue: {open_issue(title, report + (tail if args.request else ''))}")
        except Exception as error:  # noqa: BLE001
            print(f"::warning::Issue を立てられませんでした: {error}")
    return 0


# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("coverage")
    p_missed = sub.add_parser("missed")
    p_missed.add_argument("--hours", type=int, default=30)
    p_missed.add_argument("--issue", action="store_true")
    p_missed.add_argument("--request", action="store_true")
    p_missed.add_argument("--max-requests", type=int, default=5)
    p_missed.add_argument("--no-gaps", action="store_true", help="他社との突き合わせを省く")
    p_req = sub.add_parser("request")
    p_req.add_argument("url")
    p_req.add_argument("--title", default="")
    p_req.add_argument("--source-name", default="")
    sub.add_parser("classify")
    sub.add_parser("covered")
    args = parser.parse_args()

    if args.cmd == "coverage":
        return write_coverage()
    if args.cmd == "missed":
        return missed(args)
    if args.cmd == "request":
        o = outlet(args.url)
        entry = {"url": args.url, "title": args.title, "sourceName": args.source_name or (o or {}).get("name", ""),
                 "secondary": bool(o) and not official(args.url, []), "via": "manual"}
        added = add_requests([entry], datetime.now(timezone.utc))
        print(f"[request] {'足しました' if added else 'すでに依頼済みです'}: {args.url}（commit・push すると Worker が数分以内に回します）")
        return 0
    if args.cmd == "classify":
        json.dump([classify(i.get("title", "")) for i in json.load(sys.stdin)], sys.stdout, ensure_ascii=False)
        return 0
    if args.cmd == "covered":
        data = json.load(sys.stdin)
        records = prepare(data.get("records") or [])
        out = []
        for case in data.get("cases") or []:
            hit = covered(case["title"], case.get("urls") or [], records)
            out.append(hit["slug"] if hit else None)
        json.dump(out, sys.stdout, ensure_ascii=False)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
