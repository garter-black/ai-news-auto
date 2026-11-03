#!/usr/bin/env python3
"""同じ話題の記事を二度作らないための重複判定（API 不要）。

直近 N 日の記事（content/articles）と、同じ実行の中の候補どうしを比べる。
  1) sourceUrl が同じ → 重複
  2) タイトルの文字バイグラム Jaccard ≥ 0.5 → 重複
  3) 珍しい固有名詞（カタカナ語・英数字の語・「」内の語）が2つ以上一致 → 重複
     1つ一致なら Jaccard ≥ 0.3 で重複（英語の元タイトル × 日本語の記事タイトルにも効くように）

  python3 scripts/dedupe.py "タイトル" ["タイトル" ...]   # 判定だけ試す
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
RETIRED_PATH = ROOT / "content" / "retired.json"

JACCARD_DUP = 0.5
JACCARD_WITH_ENTITY = 0.3
COMMON_ENTITY_DF = 6  # これ以上の記事に出てくる語（Google, AI など）は「珍しい固有名詞」に数えない

# 見出しの定型句（一致しても同じ話題とは言えない語）
STOP_ENTITIES = {
    "プレスリリース", "お知らせ", "サービス", "スタート", "キャンペーン", "イベント", "オンライン", "セミナー",
    "プロジェクト", "システム", "ソリューション", "プラットフォーム", "コンテンツ", "グループ", "ニュース",
    "ツール", "ユーザー", "データ", "モデル", "アップデート", "リリース", "パートナー", "ビジネス", "テクノロジー",
    "コラボ", "シリーズ", "スペシャル", "フェア", "オリジナル", "コレクション", "グッズ", "ショップ", "ストア",
    "ブランド", "セット", "ランキング", "アンケート", "レポート", "マーケット", "フェス", "フェスタ", "ライブ",
    "スタンプラリー", "マルシェ", "メニュー", "スイーツ", "カフェ", "レストラン", "ホテル", "ツアー", "チケット",
    "サポート", "エリア", "センター", "スクール", "クラブ", "チーム", "ゲーム", "アプリ", "モバイル", "デジタル",
    "オンラインショップ", "ホームページ", "サイト", "ページ", "ウェブ", "インターネット", "スマートフォン",
    "ハロウィン", "クリスマス", "ギフト", "プラン", "パッケージ", "デザイン", "ポイント", "キャラクター",
    "the", "and", "for", "with", "new", "now", "from", "your", "how", "what", "why", "into",
    "inc", "ltd", "co", "jp", "com", "www", "http", "https", "2024", "2025", "2026", "2027",
    # 英語の見出しによく出る一般語（固有名詞ではない）
    "using", "use", "more", "build", "building", "data", "agent", "agents", "brings", "all", "about", "look", "does",
    "run", "app", "apps", "make", "our", "you", "can", "get", "turning", "this", "that", "are", "will", "its", "over",
    # 発表の定型語（「Introducing GPT-7」と「Introducing GPT-6」を同じ話題にしない）
    "introducing", "introduces", "introduce", "announcing", "announces", "announced", "launch", "launches", "launching",
    "launched", "available", "today", "meet", "update", "updates", "release", "released", "releases",
}
BRACKET_RE = re.compile(r"[「『【\[(（]([^」』】\])）]{2,30})[」』】\])）]")
KATAKANA_RE = re.compile(r"[ァ-ヴー・]{3,}")
LATIN_RE = re.compile(r"[a-z][a-z0-9+.\-]{1,}[a-z0-9+]|[a-z0-9]*\d[a-z0-9]*[a-z][a-z0-9]*")


def normalize(title: str) -> str:
    """全角半角・大小文字をそろえ、記号と空白を落とす"""
    text = unicodedata.normalize("NFKC", title or "").lower()
    return re.sub(r"[\W_]+", "", text)


def bigrams(title: str) -> set[str]:
    text = normalize(title)
    return {text[i : i + 2] for i in range(len(text) - 1)} if len(text) > 1 else {text}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def entities(*texts: str) -> set[str]:
    """固有名詞っぽい語（カタカナ3字以上・英数字の語・括弧内の語）"""
    found: set[str] = set()
    for raw in texts:
        text = unicodedata.normalize("NFKC", raw or "")
        lower = text.lower()
        for m in BRACKET_RE.finditer(text):
            found.add(normalize(m.group(1)))
        for m in KATAKANA_RE.finditer(text):
            word = m.group(0).strip("・ー")
            if len(word) >= 3:
                found.add(word)
        for m in LATIN_RE.finditer(lower):
            word = m.group(0).strip(".-")
            if len(word) >= 3:
                found.add(word)
    return {e for e in found if e and e not in STOP_ENTITIES}


class Index:
    """既存記事＋この実行で採用した候補の索引"""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.urls: set[str] = set()
        self.df: dict[str, int] = {}

    def add(self, title: str, url: str = "", extra: list[str] | None = None, label: str = "") -> None:
        ents = entities(title, *(extra or []))
        for e in ents:
            self.df[e] = self.df.get(e, 0) + 1
        self.entries.append({"title": title, "bigrams": bigrams(title), "entities": ents, "label": label or title})
        if url:
            self.urls.add(_norm_url(url))

    def has_url(self, url: str) -> bool:
        return bool(url) and _norm_url(url) in self.urls

    def find(self, title: str, url: str = "", extra: list[str] | None = None) -> str | None:
        """重複していれば理由（文字列）、していなければ None"""
        hit = self.match(title, url, extra)
        return hit[0] if hit else None

    def match(self, title: str, url: str = "", extra: list[str] | None = None) -> tuple[str, str] | None:
        """重複していれば (理由, 相手の label)。出典URLが同じときの label は空"""
        if url and _norm_url(url) in self.urls:
            return "同じ出典URL", ""
        grams = bigrams(title)
        ents = {e for e in entities(title, *(extra or [])) if self.df.get(e, 0) < COMMON_ENTITY_DF}
        # 当たった中でいちばん強い相手を返す（先に見つかった弱い一致で、続報の宛先を取り違えないため）
        best: tuple[tuple, str, str] | None = None
        for entry in self.entries:
            score = jaccard(grams, entry["bigrams"])
            label = entry["label"]
            shared = ents & entry["entities"]
            if score >= JACCARD_DUP:
                hit = (f"見出しが類似（{score:.2f}）: {label[:40]}", label)
            elif len(shared) >= 2:
                hit = (f"固有名詞が一致（{'・'.join(sorted(shared)[:3])}）: {label[:40]}", label)
            elif shared and score >= JACCARD_WITH_ENTITY:
                hit = (f"固有名詞＋見出しが類似（{next(iter(shared))} / {score:.2f}）: {label[:40]}", label)
            else:
                continue
            strength = (len(shared) + (2 if score >= JACCARD_DUP else 0), score)
            if best is None or strength > best[0]:
                best = (strength, *hit)
        return (best[1], best[2]) if best else None


def _norm_url(url: str) -> str:
    """クエリ・末尾の / を落とす。#entry-…（page_watch.py が付けるリリースノートの1項目）だけは残す"""
    url = url.strip().lower()
    frag = url.split("#", 1)[1] if "#entry-" in url else ""
    return re.sub(r"[?#].*$", "", url).rstrip("/") + (f"#{frag}" if frag else "")


def _article_time(data: dict) -> datetime | None:
    for key in ("generatedAt", "publishedAt"):
        try:
            value = datetime.fromisoformat(str(data.get(key, "")).replace("Z", "+00:00"))
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def load_recent(days: int = 4, skip_roundups: bool = False) -> Index:
    """直近 days 日に作った（または公開された）記事の索引。label は記事の slug"""
    index = Index()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    for path in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        when = _article_time(data)
        if when is None or when < since:
            continue
        if skip_roundups and data.get("type") == "roundup":
            continue
        keywords = [str(k) for k in (data.get("keywords") or [])][:8]
        index.add(str(data.get("title", "")), str(data.get("sourceUrl", "")), keywords, label=path.stem)
        # 続報で足した出典も「同じ出典URL」として扱う
        for update in data.get("updates") or []:
            if isinstance(update, dict) and update.get("sourceUrl"):
                index.urls.add(_norm_url(str(update["sourceUrl"])))
        # 元記事の見出し（英語ソースなら英語）でも当たるようにする
        if data.get("videoTitle") and data.get("videoTitle") != data.get("title"):
            index.add(str(data["videoTitle"]), "", None, label=path.stem)
    # 取り下げた記事（content/retired.json）の出典からは作り直さない（label の無い「同じ出典URL」として当てる）
    try:
        retired = json.loads(RETIRED_PATH.read_text(encoding="utf-8")).get("articles", {})
    except (OSError, json.JSONDecodeError):
        retired = {}
    for entry in retired.values():
        for key in ("sourceUrl", "videoUrl"):
            if isinstance(entry, dict) and entry.get(key):
                index.urls.add(_norm_url(str(entry[key])))
    return index


def main() -> int:
    index = load_recent()
    print(f"直近4日の記事 {len(index.entries)} 件を索引")
    for title in sys.argv[1:]:
        print(f"  {title[:60]} → {index.find(title) or '重複なし'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
