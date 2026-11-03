"""AdSense 再審査のあいだの記事の1日上限（scripts/ramp.py の REVIEW_PERIOD。2026-11-18 に自動で戻る）"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ramp  # noqa: E402


class ReviewPeriodCap(unittest.TestCase):
    def test_cap_is_40_until_nov_18(self):
        self.assertEqual(ramp.review_cap(80, "2026-11-03"), 40)
        self.assertEqual(ramp.review_cap(80, "2026-11-17"), 40)

    def test_restores_on_nov_18(self):
        self.assertEqual(ramp.review_cap(80, "2026-11-18"), 80)

    def test_never_raises_a_lower_cap(self):
        self.assertEqual(ramp.review_cap(25, "2026-11-10"), 25)


if __name__ == "__main__":
    unittest.main()
