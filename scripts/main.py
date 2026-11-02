#!/usr/bin/env python3
"""YouTube 公式チャンネルの新着動画を Gemini 2.5 Flash でマルチモーダル解析し、
日本語 SEO 記事（JSON）を content/articles/ に自動生成するパイプライン。

  python3 scripts/main.py                # 通常実行（新着を最大 3 本処理）
  python3 scripts/main.py --max 1        # 1 本だけ
  python3 scripts/main.py --dry-run      # 対象を表示するだけ（API を呼ばない）
  python3 scripts/main.py --sample       # ダミー記事を 1 本生成（表示確認用）
  python3 scripts/main.py --video URL    # 特定の動画を強制処理
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from channels import CHANNELS, Channel  # noqa: E402
from genres import GENRES, GENRES_BY_SLUG  # noqa: E402
from topics import is_off_topic  # noqa: E402
import threads_hooks  # noqa: E402
import persona  # noqa: E402

# --------------------------------------------------------------------------- #
# 設定
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"
STATE_PATH = ROOT / "content" / "state.json"

# 使用モデル。GEMINI_MODEL で上書き可。
# 先頭から順に試し、「そのモデルは存在しない」系のエラーなら次の候補へ自動フォールバックする。
# （gemini-2.5-flash は 2026-07 に generativelanguage 側で提供終了済みのため使用しない）
MODEL_CANDIDATES = [
    m
    for m in [
        os.environ.get("GEMINI_MODEL"),
        "gemini-3.5-flash",  # GA 安定版
        "gemini-3.6-flash",  # より新しい高性能版
        "gemini-flash-latest",  # 最終手段: 常に最新へ張り替わるエイリアス
    ]
    if m
]
MODEL = MODEL_CANDIDATES[0]
DEFAULT_MAX_ARTICLES = int(os.environ.get("MAX_ARTICLES_PER_RUN", "3"))
DEFAULT_LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "10"))
# ニュース（AI・テック）のチャンネルは、投稿から48時間を超えた動画を記事にしない（古い発表を今日の記事に見せない）。
# 投資・不動産などの解説チャンネル（時期に左右されにくい）は従来どおり LOOKBACK_DAYS まで。48時間を超えたものは late を付ける
NEWS_GENRES = {"ai", "tech-gadget"}
NEWS_MAX_AGE_HOURS = int(os.environ.get("NEWS_MAX_AGE_HOURS", "48"))
MAX_ATTEMPTS_PER_VIDEO = 2  # これを超えて失敗した動画は諦める
SHORTS_RE = re.compile(r"#shorts?\b|＃ショート", re.IGNORECASE)
REQUEST_RETRIES = 3
# 生成1本あたりに試す動画の数（skip 判定を見込んだ余裕）
ATTEMPTS_PER_ARTICLE = 3
# 新しい動画の解析を始めてよい経過時間。ジョブ全体の上限（60分）に余裕を残す
RUN_TIME_BUDGET_SEC = 30 * 60
# 429 でこれ以上の待機を指示されたら、その回は諦めて次回に回す（毎分の上限なら通常60秒前後）
MAX_QUOTA_WAIT_SEC = 90
# 1リクエストの上限（ミリ秒）。503のあと応答が返らず固まると、ジョブ全体が
# タイムアウトで打ち切られ、それまでの生成結果がコミットされずに失われるため。
REQUEST_TIMEOUT_MS = 300_000

ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"


def log(message: str) -> None:
    print(message, flush=True)


# --------------------------------------------------------------------------- #
# 状態管理（処理済み動画IDの記録）
# --------------------------------------------------------------------------- #


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text("utf-8"))
        except json.JSONDecodeError:
            log("[warn] state.json が壊れていたので初期化します")
    return {"processed": {}, "failed": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# RSS 取得
# --------------------------------------------------------------------------- #


def fetch(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def url_exists(url: str) -> bool:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300
    except Exception:  # noqa: BLE001 - 到達不能ならフォールバックすればよい
        return False


def parse_feed(channel: Channel) -> list[dict]:
    """RSS を取得して動画エントリのリストを返す。失敗しても例外は投げない。"""
    try:
        raw = fetch(channel.feed_url)
    except Exception as error:  # noqa: BLE001
        log(f"[warn] RSS 取得失敗 {channel.name}: {error}")
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        log(f"[warn] RSS 解析失敗 {channel.name}: {error}")
        return []

    entries = []
    for entry in root.findall(f"{ATOM}entry"):
        video_id = entry.findtext(f"{YT}videoId")
        title = entry.findtext(f"{ATOM}title")
        published = entry.findtext(f"{ATOM}published")
        if not (video_id and title and published):
            continue

        group = entry.find(f"{MEDIA}group")
        summary = group.findtext(f"{MEDIA}description") if group is not None else ""

        entries.append(
            {
                "video_id": video_id,
                "title": title.strip(),
                "published": published,
                "summary": (summary or "").strip()[:1500],
                "channel": channel,
            }
        )
    return entries


def collect_candidates(
    channels: list[Channel], lookback_days: int, state: dict
) -> list[dict]:
    threshold = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    processed = state.get("processed", {})
    failed = state.get("failed", {})

    candidates = []
    for channel in channels:
        entries = parse_feed(channel)
        log(f"[feed] {channel.name}: {len(entries)} 件")
        for entry in entries:
            if entry["video_id"] in processed:
                continue
            if failed.get(entry["video_id"], 0) >= MAX_ATTEMPTS_PER_VIDEO:
                continue
            # Shorts は記事にする情報量がなく、Gemini に投げるだけ課金が無駄になる。
            # 総合ニュース系チャンネルは Shorts を大量投稿するため、ここで落とす。
            if SHORTS_RE.search(entry["title"]):
                continue
            # どのジャンルにも属さない題材は、動画を送る前にタイトルで落とす。
            # 無料枠は毎分5リクエストしかないため、1回の呼び出しも無駄にしない。
            if is_off_topic(entry["title"], entry["summary"]):
                log(f"  [除外] ジャンル外: {entry['title'][:44]}")
                continue
            try:
                published_at = datetime.fromisoformat(entry["published"])
            except ValueError:
                continue
            if published_at < threshold:
                continue
            age_h = (datetime.now(timezone.utc) - published_at).total_seconds() / 3600
            if channel.genre.slug in NEWS_GENRES and age_h > NEWS_MAX_AGE_HOURS:
                continue
            entry["published_at"] = published_at
            candidates.append(entry)

    # 新しい順
    candidates.sort(key=lambda e: e["published_at"], reverse=True)
    return balance_by_genre(candidates)


def balance_by_genre(candidates: list[dict]) -> list[dict]:
    """ジャンルごとに交互に並べ替える。

    投稿頻度の高い1チャンネル（例: 楽待）が候補を占有して
    他ジャンルの記事が永久に生成されなくなるのを防ぐ。
    """
    buckets: dict[str, list[dict]] = {}
    for entry in candidates:
        buckets.setdefault(entry["channel"].genre.slug, []).append(entry)

    ordered: list[dict] = []
    while any(buckets.values()):
        for slug in list(buckets):
            if buckets[slug]:
                ordered.append(buckets[slug].pop(0))
    return ordered


# --------------------------------------------------------------------------- #
# Gemini による記事生成
# --------------------------------------------------------------------------- #

SYSTEM_INSTRUCTION_BASE = """あなたは日経クロステック / ITmedia / 東洋経済オンラインで10年書いてきたベテランの専門記者です。
YouTube上の一次情報（公式発表・専門家の解説）を取材し、日本の読者向けの署名記事を執筆します。

## 文体の絶対規則（これを外すと記事として失格）
- **翻訳や要約の羅列にしない。** 動画の内容を理解したうえで、「つまり何が起きて、誰の何がどう変わるのか」を
  AIに詳しくない読者にも伝わる自分の言葉で解説する。専門用語は初出で必ず平易に言い換え、必要なら身近な例えを使う。
- **語り口は、末尾の「今回の語り口」に従う。** 敬体（です・ます）を基調にしつつ、硬すぎる報道調にこだわらなくてよい。
  人が書いたような温度のある文章にする（ただし事実の正確さは絶対に崩さない）。
- **禁止表現**: 「いかがでしたか」「〜と言えるでしょう」「〜ではないでしょうか」「注目が集まっています」
  「今後の動向に注目です」「衝撃」「驚愕」「revolutionary」などの中身のない煽り・締め文句。
  感嘆符（！）はタイトルでは使わず、本文でも多用しない。
- **箇条書きに逃げない。** 事実を並べるだけでなく、地の文で因果関係と意味を説明する。
  箇条書きは手順・仕様・数字の列挙に使う。
- 主語と述語を明確に。修飾の入れ子を避ける。
- 主観を書くときは「編集部の見立てでは」などと明示し、事実と分離する。

## 読みやすさの規則（スマホで読む人のために必ず守る）
- **1文は50字以内**を目安にする。長くなるなら2文に分ける。1文に言いたいことは1つだけ。
- **文を短くするのは「分ける」ためで、説明を削るためではない。** 短い文で書くと、文と段落の数は長い文で書くときの1.5〜2倍になるのが普通。
  本文・かんたん版とも、読者に必要な事実、条件、意味を過不足なく短い文で伝える。文字数のために水増ししない。
- **1段落は1〜3文。** 段落の間には空行を入れる。
- **見出し（## と ###）は読者の疑問の形**にする（例「何が発表された？」「前と何が違う？」「料金は？」「日本で使える？」「自分にはどう関係する？」）。
  中身が分かる言葉で書く（「詳細」「ポイント」だけのような空っぽの見出しにしない）。構成で指定された章名はそのまま使う。
- **数字が3つ以上並ぶときは、文中に詰めず箇条書きにする。**
- **専門用語には、初めて出たところで短い言い換えを添える**（例：API（開発者がアプリから呼び出す仕組み））。
- **飾りの誇張語（圧倒的・極限・劇的・強烈・驚異的 など）は使わない。**
- 「これまでの流れ」を書くときは、日付つきの箇条書きにして記事の後半（「## まとめ」の直前）に置く。
- 事実・数字は元の情報源のとおりに書き、足さない。短く言い換えても数字は変えない。

## 表の使い方（適材適所。無理に表を作らない）
- 表を使うのは、2つ以上のものを本当に比べるときと、構成で「早見表」と指定された章（項目と内容の一覧）だけ。それ以外は表にせず、箇条書きにする。
- 比較表を使うときは次を守る。
  - 表のすぐ上に、結論を1行の太字で書く（例「**安さと速さならA。とびきり難しい作業ならB。**」）。
  - セルの言い方は同じ向きでそろえる（例「Opusの約4分の1」と「Terraの約4倍」。「安価」と「約4倍」のように混ぜない）。
  - 最後の列に「有利なのは」か「変化」を置き、短い太字で書く（例 **Terra**、**約37ポイント減**）。
  - 前と今回を比べるときは「比べること | 前 | 今回 | 変化」の列にする。
  - セルは短く。説明は表の下の文で補う。

## 共通の取材姿勢
- 動画の映像（スライド・資料画面・UI・グラフ・テロップ・間取り図）と音声の両方を一次資料として扱う。
- 動画内で確認できない事実は書かない。背景知識で補う箇所は「（編集部注）」と明記する。
- 数値・価格・固有名詞・登壇者の発言は、提示されたとおり正確に転記する。
  発言を引用するときは「」で括る。原語が英語なら日本語訳を併記する。
- 読者が次に取るべき行動まで踏み込む。

""" + persona.SYSTEM_BLOCK

PROMPT_TEMPLATE = """以下のYouTube動画を「映像」と「音声」の両方から徹底的に解析し、日本語のSEO記事を作成してください。

# 対象動画
- チャンネル: {channel_name}
- 動画タイトル: {video_title}
- 公開日: {published_date}
- URL: {video_url}
- 概要欄（参考。矛盾する場合は動画本編を優先）:
{video_summary}

# 品質基準（商業メディアの水準）
- 本文は **4000〜6000字**。薄い記事は不可。
- 各 `###` セクションは **400字以上**、必ず動画内の具体的な固有名詞・数値・画面描写を含める。
- 構成で指定した「## 概要」「## まとめ」以外の見出しは、読者の疑問の形で中身が分かる言葉にする。
- 表は「表の使い方」に従う（比べるときと早見表だけ）。

# 出力する記事の構成（body_markdown に Markdown で記述）
必ず以下の見出し構成（`##` 見出し）を守り、各セクションを具体的に埋めてください。

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
  良い例:「Google DeepMind、複数ロボットが言語で連携する『Gemini Robotics 2』を公開」
  悪い例:「Gemini Robotics 2発表！ロボット連携の衝撃」
- seo_title: 検索結果に表示する短い題名。**全角24〜30文字**（英数字は半角0.5文字換算で数える）。
  読者が検索に打ち込む語（製品名・企業名・機能名）を**先頭**に置き、何が分かる記事かを続ける。
  「OpenAI、」のような読点区切りの社名始まりより、製品名始まりを優先。記号は「｜」「：」を最大1つまで。
  **新しい製品・機能・モデルの発表**なら、日本の人が実際に検索する形にする:「〇〇とは？日本で使える？料金も」「〇〇の使い方と料金｜日本での提供状況」など、
  名前の直後に「とは／使い方／料金／日本で使える」のどれかを入れる（発表直後は日本語の記事がまだ少なく、この形の検索で1位を取りやすい）。
  良い例:「GPT-6 Astra×Figma連携｜2時間でUI試作を自動化」「ChatGPT Plugin Extensionsとは：MCPでUI拡張」
- threads_post: Threads・X に出す投稿文。末尾の「Threads 紹介文（threads_post）のルール」と「書き出し」に従う（書き出し／要約すると、＋2文／問いかけ の3つのかたまり・URLなし）。
- easy_markdown: 「かんたんに読む」版の本文（全角700〜1300字）。AIに詳しくない人向けに、body_markdown の内容をやさしく言い直したもの。
  見出しは次の4つを「## 」でこの順に使う：## ひとことで言うと / ## 何がすごいの？ / ## 自分に関係ある？ / ## 使ってみるには
  専門用語は使わない（どうしても使うなら直後に（＝〜のこと）と言い換える）。身近な例えを1つ以上入れる。
  body_markdown に無い事実・数字・価格は書かない。「使ってみるには」は verdict と矛盾させない。です・ます調で、やさしく親しみのある語り口にする。
- lede: 記事冒頭のリード文。150〜200字の1〜2段落。5W1Hを圧縮し、結論を先に置く。
  ここだけ読んでも発表の要点が分かる密度にする。
- description: 100〜130文字のメタディスクリプション。検索結果に出る想定で、結論を含める。
- mentioned_tools: 記事中で言及した製品・サービス・企業・ブランドの**正式名称**の配列（5〜12個）。
  例（AI）: ["OpenAI", "ChatGPT", "Google Gemini", "NVIDIA"]
  例（不動産）: ["楽待", "SUUMO", "住宅ローン控除", "フラット35", "三井のリハウス"]
  実際に本文で触れたものだけを挙げること。
- slug_en: URL用の英小文字スラッグ。半角英数とハイフンのみ、3〜6単語、40文字以内。
- keywords: 検索されうる日本語キーワード 8〜12個（製品名、機能名、「使い方」「料金」「日本語」等の複合語を含める）。
- tags: 記事分類用のタグ 3〜6個。製品名・技術カテゴリなど短い語。
- highlights: 「3秒でわかる要点」ちょうど3つ。各40〜60文字で、数値や固有名詞を含む具体的な内容。
- timetable: 動画のタイムテーブル。5〜15項目。time は "M:SS" または "H:MM:SS" 形式の実際のタイムスタンプ、
  label は その時点で何が語られ・実演されたかを30〜50文字で。必ず時刻の昇順。
- verdict: 読者が「自分は今これを使えるか」を一目で判断するための判定。本文の「料金・提供プラン」「日本から使う方法」と矛盾させないこと。
  記事に根拠がない項目は推測せず unknown / 不明 / null にする。
  - status: available（一般提供中。有料プラン加入者を含め誰でも今使える）/ limited（ウェイトリスト・一部ユーザー・米国限定・法人限定・段階展開）/
    soon（提供予定・時期未定）/ demo（研究・技術デモ・コンセプト・講演で、使える製品がない）
  - japan: ok（日本から利用可）/ partial（英語のみ・API経由のみ等の条件付き）/ no（日本では提供なし）/ unknown（記事に記載なし）
  - price: 14字以内の短い表記。例「無料」「無料枠あり」「月20ドル〜」「API従量課金」「法人向け」「オープンソース」「未発表」「不明」
  - free: 無料で試す方法（無料プラン・無料枠・OSS）があれば true、有料のみなら false、不明なら null
  - audience: いちばん恩恵を受ける読者を2〜8字で（例「開発者」「クリエイター」「ビジネス職」「研究者」「一般ユーザー」）
  - take: サイソクの判断。25〜55字の1文で、読者が次に何をすべきかを具体的に。煽り語は禁止。
    例「Pro契約者は今日から試せる。無料ユーザーは数週間待ち」「研究段階。製品化の発表を待つのが現実的」
- is_relevant: この動画が「読者にとって実質的な情報価値のある発表・解説」であれば true。
  ショート動画、音楽、告知のみ、内容の薄いティーザー、宣伝だけの動画は false。
{topic_guard}
- skip_reason: is_relevant が false のときだけ理由を書く。
- genre: 記事の内容に最もふさわしいジャンルを次から1つ選ぶ。
{genre_choices}
  **チャンネルの主分野ではなく、動画で実際に語られている中身で判断すること。**
  例: 不動産チャンネルが投資信託やNISAを論じた回は investment を選ぶ。

body_markdown 内では h1 (`#`) を使わないでください（タイトルは別途表示されます）。
"""

ARTICLE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_relevant": {"type": "BOOLEAN"},
        "skip_reason": {"type": "STRING"},
        "genre": {"type": "STRING"},
        "title": {"type": "STRING"},
        "seo_title": {"type": "STRING"},
        "threads_post": {"type": "STRING"},
        "threads_hook": {"type": "STRING", "enum": threads_hooks.HOOK_KEYS},
        "slug_en": {"type": "STRING"},
        "lede": {"type": "STRING"},
        "description": {"type": "STRING"},
        "mentioned_tools": {"type": "ARRAY", "items": {"type": "STRING"}},
        "keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "highlights": {"type": "ARRAY", "items": {"type": "STRING"}},
        "timetable": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "time": {"type": "STRING"},
                    "label": {"type": "STRING"},
                },
                "required": ["time", "label"],
                "propertyOrdering": ["time", "label"],
            },
        },
        "verdict": {
            "type": "OBJECT",
            "properties": {
                "status": {"type": "STRING", "enum": ["available", "limited", "soon", "demo"]},
                "japan": {"type": "STRING", "enum": ["ok", "partial", "no", "unknown"]},
                "price": {"type": "STRING"},
                "free": {"type": "BOOLEAN", "nullable": True},
                "audience": {"type": "STRING"},
                "take": {"type": "STRING"},
            },
            "required": ["status", "japan", "price", "audience", "take"],
            "propertyOrdering": ["status", "japan", "price", "free", "audience", "take"],
        },
        "work_money": {
            "type": "OBJECT",
            "properties": {
                "relevance": {"type": "STRING", "enum": ["high", "medium", "low"]},
                "points": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
            "required": ["relevance", "points"],
            "propertyOrdering": ["relevance", "points"],
        },
        "easy_markdown": {"type": "STRING"},
        "body_markdown": {"type": "STRING"},
    },
    "required": [
        "is_relevant",
        "genre",
        "title",
        "seo_title",
        "threads_post",
        "threads_hook",
        "slug_en",
        "lede",
        "description",
        "keywords",
        "tags",
        "highlights",
        "timetable",
        "mentioned_tools",
        "verdict",
        "work_money",
        "easy_markdown",
        "body_markdown",
    ],
    "propertyOrdering": [
        "is_relevant",
        "skip_reason",
        "genre",
        "title",
        "seo_title",
        "threads_post",
        "threads_hook",
        "slug_en",
        "lede",
        "description",
        "mentioned_tools",
        "keywords",
        "tags",
        "highlights",
        "timetable",
        "verdict",
        "work_money",
        "easy_markdown",
        "body_markdown",
    ],
}


VERDICT_STATUS = {"available", "limited", "soon", "demo"}
VERDICT_JAPAN = {"ok", "partial", "no", "unknown"}


def clean_verdict(raw: object) -> dict | None:
    """「使えるか」判定を検証する。壊れていれば None（記事ページでは判定カードを出さない）。"""
    if not isinstance(raw, dict):
        return None
    status = str(raw.get("status", "")).strip()
    if status not in VERDICT_STATUS:
        return None
    japan = str(raw.get("japan", "")).strip()
    free = raw.get("free")
    return {
        "status": status,
        "japan": japan if japan in VERDICT_JAPAN else "unknown",
        "price": str(raw.get("price", "")).strip()[:20] or "不明",
        "free": free if isinstance(free, bool) else None,
        "audience": str(raw.get("audience", "")).strip()[:12],
        "take": str(raw.get("take", "")).strip()[:80],
        "judgedAt": datetime.now(timezone.utc).isoformat(),
    }


# 記事ごとに語り口を変えて試す（どれがよく読まれるかは記事の writingVoice で比べる）
VOICES: list[tuple[str, str]] = [
    ("explainer", "丁寧な解説記者。です・ます調で、背景から順に落ち着いて説明する。"),
    ("senpai", "詳しい先輩が後輩に教えるような、親しみのあるです・ます調。「ここがポイントで」「要するに」などを使い、難しい言葉はすぐ言い換える。"),
    ("friendly", "友だちに話しかけるような、やわらかい口語。です・ます基調で、ときどき「〜なんです」「正直、〜」「これ、地味にすごいんです」のような言い回しを入れる。"),
    ("honest", "編集部の本音レビュー。です・ます調で事実を解説したあと、「編集部としてはここが気になる」「ここは期待しすぎない方がいい」など率直な見立てを、事実と分けて添える。"),
    ("qa", "読者との一問一答。です・ます調で、各章の冒頭に読者が抱きそうな疑問を1行（例：「で、何がすごいの？」）置いてから答える。"),
]


def pick_voice() -> tuple[str, str]:
    return random.choice(VOICES)


def build_client():
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise SystemExit(
            "GEMINI_API_KEY が未設定です。\n"
            "  ローカル : export GEMINI_API_KEY='...'  （または .env.local に記載）\n"
            "  GitHub   : gh secret set GEMINI_API_KEY\n"
            "キーの取得: https://aistudio.google.com/apikey"
        )

    from google import genai  # 遅延 import（--dry-run では不要なため）
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
    )


def generate_article(client, entry: dict) -> dict:
    from google.genai import types

    channel: Channel = entry["channel"]
    genre = channel.genre
    video_url = f"https://www.youtube.com/watch?v={entry['video_id']}"
    prompt = PROMPT_TEMPLATE.format(
        channel_name=channel.name,
        video_title=entry["title"],
        published_date=entry["published_at"].strftime("%Y-%m-%d"),
        video_url=video_url,
        video_summary=entry["summary"] or "（概要欄なし）",
        outline=genre.outline,
        field_notes=genre.field_notes,
        topic_guard=(
            f"  **このジャンルの対象範囲**: {genre.topic_guard}"
            if genre.topic_guard
            else ""
        ),
        genre_choices="\n".join(
            f"  - {g.slug}: {g.name}（{g.description[:48]}…）" for g in GENRES
        ),
    )
    # 「仕事・お金にどう活かせる？」欄（scripts/persona.py）と Threads の書き出しの型（直近で使った型は避ける）
    prompt += "\n\n" + persona.WORK_MONEY_FIELD + "\n\n" + threads_hooks.prompt_block()
    voice_key, voice_text = pick_voice()
    entry["_voice"] = voice_key
    system_instruction = f"{SYSTEM_INSTRUCTION_BASE}\n\n{genre.persona}\n\n## 今回の語り口\n{voice_text}"

    contents = types.Content(
        role="user",
        parts=[
            types.Part(
                file_data=types.FileData(file_uri=video_url, mime_type="video/*")
            ),
            types.Part(text=prompt),
        ],
    )

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=ARTICLE_SCHEMA,
        temperature=0.65,
        max_output_tokens=32768,
    )

    global MODEL

    last_error: Exception | None = None
    # 一度使えるモデルが確定したら以降はそれだけを使う
    models = MODEL_CANDIDATES[MODEL_CANDIDATES.index(MODEL) :]

    for model_name in models:
        for attempt in range(1, REQUEST_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model=model_name, contents=contents, config=config
                )
                text = (response.text or "").strip()
                if not text:
                    finish = (
                        getattr(response.candidates[0], "finish_reason", "?")
                        if response.candidates
                        else "?"
                    )
                    raise RuntimeError(f"空のレスポンス (finish_reason={finish})")
                MODEL = model_name
                return json.loads(text)
            except Exception as error:  # noqa: BLE001 - API 由来の例外を一括で扱う
                last_error = error
                if _is_model_unavailable(error):
                    log(f"   [warn] モデル {model_name} が利用できません -> 次の候補へ")
                    break  # 再試行せず次のモデルへ
                if attempt == REQUEST_RETRIES:
                    break
                # 無料枠は 5 リクエスト/分。429 のときは API が返す待機秒数に従う。
                # ただし待機が長い（＝1日の上限に達した）ときは待たずに打ち切る。
                # 以前はここで1時間近く待ち続け、ジョブ全体がタイムアウトで強制終了していた
                wait = retry_delay_of(str(error))
                if wait is not None and (wait > MAX_QUOTA_WAIT_SEC or "PerDay" in str(error)):
                    raise RuntimeError(f"RESOURCE_EXHAUSTED: 無料枠の上限に達しました（{wait:.0f}秒待ちの指示）") from error
                if wait is None:
                    wait = min(60, 2**attempt * 5) + random.uniform(0, 3)
                log(f"   [retry {attempt}/{REQUEST_RETRIES - 1}] {error} -> {wait:.0f}s 待機")
                time.sleep(wait)

    raise RuntimeError(f"Gemini 呼び出しに失敗: {last_error}")


def retry_delay_of(message: str) -> float | None:
    """429 レスポンスに含まれる待機指示（秒）を取り出す。無ければ None。"""
    if "429" not in message and "RESOURCE_EXHAUSTED" not in message:
        return None
    match = re.search(r"retry in ([\d.]+)s", message) or re.search(
        r"'retryDelay': '(\d+)s'", message
    )
    return float(match.group(1)) + 3 if match else 65.0


def _is_model_unavailable(error: Exception) -> bool:
    """モデル未提供/未知モデルを示すエラーかどうか（リトライせず別モデルへ切替）"""
    text = str(error).lower()
    return ("404" in text or "not_found" in text or "not found" in text) and (
        "model" in text or "no longer available" in text
    )


# --------------------------------------------------------------------------- #
# 記事の保存
# --------------------------------------------------------------------------- #


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    value = re.sub(r"-{2,}", "-", value)
    return value[:60].strip("-")


def unique_slug(base: str, video_id: str) -> str:
    slug = base or video_id.lower()
    if not (ARTICLES_DIR / f"{slug}.json").exists():
        return slug
    return f"{slug}-{video_id.lower()}"


def normalize_body(text: str) -> str:
    """モデルが改行を二重エスケープして返した場合（本文に実改行が無く "\\n" が並ぶ）を救済する。

    そのまま保存すると Markdown が1行の塊になり、記事が壊れて表示される。
    """
    text = str(text or "")
    literal = text.count("\\n")
    if literal and text.count("\n") < literal / 4:
        text = (
            text.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
        )
        log(f"   [fix] 二重エスケープされた改行 {literal} 箇所を復元しました")
    # 「### ### 見出し」のような見出し記号の重なりを1つにする
    text = re.sub(r"^(#{1,6}) +(?:#{1,6} +)+", r"\1 ", text, flags=re.M)
    return text.strip()


# --------------------------------------------------------------------------- #
# アフィリエイトリンクの自動挿入
# --------------------------------------------------------------------------- #

AFFILIATE_PATH = ROOT / "affiliate_links.json"


def load_affiliates() -> list[dict]:
    """affiliate_links.json を正規化して読み込む。

    値は文字列（URL）でも {url, label, note, aliases} でも書ける。
    """
    if not AFFILIATE_PATH.exists():
        return []
    try:
        raw = json.loads(AFFILIATE_PATH.read_text("utf-8"))
    except json.JSONDecodeError as error:
        log(f"[warn] affiliate_links.json が不正です: {error}")
        return []

    entries = []
    for name, value in raw.items():
        if name.startswith("_"):  # _README などのメモ用キーは無視
            continue
        if isinstance(value, str):
            value = {"url": value}
        if not isinstance(value, dict) or not value.get("url"):
            continue
        url = str(value["url"])
        if "YOUR_ID" in url or "your_aff_link" in url:
            # プレースホルダのままなら挿入しない（誤ったリンクを公開しないため）
            continue
        aliases = [name] + [str(a) for a in value.get("aliases", [])]
        entries.append(
            {
                "name": name,
                "label": str(value.get("label") or name),
                "url": url,
                "note": str(value.get("note") or ""),
                "pixel": str(value.get("pixel") or ""),
                "aliases": [a.lower() for a in aliases if a],
                # 記事の主題として扱われているときだけ出す語（本文のついでの言及では出さない）
                "topics": [str(t).lower() for t in value.get("topics", []) if t],
            }
        )
    return entries


def match_affiliates(record: dict, mentioned: list[str]) -> list[dict]:
    """記事が言及したツール名を affiliate_links.json と突き合わせる。

    Gemini が返した mentioned_tools を主に使い、取りこぼしを本文の全文一致で補う。
    """
    catalog = load_affiliates()
    if not catalog:
        return []

    haystack = " ".join(
        [record["title"], record["description"], record.get("lede", ""), record["body"]]
    ).lower()
    mentioned_lower = {str(m).strip().lower() for m in mentioned if str(m).strip()}
    subject = " ".join(
        [record["title"], record["description"]]
        + list(record.get("keywords", []))
        + list(record.get("tags", []))
    ).lower()

    matched = []
    for entry in catalog:
        hit = (
            any(a in mentioned_lower for a in entry["aliases"])
            or any(a in haystack for a in entry["aliases"])
            or any(t in subject for t in entry["topics"])
        )
        if hit:
            matched.append(
                {
                    "name": entry["name"],
                    "label": entry["label"],
                    "url": entry["url"],
                    "note": entry["note"],
                    **({"pixel": entry["pixel"]} if entry["pixel"] else {}),
                }
            )
    return matched[:6]


def linkify_first_mention(body: str, affiliates: list[dict]) -> str:
    """本文中の最初の1回だけ、ツール名をアフィリエイトリンクに置き換える。

    見出し行・表・コードブロック・既存リンク内は対象外にして Markdown を壊さない。
    """
    if not affiliates:
        return body

    remaining = {a["name"]: a["url"] for a in affiliates}
    lines = body.split("\n")
    in_code = False

    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line.strip():
            continue
        if line.lstrip().startswith(("#", "|", ">", "    ")):
            continue

        for name in list(remaining):
            if "[" in line or "](" in line:
                break  # すでにリンクを含む行は触らない
            pattern = re.compile(re.escape(name), re.IGNORECASE)
            match = pattern.search(line)
            if not match:
                continue
            url = remaining.pop(name)
            line = (
                line[: match.start()]
                + f"[{match.group(0)}]({url})"
                + line[match.end() :]
            )
            lines[index] = line

    return "\n".join(lines)


def clean_list(values, limit: int) -> list[str]:
    seen: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in seen:
            seen.append(text)
        if len(seen) >= limit:
            break
    return seen


def pick_thumbnail(video_id: str) -> str:
    maxres = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
    if url_exists(maxres):
        return maxres
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def build_record(entry: dict, data: dict) -> dict:
    channel: Channel = entry["channel"]
    video_id = entry["video_id"]

    tags = clean_list(list(channel.base_tags) + list(data.get("tags") or []), 8)
    timetable = []
    for item in data.get("timetable") or []:
        time_value = str(item.get("time", "")).strip()
        label = str(item.get("label", "")).strip()
        if re.fullmatch(r"\d{1,2}(:\d{2}){1,2}", time_value) and label:
            timetable.append({"time": time_value, "label": label})

    # ジャンルは「チャンネルの主分野」ではなく「動画で実際に語られた内容」で決める。
    # 不動産チャンネルが投資信託を論じる回などを正しく分類し、免責文も内容に合わせるため。
    resolved_genre = GENRES_BY_SLUG.get(str(data.get("genre", "")).strip(), channel.genre)
    if resolved_genre.slug != channel.genre.slug:
        log(f"   [genre] {channel.genre.name} → {resolved_genre.name}（内容で判定）")

    record = {
        "slug": unique_slug(slugify(data.get("slug_en", "")), video_id),
        "title": str(data["title"]).strip(),
        "seoTitle": str(data.get("seo_title", "")).strip(),
        "threadsPost": normalize_body(str(data.get("threads_post", ""))).strip(),
        "threadsHook": threads_hooks.clean_hook(data.get("threads_hook")),
        "threadsStyle": threads_hooks.POST_STYLE,
        "writingVoice": entry.get("_voice", ""),
        "lede": normalize_body(data.get("lede", "")),
        "description": str(data["description"]).strip(),
        "keywords": clean_list(data.get("keywords"), 14),
        "tags": tags,
        "highlights": clean_list(data.get("highlights"), 3),
        "timetable": timetable[:20],
        "verdict": clean_verdict(data.get("verdict")),
        # 「仕事・お金にどう活かせる？」欄（記事ページの判定カードの下。無い記事は出さない）
        "workMoney": persona.clean_work_money(data.get("work_money")),
        "body": normalize_body(data["body_markdown"]),
        # 「かんたんに読む」版（記事ページで「専門家として読む」と切り替えられる）
        "easy": normalize_body(str(data.get("easy_markdown", ""))),
        "channel": channel.name,
        "channelId": channel.channel_id,
        "channelSlug": channel.slug,
        "genre": resolved_genre.name,
        "genreSlug": resolved_genre.slug,
        "disclaimer": resolved_genre.disclaimer,
        "videoId": video_id,
        "videoUrl": f"https://www.youtube.com/watch?v={video_id}",
        "videoTitle": entry["title"],
        "publishedAt": entry["published_at"].astimezone(timezone.utc).isoformat(),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "thumbnail": pick_thumbnail(video_id),
        "model": MODEL,
        "affiliates": [],
    }

    # 記事が言及したツールとアフィリエイト定義を突き合わせ、本文にもリンクを1回だけ挿入する
    affiliates = match_affiliates(record, data.get("mentioned_tools") or [])
    if affiliates:
        record["affiliates"] = affiliates
        record["body"] = linkify_first_mention(record["body"], affiliates)
        log(f"   [aff] {len(affiliates)}件: {', '.join(a['name'] for a in affiliates)}")

    return record


def write_article(record: dict) -> Path:
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTICLES_DIR / f"{record['slug']}.json"
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


# --------------------------------------------------------------------------- #
# 単発の動画URL指定
# --------------------------------------------------------------------------- #


def entry_from_url(url: str, channel_slug: str | None = None) -> dict:
    match = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})", url)
    if not match:
        raise SystemExit(f"動画IDを URL から抽出できません: {url}")
    video_id = match.group(1)

    channel = next((c for c in CHANNELS if c.slug == channel_slug), None)
    if channel is None:
        # チャンネル未指定なら、どのRSSに含まれているかを探して自動判定する
        for candidate in CHANNELS:
            if any(e["video_id"] == video_id for e in parse_feed(candidate)):
                channel = candidate
                break
    if channel is None:
        raise SystemExit(
            "この動画のチャンネルを特定できませんでした。"
            "--channel <slug> で明示してください（slug は scripts/channels.py 参照）"
        )

    return {
        "video_id": video_id,
        "title": f"YouTube video {video_id}",
        "summary": "",
        "published": "",
        "published_at": datetime.now(timezone.utc),
        "channel": channel,
    }


# --------------------------------------------------------------------------- #
# サンプル記事（表示確認用）
# --------------------------------------------------------------------------- #


def write_sample() -> Path:
    entry = {
        "video_id": "SAMPLE00000",
        "title": "【サンプル】自動生成パイプラインの動作確認用記事",
        "published_at": datetime.now(timezone.utc),
        "channel": CHANNELS[0],
    }
    data = {
        "title": "【サンプル記事】自動生成パイプラインの表示確認用ダミー",
        "slug_en": "sample-preview-article",
        "description": "レイアウト確認用のダミー記事です。GEMINI_API_KEY を設定して python3 scripts/main.py を実行すると本物の記事に置き換わります。",
        "keywords": ["サンプル", "動作確認"],
        "tags": ["サンプル"],
        "highlights": [
            "これはレイアウト確認のためのダミーテキストです（要点1）",
            "本番では動画から抽出した具体的な数値が入ります（要点2）",
            "GEMINI_API_KEY 設定後に本物の記事が生成されます（要点3）",
        ],
        "timetable": [
            {"time": "0:00", "label": "オープニング（ダミー）"},
            {"time": "1:23", "label": "主要機能のデモ（ダミー）"},
        ],
        "body_markdown": (
            "## この発表を一言でいうと\n\nこれはサンプル記事です。実際の記事は "
            "Gemini が動画を解析して生成します。\n\n"
            "## 発表された機能・アップデートの全容\n\n### ダミー機能A\n\n本文サンプル。\n\n"
            "## 性能・ベンチマーク\n\n| 項目 | 値 |\n| --- | --- |\n| ダミー | 100 |\n\n"
            "## まとめ\n\n`python3 scripts/main.py --max 1` で本物の記事を生成できます。\n"
        ),
    }
    record = build_record(entry, data)
    record["thumbnail"] = "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
    return write_article(record)


# --------------------------------------------------------------------------- #
# メイン
# --------------------------------------------------------------------------- #


def relink_existing() -> int:
    """affiliate_links.json を更新したあと、既存記事のリンクを貼り直す（API不要）。"""
    if not ARTICLES_DIR.exists():
        log("[relink] 記事がありません")
        return 0

    updated = 0
    for path in sorted(ARTICLES_DIR.glob("*.json")):
        try:
            record = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError:
            log(f"[warn] 読み込み失敗: {path.name}")
            continue

        before = json.dumps(record.get("affiliates", []), sort_keys=True)
        affiliates = match_affiliates(record, record.get("tags", []))
        if json.dumps(affiliates, sort_keys=True) == before:
            continue

        record["affiliates"] = affiliates
        record["body"] = linkify_first_mention(record["body"], affiliates)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        updated += 1
        log(f"[relink] {path.name}: {len(affiliates)}件")

    log(f"[done] {updated} 本を更新しました")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max", type=int, default=DEFAULT_MAX_ARTICLES, help="1回で生成する最大記事数")
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="何日前までの動画を対象にするか")
    parser.add_argument("--channel", action="append", help="対象チャンネル slug（複数指定可）")
    parser.add_argument("--video", help="この YouTube URL だけを処理する")
    parser.add_argument("--dry-run", action="store_true", help="API を呼ばず対象一覧のみ表示")
    parser.add_argument("--sample", action="store_true", help="ダミー記事を1本生成して終了")
    parser.add_argument(
        "--relink",
        action="store_true",
        help="APIを呼ばず、既存記事のアフィリエイトリンクだけを貼り直す",
    )
    args = parser.parse_args()

    if args.relink:
        return relink_existing()

    if args.sample:
        path = write_sample()
        log(f"[sample] 生成しました: {path.relative_to(ROOT)}")
        return 0

    state = load_state()
    state.setdefault("processed", {})
    state.setdefault("failed", {})

    if args.video:
        candidates = [
            entry_from_url(args.video, args.channel[0] if args.channel else None)
        ]
    else:
        # active=False のチャンネルは新規生成の対象から外す（一点集中のため）。
        # --channel で明示した場合だけは停止中でも処理する。
        channels = [c for c in CHANNELS if c.active]
        if args.channel:
            wanted = set(args.channel)
            channels = [c for c in CHANNELS if c.slug in wanted]
            if not channels:
                raise SystemExit(f"該当するチャンネルがありません: {args.channel}")
        candidates = collect_candidates(channels, args.days, state)

    log(f"[info] 候補 {len(candidates)} 件 / 上限 {args.max} 件")
    # 記事化の価値なし（skip）と判定される動画が多いので、上限は「生成できた本数」で数える。
    # 解析を試す動画は多めに確保し、試行回数と経過時間の上限で無料枠とジョブ時間を守る。
    targets = candidates[: args.max * ATTEMPTS_PER_ARTICLE]

    if not targets:
        log("[done] 新規に処理すべき動画はありません")
        return 0

    for entry in targets:
        log(f"  - [{entry['channel'].name}] {entry['title']} ({entry['video_id']})")

    if args.dry_run:
        log("[dry-run] API 呼び出しはスキップしました")
        return 0

    client = build_client()
    created = 0
    # 同じ発表の記事が直近5日にあれば作らない（scripts/story.py。サイトの重複判定と同じ基準。
    # 1つの事例を何本にも分けた公式動画・同じ動画の再投稿・解説チャンネルの後追いを外す）
    import story  # 遅延 import（--dry-run・--relink では使わない）
    same_story = story.Recent(5)

    run_started = time.time()
    for index, entry in enumerate(targets, start=1):
        if created >= args.max:
            break
        if time.time() - run_started > RUN_TIME_BUDGET_SEC:
            log("[info] 時間の上限に達したため、残りは次回に回します")
            break

        video_id = entry["video_id"]
        same = same_story.match(entry["title"], video_id=video_id, published=entry.get("published_at"),
                                extra_titles=[entry["title"]], channel_slug=entry["channel"].slug)
        if same:
            log(f"\n[{index}/{len(targets)}] [skip] 同じ発表の記事あり: {entry['title'][:60]} → {same[0]}（{same[1]}）")
            state["processed"][video_id] = {"skipped": True, "reason": f"dedupe: {same[1]}: {same[0]}"[:200],
                                             "at": datetime.now(timezone.utc).isoformat()}
            continue
        log(f"\n[{index}/{len(targets)}] 解析中: {entry['title']}")
        started = time.time()

        try:
            data = generate_article(client, entry)
        except Exception as error:  # noqa: BLE001
            log(f"   [NG] {error}")
            state["failed"][video_id] = state["failed"].get(video_id, 0) + 1
            if "RESOURCE_EXHAUSTED" in str(error):
                # 1日の無料枠を使い切った。続けても失敗するだけなので打ち切る
                log("[info] 無料枠の上限に達したため、残りは次回に回します")
                break
            continue

        if not data.get("is_relevant", True):
            reason = data.get("skip_reason") or "記事化の価値なしと判定"
            log(f"   [skip] {reason}")
            state["processed"][video_id] = {
                "skipped": True,
                "reason": reason,
                "at": datetime.now(timezone.utc).isoformat(),
            }
            continue

        try:
            record = build_record(entry, data)
        except (KeyError, TypeError, ValueError) as error:
            log(f"   [NG] レスポンスの形式が不正: {error}")
            state["failed"][video_id] = state["failed"].get(video_id, 0) + 1
            continue

        if (datetime.now(timezone.utc) - entry["published_at"]).total_seconds() > NEWS_MAX_AGE_HOURS * 3600:
            record["late"] = True  # 投稿から48時間を超えた動画（解説チャンネル）。ニュース用サイトマップに載せない
        path = write_article(record)
        same_story.add(record)
        import quality  # 遅延 import（読みやすさの数値をログに残すだけ。動画記事は作り直さない）
        log(f"   [read] {quality.readability(record.get('body') or '')}")
        state["processed"][video_id] = {
            "slug": record["slug"],
            "at": record["generatedAt"],
        }
        state["failed"].pop(video_id, None)
        created += 1
        log(
            f"   [OK] {path.relative_to(ROOT)} "
            f"({len(record['body'])}文字 / {time.time() - started:.0f}s)"
        )

    save_state(state)
    log(f"\n[done] 新規記事 {created} 本を生成しました")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
