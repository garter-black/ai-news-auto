"""AI・テックの用語の訳し方（2026-11-03）。記事を書くプロンプトに入れ、品質ゲートで誤訳を見つけるのに使う。

無料の軽量モデルは「inference」を「インプレッション」、「early adopter」を「初期 adopter」のように訳し損ねることがあった。
訳語はここ1か所で決める（web_articles.py のプロンプトと quality.py の誤訳チェックが読む）。

  TERMS          … (英語, 日本語の書き方, 補足)。プロンプトの用語集になる
  MISTRANSLATIONS … (元記事にある英語の正規表現, 記事に出たら誤訳とみなす日本語の正規表現, 正しい書き方[, 元記事にあれば誤訳とみなさない英語])
"""

from __future__ import annotations

import re

TERMS: list[tuple[str, str, str]] = [
    ("inference", "推論", "学習済みのAIが答えを出す処理。「インプレッション」「推測」ではない"),
    ("inference cost / pricing", "推論のコスト（AIを動かす費用）", ""),
    ("reasoning model", "推論モデル（考えてから答えるAI）", "「理由付け」と訳さない"),
    ("training / pre-training", "学習 / 事前学習", ""),
    ("fine-tuning", "ファインチューニング（追加学習）", ""),
    ("model weights / open weights", "モデルの重み / オープンウェイト（重みを公開したモデル）", ""),
    ("parameters", "パラメーター（AIの規模を表す数）", ""),
    ("token", "トークン（AIが文字を数える単位）", ""),
    ("context window", "コンテキストウィンドウ（一度に読める量）", ""),
    ("prompt", "プロンプト（AIへの指示文）", ""),
    ("agent / agentic", "エージェント（自分で手順を進めるAI） / エージェント型", ""),
    ("benchmark", "ベンチマーク（性能テスト）", ""),
    ("latency", "応答の速さ（遅延）", ""),
    ("throughput", "処理量", ""),
    ("general availability (GA)", "一般提供", "「一般的な可用性」と訳さない"),
    ("preview / public preview", "プレビュー版 / 公開プレビュー", ""),
    ("beta", "ベータ版", ""),
    ("rollout / rolling out", "順次提供（段階的に提供）", "提供が始まっていることを表す。「予定」ではない"),
    ("waitlist", "順番待ちの登録", ""),
    ("early adopter", "早くから使い始める人（アーリーアダプター）", "「初期 adopter」のように英語を残さない"),
    ("deploy / deployment", "導入 / 本番での利用", ""),
    ("open source", "オープンソース（無料で公開されたソフト）", ""),
    ("hallucination", "ハルシネーション（AIがもっともらしい誤りを答えること）", ""),
    ("multimodal", "マルチモーダル（文字・画像・音声をまとめて扱える）", ""),
    ("on-device", "端末の中で動く", ""),
    ("enterprise", "大企業向け / 法人向け", ""),
    ("pricing tier / plan", "料金プラン", ""),
    ("rate limit / usage limit", "利用上限", ""),
    ("API", "API（アプリから呼び出す仕組み）", "英語のまま書いてよい"),
]

MISTRANSLATIONS: list[tuple] = [
    (re.compile(r"\binferenc", re.I), re.compile(r"インプレッション"), "inference は「推論」", re.compile(r"\bimpressions?\b", re.I)),
    (re.compile(r"\binferenc", re.I), re.compile(r"推測(?:コスト|速度|処理|性能|時|費用|用|向け|の(?:コスト|速度|処理|性能|費用))"), "inference は「推論」"),
    (re.compile(r"\breasoning\b", re.I), re.compile(r"理由付け"), "reasoning は「推論（考えてから答える）」"),
    (re.compile(r"general(?:ly)? availab", re.I), re.compile(r"一般的な可用性|一般可用性"), "general availability は「一般提供」"),
    (re.compile(r"\bopen[- ]weights?\b", re.I), re.compile(r"開いた重み|オープンな体重"), "open weights は「オープンウェイト（重みを公開したモデル）」"),
    (re.compile(r"\bweights?\b", re.I), re.compile(r"体重"), "weights は「（モデルの）重み」"),
    (re.compile(r"\bcontext window\b", re.I), re.compile(r"文脈の窓|コンテキストの窓"), "context window は「コンテキストウィンドウ（一度に読める量）」"),
    (re.compile(r"\bearly adopters?\b", re.I), re.compile(r"初期\s*adopter|早期\s*adopter", re.I), "early adopter は「早くから使い始める人」"),
]


def prompt_block() -> str:
    """記事を書くプロンプトに足す用語集"""
    lines = []
    for en, ja, note in TERMS:
        lines.append(f"- {en} → {ja}" + (f"（{note}）" if note else ""))
    return (
        "## 用語の訳し方（必ず守る）\n"
        "英語の用語は次のとおりに訳す。表にない一般的な英単語（users, feature, release など）は日本語に訳し、"
        "日本語の文の中に小文字の英単語を残さない（製品名・会社名・API などの略語はそのままでよい）。"
        "中国語の言い回し（「不过」「但是」「这个」など）や中国の簡体字を混ぜない。\n" + "\n".join(lines)
    )


def mistranslations(article_text: str, source_text: str) -> list[str]:
    """元記事の英語に対して、記事に誤訳の言い回しがあれば「（誤訳の語）→ 正しい書き方」を返す"""
    out = []
    for src_re, bad_re, fix, *unless in MISTRANSLATIONS:
        if not src_re.search(source_text or "") or (unless and unless[0].search(source_text or "")):
            continue
        m = bad_re.search(article_text or "")
        if m:
            out.append(f"「{m.group(0)}」（{fix}）")
    return out
