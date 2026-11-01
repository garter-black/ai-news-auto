import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import feeds


class AiFeedDetectionTest(unittest.TestCase):
    def test_atom_content_used_when_summary_is_empty(self):
        item = {"title": "Figma Weave workflows", "summary": "", "content": "Figma Weave brings AI generation and editing tools together."}
        self.assertTrue(feeds.is_ai_related(item))

    def test_long_form_llm_in_summary(self):
        item = {"title": "Catching bugs before they land", "summary": "Using large language models to catch bugs on the fly", "content": ""}
        self.assertTrue(feeds.is_ai_related(item))

    def test_title_only_remains_title_only(self):
        item = {"title": "Company event", "summary": "", "content": "AI tools"}
        self.assertFalse(feeds.is_ai_related(item, title_only=True))

    def test_summary_remains_authoritative_and_footer_not_scanned(self):
        self.assertFalse(feeds.is_ai_related({"title": "Company event", "summary": "A new office", "content": "AI"}))
        self.assertFalse(feeds.is_ai_related({"title": "Company event", "summary": "", "content": "x" * 601 + " AI"}))

    def test_feed_reporting_preserves_selection_and_counts_exclusions(self):
        now = datetime.now(timezone.utc)
        def item(id, title, date):
            return {"id": id, "title": title, "summary": "", "content": "", "published_at": date,
                    "url": f"https://example.com/{id}", "priority": 1}
        items = [item("new", "AI update", now), item("done", "AI done", now),
                 item("other", "garden", now), item("old", "AI yesterday", now - timedelta(days=5))]
        feed = {"slug": "sample", "ai_only": True}
        with patch.object(feeds, "parse_feed", return_value=items):
            plain = feeds.collect(3, {"processed_web": {"done": {}}}, [feed])
            report = {}
            observed = feeds.collect(3, {"processed_web": {"done": {}}}, [feed], report=report)
        self.assertEqual([x['id'] for x in plain], ['new'])
        self.assertEqual(observed, plain)
        totals = feeds.report_totals(report)
        self.assertEqual((totals['fetched'], totals['kept'], totals['processed'], totals['not_ai'], totals['undated_or_old']), (4, 1, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
