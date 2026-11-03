"""scripts/images.py の「公式の発表か」「出典に出す名前」の判定（通信しない）"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import images  # noqa: E402


def web(slug: str, url: str, name: str) -> dict:
    return {"sourceType": "web", "channelSlug": slug, "sourceUrl": url, "sourceName": name, "channel": name}


class OfficialSourceTest(unittest.TestCase):
    def test_company_newsroom_is_official(self):
        self.assertEqual(images.official_source(web("nvidia-blog", "https://blogs.nvidia.com/blog/x/", "NVIDIA公式ブログ")), {"wire": ""})
        self.assertEqual(images.official_source(web("sakana-ai", "https://sakana.ai/x/", "Sakana AI公式ブログ")), {"wire": ""})
        self.assertEqual(images.official_source(web("radar-official", "https://www.microsoft.com/x", "Microsoft公式")), {"wire": ""})

    def test_press_wire_is_official_with_wire_name(self):
        self.assertEqual(images.official_source(web("prtimes-all", "https://prtimes.jp/main/html/rd/p/1.html", "PR TIMES")), {"wire": "PR TIMES"})
        self.assertEqual(images.official_source(web("atpress", "https://www.atpress.ne.jp/news/1", "@Press")), {"wire": "@Press"})

    def test_media_and_unknown_are_not_official(self):
        self.assertIsNone(images.official_source(web("radar-techcrunch.com", "https://techcrunch.com/x", "TechCrunch")))
        self.assertIsNone(images.official_source(web("trends", "https://news.yahoo.co.jp/x", "Yahoo!ニュース")))
        self.assertIsNone(images.official_source(web("some-new-feed", "https://example.com/x", "例")))
        self.assertIsNone(images.official_source({"sourceType": None, "thumbnail": "https://i.ytimg.com/vi/x/maxresdefault.jpg"}))


class OrgNameTest(unittest.TestCase):
    def test_strips_channel_words(self):
        cases = {
            "NVIDIA公式ブログ": "NVIDIA",
            "Apple Newsroom（日本）": "Apple",
            "Amazon公式ニュース（About Amazon）": "Amazon",
            "PlayStation.Blog 日本語": "PlayStation",
            "Xbox Wire Japan": "Xbox",
            "Microsoft Source（ニュースルーム）": "Microsoft",
            "任天堂 トピックス": "任天堂",
            "ソフトバンクニュース": "ソフトバンク",
            "IPA（情報処理推進機構）": "IPA（情報処理推進機構）",
            "国立天文台": "国立天文台",
        }
        for name, want in cases.items():
            self.assertEqual(images.org_name({"sourceName": name}), want, name)

    def test_wire_org_from_page(self):
        url = "https://prtimes.jp/main/html/rd/p/1.html"
        images.PAGE_META[url] = {"title": "新サービス開始 | 株式会社アガタのプレスリリース", "author": ""}
        self.assertEqual(images.wire_org({"sourceUrl": url}), "株式会社アガタ")
        images.PAGE_META[url] = {"title": "題名 | おもいお", "author": ""}
        self.assertEqual(images.wire_org({"sourceUrl": url}), "おもいお")
        images.PAGE_META[url] = {"title": "x", "author": "株式会社サンプル"}
        self.assertEqual(images.wire_org({"sourceUrl": url}), "株式会社サンプル")


@unittest.skipIf(images.Image is None, "Pillow が無い")
class VariantsTest(unittest.TestCase):
    def test_keep_aspect_and_widths(self):
        image = images.Image.new("RGB", (2400, 1350), (40, 90, 160))
        jpeg, variants = images.render(image, keep_aspect=True)
        self.assertEqual(images.open_image(jpeg).size, (1200, 675))
        self.assertEqual(set(variants), {"w400", "sm", "w800", "w1200"})
        self.assertEqual(images.open_image(variants["w400"]).size, (400, 225))

    def test_small_source_is_not_enlarged(self):
        image = images.Image.new("RGB", (900, 500), (40, 90, 160))
        jpeg, variants = images.render(image, keep_aspect=True)
        self.assertEqual(images.open_image(jpeg).size, (900, 500))
        self.assertEqual(images.open_image(variants["w1200"]).size, (900, 500))


class PageCandidatesTest(unittest.TestCase):
    """元記事のページから、本文の画像だけを候補にする（関連記事のカード・メニュー・古い宣伝画像は外す）"""

    HTML = """<html><head><meta property="og:image" content="/og.jpg"></head><body>
    <header><img src="/logo-site.png"></header>
    <main><article>
      <figure><img src="/news/2026/11/02/main-photo.jpg" alt="調印式"></figure>
      <p><img src="/news/2026/11/02/sub.jpg"></p>
      <a href="/other-article.html"><img src="/thumbs/related.jpg"></a>
      <img src="/service/2026/07/14/promo.png">
      <iframe src="https://www.youtube.com/embed/abcdefghijk"></iframe>
    </article>
    <aside class="ranking"><img src="/rank1.jpg"></aside></main>
    <footer><img src="/foot.jpg"></footer></body></html>"""

    def test_body_images_only(self):
        parser = images._PageCollector()
        parser.feed(self.HTML)
        final = "https://example.co.jp/news/release.html"
        urls = [c["url"] for c in images.page_candidates(parser, final, "", {"publishedAt": "2026-11-02T00:00:00Z"})]
        self.assertEqual(urls[0], "https://example.co.jp/og.jpg")
        self.assertEqual(urls[1], "https://example.co.jp/news/2026/11/02/main-photo.jpg")
        self.assertIn("https://example.co.jp/news/2026/11/02/sub.jpg", urls)
        for bad in ("related.jpg", "promo.png", "rank1.jpg", "foot.jpg", "logo-site.png"):
            self.assertFalse(any(u.endswith(bad) for u in urls), bad)
        self.assertEqual(parser.youtube, ["abcdefghijk"])

    def test_video_on_topic(self):
        article = {"title": "NVIDIA、Kubernetes向けの実行基盤『Dynamo』を公開", "sourceName": "NVIDIA Developer Blog", "tags": ["Kubernetes"]}
        self.assertTrue(images.video_on_topic("NVIDIA Dynamo in 5 Minutes", article))
        self.assertFalse(images.video_on_topic("Pager Health Simplifies the Healthcare Journey with NVIDIA", article))

    def test_safe_url_encodes_japanese(self):
        self.assertEqual(images.safe_url("https://a.jp/画像.png?x=1"), "https://a.jp/%E7%94%BB%E5%83%8F.png?x=1")


class AiImageTest(unittest.TestCase):
    def test_objects_only_scene(self):
        scene = images.objects_only_scene({"scene": "A meeting room where businessmen review a price chart, soft light"}, {})
        self.assertNotIn("businessmen", scene)
        self.assertIn("no people", scene)

    @unittest.skipIf(images.Image is None, "Pillow が無い")
    def test_trim_dark_band(self):
        image = images.Image.new("RGB", (400, 210), (120, 140, 160))
        image.paste((5, 5, 5), (0, 200, 400, 210))
        self.assertEqual(images.trim_dark_band(image).size, (400, 200))
        dark = images.Image.new("RGB", (400, 210), (40, 40, 40))
        self.assertEqual(images.trim_dark_band(dark).size, (400, 210))


if __name__ == "__main__":
    unittest.main()


class HondaReleaseTest(unittest.TestCase):
    """2026-11-03 Honda RN0: 本文の写真の囲みが class="p-related22__thumbnail"、og:image が /content/dam/site/…_og.jpg で、
    どちらも使われず AI の作り物の自転車になった。発表のページに固有の名前（2261103e）が入った画像は本文の写真として扱う"""

    PAGE = "https://global.honda/jp/news/2026/2261103e.html"
    HTML = """<html><head><meta property="og:image" content="https://global.honda/content/dam/site/global-jp/news-new/cq_img/2026/11/2261103e/2261103e_og.jpg"></head><body>
    <main><div class="p-post22__text"><p>本文</p></div>
    <figure class="p-image23 p-related22__thumbnail"><picture><img src="/content/dam/site/global-jp/news-new/cq_img/2026/11/2261103e/2261103e_01.jpg" alt="RN0"></picture></figure>
    <figure class="p-image23 p-related22__thumbnail"><a href="/jp/news/2026/2261103a.html"><img src="/content/dam/site/global-jp/news-new/cq_img/2026/11/2261103e/2261103e_link01.jpg"></a></figure>
    <aside class="p-sidebar22"><a href="https://global.honda/jp/news/2026/2261103e/image_download.html?from=newslink_media"><img src="/content/dam/x/2261103e/web/2261103e_001L.jpg"></a>
    <a href="https://global.honda/jp/news/2026/2261103e/image_download.html" class="c-button22">ダウンロード</a></aside>
    <div class="related"><img src="/content/dam/other/old.jpg"></div>
    </main></body></html>"""

    def parsed(self):
        parser = images._PageCollector()
        parser.feed(self.HTML)
        return parser

    def test_release_token(self):
        self.assertEqual(images.release_token(self.PAGE), "2261103e")
        self.assertEqual(images.release_token("https://example.com/news/index.html"), "")
        self.assertEqual(images.release_token("https://example.com/news/"), "")

    def test_own_release_body_photo_is_candidate(self):
        urls = [c["url"] for c in images.page_candidates(self.parsed(), self.PAGE, article={"sourceUrl": self.PAGE})]
        self.assertIn("https://global.honda/content/dam/site/global-jp/news-new/cq_img/2026/11/2261103e/2261103e_01.jpg", urls)
        self.assertFalse(any("link01" in u for u in urls))  # 別の発表へのリンクのカードは使わない
        self.assertFalse(any("old.jpg" in u for u in urls))  # 関連記事の画像は今までどおり使わない

    def test_press_kit_in_sidebar(self):
        self.assertEqual(images.kit_pages(self.parsed(), self.PAGE), ["https://global.honda/jp/news/2026/2261103e/image_download.html?from=newslink_media"])

    def test_og_under_site_dir_is_own_release(self):
        og = "https://global.honda/content/dam/site/global-jp/news-new/cq_img/2026/11/2261103e/2261103e_og.jpg"
        self.assertEqual(images.url_problem(og, 0), "サイト共通の既定画像（ファイル名）")
        self.assertTrue(images.own_release_image(og, "2261103e"))

    @unittest.skipIf(images.Image is None, "Pillow が無い")
    def test_trim_white_pillarbox(self):
        canvas = images.Image.new("RGB", (1200, 630), (255, 255, 255))
        photo = images.Image.effect_noise((946, 630), 60).convert("RGB")
        canvas.paste(photo, (127, 0))
        self.assertEqual(images.trim_flat_border(canvas).size, (946, 630))
        # 白地の製品写真（上下も白い）は切らない
        studio = images.Image.new("RGB", (1200, 630), (255, 255, 255))
        studio.paste(images.Image.effect_noise((600, 400), 60).convert("RGB"), (300, 115))
        self.assertEqual(images.trim_flat_border(studio).size, (1200, 630))


class ProductLaunchTest(unittest.TestCase):
    def test_honda_rn0_is_product_launch(self):
        article = {
            "title": "Hondaが初の高級電動アシストマウンテンバイク「RN0」を公開、独ROTWILDと共同開発",
            "genreSlug": "business", "tags": ["自動車・二輪", "電動化", "新製品"], "keywords": ["e-MTB"], "verdict": None,
        }
        self.assertTrue(images.product_launch(article))

    def test_gadget_launch(self):
        self.assertTrue(images.product_launch({"title": "Googleが新型スマホ「Pixel 11」を発表", "genreSlug": "tech-gadget", "tags": []}))

    def test_not_product_launch(self):
        self.assertFalse(images.product_launch({"title": "OpenAIが新しいAIモデルを公開", "genreSlug": "ai", "tags": ["生成AI"]}))
        self.assertFalse(images.product_launch({"title": "日銀が金利を発表", "genreSlug": "investment", "tags": []}))
        self.assertFalse(images.product_launch({"title": "新型自転車の補助金を発表", "genreSlug": "public", "tags": ["新製品"]}))
        self.assertFalse(images.product_launch({"title": "ホンダの決算、二輪が好調", "genreSlug": "business", "tags": ["自動車・二輪"]}))

    def test_ai_prompt_forbids_product(self):
        plan = {"scene": "An empty forest trail at dawn.", "productLaunch": True}
        prompt = images.ai_prompt(plan, {})
        self.assertIn("Do not depict the announced product or any similar device", prompt)
        self.assertNotIn("Do not depict", images.ai_prompt({"scene": "A rice paddy."}, {}))
