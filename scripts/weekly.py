#!/usr/bin/env python3
"""今週のAIニュースまとめ（/weekly/[yyyy-ww]）を作り、content/weekly.json に足す。

毎週月曜 07:00 JST に、前の週（月〜日、日本時間）にサイソクが出した AI 記事から10本を選ぶ。
1. 点数で候補を絞る（大きな発表・速報 → 公式発表 → 使える判定 → 品質点 → 厚さ）
2. LLM（quality。週1回なので質を優先）が重要な順に10本を選び、「何が起きた」「なぜ大事」を1文ずつ書く
3. 機械的に確かめる（候補の記事か・1文60字以内か・数字は元記事にあるか）。外れたら記事の要点から作る

  python3 scripts/weekly.py                  # 前の週のまとめを作る（作成済みなら何もしない）
  python3 scripts/weekly.py --week 2026-43   # 週を指定
  python3 scripts/weekly.py --force          # 作成済みでも作り直す
  python3 scripts/weekly.py --dry-run        # 候補を表示するだけ
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from digest import load_articles, score as digest_score  # noqa: E402
from indexnow import is_distribution_eligible  # noqa: E402
import headline_normalize  # noqa: E402  題名の門番（まとめにも通す。台帳 A25）

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "content" / "weekly.json"
JST = timezone(timedelta(hours=9))
PICKS = 10
SENTENCE_MAX = 58

# 公式チャンネル・主要企業（大きな発表になりやすい）
OFFICIAL_CHANNELS = {
    "openai", "anthropic", "google", "google-deepmind", "google-developers", "nvidia",
    "microsoft-developer", "microsoft-research", "hugging-face", "ai-at-meta",
}
MAJOR_RE = re.compile(r"OpenAI|ChatGPT|GPT-|Codex|Anthropic|Claude|Gemini|Google|DeepMind|NVIDIA|Microsoft|Copilot|Meta|Llama|xAI|Grok|DeepSeek|Apple|Kimi|Mistral|Qwen", re.I)
LAUNCH_RE = re.compile(r"発表|リリース|公開|提供開始|登場|新モデル|新機能|launch|release", re.I)

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "lede": {"type": "ARRAY", "items": {"type": "STRING"}},
        "picks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "slug": {"type": "STRING"},
                    "what": {"type": "STRING"},
                    "why": {"type": "STRING"},
                },
                "required": ["slug", "what", "why"],
            },
        },
    },
    "required": ["lede", "picks"],
}

SYSTEM = """あなたは日本のAIニュースサイトの編集長です。1週間のニュースから、日本でAIを使う人が押さえるべき10本を選び、短く解説します。
渡された情報に無い事実・数字は書かない。煽らない（衝撃・革命・ヤバい・圧倒的 は使わない）。です・ます調。出力は指定の JSON だけ。"""

PROMPT = """{start}〜{end}（日本時間）にサイソクが報じたAIニュースの候補です。重要な順に10本を選んでください。

# 選び方
- 候補は重要そうな順に並んでいる。大きな発表（新しいモデル・製品、料金や提供範囲の変更）を優先する。
- 同じ製品・同じ話題の記事は1本だけにする（例: 同じモデルのベンチマーク記事が2本あれば1本）。
- 研究の解説や導入事例は、内容が具体的で影響が大きいものだけ。
- 主語にする企業・製品は、その候補の題名・要点に書かれたものだけ。

# 書き方（1本ごと）
- what: 何が起きたか。1文、40字以内。主語（企業・製品）をはっきり。
- why: なぜ大事か。読者の仕事や選択に何が変わるかを1文、50字以内で。what の言い換えにしない。
  例）what「OpenAIがGPT-6.1 Terraを公開しました。」 why「同じ予算で使える量が増え、APIの費用を見直す好機です。」
- 候補に書かれた事実だけを使う。数字は候補に書かれたものだけ。
- 「〜が公開されました」の繰り返しを避け、文末に変化をつける。
- lede: この週全体の流れ。2文。1文は50字以内。

# 候補（重要そうな順。slug / 企業 / 題名 / 要点 / 判定）
{candidates}"""


def log(message: str) -> None:
    print(message, flush=True)


# --------------------------------------------------------------------------- #
# 週の計算
# --------------------------------------------------------------------------- #


def week_id(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-{week:02d}"


def week_range(wid: str) -> tuple[datetime, datetime]:
    """週の始まり（月曜 0:00 JST）と終わり（翌週月曜 0:00 JST）"""
    year, week = (int(x) for x in wid.split("-"))
    monday = date.fromisocalendar(year, week, 1)
    start = datetime(monday.year, monday.month, monday.day, tzinfo=JST)
    return start, start + timedelta(days=7)


def last_full_week(now: datetime | None = None) -> str:
    now = (now or datetime.now(timezone.utc)).astimezone(JST)
    this_monday = now.date() - timedelta(days=now.weekday())
    return week_id(this_monday - timedelta(days=7))


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 候補選び
# --------------------------------------------------------------------------- #


HOWTO_RE = re.compile(r"入門|活用法|活用術|使い方|ガイド|解説|手順|検証|レビュー|仕組み")
# 導入事例（他社が使った話）は、製品そのものの発表より下げる
CASE_RE = re.compile(r"事例|導入|採用|統合|で構築|を活用")
MODEL_RE = re.compile(r"(GPT|Gemini|Claude|Opus|Sonnet|Fable|Kimi|Llama|Grok|DeepSeek|Qwen|Mistral|RTX)[\s\-]?[A-Z]?\d|Vera Rubin|Blackwell|Jetson Thor", re.I)


def score(article: dict) -> float:
    """大きな発表・速報 → 公式発表 → 実用的な話題、の順に高くする"""
    s = digest_score(article)
    title = f"{article.get('title', '')} {article.get('seoTitle', '')}"
    official = article.get("channelSlug") in OFFICIAL_CHANNELS or bool(
        article.get("sourceType") == "web" and MAJOR_RE.search(article.get("sourceName", ""))
    )
    if article.get("breaking"):
        s += 100
    if official:
        s += 20
    if MAJOR_RE.search(title):
        s += 10
    if LAUNCH_RE.search(title):
        s += 25 if official else 8
    if MODEL_RE.search(title):
        s += 15
    if HOWTO_RE.search(title):
        s -= 15
    if CASE_RE.search(title):
        s -= 12
    return s


def candidates_for(wid: str, limit: int = 40) -> list[dict]:
    start, end = week_range(wid)
    picked = []
    for article in load_articles():
        at = parse_dt(article.get("generatedAt") or article.get("publishedAt"))
        if not at or not (start <= at < end):
            continue
        if article.get("genreSlug", "ai") != "ai" or not is_distribution_eligible(article):
            continue
        picked.append(article)
    picked.sort(key=lambda a: (score(a), a.get("generatedAt", "")), reverse=True)
    result, groups = [], set()
    for article in picked:
        # 同じまとめに入っている記事は1本だけ
        group = article.get("roundupSlug") or article.get("roundupKey") or ""
        if group and group in groups:
            continue
        if group:
            groups.add(group)
        result.append(article)
        if len(result) >= limit:
            break
    return result


def item_title(article: dict) -> str:
    """まとめに出す題名。記事の見出し（h1 の title）を題名の門番に通したもの。
    検索向けの seoTitle（「〇〇とは？日本で使える？料金も」）は10本並ぶと機械的なので使わない（2026-11-03 オーナー決定・台帳 A25）"""
    title = str(article.get("title") or article.get("seoTitle") or "")
    return headline_normalize.normalize_title(title, article)


def company_of(article: dict) -> str:
    """lib/companies.ts の companyOf を簡略化（表示用の企業名）。発信元の名前を先に見る
    （「Apple Creator Studio」の記事が、タグの「Pixelmator Pro」で Google になっていた。2026-11-03 の点検）"""
    patterns = [
        ("OpenAI", r"OpenAI|ChatGPT|GPT-|Codex|Sora"), ("Anthropic", r"Anthropic|Claude"),
        ("Apple", r"\bApple\b|アップル|iPhone|iPad|\bMac\b|Siri"),
        ("Google", r"Google|グーグル|DeepMind|Gemini|Gemma|\bPixel\b|Android"), ("NVIDIA", r"NVIDIA|エヌビディア|RTX|CUDA"),
        ("Microsoft", r"Microsoft|マイクロソフト|Azure|Copilot|GitHub|Foundry"), ("Meta", r"\bMeta\b|Llama"),
        ("Hugging Face", r"Hugging ?Face"), ("DeepSeek", r"DeepSeek"), ("xAI", r"\bxAI\b|Grok"),
        ("Mistral AI", r"Mistral"), ("Moonshot AI", r"Kimi|Moonshot"),
    ]
    for text in (str(article.get("sourceName") or ""), f"{article.get('title', '')} {' '.join(article.get('tags') or [])}"):
        best = None
        for name, pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match and (best is None or match.start() < best[1]):
                best = (name, match.start())
        if best:
            return best[0]
    return ""


# --------------------------------------------------------------------------- #
# 文の確認
# --------------------------------------------------------------------------- #


def clip_sentence(text: str, limit: int = 50) -> str:
    """最初の1文を、limit 字を超えるなら読点で切って収める"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    first = re.split(r"(?<=。)", text)[0].strip() or text
    if len(first) <= limit:
        return first if first.endswith("。") else first + "。"
    cut = first[:limit]
    comma = cut.rfind("、")
    if comma >= limit * 0.5:
        return cut[:comma] + "。"
    return cut.rstrip("、。 ") + "…"


def numbers(text: str) -> set[str]:
    t = unicodedata.normalize("NFKC", text)
    return {n.replace(",", "") for n in re.findall(r"\d+(?:[.,]\d+)*", t)}


def sentence_ok(text: str, article: dict) -> bool:
    text = str(text or "").strip()
    if not text or len(text) > SENTENCE_MAX:
        return False
    if len([s for s in re.split(r"(?<=[。！？])", text) if s.strip()]) > 1:
        return False
    if re.search(r"衝撃|革命|ヤバ|圧倒的|驚愕", text):
        return False
    source = " ".join(
        [article.get("title", ""), article.get("seoTitle", ""), article.get("lede", ""), article.get("description", ""), *article.get("highlights", []), article.get("body", "")[:6000]]
    )
    return numbers(text) <= numbers(source)


def fallback_lines(article: dict) -> tuple[str, str]:
    """LLM が使えないときの2行。要点・リード文の文から短いものを選ぶ（途中で切らない）"""
    sentences: list[str] = []
    for text in [*(article.get("highlights") or []), article.get("lede", ""), article.get("description", "")]:
        for sentence in re.split(r"(?<=。)", re.sub(r"\s+", " ", str(text or "")).strip()):
            sentence = sentence.strip()
            if sentence and sentence not in sentences:
                sentences.append(sentence if sentence.endswith("。") else sentence + "。")
    if not sentences:
        return article.get("title", "") + "。", ""
    fitting = [x for x in sentences if len(x) <= SENTENCE_MAX] or sorted(sentences, key=len)
    what = fitting[0]
    rest = [x for x in fitting if x != what] or [x for x in sorted(sentences, key=len) if x != what]
    return what, (rest[0] if rest else "")


def describe(article: dict) -> str:
    verdict = article.get("verdict") or {}
    judged = ""
    if verdict:
        judged = f" / 判定: 提供={verdict.get('status')} 日本={verdict.get('japan')} 料金={verdict.get('price', '')}"
    highlights = " ".join(str(h) for h in (article.get("highlights") or [])[:3])
    title = item_title(article)
    return f"- {article['slug']} / {company_of(article) or 'その他'} / {title} / {highlights}{judged}"


def build_week(wid: str, use_llm: bool = True) -> dict | None:
    start, end = week_range(wid)
    candidates = candidates_for(wid)
    if len(candidates) < 3:
        log(f"[weekly] {wid}: 候補が {len(candidates)} 本しかないので作りません")
        return None
    by_slug = {a["slug"]: a for a in candidates}
    last_day = (end - timedelta(days=1)).date()
    lede: list[str] = []
    picks: list[dict] = []
    model = "fallback"

    if use_llm:
        from llm import _load_env_local, generate_json

        _load_env_local()
        prompt = PROMPT.format(
            start=f"{start.month}月{start.day}日",
            end=f"{last_day.month}月{last_day.day}日",
            candidates="\n".join(describe(a) for a in candidates[:16]),
        )
        try:
            try:
                data, model = generate_json(SYSTEM, prompt, SCHEMA, tier="quality", temperature=0.4, max_output_tokens=4096)
            except Exception as error:  # noqa: BLE001 - 質重視の枠が尽きていたら軽量モデルで
                log(f"[weekly] quality で失敗（{str(error)[:80]}）→ bulk で再挑戦")
                data, model = generate_json(SYSTEM, prompt, SCHEMA, tier="bulk", temperature=0.4, max_output_tokens=4096)
            lede = [s.strip() for s in data.get("lede") or [] if isinstance(s, str) and 0 < len(s.strip()) <= SENTENCE_MAX][:2]
            seen = set()
            for pick in data.get("picks") or []:
                slug = str(pick.get("slug", "")).strip()
                article = by_slug.get(slug)
                if not article or slug in seen:
                    continue
                seen.add(slug)
                what, why = str(pick.get("what", "")).strip(), str(pick.get("why", "")).strip()
                fb_what, fb_why = fallback_lines(article)
                picks.append({
                    "slug": slug,
                    "what": what if sentence_ok(what, article) else fb_what,
                    "why": why if sentence_ok(why, article) else fb_why,
                })
                if len(picks) >= PICKS:
                    break
        except Exception as error:  # noqa: BLE001 - LLM が使えなくても要点から作る
            log(f"[weekly] LLM が使えないので記事の要点から作ります: {str(error)[:160]}")

    # 並び順は点数（大きな発表が先）で決める。LLM は「どれを載せるか」と文を担当する
    rank = {a["slug"]: i for i, a in enumerate(candidates)}
    picks.sort(key=lambda p: rank.get(p["slug"], 999))
    # 足りない分は点数順に、記事の要点から埋める
    chosen = {p["slug"] for p in picks}
    for article in candidates:
        if len(picks) >= PICKS:
            break
        if article["slug"] in chosen:
            continue
        what, why = fallback_lines(article)
        picks.append({"slug": article["slug"], "what": what, "why": why})

    if not lede:
        names = []
        for p in picks[:3]:
            name = company_of(by_slug[p["slug"]])
            if name and name not in names:
                names.append(name)
        lede = [
            f"{start.month}月{start.day}日からの1週間に出たAIニュースから、{len(picks)}本を選びました。",
            (f"{'・'.join(names)}の発表を中心にまとめています。" if names else "大きな発表から順に並べています。"),
        ]

    items = []
    for p in picks:
        article = by_slug[p["slug"]]
        items.append({
            "slug": article["slug"],
            "title": item_title(article),
            "company": company_of(article),
            "publishedAt": article.get("generatedAt") or article.get("publishedAt", ""),
            "what": p["what"],
            "why": p["why"],
        })

    return {
        "id": wid,
        "start": start.date().isoformat(),
        "end": last_day.isoformat(),
        "title": f"今週のAIニュースまとめ（{start.month}月{start.day}日〜{last_day.month}月{last_day.day}日）",
        "lede": lede,
        "items": items,
        "articleCount": len(load_week_ai(wid)),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
    }


def load_week_ai(wid: str) -> list[dict]:
    start, end = week_range(wid)
    result = []
    for article in load_articles():
        at = parse_dt(article.get("generatedAt") or article.get("publishedAt"))
        if at and start <= at < end and article.get("genreSlug", "ai") == "ai":
            result.append(article)
    return result


def load_weeks() -> list[dict]:
    try:
        data = json.loads(OUT_PATH.read_text("utf-8"))
        return [w for w in data.get("weeks", []) if isinstance(w, dict) and w.get("id")]
    except (OSError, json.JSONDecodeError):
        return []


def save_weeks(weeks: list[dict]) -> None:
    weeks = sorted(weeks, key=lambda w: w["id"], reverse=True)
    OUT_PATH.write_text(json.dumps({"weeks": weeks}, ensure_ascii=False, indent=1) + "\n", "utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--week", help="yyyy-ww（ISO週）。省略時は前の週")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()

    wid = args.week or last_full_week()
    if not re.fullmatch(r"\d{4}-\d{2}", wid):
        log("[weekly] --week は 2026-43 の形で指定してください")
        return 1
    weeks = load_weeks()
    if not args.force and not args.dry_run and any(w["id"] == wid for w in weeks):
        log(f"[weekly] {wid} は作成済みです（--force で作り直し）")
        return 0

    if args.dry_run:
        for article in candidates_for(wid):
            log(f"{score(article):6.1f}  {article.get('generatedAt', '')[:16]}  {article.get('seoTitle') or article.get('title')}")
        return 0

    week = build_week(wid, use_llm=not args.no_llm)
    if not week:
        return 0
    weeks = [w for w in weeks if w["id"] != wid] + [week]
    save_weeks(weeks)
    log(f"[weekly] {wid}: {len(week['items'])}本（{week['model']}）→ {OUT_PATH.relative_to(ROOT)}")
    for index, item in enumerate(week["items"], 1):
        log(f"  {index:2}. {item['title']}\n      {item['what']}\n      {item['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
