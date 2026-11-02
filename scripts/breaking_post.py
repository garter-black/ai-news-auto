#!/usr/bin/env python3
"""
速報の即時投稿。大きなニュース（主要企業の公式発表。記事 JSON の breaking: true）を、
デプロイが終わって記事が開けるようになったらすぐ Threads・X・Facebook に出す。

  auto_run.yml が速報記事を作った回にデプロイを強制し、このスクリプトのワークフロー（breaking_post.yml）を呼ぶ。

  python3 scripts/breaking_post.py --dry-run              # 対象と投稿文を表示するだけ
  python3 scripts/breaking_post.py --dry-run --slug X     # 指定した記事の速報の投稿文を表示（対象の条件を問わない）
  python3 scripts/breaking_post.py                        # THREADS_MODE=live のときだけ実際に投稿
  python3 scripts/breaking_post.py --merge mine.json      # 投稿履歴をいまの state に足す（push の衝突対策）

決まりごと（数字は threads_post.py）:
- 対象は生成から BREAKING_MAX_AGE_MIN 分以内・未投稿の速報記事。それより古ければ通常の予約に回る
- 1日 BREAKING_PER_DAY 本まで（通常の SNS_PER_DAY とは別枠。既定15・最大30の安全上限。本物の速報はすべて出す）
- 深夜（日本時間1〜5時）は出さず、その間に作った速報は朝 6:00 にまとめて出す（見張り番の Worker が 6:00 にこのワークフローを起動。
  1回 MORNING_PER_RUN 本まで、間を空けて出す）
- 記事ページと図解カードが 200 を返すまで最大 --wait 秒待つ。間に合わなければ出さない（通常の予約に回る）
- 出した記事は state/threads_posted.json に breaking: true で記録し、通常の予約で二重に出さない
- 他人の投稿へのコメント・返信は一切しない（返信するのは自分の投稿のリンクだけ）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import threads_post as tp  # noqa: E402

# 1回の実行で出す最大本数（同時に2本来たときだけ。間を空ける）。朝 6:00 の回は深夜の分をまとめて出す
MAX_PER_RUN = 2
MORNING_PER_RUN = 4
GAP_SEC = 180


def log(message: str) -> None:
    print(message, flush=True)


def targets(state: dict, now: datetime) -> list[dict]:
    """即時投稿の対象（新しい順）"""
    found = []
    for path in tp.ARTICLES_DIR.glob("*.json"):
        article = json.loads(path.read_text("utf-8"))
        if not article.get("breaking") or article["slug"] in state["posted"]:
            continue
        age = now - datetime.fromisoformat(article["generatedAt"])
        if age <= timedelta(minutes=tp.BREAKING_MAX_AGE_MIN) or tp.queued_for_morning(article, now):
            found.append(article)
    return sorted(found, key=lambda a: a["generatedAt"], reverse=True)


def is_ok(url: str, image: bool) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SaisokuBreakingBot/1.0"})
        with urllib.request.urlopen(req, timeout=30) as res:
            if res.status != 200:
                return False
            return not image or res.headers.get("Content-Type", "").startswith("image/")
    except (urllib.error.URLError, TimeoutError):
        return False


def wait_live(article: dict, timeout: int, fmt: str = "classic") -> bool:
    """記事ページと添える画像（図解カード、またはカバー・写真）が開けるまで待つ（Buffer が画像を取りに来るため）"""
    page = f"{tp.site_url()}/articles/{article['slug']}"
    card = tp.post_image(article, fmt)
    deadline = time.monotonic() + timeout
    while True:
        if is_ok(page, image=False) and is_ok(card, image=True):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(15)


def merge(mine_path: str) -> int:
    """手元で投稿した記録を、最新の state（他の実行が更新したもの）に足す"""
    mine = json.loads(Path(mine_path).read_text("utf-8"))
    state = tp.load_state()
    added = 0
    for slug, entry in mine.get("posted", {}).items():
        if entry.get("breaking") and slug not in state["posted"]:
            state["posted"][slug] = entry
            added += 1
    tp.save_state(state)
    log(f"[breaking] 投稿履歴に {added} 件を足しました")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--slug", default="", help="対象の条件を問わず、この記事を速報として扱う（確認用）")
    parser.add_argument("--wait", type=int, default=480, help="記事と図解カードの公開を待つ最大秒数")
    parser.add_argument("--merge", default="", help="この投稿履歴ファイルの速報の記録を state に足す")
    args = parser.parse_args()
    if args.merge:
        return merge(args.merge)

    live = os.environ.get("THREADS_MODE") == "live" and not args.dry_run
    key = os.environ.get("BUFFER_API_KEY", "").strip()
    state = tp.load_state()
    now = datetime.now(timezone.utc)

    if args.slug and live:
        log("[breaking] --slug は確認用です（--dry-run と一緒に使う）。臨時の投稿は Post now を使ってください")
        return 1
    if args.slug:
        found =[json.loads((tp.ARTICLES_DIR / f"{args.slug}.json").read_text("utf-8"))]
    else:
        found = targets(state, now)
    if not found:
        log("[breaking] 即時投稿する速報記事はありません")
        return 0
    if not args.slug and not tp.breaking_allowed(state, now):
        log(
            f"[breaking] いまは出しません（深夜、または今日の上限 {tp.BREAKING_PER_DAY} 本に到達: "
            f"{tp.breaking_today(state, now)} 本）。通常の予約に回ります"
        )
        return 0
    room = max(0, tp.BREAKING_PER_DAY - tp.breaking_today(state, now))
    morning = any(tp.queued_for_morning(a, now) for a in found)
    found = found[: min(MORNING_PER_RUN if morning else MAX_PER_RUN, room if not args.slug else 1)]
    if live and not key:
        log("[breaking] BUFFER_API_KEY が無いため投稿できません")
        return 0

    summary = [f"### 速報の即時投稿 {'（投稿）' if live else '（下書き・未投稿）'}", ""]
    for index, article in enumerate(found):
        # 形式の A/B（速報どうしで交互）。下書きでは何本目かを足して交互に見せる
        fmt = tp.choose_format(state, article, breaking=True, offset=0 if live else index)
        classic_text = tp.compose(article, [], breaking=True, recent_questions=tp.recent_voice(state, "questionText"), use_llm=True)
        question = "" if article.get("disaster") else article.get("_question") or ""
        text = tp.compose_hook(article, [], breaking=True) if fmt == "hook_thread" else classic_text
        link = f"{tp.site_url()}/articles/{article['slug']}"
        if fmt == "hook_thread":
            x_preview = f"{text}\n[X 返信]\n{tp.compose_detail_x(article, tp.utm_link(link, 'x', fmt))}"
            detail = tp.compose_detail(article)
        else:
            x_preview, detail = tp.compose_x(article, text), tp.link_reply_text(article)
        image = tp.post_image(article, fmt)
        if fmt == "classic" and not article.get("disaster"):
            # 本文は「【速報】の書き出し／要約すると、2文／問いかけ」で完結（2026-11-02）。編集部の一言は足さない
            comment = ""
            for problem in tp.post_problems(text, article):
                log(f"  [check] {article['slug']}: {problem}")
            # 2026-11-03〜: Threads は本文の最後に記事のリンク（返信は作らない）
            threads_text = tp.with_link(text, article, "threads", fmt)
            detail = "（なし。リンクは本文。X だけ返信にリンク）"
        else:
            # hook_thread と地震・津波: Threads の本文に編集部の一言（地震・津波は付けない）。問いかけは付けない
            comment = tp.voice.editor_comment(article, tp.recent_voice(state, "comment"))
            threads_text = tp.voice.add_voice(text, comment, "", tp.MAX_CHARS)
            comment = comment if comment and comment in threads_text else ""
            question = ""
        question = question if question and question in threads_text else ""
        log(f"--- {article['slug']} [{fmt}] トピック={tp.topic_of(article)} ({len(threads_text)}文字)\n{threads_text}\n[返信]\n{detail}\n[X]\n{x_preview}\n[画像] {image}\n")
        summary += [f"**{article['slug']}** [{fmt}]", "", "```", threads_text, "---", detail, "```", f"画像: {image}", ""]
        if not live:
            continue
        if index:
            time.sleep(GAP_SEC)
        if not wait_live(article, args.wait, fmt):
            log(f"  [skip] {args.wait}秒待っても記事か図解カードが開けません。通常の予約に回します")
            continue
        try:
            result = tp.share_now(key, article, text, fmt=fmt, fb_text=classic_text, threads_text=threads_text)
        except Exception as error:  # noqa: BLE001  失敗しても次の記事・通常の予約は止めない
            log(f"  [error] {error}")
            continue
        state["posted"][article["slug"]] = {
            **result,
            "hook": "breaking",
            "breaking": True,
            "score": tp.score(article),
            "style": "standalone",
            "image": "card" if fmt == "classic" else "photo" if image != f"{tp.site_url()}/cover/{article['slug']}" else "cover",
            "format": fmt,
            **({"hookTextVersion": tp.HOOK_TEXT_VERSION} if fmt == "hook_thread" else {}),
            "topic": tp.topic_of(article),
            "question": bool(question),
            **({"questionText": question} if question else {}),
            **({"comment": comment} if comment else {}),
            "postedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        tp.save_state(state)
        log(f"  [posted] threads {result['id']} / {result.get('channels')}")
    tp.write_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
