"""IndexNowの対象選定だけ検証。実コンテンツ・キー・ネットワークは使わない。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import indexnow

BASE = "https://example.test"
NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
OLD = "2026-10-29T00:00:00+00:00"
NEW = "2026-10-31T06:00:00+00:00"


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)


class IndexNowGuideSelectionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.guides, self.articles = self.root / "howto", self.root / "articles"
        self.guides.mkdir()
        self.articles.mkdir()
        for attribute, value in [("HOWTO_DIR", self.guides), ("ARTICLES_DIR", self.articles), ("datetime", FixedDatetime)]:
            patcher = patch.object(indexnow, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        network = patch.object(indexnow.urllib.request, "urlopen", side_effect=AssertionError("network is forbidden"))
        self.urlopen = network.start()
        self.addCleanup(network.stop)

    def guide(self, slug="sample", **timestamps):
        (self.guides / f"{slug}.json").write_text(json.dumps({"slug": slug, **timestamps}))

    def selected(self, hours=12):
        result = indexnow.collect_urls(BASE, hours)
        self.urlopen.assert_not_called()
        return result

    def test_partial_update_is_selected_with_old_whole_guide_check(self):
        self.guide(checkedAt=OLD, updatedAt=NEW, createdAt=OLD)
        self.assertIn(BASE + "/guides/sample", self.selected())

    def test_new_check_is_selected_with_old_update(self):
        self.guide(checkedAt=NEW, updatedAt=OLD, createdAt=OLD)
        self.assertIn(BASE + "/guides/sample", self.selected())

    def test_mixed_timezones_use_instant_not_lexical_date(self):
        # 10/30 -05:00の方が、10/31 +09:00より実際には新しい。
        self.guide(checkedAt="2026-10-30T20:30:00-05:00", updatedAt="2026-10-31T03:30:00+09:00")
        self.assertIn(BASE + "/guides/sample", self.selected())
        actual = indexnow.latest_guide_timestamp({"checkedAt": "2026-10-31T10:30:00+09:00",
                                                "updatedAt": "2026-10-30T23:30:00-05:00"})
        self.assertEqual(actual, datetime(2026, 10, 31, 4, 30, tzinfo=timezone.utc))
        self.assertEqual(actual.tzinfo, timezone.utc)

    def test_utc_z_and_creation_fallback(self):
        self.guide(createdAt="2026-10-31T06:00:00Z")
        self.assertIn(BASE + "/guides/sample", self.selected())

    def test_invalid_fields_do_not_hide_another_valid_timestamp(self):
        for value in [None, "", "invalid", "2026-02-30T12:00:00+00:00", 123, [],
                      "2026-10-31T06:00:00", "0001-01-01T00:00:00+09:00"]:
            with self.subTest(value=value):
                self.guide(checkedAt=value, updatedAt=NEW)
                self.assertIn(BASE + "/guides/sample", self.selected())
                self.guide(checkedAt=NEW, updatedAt=value)
                self.assertIn(BASE + "/guides/sample", self.selected())

    def test_missing_invalid_or_timezone_free_dates_are_skipped_without_crash(self):
        for fields in [{}, {"checkedAt": None}, {"checkedAt": "invalid", "updatedAt": []},
                       {"checkedAt": "2026-10-31"}, {"createdAt": "2026-10-31T06:00:00"}]:
            with self.subTest(fields=fields):
                self.guide(**fields)
                self.assertEqual(self.selected(), [BASE, BASE + "/articles", BASE + "/genres/ai"])
        (self.guides / "bad-json.json").write_text("{broken")
        (self.guides / "not-object.json").write_text("[]")
        self.assertEqual(len(self.selected()), 3)

    def test_window_boundary_is_inclusive_and_older_dates_are_excluded(self):
        self.guide("boundary", checkedAt="2026-10-31T09:00:00+09:00", updatedAt=OLD)
        self.guide("expired", checkedAt="2026-10-30T23:59:59.999999+00:00", updatedAt=OLD)
        urls = self.selected()
        self.assertIn(BASE + "/guides/boundary", urls)
        self.assertNotIn(BASE + "/guides/expired", urls)
        self.assertEqual(urls.count(BASE + "/guides"), 1)

    def test_all_mode_keeps_old_valid_guides(self):
        self.guide(checkedAt=OLD, updatedAt=OLD)
        self.assertNotIn(BASE + "/guides/sample", self.selected())
        self.assertIn(BASE + "/guides/sample", self.selected(hours=None))

    def test_thin_offfocus_and_wire_are_excluded(self):
        long = "## 日本で使える？\n" + "a" * 2000
        cases = [
            {"slug": "ai-long", "genreSlug": "ai", "sourceType": "web", "body": long, "generatedAt": NEW},
            {"slug": "ai-short", "genreSlug": "ai", "sourceType": "web", "body": "a" * 1000, "generatedAt": NEW},
            {"slug": "noindex", "indexable": False, "generatedAt": NEW},
            {"slug": "sports", "genreSlug": "sports", "title": "試合結果", "body": long, "generatedAt": NEW},
            {"slug": "wire", "sourceType": "web", "channelSlug": "prtimes", "body": long, "generatedAt": NEW},
            {"slug": "old", "indexable": True, "generatedAt": OLD},
        ]
        for article in cases:
            (self.articles / (article["slug"] + ".json")).write_text(json.dumps(article))
        self.assertEqual(self.selected(), [BASE, BASE + "/articles", BASE + "/genres/ai",
                                           BASE + "/articles/ai-long"])

    def test_search_policy_reasons(self):
        long = "a" * 2000
        jp = "## 無料で使える？\n" + long
        cases = [
            ({"genreSlug": "ai", "body": jp}, None),
            ({"genreSlug": "ai", "body": long, "verdict": {"japan": "ok"}}, None),
            ({"genreSlug": "ai", "body": long}, "no-japan-value"),
            ({"genreSlug": "ai", "body": "a"}, "thin"),
            ({"genreSlug": "public", "title": "生成AIの指針", "body": jp}, None),
            ({"genreSlug": "public", "title": "道路の工事", "body": jp}, "off-focus"),
            ({"genreSlug": "ai", "body": "a", "channelSlug": "prtimes"}, "wire"),
            ({"body": "a", "indexable": True}, None),
            ({"body": jp, "genreSlug": "ai", "indexable": False}, "manual"),
            ({"type": "roundup", "body": "a"}, None),
        ]
        for article, reason in cases:
            with self.subTest(article=article):
                self.assertEqual(indexnow.noindex_reason(article), reason)

    def test_duplicates_keep_longest(self):
        arts = [{"slug": "a", "title": "同じ 題名", "body": "x" * 10},
                {"slug": "b", "title": "同じ題名", "body": "x" * 20},
                {"slug": "c", "title": "別", "sourceUrl": "u", "body": "x"},
                {"slug": "d", "title": "別2", "sourceUrl": "u", "body": "xx"}]
        self.assertEqual(indexnow.duplicate_slugs(arts), {"a", "c"})

    def test_distribution_policy_unchanged(self):
        cases = [
            ({"sourceType": "web", "body": "a"}, False),
            ({"sourceType": "web", "body": "a" * 2000}, True),
            ({"sourceType": "web", "body": "a", "channelSlug": "prtimes", "indexable": True}, True),
            ({"sourceType": "video"}, True),
        ]
        for article, distribution in cases:
            with self.subTest(article=article):
                self.assertEqual(indexnow.is_distribution_eligible(article), distribution)

if __name__ == "__main__":
    unittest.main()
