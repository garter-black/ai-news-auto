#!/usr/bin/env python3
"""元記事の中のリンクを1〜2本だけ追いかけて、確かな事実の束（scripts/facts_bundle.py）に足す。

- 追いかけるのは、元記事と同じ公式のドメイン（例: support.claude.com → claude.com・anthropic.com）のページだけ。
  お金・税・制度の記事では、財務省・国税庁・日銀・自治体など公的なページ（go.jp・lg.jp・boj.or.jp）も追いかける。
- 料金ページ・ヘルプ・資料（PDF）・詳しい案内を優先し、ログイン・規約・SNS・採用などは追わない。
- 速さを守る: 取りに行くのは最大2本、1本8秒（PDF は読み取りサービス経由で15秒）まで。全体でも20秒まで。
- 長いページ・PDF は、見出しと記事の言葉が重なる部分だけを抜き出す（1本1,000字まで）。

  python3 scripts/source_links.py URL     # その記事から追いかけるリンクと抜き出しを表示（試し）
"""

from __future__ import annotations

import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feeds  # noqa: E402

JST = timezone(timedelta(hours=9))
MAX_FETCHES = 2
FETCH_TIMEOUT = 8
PDF_TIMEOUT = 15
TOTAL_BUDGET = 20
EXCERPT_CAP = 1000
MIN_SCORE = 4

# 同じ会社の公式ドメイン（どれかが元記事のドメインなら、ほかも「同じ公式」とみなす）
OFFICIAL_GROUPS = [
    {"anthropic.com", "claude.com", "claude.ai"},
    {"openai.com", "chatgpt.com"},
    {"google.com", "blog.google", "googleblog.com", "gemini.google", "ai.google.dev", "deepmind.google", "google"},
    {"microsoft.com", "windows.com", "github.blog", "github.com"},
    {"apple.com"},
    {"meta.com", "fb.com", "facebook.com"},
    {"rakuten.co.jp", "rakuten-sec.co.jp"},
]
# 公的な一次資料（お金・税・制度の記事で追いかける）
PUBLIC_RE = re.compile(r"(?:^|\.)(?:go\.jp|lg\.jp|boj\.or\.jp|e-gov\.go\.jp|jpx\.co\.jp|fsa\.go\.jp)$")
# 出典の表示名（ドメイン → 名前）。無ければ元記事の発信元の名前
SITE_NAMES = {
    "claude.com": "Anthropic公式", "anthropic.com": "Anthropic公式", "support.claude.com": "Anthropic公式ヘルプ",
    "openai.com": "OpenAI公式", "help.openai.com": "OpenAI公式ヘルプ", "chatgpt.com": "OpenAI公式",
    "blog.google": "Google公式ブログ", "support.google.com": "Google公式ヘルプ", "ai.google.dev": "Google公式",
    "gemini.google": "Google公式", "microsoft.com": "Microsoft公式", "support.microsoft.com": "Microsoft公式サポート",
    "mof.go.jp": "財務省", "nta.go.jp": "国税庁", "boj.or.jp": "日本銀行", "fsa.go.jp": "金融庁", "mhlw.go.jp": "厚生労働省",
    "cao.go.jp": "内閣府", "soumu.go.jp": "総務省", "meti.go.jp": "経済産業省", "digital.go.jp": "デジタル庁",
    "metro.tokyo.lg.jp": "東京都", "kantei.go.jp": "首相官邸", "rakuten-sec.co.jp": "楽天証券",
}
SKIP_RE = re.compile(
    r"log-?in|sign-?in|sign-?up|register|account|privacy|cookie|careers?|jobs?|recruit|contact|newsletter|subscribe|"
    r"twitter\.com|x\.com|facebook\.com/(?!.*news)|linkedin|instagram|youtube|tiktok|/tag/|/tags/|/category/|/author/|/search|"
    r"/feed|rss|\.(?:jpg|jpeg|png|gif|webp|svg|zip|mp4|mp3|xlsx?|csv|docx?)(?:$|\?)|お問い合わせ|ログイン|採用|プライバシー|個人情報",
    re.I,
)
LINK_TEXT_SKIP_RE = re.compile(r"^(?:here|こちら|詳細|more|read more|もっと見る|top|home|ホーム|一覧|戻る|次へ|前へ)$|トップへ|ページの先頭|サイトマップ", re.I)
PRICE_RE = re.compile(r"pric|plans?\b|料金|価格|プラン|費用|fee|cost|billing|課金|手数料", re.I)
HELP_RE = re.compile(r"help|support|faq|/articles?/|docs?\b|ヘルプ|よくある|使い方|guide|limits?\b|上限|制限", re.I)
DOC_RE = re.compile(r"\.pdf(?:$|\?)|資料|別紙|概要|詳細|details|announce|発表|お知らせ|notice|リリース|release", re.I)
MONEY_DOC_RE = re.compile(r"税|控除|手続|制度|給付|補助|支援金|リーフレット|パンフレット|Q&A|ＱＡ|よくある質問|申請|申告|改正")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9.+-]{2,}|[ァ-ヶー]{2,}|[一-龠々]{2,}")
STOP_WORDS = {"the", "and", "for", "with", "what", "your", "plan", "plans", "about", "new", "how", "from", "this", "that", "are", "you"}


def host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower().split(":")[0].removeprefix("www.")


def base_domain(h: str) -> str:
    """support.claude.com → claude.com、corp.rakuten.co.jp → rakuten.co.jp、www.mof.go.jp → mof.go.jp"""
    parts = h.split(".")
    if len(parts) >= 3 and parts[-1] == "jp" and parts[-2] in ("co", "or", "ne", "go", "lg", "ac", "ed"):
        return ".".join(parts[-3:])
    if len(parts) >= 4 and parts[-1] == "jp" and parts[-2] == "lg":
        return ".".join(parts[-4:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def official_domains(source_url: str) -> set[str]:
    base = base_domain(host(source_url))
    for group in OFFICIAL_GROUPS:
        if base in group:
            return set(group)
    return {base}


def is_public(url: str) -> bool:
    return bool(PUBLIC_RE.search(host(url)))


def site_name(url: str, fallback: str = "") -> str:
    h = host(url)
    for key in (h, base_domain(h)):
        if key in SITE_NAMES:
            return SITE_NAMES[key]
    return fallback or base_domain(h)


def _words(text: str) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(text or "") if w.lower() not in STOP_WORDS}


def _norm(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}"


def score_link(link: dict, source_url: str, title_words: set[str], money: bool) -> int:
    url, text = link.get("url") or "", link.get("text") or ""
    if not url.startswith("http") or _norm(url) == _norm(source_url) or SKIP_RE.search(url) or SKIP_RE.search(text):
        return -1
    path = urllib.parse.urlsplit(url).path
    if re.fullmatch(r"/?(?:index\.\w+)?", path) or LINK_TEXT_SKIP_RE.match(text.strip()) and not DOC_RE.search(url):
        return -1
    # 一覧ページ（/news/press/ のような浅いフォルダ）は追わない
    if path.endswith("/") and len([p for p in path.split("/") if p]) <= 2 and not PRICE_RE.search(path):
        return -1
    domain_ok = base_domain(host(url)) in official_domains(source_url)
    public = money and is_public(url)
    if not (domain_ok or public):
        return -1
    both = f"{text} {urllib.parse.unquote(url)}"
    overlap = len(title_words & _words(both))
    pdf = bool(re.search(r"\.pdf(?:$|\?)", url, re.I))
    strong = bool(PRICE_RE.search(both) or HELP_RE.search(both) or pdf)
    # 説明文つきのカード（「人気記事」「関連サービス」の囲み）と、元記事と同じフォルダの別の記事は追わない（料金・ヘルプ・PDF は別）
    if len(text) > 70 and not (pdf or public):
        return -1
    src_parts = urllib.parse.urlsplit(source_url)
    if host(url) == host(source_url) and path.rsplit("/", 1)[0] == src_parts.path.rsplit("/", 1)[0] and not strong:
        return -1
    # 中身の手がかり（題名の言葉・料金・ヘルプ・PDF・制度の資料）が1つも無いリンク（関連サービスの宣伝・別の記事）は追わない
    if not (overlap or PRICE_RE.search(both) or HELP_RE.search(both) or pdf or (money and MONEY_DOC_RE.search(text))):
        return -1
    score = 0
    score += 5 if PRICE_RE.search(both) else 0
    score += 3 if HELP_RE.search(both) else 0
    score += 3 if DOC_RE.search(both) else 0
    score += 2 * min(3, overlap)
    score += 2 if link.get("main") else 0
    if money:
        score += 4 if public else 0
        score += 3 if MONEY_DOC_RE.search(text) else 0
    return score


def pick(links: list[dict], source_url: str, title: str, money: bool = False, limit: int = MAX_FETCHES) -> list[dict]:
    """追いかけるリンク（点数の高い順・最大 limit 本）"""
    words = _words(title)
    scored = []
    for i, link in enumerate(links or []):
        s = score_link(link, source_url, words, money)
        if s >= MIN_SCORE:
            scored.append((s, -i, link))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [{**link, "score": s} for s, _, link in scored[:limit]]


def excerpt(text: str, title: str, keywords: set[str] | None = None, cap: int = EXCERPT_CAP) -> str:
    """長いページ・PDF から、記事の言葉（題名・キーワード）と重なる行と、その見出しの下の行を抜き出す（元の順番のまま）"""
    lines = [ln.strip() for ln in (text or "").split("\n") if len(ln.strip()) >= 4]
    if sum(len(ln) for ln in lines) <= cap:
        return "\n".join(lines)
    words = _words(title) | (keywords or set())
    scores = []
    for i, ln in enumerate(lines):
        s = 2 * len(words & _words(ln))
        s += 2 if re.search(r"\d", ln) else 0
        s += 2 if PRICE_RE.search(ln) or MONEY_DOC_RE.search(ln) else 0
        if len(ln) <= 40 and s >= 2 and i + 1 < len(lines):  # 見出しらしい短い行は次の行にも点を分ける
            scores.append((s, i))
            scores.append((s - 1, i + 1))
            continue
        scores.append((s, i))
    best: dict[int, int] = {}
    for s, i in scores:
        best[i] = max(best.get(i, -1), s)
    chosen, total = set(), 0
    for i, s in sorted(best.items(), key=lambda x: (-x[1], x[0])):
        if s <= 0 or total + len(lines[i]) > cap:
            continue
        chosen.add(i)
        total += len(lines[i]) + 1
    return "\n".join(lines[i] for i in sorted(chosen))


def _fetch_page(url: str, timeout: int) -> dict:
    """1ページ取る（HTML は直接。PDF は読み取りサービス経由）"""
    if re.search(r"\.pdf(?:$|\?)", url, re.I):
        data, _ = feeds.fetch_jina(url, timeout=PDF_TIMEOUT)  # 鍵が残高切れなら鍵なしで読む（feeds.py）
        body = feeds._decode(data)
        m = re.search(r"^Title:\s*(.+)$", body, re.M)
        title = m.group(1) if m else ""
        if "Markdown Content:" in body:
            body = body.split("Markdown Content:", 1)[1]
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
        body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)
        return {"text": feeds._collapse([body])[:60000], "title": title.strip(), "pdf": True}
    data, final_url = feeds.fetch(url, timeout=timeout, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")
    if data[:5] == b"%PDF-":
        raise ValueError("PDF（読み取りサービスで取り直さない。取得は最大2本まで）")
    parser = feeds._Extractor()
    try:
        parser.feed(feeds._decode(data))
        parser.close()
    except Exception:  # noqa: BLE001
        pass
    candidates = [feeds._collapse(parser.buffers[k]) for k in ("article", "main", "body")]
    text = next((c for c in candidates if len(c) >= 300), max(candidates, key=len))
    return {"text": text[:60000], "title": parser.meta.get("og:title", ""), "final_url": final_url}


def follow(page: dict | None, source_url: str, title: str, source_name: str = "", money: bool = False,
           keywords: set[str] | None = None, fetcher=None, now: datetime | None = None, log=None,
           max_fetches: int = MAX_FETCHES) -> list[dict]:
    """元記事のリンクを最大2本取りに行き、抜き出しを返す。
    戻り値: [{"url", "name", "title", "excerpt", "checkedAt", "public"}]。失敗しても例外にしない（取れたものだけ）"""
    links = (page or {}).get("links") or []
    targets = pick(links, (page or {}).get("final_url") or source_url, title, money, limit=min(max_fetches, MAX_FETCHES))
    fetcher = fetcher or _fetch_page
    started = time.time()
    today = (now or datetime.now(JST)).astimezone(JST).strftime("%Y-%m-%d")
    out = []
    for link in targets:
        if time.time() - started > TOTAL_BUDGET:
            break
        try:
            got = fetcher(link["url"], FETCH_TIMEOUT)
        except Exception as error:  # noqa: BLE001 - リンク先が取れなくても記事は作る
            if log:
                log(f"   [links] 取得に失敗: {link['url'][:80]}（{str(error)[:60]}）")
            continue
        text = excerpt(got.get("text") or "", f"{title} {link.get('text', '')}", keywords)
        if len(text) < 80:
            continue
        out.append({
            "url": link["url"],
            "name": site_name(link["url"], source_name),
            "title": (got.get("title") or link.get("text") or "").strip()[:80],
            "excerpt": text,
            "checkedAt": today,
            "public": is_public(link["url"]),
        })
        if log:
            log(f"   [links] {link['url'][:90]}（{len(text)}字を抜き出し・点数{link['score']}）")
    return out


def main() -> int:
    url = sys.argv[1]
    page = feeds.fetch_article_text(url)
    money = "--money" in sys.argv
    for link in pick(page.get("links") or [], page.get("final_url") or url, page.get("title") or "", money, limit=5):
        print(link["score"], link["url"], link["text"][:60])
    for got in follow(page, url, page.get("title") or "", money=money, log=print):
        print(f"\n## {got['name']} {got['url']}\n{got['excerpt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
