"""scripts/images.py: 報道の記事から会社自身の発表をたどって公式の画像を使う・AI 画像で製品を描かない（通信しない）

2026-11-03: kubell の「セキュアSAMBA」の Mac 用アプリの記事（出典はマイナビニュース）で、会社の発表の画像を探さず、
AI が Finder を開いた MacBook を描いた画像を付けていた。
"""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import images  # noqa: E402

OUTLET_URL = "https://news.mynavi.jp/techplus/article/20261103-5248905/"
HOME_URL = "https://www.kubell-storage.com/"
MAC_URL = "https://www.kubell-storage.com/news/2026/10/sambamacdrive.html"
LINK_URL = "https://www.kubell-storage.com/news/2026/10/samba-link.html"

ARTICLE = {
    "slug": "secure-samba-mac-drive-app",
    "sourceType": "web",
    "sourceUrl": OUTLET_URL,
    "sourceName": "マイナビニュース",
    "channel": "マイナビニュース",
    "channelSlug": "radar-news.mynavi.jp",
    "title": "法人向けオンライン保存「セキュアSAMBA」、Mac用アプリ提供開始　Finderで直接編集",
    "seoTitle": "セキュアSAMBAのMac向けアプリとは？料金と始め方",
    "lede": "マイナビニュースの報道によると、kubellストレージは10月28日、法人向けの国産オンラインストレージ「セキュアSAMBA」のMac向けドライブアプリの提供を始めました。",
    "keywords": ["セキュアSAMBA", "Mac向けドライブアプリ", "kubellストレージ"],
    "tags": ["セキュアSAMBA", "オンラインストレージ", "Mac"],
    "genreSlug": "ai",
    "publishedAt": "2026-11-03T08:30:00+00:00",
    "factSources": [{"label": "日本での提供", "name": "Apple", "url": "https://www.apple.com/jp/iphone/"}],
    "thumbnail": "https://ai-tech-express.jp/img/secure-samba-mac-drive-app-a620cc14.jpg",
    "imageCredit": {"kind": "ai", "model": "flux-2-klein-4b"},
}

# マイナビニュースの本文は class="c-archiveMain" の中（NOT_BODY の archive に当たり「本文ではない場所」に見える）
OUTLET_HTML = """<html><head><meta property="og:image" content="/index_images/index.jpg"><title>記事 | マイナビニュース</title></head>
<body><div class="c-archiveMain"><article class="article"><div class="article-body">
<p><a href="https://www.kubell-storage.com/">kubellストレージ</a>は10月28日、法人向け国産オンラインストレージ「<a href="https://securesamba.jp/">セキュアSAMBA</a>」の提供を発表した。</p>
<p><a href="https://twitter.com/mn_techplus">X</a> <a href="https://ad.example.com/lp">詳しくはこちら</a></p>
</div></article></div></body></html>"""

HOME_HTML = """<html><head><title>株式会社kubellストレージ</title></head><body>
<ul class="news-list">
<li><a href="https://www.kubell-storage.com/news/2026/10/sambamacdrive.html">2026.10.28 プレスリリース 法人向け国産オンラインストレージ「セキュアSAMBA」、「Mac向けドライブアプリ」をリリース</a></li>
<li><a href="https://www.kubell-storage.com/news/2026/10/samba-link.html">2026.10.15 プレスリリース 法人向け国産オンラインストレージ「セキュアSAMBA」、「外部共有リンク」を強化</a></li>
<li><a href="https://www.kubell-storage.com/news/2025/01/old.html">2025.01.10 プレスリリース 「セキュアSAMBA」Mac向けの旧版</a></li>
</ul><a href="/news/">ニュース一覧</a></body></html>"""

MAC_HTML = """<html><head>
<meta property="og:site_name" content="株式会社kubellストレージ">
<meta property="og:title" content="法人向け国産オンラインストレージ「セキュアSAMBA」、「Mac向けドライブアプリ」をリリース |  株式会社kubellストレージ">
<meta property="og:image" content="https://www.kubell-storage.com/image/common/ogp.png">
<title>法人向け国産オンラインストレージ「セキュアSAMBA」、「Mac向けドライブアプリ」をリリース |  株式会社kubellストレージ</title></head>
<body><article><img src="/assets_c/2026/10/key-visual-1999x1125.png" width="1999" height="1125"></article></body></html>"""

SECURESAMBA_HTML = "<html><head><title>セキュアSAMBA</title></head><body><a href='/pricing/'>料金</a></body></html>"


def fake_pages(pages: dict[str, str]):
    def fetch(url: str):
        if url not in pages:
            raise RuntimeError(f"not found {url}")
        return pages[url], url

    return fetch


class ReleaseTestBase(unittest.TestCase):
    def setUp(self):
        images.PARSE_CACHE.clear()
        images.PAGE_META.clear()
        self.log = mock.patch.object(images, "log", lambda *_: None)
        self.log.start()

    def tearDown(self):
        self.log.stop()
        images.PARSE_CACHE.clear()
        images.PAGE_META.clear()


class AnnouncementTest(unittest.TestCase):
    def test_service_announcements(self):
        self.assertTrue(images.announcement_article(ARTICLE))
        self.assertFalse(images.real_object_article(ARTICLE))  # アプリの話なので「形のある製品」ではないが、発表ではある
        for title in ("Notion、新しいAI機能を提供開始", "freee、請求書アプリをリリース", "Googleが新型スマホ「Pixel 11」を発表"):
            self.assertTrue(images.announcement_article({"title": title, "genreSlug": "ai"}), title)

    def test_not_announcements(self):
        for article in (
            {"title": "ソフトバンクの決算、通信が好調", "genreSlug": "business"},
            {"title": "生成AIの利用実態を調査、6割が業務で利用", "genreSlug": "ai"},
            {"title": "なでしこジャパン、W杯予選のメンバーを発表", "genreSlug": "sports"},
            {"title": "東京都、防災アプリを公開", "genreSlug": "public"},
            {"title": "AIで地震の揺れを予測", "genreSlug": "ai"},
            {"title": "国土交通省、令和8年秋の褒章受章者を発表　伝達式は11月18日に開催へ", "genreSlug": "business"},
            {"title": "財務省が製造たばこの小売定価の認可情報を公開、随時更新される変更手続きを整理", "genreSlug": "business"},
            {"title": "欧州中央銀行のECBが政策金利据え置きを発表、今後の利上げを示唆", "genreSlug": "business"},
            {"title": "NTTドコモグループ、令和8年熊本地震の被災地で社員ボランティア活動を開始", "genreSlug": "business"},
            {"title": "まとめ記事", "type": "roundup"},
        ):
            self.assertFalse(images.announcement_article(article), article["title"])

    def test_secondary_source(self):
        self.assertTrue(images.secondary_source(ARTICLE))
        official = {**ARTICLE, "sourceUrl": MAC_URL, "channelSlug": "radar-official"}
        self.assertFalse(images.secondary_source(official))
        self.assertFalse(images.secondary_source({**ARTICLE, "sourceType": "youtube"}))


class ReleaseScoreTest(unittest.TestCase):
    def test_matching_release_scores_higher_than_other_release(self):
        mac = images.release_score("2026.10.28 プレスリリース 「セキュアSAMBA」、「Mac向けドライブアプリ」をリリース " + MAC_URL, ARTICLE)
        other = images.release_score("2026.10.15 プレスリリース 「セキュアSAMBA」、「外部共有リンク」を強化 " + LINK_URL, ARTICLE)
        self.assertGreater(mac, 0)
        self.assertGreater(mac, other)

    def test_needs_product_name_and_recent_date(self):
        self.assertEqual(images.release_score("2026.10.28 新しいMac向けアプリをリリース", ARTICLE), 0)  # 製品名が無い
        self.assertEqual(images.release_score("2025.01.10 「セキュアSAMBA」Mac向けの旧版", ARTICLE), 0)  # 古い発表
        self.assertEqual(images.release_score("「セキュアSAMBA」", ARTICLE), 0)  # 製品名だけ（どの発表か分からない）

    def test_text_date(self):
        self.assertEqual(images._text_date("/news/2026/10/x.html").month, 10)
        self.assertEqual(images._text_date("/news/20261028-sambamacdrive/").day, 28)
        self.assertEqual(images._text_date("2026年10月28日 発表").day, 28)
        self.assertIsNone(images._text_date("/news/17295/"))


class FindOfficialReleaseTest(ReleaseTestBase):
    def test_follows_company_site_to_matching_release(self):
        pages = {OUTLET_URL: OUTLET_HTML, HOME_URL: HOME_HTML, MAC_URL: MAC_HTML, "https://securesamba.jp/": SECURESAMBA_HTML}
        with mock.patch.object(images, "fetch_page", side_effect=fake_pages(pages)):
            found = images.find_official_release(ARTICLE)
        self.assertIsNotNone(found)
        self.assertEqual(found["url"], MAC_URL)
        self.assertEqual(found["official"], {"wire": ""})
        self.assertEqual(found["org"], "株式会社kubellストレージ")
        self.assertEqual(found["article"]["sourceUrl"], MAC_URL)
        self.assertEqual(found["article"]["thumbnail"], "")  # 報道の記事の画像・前の AI 画像を候補にしない
        self.assertEqual(ARTICLE["sourceUrl"], OUTLET_URL)  # 元の記事は書き換えない

    def test_direct_pr_times_link(self):
        prt = "https://prtimes.jp/main/html/rd/p/000000123.000045678.html"
        outlet = OUTLET_HTML.replace("https://securesamba.jp/", prt).replace(">セキュアSAMBA<", ">「セキュアSAMBA」Mac向けドライブアプリ提供開始<")
        prt_html = "<html><head><title>「セキュアSAMBA」、「Mac向けドライブアプリ」をリリース | 株式会社kubellストレージのプレスリリース</title></head><body></body></html>"
        pages = {OUTLET_URL: outlet, prt: prt_html, HOME_URL: HOME_HTML, MAC_URL: MAC_HTML}
        with mock.patch.object(images, "fetch_page", side_effect=fake_pages(pages)):
            found = images.find_official_release(ARTICLE)
        self.assertEqual(found["url"], prt)
        self.assertEqual(found["official"], {"wire": "PR TIMES"})
        self.assertEqual(found["org"], "株式会社kubellストレージ")

    def test_release_title_must_match(self):
        wrong = MAC_HTML.replace("「Mac向けドライブアプリ」をリリース", "会社概要").replace("「セキュアSAMBA」、", "")
        pages = {OUTLET_URL: OUTLET_HTML, HOME_URL: HOME_HTML, MAC_URL: wrong, LINK_URL: wrong, "https://securesamba.jp/": SECURESAMBA_HTML}
        with mock.patch.object(images, "fetch_page", side_effect=fake_pages(pages)):
            self.assertIsNone(images.find_official_release(ARTICLE))

    def test_ignores_ads_sns_and_generic_links(self):
        outlet = OUTLET_HTML.replace("https://www.kubell-storage.com/", "https://other.example.com/").replace(">kubellストレージ<", ">こちら<")
        outlet = outlet.replace("https://securesamba.jp/", "https://ad2.example.com/").replace(">セキュアSAMBA<", ">人気の記事<")
        calls = []

        def fetch(url):
            calls.append(url)
            if url == OUTLET_URL:
                return outlet, url
            raise RuntimeError("should not fetch")

        with mock.patch.object(images, "fetch_page", side_effect=fetch):
            self.assertIsNone(images.find_official_release(ARTICLE))
        self.assertEqual(calls, [OUTLET_URL])


class ProcessReleaseTest(ReleaseTestBase):
    def ctx(self, **extra):
        return {
            "repeats": Counter(), "used_stock": set(), "credit_by_thumb": {}, "official_by_original": {}, "dry_run": True,
            "fast": False, "ai_only": False, "defer_hours": 3, "retry_ai": False, "repick": True, "scene": "",
            "previous_credit": {}, "previous_thumb": {}, **extra,
        }

    def release(self):
        draft = {**ARTICLE, "sourceUrl": MAC_URL, "sourceName": "株式会社kubellストレージ", "channelSlug": "radar-official", "thumbnail": ""}
        return {"url": MAC_URL, "article": draft, "official": {"wire": ""}, "org": "株式会社kubellストレージ"}

    def test_uses_company_release_image(self):
        article = {**ARTICLE}
        article.pop("imageCredit")
        best = {"kind": "hero", "url": "https://www.kubell-storage.com/assets_c/2026/10/key.png", "image": object(), "rank": images.OFFICIAL_RANK["hero"], "where": "body"}
        seen = {}

        def pick(src, repeats, fast=False):
            seen["sourceUrl"] = src["sourceUrl"]
            return {"best": best, "reasons": []}

        with mock.patch.object(images, "find_official_release", return_value=self.release()), \
                mock.patch.object(images, "pick_official", side_effect=pick), \
                mock.patch.object(images, "store", return_value=("https://ai-tech-express.jp/img/secure-samba-mac-drive-app-03b283e8.jpg", (1200, 675))), \
                mock.patch.object(images, "generate_ai", side_effect=AssertionError("AI を作らない")):
            outcome = images.process(Path("/nonexistent/secure-samba-mac-drive-app.json"), article, self.ctx())
        self.assertEqual(outcome, "official")
        self.assertEqual(seen["sourceUrl"], MAC_URL)  # 会社の発表のページで画像を選ぶ
        credit = article["imageCredit"]
        self.assertEqual(credit["kind"], "official")
        self.assertEqual(credit["org"], "株式会社kubellストレージ")
        self.assertEqual(credit["sourceUrl"], MAC_URL)
        self.assertEqual(credit["foundFrom"], OUTLET_URL)
        self.assertEqual(credit["imageKind"], "hero")
        self.assertEqual(article["sourceUrl"], OUTLET_URL)  # 記事の出典は報道のまま
        self.assertTrue(article["thumbnail"].endswith("-03b283e8.jpg"))

    def test_banner_from_release_beats_ai_for_announcements(self):
        article = {**ARTICLE}
        article.pop("imageCredit")
        best = {"kind": "banner", "url": "https://www.kubell-storage.com/assets_c/2026/10/key.png", "image": object(), "rank": images.OFFICIAL_RANK["banner"], "where": "body"}
        with mock.patch.object(images, "find_official_release", return_value=self.release()), \
                mock.patch.object(images, "pick_official", return_value={"best": best, "reasons": []}), \
                mock.patch.object(images, "store", return_value=("https://ai-tech-express.jp/img/x-1.jpg", (1200, 675))), \
                mock.patch.object(images, "generate_ai", side_effect=AssertionError("AI を作らない")):
            self.assertEqual(images.process(Path("/nonexistent/x.json"), article, self.ctx()), "official")

    def test_official_only_keeps_current_image_when_no_release(self):
        article = {**ARTICLE}
        article.pop("imageCredit")
        with mock.patch.object(images, "find_official_release", return_value=None), \
                mock.patch.object(images, "pick_official", side_effect=AssertionError("報道の記事の画像は見ない")), \
                mock.patch.object(images, "generate_ai", side_effect=AssertionError("AI を作らない")):
            outcome = images.process(Path("/nonexistent/x.json"), article, self.ctx(official_only=True))
        self.assertEqual(outcome, "kept")
        self.assertEqual(article["thumbnail"], ARTICLE["thumbnail"])

    def test_non_announcement_does_not_search(self):
        article = {**ARTICLE, "title": "生成AIの利用実態を調査、6割が業務で利用", "keywords": [], "tags": []}
        article.pop("imageCredit")
        with mock.patch.object(images, "find_official_release", side_effect=AssertionError("探さない")), \
                mock.patch.object(images, "visual_plan", side_effect=RuntimeError("stop")):
            self.assertEqual(images.process(Path("/nonexistent/x.json"), article, self.ctx()), "error")


class NoProductAiTest(unittest.TestCase):
    def test_service_prompt_forbids_product_and_screens(self):
        prompt = images.ai_prompt({"scene": "Soft light on an empty desk with paper folders.", "serviceLaunch": True}, {})
        self.assertIn("Do not depict the announced product, app or service", prompt)
        self.assertIn("laptop, phone, tablet", prompt)
        self.assertNotIn("Do not depict", images.ai_prompt({"scene": "A rice paddy."}, {}))

    def test_service_plan_rule_is_added(self):
        captured = {}

        def fake_generate(system, prompt, schema, **_):
            captured["prompt"] = prompt
            return {"queries": ["office desk"], "scene": "An empty desk."}, "test-model"

        with mock.patch.object(images.llm, "generate_json", side_effect=fake_generate), mock.patch.object(images, "llm_tier", return_value="images"):
            plan = images.visual_plan(ARTICLE, service=True)
        self.assertIn("must NOT depict the product", captured["prompt"])
        self.assertTrue(plan["serviceLaunch"])
        self.assertFalse(plan["productLaunch"])
        self.assertTrue(plan["product"])  # depicts_product に渡す（画面・端末が写っていないか確かめる）

    def test_ai_for_announcement_is_checked_for_product(self):
        """AI 画像に製品・画面が写ったら作り直し、写っていないことを確かめたら noProduct を付ける"""
        article = {**ARTICLE}
        article.pop("imageCredit")
        plan = {"queries": ["office desk"], "query_ja": "", "scene": "An empty desk.", "model": "m", "product": "computer, phone or app screen", "productLaunch": False, "serviceLaunch": True}
        made = [("img1", "flux"), ("img2", "flux")]
        checks = iter([True, False])  # 1回目は画面が写った → 作り直し
        ctx = ProcessReleaseTest.ctx(self)
        with mock.patch.object(images, "find_official_release", return_value=None), \
                mock.patch.object(images, "visual_plan", return_value=plan), \
                mock.patch.object(images, "generate_ai", side_effect=lambda *a, **k: made.pop(0)), \
                mock.patch.object(images, "shows_face", return_value=False), \
                mock.patch.object(images, "depicts_product", side_effect=lambda *a: next(checks)), \
                mock.patch.object(images, "store", return_value=("https://ai-tech-express.jp/img/x-2.jpg", (1200, 630))), \
                mock.patch.object(images, "log", lambda *_: None):
            outcome = images.process(Path("/nonexistent/x.json"), article, ctx)
        self.assertEqual(outcome, "ai")
        self.assertTrue(article["imageCredit"]["noProduct"])
        self.assertEqual(article["imageCredit"]["prompt"], images.NO_PRODUCT_SCENE)


if __name__ == "__main__":
    unittest.main()
