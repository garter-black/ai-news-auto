"""公開中の記事のジャンル・企業の札・タグを見直す（2026-11-03 の点検 11。台帳 A30）。

1. 機械の判定（scripts/labels.py の genre_fix・normalize_tags）
2. Haiku の確認（llm.generate_json(purpose="helper")。見出し・リード・タグだけを渡し、ジャンルと「見出しの主語の会社」を答えさせる）
の2つを並べ、直す案を作る。記事に書くのは --apply のときだけ。

直す決まり:
- ジャンル: 機械の判定が直すと言ったもの。Haiku と機械が同じ答えのとき、または Haiku だけが違うと言い、それが
  --accept-llm に書いた slug のとき（人が案を見て選ぶ）
- 企業の札: Haiku の答えた主語の会社が、サイトの判定（lib/companies.ts の companyOf を node で動かした結果）と違い、
  人が案を見て --accept-company に書いた slug だけ、記事 JSON に companySlug を書く（サイトはこれを優先する）。
  まずはサイトの判定の決まり（companyOf）を直し、決まりで直せないものだけ companySlug を使う
- タグ: normalize_tags でそろえる
- 見出しの会社名: headline_normalize.unify_companies（「アトラシアン」→「Atlassian」）

使い方:
  python scripts/label_check.py --propose out/labels.json [--limit N] [--no-llm]
  python scripts/label_check.py --apply out/labels.json [--accept-llm slug1,slug2] [--accept-company slug3] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import headline_normalize as hn  # noqa: E402
import labels  # noqa: E402
from genres import GENRES_BY_SLUG  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = ROOT / "content" / "articles"
BATCH = 25

GENRE_GUIDE = """- ai: AI・テクノロジー（AI・生成AI・機械学習・AIエージェント・AI半導体・ロボットが話の中心。AIの話でなければ選ばない）
- tech-gadget: テクノロジー・ガジェット（AIが中心でない製品・端末・アプリ・ソフトウェア・通信・セキュリティ・開発ツール）
- business: ビジネス・経済（企業の提携・買収・人事以外の経営・業界の動き・経済統計・景気）
- investment: 投資・マネー（決算・業績、株・金利・為替・金融政策、NISA・iDeCo・税・手数料、金融商品）
- real-estate: 不動産（住宅・地価・住宅ローン・不動産テック・REIT）
- public: 社会・行政（国・自治体・公的機関（IPA・独立行政法人など）の発表・制度・支援金・調査）
- lifestyle: 暮らし・生活（日用品・食・健康・家計の身近な話）
- entertainment: エンタメ・カルチャー（ゲーム・配信・映画・音楽のビジネス）
- sports: スポーツ
- science: 科学・宇宙（研究・宇宙開発。AIが中心なら ai）"""

RULES = f"""あなたはニュースサイトのデスクです。記事ごとに「ジャンル」と「見出しの主語の会社」を決めます。

# ジャンル（slug を1つ）
{GENRE_GUIDE}

# 見出しの主語の会社（company）
見出しで「だれが」にあたる会社・団体の名前を、見出しの書き方のまま1つ書く（例:「Qodo、Codex向けプラグインを公開」なら Qodo。
「Uber、全社員にChatGPTを展開」なら Uber。「NVIDIAが…」なら NVIDIA）。製品名から会社を推測しない。主語が会社・団体でなければ空文字。

出力は JSON: {{"items":[{{"slug":"...","genre":"...","company":"...","sure":true}}]}} のみ。sure は自信があるとき true。"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"slug": {"type": "STRING"}, "genre": {"type": "STRING"}, "company": {"type": "STRING"}, "sure": {"type": "BOOLEAN"}},
                "required": ["slug", "genre", "company"],
            },
        }
    },
    "required": ["items"],
}

# サイトの企業の一覧（lib/companies.ts の COMPANIES）の名前 → slug。node で読む
def site_companies() -> tuple[dict[str, str], dict[str, str]]:
    """({企業の名前の小文字: slug}, {記事 slug: サイトの判定の企業 slug})"""
    script = ROOT / "scripts" / "label_site_companies.cjs"
    out = subprocess.run(["node", str(script)], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    return {k.lower(): v for k, v in data["names"].items()}, data["articles"]


def load_live() -> list[tuple[Path, dict]]:
    rows = []
    for path in sorted(ARTICLES.glob("*.json")):
        try:
            rows.append((path, json.loads(path.read_text("utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def _payload(r: dict) -> dict:
    return {"slug": r["slug"], "title": r.get("title", ""), "lede": str(r.get("lede") or r.get("description") or "")[:200],
            "tags": r.get("tags") or [], "source": r.get("sourceName") or r.get("channel") or ""}


def ask_llm(records: list[dict]) -> dict[str, dict]:
    import llm  # noqa: PLC0415

    out: dict[str, dict] = {}
    for i in range(0, len(records), BATCH):
        chunk = records[i:i + BATCH]
        data, model = llm.generate_json(RULES, json.dumps([_payload(r) for r in chunk], ensure_ascii=False), SCHEMA,
                                        temperature=0.1, max_output_tokens=4096, tier="bulk", purpose="helper")
        print(f"[labels] {i + len(chunk)}/{len(records)}（{model}）", flush=True)
        for x in data.get("items", []):
            out[str(x.get("slug"))] = {"genre": str(x.get("genre", "")).strip(), "company": str(x.get("company", "")).strip(),
                                       "sure": bool(x.get("sure", True)), "model": model}
    return out


def company_slug_for(name: str, names: dict[str, str]) -> str:
    """Haiku が答えた主語の会社 → サイトの企業 slug（一覧に無い会社は other。空は ''）"""
    n = hn.unify_companies(name or "").strip().lower()
    if not n:
        return ""
    for key, slug in sorted(names.items(), key=lambda kv: -len(kv[0])):
        if key and (n == key or n.startswith(key)):
            return slug
    return "other"


def propose(limit: int | None, use_llm: bool) -> dict:
    rows = load_live()[: limit or None]
    records = [r for _, r in rows]
    names, site = site_companies()
    answers = ask_llm(records) if use_llm else {}
    out = {}
    for r in records:
        slug = r["slug"]
        fix = labels.genre_fix(r)
        a = answers.get(slug, {})
        llm_genre = a.get("genre") if a.get("genre") in GENRES_BY_SLUG else ""
        llm_company = company_slug_for(a.get("company", ""), names) if a else ""
        tags = labels.normalize_tags(r.get("tags"))
        title = hn.unify_companies(r.get("title", ""))
        item = {
            "title": r.get("title", ""), "genre": r.get("genreSlug"), "rule_genre": fix[0] if fix else None,
            "rule_reason": fix[1] if fix else None, "llm_genre": llm_genre or None, "llm_sure": a.get("sure"),
            "site_company": site.get(slug), "llm_company_name": a.get("company"), "llm_company": llm_company or None,
            "tags": tags if tags != (r.get("tags") or []) else None, "new_title": title if title != r.get("title", "") else None,
        }
        if any([item["rule_genre"], llm_genre and llm_genre != r.get("genreSlug"), llm_company and llm_company != site.get(slug),
                item["tags"], item["new_title"]]):
            out[slug] = item
    return out


def apply(proposals: dict, accept_llm: set[str], dry_run: bool, accept_company: set[str] = frozenset()) -> int:
    import web_articles  # noqa: PLC0415  web_disclaimer（ジャンルごとの免責文）

    changed = 0
    for slug, p in proposals.items():
        path = ARTICLES / f"{slug}.json"
        if not path.exists():
            continue
        record = json.loads(path.read_text("utf-8"))
        before = json.dumps(record, ensure_ascii=False, sort_keys=True)
        # ジャンル: 機械の判定（いまの記事でもう一度確かめる）か、人が選んだ Haiku の案
        fix = labels.genre_fix(record)
        target = fix[0] if fix else (p.get("llm_genre") if slug in accept_llm else None)
        if target and target in GENRES_BY_SLUG and target != record.get("genreSlug"):
            genre = GENRES_BY_SLUG[target]
            record["genre"], record["genreSlug"] = genre.name, genre.slug
            if record.get("sourceType") == "web" and not str(record.get("disclaimer", "")).startswith("本記事は報道"):
                record["disclaimer"] = web_articles.web_disclaimer(genre)
        # 企業の札: Haiku の主語の会社がサイトの判定と違うとき（人が確かめた案だけ。--accept-company に書いた slug）
        if slug in accept_company and p.get("llm_company") and p.get("llm_company") != p.get("site_company"):
            record["companySlug"] = p["llm_company"]
        record["tags"] = labels.normalize_tags(record.get("tags"))
        title = hn.unify_companies(record.get("title", ""))
        if title != record.get("title"):
            record["title"] = title
        if json.dumps(record, ensure_ascii=False, sort_keys=True) != before:
            changed += 1
            print(f"[labels] {slug}: {p.get('genre')}→{record.get('genreSlug')} company={record.get('companySlug', '-')}")
            if not dry_run:
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--propose")
    ap.add_argument("--apply")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--accept-llm", default="", help="Haiku のジャンルの案を採る slug（カンマ区切り）")
    ap.add_argument("--accept-company", default="", help="Haiku の企業の案を companySlug に書く slug（カンマ区切り）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.propose:
        out = propose(args.limit, not args.no_llm)
        Path(args.propose).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
        print(f"[labels] 案 {len(out)} 本 → {args.propose}")
        return 0
    if args.apply:
        proposals = json.loads(Path(args.apply).read_text("utf-8"))
        accept = {s.strip() for s in args.accept_llm.split(",") if s.strip()}
        accept_company = {s.strip() for s in args.accept_company.split(",") if s.strip()}
        print(f"[labels] 直した記事 {apply(proposals, accept, args.dry_run, accept_company)} 本{'（試し）' if args.dry_run else ''}")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
