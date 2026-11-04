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
    {"global.toyota", "toyota.jp", "toyota.co.jp", "toyota-global.com"},
    {"global.honda", "honda.co.jp", "honda.com"},
    {"nissan-global.com", "nissan.co.jp"},
    {"samsung.com", "news.samsung.com"},
    {"sony.com", "sony.co.jp", "sony.jp"},
]
# 会社が開示資料（適時開示・IR の PDF）の置き場に指定している IR の配信サービス（2026-11-04 オーナー承認・台帳 A44）。
# 公式のページから張られているときだけ「同じ会社の公式」とみなす（例: ゆうちょ銀行の IR ニュース → ssl4.eir-parts.net の TDnet の PDF）
IR_HOST_RE = re.compile(r"(?:^|\.)(?:eir-parts\.net|release\.tdnet\.info|xj-storage\.jp|irpocket\.com|magicalir\.net)$")
# IR の一覧ページへのリンクの手がかり（同じ公式ドメインのページだけ）
IR_PAGE_RE = re.compile(r"/ir(?:/|$|-)|investor|IRニュース|IR情報|投資家|株主・投資家|適時開示|ニュースリリース（IR）", re.I)
# IR の資料を探す話題（株式・提携・決算・配当など。適時開示に出る種類）
IR_TOPIC_RE = re.compile(
    r"株式|株主|議決権|出資|資本(?:業務)?提携|業務提携|買収|子会社|持分法|公開買付|(?<![A-Za-z])TOB(?![A-Za-z])|経営統合|合併|"
    r"決算|業績|上方修正|下方修正|配当|自己株式|自社株|増資|社債|業績予想|特別(?:利益|損失)|減損"
)
IR_NEWS_TYPES = ("earnings", "deal", "filing")
TDNET_LIST = "https://www.release.tdnet.info/inbs/I_list_{page:03d}_{day}.html"
TDNET_PAGES = 5
IR_BUDGET = 18
# 開示資料は数字（株数・割合・日程）が表や後半にあるので、長めに抜き出す
IR_EXCERPT_CAP = 6000
# ニュースの種類ごとに、深く読むべきリンクの手がかり（scripts/news_types.py。2026-11-03 オーナー決定「元の資料を要約ページの先まで読む」）
TYPE_HINT_RE = {
    "earnings": re.compile(r"financial|statement|results|earnings|investor|/ir/|10-q|10-k|8-k|data ?sheet|supplement|presentation|業績|決算|財務|短信|説明資料|連結", re.I),
    "filing": re.compile(r"prospectus|s-1|f-1|amend|risk|financial|目論見|届出|財務", re.I),
    "security": re.compile(r"advisor|cve-|nvd|jvn|security|patch|release ?notes|changelog|bulletin|修正|脆弱性|アップデート|更新", re.I),
    "product": re.compile(r"spec|仕様|諸元|価格|price|grade|グレード|lineup|ラインアップ|製品|product|詳細|オプション|装備|商品サイト|商品情報|特設サイト|ブランドサイト", re.I),
    "policy": re.compile(r"概要|資料|q&a|ＱＡ|要綱|要領|手引|申請|対象|リーフレット|パンフレット|公募|募集", re.I),
    "research": re.compile(r"report|survey|調査|結果|報告書|概要|data|statistics|統計", re.I),
    "pricing": re.compile(r"pric|plans?\b|料金|価格|プラン|limits?|上限", re.I),
    "deal": re.compile(r"press|release|investor|発表|お知らせ|合意|契約", re.I),
}
# 種類によっては、公式の発表元のほかに公的な一次資料も追う（脆弱性の公的データベース・米国の開示書類）
TYPE_PUBLIC_RE = {
    "security": re.compile(r"(?:^|\.)(?:nvd\.nist\.gov|jvn\.jp|cve\.org|cve\.mitre\.org|jpcert\.or\.jp|cisa\.gov)$"),
    "filing": re.compile(r"(?:^|\.)sec\.gov$"),
}
# 深く読む種類は、抜き出しを長めにとる（1本あたりの字数）
TYPE_EXCERPT_CAP = {"earnings": 3000, "filing": 3000, "security": 2000, "product": 2000, "policy": 2000, "research": 2000,
                    "pricing": 1800, "deal": 1500}
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
    r"twitter\.com|x\.com|facebook\.com/(?!.*news)|linkedin|instagram|youtube|tiktok|(?<!/releases)/tag/|/tags/|/category/|/author/|/search|"
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


def score_link(link: dict, source_url: str, title_words: set[str], money: bool, news_type: str = "") -> int:
    url, text = link.get("url") or "", link.get("text") or ""
    if not url.startswith("http") or _norm(url) == _norm(source_url) or SKIP_RE.search(url) or SKIP_RE.search(text):
        return -1
    path = urllib.parse.urlsplit(url).path
    if re.fullmatch(r"/?(?:index\.\w+)?", path) or LINK_TEXT_SKIP_RE.match(text.strip()) and not DOC_RE.search(url):
        return -1
    # 一覧ページ（/news/press/ のような浅いフォルダ）は追わない。ただし製品の発表で「商品サイト」と張られた製品のページ（toyota.jp/crownestate/）は追う
    product_site = news_type == "product" and bool(re.search(r"商品サイト|製品サイト|特設サイト|ブランドサイト|商品情報|製品情報", text))
    if path.endswith("/") and len([p for p in path.split("/") if p]) <= 2 and not PRICE_RE.search(path) and not product_site:
        return -1
    domain_ok = base_domain(host(url)) in official_domains(source_url) or bool(IR_HOST_RE.search(host(url)))
    public = (money and is_public(url)) or bool(news_type in TYPE_PUBLIC_RE and TYPE_PUBLIC_RE[news_type].search(host(url)))
    both = f"{text} {urllib.parse.unquote(url)}"
    overlap = len(title_words & _words(both))
    type_hint = bool(news_type in TYPE_HINT_RE and TYPE_HINT_RE[news_type].search(both))
    if news_type == "security":
        cve = bool(re.search(r"CVE-\d{4}-\d+", both, re.I))
        # 注意喚起（JPCERT など）から、製品の開発元の修正版・告知（GitHub のリリースなど）へ。製品名か CVE 番号が合うときだけ
        h = host(url)
        vendor = h == "github.com" or any(w in h for w in title_words if len(w) >= 4)
        if not (domain_ok or public) and (overlap or cve) and vendor:
            public = True
        # 「security」というフォルダ名だけが合う、関係ない同じサイトのページ（ランサムウェアの FAQ など）は追わない
        if type_hint and not (overlap or cve):
            type_hint = False
    if not (domain_ok or public):
        return -1
    pdf = bool(re.search(r"\.pdf(?:$|\?)", url, re.I))
    strong = bool(PRICE_RE.search(both) or HELP_RE.search(both) or pdf or type_hint)
    # 説明文つきのカード（「人気記事」「関連サービス」の囲み）と、元記事と同じフォルダの別の記事は追わない（料金・ヘルプ・PDF は別）
    if len(text) > 70 and not (pdf or public):
        return -1
    src_parts = urllib.parse.urlsplit(source_url)
    if host(url) == host(source_url) and path.rsplit("/", 1)[0] == src_parts.path.rsplit("/", 1)[0] and not strong:
        return -1
    # 中身の手がかり（題名の言葉・料金・ヘルプ・PDF・制度の資料）が1つも無いリンク（関連サービスの宣伝・別の記事）は追わない
    if not (overlap or PRICE_RE.search(both) or HELP_RE.search(both) or pdf or type_hint or (money and MONEY_DOC_RE.search(text))):
        return -1
    score = 0
    score += 5 if PRICE_RE.search(both) else 0
    score += 3 if HELP_RE.search(both) else 0
    score += 3 if DOC_RE.search(both) else 0
    score += 2 * min(3, overlap)
    score += 2 if link.get("main") else 0
    score += 4 if type_hint else 0
    if money:
        score += 4 if public else 0
        score += 3 if MONEY_DOC_RE.search(text) else 0
    return score


def pick(links: list[dict], source_url: str, title: str, money: bool = False, limit: int = MAX_FETCHES,
         news_type: str = "") -> list[dict]:
    """追いかけるリンク（点数の高い順・最大 limit 本）"""
    words = _words(title)
    scored = []
    for i, link in enumerate(links or []):
        s = score_link(link, source_url, words, money, news_type)
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
        s += 6 if re.search(r"in millions|in thousands|百万円|百万ドル|単位[:：]", ln, re.I) else 0  # 表の単位の断り書きは必ず残す
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


def pdf_text(data: bytes) -> str:
    """PDF の文字を取り出す（読み取りサービスが使えないときの予備。pypdf が無ければ空）"""
    try:
        import io  # noqa: PLC0415
        from pypdf import PdfReader  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:30])
    except Exception:  # noqa: BLE001
        return ""


def _fetch_pdf(url: str) -> dict:
    """PDF を読む（読み取りサービス → 直接取って pypdf の順）。どちらも文字が取れなければ例外（読めなかった資料として残す）"""
    try:
        data, _ = feeds.fetch_jina(url, timeout=PDF_TIMEOUT)  # 鍵が残高切れなら鍵なしで読む（feeds.py）
        body = feeds._decode(data)
    except Exception:  # noqa: BLE001
        body = ""
    if len(re.sub(r"\s+", "", body.split("Markdown Content:", 1)[-1])) < 200:
        raw, _ = feeds.fetch(url, timeout=PDF_TIMEOUT, accept="application/pdf,*/*;q=0.8")
        text = pdf_text(raw) if raw[:5] == b"%PDF-" else ""
        if len(re.sub(r"\s+", "", text)) < 200:
            raise ValueError("PDF の文字を取り出せない（画像だけの PDF か、読み取りに失敗）")
        return {"text": feeds._collapse([text])[:60000], "title": "", "pdf": True}
    m = re.search(r"^Title:\s*(.+)$", body, re.M)
    title = m.group(1) if m else ""
    if "Markdown Content:" in body:
        body = body.split("Markdown Content:", 1)[1]
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)
    return {"text": feeds._collapse([body])[:60000], "title": title.strip(), "pdf": True}


def _fetch_page(url: str, timeout: int) -> dict:
    """1ページ取る（HTML は直接。PDF は読み取りサービス経由、だめなら直接取って文字を取り出す）"""
    if re.search(r"\.pdf(?:$|\?)", url, re.I) or IR_HOST_RE.search(host(url)) and "/doc/" in url:
        return _fetch_pdf(url)
    data, final_url = feeds.fetch(url, timeout=timeout, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")
    if data[:5] == b"%PDF-":
        text = pdf_text(data)  # 拡張子の無い PDF（IR の配信サービスなど）は、その場で文字を取り出す
        if len(re.sub(r"\s+", "", text)) < 200:
            raise ValueError("PDF の文字を取り出せない（画像だけの PDF か、読み取りに失敗）")
        return {"text": feeds._collapse([text])[:60000], "title": "", "pdf": True, "final_url": final_url}
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
           max_fetches: int = MAX_FETCHES, news_type: str = "", unread: list | None = None,
           published: datetime | None = None, source_text: str = "", list_fetcher=None) -> list[dict]:
    """元記事のリンクを最大2本取りに行き、抜き出しを返す。
    戻り値: [{"url", "name", "title", "excerpt", "checkedAt", "public"}]。失敗しても例外にしない（取れたものだけ）。
    unread（リスト）を渡すと、見つけたのに読めなかった資料（PDF・IR の開示資料）の URL を足す（「未発表」と書かせない門に使う。台帳 A44）。
    株式・提携・決算などの話題（IR_TOPIC_RE）では、同じ会社の IR の開示資料（適時開示の PDF）も探して読む（ir_documents）"""
    links = (page or {}).get("links") or []
    # 深く読む種類（決算・脆弱性・製品・制度…）は3本まで追う（scripts/news_types.py）
    cap_fetches = MAX_FETCHES + (1 if news_type in TYPE_EXCERPT_CAP else 0)
    targets = pick(links, (page or {}).get("final_url") or source_url, title, money, limit=min(max_fetches, cap_fetches), news_type=news_type)
    if fetcher is not None and list_fetcher is None:
        list_fetcher = lambda day, n: ""  # noqa: E731 - 試しの取得（テスト）では TDnet の一覧を取りに行かない
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
            if unread is not None and is_document(link["url"]):
                unread.append(link["url"])
            continue
        text = excerpt(got.get("text") or "", f"{title} {link.get('text', '')}", keywords, cap=TYPE_EXCERPT_CAP.get(news_type, EXCERPT_CAP))
        if len(text) < 80:
            if unread is not None and is_document(link["url"]):
                unread.append(link["url"])
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
    # 同じ会社の IR の開示資料（適時開示の PDF）。数字（株数・金額・日程）が発表の本文ではなく別の PDF にあることが多い（ゆうちょ銀行の例）
    if ir_wanted(title, news_type, source_text):
        seen = {o["url"] for o in out} | {link["url"] for link in targets}
        for doc in ir_documents(page, source_url, title, source_name, published or now, fetcher=fetcher,
                                list_fetcher=list_fetcher, log=log, skip=seen):
            try:
                got = fetcher(doc["url"], FETCH_TIMEOUT)
            except Exception as error:  # noqa: BLE001
                if log:
                    log(f"   [ir] 開示資料を読めない: {doc['url'][:80]}（{str(error)[:60]}）")
                if unread is not None:
                    unread.append(doc["url"])
                continue
            text = excerpt(got.get("text") or "", f"{title} {doc.get('text', '')}", keywords, cap=IR_EXCERPT_CAP)
            if len(text) < 80:
                if unread is not None:
                    unread.append(doc["url"])
                continue
            out.append({
                "url": doc["url"],
                "name": f"{source_name or site_name(doc['url'])} IR（適時開示）",
                "title": (doc.get("text") or got.get("title") or "").strip()[:80],
                "excerpt": text,
                "checkedAt": today,
                "public": False,
                "ir": True,
            })
            if log:
                log(f"   [ir] {doc['url'][:90]}（{len(text)}字を抜き出し・{doc.get('via')}）")
    return out


def is_document(url: str) -> bool:
    """開示資料・PDF の URL か（読めなかったときに「未読の資料」として残すもの）"""
    return bool(re.search(r"\.pdf(?:$|\?)", url, re.I) or IR_HOST_RE.search(host(url)))


def ir_wanted(title: str, news_type: str = "", source_text: str = "") -> bool:
    """IR の開示資料を探す記事か（決算・提携・書類の種類か、題名・冒頭が株式・提携・決算などの話題）"""
    return news_type in IR_NEWS_TYPES or bool(IR_TOPIC_RE.search(f"{title} {(source_text or '')[:400]}"))


def _company_key(name: str) -> str:
    """会社名の比べ方（「株式会社」「公式」「（日本）」などを外す）"""
    name = re.sub(r"株式会社|（株）|\(株\)|有限会社|公式|ニュースリリース|プレスリリース|IR|ニュース|（[^）]*）|\([^)]*\)|\s", "", name or "")
    return name


def shared_words(title_words: set[str], text: str) -> int:
    """題名の言葉（2字以上）のうち、相手の文字列に含まれる数（日本語は語の区切りが無いので部分一致で数える）"""
    low = (text or "").lower()
    return sum(1 for w in title_words if len(w) >= 2 and w in low)


def tdnet_rows(html: str) -> list[dict]:
    """TDnet の開示一覧（日ごと）の行: [{"code", "name", "title", "url"}]"""
    rows = []
    for m in re.finditer(r'kjCode"[^>]*>\s*(\d+)\s*</td>\s*<td[^>]*kjName"[^>]*>([^<]*)</td>\s*<td[^>]*kjTitle"[^>]*>\s*<a href="([^"]+)"[^>]*>([^<]+)</a>', html):
        href = m.group(3)
        rows.append({"code": m.group(1), "name": m.group(2).strip(), "title": m.group(4).strip(),
                     "url": href if href.startswith("http") else "https://www.release.tdnet.info/inbs/" + href})
    return rows


def _tdnet_list(day: str, page: int) -> str:
    data, _ = feeds.fetch(TDNET_LIST.format(page=page, day=day), timeout=FETCH_TIMEOUT, accept="text/html")
    return feeds._decode(data)


def ir_documents(page: dict | None, source_url: str, title: str, source_name: str, when: datetime | None, *,
                 fetcher=None, list_fetcher=None, log=None, skip: set[str] | None = None, limit: int = 1) -> list[dict]:
    """同じ会社の IR の開示資料を探す（最大 limit 本）。探す順:
    1. 元のページから張られた IR の配信サービスの資料（IR_HOST_RE）
    2. 元のページから張られた同じ公式ドメインの IR の一覧ページ（IR_PAGE_RE）の中の、題名の言葉が重なる資料（PDF・IR の配信サービス）
    3. 日本の会社なら、発表の日の TDnet（東証の適時開示）の一覧で、会社名が合い題名の言葉が重なる開示（PDF）
    どれも会社自身の開示（報道ではない）。戻り値: [{"url", "text", "via"}]"""
    fetcher = fetcher or _fetch_page
    list_fetcher = list_fetcher or _tdnet_list
    skip = set(skip or ())
    words = _words(title)
    started = time.time()
    found: list[dict] = []

    def add(url: str, text: str, via: str) -> None:
        if url not in skip and all(f["url"] != url for f in found):
            found.append({"url": url, "text": text, "via": via})

    links = (page or {}).get("links") or []
    base_url = (page or {}).get("final_url") or source_url
    for link in links:
        url = link.get("url") or ""
        if IR_HOST_RE.search(host(url)) and (shared_words(words, link.get("text") or "") or "/doc/" in url):
            add(url, link.get("text") or "", "元のページのリンク")
    if len(found) < limit:
        ir_pages = [l for l in links if (l.get("url") or "").startswith("http") and base_domain(host(l["url"])) in official_domains(base_url)
                    and IR_PAGE_RE.search(f"{l.get('url')} {l.get('text') or ''}") and not is_document(l["url"])][:1]
        for ir in ir_pages:
            if time.time() - started > IR_BUDGET:
                break
            try:
                got = fetcher(ir["url"], FETCH_TIMEOUT)
            except Exception:  # noqa: BLE001
                continue
            for link in got.get("links") or []:
                url = link.get("url") or link.get("href") or ""
                same = base_domain(host(url)) in official_domains(base_url) or IR_HOST_RE.search(host(url))
                if same and is_document(url) and shared_words(words, link.get("text") or "") >= 1:
                    add(url, link.get("text") or "", "IR の一覧ページ")
    jp = host(source_url).endswith(".jp") or bool(re.search(r"[ぁ-んァ-ヶ一-龠]", source_name or ""))
    key = _company_key(source_name)
    if len(found) < limit and jp and key and when is not None:
        day = when.astimezone(JST).strftime("%Y%m%d")
        best: list[tuple[int, dict]] = []
        for n in range(1, TDNET_PAGES + 1):
            if time.time() - started > IR_BUDGET:
                break
            try:
                html = list_fetcher(day, n)
            except Exception:  # noqa: BLE001
                break
            rows = tdnet_rows(html)
            if not rows:
                break
            for row in rows:
                name = _company_key(row["name"])
                if not name or not (name in key or key in name or name in title):
                    continue
                overlap = shared_words(words - set(_words(row["name"])) - {name.lower()}, row["title"])
                if overlap:
                    best.append((overlap, row))
        for _, row in sorted(best, key=lambda x: -x[0])[:limit]:
            add(row["url"], row["title"], "TDnet（適時開示）")
    if log and found:
        log(f"   [ir] 開示資料の候補 {len(found)}本（{', '.join(f['via'] for f in found)}）")
    return found[:limit]


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
