#!/usr/bin/env python3
"""SNS 用の短い解説動画 v3「画面で見せる型」（20〜30秒・横型 1920x1080・30fps）。

  python3 scripts/shorts_v3.py <slug>                    # 本番の声（VOICEVOX）
  python3 scripts/shorts_v3.py <slug> --tts fake         # 声なしの試作（合成音）
  python3 scripts/shorts_v3.py <slug> --reuse-script     # 前回の台本を使い回す（LLM を呼ばない）

出力（--out。既定 out/shorts_v3/ 。git 管理外）
  <slug>__v3.mp4 / <slug>__v3.report.json（品質ゲートの記録と判定・各段階の秒数）/ <slug>__v3.sheet.png
  <slug>.v3script.json（台本）

考え方（docs/sns-video-v3-20261102.md）
  - 画面が話を運ぶ: 公式ページ（発表・料金・製品ページ）を本物のブラウザで撮り、仮想カメラで
    大事な数字の場所へなめらかにズームする（ease-in-out・動きの速い所だけ軽いモーションブラー）。
    カーソルがその文字へ動いてクリックし、周りを少し暗くして目立たせる。無関係な写真・AI 画像は使わない。
  - 画面の文字はキーワードだけ。題（つかみ）は最初の 2 秒以内に消える。書体は Noto Sans JP（Black/Bold）だけ、
    強調色は1色。飾りの線・帯・枠は描かない（video_gate.lint_renderer がこのファイルを検査する）。
  - 2〜3秒ごとに何かが変わる（カメラの移動・ページの切り替え・数字のカウントアップ・比較のグラフ）。
  - 音: VOICEVOX の声（並列で合成）＋ 自作の BGM（声の下で自動で下げる・声との差 12〜18dB）＋ 自作の効果音。-14 LUFS。
  - 速さ: ページ撮影は並列・VOICEVOX は最初に温めておく・描画は OpenCV で1フレーム数ミリ秒・複数プロセス。
  - 品質ゲート（video_gate.check_video）に落ちた動画は出さない。動画が作れなくても投稿は止めない（sns_video.py）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import shorts  # noqa: E402  (記事・企業・フォント・VOICEVOX・ffmpeg)
import shorts_v2 as v2  # noqa: E402  (声の前後の無音処理・公式ページの判定・折り返し)
import video_gate as gate  # noqa: E402

try:  # 描画を速くする（無ければ Pillow で同じことを遅くやる）
    import cv2

    cv2.setNumThreads(1)
except ImportError:  # pragma: no cover
    cv2 = None

# 横型 16:9（2026-11-02 オーナー指示: タイムラインで流れてきたときに見やすい）
W, H, FPS = gate.V3_W, gate.V3_H, gate.FPS
SR = 48000
VERSION = "v3"
LAYOUT = "landscape"

# 色（サイトの色: 墨 #0b1a33・青 #1d4a8f・赤 #c8221a・白。明るく清潔な見た目）
STAGE = (243, 245, 249)  # 背景（ほぼ白の明るいグレー）
INK = (11, 26, 51)
BLUE = (29, 74, 143)
ACCENT = (200, 34, 26)  # 数字の強調（赤）
MUTED = (84, 100, 128)  # 補足の文字
SOFT = (90, 103, 126)  # 出典・クレジット
MARKER = (214, 226, 244)  # キーワードの下を走るマーカー（青を薄くした色）
SELECT = (178, 208, 250)  # 画面の文字の選択色（ブラウザで文字を選んだときの青）
WHITE = (255, 255, 255)
FONT_FAMILY = "Noto Sans JP"
LOGO_FAMILY = "Dela Gothic One"

# 画面の割り付け（x0, y0, x1, y1）。左に文字の列・右に動く画面（公式ページ）
PANEL = (712, 64, 1856, 1016)
PW, PH = PANEL[2] - PANEL[0], PANEL[3] - PANEL[1]
RADIUS = 26
COL = (104, 150, 644, 930)  # 文字の列（キーワード・補足・つかみ・締め）
COL_W = COL[2] - COL[0]
FOCUS = (PW * 0.5, PH * 0.40)  # 注目する文字を置くパネル内の位置
ANCHOR_Y = PANEL[1] + int(FOCUS[1])  # キーワードの縦の中心（画面の注目の行と同じ高さ＝目が横に動くだけ）
SOURCE_Y = 968  # 出典の小さな1行
LOGO_XY = (104, 70)

# ページの撮影
CAPTURE_W = 540  # CSS px（縦の画面にちょうど入るスマホ〜小型タブレットの幅）
CAPTURE_DPR = 4  # 2.2倍に寄っても画素が足りる（ページの画素 2160px 幅）
CAPTURE_MAX_H = 5200  # CSS px（OpenCV の上限 32767px 以内）
ZOOM_MAX = 2.2  # ページ幅いっぱいの倍率に対する最大ズーム
ZOOM_BLOCK = 1.35  # 行に寄るときの最小ズーム（ページ幅いっぱいだと文字が小さすぎる）

# 時間
LEAD = 0.15
GAP = 0.22
END_HOLD = 0.7  # 締めの声のあと、最後の画面を残す時間（締めのカードは作らない）
CREDIT_SEC = 1.4  # 最後の場面に小さく出すクレジットの長さ
HOOK_TEXT_MAX = 1.7  # つかみの文字はこの時刻までに消す（ゲートは 2.0 秒）
# 長さ（2026-11-02 夜 オーナー「30秒に収めなくてよい。ちゃんと説明して」→ 45〜75秒）
TARGET_MAX = 74.0
TARGET_MIN = 42.0
LENGTH_RANGE = (40.0, 80.0)  # ゲートの長さの範囲（report["length_range"] で渡す）
APPROVED_RANGE = (40.0, 110.0)  # オーナーが承認した台本（読み上げを変えない）は長くなってもよい
SEP_TARGET_DB = 15.0  # 声と BGM の差

# 声（2026-11-02 夜 オーナーが試聴サンプル6つから「九州そら（ノーマル・高さ-0.01・抑揚1.0・速さ1.38）」を選んだ。
# 東北ずん子は「少し高い・AIっぽい」。九州そらは落ち着いた大人の声。規約は zunko.jp（「VOICEVOX:九州そら」の表記で商用可）。
# 以前の比較（明るさ）: 冥鳴ひまり→東北ずん子（夕方）。
# 候補（同じ文で比較）: 冥鳴ひまり 声の高さ中央 253Hz・明るさ（スペクトル重心）1854Hz / 冥鳴ひまり（高さ+0.04・抑揚1.3）293Hz・1813Hz /
# 東北ずん子 353Hz・2451Hz / 春日部つむぎ 358Hz・2010Hz。No.7 は商用に事前確認が要るので外した）
V3_VOICE = {
    "speaker": os.environ.get("V3_VOICEVOX_SPEAKER", "九州そら"),
    "style": os.environ.get("V3_VOICEVOX_STYLE", "ノーマル"),
    "pitch": float(os.environ.get("V3_VOICEVOX_PITCH", "-0.01")),
    "intonation": float(os.environ.get("V3_VOICEVOX_INTONATION", "1.0")),
    "speed": float(os.environ.get("V3_VOICEVOX_SPEED", "1.38")),
}

SITE = "ai-tech-express.jp"
BRAND = "サイソクニュース"

# 公式の製品・料金ページ（記事の主役がこれに当たるときだけ、発表ページと一緒に撮る）
PRODUCT_PAGES = [
    (re.compile(r"Claude", re.I), "https://claude.com/pricing"),
    (re.compile(r"ChatGPT|Codex", re.I), "https://chatgpt.com/pricing"),
    (re.compile(r"Gemini", re.I), "https://ai.google.dev/gemini-api/docs/pricing?hl=ja"),  # 言語を固定（撮るたびに英語・日本語が変わるため）
]

AD_HOSTS = v2.AD_HOSTS


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def ease(k: float) -> float:
    """ease-in-out（3次）"""
    k = min(max(k, 0.0), 1.0)
    return 4 * k * k * k if k < 0.5 else 1 - (-2 * k + 2) ** 3 / 2


def ease_out(k: float) -> float:
    k = min(max(k, 0.0), 1.0)
    return 1 - (1 - k) ** 3


class Timer:
    def __init__(self):
        self.t0 = time.time()
        self.stages: dict[str, float] = {}

    def mark(self, name: str, since: float) -> None:
        self.stages[name] = round(time.time() - since, 2)
        log(f"[v3] {name}: {self.stages[name]:.1f}秒")


# --------------------------------------------------------------------------- #
# 1. 公式ページの撮影（本物のブラウザ・並列）
# --------------------------------------------------------------------------- #

HIDE_JS = r"""() => {
  const bad = /cookie|consent|privacy|gdpr|同意|クッキー|newsletter|subscribe|chat/i;
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
    const r = el.getBoundingClientRect();
    const text = (el.innerText || '') + ' ' + (el.id || '') + ' ' + (el.className || '');
    if (r.top <= 2 && r.height < 140 && !bad.test(text)) { el.style.setProperty('position', 'absolute', 'important'); continue; }
    el.style.setProperty('display', 'none', 'important');
  }
}"""

SCROLL_JS = r"""async (maxY) => {
  const h = Math.min(maxY, document.documentElement.scrollHeight);
  for (let y = 0; y < h; y += 800) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 70)); }
  window.scrollTo(0, 0); await new Promise(r => setTimeout(r, 200));
}"""

# 文字ノードごとに、1文字ずつの位置（ページ座標・CSS px）とまとまり（ブロック）を集める
TEXT_JS = r"""(maxY) => {
  const blockOf = (el) => { while (el && el !== document.body) { const d = getComputedStyle(el).display; if (!d.startsWith('inline') && d !== 'contents') return el; el = el.parentElement; } return document.body; };
  const blocks = new Map(); const nodes = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const range = document.createRange(); let n; let budget = 20000;
  while ((n = walker.nextNode()) && budget > 0) {
    const t = n.nodeValue; if (!t || !t.trim()) continue;
    const el = n.parentElement; if (!el || el.closest('script,style,noscript,svg,button,nav,footer')) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.2) continue;
    range.selectNodeContents(n); const b = range.getBoundingClientRect();
    if (b.width < 1 || b.height < 1) continue;
    const top = b.top + scrollY; if (top > maxY || b.bottom + scrollY < 0) continue;
    const blk = blockOf(el); let id = blocks.get(blk); if (id === undefined) { id = blocks.size; blocks.set(blk, id); }
    const chars = [];
    for (let i = 0; i < t.length; i++) {
      range.setStart(n, i); range.setEnd(n, i + 1); const c = range.getBoundingClientRect();
      chars.push(c.width > 0 ? [c.left + scrollX, c.top + scrollY, c.right + scrollX, c.bottom + scrollY].map(v => Math.round(v * 10) / 10) : null);
    }
    budget -= t.length;
    nodes.push({ b: id, t, c: chars, fs: parseFloat(cs.fontSize) || 16 });
  }
  const out = [];
  for (const [el, id] of blocks) { const r = el.getBoundingClientRect(); out[id] = [r.left + scrollX, r.top + scrollY, r.right + scrollX, r.bottom + scrollY]; }
  return { blocks: out, nodes, height: document.documentElement.scrollHeight, title: document.title };
}"""


@dataclass
class Block:
    id: str
    text: str
    rect: tuple  # ページの画素（CSS px × dpr）
    chars: list  # [(文字, rect or None)]
    font_px: float


@dataclass
class Page:
    url: str
    host: str
    img: np.ndarray  # RGB uint8
    blocks: list[Block]
    title: str = ""
    seconds: float = 0.0

    @property
    def width(self) -> int:
        return self.img.shape[1]

    @property
    def height(self) -> int:
        return self.img.shape[0]

    def bg(self) -> tuple:
        patch = self.img[: min(40, self.height), : min(40, self.width)].reshape(-1, 3)
        return tuple(int(x) for x in np.median(patch, axis=0))


def _blocks_from(data: dict, page_no: int) -> list[Block]:
    k = CAPTURE_DPR
    grouped: dict[int, list] = {}
    sizes: dict[int, float] = {}
    for node in data["nodes"]:
        grouped.setdefault(node["b"], []).extend(zip(node["t"], node["c"]))
        sizes[node["b"]] = max(sizes.get(node["b"], 0), node["fs"])
    out = []
    for bid, chars in grouped.items():
        rect = data["blocks"][bid] if bid < len(data["blocks"]) and data["blocks"][bid] else None
        text = re.sub(r"\s+", " ", "".join(c for c, _ in chars)).strip()
        if not rect or not text:
            continue
        scaled = [(c, tuple(v * k for v in r) if r else None) for c, r in chars]
        out.append(Block(f"{page_no}-{len(out) + 1}", text, tuple(v * k for v in rect), scaled, sizes[bid]))
    out.sort(key=lambda b: (b.rect[1], b.rect[0]))
    for i, b in enumerate(out):
        b.id = f"{page_no}-{i + 1}"
    return out


async def _capture_one(browser, url: str, page_no: int, timeout_ms: int) -> Page | None:
    started = time.time()
    ctx = await browser.new_context(viewport={"width": CAPTURE_W, "height": 960}, device_scale_factor=CAPTURE_DPR, locale="ja-JP", extra_http_headers={"Accept-Language": "ja,en;q=0.8"})
    try:
        page = await ctx.new_page()
        await page.route("**/*", lambda r: r.abort() if any(h in r.request.url for h in AD_HOSTS) else r.continue_())
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if not resp or resp.status >= 400:
            log(f"   [v3] 撮れない（HTTP {resp.status if resp else '?'}）: {url}")
            return None
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        title = await page.title()
        if re.search(r"just a moment|attention required|access denied", title, re.I):
            log(f"   [v3] ボット確認の画面なので使わない: {url}")
            return None
        await page.evaluate(SCROLL_JS, CAPTURE_MAX_H)
        await page.evaluate(HIDE_JS)
        # 読み込みに失敗した・まだのフォントをもう一度読む（失敗すると本文が明朝の代わりの字になる）
        await page.evaluate("Promise.race([Promise.all([...document.fonts].filter(f => f.status !== 'loaded').map(f => f.load().catch(() => null))), new Promise(r => setTimeout(r, 4000))]).then(() => true)")
        await page.evaluate("document.fonts.ready.then(() => true)")
        # 普通の画面の高さのまま測り、上から1画面ずつ撮ってつなぐ（Chrome は 16384px を超える1枚を正しく撮れない。
        # 画面の高さを変えると配置が変わるので、測るときと撮るときの配置を同じにする）
        await page.evaluate("document.documentElement.style.scrollBehavior = 'auto'; window.scrollTo(0, 0)")
        await page.wait_for_timeout(150)
        data = await page.evaluate(TEXT_JS, CAPTURE_MAX_H)
        height = int(min(data["height"], CAPTURE_MAX_H))
        shots = []
        y = 0
        while y < height:
            got = await page.evaluate(f"window.scrollTo(0, {y}); window.scrollY")
            await page.wait_for_timeout(90)
            got = await page.evaluate("window.scrollY")
            shots.append((got, await page.screenshot(timeout=timeout_ms)))
            if got + 960 >= height or got < y:
                break
            y = got + 960
    except Exception as error:  # noqa: BLE001
        log(f"   [v3] 撮影に失敗: {url}: {type(error).__name__}: {str(error)[:120]}")
        return None
    finally:
        await ctx.close()
    k = CAPTURE_DPR
    img = None
    for top, png in shots:
        arr = np.frombuffer(png, dtype=np.uint8)
        if cv2 is not None:
            part = cv2.cvtColor(cv2.imdecode(arr, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        else:  # pragma: no cover
            import io

            part = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
        if img is None:
            img = np.zeros((height * k, part.shape[1], 3), dtype=np.uint8)
        y0 = int(top * k)
        h = min(part.shape[0], img.shape[0] - y0)
        if h > 0:
            img[y0 : y0 + h] = part[:h]
    if img.reshape(-1, 3)[::97].std() < 6:
        log(f"   [v3] ほぼ単色（読み込み失敗）なので使わない: {url}")
        return None
    host = urllib.parse.urlparse(url).netloc.removeprefix("www.")
    blocks = _blocks_from(data, page_no)
    return Page(url, host, img, blocks, title, round(time.time() - started, 1))


async def _capture_all(urls: list[str], timeout_ms: int) -> list[Page | None]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chrome", headless=True)
        except Exception:  # noqa: BLE001
            browser = await p.chromium.launch(headless=True)
        try:
            return await asyncio.gather(*[_capture_one(browser, u, i + 1, timeout_ms) for i, u in enumerate(urls)])
        finally:
            await browser.close()


def capture_pages(urls: list[str], timeout_ms: int = 25000) -> list[Page]:
    try:
        got = asyncio.run(_capture_all(urls, timeout_ms))
    except ImportError:
        log("   [v3] playwright が無いのでページを撮れない")
        return []
    pages = [p for p in got if p is not None]
    for i, p in enumerate(pages):  # 撮れたページだけで番号を振り直す
        for b in p.blocks:
            b.id = f"{i + 1}-{b.id.split('-')[1]}"
    return pages


def page_urls(article: dict, extra: list[str] | None = None) -> list[str]:
    """撮ってよいページ: 記事の出典が公式（v2 と同じ判定）なら出典、主役の製品の公式料金ページ。最大3つ"""
    urls = []
    if src := v2.official_page_url(article):
        urls.append(src)
    who = " ".join([article.get("title", ""), " ".join(article.get("tags") or []), " ".join((article.get("keywords") or [])[:4])])
    for pattern, url in PRODUCT_PAGES:
        if pattern.search(who) and url not in urls:
            urls.append(url)
    urls = urls[:3]
    for url in extra or []:  # 台本に合わせて足す公式ページ（他社の料金表など。最大5つ）
        if url not in urls:
            urls.append(url)
    return urls[:5]


# --------------------------------------------------------------------------- #
# 文字の場所を探す（台本の target → ページ上の枠）
# --------------------------------------------------------------------------- #


def _norm_chars(chars: list) -> tuple[str, list]:
    """1文字ずつ NFKC・小文字・空白をまとめる（元の文字の枠を保つ）"""
    out, rects = [], []
    for ch, r in chars:
        n = unicodedata.normalize("NFKC", ch).casefold()
        for c in n:
            if c.isspace():
                if out and out[-1] == " ":
                    continue
                c = " "
            out.append(c)
            rects.append(r)
    return "".join(out), rects


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


@dataclass
class Target:
    page: int  # 0 始まり
    block: Block
    rect: tuple  # 一致した文字の枠（ページの画素）
    text: str
    focus: tuple | None = None  # キーワードの数字の部分（例: $200）の枠。寄る先
    match: tuple | None = None  # target の文字そのものの枠（選択の青を付ける所・行の中の一部）
    line_chars: list = field(default_factory=list)  # 注目の行の1文字ずつ [(文字, 枠)]（語の頭で切るため）
    rows: list = field(default_factory=list)  # target の文字の行ごとの枠（複数行の文の選択・訳の吹き出しの位置）

    @property
    def span(self) -> tuple:
        """target の文字全体の枠（複数行ならすべての行）"""
        return _union(self.rows) if self.rows else (self.match or self.rect)

    @property
    def zoom_rect(self) -> tuple:
        return self.focus or self.rect


def _union(boxes: list) -> tuple:
    return (min(r[0] for r in boxes), min(r[1] for r in boxes), max(r[2] for r in boxes), max(r[3] for r in boxes))


def _first_line(boxes: list) -> list:
    """複数行にまたがる一致は最初の行だけ（注目の枠が大きくなりすぎないように）"""
    top = boxes[0][1]
    h = boxes[0][3] - boxes[0][1]
    return [r for r in boxes if abs(r[1] - top) < h * 0.6]


def find_target(pages: list[Page], needle: str, block_id: str = "", page_no: int = 0, keyword: str = "") -> Target | None:
    needle_n = _norm(needle)
    if len(needle_n) < 2:
        return None
    order = []
    for pi, page in enumerate(pages):
        for b in page.blocks:
            score = (0 if b.id == block_id else 1, 0 if pi + 1 == page_no else 1, -b.font_px, len(b.text))
            order.append((score, pi, b))
    order.sort(key=lambda x: x[0])
    for _, pi, b in order:
        flat, rects = _norm_chars(b.chars)
        at = flat.find(needle_n)
        if at < 0:
            continue
        boxes = [r for r in rects[at : at + len(needle_n)] if r]
        if not boxes:
            continue
        focus = None
        for num in _digits(keyword):
            # キーワードの数字（前の $ と後ろの単位まで）を、一致した所 → ブロック全体の順に探す
            pat = r"[$¥￥]?" + r"(?<![\d.])" + re.escape(num) + r"(?![\d])(?:\s?(?:%|x|×|倍|円|ドル|時間|分|秒|万|億|兆|人|件|社|gb|tb))?"
            m = re.search(pat, flat[at : at + len(needle_n)])
            off = at
            if not m:
                m, off = re.search(pat, flat), 0
            if m:
                fb = [r for r in rects[off + m.start() : off + m.end()] if r]
                if fb:
                    focus = _union(fb)
                    break
        line = _first_line(boxes)
        ref = focus or line[0]
        row = lambda r: abs(r[1] - ref[1]) < (ref[3] - ref[1]) * 0.6  # noqa: E731
        match = _union([r for r in boxes if row(r)] or line)
        line_chars = [(c, r) for c, r in zip(flat, rects) if r and row(r)]
        if focus:  # 注目の枠は数字を含む行
            all_boxes = [r for r in rects if r]
            line = [r for r in all_boxes if row(r)] or line
        rows: list[list] = []
        for r in boxes:  # target の文字を行ごとにまとめる
            if rows and abs(r[1] - rows[-1][0][1]) < (r[3] - r[1]) * 0.6:
                rows[-1].append(r)
            else:
                rows.append([r])
        return Target(pi, b, _union(line), needle, focus, match, line_chars, [_union(x) for x in rows])
    return None


def snippets(pages: list[Page], per_page: int = 70) -> str:
    rows = []
    for pi, page in enumerate(pages):
        rows.append(f"## ページ{pi + 1}: {page.host}（{page.title[:60]}）")
        seen = set()
        n = 0
        for b in page.blocks:
            t = b.text[:200]
            if len(t) < 2 or t in seen:
                continue
            seen.add(t)
            big = "|大" if b.font_px >= 22 else ""
            rows.append(f"[{b.id}{big}] {t}")
            n += 1
            if n >= per_page:
                break
    return "\n".join(rows)


# --------------------------------------------------------------------------- #
# 2. 台本（LLM。記事の事実だけ・画面の証拠つき）
# --------------------------------------------------------------------------- #

_BEAT = {
    "type": "OBJECT",
    "properties": {
        "label": {"type": "STRING"},
        "keyword": {"type": "STRING"},
        "sub": {"type": "STRING"},
        "speech": {"type": "STRING"},
        "block": {"type": "STRING"},
        "target": {"type": "STRING"},
        "ja": {"type": "STRING"},
    },
    "required": ["label", "keyword", "sub", "speech", "block", "target", "ja"],
}
_ROW = {
    "type": "OBJECT",
    "properties": {
        "name": {"type": "STRING"},
        "price": {"type": "STRING"},
        "usage": {"type": "STRING"},
        "mult": {"type": "STRING"},
        "block": {"type": "STRING"},
        "target": {"type": "STRING"},
    },
    "required": ["name", "price", "usage", "mult", "block", "target"],
}
SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "hook": {
            "type": "OBJECT",
            "properties": {"text": {"type": "STRING"}, "payoff": {"type": "STRING"}, "speech": {"type": "STRING"}, "block": {"type": "STRING"}, "target": {"type": "STRING"}, "ja": {"type": "STRING"}},
            "required": ["text", "payoff", "speech", "block", "target", "ja"],
        },
        "beats": {"type": "ARRAY", "items": _BEAT},
        "compare": {
            "type": "OBJECT",
            "properties": {"beat": {"type": "INTEGER"}, "base": {"type": "STRING"}, "subject": {"type": "STRING"}, "ratio": {"type": "STRING"}},
            "required": ["beat", "base", "subject", "ratio"],
        },
        "ladder": {
            "type": "OBJECT",
            "properties": {"beat": {"type": "INTEGER"}, "subject": {"type": "STRING"}, "rows": {"type": "ARRAY", "items": _ROW}},
            "required": ["beat", "subject", "rows"],
        },
        "units": {
            "type": "OBJECT",
            "properties": {
                "beat": {"type": "INTEGER"},
                "unit": {"type": "STRING"},
                "subject": {"type": "STRING"},
                "rows": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}, "count": {"type": "INTEGER"}}, "required": ["name", "count"]}},
            },
            "required": ["beat", "unit", "subject", "rows"],
        },
        "conclusion": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}, "speech": {"type": "STRING"}}, "required": ["text", "speech"]},
        "short_beats": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        "title": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "propertyOrdering": ["hook", "beats", "compare", "ladder", "units", "conclusion", "short_beats", "title", "summary", "hashtags"],
    "required": ["hook", "beats", "compare", "ladder", "units", "conclusion", "short_beats", "title", "summary", "hashtags"],
}


BEATS_MIN, BEATS_MAX = 5, 7
SPEECH_TOTAL_MAX = 330  # 読み上げの合計（九州そら・速さ1.38 で約1秒5字 → 約65秒）


def script_system() -> str:
    import persona

    p = persona.PERSONA
    return f"""あなたはニュースサイト「{persona.BRAND}」の横型の解説動画（45〜70秒）の構成作家。
動画は「画面で見せる型」: 公式ページの本物の画面をズームして証拠を見せる。画面に出す文字はキーワードだけで、話はナレーションが運ぶ。
見る人: {p['who']}
見る人が知りたいこと: 自分の仕事・お金にどう関係するか。いくらで、何と比べてどれくらい違うか。自分は必要か。どこを押せば確かめられるか。
ナレーションの話し方: {p['voice']} 詳しくて頼れる先輩が話すように、です・ます調で短い文。専門用語は普通の言葉に言い換える。

# いちばん大事なこと
hook の問いに、**具体的な数字と比較で答える**。忙しい会社員が頭の中で絵を描ける答えにする。
「Proで足りない人向けです」のような、聞かなくても分かる答え・中身の無い言い回しは禁止。
例（料金のニュース）: 問い「最上位プラン、月200ドル必要？」→ 答え「多くの人は月20ドルのProで足ります。毎日長い作業を任せる人だけ」
→ 料金の段階（Free 0ドル・Pro 20ドル・Max 100ドル・Ultra 200ドル）と使える量（Proの5倍・10倍）を並べる
→ たとえ（Proの1週間分を1杯とすると、Maxは5杯、Ultraは10杯）→ 必要な人・要らない人の具体的な目安 → 自分の使用量の確かめ方。

# 事実の決まり
- 使ってよい事実は「資料」の記事と「公式ページの文字」に書かれていることだけ。推測・一般論・他社の情報の追加は禁止。
- 数字・価格・製品名は資料の表記どおり。資料に無い数字は書かない（メッセージ数・時間など、公式に無い数字を作らない）。
  公式に1回あたりの数字が無いときは、倍率（Proの5倍・10倍）と身近な場面（1週間分・1日の作業）で言い換える。
- 「初」「最大」「最高」「最速」「最安」「唯一」などの評価語は資料に同じ言葉があるときだけ。
- 煽らない（衝撃・ヤバい・革命・神・ついに・必見は禁止）。絵文字・記号の飾りは禁止。「ざっくり」は使わない（まとめるときは「要約すると」）。

# 構成
- hook（最初の2秒）: text は画面に大きく出す1行（12〜20字）。見る人が「自分ごと」になる問いかけ。
  payoff はその答えの手がかりになる数字のキーワード（8字以内・資料の表記どおり。例: 月200ドル）。speech は text を自然に読む1文（20字以内）。
  block / target は payoff の証拠になる画面の文字。できれば文字の大きい行（番号に「|大」）を選ぶ。
- beats: {BEATS_MIN}〜{BEATS_MAX}個。話題に合わせて次の役目から選んで、この順に並べる（label は左上に小さく出す見出し・2〜7字）:
  1. 答え（label 例: 答え）: hook の問いへの答えを1行で。誰ならイエスか。
  2. 比較（label 例: プランの比較・前と比べると）: 料金のニュースなら料金の段階を並べる（ladder を使う）。製品のニュースなら前の版・他のプランとの違い。
     並べて初めて分かる違いを1つ必ず言う（例: Maxは月100ドルでProの5倍、Ultraは月200ドルでProの10倍＝1ドルあたりの量は同じ。
     だからUltraを選ぶ理由は量ではなく、5時間制限が無いこと）。計算の答えを新しい数字として書かない（「同じ」「倍」のように言葉で）。
  3. たとえ（label 例: たとえると）: 公式の数字・倍率だけを使って、毎日の仕事の場面に置き換える。
     比較（ladder・compare）で同じ倍率をもう言っているなら、たとえの場面は作らない（その時間を「必要な人」の具体的な場面に回す）。
  4. 必要な人（label 例: 必要な人・こんな使い方）: **頭に絵が浮かぶ、具体的な1日の仕事の場面**で言う。種類の名前（資料作り・レポート・表計算）で終わらせない。
     例: 「営業データの集計からグラフ、提案スライドまで、1日中まとめて任せる」「Claude Codeで、1日かけてアプリの修正を進める」
     「月曜の朝に、先週の問い合わせ100件を分類して、返信の下書きまで作らせる」のように、作業の流れが分かる言い方。
     見る人は20〜50代の会社員・個人事業主。場面の中の作業は例として自由に書いてよいが、数字（件数・時間など）は資料にあるものだけ。資料に無い数字を場面に入れない。
     画面の keyword・sub も場面そのものを短く（例: keyword「集計→提案書を丸投げ」sub「朝から晩まで任せる」）。「長い作業を任せる」のような種類の言葉にしない。
  5. 要らない人・注意（label 例: 要らない人・注意）: これも具体的な仕事の場面で（例: 「1日に数回、メールの言い回しを相談する程度ならPro」）。条件（月払いのみ・アプリ経由は価格が違う）も入れてよい。
  6. 確かめ方・使い方（label 例: 確かめ方・使い方）: どこを押せば確かめられる・試せるか（例: 設定の使用状況）。
  keyword: 画面に大きく出す言葉。2〜10字（短いほどよい）。文にしない。句読点を付けない。sub: 補足（14字以内。不要なら空）。
  speech: 読み上げ。短い文で1〜3文・60字以内。keyword の数字・名前を必ず含める。
  block: 下の「画面の文字」一覧の番号（例: 1-23）。target: その行の文字から、この beat の証拠になる部分を**一文字も変えずに**抜き出す（日本語は2〜40字。英語は100字まで）。
  英語の target は文として完結する範囲（文の頭から、文末のピリオドかカンマまで）を選ぶ。文の途中で切らない。
  ja: target が英語のときだけ、target の日本語訳（画面のその英文のすぐ下に訳として出す）。文全体を、自然なです・ます調で（36字以内）。
  直訳の硬さを避け、文の一部だけを訳さない・言葉を足さない・target に無い数字を入れない。製品名は英字のまま。target が日本語なら空文字。hook も同じ。
  英文の頭の返事の語（Yes. / No.）は訳さなくてよい。
  訳は、その場面の読み上げを裏付ける内容のときだけ付ける。読み上げと関係の薄い英文（例: 要らない人の場面に性能の説明文）を証拠にしない。
  短い句（「Starting this week,」など）しか証拠に無いときは ja を空にする（訳の吹き出しは出さない）。
- **同じことを2回言わない。** どの場面も、前の場面に無い新しい情報を1つ足す（倍率・価格・条件を別の場面で言い直さない。絵も同じ倍率を2回見せない）。
- 読み上げ（hook・beats・conclusion の speech）の合計は{SPEECH_TOTAL_MAX - 30}字前後まで（約60秒）。
- ladder: 料金の段階を比べる beat があるときだけ（無ければ beat=0・rows は空）。beat はその beat の番号、subject は話の主役のプラン名（例: Ultra）。
  rows は安い順に2〜5行。name はプラン名、price は画面に出す価格（例: $20/月。公式ページの表記の数字どおり）、
  usage は使える量の短い説明（例: Proの5倍。公式の表記の範囲で。8字以内）、mult は基準（例: Pro）を1としたときの倍率の数字だけ（例: 5。分からなければ空）。
  block / target は、その行の価格が書かれた公式ページの文字（料金表のカードの価格など）。target は一覧の文字をそのまま。
- units: 「たとえ」の beat を絵にするときだけ。ladder を使うときは使わない（同じ倍率を2回見せない）。無ければ beat=0・rows は空。基準の量を1つの四角として、各プランが四角いくつ分かを並べる。
  beat はその beat の番号、unit は基準の言い方（例: Proの1週間分。12字以内）、subject は主役（例: Ultra）、
  rows は2〜4行で name と count（公式の倍率の数字どおり。例: Pro 1・Max 5・Ultra 10。12まで）。公式に倍率が無ければ使わない。
- compare: 2つを倍率で比べる事実があり、ladder を使わないときだけ。beat はその beat の番号、base は基準（例: Pro）、subject は相手（例: Ultra）、
  ratio は資料の表記どおりの倍率（例: 10倍）。無ければ beat=0、他は空文字。
- short_beats: SNS 用の短い版（30〜40秒）に使う beat の番号を2つ（例: [1, 2]）。1つめは「答え」、2つめは「並べて初めて分かる違い」を言う beat。
  短い版は hook → この2つ → 「◯◯は記事で」で終わるので、この2つだけで問いへの本当の答えが伝わる beat を選ぶ。
- conclusion: 最後の一言。text は16字以内で hook の問いに答える。speech は14〜30字。
- title: 40字以内・主役の名前を入れて事実をそのまま。summary: 説明欄の要約。「要約すると、」で始めて80〜140字。hashtags: 3〜4個（#付き）。

# 画面の文字（text・payoff・keyword・sub・conclusion.text・ja・ladder）
- 製品名・社名は資料の表記のまま英字で書く（Claude・Gemini・Ultra。カタカナにしない）。カタカナにするのは speech だけ。

# 読み上げ（speech）
- 英語の製品名・社名はカタカナ（Claude→クロード、OpenAI→オープンエーアイ、Pro→プロ）。記号は言葉に（$200→200ドル、%→パーセント）。
- 数字はアラビア数字のまま（かなや漢数字にしない）。"""


REVIEW_SCHEMA = {
    "type": "OBJECT",
    "properties": {"ok": {"type": "BOOLEAN"}, "issues": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["ok", "issues"],
}


def review_system() -> str:
    return """あなたは経済ニュース番組の厳しい編集長。短い解説動画の台本を読んで、次を判定する。
1. hook の問いに、具体的な数字や比較で答えているか（忙しい会社員が頭の中で絵を描けるか）。
2. 聞かなくても分かる当たり前の文・中身の無い言い回し（例:「Proで足りない人向けです」「用途に合わせて選びましょう」）が無いか。
3. 料金のニュースなら料金の段階、製品のニュースなら使い方と比較があるか。並べて初めて分かる違い（例: 1ドルあたりの量が同じ・選ぶ理由は量ではなく制限の有無）を1つ言っているか。
4. 必要な人・要らない人が、具体的な1日の仕事の場面になっているか（「資料作り」「レポート」のような種類の名前で終わっていたら不合格。
   例「営業データの集計からグラフ、提案スライドまで1日中まとめて任せる」のように作業の流れが絵になるか）。最後に、どこで確かめられるかが分かるか。
6. 同じことを2回言っていないか（倍率・価格・条件の言い直し、同じ倍率の絵が2回など）。どの場面も新しい情報を足しているか。
7. 画面の keyword・sub も具体的か（必要な人の keyword が「長い作業を任せる」のような種類の言葉なら不合格）。訳（ja）がその場面の読み上げを裏付けているか。
5. 資料（記事と公式ページの文字）に無い数字や事実を足していないか。
すべて満たすときだけ ok=true。足りないときは ok=false にし、issues に直し方を具体的に4つまで書く（例:「beat3 は倍率をProの1週間分=1杯のように身近に言い換える」）。
資料に無い数字を足すような直し方は書かない。target（証拠）は画面の文字をそのまま、英語は文の頭から文末かカンマまでの範囲なので、文の途中で切る直し方は書かない。
細かい言い回しより、中身（問いへの答え・比較・目安）を優先する。"""


def official_text(pages: list[Page], limit: int | None = None) -> str:
    """公式ページの文字（事実として使ってよい）。大きな見出し（料金表のプラン名など）ごとに1行にまとめる
    （「Max / 終日の業務に / $100 / 月額 / Pro の 5 倍の使用量」が同じ行＝同じ話題として数字の文脈を確かめられる）"""
    out = []
    for page in pages:
        out.append(f"## {page.host}（{page.title[:50]}）")
        line: list[str] = []
        for b in page.blocks:
            if b.font_px >= 24 and line:
                out.append(" / ".join(line).replace("。", "、"))  # 1つの見出しの中は1つの話題（数字の文脈の確認で文を分けない）
                line = []
            if b.text and (not line or line[-1] != b.text):
                line.append(b.text[:300])
        if line:
            out.append(" / ".join(line).replace("。", "、"))
    text = "\n".join(out)
    return text[:limit] if limit else text


def with_official(article: dict, pages_or_text) -> dict:
    """事実の照合に使う資料: 記事 ＋ 公式ページの文字（撮った公式ページは一次情報なので、数字の出どころとして認める）"""
    text = pages_or_text if isinstance(pages_or_text, str) else official_text(pages_or_text)
    return gate.with_official(article, text)


# 画面の文字にカタカナの製品名が入ったら英字に直す（読み上げ用のカタカナが画面に漏れるのを防ぐ）
KATAKANA_NAMES = {
    "クロード": "Claude", "アンソロピック": "Anthropic", "ジェミニ": "Gemini", "グーグル": "Google",
    "チャットジーピーティー": "ChatGPT", "オープンエーアイ": "OpenAI", "コーデックス": "Codex", "ウルトラ": "Ultra",
    "カーボン": "Carbon", "アルゴン": "Argon", "マイクロソフト": "Microsoft", "コパイロット": "Copilot",
}  # fmt: skip
SCREEN_FIELDS = (("hook", "text"), ("hook", "payoff"), ("hook", "ja"), ("conclusion", "text"))


def latin_names(script: dict) -> dict:
    """画面の文字のカタカナの製品名を英字に置き換える（台本を直接直す）"""
    def fix(text: str) -> str:
        for kata, latin in KATAKANA_NAMES.items():
            text = text.replace(kata, latin)
        return text

    for a, b in SCREEN_FIELDS:
        if isinstance(script.get(a), dict) and isinstance(script[a].get(b), str):
            script[a][b] = fix(script[a][b])
    for beat in script.get("beats") or []:
        for k in ("keyword", "sub", "ja"):
            if isinstance(beat.get(k), str):
                beat[k] = fix(beat[k])
    return script


def item_block_text(pages: list, item: dict) -> str:
    """台本の block 番号の行の文字（見出しなど、文末の記号が無くても行全体なら完結とみなす）"""
    for p in pages:
        for b in p.blocks:
            if b.id == item.get("block"):
                return b.text
    return ""


def clause_ok(target: str, block_text: str) -> bool:
    """英語の target が「意味のまとまり」か: 文の頭から始まり、文末・カンマ・コロンなどの区切りで終わる"""
    t, b = _norm(target), _norm(block_text)
    at = b.find(t)
    if at < 0:
        return bool(re.search(r"[.?!:;,]\s*$", t))
    before, after = b[:at].rstrip(), b[at + len(t) :]
    starts = not before or before[-1] in ".?!:;"
    ends = bool(re.search(r"[.?!:;,]$", t)) or not after.strip() or after.lstrip()[:1] in ".?!:;,"
    return starts and ends


def is_english(text: str) -> bool:
    """英語の文・句か（字幕を付けるか）: 英字の語が2つ以上で、かなと漢字が無い"""
    return len(re.findall(r"[A-Za-z]{2,}", text or "")) >= 2 and not re.search(r"[ぁ-んァ-ヴ一-龥]", text or "")


def script_material(article: dict, pages: list[Page]) -> str:
    return (
        v2.article_material(article)
        + "\n\n# 公式ページの文字（事実として使ってよい・証拠の場所は下の一覧の番号で）\n" + official_text(pages, 24000)
        + "\n\n# 画面の文字（[番号] 文字）\n" + snippets(pages)
    )


def flat_script(s: dict) -> dict:
    """ゲート（v2 と同じ事実照合）に渡す形: hook / points / use / numbers / title / headline"""
    hook = s.get("hook") or {}
    beats = s.get("beats") or []
    comp = s.get("compare") or {}
    numbers = []
    if hook.get("payoff"):
        numbers.append({"value": hook["payoff"], "label": ""})
    if comp.get("beat") and comp.get("ratio"):
        numbers.append({"value": comp["ratio"], "label": ""})
    lad = s.get("ladder") or {}
    ladder_text = " ".join(f"{r.get('name', '')} {r.get('price', '')} {r.get('usage', '')}" for r in (lad.get("rows") or []) if lad.get("beat"))
    return {
        "title": s.get("title", ""),
        "headline": hook.get("text", ""),
        "hook": {"caption": hook.get("text", ""), "speech": hook.get("speech", "")},
        "points": [{"caption": f"{b.get('keyword', '')} {b.get('sub', '')}".strip(), "speech": b.get("speech", "")} for b in beats],
        "use": {"caption": (s.get("conclusion") or {}).get("text", ""), "speech": (s.get("conclusion") or {}).get("speech", "")},
        "numbers": numbers,
        "hook_text": " ".join(x for x in [hook.get("payoff", ""), comp.get("base", "") if comp.get("beat") else "", comp.get("subject", "") if comp.get("beat") else "", ladder_text] if x),
        "summary": s.get("summary", ""),
        "hashtags": s.get("hashtags", []),
    }


def _digits(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", gate.norm(text))


def script_problems(s: dict, article: dict, pages: list[Page]) -> list[str]:
    out = []
    article = with_official(article, pages)  # 事実の出どころ: 記事 ＋ 撮った公式ページの文字
    hook, beats, concl = s.get("hook") or {}, s.get("beats") or [], s.get("conclusion") or {}
    if not (BEATS_MIN <= len(beats) <= BEATS_MAX):
        out.append(f"beats は{BEATS_MIN}〜{BEATS_MAX}個（今は{len(beats)}）。")
    if not (6 <= len(hook.get("text", "")) <= 22):
        out.append(f"hook.text は12〜20字（今は{len(hook.get('text', ''))}字）。")
    if len(hook.get("payoff", "")) > 9:
        out.append("hook.payoff は8字以内。")
    if len(concl.get("text", "")) > 18:
        out.append("conclusion.text は16字以内。")
    for i, b in enumerate(beats, 1):
        kw = b.get("keyword", "")
        if not (2 <= len(kw) <= 12) or re.search(r"[、。！？]", kw):
            out.append(f"beat{i}.keyword は2〜10字で句読点なし（{kw}）。")
        if len(b.get("sub", "")) > 16:
            out.append(f"beat{i}.sub は14字以内。")
        if len(b.get("speech", "")) > 66:
            out.append(f"beat{i}.speech が長すぎます（60字以内。今は{len(b.get('speech', ''))}字）。")
        if not (2 <= len(b.get("label", "")) <= 9):
            out.append(f"beat{i}.label は2〜7字の見出し（例: 答え・プランの比較）。")
    total_speech = sum(len(x.get("speech", "")) for x in [hook, concl] + beats)
    if total_speech > SPEECH_TOTAL_MAX:
        out.append(f"読み上げの合計が長すぎます（{total_speech}字。{SPEECH_TOTAL_MAX - 30}字前後まで）。")
    if total_speech < 190:
        out.append(f"読み上げの合計が短すぎます（{total_speech}字）。問いに数字と比較で答えるには足りません。")
    if len(hook.get("speech", "")) > 24:
        out.append("hook.speech は20字以内。")
    if len(concl.get("speech", "")) > 34:
        out.append("conclusion.speech は30字以内。")
    joined = json.dumps(s, ensure_ascii=False)
    if "ざっくり" in joined:
        out.append("「ざっくり」は使わない。")
    # 画面の証拠: target がページにあり、キーワードの数字がその行にある
    items = [("hook", hook.get("payoff") or hook.get("text", ""), hook)] + [(f"beat{i}", b.get("keyword", ""), b) for i, b in enumerate(beats, 1)]
    for name, kw, item in items:
        tgt = find_target(pages, item.get("target", ""), item.get("block", ""), keyword=kw) if pages else None
        if pages and not tgt:
            out.append(f"{name}.target「{item.get('target', '')}」が画面の文字にありません。一覧の文字をそのまま抜き出してください。")
            continue
        if tgt:
            have = {float(x) for x in _digits(tgt.block.text)}
            miss = [d for d in _digits(kw) if float(d) not in have]
            if miss:
                out.append(f"{name} の数字 {', '.join(miss)} が証拠の行 [{tgt.block.id}] にありません。数字が書かれた行を選んでください。")
    # 英語の証拠には日本語の字幕（target の訳）。訳に無い数字・記事に無い語を入れない
    mat = gate.material_text(article)
    mat_numbers = gate.number_tokens(mat)
    for name, _, item in items:
        tgt_text, ja = item.get("target", ""), (item.get("ja") or "").strip()
        if not is_english(tgt_text):
            continue
        if not ja:
            out.append(f"{name}.ja: target が英語なので、target の日本語訳を入れてください。")
            continue
        if len(ja) > 40:
            out.append(f"{name}.ja は36字以内（今は{len(ja)}字）。")
        if name != "hook" and not clause_ok(tgt_text, item_block_text(pages, item)):
            out.append(f"target の区切り: {name}.target「{tgt_text[:40]}」は文の途中で切れています。文の頭から、文末（ピリオド）か読点（カンマ）までのまとまりを選んでください。")
        extra = [d for d in _digits(ja) if d not in set(_digits(tgt_text))]
        if extra:
            out.append(f"{name}.ja に target に無い数字 {', '.join(extra)} があります。訳に言葉や数字を足さない。")
        if bad := gate.numbers_ok(ja, mat_numbers):
            out.append(f"{name}.ja の数字 {', '.join(bad)} が記事にありません。")
        if bad := gate.proper_noun_problems(ja, mat + " " + tgt_text):
            out.append(f"{name}.ja に記事・target に無い語 {', '.join(bad)}。")
    screen = [s.get("hook", {}).get(k, "") for k in ("text", "payoff", "ja")] + [(s.get("conclusion") or {}).get("text", "")]
    screen += [b.get(k, "") for b in beats for k in ("keyword", "sub", "ja")]
    speech_all = " ".join([hook.get("speech", ""), concl.get("speech", "")] + [b.get("speech", "") for b in beats])
    for text in screen:
        for kata in re.findall(r"[ァ-ヴー]{3,}", text or ""):
            if kata in speech_all and kata not in mat and kata not in gate.KATAKANA_OK:
                out.append(f"画面の文字「{text}」のカタカナ「{kata}」は読み上げ用の書き方です。画面では資料の英字の表記に。")
    # 料金の段階（ladder）: 各行の価格がその行の証拠（公式ページ）にあり、公式ページで名前と価格が同じ話題に出てくる
    lad = s.get("ladder") or {}
    if lad.get("beat"):
        rows = lad.get("rows") or []
        if not (1 <= int(lad["beat"]) <= len(beats)) or not (2 <= len(rows) <= 5):
            out.append("ladder は beat（beat の番号）と rows（2〜5行）をそろえるか、beat=0 に。")
        lines_off = [ln for ln in official_text(pages).split("\n")]
        for k, row in enumerate(rows, 1):
            for field_name, lim in (("name", 17), ("price", 12), ("usage", 10)):
                if len(row.get(field_name, "")) > lim:
                    out.append(f"ladder.rows[{k}].{field_name} が長すぎます（{lim - 1}字以内）。")
            tgt = find_target(pages, row.get("target", ""), row.get("block", ""), keyword=row.get("price", "")) if pages else None
            if pages and not tgt:
                out.append(f"ladder.rows[{k}].target「{row.get('target', '')}」が画面の文字にありません。")
            for d in _digits(row.get("price", "")):
                same = [ln for ln in lines_off if re.search(r"(?<![\d.])" + re.escape(d) + r"(?![\d])", gate.norm(ln)) and row.get("name", "").lower() in ln.lower()]
                if not same:
                    out.append(f"ladder.rows[{k}] の価格 {d} が、公式ページで「{row.get('name')}」と同じ所に出てきません（別のプランの数字では？）。")
            if row.get("mult") and not re.fullmatch(r"\d+(?:\.\d+)?", row["mult"].strip()):
                out.append(f"ladder.rows[{k}].mult は数字だけ（例: 5）か空。")
            for text in (row.get("price", ""), row.get("usage", ""), row.get("mult", "")):
                if bad := gate.numbers_ok(text, gate.number_tokens(gate.material_text(article))):
                    out.append(f"ladder.rows[{k}] の数字 {', '.join(bad)} が資料にありません。")
    uni = s.get("units") or {}
    if uni.get("beat") and (s.get("ladder") or {}).get("beat"):
        out.append("ladder と units を両方使っています。同じ倍率を2回見せないので units は beat=0 にし、たとえの場面の代わりに「必要な人」の具体的な仕事の場面を入れてください。")
    if uni.get("beat"):
        urows = uni.get("rows") or []
        if not (1 <= int(uni["beat"]) <= len(beats)) or not (2 <= len(urows) <= 4) or len(uni.get("unit", "")) > 13:
            out.append("units は beat・unit（12字以内）・rows（2〜4行）をそろえるか、beat=0 に。")
        mat_nums = gate.number_tokens(gate.material_text(article))
        for r in urows:
            c = int(r.get("count") or 0)
            if not (1 <= c <= 12):
                out.append(f"units.rows の count は1〜12（{r.get('name')}: {c}）。")
            elif str(c) not in mat_nums:
                out.append(f"units.rows の {r.get('name')} の数 {c} が資料（公式の倍率）にありません。")
        if bad := gate.numbers_ok(uni.get("unit", ""), mat_nums):
            out.append(f"units.unit の数字 {', '.join(bad)} が資料にありません。")
    comp = s.get("compare") or {}
    if comp.get("beat"):
        if not (1 <= int(comp["beat"]) <= len(beats)) or not comp.get("ratio") or not comp.get("base") or not comp.get("subject"):
            out.append("compare は beat（beat の番号）・base・subject・ratio をそろえるか、beat=0 に。")
        elif not re.search(r"\d", comp["ratio"]):
            out.append("compare.ratio は数字の倍率（例: 10倍）。")
    flat = flat_script(s)
    out += gate.number_context_problems(s, article)
    out += gate.fact_problems(flat, article) + gate.title_problems(flat, article, v2.main_name(article))
    out += v2.reading_problems(flat)
    return out


STYLE_PREFIXES = ("speech で数字", "speech に英単語", "target の区切り")


def review_script(data: dict, article: dict, pages: list[Page], tier: str) -> dict | None:
    import llm

    prompt = f"# 資料\n{script_material(article, pages)}\n\n# 台本\n{json.dumps(data, ensure_ascii=False)}\n\n判定を JSON で返してください。"
    try:
        verdict, _ = llm.generate_json(review_system(), prompt, REVIEW_SCHEMA, temperature=0.2, max_output_tokens=2048, tier=tier, visibility="high", purpose="video_review")
        return verdict
    except Exception as error:  # noqa: BLE001 - レビューが使えないときは、事実の検査に通った台本をそのまま使う
        log(f"   [v3] 台本のレビューに失敗: {type(error).__name__}")
        return None


def write_script(article: dict, pages: list[Page]) -> tuple[dict, str]:
    import llm

    prompt = f"# 資料\n{script_material(article, pages)}\n\n上の資料だけを使って、決まりどおりの台本を JSON で書いてください。"
    feedback, last = "", ""
    tier = os.environ.get("V3_LLM_TIER", "quality")
    reviews = 0
    best = None  # 事実の検査に通った最後の台本（レビューの書き直しが通らなかったときに使う）
    for attempt in range(1, 9):
        try:
            data, model = llm.generate_json(script_system(), prompt + feedback, SCRIPT_SCHEMA, temperature=0.5, max_output_tokens=8192, tier=tier, visibility="high", purpose="video")
        except RuntimeError:
            if tier == "bulk":
                raise
            tier = "bulk"  # 上位モデルの枠が尽きたら軽いモデルへ（事実の照合は同じ）
            log("   [v3] 台本: quality の枠が無いので bulk で書く")
            continue
        data["beats"] = [b for b in data.get("beats") or [] if isinstance(b, dict)][:BEATS_MAX]
        latin_names(data)
        problems = script_problems(data, article, pages)
        style = [p for p in problems if p.startswith(STYLE_PREFIXES)]
        if attempt >= 4 and len(style) == len(problems):
            # 英文の区切りが直らない beat は、訳の吹き出しを出さない（ja を空に）。途中で切れた文の訳は出さない
            for p in problems:
                if m := re.match(r"target の区切り: (beat(\d)|hook)\.", p):
                    item = data["hook"] if m.group(1) == "hook" else data["beats"][int(m.group(2)) - 1]
                    item["ja"] = ""
        if not problems or (attempt >= 4 and len(style) == len(problems)):
            best = (json.loads(json.dumps(data, ensure_ascii=False)), model, list(problems))
            # 自己レビュー: 問いに具体的な数字・比較で答えているか、当たり前・空の文が無いか（2回まで書き直す）
            if reviews < 2 and attempt < 8:
                reviews += 1
                verdict = review_script(data, article, pages, tier)
                if verdict and not verdict.get("ok") and verdict.get("issues"):
                    issues = [str(x) for x in verdict["issues"]][:8]
                    log(f"   [v3] 台本のレビュー（{reviews}）: 書き直し {issues}")
                    feedback = "\n\n# 編集長のレビュー（すべて直す。資料に無い数字は足さない）\n" + "\n".join(f"- {p}" for p in issues) + "\n\n# 前回の台本\n" + json.dumps(data, ensure_ascii=False)
                    continue
                log(f"   [v3] 台本のレビュー（{reviews}）: 合格")
            if problems:  # 読み・長さだけは最後に妥協する（事実・証拠の問題は必ず直す。入らない文字は描画で止まる）
                log(f"   [v3] 読み・長さの注意を残して採用: {problems}")
                data["_notes"] = problems
            data["_reviews"] = reviews
            return data, model
        last = "; ".join(problems)
        log(f"   [v3] 台本の書き直し（{attempt}）: {last[:300]}")
        feedback = "\n\n# 前回の台本の問題（すべて直す）\n" + "\n".join(f"- {p}" for p in problems) + "\n\n# 前回の台本\n" + json.dumps(data, ensure_ascii=False)
    if best:  # レビューのあとの書き直しが検査に通らなかった → 検査に通った前の台本を使う
        data, model, problems = best
        log(f"   [v3] レビュー後の書き直しが通らないので、検査に通った前の台本を使う（{last[:200]}）")
        if problems:
            data["_notes"] = problems
        data["_reviews"] = reviews
        return data, model
    raise RuntimeError(f"台本を作れませんでした: {last[:400]}")


# --------------------------------------------------------------------------- #
# 3. 声（VOICEVOX を最初に温め、行ごとに並列で合成）
# --------------------------------------------------------------------------- #


def warm_voicevox() -> threading.Thread | None:
    """VOICEVOX の最初の合成は遅いので、ページを撮っている間に1回合成しておく"""

    def run():
        try:
            tts = make_tts("voicevox", V3_VOICE["speed"])
            with tempfile.TemporaryDirectory() as tmp:
                tts.synth("はい。", Path(tmp) / "w.wav")
        except Exception as error:  # noqa: BLE001
            log(f"   [v3] VOICEVOX の準備: {type(error).__name__}")

    if not shorts.VoiceVox.available():
        return None
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th


@dataclass
class Line:
    kind: str  # hook / beat / conclusion
    index: int  # beat の番号（1〜）・hook=0・conclusion=beat の数+1
    speech: str
    start: float = 0.0
    end: float = 0.0


@dataclass
class Narration:
    lines: list[Line]
    samples: np.ndarray
    end_start: float
    total: float
    voice: dict


def wait_voicevox(timeout: float = 240.0) -> bool:
    started = time.time()
    while time.time() - started < timeout:
        if shorts.VoiceVox.available():
            return True
        time.sleep(2)
    return False


# 読み上げ用の読み（台本の文は変えず、声に渡すときだけ置き換える。長い語から順に）
TTS_READINGS = [
    ("Google AI Studio", "グーグル エーアイ スタジオ"), ("AI Studio", "エーアイ スタジオ"),
    ("GPT-6.1 Sol", "ジーピーティー 6.1 ソル"), ("GPT-6 Astra", "ジーピーティー 6 アストラ"), ("Claude Opus 5.5", "クロード オーパス 5.5"),
    ("Opus 5.5", "オーパス 5.5"), ("OpenAI", "オープンエーアイ"), ("Anthropic", "アンソロピック"), ("Claude Code", "クロード コード"),
    ("Claude", "クロード"), ("Gemini", "ジェミニ"), ("Google", "グーグル"), ("Carbon", "カーボン"), ("Argon", "アルゴン"),
    ("Ultra", "ウルトラ"), ("Max", "マックス"), ("Pro", "プロ"), ("Free", "フリー"), ("API", "エーピーアイ"), ("AI", "エーアイ"),
    ("%", "パーセント"), ("$", ""),
]  # fmt: skip


def speech_for_tts(text: str) -> str:
    """声に渡す文: 英字の製品名をカタカナに、記号を言葉に（画面・記録の文はそのまま）"""
    out = text
    for src, dst in TTS_READINGS:
        out = out.replace(src, dst)
    return out


SHORT_RANGE = (20.0, 45.0)  # SNS 用の短い版（30〜40秒が目安）


def length_targets(script: dict) -> tuple[float, float, tuple[float, float]]:
    """(読み直しの下限, 上限, ゲートの範囲)。短い版・承認済みの台本・ふだんで変える"""
    if script.get("_variant") == "short":
        return 24.0, 42.0, SHORT_RANGE
    if script.get("_approved"):
        return TARGET_MIN, 108.0, APPROVED_RANGE
    return TARGET_MIN, TARGET_MAX, LENGTH_RANGE


def short_script(script: dict) -> dict:
    """同じ台本から SNS 用の短い版を作る（LLM は呼ばない）: hook → 答え → 並べて初めて分かる違い → 「◯◯は記事で」。
    beat の番号を持つ ladder・units・flows・compare は新しい番号に付け替える"""
    beats = script.get("beats") or []
    n = len(beats)
    picks = [int(i) for i in (script.get("short_beats") or []) if isinstance(i, (int, float)) and 1 <= int(i) <= n]
    picks = list(dict.fromkeys(picks))[:2]
    if len(picks) < 2:  # 台本に無いとき: 答え（1つめ）＋ 比較のグラフの beat（無ければ2つめ）
        graph = [int((script.get(k) or {}).get("beat") or 0) for k in ("ladder", "compare")] + [int(L.get("beat") or 0) for L in script.get("ladders") or []]
        second = next((b for b in graph if b and b != 1), 2 if n >= 2 else 0)
        picks = [b for b in dict.fromkeys([1, second]) if 1 <= b <= n]
    # 30秒に届かない（読み上げが約145字未満）なら、グラフの場面 → 次の場面の順に1つ足す（短すぎて答えが薄くならないように）
    def chars(ps: list[int]) -> int:
        return len((script.get("hook") or {}).get("speech", "")) + sum(len(beats[i - 1].get("speech", "")) for i in ps) + 24

    if chars(picks) < 145 and len(picks) < n:
        graph = [int((script.get(k) or {}).get("beat") or 0) for k in ("ladder", "compare", "units")] + [int(L.get("beat") or 0) for L in script.get("ladders") or []]
        extra = [b for b in graph if b and b not in picks] + [b for b in range(1, n + 1) if b not in picks]
        if extra and chars(picks + [extra[0]]) <= 210:
            picks.append(extra[0])
    picks.sort()
    remap = {old: new for new, old in enumerate(picks, 1)}
    out = json.loads(json.dumps(script, ensure_ascii=False))
    out["beats"] = [out["beats"][i - 1] for i in picks]

    def moved(obj: dict) -> dict:
        obj = dict(obj or {})
        obj["beat"] = remap.get(int(obj.get("beat") or 0), 0)
        return obj

    for key in ("ladder", "compare", "units"):
        if key in out:
            out[key] = moved(out[key])
    for key in ("ladders", "flows"):
        if key in out:
            out[key] = [moved(x) for x in out[key] if remap.get(int(x.get("beat") or 0))]
    skipped = [(b.get("label") or "").strip() for i, b in enumerate(beats, 1) if i not in picks and (b.get("label") or "").strip()]
    skipped = [x for x in skipped if x not in ("答え",)]
    # 締めの「◯◯は記事で」: 「必要な人」系と「確かめ方」系を優先（無ければ最後の2つ）
    who = next((x for x in skipped if re.search(r"必要|向いて|使い方|活用|こんな", x)), None)
    how = next((x for x in skipped if re.search(r"確かめ|試し|始め|今できる|次の一歩", x)), None)
    tail = [x for x in (who, how) if x] or skipped[-2:]
    text = f"{'・'.join(tail)}は記事で" if tail and len("・".join(tail)) <= 12 else "詳しくは記事で"
    speech = f"{'や'.join(tail)}は、記事で詳しく解説しています。" if tail and len("・".join(tail)) <= 12 else "詳しくは、記事で解説しています。"
    out["conclusion"] = {"text": text, "speech": speech}
    out["_variant"] = "short"
    return out


def make_tts(kind: str, speed: float):
    """v3 の声: VOICEVOX は V3_VOICE（話者・スタイル・高さ・抑揚）。それ以外は v2 と同じ"""
    if kind == "voicevox" or (kind == "auto" and shorts.VoiceVox.available()):
        v = V3_VOICE
        tts = shorts.VoiceVox(v["speaker"], speed, style=v["style"], pitch=v["pitch"], intonation=v["intonation"])
        tts.engine = "voicevox"
        tts.label = f"VOICEVOX（{v['speaker']}・{v['style']}・高さ{v['pitch']:+.2f}・抑揚{v['intonation']:.2f}）"
        return tts
    return v2.make_tts(kind, speed)


def narrate(script: dict, tts_kind: str, work: Path) -> Narration:
    lines = [Line("hook", 0, script["hook"]["speech"])] + [Line("beat", i, b["speech"]) for i, b in enumerate(script["beats"], 1)] + [Line("conclusion", len(script["beats"]) + 1, script["conclusion"]["speech"])]
    if tts_kind == "voicevox" and not wait_voicevox():
        raise RuntimeError("VOICEVOX に繋がりません")
    base_speed = V3_VOICE["speed"] if tts_kind != "fake" else 1.22
    speed = base_speed
    for _ in range(5):
        tts = v2.FakeTTS(0.135 * 1.22 / speed) if tts_kind == "fake" else make_tts(tts_kind, speed)

        def synth(i: int) -> np.ndarray:
            raw, dst = work / f"l{i}.src.wav", work / f"l{i}.wav"
            tts.synth(speech_for_tts(lines[i].speech), raw)
            v2._to_48k_trimmed(raw, dst)
            return v2._read_wav(dst)

        with ThreadPoolExecutor(max_workers=4) as pool:
            clips = list(pool.map(synth, range(len(lines))))
        t = LEAD
        for line, clip in zip(lines, clips):
            line.start, line.end = t, t + len(clip) / SR
            t = line.end + GAP
        total = lines[-1].end + END_HOLD
        end_start = total  # 締めのカードは無い（最後の場面のまま終わる）
        # 長さが外れたら、声の部分の長さから必要な速さを1回で計算して読み直す（何度も合成すると遅い）
        voiced = sum(len(c) for c in clips) / SR
        fixed = total - voiced
        t_min, t_max, _ = length_targets(script)
        if total < t_min and speed > base_speed - 0.08:
            speed = max(base_speed - 0.08, speed * voiced / max(t_min + 0.6 - fixed, 1.0))
            log(f"   [v3] {total:.1f}秒で短い → 速さ {speed:.2f} で読み直し")
            continue
        if total <= t_max or speed >= base_speed + 0.1:
            break
        speed = min(base_speed + 0.1, speed * voiced / max(t_max - 0.6 - fixed, 1.0))
        log(f"   [v3] {total:.1f}秒で長い → 速さ {speed:.2f} で読み直し")
    samples = np.zeros(int(math.ceil(total * SR)) + 1, dtype=np.float32)
    for line, clip in zip(lines, clips):
        a = int(line.start * SR)
        samples[a : a + len(clip)] += clip[: len(samples) - a]
    voice = {"engine": tts.engine, "credit": tts.credit, "label": tts.label, "speed": round(speed, 2)}
    return Narration(lines, samples, end_start, total, voice)


# --------------------------------------------------------------------------- #
# 4. 音（自作の BGM・効果音・自動で下げる・-14 LUFS）
# --------------------------------------------------------------------------- #


def _midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def fft_filter(x: np.ndarray, low: float = 0.0, high: float = 0.0, dip: tuple | None = None) -> np.ndarray:
    """周波数で形を整える（low: これより上を落とす / high: これより下を落とす / dip: (中心, 幅oct, dB)）"""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    g = np.ones_like(f)
    if low:
        g /= np.sqrt(1 + (f / low) ** 4)
    if high:
        g *= 1 / np.sqrt(1 + (high / np.maximum(f, 1e-3)) ** 4)
    if dip:
        c, width, db = dip
        g *= 10 ** (db / 20 * np.exp(-((np.log2(np.maximum(f, 1) / c) / width) ** 2)))
    return np.fft.irfft(spec * g, n=len(x)).astype(np.float32)


def _add(out: np.ndarray, sig: np.ndarray, at: float) -> None:
    i = int(at * SR)
    if i >= len(out) or i + len(sig) <= 0:
        return
    a, b = max(0, i), min(len(out), i + len(sig))
    out[a:b] += sig[a - i : b - i]


def synth_bgm(total: float, seed: int = 0) -> np.ndarray:
    """自作の BGM（2026-11-02 オーナー「暗いかも」→ 明るく弾む形に）。外部音源なし＝権利の心配なし。
    122BPM・D メジャー（I–V–vi–IV: D A Bm G）・マリンバ風のプラック（裏で跳ねる）＋薄いパッド＋弾むベース＋
    軽いキック・手拍子・シェイカー。声の帯域（1〜4kHz）は空ける"""
    rng = np.random.default_rng(seed)
    bpm = 122
    beat = 60 / bpm
    bar = 4 * beat
    n = int((total + 1) * SR)
    out = np.zeros(n, dtype=np.float32)
    prog = [[62, 66, 69, 73], [57, 61, 64, 69], [59, 62, 66, 69], [55, 59, 62, 66]]  # Dmaj7 A Bm Gmaj7
    bars = int(math.ceil(total / bar)) + 1

    def tone(freq: float, dur: float, decay: float, harm=(1.0, 0.0, 0.0, 0.22), attack=0.003) -> np.ndarray:
        t = np.arange(int(dur * SR)) / SR
        env = np.minimum(1, t / attack) * np.exp(-t / decay)
        # マリンバ風: 基音＋4倍音（倍音はすぐ消える）
        sig = sum(a * np.sin(2 * np.pi * freq * (k + 1) * t) * (np.exp(-t / (decay * 0.25)) if k else 1) for k, a in enumerate(harm) if a)
        return (env * sig).astype(np.float32)

    # 8分のプラック（裏拍を強めにして跳ねる・3拍目の裏は休む）
    pattern = [(0, 0, 0.10), (0.5, 2, 0.13), (1, 1, 0.08), (1.5, 3, 0.13), (2, 2, 0.09), (3, 1, 0.08), (3.5, 3, 0.12)]
    for b in range(bars):
        chord = prog[b % 4]
        t0 = b * bar
        # パッド（薄く・明るい高めの和音）
        t = np.arange(int(bar * SR)) / SR
        env = np.minimum(1, t / 0.3) * np.minimum(1, (bar - t) / 0.3)
        pad = sum(np.sin(2 * np.pi * _midi(m + 12) * d * t) for m in chord[1:] for d in (0.997, 1.003))
        _add(out, (0.016 * env * pad).astype(np.float32), t0)
        for at, idx, vel in pattern:
            note = chord[idx] + 12
            _add(out, vel * tone(_midi(note), 0.45, 0.16), t0 + at * beat + (0.01 if at % 1 else 0))
        # 2小節目ごとに高い音の飾り（上がっていく3音）
        if b % 2 == 1:
            for k, idx in enumerate((1, 2, 3)):
                _add(out, 0.06 * tone(_midi(chord[idx] + 24), 0.3, 0.09), t0 + (3 + k / 3) * beat)
        # ベース（ルートと1オクターブ上を行き来して弾む）
        for at, oct_ in ((0, 0), (0.75, 12), (1.5, 0), (2, 0), (2.75, 12), (3.5, 0)):
            _add(out, 0.24 * tone(_midi(chord[0] - 24 + oct_), 0.32, 0.12, harm=(1.0, 0.35)), t0 + at * beat)
        # キック（1・3拍。軽め）・手拍子（2・4拍）・シェイカー（16分）
        for at in (0, 2):
            tt = np.arange(int(0.25 * SR)) / SR
            f = 55 + 80 * np.exp(-tt / 0.03)
            _add(out, (0.30 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt / 0.11)).astype(np.float32), t0 + at * beat)
        for at in (1, 3):
            tt = np.arange(int(0.18 * SR)) / SR
            clap = fft_filter(rng.standard_normal(len(tt)).astype(np.float32), low=6000, high=900)
            env = np.exp(-tt / 0.045) * (1 + 0.6 * np.exp(-((tt - 0.012) / 0.004) ** 2))
            _add(out, (0.10 * clap * env).astype(np.float32), t0 + at * beat)
        for k in range(16):
            tt = np.arange(int(0.05 * SR)) / SR
            vel = 0.035 if k % 2 else 0.018
            _add(out, (vel * rng.standard_normal(len(tt)) * np.exp(-tt / 0.012)).astype(np.float32), t0 + k * beat / 4)
    out = fft_filter(out, low=10000, high=45, dip=(2400, 1.1, -5))  # 声の帯域（1〜4kHz）を空ける
    out = out[: int(total * SR)]
    t = np.arange(len(out)) / SR
    out *= np.minimum(1, t / 0.2) * np.minimum(1, np.maximum(0, (total - t) / 0.9))
    return (out / max(np.abs(out).max(), 1e-6) * 0.5).astype(np.float32)


def sfx_whoosh(d: float = 0.34, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(int(d * SR)) / SR
    x = fft_filter(rng.standard_normal(len(t)).astype(np.float32), low=3500, high=300)
    env = np.sin(np.pi * t / d) ** 2.2 * (0.6 + 0.4 * t / d)
    return (x * env / max(np.abs(x).max(), 1e-6) * 0.5).astype(np.float32)


def sfx_pop() -> np.ndarray:
    t = np.arange(int(0.12 * SR)) / SR
    f = 1500 - 500 * t / 0.12
    return (0.5 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.03)).astype(np.float32)


def sfx_click() -> np.ndarray:
    rng = np.random.default_rng(3)
    t = np.arange(int(0.04 * SR)) / SR
    return (0.35 * np.sin(2 * np.pi * 2800 * t) * np.exp(-t / 0.006) + 0.15 * rng.standard_normal(len(t)) * np.exp(-t / 0.002)).astype(np.float32)


def _rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(max(np.sqrt(np.mean(np.square(x, dtype=np.float64))), 1e-9)))


def speech_mask(voice: np.ndarray, win: float = 0.05) -> np.ndarray:
    """声が出ている所（50ms ごとの RMS が最大から -35dB 以内）。1サンプルごとの True/False"""
    hop = int(win * SR)
    k = len(voice) // hop
    rms = np.sqrt(np.mean(np.square(voice[: k * hop].reshape(k, hop)), axis=1) + 1e-12)
    on = 20 * np.log10(rms) > 20 * np.log10(rms.max() + 1e-12) - 35
    mask = np.repeat(on, hop)
    return np.concatenate([mask, np.zeros(len(voice) - len(mask), dtype=bool)])


def duck_gain(voice: np.ndarray, bgm: np.ndarray) -> tuple[np.ndarray, dict]:
    """BGM の音量を、声が出ている間は声より SEP_TARGET_DB 下に、行の間は 6dB 上げる（なめらかに）"""
    mask = speech_mask(voice)
    if not mask.any():
        return np.full(len(bgm), 0.3, dtype=np.float32), {"separation_db": None}
    v_db = _rms_db(voice[mask])
    b_db = _rms_db(bgm[mask])
    g_speech = 10 ** ((v_db - SEP_TARGET_DB - b_db) / 20)
    g_gap = g_speech * 10 ** (6 / 20)
    # 声の始まりの 80ms 前から下げ、終わってから 250ms で戻す（50ms の箱で広げてからならす）
    hop = int(0.01 * SR)
    k = len(mask) // hop + 1
    on = np.zeros(k)
    on[: len(mask[::hop])] = mask[::hop]
    grow = np.convolve(on, np.ones(25), mode="full")[: k] > 0  # 後ろへ 250ms
    ahead = np.convolve(on[::-1], np.ones(8), mode="full")[: k][::-1] > 0  # 前へ 80ms
    m = (grow | ahead).astype(float)
    m = np.convolve(m, np.ones(9) / 9, mode="same")
    m = np.repeat(m, hop)[: len(bgm)]
    m = np.concatenate([m, np.full(len(bgm) - len(m), m[-1] if len(m) else 0)])
    gain = (g_gap + (g_speech - g_gap) * m).astype(np.float32)
    sep = v_db - _rms_db((bgm * gain)[mask])
    return gain, {"voice_db": round(v_db, 1), "bgm_db": round(_rms_db((bgm * gain)[mask]), 1), "separation_db": round(sep, 1)}


def master(mix: np.ndarray, total: float, work: Path) -> Path:
    """-14 LUFS / ピーク -1.5 dBTP（2回測って直線的に合わせる）→ 48kHz ステレオ"""
    src = work / "mix.wav"
    v2._write_wav(src, mix)
    ff = shorts.ffmpeg_bin()
    target = "I=-14:TP=-1.5:LRA=11"
    err = subprocess.run([ff, "-hide_banner", "-nostats", "-i", str(src), "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"], capture_output=True, text=True).stderr
    m = json.loads(err[err.rfind("{") : err.rfind("}") + 1])
    af = (
        f"loudnorm={target}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
        f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,alimiter=limit=0.84:level=false"
    )
    out = work / "master.wav"
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(src), "-af", af, "-ar", str(SR), "-ac", "2", "-t", f"{total:.3f}", str(out)], check=True)
    return out


def build_audio(narr: Narration, events: list[dict], work: Path, bgm_on: bool = True) -> tuple[Path, dict]:
    n = int(round(narr.total * SR))
    voice = np.zeros(n, dtype=np.float32)
    voice[: min(n, len(narr.samples))] = narr.samples[:n]
    info: dict = {"bgm": "自作（synth_bgm・外部音源なし）" if bgm_on else "なし", "sfx": "自作（sfx_whoosh / sfx_pop / sfx_click）"}
    mix = voice.copy()
    if bgm_on:
        bgm = synth_bgm(narr.total)[:n]
        bgm = np.pad(bgm, (0, n - len(bgm)))
        gain, sep = duck_gain(voice, bgm)
        mix += bgm * gain
        info.update(sep)
    sfx = np.zeros(n, dtype=np.float32)
    whoosh, pop, click = sfx_whoosh(), sfx_pop(), sfx_click()
    for e in events:
        if e["kind"] in ("move", "cut"):
            _add(sfx, whoosh * (0.7 if e["kind"] == "move" else 1.0), e["t"] - 0.06)
        elif e["kind"] in ("pop", "chart"):
            _add(sfx, pop, e["t"])
        elif e["kind"] == "click":
            _add(sfx, click, e["t"])
    v_peak = float(np.abs(voice).max() or 1.0)
    mix += sfx * (v_peak * 0.22 / max(float(np.abs(sfx).max()), 1e-6))
    return master(mix, narr.total, work), info


# --------------------------------------------------------------------------- #
# 5. 絵の計画（カメラ・注目・カーソル・文字）
# --------------------------------------------------------------------------- #


@dataclass
class Cam:
    page: int
    cx: float
    cy: float
    s: float  # 出力の画素 / ページの画素

    def map(self, x: float, y: float) -> tuple[float, float]:
        return FOCUS[0] + self.s * (x - self.cx), FOCUS[1] + self.s * (y - self.cy)


def clamp_cam(page: Page, page_no: int, cx: float, cy: float, s: float) -> Cam:
    pw, ph = page.width, page.height
    if pw * s >= PW:
        cx = min(max(cx, FOCUS[0] / s), pw - (PW - FOCUS[0]) / s)
    else:
        cx = pw / 2 + (FOCUS[0] - PW / 2) / s
    if ph * s >= PH:
        cy = min(max(cy, FOCUS[1] / s), ph - (PH - FOCUS[1]) / s)
    else:
        cy = FOCUS[1] / s
    return Cam(page_no, cx, cy, s)


def fit_s(page: Page) -> float:
    return PW / page.width


WORD_BREAK = set(" 　、。，,（(「『・/")


def frame_rect(page: Page, page_no: int, rect: tuple, frac: float, zmax: float, zmin: float = 1.0, line: tuple | None = None, chars: list | None = None) -> Cam:
    """rect の幅が画面幅の frac になる倍率（ページ幅いっぱい〜zmax 倍）で、rect を注目位置に置く。
    line（rect を含む行）があるとき:
      - 行の全体が無理なく入る倍率（ページ幅の 1.3 倍以上）なら、行の全体が入るまで引く
      - 入らないときは、注目の所が見える範囲で、なるべく行の頭から。頭を切るときは語の頭で切る（語の途中で切れた文にしない）"""
    s0 = fit_s(page)
    rw, rh = max(rect[2] - rect[0], 1), max(rect[3] - rect[1], 1)
    s = min(frac * PW / rw, 0.22 * PH / rh)
    s = min(max(s, s0 * zmin), s0 * zmax)
    cx = (rect[0] + rect[2]) / 2
    sub = line is not None
    line = line or rect
    margin = 50
    if sub:
        s_fit = (PW * 0.96 - 2 * margin) / max(line[2] - line[0], 1)
        if s_fit >= s0 * 1.3:
            s = min(s, s_fit)
    vis = PW / s
    if line[2] - line[0] > vis - 2 * margin / s:
        left = line[0] - margin / s
        if sub and rect[2] + 70 / s > left + vis:
            want = rect[2] + 70 / s - vis + margin / s  # 文字の始まりはここより右
            starts = []
            if chars:
                starts = [r[0] for i, (c, r) in enumerate(chars) if c not in WORD_BREAK and (i == 0 or chars[i - 1][0] in WORD_BREAK)]
            cand = [x for x in starts if want <= x < rect[2]]
            left = (min(cand) if cand else want) - margin / s
        cx = left + FOCUS[0] / s
    return clamp_cam(page, page_no, cx, (rect[1] + rect[3]) / 2, s)


def hero_block(page: Page) -> Block | None:
    """ページの上の方（CSS 1600px まで）でいちばん大きな文字の見出し"""
    top = [b for b in page.blocks if b.rect[1] < 1600 * CAPTURE_DPR and b.font_px >= 26 and len(b.text) >= 6]
    return max(top, key=lambda b: b.font_px) if top else None


def click_point(tg: "Target") -> tuple[float, float]:
    """カーソルの先は注目の文字の右下すぐ外（数字を隠さない）"""
    r = tg.zoom_rect
    return r[2] + (r[3] - r[1]) * 0.15, r[1] + (r[3] - r[1]) * 0.7


def wide(page: Page, page_no: int, rect: tuple) -> Cam:
    return clamp_cam(page, page_no, page.width / 2, (rect[1] + rect[3]) / 2, fit_s(page))


@dataclass
class Move:
    t: float
    dur: float
    cam: Cam
    cut: bool = False


@dataclass
class Spot:
    t0: float
    t1: float
    page: int
    rect: tuple
    sel: list | None = None  # 選択の青を付ける文字（target そのもの・行ごとの枠）
    sel_t: float = 0.0  # 選択が始まる時刻


@dataclass
class Click:
    t: float
    page: int
    x: float
    y: float


@dataclass
class Ladder:
    """料金の段階のグラフ（公式の料金表の数字だけ）: 安い順の行・主役のプラン・1行ずつ出す間隔"""
    t0: float
    t1: float
    rows: list
    subject: str
    reveal: float = 0.5
    mode: str = "price"  # price（料金の段階）/ units（たとえ: 基準の量の四角いくつ分）/ flow（手順の箱と矢印）
    unit: str = ""  # units: 基準の言い方 / flow: 最後に出す一言
    title: str = ""  # グラフの見出し（空なら既定）
    source: str = ""  # 見出しの横の出どころ（空なら既定）
    tour: float = 0.0  # >0 なら、強調の行を上から順にこの秒数ずつ移す（読み上げが長い場面）


@dataclass
class Chart:
    t0: float
    t1: float
    base: str
    subject: str
    ratio: str


DRIFT = 0.02  # 止まっている間もゆっくり寄る（1秒に2%）


class Camera:
    def __init__(self, first: Cam, moves: list[Move]):
        self.moves = sorted(moves, key=lambda m: m.t)
        self.first = first
        self.starts: list[Cam] = []
        prev_cam, prev_end = first, 0.0
        for m in self.moves:
            at = self._hold(prev_cam, prev_end, m.t)
            if m.cut:  # 切り替え: 少し寄った所から落ち着く
                at = Cam(m.cam.page, m.cam.cx, m.cam.cy, m.cam.s * 1.10)
            self.starts.append(at)
            prev_cam, prev_end = m.cam, m.t + m.dur

    @staticmethod
    def _hold(cam: Cam, since: float, t: float) -> Cam:
        return Cam(cam.page, cam.cx, cam.cy, cam.s * (1 + DRIFT * max(0.0, t - since)))

    def at(self, t: float) -> Cam:
        idx = -1
        for i, m in enumerate(self.moves):
            if m.t <= t:
                idx = i
        if idx < 0:
            return self._hold(self.first, 0.0, t)
        m, a = self.moves[idx], self.starts[idx]
        if t >= m.t + m.dur:
            return self._hold(m.cam, m.t + m.dur, t)
        k = ease((t - m.t) / m.dur)
        b = m.cam
        return Cam(b.page, a.cx + (b.cx - a.cx) * k, a.cy + (b.cy - a.cy) * k, math.exp(math.log(a.s) + (math.log(b.s) - math.log(a.s)) * k))


@dataclass
class TextItem:
    role: str
    text: str
    sprite: np.ndarray  # RGBA
    x: int
    y: int
    t0: float
    t1: float
    anim: str = "rise"  # rise（下から上がる）/ wipe（左から現れる）/ none
    delay: float = 0.0
    marker: bool = False  # キーワードの下をマーカーが走る

    def bbox(self) -> list[float]:
        a = self.sprite[..., 3]
        ys, xs = np.nonzero(a > 8)
        if not len(xs):
            return [self.x, self.y, self.x, self.y]
        return [float(self.x + xs.min()), float(self.y + ys.min()), float(self.x + xs.max() + 1), float(self.y + ys.max() + 1)]


RENDERED: list[str] = []  # 動画に描いた文字すべて（数字が記事と合っているかをゲートで見る。途中の数字は描かない）


def render_text(segments: list[tuple[str, str, tuple]], size: int, tracking: float = 0.0) -> np.ndarray:
    """1行の文字（[(文字, 書体, 色)]）を RGBA で。前後の余白は切り落とす。tracking は字間（文字の大きさに対する割合）"""
    RENDERED.append("".join(t for t, _, _ in segments))
    fonts = [shorts.font(face, size) for _, face, _ in segments]
    track = size * tracking
    width = int(sum(f.getlength(t) + track * len(t) for (t, _, _), f in zip(segments, fonts))) + size
    img = Image.new("RGBA", (width + 8, int(size * 1.6)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 4.0
    for (t, _, color), f in zip(segments, fonts):
        if track:
            for ch in t:
                d.text((x, size * 0.15), ch, font=f, fill=color + (255,))
                x += f.getlength(ch) + track
        else:
            d.text((x, size * 0.15), t, font=f, fill=color + (255,))
            x += f.getlength(t)
    arr = np.asarray(img)
    ys, xs = np.nonzero(arr[..., 3] > 0)
    if not len(xs):
        return np.zeros((1, 1, 4), dtype=np.uint8)
    return np.ascontiguousarray(arr[max(0, ys.min() - 2) : ys.max() + 3, max(0, xs.min() - 2) : xs.max() + 3])


NUM_SPLIT = re.compile(r"([$＄]?\d[\d,，.．]*(?:万|億|兆)?\d*(?:円|ドル|%|％|倍|人|件|社|年|月|日|時間|分|秒|GB|TB|万人|トークン)?)")


def accent_segments(text: str, face: str = "black", base=INK) -> list[tuple[str, str, tuple]]:
    out = []
    for i, part in enumerate(NUM_SPLIT.split(text)):
        if part:
            out.append((part, face, ACCENT if i % 2 else base))
    return out


def fit_line(text: str, face: str, sizes: range, width: int, color=INK, accent: bool = True, tracking: float = 0.0) -> np.ndarray:
    for size in sizes:
        spr = render_text(accent_segments(text, face, color) if accent else [(text, face, color)], size, tracking)
        if spr.shape[1] <= width:
            return spr
    raise RuntimeError(f"文字が入りません: {text}")


def stack(sprites: list[np.ndarray], gap: int, center: bool = False) -> np.ndarray:
    w = max(s.shape[1] for s in sprites)
    h = sum(s.shape[0] for s in sprites) + gap * (len(sprites) - 1)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    y = 0
    for s in sprites:
        x = (w - s.shape[1]) // 2 if center else 0
        out[y : y + s.shape[0], x : x + s.shape[1]] = s
        y += s.shape[0] + gap
    return out


def wrapped(text: str, face: str, sizes: range, width: int, rows_max: int, color=INK, accent: bool = True, center: bool = False) -> np.ndarray:
    for size in sizes:
        rows = v2.wrap_jp(text, shorts.font(face, size), width - 8)
        if len(rows) <= rows_max:
            sprites = [render_text(accent_segments(r, face, color) if accent else [(r, face, color)], size) for r in rows]
            if all(s.shape[1] <= width for s in sprites):
                return stack(sprites, int(size * 0.22), center)
    raise RuntimeError(f"文字が入りません: {text}")


def big_lines(text: str) -> np.ndarray:
    """つかみ・締めの大きな文字: 文節の途中で折り返さない、いちばん大きな文字（2行 → 3行の順に探す）"""
    bounds, at = {0, len(text)}, 0
    for ph in v2._phrases(text):
        at += len(ph)
        bounds.add(at)
    for rows_max, sizes in ((2, range(86, 62, -2)), (3, range(80, 60, -2)), (2, range(60, 48, -2))):
        for size in sizes:
            rows = v2.wrap_jp(text, shorts.font("black", size), COL_W - 8)
            cuts, at = set(), 0
            for r in rows:
                at += len(r)
                cuts.add(at)
            if len(rows) <= rows_max and "".join(rows) == text and cuts <= bounds:
                sprites = [render_text(accent_segments(r, "black"), size) for r in rows]
                if all(sp.shape[1] <= COL_W for sp in sprites):
                    return stack(sprites, int(size * 0.22))
    return wrapped(text, "black", range(80, 44, -2), COL_W, 3)


CALLOUT_MAX_W = 860
CALLOUT_SH = 16  # 吹き出しの影の余白
CALLOUT_GAP = 16  # 選択した英文と吹き出しのすき間（とがりを含まない）
POINTER = (30, 14)  # 吹き出しのとがり（幅・高さ）


def callout_sprite(text: str) -> np.ndarray:
    """訳の吹き出しの本体（墨の角丸・小さな見出し「日本語訳」・白い太字の訳・やわらかい影）。とがりは描画のときに足す"""
    pad_x, pad_y = 30, 20
    label = fit_line("日本語訳", "bold", range(22, 16, -2), CALLOUT_MAX_W, color=(150, 182, 232), accent=False, tracking=0.1)
    body = None
    for size, rows_max in ((42, 2), (38, 2), (36, 3), (32, 3)):
        try:
            body = wrapped(text, "bold", range(size, size - 1, -1), CALLOUT_MAX_W - pad_x * 2, rows_max, color=WHITE, accent=False)
            break
        except RuntimeError:
            continue
    if body is None:
        body = wrapped(text, "bold", range(32, 22, -1), CALLOUT_MAX_W - pad_x * 2, 4, color=WHITE, accent=False)
    w = max(label.shape[1], body.shape[1]) + pad_x * 2
    h = pad_y * 2 + label.shape[0] + 10 + body.shape[0]
    sh = CALLOUT_SH
    out = np.zeros((h + sh * 2, w + sh * 2, 4), dtype=np.uint8)
    card, shadow = _mask_callout(w, h, sh)
    out[..., :3] = INK
    out[..., 3] = shadow
    box = np.zeros_like(out)
    box[..., :3] = INK
    box[..., 3] = card
    blend_rgba(out, box, 0, 0)
    blend_rgba(out, label, sh + pad_x, sh + pad_y)
    blend_rgba(out, body, sh + pad_x, sh + pad_y + label.shape[0] + 10)
    return out


def _mask_callout(w: int, h: int, sh: int) -> tuple[np.ndarray, np.ndarray]:
    """吹き出しの形（角丸）と、そのやわらかい影の濃さ（どちらもマスク）"""
    q = 4
    big = Image.new("L", ((w + sh * 2) * q, (h + sh * 2) * q), 0)
    ImageDraw.Draw(big).rounded_rectangle((sh * q, sh * q, (sh + w) * q, (sh + h) * q), radius=16 * q, fill=255)
    card = big.resize((w + sh * 2, h + sh * 2), Image.LANCZOS)
    shadow = Image.new("L", card.size, 0)
    shadow.paste(card, (0, 6))
    shadow = shadow.filter(ImageFilter.GaussianBlur(9)).point(lambda v: int(v * 0.30))
    return np.asarray(card), np.asarray(shadow)


def _mask_pointer(up: bool) -> np.ndarray:
    """吹き出しのとがり（墨の三角）。up=True なら上の英文を指す"""
    q = 4
    pw, ph = POINTER
    big = Image.new("L", (pw * q, (ph + 2) * q), 0)
    pts = [(0, (ph + 2) * q), (pw * q, (ph + 2) * q), (pw * q / 2, 0)] if up else [(0, 0), (pw * q, 0), (pw * q / 2, (ph + 2) * q)]
    ImageDraw.Draw(big).polygon(pts, fill=255)
    a = np.asarray(big.resize((pw, ph + 2), Image.LANCZOS))
    out = np.zeros((ph + 2, pw, 4), dtype=np.uint8)
    out[..., :3] = INK
    out[..., 3] = a
    return out


def callout_place(sub: "Subtitle", cam: "Cam") -> tuple[int, int, int, int, bool] | None:
    """吹き出しの位置（パネル座標）: (x, y, とがりの x, とがりの y, 上向きか)。選択した英文にかぶらない所。置けなければ None"""
    if cam.page != sub.page:
        return None
    rows = [(*cam.map(r[0], r[1]), *cam.map(r[2], r[3])) for r in sub.rows]
    vis = [r for r in rows if r[3] > 0 and r[1] < PH]  # 横にはみ出した行も避ける（行の一部が見えていれば隠さない）
    if not vis:  # 英文が画面に無い（カメラが別の所）なら出さない
        return None
    top, bottom = min(r[1] for r in vis), max(r[3] for r in vis)
    sh = CALLOUT_SH
    h, w = sub.sprite.shape[0] - sh * 2, sub.sprite.shape[1] - sh * 2
    margin = 36
    anchor = max(0, min(r[0] for r in vis))
    x = int(min(max(anchor - 6, margin), PW - margin - w))
    py_gap = CALLOUT_GAP + POINTER[1]
    if bottom + py_gap + h <= PH - margin:
        y, up = int(bottom + py_gap), True
    elif top - py_gap - h >= margin:
        y, up = int(top - py_gap - h), False
    else:
        return None
    last = [r for r in vis if (r[3] >= bottom - 2 if up else r[1] <= top + 2)][0]
    tip_x = int(min(max(max(last[0], 0) + 44, x + 24), x + w - 24 - POINTER[0]))
    tip_y = y - POINTER[1] if up else y + h
    return x, y, tip_x, tip_y, up


def blend_rgba(dst: np.ndarray, src: np.ndarray, x: int, y: int) -> None:
    """RGBA の上に RGBA を重ねる（字幕の影の上に文字）"""
    h, w = src.shape[:2]
    roi = dst[y : y + h, x : x + w].astype(np.float32)
    sa = src[..., 3:4].astype(np.float32) / 255
    da = roi[..., 3:4] / 255
    oa = sa + da * (1 - sa)
    rgb = (src[..., :3] * sa + roi[..., :3] * da * (1 - sa)) / np.maximum(oa, 1e-6)
    dst[y : y + h, x : x + w, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    dst[y : y + h, x : x + w, 3:4] = np.clip(oa * 255, 0, 255).astype(np.uint8)


@dataclass
class Subtitle:
    """訳の吹き出し: 選択した英文（rows・ページの座標）のすぐ下（入らなければ上）に付いて、カメラと一緒に動く"""
    t0: float
    t1: float
    text: str
    sprite: np.ndarray
    page: int
    rows: list


@dataclass
class Plan:
    article: dict
    script: dict
    narr: Narration
    pages: list[Page]
    targets: list[Target | None]
    camera: Camera
    spots: list[Spot]
    clicks: list[Click]
    charts: list[Chart]
    texts: list[TextItem]
    subtitles: list[Subtitle]
    events: list[dict]
    focus: list[dict]
    credits: list[str]
    ladders: list = field(default_factory=list)


def _source_label(page: Page) -> str:
    return f"公式ページ　{page.host}"


EYEBROWS = ["何が変わった", "使いどころ", "知っておきたい", "次の一歩"]


def column_group(parts: list[tuple[str, np.ndarray, int]], center_y: int) -> list[tuple[str, np.ndarray, int, int]]:
    """文字の列に縦に並べる: [(役目, 絵, 上の間隔)] → [(役目, 絵, x, y)]。キーワードの中心が center_y に来る"""
    ys, y = [], 0
    key_mid = None
    for role, spr, gap in parts:
        y += gap if ys else 0
        ys.append(y)
        if role in ("keyword", "hook", "conclusion") and key_mid is None:
            key_mid = y + spr.shape[0] / 2
        y += spr.shape[0]
    top = int(center_y - (key_mid if key_mid is not None else y / 2))
    top = max(COL[1], min(top, COL[3] - y))
    return [(role, spr, COL[0], top + yy) for (role, spr, _), yy in zip(parts, ys)]


def time_windows(t0: float, t1: float, step: float = 0.5) -> list[tuple[float, float, float]]:
    """0.5秒の決まった刻み（全体で共通）で区切る: (始まり, 終わり, その刻みの真ん中の時刻)。同じ刻みは同じ時刻のカメラで測る"""
    out = []
    k = math.floor(t0 / step)
    while k * step < t1 - 1e-6:
        a, b = max(t0, k * step), min(t1, (k + 1) * step)
        if b - a > 1e-3:
            out.append((a, b, (k + 0.5) * step))
        k += 1
    return out


def make_plan(article: dict, script: dict, narr: Narration, pages: list[Page]) -> Plan:
    lines = narr.lines
    hook = script["hook"]
    beats = script["beats"]
    items = [hook] + beats
    kws = [hook.get("payoff") or ""] + [b.get("keyword", "") for b in beats]
    targets = [find_target(pages, it.get("target", ""), it.get("block", ""), keyword=k) for it, k in zip(items, kws)]
    if not targets[0]:
        raise RuntimeError("つかみの証拠の場所がページに見つかりません")
    found = [t is not None for t in targets]
    for i in range(1, len(targets)):  # 見つからない beat は1つ前の場所を使う（ゲートで事実は見ている）
        targets[i] = targets[i] or targets[i - 1]
    comp = script.get("compare") or {}
    comp_beat = int(comp.get("beat") or 0) if comp.get("ratio") else 0
    # 料金の段階・数字の比較のグラフ（ladder 1つ、または ladders に複数）。beat ごとに1つ
    lad_map: dict[int, dict] = {}
    for L_ in [script.get("ladder") or {}] + list(script.get("ladders") or []):
        rows_ = [r for r in (L_.get("rows") or []) if r.get("name") and r.get("price")]
        if int(L_.get("beat") or 0) and len(rows_) >= 2:
            lad_map[int(L_["beat"])] = {**L_, "rows": rows_}
    if comp_beat in lad_map:
        comp_beat = 0  # 同じ場面に2つのグラフは出さない
    # 手順の絵（flows）: 作業の流れ・たとえの手順を箱と矢印で
    flow_map = {int(f.get("beat") or 0): f for f in (script.get("flows") or []) if int(f.get("beat") or 0) and len(f.get("steps") or []) >= 2}
    n_beats = len(beats)
    ladders: list[Ladder] = []
    uni = script.get("units") or {}
    uni_rows = [{"name": r.get("name", ""), "count": int(r.get("count") or 0)} for r in (uni.get("rows") or []) if 1 <= int(r.get("count") or 0) <= 12]
    uni_beat = int(uni.get("beat") or 0) if len(uni_rows) >= 2 else 0

    moves: list[Move] = []
    spots: list[Spot] = []
    clicks: list[Click] = []
    charts: list[Chart] = []
    events: list[dict] = []
    sub_spans: list[tuple[float, float, int]] = []  # (t0, t1, 台本の番号 0=hook)

    def ev(t: float, kind: str, note: str = "") -> None:
        events.append({"t": round(max(0.0, t), 3), "kind": kind, "note": note})

    # つかみ: ページ幅いっぱい → 0.18秒から payoff の数字へ寄る（1秒で数字が大きく見える）
    t0 = targets[0]
    p0 = pages[t0.page]
    wide_rect = t0.block.rect if t0.block.rect[2] - t0.block.rect[0] > 0.5 * p0.width else t0.rect
    first = frame_rect(p0, t0.page, (wide_rect[0], t0.rect[1], wide_rect[2], t0.rect[3]), 0.96, 1.5, zmin=1.0 if wide_rect is t0.block.rect else 1.2)
    hero = hero_block(p0)
    if t0.block.font_px < 22 and hero is not None and hero is not t0.block:
        first = frame_rect(p0, t0.page, hero.rect, 0.96, 1.4)
    # 数字に寄るときは大きく、見出し（数字なし）は全体が入る程度に寄る（見出しの途中で切らない）
    hook_close = frame_rect(p0, t0.page, t0.zoom_rect, 0.34, ZOOM_MAX, zmin=1.6, line=t0.rect, chars=t0.line_chars) if t0.focus else frame_rect(p0, t0.page, t0.rect, 0.9, 1.6, zmin=1.15)
    moves.append(Move(0.18, 0.8, hook_close))
    ev(0.18, "move", "つかみ: 数字へ寄る")
    # つかみの訳は、同じ英文を beat でも訳すなら出さない（同じ訳が2回続かないように）
    hook_xl = is_english(hook.get("target", "")) and bool(hook.get("ja")) and all(_norm(b.get("target", "")) != _norm(hook.get("target", "")) for b in beats)
    spots.append(Spot(0.7, lines[1].start - 0.08, t0.page, t0.span if hook_xl else t0.rect, t0.rows if hook_xl else [t0.match], 0.92))
    clicks.append(Click(0.92, t0.page, *click_point(t0)))
    ev(0.92, "click")
    if hook_xl:
        sub_spans.append((1.25, lines[1].start - 0.08, 0))

    for i, (beat, line) in enumerate(zip(beats, lines[1 : n_beats + 1]), 1):
        tg = targets[i]
        page = pages[tg.page]
        nxt = lines[i + 1].start
        dur = nxt - line.start
        prev_page = moves[-1].cam.page
        a_t = line.start - 0.16
        cut = tg.page != prev_page
        wide_block = tg.block.rect[2] - tg.block.rect[0] > 0.5 * page.width
        # 英文に訳を付ける beat は、文全体（複数行）を選択して、その下に訳の吹き出しを出す
        xl = found[i] and is_english(beat.get("target", "")) and len((beat.get("ja") or "").strip()) >= 10  # 短すぎる訳（「今週からです。」など）は出さない
        span = tg.span if xl else tg.rect
        sel = tg.rows if xl else [tg.match]
        line_rect = (tg.block.rect[0], span[1], tg.block.rect[2], span[3]) if wide_block else span
        block_cam = frame_rect(page, tg.page, line_rect, 0.96, 1.6, zmin=1.25 if wide_block else ZOOM_BLOCK)
        moves.append(Move(a_t, 0.45 if cut else 0.55, block_cam, cut=cut))
        ev(a_t, "cut" if cut else "move", f"beat{i}: 行へ")
        b_t = line.start + max(0.85, 0.42 * dur)
        sub_t0, sub_end = a_t + 0.95, nxt - 0.08
        if i in lad_map:
            # 料金の段階: 本物の料金表の価格を安い順に1つずつ見せて（証拠）、そのあと比較のグラフでまとめる
            lad = lad_map[i]
            lad_rows = lad["rows"]
            moves.pop()
            events.pop()
            row_tgs = [find_target(pages, r.get("target", ""), r.get("block", ""), keyword=r.get("price", "")) for r in lad_rows]
            shown = [(r, t) for r, t in zip(lad_rows, row_tgs) if t is not None]
            step = min(1.6, max(1.0, 0.5 * dur / max(len(shown), 1)))
            t_cur = a_t
            for r, rt in shown:
                rp = pages[rt.page]
                cam_r = frame_rect(rp, rt.page, rt.zoom_rect, 0.30, 1.9, zmin=1.35, line=rt.rect, chars=rt.line_chars)
                is_cut = rt.page != moves[-1].cam.page
                moves.append(Move(t_cur, 0.45 if is_cut else 0.55, cam_r, cut=is_cut))
                ev(t_cur, "cut" if is_cut else "move", f"料金表: {r.get('name')}")
                spots.append(Spot(t_cur + 0.4, t_cur + step - 0.05, rt.page, rt.rect, [rt.zoom_rect], t_cur + 0.5))
                t_cur += step
            t_c = t_cur if shown else line.start + 0.3
            reveal = 0.5
            t_hi = t_c + 0.3 + reveal * len(lad_rows) + 0.3
            # 読み上げが長い場面は、グラフを出したまま1行ずつ順に強調していく（読み上げで各行を話す間ずっと見せる）
            tour = nxt - t_hi > 4.0
            t_end = min(nxt - 0.06, t_hi + (1.8 * len(lad_rows) + 0.2 if tour else 1.5))
            L_new = Ladder(t_c, t_end, lad_rows, lad.get("subject", ""), reveal, "price", "", lad.get("title", ""), lad.get("source", ""))
            L_new.tour = 1.8 if tour else 0.0
            ladders.append(L_new)
            ev(t_c, "chart", "料金の段階のグラフ")
            if tour:
                for k in range(len(lad_rows)):
                    ev(t_hi + 1.8 * k, "pop", f"グラフ: {lad_rows[k].get('name')}")
            else:
                ev(t_hi, "pop", "グラフ: 主役を強調")
            if nxt - t_end > 0.8 and shown:  # 残りの時間は料金表の主役のプランへ戻る
                sub_r, sub_t = next(((r, t) for r, t in shown if r.get("name", "").lower() == lad.get("subject", "").lower()), shown[-1])
                cam_s = frame_rect(pages[sub_t.page], sub_t.page, sub_t.rect, 0.8, 1.5, zmin=1.2)
                moves.append(Move(t_end - 0.1, 0.6, cam_s, cut=sub_t.page != moves[-1].cam.page))
                ev(t_end - 0.1, "move", f"料金表: {sub_r.get('name')} へ戻る")
                spots.append(Spot(t_end + 0.3, nxt - 0.08, sub_t.page, sub_t.rect, [sub_t.zoom_rect], t_end + 0.4))
            continue
        if i in flow_map and i not in lad_map:
            # 手順の絵: 行の証拠を見せたあと、作業の流れを箱と矢印で1つずつ出す
            fl = flow_map[i]
            steps = [str(x) for x in fl.get("steps")][:4]
            t_u = line.start + max(0.9, 0.22 * dur)
            t_hi = t_u + 0.3 + 0.7 * len(steps) + 0.2
            t_end = min(nxt - 0.06, t_hi + 2.0)
            ladders.append(Ladder(t_u, t_end, [{"name": x} for x in steps], "", 0.7, "flow", fl.get("note", ""), fl.get("title", ""), ""))
            ev(t_u, "chart", "手順の絵")
            ev(t_hi, "pop", "手順: まとめ")
            spots.append(Spot(a_t + 0.35, t_u, tg.page, span, sel, a_t + 0.6))
            if xl and t_u - (a_t + 0.95) > 0.8:
                sub_spans.append((a_t + 0.95, t_u - 0.05, i))
            if nxt - t_end > 0.8:
                moves.append(Move(t_end - 0.1, 0.6, block_cam))
                ev(t_end - 0.1, "move", f"beat{i}: 画面へ戻る")
            continue
        if i == uni_beat and i not in lad_map:
            # たとえ: 行の証拠を見せたあと、基準の量を四角1つとして並べる絵
            t_u = line.start + max(0.9, 0.3 * dur)
            t_hi = t_u + 0.3 + 0.5 * len(uni_rows) + 0.3
            t_end = min(nxt - 0.06, t_hi + 1.8)
            ladders.append(Ladder(t_u, t_end, uni_rows, uni.get("subject", ""), 0.5, "units", uni.get("unit", "")))
            ev(t_u, "chart", "たとえの絵")
            ev(t_hi, "pop", "たとえ: 主役を強調")
            spots.append(Spot(a_t + 0.35, t_u, tg.page, span, sel, a_t + 0.6))
            if xl:
                sub_spans.append((a_t + 0.95, t_u - 0.05, i))
            if nxt - t_end > 0.8:
                moves.append(Move(t_end - 0.1, 0.6, block_cam))
                ev(t_end - 0.1, "move", f"beat{i}: 画面へ戻る")
            continue
        if i == comp_beat:
            c_end = min(nxt - 0.06, b_t + 2.8)  # グラフは2.8秒まで。残りは画面へ戻る（止まった絵を長く出さない）
            charts.append(Chart(b_t, c_end, comp["base"], comp["subject"], comp["ratio"]))
            ev(b_t, "chart", "比較のグラフ")
            spots.append(Spot(a_t + 0.35, b_t, tg.page, span, sel, a_t + 0.6))
            sub_end = b_t - 0.05
            if nxt - c_end > 0.8:
                moves.append(Move(c_end - 0.1, 0.6, block_cam))
                ev(c_end - 0.1, "move", f"beat{i}: 画面へ戻る")
                spots.append(Spot(c_end + 0.3, nxt - 0.08, tg.page, span, sel, c_end + 0.4))
                if xl and nxt - c_end > 1.6:
                    sub_spans.append((c_end + 0.6, nxt - 0.08, i))
        elif nxt - b_t > 0.9:
            zr = tg.zoom_rect
            moves.append(Move(b_t, 0.6, frame_rect(page, tg.page, zr, 0.36 if tg.focus else 0.7, ZOOM_MAX, zmin=1.7, line=tg.rect, chars=tg.line_chars)))
            ev(b_t, "move", f"beat{i}: 数字へ寄る")
            if xl:  # 行に着いたらすぐ文を選択して訳を出し、寄っている間も出したまま
                spots.append(Spot(a_t + 0.35, nxt - 0.08, tg.page, span, sel, a_t + 0.6))
            else:
                spots.append(Spot(b_t + 0.3, nxt - 0.08, tg.page, span, sel, b_t + 0.55))
            clicks.append(Click(b_t + 0.55, tg.page, *click_point(tg)))
            ev(b_t + 0.55, "click")
            if nxt - b_t > 3.3:  # 長い beat: 途中で少し引いて行の前後も見せる（3.6秒より長く止まらない）
                m_t = b_t + (nxt - b_t) * 0.55
                moves.append(Move(m_t, 0.6, frame_rect(page, tg.page, line_rect, 0.96, 1.4, zmin=1.15)))
                ev(m_t, "move", f"beat{i}: 少し引く")
        else:
            spots.append(Spot(a_t + 0.3, nxt - 0.08, tg.page, span, sel, a_t + 0.55))
            sub_t0 = a_t + 0.9
        if xl and sub_end - sub_t0 > 0.6:
            sub_spans.append((sub_t0, sub_end, i))

    # 締め: 最初の画面（ページ幅いっぱい）へ引いて戻る
    concl = lines[-1]
    moves.append(Move(concl.start - 0.16, 0.7, Camera._hold(first, 0, 0), cut=first.page != moves[-1].cam.page))
    ev(concl.start - 0.16, "move", "締め: 最初の画面へ戻る")
    c_t = concl.start + max(0.9, 0.45 * (concl.end - concl.start))
    if narr.end_start - c_t > 0.9:  # 最初の数字へもう一度寄る（最初と最後がつながる）
        moves.append(Move(c_t, 0.7, hook_close))
        ev(c_t, "move", "締め: 最初の数字へ")
        spots.append(Spot(c_t + 0.35, narr.end_start, t0.page, t0.rect, [t0.match], c_t + 0.5))
    # 2.6秒より長く何も変わらない所には、ゆっくり寄る・引くを足す（長い動画でも 1.5〜3秒ごとに何かが変わる）
    cover = [(c.t0, c.t1) for c in charts] + [(L.t0, L.t1) for L in ladders]
    for _ in range(3):
        camera = Camera(first, moves)
        changes = sorted({e["t"] for e in events if e["kind"] in ("move", "cut", "chart", "pop")})
        bounds = [0.0] + changes + [narr.end_start]
        added = False
        for a, b in zip(bounds, bounds[1:]):
            if b - a <= 2.9 or any(c0 - 0.2 <= (a + b) / 2 <= c1 + 0.2 for c0, c1 in cover):
                continue
            n_add = math.ceil((b - a) / 2.4) - 1
            for k in range(1, n_add + 1):
                t = a + (b - a) * k / (n_add + 1)
                c = camera.at(t)
                pg = pages[c.page]
                zin = c.s * 1.22 <= fit_s(pg) * ZOOM_MAX * 1.1 and k % 2 == 1
                moves.append(Move(t, 0.7, clamp_cam(pg, c.page, c.cx, c.cy, c.s * (1.22 if zin else 1 / 1.22))))
                ev(t, "move", "ゆっくり寄る" if zin else "少し引く")
                added = True
        if not added:
            break
    camera = Camera(first, moves)

    # 文字（左の列）: 小さな見出し → キーワード（マーカー）→ 補足。キーワードの中心は画面の注目の行と同じ高さ
    texts: list[TextItem] = []
    logo = render_text([(BRAND, "logo", INK)], 32)
    texts.append(TextItem("logo", BRAND, logo, LOGO_XY[0], LOGO_XY[1], 0.0, narr.total + 1, anim="none"))
    hook_spr = big_lines(hook["text"])
    hook_end = lines[0].end if lines[0].end <= 2.0 else min(HOOK_TEXT_MAX, lines[0].end - 0.5)
    (_, _, hx, hy), = column_group([("hook", hook_spr, 0)], ANCHOR_Y)
    texts.append(TextItem("hook", hook["text"], hook_spr, hx, hy, 0.0, hook_end, anim="none"))
    if hook.get("payoff") and lines[1].start - 0.06 - hook_end >= 0.5:
        pay = fit_line(hook["payoff"], "black", range(150, 84, -4), COL_W)
        (_, _, px, py), = column_group([("keyword", pay, 0)], ANCHOR_Y)
        texts.append(TextItem("keyword", hook["payoff"], pay, px, py, hook_end, lines[1].start - 0.06, anim="wipe", marker=True))
        ev(hook_end, "pop", "つかみの数字")
    for i, (beat, line) in enumerate(zip(beats, lines[1 : n_beats + 1]), 1):
        t_in, t_out = line.start - 0.1, lines[i + 1].start - 0.06
        label = (beat.get("label") or EYEBROWS[min(i, len(EYEBROWS)) - 1]).strip()
        brow = fit_line(label, "bold", range(28, 20, -2), COL_W, color=BLUE, accent=False, tracking=0.08)
        try:
            kw = fit_line(beat["keyword"], "black", range(132, 56, -4), COL_W)
        except RuntimeError:  # 長い場面のキーワードは2行に（文節で折り返す）
            kw = wrapped(beat["keyword"], "black", range(76, 46, -2), COL_W, 2)
        parts = [("eyebrow", brow, 0), ("keyword", kw, 18)]
        if beat.get("sub"):
            parts.append(("sub", fit_line(beat["sub"], "bold", range(42, 30, -2), COL_W, color=MUTED, accent=False), 30))
        for role, spr, x, y in column_group(parts, ANCHOR_Y):
            text = {"eyebrow": label, "keyword": beat["keyword"], "sub": beat.get("sub", "")}[role]
            texts.append(TextItem(role, text, spr, x, y, t_in, t_out, anim="wipe" if role == "keyword" else "rise", delay={"eyebrow": 0.0, "keyword": 0.06, "sub": 0.22}[role], marker=role == "keyword"))
    concl_brow = fit_line("要約すると", "bold", range(28, 20, -2), COL_W, color=BLUE, accent=False, tracking=0.08)
    concl_spr = big_lines(script["conclusion"]["text"])
    for role, spr, x, y in column_group([("eyebrow", concl_brow, 0), ("conclusion", concl_spr, 18)], ANCHOR_Y):
        text = "要約すると" if role == "eyebrow" else script["conclusion"]["text"]
        texts.append(TextItem(role, text, spr, x, y, concl.start - 0.1, narr.total + 1, anim="rise", delay=0.0 if role == "eyebrow" else 0.08))
    # 出典（どのページか）: 小さく1行・ページが変わったら切り替える
    spans: list[list] = []
    for m in [Move(0.0, 0, first)] + camera.moves:
        if not spans or spans[-1][0] != m.cam.page:
            if spans:
                spans[-1][2] = m.t
            spans.append([m.cam.page, m.t, narr.end_start])
    credit_t = narr.total - CREDIT_SEC
    for page_no, a, b in spans:
        b = min(b, credit_t)
        if b - a < 0.3:
            continue
        label = _source_label(pages[page_no])
        spr = fit_line(label, "bold", range(24, 18, -2), COL_W, color=SOFT, accent=False)
        texts.append(TextItem("source", label, spr, COL[0], SOURCE_Y, a, b, anim="rise" if a > 0 else "none"))
    voice = narr.voice
    voice_text = f"音声 {voice['credit']}" if voice.get("credit") else "音声 試作の声（本番は VOICEVOX）"
    hosts = "・".join(dict.fromkeys(p.host for p in pages))
    credit = f"{voice_text}　画面 {hosts}（公式）"
    try:
        host_row = fit_line(f"画面 {hosts}（公式）", "bold", range(22, 15, -1), COL_W, color=SOFT, accent=False)
    except RuntimeError:  # ページが多いときは2行に
        host_row = wrapped(f"画面 {hosts}（公式）", "bold", range(20, 14, -1), COL_W, 2, color=SOFT, accent=False)
    rows = [fit_line(voice_text, "bold", range(22, 16, -1), COL_W, color=SOFT, accent=False), host_row]
    spr = stack(rows, 8)
    texts.append(TextItem("credit", credit, spr, COL[0], SOURCE_Y - spr.shape[0] + rows[0].shape[0], credit_t, narr.total + 1, anim="rise"))
    credits = [credit]

    # 日本語字幕（英語の文に寄っている間）: 画面の下に中央そろえ
    subtitles: list[Subtitle] = []
    for a, b, idx in sub_spans:
        text = (items[idx].get("ja") or "").strip()
        tg = targets[idx]
        subtitles.append(Subtitle(a, b, text, callout_sprite(text), tg.page, list(tg.rows or [tg.match or tg.rect])))

    # 注目の場所（画面上の枠）: 字幕がかぶらないかをゲートで見る
    focus = []
    for sp in spots:  # カメラが動くので 0.5秒ごとの位置で記録する（字幕の吹き出しも同じ刻みで記録して比べる）
        for w0, w1, wm in time_windows(sp.t0, sp.t1):
            cam = camera.at(wm)
            if cam.page != sp.page:
                continue
            x0, y0 = cam.map(sp.rect[0], sp.rect[1])
            x1, y1 = cam.map(sp.rect[2], sp.rect[3])
            if x1 <= 0 or y1 <= 0 or x0 >= PW or y0 >= PH:
                continue
            focus.append({"t0": round(w0, 3), "t1": round(w1, 3), "bbox": [round(PANEL[0] + max(0, x0), 1), round(PANEL[1] + max(0, y0), 1), round(PANEL[0] + min(PW, x1), 1), round(PANEL[1] + min(PH, y1), 1)]})

    events.sort(key=lambda e: e["t"])
    return Plan(article, script, narr, pages, targets, camera, spots, clicks, charts, texts, subtitles, events, focus, credits, ladders)


def stage_bg() -> np.ndarray:
    """背景: 明るいほぼ白の単色（上ほどわずかに明るい）と、画面のカードのやわらかい影（線や帯は描かない）"""
    yy = np.mgrid[0:H, 0:W][0].astype(np.float32)
    base = np.array(STAGE, dtype=np.float32)[None, None, :] + (4 * (1 - yy / H))[..., None]  # 上ほどわずかに明るい
    shadow = _mask_card_shadow()[..., None] * 0.16
    base = base * (1 - shadow) + np.array(INK, dtype=np.float32) * shadow
    return np.clip(base, 0, 255).astype(np.uint8)


def _mask_card_shadow() -> np.ndarray:
    """画面のカードの下に落ちるやわらかい影の濃さ（0〜1）。ぼかしたマスクだけ"""
    q = 4
    m = Image.new("L", (W // q, H // q), 0)
    ImageDraw.Draw(m).rounded_rectangle(((PANEL[0] + 6) // q, (PANEL[1] + 22) // q, (PANEL[2] - 6) // q, (PANEL[3] + 26) // q), radius=RADIUS // q, fill=255)
    m = m.filter(ImageFilter.GaussianBlur(9)).resize((W, H), Image.BILINEAR)
    return np.asarray(m).astype(np.float32) / 255


# --------------------------------------------------------------------------- #
# 6. 描画（OpenCV でページを動かす・1フレーム数ミリ秒）
# --------------------------------------------------------------------------- #


def blend(dst: np.ndarray, rgba: np.ndarray, x: int, y: int, alpha: float = 1.0, col_alpha: np.ndarray | None = None) -> None:
    """dst（RGB）に rgba を (x, y) へ重ねる。はみ出す所は切る。col_alpha は横の位置ごとの不透明度（左から現れる動き）"""
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(dst.shape[1], x + w), min(dst.shape[0], y + h)
    if x0 >= x1 or y0 >= y1 or alpha <= 0:
        return
    src = rgba[y0 - y : y1 - y, x0 - x : x1 - x]
    a = src[..., 3:4].astype(np.uint16)
    if alpha < 1:
        a = (a * int(alpha * 256)) >> 8
    if col_alpha is not None:
        a = (a * (col_alpha[x0 - x : x1 - x] * 256).astype(np.uint16)[None, :, None]) >> 8
    roi = dst[y0:y1, x0:x1]
    roi[:] = ((src[..., :3].astype(np.uint16) * a + roi.astype(np.uint16) * (255 - a)) // 255).astype(np.uint8)


def _mask_panel_corners() -> np.ndarray:
    """パネルの角を丸くするための不透明度（角の1つ分。描くのはマスクだけ）"""
    s = 4
    big = Image.new("L", (RADIUS * s * 2, RADIUS * s * 2), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, RADIUS * s * 4, RADIUS * s * 4), radius=RADIUS * s, fill=255)
    return np.asarray(big.resize((RADIUS * 2, RADIUS * 2), Image.LANCZOS))[:RADIUS, :RADIUS].astype(np.float32) / 255


def _mask_spot(rect: tuple, pad: float = 18) -> np.ndarray:
    """注目の場所の周りだけ明るく残すマスク（1/4 の大きさでぼかしてから拡大。枠線は描かない）"""
    q = 4
    m = np.zeros((PH // q, PW // q), dtype=np.uint8)
    x0, y0, x1, y1 = [int(round(v / q)) for v in (rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad)]
    if cv2 is not None:
        cv2.rectangle(m, (x0, y0), (x1, y1), 255, thickness=-1)
        m = cv2.GaussianBlur(m, (0, 0), 5)
        return cv2.resize(m, (PW, PH), interpolation=cv2.INTER_LINEAR)
    img = Image.fromarray(m)
    ImageDraw.Draw(img).rectangle((x0, y0, x1, y1), fill=255)
    return np.asarray(img.filter(ImageFilter.GaussianBlur(5)).resize((PW, PH), Image.BILINEAR))


EDGE_FADE = 64  # 画面の左右の端をなじませる幅


def _highlight_select(img: np.ndarray, rect: tuple, k: float) -> None:
    """画面の文字に「選択」の青を付ける（左から k の割合まで）。掛け算なので文字は黒いまま・地だけ青くなる"""
    pad = 5
    x0, y0, x1, y1 = rect[0] - pad, rect[1] - pad * 0.6, rect[2] + pad, rect[3] + pad * 0.6
    x1 = x0 + (x1 - x0) * k
    xa, ya, xb, yb = max(0, int(x0)), max(0, int(y0)), min(img.shape[1], int(x1)), min(img.shape[0], int(y1))
    if xa >= xb or ya >= yb:
        return
    roi = img[ya:yb, xa:xb].astype(np.uint16)
    img[ya:yb, xa:xb] = ((roi * np.array(SELECT, dtype=np.uint16)) >> 8).astype(np.uint8)


def push_in(arr: np.ndarray, info: dict, L: "Ladder", t: float, t_hi: float, amount: float = 0.16) -> np.ndarray:
    """グラフのカメラ: ずっとごくゆっくり寄り、主役の行を強調したあとは、その行へ寄っていく（止まった絵にしない）"""
    if cv2 is None:
        return arr
    base = 0.03 * min(1.0, max(0.0, (t - L.t0) / max(L.t1 - L.t0, 0.5)))
    after = ease((t - t_hi) / 1.4)
    k = 1 + base + amount * after
    subj = next((r for r in info["rows"] if r.get("subj")), None)
    cy = PH / 2 + ((subj["y"] + subj["h"] / 2 - 10 - PH / 2) * after if subj else 0)
    cx = PW / 2 - PW * 0.08 * after
    if k <= 1.001:
        return arr
    m = np.array([[k, 0, cx * (1 - k)], [0, k, cy * (1 - k)]], dtype=np.float32)
    return cv2.warpAffine(arr, m, (PW, PH), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _highlight_row(img: np.ndarray, x: int, y: int, w: int, h: int, k: float) -> None:
    """料金の段階のグラフで、主役のプランの行の地を薄い青に（不透明度 k）"""
    roi = img[max(0, y) : y + h, max(0, x) : x + w].astype(np.float32)
    img[max(0, y) : y + h, max(0, x) : x + w] = (roi * (1 - 0.55 * k) + np.array(MARKER, dtype=np.float32) * 0.55 * k).astype(np.uint8)


def _highlight_marker(frame: np.ndarray, x: int, y: int, w: int, h: int, k: float) -> None:
    """キーワードの下半分を走る薄い青のマーカー（左から k の割合まで）"""
    xb = x + int(w * k)
    if xb <= x:
        return
    frame[y : y + h, x:xb] = MARKER


def _cursor_sprite() -> np.ndarray:
    """カーソル（白い矢印・黒いふち・やわらかい影）。塗りの多角形を2枚重ねる"""
    s = 4
    pts = [(0, 0), (0, 46), (11, 35), (19, 54), (27, 50), (19, 32), (34, 32)]
    big = Image.new("RGBA", (60 * s, 76 * s), (0, 0, 0, 0))
    shadow = Image.new("L", big.size, 0)
    ImageDraw.Draw(shadow).polygon([((x + 6) * s, (y + 8) * s) for x, y in pts], fill=110)
    shadow = shadow.filter(ImageFilter.GaussianBlur(5 * s))
    big.paste((0, 0, 0, 255), (0, 0), shadow)
    d = ImageDraw.Draw(big)
    d.polygon([((x + 4) * s, (y + 4) * s) for x, y in pts], fill=(20, 20, 24, 255))
    cx, cy = 4 + 34 / 3, 4 + 30
    inner = [((cx + (x + 4 - cx) * 0.80) * s, (cy + (y + 4 - cy) * 0.80) * s + 2 * s) for x, y in pts]
    d.polygon(inner, fill=(255, 255, 255, 255))
    return np.asarray(big.resize((50, 63), Image.LANCZOS))


def data_bars(card: Image.Image, rows: list[tuple[int, int, int, tuple]], height: int = 56, radius: int | None = None) -> None:
    """比較グラフの棒・たとえの四角（データの印だけ。飾りではない）: [(x, y, 長さ, 色)]。radius を渡すと長さどおりの角丸の四角"""
    d = ImageDraw.Draw(card)
    for x, y, length, color in rows:
        if length > 2:
            if radius is not None:
                d.rounded_rectangle((x, y, x + length, y + height), radius=radius, fill=color)
            else:
                d.rounded_rectangle((x, y, x + max(length, int(height * 1.4)), y + height), radius=height // 2, fill=color)


class Renderer:
    def __init__(self, plan: Plan):
        self.plan = plan
        self.stage = stage_bg()
        self.corner = _mask_panel_corners()
        self.cursor = _cursor_sprite()
        self.pointers = {True: _mask_pointer(True), False: _mask_pointer(False)}
        self.edge = (np.linspace(0, 1, EDGE_FADE, dtype=np.float32) ** 1.8) * 0.95  # 端ほど地の色
        self.levels = [self._pyramid(p.img) for p in plan.pages]
        self.bgs = [p.bg() for p in plan.pages]
        self.chart_cards = [self._chart_base(c) for c in plan.charts]
        self.ladder_cards = [self._ladder_base(L) for L in plan.ladders]
        self.end_start = plan.narr.end_start
        self.total = plan.narr.total
        self.panel_stage = self.stage[PANEL[1] : PANEL[3], PANEL[0] : PANEL[2]].copy()

    @staticmethod
    def _pyramid(img: np.ndarray) -> list[tuple[float, np.ndarray]]:
        """ミップマップ（1, 0.71, 0.5, 0.35）。縮小しすぎてちらつかないように近い大きさから引く"""
        out = [(1.0, img)]
        cur = img
        for scale in (0.7071, 0.5, 0.3536):
            size = (max(1, int(img.shape[1] * scale)), max(1, int(img.shape[0] * scale)))
            if cv2 is not None:
                cur = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
            else:  # pragma: no cover
                cur = np.asarray(Image.fromarray(img).resize(size, Image.BOX))
            out.append((scale, cur))
        return out

    def warp(self, cam: Cam) -> np.ndarray:
        levels = self.levels[cam.page]
        lvl_s, img = levels[-1]
        for ls, im in levels:
            if cam.s / ls >= 0.70:
                lvl_s, img = ls, im
                break
        k = cam.s / lvl_s
        tx = FOCUS[0] - cam.s * cam.cx
        ty = FOCUS[1] - cam.s * cam.cy
        if cv2 is not None:
            m = np.array([[k, 0, tx], [0, k, ty]], dtype=np.float32)
            return cv2.warpAffine(img, m, (PW, PH), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=self.bgs[cam.page])
        inv = (1 / k, 0, -tx / k, 0, 1 / k, -ty / k)  # pragma: no cover
        return np.asarray(Image.fromarray(img).transform((PW, PH), Image.AFFINE, inv, resample=Image.BILINEAR, fillcolor=self.bgs[cam.page]))

    BAR_H = 56

    def _chart_base(self, c: Chart) -> dict:
        """比較グラフのカード（白地・名前2つ・倍率）。棒は毎フレーム長さを変えて描く"""
        card = np.full((PH, PW, 3), 255, dtype=np.uint8)
        labels = []
        y = 250
        for name in (c.base, c.subject):
            spr = render_text([(name, "bold", INK)], 56)
            labels.append((name, spr, 110, y))
            y += 250
        # 倍率は記事の表記どおり（台本の compare.ratio）をそのまま。途中の数字（カウントアップ）は描かない
        value = render_text([(c.ratio, "black", ACCENT)], 150)
        return {"card": card, "labels": labels, "chart": c, "value": value}

    LAD_X = 76

    def _ladder_base(self, L: "Ladder") -> dict:
        """料金の段階のカード: 見出し・各行（プラン名・価格・使える量）の文字を先に作る。棒は毎フレーム描く"""
        if L.mode == "flow":
            n = len(L.rows)
            arrow = render_text([("→", "black", BLUE)], 60)
            gap = arrow.shape[1] + 36
            box_w = int((PW - 2 * self.LAD_X - gap * (n - 1)) / n)
            steps = [wrapped(r["name"], "black", range(46, 26, -2), box_w - 36, 2, color=INK, accent=False, center=True) for r in L.rows]
            nums = [render_text([(chr(0x2460 + k), "black", BLUE)], 34) for k in range(n)]
            title = render_text([(L.title, "bold", BLUE)], 30, 0.06) if L.title else None
            note = wrapped(L.unit, "black", range(46, 30, -2), PW - 2 * self.LAD_X, 2, color=INK) if L.unit else None
            return {"steps": steps, "nums": nums, "arrow": arrow, "gap": gap, "box_w": box_w, "title": title, "note": note, "rows": []}
        if L.mode == "units":
            title = render_text([(f"{L.unit}を", "bold", BLUE), ("■", "bold", BLUE), ("1つとすると", "bold", BLUE)], 30, 0.04)
            src = render_text([("公式の倍率より", "bold", SOFT)], 22)
            n = len(L.rows)
            top, bottom = 170, PH - 90
            row_h = min(200, (bottom - top) // n)
            rows = []
            for k, r in enumerate(L.rows):
                subj = r["name"].lower() == (L.subject or "").lower()
                rows.append({
                    "y": top + k * row_h, "h": row_h, "subj": subj, "count": r["count"],
                    "name": render_text([(r["name"], "black", INK)], 46),
                    "label": render_text([(f"{r['count']}個分", "black", ACCENT if subj else MUTED)], 40),
                })
            return {"title": title, "src": src, "rows": rows}
        title = render_text([(L.title or "料金と使える量", "bold", BLUE)], 28, 0.06)
        src = render_text([(L.source or "公式の料金ページより", "bold", SOFT)], 22)
        n = len(L.rows)
        top, bottom = 150, PH - 70
        row_h = min(190, (bottom - top) // n)
        mults = [float(r["mult"]) for r in L.rows if re.fullmatch(r"\d+(?:\.\d+)?", str(r.get("mult", "")).strip())]
        max_mult = max(mults) if mults else 1.0
        rows = []
        long = max(len(r.get("name", "")) for r in L.rows) > 8 or max(len(r.get("price", "")) for r in L.rows) > 8
        fs = 34 if long else 46  # 長い名前・価格は小さめに（棒の場所を残す）
        for k, r in enumerate(L.rows):
            subj = r.get("name", "").lower() == (L.subject or "").lower()
            name = render_text([(r["name"], "black", INK)], fs)
            price = render_text(accent_segments(r["price"], "black", INK) if subj else [(r["price"], "black", INK)], fs)
            usage = render_text([(r.get("usage", ""), "bold", BLUE if subj else MUTED)], 30) if r.get("usage") else None
            m = str(r.get("mult", "")).strip()
            frac = float(m) / max_mult if re.fullmatch(r"\d+(?:\.\d+)?", m) else 0.0
            rows.append({"y": top + k * row_h, "h": row_h, "name": name, "price": price, "usage": usage, "frac": frac, "subj": subj})
        return {"title": title, "src": src, "rows": rows}

    def units_frame(self, info: dict, L: "Ladder", t: float) -> np.ndarray:
        """たとえの絵: 各行に、基準の量の四角を count 個（左から1つずつ並ぶ）"""
        arr = np.full((PH, PW, 3), 255, dtype=np.uint8)
        x0 = self.LAD_X
        n = len(info["rows"])
        t_hi = L.t0 + 0.3 + L.reveal * n + 0.3
        hi = ease_out((t - t_hi) / 0.4)
        sq, gap = 46, 12
        boxes = []
        labels = []
        for k, row in enumerate(info["rows"]):
            age = t - (L.t0 + 0.3 + L.reveal * k)
            if age < 0:
                continue
            if row["subj"] and hi > 0:
                _highlight_row(arr, 30, row["y"] - 20, PW - 60, row["h"] - 16, hi)
            shown = min(row["count"], int(age / 0.05) + 1)
            mid = row["y"] + row["h"] // 2 - 30
            for j in range(shown):
                boxes.append((x0 + 190 + j * (sq + gap), mid + 30 - sq // 2, sq, BLUE if row["subj"] else (196, 205, 220)))
            a = ease_out(age / 0.35)
            labels.append((row, mid, a, x0 + 190 + row["count"] * (sq + gap) + 14))
        img = Image.fromarray(arr)
        data_bars(img, boxes, sq, radius=9)
        arr = np.asarray(img).copy()
        blend(arr, info["title"], x0, 70)
        blend(arr, info["src"], x0 + info["title"].shape[1] + 24, 76)
        for row, mid, a, lx in labels:
            blend(arr, row["name"], x0, mid + 30 - row["name"].shape[0] // 2, a)
            blend(arr, row["label"], lx, mid + 30 - row["label"].shape[0] // 2, a)
        return push_in(arr, info, L, t, t_hi)

    def flow_frame(self, info: dict, L: "Ladder", t: float) -> np.ndarray:
        """手順の絵: 番号つきの箱が左から1つずつ現れ、間に矢印。最後に一言（note）"""
        arr = np.full((PH, PW, 3), 255, dtype=np.uint8)
        n = len(info["steps"])
        x0, box_w, gap = self.LAD_X, info["box_w"], info["gap"]
        box_h = 230
        y_box = PH // 2 - box_h // 2 - 40
        boxes, items = [], []
        for k in range(n):
            age = t - (L.t0 + 0.3 + L.reveal * k)
            if age < 0:
                continue
            a = ease_out(age / 0.35)
            bx = x0 + k * (box_w + gap)
            dy = int((1 - a) * 24)
            boxes.append((bx, y_box + dy, box_w, MARKER if a >= 0.5 else (236, 241, 249)))
            items.append((k, bx, dy, a))
        img = Image.fromarray(arr)
        data_bars(img, boxes, box_h, radius=22)
        arr = np.asarray(img).copy()
        if info["title"] is not None:
            blend(arr, info["title"], x0, 70)
        for k, bx, dy, a in items:
            num, st = info["nums"][k], info["steps"][k]
            blend(arr, num, bx + 22, y_box + 18 + dy, a)
            blend(arr, st, bx + (box_w - st.shape[1]) // 2, y_box + (box_h - st.shape[0]) // 2 + 14 + dy, a)
            if k > 0:
                ar = info["arrow"]
                blend(arr, ar, bx - gap + (gap - ar.shape[1]) // 2, y_box + (box_h - ar.shape[0]) // 2 + dy, a)
        t_hi = L.t0 + 0.3 + L.reveal * n + 0.2
        if info["note"] is not None and t >= t_hi:
            a = ease_out((t - t_hi) / 0.4)
            blend(arr, info["note"], x0, y_box + box_h + 60 + int((1 - a) * 16), a)
        # ゆっくり寄り、まとめの一言が出たらそこへ寄る（止まった絵にしない）
        if cv2 is not None:
            after = ease((t - t_hi) / 1.4)
            k = 1 + 0.04 * min(1.0, max(0.0, (t - L.t0) / max(L.t1 - L.t0, 0.5))) + 0.09 * after
            cy = PH / 2 + (y_box + box_h + 90 - PH / 2) * after * 0.6
            m = np.array([[k, 0, PW / 2 * (1 - k)], [0, k, cy * (1 - k)]], dtype=np.float32)
            arr = cv2.warpAffine(arr, m, (PW, PH), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        return arr

    def ladder_frame(self, idx: int, t: float) -> np.ndarray:
        info = self.ladder_cards[idx]
        L: Ladder = self.plan.ladders[idx]
        if L.mode == "units":
            return self.units_frame(info, L, t)
        if L.mode == "flow":
            return self.flow_frame(info, L, t)
        card = Image.new("RGB", (PW, PH), (255, 255, 255))
        x0 = self.LAD_X
        name_w = max(r["name"].shape[1] for r in info["rows"])
        price_w = max(r["price"].shape[1] for r in info["rows"])
        price_x = x0 + max(150, name_w + 36)
        bar_x = price_x + price_w + 40
        bar_max = max(160, PW - bar_x - 230)
        n = len(info["rows"])
        t_hi = L.t0 + 0.3 + L.reveal * n + 0.3
        hi = ease_out((t - t_hi) / 0.4)
        focus_k = None  # 順に強調するときの今の行
        if L.tour and t >= t_hi:
            focus_k = min(n - 1, int((t - t_hi) / L.tour))
            hi = ease_out((t - t_hi - focus_k * L.tour) / 0.3)
        arr = np.asarray(card).copy()
        bars = []
        for k, row in enumerate(info["rows"]):
            age = t - (L.t0 + 0.3 + L.reveal * k)
            if age < 0:
                continue
            on = (k == focus_k) if focus_k is not None else row["subj"]
            if on and hi > 0:
                _highlight_row(arr, 30, row["y"] - 18, PW - 60, row["h"] - 14, hi)
            grow = ease_out(age / 0.45)
            length = int((max(row["frac"], 0.0) * bar_max if row["frac"] else 36) * grow)
            mid = row["y"] + row["h"] // 2 - 30
            bars.append((bar_x, mid + 6, length, BLUE if row["subj"] else (204, 212, 225)))
            row["_age"] = age
            row["_len"] = length
            row["_mid"] = mid
        img = Image.fromarray(arr)
        data_bars(img, bars, 40)
        arr = np.asarray(img).copy()
        blend(arr, info["title"], x0, 60)
        blend(arr, info["src"], x0 + info["title"].shape[1] + 24, 64)
        for k, row in enumerate(info["rows"]):
            age = row.pop("_age", None)
            if age is None:
                continue
            a = ease_out(age / 0.35)
            mid = row.pop("_mid")
            length = row.pop("_len")
            dx = int((1 - a) * 90)  # 右から滑り込む
            blend(arr, row["name"], x0 + dx, mid - row["name"].shape[0] // 2 + 26, a)
            blend(arr, row["price"], price_x + dx, mid - row["price"].shape[0] // 2 + 26, a)
            if row["usage"] is not None:
                blend(arr, row["usage"], bar_x + max(length, 56) + 20, mid + 26 - row["usage"].shape[0] // 2, a)
        if focus_k is not None:  # 強調している行へ軽く寄る
            info = {**info, "rows": [{**r, "subj": i == focus_k} for i, r in enumerate(info["rows"])]}
        return push_in(arr, info, L, t, t_hi if focus_k is None else t_hi + focus_k * L.tour, 0.08 if focus_k is not None else 0.16)

    def chart_frame(self, idx: int, t: float) -> tuple[np.ndarray, list]:
        info = self.chart_cards[idx]
        c: Chart = info["chart"]
        card = Image.fromarray(info["card"].copy())
        ratio = float(re.sub(r"[^\d.]", "", gate.norm(c.ratio)) or 1)
        maxlen = PW - 110 - 110
        grow = ease_out((t - c.t0 - 0.2) / 0.9)
        base_len = max(self.BAR_H, int(maxlen / max(ratio, 1)))
        rows = []
        for k, (name, spr, x, y) in enumerate(info["labels"]):
            bar_y = y + spr.shape[0] + 26
            length = int(base_len * min(1, max(0.0, (t - c.t0) / 0.25))) if k == 0 else int(self.BAR_H + (maxlen - self.BAR_H) * grow)
            rows.append((x, bar_y, length, (212, 219, 230) if k == 0 else BLUE))
        data_bars(card, rows, self.BAR_H)
        arr = np.asarray(card).copy()
        for name, spr, x, y in info["labels"]:
            blend(arr, spr, x, y)
        spr = info["value"]
        _, lab, _, sy = info["labels"][1]
        k = ease_out((t - c.t0 - 0.55) / 0.3)
        blend(arr, spr, 110 + maxlen - spr.shape[1], sy + lab.shape[0] - spr.shape[0] + int((1 - k) * 20), k)
        return arr, []

    def cursor_at(self, t: float, cam: Cam) -> tuple[float, float, float, float] | None:
        """(x, y, 不透明度, 押した後の経過) パネル座標。カーソルは前のクリックから次のクリックへ弧を描いて動く"""
        clicks = [c for c in self.plan.clicks if c.page == cam.page]
        nxt = next((c for c in clicks if c.t >= t - 0.6), None)
        prev = [c for c in clicks if c.t < t]
        if not clicks:
            return None
        if nxt is None:
            last = prev[-1]
            px, py, since = last.x, last.y, t - last.t
        else:
            src = prev[-1] if prev and prev[-1] is not nxt else None
            start = nxt.t - 0.6
            if t < start:
                if src is None:
                    return None
                px, py, since = src.x, src.y, t - src.t
            else:
                k = ease((t - start) / 0.6)
                sx, sy = (src.x, src.y) if src else (nxt.x + 260 / cam.s, nxt.y + 420 / cam.s)
                dx, dy = nxt.x - sx, nxt.y - sy
                arc = math.sin(math.pi * k) * 0.12
                px, py = sx + dx * k - dy * arc, sy + dy * k + dx * arc
                since = t - nxt.t
        alpha = 1.0
        first_t = clicks[0].t - 0.6
        alpha *= min(1.0, max(0.0, (t - first_t) / 0.15))
        for m in self.plan.camera.moves:
            if m.cut and m.t > clicks[-1].t and t > m.t - 0.15:
                alpha = 0.0
        concl = self.plan.narr.lines[-1].start - 0.16
        if t > concl:
            alpha *= max(0.0, 1 - (t - concl) / 0.2)
        x, y = cam.map(px, py)
        return x, y, alpha, since

    def panel(self, t: float) -> np.ndarray:
        cam = self.plan.camera.at(t)
        img = self.motion_blurred(t, cam)
        # 注目（周りを少し暗く）＋ target の文字に選択の青
        for sp in self.plan.spots:
            if sp.page != cam.page or not (sp.t0 <= t <= sp.t1 + 0.25):
                continue
            a = min(1.0, (t - sp.t0) / 0.3) * min(1.0, max(0.0, (sp.t1 + 0.25 - t) / 0.25))
            if a <= 0:
                continue
            x0, y0 = cam.map(sp.rect[0], sp.rect[1])
            x1, y1 = cam.map(sp.rect[2], sp.rect[3])
            mask = _mask_spot((x0, y0, x1, y1))
            dim = 0.10 * a
            f = (255 - dim * (255 - mask.astype(np.float32))).astype(np.uint16)
            img = ((img.astype(np.uint16) * f[..., None]) >> 8).astype(np.uint8)
            if sp.sel and t >= sp.sel_t and t <= sp.t1:
                # 行ごとに左から順に選択していく（複数行の文は1行目 → 2行目）
                widths = [max(r[2] - r[0], 1) for r in sp.sel]
                done = ease_out((t - sp.sel_t) / (0.35 + 0.12 * (len(sp.sel) - 1))) * sum(widths)
                for r, wd in zip(sp.sel, widths):
                    k = min(1.0, max(0.0, done / wd))
                    done -= wd
                    if k <= 0:
                        break
                    sx0, sy0 = cam.map(r[0], r[1])
                    sx1, sy1 = cam.map(r[2], r[3])
                    _highlight_select(img, (sx0, sy0, sx1, sy1), k)
        # 左右の端はページの地の色へなじませる（行の途中で切れた語が、ぶつっと切れて見えないように）
        bg = np.array(self.bgs[cam.page], dtype=np.float32)
        e = EDGE_FADE
        for sl, ramp in ((np.s_[:, :e], self.edge[::-1]), (np.s_[:, PW - e :], self.edge)):
            part = img[sl].astype(np.float32)
            img[sl] = (part * (1 - ramp[None, :, None]) + bg * ramp[None, :, None]).astype(np.uint8)
        # 訳の吹き出し（選択した英文のすぐ下・選択が終わるころにふわっと出る）
        for sub in self.plan.subtitles:
            fa = self._fade(t, sub.t0, sub.t1, 0.2)
            place = callout_place(sub, cam) if fa > 0 else None
            if not place:
                continue
            x, y, tip_x, tip_y, up = place
            k = ease_out((t - sub.t0) / 0.3)
            dy = int((1 - k) * (-10 if up else 10))
            blend(img, sub.sprite, x - CALLOUT_SH, y - CALLOUT_SH + dy, fa)
            blend(img, self.pointers[up], tip_x, tip_y + dy - (0 if up else 2), fa)
        # カーソルとクリックの波紋
        cur = self.cursor_at(t, cam)
        if cur:
            x, y, alpha, since = cur
            if 0 <= since < 0.45:
                k = since / 0.45
                self._ripple(img, x, y, 14 + 40 * ease_out(k), 0.40 * (1 - k))
            spr = self.cursor
            if 0 <= since < 0.16:
                sc = 1 - 0.18 * math.sin(math.pi * since / 0.16)
                h2, w2 = int(spr.shape[0] * sc), int(spr.shape[1] * sc)
                spr = cv2.resize(spr, (w2, h2), interpolation=cv2.INTER_AREA) if cv2 is not None else np.asarray(Image.fromarray(spr).resize((w2, h2)))
            if alpha > 0:
                blend(img, spr, int(x - 4), int(y - 4), alpha)
        # 料金の段階のグラフ
        for k, L in enumerate(self.plan.ladders):
            if L.t0 <= t <= L.t1 + 0.2:
                a = min(1.0, (t - L.t0) / 0.25) * min(1.0, max(0.0, (L.t1 + 0.2 - t) / 0.2))
                card = self.ladder_frame(k, t)
                img = ((img.astype(np.uint16) * int((1 - a) * 256) + card.astype(np.uint16) * int(a * 256)) >> 8).astype(np.uint8)
        # 比較のグラフ
        for k, ch in enumerate(self.plan.charts):
            if ch.t0 <= t <= ch.t1 + 0.2:
                a = min(1.0, (t - ch.t0) / 0.25) * min(1.0, max(0.0, (ch.t1 + 0.2 - t) / 0.2))
                card, _ = self.chart_frame(k, t)
                img = ((img.astype(np.uint16) * int((1 - a) * 256) + card.astype(np.uint16) * int(a * 256)) >> 8).astype(np.uint8)
        return img

    @staticmethod
    def _fade(t: float, t0: float, t1: float, d: float) -> float:
        if t < t0 or t > t1 + d:
            return 0.0
        return min(1.0, (t - t0) / d) * min(1.0, max(0.0, (t1 + d - t) / d))

    def motion_blurred(self, t: float, cam: Cam) -> np.ndarray:
        """速く動く所だけモーションブラー（シャッター 180度相当）。
        横・縦の移動は動きの向きにぼかし、拡大・縮小は前後の数枚を重ねる"""
        c1, c2 = self.plan.camera.at(t - 1 / 120), self.plan.camera.at(t + 1 / 120)
        img = self.warp(cam)
        if c1.page != c2.page:
            return img
        dx, dy = (c1.cx - c2.cx) * cam.s, (c1.cy - c2.cy) * cam.s  # 1/60 秒で画面上を動く量
        zoom_px = abs(math.log(c2.s / c1.s)) * PH * 0.5
        length = math.hypot(dx, dy)
        if zoom_px > 4 and zoom_px > length:
            n = int(min(6, 2 + zoom_px // 4))
            acc = np.zeros(img.shape, dtype=np.uint32)
            for k in range(n):
                acc += self.warp(self.plan.camera.at(t + (k / (n - 1) - 0.5) / 60))
            return (acc // n).astype(np.uint8)
        if length > 3 and cv2 is not None:
            size = int(min(101, length * 0.6)) | 1
            if size < 3:
                return img
            kernel = np.zeros((size, size), dtype=np.float32)
            c = size // 2
            for i in range(size):  # 動きの向きに並べた点（線を描く関数は使わない）
                u = i / (size - 1) - 0.5
                kernel[int(round(c + u * (size - 1) * dy / length)), int(round(c + u * (size - 1) * dx / length))] += 1
            kernel /= kernel.sum()
            return cv2.filter2D(img, -1, kernel, borderType=cv2.BORDER_REPLICATE)
        return img

    @staticmethod
    def _ripple(img: np.ndarray, x: float, y: float, r: float, alpha: float) -> None:
        R = int(r) + 3
        x0, y0 = int(x) - R, int(y) - R
        yy, xx = np.mgrid[0 : 2 * R, 0 : 2 * R].astype(np.float32)
        dist = np.sqrt((xx + x0 - x) ** 2 + (yy + y0 - y) ** 2)
        a = np.clip(r - dist, 0, 1.5) / 1.5 * alpha * 255
        spr = np.zeros((2 * R, 2 * R, 4), dtype=np.uint8)
        spr[..., :3] = BLUE
        spr[..., 3] = a.astype(np.uint8)
        blend(img, spr, x0, y0)

    def frame(self, i: int) -> np.ndarray:
        t = i / FPS
        frame = self.stage.copy()
        panel = self.panel(t)
        r = RADIUS
        for (sy, sx, flip) in ((slice(0, r), slice(0, r), (False, False)), (slice(0, r), slice(PW - r, PW), (False, True)), (slice(PH - r, PH), slice(0, r), (True, False)), (slice(PH - r, PH), slice(PW - r, PW), (True, True))):
            a = self.corner
            if flip[0]:
                a = a[::-1]
            if flip[1]:
                a = a[:, ::-1]
            panel[sy, sx] = (panel[sy, sx] * a[..., None] + self.panel_stage[sy, sx] * (1 - a[..., None])).astype(np.uint8)
        frame[PANEL[1] : PANEL[3], PANEL[0] : PANEL[2]] = panel
        for item in self.plan.texts:
            if not (item.t0 - 1e-6 <= t < item.t1 + 0.2):
                continue
            age = t - item.t0 - item.delay
            if age < 0:
                continue
            k_out = 1 - ease((t - item.t1) / 0.2) if t > item.t1 else 1.0
            h, w = item.sprite.shape[:2]
            dy_out = -int((1 - k_out) * 14)
            if item.anim == "none":
                blend(frame, item.sprite, item.x, item.y + dy_out, k_out)
            elif item.anim == "wipe":
                # 左から現れる（やわらかい端）＋少し上がる。マーカーは少し遅れて左から走る
                k = ease_out(age / 0.5)
                if item.marker:
                    mk = ease((age - 0.22) / 0.45)
                    mh = int(h * 0.34)
                    if mk > 0 and k_out > 0.6:
                        _highlight_marker(frame, item.x - 6, item.y + h - mh - int(h * 0.06) + dy_out, w + 12, mh, mk)
                edge = 60.0
                col = np.clip((k * (w + edge) - np.arange(w, dtype=np.float32)) / edge, 0, 1)
                blend(frame, item.sprite, item.x, item.y + int((1 - k) * 16) + dy_out, k_out, col_alpha=col)
            else:  # rise: 下から上がってくる（下は見えない所から）
                k = ease_out(age / 0.38)
                blend(frame, item.sprite, item.x, item.y + int((1 - k) * 22) + dy_out, k * k_out)
        return frame


_WORKER: Renderer | None = None


def _render(i: int) -> bytes:
    assert _WORKER is not None
    return _WORKER.frame(i).tobytes()


def encode(renderer: Renderer, audio: Path, out: Path, fps: int = FPS, workers: int | None = None, out_scale: float = 1.0, preset: str = "veryfast") -> None:
    global _WORKER
    n = int(round(renderer.total * fps))
    vf = ["-vf", f"scale={int(W * out_scale) // 2 * 2}:{int(H * out_scale) // 2 * 2}:flags=area"] if out_scale != 1.0 else []
    cmd = [
        shorts.ffmpeg_bin(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
        "-i", str(audio), "-map", "0:v", "-map", "1:a", *vf,
        # 長い動画は 24MiB に収まるよう少し強めに圧縮（文字の画面なので crf 23 でも十分きれい）
        "-c:v", "libx264", "-preset", preset, "-crf", "20" if renderer.total <= 45 else "23",
        "-maxrate", f"{min(8.0, max(2.0, 150.0 / max(renderer.total, 1))):.1f}M", "-bufsize", "16M",
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps * 2), "-keyint_min", str(fps), "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "160k", "-ar", str(SR), "-ac", "2",
        "-t", f"{renderer.total:.3f}", "-movflags", "+faststart", str(out),
    ]  # fmt: skip
    workers = workers if workers is not None else max(1, min(8, (os.cpu_count() or 2)))
    step = FPS / fps
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin
    try:
        if workers <= 1 or sys.platform == "win32":
            for i in range(n):
                proc.stdin.write(renderer.frame(int(round(i * step))).tobytes())
        else:
            import multiprocessing as mp

            _WORKER = renderer
            with mp.get_context("fork").Pool(workers) as pool:
                for data in pool.imap(_render, [int(round(i * step)) for i in range(n)], chunksize=4):
                    proc.stdin.write(data)
            _WORKER = None
    finally:
        proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg が失敗しました")


# --------------------------------------------------------------------------- #
# 7. 記録・見比べ用の画像・まとめ
# --------------------------------------------------------------------------- #

SAFE_V3 = (40, 30, W - 40, H - 30)


def text_records(plan: Plan) -> list[dict]:
    out = []
    rows = [(item.role, item.text, item.sprite, item.bbox(), item.t0, item.t1) for item in plan.texts]
    for sub in plan.subtitles:  # 吹き出しはカメラと動くので、0.5秒ごとの位置で記録する（注目の場所と同じ刻み）
        spr = sub.sprite
        for w0, w1, wm in time_windows(sub.t0, sub.t1):
            place = callout_place(sub, plan.camera.at(wm))
            if not place:
                continue
            x, y = PANEL[0] + place[0], PANEL[1] + place[1]
            # 文字の面積は吹き出しの中の文字の所だけ（余白・影は数えない）。かぶりの検査はこの枠＋余白で十分（英文の行の外に置いている）
            bbox = [float(x + 26), float(y + 16), float(x + spr.shape[1] - CALLOUT_SH * 2 - 26), float(y + spr.shape[0] - CALLOUT_SH * 2 - 16)]
            rows.append(("subtitle", sub.text, spr, bbox, w0, w1))
    for role, text, spr, bbox, t0, t1 in rows:
        if role in ("hook", "keyword", "sub", "conclusion", "eyebrow"):
            box = list(COL)
        elif role in ("source", "credit"):
            box = [COL[0], SOURCE_Y - 60, PANEL[0] - 8, H - 20]
        elif role == "subtitle":
            box = list(PANEL)
        else:
            box = [0, 0, PANEL[0], PANEL[1] + 40]
        size = {"hook": 46, "keyword": 52, "sub": 30, "conclusion": 46, "eyebrow": 20, "source": 18, "credit": 16, "subtitle": 24, "logo": 24}.get(role, 18)
        color = {"sub": MUTED, "source": SOFT, "credit": SOFT, "eyebrow": BLUE}.get(role, INK)
        if role == "subtitle":  # 墨の吹き出しの上の白い字
            contrast = gate.contrast(WHITE, INK)
        else:
            contrast = min(gate.contrast(color, STAGE), gate.contrast(ACCENT, STAGE) if role == "keyword" else 99)
        face = "logo" if role == "logo" else ("black" if role in ("hook", "keyword", "conclusion") else "bold")
        out.append(
            {
                "role": role,
                "text": text,
                "bbox": [round(v, 1) for v in bbox],
                "box": box,
                "size": int(spr.shape[0] * 0.75) if role != "subtitle" else int((bbox[3] - bbox[1]) * 0.75),
                "min_size": size * 3 // 4,
                "contrast": round(contrast, 2),
                "missing": gate.missing_glyphs(text, shorts.font(face, 40)),
                "safe": True,
                "t0": round(t0, 3),
                "t1": round(t1, 3),
            }
        )
    return out


def units_for_gate(plan: Plan) -> list[dict]:
    """字幕（画面のキーワード）とナレーションの対応（v2 のゲートと同じ形）"""
    by_line: dict[int, list] = {}
    order = {"hook": 0, "conclusion": len(plan.narr.lines) - 1}
    beat_i = 0
    for item in plan.texts:
        if item.role in ("hook", "conclusion"):
            line = order[item.role]
        elif item.role == "keyword":
            line = 0 if item.t0 < plan.narr.lines[1].start - 0.2 else None
            if line is None:
                beat_i += 1
                line = beat_i
        else:
            continue
        by_line.setdefault(line, []).append({"text": item.text, "start": round(item.t0 if item.t0 > 0.05 else 0.0, 3), "end": round(item.t1, 3)})
    units = []
    for ln in plan.narr.lines:
        kind = {"hook": "hook", "beat": "point", "conclusion": "use"}[ln.kind]
        chunks = sorted(by_line.get(ln.index, []), key=lambda c: c["start"])
        units.append({"kind": kind, "line": ln.index, "caption": " ".join(c["text"] for c in chunks), "start": round(ln.start, 3), "end": round(ln.end, 3), "chunks": chunks})
    return units


def make_meta(a: dict, script: dict, voice: dict, pages: list[Page]) -> dict:
    url = f"https://{SITE}/articles/{a['slug']}"
    title = re.sub(r"\s+", " ", script["title"]).strip()
    title = title if len(title) <= 40 else title[:39] + "…"
    tags = []
    for tag in list(script.get("hashtags", []))[:4] + ["#サイソク"]:
        tag = "#" + re.sub(r"[\s#＃]+", "", str(tag))
        if len(tag) > 1 and tag not in tags:
            tags.append(tag)
    summary = script.get("summary", "").strip()
    if summary and not summary.startswith("要約すると"):
        summary = "要約すると、" + summary
    lines = [summary, "", "▼記事はこちら", url, ""]
    if voice.get("credit"):
        lines.append(f"音声：{voice['credit']}")
    for p in pages:
        lines.append(f"画面：{p.host}（公式ページ） {p.url}")
    lines.append("BGM・効果音：サイソクニュース自作")
    lines.append(SITE)
    lines += ["", " ".join(tags)]
    return {"title": title, "description": "\n".join(lines), "hashtags": tags, "url": url}


def contact_sheet(mp4: Path, plan: Plan, out: Path, every: float = 2.0) -> None:
    """2秒ごとのコマを並べる（実際の mp4 から取り出す）。動画を再生しなくても流れが見える"""
    total = plan.narr.total
    marks = [round(k * every, 2) for k in range(int(total / every) + 1)]
    if total - 0.4 - marks[-1] > 0.6:
        marks.append(round(total - 0.4, 2))
    tw, th = 480, 270
    cols = 4
    rows = math.ceil(len(marks) / cols)
    sheet = Image.new("RGB", (cols * (tw + 12) + 12, rows * (th + 40) + 12), (226, 230, 237))
    d = ImageDraw.Draw(sheet)
    f = shorts.font("bold", 20)
    for i, t in enumerate(marks):
        raw = subprocess.run(
            [shorts.ffmpeg_bin(), "-loglevel", "error", "-ss", f"{min(t, total - 0.05):.2f}", "-i", str(mp4), "-frames:v", "1", "-vf", f"scale={tw}:{th}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True,
        ).stdout  # fmt: skip
        frame = Image.frombytes("RGB", (tw, th), raw[: tw * th * 3])
        x, y = 12 + (i % cols) * (tw + 12), 12 + (i // cols) * (th + 40)
        sheet.paste(frame, (x, y))
        notes = [e["note"] for e in plan.events if t - every < e["t"] <= t and e["note"]]
        d.text((x, y + th + 6), f"{t:.1f}s  {(notes[-1] if notes else '')[:18]}", font=f, fill=(20, 26, 40))
    sheet.save(out)


def load_script(slug: str, article: dict, pages: list[Page], out_dir: Path, reuse: bool) -> dict:
    path = out_dir / f"{slug}.v3script.json"
    if reuse and path.exists():
        return latin_names(json.loads(path.read_text("utf-8")))
    script, model = write_script(article, pages)
    script["_model"] = model
    path.write_text(json.dumps(script, ensure_ascii=False, indent=2), "utf-8")
    return script


def make_video(
    slug: str,
    out_dir: Path,
    tts: str = "voicevox",
    reuse: bool = False,
    profile: str | None = None,
    article: dict | None = None,
    pages: list[Page] | None = None,
    script: dict | None = None,
    fps: int = FPS,
    out_scale: float = 1.0,
    workers: int | None = None,
    sheet: bool = True,
    bgm: bool = True,
    extra_pages: list[str] | None = None,
) -> dict:
    """1本作って品質ゲートにかける。戻り値は report（gate.ok で合否）。例外は投げず report に残す"""
    timer = Timer()
    RENDERED.clear()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{slug}__v3" + ("short" if (script or {}).get("_variant") == "short" else "")
    mp4 = out_dir / f"{name}.mp4"
    report: dict = {"slug": slug, "version": VERSION, "design": "v3", "design_name": "画面で見せる型", "errors": []}
    plan = None
    try:
        a = article or shorts.load_article(slug)
        warm = warm_voicevox() if tts == "voicevox" else None
        if pages is None:
            t = time.time()
            urls = page_urls(a, extra_pages)
            if not urls:
                raise RuntimeError("撮ってよい公式ページがありません（ニュースサイトが出典の記事は v3 の対象外）")
            pages = capture_pages(urls)
            timer.mark("capture", t)
            if not pages:
                raise RuntimeError("公式ページを撮れませんでした")
        report["pages"] = [{"url": p.url, "host": p.host, "title": p.title, "seconds": p.seconds, "size": [p.width, p.height], "blocks": len(p.blocks)} for p in pages]
        t = time.time()
        script = latin_names(script or load_script(slug, a, pages, out_dir, reuse))
        timer.mark("script", t)
        t = time.time()
        if warm:
            warm.join(timeout=60)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            narr = narrate(script, tts, work)
            timer.mark("tts", t)
            t = time.time()
            plan = make_plan(a, script, narr, pages)
            audio, audio_info = build_audio(narr, plan.events, work, bgm_on=bgm)
            timer.mark("plan_audio", t)
            t = time.time()
            encode(Renderer(plan), audio, mp4, fps=fps, workers=workers, out_scale=out_scale)
            timer.mark("render", t)
        report["audio"] = audio_info
    except Exception as error:  # noqa: BLE001 - 失敗は「動画なし」にするだけ
        import traceback

        report["errors"].append(f"{type(error).__name__}: {error}")
        report["traceback"] = traceback.format_exc()[-2000:]
        log(f"[v3] {slug}: 失敗 {type(error).__name__}: {error}")
    a = article or shorts.load_article(slug)
    if plan is not None:
        report.update(
            {
                "company": v2.main_name(a),
                "script": flat_script(plan.script),
                "script_v3": plan.script,
                "units": units_for_gate(plan),
                "total": round(plan.narr.total, 3),
                "end_start": round(plan.narr.end_start, 3),
                "allowed_silences": [[0.0, LEAD + 0.05], [round(plan.narr.lines[-1].end, 3), round(plan.narr.total, 3)]],
                "end_card": False,
                "texts": text_records(plan),
                "credits_shown": plan.credits,
                "voice": plan.narr.voice,
                "image_credit": "",
                "safe": list(SAFE_V3),
                "frame": [W, H],
                "layout": LAYOUT,
                "subtitles": [{"t0": round(x.t0, 3), "t1": round(x.t1, 3), "text": x.text} for x in plan.subtitles],
                "fps": fps,
                "events": plan.events,
                "focus": plan.focus,
                "fonts": [FONT_FAMILY, LOGO_FAMILY],
                "rendered_strings": sorted(set(RENDERED)),
                "charts": [{"t0": round(c.t0, 3), "t1": round(c.t1, 3), "base": c.base, "subject": c.subject, "ratio": c.ratio} for c in plan.charts],
                "ladders": [{"t0": round(L.t0, 3), "t1": round(L.t1, 3), "mode": L.mode, "unit": L.unit, "title": L.title, "subject": L.subject, "rows": [{k: r.get(k, "") for k in ("name", "price", "usage", "mult", "count") if k in r} for r in L.rows]} for L in plan.ladders],
                "official_text": official_text(plan.pages),
                "length_range": list(length_targets(plan.script)[2]),
                "variant": plan.script.get("_variant", "full"),
                "panel": list(PANEL),
                "targets": [{"page": t.page + 1, "block": t.block.id, "text": t.text, "block_text": t.block.text[:160]} if t else None for t in plan.targets],
                **make_meta(a, plan.script, plan.narr.voice, plan.pages),
            }
        )
    t = time.time()
    prof = profile or ("production" if (report.get("voice") or {}).get("engine") == "voicevox" else "preview")
    if mp4.exists() and not report["errors"]:
        res = gate.check_video(mp4, report, a, prof)
    else:
        res = gate.GateResult(ok=False, reasons=[f"描画: {e}" for e in report["errors"]] or ["動画がありません"])
    timer.mark("gate", t)
    if sheet and mp4.exists() and plan is not None and not report["errors"]:
        t = time.time()
        contact_sheet(mp4, plan, out_dir / f"{name}.sheet.png")
        timer.mark("sheet", t)
    report["gate"] = res.to_dict()
    report["sizeMB"] = round(mp4.stat().st_size / 1e6, 2) if mp4.exists() else 0
    report["timings"] = {**timer.stages, "total": round(time.time() - timer.t0, 1)}
    (out_dir / f"{name}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    log(f"[v3] {name}: {'合格' if res.ok else '不合格'} {report['timings']['total']}秒 {report['sizeMB']}MB {res.reasons[:4]}")
    return report


def make_versions(slug: str, out_dir: Path, tts: str = "voicevox", reuse: bool = False, extra_pages: list[str] | None = None, **kw) -> dict:
    """同じ台本から2本: サイト用（全部）と SNS 用（短い版）。ページの撮影と台本は1回だけ。
    戻り値 {"full": report, "short": report}。どちらも例外は投げず report に残す"""
    a = shorts.load_article(slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        pages = capture_pages(page_urls(a, extra_pages))
        script = latin_names(load_script(slug, a, pages, out_dir, reuse)) if pages else None
    except Exception as error:  # noqa: BLE001
        log(f"[v3] {slug}: 台本を作れない {type(error).__name__}: {error}")
        pages, script = None, None
    if not pages or not script:
        empty = {"slug": slug, "errors": ["公式ページか台本が無い"], "gate": {"ok": False, "reasons": ["公式ページか台本が無い"]}}
        return {"full": empty, "short": empty}
    full = make_video(slug, out_dir, tts, profile=kw.get("profile"), article=a, pages=pages, script=json.loads(json.dumps(script, ensure_ascii=False)))
    short = make_video(slug, out_dir, tts, profile=kw.get("profile"), article=a, pages=pages, script=short_script(script))
    return {"full": full, "short": short}


def main() -> None:
    parser = argparse.ArgumentParser(description="SNS 用ショート動画 v3（画面で見せる型）")
    parser.add_argument("slugs", nargs="+")
    parser.add_argument("--out", default=str(ROOT / "out" / "shorts_v3"))
    parser.add_argument("--tts", default=os.environ.get("SHORTS_TTS", "voicevox"), choices=["auto", "voicevox", "edge", "fake"])
    parser.add_argument("--reuse-script", action="store_true")
    parser.add_argument("--profile", choices=list(gate.PROFILES))
    parser.add_argument("--no-bgm", action="store_true")
    parser.add_argument("--pages", nargs="*", default=[], help="足して撮る公式ページ（他社の料金表など）")
    parser.add_argument("--short", action="store_true", help="同じ台本から SNS 用の短い版を作る（--reuse-script と一緒に）")
    args = parser.parse_args()
    import llm

    llm._load_env_local()
    failed = 0
    for slug in args.slugs:
        if args.short:
            path = Path(args.out) / f"{slug}.v3script.json"
            base = latin_names(json.loads(path.read_text("utf-8")))
            r = make_video(slug, Path(args.out), args.tts, False, args.profile, bgm=not args.no_bgm, extra_pages=args.pages, script=short_script(base))
        else:
            r = make_video(slug, Path(args.out), args.tts, args.reuse_script, args.profile, bgm=not args.no_bgm, extra_pages=args.pages)
        failed += 0 if r["gate"]["ok"] else 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
