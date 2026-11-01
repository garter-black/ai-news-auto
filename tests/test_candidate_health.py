import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import candidate_health as health


class CandidateHealthTests(unittest.TestCase):
    def test_counts_requires_both_parts(self):
        line = '[info] 大きなニュース 0 件（上限 2）/ 候補 AI 0 件（上限 1）/ 幅広い話題 0 件（上限 1）'
        self.assertIsNone(health.counts(line))
        self.assertEqual(health.counts(line + '\n[info] 話題キュー 1 件 / 上限 1'), (0, 0, 0, 1))

    def test_evidence_window_and_nonzero(self):
        now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        samples = [(now - timedelta(minutes=m), (0, 0, 0, 0)) for m in (5, 40, 75, 110, 145)]
        self.assertEqual(health.assess(samples, now), 'zero')
        self.assertEqual(health.assess(samples[:4], now), 'unknown')
        samples[-1] = (samples[-1][0], (0, 1, 0, 0))
        self.assertEqual(health.assess(samples, now), 'nonzero')


if __name__ == '__main__':
    unittest.main()
