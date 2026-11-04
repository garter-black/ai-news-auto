#!/usr/bin/env python3
"""
記事に「外から来た人」の数を、流入元・記事・SNS の投稿ごとに出す（自前計測。毎晩・毎朝の報告用）。

  python3 scripts/inflow_report.py --yesterday        # 昨日（日本時間）
  python3 scripts/inflow_report.py --date 2026-11-03
  python3 scripts/inflow_report.py                    # 今日（途中まで）
  python3 scripts/inflow_report.py --yesterday --top 5
  python3 scripts/inflow_report.py --date 2026-11-03 --json   # 生の JSON

数えるもの: 記事・朝のまとめ・トップページを「そのタブで最初に開いた」1回（外からの入口）。JS が動いた実ブラウザだけ。
数えないもの: ロボット・SNS のリンクプレビュー・自動ブラウザ（HeadlessChrome・webdriver）・Lighthouse・PageSpeed・
  JS の動かないアクセス・自社のテスト（?_internal=1 を付けて開いたブラウザ、ヘッダー X-Saisoku-Internal: 1）。
  同じ人が同じページを30分以内に開き直したものは1回。人数は「日付+IP+UA」のハッシュによる推定（日をまたいで足さない）。
流入元: utm_source があればそれ（threads / x / facebook / bluesky / newsletter / line）、無ければ参照元のサイト
  （google / yahoo / bing / discover / news / direct / other）。

鍵 AFF_STATS_TOKEN は環境変数か .env.local（メインのフォルダ）から読む（scripts/aff_stats.py と同じ）。中身は表示しない。
数えるのは Worker（cloudflare/inflow.ts）、送るのは画面の components/InflowBeacon.tsx。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aff_stats import JST, load_token  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

LABELS = {
    "threads": "Threads", "x": "X", "facebook": "Facebook", "bluesky": "Bluesky", "google": "Google検索",
    "yahoo": "Yahoo!検索", "bing": "Bing", "discover": "Googleアプリ/Discover", "news": "ニュースアプリ",
    "newsletter": "メルマガ", "line": "LINE", "direct": "直接・不明", "other": "その他のサイト",
}
SNS = ("threads", "x", "facebook", "bluesky")
# 書き出しの型（state/threads_posted.json の opener → hook の順）。opener は 2026-11-03〜の1行目の呼びかけの言い回し
HOOK_LABELS = {
    "check": "呼びかけ・要チェック", "good": "呼びかけ・朗報", "see": "呼びかけ・見てほしい", "know": "呼びかけ・知っておいて",
    "keep": "呼びかけ・押さえたい", "careful": "呼びかけ・確認を", "gen": "呼びかけ", "callout": "呼びかけ", "breaking": "速報",
    "digest": "まとめ", "poll": "問いかけ",
}
SLOT_TEST_LABELS = {"non_ai": "AI以外の枠", "ai": "AIの枠", "non_ai_fallback": "AI以外の枠（記事が無くAI）"}


def hook_label(rec: dict) -> str:
    key = rec.get("opener") or rec.get("hook") or ""
    if key in HOOK_LABELS:
        return HOOK_LABELS[key]
    try:
        import threads_hooks  # noqa: PLC0415

        return next((h["name"] for h in threads_hooks.HOOKS if h["key"] == key), key)
    except Exception:  # noqa: BLE001
        return key


def site() -> str:
    return os.environ.get("SITE_URL", "https://ai-tech-express.jp").rstrip("/")


def fetch(token: str, date: str, fresh: bool = False) -> dict:
    req = urllib.request.Request(
        f"{site()}/api/aff/inflow?date={date}" + ("&fresh=1" if fresh else ""),
        # 自社のアクセスなので、念のため数えないしるしを付ける
        headers={"Authorization": f"Bearer {token}", "User-Agent": "SaisokuInflowReport/1.0", "X-Saisoku-Internal": "1"},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read())


def title_of(page: str, width: int = 26) -> str:
    if page == "_home":
        return "トップページ"
    if page.startswith("digest-"):
        d = page[len("digest-"):]
        return f"朝のまとめ {d[4:6]}/{d[6:8]}" if len(d) == 8 else page
    try:
        data = json.loads((ROOT / "content" / "articles" / f"{page}.json").read_text("utf-8"))
        text = str(data.get("title") or page)
    except (OSError, ValueError):
        text = page
    return text if len(text) <= width else text[: width - 1] + "…"


def jst(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(JST)
    except (TypeError, ValueError):
        return None


def load_state(name: str) -> dict:
    try:
        return json.loads((ROOT / "state" / name).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def video_variant(campaign: str, content: str) -> str:
    """動画の版（cloudflare/inflow.ts の videoVariant と同じ）。2026-11-05〜 utm_campaign=sns_video_<版>（S29）"""
    if campaign in ("sns_video_short", "sns_video_full"):
        return campaign.rsplit("_", 1)[1]
    return content if campaign == "sns_video" and content in ("short", "full") else ""


def views_of(rows: list[dict], page: str, source: str, campaign: str | None = None, content: str | None = None) -> int:
    if campaign == "sns_video":
        # 動画の投稿は新旧どちらの形でも（content は版 short / full）
        return sum(
            r["views"] for r in rows
            if r["page"] == page and r["source"] == source and video_variant(r.get("campaign", ""), r.get("content", ""))
            and (content is None or video_variant(r.get("campaign", ""), r.get("content", "")) == content)
        )
    return sum(
        r["views"] for r in rows
        if r["page"] == page and r["source"] == source
        and (campaign is None or r.get("campaign", "") == campaign)
        and (content is None or r.get("content", "") == content)
    )


# 自前計測が本番で動き始めた時刻（2026-11-03 10:02 日本時間のデプロイ）。これより前に出た投稿の最初の数時間は数えていない
INFLOW_START = datetime(2026, 11, 3, 10, 2, tzinfo=JST)


def sent_time(rec: dict, now: datetime | None = None) -> tuple[datetime | None, bool]:
    """投稿が実際に出た時刻と、もう出たかどうか。

    postedAt は「Buffer に予約した時刻」で、予約投稿では実際に出る時刻（dueAt）より何時間も前になる。
    sentAt（Buffer の送信時刻）→ delivery.threads.sentAt → dueAt（予約の時刻）→ postedAt（すぐ出した投稿）の順に使う。
    dueAt がまだ来ていない投稿は「予約中」（False）
    """
    now = now or datetime.now(JST)
    delivery = ((rec.get("delivery") or {}).get("threads") or {}).get("sentAt")
    for value in (rec.get("sentAt"), delivery):
        when = jst(value or "")
        if when:
            return when, True
    due = jst(rec.get("dueAt") or "")
    if due:
        return due, due <= now
    when = jst(rec.get("postedAt") or "")
    return when, bool(when and when <= now)


def reader_comments(rec: dict) -> float:
    """読者のコメント数（Buffer のコメント数から自分の返信を引く。scripts/threads_learn.py の reader_metrics と同じ計算）"""
    try:
        import threads_learn  # noqa: PLC0415

        return float(threads_learn.reader_metrics(rec).get("comments") or 0)
    except Exception:  # noqa: BLE001
        return float((rec.get("metrics") or {}).get("comments") or 0)


def per_k(value: float, impressions: int) -> float | None:
    """1000表示あたり（表示が無ければ None）"""
    return round(value / impressions * 1000, 1) if impressions else None


def pattern_breakdown(posts: list[dict]) -> list[dict]:
    """問いかけの型ごと（台帳 S19）: 本数・表示・読者のコメント・訪問と、1000表示あたりのコメント・訪問。
    まだ出ていない予約と動画の行は数えない。型の記録が無い投稿（11/04 より前）も数えない"""
    groups: dict[str, dict] = {}
    for p in posts:
        if p.get("pending") or not p.get("pattern"):
            continue
        g = groups.setdefault(p["pattern"], {"pattern": p["pattern"], "n": 0, "impressions": 0, "comments": 0.0, "visits": 0})
        g["n"] += 1
        g["impressions"] += int(p.get("impressions") or 0)
        g["comments"] += float(p.get("comments") or 0)
        g["visits"] += int(p.get("visits") or 0)
    out = []
    for g in groups.values():
        g["commentsPer1k"], g["visitsPer1k"] = per_k(g["comments"], g["impressions"]), per_k(g["visits"], g["impressions"])
        out.append(g)
    return sorted(out, key=lambda g: (-g["n"], g["pattern"]))


def pattern_window(posted: dict, now: datetime | None = None, days: int = 14) -> list[dict]:
    """直近 days 日の型ごとの本数・表示・読者のコメント（記録だけ。訪問は日ごとの報告で見る）。2週間で各型5本が目安"""
    now = now or datetime.now(JST)
    posts = []
    for rec in posted.values():
        when, sent = sent_time(rec, now)
        if not rec.get("questionPattern") or not when or not sent or when < now - timedelta(days=days):
            continue
        posts.append({"pattern": rec["questionPattern"], "impressions": (rec.get("metrics") or {}).get("views") or 0,
                      "comments": reader_comments(rec), "visits": 0, "pending": False})
    return pattern_breakdown(posts)


def pattern_name(key: str) -> str:
    try:
        import question_patterns  # noqa: PLC0415

        return question_patterns.NAMES.get(key, key)
    except Exception:  # noqa: BLE001
        return key


def threads_posts(data: dict, posted: dict, videos: dict, now: datetime | None = None) -> list[dict]:
    """Threads の投稿ごとの結果。その日に出した投稿と、その日に Threads から訪問があった投稿（前の日の投稿も）。
    その日の予約でまだ出ていない投稿は pending=True（訪問0 には数えない）"""
    date, rows = data["date"], data.get("rows") or []
    visited = {r["page"] for r in rows if r["source"] == "threads"}
    out = []
    for slug, rec in posted.items():
        when, sent = sent_time(rec, now)
        if not when or not (when.date().isoformat() == date or slug in visited):
            continue
        if when.date().isoformat() > date:
            continue
        # 動画の投稿（sns_video）は下の動画の行で数える
        total = views_of(rows, slug, "threads") - views_of(rows, slug, "threads", "sns_video")
        metrics = rec.get("metrics") or {}
        out.append({
            "slug": slug, "at": when, "kind": rec.get("format") or rec.get("style") or "",
            "visits": total, "utm": total - views_of(rows, slug, "threads", ""),
            "impressions": metrics.get("views") or 0,
            "pending": not sent, "before_start": when < INFLOW_START,
            "hook": hook_label(rec), "test": rec.get("slotTest") or "",
            "pattern": rec.get("questionPattern") or "", "comments": reader_comments(rec),
        })
    for slug, rec in videos.items():
        when = jst(rec.get("postedAt") or "")
        variant = rec.get("variant")
        if rec.get("status") != "posted" or variant not in ("short", "full") or not when:
            continue
        hits = views_of(rows, slug, "threads", "sns_video", variant)
        if when.date().isoformat() == date or hits:
            if when.date().isoformat() > date:
                continue
            out.append({"slug": slug, "at": when, "kind": f"動画・{'短い版' if variant == 'short' else '全部の版'}", "visits": hits, "utm": hits,
                        "impressions": 0, "pending": False, "before_start": when < INFLOW_START, "hook": "", "test": "",
                        "pattern": "", "comments": 0.0})
    return sorted(out, key=lambda p: p["at"])


def show(data: dict, posted: dict, videos: dict, top: int = 3) -> None:
    rows = data.get("rows") or []
    print(f"■ {data['date']}（日本時間）記事などに外から来た人: {data['views']}回（推定 {data['visitors']}人）")
    print("  実ブラウザだけ。ロボット・リンクプレビュー・自動ブラウザ・Lighthouse・自社テストは除いています")
    if not data["views"]:
        print("  まだ記録がありません（計測は 2026-11-03 から）")
        return
    print("  流入元: " + " / ".join(f"{LABELS.get(r['source'], r['source'])} {r['views']}（{r['visitors']}人）" for r in data["bySource"]))
    sns_total = sum(r["views"] for r in data["bySource"] if r["source"] in SNS)
    print(f"  SNS 合計: {sns_total}回")

    print(f"  流入元ごとの人気ページ（上位{top}）:")
    for src in [r["source"] for r in data["bySource"]]:
        pages: dict[str, int] = {}
        for r in rows:
            if r["source"] == src:
                pages[r["page"]] = pages.get(r["page"], 0) + r["views"]
        best = sorted(pages.items(), key=lambda kv: -kv[1])[:top]
        print(f"    {LABELS.get(src, src)}: " + "、".join(f"「{title_of(p, 22)}」{n}" for p, n in best))

    camps: dict[tuple[str, str, str], int] = {}
    for r in rows:
        if r.get("campaign") and r["source"] in SNS:
            k = (r["source"], r["campaign"], r.get("content", ""))
            camps[k] = camps.get(k, 0) + r["views"]
    if camps:
        print("  SNS の投稿の型ごと（utm_campaign）: " + "、".join(
            f"{LABELS.get(s, s)}/{c}{'/' + t if t else ''} {n}" for (s, c, t), n in sorted(camps.items(), key=lambda kv: -kv[1])
        ))

    posts = threads_posts(data, posted, videos)
    if posts:
        print("  Threads の投稿ごと（この日の投稿と、この日に訪問があった投稿）:")
        zero = [p for p in posts if not p["visits"] and not p["pending"]]
        pending = [p for p in posts if p["pending"] and not p["visits"]]
        for p in [p for p in posts if p["visits"]]:
            rate = f" / 表示 {p['impressions']} → {p['visits'] / p['impressions']:.1%}" if p["impressions"] else ""
            other = p["visits"] - p["utm"]
            note = f"（うち投稿のリンク {p['utm']}・参照元だけ {other}）" if other else ""
            early = "（計測を始める前に出た投稿。数えたのは計測開始後の分だけ）" if p["before_start"] else ""
            tag = f"/{p['hook']}" if p.get("hook") else ""
            print(f"    {p['at']:%m/%d %H:%M} {p['kind'] or '-'}{tag}「{title_of(p['slug'])}」 訪問 {p['visits']}{note}{rate}{early}")
        if zero:
            mark = lambda p: "※" if p["before_start"] else ""  # noqa: E731
            print(f"    訪問0: {len(zero)}本（" + "、".join(
                f"{mark(p)}{p['at']:%H:%M} {p['kind'] or '-'}{'/' + p['hook'] if p.get('hook') and p['kind'] not in ('poll', 'digest') else ''}"
                f"{'（リンクなし）' if p['kind'] == 'poll' else ''}" for p in zero) + "）")
            if any(p["before_start"] for p in zero):
                print(f"      ※ 計測を始めた {INFLOW_START:%m/%d %H:%M} より前に出た投稿。出てすぐの数時間は数えていないので、0 とは限りません")
        tested = [p for p in posts if p.get("test") and not p["pending"]]
        if tested:
            groups: dict[str, list[dict]] = {}
            for p in tested:
                groups.setdefault(p["test"], []).append(p)
            print("    通常枠のテスト（AI / AI以外。1本あたりの訪問）: " + " / ".join(
                f"{SLOT_TEST_LABELS.get(k, k)} {len(v)}本・訪問{sum(x['visits'] for x in v)}（1本あたり {sum(x['visits'] for x in v) / len(v):.1f}）"
                for k, v in sorted(groups.items())))
        groups = pattern_breakdown(posts)
        if groups:
            fmt = lambda v: "-" if v is None else f"{v}"  # noqa: E731
            print("    問いかけの型ごと（1000表示あたりのコメント・訪問）: " + " / ".join(
                f"{pattern_name(g['pattern'])} {g['n']}本・表示{g['impressions']}・コメント{fmt(g['commentsPer1k'])}・訪問{fmt(g['visitsPer1k'])}"
                for g in groups))
        if pending:
            print(f"    まだ出ていない予約: {len(pending)}本（" + "、".join(f"{p['at']:%H:%M} {p['kind'] or '-'}" for p in pending) + "。時刻は予約の時刻）")

    video: dict[str, dict[str, int]] = {}
    for r in rows:
        variant = video_variant(r.get("campaign", ""), r.get("content", ""))
        if variant:
            video.setdefault(r["page"], {"short": 0, "full": 0})[variant] += r["views"]
    if video:
        s = sum(v["short"] for v in video.values())
        f = sum(v["full"] for v in video.values())
        print(f"  動画投稿から: 短い版 {s}回 / 全部の版 {f}回")
        for page, v in sorted(video.items(), key=lambda kv: -(kv[1]["short"] + kv[1]["full"]))[:top]:
            print(f"    「{title_of(page)}」 短い版 {v['short']} / 全部の版 {v['full']}")
    window = pattern_window(posted)
    if window:
        print("  直近14日の問いかけの型（本数。各型5本以上が目安・1000表示あたりの読者コメント）: " + " / ".join(
            f"{pattern_name(g['pattern'])} {g['n']}本（{'-' if g['commentsPer1k'] is None else g['commentsPer1k']}）" for g in window))
    if data.get("truncated"):
        print("  注意: 集計の上限に達しました。全部は数えられていません。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="YYYY-MM-DD（日本時間）")
    parser.add_argument("--yesterday", action="store_true")
    parser.add_argument("--top", type=int, default=3, help="流入元ごとに出すページ数")
    parser.add_argument("--fresh", action="store_true", help="保存済みの集計を使わず数え直す")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    token = load_token()
    if not token:
        print("[inflow] AFF_STATS_TOKEN が見つかりません（環境変数か .env.local に入れてください）", file=sys.stderr)
        return 1
    day = datetime.now(JST).date()
    if args.yesterday:
        day -= timedelta(days=1)
    if args.date:
        day = datetime.strptime(args.date, "%Y-%m-%d").date()
    try:
        data = fetch(token, day.isoformat(), args.fresh)
    except urllib.error.HTTPError as error:
        print(f"[inflow] 取得に失敗しました: HTTP {error.code}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"[inflow] 取得に失敗しました: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    show(data, load_state("threads_posted.json").get("posted") or {}, load_state("sns_video.json").get("videos") or {}, max(1, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
