"""scripts/check_images.py の画像の集め方・判定（通信しない）"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_images as ci  # noqa: E402

PAGE = """
<html><head>
<meta property="og:image" content="https://ai-tech-express.jp/cover/a"/>
<meta name="twitter:image" content="https://ai-tech-express.jp/cover/a"/>
<link rel="preload" as="image" imageSrcSet="/img/k.w400.webp 400w, /img/k.sm.webp 640w" imageSizes="100vw"/>
</head><body>
<img alt="" src="https://ai-tech-express.jp/img/k.jpg" srcSet="https://ai-tech-express.jp/img/k.w400.webp 400w, https://ai-tech-express.jp/img/k.w800.webp 800w"/>
<img src="/cover/b" alt=""/>
<img src="data:image/gif;base64,R0lGOD" alt=""/>
<img src="https://www14.a8.net/0.gif?a8mat=x" width="1" height="1" alt=""/>
<img src="https://thumbnail.image.rakuten.co.jp/x.jpg" alt=""/>
<img src="https://news.example.jp/photo.jpg" alt=""/>
</body></html>
"""


class CollectTest(unittest.TestCase):
    def test_collects_src_srcset_preload_and_meta(self):
        urls = ci.page_images(PAGE, "https://ai-tech-express.jp/")
        self.assertIn("https://ai-tech-express.jp/cover/a", urls)
        self.assertIn("https://ai-tech-express.jp/img/k.sm.webp", urls)  # 先読みの相対 srcset も本番の URL にする
        self.assertIn("https://ai-tech-express.jp/img/k.w800.webp", urls)
        self.assertIn("https://ai-tech-express.jp/cover/b", urls)
        self.assertIn("https://news.example.jp/photo.jpg", urls)
        self.assertEqual(len(urls), len(set(urls)))

    def test_never_touches_ads_or_data_uris(self):
        urls = ci.page_images(PAGE, "https://ai-tech-express.jp/")
        self.assertFalse(any("a8.net" in u or "rakuten" in u or u.startswith("data:") for u in urls))

    def test_relative_srcset_resolves_against_the_page_host(self):
        # 手元のサーバーで開くと /img/ は存在しない → 壊れた画像として見つかる（2026-11-03 の不具合）
        urls = ci.page_images(PAGE, "http://localhost:3479/")
        self.assertIn("http://localhost:3479/img/k.w400.webp", urls)


class JudgeTest(unittest.TestCase):
    def test_image_problem(self):
        self.assertIsNone(ci.image_problem(200, "image/webp", b"x" * 100))
        self.assertIn("404", ci.image_problem(404, "text/plain", b""))
        self.assertIn("画像ではない", ci.image_problem(200, "text/html; charset=utf-8", b"<html>" * 20))
        self.assertEqual(ci.image_problem(200, "image/jpeg", b""), "中身が空")

    def test_hotlink(self):
        self.assertTrue(ci.is_hotlink("https://news.mynavi.jp/article/x/index.jpg"))
        self.assertFalse(ci.is_hotlink("https://ai-tech-express.jp/img/k.jpg"))
        self.assertFalse(ci.is_hotlink("https://i.ytimg.com/vi/abc/maxresdefault.jpg"))
        self.assertFalse(ci.is_hotlink("https://thumbnail.image.rakuten.co.jp/x.jpg"))  # 広告の素材は対象外

    def test_thumb_urls_include_width_variants(self):
        urls = ci.thumb_urls("https://ai-tech-express.jp/img/k-1a2b3c4d.jpg")
        self.assertEqual(len(urls), 5)
        self.assertIn("https://ai-tech-express.jp/img/k-1a2b3c4d.w1200.webp", urls)
        self.assertEqual(ci.thumb_urls("https://i.ytimg.com/vi/abc/maxresdefault.jpg"), ["https://i.ytimg.com/vi/abc/maxresdefault.jpg"])

    def test_owner_lookup_by_youtube_id(self):
        articles = {"v": (Path("v.json"), {"slug": "v", "thumbnail": "https://i.ytimg.com/vi/abc/maxresdefault.jpg"})}
        by_url = ci.owners(articles)
        self.assertEqual(ci.slugs_for("https://i.ytimg.com/vi_webp/abc/sddefault.webp", by_url), {"v"})
        self.assertEqual(ci.slugs_for("https://ai-tech-express.jp/card/v?_internal=1", by_url), {"v"})

    def test_internal_marker(self):
        self.assertEqual(ci.internal("https://ai-tech-express.jp/"), "https://ai-tech-express.jp/?_internal=1")
        self.assertEqual(ci.internal("https://ai-tech-express.jp/a?x=1"), "https://ai-tech-express.jp/a?x=1&_internal=1")


if __name__ == "__main__":
    unittest.main()
