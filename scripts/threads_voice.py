#!/usr/bin/env python3
"""Threads の本文に足す「人らしい一行」。threads_post.py / breaking_post.py から使う（Gemini は呼ばない）。

- editor_comment: サイソク編集部の短い感想（「編集部：」で事実と分ける。誇張しない）。地震・津波には付けない
- question_line: 記事に合った自然な問いかけ（通常投稿の約半分だけ。速報・地震には付けない）
- 同じ言い回しが続かないよう、直近の投稿で使った文は避ける
- A/B（hook_thread / classic）を公平に保つため、問いかけを付けるかは形式と独立に決める（want_question）
"""
from __future__ import annotations

import re

PREFIX = "編集部："

# 判定（verdict）から言えることだけ。感想なので言い切りすぎない
COMMENTS = {
    "free": ["無料で試せるのはありがたいところ。", "まず無料で触ってみられるのがいいですね。", "お試しのハードルが低いのは助かります。"],
    "dev": ["これは開発者にはうれしい更新。", "開発の手間が少し減りそうです。", "現場のエンジニアには地味に効きそう。"],
    "business": ["仕事での使い道が広がりそう。", "日々の業務で効いてきそうな更新です。", "職場で使えるかどうか、気になるところ。", "明日の仕事で試せるか、見ておきたい更新です。"],
    # お金・制度・不動産・ビジネスの話題（読者像: 仕事とお金に意欲の高い働く人。売買はすすめない）
    "money": ["家計や仕事のお金に関わる話なので、押さえておきたいところ。", "自分の場合にどう当てはまるか、一度確かめておきたい話です。", "期限や条件は、公式の案内で確認しておくと安心です。"],
    "general": ["普段使いで便利になりそう。", "身近なところで役立ちそうです。", "使い勝手がどう変わるか楽しみです。"],
    "japan_no": ["日本で使えるようになるかが気になります。", "日本での提供に期待したいところ。"],
    "upcoming": ["続報にも注目しています。", "今後の広がりに注目です。", "提供開始の続報を待ちたいところ。"],
    "research": ["実用化までの道のりにも注目したいです。", "研究の段階ですが、今後が気になる話です。"],
    "default": ["地味に便利そうな更新です。", "これは気になる動き。", "どこまで広がるか注目しています。"],
}
QUESTIONS = {
    "topic": ["ふだん{topic}は使っていますか？", "{topic}、最近使いましたか？"],
    "free": ["無料なら試してみますか？", "まず無料で試してみたいですか？"],
    "dev": ["開発で使ってみたいですか？", "もう試した人はいますか？", "チームに入れてみたいと思いますか？"],
    "business": ["仕事で使ってみたいですか？", "職場で導入されたら使いますか？", "業務のどこで使えそうですか？"],
    "money": ["仕事やお金の面で、影響はありそうですか？", "みなさんの仕事には関係ありそうですか？"],
    "general": ["使ってみたいと思いますか？", "普段使いできそうですか？", "みなさんならどう使いますか？"],
    "japan_no": ["日本でも使えたら試したいですか？"],
    "upcoming": ["使えるようになったら試したいですか？", "みなさんはどう思いますか？"],
    "default": ["みなさんはどう思いますか？", "気になった点はありますか？", "使ってみたいと思いますか？"],
}
TOPIC_QUESTION = {"ChatGPT", "Gemini", "Claude", "Copilot"}
DEV_RE = re.compile(r"開発|エンジニア|データ")
BUSINESS = {"ビジネス職", "経営層", "営業職", "企業のIT部門", "マーケター", "個人事業主", "会社員"}
MONEY_GENRES = {"investment", "real-estate", "business", "public"}
GENERAL = {"一般ユーザー", "クリエイター", "デザイナー"}


def _kind(article: dict) -> str:
    verdict = article.get("verdict") or {}
    audience = str(verdict.get("audience") or "")
    if verdict.get("status") == "available" and verdict.get("japan") == "no":
        return "japan_no"
    if verdict.get("free") is True and verdict.get("status") == "available":
        return "free"
    if DEV_RE.search(audience):
        return "dev"
    if audience in BUSINESS:
        return "business"
    if audience in GENERAL:
        return "general"
    if "研究" in audience:
        return "research"
    if verdict.get("status") and verdict.get("status") != "available":
        return "upcoming"
    if article.get("genreSlug") in MONEY_GENRES:
        return "money"
    return "default"


def _pick(options: list[str], seed: str, recent: list[str]) -> str:
    start = sum(ord(c) for c in seed) % len(options)
    ordered = options[start:] + options[:start]
    return next((o for o in ordered if o not in recent), ordered[0])


def editor_comment(article: dict, recent: list[str]) -> str:
    """編集部の一言（1行）。地震・津波は空"""
    if article.get("disaster"):
        return ""
    kind = _kind(article)
    options = COMMENTS.get(kind) or COMMENTS["default"]
    return PREFIX + _pick(options, article["slug"], [r.removeprefix(PREFIX) for r in recent])


def question_line(article: dict, topic: str, recent: list[str]) -> str:
    """記事に合った問いかけ（1行）。地震・津波は空"""
    if article.get("disaster"):
        return ""
    kind = _kind(article)
    options = list(QUESTIONS.get(kind) or QUESTIONS["default"])
    if topic in TOPIC_QUESTION and kind not in ("japan_no",):
        options += [q.format(topic=topic) for q in QUESTIONS["topic"]]
    return _pick(options, article["slug"][::-1], recent)


def want_question(index: int) -> bool:
    """通常投稿の何本目か（形式の A/B と同じ数え方）から、問いかけを付けるか。

    形式は index の偶奇で交互なので、問いかけは2本ずつ切り替える（0,0,1,1…）。
    これで hook_thread・classic のどちらにも、問いかけあり・なしが同じ数だけ入る
    """
    return (index // 2) % 2 == 1


def add_voice(text: str, comment: str, question: str, limit: int) -> str:
    """本文の最後に、空行を挟んで「編集部の一言」と「問いかけ」を足す。上限を超えるなら問いかけ→一言の順に諦める"""
    base = text.rstrip()
    if question and base.endswith(("？", "?")):
        question = ""  # もともと問いかけで終わっている本文には重ねない
    for extra in ([comment, question], [comment], [question], []):
        lines = [x for x in extra if x]
        candidate = base + ("\n\n" + "\n".join(lines) if lines else "")
        if len(candidate) <= limit:
            return candidate
    return base
