"""デプロイ直後の1回目の表示の確かめ（scripts/cold_check.py・台帳 W29）の判定。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cold_check  # noqa: E402

BASE = "https://ai-tech-express.jp"


def result(ttfb, status=200, cache="HIT", error=""):
    return {"path": "/x", "status": status, "ttfb": ttfb, "cache": cache, "ver": "v", "error": error}


class JudgeTest(unittest.TestCase):
    def test_page_built_on_first_request_fails_even_when_fast(self):
        # 遅さの根本: ビルドで作っておらず、アクセスが来てから描いている（2026-11-03 に 30〜90 秒待たされた）
        verdict, reason = cold_check.judge(result(0.6, cache="MISS"), None)
        self.assertEqual(verdict, "fail")
        self.assertIn("MISS", reason)

    def test_slow_twice_fails_and_slow_once_warns(self):
        self.assertEqual(cold_check.judge(result(2.5), result(2.4))[0], "fail")
        self.assertEqual(cold_check.judge(result(2.5), result(0.4))[0], "warn")
        self.assertEqual(cold_check.judge(result(1.9), None)[0], "ok")
        self.assertEqual(cold_check.LIMIT, 2.0)

    def test_errors_and_timeouts_fail(self):
        self.assertEqual(cold_check.judge(result(30.0, status=0, error="TimeoutError"), None)[0], "fail")
        self.assertEqual(cold_check.judge(result(0.2, status=503, cache=""), None)[0], "fail")

    def test_stale_and_plain_pages_pass(self):
        self.assertEqual(cold_check.judge(result(0.5, cache="STALE"), None)[0], "ok")
        self.assertEqual(cold_check.judge(result(0.5, cache=""), None)[0], "ok")


class SampleTest(unittest.TestCase):
    def test_sitemap_paths_and_always_checked_pages(self):
        xml = f"<urlset><url><loc>{BASE}</loc></url><url><loc>{BASE}/articles/a</loc></url><url><loc>https://other.example/x</loc></url></urlset>"
        self.assertEqual(cold_check.sitemap_paths(xml, BASE), ["/", "/articles/a"])
        paths = [f"/articles/{i}" for i in range(100)] + ["/products/chatgpt-voice"]
        chosen = cold_check.choose(paths, 20, seed="s")
        # 2026-11-04 の点検で遅かったページは毎回測る
        for always in ("/articles/openai-gpt-6-astra-comprehensive-guide-matome", "/products/chatgpt-voice"):
            self.assertIn(always, chosen)
        self.assertEqual(len(chosen), len(set(chosen)))
        self.assertEqual(len(chosen), len(cold_check.ALWAYS) + 20)

    def test_requests_are_marked_internal_and_bypass_the_edge_cache(self):
        self.assertEqual(cold_check.HEADERS["X-Saisoku-Internal"], "1")
        self.assertEqual(cold_check.with_params("/a", "9"), "/a?_internal=1&_cold=9")
        self.assertEqual(cold_check.with_params("/a?b=1"), "/a?b=1&_internal=1")


class WorkflowTest(unittest.TestCase):
    def test_deploy_runs_the_check_after_publishing(self):
        wf = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "deploy_cloudflare.yml").read_text("utf-8")
        self.assertIn("scripts/cold_check.py version", wf)
        self.assertIn("scripts/cold_check.py check --previous", wf)
        self.assertLess(wf.index("npx opennextjs-cloudflare deploy"), wf.index("scripts/cold_check.py check"))


if __name__ == "__main__":
    unittest.main()
