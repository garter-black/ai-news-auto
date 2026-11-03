"""scripts/inflow_report.py の表示と Threads 投稿との突き合わせ（通信しない）。python3 -m unittest tests/test_inflow_report.py"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import inflow_report as r  # noqa: E402

DATA = {
    "date": "2026-11-03", "views": 9, "visitors": 8,
    "bySource": [{"source": "threads", "views": 6, "visitors": 5}, {"source": "google", "views": 2, "visitors": 2}, {"source": "direct", "views": 1, "visitors": 1}],
    "rows": [
        {"page": "a-article", "source": "threads", "campaign": "classic", "content": "", "views": 3, "visitors": 3},
        {"page": "a-article", "source": "threads", "campaign": "", "content": "", "views": 1, "visitors": 1},
        {"page": "a-article", "source": "threads", "campaign": "sns_video", "content": "short", "views": 2, "visitors": 1},
        {"page": "old-article", "source": "google", "campaign": "", "content": "", "views": 2, "visitors": 2},
        {"page": "_home", "source": "direct", "campaign": "", "content": "", "views": 1, "visitors": 1},
    ],
}
POSTED = {
    "a-article": {"postedAt": "2026-11-02T23:10:00+00:00", "format": "classic", "metrics": {"views": 200}},
    "yesterday-no-visits": {"postedAt": "2026-11-01T23:10:00+00:00", "format": "classic"},
    "tomorrow": {"postedAt": "2026-11-04T01:00:00+00:00"},
}
VIDEOS = {"a-article": {"status": "posted", "variant": "short", "postedAt": "2026-11-03T03:00:00+00:00"}}


class InflowReportTest(unittest.TestCase):
    def test_threads_posts_join(self):
        posts = r.threads_posts(DATA, POSTED, VIDEOS)
        self.assertEqual([(p["slug"], p["kind"], p["visits"], p["utm"]) for p in posts], [
            ("a-article", "classic", 4, 3),
            ("a-article", "動画・短い版", 2, 2),
        ])

    def test_show_is_plain_japanese(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            r.show(DATA, POSTED, VIDEOS)
        text = out.getvalue()
        self.assertIn("外から来た人: 9回（推定 8人）", text)
        self.assertIn("Threads 6（5人）", text)
        self.assertIn("SNS 合計: 6回", text)
        self.assertIn("訪問 4（うち投稿のリンク 3・参照元だけ 1） / 表示 200 → 2.0%", text)
        self.assertIn("動画投稿から: 短い版 2回 / 全部の版 0回", text)
        self.assertIn("トップページ", text)
        self.assertLess(len(text.splitlines()), 25)

    def test_empty_day(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            r.show({"date": "2026-11-01", "views": 0, "visitors": 0, "bySource": [], "rows": []}, {}, {})
        self.assertIn("まだ記録がありません", out.getvalue())


if __name__ == "__main__":
    unittest.main()
