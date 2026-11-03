"""Claude API の1日の回数の上限が、月の予算に比例して広がること"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import llm  # noqa: E402


class DailyCapsTest(unittest.TestCase):
    def test_default_budget_keeps_base_caps(self):
        with mock.patch.dict(os.environ, {"CLAUDE_API_MONTHLY_BUDGET_USD": "95"}, clear=False):
            os.environ.pop("CLAUDE_API_DAILY_CALLS_CORE", None)
            self.assertEqual(llm._daily_calls_cap("core"), llm.API_DAILY_CALLS["core"])

    def test_ultra_budget_doubles_caps(self):
        with mock.patch.dict(os.environ, {"CLAUDE_API_MONTHLY_BUDGET_USD": "190"}, clear=False):
            os.environ.pop("CLAUDE_API_DAILY_CALLS_CORE", None)
            self.assertEqual(llm._daily_calls_cap("core"), llm.API_DAILY_CALLS["core"] * 2)

    def test_env_override_wins(self):
        with mock.patch.dict(os.environ, {"CLAUDE_API_MONTHLY_BUDGET_USD": "190", "CLAUDE_API_DAILY_CALLS_CORE": "7"}):
            self.assertEqual(llm._daily_calls_cap("core"), 7)

    def test_small_budget_never_shrinks_below_base(self):
        with mock.patch.dict(os.environ, {"CLAUDE_API_MONTHLY_BUDGET_USD": "50"}, clear=False):
            os.environ.pop("CLAUDE_API_DAILY_CALLS_CORE", None)
            self.assertEqual(llm._daily_calls_cap("core"), llm.API_DAILY_CALLS["core"])


if __name__ == "__main__":
    unittest.main()
