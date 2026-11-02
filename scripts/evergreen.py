#!/usr/bin/env python3
"""使い方ガイド（/guides/<slug>）を作る・確認し直す。ニュースとは別のレーン（ニュースの1日上限に数えない）。

「ChatGPT 使い方」「Gemini 無料 どこまで」のように何か月も検索される疑問に、公式のヘルプ・料金ページだけを
材料に答える。予定表は content/evergreen_plan.json（上から順に作る）、出力は content/howto/<slug>.json。

  python3 scripts/evergreen.py --list                  # 予定と作成済みを表示
  python3 scripts/evergreen.py --max 1 --daily-cap 4   # 未作成を上から1本（今日の作成が4本未満のとき）
  python3 scripts/evergreen.py --only chatgpt-free-plan --force
  python3 scripts/evergreen.py --refresh --refresh-max 2   # 14日たった・関連ニュースが出たガイドを確認し直す

方針:
- 事実は「いま取得した公式ページの文字」だけ。料金・回数などの数字は公式ページにそのまま載っているかを機械的に照合する。
- 別のモデルで事実確認（scripts/quality.py の fact_check）。致命的な誤りがあれば公開しない。
- 使った・試したとは書かない。広告っぽい誇張（最強・絶対など）を書かない。
- 確認し直し: 公式ページを取り直し、今のガイドを事実確認する。合っていれば確認日（checkedAt）だけ更新、
  ずれていれば作り直す。作り直しに失敗したら古い版を残し、確認日は更新しない（needsReview を立てる）。
- refreshMode: editorial の手動原稿は自動生成・確認日の更新をしない。期限や関連ニュースを検知したら
  needsReview と needsReviewReason を記録する。編集者が再確認し checkedAt を更新した際に両方を消す。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feeds  # noqa: E402
import llm  # noqa: E402
import persona  # noqa: E402
import quality  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "content" / "evergreen_plan.json"
OUT_DIR = ROOT / "content" / "howto"
ARTICLES = ROOT / "content" / "articles"
# 合格しなかった題材（しばらく作り直さない）。state/ はビルド対象外
FAILED = ROOT / "state" / "evergreen_failed.json"
RETRY_DAYS = 3
JST = timezone(timedelta(hours=9))

# 原稿の作り方を変えたら上げる（確認し直しのときに作り直す）
PROMPT_VERSION = 1
REFRESH_DAYS = 14
PASS_SCORE = 75
MIN_PROSE = 1800  # 地の文（結論・手順・料金の説明・FAQ など）の合計
SOURCE_GEN_CAP = 9000  # 生成に渡す1ページあたりの文字数
SOURCE_TOTAL_CAP = 26000
MIN_SOURCE = 400

BANNED_RE = re.compile(
    r"(使って|試して|触って)み(た|ました)|実際に(使|試)|使用感|検証し(た|ました)|検証結果|編集部が(使|試)|レビューし|"
    r"★|☆|点満点|総合評価|口コミ|評判|最強|圧倒的|絶対|今すぐ(登録|購入)|必見"
)
# 公式ページが取れていない（ボット除け・エラー画面）
BROKEN_RE = re.compile(r"Page cannot be found|Just a moment|Enable JavaScript and cookies|Error - Office\.com|そのページは存在しません|Something went wrong")
# 数字に付く単位。これらの数字は公式ページの文字にそのまま載っていないといけない
UNIT_NUM_RE = re.compile(
    r"(?:US\$|\$|￥|¥)\s?\d[\d,，]*(?:\.\d+)?|\d[\d,，]*(?:\.\d+)?\s?(?:円|ドル|回|枚|件|GB|TB|MB|ファイル|個|本|分|時間|語|文字|ページ|%|％|日間|か月|カ月|ヶ月)"
)
# 料金・提供条件が変わったかもしれないニュース（見出しに出る語）
PRICE_NEWS_RE = re.compile(r"料金|価格|値上げ|値下げ|無料|有料|プラン|月額|年額|上限|制限|提供開始|日本で|日本語|終了|廃止|名称|改称|Plus|Pro|Go\b")


def log(message: str) -> None:
    print(message, flush=True)


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def today_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return default


def load_plan() -> list[dict]:
    return load_json(PLAN, {}).get("topics", [])


def existing() -> dict[str, dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return {p.stem: load_json(p, {}) for p in sorted(OUT_DIR.glob("*.json"))}


def write(guide: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{guide['slug']}.json").write_text(json.dumps(guide, ensure_ascii=False, indent=2) + "\n", "utf-8")


def is_editorial(guide: dict | None) -> bool:
    return (guide or {}).get("refreshMode") == "editorial"


# --------------------------------------------------------------------------- #
# 公式ページ
# --------------------------------------------------------------------------- #


def fetch_sources(urls: list[str]) -> list[dict]:
    """[{url, title, text}]。取れなかったページは除く"""
    out = []
    for url in urls:
        try:
            page = feeds.fetch_article_text(url)
        except Exception as error:  # noqa: BLE001
            log(f"   取得失敗 {url}: {str(error)[:80]}")
            continue
        text = (page.get("text") or "").strip()
        title = (page.get("title") or "").strip()
        if len(text) < MIN_SOURCE or BROKEN_RE.search(f"{title} {text[:300]}"):
            log(f"   使えない {url}（{len(text)}字）")
            continue
        out.append({"url": url, "title": title[:120], "text": text})
    return out


def source_block(sources: list[dict], cap_each: int) -> str:
    return "\n\n".join(f"[S{i + 1}] {s['title']}\nURL: {s['url']}\n<<<\n{s['text'][:cap_each]}\n>>>" for i, s in enumerate(sources))


def source_hash(sources: list[dict]) -> str:
    joined = "\n".join(re.sub(r"\s+", "", s["text"]) for s in sources)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def _digits_norm(text: str) -> str:
    return re.sub(r"(?<=\d)[,，](?=\d{3})", "", text or "").replace("￥", "¥").replace("％", "%")


def unverified_numbers(text: str, sources: list[dict]) -> list[str]:
    """単位つきの数字のうち、公式ページの文字に載っていないもの"""
    norm = _digits_norm("\n".join(s["text"] for s in sources))
    bad = []
    for m in UNIT_NUM_RE.finditer(text or ""):
        num = re.search(r"\d[\d,，]*(?:\.\d+)?", m.group(0)).group(0)
        n = _digits_norm(num)
        if not re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", norm) and m.group(0) not in bad:
            bad.append(m.group(0))
    return bad


# --------------------------------------------------------------------------- #
# 関連ニュース
# --------------------------------------------------------------------------- #


def related_news(pattern: str, since: str = "", limit: int = 6) -> list[dict]:
    """ガイドの題材に関係する AI 記事（新しい順）。since があればそれより後に作られた記事だけ"""
    try:
        regex = re.compile(pattern)
    except re.error:
        return []
    try:
        since_at = datetime.fromisoformat(since) if since else None
    except ValueError:
        since_at = None
    rows = []
    for path in ARTICLES.glob("*.json"):
        a = load_json(path, {})
        if a.get("genreSlug", "ai") != "ai" or a.get("indexable") is False:
            continue
        if not regex.search(f"{a.get('title', '')} {a.get('seoTitle') or ''}"):
            continue
        try:
            made = datetime.fromisoformat(a.get("generatedAt") or a.get("publishedAt") or "")
        except ValueError:
            continue
        if since_at and made <= since_at:
            continue
        rows.append({"slug": a.get("slug"), "title": a.get("title", ""), "at": made})
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows[:limit]


# --------------------------------------------------------------------------- #
# 原稿
# --------------------------------------------------------------------------- #

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "seo_title": {"type": "STRING"},
        "description": {"type": "STRING"},
        "conclusion": {"type": "ARRAY", "items": {"type": "STRING"}},
        "intro": {"type": "STRING"},
        "steps": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"title": {"type": "STRING"}, "body": {"type": "STRING"}}, "required": ["title", "body"]}},
        "pricing": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"plan": {"type": "STRING"}, "price": {"type": "STRING"}, "note": {"type": "STRING"}}, "required": ["plan", "price"]}},
        "free_tier": {"type": "ARRAY", "items": {"type": "STRING"}},
        "japan_answer": {"type": "STRING"},
        "japan_body": {"type": "STRING"},
        "tips": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"title": {"type": "STRING"}, "body": {"type": "STRING"}}, "required": ["title", "body"]}},
        "faq": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"q": {"type": "STRING"}, "a": {"type": "STRING"}}, "required": ["q", "a"]}},
        "work_money": {"type": "ARRAY", "items": {"type": "STRING"}},
        "used_sources": {"type": "ARRAY", "items": {"type": "INTEGER"}},
    },
    "required": ["title", "seo_title", "description", "conclusion", "intro", "steps", "pricing", "free_tier", "japan_answer", "japan_body", "tips", "faq", "work_money", "used_sources"],
}

SYSTEM = """あなたはIT解説サイトの編集者です。AIツールの初心者に向けて、公式のヘルプ・料金ページだけを根拠に、正確でやさしい解説ページを書きます。
読者はスマホで検索してきた人です。結論を先に書き、1文は短く、専門用語には短い説明を添えます。出力は指定の JSON だけ。

""" + persona.PLAIN_STYLE

PROMPT = """# 書くページ
- 検索キーワード: {keyword}（ほかに: {queries}）
- 題材: {tool}
- 読者の目的: {intent}
- 仮の題名: {title}
- 今日の日付: {today}

# 公式ページ（いま取得したもの。これだけが事実の根拠。メニュー等の残骸は無視）
{sources}

# 書き方のルール（必ず守る）
- 事実（料金・回数・上限・対応機種・提供地域・機能名・モデル名・手順の画面名）は上の公式ページに書かれていることだけ。書かれていないことは「公式ページでは案内されていない」とするか、触れない。
  公式ページどうしで食い違うときは、料金ページ・更新日の新しいページを優先し、古い情報（古いモデル名など）は書かない。
- 料金・回数・容量などの数字は、公式ページの表記どおりに写す（円・ドルの換算や概算はしない。「約」も付けない）。
- 使った・試したとは書かない（「実際に使うと」「使ってみた」は禁止）。点数・おすすめ度・最強・絶対・評判は書かない。
- 宣伝の口調にしない。有料プランを勧めない。無料でできることを先に、正直に書く。
- 1文は50字以内を目安に、1段落は1〜3文。体言止めの連続や「〜となっております」は使わない。
- 名称が変わった製品は、新しい名前を主に書き、旧名も併記する（例: Gemini Notebook（旧 NotebookLM））。

# 各項目
- title: 32〜48字。検索キーワードを自然に含め、読者の疑問に答える形。感嘆符なし。年は入れない。
- seo_title: 28〜40字。title を短くしたもの。
- description: 90〜120字。結論を含む要約。
- conclusion: 結論を3〜4個。各40〜70字。最初の1つでキーワードの疑問に直接答える。
- intro: 120〜220字。このページで何がわかるか、題材が何か。
- steps: 手順を3〜7個。title は「〜する」の形で短く、body は80〜200字で画面上の操作を具体的に（公式ページにある範囲で）。
- pricing: プランごとの料金。公式ページに載っているプランだけ。price は表記どおり（例「$20/月」「￥0」）。載っていなければ空配列。note に主な違いを短く。
- free_tier: 無料でできること・無料版の制限を3〜6個（公式ページにある範囲で。各30〜80字）。
- japan_answer: 「日本で使える？」への一言の答え（20字以内。例「使えます（日本語対応）」）。公式ページで確認できないなら「公式ページで確認を」。
- japan_body: 日本での提供状況・日本語対応・円での支払いについて、公式ページで分かること（100〜250字）。分からないことは分からないと書く。
- tips: 初心者がつまずきやすい点・安全に使うための注意を2〜4個（body は60〜160字）。個人情報や機密を入力しない等の一般的な注意は書いてよい。
- work_money: 「{work_money_heading}」欄。2〜3個、各25〜70字の1文。仕事・副業でどんな作業に使えるか、無料でどこまでできるか・
  有料にする前に何を確かめるかなど、読者が明日から動ける形で書く。料金などの数字は公式ページの表記どおりだけ（節約額・短縮時間を計算しない）。
  「稼げる」「儲かる」など儲け話にしない。有料プランを勧めない。
- faq: よくある質問を4〜6個。検索されやすい形の質問（「〇〇は無料で使える？」など）にし、a は60〜180字で結論から。
- used_sources: 実際に根拠にした公式ページの番号（S1 なら 1）。
- 分量: intro・steps・japan_body・tips・faq の地の文の合計を2200字以上にする（足りないときは手順と FAQ を公式ページの事実で具体的に。推測で水増ししない）。
{feedback}"""


def prose_of(data: dict) -> str:
    """読みやすさ・文字数の確認用に、地の文を Markdown の段落にする"""
    parts = [data.get("intro", "")]
    parts += [f"## {s.get('title', '')}\n\n{s.get('body', '')}" for s in data.get("steps", [])]
    parts += [data.get("japan_body", "")]
    parts += [f"## {t.get('title', '')}\n\n{t.get('body', '')}" for t in data.get("tips", [])]
    parts += [f"## {f.get('q', '')}\n\n{f.get('a', '')}" for f in data.get("faq", [])]
    return "\n\n".join(p for p in parts if p)


def all_text(data: dict) -> str:
    rows = [f"{p.get('plan', '')} {p.get('price', '')} {p.get('note', '')}" for p in data.get("pricing", [])]
    return "\n".join(
        [data.get("title", ""), data.get("description", ""), *data.get("conclusion", []), prose_of(data), *rows, *data.get("free_tier", []), data.get("japan_answer", ""),
         *data.get("work_money", [])]
    )


def structural_problems(data: dict, sources: list[dict]) -> list[str]:
    problems = []
    title = data.get("title", "")
    if not 24 <= len(title) <= 56:
        problems.append(f"title の長さが不適切（{len(title)}字 / 32〜48字）")
    if re.search(r"[！!]", f"{title}{data.get('seo_title', '')}"):
        problems.append("title / seo_title に感嘆符がある")
    if not 3 <= len(data.get("conclusion", [])) <= 4:
        problems.append("conclusion は3〜4個")
    if not 3 <= len(data.get("steps", [])) <= 7:
        problems.append("steps は3〜7個")
    if not 4 <= len(data.get("faq", [])) <= 6:
        problems.append("faq は4〜6個")
    if not 1 <= len(data.get("work_money", [])) <= 3:
        problems.append("work_money は1〜3個")
    problems += persona.work_money_problems(persona.clean_work_money({"relevance": "high", "points": data.get("work_money", [])}))
    length = len(re.sub(r"\s|#", "", prose_of(data)))
    if length < MIN_PROSE:
        problems.append(f"本文が短い（{length}字 / 基準 {MIN_PROSE}字以上）。公式ページの事実を使って手順・FAQ を具体的に書き足す")
    problems += quality.readability_problems(prose_of(data))
    text = all_text(data)
    if m := BANNED_RE.search(text):
        problems.append(f"使ってはいけない表現がある（「{m.group(0)}」）")
    if m := quality.ENGLISH_RUN_RE.search(text):
        problems.append(f"訳されていない英文が残っている（「{m.group(0)[:40]}…」）")
    bad = unverified_numbers(text, sources)
    if bad:
        problems.append(f"公式ページに無い数字がある（{'、'.join(bad[:6])}）。公式ページの表記どおりに直すか削る（換算・概算はしない）")
    return problems


def to_record(data: dict) -> dict:
    """quality.fact_check に渡す形"""
    rows = "\n".join(f"- {p.get('plan')}: {p.get('price')}（{p.get('note', '')}）" for p in data.get("pricing", []))
    free = "\n".join(f"- {f}" for f in data.get("free_tier", []))
    body = f"{prose_of(data)}\n\n## 料金\n{rows}\n\n## 無料でできること\n{free}\n\n## 日本で使える？\n{data.get('japan_answer', '')}"
    # 「仕事・お金にどう活かせる？」欄も事実確認に入れる（quality._article_for_check が本文より前に置く）
    work_money = persona.clean_work_money({"relevance": "high", "points": data.get("work_money") or []})
    return {"title": data.get("title", ""), "lede": data.get("intro", ""), "highlights": data.get("conclusion", []), "body": body,
            "workMoney": work_money}


def check(data: dict, sources: list[dict]) -> dict:
    """{ok, problems, fact}"""
    problems = structural_problems(data, sources)
    fact = None
    if not problems:
        cap = max(2500, 12500 // max(1, len(sources)))
        fact = quality.fact_check(to_record(data), source_block(sources, cap))
        if fact["fatal"] or fact["score"] < PASS_SCORE:
            problems += [f"事実確認: {p}" for p in fact["problems"]] or [f"事実確認の点数が低い（{fact['score']}）"]
    return {"ok": not problems, "problems": problems, "fact": fact}


def generate(topic: dict, sources: list[dict], attempts: int = 3) -> tuple[dict, dict] | None:
    cap = max(3000, min(SOURCE_GEN_CAP, SOURCE_TOTAL_CAP // max(1, len(sources))))
    feedback = ""
    for attempt in range(1, attempts + 1):
        prompt = PROMPT.format(
            keyword=topic["keyword"],
            queries="、".join(topic.get("queries", [])[1:]),
            tool=topic.get("toolName", topic["tool"]),
            intent=topic.get("intent", ""),
            title=topic["title"],
            today=today_jst(),
            work_money_heading=persona.WORK_MONEY_HEADING,
            sources=source_block(sources, cap),
            feedback=feedback,
        )
        try:
            data, model = llm.generate_json(SYSTEM, prompt, SCHEMA, temperature=0.4, max_output_tokens=12000, tier=os.environ.get("EVERGREEN_TIER", "quality"))
        except Exception as error:  # noqa: BLE001
            log(f"   生成に失敗: {str(error)[:120]}")
            return None
        verdict = check(data, sources)
        fact = verdict["fact"]
        log(f"   試行{attempt}（{model}）: {'合格' if verdict['ok'] else '不合格'}" + (f" 事実確認 {fact['score']}点" if fact else ""))
        if verdict["ok"]:
            data["_model"] = model
            return data, fact
        for p in verdict["problems"][:8]:
            log(f"     - {p}")
        feedback = "\n# 前回の原稿の問題（必ず直す）\n" + "\n".join(f"- {p}" for p in verdict["problems"][:10])
    return None


def assemble(topic: dict, data: dict, fact: dict, sources: list[dict], previous: dict | None) -> dict:
    now = now_iso()
    used = {i for i in data.get("used_sources", []) if isinstance(i, int)}
    cited = [s for i, s in enumerate(sources, 1) if not used or i in used] or sources
    return {
        "slug": topic["slug"],
        "tool": topic["tool"],
        "toolName": topic.get("toolName", topic["tool"]),
        "company": topic.get("company", "other"),
        "intent": topic.get("intent", ""),
        "keyword": topic["keyword"],
        "title": data["title"].strip(),
        "seoTitle": data["seo_title"].strip(),
        "description": data["description"].strip(),
        "conclusion": [c.strip() for c in data["conclusion"]],
        "intro": data["intro"].strip(),
        "steps": [{"title": s["title"].strip(), "body": s["body"].strip()} for s in data["steps"]],
        "pricing": [{"plan": p["plan"].strip(), "price": p["price"].strip(), "note": (p.get("note") or "").strip()} for p in data["pricing"]],
        "freeTier": [f.strip() for f in data["free_tier"]],
        "japan": {"answer": data["japan_answer"].strip(), "body": data["japan_body"].strip()},
        "tips": [{"title": t["title"].strip(), "body": t["body"].strip()} for t in data["tips"]],
        "faq": [{"q": f["q"].strip(), "a": f["a"].strip()} for f in data["faq"]],
        "workMoney": persona.clean_work_money({"relevance": "high", "points": data.get("work_money") or []}),
        "match": topic.get("match", ""),
        "products": topic.get("products", []),
        "related": [],
        "sources": [{"url": s["url"], "title": s["title"], "checkedAt": now} for s in cited],
        "sourceHash": source_hash(sources),
        "fact": {"score": fact["score"], "model": fact["model"]},
        "model": data.get("_model", ""),
        "promptVersion": PROMPT_VERSION,
        "createdAt": (previous or {}).get("createdAt", now),
        "updatedAt": now,
        "checkedAt": now,
    }


def build(topic: dict, previous: dict | None = None) -> dict | None:
    # --force を含むどの生成経路でも、明示的に保護された手動原稿は上書きしない。
    if is_editorial(previous):
        log(f"手動編集待ち: {topic['slug']}（refreshMode: editorial のため自動生成しない）")
        return None
    log(f"■ {topic['slug']}（{topic['keyword']}）")
    sources = fetch_sources(topic.get("sources", []))
    if not sources or sum(len(s["text"]) for s in sources) < 2500:
        log("   公式ページが足りないので見送り")
        return None
    result = generate(topic, sources)
    if not result:
        log("   ::warning::合格しなかったので公開しない")
        return None
    data, fact = result
    guide = assemble(topic, data, fact, sources, previous)
    write(guide)
    log(f"   → content/howto/{guide['slug']}.json「{guide['title']}」")
    return guide


# --------------------------------------------------------------------------- #
# 確認し直し
# --------------------------------------------------------------------------- #


def refresh_reason(guide: dict, days: int) -> str:
    # 編集待ちの間は同じ期限・ニュースで繰り返し起票しない。編集者が確認後に解除する。
    if is_editorial(guide) and guide.get("needsReview"):
        return ""
    checked = guide.get("checkedAt", "")
    try:
        age = datetime.now(JST) - datetime.fromisoformat(checked)
    except ValueError:
        return "確認日なし"
    if age >= timedelta(days=days):
        return f"{age.days}日経過"
    if not is_editorial(guide) and guide.get("promptVersion") != PROMPT_VERSION:
        return "原稿の作り方を更新"
    news = [n for n in related_news(guide.get("match", "$^"), since=checked) if PRICE_NEWS_RE.search(n["title"])]
    if news:
        return f"関連ニュース「{news[0]['title'][:40]}」"
    return ""


def refresh(guide: dict, topic: dict, reason: str) -> None:
    log(f"■ 確認し直し {guide['slug']}（{reason}）")
    if is_editorial(guide):
        if not guide.get("needsReview"):
            guide["needsReview"] = now_iso()
            guide["needsReviewReason"] = reason
            write(guide)
        log("   手動編集待ち（本文・確認日は変更しない）")
        return
    sources = fetch_sources([s["url"] for s in guide.get("sources", [])] or topic.get("sources", []))
    if not sources:
        log("   公式ページが取れないので次回に回す")
        return
    now = now_iso()
    same = source_hash(sources) == guide.get("sourceHash") and guide.get("promptVersion") == PROMPT_VERSION
    if not same and guide.get("promptVersion") == PROMPT_VERSION:
        # 公式ページの文字が変わった: 今の原稿がまだ正しいかを確かめる
        data = {
            "title": guide["title"], "intro": guide["intro"], "conclusion": guide["conclusion"], "steps": guide["steps"],
            "pricing": guide["pricing"], "free_tier": guide["freeTier"], "japan_answer": guide["japan"]["answer"],
            "japan_body": guide["japan"]["body"], "tips": guide["tips"], "faq": guide["faq"],
        }
        bad = unverified_numbers(all_text(data), sources)
        fact = None if bad else quality.fact_check(to_record(data), source_block(sources, max(2500, 12500 // len(sources))))
        same = not bad and fact is not None and not fact["fatal"] and fact["score"] >= PASS_SCORE
        log(f"   公式ページに変更あり → 今の原稿は{'まだ正しい' if same else 'ずれている'}" + (f"（数字 {bad[:3]}）" if bad else f"（{fact['score']}点）" if fact else ""))
    if same:
        guide["checkedAt"] = now
        guide["sourceHash"] = source_hash(sources)
        for s in guide.get("sources", []):
            s["checkedAt"] = now
        guide.pop("needsReview", None)
        write(guide)
        log("   確認日を更新")
        return
    rebuilt = build({**topic, "sources": [s["url"] for s in sources]}, previous=guide)
    if not rebuilt:
        guide["needsReview"] = now
        write(guide)
        log("   ::warning::作り直せなかったので古い版を残す（確認日は更新しない）")


# --------------------------------------------------------------------------- #


def created_today(guides: dict[str, dict]) -> int:
    day = today_jst()
    return sum(1 for g in guides.values() if (g.get("createdAt") or "").startswith(day))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max", type=int, default=1, help="この回で新しく作る本数")
    parser.add_argument("--daily-cap", type=int, default=int(os.environ.get("EVERGREEN_DAILY_CAP") or 4), help="1日（日本時間）に新しく作る上限")
    parser.add_argument("--only", default="", help="カンマ区切りの slug だけ")
    parser.add_argument("--force", action="store_true", help="作成済みでも作り直す（--only と併用）")
    parser.add_argument("--refresh", action="store_true", help="確認し直しも行う")
    parser.add_argument("--refresh-max", type=int, default=2)
    parser.add_argument("--refresh-days", type=int, default=REFRESH_DAYS)
    parser.add_argument("--budget", type=int, default=0, help="この秒数を過ぎたら新しい作業を始めない")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    started = time.time()
    over = lambda: args.budget and time.time() - started > args.budget  # noqa: E731
    plan = load_plan()
    by_slug = {t["slug"]: t for t in plan}
    guides = existing()

    if args.list:
        for t in plan:
            g = guides.get(t["slug"])
            mark = f"済 確認 {g.get('checkedAt', '')[:10]}" if g else "未"
            log(f"{t['rank']:>3} {t['priority']:>2} {mark:<16} {t['slug']:<34} {t['keyword']}")
        return 0

    only = [s.strip() for s in args.only.split(",") if s.strip()]
    made = 0
    if args.refresh and not only:
        done = 0
        for slug, guide in guides.items():
            if over():
                break
            # 手動原稿の起票は外部取得・生成をせず、自動更新の本数枠を使わない。
            # 計画外の手動原稿も検知し、通常記事の枠を使い切った後も起票できる。
            if is_editorial(guide):
                reason = refresh_reason(guide, args.refresh_days)
                if reason:
                    refresh(guide, by_slug.get(slug, {}), reason)
                continue
            if done >= args.refresh_max:
                continue
            reason = refresh_reason(guide, args.refresh_days)
            if reason and slug in by_slug:
                refresh(guide, by_slug[slug], reason)
                done += 1

    failed = load_json(FAILED, {})
    recent_fail = lambda slug: (failed.get(slug) or "") > (datetime.now(JST) - timedelta(days=RETRY_DAYS)).isoformat()  # noqa: E731
    room = max(0, args.daily_cap - created_today(guides))
    if only:
        todo, limit = [by_slug[s] for s in only if s in by_slug], len(only)
    else:
        todo, limit = [t for t in plan if t["slug"] not in guides and not recent_fail(t["slug"])], min(args.max, room)
        if room == 0:
            log(f"今日の上限（{args.daily_cap}本）に達しているので新規作成はしない")
    for topic in todo:
        if made >= limit or over():
            break
        if is_editorial(guides.get(topic["slug"])):
            log(f"手動編集で保護中: {topic['slug']}（--force でも自動生成しない）")
            continue
        if topic["slug"] in guides and not args.force:
            log(f"作成済み: {topic['slug']}（作り直すなら --force）")
            continue
        if build(topic, guides.get(topic["slug"])):
            made += 1
            failed.pop(topic["slug"], None)
        else:
            failed[topic["slug"]] = now_iso()
    if failed or FAILED.exists():
        FAILED.parent.mkdir(parents=True, exist_ok=True)
        FAILED.write_text(json.dumps(failed, ensure_ascii=False, indent=2) + "\n", "utf-8")
    log(f"新規・作り直し {made}本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
