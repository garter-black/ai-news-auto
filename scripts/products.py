#!/usr/bin/env python3
"""製品ごとの「まとめページ」（/products/<slug>）のデータを作る。

ニュースは数日で読まれなくなるが、「GPT-6.1 Terra とは」「Gemini 4 料金」のような検索は何か月も続く。
記事に出てくる製品・モデルを見つけて、製品ごとに1ページへ事実をまとめ、記事が増えたら作り直す。

流れ
  1. 候補を拾う（記事の keywords・tags・題名の「」）。新しい候補だけ LLM で製品か判定し、
     正式名・別名・企業・分類を content/product_registry.json に覚える（次からは LLM を使わない）
  2. 記事と製品を照合する（題名・短い題名に出てくれば「主」、keywords・tags だけなら「従」）
  3. 主の記事が2本以上（または判定つきの厚い記事1本）ある製品だけページにする
  4. 対象記事が前回から変わった製品だけ LLM で原稿を作る（変わらなければ LLM を呼ばない）
  5. 原稿の数字・日付が、出典に挙げた記事に本当に書いてあるかを機械的に確かめる。
     合わない項目は1回だけ書き直させ、それでも合わなければ捨てる

  python3 scripts/products.py --dry-run          # 候補と対象記事を表示するだけ（LLM は判定だけ使う）
  python3 scripts/products.py --max 3 --budget 300
  python3 scripts/products.py --only gpt-6-1-terra --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
PRODUCTS_DIR = ROOT / "content" / "products"
REGISTRY_PATH = ROOT / "content" / "product_registry.json"

# 原稿の作り方を変えたら上げる（全ページを作り直す）
PROMPT_VERSION = 1
JST = timezone(timedelta(hours=9))

# 製品の候補を拾うジャンル。照合（関連記事）は投資・ビジネスの記事も含める（不動産などは対象外）
GENRES = {"ai", "tech-gadget"}
MATCH_GENRES = GENRES | {"science", "business", "investment"}
# プレスリリース配信は検索に出していないので材料にしない（lib/indexing.ts と同じ）
WIRE_SOURCES = {"prtimes", "prtimes-all", "atpress", "value-press", "kyodo-prwire", "dreamnews"}

# lib/companies.ts の slug（色・企業ページがある企業）。それ以外は other + companyName
COMPANY_SLUGS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "nvidia": "NVIDIA",
    "microsoft": "Microsoft",
    "meta": "Meta",
    "huggingface": "Hugging Face",
    "deepseek": "DeepSeek",
    "xai": "xAI",
    "mistral": "Mistral AI",
    "moonshot": "Moonshot AI",
}
CATEGORIES = ["AIモデル", "アプリ・サービス", "開発ツール", "ガジェット", "半導体・インフラ", "ロボット"]

# 明らかに製品名ではない語（LLM に聞くまでもないもの）
STOPWORDS = {
    s.casefold()
    for s in [
        "AI", "LLM", "API", "MCP", "RAG", "MoE", "GPU", "CPU", "TPU", "SQL", "OSS", "SDK", "CLI", "UI", "UX", "VLA",
        "AIエージェント", "生成AI", "OpenAI", "NVIDIA", "Google", "Microsoft", "Anthropic", "Meta", "Apple", "Amazon",
        "AWS", "Azure", "xAI", "DeepSeek", "Hugging Face", "Google DeepMind", "DeepMind", "Moonshot AI", "Mistral AI",
        "Microsoft Research", "マイクロソフト", "Sam Altman", "GTC 2026", "Python", "TypeScript", "C#", "Go言語",
        "Windows", "macOS", "Linux", "iOS", "Android", "AI Slop", "Model Context Protocol", "Computer Use",
        "Mixture of Experts", "Embodied AI", "Confidential Computing",
    ]
}

MAX_SOURCES = 10  # 原稿の材料にする記事の上限
MIN_SCORE = 12  # これより項目の少ない原稿は保存しない（概要5点＋早見表・各項目・FAQ 1点ずつ）
PRIMARY_CHARS = 3500
SECONDARY_CHARS = 1500


def log(message: str) -> None:
    print(message, flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(text: str) -> str:
    """全角→半角、ハイフンの揺れをそろえる"""
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[‐‑‒–—−]", "-", text)


def slugify(name: str) -> str:
    s = norm(name).casefold()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> bool:
    """中身が変わったときだけ書く（無駄なコミットを出さない）"""
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text("utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return True


# --------------------------------------------------------------------------- #
# 記事
# --------------------------------------------------------------------------- #


def load_articles() -> list[dict]:
    articles = []
    for path in ARTICLES_DIR.glob("*.json"):
        data = load_json(path, None)
        if not isinstance(data, dict) or not data.get("slug"):
            continue
        if data.get("genreSlug") not in MATCH_GENRES or data.get("channelSlug") in WIRE_SOURCES:
            continue
        if data.get("indexable") is False:
            continue
        articles.append(data)
    articles.sort(key=lambda a: a.get("publishedAt", ""), reverse=True)
    return articles


def jst_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(JST).date().isoformat()
    except (ValueError, AttributeError):
        return ""


def article_version(article: dict) -> str:
    return f"{article['slug']}@{article.get('updatedAt') or article.get('generatedAt') or article.get('publishedAt', '')}"


# --------------------------------------------------------------------------- #
# 1. 候補と登録簿
# --------------------------------------------------------------------------- #


def known_names(registry: dict) -> set[str]:
    names = {s.casefold() for s in registry.get("rejected", [])}
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    names.update(s.casefold() for s, at in registry.get("deferred", {}).items() if at > week_ago)
    for product in registry.get("products", []):
        names.add(product["name"].casefold())
        names.update(alias.casefold() for alias in product.get("aliases", []))
    return names


def candidate_terms(article: dict) -> set[str]:
    terms = set()
    for value in list(article.get("keywords") or []) + list(article.get("tags") or []):
        if isinstance(value, str):
            terms.add(norm(value).strip())
    for field in ("title", "seoTitle"):
        for quoted in re.findall(r"「([^」]{2,40})」", norm(article.get(field) or "")):
            terms.add(quoted.strip())
    return {t for t in terms if 2 <= len(t) <= 40 and re.search(r"[A-Za-z]", t) and t.casefold() not in STOPWORDS}


def leads_title(name: str, article: dict) -> bool:
    """題名が製品名から始まる（その製品についての記事）"""
    for field in ("seoTitle", "title"):
        pos = norm(article.get(field) or "").casefold().find(norm(name).casefold())
        if 0 <= pos <= 2:
            return True
    return False


def find_candidates(articles: list[dict], registry: dict) -> list[dict]:
    """登録簿に無い製品名らしき語を、出てくる記事の数つきで返す"""
    known = known_names(registry)
    found: dict[str, dict] = {}
    for article in articles:
        title = norm(f"{article.get('title', '')} {article.get('seoTitle', '')}")
        for term in candidate_terms(article):
            key = term.casefold()
            if key in known:
                continue
            entry = found.setdefault(key, {"name": term, "count": 0, "inTitle": 0, "titles": []})
            entry["count"] += 1
            # 題名に出てくるのが AI・ガジェットの記事のときだけ候補にする（投資記事の銘柄名などを拾わない）
            if article.get("genreSlug") in GENRES and term.casefold() in title.casefold():
                entry["inTitle"] += 1
                if len(entry["titles"]) < 2:
                    entry["titles"].append(article.get("seoTitle") or article.get("title", ""))
    picked = [c for c in found.values() if c["inTitle"] >= 1 and c["count"] >= 2]
    return sorted(picked, key=lambda c: (-c["inTitle"], -c["count"]))


CLASSIFY_SYSTEM = """あなたは日本のITニュースサイトの編集者です。記事に出てきた語の一覧を見て、
「製品ページ（〇〇とは？料金・使い方・日本で使えるか）」を作る価値がある製品・AIモデル・アプリ・ガジェットかを判定します。

製品として扱う（kind=product）:
- 名前で検索される具体的なAIモデル（例: GPT-6.1 Terra、Claude Opus 5.5、Gemini 4 Argon、Kimi K3）
- アプリ・サービス・機能名で、それ自体を使える・契約できるもの（例: ChatGPT Work、GitHub Copilot、Codex）
- ハードウェア製品（例: iPhone Duo、RTX Spark、Pixel Watch 5）
扱わない（kind=reject）:
- 企業・人名・イベント・研究プロジェクト・論文・技術用語・手法・規格・プロトコル・一般名詞
- 何のことか語だけでは特定できない短い一般語（例: Sites、dot、Apollo）は、製品なら「提供元の名前を付けた正式名」にする（例: Codex Sites）。無理なら reject

name は公式の表記にそろえた正式名。表記揺れ（Claude 5.5 Opus → Claude Opus 5.5）は正式名に直す。
既存の製品の別表記なら、name を既存の製品名と完全に同じにする。
company は次のどれか: openai, anthropic, google, nvidia, microsoft, meta, huggingface, deepseek, xai, mistral, moonshot, other
companyName は提供元の企業名（例: OpenAI、Apple、Xiaomi）。
category は次のどれか: AIモデル, アプリ・サービス, 開発ツール, ガジェット, 半導体・インフラ, ロボット"""

CLASSIFY_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "candidate": {"type": "STRING"},
                    "kind": {"type": "STRING", "enum": ["product", "reject"]},
                    "name": {"type": "STRING"},
                    "company": {"type": "STRING"},
                    "companyName": {"type": "STRING"},
                    "category": {"type": "STRING"},
                },
                "required": ["candidate", "kind", "name", "company", "companyName", "category"],
            },
        }
    },
    "required": ["items"],
}


def classify(candidates: list[dict], registry: dict, tier: str) -> None:
    """新しい候補を LLM で判定して登録簿に足す"""
    existing = [p["name"] for p in registry.get("products", [])]
    by_slug = {p["slug"]: p for p in registry.get("products", [])}
    rejected = set(registry.get("rejected", []))
    deferred = dict(registry.get("deferred", {}))
    for start in range(0, len(candidates), 50):
        chunk = candidates[start : start + 50]
        lines = "\n".join(f"- {c['name']}（{c['count']}本。例: {' / '.join(c['titles'])}）" for c in chunk)
        prompt = (
            f"既存の製品: {', '.join(existing) or 'なし'}\n\n"
            f"次の語を1つずつ判定してください。candidate は一覧の語をそのまま返すこと。\n{lines}"
        )
        try:
            data, model = gen_json(CLASSIFY_SYSTEM, prompt, CLASSIFY_SCHEMA, tier, temperature=0.1, max_output_tokens=8192)
        except Exception as error:  # noqa: BLE001
            log(f"[products] 判定に失敗: {error}")
            return
        log(f"[products] {len(chunk)} 語を判定（{model}）")
        names = {c["name"].casefold(): c["name"] for c in chunk}
        answered = set()
        for item in data.get("items", []):
            # 「Kimi（2本。例: …）」のように一覧の行ごと返すモデルがあるので、かっこ以降は捨てる
            cand = names.get(re.sub(r"[（(].*$", "", norm(item.get("candidate", ""))).strip().strip("-").strip().casefold())
            if not cand:
                continue
            answered.add(cand)
            if item.get("kind") != "product" or item.get("category") not in CATEGORIES:
                rejected.add(cand)
                continue
            name = norm(item.get("name") or cand).strip()
            slug = slugify(name)
            if not slug:
                rejected.add(cand)
                continue
            company = item.get("company") if item.get("company") in COMPANY_SLUGS else "other"
            product = by_slug.get(slug)
            if product is None:
                product = {
                    "slug": slug,
                    "name": name,
                    "aliases": [],
                    "company": company,
                    "companyName": COMPANY_SLUGS.get(company) or norm(item.get("companyName") or "").strip() or "その他",
                    "category": item["category"],
                }
                by_slug[slug] = product
                existing.append(name)
            if cand.casefold() != product["name"].casefold() and cand not in product["aliases"]:
                product["aliases"].append(cand)
        # 返事が無かった語は、毎回聞き直さないよう7日間あける
        for cand in names.values():
            if cand not in answered:
                deferred[cand] = now_iso()
    registry["products"] = sorted(by_slug.values(), key=lambda p: p["slug"])
    registry["rejected"] = sorted(rejected, key=str.casefold)
    registry["deferred"] = dict(sorted(deferred.items()))


# --------------------------------------------------------------------------- #
# 2. 照合
# --------------------------------------------------------------------------- #


def build_matcher(products: list[dict]) -> list[tuple[re.Pattern, str]]:
    """長い別名から順に照合する（GPT-6 が GPT-6.1 Terra の中で当たらないように）"""
    entries = []
    for product in products:
        for alias in {product["name"], *product.get("aliases", [])}:
            alias = norm(alias).strip()
            if len(alias) < 2:
                continue
            body = r"\s*".join(re.escape(part) for part in alias.split())
            flags = 0 if len(alias) <= 4 else re.IGNORECASE
            pattern = re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9]|\.\d)", flags)
            entries.append((len(alias), pattern, product["slug"]))
    entries.sort(key=lambda e: -e[0])
    return [(pattern, slug) for _, pattern, slug in entries]


def find_mentions(text: str, matcher) -> dict[str, int]:
    """文中に出てくる製品 → 最初の位置"""
    text = norm(text)
    taken = [False] * len(text)
    found: dict[str, int] = {}
    for pattern, slug in matcher:
        for m in pattern.finditer(text):
            if any(taken[m.start() : m.end()]):
                continue
            for i in range(m.start(), m.end()):
                taken[i] = True
            found[slug] = min(found.get(slug, m.start()), m.start())
    return found


def match_articles(articles: list[dict], products: list[dict]) -> tuple[dict[str, dict], dict[str, dict[str, int]]]:
    """製品ごとの {primary: [...], secondary: [...]}（記事は新しい順）と、記事ごとの「題名に出てくる製品 → 位置」"""
    matcher = build_matcher(products)
    result = {p["slug"]: {"primary": [], "secondary": []} for p in products}
    positions: dict[str, dict[str, int]] = {}
    for article in articles:
        title = f"{article.get('title', '')}\n{article.get('seoTitle', '')}"
        in_title = find_mentions(title, matcher)
        extra = find_mentions(" | ".join((article.get("keywords") or []) + (article.get("tags") or [])), matcher)
        for slug in in_title:
            result[slug]["primary"].append(article)
        for slug in extra:
            if slug not in in_title:
                result[slug]["secondary"].append(article)
        if in_title:
            positions[article["slug"]] = in_title
    return result, positions


def assign_leads(positions: dict[str, dict[str, int]], hubs: set[str]) -> dict[str, list[str]]:
    """記事から案内するまとめページは1つだけ。題名でいちばん手前に出てくる製品（ページがあるもの）にする"""
    leads: dict[str, list[str]] = {slug: [] for slug in hubs}
    for article_slug, found in positions.items():
        candidates = {s: pos for s, pos in found.items() if s in hubs}
        if candidates:
            leads[min(candidates, key=lambda s: candidates[s])].append(article_slug)
    return leads


def qualifies(product: dict, match: dict) -> bool:
    # 登録簿で "hub": false にした製品（範囲が広すぎる・記事が本筋でない）は作らない
    if product.get("hub") is False:
        return False
    primary, secondary = match["primary"], match["secondary"]
    # 記事1〜2本の言い換えだけのページは、元の記事と検索で食い合うので作らない
    if len(primary) >= 3 or (len(primary) == 2 and secondary):
        return True
    # 題名に出てくる記事が1本でも、その製品についての記事（題名が製品名で始まり「使えるか」の判定つき）があり、
    # ほかの記事にもよく登場する製品（Claude のような総称）は作る
    if len(primary) != 1 or len(secondary) < 3 or not primary[0].get("verdict"):
        return False
    return any(leads_title(n, primary[0]) for n in [product["name"], *product.get("aliases", [])])


# --------------------------------------------------------------------------- #
# 3. 原稿の材料
# --------------------------------------------------------------------------- #

PRIORITY_HEAD = re.compile(r"料金|価格|プラン|提供|日本|使い方|始め方|違い|比較|できる|機能|性能|特徴|注意|対象|いつ")


def clean_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def excerpt(body: str, limit: int) -> str:
    """本文から「ひとことで言うと」と料金・提供・違いの章を優先して抜き出す"""
    parts = re.split(r"(?m)^##\s+", clean_markdown(body))
    ranked = []
    for index, part in enumerate(parts):
        head = part.split("\n", 1)[0]
        prio = 0 if index <= 1 or "ひとこと" in head else 1 if PRIORITY_HEAD.search(head) else 2
        ranked.append((prio, index, part))
    chosen, used = [], 0
    for prio, index, part in sorted(ranked):
        if used >= limit:
            break
        piece = part[: limit - used]
        chosen.append((index, piece))
        used += len(piece)
    return "\n\n".join(("## " + p if i > 0 else p) for i, p in sorted(chosen)).strip()


def pick_sources(match: dict) -> list[tuple[dict, bool]]:
    """材料にする記事。題名に出てくる記事を優先し、最初の発表（流れの起点）・まとめ記事・判定つきの新しい記事の順に"""
    primary = sorted(match["primary"], key=lambda a: a.get("publishedAt", ""), reverse=True)
    picked: dict[str, dict] = {}

    def add(article: dict) -> None:
        if article["slug"] not in picked and len(picked) < MAX_SOURCES - 2:
            picked[article["slug"]] = article

    if primary:
        add(primary[-1])
    for group in (
        [a for a in primary if a.get("type") == "roundup"],
        [a for a in primary if a.get("verdict")],
        primary,
    ):
        for article in group:
            add(article)
    chosen = [(a, True) for a in picked.values()]
    rest = MAX_SOURCES - len(chosen)
    chosen += [(a, False) for a in match["secondary"][:rest]]
    chosen.sort(key=lambda x: x[0].get("publishedAt", ""), reverse=True)
    return chosen


JAPAN = {"ok": "利用できる", "partial": "条件付き", "no": "日本では未提供", "unknown": "未確認"}
STATUS = {"available": "今すぐ使える", "limited": "一部で提供中", "soon": "提供はこれから", "demo": "研究・デモ段階"}


def source_block(article: dict, primary: bool) -> str:
    mark = "題名に製品名あり" if primary else "関連して登場"
    lines = [f"### [{article['slug']}] {jst_date(article.get('publishedAt', ''))} {article.get('title', '')}（{mark}）"]
    if article.get("highlights"):
        lines.append("要点: " + " / ".join(article["highlights"]))
    verdict = article.get("verdict") if article.get("type") != "roundup" else None
    if isinstance(verdict, dict):
        free = {True: "無料で試せる", False: "有料のみ"}.get(verdict.get("free"), "無料かは未確認")
        lines.append(
            f"サイソク編集部の判定（公式の文言ではない）: 提供状況={STATUS.get(verdict.get('status'), '不明')} / 日本={JAPAN.get(verdict.get('japan'), '不明')}"
            f" / 料金={verdict.get('price') or '不明'} / {free} / 対象={verdict.get('audience') or '不明'}"
        )
    if article.get("description"):
        lines.append("説明: " + article["description"])
    lines.append("本文抜粋:\n" + excerpt(article.get("body", ""), PRIMARY_CHARS if primary else SECONDARY_CHARS))
    return "\n".join(lines)


GEN_SYSTEM = """あなたは日本のITニュースサイト「サイソク」の編集者です。
製品ごとの「まとめページ」の原稿を、渡された記事だけを根拠に書きます。

## 絶対規則
- 渡された記事に書かれている事実だけを使う。記事に無い数字・日付・料金・機能・提供国を足さない。推測しない。
- 数字・日付・料金・製品名は記事の表記のまま書き写す（単位や通貨を換算しない）。
- すべての項目の sources に、その内容が書かれている記事の slug（### [ ] の中の英数字）を1つ以上入れる。
- 1〜2本の記事だけに頼らず、渡された記事を広く使う（新しい記事の内容も入れる）。
- 記事に根拠が無い項目は空配列にする。埋めるために一般論を書かない。
- 原稿の文中に「記事」「本記事」という言葉を書かない（読者には「発表では」「発表の時点では」と書く）。
- 日本での提供・料金は、記事に根拠が無ければ空配列にする（「案内はありません」「記載なし」「未定」のような答えになっていない文は書かない。図鑑では公式の料金表・提供状況の表を別に出す）。
- 「サイソク編集部の判定」の行を根拠にするときは「案内されています」「発表されています」と書かない（公式の文言ではないため）。「日本からも利用できます」のように書く。
- よくある質問は、記事に答えが書いてある質問だけにする（「言及がありません」と答える質問は作らない）。
- 1文は50字以内。です・ます調。1項目は2文まで。
- 誇張・煽り（革命的・衝撃・圧倒的）と感嘆符は使わない。「〜とされています」を多用しない。
- 読者は「〇〇とは？」「料金は？」「日本で使える？」と検索してきた一般の人。専門用語は短く言い換える。"""

GEN_PROMPT = """製品: {name}（提供元: {company}、分類: {category}）
別名: {aliases}

次の {count} 本の記事だけを根拠に、まとめページの原稿を JSON で作ってください。

## 作る項目
- overview: 「{name}とは？」への答え。「{name}は」で始めて2〜3文で、何をする製品か・誰が出したか・いつ発表されたか。
- facts: 早見表。label は「料金」「提供開始」「日本での提供」「使える場所」「向いている人」のうち、記事に根拠があるものだけ。value は25字以内の短い答え。
- capabilities: 「何ができる？」3〜5項目。具体的にできること（数字があれば添える）。
- pricing: 「料金は？」1〜3項目。料金・プラン・無料で試せるか。記事に料金が無ければ空配列。
- japan: 「日本で使える？」1〜2項目。
- howto: 「どうやって使う？」1〜3項目。使える場所（アプリ・プラン・API など）と始め方。根拠が無ければ空配列。
- differences: 「前のモデルと何が違う？」0〜4項目。前の版・従来製品との違いが記事にあるときだけ。
- faq: よくある質問を4〜6問。上の見出しと同じ質問は入れない。実際に検索されそうな短い質問にする（例: 無料で使える？ 〇〇との違いは？ いつから使える？ 安全性は？ 誰に向いている？）。答えは2文以内。
- timeline: 「これまでの流れ」。発表・提供開始・値下げなどの出来事を、古いものから最大8件。1件25字以内（例: 「GPT-6.1 Terraを発表」「ChatGPTで提供開始」）。slug はその出来事が書かれた記事の slug（「題名に製品名あり」の記事だけ）。同じ出来事を扱う記事が複数あっても1件だけにし、同じ日の出来事は2件まで。デモ動画の紹介や使い方の解説は出来事に入れない。

## 記事（新しい順）
{sources}"""

ITEM = {
    "type": "OBJECT",
    "properties": {"text": {"type": "STRING"}, "sources": {"type": "ARRAY", "items": {"type": "STRING"}}},
    "required": ["text", "sources"],
}
GEN_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "overview": ITEM,
        "facts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "label": {"type": "STRING"},
                    "value": {"type": "STRING"},
                    "sources": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["label", "value", "sources"],
            },
        },
        "capabilities": {"type": "ARRAY", "items": ITEM},
        "pricing": {"type": "ARRAY", "items": ITEM},
        "japan": {"type": "ARRAY", "items": ITEM},
        "howto": {"type": "ARRAY", "items": ITEM},
        "differences": {"type": "ARRAY", "items": ITEM},
        "faq": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "q": {"type": "STRING"},
                    "a": {"type": "STRING"},
                    "sources": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["q", "a", "sources"],
            },
        },
        "timeline": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"slug": {"type": "STRING"}, "event": {"type": "STRING"}},
                "required": ["slug", "event"],
            },
        },
    },
    "required": ["overview", "facts", "capabilities", "pricing", "japan", "howto", "differences", "faq", "timeline"],
    "propertyOrdering": ["overview", "facts", "capabilities", "pricing", "japan", "howto", "differences", "faq", "timeline"],
}
LIST_FIELDS = ["capabilities", "pricing", "japan", "howto", "differences"]
FACT_LABELS = ["料金", "提供開始", "日本での提供", "使える場所", "向いている人"]


# --------------------------------------------------------------------------- #
# 4. 数字の照合（原稿の数字が出典の記事に本当にあるか）
# --------------------------------------------------------------------------- #

UNIT_ALIASES = {
    "%": "%", "パーセント": "%",
    "ドル": "USD", "米ドル": "USD", "$": "USD", "USD": "USD",
    "円": "JPY", "¥": "JPY",
    "倍": "x",
}
UNITS = (
    r"パーセント|米ドル|ドル|ユーロ|円|%|倍|兆|億|万|千|年|か月|ヶ月|カ月|月|日|時間|分|秒|週間|"
    r"GB|TB|MB|ms|fps|GHz|Hz|MW|GW|kW|W|nm|mAh|インチ|件|本|人|社|個|行|台|回|語|字|文字|枚|言語|"
    r"カ国|か国|トークン|パラメータ|ポイント|位|コア|B|K|M|T"
)
NUM_RE = re.compile(r"(?P<cur>[$¥])?(?P<num>\d+(?:\.\d+)?)(?:\s?(?P<big>万|億|兆)(?P<rest>\d+(?:\.\d+)?)?)?\s?(?P<unit>" + UNITS + r")?")
DATE_RE = re.compile(r"(?:(?P<y>\d{4})年)?(?P<m>\d{1,2})月(?:(?P<d>\d{1,2})日)?")
ISO_DATE_RE = re.compile(r"(?P<y>\d{4})[/-](?P<m>\d{1,2})[/-](?P<d>\d{1,2})")
BIG = {"万": 10_000, "億": 100_000_000, "兆": 1_000_000_000_000}


def _canon_number(match: re.Match) -> tuple[str, str]:
    value = float(match.group("num"))
    big = match.group("big")
    if big:
        value = value * BIG[big] + float(match.group("rest") or 0)
    unit = match.group("unit") or ""
    if match.group("cur"):
        unit = UNIT_ALIASES[match.group("cur")]
    unit = UNIT_ALIASES.get(unit, unit)
    if big and unit in ("万", "億", "兆"):
        unit = ""
    num = f"{value:.6f}".rstrip("0").rstrip(".")
    return num, unit


def prepare(text: str) -> str:
    text = norm(text)
    return re.sub(r"(?<=\d),(?=\d{3})", "", text)


def date_tokens(text: str) -> set[tuple]:
    tokens = set()
    for m in DATE_RE.finditer(text):
        tokens.add((m.group("y"), int(m.group("m")), int(m.group("d")) if m.group("d") else None))
    for m in ISO_DATE_RE.finditer(text):
        tokens.add((m.group("y"), int(m.group("m")), int(m.group("d"))))
    return tokens


def number_tokens(text: str) -> set[tuple[str, str]]:
    """日付の部分を除いた数字（値と単位）"""
    text = DATE_RE.sub(" ", ISO_DATE_RE.sub(" ", text))
    return {_canon_number(m) for m in NUM_RE.finditer(text)}


class SourceFacts:
    """出典の記事に書かれている数字・日付の集合"""

    def __init__(self, articles: list[dict]):
        parts = []
        self.dates: set[tuple] = set()
        for article in articles:
            verdict = article.get("verdict") or {}
            parts += [
                article.get("title", ""), article.get("seoTitle", ""), article.get("lede", ""),
                article.get("description", ""), " ".join(article.get("highlights") or []),
                str(verdict.get("price", "")), str(verdict.get("audience", "")), str(verdict.get("take", "")),
                article.get("body", ""), article.get("easy", ""),
            ]
            for key in ("publishedAt", "generatedAt"):
                day = jst_date(article.get(key, ""))
                if day:
                    y, m, d = day.split("-")
                    self.dates.add((y, int(m), int(d)))
        text = prepare("\n".join(parts))
        self.dates |= date_tokens(text)
        self.numbers = number_tokens(text)
        self.values = {num for num, _ in self.numbers}

    def date_ok(self, token: tuple) -> bool:
        y, m, d = token
        for sy, sm, sd in self.dates:
            if sm != m or (y and sy and sy != y):
                continue
            if d is None or sd == d:
                return True
        return False

    def number_ok(self, token: tuple[str, str]) -> bool:
        num, unit = token
        if token in self.numbers:
            return True
        # 単位なしの数字（製品名の版番号など）は、値がどこかにあればよい
        if not unit:
            return num in self.values
        # 「4月」の月や「2026年」の年は日付側で確かめる
        if unit == "年":
            return any(sy == num for sy, _, _ in self.dates) or num in self.values
        return False


def unsupported(text: str, facts: SourceFacts) -> list[str]:
    """出典に無い数字・日付（表記のまま）"""
    text = prepare(text)
    bad = []
    for m in DATE_RE.finditer(text):
        token = (m.group("y"), int(m.group("m")), int(m.group("d")) if m.group("d") else None)
        if not facts.date_ok(token):
            bad.append(m.group(0))
    rest = DATE_RE.sub(" ", ISO_DATE_RE.sub(" ", text))
    for m in NUM_RE.finditer(rest):
        if not facts.number_ok(_canon_number(m)):
            bad.append(m.group(0).strip())
    return bad


LONG = 60  # これを超える文は書き直しを頼む（目安は50字）
TOO_LONG = 90  # これを超える文を含む項目は捨てる（読みにくい）


def long_sentences(text: str, limit: int = LONG) -> list[str]:
    return [s for s in re.split(r"(?<=[。！？])", text) if len(s.strip()) > limit]


# --------------------------------------------------------------------------- #
# 5. 原稿の生成と検査
# --------------------------------------------------------------------------- #


def valid_sources(sources, allowed: set[str]) -> list[str]:
    out = []
    for slug in sources or []:
        slug = str(slug).strip().strip("[]")
        if slug in allowed and slug not in out:
            out.append(slug)
    return out


DUP_QUESTIONS = [
    ("pricing", "料金は？", re.compile(r"料金|価格|いくら|値段")),
    ("japan", "日本で使える？", re.compile(r"日本|国内")),
    ("capabilities", "何ができる？", re.compile(r"何ができ|(どのような|どんな)(こと|操作|作業|タスク)(が|を)|できることは")),
    ("howto", "どうやって使う？", re.compile(r"使い方|どうやって|始め方|どこで使え|どのような環境|どんな環境")),
]


# 答えになっていない文（2026-11-03 オーナーの指摘「内容がすごく薄い」・台帳 W13）。サイトの lib/product-substance.ts の NON_ANSWER_RE と同じ
NON_ANSWER_RE = re.compile(
    r"元記事|記載(?:は|が)?(?:なし|ありません|ない|されていません)|言及(?:は|が)?(?:なし|ありません|されていません)|判断でき(?:ません|ない)|"
    r"未発表|未定|不明|明らかにされていません|発表されていません|わかっていません|分かっていません|案内はありません"
)


def non_answer(text: str) -> bool:
    """答えになっていない文を含むか（図鑑の原稿に入れない）"""
    return bool(NON_ANSWER_RE.search(text or ""))


def check_draft(
    draft: dict, by_slug: dict[str, dict], events: set[str] | None = None, want_sources: int = 0
) -> tuple[dict, list[str]]:
    """出典の付け方と数字を確かめる。(合格した部分だけの原稿, 問題点の一覧)
    events: 「これまでの流れ」に使ってよい記事（題名に製品名が出てくる記事）。None なら全部"""
    allowed = set(by_slug)
    problems: list[str] = []
    cache: dict[tuple, SourceFacts] = {}

    def facts_for(sources: list[str]) -> SourceFacts:
        key = tuple(sorted(sources))
        if key not in cache:
            cache[key] = SourceFacts([by_slug[s] for s in sources])
        return cache[key]

    def check(label: str, text: str, sources) -> list[str] | None:
        """合格なら出典の slug 一覧、不合格なら None"""
        text = (text or "").strip()
        srcs = valid_sources(sources, allowed)
        if not text:
            return None
        if non_answer(text):
            problems.append(f"{label}: 答えになっていない文（記載なし・元記事には・未定など）→「{text[:40]}」")
            return None
        if not srcs:
            problems.append(f"{label}: 出典の slug がない、または記事一覧に無い slug →「{text[:40]}」")
            return None
        bad = unsupported(text, facts_for(srcs))
        if bad:
            problems.append(f"{label}: 出典の記事に無い数字・日付 {bad} →「{text[:60]}」（出典 {srcs}）")
            return None
        for sentence in long_sentences(text):
            problems.append(f"{label}: 1文が長すぎる（50字以内に）→「{sentence.strip()[:40]}…」")
        if long_sentences(text, TOO_LONG):
            return None
        return srcs

    out: dict = {}
    overview = draft.get("overview") or {}
    srcs = valid_sources(overview.get("sources"), allowed)
    text = (overview.get("text") or "").strip()
    if text and srcs:
        # 概要は文ごとに確かめ、合わない文だけ落とす
        kept = []
        for sentence in [s for s in re.split(r"(?<=。)", text) if s.strip()]:
            if non_answer(sentence):
                problems.append(f"overview: 答えになっていない文 →「{sentence[:60]}」")
                continue
            bad = unsupported(sentence, facts_for(srcs))
            if bad:
                problems.append(f"overview: 出典の記事に無い数字・日付 {bad} →「{sentence[:60]}」")
                continue
            if len(sentence.strip()) > LONG:
                problems.append(f"overview: 1文が長すぎる（50字以内に）→「{sentence.strip()[:40]}…」")
                if len(sentence.strip()) > TOO_LONG and kept:
                    continue
            kept.append(sentence.strip())
        if kept:
            out["overview"] = {"text": "".join(kept), "sources": srcs}
    else:
        problems.append("overview: 本文か出典がない")

    facts = []
    seen_labels = set()
    for i, fact in enumerate(draft.get("facts") or []):
        label = (fact.get("label") or "").strip()
        if label not in FACT_LABELS or label in seen_labels:
            continue
        ok = check(f"facts[{label}]", fact.get("value", ""), fact.get("sources"))
        if ok:
            seen_labels.add(label)
            facts.append({"label": label, "value": fact["value"].strip(), "sources": ok})
    facts.sort(key=lambda f: FACT_LABELS.index(f["label"]))
    out["facts"] = facts

    for field in LIST_FIELDS:
        items = []
        for i, item in enumerate(draft.get(field) or []):
            # 「日本で使える」はサイソクの判定にもとづくことが多いので、公式が案内したような書き方はしない
            if field == "japan" and re.search(r"案内されて|発表されて", item.get("text", "")) and not re.search(r"案内はありません|発表はありません", item.get("text", "")):
                problems.append(f"japan[{i}]: 「案内されています」と書かない（例: 日本からも利用できます）→「{item.get('text', '')[:40]}」")
                continue
            ok = check(f"{field}[{i}]", item.get("text", ""), item.get("sources"))
            if ok:
                items.append({"text": item["text"].strip(), "sources": ok})
        out[field] = items

    faq = []
    for i, item in enumerate(draft.get("faq") or []):
        q = (item.get("q") or "").strip()
        # 見出し（料金は？・日本で使える？など）と同じ質問は、ページの中で答えが重なるので入れない
        dup = next((title for field, title, pattern in DUP_QUESTIONS if out.get(field) and pattern.search(q)), None)
        if dup:
            problems.append(f"faq[{i}]: 見出し「{dup}」と同じ質問なので別の質問に →「{q}」")
            continue
        ok = check(f"faq[{i}]", f"{q}\n{item.get('a', '')}", item.get("sources"))
        if ok and q:
            faq.append({"q": q, "a": item["a"].strip(), "sources": ok})
    out["faq"] = faq

    timeline = []
    seen = set()
    for item in draft.get("timeline") or []:
        slug = str(item.get("slug", "")).strip().strip("[]")
        event = (item.get("event") or "").strip()
        if slug not in allowed or slug in seen or not event or (events is not None and slug not in events):
            continue
        bad = unsupported(event, facts_for([slug]))
        if bad:
            problems.append(f"timeline[{slug}]: 出典の記事に無い数字 {bad} →「{event}」")
            continue
        seen.add(slug)
        timeline.append({"date": jst_date(by_slug[slug].get("publishedAt", "")), "slug": slug, "event": event})
    # 同じ日の出来事は2件まで（デモ動画の連投などで流れが読みにくくなるため）
    timeline.sort(key=lambda t: t["date"])
    per_day: dict[str, int] = {}
    kept = []
    for item in timeline:
        per_day[item["date"]] = per_day.get(item["date"], 0) + 1
        if per_day[item["date"]] <= 2:
            kept.append(item)
    out["timeline"] = kept
    # 1〜2本の記事だけに頼った原稿は偏るので、ほかの記事も使うよう書き直しを頼む
    cited = set(out.get("overview", {}).get("sources", []))
    for field in ["facts", *LIST_FIELDS, "faq"]:
        for item in out.get(field, []):
            cited.update(item["sources"])
    if len(cited) < want_sources:
        problems.append(f"出典が {len(cited)} 本の記事に偏っている。ほかの記事（{want_sources} 本以上）の内容も使う")
    return out, problems


def gen_json(system: str, prompt: str, schema: dict, tier: str, **kwargs) -> tuple[dict, str]:
    """tier は "quality,bulk" のように並べると、前の段のモデルが全部だめなとき次の段を使う"""
    last: Exception | None = None
    for one in [t.strip() for t in tier.split(",") if t.strip()]:
        try:
            return llm.generate_json(system, prompt, schema, tier=one, **kwargs)
        except RuntimeError as error:
            last = error
            log(f"   [{one}] すべて失敗 → 次の段へ")
    raise RuntimeError(f"すべての段で失敗: {last}")


def all_exhausted(tier: str) -> bool:
    return all(llm.all_exhausted(t.strip()) for t in tier.split(",") if t.strip())


def generate(product: dict, sources: list[tuple[dict, bool]], tier: str) -> tuple[dict | None, str, list[str]]:
    by_slug = {a["slug"]: a for a, _ in sources}
    prompt = GEN_PROMPT.format(
        name=product["name"],
        company=product["companyName"],
        category=product["category"],
        aliases="、".join(product.get("aliases") or []) or "なし",
        count=len(sources),
        sources="\n\n".join(source_block(a, primary) for a, primary in sources),
    )
    draft, model = gen_json(GEN_SYSTEM, prompt, GEN_SCHEMA, tier, temperature=0.3, max_output_tokens=8192)
    events = {a["slug"] for a, primary in sources if primary}
    want = min(4, len(by_slug))
    checked, problems = check_draft(draft, by_slug, events, want)
    if problems:
        log(f"   問題 {len(problems)} 件 → 1回だけ書き直し")
        for p in problems[:8]:
            log(f"     - {p}")
        retry = (
            prompt
            + "\n\n## 前回の原稿\n"
            + json.dumps(draft, ensure_ascii=False)
            + "\n\n## 前回の原稿の問題（直して全項目を出し直す。数字は出典の記事の表記どおりに。根拠が無い項目は消す）\n"
            + "\n".join(f"- {p}" for p in problems[:30])
        )
        try:
            draft2, model = gen_json(GEN_SYSTEM, retry, GEN_SCHEMA, tier, temperature=0.2, max_output_tokens=8192)
            checked2, problems2 = check_draft(draft2, by_slug, events, want)
            if score(checked2) >= score(checked):
                checked, problems = checked2, problems2
        except Exception as error:  # noqa: BLE001
            log(f"   書き直しに失敗（最初の原稿の合格部分を使う）: {error}")
    # 検査で多く落ちて薄くなった原稿は保存しない（薄いページは出さない）
    if "overview" not in checked or len(checked["faq"]) < 3 or score(checked) < MIN_SCORE:
        return None, model, problems
    return checked, model, problems


def score(checked: dict) -> int:
    return (
        (5 if "overview" in checked else 0)
        + len(checked.get("facts", []))
        + sum(len(checked.get(f, [])) for f in LIST_FIELDS)
        + len(checked.get("faq", []))
    )


# --------------------------------------------------------------------------- #
# 6. 全体
# --------------------------------------------------------------------------- #


def signature(articles: list[dict]) -> str:
    versions = sorted(article_version(a) for a in articles)
    return hashlib.sha1(f"v{PROMPT_VERSION}\n".encode() + "\n".join(versions).encode()).hexdigest()[:16]


def needs_update(current: dict | None, match: dict) -> bool:
    """題名に出てくる記事が増えた・続報が付いたら作り直す。keywords だけの記事の変化は3日に1回まとめて"""
    if not current:
        return True
    if current.get("primarySignature") != signature(match["primary"]):
        return True
    if current.get("signature") == signature(match["primary"] + match["secondary"]):
        return False
    three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
    return str(current.get("updatedAt", "")) < three_days_ago


def related_products(slug: str, products: dict[str, dict], matches: dict[str, dict], hubs: set[str]) -> list[str]:
    """同じ記事に出てくる製品 → 同じ企業・分類の製品の順"""
    mine = {a["slug"] for a in matches[slug]["primary"] + matches[slug]["secondary"]}
    scored = []
    for other in hubs:
        if other == slug:
            continue
        theirs = {a["slug"] for a in matches[other]["primary"] + matches[other]["secondary"]}
        overlap = len(mine & theirs)
        same_company = products[other]["company"] == products[slug]["company"] and products[slug]["company"] != "other"
        same_category = products[other]["category"] == products[slug]["category"]
        score = overlap * 3 + (2 if same_company else 0) + (1 if same_category and same_company else 0)
        if score > 0:
            scored.append((score, len(theirs), other))
    scored.sort(reverse=True)
    return [s for _, _, s in scored[:4]]


def verify_all() -> int:
    """保存済みの全ページを出典の記事と照合し直す（記事が書き換わったときの確認用）"""
    every = {}
    for path in ARTICLES_DIR.glob("*.json"):
        data = load_json(path, None)
        if isinstance(data, dict) and data.get("slug"):
            every[data["slug"]] = data
    total = 0
    for path in sorted(PRODUCTS_DIR.glob("*.json")):
        data = load_json(path, {})
        by_slug = {s: every[s] for s in data.get("articles", []) if s in every}
        _, problems = check_draft(data, by_slug)
        problems = [p for p in problems if "長すぎる" not in p and "見出し" not in p]
        total += len(problems)
        log(f"{'NG' if problems else 'OK'} {path.stem}" + "".join(f"\n   - {p}" for p in problems))
    log(f"[products] 照合で見つかった問題: {total} 件")
    return 1 if total else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max", type=int, default=3, help="1回で原稿を作る製品数の上限")
    parser.add_argument("--budget", type=int, default=420, help="使ってよい秒数（超えそうなら次の製品に進まない）")
    parser.add_argument("--tier", default="bulk", help='bulk / quality / "quality,bulk"（前の段が全部だめなら次の段）')
    parser.add_argument("--only", help="この slug の製品だけ（カンマ区切りで複数）")
    parser.add_argument("--no-sync", action="store_true", help="最後の関連製品・記事一覧のそろえ直しをしない（並列で動かすとき）")
    parser.add_argument("--force", action="store_true", help="記事が変わっていなくても作り直す")
    parser.add_argument("--dry-run", action="store_true", help="候補と対象記事を表示するだけ（原稿は作らない）")
    parser.add_argument("--no-classify", action="store_true", help="新しい候補の判定（LLM）をしない")
    parser.add_argument("--verify", action="store_true", help="保存済みのページの数字・日付を出典と照合し直すだけ（LLM なし）")
    args = parser.parse_args()

    if args.verify:
        return verify_all()

    started = time.monotonic()
    llm._load_env_local()
    articles = load_articles()
    registry = load_json(REGISTRY_PATH, {"products": [], "rejected": []})

    # 1. 新しい候補だけ判定
    if not args.no_classify:
        candidates = find_candidates(articles, registry)
        if candidates:
            log(f"[products] 新しい候補 {len(candidates)} 語: {', '.join(c['name'] for c in candidates[:30])}")
            classify(candidates, registry, args.tier)
            registry["updatedAt"] = now_iso()
            write_json(REGISTRY_PATH, registry)

    products = {p["slug"]: p for p in registry.get("products", [])}
    matches, positions = match_articles(articles, list(products.values()))
    hubs = {slug for slug, m in matches.items() if qualifies(products[slug], m)}
    # 既にページがある製品は、記事が減っても消さない（外部からのリンクを切らない）
    for path in PRODUCTS_DIR.glob("*.json"):
        if path.stem in products and (matches[path.stem]["primary"] or matches[path.stem]["secondary"]):
            hubs.add(path.stem)

    log(f"[products] 製品 {len(products)} 件のうちページ対象 {len(hubs)} 件")
    # 検査に通らなかった製品は、記事が変わるまで（または7日たつまで）作り直さない（毎回 LLM を使わないため）
    failed: dict = registry.setdefault("failed", {})
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    queue = []
    for slug in sorted(hubs, key=lambda s: -len(matches[s]["primary"])):
        match = matches[slug]
        current = load_json(PRODUCTS_DIR / f"{slug}.json", None)
        stale = args.force or needs_update(current, match)
        log(
            f"  {'*' if stale else ' '} {products[slug]['name']}: 主 {len(match['primary'])} / 従 {len(match['secondary'])}"
            f"{'' if stale else '（変化なし）'}"
        )
        if args.only and slug not in args.only.split(","):
            continue
        last_fail = failed.get(slug) or {}
        if stale and not args.force and last_fail.get("signature") == signature(match["primary"] + match["secondary"]) and last_fail.get("at", "") > week_ago:
            log(f"    （{last_fail['at'][:10]} に検査で落ちたため、記事が増えるまで待つ）")
            continue
        if stale:
            queue.append(slug)

    if args.dry_run:
        return 0

    made = 0
    for slug in queue:
        if made >= args.max:
            log(f"[products] 上限 {args.max} 件に達したので残り {len(queue) - made} 件は次回")
            break
        if time.monotonic() - started > args.budget - 90:
            log("[products] 時間の上限が近いので次回に回す")
            break
        product = products[slug]
        match = matches[slug]
        sources = pick_sources(match)
        log(f"[products] {product['name']}: 記事 {len(sources)} 本から原稿を作成")
        try:
            content, model, problems = generate(product, sources, args.tier)
        except Exception as error:  # noqa: BLE001
            log(f"   失敗: {error}")
            if all_exhausted(args.tier):
                log("[products] すべてのモデルが上限。今回は終了")
                break
            continue
        made += 1
        if content is None:
            log(f"   検査に通らず保存しない（問題 {len(problems)} 件）")
            failed[slug] = {"at": now_iso(), "signature": signature(match["primary"] + match["secondary"])}
            continue
        failed.pop(slug, None)
        current = load_json(PRODUCTS_DIR / f"{slug}.json", {}) or {}
        all_articles = sorted(match["primary"] + match["secondary"], key=lambda a: a.get("publishedAt", ""), reverse=True)
        data = {
            "slug": slug,
            "name": product["name"],
            "aliases": product.get("aliases", []),
            "company": product["company"],
            "companyName": product["companyName"],
            "category": product["category"],
            **content,
            "related": [],
            "articles": [a["slug"] for a in all_articles],
            "linkFrom": [],
            "signature": signature(match["primary"] + match["secondary"]),
            "primarySignature": signature(match["primary"]),
            "model": model,
            "createdAt": current.get("createdAt") or now_iso(),
            "updatedAt": now_iso(),
        }
        write_json(PRODUCTS_DIR / f"{slug}.json", data)
        log(f"   保存: content/products/{slug}.json（FAQ {len(content['faq'])} 問・出典つき項目 {score(content)}）")

    if args.no_sync:
        return 0
    write_json(REGISTRY_PATH, registry)
    # 関連製品・記事一覧・案内元は毎回そろえる（LLM は使わない）
    live = {p.stem for p in PRODUCTS_DIR.glob("*.json")} & set(products)
    leads = assign_leads(positions, live)
    for slug in live:
        path = PRODUCTS_DIR / f"{slug}.json"
        data = load_json(path, None)
        if not data or slug not in products:
            continue
        match = matches[slug]
        all_articles = sorted(match["primary"] + match["secondary"], key=lambda a: a.get("publishedAt", ""), reverse=True)
        data["related"] = related_products(slug, products, matches, live)
        data["articles"] = [a["slug"] for a in all_articles] or data.get("articles", [])
        data["linkFrom"] = leads.get(slug, [])
        write_json(path, data)
    log(f"[products] 完了: 作成・更新 {made} 件 / ページ {len(live)} 件（{time.monotonic() - started:.0f} 秒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
