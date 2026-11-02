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
直近4日の記事と同じ話題の候補は dedupe.py で生成前に外す。
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
import re
import urllib.parse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive  # noqa: E402
import dedupe  # noqa: E402
import fast_lane  # noqa: E402
import feeds  # noqa: E402
import followup  # noqa: E402
import llm  # noqa: E402
import main as m  # noqa: E402
import quality  # noqa: E402
import trends  # noqa: E402
from genres import AI, ALL_GENRES, BROAD_GENRES, GENRES, GENRES_BY_SLUG  # noqa: E402

RUN_TIME_BUDGET_SEC = 25 * 60
MAX_FAILURES = 2
MIN_TEXT_CHARS = 600
DEDUPE_DAYS = 4
QUALITY_LOG_KEEP = 300
# 1本あたりの目安（生成＋事実確認）。残り時間がこれを切ったら新しい記事に手を付けない
EST_SEC_PER_ARTICLE = 150
JST = timezone(timedelta(hours=9))
BROAD_SLUGS = [g.slug for g in BROAD_GENRES]
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


def rank_bucket(item: dict) -> int:
    """レーン内の並べ替え用。公式 → 官公庁・企業 → 他社の対応告知 → 配信"""
    return {0: 0, 1: 0, 2: 1, 3: 2}.get(feeds.source_rank(item), 3)


WEB_SOURCE_NOTE = """## 今回の取材対象（重要）
今回は動画ではなく、公式ブログ・ニュースルーム・プレスリリースなどの「文章」が一次資料です。
上の規則や章立てにある「動画」「映像」「音声」「スライド」は、すべて「元記事」と読み替えてください。
- 元記事の文をそのまま訳したり書き写したりしない。内容を理解したうえで、自分の言葉で要約・解説する。
  引用は短いフレーズ（1文の一部、30字程度まで）にとどめ、「」で括る。
- 本文中で必ず一度は出典（発信元の名前）に触れる（例：「〇〇の公式ブログによると」）。
- 元記事に書かれていない事実・数値・価格・日付・発言は作らない。
  一般的な背景知識で補う場合は「（編集部注）」と明記し、具体的な数値は書かない。
- 読者にとって重要で元記事から確認できない条件は不明と示す。材料のない章を無理に作らず、水増ししない。
- 編集部の見方は、確認済みの事実を根拠にし、「編集部の見方」と明示して事実と区別する。根拠のない体験談・比較・推測は書かない。"""

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
- 各 `###` セクションには、元記事にある具体的な固有名詞・数値を含める。
- 構成で指定した「## 概要」「## まとめ」以外の見出しは、読者の疑問の形で中身が分かる言葉にする。
- 表は「表の使い方」に従う（比べるときと早見表だけ）。

# 出力する記事の構成（body_markdown に Markdown で記述）
次の見出し構成（`##` 見出し）を基本にする。「動画」は「元記事」と読み替え、元記事に材料がない章は省く。重要な不明点は確認できていないと明記する。

{outline}

# 各フィールドの要件
{field_notes}
- title: 32〜48文字。報道記事の見出しとして成立する体裁にする。
  **その分野に詳しくない人にも届く見出しにする（最重要）**: 固有名詞だけで終わらせず、「何の・どんな種類のものか」と「何が新しい／何が変わるか」を必ず入れる。
  - 会社・製品は一言で正体を添える:「ChatGPTのOpenAIが」「Googleの生成AI『Gemini』に」「AI半導体のNVIDIAが」
  - 種類を言う:「新しいAIモデル」「新機能」「新サービス」「新登場」「値下げ」「日本でも提供開始」
  - 変化を言う:「料金は半分に」「パソコン操作もAIにお任せ」「何が変わった？」
  - 専門用語（API・トークン・ベンチマーク・推論など）は見出しに使わないか、使うなら日常語を添える。
  - 根拠に具体的な用途があれば、旅行の候補探し・書類の編集・会議の整理のように読者が想像できる行動を優先する。デモや一部地域限定なら、誰でも今すぐ使えると誤解させない。用途を根拠なしに作らない。会社の事業と発表物を混同せず、AIモデルを半導体と呼ばない。
  良い例:「ChatGPTのOpenAIが新AIモデル『GPT-6.1 Terra』 料金は半分、パソコン操作も得意に」
  悪い例:「OpenAIがGPT-6.1 Terraを発表 コスト半減でPC操作と視覚理解を高速化」（詳しい人にしか伝わらない）
  主要SEOキーワードを前方に置き、**感嘆符・煽り語（衝撃／ヤバい／ついに／必見）は使わない**。
  元記事のタイトルの直訳にしない。
- seo_title: 検索結果に表示する短い題名。**全角24〜30文字**（英数字は半角0.5文字換算）。
  製品名・企業名・機能名を**先頭**に置き、何が分かる記事かを続ける。記号は「｜」「：」を最大1つまで。
  **新しい製品・機能・モデルの発表**なら、日本の人が実際に検索する形にする:「〇〇とは？日本で使える？料金も」「〇〇の使い方と料金｜日本での提供状況」など、
  名前の直後に「とは／使い方／料金／日本で使える」のどれかを入れる（発表直後は日本語の記事がまだ少なく、この形の検索で1位を取りやすい）。
- threads_post: Threads・X に出す投稿文。末尾の「Threads 紹介文（threads_post）のルール」と「書き出し」に従う（完結型・2〜4行・URLなし）。
- easy_markdown: 「かんたんに読む」版（本文の必要な内容を過不足なく、短く分かりやすく）。body_markdown の内容をやさしく言い直したもの。
  見出しは次の4つを「## 」でこの順に使う：## ひとことで言うと / ## 何がすごいの？ / ## 自分に関係ある？ / ## 使ってみるには
  必ず「## ひとことで言うと」から始める。専門用語は使わない（使うなら直後に（＝〜のこと））。身近な例えを1つ以上入れる。
  body_markdown に無い事実・数字・価格は書かない。「使ってみるには」は verdict と矛盾させない。です・ます調。
- lede: 記事冒頭のリード文。150〜200字。結論を先に置き、発信元の名前を含める。
- description: 100〜130文字のメタディスクリプション。結論を含める。
- mentioned_tools: 記事中で言及した製品・サービス・企業の**正式名称**（3〜12個）。本文で触れたものだけ。
- slug_en: URL用の英小文字スラッグ。半角英数とハイフンのみ、3〜6単語、40文字以内。
- keywords: 検索されうる日本語キーワード 8〜12個。
- tags: 記事分類用のタグ 3〜6個。
- highlights: 「3秒でわかる要点」ちょうど3つ。各40〜60文字で、元記事にある数値や固有名詞を含める。
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


BROAD_SOURCE_NOTE = """## 幅広い話題の編集ルール（必ず守る）
- 資料はプレスリリース・官公庁・企業・研究機関の公式発表。発表者の宣伝文句（「業界初」「最高」等）は事実として断定せず、
  「〇〇によると」「〇〇は…としています」と発信元に帰属させる。
- 読者目線の3点を必ず書く: 概要（ひとことで言うと何か） / 自分に関係あるか / 使える・買える・申し込めるか。
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
- 各 `###` セクションには、元記事にある具体的な固有名詞・数値・日付を含める。
- 構成で指定した「## 概要」「## まとめ」以外の見出しは、読者の疑問の形で中身が分かる言葉にする。
- 表は「表の使い方」に従う（比べるときと早見表だけ）。

# 出力する記事の構成（body_markdown に Markdown で記述）
次の `##` 見出し構成を基本にする。元記事に材料がない章は省く。重要な不明点は確認できていないと明記する。

{outline}

# 各フィールドの要件
{field_notes}
- title: 32〜48文字。報道記事の見出しとして成立する平叙文。主要キーワードを前方に置き、
  **その分野に詳しくない人にも届く見出しにする（最重要）**: 固有名詞だけで終わらせず、「何の・どんな種類のものか」と「何が新しい／何が変わるか」を必ず入れる。
  - 会社・製品は一言で正体を添える:「ChatGPTのOpenAIが」「Googleの生成AI『Gemini』に」「AI半導体のNVIDIAが」
  - 種類を言う:「新しいAIモデル」「新機能」「新サービス」「新登場」「値下げ」「日本でも提供開始」
  - 変化を言う:「料金は半分に」「パソコン操作もAIにお任せ」「何が変わった？」
  - 専門用語（API・トークン・ベンチマーク・推論など）は見出しに使わないか、使うなら日常語を添える。
  - 根拠に具体的な用途があれば、旅行の候補探し・書類の編集・会議の整理のように読者が想像できる行動を優先する。デモや一部地域限定なら、誰でも今すぐ使えると誤解させない。用途を根拠なしに作らない。会社の事業と発表物を混同せず、AIモデルを半導体と呼ばない。
  良い例:「ChatGPTのOpenAIが新AIモデル『GPT-6.1 Terra』 料金は半分、パソコン操作も得意に」
  悪い例:「OpenAIがGPT-6.1 Terraを発表 コスト半減でPC操作と視覚理解を高速化」（詳しい人にしか伝わらない）
  **感嘆符・煽り語（衝撃／ヤバい／ついに／必見／知らないと損）は使わない**。元記事のタイトルをそのまま使わない。
- seo_title: 全角24〜30文字。固有名詞（商品名・制度名・組織名）を先頭に置く。記号は「｜」「：」を最大1つまで。
- threads_post: Threads・X に出す投稿文。末尾の「Threads 紹介文（threads_post）のルール」と「書き出し」に従う（完結型・2〜4行・URLなし）。
- easy_markdown: 「かんたんに読む」版（本文の必要な内容を過不足なく、短く分かりやすく）。必ず「## ひとことで言うと」から始め、
  ## ひとことで言うと / ## 何がすごいの？ / ## 自分に関係ある？ / ## 使ってみるには の4見出しをこの順に使う。
  「使ってみるには」は、使う・買う・申し込む方法か、それが無い話題なら読者が取れる行動（公式情報の確認など）を書く。
  専門用語は使わない（使うなら直後に（＝〜のこと））。身近な例えを1つ以上入れる。body_markdown に無い事実は書かない。です・ます調。
- lede: 150〜200字。結論を先に置き、発信元の名前を含める。
- description: 100〜130文字のメタディスクリプション。結論を含める。
- mentioned_tools: 記事中で言及した商品・サービス・企業・制度の正式名称（0〜12個）。本文で触れたものだけ。
- slug_en: URL用の英小文字スラッグ。半角英数とハイフンのみ、3〜6単語、40文字以内。
- keywords: 検索されうる日本語キーワード 8〜12個。tags: 3〜6個。
- highlights: 「3秒でわかる要点」ちょうど3つ。各40〜60文字で、元記事にある数値や固有名詞を含める。
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


def build_broad_prompt(item: dict, text: str, genre, context: str = "") -> str:
    prompt = BROAD_PROMPT_TEMPLATE.format(
        source_name=item["source_name"],
        title=item["title"],
        published_date=item["published_at"].astimezone(JST).strftime("%Y-%m-%d"),
        url=item["url"],
        genre_name=f"{genre.slug}（{genre.name}）",
        text=text,
        outline=genre.outline,
        field_notes=genre.field_notes,
        topic_guard=f"  **このジャンルの対象範囲**: {genre.topic_guard}",
        genre_choices="\n".join(f"  - {g.slug}: {g.name}" for g in ALL_GENRES),
    )
    return prompt + context + "\n\n" + m.threads_hooks.prompt_block()


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

TREND_SOURCE_NOTE = """## 今回の取材対象（重要）
今回は動画ではなく、日本でいま話題になっているキーワードと、それを報じた複数の報道記事が資料です。
上の規則にある「動画」「映像」「音声」「スライド」は、すべて「関連報道」と読み替えてください。
- 報道の文をそのまま書き写さない。内容を理解したうえで自分の言葉で要約・解説する。
  引用は短いフレーズ（30字程度まで）にとどめ、「」で括る。
- 事実を書くたびに「〇〇（媒体名）によると」のように出典の媒体名を示す。複数の報道を突き合わせて書く。
- 報道に書かれていない事実・数値・日付・発言は作らない。

## トレンド記事の編集ルール（必ず守る）
{rules}"""

TREND_OUTLINE = """## 概要
短い文3〜4つで、何が話題になっていて、読者に何が関係するのかを専門用語なしで説明する（1段落は1〜3文）。

## なぜいま話題？
きっかけ（発表・発売・料金改定・障害・制度変更など）を、報道に書かれた事実だけで説明する。

## 報道で分かっていることは？
各報道の内容を `###` 小見出し（中身が分かる言葉か疑問の形）で整理し、媒体名を明示する。報道間で食い違う点はその旨を書く。

## 自分にとって何が変わる？
お金・手間・使い方・注意点など、生活や仕事への具体的な影響。

## もう使える？申し込める？
話題がサービス・製品・制度など読者が実際に使う/申し込むものの場合だけ書く。当てはまらない話題ではこの章を省く。

## まだ分からないことは？
報道で明らかになっていない点を「現時点では発表されていない」と明記する。

## まとめ
読者が次に取るべき行動を3行程度で。"""

TREND_PROMPT_TEMPLATE = """日本でいま話題になっているキーワードについて、関連報道を読み解き、日本語のSEO記事を作成してください。

# 話題
- キーワード: {keyword}
- 話題の度合い: {summary}
- 想定ジャンル: {genre_name}

# 関連報道（本文は自動抽出。メニュー等の残骸は無視する）
{sources}

# 品質基準
- 本文は報道の情報量と読者の疑問に合わせた長さにする。文字数のために水増ししない。必要な条件や背景があれば省かない。
- 構成で指定した「## 概要」「## まとめ」以外の見出しは、読者の疑問の形で中身が分かる言葉にする。
- 表は「表の使い方」に従う（比べるときと早見表だけ）。

# 出力する記事の構成（body_markdown に Markdown で記述）
{outline}

# 各フィールドの要件
- title: 32〜48文字。「何が起きたか」が分かる平叙文。感嘆符・煽り語（衝撃／ヤバい／終了／知らないと損）は使わない。
  **その分野に詳しくない人にも届く見出しにする（最重要）**: 固有名詞だけで終わらせず、「何の・どんな種類のものか」と「何が新しい／何が変わるか」を必ず入れる。
  - 会社・製品は一言で正体を添える:「ChatGPTのOpenAIが」「Googleの生成AI『Gemini』に」「AI半導体のNVIDIAが」
  - 種類を言う:「新しいAIモデル」「新機能」「新サービス」「新登場」「値下げ」「日本でも提供開始」
  - 変化を言う:「料金は半分に」「パソコン操作もAIにお任せ」「何が変わった？」
  - 専門用語（API・トークン・ベンチマーク・推論など）は見出しに使わないか、使うなら日常語を添える。
  - 根拠に具体的な用途があれば、旅行の候補探し・書類の編集・会議の整理のように読者が想像できる行動を優先する。デモや一部地域限定なら、誰でも今すぐ使えると誤解させない。用途を根拠なしに作らない。会社の事業と発表物を混同せず、AIモデルを半導体と呼ばない。
  良い例:「ChatGPTのOpenAIが新AIモデル『GPT-6.1 Terra』 料金は半分、パソコン操作も得意に」
  悪い例:「OpenAIがGPT-6.1 Terraを発表 コスト半減でPC操作と視覚理解を高速化」（詳しい人にしか伝わらない）
- seo_title: 全角24〜30文字。検索されるキーワードを先頭に置く。記号は「｜」「：」を最大1つまで。
- threads_post: Threads・X に出す投稿文。末尾の「Threads 紹介文（threads_post）のルール」と「書き出し」に従う（完結型・2〜4行・URLなし）。
- easy_markdown: 「かんたんに読む」版（本文の必要な内容を過不足なく、短く分かりやすく）。必ず「## ひとことで言うと」から始め、
  ## ひとことで言うと / ## 何がすごいの？ / ## 自分に関係ある？ / ## 使ってみるには の4見出しをこの順に使う。
  話題が「使う」ものでない場合、「使ってみるには」では読者が取れる行動（公式情報の確認など）を書く。
  専門用語は使わない。body_markdown に無い事実は書かない。です・ます調。
- lede: 150〜200字。なぜ話題か＋結論を先に。
- description: 100〜130文字。
- mentioned_tools: 記事中で言及した製品・サービス・企業・制度の正式名称（0〜12個）。
- slug_en: 英小文字とハイフン、3〜6単語、40文字以内。
- keywords: 8〜12個。tags: 3〜6個。
- highlights: ちょうど3つ、各40〜60文字。報道にある数値や固有名詞を含める。
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
    prompt = TREND_PROMPT_TEMPLATE.format(
        keyword=item["title"],
        summary=item.get("summary", ""),
        genre_name=genre.name,
        sources="\n\n".join(blocks),
        outline=TREND_OUTLINE,
        genre_choices="\n".join(f"  - {g.slug}: {g.name}" for g in GENRES),
    )
    return prompt + context + "\n\n" + m.threads_hooks.prompt_block()


def build_trend_record(item: dict, fetched: list[dict], data: dict, model_name: str, voice_key: str) -> dict:
    genre = GENRES_BY_SLUG.get(item.get("genre") or "", AI)
    pseudo = {
        "id": item["id"],
        "url": fetched[0]["url"],
        "title": item["title"],
        "source_name": trend_source_name(fetched),
        "source_slug": "trends",
        "published_at": item["published_at"],
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


def build_prompt(item: dict, text: str, context: str = "") -> str:
    genre = AI
    prompt = WEB_PROMPT_TEMPLATE.format(
        source_name=item["source_name"],
        title=item["title"],
        published_date=item["published_at"].astimezone(JST).strftime("%Y-%m-%d"),
        url=item["url"],
        text=text,
        outline=genre.outline,
        field_notes=genre.field_notes,
        topic_guard=f"  **このジャンルの対象範囲**: {genre.topic_guard}" if genre.topic_guard else "",
        genre_choices="\n".join(f"  - {g.slug}: {g.name}（{g.description[:48]}…）" for g in GENRES),
    )
    return prompt + context + "\n\n" + m.threads_hooks.prompt_block()


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
- 報道の見立て・推測・未確認の情報は事実として書かない。元記事の文を書き写さず、自分の言葉で要約する。"""


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


def build_web_record(item: dict, data: dict, page: dict, model_name: str, voice_key: str) -> dict:
    resolved = GENRES_BY_SLUG.get(str(data.get("genre", "")).strip(), AI)
    thumbnail = page.get("image") or item.get("image") or ""
    record = {
        "slug": m.unique_slug(m.slugify(data.get("slug_en", "")), item["id"][:8]),
        "sourceType": "web",
        "sourceUrl": item["url"],
        "sourceName": item["source_name"],
        "title": str(data["title"]).strip(),
        "seoTitle": str(data.get("seo_title", "")).strip(),
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
        "verdict": m.clean_verdict(data.get("verdict")),
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
    affiliates = m.match_affiliates(record, data.get("mentioned_tools") or [])
    if affiliates:
        record["affiliates"] = affiliates
        record["body"] = m.linkify_first_mention(record["body"], affiliates)
        log(f"   [aff] {len(affiliates)}件: {', '.join(a['name'] for a in affiliates)}")
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


def generated_today(state: dict) -> int:
    today = datetime.now(JST).date()
    count = 0
    for value in (state.get("processed_web") or {}).values():
        if isinstance(value, dict) and value.get("slug"):
            try:
                if datetime.fromisoformat(value["at"]).astimezone(JST).date() == today:
                    count += 1
            except (KeyError, ValueError):
                continue
    return count


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
    entries = state.setdefault("quality_log", [])
    entries.append(entry)
    del entries[:-QUALITY_LOG_KEEP]


def tag_sections(items: list[dict]) -> list[dict]:
    """幅広い話題の候補にセクション（ジャンル slug）を付ける"""
    for item in items:
        feed = feeds.FEEDS_BY_SLUG.get(item["source_slug"], {})
        section = feed.get("section")
        item["section"] = (feeds.classify_section(item) or feed.get("fallback")) if section == "auto" else section
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


def lane_order(lanes: dict[str, list[dict]]) -> list[str]:
    """ai を先頭に、幅広い話題は時刻で開始位置をずらして毎回同じセクションが先にならないようにする"""
    broad = [s for s in BROAD_SLUGS if s in lanes]
    if broad:
        shift = datetime.now(JST).hour % len(broad)
        broad = broad[shift:] + broad[:shift]
    return (["ai"] if "ai" in lanes else []) + broad


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
        published = (
            item.get("published_at")
            or fast_lane.parse_time(meta.get("publishedAt"))
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

    if fast_mode:
        # 速報レーン: 待たせないよう、生成・事実確認・続報の判定をすべて quality の段で行い、ほかのレーンは回さない
        quality.CHECK_TIER = "quality"
        followup.CHECK_TIER = "quality"
        args.tier = "quality"
        args.trends = args.max = args.max_broad = 0

    done_today = generated_today(state)
    remaining = (args.daily_cap - done_today) if args.daily_cap else 10**6
    cap_reached = remaining <= 0
    if cap_reached and args.max_major <= 0 and not fast_mode:
        log(f"[info] 今日はすでに {done_today} 本生成済み（上限 {args.daily_cap}）")
        return 0
    if cap_reached:
        log(f"[info] 今日はすでに {done_today} 本生成済み（上限 {args.daily_cap}）。大きなニュースだけ確認します")
    trend_limit = 0 if cap_reached else min(args.trends, remaining)
    ai_limit = 0 if cap_reached else (args.max if args.section in ("ai", "all") else 0)
    broad_limit = 0 if cap_reached else (args.max_broad if args.section in ("broad", "all") else 0)
    log(f"[info] 今日の生成済み {done_today} 本 / 上限 {args.daily_cap or 'なし'}")

    recent = dedupe.load_recent(DEDUPE_DAYS)
    log(f"[info] 重複チェック: 直近{DEDUPE_DAYS}日の記事 {len(recent.entries)} 件")
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
    if not ai_limit:
        ai_items = []
    if not broad_limit:
        broad_items = []
    lanes = build_lanes(
        [i for i in ai_items if i["id"] not in major_ids], [i for i in broad_items if i["id"] not in major_ids], args.section
    )
    # レーン内は 公式・官公庁 → 他社の対応告知 → 配信 の順（同じ順位の中は collect() の順を保つ）
    for lane in lanes:
        lanes[lane].sort(key=rank_bucket)
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
    wire_attempts = [0]
    used_queue_ids: set[str] = set()
    run_started = time.time()

    def total_created() -> int:
        return sum(created.values())

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
        return "bulk" if fast_mode and llm.all_exhausted("quality") else "quality"

    def generate(system: str, prompt: str, schema: dict, item_id: str, tier: str):
        try:
            return llm.generate_json(system, prompt, schema, temperature=0.6, max_output_tokens=32768, tier=tier)
        except Exception as error:  # noqa: BLE001
            log(f"   [NG] {str(error)[:200]}")
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

    def gated(item: dict, kind: str, system: str, prompt: str, schema: dict, source_text: str, build, context: str = "",
              tier: str | None = None, attempts: int = 2):
        """生成 → 品質ゲート →（不合格なら問題点を添えて作り直し。通常は計2回、大きなニュースは計3回）。
        戻り値: ("ok", record, model, verdict) / ("skip", None, model, reason) / ("reject", ...) / ("fail", None, None, None)"""
        tier = tier or args.tier
        verdict: dict = {}
        model_name = None
        for attempt in range(1, attempts + 1):
            data, model_name = generate(system, prompt, schema, item["id"], tier)
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
                verdict = quality.evaluate(record, source_text, kind, context, check_facts=not args.no_gate)
                tag = "合格" if verdict["ok"] else ("読みやすさのみ基準外" if verdict.get("soft") else "不合格")
                r = verdict.get("readability") or {}
                log(f"   [gate] {tag} score={verdict.get('score')} 試行{attempt}/{attempts}"
                    f" 文{r.get('avg_sentence')}字/60字超{r.get('long_share', 0):.0%}/段落{r.get('avg_paragraph')}字(最長{r.get('max_paragraph')})"
                    f" {('/ ' + ' | '.join(verdict['problems'][:3])[:300]) if verdict['problems'] else ''}")
                log_quality(state, item, kind, verdict, attempt, verdict["ok"], model_name or "")
                if not verdict["ok"] and not verdict.get("soft") and quality.MAJOR and quality.surgical_ok(verdict):
                    # 大きなニュース: 致命的でない細かい指摘（言い過ぎ・訳語・補足の程度）は、作り直さず指摘された文だけ直す
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
                    record["qualityScore"] = verdict.get("score")
                    return "ok", record, model_name, verdict
            if attempt < attempts and not verdict.get("unavailable") and time_left() > EST_SEC_PER_ARTICLE:
                prompt = prompt + quality.feedback_block(verdict.get("problems") or [])
                log(f"   [gate] 問題点を伝えて作り直します（{tier}）")
                continue
            break
        reason = "; ".join(verdict.get("problems") or [])[:200]
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
            if is_wire(cand) and wire_attempts[0] >= args.max_wire:
                return "next", None  # 配信は1回の上限まで。次の実行に回す
        if recent.has_url(cand["url"]):
            mark(state, cand, skipped=True, reason="dedupe: 同じ出典URL")
            return "drop", None
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
            log(f"   [warn] 本文の取得に失敗（{str(error)[:80]}）。フィードの本文を使います")
            page = {"text": "", "image": None}
        text = max([page.get("text") or "", item.get("content") or ""], key=len)
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
        context = archive.prompt_block(related)
        secondary_note = f"\n\n{SECONDARY_SOURCE_NOTE.format(name=item['source_name'])}" if item.get("secondary") else ""
        if related:
            log(f"   [context] 過去記事 {len(related)} 本: {', '.join(r['slug'] for r in related)}")
        voice_key, voice_text = m.pick_voice()
        if is_ai:
            genre = AI
            system = f"{m.SYSTEM_INSTRUCTION_BASE}\n\n{AI.persona}\n\n{WEB_SOURCE_NOTE}{secondary_note}\n\n## 今回の語り口\n{voice_text}"
            prompt, schema, kind = build_prompt(item, text, context), WEB_SCHEMA, "ai"
        else:
            genre = GENRES_BY_SLUG[lane]
            system = (
                f"{m.SYSTEM_INSTRUCTION_BASE}\n\n{genre.persona}\n\n{WEB_SOURCE_NOTE}{secondary_note}\n\n{BROAD_SOURCE_NOTE}"
                f"\n\n## 今回の語り口\n{voice_text}"
            )
            prompt, schema, kind = build_broad_prompt(item, text, genre, context), TREND_SCHEMA, "broad"

        def build(data, model, item=item, page=page, voice_key=voice_key, genre=genre, related=related):
            if str(data.get("genre", "")).strip() not in GENRES_BY_SLUG:
                data = {**data, "genre": genre.slug}
            return attach_related(build_web_record(item, data, page, model, voice_key), data, related)

        started = time.time()
        if major:
            log(f"   [major] tier={major_tier()} / 最大{MAJOR_ATTEMPTS}回試行（これまでの失敗 {state['major_tries'].get(item['id'], 0)} 回）")
        # 事実確認には発信元・公開日時も渡す（記事に書いた「〇〇によると」「10月26日に発表」を創作と誤判定させない）。
        # 大きなニュースは長さの基準を緩め、細かい指摘は外科的に直して公開する（quality.MAJOR）
        quality.MAJOR = bool(major)
        try:
            status, record, model_name, info = gated(
                item, kind, system, prompt, schema, f"{source_header(item)}\n\n{text}", build, archive.check_block(related),
                tier=major_tier() if major else None, attempts=MAJOR_ATTEMPTS if major else 2,
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
            if status in ("skip", "reject"):
                mark(state, item, skipped=True, reason=f"{'quality: ' if status == 'reject' else ''}{info}"[:200], model=model_name)
            return
        # 速報（scripts/breaking_post.py がデプロイ直後に SNS へ出す）。
        # 速報レーンの記事は Worker が主要企業の公式発表と判定したものなので、必ず速報にする（公開したらすぐ SNS へ）
        # 通常の収集から来たものは、発表から12時間以内のときだけ速報にする（古い発表に【速報】を付けない）
        fresh = item.get("published_at") and item["published_at"] > datetime.now(timezone.utc) - timedelta(hours=12)
        if major and ((is_breaking(item) and fresh) or item.get("fast")):
            record["breaking"] = True
        stamp_times(record, item, run_detected_at)
        path = m.write_article(record)
        recent.add(record["title"], record["sourceUrl"], record.get("keywords"))
        touched.add(record["slug"])
        run_status[item["id"]] = ("article", record["slug"])
        mark(state, item, slug=record["slug"], model=model_name, section=lane, score=record.get("qualityScore"),
             **({"major": True} if major else {}), **({"breaking": True} if record.get("breaking") else {}))
        state["major_tries"].pop(item["id"], None)
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
            if llm.all_exhausted("quality") and not (fast_mode and not llm.all_exhausted("bulk")):
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
            context = archive.prompt_block(related)
            voice_key, voice_text = m.pick_voice()
            system = (
                f"{m.SYSTEM_INSTRUCTION_BASE}\n\n{genre.persona}\n\n"
                f"{TREND_SOURCE_NOTE.format(rules=trends.EDITORIAL_RULES)}\n\n## 今回の語り口\n{voice_text}"
            )
            if related:
                log(f"   [context] 過去記事 {len(related)} 本: {', '.join(r['slug'] for r in related)}")
            started = time.time()
            status, record, model_name, info = gated(
                q, "trend", system, build_trend_prompt(q, fetched, genre, context), TREND_SCHEMA, source_text,
                lambda data, model, q=q, fetched=fetched, voice_key=voice_key, related=related: attach_related(
                    build_trend_record(q, fetched, data, model, voice_key), data, related
                ),
                archive.check_block(related),
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
            stamp_times(record, q, run_detected_at)
            path = m.write_article(record)
            recent.add(record["title"], record["sourceUrl"], record.get("keywords"))
            touched.add(record["slug"])
            run_status[q["id"]] = ("article", record["slug"])
            mark(state, q, slug=record["slug"], model=model_name, kind="trend", score=record.get("qualityScore"))
            created["trend"] += 1
            log(f"   [OK] {path.relative_to(m.ROOT)} ({len(record['body'])}字 / {model_name} / {time.time() - started:.0f}s)")

        # 公式フィード（AI）と幅広い話題を、レーンごとに1本ずつ交互に処理する
        order = lane_order(lanes)
        pointers = {lane: 0 for lane in order}
        active = list(order)
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
                items = lanes[lane]
                # このレーンで次に試せる候補を探す（足切り・重複・フィード上限は飛ばす）
                item = None
                target = None
                while pointers[lane] < len(items):
                    cand = items[pointers[lane]]
                    pointers[lane] += 1
                    action, target = pick(cand, is_ai)
                    if action == "take":
                        item = cand
                        break
                if item is None:
                    active.remove(lane)
                    continue
                process_web(item, lane, target)
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
