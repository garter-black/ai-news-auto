#!/usr/bin/env python3
"""SNS（Threads）に投稿した記事だけ、長めの解説動画（v3）を作り、あとから「動画で解説」の投稿として出す。

2026-11-03 オーナー決定: 「当面は SNS に投稿する記事だけ動画を作る」「まず Threads に1日2本」。
**既定ではすべて止まっている（フラグ）。** 投稿を有効にするのはオーナーの確認のあと。

  python3 scripts/sns_video_followup.py --plan        # 対象の記事と、今日あと何本作れるかを表示するだけ（何も作らない）
  python3 scripts/sns_video_followup.py               # フラグに従って 作る → 置く → 出す（既定は何もしない）

フラグ（環境変数。GitHub では Actions の Variables）
  SNS_VIDEO_FOLLOWUP=on    動画を作る（台本 → 声 → 描画 → 品質ゲート）。off なら何もしない
  SNS_VIDEO_UPLOAD=on      ゲートに合格した動画を Cloudflare R2 に置いて公開 URL にする
  SNS_VIDEO_POST=live      Buffer で Threads に出す。それ以外は投稿文の下書きを表示・記録するだけ
  SNS_VIDEO_DAILY_MAX=2    1日（日本時間）に出す本数の上限
  SNS_VIDEO_WINDOW_H=12    Threads に投稿してから何時間以内の記事を対象にするか
  SNS_VIDEO_R2_BUCKET / SNS_VIDEO_PUBLIC_BASE   R2 のバケット名と、公開 URL の頭（例 https://media.ai-tech-express.jp）

流れと約束（scripts/sns_video.py の約束を引き継ぐ）
  1. 文面の投稿は今までどおり先に出る（threads_post.py。このスクリプトは文面の投稿に一切さわらない）。
  2. 対象: state/threads_posted.json に載った記事のうち、投稿から SNS_VIDEO_WINDOW_H 時間以内・まだ動画を作っていない・
     公式ページを撮れる記事（v3 の対象）。点数（threads_post.score）の高い順に、1日 SNS_VIDEO_DAILY_MAX 本まで。
  3. 台本は Claude API（ANTHROPIC_API_KEY があるとき。台本 Opus・レビュー Sonnet）→ Claude CLI → 無料モデル（scripts/llm.py）。
  4. 品質ゲートに落ちた・時間切れ・置けなかった → その記事は「動画なし」（何も出さない・再挑戦しない）。
  5. Buffer は既に出た投稿への返信を作れないので、動画は**別の投稿**として出す（本文＋動画、返信に記事のリンクとクレジット）。
  6. Buffer にはっきり断られたら記録して終わり。受理されたか分からない失敗（時間切れ・通信断）は二重投稿を避けるため再送しない。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

STATE = ROOT / "state" / "sns_video.json"
POSTED = ROOT / "state" / "threads_posted.json"
OUT = ROOT / "out" / "sns_video_followup"
JST = timezone(timedelta(hours=9))
MAX_CHARS = 480


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def flags(env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    return {
        "followup": env.get("SNS_VIDEO_FOLLOWUP", "off") == "on",
        "upload": env.get("SNS_VIDEO_UPLOAD", "off") == "on",
        "post": env.get("SNS_VIDEO_POST", "off") == "live",
        "daily_max": int(env.get("SNS_VIDEO_DAILY_MAX", "2") or 2),
        "window_h": float(env.get("SNS_VIDEO_WINDOW_H", "12") or 12),
        "bucket": env.get("SNS_VIDEO_R2_BUCKET", ""),
        "public_base": env.get("SNS_VIDEO_PUBLIC_BASE", "").rstrip("/"),
    }


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", "utf-8")


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def used_today(state: dict, now: datetime) -> int:
    """今日（日本時間）に出した（または出す予定で作った）本数。ゲート不合格・失敗は数えない"""
    today = now.astimezone(JST).date()
    n = 0
    for rec in (state.get("videos") or {}).values():
        at = _parse(rec.get("at", ""))
        if at and at.astimezone(JST).date() == today and rec.get("status") in ("posted", "dry_run", "uploaded"):
            n += 1
    return n


def candidates(posted: dict, state: dict, now: datetime, window_h: float, load_article=None, eligible=None, score=None) -> list[str]:
    """Threads に投稿済み・窓の中・まだ動画を作っていない・v3 の対象の記事を、点数の高い順に"""
    import shorts
    import shorts_v3 as v3
    import threads_post

    load_article = load_article or shorts.load_article
    eligible = eligible or (lambda a: bool(v3.page_urls(a)))
    score = score or threads_post.score
    done = set((state.get("videos") or {}).keys())
    rows = []
    for slug, rec in (posted.get("posted") or {}).items():
        at = _parse(rec.get("postedAt") or rec.get("sentAt") or "")
        if slug in done or not at or now - at > timedelta(hours=window_h) or at > now:
            continue
        try:
            article = load_article(slug)
        except (Exception, SystemExit):  # noqa: BLE001 - 記事が無い（消えた・まとめ記事）なら対象外
            continue
        if not eligible(article):
            continue
        rows.append((score(article), at, slug))
    rows.sort(key=lambda r: (-r[0], r[1]))
    return [slug for _, _, slug in rows]


def compose_post(report: dict) -> tuple[str, str]:
    """動画の投稿文（本文・返信）。本文: 問い＋「動画で◯秒にまとめました」＋要約。返信: 記事のリンクとクレジット"""
    sv3 = report.get("script_v3") or {}
    hook = ((sv3.get("hook") or {}).get("text") or report.get("title") or "").strip()
    seconds = int(round(float(report.get("total") or 0)))
    summary = (sv3.get("summary") or "").strip()
    head = f"{hook}\n公式ページの画面を見ながら、{seconds}秒で解説しました。"
    body = f"{head}\n\n{summary}" if summary else head
    if len(body) > MAX_CHARS:
        body = body[: MAX_CHARS - 1] + "…"
    lines = ["▼記事はこちら", report.get("url", "")]
    voice = (report.get("voice") or {}).get("credit")
    if voice:
        lines.append(f"音声：{voice}")
    hosts = "・".join(dict.fromkeys(p.get("host", "") for p in report.get("pages") or [] if p.get("host")))
    if hosts:
        lines.append(f"画面：{hosts}（公式ページ）")
    lines.append("BGM・効果音：サイソクニュース自作")
    return body, "\n".join(x for x in lines if x)


def buffer_input(channel_id: str, body: str, reply: str, video_url: str, link: str, title: str, topic: str = "") -> dict:
    """Buffer の createPost の入力（本文＋動画、返信にリンクカード）。いますぐ出す"""
    thread = [
        {"text": body, "assets": [{"video": {"url": video_url}}]},
        {"text": reply, "assets": [], "metadata": {"threads": {"linkAttachment": {"url": link, "title": title}}}},
    ]
    meta = {"thread": thread}
    if topic:
        meta["topic"] = topic
    return {"channelId": channel_id, "text": body, "schedulingType": "automatic", "mode": "shareNow", "assets": [], "metadata": {"threads": meta}}


def upload_r2(mp4: Path, bucket: str, public_base: str, key: str) -> str:
    """Cloudflare R2 に置いて公開 URL を返す（wrangler。CLOUDFLARE_API_TOKEN に R2 の書き込み権限が要る）"""
    if not bucket or not public_base:
        raise RuntimeError("SNS_VIDEO_R2_BUCKET / SNS_VIDEO_PUBLIC_BASE が未設定")
    cmd = ["npx", "--yes", "wrangler", "r2", "object", "put", f"{bucket}/{key}", "--file", str(mp4), "--content-type", "video/mp4", "--remote"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"R2 に置けませんでした: {proc.stderr[-300:]}")
    return f"{public_base}/{key}"


def run(now: datetime | None = None, env: dict | None = None, plan_only: bool = False) -> list[dict]:
    f = flags(env)
    now = now or datetime.now(timezone.utc)
    state = load_json(STATE, {"videos": {}})
    state.setdefault("videos", {})
    posted = load_json(POSTED, {"posted": {}})
    left = max(0, f["daily_max"] - used_today(state, now))
    picks = candidates(posted, state, now, f["window_h"])[:left] if left else []
    log(f"[video-followup] フラグ {json.dumps({k: f[k] for k in ('followup', 'upload', 'post', 'daily_max')})}・今日あと{left}本・対象 {picks}")
    if plan_only or not f["followup"]:
        if not f["followup"]:
            log("[video-followup] SNS_VIDEO_FOLLOWUP が on ではないので、何も作らない")
        return [{"slug": s, "status": "planned"} for s in picks]
    import shorts_v3 as v3
    import threads_post

    results = []
    for slug in picks:
        rec = {"at": now.isoformat(timespec="seconds"), "status": "started"}
        state["videos"][slug] = rec
        save_state(state)  # 途中で落ちても同じ記事を二度作らない
        report = v3.make_video(slug, OUT)
        rec.update({"seconds": report.get("total"), "timings": report.get("timings"), "gate": (report.get("gate") or {}).get("ok")})
        mp4 = OUT / f"{slug}__v3.mp4"
        if not (report.get("gate") or {}).get("ok") or not mp4.exists():
            rec.update({"status": "gate_failed", "reasons": (report.get("gate") or {}).get("reasons", [])[:5]})
            save_state(state)
            results.append({"slug": slug, **rec})
            continue
        body, reply = compose_post(report)
        rec.update({"body": body, "reply": reply})
        if not f["upload"]:
            rec["status"] = "dry_run"
            save_state(state)
            results.append({"slug": slug, **rec})
            continue
        try:
            url = upload_r2(mp4, f["bucket"], f["public_base"], f"videos/{now.astimezone(JST):%Y%m%d}/{slug}.mp4")
            rec.update({"status": "uploaded", "videoUrl": url})
        except Exception as error:  # noqa: BLE001 - 置けなければ動画なし
            rec.update({"status": "upload_failed", "error": str(error)[:300]})
            save_state(state)
            results.append({"slug": slug, **rec})
            continue
        if f["post"]:
            api_key = os.environ.get("BUFFER_API_KEY", "")
            try:
                channel = threads_post.buffer_threads_channel(api_key)
                if not channel:
                    raise RuntimeError("Buffer に Threads のチャンネルが無い")
                import shorts

                article = shorts.load_article(slug)
                link = report.get("url", "")
                post_id = threads_post.create_post(api_key, buffer_input(channel["id"], body, reply, url, link, report.get("title", ""), threads_post.topic_of(article)))
                rec.update({"status": "posted", "bufferId": post_id})
            except RuntimeError as error:
                # Buffer の MutationError（はっきり断られた）も通信の失敗も、再送はしない（約束6）
                rec.update({"status": "post_failed", "error": str(error)[:300]})
        save_state(state)
        results.append({"slug": slug, **rec})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="SNS に投稿した記事の動画を作って後から出す（既定は止まっている）")
    parser.add_argument("--plan", action="store_true", help="対象と残りの本数を表示するだけ")
    args = parser.parse_args()
    import llm

    llm._load_env_local()
    for row in run(plan_only=args.plan):
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
