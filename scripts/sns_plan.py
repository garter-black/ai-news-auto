#!/usr/bin/env python3
"""SNS の伸ばし方の計画を1か所で持つ（2026-11-05 とろいも案 v1。台帳 S27〜S31）。設定は scripts/sns_plan.json。

すべての投稿の経路（threads_post・breaking_post・bluesky_post・morning_digest・sns_video_followup・門番 sns_rules）が
ここを読む。経路ごとに別の決まりを書かない（O13）。

  lane_of(article)        → (レーン名, 点数)。safety（障害・流出・安全）> money（お金・制度・政府）> brand（有名ブランド）> consumer_ai
  excluded(article)       → SNS に出さない理由（海外の企業向け・開発者向け。サイトには載せる）。出してよければ ""
  sns_score(article)      → 並べ順の点数（レーン＋ Muse の動画待ち）
  slots_jst()             → 通常の投稿の時刻 [(時, 分), ...]
  daily_cap() / cap_exempt(article_or_entry)  → 1日の上限と、上限に数えない種類（ミサイル・災害）
  count_today(posted, day, extra) → その日（日本時間）に出した（予約した）自分の投稿の数
  utm_content(ptype, pos) → "video_body" など
  hook_format_on() / link_ab_on()  → オーナー承認待ちの形（既定 off）
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = Path(__file__).resolve().parent / "sns_plan.json"
MUSE_QUEUE = ROOT / "state" / "muse_queue.json"
JST = timezone(timedelta(hours=9))


@lru_cache(maxsize=1)
def plan() -> dict:
    return json.loads(PLAN_PATH.read_text("utf-8"))


@lru_cache(maxsize=None)
def _re(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def _text(article: dict) -> str:
    tags = " ".join(str(t) for t in (article.get("tags") or []) if t)
    return f"{article.get('title', '')} {article.get('seoTitle', '')} {tags}"


def _host(article: dict) -> str:
    url = str(article.get("sourceUrl") or "")
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    return parts.netloc.lower().split(":")[0]


def japanese_source(article: dict) -> bool:
    """出どころが日本の会社・官公庁のサイト（ドメインが .jp）。海外の会社の日本語のページ（/jp/）は海外として扱う"""
    host = _host(article)
    if not host:
        return False
    if _re(plan()["exclude"]["japan_host_re"]).search(host):
        return True
    return any(host == h or host.endswith("." + h) for h in plan()["exclude"].get("japan_hosts") or [])


def lanes(article: dict) -> list[tuple[str, int]]:
    """当たるレーン（点数の高い順）"""
    text = _text(article)
    out = []
    for name, lane in plan()["lanes"].items():
        if not _re(lane["re"]).search(text):
            continue
        if lane.get("needs_re") and not _re(lane["needs_re"]).search(f"{text} {article.get('lede', '')}"):
            continue
        out.append((name, int(lane["score"])))
    return sorted(out, key=lambda x: -x[1])


def lane_of(article: dict) -> tuple[str, int]:
    found = lanes(article)
    return found[0] if found else ("", 0)


def excluded(article: dict | None) -> str:
    """SNS に出さない理由（海外の企業向け・開発者向けの話題）。出してよければ ""。
    災害・ミサイル・急ぎの安全の知らせ（disaster / urgent / safetyAlert）は外さない"""
    if not article or article.get("disaster") or article.get("urgent") or article.get("safetyAlert"):
        return ""
    if article.get("kind") == "digest" or article.get("items"):
        return ""
    ex = plan()["exclude"]
    text = _text(article)
    audience = str((article.get("verdict") or {}).get("audience") or "")
    hit = _re(ex["text_re"]).search(text)
    aud = _re(ex["audience_re"]).search(audience) if audience else None
    if not (hit or aud):
        return ""
    if not aud and japanese_source(article):
        return ""  # 日本の会社・官公庁の発表は外さない（日本の読者に関係する）。読者が開発者・IT 担当の記事は日本の発表でも外す
    if not aud and lane_of(article)[0] == "safety":
        return ""  # 一般の人が使うサービスの障害・流出は外さない
    why = f"「{hit.group(0)}」" if hit else f"読者が「{audience}」"
    return f"海外の企業向け・開発者向けの話題（{why}）は SNS に出さない（サイトだけ）"


def muse_slugs(now: datetime | None = None) -> set[str]:
    """Muse に頼んで、まだ待っている記事（state/muse_queue.json）"""
    data = load_muse_queue()
    return {i["slug"] for i in data.get("items", []) if i.get("status") == "queued"}


def sns_score(article: dict) -> float:
    """SNS に出す順番の点数。レーン → Muse に頼んだ記事 → 新しさは呼ぶ側で"""
    if excluded(article):
        return -100.0
    score = float(lane_of(article)[1])
    if article.get("slug") in muse_slugs():
        score += 2.0
    return score


def has_lane(article: dict) -> bool:
    return bool(lane_of(article)[0])


# ---- 枠と1日の上限 ----

def _hm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)


def slots_jst() -> list[tuple[int, int]]:
    return sorted(_hm(s) for s in plan()["slots_jst"])


def digest_slot_jst() -> tuple[int, int]:
    return _hm(plan().get("digest_slot_jst") or plan()["slots_jst"][0])


def daily_cap() -> int:
    raw = os.environ.get("SNS_DAILY_CAP", "").strip()
    cap = int(raw) if raw.isdigit() else int(plan()["daily_cap"])
    return max(0, min(cap, int(plan()["daily_cap"])))  # 変数で減らすことはできるが、計画より増やさない


def min_gap() -> timedelta:
    return timedelta(minutes=int(plan()["min_gap_min"]))


def cap_exempt(record: dict | None) -> bool:
    """1日の上限に数えない（ミサイル・災害の知らせ）。記事でも投稿の記録でもよい"""
    record = record or {}
    if record.get("disaster") or record.get("missile") or record.get("hook") == "disaster":
        return True
    kind = str(record.get("publicInterest") or record.get("publicInterestKind") or "")
    if not kind and record.get("title"):
        import public_interest  # noqa: PLC0415 - 記事の題名からミサイル・災害かを見る（同じ判定を1か所で）

        kind = public_interest.category(str(record.get("title") or "")) or ""
    return kind in plan()["cap_exempt"]


def _when(rec: dict) -> datetime | None:
    for key in ("dueAt", "postedAt", "sentAt"):
        raw = rec.get(key)
        if not raw:
            continue
        try:
            when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    pending = (rec.get("pending") or {}).get("slot")
    if pending:
        try:
            return datetime.fromisoformat(pending)
        except ValueError:
            return None
    return None


def count_today(posted: dict, day: date, skipped: set[str] | None = None) -> int:
    """その日（日本時間）に出した・予約した自分の投稿の数（Threads の記録。X・Facebook は同じ記事を同じ日に出す）。
    動画が作れず出さなかったもの（skipped）とミサイル・災害は数えない"""
    skipped = skipped or set()
    n = 0
    for slug, rec in (posted or {}).items():
        if slug in skipped or cap_exempt(rec):
            continue
        if not (rec.get("id") or rec.get("status") == "awaiting_video" or rec.get("style") == "digest"):
            continue
        when = _when(rec)
        if when and when.astimezone(JST).date() == day:
            n += 1
    return n


def video_skipped(path: Path | None = None) -> set[str]:
    """動画が作れず出さなかった記事（state/sns_video.json の queued が skipped）。1日の上限に数えない"""
    path = path or ROOT / "state" / "sns_video.json"
    try:
        data = json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return set()
    return {k for k, v in (data.get("queued") or {}).items() if (v or {}).get("status") == "skipped"}


def cap_left(posted: dict, day: date, skipped: set[str] | None = None) -> int:
    return max(0, daily_cap() - count_today(posted, day, skipped))


# ---- UTM ----

def utm_content(ptype: str, pos: str) -> str:
    u = plan()["utm"]
    ptype = ptype if ptype in u["types"] else "video"
    pos = pos if pos in u["positions"] else "body"
    return f"{ptype}_{pos}"


UTM_CONTENT_RE = re.compile(r"^(?:video|hook|card|digest)_(?:body|reply)$")


# ---- オーナー承認待ちの形（既定 off） ----

def _flag(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in ("1", "on", "true", "yes")


def hook_format_on() -> bool:
    """Muse の型の文面（1行目＝結論＋数字・2行目＝なぜ大事か・3行目＝記事にだけあるもの）。S1 を変えるのでオーナー承認まで off"""
    return _flag("SNS_HOOK_FORMAT")


def link_ab_on() -> bool:
    """Threads のリンクの位置の比べ（本文／返信を交互）。オーナー承認まで off"""
    return _flag("THREADS_LINK_AB")


def threads_link_position(posted: dict) -> str:
    """THREADS_LINK_AB=1 のとき、記事の投稿ごとに body と reply を交互にする（off なら body）"""
    if not link_ab_on():
        return "body"
    n = sum(1 for r in (posted or {}).values() if r.get("linkPosition") in ("body", "reply"))
    return "reply" if n % 2 else "body"


# ---- Muse の受け渡しの記録 ----

def load_muse_queue(path: Path | None = None) -> dict:
    path = path or MUSE_QUEUE
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return {"items": []}


def muse_item(slug: str, data: dict | None = None) -> dict:
    data = data if data is not None else load_muse_queue()
    return next((i for i in data.get("items", []) if i.get("slug") == slug), {})


def muse_wait_until(slug: str, slot: datetime, breaking: bool = False, data: dict | None = None) -> datetime | None:
    """この記事の投稿が Muse の動画を待つ期限（頼んでいなければ None）。速報は breaking_wait_hours（既定0＝待たない）"""
    item = muse_item(slug, data)
    if item.get("status") != "queued":
        return None
    m = plan()["muse"]
    hours = float(os.environ.get("MUSE_WAIT_HOURS") or m["wait_hours"])
    if breaking:
        hours = float(m["breaking_wait_hours"])
    if hours <= 0:
        return None
    try:
        queued = datetime.fromisoformat(item.get("queuedAt") or "")
    except ValueError:
        queued = slot
    return max(slot, queued) + timedelta(hours=hours)
