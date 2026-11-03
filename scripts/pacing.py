#!/usr/bin/env python3
"""記事の1日の上限の数え方と、1日の中での配分（2026-11-03）。

1. 上限に数えるのは「いま公開中の記事」だけ。取り下げた記事（content/retired.json に移り、JSON が消えたもの。
   薄い・主題外・重複など）は数えない。取り下げた分は、その日のうちに質の良い記事で埋め直せる。
2. 夜中（日本時間 23時〜7時）に海外の発表がまとまって出ても、上限を使い切らない。
   夜中に使えるのは上限の NIGHT_SHARE（既定 3割）まで。残りは 7時〜23時に少しずつ使えるようにする
   （1時間先までの分を前借りできる）。読まれる昼間と SNS の投稿枠に新しい記事が出るようにするため。
   夜中に使わなかった候補は消えずに残るので（対象は発表から48時間以内）、朝からの回で記事になる。
   速報レーン・大きなニュースはこの配分の外（上限に達していても作る。従来どおり）。

  python3 scripts/pacing.py          # 今日の時間ごとの本数（作った本数 / いま公開中の本数）と、いまの配分を表示
  python3 scripts/pacing.py --days 2 # 昨日の分も表示
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
STATE_PATH = ROOT / "content" / "state.json"

DAY_START = 7  # 昼の配分の始まり（日本時間）
DAY_END = 23  # 昼の配分の終わり。ここで上限いっぱいまで使える
NIGHT_SHARE = float(os.environ.get("PACE_NIGHT_SHARE", "0.3"))
LOOKAHEAD_MIN = int(os.environ.get("PACE_LOOKAHEAD_MIN", "60"))


def _today_entries(state: dict, now: datetime):
    """今日（日本時間）作った記事の (時刻, 記録) を返す（processed_web で slug があるもの）"""
    today = now.astimezone(JST).date()
    for value in (state.get("processed_web") or {}).values():
        if not (isinstance(value, dict) and value.get("slug")):
            continue
        try:
            at = datetime.fromisoformat(value["at"]).astimezone(JST)
        except (KeyError, TypeError, ValueError):
            continue
        if at.date() == today:
            yield at, value


def is_live(slug: str, articles_dir: Path | None = None) -> bool:
    """記事がいま公開中か（取り下げると JSON が消える。scripts/retire_articles.py）"""
    return ((articles_dir or ARTICLES_DIR) / f"{slug}.json").exists()


def live_today(state: dict, now: datetime | None = None, articles_dir: Path | None = None) -> int:
    """1日の上限に数える本数: 今日作った記事のうち、いま公開中のもの。読者像から外れた一般の話題は数えない（別の上限）"""
    now = now or datetime.now(JST)
    return sum(1 for _, v in _today_entries(state, now) if not v.get("offPersona") and is_live(v["slug"], articles_dir))


def allowed_by(cap: int, now: datetime | None = None, night_share: float | None = None, lookahead_min: int | None = None) -> int:
    """この時刻までに使ってよい本数（1日の累計）。0 の上限（無制限）はそのまま大きな数を返す"""
    if cap <= 0:
        return 10**6
    now = (now or datetime.now(JST)).astimezone(JST)
    share = NIGHT_SHARE if night_share is None else night_share
    ahead = LOOKAHEAD_MIN if lookahead_min is None else lookahead_min
    night = min(cap, math.ceil(cap * max(0.0, min(1.0, share))))
    minutes = now.hour * 60 + now.minute + ahead
    if minutes < DAY_START * 60:
        return night
    span = (DAY_END - DAY_START) * 60
    done = min(1.0, (minutes - DAY_START * 60) / span)
    return min(cap, night + math.floor((cap - night) * done))


def run_allowance(cap: int, done_today: int, now: datetime | None = None) -> tuple[int, int, int]:
    """(この回に作ってよい本数, 1日の上限までの残り, いまの時刻までの配分の残り)"""
    if cap <= 0:
        return 10**6, 10**6, 10**6
    by_cap = cap - done_today
    by_pace = allowed_by(cap, now) - done_today
    return min(by_cap, by_pace), by_cap, by_pace


def by_hour(state: dict, now: datetime | None = None, articles_dir: Path | None = None) -> dict[int, tuple[int, int]]:
    """今日の時間ごとの (作った本数, いま公開中の本数)。読者像外の話題も含む（実際に出た本数を見るため）"""
    now = now or datetime.now(JST)
    out: dict[int, list[int]] = {}
    for at, v in _today_entries(state, now):
        row = out.setdefault(at.hour, [0, 0])
        row[0] += 1
        row[1] += is_live(v["slug"], articles_dir)
    return {h: (c, l) for h, (c, l) in sorted(out.items())}


def hour_line(state: dict, now: datetime | None = None, articles_dir: Path | None = None) -> str:
    rows = by_hour(state, now, articles_dir)
    if not rows:
        return "まだなし"
    return " ".join(f"{h:02d}時 {c}/{l}" for h, (c, l) in rows.items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=1, help="何日分を表示するか（今日から遡る）")
    parser.add_argument("--cap", type=int, default=int(os.environ.get("WEB_DAILY_CAP", "40") or 40), help="1日の上限（表示用）")
    args = parser.parse_args()
    state = json.loads(STATE_PATH.read_text("utf-8")) if STATE_PATH.exists() else {}
    now = datetime.now(JST)
    for back in range(args.days - 1, -1, -1):
        day = now - timedelta(days=back)
        rows = by_hour(state, day)
        made = sum(c for c, _ in rows.values())
        live = sum(l for _, l in rows.values())
        print(f"{day:%Y-%m-%d}（作った {made} 本 / いま公開中 {live} 本）")
        for h in range(24):
            c, l = rows.get(h, (0, 0))
            print(f"  {h:02d}時 {c:3d} / {l:3d} {'#' * l}{'.' * (c - l)}")
    done = live_today(state, now)
    allow, by_cap, by_pace = run_allowance(args.cap, done, now)
    print(f"いまの配分: 上限 {args.cap} / 上限に数える本数 {done} / この時刻までの配分 {allowed_by(args.cap, now)} → この回に作れる {max(0, allow)} 本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
