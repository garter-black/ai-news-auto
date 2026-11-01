#!/usr/bin/env python3
"""Alert on repeated *observed* zero-candidate pipeline runs; never generate articles."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone

TITLE = "【自動通知】通常記事の候補が長時間0件です"
WINDOW = timedelta(hours=3)
LINE = re.compile(r"\[info\] 大きなニュース (\d+) 件.*?候補 AI (\d+) 件.*?幅広い話題 (\d+) 件")
TREND = re.compile(r"\[info\] 話題キュー (\d+) 件")


def counts(log: str):
    match, trend = LINE.search(log), TREND.search(log)
    if not match or not trend:
        return None
    return tuple(map(int, match.groups())) + (int(trend.group(1)),)


def assess(samples, now):
    """Only alarm when enough valid observations span most of the window."""
    recent = [(dt, values) for dt, values in samples if now - WINDOW <= dt <= now]
    if len(recent) < 5:
        return "unknown"
    times = [dt for dt, _ in recent]
    if now - max(times) > timedelta(minutes=55) or now - min(times) < timedelta(hours=2, minutes=20):
        return "unknown"
    return "zero" if all(sum(v) == 0 for _, v in recent) else "nonzero"


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=90).stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    runs = json.loads(gh("run", "list", "--workflow", "auto_run.yml", "--limit", "30", "--json", "databaseId,displayTitle,createdAt,status,conclusion"))
    samples, missing = [], 0
    for run in runs:
        at = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
        if at < now - WINDOW or at > now or run["status"] != "completed" or run["displayTitle"].startswith("速報レーン"):
            continue
        try:
            values = counts(gh("run", "view", str(run["databaseId"]), "--log"))
        except (subprocess.SubprocessError, OSError):
            values = None
        if values is None:
            missing += 1
        else:
            samples.append((at, values))
    status = assess(samples, now)
    # Missing logs can conceal nonzero candidate runs, so never infer zero from incomplete evidence.
    if missing and status == "zero":
        status = "unknown"
    print(f"[candidate-health] status={status}, observations={len(samples)}, missing={missing}, window=3h")
    if status != "zero":
        return 0
    body = (f"直近3時間の通常記事の実行 {len(samples)} 回で、公式・幅広い話題・トレンドの候補がすべて0件でした。\n\n"
            "休日など実際に発表が少ない場合もあります。取得エラー、日付、処理済み・判定条件を確認してください。"
            "記事数やアクセスの停止を断定する通知ではありません。")
    if args.dry_run:
        print("[candidate-health] dry-run: would check for an existing Issue")
        return 0
    issues = json.loads(gh("issue", "list", "--state", "open", "--limit", "100", "--json", "title"))
    if any(i["title"] == TITLE for i in issues):
        print("[candidate-health] matching Issue already open")
    else:
        gh("issue", "create", "--title", TITLE, "--body", body)
        print("[candidate-health] Issue created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
