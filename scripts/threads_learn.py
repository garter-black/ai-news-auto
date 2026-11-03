"""
Threads の「勝ちパターン」を学ぶ。

1. Buffer から、投稿ごとの反応（閲覧・いいね・返信・再投稿）を取ってきて、投稿履歴に書き込む
2. 書き出しの型・時間帯・話題（企業）・問いかけで締めたか、ごとに成績を集計する
3. 結果を state/threads_learning.json に保存する。threads_post.py（どの記事を出すか）と
   threads_hooks.py（どの書き出しを勧めるか）がこれを読んで、伸びた型を優先する

Buffer の数値は1日1回更新なので、1日1回動かせば十分。

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
                    edges {{ node {{ id sentAt externalLink metricsUpdatedAt metrics {{ type value }} }} }}
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
            if not data["pageInfo"]["hasNextPage"]:
                break
            cursor = data["pageInfo"]["endCursor"]
    return result


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
    }


def summarize(rows: list[dict]) -> dict:
    """軸ごとに、平均閲覧・平均反応・総合点（全体平均に寄せた値）を出す"""
    if not rows:
        return {}
    overall_views = sum(r["views"] for r in rows) / len(rows)
    overall_eng = sum(r["engagement"] for r in rows) / len(rows)
    out: dict[str, dict] = {}
    for axis in ("hook", "slot", "company", "ending", "format", "postFormat", "postFormatKind", "question"):
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(row["features"][axis], []).append(row)
        table = {}
        for key, items in groups.items():
            n = len(items)
            views = sum(i["views"] for i in items)
            eng = sum(i["engagement"] for i in items)
            table[key] = {
                "n": n,
                "avgViews": round(views / n, 1),
                "avgEngagement": round(eng / n, 2),
                "avgComments": round(sum(i.get("comments", 0) for i in items) / n, 2),
                # 本数が少ない型ほど全体平均に寄せる（たまたまの1本で決めつけない）
                "score": round(((views + PRIOR * overall_views) / (n + PRIOR))
                               + 10 * ((eng + PRIOR * overall_eng) / (n + PRIOR)), 2),
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
    if recent and not args.offline:
        # 数値は前回から変わっていないので、学び直しも書き直しもしない（Buffer も呼ばない）
        print(f"[learn] 反応は {recent} に取得済み（1日1回）。今回は Buffer を呼ばず、学習もそのまま")
        return 0
    if not args.offline:
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
    for slug, entry in state["posted"].items():
        metrics = entry.get("metrics")
        # 朝のまとめ・問いかけ・時刻を変えた動作検証投稿は型の学習には入れない
        if not metrics or not learning_sample(entry):
            continue
        rows.append({
            "slug": slug,
            "views": views_of(metrics),
            "engagement": engagement_of(metrics),
            "comments": float(metrics.get("comments", 0) or 0),
            "features": features(slug, entry),
        })
    x_rows = [
        {"slug": slug, "views": views_of(e["xMetrics"]), "engagement": engagement_of(e["xMetrics"]), "features": features(slug, e)}
        for slug, e in state["posted"].items() if e.get("xMetrics") and learning_sample(e)
    ]
    learning = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **({"metricsFetchedAt": fetched_at} if fetched_at else {}),
        "posts": len(rows),
        "axes": summarize(rows),
        # X の成績（形式の比較用。hook などの学習には使わない）
        "xPosts": len(x_rows),
        "xAxes": {k: v for k, v in summarize(x_rows).items() if k in ("postFormat", "postFormatKind")},
        "top": sorted(
            ({"slug": r["slug"], "views": r["views"], "engagement": r["engagement"], **r["features"]} for r in rows),
            key=lambda r: -(r["views"] + 10 * r["engagement"]),
        )[:10],
    }
    LEARNING_PATH.write_text(json.dumps(learning, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    hooks = learning["axes"].get("hook", {})
    print(f"[learn] 学習に使えた投稿: {len(rows)}本")
    for key, value in list(hooks.items())[:5]:
        print(f"  {key}: {value['n']}本 平均閲覧{value['avgViews']} 平均反応{value['avgEngagement']}")
    # 形式の A/B の比較（平均閲覧）
    for label, axes in (("Threads", learning["axes"]), ("X", learning["xAxes"])):
        for key, value in (axes.get("postFormatKind") or {}).items():
            print(f"  [形式 {label}] {key}: {value['n']}本 平均閲覧{value['avgViews']} 平均反応{value['avgEngagement']}")
    for key, value in (learning["axes"].get("question") or {}).items():
        print(f"  [問いかけ] {key}: {value['n']}本 平均閲覧{value['avgViews']} 平均返信{value['avgComments']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
