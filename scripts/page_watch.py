#!/usr/bin/env python3
"""料金・プラン・リリースノートのページの変更を見張る（ChatGPT・Claude・Gemini・Copilot など）。

公式ブログに載らない発表（プランの改名・利用上限の変更・アプリの新機能）を取りこぼさないための網。
見張るページは scripts/sources_registry.json の watch_pages（一覧を1か所にまとめる）。

- mode "dated": 日付の見出しが新しい順に並ぶページ（リリースノート）。前回より新しい日付の項目が出たら候補にする
- mode "diff":  料金・プランのページ。本文の行の差分を取り、金額・プラン名・上限の行が変わり、
                 2回続けて同じ内容だったとき（A/B テストや一時的な表示の揺れを除く）だけ候補にする
- どちらも、意味のある変更か（significant_re。無ければ既定の語）を確かめる。項目が短いときは、項目の中の公式ページのリンクも読む
- 初めて見るページは基準だけ保存して候補にしない（デプロイ直後に古い項目を流さない）
- 料金の金額の変化は scripts/pricing_watch.py（毎日）が state/pricing_news.json に置く。ここで他の候補と一緒に渡す
- 状態は state/page_watch.json（auto_run.yml が変わったときだけコミットする）。記事にするかは web_articles.py が
  大きなニュースと同じ流れで判断する（同じ発表の記事があれば続報か見送り・品質ゲート・速報投稿はしない）

  python3 scripts/page_watch.py check --dry-run   # 取得して、候補になるものを表示（状態を書かない）
  python3 scripts/page_watch.py check             # 取得して状態を更新し、候補を表示
  python3 scripts/page_watch.py status            # 各ページの最後の確認・最新の項目
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feeds  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = Path(__file__).resolve().with_name("sources_registry.json")
STATE_PATH = ROOT / "state" / "page_watch.json"
JST = timezone(timedelta(hours=9))
MAX_PAGE = 3_000_000
SNAPSHOT_LINES = 600
PENDING_KEEP_HOURS = 48
MAX_NEW_PER_PAGE = 3
ENTRY_MIN_CHARS = 60
LINK_FETCH_BELOW = 1500
# 意味のある変更の既定（新モデル・プラン・料金・上限・提供開始・改名・終了）
DEFAULT_SIGNIFICANT = (
    r"(?<![A-Za-z])(?:Free|Plus|Pro|Max|Ultra|Team|Teams|Business|Enterprise|Premium|Edu)(?![a-z])|"
    r"\$\s?\d|¥\s?\d|\d\s?円|per month|/month|月額|price|pricing|料金|価格|"
    r"limit|usage|allowance|quota|上限|利用枠|"
    r"renam|now called|名称|改名|"
    r"launch|introduc|now available|rolling out|available (?:to|in|for)|generally available|一般提供|提供開始|"
    r"new model|model|モデル|deprecat|retir|sunset|discontinu|終了|"
    r"Japan|Japanese|日本"
)
PRICE_PLAN_RE = re.compile(
    r"\$\s?\d|¥\s?\d|\d\s?円|/\s?(?:mo|month)|per (?:user|month|seat)|月額|年額|"
    r"(?<![A-Za-z])(?:Free|Plus|Pro|Max|Ultra|Team|Teams|Business|Enterprise|Premium|Edu|Starter|Standard|Advanced)(?![a-z])|"
    r"limit|usage|messages|上限|回数|プラン|\d+\s?x\b|\d+\s?倍",
)
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_PATTERNS = [
    (re.compile(rf"^(?:#+\s*)?(?:\*\*)?\s*({MONTHS})\s+(\d{{1,2}}),\s*(20\d\d)\s*(?:\*\*)?\s*$"), "mdy"),
    (re.compile(r"^(?:#+\s*)?(20\d\d)[./-](\d{1,2})[./-](\d{1,2})\s*$"), "ymd"),
    (re.compile(r"^(?:#+\s*)?(20\d\d)年\s*(\d{1,2})月\s*(\d{1,2})日\s*$"), "ymd"),
]
MONTH_NO = {m: i + 1 for i, m in enumerate(MONTHS.split("|"))}
# 項目の中のリンクで読みにいく公式のホスト
OFFICIAL_HOSTS = (
    "anthropic.com", "claude.com", "claude.ai", "openai.com", "chatgpt.com", "blog.google", "gemini.google", "google.com",
    "googleblog.com", "deepmind.google", "microsoft.com", "github.blog", "github.com",
)
UA = "Mozilla/5.0 (compatible; SaisokuWatch/1.0; +https://ai-tech-express.jp)"


def log(message: str) -> None:
    print(message, flush=True)


def load_watches() -> list[dict]:
    try:
        data = json.loads(REGISTRY_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    return [w for w in data.get("watch_pages", []) if isinstance(w, dict) and w.get("id") and w.get("url") and not w.get("disabled")]


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("pages", {})
    data.setdefault("pending", [])
    return data


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if not STATE_PATH.exists() or STATE_PATH.read_text("utf-8") != text:
        STATE_PATH.write_text(text, "utf-8")


# --------------------------------------------------------------------------- #
# 取得と本文の取り出し（リンクを [文字](URL) で残す簡単なマークダウン）
# --------------------------------------------------------------------------- #


def html_to_md(raw: str, base: str, container: str = "") -> str:
    text = re.sub(r"(?is)<(script|style|noscript|svg|template|nav|footer|header|form|button|sup)\b[^>]*>.*?</\1>", " ", raw)
    for tag in ([container] if container else []) + ["main", "article"]:
        m = re.search(rf"(?is)<{tag}\b[^>]*>(.*)</{tag}>", text)
        if m and len(re.sub(r"<[^>]+>", "", m.group(1))) > 500:
            text = m.group(1)
            break

    def link(m: re.Match) -> str:
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        try:
            href = urllib.parse.urljoin(base, html.unescape(m.group(1)).strip())
        except ValueError:
            return label
        return f"[{label}]({href})" if label else ""

    text = re.sub(r"(?is)<a\b[^>]*?\bhref=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", link, text)
    text = re.sub(r"(?is)<h([1-4])\b[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", text)
    text = re.sub(r"(?is)</h[1-4]>|<br\s*/?>|</?(?:p|div|li|ul|ol|tr|table|section|dd|dt|blockquote)\b[^>]*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t　\xa0]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def fetch_page(watch: dict) -> str:
    """ページの本文（マークダウン）。fetch=jina は読み取りサービス経由（ボット除けのあるページ）"""
    url = watch["url"]
    if watch.get("fetch") == "jina":
        data, _ = feeds.fetch("https://r.jina.ai/" + url, accept="text/plain", token=os.environ.get("JINA_API_KEY", ""), timeout=60)
        body = feeds._decode(data[:MAX_PAGE])  # noqa: SLF001
        if "Markdown Content:" in body:
            body = body.split("Markdown Content:", 1)[1]
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
        return "\n".join(line.strip() for line in body.split("\n") if line.strip())
    data, final = feeds.fetch(url, accept="text/html,application/xhtml+xml,text/markdown;q=0.9,*/*;q=0.8")
    body = feeds._decode(data[:MAX_PAGE])  # noqa: SLF001
    if watch.get("format") == "md" or url.endswith(".md") or not re.search(r"(?i)<html|<body|<div", body[:5000]):
        if body.lstrip().startswith("{"):
            try:  # GitHub Docs の API（{"body": "..."}）など
                body = str(json.loads(body).get(watch.get("json_key", "body")) or "")
            except ValueError:
                pass
        return "\n".join(line.rstrip() for line in body.split("\n") if line.strip())
    return html_to_md(body, final or url, watch.get("container", ""))


def plain(md: str) -> str:
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
    return re.sub(r"[*_`]+", "", md)


def links_of(md: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\]\((https?://[^)\s]+)\)", md)))


def official_link(url: str, page_url: str) -> bool:
    host = urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
    if not host or url.split("#")[0].rstrip("/") == page_url.split("#")[0].rstrip("/"):
        return False
    if re.search(r"/(?:login|auth|signup|download|pricing)\b", url):
        return False
    return any(host == h or host.endswith("." + h) for h in OFFICIAL_HOSTS)


# --------------------------------------------------------------------------- #
# リリースノート（日付の見出し）
# --------------------------------------------------------------------------- #


def parse_date_line(line: str):
    s = line.strip()
    if len(s) > 48:
        return None
    for pattern, kind in DATE_PATTERNS:
        m = pattern.match(s)
        if not m:
            continue
        try:
            if kind == "mdy":
                return datetime(int(m.group(3)), MONTH_NO[m.group(1)], int(m.group(2))).date()
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            return None
    return None


def dated_entries(md: str) -> list[tuple[str, str]]:
    """[(YYYY-MM-DD, 項目のマークダウン)]。ページの順（新しい順）。同じ日付の見出しが続くときはまとめる"""
    out: list[tuple[str, list[str]]] = []
    for line in md.split("\n"):
        d = parse_date_line(line)
        if d:
            key = d.isoformat()
            if out and out[-1][0] == key:
                continue
            out.append((key, []))
        elif out:
            out[-1][1].append(line)
    return [(k, "\n".join(v).strip()) for k, v in out]


def entry_title(watch: dict, date: str, entry: str) -> str:
    """候補の見出し: 項目の最初の小見出し（無ければ最初の文）"""
    heads = [plain(m.group(1)).strip() for m in re.finditer(r"^#{1,4}\s*(.+)$", entry, re.M)]
    first = heads[0] if heads else plain(entry).strip().split("\n")[0]
    first = re.split(r"(?<=[.!?。])\s", first)[0][:140]
    more = f" ほか{len(heads) - 1}件" if len(heads) > 1 else ""
    return f"{watch.get('product_name') or watch['name']}: {first}{more}"


def is_significant(watch: dict, text: str) -> bool:
    return bool(re.search(watch.get("significant_re") or DEFAULT_SIGNIFICANT, plain(text), re.I))


# --------------------------------------------------------------------------- #
# 料金・プランのページ（行の差分）
# --------------------------------------------------------------------------- #


def snapshot_lines(md: str) -> list[str]:
    lines = []
    for line in plain(md).split("\n"):
        line = re.sub(r"\s+", " ", re.sub(r"^\s*(?:#+|[-*]|\d+\.)\s+", "", line)).strip()
        if len(line) < 3 or len(line) > 400 or not re.search(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff]", line):
            continue
        lines.append(line)
    return list(dict.fromkeys(lines))[:SNAPSHOT_LINES]


def digest(lines: list[str]) -> str:
    """並び順は見ない（同じ行の入れ替わり・注番号の付け直しで変化扱いにしない）"""
    return hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()[:16]


def meaningful_diff(old: list[str], new: list[str]) -> tuple[list[str], list[str]]:
    """金額・プラン名・上限を含む行のうち、消えた行と増えた行"""
    olds, news = set(old), set(new)
    removed = [x for x in old if x not in news and PRICE_PLAN_RE.search(x)]
    added = [x for x in new if x not in olds and PRICE_PLAN_RE.search(x)]
    return removed, added


# --------------------------------------------------------------------------- #
# 1回分の確認
# --------------------------------------------------------------------------- #


def _item(watch: dict, key: str, title: str, content: str, published: datetime | None, now: datetime, url: str) -> dict:
    return {
        "id": hashlib.sha1(f"{watch['url']}|{key}".encode("utf-8")).hexdigest()[:16],
        "url": url,
        "title": title[:200],
        "summary": plain(content)[:600],
        "content": content[: feeds.TEXT_CAP],
        "published_at": (published or now).isoformat(),
        "published_guessed": published is None,
        "source_name": watch["name"],
        "source_slug": f"watch-{watch['id']}",
        "lang": watch.get("lang", "en"),
        "entity": watch.get("entity", ""),
        "detectedAt": now.isoformat(timespec="seconds"),
    }


def _with_links(watch: dict, entry_md: str) -> str:
    """項目が短いときは、項目の中の公式ページ（ヘルプ記事・発表）を最大2本読んで足す"""
    text = plain(entry_md).strip()
    if len(text) >= LINK_FETCH_BELOW:
        return text
    parts = [text]
    for link in [u for u in links_of(entry_md) if official_link(u, watch["url"])][:2]:
        try:
            page = feeds.fetch_article_text(link)
        except Exception:  # noqa: BLE001
            continue
        body = (page.get("text") or "").strip()
        if len(body) >= 200:
            parts.append(f"【項目のリンク先の公式ページ: {page.get('title') or link}（{link}）】\n{body[:5000]}")
    return "\n\n".join(parts)


def check_one(watch: dict, page: dict, now: datetime, md: str) -> list[dict]:
    """1ページ分。page は状態（書き換える）。候補のリスト"""
    out: list[dict] = []
    if watch.get("mode", "dated") == "dated":
        entries = [(d, e) for d, e in dated_entries(md) if len(plain(e)) >= ENTRY_MIN_CHARS or d]
        if not entries:
            page["error"] = "日付の見出しが見つからない（ページの形が変わった？）"
            return out
        newest = max(d for d, _ in entries[:10])
        last = page.get("last_date")
        page["last_date"] = max(newest, last or newest)
        page.pop("error", None)
        if not last:
            log(f"[watch] {watch['id']}: 基準を保存（最新 {newest}）")
            return out
        fresh = [(d, e) for d, e in entries[:20] if d > last][:MAX_NEW_PER_PAGE]
        for d, e in fresh:
            title = entry_title(watch, d, e)
            if len(plain(e)) < ENTRY_MIN_CHARS or not is_significant(watch, title + "\n" + e):
                log(f"[watch] {watch['id']}: {d} の項目は記事にする変更ではない: {title[:80]}")
                page.setdefault("minor", []).append({"date": d, "title": title[:120]})
                page["minor"] = page["minor"][-10:]
                continue
            day = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
            # 日付だけの見出し: 今日・昨日（米国時間のずれを含む）なら見つけた時刻を発表の時刻に使う（速さのラベルは出さない）
            published = None if now - day <= timedelta(hours=48) else day
            content = _with_links(watch, e)
            header = f"【{watch['name']}の {d} の項目】（{watch['url']}）\n"
            out.append(_item(watch, f"entry-{d}", title, header + content, published, now, f"{watch['url'].split('#')[0]}#entry-{d}"))
        return out

    # diff
    lines = snapshot_lines(md)
    if len(lines) < 10:
        page["error"] = f"本文が短い（{len(lines)}行）"
        return out
    h = digest(lines)
    page.pop("error", None)
    if not page.get("hash"):
        page.update({"hash": h, "lines": lines, "since": now.isoformat(timespec="seconds")})
        log(f"[watch] {watch['id']}: 基準を保存（{len(lines)}行）")
        return out
    if h == page["hash"]:
        page.pop("pending", None)
        return out
    removed, added = meaningful_diff(page.get("lines") or [], lines)
    if not (removed or added):
        # 金額・プランに関係ない変更（文言・並び）は基準だけ更新
        page.update({"hash": h, "lines": lines})
        page.pop("pending", None)
        return out
    pend = page.get("pending") or {}
    if pend.get("hash") != h:
        page["pending"] = {"hash": h, "at": now.isoformat(timespec="seconds")}
        log(f"[watch] {watch['id']}: 料金・プランの行が変わった（次の確認で同じなら候補にする）: -{len(removed)} +{len(added)}")
        return out
    prev_since = page.get("since", "")
    page.update({"hash": h, "lines": lines, "since": now.isoformat(timespec="seconds")})
    page.pop("pending", None)
    change = "\n".join([f"- 削除: {x}" for x in removed[:20]] + [f"- 追加: {x}" for x in added[:20]])
    if not is_significant(watch, change):
        return out
    summary = " / ".join(added[:3]) or " / ".join(removed[:3])
    title = f"{watch.get('product_name') or watch['name']}が更新: {summary}"
    content = (
        f"【{watch['name']}（{watch['url']}）の変更点。前回の確認（{prev_since}）から {now.isoformat(timespec='minutes')} までの差分】\n"
        f"{change}\n\n【現在のページの内容】\n" + "\n".join(lines)[:8000]
    )
    key = f"entry-{now.astimezone(JST):%Y-%m-%d}-{h[:6]}"
    out.append(_item(watch, key, title, content, None, now, f"{watch['url'].split('#')[0]}#{key}"))
    return out


def check(dry_run: bool = False, now: datetime | None = None, fetcher=fetch_page) -> list[dict]:
    """全ページを確認して、新しい候補（と、まだ記事にしていない前回までの候補）を返す"""
    now = now or datetime.now(timezone.utc)
    watches = load_watches()
    state = load_state()

    def run(w):
        try:
            return w, fetcher(w), ""
        except Exception as error:  # noqa: BLE001
            return w, "", f"{type(error).__name__}: {str(error)[:100]}"

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(run, watches))
    found: list[dict] = []
    for w, md, error in results:
        page = state["pages"].setdefault(w["id"], {})
        if error:
            page["error"] = error
            log(f"[watch] {w['id']}: 取得できない（{error}）")
            continue
        try:
            items = check_one(w, page, now, md)
        except Exception as error:  # noqa: BLE001
            page["error"] = f"解析に失敗: {str(error)[:100]}"
            continue
        page["ok"] = now.astimezone(JST).strftime("%Y-%m-%d")
        for it in items:
            log(f"[watch] 候補: {it['title'][:100]}（{it['url']}）")
        found += items
    found += pricing_news(now)
    have = {p["id"] for p in state["pending"]}
    state["pending"] += [it for it in found if it["id"] not in have]
    since = now - timedelta(hours=PENDING_KEEP_HOURS)
    state["pending"] = [p for p in state["pending"] if datetime.fromisoformat(p["detectedAt"]) >= since][-20:]
    if not dry_run:
        save_state(state)
    return [dict(p) for p in state["pending"]] if not dry_run else found


def pricing_news(now: datetime) -> list[dict]:
    """scripts/pricing_watch.py が公式の料金ページで見つけた「料金が変わった」候補（同じ形）。
    state の隣の pricing_news.json（料金の見張りは別のワークフローで1日1回動く）"""
    try:
        data = json.loads(STATE_PATH.with_name("pricing_news.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    since = now - timedelta(hours=PENDING_KEEP_HOURS)
    out = []
    for p in data.get("pending", []) if isinstance(data, dict) else []:
        try:
            if p.get("id") and datetime.fromisoformat(p["detectedAt"]) >= since:
                out.append({k: v for k, v in p.items() if k != "pricingChanges"})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def candidates(processed: dict, now: datetime | None = None, max_items: int = 2, dry_run: bool = False) -> list[dict]:
    """web_articles.py 用: 確認して、まだ処理していない候補（feeds.parse_feed と同じ形）。
    本文は項目（とリンク先）だけを渡す（_page。ページ全体を読み直して別の項目を混ぜない）"""
    items = []
    for p in check(dry_run=dry_run, now=now):
        if p["id"] in processed:
            continue
        it = dict(p)
        it["published_at"] = datetime.fromisoformat(it["published_at"])
        it["detected_at"] = datetime.fromisoformat(it.pop("detectedAt"))
        it.update({"watch": True, "priority": 1, "image": None,
                   "_page": {"text": it["content"], "image": None, "title": "", "description": "", "published": ""}})
        items.append(it)
    return items[:max_items]


def status() -> int:
    state = load_state()
    for w in load_watches():
        p = state["pages"].get(w["id"], {})
        latest = p.get("last_date") or (f"{len(p.get('lines') or [])}行 / {p.get('since', '')}" if p.get("hash") else "未確認")
        log(f"- {w['id']}（{w.get('mode', 'dated')}）: 最終確認 {p.get('ok', '-')} / 最新 {latest}{' / エラー ' + p['error'] if p.get('error') else ''}")
    log(f"未処理の候補 {len(state['pending'])} 件")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    p_check = sub.add_parser("check")
    p_check.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    args = parser.parse_args()
    if args.cmd == "check":
        items = check(dry_run=args.dry_run)
        log(f"[watch] 候補 {len(items)} 件")
        return 0
    if args.cmd == "status":
        return status()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
