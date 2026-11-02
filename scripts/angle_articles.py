"""
大きなニュースを、別の角度（別の疑問・別の読者）から記事にする。

同じ内容の言い換えは重複と見なされるので、角度ごとに「答える疑問」と「読者」を変え、
使う事実は公式の発表（と、うちの過去記事）だけに限る。元の記事へのリンクも必ず入れる。

使い方:
  .venv/bin/python scripts/angle_articles.py --base <元記事slug> --source <URL> [--source <URL>...] --angle "見出しの方向|狙う疑問|読者" ...
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive  # noqa: E402
import feeds  # noqa: E402
import llm  # noqa: E402
import main as m  # noqa: E402
import quality  # noqa: E402
import web_articles as w  # noqa: E402
from genres import AI  # noqa: E402

ANGLE_NOTE = """
# 今回の記事の角度（必ず守る）
すでに当サイトには、この発表全体を解説した記事「{base_title}」（/articles/{base_slug}）があります。
今回はそれとは別に、次の角度だけに絞った記事を書いてください。全体の解説を繰り返さないこと。
- 記事の方向: {direction}
- この記事が答える疑問: {question}
- 想定する読者: {reader}
- title と seo_title は、この疑問がそのまま検索されたときに見つかる言い方にする（元記事と同じ題名にしない）。
- 本文の冒頭近くで、全体の解説は元記事へ、と一文で案内し、[{base_title}](/articles/{base_slug}) へリンクする。
- 使う事実は、下の一次情報と、渡された過去記事の内容だけ。書かれていないことは「公式発表には記載がありません」と書く。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--angle", action="append", required=True, help="方向|疑問|読者")
    parser.add_argument("--tier", default="quality")
    args = parser.parse_args()
    llm._load_env_local()  # noqa: SLF001

    base = json.loads((m.ARTICLES_DIR / f"{args.base}.json").read_text("utf-8"))
    pages = []
    for url in args.source:
        page = feeds.fetch_article_text(url)
        pages.append((url, page))
        print(f"[source] {url} {len(page['text'])}字")
    text = "\n\n".join(f"【出典: {p['title'] or url}（{url}）】\n{p['text']}" for url, p in pages)

    arch = archive.Archive()
    related = [r for r in arch.related(base["title"], text[:3000], genre_slug="ai") if r["slug"] != args.base]
    base_row = {"slug": args.base, "title": base["title"]}
    context = archive.prompt_block(related)

    for spec in args.angle:
        direction, question, reader = (spec.split("|") + ["", "", ""])[:3]
        item = {
            "id": f"angle-{args.base}-{abs(hash(spec)) % 10**8}",
            "url": pages[0][0],
            "title": pages[0][1]["title"] or base["videoTitle"],
            "source_name": base.get("sourceName") or base["channel"],
            "source_slug": base.get("channelSlug") or "openai-news",
            "published_at": datetime.fromisoformat(base["publishedAt"]),
            "image": base.get("thumbnail"),
        }
        voice_key, voice_text = m.pick_voice()
        system = f"{m.SYSTEM_INSTRUCTION_BASE}\n\n{AI.persona}\n\n{w.WEB_SOURCE_NOTE}\n\n## 今回の語り口\n{voice_text}"
        note = ANGLE_NOTE.format(base_title=base["title"], base_slug=args.base, direction=direction, question=question, reader=reader)
        prompt = w.build_prompt(item, text, context + note)
        problems: list[str] = []
        for attempt in (1, 2):
            full = prompt + (quality.feedback_block(problems) if problems else "")
            data, model_name = llm.generate_json(system, full, w.WEB_SCHEMA, temperature=0.6, max_output_tokens=32768, tier=args.tier)
            record = w.build_web_record(item, data, {"image": base.get("thumbnail")}, model_name, voice_key)
            record = w.attach_related(record, data, related + [base_row])
            # 元記事へのリンクは必ず残す
            record["relatedSlugs"] = list(dict.fromkeys([args.base] + record.get("relatedSlugs", [])))
            # 意図して角度を変えた解説（scripts/story.py が重複にしない。ニュース用サイトマップには載せない）
            record["angleOf"] = args.base
            verdict = quality.evaluate(record, text, "ai", archive.check_block(related))
            print(f"[gate] {direction[:20]} 試行{attempt} accepted={verdict.get('ok')} score={verdict.get('score')} {verdict.get('problems', [])[:2]}")
            if verdict.get("soft"):
                # 読みやすさだけ基準外: 文を分ける書き直しを1回。だめでも元の原稿で受理する
                polished, info = quality.polish(record, text, "ai", archive.check_block(related))
                print(f"[read] {verdict.get('readability')} → {info.get('readability') if polished else info}")
                if polished is not None:
                    record, verdict = polished, info
            if verdict.get("ok") or verdict.get("soft"):
                record["qualityScore"] = verdict.get("score")
                path = m.write_article(record)
                print(f"[OK] {path.name} {record['title']} / {record['seoTitle']} / {len(record['body'])}字 読みやすさ={verdict.get('readability')}")
                break
            problems = verdict.get("problems", [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
