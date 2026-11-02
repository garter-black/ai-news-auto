#!/usr/bin/env python3
"""SNS（Threads）に投稿した記事の解説動画を作り、あとから「動画で解説」の投稿として出す。記事ページにも動画を載せる。

2026-11-03 オーナー決定
  - SNS に投稿した記事は**全部**動画にする（今は Threads 1日約9枠）。それ以外の記事は収益が伸びてから。
  - 同じ台本から2本を自動で作る: サイト用（全部・80〜100秒）と SNS 用（短い版・30〜40秒。問い → 答え → 並べて初めて分かる違い
    →「◯◯は記事で」）。短い版も本当の答えを言う（中身の無い引きにしない）。2本目のために LLM は呼ばない（shorts_v3.short_script）。
  - 1週間の A/B: **同じ日の中で投稿ごとに**短い版と全部の版を交互に出す。版を記録し、記事のリンクに
    utm_source=threads&utm_medium=social&utm_campaign=sns_video&utm_content=short|full を付ける（worker が記事への訪問を版ごとに数える）。
  - 記事のリンクは投稿の本文に入れる（返信は作らない。threads_post.py と同じ決まり）。
  - 記事ページにはサイト用を載せる（components/ArticleVideo.tsx。押したときだけ読み込む・ポスター画像・音つき自動再生なし）。

**既定ではすべて止まっている（フラグ）。** 投稿を有効にするのはオーナーの確認のあと。

  python3 scripts/sns_video_followup.py --plan        # 対象の記事と、今日あと何本作れるかを表示するだけ
  python3 scripts/sns_video_followup.py               # フラグに従って 作る → 置く → 出す（既定は何もしない）
  python3 scripts/sns_video_followup.py --ab-report   # 短い版と全部の版の比べ（投稿ごとの平均: 記事への訪問・いいね・返信・再投稿・表示）
  python3 scripts/sns_video_followup.py --draft-test REPORT VIDEO_URL [--yes]   # Buffer に下書きを1本（--yes が無ければ表示だけ）

フラグ（環境変数。GitHub では Actions の Variables）
  SNS_VIDEO_FOLLOWUP=on    動画を作る（台本 → 声 → 2本の描画 → 品質ゲート）。off なら何もしない
  SNS_VIDEO_UPLOAD=on      合格した動画とポスター画像を Workers KV（saisoku-images）に置く → https://ai-tech-express.jp/video/<名前>
                           （cloudflare/video.ts が Range つきで配信。書き込みは images.kv_put と同じデプロイ用トークン。新しい鍵は不要）
  SNS_VIDEO_EMBED=on       記事 JSON に video（サイト用の URL・ポスター・秒数）を書く → 次のデプロイで記事ページに出る
  SNS_VIDEO_POST=live      Buffer で Threads に出す。それ以外は投稿文を記録するだけ
  SNS_VIDEO_DAILY_MAX=12   1日（日本時間）に作る本数の上限（SNS の投稿の数より多めに）
  SNS_VIDEO_PER_RUN=3      1回の実行で作る本数（1本 約5分）
  SNS_VIDEO_WINDOW_H=12    Threads に投稿してから何時間以内の記事を対象にするか
  SNS_VIDEO_AB_UNTIL=YYYY-MM-DD   この日（日本時間）まで短い版と全部の版を交互に出す。過ぎたら SNS_VIDEO_AFTER_AB（既定 short）
  SNS_VIDEO_STORE=kv       置き場所（kv＝既定 / r2。r2 は SNS_VIDEO_R2_BUCKET・SNS_VIDEO_PUBLIC_BASE と R2 の権限が要る）

約束（scripts/sns_video.py の約束を引き継ぐ）
  1. 文面の投稿は今までどおり先に出る（threads_post.py。このスクリプトは文面の投稿にさわらない）。
  2. 台本は Claude API（purpose=video。予算の範囲）→ Claude CLI → 無料モデル。どの経路でも事実の検査と編集長のレビューは同じに通す。
  3. 品質ゲートに落ちた・時間切れ・置けなかった → その版は出さない・再挑戦しない（記録は state/sns_video.json）。
  4. Buffer は出た投稿への返信を作れないので、動画は**別の投稿**として出す（本文＋動画。本文の最後に記事のリンク）。
  5. Buffer にはっきり断られても、受理されたか分からない失敗でも、二重投稿を避けるため再送しない。
  6. 出す時刻: 文面の投稿が Threads に出て（dueAt）20分以上たってから。1回の実行で出すのは1本だけ、動画の投稿どうしは40分以上あける。
     深夜（日本時間 1〜6時台）は出さず、朝の回に回す。作るのは先にやっておく（status=ready で待つ）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

STATE = ROOT / "state" / "sns_video.json"
POSTED = ROOT / "state" / "threads_posted.json"
ARTICLES = ROOT / "content" / "articles"
OUT = ROOT / "out" / "sns_video_followup"
SITE = "https://ai-tech-express.jp"
JST = timezone(timedelta(hours=9))
MAX_CHARS = 480
KV_LIMIT = 25 * 1024 * 1024  # Workers KV の1つの値の上限
CAMPAIGN = "sns_video"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def flags(env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    return {
        "followup": env.get("SNS_VIDEO_FOLLOWUP", "off") == "on",
        "upload": env.get("SNS_VIDEO_UPLOAD", "off") == "on",
        "embed": env.get("SNS_VIDEO_EMBED", "off") == "on",
        "post": env.get("SNS_VIDEO_POST", "off") == "live",
        "daily_max": int(env.get("SNS_VIDEO_DAILY_MAX", "12") or 12),
        "per_run": int(env.get("SNS_VIDEO_PER_RUN", "3") or 3),
        "window_h": float(env.get("SNS_VIDEO_WINDOW_H", "12") or 12),
        "ab_until": env.get("SNS_VIDEO_AB_UNTIL", ""),
        "after_ab": env.get("SNS_VIDEO_AFTER_AB", "short") or "short",
        "store": env.get("SNS_VIDEO_STORE", "kv") or "kv",
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


def _today(rec: dict, now: datetime) -> bool:
    at = _parse(rec.get("at", ""))
    return bool(at and at.astimezone(JST).date() == now.astimezone(JST).date())


def published_at(rec: dict) -> datetime | None:
    """文面の投稿が Threads に出た（出る）時刻。Buffer の予約は dueAt（作った時刻 postedAt より後のことがある）"""
    return _parse(rec.get("sentAt") or rec.get("dueAt") or rec.get("postedAt") or "")


# 動画の投稿を出してよい時間（日本時間）。深夜（1〜6時台）は出さず朝に回す（速報の静かな時間 BREAKING_QUIET_HOURS_JST と同じ考え）
QUIET_HOURS_JST = range(1, 7)
MIN_GAP_MIN = 40  # 動画の投稿どうしの間隔（同じ時間帯に続けて出さない）
AFTER_TEXT_MIN = 20  # 文面の投稿が出てから動画を出すまでの最短


def can_post_now(state: dict, now: datetime) -> tuple[bool, str]:
    """静かな時間・前の動画の投稿からの間隔を見て、今この回で動画を1本出してよいか"""
    if now.astimezone(JST).hour in QUIET_HOURS_JST:
        return False, "深夜（1〜6時台）は出さない"
    last = max((_parse(r.get("postedAt", "")) for r in (state.get("videos") or {}).values() if r.get("status") == "posted" and r.get("postedAt")), default=None)
    if last and now - last < timedelta(minutes=MIN_GAP_MIN):
        return False, f"前の動画の投稿から{MIN_GAP_MIN}分たっていない"
    return True, ""


def used_today(state: dict, now: datetime) -> int:
    """今日（日本時間）に作った本数（ゲート不合格・失敗も、作る手間がかかったので数える。途中で止まったものは数えない）"""
    return sum(1 for rec in (state.get("videos") or {}).values() if _today(rec, now) and rec.get("status") != "started")


def pick_variant(state: dict, now: datetime, ab_until: str, after_ab: str = "short") -> str:
    """SNS に出す版。A/B の間は、**同じ日の中で投稿ごとに**短い版 → 全部の版 → 短い版…と交互（その日に出した数で決める）"""
    try:
        until = date.fromisoformat(ab_until) if ab_until else None
    except ValueError:
        until = None
    if until is not None and now.astimezone(JST).date() > until:
        return after_ab if after_ab in ("short", "full") else "short"
    shown = sum(1 for rec in (state.get("videos") or {}).values() if rec.get("variant") and rec.get("status") == "posted" and _today({"at": rec.get("postedAt", "")}, now))
    return "short" if shown % 2 == 0 else "full"


def candidates(posted: dict, state: dict, now: datetime, window_h: float, load_article=None, eligible=None, score=None, retry_dry: bool = False) -> list[str]:
    """Threads に投稿済み・窓の中・まだ動画を作っていない・v3 の対象の記事を、Threads の点数の高い順に"""
    # 重いモジュール（Pillow・numpy）は、既定の関数を使うときだけ読み込む（テストは軽い環境でも動く）
    if load_article is None:
        import shorts

        load_article = shorts.load_article
    if eligible is None:
        import shorts_v3 as v3

        eligible = lambda a: bool(v3.page_urls(a))  # noqa: E731
    if score is None:
        import threads_post

        score = threads_post.score
    # 作ったが置かなかった（dry_run）記事は、置くのを有効にしたあとはもう一度作る（窓の中なら）
    # 撮れない・台本が作れないで落ちた記事は、仕組みを直したあと1回だけやり直す（tries < 2）
    def retry(r: dict) -> bool:
        if retry_dry and r.get("status") == "dry_run":
            return True
        why = f"{r.get('reason', '')} {' '.join(v.get('reasons', [''])[0] for v in (r.get('versions') or {}).values() if v.get('reasons'))}"
        return r.get("status") == "gate_failed" and int(r.get("tries", 1)) < 2 and bool(re.search(r"撮れる|台本|公式ページか台本が無い|文字が入りません", why))

    done = {k for k, r in (state.get("videos") or {}).items() if not retry(r)}
    rows = []
    for slug, rec in (posted.get("posted") or {}).items():
        at = published_at(rec)
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


def article_link(report: dict, variant: str) -> str:
    """記事のリンク（Threads の UTM ＋ 版）。threads_post.utm_link と同じ形"""
    import threads_post

    return threads_post.utm_link(report.get("url", ""), "threads", CAMPAIGN, variant)


def compose_post(report: dict, variant: str = "short") -> str:
    """動画の投稿文: 問い＋「◯秒で解説」＋要約。本文の最後に記事のリンクと音声のクレジット（返信は作らない）"""
    sv3 = report.get("script_v3") or {}
    hook = ((sv3.get("hook") or {}).get("text") or report.get("title") or "").strip()
    seconds = int(round(float(report.get("total") or 0)))
    summary = (sv3.get("summary") or "").strip()
    head = f"{hook}\n公式ページの画面を見ながら、{seconds}秒で解説しました。"
    voice = (report.get("voice") or {}).get("credit")
    tail = f"\n\n詳しくはこちら👇\n{article_link(report, variant)}" + (f"\n音声：{voice}" if voice else "")
    room = MAX_CHARS - len(head) - len(tail) - 2
    if summary and room > 20:
        body = f"{head}\n\n{summary if len(summary) <= room else summary[: room - 1] + '…'}"
    else:
        body = head
    return body + tail


def buffer_input(channel_id: str, body: str, video_url: str, topic: str = "") -> dict:
    """Buffer の createPost の入力（本文＋動画。いますぐ出す。返信は作らない）"""
    meta: dict = {"thread": [{"text": body, "assets": [{"video": {"url": video_url}}]}]}
    if topic:
        meta["topic"] = topic
    return {"channelId": channel_id, "text": body, "schedulingType": "automatic", "mode": "shareNow", "assets": [], "metadata": {"threads": meta}}


def video_name(slug: str, data: bytes, ext: str = "mp4") -> str:
    """配信の名前（中身のハッシュ入り＝変わらないので1年キャッシュできる）"""
    import hashlib

    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in slug)[:120]
    return f"{safe}-{hashlib.sha256(data).hexdigest()[:10]}.{ext}"


def upload_kv(path: Path, slug: str, ext: str = "mp4") -> str:
    """Workers KV（saisoku-images）に video/<名前> で置き、サイトの /video/<名前> の URL を返す"""
    import images

    data = path.read_bytes()
    if len(data) >= KV_LIMIT:
        raise RuntimeError(f"KV の上限より大きい（{len(data) / 1e6:.1f}MB）")
    name = video_name(slug, data, ext)
    images.kv_put(f"video/{name}", data)
    return f"{SITE}/video/{name}"


def upload_r2(mp4: Path, bucket: str, public_base: str, key: str) -> str:
    """（予備）Cloudflare R2 に置いて公開 URL を返す（wrangler。CLOUDFLARE_API_TOKEN に R2 の書き込み権限が要る）"""
    if not bucket or not public_base:
        raise RuntimeError("SNS_VIDEO_R2_BUCKET / SNS_VIDEO_PUBLIC_BASE が未設定")
    cmd = ["npx", "--yes", "wrangler", "r2", "object", "put", f"{bucket}/{key}", "--file", str(mp4), "--content-type", "video/mp4", "--remote"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"R2 に置けませんでした: {proc.stderr[-300:]}")
    return f"{public_base}/{key}"


def poster_of(mp4: Path, at: float = 0.6) -> Path:
    """記事ページのポスター画像（つかみの問いが出ている所。1280x720 の JPEG）"""
    import shorts

    out = mp4.with_suffix(".poster.jpg")
    subprocess.run([shorts.ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{at}", "-i", str(mp4), "-frames:v", "1", "-vf", "scale=1280:720:flags=area", "-q:v", "3", str(out)], check=True)
    return out


def write_embed(slug: str, info: dict) -> None:
    """記事 JSON に video を書く（components/ArticleVideo.tsx が使う。次のデプロイで記事ページに出る）"""
    path = ARTICLES / f"{slug}.json"
    article = json.loads(path.read_text("utf-8"))
    article["video"] = info
    path.write_text(json.dumps(article, ensure_ascii=False, indent=2) + "\n", "utf-8")


def post_report(report: dict) -> dict:
    """投稿文を作るのに要る所だけ（state に残して、あとの回で出す）"""
    sv3 = report.get("script_v3") or {}
    return {
        "title": report.get("title", ""), "url": report.get("url", ""), "total": report.get("total"), "voice": report.get("voice") or {},
        "script_v3": {"hook": {"text": (sv3.get("hook") or {}).get("text", "")}, "summary": sv3.get("summary", "")},
    }  # fmt: skip


def run(now: datetime | None = None, env: dict | None = None, plan_only: bool = False, make_versions=None, select=None, poster=None) -> list[dict]:
    """1回分: ①新しく SNS に出た記事の動画を作る（2つの版）→ 置く → 記事に載せる ②用意できた動画を1本だけ投稿する（静かな時間・間隔を守る）"""
    f = flags(env)
    now = now or datetime.now(timezone.utc)
    state = load_json(STATE, {"videos": {}})
    state.setdefault("videos", {})
    posted = load_json(POSTED, {"posted": {}})
    left = max(0, min(f["per_run"], f["daily_max"] - used_today(state, now)))
    pick_fn = select or (lambda p, s, n, w: candidates(p, s, n, w, retry_dry=f["upload"]))
    picks = pick_fn(posted, state, now, f["window_h"])[:left] if left else []
    log(f"[video-followup] フラグ {json.dumps({k: f[k] for k in ('followup', 'upload', 'embed', 'post', 'daily_max', 'per_run')})}・この回 {left}本まで・対象 {picks}")
    if plan_only or not f["followup"]:
        if not f["followup"]:
            log("[video-followup] SNS_VIDEO_FOLLOWUP が on ではないので、何も作らない")
        return [{"slug": s, "status": "planned"} for s in picks]
    if make_versions is None:
        import shorts_v3 as v3

        make_versions = v3.make_versions
    results = []
    for slug in picks:
        prev_tries = int((state["videos"].get(slug) or {}).get("tries", 0) or 0)
        rec = {"at": now.isoformat(timespec="seconds"), "status": "started", "tries": prev_tries + 1, "textAt": (published_at((posted.get("posted") or {}).get(slug) or {}) or now).isoformat(timespec="seconds")}
        state["videos"][slug] = rec
        save_state(state)  # 途中で落ちても同じ記事を二度作らない
        reports = make_versions(slug, OUT)
        ok = {k: bool((r.get("gate") or {}).get("ok")) and (OUT / f"{slug}__v3{'short' if k == 'short' else ''}.mp4").exists() for k, r in reports.items()}
        rec["versions"] = {k: {"ok": ok[k], "seconds": r.get("total"), "reasons": (r.get("gate") or {}).get("reasons", [])[:3], **({"post": post_report(r)} if ok[k] else {})} for k, r in reports.items()}
        rec["timings"] = {k: r.get("timings") for k, r in reports.items()}
        rec["ok"] = any(ok.values())
        rec["reason"] = fail_reason(reports, ok)
        rec["pages"] = sorted({p.get("host", "") for r in reports.values() for p in (r.get("pages") or [])})
        if not any(ok.values()):
            rec["status"] = "gate_failed"
            save_state(state)
            results.append({"slug": slug, **rec})
            continue
        if not f["upload"]:
            rec["status"] = "dry_run"
            save_state(state)
            results.append({"slug": slug, **rec})
            continue
        try:
            urls = {}
            for k in ("full", "short"):
                if ok.get(k):
                    mp4 = OUT / f"{slug}__v3{'short' if k == 'short' else ''}.mp4"
                    urls[k] = upload_r2(mp4, f["bucket"], f["public_base"], f"videos/{now.astimezone(JST):%Y%m%d}/{mp4.name}") if f["store"] == "r2" else upload_kv(mp4, slug)
            if ok.get("full"):
                urls["poster"] = upload_kv(poster_of(OUT / f"{slug}__v3.mp4"), slug, "jpg")
            rec.update({"status": "ready", "urls": urls})
        except Exception as error:  # noqa: BLE001 - 置けなければ動画なし
            rec.update({"status": "upload_failed", "error": str(error)[:300]})
            save_state(state)
            results.append({"slug": slug, **rec})
            continue
        if f["embed"] and urls.get("full"):
            try:
                full = reports["full"]
                write_embed(slug, {"src": urls["full"], "poster": urls.get("poster", ""), "seconds": round(float(full.get("total") or 0), 1), "title": full.get("title", ""), "credit": (full.get("voice") or {}).get("credit", "")})
                rec["embedded"] = True
            except Exception as error:  # noqa: BLE001
                rec["embedError"] = str(error)[:200]
        save_state(state)
        results.append({"slug": slug, **rec})
    if f["post"]:
        out = post_one(state, now, f, poster or post_video)
        if out:
            results.append(out)
        save_state(state)
    return results


def fail_reason(reports: dict, ok: dict) -> str:
    """動画を作れなかった（片方だけ落ちた）理由を1行で（記録・日ごとの成功率の内訳に使う）"""
    out = []
    for k, r in reports.items():
        if ok.get(k):
            continue
        why = r.get("failReason") or "・".join((r.get("gate") or {}).get("reasons", [])[:2]) or "不明"
        out.append(f"{k}: {why[:160]}")
    return " / ".join(dict.fromkeys(out))


def daily_rates(state: dict) -> dict:
    """日（日本時間）ごとの、動画を作れた割合と、作れなかった理由の内訳"""
    days: dict = {}
    for rec in (state.get("videos") or {}).values():
        at = _parse(rec.get("at", ""))
        if not at or rec.get("status") == "started":
            continue
        d = days.setdefault(at.astimezone(JST).date().isoformat(), {"tried": 0, "ok": 0, "reasons": {}})
        d["tried"] += 1
        good = rec.get("ok") if "ok" in rec else rec.get("status") in ("ready", "posted", "dry_run", "uploaded")
        if good:
            d["ok"] += 1
        else:
            key = (rec.get("reason") or rec.get("status") or "不明").split(" / ")[0][:60]
            d["reasons"][key] = d["reasons"].get(key, 0) + 1
    for d in days.values():
        d["rate"] = round(d["ok"] / max(d["tried"], 1), 2)
    return dict(sorted(days.items()))


def post_one(state: dict, now: datetime, f: dict, poster) -> dict | None:
    """用意できた動画（ready）を1本だけ出す。文面の投稿が出てから20分以上・24時間以内・古い順。静かな時間と間隔を守る"""
    allowed, why = can_post_now(state, now)
    if not allowed:
        log(f"[video-followup] この回は動画を出さない: {why}")
        return None
    ready = []
    for slug, rec in (state.get("videos") or {}).items():
        text_at = _parse(rec.get("textAt", "")) or _parse(rec.get("at", ""))
        if rec.get("status") != "ready" or not text_at:
            continue
        if now - text_at < timedelta(minutes=AFTER_TEXT_MIN) or now - text_at > timedelta(hours=24):
            continue
        ready.append((text_at, slug, rec))
    if not ready:
        return None
    ready.sort()
    _, slug, rec = ready[0]
    variant = pick_variant(state, now, f["ab_until"], f["after_ab"])
    versions = rec.get("versions") or {}
    if not (versions.get(variant) or {}).get("ok") or variant not in (rec.get("urls") or {}):
        variant = next((k for k in ("short", "full") if (versions.get(k) or {}).get("ok") and k in (rec.get("urls") or {})), "")
    if not variant:
        rec["status"] = "post_skipped"
        return {"slug": slug, **rec}
    body = compose_post(versions[variant]["post"], variant)
    rec.update({"variant": variant, "body": body, "link": article_link(versions[variant]["post"], variant)})
    rec.update(poster(slug, body, rec["urls"][variant]))
    log(f"[video-followup] 投稿 {slug}（{variant}）: {rec.get('status')} {rec.get('bufferId', '')} {rec.get('error', '')}")
    return {"slug": slug, **rec}


def post_video(slug: str, body: str, video_url: str) -> dict:
    """Buffer で Threads に出す。失敗しても再送しない（約束5）"""
    import shorts
    import threads_post

    api_key = os.environ.get("BUFFER_API_KEY", "")
    try:
        channel = threads_post.buffer_threads_channel(api_key)
        if not channel:
            raise RuntimeError("Buffer に Threads のチャンネルが無い")
        post_id = threads_post.create_post(api_key, buffer_input(channel["id"], body, video_url, threads_post.topic_of(shorts.load_article(slug))))
        return {"status": "posted", "bufferId": post_id, "postedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    except Exception as error:  # noqa: BLE001 - 受理されたか分からない失敗も含め、再送しない
        return {"status": "post_failed", "error": str(error)[:300], "postedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def ab_rows(state: dict, metrics: dict, visits: dict) -> dict:
    """短い版と全部の版の比べ: 投稿ごとの平均（記事への訪問・いいね・返信・再投稿・表示）"""
    out: dict = {}
    for slug, rec in (state.get("videos") or {}).items():
        if rec.get("status") != "posted" or rec.get("variant") not in ("short", "full"):
            continue
        m = (metrics.get(rec.get("bufferId", "")) or {}).get("metrics") or {}
        row = out.setdefault(rec["variant"], {"posts": 0, "visits": 0.0, "likes": 0.0, "replies": 0.0, "reposts": 0.0, "views": 0.0})
        row["posts"] += 1
        row["visits"] += visits.get((slug, rec["variant"]), 0)
        row["likes"] += float(m.get("reactions", m.get("likes", 0)) or 0)
        row["replies"] += float(m.get("comments", m.get("replies", 0)) or 0)
        row["reposts"] += float(m.get("reposts", 0) or 0)
        row["views"] += float(m.get("views", m.get("impressions", 0)) or 0)
    for row in out.values():
        for k in ("visits", "likes", "replies", "reposts", "views"):
            row[k] = round(row[k] / max(row["posts"], 1), 2)
    return out


def fetch_visits() -> dict:
    """worker が数えた「動画投稿から記事に来た人」（AFF_KV の v:<日付>:<slug>:<版>:...）を (slug, 版) ごとに"""
    import urllib.parse
    import urllib.request

    import images

    ns = os.environ.get("AFF_KV_ID") or "5de7984f700648c58d0400adaf75a614"
    counts: dict = {}
    cursor = ""
    while True:
        url = f"https://api.cloudflare.com/client/v4/accounts/{images.ACCOUNT_ID}/storage/kv/namespaces/{ns}/keys?prefix=v:&limit=1000" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {images.cf_token('kv')}"})
        with urllib.request.urlopen(req, timeout=60) as res:
            body = json.loads(res.read())
        for key in body.get("result") or []:
            parts = key["name"].split(":")
            if len(parts) >= 5:
                counts[(parts[2], parts[3])] = counts.get((parts[2], parts[3]), 0) + 1
        cursor = (body.get("result_info") or {}).get("cursor") or ""
        if not cursor:
            return counts


def ab_report() -> dict:
    import threads_learn

    state = load_json(STATE, {"videos": {}})
    metrics = threads_learn.fetch_metrics(os.environ.get("BUFFER_API_KEY", "")) if os.environ.get("BUFFER_API_KEY") else {}
    try:
        visits = fetch_visits()
    except Exception as error:  # noqa: BLE001
        log(f"[video-followup] 記事への訪問を読めない: {type(error).__name__}")
        visits = {}
    return {"ab": ab_rows(state, metrics, visits), "daily": daily_rates(state)}


def draft_test(report_path: Path, video_url: str, yes: bool = False, variant: str = "short") -> dict:
    """Buffer に Threads の動画投稿を**下書き**（saveToDraft・公開しない）で1本作り、Threads で動画が通るかを確かめる。
    yes=False なら Buffer に送る入力を表示するだけ（何も送らない）"""
    import threads_post

    report = json.loads(Path(report_path).read_text("utf-8"))
    inp = buffer_input("<threads のチャンネル>", compose_post(report, variant), video_url)
    inp.update({"mode": "addToQueue", "saveToDraft": True})
    if not yes:
        log("[video-followup] 下書きのテスト（送らない）。--yes を付けたときだけ Buffer に下書きを作る")
        return {"input": inp, "sent": False}
    api_key = os.environ.get("BUFFER_API_KEY", "")
    channel = threads_post.buffer_threads_channel(api_key)
    if not channel:
        raise RuntimeError("Buffer に Threads のチャンネルが無い")
    inp["channelId"] = channel["id"]
    draft_id = threads_post.create_post(api_key, inp)
    return {"input": inp, "sent": True, "draftId": draft_id, "check": read_draft(api_key, channel["id"], draft_id)}


def read_draft(api_key: str, channel_id: str, post_id: str) -> dict:
    """作った下書きを Buffer から読み直す（本文・状態・付いている動画）。読めなくても例外は投げない"""
    import threads_post

    fields = "id text status channelId assets { thumbnail mimeType source }"
    try:
        orgs = threads_post.buffer_graphql(api_key, "query { account { organizations { id } } }")["account"]["organizations"]
    except Exception as error:  # noqa: BLE001
        return {"error": f"organizations: {error}"[:300]}
    tries = []
    for org in orgs:
        for status in ("[draft]", ""):
            flt = f"status: {status}, channelIds: {json.dumps([channel_id])}" if status else f"channelIds: {json.dumps([channel_id])}"
            query = f"query {{ posts(first: 50, input: {{ organizationId: {json.dumps(org['id'])}, filter: {{ {flt} }} }}) {{ edges {{ node {{ {fields} }} }} }} }}"
            try:
                edges = threads_post.buffer_graphql(api_key, query)["posts"]["edges"]
            except Exception as error:  # noqa: BLE001
                tries.append(str(error)[:200])
                continue
            for edge in edges:
                if edge["node"].get("id") == post_id:
                    return {"found": True, "post": edge["node"], "filter": status or "なし"}
    return {"found": False, "errors": tries}


def main() -> None:
    parser = argparse.ArgumentParser(description="SNS に投稿した記事の動画を作って後から出す（既定は止まっている）")
    parser.add_argument("--plan", action="store_true", help="対象と残りの本数を表示するだけ")
    parser.add_argument("--ab-report", action="store_true", help="短い版と全部の版の比べ")
    parser.add_argument("--draft-test", nargs=2, metavar=("REPORT_JSON", "VIDEO_URL"), help="Buffer に Threads の動画の下書きを作るテスト（--yes が無ければ表示だけ）")
    parser.add_argument("--yes", action="store_true", help="--draft-test を本当に送る")
    args = parser.parse_args()
    import llm

    llm._load_env_local()
    if args.draft_test:
        print(json.dumps(draft_test(Path(args.draft_test[0]), args.draft_test[1], args.yes), ensure_ascii=False, indent=2))
        return
    if args.ab_report:
        print(json.dumps(ab_report(), ensure_ascii=False, indent=2))
        return
    for row in run(plan_only=args.plan):
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
