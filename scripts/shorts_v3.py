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
import random
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
# オーナーが承認した基本の形（2026-11-04「基本的な構成はいい感じ」）: 左に小さな見出し＋大きな見出し（文字に合わせたマーカー）、
# 右に公式ページの本物の画面（選択の青・英語なら日本語の吹き出し）。中身に合わせた型（video_templates）のカードは
# 場面の一部で右の画面に出るだけで、この形を置き換えない（ゲート: カードは動画の半分まで）
BASE_LAYOUT = "screen-left-headline"

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
  const range = document.createRange(); let n; let budget = 20000; const refs = [];
  while ((n = walker.nextNode()) && budget > 0) {
    const t = n.nodeValue; if (!t || !t.trim()) continue;
    const el = n.parentElement; if (!el || el.closest('script,style,noscript,svg,button,nav,footer')) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.2) continue;
    // 閉じた <details>（「AI の要約」など）・祖先が非表示の文字は画面に見えないので使わない（位置が別の文と重なって、選択の青がずれる）
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true, contentVisibilityAuto: true })) continue;
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
    refs.push(n);
  }
  window.__v3refs = refs;  // OCCLUDE_JS が同じ順で「本当に見えているか」を確かめる
  const out = [];
  for (const [el, id] of blocks) { const r = el.getBoundingClientRect(); out[id] = [r.left + scrollX, r.top + scrollY, r.right + scrollX, r.bottom + scrollY]; }
  return { blocks: out, nodes, height: document.documentElement.scrollHeight, title: document.title };
}"""


# 2026-11-04 オーナー「選択の青が文字からずれる・別の文に付く」: 閉じたメニュー・横に流れる帯の外・別の要素の下に隠れた文字も
# 位置は測れてしまう（その位置に写っているのは別の文字）。撮った画面と同じ配置のまま、文字の点（1つのノードの最初・真ん中・最後の字）で
# elementsFromPoint を引き、その文字の要素が一番上（上にあるのは透明な要素だけ）のときだけ「見えている」とする。見えない文字は証拠に使わない
OCCLUDE_JS = r"""async (maxY) => {
  const refs = window.__v3refs || [];
  const opaque = (e) => {
    if (/^(IMG|VIDEO|CANVAS|IFRAME|PICTURE|svg)$/i.test(e.tagName)) return true;
    const cs = getComputedStyle(e);
    if (cs.backgroundImage && cs.backgroundImage !== 'none') return true;
    const m = (cs.backgroundColor || '').match(/rgba?\(([^)]+)\)/);
    if (!m) return false;
    const p = m[1].split(',').map(parseFloat);
    return p.length < 4 || p[3] > 0.5;
  };
  // 祖先の不透明度の積・visibility（透明な固定の目次などは当たり判定には出るが、画面には写っていない）
  const shown = (el) => { let o = 1; for (let e = el; e && e.nodeType === 1; e = e.parentElement) { const cs = getComputedStyle(e); if (cs.visibility === 'hidden' || cs.display === 'none') return 0; o *= parseFloat(cs.opacity); } return o; };
  const range = document.createRange();
  const faint = refs.map((n) => !n.parentElement || shown(n.parentElement) < 0.5);
  const picks = refs.map((n) => { const t = n.nodeValue || ''; const idx = []; for (let i = 0; i < t.length; i++) if (t[i].trim()) idx.push(i); return idx.length ? [...new Set([idx[0], idx[idx.length >> 1], idx[idx.length - 1]])] : []; });
  const votes = refs.map(() => [0, 0]); const seen = refs.map(() => new Set());
  const H = Math.min(maxY, document.documentElement.scrollHeight); const vh = innerHeight;
  for (let y = 0; y < H; y += vh - 60) {
    window.scrollTo(0, y); await new Promise((r) => setTimeout(r, 50));
    refs.forEach((n, k) => {
      const el = n.parentElement; if (!el) return;
      if (getComputedStyle(el).pointerEvents === 'none') return;  // 当たり判定が無い文字は確かめられない（見えているとみなす）
      for (const i of picks[k]) {
        if (seen[k].has(i)) continue;
        range.setStart(n, i); range.setEnd(n, i + 1); const r = range.getBoundingClientRect();
        const cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
        if (r.width <= 0 || cy < 2 || cy > vh - 2 || cx < 1 || cx > innerWidth - 1) continue;
        seen[k].add(i);
        const stack = document.elementsFromPoint(cx, cy);
        const at = stack.findIndex((e) => e === el || el.contains(e));
        const ok = at >= 0 && stack.slice(0, at).every((e) => !opaque(e));
        votes[k][ok ? 0 : 1]++;
      }
    });
  }
  window.scrollTo(0, 0);
  return votes.map(([a, b], k) => !faint[k] && a >= b);
}"""


def layout_same(a: dict, b: dict, tol: float = 2.0) -> bool:
    """2回測った文字の配置が同じか（高さと、最初の200個のまとまりの位置が tol px 以内）"""
    if abs(a.get("height", 0) - b.get("height", 0)) > tol or len(a.get("blocks") or []) != len(b.get("blocks") or []):
        return False
    for ra, rb in list(zip(a["blocks"], b["blocks"]))[:200]:
        if ra and rb and any(abs(x - y) > tol for x, y in zip(ra, rb)):
            return False
    return True


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
    own: bool = False  # サイソクニュース自身の記事ページ（公式ページが撮れないときの画面。事実の出どころには数えない）

    @property
    def width(self) -> int:
        return self.img.shape[1]

    @property
    def height(self) -> int:
        return self.img.shape[0]

    def bg(self) -> tuple:
        """ページの地の色: 左右の端の細い帯（ページ全体の高さ）の中央値。2026-11-04: 左上の角だけを見ると、
        黒い見出しの帯があるページ（developer.nvidia.com）で地が黒になり、画面の左右に黒い帯が出ていた"""
        k = max(2, min(24, self.width // 40))
        step = max(1, self.height // 2000)
        patch = np.concatenate([self.img[::step, :k], self.img[::step, -k:]], axis=1).reshape(-1, 3)
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
            CAPTURE_FAILS.append(f"{urllib.parse.urlparse(url).netloc}: HTTP {resp.status if resp else '?'}")
            return None
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        title = await page.title()
        if re.search(r"just a moment|attention required|access denied", title, re.I):
            log(f"   [v3] ボット確認の画面なので使わない: {url}")
            CAPTURE_FAILS.append(f"{urllib.parse.urlparse(url).netloc}: ボット確認")
            return None
        await page.evaluate(SCROLL_JS, CAPTURE_MAX_H)
        await page.evaluate(HIDE_JS)
        if urllib.parse.urlparse(url).netloc.removeprefix("www.") == SITE:
            await page.evaluate(OWN_HIDE_JS)  # 自分の記事: 広告・提携リンクの箱は隠す（画面の証拠は記事の本文・表・要点だけ）
        # 読み込みに失敗した・まだのフォントをもう一度読む（失敗すると本文が明朝の代わりの字になる）
        await page.evaluate("Promise.race([Promise.all([...document.fonts].filter(f => f.status !== 'loaded').map(f => f.load().catch(() => null))), new Promise(r => setTimeout(r, 4000))]).then(() => true)")
        await page.evaluate("document.fonts.ready.then(() => true)")
        # 普通の画面の高さのまま測り、上から1画面ずつ撮ってつなぐ（Chrome は 16384px を超える1枚を正しく撮れない。
        # 画面の高さを変えると配置が変わるので、測るときと撮るときの配置を同じにする）
        await page.evaluate("document.documentElement.style.scrollBehavior = 'auto'; window.scrollTo(0, 0)")
        await page.wait_for_timeout(150)
        # 撮っている間に配置が動く（画像の読み込みなど）と、文字の位置と写真がずれる（選択の青が別の文に付く）。
        # 撮る前と後で測り直し、動いていたらもう一度撮る（最大2回）。最後は「撮った直後に測った位置」を使う
        data = await page.evaluate(TEXT_JS, CAPTURE_MAX_H)
        for _round in range(3):
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
            await page.evaluate("window.scrollTo(0, 0)")
            after = await page.evaluate(TEXT_JS, CAPTURE_MAX_H)
            if layout_same(data, after):
                break
            log(f"   [v3] 撮っている間にページの配置が動いたので撮り直す（{_round + 1}）: {url}")
            data = after
        try:
            visible = await page.evaluate(OCCLUDE_JS, CAPTURE_MAX_H)
        except Exception as error:  # noqa: BLE001 - 確かめられないときは今までどおり
            visible = None
            log(f"   [v3] 文字が見えているかを確かめられない: {type(error).__name__}")
        if visible is not None and len(visible) == len(data["nodes"]):
            hidden = sum(1 for v in visible if not v)
            if hidden:
                log(f"   [v3] 隠れている文字のまとまり {hidden}個は証拠に使わない: {url}")
            data["nodes"] = [nd for nd, v in zip(data["nodes"], visible) if v]
    except Exception as error:  # noqa: BLE001
        log(f"   [v3] 撮影に失敗: {url}: {type(error).__name__}: {str(error)[:120]}")
        CAPTURE_FAILS.append(f"{urllib.parse.urlparse(url).netloc}: {type(error).__name__}")
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
    return Page(url, host, img, blocks, title, round(time.time() - started, 1), own=host == SITE)


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


CAPTURE_FAILS: list[str] = []  # 撮れなかったページと理由（動画を作れなかった理由の記録に使う）

OWN_HIDE_JS = r"""() => {
  const kill = (el) => el && el.style.setProperty('display', 'none', 'important');
  document.querySelectorAll('[data-ad-position], ins.adsbygoogle, iframe, aside, [data-aff-rank], a[rel~="sponsored"]').forEach((el) => {
    kill(el);
    const box = el.closest('section, div');
    if (box && /^(広告|PR)/.test((box.innerText || '').trim())) kill(box);
  });
  for (const el of document.querySelectorAll('span, p')) if (/^本ページはプロモーションを含みます/.test((el.innerText || '').trim())) kill(el);
}"""

# 公式ページがボット対策などで撮れないとき、同じ会社の撮れる公式ページ（ボット対策は回避しない）
COMPANY_FALLBACKS = [
    (re.compile(r"(^|\.)(openai\.com|chatgpt\.com)$"), ["https://developers.openai.com/api/docs/changelog", "https://developers.openai.com/api/docs/pricing"]),
    (re.compile(r"(^|\.)(anthropic\.com|claude\.ai)$"), ["https://claude.com/pricing", "https://platform.claude.com/docs/en/release-notes/overview"]),
]


def fallback_urls(article: dict, tried: list[str]) -> list[str]:
    """撮れなかった公式ページの代わり: 同じ会社の撮れる公式ページ（開発者向けの資料・料金表など）"""
    out: list[str] = []
    hosts = {urllib.parse.urlparse(u).netloc.removeprefix("www.") for u in tried}
    topic = f"{article.get('title', '')} {' '.join(article.get('tags') or [])}"
    for pattern, urls in COMPANY_FALLBACKS:
        if any(pattern.search(h) for h in hosts):
            out += urls
            if re.search(r"Codex", topic, re.I) and "openai" in pattern.pattern:
                out.append("https://developers.openai.com/codex")
    return [u for u in dict.fromkeys(out) if u not in tried]


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


# 台本が場面ごとに画面の道具（カード）を選ぶ（2026-11-04・オーナーの承認待ち。既定 off＝今までと同じ）。
# on のとき: 台本に cards（2列・前→今・○×・数字・手順）。カードの文字・数字は記事と照合。ゲートは道具2種類以上と「青と訳の無い画面は4秒まで」
AUTO_CARDS_ENV = "SNS_VIDEO_AUTO_CARDS"
AUTO_CARD_MODES = ("timeline", "checklist", "figures", "specs")  # 2列・前→今＝timeline、○×＝checklist、数字＝figures、手順＝specs


def auto_cards_on(env: dict | None = None) -> bool:
    return (os.environ if env is None else env).get(AUTO_CARDS_ENV, "off") == "on"


_CARD = {
    "type": "OBJECT",
    "properties": {
        "beat": {"type": "INTEGER"},
        "mode": {"type": "STRING"},
        "title": {"type": "STRING"},
        "rows": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"label": {"type": "STRING"}, "text": {"type": "STRING"}, "value": {"type": "STRING"}},
                                            "required": ["label", "text", "value"]}},
    },
    "required": ["beat", "mode", "title", "rows"],
}


def script_schema(env: dict | None = None) -> dict:
    """台本の形。カードの旗が on のときだけ cards を足す（off なら SCRIPT_SCHEMA そのまま）"""
    if not auto_cards_on(env):
        return SCRIPT_SCHEMA
    out = json.loads(json.dumps(SCRIPT_SCHEMA))
    out["properties"]["cards"] = {"type": "ARRAY", "items": _CARD}
    out["required"] = out["required"] + ["cards"]
    out["propertyOrdering"] = out["propertyOrdering"] + ["cards"]
    return out


AUTO_CARDS_RULES = """

# 画面の道具（cards。場面ごとに1つまで。2〜3枚・2種類以上）
- 右の画面は基本は公式ページの画面（選択の青と訳）。場面によって、右に白いカードを2〜4秒出して中身を見せる。左の大きな文字（keyword）は**持ち帰る一言**（例: 契約そのまま・PCは汚れない・費用管理は別で）、カードは**その中身の詳しいこと**。左とカードに同じ言葉を並べない。
- mode は次から選ぶ。続けて同じ mode を使わない。1本で2種類以上:
  - timeline: 2つを並べる（例: 手元のIDE｜クラウド）・前→今（例: 前: 手作業 → 今: 自動）。rows は2〜3行で label（8字以内）と text（18字以内）。value は空。
  - checklist: できる・できないを○×で（例: label「上限の設定」text「× 決められない」）。rows は2〜4行。text は「○ 」か「× 」で始める。value は空。
  - figures: 大事な数字を大きく（例: label「検知まで」value「平均69秒」）。rows は1〜3行。value は記事の数字そのまま（10字以内）、text は空か補足（14字以内）。
  - specs: 手順や要素を順に（例: 検知 → 特定 → 隔離 → 再開）。rows は2〜4行で text（14字以内）だけ。label・value は空。
- beat はカードを出す場面の番号（beat 1〜）。ladder・compare・units を使う場面には出さない。title は14字以内。
- カードの文字・数字は資料（記事）にあるものだけ。数字は記事の表記どおり。製品名は英字のまま。"""
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
**初心者でもわかるが、専門家が物足りなくない**動画にする（2026-11-04 オーナー）。最初に「それは何か・なぜ大事か・誰向けか」をやさしい言葉で言い、
そのあとで本物の画面の証拠（数字・しくみ）を見せる。専門用語は最初に出たときに普通の言葉で言い換えるが、具体的な数字・しくみは省かない。
見る人の「で、自分にはどう関係する？」に、**具体的な数字と比較で答える**。忙しい会社員が頭の中で絵を描ける答えにする。
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
- hook（最初の5〜8秒。2026-11-04 オーナー）: **詳しくない人にも、何のニュースで、なぜ大事かが分かる**ふつうの言葉で始める（記事を読みたくなるように）。
  問いかけ・説明の無い数字・専門用語で始めない（だめな例: 「99.9%でも学習は止まる？」・左に「99.9%」だけ）。
  text は画面に大きく出すニュースの見出し（10〜22字・主役の名前を入れる・**数字を入れない**。例: NVIDIAが学習の自動復旧ソフトを公開）。
  speech は2文で（合計25〜45字）。**1文目は見る人の損得・具体的な場面**（16字前後の言い切り。例: 「AIの学習が止まると、直るまでGPUは遊んだままです。」）、
  **主役の名前は2文目までに**（画面の見出しには0秒から主役の名前が出ている。2026-11-04 オーナー承認）。例: 「NVIDIAが、止まった学習を自動で立て直すソフトを無料で公開しました。」
  payoff は text が消えたあと左に出す、ニュースの中身の短い言い回し（6〜16字・**数字だけにしない**。例: 止まった学習を自動で再開）。payoff の言葉は speech にも入れる。
  数字は、意味が分かる文脈のあと（beat）で、ふつうの言葉の説明と一緒に出す（例: 「1台の故障率は0.1%でも、1万台なら毎日どこかが止まる」）。
  block / target は payoff の証拠になる画面の文字（記事の見出しなど、文字の大きい行（番号に「|大」）を選ぶ）。
- beats: {BEATS_MIN}〜{BEATS_MAX}個。**1つめは必ず「これは何？」**、2つめ以降は話題に合わせて次の役目から選んで、この順に並べる:
  0. これは何？（必ず beat 1。label 例: 止まった学習を自動で再開・夜中の対応が減る）: 初めて聞く人向けに、主役が**何か**と、**なぜ大事か・誰向けか**を1〜2文・35〜65字で（hook の続き）。
     主役の名前で始める。例「NVIDIA Recoveryは、AIの学習が止まったときに自動で見つけて再開する仕組みです。GPUで大きなモデルを学習させる会社向けです。」
     専門用語は普通の言葉に（GPU→AIの計算に使う部品、など。言い換えに資料に無い数字を入れない）。keyword は何ができるかの短い言葉（例: 学習を自動で再開）。
  1. いちばん大事な点（label 例: 故障は台数が増えるほど起きる）: このニュースのいちばん大事な事実を、数字の意味が分かる言い方で1行。
  2. 比較（label 例: 料金は3段階・前は人が手で直していた）: 料金のニュースなら料金の段階を並べる（ladder を使う）。製品のニュースなら前の版・他のプランとの違い。
     並べて初めて分かる違いを1つ必ず言う（例: Maxは月100ドルでProの5倍、Ultraは月200ドルでProの10倍＝1ドルあたりの量は同じ。
     だからUltraを選ぶ理由は量ではなく、5時間制限が無いこと）。計算の答えを新しい数字として書かない（「同じ」「倍」のように言葉で）。
  3. たとえ（label 例: 1週間分で比べると）: 公式の数字・倍率だけを使って、毎日の仕事の場面に置き換える。
     比較（ladder・compare）で同じ倍率をもう言っているなら、たとえの場面は作らない（その時間を「必要な人」の具体的な場面に回す）。
  4. 必要な人（label 例: 大きな学習を回す会社に効く）: **頭に絵が浮かぶ、具体的な1日の仕事の場面**で言う。種類の名前（資料作り・レポート・表計算）で終わらせない。
     例: 「営業データの集計からグラフ、提案スライドまで、1日中まとめて任せる」「Claude Codeで、1日かけてアプリの修正を進める」
     「月曜の朝に、先週の問い合わせ100件を分類して、返信の下書きまで作らせる」のように、作業の流れが分かる言い方。
     見る人は20〜50代の会社員・個人事業主。場面の中の作業は例として自由に書いてよいが、数字（件数・時間など）は資料にあるものだけ。資料に無い数字を場面に入れない。
     画面の keyword・sub も場面そのものを短く（例: keyword「集計→提案書を丸投げ」sub「朝から晩まで任せる」）。「長い作業を任せる」のような種類の言葉にしない。
  5. 要らない人・注意（label 例: 小さな使い方ならProで十分）: これも具体的な仕事の場面で（例: 「1日に数回、メールの言い回しを相談する程度ならPro」）。条件（月払いのみ・アプリ経由は価格が違う）も入れてよい。
  6. 確かめ方・使い方（label 例: まず監視だけで試せる）: どこを押せば確かめられる・試せるか（例: 設定の使用状況）。
  label: 左上に小さく出す見出し（4〜14字）。**その場面の中身から作った、自然な日本語の見出し**（例: 止まった学習を自動で再開・故障は台数が増えるほど起きる）。
  場面ごとに必ず違う言葉にする。決まった問いの見出し（答えは？・何ができる？・前と何が違う？・誰に効く？・どう試す？）・問いの形（？で終わる）・
  役目の名前（答え・注意・比較・確かめ方）・英語の製品名だけ・名詞1つだけ（実績・Recovery）にしない。label と keyword は同じ言葉にしない。
  keyword: 画面に大きく出す言葉。2〜14字（短いほどよい。長いと2行になる）。文にしない。句読点を付けない。**意味の分かる短い言い回し**にする（例: 学習を自動で再開・検知まで平均69秒・人手から自動へ）。
  名詞1つだけ（クラスター）・英語の製品名だけ（Recovery）にしない。場面ごとに違う言葉にする（同じ keyword を2回使わない）。
  **keyword と sub はその場面の speech と同じ話**にする（keyword・sub の数字と言葉は、その場面の speech にも入れる。別の場面の数字を出さない）。sub: 補足（14字以内。不要なら空）。
  speech: 読み上げ。短い文で1〜3文・60字以内。keyword の数字・名前を必ず含める。
  block: 下の「画面の文字」一覧の番号（例: 1-23）。target: その行の文字から、この beat の証拠になる部分を**一文字も変えずに**抜き出す（日本語は2〜40字。英語は100字まで）。
  英語の target は文として完結する範囲（文の頭から、文末のピリオドかカンマまで）を選ぶ。文の途中で切らない。
  証拠の文は画面で選択（青）して読んでいる間ずっと見せるので、その場面の keyword の数字か言葉を必ず含む文を選ぶ（英語なら ja の訳に入っていればよい）。
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
- short_beats: SNS 用の短い版（30〜40秒）に使う beat の番号を2つ（beat 1 以外。例: [2, 3]）。1つめは「答え」、2つめは「並べて初めて分かる違い」を言う beat。
  短い版は hook → beat 1（これは何？）→ この2つ → 「◯◯は記事で」で終わるので、これだけで問いへの本当の答えが伝わる beat を選ぶ（長すぎるときは2つめを落とす）。
- conclusion: 最後の一言。text は16字以内でこのニュースの要点を言う。speech は14〜30字。
- title: 40字以内・主役の名前を入れて事実をそのまま。summary: 説明欄の要約。「要約すると、」で始めて80〜140字。hashtags: 3〜4個（#付き）。

# 画面の文字（text・payoff・keyword・sub・conclusion.text・ja・ladder）
- 製品名・社名は資料の表記のまま英字で書く（Claude・Gemini・Ultra。カタカナにしない）。カタカナにするのは speech だけ。

# 見続けたくなる作り（2026-11-04 オーナー「真面目すぎて、ただの読み聞かせ」。ふざけない・大人の言い方のまま）
- hook.speech は要約で始めない。見る人が3秒で気になる具体的な場面か意外な対比（例: エディタを変えずに、使うAIだけ乗り換えられたら？）から入り、続けて何のニュースかを言う。
- 話し言葉の短い文で、1つの beat で1つのことだけ（speech は60字以内）。途中に「で、何が変わる？」の切り替えと、見る人の仕事に当てはめた「あなたの場合」の場面を1つずつ入れる。
- conclusion.speech は要点のくり返しではなく、見る人への問いかけ（投稿の問いと同じ向き）で終える。「担い手」「〜を図る」などの硬い言い回しは使わない。

# 読み上げ（speech）
- 英語の製品名・社名はカタカナ（Claude→クロード、OpenAI→オープンエーアイ、Pro→プロ）。記号は言葉に（$200→200ドル、%→パーセント）。
- 数字はアラビア数字のまま（かなや漢数字にしない）。""" + (AUTO_CARDS_RULES if auto_cards_on() else "")


REVIEW_SCHEMA = {
    "type": "OBJECT",
    "properties": {"ok": {"type": "BOOLEAN"}, "issues": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["ok", "issues"],
}


def review_system() -> str:
    return """あなたは経済ニュース番組の厳しい編集長。短い解説動画の台本を読んで、次を判定する。
0. 最初の5〜8秒（hook と beat 1）で、詳しくない人にも何のニュースで、なぜ大事かが分かるか（問いかけ・説明の無い数字・専門用語で始めていたら不合格）。
   beat 1 が「これは何？」になっているか（初めて聞く人が、主役が何で、なぜ大事か・誰向けかを最初の数秒で分かるか。専門家が退屈しない具体さもあるか）。
   label と keyword が場面ごとに違い、場面の中身から作った自然な日本語か（決まった問いの見出し「答えは？」など・名詞1つだけ・英語の製品名だけ・同じ言葉の繰り返しは不合格）。
1. 見る人の「自分にどう関係する？」に、具体的な数字や比較で答えているか（忙しい会社員が頭の中で絵を描けるか）。
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
        if page.own:  # 自分の記事は事実の出どころにしない（数字は記事 JSON と本物の公式ページで照合する）
            continue
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


# 日本語の証拠の文の区切り（2026-11-04 オーナー「行の終わりの『これま』だけに選択の青が付く」）。
# 選択は意味のまとまり（句点・読点・かぎかっこの間）で始まり・終わる。語の途中で切らない
JA_START_BREAK = set("。！？!?、，,」』）)】 　・：:")
JA_END_BREAK = set("。！？!?、，,」』）)】")
JA_PHRASE_MAX = 64  # これより長くなるなら句読点まで広げず、文節（budoux）の切れ目で止める


def _bunsetsu_bounds(text: str) -> set[int]:
    """文節の切れ目の位置（budoux が無ければ文字の種類の変わり目）"""
    try:
        import budoux

        parts = budoux.load_default_japanese_parser().parse(text)
    except Exception:  # noqa: BLE001
        parts = [m.group(0) for m in re.finditer(r"[一-龥々]+[ぁ-ん]*|[ァ-ヴー]+[ぁ-ん]*|[A-Za-z0-9.,%$]+[ぁ-ん]*|.", text)]
    out, at = {0}, 0
    for part in parts:
        at += len(part)
        out.add(at)
    return out


def ja_phrase_bounds(block_text: str, start: int, end: int) -> tuple[int, int]:
    """block_text[start:end] を意味のまとまりまで広げた範囲。句読点・かぎかっこ・行の頭と終わりまで（長すぎるなら文節の切れ目まで）"""
    def brk(i: int, chars: set) -> bool:  # 数字の間のカンマ・ピリオド（1,000・1.5）は区切りではない
        c = block_text[i]
        if c in ",，.." and 0 < i < len(block_text) - 1 and block_text[i - 1].isdigit() and block_text[i + 1].isdigit():
            return False
        return c in chars

    a, b = start, end
    while a > 0 and not brk(a - 1, JA_START_BREAK):
        a -= 1
    while b < len(block_text) and not brk(b - 1, JA_END_BREAK):
        b += 1
    while a < start and block_text[a] in " 　":
        a += 1
    if b - a <= JA_PHRASE_MAX:
        return a, b
    bounds = sorted(_bunsetsu_bounds(block_text))
    a2 = max([x for x in bounds if x <= start] or [start])
    b2 = min([x for x in bounds if x >= end] or [end])
    return a2, b2


def ja_phrase_ok(target: str, block_text: str) -> bool:
    """日本語の target が意味のまとまりか（ja_phrase_bounds で広げても変わらない）"""
    at = block_text.find(target)
    if at < 0 or not target:
        return True  # 見つからないときは別の検査（画面の文字にありません）が落とす
    a, b = ja_phrase_bounds(block_text, at, at + len(target))
    punct = "".join(JA_START_BREAK | JA_END_BREAK)
    return block_text[a:b].strip(punct) == target.strip(punct)


def snap_target(item: dict, pages: list) -> bool:
    """日本語の target を、画面の文字の意味のまとまりまで広げる（言葉は足さない・画面の文字そのまま）。変えたら True"""
    tgt = str(item.get("target") or "")
    if not tgt or is_english(tgt) or not pages:
        return False
    blocks = [b for p in pages for b in p.blocks]
    blk = next((b for b in blocks if b.id == item.get("block") and tgt in b.text), None) or next((b for b in blocks if tgt in b.text), None)
    if blk is None:
        return False
    at = blk.text.find(tgt)
    a, b = ja_phrase_bounds(blk.text, at, at + len(tgt))
    new = blk.text[a:b].strip()
    for close, opener in (("」", "「"), ("』", "『"), ("）", "（"), (")", "(")):
        if new.endswith(close) and opener not in new and not tgt.endswith(close):
            new = new[: -len(close)].rstrip()
    if new == tgt or len(new) < len(tgt):
        return False
    item["target"], item["block"] = new, blk.id
    return True


def align_subs(script: dict) -> list[str]:
    """左の補足（sub）は、選んだ証拠の文（英語なら訳）に数字・言葉があるときだけ出す。無ければ空にする（補足は無くてもよい）。
    2026-11-04 AI の目視の確認「左の補足『対応はSlurm』『平均69秒で検知』が選択した文に無い」"""
    done = []
    for i, b in enumerate(script.get("beats") or [], 1):
        sub = str(b.get("sub") or "").strip()
        if sub and not gate.key_fact_ok(sub, "", str(b.get("target", "")), str(b.get("ja", "") or "")):
            b["sub"] = ""
            done.append(f"beat{i}")
    return done


def snap_targets(script: dict, pages: list) -> list[str]:
    """台本の hook と beats の日本語の証拠を意味のまとまりに広げる。直した場所の一覧"""
    done = []
    for name, item in [("hook", script.get("hook") or {})] + [(f"beat{i}", b) for i, b in enumerate(script.get("beats") or [], 1)]:
        if isinstance(item, dict) and snap_target(item, pages):
            done.append(name)
    return done


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
    if hook.get("payoff") and re.search(r"\d", hook["payoff"]):  # 数字のときだけ数字カードとして照合（2026-11-04 からはふつうの言い回しが多い）
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
    if not (8 <= len(hook.get("text", "")) <= 24):
        out.append(f"hook.text は10〜22字のニュースの見出し（今は{len(hook.get('text', ''))}字）。")
    if len(hook.get("payoff", "")) > 17:
        out.append("hook.payoff は6〜16字の言い回し。")
    if len(concl.get("text", "")) > 18:
        out.append("conclusion.text は16字以内。")
    for i, b in enumerate(beats, 1):
        kw = b.get("keyword", "")
        if not (2 <= len(kw) <= 16) or re.search(r"[、。！？]", kw):  # 2026-11-04: 自然な言い回しは長め（16字まで・2行に折り返す）
            out.append(f"beat{i}.keyword は2〜14字で句読点なし（{kw}）。")
        if len(b.get("sub", "")) > 16:
            out.append(f"beat{i}.sub は14字以内。")
        if len(b.get("speech", "")) > 66:
            out.append(f"beat{i}.speech が長すぎます（60字以内。今は{len(b.get('speech', ''))}字）。")
        if not (2 <= len(b.get("label", "")) <= 15):
            out.append(f"beat{i}.label は4〜14字の自然な見出し（例: 止まった学習を自動で再開）。")
    out += [f"見出し: {p}" for p in gate.label_problems(s)]  # 同じ見出し・名詞1つだけの見出しやキーワード（ゲートでも見る）
    out += [f"始まり: {p}" for p in gate.opening_problems(s, article)]  # 最初の5〜8秒で何のニュースか分かる（ゲートでも見る）
    if beats and len(beats[0].get("speech", "")) > 80:
        out.append("beat1（これは何？）の speech は35〜65字で、主役が何か・誰向けかを短く。")
    total_speech = sum(len(x.get("speech", "")) for x in [hook, concl] + beats)
    if total_speech > SPEECH_TOTAL_MAX:
        out.append(f"読み上げの合計が長すぎます（{total_speech}字。{SPEECH_TOTAL_MAX - 30}字前後まで）。")
    if total_speech < 190:
        out.append(f"読み上げの合計が短すぎます（{total_speech}字）。問いに数字と比較で答えるには足りません。")
    if len(hook.get("speech", "")) > 50:
        out.append("hook.speech は25〜45字（何が起きたか＋なぜ大事か）。")
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
        if tgt and not is_english(item.get("target", "")) and not ja_phrase_ok(item.get("target", ""), tgt.block.text):
            out.append(f"証拠の文の区切り: {name}.target「{item.get('target', '')[:30]}」が語や文の途中で切れています。句点・読点・かぎかっこの間のまとまりをそのまま選んでください。")
        if tgt and name != "hook" and not gate.key_fact_ok(item.get("keyword", ""), item.get("sub", ""), item.get("target", ""), item.get("ja", "")):
            out.append(f"{name}.target「{item.get('target', '')[:40]}」に、キーワード「{item.get('keyword', '')}」の数字・言葉がありません。"
                       "選択して見せる証拠の文には、その場面の大事な数字か言葉を含む行を選んでください（英語なら ja の訳に入っていればよい）。")
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
        if len(ja) > 46:
            out.append(f"{name}.ja は40字以内（今は{len(ja)}字）。")
        if name != "hook" and not clause_ok(tgt_text, item_block_text(pages, item)):
            out.append(f"target の区切り: {name}.target「{tgt_text[:40]}」は文の途中で切れています。文の頭から、文末（ピリオド）か読点（カンマ）までのまとまりを選んでください。")
        extra = [d for d in _digits(ja) if d not in set(_digits(tgt_text))]
        if extra:
            out.append(f"{name}.ja に target に無い数字 {', '.join(extra)} があります。訳に言葉や数字を足さない。")
        if bad := gate.numbers_ok(ja, mat_numbers):
            out.append(f"{name}.ja の数字 {', '.join(bad)} が記事にありません。")
        # 訳のカタカナ語（英語の言い換え）は問わない。英字の名前と「」の語だけ、記事か target にあるかを見る
        bad = [w for w in gate.proper_noun_problems(ja, mat + " " + tgt_text) if not re.fullmatch(r"[ァ-ヴー・]+", w)]
        if bad:
            out.append(f"{name}.ja に記事・target に無い語 {', '.join(bad)}。")
    # 画面の文字のカタカナの製品名は latin_names が英字に直す（一般のカタカナ語「スピード」などは問わない）
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
    if auto_cards_on():
        out += card_problems(s, article)
    out += gate.fact_problems(flat, article) + gate.title_problems(flat, article, v2.main_name(article))
    out += v2.reading_problems(flat)
    return out


def card_problems(s: dict, article: dict) -> list[str]:
    """台本が選んだカードの検査（旗が on のとき）: 形・数・続けて同じ道具を使わない・文字と数字は記事にある・左の一言とカードが同じ言葉にならない"""
    out = []
    beats = s.get("beats") or []
    cards = [c for c in s.get("cards") or [] if isinstance(c, dict)]
    taken = {int((s.get(k) or {}).get("beat") or 0) for k in ("ladder", "compare", "units")}
    mat = gate.material_text(article)
    nums = gate.number_tokens(mat) | {"0"}
    if len(cards) < 2 or len({c.get("mode") for c in cards}) < 2:
        out.append("cards は2〜3枚で、2種類以上の mode を使う。")
    seen_beats, prev = set(), None
    for c in sorted(cards, key=lambda c: int(c.get("beat") or 0)):
        b, mode, rows = int(c.get("beat") or 0), c.get("mode"), c.get("rows") or []
        name = f"cards（beat{b}）"
        if mode not in AUTO_CARD_MODES:
            out.append(f"{name}.mode は {'/'.join(AUTO_CARD_MODES)} のどれか（{mode}）。")
        if not (1 <= b <= len(beats)) or b in seen_beats or b in taken:
            out.append(f"{name}.beat は場面の番号で、1場面に1枚・ladder などと別の場面に。")
        if prev and prev[0] == mode and prev[1] == b - 1:
            out.append(f"{name}: 続けて同じ道具（{mode}）を使わない。")
        seen_beats.add(b)
        prev = (mode, b)
        lo, hi = (1, 3) if mode == "figures" else (2, 4)
        if not (lo <= len(rows) <= hi):
            out.append(f"{name}.rows は{lo}〜{hi}行。")
        if len(c.get("title", "")) > 14:
            out.append(f"{name}.title は14字以内。")
        kw = (beats[b - 1].get("keyword", "") if 1 <= b <= len(beats) else "")
        for r in rows:
            label, text, value = r.get("label", ""), r.get("text", ""), r.get("value", "")
            if len(label) > 8 or len(text) > 18 or len(value) > 10:
                out.append(f"{name}: 行の文字が長すぎます（label 8字・text 18字・value 10字まで）。")
            if mode == "checklist" and not re.match(r"^[○×✕✓]\s?", text):
                out.append(f"{name}: checklist の text は「○ 」か「× 」で始める（{text}）。")
            if mode == "figures" and not re.search(r"\d", gate.norm(value)):
                out.append(f"{name}: figures の value は記事の数字（{value}）。")
            for t in (c.get("title", ""), label, text, value):
                if bad := gate.numbers_ok(t, nums):
                    out.append(f"{name}: 記事に無い数字 {', '.join(sorted(set(bad)))}（{t}）。")
                if bad := gate.proper_noun_problems(t, mat):
                    out.append(f"{name}: 記事に無い語 {', '.join(sorted(set(bad)))}（{t}）。")
                out += [f"{name}: {p}" for p in gate.stiff_problems(t)] if hasattr(gate, "stiff_problems") else []
            if kw and len(kw) >= 4 and (gate.norm(kw) in gate.norm(text) or gate.norm(text) and gate.norm(text) in gate.norm(kw)):
                out.append(f"{name}: 左の一言「{kw}」とカードの文字が同じ（左は持ち帰る一言、カードは中身に）。")
    return out


def normalize_cards(script: dict) -> list[dict]:
    """台本のカードを描画の形に（specs は name、figures は label・value・change、出どころは「記事より」）。型のカード（video_templates）はそのまま"""
    out = []
    for c in script.get("cards") or []:
        if not isinstance(c, dict):
            continue
        c = dict(c)
        rows = []
        for r in c.get("rows") or []:
            r = dict(r)
            if c.get("mode") == "specs" and not r.get("name"):
                r["name"] = r.get("text") or r.get("label") or ""
            if c.get("mode") == "figures" and r.get("text") and not r.get("change"):
                r["change"] = r["text"]
            rows.append(r)
        c["rows"] = rows
        c.setdefault("source", "記事より")
        out.append(c)
    return out


STYLE_PREFIXES = ("speech で数字", "speech に英単語", "target の区切り", "hook.payoff は", "hook.speech は", "conclusion.speech は", "読み上げの合計が")


def review_script(data: dict, article: dict, pages: list[Page], tier: str) -> dict | None:
    import llm

    prompt = f"# 資料\n{script_material(article, pages)}\n\n# 台本\n{json.dumps(data, ensure_ascii=False)}\n\n判定を JSON で返してください。"
    try:
        verdict, _ = llm.generate_json(review_system(), prompt, REVIEW_SCHEMA, temperature=0.2, max_output_tokens=2048, tier=tier, visibility="high", purpose="video_review")
        return verdict
    except Exception as error:  # noqa: BLE001 - レビューが使えないときは、事実の検査に通った台本をそのまま使う
        log(f"   [v3] 台本のレビューに失敗: {type(error).__name__}")
        return None


SCRIPT_TIME_CAP = float(os.environ.get("V3_SCRIPT_TIME_CAP", "480"))  # 台本の段の上限（秒）。CI は1回3本×(台本＋描画2本)を50分の枠に収める
SCRIPT_MAIN_ATTEMPTS = int(os.environ.get("V3_SCRIPT_ATTEMPTS", "5"))  # ふつうの書き直しの回数（このあと strict / simple の立て直しが最大4回）
FALLBACK_ROUNDS = ("strict", "simple", "strict", "simple")
CALL_GUESS = 70.0  # LLM の1回の目安（秒）。実測が出たら実測の平均を使う


def _better(new: list[str], old: list[str]) -> bool:
    """問題が減ったか（事実・証拠の問題の数 → 全体の数の順に比べる）"""
    def key(ps: list[str]) -> tuple[int, int]:
        return (sum(1 for p in ps if not p.startswith(STYLE_PREFIXES)), len(ps))

    return key(new) < key(old)


def _sentences(text: str) -> list[str]:
    return [s for s in re.findall(r"[^。！？!?]+[。！？!?]*", text or "") if s.strip()]


def speech_total(data: dict) -> int:
    return sum(len(x.get("speech", "")) for x in [data.get("hook") or {}, data.get("conclusion") or {}] + list(data.get("beats") or []))


def shorten_speech(data: dict, limit: int = SPEECH_TOTAL_MAX, floor: int = 200, beat_floor: int = 18) -> dict | None:
    """読み上げの合計が長すぎるとき、いちばん大事でない文を1つずつ落として limit 字以内にする（LLM を呼ばない・言葉は足さない）。
    落とさない文: 画面のキーワード・補足の数字や言葉を含む文（読み上げと画面がずれる）・グラフの倍率や料金の段階の数字を含む文・各場面の最後の1文。
    落とす順: 数字の無い文 → 数字のある文。同じなら長い場面の後ろの文から。つかみと締めは触らない。落とせないときは None"""
    out = json.loads(json.dumps(data, ensure_ascii=False))
    beats = out.get("beats") or []
    total = speech_total(out)
    if total <= limit:
        return None
    protect_by_beat: dict[int, set] = {}
    comp = out.get("compare") or {}
    if comp.get("beat"):
        protect_by_beat.setdefault(int(comp["beat"]), set()).update(_digits(comp.get("ratio", "")))
    lad = out.get("ladder") or {}
    if lad.get("beat"):
        protect_by_beat.setdefault(int(lad["beat"]), set()).update(d for r in lad.get("rows") or [] for d in _digits(f"{r.get('price', '')} {r.get('mult', '')}"))
    uni = out.get("units") or {}
    if uni.get("beat"):
        protect_by_beat.setdefault(int(uni["beat"]), set()).update(str(r.get("count", "")) for r in uni.get("rows") or [])
    while total > limit:
        cands = []
        for i, b in enumerate(beats, 1):
            sents = _sentences(b.get("speech", ""))
            if len(sents) < 2:
                continue
            caption = f"{b.get('keyword', '')} {b.get('sub', '')}"
            protect = set(_digits(caption)) | protect_by_beat.get(i, set())
            terms = [t for t in gate._TERM.findall(caption) if t not in gate.LATIN_OK]
            for j, s in enumerate(sents):
                rest = "".join(sents[:j] + sents[j + 1 :])
                if len(rest) < beat_floor:
                    continue
                if set(_digits(s)) & protect:
                    continue
                if any(gate._term_in(t, s) and not gate._term_in(t, rest) for t in terms):
                    continue
                cands.append(((1 if _digits(s) else 0, 1 if j == 0 else 0, -len(b.get("speech", "")), -j), i, j, s))
        if not cands:
            return None
        _, i, j, s = min(cands, key=lambda c: c[0])
        sents = _sentences(beats[i - 1]["speech"])
        beats[i - 1]["speech"] = "".join(sents[:j] + sents[j + 1 :]).strip()
        total -= len(s)
        out.setdefault("_repairs", []).append(f"beat{i} の読み上げの文を削った: {s.strip()[:40]}")
    if total < floor:
        return None
    return out


GENERIC_TERMS = {"ドル", "時間", "毎週", "毎月", "月額", "プラン", "場合", "以上", "以下", "可能", "必要", "利用", "提供", "公開", "発表", "対応"}


RUN_RE = re.compile(r"[A-Za-z0-9ァ-ヴー一-龥々]+")


def _phrases(text: str) -> list[str]:
    """語の続き（かなを含まないひと続き）と、「の」でつながった2つ（学習の復旧）"""
    runs = list(RUN_RE.finditer(text))
    out = [m.group(0) for m in runs]
    out += [f"{x.group(0)}の{y.group(0)}" for x, y in zip(runs, runs[1:]) if text[x.end() : y.start()] == "の"]
    return out


def keyword_candidates(beat: dict, article: dict, others: list[str] | None = None) -> list[str]:
    """証拠の文（英語なら訳）にある言葉のうち、記事にも書かれているもの（キーワードの付け替え候補。言葉は作らない）。
    候補は語（GPU・故障）と、語の続き（GPU故障・学習の復旧。中の語がどれも記事にあるときだけ）。
    元のキーワードと文字が重なる → ほかの場面のキーワードと重ならない → 読み上げにある → 英字だけでない → 6字に近い（短すぎず長すぎず）の順"""
    tgt, ja = beat.get("target", ""), (beat.get("ja") or "").strip()
    src = gate.norm(ja if is_english(tgt) and ja else tgt)
    mat = gate.norm(gate.material_text(article)).lower()
    old = beat.get("keyword", "")
    speech = beat.get("speech", "")
    found = [t.strip(".-+") for t in gate._TERM.findall(src)] + _phrases(src)
    seen, out = set(), []
    for t in found:
        if t in seen or t in gate.LATIN_OK or t in GENERIC_TERMS or not (2 <= len(t) <= 10) or re.fullmatch(r"[\d.]+", t):
            continue
        seen.add(t)
        terms = [x.strip(".-+") for x in gate._TERM.findall(t)]
        if not terms or not all(x.lower() in mat for x in terms):
            continue
        if not gate.key_fact_ok(t, beat.get("sub", ""), tgt, ja):
            continue
        overlap = any(t[k : k + 2] in old for k in range(len(t) - 1)) or t.lower() in old.lower()
        dup = any(t.lower() in o.lower() or o.lower() in t.lower() for o in others or [] if o)
        if gate.bare_phrase(t) or any(gate.norm(t).lower() == gate.norm(o).lower() for o in others or [] if o) or not gate.point_ok(t, speech):
            continue  # 名詞1つだけ（クラスター）・英語の製品名だけ（Recovery）・ほかの場面と同じ言葉は、画面の大きな言葉にしない（2026-11-04）
        latin = bool(re.fullmatch(r"[A-Za-z0-9 .\-+]+", t))  # 英字だけ（製品名・社名）は画面の大きな言葉として弱いので後ろへ
        out.append(((0 if overlap else 1, 1 if dup else 0, 0 if any(gate._term_in(x, speech) for x in terms) else 1, 1 if latin else 0, abs(len(t) - 6), -len(t)), t))
    return [t for _, t in sorted(out)]


def _evidence_ok(pages: list[Page], item: dict) -> bool:
    tgt = find_target(pages, item.get("target", ""), item.get("block", ""), keyword=item.get("keyword", ""))
    if not tgt or not gate.key_fact_ok(item.get("keyword", ""), item.get("sub", ""), item.get("target", ""), item.get("ja", "")):
        return False
    have = {float(x) for x in _digits(tgt.block.text)}
    return all(float(d) in have for d in _digits(item.get("keyword", "")))


def repair_evidence(data: dict, article: dict, pages: list[Page]) -> dict | None:
    """証拠の文にキーワードの数字・言葉が無い場面を、LLM を呼ばずに直す。場面ごとに次の順で試す:
    ①画面の文字から、キーワードの数字・言葉を含む日本語の文を選び直す（訳が要らない）
    ②今の証拠の文（英語なら訳）にある、記事にも書かれた言葉をキーワードにする（証拠と訳はそのまま。言葉は作らない）
    ③キーワードを含む英語の文を選び直す（訳が無いので ja は空。訳は次の書き直しで LLM に頼む）
    どれもだめな場面はそのまま残す（他の場面は直す）。何も変わらなければ None"""
    out = json.loads(json.dumps(data, ensure_ascii=False))
    changed = False
    repairs = out.setdefault("_repairs", [])
    hook = out.get("hook") or {}
    digits = _digits(hook.get("payoff", ""))
    htg = find_target(pages, hook.get("target", ""), hook.get("block", ""), keyword=hook.get("payoff", "")) if hook else None
    if digits and (not htg or any(float(d) not in {float(x) for x in _digits(htg.block.text)} for d in digits)):
        for p in pages:
            hit = next((blk for blk in sorted(p.blocks, key=lambda b: -b.font_px) if all(float(d) in {float(x) for x in _digits(blk.text)} for d in digits) and len(blk.text) <= 200), None)
            if hit:
                hook.update({"block": hit.id, "target": hit.text, "ja": ""})
                repairs.append(f"hook の証拠を選び直した: {hit.text[:40]}")
                changed = True
                break
    for i, b in enumerate(out.get("beats") or [], 1):
        if _evidence_ok(pages, b):
            continue
        picks = []
        for p in pages:
            for blk in p.blocks:
                for sent in re.split(r"(?<=[。！？.!?])\s*", blk.text):
                    sent = sent.strip()
                    if 4 <= len(sent) <= 140 and gate.key_fact_ok(b.get("keyword", ""), b.get("sub", ""), sent):
                        picks.append(((1 if is_english(sent) else 0, 1 if p.own else 0, len(sent)), blk.id, sent))
        picks.sort(key=lambda x: x[0])
        ja_pick = next((x for x in picks if not is_english(x[2]) and find_target(pages, x[2], x[1])), None)
        if ja_pick:  # ①
            b.update({"block": ja_pick[1], "target": ja_pick[2], "ja": ""})
            repairs.append(f"beat{i} の証拠を選び直した: {ja_pick[2][:40]}")
            changed = True
            continue
        if find_target(pages, b.get("target", ""), b.get("block", "")):  # ②
            others = [x.get("keyword", "") for k, x in enumerate(out.get("beats") or [], 1) if k != i]
            for kw in keyword_candidates(b, article, others):
                trial = dict(b, keyword=kw)
                if _evidence_ok(pages, trial):
                    repairs.append(f"beat{i} のキーワードを証拠の文にある言葉にした: {b.get('keyword', '')} → {kw}")
                    b["keyword"] = kw
                    changed = True
                    break
            else:
                kw = None
            if kw:
                continue
        en_pick = next((x for x in picks if find_target(pages, x[2], x[1])), None)
        if en_pick:  # ③
            b.update({"block": en_pick[1], "target": en_pick[2], "ja": ""})
            repairs.append(f"beat{i} の証拠を英語の文に選び直した（訳は次の書き直しで）: {en_pick[2][:40]}")
            changed = True
    if not repairs:
        out.pop("_repairs", None)
    return out if changed else None


def self_correct(data: dict, article: dict, pages: list[Page], problems: list[str] | None = None) -> tuple[dict, list[str]]:
    """LLM に戻す前に、機械で直せる問題を直して検査し直す（読み上げの長さ・証拠の行）。問題が減ったときだけ採用"""
    problems = script_problems(data, article, pages) if problems is None else problems
    if any(p.startswith("読み上げの合計が長すぎ") for p in problems):
        cand = shorten_speech(data)
        if cand is not None:
            cand_problems = script_problems(cand, article, pages)
            if _better(cand_problems, problems):
                log(f"   [v3] 読み上げを {speech_total(data)}字 → {speech_total(cand)}字に削った")
                data, problems = cand, cand_problems
    if any("hook の左の言葉" in p for p in problems):
        # つかみの左の言葉が読み上げに無い → つかみの見出し（読み上げと同じ話・数字なし）を左にも出す（言葉は作らない。2026-11-04）
        text = ((data.get("hook") or {}).get("text") or "").strip()
        if 6 <= len(text) <= 17:
            cand = json.loads(json.dumps(data, ensure_ascii=False))
            cand["hook"]["payoff"] = text
            cand.setdefault("_repairs", []).append(f"hook の左の言葉をつかみの見出しにした: {text}")
            cand_problems = script_problems(cand, article, pages)
            if _better(cand_problems, problems):
                log(f"   [v3] つかみの左の言葉を見出しにした: {text}")
                data, problems = cand, cand_problems
    if any(EVIDENCE_PROBLEM.search(p) for p in problems):
        cand = repair_evidence(data, article, pages)
        if cand is not None:
            cand_problems = script_problems(cand, article, pages)
            if _better(cand_problems, problems):
                log(f"   [v3] 証拠の行・キーワードを直した: {(cand.get('_repairs') or [])[-3:]}")
                data, problems = cand, cand_problems
    return data, problems


def feedback_for(problems: list[str], data: dict, article: dict, pages: list[Page]) -> str:
    """次の書き直しに渡す具体的な直し方（問題の一覧＋字数の予算＋キーワードの候補）＋前回の台本"""
    lines = [f"- {p}" for p in problems]
    total = speech_total(data)
    if total > SPEECH_TOTAL_MAX:
        goal = SPEECH_TOTAL_MAX - 30
        longest = sorted(((len(b.get("speech", "")), i) for i, b in enumerate(data.get("beats") or [], 1)), reverse=True)[:3]
        lines.append(
            f"- 字数の予算: 読み上げ（hook・beats・conclusion の speech）の合計を{goal}字以内に（今は{total}字。{total - goal}字以上削る）。"
            + "長い speech: " + "・".join(f"beat{i} {n}字" for n, i in longest) + "。言い換えより、1場面1〜2文に減らす。"
        )
    for m in re.finditer(r"(beat(\d+))\.target「[^」]*」に、キーワード", "\n".join(problems)):
        k = int(m.group(2))
        beats = data.get("beats") or []
        if 1 <= k <= len(beats):
            cands = keyword_candidates(beats[k - 1], article, [x.get("keyword", "") for j, x in enumerate(beats, 1) if j != k])[:4]
            if cands:
                lines.append(f"- {m.group(1)}: 証拠の文をそのまま使うなら、keyword を証拠の文（訳）にある言葉に（候補: {'・'.join(cands)}）。")
    return "\n\n# 前回の台本の問題（すべて直す）\n" + "\n".join(lines) + "\n\n# 前回の台本\n" + json.dumps({k: v for k, v in data.items() if not k.startswith("_")}, ensure_ascii=False)


def _clear_broken_ja(data: dict, problems: list[str]) -> None:
    """英文の区切りが直らない場面は、訳の吹き出しを出さない（ja を空に）。途中で切れた文の訳は出さない"""
    for p in problems:
        if m := re.match(r"target の区切り: (beat(\d+)|hook)\.", p):
            beats = data.get("beats") or []
            if m.group(1) == "hook":
                data["hook"]["ja"] = ""
            elif 1 <= int(m.group(2)) <= len(beats):
                beats[int(m.group(2)) - 1]["ja"] = ""


def write_script(article: dict, pages: list[Page], note: str = "") -> tuple[dict, str]:
    """台本を書く。LLM の答えを検査し、機械で直せるもの（長さ・証拠の行）は直してから、残りの問題を具体的に返して書き直させる。
    ふつうの書き直し SCRIPT_MAIN_ATTEMPTS 回（途中で編集長のレビュー1回）→ strict / simple の立て直し（最大4回）。
    全体を SCRIPT_TIME_CAP 秒に収める（LLM の1回の実測の平均で、次の1回が間に合うかを見る）"""
    import llm

    system = script_system() + note  # note は型の一言（video_templates.NOTES）。空なら今までと同じ
    prompt = f"# 資料\n{script_material(article, pages)}\n\n上の資料だけを使って、決まりどおりの台本を JSON で書いてください。"
    feedback, last = "", ""
    tier = os.environ.get("V3_LLM_TIER", "quality")
    reviews = 0
    best = None  # 事実の検査に通った最後の台本（レビューの書き直しが通らなかったときに使う）
    tolerable = None  # 読み・長さの注意だけが残った台本（時間切れのときに使う）
    t_start = time.time()
    deadline = t_start + SCRIPT_TIME_CAP
    call_secs: list[float] = []
    calls = 0

    def guess() -> float:
        return sum(call_secs) / len(call_secs) if call_secs else CALL_GUESS

    def ask(sys_text: str, temperature: float) -> tuple[dict, str]:
        nonlocal tier, calls
        while True:
            t = time.time()
            calls += 1
            try:
                got = llm.generate_json(sys_text, prompt + feedback, script_schema(), temperature=temperature, max_output_tokens=8192, tier=tier, visibility="high", purpose="video")
                call_secs.append(time.time() - t)
                return got
            except RuntimeError as error:
                call_secs.append(max(time.time() - t, 1.0))
                if tier == "bulk" or deadline - time.time() < guess() * 0.8:
                    raise
                tier = "bulk"  # 上位モデルの枠が尽きたら軽いモデルへ（事実の照合は同じ）
                log(f"   [v3] 台本: quality で書けない（{str(error)[:120]}）ので bulk で書く")

    def finish(data: dict, model: str, problems: list[str], mode: str = "") -> tuple[dict, str]:
        if problems:  # 読み・長さだけは最後に妥協する（事実・証拠の問題は必ず直す。入らない文字は描画で止まる）
            log(f"   [v3] 読み・長さの注意を残して採用: {problems}")
            data["_notes"] = problems
        if mode:
            data["_mode"] = mode
        data["_reviews"] = reviews
        data["_attempts"] = calls
        data["_script_seconds"] = round(time.time() - t_start, 1)
        return data, model

    def prepare(data: dict) -> tuple[dict, list[str]]:
        data["beats"] = [b for b in data.get("beats") or [] if isinstance(b, dict)][:BEATS_MAX]
        latin_names(data)
        snap_targets(data, pages)  # 日本語の証拠は意味のまとまりまで広げる（選択の青が語の途中で切れないように）
        data, probs = self_correct(data, article, pages)
        if align_subs(data):  # 証拠に無い補足は出さない（検査し直す）
            probs = script_problems(data, article, pages)
        return data, probs

    # ① ふつうの書き直し（途中で編集長のレビュー1回）
    for attempt in range(1, SCRIPT_MAIN_ATTEMPTS + 1):
        left = deadline - time.time()
        if attempt > 1 and left < guess() * 2.2:  # 立て直し（simple）の1回分を残す
            log(f"   [v3] 台本: 残り{left:.0f}秒なので立て直しへ")
            break
        try:
            data, model = ask(system, 0.5)
        except RuntimeError as error:
            last = f"{last}; {error}"
            break
        data, problems = prepare(data)
        style = [p for p in problems if p.startswith(STYLE_PREFIXES)]
        if attempt >= 3 and problems and len(style) == len(problems):
            _clear_broken_ja(data, problems)
        if not problems or (attempt >= 3 and len(style) == len(problems)):
            best = (json.loads(json.dumps(data, ensure_ascii=False)), model, list(problems))
            # 自己レビュー: 問いに具体的な数字・比較で答えているか、当たり前・空の文が無いか（1回だけ書き直す）
            if reviews < 1 and attempt < SCRIPT_MAIN_ATTEMPTS and deadline - time.time() > guess() * 2.5:
                reviews += 1
                verdict = review_script(data, article, pages, tier)
                if verdict and not verdict.get("ok") and verdict.get("issues"):
                    issues = [str(x) for x in verdict["issues"]][:8]
                    log(f"   [v3] 台本のレビュー（{reviews}）: 書き直し {issues}")
                    feedback = "\n\n# 編集長のレビュー（すべて直す。資料に無い数字は足さない）\n" + "\n".join(f"- {p}" for p in issues) + "\n\n# 前回の台本\n" + json.dumps({k: v for k, v in data.items() if not k.startswith("_")}, ensure_ascii=False)
                    continue
                log(f"   [v3] 台本のレビュー（{reviews}）: 合格")
            return finish(data, model, problems)
        if len(style) == len(problems):
            tolerable = (json.loads(json.dumps(data, ensure_ascii=False)), model, list(problems))
        last = "; ".join(problems)
        log(f"   [v3] 台本の書き直し（{attempt}・{time.time() - t_start:.0f}秒）: {last[:300]}")
        feedback = feedback_for(problems, data, article, pages)
    if best:  # レビューのあとの書き直しが検査に通らなかった → 検査に通った前の台本を使う
        data, model, problems = best
        log(f"   [v3] レビュー後の書き直しが通らないので、検査に通った前の台本を使う（{last[:200]}）")
        return finish(data, model, problems)
    # ② 立て直し: strict（証拠の行だけで・温度を下げる）と simple（記事の要点と確かめた数字だけ）を交互に。時間の許す限り
    for k, phase in enumerate(FALLBACK_ROUNDS):
        left = deadline - time.time()
        if left < guess() * 0.9:
            log(f"   [v3] 台本: 時間切れ（{time.time() - t_start:.0f}秒・LLM {calls}回）")
            break
        if phase == "strict" and left < guess() * 2.0 and "simple" in FALLBACK_ROUNDS[k + 1 :]:
            continue  # 残りが少ないときは、通りやすい simple を先に
        note_text = STRICT_NOTE if phase == "strict" else SIMPLE_NOTE
        try:
            data, model = ask(system + note_text, 0.2)
        except RuntimeError as error:
            last = f"{last}; {error}"
            log(f"   [v3] {phase} の台本: LLM が答えない（{str(error)[:160]}）")
            continue
        data, problems = prepare(data)
        style = [p for p in problems if p.startswith(STYLE_PREFIXES)]
        _clear_broken_ja(data, style)
        if len(style) == len(problems):
            log(f"   [v3] {phase} の台本で採用（{'・'.join(problems)[:200] or '問題なし'}）")
            return finish(data, model, problems, phase)
        last = "; ".join(problems)
        log(f"   [v3] {phase} の台本も書き直し（{time.time() - t_start:.0f}秒）: {last[:300]}")
        feedback = feedback_for(problems, data, article, pages).replace(
            "# 前回の台本の問題（すべて直す）", "# 前回の台本の問題（資料・画面の文字に無い主張はすべて消すか、資料の文どおりに直す）"
        )
    if tolerable:  # 時間切れ: 読み・長さの注意だけが残った台本を使う
        data, model, problems = tolerable
        _clear_broken_ja(data, problems)
        return finish(data, model, problems, "tolerable")
    raise RuntimeError(f"台本を作れませんでした（LLM {calls}回・{time.time() - t_start:.0f}秒）: {last.lstrip('; ')[:400]}")


EVIDENCE_PROBLEM = re.compile(r"の数字・言葉がありません|が証拠の行 \[|が画面の文字にありません")


STRICT_NOTE = """

# いちばん大事（前回までの台本が事実の検査に通らなかった）
- 数字・名前・事実は「資料」の文と「画面の文字」の行に書かれているものだけ。行の文字を言い換えるときも数字は変えない。
- 証拠（block / target）は、その場面で言う数字や名前が**その行に書かれている**ものだけを選ぶ。合う行が無い主張は台本から消す。
- keyword は証拠の文（英語なら ja の訳）にある言葉か数字にする。
- 数字は減らしてよい。迷う数字は書かない。"""

SIMPLE_NOTE = """

# 簡単な台本にする（事実の検査に通ることを最優先）
- 記事の「要点」と「判定」に書かれた事実と数字だけを使う。比べる表（ladder・units・compare）は使わない（beat=0・rows は空）。
- beats はちょうど5つ: 答え → 何が変わった → 必要な人（具体的な仕事の場面）→ 注意・条件 → 確かめ方。読み上げの合計は200〜280字。
- 証拠は、その場面の言葉や数字がそのまま書かれた「画面の文字」の行（記事の要点・表の行など）。英語の行なら ja に訳を。
- keyword は証拠の文（英語なら ja の訳）にある言葉か数字にする。"""


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
    if script.get("_length_range"):  # 型（video_templates の速報 15〜25秒）が決めた範囲
        lo, hi = (float(x) for x in script["_length_range"])
        return lo + 1.5, hi - 1.5, (lo, hi)
    if script.get("_variant") == "short":
        return 24.0, 42.0, SHORT_RANGE
    if script.get("_approved"):
        return TARGET_MIN, 108.0, APPROVED_RANGE
    return TARGET_MIN, TARGET_MAX, LENGTH_RANGE


def short_script(script: dict) -> dict:
    """同じ台本から SNS 用の短い版を作る（LLM は呼ばない）: hook → これは何？（beat 1）→ 答え → 並べて初めて分かる違い → 「◯◯は記事で」。
    2026-11-04 オーナー「最初に何か・なぜ大事かを初心者向けに」→ beat 1 は必ず入れる。長すぎる（読み上げ約200字超）ときは最後の場面を落とす。
    beat の番号を持つ ladder・units・flows・compare は新しい番号に付け替える"""
    beats = script.get("beats") or []
    n = len(beats)
    chosen = [int(i) for i in (script.get("short_beats") or []) if isinstance(i, (int, float)) and 1 <= int(i) <= n]
    chosen = [b for b in dict.fromkeys(chosen) if b != 1][:2]
    if not chosen:  # 台本に無いとき: 比較のグラフの beat（無ければ2つめ）
        graph = [int((script.get(k) or {}).get("beat") or 0) for k in ("ladder", "compare")] + [int(L.get("beat") or 0) for L in script.get("ladders") or []]
        chosen = [b for b in [next((b for b in graph if b and b != 1), 2 if n >= 2 else 0)] if 1 <= b <= n]
    picks = [1] + chosen if n else []

    # 30秒に届かない（読み上げが約145字未満）なら、グラフの場面 → 次の場面の順に1つ足す（短すぎて答えが薄くならないように）
    def chars(ps: list[int]) -> int:
        return len((script.get("hook") or {}).get("speech", "")) + sum(len(beats[i - 1].get("speech", "")) for i in ps) + 24

    while chars(picks) > 200 and len(picks) > 2:  # 45秒に収める（締めの案内の分を残す）
        picks.pop()
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
    for key in ("ladders", "flows", "cards"):
        if key in out:
            out[key] = [moved(x) for x in out[key] if remap.get(int(x.get("beat") or 0))]
    skipped = [(b.get("label") or "").strip() for i, b in enumerate(beats, 1) if i not in picks and (b.get("label") or "").strip()]
    skipped = [x for x in skipped if x not in ("答え",)]
    # 締めの「◯◯は記事で」: 「必要な人」系と「確かめ方」系を優先（無ければ最後の2つ）
    who = next((x for x in skipped if re.search(r"必要|向いて|使い方|活用|こんな", x)), None)
    how = next((x for x in skipped if re.search(r"確かめ|試し|始め|今できる|次の一歩", x)), None)
    tail = [x for x in (who, how) if x] or skipped[-2:]
    out["conclusion"] = short_ending(tail, (script.get("hook") or {}).get("text", ""))
    out["_variant"] = "short"
    return out


# 短い版の締め（2026-11-03 オーナー「記事で詳しく解説しています、だけだと素っ気ない。温かく、リンクへ誘って」）。押しつけない言い方だけ
SHORT_INVITES = [
    "投稿のリンクから、ぜひチェックしてみてください。",
    "気になったら、投稿のリンクからのぞいてみてください。",
    "続きは、投稿のリンクからどうぞ。",
]


def short_ending(topics: list[str], seed: str = "") -> dict:
    """短い版の締め: 声「◯◯は、記事でくわしく解説しています。投稿のリンクから、ぜひチェックしてみてください。」
    画面「くわしくは記事で」＋「投稿のリンクからどうぞ ↗」＋ブランド。誘い文句は話題ごとに少し変える（同じ言い回しが続かないように）"""
    import zlib

    # 見出しが文（「夜中の対応が減る」「？」で終わる）のときは「◯◯は、」にすると日本語が崩れるので、名詞の見出しだけを使う
    topics = [x for x in topics if x and not re.search(r"[ぁ-んー？?]$", x)]
    joined = "と".join(topics)
    head = f"{joined}は、記事でくわしく解説しています。" if topics and len(joined) <= 14 else "くわしくは、記事で解説しています。"
    invite = SHORT_INVITES[zlib.crc32(seed.encode("utf-8")) % len(SHORT_INVITES)]
    return {"text": "くわしくは記事で", "speech": head + invite, "cta": "投稿のリンクからどうぞ ↗", "eyebrow": "続きは記事で"}


# 文の役目ごとの声（2026-11-04 オーナー承認。九州そら・高さ-0.01・抑揚1.0・速さ1.38 が基準）:
# (速さの倍率＝基準の速さに掛ける, 抑揚, 高さ, 前の間（None＝0.22〜0.30秒で少しずつ変える）, 後の間)
VOICE_ROLES = {
    "hook": (1.32 / 1.38, 1.12, -0.01, 0.0, 0.35),
    "normal": (1.0, 1.00, -0.01, None, 0.0),
    "turn": (1.36 / 1.38, 1.10, -0.01, 0.45, 0.25),
    "end": (1.30 / 1.38, 0.95, -0.02, 0.40, 0.0),
}
TURN_START = ("ただし", "ところが", "一方で", "しかし", "でも", "やっかいなのは")
VOICE_ROLES_ON = os.environ.get("V3_VOICE_ROLES", "on") != "off"


def line_role(line: "Line") -> str:
    if line.kind == "hook":
        return "hook"
    if line.kind == "conclusion":
        return "end"
    return "turn" if line.speech.startswith(TURN_START) else "normal"


def make_tts(kind: str, speed: float, intonation: float | None = None, pitch: float | None = None, pause: float = 1.0):
    """v3 の声: VOICEVOX は V3_VOICE（話者・スタイル・高さ・抑揚）。役目ごとに抑揚・高さ・句読点の間を変えられる。それ以外は v2 と同じ"""
    if kind == "voicevox" or (kind == "auto" and shorts.VoiceVox.available()):
        v = V3_VOICE
        tts = shorts.VoiceVox(v["speaker"], speed, style=v["style"], pitch=v["pitch"] if pitch is None else pitch, intonation=v["intonation"] if intonation is None else intonation)
        tts.pause = pause
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
    roles = [line_role(ln) if VOICE_ROLES_ON else "normal" for ln in lines]
    rng = random.Random(len(lines) * 7 + sum(len(ln.speech) for ln in lines))  # 間の揺らぎは台本ごとに決まった値（作り直しても同じ）
    jitter = [round(rng.uniform(0.9, 1.1), 2) if VOICE_ROLES_ON else 1.0 for _ in lines]
    pre_gap = [rng.uniform(0.22, 0.30) if VOICE_ROLES_ON else GAP for _ in lines]
    for _ in range(5):
        tts = v2.FakeTTS(0.135 * 1.22 / speed) if tts_kind == "fake" else make_tts(tts_kind, speed)

        def synth(i: int) -> np.ndarray:
            raw, dst = work / f"l{i}.src.wav", work / f"l{i}.wav"
            mult, inton, pitch, _pre, _post = VOICE_ROLES[roles[i]]
            if tts_kind == "fake":
                one = v2.FakeTTS(0.135 * 1.22 / (speed * mult))
            elif VOICE_ROLES_ON:
                one = make_tts(tts_kind, round(speed * mult, 3), intonation=inton, pitch=pitch, pause=jitter[i])
            else:
                one = tts
            one.synth(speech_for_tts(lines[i].speech), raw)
            v2._to_48k_trimmed(raw, dst)
            return v2._read_wav(dst)

        with ThreadPoolExecutor(max_workers=4) as pool:
            clips = list(pool.map(synth, range(len(lines))))
        t = LEAD
        prev_post = 0.0
        for k, (line, clip) in enumerate(zip(lines, clips)):
            if k:
                pre = VOICE_ROLES[roles[k]][3]
                t += max(prev_post, pre if pre is not None else pre_gap[k]) if VOICE_ROLES_ON else GAP
            line.start, line.end = t, t + len(clip) / SR
            t = line.end
            prev_post = VOICE_ROLES[roles[k]][4]
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
    voice = {"engine": tts.engine, "credit": tts.credit, "label": tts.label + ("・役目ごとの速さと間" if VOICE_ROLES_ON else ""), "speed": round(speed, 2), "roles": VOICE_ROLES_ON}
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


def clamp_cam(page: Page, page_no: int, cx: float, cy: float, s: float, over: float = 0.0) -> Cam:
    """カメラをページの中に収める。over（画面の画素）だけは左右にページの外（地の色）を見せてよい
    （ページの左端近くから始まる行に、余白をつけて寄るため。frame_box が使う）"""
    pw, ph = page.width, page.height
    if pw * s >= PW:
        cx = min(max(cx, (FOCUS[0] - over) / s), pw - (PW - FOCUS[0] - over) / s)
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


# 2026-11-04 オーナー「証拠の文が画面の端に寄って、行の頭が切れる（…締役、AKARI…）」:
# 寄るときは、証拠の文が触れている行を行の頭から（寄りの段では行の頭〜証拠の文の終わりまで）まるごと画面に入れ、
# 周りに余白を残して真ん中に置く。入らないほど寄らない（読める大きさまで引く）
FRAME_MARGIN_X = 96  # 出力の画素。左右の端は EDGE_FADE（64）で地の色になじむので、それより内側に文字を置く
FRAME_MARGIN_Y = 44
QA_MARGIN_X = 70  # EDGE_FADE（64）＋少し。 ゲート（QA）が見る余白（少しのずれは許す）
QA_MARGIN_Y = 16


def row_extents(tg: "Target") -> list[tuple]:
    """target が触れている行ごとの、行の頭から終わりまでの枠（同じブロックの文字の枠から）"""
    out = []
    chars = [r for _, r in tg.block.chars if r]
    for r in tg.rows or [tg.match or tg.rect]:
        cy = (r[1] + r[3]) / 2
        same = [c for c in chars if c[1] <= cy <= c[3]]
        out.append((min([c[0] for c in same] + [r[0]]), r[1], max([c[2] for c in same] + [r[2]]), r[3]))
    return out


def line_box(tg: "Target") -> tuple:
    """証拠の文が触れている行の全体（行の頭から終わりまで）"""
    return _union(row_extents(tg))


def view_box(tg: "Target") -> tuple:
    """カメラに入れる所: 証拠の文の行（縦）× 同じ段落の幅いっぱい（横）。2026-11-04 の目視の確認: 選択した文は入っていても、
    同じ段落の前後の行が右端で切れて（「applicati」「determined」）続きが読めない、と落ちた → 段落の横幅は必ず入れる"""
    lb = line_box(tg)
    br = tg.block.rect
    return (min(lb[0], br[0]), lb[1], max(lb[2], br[2]), lb[3])


def zoom_box(tg: "Target") -> tuple:
    """寄るときに必ず入れる所: 最初の行の頭 〜 証拠の文の終わり（行の頭は切らない）"""
    rows = tg.rows or [tg.match or tg.rect]
    full = row_extents(tg)
    return (min(r[0] for r in full), min(r[1] for r in rows), max(r[2] for r in rows), max(r[3] for r in rows))


def frame_box(page: Page, page_no: int, box: tuple, zmax: float, mx: float = FRAME_MARGIN_X, my: float = FRAME_MARGIN_Y) -> Cam:
    """box がまるごと余白つきで入る、いちばん大きな倍率（ページ幅いっぱいの zmax 倍まで）で、box の真ん中を注目の位置に置く。
    ページ幅いっぱいでも入らないときは、ページ幅の 0.8 倍まで引く（それでも入らなければゲートが落とす）"""
    s0 = fit_s(page)
    bw, bh = max(box[2] - box[0], 1), max(box[3] - box[1], 1)
    half_h = min(FOCUS[1], PH - FOCUS[1])
    s = min(s0 * zmax, (PW - 2 * mx) / bw, (2 * half_h - 2 * my) / bh)
    s = max(s / (1 + DRIFT_MAX), s0 * 0.8)  # 止めている間にゆっくり寄る分（DRIFT_MAX）を先に引いておく
    # 2026-11-04: ページの左端近く（x=48 など）から始まる行は、ページの中に収めると左の余白が取れず QA(b) に落ちていた
    # → 左右は余白の分だけページの外（地の色・端はなじませる）を見せてよい（over=mx）。
    # ページが画面より細いときは、ページの真ん中ではなく証拠の文の真ん中に置く。いちばん寄ったとき（DRIFT_MAX）に余白が足りなければ少しずつ引く
    bx, by = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    cam = clamp_cam(page, page_no, bx, by, s, over=mx)
    for _ in range(10):
        cam = clamp_cam(page, page_no, bx, by, s, over=mx)
        if page.width * s < PW:
            slack = (PW / s - page.width) / 2
            cam = Cam(page_no, min(max(bx, page.width / 2 - slack), page.width / 2 + slack), cam.cy, s)
        top = Cam(cam.page, cam.cx, cam.cy, cam.s * (1 + DRIFT_MAX))
        v = cam_view(top)
        mx_p, my_p = (QA_MARGIN_X + 6) / top.s, (QA_MARGIN_Y + 4) / top.s
        if (box[0] >= v[0] + mx_p and box[2] <= v[2] - mx_p and box[1] >= v[1] + my_p and box[3] <= v[3] - my_p) or s <= s0 * 0.62:
            break
        s *= 0.96
    return cam


def cam_view(cam: Cam) -> tuple:
    """カメラに写るページの範囲（ページの画素）"""
    return (cam.cx - FOCUS[0] / cam.s, cam.cy - FOCUS[1] / cam.s, cam.cx + (PW - FOCUS[0]) / cam.s, cam.cy + (PH - FOCUS[1]) / cam.s)


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
    extra: dict = field(default_factory=dict)  # 型のカード（video_templates）の追加の情報（写真の場所など）


@dataclass
class Chart:
    t0: float
    t1: float
    base: str
    subject: str
    ratio: str


DRIFT = 0.008  # 止まっている間もごくゆっくり寄る（1秒に0.8%。2026-11-03 動きを控えめに）
DRIFT_MAX = 0.04  # 2026-11-04: ゆっくり寄るのは4%まで（長く止めても証拠の文が画面の端へはみ出さない。frame_box はこの分の余白を先に取る）


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
        # 4%（DRIFT_MAX）まで寄ったら、同じ速さでゆっくり戻る（行き来する）。止めっぱなしにすると
        # 長く止める場面で絵がまったく動かず、ゲートの「動きが足りない」に落ちていた（2026-11-04）
        x = DRIFT * max(0.0, t - since) / DRIFT_MAX
        return Cam(cam.page, cam.cx, cam.cy, cam.s * (1 + DRIFT_MAX * (1 - abs(1 - (x % 2.0)))))  # 0→4%→0… を一定の速さで

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

    def marker_lines(self) -> list[dict]:
        """行ごとのマーカー（2026-11-04 オーナー「マーカーが文字の右端よりずっと先まで出ている」）。
        実際に描いた文字の枠（行ごと・アルファで測る）にぴったり合わせ、左右の余白は小さく同じだけ（0.15em・6px・行幅の5% のいちばん小さい値）。
        戻り値 [{"glyph": [x0, y0, x1, y1], "marker": [x0, y0, x1, y1]}]（スプライトの中の座標）"""
        cached = getattr(self, "_marker_lines", None)
        if cached is not None:
            return cached
        out = []
        for x0, y0, x1, y1 in line_boxes(self.sprite):
            lh, lw = y1 - y0, x1 - x0
            pad = int(max(2, min(6, 0.15 * lh, 0.05 * lw)))
            mh = int(lh * 0.34)
            my = y1 - mh - int(lh * 0.06)
            out.append({"glyph": [x0, y0, x1, y1], "marker": [x0 - pad, my, x1 + pad, my + mh]})
        self._marker_lines = out
        return out

    def bbox(self) -> list[float]:
        a = self.sprite[..., 3]
        ys, xs = np.nonzero(a > 8)
        if not len(xs):
            return [self.x, self.y, self.x, self.y]
        return [float(self.x + xs.min()), float(self.y + ys.min()), float(self.x + xs.max() + 1), float(self.y + ys.max() + 1)]


def line_boxes(sprite: np.ndarray) -> list[tuple[int, int, int, int]]:
    """スプライト（RGBA）の文字の行ごとの枠 (x0, y0, x1, y1)。縦に3px 以上あいた所で行を分け、行ごとに左右の端を測る"""
    a = sprite[..., 3] > 8
    rows = np.nonzero(a.any(axis=1))[0]
    if not len(rows):
        return []
    groups, start, prev = [], int(rows[0]), int(rows[0])
    for r in rows[1:]:
        r = int(r)
        if r - prev > 2:
            groups.append([start, prev])
            start = r
        prev = r
    groups.append([start, prev])
    tall = max(b - a_ + 1 for a_, b in groups)
    merged: list[list[int]] = []
    for g in groups:  # 点や線だけの細い切れ端（「二」の上の線など）は隣の行に足す
        if merged and ((g[1] - g[0] + 1) < 0.3 * tall or (merged[-1][1] - merged[-1][0] + 1) < 0.3 * tall):
            merged[-1][1] = g[1]
        else:
            merged.append(list(g))
    out = []
    for y0, y1 in merged:
        cols = np.nonzero(a[y0 : y1 + 1].any(axis=0))[0]
        out.append((int(cols[0]), int(y0), int(cols[-1]) + 1, int(y1) + 1))
    return out


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
    # 決めた大きさで入らなければ、さらに小さく（24px まで）。それでも入らないときだけ止める（長い数字・単位で描画が止まらないように）
    smallest = min(sizes) if len(sizes) else 40
    for size in list(sizes) + list(range(smallest - 2, 23, -2)):
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
    for rows_max, sizes in ((1, range(86, 58, -2)), (2, range(86, 62, -2)), (3, range(80, 60, -2)), (2, range(60, 48, -2))):
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
    row_top, row_bottom = top, bottom
    if sub.block:  # 2026-11-04 オーナー: 吹き出しは選択した文の続きの行（同じ段落の下の行）にもかぶせない → 下に置くなら段落の下に
        bx0, by0 = cam.map(sub.block[0], sub.block[1])
        bx1, by1 = cam.map(sub.block[2], sub.block[3])
        bottom = max(bottom, min(by1, PH))
        top = min(top, max(by0, 0)) if by0 >= 0 else top
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
    last = [r for r in vis if (r[3] >= row_bottom - 2 if up else r[1] <= row_top + 2)][0]
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
    block: tuple = ()  # 選択した英文の段落全体（ページの座標）。吹き出しは続きの行にもかぶせない（2026-11-04 オーナー）


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
    highlights: list = field(default_factory=list)  # 証拠の文の選択（ゲートが長さと中身を見る）
    camera_moves: list = field(default_factory=list)  # カメラの動き（ゲートが回数と割合を見る）
    qa: list = field(default_factory=list)  # 選択と寄りの位置（geometry_records → ゲートの QA）


def _source_label(page: Page) -> str:
    return f"記事の画面　{page.host}" if page.own else f"公式ページ　{page.host}"


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
    snap_targets(script, pages)  # 古い台本・型の台本も、選択は意味のまとまりで（2026-11-04）
    align_subs(script)  # 証拠に無い補足は出さない
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
    # 型のカード（video_templates.decorate が足す。既定の型の台本には無い＝今までと同じ）
    card_map = {int(c.get("beat") or 0): c for c in normalize_cards(script) if int(c.get("beat") or 0) and (c.get("rows") or c.get("mode") == "image")}
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
    move_log: list[dict] = []  # カメラの動き（ゲートが1場面の回数と動いている時間の割合を見る）

    def ev(t: float, kind: str, note: str = "") -> None:
        events.append({"t": round(max(0.0, t), 3), "kind": kind, "note": note})

    # つかみ: ページ幅いっぱい → 0.18秒から payoff の数字へ寄る（1秒で数字が大きく見える）
    t0 = targets[0]
    p0 = pages[t0.page]
    wide_rect = t0.block.rect if t0.block.rect[2] - t0.block.rect[0] > 0.5 * p0.width else t0.rect
    first = frame_box(p0, t0.page, (min(wide_rect[0], line_box(t0)[0]), line_box(t0)[1], max(wide_rect[2], line_box(t0)[2]), line_box(t0)[3]), 1.5)
    hero = hero_block(p0)
    if t0.block.font_px < 22 and hero is not None and hero is not t0.block:
        first = frame_rect(p0, t0.page, hero.rect, 0.96, 1.4)
    # 数字に寄るときは大きく、見出し（数字なし）は全体が入る程度に寄る（見出しの途中で切らない）
    # 2026-11-04: 数字だけに寄って行の頭や文の続きを切らない。証拠の文（行の頭から）がまるごと入る所まで寄る
    hook_close = frame_box(p0, t0.page, view_box(t0), ZOOM_MAX)
    moves.append(Move(0.18, 0.8, hook_close))
    ev(0.18, "move", "つかみ: 数字へ寄る")
    # つかみの訳は、同じ英文を beat でも訳すなら出さない（同じ訳が2回続かないように）
    hook_xl = is_english(hook.get("target", "")) and bool(hook.get("ja")) and all(_norm(b.get("target", "")) != _norm(hook.get("target", "")) for b in beats)
    # 選択は証拠の文の全部の行（日本語も。1行目の終わりの数文字だけにしない）
    spots.append(Spot(0.7, lines[1].start - 0.08, t0.page, t0.span, list(t0.rows or [t0.match]), 0.92))
    qa_items: list[dict] = []  # 選択と寄りの位置の記録（ゲートの QA が見る）

    def qa_add(beat_no: int, tg_: "Target", sel_: list, t_sel: float, t_end: float, item: dict, keyword: str, sub: str, speech: str) -> None:
        qa_items.append({"beat": beat_no, "page": tg_.page, "rows": [list(map(float, r)) for r in (tg_.rows or [tg_.match or tg_.rect])],
                         "line": list(map(float, line_box(tg_))), "sel": [list(map(float, r)) for r in sel_], "t0": float(t_sel), "t1": float(t_end),
                         "target": item.get("target", ""), "ja": item.get("ja", "") or "", "block_text": tg_.block.text, "keyword": keyword, "sub": sub, "speech": speech})

    qa_add(0, t0, list(t0.rows or [t0.match]), 0.92, lines[1].start - 0.08, hook, hook.get("payoff", ""), "", hook.get("speech", ""))
    clicks.append(Click(0.92, t0.page, *click_point(t0)))
    ev(0.92, "click")
    if hook_xl:
        sub_spans.append((1.25, lines[1].start - 0.08, 0))

    # 2026-11-03 オーナー「右の画面の動きが多すぎるときがある」→ 動きの予算:
    #   1場面のカメラの動きは「行へ」1回＋証拠に寄る1回まで・動いたあとは1.5秒以上止める・行ったり来たりしない・ゆっくり動かす。
    #   止まっている間の変化は、選択の青が文をなぞる・訳の吹き出し・補足の文字で出す（カメラは動かさない）
    # 「日本語のページでどこを読んでいるか分からない」→ 日本語の証拠も英語と同じく、読んでいる文に選択の青を付け、読み終わるまで画面に入れておく
    MOVE = 0.9  # 1回の動きの長さ（ゆっくり）
    HOLD = 1.5  # 動いたあと止めておく最短
    highlights: list[dict] = []

    def last_end() -> float:
        return max((m.t + m.dur for m in moves), default=0.0)

    def add_move(t: float, cam: Cam, note: str, beat_no: int, cut: bool = False, dur: float = MOVE) -> bool:
        if t < last_end() + HOLD - 1e-6:  # 前の動きのあと1.5秒たっていない
            return False
        moves.append(Move(t, 0.5 if cut else dur, cam, cut=cut))
        move_log.append({"t": round(t, 3), "dur": 0.5 if cut else dur, "beat": beat_no, "note": note})
        ev(t, "cut" if cut else "move", note)
        return True

    move_log[:] = [{"t": 0.18, "dur": 0.8, "beat": 0, "note": "つかみ: 数字へ寄る"}]
    for i, (beat, line) in enumerate(zip(beats, lines[1 : n_beats + 1]), 1):
        tg = targets[i]
        page = pages[tg.page]
        nxt = lines[i + 1].start
        a_t = max(line.start - 0.16, last_end() + HOLD)
        cut = tg.page != moves[-1].cam.page
        wide_block = tg.block.rect[2] - tg.block.rect[0] > 0.5 * page.width
        # 証拠の文（target の全行）に選択の青。英語なら訳の吹き出しも
        xl = found[i] and is_english(beat.get("target", "")) and len((beat.get("ja") or "").strip()) >= 10  # 短すぎる訳は出さない
        span = tg.span if found[i] else tg.rect
        sel = tg.rows if found[i] else [tg.match]
        line_rect = (tg.block.rect[0], span[1], tg.block.rect[2], span[3]) if wide_block else span
        # 行へ: 証拠の文が触れている行を、行の頭から終わりまでまるごと（余白つき・真ん中）
        block_cam = frame_box(page, tg.page, view_box(tg), 1.6) if found[i] else frame_rect(page, tg.page, line_rect, 0.96, 1.6, zmin=1.25 if wide_block else ZOOM_BLOCK)
        chart_beat = i in lad_map or i in flow_map or i == uni_beat or i == comp_beat or i in card_map
        if i in lad_map:  # 料金の段階: 証拠は主役のプランの価格の行（表の行を順に回らない）
            lad = lad_map[i]
            sub_row = next((r for r in lad["rows"] if r.get("name", "").lower() == lad.get("subject", "").lower()), lad["rows"][-1])
            rt = find_target(pages, sub_row.get("target", ""), sub_row.get("block", ""), keyword=sub_row.get("price", ""))
            if rt is not None:
                tg, page = rt, pages[rt.page]
                span, sel = rt.span, list(rt.rows or [rt.match])
                block_cam = frame_box(page, rt.page, view_box(rt), 1.9)
                cut = rt.page != moves[-1].cam.page
        move_dur = MOVE if nxt - line.start >= 4.0 else 0.6  # 短い場面は少し速く（選択を見せる時間を残す）
        if not add_move(a_t, block_cam, f"beat{i}: 行へ", i, cut=cut, dur=move_dur):
            a_t = last_end() + HOLD
            add_move(a_t, block_cam, f"beat{i}: 行へ", i, cut=cut, dur=move_dur)
        arrive = a_t + (0.5 if cut else move_dur)
        sel_t = arrive + 0.15
        sweep = 0.35 + 0.12 * (len(sel) - 1)
        ev(sel_t, "highlight", f"beat{i}: 読んでいる文に選択の青")
        chart_t = None
        if chart_beat:
            chart_t = max(sel_t + sweep + 1.4, line.start + 0.25 * (nxt - line.start))  # 選択を1.2秒以上見せてからグラフ
            if nxt - chart_t < 1.6:
                chart_t = None
        spot_end = (chart_t - 0.05) if chart_t else nxt - 0.08
        spots.append(Spot(a_t + 0.35, spot_end, tg.page, span, sel, sel_t))
        if found[i]:
            ev_item = beat if tg is targets[i] else {"target": next((r.get("target", "") for r in lad_map.get(i, {}).get("rows", []) if find_target(pages, r.get("target", ""), r.get("block", "")) is tg), "") or tg.text, "ja": ""}
            qa_add(i, tg, sel, sel_t, spot_end, ev_item, beat.get("keyword", ""), beat.get("sub", ""), beat.get("speech", ""))
        if found[i]:
            highlights.append({"beat": i, "text": beat.get("target", ""), "ja": beat.get("ja", "") if xl else "", "keyword": beat.get("keyword", ""), "sub": beat.get("sub", ""),
                               "t_full": round(sel_t + sweep, 3), "t_end": round(spot_end, 3), "line": [round(line.start, 3), round(line.end, 3)]})
        if xl and spot_end - (sel_t + 0.4) > 0.6:
            sub_spans.append((sel_t + 0.4, spot_end, i))
            ev(sel_t + 0.4, "text", f"beat{i}: 訳")
        if chart_t is not None:
            if i in lad_map:
                lad = lad_map[i]
                t_hi = chart_t + 0.3 + 0.5 * len(lad["rows"]) + 0.3
                tour = nxt - t_hi > 4.0
                t_end = min(nxt - 0.06, t_hi + (1.8 * len(lad["rows"]) + 0.2 if tour else 2.4))
                L_new = Ladder(chart_t, t_end, lad["rows"], lad.get("subject", ""), 0.5, "price", "", lad.get("title", ""), lad.get("source", ""))
                L_new.tour = 1.8 if tour else 0.0
                ladders.append(L_new)
                ev(chart_t, "chart", "料金の段階のグラフ")
                if tour:
                    for k in range(len(lad["rows"])):
                        ev(t_hi + 1.8 * k, "pop", f"グラフ: {lad['rows'][k].get('name')}")
                else:
                    ev(t_hi, "pop", "グラフ: 主役を強調")
            elif i in flow_map:
                steps = [str(x) for x in flow_map[i].get("steps")][:4]
                t_hi = chart_t + 0.3 + 0.7 * len(steps) + 0.2
                t_end = min(nxt - 0.06, t_hi + 2.4)
                ladders.append(Ladder(chart_t, t_end, [{"name": x} for x in steps], "", 0.7, "flow", flow_map[i].get("note", ""), flow_map[i].get("title", ""), ""))
                ev(chart_t, "chart", "手順の絵")
                ev(t_hi, "pop", "手順: まとめ")
            elif i in card_map and not (i == uni_beat or i == comp_beat):
                cd = card_map[i]
                rows_c = list(cd.get("rows") or [])
                reveal = 0.5 if cd["mode"] != "image" else 0.0
                t_hi = chart_t + 0.3 + reveal * len(rows_c) + 0.3
                tour = cd["mode"] in ("specs", "checklist", "timeline") and nxt - t_hi > 1.6 * len(rows_c) + 0.6
                hold = 1.6 * len(rows_c) + 0.4 if tour else (3.4 if cd["mode"] == "image" else 2.4)
                t_end = min(nxt - 0.06, t_hi + hold)
                L_new = Ladder(chart_t, t_end, rows_c, cd.get("subject", ""), reveal, cd["mode"], cd.get("note", ""), cd.get("title", ""), cd.get("source", ""),
                               extra={k: cd[k] for k in ("image", "focus") if k in cd})  # fmt: skip
                L_new.tour = 1.6 if tour else 0.0
                ladders.append(L_new)
                ev(chart_t, "chart", f"型のカード: {cd['mode']}")
                if cd["mode"] == "image":  # 写真はゆっくり寄るだけ。1秒後に小さく出どころ（画像：◯◯（公式発表より））
                    ev(chart_t + 1.0, "text", "カード: 写真の出どころ")
                elif tour:
                    for k in range(len(rows_c)):
                        ev(t_hi + 1.6 * k, "pop", f"カード: {k + 1}行目")
                else:
                    ev(t_hi, "pop", "カード: 強調")
                if nxt - t_end > 1.2:  # カードが消えて証拠の画面に戻る（大きく絵が変わる所）
                    ev(t_end + 0.05, "chart", "カード: 画面へ戻る")
                    # 戻った画面にも選択の青と訳を出す（青も訳も無い英語の画面を残さない。2026-11-04 オーナー）
                    spots.append(Spot(t_end + 0.1, nxt - 0.08, tg.page, span, sel, t_end + 0.3))
                    if found[i]:
                        qa_add(i, tg, sel, t_end + 0.3, nxt - 0.08, beat, beat.get("keyword", ""), beat.get("sub", ""), beat.get("speech", ""))
                    if xl and nxt - 0.08 - (t_end + 0.4) > 0.6:
                        sub_spans.append((t_end + 0.4, nxt - 0.08, i))
            elif i == uni_beat:
                t_hi = chart_t + 0.3 + 0.5 * len(uni_rows) + 0.3
                t_end = min(nxt - 0.06, t_hi + 2.2)
                ladders.append(Ladder(chart_t, t_end, uni_rows, uni.get("subject", ""), 0.5, "units", uni.get("unit", "")))
                ev(chart_t, "chart", "たとえの絵")
                ev(t_hi, "pop", "たとえ: 主役を強調")
            else:
                t_end = min(nxt - 0.06, chart_t + 3.2)
                charts.append(Chart(chart_t, t_end, comp["base"], comp["subject"], comp["ratio"]))
                ev(chart_t, "chart", "比較のグラフ")
            if nxt - t_end > 1.2:  # グラフのあとは、証拠の行の画面のまま（選択も戻す）
                spots.append(Spot(t_end + 0.2, nxt - 0.08, tg.page, span, sel, t_end + 0.2))
            continue
        # 証拠に寄る（1場面に1回）: 行に着いて1.5秒以上たってから・寄ったあとも1.5秒以上止められるときだけ
        zoom_t = max(sel_t + sweep + 0.6, arrive + HOLD)
        if nxt - zoom_t >= 0.9 + HOLD:
            # 寄る: 最初の行の頭〜証拠の文の終わりがまるごと入る所まで。行へ着いたときより1割以上寄れないなら動かさない
            # 2026-11-04（AI の目視の確認）: 行の終わりを切っても「続きが見えない」になる → 寄りも行の全体（短い行・表の欄のときだけ寄れる）
            cam_z = frame_box(page, tg.page, view_box(tg), ZOOM_MAX) if found[i] else block_cam
            if cam_z.s >= block_cam.s * 1.1 and add_move(zoom_t, cam_z, f"beat{i}: 証拠に寄る", i):
                clicks.append(Click(zoom_t + 0.6, tg.page, *click_point(tg)))
                ev(zoom_t + 0.6, "click")

    # 締め: 最初の画面へ戻る（1回）→ 止めてから最初の数字へ寄る（1回）
    concl = lines[-1]
    n_last = n_beats + 1
    back_t = max(concl.start - 0.16, last_end() + HOLD)
    add_move(back_t, Camera._hold(first, 0, 0), "締め: 最初の画面へ戻る", n_last, cut=first.page != moves[-1].cam.page)
    # 締めで最初の数字へ寄る時刻（2026-11-04: 早めに。選択の青と訳の無い画面を4秒より長く続けない）
    c_t = max(back_t + MOVE + HOLD, concl.start + 0.2 * (concl.end - concl.start))
    if narr.end_start - c_t > 0.9 + 1.0 and add_move(c_t, hook_close, "締め: 最初の数字へ", n_last):
        spots.append(Spot(c_t + 0.35, narr.end_start, t0.page, t0.span, list(t0.rows or [t0.match]), c_t + 0.5))
        qa_add(n_last, t0, list(t0.rows or [t0.match]), c_t + 0.5, narr.end_start, hook, hook.get("payoff", ""), "", hook.get("speech", ""))
        if hook_xl and narr.end_start - (c_t + 0.6) >= 1.0:  # 締めで英文に戻ったら、訳の吹き出しも出す（訳の無い英語の画面にしない。2026-11-04）
            sub_spans.append((c_t + 0.6, narr.end_start, 0))
    # 3.6秒より長く何も変わらない所は、カーソルが読んでいる文を指す（カメラは動かさない）。左端と右端を交互に
    # （2026-11-04 オーナー「ただの読み聞かせ」: 4秒を超えて止まった画面を作らない。ゲートは video_gate.CALM_MAX_SEGMENT＝4.0秒）
    cover = [(c.t0, c.t1) for c in charts] + [(L.t0, L.t1) for L in ladders]
    camera = Camera(first, moves)
    side = 0
    for _ in range(3):
        changes = sorted({e["t"] for e in events if e["kind"] in ("move", "cut", "chart", "pop", "highlight", "text", "click")})
        kept: list[float] = []  # ゲートと同じく、0.7秒以内に続いた変化は1回と数える（その分、間が長く見える）
        for c_ in changes:
            if not kept or c_ - kept[-1] >= 0.7:
                kept.append(c_)
        bounds = [0.0] + kept + [narr.end_start]
        added = False
        for a, b in zip(bounds, bounds[1:]):
            if b - a <= 3.6 or any(c0 - 0.2 <= (a + b) / 2 <= c1 + 0.2 for c0, c1 in cover):
                continue
            t = (a + b) / 2
            cam = camera.at(t)
            sp = next((x for x in spots if x.page == cam.page and x.t0 <= t <= x.t1 and x.sel), None)
            if sp is None:  # 選択が出ていない所（締めなど）は、同じページで最後に読んだ文を指す
                sp = max((x for x in spots if x.page == cam.page and x.t0 <= t and x.sel), key=lambda x: x.t0, default=None)
            if sp is None:
                continue
            # 2026-11-04: カーソルが前に指した所のすぐ近くを指すと、絵がほとんど変わらずゲートに落ちていた（読んでいる文を指す）。
            # 読んでいる文の行の左端・右端から、画面の上で前のカーソルの位置からいちばん遠い所を選ぶ（画面の外は選ばない）
            prev = [c for c in clicks if c.page == cam.page and c.t < t]
            last = max(prev, key=lambda c: c.t) if prev else None
            px, py = cam.map(last.x, last.y) if last else (PW + 260.0, PH + 420.0)
            cands = []
            for r in sp.sel:
                h = r[3] - r[1]
                for k, x in enumerate((r[2] + h * 0.15, r[0] + h * 0.3)):
                    sx, sy = cam.map(x, r[1] + h * 0.7)
                    if 40 <= sx <= PW - 60 and 40 <= sy <= PH - 70:
                        cands.append((math.hypot(sx - px, sy - py), (k + side) % 2, x, r[1] + h * 0.7))
            if not cands:
                r = sp.sel[-1] if side % 2 == 0 else sp.sel[0]
                h = r[3] - r[1]
                cands = [(0.0, 0, r[2] + h * 0.15 if side % 2 == 0 else r[0] + h * 0.3, r[1] + h * 0.7)]
            _, _, x, y = max(cands, key=lambda c: (c[0], -c[1]))
            clicks.append(Click(t, cam.page, x, y))
            ev(t, "click", "読んでいる文を指す")
            side += 1
            added = True
        if not added:
            break
    clicks.sort(key=lambda c: c.t)
    camera = Camera(first, moves)

    # 文字（左の列）: 小さな見出し → キーワード（マーカー）→ 補足。キーワードの中心は画面の注目の行と同じ高さ
    texts: list[TextItem] = []
    logo = render_text([(BRAND, "logo", INK)], 32)
    # 短い版は締めでブランドを案内の下に出すので、左上のロゴはそこで消す（同じロゴが2つ並ばないように）
    logo_end = lines[-1].start - 0.1 if (script.get("conclusion") or {}).get("cta") else narr.total + 1
    texts.append(TextItem("logo", BRAND, logo, LOGO_XY[0], LOGO_XY[1], 0.0, logo_end, anim="none"))
    hook_spr = big_lines(hook["text"])
    hook_end = lines[0].end if lines[0].end <= 2.0 else min(HOOK_TEXT_MAX, lines[0].end - 0.5)
    (_, _, hx, hy), = column_group([("hook", hook_spr, 0)], ANCHOR_Y)
    texts.append(TextItem("hook", hook["text"], hook_spr, hx, hy, 0.0, hook_end, anim="none"))
    if hook.get("payoff") and lines[1].start - 0.06 - hook_end >= 0.5:
        # 1行で 84px 以上に入らなければ2行に（文節で折り返す）。2026-11-04 から payoff はニュースの中身の言い回しで長め。
        # fit_line は入らないと 24px まで小さくするので使わない（36px になってゲートの「文字が小さすぎる」に落ちた）
        pay = next((spr for size in range(150, 83, -4) if (spr := render_text(accent_segments(hook["payoff"], "black"), size)).shape[1] <= COL_W), None)
        if pay is None:
            pay = wrapped(hook["payoff"], "black", range(84, 52, -2), COL_W, 2)
        (_, _, px, py), = column_group([("keyword", pay, 0)], ANCHOR_Y)
        texts.append(TextItem("keyword", hook["payoff"], pay, px, py, hook_end, lines[1].start - 0.06, anim="wipe", marker=True))
        ev(hook_end, "pop", "つかみの数字")
    for i, (beat, line) in enumerate(zip(beats, lines[1 : n_beats + 1]), 1):
        t_in, t_out = line.start - 0.1, lines[i + 1].start - 0.06
        label = (beat.get("label") or EYEBROWS[min(i, len(EYEBROWS)) - 1]).strip()
        brow = fit_line(label, "bold", range(28, 20, -2), COL_W, color=BLUE, accent=False, tracking=0.08)
        # 1行で 60px 以上に入らなければ2行に（文節で折り返す）。fit_line は 24px まで縮めるので使わない（36px でゲートに落ちた。2026-11-04）
        kw = next((spr for size in range(132, 59, -4) if (spr := render_text(accent_segments(beat["keyword"], "black"), size)).shape[1] <= COL_W), None)
        if kw is None:
            kw = wrapped(beat["keyword"], "black", range(76, 46, -2), COL_W, 2)
        parts = [("eyebrow", brow, 0), ("keyword", kw, 18)]
        if beat.get("sub"):
            parts.append(("sub", fit_line(beat["sub"], "bold", range(42, 30, -2), COL_W, color=MUTED, accent=False), 30))
        # 補足は、長い場面では読み上げの途中で出す（カメラを動かさずに画面が変わる）
        sub_delay = 0.22 if t_out - t_in < 5.0 else round(0.42 * (t_out - t_in), 2)
        for role, spr, x, y in column_group(parts, ANCHOR_Y):
            text = {"eyebrow": label, "keyword": beat["keyword"], "sub": beat.get("sub", "")}[role]
            texts.append(TextItem(role, text, spr, x, y, t_in, t_out, anim="wipe" if role == "keyword" else "rise", delay={"eyebrow": 0.0, "keyword": 0.06, "sub": sub_delay}[role], marker=role == "keyword"))
        ev(t_in, "text", f"beat{i}: キーワード")
        if beat.get("sub") and sub_delay > 1.0:
            ev(t_in + sub_delay, "text", f"beat{i}: 補足")
    concl_info = script["conclusion"]
    brow_text = concl_info.get("eyebrow") or "要約すると"
    concl_brow = fit_line(brow_text, "bold", range(28, 20, -2), COL_W, color=BLUE, accent=False, tracking=0.08)
    concl_spr = big_lines(concl_info["text"])
    parts = [("eyebrow", concl_brow, 0), ("conclusion", concl_spr, 18)]
    labels = {"eyebrow": brow_text, "conclusion": concl_info["text"]}
    if concl_info.get("cta"):  # 短い版: 投稿のリンクへの案内（マーカー付き）とブランド
        parts.append(("cta", fit_line(concl_info["cta"], "black", range(44, 30, -2), COL_W, color=BLUE, accent=False), 28))
        parts.append(("brand", render_text([(BRAND, "logo", INK)], 36), 30))
        labels.update({"cta": concl_info["cta"], "brand": BRAND})
    for role, spr, x, y in column_group(parts, ANCHOR_Y):
        delay = {"eyebrow": 0.0, "conclusion": 0.08, "cta": 0.5, "brand": 0.75}[role]
        texts.append(TextItem("sub" if role in ("cta", "brand") else role, labels[role], spr, x, y, concl.start - 0.1, narr.total + 1,
                              anim="wipe" if role == "cta" else "rise", delay=delay, marker=role == "cta"))
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
    official_hosts = [p.host for p in pages if not p.own]
    if not official_hosts:  # 自分の記事だけで作ったときは、出典の公式ページを書く
        src = article.get("sourceUrl") or ""
        official_hosts = [urllib.parse.urlparse(src).netloc.removeprefix("www.")] if src else []
    hosts = "・".join(dict.fromkeys(official_hosts)) + ("・本記事" if any(p.own for p in pages) else "")
    credit = f"{voice_text}　画面 {hosts}（公式）"
    try:
        host_row = fit_line(f"画面 {hosts}（公式）", "bold", range(22, 15, -1), COL_W, color=SOFT, accent=False)
    except RuntimeError:  # ページが多いときは2行に
        host_row = wrapped(f"画面 {hosts}（公式）", "bold", range(20, 14, -1), COL_W, 2, color=SOFT, accent=False)
    rows = [fit_line(voice_text, "bold", range(22, 16, -1), COL_W, color=SOFT, accent=False), host_row]
    spr = stack(rows, 8)
    texts.append(TextItem("credit", credit, spr, COL[0], SOURCE_Y - spr.shape[0] + rows[0].shape[0], credit_t, narr.total + 1, anim="rise"))
    credits = [credit]
    if script.get("_image_credit") and any(L.mode == "image" for L in ladders):  # 公式の写真を出したら、その出どころもクレジットに
        credits.append(script["_image_credit"])

    # 日本語字幕（英語の文に寄っている間）: 画面の下に中央そろえ
    subtitles: list[Subtitle] = []
    for a, b, idx in sub_spans:
        text = (items[idx].get("ja") or "").strip()
        tg = targets[idx]
        subtitles.append(Subtitle(a, b, text, callout_sprite(text), tg.page, list(tg.rows or [tg.match or tg.rect]), tuple(tg.block.rect)))

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
    plan = Plan(article, script, narr, pages, targets, camera, spots, clicks, charts, texts, subtitles, events, focus, credits, ladders, highlights, move_log)
    plan.qa = qa_items
    return plan


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


TEMPLATE_CARD_MODES = ("image", "specs", "checklist", "timeline", "figures", "versus")  # video_templates.CARD_MODES と同じ


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
        if L.mode in TEMPLATE_CARD_MODES:  # 型のカード（video_templates）
            import video_templates

            return video_templates.card_base(self, L)
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
        if L.mode in TEMPLATE_CARD_MODES:
            import video_templates

            return video_templates.card_frame(self, info, L, t)
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
                    if mk > 0 and k_out > 0.6:
                        # 行ごとに、その行の文字の幅だけ（2行の見出しの短い2行目も、その幅だけ）。左から順に走る
                        spans = item.marker_lines()
                        total_w = sum(m["marker"][2] - m["marker"][0] for m in spans) or 1
                        done = mk * total_w
                        for m in spans:
                            mx0, my0, mx1, my1 = m["marker"]
                            k_line = min(1.0, max(0.0, done / max(mx1 - mx0, 1)))
                            done -= mx1 - mx0
                            if k_line <= 0:
                                break
                            _highlight_marker(frame, item.x + mx0, item.y + my0 + dy_out, mx1 - mx0, my1 - my0, k_line)
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


QA_VERSION = 1


def geometry_records(plan: Plan, step: float = 0.1) -> list[dict]:
    """選択の青と寄りの位置の記録（ゲートの QA が見る）。証拠ごとに、選択が出ている間の止まった時刻のカメラの範囲（ページの画素）・
    選択の枠・証拠の文の行の枠・左のキーワードと読み上げ。どの時刻でも証拠の文がまるごと余白つきで写っているかをゲートが確かめる"""
    moves = plan.camera.moves

    def moving(t: float) -> bool:
        return any(m.t - 1e-6 <= t <= m.t + m.dur + 0.04 for m in moves)

    out = []
    for q in plan.qa:
        t0, t1 = q["t0"], q["t1"]
        times = [round(t0 + k * step, 3) for k in range(int(max(0.0, t1 - t0) / step) + 1)]
        views, peak = [], None
        for t in times:
            if moving(t):
                continue
            cam = plan.camera.at(t)
            if cam.page != q["page"]:
                views.append([t, -1, 0, 0, 0, 0])  # 別のページが写っている
                continue
            v = cam_view(cam)
            views.append([t, round(cam.s, 5), *[round(x, 1) for x in v]])
            # 目視の確認のコマは、選択が消える直前（最後の 0.2秒）を避ける（コマの取り出しが次のフレームになり、選択の無い絵を見せていた）
            if t <= t1 - 0.2 and (peak is None or cam.s > peak[1]):
                peak = (t, cam.s)
        tgt = q["target"]
        phrase = clause_ok(tgt, q["block_text"]) if is_english(tgt) else ja_phrase_ok(tgt, q["block_text"])
        out.append({k: v for k, v in q.items() if k != "block_text"} | {"views": views, "t_peak": round(peak[0], 3) if peak else round(t1, 3), "phrase_ok": bool(phrase),
                                                                      "margin": [QA_MARGIN_X, QA_MARGIN_Y]})
    return out


def text_records(plan: Plan) -> list[dict]:
    out = []
    rows = [(item.role, item.text, item.sprite, item.bbox(), item.t0, item.t1) for item in plan.texts]
    avoid: list = []  # 吹き出しごとの「かぶせてはいけない枠」（選択した文の行と、その段落）
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
            cam = plan.camera.at(wm)  # 選択した文とその段落（続きの行）の画面の枠。ゲートが吹き出しのかぶりを見る（2026-11-04）
            pts = [cam.map(r[0], r[1]) + cam.map(r[2], r[3]) for r in sub.rows] + ([cam.map(sub.block[0], sub.block[1]) + cam.map(sub.block[2], sub.block[3])] if sub.block else [])
            avoid.append([[round(PANEL[0] + max(0.0, q[0]), 1), round(PANEL[1] + max(0.0, q[1]), 1), round(PANEL[0] + min(float(PW), q[2]), 1), round(PANEL[1] + min(float(PH), q[3]), 1)] for q in pts])
    markers = {i: item.marker_lines() for i, item in enumerate(plan.texts) if item.marker}
    sub_no = 0
    for idx, (role, text, spr, bbox, t0, t1) in enumerate(rows):
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
        if role == "subtitle":
            out[-1]["avoid"] = avoid[sub_no] if sub_no < len(avoid) else []
            sub_no += 1
        if idx in markers:  # マーカーの枠と文字の枠（行ごと・画面の座標）。ゲートがはみ出しを見る
            it = plan.texts[idx]
            out[-1]["marker"] = True
            out[-1]["marker_lines"] = [{"glyph": [it.x + m["glyph"][0], it.y + m["glyph"][1], it.x + m["glyph"][2], it.y + m["glyph"][3]],
                                        "marker": [it.x + m["marker"][0], it.y + m["marker"][1], it.x + m["marker"][2], it.y + m["marker"][3]]}
                                       for m in markers[idx]]  # fmt: skip
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


def make_meta(a: dict, script: dict, voice: dict, pages: list[Page], image_credit: str = "") -> dict:
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
        lines.append(f"画面：{p.host}（本記事） {p.url}" if p.own else f"画面：{p.host}（公式ページ） {p.url}")
    if any(p.own for p in pages) and a.get("sourceUrl"):
        lines.append(f"出典：{a['sourceUrl']}")
    if image_credit:  # 型のカードで公式発表の写真を出したとき
        lines.append(image_credit)
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


# --------------------------------------------------------------------------- #
# QA（2026-11-04 オーナー「選択の青がずれる・意味の無い所に付く・行の頭が切れる・左の文字と話が合わない」）
#   位置の検査はゲート（video_gate.geometry_problems）。ここでは2本の動画のコマ（場面ごとに一番寄った所）を AI に見せて目視の確認をし、
#   だめなら台本を1回だけ作り直す。両方に通った動画だけ report["gate"]["qa"] が geometry=true・vision=true になる（投稿の条件）
# --------------------------------------------------------------------------- #

QA_RETRIES = int(os.environ.get("V3_QA_RETRIES", "1"))  # 作り直しの回数
QA_RETRY_BEFORE = float(os.environ.get("V3_QA_RETRY_BEFORE", "420"))  # 1回目にこれより時間がかかったら作り直さない（CI の枠を守る）
QA_RETRY_SCRIPT_CAP = float(os.environ.get("V3_QA_RETRY_SCRIPT_CAP", "300"))
VISION_MAX_FRAMES = 14

VISION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "frames": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "index": {"type": "INTEGER"},
                    "highlight_ok": {"type": "BOOLEAN"},
                    "framing_ok": {"type": "BOOLEAN"},
                    "coherent": {"type": "BOOLEAN"},
                    "issue": {"type": "STRING"},
                },
                "required": ["index", "highlight_ok", "framing_ok", "coherent", "issue"],
            },
        },
        # 2026-11-04 オーナー「始まりで置いていかれる」: 版ごとに、最初の8秒で詳しくない人が何のニュースか分かるか
        "opening": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"version": {"type": "STRING"}, "clear": {"type": "BOOLEAN"}, "issue": {"type": "STRING"}},
                "required": ["version", "clear", "issue"],
            },
        },
    },
    "required": ["frames", "opening"],
}
OPENING_SEC = 8.0  # 始まりの確認に見せる範囲（秒）
OPENING_FRAMES = (1.0, 4.0, 7.0)  # その中で見せるコマの時刻


def opening_text(rep: dict) -> str:
    """最初の8秒の読み上げと左の文字（つかみ＋beat 1）"""
    sv = rep.get("script_v3") or {}
    hook, beats = sv.get("hook") or {}, sv.get("beats") or []
    b1 = beats[0] if beats else {}
    return (f"読み上げ「{hook.get('speech', '')}」→「{b1.get('speech', '')}」 / 左の文字「{hook.get('text', '')}」→「{hook.get('payoff', '')}」"
            f"→ 見出し「{b1.get('label', '')}」キーワード「{b1.get('keyword', '')}」")


def vision_system() -> str:
    return """あなたは解説動画の厳しい最終チェック係。横長の動画のコマ（1枚＝1場面の、いちばん寄った瞬間）を見て、コマごとに次の3つを判定する。
画面の右は公式ページの本物の画面。読んでいる文に、ブラウザで文字を選んだときの薄い青（選択の青）が付いている。英語の文には黒い吹き出しで日本語の訳が出る。
画面の左には、場面の小見出しと大きなキーワード（数字は赤）と補足が出る。

1. highlight_ok: 選択の青が、意味の通る完全なまとまり（文・節・句）にぴったり付いているか。
   文字からずれている・文字の無い所にある・行の終わりの数文字だけ（例:「これま」）・語の途中で切れている・関係の無い別の文に付いている → false。
2. framing_ok: 選択した文がまるごと画面の中にあるか。行の頭が画面の左端で切れている（例:「…締役、AKARI」「…ナ、飲食店の」）・文が画面の端に寄って続きが見えない → false。
3. coherent: 左のキーワード・数字と、その場面の読み上げ（下に書く）と、選択した文（英語なら訳）が、同じことを言っているか。別の話なら false。

細かい好みではなく、見る人が「ずれている」「切れている」「話が合わない」と感じるものだけを false にする。

さらに版ごとに opening を判定する（最後に付ける「始まり」のコマと、最初の8秒の読み上げ・左の文字を見る）:
4. clear: このニュースに詳しくない人が、最初の8秒で「何のニュースで、なぜ大事か」が分かり、記事を読みたくなるか。
   問いかけだけ・説明の無い数字（例: 左に「99.9%」だけ）・説明の無い専門用語で始まる・何の話か分からない → false（issue に直し方）。
   さらに、忙しい人が5秒を過ぎても見続けたくなるか（要約の読み聞かせ・同じ画面が続くだけ・硬い言い回しなら false）。
false のときは issue に、何がどうだめかを短く日本語で書く（true のときは空文字）。すべてのコマについて index 順に答える。"""


def keyframe_jpeg(mp4: Path, t: float, width: int = 960) -> bytes:
    return subprocess.run(
        [shorts.ffmpeg_bin(), "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(mp4), "-frames:v", "1", "-vf", f"scale={width}:-2:flags=area",
         "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip


def qa_frames(report: dict) -> list[dict]:
    """目視の確認に見せるコマ: つかみと各場面の、選択が出ている間でいちばん寄った時刻（締めはつかみと同じ所なので除く）"""
    n = len((report.get("script_v3") or {}).get("beats") or [])
    return [g for g in report.get("geometry") or [] if 0 <= int(g.get("beat", 0)) <= n]


def qa_sheet(mp4: Path, marks: list[tuple[float, str]], out: Path, tw: int = 640) -> None:
    """QA の見比べ用: 指定した時刻のコマを並べて、下に場面とキーワードを書く"""
    th = tw * 9 // 16
    cols = 3
    rows = max(1, math.ceil(len(marks) / cols))
    sheet = Image.new("RGB", (cols * (tw + 12) + 12, rows * (th + 44) + 12), (226, 230, 237))
    d = ImageDraw.Draw(sheet)
    f = shorts.font("bold", 22)
    for i, (t, label) in enumerate(marks):
        raw = subprocess.run(
            [shorts.ffmpeg_bin(), "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(mp4), "-frames:v", "1", "-vf", f"scale={tw}:{th}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True,
        ).stdout  # fmt: skip
        x, y = 12 + (i % cols) * (tw + 12), 12 + (i // cols) * (th + 44)
        sheet.paste(Image.frombytes("RGB", (tw, th), raw[: tw * th * 3]), (x, y))
        d.text((x, y + th + 8), f"{t:.1f}s  {label}"[:40], font=f, fill=(20, 26, 40))
    sheet.save(out)


def _scene_name(g: dict, n: int) -> str:
    b = int(g.get("beat", 0))
    return "つかみ" if b == 0 else ("締め" if b > n else f"場面{b}")


def vision_review(versions: dict[str, dict]) -> tuple[dict[str, list[str]], str]:
    """2本の動画のコマをまとめて1回で AI に見せる。戻り値 ({版: だめだった点}, 確認できなかった理由)"""
    import llm

    images, metas = [], []
    for ver, rep in versions.items():
        mp4 = Path(rep.get("mp4") or "")
        if not mp4.exists():
            continue
        n = len((rep.get("script_v3") or {}).get("beats") or [])
        for g in qa_frames(rep):
            metas.append((ver, g, n))
    if len(metas) > VISION_MAX_FRAMES:  # 多すぎるときは短い版を優先して残す（SNS に出す版）
        metas = sorted(metas, key=lambda m: (m[0] != "short",))[:VISION_MAX_FRAMES]
    if not metas:
        return {}, "確認するコマが無い"
    lines = []
    for k, (ver, g, n) in enumerate(metas):
        images.append(keyframe_jpeg(Path(versions[ver]["mp4"]), float(g.get("t_peak", 0))))
        ja = f" / 訳: {g.get('ja')}" if g.get("ja") else ""
        lines.append(f"[{k}] {'短い版' if ver == 'short' else '全部の版'}・{_scene_name(g, n)}: 読み上げ「{g.get('speech', '')}」 / 左のキーワード「{g.get('keyword', '')}」補足「{g.get('sub', '')}」 / 選択した文「{g.get('target', '')}」{ja}")
    opening_lines = []
    for ver, rep in versions.items():
        mp4 = Path(rep.get("mp4") or "")
        if not mp4.exists():
            continue
        ks = []
        for t in OPENING_FRAMES:
            if t < float(rep.get("total") or OPENING_SEC):
                images.append(keyframe_jpeg(mp4, t))
                ks.append(str(len(images) - 1))
        opening_lines.append(f"- version={ver}（{'短い版' if ver == 'short' else '全部の版'}）: 始まりのコマ index {'・'.join(ks)}（frames では判定しない） / {opening_text(rep)}")
    prompt = ("コマは上の画像の順（index 0 から）。各コマの場面の情報:\n" + "\n".join(lines)
              + "\n\n# 始まり（最初の8秒）の確認（opening に version ごとに答える）\n" + "\n".join(opening_lines) + "\n\n判定を JSON で返してください。")
    try:
        verdict, _ = llm.generate_json_images(vision_system(), prompt, images, VISION_SCHEMA, purpose="video_review")
    except Exception as error:  # noqa: BLE001 - 確認できない＝合格にしない
        return {}, str(error)[:200]
    got = {int(f.get("index", -1)): f for f in verdict.get("frames") or []}
    issues: dict[str, list[str]] = {ver: [] for ver in versions}
    for k, (ver, g, n) in enumerate(metas):
        f = got.get(k)
        if f is None:
            issues[ver].append(f"{_scene_name(g, n)}: 判定が無い")
            continue
        bad = [name for key, name in (("highlight_ok", "選択の青"), ("framing_ok", "画面の切れ"), ("coherent", "話の食い違い")) if not f.get(key)]
        if bad:
            issues[ver].append(f"{_scene_name(g, n)}（{'・'.join(bad)}）: {str(f.get('issue', ''))[:80]}")
    opening = {str(o.get("version", "")): o for o in verdict.get("opening") or []}
    for ver in {m[0] for m in metas}:
        o = opening.get(ver)
        if o is None:
            issues[ver].append("始まり（最初の8秒）: 判定が無い")
        elif not o.get("clear"):
            issues[ver].append(f"始まり（最初の8秒で何のニュースか分からない）: {str(o.get('issue', ''))[:100]}")
    return issues, ""


def _save_report(rep: dict) -> None:
    mp4 = Path(rep.get("mp4") or "")
    if mp4.name:
        mp4.with_name(mp4.stem + ".report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), "utf-8")


def qa_note(versions: dict[str, dict]) -> str:
    """作り直しの台本に足す一言（前回の QA で見つかった問題）"""
    probs = []
    for ver, rep in versions.items():
        probs += [r.removeprefix("v3: ") for r in (rep.get("gate") or {}).get("reasons", []) if "QA" in r]
    probs = list(dict.fromkeys(probs))[:10]
    if not probs:
        return ""
    return ("\n\n# 前回の動画の確認で見つかった問題（必ず直す）\n" + "\n".join(f"- {p}" for p in probs)
            + "\n- 証拠（target）は、その場面の読み上げと左のキーワードが言っていることを、そのまま書いた完全な文・節にする（語や文の途中で切らない）。"
            + "\n- keyword の数字・言葉は、その場面の speech と target（英語なら ja）の両方に入れる。")


def qa_versions(slug: str, out: dict, regen=None, started: float | None = None) -> dict:
    """2本の動画に QA の目視の確認をして、report["gate"]["qa"] を決める。だめなら regen(note) で1回だけ作り直す"""
    started = started or time.time()
    global SCRIPT_TIME_CAP
    for attempt in range(QA_RETRIES + 1):
        ready = {k: r for k, r in out.items() if (r.get("gate") or {}).get("ok") and ((r.get("gate") or {}).get("qa") or {}).get("geometry")}
        issues, unavailable = vision_review(ready) if ready else ({}, "")
        for ver, rep in out.items():
            g = rep.setdefault("gate", {"ok": False, "reasons": []})
            qa = g.setdefault("qa", {"geometry": False, "vision": False, "version": QA_VERSION})
            if ver not in ready:
                qa["vision"] = False
                continue
            if unavailable:
                qa["vision"] = False
                g["reasons"] = list(g.get("reasons") or []) + [f"v3: QA(目視): AI の目視の確認ができない（{unavailable}）"]
            elif issues.get(ver):
                qa["vision"] = False
                g["reasons"] = list(g.get("reasons") or []) + [f"v3: QA(目視): {x}" for x in issues[ver]]
            else:
                qa["vision"] = True
            if not qa["vision"]:
                g["ok"] = False
            _save_report(rep)
        passed = {k: bool((r.get("gate") or {}).get("ok")) for k, r in out.items()}
        qa_failed = any(any("QA" in x for x in (r.get("gate") or {}).get("reasons", [])) for r in out.values())
        log(f"[v3] {slug}: QA {'合格' if all(passed.values()) else '不合格'} {passed}" + (f"（目視の確認ができない: {unavailable}）" if unavailable else ""))
        if all(passed.values()) or not qa_failed or unavailable or regen is None or attempt >= QA_RETRIES:
            return out
        if time.time() - started > QA_RETRY_BEFORE:
            log(f"[v3] {slug}: QA に落ちたが、時間が足りないので作り直さない（{time.time() - started:.0f}秒）")
            return out
        note = qa_note(out)
        log(f"[v3] {slug}: QA に落ちたので台本から1回だけ作り直す: {note[:300]}")
        saved = SCRIPT_TIME_CAP
        SCRIPT_TIME_CAP = min(SCRIPT_TIME_CAP, QA_RETRY_SCRIPT_CAP)
        try:
            out = regen(note)
        finally:
            SCRIPT_TIME_CAP = saved
    return out


def load_script(slug: str, article: dict, pages: list[Page], out_dir: Path, reuse: bool, note: str = "", cache: str = "") -> dict:
    """note: 型（video_templates）の一言を台本の決まりに足す（空なら今までと同じ）。cache: 台本を置くファイル名"""
    path = out_dir / (cache or f"{slug}.v3script.json")
    if reuse and path.exists():
        return latin_names(json.loads(path.read_text("utf-8")))
    script, model = write_script(article, pages, note) if note else write_script(article, pages)
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
                "image_credit": plan.credits[1] if len(plan.credits) > 1 else "",
                **({"template": plan.script["_template"]} if plan.script.get("_template") else {}),
                **({"min_changes": int(plan.script["_min_changes"])} if plan.script.get("_min_changes") else {}),
                "safe": list(SAFE_V3),
                "frame": [W, H],
                "layout": LAYOUT,
                "base_layout": BASE_LAYOUT,
                "subtitles": [{"t0": round(x.t0, 3), "t1": round(x.t1, 3), "text": x.text} for x in plan.subtitles],
                **({"auto_cards": True} if auto_cards_on() else {}),
                "fps": fps,
                "events": plan.events,
                "focus": plan.focus,
                "fonts": [FONT_FAMILY, LOGO_FAMILY],
                "rendered_strings": sorted(set(RENDERED)),
                "charts": [{"t0": round(c.t0, 3), "t1": round(c.t1, 3), "base": c.base, "subject": c.subject, "ratio": c.ratio} for c in plan.charts],
                "camera_moves": plan.camera_moves,
                "highlights": plan.highlights,
                "motion_budget": True,
                "ladders": [{"t0": round(L.t0, 3), "t1": round(L.t1, 3), "mode": L.mode, "unit": L.unit, "title": L.title, "subject": L.subject, "rows": [{k: r.get(k, "") for k in ("name", "price", "usage", "mult", "count") if k in r} for r in L.rows]} for L in plan.ladders],
                "official_text": official_text(plan.pages),
                "length_range": list(length_targets(plan.script)[2]),
                "variant": plan.script.get("_variant", "full"),
                "panel": list(PANEL),
                "targets": [{"page": t.page + 1, "block": t.block.id, "text": t.text, "block_text": t.block.text[:160]} if t else None for t in plan.targets],
                "geometry": geometry_records(plan),
                "qa_version": QA_VERSION,
                **make_meta(a, plan.script, plan.narr.voice, plan.pages, plan.credits[1] if len(plan.credits) > 1 else ""),
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
    # QA（2026-11-04）: 位置の検査（ゲートの中）と、AI の目視の確認（qa_versions が2本まとめて行う）。両方 true の動画だけ投稿に使う
    report["gate"]["qa"] = {"geometry": bool(res.metrics.get("qa_geometry")), "vision": False, "version": QA_VERSION, "v3": bool(res.metrics.get("qa_v3"))}
    if not report["gate"]["qa"]["geometry"] and res.ok:
        report["gate"]["ok"] = False
    report["sizeMB"] = round(mp4.stat().st_size / 1e6, 2) if mp4.exists() else 0
    report["mp4"] = str(mp4)
    report["timings"] = {**timer.stages, "total": round(time.time() - timer.t0, 1)}
    (out_dir / f"{name}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    log(f"[v3] {name}: {'合格' if res.ok else '不合格'} {report['timings']['total']}秒 {report['sizeMB']}MB {res.reasons[:4]}")
    return report


def prepare_versions(slug: str, a: dict, out_dir: Path, reuse: bool = False, extra_pages: list[str] | None = None, note: str = "", cache: str = "") -> tuple[list, dict | None, str]:
    """2本（サイト用・SNS 用）に共通の準備: ページの撮影と台本（1回だけ）。戻り値 (pages, script, 失敗の理由)"""
    out_dir.mkdir(parents=True, exist_ok=True)
    CAPTURE_FAILS.clear()
    reason = ""
    pages, script = [], None
    try:
        urls = page_urls(a, extra_pages)
        pages = capture_pages(urls) if urls else []
        source = v2.official_page_url(a)
        have_source = bool(source) and any(p.url == source for p in pages)
        if urls and not have_source:  # 出典の公式ページが撮れない → 同じ会社の撮れる公式ページを足す
            extra = fallback_urls(a, urls)
            if extra:
                pages = capture_pages([p.url for p in pages] + extra)
        if not have_source:  # 出典の公式ページが無い・撮れない → 自分の記事ページ（料金表・要点・FAQ）も画面に使う
            own = capture_pages([p.url for p in pages] + [f"https://{SITE}/articles/{slug}"])
            pages = own or pages
        if not pages:
            reason = "撮れるページが無い（" + "・".join(CAPTURE_FAILS[:3]) + "）"
        else:
            script = latin_names(load_script(slug, a, pages, out_dir, reuse, note, cache) if (note or cache) else load_script(slug, a, pages, out_dir, reuse))
    except Exception as error:  # noqa: BLE001
        reason = reason or f"台本を作れない: {str(error)[:200]}"
        log(f"[v3] {slug}: {reason}")
        script = None
    if not pages or not script:
        reason = reason or "台本を作れない"
    return pages, script, reason


def _make_versions_once(slug: str, out_dir: Path, tts: str = "voicevox", reuse: bool = False, extra_pages: list[str] | None = None, template: str | None = None, qa_note_text: str = "", **kw) -> dict:
    """同じ台本から2本: サイト用（全部）と SNS 用（短い版）。ページの撮影と台本は1回だけ。
    戻り値 {"full": report, "short": report}。どちらも例外は投げず report に残す。
    template: 中身に合わせた型（video_templates）。None のときは記事から選ぶ（2026-11-04 既定 on）。
    SNS_VIDEO_TEMPLATES=off のときは型を使わない（下の処理。2026-11-03 までと同じ）"""
    a = shorts.load_article(slug)
    if template is None:
        import video_templates

        template = video_templates.template_for(a) if video_templates.enabled() else video_templates.APPROVED
    if template != "default":
        import video_templates

        return video_templates.make_versions(slug, out_dir, template, tts=tts, reuse=reuse, extra_pages=extra_pages, qa_note=qa_note_text, **kw)
    pages, script, reason = prepare_versions(slug, a, out_dir, reuse, extra_pages, note=qa_note_text, cache=f"{slug}.v3script.json") if qa_note_text else prepare_versions(slug, a, out_dir, reuse, extra_pages)
    if not pages or not script:
        empty = {"slug": slug, "errors": [reason], "gate": {"ok": False, "reasons": [reason]}, "failReason": reason, "captureFails": list(CAPTURE_FAILS)}
        return {"full": empty, "short": empty}
    full = make_video(slug, out_dir, tts, profile=kw.get("profile"), article=a, pages=pages, script=json.loads(json.dumps(script, ensure_ascii=False)))
    short = make_video(slug, out_dir, tts, profile=kw.get("profile"), article=a, pages=pages, script=short_script(script))
    return {"full": full, "short": short}


def make_versions(slug: str, out_dir: Path, tts: str = "voicevox", reuse: bool = False, extra_pages: list[str] | None = None, template: str | None = None, **kw) -> dict:
    """同じ台本から2本（サイト用・SNS 用）を作り、QA（位置の検査＋AI の目視の確認）にかける。だめなら1回だけ台本から作り直す。
    戻り値 {"full": report, "short": report}。report["gate"]["qa"] = {"geometry", "vision", "version"}（両方 true の動画だけ投稿に使う）"""
    started = time.time()
    a = shorts.load_article(slug)
    if template is None:
        import video_templates

        template = video_templates.template_for(a) if video_templates.enabled() else video_templates.APPROVED
    out = _make_versions_once(slug, out_dir, tts, reuse, extra_pages, template, **kw)

    def regen(note: str) -> dict:
        return _make_versions_once(slug, out_dir, tts, False, extra_pages, template, qa_note_text=note, **kw)

    return qa_versions(slug, out, regen, started)


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
    parser.add_argument("--template", help="中身に合わせた型で2本（サイト用・SNS 用）を作る見本（video_templates.TEMPLATES。投稿しない）")
    args = parser.parse_args()
    import llm

    llm._load_env_local()
    failed = 0
    for slug in args.slugs:
        if args.template:  # 見本: 型を決めて2本（オーナーの承認前の確認用）
            out = make_versions(slug, Path(args.out), args.tts, args.reuse_script, args.pages, template=args.template, profile=args.profile)
            failed += sum(0 if r["gate"]["ok"] else 1 for r in out.values())
            continue
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
