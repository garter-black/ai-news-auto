"""SNS に投稿した記事の動画を後から出す仕組み（scripts/sns_video_followup.py）と、同じ台本から作る短い版のテスト。
ネット・LLM・描画は使わない。

- フラグは既定ですべて止まっている（何も作らない・置かない・出さない）
- 対象: Threads に投稿済み・窓の中・まだ動画が無い・v3 の対象。点数の高い順。1日・1回の上限
- A/B: 同じ日の中で投稿ごとに short → full → short（日が変われば short から）。期限を過ぎたら既定の版
- 投稿文: 本文の最後に記事のリンク（utm_content=short|full）と音声のクレジット。返信は作らない
- 短い版の台本: hook → 答え → 並べて初めて分かる違い →「◯◯は記事で」。グラフの番号の付け替え
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sns_video_followup as fu  # noqa: E402

NOW = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)  # 日本時間 12:00

REPORT = {
    "title": "Claudeの最上位プランUltra",
    "url": "https://ai-tech-express.jp/articles/claude-ultra",
    "total": 34.6,
    "voice": {"credit": "VOICEVOX:九州そら"},
    "script_v3": {"hook": {"text": "月200ドル、あなたに必要？"}, "summary": "要約すると、ほとんどの人はProで足ります。" * 3},
}


def posted(**hours_ago) -> dict:
    return {"posted": {slug: {"postedAt": (NOW - timedelta(hours=h)).isoformat()} for slug, h in hours_ago.items()}}


class FlagsTest(unittest.TestCase):
    def test_default_off(self):
        f = fu.flags({})
        self.assertFalse(f["followup"] or f["upload"] or f["embed"] or f["post"])
        self.assertEqual((f["daily_max"], f["per_run"], f["store"]), (12, 3, "kv"))

    def test_post_needs_live(self):
        self.assertFalse(fu.flags({"SNS_VIDEO_POST": "on"})["post"])
        self.assertTrue(fu.flags({"SNS_VIDEO_POST": "live"})["post"])

    def test_run_does_nothing_when_off(self):
        rows = fu.run(now=NOW, env={}, plan_only=False, make_versions=lambda *a, **k: self.fail("作ってはいけない"), select=lambda *a: ["a", "b"])
        self.assertTrue(all(r["status"] == "planned" for r in rows))


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
        state = {"videos": {"x": {"at": today, "status": "posted"}, "y": {"at": today, "status": "gate_failed"}, "s": {"at": today, "status": "started"}, "z": {"at": (NOW - timedelta(days=1)).isoformat(), "status": "posted"}}}
        self.assertEqual(fu.used_today(state, NOW), 2)


class VariantTest(unittest.TestCase):
    def rec(self, variant, hours=1, status="posted"):
        at = (NOW - timedelta(hours=hours)).isoformat()
        return {"at": at, "postedAt": at, "status": status, "variant": variant}

    def test_alternate_within_day(self):
        self.assertEqual(fu.pick_variant({"videos": {}}, NOW, "2026-11-10"), "short")
        self.assertEqual(fu.pick_variant({"videos": {"a": self.rec("short")}}, NOW, "2026-11-10"), "full")
        self.assertEqual(fu.pick_variant({"videos": {"a": self.rec("short"), "b": self.rec("full")}}, NOW, "2026-11-10"), "short")
        # 不合格（出していない）は数えない。前の日の分も数えない（日ごとに short から）
        state = {"videos": {"a": self.rec("short"), "b": self.rec("full", status="gate_failed"), "c": self.rec("full", hours=30)}}
        self.assertEqual(fu.pick_variant(state, NOW, "2026-11-10"), "full")

    def test_after_ab(self):
        self.assertEqual(fu.pick_variant({"videos": {"a": self.rec("short")}}, NOW, "2026-11-02"), "short")
        self.assertEqual(fu.pick_variant({"videos": {}}, NOW, "2026-11-02", "full"), "full")


class PostTimingTest(unittest.TestCase):
    F = {"ab_until": "2026-11-10", "after_ab": "short"}

    def ready(self, minutes_since_text, variant_ok=("short", "full")):
        post = {"title": "t", "url": "https://ai-tech-express.jp/articles/x", "total": 30, "voice": {"credit": "VOICEVOX:九州そら"}, "script_v3": {"hook": {"text": "問い？"}, "summary": "要約すると、答えです。"}}
        return {
            "status": "ready", "textAt": (NOW - timedelta(minutes=minutes_since_text)).isoformat(),
            "versions": {k: {"ok": k in variant_ok, "post": post} for k in ("short", "full")},
            "urls": {k: f"https://ai-tech-express.jp/video/x-{k}.mp4" for k in variant_ok},
        }  # fmt: skip

    def test_published_at_uses_due(self):
        self.assertEqual(fu.published_at({"postedAt": "2026-11-02T10:00:00+00:00", "dueAt": "2026-11-02T12:00:00+00:00"}).hour, 12)

    def test_future_due_is_not_candidate(self):
        p = {"posted": {"a": {"postedAt": (NOW - timedelta(hours=1)).isoformat(), "dueAt": (NOW + timedelta(hours=2)).isoformat()}}}
        self.assertEqual(fu.candidates(p, {"videos": {}}, NOW, 12, load_article=lambda s: {}, eligible=lambda a: True, score=lambda a: 1), [])

    def test_quiet_hours_and_gap(self):
        night = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)  # 日本時間 3:00
        self.assertFalse(fu.can_post_now({"videos": {}}, night)[0])
        recent = {"videos": {"a": {"status": "posted", "postedAt": (NOW - timedelta(minutes=10)).isoformat()}}}
        self.assertFalse(fu.can_post_now(recent, NOW)[0])
        self.assertTrue(fu.can_post_now({"videos": {}}, NOW)[0])

    def test_post_one_oldest_ready_and_variant(self):
        state = {"videos": {"new": self.ready(5), "old": self.ready(90), "mid": self.ready(40)}}
        sent = []
        out = fu.post_one(state, NOW, self.F, lambda slug, body, url: sent.append((slug, url)) or {"status": "posted", "bufferId": "b1", "postedAt": NOW.isoformat()})
        self.assertEqual(out["slug"], "old")  # 文面から20分たっていない new は待つ
        self.assertEqual(out["variant"], "short")  # その日の最初は短い版
        self.assertIn("utm_content=short", out["body"])
        self.assertEqual(sent, [("old", "https://ai-tech-express.jp/video/x-short.mp4")])
        # 間隔（40分）をあけるので、同じ回・すぐ次の回には出さない
        self.assertIsNone(fu.post_one(state, NOW + timedelta(minutes=5), self.F, lambda *a: self.fail("出してはいけない")))
        out2 = fu.post_one(state, NOW + timedelta(minutes=45), self.F, lambda slug, body, url: {"status": "posted", "postedAt": (NOW + timedelta(minutes=45)).isoformat()})
        self.assertEqual((out2["slug"], out2["variant"]), ("mid", "full"))  # 次は全部の版

    def test_post_one_falls_back_to_passing_version(self):
        state = {"videos": {"a": self.ready(60, variant_ok=("full",))}}
        out = fu.post_one(state, NOW, self.F, lambda *a: {"status": "posted", "postedAt": NOW.isoformat()})
        self.assertEqual(out["variant"], "full")


class ComposeTest(unittest.TestCase):
    def test_post_text_link_in_body(self):
        body = fu.compose_post(REPORT, "full")
        self.assertTrue(body.startswith("月200ドル、あなたに必要？"))
        self.assertIn("35秒", body)
        self.assertIn("utm_source=threads&utm_medium=social&utm_campaign=sns_video&utm_content=full", body)
        self.assertIn("音声：VOICEVOX:九州そら", body)  # クレジットは投稿の文にも必ず
        self.assertNotIn("ざっくり", body)
        self.assertLessEqual(len(body), fu.MAX_CHARS)

    def test_buffer_input_no_reply(self):
        inp = fu.buffer_input("ch1", "本文", "https://ai-tech-express.jp/video/x.mp4", "AI")
        thread = inp["metadata"]["threads"]["thread"]
        self.assertEqual(len(thread), 1)  # 返信は作らない（リンクは本文）
        self.assertEqual(thread[0]["assets"], [{"video": {"url": "https://ai-tech-express.jp/video/x.mp4"}}])
        self.assertEqual(inp["mode"], "shareNow")

    def test_video_name(self):
        a, b = fu.video_name("claude-ultra", b"x"), fu.video_name("claude-ultra", b"y", "jpg")
        self.assertTrue(a.startswith("claude-ultra-") and a.endswith(".mp4") and b.endswith(".jpg"))
        self.assertNotEqual(a[:-4], b[:-4])  # 中身が変われば名前も変わる（1年キャッシュしても古い動画が出ない）

    def test_draft_test_does_not_send_without_yes(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(REPORT, fh, ensure_ascii=False)
        res = fu.draft_test(Path(fh.name), "https://ai-tech-express.jp/video/x.mp4")
        self.assertFalse(res["sent"])
        self.assertTrue(res["input"]["saveToDraft"])

    def test_ab_rows(self):
        state = {"videos": {
            "a": {"status": "posted", "variant": "short", "bufferId": "p1"},
            "b": {"status": "posted", "variant": "short", "bufferId": "p2"},
            "c": {"status": "posted", "variant": "full", "bufferId": "p3"},
            "d": {"status": "gate_failed", "variant": "full"},
        }}  # fmt: skip
        metrics = {"p1": {"metrics": {"reactions": 4, "comments": 1, "views": 100}}, "p2": {"metrics": {"reactions": 2, "views": 50}}, "p3": {"metrics": {"reposts": 3, "views": 80}}}
        rows = fu.ab_rows(state, metrics, {("a", "short"): 6, ("c", "full"): 2})
        self.assertEqual(rows["short"], {"posts": 2, "visits": 3.0, "likes": 3.0, "replies": 0.5, "reposts": 0.0, "views": 75.0})
        self.assertEqual(rows["full"]["posts"], 1)
        self.assertEqual(rows["full"]["reposts"], 3.0)


try:  # 短い版の台本は shorts_v3（numpy・Pillow）を使う。無い環境では飛ばす
    import numpy  # noqa: F401
    from PIL import Image  # noqa: F401

    HEAVY = True
except ImportError:  # pragma: no cover
    HEAVY = False


@unittest.skipUnless(HEAVY, "numpy / Pillow が必要")
class ShortScriptTest(unittest.TestCase):
    SCRIPT = {
        "hook": {"text": "月200ドル、必要？", "speech": "クロードの最上位、月200ドル。あなたに必要？"},
        "beats": [
            {"label": "答え", "speech": "要約すると、ほとんどの人はプロ、月20ドルで足ります。"},
            {"label": "プランの比較", "speech": "料金は3段階です。プロが20ドル、マックスが100ドルでプロの5倍、ウルトラが200ドルで10倍使えます。"},
            {"label": "ポイント", "speech": "ポイントは、1ドルあたりに使える量はマックスもウルトラも同じこと。違いは5時間ごとの上限がないことです。"},
            {"label": "必要な人", "speech": "朝から営業データを集めて提案資料を仕上げる人です。"},
            {"label": "確かめ方", "speech": "設定の使用状況で分かります。"},
        ],
        "ladder": {"beat": 2, "rows": [{"name": "Pro"}, {"name": "Max"}]},
        "flows": [{"beat": 4, "steps": ["a", "b"]}],
        "short_beats": [1, 3],
        "conclusion": {"text": "迷ったらPro", "speech": "迷ったらプロから。"},
    }

    def test_short_keeps_answer_and_insight(self):
        import shorts_v3 as v3

        s = v3.short_script(self.SCRIPT)
        labels = [b["label"] for b in s["beats"]]
        self.assertEqual(labels[0], "答え")
        self.assertIn("ポイント", labels)
        end = s["conclusion"]
        self.assertEqual(end["text"], "くわしくは記事で")
        self.assertTrue(end["speech"].startswith("必要な人と確かめ方は、記事でくわしく解説しています。"))
        self.assertIn("投稿のリンク", end["speech"])  # 温かく、リンクへ誘う（押しつけない）
        self.assertIn("投稿のリンク", end["cta"])
        self.assertEqual(s["_variant"], "short")
        self.assertEqual(s["flows"], [])  # 使わない場面の絵は外す
        if "プランの比較" in labels:  # 短すぎるときはグラフの場面を足す。番号は付け替える
            self.assertEqual(s["ladder"]["beat"], labels.index("プランの比較") + 1)
        self.assertEqual(self.SCRIPT["beats"][0]["label"], "答え")  # 元の台本は変えない


if __name__ == "__main__":
    unittest.main()
