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

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = ROOT / "content" / "articles"
RETIRED = ROOT / "content" / "retired.json"
THREADS = ROOT / "state" / "threads_posted.json"
JST = timezone(timedelta(hours=9))
CATEGORIES = ("creator-video", "creator-roundup", "wire", "thin", "duplicate")
LABELS = {
    "creator-video": "他人の動画の要約",
    "creator-roundup": "他人の動画の要約だけのまとめ",
    "wire": "プレスリリースの言い換え",
    "thin": "薄い記事（1,500字未満・表やよくある質問なし）",
    "duplicate": "同じ発表の重複（代表の記事へ転送）",
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


def recent_sns(now: datetime) -> set[str]:
    try:
        posted = json.loads(THREADS.read_text(encoding="utf-8")).get("posted", {})
    except (OSError, json.JSONDecodeError):
        return set()
    keep = set()
    for slug, item in posted.items():
        try:
            at = datetime.fromisoformat(str(item.get("postedAt", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if now - at < timedelta(days=SNS_KEEP_DAYS):
            keep.add(slug)
    return keep


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


# ---- 参照の付け替え ----------------------------------------------------------------


class Rewriter:
    def __init__(self, retired: dict[str, dict]):
        self.retired = retired
        self.changed = 0
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
        return self.retired[slug].get("redirect") or None

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


def apply(retired: dict[str, dict], now: datetime) -> dict:
    stamp = now.astimezone(JST).isoformat(timespec="seconds")
    store = {"version": 1, "articles": {}}
    if RETIRED.exists():
        store = json.loads(RETIRED.read_text(encoding="utf-8"))
    for slug, entry in retired.items():
        store["articles"][slug] = {**entry, "retiredAt": stamp}
    all_retired = store["articles"]
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
        new = rw.walk(data)
        if rw.changed:
            path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stats["articles_rewritten"] += 1

    files = [ROOT / f for f in DATA_FILES] + [p for d in DATA_DIRS for p in sorted((ROOT / d).glob("*.json"))]
    for path in files:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        rw.changed = 0
        new = rw.walk(data)
        if path.parent.name == "products":
            fixed = fix_product(new)
            if fixed is None:
                path.unlink()
                stats["products_removed"].append(path.stem)
                continue
            new = fixed
        if rw.changed:
            path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stats["data_rewritten"] += 1

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="取り下げる（指定しなければ数えるだけ）")
    parser.add_argument("--report", type=Path, help="対象の一覧を Markdown に書く")
    parser.add_argument("--refs", action="store_true", help="取り下げ済みの記事への参照だけを付け替える（新しく取り下げない）")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.refs:
        print(f"[retire] 参照の付け替え: {json.dumps(apply({}, now), ensure_ascii=False)}")
        return 0
    articles = load_articles()
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
