#!/usr/bin/env python3
"""SNS 用の縦型ショート動画 v2（Threads / X / Facebook / Bluesky に付ける 15〜30秒の動画）。

  python3 scripts/shorts_v2.py <slug> --design A,B,C            # 同じ台本・同じ声で3つのデザイン
  python3 scripts/shorts_v2.py <slug> --design A --tts voicevox # 本番の声
  python3 scripts/shorts_v2.py <slug> --bgm pad                 # 自作の静かな和音（著作権の心配なし）を小さく敷く

出力（--out。既定 out/shorts_v2/ 。git 管理外）
  <slug>__<design>.mp4          1080x1920 / 30fps / H.264 High + AAC 48kHz / -14 LUFS / faststart
  <slug>__<design>.report.json  品質ゲート用の記録（字幕の時刻・文字の枠・クレジット・台本）と判定結果
  <slug>__<design>.sheet.png    6コマを横に並べた見比べ用の画像
  <slug>.v2script.json          台本（--reuse-script で使い回す）

デザイン
  A 報道テロップ型: 写真を全面にそのまま（暗くしない）・下だけ影・白い太字のテロップ・赤い「速報」・企業色の縦線
  B 大きな文字型 : 上半分に写真（そのまま）・下半分は紺の面に大きな字幕・要点ごとに写真の切り取りを切り替える
  C 公式ページ型 : 公式の発表ページ（ヘッドレス Chrome で撮影）をゆっくりスクロール。出典を表示。
                   公式ページを撮れない記事（ニュースサイトが出典など）は「数字カード型」になる

流れ: 台本（LLM。記事の事実だけ）→ 1文ずつ音声合成 → 字幕を文節で区切って時刻を割り当て →
      Pillow で全フレーム → ffmpeg。描いた文字の枠・コントラスト・豆腐は描くときに測って report に残し、
      scripts/video_gate.py が最後に合否を出す。最小サイズまで縮めても入らない文字は縮めずに失敗にする。

声: 本番は VOICEVOX（冥鳴ひまり・クレジット必須）。edge-tts は手元の試作だけ（画面に「試作の声」と出る）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import shorts  # noqa: E402  (記事・企業色・フォント・TTS・ffmpeg を共有)
import video_gate as gate  # noqa: E402

W, H, FPS = gate.W, gate.H, gate.FPS
SAFE = gate.SAFE
SR = 48000
NAVY = (29, 74, 143)
NAVY_DARK = (16, 42, 84)
RED = (200, 34, 26)
INK = (11, 26, 51)
PAPER = (243, 245, 248)
WHITE = (255, 255, 255)
SOFT = (205, 218, 240)  # 紺の上の補助文字
SITE = "ai-tech-express.jp"
TAGLINE = "忙しくても、サイソクで「旬」がわかる"
OUTRO = {"caption": "詳しくはサイソクで", "speech": "詳しくは、サイソクで。"}

LEAD = 0.12  # 最初の声まで（文字は0秒から出ている）
GAP_SENTENCE, GAP_LINE = 0.14, 0.32
END_MIN = 2.0  # 締めの画面の最短
TARGET_MAX = 30.0


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class TextFitError(RuntimeError):
    """最小の文字サイズでも枠に入らない（縮めすぎて読めなくするより、動画を作らない）"""


# --------------------------------------------------------------------------- #
# 台本（LLM）
# --------------------------------------------------------------------------- #

_LINE = {"type": "OBJECT", "properties": {"caption": {"type": "STRING"}, "speech": {"type": "STRING"}}, "required": ["caption", "speech"]}
SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "headline": {"type": "STRING"},
        "hook": _LINE,
        "points": {"type": "ARRAY", "items": _LINE},
        "use": _LINE,
        "numbers": {
            "type": "ARRAY",
            "items": {"type": "OBJECT", "properties": {"value": {"type": "STRING"}, "label": {"type": "STRING"}}, "required": ["value", "label"]},
        },
        "summary": {"type": "STRING"},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "propertyOrdering": ["title", "headline", "hook", "points", "use", "numbers", "summary", "hashtags"],
    "required": ["title", "headline", "hook", "points", "use", "numbers", "summary", "hashtags"],
}

SCRIPT_SYSTEM = """あなたはニュースサイト「サイソク」の縦型ショート動画（20秒前後）の台本担当。
見る人は20〜50代の働く人。仕事・副業・独立・起業・投資・不動産・仕事でのAI活用に関心がある。
数秒で「何が起きたか」「それで何ができるか」「仕事やお金にどう関係するか」がわかるように書く。

# 絶対の決まり
- 使ってよい事実は「資料」に書かれていることだけ。推測・補足・一般論の追加は禁止。
- 数字・日付・価格・製品名は資料の表記どおり正確に。丸めない。資料に無い数字は書かない（「3つ」などの数も書かない）。
- 「初」「最大」「最高」「最速」「最安」「唯一」などの評価語は、資料に同じ言葉があるときだけ使う。
- 煽らない。「衝撃」「ヤバい」「革命」「神」「ついに」「必見」などは使わない。絵文字・記号の飾りは使わない。
- 中学生にもわかるふつうの言葉。です・ます調。1文は短く。

# 構成
- hook: 画面いっぱいに大きく出す「つかみ」。caption は20字以内で、誰が・何をしたかが一目でわかる言い切り（体言止め可）。
  speech は caption をそのまま自然に読む文（25字以内）。
- points: 要点を3つ。caption は1〜2文で合計25〜40字。hook と同じ事実はくり返さない。
  読む人の仕事・お金にとっての意味がわかる順に。
- use: 「使える？」の答え。誰に役立つか・何ができるか・注意点を、資料の「判定」「かんたん版」の事実だけで1〜2文（25〜40字）。
  資料に無い使い道を作らない。
- 最後の「詳しくはサイソクで」はこちらで足すので書かない。

# caption と speech
- caption は画面の字幕。資料の表記（英数字・記号）のまま。読点（、）で区切りを入れる。
- speech は読み上げ用。caption と同じ内容・同じ文の数（。の数）にする。
  英語の製品名・社名はカタカナ（例: OpenAI→オープンエーアイ、Claude→クロード、GPT-6.1→ジーピーティー6.1）、
  記号は言葉に（$1→1ドル、50%→50パーセント、〜→から）。数字はアラビア数字のまま（かなや漢数字にしない）。
  5桁以上の金額だけ「11万9800円」の形にする。

# その他
- numbers: 仕事やお金の判断に効く数字を資料から最大2つ。value は資料の表記どおり10字以内（例: 月200ドル、10倍）。
  label はその数字の説明12字以内（例: 月額・据え置き）。数字が無ければ空の配列。
- title: 動画のタイトル。40字以内。事実をそのまま。主役の名前（製品名・会社名）を入れる。
- headline: 20字以内の見出し。製品名＋何が起きたか。
- summary: 説明欄用の要約。80〜120字。
- hashtags: 記事の中心となる固有名詞・話題を3〜4個。先頭に # を付ける。"""


def article_material(a: dict) -> str:
    v = a.get("verdict") or {}
    parts = [
        f"タイトル: {a.get('title', '')}",
        f"リード: {a.get('lede', '')}",
        f"概要: {a.get('description', '')}",
        "要点:\n" + "\n".join(f"- {h}" for h in a.get("highlights", [])),
    ]
    if v:
        parts.append(
            f"判定: 提供状況={v.get('status', '')} 日本={v.get('japan', '')} 価格={v.get('price', '')} "
            f"無料={v.get('free', '')} 対象={v.get('audience', '')} ひとこと={v.get('take', '')}"
        )
    if a.get("easy"):
        parts.append("かんたん版:\n" + a["easy"][:2000])
    parts.append("本文:\n" + (a.get("body", "") or "")[:5000])
    return "\n\n".join(parts)


def reading_problems(script: dict) -> list[str]:
    """読み上げの問題（英単語が残っている・数字をかなにした）"""
    out = []
    for line in gate.script_lines(script):
        speech = gate.norm(line.get("speech", ""))
        for num in re.findall(r"\d+(?:\.\d+)?", gate.norm(line.get("caption", ""))):
            if len(num.split(".")[0]) <= 4 and num not in speech:
                out.append(f"speech で数字 {num} をかなや漢数字にしました。数字はアラビア数字のまま。")
        if latin := re.findall(r"[A-Za-z]*[a-z]{2,}[A-Za-z]*", line.get("speech", "")):
            out.append(f"speech に英単語 {', '.join(latin)} が残っています。カタカナにしてください。")
    return out


def shape_problems(script: dict) -> list[str]:
    out = []
    hook = script.get("hook") or {}
    if len(hook.get("caption", "")) > 22:
        out.append(f"hook の caption が長すぎます（{len(hook['caption'])}字。20字以内）。")
    for line in gate.script_lines(script):
        cap, sp = line.get("caption", ""), line.get("speech", "")
        if len(cap) > 48:
            out.append(f"caption が長すぎます（{cap[:12]}…{len(cap)}字。40字前後に）。")
        nc, ns = len(re.findall(r"[。！？]", cap.rstrip("。！？") + "。")), len(re.findall(r"[。！？]", sp.rstrip("。！？") + "。"))
        if nc != ns:
            out.append(f"caption と speech の文の数が違います（{cap[:12]}…）。同じ数にしてください。")
    for n in script.get("numbers") or []:
        if len(str(n.get("value", ""))) > 10 or len(str(n.get("label", ""))) > 14:
            out.append(f"numbers の value は10字・label は12字以内（{n.get('value')} / {n.get('label')}）。")
    return out


def main_name(a: dict) -> str:
    """記事の主役の会社名（lib/companies.ts で判定できたときだけ。配信元のサイト名は使わない）"""
    c = shorts.company_of(a)
    return "" if c["other"] else c["name"]


def write_script(a: dict) -> tuple[dict, str]:
    import llm

    material = article_material(a)
    prompt = f"# 資料\n{material}\n\n上の資料だけを使って、決まりどおりの台本を JSON で書いてください。"
    feedback = ""
    tries = 5
    last = ""
    for attempt in range(1, tries + 1):
        data, model = llm.generate_json(SCRIPT_SYSTEM, prompt + feedback, SCRIPT_SCHEMA, temperature=0.4, max_output_tokens=4096, tier="bulk")
        points = [p for p in data.get("points", []) if isinstance(p, dict) and p.get("caption") and p.get("speech")]
        if not (data.get("hook") or {}).get("caption") or len(points) < 3 or not (data.get("use") or {}).get("caption"):
            feedback = "\n\n# 前回の問題\n- hook・points 3つ・use（それぞれ caption と speech）をそろえてください。"
            continue
        data["points"] = points[:3]
        data["numbers"] = [n for n in data.get("numbers") or [] if isinstance(n, dict) and n.get("value")][:2]
        facts = gate.fact_problems(data, a) + gate.title_problems(data, a, main_name(a))
        shape = shape_problems(data)
        reading = reading_problems(data)
        # 事実の問題は必ず直す。長さ・読みは最後は妥協する（入らない文字は描画で止まり、ゲートで落ちる）
        if not facts and ((not shape and not reading) or attempt == tries):
            if shape or reading:
                log(f"   [v2] 注意を残したまま採用: {shape + reading}")
            return data, model
        last = "; ".join(facts + shape + reading)
        log(f"   [v2] 書き直し: {last}")
        feedback = "\n\n# 前回の問題（すべて直す）\n" + "\n".join(f"- {x}" for x in facts + shape + reading)
    raise RuntimeError(f"台本を作れませんでした: {last}")


# --------------------------------------------------------------------------- #
# 声（1文ずつ合成して並べる）
# --------------------------------------------------------------------------- #


class FakeTTS:
    """自動テスト用。文字数に比例した長さの「声らしい」音（ネットも VOICEVOX も不要）"""

    engine = "fake"
    credit = ""
    label = "テスト用の合成音"

    def __init__(self, sec_per_char: float = 0.11):
        self.spc = sec_per_char

    def synth(self, text: str, dst: Path) -> None:
        n = max(1, len(re.sub(r"[\s、。]", "", text)))
        dur = 0.25 + n * self.spc
        t = np.arange(int(dur * SR)) / SR
        syll = 0.55 + 0.45 * np.abs(np.sin(2 * np.pi * 3.2 * t))  # 音節っぽい揺れ
        f0 = 180 + 25 * np.sin(2 * np.pi * 0.7 * t)
        wave_ = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in (1, 2, 3, 4))
        x = 0.25 * syll * wave_ / 2.1
        fade = np.minimum(1, np.minimum(t / 0.02, (dur - t) / 0.02))
        _write_wav(dst, (x * fade).astype(np.float32))


def _write_wav(path: Path, samples: np.ndarray, channels: int = 1) -> None:
    import wave

    data = np.clip(samples, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((data * 32767).astype("<i2").tobytes())


def _read_wav(path: Path) -> np.ndarray:
    import wave

    with wave.open(str(path)) as w:
        raw = w.readframes(w.getnframes())
        ch = w.getnchannels()
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    return x.reshape(-1, ch).mean(axis=1) if ch > 1 else x


def _to_48k_trimmed(src: Path, dst: Path) -> None:
    """48kHz モノラルにして、前後の無音を削る（行間の長さをこちらで揃えるため）"""
    trim = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.03,areverse,silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.06,areverse"
    subprocess.run(
        [shorts.ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(src), "-af", trim, "-ac", "1", "-ar", str(SR), "-sample_fmt", "s16", str(dst)],
        check=True,
    )


def make_tts(kind: str, speed: float):
    if kind == "fake":
        return FakeTTS()
    tts = shorts.make_tts(kind, speed)
    tts.engine = "voicevox" if isinstance(tts, shorts.VoiceVox) else "edge"
    return tts


@dataclass
class Unit:
    kind: str  # hook / point / use / outro
    line: int  # 何行目か（hook=0, point=1..3, use=4, outro=5）
    caption: str
    speech: str
    start: float = 0.0
    end: float = 0.0
    chunks: list = field(default_factory=list)  # [{"text", "start", "end"}]

    def to_dict(self) -> dict:
        return {"kind": self.kind, "line": self.line, "caption": self.caption, "start": round(self.start, 3), "end": round(self.end, 3), "chunks": self.chunks}


def split_pairs(caption: str, speech: str) -> list[tuple[str, str]]:
    cs = [s for s in re.split(r"(?<=[。！？])", caption.strip()) if s.strip()]
    ss = [s for s in re.split(r"(?<=[。！？])", speech.strip()) if s.strip()]
    return list(zip(cs, ss)) if len(cs) == len(ss) and len(cs) > 1 else [(caption.strip(), speech.strip())]


def script_units(script: dict) -> list[Unit]:
    rows = [("hook", 0, script["hook"])] + [("point", i + 1, p) for i, p in enumerate(script["points"])] + [("use", 4, script["use"]), ("outro", 5, OUTRO)]
    units = []
    for kind, line, item in rows:
        for cap, sp in split_pairs(item["caption"], item["speech"]):
            units.append(Unit(kind, line, cap, sp))
    return units


@dataclass
class Narration:
    units: list[Unit]
    samples: np.ndarray  # 48kHz モノラル（声だけ）
    total: float
    end_start: float  # 締めの画面に切り替わる時刻
    allowed_silences: list
    voice: dict


def narrate(script: dict, tts_kind: str, work: Path) -> Narration:
    """1文ずつ合成して並べる。30秒を超えたら少し速く読み直す（45秒を超えたら失敗）"""
    speed = 1.15
    for _ in range(4):
        tts = make_tts(tts_kind, speed)
        units = script_units(script)
        clips = []
        for i, u in enumerate(units):
            raw, dst = work / f"u{i}.src.wav", work / f"u{i}.wav"
            tts.synth(u.speech, raw)
            _to_48k_trimmed(raw, dst)
            clips.append(_read_wav(dst))
        t = LEAD
        end_start = 0.0
        for i, (u, clip) in enumerate(zip(units, clips)):
            if u.kind == "outro":
                end_start = t - GAP_LINE + 0.2
                t = end_start + 0.25
            u.start, u.end = t, t + len(clip) / SR
            nxt = units[i + 1] if i + 1 < len(units) else None
            t = u.end + (GAP_SENTENCE if nxt and nxt.line == u.line else GAP_LINE)
        outro = units[-1]
        total = max(end_start + END_MIN, outro.end + 0.7)
        if total <= TARGET_MAX or speed >= 1.45:
            break
        speed += 0.1
        log(f"   [v2] {total:.1f}秒で長い → 速さ {speed:.2f} で読み直し")
    if total > gate.MAX_SEC:
        raise RuntimeError(f"長すぎます（{total:.1f}秒）")
    samples = np.zeros(int(math.ceil(total * SR)) + 1, dtype=np.float32)
    for u, clip in zip(units, clips):
        a = int(u.start * SR)
        samples[a : a + len(clip)] += clip[: len(samples) - a]
    voice = {"engine": tts.engine, "credit": tts.credit, "label": tts.label, "speed": round(speed, 2)}
    return Narration(units, samples, total, end_start, [[0.0, LEAD + 0.05], [outro.end, total]], voice)


# --------------------------------------------------------------------------- #
# 音（BGM・音量）
# --------------------------------------------------------------------------- #


def pad_bgm(total: float, seed: int = 0) -> np.ndarray:
    """自作の静かな和音（サイン波の重ね合わせ）。外部の音源を使わないので権利の心配がない"""
    t = np.arange(int(total * SR)) / SR
    chords = [[261.63, 329.63, 392.0, 493.88], [220.0, 261.63, 329.63, 392.0], [174.61, 220.0, 261.63, 329.63], [196.0, 246.94, 293.66, 392.0]]
    seg = 4.0
    out = np.zeros_like(t)
    for i in range(int(math.ceil(total / seg))):
        a, b = i * seg, min(total, (i + 1) * seg + 1.0)
        m = (t >= a) & (t < b)
        tt = t[m] - a
        env = np.minimum(1, tt / 1.2) * np.minimum(1, np.maximum(0, (b - a - tt) / 1.0))
        tone = sum(np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(2 * np.pi * f * 2 * tt) for f in chords[(i + seed) % 4])
        out[m] += env * tone
    out /= max(np.abs(out).max(), 1e-6)
    fade = np.minimum(1, np.minimum(t / 1.5, (total - t) / 1.5))
    return (out * fade * 0.05).astype(np.float32)


def master_audio(voice: np.ndarray, total: float, work: Path, bgm: str = "none") -> Path:
    """声（＋BGM）→ -14 LUFS / ピーク -1.5 dBTP（2回測って直線的に合わせる）→ 48kHz ステレオ"""
    n = int(round(total * SR))
    mix = np.zeros(n, dtype=np.float32)
    mix[: min(n, len(voice))] = voice[:n]
    if bgm == "pad":
        mix += pad_bgm(total)[:n]
    src = work / "mix.wav"
    _write_wav(src, mix)
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


# --------------------------------------------------------------------------- #
# 素材（写真・公式ページ）
# --------------------------------------------------------------------------- #


def image_credit_text(credit: dict | None) -> str:
    """サイトの表記（lib/image-credit.ts・components/ImageCredit.tsx）と同じ考え方"""
    if not credit:
        return ""
    kind = credit.get("kind")
    if kind == "official":
        org = credit.get("org") or credit.get("via") or credit.get("provider")
        via = credit.get("via")
        if not org:
            return ""
        if via and org != via:
            return f"画像：{org}（{via}配信の公式発表より）"
        return f"画像：{via}配信の公式発表より" if via else f"画像：{org}（公式発表より）"
    if kind == "ai":
        return "画像はイメージ（AI生成）"
    if kind == "stock":
        lic = credit.get("license", "")
        trim = "・トリミング" if lic.startswith("CC BY") else ""
        who = " / ".join(x for x in (credit.get("creator"), credit.get("provider")) if x)
        return f"写真はイメージ：{who}（{lic}{trim}）" if lic else f"写真はイメージ：{who}"
    return ""


def usable_photo(article: dict) -> bool:
    """動画に使ってよい写真か: 自前ドメインで配信している公式発表・フリー素材・AI 生成だけ。
    YouTube のサムネイルや他サイトの写真（kind=source）は動画には使わない"""
    url = article.get("thumbnail") or ""
    kind = (article.get("imageCredit") or {}).get("kind")
    return urllib.parse.urlparse(url).netloc.endswith(SITE) and kind in ("official", "stock", "ai")


def brand_panel(company: dict, size=(1200, 676)) -> Image.Image:
    base = NAVY if company["other"] else shorts.hex_rgb(company["color"])
    img = Image.new("RGB", size, base)
    d = ImageDraw.Draw(img)
    name = company["name"] or "NEWS"
    f = shorts.font("black", 200)
    s = min(220, int(200 * (size[0] - 100) / max(f.getlength(name), 1)))
    d.text((48, size[1] // 2 - s // 2 - s // 4), name, font=shorts.font("black", s), fill=tuple(min(255, int(c * 0.86 + 255 * 0.14)) for c in base))
    return img


AD_HOSTS = ("a8.net", "rakuten", "amazon-adsystem", "googlesyndication", "doubleclick", "google-analytics", "googletagmanager", "facebook.net", "adservice")


def official_page_url(article: dict) -> str | None:
    """撮影してよい公式ページか: 公式発表の写真と同じサイト、または公式発表の見張り（radar-official）から来た記事"""
    url = article.get("sourceUrl") or ""
    if not url.startswith("https://") or url.lower().endswith((".pdf", ".xlsx", ".xls", ".zip")):
        return None
    host = urllib.parse.urlparse(url).netloc
    credit = article.get("imageCredit") or {}
    img_host = urllib.parse.urlparse(credit.get("sourceUrl") or "").netloc
    if credit.get("kind") == "official" and img_host and (host == img_host or host.endswith("." + img_host) or img_host.endswith("." + host)):
        return url
    if article.get("channelSlug") == "radar-official":
        return url
    return None


def page_screenshot(url: str, dst: Path, timeout_ms: int = 25000) -> Image.Image | None:
    """公式ページをスマホ幅で撮る（広告・計測への通信は止める・クリックや同意はしない）。失敗したら None"""
    if dst.exists():
        return Image.open(dst).convert("RGB")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("   [v2] playwright が無いので公式ページは撮らない")
        return None
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001
                browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 400, "height": 860}, device_scale_factor=2.5, locale="ja-JP")
            page.route("**/*", lambda r: r.abort() if any(h in r.request.url for h in AD_HOSTS) else r.continue_())
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001
                pass
            # 固定表示のバナー（クッキー・チャット）は隠すだけ。押さない
            page.add_style_tag(content="[style*='position: fixed'],[class*='cookie' i],[id*='cookie' i],[class*='consent' i],[id*='consent' i]{display:none!important}")
            page.wait_for_timeout(600)
            height = page.evaluate("document.documentElement.scrollHeight")
            png = page.screenshot(full_page=True, clip={"x": 0, "y": 0, "width": 400, "height": min(int(height), 1680)}, timeout=timeout_ms)
            browser.close()
        import io

        img = Image.open(io.BytesIO(png)).convert("RGB")
        img = img.crop((0, 0, img.width, min(img.height, int(img.width * 4.2))))
        # 真っ白・ほぼ単色（読み込み失敗）は使わない
        if np.asarray(img.convert("L").resize((50, 200))).std() < 8:
            log("   [v2] 公式ページの撮影結果がほぼ単色なので使わない")
            return None
        dst.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst)
        return img
    except Exception as error:  # noqa: BLE001
        log(f"   [v2] 公式ページを撮れませんでした: {type(error).__name__}: {str(error)[:120]}")
        return None


@dataclass
class Plan:
    article: dict
    script: dict
    narration: Narration
    company: dict
    accent: tuple
    photo: Image.Image
    has_photo: bool
    image_credit: str
    page: Image.Image | None
    page_host: str
    chips: list[str]
    numbers: list[dict]
    preview_voice: bool


JAPAN = {"ok": "日本で使える", "partial": "日本は一部のみ", "no": "日本は対象外"}
STATUS = {"soon": "提供はこれから", "demo": "デモのみ", "limited": "一部に提供"}


def verdict_chips(article: dict) -> list[str]:
    """「使える？」に出す札。記事の判定（verdict）の値をそのまま短く並べるだけ（作らない）"""
    v = article.get("verdict") or {}
    chips = []
    if v.get("japan") in JAPAN:
        chips.append(JAPAN[v["japan"]])
    if v.get("status") in STATUS:
        chips.append(STATUS[v["status"]])
    price = str(v.get("price") or "").strip()
    if v.get("free") is True:
        chips.append("無料あり")
    elif price and price not in ("不明", "なし", "-") and len(price) <= 12:
        chips.append(f"料金 {price}")
    aud = str(v.get("audience") or "").strip()
    if aud and len(aud) <= 14:
        chips.append(f"向く人 {aud}")
    return chips


def make_plan(article: dict, script: dict, narration: Narration, work: Path, want_page: bool) -> Plan:
    company = shorts.company_of(article)
    accent = RED if company["other"] else shorts.hex_rgb(company["color"])
    has_photo = False
    photo = None
    if usable_photo(article):
        photo = shorts.fetch_image(article["thumbnail"])
        has_photo = photo is not None
    if photo is None:
        photo = brand_panel(company)
    page, page_host = None, ""
    if want_page and (url := official_page_url(article)):
        page = page_screenshot(url, work / "pages" / f"{article['slug']}.png")
        page_host = urllib.parse.urlparse(url).netloc.removeprefix("www.")
    return Plan(
        article=article,
        script=script,
        narration=narration,
        company=company,
        accent=accent,
        photo=photo,
        has_photo=has_photo,
        image_credit=image_credit_text(article.get("imageCredit")) if has_photo else "",
        page=page,
        page_host=page_host,
        chips=verdict_chips(article),
        numbers=list(script.get("numbers") or []),
        preview_voice=narration.voice["engine"] != "voicevox",
    )


# --------------------------------------------------------------------------- #
# 描画の部品
# --------------------------------------------------------------------------- #


@dataclass
class Layer:
    img: Image.Image  # RGBA
    x: int
    y: int

    def paste(self, base: Image.Image, alpha: float = 1.0, scale: float = 1.0, dx: int = 0, dy: int = 0) -> None:
        img = self.img
        x, y = self.x + dx, self.y + dy
        if scale != 1.0:
            w, h = img.size
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            img = img.resize((nw, nh), Image.BILINEAR)
            x, y = x + (w - nw) // 2, y + (h - nh) // 2
        mask = img.getchannel("A")
        if alpha < 1.0:
            mask = mask.point(lambda v, a=alpha: int(v * a))
        base.paste(img, (x, y), mask)


def ease(k: float) -> float:
    k = min(max(k, 0.0), 1.0)
    return 1 - (1 - k) ** 3


NUM_RE = re.compile(r"[0-9０-９][0-9０-９.,．，]*(?:万|億|兆)?[0-9０-９]*(?:円|ドル|%|％|倍|人|件|社|年|月|日|時間|分|秒|GB|TB|万人)?")


_ATOM = re.compile(r"[A-Za-z0-9０-９.,，．%％+\-]+|[ァ-ヴーｰ]+・?|.", re.S)
NO_HEAD = "、。，．）」』！？ーぁぃぅぇぉっゃゅょァィゥェォッャュョ・：:%％"
NO_TAIL = "（「『"


def _phrases(text: str) -> list[str]:
    try:
        import budoux

        return budoux.load_default_japanese_parser().parse(text)
    except Exception:  # noqa: BLE001
        return [m.group(0) for m in _ATOM.finditer(text)]


def wrap_jp(text: str, fnt, width: float) -> list[str]:
    """文節（budoux）で折り返す。長すぎる文節は「英数字のまとまり」「カタカナ語」を割らない単位で区切り、
    それでも1行に入らないときだけ1字ずつ。行頭に句読点・長音・小さい字が来ないようにする"""
    rows, cur = [], ""

    def push(piece: str) -> None:
        nonlocal cur
        if fnt.getlength(cur + piece) <= width or not cur:
            cur += piece
        else:
            rows.append(cur)
            cur = piece

    for ph in _phrases(text):
        if fnt.getlength(cur + ph) <= width:
            cur += ph
            continue
        if fnt.getlength(ph) <= width:
            push(ph)
            continue
        for atom in (m.group(0) for m in _ATOM.finditer(ph)):
            if fnt.getlength(atom) <= width:
                push(atom)
            else:
                for ch in atom:
                    push(ch)
    if cur:
        rows.append(cur)
    for i in range(1, len(rows)):
        while rows[i] and rows[i][0] in NO_HEAD:
            rows[i - 1] += rows[i][0]
            rows[i] = rows[i][1:]
        while rows[i - 1] and rows[i - 1][-1] in NO_TAIL:
            rows[i] = rows[i - 1][-1] + rows[i]
            rows[i - 1] = rows[i - 1][:-1]
    return [r for r in rows if r]


class Canvas:
    """文字を描くたびに、実際のインクの枠・サイズ・コントラスト・豆腐を記録する"""

    def __init__(self):
        self.texts: list[dict] = []

    def fit(self, text: str, face: str, size_max: int, size_min: int, width: int, rows_max: int, stroke: int = 0, height: int = 0, line_gap: float = 1.3) -> tuple[int, list[str]]:
        for size in range(size_max, size_min - 1, -2):
            f = shorts.font(face, size)
            rows = wrap_jp(text, f, width - stroke * 2 - 4)
            if len(rows) > rows_max or any(f.getlength(r) + stroke * 2 + 4 > width for r in rows):
                continue
            if height:
                top, bottom = ref_box(face, size, stroke, "".join(rows))
                ink_h = bottom - top
                if max(int(size * line_gap), ink_h + 4) * (len(rows) - 1) + ink_h > height:
                    continue
            return size, rows
        raise TextFitError(f"文字が入りません（{size_min}px でも {rows_max} 行・枠の高さを超える）: {text[:20]}")

    def text(
        self,
        role: str,
        text: str,
        box: tuple[int, int, int, int],
        face: str = "black",
        size: tuple[int, int] = (64, 52),
        rows_max: int = 2,
        fill=WHITE,
        stroke: int = 0,
        stroke_fill=(0, 0, 0),
        bg=None,
        align: str = "left",
        valign: str = "top",
        line_gap: float = 1.3,
        underline_numbers=None,
        t0: float = 0.0,
        t1: float = 0.0,
        safe: bool = True,
    ) -> Layer:
        bw, bh = box[2] - box[0], box[3] - box[1]
        px, rows = self.fit(text, face, size[0], size[1], bw, rows_max, stroke, bh, line_gap)
        f = shorts.font(face, px)
        # 行の高さは「国Agjy（）」のインクの上下で決める（日本語・英字の下がり・かっこが必ず収まる）
        ref = ref_box(face, px, stroke, "".join(rows))
        ink_h = ref[1] - ref[0]
        lh = max(int(px * line_gap), ink_h + 4)
        block_h = lh * (len(rows) - 1) + ink_h
        if block_h > bh:
            raise TextFitError(f"文字の高さが枠を超えます: {text[:20]}")
        top = {"top": 0, "center": (bh - block_h) // 2, "bottom": bh - block_h}[valign]
        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        ink = None
        for i, row in enumerate(rows):
            rw = f.getlength(row) + stroke * 2
            x = {"left": 2, "center": (bw - rw) / 2, "right": bw - rw - 2}[align] + stroke
            y = top + i * lh - ref[0]
            if underline_numbers:
                for m in NUM_RE.finditer(row):
                    ux0 = x + f.getlength(row[: m.start()])
                    ux1 = x + f.getlength(row[: m.end()])
                    uy = top + i * lh + ink_h - max(6, px // 12)
                    d.rectangle((ux0, uy, ux1, uy + max(6, px // 12)), fill=underline_numbers)
            d.text((x, y), row, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
            bb = d.textbbox((x, y), row, font=f, stroke_width=stroke)
            ink = bb if ink is None else (min(ink[0], bb[0]), min(ink[1], bb[1]), max(ink[2], bb[2]), max(ink[3], bb[3]))
        ink = ink or (0, 0, 0, 0)
        bbox = [box[0] + ink[0], box[1] + ink[1], box[0] + ink[2], box[1] + ink[3]]
        if not gate._inside(bbox, box):
            raise TextFitError(f"文字が枠からはみ出します: {text[:20]}")
        surround = stroke_fill if stroke >= 3 else bg
        self.texts.append(
            {
                "role": role,
                "text": text,
                "rows": rows,
                "bbox": [round(v, 1) for v in bbox],
                "box": list(box),
                "size": px,
                "min_size": size[1],
                "contrast": round(gate.contrast(fill, surround), 2) if surround is not None else None,
                "missing": gate.missing_glyphs(text, f),
                "safe": safe,
                "t0": round(t0, 3),
                "t1": round(t1, 3),
            }
        )
        if surround is None:
            raise TextFitError(f"背景が決まらない文字（フチか下地が必要）: {text[:20]}")
        return Layer(img, box[0], box[1])


def chunk_caption(text: str, face: str, size: int, width: int, rows: int, stroke: int = 0) -> list[str]:
    """字幕を「一度に出す分」に区切る（文節単位・決まった行数に収まる分ずつ）。文末の。は出さない"""
    text = text.strip().rstrip("。")
    f = shorts.font(face, size)
    try:
        import budoux

        phrases = budoux.load_default_japanese_parser().parse(text)
    except Exception:  # noqa: BLE001
        phrases = list(text)
    chunks, cur = [], ""
    for ph in phrases:
        cand = cur + ph
        if len(wrap_jp(cand, f, width - stroke * 2)) <= rows:
            cur = cand
            # 読点で区切れて、もう十分な長さなら次へ
            if cur.endswith(("、", "。")) and len(wrap_jp(cur, f, width - stroke * 2)) == rows:
                chunks.append(cur.rstrip("、"))
                cur = ""
            continue
        if cur:
            chunks.append(cur.rstrip("、"))
        cur = ph
    if cur:
        chunks.append(cur.rstrip("、"))
    return [c for c in (c.strip() for c in chunks) if c]


def time_chunks(units: list[Unit], end_start: float, chunker) -> None:
    """各文の字幕を文字数に比例した時刻に割り当てる。最後の字幕は次の文が始まるまで残す"""
    body = [u for u in units if u.kind != "outro"]
    for i, u in enumerate(body):
        nxt = body[i + 1].start if i + 1 < len(body) else end_start
        start = 0.0 if i == 0 else u.start - 0.06
        texts = chunker(u)
        # 短すぎる字幕は隣とつなぐ（つないで入らなければそのまま。ゲートで落ちる）
        while len(texts) > 1:
            weights = [max(1, len(re.sub(r"[\s、]", "", t))) for t in texts]
            dur = u.end - start
            shortest = min(range(len(texts)), key=lambda k: weights[k])
            if weights[shortest] / sum(weights) * dur >= gate.MIN_CHUNK_SEC:
                break
            j = shortest - 1 if shortest > 0 else shortest + 1
            a, b = sorted((shortest, j))
            merged = texts[a] + texts[b]
            if len(chunker(Unit(u.kind, u.line, merged, ""))) != 1:
                break
            texts[a : b + 1] = [merged]
        weights = [max(1, len(re.sub(r"[\s、]", "", t))) for t in texts]
        total_w = sum(weights)
        t = start
        u.chunks = []
        for k, (txt, wgt) in enumerate(zip(texts, weights)):
            end = nxt if k == len(texts) - 1 else t + (u.end - start) * wgt / total_w
            u.chunks.append({"text": txt, "start": round(t, 3), "end": round(end, 3)})
            t = end


class KenBurns:
    """写真をゆっくり拡大・移動する。最初に一度だけ高画質で拡大しておき、毎フレームは切り出すだけ"""

    def __init__(self, img: Image.Image, out: tuple[int, int], max_zoom: float = 1.18):
        self.out = out
        sw, sh = img.size
        scale = max(out[0] / sw, out[1] / sh) * max_zoom
        self.src = img.resize((math.ceil(sw * scale), math.ceil(sh * scale)), Image.LANCZOS)

    def frame(self, zoom: float, cx: float, cy: float = 0.5) -> Image.Image:
        """zoom: 1.0=出力の枠いっぱい。cx, cy: 動ける範囲の中での位置（0〜1）"""
        sw, sh = self.src.size
        ow, oh = self.out
        full_w = min(sw, sh * ow / oh)  # 1倍のときの切り出し幅（縦横比は出力に合わせる）
        win_w = full_w / max(zoom, 1.0)
        win_h = win_w * oh / ow
        win_w, win_h = min(win_w, sw), min(win_h, sh)
        x0 = max(0.0, (sw - win_w) * min(max(cx, 0), 1))
        y0 = max(0.0, (sh - win_h) * min(max(cy, 0), 1))
        return self.src.resize((ow, oh), Image.BILINEAR, box=(x0, y0, x0 + win_w, y0 + win_h))


def gradient(w: int, h: int, top_alpha: float, bottom_alpha: float, color=(0, 0, 0)) -> Image.Image:
    a = np.linspace(0, 1, h)[:, None]
    a = a * a * (3 - 2 * a)  # なめらかに
    alpha = (top_alpha + (bottom_alpha - top_alpha) * a) * 255
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[..., :3] = color
    arr[..., 3] = np.repeat(alpha, w, axis=1).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


REF_TEXT = "国画鬱Ágjy（）「」|"


def ref_box(face: str, size: int, stroke: int = 0, text: str = "") -> tuple[int, int]:
    """行のインクの上端・下端（基準の字と実際の字の両方を含み、上下2pxの余裕）"""
    f = shorts.font(face, size)
    top, bottom = 10**6, -(10**6)
    for t in [REF_TEXT] + ([text] if text else []):
        b = f.getbbox(t, stroke_width=stroke)
        top, bottom = min(top, b[1]), max(bottom, b[3])
    return top - 2, bottom + 2


def ink_height(face: str, size: int, stroke: int = 0) -> int:
    top, bottom = ref_box(face, size, stroke)
    return bottom - top


def rb(x0: int, y: int, x1: int, face: str, size: int, stroke: int = 0, rows: int = 1, gap: float = 1.3) -> tuple[int, int, int, int]:
    """文字の大きさから決めた、rows 行ぶんの枠"""
    ih = ink_height(face, size, stroke)
    return (x0, y, x1, y + max(int(size * gap), ih + 4) * (rows - 1) + ih)


def pill_layer(canvas: Canvas, role: str, text: str, x: int, y: int, size: int, bg, fg=WHITE, pad=(18, 10), t0=0.0, t1=0.0, face="bold") -> Layer:
    f = shorts.font(face, size)
    tw = int(f.getlength(text)) + 6
    ih = ink_height(face, size)
    w, h = tw + pad[0] * 2, ih + pad[1] * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=8, fill=bg)
    inner = canvas.text(role, text, (x + pad[0], y + pad[1], x + pad[0] + tw, y + pad[1] + ih), face=face, size=(size, size), rows_max=1, fill=fg, bg=bg, t0=t0, t1=t1)
    img.alpha_composite(inner.img, (pad[0], pad[1]))
    return Layer(img, x, y)


def chips_layer(canvas: Canvas, chips: list[str], box, size: int, bg, fg=WHITE, t0=0.0, t1=0.0, outline=None) -> Layer | None:
    """札を左から並べる。2行に入らない札は出さない（事実の一部を削るだけで、作り替えはしない）"""
    if not chips:
        return None
    f = shorts.font("bold", size)
    x0, y0, x1, y1 = box
    img = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x, y = 0, 0
    ih = ink_height("bold", size)
    h = ih + 20
    for chip in chips:
        tw = int(f.getlength(chip)) + 6
        w = tw + 40
        if x + w > x1 - x0:
            x, y = 0, y + h + 12
        if y + h > y1 - y0 or w > x1 - x0:
            continue
        d.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=h // 2, fill=bg, outline=outline, width=3 if outline else 0)
        inner = canvas.text("chip", chip, (x0 + x + 20, y0 + y + 10, x0 + x + 20 + tw, y0 + y + 10 + ih), face="bold", size=(size, size), rows_max=1, fill=fg, bg=bg, t0=t0, t1=t1)
        img.alpha_composite(inner.img, (x + 20, y + 10))
        x += w + 12
    return Layer(img, x0, y0)


# --------------------------------------------------------------------------- #
# デザイン共通
# --------------------------------------------------------------------------- #


class Design:
    key = "?"
    name = "?"
    caption_box = (96, 1170, 950, 1500)
    caption_face = "black"
    caption_size = (72, 56)
    caption_rows = 3
    caption_stroke = 8
    caption_fill = WHITE
    caption_bg = None
    hook_box = (96, 1000, 950, 1500)
    hook_size = (120, 88)

    def __init__(self, plan: Plan):
        self.plan = plan
        self.n = plan.narration
        self.units = self.n.units
        self.total = self.n.total
        self.end_start = self.n.end_start
        self.canvas = Canvas()
        self.credits: list[str] = []
        time_chunks(self.units, self.end_start, self._chunker)
        self.lines = self._line_spans()
        self.setup()
        self.caption_layers = self._caption_layers()
        self.end_card = self._end_card()

    # 字幕の区切り方（デザインの字幕の枠に合わせる）
    def _chunker(self, u: Unit) -> list[str]:
        if u.kind == "hook":
            return [u.caption.strip().rstrip("。")]
        box = self.caption_box_for(u)
        return chunk_caption(u.caption, self.caption_face, self.caption_size[0], box[2] - box[0], self.caption_rows if u.kind != "use" else 2, self.caption_stroke)

    def caption_box_for(self, u: Unit) -> tuple:
        return self.caption_box

    def _line_spans(self) -> dict[int, tuple[float, float]]:
        """行（hook / 要点1〜3 / 使える？）ごとの表示区間"""
        spans: dict[int, list[float]] = {}
        for u in self.units:
            if u.kind == "outro":
                continue
            for c in u.chunks:
                s = spans.setdefault(u.line, [c["start"], c["end"]])
                s[0], s[1] = min(s[0], c["start"]), max(s[1], c["end"])
        return {k: (v[0], v[1]) for k, v in spans.items()}

    def kind_of_line(self, line: int) -> str:
        return {0: "hook", 4: "use"}.get(line, "point")

    def number_for_line(self, line: int) -> dict | None:
        """その要点の字幕に出てくる数字カード（字幕と同じ数字のときだけ出す）"""
        if self.kind_of_line(line) != "point":
            return None
        cap = gate.norm("".join(u.caption for u in self.units if u.line == line))
        for n in self.plan.numbers:
            v = gate.norm(str(n.get("value", "")))
            nums = re.findall(r"\d+(?:\.\d+)?", v)
            if nums and all(x in cap for x in nums):
                return n
        return None

    def _caption_layers(self) -> list[tuple[float, float, Layer, Unit]]:
        out = []
        for u in self.units:
            if u.kind == "outro":
                continue
            for c in u.chunks:
                if u.kind == "hook":
                    layer = self.hook_layer(c["text"], c["start"], c["end"])
                else:
                    layer = self.caption_layer(u, c["text"], c["start"], c["end"])
                out.append((c["start"], c["end"], layer, u))
        return out

    def hook_layer(self, text: str, t0: float, t1: float) -> Layer:
        return self.canvas.text("hook", text, self.hook_box, size=self.hook_size, rows_max=3, fill=WHITE, stroke=10, valign="bottom", t0=t0, t1=t1)

    def caption_layer(self, u: Unit, text: str, t0: float, t1: float) -> Layer:
        return self.canvas.text(
            "caption", text, self.caption_box_for(u), face=self.caption_face, size=self.caption_size, rows_max=self.caption_rows if u.kind != "use" else 2,
            fill=self.caption_fill, stroke=self.caption_stroke, bg=self.caption_bg, t0=t0, t1=t1,
        )  # fmt: skip

    def current_caption(self, t: float):
        cur = None
        for item in self.caption_layers:
            if item[0] <= t + 1e-6:
                cur = item
        return cur

    def current_line(self, t: float) -> int | None:
        for line, (a, b) in self.lines.items():
            if a <= t < b:
                return line
        return None

    # 締めの画面（全デザイン共通）
    def _end_card(self) -> Image.Image:
        c = self.canvas
        t0, t1 = self.end_start, self.total
        img = Image.new("RGB", (W, H), NAVY)
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, W, 12), fill=RED)
        logo = c.text("end_logo", "サイソク", (60, 470, 950, 680), face="logo", size=(150, 120), rows_max=1, align="center", bg=NAVY, t0=t0, t1=t1)
        logo.paste(img)
        c.text("end_tagline", TAGLINE, rb(60, 700, 950, "bold", 40), face="bold", size=(40, 32), rows_max=1, fill=SOFT, align="center", bg=NAVY, t0=t0, t1=t1).paste(img)
        c.text("end_cta", OUTRO["caption"], rb(60, 900, 950, "black", 80), size=(80, 64), rows_max=1, align="center", bg=NAVY, t0=t0, t1=t1).paste(img)
        f = shorts.font("bold", 50)
        uw = f.getlength(SITE)
        d.rounded_rectangle(((W - uw) / 2 - 44, 1060, (W + uw) / 2 + 44, 1170), radius=55, outline=WHITE, width=4)
        c.text("end_url", SITE, rb(int((W - uw) / 2) - 8, 1115 - ink_height("bold", 50) // 2, int((W + uw) / 2) + 8, "bold", 50), face="bold", size=(50, 50), rows_max=1, align="center", bg=NAVY, t0=t0, t1=t1).paste(img)
        self.credits.append(SITE)
        y = 1240
        voice = self.n.voice
        voice_text = f"音声：{voice['credit']}" if voice.get("credit") else "音声：試作の声（本番は VOICEVOX:冥鳴ひまり）"
        lines = [voice_text] + ([self.plan.image_credit] if self.plan.image_credit and self.show_photo_credit() else [])
        if self.plan.page is not None and self.key == "C":
            lines.append(f"出典：{self.plan.page_host}（公式ページ）")
        for text in lines:
            layer = c.text("end_credit", text, rb(60, y, 950, "bold", 30, rows=2), face="bold", size=(30, 22), rows_max=2, fill=SOFT, align="center", bg=NAVY, t0=t0, t1=t1)
            layer.paste(img)
            y = int(c.texts[-1]["bbox"][3]) + 18
            self.credits.append(text)
        return img

    def setup(self) -> None:  # 各デザインで静的な部品を作る
        raise NotImplementedError

    def draw(self, t: float) -> Image.Image:  # 締め以外の1フレーム
        raise NotImplementedError

    def frame(self, t: float) -> Image.Image:
        if t >= self.end_start + 0.25:
            return self.end_card
        img = self.draw(min(t, self.end_start + 0.25))
        if t >= self.end_start:
            return Image.blend(img, self.end_card, ease((t - self.end_start) / 0.25))
        return img

    # 字幕の出し方（ふわっと出す。拡大はしないので枠の外に出ない）
    def paste_caption(self, base: Image.Image, t: float, pop: bool = False) -> None:
        cur = self.current_caption(t)
        if not cur:
            return
        start, _, layer, u = cur
        age = t - start
        if u.kind == "hook" and start <= 0.0:
            layer.paste(base)
            return
        k = ease(age / (0.16 if pop else 0.12))
        layer.paste(base, alpha=min(1.0, 0.25 + 0.75 * k), scale=(0.9 + 0.1 * k) if pop else 1.0)

    def overlay_common(self, base: Image.Image, labels_on_photo: bool = True) -> None:
        for layer in self.static_layers:
            layer.paste(base)

    def make_labels(self, on_photo: bool) -> list[Layer]:
        """左上の赤い「速報」と企業名、右上のロゴ、画像クレジット、試作の声の表示"""
        c = self.canvas
        T = (0.0, self.end_start)
        layers = [pill_layer(c, "label", "速報", 60, 160, 36, RED, t0=T[0], t1=T[1])]
        pill_h = layers[0].img.height
        f = shorts.font("bold", 36)
        x = 60 + int(f.getlength("速報")) + 36 + 20
        name = self.plan.company["name"] if not self.plan.company["other"] else (self.plan.article.get("genre") or "")
        stroke = dict(stroke=4, stroke_fill=(0, 0, 0)) if on_photo else dict(bg=PAPER)
        fill = WHITE if on_photo else INK
        if name:
            layers.append(c.text("company", name, rb(x, 160 + (pill_h - ink_height("bold", 36, 4)) // 2, 760, "bold", 36, 4), face="bold", size=(36, 26), rows_max=1, fill=fill, valign="center", t0=T[0], t1=T[1], **stroke))
        logo_f = shorts.font("logo", 44)
        lw = int(logo_f.getlength("サイソク")) + 16
        layers.append(c.text("logo", "サイソク", rb(950 - lw, 160 + max(0, (pill_h - ink_height("logo", 44, 4)) // 2), 950, "logo", 44, 4), face="logo", size=(44, 44), rows_max=1, fill=fill if on_photo else NAVY, align="right", valign="center", t0=T[0], t1=T[1], **stroke))
        y = 160 + pill_h + 16
        if self.plan.image_credit and self.show_photo_credit():
            layers.append(c.text("image_credit", self.plan.image_credit, rb(60, y, 950, "bold", 26, 3, rows=2), face="bold", size=(26, 20), rows_max=2, fill=fill, t0=T[0], t1=T[1], **({"stroke": 3, "stroke_fill": (0, 0, 0)} if on_photo else {"bg": PAPER})))
            self.credits.append(self.plan.image_credit)
            y = int(c.texts[-1]["bbox"][3]) + 8
        if self.plan.preview_voice:
            layers.append(c.text("preview_voice", "※試作の声（本番は VOICEVOX）", rb(60, y, 950, "bold", 26, 3), face="bold", size=(26, 22), rows_max=1, fill=(255, 220, 200) if on_photo else RED, t0=T[0], t1=T[1], **({"stroke": 3, "stroke_fill": (0, 0, 0)} if on_photo else {"bg": PAPER})))
        return layers

    def show_photo_credit(self) -> bool:
        return True

    def number_card(self, n: dict, box, bg=NAVY, fg=WHITE, stripe=RED, t0=0.0, t1=0.0, value_size=(150, 80)) -> Layer:
        x0, y0, x1, y1 = box
        img = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), radius=26, fill=bg + (255,))
        if stripe:
            d.rounded_rectangle((0, 0, x1 - x0 - 1, 14), radius=7, fill=stripe)
        # 文字はカードの内側かつ安全領域の内側（カードが画面の端まで広がっていても文字は端に寄せない）
        tx0, tx1 = max(x0 + 40, SAFE[0]), min(x1 - 40, SAFE[2])
        label_h = 70
        v = self.canvas.text("number", str(n["value"]), (tx0, y0 + 30, tx1, y1 - label_h - 20), size=value_size, rows_max=1, fill=fg, bg=bg, align="center", valign="center", t0=t0, t1=t1)
        img.alpha_composite(v.img, (tx0 - x0, 30))
        lab = self.canvas.text("number_label", str(n.get("label", "")), (tx0, y1 - label_h - 16, tx1, y1 - 14), face="bold", size=(42, 30), rows_max=1, fill=fg, bg=bg, align="center", valign="center", t0=t0, t1=t1)
        img.alpha_composite(lab.img, (tx0 - x0, y1 - y0 - label_h - 16))
        return Layer(img, x0, y0)

    def progress(self, base: Image.Image, t: float, y: int, color, track=(255, 255, 255)) -> None:
        d = ImageDraw.Draw(base)
        d.rectangle((60, y, 950, y + 6), fill=track)
        d.rectangle((60, y, 60 + int(890 * min(t / max(self.end_start, 0.1), 1)), y + 6), fill=color)


# --------------------------------------------------------------------------- #
# A 報道テロップ型
# --------------------------------------------------------------------------- #


class DesignA(Design):
    key = "A"
    name = "報道テロップ型"

    def setup(self) -> None:
        self.kb = KenBurns(self.plan.photo, (W, H), max_zoom=1.16)
        self.shade = gradient(W, H - 860, 0.0, 0.80)
        self.static_layers = self.make_labels(on_photo=True)
        c = self.canvas
        self.line_layers: dict[int, list[tuple[Layer, str]]] = {}
        for line, (a, b) in self.lines.items():
            kind = self.kind_of_line(line)
            items = []
            if kind == "point":
                items.append((c.text("point_label", f"ポイント{line}", rb(96, 1104, 950, "bold", 38, 5), face="bold", size=(38, 30), rows_max=1, stroke=5, valign="center", t0=a, t1=b), "label"))
                if n := self.number_for_line(line):
                    items.append((self.number_card(n, (60, 560, 950, 900), t0=a, t1=b), "card"))
            elif kind == "use":
                items.append((pill_layer(c, "use_label", "使える？", 96, 930, 48, RED, t0=a, t1=b, face="black"), "label"))
                if chips := chips_layer(c, self.plan.chips, (96, 1022, 950, 1160), 34, NAVY, t0=a, t1=b):
                    items.append((chips, "label"))
            self.line_layers[line] = items

    def caption_box_for(self, u: Unit) -> tuple:
        return (96, 1180, 950, 1500)

    def draw(self, t: float) -> Image.Image:
        p = t / self.total
        img = self.kb.frame(1.0 + 0.12 * p, 0.32 + 0.36 * p, 0.45)
        img.paste(self.shade, (0, 860), self.shade)
        self.overlay_common(img)
        line = self.current_line(t)
        if line is not None:
            a, _ = self.lines[line]
            k = ease((t - a) / 0.2)
            for layer, role in self.line_layers.get(line, []):
                if role == "card":
                    layer.paste(img, alpha=k, scale=0.94 + 0.06 * k)
                else:
                    layer.paste(img, alpha=k)
            # 企業色の縦線（字幕の左）
            cur = self.current_caption(t)
            if cur and cur[3].kind != "hook":
                bb = next(x["bbox"] for x in self.canvas.texts if x["role"] == "caption" and x["t0"] == round(cur[0], 3))
                ImageDraw.Draw(img).rounded_rectangle((60, bb[1] + 6, 72, bb[3] - 4), radius=5, fill=self.plan.accent)
            elif cur:
                bb = next(x["bbox"] for x in self.canvas.texts if x["role"] == "hook")
                ImageDraw.Draw(img).rounded_rectangle((60, bb[1] + 10, 74, bb[3] - 8), radius=6, fill=self.plan.accent)
        self.paste_caption(img, t)
        self.progress(img, t, 1520, self.plan.accent, track=(90, 90, 96))
        return img


# --------------------------------------------------------------------------- #
# B 大きな文字型
# --------------------------------------------------------------------------- #


class DesignB(Design):
    key = "B"
    name = "大きな文字型"
    caption_size = (86, 64)
    caption_rows = 3
    caption_stroke = 0
    caption_bg = NAVY
    hook_box = (60, 990, 950, 1490)
    hook_size = (132, 96)
    PHOTO_H = 900

    # 要点ごとに写真の切り取りを変える（寄り・横移動・引き）
    SHOTS = [(1.0, 1.10, 0.5, 0.5), (1.12, 1.12, 0.15, 0.55), (1.18, 1.04, 0.85, 0.7), (1.08, 1.08, 0.75, 0.2), (1.02, 1.14, 0.4, 0.6)]

    def caption_box_for(self, u: Unit) -> tuple:
        return (60, 1180, 950, 1500) if u.kind == "use" else (60, 1030, 950, 1500)

    def setup(self) -> None:
        self.kb = KenBurns(self.plan.photo, (W, self.PHOTO_H), max_zoom=1.2)
        self.panel = Image.new("RGB", (W, H - self.PHOTO_H), NAVY)
        ImageDraw.Draw(self.panel).rectangle((0, 0, W, 12), fill=self.plan.accent)
        self.static_layers = self.make_labels(on_photo=True)
        c = self.canvas
        self.line_layers: dict[int, list[tuple[Layer, str]]] = {}
        for line, (a, b) in self.lines.items():
            kind = self.kind_of_line(line)
            items = []
            if kind == "point":
                items.append((c.text("point_label", f"ポイント {line}/3", rb(60, 950, 950, "bold", 40), face="bold", size=(40, 32), rows_max=1, fill=SOFT, bg=NAVY, valign="center", t0=a, t1=b), "label"))
                if n := self.number_for_line(line):
                    items.append((self.number_card(n, (60, 600, 950, 870), bg=RED, stripe=None, t0=a, t1=b, value_size=(140, 80)), "card"))
            elif kind == "use":
                items.append((pill_layer(c, "use_label", "使える？", 60, 950, 50, RED, t0=a, t1=b, face="black"), "label"))
                if chips := chips_layer(c, self.plan.chips, (60, 1050, 950, 1170), 34, NAVY_DARK, outline=SOFT, t0=a, t1=b):
                    items.append((chips, "label"))
            self.line_layers[line] = items

    def caption_layer(self, u: Unit, text: str, t0: float, t1: float) -> Layer:
        return self.canvas.text(
            "caption", text, self.caption_box_for(u), size=self.caption_size, rows_max=self.caption_rows if u.kind != "use" else 2,
            fill=WHITE, bg=NAVY, line_gap=1.34, underline_numbers=RED, t0=t0, t1=t1,
        )  # fmt: skip

    def hook_layer(self, text: str, t0: float, t1: float) -> Layer:
        return self.canvas.text("hook", text, self.hook_box, size=self.hook_size, rows_max=3, fill=WHITE, bg=NAVY, line_gap=1.22, valign="center", t0=t0, t1=t1)

    def draw(self, t: float) -> Image.Image:
        line = self.current_line(t)
        if line is None:
            line = max([k for k, (a, _) in self.lines.items() if a <= t] or [0])
        a, b = self.lines.get(line, (0.0, self.end_start))
        z0, z1, x0, x1 = self.SHOTS[line % len(self.SHOTS)]
        k = min(max((t - a) / max(b - a, 0.1), 0), 1)
        img = Image.new("RGB", (W, H), NAVY)
        img.paste(self.kb.frame(z0 + (z1 - z0) * k, x0 + (x1 - x0) * k, 0.5), (0, 0))
        img.paste(self.panel, (0, self.PHOTO_H))
        self.overlay_common(img)
        if line in self.line_layers:
            kk = ease((t - a) / 0.14)
            for layer, role in self.line_layers[line]:
                layer.paste(img, alpha=kk, scale=(0.9 + 0.1 * kk) if role == "card" else 1.0)
        self.paste_caption(img, t, pop=True)
        self.progress(img, t, 1520, self.plan.accent, track=NAVY_DARK)
        return img


# --------------------------------------------------------------------------- #
# C 公式ページ型（撮れないときは数字カード型）
# --------------------------------------------------------------------------- #


class DesignC(Design):
    key = "C"
    name = "公式ページ型"
    caption_face = "black"
    caption_size = (66, 52)
    caption_rows = 3
    caption_stroke = 0
    caption_fill = INK
    caption_bg = PAPER
    hook_box = (96, 1262, 950, 1512)
    hook_size = (92, 60)
    CARD = (40, 330, 1040, 1188)
    BAR = 84

    def caption_box_for(self, u: Unit) -> tuple:
        return (96, 1262, 950, 1512)

    def show_photo_credit(self) -> bool:
        # 公式ページだけを映すとき（写真が公式発表の画像でない）は、写真を出さないのでクレジットも出さない
        return not self.page_mode or self.official_photo

    def setup(self) -> None:
        self.official_photo = (self.plan.article.get("imageCredit") or {}).get("kind") == "official" and self.plan.has_photo
        self.page_mode = self.plan.page is not None
        if not self.page_mode:
            self.name = "数字カード型"
        x0, y0, x1, y1 = self.CARD
        self.view = (x1 - x0, y1 - y0 - self.BAR)
        self.kb = KenBurns(self.plan.photo, self.view, max_zoom=1.15)
        if self.page_mode:
            page = self.plan.page
            self.page = page.resize((self.view[0], int(page.height * self.view[0] / page.width)), Image.LANCZOS)
        # カードの枠（影つき）
        self.bg = Image.new("RGB", (W, H), PAPER)
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle((x0 + 6, y0 + 14, x1 + 6, y1 + 18), radius=30, fill=(11, 26, 51, 60))
        shadow = shadow.filter(ImageFilter.GaussianBlur(14))
        self.bg.paste(shadow, (0, 0), shadow)
        d = ImageDraw.Draw(self.bg)
        d.rounded_rectangle(self.CARD, radius=30, fill=WHITE)
        d.rounded_rectangle((x0, y0, x1, y0 + self.BAR + 30), radius=30, fill=(233, 237, 243))
        d.rectangle((x0, y0 + self.BAR, x1, y0 + self.BAR + 30), fill=WHITE)
        c = self.canvas
        T = (0.0, self.end_start)
        if self.page_mode:
            d.ellipse((x0 + 34, y0 + 30, x0 + 58, y0 + 54), fill=(46, 160, 67))
            c.text("page_host", self.plan.page_host, rb(x0 + 76, y0 + 16, 700, "bold", 34), face="bold", size=(34, 24), rows_max=1, fill=INK, bg=(233, 237, 243), valign="center", t0=T[0], t1=T[1]).paste(self.bg)
            pill = pill_layer(c, "page_badge", "公式ページ", 760, y0 + 18, 30, NAVY, t0=T[0], t1=T[1])
            pill.paste(self.bg)
            self.credits.append(f"出典：{self.plan.page_host}（公式ページ）")
        else:
            c.text("card_title", "数字でわかる", rb(x0 + 40, y0 + 14, 950, "black", 36), face="black", size=(36, 28), rows_max=1, fill=INK, bg=(233, 237, 243), valign="center", t0=T[0], t1=T[1]).paste(self.bg)
        self.static_layers = self.make_labels(on_photo=False)
        if self.page_mode:
            self.static_layers.append(c.text("source_credit", f"出典：{self.plan.page_host}（公式ページを撮影）", rb(60, 1198, 950, "bold", 26), face="bold", size=(26, 22), rows_max=1, fill=INK, bg=PAPER, t0=T[0], t1=T[1]))
        self.line_layers: dict[int, list[tuple[Layer, str]]] = {}
        vy = y0 + self.BAR
        for line, (a, b) in self.lines.items():
            kind = self.kind_of_line(line)
            items = []
            if kind == "point":
                n = self.number_for_line(line)
                if n and self.page_mode:
                    items.append((self.number_card(n, (100, vy + 260, 980, vy + 600), t0=a, t1=b), "card"))
                elif n:
                    items.append((self.number_card(n, (x0, vy, x1, y1), bg=WHITE, fg=INK, stripe=None, t0=a, t1=b, value_size=(170, 90)), "full"))
                else:
                    items.append((self._point_badge(line, vy, a, b), "label"))
            elif kind == "use":
                # ページの文字と重ならないよう、白い下地の上に「使える？」と札
                ph = 250 if self.plan.chips else 130
                plate = Image.new("RGBA", (x1 - x0, ph), (0, 0, 0, 0))
                ImageDraw.Draw(plate).rounded_rectangle((0, 0, x1 - x0 - 1, ph + 29), radius=30, fill=(255, 255, 255, 245))
                ImageDraw.Draw(plate).rectangle((0, 0, x1 - x0, 4), fill=(214, 220, 230, 255))
                items.append((Layer(plate, x0, y1 - ph), "label"))
                items.append((pill_layer(c, "use_label", "使える？", 80, y1 - ph + 28, 46, RED, t0=a, t1=b, face="black"), "label"))
                if chips := chips_layer(c, self.plan.chips, (80, y1 - 130, 950, y1 - 16), 32, NAVY, t0=a, t1=b):
                    items.append((chips, "label"))
            self.line_layers[line] = items

    def _point_badge(self, line: int, vy: int, a: float, b: float) -> Layer:
        return pill_layer(self.canvas, "point_label", f"ポイント{line}", 80, vy + 30, 36, RED, t0=a, t1=b)

    def view_image(self, t: float) -> Image.Image:
        line = self.current_line(t)
        hook_end = self.lines.get(0, (0, 1.5))[1]
        if not self.page_mode or (t < hook_end and self.official_photo):
            p = t / self.total
            return self.kb.frame(1.0 + 0.1 * p, 0.3 + 0.4 * p, 0.45)
        # 公式ページ: つかみの後からゆっくり下へ（最後は止める）
        span = max(self.end_start - hook_end, 1.0)
        k = min(max((t - hook_end) / span, 0), 1)
        k = k * k * (3 - 2 * k)
        max_off = max(0, min(self.page.height - self.view[1], int(self.view[1] * 2.6)))
        off = int(max_off * k)
        del line
        return self.page.crop((0, off, self.view[0], off + self.view[1]))

    def draw(self, t: float) -> Image.Image:
        img = self.bg.copy()
        x0, y0, x1, y1 = self.CARD
        view = self.view_image(t)
        mask = Image.new("L", self.view, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, -40, self.view[0], self.view[1] - 1), radius=30, fill=255)
        img.paste(view, (x0, y0 + self.BAR), mask)
        self.overlay_common(img, labels_on_photo=False)
        line = self.current_line(t)
        if line is not None and line in self.line_layers:
            a, _ = self.lines[line]
            k = ease((t - a) / 0.18)
            for layer, role in self.line_layers[line]:
                if role == "full":
                    layer.paste(img, alpha=k)
                else:
                    layer.paste(img, alpha=k, scale=(0.94 + 0.06 * k) if role == "card" else 1.0)
        cur = self.current_caption(t)
        if cur:
            role = "hook" if cur[3].kind == "hook" else "caption"
            bb = next(x["bbox"] for x in self.canvas.texts if x["role"] == role and x["t0"] == round(cur[0], 3))
            ImageDraw.Draw(img).rounded_rectangle((60, bb[1] + 6, 72, bb[3] - 4), radius=5, fill=self.plan.accent)
        self.paste_caption(img, t)
        self.progress(img, t, 1526, self.plan.accent, track=(214, 220, 230))
        return img

    def hook_layer(self, text: str, t0: float, t1: float) -> Layer:
        return self.canvas.text("hook", text, self.hook_box, size=self.hook_size, rows_max=3, fill=INK, bg=PAPER, line_gap=1.22, valign="center", t0=t0, t1=t1)


DESIGNS = {"A": DesignA, "B": DesignB, "C": DesignC}


# --------------------------------------------------------------------------- #
# 書き出し
# --------------------------------------------------------------------------- #


_WORKER_DESIGN: Design | None = None
_WORKER_FPS = FPS


def _render_frame(i: int) -> bytes:
    assert _WORKER_DESIGN is not None
    return _WORKER_DESIGN.frame(i / _WORKER_FPS).convert("RGB").tobytes()


def frame_bytes(design: Design, n: int, fps: int, workers: int):
    """フレームを順番どおりに作る。workers>1 なら複数プロセスで（fork で設計図を共有）"""
    global _WORKER_DESIGN, _WORKER_FPS
    if workers <= 1 or sys.platform == "win32":
        for i in range(n):
            yield design.frame(i / fps).convert("RGB").tobytes()
        return
    import multiprocessing as mp

    _WORKER_DESIGN, _WORKER_FPS = design, fps
    with mp.get_context("fork").Pool(workers) as pool:
        yield from pool.imap(_render_frame, range(n), chunksize=6)
    _WORKER_DESIGN = None


def default_workers() -> int:
    return max(1, min(4, (os.cpu_count() or 2) - 1))


def encode(design: Design, audio: Path, out: Path, fps: int = FPS, preset: str = "fast", out_scale: float = 1.0, workers: int | None = None) -> None:
    n = int(round(design.total * fps))
    vf = ["-vf", f"scale={int(W * out_scale) // 2 * 2}:{int(H * out_scale) // 2 * 2}:flags=area"] if out_scale != 1.0 else []
    cmd = [
        shorts.ffmpeg_bin(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
        "-i", str(audio), "-map", "0:v", "-map", "1:a", *vf,
        "-c:v", "libx264", "-preset", preset, "-crf", "20", "-maxrate", "6M", "-bufsize", "12M",
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps * 2), "-keyint_min", str(fps * 2), "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "160k", "-ar", str(SR), "-ac", "2",
        "-t", f"{design.total:.3f}", "-movflags", "+faststart", str(out),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin
    try:
        for data in frame_bytes(design, n, fps, default_workers() if workers is None else workers):
            proc.stdin.write(data)
    finally:
        proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg が失敗しました")


def make_meta(a: dict, script: dict, voice: dict, image_credit: str, page_host: str = "") -> dict:
    url = f"https://{SITE}/articles/{a['slug']}"
    title = re.sub(r"\s+", " ", script["title"]).strip()
    title = title if len(title) <= 40 else title[:39] + "…"
    tags = []
    for tag in list(script.get("hashtags", []))[:4] + ["#サイソク"]:
        tag = "#" + re.sub(r"[\s#＃]+", "", str(tag))
        if len(tag) > 1 and tag not in tags:
            tags.append(tag)
    lines = [script["summary"].strip(), "", "▼記事はこちら", url, ""]
    if voice.get("credit"):
        lines.append(f"音声：{voice['credit']}")
    if image_credit:
        lines.append(image_credit)
    if page_host:
        lines.append(f"出典：{page_host}（公式ページ）")
    lines += ["", " ".join(tags)]
    return {"title": title, "description": "\n".join(lines).replace("\n\n\n", "\n\n"), "hashtags": tags, "url": url}


def contact_sheet(mp4: Path, design: Design, out: Path) -> None:
    """6コマ（つかみ・要点1〜3・使える？・締め）を横に並べる"""
    marks = []
    for line, label in [(0, "つかみ"), (1, "要点1"), (2, "要点2"), (3, "要点3"), (4, "使える？")]:
        if line in design.lines:
            a, b = design.lines[line]
            marks.append(((a + b) / 2 if line else min(0.5, b - 0.05), label))
    marks.append(((design.end_start + design.total) / 2, "締め"))
    tw, th = 300, 533
    sheet = Image.new("RGB", (len(marks) * (tw + 12) + 12, th + 64), (238, 240, 244))
    d = ImageDraw.Draw(sheet)
    f = shorts.font("bold", 24)
    for i, (t, label) in enumerate(marks):
        raw = subprocess.run(
            [shorts.ffmpeg_bin(), "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(mp4), "-frames:v", "1", "-vf", f"scale={tw}:{th}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True,
        ).stdout  # fmt: skip
        frame = Image.frombytes("RGB", (tw, th), raw[: tw * th * 3])
        x = 12 + i * (tw + 12)
        sheet.paste(frame, (x, 12))
        d.text((x, th + 22), f"{label}  {t:.1f}s", font=f, fill=INK)
    sheet.save(out)


def render_design(plan: Plan, key: str, audio: Path, out_dir: Path, *, fps: int = FPS, preset: str = "fast", out_scale: float = 1.0, profile: str = "production", sheet: bool = True, tag: str = "") -> dict:
    """1つのデザインを書き出して品質ゲートにかける。戻り値は report（ok / reasons を含む）"""
    slug = plan.article["slug"]
    name = f"{slug}__{key}{tag}"
    mp4 = out_dir / f"{name}.mp4"
    report: dict = {"slug": slug, "design": key, "errors": []}
    started = time.time()
    design = None
    try:
        design = DESIGNS[key](plan)
        report["design_name"] = design.name
        encode(design, audio, mp4, fps=fps, preset=preset, out_scale=out_scale)
    except TextFitError as error:
        report["errors"].append(str(error))
    except Exception as error:  # noqa: BLE001 - 描画の失敗は「動画なし」にするだけ
        import traceback

        report["errors"].append(f"{type(error).__name__}: {error}")
        report["traceback"] = traceback.format_exc()[-1500:]
    report["render_seconds"] = round(time.time() - started, 1)
    meta = make_meta(plan.article, plan.script, plan.narration.voice, plan.image_credit, plan.page_host if key == "C" and plan.page is not None else "")
    report.update(
        {
            "company": main_name(plan.article),
            "script": plan.script,
            "units": [u.to_dict() for u in plan.narration.units if u.kind != "outro"],
            "total": round(plan.narration.total, 3),
            "end_start": round(plan.narration.end_start, 3),
            "allowed_silences": plan.narration.allowed_silences,
            "texts": design.canvas.texts if design else [],
            "credits_shown": design.credits if design else [],
            "voice": plan.narration.voice,
            "image_credit": plan.image_credit if design and design.show_photo_credit() else "",
            "safe": list(SAFE),
            "fps": fps,
            **meta,
        }
    )
    if mp4.exists() and not report["errors"]:
        res = gate.check_video(mp4, report, plan.article, profile)
        if sheet:
            contact_sheet(mp4, design, out_dir / f"{name}.sheet.png")
    else:
        res = gate.GateResult(ok=False, reasons=[f"描画: {e}" for e in report["errors"]] or ["動画がありません"])
    report["gate"] = res.to_dict()
    report["sizeMB"] = round(mp4.stat().st_size / 1e6, 2) if mp4.exists() else 0
    (out_dir / f"{name}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    log(f"[v2] {name}: {'合格' if res.ok else '不合格'} {report['render_seconds']}秒 {report['sizeMB']}MB {res.reasons[:3]}")
    return report


def load_script(slug: str, a: dict, out_dir: Path, reuse: bool) -> dict:
    path = out_dir / f"{slug}.v2script.json"
    if reuse and path.exists():
        return json.loads(path.read_text("utf-8"))
    script, model = write_script(a)
    script["_model"] = model
    path.write_text(json.dumps(script, ensure_ascii=False, indent=2), "utf-8")
    log(f"[v2] {slug}: 台本 {model}")
    return script


def make_videos(slug: str, designs: list[str], out_dir: Path, tts: str = "auto", reuse: bool = False, bgm: str = "none", profile: str | None = None, script: dict | None = None, article: dict | None = None, pages: bool = True, **kw) -> list[dict]:
    a = article or shorts.load_article(slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = script or load_script(slug, a, out_dir, reuse)
    reports = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        narration = narrate(script, tts, work)
        log(f"[v2] {slug}: 声 {narration.voice['label']} / {narration.total:.1f}秒")
        audio = master_audio(narration.samples, narration.total, work, bgm)
        plan = make_plan(a, script, narration, out_dir / "_cache", want_page=pages and "C" in designs)
        prof = profile or ("production" if narration.voice["engine"] == "voicevox" else "preview")
        tag = kw.pop("tag", "_bgm" if bgm == "pad" else "")
        for key in designs:
            reports.append(render_design(plan, key, audio, out_dir, profile=prof, tag=tag, **kw))
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description="SNS 用ショート動画 v2")
    parser.add_argument("slugs", nargs="+")
    parser.add_argument("--design", default="A", help="A,B,C のどれか（カンマ区切りで複数）")
    parser.add_argument("--out", default=str(ROOT / "out" / "shorts_v2"))
    parser.add_argument("--tts", default=os.environ.get("SHORTS_TTS", "auto"), choices=["auto", "voicevox", "edge", "fake"])
    parser.add_argument("--bgm", default="none", choices=["none", "pad"])
    parser.add_argument("--reuse-script", action="store_true")
    parser.add_argument("--profile", choices=list(gate.PROFILES))
    args = parser.parse_args()
    import llm

    llm._load_env_local()
    designs = [d.strip().upper() for d in args.design.split(",") if d.strip()]
    failed = 0
    for slug in args.slugs:
        try:
            for r in make_videos(slug, designs, Path(args.out), args.tts, args.reuse_script, args.bgm, args.profile):
                failed += 0 if r["gate"]["ok"] else 1
        except Exception as error:  # noqa: BLE001 - 1本の失敗で全体を止めない
            log(f"[v2] {slug}: 失敗 {type(error).__name__}: {error}")
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
