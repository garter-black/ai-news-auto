"""Buffer API の呼び出しを減らす仕組み（scripts/buffer_budget.py と、threads_post の予約の流れ）。ネットワークは使わない"""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import buffer_budget as bb  # noqa: E402
import threads_post as tp  # noqa: E402

NOW = datetime(2026, 11, 3, 6, 40, tzinfo=timezone.utc)


def headers(**values) -> Message:
    msg = Message()
    for k, v in values.items():
        msg[k.replace("_", "-")] = v
    return msg


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for target, attr, value in ((bb, "LEDGER_DIR", root / "buffer_calls"), (tp, "CHANNEL_CACHE", root / "buffer_channels.json")):
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        env = patch.dict(os.environ, {"BUFFER_LEDGER": "pipeline"})
        env.start()
        self.addCleanup(env.stop)
        for name in ("BUFFER_DAILY_CAP", "BUFFER_MONITOR_CAP"):
            os.environ.pop(name, None)
        bb.reset_memory()
        tp._account_memo.clear()
        self.addCleanup(bb.reset_memory)
        self.addCleanup(tp._account_memo.clear)
        self.root = root


class BudgetTest(LedgerCase):
    def test_counts_per_day_and_caps(self):
        with patch.dict(os.environ, {"BUFFER_DAILY_CAP": "3", "BUFFER_MONITOR_CAP": "1"}):
            bb.record("monitor", now=NOW)
            with self.assertRaises(bb.BufferBudgetExceeded):
                bb.check("monitor", NOW)  # 読むだけは上限1回
            bb.check("book", NOW)  # 予約はまだ呼べる
            bb.record("book", now=NOW)
            bb.record("book", now=NOW)
            with self.assertRaises(bb.BufferBudgetExceeded):
                bb.check("book", NOW)
            bb.check("book", NOW + timedelta(days=1))  # UTC の日付が変われば0から

    def test_other_writers_are_summed(self):
        (self.root / "buffer_calls").mkdir()
        (self.root / "buffer_calls" / "video.json").write_text(json.dumps({"day": "2026-11-03", "calls": {"book": 5, "monitor": 0}}))
        (self.root / "buffer_calls" / "old.json").write_text(json.dumps({"day": "2026-11-02", "calls": {"book": 99, "monitor": 0}}))
        bb.record("book", now=NOW)
        self.assertEqual(bb.status(NOW)["total"], 6)
        saved = json.loads((self.root / "buffer_calls" / "pipeline.json").read_text())
        self.assertEqual(saved["calls"]["book"], 1)

    def test_429_blocks_until_retry_after_without_calling(self):
        until = bb.record_429(headers(Retry_After="7200"), '{"extensions":{"code":"RATE_LIMIT_EXCEEDED","window":"24h"}}', now=NOW)
        self.assertEqual(until, NOW + timedelta(hours=2))
        with self.assertRaises(bb.BufferRateLimited):
            bb.check("book", NOW + timedelta(minutes=30))
        bb.check("book", NOW + timedelta(hours=2, seconds=1))  # 待ち時間が過ぎたら呼べる
        saved = json.loads((self.root / "buffer_calls" / "pipeline.json").read_text())
        self.assertEqual(saved["last429"]["window"], "24h")

    def test_429_without_retry_after_uses_window_default(self):
        until = bb.record_429(None, '"window":"15m"', now=NOW)
        self.assertEqual(until, NOW + timedelta(minutes=15))

    def test_rate_limit_headers_keep_reserve_for_booking(self):
        h = headers(RateLimit='"pro-24h";r=30;t=3600, "pro-15m";r=140;t=100', RateLimit_Policy='"pro-24h";q=2000;w=86400, "pro-15m";q=150;w=900')
        self.assertEqual(bb.parse_headers(h)["86400"], {"r": "30", "t": "3600", "q": "2000"})
        bb.record("book", h, now=NOW)
        with self.assertRaises(bb.BufferBudgetExceeded):
            bb.check("monitor", NOW)  # 残り30 < 2000の1/4 → 読むだけは止める
        bb.check("book", NOW)  # 予約は残り3回まで呼べる
        bb.check("monitor", NOW + timedelta(hours=1, seconds=1))  # 窓がリセットされたら戻る

    def test_memory_only_without_ledger_name(self):
        with patch.dict(os.environ, {"BUFFER_LEDGER": ""}):
            bb.record("book", now=NOW)
            self.assertFalse((self.root / "buffer_calls").exists())
            self.assertEqual(bb.status(NOW)["total"], 1)


class Response(io.BytesIO):
    def __init__(self, payload, hdrs=None):
        super().__init__(json.dumps(payload).encode())
        self.headers = hdrs or headers()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class GraphqlTest(LedgerCase):
    def test_429_is_recorded_and_next_call_is_not_sent(self):
        err = urllib.error.HTTPError(
            tp.BUFFER_API, 429, "Too Many", headers(Retry_After="3600"),
            io.BytesIO(b'{"errors":[{"message":"Too many","extensions":{"code":"RATE_LIMIT_EXCEEDED","window":"24h"}}]}'),
        )
        with patch.object(tp.urllib.request, "urlopen", side_effect=err) as opened:
            with self.assertRaises(bb.BufferRateLimited):
                tp.buffer_graphql("k", "query { a }")
            with self.assertRaises(bb.BufferRateLimited):
                tp.buffer_graphql("k", "mutation { b }")
        self.assertEqual(opened.call_count, 1)  # 2回目は Buffer に送らない
        self.assertEqual(bb.status()["total"], 0)  # 429 は回数に数えない

    def test_success_counts_role(self):
        bb.set_role("monitor")
        with patch.object(tp.urllib.request, "urlopen", side_effect=lambda *a, **k: Response({"data": {"x": 1}})):
            tp.buffer_graphql("k", "query { a }")
            tp.buffer_graphql("k", "mutation { b }")
        self.assertEqual(bb.status()["calls"], {"book": 1, "monitor": 1})

    def test_channels_are_cached(self):
        calls = []

        def fake(key, query, variables=None):
            calls.append(query)
            if "account" in query:
                return {"account": {"organizations": [{"id": "org"}]}}
            return {"channels": [{"id": "t1", "service": "threads"}, {"id": "x1", "service": "twitter"}]}

        with patch.object(tp, "buffer_graphql", side_effect=fake):
            self.assertEqual(tp.buffer_channels("k")["threads"]["id"], "t1")
            tp._account_memo.clear()  # 次の実行（別のプロセス）を想定
            self.assertEqual(tp.buffer_threads_channel("k")["id"], "t1")
            self.assertEqual(tp.buffer_org_ids("k"), ["org"])
        self.assertEqual(len(calls), 2)  # 1回目の問い合わせだけ
        cached = json.loads(tp.CHANNEL_CACHE.read_text())
        cached["fetchedAt"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        tp.CHANNEL_CACHE.write_text(json.dumps(cached))
        tp._account_memo.clear()
        with patch.object(tp, "buffer_graphql", side_effect=fake):
            tp.buffer_channels("k")
        self.assertEqual(len(calls), 4)  # 7日より古ければ問い合わせ直す


class SlotTest(unittest.TestCase):
    def setUp(self):
        p = patch.multiple(tp, SLOTS_JST=[(7, 30), (12, 15), (18, 30)], DIGEST_ON=False)
        p.start()
        self.addCleanup(p.stop)

    def test_missed_past_slots_are_skipped_future_ones_booked(self):
        # 12:15 JST の枠が上限で取れなかった。リセット後の 13:00 JST の回では 12:15 は過ぎたので飛ばし、18:30 を取る
        state = {"posted": {}}
        after_reset = datetime(2026, 11, 4, 4, 0, tzinfo=timezone.utc)  # 13:00 JST
        slots = tp.free_slots(state, after_reset)
        self.assertEqual([s.astimezone(tp.JST).strftime("%H:%M") for s in slots], ["18:30"])

    def test_taken_slots_are_not_double_booked(self):
        state = {"posted": {"a": {"dueAt": "2026-11-04T09:30:00+00:00"}}}  # 18:30 JST は予約済み
        self.assertEqual(tp.free_slots(state, datetime(2026, 11, 4, 4, 0, tzinfo=timezone.utc)), [])
        remote = [datetime(2026, 11, 4, 9, 30, tzinfo=timezone.utc)]
        self.assertEqual(tp.free_slots({"posted": {}}, datetime(2026, 11, 4, 4, 0, tzinfo=timezone.utc), extra_taken=remote), [])

    def test_drop_remote_duplicates(self):
        slot = datetime(2026, 11, 4, 9, 30, tzinfo=timezone.utc)
        plan = [({"slug": "a"}, slot), ({"slug": "b"}, slot + timedelta(hours=3))]
        remote = [{"dueAt": "2026-11-04T09:45:00Z", "text": "x"}, {"dueAt": "2026-11-05T00:00:00Z", "text": "https://ai-tech-express.jp/articles/b?utm=1"}]
        self.assertEqual(tp.drop_remote_duplicates(plan, remote), [])


class MainTest(LedgerCase):
    def run_main(self, argv=("threads_post.py",)):
        with patch.object(sys, "argv", list(argv)), patch.dict(os.environ, {"BUFFER_API_KEY": "k", "THREADS_MODE": "live"}):
            return tp.main()

    def test_no_free_slot_means_zero_buffer_calls(self):
        with patch.object(tp, "load_state", return_value={"posted": {}}), patch.object(tp, "free_slots", return_value=[]), \
                patch.object(tp, "buffer_graphql", side_effect=AssertionError("Buffer を呼んではいけない")):
            self.assertEqual(self.run_main(), 0)

    def test_no_article_means_zero_buffer_calls(self):
        with patch.object(tp, "load_state", return_value={"posted": {}}), patch.object(tp, "free_slots", return_value=[NOW + timedelta(hours=2)]), \
                patch.object(tp, "candidates", return_value=[]), patch.object(tp, "hook_bonus", return_value={}), \
                patch.object(tp, "buffer_graphql", side_effect=AssertionError("Buffer を呼んではいけない")):
            self.assertEqual(self.run_main(), 0)

    def test_rate_limited_run_books_nothing_and_keeps_state(self):
        bb.record_429(headers(Retry_After="3600"), "", now=datetime.now(timezone.utc))
        saved = []
        with patch.object(tp, "load_state", return_value={"posted": {}}), patch.object(tp, "free_slots", return_value=[NOW + timedelta(hours=2)]), \
                patch.object(tp, "candidates", return_value=[{"slug": "a", "title": "t"}]), patch.object(tp, "hook_bonus", return_value={}), \
                patch.object(tp, "score", return_value=1), patch.object(tp, "save_state", side_effect=saved.append), \
                patch.object(tp.urllib.request, "urlopen", side_effect=AssertionError("送ってはいけない")):
            self.assertEqual(self.run_main(), 0)
        self.assertEqual(saved, [])

    def test_extra_channels_stop_on_rate_limit(self):
        calls = []

        def create(key, post_input, **kwargs):
            calls.append(post_input["channelId"])
            raise bb.BufferRateLimited("429")

        article = {"slug": "a", "title": "t", "description": "d", "tags": []}
        with patch.object(tp, "create_post", side_effect=create), patch.object(tp, "compose_x", return_value="x"), \
                patch.object(tp, "card_url", return_value="https://ai-tech-express.jp/card/a"):
            out = tp.publish_extra_channels("k", {"twitter": {"id": "x1"}, "facebook": {"id": "f1"}}, "text", "https://l", article,
                                            NOW + timedelta(hours=1))
        self.assertEqual(out, {})
        self.assertEqual(calls, ["x1"])  # Facebook は呼ばない


class LearnTest(unittest.TestCase):
    def test_skips_buffer_when_fetched_recently(self):
        import threads_learn

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "learning.json"
            recent = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
            path.write_text(json.dumps({"metricsFetchedAt": recent}))
            with patch.object(threads_learn, "LEARNING_PATH", path), patch.object(sys, "argv", ["threads_learn.py"]), \
                    patch.dict(os.environ, {"BUFFER_API_KEY": "k"}), patch.object(threads_learn, "load_state", return_value={"posted": {}}), \
                    patch.object(threads_learn, "fetch_metrics", side_effect=AssertionError("Buffer を呼んではいけない")):
                self.assertEqual(threads_learn.main(), 0)
            self.assertEqual(json.loads(path.read_text())["metricsFetchedAt"], recent)


class VideoTest(unittest.TestCase):
    def test_rate_limited_swap_stays_ready(self):
        import sns_video_followup as fu

        with patch.object(tp, "buffer_graphql", side_effect=bb.BufferRateLimited("429")):
            res = fu.edit_scheduled("p", {"total": 30}, "short", "https://ai-tech-express.jp/video/x.mp4", NOW, api_key="k")
        self.assertEqual(res["status"], "ready")
        self.assertIn("deferred", res)


if __name__ == "__main__":
    unittest.main()
