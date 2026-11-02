#!/usr/bin/env python3
"""同じ発表（同じ話題）の記事を見分ける判定と、重複記事への印付け（duplicateOf）。

サイト（lib/indexing.ts・lib/article-index.mjs）と IndexNow（scripts/indexnow.py）は、ここで記事 JSON に書いた
duplicateOf を読むだけにする（判定を TypeScript と Python で二重に持たない）。

同じ話題とみなす条件（どれか1つ。比べるのは「サイソクに載せた時刻」か「元の発表の時刻」が5日以内の記事だけ）:
  1. 出典 URL が同じ（クエリ・utm・末尾の /・www・言語の段（/ja-jp/ など）を除いて比べる。
     openai.com/blog/X → /index/X のような既知の転送も同じにする。YouTube は動画 ID で比べる）
  2. 正規化した題名が同じ
  3. 同じ会社（radar の entities）で、
     a. 題名に同じ「名前＋版数」の製品名（genie4・gemini4carbon など）がある
     b. 日本語の題名に英字のまま同じ珍しい固有名詞（Legora・Zendesk など。全記事で6本以下にしか出てこない語）がある
     c. 同じ発信元が6時間以内に出し、題名に同じ珍しい固有名詞がある（1つの事例を何本にも分けた公開）
     d. 日本語の題名の文字バイグラムの類似度が 0.6 以上、または元の題名（動画・元記事）がまったく同じ
     会社が両方わかっていて1社も重ならないとき・ジャンルが違うときは同じ話題にしない。
     製品名は「名前＋版数＋名前」をひとまとめにする（GPT-6 Astra と GPT-6 Sol は別。PS5 のような短い型番は数えない）。
     a は題名か keywords も似ていること（類似度 0.15 以上か keywords の重なり 0.3 以上）、
     b は類似度 0.35 以上か（0.2 以上かつ keywords の重なり 0.3 以上）。
同じ話題でも重複にしないもの（意図して角度を変えた解説）:
  - angleOf が付いた記事（scripts/angle_articles.py が付ける）
  - 後の記事が先の記事にリンク（relatedSlugs と本文の /articles/<slug>）し、題名が解説の形（使い方・比較・違い・誰が使える 等）
代表（canonical）: 同じ話題のうち、ほかの理由で検索から外れない記事の中で最初に載せたもの（無ければ最初の記事）。
  手動で indexable: true にした記事は代表にし、印を付けない。まとめ記事（roundup）・地震の記事は対象外。
一度付けた duplicateOf は、--recompute を付けない限り外さない（代表が入れ替わって検索のページが揺れないように）。

  python3 scripts/story.py mark --dry-run        # 全記事を調べて、付ける印を表示するだけ
  python3 scripts/story.py mark                  # 印を付ける（直近 --days 日に載せた記事だけ見直す。0=全記事）
  python3 scripts/story.py check "題名" [--url URL] [--keywords a,b]   # 候補が直近5日の記事と同じ話題か
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dedupe  # noqa: E402
import radar  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
WINDOW = timedelta(days=5)
RARE_DF = 6
SIM_STRONG = 0.6
SIM_WITH_TOKEN = 0.2
SIM_RARE = 0.35
KW_OVERLAP = 0.3
# 解説の形の題名（角度を変えた記事の目印。これだけでは重複を外さない。先の記事へのリンクも要る）
EXPLAINER_RE = re.compile(r"使い方|比較|違い|選び方|誰が使える|使える？|とは[？?]|始め方|できること|料金.*(整理|まとめ)|プラン別|解説|徹底|まとめ|FAQ|Q&A")
LOCALE_SEG = re.compile(r"^(?:[a-z]{2}(?:[-_][a-z]{2,4})?|intl)$", re.I)
KNOWN_LOCALES = {"ja", "jp", "ja-jp", "ja_jp", "en", "en-us", "en_us", "en-gb", "us", "intl", "global"}
YOUTUBE_RE = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})")


# --------------------------------------------------------------------------- #
# 出典 URL
# --------------------------------------------------------------------------- #


def url_keys(url: str) -> set[str]:
    """同じページとみなす鍵。クエリ（utm など）・末尾の /・www・言語の段を落とす。
    #entry-YYYY-MM-DD（scripts/page_watch.py が付けるリリースノートの1項目）だけは残す"""
    url = str(url or "").strip()
    if not url:
        return set()
    m = YOUTUBE_RE.search(url)
    if m:
        return {f"yt:{m.group(1)}"}
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return set()
    host = parts.netloc.lower().removeprefix("www.")
    if not host:
        return set()
    segs = [s for s in parts.path.split("/") if s]
    while segs and segs[0].lower() in KNOWN_LOCALES:
        segs = segs[1:]
    if host == "openai.com" and segs[:1] == ["blog"]:
        segs = ["index", *segs[1:]]
    frag = parts.fragment if parts.fragment.startswith("entry-") else ""
    path = "/".join(segs).lower()
    keys = {f"{host}/{path}" + (f"#{frag}" if frag else "")}
    last = segs[-1].lower() if segs else ""
    if not frag and len(last) >= 12 and re.search(r"[a-z]-[a-z0-9]", last):
        keys.add(f"{host}|{last}")
    return keys


# --------------------------------------------------------------------------- #
# 記事の手がかり
# --------------------------------------------------------------------------- #


def _time(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def title_key(title: str) -> str:
    return dedupe.normalize(title)


class Story:
    """記事（または候補）1本ぶんの手がかり"""

    __slots__ = ("slug", "title", "head_titles", "gen", "src", "keys", "ents", "head", "tokens", "products", "latin", "kw", "genre", "sources", "data")

    def __init__(self, data: dict) -> None:
        self.data = data
        self.slug = str(data.get("slug") or "")
        self.title = str(data.get("title") or "")
        heads = [self.title]
        for k in ("videoTitle", "originalTitle"):
            v = str(data.get(k) or "")
            if v and v not in heads and not v.startswith("YouTube video "):
                heads.append(v)
        self.head_titles = heads
        # 元の題名（動画・元記事）。題名と同じでも残す（同じ動画の再投稿を見分ける）
        self.sources = [str(data.get(k)) for k in ("videoTitle", "originalTitle") if data.get(k) and not str(data.get(k)).startswith("YouTube video ")]
        self.gen = _time(data.get("generatedAt")) or _time(data.get("publishedAt"))
        self.src = _time(data.get("sourcePublishedAt")) or _time(data.get("publishedAt")) or self.gen
        keys: set[str] = set()
        keys |= url_keys(str(data.get("sourceUrl") or ""))
        if data.get("videoId"):
            keys.add(f"yt:{data['videoId']}")
        for u in data.get("updates") or []:
            if isinstance(u, dict) and u.get("sourceUrl"):
                keys |= url_keys(str(u["sourceUrl"]))
        self.keys = keys
        keywords = [str(k) for k in (data.get("keywords") or [])][:10]
        text = " | ".join(heads + keywords + [str(data.get("channel") or ""), str(data.get("sourceName") or "")])
        self.ents = set(radar.entities_of(text))
        head: set[str] = set()
        for h in heads:
            head |= radar.features(h, strong=True)
        self.head = head
        self.tokens = head | radar.features(" | ".join(keywords), strong=True)
        self.products = set()
        for h in heads:
            self.products |= product_tokens(h)
        # 日本語の題名に英字のまま残った語（固有名詞。一般語は訳されるので残りにくい）
        self.kw = {dedupe.normalize(k) for k in keywords if k}
        self.genre = str(data.get("genreSlug") or "")
        self.latin = {t for t in radar.features(self.title, strong=True) if t.isascii() and t.isalpha() and len(t) >= 4}


COMPOUND_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z]+)[ \-]?(\d+(?:\.\d+)?)[ \-]([A-Z][A-Za-z]{2,})")
YEARISH_RE = re.compile(r"(?:19|20)\d\d$|^(?:top|in|no|vol|ep|part|sj|q|fy)\d")


def product_tokens(title: str) -> set[str]:
    """題名の製品名（「名前＋版数」。後ろに名前が続けば3語でひとまとめ: GPT-6 Astra → gpt6astra）。
    名前が続くものは版数だけ（gpt6）では数えない（GPT-6 Astra と GPT-6 Sol を同じにしない）"""
    t = unicodedata.normalize("NFKC", title or "")
    out: set[str] = set()
    compound_heads: set[str] = set()
    for m in COMPOUND_RE.finditer(t):
        alpha, num, word = m.group(1).lower(), m.group(2), m.group(3).lower()
        if word in radar.STOP or alpha in radar.STOP:
            continue
        out.add(f"{alpha}{num}{word}")
        compound_heads.add(f"{alpha}{num}")
    for tok in radar.features(t, strong=True):
        if (radar._special(tok) and not tok.startswith(radar.EVENT_PREFIX) and tok not in compound_heads
                and not YEARISH_RE.search(tok) and len(re.match(r"[a-z]*", tok).group(0)) >= 3):
            out.add(tok)
    return out


def gap(a: Story, b: Story) -> timedelta:
    out = []
    if a.gen and b.gen:
        out.append(abs(a.gen - b.gen))
    if a.src and b.src:
        out.append(abs(a.src - b.src))
    return min(out) if out else timedelta(days=9999)


def title_sim(a: Story, b: Story) -> float:
    """日本語の題名どうしの類似度（元動画の題名はシリーズの定型が多く、似ていても別の話のことがあるので使わない）"""
    return dedupe.jaccard(dedupe.bigrams(a.title), dedupe.bigrams(b.title))


def same_source_title(a: Story, b: Story) -> bool:
    """元の題名（動画・元記事）がまったく同じ（同じ動画の再投稿・同じ発表の別URL）"""
    xa = {dedupe.normalize(t) for t in a.sources if len(dedupe.normalize(t)) >= 12}
    return bool(xa & {dedupe.normalize(t) for t in b.sources})


def is_angle(later: Story, earlier: Story) -> bool:
    """後の記事が先の記事を意図して別の角度から書いたもの（重複にしない）"""
    d = later.data
    if d.get("angleOf") == earlier.slug:
        return True
    if earlier.slug in (d.get("relatedSlugs") or []) and EXPLAINER_RE.search(later.title):
        return f"/articles/{earlier.slug}" in str(d.get("body") or "")
    return False


def same_story(a: Story, b: Story, df: dict[str, int], window: timedelta = WINDOW, candidate: bool = False) -> str | None:
    """同じ話題なら理由、違えば None。
    candidate=True は生成前の候補（英語の元の題名だけで keywords が無い）。製品名と会社が一致すれば同じ話題とみなす
    （大きなニュースは web_articles.py が続報の判定（LLM）に回すので、別の発表なら新しい記事になる）"""
    if gap(a, b) > window:
        return None
    if a.keys & b.keys:
        return "同じ出典URL"
    if title_key(a.title) and title_key(a.title) == title_key(b.title):
        return "同じ題名"
    if a.genre and b.genre and a.genre != b.genre:
        return None  # 投資の解説の中で AI に触れた、など
    if a.ents and b.ents and not (a.ents & b.ents):
        return None
    if same_source_title(a, b):
        return "元の題名が同じ"
    ents = a.ents & b.ents
    sim = title_sim(a, b)
    products = sorted(a.products & b.products)
    rare = sorted(t for t in a.latin & b.latin if df.get(t, 0) <= RARE_DF and t not in radar.ALIASES and t not in radar.STOP)
    kw = dedupe.jaccard(a.kw, b.kw)
    if products and ents and (candidate or sim >= 0.15 or kw >= KW_OVERLAP):
        return f"製品名が一致（{'・'.join(products[:2])}）"
    same_drop = (a.data.get("channelSlug") and a.data.get("channelSlug") == b.data.get("channelSlug")
                 and a.src and b.src and abs(a.src - b.src) <= timedelta(hours=6))
    if rare and same_drop:
        return f"同じ発信元が同時に出した同じ話題（{'・'.join(rare[:2])}）"
    if rare and (ents or not (a.ents or b.ents)) and (sim >= SIM_RARE or (sim >= SIM_WITH_TOKEN and kw >= KW_OVERLAP)):
        return f"珍しい固有名詞が一致（{'・'.join(rare[:2])} / {sim:.2f}）"
    if sim >= SIM_STRONG and (ents or not (a.ents or b.ents)):
        return f"題名が類似（{sim:.2f}）"
    return None


def doc_freq(stories: list[Story]) -> dict[str, int]:
    df: dict[str, int] = {}
    for s in stories:
        for t in s.tokens:
            df[t] = df.get(t, 0) + 1
    return df


# --------------------------------------------------------------------------- #
# 代表の選び方（検索から外れる理由が他に無い記事を優先）
# --------------------------------------------------------------------------- #


def _other_noindex(data: dict) -> str | None:
    import indexnow  # noqa: PLC0415

    return indexnow.noindex_reason({k: v for k, v in data.items() if k not in ("duplicateOf",)})


def eligible(data: dict) -> bool:
    if data.get("type") == "roundup" or data.get("disaster") or data.get("quakeEventId"):
        return False
    return bool(data.get("slug") and data.get("title"))


def plan(stories: list[Story], df: dict[str, int], recheck_since: datetime | None = None) -> dict[str, tuple[str, str]]:
    """{重複の slug: (代表の slug, 理由)}。stories は載せた順（古い順）に並べておく。
    recheck_since があれば、それより後に載せた記事だけを「重複かもしれない側」として見る"""
    groups: list[list[tuple[Story, str]]] = []  # 先頭が話題の最初の記事
    seen: list[Story] = []
    for s in stories:
        # 意図して角度を変えた解説は、どの話題にも入れない（ほかの記事の代表にもならない）
        if any(is_angle(s, x) for x in seen):
            seen.append(s)
            continue
        seen.append(s)
        hit = None
        for g in groups:
            head = g[0][0]
            why = same_story(s, head, df)
            if why:
                hit = (g, why)
                break
        if hit:
            hit[0].append((s, hit[1]))
        else:
            groups.append([(s, "")])
    out: dict[str, tuple[str, str]] = {}
    for g in groups:
        if len(g) < 2:
            continue
        members = [x for x, _ in g]
        manual = [x for x in members if x.data.get("indexable") is True]
        # すでに代表として扱われている記事（duplicateOf の無い先の記事）を動かさない
        keeper = manual[0] if manual else next((x for x in members if _other_noindex(x.data) is None), members[0])
        for x, why in g:
            if x is keeper or x.data.get("indexable") is True:
                continue
            if recheck_since and x.gen and x.gen < recheck_since and not x.data.get("duplicateOf"):
                continue
            out[x.slug] = (keeper.slug, why or same_story(x, keeper, df) or "同じ話題")
    return out


def load_all() -> list[tuple[Path, dict]]:
    rows = []
    for path in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            data.setdefault("slug", path.stem)
            rows.append((path, data))
    return rows


def mark(dry_run: bool = False, days: int = 0, recompute: bool = False) -> dict[str, tuple[str, str]]:
    rows = [(p, d) for p, d in load_all() if eligible(d)]
    stories = [Story(d) for _, d in rows]
    order = sorted(range(len(stories)), key=lambda i: (stories[i].gen or datetime.max.replace(tzinfo=timezone.utc), stories[i].slug))
    stories = [stories[i] for i in order]
    rows = [rows[i] for i in order]
    df = doc_freq(stories)
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    result = plan(stories, df, None if recompute else since)
    by_slug = {d["slug"]: (p, d) for p, d in rows}
    changed = 0
    for slug, (keep, why) in sorted(result.items()):
        path, data = by_slug[slug]
        print(f"[dup] {slug} → {keep}（{why}）")
        if data.get("duplicateOf") == keep:
            continue
        if data.get("duplicateOf") and not recompute:
            continue  # 前に付けた印を保つ
        changed += 1
        if not dry_run:
            data["duplicateOf"] = keep
            data["duplicateReason"] = why[:120]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if recompute:
        for path, data in rows:
            if data.get("duplicateOf") and data["slug"] not in result:
                print(f"[undup] {data['slug']}（同じ話題の判定から外れた）")
                changed += 1
                if not dry_run:
                    data.pop("duplicateOf", None)
                    data.pop("duplicateReason", None)
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[story] 重複 {len(result)} 本（今回の変更 {changed} 本{'・dry-run' if dry_run else ''}）")
    return result


# --------------------------------------------------------------------------- #
# 生成前の確認（パイプラインが使う）
# --------------------------------------------------------------------------- #


class Recent:
    """直近 days 日の記事の索引。候補が同じ話題か調べる"""

    def __init__(self, days: int = 5) -> None:
        since = datetime.now(timezone.utc) - timedelta(days=days + 1)
        all_rows = [d for _, d in load_all() if eligible(d)]
        self.df = doc_freq([Story(d) for d in all_rows])
        self.stories = [s for s in (Story(d) for d in all_rows) if s.gen and s.gen >= since]

    def add(self, data: dict) -> None:
        self.stories.append(Story(data))

    def match(self, title: str, url: str = "", keywords: list[str] | None = None, published: datetime | None = None,
              video_id: str = "", extra_titles: list[str] | None = None, channel_slug: str = "",
              genre: str = "") -> tuple[str, str] | None:
        """同じ話題の記事があれば (slug, 理由)"""
        now = datetime.now(timezone.utc)
        cand = Story({
            "slug": "", "title": title, "videoTitle": (extra_titles or [""])[0], "sourceUrl": url, "videoId": video_id,
            "keywords": keywords or [], "generatedAt": now.isoformat(),
            "publishedAt": (published or now).isoformat(), "channelSlug": channel_slug, "genreSlug": genre,
        })
        best = None
        for s in self.stories:
            why = same_story(cand, s, self.df, candidate=True)
            if why:
                score = 3 if why == "同じ出典URL" else (2 if why.startswith("製品名") else 1)
                if best is None or score > best[0]:
                    best = (score, s.slug, why)
        return (best[1], best[2]) if best else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    p_mark = sub.add_parser("mark")
    p_mark.add_argument("--dry-run", action="store_true")
    p_mark.add_argument("--days", type=int, default=0, help="直近この日数に載せた記事だけ見直す（0=全記事）")
    p_mark.add_argument("--recompute", action="store_true", help="前に付けた印も付け直す（外れたものは消す）")
    p_check = sub.add_parser("check")
    p_check.add_argument("title")
    p_check.add_argument("--url", default="")
    p_check.add_argument("--keywords", default="")
    args = parser.parse_args()
    if args.cmd == "mark":
        mark(args.dry_run, args.days, args.recompute)
        return 0
    if args.cmd == "check":
        hit = Recent().match(args.title, args.url, [k for k in args.keywords.split(",") if k])
        print(f"{hit[0]}（{hit[1]}）" if hit else "同じ話題の記事なし")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
