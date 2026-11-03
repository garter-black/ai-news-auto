#!/usr/bin/env python3
"""SNS 動画 v3 の「中身に合わせた型」（2026-11-03・オーナーの承認待ち。既定は使わない）。

  python3 scripts/video_templates.py <slug>                    # どの型になるかを表示するだけ
  python3 scripts/video_templates.py <slug> --render [--template security] [--out DIR]
                                                               # 見本を書き出す（投稿しない・KV にも置かない）

ニュースの種類ごとに、右の画面のカードを変える。声（VOICEVOX 九州そら）・英語のページの日本語の吹き出し・
日本語のページの選択の青・落ち着いたカメラ・最後に記事のリンクへ温かく誘う締めは、承認済みの shorts_v3 のまま。

| 型 | 選ぶ条件（記事 JSON だけで決める・LLM なし） | 右の画面に足すもの |
|---|---|---|
| launch（発表） | 発表・公開・提供開始などの語 ＋ 公式の写真か「前と何が違う？」の小見出し | 公式発表の写真（ゆっくり寄る・「画像：◯◯（公式発表より）」）と主な特長のカード |
| pricing（料金） | 料金・月額・プランなどの語 ＋ 料金の行（記事の「料金は？」）か公式の料金ページ | 料金の段階のグラフ（台本の ladder。無ければ記事の料金の行から） |
| comparison（比較） | 記事に本物の比較データ（charts の rivals）がある（A7） | 横に並べた比較（主役と相手・同じ単位の棒） |
| security（セキュリティ） | 脆弱性・不正利用・攻撃などの語 ＋ 影響・対象・今することのうち2つ | 影響・対象・今すること のチェックリスト |
| policy_money（お金・制度） | 買収・投資・規制・予算などの語 ＋ 誰が・何を・いつ・いくら のうち「いつ」か「いくら」 | 誰が・何を・いつ・いくら の流れ |
| earnings（決算） | 決算・売上高・営業利益などの語 ＋ 記事に数字が2つ以上 | 主な数字のカード |
| breaking（速報） | 記事の breaking ＝ true（上の4つ＝データの型に当たらないとき） | SNS 用は 15〜25秒の短い版（答え1つ＋締め） |
| default | どれにも当たらない | 今の承認済みの型のまま |

切り替え: 環境変数 SNS_VIDEO_TEMPLATES=on のときだけ shorts_v3.make_versions がこの型を使う（既定 off ＝今のまま）。
見本は --template（またはこのファイルの --render）で作る。

決まり（docs/owner_rules.md）:
- I1/I2: 写真は記事の imageCredit が official（公式発表の写真）のときだけ。AI 画像・素材写真・ニュースサイトの写真は使わない。
  公式の動画のコマは、利用の条件が記事ごとに確かめられていないので使わない（写真と公式ページの画面だけ）。
- A7: 比較は記事の charts（rivals.py が公式ページの引用で確かめた数字）だけ。作った比較は描かない。
- 描く数字はすべて記事 JSON にある（品質ゲート video_gate.check_v3 が rendered_strings を記事と照合する）。
- 飾りの線・帯・枠は描かない（video_gate.lint_renderer がこのファイルも検査する。棒・箱は data_bars、強調は _highlight_* だけ）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

ENV = "SNS_VIDEO_TEMPLATES"
APPROVED = "default"  # オーナーが承認した今の型（shorts_v3 そのまま）
TEMPLATES = ("launch", "pricing", "comparison", "security", "policy_money", "earnings", "breaking", "default")
DATA_FIRST = ("security", "earnings", "comparison", "pricing")  # 速報でも、記事に形のあるデータがあればこちら
NAMES = {
    "launch": "発表（公式の写真と特長）",
    "pricing": "料金（料金の段階）",
    "comparison": "比較（横に並べる）",
    "security": "セキュリティ（影響・対象・今すること）",
    "policy_money": "お金・制度（誰が・いつ・いくら）",
    "earnings": "決算（主な数字）",
    "breaking": "速報（15〜25秒）",
    "default": "今の型（承認済み）",
}
# 速報の SNS 版の長さ（2026-11-03 オーナー「速報は短く、パンチのある15〜25秒」）
BREAKING_RANGE = (15.0, 25.0)
# 速報の画面の変化の下限（今の型は10回。場面が1〜2つの15〜25秒では6回が同じテンポ。1場面の平均・最長の基準はそのまま）
BREAKING_MIN_CHANGES = 6
CARD_MODES = ("image", "specs", "checklist", "timeline", "figures", "versus")


def enabled(env: dict | None = None) -> bool:
    """新しい型を本番で使うか（既定 off。オーナーの承認後に GitHub の変数で on にする）"""
    return str((env if env is not None else os.environ).get(ENV, "off")).strip().lower() in ("on", "1", "true", "yes")


# --------------------------------------------------------------------------- #
# 1. 記事 JSON から読む（ネット・LLM なし）
# --------------------------------------------------------------------------- #


def section(body: str, head: str) -> str:
    """本文の「## 見出し」の中身（見出しの頭が head で始まる最初の節。### の小見出しは中に含む）"""
    m = re.search(r"^## " + re.escape(head) + r"[^\n]*\n(.*?)(?=^## |\Z)", body or "", re.M | re.S)
    return m.group(1).strip() if m else ""


def bullets(text: str) -> list[tuple[str, str]]:
    """箇条書きの「- 名前：値」を (名前, 値) に。区切りの無い行は (行, "")"""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*[-*・]\s+(.+?)\s*$", line)
        if not m:
            continue
        item = re.sub(r"\*\*", "", m.group(1)).strip()
        parts = re.split(r"\s*[：:]\s*", item, maxsplit=1)
        out.append((parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""))
    return out


def table_rows(body: str) -> list[list[str]]:
    """本文の表（| 項目 | 内容 |）の行（見出しと区切りの行は除く）"""
    rows = []
    for line in (body or "").splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [re.sub(r"\*\*", "", c).strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows[1:] if rows and not any(_has_digit(c) for c in rows[0]) else rows


def _has_digit(text: str) -> bool:
    return bool(re.search(r"[0-9０-９]", text or ""))


def _short(text: str, limit: int) -> str:
    """カードの1行: 文末の言い回しを落とし、limit 字を超えたら読点で切る（切れなければ空＝使わない）"""
    t = re.sub(r"\s+", "", (text or "").strip())
    t = re.sub(r"[（(][^）)]*[）)]$", "", t)
    tails = [("することが重要です", ""), ("することが求められます", ""), ("ことが求められます", ""), ("ことが重要です", ""), ("が求められます", ""),
             ("しましょう", ""), ("してください", ""), ("とされています", ""), ("としています", ""), ("されています", ""), ("しています", ""),
             ("ています", "ている"), ("できます", "できる"), ("されました", "された"), ("ました", "た"), ("でした", "だった"), ("です", ""), ("ます", "")]  # fmt: skip
    for tail, repl in tails:
        if re.search(tail + r"[。．.]?$", t):
            t = re.sub(tail + r"[。．.]?$", repl, t)
            break
    t = re.sub(r"^(また|さらに|そのため|一方で|ただし|なお|特に)、", "", t.rstrip("。．.、"))
    if len(t) > limit:
        cut = t[:limit].rfind("、")
        t = t[:cut] if cut >= 8 else ""
    if t and re.search(r"[はがをにとでもの]$", t):  # 文の途中で切れた（「〜は」で終わる）ものは使わない
        return ""
    return t


def sentences(text: str) -> list[str]:
    text = re.sub(r"^#+ .*$", "", text or "", flags=re.M)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.M)
    return [s.strip() for s in re.split(r"(?<=[。！？])\s*|\n+", text) if len(s.strip()) >= 6]


def figure_rows(article: dict, limit: int = 4) -> list[dict]:
    """主な数字: 本文の「数字で見ると…」の箇条書き（名前：値。値に数字がある行）。無ければ決算の文から"""
    rows = []
    for label, value in bullets(section(article.get("body", ""), "数字で見ると")):
        if value and _has_digit(value) and len(label) <= 18 and len(value) <= 16:
            rows.append({"label": label, "value": value})
    if len(rows) < 2:  # 表の「項目 | 数字（増減）」
        rows = []
        for cells in table_rows(article.get("body", "")):
            if len(cells) < 2 or not _has_digit(cells[1]) or len(cells[0]) > 14:
                continue
            m = re.match(r"^([^（(]+?)\s*[（(]([^）)]*)[）)]", cells[1])
            value, change = (m.group(1).strip(), m.group(2).strip()) if m else (cells[1], "")
            if len(value) <= 14 and re.search(_MONEY + "|" + _PCT, value):
                rows.append({"label": cells[0], "value": value, "change": change if len(change) <= 14 and _has_digit(change) else ""})
    if len(rows) < 2:
        rows += earnings_rows(article)
    seen, out = set(), []
    for r in rows:
        if r["value"] not in seen:
            seen.add(r["value"])
            out.append(r)
    return out[:limit]


_MONEY = r"(?:約)?[0-9０-９][0-9０-９,，.．]*(?:万|億|兆)?[0-9０-９]*(?:円|ドル|米ドル|ユーロ|元)"
_PCT = r"[0-9０-９][0-9０-９.．]*[%％]"
EARN_LABELS = ("売上高", "売上収益", "営業収益", "営業利益", "経常利益", "純利益", "最終利益", "最終損益", "1株当たり利益", "粗利益率", "営業利益率", "サービス売上")


def earnings_rows(article: dict) -> list[dict]:
    """決算の数字: 「売上高は1,145億ドル（12％増）」のような文から (指標, 金額＋増減)"""
    text = " ".join([article.get("lede", ""), " ".join(article.get("highlights") or []), article.get("body", "")])
    rows, seen = [], set()
    for label in EARN_LABELS:
        m = re.search(re.escape(label) + r"[^。\n]{0,14}?(" + _MONEY + r")", text)
        if not m or label in seen:
            continue
        seen.add(label)
        value = m.group(1)
        around = text[m.start(): m.end() + 16]
        pct = re.search(r"(?:前年(?:同期)?比)?" + _PCT + r"(?:の)?(?:増|減|上昇|低下)", around)
        rows.append({"label": label, "value": value, "change": (pct.group(0).replace("の", "") if pct else "")})
    return rows


def price_rows(article: dict) -> list[dict]:
    """料金の行: 本文の「料金は？」（無ければ「数字で見ると」）の箇条書きで、値に金額があるもの"""
    out = []
    for head in ("料金は", "数字で見ると", "スペックと価格"):
        for name, value in bullets(section(article.get("body", ""), head)):
            m = re.search(r"(?:月額|毎月|年額|月)?\s*" + _MONEY + r"(?:/月|／月)?", value)
            if not m or not name or len(name) > 16 or name.startswith(("最大", "最小", "合計")):
                continue
            price = m.group(0).strip()
            if len(price) > 12:
                continue
            num = re.sub(r"[^0-9.]", "", re.search(r"[0-9０-９][0-9０-９,，.．]*", price).group(0).translate(str.maketrans("０１２３４５６７８９，．", "0123456789,.")).replace(",", ""))
            out.append({"name": name, "price": price, "usage": "", "mult": num, "block": "", "target": ""})
        if len(out) >= 2:
            break
        out = []
    seen, rows = set(), []
    for r in out:
        if r["name"] not in seen:
            seen.add(r["name"])
            rows.append(r)
    return rows[:5]


def rival_groups(article: dict) -> dict | None:
    """比較のデータ（A7: 記事の charts の rivals だけ。rivals.py が公式ページの引用で確かめた数字）"""
    for ch in article.get("charts") or []:
        if not isinstance(ch, dict) or ch.get("kind") != "rivals":
            continue
        groups = []
        for g in ch.get("groups") or []:
            items = [it for it in g.get("items") or [] if isinstance(it.get("value"), (int, float)) and it.get("display") and it.get("label")]
            if len(items) >= 2:
                groups.append({"label": g.get("label", ""), "unit": g.get("unit", ""), "items": items[:3]})
        if groups:
            src = ch.get("source") or {}
            return {"title": ch.get("title") or "比べると", "source": src.get("name") or "各社の公式ページ", "groups": groups[:3]}
    return None


def feature_lines(article: dict, limit: int = 3) -> list[str]:
    """主な特長: 「前と何が違う？」（無ければ製品の節）の ### 小見出し（短いものだけ）"""
    body = article.get("body", "")
    for head in ("前と何が違う", "どんな製品", "何が変わった", "主な特長", "特長"):
        sec = section(body, head)
        lines = [re.sub(r"\*\*", "", h).strip() for h in re.findall(r"^### (.+)$", sec, re.M)]
        lines = [h for h in lines if 4 <= len(h) <= 24]
        if len(lines) >= 2:
            return lines[:limit]
    return []


def official_image(article: dict) -> dict | None:
    """公式発表の写真（I1/I2）: imageCredit が official のときだけ。サイトに保存した写し（thumbnail）を優先"""
    credit = article.get("imageCredit") or {}
    if credit.get("kind") != "official":
        return None
    thumb = article.get("thumbnail") or ""
    url = thumb if "ai-tech-express.jp/img/" in thumb else credit.get("original") or ""
    if not url:
        return None
    org = (credit.get("org") or credit.get("provider") or "").strip()
    focus = article.get("imageFocus") or {}
    return {"url": url, "org": org, "credit": f"画像：{org}（公式発表より）" if org else "画像：公式発表より", "focus": [float(focus.get("x", 0.5)), float(focus.get("y", 0.5))]}


IMPACT = re.compile(r"(課金が発生|料金が発生|被害額|損害|乗っ取られ|盗まれ|漏えい|漏洩|流出|侵入され|悪用され|改ざん|数分|数百万|スピード)")
AFFECTED = re.compile(r"(対象|バージョン|以前の版|v\d|利用者|ユーザー|企業|組織|分野|セクター|業種|地域)")
REPORTING = re.compile(r"(警告し|発表し|報告を行|報告し|公表し|明らかにし)")
ACTION = re.compile(r"(確認|更新|アップデート|見直|徹底|管理|無効|変更|点検|監視|強化|分離|有効に)")


def security_rows(article: dict) -> list[dict]:
    """影響・対象・今すること（それぞれ記事の1文を短くしたもの。今することは todayActions を優先）"""
    body = re.sub(r"^## (これまでの流れ|続報)[^\n]*\n.*?(?=^## |\Z)", "", article.get("body", ""), flags=re.M | re.S)  # 年表は使わない
    pool = sentences(" ".join([article.get("lede", ""), body]))
    used: set[str] = set()

    def pick(pattern: re.Pattern, extra: re.Pattern | None = None, src: list[str] | None = None) -> str:
        for s in src if src is not None else pool:
            if s in used or not pattern.search(s) or (extra is not None and not extra.search(s)):
                continue
            if src is None and REPORTING.search(s):  # 「◯◯は〜と警告しました」のような発表の文は中身ではない
                continue
            short = _short(s, 44)
            if short and len(short) >= 8:
                used.add(s)
                return short
        return ""

    rows = []
    if v := pick(AFFECTED, re.compile(r"(被害|影響|対象|狙)")):
        rows.append({"label": "対象", "text": v})
    if v := pick(IMPACT):
        rows.append({"label": "影響", "text": v})
    acts = [a for a in article.get("todayActions") or [] if isinstance(a, str)]
    tail = sentences(" ".join(section(body, h) for h in ("まとめ", "自分にはどう関係", "自分に関係", "日本で使える")))
    if v := pick(ACTION, None, acts + tail):
        rows.append({"label": "今すること", "text": v})
    order = {"影響": 0, "対象": 1, "今すること": 2}
    return sorted(rows, key=lambda r: order[r["label"]])


_WHEN = re.compile(r"(\d{4}会計年度(?:の)?第\d四半期|\d{4}年\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月|\d{4}年度|\d{1,2}月\d{1,2}日|\d{4}年(?:までに)?|\d+カ月)")


def _main_name(article: dict) -> str:
    """主役の会社名（shorts_v2.main_name。numpy が無い所では空＝題の頭だけで決める）"""
    try:
        import shorts_v2 as v2

        return (v2.main_name(article) or "").strip()
    except ImportError:
        return ""


def policy_rows(article: dict) -> list[dict]:
    """誰が・何を・いつ・いくら（いつ・いくら は記事の表記どおり。いくら は「数字で見ると」の行を優先）"""
    title = article.get("title", "")
    rows = []
    m0 = re.match(r"^([^、「」　]{2,20})、", title)  # 題の頭「Salesforce、…」
    who = m0.group(1) if m0 else _main_name(article)
    who = "" if who in ("その他",) else who
    if who:
        rows.append({"label": "誰が", "text": who})
    m = re.match(r"^[^、]{1,24}、(.+?)(?:　|$)", title)
    if m and 6 <= len(m.group(1)) <= 26:
        rows.append({"label": "何を", "text": m.group(1)})
    text = " ".join([article.get("lede", ""), " ".join(article.get("highlights") or []), article.get("body", "")[:4000]])
    if w := _WHEN.search(text):
        rows.append({"label": "いつ", "text": w.group(1)})
    money = None
    for label, value in bullets(section(article.get("body", ""), "数字で見ると")):
        if re.search(_MONEY, value) and len(value) <= 16:
            money = {"label": "いくら", "text": re.search(_MONEY, value).group(0), "note": label[:16]}
            break
    if money is None and (mm := re.search(_MONEY, text)):
        money = {"label": "いくら", "text": mm.group(0), "note": ""}
    if money:
        rows.append(money)
    return rows


# --------------------------------------------------------------------------- #
# 2. 型を選ぶ（決まった規則だけ・LLM なし）
# --------------------------------------------------------------------------- #

WORDS = {
    "security": ["脆弱性", "脆弱", "セキュリティ更新", "パッチ", "CVE", "不正アクセス", "情報漏えい", "情報漏洩", "漏えい", "マルウェア", "ランサム",
                 "サイバー攻撃", "攻撃者", "フィッシング", "不正利用", "ゼロデイ", "悪用", "乗っ取", "警告", "注意喚起", "ハッキング", "侵害"],
    "earnings": ["決算", "売上高", "営業利益", "純利益", "最終利益", "増収", "減収", "増益", "減益", "四半期", "通期", "業績", "上方修正", "下方修正", "黒字", "赤字"],
    "pricing": ["料金", "価格", "値上げ", "値下げ", "月額", "年額", "無料枠", "利用枠", "クレジット", "プラン", "円/月", "ドル/月", "据え置"],
    "comparison": ["比較", "比べ", "vs", "VS", "対決", "上回", "下回", "前モデル", "従来比", "ベンチマーク", "ライバル"],
    "policy_money": ["買収", "出資", "投資", "資金調達", "調達", "上場", "IPO", "規制", "法案", "法律", "政府", "財務省", "総務省", "経産省", "補助", "助成",
                     "予算", "税", "方針", "制度", "合意", "億ドル", "億円", "兆円", "報奨金"],
    "launch": ["発表", "公開", "提供開始", "発売", "リリース", "登場", "新モデル", "新製品", "搭載", "投入", "一般提供", "ローンチ"],
}
# 型ごとの最低点（題に1語=3点、題以外の要約・タグ・キーワードに1語=1点）
THRESHOLD = {"security": 3, "earnings": 3, "pricing": 3, "comparison": 3, "policy_money": 3, "launch": 3}


# shorts_v3.PRODUCT_PAGES の製品（公式の料金ページを撮る）。numpy なしで型を選べるようにここにも持つ（テストで一致を確かめる）
PRICING_PRODUCTS = re.compile(r"Claude|ChatGPT|Codex|Gemini", re.I)


def scores(article: dict) -> dict[str, int]:
    title = article.get("title", "")
    rest = " ".join([article.get("lede", ""), article.get("description", ""), " ".join(article.get("highlights") or []),
                     " ".join(article.get("tags") or []), " ".join(article.get("keywords") or [])])  # fmt: skip
    out = {}
    for name, words in WORDS.items():
        out[name] = sum(3 for w in words if w in title) + sum(1 for w in words if w in rest)
    if rival_groups(article):  # 本物の比較データがある（A7）
        out["comparison"] += 3
    return out


def eligible(name: str, article: dict) -> bool:
    """その型のカードに使う本物のデータが記事にあるか（無ければ選ばない）"""
    if name == "security":
        return len(security_rows(article)) >= 2
    if name == "earnings":
        return len(figure_rows(article)) >= 2 and any(w in article.get("title", "") + article.get("lede", "") for w in ("決算", "売上高", "営業利益", "純利益", "四半期", "通期", "業績"))
    if name == "comparison":
        return rival_groups(article) is not None
    if name == "pricing":
        if len(price_rows(article)) >= 2:
            return True
        who = " ".join([article.get("title", ""), " ".join(article.get("tags") or [])])
        return bool(PRICING_PRODUCTS.search(who))  # 公式の料金ページを撮れる製品（台本の ladder で料金の段階を作る）
    if name == "policy_money":
        rows = {r["label"] for r in policy_rows(article)}
        return "誰が" in rows and ("いつ" in rows or "いくら" in rows) and len(rows) >= 3
    if name == "launch":
        return official_image(article) is not None or len(feature_lines(article)) >= 2
    return name == "default"


def template_for(article: dict) -> str:
    """記事の中身から動画の型を1つ選ぶ（決まった規則だけ。同じ記事なら必ず同じ答え）。
    1) データの型（セキュリティ・決算・比較・料金）で、点が最低点以上・データがある → いちばん点の高いもの
    2) 速報（breaking）→ breaking  3) お金・制度・発表 → 点の高いもの  4) どれでもない → default"""
    sc = scores(article)

    def best(names: tuple) -> str | None:
        ok = [n for n in names if sc.get(n, 0) >= THRESHOLD[n] and eligible(n, article)]
        return max(ok, key=lambda n: (sc[n], -names.index(n))) if ok else None

    if found := best(DATA_FIRST):
        return found
    if article.get("breaking"):
        return "breaking"
    return best(("policy_money", "launch")) or "default"


# --------------------------------------------------------------------------- #
# 3. 台本にカードを足す（台本の LLM は1回だけ。カードの中身は記事 JSON から決まる）
# --------------------------------------------------------------------------- #

# 台本の LLM への一言（型ごと）。数字・事実の決まりは今の台本と同じ（script_problems が検査する）
NOTES = {
    "launch": "\n\n# この動画の型: 発表\n- 製品・サービスの発表。比較の beat は「前と何が違う？」の特長を順に言う（主な特長のカードを右の画面に出す）。",
    "pricing": "\n\n# この動画の型: 料金\n- 料金・プランの変更。比較の beat では、資料に料金の行が2つ以上あれば必ず料金の段階（ladder）を使う。",
    "comparison": "\n\n# この動画の型: 比較\n- 比較の beat では、記事の「同じクラスの製品との比較」にある数字だけで並べて言う（右の画面に横並びの比較を出す）。他の数字は足さない。",
    "security": "\n\n# この動画の型: セキュリティ\n- beats に「影響（何が起きるか）」「対象（誰・どこが）」「今すること（具体的な確認・対策）」を必ず入れる。煽らない・怖がらせない。",
    "policy_money": "\n\n# この動画の型: お金・制度\n- 答えの beat で「誰が・何を・いつ・いくら」を記事の表記どおりに言う（無いものは言わない）。",
    "earnings": "\n\n# この動画の型: 決算\n- 答えの beat で主な数字（売上高・利益など）を記事の表記どおりに言い、次の beat でその理由（何が伸びたか）を言う。",
}


def used_beats(script: dict) -> set[int]:
    out = set()
    for key in ("ladder", "compare", "units"):
        obj = script.get(key) or {}
        if int(obj.get("beat") or 0) and (obj.get("rows") or obj.get("ratio")):
            out.add(int(obj["beat"]))
    for key in ("ladders", "flows", "cards"):
        out |= {int(x.get("beat") or 0) for x in script.get(key) or [] if int(x.get("beat") or 0)}
    return out


def _beat_like(script: dict, pattern: str, taken: set[int], default: int | None) -> int:
    beats = script.get("beats") or []
    for i, b in enumerate(beats, 1):
        if i not in taken and re.search(pattern, (b.get("label") or "") + (b.get("keyword") or "")):
            return i
    if default and 1 <= default <= len(beats) and default not in taken:
        return default
    return next((i for i in range(1, len(beats) + 1) if i not in taken), 0)


def decorate(template: str, article: dict, script: dict, image_path: str = "") -> dict:
    """台本（full か short）に、型のカードを足した写しを返す。default と breaking はカードを足さない"""
    s = json.loads(json.dumps(script, ensure_ascii=False))
    s["_template"] = template
    if template in ("default", "breaking"):
        return s
    taken = used_beats(s)
    cards = list(s.get("cards") or [])

    def add(card: dict, beat: int) -> None:
        if beat:
            card["beat"] = beat
            cards.append(card)
            taken.add(beat)

    if template == "launch":
        img = official_image(article)
        if img and image_path:
            add({"mode": "image", "title": "公式発表の写真", "source": img["credit"], "rows": [], "image": image_path, "focus": img["focus"]}, _beat_like(s, r"答え|何が", taken, 1))
            s["_image_credit"] = img["credit"]
        feats = feature_lines(article)
        if len(feats) >= 2:
            add({"mode": "specs", "title": "主な特長", "source": "記事より", "rows": [{"name": x} for x in feats]}, _beat_like(s, r"比較|違い|特長|変わ", taken, 2))
    elif template == "pricing":
        if not (s.get("ladder") or {}).get("beat") and len(rows := price_rows(article)) >= 2:
            beat = _beat_like(s, r"比較|料金|プラン|段階", taken, 2)
            if beat:
                s["ladder"] = {"beat": beat, "subject": rows[-1]["name"], "rows": rows, "title": "料金", "source": "記事より"}
                taken.add(beat)
    elif template == "comparison":
        g = rival_groups(article)
        if g:
            add({"mode": "versus", "title": g["title"], "source": g["source"] + "より", "rows": g["groups"]}, _beat_like(s, r"比較|比べ|違い|ライバル", taken, 2))
    elif template == "security":
        rows = security_rows(article)
        if len(rows) >= 2:
            add({"mode": "checklist", "title": "影響・対象・今すること", "source": "記事より", "rows": rows}, _beat_like(s, r"今すること|対策|確かめ|使い方|確認", taken, len(s.get("beats") or [])))
    elif template == "policy_money":
        rows = policy_rows(article)
        if len(rows) >= 3:
            add({"mode": "timeline", "title": "誰が・いつ・いくら", "source": "記事より", "rows": rows}, _beat_like(s, r"答え", taken, 1))
    elif template == "earnings":
        rows = figure_rows(article)
        if len(rows) >= 2:
            add({"mode": "figures", "title": "主な数字", "source": "記事より", "rows": rows}, _beat_like(s, r"答え|数字", taken, 1))
    if cards:
        s["cards"] = cards
    return s


# 速報の SNS 版の締め（短く温かく・投稿のリンクへ誘う。押しつけない）
BREAKING_ENDING = {"text": "くわしくは記事で", "speech": "続きは記事で。投稿のリンクから、ぜひチェックしてみてください。", "cta": "投稿のリンクからどうぞ ↗", "eyebrow": "続きは記事で"}


def breaking_script(script: dict) -> dict:
    """速報の SNS 版（15〜25秒）: つかみ → 答えの場面1つ（短ければ2つ）→ 締め。LLM は呼ばない（同じ台本から）"""
    beats = script.get("beats") or []
    picks = [int(i) for i in (script.get("short_beats") or []) if isinstance(i, (int, float)) and 1 <= int(i) <= len(beats)] or [1]
    picks = list(dict.fromkeys(picks))
    hook_len = len((script.get("hook") or {}).get("speech", ""))
    keep = [picks[0]]
    # 九州そら（速さ1.38）は約1秒5.5字。つかみ＋場面＋締め（31字）＋間で 25秒を超えないように、2つめは短いときだけ
    if len(picks) > 1 and hook_len + sum(len(beats[i - 1].get("speech", "")) for i in picks[:2]) + 31 <= 100:
        keep = sorted(picks[:2])
    remap = {old: new for new, old in enumerate(keep, 1)}
    out = json.loads(json.dumps(script, ensure_ascii=False))
    out["beats"] = [out["beats"][i - 1] for i in keep]
    for key in ("ladder", "compare", "units"):
        if key in out:
            obj = dict(out[key] or {})
            obj["beat"] = remap.get(int(obj.get("beat") or 0), 0)
            out[key] = obj
    for key in ("ladders", "flows", "cards"):
        if key in out:
            out[key] = [dict(x, beat=remap[int(x.get("beat") or 0)]) for x in out[key] if remap.get(int(x.get("beat") or 0))]
    out["conclusion"] = dict(BREAKING_ENDING)
    out["_variant"] = "short"
    out["_template"] = "breaking"
    out["_length_range"] = list(BREAKING_RANGE)
    out["_min_changes"] = BREAKING_MIN_CHANGES
    return out


def fetch_image(url: str, dest: Path) -> str:
    """公式の写真を手元に置く（サイトに保存した写し。失敗したら空＝写真のカードは出さない）"""
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1000:
        return str(dest)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (saisoku video template)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        if len(data) < 1000:
            return ""
        dest.write_bytes(data)
        return str(dest)
    except Exception:  # noqa: BLE001 - 写真が無くても動画は作る（画面だけ）
        return ""


def make_versions(slug: str, out_dir: Path, template: str, tts: str = "voicevox", reuse: bool = False, extra_pages: list[str] | None = None, **kw) -> dict:
    """型つきで2本（サイト用・SNS 用）。撮影・台本は shorts_v3 と同じ（台本は型の一言を足して1回だけ）。
    戻り値は shorts_v3.make_versions と同じ形 {"full": report, "short": report}。breaking の full は今の型と同じ台本"""
    import shorts
    import shorts_v3 as v3

    a = shorts.load_article(slug)
    note = NOTES.get(template, "")
    cache = f"{slug}.{template}.v3script.json" if note else f"{slug}.v3script.json"
    pages, script, reason = v3.prepare_versions(slug, a, out_dir, reuse, extra_pages, note=note, cache=cache)
    if not pages or not script:
        empty = {"slug": slug, "errors": [reason], "gate": {"ok": False, "reasons": [reason]}, "failReason": reason, "captureFails": list(v3.CAPTURE_FAILS), "template": template}
        return {"full": empty, "short": empty}
    image_path = ""
    if template == "launch" and (img := official_image(a)):
        image_path = fetch_image(img["url"], out_dir / "_images" / f"{slug}{Path(img['url'].split('?')[0]).suffix or '.jpg'}")
    full_s = decorate(template, a, script, image_path)
    short_s = breaking_script(script) if template == "breaking" else decorate(template, a, v3.short_script(script), image_path)
    full = v3.make_video(slug, out_dir, tts, profile=kw.get("profile"), article=a, pages=pages, script=full_s)
    short = v3.make_video(slug, out_dir, tts, profile=kw.get("profile"), article=a, pages=pages, script=short_s)
    return {"full": full, "short": short}


# --------------------------------------------------------------------------- #
# 4. カードの描画（shorts_v3.Renderer から呼ぶ。白いカード・墨の文字・青の強調・赤の数字・Noto Sans JP だけ）
# --------------------------------------------------------------------------- #


def card_base(r, L) -> dict:
    """カードの文字・写真を先に作る（毎フレームは位置と不透明度だけ変える）。r は shorts_v3.Renderer"""
    import numpy as np
    import shorts_v3 as v3

    PW, PH, X0 = v3.PW, v3.PH, r.LAD_X
    title = v3.render_text([(L.title, "bold", v3.BLUE)], 28, 0.06) if L.title else None
    src = v3.fit_line(L.source, "bold", range(22, 16, -2), PW - 2 * X0 - (title.shape[1] if title is not None else 0) - 24, color=v3.SOFT, accent=False) if L.source and L.mode != "image" else None
    info: dict = {"title": title, "src": src, "rows": []}
    if L.mode == "image":
        from PIL import Image

        img = np.asarray(Image.open(L.extra["image"]).convert("RGB"))
        box_w, box_h = PW - 2 * 40, PH - 130 - 110 - 80  # 写真はカードの幅いっぱい近くまで大きく
        h, w = img.shape[:2]
        scale = min(box_w / w, box_h / h)
        info.update({"img": img, "size": (int(w * scale), int(h * scale)), "focus": L.extra.get("focus") or [0.5, 0.5]})
        info["credit"] = v3.fit_line(L.source, "bold", range(26, 18, -2), PW - 2 * X0, color=v3.SOFT, accent=False)
        info["corner"] = _mask_photo_corner(18)
        return info
    if L.mode == "versus":
        groups = L.rows
        n = len(groups)
        g_h = min(300, (PH - 150 - 60) // max(n, 1))
        top = 150 + (PH - 150 - 60 - g_h * n) // 2  # 行が少ないときはカードの真ん中に
        names = []
        for g in groups:
            for it in g["items"]:
                if it["label"] not in names:
                    names.append(it["label"])
        subj = next((it["label"] for g in groups for it in g["items"] if it.get("highlight")), names[0])
        rows = []
        for k, g in enumerate(groups):
            vmax = max(float(it["value"]) for it in g["items"]) or 1.0
            items = []
            for it in g["items"]:
                on = it["label"] == subj
                items.append({
                    "name": v3.fit_line(it["label"], "bold", range(30, 18, -2), 360, color=v3.INK if on else v3.MUTED, accent=False),
                    "value": v3.render_text(v3.accent_segments(it["display"], "black", v3.INK) if on else [(it["display"], "black", v3.MUTED)], 40),
                    "frac": float(it["value"]) / vmax, "subj": on,
                })  # fmt: skip
            rows.append({"y": top + k * g_h, "h": g_h, "label": v3.render_text([(g["label"], "black", v3.BLUE)], 34), "items": items, "subj": False})
        info["rows"] = rows
        return info
    if L.mode == "figures":
        n = len(L.rows)
        cols = 2 if n >= 3 else n
        tile_w = (PW - 2 * X0 - 36 * (cols - 1)) // cols
        rows_n = (n + cols - 1) // cols
        tile_h = min(300, (PH - 170 - 60 - 36 * (rows_n - 1)) // rows_n)
        y_top = 150 + (PH - 150 - 60 - (tile_h * rows_n + 36 * (rows_n - 1))) // 2
        tiles = []
        for k, row in enumerate(L.rows):
            cx, cy = k % cols, k // cols
            value = v3.fit_line(row["value"], "black", range(96, 40, -4), tile_w - 60, color=v3.ACCENT, accent=False)
            parts = [v3.fit_line(row["label"], "bold", range(34, 22, -2), tile_w - 60, color=v3.INK, accent=False), value]
            if row.get("change"):
                parts.append(v3.fit_line(row["change"], "bold", range(32, 22, -2), tile_w - 60, color=v3.BLUE, accent=False))
            tiles.append({"x": X0 + cx * (tile_w + 36), "y": y_top + cy * (tile_h + 36), "w": tile_w, "h": tile_h, "sprite": v3.stack(parts, 14)})
        info.update({"tiles": tiles, "rows": [{"y": t["y"], "h": t["h"], "subj": k == 0} for k, t in enumerate(tiles)]})
        return info
    # specs / checklist / timeline: 縦に並んだ行（ラベル＋文）。今読んでいる行を薄い青に
    n = len(L.rows)
    row_h = min(190, (PH - 160 - 70) // max(n, 1))
    top = 160 + (PH - 160 - 70 - row_h * n) // 2  # 行が少ないときはカードの真ん中に
    label_w = 0 if L.mode == "specs" else 230
    text_x = X0 + (80 if L.mode == "specs" else label_w + (60 if L.mode == "timeline" else 0))
    width = PW - text_x - X0
    rows = []
    for k, row in enumerate(L.rows):
        if L.mode == "specs":
            head = v3.render_text([(chr(0x2460 + k), "black", v3.BLUE)], 46)
            body = v3.wrapped(row["name"], "black", range(50, 32, -2), width, 2, color=v3.INK)
        elif L.mode == "checklist":
            head = v3.render_text([(row["label"], "black", v3.BLUE)], 34)
            body = v3.wrapped(row["text"], "bold", range(40, 28, -2), width, 2, color=v3.INK)
        else:  # timeline
            head = v3.render_text([(row["label"], "black", v3.BLUE)], 34)
            parts = [v3.wrapped(row["text"], "black", range(54, 30, -2), width, 2, color=v3.INK)]
            if row.get("note"):
                parts.append(v3.fit_line(row["note"], "bold", range(28, 20, -2), width, color=v3.MUTED, accent=False))
            body = v3.stack(parts, 8)
        rows.append({"y": top + k * row_h, "h": row_h, "head": head, "body": body, "subj": False})
    info.update({"rows": rows, "text_x": text_x})
    return info


def _mask_photo_corner(r: int):
    """写真の角を丸くする不透明度（角の1つ分。マスクだけで線は描かない）"""
    import numpy as np

    yy, xx = np.mgrid[0:r, 0:r].astype(np.float32)
    d = np.sqrt((r - xx - 0.5) ** 2 + (r - yy - 0.5) ** 2)
    return np.clip(r - d + 0.5, 0, 1)


def _mask_round(photo, corner) -> None:
    """写真（RGB）の四隅を白へなじませる（カードの白に溶ける角丸）"""
    import numpy as np

    r = corner.shape[0]
    for fy in (False, True):
        for fx in (False, True):
            a = corner[::-1] if fy else corner
            a = a[:, ::-1] if fx else a
            ys = slice(photo.shape[0] - r, photo.shape[0]) if fy else slice(0, r)
            xs = slice(photo.shape[1] - r, photo.shape[1]) if fx else slice(0, r)
            photo[ys, xs] = (photo[ys, xs] * a[..., None] + 255 * (1 - a[..., None])).astype(np.uint8)


def card_frame(r, info: dict, L, t: float):
    """1フレームのカード（パネルの大きさの RGB）"""
    import numpy as np
    from PIL import Image
    import shorts_v3 as v3

    PW, PH, X0 = v3.PW, v3.PH, r.LAD_X
    arr = np.full((PH, PW, 3), 255, dtype=np.uint8)
    if info.get("title") is not None and L.mode != "image":  # 写真の見出しは写真の左端にそろえる（_image_frame）
        v3.blend(arr, info["title"], X0, 64)
    if info.get("src") is not None:
        v3.blend(arr, info["src"], X0 + info["title"].shape[1] + 24 if info.get("title") is not None else X0, 68)
    if L.mode == "image":
        return _image_frame(info, L, t, arr)
    if L.mode == "figures":
        n = len(info["tiles"])
        boxes, shown = [], []
        for k, tile in enumerate(info["tiles"]):
            age = t - (L.t0 + 0.3 + L.reveal * k)
            if age < 0:
                continue
            a = v3.ease_out(age / 0.4)
            dy = int((1 - a) * 24)
            boxes.append((tile["x"], tile["y"] + dy, tile["w"], v3.MARKER if a >= 0.5 else (236, 241, 249)))
            shown.append((tile, dy, a))
        img = Image.fromarray(arr)
        v3.data_bars(img, [(x, y, w, c) for x, y, w, c in boxes], info["tiles"][0]["h"], radius=22)
        arr = np.asarray(img).copy()
        for tile, dy, a in shown:
            spr = tile["sprite"]
            v3.blend(arr, spr, tile["x"] + 30, tile["y"] + (tile["h"] - spr.shape[0]) // 2 + dy, a)
        t_hi = L.t0 + 0.3 + L.reveal * n + 0.3
        return v3.push_in(arr, info, L, t, t_hi, 0.08)
    if L.mode == "versus":
        n = len(info["rows"])
        bars, labels = [], []
        bar_x = X0 + 380
        bar_max = PW - bar_x - X0 - 230
        for k, row in enumerate(info["rows"]):
            age = t - (L.t0 + 0.3 + L.reveal * k)
            if age < 0:
                continue
            a = v3.ease_out(age / 0.35)
            grow = v3.ease_out(age / 0.6)
            labels.append((row["label"], X0, row["y"] + 4, a))
            m = len(row["items"])
            step = min(84, (row["h"] - 70) // max(m, 1))
            for j, it in enumerate(row["items"]):
                y = row["y"] + 58 + j * step
                length = int(max(it["frac"], 0.05) * bar_max * grow)
                bars.append((bar_x, y + 8, length, v3.BLUE if it["subj"] else (204, 212, 225)))
                labels.append((it["name"], X0 + int((1 - a) * 60), y + 22 - it["name"].shape[0] // 2, a))
                labels.append((it["value"], bar_x + max(length, 40) + 18, y + 22 - it["value"].shape[0] // 2, a))
        img = Image.fromarray(arr)
        v3.data_bars(img, bars, 30)
        arr = np.asarray(img).copy()
        for spr, x, y, a in labels:
            v3.blend(arr, spr, x, y, a)
        t_hi = L.t0 + 0.3 + L.reveal * n + 0.3
        return v3.push_in(arr, info, L, t, t_hi, 0.06)
    # specs / checklist / timeline: 1行ずつ現れ、読み上げに合わせて強調の行が上から移る
    n = len(info["rows"])
    t_hi = L.t0 + 0.3 + L.reveal * n + 0.2
    focus_k = min(n - 1, int((t - t_hi) / L.tour)) if L.tour and t >= t_hi else (n - 1 if t >= t_hi else -1)
    dots = []
    shown = []
    for k, row in enumerate(info["rows"]):
        age = t - (L.t0 + 0.3 + L.reveal * k)
        if age < 0:
            continue
        a = v3.ease_out(age / 0.35)
        if k == focus_k:
            hi = v3.ease_out((t - t_hi - (focus_k * L.tour if L.tour else 0)) / 0.3)
            v3._highlight_row(arr, 30, row["y"] - 16, PW - 60, row["h"] - 18, hi)
        if L.mode == "timeline":  # 流れの点（データの印。線でつながない）
            dots.append((X0 + 200 + 8, row["y"] + 18, 26, v3.BLUE if k <= max(focus_k, 0) else (196, 205, 220)))
        shown.append((k, row, a))
    if dots:
        img = Image.fromarray(arr)
        v3.data_bars(img, [(x, y, 26, c) for x, y, _, c in dots], 26, radius=13)
        arr = np.asarray(img).copy()
    for k, row, a in shown:
        dx = int((1 - a) * 60)
        v3.blend(arr, row["head"], X0 + dx, row["y"] + 4, a)
        v3.blend(arr, row["body"], info["text_x"] + dx, row["y"], a)
    rows = [dict(r_, subj=(i == focus_k)) for i, r_ in enumerate(info["rows"])]
    return v3.push_in(arr, {**info, "rows": rows}, L, t, t_hi + (focus_k * L.tour if L.tour and focus_k > 0 else 0), 0.05)


def _image_frame(info: dict, L, t: float, arr):
    """公式発表の写真: カードの中にゆっくり寄る（Ken Burns・写真の主役の位置へ）。下に小さく「画像：◯◯（公式発表より）」"""
    import numpy as np
    import shorts_v3 as v3

    PW, PH, X0 = v3.PW, v3.PH, 76
    w, h = info["size"]
    x, y = (PW - w) // 2, 130 + (PH - 130 - 110 - h) // 2
    if info.get("title") is not None:
        v3.blend(arr, info["title"], x, y - info["title"].shape[0] - 26)
    img = info["img"]
    ih, iw = img.shape[:2]
    # 一定の速さでゆっくり寄る（16%まで。止まった絵にしない＝ゲートの「動き」の基準を満たす。速さが変わらないので落ち着いて見える）
    k = min(1.0, max(0.0, (t - L.t0) / max(L.t1 - L.t0 + 0.2, 0.5)))
    zoom = 1.0 + 0.16 * k
    fx, fy = info["focus"]
    cw, ch = iw / zoom, ih / zoom
    cx = min(max(iw / 2 + (fx * iw - iw / 2) * k, cw / 2), iw - cw / 2)
    cy = min(max(ih / 2 + (fy * ih - ih / 2) * k, ch / 2), ih - ch / 2)
    if v3.cv2 is not None:
        sx, sy = w / cw, h / ch
        m = np.array([[sx, 0, -sx * (cx - cw / 2)], [0, sy, -sy * (cy - ch / 2)]], dtype=np.float32)
        photo = v3.cv2.warpAffine(img, m, (w, h), flags=v3.cv2.INTER_AREA if sx < 1 else v3.cv2.INTER_LINEAR, borderMode=v3.cv2.BORDER_REPLICATE)
    else:  # pragma: no cover
        from PIL import Image

        photo = np.asarray(Image.fromarray(img).crop((int(cx - cw / 2), int(cy - ch / 2), int(cx + cw / 2), int(cy + ch / 2))).resize((w, h)))
    photo = photo.copy()
    _mask_round(photo, info["corner"])
    arr[y : y + h, x : x + w] = photo
    a = v3.ease_out((t - L.t0 - 1.0) / 0.4)  # 出どころは1秒後（計画の「カード: 写真の出どころ」と同じ時刻）
    if a > 0:
        v3.blend(arr, info["credit"], x, y + h + 28 + int((1 - a) * 12), a)
    return arr


# --------------------------------------------------------------------------- #
# 5. コマンド
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(description="SNS 動画の型（中身に合わせる）")
    parser.add_argument("slugs", nargs="+")
    parser.add_argument("--render", action="store_true", help="見本を書き出す（投稿しない）")
    parser.add_argument("--template", choices=TEMPLATES, help="型を決めて作る（省略時は template_for）")
    parser.add_argument("--out", default=str(ROOT / "out" / "video_templates"))
    parser.add_argument("--tts", default="voicevox")
    parser.add_argument("--reuse-script", action="store_true")
    parser.add_argument("--profile")
    args = parser.parse_args()
    import shorts

    for slug in args.slugs:
        a = shorts.load_article(slug)
        tpl = args.template or template_for(a)
        print(json.dumps({"slug": slug, "template": tpl, "name": NAMES[tpl], "scores": scores(a)}, ensure_ascii=False))
        if args.render:
            import llm

            llm._load_env_local()
            out = make_versions(slug, Path(args.out), tpl, tts=args.tts, reuse=args.reuse_script, profile=args.profile)
            for ver, rep in out.items():
                g = rep.get("gate") or {}
                print(f"  {ver}: {'合格' if g.get('ok') else '不合格'} {rep.get('total')}秒 {g.get('reasons', [])[:4]}")


if __name__ == "__main__":
    main()
