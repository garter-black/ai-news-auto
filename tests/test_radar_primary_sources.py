"""見逃しチェック（scripts/radar.py）が公式の一次情報を見落とさないこと（issue #26・2026-11-04）。

Chrome 157 の安定版は、報道（GIGAZINE）の中に Google 公式の Chrome Releases（Blogger の /YYYY/MM/slug.html）と
Chrome Platform Status の公開メモ（/release-notes/157）へのリンクがあったのに「出典の候補: 見つからず」になっていた。
原因は、公式の URL の形（primary_path_re）が末尾の .html と /release-notes/ を受け付けず、
chromestatus.com・developer.chrome.com を Google の公式として知らなかったこと。
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fast_lane  # noqa: E402
import radar  # noqa: E402

CHROME_TITLE = "「Google Chrome 157」安定版リリース、Android版でオンデバイスの言語判定が可能に"
GIGAZINE_HTML = """
<a href="https://developer.mozilla.org/ja/docs/Web/API/WebGPU_API">MDN</a>
<a href="https://chromereleases.googleblog.com/2026/11/stable-channel-update-for-desktop.html">Chrome Releases</a>
<a href="https://chromestatus.com/release-notes/157">release notes</a>
<a href="https://developer.chrome.com/origintrials#/view_trial/3609072151384031233">trial</a>
"""


class PrimaryUrlShapeTest(unittest.TestCase):
    def test_blogger_html_posts_are_official(self):
        ents = radar.classify(CHROME_TITLE)["entities"]
        for url in [
            "https://chromereleases.googleblog.com/2026/11/stable-channel-update-for-desktop.html",
            "https://android-developers.googleblog.com/2026/10/android-17-beta-new-features.html",
        ]:
            self.assertTrue(radar.official(url, ents), url)

    def test_chrome_release_notes_are_official(self):
        ents = radar.classify(CHROME_TITLE)["entities"]
        self.assertTrue(radar.official("https://chromestatus.com/release-notes/157", ents))
        self.assertTrue(fast_lane.allowed_url("https://chromestatus.com/release-notes/157"))
        self.assertTrue(fast_lane.allowed_url("https://developer.chrome.com/blog/new-in-chrome-157"))

    def test_top_pages_and_reports_are_still_not_official(self):
        ents = ["google"]
        for url in ["https://chromestatus.com/", "https://blog.google/", "https://gigazine.net/news/20261104-google-chrome-157/",
                    "https://developer.chrome.com/origintrials"]:
            self.assertFalse(radar.official(url, ents), url)

    def test_chrome_version_names_google(self):
        self.assertIn("google", radar.classify("Chrome 157 rolls out to stable with on-device language detection")["entities"])
        self.assertNotIn("google", radar.entities_of("Chromebook deals for students"))

    def test_report_resolves_to_chrome_official_source(self):
        with patch.object(radar, "fetch_text", return_value=GIGAZINE_HTML):
            got = radar.resolve({"title": CHROME_TITLE, "links": ["https://gigazine.net/news/20261104-google-chrome-157/"], "published": None})
        self.assertEqual(got["via"], "official")
        self.assertEqual(got["url"], "https://chromestatus.com/release-notes/157")


if __name__ == "__main__":
    unittest.main()
