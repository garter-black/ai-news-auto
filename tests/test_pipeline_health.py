"""scripts/pipeline_health.py の「直った」の判定（2026-11-03 速報レーンで記事ができても issue #8 が開いたままだった件）。外部への通信なし。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pipeline_health as ph  # noqa: E402

STALLED = {
    "status": "stalled", "consecutive_zero": 2, "issue": "8", "reason": "品質ゲート不合格",
    "last_zero_at": "2026-11-03T07:14:43+00:00", "stalled_since": "2026-11-03T07:14:43+00:00",
}


class PipelineHealthRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.articles = base / "articles"
        self.articles.mkdir()
        self.health = base / "pipeline_health.json"
        patcher = patch.object(ph, "HEALTH_PATH", self.health)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(ph, "ARTICLES_DIR", self.articles)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.health.write_text(json.dumps(STALLED), "utf-8")

    def article(self, slug, generated, **extra):
        (self.articles / f"{slug}.json").write_text(json.dumps({"slug": slug, "generatedAt": generated, **extra}), "utf-8")

    def test_fast_lane_article_after_stall_recovers(self):
        self.article("old", "2026-11-03T05:00:00+00:00")
        self.article("secure-samba-mac-drive-app", "2026-11-03T08:39:58.789604+00:00")
        data = ph.recover_from_articles()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["consecutive_zero"], 0)
        self.assertEqual(data["recovered_by"], "secure-samba-mac-drive-app")
        self.assertNotIn("stalled_since", data)
        self.assertEqual(data["issue"], "8")  # issue は alert() が閉じてから消す

    def test_no_new_article_stays_stalled(self):
        self.article("old", "2026-11-03T05:00:00+00:00")
        self.article("dup", "2026-11-03T09:00:00+00:00", duplicateOf="old")  # 重複の印の記事は数えない
        self.assertEqual(ph.recover_from_articles()["status"], "stalled")

    def test_alert_closes_issue_after_recovery(self):
        self.article("new", "2026-11-03T08:40:00+00:00")
        calls = []
        with patch.object(ph, "_gh", side_effect=lambda *a: calls.append(a) or ""):
            self.assertEqual(ph.alert(), 0)
        self.assertEqual(calls[0][:3], ("issue", "close", "8"))
        saved = json.loads(self.health.read_text("utf-8"))
        self.assertEqual(saved["status"], "ok")
        self.assertNotIn("issue", saved)

    def test_record_with_created_recovers_even_without_candidates(self):
        """速報レーンの回は record(created, 0) で直ったことにする"""
        data = ph.record(1, 0)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["consecutive_zero"], 0)


if __name__ == "__main__":
    unittest.main()
