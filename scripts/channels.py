"""監視対象の YouTube チャンネル定義.

channel_id は実際に RSS が 200 を返すことを確認済み。
追加したいときは `python3 scripts/resolve_handle.py @Handle` で ID を引ける。
"""

from __future__ import annotations

from dataclasses import dataclass

from genres import AI, INVESTMENT, REAL_ESTATE, Genre


@dataclass(frozen=True)
class Channel:
    slug: str  # URL に使う識別子（/channels/<slug>）
    name: str  # 表示名
    channel_id: str  # YouTube チャンネルID (UC...)
    genre: Genre = AI  # 記事の書き方を決めるジャンル
    # False にすると新規生成の対象から外れる。既存記事は残したまま
    # 「今後どの分野に力を注ぐか」だけを切り替えるための仕組み。
    active: bool = True
    # そのチャンネルの記事に必ず付けるタグ
    base_tags: tuple[str, ...] = ()

    @property
    def feed_url(self) -> str:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={self.channel_id}"


CHANNELS: list[Channel] = [
    Channel(
        slug="openai",
        name="OpenAI",
        channel_id="UCXZCJLdBC09xxGZ6gcdrc6A",
        base_tags=("OpenAI", "ChatGPT"),
    ),
    Channel(
        slug="anthropic",
        name="Anthropic",
        channel_id="UCrDwWp7EBBv4NwvScIpBDOA",
        base_tags=("Anthropic", "Claude"),
    ),
    Channel(
        slug="google-deepmind",
        name="Google DeepMind",
        channel_id="UCP7jMXSY2xbc3KCAE0MHQ-A",
        base_tags=("Google DeepMind", "Gemini"),
    ),
    Channel(
        slug="google",
        name="Google",
        channel_id="UCK8sQmJBp8GCxrOtXWBpyEA",
        base_tags=("Google",),
    ),
    Channel(
        slug="google-developers",
        name="Google for Developers",
        channel_id="UC_x5XG1OV2P6uZZ5FSM9Ttw",
        base_tags=("Google", "開発者向け"),
    ),
    Channel(
        slug="microsoft-developer",
        name="Microsoft Developer",
        channel_id="UCsMica-v34Irf9KVTh6xx-g",
        base_tags=("Microsoft", "Copilot"),
    ),
    Channel(
        slug="nvidia",
        name="NVIDIA",
        channel_id="UCHuiy8bXnmK5nisYHUd1J5g",
        base_tags=("NVIDIA", "GPU"),
    ),
    Channel(
        slug="meta-ai",
        name="AI at Meta",
        channel_id="UC5qxlwEKM7-5YZudb24l0bg",
        base_tags=("Meta", "Llama"),
    ),
    Channel(
        slug="hugging-face",
        name="Hugging Face",
        channel_id="UCHlNU7kIZhRgSbhHvFoy72w",
        base_tags=("Hugging Face", "オープンソース"),
    ),
    # ---- 海外のAI専門解説チャンネル ----
    # 2026-11-03 停止（オーナー承認）: 他人の動画の要約は独自の価値が出しにくく、AdSense「有用性の低いコンテンツ」の原因になるため。
    # 既存の要約記事は取り下げ済み（content/retired.json）。scripts/indexnow.py の CREATOR_CHANNELS は active にしない（tests/test_retired_sources.py）
    Channel(
        slug="ai-explained",
        name="AI Explained",
        channel_id="UCNJ1Ymd5yFuUPtn21xtRbbw",
        base_tags=("AI解説", "論文"),
        active=False,
    ),
    Channel(
        slug="two-minute-papers",
        name="Two Minute Papers",
        channel_id="UCbfYPyITQ-7l4upoX8nvctg",
        base_tags=("AI研究", "論文"),
        active=False,
    ),
    Channel(
        slug="microsoft-research",
        name="Microsoft Research",
        channel_id="UCCb9_Kn8F_Opb3UCGm-lILQ",
        base_tags=("Microsoft", "AI研究"),
    ),
    Channel(
        slug="bycloud",
        name="bycloud",
        channel_id="UCgfe2ooZD3VJPB6aJAnuQng",
        base_tags=("AI解説", "LLM"),
        active=False,
    ),
    Channel(
        slug="matthew-berman",
        name="Matthew Berman",
        channel_id="UCawZsQWqfGSbCI5yjkdVkTA",
        base_tags=("AIツール", "検証"),
        active=False,
    ),
    # ---------------------------------------------------------------- #
    # 不動産（すべて RSS 疎通・稼働を確認済み）
    # ---------------------------------------------------------------- #
    Channel(
        slug="rakumachi",
        name="楽待 RAKUMACHI",
        channel_id="UCPMJKbrxtpARoTd1b49iUvA",
        genre=REAL_ESTATE,
        active=False,
        base_tags=("不動産投資", "収益物件"),
    ),
    Channel(
        slug="uraken-fudosan",
        name="ウラケン不動産",
        channel_id="UCfpEDJ5UHn3TlJiE1RD7X6g",
        genre=REAL_ESTATE,
        active=False,
        base_tags=("不動産投資", "住宅ローン"),
    ),
    Channel(
        slug="yukkuri-fudosan",
        name="ゆっくり不動産",
        channel_id="UCnV2ptTOZMfUEbgAs85mpRw",
        genre=REAL_ESTATE,
        active=False,
        base_tags=("物件紹介", "リノベーション"),
    ),
    Channel(
        slug="mansion-mania",
        name="マンションマニアの住まいカウンター",
        channel_id="UCdUdtHlqi01V44AEKwXzT_g",
        genre=REAL_ESTATE,
        active=False,
        base_tags=("マンション購入", "新築マンション"),
    ),
    Channel(
        slug="ienomikata",
        name="イエノミカタ",
        channel_id="UCFFYj-EIVJf5DcRRKdJutcw",
        genre=REAL_ESTATE,
        active=False,
        base_tags=("エリア相場", "住宅地"),
    ),
    # ---------------------------------------------------------------- #
    # 投資・マネー（すべて RSS 疎通・直近の投稿を確認済み）
    # ---------------------------------------------------------------- #
    Channel(
        slug="liberal-arts",
        name="両学長 リベラルアーツ大学",
        channel_id="UC67Wr_9pA4I0glIxDt_Cpyw",
        genre=INVESTMENT,
        active=False,
        base_tags=("資産形成", "家計"),
    ),
    Channel(
        slug="bank-academy",
        name="BANK ACADEMY",
        channel_id="UCGXU4lVXc9msfUkjNykA7TA",
        genre=INVESTMENT,
        active=False,
        base_tags=("新NISA", "投資信託"),
    ),
    Channel(
        slug="rakuten-sec",
        name="楽天証券",
        channel_id="UCALplCbeagOxXGmEJ-hOJIg",
        genre=INVESTMENT,
        active=False,
        base_tags=("マーケット", "株式投資"),
    ),
    # 総合ニュース系。投資と無関係な動画は topic_guard で自動的に除外される
    Channel(
        slug="nikkei",
        name="日本経済新聞",
        channel_id="UCHL12woHGeiqAqLrK-pJe7g",
        genre=INVESTMENT,
        active=False,
        base_tags=("経済", "マーケット"),
    ),
]

CHANNELS_BY_ID = {c.channel_id: c for c in CHANNELS}
