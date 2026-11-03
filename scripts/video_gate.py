#!/usr/bin/env python3
"""SNS 用ショート動画の品質ゲート（動画を投稿に付けてよいかの最終判定）。

  python3 scripts/video_gate.py out/shorts_v2/<name>.mp4            # 同じ場所の <name>.report.json を使う
  python3 scripts/video_gate.py <mp4> --report <report.json> --profile preview

判定は check_video() 1か所。戻り値は GateResult(ok, reasons, warnings, metrics)。
ok が False の動画は**絶対に投稿に付けない**（投稿そのものは動画なしで今までどおり出す。下の「失敗時の約束」）。

見ること
- 形式: H.264(High/Main) + AAC、yuv420p、1080x1920（v3 は report["frame"] の横型 1920x1080）、30fps 固定、5〜45秒、moov が先頭（faststart）、
  ファイルサイズは 24MiB 以下（各SNSの上限より十分小さく、Cloudflare KV の1値25MiBにも収まる）。
- 音: 音声トラックがある・ラウドネスが -14 LUFS ±1.5・トゥルーピーク -1.0 dBTP 以下（クリップしない）・
  意図した所（冒頭と締め）以外に1秒を超える無音がない・字幕の時間がナレーションと合っている。
- 絵: 真っ黒/単色の画面が0.5秒以上続かない。文字は描いたときに測った枠（report.texts）が
  自分の箱と安全領域（下20%・右12%を避ける）からはみ出さない・最小文字サイズ以上・
  フォントに無い字（豆腐）が無い・文字と背景のコントラスト比が基準以上。
- 中身: 字幕・読み上げ・数字カードに出る数字と固有名詞（英字・「」・長いカタカナ）が全部記事 JSON にある、
  評価語（初・最大など）は記事にあるときだけ、タイトルが記事と合っている、クレジット（音声・画像）が出ている。

失敗時の約束（後で投稿処理に組み込むときの契約）は sns_video.video_for_post() の説明を参照。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# 基準
# --------------------------------------------------------------------------- #

W, H, FPS = 1080, 1920, 30
V3_W, V3_H = 1920, 1080  # v3 は横型 16:9（2026-11-02 オーナー指示）。report["frame"] で寸法を渡す
# Threads/Reels/TikTok の UI（下のキャプション・右のボタン列・上のアカウント名）に隠れない範囲。x0, y0, x1, y1
SAFE = (60, 150, 950, 1536)
MIN_SEC, MAX_SEC = 5.0, 45.0
TARGET_SEC = (15.0, 30.0)  # 目安（外れても警告だけ）
MAX_BYTES = 24 * 1024 * 1024
LUFS_TARGET, LUFS_TOL = -14.0, 1.5
TRUE_PEAK_MAX = -1.0
SILENCE_MAX = 1.0  # 秒。これより長い無音は意図した区間以外で失敗
CONTRAST_MIN, CONTRAST_MIN_LARGE = 4.5, 3.0  # WCAG AA（48px 以上は大きな文字の基準）
LARGE_TEXT_PX = 48
MIN_CHUNK_SEC = 0.45  # 字幕1つの最短表示
MAX_CHARS_PER_SEC = 16.0  # 字幕が速すぎて読めない目安

PROFILES = {
    # 本番: 投稿に付けてよいか
    "production": {"size": (W, H), "fps": FPS, "require_voicevox": True},
    # 手元の試作（edge-tts の声）。声のクレジットは警告に下げる
    "preview": {"size": (W, H), "fps": FPS, "require_voicevox": False},
    # 自動テスト（小さい解像度・低いfps）
    "test": {"size": None, "fps": None, "require_voicevox": False},
}


@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reasons": self.reasons, "warnings": self.warnings, "metrics": self.metrics}


# --------------------------------------------------------------------------- #
# 事実の照合（台本 ↔ 記事 JSON）。shorts_v2 の台本の書き直しでも同じ関数を使う
# --------------------------------------------------------------------------- #

_DIGITS = str.maketrans("０１２３４５６７８９，．％", "0123456789,.%")


def norm(text: str) -> str:
    return (text or "").translate(_DIGITS).replace(",", "")


def material_text(article: dict) -> str:
    """照合に使う記事の文字（タイトル・リード・概要・要点・判定・本文・かんたん版）"""
    v = article.get("verdict") or {}
    parts = [
        article.get("title", ""),
        article.get("seoTitle", ""),
        article.get("lede", ""),
        article.get("description", ""),
        " ".join(article.get("highlights") or []),
        " ".join(str(x) for x in v.values() if isinstance(x, (str, int, float))),
        article.get("body", "") or "",
        article.get("easy", "") or "",
        article.get("sourceName", "") or "",
        " ".join(article.get("tags") or []),
    ]
    return "\n".join(parts)


def _expand_kanji_number(m: re.Match) -> str | None:
    """「11万9800」「3600万」「1.2億」→ 数値の文字列（比べるため）"""
    try:
        head = float(m.group(1))
        unit = {"万": 10_000, "億": 100_000_000, "兆": 1_000_000_000_000}[m.group(2)]
        tail = float(m.group(3)) if m.group(3) else 0
        value = head * unit + tail
        return str(int(value)) if value == int(value) else str(value)
    except (KeyError, ValueError, TypeError):
        return None


def number_tokens(text: str) -> set[str]:
    t = norm(text)
    found = set(re.findall(r"\d+(?:\.\d+)?", t))
    for m in re.finditer(r"(\d+(?:\.\d+)?)(万|億|兆)(\d+)?", t):
        value = _expand_kanji_number(m)
        if value:
            found.add(value)
    return found


def numbers_ok(line: str, material_numbers: set[str]) -> list[str]:
    """line の数字のうち、記事に無いもの。「11万9800」は 119800 と見なしても良い"""
    t = norm(line)
    bad = []
    expanded_spans = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)(万|億|兆)(\d+)?", t):
        value = _expand_kanji_number(m)
        if value and value in material_numbers:
            expanded_spans.append(m.span())
    for m in re.finditer(r"\d+(?:\.\d+)?", t):
        if any(a <= m.start() < b for a, b in expanded_spans):
            continue
        if m.group(0) not in material_numbers:
            bad.append(m.group(0))
    return bad


CLAIM_WORDS = ["史上最大", "史上初", "過去最高", "世界初", "国内初", "業界初", "唯一", "最大", "最高", "最速", "最安", "世界一", "日本一", "No.1", "初の", "初めて"]
LATIN_OK = {"AI", "OK", "PC", "SNS", "IT", "Web", "WEB", "App", "app"}
KATAKANA_OK = {"サイソク", "ポイント", "ニュース"}


def proper_noun_problems(line: str, material: str) -> list[str]:
    """字幕の英字の語・「」の語・6字以上のカタカナが記事にあるか"""
    low = material.lower()
    bad = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9.+\-]*[A-Za-z0-9+]|[A-Za-z]", line):
        if word in LATIN_OK or len(word) == 1:
            continue
        if word.lower() not in low:
            bad.append(word)
    for quoted in re.findall(r"「([^」]{2,})」", line):
        if quoted not in material:
            bad.append(f"「{quoted}」")
    flat = re.sub(r"[・\s=＝]", "", material)
    for kata in re.findall(r"[ァ-ヴー・]{6,}", line):
        if re.sub(r"[・\s]", "", kata) not in flat and kata not in KATAKANA_OK:
            bad.append(kata)
    return bad


def script_lines(script: dict) -> list[dict]:
    """台本のうち画面・音声に出る行（hook / points / use）"""
    lines = []
    if script.get("hook"):
        lines.append(script["hook"])
    lines += list(script.get("points") or [])
    if script.get("use"):
        lines.append(script["use"])
    return lines


def screen_texts(script: dict) -> list[str]:
    """画面に出る文字（字幕・つかみの大きな文字・見出し・数字カード）"""
    texts = [str(script.get("hook_text", "")), str(script.get("headline", ""))]
    texts += [str(x.get("caption", "")) for x in script_lines(script)]
    for n in script.get("numbers") or []:
        texts += [str(n.get("value", "")), str(n.get("label", ""))]
    return [t for t in texts if t]


def fact_problems(script: dict, article: dict) -> list[str]:
    """台本の事実の問題（空なら合格）。数字・固有名詞・評価語・数字カードの値"""
    material = material_text(article)
    mat_numbers = number_tokens(material)
    problems = []
    for text in screen_texts(script):
        if bad := numbers_ok(text, mat_numbers):
            problems.append(f"記事に無い数字 {', '.join(sorted(set(bad)))}（{text[:24]}）")
        if bad := proper_noun_problems(text, material):
            problems.append(f"記事に無い語 {', '.join(sorted(set(bad)))}（{text[:24]}）")
    for line in script_lines(script):
        if bad := numbers_ok(line.get("speech", ""), mat_numbers):
            problems.append(f"読み上げに記事に無い数字 {', '.join(sorted(set(bad)))}")
    joined = "".join(screen_texts(script)) + "".join(x.get("speech", "") for x in script_lines(script))
    claims = [w for w in CLAIM_WORDS if w in joined and w not in material]
    if claims:
        problems.append(f"記事に無い評価語「{'」「'.join(claims)}」")
    mat_compact = re.sub(r"\s+", "", norm(material))
    for n in script.get("numbers") or []:
        value = re.sub(r"\s+", "", norm(str(n.get("value", ""))))
        if value and value not in mat_compact:
            problems.append(f"数字カード「{n.get('value')}」が記事の表記どおりではない")
    return problems


_TERM = re.compile(r"[A-Za-z][A-Za-z0-9.+\-]*|[ァ-ヴー]{2,}|[一-龥々]{2,}")


def _term_in(term: str, sentence: str) -> bool:
    """語が文にあるか。漢字・カタカナの語は2字のまとまりのどれかがあればよい（脆弱性発見 ↔ 弱点発見 のような言い換え）"""
    low = sentence.lower()
    if term.lower() in low:
        return True
    if re.fullmatch(r"[ァ-ヴー一-龥々]{3,}", term):
        return any(term[i : i + 2] in sentence for i in range(len(term) - 1))
    return False


def number_context_problems(script_v3: dict, article: dict) -> list[str]:
    """v3: 画面・読み上げの数字が、記事の中で同じ話題の文に出てくるか。
    例: 「APIクレジット 月200ドル分」は、記事に 200 があっても APIクレジットの文に無ければ失敗（別ページの数字の混入を防ぐ）"""
    material = norm(material_text(article))
    # 記事は文ごと、公式ページの文字は見出しごとの1行（料金表の行など、文に分けると名前と価格が離れるため）
    head, _, official = material.partition("# 公式ページ")
    sentences = [x for x in re.split(r"[。\n]|(?<=[.!?])\s", head) if x.strip()] + [x for x in official.split("\n") if x.strip()]
    hook = script_v3.get("hook") or {}
    # つかみは問いかけの言葉が多いので、答えの数字（payoff）の言葉だけで見る
    items = [("hook", hook.get("payoff", ""), hook.get("speech", ""))]
    items += [(f"beat{i}", f"{b.get('keyword', '')} {b.get('sub', '')}", b.get("speech", "")) for i, b in enumerate(script_v3.get("beats") or [], 1)]
    concl = script_v3.get("conclusion") or {}
    items.append(("conclusion", concl.get("text", ""), concl.get("speech", "")))
    # 料金の段階のグラフの数字（プラン名と価格が公式ページの同じ所にあるかは台本の検査で見ている）は、その beat では文脈を問わない
    lad_nums_by: dict[str, set] = {}
    for lad in [script_v3.get("ladder") or {}] + list(script_v3.get("ladders") or []):
        if lad.get("beat"):
            rows_text = " ".join(f"{r.get('name', '')} {r.get('price', '')} {r.get('usage', '')} {r.get('mult', '')}" for r in lad.get("rows") or [])
            lad_nums_by.setdefault(f"beat{int(lad['beat'])}", set()).update(re.findall(r"\d+(?:\.\d+)?", norm(rows_text)))
    problems = []
    for name, screen, speech in items:
        text = norm(f"{screen} {speech}")
        terms = {t for t in _TERM.findall(norm(screen)) if t not in LATIN_OK and not re.fullmatch(r"\d+", t)}
        terms -= {"ドル", "時間", "毎週", "毎月", "月額", "プラン"}
        for num in set(re.findall(r"\d+(?:\.\d+)?", text)):
            if num in lad_nums_by.get(name, ()) or num == "1":  # 「1つ」「1ドルあたり」のような1は文脈を問わない（記事・公式にあるかは別に見る）
                continue
            pat = re.compile(r"(?<![\d.])" + re.escape(num) + r"(?![\d])")
            hits = [s for s in sentences if pat.search(s)]
            if not hits:
                continue  # 記事に無い数字は fact_problems が落とす
            if terms and not any(_term_in(t, s) for s in hits for t in terms):
                problems.append(f"{name}: 数字 {num} が記事の中で「{'・'.join(sorted(terms)[:3])}」と同じ文に出てこない（別の話の数字では？）")
    return problems


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s、。・「」『』（）()!！?？:：,，.．\-–—]", "", text or "")
    return {t[i : i + 2] for i in range(len(t) - 1)}


def title_problems(script: dict, article: dict, company: str = "") -> list[str]:
    """動画の題・見出しが記事と同じ話か（主役の名前が入っていて、文字の重なりがある）"""
    vt = f"{script.get('title', '')} {script.get('headline', '')}"
    at = article.get("title", "")
    problems = []
    keys = [k for k in re.findall(r"[A-Za-z][A-Za-z0-9.\-]+", at) if k not in LATIN_OK]
    if company:
        keys.append(company)
    keys += re.findall(r"「([^」]+)」", at)
    if keys and not any(k.lower() in vt.lower() for k in keys):
        problems.append(f"題・見出しに記事の主役（{' / '.join(keys[:3])}）が無い")
    a, b = _bigrams(at), _bigrams(vt)
    if a and len(a & b) / max(len(a), 1) < 0.12:
        problems.append("題・見出しと記事タイトルの重なりが少なすぎる")
    return problems


# --------------------------------------------------------------------------- #
# 色・フォント（描画側でも使う）
# --------------------------------------------------------------------------- #


def _lum(rgb) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(fg, bg) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


_NOTDEF: dict[int, bytes] = {}


def missing_glyphs(text: str, fnt) -> list[str]:
    """フォントに無い字（豆腐になる字）。存在しない私用面の字の描画と同じなら「無い」"""
    from PIL import Image, ImageDraw

    def render(ch: str) -> bytes:
        img = Image.new("L", (fnt.size * 2, fnt.size * 2), 0)
        ImageDraw.Draw(img).text((fnt.size // 2, fnt.size // 4), ch, font=fnt, fill=255)
        return img.tobytes()

    key = id(fnt)
    if key not in _NOTDEF:
        _NOTDEF[key] = render("\U0010fffd")
    notdef = _NOTDEF[key]
    bad = []
    for ch in dict.fromkeys(text):
        if ch.isspace():
            continue
        if render(ch) == notdef:
            bad.append(ch)
    return bad


# --------------------------------------------------------------------------- #
# 動画ファイルの検査（ffprobe / ffmpeg）
# --------------------------------------------------------------------------- #


def _bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError(f"{name} が見つかりません")


def probe(path: Path) -> dict:
    try:
        out = subprocess.run(
            [_bin("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=60,
        ).stdout  # fmt: skip
        return json.loads(out)
    except RuntimeError:
        return _probe_with_ffmpeg(path)


def _probe_with_ffmpeg(path: Path) -> dict:
    """ffprobe が無い環境（imageio-ffmpeg だけ）の簡易版"""
    err = subprocess.run([_bin("ffmpeg"), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    streams = []
    if m := re.search(r"Video: (\w+)(?: \((\w[\w ]*)\))?.*?, (\w+)\(?.*?, (\d+)x(\d+).*?, ([\d.]+) fps", err):
        streams.append({"codec_type": "video", "codec_name": m.group(1), "profile": m.group(2) or "", "pix_fmt": m.group(3),
                        "width": int(m.group(4)), "height": int(m.group(5)), "r_frame_rate": f"{m.group(6)}/1"})  # fmt: skip
    if m := re.search(r"Audio: (\w+).*?, (\d+) Hz, (\w+)", err):
        streams.append({"codec_type": "audio", "codec_name": m.group(1), "sample_rate": m.group(2), "channels": 2 if "stereo" in m.group(3) else 1})
    d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else 0
    return {"streams": streams, "format": {"duration": str(dur), "size": str(path.stat().st_size)}}


def moov_first(path: Path) -> bool:
    """MP4 の moov が mdat より前にあるか（faststart。SNS 側で再生開始が速い）"""
    with open(path, "rb") as f:
        while True:
            head = f.read(8)
            if len(head) < 8:
                return False
            size, kind = struct.unpack(">I4s", head)
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0] - 8
            if kind == b"moov":
                return True
            if kind == b"mdat":
                return False
            if size < 8:
                return False
            f.seek(size - 8, 1)


def loudness(path: Path) -> dict:
    """EBU R128 の積分ラウドネスとトゥルーピーク"""
    err = subprocess.run(
        [_bin("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    ).stderr  # fmt: skip
    summary = err[err.rfind("Summary:") :] if "Summary:" in err else err
    i = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    p = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    return {
        "lufs": float(i.group(1)) if i and i.group(1) != "-inf" else None,
        "true_peak": float(p.group(1)) if p and p.group(1) != "-inf" else None,
    }


def silences(path: Path, noise_db: float = -45, min_sec: float = 0.6) -> list[tuple[float, float]]:
    err = subprocess.run(
        [_bin("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af", f"silencedetect=noise={noise_db}dB:d={min_sec}", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    ).stderr  # fmt: skip
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        out.append((max(0.0, s), e if e is not None else float("inf")))
    return out


def frame_stats(path: Path, fps: int = 10) -> list[tuple[float, float]]:
    """10fps・小さな白黒にして、各フレームの明るさの平均と標準偏差"""
    import numpy as np

    w, h = 54, 96
    raw = subprocess.run(
        [_bin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf", f"fps={fps},scale={w}:{h}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=180, check=True,
    ).stdout  # fmt: skip
    frames = np.frombuffer(raw, dtype=np.uint8)
    n = len(frames) // (w * h)
    frames = frames[: n * w * h].reshape(n, h, w).astype(np.float32)
    return [(float(f.mean()), float(f.std())) for f in frames]


def _runs(flags: list[bool], step: float) -> float:
    best = cur = 0
    for flag in flags:
        cur = cur + 1 if flag else 0
        best = max(best, cur)
    return best * step


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #


def _fps(rate: str) -> float:
    try:
        a, b = rate.split("/")
        return float(a) / float(b)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _inside(box, outer) -> bool:
    return box[0] >= outer[0] - 0.5 and box[1] >= outer[1] - 0.5 and box[2] <= outer[2] + 0.5 and box[3] <= outer[3] + 0.5


def check_format(mp4: Path, profile: dict, result: GateResult, frame: tuple | None = None, max_sec: float = MAX_SEC) -> float:
    info = probe(mp4)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(info.get("format", {}).get("duration") or 0)
    size = int(info.get("format", {}).get("size") or mp4.stat().st_size)
    result.metrics.update({"duration": round(duration, 2), "bytes": size})
    if not video:
        result.reasons.append("映像トラックが無い")
        return duration
    if video.get("codec_name") != "h264":
        result.reasons.append(f"映像が H.264 ではない（{video.get('codec_name')}）")
    if video.get("profile") and not re.match(r"(High|Main|Constrained Baseline)$", str(video.get("profile"))):
        result.reasons.append(f"H.264 のプロファイルが不適（{video.get('profile')}）")
    if video.get("pix_fmt") != "yuv420p":
        result.reasons.append(f"色の形式が yuv420p ではない（{video.get('pix_fmt')}）")
    vw, vh = int(video.get("width", 0)), int(video.get("height", 0))
    result.metrics["size"] = f"{vw}x{vh}"
    want = (frame or profile["size"]) if profile["size"] else None
    if want and (vw, vh) != tuple(want):
        result.reasons.append(f"画面サイズが {want[0]}x{want[1]} ではない（{vw}x{vh}）")
    aw, ah = frame or (9, 16)
    if vw * ah != vh * aw:
        result.reasons.append(f"縦横比が {'16:9' if aw > ah else '9:16'} ではない（{vw}x{vh}）")
    fps = _fps(str(video.get("r_frame_rate", "0/1")))
    result.metrics["fps"] = round(fps, 3)
    if profile["fps"] and abs(fps - profile["fps"]) > 0.01:
        result.reasons.append(f"fps が {profile['fps']} ではない（{fps:.2f}）")
    if not (MIN_SEC <= duration <= max_sec):
        result.reasons.append(f"長さが {MIN_SEC:.0f}〜{max_sec:.0f}秒の外（{duration:.1f}秒）")
    elif not (TARGET_SEC[0] <= duration <= max(TARGET_SEC[1], max_sec)):
        result.warnings.append(f"長さが目安 {TARGET_SEC[0]:.0f}〜{TARGET_SEC[1]:.0f}秒の外（{duration:.1f}秒）")
    if size > MAX_BYTES:
        result.reasons.append(f"ファイルが大きすぎる（{size / 1e6:.1f}MB > {MAX_BYTES / 1e6:.1f}MB）")
    if not moov_first(mp4):
        result.reasons.append("faststart になっていない（moov が後ろ）")
    if not audio:
        result.reasons.append("音声トラックが無い")
    else:
        if audio.get("codec_name") != "aac":
            result.reasons.append(f"音声が AAC ではない（{audio.get('codec_name')}）")
        if int(audio.get("sample_rate") or 0) not in (44100, 48000):
            result.reasons.append(f"音声のサンプルレートが不適（{audio.get('sample_rate')}）")
        if audio.get("duration") and abs(float(audio["duration"]) - duration) > 0.3:
            result.reasons.append(f"音声と映像の長さがずれている（音 {float(audio['duration']):.2f}秒 / 全体 {duration:.2f}秒）")
    return duration


def check_audio(mp4: Path, report: dict, duration: float, result: GateResult) -> None:
    loud = loudness(mp4)
    result.metrics.update(loud)
    if loud["lufs"] is None:
        result.reasons.append("音が入っていない（ラウドネスを測れない）")
        return
    if abs(loud["lufs"] - LUFS_TARGET) > LUFS_TOL:
        result.reasons.append(f"音量が基準外（{loud['lufs']:.1f} LUFS。目標 {LUFS_TARGET:.0f}±{LUFS_TOL}）")
    if loud["true_peak"] is not None and loud["true_peak"] > TRUE_PEAK_MAX:
        result.reasons.append(f"音が割れるおそれ（ピーク {loud['true_peak']:.1f} dBTP > {TRUE_PEAK_MAX}）")
    allowed = [tuple(x) for x in report.get("allowed_silences", [])]
    long_gaps = []
    for s, e in silences(mp4):
        e = min(e, duration)
        if e - s <= SILENCE_MAX:
            continue
        # 意図した無音（冒頭・締め）と重なる分を除いて、それでも長ければ失敗
        rest = e - s
        for a, b in allowed:
            rest -= max(0.0, min(e, b) - max(s, a))
        if rest > SILENCE_MAX:
            long_gaps.append(f"{s:.1f}〜{e:.1f}秒")
    if long_gaps:
        result.reasons.append(f"長い無音がある（{', '.join(long_gaps)}）")


def check_timeline(report: dict, duration: float, result: GateResult) -> None:
    units = report.get("units") or []
    if not units:
        result.reasons.append("字幕とナレーションの対応表（units）が無い")
        return
    total = float(report.get("total") or 0)
    if abs(total - duration) > 0.25:
        result.reasons.append(f"描いた長さと動画の長さがずれている（{total:.2f} / {duration:.2f}秒）")
    speech = covered = 0.0
    for u in units:
        s, e = float(u["start"]), float(u["end"])
        chunks = u.get("chunks") or []
        if e <= s:
            result.reasons.append(f"ナレーションの長さが0（{u.get('kind')}）")
            continue
        if not chunks:
            result.reasons.append(f"字幕が無い区間がある（{u.get('kind')}: {u.get('caption', '')[:16]}）")
            continue
        speech += e - s
        for c in chunks:
            covered += max(0.0, min(e, c["end"]) - max(s, c["start"]))
            if c["end"] - c["start"] < MIN_CHUNK_SEC - 1e-6:
                result.reasons.append(f"字幕の表示が短すぎる（{c['end'] - c['start']:.2f}秒: {c['text'][:12]}）")
            chars = len(re.sub(r"[\s、。]", "", c["text"]))
            if chars / max(c["end"] - c["start"], 0.01) > MAX_CHARS_PER_SEC:
                result.warnings.append(f"字幕が速い（{c['text'][:12]}）")
        if chunks[0]["start"] > s + 0.3 or chunks[-1]["end"] < e - 0.05:
            result.reasons.append(f"字幕がナレーションより遅れる/先に消える（{u.get('kind')}）")
    if speech and covered / speech < 0.95:
        result.reasons.append(f"ナレーション中に字幕が出ていない時間がある（{covered / speech:.0%}）")
    hook = next((u for u in units if u.get("kind") == "hook"), None)
    if not hook or not hook.get("chunks") or hook["chunks"][0]["start"] > 0.05:
        result.reasons.append("最初の1秒に「つかみ」の文字が出ていない")


def check_texts(report: dict, result: GateResult) -> None:
    texts = report.get("texts") or []
    if not texts:
        result.reasons.append("文字の配置記録（texts）が無い")
        return
    safe = tuple(report.get("safe") or SAFE)
    for t in texts:
        name = f"{t.get('role')}「{str(t.get('text', ''))[:14]}」"
        bbox = t["bbox"]
        if t.get("box") and not _inside(bbox, t["box"]):
            result.reasons.append(f"文字が枠からはみ出す: {name}")
        if t.get("safe", True) and not _inside(bbox, safe):
            result.reasons.append(f"文字が安全領域の外: {name} {[round(x) for x in bbox]}")
        if t.get("min_size") and t.get("size", 0) < t["min_size"]:
            result.reasons.append(f"文字が小さすぎる: {name}（{t.get('size')}px < {t['min_size']}px）")
        if t.get("missing"):
            result.reasons.append(f"フォントに無い字: {name} {''.join(t['missing'])}")
        need = CONTRAST_MIN_LARGE if t.get("size", 0) >= LARGE_TEXT_PX else CONTRAST_MIN
        if t.get("contrast") is not None and t["contrast"] < need:
            result.reasons.append(f"コントラスト不足: {name}（{t['contrast']:.1f} < {need}）")


def check_visual(mp4: Path, result: GateResult) -> None:
    stats = frame_stats(mp4)
    step = 0.1
    black = _runs([m < 18 and s < 6 for m, s in stats], step)
    blank = _runs([s < 3.0 for _, s in stats], step)
    result.metrics.update({"black_run": round(black, 2), "blank_run": round(blank, 2)})
    if black > 0.5:
        result.reasons.append(f"真っ黒な画面が {black:.1f}秒続く")
    if blank > 0.5:
        result.reasons.append(f"単色の画面が {blank:.1f}秒続く")


def check_content(report: dict, article: dict, profile: dict, result: GateResult) -> None:
    script = report.get("script") or {}
    if report.get("slug") != article.get("slug"):
        result.reasons.append("動画の記事と照合した記事が違う")
    for p in fact_problems(script, article):
        result.reasons.append(f"事実: {p}")
    if report.get("script_v3"):
        for p in number_context_problems(report["script_v3"], article):
            result.reasons.append(f"事実: {p}")
    for p in title_problems(script, article, report.get("company", "")):
        result.reasons.append(f"題: {p}")
    credits = report.get("credits_shown") or []
    voice = report.get("voice") or {}
    if voice.get("engine") == "voicevox":
        if not voice.get("credit") or not any(voice["credit"] in c for c in credits):
            result.reasons.append("VOICEVOX のクレジットが画面に無い")
        if voice.get("credit") and voice["credit"] not in (report.get("description") or ""):
            result.reasons.append("VOICEVOX のクレジットが説明文に無い")
    elif profile["require_voicevox"]:
        result.reasons.append(f"投稿用の声ではない（{voice.get('engine')}）。本番は VOICEVOX のみ")
    else:
        result.warnings.append(f"試作の声（{voice.get('engine')}）")
    need_image = report.get("image_credit")
    if need_image and not any(need_image in c for c in credits):
        result.reasons.append(f"画像のクレジットが画面に無い（{need_image}）")
    if report.get("version") == "v3":  # v3 は締めのカードが無い。URL は投稿の説明文に入れる
        if "ai-tech-express.jp" not in (report.get("description") or ""):
            result.reasons.append("説明文にサイトの URL が無い")
    elif "ai-tech-express.jp" not in "".join(credits):
        result.reasons.append("締めにサイトの URL が無い")


# --------------------------------------------------------------------------- #
# v3「画面で見せる型」の追加基準（report["version"] == "v3" のときだけ）
# --------------------------------------------------------------------------- #

V3_SEC = (20.0, 32.0)
V3_MIN_CHANGES = 10
V3_AVG_SEGMENT = (1.5, 3.0)
V3_MAX_SEGMENT = 3.6  # これより長く何も変わらない区間があれば失敗
V3_MOTION_MIN = 0.6  # 区間ごとのフレーム差（108x192・白黒・10fps の平均絶対差）。止まった画面は 0
V3_EVENT_MIN = 1.2  # 「変わった」と言える差（変化の直後 0.8 秒の最大値）
V3_SEPARATION = (12.0, 18.0)  # 声と BGM の差（声が出ている間の RMS の差・dB）
V3_TITLE_MAX = 2.0  # つかみ（題）の文字は 2 秒までに消す
V3_TEXT_RATIO = 0.18  # 1フレームの中で文字が占める面積の上限
V3_MAX_FONTS = 2
V3_CAPTION_ROLES = ("hook", "keyword", "sub", "conclusion", "source", "credit", "subtitle", "eyebrow", "logo")
# 飾りの線・帯・枠を描く呼び出し。v3 の描画ではマスク（_mask_*）・比較グラフの棒（data_bars）・カーソル（_cursor_sprite）以外で禁止
LINT_CALLS = {"line", "rectangle", "rounded_rectangle", "polygon", "draw_rect", "hline", "vline", "drawbox"}
# _highlight_*: キーワードの下を走るマーカーと、画面の文字の「選択」の青（強調はこの2つだけ。2026-11-02）
LINT_ALLOWED = ("_mask_", "data_bars", "_cursor_sprite", "_highlight_")


def lint_renderer(path: Path) -> list[str]:
    """描画のソースに、線・帯・枠を描く呼び出しが無いか（許可した関数の中だけ可）"""
    import ast

    tree = ast.parse(Path(path).read_text("utf-8"))
    problems = []

    def visit(node, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if isinstance(child, ast.Call):
                attr = child.func.attr if isinstance(child.func, ast.Attribute) else getattr(child.func, "id", "")
                if attr in LINT_CALLS and not any(func.startswith(a) for a in LINT_ALLOWED):
                    problems.append(f"{Path(path).name}:{child.lineno} {func or '(top)'} が {attr}() を描いている")
            if isinstance(child, ast.Constant) and isinstance(child.value, str) and "drawbox" in child.value:
                problems.append(f"{Path(path).name}:{child.lineno} ffmpeg の drawbox（帯）を使っている")
            visit(child, name)

    visit(tree, "")
    return problems


def motion_series(path: Path, fps: int = 10, size: tuple = (108, 192), crop: tuple | None = None) -> list[float]:
    """隣り合うフレームの平均絶対差（108x192 の白黒）。差の i 番目は i/fps〜(i+1)/fps 秒。
    crop=(x0, y0, x1, y1, 元の幅, 元の高さ) なら、その範囲（v3 横型は動く画面のカード）だけを測る"""
    import numpy as np

    w, h = size
    vf = f"fps={fps},scale={w}:{h}"
    if crop:
        x0, y0, x1, y1, fw, fh = crop
        vf = f"fps={fps},crop=iw*{(x1 - x0) / fw:.4f}:ih*{(y1 - y0) / fh:.4f}:iw*{x0 / fw:.4f}:ih*{y0 / fh:.4f},scale={w}:{h}"
    raw = subprocess.run(
        [_bin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf", vf, "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=180, check=True,
    ).stdout  # fmt: skip
    a = np.frombuffer(raw, dtype=np.uint8)
    n = len(a) // (w * h)
    a = a[: n * w * h].reshape(n, h, w).astype(np.float32)
    return [float(x) for x in np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))]


def _visible(texts: list[dict], t: float) -> list[dict]:
    return [x for x in texts if float(x.get("t0", 0)) - 1e-6 <= t < float(x.get("t1", 0))]


def text_ratio_max(texts: list[dict], total: float, frame: tuple = (W, H)) -> tuple[float, float]:
    """文字の枠（重なりは1回）が画面に占める割合の最大と、その時刻"""
    import numpy as np

    worst, at = 0.0, 0.0
    t = 0.0
    while t < total:
        grid = np.zeros((frame[1] // 8, frame[0] // 8), dtype=bool)
        for x in _visible(texts, t):
            b = x["bbox"]
            grid[max(0, int(b[1]) // 8) : int(math.ceil(b[3] / 8)), max(0, int(b[0]) // 8) : int(math.ceil(b[2] / 8))] = True
        r = float(grid.mean())
        if r > worst:
            worst, at = r, t
        t += 0.1
    return worst, at


def _overlap(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


# 動きの予算（2026-11-03 オーナー「右の画面の動きが多すぎる」）。report["motion_budget"] がある新しい動画だけに当てる
CALM_MAX_MOVES_PER_BEAT = 2  # 「行へ」＋「証拠に寄る」
CALM_MOVING_SHARE = 0.35  # カメラが動いている時間の割合の上限
CALM_MIN_HOLD = 1.4  # 動いたあと止めておく最短（計画は 1.5秒）
CALM_MAX_SEGMENT = 4.8  # 止めておく分、何も変わらない区間の上限は少し長く（選択・訳・補足の文字で変化を出す）
CALM_AVG_SEGMENT = (1.5, 3.6)
CALM_MOTION_MIN = 0.2
CALM_EVENT_MIN_SOFT = 0.3  # 文字・選択の変化の直後の差（カメラの動きより小さい）
HIGHLIGHT_MIN_SEC = 1.2  # 証拠の文の選択を見せる最短


def key_fact_ok(keyword: str, sub: str, text: str, ja: str = "") -> bool:
    """選択した証拠の文（英語なら訳も）に、その場面のキーワードの数字・言葉が入っているか"""
    hay = norm(f"{text} {ja}")
    digits = re.findall(r"\d+(?:\.\d+)?", norm(keyword))
    if digits:
        have = {float(x) for x in re.findall(r"\d+(?:\.\d+)?", hay)}
        return all(float(d) in have for d in digits)
    terms = [t for t in _TERM.findall(norm(f"{keyword} {sub}")) if t not in LATIN_OK and not re.fullmatch(r"\d+", t)]
    terms = [t for t in terms if t not in ("ドル", "時間", "毎週", "毎月", "月額", "プラン")]
    return not terms or any(_term_in(t, hay) for t in terms)


def check_calm(report: dict, total: float, result: GateResult) -> None:
    """カメラの動きの予算と、証拠の文の選択（長さ・中身）"""
    moves = report.get("camera_moves") or []
    per_beat: dict = {}
    for mv in moves:
        per_beat[mv.get("beat", -1)] = per_beat.get(mv.get("beat", -1), 0) + 1
    share = sum(float(mv.get("dur", 0)) for mv in moves) / max(total, 1)
    holds = [b["t"] - (a["t"] + a["dur"]) for a, b in zip(moves, moves[1:])]
    m = result.metrics
    m.update({"calm_moves_max": max(per_beat.values(), default=0), "calm_moving_share": round(share, 3), "calm_min_hold": round(min(holds), 2) if holds else None})
    if m["calm_moves_max"] > CALM_MAX_MOVES_PER_BEAT:
        worst = max(per_beat, key=per_beat.get)
        result.reasons.append(f"v3: カメラの動きが多い（場面{worst}で{per_beat[worst]}回。{CALM_MAX_MOVES_PER_BEAT}回まで）")
    if share > CALM_MOVING_SHARE:
        result.reasons.append(f"v3: カメラが動いている時間が長い（{share:.0%}。{CALM_MOVING_SHARE:.0%}まで）")
    if holds and min(holds) < CALM_MIN_HOLD - 1e-6:
        result.reasons.append(f"v3: カメラが止まらずに続けて動く（{min(holds):.1f}秒。{CALM_MIN_HOLD}秒以上止める）")
    for h in report.get("highlights") or []:
        shown = float(h.get("t_end", 0)) - float(h.get("t_full", 0))
        if shown < HIGHLIGHT_MIN_SEC - 1e-6:
            result.reasons.append(f"v3: 場面{h.get('beat')}の証拠の選択が短い（{shown:.1f}秒。{HIGHLIGHT_MIN_SEC}秒以上）")
        line = h.get("line") or [0, total]
        if float(h.get("t_full", 0)) > float(line[1]) + 0.5:
            result.reasons.append(f"v3: 場面{h.get('beat')}の選択が読み上げに間に合っていない")
        if not key_fact_ok(h.get("keyword", ""), h.get("sub", ""), h.get("text", ""), h.get("ja", "")):
            result.reasons.append(f"v3: 場面{h.get('beat')}の選択した文に、キーワード「{h.get('keyword')}」の数字・言葉が無い")


def check_v3(mp4: Path, report: dict, duration: float, result: GateResult, article: dict | None = None, renderer: Path | None = None) -> None:
    m = result.metrics
    calm = bool(report.get("motion_budget"))
    lo, hi = report.get("length_range") or V3_SEC
    if not (lo <= duration <= hi):
        result.reasons.append(f"v3: 長さが {lo:.0f}〜{hi:.0f}秒の外（{duration:.1f}秒）")
    end = float(report.get("end_start") or duration)
    # 場面の変化（カメラの移動・切り替え・数字・グラフ）。クリックは数えない
    kinds = ("move", "cut", "chart", "pop", "highlight", "text") if calm else ("move", "cut", "chart", "pop")
    changes = sorted({round(e["t"], 2) for e in report.get("events") or [] if e.get("kind") in kinds and e["t"] < end - 0.3})
    soft = {round(e["t"], 2) for e in report.get("events") or [] if e.get("kind") in ("highlight", "text")} if calm else set()
    if calm:  # 0.7秒以内に続いた変化は1回と数える（文字と選択が同時に出るなど）。カメラの動き・グラフがあればそちらを残す
        merged: list[float] = []
        for t in changes:
            if merged and t - merged[-1] < 0.7:
                if merged[-1] in soft and t not in soft:
                    merged[-1] = t
                continue
            merged.append(t)
        changes = merged
    max_seg, avg_rng, motion_min = (CALM_MAX_SEGMENT, CALM_AVG_SEGMENT, CALM_MOTION_MIN) if calm else (V3_MAX_SEGMENT, V3_AVG_SEGMENT, V3_MOTION_MIN)
    bounds = [0.0] + [t for t in changes if t > 0.3] + [end]
    segs = [(a, b) for a, b in zip(bounds, bounds[1:]) if b - a > 0.05]
    avg = (end / len(segs)) if segs else 99
    m.update({"v3_changes": len(changes), "v3_avg_segment": round(avg, 2), "v3_max_segment": round(max((b - a for a, b in segs), default=99), 2)})
    if len(changes) < V3_MIN_CHANGES:
        result.reasons.append(f"v3: 画面の変化が少ない（{len(changes)}回 < {V3_MIN_CHANGES}回）")
    if not (avg_rng[0] <= avg <= avg_rng[1]):
        result.reasons.append(f"v3: 1場面の平均が {avg_rng[0]}〜{avg_rng[1]}秒の外（{avg:.2f}秒）")
    if m["v3_max_segment"] > max_seg:
        result.reasons.append(f"v3: {m['v3_max_segment']:.1f}秒間なにも変わらない区間がある（{max_seg}秒まで）")
    # 実際の動き（動画のフレーム差）: 各区間が止まっていない・変化の直後に本当に絵が変わっている
    fw, fh = report.get("frame") or (W, H)
    panel = report.get("panel")
    if fw > fh and panel:
        # 横型: 動く所（画面のカード）は画面の約半分。全体の差を「動く所の面積」で割って、縦型と同じ基準で見る
        share = (panel[2] - panel[0]) * (panel[3] - panel[1]) / (fw * fh)
        diffs = [d / max(share, 0.3) for d in motion_series(mp4, size=(192, 108))]
    else:
        diffs = motion_series(mp4)
    step = 0.1
    seg_scores = []
    for a, b in segs:
        part = diffs[int(a / step) : max(int(a / step) + 1, int(b / step))]
        seg_scores.append(round(sum(part) / max(len(part), 1), 2))
    m["v3_motion"] = seg_scores
    still = [f"{a:.1f}〜{b:.1f}秒({s})" for (a, b), s in zip(segs, seg_scores) if s < motion_min]
    if still:
        result.reasons.append(f"v3: 動きが足りない区間 {', '.join(still[:4])}（基準 {motion_min}）")
    weak = []
    for t in changes:
        win = diffs[int(t / step) : int((t + 0.8) / step) + 1]
        if not win or max(win) < (CALM_EVENT_MIN_SOFT if t in soft else V3_EVENT_MIN):
            weak.append(f"{t:.1f}秒")
    if weak:
        result.reasons.append(f"v3: 変化と記録した所で絵がほとんど変わっていない（{', '.join(weak[:4])}）")
    # 声と BGM の差
    sep = (report.get("audio") or {}).get("separation_db")
    m["v3_separation_db"] = sep
    if sep is None or not (V3_SEPARATION[0] <= sep <= V3_SEPARATION[1]):
        result.reasons.append(f"v3: 声とBGMの差が {V3_SEPARATION[0]:.0f}〜{V3_SEPARATION[1]:.0f}dB の外（{sep}）")
    texts = report.get("texts") or []
    # 題（つかみ）を出しっぱなしにしない
    for x in texts:
        if x.get("role") in ("hook", "title") and float(x.get("t1", 0)) > V3_TITLE_MAX + 1e-6:
            result.reasons.append(f"v3: つかみの文字が {V3_TITLE_MAX:.0f}秒を過ぎても出ている（{x.get('t1')}秒まで）")
    ratio, at = text_ratio_max(texts, end, (fw, fh))
    m["v3_text_ratio"] = round(ratio, 3)
    if ratio > V3_TEXT_RATIO:
        result.reasons.append(f"v3: 文字が画面の {ratio:.0%} を占める（{at:.1f}秒。上限 {V3_TEXT_RATIO:.0%}）")
    # 字幕が注目の場所（大事な画面の文字）にかぶらない
    for f in report.get("focus") or []:
        for x in texts:
            if x.get("role") not in V3_CAPTION_ROLES:
                continue
            if float(x["t0"]) < float(f["t1"]) and float(f["t0"]) < float(x["t1"]) and _overlap(x["bbox"], f["bbox"]) > 0:
                result.reasons.append(f"v3: 文字「{str(x.get('text'))[:10]}」が注目の場所にかぶる（{f['t0']}秒〜）")
                break
    # 画面に描いた数字（キーワード・グラフ・締め）がすべて記事にあり、グラフの倍率は台本と同じ表記
    material_numbers = number_tokens(material_text(article or {})) | {"0"}
    for text in report.get("rendered_strings") or []:
        if text.startswith(("音声", "出典：", "公式ページ")) or text in ("ai-tech-express.jp",):
            continue
        if bad := numbers_ok(text, material_numbers):
            result.reasons.append(f"v3: 画面の数字 {', '.join(sorted(set(bad)))} が記事に無い（{text[:16]}）")
    sv3 = report.get("script_v3") or {}
    comp = sv3.get("compare") or {}
    for ch in report.get("charts") or []:
        if norm(ch.get("ratio", "")) != norm(comp.get("ratio", "")) or not ch.get("ratio"):
            result.reasons.append(f"v3: グラフの倍率「{ch.get('ratio')}」が台本「{comp.get('ratio')}」と違う")
        spoken = norm(" ".join(b.get("keyword", "") + b.get("speech", "") for b in sv3.get("beats") or []))
        for d in re.findall(r"\d+(?:\.\d+)?", norm(ch.get("ratio", ""))):
            if d not in spoken:
                result.reasons.append(f"v3: グラフの数字 {d} がキーワード・読み上げに無い")
    if calm:
        check_calm(report, end, result)
    fonts = report.get("fonts") or []
    if not fonts or len(set(fonts)) > V3_MAX_FONTS:
        result.reasons.append(f"v3: 書体は{V3_MAX_FONTS}つまで（{fonts}）")
    # 描画のソースに線・帯・枠が無い
    renderer = renderer or Path(__file__).resolve().parent / "shorts_v3.py"
    for p in lint_renderer(renderer):
        result.reasons.append(f"v3: 飾りの線・帯: {p}")


_MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}


def japanese_dates(text: str) -> str:
    """英語の日付（December 1, 2026）に日本語の書き方（2026年12月1日）を添える（数字の照合で月を読めるように）"""
    def rep(m: re.Match) -> str:
        mon = _MONTHS[m.group(1)]
        year = f"{m.group(3)}年" if m.group(3) else ""
        return f"{m.group(0)}（{year}{mon}月{int(m.group(2))}日）"

    return re.sub(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(\d{4}))?", rep, text)


def with_official(article: dict, text: str) -> dict:
    """事実の照合に使う資料: 記事 ＋ 撮った公式ページの文字（v3 は公式ページを一次情報として数字の出どころに認める）"""
    if not text:
        return article
    text = japanese_dates(text)
    aug = dict(article)
    aug["body"] = (article.get("body") or "") + "\n\n# 公式ページ\n" + text
    return aug


def derived_counts(script_v3: dict) -> str:
    """グラフの行の数（例: 料金の段階が3行 →「3段階・3つ」）。公式の行を数えた数なので、数字の出どころとして認める"""
    out = []
    for lad in [script_v3.get("ladder") or {}] + list(script_v3.get("ladders") or []):
        rows = [r for r in lad.get("rows") or [] if r.get("name")]
        if lad.get("beat") and rows:
            n = len(rows)
            out.append(f"（グラフの行の数）{'・'.join(r['name'] for r in rows)}の{n}段階、{n}つ、{n}種類、{n}プラン")
    return "\n".join(out)


def check_video(mp4: Path, report: dict, article: dict, profile: str = "production") -> GateResult:
    """動画を投稿に付けてよいか。例外は投げず、問題はすべて reasons に入れる"""
    result = GateResult(ok=False)
    article = with_official(article, "\n".join(x for x in [report.get("official_text") or "", derived_counts(report.get("script_v3") or {})] if x))
    prof = PROFILES[profile]
    try:
        mp4 = Path(mp4)
        if not mp4.exists() or mp4.stat().st_size == 0:
            result.reasons.append("動画ファイルが無い")
            return result
        max_sec = max(MAX_SEC, float((report.get("length_range") or [0, MAX_SEC])[1]))
        duration = check_format(mp4, prof, result, tuple(report["frame"]) if report.get("frame") else None, max_sec)
        check_audio(mp4, report, duration, result)
        check_timeline(report, duration, result)
        check_texts(report, result)
        check_visual(mp4, result)
        check_content(report, article, prof, result)
        if report.get("version") == "v3":
            check_v3(mp4, report, duration, result, article)
        for err in report.get("errors") or []:
            result.reasons.append(f"描画: {err}")
    except Exception as error:  # noqa: BLE001 - 検査そのものが壊れても「不合格」として返す
        result.reasons.append(f"検査中のエラー: {type(error).__name__}: {error}")
    result.ok = not result.reasons
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="ショート動画の品質ゲート")
    parser.add_argument("mp4")
    parser.add_argument("--report")
    parser.add_argument("--article", help="記事 JSON（省略時は content/articles/<slug>.json）")
    parser.add_argument("--profile", default="production", choices=list(PROFILES))
    args = parser.parse_args()
    mp4 = Path(args.mp4)
    report = json.loads(Path(args.report or mp4.with_suffix(".report.json")).read_text("utf-8"))
    root = Path(__file__).resolve().parent.parent
    article = json.loads(Path(args.article or root / "content" / "articles" / f"{report['slug']}.json").read_text("utf-8"))
    res = check_video(mp4, report, article, args.profile)
    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    sys.exit(0 if res.ok else 1)


if __name__ == "__main__":
    main()
