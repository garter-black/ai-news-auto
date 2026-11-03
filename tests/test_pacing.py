"""1日の上限の数え方（公開中の記事だけ）・時刻ごとの配分・品質ゲートの再挑戦（scripts/pacing.py・scripts/web_articles.py。2026-11-03）"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pacing  # noqa: E402

JST = timezone(timedelta(hours=9))


def at(hour, minute=0, day=3):
    return datetime(2026, 11, day, hour, minute, tzinfo=JST)


class LiveCapCount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for slug in ("live-a", "live-b", "off-live"):
            (self.dir / f"{slug}.json").write_text("{}", "utf-8")
        self.state = {"processed_web": {
            "1": {"slug": "live-a", "at": at(2).isoformat()},
            "2": {"slug": "live-b", "at": at(9).isoformat()},
            "3": {"slug": "retired-thin", "at": at(3).isoformat()},  # 取り下げ（JSON なし）
            "4": {"slug": "off-live", "at": at(4).isoformat(), "offPersona": True},  # 別の上限
            "5": {"skipped": True, "at": at(5).isoformat()},
            "6": {"slug": "live-a", "at": at(23, day=2).isoformat()},  # 昨日
        }}

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_only_live_articles_made_today(self):
        self.assertEqual(pacing.live_today(self.state, at(12), self.dir), 2)

    def test_retired_articles_free_up_the_cap(self):
        (self.dir / "live-b.json").unlink()
        self.assertEqual(pacing.live_today(self.state, at(12), self.dir), 1)

    def test_by_hour_shows_made_and_live(self):
        rows = pacing.by_hour(self.state, at(12), self.dir)
        self.assertEqual(rows[2], (1, 1))
        self.assertEqual(rows[3], (1, 0))
        self.assertEqual(rows[4], (1, 1))
        self.assertNotIn(5, rows)

    def test_web_articles_uses_live_count(self):
        import web_articles
        with patch.object(pacing, "ARTICLES_DIR", self.dir), patch.object(pacing, "datetime") as fake:
            fake.now.return_value = at(12)
            fake.fromisoformat = datetime.fromisoformat
            self.assertEqual(web_articles.generated_today(self.state), 2)


class Pacing(unittest.TestCase):
    def allowed(self, cap, hour, minute=0):
        return pacing.allowed_by(cap, at(hour, minute), night_share=0.3, lookahead_min=60)

    def test_overnight_burst_is_limited(self):
        for hour in (0, 3, 5):
            self.assertEqual(self.allowed(40, hour), 12)

    def test_day_budget_grows_steadily_and_reaches_cap_at_23(self):
        values = [self.allowed(40, h) for h in range(6, 24)]
        self.assertEqual(values, sorted(values))
        self.assertLess(self.allowed(40, 7), 16)
        self.assertGreaterEqual(self.allowed(40, 13) - self.allowed(40, 7), 9)
        self.assertEqual(self.allowed(40, 22), 40)
        self.assertEqual(self.allowed(40, 23, 30), 40)

    def test_reserves_most_of_the_cap_for_daytime(self):
        self.assertGreaterEqual(40 - self.allowed(40, 5, 59), 28)

    def test_run_allowance_takes_the_smaller_limit(self):
        with patch.object(pacing, "NIGHT_SHARE", 0.3), patch.object(pacing, "LOOKAHEAD_MIN", 60):
            allow, by_cap, by_pace = pacing.run_allowance(40, 12, at(3))
            self.assertEqual((allow, by_cap, by_pace), (0, 28, 0))  # 夜中の分は使い切った → 大きなニュースだけ
            allow, _, _ = pacing.run_allowance(40, 12, at(10))
            self.assertGreater(allow, 0)  # 朝からは作れる
            allow, by_cap, _ = pacing.run_allowance(40, 40, at(22))
            self.assertEqual((allow, by_cap), (0, 0))

    def test_today_scenario_live_33_allows_more_in_evening(self):
        # 2026-11-03: 58本作り25本を取り下げ → 公開中33本。18時台でも上限40までの7本を作れる
        with patch.object(pacing, "NIGHT_SHARE", 0.3), patch.object(pacing, "LOOKAHEAD_MIN", 60):
            allow, by_cap, _ = pacing.run_allowance(40, 33, at(19))
        self.assertEqual(by_cap, 7)
        self.assertGreater(allow, 0)

    def test_no_cap_means_unlimited(self):
        self.assertGreaterEqual(pacing.run_allowance(0, 500, at(3))[0], 10**5)


class GateRetry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import web_articles
        cls.w = web_articles

    def item(self, slug="openai-news", **extra):
        return {"id": "x1", "url": "https://example.com/a", "title": "OpenAI が新モデル", "source_slug": slug, **extra}

    def test_bounded_retries_then_gives_up(self):
        state = {}
        now = datetime(2026, 11, 3, 4, 0, tzinfo=timezone.utc)
        it = self.item()
        self.assertEqual(self.w.gate_retry_keep(state, it, "数字の比較相手が違う", now), 1)
        self.assertFalse(self.w.gate_retry_due(state, "x1", now + timedelta(hours=1)))
        self.assertTrue(self.w.gate_retry_due(state, "x1", now + timedelta(hours=self.w.GATE_RETRY_WAIT_HOURS)))
        self.assertEqual(state["gate_retry"]["x1"]["reason"], "数字の比較相手が違う")
        for n in range(2, self.w.GATE_RETRY_MAX + 1):
            self.assertEqual(self.w.gate_retry_keep(state, it, "まだ違う", now), n)
        self.assertIsNone(self.w.gate_retry_keep(state, it, "まだ違う", now))
        self.assertNotIn("x1", state["gate_retry"])
        self.assertTrue(self.w.gate_retry_due(state, "x1", now))

    def test_only_strong_candidates_are_retried(self):
        self.assertTrue(self.w.strong_candidate(self.item("openai-news")))
        self.assertTrue(self.w.strong_candidate(self.item("mhlw")))  # 官公庁
        self.assertFalse(self.w.strong_candidate(self.item("aws-ml-blog")))  # 他社の提供告知（derivative）
        self.assertFalse(self.w.strong_candidate(self.item("mhlw", _off_persona=True)))

    def test_old_records_are_pruned(self):
        now = datetime(2026, 11, 3, tzinfo=timezone.utc)
        state = {"gate_retry": {"old": {"first": (now - timedelta(days=4)).isoformat()},
                                "new": {"first": now.isoformat()}}}
        self.w.prune_gate_retry(state, now)
        self.assertEqual(list(state["gate_retry"]), ["new"])


if __name__ == "__main__":
    unittest.main()
