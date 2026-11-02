"""SNS に投稿した記事の動画を後から出す仕組み（scripts/sns_video_followup.py）のテスト。ネット・LLM・描画は使わない。

- フラグは既定ですべて止まっている（何も作らない・置かない・出さない）
- 対象: Threads に投稿済み・窓の中・まだ動画が無い・v3 の対象。点数の高い順。1日の上限
- 投稿文（本文・返信のクレジット）と Buffer の入力の形
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sns_video_followup as fu  # noqa: E402

NOW = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)  # 日本時間 12:00


def posted(**hours_ago) -> dict:
    return {"posted": {slug: {"postedAt": (NOW - timedelta(hours=h)).isoformat()} for slug, h in hours_ago.items()}}


class FlagsTest(unittest.TestCase):
    def test_default_off(self):
        f = fu.flags({})
        self.assertFalse(f["followup"])
        self.assertFalse(f["upload"])
        self.assertFalse(f["post"])
        self.assertEqual(f["daily_max"], 2)

    def test_post_needs_live(self):
        self.assertFalse(fu.flags({"SNS_VIDEO_POST": "on"})["post"])
        self.assertTrue(fu.flags({"SNS_VIDEO_POST": "live"})["post"])


class SelectTest(unittest.TestCase):
    def pick(self, p, state=None, window=12):
        scores = {"a": 1, "b": 5, "c": 3, "old": 9, "news": 9}
        return fu.candidates(
            p, state or {"videos": {}}, NOW, window,
            load_article=lambda s: {"slug": s},
            eligible=lambda a: a["slug"] != "news",  # ニュースサイトが出典の記事は v3 の対象外
            score=lambda a: scores[a["slug"]],
        )  # fmt: skip

    def test_order_window_and_eligibility(self):
        self.assertEqual(self.pick(posted(a=1, b=2, c=3, old=20, news=1)), ["b", "c", "a"])

    def test_skip_done(self):
        self.assertEqual(self.pick(posted(a=1, b=2), {"videos": {"b": {"status": "gate_failed"}}}), ["a"])

    def test_daily_count(self):
        today = NOW.isoformat()
        state = {"videos": {"x": {"at": today, "status": "posted"}, "y": {"at": today, "status": "gate_failed"}, "z": {"at": (NOW - timedelta(days=1)).isoformat(), "status": "posted"}}}
        self.assertEqual(fu.used_today(state, NOW), 1)


class ComposeTest(unittest.TestCase):
    REPORT = {
        "title": "Claudeの最上位プランUltra",
        "url": "https://ai-tech-express.jp/articles/claude-ultra",
        "total": 81.3,
        "voice": {"credit": "VOICEVOX:九州そら"},
        "pages": [{"host": "support.claude.com"}, {"host": "claude.com"}],
        "script_v3": {"hook": {"text": "月200ドル、あなたに必要？"}, "summary": "要約すると、ほとんどの人はProで足ります。"},
    }

    def test_post_text(self):
        body, reply = fu.compose_post(self.REPORT)
        self.assertTrue(body.startswith("月200ドル、あなたに必要？"))
        self.assertIn("81秒", body)
        self.assertIn("要約すると", body)
        self.assertNotIn("ざっくり", body)
        self.assertLessEqual(len(body), fu.MAX_CHARS)
        self.assertIn("音声：VOICEVOX:九州そら", reply)  # クレジットは投稿の文にも必ず
        self.assertIn(self.REPORT["url"], reply)

    def test_buffer_input(self):
        body, reply = fu.compose_post(self.REPORT)
        inp = fu.buffer_input("ch1", body, reply, "https://media.example/v.mp4", self.REPORT["url"], "t", "AI")
        first, second = inp["metadata"]["threads"]["thread"]
        self.assertEqual(first["assets"], [{"video": {"url": "https://media.example/v.mp4"}}])
        self.assertEqual(second["metadata"]["threads"]["linkAttachment"]["url"], self.REPORT["url"])
        self.assertEqual(inp["mode"], "shareNow")

    def test_video_name_and_store(self):
        a, b = fu.video_name("claude-ultra", b"x"), fu.video_name("claude-ultra", b"y")
        self.assertTrue(a.startswith("claude-ultra-") and a.endswith(".mp4"))
        self.assertNotEqual(a, b)  # 中身が変われば名前も変わる（1年キャッシュしても古い動画が出ない）
        self.assertEqual(fu.flags({})["store"], "kv")

    def test_draft_test_does_not_send_without_yes(self):
        import json
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(self.REPORT, fh, ensure_ascii=False)
        res = fu.draft_test(Path(fh.name), "https://ai-tech-express.jp/video/x.mp4")
        self.assertFalse(res["sent"])
        self.assertTrue(res["input"]["saveToDraft"])
        self.assertEqual(res["input"]["mode"], "addToQueue")

    def test_run_does_nothing_when_off(self):
        rows = fu.run(now=NOW, env={}, plan_only=False)
        self.assertTrue(all(r["status"] == "planned" for r in rows))


if __name__ == "__main__":
    unittest.main()
