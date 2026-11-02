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


if __name__ == "__main__":
    unittest.main()
