#!/usr/bin/env python3
"""公式ブログ・プレスリリース（文章）から、動画記事と同じ形式の記事 JSON を作る。

動画解析よりずっと少ないトークンで済むので、無料枠のまま本数を増やせる。

  python3 scripts/web_articles.py --dry-run --max 5     # 候補を表示（API なし）
  python3 scripts/web_articles.py --max 10              # 最大10本生成（bulk モデル）
  python3 scripts/web_articles.py --max 5 --tier quality --days 2 --daily-cap 40
  python3 scripts/web_articles.py --max 0 --trends 2          # 話題のニュース（キュー）だけ2本
  python3 scripts/web_articles.py --section broad --max-broad 6 --dry-run   # 幅広い話題の候補をセクション別に表示
  python3 scripts/web_articles.py --max 2 --max-broad 6 --daily-cap 120     # 毎時実行の想定（AI 2本＋幅広い話題 6本）

生成した記事は quality.py のゲート（形のチェック＋元記事との事実確認）を通ったものだけ書き出す。
直近5日の記事と同じ話題の候補は dedupe.py と story.py（サイトの重複判定と同じ基準）で生成前に外す。
元の発表から48時間を超えた候補は通常の記事にしない（記事ページの公開日時でフィードの日付を直してから判定。
大きなニュースで記事がまだ無いものだけ late として「速報ではない」形で作る）。
直近14日の記事の続報は、新しいページを作らず既存記事に「## 続報」の章を足す（followup.py）。
  続報は --daily-cap に数えない（新しいページの本数の上限なので）。1回の実行で最大 --max-updates 本。
新しい記事には、サイソクの過去記事（archive.py）から「これまでの流れ」の材料を渡す。
大きなニュース（主要企業の新モデル・新製品の公式発表）は実行の最初に tier=quality で作り、最大3回試行。
  作れなければ processed にせず、3回の実行まで再挑戦する。同じ話題の派生の発表（GitHub Copilot 対応など）は本家の記事への続報に回す。
配信（PR TIMES など）は生成前に本文の厚み・お知らせ系を足切りし、1回の実行で --max-wire 本までしか生成に回さない。
  （2026-10-27 から配信は取得していない。kind=wire のフィードが無ければこの処理は何もしない）

速報レーン（--fast-url）: 見張り番の Worker が主要企業の公式発表を見つけたら、その URL だけを大きなニュースとして
最初に作り、ほかのレーン（話題・公式フィード・幅広い話題）は回さない。事実確認も quality の段で行い待たせない。
結果は state/fast_lane.json に書く（Worker が受領を確認する）。詳しくは scripts/fast_lane.py
  python3 scripts/web_articles.py --fast-url https://openai.com/index/xxx --fast-meta '[{"url": "...", "detectedAt": "..."}]'
通常の回は、速報レーンの実行が待機していると今の記事を作り終えたところで早めに終える（fast_lane.fast_waiting）。
レーダー（ops/watchdog/src/radar.js）の話題で一次情報が無いものは、信頼できる報道機関の記事を出典にする（secondary。
「〇〇の報道によると」と書かせ、免責文も報道向けにする）。
大きなニュース（速報レーン・major）は品質ゲートを実用的にする: 本文の長さの基準を8割に緩め、事実確認には発信元・公開日時を渡し、
致命的でない細かい指摘（言い過ぎ・訳語など。点数75以上）は作り直さず指摘の文だけ直して公開する（quality.surgical_fix）。
記事には sourcePublishedAt（発表の時刻）と detectedAt（こちらが見つけた時刻）を残す（scripts/latency.py が速さを集計）。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import urllib.parse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive  # noqa: E402
import article_charts  # noqa: E402
import dedupe  # noqa: E402
import facts_bundle  # noqa: E402
import fast_lane  # noqa: E402
import pipeline_health  # noqa: E402
import feeds  # noqa: E402
import followup  # noqa: E402
import glossary  # noqa: E402
import indexnow  # noqa: E402  検索に出せない記事は公開しない（公開前の門）
import japan_availability  # noqa: E402
import llm  # noqa: E402
import main as m  # noqa: E402
import news_types  # noqa: E402  ニュースの種類ごとの記事の型
import source_links  # noqa: E402
import headline_normalize  # noqa: E402
import pacing  # noqa: E402  1日の上限の数え方（公開中だけ）と時刻ごとの配分
import page_watch  # noqa: E402
import persona  # noqa: E402
import quality  # noqa: E402
import rivals  # noqa: E402
import story  # noqa: E402
import trends  # noqa: E402
import ui_claims  # noqa: E402
from genres import AI, ALL_GENRES, BROAD_GENRES, GENRES, GENRES_BY_SLUG, WEB_GENRES  # noqa: E402
import genre_focus  # noqa: E402  投資・マネー／不動産／エンタメの扱う中身と1日の最低本数（2026-11-03）

RUN_TIME_BUDGET_SEC = 25 * 60
MAX_FAILURES = 2
MIN_TEXT_CHARS = 600
DEDUPE_DAYS = 5
# 元の発表からこれ以上たったものは、通常のニュース記事にしない（scripts/story.py・lib/indexing.ts の48時間と同じ）。
# 大きなニュースで、まだ記事が無いものだけは「速報ではない」と明記して作る（late。ニュース用サイトマップ・速報投稿に出さない）
STALE_HOURS = 48
LATE_NOTE = (
    "\n\n# この発表の時期（必ず守る）\n"
    "この発表は{date}（日本時間）に公開されたもので、今日の速報ではありません。本文の冒頭近くで「{date}に発表された」と日付を書き、"
    "「本日」「たった今」「速報」など新しい発表のように見せる言い方をしないでください。題名にも【速報】などを付けないでください。"
)
QUALITY_LOG_KEEP = 300
# 1本あたりの目安（生成＋事実確認）。残り時間がこれを切ったら新しい記事に手を付けない
EST_SEC_PER_ARTICLE = 150
JST = timezone(timedelta(hours=9))
# 幅広い話題に投資・マネー／不動産を足したレーン（genres.WEB_GENRES。2026-11-03 オーナー決定）
BROAD_SLUGS = [g.slug for g in WEB_GENRES]
AI_SLUGS = {f["slug"] for f in feeds.FEEDS}

# 幅広い話題: 価値の薄いお知らせ（タイトルで判定）。人事・採用・小さな販促・ただの開店・会議の案内など
LOW_VALUE_RE = re.compile(
    r"人事|役員|異動|就任|退任|社長交代|採用|求人|新卒|中途|インターン|官庁訪問|職員|"
    r"説明会|相談会|セミナー|ウェビナー|勉強会|講座|講演|登壇|出展|展示会|ブース|"
    r"(?<!AI)(?<!ソース)オープン(?!AI|ソース)|開店|新店|移転|ポップアップ|"
    r"キャンペーン|プレゼント|抽選|クーポン|セール|割引|ポイント(還元|付与)|福袋|"
    r"受賞|アワード|表彰|認定を取得|認証を取得|取得しました|選出|ランクイン|周年|協賛|寄付|寄贈|贈呈|"
    # イベントの開催告知（東京都・企業ニュースルームに多い）
    r"を開催|の開催$|開催について|開催のお知らせ|ライブ配信|"
    r"お詫び|訂正|休業|営業時間|メンテナンス|システム障害|サービス障害|公開再開|"
    r"会議|審議会|委員会|部会|懇談会|議事|入札|調達|公募|募集|大臣会見|会見要旨|総理の動き|"
    r"ホワイトペーパー|資料(を)?公開|ダウンロード|導入事例|お客様の声|メディア掲載|出演|"
    r"届出情報の更新|データベース.*更新|を掲載しました|一覧へ|出張|視察|表敬|ビデオメッセージ|終了まで残り|"
    # 海外の調査会社の「市場レポート販売」告知（中身が薄く数字の出所も不明）
    r"市場(調査|分析)?レポート|調査資料|世界市場|年平均成長率|CAGR|百万米ドル|億米ドル",
)
# 幅広い話題: 扱わない話題（タイトル＋概要で判定）。死傷を伴う事件事故・ゴシップ・選挙・医療効果・噂
HARD_EXCLUDE_RE = re.compile(
    r"逮捕|容疑|送検|起訴|判決|事件|殺人|殺害|死亡事故|死傷|死者|負傷者|重傷|重体|遺体|行方不明|"
    r"訃報|逝去|死去|追悼|選挙|候補者|立候補|政党|支持率|不倫|熱愛|交際を|離婚|炎上|スキャンダル|"
    r"噂|うわさ|リーク|治る|治療効果|完治|特効|がんが消|効く|痩せる|やせる|ダイエット効果",
)

# 告知だけの記事（ウェビナー・イベント募集など）は、製品発表を伴わない限り記事にしない
EVENT_RE = re.compile(
    r"webinar|ウェビナー|セミナー|meetup|ミートアップ|hackathon|ハッカソン|register now|join us|"
    r"livestream|ライブ配信|イベント開催|開催のお知らせ|参加者募集|登壇|出展|展示会|"
    r"award|受賞|認証を取得|資金調達|採用|募集開始|キャンペーン|プレゼント",
    re.IGNORECASE,
)
NOTABLE_RE = re.compile(
    r"launch|introduc|announc|release|available|general availability|\bGA\b|new model|open[- ]source|unveil|debut|rolling out|"
    r"発表|提供開始|公開|リリース|一般提供|新機能|新モデル|オープンソース|搭載|利用可能|利用できる|日本語対応|対応開始|登場",
    re.IGNORECASE,
)

# 大きなニュース: 主要企業の新モデル・新製品（公式の発信のみ）。実行の最初に quality で作り、落とさない
MAJOR_RE = re.compile(
    r"GPT[-‐ ]?\d|(?<![A-Za-z])o[1-9](?:-mini|-pro)?(?![A-Za-z])|ChatGPT|(?<![A-Za-z])Sora(?![A-Za-z])|"
    r"Claude\s?(?:\d|Opus|Sonnet|Haiku)|Gemini\s?(?:\d|Ultra|Pro|Flash|Nano)|Gemma\s?\d|(?<![A-Za-z])Veo\s?\d|"
    r"Imagen\s?\d|Llama\s?\d|(?<![A-Za-z])Grok\s?\d|Apple Intelligence|iPhone\s?\d{2}|Pixel\s?\d{2}|Nemotron|"
    # 2026-10-27 に取りこぼした Genie 4・Siri AI を受けて追加（世界モデル・OS の大型アップデート・音声アシスタント）
    r"Genie\s?\d|(?<![A-Za-z])Siri(?![A-Za-z])|(?<![A-Za-z])i(?:Pad)?OS\s?\d{2}|macOS\s?\d{2}|Android\s?\d{2}|Windows\s?1\d|NotebookLM",
    re.IGNORECASE,
)
# 「GPT-6.1 Terra」「Genie 4: ...」のように名前だけの見出しも発表とみなす
MAJOR_HEAD_RE = re.compile(r"^\s*(?:Introducing\s+|新しい|新)?(?:GPT|Claude|Gemini|Gemma|Llama|Grok|Sora|Veo|Genie|Siri|o\d)\b", re.I)
# 会社そのものの大きな出来事（上場申請・買収）。その会社自身の公式発表（source_rank 0）なら製品名が無くても大きなニュース
BIG_EVENT_RE = re.compile(
    r"(?<![A-Za-z])IPO(?![A-Za-z])|(?<![A-Za-z])S-1(?![0-9])|initial public offering|go(?:es|ing)? public|上場|新規株式公開|"
    r"(?<![A-Za-z])acquir(?:es|ed|ing)(?![A-Za-z])|acquisition|merger|買収|経営統合",
    re.IGNORECASE,
)
MAJOR_MAX_RUNS = 3  # 失敗しても processed にせず、この回数の実行まで再挑戦する（本家の公式発表は MAJOR_MAX_RUNS_TOP）
MAJOR_MAX_RUNS_TOP = 5
MAJOR_ATTEMPTS = 3  # 1回の実行での生成の試行回数（通常は2）

# 配信（PR TIMES など）の生成前チェック。生成してから落ちると無料枠を2〜4回分むだにするため
WIRE_MIN_CORE_CHARS = 900
WIRE_LOW_VALUE_RE = re.compile(
    r"イベント開催|キャンペーン|セール|受賞|就任|人事|出店|開業|限定|先着|プレゼント|募集|セミナー|ウェビナー|"
    r"説明会|お知らせ|協賛|掲載"
)
WIRE_SUBSTANCE_RE = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])|新サービス|新製品|生成AI")
WIRE_FACT_RE = re.compile(
    r"\d[\d,]*\s*円|[¥￥]\s*\d|\d+(?:\.\d+)?\s*ドル|月額|税込|"
    r"\d{1,2}月\d{1,2}日.{0,20}?(?:発売|提供開始|販売開始|開始|リリース|公開)|(?:発売|提供開始|リリース)日"
)
# 配信の本文末尾の定型（会社概要・問い合わせ先など）。ここから先は中身に数えない
WIRE_TAIL_RE = re.compile(
    r"^\s*(?:[■【〈<《\[●◆]\s*)?(?:会社概要|企業概要|団体概要|法人概要|運営会社|会社情報|本件に関する|本リリースに関する|"
    r"報道関係者|お問い?合わせ|問い?合わせ先|About\b|このプレスリリース|すべての画像)"
    r"|^.{0,30}(?:とは|について)\s*[】]?\s*$",
    re.M,
)


def core_text(text: str) -> str:
    """配信の本文から末尾の定型（会社概要など）を落とした部分"""
    m_tail = WIRE_TAIL_RE.search(text or "")
    return text[: m_tail.start()] if m_tail else (text or "")


def is_wire(item: dict) -> bool:
    return feeds.feed_kind(item) == "wire"


def wire_precheck(item: dict, text: str) -> str | None:
    """配信の安い足切り（本文取得後・生成前）。除外理由か None"""
    core = core_text(text)
    if len(core) < WIRE_MIN_CORE_CHARS:
        return f"配信の本文が薄い（定型を除いて{len(core)}字 / 基準 {WIRE_MIN_CORE_CHARS}字）"
    head = f"{item['title']} {item.get('summary', '')[:300]}"
    if m_low := WIRE_LOW_VALUE_RE.search(head):
        # AI・新サービス・新製品の話で、価格か提供日が書いてあれば残す
        if not (WIRE_SUBSTANCE_RE.search(head) and WIRE_FACT_RE.search(f"{head}\n{core}")):
            return f"配信の価値が薄いお知らせ（{m_low.group(0)}）"
    return None


def is_major(item: dict, trend_index: dedupe.Index | None = None) -> bool:
    """大きなニュースか。公式の発信（配信・他社の対応告知は除く）で、主要企業の新モデル・新製品、
    または Googleトレンドの話題キューにも載っている話題"""
    if feeds.source_rank(item) > 2 or prefilter(item):
        return False
    title = item["title"]
    if feeds.source_rank(item) <= 1 and MAJOR_RE.search(title) and (NOTABLE_RE.search(title) or MAJOR_HEAD_RE.match(title)):
        return True
    if feeds.source_rank(item) == 0 and BIG_EVENT_RE.search(title):
        return True
    return bool(trend_index and trend_index.entries and trend_index.find(title))


def major_max_runs(item: dict) -> int:
    """大きなニュースを何回の実行まで再挑戦するか（本家・公式の発表は長めに粘る）"""
    return MAJOR_MAX_RUNS_TOP if feeds.source_rank(item) <= 1 else MAJOR_MAX_RUNS


def is_breaking(item: dict) -> bool:
    """速報としてすぐ SNS に出すか。大きなニュースのうち、本家の公式発信（source_rank 0〜1）で
    主要企業の新モデル・新製品の名前が見出しにあるものだけ（トレンドに載っただけの話題は含めない）"""
    title = item.get("title", "")
    if feeds.source_rank(item) > 1 or prefilter(item):
        return False
    if MAJOR_RE.search(title) and (NOTABLE_RE.search(title) or MAJOR_HEAD_RE.match(title)):
        return True
    return feeds.source_rank(item) == 0 and bool(BIG_EVENT_RE.search(title))


def sns_likely(item: dict) -> bool:
    """SNS（Threads・Bluesky）に出しそうな記事か。投稿は記事を作ったあとに scripts/threads_post.py が
    score()（誰でも反応できる話題か）で選ぶので、同じ目安（BROAD に当たり NICHE に当たらない）を
    元記事の見出しと発信元で先に見る。当たった記事だけ Claude で書く（1日の回数は llm.py の上限まで）"""
    try:
        import threads_post as tp  # noqa: PLC0415 - 投稿の選び方の目安を読むだけ
    except Exception:  # noqa: BLE001
        return False
    text = f"{item.get('title', '')} {item.get('source_name', '')}"
    return bool(tp.BROAD.search(text)) and not tp.NICHE.search(text)


def article_purpose(item: dict, lane: str, major: bool = False) -> str:
    """Claude API のどの段で書くか（llm.API_TIERS）。速報=Opus、SNS・読者像の中心=Sonnet、ほか=Haiku（予算で上下する）"""
    if major:
        return "breaking"
    if lane == "ai" and sns_likely(item):
        return "sns"
    if lane == "ai" or persona.is_persona_topic({"genreSlug": lane, "title": item.get("title", "")}):
        return "core"
    return "rest"


def rank_bucket(item: dict) -> int:
    """レーン内の並べ替え用。公式 → 官公庁・企業 → 他社の対応告知 → 配信"""
    return {0: 0, 1: 0, 2: 1, 3: 2}.get(feeds.source_rank(item), 3)


WEB_SOURCE_NOTE = """## 今回の取材対象（重要）
今回は動画ではなく、公式ブログ・ニュースルーム・プレスリリースなどの「文章」が一次資料です。
上の規則にある「動画」「映像」「音声」「スライド」は、すべて「元の資料（発表の文章）」と読み替えてください。本文には「元記事」という言葉を書かない（「〇〇の発表によると」と書く）。
- 元記事の文をそのまま訳したり書き写したりしない。内容を理解したうえで、自分の言葉で要約・解説する。
  引用は短いフレーズ（1文の一部、30字程度まで）にとどめ、「」で括る。
- 本文中で必ず一度は出典（発信元の名前）に触れる（例：「〇〇の公式ブログによると」）。
- 書いてよい事実は、元記事と「確認済みの事実の束」（プロンプトにあるときだけ）にあるものだけ。
  束の事実を使うときは、その文の直後に（ ）で出典と確認日を書く（例「（Anthropic公式の料金ページ、11/3確認）」）。
  どちらにも無い事実・数値・価格・日付・発言は作らない。一般的な背景知識で補う場合は「（編集部注）」と明記し、具体的な数値は書かない。
- 数字の計算（割合・差・増減率・ペース・平均・円への換算）は自分でしない。計算した数字が要るときは、
  「計算済みの数字」の欄にあるものだけを表記どおり（「約」を含む）に使い、「（サイソク編集部の計算）」と添える。
- 読者にとって重要で元記事から確認できない条件は、「〇〇はまだ発表されていません」と、何が未定かを主語にして1回だけ書く（「元記事には書かれていません」「言及なし」「確認できていません」とは書かない）。材料のない章は作らず、水増ししない。
- 編集部の見方は、確認済みの事実を根拠にし、「編集部の見方」と明示して事実と区別する。根拠のない体験談・比較・推測は書かない。
- 画面の道順（「設定 > 使用状況」）・メニュー名・ボタン名・英語の画面の文字（"Add source"）・番号つきの操作手順は、
  元記事か「確認済みの事実の束」（取りに行った公式ページ・当サイトのガイド）に書かれているものだけを書く。無ければ場所や手順を作らず、
  「公式ヘルプの手順を確認してください」と書く（根拠の無い手順は、プログラムがこの一文に置き換える）。
- サイソク（編集部）は自分では試していない。「筆者のテスト環境では」「私たちが試したところ」「実際に使ってみると」のように、
  自分で試した・使ったように書かない。元記事の書き手が試した結果なら「〇〇（媒体名）の記者が試したところ」と、誰が試したかを書く。"""

WEB_PROMPT_TEMPLATE = """以下の一次情報（公式発表の文章）を読み解き、日本語のSEO記事を作成してください。

# 元記事
- 発信元: {source_name}
- タイトル: {title}
- 公開日: {published_date}
- URL: {url}
- 本文（抜粋・自動抽出。メニュー等の残骸が混ざることがあるので無視する）:
<<<
{text}
>>>

# 品質基準（商業メディアの水準）
- 本文は元記事の情報量と読者の疑問に合わせた長さにする。文字数のために水増ししない。必要な条件や背景があれば省かない。
- 見出しを付けた章には、資料にある具体的な固有名詞・数値を含める。
- 本文の組み立ては下の「記事の型」に従う（決まった章立ては使わない。材料のあることだけを書く）。
- 表は「表の使い方」に従う（比べるときと早見表だけ）。

# 記事の型（body_markdown に Markdown で記述）

{outline}

# 各フィールドの要件
{field_notes}
- title: <<TITLE_STYLE>>
  主要SEOキーワードを前方に置き、**感嘆符・煽り語（衝撃／ヤバい／ついに／必見）は使わない**。
  元記事のタイトルの直訳にしない。
- seo_title: 検索結果に表示する短い題名。**全角24〜30文字**（英数字は半角0.5文字換算）。
  製品名・企業名・機能名を**先頭**に置き、何が分かる記事かを続ける。記号は「｜」「：」を最大1つまで。
  **新しい製品・機能・モデルの発表**なら、日本の人が実際に検索する形にする:「〇〇とは？日本で使える？料金も」「〇〇の使い方と料金｜日本での提供状況」など、
  名前の直後に「とは／使い方／料金／日本で使える」のどれかを入れる（発表直後は日本語の記事がまだ少なく、この形の検索で1位を取りやすい）。
- threads_post: Threads・X に出す投稿文。末尾の「Threads 紹介文（threads_post）のルール」と「書き出し」に従う（書き出し／要約すると、＋2文／問いかけ の3つのかたまり・URLなし）。
<<EASY_RULE>>
- lede: 記事冒頭のリード文。150〜200字。1文目で今回なにが新しいか（誰が・何を・いつ）を言い切り、2文目に一番大事な数字か条件を置く。発信元の名前を含める。
  報道記事のリードの文体（です・ます調で落ち着いた書き方）。よく知られた会社に「ChatGPTのOpenAI」のような説明を付けない。幼い言い方・話し言葉は使わない。
- description: 100〜130文字のメタディスクリプション。結論を含める。
- mentioned_tools: 記事中で言及した製品・サービス・企業の**正式名称**（3〜12個）。本文で触れたものだけ。
- slug_en: URL用の英小文字スラッグ。半角英数とハイフンのみ、3〜6単語、40文字以内。
- keywords: 検索されうる日本語キーワード 8〜12個。
- tags: 記事分類用のタグ 3〜6個。
- highlights: 記事のポイントちょうど3つ。各40〜60文字で、資料にある数値や固有名詞を含める。リードの文をくり返さない（リードに無い事実を入れる）。
- timetable: 文章が元なので **必ず空配列 []** にする。
- verdict: 読者が「自分は今これを使えるか」を判断するための判定。本文の「料金・提供プラン」「日本から使う方法」と矛盾させない。
  元記事に根拠がない項目は推測せず unknown / 不明 / null にする。
  - status: available（一般提供中）/ limited（ウェイトリスト・一部ユーザー・地域限定・法人限定・段階展開）/
    soon（提供予定）/ demo（研究・技術デモ・事例紹介で、すぐ使える製品がない）
  - japan: ok / partial（英語のみ・API経由のみ等）/ no / unknown（元記事に記載なし）
  - price: 14字以内。例「無料」「無料枠あり」「月20ドル〜」「API従量課金」「法人向け」「不明」
  - free: 無料で試せるなら true、有料のみ false、不明なら null
  - audience: いちばん恩恵を受ける読者を2〜8字で
  - take: サイソクの判断。25〜55字の1文で、読者が次に何をすべきか。煽り語は禁止。
- is_relevant: 読者にとって実質的な情報価値のある発表・解説なら true。
  イベント・ウェビナーの告知だけ、採用・受賞・資格取得・キャンペーンのお知らせ、宣伝だけで中身のないもの、
  AI・テクノロジーとほぼ無関係なものは false。
{topic_guard}
- skip_reason: is_relevant が false のときだけ理由を書く。
- genre: 記事の内容に最もふさわしいジャンルを次から1つ選ぶ（迷ったら ai）。
{genre_choices}

body_markdown 内では h1 (`#`) を使わないでください（タイトルは別途表示されます）。
"""
WEB_PROMPT_TEMPLATE = WEB_PROMPT_TEMPLATE.replace("<<TITLE_STYLE>>", headline_normalize.TITLE_STYLE_PROMPT)  # 見出しの書き方（scripts/headline_normalize.py）
WEB_PROMPT_TEMPLATE = WEB_PROMPT_TEMPLATE.replace("<<EASY_RULE>>", news_types.EASY_RULE)  # かんたん版の書き方（scripts/news_types.py）


BROAD_SOURCE_NOTE = """## 幅広い話題の編集ルール（必ず守る）
- 資料はプレスリリース・官公庁・企業・研究機関の公式発表。発表者の宣伝文句（「業界初」「最高」等）は事実として断定せず、
  「〇〇によると」「〇〇は…としています」と発信元に帰属させる。
- 読者に関係する条件（使える・買える・申し込めるか、対象者、期限）は、資料にあるときだけ本文に書く（無い項目の章は作らない）。
- 事件・事故の死傷者、特定個人のゴシップ、選挙・政党の話、病気が治る・効くといった医療効果、出典不明の噂は書かない。
  元記事がそれらを主題にしているなら is_relevant を false にする。"""

BROAD_PROMPT_TEMPLATE = """以下の一次情報（公式発表の文章）を読み解き、日本語のSEO記事を作成してください。

# 元記事
- 発信元: {source_name}
- タイトル: {title}
- 公開日: {published_date}
- URL: {url}
- 想定ジャンル: {genre_name}
- 本文（抜粋・自動抽出。メニュー等の残骸が混ざることがあるので無視する）:
<<<
{text}
>>>

# 品質基準
- 本文は元記事の情報量と読者の疑問に合わせた長さにする。文字数のために水増ししない。必要な条件や背景があれば省かない。
- 見出しを付けた章には、資料にある具体的な固有名詞・数値・日付を含める。
- 本文の組み立ては下の「記事の型」に従う（決まった章立ては使わない。材料のあることだけを書く）。
- 表は「表の使い方」に従う（比べるときと早見表だけ）。

# 記事の型（body_markdown に Markdown で記述）

{outline}

# 各フィールドの要件
{field_notes}
- title: <<TITLE_STYLE>>
  **感嘆符・煽り語（衝撃／ヤバい／ついに／必見／知らないと損）は使わない**。元記事のタイトルをそのまま使わない。
- seo_title: 全角24〜30文字。固有名詞（商品名・制度名・組織名）を先頭に置く。記号は「｜」「：」を最大1つまで。
- threads_post: Threads・X に出す投稿文。末尾の「Threads 紹介文（threads_post）のルール」と「書き出し」に従う（書き出し／要約すると、＋2文／問いかけ の3つのかたまり・URLなし）。
<<EASY_RULE>>
- lede: 150〜200字。1文目で今回なにが新しいか（誰が・何を・いつ）を言い切り、2文目に一番大事な数字か条件を置く。発信元の名前を含める。報道記事のリードの文体で、よく知られた会社に説明を付けず、幼い言い方・話し言葉は使わない。
- description: 100〜130文字のメタディスクリプション。結論を含める。
- mentioned_tools: 記事中で言及した商品・サービス・企業・制度の正式名称（0〜12個）。本文で触れたものだけ。
- slug_en: URL用の英小文字スラッグ。半角英数とハイフンのみ、3〜6単語、40文字以内。
- keywords: 検索されうる日本語キーワード 8〜12個。tags: 3〜6個。
- highlights: 記事のポイントちょうど3つ。各40〜60文字で、資料にある数値や固有名詞を含める。リードの文をくり返さない（リードに無い事実を入れる）。
- timetable: **必ず空配列 []**。
- verdict: 読者が使う・買う・申し込む・参加できるものの場合だけ埋める（基準: status available/limited/soon/demo、
  japan ok/partial/no/unknown、price 14字以内、free true/false/null、audience 2〜8字、take 25〜55字）。
  当てはまらない話題（研究成果・統計・業績・行政の方針など）では **null** にする。元記事に根拠のない項目は推測しない。
- is_relevant: 読者にとって実質的な情報（新しい商品・サービス・制度・研究成果・数字）があれば true。
{topic_guard}
- skip_reason: is_relevant が false のときだけ理由を書く。
- genre: 次から1つ（想定ジャンルが合っていればそれを使う）。
{genre_choices}

body_markdown 内では h1 (`#`) を使わないでください（タイトルは別途表示されます）。
"""
BROAD_PROMPT_TEMPLATE = BROAD_PROMPT_TEMPLATE.replace("<<TITLE_STYLE>>", headline_normalize.TITLE_STYLE_PROMPT)  # 見出しの書き方（scripts/headline_normalize.py）
BROAD_PROMPT_TEMPLATE = BROAD_PROMPT_TEMPLATE.replace("<<EASY_RULE>>", news_types.EASY_RULE)  # かんたん版の書き方（scripts/news_types.py）


def build_broad_prompt(item: dict, text: str, genre, context: str = "", outline: str | None = None) -> str:
    prompt = BROAD_PROMPT_TEMPLATE.format(
        source_name=item["source_name"],
        title=item["title"],
        published_date=item["published_at"].astimezone(JST).strftime("%Y-%m-%d"),
        url=item["url"],
        genre_name=f"{genre.slug}（{genre.name}）",
        text=text,
        outline=outline or news_types.prompt_block(item.get("newsType") or news_types.classify(item["title"], text, url=item.get("url", ""), genre=genre.slug),
                                                   genre_slug=genre.slug),
        field_notes=news_types.clean_field_notes(genre.field_notes),
        topic_guard=f"  **このジャンルの対象範囲**: {genre.topic_guard}",
        genre_choices="\n".join(f"  - {g.slug}: {g.name}" for g in ALL_GENRES),
    )
    return (prompt + context + extra_fields_block(genre.slug) + "\n\n" + persona.WORK_MONEY_FIELD
            + "\n\n" + m.threads_hooks.prompt_block())


def publish_blocked(record: dict) -> str | None:
    """公開しない理由（scripts/indexnow.py の publish_block_reason。検索に出せない記事は作っても取り下げるだけ）。
    2026-11-03 オーナー承認。PUBLISH_GATE=0 で止められる（緊急時だけ）"""
    if os.environ.get("PUBLISH_GATE", "1") == "0":
        return None
    return indexnow.publish_block_reason(record)


def broad_prefilter(item: dict) -> str | None:
    """幅広い話題の安い足切り。除外理由（文字列）か None"""
    title = item["title"]
    if HARD_EXCLUDE_RE.search(f"{title} {item.get('summary', '')[:200]}"):
        return "扱わない話題（事件事故・ゴシップ・選挙・医療効果・噂）"
    if len(title) < 10:
        return "タイトルが短すぎる（一覧リンク等）"
    if m_low := LOW_VALUE_RE.search(title):
        return f"価値の薄いお知らせ（{m_low.group(0)}）"
    if not item.get("section"):
        return "セクションを判定できない"
    # 投資・マネー／不動産／エンタメで扱わない候補（書き写し・告知・ゴシップ。scripts/genre_focus.py）
    if focus_reason := genre_focus.candidate_problem(item["section"], title, item.get("summary", "")):
        return focus_reason
    if item.get("section") == "sports":
        # スポーツは日本のスポーツだけ（世界的な大会は例外。scripts/persona.py の SPORTS_SCOPE_RULE）
        feed = feeds.FEEDS_BY_SLUG.get(item.get("source_slug", ""), {})
        ok, why = persona.sports_scope(f"{title} {item.get('summary', '')[:300]}", japanese_source=not feed.get("foreign"))
        if not ok:
            return f"スポーツの対象外（{why}）"
    if indexnow.candidate_off_focus(item.get("section", ""), title, item.get("summary", "")):
        # 作っても検索に出せず取り下げるだけ（2026-11-03 オーナー承認。scripts/indexnow.py の公開前の門）
        return "サイトの主題外（AI・お金・制度のお金の話・日本のスポーツのどれでもない）"
    return None


# --------------------------------------------------------------------------- #
# 話題のニュース（Googleトレンド → state/trend_queue.json）
# --------------------------------------------------------------------------- #

TREND_MIN_SOURCE_CHARS = 400
TREND_MIN_SOURCES = 2
TREND_MAX_SOURCES = 3
TREND_SOURCE_CAP = 5000  # 1報道あたりの文字数上限（プロンプトを無料枠の範囲に収める）

# 「使えるか」判定は話題によっては当てはまらないので null を許す
TREND_SCHEMA = copy.deepcopy(m.ARTICLE_SCHEMA)
TREND_SCHEMA["properties"]["verdict"]["nullable"] = True
TREND_SCHEMA["required"] = [k for k in TREND_SCHEMA["required"] if k != "verdict"]
# 「これまでの流れ」で実際に使った過去記事の slug（任意）
WEB_SCHEMA = copy.deepcopy(m.ARTICLE_SCHEMA)
for _schema in (WEB_SCHEMA, TREND_SCHEMA):
    _schema["properties"]["related_used"] = {"type": "ARRAY", "items": {"type": "STRING"}}
    _schema["propertyOrdering"].append("related_used")
    # ジャンル別の判定カード（スポーツ: いつ・放送/配信、制度: 開始日・期限）。任意の項目
    _verdict = _schema["properties"]["verdict"]
    _verdict["properties"]["when"] = {"type": "STRING"}
    _verdict["properties"]["broadcast"] = {"type": "STRING"}
    _verdict["propertyOrdering"] += ["when", "broadcast"]
    # よくある質問（3〜5問）と「今日やること」（2〜3個）。合うジャンルだけ。任意の項目
    _schema["properties"]["faq"] = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {"q": {"type": "STRING"}, "a": {"type": "STRING"}},
            "required": ["q", "a"],
            "propertyOrdering": ["q", "a"],
        },
    }
    _schema["properties"]["today_actions"] = {"type": "ARRAY", "items": {"type": "STRING"}}
    _schema["propertyOrdering"] += ["faq", "today_actions"]

# FAQ と「今日やること」を書くジャンル（AI・製品、お金・税・制度、使い方の話題）。スポーツ・エンタメ・科学は書かない
FAQ_GENRES = {"ai", "investment", "business", "public", "tech-gadget", "lifestyle", "real-estate"}


def schema_for(base: dict, genre_slug: str) -> dict:
    """ジャンルに合わせた出力の形。FAQ を書くジャンルでは faq と today_actions を必須にする（無料の軽量モデルが書き落とすため。2026-11-03）。
    それでも空・足りないときは fill_faq_actions が足りない欄だけ書き足す（記事全体は作り直さない）"""
    if genre_slug not in FAQ_GENRES:
        return base
    schema = copy.deepcopy(base)
    schema["required"] = list(dict.fromkeys(schema["required"] + ["faq", "today_actions"]))
    return schema
# 読者が使う・買う・申し込むものが無い「数字の話題」（統計・業績・倒産件数など）。判定カードは出さない
DATA_TOPIC_RE = re.compile(
    r"統計|倒産|件数|調査結果|決算|業績|自社株買い|株主還元|配当|(?<![A-Za-z])GDP(?![A-Za-z])|指数|物価|消費者物価|雇用統計|失業率|景気|"
    r"口座数|契約数|販売台数|出荷台数|上方修正|下方修正|前年同月比|前年比|月例経済|日銀短観|短観|有効求人倍率"
)
DATA_GENRES = {"business", "investment", "science", "real-estate"}
# 製品・サービスの発表ではないニュース（事故・情報流出・攻撃、買収・上場・人事などの会社の動き、表彰、統計・業績）。
# 「使える／提供前」の判定は出さない（2026-11-03 の画面の点検 A。北陸電力のメールアドレス流出に「使える・無料」と出ていた）。
# サイトの lib/verdict.ts の NON_PRODUCT_PATTERN と同じ文字列（tests/test_verdict_scope.py が照らし合わせる）。スポーツには使わない
NON_PRODUCT_RE = re.compile(
    r"流出(?!を防|対策)|(?:漏えい|漏洩)(?:し|が|の(?:可能性|恐れ|疑い))|不正アクセス(?!を防|対策)|不正利用(?!を防|対策)|サイバー攻撃(?:を受け|で|被害)|ランサムウェア被害|悪用を確認|悪用され|"
    r"障害が発生|大規模障害|で通信障害|通信障害が|に脆弱性|脆弱性が見つか|欠陥を発見|欠陥が見つか|インシデント|事件|逮捕|書類送検|訴訟|提訴|行政処分|業務改善命令|リコール|"
    r"買収|合併|経営統合|持株会社|分社|上場申請|新規上場|株式上場|資金調達|出資|子会社化|就任|退任|社長交代|人事異動|企業再編|連携協定|協定を締結|業務提携|資本提携|"
    r"表彰|受賞|選出|アワード|Awards?|"
    r"統計(?!的)|倒産|件数|調査結果|決算|業績|自社株買い|株主還元|配当|物価|雇用統計|失業率|景気|口座数|契約数|販売台数|出荷台数|上方修正|下方修正|前年同月比|前年比|突破|有効求人倍率|短観"
)

FAQ_TODAY_FIELD = """# faq と today_actions（記事ページの「よくある質問」「今日やること」欄。このジャンルでは必須）
- faq: 読者が検索しそうな疑問を必ず3〜5個（q と a）。質問は次の候補から、**この記事に答えがあるものだけ**選ぶ:
  何が発表された？／前と何が違う？／誰に関係する？／無料で使える？／日本で使える？／いつから？／いくら？／どうやって申し込む・始める？／注意点は？
  （「何が発表された？」「前と何が違う？」「誰に関係する？」はどの記事にも答えがあるので、ほかが足りないときに使う）
  q は製品名・制度名を入れた20〜40字の疑問文（例「Claude Ultraは無料で使える？」）。
  a は40〜120字。**body_markdown と元記事、「当サイトで確認済み」の欄に書かれた事実だけ**で答える。新しい数字・条件を足さない。
  答えが「元記事には書かれていません」になる質問は入れない。
- today_actions: 「今日やること」を必ず2〜3個。各20〜60字の1文で、読者が今日できる具体的な行動を書く（例「設定の使用状況画面で、今の上限を確かめる」）。
  画面の場所・ボタン名は元記事にあるときだけ書く。無ければ「公式の設定画面で確認する」のように書き、場所を作らない。
  提供予定・研究段階・統計や業績の話題でも、公式発表で条件を確かめる・自分の契約や設定を見直す・社内で共有する など、記事に沿った行動を書く。
  特定の銘柄・物件の売買はすすめない。"""
FAQ_TODAY_OFF = """# faq と today_actions
- このジャンルでは faq と today_actions は空配列 [] にする。"""

VERDICT_SPORTS = """# verdict（スポーツの判定カード。製品の言い方はしない）
- スポーツの話題では、verdict を「観戦・参加できるか」の判定として埋める。「日本で使える」「日本でも提供予定」「無料で試せる」のような製品の言い方は使わない。
  - status: available（チケット発売中・観戦や参加ができる）/ limited（抽選・会員先行・一部だけ）/ soon（発売前・開催前で、まだ申し込めない）。demo は使わない
  - japan: 常に ok（日本の大会・試合なので。カードには出さない）
  - when: 試合・大会・発売の日時（例「11月29日（土）19:00」）。元記事に無ければ「記載なし」
  - broadcast: テレビ放送・ネット配信（例「日本テレビ系で生中継、TVerで配信」）。元記事に書かれているときだけ。無ければ「記載なし」
  - price: チケット・参加費（例「3,000円〜」）。無ければ「記載なし」。free は無料なら true、有料なら false、不明なら null
  - audience: 誰向けか（例「現地観戦したい人」）。take: 次に何をすればいいか1文"""
VERDICT_PUBLIC = """# verdict（制度の判定カード）
- 制度・手続き・給付の話題では、verdict を「申し込めるか」の判定として埋める（会議の案内・統計・方針だけなら null）。
  - status: available（受付中・もう始まっている）/ limited（対象が限られる・一部の地域だけ）/ soon（これから始まる）/ demo（案・検討中・意見募集中）
  - japan: 常に ok（日本の制度なので。カードには出さない）
  - when: 開始日・申込期限（例「12月1日から／期限は1月31日」）。元記事に無ければ「記載なし」
  - price: 費用・金額（給付額・手数料など）。audience: 対象者（例「個人事業主」）"""
VERDICT_DATA = """# verdict（数字の話題）
- 統計・業績・決算・倒産件数・口座数・自社株買い・相場など、読者が使う・買う・申し込むものが無い話題では verdict は **null** にする。"""
VERDICT_NON_PRODUCT = """# verdict（製品・サービスの発表でない話題）
- 事故・情報流出・サイバー攻撃・障害、買収・上場・出資・人事などの会社の動き、表彰・受賞、統計・調査結果のニュースでは verdict は **null** にする
  （「使える」「提供予定」は製品・サービスが使えるかの答え。これらの記事には当てはまらない）。"""


def extra_fields_block(genre_slug: str) -> str:
    """ジャンルに合わせた faq・today_actions・判定カードの書き方（プロンプトの末尾に足す）"""
    parts = [FAQ_TODAY_FIELD if genre_slug in FAQ_GENRES else FAQ_TODAY_OFF]
    if genre_slug == "sports":
        parts.append(VERDICT_SPORTS)
    elif genre_slug == "public":
        parts.append(VERDICT_PUBLIC)
    if genre_slug in DATA_GENRES:
        parts.append(VERDICT_DATA)
    if genre_slug != "sports":
        parts.append(VERDICT_NON_PRODUCT)
    return "\n\n" + "\n\n".join(parts)


def verdict_kind(genre_slug: str) -> str:
    """判定カードの種類（サイトの lib/verdict.ts の verdictKind と同じ）"""
    return {"sports": "sports", "public": "public"}.get(genre_slug, "product")


def genre_verdict(raw: object, genre_slug: str, record: dict) -> dict | None:
    """生成された判定をジャンルに合わせて整える。
    数字の話題（統計・業績など）と、製品・サービスの発表でない話題（流出・買収・表彰など。NON_PRODUCT_RE）は判定カードを出さない。スポーツ・制度は種類（kind）と「いつ」「放送・配信」を残す。
    製品の判定で japan が unknown のままなら、日本での提供の表（content/japan_availability.json）で直す（一般提供中のときだけ）"""
    verdict = m.clean_verdict(raw)
    if not verdict:
        return None
    text = f"{record.get('title', '')} {record.get('seoTitle', '')} {' '.join(record.get('tags') or [])}"
    if genre_slug in DATA_GENRES and DATA_TOPIC_RE.search(text):
        return None
    kind = verdict_kind(genre_slug)
    # 製品・サービスの発表でない話題（流出・買収・表彰・統計など）は判定を付けない。見出しとタグで見る（サイトの verdictOf と同じ）
    if kind != "sports" and NON_PRODUCT_RE.search(f"{record.get('title', '')} {' '.join(record.get('tags') or [])}"):
        return None
    if kind != "product":
        verdict["kind"] = kind
        verdict["japan"] = "ok"  # 日本の大会・制度。カードには出さない
        for key in ("when", "broadcast") if kind == "sports" else ("when",):
            value = re.sub(r"\s+", " ", str((raw or {}).get(key) or "")).strip()[:40] if isinstance(raw, dict) else ""
            if value:
                verdict[key] = value
        if verdict["status"] == "demo" and kind == "sports":
            verdict["status"] = "soon"
        return verdict
    return japan_availability.apply_to_verdict(verdict, record)


# 料金・プラン・リリースノートのページの見張り（page_watch.py / pricing_watch.py）から来た候補の書き方（2026-11-03）。
# 差分の行（「- 追加: Plan: Pro, Feature: …」）をそのまま記事に貼った例があったため
WATCH_DIFF_NOTE = """## 今回の元記事は「公式ページの変更点」です（重要）
- 元記事の「- 削除: …」「- 追加: …」の行は、公式ページの表示の差分（英語のまま・表の1行を横に並べたもの）です。記事にそのまま貼らない。
- 何が・どう変わったかを、自然な日本語の文で説明する。変わったものが3つ以上なら「比べること | 前 | 今回」の表にする（英語の項目名は日本語に訳し、
  プラン名・製品名・金額だけ英語・数字のまま残す）。「Plan: Pro, Feature: …」のような英語の書き方を記事に出さない。
- 差分だけでは分からない条件（いつから・誰が対象か・日本での扱い）は、元記事と確認済みの事実の束にあるときだけ書き、無ければ「公式ページには書かれていません」と書く。"""

FILL_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "faq": WEB_SCHEMA["properties"]["faq"],
        "today_actions": WEB_SCHEMA["properties"]["today_actions"],
    },
    "required": ["faq", "today_actions"],
    "propertyOrdering": ["faq", "today_actions"],
}
FILL_SYSTEM = "あなたはニュースサイトの編集者です。できあがった記事に「よくある質問」と「今日やること」の欄だけを書き足します。出力は指定の JSON だけ。"


def fill_faq_actions(record: dict, source_text: str = "", context: str = "") -> list[str]:
    """FAQ を書くジャンルで faq / todayActions が無い・足りない記事に、足りない欄だけを書き足す（安いモデル。本文は変えない）。
    書き足せた欄の名前を返す。答えの事実は、このあと品質ゲートの事実確認で本文・元記事と突き合わせる"""
    if record.get("genreSlug") not in FAQ_GENRES:
        return []
    need = [k for k in ("faq", "todayActions") if not record.get(k)]
    if not need:
        return []
    verdict = record.get("verdict") or {}
    prompt = (
        f"{FAQ_TODAY_FIELD}\n\n# 書き足す欄\n" + "・".join("faq" if k == "faq" else "today_actions" for k in need)
        + "（ほかの欄は空配列 [] でよい）\n\n"
        f"# 記事\nタイトル: {record.get('title', '')}\nリード: {record.get('lede', '')}\n"
        f"判定: 提供状況={verdict.get('status')} 日本={verdict.get('japan')} 料金={verdict.get('price')}\n\n"
        f"本文:\n<<<\n{(record.get('body') or '')[:8000]}\n>>>\n\n"
        f"# 元記事（抜粋）\n<<<\n{(source_text or '')[:6000]}\n>>>"
        + (f"\n\n# 当サイトで確認済み\n{context[:3000]}" if context.strip() else "")
    )
    data, model = llm.generate_json(FILL_SYSTEM, prompt, FILL_SCHEMA, temperature=0.3, max_output_tokens=4096, tier=quality.CHECK_TIER,
                                    purpose="helper")
    added = []
    if "faq" in need and (faq := clean_faq(data.get("faq"))):
        record["faq"] = faq
        added.append("faq")
    if "todayActions" in need and (actions := clean_actions(data.get("today_actions"))):
        record["todayActions"] = actions
        added.append("todayActions")
    if added:
        log(f"   [fill] 足りない欄だけ書き足した: {'・'.join(added)}（{model}）")
    else:
        log(f"   [fill] 書き足せなかった（{model}）: {'・'.join(need)}")
    return added


def pre_gate(record: dict, source_text: str = "", context: str = "") -> None:
    """品質ゲートの前の安い手直し（通常の実行と試し書きの道具が同じものを使う）:
    足りない FAQ・今日やることの書き足し → 判定カードを本文にそろえる・紹介文の「要約すると、」・言葉の問題の文だけ直す"""
    try:
        fill_faq_actions(record, source_text, context)
    except Exception as error:  # noqa: BLE001 - 書き足せなくても記事は出せる（欄を出さないだけ）
        log(f"   [fill] 失敗（{str(error)[:100]}）")
    quality.pre_gate_repair(record, source_text, log=log)


def write_tier(tier: str) -> str:
    """本文を書く段の tier。通常（bulk）の記事も、本文は強い無料モデルから書く（llm.py の tier "write"）。
    軽量モデルは事実確認・手直しなど小さな作業（quality.CHECK_TIER）に残す"""
    return "write" if tier == "bulk" else tier


def clean_faq(raw: object) -> list[dict]:
    """生成された faq を整える（3〜5問。足りなければ出さない）"""
    out, seen = [], set()
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        q = re.sub(r"\s+", " ", str(row.get("q") or "")).strip()
        a = re.sub(r"\s+", " ", str(row.get("a") or "")).strip()
        if not q or not a or q in seen or quality.FAQ_UNANSWERED_RE.search(a):
            continue
        seen.add(q)
        out.append({"q": q[:60], "a": a[:200]})
    return out[:5] if len(out) >= 3 else []


def clean_actions(raw: object) -> list[str]:
    out = []
    for value in raw if isinstance(raw, list) else []:
        # 先頭の箇条書きの印（「・」「- 」「1. 」「2）」）だけ外す。「2026年11月10日に…」の年の数字は消さない
        text = re.sub(r"^(?:[・\-*]\s*|\d{1,2}\s*[.)）]\s*)", "", re.sub(r"\s+", " ", str(value or "")).strip().lstrip("　 ")).strip()
        if text and text not in out:
            out.append(text[:90])
    return out[:3] if len(out) >= 2 else []


SEO_MAX_WIDTH = 30


def _width(text: str) -> float:
    """全角1・半角0.5で数えた幅"""
    return sum(0.5 if ord(c) < 0x2E80 else 1 for c in text)


def fallback_seo_title(title: str) -> str:
    """seo_title が空のときの短い題名。見出しを区切り（空白・読点・句点・？）ごとに足していき、全角30字に収まるところまで。
    区切りでうまく短くできない（18字未満になる）ときは、言葉の途中で切らず見出しのまま（検索結果では Google が省略する）"""
    title = re.sub(r"\s+", " ", title or "").replace("！", "").replace("!", "").strip()
    if _width(title) <= SEO_MAX_WIDTH:
        return title
    out = ""
    # 半角の空白は日本語のあとだけで区切る（「BANK ACADEMY」のような英語の名前の途中で切らない）
    for part in re.split(r"(?<=[　、。？?])|(?<=[^\x00-\x7f] )", title):
        if _width(out + part) > SEO_MAX_WIDTH:
            break
        out += part
    out = out.rstrip("、。・｜：:| 　")
    return out if _width(out) >= 18 else title


FIRST_PERSON_FIX_RE = re.compile(r"筆者(?=の|が|は|も|1台|１台)")


def fix_press_first_person(record: dict, outlet: str) -> None:
    """報道が元の記事で、元記事の「筆者」（他社の記者）をサイソクの話と読めないよう「〇〇の記者」に直す。record をその場で書き換える"""
    for key in ("body", "easy", "lede"):
        text = record.get(key)
        if isinstance(text, str) and "筆者" in text:
            record[key] = FIRST_PERSON_FIX_RE.sub(f"{outlet}の記者", text)
    record["highlights"] = [FIRST_PERSON_FIX_RE.sub(f"{outlet}の記者", h) for h in record.get("highlights") or []]

TREND_SOURCE_NOTE = """## 今回の取材対象（重要）
今回は動画ではなく、日本でいま話題になっているキーワードと、それを報じた複数の報道記事が資料です。
上の規則にある「動画」「映像」「音声」「スライド」は、すべて「関連報道」と読み替えてください。
- 報道の文をそのまま書き写さない。内容を理解したうえで自分の言葉で要約・解説する。
  引用は短いフレーズ（30字程度まで）にとどめ、「」で括る。
- 事実を書くたびに「〇〇（媒体名）によると」のように出典の媒体名を示す。複数の報道を突き合わせて書く。
- 報道に書かれていない事実・数値・日付・発言は作らない。

## トレンド記事の編集ルール（必ず守る）
{rules}"""

TREND_PROMPT_TEMPLATE = """日本でいま話題になっているキーワードについて、関連報道を読み解き、日本語のSEO記事を作成してください。

# 話題
- キーワード: {keyword}
- 話題の度合い: {summary}
- 想定ジャンル: {genre_name}

# 関連報道（本文は自動抽出。メニュー等の残骸は無視する）
{sources}

# 品質基準
- 本文は報道の情報量と読者の疑問に合わせた長さにする。文字数のために水増ししない。必要な条件や背景があれば省かない。
- 本文の組み立ては下の「記事の型」に従う（決まった章立ては使わない。材料のあることだけを書く）。
- 表は「表の使い方」に従う（比べるときと早見表だけ）。

# 記事の型（body_markdown に Markdown で記述）
{outline}

# 各フィールドの要件
- title: <<TITLE_STYLE>>
- seo_title: 全角24〜30文字。検索されるキーワードを先頭に置く。記号は「｜」「：」を最大1つまで。
- threads_post: Threads・X に出す投稿文。末尾の「Threads 紹介文（threads_post）のルール」と「書き出し」に従う（書き出し／要約すると、＋2文／問いかけ の3つのかたまり・URLなし）。
<<EASY_RULE>>
- lede: 150〜200字。1文目で何が起きたか（今回の新しい事実）を言い切り、2文目になぜ話題か・一番大事な数字を置く。報道記事のリードの文体で、よく知られた会社に説明を付けず、幼い言い方・話し言葉は使わない。
- description: 100〜130文字。
- mentioned_tools: 記事中で言及した製品・サービス・企業・制度の正式名称（0〜12個）。
- slug_en: 英小文字とハイフン、3〜6単語、40文字以内。
- keywords: 8〜12個。tags: 3〜6個。
- highlights: ちょうど3つ、各40〜60文字。報道にある数値や固有名詞を含める。リードの文をくり返さない。
- timetable: **必ず空配列 []**。
- verdict: 話題がサービス・製品・制度など読者が使う/申し込むものの場合だけ埋める（基準: status available/limited/soon/demo、
  japan ok/partial/no/unknown、price 14字以内、free true/false/null、audience 2〜8字、take 25〜55字）。
  当てはまらない話題（相場の動き・業界ニュースなど）では **null** にする。
- is_relevant: サイトの軸（テクノロジー・お金・住まい）について読者に実質的な情報があれば true。
  事件・事故・災害・政治・芸能・スポーツが中心の話題、報道が薄く事実が確認できない話題は false。
- skip_reason: is_relevant が false のときだけ。
- genre: 次から1つ（想定ジャンルが合っていればそれを使う）。
{genre_choices}

body_markdown 内では h1 (`#`) を使わないでください。
"""
TREND_PROMPT_TEMPLATE = TREND_PROMPT_TEMPLATE.replace("<<TITLE_STYLE>>", headline_normalize.TITLE_STYLE_PROMPT)  # 見出しの書き方（scripts/headline_normalize.py）
TREND_PROMPT_TEMPLATE = TREND_PROMPT_TEMPLATE.replace("<<EASY_RULE>>", news_types.EASY_RULE)  # かんたん版の書き方（scripts/news_types.py）


def fetch_trend_sources(item: dict) -> list[dict]:
    """news.google.com 以外の報道 URL から本文を取り、400字以上取れたものを最大3本返す。"""
    got = []
    for source in item.get("sources") or []:
        if len(got) >= TREND_MAX_SOURCES:
            break
        url = source.get("url") or ""
        if not url or urllib.parse.urlsplit(url).netloc.endswith("news.google.com"):
            continue
        try:
            page = feeds.fetch_article_text(url)
        except Exception as error:  # noqa: BLE001
            log(f"   [warn] 報道の取得に失敗: {source.get('source')}（{str(error)[:60]}）")
            continue
        if len(page["text"]) < TREND_MIN_SOURCE_CHARS:
            continue
        got.append({**source, "text": page["text"][:TREND_SOURCE_CAP], "image": page.get("image")})
    return got


def trend_source_name(fetched: list[dict]) -> str:
    names = list(dict.fromkeys(s.get("source") for s in fetched if s.get("source")))
    if not names:
        return "話題のニュース"
    return "・".join(names[:2]) + (" ほか" if len(names) > 2 else "")


def build_trend_prompt(item: dict, fetched: list[dict], genre, context: str = "") -> str:
    blocks = []
    for n, s in enumerate(fetched, start=1):
        blocks.append(
            f"## 報道{n}: {s.get('source') or '不明'}「{s.get('title', '')}」（{s.get('published', '')}）\n"
            f"URL: {s['url']}\n<<<\n{s['text']}\n>>>"
        )
    joined = "\n".join(s.get("text", "")[:1500] for s in fetched)
    item["newsType"] = item.get("newsType") or news_types.classify(item["title"] + " " + " ".join(s.get("title", "") for s in fetched), joined,
                                                                   genre=genre.slug)
    prompt = TREND_PROMPT_TEMPLATE.format(
        keyword=item["title"],
        summary=item.get("summary", ""),
        genre_name=genre.name,
        sources="\n\n".join(blocks),
        outline=news_types.prompt_block(item["newsType"], genre_slug=genre.slug, source_kind="trend"),
        genre_choices="\n".join(f"  - {g.slug}: {g.name}" for g in GENRES),
    )
    return (prompt + context + extra_fields_block(genre.slug) + "\n\n" + persona.WORK_MONEY_FIELD
            + "\n\n" + m.threads_hooks.prompt_block())


def build_trend_record(item: dict, fetched: list[dict], data: dict, model_name: str, voice_key: str) -> dict:
    genre = GENRES_BY_SLUG.get(item.get("genre") or "", AI)
    pseudo = {
        "id": item["id"],
        "url": fetched[0]["url"],
        "title": item["title"],
        "source_name": trend_source_name(fetched),
        "source_slug": "trends",
        "published_at": item["published_at"],
        "newsType": item.get("newsType") or "",
    }
    image = next((s["image"] for s in fetched if s.get("image")), "")
    record = build_web_record(pseudo, {**data, "genre": genre.slug}, {"image": image}, model_name, voice_key)
    record.update(
        {
            "channel": "話題のニュース",
            "channelSlug": "trends",
            "disclaimer": web_disclaimer(genre).replace("公式発表の文章", "複数の報道"),
            "trendKeyword": item["title"],
            "sources": [{"title": s.get("title", ""), "url": s["url"], "source": s.get("source", "")} for s in fetched],
        }
    )
    return record


def log(message: str) -> None:
    print(message, flush=True)


def build_prompt(item: dict, text: str, context: str = "", outline: str | None = None) -> str:
    genre = AI
    prompt = WEB_PROMPT_TEMPLATE.format(
        source_name=item["source_name"],
        title=item["title"],
        published_date=item["published_at"].astimezone(JST).strftime("%Y-%m-%d"),
        url=item["url"],
        text=text,
        outline=outline or news_types.prompt_block(item.get("newsType") or news_types.classify(item["title"], text, url=item.get("url", ""))),
        field_notes=news_types.clean_field_notes(genre.field_notes),
        topic_guard=f"  **このジャンルの対象範囲**: {genre.topic_guard}" if genre.topic_guard else "",
        genre_choices="\n".join(f"  - {g.slug}: {g.name}（{g.description[:48]}…）" for g in GENRES),
    )
    return (prompt + context + extra_fields_block(genre.slug) + "\n\n" + persona.WORK_MONEY_FIELD
            + "\n\n" + m.threads_hooks.prompt_block())


def check_context_for(title: str, text: str, related: list[dict], bundle: facts_bundle.Bundle | None = None) -> str:
    """事実確認に渡す確認済みの事実（過去記事＋日本での提供の表＋確かな事実の束・計算済みの数字）"""
    parts = (archive.check_block(related), japan_availability.check_block(title, text), bundle.check_block() if bundle else "")
    return "\n\n".join(x for x in parts if x)


def compose(item: dict, text: str, lane: str, related: list[dict], voice_text: str, page: dict | None = None):
    """1本の記事の生成に渡すもの一式（通常の実行と、試し書きの道具が同じものを使う）。
    戻り値: (genre, system, prompt, schema, kind, check_context)。確かな事実の束も欲しいときは compose_with_facts"""
    return compose_with_facts(item, text, lane, related, voice_text, page)[:6]


def compose_with_facts(item: dict, text: str, lane: str, related: list[dict], voice_text: str, page: dict | None = None,
                       bundle: facts_bundle.Bundle | None = None):
    """compose と同じものに、確かな事実の束（facts_bundle.Bundle）を足して返す。
    page（元記事の取得結果）を渡すと、元記事の中のリンクを最大2本追いかけて束に入れる。
    戻り値: (genre, system, prompt, schema, kind, check_context, bundle)"""
    # 先に資料だけで種類を見て、深く読む種類（決算・脆弱性・製品・制度…）なら束を作るときにリンク先を長めに読む（scripts/news_types.py）
    pre_type = news_types.classify(item["title"], text, url=item.get("url", ""), genre="" if lane == "ai" else lane, item=item)
    if bundle is None:
        try:
            # 速報（速報レーン・大きなニュース）は待たせないよう、リンク先は1本だけ（深く読む種類は2本）
            fast = item.get("fast") or item.get("major")
            deep = pre_type in source_links.TYPE_EXCERPT_CAP
            bundle = facts_bundle.build(item, text, related, lane, page, follow_links=page is not None, log=log,
                                        max_links=(2 if deep else 1) if fast
                                        else (GATE_RETRY_LINKS if item.get("gate_retry") else (3 if deep else 2)),
                                        news_type=pre_type)
        except Exception as error:  # noqa: BLE001 - 束が作れなくても記事は今までどおり作る
            log(f"   [facts] 確かな事実の束を作れませんでした（{str(error)[:80]}）")
            bundle = facts_bundle.Bundle()
    if not bundle.empty():
        log(f"   [facts] 束 {len(bundle.groups)} 欄（{'・'.join(g.label for g in bundle.groups)}）/ 計算済みの数字 {len(bundle.computed)} 個"
            f"{' / 料金の表を差し込む' if bundle.ladder else ''}{' / お金・制度の章立て' if bundle.money else ''}")
    # ニュースの種類（決算・脆弱性・製品・料金・提携・制度・調査…）を決め、その種類の型で書かせる（scripts/news_types.py。2026-11-03 オーナー決定）
    item["newsType"] = news_types.classify(item["title"], text, url=item.get("url", ""), genre="" if lane == "ai" else lane, bundle=bundle,
                                           item=item)
    log(f"   [type] {news_types.get(item['newsType']).label}（{item['newsType']}）")
    outline = news_types.prompt_block(item["newsType"], genre_slug=lane)
    context = archive.prompt_block(related) + japan_availability.prompt_block(item["title"], text) + bundle.prompt_block()
    if item.get("late"):
        context += LATE_NOTE.format(date=f"{item['published_at'].astimezone(JST):%Y年%m月%d日}")
    secondary_note = f"\n\n{SECONDARY_SOURCE_NOTE.format(name=item['source_name'])}" if item.get("secondary") else ""
    watch_note = f"\n\n{WATCH_DIFF_NOTE}" if item.get("watch") else ""
    if lane == "ai":
        genre = AI
        system = (f"{m.SYSTEM_INSTRUCTION_BASE}\n\n{AI.persona}\n\n{WEB_SOURCE_NOTE}{secondary_note}{watch_note}\n\n"
                  f"{glossary.prompt_block()}\n\n## 今回の語り口\n{voice_text}")
        prompt, schema, kind = build_prompt(item, text, context, outline), schema_for(WEB_SCHEMA, AI.slug), "ai"
    else:
        genre = GENRES_BY_SLUG[lane]
        system = (
            f"{m.SYSTEM_INSTRUCTION_BASE}\n\n{genre.persona}\n\n{WEB_SOURCE_NOTE}{secondary_note}{watch_note}\n\n{BROAD_SOURCE_NOTE}"
            f"\n\n{glossary.prompt_block()}\n\n## 今回の語り口\n{voice_text}"
        )
        prompt, schema, kind = build_broad_prompt(item, text, genre, context, outline), schema_for(TREND_SCHEMA, genre.slug), "broad"
    return genre, system, prompt, schema, kind, check_context_for(item["title"], text, related, bundle), bundle


def prefilter(item: dict) -> str | None:
    """API を呼ぶ前の安い足切り。除外理由（文字列）か None"""
    title = item["title"]
    if EVENT_RE.search(title) and not NOTABLE_RE.search(title):
        return "告知・お知らせ系"
    return None


SECONDARY_SOURCE_NOTE = """## 今回の元記事は報道です（重要）
今回の元記事は企業の公式発表ではなく、報道機関「{name}」の記事です（公式の発表文が見つからなかったため）。
- リードと本文の最初の段落で「{name}の報道によると」のように、報道が出典であることを必ず書く。見出しでも公式発表と断定しない。
- 元記事が紹介している企業の発表・発言は「（{name}によると）」と添えて書き、元記事に無い公式の数字・発言・引用は作らない。
- 報道の見立て・推測・未確認の情報は事実として書かない。元記事の文を書き写さず、自分の言葉で要約する。
- 元記事の「筆者」「私」「記者」は、サイソクではなく{name}の記者のこと。「{name}の記者のテスト環境では」のように書き換え、
  サイソクが自分で試したように読める書き方（「筆者のテスト環境では」「私たちが試した」）はしない。"""


def source_age(item: dict, now: datetime | None = None) -> timedelta | None:
    """元の発表からの経過時間（発表の時刻が分からないときは None）"""
    published = item.get("published_at")
    if not isinstance(published, datetime) or item.get("published_guessed") or item.get("date_from_first_seen"):
        return None
    return (now or datetime.now(timezone.utc)) - published


def cap_with_page_date(item: dict, page: dict | None) -> bool:
    """記事ページに書かれた公開日時で発表の時刻を直す（フィードに日付が無い・初めて見た時刻で代用した・
    フィードの日付が更新日で新しく見える、のいずれか）。直したら True"""
    found = feeds.page_published(page)
    if not found or found > datetime.now(timezone.utc) + timedelta(hours=1):
        return False
    current = item.get("published_at")
    guessed = item.get("published_guessed") or item.get("date_from_first_seen") or not isinstance(current, datetime)
    # 日付だけ（00:00）のページは、フィードの時刻より1日以上古いときだけ使う
    if guessed or found < current - timedelta(hours=24 if found.strftime("%H:%M:%S") == "00:00:00" else 6):
        item["published_at"] = found
        item["published_guessed"] = False
        item["date_from_first_seen"] = False
        item["published_from_page"] = True
        return True
    return False


def is_stale(item: dict) -> bool:
    age = source_age(item)
    return bool(age and age > timedelta(hours=STALE_HOURS))


def source_header(item: dict) -> str:
    """事実確認に渡す元記事の情報（発信元・公開日時・URL）。記事に書いた発信元名・日付を「創作」と誤判定させない"""
    published = item.get("published_at")
    when = ""
    if published and not item.get("published_guessed"):
        when = f"{published.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC（日本時間 {published.astimezone(JST):%Y-%m-%d %H:%M}）"
    kind = f"報道機関（{item['source_name']}）" if item.get("secondary") else "公式の発表"
    return (
        f"[元記事の情報] 発信元: {item['source_name']}（{kind}） / 公開日時: {when or '不明（検知した時刻で代用）'} / URL: {item['url']}\n"
        f"タイトル: {item['title']}"
    )


def web_disclaimer(genre) -> str:
    text = genre.disclaimer.replace("元動画の映像・音声をAIが解析して", "公式発表の文章をもとにAIが要約・解説して")
    if "文章をもとに" in text:
        return text
    if text == genre.disclaimer:
        text = "本記事は公式発表の文章をもとにAIが要約・解説して自動生成したものです。" + text
    return text


def build_web_record(item: dict, data: dict, page: dict, model_name: str, voice_key: str,
                     bundle: facts_bundle.Bundle | None = None) -> dict:
    resolved = GENRES_BY_SLUG.get(str(data.get("genre", "")).strip(), AI)
    thumbnail = page.get("image") or item.get("image") or ""
    record = {
        "slug": m.unique_slug(m.slugify(data.get("slug_en", "")), item["id"][:8]),
        "sourceType": "web",
        "sourceUrl": item["url"],
        "sourceName": item["source_name"],
        "title": str(data["title"]).strip(),
        # 検索結果の短い題名。空なら見出しから作る（空のままだと検索結果に長い見出しがそのまま出る）
        "seoTitle": str(data.get("seo_title", "")).strip() or fallback_seo_title(str(data["title"])),
        "threadsPost": m.normalize_body(str(data.get("threads_post", ""))).strip(),
        "threadsHook": m.threads_hooks.clean_hook(data.get("threads_hook")),
        "threadsStyle": m.threads_hooks.POST_STYLE,
        "writingVoice": voice_key,
        "lede": m.normalize_body(data.get("lede", "")),
        "description": str(data["description"]).strip(),
        "keywords": m.clean_list(data.get("keywords"), 14),
        "tags": m.clean_list(data.get("tags"), 8),
        "highlights": m.clean_list(data.get("highlights"), 3),
        "timetable": [],
        "verdict": None,  # 下で genre_verdict（ジャンル別の判定・日本での提供の表）
        # 「仕事・お金にどう活かせる？」欄（scripts/persona.py。品質ゲートの形・事実確認の対象）
        "workMoney": persona.clean_work_money(data.get("work_money")),
        "body": m.normalize_body(data["body_markdown"]),
        "easy": m.normalize_body(str(data.get("easy_markdown", ""))),
        "channel": item["source_name"],
        "channelId": "",
        "channelSlug": item["source_slug"],
        "genre": resolved.name,
        "genreSlug": resolved.slug,
        "disclaimer": (
            f"本記事は報道（{item['source_name']}）の記事をもとにAIが要約・解説して自動生成したものです。"
            "誤りが含まれる可能性があるため、正確な情報は元の報道と公式発表をご確認ください。"
            if item.get("secondary") else web_disclaimer(resolved)
        ),
        "videoId": "",
        "videoUrl": "",
        "videoTitle": item["title"],
        "publishedAt": item["published_at"].astimezone(timezone.utc).isoformat(),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "thumbnail": thumbnail,
        "model": model_name,
        "affiliates": [],
    }
    if not record["body"].strip():
        raise ValueError("本文が空です")
    if item.get("newsType"):
        record["newsType"] = item["newsType"]  # ニュースの種類（scripts/news_types.py）。判定カードの有無・点検に使う
    record["verdict"] = genre_verdict(data.get("verdict"), resolved.slug, record)
    if record.get("newsType") and record["verdict"] and not news_types.allows_verdict(record["newsType"]) \
            and record["newsType"] not in ("general", "explainer"):
        record["verdict"] = None  # 決算・脆弱性・提携・調査・人事などに「使えるか」の判定カードを出さない（報告の自動ゲート17・台帳 A8）
    # 判定を日本での提供の表で直したら、本文の「日本で使える？」の「まだ分かりません」も表の答えにそろえる
    record["body"] = japan_availability.fix_japan_section(record["body"], record)
    fix_ui_steps(record, item, page, bundle)
    if bundle is not None and not bundle.empty():
        strip_fact_ids(record)
        # 料金の段の表はプログラムが差し込む（LLM に表を書かせない）。束の中身は品質ゲートの数字・出典の照合に使う（書き出す前に外す）
        record["body"] = facts_bundle.insert_ladder(record["body"], bundle)
        record["_facts"] = bundle.meta()
        if bundle.ladder and bundle.ladder.get("product") and "markdown" in bundle.ladder:
            # 記事ページは、本文に差し込んだ表の場所に、毎日更新の料金表（components/PriceTable.tsx）を出す
            record["pricingProduct"] = bundle.ladder["product"]
            if bundle.ladder.get("compare"):
                record["pricingCompare"] = bundle.ladder["compare"]
    # よくある質問（3〜5問）と「今日やること」（2〜3個）。合うジャンルだけ（FAQ_GENRES）
    if resolved.slug in FAQ_GENRES:
        if faq := clean_faq(data.get("faq")):
            record["faq"] = faq
        if actions := clean_actions(data.get("today_actions")):
            record["todayActions"] = actions
    if item.get("secondary"):
        fix_press_first_person(record, item["source_name"])
    affiliates = m.match_affiliates(record, data.get("mentioned_tools") or [])
    if affiliates:
        record["affiliates"] = affiliates
        record["body"] = m.linkify_first_mention(record["body"], affiliates)
        log(f"   [aff] {len(affiliates)}件: {', '.join(a['name'] for a in affiliates)}")
    return record


FACT_ID_RE = re.compile(r"\s?[（(\[]\s?[FC]\d{1,2}(?:\s?[、,・]\s?[FC]\d{1,2})*\s?[）)\]]")


HELP_URL_RE = re.compile(r"support\.|help\.|/help|/docs?/|/faq", re.I)


def fix_ui_steps(record: dict, item: dict, page: dict | None, bundle: facts_bundle.Bundle | None) -> None:
    """根拠の無い画面の操作手順（道順・ボタン名・手順の行）を「公式ヘルプの手順を確認してください（リンク）」に置き換える。
    根拠 = 元記事＋確かな事実の束（取りに行った公式ページ・当サイトのガイド）。record をその場で書き換える"""
    groups = bundle.groups if bundle is not None else []
    source = "\n".join([str((page or {}).get("text") or ""), str(item.get("content") or "")] + [g.body for g in groups])
    if not source.strip():
        return
    allowed = ui_claims.Allowed(source)
    link = next((g for g in groups if g.label in ("関連ページ", "公的資料") and HELP_URL_RE.search(g.url)), None)
    if link:
        url, name = link.url, link.name.split("「")[0]
    else:
        url = item.get("url", "")
        name = "公式ヘルプ" if HELP_URL_RE.search(url) else f"{item.get('source_name') or '発表元'}の案内"
    for key in ("body", "easy"):
        new, replaced = ui_claims.rewrite(record.get(key) or "", allowed, url, name)
        if replaced:
            record[key] = new
            log(f"   [ui] 根拠の無い画面の手順を公式ヘルプへの案内に置き換え（{key}）: {'、'.join(replaced[:3])}"[:200])


def strip_fact_ids(record: dict) -> None:
    """束の欄の記号（「（C2）」「[F1]」）が本文に漏れていたら消す。record をその場で書き換える"""
    for key in ("body", "easy", "lede"):
        if isinstance(record.get(key), str):
            record[key] = FACT_ID_RE.sub("", record[key])
    record["highlights"] = [FACT_ID_RE.sub("", h) for h in record.get("highlights") or []]
    for row in record.get("faq") or []:
        row["a"] = FACT_ID_RE.sub("", row.get("a", ""))
    if record.get("todayActions"):
        record["todayActions"] = [FACT_ID_RE.sub("", a) for a in record["todayActions"]]
    wm = record.get("workMoney") or {}
    if wm.get("points"):
        wm["points"] = [FACT_ID_RE.sub("", p) for p in wm["points"]]


def attach_charts(record: dict) -> dict:
    """記事の数字の図（前と今・推移・料金の段）を charts に入れる（scripts/article_charts.py。LLM なし・決まった手順）"""
    try:
        charts = article_charts.derive_charts(record)
    except Exception as error:  # noqa: BLE001 - 図が作れなくても記事は出す
        log(f"   [charts] 図を作れませんでした（{str(error)[:80]}）")
        return record
    if charts:
        record["charts"] = charts
        log(f"   [charts] {', '.join(c['kind'] for c in charts)}")
    return record


RIVALS_MIN_LEFT_SEC = 2 * EST_SEC_PER_ARTICLE


def attach_rivals(record: dict, fast: bool, time_left: float) -> None:
    """製品の記事に「同じクラスの製品と比べると？」の比較表（scripts/rivals.py。ライバルの数字は各社の公式ページだけ・無料モデル）。
    速報レーンと時間が足りない回は公開を急ぐので飛ばし、同じ実行の後の工程（auto_run.yml の Rival comparison）に回す"""
    if fast or time_left < RIVALS_MIN_LEFT_SEC:
        return
    try:
        catalog = rivals.load_catalog()
        if rivals.guess_category(record, catalog) is None:
            return
        ok, why = rivals.attach(record, catalog=catalog, budget=min(rivals.ARTICLE_BUDGET, time_left - EST_SEC_PER_ARTICLE), log_fn=log)
    except Exception as error:  # noqa: BLE001 - 比較表が作れなくても記事は出す
        ok, why = False, f"失敗（{str(error)[:80]}）"
    log(f"   [rivals] {'比較表を追加' if ok else '比較表なし'}: {why}")
    rivals.remember(record.get("slug") or "", "added" if ok else why)


def finalize_facts(record: dict, source_text: str) -> dict:
    """品質ゲートを通った記事に、元記事のほかに確かめた資料（factSources）を残し、束の中身（_facts）を外す"""
    if record.get("_facts"):
        if found := facts_bundle.sources(record, source_text):
            record["factSources"] = found
    record.pop("_facts", None)
    return record


def attach_related(record: dict, data: dict, rows: list[dict]) -> dict:
    """渡した過去記事のうち実際に使ったものを relatedSlugs に残す。渡していない slug へのリンクは外す"""
    allowed = {r["slug"] for r in rows}
    record["body"], linked = archive.clean_internal_links(record["body"], allowed)
    record["easy"], _ = archive.clean_internal_links(record.get("easy", ""), allowed)
    used = {str(s).strip() for s in data.get("related_used") or []} & allowed
    used |= linked
    if used:
        record["relatedSlugs"] = [r["slug"] for r in rows if r["slug"] in used]
    return record


# 読者像から外れた一般の話題（persona.is_off_persona_candidate）の1日の上限。WEB_DAILY_CAP とは別に数える（2026-11-03）
OFF_PERSONA_DAILY_CAP = int(os.environ.get("OFF_PERSONA_DAILY_CAP", "3"))


def off_persona_today(state: dict, now: datetime | None = None) -> int:
    """今日（JST）作った、読者像から外れた一般の話題の記事の本数"""
    today = (now or datetime.now(JST)).astimezone(JST).date()
    count = 0
    for value in (state.get("processed_web") or {}).values():
        if isinstance(value, dict) and value.get("slug") and value.get("offPersona"):
            try:
                if datetime.fromisoformat(value["at"]).astimezone(JST).date() == today:
                    count += 1
            except (KeyError, ValueError):
                continue
    return count


def generated_today(state: dict) -> int:
    """1日の上限に数える本数: 今日（JST）作った記事のうち、いま公開中のもの（取り下げた記事は数えない。scripts/pacing.py）。
    読者像から外れた一般の話題は数えない（別の上限）"""
    return pacing.live_today(state)


def mark(state: dict, item: dict, **extra) -> None:
    state["processed_web"][item["id"]] = {
        "url": item["url"],
        "at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    state["failed_web"].pop(item["id"], None)


# 速報レーンで作り直さない見送り（ほかの記事と同じ話題・続報に新しい事実が無い）
KEEP_SKIP_PREFIXES = ("dedupe", "followup")
# 主要企業の公式発表で本文が読めなかったとき、processed にせず読み直す時間
SHORT_RETRY_HOURS = 24


def top_tier(item_or_url) -> bool:
    """速報レーンの対象の発信元（主要企業の公式ニュースルーム）か"""
    url = item_or_url if isinstance(item_or_url, str) else item_or_url.get("url", "")
    return fast_lane.allowed_url(url)


def short_text_retry(state: dict, item: dict) -> bool:
    """本文が読めなかった主要企業の公式発表を、SHORT_RETRY_HOURS のあいだ processed にせず再挑戦させるか"""
    if not top_tier(item):
        return False
    retry = state.setdefault("short_text_retry", {})
    first = retry.setdefault(item["id"], datetime.now(timezone.utc).isoformat())
    try:
        if datetime.now(timezone.utc) - datetime.fromisoformat(first) < timedelta(hours=SHORT_RETRY_HOURS):
            return True
    except ValueError:
        pass
    retry.pop(item["id"], None)
    return False


def reopen_short_text(state: dict, now: datetime) -> None:
    """以前の回で「本文が短い」として processed にした主要企業の公式発表のうち、24時間以内のものを再挑戦に戻す
    （読み取りサービスの鍵が無かった・一時的に読めなかったなど）。古い再挑戦の記録は消す"""
    retry = state.setdefault("short_text_retry", {})
    for key, value in list(state.get("processed_web", {}).items()):
        if not (isinstance(value, dict) and value.get("skipped") and str(value.get("reason", "")).startswith("short text")):
            continue
        try:
            # 再挑戦の期間は「最初に読めなかった時刻」から数える（見送りの記録を付け直すたびに延びないように）
            first = datetime.fromisoformat(retry.get(key) or value["at"])
        except (KeyError, TypeError, ValueError):
            continue
        if now - first < timedelta(hours=SHORT_RETRY_HOURS) and top_tier(value.get("url", "")):
            del state["processed_web"][key]
            retry.setdefault(key, value["at"])
            log(f"[info] 本文が読めず見送った公式発表を再挑戦に戻します: {value.get('url', '')[:90]}")
    for key, first in list(retry.items()):
        try:
            if now - datetime.fromisoformat(first) > timedelta(hours=SHORT_RETRY_HOURS * 3):
                retry.pop(key, None)
        except (TypeError, ValueError):
            retry.pop(key, None)


# 品質ゲートに2回落ちた有力な候補（公式・官公庁・企業の一次情報で、読者像の話題）は捨てずに、
# 時間をおいて資料（元記事のリンク先）を増やし、前回の問題点を伝えて作り直す（2026-11-03）。回数に上限あり。品質ゲートは緩めない
GATE_RETRY_MAX = int(os.environ.get("GATE_RETRY_MAX", "2"))
GATE_RETRY_WAIT_HOURS = float(os.environ.get("GATE_RETRY_WAIT_HOURS", "3"))
GATE_RETRY_LINKS = 4  # 再挑戦のときに追いかける元記事のリンクの数（通常は2）


def strong_candidate(item: dict) -> bool:
    """品質ゲートに落ちても再挑戦させる候補か: 一次情報（配信・他社の提供告知でない）で、読者像から外れた話題でない"""
    if is_wire(item) or item.get("_off_persona") or item.get("radar"):
        return False
    return feeds.source_rank(item) <= 2 and feeds.feed_kind(item) != "derivative"


def gate_retry_keep(state: dict, item: dict, reason: str, now: datetime | None = None) -> int | None:
    """再挑戦に回すなら何回目かを返す。回数を使い切っていたら記録を消して None（見送りで確定）"""
    now = now or datetime.now(timezone.utc)
    retry = state.setdefault("gate_retry", {})
    rec = retry.get(item["id"]) or {"tries": 0, "first": now.isoformat()}
    if rec.get("tries", 0) >= GATE_RETRY_MAX:
        retry.pop(item["id"], None)
        return None
    rec.update({
        "tries": rec.get("tries", 0) + 1, "next": (now + timedelta(hours=GATE_RETRY_WAIT_HOURS)).isoformat(),
        "reason": str(reason or "")[:300], "url": item.get("url", ""), "title": item.get("title", "")[:120],
    })
    retry[item["id"]] = rec
    return rec["tries"]


def gate_retry_due(state: dict, item_id: str, now: datetime | None = None) -> bool:
    """再挑戦待ちの候補は、待ち時間が過ぎるまで候補に戻さない（記録が無ければ True）"""
    rec = (state.get("gate_retry") or {}).get(item_id)
    if not rec:
        return True
    try:
        return (now or datetime.now(timezone.utc)) >= datetime.fromisoformat(rec["next"])
    except (KeyError, TypeError, ValueError):
        return True


def prune_gate_retry(state: dict, now: datetime | None = None) -> None:
    """古い再挑戦の記録（最初に落ちてから3日超。どのみち古い発表として作らない）を消す"""
    now = now or datetime.now(timezone.utc)
    retry = state.get("gate_retry") or {}
    for key, rec in list(retry.items()):
        try:
            if now - datetime.fromisoformat(rec["first"]) > timedelta(days=3):
                retry.pop(key, None)
        except (KeyError, TypeError, ValueError):
            retry.pop(key, None)


def prune_processed(state: dict, keep_days: int = 14) -> None:
    """processed_web が際限なく増えないよう、対象期間（--days）より十分古い記録を消す"""
    since = datetime.now(timezone.utc) - timedelta(days=keep_days)
    for key, value in list(state["processed_web"].items()):
        try:
            if datetime.fromisoformat(value["at"]) < since:
                del state["processed_web"][key]
        except (KeyError, TypeError, ValueError):
            continue


def log_quality(state: dict, item: dict, kind: str, verdict: dict, attempt: int, accepted: bool, model: str = "") -> None:
    """品質ゲートの結果を state.json に残す（直近 QUALITY_LOG_KEEP 件）"""
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "id": item["id"],
        "title": item["title"][:80],
        "source": item.get("source_slug", "trends"),
        "kind": kind,
        "attempt": attempt,
        "accepted": accepted,
        "score": verdict.get("score"),
        "problems": [str(p)[:160] for p in verdict.get("problems") or []][:6],
        "model": model,
    }
    if verdict.get("readability"):
        entry["readability"] = verdict["readability"]
    if verdict.get("warnings"):
        entry["warnings"] = [str(w)[:120] for w in verdict["warnings"]][:3]
    entries = state.setdefault("quality_log", [])
    entries.append(entry)
    del entries[:-QUALITY_LOG_KEEP]


def tag_sections(items: list[dict]) -> list[dict]:
    """幅広い話題の候補にセクション（ジャンル slug）を付ける"""
    for item in items:
        feed = feeds.FEEDS_BY_SLUG.get(item["source_slug"], {})
        section = feed.get("section")
        item["section"] = (feeds.classify_section(item) or feed.get("fallback")) if section == "auto" else section
        # 官公庁・企業の混ざったフィードから、投資・マネー／不動産の話題をそれぞれのレーンへ（scripts/genre_focus.py）
        item["section"] = genre_focus.route_section(item, item["section"])
        item["kind"] = feed.get("kind", "")
    return items


def build_lanes(ai_items: list[dict], broad_items: list[dict], section_filter: str) -> dict[str, list[dict]]:
    """レーン（ai と各セクション）ごとの候補列。並びは collect() の順（発信元が交互・新しい順）を保つ"""
    lanes: dict[str, list[dict]] = {}
    if section_filter in ("ai", "all"):
        lanes["ai"] = list(ai_items)
    if section_filter in ("broad", "all"):
        ai_ids = {i["id"] for i in ai_items}
        for slug in BROAD_SLUGS:
            lanes[slug] = [i for i in broad_items if i.get("section") == slug and i["id"] not in ai_ids]
        lanes["_unsorted"] = [i for i in broad_items if not i.get("section") and i["id"] not in ai_ids]
    return lanes


def lane_order(lanes: dict[str, list[dict]], now: datetime | None = None) -> list[str]:
    """ai を先頭に、読者像（scripts/persona.py）で優先するレーン（ビジネス・お金／制度）を次に、
    残りは時刻で開始位置をずらして毎回同じセクションが先にならないようにする。スポーツは最後（1日の上限つき）"""
    # 今日まだ最低本数に届いていない 投資・マネー／不動産／エンタメ を先に（候補があるときだけ。scripts/genre_focus.py）
    boost = genre_focus.boost_lanes(lanes, now=now)
    first = boost + [s for s in persona.PRIORITY_LANES if s in lanes and s not in boost]
    rest = [s for s in BROAD_SLUGS if s in lanes and s not in first and s != "sports"]
    if rest:
        shift = (now or datetime.now(JST)).hour % len(rest)
        rest = rest[shift:] + rest[:shift]
    last = ["sports"] if "sports" in lanes else []
    return (["ai"] if "ai" in lanes else []) + first + rest + last


def lane_turns(lane: str) -> int:
    """1巡で何本まで試すか。優先レーンは2本（全体の本数は変えず、順番だけ前に寄せる）"""
    return 2 if lane in persona.PRIORITY_LANES else 1


def persona_rank(item: dict) -> int:
    """レーン内の並べ替え用（rank_bucket の次）。お金・投資・不動産・起業/副業・税や補助金の話題を先に、
    読者像から外れた一般の話題（叙勲・記念日の告知・広報動画・会議の日程など）を最後に（persona.candidate_rank）"""
    return persona.candidate_rank(item.get("title", ""), item.get("summary", ""))


def off_persona(item: dict) -> bool:
    """幅広い話題の候補が、読者像から外れた一般の話題か（後回しにし、1日の上限とは別に数える）"""
    return persona.is_off_persona_candidate(item.get("title", ""), item.get("summary", ""))


def sports_today(state: dict, now: datetime | None = None) -> int:
    """今日（JST）作ったスポーツの記事の本数（processed_web の section）"""
    today = (now or datetime.now(JST)).astimezone(JST).date()
    count = 0
    for value in (state.get("processed_web") or {}).values():
        if isinstance(value, dict) and value.get("slug") and value.get("section") == "sports":
            try:
                if datetime.fromisoformat(value["at"]).astimezone(JST).date() == today:
                    count += 1
            except (KeyError, ValueError):
                continue
    return count


# --------------------------------------------------------------------------- #
# 速報レーン
# --------------------------------------------------------------------------- #


def _iso(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else ""


# 掲載＝サイトで開けるようになるまで。記事を書き出してからデプロイ（実測2〜3分）を足した目安
DEPLOY_ALLOWANCE_MIN = 3
SPEED_LABEL_MAX_MIN = 60


def reliable_published(published: datetime | None, detected: datetime | None, guessed: bool = False) -> bool:
    """発表の時刻が確かか。検知の時刻で代用したもの・日付だけ（UTC 00:00:00）のものは確かでない。
    ただし 00:00:00 でも、検知がその30分以内なら本当の時刻とみなす（OpenAI は 09:00 JST ちょうどに出すことがある）"""
    if not isinstance(published, datetime) or guessed:
        return False
    if published.astimezone(timezone.utc).strftime("%H:%M:%S") != "00:00:00":
        return True
    return bool(detected) and timedelta(0) <= detected - published <= timedelta(minutes=30)


def stamp_times(record: dict, item: dict, run_detected_at: datetime) -> None:
    """速さの計測用: 発表の時刻（sourcePublishedAt）と、こちらが見つけた時刻（detectedAt）。
    通常の回の detectedAt は、その回がフィードを読んだ時刻。
    発表の時刻が確かで、掲載まで60分以内なら speedMinutes（「公式発表から12分で掲載」のラベル・速報の投稿文に使う）"""
    published = item.get("published_at")
    detected = item.get("detected_at") or run_detected_at
    if isinstance(published, datetime) and not item.get("published_guessed"):
        record["sourcePublishedAt"] = _iso(published)
    record["detectedAt"] = _iso(detected)
    if item.get("fast"):
        record["fastLane"] = True
    if reliable_published(published, detected, bool(item.get("published_guessed"))):
        elapsed = (datetime.now(timezone.utc) - published).total_seconds() / 60
        minutes = int(-(-elapsed // 1)) + DEPLOY_ALLOWANCE_MIN  # 切り上げ
        if 0 < minutes <= SPEED_LABEL_MAX_MIN:
            record["speedMinutes"] = minutes


def latency_note(record: dict) -> str:
    def minutes(a: str, b: str) -> str:
        try:
            return f"{(datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60:.0f}分"
        except (TypeError, ValueError):
            return "不明"

    now = datetime.now(timezone.utc).isoformat()
    speed = f" / ラベル「公式発表から{record['speedMinutes']}分で掲載」" if record.get("speedMinutes") else " / 速さのラベルなし"
    return (
        f"発表→書き出し {minutes(record.get('sourcePublishedAt', ''), now)} / 検知→書き出し {minutes(record.get('detectedAt', ''), now)}{speed}"
    )


def _guess_source(url: str) -> str:
    host = urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
    for src in fast_lane.rules()["sources"]:
        if urllib.parse.urlsplit(src["url"]).netloc.lower().removeprefix("www.") == host:
            return src["slug"]
    return ""


def build_fast_items(urls: list[str], meta_raw: str, state: dict, now: datetime) -> tuple[list[dict], dict]:
    """速報レーンの候補（feeds.parse_feed と同じ形）と、作る前に決まった結果 {id: result}"""
    fast_lane.register_sources(feeds)
    metas = fast_lane.parse_meta(meta_raw)
    by_url = {feeds.item_id(meta["url"]): meta for meta in metas}
    ordered = list(dict.fromkeys([*urls, *(meta["url"] for meta in metas)]))[:3]
    acks = fast_lane.load_acks()
    cap = int(fast_lane.rules()["daily_max"])  # 1日の上限（Worker と同じ。通常の回の --daily-cap とは別枠）
    items: list[dict] = []
    results: dict[str, dict] = {}
    for url in ordered:
        iid = feeds.item_id(url)
        meta = by_url.get(iid, {})
        key = str(meta.get("id") or iid)
        detected = fast_lane.parse_time(meta.get("detectedAt")) or now
        base = {"url": url, "title": str(meta.get("title") or "")[:200], "source": meta.get("source", ""), "detectedAt": _iso(detected)}
        if not fast_lane.allowed_url(url):
            log(f"[fast] 対象外の URL のため処理しません: {url}")
            results[key] = {**base, "outcome": "skipped", "reason": "対象外のURL"}
            continue
        if fast_lane.created_today(acks) >= cap:
            log(f"[fast] 今日の速報レーンの上限（{cap}本）に達したため通常の回に任せます: {url}")
            results[key] = {**base, "outcome": "capped", "reason": f"1日の上限 {cap}"}
            continue
        slug = str(meta.get("source") or _guess_source(url))
        feed = feeds.FEEDS_BY_SLUG.get(slug) or {}
        item = None
        if feed and not feed.get("fast_only"):
            try:
                item = next((i for i in feeds.parse_feed(feed) if i["id"] == iid), None)
            except Exception as error:  # noqa: BLE001
                log(f"[fast] フィードの取得に失敗（{str(error)[:80]}）。記事ページから作ります")
        # Worker が読んだ本文（GitHub Actions からは openai.com などが 403 で読めない）。十分な長さならページを読み直さない
        worker_text = str(meta.get("text") or "")[: feeds.TEXT_CAP]
        page = None
        if len(worker_text) >= 2000:
            page = {"text": worker_text, "image": str(meta.get("image") or "") or None, "title": "", "description": "", "published": ""}
            log(f"[fast] Worker が読んだ本文を使います（{len(worker_text)}字）")
        elif item is None or not item.get("published_at"):
            try:
                page = feeds.fetch_article_text(url)
            except Exception as error:  # noqa: BLE001
                log(f"[fast] 記事ページの取得に失敗: {str(error)[:120]}")
        outlet = fast_lane.outlet(url)
        secondary = bool(meta.get("secondary")) if "secondary" in meta else bool(outlet and not fast_lane.primary_name(url))
        if item is None:
            item = {
                "id": iid, "url": url, "title": base["title"] or (page or {}).get("title", ""),
                "summary": (page or {}).get("description", "")[:600], "content": "",
                "published_at": None,
                "source_name": (feed.get("name") if feed and not secondary and slug != "radar-official" else "")
                or str(meta.get("sourceName") or "") or (outlet or {}).get("name") or fast_lane.primary_name(url) or urllib.parse.urlsplit(url).netloc,
                "source_slug": slug or (f"radar-{outlet['host']}" if outlet else "fast"), "lang": feed.get("lang", "en"), "image": None, "priority": 1,
            }
        if secondary:
            # 報道が出典（レーダーで一次情報が見つからなかった話題）。記事に「〇〇の報道によると」と書かせる
            item["secondary"] = True
            if meta.get("sourceName"):
                item["source_name"] = str(meta["sourceName"])
        if len(worker_text) > len(item.get("content") or ""):
            item["content"] = worker_text
        if meta.get("image") and not item.get("image"):
            item["image"] = str(meta["image"])
        # レーダーの話題（まとめサイト・他社の見出しから見つけたもの）の publishedAt は見出しの時刻で、発表の時刻ではない。
        # 発表の時刻としては使わず、公式ページに日付が無ければ「不明」にする（古い発表を今日の速報に見せない）
        from_radar = bool(meta.get("via"))
        if from_radar:
            item["radar"] = True
        published = (
            item.get("published_at")
            or (None if from_radar else fast_lane.parse_time(meta.get("publishedAt")))
            or fast_lane.parse_time((page or {}).get("published"))
        )
        # 発表の時刻が分からなければ検知の時刻で代用する（速さのラベルは出さない）
        item["published_at"] = published or detected
        item["published_guessed"] = published is None
        item.update({"fast": True, "fast_key": key, "detected_at": detected})
        # 新製品・ゲームの公式（rules の genre）は、そのジャンルの形で作る
        genre_slug = str(meta.get("genre") or fast_lane.genre_of(item["source_slug"]))
        if genre_slug != "ai" and genre_slug in GENRES_BY_SLUG:
            item["section"] = genre_slug
        if page:
            item["_page"] = page
        if not item["title"]:
            log(f"[fast] 見出しが取れないため処理しません: {url}")
            results[key] = {**base, "outcome": "retry", "reason": "見出しが取れない"}
            continue
        done = state["processed_web"].get(iid) or {}
        if done and not (done.get("slug") or done.get("updated")) and not str(done.get("reason", "")).startswith(KEEP_SKIP_PREFIXES):
            # 処理済みでも記事になっていない（本文が読めない・品質で落ちた・足切りなど）。速報レーンでは作り直す
            log(f"[fast] 前に見送った記事（{str(done.get('reason', ''))[:60]}）を作り直します")
            del state["processed_web"][iid]
            state["major_tries"].pop(iid, None)
            state["failed_web"].pop(iid, None)
        if iid in state["processed_web"]:
            done = state["processed_web"][iid]
            results[key] = {
                **base, "outcome": "duplicate", "slug": done.get("slug") or done.get("updated") or "",
                "reason": "通常の回などで処理済み",
            }
        items.append(item)
        ok, why = fast_lane.relevant(item["title"], item["source_slug"])
        log(
            f"[fast] {item['source_name']}: {item['title'][:80]}（判定 {why if ok else why + '・手動の指定として続ける'} / "
            f"発表 {'不明' if item['published_guessed'] else format(item['published_at'].astimezone(JST), '%m-%d %H:%M')} / "
            f"検知 {detected.astimezone(JST):%H:%M:%S}）"
        )
    return items, results


def write_fast_results(items: list[dict], results: dict, state: dict, run_status: dict, now: datetime) -> None:
    """state/fast_lane.json に結果を書く（Worker がこれを見て再起動をやめる）"""
    import os  # noqa: PLC0415

    for item in items:
        key = item["fast_key"]
        if key in results:
            continue
        done = state["processed_web"].get(item["id"]) or {}
        status = run_status.get(item["id"], ("",))
        if done.get("slug"):
            outcome, slug, reason = "created", done["slug"], ""
        elif done.get("updated"):
            outcome, slug, reason = "updated", done["updated"], "既存記事に続報を追加"
        elif done.get("skipped"):
            reason = str(done.get("reason", ""))
            outcome = "duplicate" if reason.startswith("dedupe") else "skipped"
            slug = ""
        else:
            outcome, slug = "retry", ""
            reason = "作れなかった（次の起動で再挑戦）" if status[0] == "pending" else "今回は処理できなかった"
        results[key] = {
            "url": item["url"], "title": item["title"][:200], "source": item["source_slug"],
            "outcome": outcome, "slug": slug, "reason": reason[:200], "detectedAt": _iso(item.get("detected_at")),
            "sourcePublishedAt": _iso(item.get("published_at")),
        }
    if not results:
        return
    data = fast_lane.load_acks()
    finished = _iso(datetime.now(timezone.utc))
    for key, result in results.items():
        data["items"][key] = {**result, "startedAt": _iso(now), "finishedAt": finished, "run": os.environ.get("GITHUB_RUN_ID", "")}
        log("[fast] 結果: " + " ".join(x for x in (result['outcome'], result.get('slug'), result.get('reason')) if x))
    fast_lane.save_acks(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max", type=int, default=5, help="この実行で生成する AI（公式ブログ）記事の最大数")
    parser.add_argument("--max-broad", type=int, default=6, help="この実行で生成する幅広い話題の記事の最大数")
    parser.add_argument("--section", choices=["ai", "broad", "all"], default="all", help="対象のレーン")
    parser.add_argument("--per-feed", type=int, default=2, help="幅広い話題で同じフィードから1回に作る最大本数")
    parser.add_argument("--days", type=int, default=3, help="何日前までの記事を対象にするか")
    parser.add_argument("--dry-run", action="store_true", help="API を呼ばず候補一覧のみ表示")
    parser.add_argument("--daily-cap", type=int, default=0, help="今日（JST）の生成数がこれ以上なら何もしない（0=無制限。大きなニュースは除く）")
    parser.add_argument("--tier", choices=["bulk", "quality"], default="bulk")
    parser.add_argument("--source", action="append", help="対象フィード slug（複数可。scripts/feeds.py 参照）")
    parser.add_argument("--trends", type=int, default=0, help="話題のニュース（state/trend_queue.json）から生成する最大本数")
    parser.add_argument("--no-gate", action="store_true", help="品質ゲートの事実確認を省く（形のチェックは行う）。テスト用")
    parser.add_argument("--only", help="タイトルがこの正規表現に一致する候補だけを対象にする（テスト用）")
    parser.add_argument("--max-updates", type=int, default=3, help="この実行で既存記事に足す続報の最大数（--daily-cap には数えない）")
    parser.add_argument("--max-major", type=int, default=2, help="この実行で先に処理する大きなニュースの最大数（tier=quality。0=使わない）")
    parser.add_argument("--max-wire", type=int, default=2, help="この実行で配信（PR TIMES 等）を生成に回す最大数")
    parser.add_argument("--fast-url", action="append", default=[], help="速報レーン: この URL だけを大きなニュースとして最初に作る（複数可）")
    parser.add_argument("--fast-meta", default="", help="速報レーン: Worker が渡す JSON（id・title・source・publishedAt・detectedAt）")
    args = parser.parse_args()
    args.fast_url = [u.strip() for u in args.fast_url if u and u.strip()]
    fast_mode = bool(args.fast_url or fast_lane.parse_meta(args.fast_meta))

    llm._load_env_local()
    state = m.load_state()
    state.setdefault("processed_web", {})
    state.setdefault("failed_web", {})
    state.setdefault("major_tries", {})
    run_detected_at = datetime.now(timezone.utc)
    reopen_short_text(state, run_detected_at)
    prune_gate_retry(state, run_detected_at)

    if fast_mode:
        # 速報レーン: 待たせないよう、生成・事実確認・続報の判定をすべて quality の段で行い、ほかのレーンは回さない
        quality.CHECK_TIER = "quality"
        followup.CHECK_TIER = "quality"
        args.tier = "quality"
        args.trends = args.max = args.max_broad = 0

    done_today = generated_today(state)
    # 上限は公開中の記事だけで数え、夜中に使い切らないよう時刻ごとの配分も見る（scripts/pacing.py。速報・大きなニュースは外）
    remaining, by_cap, by_pace = pacing.run_allowance(args.daily_cap, done_today)
    cap_reached = remaining <= 0
    log(f"[pace] 今日の時間ごとの本数（作った/公開中）: {pacing.hour_line(state)}")
    if args.daily_cap:
        log(f"[pace] 上限 {args.daily_cap} / 公開中 {done_today} 本 / この時刻までの配分 {pacing.allowed_by(args.daily_cap)} 本"
            f" → この回 {max(0, remaining)} 本まで")
    why = "1日の上限" if by_cap <= 0 else "この時刻までの配分（夜中に使い切らず昼に回す）"
    if cap_reached and args.max_major <= 0 and not fast_mode:
        log(f"[info] 今日の公開中 {done_today} 本で{why}に達しています（上限 {args.daily_cap}）")
        return 0
    if cap_reached:
        log(f"[info] 今日の公開中 {done_today} 本で{why}に達しています（上限 {args.daily_cap}）。大きなニュースだけ確認します")
    trend_limit = 0 if cap_reached else min(args.trends, remaining)
    ai_limit = 0 if cap_reached else (args.max if args.section in ("ai", "all") else 0)
    broad_limit = 0 if cap_reached else (args.max_broad if args.section in ("broad", "all") else 0)
    log(f"[info] 今日の生成済み（公開中） {done_today} 本 / 上限 {args.daily_cap or 'なし'}")

    recent = dedupe.load_recent(DEDUPE_DAYS)
    story_recent = story.Recent(DEDUPE_DAYS)
    log(f"[info] 重複チェック: 直近{DEDUPE_DAYS}日の記事 {len(recent.entries)} 件（同じ発表の判定 {len(story_recent.stories)} 件）")
    # 続報の対象（直近14日・まとめ記事を除く）と、同じ実行の中で採った候補
    followups = dedupe.load_recent(followup.FOLLOWUP_DAYS, skip_roundups=True)
    run_seen = dedupe.Index()  # label は候補の id
    # 同じ実行で先に扱った候補の結果: ("article", slug) / ("pending", id) / ("failed", source_rank) / ("planned", id)
    run_status: dict[str, tuple] = {}
    run_titles: dict[str, str] = {}

    def seen_add(item: dict) -> None:
        run_seen.add(item["title"], item.get("url", ""), label=item["id"])
        run_titles[item["id"]] = item["title"]

    def seen_match(item: dict):
        """run_seen.match の理由の相手を id ではなく見出しで表示する"""
        hit = run_seen.match(item["title"], item.get("url", ""))
        if hit and hit[1]:
            return hit[0].replace(f": {hit[1][:40]}", f": {run_titles.get(hit[1], hit[1])[:40]}"), hit[1]
        return hit
    touched: set[str] = set()  # この実行で作った・続報を足した記事
    updated_slugs: set[str] = set()  # この実行で続報を足した記事（同じ記事に二度足さない）
    arch = archive.Archive()
    log(f"[info] 続報の対象: 直近{followup.FOLLOWUP_DAYS}日 {len(followups.entries)} 件 / 文脈用の過去記事 {len(arch.rows)} 件")

    updated_count = [0]

    def followup_target(title: str, url: str = ""):
        """続報を足せる既存記事 ("ok", path, data, 理由) / 続報の上限で今回は見送り ("defer",) / 対象なし None"""
        if args.max_updates <= 0:
            return None  # 続報を使わない（従来どおり重複として外す）
        hit = followups.match(title, url)
        if not hit or not hit[1] or hit[1] in touched:
            return None
        target = followup.load_target(hit[1])
        if target is None:
            return None
        if updated_count[0] >= args.max_updates:
            return ("defer",)
        return ("ok", target[0], target[1], hit[0])

    def followup_for(slug: str, why: str):
        """この実行で作った記事への続報（Copilot 対応など派生の発表を本家の記事に足す）"""
        if args.max_updates <= 0 or slug in updated_slugs:
            return None
        target = followup.load_target(slug)
        if target is None:
            return None
        if updated_count[0] >= args.max_updates:
            return ("defer",)
        return ("ok", target[0], target[1], f"この実行の記事と同じ話題: {why}")

    def in_run_action(cand: dict):
        """同じ実行で先に扱った同じ話題との関係。
        None（関係なし・置き換え可）/ ("ok", ...)=続報へ / ("defer", 理由)=次回へ / ("dup", 理由)=重複"""
        hit = seen_match(cand)
        if not hit:
            return None
        why, label = hit
        status = run_status.get(label)
        if status is None:
            return ("dup", why)
        if status[0] == "article":
            target = followup_for(status[1], why)
            if target and target[0] == "defer":
                return ("defer", "続報の上限")
            return target or ("dup", why)
        if status[0] in ("pending", "planned"):
            return ("defer", f"本家の記事を優先（{why}）")
        if status[0] == "failed" and feeds.source_rank(cand) < status[1]:
            return None  # 先の候補は落ちたので、より一次の情報で作り直す
        return ("dup", why)

    # --- 話題のニュース ---
    all_queue = trends.load_queue()
    trend_index = dedupe.Index()
    for q in all_queue:
        trend_index.add(q["title"])
    queue = []
    if trend_limit > 0:
        queue = [
            q for q in all_queue
            if q["id"] not in state["processed_web"] and state["failed_web"].get(q["id"], 0) < MAX_FAILURES
        ]
        queue.sort(key=lambda q: q.get("traffic", 0), reverse=True)
        log(f"[info] 話題キュー {len(queue)} 件 / 上限 {trend_limit} 本")

    # --- 公式フィード（AI）と幅広い話題 ---
    major_ids: set[str] = set()

    def usable(items):
        kept = []
        for i in items:
            if not gate_retry_due(state, i["id"]):
                continue  # 品質ゲートに落ちて再挑戦待ち（待ち時間が過ぎたら戻る）
            if args.max_major > 0 and is_major(i, trend_index):
                # 大きなニュースは失敗回数ではなく実行回数で区切る（MAJOR_MAX_RUNS 回まで再挑戦）
                if state["major_tries"].get(i["id"], 0) < major_max_runs(i):
                    major_ids.add(i["id"])
                    kept.append(i)
            elif state["failed_web"].get(i["id"], 0) < MAX_FAILURES:
                kept.append(i)
        return kept

    ai_items: list[dict] = []
    broad_items: list[dict] = []
    fast_items: list[dict] = []
    fast_results: dict[str, dict] = {}
    if fast_mode:
        fast_items, fast_results = build_fast_items(args.fast_url, args.fast_meta, state, run_detected_at)
        args.max_major = len(fast_items)
    elif (ai_limit > 0 or args.max_major > 0) and args.section in ("ai", "all"):
        selected = [f for f in feeds.FEEDS if not args.source or f["slug"] in args.source]
        report = {}
        ai_items = usable(feeds.collect(args.days, state, selected, report=report)) if selected else []
        log(f"[feed-health] AI {json.dumps(feeds.report_totals(report), ensure_ascii=False, sort_keys=True)}")
    if not fast_mode and (broad_limit > 0 or (args.max_major > 0 and args.section in ("broad", "all"))):
        selected = [f for f in feeds.BROAD_FEEDS if not args.source or f["slug"] in args.source]
        report = {}
        broad_items = tag_sections(usable(feeds.collect(args.days, state, selected, report=report))) if selected else []
        log(f"[feed-health] broad {json.dumps(feeds.report_totals(report), ensure_ascii=False, sort_keys=True)}")
    if args.only:
        only_re = re.compile(args.only)
        ai_items = [i for i in ai_items if only_re.search(i["title"])]
        broad_items = [i for i in broad_items if only_re.search(i["title"])]
    # 大きなニュース: 本家（その企業の公式）→ 他の公式 の順、同じ順位なら新しい順
    majors = sorted(
        [i for i in ai_items + broad_items if i["id"] in major_ids],
        key=lambda i: (feeds.source_rank(i), -i["published_at"].timestamp()),
    )
    majors = [i for n, i in enumerate(majors) if i["id"] not in {j["id"] for j in majors[:n]}]  # 同じ URL は AI 側を残す
    if fast_mode:
        majors = fast_items
    elif args.max_major > 0 and args.section in ("ai", "all") and not args.only and not args.source:
        # 料金・プラン・リリースノートのページの新しい項目（scripts/page_watch.py）。大きなニュースと同じ流れで先に作る
        # （同じ発表の記事があれば続報か見送り。速報投稿はしない）
        try:
            watch_items = page_watch.candidates(state["processed_web"], dry_run=args.dry_run)
        except Exception as error:  # noqa: BLE001
            log(f"[watch] ページの見張りに失敗: {str(error)[:120]}")
            watch_items = []
        for it in watch_items:
            major_ids.add(it["id"])
        if watch_items:
            log(f"[watch] 料金・プラン・リリースノートの新しい項目 {len(watch_items)} 件: {' / '.join(i['title'][:50] for i in watch_items)}")
        majors = watch_items + majors
    if not ai_limit:
        ai_items = []
    if not broad_limit:
        broad_items = []
    lanes = build_lanes(
        [i for i in ai_items if i["id"] not in major_ids], [i for i in broad_items if i["id"] not in major_ids], args.section
    )
    # レーン内は 公式・官公庁 → 他社の対応告知 → 配信 の順（同じ順位の中は collect() の順を保つ）
    # 同じ順位の中では、読者像で優先する話題（お金・投資・不動産・起業/副業・税や補助金）を先にする
    for lane in lanes:
        lanes[lane].sort(key=lambda i: (rank_bucket(i), 0 if lane == "ai" else persona_rank(i)))
    log(
        f"[info] 大きなニュース {len(majors)} 件（上限 {args.max_major} 本・tier quality）/ "
        f"候補 AI {len(ai_items)} 件（上限 {ai_limit} 本）/ 幅広い話題 {len(broad_items)} 件（上限 {broad_limit} 本）"
        f" / 配信の生成は1回 {args.max_wire} 本まで"
    )

    if args.dry_run:
        if majors:
            log("\n  ■ 大きなニュース（最初に処理・tier quality・最大3回試行・失敗しても3回の実行まで再挑戦）")
        for item in majors[: max(args.max_major, 0) * 3]:
            reason = None
            if recent.has_url(item["url"]):
                reason = "重複: 同じ出典URL"
            act = None if reason else in_run_action(item)
            if act and act[0] != "ok":
                reason = f"{act[0]}: {act[1]}"
            tries = state["major_tries"].get(item["id"], 0)
            if not reason and not act:
                hit = followups.match(item["title"], item["url"])
                if hit and hit[1] and followup.load_target(hit[1]):
                    reason = f"続報候補 → {hit[1]}"
                else:
                    reason = "対象（新しい記事）"
                    seen_add(item)
                    run_status[item["id"]] = ("planned", item["id"])
            elif act and act[0] == "ok":
                reason = f"続報候補 → この実行の記事（{act[3][:50]}）"
            log(
                f"    - [{item['source_slug']} rank{feeds.source_rank(item)}] {item['published_at'].astimezone(JST):%m-%d %H:%M}"
                f" {item['title'][:60]}  ({reason}{f' / これまで {tries} 回失敗' if tries else ''})"
            )
            # dry-run では大きなニュースは成功した前提で、派生の発表を続報に回す様子を示す
            if run_status.get(item["id"], ("",))[0] == "planned":
                run_status[item["id"]] = ("article-dry", item["id"])
        for q in queue[: max(trend_limit * 3, 5)]:
            fetched = fetch_trend_sources(q)
            ok = len(fetched) >= TREND_MIN_SOURCES
            hit = followups.match(q["title"])
            target = followup.load_target(hit[1]) if hit and hit[1] else None
            log(
                f"  - [trend/{q.get('genre')}] {q['title']}（traffic {q.get('traffic', 0):,}）"
                f" 本文の取れた報道 {len(fetched)} 本 → {'除外' if not ok else (f'続報候補 → {hit[1]}' if target else '対象')}"
                f"  {trend_source_name(fetched) if fetched else ''}"
            )
        wire_seen = 0
        for lane in lane_order(lanes):
            items = lanes[lane]
            cap = ai_limit if lane == "ai" else broad_limit
            counts = {"対象": 0, "除外": 0, "重複": 0}
            lines = []
            for item in items:
                reason = prefilter(item) if lane == "ai" else broad_prefilter(item)
                hit_run = None if reason else seen_match(item)
                status = run_status.get(hit_run[1]) if hit_run else None
                hit = None if reason or recent.has_url(item["url"]) else followups.match(item["title"], item["url"])
                target = followup.load_target(hit[1]) if hit and hit[1] else None
                dup = None if reason else (recent.find(item["title"], item["url"]) or (hit_run[0] if hit_run else None))
                if reason:
                    counts["除外"] += 1
                    flag = f"除外: {reason}"
                elif status and status[0] == "article-dry":
                    counts["続報"] = counts.get("続報", 0) + 1
                    flag = f"派生 → この実行の大きなニュースの記事に続報（{hit_run[0][:50]}）"
                elif target and not hit_run:
                    counts["続報"] = counts.get("続報", 0) + 1
                    seen_add(item)
                    flag = f"続報候補 → {hit[1]}（{hit[0][:40]}）"
                elif dup:
                    counts["重複"] += 1
                    flag = f"重複: {dup}"
                else:
                    counts["対象"] += 1
                    seen_add(item)
                    n_ctx = len(arch.related(item["title"], item.get("content") or item.get("summary") or "", item["url"], lane))
                    flag = f"対象（過去記事の文脈 {n_ctx}本）" if n_ctx else "対象"
                    if is_wire(item):
                        wire_seen += 1
                        flag += "（配信: 本文の厚み・お知らせ判定は取得後）" + ("" if wire_seen <= args.max_wire else " ※配信の上限超え→次回")
                if len(lines) < max(cap, 3) * 2 or (flag.startswith("対象") and counts["対象"] <= max(cap, 3)) or flag.startswith(("続報", "派生")):
                    lines.append(
                        f"    - [{item['source_slug']}] {item['published_at'].astimezone(JST):%m-%d %H:%M} {item['title'][:60]}  ({flag})"
                    )
            log(
                f"\n  ■ {lane}: 候補 {len(items)} 件 → 対象 {counts['対象']} / 続報候補 {counts.get('続報', 0)}"
                f" / 除外 {counts['除外']} / 重複 {counts['重複']}"
            )
            for line in lines[: max(cap, 3) * 3]:
                log(line)
        unsorted = lanes.get("_unsorted") or []
        if unsorted:
            log(f"\n  ■ セクション判定できず（生成しない）: {len(unsorted)} 件  例: {' / '.join(i['title'][:30] for i in unsorted[:3])}")
        log("\n[dry-run] API 呼び出しはスキップしました")
        return 0

    created = {"major": 0, "trend": 0, "ai": 0, "broad": 0}
    per_section: dict[str, int] = {}
    per_feed: dict[str, int] = {}
    rejected = 0
    last_problem = [""]  # 0本の回の理由（scripts/pipeline_health.py が記録する）
    wire_attempts = [0]
    used_queue_ids: set[str] = set()
    run_started = time.time()

    def total_created() -> int:
        return sum(created.values())

    # 読者像から外れた一般の話題の本数（total_created・1日の上限には数えない。OFF_PERSONA_DAILY_CAP まで）
    created_off = [0]
    off_done_today = off_persona_today(state)

    def time_left() -> float:
        return RUN_TIME_BUDGET_SEC - (time.time() - run_started)

    def time_up() -> bool:
        if time_left() < EST_SEC_PER_ARTICLE:
            log("[info] 時間の上限が近いため、残りは次回に回します")
            return True
        if not fast_mode and fast_lane.fast_waiting():
            log("[info] 速報レーンの実行が待っているため、ここで終えて譲ります（残りは次回）")
            return True
        return False

    def major_tier() -> str:
        """大きなニュースの段。速報レーンで quality が尽きていたら bulk で作る（出さないよりよい）"""
        return "bulk" if fast_mode and llm.all_exhausted("quality", visibility="high") else "quality"

    def generate(system: str, prompt: str, schema: dict, item_id: str, tier: str, purpose: str | None = None):
        # Claude API（段ごとのモデル・予算の配分）→ 速報・SNS だけ Claude CLI → 無料モデル
        try:
            return llm.generate_json(system, prompt, schema, temperature=0.6, max_output_tokens=32768, tier=write_tier(tier),
                                     purpose=purpose)
        except Exception as error:  # noqa: BLE001
            log(f"   [NG] {str(error)[:200]}")
            last_problem[0] = f"生成に失敗: {str(error)[:160]}"
            state["failed_web"][item_id] = state["failed_web"].get(item_id, 0) + 1
            return None, None

    def polish_or_keep(item: dict, kind: str, record: dict, verdict: dict, source_text: str, context: str):
        """読みやすさだけ基準外: 本文の文を分ける書き直しを1回試す。だめなら元の原稿のまま（落とさない）"""
        try:
            polished, info = quality.polish(record, source_text, kind, context, check_facts=not args.no_gate)
        except Exception as error:  # noqa: BLE001
            polished, info = None, f"書き直しに失敗（{str(error)[:80]}）"
        if polished is None:
            log(f"   [read] 読みやすさは基準外のまま受理（{info}） {verdict.get('readability')}")
            log_quality(state, item, kind, {**verdict, "problems": [f"読みやすさ基準外のまま受理: {info}"]}, 0, True)
            return record, verdict
        log(f"   [read] 文を分けて差し替え {verdict.get('readability')} → {info.get('readability')}（{info.get('polished_by')}）")
        log_quality(state, item, kind, info, 0, True, str(info.get("polished_by") or ""))
        return polished, info

    def gate_verdict(blocked: str) -> dict:
        return {"ok": False, "score": None, "problems": [f"公開前の門: {indexnow.PUBLISH_FEEDBACK[blocked]}"]}

    def gated(item: dict, kind: str, system: str, prompt: str, schema: dict, source_text: str, build, context: str = "",
              tier: str | None = None, attempts: int = 2, purpose: str | None = None):
        """生成 → 品質ゲート →（不合格なら問題点を添えて作り直し。通常は計2回、大きなニュースは計3回）。
        戻り値: ("ok", record, model, verdict) / ("skip", None, model, reason) / ("reject", ...) / ("fail", None, None, None)"""
        tier = tier or args.tier
        verdict: dict = {}
        model_name = None
        for attempt in range(1, attempts + 1):
            data, model_name = generate(system, prompt, schema, item["id"], tier, purpose)
            if data is None:
                return "fail", None, None, None
            if not data.get("is_relevant", True):
                return "skip", None, model_name, str(data.get("skip_reason") or "記事化の価値なしと判定")
            try:
                record = build(data, model_name)
            except (KeyError, TypeError, ValueError) as error:
                log(f"   [NG] レスポンスの形式が不正: {error}")
                verdict = {"ok": False, "score": None, "problems": [f"形式が不正: {error}"]}
            else:
                pre_gate(record, source_text, context)
                blocked = publish_blocked(record)
                if blocked and blocked not in indexnow.PUBLISH_RETRYABLE:
                    return "skip", None, model_name, f"公開前の門: {indexnow.PUBLISH_FEEDBACK[blocked]}"
                # ニュースの種類に必要な事実（決算の前年比・脆弱性の対象の版・製品の価格など）が、資料にあるのに本文に無い:
                # 事実確認の前に、足りない事実を伝えて作り直す（最後の試行では作り直さず、記録だけして先へ進む。scripts/news_types.py）
                missing = news_types.missing_facts(record.get("newsType") or "", record.get("body") or "", source_text) \
                    if record.get("newsType") and not blocked else []
                if missing:
                    log(f"   [type] 資料にあるのに本文に無い事実: {'・'.join(x.label for x in missing)}"
                        f"{'（最後の試行なので記録だけ）' if attempt >= attempts else ''}")
                if blocked:
                    # 薄い・日本の読者向けの情報がない: 事実確認の前に、問題点を伝えて作り直す（LLM の確認を無駄に使わない）
                    verdict = gate_verdict(blocked)
                elif missing and attempt < attempts and time_left() > EST_SEC_PER_ARTICLE:
                    verdict = {"ok": False, "score": None, "problems": news_types.missing_feedback(record["newsType"], missing)}
                else:
                    verdict = quality.evaluate(record, source_text, kind, context, check_facts=not args.no_gate)
                tag = "合格" if verdict["ok"] else ("読みやすさのみ基準外" if verdict.get("soft") else "不合格")
                r = verdict.get("readability") or {}
                log(f"   [gate] {tag} score={verdict.get('score')} 試行{attempt}/{attempts}"
                    f" 文{r.get('avg_sentence')}字/60字超{r.get('long_share', 0):.0%}/段落{r.get('avg_paragraph')}字(最長{r.get('max_paragraph')})"
                    f" {('/ ' + ' | '.join(verdict['problems'][:3])[:300]) if verdict['problems'] else ''}"
                    f"{(' / 注意: ' + ' | '.join(verdict['warnings'])) if verdict.get('warnings') else ''}")
                log_quality(state, item, kind, verdict, attempt, verdict["ok"], model_name or "")
                if not verdict["ok"] and not verdict.get("soft") and not blocked and verdict.get("fact") is not None \
                        and (quality.MAJOR or record.get("newsType")) and quality.surgical_ok(verdict):
                    # 大きなニュースと、種類の型で書いた記事（newsType。2026-11-03〜）: 致命的でない細かい指摘（言い過ぎ・訳語・補足の程度）は、
                    # 作り直さず指摘された文だけ直す（安い段）。直したあとも事実確認を通す。残った指摘を受理するのは大きなニュースだけ（quality.surgical_fix）
                    try:
                        fixed, info = quality.surgical_fix(record, source_text, kind, verdict.get("problems") or [], context,
                                                           check_facts=not args.no_gate)
                    except Exception as error:  # noqa: BLE001
                        fixed, info = None, f"外科的な修正に失敗（{str(error)[:80]}）"
                    if fixed is None:
                        log(f"   [fix] 指摘の箇所だけの修正では救えず: {info}")
                    else:
                        minor = info.get("accepted_minor") or []
                        log(f"   [fix] 指摘の箇所だけ直して合格 score={info.get('score')}（{info.get('fixed_by')}）"
                            f"{' / 残りの細かい指摘は受理: ' + ' | '.join(minor[:2])[:200] if minor else ''}")
                        record, verdict = fixed, info
                        log_quality(state, item, kind, verdict, attempt, True, str(info.get("fixed_by") or ""))
                if verdict.get("soft"):
                    record, verdict = polish_or_keep(item, kind, record, verdict, source_text, context)
                if verdict["ok"] or verdict.get("soft"):
                    # 書き直し（読みやすさ・指摘の箇所の修正）で短くなっていないか、公開前の門をもう一度通す
                    blocked = publish_blocked(record)
                    if blocked:
                        if blocked not in indexnow.PUBLISH_RETRYABLE:
                            return "skip", None, model_name, f"公開前の門: {indexnow.PUBLISH_FEEDBACK[blocked]}"
                        verdict = gate_verdict(blocked)
                        log(f"   [gate] 公開前の門で不合格（{blocked}）")
                    else:
                        record["qualityScore"] = verdict.get("score")
                        return "ok", record, model_name, verdict
            if attempt < attempts and not verdict.get("unavailable") and time_left() > EST_SEC_PER_ARTICLE:
                prompt = prompt + quality.feedback_block(verdict.get("problems") or [])
                log(f"   [gate] 問題点を伝えて作り直します（{tier}）")
                continue
            break
        reason = "; ".join(verdict.get("problems") or [])[:200]
        last_problem[0] = f"品質ゲート不合格: {reason[:160]}"
        return "reject", None, model_name, reason

    def try_followup(item: dict, text: str, target: tuple) -> str:
        """既存記事に続報を足す。戻り値: updated / no_new / different / reject / fail（different なら新しい記事として作る）"""
        _, path, article, why = target
        log(f"   [followup] 続報候補: {path.stem}（{why}）")
        try:
            check = followup.check_same_topic(article, item, text)
        except Exception as error:  # noqa: BLE001
            log(f"   [followup] 判定に失敗（{str(error)[:120]}）")
            state["failed_web"][item["id"]] = state["failed_web"].get(item["id"], 0) + 1
            return "fail"
        log(
            f"   [followup] same_topic={check['same_topic']} adds_new_facts={check['adds_new_facts']}"
            f" {check['summary_of_new_facts'][:100]}（{check['model']}）"
        )
        if not check["same_topic"]:
            return "different"
        if not check["adds_new_facts"]:
            mark(state, item, skipped=True, reason=f"followup: 新しい事実なし（{path.stem}）"[:200])
            return "no_new"
        try:
            data, model_name = followup.generate_update(article, item, text, check["summary_of_new_facts"], args.tier)
        except Exception as error:  # noqa: BLE001
            log(f"   [NG] 続報の生成に失敗（{str(error)[:160]}）")
            state["failed_web"][item["id"]] = state["failed_web"].get(item["id"], 0) + 1
            return "fail"
        problems = followup.update_problems(data, text)
        score = None
        if not problems and not args.no_gate:
            try:
                fact = followup.fact_check_update(article, data, text)
            except Exception as error:  # noqa: BLE001
                problems = [f"事実確認を実行できなかった（{str(error)[:80]}）"]
            else:
                score = fact["score"]
                if not (fact["grounded"] and fact["score"] >= quality.PASS_SCORE):
                    problems = fact["problems"] or [f"元記事との整合性が低い（score {fact['score']}）"]
        verdict = {"ok": not problems, "score": score, "problems": problems}
        log(f"   [gate] 続報 {'合格' if not problems else '不合格'} score={score} {('/ ' + ' | '.join(problems[:3])[:300]) if problems else ''}")
        log_quality(state, item, "update", verdict, 1, not problems, model_name or "")
        if problems:
            mark(state, item, skipped=True, reason=f"followup quality: {'; '.join(problems)}"[:200], model=model_name)
            return "reject"
        record = followup.apply_update(article, data, item)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        touched.add(path.stem)
        updated_slugs.add(path.stem)
        updated_count[0] += 1
        mark(state, item, updated=path.stem, model=model_name, kind="update", score=score)
        log(f"   [UPDATE] {path.relative_to(m.ROOT)} に続報を追加（{quality._plain_len(str(data['update_markdown']))}字 / {model_name}）")
        return "updated"

    def pick(cand: dict, is_ai: bool, major: bool = False):
        """候補を採るか決める。("take", target|None) / ("next", None)=今回は見送り（mark しない）/ ("drop", None)=mark 済み"""
        if not major:
            reason = prefilter(cand) if is_ai else broad_prefilter(cand)
            if reason:
                mark(state, cand, skipped=True, reason=f"prefilter: {reason}")
                return "drop", None
            if not is_ai and per_feed.get(cand["source_slug"], 0) >= args.per_feed:
                return "next", None  # 次の実行に回す
            if not is_ai and cand.get("section") == "sports" and sports_today(state) >= persona.SPORTS_DAILY_CAP:
                return "next", None  # スポーツは1日の上限まで（集客の入口。大きなものだけ）
            if is_wire(cand) and wire_attempts[0] >= args.max_wire:
                return "next", None  # 配信は1回の上限まで。次の実行に回す
        if recent.has_url(cand["url"]):
            mark(state, cand, skipped=True, reason="dedupe: 同じ出典URL")
            return "drop", None
        if is_stale(cand) and not cand.get("fast"):
            if not major:
                hours = int(source_age(cand).total_seconds() // 3600)
                mark(state, cand, skipped=True, reason=f"stale: 発表から{hours}時間（{STALE_HOURS}時間を超えた発表は通常の記事にしない）")
                return "drop", None
            cand["late"] = True  # 大きなニュースで記事がまだ無いものだけ、速報ではない形で作る
        # 同じ実行で先に扱った同じ話題: 本家の記事ができていれば続報へ、本家が再試行待ちなら次回へ
        act = in_run_action(cand)
        if act and act[0] == "ok":
            return "take", act
        if act and act[0] == "defer":
            log(f"   [defer] {cand['title'][:60]}（{act[1][:80]}）")
            return "next", None
        if act and act[0] == "dup":
            mark(state, cand, skipped=True, reason=f"dedupe: {act[1]}"[:200])
            return "drop", None
        # 直近14日の記事の続報なら、新しい記事ではなく既存記事に足す
        target = followup_target(cand["title"], cand["url"])
        if target and target[0] == "defer":
            return "next", None  # 続報の上限。次の実行に回す
        if not target:
            hit = recent.match(cand["title"], cand["url"])
            if hit and major:
                # 大きなニュースは見出しの似ただけで落とさない。既存記事があれば同じ話題か LLM で確かめる（違えば新しい記事）
                target = followup_for(hit[1], hit[0]) if hit[1] else None
                if target and target[0] == "defer":
                    return "next", None
                if target or not hit[1]:
                    return "take", target
            if hit:
                mark(state, cand, skipped=True, reason=f"dedupe: {hit[0]}"[:200])
                return "drop", None
        if not target:
            # 同じ発表の記事（出典URL・会社＋製品名・珍しい固有名詞。scripts/story.py。サイトの重複判定と同じ基準）
            same = story_recent.match(cand["title"], cand["url"], published=cand.get("published_at"))
            if same:
                if major:
                    target = followup_for(same[0], same[1])
                    if target and target[0] == "defer":
                        return "next", None
                    if target:
                        return "take", target
                mark(state, cand, skipped=True, reason=f"dedupe: 同じ発表の記事あり（{same[1]}）: {same[0]}"[:200])
                return "drop", None
        return "take", target

    def major_retry(item: dict, why: str) -> None:
        """大きなニュースが作れなかった: processed にせず次の実行で再挑戦（major_max_runs 回まで）"""
        tries = state["major_tries"].get(item["id"], 0) + 1
        state["major_tries"][item["id"]] = tries
        if tries >= major_max_runs(item):
            log(f"   [major] {tries} 回の実行で作れなかったため諦めます: {why[:120]}")
            mark(state, item, skipped=True, reason=f"major gave up x{tries}: {why}"[:200])
            run_status[item["id"]] = ("failed", 99)  # 派生の発表で作り直してよい
        else:
            log(f"   [major] 作れなかった（{tries}/{major_max_runs(item)}）。processed にせず次の実行で再挑戦: {why[:120]}")
            run_status[item["id"]] = ("pending", item["id"])

    def process_web(item: dict, lane: str, target, major: bool = False) -> None:
        """公式フィード・配信の1件: 本文取得 → 生成前チェック → 続報 or 新しい記事"""
        nonlocal rejected
        is_ai = lane == "ai"
        seen_add(item)  # 同じ実行の中の似た候補も外す（または続報へ）
        if not major:
            per_feed[item["source_slug"]] = per_feed.get(item["source_slug"], 0) + 1

        log(f"\n[{'fast' if item.get('fast') else ('major' if major else lane)}] {item['source_name']}: {item['title'][:90]}")
        page = item.pop("_page", None)
        try:
            page = page or feeds.fetch_article_text(item["url"])
        except Exception as error:  # noqa: BLE001
            log(f"   [warn] 本文の取得に失敗（{str(error)[:240]}）。フィードの本文を使います")
            page = {"text": "", "image": None}
        text = max([page.get("text") or "", item.get("content") or ""], key=len)
        if cap_with_page_date(item, page):
            log(f"   [date] 記事ページの公開日時で発表の時刻を直しました: {item['published_at'].astimezone(JST):%m-%d %H:%M}")
        if is_stale(item):
            hours = int(source_age(item).total_seconds() // 3600)
            if not major:
                log(f"   [skip] 発表から{hours}時間たっている（{STALE_HOURS}時間を超えた発表は通常の記事にしない）")
                mark(state, item, skipped=True, reason=f"stale: 発表から{hours}時間（記事ページの日付）")
                run_status[item["id"]] = ("failed", feeds.source_rank(item))
                return
            item["late"] = True
            log(f"   [late] 発表から{hours}時間。速報ではない形で作ります（速報投稿・ニュース用サイトマップに出さない）")
        if len(text) < MIN_TEXT_CHARS:
            log(f"   [skip] 本文が短い（{len(text)}字）")
            if major:
                major_retry(item, f"本文が短い（{len(text)}字）")
            elif short_text_retry(state, item):
                log(f"   [retry] 主要企業の公式発表なので processed にせず、{SHORT_RETRY_HOURS}時間は次の回で読み直す")
                run_status[item["id"]] = ("failed", feeds.source_rank(item))
            else:
                mark(state, item, skipped=True, reason=f"short text {len(text)}")
                run_status[item["id"]] = ("failed", feeds.source_rank(item))
            return
        if is_wire(item):
            reason = wire_precheck(item, text)
            if reason:
                log(f"   [skip] {reason}（生成前に除外）")
                mark(state, item, skipped=True, reason=f"precheck: {reason}"[:200])
                run_status[item["id"]] = ("failed", feeds.source_rank(item))
                return
            wire_attempts[0] += 1
        if target:
            outcome = try_followup(item, text, target)
            if outcome != "different":
                if major and outcome == "fail":
                    major_retry(item, "続報の処理に失敗")
                elif outcome == "updated":
                    run_status[item["id"]] = ("article", target[1].stem)
                return

        related = arch.related(item["title"], text, item["url"], "ai" if is_ai else lane)
        if related:
            log(f"   [context] 過去記事 {len(related)} 本: {', '.join(r['slug'] for r in related)}")
        voice_key, voice_text = m.pick_voice()
        retry_rec = (state.get("gate_retry") or {}).get(item["id"]) if not major else None
        if retry_rec:
            item["gate_retry"] = retry_rec.get("tries", 1)
            log(f"   [retry] 品質ゲートの再挑戦（{item['gate_retry']}/{GATE_RETRY_MAX}）: 資料を増やし前回の問題点を伝えて作り直す"
                f" / 前回: {str(retry_rec.get('reason', ''))[:120]}")
        genre, system, prompt, schema, kind, check_context, bundle = compose_with_facts(item, text, lane, related, voice_text, page)
        if retry_rec and retry_rec.get("reason"):
            prompt += quality.feedback_block([str(retry_rec["reason"])])

        def build(data, model, item=item, page=page, voice_key=voice_key, genre=genre, related=related, bundle=bundle):
            if str(data.get("genre", "")).strip() not in GENRES_BY_SLUG:
                data = {**data, "genre": genre.slug}
            return attach_related(build_web_record(item, data, page, model, voice_key, bundle), data, related)

        started = time.time()
        if major:
            log(f"   [major] tier={major_tier()} / 最大{MAJOR_ATTEMPTS}回試行（これまでの失敗 {state['major_tries'].get(item['id'], 0)} 回）")
        # 事実確認には発信元・公開日時も渡す（記事に書いた「〇〇によると」「10月26日に発表」を創作と誤判定させない）。
        # 大きなニュースは長さの基準を緩め、細かい指摘は外科的に直して公開する（quality.MAJOR）
        quality.MAJOR = bool(major)
        purpose = article_purpose(item, lane, major)
        log(f"   [llm] 段: {purpose}（Claude API の配分 → {'Claude CLI → ' if purpose in llm.API_CLI_PURPOSES else ''}無料モデル）")
        try:
            status, record, model_name, info = gated(
                item, kind, system, prompt, schema, f"{source_header(item)}\n\n{text}", build, check_context,
                tier=major_tier() if major else None, attempts=MAJOR_ATTEMPTS if major else 2, purpose=purpose,
            )
        finally:
            quality.MAJOR = False
        if status != "ok":
            if status in ("skip", "reject"):
                log(f"   [{status}] {info}（{model_name}）")
                rejected += status == "reject"
            if major and status != "skip":
                # 大きなニュースは長さ等で落ちても processed にしない。
                # ただし「記事化の価値なし」（skip。例: 社内の人事施策）は作り直しても同じなので見送りで確定する
                major_retry(item, f"{status}: {info or '生成に失敗'}")
                return
            run_status[item["id"]] = ("failed", feeds.source_rank(item))
            if status == "reject" and strong_candidate(item):
                tries = gate_retry_keep(state, item, info or "")
                if tries:
                    log(f"   [retry] 有力な候補なので捨てずに、{GATE_RETRY_WAIT_HOURS:g}時間後に資料を増やして再挑戦（{tries}/{GATE_RETRY_MAX}）"
                        f" 理由: {str(info or '')[:160]}")
                    return
                log(f"   [retry] 再挑戦 {GATE_RETRY_MAX} 回でも品質ゲートを通らなかったので見送りで確定: {str(info or '')[:160]}")
            if status in ("skip", "reject"):
                mark(state, item, skipped=True, reason=f"{'quality: ' if status == 'reject' else ''}{info}"[:200], model=model_name)
            return
        # 速報（scripts/breaking_post.py がデプロイ直後に SNS へ出す）。
        # 速報レーンの記事は Worker が主要企業の公式発表と判定したものなので、必ず速報にする（公開したらすぐ SNS へ）
        # 通常の収集から来たものは、発表から12時間以内のときだけ速報にする（古い発表に【速報】を付けない）
        fresh = item.get("published_at") and item["published_at"] > datetime.now(timezone.utc) - timedelta(hours=12)
        # レーダーの話題は、公式ページの日付で発表から12時間以内と分かったときだけ速報（見出しの時刻では速報にしない）
        radar_ok = not item.get("radar") or (fresh and not item.get("published_guessed"))
        if major and ((is_breaking(item) and fresh) or (item.get("fast") and radar_ok)) and not item.get("late") and not item.get("watch"):
            record["breaking"] = True
        if item.get("late"):
            record["late"] = True
        finalize_facts(record, text)
        attach_charts(record)
        attach_rivals(record, fast_mode, time_left())
        stamp_times(record, item, run_detected_at)
        path = m.write_article(record)
        recent.add(record["title"], record["sourceUrl"], record.get("keywords"))
        story_recent.add(record)
        touched.add(record["slug"])
        run_status[item["id"]] = ("article", record["slug"])
        mark(state, item, slug=record["slug"], model=model_name, section=lane, score=record.get("qualityScore"),
             **({"offPersona": True} if item.get("_off_persona") else {}),
             **({"major": True} if major else {}), **({"breaking": True} if record.get("breaking") else {}))
        state["major_tries"].pop(item["id"], None)
        (state.get("gate_retry") or {}).pop(item["id"], None)
        if item.get("_off_persona") and not major:
            created_off[0] += 1  # 読者像から外れた一般の話題は、1日の上限・この実行の本数に数えない
        else:
            created["major" if major else ("ai" if is_ai else "broad")] += 1
        per_section[record["genreSlug"]] = per_section.get(record["genreSlug"], 0) + 1
        log(
            f"   [OK{' major' if major else ''}] {path.relative_to(m.ROOT)} ({record['genreSlug']} / {len(record['body'])}字 / "
            f"score {record.get('qualityScore')} / {model_name} / {time.time() - started:.0f}s"
            f"{' / 過去記事 ' + str(len(record['relatedSlugs'])) + '本' if record.get('relatedSlugs') else ''})"
        )
        if item.get("fast"):
            log(f"   [fast] {latency_note(record)}（公開はこのあとのデプロイ・約3分）")

    try:
        # 大きなニュース（主要企業の新モデル・新製品）は、どのレーンよりも先に quality で作る
        for item in majors:
            if created["major"] >= args.max_major or time_up():
                break
            if llm.all_exhausted("quality", visibility="high") and not (fast_mode and not llm.all_exhausted("bulk")):
                log("[major] quality のモデルが使えないため、大きなニュースは次回に回します（processed にしない）")
                break
            if item.get("fast") and item["id"] in state["processed_web"]:
                log(f"\n[fast] すでに処理済み: {item['title'][:80]}")
                continue
            action, target = pick(item, True, major=True)
            if action != "take":
                continue
            # AI フィードの候補は ai レーン、幅広い話題のフィード（Apple 日本など）はそのセクションの形で作る
            lane = item.get("section") if item["source_slug"] not in AI_SLUGS and item.get("section") in GENRES_BY_SLUG else "ai"
            process_web(item, lane, target, major=True)

        # 話題は鮮度が命なので先に処理する
        for q in queue:
            if created["trend"] >= trend_limit or total_created() >= remaining or time_up():
                break
            if llm.all_exhausted(args.tier):
                break
            log(f"\n[trend] {q['title']}（{q.get('genre')} / traffic {q.get('traffic', 0):,}）")
            target = followup_target(q["title"])
            if target and target[0] == "defer":
                log("   [skip] 続報の上限に達したため次回に回す")
                continue
            act = None if target else in_run_action({**q, "url": ""})
            if act and act[0] == "ok":
                target = act
            elif act and act[0] == "defer":
                log(f"   [defer] {act[1][:80]}")
                continue
            dup = None if target else ((act[1] if act else None) or recent.find(q["title"]))
            if dup:
                log(f"   [skip] 重複: {dup}")
                mark(state, q, skipped=True, reason=f"dedupe: {dup}"[:200])
                used_queue_ids.add(q["id"])
                continue
            fetched = fetch_trend_sources(q)
            if len(fetched) < TREND_MIN_SOURCES:
                log(f"   [skip] 本文の取れた報道が {len(fetched)} 本")
                mark(state, q, skipped=True, reason=f"trend sources {len(fetched)}")
                used_queue_ids.add(q["id"])
                continue
            seen_add({**q, "url": ""})
            source_text = "\n\n".join(f"【{s.get('source') or '報道'}】\n{s['text']}" for s in fetched)
            if target:
                pseudo = {
                    "id": q["id"], "url": fetched[0]["url"], "title": fetched[0].get("title") or q["title"],
                    "source_name": trend_source_name(fetched), "source_slug": "trends", "published_at": q["published_at"],
                }
                outcome = try_followup(pseudo, source_text, target)
                if outcome != "different":
                    if outcome != "fail" or state["failed_web"].get(q["id"], 0) >= MAX_FAILURES:
                        used_queue_ids.add(q["id"])
                    continue
            genre = GENRES_BY_SLUG.get(q.get("genre") or "", AI)
            related = arch.related(q["title"], source_text, fetched[0]["url"], genre.slug)
            context = archive.prompt_block(related) + japan_availability.prompt_block(q["title"], source_text)
            voice_key, voice_text = m.pick_voice()
            system = (
                f"{m.SYSTEM_INSTRUCTION_BASE}\n\n{genre.persona}\n\n"
                f"{TREND_SOURCE_NOTE.format(rules=trends.EDITORIAL_RULES)}\n\n{glossary.prompt_block()}\n\n## 今回の語り口\n{voice_text}"
            )
            if related:
                log(f"   [context] 過去記事 {len(related)} 本: {', '.join(r['slug'] for r in related)}")
            started = time.time()
            status, record, model_name, info = gated(
                q, "trend", system, build_trend_prompt(q, fetched, genre, context), schema_for(TREND_SCHEMA, genre.slug), source_text,
                lambda data, model, q=q, fetched=fetched, voice_key=voice_key, related=related: attach_related(
                    build_trend_record(q, fetched, data, model, voice_key), data, related
                ),
                check_context_for(q["title"], source_text, related),
                purpose=article_purpose(q, genre.slug),
            )
            if status == "fail":
                if state["failed_web"].get(q["id"], 0) >= MAX_FAILURES:
                    used_queue_ids.add(q["id"])
                continue
            used_queue_ids.add(q["id"])
            if status in ("skip", "reject"):
                log(f"   [{status}] {info}（{model_name}）")
                mark(state, q, skipped=True, reason=f"{'quality: ' if status == 'reject' else ''}{info}"[:200], model=model_name)
                rejected += status == "reject"
                run_status[q["id"]] = ("failed", 2)
                continue
            attach_charts(record)
            stamp_times(record, q, run_detected_at)
            path = m.write_article(record)
            recent.add(record["title"], record["sourceUrl"], record.get("keywords"))
            story_recent.add(record)
            touched.add(record["slug"])
            run_status[q["id"]] = ("article", record["slug"])
            mark(state, q, slug=record["slug"], model=model_name, kind="trend", score=record.get("qualityScore"))
            created["trend"] += 1
            log(f"   [OK] {path.relative_to(m.ROOT)} ({len(record['body'])}字 / {model_name} / {time.time() - started:.0f}s)")

        # 公式フィード（AI）と幅広い話題を、レーンごとに1本ずつ交互に処理する
        order = lane_order(lanes)
        pointers = {lane: 0 for lane in order}
        active = list(order)
        deferred_off: list[tuple[str, dict]] = []

        def next_candidate(lane: str, is_ai: bool):
            """レーンの次の候補 (item, target) か (None, None)。幅広い話題の「読者像から外れた一般の話題」は deferred_off に回す"""
            items = lanes[lane]
            while pointers[lane] < len(items):
                cand = items[pointers[lane]]
                pointers[lane] += 1
                if not is_ai and off_persona(cand):
                    deferred_off.append((lane, cand))
                    continue
                action, found = pick(cand, is_ai)
                if action == "take":
                    return cand, found
            return None, None
        while active:
            if total_created() >= remaining or time_up():
                break
            if llm.all_exhausted(args.tier):
                log("[info] すべてのモデルが無料枠の上限に達したため終了します")
                break
            for lane in list(active):
                if total_created() >= remaining or time_up() or llm.all_exhausted(args.tier):
                    break
                is_ai = lane == "ai"
                if (is_ai and created["ai"] >= ai_limit) or (not is_ai and created["broad"] >= broad_limit):
                    active.remove(lane)
                    continue
                # このレーンで次に試せる候補を探す（足切り・重複・フィード上限は飛ばす。読者像から外れた一般の話題は後回し）
                item, target = next_candidate(lane, is_ai)
                if item is None:
                    active.remove(lane)
                    continue
                process_web(item, lane, target)
                # 優先レーンは1巡で2本まで試す（上限・時間は次の巡回の頭で確かめる）
                for _ in range(lane_turns(lane) - 1):
                    if total_created() >= remaining or time_up() or llm.all_exhausted(args.tier):
                        break
                    if created["broad"] >= broad_limit:
                        break
                    extra, target = next_candidate(lane, is_ai)
                    if extra is None:
                        break
                    process_web(extra, lane, target)
        # 読者像から外れた一般の話題は、読者像の候補を回し終えてから、1日 OFF_PERSONA_DAILY_CAP 本まで（上限・本数には数えない）
        for lane, cand in deferred_off:
            if off_done_today + created_off[0] >= OFF_PERSONA_DAILY_CAP or time_up() or llm.all_exhausted(args.tier):
                if deferred_off:
                    log(f"[persona] 読者像から外れた一般の話題 {len(deferred_off)} 件は見送り（今日 {off_done_today + created_off[0]} 本 / 上限 {OFF_PERSONA_DAILY_CAP}）")
                break
            action, target = pick(cand, False)
            if action == "take":
                cand["_off_persona"] = True
                log(f"\n[persona] 読者像から外れた一般の話題（後回しの分）: {cand['title'][:60]}")
                process_web(cand, lane, target)
    finally:
        if fast_mode:
            write_fast_results(fast_items, fast_results, state, run_status, run_detected_at)
        prune_processed(state)
        # 再挑戦の記録も古いものは消す（processed になったもの・対象期間外のもの）
        for key in list(state["major_tries"]):
            if key in state["processed_web"] or (
                args.max_major > 0 and key not in major_ids and not args.only and not args.source and not fast_mode
            ):
                state["major_tries"].pop(key, None)
        m.save_state(state)
        trends.remove_from_queue(used_queue_ids)

    if not fast_mode and not args.only and not args.source:
        # 見張り: 候補があるのに 0 本の回が2回続いたら state/pipeline_health.json に「止まっている」と書く（issue は workflow が立てる）
        candidates = len(majors) + (len(queue) if trend_limit > 0 else 0) + (len(ai_items) if ai_limit > 0 else 0) \
            + (len(broad_items) if broad_limit > 0 else 0)
        reason = last_problem[0]
        if llm.all_exhausted(args.tier):
            reason = "無料モデルも Claude API も使えない（無料枠切れ・API 停止） " + reason
        if not reason:
            candidates = 0  # 失敗が無く 0 本（見送り・本文が短いなど）は「止まっている」に数えない
        try:
            health = pipeline_health.record(total_created() + updated_count[0], candidates, reason)
            if health.get("status") == "stalled":
                log(f"[health] 止まっています: 0本の回が {health.get('consecutive_zero')} 回続いた / {health.get('reason')}")
        except Exception as error:  # noqa: BLE001 - 見張りの失敗で記事づくりは止めない
            log(f"[health] 記録に失敗: {str(error)[:100]}")
    elif total_created() + updated_count[0] > 0:
        # 速報レーン・記事を指定した回でも、記事ができたら「止まっている」の印は外す（0本の回は数えない）
        try:
            pipeline_health.record(total_created() + updated_count[0], 0)
        except Exception as error:  # noqa: BLE001
            log(f"[health] 記録に失敗: {str(error)[:100]}")

    log(
        f"\n[done] 新規記事 {total_created()} 本（大きなニュース {created['major']} / AI {created['ai']} / 幅広い話題 {created['broad']}"
        f" / 話題 {created['trend']}）"
        f" / 続報で更新 {updated_count[0]} 本（上限に数えない）"
        f" / 配信の生成 {wire_attempts[0]} 本（上限 {args.max_wire}）"
        f" / 品質ゲート不合格 {rejected} 本 / ジャンル別 {per_section or '-'} / 今日の使用回数 {llm.usage_today()}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
