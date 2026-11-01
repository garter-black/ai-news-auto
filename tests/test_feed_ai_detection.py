import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
