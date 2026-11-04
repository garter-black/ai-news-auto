#!/usr/bin/env python3
"""価値の低い記事を取り下げる（2026-11-03 オーナー承認。AdSense「有用性の低いコンテンツ」への対策）。

取り下げた記事は content/retired.json に残し、JSON は消す。サイトは次のように返す（cloudflare/worker.ts）:
  - 重複記事（同じ発表の代表でない記事）… 代表の記事へ 301 転送
  - それ以外 … 410 Gone（「この記事は取り下げました」と新着・トップへのリンク）
一覧・サイトマップ・検索・フィード・関連記事は content/articles から作るので、JSON を消せば自然に外れる。
ほかのデータ（記事の関連・まとめ記事の元記事・本文のリンク・製品まとめ・朝のまとめ・週のまとめ・料金表など）に
残る参照は、転送先があれば付け替え、なければ外す（リンク切れを残さない）。

対象（区分）:
  creator-video     個人・他媒体の YouTube 動画の要約（scripts/indexnow.py の CREATOR_CHANNELS）
  creator-roundup   上の動画の要約だけを束ねたまとめ記事（元記事が残らない・2本未満になる）
  wire              プレスリリース配信の言い換え（WIRE_SOURCES）
  thin              本文1,500字未満で、表・よくある質問・図・比較のどれもない記事（地震・津波の型の記事は除く）
  duplicate         同じ発表の重複記事のうち代表でないもの（scripts/story.py が duplicateOf を付けたものだけ。代表へ転送）
直近7日に SNS（Threads）へ出した記事は、投稿のリンクが生きているので取り下げない（一覧に別で出す）。

  python3 scripts/retire_articles.py                 # 対象を数えるだけ（何も変えない）
  python3 scripts/retire_articles.py --report out.md # 対象の一覧を Markdown に書く
  python3 scripts/retire_articles.py --apply         # 取り下げる

追加の取り下げ（2026-11-03 オーナー承認）: --noindex で、検索に出していない（noindex の）記事をすべて取り下げる。
  区分は scripts/indexnow.py の noindex_reason（off-focus 主題外 / no-japan-value 日本の読者に役立つ情報がない / thin 薄い /
  duplicate 重複 / creator-video 他人の動画の要約 / wire 配信の言い換え）。
  残すもの: 直近7日に SNS（Threads・Bluesky・SNS 動画）に出した記事、地震・津波の速報（disaster。短いのは設計どおり）、
  手で「検索に出さない」と決めた記事（indexable: false）。
  転送: 重複は代表の記事へ。それ以外は REPRESENTATIVES（手で確かめた、はっきり近い記事）があれば 301、無ければ 410。
  まとめ記事の元記事がすべて取り下げになり、そのまとめ自身が転送先なら、まとめは残して元記事の一覧をリンクなしにする。

  python3 scripts/retire_articles.py --noindex                 # 数えるだけ
  python3 scripts/retire_articles.py --noindex --apply         # 取り下げる
  python3 scripts/retire_articles.py --noindex --append out.md # 一覧を Markdown の末尾に足す

お知らせ・統計の転載の取り下げ（2026-11-03 オーナー決定・台帳 A26。2026-11-04 D5 で毎週の自動実行 notice_retire.yml）: --notice で、発表をそのまま書き写しただけの記事
（認可・受章者の一覧、議事要旨、決算発表日の予告、毎年の啓発・手続きのお知らせ、統計・実績の数字の転載。判定は scripts/notice_gate.py）を取り下げる。
  最後に SNS（Threads・Bluesky・SNS 動画）に出してから7日、出していなければ公開から7日たつまでは残し、一覧に別で出す。転送先は無いので 410。
  これからは公開前の門（scripts/indexnow.py の publish_block_reason → "notice"）で作らない。

  python3 scripts/retire_articles.py --notice                    # 数えるだけ

他人の動画の要約の取り下げ（2026-11-04 オーナー決定 D3・台帳 A10）: --creator で、発信元が個人・他媒体の YouTube（CREATOR_CHANNELS）の
記事をすべて取り下げる（検索に出していなくても読める状態で残さない。SNS に出した記事も残さない。410）。
  python3 scripts/retire_articles.py --creator --apply
  python3 scripts/retire_articles.py --notice --apply --append out.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import indexnow  # noqa: E402
import notice_gate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = ROOT / "content" / "articles"
RETIRED = ROOT / "content" / "retired.json"
THREADS = ROOT / "state" / "threads_posted.json"
BLUESKY = ROOT / "state" / "bluesky_extra.json"
SNS_VIDEO = ROOT / "state" / "sns_video.json"
JST = timezone(timedelta(hours=9))
CATEGORIES = ("creator-video", "creator-roundup", "wire", "thin", "duplicate")
LABELS = {
    "creator-video": "他人の動画の要約",
    "creator-roundup": "他人の動画の要約だけのまとめ",
    "wire": "プレスリリースの言い換え",
    "thin": "薄い記事（1,500字未満・表やよくある質問なし）",
    "duplicate": "同じ発表の重複（代表の記事へ転送）",
    "off-focus": "サイトの主題外（AI・お金・制度のお金の話・日本のスポーツのどれでもない）",
    "no-japan-value": "日本の読者に役立つ情報がない（使えるかの判定・料金・使い方などの章がない）",
}
NOINDEX_CATEGORIES = ("off-focus", "no-japan-value", "thin", "duplicate", "creator-video", "wire")
NOINDEX_LABELS = {
    **LABELS,
    "thin": f"薄い記事（本文{indexnow.THIN_BODY:,}字未満）",
    "creator-video": "他人の動画の要約",
    "wire": "プレスリリースの言い換え",
    "roundup-of-retired": "元記事がすべて取り下げになったまとめ",
}
NOTICE_LABELS = {f"notice-{k}": v for k, v in notice_gate.LABELS.items()}
# 重複ではないが、はっきり近い記事があるもの（2026-11-03 に題名・本文を見て決めた）。転送先が残っていなければ 410
REPRESENTATIVES = {
    # JPCERT/CC の週報が PHP の脆弱性の注意喚起を含む
    "php-vulnerabilities-jpcert-alert-2026": "jpcert-weekly-report-20261028-vulnerabilities",
    # 同じ「dバリューパス」の特典追加の記事
    "docomo-d-valuepass-kusuriya": "docomo-d-value-pass-uber-luup-2026",
    # 楽天証券のつなぎ売りの解説3本 → その3本を1本にまとめた記事
    "rakuten-sec-cross-trade-genwatashi": "gyakuhibu-cross-trade-complete-guide-matome",
    "understanding-gyakuhibu-margin-trading-risks": "gyakuhibu-cross-trade-complete-guide-matome",
    "rakuten-sec-cross-trade-margin-category": "gyakuhibu-cross-trade-complete-guide-matome",
}
SNS_KEEP_DAYS = 7
# 参照を書き換えるデータ（記事以外）。状態の記録（content/state.json・llm_usage など）は画面に出ないので触らない
DATA_FILES = [
    "content/events.json",
    "content/weekly.json",
    "content/pricing.json",
]
DATA_DIRS = ["content/products", "content/digests", "content/buying-guides", "content/howto", "content/guides"]
TABLE = re.compile(r"^\|.*\|\s*$", re.M)


def load_articles() -> list[dict]:
    out = []
    for path in sorted(ARTICLES.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _when(value) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def sns_posts(everywhere: bool = False) -> dict[str, datetime]:
    """記事ごとの最後に SNS へ出した時刻。everywhere なら Threads に加えて Bluesky・SNS 動画も見る"""
    rows = [(slug, item.get("postedAt")) for slug, item in (_read(THREADS).get("posted") or {}).items() if isinstance(item, dict)]
    if everywhere:
        rows += [(slug, item.get("at")) for slug, item in _read(BLUESKY).items() if isinstance(item, dict)]
        rows += [
            (slug, item.get("postedAt"))
            for slug, item in (_read(SNS_VIDEO).get("videos") or {}).items()
            if isinstance(item, dict) and item.get("status") == "posted"
        ]
    last: dict[str, datetime] = {}
    for slug, value in rows:
        if (at := _when(value)) and (slug not in last or at > last[slug]):
            last[slug] = at
    return last


def recent_sns(now: datetime, everywhere: bool = False) -> set[str]:
    """直近 SNS_KEEP_DAYS 日に SNS へ出した記事。everywhere なら Threads に加えて Bluesky・SNS 動画も見る"""
    return {slug for slug, at in sns_posts(everywhere).items() if now - at < timedelta(days=SNS_KEEP_DAYS)}


def notice_anchor(article: dict, posts: dict[str, datetime]) -> datetime | None:
    """お知らせ型の記事の「7日」を数え始める時刻（2026-11-04 オーナー決定 D5）: 最後に SNS に出した時刻。
    SNS に出していなければ公開した時刻"""
    return posts.get(article.get("slug", "")) or _when(article.get("publishedAt") or article.get("generatedAt"))


def notice_due(article: dict, posts: dict[str, datetime], now: datetime, days: float = SNS_KEEP_DAYS) -> bool:
    """お知らせ型の記事が取り下げの時期か（SNS に出してから、出していなければ公開から days 日を過ぎた）"""
    anchor = notice_anchor(article, posts)
    return anchor is None or now - anchor >= timedelta(days=days)


def is_thin(article: dict) -> bool:
    """はっきり価値が足りない短い記事だけ（迷うものは残す）"""
    body = article.get("body", "")
    if len(body) >= indexnow.THIN_BODY or article.get("type") == "roundup" or article.get("disaster"):
        return False
    return not (TABLE.search(body) or article.get("faq") or article.get("charts") or article.get("rivals"))


def plan(articles: list[dict], now: datetime) -> tuple[dict[str, dict], list[dict]]:
    """取り下げる記事 {slug: {reason, title, redirect?}} と、SNS に出したので残す記事"""
    by_slug = {a["slug"]: a for a in articles}
    sns = recent_sns(now)
    picked: dict[str, dict] = {}
    kept: list[dict] = []

    def take(a: dict, reason: str, redirect: str = "") -> None:
        if a["slug"] in sns:
            kept.append({"slug": a["slug"], "title": a.get("title", ""), "reason": reason})
            return
        entry = {"reason": reason, "title": a.get("title", ""), "channel": a.get("channelSlug", "")}
        if redirect:
            entry["redirect"] = redirect
        for key in ("sourceUrl", "videoUrl"):
            if a.get(key):
                entry[key] = a[key]
        picked[a["slug"]] = entry

    for a in articles:
        if isinstance(a.get("indexable"), bool) and a["indexable"]:
            continue  # 手で「検索に出す」と決めた記事は触らない
        if a.get("type") == "roundup":
            continue
        channel = a.get("channelSlug")
        if channel in indexnow.CREATOR_CHANNELS:
            take(a, "creator-video")
        elif channel in indexnow.WIRE_SOURCES:
            take(a, "wire")
        elif a.get("duplicateOf") and a["duplicateOf"] != a["slug"] and a["duplicateOf"] in by_slug:
            take(a, "duplicate", a["duplicateOf"])
        elif is_thin(a):
            take(a, "thin")

    # 転送先が取り下げ対象なら、その先をたどる。たどれなければ 410
    for slug, entry in picked.items():
        seen = {slug}
        target = entry.get("redirect", "")
        while target in picked and target not in seen:
            seen.add(target)
            target = picked[target].get("redirect", "")
        if target and target not in picked and target in by_slug:
            entry["redirect"] = target
        else:
            entry.pop("redirect", None)

    # まとめ記事: 残る元記事（転送で付け替えられるものを含む）が2本未満なら一緒に取り下げる
    for a in articles:
        if a.get("type") != "roundup":
            continue
        sources = [s.get("slug") for s in a.get("sources") or [] if isinstance(s, dict) and s.get("slug")]
        if not sources:
            continue
        alive = {s if s not in picked else picked[s].get("redirect", "") for s in sources} - {""}
        if len(alive) < 2 and any(s in picked for s in sources):
            take(a, "creator-roundup")
    return picked, kept


def duplicate_keeper(article: dict, articles: list[dict], removed: set[str]) -> str:
    """重複記事の代表（duplicateOf、無ければ同じ出典URL・同じ題名の残る記事のうち本文が最も長いもの）"""
    target = article.get("duplicateOf")
    if target and target != article["slug"]:
        return target
    url = indexnow._url_key(article.get("sourceUrl", ""))
    title = indexnow.TITLE_NOISE.sub("", article.get("title", "")).lower()
    same = [
        a for a in articles
        if a["slug"] != article["slug"] and a["slug"] not in removed and a.get("type") != "roundup"
        and ((url and indexnow._url_key(a.get("sourceUrl", "")) == url) or indexnow.TITLE_NOISE.sub("", a.get("title", "")).lower() == title)
    ]
    same.sort(key=lambda a: (-len(a.get("body", "")), a.get("generatedAt", "")))
    return same[0]["slug"] if same else ""


def plan_noindex(articles: list[dict], now: datetime) -> tuple[dict[str, dict], list[dict]]:
    """検索に出していない記事をすべて取り下げる計画 {slug: {reason, title, redirect?}} と、残す記事 [{slug, title, reason, why}]"""
    by_slug = {a["slug"]: a for a in articles}
    dups = indexnow.duplicate_slugs(articles)
    sns = recent_sns(now, everywhere=True)
    reasons = {a["slug"]: indexnow.noindex_reason(a, dups) for a in articles}
    picked: dict[str, dict] = {}
    kept: list[dict] = []
    for a in articles:
        reason = reasons[a["slug"]]
        if not reason or reason == "manual":
            continue
        if a.get("disaster"):
            kept.append({"slug": a["slug"], "title": a.get("title", ""), "reason": reason, "why": "地震・津波の速報（短いのは設計どおり）"})
            continue
        if a["slug"] in sns:
            kept.append({"slug": a["slug"], "title": a.get("title", ""), "reason": reason, "why": f"直近{SNS_KEEP_DAYS}日に SNS に出した"})
            continue
        entry = {"reason": reason, "title": a.get("title", ""), "channel": a.get("channelSlug", "")}
        for key in ("sourceUrl", "videoUrl"):
            if a.get(key):
                entry[key] = a[key]
        picked[a["slug"]] = entry
    removed = set(picked)
    for slug, entry in picked.items():
        a = by_slug[slug]
        target = duplicate_keeper(a, articles, removed) if entry["reason"] == "duplicate" else REPRESENTATIVES.get(slug, "")
        if target and target in by_slug and target not in removed:
            entry["redirect"] = target

    # まとめ記事: 元記事（転送で付け替えられるものを含む。自分自身は数えない）が2本未満になるなら一緒に取り下げる。
    # ただし、取り下げた元記事の転送先がそのまとめ自身なら残す（元記事の一覧はリンクなしで残す）
    for a in articles:
        if a.get("type") != "roundup" or a["slug"] in removed:
            continue
        sources = [s.get("slug") for s in a.get("sources") or [] if isinstance(s, dict) and s.get("slug")]
        if not any(s in picked for s in sources):
            continue
        targets = {s if s not in picked else picked[s].get("redirect", "") for s in sources}
        alive = targets - {"", a["slug"]}
        if len(alive) < 2 and a["slug"] not in targets and a["slug"] not in sns:
            picked[a["slug"]] = {"reason": "roundup-of-retired", "title": a.get("title", ""), "channel": a.get("channelSlug", "")}
    return picked, kept


def plan_notice(articles: list[dict], now: datetime) -> tuple[dict[str, dict], list[dict]]:
    """お知らせ・告知・統計の転載の記事を取り下げる計画 {slug: {reason, title}} と、まだ残す記事。
    SNS に出してから7日（出していなければ公開から7日）を過ぎたものだけ取り下げる（2026-11-04 オーナー決定 D5。
    毎週 .github/workflows/notice_retire.yml が --notice --apply で動かす）"""
    posts = sns_posts(everywhere=True)
    picked: dict[str, dict] = {}
    kept: list[dict] = []
    for a in articles:
        if isinstance(a.get("indexable"), bool) and a["indexable"]:
            continue  # 手で「検索に出す」と決めた記事は触らない
        kind = notice_gate.notice_reason(a)
        if not kind:
            continue
        reason = f"notice-{kind}"
        if not notice_due(a, posts, now):
            why = f"SNS に出してから{SNS_KEEP_DAYS}日たっていない" if a["slug"] in posts else f"公開から{SNS_KEEP_DAYS}日たっていない（SNS には未投稿）"
            anchor = notice_anchor(a, posts)
            until = (anchor + timedelta(days=SNS_KEEP_DAYS)).astimezone(JST).strftime("%m/%d %H:%M") if anchor else ""
            kept.append({"slug": a["slug"], "title": a.get("title", ""), "reason": reason, "why": f"{why}。{until} から取り下げの対象"})
            continue
        entry = {"reason": reason, "title": a.get("title", ""), "channel": a.get("channelSlug", "")}
        for key in ("sourceUrl", "videoUrl"):
            if a.get(key):
                entry[key] = a[key]
        picked[a["slug"]] = entry
    return picked, kept


def plan_creator(articles: list[dict]) -> dict[str, dict]:
    """他人の動画の要約の記事をすべて取り下げる計画（2026-11-04 オーナー決定 D3・台帳 A10）。
    発信元が個人・他媒体の YouTube（indexnow.CREATOR_CHANNELS）の記事。検索に出していなくても読める状態で残さない。
    オーナーがこの種類の削除を承認したので、直近に SNS に出した記事も残さない（転送先は無いので 410）"""
    picked: dict[str, dict] = {}
    for a in articles:
        if a.get("channelSlug") not in indexnow.CREATOR_CHANNELS:
            continue
        entry = {"reason": "creator-video", "title": a.get("title", ""), "channel": a.get("channelSlug", "")}
        for key in ("sourceUrl", "videoUrl"):
            if a.get(key):
                entry[key] = a[key]
        picked[a["slug"]] = entry
    return picked


def report_notice(retired: dict[str, dict], kept: list[dict], before: int, after: int, path: Path) -> None:
    """お知らせ・統計の転載の取り下げの一覧を、既存の Markdown の末尾に足す"""
    lines = [
        "",
        "## お知らせ・統計の転載の取り下げ",
        "",
        "### 結論",
        "",
        f"- 発表をそのまま書き写しただけの記事（お知らせ・告知・統計の転載）を **{len(retired)}本** 取り下げました。記事は {before}本 → **{after}本**。",
        "- 取り下げた記事の URL は「この記事は掲載を終了しました」と返します（410 Gone）。一覧・サイトマップ・関連記事からも外れました。",
        f"- SNS に出してから{SNS_KEEP_DAYS}日（出していなければ公開から{SNS_KEEP_DAYS}日）たっていない記事は、まだ残しています（下の「残した記事」）。毎週の自動の取り下げで外れます。",
        "- これからは、同じ種類の記事は公開前に止めます（`scripts/notice_gate.py` の判定を `scripts/indexnow.py` の公開前の門で使う）。",
        "",
        "### 区分ごとの本数",
        "",
        "| 区分 | 本数 |",
        "|---|---|",
    ]
    cats = [c for c in NOTICE_LABELS if any(e["reason"] == c for e in retired.values())]
    lines += [f"| {NOTICE_LABELS[c]} | {sum(1 for e in retired.values() if e['reason'] == c)} |" for c in cats]
    lines += ["", f"### 残した記事（SNS か公開から{SNS_KEEP_DAYS}日たっていない）", ""]
    if kept:
        lines += ["| slug | 題名 | 区分 | 理由 |", "|---|---|---|---|"]
        lines += [f"| {k['slug']} | {k['title'].replace('|', '｜')} | {NOTICE_LABELS.get(k['reason'], k['reason'])} | {k['why']} |" for k in kept]
    else:
        lines.append("なし")
    lines += ["", "### 取り下げた記事の一覧", "", "| slug | 題名 | 区分 |", "|---|---|---|"]
    for cat in cats:
        for slug, e in sorted((s, e) for s, e in retired.items() if e["reason"] == cat):
            lines.append(f"| {slug} | {e['title'].replace('|', '｜')} | {NOTICE_LABELS[cat]} |")
    with path.open("a", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")


# ---- 参照の付け替え ----------------------------------------------------------------


class Rewriter:
    def __init__(self, retired: dict[str, dict]):
        self.retired = retired
        self.changed = 0
        self.self_slug = ""  # いま書き換えている記事（転送先が自分自身になるリンクは外す）
        names = sorted(retired, key=len, reverse=True)
        self.link = (
            re.compile(r"\[([^\]]*)\]\((?:https://ai-tech-express\.jp)?/articles/(" + "|".join(map(re.escape, names)) + r")/?(#[^)\s]*)?\)")
            if names
            else None
        )
        self.bare = (
            re.compile(r"(?:https://ai-tech-express\.jp)?/articles/(" + "|".join(map(re.escape, names)) + r")(?![A-Za-z0-9_-])")
            if names
            else None
        )

    def target(self, slug: str) -> str | None:
        """転送先（無ければ None。取り下げでない slug はそのまま）"""
        if slug not in self.retired:
            return slug
        to = self.retired[slug].get("redirect") or None
        return None if to and to == self.self_slug else to

    def text(self, value: str) -> str:
        if not self.link or "/articles/" not in value:
            return value

        def repl(m: re.Match) -> str:
            to = self.target(m.group(2))
            return f"[{m.group(1)}](/articles/{to}{m.group(3) or ''})" if to else m.group(1)

        out = self.link.sub(repl, value)

        def repl_bare(m: re.Match) -> str:
            to = self.target(m.group(1))
            return m.group(0).replace(m.group(1), to) if to else m.group(0)

        out = self.bare.sub(repl_bare, out)
        if out != value:
            self.changed += 1
        return out

    def slugs(self, values: list) -> list:
        out: list = []
        for v in values:
            to = self.target(v) if isinstance(v, str) else v
            if to and to not in out:
                out.append(to)
        if out != values:
            self.changed += 1
        return out

    def walk(self, value, key: str = ""):
        """JSON を歩いて付け替える。slug の入った辞書の並びは、転送先が無ければ並びから外す"""
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                if k in ("slug", "verdictSource", "duplicateOf", "angleOf", "roundupSlug", "mainSlug") and isinstance(v, str) and v in self.retired:
                    to = self.target(v)
                    self.changed += 1
                    if to:
                        out[k] = to
                    elif k == "slug":
                        out[k] = ""  # 呼び出し側（並び）で外す。単独の出典（料金表）はリンクなしの表示になる
                    elif k == "verdictSource":
                        out[k] = None  # 料金表の型（string | null）を保つ
                    continue  # duplicateOf など: 消す
                out[k] = self.walk(v, k)
            return out
        if isinstance(value, list):
            if value and all(isinstance(v, str) for v in value) and any(v in self.retired for v in value):
                return self.slugs(value)
            items = [self.walk(v, key) for v in value]
            kept = [
                v for v, orig in zip(items, value)
                if not (isinstance(orig, dict) and isinstance(orig.get("slug"), str) and orig.get("slug") in self.retired and not (isinstance(v, dict) and v.get("slug")))
            ]
            # 同じ slug が並びに2回出たら1つに（転送で同じ記事に付け替えたとき）
            seen: set[str] = set()
            deduped = []
            for v in kept:
                s = v.get("slug") if isinstance(v, dict) else None
                if isinstance(s, str) and s and isinstance(v, dict) and len(v) > 1 and key in ("sources", "items", "timeline", "articles"):
                    if s in seen:
                        self.changed += 1
                        continue
                    seen.add(s)
                deduped.append(v)
            return deduped
        if isinstance(value, str):
            return self.text(value)
        return value


SOURCE_KEYS = ("overview", "capabilities", "pricing", "japan", "howto", "differences", "faq", "facts")


def fix_product(data: dict) -> dict | None:
    """出典をすべて失った項目は外す（出典のない事実を載せない）。関係する記事が無くなった製品は消す"""
    if not data.get("articles"):
        return None
    for key in SOURCE_KEYS:
        value = data.get(key)
        if isinstance(value, dict) and "sources" in value and not value["sources"]:
            return None  # 概要の出典が無い
        if isinstance(value, list):
            data[key] = [v for v in value if not (isinstance(v, dict) and "sources" in v and not v["sources"])]
    data["timeline"] = [t for t in data.get("timeline") or [] if not isinstance(t, dict) or t.get("slug") != ""]
    return data


def parent_product(slug: str, live: set[str]) -> str:
    """消える製品の転送先: 今も残る製品のうち、slug が「その製品の slug + -」で始まる一番長いもの
    （chatgpt-enterprise → chatgpt、gemini-4-argon-pro → gemini-4-argon）。無ければ ""（サイトは図鑑の一覧 /products へ転送）"""
    best = ""
    for cand in live:
        if cand != slug and slug.startswith(cand + "-") and len(cand) > len(best):
            best = cand
    return best


def record_removed_products(store: dict, removed: list[str], stamp: str, names: dict[str, str] | None = None) -> None:
    """消した製品ページを content/retired.json の products に残す（cloudflare/worker.ts が 404 にせず親の製品・一覧へ 301。台帳 W30）。
    前に消した製品の転送先が今は無ければ、たどり直す。同じ slug の製品がまた作られていれば、その記録は消す"""
    live = {p.stem for p in (ROOT / "content" / "products").glob("*.json")}
    products = store.setdefault("products", {})
    for slug in removed:
        products[slug] = {"reason": "no-sources", "retiredAt": stamp, "name": (names or {}).get(slug, slug)}
    for slug in list(products):
        if slug in live:
            del products[slug]
            continue
        products[slug]["redirect"] = parent_product(slug, live)
        if not products[slug]["redirect"]:
            products[slug].pop("redirect")


def forget_products(slugs) -> list[str]:
    """製品ページを（今も載っている記事を出典に）作り直したとき、その製品の転送の記録を content/retired.json から消す。
    scripts/products.py が保存のたびに呼ぶ（台帳 W30。2026-11-04: 作り直した chatgpt-enterprise に転送の記録が残り、公開のテストが落ちた）"""
    if not RETIRED.exists():
        return []
    store = json.loads(RETIRED.read_text(encoding="utf-8"))
    products = store.get("products") or {}
    gone = [s for s in slugs if s in products]
    if not gone:
        return []
    for s in gone:
        del products[s]
    RETIRED.write_text(json.dumps(store, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return gone


def apply(retired: dict[str, dict], now: datetime) -> dict:
    stamp = now.astimezone(JST).isoformat(timespec="seconds")
    store = {"version": 1, "articles": {}}
    if RETIRED.exists():
        store = json.loads(RETIRED.read_text(encoding="utf-8"))
    for slug, entry in retired.items():
        store["articles"][slug] = {**entry, "retiredAt": stamp}
    all_retired = store["articles"]
    # 前に取り下げた記事の転送先が今回取り下げになったら、その先をたどる（たどれなければ 410）
    for slug, entry in all_retired.items():
        seen = {slug}
        target = entry.get("redirect", "")
        while target in all_retired and target not in seen:
            seen.add(target)
            target = all_retired[target].get("redirect", "")
        if target and target not in all_retired and (ARTICLES / f"{target}.json").exists():
            entry["redirect"] = target
        else:
            entry.pop("redirect", None)
    rw = Rewriter(all_retired)
    stats = {"deleted": 0, "articles_rewritten": 0, "data_rewritten": 0, "products_removed": []}

    for slug in retired:
        path = ARTICLES / f"{slug}.json"
        if path.exists():
            path.unlink()
            stats["deleted"] += 1

    for path in sorted(ARTICLES.glob("*.json")):
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        rw.changed = 0
        rw.self_slug = data.get("slug", "")
        # まとめ記事の元記事が、取り下げて自分自身へ転送されるなら、一覧には題名だけ残す（リンクなし）
        for source in data.get("sources") or []:
            if isinstance(source, dict) and source.get("slug") in all_retired and all_retired[source["slug"]].get("redirect") == rw.self_slug:
                source["slug"] = ""
                rw.changed += 1
        new = rw.walk(data)
        if rw.changed:
            path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stats["articles_rewritten"] += 1

    removed_names: dict[str, str] = {}
    files = [ROOT / f for f in DATA_FILES] + [p for d in DATA_DIRS for p in sorted((ROOT / d).glob("*.json"))]
    for path in files:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        rw.changed = 0
        rw.self_slug = ""
        new = rw.walk(data)
        if path.parent.name == "products":
            fixed = fix_product(new)
            if fixed is None:
                path.unlink()
                stats["products_removed"].append(path.stem)
                removed_names[path.stem] = str(new.get("name") or path.stem)
                continue
            new = fixed
        if rw.changed:
            path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stats["data_rewritten"] += 1

    # 消した製品ページは 404 にせず、親の製品ページ（無ければ図鑑の一覧）へ転送する（台帳 W30）
    if removed_names or store.get("products"):
        record_removed_products(store, stats["products_removed"], stamp, removed_names)
    RETIRED.write_text(json.dumps(store, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return stats


def report(retired: dict[str, dict], kept: list[dict], remaining: int, path: Path) -> None:
    lines = [
        "# 削除した記事（2026-11-03）",
        "",
        "## 結論",
        "",
        f"- 取り下げた記事：**{len(retired)}本**。残る記事：**{remaining}本**。",
        "- 重複の記事は、同じ発表の代表の記事へ自動で転送します（301）。それ以外は「取り下げました」と返します（410 Gone）。",
        "- 一覧・サイトマップ・検索・フィード・関連記事・まとめ記事の元記事・製品まとめ・料金表から外しました。",
        f"- 直近{SNS_KEEP_DAYS}日に SNS に出した記事は、投稿のリンクが生きているので残しました（下の「残した記事」）。",
        "",
        "## 区分ごとの本数",
        "",
        "| 区分 | 本数 |",
        "|---|---|",
    ]
    for cat in CATEGORIES:
        n = sum(1 for e in retired.values() if e["reason"] == cat)
        lines.append(f"| {LABELS[cat]} | {n} |")
    lines += ["", f"## 残した記事（直近{SNS_KEEP_DAYS}日に SNS に出したため）", ""]
    if kept:
        lines += ["| slug | 題名 | 本来の区分 |", "|---|---|---|"]
        lines += [f"| {k['slug']} | {k['title']} | {LABELS[k['reason']]} |" for k in kept]
    else:
        lines.append("なし")
    for cat in CATEGORIES:
        rows = sorted((s, e) for s, e in retired.items() if e["reason"] == cat)
        if not rows:
            continue
        lines += ["", f"## {LABELS[cat]}（{len(rows)}本）", "", "| slug | 題名 | 転送先 |", "|---|---|---|"]
        for slug, e in rows:
            title = e["title"].replace("|", "｜")
            lines.append(f"| {slug} | {title} | {e.get('redirect', '—（410）')} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def report_append(retired: dict[str, dict], kept: list[dict], before: int, after: int, indexable: int, path: Path) -> None:
    """追加の取り下げ（noindex の記事）の一覧を、既存の Markdown の末尾に足す"""
    redirects = sum(1 for e in retired.values() if e.get("redirect"))
    lines = [
        "",
        "## 追加の取り下げ（noindex の記事）",
        "",
        "### 結論",
        "",
        f"- 検索に出していなかった（noindex の）記事を **{len(retired)}本** 取り下げました。記事は {before}本 → **{after}本**。",
        f"- 残った記事のうち検索に出す記事は **{indexable}本（{indexable / max(after, 1):.0%}）** です。",
        f"- はっきり近い記事がある {redirects}本は、その記事へ自動で転送します（301）。ほかは「取り下げました」と返します（410 Gone）。",
        "- これからは、同じ種類の記事（主題外・日本の読者に役立つ情報がない・薄い）は公開前に止めます（scripts/indexnow.py の公開前の門）。",
        "",
        "### 区分ごとの本数",
        "",
        "| 区分 | 本数 |",
        "|---|---|",
    ]
    cats = [c for c in (*NOINDEX_CATEGORIES, "roundup-of-retired") if any(e["reason"] == c for e in retired.values())]
    lines += [f"| {NOINDEX_LABELS[c]} | {sum(1 for e in retired.values() if e['reason'] == c)} |" for c in cats]
    lines += ["", "### 残した noindex の記事", ""]
    if kept:
        lines += ["| slug | 題名 | 区分 | 残した理由 |", "|---|---|---|---|"]
        lines += [f"| {k['slug']} | {k['title'].replace('|', '｜')} | {NOINDEX_LABELS.get(k['reason'], k['reason'])} | {k['why']} |" for k in kept]
    else:
        lines.append("なし")
    lines += ["", "### 取り下げた記事の一覧", "", "| slug | 題名 | 理由 | 転送先 |", "|---|---|---|---|"]
    for cat in cats:
        for slug, e in sorted((s, e) for s, e in retired.items() if e["reason"] == cat):
            lines.append(f"| {slug} | {e['title'].replace('|', '｜')} | {NOINDEX_LABELS[cat]} | {e.get('redirect', '—（410）')} |")
    with path.open("a", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="取り下げる（指定しなければ数えるだけ）")
    parser.add_argument("--report", type=Path, help="対象の一覧を Markdown に書く")
    parser.add_argument("--refs", action="store_true", help="取り下げ済みの記事への参照だけを付け替える（新しく取り下げない）")
    parser.add_argument("--noindex", action="store_true", help="検索に出していない（noindex の）記事をすべて取り下げる")
    parser.add_argument("--notice", action="store_true", help="お知らせ・告知・統計の転載の記事を取り下げる（scripts/notice_gate.py）")
    parser.add_argument("--creator", action="store_true", help="他人の動画の要約の記事（CREATOR_CHANNELS）をすべて取り下げる（D3）")
    parser.add_argument("--append", type=Path, help="--noindex / --notice の一覧を Markdown の末尾に足す")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.refs:
        print(f"[retire] 参照の付け替え: {json.dumps(apply({}, now), ensure_ascii=False)}")
        return 0
    articles = load_articles()
    if args.creator:
        retired = plan_creator(articles)
        print(f"[retire] 全 {len(articles)} 本のうち、他人の動画の要約の取り下げ {len(retired)} 本")
        for slug, e in sorted(retired.items()):
            print(f"  - {slug}（{e['channel']}）: {e['title']}")
        if args.apply and retired:
            stats = apply(retired, now)
            again = apply({}, now)
            stats["second_pass"] = again["articles_rewritten"] + again["data_rewritten"]
            print(f"[retire] 実行: {json.dumps(stats, ensure_ascii=False)}")
        return 0
    if args.notice:
        retired, kept = plan_notice(articles, now)
        counts = {c: sum(1 for e in retired.values() if e["reason"] == c) for c in NOTICE_LABELS}
        print(f"[retire] 全 {len(articles)} 本のうち、お知らせ・統計の転載の取り下げ {len(retired)} 本: "
              + "・".join(f"{NOTICE_LABELS[c]} {n}" for c, n in counts.items() if n))
        for slug, e in sorted(retired.items()):
            print(f"  - {slug}: {e['title']}")
        print("[retire] 残す: " + (", ".join(f"{k['slug']}（{k['why']}）" for k in kept) or "なし"))
        if args.apply:
            stats = apply(retired, now)
            again = apply({}, now)
            stats["second_pass"] = again["articles_rewritten"] + again["data_rewritten"]
            print(f"[retire] 実行: {json.dumps(stats, ensure_ascii=False)}")
        if args.append:
            after = len(load_articles()) if args.apply else len(articles) - len(retired)
            report_notice(retired, kept, len(articles), after, args.append)
            print(f"[retire] 一覧 → {args.append}")
        return 0
    if args.noindex:
        retired, kept = plan_noindex(articles, now)
        counts = {c: sum(1 for e in retired.values() if e["reason"] == c) for c in (*NOINDEX_CATEGORIES, "roundup-of-retired")}
        print(f"[retire] 全 {len(articles)} 本のうち noindex の取り下げ {len(retired)} 本（転送 {sum(1 for e in retired.values() if e.get('redirect'))}）: "
              + "・".join(f"{c} {n}" for c, n in counts.items() if n))
        print("[retire] 残す: " + ", ".join(f"{k['slug']}（{k['why']}）" for k in kept))
        if args.apply:
            stats = apply(retired, now)
            again = apply({}, now)
            stats["second_pass"] = again["articles_rewritten"] + again["data_rewritten"]
            print(f"[retire] 実行: {json.dumps(stats, ensure_ascii=False)}")
        if args.append:
            rest = load_articles() if args.apply else [a for a in articles if a["slug"] not in retired]
            dups = indexnow.duplicate_slugs(rest)
            indexable = sum(1 for a in rest if indexnow.noindex_reason(a, dups) is None)
            report_append(retired, kept, len(articles), len(rest), indexable, args.append)
            print(f"[retire] 一覧 → {args.append}")
        return 0
    retired, kept = plan(articles, now)
    counts = {cat: sum(1 for e in retired.values() if e["reason"] == cat) for cat in CATEGORIES}
    print(f"[retire] 全 {len(articles)} 本のうち取り下げ {len(retired)} 本: " + "・".join(f"{LABELS[c]} {n}" for c, n in counts.items()))
    print(f"[retire] 直近{SNS_KEEP_DAYS}日に SNS に出したので残す: {len(kept)} 本 " + ", ".join(k["slug"] for k in kept))
    if args.report:
        report(retired, kept, len(articles) - len(retired), args.report)
        print(f"[retire] 一覧 → {args.report}")
    if args.apply:
        stats = apply(retired, now)
        # 入れ子のリンク（[[…](/articles/a)](/articles/b)）などは1回では付け替えきれないことがあるので、もう1回だけ回す
        again = apply({}, now)
        stats["second_pass"] = again["articles_rewritten"] + again["data_rewritten"]
        print(f"[retire] 実行: {json.dumps(stats, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
