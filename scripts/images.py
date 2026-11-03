#!/usr/bin/env python3
"""記事の写真を選ぶ。ニュースと関係のある本物の画像を優先し、関係の無い素材写真・AI のイメージは使わない（2026-11 見直し）。

  1. 公式の発表の画像（会社・団体の自社サイト、PR TIMES などのプレスリリース配信の記事だけ）。og:image だけでなく、
     本文の写真・図、その会社の公式 YouTube 動画のサムネイル、本文から張られたプレスキットまで見て、
     写真（製品・人物・会場・調印式）> 公式動画 > プレスキットの写真 > 見出し画像・キービジュアル の順に選ぶ。
     ダウンロードして幅 1200px まで縮小（切り抜かない）し、自前の KV から配信する。記事には「画像：◯◯（公式発表より）」と出す。
     ほかの報道機関・通信社（ロイター・AP・Getty・日経・ITmedia など）の写真、他人の SNS の画像、関連記事のカード・メニュー・
     古い宣伝画像は使わない。ほかのニュースサイト・ブログ・不明な発信元の記事では元記事の画像を使わない
  2. AI 生成: Cloudflare Workers AI（flux-2-klein-4b → flux-1-schnell）。前からの写真風のプロンプト。話題を問わない。
     文字・ロゴ・実在の人物なし。1日 IMAGE_AI_DAILY_MAX 枚まで。
     製品の発表の記事（product_launch。2026-11-03）は製品そのもの・見間違える物を描かない（使う場面・会場の雰囲気・抽象）。
     描いてしまったら作り直し、だめなら AI 画像は使わない。前に付けた公式の写真は、選び直しで AI・素材に替えない
  3. フリー素材: Wikimedia Commons と Openverse（CC0・パブリックドメイン・CC BY だけ）。AI が使えないとき。関係の確認は厳しめ
  4. 公式のロゴ・文字の多い図・バナー（どれも無ければ今までどおり写真なしのカバー）

2・3 の画像は 1200×630 の JPEG（約100KB）、1 は縦横比そのままの幅 1200px までの JPEG にし、どれも幅 400 / 640（.sm）/ 800 / 1200 の
WebP を添えて Workers KV（saisoku-images）へ置き、サイトの /img/<名前> で配信する（cloudflare/worker.ts。srcset は lib/thumb-url.ts）。
記事 JSON には thumbnail（画像の URL）と imageCredit（出典・ライセンス・作者）を書く。
YouTube のサムネイルはそのまま使う。

  python3 scripts/images.py --changed            # このワークツリーで増えた・変わった記事だけ（auto_run.yml）
  python3 scripts/images.py --recent 40          # 新しい40本のうち未確認のもの
  python3 scripts/images.py --all --max 50       # 全記事（新しい順）を点検して直す（さかのぼり）
  python3 scripts/images.py --slug <slug> --dry-run
  python3 scripts/images.py --recheck-source        # 元記事の写真を採用した記事を今の基準で見直す
  python3 scripts/images.py --repick-hours 72 --max 60  # 新しい記事の素材・AI の画像を今の基準で選び直す
  python3 scripts/images.py --product-repick-days 7     # 製品の発表の記事の AI・素材の画像を選び直す（公式の製品写真→製品を描かない場面）
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
import image_focus  # noqa: E402
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
# 1. 元記事の写真（公式の発表だけ）
# --------------------------------------------------------------------------- #
# 2026-11 見直し: og:image だけで決めず、元記事の本文の写真・図、公式の YouTube 動画のサムネイル、本文から張られた
# プレスキットまで見て、いちばん良いものを選ぶ。
#   photo（製品・人物・会場・調印式などの写真）> youtube（その会社の公式チャンネルの動画）> kit（プレスキットの写真）
#   > hero / banner（ブログの見出し画像・キービジュアル）> dense（文字の多い図・スライド・表）> logo（ロゴだけ）
# ほかの報道機関・通信社（ロイター・AP・Getty・共同・時事・日経・ITmedia など）の写真や、他人の SNS 投稿の画像は使わない。

# 写真の種類の順位（小さいほど良い）。数字の図（figure）と素材（stock）は process() で間に入る。
# generic はサイト共通の共有画像のうち写真・キービジュアルのもの（本社ビルなど。その発表だけの写真ではない）
OFFICIAL_RANK = {"photo": 0, "video": 1, "youtube": 1, "kit": 2, "hero": 3, "banner": 4, "dense": 5, "generic": 6, "logo": 7}
MIN_PHOTO_WIDTH = 560  # 元記事の写真（実写）。755px の発表写真（日産の調印式など）や 600px の製品写真を落とさない（拡大はしない）
MIN_GRAPHIC_WIDTH = 700  # 見出し画像・キービジュアル
MIN_DENSE_WIDTH = 800  # 文字の多い図（小さいと読めない）
MIN_LOGO_WIDTH = 150

# 通信社・報道機関・写真素材の配信元（公式の発表のページに載っていても使わない）
AGENCY_IMAGE_HOSTS = {
    "gettyimages.com", "gettyimages.co.jp", "reuters.com", "apnews.com", "ap.org", "afp.com", "afpbb.com", "jiji.com",
    "kyodonews.jp", "kyodonews.net", "nikkei.com", "itmedia.co.jp", "shutterstock.com", "istockphoto.com", "alamy.com",
    "aflo.com", "pixta.jp", "amanaimages.com", "sankei.com", "asahi.com", "yomiuri.co.jp", "mainichi.jp", "nhk.or.jp",
    "twimg.com", "fbcdn.net", "cdninstagram.com", "tiktokcdn.com",
}
AGENCY_CREDIT = re.compile(r"(getty ?images|reuters|ロイター|\bAP\b|\bAFP\b|共同通信|時事通信|アフロ|AFLO|pixta|shutterstock|istock)", re.I)

# 本文ではない場所（関連記事・おすすめ・ランキング・バナー・メニュー・共有ボタン）
NOT_BODY = re.compile(
    r"(related|recommend|ranking|pickup|pick-up|popular|sidebar|side-?nav|side-?bar|banner|bnr|breadcrumb|menu|share|sns|social|"
    r"footer|global-?nav|gnav|carousel|other-?news|more-?news|news-?list|newslist|latest|archive|widget|ad-|ads-|advert|"
    r"author-?(box|bio|card)|profile|subscribe|newsletter|cta|promo)",
    re.I,
)
BODY_HINT = re.compile(r"(article|entry|post|content|body|release|press|detail|main|story|text|news-?detail|hero|featured|kv|mv)", re.I)
VOID_TAGS = {"img", "br", "meta", "link", "input", "hr", "source", "area", "wbr", "col", "embed", "param", "track", "base"}
YOUTUBE_ID = re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?(?:[^\"'#]*&)?v=|live/)|youtu\.be/)([\w-]{11})")
KIT_TEXT = re.compile(
    r"(プレスキット|報道用素材|報道関係者向け素材|画像素材|写真素材|画像ダウンロード|写真ダウンロード|素材ダウンロード|画像・動画ダウンロード|"
    r"press ?kit|media ?kit|image gallery|photo gallery|media assets|image[_-]?download|media[_-]?download|photo[_-]?download|download[_-]?images?|press[_-]?images?)",
    re.I,
)
# 製品のページ（製品の発表の記事だけ、本文から張られた同じ会社のサイトの製品ページの写真も見る）
PRODUCT_LINK = re.compile(
    r"(製品(情報|サイト|ページ|詳細|紹介)|商品(情報|サイト|ページ|詳細|紹介)|特設サイト|スペシャルサイト|ブランドサイト|モデル(サイト|紹介)|"
    r"詳しくは|詳細はこちら|product ?(page|site|details?)|/products?/|/lineup/|/models?/)",
    re.I,
)


def safe_url(url: str) -> str:
    """日本語などを含む URL を、そのまま送れる形（%XX）にする（楽天の発表の画像名は日本語）"""
    return urllib.parse.quote(url, safe=":/?&=%#,;+~@!$'()*[]")


def _host_label_set(host: str) -> set[str]:
    skip = {"www", "com", "co", "jp", "net", "org", "or", "go", "ac", "ne", "lg", "global", "corp", "news", "blog", "blogs", "newsroom", "press", "jp-ja", "ja", "en"}
    return {h for h in (host or "").lower().split(".") if len(h) >= 4 and h not in skip}


class _PageCollector(HTMLParser):
    """元記事のページから、画像（本文の中か・図か・説明文）・YouTube の動画・プレスキットへのリンクを集める"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.images: list[dict] = []
        self.youtube: list[str] = []
        self.videos: list[str] = []  # 会社が自分で置いている動画ファイル（mp4・webm）
        self.links: list[tuple[str, str, bool]] = []  # (リンク先, 文字, 本文ではない場所か)
        self.stack: list[tuple[str, bool, bool]] = []  # (tag, 本文か, 本文ではない場所か)
        self.order = 0
        self._link: list | None = None
        self._a_href = ""  # 開いている <a> のリンク先（関連記事のカードの画像を見分ける）

    def _state(self) -> tuple[bool, bool, bool]:
        body = any(b for _, b, _ in self.stack)
        bad = any(x for _, _, x in self.stack)
        figure = any(t == "figure" for t, _, _ in self.stack)
        return body, bad, figure

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in {"og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"} and a.get("content"):
                self.meta.setdefault(key, a["content"].strip())
            if key in {"og:video", "og:video:url", "og:video:secure_url"} and re.search(r"\.(mp4|webm|mov)(\?|$)", a.get("content", ""), re.I):
                self.videos.append(a["content"].strip())
            return
        if tag == "link":
            if a.get("rel", "").lower() == "image_src" and a.get("href"):
                self.meta.setdefault("image_src", a["href"].strip())
            return
        cls = f"{a.get('class', '')} {a.get('id', '')} {a.get('role', '')}"
        if tag not in VOID_TAGS:
            in_body, _, _ = self._state()
            body = tag in ("article", "main") or bool(BODY_HINT.search(cls) and not NOT_BODY.search(cls))
            # <header> は本文（article）の中なら見出し画像があるので外さない
            bad = tag in ("nav", "footer", "aside", "form") or (tag == "header" and not in_body) or bool(NOT_BODY.search(cls))
            if tag in ("html", "body"):
                body, bad = False, False  # WordPress などは <body> にページ全体の飾りのクラス（menu・sidebar など）が付く
            self.stack.append((tag, body, bad))
        if tag == "iframe":
            match = YOUTUBE_ID.search(a.get("src") or a.get("data-src") or "")
            if match:
                self.youtube.append(match.group(1))
            return
        if tag == "a":
            href = a.get("href") or ""
            match = YOUTUBE_ID.search(href)
            _, bad, _ = self._state()
            if match and not bad:
                self.youtube.append(match.group(1))
            # 本文ではない場所（横の欄の「画像・動画 ダウンロード」など）のリンクも、プレスキットを探すために印を付けて集める
            self._link = [href, "", bad] if href else None
            self._a_href = href
            return
        if tag == "lite-youtube" or a.get("data-youtube-id") or a.get("videoid"):
            vid = a.get("videoid") or a.get("data-youtube-id") or ""
            if re.fullmatch(r"[\w-]{11}", vid):
                self.youtube.append(vid)
        if tag in ("video", "source"):
            _, bad, _ = self._state()
            src = a.get("src") or a.get("data-src") or ""
            if src and not bad and (tag == "video" or a.get("type", "").startswith("video/") or re.search(r"\.(mp4|webm|mov)(\?|$)", src, re.I)):
                self.videos.append(src)
                return
            if tag == "video":
                if a.get("poster") and not bad:
                    # 動画の表紙の画像（公式の静止画）も候補にする
                    self.order += 1
                    body, _, figure = self._state()
                    self.images.append({"url": a["poster"], "width": 0, "body": body, "bad": False, "figure": figure, "alt": "", "order": self.order, "hint": "", "link": ""})
                return
        if tag not in ("img", "source"):
            return
        if re.search(r"(logo|icon|avatar|btn|button|arrow|badge|qr)", f"{cls} {a.get('alt', '')}", re.I):
            kind_hint = "logo"
        else:
            kind_hint = ""
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
        if url.startswith("data:"):
            url, width = "", 0
        url = url or a.get("data-src") or a.get("data-original") or a.get("data-lazy-src") or a.get("src") or ""
        if not url or url.startswith("data:"):
            return
        try:
            width = width or int(re.sub(r"\D", "", a.get("width", "")) or 0)
        except ValueError:
            width = 0
        if 0 < width < MIN_LOGO_WIDTH:
            return
        body, bad, figure = self._state()
        self.order += 1
        self.images.append({"url": url, "width": width, "body": body, "bad": bad, "figure": figure, "alt": a.get("alt", "")[:120], "order": self.order, "hint": kind_hint, "link": self._a_href})

    def handle_data(self, data):
        if self._link is not None and len(self._link[1]) < 120:
            self._link[1] += data.strip()

    def handle_endtag(self, tag):
        if tag == "a":
            self._a_href = ""
        if tag == "a" and self._link is not None:
            self.links.append((self._link[0], self._link[1], self._link[2]))
            self._link = None
        if tag in VOID_TAGS:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def fetch_page(page_url: str) -> tuple[str, str]:
    """(HTML, 最終URL)。ボット除けで弾かれるページは読み取りサービス（r.jina.ai）で描画後の HTML を取る（feeds.py と同じ）"""
    try:
        data, _, final = http_get(page_url, accept="text/html,application/xhtml+xml", timeout=20, limit=6 * 1024 * 1024)
    except Exception as error:  # noqa: BLE001
        try:
            # feeds.fetch_jina: 鍵（JINA_API_KEY）が残高切れ（402）・無効（401）なら鍵なしの無料枠で読み直し、
            # robots.txt が禁じるページは読まない（以前は鍵つきで直接呼んでいたため、残高切れの間は公式の写真を探せなかった）
            import feeds  # noqa: PLC0415

            data, _ = feeds.fetch_jina(page_url, timeout=45, accept="text/html", return_format="html")
            final = page_url
        except Exception as error2:  # noqa: BLE001
            raise RuntimeError(f"元記事を開けない: {str(error)[:60]} / {str(error2)[:60]}") from error2
    head = data[:4000].decode("ascii", "ignore")
    match = re.search(r"charset=[\"']?([\w-]+)", head, re.I)
    for enc in [match.group(1) if match else None, "utf-8", "cp932", "euc-jp"]:
        if not enc:
            continue
        try:
            return data.decode(enc), final
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "ignore"), final


def parse_page(page_url: str) -> tuple[_PageCollector, str] | None:
    try:
        text, final = fetch_page(page_url)
    except Exception as error:  # noqa: BLE001
        log(f"   [img] {str(error)[:140]}")
        return None
    parser = _PageCollector()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001
        pass
    # 出典の表示用（プレスリリース配信の記事は、発表した会社の名前を題名・構造化データから取る）
    title = re.search(r"<title[^>]*>([^<]{1,300})</title>", text, re.I)
    author = re.search(r'"author"\s*:\s*\{[^{}]{0,200}?"name"\s*:\s*"([^"]{1,60})"', text)
    PAGE_META.setdefault(page_url, {
        "title": html.unescape(title.group(1)).strip() if title else "",
        "author": html.unescape(author.group(1)).strip() if author else "",
    })
    return parser, final


def _links_elsewhere(item: dict, final: str) -> bool:
    """ほかのページへのリンクに包まれた画像（関連記事・おすすめ・サービス紹介のカード）"""
    href = (item.get("link") or "").strip()
    if not href or href.startswith(("#", "javascript:")):
        return False
    if re.search(r"\.(jpe?g|png|webp|gif|avif)(\?|$)", href, re.I):
        return False  # 拡大画像へのリンク
    target = urllib.parse.urljoin(final, href).split("#")[0].rstrip("/")
    return target != final.split("#")[0].rstrip("/")


def _stale(url: str, article: dict | None) -> bool:
    """URL の日付（/2026/07/14/ など）が記事より2か月以上前なら、その発表の画像ではない（サイトの宣伝・過去の記事）"""
    match = re.search(r"/(20\d\d)/(\d{1,2})(?:/(\d{1,2}))?/", url)
    if not match or not article:
        return False
    try:
        when = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3) or 1), tzinfo=timezone.utc)
        made = datetime.fromisoformat(str(article.get("publishedAt") or article.get("generatedAt")).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return False
    return (made - when).days > 60


def release_token(page_url: str) -> str:
    """発表のページに固有の名前（https://global.honda/jp/news/2026/2261103e.html → 2261103e）。
    数字を含む5文字以上か、12文字以上の名前だけ（news・index などの共通の名前は空）"""
    parts = [x for x in urllib.parse.urlparse(page_url or "").path.split("/") if x]
    for part in reversed(parts[-2:]):
        stem = re.sub(r"\.(s?html?|php|aspx?)$", "", part, flags=re.I)
        if stem.lower() in {"index", "default", "main", "detail", "news", "release", "press", "article"}:
            continue
        if (len(stem) >= 5 and re.search(r"\d", stem)) or len(stem) >= 12:
            return stem
        break
    return ""


def own_release_image(url: str, token: str) -> bool:
    """画像の場所（パス）に、その発表のページに固有の名前が入っている＝その発表のために置かれた画像"""
    return bool(token) and token.lower() in urllib.parse.urlparse(url).path.lower()


def page_candidates(parser: _PageCollector, final: str, known: str = "", article: dict | None = None) -> list[dict]:
    """元記事の画像候補を良さそうな順に: og:image → 本文の図（figure）→ 本文の画像 → 本文の外（関連記事などを除く）"""
    out: list[dict] = []
    if known:
        out.append({"url": known, "where": "known"})
    for key in ("og:image", "og:image:secure_url", "og:image:url", "twitter:image", "twitter:image:src", "image_src"):
        if parser.meta.get(key):
            out.append({"url": parser.meta[key], "where": "og"})
    # その発表のために置かれた画像（パスに発表のページの名前が入っている）は、囲みの名前（Honda の本文の写真は
    # class="p-related22__thumbnail"）が関連記事のように見えても本文の写真として扱う（2026-11-03 Honda RN0 の取りこぼし）
    token = release_token((article or {}).get("sourceUrl") or final)
    for item in parser.images:
        if own_release_image(urllib.parse.urljoin(final, item["url"]), token) and item["bad"] and (item["figure"] or item["body"]):
            item["bad"], item["body"] = False, True
    usable = [
        i for i in parser.images
        if not i["bad"] and not _links_elsewhere(i, final) and not _stale(urllib.parse.urljoin(final, i["url"]), article)
    ]
    body = [i for i in usable if i["body"]]
    body.sort(key=lambda i: (not i["figure"], i["order"]))
    others = [i for i in usable if not i["body"]]
    for item in body[:10] + others[:3]:
        out.append({"url": item["url"], "where": "figure" if item["figure"] else ("body" if item["body"] else "page"), "alt": item["alt"], "hint": item["hint"]})
    seen, result = set(), []
    for item in out:
        full = urllib.parse.urljoin(final, html.unescape(item["url"].strip()))
        # 同じ画像の大きさ違い（?w=… や -300x200）は1つにまとめる
        base = re.sub(r"(\?.*$|-\d{2,4}x\d{2,4}(?=\.\w+$))", "", full)
        if full.startswith("http") and base not in seen:
            seen.add(base)
            result.append({**item, "url": full})
    return result


def youtube_official(video_id: str, article: dict) -> dict | None:
    """その会社・団体の公式チャンネルの動画なら {"title", "author", "thumb"}（oEmbed のチャンネル名で確かめる）"""
    try:
        data = http_json("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(f"https://www.youtube.com/watch?v={video_id}"), ua=BROWSER_UA, timeout=15)
    except Exception:  # noqa: BLE001
        return None
    author = str(data.get("author_name") or "")
    norm = lambda s: re.sub(r"[\s\-_・.]", "", s.lower())  # noqa: E731
    a = norm(author)
    org = norm(org_name(article))
    host = urllib.parse.urlparse(article.get("sourceUrl") or "").netloc
    labels = {re.sub(r"news$", "", h) for h in _host_label_set(host)}
    ok = bool(a) and ((org and len(org) >= 3 and (org in a or a in org)) or any(len(h) >= 4 and h in a for h in labels))
    if not ok:
        log(f"   [img] YouTube {video_id} は公式チャンネルと確かめられない（{author[:40]}）")
        return None
    return {"title": str(data.get("title") or ""), "author": author}


def video_on_topic(video_title: str, article: dict) -> bool:
    """動画の題名に、記事の題名・タグ・本文の冒頭と同じ固有の言葉（英単語・カタカナ語・漢字語）があるか。会社名だけの一致は数えない"""
    text = " ".join(str(article.get(k) or "") for k in ("title", "description", "lede")) + " " + " ".join(article.get("tags") or []) + " " + str(article.get("body") or "")[:1500]
    low = text.lower()
    org = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", org_name(article) + " " + (article.get("sourceName") or ""))}
    common = {"with", "from", "your", "into", "how", "the", "and", "for", "new", "what", "video", "official", "minutes", "introducing", "using", "build", "cloud", "next"}
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{3,}", video_title)} - org - common
    words |= set(re.findall(r"[ァ-ヴー]{3,}|[一-龥]{2,}", video_title))
    return any(w in low if w.isascii() else w in text for w in words)


def youtube_frames(video_id: str) -> list:
    """公式動画の画像（YouTube が配る画像だけ。動画そのものはダウンロードしない＝利用規約を守る）。
    maxresdefault と、動画の途中のコマ maxres1〜3（1280×720）。無ければ sddefault・sd1〜3（640×480 の上下の黒帯を切って 16:9）"""
    out = []
    for names in (("maxresdefault", "maxres1", "maxres2", "maxres3"), ("sddefault", "sd1", "sd2", "sd3")):
        for name in names:
            try:
                data, _, _ = http_get(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg", accept="image/*", timeout=10)
                image = open_image(data)
            except Exception:  # noqa: BLE001
                continue
            w, h = image.size
            if name.startswith("sd") and (w, h) == (640, 480):
                image = image.crop((0, 60, 640, 420))
            if image.size[0] >= MIN_PHOTO_WIDTH:
                out.append((name, image))
        if out:
            break
    return out


def _ffmpeg() -> str:
    import shutil  # noqa: PLC0415

    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # noqa: PLC0415

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return ""


def video_frames(url: str, referer: str, deadline: float = 0.0) -> list:
    """会社が自分で置いている動画ファイル（mp4・webm）から、途中のコマを3枚だけ取り出す（全体はダウンロードしない）。
    ffmpeg が無い・遅い・読めないときは空"""
    exe = _ffmpeg()
    if not exe:
        log("   [img] ffmpeg が無いので動画のコマは取らない")
        return []
    common = ["-hide_banner", "-loglevel", "error", "-user_agent", BROWSER_UA, "-headers", f"Referer: {referer}\r\n", "-rw_timeout", "5000000"]
    deadline = deadline or time.time() + 14
    duration = 0.0
    try:
        probe = subprocess.run([exe, "-hide_banner", "-user_agent", BROWSER_UA, "-rw_timeout", "5000000", "-i", url], capture_output=True, text=True, timeout=6)
        match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe.stderr)
        if match:
            duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    except Exception:  # noqa: BLE001
        return []
    times = [duration * f for f in (0.25, 0.5, 0.75)] if duration >= 3 else [1.0, 3.0]
    out = []
    for t in times:
        left = deadline - time.time()
        if left < 2:
            break
        try:
            res = subprocess.run(
                [exe, *common, "-ss", f"{t:.2f}", "-i", url, "-frames:v", "1", "-q:v", "2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                capture_output=True, timeout=min(6, left),
            )
            if res.stdout:
                out.append((f"{t:.1f}s", open_image(res.stdout)))
        except Exception:  # noqa: BLE001
            continue
    return out


def sharpness(image) -> float:
    """くっきりしているか（場面の切り替わりのぼやけたコマ・暗いコマは低い）"""
    from PIL import ImageFilter, ImageStat  # noqa: PLC0415

    gray = image.convert("L").resize((320, 180))
    mean = ImageStat.Stat(gray).mean[0]
    if mean < 35 or mean > 235:
        return 0.0
    return ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0]


def best_frame(frames: list, budget: int) -> tuple[object, str, str] | None:
    """コマの中からいちばん良いもの（くっきり・文字が少ない・人や製品や会場が写る写真）。(画像, 種類, 名前)"""
    ranked = sorted(frames, key=lambda f: -sharpness(f[1]))[: max(1, budget)]
    fallback = None
    for name, image in ranked:
        if sharpness(image) <= 0:
            continue
        kind = classify_official(image)
        if kind == "photo":
            return image, "photo", name
        if kind in ("hero", "banner") and fallback is None:
            fallback = (image, kind, name)
    return fallback


CLASSIFY_PROMPT = (
    "This image comes from a company's or organization's own press release or blog post. "
    "Classify it with exactly one word:\n"
    "photo - a real photograph (people, products, buildings, vehicles, events, ceremonies, food, nature) or a realistic product render; "
    "a small logo or a few words on it are fine\n"
    "hero - an illustrated key visual, artwork or abstract graphic with at most a short title\n"
    "dense - a slide, chart, graph, table, diagram, document, screenshot or infographic with lots of small text\n"
    "logo - only a logo, wordmark or icon on a plain background\n"
    "banner - an advertisement, flyer or campaign banner dominated by big headline text, prices or dates\n"
    "Answer with one word: photo, hero, dense, logo or banner."
)


def classify_official(image) -> str:
    """公式の画像の種類（photo / hero / dense / logo / banner）。画像判定ができないときは Pillow の目安で決める"""
    stats = graphic_stats(image)
    if stats["flat"] >= 0.88 or stats["bins"] <= 24:
        return "logo"  # 無地の地にロゴ・文字だけ（判定にかけるまでもない）
    text = (cf_vision(image, CLASSIFY_PROMPT, max_tokens=8) or "").lower()
    for kind in ("photo", "hero", "dense", "logo", "banner"):
        if kind in text:
            return kind
    doc = doc_stats(image)
    if doc["white"] >= 0.6 or doc["flat"] >= 0.8:
        return "dense"
    return "photo"


def trim_flat_border(image):
    """左右（または上下）に無地の帯を付けて共有画像の形（1200×630）にした写真から、帯を切り落とす。
    写真が上下（左右）の端まで続いていて、両側に幅 2% 以上の同じ色の帯があるときだけ切る（白地の製品写真は切らない）"""
    if ImageChops is None:
        return image
    rgb = image.convert("RGB")
    w, h = rgb.size
    corner = rgb.getpixel((0, 0))
    bbox = ImageChops.difference(rgb, Image.new("RGB", rgb.size, corner)).convert("L").point(lambda v: 255 if v > 24 else 0).getbbox()
    if not bbox:
        return image
    x0, y0, x1, y1 = bbox
    pillar = x0 >= w * 0.02 and (w - x1) >= w * 0.02 and y0 <= h * 0.01 and y1 >= h * 0.99
    letter = y0 >= h * 0.02 and (h - y1) >= h * 0.02 and x0 <= w * 0.01 and x1 >= w * 0.99
    if pillar and abs(x0 - (w - x1)) <= w * 0.03:
        return image.crop((x0, 0, x1, h))
    if letter and abs(y0 - (h - y1)) <= h * 0.03:
        return image.crop((0, y0, w, y1))
    return image


def size_ok(kind: str, w: int, h: int) -> str | None:
    """種類ごとの大きさ・縦横比の条件（だめなら理由）。SNS カードは 3:1 に切り抜くので横長は 3.3 まで通す"""
    ratio = w / max(h, 1)
    if kind == "logo":
        return None if w >= MIN_LOGO_WIDTH else f"小さい（{w}×{h}）"
    if kind in ("photo", "youtube", "video", "kit", "generic"):
        if w < MIN_PHOTO_WIDTH or h < 300:
            return f"小さい（{w}×{h}）"
        if not (1.0 <= ratio <= 3.3):
            return f"縦横比が合わない（{w}×{h}）"
        return None
    if kind == "dense":
        if w < MIN_DENSE_WIDTH:
            return f"文字の多い図で小さい（{w}×{h}）"
        return None if 1.0 <= ratio <= 2.6 else f"縦横比が合わない（{w}×{h}）"
    if w < MIN_GRAPHIC_WIDTH or h < 300:
        return f"小さい（{w}×{h}）"
    return None if 1.2 <= ratio <= 3.3 else f"縦横比が合わない（{w}×{h}）"


def agency_problem(url: str, alt: str = "") -> str | None:
    host = urllib.parse.urlparse(url).netloc
    if _host_in(host, AGENCY_IMAGE_HOSTS):
        return "報道機関・通信社・SNS の画像"
    if alt and AGENCY_CREDIT.search(alt):
        return "通信社のクレジット入り"
    return None


def kit_pages(parser: _PageCollector, final: str) -> list[str]:
    """本文から張られたプレスキット・報道用素材のページ（同じサイトのもの・1つだけ）"""
    host = urllib.parse.urlparse(final).netloc.lower()
    root = ".".join(host.split(".")[-3:]) if host.endswith(".jp") else ".".join(host.split(".")[-2:])
    out = []
    for href, text, bad in parser.links:
        # 本文の外（横の欄など）のリンクは、リンク先の名前でプレスキットと分かるものだけ（image_download.html など）
        if not KIT_TEXT.search(href if bad else f"{text} {href}"):
            continue
        full = urllib.parse.urljoin(final, href)
        target = urllib.parse.urlparse(full)
        if target.scheme.startswith("http") and (target.netloc.lower() == host or target.netloc.lower().endswith("." + root)) and not re.search(r"\.(zip|pdf)$", target.path, re.I):
            if full not in out:
                out.append(full)
    return out[:1]


def product_pages(parser: _PageCollector, final: str) -> list[str]:
    """本文から張られた、同じ会社のサイトの製品ページ（製品の発表の記事だけ見る・1つだけ）"""
    host = urllib.parse.urlparse(final).netloc.lower()
    root = ".".join(host.split(".")[-3:]) if host.endswith(".jp") and host.count(".") >= 2 and host.split(".")[-2] in {"co", "or", "ne", "ac", "go"} else ".".join(host.split(".")[-2:])
    here = final.split("#")[0].rstrip("/")
    out = []
    for href, text, bad in parser.links:
        if bad or not PRODUCT_LINK.search(f"{text} {href}"):
            continue
        full = urllib.parse.urljoin(final, href).split("#")[0]
        target = urllib.parse.urlparse(full)
        same_org = target.netloc.lower() == host or target.netloc.lower().endswith("." + root) or target.netloc.lower() == root
        if not target.scheme.startswith("http") or not same_org or full.rstrip("/") == here:
            continue
        if re.search(r"\.(zip|pdf|jpe?g|png|webp)$", target.path, re.I) or re.search(r"/(news|press|release|newsroom)/", target.path, re.I):
            continue
        if full not in out:
            out.append(full)
    return out[:1]


def related_to(image, article: dict) -> bool | None:
    text = cf_vision(
        image,
        f"News article title (Japanese): {article.get('title', '')}\n"
        "Does this image clearly show the product, service, person, place or event that this article is about? Answer yes or no.",
        max_tokens=5,
    )
    low = (text or "").strip().lower()
    return True if low.startswith("yes") else False if low.startswith("no") else None


# --------------------------------------------------------------------------- #
# 製品の発表の記事（2026-11-03 オーナー決定）
# --------------------------------------------------------------------------- #
# 特定の製品（端末・乗り物・ガジェット・機械）の発表では、AI が作った「それらしい製品」の画像を読者が本物と受け取る
# （Honda RN0 の記事で、AI の電動自転車の画像を見て製品を評価された）。製品の発表では公式の製品写真を最優先し、
# 無いときも製品そのもの・製品と見間違える物は描かず、使う場面・会場の雰囲気・抽象的な背景にする。
LAUNCH_VERB = re.compile(r"(発表|公開|発売|披露|お披露目|初公開|投入|登場|予約開始|受注開始|販売開始|一部改良|unveil|launch|announce|debut)", re.I)
# 形のある製品だけを指す言葉（「AI半導体のNVIDIA」「Apple端末向けツール」のような、会社や対象の説明にも使う言葉は入れない）
PRODUCT_NOUN = re.compile(
    r"(スマートフォン|iPhone ?\d|iPad|Pixel ?\d|Galaxy|Xperia|タブレット|ノートPC|ノートパソコン|パソコン|MacBook|iMac|Mac mini|Mac Studio|"
    r"イヤホン|イヤフォン|ヘッドホン|ヘッドフォン|AirPods|スピーカー|カメラ|スマートウォッチ|腕時計|スマートリング|スマートグラス|メガネ|ゴーグル|"
    r"ヘッドセット|Vision Pro|ゲーム機|新型機|コントローラー|ディスプレイ|モニター|テレビ|プロジェクター|ルーター|キーボード|"
    r"ドローン|新型車|自動車|電気自動車|\bEV\b|SUV|セダン|ミニバン|軽自動車|トラック|バイク|二輪車|スクーター|自転車|マウンテンバイク|e-?MTB|e-?bike|電動アシスト|"
    r"キックボード|冷蔵庫|洗濯機|エアコン|掃除機|炊飯器|電子レンジ|ドライヤー|充電器|モバイルバッテリー|家電)",
    re.I,
)
# 形の無いもの（アプリ・機能・更新・ゲームの題名・使い方）や、業績の話は製品の発表として扱わない
NOT_PRODUCT = re.compile(r"(アプリ|ツール|ソフト|機能|アップデート|更新|iOS|OS\b|基盤|フレームワーク|手法|体験版|版『|版「|ゲーム|新作|撮影術|活用法|使い方|決算|業績|実績|販売台数|値上げ|値下げ)")
CORPORATE = re.compile(r"(トヨタ自動車|日産自動車|三菱自動車|いすゞ自動車|本田技研工業|ヤマハ発動機|川崎重工)")
PRODUCT_TAGS = {"自動車・二輪", "ガジェット", "家電", "スマートフォン", "ウェアラブル", "ハードウェア"}


def product_launch(article: dict) -> bool:
    """特定の製品（端末・乗り物・ガジェット・機械）の発表の記事か。
    題名に「発表／公開／発売など＋形のある製品の言葉」があり、アプリ・機能・業績の話ではないもの。
    ジャンル（スポーツ・制度は除く）と判定カード（kind）、札（自動車・二輪・ガジェットなど）も見る"""
    if article.get("type") == "roundup" or article.get("noPhoto"):
        return False
    verdict = article.get("verdict") if isinstance(article.get("verdict"), dict) else {}
    if article.get("genreSlug") in ("sports", "public") or verdict.get("kind") in ("sports", "public"):
        return False
    title = CORPORATE.sub("", " ".join(str(article.get(k) or "") for k in ("title", "seoTitle")))
    # 形の無いもの・業績の話かは本来の題名だけで見る（検索用の題名には「料金も」などの決まり文句が付く）
    if not LAUNCH_VERB.search(title) or NOT_PRODUCT.search(CORPORATE.sub("", str(article.get("title") or ""))):
        return False
    if PRODUCT_NOUN.search(title):
        return True
    # 題名に製品の言葉が無くても、製品の札（自動車・二輪・ガジェットなど）＋「新製品」の札か、ガジェットのジャンルで「発売」なら製品の発表
    tags = set(article.get("tags") or [])
    return bool(tags & PRODUCT_TAGS) and ("新製品" in tags or (article.get("genreSlug") == "tech-gadget" and "発売" in title))


# 実物の見た目が大事な記事（2026-11-03 の画面の点検 C）。製品の発表（product_launch）に加えて、
# レース（トヨタの WEC の記事に AI が別の赤いスーパーカーを描いた）・発売・リコール・デザインの一新・形のある製品の話題も、
# AI 画像で製品（車・端末・商品）を描かない。公式の写真が無ければ、製品の無い場面（会場・道・光）にする。
# sns_rules.py は product_launch（製品の発表）だけを見る。こちらは画像を選ぶときだけに使う
MOTORSPORT = re.compile(r"(WEC|F1|フォーミュラ|スーパーフォーミュラ|SUPER GT|スーパーGT|ラリー|WRC|ル・マン|耐久レース|モータースポーツ|MotoGP|鈴鹿8耐|レーシング|レースカー|ハイパーカー)", re.I)
REAL_OBJECT_TITLE = re.compile(
    r"(新発売|発売|リコール|(?:アイコン|ロゴ|デザイン|外観)[^、。　]{0,12}(?:一新|刷新)|車いす|車椅子|化粧水|スキンケア|飲料|ボトル|缶コーヒー)"
)


def real_object_article(article: dict) -> bool:
    """AI 画像で描いてはいけない実物（製品・車・端末・商品）が主役の記事か。製品の発表・レース・発売・リコール・デザインの一新など"""
    if article.get("type") == "roundup" or article.get("noPhoto"):
        return False
    if product_launch(article):
        return True
    title = " ".join(str(article.get(k) or "") for k in ("title", "seoTitle"))
    tags = " ".join(str(t) for t in (article.get("tags") or []))
    if MOTORSPORT.search(f"{title} {tags}"):
        return True
    # 制度の記事（「自転車の補助金」など）は製品そのものの話ではない
    if article.get("genreSlug") == "public":
        return False
    return bool(REAL_OBJECT_TITLE.search(title) or PRODUCT_NOUN.search(CORPORATE.sub("", title)))


def pick_official(article: dict, repeats: Counter, fast: bool = False) -> dict:
    """公式の発表の中から、いちばん良い画像を選ぶ。
    {"best": {"kind", "url", "image", "rank", "where"} or None, "reasons": [使わなかった理由]}"""
    current = article.get("thumbnail", "") or ""
    page = article.get("sourceUrl", "") if article.get("sourceType") == "web" else ""
    reasons: list[str] = []
    found: list[dict] = []
    # 速報の回（fast）も同じ探し方をする（2026-11-03: 速報の回だけ動画・プレスキットを見ずに AI にしていた）。fast は AI のモデル選びだけに使う
    fast = False
    product = real_object_article(article)
    parsed = parse_page(page) if page else None
    if not parsed:
        return {"best": None, "reasons": ["元記事を開けない" if page else "元記事なし"]}
    parser, final = parsed
    known = current if current and not is_ours(current) and not is_youtube(current) else ""
    candidates = page_candidates(parser, final, known, article)
    vision_budget = 4 if fast else 7
    fetches = 0
    token = release_token(page)

    def consider(item: dict, forced_kind: str = "") -> dict | None:
        nonlocal vision_budget, fetches
        url = item["url"]
        framed = forced_kind in ("youtube", "video")  # 動画のコマ（画像と種類は呼び出し側で決めてある）
        problem = url_problem(url, 0) if not framed else None
        site_default = False
        if problem == "サイト共通の既定画像（ファイル名）" and own_release_image(url, token):
            # 「…_og.jpg」が「/content/dam/site/」の下にあっても、パスに発表のページの名前（2261103e など）が入っていれば
            # その発表のための画像（2026-11-03 Honda RN0 の og:image がサイト共通の画像として扱われた）
            problem = None
        if problem and problem.startswith("サイト共通"):
            site_default = True  # サイト共通の既定画像（多くはロゴ）。ロゴとしてだけ使える
            problem = None
        if problem and "ロゴ" in problem and not re.search(r"\.(svg|gif|ico)(\?|$)", url, re.I):
            site_default = True
            problem = None
        problem = problem or agency_problem(url, item.get("alt", ""))
        # 別の発表（元記事のページが違う）と同じ画像は、サイト共通・使い回しの画像。ロゴとしてだけ使える
        others = PAGES_BY_IMAGE.get(url, set()) - {page, ""}
        if not problem and others and not framed:
            site_default = True
        if problem:
            reasons.append(problem)
            return None
        if fetches >= 12:
            return None
        fetches += 1
        try:
            if framed:
                image = item["image"]
            else:
                data, ctype, _ = http_get(safe_url(url), accept="image/avif,image/webp,image/*,*/*;q=0.8", referer=page, timeout=20)
                if ctype and not ctype.startswith("image/") and "octet-stream" not in ctype:
                    reasons.append("画像ではない")
                    return None
                image = trim_flat_border(open_image(data))
        except Exception as error:  # noqa: BLE001
            reasons.append(f"読めない（{str(error)[:40]}）")
            return None
        w, h = image.size
        if framed:
            kind = forced_kind
        elif site_default or item.get("hint") == "logo" or w < MIN_PHOTO_WIDTH:
            # 小さい画像・サイト共通の画像は、ロゴかどうかだけを Pillow で見る（判定の呼び出しを節約）。
            # ロゴとして使うのは og:image（サイト共通の共有画像）か、名前・説明に logo とある画像だけ（SNS のアイコンは使わない）
            stats = graphic_stats(image)
            logo_like = item.get("where") in ("og", "known") or re.search(r"logo|ロゴ", f"{url} {item.get('alt', '')}", re.I)
            social = re.search(r"(twitter|x-logo|facebook|youtube|instagram|line|tiktok|linkedin|note|share|sns|rss|mail)", f"{url} {item.get('alt', '')}", re.I)
            kind = ""
            if logo_like and not social:
                if w >= MIN_PHOTO_WIDTH and not re.search(r"logo|ロゴ", f"{url} {item.get('alt', '')}", re.I):
                    # 大きなサイト共通の画像は中身を確かめる（グラフや表をロゴとして載せない）。
                    # 会社の建物などの写真なら「共通の写真」（ロゴより上、図より下）
                    if vision_budget > 0:
                        vision_budget -= 1
                        seen = classify_official(image)
                        kind = "logo" if seen == "logo" else "generic" if seen in ("photo", "hero") else ""
                elif stats["flat"] >= 0.6 or stats["bins"] <= 60:
                    kind = "logo"
            if not kind:
                reasons.append(f"小さい（{w}×{h}）" if w < MIN_PHOTO_WIDTH else "サイト共通の画像")
                return None
        else:
            if vision_budget <= 0:
                reasons.append("判定の回数切れ")
                return None
            vision_budget -= 1
            kind = classify_official(image)
            if forced_kind == "kit" and kind == "photo":
                if related_to(image, article) is False:
                    reasons.append("プレスキットの写真が記事と関係ない")
                    return None
                kind = "kit"
        problem = size_ok(kind, w, h)
        if problem:
            reasons.append(f"{kind}: {problem}")
            return None
        hit = {"kind": kind, "url": url, "image": image, "rank": item.get("rank", OFFICIAL_RANK.get(kind, 9)), "where": item.get("where", ""), "order": len(found), "big": w >= 1000}
        found.append(hit)
        return hit

    # 1. 元記事のページの写真・図（良い順に見て、写真が見つかったら止める）
    for item in candidates[:10]:
        hit = consider(item)
        if hit and hit["kind"] == "photo" and hit["big"]:
            break
    # 2. 公式の動画のコマ。写真のようなコマ（人・製品・会場が写り、文字が少ない）は元記事の写真の次、文字の多いコマは見出し画像の後
    #    2a. 会社が自分のページに置いた動画ファイル（mp4・webm）から ffmpeg で数コマ取り出す
    if not any(f["rank"] < 1 for f in found) and not fast and parser.videos:
        video_deadline = time.time() + 14  # 動画のコマ取りは1本の記事で14秒まで
        for src in list(dict.fromkeys(urllib.parse.urljoin(final, v) for v in parser.videos))[:2]:
            if agency_problem(src) or not src.startswith("http"):
                continue
            if time.time() > video_deadline - 4:
                break
            frames = video_frames(src, final, video_deadline)
            picked = best_frame(frames, min(2, vision_budget))
            vision_budget -= min(2, len(frames))
            if not picked:
                continue
            image, seen, name = picked
            log(f"   [img] 公式動画のコマ（{name}・{seen}）: {src[:90]}")
            hit = consider({"url": src, "image": image, "where": f"video {name}", "rank": 1 if seen == "photo" else 3.5}, forced_kind="video")
            if hit and hit["rank"] < 2:
                break
    #    2b. 公式 YouTube 動画の画像（YouTube が配る表紙と途中のコマだけ。動画はダウンロードしない）
    if not any(f["rank"] < 1 for f in found):
        for vid in list(dict.fromkeys(parser.youtube))[:2]:
            info = youtube_official(vid, article)
            if not info:
                continue
            # 同じ会社の動画でも、別の話題の動画（ページの下の「おすすめ動画」など）は使わない
            if not video_on_topic(info["title"], article):
                log(f"   [img] YouTube {vid} は記事の話題と違う動画（{info['title'][:40]}）")
                continue
            picked = best_frame(youtube_frames(vid), min(2 if not fast else 1, vision_budget))
            vision_budget -= 2
            if not picked:
                continue
            image, seen, name = picked
            log(f"   [img] 公式 YouTube 動画の画像（{name}・{seen}）")
            hit = consider({"url": f"https://www.youtube.com/watch?v={vid}", "image": image, "where": f"youtube {name}", "rank": 1 if seen == "photo" else 3.5}, forced_kind="youtube")
            if hit and hit["rank"] < 2:
                break
    # 3. プレスキット・報道用素材のページ（製品の発表なら、本文から張られた製品ページも）
    if not fast and not any(f["rank"] < 2 for f in found):
        for kit in kit_pages(parser, final) + (product_pages(parser, final) if product else []):
            sub = parse_page(kit)
            if not sub:
                continue
            for item in page_candidates(*sub, article=article)[:5]:
                if item["where"] == "og":
                    continue
                hit = consider(item, forced_kind="kit")
                if hit and hit["kind"] == "kit":
                    break
    # 同じ種類なら大きい（幅 1000px 以上の）ものを先に
    found.sort(key=lambda f: (f["rank"], not f.get("big"), f["order"]))
    best = found[0] if found else None
    if best and page:
        PAGES_BY_IMAGE.setdefault(best["url"], set()).add(page)
    return {"best": best, "reasons": list(dict.fromkeys(reasons)) or ["候補なし"]}


def logo_canvas(image):
    """ロゴだけの画像を、白（またはロゴの地の色）の 1200×630 の真ん中に置く（SNS カードの 3:1 の枠にも収まる大きさ）"""
    w, h = image.size
    # 細い枠線（デジタル庁の共有画像など）を切り落としてから、ロゴの部分だけを取り出す
    m = max(2, round(min(w, h) * 0.03))
    image = image.crop((m, m, w - m, h - m))
    w, h = image.size
    corner = image.getpixel((2, 2))
    bg = Image.new("RGB", (w, h), corner)
    bbox = ImageChops.difference(image, bg).convert("L").point(lambda v: 255 if v > 40 else 0).getbbox()
    logo = image.crop(bbox) if bbox else image
    lw, lh = logo.size
    scale = min(560 / lw, 220 / lh, 3.0)
    logo = logo.resize((max(1, round(lw * scale)), max(1, round(lh * scale))), Image.LANCZOS)
    light = sum(corner) / 3 > 200
    canvas = Image.new("RGB", (OUT_W, OUT_H), (255, 255, 255) if light else corner)
    canvas.paste(logo, ((OUT_W - logo.size[0]) // 2, (OUT_H - logo.size[1]) // 2))
    if light and bg.getpixel((0, 0)) != (255, 255, 255):
        # ロゴの地が薄い色なら、その色ごと四角く置く（地の色の違いで白い枠が見えないように）
        canvas = Image.new("RGB", (OUT_W, OUT_H), corner)
        canvas.paste(logo, ((OUT_W - logo.size[0]) // 2, (OUT_H - logo.size[1]) // 2))
    return canvas


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


PRODUCT_PLAN_RULE = """
IMPORTANT: this article announces a specific product (a device, vehicle, gadget or machine).
Readers must never mistake an illustration for the real product. So:
- "product": the generic English name of the product category (1-3 words, e.g. "electric mountain bike", "smartphone", "electric car").
- "scene" and "queries" must NOT depict the announced product or any similar device, vehicle, machine or gadget
  (no bikes for a bike, no phones for a phone, no cars for a car, not even partly or in the distance).
  Show instead the usage context without the product (e.g. an empty forest trail at dawn), the mood of the event venue
  (e.g. an exhibition hall with blurred stage lights and no exhibits), or the category in the abstract (light, motion, texture)."""


def visual_plan(article: dict, product: bool = False) -> dict:
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
    if product:
        prompt += PRODUCT_PLAN_RULE
    schema = {**QUERY_SCHEMA, "properties": {**QUERY_SCHEMA["properties"], "product": {"type": "STRING"}}} if product else QUERY_SCHEMA
    data, model = llm.generate_json(QUERY_SYSTEM, prompt, schema, tier=llm_tier(), temperature=0.3, max_output_tokens=2048)
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
        "product": re.sub(r"[^\w\s-]", " ", str(data.get("product") or "")).strip()[:40] if product else "",
        "productLaunch": product,
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
        '{"related": true/false (does the photo directly show the actual subject of the article - the same kind of product, food, vehicle, '
        'building, place or event - so a reader sees the connection instantly? A generic scene that only loosely fits the topic is false), '
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


# 製品の発表の記事で AI に必ず足す決まり（製品の名前・種類は書かない。書くと描かれやすい）
NO_PRODUCT_RULE = (
    "Do not depict the announced product or any similar device, vehicle, machine or gadget, not even partly or in the background; "
    "show only the setting, light and atmosphere."
)
# 製品を描いてしまったときの、製品の無い場面（会場の雰囲気）
NO_PRODUCT_SCENE = "A softly lit, empty exhibition hall with blurred stage lights and warm bokeh, polished floor, no exhibits, no products and no people in view."


def ai_prompt(plan: dict, article: dict) -> str:
    scene = plan.get("scene") or ", ".join(plan.get("queries") or []) or article.get("genre", "")
    rule = f" {NO_PRODUCT_RULE}" if plan.get("productLaunch") else ""
    return (
        f"Photorealistic editorial news photograph. {scene} "
        "Natural muted colors, soft natural light, documentary style, shot on a full-frame camera with a 35mm lens, "
        "wide landscape composition with empty space in the lower third. "
        f"No text, no letters, no numbers, no signs, no logos, no watermark, no brand names, no identifiable faces.{rule}"
    )


def depicts_product(image, product: str) -> bool:
    """製品の発表の記事で、画像に製品（の種類の物）や見間違える物が写っているか。判定できないときは写っている側に倒す"""
    noun = product or "the kind of product announced (a device, vehicle, machine or gadget)"
    # 製品と同じ種類の物だけでなく、電子機器・乗り物・機械はどれも写さない（別の機器でも「この製品か」と見られる）
    text = cf_vision(
        image,
        f"Does this image show a {noun}, or any electronic device, gadget, screen, vehicle or machine (even partly or in the background)? Answer yes or no.",
        max_tokens=5,
    )
    low = (text or "").strip().lower()
    if low.startswith("no"):
        return False
    return True


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


def trim_dark_band(image):
    """生成画像の下の端に出ることがある、真っ黒で平らな帯（数〜数十px）を切り落とす。暗い場面そのものは切らない"""
    from PIL import ImageStat  # noqa: PLC0415

    gray = image.convert("L")
    w, h = gray.size
    cut = 0
    for y in range(h - 1, max(h - 48, 0), -1):
        stat = ImageStat.Stat(gray.crop((0, y, w, y + 1)))
        if stat.mean[0] < 30 and stat.stddev[0] < 8:
            cut += 1
        else:
            break
    return image.crop((0, 0, w, h - cut)) if cut >= 3 else image


def objects_only_scene(plan: dict, article: dict) -> str:
    """人の写らない場面（物・建物・場所だけ）。元の場面の言葉に「人を描かない」をはっきり足す"""
    scene = plan.get("scene") or ", ".join(plan.get("queries") or []) or article.get("genre", "")
    scene = re.sub(r"\b(people|person|persons|man|men|woman|women|workers?|staff|employees?|businessm[ae]n|crowd|team|hands?|figures?)\b[^.,]*", "", scene, flags=re.I)
    return f"{scene.strip(' ,.')}. A still life of objects and places only: absolutely no people, no hands, no faces, no silhouettes."


def shows_face(image) -> bool:
    """人の顔（ぼけた背景の人も含む）か、文字・文字のような落書きが写っているか（判定できないときは False）"""
    face = cf_vision(image, "Can you see any human face in this image, including people in the background (not just hands or a pure back view)? Answer yes or no.", max_tokens=5)
    if (face or "").strip().lower().startswith("yes"):
        return True
    text = cf_vision(image, "Does this image contain visible letters, words or handwriting (on a sign, paper, screen or wall)? Small unreadable details on documents do not count. Answer yes or no.", max_tokens=5)
    return (text or "").strip().lower().startswith("yes")


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
            image = trim_dark_band(open_image(raw))
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
        # 細かい写真で重くなりすぎないよう、幅に応じた上限（1200px で約90KB）まで画質を下げる
        cap = width * 75
        for quality in ((74 if width <= 640 else 72), 64, 56, 48):
            buf = io.BytesIO()
            resized.save(buf, "WEBP", quality=quality, method=6)
            if buf.tell() <= cap:
                break
        out[name] = buf.getvalue()
    return out


def render(image, keep_aspect: bool = False) -> tuple[bytes, dict[str, bytes]]:
    """JPEG（140KB 以下）と幅ごとの WebP。
    フリー素材・AI 生成は 1200×630 に切り抜く。公式発表の写真（keep_aspect）は切り抜かず、幅 1200 まで縮小するだけ"""
    if not keep_aspect:
        centering = subject_centering(image, OUT_W / OUT_H)
        if centering is not None:
            fitted = ImageOps.fit(image, (OUT_W, OUT_H), method=Image.LANCZOS, centering=centering)
            return _jpeg(fitted), webp_variants(fitted)
        # 主役が 1200×630 に収まらない（縦に長い等）：切り抜きを控えめにし、4:3 までにとどめる（SNS カードは主役の位置で敷き方を決める）
        w, h = image.size
        if h / w > 0.75:
            centre = subject_centering(image, 4 / 3) or (0.5, 0.45)
            image = ImageOps.fit(image, (w, round(w * 0.75)), method=Image.LANCZOS, centering=centre)
        keep_aspect = True
    w, h = image.size
    fitted = image if w <= OUT_W else image.resize((OUT_W, round(h * OUT_W / w)), Image.LANCZOS)
    return _jpeg(fitted), webp_variants(fitted)


def subject_centering(image, target: float) -> tuple[float, float] | None:
    """主役（scripts/image_focus.py）が丸ごと入る、縦横比 target の切り抜き位置（ImageOps.fit の centering）。
    主役が入らなければ None。解析できない環境・写真では、これまでどおり少し上寄せの真ん中"""
    try:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "JPEG", quality=85)
        focus = image_focus.subject_box(buf.getvalue())
    except Exception:  # noqa: BLE001 - OpenCV が無い等
        return (0.5, 0.45)
    if not focus:
        return (0.5, 0.45)
    keep = image_focus.keep_box(focus["box"])
    w, h = image.size
    if w / h >= target:  # 横を削る
        frame = h * target / w
        lo, hi = keep[0], keep[2]
    else:  # 縦を削る
        frame = w / target / h
        lo, hi = keep[1], keep[3]
    if hi - lo > frame + 0.1:  # 端まで続く背景（1割まで）は削ってよい。それより大きい主役は切らない
        return None
    free = 1 - frame
    if free <= 0.001:
        pos = 0.5
    else:
        start = min(max((lo + hi) / 2 - frame / 2, hi - frame, 0.0), min(lo, free))
        pos = max(0.0, min(1.0, start / free))
    return (pos, 0.5) if w / h >= target else (0.5, pos)


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

    previous = article.get("imageCredit") or ctx.get("previous_credit", {}).get(slug) or {}
    product = real_object_article(article)
    if product:
        log("   [img] 実物（製品・車・商品）が主役の記事: 公式の写真を最優先。AI・素材は製品を描かない場面だけ")
    # 自前の画像（まとめ記事・別の切り口の記事が元記事の画像を引き継いだ）なら出典も引き継ぐ
    if is_ours(thumb) and not ctx.get("retry_ai") and not ctx.get("repick"):
        credit = ctx["credit_by_thumb"].get(thumb)
        if credit:
            article["imageCredit"] = credit
            if not ctx["dry_run"]:
                save(path, article)
            return "inherit"

    # 1. 公式の発表の画像（会社・団体の自社サイト、プレスリリース配信）。元記事の本文の写真・図、公式の YouTube 動画、
    #    プレスキットまで見て良い順に選び、縮小して自前の KV から配信する。ほかのニュースサイト・ブログ・SNS の画像は使わない
    official = official_source(article)
    best = None
    if not ctx.get("retry_ai") and official is None:
        log("   [img] 公式の発表ではないので元記事の写真は使わない")
    elif not ctx.get("retry_ai"):
        result = pick_official(article, ctx["repeats"], fast=ctx.get("fast", False))
        best = result["best"]
        if best:
            log(f"   [img] 公式の画像: {best['kind']}（{best['where']}）{best['url'][:90]}")
        else:
            log(f"   [img] 公式の画像は使わない: {' / '.join(result['reasons'])[:200]}")
        # 製品の発表は、製品が写った公式のバナーも AI の作り物より良い
        if best and best["rank"] <= OFFICIAL_RANK["banner" if product else "hero"]:
            return use_official(path, article, ctx, best, official)

    # 選び直し（--slug・--repick-hours など）で、前に付けた公式の写真を AI・素材・写真なしに替えない
    # （その回だけ元記事が開けない・判定が落ちた、で公式の写真を失わない）
    if previous.get("kind") == "official" and is_ours(previous_thumb := (ctx.get("previous_thumb", {}).get(slug) or thumb)):
        article["thumbnail"] = previous_thumb
        article["imageCredit"] = previous
        log("   [img] 前の公式の写真をそのまま使う（公式の写真を AI・素材に替えない）")
        if not ctx["dry_run"]:
            save(path, article)
        return "kept-official"

    # ここから先は、公式の写真・見出し画像が無い記事
    try:
        plan = visual_plan(article, product=product)
    except Exception as error:  # noqa: BLE001
        log(f"   [img] 検索語を作れない: {str(error)[:100]}")
        if best:
            return use_official(path, article, ctx, best, official)
        return "error"
    if ctx.get("scene"):
        plan["scene"] = ctx["scene"]
    log(f"   [img] 検索語: {plan['queries']} / {plan.get('query_ja')} ({plan['model']})")

    credit: dict = {}
    image = None
    # 2. AI 生成（前からの写真風のプロンプト。話題を問わない。実在の人物は描かない。1日 IMAGE_AI_DAILY_MAX 枚まで）
    if not ctx.get("retry_ai"):
        made = None
        drew_product = False
        for attempt in range(3):
            # 2回とも顔・文字が出たら、最後は「人を描かない・物だけ」の場面で1回だけ作る
            use = plan if attempt < 2 else {**plan, "scene": objects_only_scene(plan, article)}
            if drew_product:
                use = {**plan, "scene": NO_PRODUCT_SCENE}  # 製品を描いてしまったら、製品の無い会場の雰囲気にする
            if attempt == 2:
                log("   [img] 人を描かない場面で作り直す")
            made = generate_ai(use, article, fast=ctx.get("fast", False))
            # 人の顔・文字の写った画像は使わない（実在の人物と誤解される・作り物の人物や崩れた文字を載せない）。1回だけ作り直す
            if made and shows_face(made[0]):
                log("   [img] AI 画像に人の顔か文字が写ったので作り直す")
                made = None
                continue
            # 製品の発表で、製品や見間違える物を描いた画像は使わない（作り物を本物の製品と受け取られる）
            if made and product and depicts_product(made[0], plan.get("product", "")):
                log("   [img] AI 画像に製品か似た物が写ったので、製品の無い場面で作り直す")
                made = None
                drew_product = True
                continue
            if made and product:
                plan = use
            break
        if made:
            image, model = made
            credit = {"kind": "ai", "model": model, "prompt": plan.get("scene", "")[:300], "provider": "Cloudflare Workers AI"}
            if product:
                credit["noProduct"] = True  # 製品の発表: 製品を描かない場面で作り、写っていないことを確かめた
            log(f"   [img] AI 生成: {model}")
    # 3. フリー素材（AI が使えないとき。記事と関係がはっきりしたものだけ）
    if image is None and not ctx.get("ai_only"):
        pick, image = find_stock(article, plan, ctx["used_stock"])
        if pick and image is not None and product and depicts_product(image, plan.get("product", "")):
            log(f"   [img] 素材に製品か似た物が写っているので使わない: {pick['title'][:60]}")
            pick, image = None, None
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
    if image is None and ctx.get("retry_ai"):
        log("   [img] 素材が見つからないので今の画像のまま")
        return "kept"
    if image is None:
        # 4. 公式のロゴ・文字の多い図・バナー（AI も素材も無いとき）。それも無ければ今までどおり（写真なしのカバー）
        if best:
            return use_official(path, article, ctx, best, official)
        return "none"

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


def use_official(path: Path, article: dict, ctx: dict, best: dict, official: dict) -> str:
    """公式の画像（写真・動画のサムネイル・見出し画像・図・ロゴ）を保存して記事に付ける"""
    slug = article.get("slug") or path.stem
    url, image, kind = best["url"], best["image"], best["kind"]
    org = wire_org(article) if official["wire"] else org_name(article)
    reuse = ctx["official_by_original"].get(url)
    if reuse:
        new_url, size = reuse["thumbnail"], (reuse["credit"].get("width"), reuse["credit"].get("height"))
        log(f"   [img] 公式の画像（保存済みを使い回す）: {new_url}")
    else:
        log(f"   [img] 公式の画像を保存して使う（{kind}）: {url[:100]}")
        try:
            if kind == "logo":
                new_url, size = store(slug, logo_canvas(image), ctx["dry_run"])
            else:
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
    if kind != "photo":
        credit["imageKind"] = kind  # youtube / kit / hero / banner / dense / logo
    if official["wire"]:
        credit["via"] = official["wire"]
    article["thumbnail"] = new_url
    article["imageCredit"] = credit
    ctx["official_by_original"][url] = {"thumbnail": new_url, "credit": credit}
    if not ctx["dry_run"]:
        save(path, article)
    return "official"


def settle_hotlink(path: Path, article: dict, ctx: dict) -> str | None:
    """選び終えても外部サイトの画像を直リンクしたまま（AI の上限・素材なし・失敗など）の記事を残さない。

    読者のブラウザに他サイトの画像を直接読ませると、参照元チェック・削除・URL 変更で壊れた画像アイコンになる。
    公式発表の画像は自前の KV（/img/）に保存して配信し、それ以外（ほかのニュースサイトの画像など）や
    保存できない画像は外して写真なし（独自カバー /cover/<slug>）にする。imageCredit は付けないので、次の回にまた選び直す。
    戻り値: "cached-official" / "unhotlinked" / None（直リンクではない）
    """
    thumb = article.get("thumbnail") or ""
    if not thumb or is_ours(thumb) or is_youtube(thumb) or not re.match(r"^https?://", thumb):
        return None
    slug = article.get("slug") or path.stem
    official = official_source(article)
    if official is not None and not url_problem(thumb, ctx["repeats"].get(thumb, 0)):
        try:
            data, ctype, _ = http_get(thumb, referer=article.get("sourceUrl") or "", timeout=20)
            if ctype and not ctype.lower().startswith("image/"):
                raise ValueError(f"画像ではない（{ctype[:40]}）")
            image = open_image(data)
            problem = image_problem(image, MIN_SOURCE_WIDTH)
            if problem:
                raise ValueError(problem)
            best = {"url": thumb, "image": image, "kind": "photo"}
            if use_official(path, article, ctx, best, official) == "official":
                log(f"   [img] 直リンクだった公式の画像を自前に保存: {article['thumbnail']}")
                return "cached-official"
        except Exception as error:  # noqa: BLE001
            log(f"   [img] 直リンクの画像を保存できない: {str(error)[:120]}")
    log(f"   [img] 外部の画像の直リンクを外す（写真なしのカバーにする）: {thumb[:100]}")
    article["thumbnail"] = ""
    article.pop("imageCredit", None)
    article.pop("imageFocus", None)
    if not ctx["dry_run"]:
        save(path, article)
    return "unhotlinked"


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
    parser.add_argument("--repick-hours", type=float, default=0, help="この時間以内の記事で、フリー素材・AI 生成・数字の図にした画像を今の基準で選び直す")
    parser.add_argument("--product-repick-days", type=float, default=0, help="この日数以内の製品の発表の記事で、AI 生成・素材の画像を選び直す（公式の製品写真→製品を描かない場面）")
    parser.add_argument("--scene", help="AI 生成の場面（英語）をこの文にする（--slug で1本だけ直すとき用）")
    parser.add_argument("--report", help="記事ごとの結果（JSON Lines）をこのファイルに書く（確認用）")
    parser.add_argument("--retry-ai", action="store_true", help="AI 生成の画像の記事でフリー素材を探し直す（見つからなければそのまま）")
    parser.add_argument("--cache-official", action="store_true", help="外部の写真を直リンクしている記事を直す（公式発表の写真は自前に保存、それ以外はフリー素材・AI 生成に）")
    parser.add_argument("--variants", action="store_true", help="前からある自前の写真に幅ごとの WebP を足す")
    parser.add_argument("--focus-recent", type=int, default=40, help="新しい N 本のうち、写真の主役の位置（imageFocus）が無い・写真が変わった記事を解析する（0 で止める）")
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
        "repick": bool(args.slug or args.repick_hours or args.product_repick_days),
        "scene": args.scene or "",
        # 選び直しの前の画像（公式の写真を AI・素材に替えないため。下で imageCredit を消す前に覚えておく）
        "previous_credit": {a.get("slug") or p.stem: a["imageCredit"] for p, a in articles.items() if isinstance(a.get("imageCredit"), dict)},
        "previous_thumb": {a.get("slug") or p.stem: a.get("thumbnail") or "" for p, a in articles.items()},
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
    elif args.repick_hours:
        # 新しい記事の画像を今の基準（公式の写真・図 → 数字の図 → 関係のはっきりした素材だけ）で選び直す
        targets = [
            (p, a)
            for p, a in ordered
            if age_hours(a) <= args.repick_hours and (a.get("imageCredit") or {}).get("kind") in ("stock", "ai", "figure")
        ]
        for _, a in targets:
            a.pop("imageCredit", None)
    elif args.product_repick_days:
        # 製品の発表の記事で、AI 生成・素材の画像にしたもの（製品の作り物・別の製品の写真を本物と受け取られる）
        targets = [
            (p, a)
            for p, a in ordered
            if age_hours(a) <= args.product_repick_days * 24 and (a.get("imageCredit") or {}).get("kind") in ("stock", "ai") and real_object_article(a)
        ]
        for p, a in targets:
            log(f"[img] 製品の発表で AI・素材の画像: {a.get('slug')}")
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
        began = time.time()
        try:
            outcome = process(path, article, ctx)
        except Exception as error:  # noqa: BLE001 - 1本の失敗で全体を止めない
            log(f"   [img] 失敗: {str(error)[:160]}")
            outcome = "error"
        tally[outcome] += 1
        slug = article.get("slug") or path.stem
        # 外部サイトの画像の直リンクを残さない（公式は自前に保存、それ以外は外す）
        try:
            settled = settle_hotlink(path, article, ctx)
        except Exception as error:  # noqa: BLE001
            log(f"   [img] 直リンクの後始末に失敗: {str(error)[:160]}")
            settled = None
        if settled:
            tally[settled] += 1
        before = ctx["previous_credit"].get(slug)
        if not article.get("imageCredit") and before and article.get("thumbnail") == ctx["previous_thumb"].get(slug):
            # 選び直せなかった（AI の上限・素材なし・失敗）ときは前の画像の出典を戻す（消したまま保存すると、AI の画像に注記が出なくなる）
            article["imageCredit"] = before
            if args.product_repick_days and before.get("kind") == "ai" and not before.get("noProduct") and real_object_article(article):
                # 製品の発表で、製品を描いたかもしれない前の AI 画像は残さない（写真なしのカバーにする）
                log("   [img] 製品を描いたかもしれない前の AI 画像を外す（写真なしのカバー）")
                article["thumbnail"] = ""
                article.pop("imageCredit", None)
                article.pop("imageFocus", None)
                tally[outcome] -= 1
                outcome = "removed-ai"
                tally[outcome] += 1
            if not args.dry_run:
                save(path, article)
        # 写真の主役の位置（SNS カードの切り抜き・ロゴの角に使う。scripts/image_focus.py）
        if not args.dry_run and image_focus.ensure_focus(article):
            save(path, article)
        if args.report:
            with open(args.report, "a", encoding="utf-8") as fh:
                row = {"slug": article.get("slug"), "outcome": outcome, "seconds": round(time.time() - began, 1), "thumbnail": article.get("thumbnail"), "imageCredit": article.get("imageCredit")}
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if args.focus_recent and not args.dry_run:
        # 写真を選び直さなかった記事（YouTube のサムネイル・前からある写真）も、新しい分は主役の位置を持たせる
        focused = 0
        for path, article in sorted(articles.items(), key=newest_first, reverse=True)[: args.focus_recent]:
            if time.time() - started > max(args.budget, 60) + 60:
                break
            if image_focus.ensure_focus(article):
                save(path, article)
                focused += 1
        if focused:
            log(f"[img] 主役の位置を {focused} 本に書きました")
    log(f"[img] 結果: {dict(tally)}（AI 生成は今日 {ai_used_today()} / {AI_DAILY_MAX} 枚）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
