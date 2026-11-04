#!/usr/bin/env python3
"""記事から縦型ショート動画（YouTube Shorts / TikTok / Reels 用）を作る。

  python3 scripts/shorts.py <slug> [<slug> ...]      # 指定した記事
  python3 scripts/shorts.py --top 3                   # 直近36時間の上位記事
  python3 scripts/shorts.py <slug> --tts edge         # 音声エンジンを指定（auto / voicevox / edge）

出力（out/shorts/ 。git 管理外）:
  <slug>.mp4   1080x1920 / H.264 + AAC / 25〜45秒
  <slug>.json  投稿用のタイトル（40字以内）・説明文（記事URL・音声クレジット入り）・ハッシュタグ

流れ: LLM が記事の事実だけで台本（つかみ→要点3つ→「詳しくはサイソクで」）を書く
      → 1行ずつ音声合成 → Pillow で全フレームを描いて ffmpeg に流し込む。

音声:
- voicevox（本番用）: VOICEVOX エンジン（VOICEVOX_URL、既定 http://127.0.0.1:50021）。
  CI では docker の voicevox/voicevox_engine を立てる。既定の声は冥鳴ひまり（商用可・申請不要・クレジット必須）。
  クレジット「VOICEVOX:冥鳴ひまり」を動画の最後と説明文に必ず入れる。
- edge（試作用）: edge-tts（Microsoft Edge の読み上げ）。公式に第三者・商用利用が許可されたものではないので
  投稿用には使わない。手元で見た目を確かめるときの予備。
- auto: VOICEVOX エンジンに繋がれば voicevox、無ければ edge。

依存: Pillow, budoux, edge-tts（edge のときだけ）, ffmpeg（PATH に無ければ imageio-ffmpeg）。
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import llm  # noqa: E402

ARTICLES = ROOT / "content" / "articles"
FONTS = ROOT / "assets" / "fonts"
SITE = "https://ai-tech-express.jp"
JST = timezone(timedelta(hours=9))

W, H, FPS = 1080, 1920, 30
PHOTO_Y, PHOTO_H = 210, 608  # 上部の写真（16:9）
RED = (217, 45, 32)
BG = (14, 14, 16)
SAMPLE_RATE = 44100
LEAD, GAP, HOLD = 0.35, 0.3, 1.6  # 冒頭の間・行間・最後の静止（秒）
MIN_SEC, MAX_SEC = 25.0, 45.0

DEFAULT_VOICEVOX = os.environ.get("VOICEVOX_SPEAKER", "冥鳴ひまり")
DEFAULT_EDGE = os.environ.get("EDGE_VOICE", "ja-JP-NanamiNeural")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# 記事・企業
# --------------------------------------------------------------------------- #


def load_article(slug: str) -> dict:
    path = ARTICLES / f"{slug}.json"
    if not path.exists():
        raise SystemExit(f"記事がありません: {slug}")
    return json.loads(path.read_text("utf-8"))


def _companies() -> tuple[list[dict], dict]:
    """lib/companies.ts から企業名・色・判定パターンを読む（サイトと同じ判定にする）"""
    src = (ROOT / "lib" / "companies.ts").read_text("utf-8")
    found = []
    for m in re.finditer(
        r'slug: "([^"]+)",\s*name: "([^"]+)",\s*mark: "[^"]*",\s*color: "(#[0-9a-fA-F]+)",\s*pattern:\s*/(.+?)/([a-z]*),\s*\}',
        src,
        re.S,
    ):
        flags = re.I if "i" in m.group(5) else 0
        try:
            found.append({"slug": m.group(1), "name": m.group(2), "color": m.group(3), "re": re.compile(m.group(4), flags)})
        except re.error:
            continue
    block = re.search(r"CHANNEL_COMPANY[^{]*\{(.*?)\}", src, re.S)
    channels = dict(re.findall(r'"?([\w-]+)"?:\s*"([\w-]+)"', block.group(1))) if block else {}
    return found, channels


def company_of(article: dict) -> dict:
    companies, channels = _companies()
    by_slug = {c["slug"]: c for c in companies}

    def first(text: str):
        best = None
        for c in companies:
            m = c["re"].search(text or "")
            if m and (best is None or m.start() < best[1]):
                best = (c, m.start())
        return best[0] if best else None

    hit = by_slug.get(channels.get(article.get("channelSlug", ""), ""))
    if not hit and article.get("sourceType") == "web":
        hit = first(article.get("sourceName", ""))
    hit = hit or first(article.get("title", "")) or first(" ".join(article.get("tags", []))) or first(article.get("description", ""))
    if hit:
        return {"name": hit["name"], "color": hit["color"], "other": False}
    source = article.get("sourceName") or article.get("channel") or ""
    return {"name": source if article.get("sourceType") == "web" else "", "color": "#d92d20", "other": True}


def hex_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def top_slugs(n: int, hours: int = 36) -> list[str]:
    """直近の記事から品質の高い順（まとめ記事は除く）"""
    now = datetime.now(timezone.utc)
    rows = []
    for path in ARTICLES.glob("*.json"):
        try:
            a = json.loads(path.read_text("utf-8"))
            published = datetime.fromisoformat(a.get("publishedAt", "").replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            continue
        if a.get("type") == "roundup" or now - published > timedelta(hours=hours):
            continue
        rows.append((a.get("qualityScore") or 0, a.get("genreSlug") == "ai", published, a["slug"]))
    rows.sort(reverse=True)
    return [r[3] for r in rows[:n]]


# --------------------------------------------------------------------------- #
# 台本（LLM）
# --------------------------------------------------------------------------- #

LINE = {
    "type": "OBJECT",
    "properties": {"caption": {"type": "STRING"}, "speech": {"type": "STRING"}},
    "required": ["caption", "speech"],
}
SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "headline": {"type": "STRING"},
        "hook": LINE,
        "points": {"type": "ARRAY", "items": LINE},
        "summary": {"type": "STRING"},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "propertyOrdering": ["title", "headline", "hook", "points", "summary", "hashtags"],
    "required": ["title", "headline", "hook", "points", "summary", "hashtags"],
}

SCRIPT_SYSTEM = """あなたはニュースサイト「サイソク」の縦型ショート動画の台本担当。
30秒前後で読み上げる短いニュース原稿を書く。

# 絶対の決まり
- 使ってよい事実は「資料」に書かれていることだけ。推測・補足・一般論の追加は禁止。
- 数字・日付・価格・製品名は資料の表記どおり正確に。丸めない。資料に無い数字は書かない。
- 「初」「世界初」「唯一」「最大」「最高」「最速」「最安」などの評価語は、資料に同じ言葉があるときだけ使う。
- 誇張しない。「衝撃」「ヤバい」「革命」「神」「ついに」「史上最強」などの煽り語は使わない。
- 中学生にもわかるふつうの言葉。専門用語は言い換えるか短く説明する。
- です・ます調。1文は短く。

# 構成
- hook: いちばん大事な事実を1文で（誰が・何を）。30〜45字。
- points: 要点を3つ。それぞれ1〜2文、30〜42字。hook で言った事実はくり返さない。読者にとっての意味がわかる順に。
- 最後の「詳しくはサイソクで」はこちらで足すので書かない。

# caption と speech
- caption は画面に出す字幕。資料の表記（英数字・記号）のまま。読みやすいように読点（、）を入れる。
- speech は読み上げ用。caption と同じ内容を、音声合成が正しく読めるように書く。
  英語の製品名・社名はカタカナ（例: OpenAI→オープンエーアイ、GPT-6.1→ジーピーティー6.1、iPad→アイパッド）、
  記号は言葉に（$1→1ドル、50%→50パーセント、5.4mm→5.4ミリ、〜→から）。
  数字は必ずアラビア数字のまま書く（かなや漢数字にしない）。5桁以上の金額だけ「11万9800円」の形にする。

# その他
- title: 動画のタイトル。40字以内。事実をそのまま。末尾に記号を付けない。
- headline: 画面上部に出す見出し。20字以内。製品名＋何が起きたか。
- summary: 説明欄用の要約。80〜120字。
- hashtags: 記事の中心となる固有名詞・話題を3〜4個。先頭に # を付ける。"""


def _norm_digits(text: str) -> str:
    return (text or "").translate(str.maketrans("０１２３４５６７８９，．", "0123456789,.")).replace(",", "")


def article_material(a: dict) -> str:
    v = a.get("verdict") or {}
    parts = [
        f"タイトル: {a.get('title', '')}",
        f"リード: {a.get('lede', '')}",
        f"概要: {a.get('description', '')}",
        "要点:\n" + "\n".join(f"- {h}" for h in a.get("highlights", [])),
    ]
    if v:
        parts.append(f"判定: 提供状況={v.get('status', '')} 日本={v.get('japan', '')} 価格={v.get('price', '')} 対象={v.get('audience', '')}")
    parts.append("本文:\n" + (a.get("body", "") or "")[:5000])
    return "\n\n".join(parts)


def unknown_numbers(script: dict, material: str) -> list[str]:
    """字幕に出てくる数字のうち、資料に無いもの"""
    src = _norm_digits(material)
    lines = [script["hook"]] + script["points"]
    bad = []
    for line in lines:
        for num in re.findall(r"\d+(?:\.\d+)?", _norm_digits(line.get("caption", ""))):
            if num not in src:
                bad.append(num)
    return bad


CLAIM_WORDS = ["史上最大", "史上初", "過去最高", "世界初", "国内初", "業界初", "初", "唯一", "最大", "最高", "最速", "最安", "世界一", "日本一", "No.1"]


def unsupported_claims(script: dict, material: str) -> list[str]:
    """資料に無い評価語（初・最大など）"""
    text = "".join(line.get("caption", "") + line.get("speech", "") for line in [script["hook"]] + script["points"])
    return [w for w in CLAIM_WORDS if w in text and w not in material]


def unread_numbers(script: dict) -> list[str]:
    """字幕の数字（4桁まで）が読み上げ文から消えていないか（かな書きにすると読み間違えやすい）"""
    bad = []
    for line in [script["hook"]] + script["points"]:
        speech = _norm_digits(line.get("speech", ""))
        for num in re.findall(r"\d+(?:\.\d+)?", _norm_digits(line.get("caption", ""))):
            if len(num.split(".")[0]) <= 4 and num not in speech:
                bad.append(num)
    return bad


def latin_words(script: dict) -> list[str]:
    """読み上げ文に残った英単語（AI・API などの大文字略語は読めるので許す）"""
    found = []
    for line in [script["hook"]] + script["points"]:
        found += re.findall(r"[A-Za-z]*[a-z]{2,}[A-Za-z]*", line.get("speech", ""))
    return found


def write_script(a: dict) -> tuple[dict, str]:
    material = article_material(a)
    prompt = f"# 資料\n{material}\n\n上の資料だけを使って、決まりどおりの台本を JSON で書いてください。"
    feedback = ""
    last: Exception | None = None
    tries = 4
    for attempt in range(1, tries + 1):
        try:
            data, model = llm.generate_json(SCRIPT_SYSTEM, prompt + feedback, SCRIPT_SCHEMA, temperature=0.4, max_output_tokens=4096, tier="bulk")
        except RuntimeError as error:
            last = error
            break
        points = [p for p in data.get("points", []) if isinstance(p, dict) and p.get("caption") and p.get("speech")]
        hook = data.get("hook") or {}
        if not hook.get("caption") or not hook.get("speech") or len(points) < 3:
            feedback = "\n\n# 前回の問題\n- hook と points 3つ（caption と speech）をそろえてください。"
            continue
        data["points"] = points[:3]
        # 事実の問題（必ず直す）と読み上げの問題（最後は妥協する）を分けて、まとめて伝える
        facts, reading = [], []
        if bad := sorted(set(unknown_numbers(data, material))):
            facts.append(f"資料に無い数字 {', '.join(bad)} を使いました。資料にある数字だけを使ってください。")
        if claims := unsupported_claims(data, material):
            facts.append(f"資料に無い言葉「{'」「'.join(claims)}」を使いました。資料にある事実だけで書いてください。")
        if unread := unread_numbers(data):
            reading.append(f"speech で数字 {', '.join(unread)} をかなや漢数字にしました。speech でも数字はアラビア数字のまま（例: ジーピーティー6.1）。")
        if latin := latin_words(data):
            reading.append(f"speech に英単語 {', '.join(latin)} が残っています。speech では英単語をカタカナに（例: Terra→テラ）。")
        if not facts and (not reading or attempt == tries):
            if reading:
                log(f"   [shorts] 読み上げの注意を残したまま採用: {reading}")
            return data, model
        log(f"   [shorts] 書き直し: {facts + reading}")
        feedback = "\n\n# 前回の問題（すべて直す）\n" + "\n".join(f"- {x}" for x in facts + reading)
    raise RuntimeError(f"台本を作れませんでした: {last or feedback.strip()}")


# --------------------------------------------------------------------------- #
# 音声
# --------------------------------------------------------------------------- #


def ffmpeg_bin() -> str:
    """PATH の ffmpeg。無ければ pip の imageio-ffmpeg に入っている静的ビルド"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        raise SystemExit("ffmpeg が必要です（brew install ffmpeg か pip install imageio-ffmpeg）")


def _ffmpeg_to_wav(src: Path, dst: Path) -> None:
    subprocess.run(
        [ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", str(dst)],
        check=True,
    )


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


class VoiceVox:
    def __init__(self, speaker: str, speed: float = 1.15, style: str = "ノーマル", pitch: float = 0.0, intonation: float = 1.0):
        self.base = os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021").rstrip("/")
        self.speed = speed
        self.pitch = pitch  # pitchScale（0 が標準。+0.03 くらいで少し高く）
        self.intonation = intonation  # intonationScale（1 が標準。上げると抑揚が大きく明るく聞こえる）
        self.speaker_name = speaker
        self.style_id = self._resolve(speaker, style)
        self.credit = f"VOICEVOX:{speaker}"
        self.label = f"VOICEVOX（{speaker}）"

    def _get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return json.loads(r.read())

    def _resolve(self, name: str, style: str = "ノーマル") -> int:
        """キャラ名（指定のスタイル → 「ノーマル」の順）→ style id"""
        for sp in self._get("/speakers"):
            if sp.get("name") == name:
                styles = sp.get("styles", [])
                normal = [s for s in styles if s.get("name") == style] or [s for s in styles if s.get("name") == "ノーマル"] or styles
                return int(normal[0]["id"])
        raise RuntimeError(f"VOICEVOX に話者がいません: {name}")

    @staticmethod
    def available() -> bool:
        base = os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021").rstrip("/")
        try:
            with urllib.request.urlopen(base + "/version", timeout=3):
                return True
        except Exception:  # noqa: BLE001
            return False

    def synth(self, text: str, dst: Path) -> None:
        q = urllib.parse.urlencode({"text": text, "speaker": self.style_id})
        req = urllib.request.Request(f"{self.base}/audio_query?{q}", method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            query = json.loads(r.read())
        query.update({"speedScale": self.speed, "pitchScale": self.pitch, "intonationScale": self.intonation, "prePhonemeLength": 0.05, "postPhonemeLength": 0.1, "outputSamplingRate": SAMPLE_RATE, "outputStereo": False})
        if getattr(self, "pause", 1.0) != 1.0:  # 句読点の間の長さ（文ごとに少し変える。shorts_v3 の役目ごとの声）
            query["pauseLengthScale"] = self.pause
        req = urllib.request.Request(
            f"{self.base}/synthesis?speaker={self.style_id}",
            data=json.dumps(query).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = dst.with_suffix(".raw.wav")
            raw.write_bytes(r.read())
        _ffmpeg_to_wav(raw, dst)
        raw.unlink(missing_ok=True)


class EdgeTTS:
    """試作用。Microsoft の正式な利用許諾が無いので投稿用には使わない"""

    def __init__(self, voice: str, speed: float = 1.15):
        self.voice = voice
        self.rate = f"+{round((speed - 1) * 100)}%"
        self.credit = ""
        self.label = f"edge-tts（{voice}・試作用）"

    def synth(self, text: str, dst: Path) -> None:
        mp3 = dst.with_suffix(".mp3")
        for attempt in range(3):
            proc = subprocess.run(
                [sys.executable, "-m", "edge_tts", "--voice", self.voice, f"--rate={self.rate}", "--text", text, "--write-media", str(mp3)],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0 and mp3.exists() and mp3.stat().st_size > 0:
                break
            time.sleep(2 + attempt * 3)
        else:
            raise RuntimeError(f"edge-tts 失敗: {proc.stderr[-200:]}")
        _ffmpeg_to_wav(mp3, dst)
        mp3.unlink(missing_ok=True)


def make_tts(kind: str, speed: float):
    if kind == "voicevox" or (kind == "auto" and VoiceVox.available()):
        return VoiceVox(DEFAULT_VOICEVOX, speed)
    if kind in ("edge", "auto"):
        return EdgeTTS(DEFAULT_EDGE, speed)
    raise SystemExit(f"不明な音声エンジン: {kind}")


def build_audio(lines: list[dict], tts, work: Path) -> tuple[Path, list[tuple[float, float]], float]:
    """1行ずつ合成して並べる。戻り値: 音声, 各行の(開始,終了), 全体の長さ"""
    clips = []
    for i, line in enumerate(lines):
        dst = work / f"line{i}.wav"
        tts.synth(line["speech"], dst)
        clips.append(dst)
    durations = [wav_seconds(c) for c in clips]
    speech_total = sum(durations)
    gap = GAP
    # 短すぎるときは行間を少し広げ、残りは最後の静止で埋める
    base = LEAD + speech_total + GAP * (len(clips) - 1) + HOLD
    if base < MIN_SEC:
        gap = min(0.7, GAP + (MIN_SEC - base) / max(len(clips) - 1, 1))
    t = LEAD
    spans = []
    for d in durations:
        spans.append((t, t + d))
        t += d + gap
    total = max(MIN_SEC, spans[-1][1] + HOLD)

    out = work / "voice.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        cursor = 0
        for clip, (start, _) in zip(clips, spans):
            pad = int(start * SAMPLE_RATE) - cursor
            if pad > 0:
                w.writeframes(b"\x00\x00" * pad)
                cursor += pad
            with wave.open(str(clip)) as c:
                frames = c.readframes(c.getnframes())
            w.writeframes(frames)
            cursor += len(frames) // 2
        tail = int(total * SAMPLE_RATE) - cursor
        if tail > 0:
            w.writeframes(b"\x00\x00" * tail)
    return out, spans, total


# --------------------------------------------------------------------------- #
# 画像
# --------------------------------------------------------------------------- #

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (name, size)
    if key not in _font_cache:
        file = {"black": "NotoSansJP-Black.otf", "bold": "NotoSansJP-Bold.otf", "logo": "DelaGothicOne-Regular.ttf"}[name]
        _font_cache[key] = ImageFont.truetype(str(FONTS / file), size)
    return _font_cache[key]


def wrap(text: str, fnt: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """文節（budoux）で区切って、幅に収まるように折り返す"""
    try:
        import budoux

        phrases = budoux.load_default_japanese_parser().parse(text)
    except Exception:  # noqa: BLE001
        phrases = list(text)
    rows, cur = [], ""
    for ph in phrases:
        if fnt.getlength(cur + ph) <= width:
            cur += ph
            continue
        if cur:
            rows.append(cur)
            cur = ""
        # 1文節が長すぎるときは文字単位で
        for ch in ph:
            if fnt.getlength(cur + ch) > width and cur:
                rows.append(cur)
                cur = ""
            cur += ch
    if cur:
        rows.append(cur)
    # 行頭に句読点が来ないように
    for i in range(1, len(rows)):
        while rows[i] and rows[i][0] in "、。，．）」』！？":
            rows[i - 1] += rows[i][0]
            rows[i] = rows[i][1:]
    return [r for r in rows if r]


def fetch_image(url: str) -> Image.Image | None:
    if not url or not url.startswith("http"):
        return None
    candidates = [url, url.replace("maxresdefault", "hqdefault")] if "maxresdefault" in url else [url]
    for cand in candidates:
        try:
            req = urllib.request.Request(cand, headers={"User-Agent": "Mozilla/5.0 (saisoku-shorts)"})
            with urllib.request.urlopen(req, timeout=20) as r:
                img = Image.open(io.BytesIO(r.read()))
                img.load()
            if img.width >= 320:
                return img.convert("RGB")
        except Exception:  # noqa: BLE001
            continue
    return None


def cover_fit(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    sw, sh = img.size
    tw, th = size
    scale = max(tw / sw, th / sh)
    nw, nh = math.ceil(sw * scale), math.ceil(sh * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fnt, fill, pad=(16, 6)) -> int:
    x, y = xy
    tw = fnt.getlength(text)
    asc, desc = fnt.getmetrics()
    h = asc + desc
    draw.rounded_rectangle((x, y, x + tw + pad[0] * 2, y + h + pad[1] * 2 - 8), radius=6, fill=fill)
    draw.text((x + pad[0], y + pad[1] - 6), text, font=fnt, fill="white")
    return int(x + tw + pad[0] * 2)


class Scene:
    def __init__(self, article: dict, script: dict, lines: list[dict], spans, total: float, credit: str):
        self.a = article
        self.lines = lines
        self.spans = spans
        self.total = total
        self.company = company_of(article)
        self.color = hex_rgb(self.company["color"])
        self.end_start = spans[-1][0] - 0.15  # 「詳しくはサイソクで」から締めの画面
        photo = fetch_image(article.get("thumbnail", ""))
        self.has_photo = photo is not None
        if photo is None:
            # 写真が無い記事は、カバー画像と同じく企業色の地に企業名を大きく薄く敷く
            photo = self._plain_panel()
        self.zoom = 1.12 if self.has_photo else 1.05
        self.photo_src = cover_fit(photo, (int(W * 1.25), int(PHOTO_H * 1.25)))
        self.static = self._static(photo, script["headline"])
        self.captions = [self._caption(i, line) for i, line in enumerate(lines[:-1])]
        self.end = self._end_card(credit)

    def _plain_panel(self) -> Image.Image:
        base = (29, 29, 34) if self.company["other"] else self.color
        img = Image.new("RGB", (1200, 676), base)
        name = self.company["name"] or "NEWS"
        f = font("black", 200)
        size = min(200, int(200 * 1100 / max(f.getlength(name), 1)))
        f = font("black", size)
        d = ImageDraw.Draw(img)
        d.text((48, 330 - size // 2 - size // 4), name, font=f, fill=tuple(min(255, int(c * 0.86 + 255 * 0.14)) for c in base))
        return img

    # 動かない部分（背景・ロゴ・見出し）
    def _static(self, photo: Image.Image, headline: str) -> Image.Image:
        bg = cover_fit(photo, (W // 4, H // 4)).filter(ImageFilter.GaussianBlur(10)).resize((W, H), Image.BILINEAR)
        bg = Image.blend(bg, Image.new("RGB", (W, H), BG), 0.78)
        # 下に行くほど暗く
        shade = Image.linear_gradient("L").resize((W, H))
        bg = Image.composite(Image.new("RGB", (W, H), BG), bg, shade.point(lambda v: int(v * 0.85)))
        d = ImageDraw.Draw(bg)

        d.text((64, 92), "サイソク", font=font("logo", 46), fill="white")
        published = self.a.get("publishedAt", "")
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(JST)
            date = f"{dt.month}月{dt.day}日"
        except ValueError:
            date = ""
        if date:
            f = font("bold", 32)
            d.text((W - 64 - f.getlength(date), 104), date, font=f, fill=(255, 255, 255, 200))

        # 写真の下に進行バーの溝
        d.rectangle((0, PHOTO_Y + PHOTO_H, W, PHOTO_Y + PHOTO_H + 6), fill=(40, 40, 44))

        # ラベル行：赤い「速報」＋企業名
        y = PHOTO_Y + PHOTO_H + 52
        x = pill(d, (64, y), "速報", font("bold", 32), RED)
        if self.company["name"]:
            d.text((x + 20, y), self.company["name"], font=font("bold", 32), fill=(235, 235, 235))

        # 見出し：左に企業色の縦線
        fh = font("black", 60)
        rows = wrap(headline, fh, W - 64 * 2 - 36)[:2]
        top = y + 72
        line_h = 80
        d.rounded_rectangle((64, top + 6, 64 + 9, top + line_h * len(rows) - 10), radius=4, fill=self.color if not self.company["other"] else RED)
        for i, row in enumerate(rows):
            d.text((64 + 36, top + i * line_h), row, font=fh, fill="white")
        self.caption_top = top + line_h * len(rows) + 90

        f = font("bold", 30)
        site = "ai-tech-express.jp"
        d.text(((W - f.getlength(site)) / 2, 1700), site, font=f, fill=(150, 150, 155))
        return bg

    # 1行ぶんの字幕（白・黒フチ）。上に小さな「ポイント」ラベル
    def _caption(self, index: int, line: dict) -> Image.Image:
        text = line["caption"].strip()
        width = W - 72 * 2
        for size in (68, 64, 60, 56, 52):
            f = font("black", size)
            rows = wrap(text, f, width)
            if len(rows) <= 3:
                break
        line_h = int(size * 1.42)
        label = "" if index == 0 else f"ポイント{index}"
        top = 70 if label else 0
        img = Image.new("RGBA", (W, top + line_h * len(rows) + 30), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if label:
            fl = font("bold", 34)
            lw = fl.getlength(label)
            x = (W - lw - 28) / 2
            d.rounded_rectangle((x, 6, x + 14, 20 + 14), radius=3, fill=self.color if not self.company["other"] else RED)
            d.text((x + 28, 0), label, font=fl, fill=(220, 220, 225))
        for i, row in enumerate(rows):
            x = (W - f.getlength(row)) / 2
            d.text((x, top + i * line_h), row, font=f, fill="white", stroke_width=7, stroke_fill=(0, 0, 0))
        return img

    # 締めの画面
    def _end_card(self, credit: str) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        accent = self.color if not self.company["other"] else RED
        d.rectangle((0, 0, W, 10), fill=accent)
        fl = font("logo", 150)
        logo = "サイソク"
        d.text(((W - fl.getlength(logo)) / 2, 560), logo, font=fl, fill="white")
        fs = font("bold", 40)
        sub = "AIと最新ニュースを、最速で。"
        d.text(((W - fs.getlength(sub)) / 2, 790), sub, font=fs, fill=(190, 190, 195))
        fc = font("black", 66)
        cta = "詳しくはサイソクで"
        d.text(((W - fc.getlength(cta)) / 2, 1010), cta, font=fc, fill="white")
        fu = font("bold", 46)
        url = "ai-tech-express.jp"
        uw = fu.getlength(url)
        d.rounded_rectangle(((W - uw) / 2 - 40, 1140, (W + uw) / 2 + 40, 1240), radius=50, outline=(255, 255, 255), width=3)
        d.text(((W - uw) / 2, 1156), url, font=fu, fill="white")
        fn = font("bold", 30)
        note = "プロフィールのリンクから記事へ"
        d.text(((W - fn.getlength(note)) / 2, 1290), note, font=fn, fill=(150, 150, 155))
        if credit:
            fcr = font("bold", 26)
            text = f"音声：{credit}"
            d.text(((W - fcr.getlength(text)) / 2, 1560), text, font=fcr, fill=(120, 120, 125))
        return img

    def _photo(self, t: float) -> Image.Image:
        z = 1 + (self.zoom - 1) * (t / self.total)
        sw, sh = self.photo_src.size
        # 1.25倍の元画像の中央を、z が上がるほど狭く切り出す
        bw, bh = W * 1.25 / z, PHOTO_H * 1.25 / z
        bw, bh = min(bw, sw), min(bh, sh)
        cx, cy = sw / 2, sh / 2
        box = (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
        return self.photo_src.resize((W, PHOTO_H), Image.BILINEAR, box=box)

    def frame(self, t: float) -> Image.Image:
        main = self.static.copy()
        main.paste(self._photo(t), (0, PHOTO_Y))
        d = ImageDraw.Draw(main)
        accent = self.color if not self.company["other"] else RED
        d.rectangle((0, PHOTO_Y + PHOTO_H, int(W * min(t / self.end_start, 1)), PHOTO_Y + PHOTO_H + 6), fill=accent)
        # 今読んでいる行の字幕（次の行が始まるまで残す）
        current = None
        for i, (start, _) in enumerate(self.spans[:-1]):
            if t >= start - 0.05:
                current = i
        if current is not None:
            cap = self.captions[current]
            age = t - (self.spans[current][0] - 0.05)
            k = min(age / 0.22, 1.0)
            ease = 1 - (1 - k) ** 3
            y = int(self.caption_top + max(0, (1560 - self.caption_top - cap.height) // 2) + (1 - ease) * 26)
            mask = cap.getchannel("A")
            if k < 1:
                mask = mask.point(lambda v, e=ease: int(v * e))
            main.paste(cap, (0, y), mask)
        if t < self.end_start:
            return main
        k = min((t - self.end_start) / 0.3, 1.0)
        return self.end if k >= 1 else Image.blend(main, self.end, k)


# --------------------------------------------------------------------------- #
# 書き出し
# --------------------------------------------------------------------------- #


def render(scene: Scene, audio: Path, out: Path) -> None:
    n = int(math.ceil(scene.total * FPS))
    cmd = [
        ffmpeg_bin(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", str(audio),
        "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-maxrate", "4M", "-bufsize", "8M",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", str(FPS),
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-shortest", "-movflags", "+faststart", str(out),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin
    for i in range(n):
        t = i / FPS
        img = scene.frame(t)
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg が失敗しました")


def probe_seconds(path: Path) -> float:
    """書き出した動画の長さ（ffprobe が無い環境もあるので ffmpeg の表示から読む）"""
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", proc.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def make_meta(a: dict, script: dict, credit: str, seconds: float, tts_label: str) -> dict:
    url = f"{SITE}/articles/{a['slug']}"
    title = re.sub(r"\s+", " ", script["title"]).strip()
    if len(title) > 40:
        title = title[:39] + "…"
    base_tags = ["#サイソク", "#AIニュース" if a.get("genreSlug") == "ai" else "#ニュース"]
    tags = []
    for tag in list(script.get("hashtags", []))[:4] + base_tags:
        tag = "#" + re.sub(r"[\s#＃]+", "", str(tag))
        if len(tag) > 1 and tag not in tags:
            tags.append(tag)
    lines = [script["summary"].strip(), "", "▼記事はこちら", url]
    if credit:
        lines += ["", f"音声：{credit}"]
    lines += ["", " ".join(tags)]
    return {
        "slug": a["slug"],
        "title": title,
        "description": "\n".join(lines),
        "hashtags": tags,
        "url": url,
        "seconds": round(seconds, 2),
        "voice": tts_label,
        "credit": credit,
        "script": [script["hook"]] + script["points"],
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }


def make_short(slug: str, out_dir: Path, tts_kind: str, reuse: bool) -> dict:
    a = load_article(slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    script_path = out_dir / f"{slug}.script.json"
    if reuse and script_path.exists():
        script = json.loads(script_path.read_text("utf-8"))
        log(f"[shorts] {slug}: 台本を再利用")
    else:
        script, model = write_script(a)
        script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), "utf-8")
        log(f"[shorts] {slug}: 台本 {model}")
    lines = [script["hook"]] + script["points"] + [{"caption": "詳しくはサイソクで", "speech": "詳しくは、サイソクで。"}]

    speed = 1.15
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for _ in range(3):
            tts = make_tts(tts_kind, speed)
            audio, spans, total = build_audio(lines, tts, work)
            if total <= MAX_SEC:
                break
            speed += 0.1
            log(f"[shorts] {slug}: {total:.1f}秒で長すぎ -> 速さ {speed:.2f} で作り直し")
        log(f"[shorts] {slug}: 音声 {tts.label} / {total:.1f}秒")
        scene = Scene(a, script, lines, spans, total, tts.credit)
        mp4 = out_dir / f"{slug}.mp4"
        started = time.time()
        render(scene, audio, mp4)
        log(f"[shorts] {slug}: 描画 {time.time() - started:.0f}秒")
    seconds = probe_seconds(mp4)
    meta = make_meta(a, script, tts.credit, seconds, tts.label)
    meta["sizeMB"] = round(mp4.stat().st_size / 1e6, 2)
    (out_dir / f"{slug}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    log(f"[shorts] {slug}: {mp4}（{seconds:.1f}秒 / {meta['sizeMB']}MB）")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="記事から縦型ショート動画を作る")
    parser.add_argument("slugs", nargs="*")
    parser.add_argument("--top", type=int, default=0, help="直近36時間の上位N本")
    parser.add_argument("--out", default=str(ROOT / "out" / "shorts"))
    parser.add_argument("--tts", default=os.environ.get("SHORTS_TTS", "auto"), choices=["auto", "voicevox", "edge"])
    parser.add_argument("--reuse-script", action="store_true", help="前回の台本（<slug>.script.json）を使う")
    args = parser.parse_args()
    ffmpeg_bin()
    llm._load_env_local()
    slugs = args.slugs or top_slugs(args.top)
    if not slugs:
        raise SystemExit("対象の記事がありません")
    results, failed = [], []
    for slug in slugs:
        try:
            results.append(make_short(slug, Path(args.out), args.tts, args.reuse_script))
        except Exception as error:  # noqa: BLE001 - 1本の失敗で全体を止めない
            log(f"[shorts] {slug}: 失敗 {error}")
            failed.append(slug)
    log(f"[shorts] 完了 {len(results)} 本 / 失敗 {len(failed)} 本")
    if failed and not results:
        sys.exit(1)


if __name__ == "__main__":
    main()
