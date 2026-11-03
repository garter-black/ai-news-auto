"""
Threads の「勝ちパターン」を学ぶ。

1. Buffer から、投稿ごとの反応（閲覧・いいね・返信・再投稿）を取ってきて、投稿履歴に書き込む
2. 書き出しの型・時間帯・話題（企業）・問いかけで締めたか、ごとに成績を集計する
3. 結果を state/threads_learning.json に保存する。threads_post.py（どの記事を出すか）と
   threads_hooks.py（どの書き出しを勧めるか）がこれを読んで、伸びた型を優先する

Buffer の数値は1日1回更新なので、1日1回動かせば十分。

2026-11-03 オーナー承認その2: 学ぶ目当てを「表示（閲覧）」から「投稿1本あたりの、サイトに来た人数」に変えた。
訪問は自前の計測（cloudflare/inflow.ts → /api/aff/inflow。鍵 AFF_STATS_TOKEN）から、Threads から記事に来た数を投稿ごとに数える。
計測を始める前（2026-11-03 10:02 より前）に出た投稿や、訪問を取れないときは、表示 × （訪問 ÷ 表示 の全体の割合）で置き換える。
訪問の取り直しは3時間ごと（Buffer は呼ばない）。

使い方:
    python3 scripts/threads_learn.py           # 取得して学習
    python3 scripts/threads_learn.py --offline # 取得せず、手元の数値だけで学習し直す
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from threads_post import ARTICLES_DIR, buffer_graphql, buffer_account, load_state, save_state  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LEARNING_PATH = ROOT / "state" / "threads_learning.json"
JST = timezone(timedelta(hours=9))

# 反応の重み。返信と再投稿は「人に広がる」反応なので重くする
WEIGHTS = {"reactions": 1.0, "comments": 3.0, "reposts": 4.0, "quotes": 4.0}
# 少ない投稿数で極端な結論を出さないよう、全体平均に寄せる強さ（投稿何本ぶんか）
PRIOR = 3
COMPANIES = [
    ("OpenAI", r"OpenAI|ChatGPT|GPT|Codex|Sora"),
    ("Google", r"Google|Gemini|DeepMind|NotebookLM"),
    ("Anthropic", r"Anthropic|Claude"),
    ("Microsoft", r"Microsoft|Azure|Copilot|Foundry"),
    ("NVIDIA", r"NVIDIA|GeForce|RTX"),
    ("Meta", r"Meta|Llama"),
    ("DeepSeek", r"DeepSeek"),
]


def fetch_metrics(api_key: str) -> dict[str, dict]:
    """送信済みの Threads・X 投稿の反応（Buffer の投稿ID → 指標）"""
    import buffer_budget

    buffer_budget.set_role("monitor")  # 反応の取得は読むだけ（予約の分の回数を残す）
    result: dict[str, dict] = {}
    # 組織とチャンネルの ID は保存したもの（state/buffer_channels.json）を使う。毎回2回の問い合わせを省く
    for org in buffer_account(api_key, gql=buffer_graphql):
        ids = [c["id"] for c in org["channels"] if str(c.get("service", "")).lower() in ("threads", "twitter")]
        if not ids:
            continue
        cursor = None
        for _ in range(10):  # 最大 500 件
            after = f", after: {json.dumps(cursor)}" if cursor else ""
            data = buffer_graphql(
                api_key,
                f"""query {{ posts(first: 50{after}, input: {{
                    organizationId: {json.dumps(org['id'])},
                    filter: {{ status: [sent], channelIds: {json.dumps(ids)} }}
                }}) {{
                    edges {{ node {{ id sentAt externalLink metricsUpdatedAt metrics {{ type value }} metadata {{ ... on ThreadsPostMetadata {{ threadCount }} }} }} }}
                    pageInfo {{ endCursor hasNextPage }}
                }} }}""",
            )["posts"]
            for edge in data["edges"]:
                node = edge["node"]
                metrics = {m["type"]: m["value"] for m in node.get("metrics") or []}
                result[node["id"]] = {
                    "metrics": metrics,
                    "metricsUpdatedAt": node.get("metricsUpdatedAt"),
                    "sentAt": node.get("sentAt"),
                    "externalLink": node.get("externalLink"),
                }
                thread_count = (node.get("metadata") or {}).get("threadCount")
                if thread_count:
                    # 自分で付けた返信（スレッドの2件目以降）の数。Buffer のコメント数にはこれも入っている
                    result[node["id"]]["ownReplies"] = max(0, int(thread_count) - 1)
            if not data["pageInfo"]["hasNextPage"]:
                break
            cursor = data["pageInfo"]["endCursor"]
    return result


# 2026-11-03 02:02（日本時間）より前の Threads の投稿は、記事のリンクを自分への返信（2/2）に置いていた
LINK_REPLY_UNTIL = datetime(2026, 11, 2, 17, 2, tzinfo=timezone.utc)
# 朝のまとめは 2026-11-03 15:53 まで返信にリンクを置いていた
DIGEST_REPLY_UNTIL = datetime(2026, 11, 3, 6, 53, tzinfo=timezone.utc)
# Buffer の反応の数値は1日1回の更新。送信からこれより短い時刻の数値は、まだ数え終わっていない（ほぼ0）
MATURE_HOURS = 12
# 自前の訪問の計測を始めた時刻（scripts/inflow_report.py と同じ）。これより前に出た投稿は、訪問を全部は数えていない
INFLOW_START = datetime(2026, 11, 3, 1, 2, tzinfo=timezone.utc)
# 出てからこの時間たった投稿の訪問を使う（Threads の投稿は出てから半日〜1日クリックが続く）
VISITS_MATURE_HOURS = 20
# 訪問の数え直しの間隔（Buffer は呼ばない。自前の計測を読むだけ）
VISITS_EVERY_HOURS = 3
# 表示 → 訪問の置き換えの割合（訪問と表示の両方がそろった投稿が少ないうちの既定。10/28〜11/02 の実績で約2%）
DEFAULT_VISIT_RATE = 0.02


def _utc(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def metrics_mature(entry: dict) -> bool:
    """反応の数値が、送信から MATURE_HOURS 時間以上たってから取ったものか。
    送信直後に取った数値（閲覧0など）を学習に入れると、出たばかりの投稿の型が「伸びない」と誤って判断される。
    時刻の記録が無い古い投稿はそのまま使う"""
    sent, updated = _utc(entry.get("sentAt")), _utc(entry.get("metricsUpdatedAt"))
    if not sent or not updated:
        return True
    return updated - sent >= timedelta(hours=MATURE_HOURS)


def own_replies(entry: dict) -> int:
    """自分で付けた返信の数（Buffer のコメント数から引く）。threadCount が取れていればそれ、
    無ければ投稿の形と時刻から（リンクの返信を付けていた時期の投稿は1）"""
    if "ownReplies" in entry:
        return int(entry.get("ownReplies") or 0)
    if entry.get("style") == "poll":
        return 0
    if entry.get("format") == "hook_thread":
        return 1
    sent = _utc(entry.get("sentAt") or entry.get("dueAt") or entry.get("postedAt"))
    if not sent:
        return 0
    until = DIGEST_REPLY_UNTIL if entry.get("style") == "digest" else LINK_REPLY_UNTIL
    return 1 if sent < until else 0


def reader_metrics(entry: dict) -> dict:
    """読者の反応だけにした数値（コメント数から自分の返信を引く）"""
    metrics = dict(entry.get("metrics") or {})
    if "comments" in metrics:
        metrics["comments"] = max(0.0, float(metrics.get("comments") or 0) - own_replies(entry))
    return metrics


def fetch_visits(now: datetime | None = None, days: int = 14) -> dict[str, int]:
    """記事ごとの、Threads から来た訪問の数（自前の計測。計測開始の日から今日まで。鍵が無ければ空）"""
    import inflow_report  # noqa: PLC0415

    token = inflow_report.load_token()
    if not token:
        return {}
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(JST).date()
    start = max(INFLOW_START.astimezone(JST).date(), today - timedelta(days=days))
    visits: dict[str, int] = {}
    day = start
    while day <= today:
        data = inflow_report.fetch(token, day.isoformat())
        for row in data.get("rows") or []:
            if row.get("source") == "threads" and row.get("page"):
                visits[row["page"]] = visits.get(row["page"], 0) + int(row.get("views") or 0)
        day += timedelta(days=1)
    return visits


def inflow_token_present() -> bool:
    import inflow_report  # noqa: PLC0415

    return bool(inflow_report.load_token())


def visits_measurable(entry: dict, now: datetime | None = None) -> bool:
    """この投稿の訪問を、学習の目当てとして使えるか（計測開始後に出て、出てから VISITS_MATURE_HOURS 時間たった）"""
    now = now or datetime.now(timezone.utc)
    sent = _utc(entry.get("sentAt") or entry.get("dueAt") or entry.get("postedAt"))
    return bool(sent and sent >= INFLOW_START and now - sent >= timedelta(hours=VISITS_MATURE_HOURS))


def too_fresh(entry: dict, now: datetime | None = None) -> bool:
    """出てから VISITS_MATURE_HOURS 時間たっていない、計測開始後の投稿（訪問も表示もまだ数え終わっていない）"""
    now = now or datetime.now(timezone.utc)
    sent = _utc(entry.get("sentAt") or entry.get("dueAt") or entry.get("postedAt"))
    return bool(sent and sent >= INFLOW_START and now - sent < timedelta(hours=VISITS_MATURE_HOURS))


def visit_rate(rows: list[dict]) -> float:
    """表示 → 訪問の割合（訪問と表示の両方がそろった投稿から。少なければ既定）"""
    both = [r for r in rows if r.get("visits") is not None and r["views"] > 0]
    views = sum(r["views"] for r in both)
    if len(both) < 3 or views <= 0:
        return DEFAULT_VISIT_RATE
    return max(0.001, sum(r["visits"] for r in both) / views)


def views_of(metrics: dict) -> float:
    for key in ("impressions", "views", "reach"):
        if key in metrics:
            return float(metrics[key])
    return 0.0


def engagement_of(metrics: dict) -> float:
    return sum(float(metrics.get(key, 0)) * weight for key, weight in WEIGHTS.items())


def features(slug: str, entry: dict) -> dict:
    """投稿の特徴（何が効いたかを比べる軸）"""
    article: dict = {}
    path = ARTICLES_DIR / f"{slug}.json"
    if path.exists():
        article = json.loads(path.read_text("utf-8"))
    when = entry.get("sentAt") or entry.get("dueAt") or entry.get("postedAt")
    hour = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(JST).hour if when else None
    slot = "朝" if hour is not None and 5 <= hour < 10 else "昼" if hour is not None and 10 <= hour < 16 else "夜" if hour is not None else "不明"
    text = f"{article.get('title', '')} {' '.join(article.get('tags', []))}"
    company = next((name for name, pattern in COMPANIES if re.search(pattern, text)), "その他")
    post = str(article.get("threadsPost") or "").strip()
    return {
        "hook": entry.get("hook") or article.get("threadsHook") or "不明",
        "slot": slot,
        "company": company,
        "ending": "問いかけ" if post.endswith(("？", "?")) else "完結型" if entry.get("style") == "standalone" else "記事で👇",
        "format": "速報" if entry.get("breaking") else "図解カード+返信リンク" if entry.get("image") == "card"
        else "画像+返信リンク" if entry.get("dueAt") else "リンクカード",
        # 投稿の形式の A/B（hook_thread / classic）。A/B 前の投稿は別枠
        "postFormat": entry.get("format") or "A/B前",
        # 速報と通常で閲覧数の桁が違うので、形式×種類でも比べる
        "postFormatKind": f"{entry.get('format') or 'A/B前'}/{'速報' if entry.get('breaking') else '通常'}",
        # 本文の最後の問いかけ（2026-10-29〜。記録のない投稿は別枠）
        "question": "問いかけあり" if entry.get("question") else "なし" if "question" in entry else "記録前",
        # 1行目の呼びかけの言い回し（2026-11-03〜。threads_hooks.CALLOUTS の id）
        "opener": entry.get("opener") or "記録前",
        # AI の記事か、AI 以外の記事か（通常枠の AI 以外のテスト。slotTest があればその枠の種類）
        "genre": "AI" if (entry.get("genre") or article.get("genreSlug") or "ai") == "ai" else "AI以外",
        "slotTest": entry.get("slotTest") or "なし",
    }


def summarize(rows: list[dict]) -> dict:
    """軸ごとに、平均閲覧・平均反応・総合点（全体平均に寄せた値）を出す"""
    if not rows:
        return {}
    overall_views = sum(r["views"] for r in rows) / len(rows)
    overall_eng = sum(r["engagement"] for r in rows) / len(rows)
    # 目当て: 投稿1本あたりの訪問（無い投稿は 表示 × 割合 で置き換えた値。r["value"]）
    for r in rows:
        r.setdefault("value", r["views"] * DEFAULT_VISIT_RATE)
    overall_value = sum(r["value"] for r in rows) / len(rows)
    out: dict[str, dict] = {}
    for axis in ("hook", "slot", "company", "ending", "format", "postFormat", "postFormatKind", "question", "opener", "genre", "slotTest"):
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(row["features"][axis], []).append(row)
        table = {}
        for key, items in groups.items():
            n = len(items)
            views = sum(i["views"] for i in items)
            eng = sum(i["engagement"] for i in items)
            value = sum(i["value"] for i in items)
            table[key] = {
                "n": n,
                "avgViews": round(views / n, 1),
                "avgEngagement": round(eng / n, 2),
                "avgComments": round(sum(i.get("comments", 0) for i in items) / n, 2),
                # 1本あたりの訪問（計測前の投稿は表示から置き換えた値）と、実際に訪問を数えた本数
                "avgVisits": round(value / n, 2),
                "nVisits": sum(1 for i in items if i.get("visits") is not None),
                # 目当ては訪問。本数が少ない型ほど全体平均に寄せる（たまたまの1本で決めつけない）。反応は同点のときの差くらい
                "score": round(100 * ((value + PRIOR * overall_value) / (n + PRIOR))
                               + ((eng + PRIOR * overall_eng) / (n + PRIOR)), 2),
            }
        out[axis] = dict(sorted(table.items(), key=lambda kv: -kv[1]["score"]))
    return out


def learning_sample(entry: dict) -> bool:
    """形式比較を崩さないよう、別企画と時刻を変えた動作検証投稿を除く。"""
    return entry.get("style") not in ("digest", "poll") and not (entry.get("verification") or {}).get("excludeFromLearning")


# Buffer の反応の数値は1日1回しか更新されないので、取りに行くのも1日1回（2026-11-03 に毎回の取得で上限に当たった）
FETCH_EVERY_HOURS = 20


def fetched_recently(now: datetime | None = None) -> str:
    """前回 Buffer から反応を取れた時刻が FETCH_EVERY_HOURS 時間以内なら、その時刻（ISO）。でなければ空"""
    now = now or datetime.now(timezone.utc)
    try:
        at = json.loads(LEARNING_PATH.read_text("utf-8")).get("metricsFetchedAt") or ""
        if at and now - datetime.fromisoformat(at) < timedelta(hours=FETCH_EVERY_HOURS):
            return at
    except (OSError, ValueError, TypeError):
        pass
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--force", action="store_true", help="前回の取得から20時間たっていなくても Buffer から取る")
    args = parser.parse_args()

    state = load_state()
    previous = ""
    try:
        previous = json.loads(LEARNING_PATH.read_text("utf-8")).get("metricsFetchedAt") or ""
    except (OSError, ValueError):
        pass
    fetched_at = previous
    recent = "" if args.force else fetched_recently()
    visits_at = ""
    try:
        visits_at = json.loads(LEARNING_PATH.read_text("utf-8")).get("visitsFetchedAt") or ""
    except (OSError, ValueError):
        pass
    now = datetime.now(timezone.utc)
    visits_due = args.force or not visits_at or now - datetime.fromisoformat(visits_at) >= timedelta(hours=VISITS_EVERY_HOURS)
    if recent and not args.offline and not visits_due:
        # 数値は前回から変わっていないので、学び直しも書き直しもしない（Buffer も呼ばない）
        print(f"[learn] 反応は {recent} に取得済み（1日1回）・訪問も {visits_at} に取得済み。今回は学習もそのまま")
        return 0
    if not args.offline and visits_due:
        try:
            visits = fetch_visits(now)
        except Exception as error:  # noqa: BLE001  訪問を取れなくても、表示で学ぶ
            print(f"[learn] 訪問の数を取れませんでした: {type(error).__name__}: {str(error)[:120]}", file=sys.stderr)
            visits = None
        if visits is not None and (visits or inflow_token_present()):
            visits_at = now.isoformat(timespec="seconds")
            changed = 0
            for slug, entry in state["posted"].items():
                if visits_measurable(entry, now) and entry.get("visits") != visits.get(slug, 0):
                    entry["visits"] = visits.get(slug, 0)
                    changed += 1
            print(f"[learn] 訪問の数を取り込みました（{len(visits)}記事・投稿の記録を{changed}件更新）")
            if changed:
                save_state(state)
    if not args.offline and not recent:
        key = os.environ.get("BUFFER_API_KEY", "").strip()
        if not key:
            print("[learn] BUFFER_API_KEY が無いので、手元の数値だけで学習します")
        else:
            try:
                fetched = fetch_metrics(key)
            except Exception as error:  # 学習の失敗で投稿を止めない
                print(f"[learn] 反応の取得に失敗しました: {error}", file=sys.stderr)
                fetched = {}
            if fetched:
                fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            updated = 0
            for entry in state["posted"].values():
                info = fetched.get(entry.get("id", ""))
                if info and info["metrics"]:
                    entry.update(info)
                    updated += 1
                # X の反応は xMetrics に分けて持つ（Buffer が X の数値を返すときだけ）
                x_info = fetched.get((entry.get("channels") or {}).get("twitter", ""))
                if x_info and x_info["metrics"]:
                    entry["xMetrics"] = x_info["metrics"]
            print(f"[learn] {len(fetched)}件の投稿の反応を取得、{updated}件を履歴に反映")
            save_state(state)

    rows = []
    fresh_skipped = 0
    for slug, entry in state["posted"].items():
        if too_fresh(entry, now):
            fresh_skipped += 1  # 出たばかり（訪問も表示もまだ数え終わっていない）
            continue
        if entry.get("visits") is not None and visits_measurable(entry, now) and learning_sample(entry):
            # 訪問を数えた投稿は、Buffer の数値がまだでも学習に入れる（目当ては訪問）
            metrics = reader_metrics(entry) if entry.get("metrics") and metrics_mature(entry) else {}
            rows.append({
                "slug": slug, "views": views_of(metrics), "engagement": engagement_of(metrics),
                "comments": float(metrics.get("comments", 0) or 0), "visits": int(entry["visits"]),
                "features": features(slug, entry),
            })
            continue
        # 朝のまとめ・問いかけ・時刻を変えた動作検証投稿は型の学習には入れない。
        # 送信直後に取った（まだ数え終わっていない）数値も入れない。コメントは読者の分だけ（自分のリンクの返信を引く）
        if not entry.get("metrics") or not learning_sample(entry) or not metrics_mature(entry):
            continue
        metrics = reader_metrics(entry)
        rows.append({
            "slug": slug,
            "views": views_of(metrics),
            "engagement": engagement_of(metrics),
            "comments": float(metrics.get("comments", 0) or 0),
            "features": features(slug, entry),
        })
    rate = visit_rate(rows)
    for r in rows:
        r["value"] = float(r["visits"]) if r.get("visits") is not None else r["views"] * rate
    x_rows = [
        {"slug": slug, "views": views_of(e["xMetrics"]), "engagement": engagement_of(e["xMetrics"]), "features": features(slug, e)}
        for slug, e in state["posted"].items() if e.get("xMetrics") and learning_sample(e)
    ]
    learning = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **({"metricsFetchedAt": fetched_at} if fetched_at else {}),
        **({"visitsFetchedAt": visits_at} if visits_at else {}),
        # 目当て（2026-11-03〜）: 投稿1本あたりの訪問。visitRate は 表示 → 訪問 の置き換えの割合
        "target": "visits",
        "visitRate": round(rate, 4),
        "postsWithVisits": sum(1 for r in rows if r.get("visits") is not None),
        "freshSkipped": fresh_skipped,
        "posts": len(rows),
        "axes": summarize(rows),
        # X の成績（形式の比較用。hook などの学習には使わない）
        "xPosts": len(x_rows),
        "xAxes": {k: v for k, v in summarize(x_rows).items() if k in ("postFormat", "postFormatKind")},
        "top": sorted(
            ({"slug": r["slug"], "visits": r.get("visits"), "value": round(r["value"], 2), "views": r["views"], "engagement": r["engagement"], **r["features"]} for r in rows),
            key=lambda r: -(r["value"] * 100 + r["engagement"]),
        )[:10],
    }
    LEARNING_PATH.write_text(json.dumps(learning, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    hooks = learning["axes"].get("hook", {})
    print(f"[learn] 学習に使えた投稿: {len(rows)}本")
    print(f"[learn] 目当ては訪問。訪問を数えた投稿 {learning['postsWithVisits']}本・表示→訪問の割合 {rate:.1%}（ほかの投稿はこれで置き換え）")
    for key, value in list(hooks.items())[:5]:
        print(f"  {key}: {value['n']}本 1本あたりの訪問{value['avgVisits']} 平均閲覧{value['avgViews']} 平均反応{value['avgEngagement']}")
    # 形式の A/B の比較（平均閲覧）
    for label, axes in (("Threads", learning["axes"]), ("X", learning["xAxes"])):
        for key, value in (axes.get("postFormatKind") or {}).items():
            print(f"  [形式 {label}] {key}: {value['n']}本 平均閲覧{value['avgViews']} 平均反応{value['avgEngagement']}")
    for key, value in (learning["axes"].get("question") or {}).items():
        print(f"  [問いかけ] {key}: {value['n']}本 平均閲覧{value['avgViews']} 平均返信{value['avgComments']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
