#!/usr/bin/env python3
"""note / はてなブログ向けの「貼るだけ」下書きを作る（その日の主要記事 1〜3本）。

方針（スパムにしない）:
- 記事の丸写しではなく、800〜1200字の「自分にどう関係する？」の視点の要約を新しく書く。
- 事実は記事 JSON にあるものだけ（数字は元記事に無いものを入れたら作り直し）。
- 末尾に元記事へのリンクと「この記事の元記事」の明記（正規の記事はサイト側）。
- 自動で公開しない。note は手動で貼る。はてなブログは --hatena で「下書き」として送るだけ
  （HATENA_ID / HATENA_BLOG_ID / HATENA_API_KEY が無ければ何もしない。HATENA_PUBLISH=1 で公開）。

  python3 scripts/crosspost_drafts.py                 # out/drafts/YYYY-MM-DD/ に書き出す
  python3 scripts/crosspost_drafts.py --n 3 --hatena  # はてなブログへ下書き投稿も
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm  # noqa: E402
import persona  # noqa: E402
from digest import JST, ROOT, article_url, pick_top, site_url, title_of  # noqa: E402
from quality import readability_problems  # noqa: E402

OUT_DIR = ROOT / "out" / "drafts"
MIN_CHARS, MAX_CHARS = 800, 1200

SYSTEM = """あなたは「サイソクニュース」の編集者です。自社サイトの記事をもとに、note・はてなブログに載せる短い解説を書きます。
目的は、読者が「このニュースは自分にどう関係するのか」を3分でつかめること。記事の言い換えや宣伝文ではなく、読者目線の整理に価値を置きます。
読者は、""" + persona.PERSONA["who"] + """
話し方: """ + persona.PERSONA["voice"] + """

# 守ること
- 事実（社名・製品名・数字・日付・価格・提供範囲）は、与えた「記事データ」に書かれているものだけを使う。推測で数字を作らない。
  記事データに無いことは書かない。分からないことは「記事では触れられていない」と書く。
- 本文は1000字前後（800〜1200字。空白・記号を除く）。短すぎるのは不合格。下の各章の字数の目安を守る。
- 読む人は、忙しくてエンジニアではない会社員。硬すぎ・難しすぎは不合格。分量は減らさず、伝え方をやさしくする。
  - 詳しい先輩が話しかけるように。です・ます調だが、少し会話っぽく（「ざっくり言うと」「つまり」「たとえば」）。
  - 1文は短く（平均40字以内）。1文に言いたいことは1つだけ。1段落は1〜3文で、段落の間は必ず空行を入れる。
  - 専門用語は、初めて出たところで普通の言葉で説明するか、普通の言葉に置き換える。
    お手本:「""" + persona.PLAIN_STYLE_EXAMPLE + """」
  - 仕事や暮らしの身近な例え・場面を1つ以上入れる（例えで新しい数字や効果を足さない）。
  - 使わない言い回し: 「〜することが可能です」（→〜できます）、「〜を見据える」、「〜において」（→〜で）、「〜となっております」、
    「〜と言えるでしょう」、「記事では〜への言及はありませんが」（→〜は書かれていません）。煽らない（「衝撃」「ヤバい」「革命」は使わない）。
- 構成（Markdown。見出しは ### を3つ）:
  1. 冒頭（約150字・2段落）: 1段落目は「ざっくり言うと、」で始まる1〜2文の要約。2段落目で、なぜ注目かを短く。
  2. ### 自分にどう関係する？（約400字）— 次の3つの立場から、記事データで本当に関係が言えるものだけを選ぶ（2つでもよい。無理に3つにしない）:
     「**会社員として仕事で**」／「**副業・独立を考えている人**」／「**お金・資産づくりの面で**」。
     立場ごとに「**立場名**」の行と、何が変わるか・明日から何をすればよいかを2〜3文の短い段落で書く（記事データの事実だけ。
     記事データの「仕事・お金にどう活かせる」があれば参考にする）。関係が薄い立場は書かない。
     お金・資産づくりの話は一般的な情報と判断材料の提示に留め、特定の銘柄・ファンド・物件の売買をすすめない。「儲かる」「稼げる」と書かない。
  3. ### 今できること・まだできないこと（約250字）— 記事の「使える判定」をもとに、日本から使えるか・無料か・いつからか・誰向けか。
  4. ### 気をつけたい点（約200字）— 料金・提供範囲・記事で未確認の点など。
- 本文にURLやリンク、元記事への誘導文は書かない（こちらで末尾に付ける）。
- タイトルは28〜40字。釣りタイトルにしない。記事の見出しをそのまま使わず、読者の疑問の形にしてもよい。
- tags: 3〜5個。製品名・会社名・分野。"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "body_markdown": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["title", "body_markdown", "tags"],
}


def log(message: str) -> None:
    print(message, flush=True)


def source_text(article: dict) -> str:
    """モデルに渡す記事データ（本文は長すぎるので先頭を中心に）"""
    verdict = article.get("verdict") or {}
    parts = [
        f"見出し: {title_of(article)}",
        f"リード: {article.get('lede', '')}",
        "要点:\n" + "\n".join(f"- {h}" for h in article.get("highlights") or []),
        "使える判定: " + json.dumps({k: verdict.get(k) for k in ("status", "japan", "price", "free", "audience", "take")}, ensure_ascii=False),
        f"{persona.WORK_MONEY_HEADING}: " + " / ".join((article.get("workMoney") or {}).get("points") or ["（記事に記載なし）"]),
        f"発信元: {article.get('sourceName') or article.get('channel', '')}",
        f"公開日: {str(article.get('publishedAt', ''))[:10]}",
        "本文:\n" + str(article.get("body", ""))[:7000],
    ]
    return "\n\n".join(parts)


def plain_len(text: str) -> int:
    return len(re.sub(r"[\s#*>\-|`]", "", text))


def unknown_numbers(draft: str, source: str) -> list[str]:
    """下書きにあって記事データに無い数字（2桁以上・小数・%）"""
    src = re.sub(r"[,，]", "", source)
    found = []
    for num in set(re.findall(r"\d+(?:\.\d+)?", re.sub(r"[,，]", "", draft))):
        if (len(num) >= 2 or "." in num) and num not in src:
            found.append(num)
    return sorted(found)


# 伝わりやすさの機械チェック（2026-11-02 オーナー「ちょっと硬いのと、難しすぎる。ボリュームは減らさず、伝え方を変えて」）
MAX_AVG_SENTENCE = 40


def sentences(body: str) -> list[str]:
    """地の文の文（見出し・箇条書きの記号を除いて「。」「？」「！」で区切る）"""
    text = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    text = re.sub(r"[*_`>]|^\s*[-・]\s*", "", text, flags=re.M)
    return [s.strip() for s in re.split(r"(?<=[。？！?!])|\n", text) if len(s.strip()) >= 2]


def style_problems(body: str) -> list[str]:
    """硬さ・難しさの指摘（平均の文の長さ・硬い言い回し・冒頭の「ざっくり言うと」）。直しの指示に使う"""
    problems = []
    found = sentences(body)
    if found:
        avg = sum(len(s) for s in found) / len(found)
        if avg > MAX_AVG_SENTENCE:
            problems.append(f"文が長い（1文の平均 {avg:.0f}字 / {MAX_AVG_SENTENCE}字以内）。1文に1つのことだけにして分ける（中身は減らさない）")
    stiff = sorted(set(persona.STIFF_RE.findall(body)))
    if stiff:
        problems.append(f"硬い言い回しがある（{'、'.join(stiff)}）。話し言葉に近い言い方に直す（例: することが可能です→できます、において→で）")
    if not re.sub(r"^[\s*_#>]+", "", body).startswith("ざっくり言うと"):
        problems.append("冒頭を「ざっくり言うと、」で始まる1〜2文の要約にする")
    return problems


HEADINGS = ("自分にどう関係する？", "今できること・まだできないこと", "気をつけたい点")


def fix_headings(body: str) -> str:
    """決めた見出しの書き崩れ（誤字・記号違い）を元に戻す"""
    def repl(m: re.Match) -> str:
        text = m.group(1)
        for h in HEADINGS:
            same = sum(1 for c in set(h) if c in text)
            if same >= len(set(h)) * 0.7:
                return f"### {h}"
        return m.group(0)
    return re.sub(r"^###\s*(.+?)\s*$", repl, body, flags=re.M)


def write_draft(article: dict) -> dict | None:
    """最大3回。2回目からは前回の下書きを渡して直させる（足りない字数は「足す」ほうが守られやすい）。
    形が合格したものが無ければ、記事に無い数字を含まない中で最も長いもの（700字以上）を注意つきで返す"""
    source = source_text(article)
    feedback = ""
    best: dict | None = None
    for attempt in range(3):
        try:
            data, model = llm.generate_json(
                SYSTEM, f"# 記事データ\n{source}{feedback}", SCHEMA, temperature=0.5, max_output_tokens=4096, tier="bulk"
            )
        except Exception as error:  # noqa: BLE001 - 全モデル失敗
            log(f"   [NG] 生成できず: {str(error)[:150]}")
            break
        body = fix_headings(str(data.get("body_markdown", "")).strip())
        n = plain_len(body)
        hard = []
        if n < MIN_CHARS:
            hard.append(f"本文が {n}字で短い。あと{MIN_CHARS + 150 - n}字ほど足して1000字前後にする（「自分にどう関係する？」の立場ごとの説明を厚くする）")
        elif n > MAX_CHARS + 150:
            hard.append(f"本文が {n}字で長い。1100字以内に削る")
        bad = unknown_numbers(body, source)
        if bad:
            hard.append(f"記事データに無い数字がある（{', '.join(bad[:6])}）。記事データにある数字だけを使う")
        if re.search(r"https?://", body):
            hard.append("本文にURLを書かない")
        if m := persona.ADVICE_RE.search(body):
            hard.append(f"儲け話・投資助言の表現がある（「{m.group(0)}」）。一般的な情報と判断材料の提示に直す")
        hard += style_problems(body)
        soft = readability_problems(body)  # 読みやすさは直してほしいが、それだけで落とさない
        data.update(body_markdown=body, model=model, chars=n, warnings=hard + soft)
        if not hard:
            return data
        if not bad and n >= 700 and (best is None or n > best["chars"]):
            best = data
        log(f"   作り直し（{attempt + 1}回目）: {' / '.join(hard + soft)[:200]}")
        feedback = (
            "\n\n# 前回の下書き（これを直して、全文を書き直す）\n" + body
            + "\n\n# 直すこと\n" + "\n".join(f"- {p}" for p in hard + soft)
        )
    return best


def footer(article: dict, channel: str) -> str:
    url = article_url(article["slug"], channel, "crosspost")
    return (
        "\n\n---\n\n"
        f"この記事は、サイソクニュースの記事「{title_of(article)}」をもとに、要点を短く整理したものです。"
        "料金の詳細や発表の全体は元記事にまとめています。\n\n"
        f"元記事: {url}\n"
    )


def to_note(draft: dict, article: dict) -> str:
    # note のエディタは Markdown の見出し（###）と箇条書きを貼り付けで解釈する
    return f"{draft['title']}\n\n{draft['body_markdown']}{footer(article, 'note')}\nハッシュタグ: {' '.join('#' + t.replace(' ', '') for t in draft['tags'])}\n"


def to_hatena(draft: dict, article: dict) -> str:
    return f"{draft['body_markdown']}{footer(article, 'hatena')}"


def post_hatena(title: str, body_md: str, tags: list[str]) -> str | None:
    """はてなブログ AtomPub。既定は下書き（app:draft=yes）。成功したら編集URL"""
    hid, blog, key = (os.environ.get(k) for k in ("HATENA_ID", "HATENA_BLOG_ID", "HATENA_API_KEY"))
    if not (hid and blog and key):
        return None
    draft = "no" if os.environ.get("HATENA_PUBLISH") == "1" else "yes"
    cats = "".join(f'<category term="{html.escape(t)}" />' for t in tags[:5])
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<entry xmlns="http://www.w3.org/2005/Atom" xmlns:app="http://www.w3.org/2007/app">'
        f"<title>{html.escape(title)}</title>"
        f'<content type="text/x-markdown">{html.escape(body_md)}</content>'
        f"{cats}<app:control><app:draft>{draft}</app:draft></app:control></entry>"
    )
    auth = base64.b64encode(f"{hid}:{key}".encode()).decode()
    req = urllib.request.Request(
        f"https://blog.hatena.ne.jp/{hid}/{blog}/atom/entry",
        data=xml.encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/xml; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            text = res.read().decode(errors="replace")
    except urllib.error.HTTPError as error:
        log(f"   [NG] はてなブログ {error.code}: {error.read()[:200]!r}")
        return None
    m = re.search(r'<link rel="alternate" type="text/html" href="([^"]+)"', text)
    return m.group(1) if m else "posted"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=3, help="作る本数（1〜3）")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--hatena", action="store_true", help="はてなブログへ下書き投稿する（鍵があるときだけ）")
    args = parser.parse_args()

    jst = datetime.now(timezone.utc).astimezone(JST)
    out = OUT_DIR / f"{jst:%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    # 読者像の話題（AI・ビジネス/お金/投資・不動産・起業/副業・税や補助金。スポーツなし）から選ぶ
    articles = pick_top(args.hours, max(1, min(args.n, 3)), persona_topics=True)
    if not articles:
        log("[skip] 対象記事がありません")
        return 0

    index = [f"# 転載用の下書き（{jst:%Y-%m-%d}）", "", "note は手動で貼り付け、はてなブログは下書きとして確認してから公開してください。", ""]
    for article in articles:
        log(f"[draft] {title_of(article)}")
        draft = write_draft(article)
        if not draft:
            continue
        note_path = out / f"{article['slug']}.note.md"
        hatena_path = out / f"{article['slug']}.hatena.md"
        note_path.write_text(to_note(draft, article), encoding="utf-8")
        hatena_path.write_text(f"タイトル: {draft['title']}\nカテゴリ: {', '.join(draft['tags'])}\n\n{to_hatena(draft, article)}", encoding="utf-8")
        status = f"{draft['chars']}字・{draft['model']}" + (f"・注意: {'; '.join(draft['warnings'])[:120]}" if draft["warnings"] else "")
        index += [f"- {draft['title']}（{status}）", f"  - note: {note_path.name}", f"  - はてなブログ: {hatena_path.name}"]
        if args.hatena:
            posted = post_hatena(draft["title"], to_hatena(draft, article), draft["tags"])
            index.append(f"  - はてなブログ投稿: {posted or '未送信（鍵が未設定）'}")
        log(f"   → {note_path.relative_to(ROOT)}（{status}）")
    (out / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    log(f"[done] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
