#!/usr/bin/env python3
"""公式ブログ・ニュースルーム・プレスリリースの RSS/Atom から記事候補を集める（文章ソース）。

  python3 scripts/feeds.py            # 各フィードの取得件数を表示（API 不要）
  python3 scripts/feeds.py --days 7   # 対象期間を変える

標準ライブラリだけで動く（html.parser / xml.etree）。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
TEXT_CAP = 12_000

# --------------------------------------------------------------------------- #
# フィード一覧
# --------------------------------------------------------------------------- #
# 2026-10-26 に urllib（UA 付き）で取得・XML 解析できることを確認したものだけを載せる。
#   ai_only=True … AI と無関係な発表も混ざるフィード。タイトル/概要に AI 系の語が無ければ除外
#   priority     … 小さいほど優先（同じ日の候補を並べる順）
# 本文取得の確認: blog.google / deepmind / huggingface / nvidia / microsoft は OK。
#   openai.com も Accept 系ヘッダ付きの urllib なら取得できた（curl の素の UA では 403 のことがある）。
#
# 確認して外したもの（2026-10-26）:
#   Anthropic: /news/rss.xml・/rss.xml・/feed.xml すべて 404（公式 RSS なし。YouTube 側で拾う）
#   Meta AI (ai.meta.com/blog/rss/, /feed/): 400 → 代わりに Meta Newsroom を ai_only で使う
#   Microsoft AI Blog (blogs.microsoft.com/ai/feed/): 410 Gone
#   PR TIMES テクノロジーカテゴリ (prtimes.jp/technology/index.rdf): 404 → 全体の index.rdf を ai_only で使う
#   PR TIMES キーワード検索 RSS: HTML が返る
#   Mistral / xAI / Perplexity / Sakana AI / Preferred Networks / SoftBank / NTT / LINEヤフー: RSS なし or 404/403
#   cloud.google.com AI ブログ RSS: XML でない / Gemini リリースノート: 日付だけの見出しで記事向きでない
#   重複のため不採用: openai.com/blog/rss.xml・ja-JP（news と同内容）、nvidianews（blogs.nvidia と同内容）、
#     azure.microsoft.com/blog（blogs.microsoft.com と同内容）、apple.com/newsroom 英語版（日本版を採用）、
#     japan.googleblog.com（旧 Blogger。blog.google/intl/ja-jp を採用）、blog.research.google（旧URL）
#   二次メディア（ITmedia AI+ 等）は一次情報ではないので対象外
# 確認して外したもの（2026-10-27）:
#   RSS なし・取得不可: microsoft.ai（中身なし）、Microsoft Copilot ブログ（XML 不正）、Azure ブログ（503）、Intel（403）、
#     Qualcomm・Canva・Notion ブログ・IBM Research・xAI・Perplexity・Anthropic（404）、Adobe（ニュースもブログも HTML しか返らない）、
#     PFN・SB Intuitions・rinna・ソフトバンク(プレス)・NEC・富士通(429)・日立・LINEヤフー(403)
#   ELYZA（note）: 2026-07 で更新停止 / Google Developers Japan: 月数件のイベント告知
#   量は多いが細かすぎる・薄い: AWS What's New（機能単位で6件/日）、Google Workspace Updates、GitHub Changelog 全体
#   技術者向け・解説中心で読者の価値が薄い: NVIDIA Developer Blog、Arm Newsroom、Apple Machine Learning Research、Amazon Science
#   重複: nvidianews（blogs.nvidia と同内容）、Google Cloud 英語版（日本語版を採用）、Microsoft Source Asia
#   その他: Stripe（決済の B2B）、YouTube 公式ブログ（日英とも量が少ない）
FEEDS: list[dict] = [
    # --- 海外 AI 企業の公式 ---
    {"slug": "openai-news", "name": "OpenAI公式ブログ", "url": "https://openai.com/news/rss.xml", "lang": "en", "priority": 1, "kind": "official", "primary_for": "openai"},
    {"slug": "google-ai-blog", "name": "Google公式ブログ（AI）", "url": "https://blog.google/technology/ai/rss/", "lang": "en", "priority": 1, "kind": "official", "primary_for": "google"},
    {"slug": "google-gemini-blog", "name": "Google公式ブログ（Gemini）", "url": "https://blog.google/products/gemini/rss/", "lang": "en", "priority": 1, "kind": "official", "primary_for": "google"},
    {"slug": "google-deepmind-blog", "name": "Google DeepMind公式ブログ", "url": "https://deepmind.google/blog/rss.xml", "lang": "en", "priority": 1, "kind": "official", "primary_for": "google"},
    {"slug": "google-developers-blog", "name": "Google Developers Blog", "url": "https://developers.googleblog.com/feeds/posts/default", "lang": "en", "priority": 2, "ai_only": True, "kind": "official", "primary_for": "google"},
    {"slug": "google-research-blog", "name": "Google Research公式ブログ", "url": "https://research.google/blog/rss/", "lang": "en", "priority": 3, "kind": "official"},
    {"slug": "microsoft-blog", "name": "Microsoft公式ブログ", "url": "https://blogs.microsoft.com/feed/", "lang": "en", "priority": 2, "ai_only": True, "kind": "official", "primary_for": "microsoft"},
    {"slug": "microsoft-source", "name": "Microsoft Source（ニュースルーム）", "url": "https://news.microsoft.com/source/feed/", "lang": "en", "priority": 2, "ai_only": True, "kind": "official", "primary_for": "microsoft"},
    {"slug": "microsoft-research-blog", "name": "Microsoft Research Blog", "url": "https://www.microsoft.com/en-us/research/feed/", "lang": "en", "priority": 3, "kind": "official"},
    {"slug": "nvidia-blog", "name": "NVIDIA公式ブログ", "url": "https://blogs.nvidia.com/feed/", "lang": "en", "priority": 2, "kind": "official", "primary_for": "nvidia"},
    {"slug": "meta-newsroom", "name": "Metaニュースルーム", "url": "https://about.fb.com/news/feed/", "lang": "en", "priority": 2, "ai_only": True, "kind": "official", "primary_for": "meta"},
    {"slug": "hugging-face-blog", "name": "Hugging Face公式ブログ", "url": "https://huggingface.co/blog/feed.xml", "lang": "en", "priority": 3, "kind": "official"},
    {"slug": "aws-ml-blog", "name": "AWS Machine Learning Blog", "url": "https://aws.amazon.com/blogs/machine-learning/feed/", "lang": "en", "priority": 3, "kind": "derivative"},
    {"slug": "github-changelog-copilot", "name": "GitHub Changelog（Copilot）", "url": "https://github.blog/changelog/label/copilot/feed/", "lang": "en", "priority": 2, "kind": "derivative"},
    {"slug": "github-blog", "name": "GitHub公式ブログ", "url": "https://github.blog/feed/", "lang": "en", "priority": 3, "ai_only": True, "kind": "derivative"},
    # --- 日本の一次情報 ---
    {"slug": "google-japan-blog", "name": "Google Japan Blog", "url": "https://blog.google/intl/ja-jp/rss/", "lang": "ja", "priority": 1, "ai_only": True, "kind": "official", "primary_for": "google"},
    {"slug": "microsoft-japan-news", "name": "日本マイクロソフト ニュースセンター", "url": "https://news.microsoft.com/ja-jp/feed/", "lang": "ja", "priority": 1, "ai_only": True, "kind": "official", "primary_for": "microsoft"},
    {"slug": "apple-newsroom-jp", "name": "Apple Newsroom（日本）", "url": "https://www.apple.com/jp/newsroom/rss-feed.rss", "lang": "ja", "priority": 2, "ai_only": True, "kind": "official", "primary_for": "apple"},
    {"slug": "nvidia-japan-blog", "name": "NVIDIA日本版ブログ", "url": "https://blogs.nvidia.co.jp/feed/", "lang": "ja", "priority": 2, "kind": "official", "primary_for": "nvidia"},
    {"slug": "aws-japan-blog", "name": "AWS ブログ（日本）", "url": "https://aws.amazon.com/jp/blogs/news/feed/", "lang": "ja", "priority": 3, "ai_only": True, "kind": "derivative"},
    # PR TIMES は幅広い話題の prtimes-all（同じ index.rdf）で拾う。AIの書式（2500字）では薄い記事が落ち続け、無料枠を浪費していたため外した
    # --- 2026-10-27 追加（配信を外した分を一次情報で補う）。量は直近14日の実績（ai_only の足切り前） ---
    # kind: 自社の発表は official。他社モデルの提供告知・翻訳・顧客事例が多いものは derivative（大きなニュース扱いにしない）。
    #   日本企業・海外企業のニュースルームは company（公式より一段下に並べる）
    {"slug": "sakana-ai", "name": "Sakana AI公式ブログ", "url": "https://sakana.ai/feed.xml", "lang": "ja", "priority": 1, "kind": "official", "primary_for": "sakana"},  # 約0.4件/日
    {"slug": "mistral-news", "name": "Mistral AI公式ニュース", "url": "https://mistral.ai/news/rss", "lang": "en", "priority": 1, "kind": "official", "primary_for": "mistral"},  # 約0.2件/日
    # blog.google 全体（Android・検索・YouTube などのカテゴリの AI の話題）。AI/Gemini カテゴリと同じ記事は上のフィードが先に取る
    {"slug": "google-blog-all", "name": "Google公式ブログ", "url": "https://blog.google/rss/", "lang": "en", "priority": 2, "ai_only": True, "kind": "official", "primary_for": "google"},  # 約2.6件/日
    {"slug": "about-amazon", "name": "Amazon公式ニュース（About Amazon）", "url": "https://www.aboutamazon.com/news/rss", "lang": "en", "priority": 2, "ai_only": True, "kind": "official", "primary_for": "amazon"},  # 約1.4件/日
    {"slug": "meta-engineering", "name": "Engineering at Meta", "url": "https://engineering.fb.com/feed/", "lang": "en", "priority": 3, "ai_only": True, "kind": "official", "primary_for": "meta"},  # 約0.6件/日
    {"slug": "aws-news-blog", "name": "AWS News Blog", "url": "https://aws.amazon.com/blogs/aws/feed/", "lang": "en", "priority": 3, "ai_only": True, "kind": "derivative"},  # 約0.4件/日
    {"slug": "google-cloud-blog-ja", "name": "Google Cloud 公式ブログ（日本）", "url": "https://cloudblog.withgoogle.com/ja/rss/", "lang": "ja", "priority": 3, "ai_only": True, "kind": "derivative"},  # 約2.4件/日
    {"slug": "windows-blog-jp", "name": "Windows ブログ（日本）", "url": "https://blogs.windows.com/japan/feed/", "lang": "ja", "priority": 2, "ai_only": True, "kind": "derivative"},  # 約1.6件/日
    {"slug": "ntt-news-ai", "name": "NTT ニュースリリース", "url": "https://group.ntt/jp/newsrelease/rss/release.rdf", "lang": "ja", "priority": 2, "ai_only": True, "kind": "company"},  # 営業日に約10件（当日分だけ載る）
    {"slug": "kddi-news-ai", "name": "KDDI ニュースリリース", "url": "https://newsroom.kddi.com/news/newsrelease.xml", "lang": "ja", "priority": 2, "ai_only": True, "kind": "company"},  # 約0.8件/日
    {"slug": "kddi-topics-ai", "name": "KDDI トピックス", "url": "https://newsroom.kddi.com/news/topics.xml", "lang": "ja", "priority": 3, "ai_only": True, "kind": "company"},  # 約0.6件/日
    {"slug": "salesforce-jp", "name": "Salesforce ニュース（日本）", "url": "https://www.salesforce.com/jp/news/feed/", "lang": "ja", "priority": 3, "ai_only": True, "kind": "company"},  # 約0.7件/日
    {"slug": "ibm-newsroom-jp", "name": "日本IBM ニュースルーム", "url": "https://jp.newsroom.ibm.com/announcements?pagetemplate=rss", "lang": "ja", "priority": 3, "ai_only": True, "kind": "company"},  # 約0.4件/日
    {"slug": "amd-newsroom", "name": "AMD ニュースルーム", "url": "https://newsroom.amd.com/rss.xml", "lang": "en", "priority": 3, "ai_only": True, "kind": "company"},  # 約0.3件/日
    {"slug": "cloudflare-blog", "name": "Cloudflare公式ブログ", "url": "https://blog.cloudflare.com/rss/", "lang": "en", "priority": 3, "ai_only": True, "kind": "company"},  # 約1.5件/日
    {"slug": "figma-blog", "name": "Figma公式ブログ", "url": "https://www.figma.com/blog/feed/atom.xml", "lang": "en", "priority": 3, "ai_only": True, "kind": "company"},  # 約0.6件/日
    # 速報の監視元にも登録済み。通常記事はAI関連のみ拾い、従来の重複・事実確認・品質ゲートを通す。
    {"slug": "atlassian-blog", "name": "Atlassian公式ブログ", "url": "https://www.atlassian.com/blog/rss", "lang": "en", "priority": 3, "ai_only": True, "kind": "company"},
    {"slug": "docker-blog", "name": "Docker公式ブログ", "url": "https://www.docker.com/feed/", "lang": "en", "priority": 3, "ai_only": True, "kind": "company"},
    # --- 2026-11-02 追加（候補不足の対策。robots.txt 許可・直近14日に更新ありを確認。数字は14日のAI関連件数） ---
    {"slug": "nvidia-developer-blog", "name": "NVIDIA Developer Blog", "url": "https://developer.nvidia.com/blog/feed", "lang": "en", "priority": 3, "ai_only": True, "kind": "company"},  # 22。技術解説が多いので大きなニュース扱いにしない
    {"slug": "softbank-news-ai", "name": "ソフトバンクニュース", "url": "https://www.softbank.jp/sbnews/feed", "lang": "ja", "priority": 2, "ai_only": True, "kind": "company"},  # 8
    {"slug": "docomo-news-release-ai", "name": "NTTドコモ 報道発表", "url": "https://www.docomo.ne.jp/info/rss/news_release.rdf", "lang": "ja", "priority": 2, "ai_only": True, "kind": "company"},  # 5
    {"slug": "cyberagent-developers", "name": "CyberAgent Developers Blog", "url": "https://developers.cyberagent.co.jp/blog/feed/", "lang": "ja", "priority": 3, "ai_only": True, "kind": "company"},  # 3
    {"slug": "moneyforward-news", "name": "マネーフォワード ニュース", "url": "https://corp.moneyforward.com/feed/", "lang": "ja", "priority": 3, "ai_only": True, "kind": "company"},  # 3
    {"slug": "brainpad-news", "name": "ブレインパッド ニュース", "url": "https://www.brainpad.co.jp/news/feed/", "lang": "ja", "priority": 3, "ai_only": True, "kind": "company"},  # 5
    {"slug": "redhat-blog", "name": "Red Hat公式ブログ", "url": "https://www.redhat.com/en/rss/blog", "lang": "en", "priority": 3, "ai_only": True, "kind": "company"},  # 8
    {"slug": "vercel-changelog", "name": "Vercel Changelog", "url": "https://vercel.com/changelog/rss.xml", "lang": "en", "priority": 3, "ai_only": True, "kind": "company"},  # 18
]

# 速報レーンの発信元一覧（sources_registry.json）の RSS も通常記事の候補に使う（一覧を1か所にまとめる）。
#   genre 付き（決算など）は幅広い話題側で拾う。"feeds": false は通常記事に使わない（細かすぎる・重複など）
REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources_registry.json")


def _registry_feeds(have: list[dict]) -> list[dict]:
    try:
        with open(REGISTRY_PATH, encoding="utf-8") as fh:
            sources = json.load(fh).get("sources", [])
    except (OSError, ValueError):
        return []
    urls = {f["url"].rstrip("/") for f in have}
    slugs = {f["slug"] for f in have}
    out = []
    for s in sources:
        if s.get("type") != "rss" or s.get("genre") or s.get("feeds") is False:
            continue
        if s["url"].rstrip("/") in urls or s["slug"] in slugs:
            continue
        out.append({"slug": s["slug"], "name": s["name"], "url": s["url"], "lang": s.get("lang", "en"),
                    "priority": 3, "ai_only": True, "kind": "company", "from_registry": True})
    return out


FEEDS.extend(_registry_feeds(FEEDS))
FEEDS_BY_SLUG = {f["slug"]: f for f in FEEDS}

# --------------------------------------------------------------------------- #
# 幅広い話題のフィード（web_articles.py --section broad）
# --------------------------------------------------------------------------- #
# section … genres.py の WEB_GENRES の slug（幅広い話題＋投資・マネー／不動産）。"auto" は classify_section() でタイトル/概要から振り分ける。
#   どのセクションでも、投資・不動産・エンタメのビジネスの話題は genre_focus.route_section がそのレーンへ付け替える（2026-11-03）
# kind    … wire（配信サービス・量が多い）/ gov（官公庁・公的機関）/ company（企業）/ science（研究機関）
# 2026-10-26 に urllib（UA 付き）で取得・解析できたものだけを載せる。量は直近の配信間隔からの概算（件/日）。
#   PR TIMES: 新着200件 ≒ 約4時間分 → 約1000件/日（毎時取得で取りこぼしほぼ無し）。本文は記事ページから取得できる
#   @Press 約23件/日、ValuePress 約14件/日、共同通信PRワイヤー 約50件/日、ドリームニューズ 約80件/日
#   官公庁は合計で約50件/日（ただし会議案内・採用など足切り対象が多い）。研究機関 約3件/日、企業 約8件/日
# 確認して外したもの（2026-10-26）:
#   PR TIMES のカテゴリ別 RSS（/technology/・/business/・/entertainment/ 等の index.rdf）: すべて 404。
#     公式にあるのは全体の index.rdf と企業別 companyrdf.php のみ → 全体を取り、classify_section で振り分ける
#   Digital PR Platform・News2u: RSS なし / ホスト不明
#   経産省 ml_index_release_atom.xml: 取得はできるが最新が 2026-06 で更新停止
#   国交省 index.rdf: 大臣会見要旨ばかりで記事向きでない / 気象庁・国民生活センター・特許庁(403)・環境省・財務省・国税庁: RSS なし
#   e-Gov パブコメ・JNTO・JVN・JST 公募: 読者向けの価値が薄い（専門的・B2B）
#   JOC ニュース: 通信社の配信記事が中心のため不採用（報道の全文は扱わない）
#   ソニー・任天堂IR・ドコモ・ソフトバンク(プレス)・LINEヤフー・パナソニック・日立・富士通・NEC・ホンダ・日産・
#     JR東日本・イオン・セブン・スクエニ・バンダイナムコ・セガ・Jリーグ・NPB・Bリーグ: RSS なし（大半は PR TIMES 経由で拾える）
#   シャープ news/rss.xml: 日付が入っていない / 日本マイクロソフト: 2025-09 で更新停止
#   NHK・新聞・Yahoo!ニュースなど報道機関の本文は対象外
# 確認して外したもの（2026-10-27）:
#   パナソニック（RSS はあるが記事ページから本文が取れず、関連記事の一覧を拾ってしまう）、イオン（本文がメニューしか取れない）
#   吉野家・ダイソー（開店・キャンペーン中心）、日本郵便（年賀はがき・局の移転など薄い告知）、JPX（本文が短く投資家向け）
#   NIMS（求人・イベント中心）、産総研 rss.php（日付がイベント日）、帝国データバンク（個別企業の倒産速報のみ）
#   JST サイエンスポータル（2026-08 で更新停止）、JETRO（企業向け）、警察庁（事件・統計中心）、松竹（日付が上映日）
#   Netflix 日本（日付なし）、NASA（英語で1日10件超・特集が中心。必要なら science に priority 3 で）
#   RSS なし: 公取委・個人情報保護委・国家サイバー統括室(NISC)・NITE・こども家庭庁・文化庁・観光庁・外務省・年金機構・
#     JAMSTEC・国環研・気象庁・東大/京大/東北大/阪大/名大などの大学（広島大は研究成果だけ採用）、
#     キヤノン・ニコン(量少)・富士フイルム・カシオ・メルカリ・ZOZO・ローソン・ファミマ・マクドナルド・ANA・JAL(403)・JR東海・
#     ホンダ・マツダ・スバル・ニトリ・味の素・日清・カルビー(403)・カゴメ・キユーピー・ロッテ・アサヒ・サッポロ・伊藤園(更新停止)、
#     ラグビー・バスケ・陸連・バレー・ゴルフ・相撲・スポーツ庁・Jリーグ・NPB・Bリーグ
#   include_re / exclude_re … タイトルがこの正規表現に合う（合わない）ものだけ取る
#   section "auto" + fallback … classify_section で決まらないときのセクション（企業の雑多なニュース向け）
BROAD_FEEDS: list[dict] = [
    # プレスリリース配信（PR TIMES・@Press・ValuePress・共同通信PRワイヤー・ドリームニューズ）は 2026-10-27 に外した。
    # 誰でも出せる告知文の言い換えは薄く、ニュースアプリや Google の評価を下げるため（オーナー判断）。
    # --- 官公庁・公的機関 ---
    {"slug": "kantei", "name": "首相官邸", "url": "https://www.kantei.go.jp/index-jnews.rdf", "section": "public", "kind": "gov", "priority": 3},
    {"slug": "gov-online", "name": "政府広報オンライン", "url": "https://www.gov-online.go.jp/rss/index.rdf", "section": "public", "kind": "gov", "priority": 2},
    {"slug": "digital-agency", "name": "デジタル庁", "url": "https://www.digital.go.jp/rss/news.xml", "section": "public", "kind": "gov", "priority": 2},
    {"slug": "soumu", "name": "総務省", "url": "https://www.soumu.go.jp/news.rdf", "section": "public", "kind": "gov", "priority": 3},
    {"slug": "mhlw", "name": "厚生労働省", "url": "https://www.mhlw.go.jp/stf/news.rdf", "section": "public", "kind": "gov", "priority": 3},
    {"slug": "mext", "name": "文部科学省", "url": "https://www.mext.go.jp/b_menu/news/index.rdf", "section": "public", "kind": "gov", "priority": 3},
    {"slug": "fsa", "name": "金融庁", "url": "https://www.fsa.go.jp/fsaNewsListAll_rss2.xml", "section": "public", "kind": "gov", "priority": 2},
    {"slug": "caa", "name": "消費者庁", "url": "https://www.caa.go.jp/news.rss", "section": "lifestyle", "kind": "gov", "priority": 2},
    {"slug": "maff", "name": "農林水産省", "url": "https://www.maff.go.jp/j/press/rss.xml", "section": "lifestyle", "kind": "gov", "priority": 3},
    {"slug": "cao", "name": "内閣府", "url": "https://www.cao.go.jp/rss/news.rdf", "section": "business", "kind": "gov", "priority": 3},
    {"slug": "boj", "name": "日本銀行", "url": "https://www.boj.or.jp/rss/whatsnew.xml", "section": "business", "kind": "gov", "priority": 2},
    {"slug": "stat", "name": "総務省統計局", "url": "https://www.stat.go.jp/whatsnew/news.rdf", "section": "business", "kind": "gov", "priority": 3},
    {"slug": "jpcert", "name": "JPCERT/CC", "url": "https://www.jpcert.or.jp/rss/jpcert.rdf", "section": "tech-gadget", "kind": "gov", "priority": 3},
    {"slug": "ipa", "name": "IPA（情報処理推進機構）", "url": "https://www.ipa.go.jp/about/newsonly-rss.rdf", "section": "tech-gadget", "kind": "gov", "priority": 3},
    # 東京都の報道発表（約4件/日。イベント・募集・審議会は LOW_VALUE_RE で落ちる）
    {"slug": "tokyo-metro", "name": "東京都", "url": "https://www.metro.tokyo.lg.jp/index.html/-/asset_publisher/knkg/rss", "section": "public", "kind": "gov", "priority": 3},
    # --- 研究機関・宇宙 ---
    {"slug": "jaxa", "name": "JAXA", "url": "https://www.jaxa.jp/rss/press_j.rdf", "section": "science", "kind": "science", "priority": 1},
    {"slug": "riken", "name": "理化学研究所", "url": "https://www.riken.jp/feed/press_feed/", "section": "science", "kind": "science", "priority": 1},
    {"slug": "naoj", "name": "国立天文台", "url": "https://www.nao.ac.jp/atom.xml", "section": "science", "kind": "science", "priority": 1},
    {"slug": "jst", "name": "科学技術振興機構（JST）", "url": "https://www.jst.go.jp/rss/press.xml", "section": "science", "kind": "science", "priority": 2},
    {"slug": "nict", "name": "情報通信研究機構（NICT）", "url": "https://www.nict.go.jp/rss/press.rdf", "section": "science", "kind": "science", "priority": 2},
    {"slug": "nii", "name": "国立情報学研究所（NII）", "url": "https://www.nii.ac.jp/news/rss.xml", "section": "science", "kind": "science", "priority": 2},  # 約0.6件/日
    {"slug": "qst", "name": "量子科学技術研究開発機構（QST）", "url": "https://www.qst.go.jp/rss/10/list7.xml", "section": "science", "kind": "science", "priority": 2},  # 約0.4件/日
    {"slug": "hiroshima-u", "name": "広島大学", "url": "https://www.hiroshima-u.ac.jp/feed/news", "section": "science", "kind": "science", "priority": 3, "include_re": "研究成果"},  # 研究成果は約0.5件/日
    # --- 企業ニュースルーム ---
    {"slug": "toyota", "name": "トヨタ自動車 グローバルニュースルーム", "url": "https://global.toyota/export/jp/allnews_rss.xml", "section": "business", "kind": "company", "priority": 2},
    {"slug": "rakuten", "name": "楽天グループ プレスリリース", "url": "https://corp.rakuten.co.jp/rss/press.xml", "section": "business", "kind": "company", "priority": 2},
    {"slug": "uniqlo", "name": "ユニクロ プレスリリース", "url": "https://www.uniqlo.com/jp/ja/contents/corp/press-release/index.xml", "section": "lifestyle", "kind": "company", "priority": 2},
    {"slug": "softbank-news", "name": "ソフトバンクニュース", "url": "https://www.softbank.jp/sbnews/feed", "section": "tech-gadget", "kind": "company", "priority": 3},
    {"slug": "au-topics", "name": "au トピックス", "url": "https://www.au.com/rss/topic.xml", "section": "tech-gadget", "kind": "company", "priority": 2},
    {"slug": "sony-jp", "name": "ソニー（製品ニュース）", "url": "https://www.sony.jp/xml/index.xml", "section": "tech-gadget", "kind": "company", "priority": 2},
    {"slug": "apple-newsroom-jp-all", "name": "Apple Newsroom（日本）", "url": "https://www.apple.com/jp/newsroom/rss-feed.rss", "section": "tech-gadget", "kind": "company", "priority": 1, "primary_for": "apple"},
    {"slug": "google-japan-blog-all", "name": "Google Japan Blog", "url": "https://blog.google/intl/ja-jp/rss/", "section": "tech-gadget", "kind": "company", "priority": 1, "primary_for": "google"},
    {"slug": "nintendo-topics", "name": "任天堂 トピックス", "url": "https://www.nintendo.com/jp/topics/c/feed/rss/", "section": "entertainment", "kind": "company", "priority": 1},
    {"slug": "playstation-blog-jp", "name": "PlayStation.Blog 日本語", "url": "https://blog.ja.playstation.com/feed/", "section": "entertainment", "kind": "company", "priority": 2},
    # --- 2026-10-27 追加の企業ニュースルーム（量は直近14日の実績） ---
    # 通信・家電・ガジェット（AI の話題は FEEDS の *-ai が先に取り、ここには残りが来る）
    {"slug": "ntt-news", "name": "NTT ニュースリリース", "url": "https://group.ntt/jp/newsrelease/rss/release.rdf", "section": "auto", "fallback": "business", "kind": "company", "priority": 2},  # 営業日に約10件
    {"slug": "kddi-news", "name": "KDDI ニュースリリース", "url": "https://newsroom.kddi.com/news/newsrelease.xml", "section": "tech-gadget", "kind": "company", "priority": 2},  # 約0.8件/日
    {"slug": "kddi-topics", "name": "KDDI トピックス", "url": "https://newsroom.kddi.com/news/topics.xml", "section": "tech-gadget", "kind": "company", "priority": 3},  # 約0.6件/日
    # ドコモは新機種・新サービス・料金の分類だけ（お知らせ・OS 更新は外す）。量は少ないが読者の関心が高い
    {"slug": "docomo-product", "name": "NTTドコモ（新製品）", "url": "https://www.docomo.ne.jp/info/rss/product.rdf", "section": "tech-gadget", "kind": "company", "priority": 1},
    {"slug": "docomo-service", "name": "NTTドコモ（サービス）", "url": "https://www.docomo.ne.jp/info/rss/service.rdf", "section": "tech-gadget", "kind": "company", "priority": 1},
    {"slug": "docomo-charge", "name": "NTTドコモ（料金）", "url": "https://www.docomo.ne.jp/info/rss/charge.rdf", "section": "tech-gadget", "kind": "company", "priority": 1},
    {"slug": "samsung-jp", "name": "Samsung Newsroom 日本", "url": "https://news.samsung.com/jp/feed", "section": "tech-gadget", "kind": "company", "priority": 2},  # 約0.6件/日
    {"slug": "about-amazon-jp", "name": "Amazon ニュース（日本）", "url": "https://www.aboutamazon.jp/news/rss", "section": "auto", "fallback": "business", "kind": "company", "priority": 2},  # 約0.3件/日
    {"slug": "nissan", "name": "日産自動車 ニュースルーム", "url": "https://global.nissannews.com/ja-JP/rss", "section": "auto", "fallback": "business", "kind": "company", "priority": 2},  # 約0.4件/日
    # 食品・飲料（新商品は価格・発売日が必ず入る）
    {"slug": "suntory", "name": "サントリー ニュースリリース", "url": "https://www.suntory.co.jp/news/rss.xml", "section": "lifestyle", "kind": "company", "priority": 2},  # 約1.3件/日
    {"slug": "kirin", "name": "キリンホールディングス ニュースリリース", "url": "https://www.kirinholdings.com/jp/newsroom/rss.php", "section": "lifestyle", "kind": "company", "priority": 2},  # 約0.9件/日
    {"slug": "meiji", "name": "明治 ニュースリリース", "url": "https://www.meiji.co.jp/news/meiji_press.xml", "section": "lifestyle", "kind": "company", "priority": 2},  # 約1.0件/日
    {"slug": "morinaga", "name": "森永製菓 ニュースリリース", "url": "https://www.morinaga.co.jp/company/rss/index.rdf", "section": "lifestyle", "kind": "company", "priority": 2},  # 約0.4件/日
    # エンタメ
    {"slug": "xbox-wire-jp", "name": "Xbox Wire Japan", "url": "https://news.xbox.com/ja-jp/feed/", "section": "entertainment", "kind": "company", "priority": 2},  # 約0.4件/日
    {"slug": "cyberagent", "name": "サイバーエージェント ニュース", "url": "https://www.cyberagent.co.jp/news/rss/data_format=xml", "section": "auto", "fallback": "entertainment", "kind": "company", "priority": 3},  # 約0.7件/日（ABEMA など）
    # 調査データ（東京商工リサーチの自社データの分析）。個別企業の倒産・解散の速報は扱わない
    {"slug": "tsr", "name": "東京商工リサーチ", "url": "https://www.tsr-net.co.jp/rss/news_analysis.xml", "section": "business", "kind": "company", "priority": 2,  # 約1.6件/日
     "exclude_re": r"(?:（[^）]{1,8}）|株式会社|（株）|有限会社|一般社団法人|医療法人|学校法人).{0,40}(?:破産|倒産|解散|民事再生|特別清算|事業停止|負債)"},
    # ニッセイ基礎研究所のレポート・コラム（経済・家計・年金・不動産。全文がフィードに入る）。週のまとめは外す
    {"slug": "nli-research", "name": "ニッセイ基礎研究所", "url": "https://www.nli-research.co.jp/rss/?data_format=xml&site=nli", "section": "business", "kind": "company", "priority": 3,  # 約2.4件/日
     "exclude_re": r"まとめ【"},
    # --- スポーツ（競技団体の公式） ---
    {"slug": "jfa", "name": "日本サッカー協会（JFA）", "url": "https://www.jfa.jp/feed.rss", "section": "sports", "kind": "company", "priority": 2},
    {"slug": "jtta", "name": "日本卓球協会", "url": "https://www.jtta.or.jp/feed/", "section": "sports", "kind": "company", "priority": 2},  # 約0.9件/日
    # --- 2026-11-02 追加（お金・仕事・不動産の一次情報。robots.txt 許可・当日の更新・日付ありを確認） ---
    {"slug": "mof", "name": "財務省", "url": "https://www.mof.go.jp/news.rss", "section": "business", "kind": "gov", "priority": 2,
     "exclude_re": "入札|発行予定|発行額|落札|国庫短期証券|利付国債|応札"},  # 約7件/日（国債の入札結果を除く）
    {"slug": "mlit-press", "name": "国土交通省 報道発表", "url": "https://www.mlit.go.jp/pressrelease.rdf", "section": "business", "kind": "gov", "priority": 2},  # 約7件/日（Shift_JIS）。不動産・住宅・交通の政策
    {"slug": "jnet21", "name": "J-Net21（中小機構）", "url": "https://j-net21.smrj.go.jp/news/info.xml", "section": "business", "kind": "gov", "priority": 3},  # 約3件/日
    # 不動産の会社は 2026-11-03 から不動産のレーンへ（scripts/genre_focus.py。店舗の開業・イベントは足切り）
    {"slug": "sumitomo-rd", "name": "住友不動産", "url": "https://www.sumitomo-rd.co.jp/news/feed/", "section": "real-estate", "kind": "company", "priority": 3},
    {"slug": "nomura-re", "name": "野村不動産ホールディングス", "url": "https://www.nomura-re-hd.co.jp/news/chtml/rss.xml", "section": "real-estate", "kind": "company", "priority": 2},
    # --- 2026-11-03 追加（日本の昼に出る候補の補強。robots.txt 許可・日付あり・記事ページの本文が3,000字前後取れることを確認） ---
    # キャッシュレス決済のキャンペーン・手数料・新機能は会社員の家計に直結する。人事・役員のお知らせは除く。
    # 同じ日に確かめたが使えなかったもの: 経産省（Atom は 2026-06 で更新停止・ほかは 404/403）、国税庁・中小企業庁・年金機構・
    #   特許庁・公取委・個人情報保護委・国民生活センター（RSS なし・404・403）、NEC・日立・富士通・メルカリ・freee・楽天モバイル・
    #   三井住友・みずほ・マネックス・LINEヤフー（RSS なし or 403）、Spotify（robots.txt が禁止）
    {"slug": "paypay", "name": "PayPay プレスリリース", "url": "https://about.paypay.ne.jp/feed/", "section": "business", "kind": "company", "priority": 2,
     "exclude_re": "人事異動|役員|組織変更|代表取締役"},  # 約0.4件/日（10〜11時・20時ごろ）
    # --- 2026-11-03 追加（投資・マネー／不動産／エンタメを質の良い記事で埋める。オーナー決定。扱う中身は scripts/genre_focus.py） ---
    # robots.txt 許可・XML が読める・直近14日に更新あり・記事ページの本文が取れる（PDF だけの発表は読み取りサービスで読む）ことを確かめた。
    # 混ざったフィード（金融庁・日本銀行・財務省・国交省・ニッセイ基礎研究所・企業）の投資・不動産の話題は genre_focus.route_section がレーンを付け替える。
    # 同じ日に確かめたが使えなかったもの: 日本証券業協会（429）、投資信託協会・住宅金融支援機構・スクウェア・エニックス・バンダイナムコ（RSS の URL が HTML）、
    #   楽天証券・SBI 証券・マネックス(403)・野村HD・大和・三菱UFJ・三井住友・みずほ(403)・三井不動産・三菱地所・積水ハウス・大和ハウス(403)・
    #   任天堂IR・ソニー(403)・コナミ・東宝・東映アニメ・U-NEXT・KADOKAWA（RSS なし）、REINS（robots.txt が禁止）、
    #   Netflix 日本（日付なし・作品の告知が中心）、JPX のマーケットニュース（上場・売買停止などのお知らせばかり）
    # 投資・マネー
    {"slug": "jpx-news", "name": "日本取引所グループ（JPX）", "url": "https://www.jpx.co.jp/rss/jpx-news.xml", "section": "investment", "kind": "gov", "priority": 2},  # 約0.8件/日（売買状況などの月次の統計は足切り）
    {"slug": "jp-bank", "name": "ゆうちょ銀行", "url": "https://www.jp-bank.japanpost.jp/rss/news.xml", "section": "investment", "kind": "company", "priority": 3},  # 約0.2件/日
    {"slug": "resona", "name": "りそなホールディングス", "url": "https://www.resona-gr.co.jp/holdings/news/newsrelease_c/rss.xml", "section": "investment", "kind": "company", "priority": 3},  # 約0.2件/日
    {"slug": "gpif", "name": "年金積立金管理運用独立行政法人（GPIF）", "url": "https://www.gpif.go.jp/rss.xml", "section": "investment", "kind": "gov", "priority": 2,
     "include_re": "運用状況|運用実績|収益|ポートフォリオ|業務概況"},  # 四半期の運用状況だけ（動画・催しの案内は外す）
    # 不動産
    {"slug": "tokyu-fudosan", "name": "東急不動産ホールディングス", "url": "https://www.tokyu-fudosan-hd.co.jp/news/companies/rss", "section": "real-estate", "kind": "company", "priority": 3},  # 約0.8件/日（グループ各社）
    {"slug": "lifull", "name": "LIFULL", "url": "https://lifull.com/news/feed/", "section": "real-estate", "kind": "company", "priority": 3},  # 約0.3件/日（不動産テック・住まいのデータ）
    # エンタメ（ビジネスの目線: 決算・販売本数・産業の数字）
    {"slug": "capcom-ir", "name": "カプコン IRニュース", "url": "https://www.capcom.co.jp/ir/news/feed", "section": "entertainment", "kind": "company", "priority": 2},  # 約0.4件/日
    {"slug": "aja", "name": "日本動画協会", "url": "https://aja.gr.jp/feed", "section": "entertainment", "kind": "company", "priority": 3},  # 月数件（アニメ産業レポートなど）
]
for _feed in BROAD_FEEDS:
    _feed.setdefault("lang", "ja")
FEEDS_BY_SLUG.update({f["slug"]: f for f in BROAD_FEEDS})

# プレスリリース配信サービス（scripts/indexnow.py の WIRE_SOURCES）からは記事を作らない（2026-10-27 に外し、2026-11-03 に
# 既存の言い換え記事を取り下げた）。誤って足されても取り込まないよう、ここで必ず外す（tests/test_retired_sources.py）
WIRE_FEED_SLUGS = {"prtimes", "prtimes-all", "atpress", "value-press", "kyodo-prwire", "dreamnews"}
FEEDS[:] = [f for f in FEEDS if f["slug"] not in WIRE_FEED_SLUGS]
BROAD_FEEDS[:] = [f for f in BROAD_FEEDS if f["slug"] not in WIRE_FEED_SLUGS]
for _slug in WIRE_FEED_SLUGS:
    FEEDS_BY_SLUG.pop(_slug, None)

# 一次情報の順位付け。kind: official（企業の公式ブログ）/ derivative（他社製品の対応・提供の告知）/ wire（配信）
#   primary_for … その企業の発表の「本家」。同じ話題が複数のフィードに来たら本家を残す
ENTITY_RES: dict[str, re.Pattern] = {
    "openai": re.compile(r"OpenAI|ChatGPT|GPT[-‐ ]?\d|\bo\d(?:-mini|-pro)?\b|\bSora\b|\bCodex\b|DALL", re.I),
    "anthropic": re.compile(r"Anthropic|\bClaude\b", re.I),
    "google": re.compile(r"Google|Gemini|Gemma|DeepMind|\bVeo\b|Imagen|\bPixel\b|NotebookLM", re.I),
    "microsoft": re.compile(r"Microsoft|マイクロソフト|\bPhi-\d|\bCopilot\b", re.I),
    "nvidia": re.compile(r"NVIDIA|エヌビディア|GeForce|\bRTX\b|Blackwell|Rubin", re.I),
    "apple": re.compile(r"\bApple\b|アップル|iPhone|iPad|\bMac\b|Apple Intelligence|Siri", re.I),
    "meta": re.compile(r"\bMeta\b|Llama|Instagram|WhatsApp|Facebook", re.I),
    "xai": re.compile(r"\bxAI\b|\bGrok\b", re.I),
    "amazon": re.compile(r"Amazon|アマゾン|\bAWS\b|Alexa", re.I),
    "mistral": re.compile(r"Mistral|Voxtral|Codestral|Magistral|Devstral|Pixtral", re.I),
    "sakana": re.compile(r"Sakana|サカナ", re.I),
}
KIND_RANK = {"official": 1, "company": 2, "gov": 2, "science": 2, "": 2, "derivative": 3, "wire": 4}


def entities_in(text: str) -> set[str]:
    return {name for name, pattern in ENTITY_RES.items() if pattern.search(text or "")}


def source_rank(item: dict) -> int:
    """小さいほど一次情報。0=その企業の本家（OpenAI の話題を openai-news が出した等）"""
    feed = FEEDS_BY_SLUG.get(item.get("source_slug", ""), {})
    owner = feed.get("primary_for")
    if owner and owner in entities_in(item.get("title", "")):
        return 0
    return KIND_RANK.get(feed.get("kind", ""), 2)


def feed_kind(item: dict) -> str:
    return FEEDS_BY_SLUG.get(item.get("source_slug", ""), {}).get("kind", "")

# section="auto" のフィード（企業の雑多なニュースなど）の振り分け。当たった語の数が多いセクションに入れる（同点は並び順）。
SECTION_RULES: list[tuple[str, re.Pattern]] = [
    ("sports", re.compile(
        r"サッカー|野球|バスケ|Jリーグ|Bリーグ|ラグビー|ゴルフ|テニス|卓球|バレーボール|陸上競技|マラソン|駅伝|"
        r"五輪|オリンピック|パラリンピック|ワールドカップ|日本代表|選手権|スポーツ|競技|アスリート|フィットネス|ランニング|"
        r"モータースポーツ|フォーミュラ|SUPER GT|(?<![A-Za-z])F1(?![A-Za-z])|(?<![A-Za-z])WEC(?![A-Za-z])|耐久レース")),
    ("entertainment", re.compile(
        r"アニメ|映画|ドラマ|漫画|マンガ|コミック|小説|書籍|新刊|ゲーム|Nintendo|Switch|PS5|プレイステーション|"
        r"音楽|楽曲|アルバム|ライブ|コンサート|ツアー|舞台|ミュージカル|展覧会|美術館|博物館|展示|声優|VTuber|配信開始|"
        r"劇場|主題歌|MV|コラボ")),
    ("science", re.compile(
        r"研究成果|研究グループ|研究チーム|共同研究|解明|発見|論文|宇宙|ロケット|衛星|天文|探査|量子|遺伝子|細胞|"
        r"化石|恐竜|実験に成功|実証に成功")),
    ("tech-gadget", re.compile(
        r"スマホ|スマートフォン|iPhone|Android|アプリ|ガジェット|パソコン|PC|ノートPC|タブレット|イヤホン|ヘッドホン|"
        r"スピーカー|カメラ|ゲーミング|ディスプレイ|モニター|家電|充電|モバイルバッテリー|Wi-Fi|5G|通信|SIM|"
        r"セキュリティ|脆弱性|クラウド|SaaS|ソフトウェア|半導体|ドローン|EV|ロボット|IoT|DX|システム", re.IGNORECASE)),
    ("lifestyle", re.compile(
        r"食品|飲料|グルメ|スイーツ|レシピ|お菓子|パン|ラーメン|カフェ|ビール|日本酒|ワイン|旅行|ホテル|旅館|観光|"
        r"子育て|育児|ベビー|キッズ|子ども|介護|シニア|美容|コスメ|スキンケア|ファッション|アパレル|インテリア|家具|"
        r"住まい|住宅|ペット|犬|猫|家計|節約|防災|日用品|雑貨|ふるさと納税|新商品|新発売")),
    ("public", re.compile(
        r"自治体|市役所|県庁|区役所|行政|制度|補助金|助成|給付|マイナンバー|マイナ|税制|年金|保険証|官民|公共")),
    ("business", re.compile(
        r"業務提携|資本提携|提携|買収|M&A|上場|決算|業績|売上|新会社|設立|事業|法人向け|BtoB|物流|人手不足|"
        r"中小企業|価格改定|値上げ|調査|市場|資金|経営|サプライチェーン|脱炭素|GX")),
]


def classify_section(item: dict) -> str | None:
    """タイトル（重み2）と概要（重み1）の語の当たり数で、幅広い話題のセクションを決める。当たらなければ None"""
    scores: dict[str, int] = {}
    for section, pattern in SECTION_RULES:
        score = 2 * len(pattern.findall(item.get("title", ""))) + len(pattern.findall(item.get("summary", "")))
        if score:
            scores[section] = score
    if not scores:
        return None
    order = [s for s, _ in SECTION_RULES]
    return max(scores, key=lambda s: (scores[s], -order.index(s)))
# 公式フィードでも SNS 投稿へのリンクが混ざる（Microsoft Source → LinkedIn など）。一次記事でないので除外
BLOCKED_HOSTS = ("linkedin.com", "x.com", "twitter.com", "facebook.com", "instagram.com", "youtube.com", "youtu.be")

# AI 関連かどうかのざっくり判定（ai_only のフィードに使う）
AI_RE = re.compile(
    r"(?<![A-Za-z])(AI|A\.I\.|LLMs?|GPT[-\w]*|Gemini|Gemma|Copilot|Claude|ChatGPT|agents?|agentic|"
    r"large language models?|machine learning|deep learning|neural|generative|model|inference|Llama|NVIDIA|GPU|robot\w*)(?![A-Za-z])"
    r"|人工知能|生成AI|機械学習|深層学習|大規模言語|言語モデル|エージェント|チャットボット|ロボット|推論",
    re.IGNORECASE,
)

ATOM = "{http://www.w3.org/2005/Atom}"
RSS1 = "{http://purl.org/rss/1.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
MEDIA = "{http://search.yahoo.com/mrss/}"


def log(message: str) -> None:
    print(message, flush=True)


# --------------------------------------------------------------------------- #
# 取得
# --------------------------------------------------------------------------- #


def fetch(url: str, timeout: int = 25, accept: str = "*/*", token: str = "", extra_headers: dict | None = None) -> tuple[bytes, str]:
    """(本文バイト列, 最終URL)。gzip 圧縮のまま返すサーバー（deepmind.google 等）にも対応。
    token は読み取りサービス（r.jina.ai）の鍵（JINA_API_KEY。あれば回数制限にかかりにくい）。
    extra_headers は読み取りサービスへの指定など（例: {"X-Return-Format": "html"}）"""
    headers = {"User-Agent": UA, "Accept": accept, "Accept-Encoding": "gzip, deflate", "Accept-Language": "ja,en;q=0.8", **(extra_headers or {})}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(5_000_000)
        encoding = (response.headers.get("Content-Encoding") or "").lower()
        final_url = response.geturl()
    if data[:2] == b"\x1f\x8b" or encoding == "gzip":
        data = gzip.decompress(data)
    elif encoding == "deflate":
        data = zlib.decompress(data, -zlib.MAX_WBITS)
    return data, final_url


def item_id(url: str) -> str:
    """URL から安定した ID（クエリの utm_* と末尾スラッシュは無視）"""
    parts = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    )
    norm = urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _parse_date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None and re.fullmatch(r"\d{4}-\d{2}-\d{2}Z?", value):
        # 日付だけ（Apple Newsroom の「2026-10-28Z」など。Python 3.12 の fromisoformat は読めない）
        dt = datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if dt is None:
        # Python 3.9 の fromisoformat は「Z」「+0900」を読めないので「+09:00」にそろえる
        iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.replace("Z", "+00:00"))
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def unescape_text(value: str) -> str:
    """二重に書かれた実体参照（&amp;#8211; など）も戻す。最大3回まで"""
    out = value or ""
    for _ in range(3):
        nxt = html.unescape(out)
        if nxt == out:
            break
        out = nxt
    return out


def strip_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", value or "")
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape_text(value)).strip()


def parse_feed(feed: dict) -> list[dict]:
    """RSS 2.0 / Atom / RSS 1.0(RDF) を共通形式に。content は全文が入っていれば全文（HTML除去済み）。"""
    data, _ = fetch(feed["url"], accept="application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8")
    data = data.lstrip()  # XML 宣言の前に改行がある WordPress（イオン等）
    try:
        root = ET.fromstring(data)
    except ValueError:
        # Shift_JIS の XML（総務省・国交省など）は expat が読めないので文字列に直してから解析する
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", _decode(data))
        root = ET.fromstring(text)
    nodes = root.findall(".//item") or root.findall(f".//{ATOM}entry") or root.findall(f".//{RSS1}item")
    items = []
    for node in nodes:
        def first(*tags):
            for tag in tags:
                el = node.find(tag)
                if el is not None and (el.text or "").strip():
                    return el.text.strip()
            return ""

        title = strip_html(first("title", f"{ATOM}title", f"{RSS1}title"))
        link = first("link", f"{RSS1}link")
        if not link:
            for el in node.findall(f"{ATOM}link"):
                if el.get("rel", "alternate") == "alternate" and el.get("href"):
                    link = el.get("href")
                    break
        if not title or not link:
            continue
        link = urllib.parse.urljoin(feed["url"], link.strip())  # 相対 URL のフィード（Sakana AI 等）
        published = _parse_date(first("pubDate", f"{DC}date", f"{ATOM}published", f"{ATOM}updated"))
        summary_raw = first("description", f"{ATOM}summary", f"{RSS1}description")
        content_raw = first(f"{CONTENT}encoded", f"{ATOM}content")
        image = None
        for el in list(node.findall(f"{MEDIA}content")) + list(node.findall(f"{MEDIA}thumbnail")) + list(node.findall("enclosure")):
            url = el.get("url") or ""
            kind = el.get("type") or el.get("medium") or "image"
            if url and "image" in kind:
                image = url
                break
        items.append(
            {
                "id": item_id(link),
                "url": link.strip(),
                "title": title,
                "summary": strip_html(summary_raw)[:600],
                "content": strip_html(content_raw or summary_raw)[:TEXT_CAP],
                "published_at": published,
                "source_name": feed["name"],
                "source_slug": feed["slug"],
                "lang": feed["lang"],
                "image": image,
                "priority": feed.get("priority", 5),
            }
        )
    return items


def is_ai_related(item: dict, title_only: bool = False) -> bool:
    text = item["title"]
    if not title_only:
        summary = item.get("summary") or ""
        # Atom feeds can provide their synopsis as content without a summary.
        # Limit this fallback to the opening, to avoid matching unrelated footer text.
        text += " " + (summary or (item.get("content") or "")[:600])
    return bool(AI_RE.search(text))


def _norm_title(title: str) -> str:
    return re.sub(r"\W+", "", title.lower())[:60]


def collect(lookback_days: int = 3, state: dict | None = None, feeds: list[dict] | None = None, report: dict | None = None) -> list[dict]:
    """期間内・未処理の候補を返す。優先度→新しい順で、同じ発信元が続かないよう交互に並べる。

    report を渡すと {slug: {"fetched": n, "kept": n, "error": str}} を書き込む。
    """
    state = state if state is not None else {}
    done = set((state.get("processed_web") or {}).keys())
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    feeds = feeds or FEEDS
    report = report if report is not None else {}

    def load(feed):
        try:
            return feed, parse_feed(feed), ""
        except Exception as error:  # noqa: BLE001 - 1つ落ちても他は続ける
            return feed, [], f"{type(error).__name__}: {str(error)[:120]}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(load, feeds))

    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    per_source: dict[str, list[dict]] = {}
    # 日付の無いフィード（Google Developers Blog 等）: 初めて見た時刻を発表時刻の代わりにする。
    #   そのフィードを初めて読んだ回は全件を「既に見た」扱いにして、古い記事を一度に流さない
    first_seen = state.setdefault("undated_first_seen", {}) if isinstance(state, dict) else {}
    now_iso = datetime.now(timezone.utc).isoformat()
    for feed, items, error in results:
        undated = [i for i in items if i["published_at"] is None]
        if undated and not error:
            known = first_seen.setdefault(feed["slug"], {})
            baseline = not known
            for item in undated:
                if item["id"] not in known:
                    known[item["id"]] = "baseline" if baseline else now_iso
                if known[item["id"]] != "baseline":
                    item["published_at"] = _parse_date(known[item["id"]])
                    item["date_from_first_seen"] = True
            # 古い記録は捨てる（今フィードに載っている分だけ残す）
            ids_now = {i["id"] for i in undated}
            for old in [k for k in known if k not in ids_now]:
                known.pop(old, None)
        kept = 0
        counts = {"undated_or_old": 0, "processed": 0, "duplicate_id": 0, "blocked_host": 0,
                  "not_ai": 0, "include_exclude": 0, "duplicate_title": 0}
        for item in items:
            if item["published_at"] is None or item["published_at"] < since:
                counts["undated_or_old"] += 1
                continue
            if item["id"] in done:
                counts["processed"] += 1
                continue
            if item["id"] in seen_ids:
                counts["duplicate_id"] += 1
                continue
            host = urllib.parse.urlsplit(item["url"]).netloc.lower()
            if any(host == h or host.endswith("." + h) for h in BLOCKED_HOSTS):
                counts["blocked_host"] += 1
                continue
            if feed.get("ai_only") and not is_ai_related(item, feed.get("title_only", False)):
                counts["not_ai"] += 1
                continue
            if feed.get("include_re") and not re.search(feed["include_re"], item["title"]):
                counts["include_exclude"] += 1
                continue
            if feed.get("exclude_re") and re.search(feed["exclude_re"], item["title"]):
                counts["include_exclude"] += 1
                continue
            key = _norm_title(item["title"])
            if key in seen_titles:
                counts["duplicate_title"] += 1
                continue
            seen_ids.add(item["id"])
            seen_titles.add(key)
            per_source.setdefault(feed["slug"], []).append(item)
            kept += 1
        report[feed["slug"]] = {"fetched": len(items), "kept": kept, "error": error, **counts}

    # 発信元ごとに新しい順 → 優先度の高い発信元から1件ずつ交互に取り出す
    queues = sorted(
        (sorted(v, key=lambda i: i["published_at"], reverse=True) for v in per_source.values()),
        key=lambda q: (q[0]["priority"], -q[0]["published_at"].timestamp()),
    )
    ordered: list[dict] = []
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    return ordered


def report_totals(report: dict) -> dict:
    """Aggregate feed outcomes without logging source contents or credentials."""
    names = ("fetched", "kept", "undated_or_old", "processed", "duplicate_id", "blocked_host",
             "not_ai", "include_exclude", "duplicate_title")
    totals = {name: sum(int(r.get(name, 0)) for r in report.values()) for name in names}
    return {"feeds": len(report), "errors": sum(bool(r.get("error")) for r in report.values()), **totals}


# --------------------------------------------------------------------------- #
# 本文の抽出（簡易リーダビリティ）
# --------------------------------------------------------------------------- #

SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "button", "template", "iframe", "select"}
BLOCK_TAGS = {"p", "div", "section", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "table", "blockquote", "pre", "figure", "figcaption", "article", "main", "dd", "dt"}
VOID_TAGS = {"br", "img", "meta", "link", "input", "hr", "source", "area", "base", "col", "embed", "param", "track", "wbr"}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.skip_at: int | None = None  # 読み飛ばし中の要素の深さ
        self.buffers = {"body": [], "article": [], "main": []}
        self.in_article = 0
        self.in_main = 0
        self.meta: dict[str, str] = {}
        # 本文の中のリンク（元記事が案内する料金ページ・ヘルプ・資料を追いかける。scripts/source_links.py）
        self.links: list[dict] = []
        self._link: dict | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in {"og:image", "og:title", "og:description", "description", "twitter:image", "article:published_time",
                       "og:published_time"} and attrs.get("content"):
                self.meta.setdefault(key, attrs["content"].strip())
            return
        if tag in VOID_TAGS:
            if tag == "br":
                self._emit("\n")
            return
        self.stack.append(tag)
        cls = (attrs.get("class") or "") + " " + (attrs.get("id") or "")
        hidden = attrs.get("aria-hidden") == "true" or re.search(r"\b(cookie|newsletter|share|related|breadcrumb|subscribe|social)\b", cls, re.I)
        if self.skip_at is None and (tag in SKIP_TAGS or (hidden and tag in {"div", "section", "ul"})):
            self.skip_at = len(self.stack) - 1
        if tag == "article":
            self.in_article += 1
        if tag == "main":
            self.in_main += 1
        if tag == "a" and attrs.get("href") and self.skip_at is None and len(self.links) < 300:
            self._link = {"href": attrs["href"].strip(), "text": [], "main": bool(self.in_article or self.in_main)}
        if tag in BLOCK_TAGS:
            self._emit("\n")

    def handle_endtag(self, tag):
        if tag == "a" and self._link is not None:
            link, self._link = self._link, None
            link["text"] = re.sub(r"\s+", " ", "".join(link["text"])).strip()[:120]
            self.links.append(link)
        if tag in VOID_TAGS or tag not in self.stack:
            return
        # 閉じ忘れのタグをまとめて閉じる
        while self.stack:
            top = self.stack.pop()
            if self.skip_at is not None and len(self.stack) <= self.skip_at:
                self.skip_at = None
            if top == "article":
                self.in_article = max(0, self.in_article - 1)
            if top == "main":
                self.in_main = max(0, self.in_main - 1)
            if top in BLOCK_TAGS:
                self._emit("\n")
            if top == tag:
                break

    def handle_data(self, data):
        if self.skip_at is None and data.strip():
            self._emit(data)
            if self._link is not None:
                self._link["text"].append(data)

    def _emit(self, text):
        if self.skip_at is not None and text != "\n":
            return
        self.buffers["body"].append(text)
        if self.in_article:
            self.buffers["article"].append(text)
        if self.in_main:
            self.buffers["main"].append(text)


def _collapse(parts: list[str]) -> str:
    text = "".join(parts)
    lines = [re.sub(r"[ \t　\xa0]+", " ", line).strip() for line in text.split("\n")]
    # 短すぎる断片（メニュー残骸）を落としつつ段落を保つ
    kept = [line for line in lines if len(line) >= 2]
    return "\n".join(kept).strip()


def _decode(data: bytes) -> str:
    head = data[:4000].decode("ascii", "ignore")
    match = re.search(r"charset=[\"']?([\w-]+)", head, re.I)
    for enc in [match.group(1) if match else None, "utf-8", "cp932", "euc-jp"]:
        if not enc:
            continue
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "ignore")


# --------------------------------------------------------------------------- #
# 直接読めないページ（GitHub Actions から openai.com などは 403）を、正規の経路で読む
#   1. 記事ページを直接 → 2. 読み取りサービス r.jina.ai（鍵 → 鍵が残高切れ・無効なら鍵なしの無料枠）
#   → 3. Claude API のサーバー側 web fetch（Haiku・予算は llm.py の帳簿で管理）→ 呼び出し側でフィードの本文
#   ボット除けをすり抜ける細工（UA の偽装・CAPTCHA の突破）はしない。2・3 は robots.txt が許すときだけ。
# 2026-11-03: JINA_API_KEY の残高切れで鍵付きは 402。鍵付きで失敗した時点で諦めていたため OpenAI 公式ブログを落としていた
# --------------------------------------------------------------------------- #

JINA_READER = "https://r.jina.ai/"
_jina_key_dead: str | None = None          # この実行で鍵が使えないと分かった理由（以後は鍵なしで読む）
_robots_cache: dict = {}  # origin → Robots / _DisallowAll / None（制限なし）
API_FETCH_MAX_PER_RUN = 8                  # Claude API の web fetch はこの実行で何回まで
API_FETCH_TOOL = "web_fetch_20260318"
_api_fetch_used = 0
_api_fetch_blocked_hosts: set[str] = set()  # API でも読めなかったホスト（この実行ではもう試さない）


class Robots:
    """robots.txt の一般のクローラー（*）向けの決まり（RFC 9309）。一番長く一致した規則が勝ち、同じ長さなら Allow。
    Python 3.12 の urllib.robotparser は「上から最初に一致した規則」で判定するため、「Allow: /」の後の
    「Disallow: /private/」を無視してしまう（2026-11-04 に見つけた。本番は 3.12）"""

    def __init__(self, lines: list[str]):
        groups: list[tuple[set[str], list[tuple[bool, str]]]] = []
        agents: set[str] = set()
        rules: list[tuple[bool, str]] = []
        last_was_agent = False
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not last_was_agent and (agents or rules):
                    groups.append((agents, rules))
                    agents, rules = set(), []
                agents.add(value.lower())
                last_was_agent = True
            elif key in ("allow", "disallow"):
                last_was_agent = False
                if value or key == "allow":
                    rules.append((key == "allow", value))
        if agents or rules:
            groups.append((agents, rules))
        self.rules = [r for a, rs in groups if "*" in a for r in rs]

    @staticmethod
    def _match(pattern: str, path: str) -> bool:
        if not pattern:
            return False
        regex = "".join(".*" if c == "*" else ("$" if c == "$" and i == len(pattern) - 1 else re.escape(c)) for i, c in enumerate(pattern))
        return re.match(regex, path) is not None

    def can_fetch(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        path = urllib.parse.unquote(parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None
        for allow, pattern in self.rules:
            if self._match(pattern, path):
                cand = (len(pattern), allow)
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and allow):
                    best = cand
        return True if best is None else best[1]


class _DisallowAll:
    def can_fetch(self, url: str) -> bool:
        return False


def robots_allows(url: str) -> bool:
    """robots.txt（RFC 9309）が一般のクローラー（*）にこの URL を許しているか。ホストごとに1回だけ読む。
    4xx（ファイルなし・ボット除けで読めない）は「制限なし」、5xx・通信の失敗は「全部禁止」として扱う（RFC 9309 2.3.1）"""
    parts = urllib.parse.urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _robots_cache:
        parser: Robots | _DisallowAll | None
        try:
            data, _ = fetch(origin + "/robots.txt", timeout=10, accept="text/plain,*/*;q=0.5")
            parser = Robots(_decode(data[:500_000]).splitlines())
        except urllib.error.HTTPError as error:
            parser = None if 400 <= error.code < 500 else _DisallowAll()  # 4xx は制限なし、5xx は全部禁止
        except Exception:  # noqa: BLE001
            parser = _DisallowAll()
        _robots_cache[origin] = parser
    parser = _robots_cache[origin]
    return parser is None or parser.can_fetch(url)


def fetch_jina(url: str, timeout: int = 25, accept: str = "text/plain", check_robots: bool = True, return_format: str = "") -> tuple[bytes, str]:
    """読み取りサービス（r.jina.ai）経由で取る。鍵（JINA_API_KEY）が残高切れ（402）・無効（401）なら、
    この実行の残りは鍵なし（無料枠。回数制限は厳しめ）で読む。robots.txt が禁じるページは読まない。
    return_format="html" で描画後の HTML を返させる（scripts/images.py が写真の候補を探すとき）"""
    extra = {"extra_headers": {"X-Return-Format": return_format}} if return_format else {}  # 指定が無いときは今までどおりの呼び方
    global _jina_key_dead
    if check_robots and not robots_allows(url):
        raise RuntimeError(f"robots.txt が許していない: {url}")
    key = os.environ.get("JINA_API_KEY", "")
    if key and not _jina_key_dead:
        try:
            return fetch(JINA_READER + url, timeout=timeout, accept=accept, token=key, **extra)
        except urllib.error.HTTPError as error:
            if error.code not in (401, 402):
                raise
            _jina_key_dead = f"HTTP {error.code}"
            note = "残高切れ" if error.code == 402 else "鍵が無効"
            print(f"[jina] JINA_API_KEY が使えない（{_jina_key_dead}: {note}）。この実行は鍵なし（無料枠）で読みます", flush=True)
    return fetch(JINA_READER + url, timeout=timeout, accept=accept, **extra)


def _clean_markdown(body: str, url: str) -> tuple[str, list[dict]]:
    """読み取りサービス・web fetch のマークダウンから、画像とリンクの記法を外した本文とリンクを返す"""
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    links = [{"href": href, "text": text, "main": True} for text, href in re.findall(r"\[([^\]]{1,120})\]\((https?://[^)\s]+)", body)]
    body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)
    return _collapse([body]), _abs_links(links, url)


def _fetch_via_reader(url: str) -> dict:
    """ボット除けで弾かれるページ（openai.com など）を、読み取りサービス（r.jina.ai）経由で取る"""
    data, _ = fetch_jina(url)
    body = _decode(data)
    title = ""
    m = re.search(r"^Title:\s*(.+)$", body, re.M)
    if m:
        title = m.group(1).strip()
    m = re.search(r"^Published Time:\s*(.+)$", body, re.M)
    published = m.group(1).strip() if m else ""
    if "Markdown Content:" in body:
        body = body.split("Markdown Content:", 1)[1]
    text, links = _clean_markdown(body, url)
    return {"text": text[:TEXT_CAP], "full_text": text[:400_000] if len(text) > TEXT_CAP else "", "image": None, "title": title,
            "description": "", "final_url": url, "published": published, "links": links}


def _fetch_via_api(url: str) -> dict:
    """Claude API のサーバー側 web fetch（Anthropic の取得器。robots.txt を守る）で本文を取る。
    Haiku で1回だけ取得させ、取得結果のブロックの本文をそのまま使う（モデルの要約は使わない）。
    予算・回数は llm.py の帳簿（段 helper）で管理する。読めなければ例外"""
    global _api_fetch_used
    host = urllib.parse.urlsplit(url).netloc.lower()
    if os.environ.get("WEB_FETCH_API_DISABLE") == "1":
        raise RuntimeError("WEB_FETCH_API_DISABLE=1")
    if host in _api_fetch_blocked_hosts:
        raise RuntimeError(f"{host} は API でも読めなかった（この実行）")
    if _api_fetch_used >= API_FETCH_MAX_PER_RUN:
        raise RuntimeError(f"API の web fetch はこの実行の上限 {API_FETCH_MAX_PER_RUN} 回に到達")
    if not robots_allows(url):
        raise RuntimeError(f"robots.txt が許していない: {url}")
    import llm  # noqa: PLC0415 - 使うときだけ読み込む（feeds.py は標準ライブラリだけで動かす）

    model, why = llm.api_choose("helper")
    if not model:
        raise RuntimeError(f"Claude API を使わない（{why}）")
    _api_fetch_used += 1
    try:
        message = llm._get_api_client().messages.create(  # noqa: SLF001 - llm.py の API クライアントを共有する
            model=model,
            max_tokens=512,
            tools=[{"type": API_FETCH_TOOL, "name": "web_fetch", "max_uses": 1, "max_content_tokens": 8000,
                    "allowed_domains": [host]}],
            messages=[{"role": "user", "content": f"Use web_fetch exactly once to fetch this URL, then reply only DONE.\n{url}"}],
        )
    except ImportError as error:
        raise RuntimeError("anthropic パッケージが無い") from error
    except Exception as error:  # noqa: BLE001
        detail = f"{type(error).__name__}: {str(error)[:120]}"
        if llm._api_error_kind(error) == "exhausted":  # noqa: SLF001
            llm.mark_api_exhausted(detail)
        raise RuntimeError(f"web fetch に失敗（{detail}）") from error
    llm.record_api_usage("helper", model, message.usage)
    for block in message.content:
        if getattr(block, "type", "") != "web_fetch_tool_result":
            continue
        result = block.content
        if getattr(result, "type", "") != "web_fetch_result":
            code = getattr(result, "error_code", "?")
            if code in ("url_not_accessible", "url_not_allowed"):
                _api_fetch_blocked_hosts.add(host)
            raise RuntimeError(f"web fetch: {code}")
        document = result.content
        source = getattr(document, "source", None)
        if getattr(source, "type", "") != "text":
            raise RuntimeError(f"web fetch: 文字でない中身（{getattr(source, 'type', '?')}）")
        text, links = _clean_markdown(str(getattr(source, "data", "") or ""), url)
        return {"text": text[:TEXT_CAP], "full_text": text[:400_000] if len(text) > TEXT_CAP else "", "image": None,
                "title": str(getattr(document, "title", "") or ""), "description": "",
                "final_url": getattr(result, "url", "") or url, "published": "", "links": links}
    raise RuntimeError("web fetch: 取得結果が無い")


def _short_error(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    return f"{type(error).__name__}: {str(error)[:60]}" if not isinstance(error, RuntimeError) else str(error)[:80]


def fetch_article_text(url: str) -> dict:
    """{"text", "image", "title", "description", "final_url", "published"}。取得失敗時は例外。
    直接取れない・中身が薄いときは読み取りサービス → Claude API の web fetch の順で取り直す（一次情報を落とさないため）。
    例外の文言は理由を先に書く（ログで URL が切れても理由が読めるように）"""
    reasons: list[str] = []
    try:
        direct = _fetch_direct(url)
    except Exception as error:  # noqa: BLE001
        direct = None
        reasons.append(f"直接 {_short_error(error)}")
    if direct and len(direct["text"]) >= 600:
        return direct
    best = direct
    for name, route in (("読み取り", _fetch_via_reader), ("API", _fetch_via_api)):
        try:
            via = route(url)
        except Exception as error:  # noqa: BLE001
            reasons.append(f"{name} {_short_error(error)}")
            continue
        if len(via["text"]) > len(best["text"] if best else ""):
            if direct:
                via["image"] = direct.get("image")
                via["description"] = direct.get("description", "")
                via["published"] = via.get("published") or direct.get("published", "")
            best = via
        if len(best["text"]) >= 600:
            return best
    if best:
        return best
    raise RuntimeError(f"本文を取得できません（{' / '.join(reasons)}）: {url}")


def _fetch_direct(url: str) -> dict:
    data, final_url = fetch(url, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")
    if data[:5] == b"%PDF-":
        # PDF の中身をそのまま文字として読むと、壊れた文字列が「本文」になる（2026-11-03）。読み取りサービスに任せる
        raise ValueError("PDF の資料は直接読まない（読み取りサービスで読む）")
    parser = _Extractor()
    try:
        parser.feed(_decode(data))
        parser.close()
    except Exception:  # noqa: BLE001 - 壊れた HTML でもそこまでの結果を使う
        pass
    candidates = [_collapse(parser.buffers[k]) for k in ("article", "main", "body")]
    text = next((c for c in candidates if len(c) >= 500), max(candidates, key=len))
    image = parser.meta.get("og:image") or parser.meta.get("twitter:image")
    if image:
        image = urllib.parse.urljoin(final_url, html.unescape(image))
    return {
        "text": text[:TEXT_CAP],
        # 長い資料（上場の届出書・決算の資料など）の後半。ニュースの種類によっては必要な部分だけ抜き出して使う（facts_bundle.continuation_group）
        "full_text": text[:400_000] if len(text) > TEXT_CAP else "",
        "image": image,
        "title": parser.meta.get("og:title", ""),
        "description": parser.meta.get("og:description") or parser.meta.get("description", ""),
        "final_url": final_url,
        "published": parser.meta.get("article:published_time", "") or parser.meta.get("og:published_time", "") or _jsonld_published(data),
        "links": _abs_links(parser.links, final_url),
    }


def _abs_links(links: list[dict], base: str) -> list[dict]:
    """リンクを絶対URLにして重複を外す（{"url", "text", "main"}）"""
    out, seen = [], set()
    for link in links:
        href = html.unescape(link.get("href") or "")
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        url = urllib.parse.urljoin(base, href).split("#")[0]
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "text": link.get("text") or "", "main": bool(link.get("main"))})
    return out


_JSONLD_DATE_RE = re.compile(rb'"datePublished"\s*:\s*"([^"]{8,40})"')


def _jsonld_published(data: bytes) -> str:
    """JSON-LD の datePublished（article:published_time が無いページ用）。最初のもの"""
    m = _JSONLD_DATE_RE.search(data[:600_000])
    return m.group(1).decode("utf-8", "ignore").strip() if m else ""


def page_published(page: dict | None) -> datetime | None:
    """記事ページに書かれた公開日時（article:published_time・JSON-LD datePublished・読み取りサービスの Published Time）"""
    return _parse_date(str((page or {}).get("published") or ""))


# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--text", help="この URL の本文抽出結果を表示して終了")
    parser.add_argument("--broad", action="store_true", help="幅広い話題のフィード（BROAD_FEEDS）を確認する")
    args = parser.parse_args()

    if args.text:
        result = fetch_article_text(args.text)
        print(json.dumps({**result, "text": result["text"][:1500], "chars": len(result["text"])}, ensure_ascii=False, indent=2))
        return 0

    report: dict = {}
    targets = BROAD_FEEDS if args.broad else FEEDS
    items = collect(args.days, {}, targets, report=report)
    log(f"{'slug':28s} {'取得':>5s} {'期間内':>6s}  状態")
    for feed in targets:
        r = report.get(feed["slug"], {})
        status = "NG " + r["error"] if r.get("error") else "OK"
        log(f"{feed['slug']:28s} {r.get('fetched', 0):5d} {r.get('kept', 0):6d}  {status}")
    log(f"\n直近 {args.days} 日の候補: {len(items)} 件")
    for item in items[:15]:
        log(f"  - [{item['source_slug']}] {item['published_at']:%m-%d %H:%M} {item['title'][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
