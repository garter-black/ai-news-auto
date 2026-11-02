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
