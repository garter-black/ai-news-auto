#!/usr/bin/env python3
"""SNS 用の縦型ショート動画 v3「画面で見せる型」（20〜30秒・1080x1920・30fps）。

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

W, H, FPS = gate.W, gate.H, gate.FPS
SR = 48000
VERSION = "v3"

# 色（強調色は ACCENT の1色だけ）
STAGE = (11, 17, 30)
WHITE = (255, 255, 255)
SUBTLE = (150, 166, 190)
ACCENT = (255, 92, 74)  # サイソクの速報レッドを暗い背景で読める明るさにしたもの
FONT_FAMILY = "Noto Sans JP"

# 画面の割り付け（x0, y0, x1, y1）。文字は上の帯だけ・画面（公式ページ）はその下
TEXT_ZONE = (64, 178, 950, 418)
SOURCE_Y = 430
PANEL = (36, 478, 1044, 1888)
PW, PH = PANEL[2] - PANEL[0], PANEL[3] - PANEL[1]
RADIUS = 40
FOCUS = (PW * 0.5, PH * 0.34)  # 注目する文字を置くパネル内の位置（下の UI に隠れない高さ）

# ページの撮影
CAPTURE_W = 540  # CSS px（縦の画面にちょうど入るスマホ〜小型タブレットの幅）
CAPTURE_DPR = 4  # 2.2倍に寄っても画素が足りる（ページの画素 2160px 幅）
CAPTURE_MAX_H = 5200  # CSS px（OpenCV の上限 32767px 以内）
ZOOM_MAX = 2.2  # ページ幅いっぱいの倍率に対する最大ズーム
ZOOM_BLOCK = 1.35  # 行に寄るときの最小ズーム（ページ幅いっぱいだと文字が小さすぎる）

# 時間
LEAD = 0.15
GAP = 0.26
END_SEC = 1.3  # 締めのカード（1.5秒以下）
HOOK_TEXT_MAX = 1.7  # つかみの文字はこの時刻までに消す（ゲートは 2.0 秒）
TARGET_MAX = 30.5
TARGET_MIN = 20.5
SEP_TARGET_DB = 15.0  # 声と BGM の差

SITE = "ai-tech-express.jp"
BRAND = "サイソクニュース"

# 公式の製品・料金ページ（記事の主役がこれに当たるときだけ、発表ページと一緒に撮る）
PRODUCT_PAGES = [
    (re.compile(r"Claude", re.I), "https://claude.com/pricing"),
    (re.compile(r"ChatGPT|Codex", re.I), "https://chatgpt.com/pricing"),
    (re.compile(r"Gemini", re.I), "https://ai.google.dev/gemini-api/docs/pricing"),
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


def page_urls(article: dict) -> list[str]:
    """撮ってよいページ: 記事の出典が公式（v2 と同じ判定）なら出典、主役の製品の公式料金ページ。最大3つ"""
    urls = []
    if src := v2.official_page_url(article):
        urls.append(src)
    who = " ".join([article.get("title", ""), " ".join(article.get("tags") or []), " ".join((article.get("keywords") or [])[:4])])
    for pattern, url in PRODUCT_PAGES:
        if pattern.search(who) and url not in urls:
            urls.append(url)
    return urls[:3]


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
        if focus:  # 注目の枠は数字を含む行
            all_boxes = [r for r in rects if r]
            line = [r for r in all_boxes if abs(r[1] - focus[1]) < (focus[3] - focus[1]) * 0.6] or line
        return Target(pi, b, _union(line), needle, focus)
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
        "keyword": {"type": "STRING"},
        "sub": {"type": "STRING"},
        "speech": {"type": "STRING"},
        "block": {"type": "STRING"},
        "target": {"type": "STRING"},
    },
    "required": ["keyword", "sub", "speech", "block", "target"],
}
SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "hook": {
            "type": "OBJECT",
            "properties": {"text": {"type": "STRING"}, "payoff": {"type": "STRING"}, "speech": {"type": "STRING"}, "block": {"type": "STRING"}, "target": {"type": "STRING"}},
            "required": ["text", "payoff", "speech", "block", "target"],
        },
        "beats": {"type": "ARRAY", "items": _BEAT},
        "compare": {
            "type": "OBJECT",
            "properties": {"beat": {"type": "INTEGER"}, "base": {"type": "STRING"}, "subject": {"type": "STRING"}, "ratio": {"type": "STRING"}},
            "required": ["beat", "base", "subject", "ratio"],
        },
        "conclusion": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}, "speech": {"type": "STRING"}}, "required": ["text", "speech"]},
        "title": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "propertyOrdering": ["hook", "beats", "compare", "conclusion", "title", "summary", "hashtags"],
    "required": ["hook", "beats", "compare", "conclusion", "title", "summary", "hashtags"],
}


def script_system() -> str:
    import persona

    p = persona.PERSONA
    return f"""あなたはニュースサイト「{persona.BRAND}」の縦型ショート動画（20〜28秒）の構成作家。
動画は「画面で見せる型」: 公式ページの本物の画面をズームして証拠を見せる。画面に出す文字はキーワードだけで、話はナレーションが運ぶ。
見る人: {p['who']}
ナレーションの話し方: {p['voice']} 友だちの詳しい先輩が「これ知ってる？」と話すように、です・ます調で短く。

# 絶対の決まり
- 使ってよい事実は「資料」（記事）に書かれていることだけ。推測・補足・一般論の追加は禁止。
- 数字・価格・製品名は資料の表記どおり。資料に無い数字は書かない。
- 「初」「最大」「最高」「最速」「最安」「唯一」などの評価語は資料に同じ言葉があるときだけ。
- 煽らない（衝撃・ヤバい・革命・神・ついに・必見は禁止）。絵文字・記号の飾りは禁止。「ざっくり」は使わない。

# 構成
- hook（最初の1.5秒）: text は画面に大きく出す1行（12〜20字）。見る人が「自分ごと」になる問いかけか得。
  例の形:「Claudeの最上位、月200ドルの価値ある？」。payoff はその答えの手がかりになる数字のキーワード（8字以内・資料の表記どおり。例: 月200ドル）。
  speech は text を自然に読む1文（22字以内）。block / target は payoff の証拠になる画面の文字。
  できれば文字の大きい行（番号に「|大」が付いた行。料金表の大きな金額など）を選ぶ。最初の画面になるので見栄えが大事。
- beats: ちょうど4つ。1つ目は「見る人にとっての意味」（誰の仕事・お金にどう関係するか）を一言で。2〜4つ目は証拠を1つずつ。
  keyword: 画面に大きく出す言葉。2〜8字（例: Proの10倍、5時間制限なし）。文にしない。句読点を付けない。
  sub: 補足（14字以内。不要なら空）。
  speech: 読み上げ。1〜2文・合計38字以内。keyword の数字・名前を必ず含める。
  block: 下の「画面の文字」一覧の番号（例: 1-23）。target: その行の文字から、この beat の証拠になる部分を**一文字も変えずに**抜き出す（2〜40字。英語なら英語のまま）。
  証拠が無い beat（1つ目など）は、話に一番近い見出し・大きな文字の行（|大）を選ぶ。長い本文の段落は選ばない。
- **画面の文字は証拠の場所を選ぶためだけに使う。** 画面にあっても記事（資料）に無い事実・数字は、keyword・sub・speech に書かない。
- compare: 2つを倍率で比べられる事実が資料にあるときだけ。beat はそれを話す beat の番号（1〜4）、base は基準（例: Pro）、subject は比べる相手（例: Ultra）、
  ratio は資料の表記どおりの倍率（例: 10倍）。無ければ beat=0、他は空文字。
- conclusion: 最後の一言。text は16字以内で hook の問いに答える（例: 上限に困る人なら検討の価値）。speech は20〜32字。
- title: 40字以内・主役の名前を入れて事実をそのまま。summary: 説明欄の要約。「要約すると、」で始めて80〜120字。hashtags: 3〜4個（#付き）。

# 読み上げ（speech）
- 英語の製品名・社名はカタカナ（Claude→クロード、OpenAI→オープンエーアイ、Pro→プロ）。記号は言葉に（$200→200ドル、%→パーセント）。
- 数字はアラビア数字のまま（かなや漢数字にしない）。"""


def script_material(article: dict, pages: list[Page]) -> str:
    return v2.article_material(article) + "\n\n# 画面の文字（[番号] 文字）\n" + snippets(pages)


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
    return {
        "title": s.get("title", ""),
        "headline": hook.get("text", ""),
        "hook": {"caption": hook.get("text", ""), "speech": hook.get("speech", "")},
        "points": [{"caption": f"{b.get('keyword', '')} {b.get('sub', '')}".strip(), "speech": b.get("speech", "")} for b in beats],
        "use": {"caption": (s.get("conclusion") or {}).get("text", ""), "speech": (s.get("conclusion") or {}).get("speech", "")},
        "numbers": numbers,
        "hook_text": " ".join(x for x in [hook.get("payoff", ""), comp.get("base", "") if comp.get("beat") else "", comp.get("subject", "") if comp.get("beat") else ""] if x),
        "summary": s.get("summary", ""),
        "hashtags": s.get("hashtags", []),
    }


def _digits(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", gate.norm(text))


def script_problems(s: dict, article: dict, pages: list[Page]) -> list[str]:
    out = []
    hook, beats, concl = s.get("hook") or {}, s.get("beats") or [], s.get("conclusion") or {}
    if len(beats) != 4:
        out.append(f"beats はちょうど4つ（今は{len(beats)}）。")
    if not (6 <= len(hook.get("text", "")) <= 22):
        out.append(f"hook.text は12〜20字（今は{len(hook.get('text', ''))}字）。")
    if len(hook.get("payoff", "")) > 9:
        out.append("hook.payoff は8字以内。")
    if len(concl.get("text", "")) > 18:
        out.append("conclusion.text は16字以内。")
    for i, b in enumerate(beats, 1):
        kw = b.get("keyword", "")
        if not (2 <= len(kw) <= 10) or re.search(r"[、。！？]", kw):
            out.append(f"beat{i}.keyword は2〜8字で句読点なし（{kw}）。")
        if len(b.get("sub", "")) > 16:
            out.append(f"beat{i}.sub は14字以内。")
        if len(b.get("speech", "")) > 48:
            out.append(f"beat{i}.speech が長すぎます（38字以内）。")
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
            have = set(_digits(tgt.block.text))
            miss = [d for d in _digits(kw) if d not in have]
            if miss:
                out.append(f"{name} の数字 {', '.join(miss)} が証拠の行 [{tgt.block.id}] にありません。数字が書かれた行を選んでください。")
    comp = s.get("compare") or {}
    if comp.get("beat"):
        if not (1 <= int(comp["beat"]) <= 4) or not comp.get("ratio") or not comp.get("base") or not comp.get("subject"):
            out.append("compare は beat（1〜4）・base・subject・ratio をそろえるか、beat=0 に。")
        elif not re.search(r"\d", comp["ratio"]):
            out.append("compare.ratio は数字の倍率（例: 10倍）。")
    flat = flat_script(s)
    out += gate.number_context_problems(s, article)
    out += gate.fact_problems(flat, article) + gate.title_problems(flat, article, v2.main_name(article))
    out += v2.reading_problems(flat)
    return out


def write_script(article: dict, pages: list[Page]) -> tuple[dict, str]:
    import llm

    prompt = f"# 資料\n{script_material(article, pages)}\n\n上の資料だけを使って、決まりどおりの台本を JSON で書いてください。"
    feedback, last = "", ""
    tier = os.environ.get("V3_LLM_TIER", "quality")
    for attempt in range(1, 6):
        try:
            data, model = llm.generate_json(script_system(), prompt + feedback, SCRIPT_SCHEMA, temperature=0.5, max_output_tokens=4096, tier=tier)
        except RuntimeError:
            if tier == "bulk":
                raise
            tier = "bulk"  # 上位モデルの枠が尽きたら軽いモデルへ（事実の照合は同じ）
            log("   [v3] 台本: quality の枠が無いので bulk で書く")
            continue
        data["beats"] = [b for b in data.get("beats") or [] if isinstance(b, dict)][:4]
        problems = script_problems(data, article, pages)
        if not problems:
            return data, model
        last = "; ".join(problems)
        log(f"   [v3] 台本の書き直し（{attempt}）: {last[:300]}")
        feedback = "\n\n# 前回の台本の問題（すべて直す）\n" + "\n".join(f"- {p}" for p in problems) + "\n\n# 前回の台本\n" + json.dumps(data, ensure_ascii=False)
    raise RuntimeError(f"台本を作れませんでした: {last[:400]}")


# --------------------------------------------------------------------------- #
# 3. 声（VOICEVOX を最初に温め、行ごとに並列で合成）
# --------------------------------------------------------------------------- #


def warm_voicevox() -> threading.Thread | None:
    """VOICEVOX の最初の合成は遅いので、ページを撮っている間に1回合成しておく"""

    def run():
        try:
            tts = shorts.VoiceVox(shorts.DEFAULT_VOICEVOX, 1.2)
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
    index: int  # beat の番号（1〜4）・hook=0・conclusion=5
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


def narrate(script: dict, tts_kind: str, work: Path) -> Narration:
    lines = [Line("hook", 0, script["hook"]["speech"])] + [Line("beat", i, b["speech"]) for i, b in enumerate(script["beats"], 1)] + [Line("conclusion", 5, script["conclusion"]["speech"])]
    if tts_kind == "voicevox" and not wait_voicevox():
        raise RuntimeError("VOICEVOX に繋がりません")
    speed = 1.22
    for _ in range(5):
        tts = v2.FakeTTS(0.135 * 1.22 / speed) if tts_kind == "fake" else v2.make_tts(tts_kind, speed)

        def synth(i: int) -> np.ndarray:
            raw, dst = work / f"l{i}.src.wav", work / f"l{i}.wav"
            tts.synth(lines[i].speech, raw)
            v2._to_48k_trimmed(raw, dst)
            return v2._read_wav(dst)

        with ThreadPoolExecutor(max_workers=4) as pool:
            clips = list(pool.map(synth, range(len(lines))))
        t = LEAD
        for line, clip in zip(lines, clips):
            line.start, line.end = t, t + len(clip) / SR
            t = line.end + GAP
        end_start = lines[-1].end + 0.32
        total = end_start + END_SEC
        if total < TARGET_MIN and speed > 1.06:
            speed -= 0.08  # 短すぎる（20秒未満）ときは少しゆっくり読む
            log(f"   [v3] {total:.1f}秒で短い → 速さ {speed:.2f} で読み直し")
            continue
        if total <= TARGET_MAX or speed >= 1.45:
            break
        speed += 0.08
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
    """自作の BGM（108BPM・明るめのコード進行・プラック＋パッド＋ベース＋軽いキックとハット）。外部音源なし＝権利の心配なし"""
    rng = np.random.default_rng(seed)
    bpm = 108
    beat = 60 / bpm
    bar = 4 * beat
    n = int((total + 1) * SR)
    out = np.zeros(n, dtype=np.float32)
    prog = [[53, 57, 60, 64], [55, 59, 62, 67], [52, 55, 59, 62], [57, 60, 64, 67]]  # Fmaj7 G Em7 Am7
    bars = int(math.ceil(total / bar)) + 1

    def tone(freq: float, dur: float, decay: float, harm=(1.0, 0.35, 0.12), attack=0.004) -> np.ndarray:
        t = np.arange(int(dur * SR)) / SR
        env = np.minimum(1, t / attack) * np.exp(-t / decay)
        return (env * sum(a * np.sin(2 * np.pi * freq * (k + 1) * t) for k, a in enumerate(harm))).astype(np.float32)

    pattern = [0, 2, 1, 3, 2, 1, 3, 1]
    for b in range(bars):
        chord = prog[b % 4]
        t0 = b * bar
        # パッド（ゆっくり立ち上がる和音）
        t = np.arange(int(bar * SR)) / SR
        env = np.minimum(1, t / 0.5) * np.minimum(1, (bar - t) / 0.4)
        pad = sum(np.sin(2 * np.pi * _midi(m) * d * t) + 0.25 * np.sin(4 * np.pi * _midi(m) * d * t) for m in chord for d in (0.998, 1.002))
        _add(out, (0.035 * env * pad).astype(np.float32), t0)
        # プラック（16分のアルペジオ）
        for k in range(16):
            note = chord[pattern[k % 8]] + 12
            vel = 0.13 if k % 4 == 0 else 0.085
            _add(out, vel * tone(_midi(note), 0.5, 0.14), t0 + k * beat / 4 + (0.012 if k % 2 else 0))
        # ベース
        for at in (0, 1.5, 2, 3.5):
            _add(out, 0.30 * tone(_midi(chord[0] - 12), 0.6, 0.32, harm=(1.0, 0.3)), t0 + at * beat)
        # キック（1・3拍）とハット（裏拍）
        for at in (0, 2):
            tt = np.arange(int(0.35 * SR)) / SR
            f = 46 + 70 * np.exp(-tt / 0.035)
            _add(out, (0.42 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt / 0.16)).astype(np.float32), t0 + at * beat)
        for k in range(8):
            if k % 2:
                tt = np.arange(int(0.06 * SR)) / SR
                _add(out, (0.05 * rng.standard_normal(len(tt)) * np.exp(-tt / 0.018)).astype(np.float32), t0 + k * beat / 2)
    out = fft_filter(out, low=7000, high=40, dip=(2500, 1.2, -5))  # 声の帯域（1〜4kHz）を空ける
    out = out[: int(total * SR)]
    t = np.arange(len(out)) / SR
    out *= np.minimum(1, t / 0.25) * np.minimum(1, np.maximum(0, (total - t) / 0.9))
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
        if e["kind"] in ("move", "cut", "end"):
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


def frame_rect(page: Page, page_no: int, rect: tuple, frac: float, zmax: float, zmin: float = 1.0) -> Cam:
    """rect の幅が画面幅の frac になる倍率（ページ幅いっぱい〜zmax 倍）で、rect を注目位置に置く"""
    s0 = fit_s(page)
    rw, rh = max(rect[2] - rect[0], 1), max(rect[3] - rect[1], 1)
    s = min(frac * PW / rw, 0.22 * PH / rh)
    s = min(max(s, s0 * zmin), s0 * zmax)
    return clamp_cam(page, page_no, (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2, s)


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


@dataclass
class Click:
    t: float
    page: int
    x: float
    y: float


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
    enter: bool = True
    delay: float = 0.0

    def bbox(self) -> list[float]:
        a = self.sprite[..., 3]
        ys, xs = np.nonzero(a > 8)
        if not len(xs):
            return [self.x, self.y, self.x, self.y]
        return [float(self.x + xs.min()), float(self.y + ys.min()), float(self.x + xs.max() + 1), float(self.y + ys.max() + 1)]


RENDERED: list[str] = []  # 動画に描いた文字すべて（数字が記事と合っているかをゲートで見る。途中の数字は描かない）


def render_text(segments: list[tuple[str, str, tuple]], size: int) -> np.ndarray:
    """1行の文字（[(文字, 書体, 色)]）を RGBA で。前後の余白は切り落とす"""
    RENDERED.append("".join(t for t, _, _ in segments))
    fonts = [shorts.font(face, size) for _, face, _ in segments]
    width = int(sum(f.getlength(t) for (t, _, _), f in zip(segments, fonts))) + size
    img = Image.new("RGBA", (width + 8, int(size * 1.6)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 4.0
    for (t, _, color), f in zip(segments, fonts):
        d.text((x, size * 0.15), t, font=f, fill=color + (255,))
        x += f.getlength(t)
    arr = np.asarray(img)
    ys, xs = np.nonzero(arr[..., 3] > 0)
    if not len(xs):
        return np.zeros((1, 1, 4), dtype=np.uint8)
    return np.ascontiguousarray(arr[max(0, ys.min() - 2) : ys.max() + 3, max(0, xs.min() - 2) : xs.max() + 3])


NUM_SPLIT = re.compile(r"([$＄]?\d[\d,，.．]*(?:万|億|兆)?\d*(?:円|ドル|%|％|倍|人|件|社|年|月|日|時間|分|秒|GB|TB|万人|トークン)?)")


def accent_segments(text: str, face: str = "black", base=WHITE) -> list[tuple[str, str, tuple]]:
    out = []
    for i, part in enumerate(NUM_SPLIT.split(text)):
        if part:
            out.append((part, face, ACCENT if i % 2 else base))
    return out


def fit_line(text: str, face: str, sizes: range, width: int, color=WHITE, accent: bool = True) -> np.ndarray:
    for size in sizes:
        spr = render_text(accent_segments(text, face, color) if accent else [(text, face, color)], size)
        if spr.shape[1] <= width:
            return spr
    raise RuntimeError(f"文字が入りません: {text}")


def stack(sprites: list[np.ndarray], gap: int) -> np.ndarray:
    w = max(s.shape[1] for s in sprites)
    h = sum(s.shape[0] for s in sprites) + gap * (len(sprites) - 1)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    y = 0
    for s in sprites:
        out[y : y + s.shape[0], : s.shape[1]] = s
        y += s.shape[0] + gap
    return out


def wrapped(text: str, face: str, sizes: range, width: int, rows_max: int) -> np.ndarray:
    for size in sizes:
        rows = v2.wrap_jp(text, shorts.font(face, size), width - 8)
        if len(rows) <= rows_max:
            sprites = [render_text(accent_segments(r, face), size) for r in rows]
            if all(s.shape[1] <= width for s in sprites):
                return stack(sprites, int(size * 0.18))
    raise RuntimeError(f"文字が入りません: {text}")


def place_in_zone(sprite: np.ndarray) -> tuple[int, int]:
    zx0, zy0, zx1, zy1 = TEXT_ZONE
    return zx0, zy0 + max(0, (zy1 - zy0 - sprite.shape[0]) // 2)


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
    events: list[dict]
    focus: list[dict]
    end_card: np.ndarray
    credits: list[str]


def _source_label(page: Page) -> str:
    return f"公式ページ：{page.host}"


def make_plan(article: dict, script: dict, narr: Narration, pages: list[Page]) -> Plan:
    lines = narr.lines
    hook = script["hook"]
    beats = script["beats"]
    items = [hook] + beats
    kws = [hook.get("payoff") or ""] + [b.get("keyword", "") for b in beats]
    targets = [find_target(pages, it.get("target", ""), it.get("block", ""), keyword=k) for it, k in zip(items, kws)]
    if not targets[0]:
        raise RuntimeError("つかみの証拠の場所がページに見つかりません")
    for i in range(1, len(targets)):  # 見つからない beat は1つ前の場所を使う（ゲートで事実は見ている）
        targets[i] = targets[i] or targets[i - 1]
    comp = script.get("compare") or {}
    comp_beat = int(comp.get("beat") or 0) if comp.get("ratio") else 0

    moves: list[Move] = []
    spots: list[Spot] = []
    clicks: list[Click] = []
    charts: list[Chart] = []
    events: list[dict] = []

    def ev(t: float, kind: str, note: str = "") -> None:
        events.append({"t": round(max(0.0, t), 3), "kind": kind, "note": note})

    # つかみ: ページ幅いっぱい → 0.25秒から payoff の数字へ寄る（1秒で数字が大きく見える）
    t0 = targets[0]
    p0 = pages[t0.page]
    wide_rect = t0.block.rect if t0.block.rect[2] - t0.block.rect[0] > 0.5 * p0.width else t0.rect
    first = frame_rect(p0, t0.page, (wide_rect[0], t0.rect[1], wide_rect[2], t0.rect[3]), 0.96, 1.5, zmin=1.0 if wide_rect is t0.block.rect else 1.2)
    hero = hero_block(p0)
    if t0.block.font_px < 22 and hero is not None and hero is not t0.block:
        # 数字が本文の中の小さな字なら、最初の画面はページの大きな見出し（公式ページだと一目でわかる）→ 数字へ
        first = frame_rect(p0, t0.page, hero.rect, 0.96, 1.4)
    hook_close = frame_rect(p0, t0.page, t0.zoom_rect, 0.34, ZOOM_MAX, zmin=1.6)
    moves.append(Move(0.18, 0.8, hook_close))
    ev(0.18, "move", "つかみ: 数字へ寄る")
    spots.append(Spot(0.7, lines[1].start - 0.08, t0.page, t0.rect))
    clicks.append(Click(0.92, t0.page, *click_point(t0)))
    ev(0.92, "click")

    for i, (beat, line) in enumerate(zip(beats, lines[1:5]), 1):
        tg = targets[i]
        page = pages[tg.page]
        nxt = lines[i + 1].start
        dur = nxt - line.start
        prev_page = moves[-1].cam.page
        a_t = line.start - 0.16
        cut = tg.page != prev_page
        block_cam = frame_rect(page, tg.page, tg.block.rect if tg.block.rect[2] - tg.block.rect[0] > 0.5 * page.width else tg.rect, 0.96, 1.6, zmin=1.0 if tg.block.rect[2] - tg.block.rect[0] > 0.5 * page.width else ZOOM_BLOCK)
        moves.append(Move(a_t, 0.45 if cut else 0.55, block_cam, cut=cut))
        ev(a_t, "cut" if cut else "move", f"beat{i}: 行へ")
        b_t = line.start + max(0.85, 0.42 * dur)
        if i == comp_beat:
            charts.append(Chart(b_t, nxt - 0.06, comp["base"], comp["subject"], comp["ratio"]))
            ev(b_t, "chart", "比較のグラフ")
        elif nxt - b_t > 0.9:
            zr = tg.zoom_rect
            moves.append(Move(b_t, 0.6, frame_rect(page, tg.page, zr, 0.36 if tg.focus else 0.7, ZOOM_MAX, zmin=1.7)))
            ev(b_t, "move", f"beat{i}: 数字へ寄る")
            spots.append(Spot(b_t + 0.3, nxt - 0.08, tg.page, tg.rect))
            clicks.append(Click(b_t + 0.55, tg.page, *click_point(tg)))
            ev(b_t + 0.55, "click")
        else:
            spots.append(Spot(a_t + 0.3, nxt - 0.08, tg.page, tg.rect))

    # 締め: 最初の画面（ページ幅いっぱい）へ引いて戻る
    concl = lines[5]
    moves.append(Move(concl.start - 0.16, 0.7, Camera._hold(first, 0, 0), cut=first.page != moves[-1].cam.page))
    ev(concl.start - 0.16, "move", "締め: 最初の画面へ戻る")
    c_t = concl.start + max(0.9, 0.45 * (concl.end - concl.start))
    if narr.end_start - c_t > 0.9:  # 最初の数字へもう一度寄る（最初と最後がつながる）
        moves.append(Move(c_t, 0.7, hook_close))
        ev(c_t, "move", "締め: 最初の数字へ")
        spots.append(Spot(c_t + 0.35, narr.end_start, t0.page, t0.rect))
    ev(narr.end_start, "end", "締めのカード")
    camera = Camera(first, moves)

    # 文字
    zone_w = TEXT_ZONE[2] - TEXT_ZONE[0]
    texts: list[TextItem] = []
    hook_spr = wrapped(hook["text"], "black", range(92, 70, -2), zone_w, 2)
    hook_end = lines[0].end if lines[0].end <= 2.0 else min(HOOK_TEXT_MAX, lines[0].end - 0.5)
    texts.append(TextItem("hook", hook["text"], hook_spr, *place_in_zone(hook_spr), 0.0, hook_end, enter=False))
    if hook.get("payoff") and lines[1].start - 0.06 - hook_end >= 0.5:
        pay = fit_line(hook["payoff"], "black", range(150, 96, -4), zone_w)
        texts.append(TextItem("keyword", hook["payoff"], pay, *place_in_zone(pay), hook_end, lines[1].start - 0.06))
        ev(hook_end, "pop", "つかみの数字")
    for i, (beat, line) in enumerate(zip(beats, lines[1:5]), 1):
        t_in, t_out = line.start - 0.1, lines[i + 1].start - 0.06
        kw = fit_line(beat["keyword"], "black", range(140, 88, -4), zone_w)
        if beat.get("sub"):
            sub = fit_line(beat["sub"], "bold", range(46, 34, -2), zone_w, color=SUBTLE, accent=False)
            block = stack([kw, sub], 20)
            x, y = place_in_zone(block)
            texts.append(TextItem("keyword", beat["keyword"], kw, x, y, t_in, t_out))
            texts.append(TextItem("sub", beat["sub"], sub, x, y + kw.shape[0] + 20, t_in, t_out, delay=0.1))
        else:
            texts.append(TextItem("keyword", beat["keyword"], kw, *place_in_zone(kw), t_in, t_out))
    concl_spr = wrapped(script["conclusion"]["text"], "black", range(92, 70, -2), zone_w, 2)
    texts.append(TextItem("conclusion", script["conclusion"]["text"], concl_spr, *place_in_zone(concl_spr), concl.start - 0.1, narr.end_start))
    # 出典（どのページか）: 小さく1行・ページが変わったら切り替える
    spans: list[list] = []
    for m in [Move(0.0, 0, first)] + camera.moves:
        if not spans or spans[-1][0] != m.cam.page:
            if spans:
                spans[-1][2] = m.t
            spans.append([m.cam.page, m.t, narr.end_start])
    for page_no, a, b in spans:
        label = _source_label(pages[page_no])
        spr = fit_line(label, "bold", range(28, 22, -2), zone_w, color=SUBTLE, accent=False)
        texts.append(TextItem("source", label, spr, TEXT_ZONE[0], SOURCE_Y, a, b, enter=a > 0))

    # 注目の場所（画面上の枠）: 字幕がかぶらないかをゲートで見る
    focus = []
    for sp in spots:
        cam = camera.at((sp.t0 + sp.t1) / 2)
        if cam.page == sp.page:
            x0, y0 = cam.map(sp.rect[0], sp.rect[1])
            x1, y1 = cam.map(sp.rect[2], sp.rect[3])
            focus.append({"t0": round(sp.t0, 3), "t1": round(sp.t1, 3), "bbox": [round(PANEL[0] + x0, 1), round(PANEL[1] + y0, 1), round(PANEL[0] + x1, 1), round(PANEL[1] + y1, 1)]})

    end_card, credits = make_end_card(narr, pages, texts)
    events.sort(key=lambda e: e["t"])
    return Plan(article, script, narr, pages, targets, camera, spots, clicks, charts, texts, events, focus, end_card, credits)


def stage_bg() -> np.ndarray:
    """背景: 濃い紺の単色に、上の方だけごく弱い光（線や帯は描かない）"""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx - W * 0.5) / (W * 0.9)) ** 2 + ((yy - 300) / 900) ** 2)
    glow = np.clip(1 - d, 0, 1) ** 2 * 14
    base = np.array(STAGE, dtype=np.float32)[None, None, :] + glow[..., None] * np.array([0.6, 0.8, 1.2], dtype=np.float32)
    return np.clip(base, 0, 255).astype(np.uint8)


def make_end_card(narr: Narration, pages: list[Page], texts: list[TextItem]) -> tuple[np.ndarray, list[str]]:
    """締め（1.3秒）: サイソクニュース・URL・声と出典のクレジット。小さく・静かに"""
    t0, t1 = narr.end_start, narr.total
    rows = [
        ("end_brand", BRAND, "black", 64, WHITE),
        ("end_url", SITE, "bold", 40, (199, 210, 227)),
    ]
    voice = narr.voice
    credits = [SITE]
    voice_text = f"音声：{voice['credit']}" if voice.get("credit") else "音声：試作の声（本番は VOICEVOX）"
    hosts = "・".join(dict.fromkeys(p.host for p in pages))
    small = [voice_text, f"出典：{hosts}（公式ページ）"]
    credits += small
    for text in small:
        rows.append(("end_credit", text, "bold", 28, SUBTLE))
    sprites = [(role, text, fit_line(text, face, range(size, size - 10, -2), TEXT_ZONE[2] - TEXT_ZONE[0], color=color, accent=False)) for role, text, face, size, color in rows]
    gaps = [26, 56, 14]
    total_h = sum(s.shape[0] for _, _, s in sprites) + sum(gaps[: len(sprites) - 1])
    y = (H - total_h) // 2 - 80
    card = stage_bg()
    for k, (role, text, spr) in enumerate(sprites):
        x = (W - spr.shape[1]) // 2
        blend(card, spr, x, y)
        texts.append(TextItem(role, text, spr, x, y, t0, t1, enter=False))
        y += spr.shape[0] + (gaps[k] if k < len(gaps) else 0)
    return card, credits


# --------------------------------------------------------------------------- #
# 6. 描画（OpenCV でページを動かす・1フレーム数ミリ秒）
# --------------------------------------------------------------------------- #


def blend(dst: np.ndarray, rgba: np.ndarray, x: int, y: int, alpha: float = 1.0) -> None:
    """dst（RGB）に rgba を (x, y) へ重ねる。はみ出す所は切る"""
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(dst.shape[1], x + w), min(dst.shape[0], y + h)
    if x0 >= x1 or y0 >= y1 or alpha <= 0:
        return
    src = rgba[y0 - y : y1 - y, x0 - x : x1 - x]
    a = src[..., 3:4].astype(np.uint16)
    if alpha < 1:
        a = (a * int(alpha * 256)) >> 8
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
    return np.asarray(big.resize((60, 76), Image.LANCZOS))


def data_bars(card: Image.Image, rows: list[tuple[int, int, int, tuple]], height: int = 64) -> None:
    """比較グラフの棒（データの棒だけ。飾りではない）: [(x, y, 長さ, 色)]"""
    d = ImageDraw.Draw(card)
    for x, y, length, color in rows:
        if length > 2:
            d.rounded_rectangle((x, y, x + max(length, int(height * 1.4)), y + height), radius=height // 2, fill=color)


class Renderer:
    def __init__(self, plan: Plan):
        self.plan = plan
        self.stage = stage_bg()
        self.corner = _mask_panel_corners()
        self.cursor = _cursor_sprite()
        self.levels = [self._pyramid(p.img) for p in plan.pages]
        self.bgs = [p.bg() for p in plan.pages]
        self.chart_cards = [self._chart_base(c) for c in plan.charts]
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

    BAR_H = 64

    def _chart_base(self, c: Chart) -> dict:
        """比較グラフのカード（白地・名前2つ・倍率）。棒は毎フレーム長さを変えて描く"""
        card = np.full((PH, PW, 3), 255, dtype=np.uint8)
        ink = (24, 28, 36)
        labels = []
        y = 340
        for name in (c.base, c.subject):
            spr = render_text([(name, "bold", ink)], 64)
            labels.append((name, spr, 84, y))
            y += 280
        # 倍率は記事の表記どおり（台本の compare.ratio）をそのまま。途中の数字（カウントアップ）は描かない
        value = render_text([(c.ratio, "black", ACCENT)], 170)
        return {"card": card, "labels": labels, "chart": c, "value": value}

    def chart_frame(self, idx: int, t: float) -> tuple[np.ndarray, list]:
        info = self.chart_cards[idx]
        c: Chart = info["chart"]
        card = Image.fromarray(info["card"].copy())
        ratio = float(re.sub(r"[^\d.]", "", gate.norm(c.ratio)) or 1)
        maxlen = PW - 84 - 84
        grow = ease_out((t - c.t0 - 0.2) / 0.9)
        base_len = max(self.BAR_H, int(maxlen / max(ratio, 1)))
        rows = []
        for k, (name, spr, x, y) in enumerate(info["labels"]):
            bar_y = y + spr.shape[0] + 28
            length = int(base_len * min(1, max(0.0, (t - c.t0) / 0.25))) if k == 0 else int(self.BAR_H + (maxlen - self.BAR_H) * grow)
            rows.append((x, bar_y, length, (208, 213, 221) if k == 0 else ACCENT))
        data_bars(card, rows, self.BAR_H)
        arr = np.asarray(card).copy()
        for name, spr, x, y in info["labels"]:
            blend(arr, spr, x, y)
        # 倍率（長い棒の右上）。棒が伸び切る少し前にふわっと出す
        spr = info["value"]
        _, lab, _, sy = info["labels"][1]
        k = ease_out((t - c.t0 - 0.55) / 0.3)
        blend(arr, spr, 84 + maxlen - spr.shape[1], sy + lab.shape[0] - spr.shape[0] + int((1 - k) * 20), k)
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
        # 次の場面（カメラの切り替え・締め）で消す
        for m in self.plan.camera.moves:
            if m.cut and m.t > clicks[-1].t and t > m.t - 0.15:
                alpha = 0.0
        concl = self.plan.narr.lines[5].start - 0.16
        if t > concl:
            alpha *= max(0.0, 1 - (t - concl) / 0.2)
        x, y = cam.map(px, py)
        return x, y, alpha, since

    def panel(self, t: float) -> np.ndarray:
        cam = self.plan.camera.at(t)
        img = self.motion_blurred(t, cam)
        # 注目（周りを少し暗く）
        for sp in self.plan.spots:
            if sp.page != cam.page or not (sp.t0 <= t <= sp.t1 + 0.25):
                continue
            a = min(1.0, (t - sp.t0) / 0.3) * min(1.0, max(0.0, (sp.t1 + 0.25 - t) / 0.25))
            if a <= 0:
                continue
            x0, y0 = cam.map(sp.rect[0], sp.rect[1])
            x1, y1 = cam.map(sp.rect[2], sp.rect[3])
            mask = _mask_spot((x0, y0, x1, y1))
            dim = 0.30 * a
            f = (255 - dim * (255 - mask.astype(np.float32))).astype(np.uint16)
            img = ((img.astype(np.uint16) * f[..., None]) >> 8).astype(np.uint8)
        # カーソルとクリックの波紋
        cur = self.cursor_at(t, cam)
        if cur:
            x, y, alpha, since = cur
            if 0 <= since < 0.45:
                k = since / 0.45
                self._ripple(img, x, y, 14 + 46 * ease_out(k), 0.45 * (1 - k))
            spr = self.cursor
            if 0 <= since < 0.16:
                sc = 1 - 0.18 * math.sin(math.pi * since / 0.16)
                h2, w2 = int(spr.shape[0] * sc), int(spr.shape[1] * sc)
                spr = cv2.resize(spr, (w2, h2), interpolation=cv2.INTER_AREA) if cv2 is not None else np.asarray(Image.fromarray(spr).resize((w2, h2)))
            if alpha > 0:
                blend(img, spr, int(x - 4), int(y - 4), alpha)
        # 比較のグラフ
        for k, ch in enumerate(self.plan.charts):
            if ch.t0 <= t <= ch.t1 + 0.2:
                a = min(1.0, (t - ch.t0) / 0.25) * min(1.0, max(0.0, (ch.t1 + 0.2 - t) / 0.2))
                card, _ = self.chart_frame(k, t)
                img = ((img.astype(np.uint16) * int((1 - a) * 256) + card.astype(np.uint16) * int(a * 256)) >> 8).astype(np.uint8)
        return img

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
            n = int(min(7, 2 + zoom_px // 4))
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
        spr[..., :3] = ACCENT
        spr[..., 3] = a.astype(np.uint8)
        blend(img, spr, x0, y0)

    def frame(self, i: int) -> np.ndarray:
        t = i / FPS
        out_k = ease((t - self.end_start) / 0.3) if t >= self.end_start else 0.0
        if out_k >= 1:
            return self.plan.end_card
        frame = self.stage.copy()
        panel = self.panel(min(t, self.end_start))
        # 角を丸くして貼る（角の4か所だけ背景と混ぜる）
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
            if item.role.startswith("end_"):
                continue
            if not (item.t0 - 1e-6 <= t < item.t1 + 0.18):
                continue
            age = t - item.t0 - item.delay
            if age < 0:
                continue
            k_in = ease_out(age / 0.32) if item.enter else 1.0
            k_out = 1 - ease((t - item.t1) / 0.18) if t > item.t1 else 1.0
            alpha = k_in * k_out
            dy = int((1 - k_in) * 26 - (1 - k_out) * 10)
            blend(frame, item.sprite, item.x, item.y + dy, alpha)
        if out_k > 0:
            frame = ((frame.astype(np.uint16) * int((1 - out_k) * 256) + self.plan.end_card.astype(np.uint16) * int(out_k * 256)) >> 8).astype(np.uint8)
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
        "-c:v", "libx264", "-preset", preset, "-crf", "20", "-maxrate", "8M", "-bufsize", "16M",
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


def text_records(plan: Plan) -> list[dict]:
    out = []
    for item in plan.texts:
        bbox = item.bbox()
        box = list(TEXT_ZONE) if item.role in ("hook", "keyword", "sub", "conclusion") else [0, 0, W, H]
        if item.role == "source":
            box = [TEXT_ZONE[0], SOURCE_Y - 4, TEXT_ZONE[2], PANEL[1]]
        size = {"hook": 70, "keyword": 88, "sub": 34, "conclusion": 70, "source": 22, "end_brand": 54, "end_url": 30, "end_credit": 22}.get(item.role, 22)
        color = ACCENT if item.role == "keyword" else (SUBTLE if item.role in ("sub", "source", "end_credit") else WHITE)
        out.append(
            {
                "role": item.role,
                "text": item.text,
                "bbox": [round(v, 1) for v in bbox],
                "box": box,
                "size": int(item.sprite.shape[0] * 0.75),
                "min_size": size * 3 // 4,
                "contrast": round(min(gate.contrast(color, STAGE), gate.contrast(WHITE, STAGE)), 2),
                "missing": gate.missing_glyphs(item.text, shorts.font("black" if item.role in ("hook", "keyword", "conclusion", "end_brand") else "bold", 40)),
                "safe": True,
                "t0": round(item.t0, 3),
                "t1": round(item.t1, 3),
            }
        )
    return out


def units_for_gate(plan: Plan) -> list[dict]:
    """字幕（画面のキーワード）とナレーションの対応（v2 のゲートと同じ形）"""
    by_line: dict[int, list] = {}
    order = {"hook": 0, "conclusion": 5}
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
    lines += ["", " ".join(tags)]
    return {"title": title, "description": "\n".join(lines), "hashtags": tags, "url": url}


def contact_sheet(mp4: Path, plan: Plan, out: Path) -> None:
    """場面が変わる所の直後を12コマ並べる（実際の mp4 から取り出す）"""
    marks = [(0.0, "0.0s 最初の画面")]
    for e in plan.events:
        if e["kind"] in ("move", "cut", "chart", "pop"):
            marks.append((e["t"] + (0.75 if e["kind"] in ("move", "cut") else 0.6), f"{e['t']:.1f}s {e['note'] or e['kind']}"))
    marks.append((plan.narr.end_start + 0.6, f"{plan.narr.end_start:.1f}s 締め"))
    marks = sorted({round(t, 2): (t, lab) for t, lab in marks}.values())[:14]
    tw, th = 216, 384
    cols = 7
    rows = math.ceil(len(marks) / cols)
    sheet = Image.new("RGB", (cols * (tw + 10) + 10, rows * (th + 46) + 10), (236, 239, 244))
    d = ImageDraw.Draw(sheet)
    f = shorts.font("bold", 17)
    for i, (t, label) in enumerate(marks):
        raw = subprocess.run(
            [shorts.ffmpeg_bin(), "-loglevel", "error", "-ss", f"{min(t, plan.narr.total - 0.05):.2f}", "-i", str(mp4), "-frames:v", "1", "-vf", f"scale={tw}:{th}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True,
        ).stdout  # fmt: skip
        frame = Image.frombytes("RGB", (tw, th), raw[: tw * th * 3])
        x, y = 10 + (i % cols) * (tw + 10), 10 + (i // cols) * (th + 46)
        sheet.paste(frame, (x, y))
        d.text((x, y + th + 6), label[:16], font=f, fill=(20, 26, 40))
    sheet.save(out)


def load_script(slug: str, article: dict, pages: list[Page], out_dir: Path, reuse: bool) -> dict:
    path = out_dir / f"{slug}.v3script.json"
    if reuse and path.exists():
        return json.loads(path.read_text("utf-8"))
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
) -> dict:
    """1本作って品質ゲートにかける。戻り値は report（gate.ok で合否）。例外は投げず report に残す"""
    timer = Timer()
    RENDERED.clear()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{slug}__v3"
    mp4 = out_dir / f"{name}.mp4"
    report: dict = {"slug": slug, "version": VERSION, "design": "v3", "design_name": "画面で見せる型", "errors": []}
    plan = None
    try:
        a = article or shorts.load_article(slug)
        warm = warm_voicevox() if tts == "voicevox" else None
        if pages is None:
            t = time.time()
            urls = page_urls(a)
            if not urls:
                raise RuntimeError("撮ってよい公式ページがありません（ニュースサイトが出典の記事は v3 の対象外）")
            pages = capture_pages(urls)
            timer.mark("capture", t)
            if not pages:
                raise RuntimeError("公式ページを撮れませんでした")
        report["pages"] = [{"url": p.url, "host": p.host, "title": p.title, "seconds": p.seconds, "size": [p.width, p.height], "blocks": len(p.blocks)} for p in pages]
        t = time.time()
        script = script or load_script(slug, a, pages, out_dir, reuse)
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
                "texts": text_records(plan),
                "credits_shown": plan.credits,
                "voice": plan.narr.voice,
                "image_credit": "",
                "safe": list(gate.SAFE),
                "fps": fps,
                "events": plan.events,
                "focus": plan.focus,
                "fonts": [FONT_FAMILY],
                "rendered_strings": sorted(set(RENDERED)),
                "charts": [{"t0": round(c.t0, 3), "t1": round(c.t1, 3), "base": c.base, "subject": c.subject, "ratio": c.ratio} for c in plan.charts],
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


def main() -> None:
    parser = argparse.ArgumentParser(description="SNS 用ショート動画 v3（画面で見せる型）")
    parser.add_argument("slugs", nargs="+")
    parser.add_argument("--out", default=str(ROOT / "out" / "shorts_v3"))
    parser.add_argument("--tts", default=os.environ.get("SHORTS_TTS", "voicevox"), choices=["auto", "voicevox", "edge", "fake"])
    parser.add_argument("--reuse-script", action="store_true")
    parser.add_argument("--profile", choices=list(gate.PROFILES))
    parser.add_argument("--no-bgm", action="store_true")
    args = parser.parse_args()
    import llm

    llm._load_env_local()
    failed = 0
    for slug in args.slugs:
        r = make_video(slug, Path(args.out), args.tts, args.reuse_script, args.profile, bgm=not args.no_bgm)
        failed += 0 if r["gate"]["ok"] else 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
