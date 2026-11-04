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


class NoOutletRequestTest(unittest.TestCase):
    """速報レーンへの依頼（state/radar_requests.json）に、報道機関の記事を出典として入れない（台帳 W31・2026-11-04）。"""

    def test_outlet_and_secondary_requests_are_dropped(self):
        import tempfile
        from datetime import datetime, timezone

        now = datetime(2026, 11, 4, 4, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "radar_requests.json"
            # 前からあった報道の依頼（9to5Google）も消える
            path.write_text('{"items": [{"url": "https://9to5google.com/2026/11/02/google-home-speaker-discount/", "secondary": true, '
                            '"requestedAt": "2026-11-04T03:00:00+00:00"}]}', "utf-8")
            with patch.object(radar, "REQUESTS_PATH", path):
                added = radar.add_requests([
                    {"url": "https://gori.me/macbookpro/172017", "title": "MacBook Pro", "sourceName": "ゴリミー", "secondary": True},
                    {"url": "https://k-tai.watch.impress.co.jp/docs/news/2153244.html", "title": "x", "sourceName": "ケータイ Watch", "secondary": False},
                    {"url": "https://chromestatus.com/release-notes/157", "title": "Chrome 157", "sourceName": "Chrome Platform Status", "secondary": False},
                ], now)
                items = radar.load_requests()["items"]
        self.assertEqual(added, 1)
        self.assertEqual([i["url"] for i in items], ["https://chromestatus.com/release-notes/157"])
