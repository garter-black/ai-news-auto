"""調査会社・情報会社（東京商工リサーチ・帝国データバンクなど）の記事を「報道」として扱う（2026-11-04 オーナー決定 D2・台帳 A37）。

取材・調査の記事は、登場する企業・機関の公式発表ではない。そこで
  - 記事の生成（scripts/web_articles.py）: 「〇〇の調べによると」と書かせ、免責文を報道向けにする
  - 写真（scripts/images.py）: その会社のサイトの写真を、記事の主役（登場する企業）の公式写真として使わない
  - 毎日の点検（scripts/owner_rules_audit.py）: 公式発表の免責文・公式写真の出典が残っていないか
一覧の正本は lib/research-firms.json（サイトの lib/news-outlets.ts も同じ一覧。ずれはテストが確かめる）。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIST = ROOT / "lib" / "research-firms.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(LIST.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"channels": {}, "hosts": {}}


def channels() -> dict[str, str]:
    return dict(_data().get("channels") or {})


def hosts() -> dict[str, str]:
    return dict(_data().get("hosts") or {})


def host_firm(url: str) -> str:
    """URL が調査会社・情報会社のサイトなら会社名（違えば空）"""
    host = urllib.parse.urlparse(str(url or "")).netloc.lower().split(":")[0]
    for h, name in hosts().items():
        if host == h or host.endswith("." + h):
            return name
    return ""


def firm_name(channel_slug: str = "", url: str = "") -> str:
    """発信元（channelSlug か元記事の URL）が調査会社・情報会社なら会社名（違えば空）"""
    return channels().get(str(channel_slug or "").strip(), "") or host_firm(url)


def article_firm(article: dict) -> str:
    return firm_name(article.get("channelSlug", ""), article.get("sourceUrl", ""))


OFFICIAL_BASIS = re.compile(r"(?:研究機関の|官公庁などの|各社の)?公式発表の文章をもとに")
# 免責文に残ってはいけない言い方（点検・テストで使う）
OFFICIAL_CLAIM = re.compile(r"公式発表の文章をもとに|公式発表をもとに")


def disclaimer(name: str, base: str = "") -> str:
    """調査会社・情報会社の記事の免責文（「公式発表の文章をもとに」と書かない）。
    base（ジャンルの免責文）があれば、出典の言い方だけを「報道（〇〇の調査・取材記事）」に替え、投資助言でない旨などは残す"""
    basis = f"報道（{name}の調査・取材記事）をもとに"
    if base and OFFICIAL_BASIS.search(base):
        return OFFICIAL_BASIS.sub(basis, base, count=1)
    return (
        f"本記事は{basis}AIが要約・解説して自動生成したものです。"
        "誤りが含まれる可能性があるため、正確な情報は元の記事と、登場する企業・機関の発表をご確認ください。"
    )
