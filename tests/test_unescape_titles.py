"""見出しに「&#8211;」のような実体参照が文字のまま残らないこと"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fast_lane  # noqa: E402
import feeds  # noqa: E402


class UnescapeTitlesTest(unittest.TestCase):
    def test_strip_html_unescapes_double_encoded(self):
        self.assertEqual(feeds.strip_html("提供を開始 &amp;#8211; 効率化"), "提供を開始 – 効率化")
        self.assertEqual(feeds.strip_html("A &amp;amp; B"), "A & B")
        self.assertEqual(feeds.strip_html("<b>ふつう</b>の見出し"), "ふつう の見出し")

    def test_fast_meta_titles_are_unescaped(self):
        raw = json.dumps({"url": "https://example.com/a", "title": "提供を開始 &#8211; 効率化", "sourceName": "A&amp;B"})
        [item] = fast_lane.parse_meta(raw)
        self.assertEqual(item["title"], "提供を開始 – 効率化")
        self.assertEqual(item["sourceName"], "A&B")


if __name__ == "__main__":
    unittest.main()
