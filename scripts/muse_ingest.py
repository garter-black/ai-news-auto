#!/usr/bin/env python3
"""とろいも（Muse）が作った動画を取り込み、その記事の SNS の投稿に使う（2026-11-05 とろいも案 v1・台帳 S30）。

  python3 scripts/muse_ingest.py <手元の mp4> <slug>              # 確かめる → 置く → 記録して main に push
  python3 scripts/muse_ingest.py <mp4> <slug> --check            # 確かめるだけ（置かない・記録しない）
  python3 scripts/muse_ingest.py <mp4> <slug> --hold             # 置くが、投稿には使わない（オーナーに見せてから。--release で外す）
  python3 scripts/muse_ingest.py --release <slug>                # --hold を外して投稿に使う
  python3 scripts/muse_ingest.py <mp4> <slug> --no-push          # 手元の state だけ書く（試すとき）

手順（Claude が対話の回で動かす。動画は Muse が Google Drive に news-<slug>.mp4 で置き、Claude が落としてくる）
  1. 確かめる: 長さ 30〜120秒・画面 1920x1080 か 1080x1920・音声あり（ffprobe）。だめなら何もしない（終了コード 2）
  2. 置く: 今の SNS 動画と同じ所（Workers KV saisoku-images → https://ai-tech-express.jp/video/<名前>）に動画と表紙の画像
  3. 記録: state/sns_video.json の videos[slug] を Muse の動画にする（前の自動動画は videosArchive に残す）＝自動動画の代わりに使う。
     state/muse_queue.json の状態を delivered にする。予約前の投稿（動画待ち）は sns_video_followup が次の回に Muse の動画で送る。
     もう Buffer に予約された投稿があれば、BUFFER_API_KEY があるときだけ予約の動画を差し替える（時刻・本文はそのまま）
  4. main に push（一時的な作業ツリーで origin/main に載せ、scripts/git_safe_push.py で送る。今いるブランチは触らない）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sns_plan  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://ai-tech-express.jp"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def probe(mp4: Path) -> dict:
    """ffprobe で長さ・画面の大きさ・音声の有無"""
    exe = shutil.which("ffprobe") or "ffprobe"
    out = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(mp4)],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"動画を読めません: {out.stderr[-200:]}")
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    return {"seconds": round(duration, 2), "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
            "audio": bool(audio), "codec": video.get("codec_name", ""), "bytes": mp4.stat().st_size}


def problems(info: dict) -> list[str]:
    m = sns_plan.plan()["muse"]
    out = []
    if not (float(m["min_seconds"]) <= info["seconds"] <= float(m["max_seconds"])):
        out.append(f"長さが {info['seconds']}秒（{m['min_seconds']}〜{m['max_seconds']}秒まで）")
    if [info["width"], info["height"]] not in [list(x) for x in m["sizes"]]:
        out.append(f"画面が {info['width']}x{info['height']}（1920x1080 か 1080x1920）")
    if not info["audio"]:
        out.append("音声が無い")
    if info["bytes"] >= 25 * 1024 * 1024:
        out.append(f"大きすぎる（{info['bytes'] / 1e6:.1f}MB。置き場所の上限 25MB 未満に）")
    return out


def article(slug: str, root: Path = ROOT) -> dict:
    path = root / "content" / "articles" / f"{slug}.json"
    if not path.exists():
        raise SystemExit(f"[muse] 記事がありません: {slug}")
    return json.loads(path.read_text("utf-8"))


def video_record(slug: str, info: dict, urls: dict, art: dict, now: datetime, hold: bool, approved: str) -> dict:
    report = {"title": art.get("title", ""), "url": f"{SITE}/articles/{slug}", "total": info["seconds"], "level": "MUSE", "voice": {},
              "script_v3": {"hook": {"text": ""}, "summary": ""}}
    qa = {"muse": {"validated": True, **{k: info[k] for k in ("seconds", "width", "height", "audio")}, "approved": approved}}
    version = {"ok": True, "seconds": info["seconds"], "reasons": [], "qa": qa, "template": "muse", "level": "MUSE", "post": report}
    return {"at": now.isoformat(timespec="seconds"), "status": "ready", "tries": 1, "ok": True, "adopted": "muse", "level": "MUSE",
            "versions": {"short": version}, "urls": urls, "variant": "short", **({"museHold": True} if hold else {})}


def apply_state(root: Path, slug: str, rec: dict | None, now: datetime, release: bool = False) -> list[str]:
    """root の state を書き換える（変えたファイルの一覧）"""
    vpath, qpath = root / "state" / "sns_video.json", root / "state" / "muse_queue.json"
    state = json.loads(vpath.read_text("utf-8")) if vpath.exists() else {"videos": {}}
    videos = state.setdefault("videos", {})
    if release:
        cur = videos.get(slug) or {}
        if cur.get("adopted") != "muse":
            raise SystemExit(f"[muse] {slug} に Muse の動画が置かれていません")
        cur.pop("museHold", None)
        cur["status"] = "ready"
    else:
        if slug in videos:
            state.setdefault("videosArchive", {})[f"{slug}@{now:%Y%m%d%H%M}"] = videos.pop(slug)
        videos[slug] = rec
        if rec.get("museHold"):
            videos[slug]["status"] = "held"  # 投稿に使わない（publish_queued・attach_ready は ready だけを使う）
    vpath.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", "utf-8")
    queue = sns_plan.load_muse_queue(qpath)
    queue.setdefault("items", [])
    item = next((i for i in queue["items"] if i.get("slug") == slug), None)
    if item is None:
        item = {"slug": slug, "url": f"{SITE}/articles/{slug}", "reason": "オーナー・Muse が直接選んだ", "queuedAt": now.isoformat(timespec="seconds")}
        queue["items"].append(item)
    item.update({"status": "delivered", "deliveredAt": now.isoformat(timespec="seconds"), **({"held": True} if (rec or {}).get("museHold") else {})})
    if release:
        item.pop("held", None)
    queue["updatedAt"] = now.isoformat(timespec="seconds")
    qpath.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return ["state/sns_video.json", "state/muse_queue.json"]


def push_to_main(slug: str, rec: dict | None, now: datetime, release: bool) -> bool:
    """一時的な作業ツリー（origin/main）で記録を書いて push（今のブランチ・作業中のファイルは触らない）"""
    tmp = Path(tempfile.mkdtemp(prefix="muse-ingest-"))
    wt = tmp / "wt"
    try:
        subprocess.run(["git", "-C", str(ROOT), "fetch", "-q", "origin", "main"], check=True, timeout=120)
        subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "-q", "--detach", str(wt), "origin/main"], check=True, timeout=120)
        files = apply_state(wt, slug, rec, now, release)
        subprocess.run(["git", "-C", str(wt), "add", *files], check=True)
        msg = f"chore(muse): {'Muse の動画の保留を外す' if release else 'Muse の動画を取り込む'} {slug}"
        subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", msg], check=True)
        ok = subprocess.run([sys.executable, "scripts/git_safe_push.py"], cwd=wt, timeout=900).returncode == 0
        return ok
    finally:
        subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)], capture_output=True, timeout=120)
        shutil.rmtree(tmp, ignore_errors=True)


def swap_scheduled(slug: str, url: str, rec: dict) -> dict:
    """もう Buffer に予約された投稿の動画を Muse の動画に替える（BUFFER_API_KEY があるときだけ。時刻・本文はそのまま）"""
    key = os.environ.get("BUFFER_API_KEY", "")
    import sns_video_followup as f  # noqa: PLC0415

    state = f.load_json(f.STATE, {"videos": {}})
    posted = f.load_json(f.POSTED, {"posted": {}})
    channels = ((state.get("queued") or {}).get(slug) or {}).get("channels") or {}
    if not channels:
        return {"swap": "まだ予約されていない（動画待ちの投稿は次の回に Muse の動画で送られる）"}
    if not key:
        return {"swap": "予約済み。BUFFER_API_KEY が無いので差し替えていない（GitHub の sns_video.yml で --swap-video を使う）", "channels": channels}
    inputs = (((posted.get("posted") or {}).get(slug) or {}).get("pending") or {}).get("inputs") or {}
    report = rec["versions"]["short"]["post"]
    out = {}
    for service, post_id in channels.items():
        if service not in inputs or not post_id:
            continue
        cur = f.read_post(key, post_id) or {}
        if cur.get("status") != "scheduled":
            out[service] = f"予約中ではない（{cur.get('status')}）"
            continue
        inp = f.video_input(service, inputs[service], report, "short", url)
        inp["id"] = post_id
        for k in ("channelId", "schedulingType", "mode", "dueAt"):
            inp.pop(k, None)
        after = f.edit_post(key, inp, f.article_of(report), service=service)
        out[service] = {"status": after.get("status"), "dueAt": after.get("dueAt")}
    return {"swap": out}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mp4", nargs="?")
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--check", action="store_true", help="確かめるだけ")
    ap.add_argument("--hold", action="store_true", help="置くが投稿には使わない（オーナーに見せてから --release）")
    ap.add_argument("--release", metavar="SLUG", help="保留を外して投稿に使う")
    ap.add_argument("--approved", default="muse-partner", help="だれが確かめたか（記録に残す。例: owner-ok-2026-11-05）")
    ap.add_argument("--no-push", action="store_true", help="手元の state だけ書く")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    if args.release:
        if args.no_push:
            apply_state(ROOT, args.release, None, now, release=True)
            return 0
        return 0 if push_to_main(args.release, None, now, True) else 1
    if not args.mp4 or not args.slug:
        ap.error("mp4 と slug が要る")
    mp4 = Path(args.mp4).expanduser().resolve()
    art = article(args.slug)
    expected = f"news-{args.slug}.mp4"
    if mp4.name != expected:
        log(f"[muse] 注意: ファイル名が {expected} ではありません（{mp4.name}）。slug を確かめてください")
    info = probe(mp4)
    bad = problems(info)
    print(json.dumps({"slug": args.slug, "title": art.get("title"), **info, "problems": bad}, ensure_ascii=False))
    if bad:
        log("[muse] 取り込みません: " + "・".join(bad))
        return 2
    if args.check:
        return 0
    import sns_video_followup as f  # noqa: PLC0415 - 置き方は今の SNS 動画と同じ（Workers KV）

    url = f.upload_kv(mp4, args.slug)
    urls = {"short": url}
    if info["width"] > info["height"]:
        urls["poster"] = f.upload_kv(f.poster_of(mp4, at=1.0), args.slug, "jpg")
    rec = video_record(args.slug, info, urls, art, now, args.hold, args.approved)
    log(f"[muse] 置きました: {url}")
    if args.no_push:
        apply_state(ROOT, args.slug, rec, now)
    elif not push_to_main(args.slug, rec, now, False):
        log("[muse] main に push できませんでした（動画は置いてあります。もう一度動かしてください）")
        return 1
    if not args.hold:
        print(json.dumps(swap_scheduled(args.slug, url, rec), ensure_ascii=False))
    log("[muse] 完了。" + ("保留中（--release で投稿に使う）" if args.hold else "この記事の SNS の投稿は Muse の動画を使います"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
