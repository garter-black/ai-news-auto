"""
記事の見出し（title）と検索用の題名（seoTitle）を、報道の見出しの書き方に直す（2026-11-03 オーナー指摘
「見出しが不自然で幼い。恥ずかしい」）。書き方は scripts/headline_normalize.py の TITLE_STYLE_PROMPT（記事を書くプロンプトと同じ文）。

- よく知られた会社に説明を付けない（「ChatGPTのOpenAIが」→「OpenAIが」）。幼い言い方（勝手な・人手なしで・〜が1本に）をしない
- 社名・製品名・数字・日付は元のとおり。元の情報（見出し・説明・要点・リード）に無い数字や英字の名前は足さない
- 1回の呼び出しで複数本まとめて直す。URL（slug）と本文は変えない

使い方:
  # 案を作る（記事は変えない）。Claude API は purpose="core"（予算の配分で Sonnet、苦しければ Haiku）
  python scripts/rewrite_titles.py --propose out.json [--limit N] [--slugs a,b]
  # 案を確かめて記事に書く（--offset/--count で少しずつ。まとめ記事の見出しの写し・朝のまとめの見出しも直す）
  python scripts/rewrite_titles.py --apply out.json [--offset 0 --count 50] [--dry-run]

記事を書き出すとき（main.write_article）は fix_one が、書き方の不合格（headline_normalize.title_problems）が
残った見出しだけを1本書き直す（purpose="helper"。だめなら機械でそろえた見出しのまま）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import headline_normalize as hn  # noqa: E402
import llm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = ROOT / "content" / "articles"
DIGESTS = ROOT / "content" / "digests"
STYLE_KEY = "titleStyle"
STYLE_VERSION = "2026-11-03"
BATCH = 20

RULES = f"""あなたは日本の IT・ビジネス系ニュースサイトのデスクです。記事の見出し（title）と検索用の題名（seo_title）を直します。

# 見出し（title）の書き方
- title: {hn.TITLE_STYLE_PROMPT}

# 検索用の題名（seo_title）の書き方
- 全角24〜34字。検索される名前（製品名・企業名・制度名）を先頭に置き、何が分かる記事かを続ける。記号は「｜」「：」を最大1つまで。
- 新しい製品・機能の発表なら「〇〇とは？料金と使い方」「〇〇の使い方と料金｜日本での提供状況」のような検索の形は残してよい。
- 見出しと同じく、よく知られた会社への説明・幼い言い方・不自然な言い方をしない。元の題名で自然なら、そのまま返す。

# 守ること
- 事実は入力（元の見出し・題名・説明・要点・リード）にあることだけ。社名・製品名・数字・日付は表記もそのまま使い、新しく足さない。
- 元の見出しの「」の製品名・英字の名前は、意味を変えずに残す（よく知られた会社の前の説明「ChatGPTの」「AI半導体の」だけは外す）。
- すでに自然な見出しは、ほぼそのまま返してよい。
出力は JSON: {{"items":[{{"slug":"...","title":"...","seo_title":"..."}}]}} のみ。"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"slug": {"type": "STRING"}, "title": {"type": "STRING"}, "seo_title": {"type": "STRING"}},
                "required": ["slug", "title", "seo_title"],
            },
        }
    },
    "required": ["items"],
}

LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.+\-]*")


def numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")))


def source_text(record: dict) -> str:
    """書き直しに使ってよい事実の範囲"""
    parts = [record.get("originalTitle", ""), record.get("title", ""), record.get("seoTitle", ""), record.get("videoTitle", ""),
             record.get("description", ""), record.get("lede", ""), " ".join(record.get("highlights") or []),
             " ".join(str(t) for t in record.get("mentionedTools") or []), " ".join(str(t) for t in record.get("tags") or [])]
    return " ".join(str(p) for p in parts)


def must_keep(title: str) -> set[str]:
    """元の見出しの本見出し（全角空白の前）にある「」の名前と英字の名前。よく知られた会社の前の説明は除く"""
    base = hn.strip_known_descriptors(hn.normalize_title(title)).split("　")[0]
    names = set(re.findall(r"「([^」]{1,40})」", base))
    names |= {w for w in LATIN_RE.findall(re.sub(r"「[^」]*」", " ", base)) if len(w) >= 2}
    return names


def check_title(record: dict, new: str) -> list[str]:
    """書き直した見出しの不合格の理由（空なら合格）"""
    new = hn.normalize_title(new, record)
    problems = list(hn.title_problems(new))
    if not 16 <= len(new) <= 52:
        problems.append(f"長さ {len(new)}字")
    src = source_text(record)
    added = numbers(new) - numbers(src)
    if added:
        problems.append(f"元に無い数字 {sorted(added)}")
    src_low = src.lower()
    unknown = [w for w in LATIN_RE.findall(new) if len(w) >= 2 and w.lower() not in src_low]
    if unknown:
        problems.append(f"元に無い名前 {unknown}")
    new_low = new.lower()
    lost = [n for n in must_keep(record.get("title", "")) if n.lower() not in new_low]
    if lost:
        problems.append(f"名前が消えた {lost}")
    return problems


def check_seo(record: dict, new: str) -> list[str]:
    new = hn.normalize_title(new, record)
    problems = [p for p in hn.title_problems(new) if not p.startswith("長すぎる")]
    if not 12 <= len(new) <= 40:
        problems.append(f"長さ {len(new)}字")
    src = source_text(record)
    if numbers(new) - numbers(src):
        problems.append("元に無い数字")
    src_low = src.lower()
    if [w for w in LATIN_RE.findall(new) if len(w) >= 2 and w.lower() not in src_low]:
        problems.append("元に無い名前")
    return problems


def _payload(record: dict) -> dict:
    return {
        "slug": record["slug"], "genre": record.get("genre", ""), "title": record.get("title", ""),
        "seo_title": record.get("seoTitle", ""), "description": str(record.get("description", ""))[:160],
        "lede": str(record.get("lede", ""))[:220], "highlights": (record.get("highlights") or [])[:3],
    }


def _ask(records: list[dict], purpose: str, tier: str = "bulk", feedback: dict | None = None) -> dict[str, dict]:
    payload = [_payload(r) for r in records]
    if feedback:
        for item in payload:
            if item["slug"] in feedback:
                item["前回の案の問題"] = feedback[item["slug"]]
    data, model = llm.generate_json(RULES, json.dumps(payload, ensure_ascii=False), SCHEMA, temperature=0.3,
                                    max_output_tokens=8192, tier=tier, purpose=purpose)
    print(f"[titles] {len(records)}本（{model}）", flush=True)
    return {str(x.get("slug")): {"title": str(x.get("title", "")).strip(), "seo_title": str(x.get("seo_title", "")).strip(), "model": model}
            for x in data.get("items", [])}


def load_live() -> list[tuple[Path, dict]]:
    rows = []
    for path in sorted(ARTICLES.glob("*.json")):
        try:
            rows.append((path, json.loads(path.read_text("utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def propose(out: Path, limit: int = 0, slugs: set[str] | None = None, purpose: str = "core") -> int:
    rows = [(p, a) for p, a in load_live() if not slugs or a["slug"] in slugs]
    rows.sort(key=lambda r: str(r[1].get("publishedAt") or ""), reverse=True)
    if limit:
        rows = rows[:limit]
    results: dict[str, dict] = json.loads(out.read_text("utf-8")) if out.exists() else {}
    todo = [a for _, a in rows if a["slug"] not in results]
    print(f"[titles] 対象 {len(rows)} 本（案が未作成 {len(todo)} 本）", flush=True)
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        try:
            got = _ask(chunk, purpose)
        except Exception as error:  # noqa: BLE001
            print(f"[titles] 失敗（{i}〜）: {str(error)[:160]}", flush=True)
            continue
        # 不合格の案は、理由を添えて1回だけ聞き直す
        retry, feedback = [], {}
        for a in chunk:
            prop = got.get(a["slug"]) or {}
            bad = check_title(a, prop.get("title", "")) if prop.get("title") else ["案なし"]
            if bad:
                retry.append(a)
                feedback[a["slug"]] = f"{prop.get('title', '')} → {'; '.join(bad)}"
        if retry:
            try:
                again = _ask(retry, purpose, feedback=feedback)
                for slug, prop in again.items():
                    a = next((r for r in retry if r["slug"] == slug), None)
                    if a and prop.get("title") and not check_title(a, prop["title"]):
                        got[slug] = prop
            except Exception as error:  # noqa: BLE001
                print(f"[titles] 聞き直し失敗: {str(error)[:120]}", flush=True)
        for a in chunk:
            prop = got.get(a["slug"])
            if prop:
                results[a["slug"]] = {"old_title": a.get("title", ""), "old_seo": a.get("seoTitle", ""), **prop}
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(f"[titles] {min(i + BATCH, len(todo))}/{len(todo)}", flush=True)
    return 0


def _update_copies(changed: dict[str, tuple[str, str]], dry_run: bool) -> int:
    """まとめ記事の見出しの写し（roundupTitle）と朝のまとめ（content/digests）の見出しを新しい見出しにする"""
    n = 0
    for path, a in load_live():
        slug = a.get("roundupSlug")
        if slug in changed and a.get("roundupTitle") == changed[slug][0]:
            a["roundupTitle"] = changed[slug][1]
            n += 1
            if not dry_run:
                path.write_text(json.dumps(a, ensure_ascii=False, indent=2) + "\n", "utf-8")

    def walk(node) -> bool:
        hit = False
        if isinstance(node, dict):
            slug = node.get("slug")
            if slug in changed and node.get("title") == changed[slug][0]:
                node["title"] = changed[slug][1]
                hit = True
            for value in node.values():
                hit = walk(value) or hit
        elif isinstance(node, list):
            for value in node:
                hit = walk(value) or hit
        return hit

    for path in sorted(DIGESTS.glob("*.json")):
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if walk(data):
            n += 1
            if not dry_run:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return n


def apply_record(a: dict, prop: dict) -> list[str]:
    """案を1本の記事に当てる（合格したものだけ）。変えた内容の説明を返す"""
    changes: list[str] = []
    new_title = hn.normalize_title(prop.get("title", ""), a)
    if new_title and new_title != a.get("title") and not check_title(a, new_title):
        a.setdefault("originalTitle", a.get("title", ""))
        changes.append(f"title: {a.get('title')} → {new_title}")
        a["title"] = new_title
    new_seo = hn.normalize_title(prop.get("seo_title", ""), a)
    if new_seo and new_seo != a.get("seoTitle") and not check_seo(a, new_seo):
        changes.append(f"seoTitle: {a.get('seoTitle')} → {new_seo}")
        a["seoTitle"] = new_seo
    # リード・説明の頭の「ChatGPTのOpenAIが」も外す（本文は変えない）
    for key in ("lede", "description"):
        text = a.get(key)
        if isinstance(text, str):
            fixed = hn.strip_known_descriptors(text)
            if fixed != text:
                a[key] = fixed
                changes.append(f"{key}: 会社の説明を外した")
    return changes


def apply(path_in: Path, offset: int = 0, count: int = 0, dry_run: bool = False) -> int:
    proposals: dict[str, dict] = json.loads(path_in.read_text("utf-8"))
    rows = [(p, a) for p, a in load_live() if a["slug"] in proposals]
    rows.sort(key=lambda r: r[1]["slug"])
    rows = rows[offset: offset + count] if count else rows[offset:]
    changed: dict[str, tuple[str, str]] = {}
    kept = 0
    for path, a in rows:
        prop = proposals[a["slug"]]
        if prop.get("old_title") and prop["old_title"] != a.get("title"):
            print(f"  [飛ばす] 案を作ったあとに見出しが変わった: {a['slug']}")
            continue
        old = a.get("title", "")
        changes = apply_record(a, prop)
        bad = check_title(a, prop.get("title", "")) if prop.get("title") else []
        if bad:
            kept += 1
            print(f"  [保留] {old}  ×  {prop.get('title')}（{'; '.join(bad)}）")
        if not changes:
            continue
        if a.get("title") != old:
            changed[a["slug"]] = (old, a["title"])
        a[STYLE_KEY] = STYLE_VERSION
        print(f"[{a['slug']}]")
        for line in changes:
            print(f"  {line}")
        if not dry_run:
            path.write_text(json.dumps(a, ensure_ascii=False, indent=2) + "\n", "utf-8")
    copies = _update_copies(changed, dry_run) if changed else 0
    print(f"[titles] 対象 {len(rows)} 本 / 見出しを直した {len(changed)} 本 / 案が不合格 {kept} 本 / 写しを直したファイル {copies}")
    return 0


def fix_one(record: dict) -> str | None:
    """書き方の不合格が残った見出しを1本だけ書き直す（記事を書き出す前。record をその場で書き換える）。
    直したら説明を返す。直せなければ None（機械でそろえた見出しのまま出す。品質ゲートの警告に残る）"""
    title = str(record.get("title") or "")
    problems = hn.title_problems(title)
    if not title or not problems or not record.get("slug"):
        return None
    try:
        got = _ask([record], "helper", feedback={record["slug"]: "; ".join(problems)})
    except Exception as error:  # noqa: BLE001 - 見出しの手直しで記事を止めない
        return f"見出しの書き直しに失敗（{str(error)[:80]}）。そのまま出す"
    prop = got.get(record["slug"]) or {}
    new = hn.normalize_title(prop.get("title", ""), record)
    if not new or check_title(record, new):
        return f"見出しの書き直し案が不合格（{new}）。そのまま出す"
    record.setdefault("originalTitle", title)
    record["title"] = new
    record[STYLE_KEY] = STYLE_VERSION
    return f"見出しを書き直した（{'; '.join(problems)}）: {title} → {new}"


def main() -> int:
    parser = argparse.ArgumentParser(description="見出しを報道の書き方に直す")
    parser.add_argument("--propose", type=Path, help="案を作って書き出すファイル（途中から続けられる）")
    parser.add_argument("--apply", type=Path, help="案のファイルを記事に当てる")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--slugs", default="", help="カンマ区切りの slug だけ")
    parser.add_argument("--purpose", default="core", help="Claude API の段（scripts/llm.py の API_TIERS）")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    llm._load_env_local()  # noqa: SLF001
    if args.propose:
        slugs = {s.strip() for s in args.slugs.split(",") if s.strip()} or None
        return propose(args.propose, args.limit, slugs, args.purpose)
    if args.apply:
        return apply(args.apply, args.offset, args.count, args.dry_run)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
