#!/usr/bin/env python3
"""記事の章の見出しを記事ごとの言葉にし、水増しの文を書き直す（LLM の段。2026-11-03 オーナー指摘）。

オーナーの指摘: 「自分に関係ある？」「もう使える？取引できる？」「ライバルと比べてどう？」が全記事で同じで機械的。
「〜に関係があります」「〜にとっても重要です」「〜といえます」のくり返しで情報が無い。

この段がすること（1記事に LLM を1回だけ）:
  1. 章ごとの見出し（sectionTitles）を作る。本文 JSON の章名（## 自分に関係ある？ など）は変えない
     （料金の表・図の差し込み位置や、検索に出す条件が章名を使うため）。表示のとき lib/article-sections.ts が言い換える
  2. 水増し・答えになっていない文・幼い言い方のある章だけ、事実を残したまま書き直す
     - 書き直した章は、元の章の数字・英字の名前がすべて残っているか（「答えになっていない文」にしか無かったものは除く）、
       新しい数字・名前を足していないか、長くなっていないか、表がそのままかをプログラムで確かめ、合わない章は元のまま
  3. 見出しは長さ・決まり文句・数字（章に無い数字を入れない）を確かめてから保存する

  python3 scripts/section_polish.py --slug toyota-land-cruiser-fj-options          # 試し（前と後を表示。書き込まない）
  python3 scripts/section_polish.py --slug toyota-land-cruiser-fj-options --apply  # 書き込む
  python3 scripts/section_polish.py --all --apply --shard 0/4                      # 全記事（4つに分けた0番目）
  python3 scripts/section_polish.py --recent-hours 6 --apply                       # 新しい記事（見出しがまだの記事だけ）
  python3 scripts/section_polish.py --all --apply --max 40 --purpose helper        # まだの記事を古い順に（.github/workflows/section_backfill.yml）

LLM は無料枠の軽いモデル（llm.py の bulk 系。記事を書く段で使う gemini-3.5-flash-lite は後ろに回す）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import section_gate as gate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"

# 見出しの言い換えをしない章（ニュースサイトでも普通に使う章名。表示でも決まった名前にする）
NO_TITLE_RE = re.compile(r"^(概要|まとめ|これまでの流れ|続報|ひとことで言うと|ひとすることで言うと)")
# 見出しに使わない決まり文句（全記事で同じになる質問）
BANNED_TITLE_RE = re.compile(
    r"^(自分に関係ある|自分にはどう関係する|もう使える|もう買える|もう申し込める|ライバルと比べてどう|料金は|日本で使える|"
    r"何がすごいの|使ってみるには|数字で見ると|何が発表された|前と何が違う|中身をくわしく|なぜ今)|"
    r"すごい|しちゃ|ワクワク|徹底解説|まとめ$|について$|^詳細$|^ポイント$|[!！]"
)
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
ASCII_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.#-]*[A-Za-z0-9+#]|[A-Za-z]{2,}")
TABLE_TOKEN = "［表{n}］"

# この段で使うモデル（無料枠。記事を書く段の主力 gemini-3.5-flash-lite は最後）
POLISH_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
    "gemini-3.5-flash-lite",
]

SYSTEM = """あなたは日本のテックニュースサイト（ITmedia・日経クロステック水準）のデスクです。
AIが書いた記事の章見出しを記事ごとの言葉に直し、水増しの文を削って締めます。事実は1つも足さず、1つも落としません。
出力は指定の JSON だけ。"""

PROMPT = """# 記事
題名: {title}
リード: {lede}

# 1. 章の見出し（titles）
次の章それぞれに、表示用の見出しを1つ付けてください。
- その記事でしか使えない、中身が分かる言葉にする（例: 「自分に関係ある？」→「ランクルFJを検討中の人は何が変わる？」、
  「料金は？」→「個人は無料、AI機能だけ有料」、「日本で使える？」→「日本のApp Storeでの配信は未発表」）。
- 全角8〜20字（長くても22字）。です・ます・句点は付けない。疑問形は使ってよいが、どの記事にも使える決まり文句（「自分に関係ある？」「もう使える？」
  「料金は？」「ライバルと比べてどう？」「何がすごいの？」）は使わない。
- 大人のニュースの言葉で。幼い言い方（すごい・〜しちゃう）、あおり（衝撃・必見）、感嘆符は使わない。
- 章の本文に無い数字・名前を見出しに入れない。

{heading_list}

# 2. 書き直す章（rewrites）
次の章だけ、本文を書き直してください（「書き直す章」の一覧にある章だけ。ほかの章は出さない）。
- 消す: 事実の無いつなぎ（「〜にとっても重要です」「〜といえます」「〜に関係があります」「判断材料になります」「チェックしておきたい」
  「注目です」「期待されます」「〜の機会が生まれる可能性があります」）、中の人の注記（「ここは編集部注です」「読者が次に取るべき行動があります」）、
  同じことのくり返し、関係の薄い立場への言及（「この立場への直接の影響は小さいです」）。
- 「元記事には書かれていません」「言及なし」「判断できません」は、読者に必要な未定の点なら「〇〇はまだ発表されていません」と、
  何が未定かを主語にして1文で書く。必要の無いものは消す。「元記事」という言葉は使わない。
  「書かれていない」を「無い」と言い換えない（×「関連は書かれていません」→「関連はありません」。○「関連は示されていません」）。
- です・ます調を保つ（「〜た。」「〜である。」にしない）。箇条書き（- で始まる行）は箇条書きのまま残す。
- 「〜があります。〇〇です。」のように1つの文を2つに割らない。1文は60字まで。です・ます調。幼い言い方・一問一答にしない。
- 硬い言い回し（「〜可能です」「〜において」「〜となっております」）は使わない（「〜できます」「〜で」）。
- 残す: 数字・日付・価格・固有名詞・条件・手順・「（編集部注）」の印は全部そのまま残す。新しい事実・数字・名前・例えは足さない。
- 「［表1］」のような印は、表の置き場所です。そのまま同じ位置に残す。
- `###` の小見出しは、下の文が全部消えるときだけ小見出しごと消してよい。小見出しの文言は変えない。
- 長さは元より短くする（水増ししない）。書くことが何も残らなければ text を空文字にする。

{rewrite_list}

# 出力
{{"titles": [{{"id": "b2", "title": "…"}}], "rewrites": [{{"id": "b5", "text": "…"}}]}}"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "titles": {
            "type": "ARRAY",
            "items": {"type": "OBJECT", "properties": {"id": {"type": "STRING"}, "title": {"type": "STRING"}}, "required": ["id", "title"]},
        },
        "rewrites": {
            "type": "ARRAY",
            "items": {"type": "OBJECT", "properties": {"id": {"type": "STRING"}, "text": {"type": "STRING"}}, "required": ["id", "text"]},
        },
    },
    "required": ["titles", "rewrites"],
}


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def numbers(text: str) -> set[str]:
    t = re.sub(r"\]\([^)]*\)", "]", _nfkc(text))
    t = re.sub(r"(?m)^\s*\d+[.)]\s", "", t)
    return set(NUM_RE.findall(t))


def ascii_words(text: str) -> set[str]:
    t = re.sub(r"\]\([^)]*\)|https?://\S+", " ", _nfkc(text))
    return {w.lower() for w in ASCII_RE.findall(t)}


def needs_rewrite(heading: str, content: str) -> list[str]:
    """書き直しが要る理由（空なら書き直さない）"""
    if heading == gate.DATA_RIVAL_HEADING:
        return []
    sentences = gate._prose_sentences(content)
    reasons = []
    if any(gate.FILLER_RE.search(s) for s in sentences):
        reasons.append("filler")
    if any(gate.is_meta(s) for s in sentences):
        reasons.append("meta")
    if any(gate.is_nonanswer(s) or "元記事" in s for s in sentences):
        reasons.append("nonanswer")
    if gate.CHILDISH_RE.search(content):
        reasons.append("childish")
    if gate.SPLIT_SENTENCE_RE.search(content):
        reasons.append("split")
    if sum(1 for line in content.split("\n") if line.strip() and not line.startswith("#") and gate.QA_LINE_RE.search(line.strip())) >= 1:
        reasons.append("qa")
    return reasons


def mask_tables(content: str) -> tuple[str, list[str]]:
    """表を［表n］の印に置き換える（表は書き直させない）"""
    tables: list[str] = []
    out: list[str] = []
    block: list[str] = []
    for line in content.split("\n") + [""]:
        if line.strip().startswith("|"):
            block.append(line)
            continue
        if block:
            tables.append("\n".join(block))
            out.append(TABLE_TOKEN.format(n=len(tables)))
            block = []
        out.append(line)
    return "\n".join(out[:-1]), tables


def unmask_tables(text: str, tables: list[str]) -> str | None:
    for i, table in enumerate(tables, 1):
        token = TABLE_TOKEN.format(n=i)
        if text.count(token) != 1:
            return None
        text = text.replace(token, table)
    return text


POLITE_END_RE = re.compile(r"(です|ます|でした|ました|ません|でしょう|ください|ましょう|ではありません)$")


def _sentence_end(sentence: str) -> str:
    t = re.sub(r"[（(][^）)]*[）)]$", "", sentence.strip().rstrip("。！？"))
    return re.sub(r"[」』）)]+$", "", t).strip()


def polite_share(text: str) -> float:
    ends = [_sentence_end(s) for s in gate._prose_sentences(text) if s.strip().endswith("。")]
    return sum(1 for e in ends if POLITE_END_RE.search(e)) / len(ends) if ends else 1.0


def plain_sentences(text: str) -> list[str]:
    """です・ます で終わらない文（「〜た。」「〜である。」など）"""
    return [s for s in gate._prose_sentences(text) if s.strip().endswith("。") and not POLITE_END_RE.search(_sentence_end(s))]


def plain_len(text: str) -> int:
    return len(re.sub(r"\s|[#*|`>-]", "", text or ""))


def rewrite_problems(heading: str, old: str, new: str, article_text: str) -> list[str]:
    """書き直しを受け入れてよいか（空なら受け入れる）"""
    problems = []
    # 答えになっていない文・中の人の注記にしか無い数字は消えてよい
    rest = old
    for s in gate._prose_sentences(old):
        if gate.is_nonanswer(s) or gate.is_meta(s):
            rest = rest.replace(s, "")
    keep_nums = numbers(rest)
    lost = keep_nums - numbers(new)
    if lost:
        problems.append(f"数字が消えた（{', '.join(sorted(lost)[:5])}）")
    added = numbers(new) - numbers(old)
    if added:
        problems.append(f"元に無い数字が増えた（{', '.join(sorted(added)[:5])}）")
    lost_words = {w for w in ascii_words(old) - ascii_words(new) if w not in ascii_words(article_text.replace(old, ""))}
    if lost_words:
        problems.append(f"名前が消えた（{', '.join(sorted(lost_words)[:5])}）")
    new_words = ascii_words(new) - ascii_words(article_text)
    if new_words:
        problems.append(f"元に無い名前が増えた（{', '.join(sorted(new_words)[:5])}）")
    if plain_len(new) > plain_len(old) * 1.05 + 10:
        problems.append("長くなった")
    if "元記事" in new:
        problems.append("「元記事」が残った")
    if gate.CHILDISH_RE.search(new):
        problems.append("幼い言い方が残った")
    if any(gate.is_meta(s) for s in gate._prose_sentences(new)):
        problems.append("中の人の注記が残った")
    # です・ます調を崩さない（書き直した章だけ「〜た。」「〜である。」になっていた。2026-11-03 の抜き取り確認）
    if polite_share(old) >= 0.6 and len(plain_sentences(new)) > len(plain_sentences(old)):
        problems.append(f"です・ます調が崩れた（「{plain_sentences(new)[0][-20:]}」）")
    # 箇条書きを地の文や「・」に崩さない
    old_items = sum(1 for line in old.split("\n") if gate.LIST_RE.match(line))
    new_items = sum(1 for line in new.split("\n") if gate.LIST_RE.match(line))
    if old_items >= 2 and new_items < old_items - 1:
        problems.append(f"箇条書きが減った（{old_items}→{new_items}行）")
    # 説明を削りすぎない（消してよいのは、つなぎ・答えになっていない文・中の人の注記だけ）
    keep = old
    for s in gate._prose_sentences(old):
        if gate.FILLER_RE.search(s) or gate.is_nonanswer(s) or gate.is_meta(s):
            keep = keep.replace(s, "")
    if new.strip() and plain_len(new) < plain_len(keep) * 0.45:
        problems.append(f"説明を削りすぎた（{plain_len(keep)}→{plain_len(new)}字）")
    old_h3 = re.findall(r"(?m)^### .+$", old)
    new_h3 = re.findall(r"(?m)^### .+$", new)
    if any(h not in old_h3 for h in new_h3):
        problems.append("小見出しが変わった")
    if not new.strip() and gate.KEEP_FOR_INDEX_RE.search(heading):
        problems.append("検索に出す条件の章を空にした")
    return problems


def valid_title(title: str, heading: str, section_text: str, article_text: str) -> bool:
    t = (title or "").strip()
    if not (6 <= len(t) <= 24) or re.search(r"[。<>#|\n]", t) or BANNED_TITLE_RE.search(t):
        return False
    if t == heading:
        return False
    if numbers(t) - numbers(section_text) - numbers(article_text):
        return False
    if ascii_words(t) - ascii_words(article_text):
        return False
    return True


def _sections(markdown: str) -> list[tuple[str | None, str]]:
    return gate.split_sections(markdown)


def _join(sections: list[tuple[str | None, str]], trailing_nl: bool) -> str:
    parts = []
    for head, content in sections:
        if head is None:
            parts.append(content.strip())
        else:
            c = content.strip()
            parts.append(f"## {head}\n\n{c}" if c else f"## {head}")
    text = "\n\n".join(p for p in parts if p)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + ("\n" if trailing_nl else "")


def wants_title(record: dict, head: str) -> bool:
    """表示用の見出しを作る章か。ニュースの種類の型で書いた記事（newsType あり。scripts/news_types.py）は見出しが元から
    記事ごとの言葉なので、決まり文句（BANNED_TITLE_RE）に当たる見出しだけ言い換える"""
    if NO_TITLE_RE.match(head):
        return False
    return not record.get("newsType") or bool(BANNED_TITLE_RE.search(head))


def build_request(record: dict) -> tuple[str, dict]:
    """(プロンプト, {id: (field, 見出し, 本文, 表, 書き直すか)})"""
    items: dict[str, tuple] = {}
    heading_lines, rewrite_lines = [], []
    for field, prefix in (("body", "b"), ("easy", "e")):
        for i, (head, content) in enumerate(_sections(record.get(field) or "")):
            if head is None:
                continue
            sid = f"{prefix}{i}"
            reasons = needs_rewrite(head, content)
            masked, tables = mask_tables(content.strip())
            items[sid] = (field, head, content, tables, bool(reasons))
            label = "かんたん版" if field == "easy" else "本文"
            if wants_title(record, head):
                gist = re.sub(r"\s+", " ", re.sub(r"(?m)^\|.*$", "", gate._clean_block(content)))[:140]
                heading_lines.append(f"- id={sid}（{label}）今の章名「{head}」 中身: {gist}")
            if reasons:
                rewrite_lines.append(f"## id={sid}（{label}・章名「{head}」・直す理由: {', '.join(reasons)}）\n<<<\n{masked}\n>>>")
    prompt = PROMPT.format(
        title=record.get("title") or "",
        lede=(record.get("lede") or "")[:300],
        heading_list="\n".join(heading_lines) or "（なし）",
        rewrite_list="\n\n".join(rewrite_lines) or "（なし。rewrites は空配列にする）",
    )
    return prompt, items


def polish(record: dict, log=print, purpose: str | None = None) -> tuple[dict, dict]:
    """(新しい記事, 報告)。LLM に1回だけ頼み、確かめて合うものだけ反映する"""
    import llm  # noqa: PLC0415

    if "sections" not in llm._tiers:  # noqa: SLF001
        # 軽い Gemini を先に、そのあと他社の無料モデル。記事を書く段の Flash（1日20回）は使わない
        others = [m for m in llm.models_for("bulk") if m not in llm.QUALITY_MODELS and m not in POLISH_MODELS]
        llm._tiers["sections"] = POLISH_MODELS + others  # noqa: SLF001
    prompt, items = build_request(record)
    todo = [sid for sid, (field, head, _, _, rewrite) in items.items() if rewrite or wants_title(record, head)]
    if not items or not todo:
        return record, {"titles": 0, "rewrites": 0, "rejected": [], "model": None}
    # purpose="helper" なら Claude API の Haiku（月の予算の配分の中で。llm.api_plan）→ だめなら無料モデル
    data, model = llm.generate_json(SYSTEM, prompt, SCHEMA, temperature=0.3, max_output_tokens=12000, tier="sections", purpose=purpose)
    article_text = f"{record.get('title', '')}\n{record.get('lede', '')}\n{record.get('body', '')}\n{record.get('easy', '')}"
    report = {"titles": 0, "rewrites": 0, "rejected": [], "model": model}
    new = dict(record)
    titles: dict[str, dict[str, str]] = {"body": {}, "easy": {}}
    used: set[str] = set()
    for row in data.get("titles") or []:
        sid, title = str(row.get("id") or ""), str(row.get("title") or "").strip()
        if sid not in items:
            continue
        field, head, content, _, _ = items[sid]
        if NO_TITLE_RE.match(head):
            continue
        if title in used or not valid_title(title, head, content, article_text):
            report["rejected"].append(f"見出し {sid}「{title}」")
            continue
        used.add(title)
        titles[field][head] = title
        report["titles"] += 1
    rewrites: dict[str, str] = {}
    for row in data.get("rewrites") or []:
        sid, text = str(row.get("id") or ""), str(row.get("text") or "")
        if sid not in items or not items[sid][4]:
            continue
        field, head, content, tables, _ = items[sid]
        text = re.sub(r"(?m)^## .*$", "", text).strip()
        restored = unmask_tables(text, tables)
        if restored is None:
            report["rejected"].append(f"書き直し {sid}: 表の印が合わない")
            continue
        problems = rewrite_problems(head, content.strip(), restored, article_text)
        if problems:
            report["rejected"].append(f"書き直し {sid}（{head}）: {' / '.join(problems)}")
            continue
        rewrites[sid] = restored
        report["rewrites"] += 1
    for field, prefix in (("body", "b"), ("easy", "e")):
        text = record.get(field) or ""
        if not text.strip():
            continue
        sections = _sections(text)
        changed = False
        for i, (head, content) in enumerate(sections):
            sid = f"{prefix}{i}"
            if sid in rewrites:
                sections[i] = (head, rewrites[sid])
                changed = True
        if changed:
            new[field] = _join(sections, text.endswith("\n"))
    clean_titles = {k: v for k, v in titles.items() if v}
    if clean_titles:
        new["sectionTitles"] = clean_titles
    new["sectionsPolishedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return new, report


def _show_diff(old: dict, new: dict) -> None:
    for field in ("body", "easy"):
        if old.get(field) != new.get(field):
            o = dict((h, c.strip()) for h, c in _sections(old.get(field) or ""))
            for h, c in _sections(new.get(field) or ""):
                if h and o.get(h) != c.strip():
                    print(f"----- {field}「{h}」\n[前] {o.get(h)}\n[後] {c.strip()}")
    print("見出し:", json.dumps(new.get("sectionTitles"), ensure_ascii=False, indent=1))


def targets(args) -> list[Path]:
    paths = sorted(ARTICLES_DIR.glob("*.json"))
    if args.slug:
        return [ARTICLES_DIR / f"{s}.json" for s in args.slug.split(",")]
    if args.recent_hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=args.recent_hours)
        out = []
        for p in paths:
            r = json.loads(p.read_text("utf-8"))
            stamp = r.get("generatedAt") or r.get("publishedAt") or ""
            try:
                when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if when >= cutoff and not r.get("sectionsPolishedAt"):
                out.append(p)
        return out
    if args.shard:
        k, n = (int(x) for x in args.shard.split("/"))
        paths = [p for i, p in enumerate(paths) if i % n == k]

    # 新しい記事から（読まれやすく、検索・審査の目にも先に触れるため）
    def stamp(p: Path) -> str:
        try:
            r = json.loads(p.read_text("utf-8"))
        except ValueError:
            return ""
        return str(r.get("generatedAt") or r.get("publishedAt") or "")

    return sorted(paths, key=stamp, reverse=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--recent-hours", type=float)
    ap.add_argument("--shard")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--redo", action="store_true", help="済みの記事もやり直す")
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--log")
    ap.add_argument("--purpose", help="Claude API の段（helper = Haiku）。省くと無料モデルだけ")
    args = ap.parse_args(argv)
    if not (args.slug or args.all or args.recent_hours):
        ap.print_help()
        return 1
    import llm  # noqa: PLC0415

    llm._load_env_local()  # noqa: SLF001 - 手元の実行用（CI は環境変数）
    done = 0
    logf = open(args.log, "a", encoding="utf-8") if args.log else None
    for path in targets(args):
        record = json.loads(path.read_text("utf-8"))
        if record.get("sectionsPolishedAt") and not args.redo and not args.slug:
            continue
        try:
            new, report = polish(record, purpose=args.purpose)
        except Exception as error:  # noqa: BLE001 - 1本の失敗で止めない
            print(f"[NG] {path.stem}: {str(error)[:160]}", flush=True)
            if logf:
                logf.write(json.dumps({"slug": path.stem, "error": str(error)[:300]}, ensure_ascii=False) + "\n")
            time.sleep(5)
            continue
        print(f"[ok] {path.stem}: 見出し{report['titles']} 書き直し{report['rewrites']} 不採用{len(report['rejected'])}（{report['model']}）", flush=True)
        for r in report["rejected"][:4]:
            print(f"     不採用: {r}", flush=True)
        if logf:
            logf.write(json.dumps({"slug": path.stem, **report, "before": {k: record.get(k) for k in ("body", "easy")},
                                   "after": {k: new.get(k) for k in ("body", "easy")}, "sectionTitles": new.get("sectionTitles")},
                                  ensure_ascii=False) + "\n")
            logf.flush()
        if args.slug and not args.apply:
            _show_diff(record, new)
        if args.apply:
            # 書き込む直前に読み直す（ほかの処理が同じ記事を直していたら、その後の版に重ねる）
            latest = json.loads(path.read_text("utf-8"))
            if latest.get("body") != record.get("body") or latest.get("easy") != record.get("easy"):
                print(f"     {path.stem}: 処理中に本文が変わったので見送り", flush=True)
                continue
            latest.update({k: new[k] for k in ("body", "easy", "sectionTitles", "sectionsPolishedAt") if k in new})
            path.write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        done += 1
        if args.max and done >= args.max:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
