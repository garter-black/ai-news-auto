#!/usr/bin/env python3
"""記事の写真の「主役がどこにあるか」を求めて、記事 JSON に imageFocus として書く。

SNS カード（lib/card-designs.tsx）は、これを見て写真の切り抜き位置・見出しを置く側を決める。
カードは Cloudflare Workers（画像解析ができない）でも描くので、解析はここ（写真を選ぶとき）で済ませておく。

求め方（OpenCV。顔・物の学習モデルは使わず、速くて外れにくい組み合わせ）:
  1. 周りの色との違い（縁の色の平均から離れた色ほど主役らしい）
  2. 輪郭の多さ（Canny のエッジの密度）
  3. スペクトル残差の顕著性（Hou & Zhang 2007。目立つ形）
  4. 顔（OpenCV 同梱の Haar 分類器）。見つかった顔は必ず主役の枠に入れ、髪・肩の分だけ広げる
    5. 人の全身（OpenCV 同梱の HOG 人物検出）。見つかった人は全身を枠に入れる
1〜3 を足した地図で、行・列ごとの重みの 96% が入る範囲に余白 5% を足して主役の枠にする（外れ値に引っぱられない）。

imageFocus = {"x": 中心x, "y": 中心y, "box": [x0, y0, x1, y1], "ar": 幅/高さ, "src": 解析した画像の URL}（座標は 0〜1）

使い方:
  python scripts/image_focus.py --days 3          # 直近3日の記事のうち、未解析・写真が変わった記事
  python scripts/image_focus.py --slug <slug> --force
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
UA = "Mozilla/5.0 (compatible; SaisokuFocusBot/1.0; +https://ai-tech-express.jp/contact)"
MASS = 0.96  # 主役の枠に入れる重みの割合
MARGIN = 0.05  # 枠の外側に足す余白（車輪・足先など端が細い部分の取りこぼし対策）


def _cv():
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    return cv2, np


def _norm(a):
    _, np = _cv()
    a = a.astype("float32")
    lo, hi = float(a.min()), float(a.max())
    return (a - lo) / (hi - lo) if hi > lo else np.zeros_like(a)


def _span(weights, mass: float) -> tuple[int, int]:
    """重みの mass 割合が入る、いちばん短い区間 [i, j)"""
    _, np = _cv()
    total = float(weights.sum())
    if total <= 0:
        return 0, len(weights)
    cum = np.concatenate([[0.0], np.cumsum(weights)])
    best = (0, len(weights))
    j = 0
    for i in range(len(weights)):
        while j < len(weights) and cum[j] - cum[i] < mass * total:
            j += 1
        if cum[j] - cum[i] >= mass * total and j - i < best[1] - best[0]:
            best = (i, j)
    return best


def saliency_map(image):
    """0〜1 の「主役らしさ」の地図（入力は BGR、幅 320 に縮めたもの）"""
    cv2, np = _cv()
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # 1. 縁の色との違い（Lab）
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype("float32")
    edge = max(4, int(min(h, w) * 0.06))
    border = np.concatenate([lab[:edge].reshape(-1, 3), lab[-edge:].reshape(-1, 3), lab[:, :edge].reshape(-1, 3), lab[:, -edge:].reshape(-1, 3)])
    contrast = np.linalg.norm(lab - np.median(border, axis=0), axis=2)
    contrast = _norm(cv2.GaussianBlur(contrast, (0, 0), 3))
    # 2. 輪郭の密度
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 60, 160).astype("float32")
    density = _norm(cv2.GaussianBlur(edges, (0, 0), 6))
    # 3. スペクトル残差
    small = cv2.resize(gray, (64, 64)).astype("float32")
    spectrum = np.fft.fft2(small)
    log_amp = np.log(np.abs(spectrum) + 1e-6)
    residual = log_amp - cv2.blur(log_amp, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(residual + 1j * np.angle(spectrum)))) ** 2
    sal = _norm(cv2.resize(cv2.GaussianBlur(sal.astype("float32"), (0, 0), 2.5), (w, h)))
    combined = 0.45 * contrast + 0.3 * density + 0.25 * sal
    # 弱いところは捨てる（背景のざらつきで枠が広がらないように）
    threshold = float(np.percentile(combined, 60))
    return np.clip(combined - threshold, 0, None)


def faces(image) -> list[tuple[int, int, int, int]]:
    cv2, _ = _cv()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if not hasattr(cv2, "CascadeClassifier"):
        return []
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    size = max(12, int(min(image.shape[:2]) * 0.06))
    found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(size, size))
    return [tuple(int(v) for v in f) for f in found]


def people(image) -> list[tuple[int, int, int, int]]:
    """人の全身（HOG）。確からしいものだけ"""
    cv2, _ = _cv()
    if not hasattr(cv2, "HOGDescriptor"):
        return []
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    found, scores = hog.detectMultiScale(image, winStride=(4, 4), padding=(8, 8), scale=1.05)
    return [tuple(int(v) for v in f) for f, score in zip(found, scores) if float(score) > 0.6]


def subject_box(data: bytes) -> dict | None:
    """画像のバイト列から imageFocus（src 以外）を求める。読めなければ None"""
    cv2, np = _cv()
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    H, W = image.shape[:2]
    scale = 320 / W
    small = cv2.resize(image, (320, max(1, round(H * scale))), interpolation=cv2.INTER_AREA)
    h, w = small.shape[:2]
    weights = saliency_map(small)
    x0, x1 = _span(weights.sum(axis=0), MASS)
    y0, y1 = _span(weights.sum(axis=1), MASS)
    for fx, fy, fw, fh in faces(small):
        # 顔は髪と肩まで入れる
        x0, x1 = min(x0, int(fx - fw * 0.3)), max(x1, int(fx + fw * 1.3))
        y0, y1 = min(y0, int(fy - fh * 0.45)), max(y1, int(fy + fh * 1.6))
    for px, py, pw, ph in people(small):
        x0, x1 = min(x0, px), max(x1, px + pw)
        y0, y1 = min(y0, py), max(y1, py + ph)
    mx, my = round(w * MARGIN), round(h * MARGIN)
    x0, y0, x1, y1 = max(0, x0 - mx), max(0, y0 - my), min(w, x1 + mx), min(h, y1 + my)
    box = [round(x0 / w, 3), round(y0 / h, 3), round(x1 / w, 3), round(y1 / h, 3)]
    return {
        "x": round((box[0] + box[2]) / 2, 3),
        "y": round((box[1] + box[3]) / 2, 3),
        "box": box,
        "ar": round(W / H, 4),
    }


def keep_box(box: list[float], margin: float = 0.015) -> list[float]:
    """切り抜きで守る範囲：枠の余白（MARGIN）を除いた主役の本体＋少しの余白。写真の端に接している辺はそのまま（lib/card-geometry.ts と同じ）"""
    core = [
        box[0] if box[0] <= 0.001 else box[0] + MARGIN,
        box[1] if box[1] <= 0.001 else box[1] + MARGIN,
        box[2] if box[2] >= 0.999 else box[2] - MARGIN,
        box[3] if box[3] >= 0.999 else box[3] - MARGIN,
    ]
    return [max(0.0, core[0] - margin), max(0.0, core[1] - margin), min(1.0, core[2] + margin), min(1.0, core[3] + margin)]


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*"})
    with urllib.request.urlopen(request, timeout=20) as res:
        return res.read()


def ensure_focus(article: dict, data: bytes | None = None, force: bool = False) -> bool:
    """記事の写真の imageFocus を（無い・写真が変わったときだけ）求める。書いたら True"""
    url = str(article.get("thumbnail") or "")
    if not url.startswith("http"):
        return False
    current = article.get("imageFocus") or {}
    if current.get("src") == url and not force:
        return False
    try:
        focus = subject_box(data if data is not None else fetch(url))
    except ImportError:
        return False  # OpenCV が無い環境では何もしない（カードは真ん中で切り抜く）
    except Exception as error:  # noqa: BLE001
        print(f"[focus] {article.get('slug')}: {str(error)[:120]}", file=sys.stderr)
        return False
    if not focus:
        return False
    article["imageFocus"] = {**focus, "src": url}
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=float, default=0)
    parser.add_argument("--slug", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    since = datetime.now(timezone.utc) - timedelta(days=args.days) if args.days else None
    done = 0
    for path in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            article = json.loads(path.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if args.slug and article.get("slug") not in args.slug:
            continue
        if since and not args.slug:
            try:
                made = datetime.fromisoformat(str(article.get("generatedAt") or article.get("publishedAt")).replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                continue
            if made < since:
                continue
        if ensure_focus(article, force=args.force):
            path.write_text(json.dumps(article, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            done += 1
            print(f"[focus] {article['slug']} {article['imageFocus']['box']}")
    print(f"[focus] {done} 本に書きました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
