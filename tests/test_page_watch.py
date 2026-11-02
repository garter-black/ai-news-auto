"""料金・リリースノートの見張り（scripts/page_watch.py）。取得は差し替え、状態は一時ファイル。ネットワークは使わない。"""
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import page_watch as w  # noqa: E402

NOTES_V1 = """# Release notes
# October 21, 2026
## Claude Duet research preview for Pro and Max plans
Claude Duet is rolling out as a research preview for Pro and Max plans. It lets two people work together with Claude in one chat, and it keeps each person's settings separate.
# October 7, 2026
## Older item
Something older that already happened and should not be offered again as a candidate for an article.
"""
NOTES_V2 = """# Release notes
# November 1, 2026
## Max 20x is now called Ultra
The Max 20x plan is now called Ultra. The price stays at $200 per month, and the 5-hour usage limit is removed for Ultra subscribers worldwide.
# October 31, 2026
## Small fix
Fixed a typo in the settings screen so that the label reads correctly in every language we support today.
""" + NOTES_V1.split("\n", 1)[1]
PRICING_V1 = "\n".join(["### Free", "$0", "### Pro", "$20 per month", "### Max 20x", "$200 per month"] + [f"Feature line {i}" for i in range(12)])
PRICING_V2 = PRICING_V1.replace("### Max 20x", "### Ultra")
WATCHES = [
    {"id": "notes", "name": "Claude リリースノート", "product_name": "Claude", "mode": "dated", "url": "https://example.test/notes"},
    {"id": "pricing", "name": "Claude 料金", "product_name": "Claude", "mode": "diff", "url": "https://example.test/pricing"},
]


class PageWatchTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for name, value in [("STATE_PATH", Path(d.name) / "page_watch.json"), ("load_watches", lambda: WATCHES)]:
            p = patch.object(w, name, value)
            p.start()
            self.addCleanup(p.stop)
        net = patch.object(w.feeds, "fetch_article_text", side_effect=AssertionError("network"))
        net.start()
        self.addCleanup(net.stop)
        self.pages = {"notes": NOTES_V1, "pricing": PRICING_V1}

    def run_check(self, day):
        return w.check(now=datetime(2026, 11, day, 3, tzinfo=timezone.utc), fetcher=lambda x: self.pages[x["id"]])

    def test_dates_are_parsed_newest_first(self):
        entries = w.dated_entries(NOTES_V2)
        self.assertEqual([d for d, _ in entries], ["2026-11-01", "2026-10-31", "2026-10-21", "2026-10-07"])
        self.assertEqual(w.parse_date_line("2026.10.13"), datetime(2026, 10, 13).date())
        self.assertEqual(w.parse_date_line("### October 30, 2026"), datetime(2026, 10, 30).date())

    def test_first_run_is_baseline_then_new_significant_entry_only(self):
        self.assertEqual(self.run_check(1), [])
        self.pages["notes"] = NOTES_V2
        items = self.run_check(1)
        self.assertEqual(len(items), 1)
        self.assertIn("Ultra", items[0]["title"])
        self.assertTrue(items[0]["url"].endswith("#entry-2026-11-01"))
        self.assertTrue(items[0]["published_guessed"])  # 日付だけの見出しは発表の時刻にしない
        # 2回目は候補に重ねない（未処理の候補としてだけ残る）
        again = self.run_check(1)
        self.assertEqual([i["id"] for i in again], [items[0]["id"]])
        # web_articles 用: 処理済みなら出さない・本文は項目だけ
        with patch.object(w, "fetch_page", lambda x: self.pages[x["id"]]):
            cands = w.candidates({}, now=datetime(2026, 11, 1, 3, tzinfo=timezone.utc))
            self.assertEqual([c["id"] for c in cands], [items[0]["id"]])
            self.assertTrue(cands[0]["watch"])
            self.assertIn("Ultra", cands[0]["_page"]["text"])
            self.assertEqual(w.candidates({items[0]["id"]: {}}, now=datetime(2026, 11, 1, 3, tzinfo=timezone.utc)), [])

    def test_pricing_change_needs_two_checks_and_price_or_plan_lines(self):
        self.run_check(1)
        self.pages["pricing"] = PRICING_V2
        self.assertEqual(self.run_check(1), [])  # 1回目は保留
        items = self.run_check(2)
        self.assertEqual(len(items), 1)
        self.assertIn("Ultra", items[0]["content"])
        self.assertIn("削除: Max 20x", items[0]["content"])
        self.assertIn("追加: Ultra", items[0]["content"])
        # 金額・プランに関係ない行だけの変更は候補にしない
        self.pages["pricing"] = PRICING_V2.replace("Feature line 3", "Feature line three")
        self.run_check(3)
        self.assertEqual(len(self.run_check(3)), 1)  # 前回の候補が残っているだけ

    def test_reordered_lines_are_not_a_change(self):
        self.run_check(1)
        lines = PRICING_V1.split("\n")
        self.pages["pricing"] = "\n".join(lines[6:] + lines[:6])
        self.run_check(1)
        state = w.load_state()
        self.assertNotIn("pending", state["pages"]["pricing"])


if __name__ == "__main__":
    unittest.main()
