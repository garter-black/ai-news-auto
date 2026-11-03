"""主要な製品・サービスを日本で使えるかの表（content/japan_availability.json）を記事づくりに使う。

- prompt_block(): 生成プロンプトに「当サイトで確認済みの日本での提供状況（日付つき）」として渡す
- check_block(): 事実確認（quality.py）に渡す。表にある事実を「創作」と判定させない
- apply_to_verdict(): 判定の japan が unknown のままのとき、表で ok / partial / no に直す
- fix_japan_section(): 本文の「## 日本で使える？」が「まだ分かりません」で始まるとき、表の答えに差し替える

直すのは、記事の主役（見出し・短い題名に出てくる製品）が表にあり、提供状況が「一般提供中」（status available）のときだけ。
新しいモデル・機能が一部の人だけ・地域限定・提供予定（limited / soon / demo）なら、表があっても unknown のまま（正直に「分からない」）。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLE_PATH = ROOT / "content" / "japan_availability.json"
JAPAN_KNOWN = ("ok", "partial", "no")
ANSWER = {
    "ok": "使えます",
    "partial": "条件付きで使えます",
    "no": "日本では提供されていません",
}
# 「日本で使える？」の章の書き出しが「分からない」になっている
UNKNOWN_ANSWER_RE = re.compile(r"まだ分かりません|述べていません|触れていません|言及なし|分かりません|わかりません|不明です|確認できていません|確認できません|記載がありません|書かれていません")
# 日本での提供・日本語対応を「確認できない」とだけ言う箇条書き（表の答えと食い違うので消す）
UNKNOWN_BULLET_RE = re.compile(
    r"^\s*[-*・]\s*.*(?:提供地域|日本での提供|日本から|日本語(?:への)?対応|地域の案内).*"
    r"(?:確認できて(?:い)?ません|確認できません|記載が(?:あり)?ません|書かれていません|分かりません|不明)"
)
JAPAN_HEADING_RE = re.compile(r"^##\s*日本で(?:も)?使える[？?].*$", re.M)


@lru_cache(maxsize=1)
def load(path: str = str(TABLE_PATH)) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = []
    for item in data.get("items") or []:
        try:
            pattern = re.compile(item["match"])
        except (KeyError, re.error):
            continue
        if item.get("japan") not in (*JAPAN_KNOWN, "unknown"):
            continue
        items.append({**item, "_re": pattern})
    return items


def checked_date(item: dict) -> str:
    dates = sorted(s.get("checkedAt", "") for s in item.get("sources") or [] if s.get("checkedAt"))
    return dates[-1] if dates else ""


def _md(date: str) -> str:
    """2026-11-03 → 11/3"""
    m = re.match(r"\d{4}-(\d{2})-(\d{2})", date or "")
    return f"{int(m.group(1))}/{int(m.group(2))}" if m else date


def matches(text: str, items: list[dict] | None = None) -> list[dict]:
    """text に出てくる製品（出てくる順。同じ位置なら長く一致したもの）"""
    found = []
    for item in items if items is not None else load():
        m = item["_re"].search(text or "")
        if m:
            found.append((m.start(), -(m.end() - m.start()), item))
    found.sort(key=lambda x: (x[0], x[1]))
    return [item for _, _, item in found]


SUBJECT_SPLIT_RE = re.compile(r"[｜|：:？?]|とは|[ 　、](?=[^A-Za-z0-9])")


def subject(text: str) -> str:
    """題名の主題の部分（「Claude Ultraプランとは？料金…」→「Claude Ultraプラン」）"""
    return SUBJECT_SPLIT_RE.split((text or "").strip(), maxsplit=1)[0]


def main_product(record_or_title: dict | str) -> dict | None:
    """記事の主役の製品（短い題名の主題の部分、無ければ見出しの最初のまとまりに出てくるもの）。
    主題に出てこない製品（「CodeRabbit、OpenAIの新AI…」の OpenAI など）は主役にしない"""
    if isinstance(record_or_title, dict):
        text = subject(record_or_title.get("seoTitle") or "") or subject(record_or_title.get("title") or "")
    else:
        text = subject(record_or_title)
    hits = matches(text)
    return hits[0] if hits else None


def _line(item: dict) -> str:
    return (
        f"- {item.get('covers') or item['name']}: 日本で{ {'ok': '使える', 'partial': '条件付きで使える', 'no': '提供なし', 'unknown': '使えるか未確認'}[item['japan']] }。"
        f"{item.get('note', '')}（当サイト確認 {checked_date(item)} / 出典 {item['sources'][0]['url'] if item.get('sources') else 'なし'}）"
    )


def relevant(title: str, text: str = "", limit: int = 6) -> list[dict]:
    """見出しと元記事の冒頭に出てくる製品（見出しのものを先に）"""
    seen, out = set(), []
    for item in matches(title) + matches((text or "")[:4000]):
        if item["id"] not in seen:
            seen.add(item["id"])
            out.append(item)
    return out[:limit]


def prompt_block(title: str, text: str = "") -> str:
    """生成プロンプトに足す。当てはまる製品が無ければ空"""
    items = relevant(title, text)
    if not items:
        return ""
    lines = "\n".join(_line(i) for i in items)
    return f"""

# 当サイトで確認済みの日本での提供状況（日付つき。この欄の事実は使ってよい）
{lines}
- 本文で日本での提供に触れるときと verdict.japan は、まずこの欄を見て答える。ここで「使える」とある製品・プランの話なら「まだ分かりません」と書かない。
  書くときは「（当サイトの確認：{{確認日}}）」のように確認日を添える（例「使えます。Claude は日本でも提供されています（当サイトの確認：11/3）」）。
- ただし、元記事が**新しいモデル・機能の一部の人だけ・地域限定・提供予定の公開**なら、製品が日本で使えても、その新機能が日本で使えるとは限らない。
  その場合は「Claude 自体は日本で使えますが、今回の新機能の日本での提供はまだ発表されていません」のように分けて書き、verdict.japan は unknown にする。
- この欄に無い製品・「未確認」の製品は、発表に書かれていなければ日本での提供には触れない（大事なら「## まだ分からないこと」に1行）。"""


def check_block(title: str, text: str = "") -> str:
    """事実確認に渡す確認済みの事実（表のうち、見出し・元記事に出てくる製品）"""
    items = [i for i in relevant(title, text) if i["japan"] in JAPAN_KNOWN]
    if not items:
        return ""
    return "当サイトで確認済みの日本での提供状況:\n" + "\n".join(_line(i) for i in items)


def apply_to_verdict(verdict: dict | None, record: dict) -> dict | None:
    """主役の製品が表にあり一般提供中なら、判定の japan が unknown のとき表の値で直す。表と同じ答えなら出典（japanSource）を付ける"""
    if not verdict or verdict.get("status") != "available":
        return verdict
    item = main_product(record)
    if not item or item["japan"] not in JAPAN_KNOWN or verdict.get("japan") not in ("unknown", item["japan"]):
        return verdict
    src = (item.get("sources") or [{}])[0]
    return {
        **verdict,
        "japan": item["japan"],
        "japanSource": {"name": item["name"], "url": src.get("url", ""), "checkedAt": checked_date(item)},
    }


def fix_japan_section(body: str, record: dict) -> str:
    """「## 日本で使える？」の最初の段落が「分からない」で、判定を表で直した記事だけ、表の答えに差し替える"""
    verdict = record.get("verdict") or {}
    source = verdict.get("japanSource")
    if not source:
        return body
    item = next((i for i in load() if i["name"] == source.get("name")), None)
    heading = JAPAN_HEADING_RE.search(body or "")
    if not item or not heading:
        return body
    start = heading.end()
    nxt = re.search(r"^##\s", body[start:], re.M)
    end = start + nxt.start() if nxt else len(body)
    section = body[start:end]
    paras = re.split(r"(\n\s*\n)", section.strip("\n"))
    if not paras or not UNKNOWN_ANSWER_RE.search(paras[0].split("。")[0] + "。"):
        return body
    answer = (
        f"{ANSWER[item['japan']]}。{item['name']}は日本でも提供されています"
        f"（当サイトの確認：{_md(checked_date(item))}、[公式ページ]({source.get('url')})）。"
    )
    if item["japan"] == "partial":
        answer = f"{ANSWER['partial']}。{item.get('note', '')}（当サイトの確認：{_md(checked_date(item))}）"
    rest = paras[0].split("。", 1)[1].strip() if "。" in paras[0] else ""
    # 最初の段落の残り（「元記事には…記載がありません」など）は残さない。「分からない」の言い換えなので食い違う
    if rest and UNKNOWN_ANSWER_RE.search(rest):
        rest = ""
    new_first = answer + rest
    tail = "".join(paras[1:])
    kept = "\n".join(line for line in tail.split("\n") if not UNKNOWN_BULLET_RE.match(line))
    new_section = "\n\n" + new_first + ("\n\n" + kept.strip("\n") if kept.strip() else "") + "\n\n"
    return re.sub(r"\n{3,}", "\n\n", body[:start] + new_section + body[end:].lstrip("\n"))
