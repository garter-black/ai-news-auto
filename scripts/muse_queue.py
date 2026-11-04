#!/usr/bin/env python3
"""とろいも（Muse）に頼む動画の候補を選び、state/muse_queue.json に書く（2026-11-05 とろいも案 v1・台帳 S30 の受け渡し）。

  python3 scripts/muse_queue.py            # 朝の回: 今日の分を最大3本まで入れる。日中の回: 強い記事だけ足す（合わせて3本まで）
  python3 scripts/muse_queue.py --dry-run  # 書かずに表示だけ
  python3 scripts/muse_queue.py --list     # 今の待ちの一覧（slug・URL・理由・状態）

選ぶ条件（scripts/sns_plan.json の muse）:
  - 公式の出どころ（記事の sourceUrl がある Web の記事。動画の要約・まとめは外す）
  - 立つ数字が1つ（題名かリードに 円・%・件・台・kg・倍 など単位つきの数字）
  - ブランド・お金・暮らし（レーン safety / money / brand。読者に関係する AI は数字があるときだけ）
  - SNS に出してよい話題（海外の企業向け・開発者向けは外す）・まだ SNS に出していない・直近 lookback_hours 時間
動画は Muse が Google Drive に news-<slug>.mp4 で置く。Claude（対話の回）が落として `python3 scripts/muse_ingest.py <mp4> <slug>` で取り込む。
投稿は Muse の動画を wait_hours（既定4時間・速報は0）まで待ち、来なければ今の自動動画（同じ検査）に戻る（sns_video_followup）。
頼んでから24時間たって届かなかったものは expired にする（次の日の3本の数には入れない）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sns_plan  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = ROOT / "content" / "articles"
POSTED = ROOT / "state" / "threads_posted.json"
SITE = "https://ai-tech-express.jp"
JST = sns_plan.JST
NUMBER_RE = re.compile(r"[0-9０-９][0-9０-９,，.．]*\s*(?:万|億|兆)?\s*(?:円|ドル|%|％|件|台|人|社|kg|ｋｇ|W|Nm|倍|GB|TB|mAh|インチ|km|店舗|校|自治体|市町村?)")
EXPIRE_H = 24
MORNING_UNTIL_JST = 10  # この時刻（日本時間）までの回は「朝の回」（今日の分をまとめて入れる）


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def strong_number(article: dict) -> str:
    m = NUMBER_RE.search(f"{article.get('title', '')} {article.get('lede', '')}")
    return m.group(0).strip() if m else ""


def official(article: dict) -> bool:
    url = str(article.get("sourceUrl") or "")
    return article.get("sourceType") == "web" and url.startswith("https://") and "youtube.com" not in url


def muse_score(article: dict) -> tuple[float, str]:
    """(点数, 理由)。条件に合わなければ (0, 理由)"""
    if sns_plan.excluded(article):
        return 0.0, "SNS に出さない話題"
    if not official(article):
        return 0.0, "公式の出どころが無い"
    lane, lane_score = sns_plan.lane_of(article)
    number = strong_number(article)
    if lane not in ("safety", "money", "brand", "consumer_ai") or (lane == "consumer_ai" and not number):
        return 0.0, "ブランド・お金・暮らしの話題ではない"
    if not number:
        return 0.0, "立つ数字が無い"
    score = lane_score + 3 + (1 if sns_plan.japanese_source(article) else 0)
    label = sns_plan.plan()["lanes"][lane]["label"]
    return float(score), f"{label}・数字「{number}」・公式（{article.get('sourceName') or article.get('sourceUrl', '')[:60]}）"


def load_articles(since: datetime) -> list[dict]:
    out = []
    for path in ARTICLES.glob("*.json"):
        try:
            a = json.loads(path.read_text("utf-8"))
            if datetime.fromisoformat(a["generatedAt"]) >= since:
                out.append(a)
        except (ValueError, KeyError):
            continue
    return out


def expire(data: dict, now: datetime) -> int:
    n = 0
    for item in data.get("items", []):
        if item.get("status") != "queued":
            continue
        try:
            queued = datetime.fromisoformat(item["queuedAt"])
        except (KeyError, ValueError):
            continue
        if now - queued > timedelta(hours=EXPIRE_H):
            item.update({"status": "expired", "expiredAt": now.isoformat(timespec="seconds")})
            n += 1
    return n


def queued_today(data: dict, now: datetime) -> int:
    today = now.astimezone(JST).date()
    return sum(1 for i in data.get("items", []) if i.get("queuedAt") and datetime.fromisoformat(i["queuedAt"]).astimezone(JST).date() == today)


def pick(data: dict, now: datetime, articles: list[dict], posted: dict) -> list[dict]:
    m = sns_plan.plan()["muse"]
    room = int(m["max_per_day"]) - queued_today(data, now)
    if room <= 0:
        return []
    morning = now.astimezone(JST).hour < MORNING_UNTIL_JST
    taken = {i["slug"] for i in data.get("items", [])}
    rows = []
    for a in articles:
        slug = a["slug"]
        rec = posted.get(slug) or {}
        if slug in taken or rec.get("id") or rec.get("ownerHold"):
            continue  # もう SNS に出た・オーナーの保留
        score, why = muse_score(a)
        if score <= 0:
            continue
        if not morning and score < float(m["strong_score"]):
            continue  # 日中は強い記事だけ足す
        rows.append((score, a["generatedAt"], a, why))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    out = []
    for score, _, a, why in rows[:room]:
        out.append({"slug": a["slug"], "url": f"{SITE}/articles/{a['slug']}", "title": a.get("title", ""), "reason": why,
                    "score": score, "lane": sns_plan.lane_of(a)[0], "breaking": bool(a.get("breaking")),
                    "file": f"news-{a['slug']}.mp4", "queuedAt": now.isoformat(timespec="seconds"), "status": "queued"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    data = sns_plan.load_muse_queue()
    data.setdefault("items", [])
    if args.list:
        for i in data["items"][-12:]:
            print(f"{i.get('status'):9} {i['slug']}  {i.get('url')}\n          理由: {i.get('reason')}")
        return 0
    expired = expire(data, now)
    hours = float(sns_plan.plan()["muse"]["lookback_hours"])
    posted = (json.loads(POSTED.read_text("utf-8")) if POSTED.exists() else {}).get("posted") or {}
    new = pick(data, now, load_articles(now - timedelta(hours=hours)), posted)
    for i in new:
        log(f"[muse] 候補: {i['slug']}（{i['reason']}）")
    if not new and not expired:
        log("[muse] 新しい候補はありません")
        return 0
    data["items"] = (data["items"] + new)[-60:]
    data["updatedAt"] = now.isoformat(timespec="seconds")
    if args.dry_run:
        print(json.dumps(new, ensure_ascii=False, indent=2))
        return 0
    sns_plan.MUSE_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    sns_plan.MUSE_QUEUE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    log(f"[muse] {len(new)}本を足し、{expired}本を期限切れにしました（state/muse_queue.json）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
