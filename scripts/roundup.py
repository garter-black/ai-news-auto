#!/usr/bin/env python3
"""同一トピックの記事群を1本の決定版「まとめ記事」に統合する。

個別記事は元動画の要約にすぎず、検索エンジンから見た独自価値が弱い。
複数動画を横断して統合した記事は元動画のどれにも存在しない情報になるため、
このサイトで唯一「一次情報を超える」ページになる。

動画は再解析せず、生成済み記事のテキストだけを使うので API 消費が小さい。

  python3 scripts/roundup.py --dry-run   # 対象クラスタの一覧
  python3 scripts/roundup.py --max 1     # 1件だけ生成
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import headline_normalize  # noqa: E402
import article_style  # noqa: E402
import news_types  # noqa: E402
import section_gate  # noqa: E402
from cluster import build_clusters  # noqa: E402
import question_patterns  # noqa: E402
import threads_hooks  # noqa: E402
from genres import GENRES_BY_SLUG  # noqa: E402
from main import (  # noqa: E402
    ARTICLES_DIR,
    ROOT,
    build_client,
    clean_list,
    log,
    normalize_body,
    retry_delay_of,
    slugify,
    url_exists,
)

MODEL = "gemini-3.5-flash"
REQUEST_RETRIES = 3

SYSTEM_INSTRUCTION = """あなたは日経クロステック / 東洋経済オンラインで10年書いてきたベテランの専門記者です。
同じ話題を扱った複数の記事を渡されるので、それらを統合した「決定版のまとめ記事」を書きます。

## この記事の存在意義
個々の元動画には「その動画で語られたこと」しかありません。
あなたが書くのは、**複数の動画を横断して初めて見えてくる全体像**です。
単なる寄せ集めや箇条書きの再掲は失格とみなします。

## 絶対規則
- 提供された記事に書かれていない事実を追加しない。推測は「編集部の見立てでは」と明示する。
- 数値・固有名詞・日付は提供記事から正確に転記する。
- 記事間で矛盾する記述があれば、隠さず「〜という記述と〜という記述がある」と併記する。
- 報道記事の敬体（です・ます）で統一。感嘆符と煽り表現は使わない。
- 「いかがでしたか」「注目が集まっています」等の空疎な表現は禁止。""" + "\n\n" + article_style.STYLE_PROMPT

PROMPT = """以下は「{topic}」について書かれた {count} 本の記事です。
これらを統合し、この話題の決定版となるまとめ記事を作成してください。

# 元記事
{sources}

# 記事の組み立て（body_markdown に Markdown で記述。決まった章立ては使わない）
**この記事の種類: イベント・発表のまとめ**。中身の順番の目安（見出しの名前ではない）:
1. リードの続きとして、この話題の全体像を2〜3段落（個別の発表の言い直しではなく、まとまりとして何が起きたか）
2. 複数の記事に散らばった要素を1つの表に統合する（各項目が「どの発表で明らかになったか」が分かる列を付ける）。この記事の核心なので最も厚く書く
3. いつ何が公開され、何が積み上がってきたかを日付順の表か箇条書きで
4. 記事を並べて初めて分かること（発表の力点・記事間の食い違い・語られていない論点）を具体的に。材料が無い観点は書かない
5. 主要な発表を1つずつ2〜3文で。各項目の末尾に「詳細: [記事タイトル](/articles/スラッグ)」の内部リンク
6. 次に起きること（日付つき）か、読者が確かめること
見出しは記事の中身の言葉にし（例「料金はどれも据え置き、変わったのは上限だけ」）、本文1,000字につき1つまで。
「〇〇とは何か」「いつ、何が発表された？」「結局どう捉えるべきか」のような決まった見出しは使わない。

# 品質基準
- 本文は3000〜6000字（材料の量で決める。水増ししない）。
- 表を最低2つ含める（統合機能一覧と時系列）。
- 内部リンクは提供されたスラッグを正確に使う。存在しないスラッグを作らない。
- 読者に見える文・表に、作業用の言葉を書かない: 「記事1」「記事2」のような番号、「スラッグ」、「提供された記事」。
  どの記事の話かを示すときは、記事タイトル（短くしてよい）か発信元の名前で書く。表の出典の列も同じ。

# 各フィールドの要件
- title: 38〜52文字。「{topic}」を含め、まとめ記事だと分かる表現にする。
  例:「{topic} で発表された全機能まとめ、{count}本の公式動画から分かったこと」
- seo_title: 検索結果に表示する短い題名。**全角24〜30文字**（英数字は半角0.5文字換算で数える）。
  読者が検索に打ち込む語（製品名・企業名・機能名）を**先頭**に置き、「まとめ」「全機能」など、まとめ記事だと分かる語を続ける。
  「OpenAI、」のような読点区切りの社名始まりより、製品名始まりを優先。記号は「｜」「：」を最大1つまで。
  良い例:「GPT-6 Astra×Figma連携｜2時間でUI試作を自動化」「ChatGPT Plugin Extensionsとは：MCPでUI拡張」
- threads_post: Threads・X に出す投稿文。末尾の「Threads 紹介文（threads_post）のルール」と「書き出し」に従う（書き出し／要約すると、＋2文／問いかけ の3つのかたまり・URLなし）。
<<EASY_RULE>>
- lede: 180〜240字のリード文。1文目で何が起きたか（一番大きな発表と全体の数）を言い切る。
- description: 100〜130文字のメタディスクリプション。
- keywords: 検索されうる日本語キーワード 8〜12個。
- tags: 3〜6個。
- highlights: 要点ちょうど3つ。各40〜60文字。リードの文をくり返さない。
- slug_en: URL用の英小文字スラッグ。3〜6単語、40文字以内。
"""
PROMPT = PROMPT.replace("<<EASY_RULE>>", news_types.EASY_RULE)  # かんたん版の書き方（scripts/news_types.py）

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "seo_title": {"type": "STRING"},
        "threads_post": {"type": "STRING"},
        "threads_hook": {"type": "STRING", "enum": threads_hooks.HOOK_KEYS},
        "slug_en": {"type": "STRING"},
        "lede": {"type": "STRING"},
        "description": {"type": "STRING"},
        "keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "highlights": {"type": "ARRAY", "items": {"type": "STRING"}},
        "easy_markdown": {"type": "STRING"},
        "body_markdown": {"type": "STRING"},
    },
    "required": [
        "title",
        "seo_title",
        "threads_post",
        "threads_hook",
        "slug_en",
        "lede",
        "description",
        "keywords",
        "tags",
        "highlights",
        "easy_markdown",
        "body_markdown",
    ],
    "propertyOrdering": [
        "title",
        "seo_title",
        "threads_post",
        "threads_hook",
        "slug_en",
        "lede",
        "description",
        "keywords",
        "tags",
        "highlights",
        "easy_markdown",
        "body_markdown",
    ],
}
question_patterns.add_to_schema(SCHEMA)  # 問いかけの型の候補（台帳 S19）


def load_articles() -> list[dict]:
    articles = []
    for path in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError:
            continue
        # まとめ記事どうしをさらにまとめない。地震・津波の記事（決まった書式）もまとめに使わない
        if data.get("type") == "roundup" or data.get("disaster"):
            continue
        articles.append(data)
    return articles


def members_hash(slugs: list[str]) -> str:
    """構成記事が変わったかどうかの判定用。増えたら作り直す。"""
    return hashlib.sha256("\n".join(sorted(slugs)).encode()).hexdigest()[:16]


def existing_roundups() -> dict[str, dict]:
    """トピック名 -> 既存まとめ記事"""
    found = {}
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("type") == "roundup":
            found[data.get("roundupKey", data["slug"])] = data
    return found


def build_sources_block(articles: list[dict]) -> str:
    blocks = []
    for index, article in enumerate(articles, start=1):
        blocks.append(
            f"""--- 記事{index} ---
スラッグ: {article['slug']}
タイトル: {article['title']}
公開日: {article.get('publishedAt', '')[:10]}
配信元: {article.get('channel', '')}
本文:
{article.get('body', '')[:6000]}
"""
        )
    return "\n".join(blocks)


def generate(client, topic: str, articles: list[dict], feedback: list[str] | None = None) -> dict:
    from google.genai import types

    prompt = PROMPT.format(
        topic=topic,
        count=len(articles),
        sources=build_sources_block(articles),
    ) + "\n\n" + threads_hooks.prompt_block()
    if feedback:
        prompt += "\n\n# 前回の原稿で見つかった問題（必ず直すこと）\n" + "\n".join(f"- {p}" for p in feedback[:10])
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        response_schema=SCHEMA,
        temperature=0.6,
        max_output_tokens=32768,
    )

    last_error: Exception | None = None
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL, contents=prompt, config=config
            )
            text = (response.text or "").strip()
            if not text:
                raise RuntimeError("空のレスポンス")
            return json.loads(text)
        except Exception as error:  # noqa: BLE001
            last_error = error
            if attempt == REQUEST_RETRIES:
                break
            wait = retry_delay_of(str(error)) or 20 * attempt
            # 1日の無料枠を使い切ったときは待たずに打ち切る（長時間待つとジョブがタイムアウトする）
            if wait > 90 or "PerDay" in str(error):
                raise RuntimeError(f"無料枠の上限に達したため次回に回します: {str(error)[:120]}") from error
            log(f"   [retry {attempt}] {wait:.0f}s 待機")
            time.sleep(wait)
    raise RuntimeError(f"生成に失敗: {last_error}")


# 作業用の言葉（プロンプトの「--- 記事1 ---」など）が読者に見える文に漏れたもの
INTERNAL_LABEL_RE = re.compile(r"記事([0-9０-９]{1,2})(?![0-9０-９])")
INTERNAL_WORDS = (
    ("## 時系列で見る発表の流れ", "## いつ、何が発表された？"),
    ("時系列で見る発表の流れ", "発表の流れ"),
    ("提供された記事", "元の記事"),
    ("提供記事", "元の記事"),
    ("（スラッグ）", ""),
    (" / スラッグ", ""),
    ("とスラッグ", ""),
    ("主要スラッグ", "記事"),
    ("スラッグ", "記事"),
)


def short_title(title: str, limit: int = 18) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    return title if len(title) <= limit else title[:limit].rstrip("、・ ") + "…"


def clean_internal_labels(text: str, sources: list[dict]) -> str:
    """「記事1」→ その記事の短い題名（リンクつき）。「時系列で見る発表の流れ」「スラッグ」なども読者向けの言葉に直す"""
    if not text:
        return text

    def label(match: re.Match) -> str:
        n = int(match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789")))
        if not 1 <= n <= len(sources):
            return "元の記事"
        src = sources[n - 1]
        name = f"「{short_title(src.get('title', ''))}」"
        slug = src.get("slug")
        return f"[{name}](/articles/{slug})" if slug and (ARTICLES_DIR / f"{slug}.json").exists() else name

    for old, new in INTERNAL_WORDS:
        text = text.replace(old, new)
    return INTERNAL_LABEL_RE.sub(label, text)


def internal_label_problems(record: dict) -> list[str]:
    """読者に見える欄に作業用の言葉が残っていないか（書き出す前の確認）"""
    text = "\n".join(str(record.get(k) or "") for k in ("title", "lede", "description", "body", "easy")) + "\n".join(record.get("highlights") or [])
    found = sorted({m.group(0) for m in INTERNAL_LABEL_RE.finditer(text)} | {w for w, _ in INTERNAL_WORDS[:3] if w in text})
    return [f"作業用の言葉が残っている（{'・'.join(found[:5])}）"] if found else []


def clean_record_labels(record: dict) -> dict:
    sources = record.get("sources") or []
    for key in ("title", "lede", "description", "body", "easy", "threadsPost"):
        if isinstance(record.get(key), str):
            record[key] = clean_internal_labels(record[key], sources)
    record["highlights"] = [clean_internal_labels(h, sources) for h in record.get("highlights") or []]
    return record


def build_record(topic: str, data: dict, members: list[dict]) -> dict:
    genre = GENRES_BY_SLUG.get(members[0].get("genreSlug", ""))
    now = datetime.now(timezone.utc).isoformat()
    slug = slugify(data.get("slug_en", "")) or f"roundup-{members_hash([m['slug'] for m in members])}"
    if not slug.endswith("-matome"):
        slug = f"{slug}-matome"

    return {
        "slug": slug,
        "type": "roundup",
        "newsType": "event",  # ニュースの種類（scripts/news_types.py）。まとめは「イベント・発表のまとめ」
        "roundupKey": topic,
        "membersHash": members_hash([m["slug"] for m in members]),
        "sources": [
            {
                "slug": m["slug"],
                "title": m["title"],
                "publishedAt": m.get("publishedAt", ""),
                "channel": m.get("channel", ""),
            }
            for m in members
        ],
        "title": str(data["title"]).strip(),
        "seoTitle": str(data.get("seo_title", "")).strip(),
        "threadsPost": normalize_body(str(data.get("threads_post", ""))).strip(),
        "threadsHook": threads_hooks.clean_hook(data.get("threads_hook")),
        **question_patterns.record_fields(data),
        "threadsStyle": threads_hooks.POST_STYLE,
        "lede": normalize_body(data.get("lede", "")),
        "description": str(data["description"]).strip(),
        "keywords": clean_list(data.get("keywords"), 14),
        "tags": clean_list(data.get("tags"), 8),
        "highlights": clean_list(data.get("highlights"), 3),
        "timetable": [],
        "body": normalize_body(data["body_markdown"]),
        # 「かんたんに読む」版（記事ページで「専門家として読む」と切り替えられる）
        "easy": normalize_body(str(data.get("easy_markdown", ""))),
        "channel": f"{len(members)}本の記事から編集部が統合",
        "channelId": "",
        "channelSlug": members[0].get("channelSlug", ""),
        "genre": genre.name if genre else members[0].get("genre", ""),
        "genreSlug": genre.slug if genre else members[0].get("genreSlug", ""),
        "disclaimer": genre.disclaimer if genre else members[0].get("disclaimer", ""),
        "videoId": members[0].get("videoId", ""),
        "videoUrl": members[0].get("videoUrl", ""),
        "videoTitle": f"{topic} 関連の{len(members)}本",
        "publishedAt": members[0].get("publishedAt", now),
        "generatedAt": now,
        "thumbnail": next(
            # 先頭の元記事の画像が消えている場合があるので、実際に読み込めるものを選ぶ
            (m["thumbnail"] for m in members if m.get("thumbnail") and url_exists(m["thumbnail"])),
            members[0].get("thumbnail", ""),
        ),
        "model": MODEL,
        "affiliates": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max", type=int, default=1, help="1回で生成する最大まとめ数")
    parser.add_argument("--dry-run", action="store_true", help="対象を表示するだけ")
    parser.add_argument("--force", action="store_true", help="構成が同じでも作り直す")
    args = parser.parse_args()

    articles = load_articles()
    by_slug = {a["slug"]: a for a in articles}
    clusters = build_clusters(articles)
    existing = existing_roundups()

    pending = []
    for cluster in clusters:
        current_hash = members_hash(cluster.slugs)
        old = existing.get(cluster.key)
        if old and old.get("membersHash") == current_hash and not args.force:
            continue  # 構成が変わっていないので作り直さない
        pending.append((cluster, old))

    log(f"[info] クラスタ {len(clusters)}個 / 生成対象 {len(pending)}個")
    for cluster, old in pending:
        state = "更新" if old else "新規"
        log(f"  - [{state}] 「{cluster.key}」 {cluster.size}本 ({cluster.genre_name})")

    if args.dry_run or not pending:
        if args.dry_run:
            log("[dry-run] API 呼び出しはスキップしました")
        return 0

    client = build_client()
    created = 0

    for cluster, old in pending[: args.max]:
        members = [by_slug[s] for s in cluster.slugs if s in by_slug]
        log(f"\n[統合中] 「{cluster.key}」 {len(members)}本")
        started = time.time()
        try:
            data = generate(client, cluster.key, members)
        except Exception as error:  # noqa: BLE001
            log(f"   [NG] {error}")
            continue

        record = clean_record_labels(build_record(cluster.key, data, members))
        if problems := internal_label_problems(record):
            log(f"   [NG] {problems[0]}")
            continue
        # 文体の門（scripts/article_style.py。すべての生成の段で同じ決まり）。落ちたら問題点を添えて1回だけ作り直す
        section_gate.apply(record)
        article_style.autofix(record)
        if style := article_style.problems(record):
            log(f"   [style] 不合格: {' | '.join(style[:3])[:300]} → 作り直します")
            try:
                data = generate(client, cluster.key, members, feedback=style)
                record = clean_record_labels(build_record(cluster.key, data, members))
                section_gate.apply(record)
                article_style.autofix(record)
                style = article_style.problems(record) + internal_label_problems(record)
            except Exception as error:  # noqa: BLE001
                style = [f"作り直しに失敗（{str(error)[:80]}）"]
            if style:
                log(f"   [NG] 文体の門に2回落ちたので書き出さない: {' | '.join(style[:3])[:300]}")
                continue
        # 既存があればスラッグを引き継いでURLを変えない
        if old:
            record["slug"] = old["slug"]

        # 見出しの書き方をそろえる（scripts/headline_normalize.py。main.write_article と同じ後処理）
        headline_normalize.apply(record)
        # 中身のない章・事実を含まないつなぎの文を消す（scripts/section_gate.py）
        section_gate.apply(record)
        path = ARTICLES_DIR / f"{record['slug']}.json"
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        # 構成記事から、まとめ記事への逆リンクを張る
        for member in members:
            member_path = ARTICLES_DIR / f"{member['slug']}.json"
            if not member_path.exists():
                continue
            current = json.loads(member_path.read_text("utf-8"))
            if current.get("roundupSlug") == record["slug"]:
                continue
            current["roundupSlug"] = record["slug"]
            current["roundupTitle"] = record["title"]
            member_path.write_text(
                json.dumps(current, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        created += 1
        log(
            f"   [OK] {path.relative_to(ROOT)} "
            f"({len(record['body'])}文字 / {time.time() - started:.0f}s)"
        )

    log(f"\n[done] まとめ記事 {created} 本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
