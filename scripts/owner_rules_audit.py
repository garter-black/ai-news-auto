#!/usr/bin/env python3
"""オーナーのルールの毎日の点検（読むだけ。何も投稿・変更しない）。.github/workflows/owner_rules_audit.yml

オーナーが直したこと（docs/owner_rules.md の台帳）が本番で守られているかを、毎日1回まとめて確かめる。
生成・公開・投稿の門番（quality.py・headline_normalize.py・sns_rules.py など）をすり抜けたものや、
門番ができる前に出たものを見つけるための「見張り」。

  python3 scripts/owner_rules_audit.py                 # 本番を見て、違反を表示（終了コードは常に 0）
  python3 scripts/owner_rules_audit.py --issue         # GitHub の issue（ラベル owner-rule-violation）に書く／無ければ閉じる
  python3 scripts/owner_rules_audit.py --no-network    # 記事の JSON だけで確かめる（本番を開かない）
  python3 scripts/owner_rules_audit.py --comment-log ~/.claude/scheduled-tasks/saisoku-threads-replies/history.json
                                                       # 手元だけ: コメントの時刻（10:20〜22:59）

見るもの
  - 新しい記事 --latest 本（既定 20）の JSON と本番のページ（題名・小見出し・札・言葉づかい・画像・広告・AI画像の表示）
  - 固定ページ（/・/about・/editorial-policy・/contact・/news）: 連絡先・オーナーの名前・人の確認の約束
  - SNS: Bluesky の公開フィード（鍵なし・Buffer の回数を使わない）の直近 --sns-days 日の投稿を sns_rules で判定。
    Threads / X / Facebook は同じ文面を Buffer で出すので、Buffer は読まない（回数の節約。必要なら sns_audit.yml）

違反の行は必ず台帳の番号（[A1] など）で始める。台帳とずれていないかは tests/test_owner_rules_ledger.py が確かめる。
自社アクセスとして数えないよう、すべての取得にヘッダー X-Saisoku-Internal: 1 を付ける（AGENTS.md）。広告の画像には触らない。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
RETIRED_PATH = ROOT / "content" / "retired.json"
BASE = "https://ai-tech-express.jp"
UA = "SaisokuOwnerRulesAudit/1.0 (+https://ai-tech-express.jp/contact)"
HEADERS = {"User-Agent": UA, "X-Saisoku-Internal": "1"}
JST = timezone(timedelta(hours=9))
LABEL = "owner-rule-violation"
ISSUE_TITLE = "オーナーのルールに合わないものが本番にあります（毎日の点検）"
CONTACT_EMAIL = "contact@ai-tech-express.jp"
OWNER_NAME = "小室"
OWNER_NAME_PAGES = ("/about", "/editorial-policy")

# この点検が確かめる台帳の番号（docs/owner_rules.md の「点検」列と一致させる。tests/test_owner_rules_ledger.py）
AUDIT_RULES = (
    "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11",
    "I2", "I4", "I5",
    "A1", "A2", "A3", "A4", "A5", "A6", "A10", "A11", "A12",
    "W3", "W5", "W6",
)

# ---- 記事の言葉 ----------------------------------------------------------------

# A1: 決まった型の小見出し（2026-11-03「自分に関係ある？」「ライバルと比べてどう？」「もう使える？取引できる？」）
TEMPLATE_HEADINGS = re.compile(r"^(?:自分に関係ある[？?]|ライバルと比べてどう[？?]|もう使える[？?]|取引できる[？?]|もう使える[？?]取引できる[？?]|何がすごいの[？?])$")
# A2: 答えになっていない欄（元記事に無いことを「書かれていません」で埋める）
NON_ANSWER = re.compile(r"元記事(?:に|で)は[^。\n]{0,16}(?:書かれて|記載(?:されて)?|明記(?:されて)?|触れられて|言及(?:されて)?)(?:い)?(?:ません|なし)|判断できません|判断できない状態です")
# A3: 中身のない決まり文句
FILLER = re.compile(r"にとっても重要です|と(?:いえ|言え)(?:ます|るでしょう)|とも(?:いえ|言え)(?:ます|るでしょう)|目が離せません|注目が集まりそうです")
# A4: 幼い言い回し（2026-11-03「幼すぎる表現は本当にやめたい。恥ずかしい」）。「ざっくり」も使わない
CHILDISH = re.compile(r"すごい|スゴい|すっごく|しちゃう|しちゃい|ちゃいます|ちゃった|を作る道具|めっちゃ|ヤバい|やばい|ワクワク|ざっくり")
# A6: 見出しの札があざとい（「3秒でわかる」など）
GIMMICK_LABEL = re.compile(r"\d+秒でわかる|\d+分でわかる|一瞬でわかる|サクッと")

# A5: 有名な会社に無理な説明を付けた題名（「ChatGPTのOpenAI」「AI半導体のNVIDIA」）
FAMOUS = (
    "OpenAI|NVIDIA|Google|Apple|Microsoft|Meta|Amazon|Anthropic|Samsung|サムスン|ソニー|トヨタ|Tesla|テスラ|"
    "Intel|AMD|ソフトバンク|楽天|任天堂|パナソニック|日立|ホンダ|NTT|KDDI|LINEヤフー|Netflix|Adobe|IBM|Qualcomm|Xiaomi"
)
FORCED_DESCRIPTOR = re.compile(rf"(?:^|[　\s、「])([^\s　、。「」『』]{{2,14}})の({FAMOUS})(?=が|は|、|　|\s|の|と|も|$)")
DESCRIPTOR_OK = re.compile(r"(?:傘下|子会社|親会社|米国|米|日本|中国|韓国|台湾|欧州|英国|日本法人)$")
ODD_PUNCT = [
    (re.compile(r"」、"), "「」、」（かぎかっこのすぐ後ろの読点）"),
    (re.compile(r"[、，][　\s]|[　\s][、。]"), "読点・句点の前後の空白"),
    (re.compile(r"、、|。。|！！|？？|[!?！？]{2,}"), "記号の重なり"),
    (re.compile(r"[、。・：:]$"), "末尾の記号"),
    (re.compile(r"。(?=.)"), "見出しの途中の「。」"),
    (re.compile(r"[!！]"), "感嘆符"),
]


def title_problems(title: str) -> list[str]:
    out = []
    title = str(title or "").strip()
    for m in FORCED_DESCRIPTOR.finditer(title):
        if not DESCRIPTOR_OK.search(m.group(1)):
            out.append(f"[A5] 有名な会社に説明を付けた題名（「{m.group(1)}の{m.group(2)}」）")
    for pattern, label in ODD_PUNCT:
        if pattern.search(title):
            out.append(f"[A5] 題名の記号が不自然: {label}")
    return out


def _headings(markdown: str) -> list[str]:
    return [re.sub(r"^#+\s*", "", line).strip() for line in str(markdown or "").splitlines() if re.match(r"^#{2,4}\s", line)]


def _texts(record: dict) -> list[tuple[str, str]]:
    """言葉を確かめる欄（欄の名前, 文字）"""
    out = [(k, str(record.get(k) or "")) for k in ("title", "lede", "description", "body", "easy")]
    out += [("highlights", str(h)) for h in record.get("highlights") or []]
    wm = record.get("workMoney") or {}
    out += [("workMoney", str(p)) for p in (wm.get("points") or [] if isinstance(wm, dict) else [])]
    out += [("faq", f"{r.get('q', '')} {r.get('a', '')}") for r in record.get("faq") or [] if isinstance(r, dict)]
    out += [("todayActions", str(x)) for x in record.get("todayActions") or []]
    v = record.get("verdict") or {}
    if isinstance(v, dict):
        out.append(("verdict", str(v.get("take") or "")))
    return [(k, t) for k, t in out if t]


def _snippet(text: str, m: re.Match) -> str:
    return text[max(0, m.start() - 8): m.end() + 8].replace("\n", " ")


def record_problems(record: dict) -> list[str]:
    """記事の JSON だけで分かる違反（本番を開かない）"""
    out = title_problems(record.get("title") or "")
    for heading in _headings(f"{record.get('body') or ''}\n{record.get('easy') or ''}"):
        if TEMPLATE_HEADINGS.match(heading):
            out.append(f"[A1] 決まった型の小見出し（「{heading}」）")
    for field, text in _texts(record):
        # 型の小見出しの行は [A1] で数えるので、言葉の点検からは外す（同じものを2回数えない）
        text = "\n".join(line for line in text.splitlines() if not TEMPLATE_HEADINGS.match(re.sub(r"^#+\s*", "", line).strip()))
        if m := NON_ANSWER.search(text):
            out.append(f"[A2] 答えになっていない欄（{field}:「{_snippet(text, m)}」）。欄ごと省く")
        if m := FILLER.search(text):
            out.append(f"[A3] 中身のない決まり文句（{field}:「{_snippet(text, m)}」）")
        if m := CHILDISH.search(text):
            out.append(f"[A4] 幼い言い回し（{field}:「{_snippet(text, m)}」）")
    out += image_record_problems(record)
    out += low_value_problems(record)
    return list(dict.fromkeys(out))


def image_record_problems(record: dict) -> list[str]:
    credit = record.get("imageCredit") or {}
    if not isinstance(credit, dict) or credit.get("kind") != "ai":
        return []
    try:
        import images  # noqa: PLC0415

        if images.product_launch(record):
            return ["[I2] 発表された製品・車・端末を AI で描いた画像"]
    except Exception:  # noqa: BLE001
        pass
    return []


def low_value_problems(record: dict) -> list[str]:
    """A10: 価値の低い種類（他人の動画の要約・プレスリリースの言い換え・はっきり薄い記事）"""
    try:
        import indexnow  # noqa: PLC0415
        import retire_articles  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    channel = record.get("channelSlug")
    if channel in indexnow.CREATOR_CHANNELS:
        return ["[A10] 他人の動画の要約の記事（新しく作らない種類）"]
    if channel in getattr(indexnow, "WIRE_SOURCES", set()):
        return ["[A10] プレスリリース配信の言い換えの記事（新しく作らない種類）"]
    if retire_articles.is_thin(record):
        return ["[A10] 独自の情報が少ない短い記事"]
    return []


# ---- 本番のページ ---------------------------------------------------------------


class PageParser(HTMLParser):
    """小見出し（h1〜h3）と、本文に見える文字（script・style を除く）"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[str] = []
        self.text: list[str] = []
        self._skip = 0
        self._h: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self._skip += 1
        elif tag in ("h1", "h2", "h3"):
            self._h = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("h1", "h2", "h3") and self._h is not None:
            self.headings.append(re.sub(r"\s+", " ", "".join(self._h)).strip())
            self._h = None

    def handle_data(self, data):
        if self._skip:
            return
        self.text.append(data)
        if self._h is not None:
            self._h.append(data)


def parse(html: str) -> PageParser:
    p = PageParser()
    p.feed(html)
    return p


def article_page_problems(html: str, record: dict) -> list[str]:
    """記事のページ（本番）の違反"""
    out = []
    page = parse(html)
    # 記事の JSON にある小見出しは record_problems で数える。ここではページの部品が足した小見出しだけ
    own = set(_headings(f"{record.get('body') or ''}\n{record.get('easy') or ''}"))
    gimmick = False
    for h in page.headings:
        if h in own:
            continue
        if TEMPLATE_HEADINGS.match(h):
            out.append(f"[A1] 決まった型の小見出しがページの部品から出ている（「{h}」）")
        elif CHILDISH.search(h):
            out.append(f"[A4] 小見出しに幼い言い回し（「{h}」）")
        if GIMMICK_LABEL.search(h):
            gimmick = True
            out.append(f"[A6] あざとい札（「{h}」）")
    visible = "".join(page.text)
    if not gimmick and (m := GIMMICK_LABEL.search(visible)):
        out.append(f"[A6] あざとい札がページに出ている（「{_snippet(visible, m)}」）")
    credit = record.get("imageCredit") or {}
    if isinstance(credit, dict) and credit.get("kind") == "ai" and "AI生成" not in visible:
        out.append("[I4] AI の画像なのに「AI生成」の表示がない")
    if not re.search(r'rel="[^"]*sponsored', html):
        out.append("[A11] 記事に広告（PR）が1つもない")
    if OWNER_NAME in html:  # 見えない構造化データ（JSON-LD）も含めて見る
        out.append("[W6] オーナーの名前が記事のページに出ている（/about・/editorial-policy だけ）")
    return list(dict.fromkeys(out))


REVIEW_CLAIM = re.compile(
    r"(?:全て|すべて|全部)の記事を(?:人|編集部|編集者|スタッフ)が|全記事を(?:人|編集部|編集者|スタッフ)が|"
    r"(?:人|編集者|編集部)が(?:1本ずつ|一本ずつ|全て|すべて)(?:確認|チェック|読)|確認済み(?:の記事)?の一覧"
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def static_page_problems(path: str, html: str) -> list[str]:
    out = []
    visible = "".join(parse(html).text)
    if path in ("/about", "/editorial-policy") and (m := REVIEW_CLAIM.search(visible)):
        out.append(f"[A12] 人の確認について実態と違う約束（{path}:「{_snippet(visible, m)}」）")
    if path in ("/about", "/contact"):
        if CONTACT_EMAIL not in html:
            out.append(f"[W5] 連絡先 {CONTACT_EMAIL} が {path} に無い")
        others = {e for e in EMAIL.findall(visible) if e.lower() != CONTACT_EMAIL and not e.lower().endswith(("@example.com", "@sentry.io"))}
        if others:
            out.append(f"[W5] {path} に別の連絡先（{', '.join(sorted(others))[:60]}）")
    if path not in OWNER_NAME_PAGES and OWNER_NAME in html:
        out.append(f"[W6] オーナーの名前が {path} に出ている（/about・/editorial-policy だけ）")
    return out


def fetch(url: str, timeout: int = 30) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=HEADERS)
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.status, res.read(3_000_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, ""
        except Exception:  # noqa: BLE001
            continue
    return 0, ""


# ---- SNS ------------------------------------------------------------------------


def sns_problems(posts: list[dict], judge) -> list[str]:
    """SNS の投稿（sns_audit.normalize_bsky の形）の違反。judge は sns_rules.audit_post"""
    out = []
    seen: dict[str, str] = {}
    for post in posts:
        if post.get("reply"):
            continue
        where = post.get("url") or post.get("id") or ""
        when = str(post.get("at") or "")
        try:
            when = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(JST).strftime("%m/%d %H:%M")
        except ValueError:
            pass
        for v in judge(post) or []:
            out.append(f"{_sns_rule_id(v)} {post.get('platform')} {when}: {v}（{where}）")
        links = " ".join([post.get("text") or "", *(post.get("links") or [])])
        if m := re.search(r"/articles/([A-Za-z0-9_-]+)", links):
            slug = m.group(1)
            if slug in seen and seen[slug] != where:
                out.append(f"[S8] 同じ記事の投稿が2つある（{slug}: {seen[slug]} と {where}）")
            seen.setdefault(slug, where)
    return out


# sns_rules のルールの番号 → 台帳の番号
SNS_RULE_MAP = {
    "ルール1": "[S4]", "ルール2": "[S7]", "ルール3": "[S9]", "ルール4": "[S11]", "ルール5": "[S8]",
    "ルール6": "[S1]", "ルール7": "[S4]", "長さ": "[S11]",
}


def _sns_rule_id(violation: str) -> str:
    m = re.match(r"\[(ルール\d|長さ)\]", violation)
    if not m:
        return "[S11]"
    if m.group(1) == "ルール6":
        if "速報ではない" in violation:
            return "[S2]"
        if "書き出し" in violation and "呼びかけ" not in violation:
            return "[S3]"
        if "問いかけ" in violation:
            return "[S5]"
    if m.group(1) == "ルール1" and "リンク" in violation:
        return "[S6]"
    if m.group(1) == "ルール1" and "問いかけ" in violation:
        return "[S5]"
    if "ざっくり" in violation:
        return "[S10]"
    return SNS_RULE_MAP.get(m.group(1), "[S11]")


# ---- 手元だけ: コメントの時刻 ------------------------------------------------------


def comment_time_ok(hhmm: str) -> bool:
    """O4: コメントは 10:20〜22:59（日本時間）だけ"""
    try:
        h, m = (int(x) for x in str(hhmm).split(":")[:2])
    except ValueError:
        return False
    return (10, 20) <= (h, m) <= (22, 59)


def comment_log_problems(entries: list[dict], since: str = "2026-11-03") -> list[str]:
    return [f"[O4] {e.get('date')} {e.get('time')} に {e.get('sns')} へコメント（10:20〜22:59 だけ）"
            for e in entries if str(e.get("date") or "") >= since and not comment_time_ok(str(e.get("time") or ""))]


# ---- まとめ ---------------------------------------------------------------------


def load_articles() -> list[dict]:
    try:
        retired = set((json.loads(RETIRED_PATH.read_text("utf-8")).get("articles") or {}).keys())
    except (OSError, ValueError):
        retired = set()
    out = []
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("slug") and data["slug"] not in retired and not data.get("duplicateOf"):
            out.append(data)
    out.sort(key=lambda a: str(a.get("publishedAt") or ""), reverse=True)
    return out


def run(latest: int, sns_days: float, network: bool, base: str = BASE) -> dict[str, list[str]]:
    """{場所: [違反...]}"""
    found: dict[str, list[str]] = {}
    articles = load_articles()[:latest]
    pages: dict[str, tuple[int, str]] = {}
    if network:
        urls = [f"/articles/{a['slug']}" for a in articles] + ["/", "/about", "/editorial-policy", "/contact", "/news"]
        with ThreadPoolExecutor(max_workers=6) as pool:
            pages = dict(zip(urls, pool.map(lambda p: fetch(base + p), urls)))
    for a in articles:
        path = f"/articles/{a['slug']}"
        v = record_problems(a)
        if network:
            status, html = pages.get(path, (0, ""))
            if status != 200:
                v.append(f"[W3] ページが開けない（HTTP {status}）")
            else:
                v += article_page_problems(html, a)
        if v:
            found[path] = list(dict.fromkeys(v))
    if not network:
        return found
    for path in ("/", "/about", "/editorial-policy", "/contact", "/news"):
        status, html = pages.get(path, (0, ""))
        v = [f"[W3] ページが開けない（HTTP {status}）"] if status != 200 else static_page_problems(path, html)
        if v:
            found[path] = v
    # I5: 画像が読めるか（scripts/check_images.py と同じ判定。新しい記事のページだけ）
    try:
        import check_images  # noqa: PLC0415

        where: dict[str, str] = {}
        for p, (status, html) in pages.items():
            if status == 200 and p.startswith("/articles/"):
                for u in check_images.page_images(html, base + p)[:12]:
                    where.setdefault(u, p)
        for url, problem in check_images.check_urls(list(where)[:150]).items():
            if problem:
                found.setdefault(where[url], []).append(f"[I5] 読めない画像（{problem}: {url[:90]}）")
    except Exception as error:  # noqa: BLE001
        print(f"[owner-audit] 画像の確認をとばしました: {type(error).__name__}", file=sys.stderr)
    # SNS（Bluesky の公開フィード）
    try:
        import sns_audit  # noqa: PLC0415
        import sns_rules  # noqa: PLC0415

        since = datetime.now(timezone.utc) - timedelta(days=sns_days)
        posts = [sns_audit.normalize_bsky(p) for p in sns_audit.bluesky_posts(since)]
        v = sns_problems(posts, sns_rules.audit_post)
        found["SNS（Bluesky の直近の投稿 %d 本）" % len([p for p in posts if not p.get("reply")])] = v
    except Exception as error:  # noqa: BLE001
        found["SNS"] = [f"[S11] SNS の投稿を読めなかった（{type(error).__name__}）"]
    return {k: v for k, v in found.items() if v}


def render(found: dict[str, list[str]], latest: int) -> str:
    total = sum(len(v) for v in found.values())
    by_rule: dict[str, int] = {}
    for v in found.values():
        for line in v:
            rid = re.match(r"\[([A-Z]+\d+)\]", line)
            by_rule[rid.group(1) if rid else "?"] = by_rule.get(rid.group(1) if rid else "?", 0) + 1
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"オーナーのルールの毎日の点検（{now} JST・新しい記事 {latest} 本・固定ページ・Bluesky の直近の投稿）で、**{total} 件**見つかりました。",
        f"番号は台帳 [docs/owner_rules.md](https://github.com/{os.environ.get('GITHUB_REPOSITORY') or 'koyuiw7-pixel/ai-news-auto'}/blob/main/docs/owner_rules.md) の番号です。",
        "",
        "| 番号 | 件数 |",
        "|---|---|",
        *[f"| {k} | {n} |" for k, n in sorted(by_rule.items(), key=lambda x: (-x[1], x[0]))],
        "",
    ]
    for where, v in found.items():
        lines.append(f"**{where}**")
        lines += [f"- {x}" for x in v[:15]]
        if len(v) > 15:
            lines.append(f"- …ほか {len(v) - 15} 件")
        lines.append("")
    lines.append("見張り: scripts/owner_rules_audit.py（.github/workflows/owner_rules_audit.yml）。読むだけで、投稿・変更はしません。違反が無くなると自動で閉じます。")
    return "\n".join(lines)


def _gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60, check=False)


def write_issue(found: dict[str, list[str]], body: str) -> None:
    listed = _gh("issue", "list", "--label", LABEL, "--state", "open", "--limit", "1", "--json", "number", "--jq", ".[0].number // empty")
    number = listed.stdout.strip() if listed.returncode == 0 else ""
    run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    if not found:
        if number:
            _gh("issue", "close", number, "--comment", f"今日の点検では違反はありませんでした。{run_url}")
        return
    body = f"{body}\n\n実行: {run_url}"
    if number:
        _gh("issue", "edit", number, "--body", body)
        _gh("issue", "comment", number, "--body", f"今日の点検: {sum(len(v) for v in found.values())} 件（本文を最新に更新しました）。{run_url}")
        print(f"[owner-audit] issue #{number} を更新しました")
        return
    _gh("label", "create", LABEL, "--color", "B60205", "--description", "オーナーのルール（docs/owner_rules.md）に合わないもの", "--force")
    made = _gh("issue", "create", "--title", ISSUE_TITLE, "--label", LABEL, "--body", body)
    print(f"[owner-audit] issue を立てました: {made.stdout.strip() or made.stderr.strip()[:200]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="オーナーのルールの毎日の点検（読むだけ）")
    parser.add_argument("--latest", type=int, default=20)
    parser.add_argument("--sns-days", type=float, default=1.0)
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--issue", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument("--comment-log", default="")
    args = parser.parse_args()
    if args.comment_log:
        entries = json.loads(Path(args.comment_log).expanduser().read_text("utf-8"))
        bad = comment_log_problems(entries)
        print("\n".join(bad) or "ok: コメントはすべて 10:20〜22:59")
        return 0
    found = run(args.latest, args.sns_days, not args.no_network, args.base)
    body = render(found, args.latest)
    print(body)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(found, ensure_ascii=False, indent=1), "utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(body + "\n")
    if args.issue:
        write_issue(found, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
