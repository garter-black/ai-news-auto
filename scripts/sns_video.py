#!/usr/bin/env python3
"""SNS 投稿に動画を付けるかどうかの「入口」（投稿処理への組み込みはまだしていない）。

■ 失敗時の約束（契約）— 投稿処理に組み込むときは必ずこの形で使う
  1. video_for_post() は**例外を投げない**。台本・声・描画・品質ゲート・時間切れ・ディスク不足など、
     どんな失敗でも None を返す。
  2. None のとき、投稿は**動画なしで今までどおり**出す（文面・画像・リンク・時刻は一切変えない）。
     動画のために投稿を待たせない・止めない。動画は time_budget_s（既定 240秒）で打ち切る。
  3. 返ってくるのは品質ゲート（scripts/video_gate.py・production）に合格した動画だけ。
     production は VOICEVOX の声が必須（edge-tts の試作の声は絶対に投稿に付かない）。
  4. 動画付きの投稿が SNS/Buffer に**はっきり断られた**とき（受け付け前のエラー）だけ、
     動画なしの投稿に切り替えてよい（post_with_fallback）。受理されたか分からない失敗（時間切れ・通信断）は
     二重投稿を避けるため再送しない（AGENTS.md「SNSの受理不明記録を消して再送しない」と同じ）。
  5. Buffer では動画は公開 URL で渡す（アップロード不可）。Facebook / Threads / Bluesky は
     動画とリンクカード（linkAttachment）を同時に付けられないので、動画付きのときリンクは本文か返信に置く。

  使い方（組み込み時のイメージ）:
      video = sns_video.video_for_post(slug)              # 失敗なら None（例外なし）
      sns_video.post_with_fallback(
          lambda: post_text_only(...),                     # 今までの投稿
          (lambda v: post_with_video(..., url=publish(v))) if video else None,
          video,
      )
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


@dataclass
class VideoAttachment:
    path: Path
    design: str
    title: str
    description: str
    seconds: float
    bytes: int
    gate: dict


class DefiniteRejection(Exception):
    """SNS/Buffer が受け付ける前に、はっきり断った（入力エラーなど）。このときだけ動画なしに切り替えてよい"""


def _default_render(slug: str, design: str, out_dir: Path, tts: str, timeout: float) -> Path | None:
    """別プロセスで描画する（時間切れなら止められるように）。合格したら report のパスを返す"""
    cmd = [sys.executable, str(ROOT / "scripts" / "shorts_v2.py"), slug, "--design", design, "--tts", tts, "--out", str(out_dir), "--profile", "production"]
    try:
        subprocess.run(cmd, timeout=timeout, capture_output=True, text=True, check=False)
    except subprocess.TimeoutExpired:
        log(f"[sns_video] {slug}: 時間切れ（{timeout:.0f}秒）→ 動画なし")
        return None
    report = out_dir / f"{slug}__{design}.report.json"
    return report if report.exists() else None


def video_for_post(
    slug: str,
    *,
    design: str = os.environ.get("SNS_VIDEO_DESIGN", "A"),
    out_dir: Path | None = None,
    tts: str = "voicevox",
    time_budget_s: float = 240.0,
    render: Callable[..., Path | None] | None = None,
    enabled: bool | None = None,
) -> VideoAttachment | None:
    """品質ゲートに合格した動画を返す。少しでも問題があれば None（例外は投げない）"""
    started = time.time()
    try:
        if enabled is None:
            enabled = os.environ.get("SNS_VIDEO", "off") == "on"
        if not enabled:
            return None
        out_dir = Path(out_dir or ROOT / "out" / "sns_video")
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = out_dir / f"{slug}__{design}.report.json"
        cached = _load_ok(report_path)
        if cached:
            return cached
        report_path = (render or _default_render)(slug, design, out_dir, tts, time_budget_s)
        if time.time() - started > time_budget_s:
            log(f"[sns_video] {slug}: 時間切れ → 動画なし")
            return None
        return _load_ok(report_path) if report_path else None
    except Exception as error:  # noqa: BLE001 - 動画の失敗で投稿を止めない
        log(f"[sns_video] {slug}: 動画なしで投稿（{type(error).__name__}: {error}）")
        return None


def _load_ok(report_path: Path | None) -> VideoAttachment | None:
    """report が合格で、動画ファイルが report と同じ大きさで存在するときだけ"""
    if not report_path or not Path(report_path).exists():
        return None
    report = json.loads(Path(report_path).read_text("utf-8"))
    gate = report.get("gate") or {}
    mp4 = Path(report_path).with_name(Path(report_path).name.replace(".report.json", ".mp4"))
    if not gate.get("ok") or not mp4.exists() or mp4.stat().st_size != gate.get("metrics", {}).get("bytes"):
        return None
    if (report.get("voice") or {}).get("engine") != "voicevox":
        return None  # 試作の声の動画は付けない（約束3）
    return VideoAttachment(
        path=mp4,
        design=report.get("design", ""),
        title=report.get("title", ""),
        description=report.get("description", ""),
        seconds=float(gate.get("metrics", {}).get("duration") or 0),
        bytes=mp4.stat().st_size,
        gate=gate,
    )


def post_with_fallback(post_text_only: Callable[[], object], post_with_video: Callable[[VideoAttachment], object] | None, video: VideoAttachment | None):
    """動画があれば動画付きで投稿。はっきり断られたときだけ動画なしで出し直す（約束4）"""
    if video is None or post_with_video is None:
        return post_text_only()
    try:
        return post_with_video(video)
    except DefiniteRejection as error:
        log(f"[sns_video] 動画付きが断られたので動画なしで投稿: {error}")
        return post_text_only()
