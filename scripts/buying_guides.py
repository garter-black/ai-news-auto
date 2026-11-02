#!/usr/bin/env python3
"""比較ガイド（/compare/*）を作る・更新する。

  python3 scripts/buying_guides.py                 # まだ無いガイドを作る（無料枠の範囲で）
  python3 scripts/buying_guides.py --limit 3       # 3本まで
  python3 scripts/buying_guides.py --only security-software --force   # 作り直し
  python3 scripts/buying_guides.py --refresh       # 価格・関連記事・更新日だけ更新（週1回の想定）
  python3 scripts/buying_guides.py --list          # 予定のガイド一覧

方針（A8・景品表示法・Google の品質評価のため）:
- 使った・試したとは書かない。「公式情報と価格をもとに比較」。独自の評価点や星は付けない。
- 価格は「いま取得した公式ページの文字」か offers.json / products.json の値だけ。どちらにも無ければ空（公式サイトで確認）。
  LLM が出した価格は、数字が公式ページ（または手持ちデータ）にそのまま載っているかを機械的に照合する。
- 長所・注意点は公式ページの文字とサイソクの記事だけから書かせ、別のモデルで事実確認する。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feeds  # noqa: E402
import llm  # noqa: E402
import quality  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "content" / "buying-guides"
OFFERS = ROOT / "content" / "offers.json"
PRODUCTS = ROOT / "content" / "products.json"
AFFILIATES = ROOT / "affiliate_links.json"
ARTICLES = ROOT / "content" / "articles"
JST = timezone(timedelta(hours=9))

OFFICIAL_CAP = 4500  # 1商品あたり LLM に渡す公式ページの文字数
PASS_SCORE = 75
REFRESH_DAYS = 7

# 「使った」「点数」など、やっていないことを書かせない
BANNED_RE = re.compile(
    r"(使って|試して|触って)み(た|ました|ると)|実際に(使|試)|使用感|検証し(た|ました)|検証結果|テストし(た|ました)|計測し(た|ました)|"
    r"愛用|編集部が(使|試)|レビューし|★|☆|点満点|\d+(\.\d+)?点中|総合評価|満足度|口コミ|評判"
)

# --------------------------------------------------------------------------- #
# ガイドの予定（高単価・購買意図の強い順）。partner は offers.json の partners のキー
# --------------------------------------------------------------------------- #

GUIDES: list[dict] = [
    {
        "slug": "security-software",
        "short": "ウイルス対策ソフト",
        "title": "ウイルス対策ソフトおすすめ比較【2026年版】AI詐欺・フィッシング対策で選ぶ",
        "queries": ["ウイルス対策ソフト おすすめ", "セキュリティソフト 比較", "ノートン ウイルスバスター 比較", "AI詐欺 対策 ソフト"],
        "topics": ["security"],
        "theme": "AIを使った偽メール・偽サイト・詐欺から、PCとスマホを守るセキュリティソフトを選びたい人",
        "columns": ["対応端末・台数", "主な保護機能", "その他の機能"],
        "related": r"セキュリティ|詐欺|フィッシング|マルウェア|ディープフェイク|なりすまし|ランサム|脆弱性",
        "products": [
            {"partner": "virusbuster", "urls": ["https://www.trendmicro.com/ja_jp/forHome/products/vb.html"]},
            {"partner": "norton", "urls": ["https://jp.norton.com/products/norton-360-standard"]},
            {"partner": "eset", "urls": ["https://personal.canon.jp/product/app/eset/lineup"]},
            {"partner": "surfshark", "urls": ["https://surfshark.com/ja/antivirus", "https://surfshark.com/ja/pricing"]},
        ],
    },
    {
        "slug": "vps-dify-n8n",
        "short": "Dify・n8n用のVPS",
        "title": "Dify・n8nを動かすVPSおすすめ比較【2026年版】XServer VPSとConoHa VPS",
        "queries": ["Dify VPS おすすめ", "n8n VPS", "Dify セルフホスト サーバー", "XServer VPS ConoHa VPS 比較"],
        "topics": ["server", "agent"],
        "theme": "Dify・n8n・Ollama などのAIアプリを、自分専用のサーバー（VPS）で動かしたい人",
        "columns": ["料金の目安", "アプリの導入", "向いている使い方"],
        "related": r"Dify|n8n|セルフホスト|VPS|Docker|Ollama|AIエージェント|ワークフロー",
        "products": [
            {"partner": "xserver-vps", "urls": ["https://vps.xserver.ne.jp/"]},
            {"partner": "conoha-vps", "urls": ["https://vps.conoha.jp/pricing/", "https://vps.conoha.jp/"]},
        ],
    },
    {
        "slug": "ai-voice-recorder",
        "short": "AIボイスレコーダー",
        "title": "AIボイスレコーダー比較：PLAUD NOTE ProとNotePin Sの違い【AI議事録】",
        "queries": ["AI議事録 ボイスレコーダー", "PLAUD NOTE Pro NotePin S 違い", "AIボイスレコーダー おすすめ", "議事録 自動作成 レコーダー"],
        "topics": ["voice"],
        "theme": "会議・商談・取材の録音から、文字起こしと要約（議事録）までAIで済ませたい人",
        "columns": ["形・重さ", "マイク・録音", "向いている場面"],
        "related": r"議事録|文字起こし|音声認識|ボイスレコーダー|音声AI|PLAUD|会議",
        "products": [
            {"partner": "plaud-note-pro", "urls": ["https://jp.plaud.ai/products/plaud-note-pro"]},
            {"partner": "plaud-notepin-s", "urls": ["https://jp.plaud.ai/products/plaud-notepin-s"]},
        ],
    },
    {
        "slug": "local-llm-pc",
        "short": "ローカルAI用のPC",
        "title": "ローカルLLM・画像生成向けPCおすすめ比較【2026年版】GPU搭載PCとミニPC",
        "queries": ["ローカルLLM PC おすすめ", "ローカルLLM スペック", "画像生成AI PC おすすめ", "VRAM 16GB PC"],
        "topics": ["local", "semis"],
        "theme": "ローカルLLMや画像生成AIを、クラウドではなく手元のPCで動かしたい人",
        "columns": ["タイプ", "GPU・AI処理", "向いている使い方"],
        "related": r"ローカルLLM|ローカルAI|RTX|GeForce|VRAM|Ollama|llama\.cpp|GPU|ミニPC|NPU",
        "products": [
            {"partner": "oz-rtx5070ti", "urls": ["https://www.ozgaming-pcshop.com/view/item/000000002716"]},
            {"partner": "oz-rx9060xt", "urls": ["https://www.ozgaming-pcshop.com/view/item/000000002781"]},
            {"partner": "mdl", "urls": ["https://www.mdl.co.jp/"]},
            {"partner": "minisforum", "urls": ["https://www.minisforum.jp/"]},
            {"partner": "gmktec", "urls": ["https://jp.gmktec.com/"]},
        ],
    },
    {
        "slug": "rental-server",
        "short": "レンタルサーバー",
        "title": "レンタルサーバーおすすめ比較【2026年版】WordPress・AIブログを始めるなら",
        "queries": ["レンタルサーバー おすすめ", "レンタルサーバー 比較", "WordPress サーバー おすすめ", "エックスサーバー シンレンタルサーバー 違い"],
        "topics": [],
        "theme": "WordPressでブログやサイトを始めたい・AIで作った記事やツールを公開したい人",
        "columns": ["料金の目安", "特徴", "向いている人"],
        "related": r"WordPress|ブログ|レンタルサーバー|ホスティング|サイト制作",
        "products": [
            {"partner": "xserver", "urls": ["https://www.xserver.ne.jp/price/"]},
            {"partner": "shin-server", "urls": ["https://www.shin-server.jp/"]},
            {"partner": "lolipop", "urls": ["https://lolipop.jp/pricing/"]},
            {"partner": "sakura", "urls": ["https://rs.sakura.ad.jp/plan/"]},
        ],
    },
    {
        "slug": "video-editing",
        "short": "動画編集ソフト",
        "title": "動画編集ソフト・スクール比較【2026年版】AI動画を仕上げるなら",
        "queries": ["動画編集ソフト 初心者 おすすめ", "動画編集 ソフト 比較", "Movavi Bandicut 違い", "動画編集 スクール おすすめ"],
        "topics": ["video"],
        "theme": "生成AIで作った動画や撮った動画に、カット・字幕・BGMを足して仕上げたい人／動画編集を仕事にしたい人",
        "columns": ["種類", "主な機能", "料金の形"],
        "related": r"動画編集|動画生成|Sora|Veo|Runway|Kling|YouTube|ショート動画|字幕|Premiere",
        "products": [
            {"partner": "video-editor", "urls": ["https://www.movavi.com/jp/videoeditor/"]},
            {"partner": "bandicut", "urls": ["https://www.bandicam.jp/bandicut-video-cutter/", "https://www.bandicam.jp/bandicut-video-cutter/buy/"]},
            {"partner": "movie-school", "urls": ["https://creators-jp.com/"]},
        ],
    },
    {
        "slug": "ai-english",
        "short": "英語学習サービス",
        "title": "AI英会話・英語学習サービス比較【2026年版】AI翻訳の次に話せるように",
        "queries": ["AI英会話 おすすめ", "英語学習 アプリ 比較", "オンライン英会話 比較 社会人", "英作文 添削 サービス"],
        "topics": ["english"],
        "theme": "AI翻訳に頼るだけでなく、自分で英語を話せる・書けるようになりたい社会人",
        "columns": ["学び方", "主な特徴", "続けやすさ"],
        "related": r"英語|英会話|翻訳|通訳|DeepL|多言語|語学",
        "products": [
            {"partner": "studysapuri-en", "urls": ["https://eigosapuri.jp/"]},
            {"partner": "bestteacher", "urls": ["https://www.best-teacher-inc.com/"]},
            {"partner": "idiy", "urls": ["https://www.idiy.biz/"]},
            {"partner": "lancul", "urls": ["https://lancul.com/"]},
        ],
    },
    {
        "slug": "image-generation",
        "short": "画像生成AIの環境",
        "title": "画像生成AIの始め方比較：ブラウザで使うサービスか、GPU搭載PCか【2026年版】",
        "queries": ["画像生成AI 始め方", "画像生成AI サービス 比較", "Stable Diffusion PC スペック", "ConoHa AI Canvas"],
        "topics": ["image"],
        "theme": "Stable Diffusion などの画像生成AIを、これから本格的に始めたい人",
        "columns": ["使い方", "初期費用・料金の形", "向いている人"],
        "related": r"画像生成|Stable Diffusion|ComfyUI|Midjourney|Nano Banana|ChatGPT Images|イラスト",
        "products": [
            {"partner": "conoha-ai-canvas", "urls": ["https://ai.conoha.jp/canvas/"]},
            {"partner": "oz-rx9060xt", "urls": ["https://www.ozgaming-pcshop.com/view/item/000000002781"]},
            {"partner": "mdl", "urls": ["https://www.mdl.co.jp/"]},
        ],
    },
    {
        "slug": "ai-writing-tools",
        "short": "AI文章ツール",
        "title": "AI文章チェック・文章作成ツール比較【2026年版】文賢とDoraverse",
        "queries": ["AI 文章校正 ツール", "文章チェック ツール おすすめ", "文賢 料金", "複数のAI まとめて使える"],
        "topics": ["writing"],
        "theme": "AIで書いた文章を整えたい・仕事の文章をAIで速く正確に書きたい人",
        "columns": ["できること", "使うAI", "向いている人"],
        "related": r"文章|ライティング|校正|記事作成|執筆|ChatGPT|Claude|Gemini",
        "products": [
            {"partner": "bunken", "urls": ["https://rider-store.jp/bun-ken/"]},
            {"partner": "doraverse", "urls": ["https://doraverse.com/ja/pricing", "https://doraverse.com/"]},
        ],
    },
    {
        "slug": "kids-programming",
        "short": "子どものプログラミング教材",
        "title": "子どものプログラミング・AI教材比較【2026年版】家で学ぶか、教室に通うか",
        "queries": ["子ども プログラミング 教材 おすすめ", "小学生 プログラミング 比較", "デジタネ LITALICOワンダー 違い", "子ども AI 学習"],
        "topics": ["kids"],
        "theme": "子どもにプログラミングやAIとの付き合い方を学ばせたい保護者",
        "columns": ["学び方", "主な内容", "向いている子"],
        "related": r"子ども|子供|小学生|中学生|教育|学校|プログラミング教育",
        "products": [
            {"partner": "digitane", "urls": ["https://digitane.jp/"]},
            {"partner": "litalico", "urls": ["https://wonder.litalico.jp/"]},
        ],
    },
    {
        "slug": "ai-mini-pc",
        "short": "ミニPC",
        "title": "AI向けミニPC比較【2026年版】GMKtecとMINISFORUM、NPU搭載機の選び方",
        "queries": ["ミニPC AI おすすめ", "NPU ミニPC", "GMKtec MINISFORUM 比較", "ミニPC ローカルLLM"],
        "topics": [],
        "theme": "机の上で場所を取らずに、ローカルAIや普段の作業をこなせる小型PCがほしい人",
        "columns": ["主なラインアップ", "AI処理", "向いている人"],
        "related": r"ミニPC|NPU|Ryzen AI|ローカルLLM|Copilot\+ PC|オンデバイス",
        "products": [
            {"partner": "gmktec", "urls": ["https://jp.gmktec.com/"]},
            {"partner": "minisforum", "urls": ["https://www.minisforum.jp/"]},
        ],
    },
]

# --------------------------------------------------------------------------- #
# データ
# --------------------------------------------------------------------------- #


def log(message: str) -> None:
    print(message, flush=True)


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return default


def approved_partners() -> dict:
    """A8 で提携済み（affiliate_links.json に A8 の URL がある）の商品だけ"""
    offers = load_json(OFFERS, {})
    affiliates = load_json(AFFILIATES, {})
    out = {}
    for key, partner in offers.get("partners", {}).items():
        program = affiliates.get(partner.get("program"), {})
        url = program.get("url", "") if isinstance(program, dict) else ""
        if "a8.net" in (partner.get("link") or url):
            out[key] = partner
    return out


def known_price(key: str, partner: dict) -> tuple[str, str] | None:
    """(価格の文字, 出典) 。手持ちデータ（products.json → offers.json）にある価格"""
    products = load_json(PRODUCTS, {})
    value = (products.get("partnerPrices") or {}).get(key)
    if isinstance(value, (int, float)):
        return f"{int(value):,}円", "products.json"
    if isinstance(partner.get("price"), (int, float)):
        return f"{int(partner['price']):,}円", "offers.json"
    if partner.get("priceText"):
        return partner["priceText"], "offers.json"
    return None


def fetch_official(url: str) -> str:
    """公式ページの本文（ナビ等を除いた全体）。失敗したら空文字"""
    try:
        data, _final = feeds.fetch(url, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")
        parser = feeds._Extractor()  # noqa: SLF001 - 同じ抽出器を使う
        try:
            parser.feed(feeds._decode(data))  # noqa: SLF001
            parser.close()
        except Exception:  # noqa: BLE001
            pass
        return feeds._collapse(parser.buffers["body"])  # noqa: SLF001
    except Exception as error:  # noqa: BLE001
        log(f"   取得失敗 {url}: {str(error)[:80]}")
        return ""


def numbers_of(text: str) -> list[str]:
    return re.findall(r"\d[\d,，]*(?:\.\d+)?", text or "")


def _digits_norm(text: str) -> str:
    return re.sub(r"[,，\s　]", "", text or "")


def price_verified(price: str, *sources: str) -> bool:
    """価格の数字が、どれかの出典の文字にそのまま載っているか"""
    nums = [_digits_norm(n) for n in numbers_of(price)]
    if not nums:
        return False
    for source in sources:
        norm = _digits_norm(source)
        if all(re.search(rf"(?<!\d){re.escape(n)}(?!\d)", norm) for n in nums):
            return True
    return False


def related_slugs(pattern: str, limit: int = 6) -> list[str]:
    """関連するAI記事（新しい順）"""
    regex = re.compile(pattern, re.I)
    rows = []
    for path in ARTICLES.glob("*.json"):
        a = load_json(path, {})
        if a.get("genreSlug", "ai") != "ai" or a.get("indexable") is False:
            continue
        text = " ".join([a.get("title", ""), a.get("seoTitle") or "", *a.get("tags", [])])
        if regex.search(text):
            rows.append((a.get("publishedAt", ""), a.get("slug")))
    rows.sort(reverse=True)
    return [slug for _, slug in rows[:limit] if slug]


def related_context(slugs: list[str]) -> str:
    """サイソクの過去記事から、ガイドの文脈に使える要点（確認済みの事実）"""
    parts = []
    for slug in slugs[:4]:
        a = load_json(ARTICLES / f"{slug}.json", {})
        if a:
            parts.append(f"- {a.get('title')}（{(a.get('publishedAt') or '')[:10]}）: " + " / ".join(a.get("highlights", [])[:3]))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# LLM
# --------------------------------------------------------------------------- #

S = {"type": "STRING"}
SA = {"type": "ARRAY", "items": S}

GUIDE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "description": S,
        "intro": S,
        "criteria": SA,
        "products": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "partner": S,
                    "price": S,
                    "priceNote": S,
                    "specs": SA,
                    "bestFor": S,
                    "summary": S,
                    "strengths": SA,
                    "limits": SA,
                },
                "required": ["partner", "price", "specs", "bestFor", "summary", "strengths", "limits"],
                "propertyOrdering": ["partner", "price", "priceNote", "specs", "bestFor", "summary", "strengths", "limits"],
            },
        },
        "quickPick": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"partner": S, "reason": S},
                "required": ["partner", "reason"],
                "propertyOrdering": ["partner", "reason"],
            },
        },
        "faq": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"q": S, "a": S},
                "required": ["q", "a"],
                "propertyOrdering": ["q", "a"],
            },
        },
    },
    "required": ["description", "intro", "criteria", "products", "quickPick", "faq"],
    "propertyOrdering": ["description", "intro", "criteria", "products", "quickPick", "faq"],
}

SYSTEM = """あなたは日本のIT系メディア「サイソクニュース」の編集者です。読者が商品を選ぶための比較ガイドを書きます。
絶対の規則:
- 書いてよい事実は「公式ページの抜粋」「サイソクのデータ」「サイソクの過去記事」にあるものだけ。記憶や推測で数値・機能・価格・対応OS・特典を足さない。
- 編集部が商品を使った・試した・検証した・計測したとは書かない（実際にしていない）。使用感・口コミ・評判・点数・星・順位づけの根拠になる独自評価も書かない。
- 「最強」「絶対」「No.1」などの断定は、公式ページにその表現と根拠があるときだけ「公式によると」と付けて書く。
- 公式ページに無い項目は「公式ページに記載なし」と書く。キャンペーン価格は期間限定であることを添える。
- 文体は「です・ます」。短く具体的に。読者の立場（何をしたい人か）を起点に選び方を示す。
出力は指定の JSON だけ。"""

PROMPT = """# 作るガイド
タイトル: {title}
読者: {theme}
狙う検索語: {queries}
調べた日: {today}

# 比較する商品（partner の値をそのまま使う）
{products}

# サイソクの過去記事（確認済みの事実。文脈づけにだけ使ってよい）
{context}

# 書くもの
- description: 検索結果に出す説明（90〜120字）。「公式情報と価格をもとに比較」を含める。
- intro: 導入（250〜400字）。読者の悩み→このガイドで何を比べるか→「{today}時点の公式情報と価格をもとに比較しています」と明記（「使った／試した」系の言葉は否定形でも書かない。注記はページ側で出す）。
- criteria: 選び方・比べた観点（3〜5個、各40〜90字）。
- products: 上の商品すべてを、上と同じ順で。
  - price: 料金・価格を「公式ページの抜粋」か「サイソクのデータ」から**文字どおり**写す（例「月額1,650円（税込）〜」）。複数プランがあれば代表的な最安の1つ。見つからなければ空文字。数字を計算・換算しない。
  - priceNote: 価格の補足（年払い・キャンペーン・税込など、抜粋にある範囲で。30字以内。無ければ空）。
  - specs: 比較表の列「{columns}」の順に、各30字以内で。抜粋に無ければ「公式ページに記載なし」。
  - bestFor: 向いている人（40字以内）。
  - summary: 概要（80〜150字）。
  - strengths: 公式情報にもとづく強み（2〜4個、各30〜70字）。
  - limits: 選ぶ前に知っておきたい注意点（1〜3個、各30〜70字。例: 対応していない用途、別料金、プラン差、記載の無い点）。悪口ではなく事実ベースで。
- quickPick: 「迷ったらこれ」最大3つ。読者のタイプ別に1つずつ（例「スマホもまとめて守りたい人」）。reason は50字以内で、なぜそのタイプに合うか。
- faq: 4〜5問。検索されそうな質問（料金・無料体験・違い・選び方など）に、抜粋の範囲で答える。答えられない点は「公式サイトで確認してください」とする。"""

FACT_PROMPT = """# 出典（公式ページの抜粋・サイソクのデータ・過去記事）
<<<
{source}
>>>

# 確認するガイド
<<<
{guide}
>>>

# 確認のしかた
- ガイドの数値・価格・機能・対応端末・特典・固有名詞が、出典に書かれているか、出典と矛盾しないかを確かめる。
- 問題にしない: 出典の言い換え・要約、「公式ページに記載なし」「公式サイトで確認」の記述、読者の立場に合わせた一般的な選び方の助言（具体的な数値を含まないもの）。
- 必ず問題にする: 出典に無い数値・価格・機能・対応OSの創作、出典との矛盾、編集部が使った・試したかのような記述、根拠の無い断定や順位づけ。

# 出力
- grounded: 創作・矛盾が1つも無ければ true。
- score: 0〜100。
- problems: 最大8個。「該当箇所（短く引用）→ 問題」を1文で。"""


def product_block(index: int, key: str, partner: dict, official: str, known: tuple[str, str] | None) -> str:
    lines = [
        f"## {index}. partner: {key}",
        f"商品名: {partner['name']}（{partner.get('shop', '')}）",
        f"サイソクの一言メモ: {partner.get('note', '')}",
    ]
    if known:
        lines.append(f"サイソクのデータの価格: {known[0]}")
    lines.append(f"公式ページの抜粋:\n<<<\n{official[:OFFICIAL_CAP] or '（取得できず）'}\n>>>")
    return "\n".join(lines)


def guide_text(data: dict) -> str:
    """事実確認と禁止表現チェック用に、ガイドの文を1つにまとめる"""
    parts = [data.get("description", ""), data.get("intro", ""), *data.get("criteria", [])]
    for p in data.get("products", []):
        parts += [
            f"[{p.get('partner')}] 価格: {p.get('price')} {p.get('priceNote', '')}",
            "比較表: " + " / ".join(p.get("specs", [])),
            f"向いている人: {p.get('bestFor')}",
            p.get("summary", ""),
            "強み: " + " / ".join(p.get("strengths", [])),
            "注意点: " + " / ".join(p.get("limits", [])),
        ]
    parts += [f"迷ったら[{q.get('partner')}]: {q.get('reason')}" for q in data.get("quickPick", [])]
    parts += [f"Q. {f.get('q')} A. {f.get('a')}" for f in data.get("faq", [])]
    return "\n".join(str(x) for x in parts if x)


def structural_problems(data: dict, spec: dict, keys: list[str]) -> list[str]:
    problems = []
    got = [p.get("partner") for p in data.get("products", [])]
    if got != keys:
        problems.append(f"products の partner が指定と違う（{got} / 指定 {keys}）。指定の順で全商品を書く")
    for p in data.get("products", []):
        if len(p.get("specs", [])) != len(spec["columns"]):
            problems.append(f"{p.get('partner')} の specs が {len(spec['columns'])} 列になっていない")
        if not p.get("strengths") or not p.get("limits"):
            problems.append(f"{p.get('partner')} の strengths / limits が空")
    picks = [q.get("partner") for q in data.get("quickPick", [])]
    if not picks or any(k not in keys for k in picks) or len(picks) > 3:
        problems.append("quickPick は指定の商品から1〜3個")
    if not 3 <= len(data.get("faq", [])) <= 5:
        problems.append("faq は3〜5問")
    # 「使ったものではありません」のような否定の注記は問題にしない
    text = re.sub(r"(実際に)?(使|試)(った|用した|し)(もの|わけ)では(な[いく]|ありません)", "", guide_text(data))
    if m := BANNED_RE.search(text):
        around = text[max(0, m.start() - 15) : m.end() + 15].replace("\n", " ")
        problems.append(f"使ってもいない・根拠の無い評価の表現がある（「{around}」）。編集部や第三者が試した・評価したと読める表現を、公式情報の説明に言い換える")
    if "公式情報" not in data.get("intro", ""):
        problems.append("intro に「公式情報と価格をもとに比較」の旨が無い")
    return problems


def fact_check(data: dict, source: str) -> dict:
    prompt = FACT_PROMPT.format(source=source[:40000], guide=guide_text(data)[:12000])
    result, model = llm.generate_json(quality.FACT_SYSTEM, prompt, quality.FACT_SCHEMA, temperature=0.1, max_output_tokens=4096, tier="bulk")
    try:
        score = max(0, min(100, int(result.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    problems = [str(p).strip() for p in (result.get("problems") or []) if str(p).strip()][:8]
    return {"grounded": bool(result.get("grounded")), "score": score, "problems": problems, "model": model}


# --------------------------------------------------------------------------- #
# 作る
# --------------------------------------------------------------------------- #


def collect_facts(spec: dict, partners: dict) -> list[dict]:
    """商品ごとの公式ページ本文と手持ちの価格"""
    facts = []
    for item in spec["products"]:
        key = item["partner"]
        partner = partners.get(key)
        if not partner:
            log(f"   {key}: 提携済みの商品に無いので外す")
            continue
        texts, sources = [], []
        for url in item["urls"]:
            text = fetch_official(url)
            if text:
                texts.append(text)
                sources.append(url)
        official = "\n".join(texts)
        known = known_price(key, partner)
        if len(official) < 300 and not known:
            log(f"   {key}: 公式ページを取得できず手持ちの価格も無いので外す")
            continue
        facts.append({"key": key, "partner": partner, "official": official, "urls": sources or item["urls"], "known": known})
    return facts


def safe_note(note: str, fact: dict) -> str:
    """価格の補足。数字（期間・割引率・金額）が出典に無ければ載せない"""
    note = note.strip()
    # 「記載なし」「ご確認ください」などは補足にならないので出さない
    if re.search(r"記載なし|確認(して)?ください|ご確認|サイソクのデータ", note):
        return ""
    if not numbers_of(note):
        return note
    known = fact["known"][0] if fact["known"] else ""
    return note if price_verified(note, fact["official"], known) else ""


def data_checked_at(file: str, fallback: str) -> str:
    """手持ちデータの価格の確認日（products.json は取得日時、offers.json は今回の確認日）"""
    if file == "products.json":
        return load_json(PRODUCTS, {}).get("fetchedAt") or fallback
    return fallback


def finalize_price(row: dict, fact: dict, fetched_at: str) -> dict:
    """LLM の価格を出典と照合し、出典つきの価格にする（照合できなければ空）"""
    price = (row.get("price") or "").strip()
    known = fact["known"]
    if price and price_verified(price, fact["official"]):
        return {"price": price, "priceSource": {"type": "official", "url": fact["urls"][0], "checkedAt": fetched_at}}
    if price and known and price_verified(price, known[0]):
        return {"price": price, "priceSource": {"type": "data", "file": known[1], "checkedAt": data_checked_at(known[1], fetched_at)}}
    if known:
        return {"price": known[0], "priceSource": {"type": "data", "file": known[1], "checkedAt": data_checked_at(known[1], fetched_at)}}
    if price:
        log(f"   {fact['key']}: 価格「{price}」が公式ページに見つからないので載せない")
    return {"price": "", "priceSource": None}


_FAILED_TIERS: set[str] = set()


def build(spec: dict, partners: dict, retries: int = 1) -> dict | None:
    log(f"\n▶ {spec['slug']}: {spec['title']}")
    fetched_at = now_iso()
    facts = collect_facts(spec, partners)
    if len(facts) < 2:
        log("   比べられる商品が2つ未満なので見送り")
        return None
    keys = [f["key"] for f in facts]
    related = related_slugs(spec["related"])
    context = related_context(related) or "（なし）"
    today = datetime.now(JST)
    today_text = f"{today.year}年{today.month}月{today.day}日"
    prompt = PROMPT.format(
        title=spec["title"],
        theme=spec["theme"],
        queries="、".join(spec["queries"]),
        today=today_text,
        products="\n\n".join(product_block(i + 1, f["key"], f["partner"], f["official"], f["known"]) for i, f in enumerate(facts)),
        context=context,
        columns="」「".join(spec["columns"]),
    )
    source = "\n\n".join(
        f"## {f['partner']['name']}\nサイソクのデータ: {f['partner'].get('note', '')} {f['known'][0] if f['known'] else ''}\n{f['official'][:OFFICIAL_CAP]}"
        for f in facts
    ) + f"\n\n## サイソクの過去記事\n{context}"

    feedback = ""
    for attempt in range(retries + 1):
        data = None
        # quality が混雑（503）・枠切れのときは bulk で下書き（事実確認は同じ基準）
        for tier in ("quality", "bulk"):
            if tier in _FAILED_TIERS:
                continue
            try:
                data, model = llm.generate_json(SYSTEM, prompt + feedback, GUIDE_SCHEMA, temperature=0.4, max_output_tokens=16384, tier=tier)
                break
            except Exception as error:  # noqa: BLE001
                # 混雑で全滅した tier は、この実行中はもう待たない
                _FAILED_TIERS.add(tier)
                log(f"   {tier} で生成できず: {str(error)[:120]}")
        if data is None:
            return None
        problems = structural_problems(data, spec, keys)
        fact = None
        if not problems:
            try:
                fact = fact_check(data, source)
            except Exception as error:  # noqa: BLE001 - 確認できないものは出さない
                log(f"   事実確認できず: {str(error)[:120]}")
                return None
            if not fact["grounded"] or fact["score"] < PASS_SCORE:
                problems = fact["problems"] or [f"出典との整合性が低い（score {fact['score']}）"]
        if not problems:
            break
        log(f"   不合格（{attempt + 1}回目）: " + " / ".join(problems[:4]))
        feedback = quality.feedback_block(problems)
    else:
        return None

    by_key = {f["key"]: f for f in facts}
    products = []
    for row in data["products"]:
        fact_row = by_key[row["partner"]]
        products.append(
            {
                "partner": row["partner"],
                **finalize_price(row, fact_row, fetched_at),
                "priceNote": safe_note(row.get("priceNote") or "", fact_row),
                "specs": row["specs"],
                "bestFor": row["bestFor"],
                "summary": row["summary"],
                "strengths": row["strengths"],
                "limits": row["limits"],
                "officialUrl": fact_row["urls"][0],
            }
        )
    now = now_iso()
    previous = load_json(OUT_DIR / f"{spec['slug']}.json", {})
    return {
        "slug": spec["slug"],
        "title": spec["title"],
        "shortName": spec.get("short", ""),
        "description": data["description"],
        "targetQueries": spec["queries"],
        "topics": spec["topics"],
        "intro": data["intro"],
        "criteria": data["criteria"],
        "columns": spec["columns"],
        "products": products,
        "quickPick": data["quickPick"][:3],
        "faq": data["faq"],
        "relatedArticleSlugs": related,
        "sources": [{"partner": f["key"], "urls": f["urls"], "fetchedAt": fetched_at} for f in facts],
        "publishedAt": previous.get("publishedAt") or now,
        "updatedAt": now,
        "priceCheckedAt": fetched_at,
        "model": model,
        "factCheck": {"score": fact["score"], "model": fact["model"]} if fact else None,
    }


PRICE_SCHEMA = {"type": "OBJECT", "properties": {"price": S, "priceNote": S}, "required": ["price"], "propertyOrdering": ["price", "priceNote"]}


def refresh(guide: dict, spec: dict | None) -> dict:
    """価格の再確認・関連記事・更新日だけを更新する（本文は作り直さない）"""
    log(f"\n↻ {guide['slug']}")
    partners = approved_partners()
    fetched_at = now_iso()
    urls_by_key = {s["partner"]: s["urls"] for s in guide.get("sources", [])}
    kept = []
    for product in guide["products"]:
        key = product["partner"]
        partner = partners.get(key)
        if not partner:
            log(f"   {key}: 提携が切れたので外す")
            continue
        official = "\n".join(t for t in (fetch_official(u) for u in urls_by_key.get(key, [product.get("officialUrl")])) if t)
        known = known_price(key, partner)
        price = product.get("price", "")
        if official:
            product["priceNote"] = safe_note(product.get("priceNote") or "", {"official": official, "known": known})
        if price and price_verified(price, official):
            product["priceSource"] = {"type": "official", "url": product.get("officialUrl"), "checkedAt": fetched_at}
        elif known:
            product.update(price=known[0], priceSource={"type": "data", "file": known[1], "checkedAt": data_checked_at(known[1], fetched_at)})
        elif official:
            # 公式ページの価格が変わった → 抜粋から写し直す（照合できたものだけ）
            try:
                data, _ = llm.generate_json(
                    "公式ページの抜粋から料金を文字どおり写す。無ければ空文字。JSON だけを出力。",
                    f"商品: {partner['name']}\n代表的な最安の料金を1つ。数字を計算しない。\n<<<\n{official[:OFFICIAL_CAP]}\n>>>",
                    PRICE_SCHEMA,
                    temperature=0.0,
                    max_output_tokens=512,
                    tier="bulk",
                )
                new = (data.get("price") or "").strip()
            except Exception as error:  # noqa: BLE001
                log(f"   {key}: 価格を読めず（{str(error)[:60]}）")
                new = ""
            if new and price_verified(new, official):
                log(f"   {key}: 価格 {price or '（なし）'} → {new}")
                product.update(price=new, priceNote=(data.get("priceNote") or "").strip(), priceSource={"type": "official", "url": product.get("officialUrl"), "checkedAt": fetched_at})
            else:
                product.update(price="", priceSource=None)
        else:
            log(f"   {key}: 公式ページを取得できず。価格は前回のまま（確認日は更新しない）")
        kept.append(product)
    guide["products"] = kept
    keys = {p["partner"] for p in kept}
    guide["quickPick"] = [q for q in guide.get("quickPick", []) if q.get("partner") in keys]
    if spec:
        guide["shortName"] = spec.get("short", guide.get("shortName", ""))
        guide["relatedArticleSlugs"] = related_slugs(spec["related"]) or guide.get("relatedArticleSlugs", [])
    guide["priceCheckedAt"] = fetched_at
    guide["updatedAt"] = now_iso()
    return guide


def write(guide: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{guide['slug']}.json").write_text(json.dumps(guide, ensure_ascii=False, indent=2) + "\n", "utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=20, help="今回作る最大数")
    parser.add_argument("--only", help="この slug だけ（カンマ区切り可）")
    parser.add_argument("--force", action="store_true", help="既にあるガイドも作り直す")
    parser.add_argument("--refresh", action="store_true", help="価格・関連記事・更新日だけ更新（本文はそのまま）")
    parser.add_argument("--max-age", type=int, default=REFRESH_DAYS, help="--refresh で、この日数より新しいものは飛ばす（0 で全部）")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    llm._load_env_local()  # noqa: SLF001

    only = set(args.only.split(",")) if args.only else None
    specs = [s for s in GUIDES if not only or s["slug"] in only]
    if args.list:
        for s in specs:
            state = "あり" if (OUT_DIR / f"{s['slug']}.json").exists() else "未作成"
            log(f"{s['slug']:<20} {state:<4} {s['title']}  [{', '.join(p['partner'] for p in s['products'])}]")
        return 0

    if args.refresh:
        by_slug = {s["slug"]: s for s in GUIDES}
        for path in sorted(OUT_DIR.glob("*.json")):
            guide = load_json(path, None)
            if not guide or (only and guide["slug"] not in only):
                continue
            # 手で作り直したガイド（提携外の商品も含む新形式）は、価格の自動書き換え・商品の自動削除をしない
            if guide.get("editorial") == "manual" or guide.get("schemaVersion", 1) >= 2:
                log(f"\n- {guide['slug']}: 手作業で管理するガイドなので自動更新しない")
                continue
            checked = guide.get("priceCheckedAt")
            if args.max_age and checked and datetime.now(JST) - datetime.fromisoformat(checked) < timedelta(days=args.max_age):
                continue
            write(refresh(guide, by_slug.get(guide["slug"])))
        return 0

    partners = approved_partners()
    made, failed = 0, []
    for spec in specs:
        if made >= args.limit:
            break
        if (OUT_DIR / f"{spec['slug']}.json").exists() and not args.force:
            continue
        if llm.all_exhausted("quality") and llm.all_exhausted("bulk"):
            log("無料枠を使い切ったので、続きは次回")
            break
        guide = build(spec, partners)
        if guide:
            write(guide)
            made += 1
            log(f"   ✔ 書き出し content/buying-guides/{spec['slug']}.json（事実確認 {guide['factCheck']}）")
        else:
            failed.append(spec["slug"])
    log(f"\n作成 {made}本 / 見送り {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
