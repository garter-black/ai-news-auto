#!/usr/bin/env python3
"""毎日の先行指標（2026-11-05 とろいも案 v1「毎日の先行指標」）。読むだけ（投稿しない・Buffer は呼ばない）。

  python3 scripts/growth_indicators.py --date 2026-11-05
  python3 scripts/growth_indicators.py --yesterday
  python3 scripts/growth_indicators.py --date 2026-11-05 --json

出すもの
  1. SNS ごとの投稿数（Threads・X・Facebook・Bluesky。state/threads_posted.json・sns_video.json・bluesky_extra.json）
  2. 投稿の閲覧（Threads）: 24時間の最大・中央値（Buffer の数値。threads_learn が1日1回 threads_posted.json に写したもの）。
     1時間後の閲覧は、Buffer の数値が1日1回しか更新されないので取れない（n/a と出す）
  3. 1,000閲覧を超えた投稿の数（その日・その週）
  4. 当たり投稿（1,000閲覧以上）の「閲覧→訪問」率（自前の計測。基準は 3.7%）
  5. フォロワー: Threads（公開ページ）・Bluesky（公開 API）
  6. UTM つきの訪問（流入元 × utm_content＝投稿の種類_リンクの位置。自前の計測 /api/aff/inflow。Cloudflare の RUM は
     クエリを捨てる（requestPath だけ）ので UTM は自前の計測でしか数えられない）
  7. 日本の人の訪問（Cloudflare Web Analytics の RUM。国=JP・HeadlessChrome などの自動ブラウザを除く）
  8. 週の目標との差（W1 11/5–11/11: フォロワー40・日本の訪問200/日・1,000閲覧超え 週2本 / W2 120・600・4 / W3 300・2,500・7）
鍵: AFF_STATS_TOKEN（.env.local か環境変数）。Cloudflare は scripts/images.cf_token（wrangler のログインか CLOUDFLARE_API_TOKEN）
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
THREADS_HANDLE = "saisoku637"
BLUESKY_HANDLE = "ai-tech-express.jp"
HIT_VIEWS = 1000
BASE_VISIT_RATE = 0.037
TARGETS = [  # (週の始め, 終わり, 名前, Threads フォロワー, 日本の訪問/日, 1,000閲覧超え/週)
    (date(2026, 11, 5), date(2026, 11, 11), "W1", 40, 200, 2),
    (date(2026, 11, 12), date(2026, 11, 18), "W2", 120, 600, 4),
    (date(2026, 11, 19), date(2026, 11, 26), "W3", 300, 2500, 7),
]
HEADLESS = re.compile(r"headless|bot|crawler|spider|lighthouse|pagespeed|unknown", re.I)


def load(name: str) -> dict:
    try:
        return json.loads((ROOT / "state" / name).read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _dt(raw) -> datetime | None:
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def post_time(rec: dict, queued: dict) -> datetime | None:
    return _dt(rec.get("sentAt")) or _dt(queued.get("dueAt")) or _dt(rec.get("dueAt")) or (_dt(rec.get("postedAt")) if rec.get("id") else None)


def posts_on(day: date, posted: dict, videos: dict, extra: dict) -> dict:
    """その日（日本時間）に出た投稿（SNS ごと）。Threads は記録の id か動画つきで送った記録"""
    queued = videos.get("queued") or {}
    out: dict[str, list[str]] = {"threads": [], "x": [], "facebook": [], "bluesky": []}
    for slug, rec in posted.items():
        q = queued.get(slug) or {}
        when = post_time(rec, q)
        if not when or when.astimezone(JST).date() != day:
            continue
        ch = {**(rec.get("channels") or {}), **(q.get("channels") or {})}
        if rec.get("id") or ch.get("threads") or q.get("status") == "created":
            out["threads"].append(slug)
        if ch.get("twitter"):
            out["x"].append(slug)
        if ch.get("facebook"):
            out["facebook"].append(slug)
        if ch.get("bluesky"):
            out["bluesky"].append(slug)
    for slug, rec in extra.items():
        when = _dt(rec.get("postedAt") or rec.get("at"))
        if rec.get("uri") and when and when.astimezone(JST).date() == day and slug not in out["bluesky"]:
            out["bluesky"].append(slug)
    return out


def views_of(rec: dict) -> tuple[int | None, bool]:
    """(閲覧, 24時間たったか)。Buffer の数値が無ければ None"""
    m = rec.get("metrics") or {}
    if "views" not in m:
        return None, False
    sent, upd = _dt(rec.get("sentAt")), _dt(rec.get("metricsUpdatedAt"))
    full = bool(sent and upd and upd - sent >= timedelta(hours=20))
    return int(m.get("views") or 0), full


def inflow(day: date) -> dict:
    try:
        import inflow_report
        from aff_stats import load_token

        token = load_token()
        return inflow_report.fetch(token, day.isoformat()) if token else {"error": "AFF_STATS_TOKEN が無い"}
    except Exception as error:  # noqa: BLE001
        return {"error": f"{type(error).__name__}: {str(error)[:120]}"}


def threads_followers() -> int | None:
    """Threads の公開ページ（リンクプレビュー向けの HTML の「N Followers」）"""
    try:
        req = urllib.request.Request(f"https://www.threads.com/@{THREADS_HANDLE}", headers={"User-Agent": "facebookexternalhit/1.1"})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        m = re.search(r"([\d,.]+)\s*([KkMm万]?)\s*(?:Followers|フォロワー)", html)
        if not m:
            return None
        n = float(m.group(1).replace(",", ""))
        return int(n * {"k": 1e3, "m": 1e6, "万": 1e4}.get(m.group(2).lower(), 1))
    except Exception:  # noqa: BLE001
        return None


def bluesky_followers() -> int | None:
    try:
        url = f"https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={BLUESKY_HANDLE}"
        return int(json.loads(urllib.request.urlopen(url, timeout=30).read()).get("followersCount"))
    except Exception:  # noqa: BLE001
        return None


def japan_visits(day: date) -> dict:
    """Cloudflare Web Analytics（RUM）の日本の人の訪問。自動ブラウザ（Headless など）・Unknown を除く"""
    try:
        import images

        q = """query($a:String!,$d:Date!){viewer{accounts(filter:{accountTag:$a}){rumPageloadEventsAdaptiveGroups(limit:100,
          filter:{date_geq:$d,date_leq:$d,countryName:"JP",requestHost:"ai-tech-express.jp"}){count sum{visits} dimensions{userAgentBrowser}}}}}"""
        body = json.dumps({"query": q, "variables": {"a": images.ACCOUNT_ID, "d": day.isoformat()}}).encode()
        req = urllib.request.Request("https://api.cloudflare.com/client/v4/graphql", data=body,
                                     headers={"Authorization": f"Bearer {images.cf_token('analytics')}", "Content-Type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=60).read())
        rows = data["data"]["viewer"]["accounts"][0]["rumPageloadEventsAdaptiveGroups"]
        human = [r for r in rows if not HEADLESS.search(r["dimensions"]["userAgentBrowser"] or "")]
        return {"visits": sum(r["sum"]["visits"] for r in human), "pageviews": sum(r["count"] for r in human),
                "excluded": sum(r["sum"]["visits"] for r in rows) - sum(r["sum"]["visits"] for r in human)}
    except Exception as error:  # noqa: BLE001
        return {"error": f"{type(error).__name__}: {str(error)[:120]}"}


def target_for(day: date):
    return next((t for t in TARGETS if t[0] <= day <= t[1]), None)


def build(day: date, net: bool = True) -> dict:
    posted = (load("threads_posted.json").get("posted") or {})
    videos = load("sns_video.json")
    extra = load("bluesky_extra.json")
    posts = posts_on(day, posted, videos, extra)
    views = []
    for slug in posts["threads"]:
        v, full = views_of(posted.get(slug) or {})
        if v is not None:
            views.append((slug, v, full))
    target = target_for(day)
    week = [target[0] + timedelta(days=i) for i in range((min(day, target[1]) - target[0]).days + 1)] if target else [day]
    week_hits = []
    for d in week:
        for slug in posts_on(d, posted, videos, extra)["threads"]:
            v, _ = views_of(posted.get(slug) or {})
            if v is not None and v >= HIT_VIEWS:
                week_hits.append((slug, v))
    out: dict = {
        "date": day.isoformat(),
        "posts": {k: len(v) for k, v in posts.items()},
        "views": {
            "posts_with_metrics": len(views),
            "h1": None, "h1_note": "Buffer の数値は1日1回の更新のため、1時間後の閲覧は取れない",
            "h24_max": max((v for _, v, _ in views), default=None),
            "h24_median": statistics.median([v for _, v, _ in views]) if views else None,
            "complete_24h": sum(1 for *_, f in views if f),
        },
        "hits_day": [s for s, v, _ in views if v >= HIT_VIEWS],
        "hits_week": week_hits,
    }
    if not net:
        return out
    flow = inflow(day)
    rows = flow.get("rows") or []
    utm: dict[str, int] = {}
    for r in rows:
        if r.get("source") in ("threads", "x", "facebook", "bluesky") and r.get("content"):
            key = f"{r['source']}/{r['content']}"
            utm[key] = utm.get(key, 0) + int(r.get("views") or 0)
    out["utm_visits"] = dict(sorted(utm.items(), key=lambda kv: -kv[1]))
    out["inflow_error"] = flow.get("error")
    rates = []
    for slug, v, _ in views:
        if v >= HIT_VIEWS:
            visits = sum(int(r.get("views") or 0) for r in rows if r.get("page") == slug and r.get("source") == "threads")
            rates.append({"slug": slug, "views": v, "visits": visits, "rate": round(visits / v, 4) if v else None})
    out["hit_visit_rate"] = rates
    out["followers"] = {"threads": threads_followers(), "bluesky": bluesky_followers()}
    out["japan"] = japan_visits(day)
    if target:
        jp = out["japan"].get("visits")
        tf = out["followers"]["threads"]
        out["target"] = {
            "week": target[2], "threads_followers": target[3], "japan_visits_per_day": target[4], "hits_per_week": target[5],
            "gap_followers": (tf - target[3]) if tf is not None else None,
            "gap_japan": (jp - target[4]) if jp is not None else None,
            "gap_hits": len(week_hits) - target[5],
        }
    return out


def show(r: dict) -> None:
    p = r["posts"]
    print(f"■ 先行指標 {r['date']}（日本時間）")
    print(f"  投稿数: Threads {p['threads']} / X {p['x']} / Facebook {p['facebook']} / Bluesky {p['bluesky']}")
    v = r["views"]
    print(f"  Threads の閲覧（24時間）: 最大 {v['h24_max']} / 中央値 {v['h24_median']}（数値あり {v['posts_with_metrics']}本・24時間たった {v['complete_24h']}本）")
    print(f"  1時間後の閲覧: n/a（{v['h1_note']}）")
    print(f"  1,000閲覧超え: この日 {len(r['hits_day'])}本 / この週 {len(r['hits_week'])}本 {[s for s, _ in r['hits_week']]}")
    if "hit_visit_rate" in r:
        for h in r["hit_visit_rate"]:
            print(f"    当たり {h['slug']}: 閲覧 {h['views']} → 訪問 {h['visits']}（{(h['rate'] or 0) * 100:.1f}%・基準 {BASE_VISIT_RATE * 100:.1f}%）")
    if "followers" in r:
        f = r["followers"]
        print(f"  フォロワー: Threads {f['threads']} / Bluesky {f['bluesky']}")
        if r.get("inflow_error"):
            print(f"  UTM の訪問: 読めない（{r['inflow_error']}）")
        else:
            print("  UTM の訪問（流入元/種類_位置）: " + ("、".join(f"{k} {n}" for k, n in r["utm_visits"].items()) or "なし"))
        j = r["japan"]
        print(f"  日本の人の訪問（Cloudflare）: " + (f"{j['visits']}（ページ表示 {j['pageviews']}・自動ブラウザなど除外 {j['excluded']}）" if "visits" in j else f"読めない（{j.get('error')}）"))
    t = r.get("target")
    if t:
        def g(x):
            return "n/a" if x is None else (f"+{x}" if x >= 0 else str(x))
        print(f"  目標との差（{t['week']}）: フォロワー {t['threads_followers']} に {g(t['gap_followers'])} / 日本の訪問 {t['japan_visits_per_day']}/日 に {g(t['gap_japan'])} / 1,000閲覧超え 週{t['hits_per_week']}本 に {g(t['gap_hits'])}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date")
    ap.add_argument("--yesterday", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--offline", action="store_true", help="通信しない（記録だけで出す）")
    args = ap.parse_args()
    today = datetime.now(JST).date()
    day = date.fromisoformat(args.date) if args.date else today - timedelta(days=1) if args.yesterday else today
    r = build(day, net=not args.offline)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    else:
        show(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
