"""編集部の確認の記録を記事 JSON に書き写す処理（scripts/review_sync.py）。本番の KV・GitHub には通信しない。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import review_sync  # noqa: E402


class ReviewSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        review_sync.ARTICLES = self.dir
        (self.dir / "a.json").write_text(json.dumps({"slug": "a", "title": "A"}), encoding="utf-8")
        (self.dir / "b.json").write_text(json.dumps({"slug": "b", "title": "B", "review": {"status": "ok", "reviewedAt": "x", "reviewedBy": "編集部"}}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, slug):
        return json.loads((self.dir / f"{slug}.json").read_text(encoding="utf-8"))

    def test_ok_fix_clear_and_missing(self):
        records = {
            "a": {"status": "ok", "at": "2026-11-03T01:00:00Z"},
            "b": {"status": "clear", "at": "2026-11-03T02:00:00Z"},
            "gone": {"status": "ok", "at": "2026-11-03T03:00:00Z"},
        }
        changed, fixes = review_sync.apply(records, dry_run=False)
        self.assertEqual(len(changed), 2)
        self.assertEqual(self.load("a")["review"], {"status": "ok", "reviewedAt": "2026-11-03T01:00:00Z", "reviewedBy": "編集部"})
        self.assertNotIn("review", self.load("b"))
        # 何度動かしても同じ
        self.assertEqual(review_sync.apply(records, dry_run=False)[0], [])

    def test_fix_is_not_shown_as_reviewed(self):
        changed, fixes = review_sync.apply({"a": {"status": "fix", "at": "t"}}, dry_run=False)
        self.assertEqual(self.load("a")["review"]["status"], "fix")
        self.assertNotIn("reviewedAt", self.load("a")["review"])
        self.assertEqual([f[0] for f in fixes], ["a"])

    def test_dry_run_writes_nothing(self):
        review_sync.apply({"a": {"status": "ok", "at": "t"}}, dry_run=True)
        self.assertNotIn("review", self.load("a"))


if __name__ == "__main__":
    unittest.main()
