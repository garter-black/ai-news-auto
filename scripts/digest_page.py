#!/usr/bin/env python3
"""朝のまとめ「今朝のAIニュース3本」の号（edition）を作る。サイトの /digest/<YYYYMMDD> とシェア画像の元データ。

  python3 scripts/digest_page.py --backfill        # state/threads_posted.json の過去の朝まとめから content/digests/*.json を作る
  python3 scripts/digest_page.py --show 20261102   # 1号分の短い見出しを表示

- 号のデータは content/digests/<YYYYMMDD>.json（git に残す。デプロイ後はサイトに同梱）。
- 朝 7:05 に作った号はまだデプロイされていないので、Workers KV（saisoku-images）の digest-<YYYYMMDD>.json にも置く。
  サイト（lib/digest.ts）は同梱分 → KV の順に読む。KV への書き込みは CF_KV_TOKEN（無ければ CLOUDFLARE_API_TOKEN）。
- 見出しは short_headline（だれが＋何を）。会社名だけ（例:「Google DeepMind」）にはしない。
- シェア画像の案 C（AI のイラスト背景）のときだけ、号ごとに1枚 Workers AI で作って KV に置く（リクエストごとには作らない）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import headline_normalize  # noqa: E402  題名の門番（朝のまとめにも通す。台帳 A25）

ROOT = Path(__file__).resolve().parent.parent
DIGESTS_DIR = ROOT / "content" / "digests"
ARTICLES_DIR = ROOT / "content" / "articles"
DESIGN_FILE = ROOT / "lib" / "digest-design.ts"
JST = timezone(__import__("datetime").timedelta(hours=9))

ITEM_MAX = 40
# 日本語の間の空白で区切る（見出しの後半「〜の仕組み」などを落とす）。英語名の中の空白では切らない
SPLIT_RE = re.compile(r"(?<=[^\x00-\x7F])[ 　](?=[^\x00-\x7F])")
# 「何をしたか」が入っているかの目安（助詞・発表系の言葉）
PREDICATE_RE = re.compile(
    r"[をがは]|発表|公開|開始|提供|発売|登場|導入|買収|提携|刷新|強化|対応|拡大|終了|値上げ|値下げ|投入|搭載|参入|更新|改定|解禁|上場|決定|受賞|合意|新"
)
KANA_HIRA = re.compile(r"[ぁ-ゖ]")


def _core(text: str, names: list[str]) -> str:
    core = text
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        core = core.replace(name, "")
    return re.sub(r"[\x00-\x7F、，。！？「」『』・（）()\s]", "", core)


def meaningful(text: str, names: list[str] | None = None) -> bool:
    """会社名・製品名だけでなく「何が起きたか」まで入っているか"""
    core = _core(text, names or [])
    return len(core) >= 2 and bool(PREDICATE_RE.search(core))


def _names(article: dict) -> list[str]:
    return [str(article.get(k) or "") for k in ("channel", "sourceName")] + [str(t) for t in article.get("tags") or []]


INCOMPLETE_END = re.compile(r"(?:[がをのやしでとな、，・：:]|(?<!と)は)$")
NOUN_CHAR = re.compile(r"[^ぁ-ゖ、，。！？「」『』\s]")


def _outside_brackets(head: str) -> list[bool]:
    """各文字が「」『』の外か"""
    depth, out = 0, []
    for char in head:
        if char in "」』":
            depth = max(0, depth - 1)
        out.append(depth == 0)  # 括弧そのものは外側に数える
        if char in "「『":
            depth += 1
    return out


def _anchors(head: str) -> list[int]:
    """主語の区切り（最初の読点、無ければ「が」「は」）の直後の位置"""
    outside = _outside_brackets(head)
    for i, char in enumerate(head):
        if char in "、，" and outside[i] and i > 0:
            return [i + 1]
    return [i + 1 for i, char in enumerate(head) if char in "がは" and outside[i] and 1 < i < 20][:1]


def _shortened(head: str) -> tuple[list[str], list[str]]:
    """主語は残し、途中の長い説明を落とした候補（長いものから）。(製品名の括弧を残す版, 「〜の」の後ろだけ残す版)
    例:「Google DeepMind、言葉で遊べる世界を作る新AI「Genie 4」を発表」→「Google DeepMind、新AI「Genie 4」を発表」
       「アガタが、社内資料の…などの新サービスを開始」→「アガタが、新サービスを開始」"""
    bracketed: list[str] = []
    plain: list[str] = []
    outside = _outside_brackets(head)
    for anchor in _anchors(head):
        for i in range(anchor, len(head)):
            if head[i] in "「『" and outside[i]:
                # 括弧の直前の名詞（「新AI」「最新マルチモーダルAI」）ごと残す版と、括弧から始める版
                j = i
                while j > anchor and NOUN_CHAR.match(head[j - 1]):
                    j -= 1
                if j < i and i - j <= 12 and j > anchor:
                    bracketed.append(head[:anchor] + head[j:])
                if i > anchor:
                    bracketed.append(head[:anchor] + head[i:])
                break
        # 残す部分が短すぎる・数字から始まる（「10分の1へ」）ものは使わない
        plain += [
            head[:anchor] + head[i + 1:] for i in range(anchor, len(head) - 8)
            if head[i] == "の" and outside[i] and not head[i + 1].isdigit()
        ]
    return bracketed, plain


def short_headline(article: dict, limit: int = ITEM_MAX) -> str:
    """見出しを1行に（だれが＋何を）。会社名だけで終わる切り方はしない。
    元は記事の見出し（h1）を題名の門番に通したもの、できた1行もう一度通す（「ChatGPTのOpenAIが」を出さない。台帳 A25）"""
    return headline_normalize.strip_known_descriptors(_short_headline(article, limit))


def _short_headline(article: dict, limit: int = ITEM_MAX) -> str:
    title = headline_normalize.normalize_title(str(article.get("title") or article.get("seoTitle") or "").strip(), article)
    names = _names(article)
    segments = [s.strip() for s in SPLIT_RE.split(title) if s.strip()]
    if not segments:
        return ""
    head = segments[0]
    # 前半が名前だけなら、次の区切りまでつなぐ
    if not meaningful(head, names) and len(segments) > 1:
        head = f"{head} {segments[1]}"
    head = head.rstrip("、，。 ")

    def ok(text: str) -> bool:
        return len(text) <= limit and meaningful(text, names) and not INCOMPLETE_END.search(text)

    # 後ろの補足を切った版（読点・句点・全角空白・日本語の後の空白の手前）
    outside = _outside_brackets(head)
    cuts = sorted({
        i for i, c in enumerate(head)
        if outside[i] and i >= 12 and (c in "、，。！？　" or (c == " " and ord(head[i - 1]) > 0x7F))
    }, reverse=True)
    bases = [head] + [head[:i].rstrip() for i in cuts]
    # 1. そのまま／後ろを切っただけ  2. 主語と製品名を残して途中を落とす  3. 主語と「〜の」の後ろ
    shortened = [_shortened(base) for base in bases]
    for group in (bases, [t for b, _ in shortened for t in b], [t for _, p in shortened for t in p]):
        for text in group:
            if ok(text):
                return text
    # 4. どれも収まらない: 末尾を「…」で省く
    base = next((t for t in reversed(shortened[0][0]) if meaningful(t, names)), head)
    return base if len(base) <= limit else base[: limit - 1].rstrip("、，。 ") + "…"


def design() -> str:
    """lib/digest-design.ts の DIGEST_DESIGN（"A"・"E2" など。AI の背景を作るのは "C" のときだけ）"""
    try:
        match = re.search(r'DIGEST_DESIGN:[^=]*=\s*"([A-Z][0-9]?)"', DESIGN_FILE.read_text("utf-8"))
        return match.group(1) if match else "A"
    except OSError:
        return "A"


def image_rev() -> str:
    """lib/digest-design.ts の DIGEST_IMAGE_REV（画像URLの ?v=。無ければデザイン名）"""
    try:
        match = re.search(r'DIGEST_IMAGE_REV\s*=\s*"([A-Za-z0-9_-]+)"', DESIGN_FILE.read_text("utf-8"))
        return match.group(1) if match else design()
    except OSError:
        return design()


def edition(items: list[dict], when: datetime, heading: str) -> dict:
    jst = when.astimezone(JST)
    return {
        "id": f"{jst:%Y%m%d}",
        "date": f"{jst:%Y-%m-%d}",
        "heading": heading,
        "createdAt": when.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "items": [{"slug": a["slug"], "headline": short_headline(a)} for a in items],
    }


def path_of(edition_id: str) -> Path:
    return DIGESTS_DIR / f"{edition_id}.json"


def save(data: dict) -> Path:
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = path_of(data["id"])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return path


AI_SCENE = (
    "A single unified editorial illustration, not a collage and not a photograph, in a flat minimal vector style "
    "for a morning technology news briefing. Symbolic motifs blended into one calm scene: {themes}. "
    "Deep navy background with a soft sunrise glow, accents of muted teal and warm orange, clean geometric shapes, subtle paper grain. "
    "All motifs sit on the right half; the left half is calm, empty navy space. "
    "No text, no letters, no numbers, no logos, no brand marks, no watermark, no people, no hands, no faces, no screens with writing."
)


def ai_prompt(themes: list[str]) -> str:
    return AI_SCENE.format(themes=", ".join(t for t in themes[:3] if t) or "artificial intelligence")


def ai_background(data: dict, articles: list[dict], dry_run: bool = False) -> str | None:
    """案 C 用の背景（1号に1枚）。Workers AI で作り、KV に置いて /img/<名前>.jpg を返す。失敗したら None（写真の案で出す）"""
    import images  # noqa: PLC0415  （重い依存は案 C のときだけ）
    import llm  # noqa: PLC0415

    themes = []
    for article in articles:
        try:
            # 写真検索用の具体的な名詞（会社名・製品名・人名は含まない）を、イラストの題材にする
            queries = images.visual_plan(article).get("queries") or []
        except Exception:  # noqa: BLE001
            queries = []
        themes.append(str(queries[0] if queries else "")[:40])
    prompt = ai_prompt(themes)
    for model in images.AI_MODELS:
        try:
            raw = images._cf_ai(model, prompt, timeout=150 if "flux-2" in model else 60)  # noqa: SLF001
            image = images.open_image(raw)
            llm._record_usage("cf-image:" + model.rsplit("/", 1)[-1])  # noqa: SLF001
            break
        except Exception as error:  # noqa: BLE001
            print(f"[digest] AI 背景の生成に失敗（{model}: {type(error).__name__} {str(error)[:160]}）", flush=True)
    else:
        return None
    url, _ = images.store(f"digest-{data['id']}-bg", image, dry_run)
    return url


def publish(data: dict) -> bool:
    """未デプロイの号もサイトが読めるよう KV に置く。失敗しても投稿は止めない（False）"""
    try:
        import images  # noqa: PLC0415

        images.kv_put(f"digest-{data['id']}.json", json.dumps(data, ensure_ascii=False).encode("utf-8"))
        return True
    except Exception as error:  # noqa: BLE001
        print(f"[digest] 号のデータを KV に置けません（{type(error).__name__}）", flush=True)
        return False


def wait_live(url: str, tries: int = 12, interval: float = 10.0) -> bool:
    """サイトが画像を返すまで待つ（Buffer は予約の時点で画像を取りに来る）"""
    import time
    import urllib.request

    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "saisoku-digest/1.0"})
            with urllib.request.urlopen(req, timeout=40) as res:
                if res.status == 200 and res.headers.get_content_type().startswith("image/"):
                    return True
        except Exception:  # noqa: BLE001
            pass
        if attempt < tries - 1:
            time.sleep(interval)
    return False


def load_article(slug: str) -> dict | None:
    path = ARTICLES_DIR / f"{slug}.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else None


def backfill() -> int:
    state = json.loads((ROOT / "state" / "threads_posted.json").read_text("utf-8"))
    count = 0
    for collection in ("posted", "extraPending"):
        for key, entry in (state.get(collection) or {}).items():
            if entry.get("style") != "digest" or not key.startswith("digest-"):
                continue
            plan = entry.get("postPlan") or {}
            articles = [a for a in (load_article(s) for s in entry.get("items") or plan.get("items") or []) if a]
            if len(articles) < 2:
                continue
            when = datetime.fromisoformat(str(entry.get("dueAt") or entry.get("postedAt")).replace("Z", "+00:00"))
            first = str(plan.get("text") or "").splitlines()[0] if plan.get("text") else ""
            heading = re.sub(r"（.*$", "", first) or f"今朝のAIニュース{len(articles)}本"
            data = edition(articles, when, heading)
            data["id"] = key.removeprefix("digest-")
            data["date"] = f"{data['id'][:4]}-{data['id'][4:6]}-{data['id'][6:]}"
            old = path_of(data["id"])
            if old.exists():
                kept = json.loads(old.read_text("utf-8"))
                data = {**kept, **{k: data[k] for k in ("items",) if not kept.get(k)}}
            save(data)
            count += 1
    print(f"[digest] {count} 号を content/digests に書きました")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--show", default="")
    parser.add_argument("--background", default="", metavar="YYYYMMDD", help="案 C の背景を作って号に書き、KV にも置く")
    parser.add_argument("--publish", default="", metavar="YYYYMMDD", help="号のデータを KV に置く")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if args.backfill:
        return backfill()
    if args.background or args.publish:
        import images  # noqa: PLC0415

        images.load_env_local()
        data = json.loads(path_of(args.background or args.publish).read_text("utf-8"))
        if args.background:
            articles = [a for a in (load_article(i["slug"]) for i in data["items"]) if a]
            url = ai_background(data, articles)
            if not url:
                return 1
            data["aiImage"] = url
            save(data)
            print(f"[digest] 背景: {url}")
        return 0 if publish(data) else 1
    if args.show:
        data = json.loads(path_of(args.show).read_text("utf-8"))
        for item in data["items"]:
            print(f"・{item['headline']}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
