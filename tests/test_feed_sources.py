import sys
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import feeds
import radar


def _item(id):
    return {"id": id, "title": f"AI {id}", "summary": "", "content": "", "published_at": None,
            "url": f"https://example.com/{id}", "priority": 1}


class UndatedFeedTest(unittest.TestCase):
    def test_first_read_is_baseline_then_new_items_use_first_seen(self):
        feed = {"slug": "nodate", "ai_only": True}
        state = {"processed_web": {}}
        with patch.object(feeds, "parse_feed", return_value=[_item("a"), _item("b")]):
            self.assertEqual(feeds.collect(3, state, [feed]), [])
        with patch.object(feeds, "parse_feed", return_value=[_item("c"), _item("a"), _item("b")]):
            got = feeds.collect(3, state, [feed])
        self.assertEqual([x["id"] for x in got], ["c"])
        self.assertTrue(got[0]["published_at"] <= datetime.now(timezone.utc))

    def test_empty_state_dict_is_kept(self):
        state = {}
        with patch.object(feeds, "parse_feed", return_value=[_item("a")]):
            feeds.collect(3, state, [{"slug": "nodate"}])
        self.assertIn("nodate", state["undated_first_seen"])


class SharedRegistryTest(unittest.TestCase):
    def test_registry_rss_feeds_are_collected_without_duplicates(self):
        urls = [f["url"].rstrip("/") for f in feeds.FEEDS]
        self.assertEqual(len(urls), len(set(urls)))
        slugs = {f["slug"] for f in feeds.FEEDS}
        self.assertIn("elevenlabs-blog", slugs)
        self.assertNotIn("github-changelog", slugs)  # feeds: false
        self.assertNotIn("rakuten-press", slugs)  # genre 付きは幅広い話題側

    def test_every_ai_feed_host_is_official_for_radar(self):
        hosts = radar.rules()["primary_hosts"]
        for f in feeds.FEEDS:
            host = urllib.parse.urlsplit(f["url"]).netloc.lower().removeprefix("www.")
            ok = any(host == h["host"] or host.endswith("." + h["host"]) for h in hosts)
            self.assertTrue(ok, f"{f['slug']} ({host}) がレーダーの公式ホストに無い")


if __name__ == "__main__":
    unittest.main()
