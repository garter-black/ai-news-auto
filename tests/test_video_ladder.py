"""SNS 動画の段（scripts/video_ladder.py）のテスト。ネット・LLM・VOICEVOX は使わない（声はテスト用の合成音・画像のレビューは差し替え）。

- 段の順番（L1 → L2 → L3A）・最初に合格した段を使う・版ごとに選ぶ
- どの段も同じ合格の基準: qa（位置の検査＋画像のレビュー）が無い・レビューの代わりに決まりで通した・承認の形でない動画は合格にしない
- 時間の予算: L1 が長引いたら L2 を飛ばして L3A へ
- L3A（記事の文だけの承認の形の台本）: 直近の記事で作れて、決まった見出し・硬い言い回し・要約の始まり・長すぎる場面が無い
- 小さく書き出した L3A が描いた位置の検査に通り、レビューができなければ不合格
- 承認の形の割合（9割未満で issue）・別プロセスの時間切れ

  python3 -m unittest tests/test_video_ladder.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import numpy  # noqa: F401
    from PIL import Image  # noqa: F401

    import video_gate as gate
    import video_ladder as vl

    READY = shutil.which("ffmpeg") is not None
    IMPORTED = True
except ImportError:  # pragma: no cover
    READY = IMPORTED = False

SLUG = "github-copilot-jetbrains-air"


def article() -> dict:
    return json.loads((ROOT / "content" / "articles" / f"{SLUG}.json").read_text("utf-8"))


def fake_runner(level: str, results: dict, calls: list, qa: bool = True, advance=None, fail_reason: str = "", how: str = "", layout: str = "screen-left-headline"):
    """段の代わり: results の版だけ合格（mp4 を置く）。calls に (段, 版, 残り秒) を記録"""

    def fn(slug, level_dir, need, article, prev, remaining):
        calls.append((level, tuple(need), round(remaining, 1)))
        if advance:
            advance()
        out = {}
        for v in need:
            ok = bool(results.get(v))
            if ok:
                (level_dir / f"{vl._out_name(slug, v)}.mp4").write_bytes(b"fake")
            q = {"geometry": qa, "vision": qa, "version": 1, **({"vision_how": how} if how else {})}
            rep = {"slug": slug, "total": 40.0, "base_layout": layout, "gate": {"ok": ok, "reasons": [] if ok else [f"{level} の検査に落ちた"], "qa": q}}
            if fail_reason:
                rep["failReason"] = fail_reason
            out[v] = rep
        return out

    return fn


@unittest.skipUnless(IMPORTED, "numpy / Pillow が無い")
class LadderOrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.a = article()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_ladder(self, runners, **kw):
        return vl.make_versions(SLUG, self.tmp, article=self.a, runners=runners, isolate=False, **kw)

    def test_levels_are_all_approved_template(self):
        self.assertEqual(vl.LEVELS, ("L1", "L2", "L3A"))
        self.assertEqual(set(vl.LEVELS), set(vl.APPROVED_LEVELS))

    def test_order_and_first_passing_level(self):
        calls: list = []
        runners = {"L1": fake_runner("L1", {}, calls), "L2": fake_runner("L2", {}, calls), "L3A": fake_runner("L3A", {"full": True, "short": True}, calls)}
        out = self.run_ladder(runners)
        self.assertEqual([c[0] for c in calls], ["L1", "L2", "L3A"])
        self.assertEqual({out[v]["level"] for v in vl.VARIANTS}, {"L3A"})
        self.assertTrue((self.tmp / f"{SLUG}__v3.mp4").exists())
        self.assertTrue((self.tmp / f"{SLUG}__v3short.mp4").exists())

    def test_l1_pass_stops_the_ladder(self):
        calls: list = []
        runners = {k: fake_runner(k, {"full": True, "short": True}, calls) for k in vl.LEVELS}
        out = self.run_ladder(runners)
        self.assertEqual([c[0] for c in calls], ["L1"])
        self.assertEqual(out["short"]["level"], "L1")

    def test_each_variant_takes_its_own_first_passing_level(self):
        calls: list = []
        runners = {"L1": fake_runner("L1", {"full": True}, calls), "L2": fake_runner("L2", {"short": True}, calls), "L3A": fake_runner("L3A", {}, calls)}
        out = self.run_ladder(runners)
        self.assertEqual(calls[1][:2], ("L2", ("short",)))
        self.assertEqual((out["full"]["level"], out["short"]["level"]), ("L1", "L2"))

    def test_same_bar_for_every_level(self):
        """qa が無い・レビューの代わりに決まりで通した・承認の形でない動画は、どの段でも合格にしない"""
        for bad in ({"qa": False}, {"how": "deterministic-L4"}, {"layout": "image-and-subtitles"}):
            calls: list = []
            runners = {k: fake_runner(k, {"full": True, "short": True}, calls, **bad) for k in vl.LEVELS}
            out = self.run_ladder(runners)
            with self.subTest(bad=bad):
                self.assertIsNone(out["short"]["level"])
                self.assertFalse(out["short"]["gate"]["ok"])
        with mock.patch.dict(os.environ, {"SNS_VIDEO_REQUIRE_QA": "off"}):
            out = self.run_ladder({k: fake_runner(k, {"full": True, "short": True}, [], qa=False) for k in vl.LEVELS})
            self.assertEqual(out["short"]["level"], "L1")

    def test_time_budget_skips_l2(self):
        now = [0.0]
        calls: list = []

        def advance():
            now[0] += 600.0

        runners = {"L1": fake_runner("L1", {}, calls, advance=advance), "L2": fake_runner("L2", {"full": True, "short": True}, calls),
                   "L3A": fake_runner("L3A", {"full": True, "short": True}, calls)}  # fmt: skip
        out = self.run_ladder(runners, deadline_s=840, clock=lambda: now[0])
        self.assertEqual([c[0] for c in calls], ["L1", "L3A"])
        self.assertAlmostEqual(calls[0][2], 840 - vl.L3A_RESERVE_S, delta=1)
        self.assertEqual(out["full"]["level"], "L3A")
        self.assertIn("時間", next(e for e in out["full"]["ladder"] if e["level"] == "L2")["reasons"][0])

    def test_l2_skipped_when_no_capturable_page(self):
        calls: list = []
        runners = {"L1": fake_runner("L1", {}, calls, fail_reason="撮れるページが無い（403）"), "L2": fake_runner("L2", {"full": True, "short": True}, calls),
                   "L3A": fake_runner("L3A", {"full": True, "short": True}, calls)}  # fmt: skip
        self.run_ladder(runners)
        self.assertEqual([c[0] for c in calls], ["L1", "L3A"])

    def test_l2_gets_previous_gate_reasons_as_feedback(self):
        note = vl.feedback_note({"full": {"gate": {"ok": False, "reasons": ["v3: beat2 の見出し「何があった？」が決まった問い"]}}})
        self.assertIn("何があった", note)
        import shorts_v3 as v3

        seen = {}
        orig = v3.write_script

        def recorder(article, pages, note=""):
            seen["note"] = note
            return {}, "fake"

        with mock.patch.object(v3, "write_script", recorder), mock.patch.object(v3, "make_versions", side_effect=lambda *a, **k: (v3.write_script({}, []), {"full": {}, "short": {}})[1]):
            vl.run_v3(SLUG, self.tmp, tts="fake", feedback=note)
        self.assertIn("何があった", seen["note"])
        self.assertIs(v3.write_script, orig)

    def test_all_levels_fail_means_retry_later(self):
        out = self.run_ladder({k: fake_runner(k, {}, []) for k in vl.LEVELS})
        self.assertIsNone(out["full"]["level"])
        self.assertIn("あとの回で作り直す", out["full"]["failReason"])


@unittest.skipUnless(IMPORTED, "numpy / Pillow が無い")
class TextGateTest(unittest.TestCase):
    def test_generic_labels_and_stiff_phrases_are_banned(self):
        beat = {"label": "何があった？", "keyword": "Copilotを選べる", "sub": "", "speech": "GitHub Copilotを作業の担い手として選べるようになりました。", "target": "", "ja": ""}
        probs = " ".join(gate.label_problems({"hook": {}, "beats": [beat, dict(beat, label="何があった")]}))
        self.assertIn("決まった問い", probs)
        self.assertIn("担い手", probs)
        self.assertTrue(gate.stiff_problems("作業の担い手"))

    def test_hook_may_start_with_viewers_stake(self):
        """2026-11-04 オーナー承認: 1文目は見る人の損得、主役の名前は2文目まで（見出しには0秒から出ている）"""
        a = article()
        ok = {"text": "AIに任せる場所が1つ増える", "payoff": "追加の契約なし", "speech": "契約はそのままで大丈夫です。JetBrainsのAirで、Copilotに作業を任せられます。"}
        self.assertFalse([p for p in gate.opening_problems({"hook": ok}, a) if "主役" in p])
        late = dict(ok, speech="契約はそのままで大丈夫です。新しい場所で作業を任せられます。そしてJetBrainsのAirでCopilotが使えます。")
        self.assertTrue([p for p in gate.opening_problems({"hook": late}, a) if "主役" in p])

    def test_opening_is_not_a_summary_and_beats_are_short(self):
        a = article()
        hook = {"text": "GitHubがJetBrainsの「Air」にCopilotを提供", "payoff": "", "speech": "要約すると、GitHubがJetBrainsのAirにCopilotを提供しました。"}
        self.assertTrue(any("要約" in p for p in gate.opening_problems({"hook": hook}, a)))
        self.assertTrue(gate.beat_length_problems({"beats": [{"speech": "あ" * 90}]}))
        self.assertLessEqual(gate.CALM_MAX_SEGMENT, 4.0)


@unittest.skipUnless(IMPORTED, "numpy / Pillow が無い")
class TemplateScriptTest(unittest.TestCase):
    """L3A: 記事の文だけで、承認の形の台本（自分の記事ページを模した画面を証拠に）"""

    def check_script(self, s: dict, a: dict) -> None:
        self.assertEqual(gate.label_problems(s), [])
        self.assertEqual(gate.opening_problems(s, a), [])
        self.assertEqual(gate.beat_length_problems(s), [])
        templates = {gate.norm(x).lower() for x in gate.TEMPLATE_LABELS}
        for b in s["beats"]:
            self.assertNotIn(gate.norm(b["label"].rstrip("？?")).lower(), templates)
            self.assertTrue(b["target"])
        for t in vl.texts_of(s):
            self.assertEqual(gate.stiff_problems(t), [], t)
        self.assertGreaterEqual(len(s["beats"]), 3)

    def test_copilot_article(self):
        a = article()
        s, probs = vl.template_script(a, [vl.article_page(a)])
        self.assertIsNotNone(s, probs)
        self.check_script(s, a)
        self.assertNotIn("担い手", json.dumps(s, ensure_ascii=False))
        self.assertTrue(any(b["speech"].startswith("で、何が変わるのか。") for b in s["beats"][1:]))
        self.assertTrue(s["conclusion"]["speech"].endswith(("？", "?")))  # 締めは投稿の問いかけ

    def test_recent_articles(self):
        import shorts

        slugs = vl.recent_slugs(20)
        made = 0
        for slug in slugs:
            a = shorts.load_article(slug)
            s, _probs = vl.template_script(a, [vl.article_page(a)])
            if s:
                made += 1
                with self.subTest(slug=slug):
                    self.check_script(s, a)
        self.assertGreaterEqual(made, int(len(slugs) * 0.7))  # 作れない記事は L1/L2 とあとの回の作り直しに任せる


@unittest.skipUnless(READY, "ffmpeg / numpy / Pillow が無い")
class GoldenRenderTest(unittest.TestCase):
    """L3A を小さく（480x270・10fps）書き出す: 承認の形・描いた位置の検査に通る。レビューができなければ不合格"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.a = article()
        cls.page = vl.article_page(cls.a)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_l3a(self, review):
        import shorts_v3 as v3

        with mock.patch.object(v3, "vision_review", review):
            return vl.run_template(SLUG, self.tmp, ["short"], self.a, tts="fake", profile="test", pages=[self.page], fps=10, out_scale=0.25, workers=2, sheet=False)

    def test_l3a_uses_approved_layout_and_passes_geometry(self):
        out = self.run_l3a(lambda versions: ({k: [] for k in versions}, ""))
        r = out["short"]
        self.assertEqual(r["level"], "L3A")
        self.assertEqual(r.get("base_layout"), vl.BASE_LAYOUT)
        self.assertTrue(r["gate"]["qa"]["geometry"], r["gate"]["reasons"])
        if r["gate"]["ok"]:
            self.assertTrue(vl.qa_ok(r))

    def test_review_unavailable_is_a_fail(self):
        out = self.run_l3a(lambda versions: ({}, "画像の確認に Claude API も CLI も使えない"))
        r = out["short"]
        self.assertFalse(r["gate"]["ok"])
        self.assertFalse(vl.qa_ok(r))


@unittest.skipUnless(IMPORTED, "numpy / Pillow が無い")
class ApprovedShareTest(unittest.TestCase):
    def state(self, bad: int, n: int = 20) -> dict:
        videos = {}
        for i in range(n):
            lv = "L4" if i < bad else ("L3A" if i % 3 else "L1")
            videos[f"s{i}"] = {"at": f"2026-11-04T{i:02d}:00:00+00:00", "status": "ready", "ok": True, "level": lv,
                               "versions": {"short": {"ok": True, "level": lv, "base_layout": "screen-left-headline"}}}  # fmt: skip
        videos["old"] = {"at": "2026-11-01T00:00:00+00:00", "status": "ready", "ok": True, "versions": {"short": {"ok": True}}}  # 段より前（数えない）
        videos["fail"] = {"at": "2026-11-04T23:00:00+00:00", "status": "gate_failed", "ok": False, "versions": {"short": {"ok": False, "level": None}}}
        return {"videos": videos}

    def test_share(self):
        st = vl.approved_share(self.state(3))
        self.assertEqual((st["n"], st["approved"]), (20, 17))
        self.assertAlmostEqual(st["share"], 0.85)

    def test_alert_below_ninety_percent_only_on_actions(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(stdout="[]", returncode=0)

        self.assertEqual(vl.fallback_alert(self.state(1), {"GITHUB_ACTIONS": "true"}, run), "ok")  # 95%
        self.assertEqual(vl.fallback_alert(self.state(3), {}, run), "skip-local")
        self.assertEqual(calls, [])
        self.assertEqual(vl.fallback_alert(self.state(3), {"GITHUB_ACTIONS": "true"}, run), "created")
        self.assertTrue(any(c[:3] == ["gh", "issue", "create"] and vl.FALLBACK_LABEL in c for c in calls))


class PipelineFlowTest(unittest.TestCase):
    """2026-11-04 オーナー: 動画の検査を厳しくしても、記事づくりと予約の投稿は止めない（落ちた動画はその投稿だけ遅らせる）"""

    def test_article_pipeline_does_not_import_video_modules(self):
        import ast

        video = {"video_ladder", "shorts_v3", "shorts_v2", "video_gate", "video_templates", "sns_video_followup"}
        for name in ("main.py", "web_articles.py", "images.py", "story.py", "roundup.py", "threads_post.py", "breaking_post.py", "radar.py"):
            path = ROOT / "scripts" / name
            if not path.exists():
                continue
            tree = ast.parse(path.read_text("utf-8"))
            mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            mods |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            with self.subTest(script=name):
                self.assertFalse(mods & video, f"{name} が動画の仕組みを読み込んでいる（動画が落ちると記事づくりが止まるおそれ）")

    def _queue(self, posted, videos):
        sys.path.insert(0, str(ROOT))
        from tests import test_sns_video_required as R

        return R, R.PublishQueuedTest("test_ready_video_is_sent_at_the_slot_with_video").run_queue(posted, videos)

    def test_failing_video_delays_only_that_post(self):
        from datetime import timedelta

        from tests import test_sns_video_required as R

        first, second = R.NOW - timedelta(minutes=30), R.NOW - timedelta(minutes=10)
        failing = {"status": "gate_failed", "tries": 2, "reason": "full: v3: QA(目視): 話の食い違い"}
        _R, (state, sent, issues, _) = self._queue({"a": R.queued_rec(first), "b": R.queued_rec(second)}, {"a": failing, "b": R.ready_video("b")})
        self.assertEqual(state["queued"]["a"]["status"], "waiting")  # a は作り直しを待つ
        self.assertEqual(state["queued"]["b"]["status"], "created")  # b は先に出る
        self.assertEqual({s for s, _ in sent}, {"threads", "twitter", "facebook"})
        self.assertEqual(issues, [])

    def test_breaking_gives_up_after_two_hours_but_others_flow(self):
        from datetime import timedelta

        import sns_video_followup as fu
        from tests import test_sns_video_required as R

        old = R.queued_rec(R.NOW - timedelta(hours=fu.QUEUE_GIVE_UP_BREAKING_H, minutes=5))
        old["breaking"] = True
        failing = {"status": "gate_failed", "tries": 2, "breaking": True}
        _R, (state, sent, issues, _) = self._queue({"a": old, "b": R.queued_rec(R.NOW + timedelta(hours=1))}, {"a": failing, "b": R.ready_video("b")})
        self.assertEqual(state["queued"]["a"]["status"], "skipped")
        self.assertEqual([s for s, _ in issues], ["a"])
        self.assertEqual(state["queued"]["b"]["status"], "created")
        # ふつうの投稿は同じ遅れでもまだ待つ（6時間まで）
        normal = R.queued_rec(R.NOW - timedelta(hours=fu.QUEUE_GIVE_UP_BREAKING_H, minutes=5))
        _R, (state, sent, issues, _) = self._queue({"c": normal}, {"c": {"status": "gate_failed", "tries": 2}})
        self.assertEqual(state["queued"]["c"]["status"], "waiting")


@unittest.skipUnless(IMPORTED and hasattr(os, "fork"), "fork が無い")
class IsolatedTest(unittest.TestCase):
    def test_timeout_kills_the_level(self):
        t = time.monotonic()
        res, err = vl.isolated(time.sleep, 1.0, 30)
        self.assertIsNone(res)
        self.assertIn("時間切れ", err)
        self.assertLess(time.monotonic() - t, 10)

    def test_result_comes_back(self):
        res, err = vl.isolated(lambda x: {"full": {"gate": {"ok": x}}}, 30, True)
        self.assertEqual(err, "")
        self.assertTrue(res["full"]["gate"]["ok"])

    def test_error_is_reported(self):
        def boom():
            raise ValueError("だめ")

        res, err = vl.isolated(boom, 30)
        self.assertIsNone(res)
        self.assertIn("ValueError", err)


if __name__ == "__main__":
    unittest.main()
