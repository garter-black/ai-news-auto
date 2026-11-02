#!/usr/bin/env python3
"""記事の写真を選ぶ（3段階）。写真の無い記事・ロゴだけの記事を、大手ニュースサイトのような写真入りにする。

  1. 元記事の写真: og:image / twitter:image / 本文中のいちばん大きい画像。
     **公式の発表（会社・団体の自社サイト、PR TIMES などのプレスリリース配信）の記事だけ**。ダウンロードして
     幅 1200px まで縮小（切り抜かない）し、自前の KV から配信する。記事には「画像：◯◯（公式発表より）」と出す。
     ほかのニュースサイト・ブログ・SNS・不明な発信元の写真は保存も直リンクもしない（2・3 にする）。
     幅 800px 未満・極端な縦横比・ロゴ（無地の地に文字だけ）・サイト共通の既定画像・文字や図が主役の画像（Workers AI で判定）は使わない
  2. フリー素材: Wikimedia Commons と Openverse（CC0・パブリックドメイン・CC BY だけ。商用可・改変可）。
     検索語は記事から軽量モデル（scripts/llm.py の bulk）が作る具体的な英語の名詞句
  3. AI 生成: Cloudflare Workers AI（flux-2-klein-4b → flux-1-schnell）。文字・ロゴ・実在の人物なし。1日 IMAGE_AI_DAILY_MAX 枚まで

2・3 の画像は 1200×630 の JPEG（約100KB）、1 は縦横比そのままの幅 1200px までの JPEG にし、どれも幅 400 / 640（.sm）/ 800 / 1200 の
WebP を添えて Workers KV（saisoku-images）へ置き、サイトの /img/<名前> で配信する（cloudflare/worker.ts。srcset は lib/thumb-url.ts）。
記事 JSON には thumbnail（画像の URL）と imageCredit（出典・ライセンス・作者）を書く。
YouTube のサムネイルはそのまま使う。

  python3 scripts/images.py --changed            # このワークツリーで増えた・変わった記事だけ（auto_run.yml）
  python3 scripts/images.py --recent 40          # 新しい40本のうち未確認のもの
  python3 scripts/images.py --all --max 50       # 全記事（新しい順）を点検して直す（さかのぼり）
  python3 scripts/images.py --slug <slug> --dry-run
  python3 scripts/images.py --recheck-source        # 元記事の写真を採用した記事を今の基準で見直す
  python3 scripts/images.py --retry-ai --max 30     # AI 生成にした記事でフリー素材を探し直す（Commons が落ちていた日の後に）
  python3 scripts/images.py --cache-official --max 100  # 外部の写真を直リンクしている記事を直す（公式は自前に保存、それ以外は 2・3）
  python3 scripts/images.py --variants --max 400        # 前からある自前の写真に幅ごとの WebP を足す

必要な環境変数: CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN（Workers AI）/ CF_KV_TOKEN（KV への書き込み。無ければ
CLOUDFLARE_API_TOKEN）。手元では wrangler のログイン情報を使う。検索語づくりは GEMINI_API_KEY など（llm.py と同じ）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm  # noqa: E402

try:
    from PIL import Image, ImageChops, ImageOps

    Image.MAX_IMAGE_PIXELS = 60_000_000
except ImportError:  # pragma: no cover - CI では requirements.txt で入る
    Image = None
    ImageOps = None
    ImageChops = None

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "51e37d82e15d397b9666dc3d09bd5b92"
KV_NAMESPACE = os.environ.get("IMAGES_KV_ID") or "358e46161fdb4857865dec69dd72285b"
IMAGE_BASE = (os.environ.get("IMAGE_BASE_URL") or "https://ai-tech-express.jp/img/").rstrip("/") + "/"
AI_DAILY_MAX = int(os.environ.get("IMAGE_AI_DAILY_MAX") or "40")
PREVIEW_DIR: Path | None = None  # --preview で指定すると、出来上がった JPEG をここにも書く（確認用）
PAGES_BY_IMAGE: dict[str, set[str]] = {}  # 画像 URL → それを使っている記事の元記事ページ（main で作る）

# Wikimedia は連絡先入りの UA を求める（https://meta.wikimedia.org/wiki/User-Agent_policy）
BOT_UA = "SaisokuNewsBot/1.0 (+https://ai-tech-express.jp/contact)"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"

MIN_SOURCE_WIDTH = 800
MIN_STOCK_WIDTH = 1200
MAX_BYTES = 15 * 1024 * 1024
OUT_W, OUT_H = 1200, 630
SMALL_W, SMALL_H = 640, 336
JPEG_MAX_BYTES = 140 * 1024

# ロゴ・アイコン・既定画像らしい URL
LOGO_URL = re.compile(
    r"(logo|favicon|apple-touch|/icons?/|[-_/]icon[-_.]|noimage|no[-_]image|now[-_]?printing|placeholder|dummy|spacer|blank\.|sprite|avatar)",
    re.I,
)
GENERIC_OG = re.compile(r"(/|[-_])(ogp?|og[-_]?image|ogp[-_]?image|share|sns|social|default|site)[-_]?(image|img|default)?\d?\.(png|jpe?g|webp)$", re.I)
SHARED_DIR = re.compile(r"/(common|themes?|assets?|shared|static|img/common|images/common|site)/", re.I)
# 素材の題名で避けるもの（人物・図・文字の多いもの）
BAD_STOCK_TITLE = re.compile(
    r"\b(logos?|maps?|diagrams?|charts?|graphs?|plots?|posters?|flyers?|leaflets?|signs?|signage|screenshots?|screen shot|flags?|emblems?|"
    r"coat of arms|portraits?|selfies?|headshots?|signatures?|documents?|scanned|page \d+|book cover|cover art|stamps?|banknotes?|"
    r"banners?|infographics?|illustrations?|drawings?|cartoons?|icons?|texts?|memes?|watermarks?|paintings?|engravings?|lithographs?|MET DP\d+|nude|naked|weapons?|corpses?|blood)\b",
    re.I,
)
# 人物が主役の写真（特に子ども）は避ける
PEOPLE_TITLE = re.compile(r"\b(child|children|kid|kids|girl|girls|boy|boys|baby|toddler|woman|women|man|men|people|person|selfie|face|family|teen|student|students)\b", re.I)


def log(message: str) -> None:
    print(message, flush=True)


# --------------------------------------------------------------------------- #
# 通信
# --------------------------------------------------------------------------- #


def http_get(url: str, *, ua: str = BROWSER_UA, accept: str = "*/*", timeout: int = 20, limit: int = MAX_BYTES, referer: str = "") -> tuple[bytes, str, str]:
    """(中身, Content-Type, 最終URL)。大きすぎるものは途中で打ち切って例外"""
    headers = {"User-Agent": ua, "Accept": accept, "Accept-Language": "ja,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        length = int(res.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError(f"大きすぎる（{length // 1024}KB）")
        data = res.read(limit + 1)
        if len(data) > limit:
            raise ValueError("大きすぎる")
        return data, res.headers.get("Content-Type", ""), res.geturl()


def http_json(url: str, *, ua: str = BOT_UA, timeout: int = 20) -> dict:
    data, _, _ = http_get(url, ua=ua, accept="application/json", timeout=timeout, limit=4 * 1024 * 1024)
    return json.loads(data.decode("utf-8"))


_cf_token_cache: dict[str, str] = {}


def wrangler_token() -> str:
    """手元の wrangler ログイン（OAuth）のトークン。期限切れなら wrangler whoami で更新させる"""
    if "wrangler" in _cf_token_cache:
        return _cf_token_cache["wrangler"]
    paths = [
        Path.home() / "Library" / "Preferences" / ".wrangler" / "config" / "default.toml",
        Path.home() / ".config" / ".wrangler" / "config" / "default.toml",
        Path.home() / ".wrangler" / "config" / "default.toml",
    ]
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text("utf-8")
        exp = re.search(r'expiration_time\s*=\s*"([^"]+)"', text)
        if exp:
            try:
                expires = datetime.fromisoformat(exp.group(1).replace("Z", "+00:00"))
                if (expires - datetime.now(timezone.utc)).total_seconds() < 300:
                    subprocess.run(["npx", "--yes", "wrangler", "whoami"], cwd=ROOT.parent if not (ROOT / "node_modules").exists() else ROOT, capture_output=True, timeout=120)
                    text = path.read_text("utf-8")
            except Exception:  # noqa: BLE001
                pass
        match = re.search(r'oauth_token\s*=\s*"([^"]+)"', text)
        if match:
            _cf_token_cache["wrangler"] = match.group(1)
            return match.group(1)
    return ""


def cf_token(kind: str) -> str:
    if kind == "kv":
        token = os.environ.get("CF_KV_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
    else:
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
    return token or wrangler_token()


def kv_put(key: str, data: bytes) -> None:
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE}/values/{urllib.parse.quote(key, safe='')}"
    req = urllib.request.Request(
        url,
        data=data,
        method="PUT",
        headers={"Authorization": f"Bearer {cf_token('kv')}", "Content-Type": "application/octet-stream"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                body = json.loads(res.read() or b"{}")
                if body.get("success", True):
                    return
                raise RuntimeError(str(body)[:200])
        except urllib.error.HTTPError as error:
            detail = error.read()[:200]
            if error.code < 500 or attempt == 2:
                raise RuntimeError(f"KV への保存に失敗（{error.code} {detail!r}）") from error
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 + attempt * 3)


def kv_list() -> set[str]:
    """KV（saisoku-images）にある名前の一覧"""
    names: set[str] = set()
    cursor = ""
    while True:
        url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE}/keys?limit=1000" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cf_token('kv')}"})
        with urllib.request.urlopen(req, timeout=60) as res:
            body = json.loads(res.read())
        names |= {item["name"] for item in body.get("result") or []}
        cursor = ((body.get("result_info") or {}).get("cursor")) or ""
        if not cursor:
            return names


def backfill_variants(articles: dict, dry_run: bool, limit: int, budget: float, started: float) -> Counter:
    """前からある自前の写真（.jpg と .sm.webp だけ）に、幅ごとの WebP（.w400 / .w800 / .w1200）を足す"""
    have = kv_list()
    keys = sorted({m.group(1) for a in articles.values() if (m := re.match(re.escape(IMAGE_BASE) + r"([A-Za-z0-9][A-Za-z0-9_-]*)\.jpg$", a.get("thumbnail") or ""))})
    todo = [k for k in keys if f"{k}.jpg" in have and any(f"{k}.{n}.webp" not in have for n, _ in VARIANT_WIDTHS)]
    log(f"[img] 幅ごとの WebP が無い写真 {len(todo)} 枚（自前の写真 {len(keys)} 枚）")
    tally: Counter = Counter()
    for key in todo[:limit]:
        if budget and time.time() - started > budget:
            log("[img] 時間切れ。残りは次の回に回す")
            break
        try:
            data, _, _ = http_get(f"{IMAGE_BASE}{key}.jpg", ua=BOT_UA, timeout=30)
            variants = webp_variants(open_image(data))
            for name, body in variants.items():
                if f"{key}.{name}.webp" in have:
                    continue
                if not dry_run:
                    kv_put(f"{key}.{name}.webp", body)
            tally["variants"] += 1
        except Exception as error:  # noqa: BLE001
            log(f"   [img] {key}: {str(error)[:120]}")
            tally["error"] += 1
    return tally


# --------------------------------------------------------------------------- #
# 画像の検査
# --------------------------------------------------------------------------- #


def open_image(data: bytes):
    if Image is None:
        raise RuntimeError("Pillow が必要です（pip install pillow）")
    image = Image.open(io.BytesIO(data))
    image.load()
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        bg = Image.new("RGB", image.size, "white")
        bg.paste(image, mask=image.split()[-1])
        image = bg
    return image.convert("RGB")


def graphic_stats(image) -> dict:
    """無地の地の割合（ロゴ・文字だけの画像の目印）と色の多さ"""
    small = image.resize((160, 84))
    pixels = small.tobytes()
    n = len(pixels) // 3
    bins: Counter = Counter()
    rg_sum = yb_sum = rg_sq = yb_sq = 0.0
    for i in range(0, len(pixels), 3):
        r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
        bins[(r >> 4, g >> 4, b >> 4)] += 1
        rg = r - g
        yb = 0.5 * (r + g) - b
        rg_sum += rg
        yb_sum += yb
        rg_sq += rg * rg
        yb_sq += yb * yb
    rg_mean, yb_mean = rg_sum / n, yb_sum / n
    rg_std = max(0.0, rg_sq / n - rg_mean**2) ** 0.5
    yb_std = max(0.0, yb_sq / n - yb_mean**2) ** 0.5
    colorful = (rg_std**2 + yb_std**2) ** 0.5 + 0.3 * (rg_mean**2 + yb_mean**2) ** 0.5
    top = bins.most_common(1)[0][1] / n
    return {"flat": round(top, 3), "bins": len(bins), "colorful": round(colorful, 1)}


def image_problem(image, min_width: int) -> str | None:
    """使えない理由（使えるなら None）"""
    w, h = image.size
    if w < min_width or h < 400:
        return f"小さい（{w}×{h}）"
    ratio = w / h
    if ratio < 1.2 or ratio > 2.6:
        return f"縦横比が合わない（{w}×{h}）"
    stats = graphic_stats(image)
    if stats["flat"] >= 0.88 or stats["bins"] <= 24:
        return f"ロゴ・無地の図（無地 {stats['flat']:.0%}・色 {stats['bins']}）"
    if stats["flat"] >= 0.75 and stats["colorful"] < 4:
        return f"文字だけの図（無地 {stats['flat']:.0%}）"
    return None


def url_problem(url: str, repeats: int) -> str | None:
    if not url or not re.match(r"^https?://", url):
        return "画像なし"
    path = urllib.parse.urlparse(url).path
    if re.search(r"\.(svg|gif|ico)(\?|$)", path, re.I):
        return "SVG・GIF・アイコン"
    if LOGO_URL.search(path):
        return "ロゴ・アイコンの URL"
    if repeats >= 3:
        return f"サイト共通の既定画像（{repeats}本で同じ画像）"
    if GENERIC_OG.search(path) and (SHARED_DIR.search(path) or not re.search(r"\d{4,}", path)):
        return "サイト共通の既定画像（ファイル名）"
    return None


def is_youtube(url: str) -> bool:
    return bool(re.match(r"^https://(i\.ytimg\.com|img\.youtube\.com)/", url or ""))


def is_ours(url: str) -> bool:
    return (url or "").startswith(IMAGE_BASE)


# --------------------------------------------------------------------------- #
# 公式の発表か（元記事の写真を自前で保存して配信してよいか）
# --------------------------------------------------------------------------- #
# 元記事の写真は「その会社・団体が自分のサイトで出した発表（ニュースルーム・公式ブログ・官公庁・研究機関）」と
# 「報道向けに素材を配るプレスリリース配信（PR TIMES など）」のものだけ使い、縮小して自前の KV から配信する（出典を表示）。
# ほかのニュースサイト・ブログ・SNS・不明な発信元の写真は保存も直リンクもせず、フリー素材・AI 生成にする。

# プレスリリース配信（channelSlug → 表示名）。配信元のホストでも判定する
WIRE_CHANNELS = {
    "prtimes": "PR TIMES",
    "prtimes-all": "PR TIMES",
    "atpress": "@Press",
    "value-press": "ValuePress",
    "dreamnews": "ドリームニューズ",
    "kyodo-prwire": "共同通信PRワイヤー",
}
WIRE_HOSTS = {
    "prtimes.jp": "PR TIMES",
    "atpress.ne.jp": "@Press",
    "newscast.jp": "@Press",
    "value-press.com": "ValuePress",
    "dreamnews.jp": "ドリームニューズ",
    "kyodonewsprwire.jp": "共同通信PRワイヤー",
    "businesswire.com": "Business Wire",
    "prnewswire.com": "PR Newswire",
    "globenewswire.com": "GlobeNewswire",
}
# ニュースサイト・まとめ（公式ではない）。fast_lane_rules.json の radar.outlets に加えて
MEDIA_HOSTS = {"news.yahoo.co.jp", "yahoo.co.jp", "nikkei.com", "asahi.com", "yomiuri.co.jp", "mainichi.jp", "sankei.com", "nhk.or.jp", "nytimes.com", "note.com", "medium.com"}

_official_slugs: set[str] | None = None


def _host_in(host: str, hosts) -> str | None:
    host = (host or "").lower().split(":")[0]
    for h in hosts:
        if host == h or host.endswith("." + h):
            return h
    return None


def official_slugs() -> set[str]:
    """公式の発表のフィード（scripts/feeds.py の FEEDS・BROAD_FEEDS と速報レーンの発信元）の channelSlug"""
    global _official_slugs
    if _official_slugs is None:
        slugs: set[str] = {"radar-official"}
        try:
            import feeds  # noqa: PLC0415

            slugs |= set(feeds.FEEDS_BY_SLUG)
        except Exception as error:  # noqa: BLE001
            log(f"   [img] feeds.py を読めない: {str(error)[:80]}")
        try:
            rules = json.loads((ROOT / "scripts" / "fast_lane_rules.json").read_text("utf-8"))
            slugs |= {s.get("slug") for s in rules.get("sources") or [] if s.get("slug")}
            for outlet in (rules.get("radar") or {}).get("outlets") or []:
                if outlet.get("host"):
                    MEDIA_HOSTS.add(outlet["host"])
        except Exception:  # noqa: BLE001
            pass
        try:
            registry = json.loads((ROOT / "scripts" / "sources_registry.json").read_text("utf-8"))
            for value in registry.values():
                if isinstance(value, list):
                    slugs |= {s.get("slug") for s in value if isinstance(s, dict) and s.get("slug")}
        except Exception:  # noqa: BLE001
            pass
        _official_slugs = slugs
    return _official_slugs


def official_source(article: dict) -> dict | None:
    """公式の発表なら {"wire": 配信サービス名 or ""}、そうでなければ None"""
    if article.get("sourceType") != "web":
        return None
    page = article.get("sourceUrl") or ""
    host = urllib.parse.urlparse(page).netloc.lower()
    slug = article.get("channelSlug") or ""
    if not host or slug == "trends" or (slug.startswith("radar-") and slug != "radar-official"):
        return None
    official_slugs()  # MEDIA_HOSTS に radar.outlets を足す
    if _host_in(host, MEDIA_HOSTS):
        return None
    wire = WIRE_CHANNELS.get(slug) or WIRE_HOSTS.get(_host_in(host, WIRE_HOSTS) or "")
    if wire:
        return {"wire": wire}
    if slug in official_slugs():
        return {"wire": ""}
    return None


# 発信元の名前（sourceName）から会社・団体名だけを取り出す（「NVIDIA公式ブログ」→「NVIDIA」）
_ORG_PAREN = re.compile(r"[（(](日本|日本語|AI|Gemini|Copilot|About Amazon|ニュースルーム|新製品|サービス|料金|製品ニュース)[）)]")
_ORG_TAIL = re.compile(
    r"(?:[\s.・]*(?:公式ブログ|公式ニュース|日本版ブログ|公式|グローバルニュースルーム|ニュースルーム|ニュースリリース|ニュースセンター|プレスリリース|報道発表|"
    r"トピックス|ニュース|ブログ|Machine Learning Blog|Developer Blog|Newsroom|Blog|Wire|Source|Changelog|Japan|日本語|日本))+$",
    re.I,
)


def org_name(article: dict) -> str:
    name = (article.get("sourceName") or article.get("channel") or "").strip()
    short = _ORG_TAIL.sub("", _ORG_PAREN.sub("", name)).strip(" ・.")
    return short or name


PAGE_META: dict[str, dict] = {}  # 元記事のページ → {"title", "author"}（source_candidates が入れる）


def wire_org(article: dict) -> str:
    """プレスリリース配信の記事の、発表した会社・団体の名前（分からなければ空）"""
    meta = PAGE_META.get(article.get("sourceUrl") or "") or {}
    if meta.get("author"):
        return meta["author"]
    title = meta.get("title") or ""
    match = re.search(r"(?:\||｜| - )\s*([^|｜]{2,60}?)のプレスリリース\s*$", title)
    if match:
        return match.group(1).strip()
    # @Press は「題名 | 会社名」
    parts = [p.strip() for p in re.split(r"\s\|\s", title) if p.strip()]
    if len(parts) >= 2 and not re.search(r"(@Press|PR TIMES|プレスリリース|ニュースリリース|配信)", parts[-1]) and len(parts[-1]) <= 40:
        return parts[-1]
    return ""


# --------------------------------------------------------------------------- #
# 1. 元記事の写真
# --------------------------------------------------------------------------- #


class _ImageCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.images: list[tuple[int, int, str]] = []  # (優先度, 宣言された幅, URL)
        self.depth_main = 0
        self.order = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in {"og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"} and a.get("content"):
                self.meta.setdefault(key, a["content"].strip())
            return
        if tag == "link" and a.get("rel", "").lower() == "image_src" and a.get("href"):
            self.meta.setdefault("image_src", a["href"].strip())
            return
        if tag in ("article", "main"):
            self.depth_main += 1
            return
        if tag not in ("img", "source"):
            return
        cls = f"{a.get('class', '')} {a.get('id', '')} {a.get('alt', '')}"
        if re.search(r"(logo|icon|avatar|profile|sns|share|banner|btn|button|arrow|badge|qr)", cls, re.I):
            return
        url, width = "", 0
        srcset = a.get("srcset") or a.get("data-srcset") or ""
        if srcset:
            best = 0
            for part in srcset.split(","):
                bits = part.strip().split()
                if not bits:
                    continue
                w = int(re.sub(r"\D", "", bits[1]) or 0) if len(bits) > 1 and bits[1].endswith("w") else 0
                if w >= best:
                    best, url = w, bits[0]
            width = best
        url = url or a.get("data-src") or a.get("data-original") or a.get("data-lazy-src") or a.get("src") or ""
        if not url or url.startswith("data:"):
            return
        try:
            width = width or int(re.sub(r"\D", "", a.get("width", "")) or 0)
        except ValueError:
            width = 0
        if 0 < width < MIN_SOURCE_WIDTH // 2:
            return
        self.order += 1
        priority = 0 if self.depth_main else 1
        self.images.append((priority, -width, self.order, url))

    def handle_endtag(self, tag):
        if tag in ("article", "main") and self.depth_main:
            self.depth_main -= 1


def source_candidates(page_url: str, known: str) -> list[str]:
    """元記事の画像候補（og:image → 本文中の大きい画像の順）"""
    out: list[str] = []
    if known:
        out.append(known)
    if not page_url:
        return out
    try:
        data, _, final = http_get(page_url, accept="text/html,application/xhtml+xml", timeout=20, limit=6 * 1024 * 1024)
    except Exception as error:  # noqa: BLE001
        # ボット除けで弾かれるページは、読み取りサービス（r.jina.ai）で描画後の HTML を取る（feeds.py と同じ）
        try:
            req = urllib.request.Request(
                "https://r.jina.ai/" + page_url,
                headers={"User-Agent": BOT_UA, "X-Return-Format": "html", **({"Authorization": f"Bearer {os.environ['JINA_API_KEY']}"} if os.environ.get("JINA_API_KEY") else {})},
            )
            with urllib.request.urlopen(req, timeout=45) as res:
                data = res.read(6 * 1024 * 1024)
            final = page_url
        except Exception as error2:  # noqa: BLE001
            log(f"   [img] 元記事を開けない: {str(error)[:60]} / {str(error2)[:60]}")
            return out
    head = data[:4000].decode("ascii", "ignore")
    match = re.search(r"charset=[\"']?([\w-]+)", head, re.I)
    text = ""
    for enc in [match.group(1) if match else None, "utf-8", "cp932", "euc-jp"]:
        if not enc:
            continue
        try:
            text = data.decode(enc)
            break
        except (LookupError, UnicodeDecodeError):
            continue
    parser = _ImageCollector()
    try:
        parser.feed(text or data.decode("utf-8", "ignore"))
    except Exception:  # noqa: BLE001
        pass
    # 出典の表示用（プレスリリース配信の記事は、発表した会社の名前を題名・構造化データから取る）
    raw = text or ""
    title = re.search(r"<title[^>]*>([^<]{1,300})</title>", raw, re.I)
    author = re.search(r'"author"\s*:\s*\{[^{}]{0,200}?"name"\s*:\s*"([^"]{1,60})"', raw)
    PAGE_META[page_url] = {
        "title": html.unescape(title.group(1)).strip() if title else "",
        "author": html.unescape(author.group(1)).strip() if author else "",
    }
    for key in ("og:image", "og:image:secure_url", "og:image:url", "twitter:image", "twitter:image:src", "image_src"):
        if parser.meta.get(key):
            out.append(parser.meta[key])
    for _, _, _, url in sorted(parser.images)[:8]:
        out.append(url)
    seen, result = set(), []
    for url in out:
        full = urllib.parse.urljoin(final, html.unescape(url.strip()))
        if full not in seen and full.startswith("http"):
            seen.add(full)
            result.append(full)
    return result


def pick_source_image(article: dict, repeats: Counter) -> tuple[str | None, object, str]:
    """(使える元記事の画像URL, その画像, 使わなかった理由)"""
    current = article.get("thumbnail", "") or ""
    reasons = []
    page = article.get("sourceUrl", "") if article.get("sourceType") == "web" else ""
    # 一覧に同じ写真が並ばないよう、ほかの記事で使っていない画像を優先する（無ければ使用済みでも使う）
    fallback = None
    fallback_image = None
    for index, url in enumerate(source_candidates(page, current)[:6]):
        used = repeats.get(url, 0) - (1 if url == current else 0)
        problem = url_problem(url, repeats.get(url, 0))
        # 別の発表（元記事のページが違う）と同じ画像は、サイト共通・使い回しの画像なので使わない
        others = PAGES_BY_IMAGE.get(url, set()) - {page, ""}
        if not problem and page and others:
            problem = "別の発表と同じ画像"
        if problem:
            reasons.append(problem)
            continue
        if used > 0 and fallback:
            continue
        try:
            data, ctype, _ = http_get(url, accept="image/avif,image/webp,image/*,*/*;q=0.8", referer=page, timeout=20)
            if ctype and not ctype.startswith("image/") and "octet-stream" not in ctype:
                reasons.append("画像ではない")
                continue
            image = open_image(data)
            problem = image_problem(image, MIN_SOURCE_WIDTH) or graphic_problem(image)
        except Exception as error:  # noqa: BLE001
            problem = f"読めない（{str(error)[:40]}）"
        if problem:
            reasons.append(problem)
            continue
        if used > 0:
            # 2本までは同じ発表の写真を共有してよい。3本目からは素材・AI で変化を付ける
            if used < 2:
                fallback, fallback_image = url, image
            else:
                reasons.append(f"ほかの{used}本と同じ写真")
            continue
        repeats[url] += 0 if url == current else 1
        return url, image, ""
    if fallback:
        repeats[fallback] += 0 if fallback == current else 1
        return fallback, fallback_image, ""
    return None, None, " / ".join(dict.fromkeys(reasons)) or "候補なし"


# --------------------------------------------------------------------------- #
# 検索語（軽量モデル）
# --------------------------------------------------------------------------- #

QUERY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "queries": {"type": "ARRAY", "items": {"type": "STRING"}},
        "query_ja": {"type": "STRING"},
        "scene": {"type": "STRING"},
    },
    "required": ["queries", "scene"],
}

# 記事づくりと Gemini の無料枠を取り合わないよう、Workers AI（1本あたり0.1円未満）→ Gemma だけを使う（画像判定も Workers AI）。
# Claude は使わない（オーナーの枠のため）
IMAGE_LLM_PREFER = [m.strip() for m in (os.environ.get("IMAGES_LLM_MODELS") or "cloudflare:@cf/openai/gpt-oss-120b,cloudflare:@cf/meta/llama-3.3-70b-instruct-fp8-fast,gemma-4-26b-a4b-it,gemma-4-31b-it").split(",") if m.strip()]


def llm_tier() -> str:
    if "images" not in llm._tiers:
        bulk = llm.models_for("bulk")
        prefer = [m for m in IMAGE_LLM_PREFER if m in bulk]
        # 記事づくりの Gemini（flash 系）の枠と Claude は使わない
        rest = [m for m in bulk if m not in prefer and m.startswith(("groq:", "openrouter:", "cloudflare:"))]
        llm._tiers["images"] = prefer + rest
    return "images"


QUERY_SYSTEM = (
    "You are a photo editor at a Japanese news site. For each article you choose a stock photo for the thumbnail. "
    "Return JSON only."
)


def visual_plan(article: dict) -> dict:
    prompt = f"""Article (Japanese):
title: {article.get('title', '')}
summary: {article.get('description', '')}
genre: {article.get('genre', '')}
tags: {', '.join(article.get('tags', [])[:8])}
source: {article.get('sourceName') or article.get('channel', '')}

Return:
- "queries": 3 short English search queries (1-3 words each) for a free photo library such as Wikimedia Commons.
  Concrete, photographable nouns that a viewer would connect with the topic: places, objects, scenes, activities
  (e.g. "rice paddy", "evacuation shelter", "beverage cans", "server room", "tokyo skyline", "hospital corridor").
  Order from most specific to most generic. Add "japan" only when the place matters.
  NEVER use company names, brand or product names, logos, names of real people, or abstract words (innovation, policy, strategy, future).
- "query_ja": one Japanese search phrase (1-2 words) of the same kind.
- "scene": one English sentence describing a neutral, photorealistic editorial news photo for this article
  (subject, setting, light). No text, no signs, no logos, no brand names, no identifiable real people
  (people only from behind, as hands, or out of focus)."""
    data, model = llm.generate_json(QUERY_SYSTEM, prompt, QUERY_SCHEMA, tier=llm_tier(), temperature=0.3, max_output_tokens=2048)
    queries = []
    for q in data.get("queries") or []:
        q = re.sub(r"[^\w\s'-]", " ", str(q)).strip()
        q = re.sub(r"\s+", " ", q)
        if q and len(q) <= 40 and q.lower() not in queries:
            queries.append(q.lower())
    return {
        "queries": queries[:3],
        "query_ja": str(data.get("query_ja") or "").strip()[:20],
        "scene": str(data.get("scene") or "").strip()[:400],
        "model": model,
    }


# --------------------------------------------------------------------------- #
# 2. フリー素材
# --------------------------------------------------------------------------- #


def strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def normalize_license(name: str) -> str:
    name = (name or "").strip()
    low = name.lower()
    if low in ("cc0", "cc-0", "cc zero") or low.startswith("cc0"):
        return "CC0"
    if low.startswith("public domain") or low in ("pd", "pdm") or low.startswith("pd-"):
        return "Public Domain"
    match = re.match(r"^cc[ -]by[ -](\d(?:\.\d)?)(?:[ -]([a-z]+))?$", low)
    if match:
        port = f" {match.group(2).upper()}" if match.group(2) and match.group(2) not in ("sa",) else ""
        if match.group(2) == "sa":
            return ""
        return f"CC BY {match.group(1)}{port}"
    if re.match(r"^by$", low):
        return "CC BY"
    return ""


_wiki_failures = 0
_wiki_last = 0.0
_api_down_until = 0.0
_rest_down_until = 0.0
WIKI_INTERVAL = float(os.environ.get("WIKI_INTERVAL") or "2")


def wiki_get(url: str, tries: int = 2) -> dict:
    """Wikimedia への問い合わせ。続けて叩くと締め出されるので間を空け、混雑（429・5xx）は1回だけ待ってやり直す"""
    global _wiki_last
    for attempt in range(tries):
        time.sleep(max(0.0, WIKI_INTERVAL - (time.time() - _wiki_last)))
        _wiki_last = time.time()
        try:
            return http_json(url, timeout=25)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
            time.sleep(min(int(error.headers.get("Retry-After") or 0) or 5, 15))
    raise RuntimeError("unreachable")


def search_wikimedia_rest(search: str, limit: int, query: str, quality: bool) -> list[dict] | None:
    """api.wikimedia.org の REST 検索。題名と縦横比だけ分かるので、ライセンスと作者は選んだ後に wiki_license で確かめる"""
    global _rest_down_until
    if time.time() < _rest_down_until:
        return None
    url = "https://api.wikimedia.org/core/v1/commons/search/page?" + urllib.parse.urlencode({"q": search, "limit": str(limit)})
    try:
        data = wiki_get(url, tries=1)
    except Exception as error:  # noqa: BLE001
        log(f"   [img] Wikimedia REST にも失敗: {str(error)[:60]}")
        _rest_down_until = time.time() + 120
        return None
    out = []
    for page in data.get("pages") or []:
        key = page.get("key") or ""
        thumb = page.get("thumbnail") or {}
        excerpt = strip_tags(page.get("excerpt") or "")
        if not key.startswith("File:") or not thumb.get("url") or re.search(r"share ?alike|gfdl", excerpt, re.I):
            continue
        tw, th = int(thumb.get("width") or 0), int(thumb.get("height") or 0)
        title = key.removeprefix("File:").replace("_", " ")
        out.append(
            {
                "provider": "Wikimedia Commons",
                "title": re.sub(r"\.(jpe?g)$", "", title, flags=re.I),
                "description": excerpt[:200],
                "creator": "",
                "creatorUrl": "",
                "license": "",
                "licenseUrl": "",
                "pendingLicense": key,
                "sourceUrl": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(key)}",
                # 検索結果の小さなサムネイルの URL を 1280px 版に替える（元は幅1200px以上に絞っている）
                "download": re.sub(r"/\d+px-", "/1280px-", thumb["url"].split("?")[0]),
                # 縦横比だけ分かる（大きさは検索条件の filew で保証）
                "width": MIN_STOCK_WIDTH if tw and th else 0,
                "height": round(MIN_STOCK_WIDTH * th / tw) if tw and th else 0,
                "query": query,
                "quality": quality,
            }
        )
    return out


def wiki_license(key: str) -> dict | None:
    """ファイルページの wikitext からライセンスと作者を読む（CC0・PD・CC BY だけ。SA・GFDL・ロゴ類は None）"""
    try:
        data = wiki_get(f"https://api.wikimedia.org/core/v1/commons/page/{urllib.parse.quote(key)}")
    except Exception:  # noqa: BLE001
        return None
    text = str(data.get("source") or "")
    low = text.lower()
    if re.search(r"pd-textlogo|pd-shape|trademarked|non-free|copyvio|delete", low):
        return None
    license_name, license_url = "", ""
    if re.search(r"\{\{\s*(cc-zero|cc0)\b|\|\s*cc-zero\b", low):
        license_name, license_url = "CC0", "https://creativecommons.org/publicdomain/zero/1.0/"
    elif re.search(r"\{\{\s*(pd-|public domain)", low):
        license_name = "Public Domain"
    else:
        match = re.search(r"cc-by-(\d\.\d)\b", low)
        if match:
            license_name, license_url = f"CC BY {match.group(1)}", f"https://creativecommons.org/licenses/by/{match.group(1)}/"
    if not license_name:
        return None
    author = ""
    match = re.search(r"^\s*\|\s*author\s*=\s*(.+)$", text, re.I | re.M)
    if match:
        raw = match.group(1)
        raw = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", raw)  # [[User:X|名前]] → 名前
        raw = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", raw)  # [URL 名前] → 名前
        raw = re.sub(r"\{\{[^{}]*\}\}", "", raw)
        author = re.sub(r"\s+", " ", re.sub(r"['\[\]{}<>]", "", raw)).strip()[:80]
    if license_name.startswith("CC BY") and not author:
        return None
    return {"license": license_name, "licenseUrl": license_url, "creator": author or "不明"}


def search_wikimedia(query: str, limit: int = 10, quality: bool = False) -> list[dict]:
    """quality=True は Commons の「Quality images」（写りの審査を通ったもの）だけから探す。
    落ちている窓口はしばらく使わない（_api_down_until / _rest_down_until）"""
    global _wiki_failures
    search = f'{query} filetype:bitmap filemime:image/jpeg filew:>{MIN_STOCK_WIDTH - 1} -incategory:"Images with watermarks"'
    if quality:
        search += ' incategory:"Quality images"'
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "generator": "search",
        "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "gsrsearch": search,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": "1280",
        "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl|ImageDescription|ObjectName|Restrictions|AttributionRequired",
    }
    data = None
    global _api_down_until
    if time.time() >= _api_down_until:
        try:
            data = wiki_get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params), tries=1)
        except Exception as error:  # noqa: BLE001
            # 本体の API（action API）が落ちていることがある。5分は REST の窓口で探す（ライセンスは選んだ後に確かめる）
            log(f"   [img] Wikimedia API に失敗（{str(error)[:40]}）→ しばらく REST で探す")
            _api_down_until = time.time() + 300
    if data is None:
        rest = search_wikimedia_rest(search, limit, query, quality)
        if rest is None:
            _wiki_failures += 1
            return []
        _wiki_failures = 0
        return rest
    _wiki_failures = 0
    pages = sorted((data.get("query") or {}).get("pages") or [], key=lambda p: p.get("index", 0))
    out = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        value = lambda k: (meta.get(k) or {}).get("value", "")  # noqa: E731
        license_name = normalize_license(strip_tags(value("LicenseShortName")))
        if not license_name or strip_tags(value("Restrictions")):
            continue
        title = page.get("title", "").removeprefix("File:")
        out.append(
            {
                "provider": "Wikimedia Commons",
                "title": re.sub(r"\.(jpe?g)$", "", title, flags=re.I).replace("_", " "),
                "description": strip_tags(value("ImageDescription"))[:200],
                "creator": strip_tags(value("Artist"))[:80] or "不明",
                "creatorUrl": "",
                "license": license_name,
                "licenseUrl": strip_tags(value("LicenseUrl")),
                "sourceUrl": info.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(page.get('title', ''))}",
                "download": info.get("thumburl") or info.get("url", ""),
                "width": int(info.get("width") or 0),
                "height": int(info.get("height") or 0),
                "query": query,
                "quality": quality,
            }
        )
    return out


_openverse_down = False


def search_openverse(query: str, limit: int = 12) -> list[dict]:
    """Openverse（Flickr など）。鍵なしの匿名利用（1日の上限は小さいので失敗したらその回は使わない）"""
    global _openverse_down
    if _openverse_down:
        return []
    params = {
        "q": query,
        "license": "cc0,pdm,by",
        "category": "photograph",
        "aspect_ratio": "wide",
        "size": "large",
        "mature": "false",
        "excluded_source": "wikimedia",
        "page_size": str(limit),
    }
    try:
        data = http_json("https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(params), timeout=15)
    except Exception as error:  # noqa: BLE001
        log(f"   [img] Openverse に失敗（この回は使わない）: {str(error)[:80]}")
        _openverse_down = True
        return []
    out = []
    for item in data.get("results") or []:
        license_name = {"cc0": "CC0", "pdm": "Public Domain", "by": f"CC BY {item.get('license_version') or ''}".strip()}.get(item.get("license", ""))
        if not license_name or not item.get("url"):
            continue
        # Flickr の中サイズ（_b=1024px 以下）は幅が足りない
        if re.search(r"staticflickr\.com/.+_[bczmnstq]\.jpg$", item["url"]) or "rawpixel" in str(item.get("source") or item.get("provider") or ""):
            continue
        out.append(
            {
                "provider": f"Openverse（{item.get('source') or item.get('provider') or ''}）".replace("（）", ""),
                "title": str(item.get("title") or "")[:120],
                "description": ", ".join(t.get("name", "") for t in (item.get("tags") or [])[:8]),
                "creator": str(item.get("creator") or "不明")[:80],
                "creatorUrl": item.get("creator_url") or "",
                "license": license_name,
                "licenseUrl": item.get("license_url") or "",
                "sourceUrl": item.get("foreign_landing_url") or "",
                "download": item["url"],
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
                "query": query,
            }
        )
    return out


def stock_ok(item: dict, used: set[str]) -> bool:
    w, h = item["width"], item["height"]
    if item["sourceUrl"] in used:
        return False
    if w and h and (w < MIN_STOCK_WIDTH or not (1.25 <= w / h <= 2.4)):
        return False
    if BAD_STOCK_TITLE.search(f"{item['title']} {item['description'][:80]}") or PEOPLE_TITLE.search(item["title"]):
        return False
    return True


VISION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "related": {"type": "BOOLEAN"},
        "brand_or_text": {"type": "BOOLEAN"},
        "person_main": {"type": "BOOLEAN"},
        "why": {"type": "STRING"},
    },
    "required": ["related", "brand_or_text", "person_main"],
}


def doc_stats(image) -> dict:
    """資料・スライド・グラフ・文字バナーの目印。white=白っぽい地の割合、flat=隣と同じ明るさの画素の割合（写真はざらつく）"""
    small = image.resize((320, 168))
    px = small.tobytes()
    n = len(px) // 3
    white = 0
    for i in range(0, len(px), 3):
        lo, hi = min(px[i], px[i + 1], px[i + 2]), max(px[i], px[i + 1], px[i + 2])
        if lo > 215 and hi - lo < 25:
            white += 1
    gray = small.convert("L")
    dx = ImageChops.difference(gray, ImageChops.offset(gray, 1, 0))
    dy = ImageChops.difference(gray, ImageChops.offset(gray, 0, 1))
    diff = ImageChops.lighter(dx, dy).tobytes()
    flat = sum(1 for v in diff if v <= 2) / len(diff)
    return {"white": white / n, "flat": flat}


VISION_MODELS = ["@cf/meta/llama-4-scout-17b-16e-instruct", "@cf/mistralai/mistral-small-3.1-24b-instruct"]


def cf_vision(image, prompt: str, max_tokens: int = 60) -> str | None:
    """Workers AI の画像を読めるモデルに聞く（記事づくりの Gemini の枠を使わない）。答えの文字列か None"""
    token = cf_token("ai")
    if not token:
        return None
    small = image.copy()
    small.thumbnail((512, 512))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=80)
    uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    for model in VISION_MODELS:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": uri}}]}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                data = json.loads(res.read())
            text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if text:
                return text
        except Exception as error:  # noqa: BLE001
            log(f"   [img] 画像判定 {model.rsplit('/', 1)[-1]}: {str(error)[:80]}")
    return None


def vision_kind(image) -> str | None:
    """写真（人物・製品・場所・会場・食べ物）か、文字や図が主役の画像（資料・スライド・グラフ・表・図解・画面・文字バナー）か。
    判定できないときは None"""
    text = cf_vision(
        image,
        'Is this image mainly a "photo" (a photograph or realistic render of real people, products, places, venues, food, '
        'nature or events; a small logo or a few words on it are fine) or a "graphic" (slide, chart, graph, table, document, '
        "infographic, diagram, screenshot, title card, logo card, or a flyer, poster, advertisement or banner with large headline text, prices or dates - even if it also contains photos of people or products)? Answer with one word: photo or graphic.",
        max_tokens=10,
    )
    low = (text or "").lower()
    if "graphic" in low:
        return "graphic"
    if "photo" in low:
        # 写真入りのチラシ・告知バナーは「写真」と答えがちなので、大きな文字の有無を別に聞く
        text = cf_vision(
            image,
            "Is a large part of this image covered by big overlaid text such as headlines, slogans, prices or dates "
            "(not small logos, product labels or signs in the scene)? Answer with one word: yes or no.",
            max_tokens=5,
        )
        return "graphic" if (text or "").strip().lower().startswith("yes") else "photo"
    log("   [img] 画像判定ができない（白地・平坦さの目安で判断）")
    return None


def graphic_problem(image) -> str | None:
    """文字・図が主役の画像なら理由。チラシや図解は色が多く Pillow の指標だけでは見分けられないので、毎回画像判定にかける。
    判定できないときだけ白地・平坦さの目安で決める"""
    stats = doc_stats(image)
    kind = vision_kind(image)
    if kind == "graphic":
        return f"文字・図が主役の画像（白地 {stats['white']:.0%}）"
    if kind is None and (stats["white"] >= 0.6 or stats["flat"] >= 0.8):
        return f"資料・図らしい画像（白地 {stats['white']:.0%}・平坦 {stats['flat']:.0%}）"
    return None


def vision_check(image, article: dict) -> str | None:
    """選んだ素材を画像で確かめる。問題があれば理由。確かめられないときは None（通す）"""
    text = cf_vision(
        image,
        f"News article title (Japanese): {article.get('title', '')}\n"
        "This photo will be the article thumbnail, labeled as an illustrative image. Answer in JSON only: "
        '{"related": true/false (is the photo clearly related to the article topic?), '
        '"brand_or_text": true/false (does it show a readable brand name, company logo, or prominent text/numbers?), '
        '"person_main": true/false (is an identifiable person face or any child a main subject?)}',
        max_tokens=80,
    )
    try:
        data = llm.parse_json_lenient(text or "")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or "related" not in data:
        return None
    if data.get("related") is False:
        return "記事と関係が薄い"
    if data.get("brand_or_text") is True:
        return "ブランド名・文字が写っている"
    if data.get("person_main") is True:
        return "人物が主役"
    return None


PICK_SCHEMA = {"type": "OBJECT", "properties": {"pick": {"type": "INTEGER"}, "why": {"type": "STRING"}}, "required": ["pick"]}


def choose_stock(article: dict, candidates: list[dict]) -> dict | None:
    """候補の題名・説明から、記事に合う写真を軽量モデルに1枚選ばせる（合うものが無ければ None）"""
    if not candidates:
        return None
    lines = "\n".join(f"{i}: {'[QI] ' if c.get('quality') else ''}{c['title']} — {c['description'][:140]}" for i, c in enumerate(candidates))
    prompt = f"""Japanese news article:
title: {article.get('title', '')}
summary: {article.get('description', '')}

Candidate photos (index: title — description):
{lines}

Pick the ONE photo that would look natural as the thumbnail of this article on a major news site.
Rules:
- clearly related scene or object (a reader should see the connection at a glance), a real photograph
- generic is better than specific: reject photos tied to a different organization, government agency, military,
  event, product brand or foreign landmark that is not in the article (it would mislead readers)
- a real modern photograph: no painting, artwork, map, document, logo or screenshot; no famous person as the subject, no visible brand logos
- "[QI]" marks photos that passed Wikimedia's quality review; prefer them when equally relevant
Return {{"pick": index}} or {{"pick": -1}} if none fits."""
    try:
        data, _ = llm.generate_json("You are a careful photo editor. Return JSON only.", prompt, PICK_SCHEMA, tier=llm_tier(), temperature=0.1, max_output_tokens=1024)
        index = int(data.get("pick", -1))
    except Exception as error:  # noqa: BLE001
        log(f"   [img] 選定に失敗、検索順の先頭を使う: {str(error)[:80]}")
        index = 0
    return candidates[index] if 0 <= index < len(candidates) else None


def find_stock(article: dict, plan: dict, used: set[str]) -> tuple[dict | None, object]:
    candidates: list[dict] = []
    seen = set()
    queries = plan["queries"] + ([plan["query_ja"]] if plan.get("query_ja") else [])
    def add(items: list[dict]) -> None:
        for item in items:
            if item["sourceUrl"] in seen or not stock_ok(item, used):
                continue
            seen.add(item["sourceUrl"])
            candidates.append(item)

    # 呼び出しを減らす: 審査済み（QI）は最初の検索語だけ、日本語の検索語は足りないときだけ
    for index, query in enumerate(queries):
        if index == len(plan["queries"]) and len(candidates) >= 6:
            break
        add((search_wikimedia(query, limit=6, quality=True) if index == 0 else []) + search_wikimedia(query))
        if len(candidates) >= 12:
            break
    # Openverse（Flickr など）は応答が遅く、実際の画像が小さいことも多いので、Commons で足りないときだけ
    if len(candidates) < 6:
        for query in plan["queries"]:
            add(search_openverse(query))
    if not candidates:
        return None, None
    # 同じ検索語の上位に偏らないよう、検索語ごとに交互に並べる
    by_query: dict[str, list[dict]] = {}
    for c in candidates:
        by_query.setdefault(c["query"], []).append(c)
    mixed = []
    while any(by_query.values()) and len(mixed) < 18:
        for q in list(by_query):
            if by_query[q]:
                mixed.append(by_query[q].pop(0))
    tried = 0
    while mixed and tried < 3:
        tried += 1
        pick = choose_stock(article, mixed)
        if not pick:
            return None, None
        try:
            if pick.get("pendingLicense"):
                info = wiki_license(pick["pendingLicense"])
                if not info:
                    raise ValueError("ライセンスを確かめられない（CC0・PD・CC BY 以外）")
                pick.update(info)
                pick.pop("pendingLicense", None)
            data, ctype, _ = http_get(pick["download"], ua=BOT_UA, accept="image/jpeg,image/*", timeout=40)
            image = open_image(data)
            problem = image_problem(image, MIN_STOCK_WIDTH) or graphic_problem(image) or vision_check(image, article)
            if problem:
                raise ValueError(problem)
            return pick, image
        except Exception as error:  # noqa: BLE001
            log(f"   [img] 素材を使えない（{str(error)[:60]}）: {pick['title'][:50]}")
            mixed.remove(pick)
    return None, None


# --------------------------------------------------------------------------- #
# 3. AI 生成（Workers AI）
# --------------------------------------------------------------------------- #

AI_MODELS = ["@cf/black-forest-labs/flux-2-klein-4b", "@cf/black-forest-labs/flux-1-schnell"]


def ai_used_today() -> int:
    return sum(n for model, n in llm.usage_today().items() if model.startswith("cf-image:"))


def ai_prompt(plan: dict, article: dict) -> str:
    scene = plan.get("scene") or ", ".join(plan.get("queries") or []) or article.get("genre", "")
    return (
        f"Photorealistic editorial news photograph. {scene} "
        "Natural muted colors, soft natural light, documentary style, shot on a full-frame camera with a 35mm lens, "
        "wide landscape composition with empty space in the lower third. "
        "No text, no letters, no numbers, no signs, no logos, no watermark, no brand names, no identifiable faces."
    )


def _cf_ai(model: str, prompt: str, timeout: int) -> bytes:
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{model}"
    headers = {"Authorization": f"Bearer {cf_token('ai')}"}
    if "flux-2" in model:
        boundary = uuid.uuid4().hex
        fields = {"prompt": prompt, "width": "1216", "height": "640"}
        body = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items())
        data = (body + f"--{boundary}--\r\n").encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    else:
        data = json.dumps({"prompt": prompt, "steps": 6}).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read()
        ctype = res.headers.get("Content-Type", "")
    if "json" in ctype:
        payload = json.loads(raw)
        image = (payload.get("result") or {}).get("image")
        if not image:
            raise RuntimeError(str(payload)[:200])
        return base64.b64decode(image)
    return raw


def generate_ai(plan: dict, article: dict, fast: bool = False) -> tuple[object, str] | None:
    if ai_used_today() >= AI_DAILY_MAX:
        log(f"   [img] AI 生成は今日の上限（{AI_DAILY_MAX}枚）に達した")
        return None
    if not cf_token("ai"):
        log("   [img] Workers AI のトークンが無いので AI 生成は飛ばす")
        return None
    prompt = ai_prompt(plan, article)
    models = AI_MODELS[1:] if fast else AI_MODELS
    for model in models:
        try:
            raw = _cf_ai(model, prompt, timeout=150 if "flux-2" in model else 60)
            image = open_image(raw)
            llm._record_usage("cf-image:" + model.rsplit("/", 1)[-1])
            return image, model.rsplit("/", 1)[-1]
        except Exception as error:  # noqa: BLE001
            detail = error.read()[:160] if isinstance(error, urllib.error.HTTPError) else str(error)[:160]
            log(f"   [img] {model} に失敗: {detail!r}")
    return None


# --------------------------------------------------------------------------- #
# 保存（KV）
# --------------------------------------------------------------------------- #


# 幅ごとの WebP（表示する枠の幅でブラウザが選ぶ srcset。lib/thumb-url.ts の ownSrcSet と同じ名前）
#   <名前>.w400.webp / .sm.webp（640。一覧用・従来どおり）/ .w800.webp / .w1200.webp。元の .jpg は SNS カード・構造化データ用
VARIANT_WIDTHS = (("w400", 400), ("sm", 640), ("w800", 800), ("w1200", 1200))


def _jpeg(image) -> bytes:
    jpeg = b""
    for quality in (84, 80, 76, 72, 68, 62, 56, 50, 44, 38):
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=quality, optimize=True, progressive=True, subsampling=2)
        jpeg = buf.getvalue()
        if len(jpeg) <= JPEG_MAX_BYTES:
            break
    return jpeg


def webp_variants(image) -> dict[str, bytes]:
    """幅 400 / 640 / 800 / 1200 の WebP（元より大きくはしない。縦横比はそのまま）"""
    out: dict[str, bytes] = {}
    w, h = image.size
    for name, width in VARIANT_WIDTHS:
        width = min(width, w)
        resized = image if width == w else image.resize((width, max(1, round(h * width / w))), Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, "WEBP", quality=74 if width <= 640 else 72, method=6)
        out[name] = buf.getvalue()
    return out


def render(image, keep_aspect: bool = False) -> tuple[bytes, dict[str, bytes]]:
    """JPEG（140KB 以下）と幅ごとの WebP。
    フリー素材・AI 生成は 1200×630 に切り抜く。公式発表の写真（keep_aspect）は切り抜かず、幅 1200 まで縮小するだけ"""
    if keep_aspect:
        w, h = image.size
        fitted = image if w <= OUT_W else image.resize((OUT_W, round(h * OUT_W / w)), Image.LANCZOS)
    else:
        fitted = ImageOps.fit(image, (OUT_W, OUT_H), method=Image.LANCZOS, centering=(0.5, 0.45))
    return _jpeg(fitted), webp_variants(fitted)


def store(slug: str, image, dry_run: bool, keep_aspect: bool = False) -> tuple[str, tuple[int, int]]:
    """KV に置いて (/img/<名前>.jpg の URL, (幅, 高さ)) を返す"""
    jpeg, variants = render(image, keep_aspect)
    key = f"{slug[:120]}-{hashlib.sha1(jpeg).hexdigest()[:8]}"
    if PREVIEW_DIR:
        (PREVIEW_DIR / f"{key}.jpg").write_bytes(jpeg)
    if not dry_run:
        kv_put(f"{key}.jpg", jpeg)
        for name, data in variants.items():
            kv_put(f"{key}.{name}.webp", data)
    size = open_image(jpeg).size
    sizes = " / ".join(f".{n}.webp {len(d) // 1024}KB" for n, d in variants.items())
    log(f"   [img] 保存 {key}.jpg（{size[0]}×{size[1]}・{len(jpeg) // 1024}KB）{sizes}" + ("（dry-run）" if dry_run else ""))
    return f"{IMAGE_BASE}{key}.jpg", size


# --------------------------------------------------------------------------- #
# 記事ごとの処理
# --------------------------------------------------------------------------- #


def load_articles() -> dict[Path, dict]:
    out = {}
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            out[path] = json.loads(path.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            continue
    return out


def newest_first(item: tuple[Path, dict]) -> str:
    article = item[1]
    return article.get("generatedAt") or article.get("publishedAt") or ""


def needs_check(article: dict) -> bool:
    """点検の対象か。YouTube のサムネイルがある動画記事・確認済みの記事は対象外"""
    if article.get("imageCredit") or article.get("noPhoto"):
        return False  # noPhoto: 地震・津波の記事（scripts/quake.py）。写真・イメージ画像を付けない
    thumb = article.get("thumbnail", "") or ""
    if is_youtube(thumb):
        return False
    return True


def age_hours(article: dict) -> float:
    try:
        made = datetime.fromisoformat(str(article.get("generatedAt") or article.get("publishedAt")).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - made).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        return 0.0


def save(path: Path, article: dict) -> None:
    path.write_text(json.dumps(article, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def process(path: Path, article: dict, ctx: dict) -> str:
    slug = article.get("slug") or path.stem
    title = (article.get("title") or "")[:40]
    thumb = article.get("thumbnail", "") or ""
    log(f"[img] {slug} | {title}")

    previous = article.get("imageCredit") or {}
    # 自前の画像（まとめ記事・別の切り口の記事が元記事の画像を引き継いだ）なら出典も引き継ぐ
    if is_ours(thumb) and not ctx.get("retry_ai"):
        credit = ctx["credit_by_thumb"].get(thumb)
        if credit:
            article["imageCredit"] = credit
            if not ctx["dry_run"]:
                save(path, article)
            return "inherit"

    # 1. 元記事の写真。公式の発表（会社・団体の自社サイト、プレスリリース配信）だけ使い、縮小して自前の KV から配信する。
    #    ほかのニュースサイト・ブログ・SNS の写真は保存も直リンクもしない（AI 画像の探し直しでは検査済みなので飛ばす）
    official = official_source(article)
    if not ctx.get("retry_ai") and official is None:
        log("   [img] 公式の発表ではないので元記事の写真は使わない")
    elif not ctx.get("retry_ai"):
        url, image, why = pick_source_image(article, ctx["repeats"])
        if url and image is not None:
            org = wire_org(article) if official["wire"] else org_name(article)
            reuse = ctx["official_by_original"].get(url)
            if reuse:
                new_url, size = reuse["thumbnail"], (reuse["credit"].get("width"), reuse["credit"].get("height"))
                log(f"   [img] 公式発表の写真（保存済みを使い回す）: {new_url}")
            else:
                log(f"   [img] 公式発表の写真を保存して使う: {url[:100]}")
                try:
                    new_url, size = store(slug, image, ctx["dry_run"], keep_aspect=True)
                except Exception as error:  # noqa: BLE001
                    log(f"   [img] 保存に失敗: {str(error)[:120]}")
                    return "error"
            credit = {
                "kind": "official",
                "org": org or official["wire"],
                "provider": article.get("sourceName") or article.get("channel", ""),
                "sourceUrl": article.get("sourceUrl", ""),
                "original": url,
                "width": size[0],
                "height": size[1],
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            if official["wire"]:
                credit["via"] = official["wire"]
            article["thumbnail"] = new_url
            article["imageCredit"] = credit
            ctx["official_by_original"][url] = {"thumbnail": new_url, "credit": credit}
            if not ctx["dry_run"]:
                save(path, article)
            return "official"
        log(f"   [img] 元記事の写真は使わない: {why}")

    # 2. フリー素材 → 3. AI 生成
    try:
        plan = visual_plan(article)
    except Exception as error:  # noqa: BLE001
        log(f"   [img] 検索語を作れない: {str(error)[:100]}")
        return "error"
    log(f"   [img] 検索語: {plan['queries']} / {plan.get('query_ja')} ({plan['model']})")

    credit: dict = {}
    image = None
    if not ctx.get("ai_only"):
        pick, image = find_stock(article, plan, ctx["used_stock"])
        if pick and image is not None:
            credit = {
                "kind": "stock",
                "title": pick["title"],
                "creator": pick["creator"],
                "creatorUrl": pick["creatorUrl"],
                "license": pick["license"],
                "licenseUrl": pick["licenseUrl"],
                "provider": pick["provider"],
                "sourceUrl": pick["sourceUrl"],
                "query": pick["query"],
            }
            ctx["used_stock"].add(pick["sourceUrl"])
            log(f"   [img] 素材: {pick['title'][:60]}（{pick['creator'][:30]} / {pick['license']}）")
    if image is None and _wiki_failures > 0 and not ctx.get("fast") and not ctx.get("ai_only") and age_hours(article) < ctx["defer_hours"]:
        # Commons が落ちているだけなら AI の枠を使わず、次の回に探し直す（速報の回と、何時間も直らないときは AI）
        log("   [img] Wikimedia Commons に繋がらないので次の回に回す")
        return "deferred"
    if image is None and ctx.get("retry_ai"):
        log("   [img] 素材が見つからないので AI 生成の画像のまま")
        return "kept"
    if image is None:
        made = generate_ai(plan, article, fast=ctx.get("fast", False))
        if not made:
            return "none"
        image, model = made
        credit = {"kind": "ai", "model": model, "prompt": plan.get("scene", "")[:300], "provider": "Cloudflare Workers AI"}
        log(f"   [img] AI 生成: {model}")

    try:
        new_url, _ = store(slug, image, ctx["dry_run"])
    except Exception as error:  # noqa: BLE001
        log(f"   [img] 保存に失敗: {str(error)[:120]}")
        return "error"
    if previous.get("original"):
        credit["original"] = previous["original"]
    elif thumb and thumb != new_url and not is_ours(thumb):
        credit["original"] = thumb
    credit["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    article["thumbnail"] = new_url
    article["imageCredit"] = credit
    if not ctx["dry_run"]:
        save(path, article)
    return credit["kind"]


def load_env_local() -> None:
    """手元実行用: .env.local の鍵を読む（git worktree の中なら親のリポジトリのものも探す）"""
    for base in [ROOT, *ROOT.parents]:
        path = base / ".env.local"
        if not path.exists():
            continue
        for line in path.read_text("utf-8").splitlines():
            match = re.match(r"^(GEMINI_API_KEY|GOOGLE_API_KEY|GROQ_API_KEY|OPENROUTER_API_KEY)=(.+)$", line.strip())
            if match and not os.environ.get(match.group(1)):
                os.environ[match.group(1)] = match.group(2).strip().strip("\"'")
        break
    # 手元では wrangler のログインで Workers AI（検索語づくり・画像生成）を使う
    if not os.environ.get("CLOUDFLARE_API_TOKEN") and wrangler_token():
        os.environ["CLOUDFLARE_API_TOKEN"] = wrangler_token()
        os.environ.setdefault("CLOUDFLARE_ACCOUNT_ID", ACCOUNT_ID)


def changed_paths() -> set[Path]:
    """このワークツリーで追加・変更された記事（git status）"""
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", "content/articles"], cwd=ROOT, capture_output=True, text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return set()
    paths = set()
    for line in out.splitlines():
        name = line[3:].strip().strip('"')
        if name.endswith(".json"):
            paths.add((ROOT / name).resolve())
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--changed", action="store_true", help="追加・変更された記事だけ")
    parser.add_argument("--recent", type=int, default=0, help="新しい N 本のうち未確認のもの")
    parser.add_argument("--all", action="store_true", help="全記事（新しい順）")
    parser.add_argument("--slug", action="append", default=[], help="この記事だけ（複数可）")
    parser.add_argument("--max", type=int, default=20, help="1回で直す最大本数")
    parser.add_argument("--budget", type=float, default=0, help="この秒数を過ぎたら新しい記事に手を付けない")
    parser.add_argument("--fast", action="store_true", help="速報の回: AI は速いモデルだけ")
    parser.add_argument("--ai-only", action="store_true", help="素材を探さず AI 生成だけ（確認用）")
    parser.add_argument("--recheck-source", action="store_true", help="元記事の写真を採用した記事を今の基準で見直す")
    parser.add_argument("--retry-ai", action="store_true", help="AI 生成の画像の記事でフリー素材を探し直す（見つからなければそのまま）")
    parser.add_argument("--cache-official", action="store_true", help="外部の写真を直リンクしている記事を直す（公式発表の写真は自前に保存、それ以外はフリー素材・AI 生成に）")
    parser.add_argument("--variants", action="store_true", help="前からある自前の写真に幅ごとの WebP を足す")
    parser.add_argument("--dry-run", action="store_true", help="保存しない")
    parser.add_argument("--preview", help="出来上がった JPEG をこのフォルダにも書く（確認用）")
    parser.add_argument("--shard", help="並列実行の分担（例: 0/3）")
    parser.add_argument("--defer-hours", type=float, default=3, help="Commons に繋がらないとき、作成からこの時間までは AI にせず次の回に回す")
    args = parser.parse_args()
    global PREVIEW_DIR
    if args.preview:
        PREVIEW_DIR = Path(args.preview)
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    load_env_local()
    started = time.time()

    articles = load_articles()
    repeats: Counter = Counter()
    for a in articles.values():
        for url in {a.get("thumbnail") or "", (a.get("imageCredit") or {}).get("original") or ""}:
            if url and not is_youtube(url) and not is_ours(url):
                repeats[url] += 1
                if a.get("sourceType") == "web":
                    PAGES_BY_IMAGE.setdefault(url, set()).add(a.get("sourceUrl") or "")
    ctx = {
        "repeats": repeats,
        "used_stock": {(a.get("imageCredit") or {}).get("sourceUrl") for a in articles.values() if (a.get("imageCredit") or {}).get("kind") == "stock"},
        "credit_by_thumb": {a["thumbnail"]: a["imageCredit"] for a in articles.values() if a.get("imageCredit") and is_ours(a.get("thumbnail", ""))},
        # 保存済みの公式発表の写真（元の URL → 自前の URL）。同じ写真を記事ごとに保存し直さない
        "official_by_original": {
            a["imageCredit"]["original"]: {"thumbnail": a["thumbnail"], "credit": a["imageCredit"]}
            for a in articles.values()
            if (a.get("imageCredit") or {}).get("kind") == "official" and a["imageCredit"].get("original") and is_ours(a.get("thumbnail", ""))
        },
        "dry_run": args.dry_run,
        "fast": args.fast,
        "ai_only": args.ai_only,
        "defer_hours": args.defer_hours,
        "retry_ai": args.retry_ai,
    }

    if args.variants:
        tally = backfill_variants(articles, args.dry_run, args.max, args.budget, started)
        log(f"[img] 結果: {dict(tally)}")
        return 0

    ordered = sorted(articles.items(), key=newest_first, reverse=True)
    if args.slug:
        targets = [(p, a) for p, a in ordered if a.get("slug") in args.slug or p.stem in args.slug]
        for _, a in targets:
            a.pop("imageCredit", None)
    elif args.recheck_source:
        # 元記事の写真を採用した記事を、今の基準（文字・図が主役の画像は不採用）で見直す
        targets = [(p, a) for p, a in ordered if (a.get("imageCredit") or {}).get("kind") == "source"]
        for _, a in targets:
            a.pop("imageCredit", None)
    elif args.cache_official:
        # 外部のサイトの写真を直リンクしている記事（YouTube のサムネイル・地震の記事は除く）
        targets = [
            (p, a)
            for p, a in ordered
            if a.get("thumbnail") and not is_ours(a["thumbnail"]) and not is_youtube(a["thumbnail"]) and not a.get("noPhoto")
        ]
        for _, a in targets:
            a.pop("imageCredit", None)
    elif args.retry_ai:
        targets = [(p, a) for p, a in ordered if (a.get("imageCredit") or {}).get("kind") == "ai"]
    elif args.changed or args.recent or args.all:
        # 追加・変更された記事を先に、次に新しい順の未確認の記事（前の回で直し損ねたもの）
        changed = changed_paths() if args.changed else set()
        first = [(p, a) for p, a in ordered if p.resolve() in changed]
        rest = ordered if args.all else ordered[: args.recent]
        seen: set[Path] = set()
        targets = []
        for p, a in first + rest:
            if p not in seen and needs_check(a):
                seen.add(p)
                targets.append((p, a))
    else:
        parser.error("--changed / --recent / --all / --slug のどれかを指定してください")

    if args.shard:
        # さかのぼりを並列に流すとき用（"0/3" なら 0,3,6…番目）
        index, total = (int(x) for x in args.shard.split("/"))
        targets = [t for i, t in enumerate(targets) if i % total == index]
    log(f"[img] 対象 {len(targets)} 本（最大 {args.max} 本）")
    tally: Counter = Counter()
    for path, article in targets:
        if sum(tally.values()) >= args.max:
            break
        if args.budget and time.time() - started > args.budget:
            log("[img] 時間切れ。残りは次の回に回す")
            break
        try:
            tally[process(path, article, ctx)] += 1
        except Exception as error:  # noqa: BLE001 - 1本の失敗で全体を止めない
            log(f"   [img] 失敗: {str(error)[:160]}")
            tally["error"] += 1
    log(f"[img] 結果: {dict(tally)}（AI 生成は今日 {ai_used_today()} / {AI_DAILY_MAX} 枚）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
