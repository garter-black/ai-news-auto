#!/usr/bin/env python3
"""生成した記事を書き出す前の品質ゲート。

  (a) 形のチェック（API 不要）: 空欄・かんたん版・タイトル長・英語の残骸・元記事の丸写し
  (b) 事実確認（LLM・bulk）: 記事と元記事を突き合わせ {grounded, score, problems} を返させる
      grounded が false か score < 70 なら不合格

  (c) 読みやすさ（測るのは API 不要）: 文・段落の長さ。形と事実が合格で読みやすさだけ基準外なら「やわらかい不合格」。
      polish() で本文だけ文を分ける書き直しを1回頼み（数値を添えて）、見出し・数字・長さが保たれ再確認も通れば差し替える。
      だめなら元の原稿のまま受理して数値をログに残す（読みやすさだけで記事を落とさない）

web_articles.py は不合格のとき problems を添えて1回だけ作り直し、それでもだめなら諦める。
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calc_facts  # noqa: E402
import glossary  # noqa: E402
import headline_normalize  # noqa: E402
import japan_availability  # noqa: E402
import llm  # noqa: E402
import persona  # noqa: E402
import section_gate  # noqa: E402
import article_style  # noqa: E402  文体の決まりと公開前の門（1か所）
import ui_claims  # noqa: E402
import genre_focus  # noqa: E402  投資助言・売買のすすめ・取引のやり方の門（2026-11-03・台帳 A22）

TITLE_RANGE = (18, 64)
PASS_SCORE = 70
# 事実確認・文の書き直しに使うモデルの段（速報レーンは待たせないよう quality にする。web_articles.py が切り替える）
CHECK_TIER = "bulk"
# 大きなニュース（速報レーン・主要企業の新モデルなど）を確認しているか（web_articles.py が切り替える）。
# True のとき: 細かい指摘は外科的に直して公開する（落とさない）
MAJOR = False
# 外科的な修正に回す・修正後に受理する事実確認の点数（致命的な誤り＝fatal が無いときだけ）
SURGICAL_MIN_SCORE = 75
# 事実確認に渡す元記事の長さ。生成に渡す長さ（feeds.TEXT_CAP = 12000）＋元記事の情報の見出しより短いと、
# 元記事の後半にある事実（2026-10-28 の Mithril の買収は 9400 字目）を「創作」と誤判定するので、それより長くする
SOURCE_CAP = 13000
ARTICLE_CAP = 9000
# 事実確認に渡す「確認済みの文脈」（過去記事・日本での提供・確かな事実の束・計算済みの数字）の長さ
CONTEXT_CAP = 7000

BOILERPLATE_RE = re.compile(
    r"\b(Read more|Learn more|Subscribe|Sign up|Sign in|Cookie|Privacy Policy|Terms of (Use|Service)|"
    r"All rights reserved|Share this|Click here|Skip to (main )?content|Table of contents|Back to top|"
    r"Related (posts|articles)|Loading\.\.\.)\b",
    re.IGNORECASE,
)
# 英単語が12語以上続く＝訳し忘れ・コピペの残骸（製品名程度なら12語には届かない）
ENGLISH_RUN_RE = re.compile(r"(?:\b[A-Za-z][A-Za-z'’\-]*\b[\s,;:]+){11,}\b[A-Za-z][A-Za-z'’\-]*\b")
# 文章ソースなのに「動画」前提の言い回しが残っている
VIDEO_LEFTOVER_RE = re.compile(r"動画(内|では|の中で|で示|で紹介|で語ら|の冒頭)|映像では|スライドでは|音声では")

# サイソクが自分で試したように読める書き方（自社の検証データ ownTest が無い記事では不可）。
# 「筆者」は元記事（他社）の書き手のことが多く、サイソクの体験に読めるので使わない（報道が元なら「〇〇の記者」と書く）
FIRST_PERSON_TEST_RE = re.compile(
    r"筆者|"
    r"(?:私たち|私|我々|われわれ|編集部|サイソク(?:編集部)?)(?:が|で|の|も|は)[^。\n]{0,12}?"
    r"(?:試し|テスト|検証し|使ってみ|触ってみ|触った|実機で|計測し|測定し)|"
    r"実際に(?:試して|使って|触って)みた(?:ところ|結果|感想)|手元の(?:環境|PC|パソコン|端末|スマホ)で(?:試|確認|計測)"
)
# FAQ の答えが「分からない」だけのもの（質問として出さない）
FAQ_UNANSWERED_RE = re.compile(r"^(?:元記事(?:では|には)|現時点では|発表では)?[^。]{0,20}(?:書かれていません|言及(?:は)?ありません|記載(?:は)?ありません|分かりません|不明です)")
# 題名の約束（題名にこの言葉があれば、本文に答えが要る）
PROMISE_JAPAN_RE = re.compile(r"日本で(?:も)?使え|日本での(?:使い方|提供|利用)|日本から使|日本(?:で|でも)(?:提供|発売|利用)")
PROMISE_PRICE_RE = re.compile(r"料金|価格|値段|いくら|月額|年額|費用")
PRICE_ANSWER_RE = re.compile(r"\d[\d,.]*\s*(?:円|ドル|万円|億円|ユーロ)|[$＄￥¥]\s?\d|無料|月\d|税込|税別")
PROMISE_HOWTO_RE = re.compile(r"使い方|やり方|手順|設定方法|始め方|申し込み方|申請方法")
HOWTO_ANSWER_RE = re.compile(r"(?m)^\s*(?:\d+[.)．]|[-*・])\s+\S|^#{2,3}\s.*(?:使い方|手順|方法|始め方|やり方|申し込|申請)")
PROMISE_WHEN_RE = re.compile(r"いつから|開始日|発売日|いつ始まる")
WHEN_ANSWER_RE = re.compile(r"\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月|\d{1,2}月(?:上旬|中旬|下旬|中|から)|未定|発表されていません")


def promise_problems(record: dict) -> list[str]:
    """題名・短い題名で約束したこと（日本で使える？／料金／使い方／いつから）に、本文が答えているか（API 不要）"""
    title = f"{record.get('title') or ''} {record.get('seoTitle') or ''}"
    body = record.get("body") or ""
    problems = []
    if PROMISE_JAPAN_RE.search(title):
        heading = japan_availability.JAPAN_HEADING_RE.search(body)
        first = ""
        if heading:
            first = body[heading.end():].strip().split("\n")[0].split("。")[0]
        if heading and japan_availability.UNKNOWN_ANSWER_RE.search(first):
            problems.append(
                "題名で「日本で使えるか」を約束しているのに、本文の「日本で使える？」が「分からない」で始まっている。"
                "「当サイトで確認済み」の欄に答えがあればそれで答え、無ければ題名から日本の話を外す"
            )
        elif "日本" not in body:
            problems.append("題名で「日本で使えるか」を約束しているのに、本文に日本での提供・使い方の説明が無い。答えるか、題名から外す")
    if PROMISE_PRICE_RE.search(title) and not PRICE_ANSWER_RE.search(body):
        problems.append("題名で料金・価格を約束しているのに、本文に金額（円・ドル・無料など）が無い。元記事の金額を書くか、題名から料金の話を外す")
    if PROMISE_HOWTO_RE.search(title) and not HOWTO_ANSWER_RE.search(body):
        problems.append("題名で使い方・手順を約束しているのに、本文に手順（番号つきの手順や「使い方」の章）が無い。手順を書くか、題名から外す")
    if PROMISE_WHEN_RE.search(title) and not WHEN_ANSWER_RE.search(body):
        problems.append("題名で「いつから」を約束しているのに、本文に日付が無い。元記事の日付を書くか、題名から外す")
    return problems


def every_path_problems(record: dict) -> list[str]:
    """（2026-11-04 一貫性の点検）記事を書き出すすべての段（通常・動画・まとめ・続報・見出しの書き直し）で同じ合格の基準。
    文体の門（A1〜A6・A17〜A21・A24・A33・A36・A38）＋投資助言・取引のやり方（A22。どの記事でも）＋自分で試した書き方（A14）。
    1つでもあれば書き出さない（作り直すか見送る）。tests/test_rule_consistency.py が、記事を書くすべての段がこれを通るかを確かめる"""
    return article_style.problems(record) + genre_focus.advice_problems(record) + first_person_problems(record)


def added_problems(before: dict, after: dict) -> list[str]:
    """公開中の記事を書き換える段（続報・見出しの書き直し）向け: 書き換えで新しく増えた不合格だけ（古い型の記事にもとからある分は数えない。O9）。
    文体の門は決まりの番号（A1 など）で比べる（引用の文が変わっただけで「新しい不合格」としない）。番号が同じでも件数が増えたら新しい不合格"""
    def keyed(record: dict) -> list[tuple[str, str]]:
        return ([(code, msg) for code, msg in article_style.coded_problems(record)]
                + [(msg, msg) for msg in genre_focus.advice_problems(record) + first_person_problems(record)])

    seen: dict[str, int] = {}
    for key, _ in keyed(before):
        seen[key] = seen.get(key, 0) + 1
    out = []
    for key, msg in keyed(after):
        if seen.get(key, 0) > 0:
            seen[key] -= 1
        else:
            out.append(msg)
    return out


def first_person_problems(record: dict) -> list[str]:
    """サイソクが自分で試したように読める書き方。自社の検証データ（ownTest）がある記事だけ許す"""
    if record.get("ownTest"):
        return []
    text = "\n".join(str(record.get(k) or "") for k in ("title", "lede", "body", "easy")) + "\n" + "\n".join(record.get("highlights") or [])
    m = FIRST_PERSON_TEST_RE.search(text)
    if not m:
        return []
    return [
        f"サイソクが自分で試したように読める書き方がある（「{m.group(0)[:20]}」）。サイソクは試していない。"
        "元記事の書き手が試した結果なら「〇〇（媒体名）の記者が試したところ」と誰が試したかを書く"
    ]


def faq_problems(record: dict) -> list[str]:
    """よくある質問・今日やることの形と禁止表現（API 不要）"""
    problems = []
    faq = record.get("faq") or []
    if faq and not 3 <= len(faq) <= 5:
        problems.append(f"faq は3〜5問（{len(faq)}問）")
    for row in faq:
        if not str(row.get("q", "")).rstrip().endswith(("？", "?")):
            problems.append(f"faq の質問が疑問文になっていない（「{str(row.get('q', ''))[:20]}」）")
        if m := persona.ADVICE_RE.search(str(row.get("a", ""))):
            problems.append(f"faq の答えに儲け話・投資助言の表現がある（「{m.group(0)}」）")
    actions = record.get("todayActions") or []
    if actions and not 2 <= len(actions) <= 3:
        problems.append(f"今日やること（today_actions）は2〜3個（{len(actions)}個）")
    for a in actions:
        if m := persona.ADVICE_RE.search(a):
            problems.append(f"今日やることに儲け話・投資助言の表現がある（「{m.group(0)}」）")
    return problems


# 製品の比較表の章（scripts/rivals.py）。数字は各社の公式ページの根拠の文と照合する（rival_problems）。元記事との照合からは外す
RIVALS_HEADING = "同じクラスの製品と比べると？"
RIVALS_SECTION_RE = re.compile(rf"^## {re.escape(RIVALS_HEADING)}\n.*?(?=^## |\Z)", re.M | re.S)
RIVAL_NUM_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def _plain_numbers(text: str) -> list[float]:
    import unicodedata  # noqa: PLC0415

    return [float(m.group(0).replace(",", "")) for m in RIVAL_NUM_RE.finditer(unicodedata.normalize("NFKC", text or ""))]


def rival_problems(record: dict) -> list[str]:
    """製品の比較表（「同じクラスの製品と比べると？」）の数字が、全部「公式ページの根拠の文」か「計算の式」にあるか（API 不要）。
    - 表の値は、記事 JSON の rivals 欄に、根拠の文（quote）・出典 URL・確認日つきで残っていること
    - 値の数字は全部その根拠の文にあること（scripts/rivals.py が取ったページで確かめた文）
    - 章の数字は全部、値・根拠の文・計算の式（差・円の目安）・為替・確認日のどれかにあること"""
    body = record.get("body") or ""
    m = RIVALS_SECTION_RE.search(body)
    data = record.get("rivals")
    if not m and not data:
        return []
    if m and not data:
        return ["製品の比較表の章があるのに、根拠のデータ（rivals）が無い。章を消す"]
    if data and not m:
        return ["製品の比較表のデータ（rivals）があるのに、本文に章が無い"]
    problems = []
    allowed: list[float] = []
    products = data.get("products") or []
    if sum(1 for p in products if not p.get("self")) < 2:
        problems.append("製品の比較表のライバルが2つ未満")
    for p in products:
        allowed += _plain_numbers(p.get("name") or "")  # 製品名の数字（iPhone 18 Pro・第8世代）
        if not p.get("url"):
            problems.append(f"製品の比較表の「{p.get('name')}」に出典 URL が無い")
        elif not p.get("self") and p["url"] not in m.group(0):
            problems.append(f"製品の比較表の出典に「{p.get('name')}」の公式ページへのリンクが無い")
        for key, spec in (p.get("specs") or {}).items():
            quote = spec.get("quote") or ""
            if not (spec.get("url") and spec.get("checkedAt") and quote):
                problems.append(f"製品の比較表の「{p.get('name')}」の{key}に、根拠の文・出典・確認日が無い")
                continue
            qnums = _plain_numbers(quote)
            for n in _plain_numbers(spec.get("display") or ""):
                if not any(abs(n - q) < 1e-9 for q in qnums):
                    problems.append(f"製品の比較表の「{p.get('name')}」の{key}（{spec.get('display')}）の数字が、公式ページの根拠の文に無い")
                    break
            allowed += qnums
            if isinstance(spec.get("amount"), (int, float)):
                allowed.append(float(spec["amount"]))
        for date in [p.get("checkedAt") or ""]:
            allowed += _plain_numbers(date) + [float(x) for x in re.findall(r"\d+", re.sub(r"^\d{4}-", "", date))]
    for c in data.get("computed") or []:
        allowed += _plain_numbers(c.get("text") or "") + [float(v) for v in c.get("values") or []]
    fx = data.get("fx") or {}
    allowed += _plain_numbers(f"{fx.get('rate', '')} {fx.get('date', '')}") + [1.0]
    checked = str(data.get("checkedAt") or "")
    allowed += [float(x) for x in re.findall(r"\d+", checked)] + [float(x.lstrip("0") or 0) for x in re.findall(r"\d+", checked)]
    section = re.sub(r"\]\([^)]*\)", "]", m.group(0))  # リンク先の URL は見ない
    missing = []
    for n in _plain_numbers(section):
        if not any(abs(n - a) <= 1e-9 * max(1.0, abs(a)) for a in allowed) and n not in missing:
            missing.append(n)
    if missing:
        problems.append("製品の比較表に、公式ページの根拠の文・計算の式のどれにも無い数字がある（"
                        + "、".join(f"{n:g}" for n in missing[:5]) + "）")
    return problems


# 数字の照合で見る欄（記事に出る文章）。リンク先の URL は見ない
def _article_numbers_text(record: dict) -> str:
    parts = [record.get("title") or "", record.get("lede") or "", " ".join(record.get("highlights") or []),
             RIVALS_SECTION_RE.sub("", record.get("body") or ""), record.get("easy") or ""]
    parts += [f"{r.get('q', '')} {r.get('a', '')}" for r in record.get("faq") or []]
    parts += list(record.get("todayActions") or [])
    parts += list((record.get("workMoney") or {}).get("points") or [])
    return re.sub(r"\]\([^)]*\)", "]", "\n".join(parts))


COMPUTED_MARK_RE = re.compile(r"計算|試算|換算|目安|ペース")


def number_problems(record: dict, source_text: str, context: str = "") -> list[str]:
    """記事の数字（お金・割合・倍・大きな件数）が、元記事・確認済みの事実の束・計算済みの数字のどれかにあるか（API 不要）。
    LLM には計算させない（2026-11-03 の比較調査の3位）。無い数字は「作った数字」か「自分で計算した数字」として落とす。
    束の数字を使ったのに出典を書いていないときも落とす（どこで確かめた事実か分かるように）"""
    facts = record.get("_facts") or {}
    groups = [g for g in facts.get("groups") or [] if g.get("body")]
    allowed_text = "\n".join([source_text or "", context or ""] + [g["body"] for g in groups] + list(facts.get("computed") or []))
    if not allowed_text.strip():
        return []
    allowed = calc_facts.values(allowed_text) + [float(v) for v in facts.get("computedValues") or []]
    text = _article_numbers_text(record)
    found = [q for q in calc_facts.extract(text) if calc_facts.checkable(q)]
    problems = []
    missing, seen = [], set()
    for q in found:
        if not calc_facts.supported(q, allowed) and q.text not in seen:
            seen.add(q.text)
            missing.append(f"「{q.before[-12:]}{q.text}」")
    if missing:
        problems.append(
            f"元記事・確認済みの事実の束・計算済みの数字のどれにも無い数字がある（{'、'.join(missing[:5])}）。"
            "元記事か束の数字に直すか、その文を削る。割合・差・換算などを自分で計算しない（計算済みの数字の欄にあるものだけ使う）"
        )
    # 束の数字を出典なしで使っていないか
    source_vals = calc_facts.values(f"{source_text}\n{context}")
    plain = [q for q in found if calc_facts.supported(q, allowed) and not calc_facts.supported(q, source_vals)]
    if plain:
        for g in groups:
            if g.get("label") in ("元記事の注記", "元の資料の続き"):
                continue
            vals = calc_facts.values(g["body"])
            if any(calc_facts.supported(q, vals) for q in plain) and not any(m and m in text for m in g.get("marks") or []):
                problems.append(f"確認済みの事実の束（{g.get('label')}：{g.get('name')}）の数字を使っているのに、出典が書かれていない。"
                                f"使った文の直後に「（{g.get('name')}、確認日）」のように書く")
        group_vals = calc_facts.values("\n".join(g["body"] for g in groups))
        comp_vals = calc_facts.values("\n".join(facts.get("computed") or [])) + [float(v) for v in facts.get("computedValues") or []]
        only_computed = [q for q in plain if calc_facts.supported(q, comp_vals) and not calc_facts.supported(q, group_vals)]
        if only_computed and not COMPUTED_MARK_RE.search(text):
            problems.append("計算済みの数字を使っているのに、計算した数字だと分かる書き方になっていない。「（サイソク編集部の計算）」と添える")
    return problems


def ui_step_problems(record: dict, source_text: str, context: str = "") -> list[str]:
    """画面の道順・メニュー名・ボタン名・操作手順が、元記事・確認済みの文脈・確かな事実の束に無いもの（API 不要）。
    サイソクは自分で試していないので、資料に無い画面の手順は書かない（web_articles.fix_ui_steps が先に置き換える。残ったものを落とす）"""
    groups = (record.get("_facts") or {}).get("groups") or []
    allowed_text = "\n".join([source_text or "", context or ""] + [g.get("body") or "" for g in groups])
    if not allowed_text.strip():
        return []
    bad = ui_claims.record_problems(record, ui_claims.Allowed(allowed_text))
    if not bad:
        return []
    return [f"元記事・確認済みの資料に無い画面の手順・メニュー名・ボタン名がある（{'、'.join('「' + b[:24] + '」' for b in bad[:4])}）。"
            "資料にある道順だけを書くか、「公式ヘルプの手順を確認してください」と書く"]


# --------------------------------------------------------------------------- #
# 言葉のチェック（2026-11-03）: 中国語の混入・訳し残しの英単語・用語の誤訳。API 不要。
# 見つかった文は repair_language() が「その文だけ」直す（安い手直し）。直らなければ形の不合格（作り直し）
# --------------------------------------------------------------------------- #

# 中国語にしかない言い回し（日本語の漢字だけで書けてしまうもの。簡体字を含むものは下の文字の判定で見つかる）。
# 「所以」は「100万カ所以上」に出るので入れない
CHINESE_PHRASE_RE = re.compile(
    r"但是|可以|如果|而且|就是|然而|例如|以及|什么|怎么|我们|你们|他们|那个|不过|还是|因为|虽然|其实|那么|这样|并且|已经|应该"
)
_HAN_RE = re.compile(r"[㐀-䶿一-鿿]")


def simplified_chars(text: str) -> list[str]:
    """日本語の文字コード（cp932）に無い漢字＝中国の簡体字など（这・们・过・进 など）。2026-11-03 の全896記事で誤判定 0 件"""
    out = []
    for ch in dict.fromkeys(_HAN_RE.findall(text or "")):
        try:
            ch.encode("cp932")
        except UnicodeEncodeError:
            out.append(ch)
    return out


# 日本語の文の中に、日本語に挟まれて1語だけ残った小文字の英単語（「初期 adopter向け」）。製品名・コマンド名は小文字でも
# 普通の英単語ではないので、この一覧（訳すべき普通の英単語）に入っているものだけを見る（2026-11-03 の全記事で13件・ほぼ本物）
HALF_EN_WORDS = frozenset("""
adopter adopters early user users feature features release releases update updates inference reasoning training launch launched
available availability support supported pricing price prices plan plans customer customers business businesses enterprise
developer developers team teams performance security privacy cost costs revenue growth market markets people company companies
product products service services allowance processing picker selection resolution matching governed generalized contextual
checks effort blocking tier platform storage reusable second dollars drift decoding long-term short-term
access accuracy analysis announcement benefit benefits capability capabilities change changes choice community content contract
control data deal demand design device devices efficiency employee employees experience feedback free global goal
improvement industry information insight insights investment investors leader leaders level limit limits management
member members mission network new news number offer option options partner partners partnership policy power preview process
productivity program project projects quality rate region regions report research result results risk risks rollout
safety sales scale schedule share solution solutions speed standard strategy subscription subscribers success summary
system systems target task tasks technology test testing tool tools traffic trial usage value version vision workers workforce
worldwide yearly monthly weekly daily annual quarterly income profit profits loss losses adoption beta waitlist
generally general public private official officially upcoming coming soon recently latest previous current future
""".split())
_JA_CHAR = r"[぀-ヿ㐀-鿿々〆ー、。・「」]"
HALF_EN_RE = re.compile(rf"(?:(?<={_JA_CHAR})|(?<={_JA_CHAR} ))([a-z][a-z\-]{{2,}})(?= ?{_JA_CHAR})")


def _strip_for_language(text: str) -> str:
    """言葉のチェックで見ない部分（コード・リンク先・URL・引用した英語の画面の文字・英語の補足（ ））を消す"""
    t = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    t = re.sub(r"`[^`]*`", " ", t)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[\"“][^\"”\n]{1,80}[\"”]", " ", t)
    t = re.sub(r"「[\x20-\x7e]{1,80}」", " ", t)
    # 「調査（research）」「（tabs、tabGroups、storage など）」のような英語の補足・一覧は訳し残しではない
    t = re.sub(r"[（(][^（()）\n]{0,80}[）)]", lambda m: " " if len(re.findall(r"[A-Za-z]", m.group(0))) * 2 >= len(m.group(0)) - 2 else m.group(0), t)
    return t


def sentence_language_issues(sentence: str, source_text: str = "") -> list[str]:
    """1文の言葉の問題（直し方の説明つき）"""
    issues = []
    text = _strip_for_language(sentence)
    chars = simplified_chars(text)
    if chars:
        issues.append(f"中国の簡体字（{''.join(chars[:5])}）が混ざっている")
    if m := CHINESE_PHRASE_RE.search(text):
        issues.append(f"中国語の言い回し（「{m.group(0)}」）が混ざっている")
    words = [w for w in HALF_EN_RE.findall(text) if w in HALF_EN_WORDS]
    if words:
        issues.append(f"英単語（{', '.join(dict.fromkeys(words))}）が訳されずに残っている")
    for bad in glossary.mistranslations(text, source_text):
        issues.append(f"用語の誤訳 {bad}")
    return issues


# 言葉のチェックをする欄（読者に見えるもの）。(欄の名前, 取り出し方)
def _language_units(record: dict) -> list[tuple[str, int | None, str]]:
    """(欄, 一覧の番号 or None, 文)。本文などは1文ずつに分ける"""
    units: list[tuple[str, int | None, str]] = []
    for key in ("title", "seoTitle", "lede", "description", "body", "easy", "threadsPost"):
        value = record.get(key)
        if not isinstance(value, str):
            continue
        for line in value.splitlines():
            for sentence in re.split(r"(?<=[。！？])", line):
                if sentence.strip():
                    units.append((key, None, sentence.strip()))
    for key in ("highlights", "todayActions"):
        for i, value in enumerate(record.get(key) or []):
            units.append((key, i, str(value)))
    for i, row in enumerate(record.get("faq") or []):
        units.append(("faq.q", i, str(row.get("q", ""))))
        units.append(("faq.a", i, str(row.get("a", ""))))
    for i, value in enumerate((record.get("workMoney") or {}).get("points") or []):
        units.append(("workMoney", i, str(value)))
    if isinstance(record.get("verdict"), dict) and record["verdict"].get("take"):
        units.append(("verdict.take", None, str(record["verdict"]["take"])))
    return units


def language_issues(record: dict, source_text: str = "") -> list[dict]:
    """[{field, index, sentence, issues}]。同じ文は1回だけ"""
    out, seen = [], set()
    for field, index, sentence in _language_units(record):
        if (field, sentence) in seen:
            continue
        issues = sentence_language_issues(sentence, source_text)
        if issues:
            seen.add((field, sentence))
            out.append({"field": field, "index": index, "sentence": sentence, "issues": issues})
    return out


def language_problems(record: dict, source_text: str = "") -> list[str]:
    found = language_issues(record, source_text)
    if not found:
        return []
    examples = "、".join(f"「{x['sentence'][:30]}」→{x['issues'][0]}" for x in found[:3])
    return [f"言葉の問題がある文が{len(found)}か所（{examples}）。中国語・訳し残しの英単語を自然な日本語にし、用語集どおりに訳す"]


LANG_FIX_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "fixes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"id": {"type": "INTEGER"}, "text": {"type": "STRING"}},
                "required": ["id", "text"],
                "propertyOrdering": ["id", "text"],
            },
        }
    },
    "required": ["fixes"],
}

LANG_FIX_SYSTEM = """あなたはニュースサイトの校閲者です。指摘された文だけを、自然な日本語に直します。出力は指定の JSON だけ。"""

LANG_FIX_PROMPT = """次の文には問題（中国語の混入・訳されずに残った英単語・用語の誤訳・まだ始まっていないことを始まったように書いている・
文体の決まりに合わない言い方）があります。問題は各文の下に書いてあります。1文ずつ直してください。

# 直し方
- 指摘された問題だけを直す。中国語の言葉・簡体字は日本語に、英単語は日本語に訳す（製品名・会社名・略語はそのまま）。
- 文体の直し（幼い言い方・宣伝の言葉・つなぎ・一問一答・リードの1文目）は、ITmedia・日経クロステック水準の落ち着いた大人の日本語にする。
- 事実・数字・日付・固有名詞・リンク・太字（**）は一字も変えない（これから始まることへの直しでは、問題に書かれた開始日だけは書き足してよい）。
  文を足したり削ったりしない（1文は1文のまま）。
- 用語は次の用語集どおりに訳す。

{glossary}

# 直す文（id ごとに、直した文を text に入れて返す）
{items}"""


def _replace_unit(record: dict, field: str, index: int | None, old: str, new: str) -> bool:
    if field in ("faq.q", "faq.a"):
        row = (record.get("faq") or [])[index]
        key = field.split(".")[1]
        if row.get(key) == old:
            row[key] = new
            return True
        return False
    if field == "workMoney":
        points = (record.get("workMoney") or {}).get("points") or []
        if index is not None and index < len(points) and points[index] == old:
            points[index] = new
            return True
        return False
    if field == "verdict.take":
        if (record.get("verdict") or {}).get("take") == old:
            record["verdict"]["take"] = new
            return True
        return False
    if index is not None:
        values = record.get(field) or []
        if index < len(values) and values[index] == old:
            values[index] = new
            return True
        return False
    value = record.get(field)
    if isinstance(value, str) and old in value:
        record[field] = value.replace(old, new, 1)
        return True
    return False


def repair_language(record: dict, source_text: str = "") -> tuple[int, int, str]:
    """言葉の問題がある文だけを、安いモデル（CHECK_TIER）で1回直す。record をその場で書き換える。
    (見つかった数, 直した数, 使ったモデル)。数字が変わった・問題が残った直しは使わない"""
    found = language_issues(record, source_text)
    # 文体の決まりの「直す」項目（scripts/article_style.py）も同じ1回の手直しに入れる
    for x in article_style.repair_items(record):
        same = next((y for y in found if (y["field"], y["sentence"]) == (x["field"], x["sentence"])), None)
        if same:
            same["issues"] += x["issues"]
        else:
            found.append(x)
    have = {(x["field"], x["sentence"]) for x in found}
    for x in future_start_issues(record, source_text):
        if (x["field"], x["sentence"]) in have:
            next(y for y in found if (y["field"], y["sentence"]) == (x["field"], x["sentence"]))["issues"] += x["issues"]
        else:
            found.append(x)
    if not found:
        return 0, 0, ""
    found = found[:20]
    items = "\n".join(f"- id {i}: {x['sentence']}\n  問題: {' / '.join(x['issues'])}" for i, x in enumerate(found))
    prompt = LANG_FIX_PROMPT.format(glossary=glossary.prompt_block(), items=items)
    data, model = llm.generate_json(LANG_FIX_SYSTEM, prompt, LANG_FIX_SCHEMA, temperature=0.1, max_output_tokens=4096, tier=CHECK_TIER,
                                    purpose="helper")
    fixed = 0
    for row in data.get("fixes") or []:
        try:
            x = found[int(row.get("id"))]
        except (TypeError, ValueError, IndexError):
            continue
        new = str(row.get("text") or "").strip()
        # 「これから始まる」への直しでは、元記事の開始日（11月9日）を書き足してよい
        if not new or new == x["sentence"] or not (_numbers(x["sentence"]) <= _numbers(new) <= _numbers(x["sentence"]) | x.get("allow", set())):
            continue
        if sentence_language_issues(new, source_text):
            continue
        if x["sentence"].lstrip().startswith("#") and not new.startswith("#"):
            new = re.match(r"\s*#+\s*", x["sentence"]).group(0) + new  # 見出しの印は残す
        if x.get("check") and x["check"](new):
            continue
        if x["sentence"].count("**") != new.count("**") or x["sentence"].count("](") != new.count("]("):
            continue
        fixed += _replace_unit(record, x["field"], x["index"], x["sentence"], new)
    return len(found), fixed, model


# --------------------------------------------------------------------------- #
# Threads 紹介文の「要約すると、」（threads_hooks.POST_STYLE 3 の型。2つ目のかたまりの書き出し）
# --------------------------------------------------------------------------- #


def _post_blocks(text: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\s*\n", str(text or "").strip()) if b.strip()]


def summary_opener_problems(record: dict) -> list[str]:
    """紹介文（threads_post）の2つ目のかたまりが「要約すると、」で始まっているか（型が3の記事だけ）"""
    if int(record.get("threadsStyle") or 0) < 3 or not str(record.get("threadsPost") or "").strip():
        return []
    blocks = _post_blocks(record["threadsPost"])
    opener = f"{persona.SUMMARY_OPENER}、"
    if len(blocks) == 3 and blocks[1].startswith(opener):
        return []
    return [f"threads_post が「書き出し／{opener}＋事実2文／問いかけ」の3つのかたまりになっていない（2つ目のかたまりを「{opener}」で始める）"]


def fix_summary_opener(record: dict) -> bool:
    """機械で直せる崩れだけ直す（2つ目のかたまりの頭に「要約すると、」が無い・「要約すると：」になっている）"""
    post = str(record.get("threadsPost") or "")
    blocks = _post_blocks(post)
    opener = f"{persona.SUMMARY_OPENER}、"
    if len(blocks) != 3 or blocks[1].startswith(opener):
        return False
    second = re.sub(rf"^{persona.SUMMARY_OPENER}\s*[、,:：]?\s*", "", blocks[1])
    second = re.sub(r"^(?:つまり|簡単に言うと|ざっくり言うと|ひとことで言うと)[、,:：]\s*", "", second)
    if not second or second.endswith(("？", "?")):
        return False
    record["threadsPost"] = "\n\n".join([blocks[0], opener + second, blocks[2]])
    return True


POST_FIX_SCHEMA = {"type": "OBJECT", "properties": {"threads_post": {"type": "STRING"}}, "required": ["threads_post"]}


def repair_summary_opener(record: dict) -> str:
    """紹介文だけを型どおりに書き直す（安いモデル。本文は変えない）。使ったモデル名。直らなければ例外にせず元のまま"""
    import threads_hooks  # noqa: PLC0415 - 重い import を避ける

    prompt = (
        f"{threads_hooks.POST_RULES}\n\n# 記事\nタイトル: {record.get('title', '')}\nリード: {record.get('lede', '')}\n"
        f"要点: {' / '.join(record.get('highlights') or [])}\n\n# 今の紹介文（型が崩れている）\n{record.get('threadsPost', '')}\n\n"
        "上のルールの3つのかたまり（書き出し／「要約すると、」で始まる事実2文／問いかけ）に直した threads_post を返す。"
        "事実・数字は記事のとおり。記事に無いことを足さない。"
    )
    data, model = llm.generate_json("あなたはニュースサイトのSNS担当の編集者です。出力は指定の JSON だけ。", prompt, POST_FIX_SCHEMA,
                                    temperature=0.3, max_output_tokens=1024, tier=CHECK_TIER, purpose="helper")
    new = re.sub(r"\n{3,}", "\n\n", str(data.get("threads_post") or "").strip())
    old = record.get("threadsPost")
    record["threadsPost"] = new
    if summary_opener_problems(record) or sentence_language_issues(new):
        record["threadsPost"] = old
        return ""
    return model


# --------------------------------------------------------------------------- #
# 料金・プランのページの差分（page_watch / pricing_watch）を、そのまま貼っていないか
# --------------------------------------------------------------------------- #

RAW_DIFF_RE = re.compile(
    r"(?m)^\s*[-*]\s*(?:削除|追加)\s*[:：]|"
    r"(?<![A-Za-z])(?:Plan|Feature|Price|Tier|Model|Limit|Usage)\s*:\s*[^,\n。]{1,40},\s*(?:Plan|Feature|Price|Tier|Model|Limit|Usage)\s*:"
)


def raw_diff_problems(record: dict) -> list[str]:
    """ページの差分の行（「- 追加: …」「Plan: Pro, Feature: …」）がそのまま記事に出ていないか"""
    text = "\n".join(str(record.get(k) or "") for k in ("title", "lede", "body", "easy", "description", "videoTitle"))
    text += "\n" + "\n".join(record.get("highlights") or [])
    m = RAW_DIFF_RE.search(text)
    if not m:
        return []
    return [f"料金・プランのページの差分（「{m.group(0)[:30]}」）をそのまま貼っている。変更点は自然な日本語の文か表（前→後）で説明する"]


# --------------------------------------------------------------------------- #
# 判定カード（verdict）と本文の食い違い
# --------------------------------------------------------------------------- #

# いま提供が始まっている（順次提供を含む）言い方。「予定」「これから」を含む文は数えない
ROLLING_RE = re.compile(
    r"順次(?:提供|展開|配信|公開)(?:され|を始め|を開始し|し)て(?:い|おり)|順次(?:提供|展開|配信)中|"
    r"提供(?:を|が)(?:開始|始まり|始め)(?:しました|ました|られました)|提供(?:が始まって|されて)います|提供中です|"
    r"利用できるようになりました|使えるようになりました|一般提供(?:を開始|が始まり)"
)
FUTURE_RE = re.compile(r"予定|見込み|今後|将来|これから|まもなく|近く|以降|か月|年内|未定|まだ|始まっていません|されていません|ではありません")
JAPAN_YES_RE = re.compile(r"(?:日本(?:から|でも|で)?(?:も)?)?(?:使えます|利用できます|使用できます|利用可能です|使える状態です|提供されています|対応しています)")
JAPAN_NO_RE = re.compile(r"使えません|利用できません|提供されていません|対象外|提供の予定はありません|未提供|対応していません")
JAPAN_PARTIAL_RE = re.compile(r"英語のみ|英語だけ|英語版|API(?:経由|から)|一部|条件|法人|限定|ただし")


def _section(body: str, heading_re: re.Pattern) -> str:
    m = heading_re.search(body or "")
    if not m:
        return ""
    rest = body[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def _first_sentence(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "|", "-", "*", ">")):
            return re.split(r"(?<=[。！？])", line)[0]
    return ""


def _status_sentences(record: dict) -> list[str]:
    """提供状況を述べる文（リード・概要・日本で使える？の最初の段落・判定のひとこと）。表と箇条書きは見ない"""
    body = record.get("body") or ""
    parts = [record.get("lede") or "", _section(body, re.compile(r"^##\s*概要.*$", re.M)),
             _section(body, japan_availability.JAPAN_HEADING_RE), (record.get("verdict") or {}).get("take") or ""]
    out = []
    for part in parts:
        for line in part.splitlines():
            if line.strip().startswith(("|", "#")):
                continue
            out += [s for s in re.split(r"(?<=[。！？])", line) if s.strip()]
    return out


def align_verdict(record: dict) -> list[str]:
    """判定カードを本文の答えにそろえる（はっきり分かるときだけ）。直した内容の説明を返す。
    - status が soon/demo なのに、本文が「順次提供されています」「提供を開始しました」と書いている → limited（段階的に提供中）
    - japan が unknown なのに、「日本で使える？」の最初の1文が「使えます」 → ok（英語のみ・API だけ等の条件があれば partial）"""
    verdict = record.get("verdict")
    if not isinstance(verdict, dict) or verdict.get("kind") in ("sports", "public"):
        return []
    notes = []
    if verdict.get("status") in ("soon", "demo"):
        hit = next((s for s in _status_sentences(record) if ROLLING_RE.search(s) and not FUTURE_RE.search(s)), None)
        if hit:
            notes.append(f"判定の提供状況 {verdict['status']} → limited（本文「{hit.strip()[:40]}」）")
            verdict["status"] = "limited"
    section = _section(record.get("body") or "", japan_availability.JAPAN_HEADING_RE)
    first = _first_sentence(section)
    if verdict.get("japan") == "unknown" and first and JAPAN_YES_RE.search(first) and not JAPAN_NO_RE.search(first) \
            and not FUTURE_RE.search(first) and not japan_availability.UNKNOWN_ANSWER_RE.search(first):
        # 条件（英語のみ・API だけ・一部の人だけ）は、最初の段落の続きの文に書かれることが多い
        para = section.strip().split("\n\n")[0]
        value = "partial" if JAPAN_PARTIAL_RE.search(para) else "ok"
        notes.append(f"判定の日本 unknown → {value}（本文「{first[:40]}」）")
        verdict["japan"] = value
    return notes


def verdict_problems(record: dict) -> list[str]:
    """判定カードと本文の「日本で使える？」・提供状況が食い違っていないか（align_verdict で直せないもの）"""
    verdict = record.get("verdict")
    if not isinstance(verdict, dict) or verdict.get("kind") in ("sports", "public"):
        return []
    problems = []
    first = _first_sentence(_section(record.get("body") or "", japan_availability.JAPAN_HEADING_RE))
    if first:
        says_no = bool(JAPAN_NO_RE.search(first))
        says_yes = bool(JAPAN_YES_RE.search(first)) and not says_no and not FUTURE_RE.search(first)
        if verdict.get("japan") == "ok" and says_no:
            problems.append(f"判定は「日本から使える」なのに、本文の「日本で使える？」は「{first[:30]}」。どちらかを元記事に合わせてそろえる")
        if verdict.get("japan") == "no" and says_yes:
            problems.append(f"判定は「日本では未提供」なのに、本文の「日本で使える？」は「{first[:30]}」。どちらかを元記事に合わせてそろえる")
    if verdict.get("status") in ("soon", "demo"):
        hit = next((s for s in _status_sentences(record) if ROLLING_RE.search(s) and not FUTURE_RE.search(s)), None)
        if hit:
            problems.append(f"判定は「提供はこれから」なのに、本文は「{hit.strip()[:30]}」と書いている。提供状況をそろえる")
    if verdict.get("status") == "available":
        hit = next((s for s in _status_sentences(record)[:6] if re.search(r"提供(?:は|が)?(?:まだ|これから)|提供開始は未定|まだ(?:使えません|提供されていません)", s)), None)
        if hit and not re.search(r"日本", hit):
            problems.append(f"判定は「今すぐ使える」なのに、本文は「{hit.strip()[:30]}」と書いている。提供状況をそろえる")
    return problems


# --------------------------------------------------------------------------- #
# まだ始まっていないことを「始まった」と書いていないか（2026-11-03。東京都の支援金の受付は11月9日からなのに
# 「受付を始めています」と書いた試し書きがあり、事実確認も通った）。日付は calc_facts.dated_mentions で実際の日付にする
# --------------------------------------------------------------------------- #

# 始まる出来事の種類と、その言葉（元記事で日付の近くにあるもの）
START_EVENTS = {
    "受付": r"受付|受け付け|申請|募集|応募|申し込",
    "提供": r"提供|配信|公開|利用|開始|スタート|始ま",
    "発売": r"発売|販売|予約",
}
# 日付のすぐ後ろの「から」「より」「以降」「開始」（＝この日に始まる）
START_TAIL_RE = re.compile(r"^(?:\s?[（(][^）)]{1,8}[）)])?(?:\s?(?:午前|午後)?\s?\d{1,2}(?:[:：]\d{2}|時(?:\d{1,2}分)?))?\s?(?:から|より|以降|に開始|開始|スタート)")
# 記事の中の「もう始まった」の言い方（種類ごと）
ALREADY_RES = {
    "受付": re.compile(
        r"(?:受付|申請|募集|申し込み)(?:の受付)?(?:を|が)?(?:開始しました|開始されました|開始しています|開始されています|始めました|始めています|"
        r"始まりました|始まっています|スタートしました|スタートしています|スタート$)|受付中|受け付けています|受け付けを始め|申請できるようになりました"
    ),
    "提供": re.compile(
        r"(?:提供|配信|公開)(?:を|が)?(?:開始しました|開始されました|開始しています|始めました|始めています|始まりました|始まっています|スタートしました)|"
        r"提供されています|提供中|配信中|配信されています|利用できるようになりました|使えるようになりました"
    ),
    "発売": re.compile(r"発売中|販売中|発売されました|発売しました|発売されています|販売(?:を|が)?(?:開始しました|始まっています|されています)"),
}
# 未来・別の話だと分かる文（直さない）
NOT_ALREADY_RE = re.compile(r"予定|始まります|開始します|開始されます|受け付けます|受付します|提供します|発売します|見込み|すでに|既に|従来|これまで|既存|前回|過去")


def _published(record: dict):
    from datetime import datetime  # noqa: PLC0415

    for key in ("publishedAt", "sourcePublishedAt", "generatedAt"):
        try:
            return datetime.fromisoformat(str(record.get(key) or "").replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def future_starts(source_text: str, published) -> list[dict]:
    """元記事で、記事の日付より後に始まる出来事（「11月9日から受付」など）。[{date, kinds, label}]"""
    if not published:
        return []
    base = published.astimezone(calc_facts.JST_TZ).date()
    text = calc_facts.nfkc(source_text or "")
    out: dict = {}
    for when, start, end in calc_facts.dated_mentions(text, published):
        if when <= base or not START_TAIL_RE.match(text[end:end + 24]):
            continue
        window = text[max(0, start - 60):end + 40]
        kinds = [k for k, pattern in START_EVENTS.items() if re.search(pattern, window)]
        if not kinds:
            continue
        row = out.setdefault(when, {"date": when, "kinds": set(), "label": f"{when.month}月{when.day}日"})
        row["kinds"] |= set(kinds)
    return list(out.values())


def _future_claim(sentence: str, starts: list[dict], published) -> tuple[dict, str] | None:
    """1文が、まだ始まっていない出来事を「もう始まった」と書いているなら (出来事, 種類)"""
    if not starts or NOT_ALREADY_RE.search(sentence):
        return None
    base = published.astimezone(calc_facts.JST_TZ).date()
    # 記事の日付より前（または当日）の日付が書かれた文は、前の回など別の出来事の話
    if any(d <= base for d, _, _ in calc_facts.dated_mentions(sentence, published)):
        return None
    plain = calc_facts.nfkc(sentence)
    for row in starts:
        # 「11月9日からスタート」のように開始日を添えて書いた文は、これからのことだと分かる
        if re.search(re.escape(row["label"]) + r"(?:\s?[（(][^）)]{1,8}[）)])?\s?(?:から|より|以降)", plain):
            continue
        kind = next((k for k in sorted(row["kinds"]) if ALREADY_RES[k].search(sentence.strip())), None)
        if kind:
            return row, kind
    return None


def future_start_issues(record: dict, source_text: str = "") -> list[dict]:
    """まだ始まっていない出来事を「始めています」「受付中」と書いた文。[{field, index, sentence, issues, allow, check}]"""
    published = _published(record)
    starts = future_starts(source_text, published)
    out, seen = [], set()
    for field, index, sentence in _language_units(record) if starts else []:
        hit = _future_claim(sentence, starts, published)
        if (field, sentence) in seen or not hit:
            continue
        row, kind = hit
        seen.add((field, sentence))
        out.append({
            "field": field, "index": index, "sentence": sentence,
            "issues": [f"{row['label']}から始まる{kind}を、もう始まったように書いている。"
                       f"「{row['label']}から{kind}が始まります」のように、これからのことだと分かる言い方に直す"],
            # 直した文に書き足してよい数字（開始日の年・月・日。令和の年も）と、直した文の確かめ方
            "allow": set(re.findall(r"\d+", f"{row['date'].year} {row['label']} {row['date'].year - 2018}")),
            "check": lambda text, starts=starts, published=published: _future_claim(text, starts, published) is not None,
        })
    return out


def future_start_problems(record: dict, source_text: str = "") -> list[str]:
    found = future_start_issues(record, source_text)
    if not found:
        return []
    return [f"まだ始まっていないことを、もう始まったように書いている文が{len(found)}か所（「{found[0]['sentence'][:30]}」→{found[0]['issues'][0]}）"]


def align_future_verdict(record: dict, source_text: str = "") -> list[str]:
    """制度の判定カード: 受付が記事の日付より後に始まり、カードの「いつ」もその日付なら、「受付中」（available）を「これから始まる」（soon）に"""
    verdict = record.get("verdict")
    if not isinstance(verdict, dict) or verdict.get("kind") != "public" or verdict.get("status") != "available":
        return []
    when = calc_facts.nfkc(str(verdict.get("when") or ""))
    for row in future_starts(source_text, _published(record)):
        if "受付" in row["kinds"] and row["label"] in when:
            verdict["status"] = "soon"
            return [f"判定の提供状況 available → soon（受付は{row['label']}から）"]
    return []


def pre_gate_repair(record: dict, source_text: str = "", log=print) -> list[str]:
    """品質ゲートの前の安い手直し（record をその場で書き換える）。行ったことの説明を返す。
    1. 判定カードを本文にそろえる（制度の受付がまだ先なら「これから始まる」）  2. 紹介文の「要約すると、」（機械 → だめなら紹介文だけ書き直し）
    3. 中国語・訳し残しの英単語・誤訳・まだ始まっていないことを始まったと書いた文だけ直す。ここで直らなかったものは evaluate の形のチェックで不合格になる"""
    notes = align_verdict(record) + align_future_verdict(record, source_text)
    if summary_opener_problems(record):
        if fix_summary_opener(record):
            notes.append("紹介文の2つ目のかたまりに「要約すると、」を付けた")
        else:
            try:
                model = repair_summary_opener(record)
            except Exception as error:  # noqa: BLE001
                model, notes = "", notes + [f"紹介文の書き直しに失敗（{str(error)[:60]}）"]
            if model:
                notes.append(f"紹介文を型どおりに書き直した（{model}）")
    try:
        found, fixed, model = repair_language(record, source_text)
    except Exception as error:  # noqa: BLE001
        found, fixed, model = 1, 0, ""
        notes.append(f"言葉の手直しに失敗（{str(error)[:60]}）")
    if found:
        notes.append(f"言葉の問題 {found} 文のうち {fixed} 文を直した（{model or '-'}）")
    for note in notes:
        log(f"   [repair] {note}")
    return notes


# お金・税・制度の記事の章立て（web_articles.MONEY_OUTLINE）で必ず要る章。早見表は材料が無ければ省いてよい
MONEY_SECTIONS = [("何が変わる", r"何が変わ"), ("いつから", r"いつから"), ("誰が対象", r"対象"), ("自分の場合の確かめ方", r"確かめ方|確かめる|確認のしかた|確認方法")]


def money_outline_problems(record: dict) -> list[str]:
    """お金・税・制度の記事（束が money と判定したもの）が、決まった章立てで書かれているか（API 不要）"""
    if not (record.get("_facts") or {}).get("money") or record.get("newsType"):
        return []  # ニュースの種類の型で書いた記事（newsType あり）は、種類ごとの型（scripts/news_types.py の policy）で見る
    heads = [h for h in re.findall(r"(?m)^##\s+(.+)$", record.get("body") or "")]
    missing = [name for name, pattern in MONEY_SECTIONS if not any(re.search(pattern, h) for h in heads)]
    if not missing:
        return []
    return [f"お金・税・制度の記事の章立てになっていない（無い章: {'・'.join(missing)}）。"
            "「## 何が変わる？」「## いつから？」「## 誰が対象？」「## 早見表」（材料があるとき）「## 自分の場合の確かめ方」の順に書く"]


FACT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "grounded": {"type": "BOOLEAN"},
        "score": {"type": "INTEGER"},
        "problems": {"type": "ARRAY", "items": {"type": "STRING"}},
        "fatal": {"type": "BOOLEAN"},
    },
    "required": ["grounded", "score", "problems"],
    "propertyOrdering": ["grounded", "score", "problems", "fatal"],
}

# 新しい Web 記事は事実確認と同じ呼び出しで読者への有用性も確認する。
# followup / evergreen が使う既存の FACT_SCHEMA は変更しない。
WEB_FACT_SCHEMA = {
    **FACT_SCHEMA,
    "properties": {**FACT_SCHEMA["properties"], "useful": {"type": "BOOLEAN"}},
    "required": ["grounded", "score", "problems", "fatal", "useful"],
    "propertyOrdering": ["grounded", "score", "problems", "fatal", "useful"],
}

FACT_SYSTEM = """あなたは報道機関の校閲記者です。AIが書いた記事を、元記事（一次情報）と1文ずつ突き合わせて事実確認します。
判定は厳格に、しかし公平に行ってください。出力は指定の JSON だけ。"""

FACT_PROMPT = """# 元記事（一次情報。自動抽出なのでメニュー等の残骸は無視）
<<<
{source}
>>>
{context}

# 確認する記事
<<<
{article}
>>>

# 確認のしかた
- 記事にある数値・日付・価格・固有名詞・発言・対象者・提供状況が、元記事に書かれているか、元記事と矛盾しないかを確かめる。
- 「確認済みの文脈」に書かれた事実（サイソクの過去記事・当サイトで確認済みの日本での提供状況・確かな事実の束〔公式の料金表・当サイトのガイド・元記事のリンク先の公式ページ・公的資料・為替〕）は、元記事と同じく正しい事実として扱う。
  記事がその事実を使うときは「（Anthropic公式の料金ページ、11/3確認）」のように出典が添えてあればよい。出典が無いだけなら言い過ぎと同じ軽い問題（fatal にしない）。
- 「計算済みの数字」（サイソク編集部の計算。式つき）にある数字は、表記どおり（「約」を含む）なら正しい。欄に無い計算結果（記事が自分で計算した割合・差・増減率・ペース・円換算）は創作として問題にする。
- 次は問題にしない: 元記事の内容の言い換え・要約、「確認済みの文脈」に書かれた事実（過去の経緯・日付・価格など）、「（編集部注）」と明記した一般的な背景説明（具体的な数値を含まないもの）、
  「元記事では言及なし」「発表されていない」といった記述、読者への一般的な助言（公式情報を確認しよう等）。
- 「[元記事の情報]」の発信元・公開日時に基づく記述（「〇〇の公式ブログによると」「10月26日に発表」など）は元記事に書かれた事実として扱う。
  米国時間と日本時間の時差による1日の日付のずれも問題にしない。
- 広く知られた一般常識・定訳は創作として扱わない（例: SEC＝米証券取引委員会、IPO（上場）前の会社は株式を公開していない、Nasdaq は米国の株式市場）。
- 次は必ず問題にする: 元記事に無い具体的な数値・日付・価格・機能・発言の創作、元記事との矛盾、
  予定や研究段階のものを提供開始済み・実用化済みのように書くこと、効能・効果の断定、特定個人への憶測。
- 「よくある質問」の答えと「今日やること」も記事の一部として同じ基準で確かめる。答えに元記事・確認済みの文脈に無い数字・条件・提供状況があれば必ず問題にする。
  今日やることで、元記事に無い画面の場所・ボタン名・手順を作っていれば問題にする（「公式の設定画面で確認する」のような一般的な行動はよい）。
- 「仕事・お金にどう活かせる」の欄も記事の一部として同じ基準で確かめる。誰に関係するか・明日からできる行動の提案は
  一般的な助言として扱ってよいが、元記事に無い金額・節約額・短縮時間・収入の見込み（自分で計算した数字を含む）、
  提供前のものを今すぐ使えるように書くこと、特定の銘柄・ファンド・物件の売買のすすめ・相場の予想・儲かるといった断定は必ず問題にする。

# 出力
- grounded: 創作・矛盾が1つも無ければ true。1つでもあれば false。
- score: 記事全体が元記事に裏付けられている度合い（0〜100）。軽微な言い過ぎは減点、創作・矛盾は大きく減点。
- problems: 見つけた問題を最大8個。各項目は「記事の該当箇所（短く引用）→ 何が問題か」を1文で。問題が無ければ空配列。
- fatal: 数値・日付・価格・固有名詞・発表内容（何が・いつ・誰に提供されるか）の取り違えや創作が1つでもあれば true。
  言い過ぎ・訳語の選び方・言い回し・補足説明の程度の問題だけなら false。"""

WEB_USEFULNESS_PROMPT = """

# 読者にとっての有用性も確認する
- 文字数や特定の章・表の有無では判定しない。短い速報でも十分に答えていればよい。
- 見出しが約束した内容について、何が起きたかと、元情報から確認できる対象・条件・読者との関係など、理解に必要な具体的事実があるか確認する。
- 大事な条件が元資料にない場合、無理に補わず不明と区別できているかを見る。根拠のない体験談・比較・推測や誇大な断定は不可。
- 編集部の見方は、確認できた事実を根拠とし、事実と区別した解釈であればよい。
- useful: 見出しの疑問に答え、読者が概要を理解できる具体性があれば true。それ以外は false とし、problems に不足を記す。
"""


CONTEXT_BLOCK = """
# 確認済みの文脈（サイソクの過去記事・日本での提供状況・確かな事実の束・計算済みの数字。ここにある事実は創作として扱わない）
<<<
{context}
>>>
"""
# 読みやすさの基準（Terra の書き直し: 平均33字・60字超6%・段落48字）
READ_MAX_AVG_SENTENCE = 45
READ_LONG_SENTENCE = 60
READ_MAX_LONG_SHARE = 0.20
READ_MAX_AVG_PARAGRAPH = 120
READ_MAX_PARAGRAPH = 220
# 段落に数えない行: 見出し・表・箇条書き・引用・コード
_NON_PROSE_RE = re.compile(r"^\s*(#|\||[-*+]\s|\d+[.)]\s|>|```|出典[:：])")
_SENTENCE_END_RE = re.compile(r"(?<=[。！？!?])(?![」』）)\]])")


def _plain_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _norm(text: str) -> str:
    return re.sub(r"[\s　、。，．,.・「」『』()（）\-—:：]", "", unicodedata.normalize("NFKC", text or ""))


def copied_sentences(body: str, source: str, min_len: int = 40) -> list[str]:
    """元記事とほぼ同じ文（記号・空白を無視して40字以上一致）"""
    src = _norm(source)
    if not src:
        return []
    found = []
    for sentence in re.split(r"(?<=[。！？])|\n", body or ""):
        clean = _norm(re.sub(r"[#>*|`]", "", sentence))
        if len(clean) >= min_len and clean in src:
            found.append(sentence.strip()[:50])
    return found


def _strip_inline(text: str) -> str:
    """リンク・強調・コードの記号を外した地の文"""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"(\*\*|__|\*|<br\s*/?>)", "", text)
    return re.sub(r"\s+", "", text)


def paragraphs(body: str) -> list[str]:
    """地の文の段落（見出し・表・箇条書き・引用・コードを除く）。空行か地の文以外の行で区切る"""
    found: list[str] = []
    current: list[str] = []
    in_code = False
    for line in (body or "").splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if in_code or not line.strip() or _NON_PROSE_RE.match(line):
            if current:
                found.append("".join(current))
                current = []
            continue
        current.append(_strip_inline(line))
    if current:
        found.append("".join(current))
    return [p for p in found if p]


def readability(body: str) -> dict:
    """文・段落の長さ（決定的。LLM なし）"""
    paras = paragraphs(body)
    sentences = [s for p in paras for s in _SENTENCE_END_RE.split(p) if len(s) >= 2]
    lengths = [len(s) for s in sentences]
    para_lengths = [len(p) for p in paras]
    long_count = sum(1 for n in lengths if n > READ_LONG_SENTENCE)
    return {
        "sentences": len(lengths),
        "avg_sentence": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
        "long_share": round(long_count / len(lengths), 3) if lengths else 0.0,
        "paragraphs": len(para_lengths),
        "avg_paragraph": round(sum(para_lengths) / len(para_lengths), 1) if para_lengths else 0.0,
        "max_paragraph": max(para_lengths, default=0),
    }


def readability_problems(body: str, stats: dict | None = None) -> list[str]:
    """読みやすさの基準外。作り直しの指示に使えるよう、数字と直し方を書く"""
    r = stats or readability(body)
    if not r["sentences"]:
        return []
    problems = []
    if r["avg_sentence"] > READ_MAX_AVG_SENTENCE or r["long_share"] > READ_MAX_LONG_SHARE:
        problems.append(
            f"文が長い（1文の平均 {r['avg_sentence']}字・{READ_LONG_SENTENCE}字超の文が {r['long_share']:.0%} / "
            f"基準 平均{READ_MAX_AVG_SENTENCE}字以下・{READ_LONG_SENTENCE}字超{READ_MAX_LONG_SHARE:.0%}以下）。"
            "1文は50字以内を目安に2文に分け、1文に言いたいことは1つにする（内容と文字数は減らさない）"
        )
    if r["avg_paragraph"] > READ_MAX_AVG_PARAGRAPH or r["max_paragraph"] > READ_MAX_PARAGRAPH:
        problems.append(
            f"段落が長い（平均 {r['avg_paragraph']}字・最長 {r['max_paragraph']}字 / "
            f"基準 平均{READ_MAX_AVG_PARAGRAPH}字以下・最長{READ_MAX_PARAGRAPH}字以下）。"
            "1段落は1〜3文にし、数字が3つ以上並ぶところは箇条書きにする（内容と文字数は減らさない）"
        )
    return problems


def structural_problems(record: dict, kind: str, source_text: str = "", context: str = "") -> list[str]:
    """形のチェック。問題のリスト（空なら合格）。context は確認済みの文脈（数字の照合に使う）"""
    problems: list[str] = []
    body = record.get("body") or ""
    easy = (record.get("easy") or "").strip()
    title = record.get("title") or ""

    if not re.sub(r"(?m)^\s*#{1,6}.*$|\s", "", body):
        problems.append("本文が空です")
    if not re.sub(r"(?m)^\s*#{1,6}.*$|\s", "", easy):
        problems.append("easy_markdown に本文がありません")
    if not easy.startswith(("## ひとことで言うと", "## 概要")):
        problems.append("easy_markdown が「## ひとことで言うと」から始まっていない")
    if not TITLE_RANGE[0] <= len(title) <= TITLE_RANGE[1]:
        problems.append(f"title の長さが不適切（{len(title)}字 / {TITLE_RANGE[0]}〜{TITLE_RANGE[1]}字）")
    if re.search(r"[！!]", f"{title}{record.get('seoTitle') or ''}"):
        problems.append("title / seo_title に感嘆符がある")
    text = f"{body}\n{easy}"
    if m := BOILERPLATE_RE.search(text):
        problems.append(f"Webページの英語の残骸が残っている（「{m.group(0)}」）")
    if m := ENGLISH_RUN_RE.search(re.sub(r"`[^`]*`|\[[^\]]*\]\([^)]*\)", " ", text)):
        problems.append(f"訳されていない英文が残っている（「{m.group(0)[:40]}…」）")
    if record.get("sourceType") == "web" and (m := VIDEO_LEFTOVER_RE.search(text)):
        problems.append(f"文章が元なのに動画前提の表現がある（「{m.group(0)}」）。「〇〇の発表」と書く")
    if record.get("sourceType") == "web" and kind != "update" and not record.get("workMoney"):
        problems.append(f"work_money（「{persona.WORK_MONEY_HEADING}」欄）がありません")
    problems += persona.work_money_problems(record.get("workMoney"))
    # 個別の投資助言・売買のすすめ・取引のやり方の説明と、投資・マネーの免責文の抜け（scripts/genre_focus.py。台帳 A22）
    problems += genre_focus.advice_problems(record)
    problems += language_problems(record, source_text)
    # 文体の門（報告「記事の文章レベル比較_2026-11-03」の自動ゲート17項目・台帳 A1〜A6・A18〜A21。scripts/article_style.py が1か所で持つ）:
    # 決まった型の見出し・見出しの多すぎ・中身のない章・指示文の漏れ・割った文・一問一答・幼い言い方・宣伝の言葉・つなぎ・くり返し・英語の画面名
    if kind != "update":
        problems += article_style.problems(record)
    problems += future_start_problems(record, source_text)
    problems += raw_diff_problems(record)
    if kind != "update":
        problems += summary_opener_problems(record)
        problems += verdict_problems(record)
    if record.get("sourceType") == "web" and kind != "update":
        problems += first_person_problems(record)
        problems += promise_problems(record)
        problems += faq_problems(record)
        if m := persona.OLD_VIEWPOINT_RE.search(body):
            # 台帳 A24: 立場の小見出しは新旧どちらも作らない（前は「3つの立場にする」と直させていて、A24 と食い違っていた）
            problems.append(f"古い立場の小見出し（「{m.group(0).lstrip('# ')}」）がある。立場の小見出しは作らず、事実がある影響だけを地の文で書く")
        problems += number_problems(record, source_text, context)
        problems += rival_problems(record)
        problems += money_outline_problems(record)
        problems += ui_step_problems(record, source_text, context)
    if source_text:
        copied = copied_sentences(body, source_text)
        if len(copied) >= 2:
            problems.append(f"元記事の文をそのまま書き写している（{len(copied)}文。例「{copied[0]}」）。自分の言葉で要約する")
    return problems


def _article_for_check(record: dict) -> str:
    parts = [
        f"タイトル: {record.get('title', '')}",
        f"リード: {record.get('lede', '')}",
        "要点: " + " / ".join(record.get("highlights") or []),
    ]
    work_money = record.get("workMoney") or {}
    if work_money.get("points"):
        # 本文より前に置く（ARTICLE_CAP で切られても必ず事実確認に入るように）
        parts.append(f"{persona.WORK_MONEY_HEADING}: " + " / ".join(work_money["points"]))
    if record.get("faq"):
        # 本文より前に置く（ARTICLE_CAP で切られても事実確認に入るように）
        parts.append("よくある質問:\n" + "\n".join(f"Q. {r.get('q', '')} A. {r.get('a', '')}" for r in record["faq"]))
    if record.get("todayActions"):
        parts.append("今日やること: " + " / ".join(record["todayActions"]))
    parts += [
        f"本文:\n{record.get('body', '')}",
        f"かんたん版:\n{record.get('easy', '')}",
    ]
    verdict = record.get("verdict")
    if verdict:
        parts.append(f"判定: 提供状況={verdict.get('status')} 価格={verdict.get('price')} ひとこと={verdict.get('take')}")
    return "\n\n".join(parts)[:ARTICLE_CAP]


def fact_check(record: dict, source_text: str, context: str = "", require_usefulness: bool = False) -> dict:
    """{grounded, score, problems, model}。context は確認済みの事実（過去記事など）。LLM が使えないときは例外"""
    prompt = FACT_PROMPT.format(
        source=source_text[:SOURCE_CAP],
        context=CONTEXT_BLOCK.format(context=context[:CONTEXT_CAP]) if context.strip() else "",
        article=_article_for_check(record),
    )
    if require_usefulness:
        prompt += WEB_USEFULNESS_PROMPT
    data, model = llm.generate_json(FACT_SYSTEM, prompt, WEB_FACT_SCHEMA if require_usefulness else FACT_SCHEMA,
                                    temperature=0.1, max_output_tokens=4096, tier=CHECK_TIER, purpose="check")
    try:
        score = int(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    problems = [str(p).strip() for p in (data.get("problems") or []) if str(p).strip()][:8]
    grounded = data.get("grounded") is True
    # fatal が無い（古い形の応答）ときは、不合格を致命的とみなす（緩める方向に倒さない）
    fatal = data.get("fatal") is not False if "fatal" in data else not grounded
    result = {"grounded": grounded, "score": max(0, min(100, score)), "problems": problems, "fatal": fatal, "model": model}
    if require_usefulness:
        result["useful"] = data.get("useful") is True
    return result


VIDEO_FIX = {"映像では": "元記事では", "スライドでは": "元記事では", "音声では": "元記事では"}


def fix_video_leftover(record: dict) -> None:
    """文章が元の記事に残った「動画では」などを「元記事では」に機械的に直す（作り直しで無料枠・時間を使わない。
    2026-10-28 の速報3本がこれで1回ずつ作り直しになった）。record をその場で書き換える"""
    if record.get("sourceType") != "web":
        return
    for key in ("body", "easy", "lede"):
        text = record.get(key)
        if not isinstance(text, str) or not VIDEO_LEFTOVER_RE.search(text):
            continue
        text = re.sub(r"動画(内|では|の中で|で示|で紹介|で語ら|の冒頭)", r"元記事\1", text)
        for a, b in VIDEO_FIX.items():
            text = text.replace(a, b)
        record[key] = text


def title_warnings(record: dict) -> list[str]:
    """見出しの警告（不合格にはしない）。40字を超えると一覧・カードで前半だけになる（scripts/headline_normalize.py）"""
    return headline_normalize.title_warnings(record)


def evaluate(record: dict, source_text: str, kind: str, context: str = "", check_facts: bool = True) -> dict:
    """{ok, soft, score, problems, structural, fact, readability, readability_problems, warnings}。warnings は不合格にしない注意（見出しの長さ）。形で落ちたときは事実確認を省く（API の節約）。
    soft=True は「形と事実は合格、読みやすさだけが基準外」。polish() で文を分け、だめでも受理してよい。
    形・事実の不合格のときの problems には読みやすさを混ぜない（作り直しで本文が縮み、長さで落ちるのを防ぐ）"""
    fix_video_leftover(record)
    # 文体の決まりのうちプログラムで直せるもの（「元記事」の言い換え・種類に合わない判定カード。scripts/article_style.py）
    article_style.autofix(record)
    # 中身のない章・事実を含まないつなぎの文・中の人の注記は、プログラムで先に消す（scripts/section_gate.py。表示の段と同じ規則）
    section_gate.apply(record)
    read = readability(record.get("body") or "")
    read_problems = readability_problems("", read)
    base = {"structural": [], "fact": None, "readability": read, "readability_problems": read_problems,
            "warnings": title_warnings(record) + section_gate.style_warnings(record) + article_style.warnings(record)}
    structural = structural_problems(record, kind, source_text, context)
    if record.get("sourceType") == "web" and not (source_text or "").strip():
        structural.append("一次資料が空のため記事の内容を確認できません")
    if structural:
        return {**base, "ok": False, "soft": False, "score": None, "problems": structural, "structural": structural}
    if not check_facts:
        return {**base, "ok": not read_problems, "soft": bool(read_problems), "score": None, "problems": read_problems}
    try:
        fact = fact_check(record, source_text, context, require_usefulness=True) if record.get("sourceType") == "web" else fact_check(record, source_text, context)
    except Exception as error:  # noqa: BLE001 - 確認できない記事は出さない
        problem = f"事実確認を実行できなかった（{str(error)[:80]}）"
        return {**base, "ok": False, "soft": False, "score": None, "problems": [problem], "unavailable": True}
    fact_ok = fact["grounded"] and fact["score"] >= PASS_SCORE and fact.get("useful", True)
    problems = fact["problems"] if not fact_ok else read_problems
    if not fact_ok and not problems:
        problems = ["記事の有用性を確認できなかった"] if fact.get("useful") is False else [f"元記事との整合性が低い（score {fact['score']}）"]
    return {**base, "ok": fact_ok and not read_problems, "soft": fact_ok and bool(read_problems), "score": fact["score"],
            "problems": problems, "fact": fact}


POLISH_SCHEMA = {
    "type": "OBJECT",
    "properties": {"body_markdown": {"type": "STRING"}},
    "required": ["body_markdown"],
}

POLISH_SYSTEM = """あなたはニュースサイトの編集者です。記者の原稿を、中身を変えずにスマホで読みやすい文に整えます。
出力は指定の JSON だけ。"""

POLISH_PROMPT = """次の記事本文（Markdown）を、中身を変えずに読みやすく整えてください。

# 今の原稿の数値
{problems}

# 直し方
- 1文は60字までを目安にする。長い文は分けるが、読点は1文に2つまで使ってよい。「〜があります。〇〇です。」のように1つの文を2つに割らない。
  例:「AはBを発表し、Cができるようになったことで、Dの手間が減ると期待されています。」
  →「AはBを発表しました。Cができるようになり、Dの手間が減ります。」
- 1段落は1〜3文。段落の間には空行を入れる。
- 文中に数字が3つ以上並ぶところは箇条書きにする。
- 飾りの誇張語（圧倒的・極限・劇的・強烈 など）は普通の言葉にする。

# 変えてはいけないもの
- 見出し（## と ###）の文言・数・順番。表・リンク・既存の箇条書き。
- 事実・数字・日付・価格・固有名詞・引用。情報を削らない。足さない。
- 文を足して水増ししない（今は{length}字。分けた結果、短くなってもよい）。中身の無いつなぎ（「〜にとっても重要です」「〜と言えます」）は足さない。

# 原稿
<<<
{body}
>>>"""

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_HEADING_RE = re.compile(r"^#{2,3} .+$", re.M)
_PROTECTED_HEADING_RE = re.compile(r"^## (概要|ひとことで言うと|自分に関係ある？|まとめ|これまでの流れ|続報)")


def _numbers(text: str) -> set[str]:
    """本文中の数字（リンク先と番号付きリストの番号は除く）"""
    text = re.sub(r"\]\([^)]*\)", "]", text or "")
    text = re.sub(r"^\s*\d+[.)]\s", "", text, flags=re.M)
    return set(_NUM_RE.findall(unicodedata.normalize("NFKC", text)))


def polish(record: dict, source_text: str, kind: str, context: str = "", check_facts: bool = True):
    """読みやすさだけ基準外の記事の本文を、文を分けるだけの書き直しにかける。
    (record, verdict) を返す。見出し・数字・長さが崩れたり、事実確認に落ちたりしたら None"""
    body = record.get("body") or ""
    length = _plain_len(body)
    problems = readability_problems(body)
    prompt = POLISH_PROMPT.format(problems="\n".join(f"- {p}" for p in problems), length=length, body=body)
    data, model = llm.generate_json(POLISH_SYSTEM, prompt, POLISH_SCHEMA, temperature=0.3, max_output_tokens=16384, tier=CHECK_TIER,
                                    purpose="helper")
    new_body = re.sub(r"^(#{1,6}) +(?:#{1,6} +)+", r"\1 ", str(data.get("body_markdown") or "").strip(), flags=re.M)
    # 見出しは数と階層を保つ。言い回しの手直し（誇張語を外す等）は許すが、決まった章名は変えさせない
    heads_old, heads_new = _HEADING_RE.findall(body), _HEADING_RE.findall(new_body)
    if [h.split(" ")[0] for h in heads_old] != [h.split(" ")[0] for h in heads_new]:
        return None, f"見出しの数・階層が変わった（{len(heads_old)}→{len(heads_new)}個）"
    for a, b in zip(heads_old, heads_new):
        if _PROTECTED_HEADING_RE.match(a) and _norm(a) != _norm(b):
            return None, f"決まった章名が変わった（{a}→{b}）"[:80]
    if _plain_len(new_body) < length * 0.7:
        return None, f"本文が縮んだ（{length}→{_plain_len(new_body)}字）"
    # 「3つあります」のような1桁の数は箇条書きにするときに増えるので見逃す（事実確認は後で通す）
    added = {n for n in _numbers(new_body) - _numbers(body) if len(n) > 1}
    if added:
        return None, f"元に無い数字が増えた（{', '.join(sorted(added)[:5])}）"
    if len(_numbers(body) - _numbers(new_body)) > 2:
        return None, "数字が抜けた"
    polished = {**record, "body": new_body}
    verdict = evaluate(polished, source_text, kind, context, check_facts=check_facts)
    if not (verdict["ok"] or verdict.get("soft")):
        return None, "書き直し後の確認に不合格: " + "; ".join(verdict["problems"][:2])
    before, after = readability(body), verdict["readability"]
    improved = (
        len(verdict["readability_problems"]) < len(problems)
        or after["avg_sentence"] <= before["avg_sentence"] - 3
        or after["avg_paragraph"] <= before["avg_paragraph"] * 0.85
    )
    if not improved:
        return None, f"読みやすさが改善しなかった（{before['avg_sentence']}→{after['avg_sentence']}字）"
    verdict["polished_by"] = model
    return polished, verdict


SURGICAL_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "lede": {"type": "STRING"},
        "highlights": {"type": "ARRAY", "items": {"type": "STRING"}},
        "body_markdown": {"type": "STRING"},
        "easy_markdown": {"type": "STRING"},
        "work_money_points": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["title", "lede", "highlights", "body_markdown", "easy_markdown", "work_money_points"],
}

SURGICAL_SYSTEM = """あなたはニュースサイトのデスクです。校閲で指摘された箇所だけを、最小限の手直しで直します。
出力は指定の JSON だけ。"""

SURGICAL_PROMPT = """校閲記者が次の記事に指摘をしました。指摘された箇所だけを直してください。

# 校閲の指摘
{problems}

# 直し方
- 指摘された文（または文の一部）だけを直す。元記事に無い事実・言い過ぎは削るか、元記事どおりの弱い言い方に直す。
  訳語の指摘は正しい訳語に直す。直せないときはその文を削る。
- 指摘されていない文・見出し（## と ###）・表・リンク・数字は一字も変えない。
- 新しい事実・数字・日付・固有名詞を足さない。
- タイトル・リード・要点（highlights）・かんたん版・「仕事・お金にどう活かせる」（work_money_points）も、指摘があればそこだけ直し、
  なければそのまま返す（元の記事に無ければ空配列）。直せない項目は削る。

# 元記事
<<<
{source}
>>>

# 記事
タイトル: {title}
リード: {lede}
要点: {highlights}
仕事・お金にどう活かせる: {work_money}

本文:
<<<
{body}
>>>

かんたん版:
<<<
{easy}
>>>"""


def surgical_ok(verdict: dict) -> bool:
    """外科的な修正に回せる不合格か（形のチェックは通り、事実確認が致命的でなく SURGICAL_MIN_SCORE 点以上）"""
    fact = verdict.get("fact") or {}
    return bool(fact) and not verdict.get("structural") and not fact.get("fatal") and fact.get("useful", True) and fact.get("score", 0) >= SURGICAL_MIN_SCORE


def surgical_fix(record: dict, source_text: str, kind: str, problems: list[str], context: str = "", check_facts: bool = True):
    """大きなニュースの細かい指摘（言い過ぎ・訳語・補足の程度）を、指摘された文だけ直して受理する。
    (record, verdict) か (None, 理由)。直したあとも致命的でなく SURGICAL_MIN_SCORE 点以上なら、残った細かい指摘は受理する"""
    body = record.get("body") or ""
    prompt = SURGICAL_PROMPT.format(
        problems="\n".join(f"- {p}" for p in problems[:8]),
        source=(source_text or "")[:SOURCE_CAP] + (f"\n\n# 確認済みの文脈（ここにある事実・計算済みの数字も使ってよい）\n{context[:CONTEXT_CAP]}" if context.strip() else ""),
        title=record.get("title", ""), lede=record.get("lede", ""), highlights=" / ".join(record.get("highlights") or []),
        work_money=" / ".join((record.get("workMoney") or {}).get("points") or []) or "（なし）",
        body=body, easy=record.get("easy", ""),
    )
    # 事実確認の指摘の手直しは小さいが質が大事な段: Claude API の Haiku（予算の範囲。だめなら無料モデル）
    data, model = llm.generate_json(SURGICAL_SYSTEM, prompt, SURGICAL_SCHEMA, temperature=0.2, max_output_tokens=16384, tier=CHECK_TIER,
                                    purpose="helper")
    new_body = re.sub(r"^(#{1,6}) +(?:#{1,6} +)+", r"\1 ", str(data.get("body_markdown") or "").strip(), flags=re.M)
    heads_old, heads_new = _HEADING_RE.findall(body), _HEADING_RE.findall(new_body)
    if [_norm(h) for h in heads_old] != [_norm(h) for h in heads_new]:
        return None, f"見出しが変わった（{len(heads_old)}→{len(heads_new)}個）"
    if _plain_len(new_body) < _plain_len(body) * 0.85:
        return None, f"本文を削りすぎた（{_plain_len(body)}→{_plain_len(new_body)}字）"
    added = {n for n in _numbers(new_body) - _numbers(body) - _numbers(source_text) - _numbers(context) if len(n) > 1}
    if added:
        return None, f"元に無い数字が増えた（{', '.join(sorted(added)[:5])}）"
    fixed = {**record, "body": new_body}
    for key, field in (("title", "title"), ("lede", "lede"), ("easy_markdown", "easy")):
        value = str(data.get(key) or "").strip()
        if value:
            fixed[field] = value
    highlights = [str(h).strip() for h in data.get("highlights") or [] if str(h).strip()]
    if highlights:
        fixed["highlights"] = highlights[:3]
    if record.get("workMoney"):
        # 指摘で全部削られたら欄ごと無くなり、形のチェックで不合格になる（緩めない）
        points = [str(p).strip() for p in data.get("work_money_points") or [] if str(p).strip()]
        fixed["workMoney"] = persona.clean_work_money({**record["workMoney"], "points": points})
    verdict = evaluate(fixed, source_text, kind, context, check_facts=check_facts)
    verdict["fixed_by"] = model
    if verdict["ok"] or verdict.get("soft"):
        return fixed, verdict
    if MAJOR and surgical_ok(verdict):
        # 残りは細かい指摘だけ（致命的な誤りなし・点数も基準以上）。大きなニュースは落とさない
        return fixed, {**verdict, "ok": True, "accepted_minor": verdict.get("problems") or []}
    return None, "直したあとの確認に不合格: " + "; ".join(verdict.get("problems", [])[:2])


def feedback_block(problems: list[str]) -> str:
    """作り直しのときにプロンプト末尾へ足す指示"""
    lines = "\n".join(f"- {p}" for p in problems[:10])
    return (
        "\n\n# 前回の原稿で見つかった問題（必ず直すこと）\n"
        f"{lines}\n"
        "- 元記事に無い数値・日付・価格・発言は削る。足りない分は元記事の事実の説明を厚くして補い、推測で埋めない。"
    )
