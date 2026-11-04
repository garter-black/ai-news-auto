#!/usr/bin/env python3
"""SNS（Threads）に出す記事の解説動画を作り、**予約済みの投稿の写真を動画に差し替える**（1記事1投稿）。記事ページにも動画を載せる。

2026-11-03 オーナー決定（その2）: 「SNS投稿なのに動画じゃない投稿がある」「写真の投稿と動画の投稿が2本並ぶのは変」
  - 1記事につき投稿は1本。通常の投稿は threads_post.py が最大8時間先まで Buffer に予約する。その時刻より前に
    動画がゲートに合格したら、Buffer の editPost で**予約中の投稿の写真を動画に差し替える**（本文の記事のリンクを
    utm_campaign=sns_video&utm_content=short|full に替え、入るなら「◯秒で解説」の行と音声のクレジットを足す。予約の時刻は変えない）。
  - 間に合わなければ写真のまま出る。**同じ記事の2本目（動画だけの投稿）は出さない**（記事ページには動画を載せる）。
  - 速報（すぐ出す文面の投稿）も2本目は出さない。後追いの動画投稿は SNS_VIDEO_BREAKING_FOLLOWUP=on のときだけ（既定 off・オーナー判断待ち）。
  - 短い版／全部の版の A/B は、差し替えた投稿で続ける（同じ日の中で交互）。
  - 作る順番: 予約の時刻が近い（まだ間に合う）投稿から。そのあと、出たあとの記事（記事ページ用）。

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
  SNS_VIDEO_POST=live      Buffer の予約中の投稿の写真を動画に差し替える。それ以外は差し替えない（記録だけ）
  SNS_VIDEO_BREAKING_FOLLOWUP=on  速報の記事だけ、あとから動画の投稿を別に出す（既定 off。オーナーの判断待ち）
  SNS_VIDEO_DAILY_MAX=12   1日（日本時間）に作る本数の上限（SNS の投稿の数より多めに）
  SNS_VIDEO_PER_RUN=3      1回の実行で作る本数（1本 約5分）
  SNS_VIDEO_WINDOW_H=12    Threads に投稿してから何時間以内の記事を対象にするか
  SNS_VIDEO_AB_UNTIL=YYYY-MM-DD   この日（日本時間）まで短い版と全部の版を交互に出す。過ぎたら SNS_VIDEO_AFTER_AB（既定 short）
  SNS_VIDEO_STORE=kv       置き場所（kv＝既定 / r2。r2 は SNS_VIDEO_R2_BUCKET・SNS_VIDEO_PUBLIC_BASE と R2 の権限が要る）

約束（scripts/sns_video.py の約束を引き継ぐ）
  1. 文面の投稿は今までどおり threads_post.py が予約する。このスクリプトが触るのは、まだ出ていない予約の写真と本文のリンクだけ
     （予約の時刻・問いかけ・本文の中身は変えない。返信つきの形 hook_thread は触らない）。
  2. 台本は Claude API（purpose=video。予算の範囲）→ Claude CLI → 無料モデル。どの経路でも事実の検査と編集長のレビューは同じに通す。
  3. 品質ゲートに落ちた・時間切れ・置けなかった → その版は出さない・再挑戦しない（記録は state/sns_video.json）。
  4. 差し替えは予約の時刻の EDIT_LEAD_MIN 分前まで。差し替えたら Buffer から読み直し、予約の時刻と状態が変わっていないこと・
     動画が付いたことを確かめる（時刻が動いていたら元の時刻に戻す）。断られたら写真のまま（再挑戦しない）。
  5. 動画の投稿を別に出すのは速報の後追い（フラグ）だけ。そのときも再送しない。深夜（1〜6時台）は出さない。動画の投稿どうしは40分以上あける。
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
        "breaking_followup": env.get("SNS_VIDEO_BREAKING_FOLLOWUP", "off") == "on",
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
AFTER_TEXT_MIN = 20  # （速報の後追いだけ）文面の投稿が出てから動画を出すまでの最短
MAKE_LEAD_MIN = 20  # 予約の時刻まで、これだけあれば動画を作って差し替えに間に合う見込み（1本 約5〜10分）
EDIT_LEAD_MIN = 10  # 予約の時刻のこれより直前には差し替えない（Buffer が出し始めている途中に触らない）
LIVE_STATUSES = ("posted", "attached")  # Threads に動画が出た（出る）記録


def mergeable(rec: dict) -> bool:
    """予約中の写真を動画に差し替えられる文面の投稿か（Buffer の通常の予約・本文1つの形。速報・まとめ・問いかけは除く）"""
    return bool(rec.get("id")) and not rec.get("breaking") and rec.get("format", "classic") == "classic" and rec.get("via", "buffer") == "buffer"


def can_post_now(state: dict, now: datetime) -> tuple[bool, str]:
    """静かな時間・前の動画の投稿からの間隔を見て、今この回で動画を1本出してよいか"""
    if now.astimezone(JST).hour in QUIET_HOURS_JST:
        return False, "深夜（1〜6時台）は出さない"
    last = max((_parse(r.get("postedAt", "")) for r in (state.get("videos") or {}).values() if r.get("status") == "posted" and r.get("postedAt")), default=None)  # 別に出した動画の投稿だけ
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
    shown = sum(1 for rec in (state.get("videos") or {}).values() if rec.get("variant") and rec.get("status") in LIVE_STATUSES and _today({"at": rec.get("postedAt", "")}, now))
    return "short" if shown % 2 == 0 else "full"


MAX_TRIES = 3  # 動画の作り直しは3回まで（2026-11-03: 台本の段は自分で直すようにした。shorts_v3.self_correct）


def will_retry(r: dict) -> bool:
    """作れなかった動画を、あとの回でもう一度作るか（撮れない・台本が作れない・失敗・置けなかった。MAX_TRIES 回まで）"""
    if int(r.get("tries", 1) or 1) >= MAX_TRIES:
        return False
    if r.get("status") == "upload_failed":
        return True
    why = f"{r.get('reason', '')} {' '.join(v.get('reasons', [''])[0] for v in (r.get('versions') or {}).values() if v.get('reasons'))}"
    return r.get("status") == "gate_failed" and bool(re.search(r"撮れる|台本|公式ページか台本が無い|文字が入りません|動き|変化|フレーム差", why))


def qa_ok(version: dict | None, env: dict | None = None) -> bool:
    """（2026-11-04 オーナー指摘: 青い線のずれ・行の頭の切れ・左の文字と読み上げの食い違い）SNS に出す動画は、描いた位置の検査（geometry）と
    画像のレビュー（vision）の両方に合格したものだけ。前のコードで作った動画（qa が無い）は出さない。SNS_VIDEO_REQUIRE_QA=off で止める（試験用）"""
    env = os.environ if env is None else env
    if env.get("SNS_VIDEO_REQUIRE_QA", "on") == "off":
        return True
    qa = (version or {}).get("qa") or {}
    return bool(qa.get("geometry")) and bool(qa.get("vision"))


def awaiting(rec: dict) -> bool:
    """動画を待っている記事の投稿か（threads_post.queue_for_video。Buffer にはまだ送っていない）"""
    return rec.get("status") == "awaiting_video" and bool((rec.get("pending") or {}).get("inputs"))


def candidates(posted: dict, state: dict, now: datetime, window_h: float, load_article=None, eligible=None, score=None, retry_dry: bool = False) -> list[str]:
    """動画を作る記事の順番。①予約中で、まだ差し替えに間に合う投稿（予約の時刻が近い順）
    ②Threads に出たあと・窓の中の記事（記事ページ用。点数の高い順）。まだ動画を作っていない・v3 の対象だけ"""
    # 重いモジュール（Pillow・numpy）は、既定の関数を使うときだけ読み込む（テストは軽い環境でも動く）
    if load_article is None:
        import shorts

        load_article = shorts.load_article
    if eligible is None:
        import shorts_v3 as v3

        # 段（video_ladder）の L3/L4 は公式ページが撮れない記事でも作れる（2026-11-04 オーナー「動画がダメで投稿が止まるのはだめ」）
        eligible = lambda a: bool(a.get("slug"))  # noqa: E731
    if score is None:
        import threads_post

        score = threads_post.score
    # 作ったが置かなかった（dry_run）記事は、置くのを有効にしたあとはもう一度作る（窓の中なら）
    def retry(r: dict) -> bool:
        return (retry_dry and r.get("status") == "dry_run") or will_retry(r)

    done = {k for k, r in (state.get("videos") or {}).items() if not retry(r)}
    rows = []
    for slug, rec in (posted.get("posted") or {}).items():
        at = published_at(rec)
        if slug in done or not at:
            continue
        queued = state.get("queued", {}).get(slug) or {}
        if awaiting(rec) and (rec.get("pending") or {}).get("hold"):
            continue  # オーナーに見せた動画を置くのを待っている（作らない）
        if awaiting(rec) and queued.get("status") not in ("created", "skipped"):
            group = 0  # 動画を待っている投稿（動画が無いと出せない）は、枠が過ぎていても最優先・枠の順
        elif at > now:
            if not mergeable(rec) or at - now < timedelta(minutes=MAKE_LEAD_MIN):
                continue
            group = 0
        elif now - at > timedelta(hours=window_h):
            continue
        else:
            group = 1
        try:
            article = load_article(slug)
        except (Exception, SystemExit):  # noqa: BLE001 - 記事が無い（消えた・まとめ記事）なら対象外
            continue
        if not eligible(article):
            continue
        rows.append((group, at.timestamp() if group == 0 else -score(article), at, slug))
    rows.sort(key=lambda r: r[:3])
    return [r[3] for r in rows]


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
    head = f"{hook}\n" + (f"{seconds}秒で解説しました。" if report.get("level") in ("L3", "L4") else f"公式ページの画面を見ながら、{seconds}秒で解説しました。")
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
        "title": report.get("title", ""), "url": report.get("url", ""), "total": report.get("total"), "voice": report.get("voice") or {}, "level": report.get("level"),
        "script_v3": {"hook": {"text": (sv3.get("hook") or {}).get("text", "")}, "summary": sv3.get("summary", "")},
    }  # fmt: skip


def run(now: datetime | None = None, env: dict | None = None, plan_only: bool = False, make_versions=None, select=None, poster=None, editor=None, creator=None) -> list[dict]:
    """1回分: ①動画を作る（予約の時刻が近い記事から。2つの版）→ 置く → 記事に載せる
    ②用意できた動画で、予約中の投稿の写真を差し替える（1記事1投稿。間に合わなければ記事ページだけ）
    ③（フラグ）速報の後追いの動画の投稿を1本だけ"""
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
        # 2026-11-04 オーナー「動画がダメなら投稿しない、で投稿が止まってはいけない」: L1（今の型）→ L2（作り直し）→
        # L3（公式の画像＋字幕）→ L4（記事の文だけ）の順に試し、検査に通った最初の段を使う（scripts/video_ladder.py）
        import video_ladder

        make_versions = video_ladder.make_versions
    results = []
    for slug in picks:
        prev_tries = int((state["videos"].get(slug) or {}).get("tries", 0) or 0)
        prec = (posted.get("posted") or {}).get(slug) or {}
        rec = {
            "at": now.isoformat(timespec="seconds"), "status": "started", "tries": prev_tries + 1,
            "textAt": (published_at(prec) or now).isoformat(timespec="seconds"), "textId": prec.get("id", ""),
            "breaking": bool(prec.get("breaking")), "scheduled": bool((published_at(prec) or now) > now),
        }  # fmt: skip
        state["videos"][slug] = rec
        save_state(state)  # 途中で落ちても同じ記事を二度作らない
        reports = make_versions(slug, OUT)
        ok = {k: bool((r.get("gate") or {}).get("ok")) and (OUT / f"{slug}__v3{'short' if k == 'short' else ''}.mp4").exists() for k, r in reports.items()}
        rec["versions"] = {k: {"ok": ok[k], "seconds": r.get("total"), "reasons": (r.get("gate") or {}).get("reasons", [])[:3],
                               "qa": (r.get("gate") or {}).get("qa") or {}, "template": r.get("template", ""), "level": r.get("level"),
                               **({"post": post_report(r)} if ok[k] else {})} for k, r in reports.items()}
        rec["timings"] = {k: r.get("timings") for k, r in reports.items()}
        levels = sorted({r.get("level") for r in reports.values() if r.get("level")})
        if levels:
            rec["level"] = levels[-1]  # 低い方の段（L4 が一番低い）
            rec["ladder"] = {k: r.get("ladder") for k, r in reports.items()}
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
    results += attach_ready(state, posted, now, f, editor or edit_scheduled)
    save_state(state)
    try:
        import video_ladder

        video_ladder.fallback_alert(state)  # L3/L4 が直近20本の3割を超えたら issue（sns-video-fallback）
    except Exception as error:  # noqa: BLE001
        log(f"[video-followup] 段の見張りをとばしました: {type(error).__name__}")
    apply_converted(state, posted)
    results += publish_queued(state, posted, now, f, creator=creator)
    results += publish_extra_queued(state, now, f, creator=creator)
    save_state(state)
    if f["post"] and f["breaking_followup"]:
        out = post_one(state, now, f, poster or post_video, only_breaking=True)
        if out:
            results.append(out)
        save_state(state)
    return results


def attach_ready(state: dict, posted: dict, now: datetime, f: dict, editor) -> list[dict]:
    """用意できた動画（ready）で、まだ出ていない予約の投稿の写真を動画に差し替える（予約の時刻が近い順）。
    間に合わない・速報・形式が違う → 記事ページだけ（article_only）。2本目の投稿は作らない"""
    out = []
    rows = []
    for slug, rec in (state.get("videos") or {}).items():
        if rec.get("status") != "ready":
            continue
        prec = (posted.get("posted") or {}).get(slug) or {}
        if awaiting(prec):
            continue  # 動画を待っている投稿は publish_queued が動画つきで送る
        rows.append((published_at(prec) or now, slug, rec, prec))
    for due, slug, rec, prec in sorted(rows, key=lambda r: r[0]):
        if prec.get("breaking"):
            if f.get("breaking_followup"):
                continue  # 速報の後追い（post_one）に回す
            rec.update({"status": "article_only", "why": "速報（文面の投稿はすぐ出した）"})
        elif not mergeable(prec):
            rec.update({"status": "article_only", "why": "差し替えられない形の投稿（返信つき・Buffer 以外など）"})
        elif due - now < timedelta(minutes=EDIT_LEAD_MIN):
            rec.update({"status": "article_only", "why": "動画ができる前に投稿の時刻が来た（以前の写真の予約。2026-11-03 からは動画待ちにする）"})
        elif not f.get("post"):
            continue  # 差し替えは止めている（SNS_VIDEO_POST が live ではない）。次の回でまた見る
        else:
            variant = pick_variant(state, now, f["ab_until"], f["after_ab"])
            versions = rec.get("versions") or {}
            usable = lambda k: (versions.get(k) or {}).get("ok") and k in (rec.get("urls") or {}) and qa_ok(versions.get(k))  # noqa: E731
            if not usable(variant):
                variant = next((k for k in ("short", "full") if usable(k)), "")
            if not variant:
                rec.update({"status": "article_only", "why": "投稿に使える版が無い（動画の検査に合格した版が無い）"})
            else:
                res = editor(prec["id"], versions[variant]["post"], variant, rec["urls"][variant], now)
                if res.get("deferred"):
                    # Buffer の上限・429。何も変えずに ready のまま残し、この回はもう Buffer を呼ばない（次の回で間に合えば差し替え）
                    rec["deferred"] = res["deferred"]
                    log(f"[video-followup] 差し替えを次の回に回します {slug}: {res['deferred'][:160]}")
                    out.append({"slug": slug, **rec})
                    break
                rec.pop("deferred", None)
                rec.update({"variant": variant, "bufferId": prec["id"], "postedAt": due.isoformat(timespec="seconds"), **res})
                log(f"[video-followup] 差し替え {slug}（{variant}・予約 {due.astimezone(JST):%H:%M}）: {rec.get('status')} {rec.get('why', '')} {rec.get('error', '')}")
        out.append({"slug": slug, **rec})
    return out


QUEUE_GIVE_UP_H = 6  # 枠の時刻からこれだけ過ぎても動画ができなければ、出さない（issue を立てる）
QUEUE_LEAD_MIN = 3  # 枠が過ぎた（または今すぐの）投稿は、送ってからこれだけ先に予約する（shareNow は使わない＝時刻を記録できる）
MISSING_LABEL = "sns-video-missing"


def video_input(service: str, post_input: dict, report: dict, variant: str, video_url: str) -> dict:
    """動画を待っていた Buffer の入力の写真・リンクカードを動画に替える（本文の中身は変えない。リンクは動画の A/B 用に）"""
    import threads_post

    inp = json.loads(json.dumps({k: v for k, v in post_input.items() if not k.startswith("_")}))
    meta = inp.get("metadata") or {}
    video = [{"video": {"url": video_url}}]
    if service == "threads":
        text, _notes = merged_text(inp.get("text") or "", report, variant)
        inp["text"] = text
        thread = (meta.get("threads") or {}).get("thread") or [{}]
        thread[0] = {**thread[0], "text": text, "assets": video}
        meta.setdefault("threads", {})["thread"] = thread
    elif service == "twitter":
        thread = (meta.get("twitter") or {}).get("thread") or [{"text": inp.get("text", "")}]
        thread[0] = {**thread[0], "assets": video}
        for t in thread[1:]:  # X の返信の記事のリンクを動画の A/B 用に
            m = URL_RE.search(t.get("text") or "")
            if m:
                t["text"] = t["text"][: m.start()] + threads_post.utm_link(report.get("url", ""), "x", CAMPAIGN, variant) + t["text"][m.end():]
        meta.setdefault("twitter", {})["thread"] = thread
    elif service == "facebook":
        m = URL_RE.search(inp.get("text") or "")
        if m:
            inp["text"] = inp["text"][: m.start()] + threads_post.utm_link(report.get("url", ""), "facebook", CAMPAIGN, variant) + inp["text"][m.end():]
        meta["facebook"] = {"type": "post"}  # リンクカード（写真）ではなく動画を付ける。記事のリンクは本文にある
        inp["assets"] = video
    inp["metadata"] = meta
    return inp


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def create_queued(service: str, inp: dict, article: dict | None, api_key: str | None = None) -> str:
    """動画つきの入力で Buffer に投稿を作る（threads_post.create_post＝門番 sns_rules を通る。動画が無ければ止まる）"""
    import threads_post

    key = api_key if api_key is not None else os.environ.get("BUFFER_API_KEY", "")
    return threads_post.create_post(key, inp, service=service, article=article, source="sns_video_followup.publish_queued")


def missing_issue(slug: str, why: str) -> None:
    """動画が作れず投稿を出さなかったことを issue に残す（ラベル sns-video-missing）。Actions の中だけ"""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    body = (f"記事 `{slug}` の SNS の投稿は、動画を作れなかったため出していません（写真のカードでは出さない決まり。docs/owner_rules.md S13）。\n\n"
            f"理由: {why}\n\n動画の仕組みを直したら、この記事は手で出し直すか、そのまま見送ってください。")
    try:
        subprocess.run(["gh", "label", "create", MISSING_LABEL, "--color", "D93F0B", "--description", "動画が作れず SNS に出さなかった", "--force"],
                       check=False, capture_output=True, text=True, timeout=60)
        subprocess.run(["gh", "issue", "create", "--title", f"動画が作れず SNS に出さなかった: {slug}", "--label", MISSING_LABEL, "--body", body],
                       check=False, capture_output=True, text=True, timeout=60)
    except Exception as error:  # noqa: BLE001
        log(f"[video-followup] issue を立てられませんでした: {type(error).__name__}")


def _eligible_default(slug: str) -> bool:
    """動画を作れる記事か。段（video_ladder）の L4 は記事の文だけで作れるので、記事があれば作れる"""
    return (ARTICLES / f"{slug}.json").exists()


def remote_queued(slug: str) -> dict:
    """main の最新の state/sns_video.json にある、この記事の送った記録（ほかの実行が先に送っていないか。2026-11-04 Kimi K3 の二重投稿のあと）。
    読めなければ {}（その場合も Buffer の確かめ threads_post.ensure_not_duplicate が止める）"""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return {}
    try:
        subprocess.run(["git", "fetch", "-q", "--depth", "1", "origin", "main"], cwd=ROOT, check=True, capture_output=True, timeout=60)
        out = subprocess.run(["git", "show", "origin/main:state/sns_video.json"], cwd=ROOT, check=True, capture_output=True, text=True, timeout=30).stdout
        data = json.loads(out)
    except Exception:  # noqa: BLE001
        return {}
    q = (data.get("queued") or {}).get(slug) or {}
    extra = (data.get("extraQueued") or {}).get(slug) or {}
    return q if (q.get("status") == "created" or any((q.get("channels") or {}).values())) else (extra if extra.get("status") == "created" else {})


def publish_queued(state: dict, posted: dict, now: datetime, f: dict, creator=None, issue=None, eligible=None, remote=None) -> list[dict]:
    """（2026-11-03 オーナー指示「SNS の投稿は動画のはず」）動画を待っている記事の投稿（threads_post.queue_for_video）を、
    動画ができたものから**枠の順番に**動画つきで Buffer に送る。
      - 枠の時刻がまだ先なら、その時刻に予約。過ぎていれば（動画が遅れた）今から QUEUE_LEAD_MIN 分後に予約（遅らせる）。
      - 前の枠の投稿が動画を待っている間は、あとの枠の投稿を先に出さない（順番を保つ）。
      - 動画がもう作れない（作り直しの回数切れ・対象外）か、枠から QUEUE_GIVE_UP_H 時間たったら、出さずに issue を立てる。
      - 写真・カードだけでは絶対に出さない（送る直前に門番 sns_rules のルール8が動画を確かめる）"""
    out: list[dict] = []
    if not f.get("post"):
        return out
    creator = creator or create_queued
    issue = issue or missing_issue
    eligible = eligible or _eligible_default
    remote = remote or remote_queued
    queue = state.setdefault("queued", {})
    videos = state.get("videos") or {}
    rows = []
    for slug, rec in (posted.get("posted") or {}).items():
        if not awaiting(rec) or (queue.get(slug) or {}).get("status") in ("created", "skipped"):
            continue
        slot = _parse((rec.get("pending") or {}).get("slot") or rec.get("dueAt") or "") or now
        rows.append((slot, slug, rec))
    last_due: datetime | None = None
    for slot, slug, rec in sorted(rows, key=lambda r: r[0]):
        q = queue.setdefault(slug, {"status": "waiting"})
        v = videos.get(slug) or {}
        urls = v.get("urls") or {}
        if (rec.get("pending") or {}).get("hold") and not v.get("adopted"):
            q.update({"status": "waiting", "why": "オーナーに見せた動画を置くのを待っている"})
            out.append({"slug": slug, **q})
            continue  # ほかの投稿の順番は止めない
        if not urls or v.get("status") in ("started", "upload_failed"):  # 置いた動画があれば使う（前に記事ページだけにした動画も）
            failed = bool(v) and v.get("status") in ("gate_failed", "upload_failed", "dry_run") and not will_retry(v)
            late = now - slot > timedelta(hours=QUEUE_GIVE_UP_H)
            unfit = not v and not eligible(slug)
            if failed or late or unfit:
                why = (f"動画を作れなかった（{v.get('status')}: {(v.get('reason') or v.get('error') or '')[:160]}）" if failed
                       else "動画を作れない記事（撮ってよい公式ページが無い）" if unfit
                       else f"枠（{slot.astimezone(JST):%m/%d %H:%M}）から{QUEUE_GIVE_UP_H}時間たっても動画ができない")
                q.update({"status": "skipped", "why": why, "at": now.isoformat(timespec="seconds")})
                log(f"[video-followup] 動画が無いので出しません {slug}: {why}")
                issue(slug, why)
                out.append({"slug": slug, **q})
                continue
            q.update({"status": "waiting", "why": "動画を作っている途中・順番待ち", "checkedAt": now.isoformat(timespec="seconds")})
            log(f"[video-followup] {slug} は動画待ち。あとの枠の投稿も順番を保って待たせます")
            out.append({"slug": slug, **q})
            break
        variant = (rec.get("pending") or {}).get("forceVariant") or pick_variant(state, now, f["ab_until"], f["after_ab"])
        versions = v.get("versions") or {}
        usable = lambda k: (versions.get(k) or {}).get("ok") and k in urls and qa_ok(versions.get(k))  # noqa: E731
        if not usable(variant):
            variant = next((k for k in ("short", "full") if usable(k)), "")
        if not variant and any((versions.get(k) or {}).get("ok") for k in ("short", "full")):
            # 動画はあるが、描いた位置・画像のレビューの検査に合格していない（前のコードの動画など）→ 作り直す
            state.setdefault("videosArchive", {})[slug] = state["videos"].pop(slug)
            q.update({"status": "waiting", "why": "動画の検査（描いた位置・画像のレビュー）に合格していないので作り直す"})
            out.append({"slug": slug, **q})
            break
        if not variant:
            q.update({"status": "skipped", "why": "投稿に使える版が無い"})
            issue(slug, q["why"])
            out.append({"slug": slug, **q})
            continue
        theirs = remote(slug)
        if theirs:
            # ほかの実行（同時に動いた回）がもう送った。作らずに、その記録を写す（1記事1投稿）
            q.update({**{k: theirs.get(k) for k in ("status", "id", "dueAt", "variant", "channels") if theirs.get(k)}, "status": "created", "why": "ほかの実行が先に送った"})
            log(f"[video-followup] {slug} はほかの実行がもう送っています。二重に作りません: {q.get('channels')}")
            out.append({"slug": slug, **q})
            continue
        report = versions[variant]["post"]
        article = article_of(report)
        base = max(slot, now + timedelta(minutes=QUEUE_LEAD_MIN))
        if last_due and base <= last_due:
            base = last_due + timedelta(minutes=2)  # 前の投稿より後に（順番を保つ）
        inputs = (rec.get("pending") or {}).get("inputs") or {}
        created = dict(q.get("channels") or {})
        stop = False
        for service in ("threads", "twitter", "facebook"):
            if service not in inputs or created.get(service):
                continue
            offset = 0 if service == "threads" else int(inputs[service].get("_offsetMin") or 0)
            inp = video_input(service, inputs[service], report, variant, urls[variant])
            inp.update({"mode": "customScheduled", "dueAt": _iso(base + timedelta(minutes=offset))})
            inp.pop("saveToDraft", None)
            try:
                created[service] = creator(service, inp, article)
            except Exception as error:  # noqa: BLE001
                if _buffer_unavailable(error):
                    q["deferred"] = str(error)[:200]
                    log(f"[video-followup] Buffer の上限のため次の回に {slug}/{service}: {str(error)[:120]}")
                    stop = True
                    break
                created[service] = ""
                q.setdefault("errors", {})[service] = str(error)[:200]
                log(f"[video-followup] {slug}/{service} を送れませんでした（写真では出しません）: {str(error)[:160]}")
                if service == "threads":
                    break  # Threads が通らなければ X・Facebook も出さない（同じ記事の投稿をそろえる）
        q["channels"] = created
        if stop:
            out.append({"slug": slug, **q})
            break
        if created.get("threads"):
            q.update({"status": "created", "id": created["threads"], "dueAt": base.isoformat(timespec="seconds"), "variant": variant,
                      "delayedMin": max(0, int((base - slot).total_seconds() // 60))})
            v.update({"status": "attached", "variant": variant, "bufferId": created["threads"], "postedAt": base.isoformat(timespec="seconds")})
            last_due = base
        else:
            q.update({"status": "skipped", "why": "Buffer に動画つきの投稿を作れなかった: " + "・".join((q.get("errors") or {}).values())[:200]})
            issue(slug, q["why"])
        log(f"[video-followup] 動画つきで予約 {slug}（{variant}・{base.astimezone(JST):%H:%M}）: {q.get('status')} {created}")
        out.append({"slug": slug, **q})
    return out


def scheduled_nodes(api_key: str, hours: float = 48) -> list[dict]:
    """Buffer に予約中（scheduled）の Threads・X・Facebook の投稿（これから hours 時間）。組織ごとに1回だけ読む"""
    import sns_audit
    import threads_post as tp

    now = datetime.now(timezone.utc)
    chans = tp.buffer_channels(api_key)
    by_id = {c["id"]: k for k, c in chans.items() if k in ("threads", "twitter", "facebook")}
    out = []
    for org_id in tp.buffer_org_ids(api_key):
        flt = (f"status: [scheduled], channelIds: {json.dumps(list(by_id))}, "
               f"startDate: {json.dumps(_iso(now - timedelta(minutes=5)))}, endDate: {json.dumps(_iso(now + timedelta(hours=hours)))}")
        query = f"query {{ posts(first: 100, input: {{ organizationId: {json.dumps(org_id)}, filter: {{ {flt} }} }}) {{ edges {{ node {{ {sns_audit.POST_FIELDS} }} }} }} }}"
        for edge in tp.buffer_graphql(api_key, query)["posts"]["edges"]:
            node = edge["node"]
            node["service"] = by_id.get(node.get("channelId"), "")
            out.append(node)
    return out


def node_problems(node: dict) -> tuple[str, bool, list[str]]:
    """予約中の投稿1件の (slug, 動画が付いているか, 文面の違反)。文面は今の門番で確かめる（動画の決まりは別に見る）"""
    import sns_rules

    thread = ((node.get("metadata") or {}).get("thread")) or []
    texts = [node.get("text") or ""] + [t.get("text") or "" for t in thread]
    m = re.search(r"/articles/([A-Za-z0-9_-]+)", " ".join(texts))
    slug = m.group(1) if m else ""
    assets = list(node.get("assets") or []) + [a for t in thread[:1] for a in (t.get("assets") or [])]
    video = any(str(a.get("mimeType") or a.get("type") or "").startswith("video") for a in assets)
    article = None
    if slug:
        try:
            article = json.loads((ARTICLES / f"{slug}.json").read_text("utf-8"))
        except (OSError, ValueError):
            article = None
    service = node.get("service", "")
    text = (thread[0].get("text") if thread else "") or node.get("text") or ""
    reply = "\n".join(t.get("text") or "" for t in thread[1:]) if service == "twitter" else ""
    links = [] if service != "facebook" else [node.get("externalLink") or ""]
    problems = sns_rules.check_post({"twitter": "x"}.get(service, service), text, [], article, reply_text=reply, links=[x for x in links if x],
                                    pending_video=True) if service else ["SNS が分からない"]
    return slug, video, problems


def queue_fix(apply: bool = False, now: datetime | None = None, api_key: str | None = None) -> list[dict]:
    """（2026-11-03 オーナー指示）Buffer に予約中の、動画の無い投稿・古い形の投稿（無理な説明・呼びかけなし・使わない書き出し）を直す。
    直し方: 古い予約は**下書きに戻す**（公開しない。消さない）→ 同じ記事を新しい形の文面で「動画待ち」にし、動画ができしだい
    同じ枠の時刻（過ぎていれば順番を保って直後）に動画つきで出す（publish_queued）。apply=False なら一覧だけ（Buffer は読むだけ）"""
    import threads_post as tp

    now = now or datetime.now(timezone.utc)
    key = api_key if api_key is not None else os.environ.get("BUFFER_API_KEY", "")
    state = load_json(STATE, {"videos": {}})
    posted = load_json(POSTED, {"posted": {}})
    rows, by_slug = [], {}
    for node in scheduled_nodes(key):
        slug, video, problems = node_problems(node)
        bad = (not video) or bool(problems)
        rows.append({"id": node["id"], "service": node.get("service"), "dueAt": node.get("dueAt"), "slug": slug, "video": video,
                     "problems": problems[:4], "fix": bad})
        if bad and slug and not slug.startswith(("digest-", "poll-")):
            by_slug.setdefault(slug, []).append(node)
    if not apply:
        return rows
    chans = tp.buffer_channels(key)
    for slug, nodes in by_slug.items():
        paused = {}
        for node in nodes:
            try:
                after = pause_post(key, node)
                ok = (after.get("status") or "") == "draft"
            except Exception as error:  # noqa: BLE001
                if _buffer_unavailable(error):
                    raise
                ok, after = False, {"error": str(error)[:200]}
            paused[node["service"]] = {"id": node["id"], "ok": ok, "dueAt": node.get("dueAt"), "status": after.get("status") or after.get("error")}
            log(f"[queue-fix] {slug}/{node['service']} を下書きに戻す: {paused[node['service']]}")
        for row in rows:
            if row["slug"] == slug:
                row["paused"] = paused.get(row["service"])
        if not all(v["ok"] for v in paused.values()):
            missing_issue(slug, f"予約中の写真の投稿を下書きに戻せませんでした（Buffer で手で止めてください）: {paused}")
            continue
        slot = min(_parse(v["dueAt"]) or now for v in paused.values())
        state.setdefault("converted", {})[slug] = {"at": now.isoformat(timespec="seconds"), "paused": paused,
                                                   "pending": requeue_inputs(key, chans, slug, list(paused), slot),
                                                   "threadsSent": "threads" not in paused}
    apply_converted(state, posted)
    f = flags()
    rows += publish_queued(state, posted, now, f)
    rows += publish_extra_queued(state, now, f)
    save_state(state)
    POSTED.write_text(json.dumps(posted, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return rows


def publish_extra_queued(state: dict, now: datetime, f: dict, creator=None, issue=None) -> list[dict]:
    """Threads はもう出ていて、X・Facebook だけ写真の予約を下書きに戻した記事を、動画つきで出す（動画ができしだい・元の時刻より後）"""
    out: list[dict] = []
    if not f.get("post"):
        return out
    creator = creator or create_queued
    issue = issue or missing_issue
    for slug, item in (state.get("extraQueued") or {}).items():
        if item.get("status") in ("created", "skipped"):
            continue
        v = (state.get("videos") or {}).get(slug) or {}
        urls = v.get("urls") or {}
        slot = _parse((item.get("pending") or {}).get("slot") or "") or now
        if not urls:
            if (v and not will_retry(v) and v.get("status") not in ("started",)) or now - slot > timedelta(hours=QUEUE_GIVE_UP_H):
                item.update({"status": "skipped", "why": "動画が無い"})
                issue(slug, "X・Facebook の投稿を動画つきで出せませんでした（動画が無い）")
            out.append({"slug": slug, "extra": True, **{k: item.get(k) for k in ("status", "why")}})
            continue
        variant = v.get("variant") if v.get("variant") in urls else next((k for k in ("short", "full") if k in urls), "")
        report = ((v.get("versions") or {}).get(variant) or {}).get("post") or {}
        base = max(slot, now + timedelta(minutes=QUEUE_LEAD_MIN))
        created = dict(item.get("channels") or {})
        for service, inp0 in ((item.get("pending") or {}).get("inputs") or {}).items():
            if created.get(service):
                continue
            inp = video_input(service, inp0, report, variant, urls[variant])
            inp.update({"mode": "customScheduled", "dueAt": _iso(base + timedelta(minutes=int(inp0.get("_offsetMin") or 0)))})
            inp.pop("saveToDraft", None)
            try:
                created[service] = creator(service, inp, article_of(report))
            except Exception as error:  # noqa: BLE001
                if _buffer_unavailable(error):
                    break
                created[service] = ""
                item.setdefault("errors", {})[service] = str(error)[:200]
        item["channels"] = created
        if created and all(created.values()):
            item["status"] = "created"
        out.append({"slug": slug, "extra": True, "status": item.get("status"), "channels": created})
    return out


def requeue_inputs(api_key: str, chans: dict, slug: str, services: list[str], slot: datetime, classic: str = "") -> dict:
    """出し直す SNS の分だけ、新しい形の文面で Buffer の入力を作る（Buffer は呼ばない）。文面は門番で確かめる（動画は後で）。
    出し直す分は前の予約を下書きに戻したので「もう投稿済み」とは数えない（history=False）"""
    import sns_rules
    import threads_post as tp

    article = json.loads((ARTICLES / f"{slug}.json").read_text("utf-8"))
    classic = classic or tp.compose(article, [], use_llm=True)
    link = f"{SITE}/articles/{slug}"
    inputs = {}
    if "threads" in services:
        inputs["threads"] = tp.publish_via_buffer(api_key, chans["threads"]["id"], tp.with_link(classic, article, "threads", "classic"), link, article, slot,
                                                  build_only=True)["input"]
    extra = {k: chans[k] for k in ("twitter", "facebook") if k in services and k in chans}
    inputs.update(tp.publish_extra_channels(api_key, extra, classic, link, article, slot, fb_text=classic, build_only=True))
    for service, inp in inputs.items():
        sns_rules.enforce_buffer(service, {k: v for k, v in inp.items() if not k.startswith("_")}, article,
                                 source="sns_video_followup.requeue_inputs", pending_video=True, history=False)
    return {"inputs": inputs, "slot": slot.isoformat(timespec="seconds"), "fmt": "classic"}


def requeue(slug: str, services: list[str], slot: datetime | None = None, api_key: str | None = None) -> dict:
    """（手で）下書きに戻した投稿を、新しい形・動画つきで出し直す予約に入れる（Threads が出ている記事の X・Facebook など）"""
    import threads_post as tp

    key = api_key if api_key is not None else os.environ.get("BUFFER_API_KEY", "")
    now = datetime.now(timezone.utc)
    state = load_json(STATE, {"videos": {}})
    posted = load_json(POSTED, {"posted": {}})
    old = ((posted.get("posted") or {}).get(slug) or {}).get("channels") or {}
    pending = requeue_inputs(key, tp.buffer_channels(key), slug, services, slot or now)
    # 下書きに戻した投稿として記録し、投稿の記録からその SNS の ID を外す（pausedIds に残す）→ 門番が「もう投稿済み」と数えない
    state.setdefault("converted", {})[slug] = {"at": now.isoformat(timespec="seconds"), "pending": pending, "threadsSent": True,
                                               "paused": {k: {"id": old.get(k, ""), "ok": True} for k in services}}
    apply_converted(state, posted)
    POSTED.write_text(json.dumps(posted, ensure_ascii=False, indent=2) + "\n", "utf-8")
    out = publish_extra_queued(state, now, flags())
    save_state(state)
    return {"slug": slug, "services": services, "result": out}


REPOSTS = ROOT / "content" / "sns_reposts"
BLUESKY_EXTRA = ROOT / "state" / "bluesky_extra.json"


def next_normal_slot(posted: dict, now: datetime, lead_min: int = 90) -> datetime:
    """次のふつうの投稿枠（朝のまとめの枠を除く・予約済みの投稿と90分以上あく・動画を作る時間 lead_min 分より先）"""
    import threads_post as tp

    taken = [t for t in (_parse(r.get("dueAt") or "") for r in (posted.get("posted") or {}).values()) if t]
    base = now.astimezone(JST).replace(second=0, microsecond=0)
    for day in range(3):
        for hour, minute in tp.SLOTS_JST:
            if tp.DIGEST_ON and (hour, minute) == tp.DIGEST_SLOT_JST:
                continue
            slot = (base + timedelta(days=day)).replace(hour=hour, minute=minute)
            if slot >= base + timedelta(minutes=lead_min) and all(abs((slot - t).total_seconds()) >= 90 * 60 for t in taken):
                return slot.astimezone(timezone.utc)
    raise RuntimeError("3日以内に空いている枠がありません")


def repost(slug: str, api_key: str | None = None, now: datetime | None = None, slot: datetime | None = None) -> dict:
    """（オーナー承認の出し直し）写真で出て消した投稿を、content/sns_reposts/<slug>.json の文面・動画つきで、次のふつうの枠に出す。
    動画は作り直す（いまのコードで）。動画ができたら publish_queued が枠の時刻に Threads・X・Facebook へ、Bluesky はそのあと出す"""
    import threads_post as tp

    key = api_key if api_key is not None else os.environ.get("BUFFER_API_KEY", "")
    now = now or datetime.now(timezone.utc)
    spec_path = REPOSTS / f"{slug}.json"
    # 承認済みの文面があればそれを使う。無ければ（動画が作れず見送った投稿の出し直しなど）今の決まりで文面を作り直す
    spec = json.loads(spec_path.read_text("utf-8")) if spec_path.exists() else {}
    state = load_json(STATE, {"videos": {}})
    posted = load_json(POSTED, {"posted": {}})
    rec = (posted.get("posted") or {}).get(slug) or {}
    others = {"posted": {k: v for k, v in (posted.get("posted") or {}).items() if k != slug}}
    original = _parse(rec.get("dueAt") or "")
    if slot is None:
        taken = [t for t in (_parse(r.get("dueAt") or "") for r in others["posted"].values()) if t]
        keep = (original and original - now >= timedelta(minutes=90) and rec.get("status") == "skipped_no_video"
                and all(abs((original - t).total_seconds()) >= 90 * 60 for t in taken))  # 元の枠がまだ先で、ほかの投稿と重ならないときだけ
        slot = original if keep else next_normal_slot(others, now)
    services = spec.get("services") or ["threads", "twitter", "facebook"]
    pending = requeue_inputs(key, tp.buffer_channels(key), slug, services, slot, classic=spec.get("classic", ""))
    if spec.get("variant"):
        pending["forceVariant"] = spec["variant"]
    if spec.get("adopt"):
        pending["hold"] = True  # オーナーに見せた動画を置くまで、ここでは動画を作らない（adopt_video で置く）
    old_ids = {"threads": rec.get("id", ""), **{k: v for k, v in (rec.get("channels") or {}).items() if k in ("twitter", "facebook", "bluesky")}}
    if slug in (state.get("videos") or {}):
        state.setdefault("videosArchive", {})[slug] = state["videos"].pop(slug)  # 動画はいまのコードで作り直す
    (state.get("queued") or {}).pop(slug, None)
    state.setdefault("converted", {})[slug] = {"at": now.isoformat(timespec="seconds"), "pending": pending, "threadsSent": False, "repost": True,
                                               "paused": {k: {"id": v, "ok": True, "deleted": True} for k, v in old_ids.items() if v},
                                               "dropBlueskyExtra": bool(spec.get("bluesky"))}
    apply_converted(state, posted)
    POSTED.write_text(json.dumps(posted, ensure_ascii=False, indent=2) + "\n", "utf-8")
    drop_bluesky_extra(state)
    save_state(state)
    return {"slug": slug, "slot": slot.astimezone(JST).isoformat(timespec="minutes"), "services": services, "variant": spec.get("variant")}


def adopt_video(slug: str, src: Path, now: datetime | None = None, upload=None) -> dict:
    """検査に合格して保存した動画（見本のワークフローの成果物など。<slug>__v3.mp4 / __v3short.mp4 と .report.json）を、
    この記事の SNS 用の動画として置く（作り直さない）。検査（gate.ok・geometry・vision）に通っていない版は使わない"""
    now = now or datetime.now(timezone.utc)
    upload = upload or upload_kv
    state = load_json(STATE, {"videos": {}})
    versions, urls = {}, {}
    for variant, stem in (("full", f"{slug}__v3"), ("short", f"{slug}__v3short")):
        mp4 = next(iter(sorted(Path(src).rglob(f"{stem}.mp4"))), None)
        rep = next(iter(sorted(Path(src).rglob(f"{stem}.report.json"))), None)
        if not mp4 or not rep:
            continue
        report = json.loads(rep.read_text("utf-8"))
        gate = report.get("gate") or {}
        version = {"ok": bool(gate.get("ok")), "seconds": report.get("total"), "reasons": (gate.get("reasons") or [])[:3], "qa": gate.get("qa") or {},
                   "template": report.get("template", ""), "level": report.get("level") or "L1", "post": post_report(report)}
        if not (version["ok"] and qa_ok(version)):
            log(f"[video-followup] {stem} は検査に通っていないので使いません: {version['reasons']}")
            continue
        urls[variant] = upload(mp4, slug)
        versions[variant] = version
        if variant == "full":
            urls["poster"] = upload(poster_of(mp4), slug, "jpg")
    if not versions:
        raise RuntimeError(f"{slug}: 使える動画（検査に合格した版）が {src} にありません")
    if slug in (state.get("videos") or {}):
        state.setdefault("videosArchive", {})[f"{slug}@{now:%Y%m%d%H%M}"] = state["videos"].pop(slug)
    state.setdefault("videos", {})[slug] = {"at": now.isoformat(timespec="seconds"), "status": "ready", "tries": 1, "ok": True, "adopted": str(src),
                                            "versions": versions, "urls": urls, "level": max(v["level"] for v in versions.values())}
    save_state(state)
    return {"slug": slug, "versions": {k: {"seconds": v["seconds"], "qa": v["qa"], "template": v["template"]} for k, v in versions.items()}, "urls": urls}


def drop_bluesky_extra(state: dict) -> int:
    """出し直す記事を Bluesky の埋め合わせの記録から外す（外さないと Bluesky が「もう出した」として出さない）。何度やっても同じ"""
    keys = [s for s, c in (state.get("converted") or {}).items() if c.get("dropBlueskyExtra")]
    extra = load_json(BLUESKY_EXTRA, {})
    gone = [k for k in keys if k in extra]
    for k in gone:
        state.setdefault("blueskyArchive", {})[k] = extra.pop(k)
    if gone:
        BLUESKY_EXTRA.write_text(json.dumps(extra, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return len(gone)


def apply_converted(state: dict, posted: dict) -> int:
    """queue_fix で下書きに戻した投稿を、投稿の記録で「動画待ち」にする（何度当てても同じ）。
    Threads がもう出ている記事は、X・Facebook の分だけを state の queued に残す（publish_queued が送る）"""
    n = 0
    for slug, conv in (state.get("converted") or {}).items():
        rec = (posted.get("posted") or {}).setdefault(slug, {"via": "buffer"})
        if rec.get("convertedAt") == (conv.get("at") or "-") or (conv.get("threadsSent") and conv.get("applied")):
            continue  # この出し直しはもう写した（何度当てても同じ）
        paused = conv.get("paused") or {}
        rec.setdefault("pausedIds", {}).update({k: v.get("id") for k, v in paused.items()})
        channels = {k: v for k, v in (rec.get("channels") or {}).items() if k not in paused}
        if conv.get("repost"):
            for k in ("blueskyAt", "blueskyFailed", "sentAt", "permalink"):
                rec.pop(k, None)
        rec["channels"] = channels
        if conv.get("threadsSent"):
            # Threads は出ている（動画つき）。X・Facebook だけを動画待ちにする
            state.setdefault("extraQueued", {})[slug] = {"pending": conv["pending"], "status": "waiting"}
        else:
            rec.update({"id": "", "status": "awaiting_video", "pending": conv["pending"], "dueAt": conv["pending"]["slot"], "image": "video",
                        "convertedAt": conv.get("at") or "-"})
            rec.pop("why", None)
        conv["applied"] = True
        n += 1
    return n


def sync_posted(state: dict, posted: dict) -> int:
    """sns_video.json の queued（動画つきで送った・出さなかった）を threads_posted.json の記録に写す（ワークフローが main に残す前に呼ぶ）"""
    changed = apply_converted(state, posted)
    drop_bluesky_extra(state)
    for slug, q in (state.get("queued") or {}).items():
        rec = (posted.get("posted") or {}).get(slug)
        if not rec or not awaiting(rec):
            continue
        if q.get("status") == "created":
            rec.update({"id": q.get("id", ""), "status": "scheduled", "dueAt": q.get("dueAt") or rec.get("dueAt"), "image": "video",
                        "channels": {**(rec.get("channels") or {}), **{k: v for k, v in (q.get("channels") or {}).items() if k != "threads" and v}},
                        "videoVariant": q.get("variant", "")})
            rec.pop("pending", None)
            changed += 1
        elif q.get("status") == "skipped":
            rec.update({"status": "skipped_no_video", "why": q.get("why", "")})
            rec.pop("pending", None)
            changed += 1
    return changed


VIDEO_LINE = "公式ページの画面を見ながら、{seconds}秒の動画で解説しています。"
VIDEO_LINE_PLAIN = "{seconds}秒の動画で解説しています。"  # L3/L4（公式ページの画面を使わない段）
URL_RE = re.compile(r"https?://\S+/articles/[^\s?]+\S*")


def merged_text(text: str, report: dict, variant: str, limit: int = MAX_CHARS) -> tuple[str, dict]:
    """予約中の投稿の本文を、動画つきの本文にする。中身は変えず
    ①記事のリンクを動画の A/B 用（utm_campaign=sns_video&utm_content=版）に替える（長すぎるなら元のまま）
    ②入るなら「◯秒の動画で解説」の行（リンクの案内の前）と、音声のクレジット（最後）を足す"""
    notes = {"link": "kept", "line": False, "credit": False}
    m = URL_RE.search(text)
    new = text
    if m:
        cand = text[: m.start()] + article_link(report, variant) + text[m.end():]
        if len(cand) <= limit:
            new, notes["link"] = cand, "video"
    seconds = int(round(float(report.get("total") or 0)))
    form = VIDEO_LINE_PLAIN if report.get("level") in ("L3", "L4") else VIDEO_LINE
    line = form.format(seconds=seconds) if seconds else ""
    credit = (report.get("voice") or {}).get("credit", "")
    if line:
        idx = new.find("詳しくはこちら")
        cand = (new[:idx] + line + "\n" + new[idx:]) if idx > 0 else (new.rstrip() + "\n\n" + line)
        if len(cand) <= limit:
            new, notes["line"] = cand, True
    if credit and len(new) + len(credit) + 4 <= limit:
        new, notes["credit"] = new.rstrip() + f"\n音声：{credit}", True
    return new, notes


POST_FIELDS = "id status dueAt sentAt text externalLink assets { mimeType source } metadata { ... on ThreadsPostMetadata { topic threadCount thread { text assets { mimeType source } } } }"


def _buffer_unavailable(error: BaseException) -> bool:
    import buffer_budget

    return isinstance(error, buffer_budget.BufferUnavailable)


def read_post(api_key: str, post_id: str, raise_unavailable: bool = False) -> dict | None:
    import threads_post

    try:
        return threads_post.buffer_graphql(api_key, f"query($id: PostId!) {{ post(input: {{ id: $id }}) {{ {POST_FIELDS} }} }}", {"id": post_id}).get("post")
    except Exception as error:  # noqa: BLE001
        if raise_unavailable and _buffer_unavailable(error):
            raise
        log(f"[video-followup] 投稿を読めない {post_id}: {str(error)[:200]}")
        return None


def pause_input(node: dict) -> dict:
    """予約中の投稿を下書きに戻す editPost の入力（Buffer は本文と SNS ごとの形が無い編集を断る。画像は外す）"""
    service = node.get("service", "")
    text = node.get("text") or ""
    thread = ((node.get("metadata") or {}).get("thread")) or []
    if service == "facebook":
        meta = {"facebook": {"type": "post"}}
    else:
        key = "twitter" if service == "twitter" else "threads"
        meta = {key: {"thread": [{"text": t.get("text") or "", "assets": []} for t in thread] or [{"text": text, "assets": []}]}}
    return {"id": node["id"], "text": text, "assets": [], "metadata": meta, "saveToDraft": True}


def pause_post(api_key: str, node: dict) -> dict:
    """予約中の投稿を下書きに戻す（公開しない・消さない）。門番は「下書きに戻すだけ」かを確かめる"""
    import sns_rules
    import threads_post

    inp = pause_input(node)
    sns_rules.enforce_buffer(node.get("service", "threads"), inp, None, source="sns_video_followup.pause_post", pausing=True)
    data = threads_post.buffer_graphql(
        api_key,
        "mutation($input: EditPostInput!) { editPost(input: $input) { __typename "
        f"... on PostActionSuccess {{ post {{ {POST_FIELDS} }} }} ... on MutationError {{ message }} }} }}",
        {"input": inp},
        gated=True,
    )
    res = data.get("editPost") or {}
    if res.get("message") or res.get("__typename") not in ("PostActionSuccess", None):
        raise RuntimeError(f"Buffer: {res.get('__typename')}: {res.get('message', '')}"[:300])
    return res.get("post") or {}


def edit_post(api_key: str, inp: dict, article: dict | None = None) -> dict:
    """Buffer の editPost。本文・画像を変えるときは、変える直前に SNS ルールの門番（sns_rules）を通す（予約の時刻だけ戻すときは通さない）。
    差し替えたあとの投稿（状態・時刻・付いているもの）も同じ応答で受け取る（読み直しの1回を省く）"""
    import sns_rules
    import threads_post

    if "text" in inp or "metadata" in inp or "assets" in inp:
        sns_rules.enforce_buffer("threads", inp, article, source="sns_video_followup.edit_post", history=False)
    data = threads_post.buffer_graphql(
        api_key,
        "mutation($input: EditPostInput!) { editPost(input: $input) { __typename "
        f"... on PostActionSuccess {{ post {{ {POST_FIELDS} }} }} ... on MutationError {{ message }} }} }}",
        {"input": inp},
        gated=True,
    )
    res = data.get("editPost") or {}
    if res.get("message") or res.get("__typename") not in ("PostActionSuccess", None):
        raise RuntimeError(f"Buffer: {res.get('__typename')}: {res.get('message', '')}"[:300])
    return res.get("post") or {}


def _has_video(post: dict | None) -> bool:
    thread = ((post or {}).get("metadata") or {}).get("thread") or []
    assets = (thread[0].get("assets") if thread else None) or (post or {}).get("assets") or []
    return any(str(a.get("mimeType", "")).startswith("video") for a in assets)


def article_of(report: dict) -> dict | None:
    """動画の記録（report の url）から記事の JSON を読む。読めなければ None（門番が「記事のない投稿」として止める）"""
    m = re.search(r"/articles/([^/?#]+)", str(report.get("url") or ""))
    try:
        return json.loads((ARTICLES / f"{m.group(1)}.json").read_text("utf-8")) if m else None
    except (OSError, ValueError):
        return None


def edit_scheduled(post_id: str, report: dict, variant: str, video_url: str, now: datetime | None = None, api_key: str | None = None, allow_draft: bool = False) -> dict:
    """予約中（scheduled）の Threads の投稿の写真を動画に差し替える（editPost。mode を送らないので予約の時刻はそのまま）。
    差し替えたら読み直して、状態・時刻が変わっていないこと・動画が付いたことを確かめる。時刻が動いていたら元に戻す"""
    now = now or datetime.now(timezone.utc)
    key = api_key if api_key is not None else os.environ.get("BUFFER_API_KEY", "")
    try:
        cur = read_post(key, post_id, raise_unavailable=True)
    except Exception as error:  # noqa: BLE001 - 上限・429 のときは Buffer をそれ以上呼ばず、次の回にもう一度だけ見る
        return {"status": "ready", "deferred": str(error)[:300]}
    if not cur:
        # まだ何も送っていない（読めなかっただけ。Buffer の回数制限など）→ ready のまま次の回でもう一度（時刻に間に合う間だけ）
        return {"status": "ready", "attachError": "Buffer で予約の投稿を読めない（次の回でもう一度）"}
    ok_status = ("scheduled", "draft") if allow_draft else ("scheduled",)
    if cur.get("status") not in ok_status:
        return {"status": "article_only", "why": f"Buffer の状態が {cur.get('status')}（もう出た・出ている途中など）"}
    due = _parse(cur.get("dueAt") or "")
    if cur.get("status") == "scheduled" and (not due or due - now < timedelta(minutes=EDIT_LEAD_MIN)):
        return {"status": "article_only", "why": "予約の時刻の直前なので差し替えない（写真で出た）"}
    meta = cur.get("metadata") or {}
    if int(meta.get("threadCount") or len(meta.get("thread") or []) or 1) > 1:
        return {"status": "article_only", "why": "返信つきの形なので差し替えない"}
    text, notes = merged_text(cur.get("text") or "", report, variant)
    threads_meta: dict = {"thread": [{"text": text, "assets": [{"video": {"url": video_url}}]}]}
    if meta.get("topic"):
        threads_meta["topic"] = meta["topic"]
    inp = {"id": post_id, "text": text, "assets": [], "metadata": {"threads": threads_meta}}
    error = ""
    after: dict = {}
    try:
        after = edit_post(key, inp, article_of(report))
    except Exception as err:  # noqa: BLE001 - 断られたら写真のまま（再挑戦しない）
        error = str(err)[:300]
        if _buffer_unavailable(err):
            # 上限・429: 何も変わっていない。写真のままにして、次の回にもう一度だけ見る（ready のまま）
            return {"status": "ready", "deferred": error}
    if "status" not in after or "dueAt" not in after:
        after = read_post(key, post_id) or {}  # 応答に状態が無いときだけ読み直す
    check = {"status": after.get("status"), "dueAt": after.get("dueAt"), "video": _has_video(after), "before": {"status": cur.get("status"), "dueAt": cur.get("dueAt")}}
    # 予約の時刻・状態が動いていたら元に戻す（差し替えが通ったかどうかに関係なく）
    if cur.get("status") == "scheduled" and after and (after.get("status") != "scheduled" or _parse(after.get("dueAt") or "") != due):
        try:
            edit_post(key, {"id": post_id, "mode": "customScheduled", "dueAt": cur["dueAt"], "saveToDraft": False})
            check["restored"] = read_post(key, post_id) or {}
            check["restored"] = {k: check["restored"].get(k) for k in ("status", "dueAt")}
        except Exception as err:  # noqa: BLE001
            check["restoreError"] = str(err)[:200]
    if error or not check["video"]:
        return {"status": "attach_failed", "error": error or "差し替えたあとに動画が見当たらない", "check": check, "notes": notes}
    return {"status": "attached", "body": text, "notes": notes, "check": check, "link": article_link(report, variant) if notes["link"] == "video" else ""}


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
        good = rec.get("ok") if "ok" in rec else rec.get("status") in ("ready", "posted", "attached", "article_only", "attach_failed", "dry_run", "uploaded")
        if good:
            d["ok"] += 1
        else:
            key = (rec.get("reason") or rec.get("status") or "不明").split(" / ")[0][:60]
            d["reasons"][key] = d["reasons"].get(key, 0) + 1
    for d in days.values():
        d["rate"] = round(d["ok"] / max(d["tried"], 1), 2)
    return dict(sorted(days.items()))


def post_one(state: dict, now: datetime, f: dict, poster, only_breaking: bool = False) -> dict | None:
    """（速報の後追いだけ。SNS_VIDEO_BREAKING_FOLLOWUP=on）用意できた動画（ready）を1本だけ別の投稿で出す。
    文面の投稿が出てから20分以上・24時間以内・古い順。静かな時間と間隔を守る"""
    allowed, why = can_post_now(state, now)
    if not allowed:
        log(f"[video-followup] この回は動画を出さない: {why}")
        return None
    ready = []
    for slug, rec in (state.get("videos") or {}).items():
        text_at = _parse(rec.get("textAt", "")) or _parse(rec.get("at", ""))
        if rec.get("status") != "ready" or not text_at or (only_breaking and not rec.get("breaking")):
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
        # 動画だけの2本目はルール5（1記事1投稿）に反するので、門番が kind="video_followup" を止める（SNS_VIDEO_BREAKING_FOLLOWUP はオーナー判断待ち）
        article = shorts.load_article(slug)
        post_id = threads_post.create_post(api_key, buffer_input(channel["id"], body, video_url, threads_post.topic_of(article)),
                                           service="threads", article=article, source="sns_video_followup.post_video", kind="video_followup")
        return {"status": "posted", "bufferId": post_id, "postedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    except Exception as error:  # noqa: BLE001 - 受理されたか分からない失敗も含め、再送しない
        return {"status": "post_failed", "error": str(error)[:300], "postedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def ab_rows(state: dict, metrics: dict, visits: dict) -> dict:
    """短い版と全部の版の比べ: 投稿ごとの平均（記事への訪問・いいね・返信・再投稿・表示）"""
    out: dict = {}
    for slug, rec in (state.get("videos") or {}).items():
        if rec.get("status") not in LIVE_STATUSES or rec.get("variant") not in ("short", "full"):
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
    draft_id = threads_post.create_post(api_key, inp, service="threads", article=article_of(report), source="sns_video_followup.draft_test", kind="video_followup")
    return {"input": inp, "sent": True, "draftId": draft_id, "check": read_draft(api_key, channel["id"], draft_id)}


def read_draft(api_key: str, channel_id: str, post_id: str, statuses: tuple = ("[draft]", "")) -> dict:
    """作った投稿・下書きを Buffer から読み直す（本文・状態・付いている動画・公開先のリンク）。読めなくても例外は投げない"""
    import threads_post

    fields = "id text status channelId sentAt externalLink assets { thumbnail mimeType source }"
    try:
        orgs = [{"id": oid} for oid in threads_post.buffer_org_ids(api_key)]  # 保存した組織の ID（問い合わせを省く）
    except Exception as error:  # noqa: BLE001
        return {"error": f"organizations: {error}"[:300]}
    tries = []
    for org in orgs:
        for status in statuses:
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


def merge_draft_test(slug: str, yes: bool = False) -> dict:
    """差し替えのテスト: いつもの形（本文＋カード画像）の**下書き**を1本作り、editPost で動画に差し替えて読み直す。
    下書き（saveToDraft）なので公開されない。yes=False なら何も送らない"""
    import shorts
    import threads_post as tp

    rec = (load_json(STATE, {"videos": {}}).get("videos") or {}).get(slug) or {}
    versions, urls = rec.get("versions") or {}, rec.get("urls") or {}
    variant = next((k for k in ("short", "full") if (versions.get(k) or {}).get("ok") and k in urls), "")
    if not variant:
        raise RuntimeError(f"{slug} に置いた動画が無い")
    article = shorts.load_article(slug)
    text = tp.with_link(tp.compose(article), article, "threads", "classic")
    plan = {"slug": slug, "variant": variant, "video": urls[variant], "photoText": text, "videoText": merged_text(text, versions[variant]["post"], variant)[0]}
    if not yes:
        return {**plan, "sent": False}
    key = os.environ.get("BUFFER_API_KEY", "")
    channel = tp.buffer_threads_channel(key)
    if not channel:
        raise RuntimeError("Buffer に Threads のチャンネルが無い")
    made = tp.publish_via_buffer(key, channel["id"], text, f"{SITE}/articles/{slug}", article, draft=True, fmt="classic")
    before = read_post(key, made["id"]) or {}
    res = edit_scheduled(made["id"], versions[variant]["post"], variant, urls[variant], api_key=key, allow_draft=True)
    after = read_post(key, made["id"]) or {}
    return {**plan, "sent": True, "draftId": made["id"], "before": {"status": before.get("status"), "assets": before.get("metadata"), "text": before.get("text")}, "result": res, "after": after}


def today_report(now: datetime | None = None, api_key: str | None = None) -> list[dict]:
    """今日（日本時間）の Threads の投稿（出た・予約中）ごとに、動画が付いたか・付かなかった理由（読むだけ）"""
    import threads_post as tp

    now = now or datetime.now(timezone.utc)
    key = api_key if api_key is not None else os.environ.get("BUFFER_API_KEY", "")
    channel = tp.buffer_threads_channel(key) or {}
    start = now.astimezone(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    videos = load_json(STATE, {"videos": {}}).get("videos") or {}
    posted = load_json(POSTED, {"posted": {}}).get("posted") or {}
    by_id = {r.get("id"): s for s, r in posted.items() if r.get("id")}
    video_post_ids = {r.get("bufferId"): s for s, r in videos.items() if r.get("status") == "posted" and r.get("bufferId")}
    nodes = []
    for org in [{"id": oid} for oid in tp.buffer_org_ids(key)]:
        flt = f"channelIds: {json.dumps([channel.get('id', '')])}, startDate: {json.dumps(tp.utc_z(start))}, endDate: {json.dumps(tp.utc_z(end))}"
        query = f"query {{ posts(first: 100, input: {{ organizationId: {json.dumps(org['id'])}, filter: {{ {flt} }} }}) {{ edges {{ node {{ {POST_FIELDS} }} }} }} }}"
        nodes += [e["node"] for e in tp.buffer_graphql(key, query)["posts"]["edges"]]
    rows = []
    for n in nodes:
        when = _parse(n.get("sentAt") or n.get("dueAt") or "")
        if not when or not (start <= when.astimezone(JST) < end) or n.get("status") == "draft":
            continue
        m = re.search(r"/articles/([^\s?/]+)", n.get("text") or "")
        slug = video_post_ids.get(n["id"]) or by_id.get(n["id"]) or (m.group(1) if m else "")
        v = videos.get(slug) or {}
        if n["id"] in video_post_ids:
            kind, why = "動画だけの2本目（前のやり方）", ""
        elif _has_video(n):
            kind, why = "動画つき（写真を差し替え）", ""
        else:
            kind = "写真（動画なし）"
            prec = posted.get(slug) or {}
            if slug.startswith(("digest-", "poll-")) or prec.get("format") in ("digest", "poll"):
                why = "朝のまとめ・問いかけ（記事の投稿ではない）"
            elif not v:
                why = "速報（すぐ出した）で動画はまだ／動画を作る前に出た" if prec.get("breaking") else "まだ動画を作っていない（作る順番待ち・対象外）"
            elif v.get("status") == "gate_failed":
                why = "動画が品質ゲートに不合格: " + (v.get("reason") or "")[:140]
            else:
                why = f"{v.get('status')}: {v.get('why') or v.get('error') or ''}"[:160]
        rows.append({"time": when.astimezone(JST).strftime("%H:%M"), "status": n.get("status"), "slug": slug, "kind": kind, "why": why, "id": n["id"]})
    return sorted(rows, key=lambda r: r["time"])


def main() -> None:
    parser = argparse.ArgumentParser(description="SNS に投稿した記事の動画を作って後から出す（既定は止まっている）")
    parser.add_argument("--plan", action="store_true", help="対象と残りの本数を表示するだけ")
    parser.add_argument("--ab-report", action="store_true", help="短い版と全部の版の比べ")
    parser.add_argument("--draft-test", nargs=2, metavar=("REPORT_JSON", "VIDEO_URL"), help="Buffer に Threads の動画の下書きを作るテスト（--yes が無ければ表示だけ）")
    parser.add_argument("--yes", action="store_true", help="--draft-test を本当に送る")
    parser.add_argument("--check-post", metavar="BUFFER_ID", help="出した動画の投稿の状態を Buffer で読む（読むだけ）")
    parser.add_argument("--merge-draft-test", metavar="SLUG", help="写真の下書きを1本作り、動画に差し替えて読み直す（--yes が無ければ表示だけ。公開しない）")
    parser.add_argument("--today", action="store_true", help="今日の Threads の投稿ごとに、動画が付いたか・理由（読むだけ）")
    parser.add_argument("--sync-posted", action="store_true", help="動画つきで送った記録を state/threads_posted.json に写す（Buffer は呼ばない）")
    parser.add_argument("--queue-fix", help="plan（一覧）／apply（下書きに戻して動画待ちにする）／pause:ID,ID（その予約だけ下書きに戻す）")
    parser.add_argument("--requeue", metavar="SLUG:SNS[,SNS]", help="下書きに戻した投稿を、新しい形・動画つきで出し直す（例 slug:facebook）")
    parser.add_argument("--repost", metavar="SLUG", help="出し直し（content/sns_reposts/<slug>.json の承認済みの文面か今の決まりの文面・動画つきで次のふつうの枠に）")
    parser.add_argument("--slot", default="", help="--repost の枠（ISO 8601。省略すると次のふつうの枠）")
    parser.add_argument("--adopt-video", nargs=2, metavar=("SLUG", "DIR"), help="検査に合格して保存した動画を、この記事の SNS 用に置く（作り直さない）")
    parser.add_argument("--publish", action="store_true", help="--repost / --adopt-video のあと、動画を待っている投稿を枠の順に予約する")
    args = parser.parse_args()
    if args.repost or args.adopt_video:
        import llm

        llm._load_env_local()
        if args.repost:
            for slug in args.repost.split(","):
                print(json.dumps(repost(slug.strip(), slot=_parse(args.slot) if args.slot else None), ensure_ascii=False))
        if args.adopt_video:
            print(json.dumps(adopt_video(args.adopt_video[0], Path(args.adopt_video[1])), ensure_ascii=False))
        if args.publish:
            state, posted = load_json(STATE, {"videos": {}}), load_json(POSTED, {"posted": {}})
            apply_converted(state, posted)
            POSTED.write_text(json.dumps(posted, ensure_ascii=False, indent=2) + "\n", "utf-8")
            for row in publish_queued(state, posted, datetime.now(timezone.utc), flags()):
                print(json.dumps(row, ensure_ascii=False))
            save_state(state)
        return
    if args.requeue:
        import llm

        llm._load_env_local()
        slug, _, svc = args.requeue.partition(":")
        print(json.dumps(requeue(slug, [x for x in svc.split(",") if x in ("twitter", "facebook")]), ensure_ascii=False))
        return
    if args.queue_fix and args.queue_fix.startswith("pause:"):
        # 予約中の投稿のうち、この ID だけを下書きに戻す（公開しない・消さない。二重に入った予約を止めるとき）
        ids = {x.strip() for x in args.queue_fix[len("pause:"):].split(",") if x.strip()}
        key = os.environ.get("BUFFER_API_KEY", "")
        for node in scheduled_nodes(key):
            if node["id"] in ids:
                after = pause_post(key, node)
                print(json.dumps({"id": node["id"], "service": node.get("service"), "status": after.get("status")}, ensure_ascii=False))
        return
    if args.queue_fix:
        import llm

        llm._load_env_local()
        for row in queue_fix(apply=args.queue_fix == "apply"):
            print(json.dumps(row, ensure_ascii=False))
        return
    if args.sync_posted:
        posted = load_json(POSTED, {"posted": {}})
        n = sync_posted(load_json(STATE, {"videos": {}}), posted)
        if n:
            POSTED.write_text(json.dumps(posted, ensure_ascii=False, indent=2) + "\n", "utf-8")
        log(f"[video-followup] threads_posted.json に {n} 件を写しました")
        return
    import llm

    llm._load_env_local()
    if args.draft_test:
        print(json.dumps(draft_test(Path(args.draft_test[0]), args.draft_test[1], args.yes), ensure_ascii=False, indent=2))
        return
    if args.merge_draft_test:
        print(json.dumps(merge_draft_test(args.merge_draft_test, args.yes), ensure_ascii=False, indent=2))
        return
    if args.today:
        for row in today_report():
            print(json.dumps(row, ensure_ascii=False))
        return
    if args.check_post:
        import threads_post

        api_key = os.environ.get("BUFFER_API_KEY", "")
        channel = threads_post.buffer_threads_channel(api_key) or {}
        print(json.dumps(read_draft(api_key, channel.get("id", ""), args.check_post, ("[sent]", "[error]", "[sending]", "")), ensure_ascii=False, indent=2))
        return
    if args.ab_report:
        print(json.dumps(ab_report(), ensure_ascii=False, indent=2))
        return
    for row in run(plan_only=args.plan):
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
