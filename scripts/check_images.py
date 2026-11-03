"""壊れた画像の見張り（デプロイのあと・1時間ごと。.github/workflows/broken_images.yml）

本番のページを開いて、読者のブラウザが読む画像（<img> の src・srcset、先読みの imagesrcset、og:image・twitter:image）を
1つずつ取り寄せ、「200 で、中身が画像（Content-Type が image/…）」かを確かめる。

  見るページ: トップ、/articles、ジャンルのページ、新しい記事30本、新しい記事10本の /card/<slug>
  --articles: 全記事のサムネイル（自前の写真は幅ごとの WebP も）を直接確かめる（さかのぼりの点検用）

--fix を付けると、直せるものはその場で直す。
  - 自前の写真（/img/）の幅ごとの WebP が無い → 元の JPEG から作って KV に置き直す
  - 自前の写真の JPEG が無い → 公式発表の写真なら元の URL から取り直して保存。無理なら外して選び直す（scripts/images.py --slug）
  - 外部サイトの画像を直リンクしている → 公式発表なら自前に保存、それ以外は外す（images.settle_hotlink）
  - YouTube の高画質サムネイルが消えた → 標準画質（hqdefault）に替える
--issue を付けると、見つかった壊れた画像の一覧を GitHub の issue（ラベル broken-image）に書く（開いているものがあればコメントを足す）。

自社アクセスとして数えないよう、ページには ?_internal=1、すべての取得に X-Saisoku-Internal: 1 を付ける（AGENTS.md）。
広告・提携先（a8.net・楽天・Amazon・AdSense など）の画像には一切つながない（docs/automation.md）。
終了コード: 壊れた画像が（直したあとも）残っていれば 1。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
DEFAULT_BASE = "https://ai-tech-express.jp"
SITE_HOST = "ai-tech-express.jp"
OWN_HOSTS = {SITE_HOST}  # 手元のサーバーで試すときは main がそのホストも足す
UA = "SaisokuImageCheck/1.0 (+https://ai-tech-express.jp/contact)"
LABEL = "broken-image"
VARIANTS = ("sm.webp", "w400.webp", "w800.webp", "w1200.webp")

# 広告・提携先・計測。ここの画像は取りに行かない（数字が水増しされる）
AD_HOSTS = re.compile(
    r"(^|\.)(a8\.net|rakuten\.co\.jp|r10s\.jp|rakuten\.com|amazon\.co\.jp|amazon\.com|amzn\.to|media-amazon\.com|ssl-images-amazon\.com|"
    r"amazon-adsystem\.com|googlesyndication\.com|doubleclick\.net|googleadservices\.com|adtrafficquality\.google|adservice\.google\.com|"
    r"google-analytics\.com|googletagmanager\.com|cloudflareinsights\.com|valuecommerce\.(com|ne\.jp)|moshimo\.com|accesstrade\.net)$",
    re.I,
)
OWN_IMG = re.compile(r"^https://ai-tech-express\.jp/img/([A-Za-z0-9][A-Za-z0-9_-]*)\.(jpg|sm\.webp|w(?:400|800|1200)\.webp)$")
YOUTUBE = re.compile(r"^https://(i\.ytimg\.com|img\.youtube\.com)/")
YT_ID = re.compile(r"^https://(?:i\.ytimg\.com|img\.youtube\.com)/vi(?:_webp)?/([^/]+)/")


def log(message: str) -> None:
    print(message, flush=True)


# --------------------------------------------------------------------------- #
# ページから画像の URL を集める
# --------------------------------------------------------------------------- #


def srcset_urls(value: str) -> list[str]:
    """srcset の候補の URL だけ（"a.webp 400w, b.webp 800w" → [a.webp, b.webp]）"""
    out = []
    for part in (value or "").split(","):
        url = part.strip().split(" ")[0].strip()
        if url:
            out.append(url)
    return out


class ImageCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("img", "source"):
            if a.get("src"):
                self.urls.append(a["src"])
            self.urls.extend(srcset_urls(a.get("srcset", "")))
        elif tag == "link" and a.get("as") == "image" and "preload" in a.get("rel", ""):
            if a.get("href"):
                self.urls.append(a["href"])
            self.urls.extend(srcset_urls(a.get("imagesrcset", "")))
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image") and a.get("content"):
                self.urls.append(a["content"])


def page_images(html: str, page_url: str) -> list[str]:
    """ページの画像の絶対 URL（data: と広告・計測は除く。重複なし・出てきた順）"""
    collector = ImageCollector()
    collector.feed(html)
    seen: dict[str, None] = {}
    for raw in collector.urls:
        raw = raw.strip()
        if not raw or raw.startswith("data:") or raw.startswith("blob:"):
            continue
        url = urllib.parse.urljoin(page_url, raw)
        host = urllib.parse.urlparse(url).hostname or ""
        if not url.startswith(("http://", "https://")) or AD_HOSTS.search(host):
            continue
        seen.setdefault(url, None)
    return list(seen)


# --------------------------------------------------------------------------- #
# 取り寄せ
# --------------------------------------------------------------------------- #


def internal(url: str) -> str:
    """自分のサイトのページ URL に ?_internal=1 を付ける（画像には付けない。KV のキャッシュの鍵を変えない）"""
    parts = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parts.query)
    if ("_internal", "1") not in query:
        query.append(("_internal", "1"))
    return urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(query)))


def fetch(url: str, timeout: int = 20, limit: int = 4 * 1024 * 1024) -> tuple[int, str, bytes]:
    """(状態コード, Content-Type, 先頭の中身)。通信の失敗は 0"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/avif,image/webp,image/*,text/html;q=0.8,*/*;q=0.5", "X-Saisoku-Internal": "1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.headers.get("Content-Type", ""), res.read(limit)
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", "") if error.headers else "", b""
    except Exception as error:  # noqa: BLE001
        return 0, f"error: {str(error)[:80]}", b""


def image_problem(status: int, ctype: str, body: bytes) -> str | None:
    """画像として読めない理由（読めるなら None）"""
    if status != 200:
        return f"HTTP {status or ctype}"
    if not ctype.lower().startswith("image/"):
        return f"画像ではない（{ctype[:40] or 'Content-Type なし'}）"
    if len(body) < 64:
        return "中身が空"
    return None


def check_urls(urls: list[str], workers: int = 8) -> dict[str, str | None]:
    def one(url: str) -> tuple[str, str | None]:
        status, ctype, body = fetch(url)
        if status == 0:  # 一時的な通信の失敗は1回だけやり直す
            status, ctype, body = fetch(url)
        return url, image_problem(status, ctype, body)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(one, urls))


# --------------------------------------------------------------------------- #
# 記事
# --------------------------------------------------------------------------- #


def load_articles() -> dict[str, tuple[Path, dict]]:
    out = {}
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            article = json.loads(path.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out[article.get("slug") or path.stem] = (path, article)
    return out


def newest(articles: dict[str, tuple[Path, dict]], count: int) -> list[str]:
    def key(slug: str) -> str:
        a = articles[slug][1]
        return a.get("publishedAt") or a.get("generatedAt") or ""

    live = [s for s, (_, a) in articles.items() if not a.get("duplicateOf") and not a.get("draft")]
    return sorted(live, key=key, reverse=True)[:count]


def is_hotlink(url: str) -> bool:
    """読者のブラウザに外部サイトの画像を直接読ませているか（自前と YouTube 以外）"""
    host = urllib.parse.urlparse(url).hostname or ""
    return bool(url.startswith("http")) and host not in OWN_HOSTS and not YOUTUBE.match(url) and not AD_HOSTS.search(host)


def thumb_urls(thumbnail: str) -> list[str]:
    """記事のサムネイルで読者が読む URL（自前の写真は幅ごとの WebP も）"""
    m = OWN_IMG.match(thumbnail or "")
    if m and m.group(2) == "jpg":
        return [thumbnail] + [f"https://{SITE_HOST}/img/{m.group(1)}.{v}" for v in VARIANTS]
    return [thumbnail] if thumbnail else []


def owners(articles: dict[str, tuple[Path, dict]]) -> dict[str, set[str]]:
    """画像の URL → それをサムネイルにしている記事"""
    out: dict[str, set[str]] = defaultdict(set)
    for slug, (_, a) in articles.items():
        thumb = a.get("thumbnail") or ""
        for url in thumb_urls(thumb):
            out[url].add(slug)
        m = YT_ID.match(thumb)
        if m:  # サイトは YouTube を WebP・別の画質で出すので、動画の ID でも引けるようにする
            out[f"yt:{m.group(1)}"].add(slug)
        out[f"https://{SITE_HOST}/cover/{slug}"].add(slug)
        out[f"https://{SITE_HOST}/card/{slug}"].add(slug)
    return out


def slugs_for(url: str, by_url: dict[str, set[str]]) -> set[str]:
    clean = url.split("?")[0]
    found = set(by_url.get(clean, set()))
    m = YT_ID.match(clean)
    if m:
        found |= by_url.get(f"yt:{m.group(1)}", set())
    return found


# --------------------------------------------------------------------------- #
# 見回り
# --------------------------------------------------------------------------- #


def crawl(base: str, articles: dict[str, tuple[Path, dict]], latest: int, cards: int) -> tuple[dict[str, str | None], dict[str, set[str]], list[str]]:
    """(画像 URL → 問題, 画像 URL → 載っていたページ, 開けなかったページ)"""
    genres = sorted({a.get("genreSlug") for _, a in articles.values() if a.get("genreSlug")})
    recent = newest(articles, max(latest, cards))
    pages = ["/", "/articles"] + [f"/genres/{g}" for g in genres] + [f"/articles/{s}" for s in recent[:latest]]
    where: dict[str, set[str]] = defaultdict(set)
    failed_pages: list[str] = []

    def load(path: str) -> tuple[str, int, str]:
        status, ctype, body = fetch(internal(base + path), timeout=30, limit=8 * 1024 * 1024)
        return path, status, body.decode("utf-8", "replace") if status == 200 and "html" in ctype else ""

    with ThreadPoolExecutor(max_workers=6) as pool:
        for path, status, html in pool.map(load, pages):
            if not html:
                failed_pages.append(f"{path}（HTTP {status}）")
                continue
            for url in page_images(html, base + path):
                # 手元のサーバーで試すときも、本番の画像（https://ai-tech-express.jp/…）はそのまま本番で確かめる
                where[url].add(path)
    for slug in recent[:cards]:
        where[f"{base}/card/{slug}"].add(f"/card/{slug}")
    results = check_urls(sorted(where))
    return results, where, failed_pages


def scan_articles(articles: dict[str, tuple[Path, dict]]) -> tuple[dict[str, str | None], dict[str, set[str]]]:
    where: dict[str, set[str]] = defaultdict(set)
    for slug, (_, a) in articles.items():
        if a.get("noPhoto"):
            continue
        for url in thumb_urls(a.get("thumbnail") or ""):
            where[url].add(f"/articles/{slug}（サムネイル）")
    return check_urls(sorted(where), workers=12), where


# --------------------------------------------------------------------------- #
# 直す
# --------------------------------------------------------------------------- #


def _images():
    sys.path.insert(0, str(ROOT / "scripts"))
    import images  # noqa: PLC0415 - 直すときだけ読む（Pillow・鍵が要る）

    images.load_env_local()
    return images


def save(path: Path, article: dict) -> None:
    path.write_text(json.dumps(article, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fix(broken: dict[str, str], hotlinks: dict[str, set[str]], articles: dict[str, tuple[Path, dict]], dry_run: bool, repick_max: int) -> list[str]:
    """直せるものを直す。何をしたかの1行ずつを返す"""
    images = _images()
    done: list[str] = []
    repick: set[str] = set()
    by_url = owners(articles)
    handled: set[str] = set()
    ctx = {"repeats": Counter(), "official_by_original": {}, "dry_run": dry_run}

    for url, problem in sorted(broken.items()):
        for slug in sorted(slugs_for(url, by_url)):
            if (slug, url) in handled or slug not in articles:
                continue
            path, article = articles[slug]
            thumb = article.get("thumbnail") or ""
            own = OWN_IMG.match(thumb)
            if url.split("?")[0].endswith((f"/cover/{slug}", f"/card/{slug}")):
                continue  # 画像を作るページそのものの失敗は直せない（issue に書くだけ）
            handled.add((slug, url))
            # 1. 自前の写真
            if own and own.group(2) == "jpg":
                key = own.group(1)
                jpg_status, jpg_type, jpg = fetch(thumb, timeout=30, limit=images.MAX_BYTES)
                if not image_problem(jpg_status, jpg_type, jpg):
                    # JPEG はある → 幅ごとの WebP を作り直す
                    try:
                        variants = images.webp_variants(images.open_image(jpg))
                        if not dry_run:
                            for name, body in variants.items():
                                images.kv_put(f"{key}.{name}.webp", body)
                        done.append(f"{slug}: 幅ごとの WebP を KV に置き直した（{url.rsplit('/', 1)[-1]}: {problem}）")
                    except Exception as error:  # noqa: BLE001
                        done.append(f"{slug}: WebP を作り直せなかった（{str(error)[:80]}）")
                    continue
                credit = article.get("imageCredit") or {}
                original = credit.get("original") or ""
                if credit.get("kind") == "official" and original.startswith("http"):
                    status, ctype, data = fetch(original, timeout=30, limit=images.MAX_BYTES)
                    if not image_problem(status, ctype, data):
                        try:
                            new_url, size = images.store(slug, images.open_image(data), dry_run, keep_aspect=credit.get("imageKind") != "logo")
                            article["thumbnail"] = new_url
                            article["imageCredit"] = {**credit, "width": size[0], "height": size[1], "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                            article.pop("imageFocus", None)
                            if not dry_run:
                                save(path, article)
                            done.append(f"{slug}: 公式の写真を元の URL から取り直して自前に保存した → {new_url}")
                            continue
                        except Exception as error:  # noqa: BLE001
                            done.append(f"{slug}: 公式の写真を保存し直せなかった（{str(error)[:80]}）")
                # 取り直せない（AI 生成・素材・元が消えた）→ 外して選び直す
                article["thumbnail"] = ""
                for field in ("imageCredit", "imageFocus"):
                    article.pop(field, None)
                if not dry_run:
                    save(path, article)
                repick.add(slug)
                done.append(f"{slug}: 自前の写真が KV に無いので外し、選び直しに回した")
            # 2. YouTube のサムネイル
            elif YOUTUBE.match(thumb):
                m = YT_ID.match(thumb)
                hq = f"https://i.ytimg.com/vi/{m.group(1)}/hqdefault.jpg" if m else ""
                if hq and hq != thumb and not image_problem(*fetch(hq)):
                    article["thumbnail"] = hq
                    done.append(f"{slug}: YouTube の高画質サムネイルが無いので標準画質に替えた")
                else:
                    article["thumbnail"] = ""
                    repick.add(slug)
                    done.append(f"{slug}: YouTube のサムネイルが読めないので外した（動画が消えた・非公開）")
                article.pop("imageFocus", None)
                if not dry_run:
                    save(path, article)
            # 3. 外部サイトの直リンク（壊れている）
            elif is_hotlink(thumb):
                hotlinks.setdefault(thumb, set()).add(slug)

    # 外部サイトの画像の直リンク（壊れていなくても直す）
    for url, slugs in sorted(hotlinks.items()):
        for slug in sorted(slugs):
            if slug not in articles:
                continue
            path, article = articles[slug]
            if article.get("thumbnail") != url:
                continue
            try:
                outcome = images.settle_hotlink(path, article, ctx)
            except Exception as error:  # noqa: BLE001
                done.append(f"{slug}: 直リンクを直せなかった（{str(error)[:80]}）")
                continue
            if outcome == "cached-official":
                done.append(f"{slug}: 直リンクの公式の画像を自前に保存した → {article.get('thumbnail')}")
            elif outcome == "unhotlinked":
                repick.add(slug)
                done.append(f"{slug}: 外部サイトの画像の直リンクを外した（{urllib.parse.urlparse(url).hostname}）")

    if repick and not dry_run:
        targets = sorted(repick)[:repick_max]
        cmd = [sys.executable, str(ROOT / "scripts" / "images.py"), "--max", str(len(targets)), "--budget", "300", "--focus-recent", "0"]
        for slug in targets:
            cmd += ["--slug", slug]
        log(f"[img-check] 写真を選び直す: {', '.join(targets)}")
        try:
            subprocess.run(cmd, cwd=ROOT, timeout=480, check=False)
        except subprocess.TimeoutExpired:
            log("[img-check] 選び直しが時間切れ（残りは次の回の images.py が拾う）")
    return done


# --------------------------------------------------------------------------- #
# issue
# --------------------------------------------------------------------------- #


def report_issue(broken: dict[str, str], where: dict[str, set[str]], fixed: list[str], base: str, run_url: str) -> None:
    lines = [f"本番（{base}）で読めない画像が {len(broken)} 件ありました（{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}）。", ""]
    lines.append("| 画像 | 問題 | 載っていたページ |")
    lines.append("|---|---|---|")
    for url, problem in sorted(broken.items())[:80]:
        pages = ", ".join(sorted(where.get(url, set()))[:3])
        lines.append(f"| {url} | {problem} | {pages} |")
    if len(broken) > 80:
        lines.append(f"| …ほか {len(broken) - 80} 件 | | |")
    if fixed:
        lines += ["", "自動で直したもの（反映は次のデプロイのあと）:", *[f"- {x}" for x in fixed[:60]]]
    if run_url:
        lines += ["", f"実行の記録: {run_url}"]
    lines += ["", "見張り: scripts/check_images.py（.github/workflows/broken_images.yml）。壊れた画像が無くなったら、この issue は自動で閉じます。"]
    body = "\n".join(lines)
    subprocess.run(["gh", "label", "create", LABEL, "--color", "D93F0B", "--description", "本番で読めない画像", "--force"], check=False, capture_output=True, text=True)
    found = subprocess.run(["gh", "issue", "list", "--label", LABEL, "--state", "open", "--json", "number", "--limit", "1"], capture_output=True, text=True)
    existing = json.loads(found.stdout or "[]") if found.returncode == 0 else []
    if existing:
        subprocess.run(["gh", "issue", "comment", str(existing[0]["number"]), "--body", body], check=False)
        log(f"[img-check] issue #{existing[0]['number']} にコメントを足しました")
    else:
        subprocess.run(["gh", "issue", "create", "--title", f"本番で読めない画像があります（{len(broken)}件）", "--label", LABEL, "--body", body], check=False)


def close_issue() -> None:
    found = subprocess.run(["gh", "issue", "list", "--label", LABEL, "--state", "open", "--json", "number"], capture_output=True, text=True)
    for item in json.loads(found.stdout or "[]") if found.returncode == 0 else []:
        subprocess.run(["gh", "issue", "close", str(item["number"]), "--comment", "読めない画像は無くなりました（scripts/check_images.py）。"], check=False)


# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=DEFAULT_BASE, help="見るサイト（既定は本番）")
    parser.add_argument("--articles", action="store_true", help="ページを見回らず、全記事のサムネイルを直接確かめる")
    parser.add_argument("--latest", type=int, default=30, help="開く新しい記事の数")
    parser.add_argument("--cards", type=int, default=10, help="/card/<slug> を確かめる新しい記事の数")
    parser.add_argument("--fix", action="store_true", help="直せるものを直す（KV への保存・記事 JSON の書き換え）")
    parser.add_argument("--repick-max", type=int, default=8, help="1回で写真を選び直す最大本数")
    parser.add_argument("--dry-run", action="store_true", help="--fix で何も保存しない")
    parser.add_argument("--issue", action="store_true", help="GitHub の issue（ラベル broken-image）に書く・無くなったら閉じる")
    parser.add_argument("--run-url", default="", help="issue に添える実行の URL")
    parser.add_argument("--json", help="結果をこのファイルに書く")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    OWN_HOSTS.add(urllib.parse.urlparse(base).hostname or SITE_HOST)

    articles = load_articles()
    if args.articles:
        results, where = scan_articles(articles)
        failed_pages: list[str] = []
    else:
        results, where, failed_pages = crawl(base, articles, args.latest, args.cards)
    broken = {u: p for u, p in results.items() if p}
    hotlinks: dict[str, set[str]] = defaultdict(set)
    by_url = owners(articles)
    for url in results:
        if is_hotlink(url):
            hotlinks[url] |= slugs_for(url, by_url) or {s for s, (_, a) in articles.items() if a.get("thumbnail") == url}
    if args.articles:
        for slug, (_, a) in articles.items():
            if is_hotlink(a.get("thumbnail") or ""):
                hotlinks[a["thumbnail"]].add(slug)

    log(f"[img-check] 画像 {len(results)} 件を確認: 読めない {len(broken)} 件・外部サイトの直リンク {len(hotlinks)} 件" + (f"・開けないページ {len(failed_pages)}" if failed_pages else ""))
    for url, problem in sorted(broken.items()):
        log(f"   ✗ {url} … {problem}（{', '.join(sorted(where.get(url, set()))[:2])}）")
    for url, slugs in sorted(hotlinks.items()):
        log(f"   ↗ 直リンク {url[:110]}（{', '.join(sorted(slugs))}）")
    for page in failed_pages:
        log(f"   ! ページを開けない: {page}")

    fixed: list[str] = []
    if args.fix and (broken or hotlinks):
        fixed = fix(broken, {u: set(s) for u, s in hotlinks.items()}, articles, args.dry_run, args.repick_max)
        for line in fixed:
            log(f"   ✓ {line}")
        # 直したあと、まだ読めないもの（KV を直したもの・記事を書き換えたものは次のデプロイで消える）
        recheck = check_urls(sorted(broken))
        still = {u: p for u, p in recheck.items() if p}
        log(f"[img-check] 直したあとも読めない画像（記事の書き換えは次のデプロイで反映）: {len(still)} 件")
    if args.json:
        Path(args.json).write_text(json.dumps({"broken": broken, "hotlinks": {u: sorted(s) for u, s in hotlinks.items()}, "fixed": fixed, "failedPages": failed_pages}, ensure_ascii=False, indent=2), "utf-8")
    if args.issue:
        problems = dict(broken)
        problems.update({f"(ページ) {p}": "開けない" for p in failed_pages})
        if problems:
            report_issue(problems, where, fixed, base, args.run_url)
        else:
            close_issue()
    return 1 if broken or failed_pages else 0


if __name__ == "__main__":
    raise SystemExit(main())
