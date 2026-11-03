"""SNS 動画の段の下がり方（scripts/video_ladder.py）のテスト。ネット・LLM・VOICEVOX は使わない（声はテスト用の合成音）。

- 段の順番（L1 → L2 → L3 → L4）・最初に合格した段を使う・版ごとに選ぶ・qa（描いた位置・画像のレビュー）が無ければ合格にしない
- 時間の予算: 残りが少なければ L2・L3 を飛ばして L4 へ
- L4 の計画は、直近の SNS 向けの記事（content/articles の新しい20本）と、です・ますの文が無い記事でも検査に通る
- AI 画像は使わない（I2）
- 小さく書き出した L3/L4 の動画が品質ゲート（test プロファイル）と qa に通る（ffmpeg / numpy / Pillow が無ければ飛ばす）
- 直近20本の段の内訳と、L3/L4 が3割を超えたときの issue（GitHub Actions の中だけ）

  python3 -m unittest tests/test_video_ladder.py -v
"""

from __future__ import annotations

import copy
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
FIX = ROOT / "tests" / "fixtures" / "video"

try:
    import numpy as np
    from PIL import Image

    import video_ladder as vl

    READY = shutil.which("ffmpeg") is not None
    IMPORTED = True
except ImportError:  # pragma: no cover
    READY = IMPORTED = False


def article() -> dict:
    """テスト用の記事（架空）: です・ますのリード・FAQ・公式の写真（サイトに保存した写し）"""
    a = json.loads((FIX / "fixture_ai.json").read_text("utf-8"))
    a["lede"] = "Fixture AIは新しい有料プラン「Pro Max」を発表しました。月額は3000円で、利用量は通常プランの5倍です。日本でもすぐに使えます。"
    a["faq"] = [
        {"q": "Pro Maxはいくらで使える？", "a": "料金は月額3000円です。通常プランより高い分、利用量は5倍になります。"},
        {"q": "Pro Maxは日本で使える？", "a": "日本でも2026年11月から提供されます。申し込みは公式サイトからできます。"},
    ]
    a["workMoney"] = {"points": ["仕事で毎日使う人は、資料作りや調べものをまとめて任せられます。"]}
    return a


def fake_runner(level: str, results: dict, calls: list, qa: bool = True, advance=None, fail_reason: str = ""):
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
            rep = {"slug": slug, "total": 20.0, "gate": {"ok": ok, "reasons": [] if ok else [f"{level} の検査に落ちた"], "qa": {"geometry": qa, "vision": qa, "version": 1}}}
            if fail_reason:
                rep["failReason"] = fail_reason
            out[v] = rep
        return out

    return fn


@unittest.skipUnless(IMPORTED, "numpy / Pillow / budoux が無い")
class LadderOrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.a = article()
        self.slug = self.a["slug"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_ladder(self, runners, **kw):
        return vl.make_versions(self.slug, self.tmp, article=self.a, runners=runners, isolate=False, **kw)

    def test_order_and_first_passing_level(self):
        calls: list = []
        runners = {
            "L1": fake_runner("L1", {}, calls),
            "L2": fake_runner("L2", {}, calls),
            "L3": fake_runner("L3", {"full": True, "short": True}, calls),
            "L4": fake_runner("L4", {"full": True, "short": True}, calls),
        }
        out = self.run_ladder(runners)
        self.assertEqual([c[0] for c in calls], ["L1", "L2", "L3"])  # L4 は呼ばない
        self.assertEqual(out["full"]["level"], "L3")
        self.assertEqual(out["short"]["level"], "L3")
        self.assertEqual([e["level"] for e in out["full"]["ladder"]], ["L1", "L2", "L3"])
        self.assertTrue((self.tmp / f"{self.slug}__v3.mp4").exists())
        self.assertTrue((self.tmp / f"{self.slug}__v3short.mp4").exists())
        self.assertTrue(json.loads((self.tmp / f"{self.slug}__v3.report.json").read_text("utf-8"))["gate"]["ok"])

    def test_l1_pass_stops_the_ladder(self):
        calls: list = []
        runners = {k: fake_runner(k, {"full": True, "short": True}, calls) for k in vl.LEVELS}
        out = self.run_ladder(runners)
        self.assertEqual([c[0] for c in calls], ["L1"])
        self.assertEqual({out[v]["level"] for v in vl.VARIANTS}, {"L1"})

    def test_each_variant_takes_its_own_first_passing_level(self):
        calls: list = []
        runners = {
            "L1": fake_runner("L1", {"full": True}, calls),
            "L2": fake_runner("L2", {"short": True}, calls),
            "L3": fake_runner("L3", {"full": True, "short": True}, calls),
            "L4": fake_runner("L4", {"full": True, "short": True}, calls),
        }
        out = self.run_ladder(runners)
        self.assertEqual(calls[1][:2], ("L2", ("short",)))  # 合格した版は作り直さない
        self.assertEqual(out["full"]["level"], "L1")
        self.assertEqual(out["short"]["level"], "L2")

    def test_gate_ok_without_qa_is_not_a_pass(self):
        calls: list = []
        runners = {
            "L1": fake_runner("L1", {"full": True, "short": True}, calls, qa=False),  # 古いコードの動画（qa が無い・不合格）
            "L2": fake_runner("L2", {}, calls),
            "L3": fake_runner("L3", {}, calls),
            "L4": fake_runner("L4", {"full": True, "short": True}, calls),
        }
        out = self.run_ladder(runners)
        self.assertEqual(out["short"]["level"], "L4")
        self.assertIn("qa", " ".join(out["short"]["ladder"][0]["reasons"]))
        with mock.patch.dict(os.environ, {"SNS_VIDEO_REQUIRE_QA": "off"}):
            calls.clear()
            out = self.run_ladder(runners)
            self.assertEqual(out["short"]["level"], "L1")

    def test_time_budget_skips_to_l4(self):
        now = [0.0]
        calls: list = []

        def advance():
            now[0] += 600.0  # L1 が 10 分かかった

        runners = {
            "L1": fake_runner("L1", {}, calls, advance=advance),
            "L2": fake_runner("L2", {"full": True, "short": True}, calls),
            "L3": fake_runner("L3", {"full": True, "short": True}, calls),
            "L4": fake_runner("L4", {"full": True, "short": True}, calls),
        }
        out = self.run_ladder(runners, deadline_s=720, clock=lambda: now[0])
        self.assertEqual([c[0] for c in calls], ["L1", "L4"])
        # L1 の時間は、L3 と L4 の分を残して区切る（720 - 90 - 100）
        self.assertAlmostEqual(calls[0][2], 720 - vl.L4_RESERVE_S - vl.MIN_SECONDS["L3"], delta=1)
        skipped = [e for e in out["full"]["ladder"] if e.get("skipped")]
        self.assertEqual([e["level"] for e in skipped], ["L2", "L3"])
        self.assertTrue(all("時間" in e["reasons"][0] for e in skipped))
        self.assertEqual(out["full"]["level"], "L4")

    def test_l2_skipped_when_no_capturable_page(self):
        calls: list = []
        runners = {
            "L1": fake_runner("L1", {}, calls, fail_reason="撮れるページが無い（403）"),
            "L2": fake_runner("L2", {"full": True, "short": True}, calls),
            "L3": fake_runner("L3", {"full": True, "short": True}, calls),
            "L4": fake_runner("L4", {}, calls),
        }
        out = self.run_ladder(runners)
        self.assertEqual([c[0] for c in calls], ["L1", "L3"])
        self.assertEqual(out["full"]["level"], "L3")

    def test_l3_skipped_without_official_image(self):
        self.a["imageCredit"] = {"kind": "ai", "org": ""}
        calls: list = []
        runners = {k: fake_runner(k, {"full": True, "short": True} if k == "L4" else {}, calls) for k in vl.LEVELS}
        out = self.run_ladder(runners)
        self.assertEqual([c[0] for c in calls], ["L1", "L2", "L4"])
        self.assertIn("公式の画像が無い", out["full"]["ladder"][2]["reasons"][0])

    def test_l2_gets_previous_gate_reasons_as_feedback(self):
        prev = {"full": {"gate": {"ok": False, "reasons": ["v3: 場面2の選択した文に、キーワード「5倍」の数字・言葉が無い"]}}}
        note = vl.feedback_note(prev)
        self.assertIn("キーワード「5倍」", note)
        import shorts_v3 as v3

        seen = {}
        orig = v3.write_script

        def recorder(article, pages, note=""):
            seen["note"] = note
            return {}, "fake"

        with mock.patch.object(v3, "write_script", recorder), mock.patch.object(v3, "make_versions", side_effect=lambda *a, **k: (v3.write_script({}, []), {"full": {}, "short": {}})[1]):
            vl.run_v3(self.slug, self.tmp, tts="fake", feedback=note)
        self.assertIn("キーワード「5倍」", seen["note"])
        self.assertIs(v3.write_script, orig)

    def test_l4_is_retried_once_when_it_fails(self):
        self.a["imageCredit"] = {"kind": "ai"}  # 写真なし → 同じ割り付けで場面を減らしてもう1回
        calls: list = []
        runners = {k: fake_runner(k, {}, calls) for k in vl.LEVELS}
        runners["L4text"] = fake_runner("L4text", {"full": True}, calls)
        out = self.run_ladder(runners)
        self.assertEqual([c[0] for c in calls], ["L1", "L2", "L4", "L4text"])
        self.assertEqual(out["full"]["level"], "L4")
        self.assertEqual(out["full"]["ladder"][-1]["mode"], "fewer")

    def test_all_levels_fail_returns_failure_report(self):
        calls: list = []
        runners = {k: fake_runner(k, {}, calls) for k in vl.LEVELS}
        out = self.run_ladder(runners)
        self.assertIsNone(out["full"]["level"])
        self.assertFalse(out["full"]["gate"]["ok"])
        self.assertFalse((self.tmp / f"{self.slug}__v3.mp4").exists())


@unittest.skipUnless(IMPORTED, "numpy / Pillow / budoux が無い")
class PlanTest(unittest.TestCase):
    """描画しない検査: 直近の SNS 向けの記事 20 本で、L4（写真つき・写真なし）と L3 の計画が検査に通る"""

    def test_l4_plan_passes_for_recent_articles(self):
        import shorts

        slugs = vl.recent_slugs(20)
        self.assertGreaterEqual(len(slugs), 10)
        for slug in slugs:
            a = shorts.load_article(slug)
            for v in vl.VARIANTS:
                for images in (None, []):  # 公式の写真つき（あれば）・写真なし（ブランドのカード）
                    plan = vl.build_plan(a, "L4", v, images=images)
                    with self.subTest(slug=slug, variant=v, image=images is None):
                        self.assertEqual(vl.plan_problems(plan, a), [])
                        self.assertTrue(vl.all_verbatim(plan, a))  # レビューが使えなくても L4 は決まりで判定できる
                        lo, hi = plan.length_range
                        self.assertLessEqual(vl.est_seconds(plan.lines()), hi)
                if vl.official_images(a):
                    plan = vl.build_plan(a, "L3", v)
                    with self.subTest(slug=slug, variant=v, level="L3"):
                        self.assertEqual(vl.plan_problems(plan, a), [])

    def test_l4_plan_without_polite_sentences(self):
        a = article()
        a.update({"lede": "テスト用の記事。", "faq": [], "workMoney": {}, "body": "## 概要\n\n新プランを発表した。", "threadsPost": ""})
        plan = vl.build_plan(a, "L4", "short")
        self.assertEqual(vl.plan_problems(plan, a), [])
        self.assertEqual(plan.beats[0].source, "title")
        self.assertTrue(plan.beats[0].speech.startswith("見出しは「"))

    def test_narration_is_polite_and_numbers_come_from_article(self):
        import sns_rules

        a = article()
        for v in vl.VARIANTS:
            plan = vl.build_plan(a, "L4", v)
            for line in plan.lines():
                self.assertEqual(sns_rules.grammar_problems(line), [])
                self.assertEqual(vl.polite_ok(line), [], line)
            self.assertTrue(all(b.speech for b in plan.beats))

    def test_ai_image_is_never_used(self):
        a = article()
        a["imageCredit"] = {"kind": "ai", "org": "AI"}
        self.assertEqual(vl.official_images(a), [])
        a["imageCredit"] = {"kind": "stock"}
        a["bodyImages"] = [{"src": "https://ai-tech-express.jp/img/x.jpg", "kind": "ai", "credit": "AI"}]
        self.assertEqual(vl.official_images(a), [])
        a["bodyImages"] = [{"src": "https://example.com/x.jpg", "kind": "photo", "credit": "Example"}]  # サイトに保存していない
        self.assertEqual(vl.official_images(a), [])

    def test_subtitles_join_back_to_the_narration(self):
        import shorts

        for slug in vl.recent_slugs(10):
            plan = vl.build_plan(shorts.load_article(slug), "L4", "full")
            for line in plan.lines():
                self.assertEqual("".join(vl.sub_chunks(line)), line)

    def test_fixed_texts_have_no_numbers(self):
        import video_gate as gate

        for t in vl.fixed_texts({"slug": "x", "title": "Xが新製品"}):
            self.assertFalse(gate.number_tokens(t) - {"0"}, t)


def write_photo(path: Path, size=(1200, 675)) -> None:
    """公式の写真の代わり（グラデーションと図形）"""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.stack([(xx / w * 200 + 30), (yy / h * 160 + 60), np.full_like(xx, 180)], axis=-1).astype(np.uint8)
    img[h // 3 : h // 3 * 2, w // 4 : w // 2] = (240, 240, 250)
    Image.fromarray(img).save(path)


@unittest.skipUnless(READY, "ffmpeg / numpy / Pillow が無い")
class GoldenRenderTest(unittest.TestCase):
    """小さく（480x270・10fps）書き出して、品質ゲート（test プロファイル）と qa に通る"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.a = article()
        cls.photo = cls.tmp / "photo.jpg"
        write_photo(cls.photo)
        cls.pictures = {cls.a["thumbnail"]: str(cls.photo)}
        cls.fast = {"fps": 10, "out_scale": 0.25, "pictures": cls.pictures, "workers": 2}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def check(self, rep: dict, level: str) -> None:
        g = rep["gate"]
        self.assertTrue(g["ok"], g["reasons"])
        self.assertTrue(g["qa"]["geometry"])
        self.assertTrue(g["qa"]["vision"])
        self.assertEqual(g["qa"]["version"], 1)
        self.assertEqual(rep["level"], level)
        lo, hi = rep["length_range"]
        self.assertTrue(lo <= rep["total"] <= hi, rep["total"])
        self.assertEqual(rep["frame"], [1920, 1080])
        joined = "".join(c["text"] for u in rep["units"] for c in u["chunks"])
        self.assertEqual(joined, "".join(rep["speech_lines"]))  # 字幕 = 読み上げ

    def test_l4_renders_and_passes_without_reviewer(self):
        out = self.tmp / "l4"
        res = vl.run_simple("L4", self.a["slug"], out, ["full", "short"], self.a, tts="fake", profile="test", reviewer=lambda frames: (None, "テスト"), **self.fast)
        for v in vl.VARIANTS:
            self.check(res[v], "L4")
            self.assertTrue(res[v]["gate"]["qa"]["vision_how"].startswith("deterministic-L4"))
            self.assertTrue((out / f"{self.a['slug']}__v3{'short' if v == 'short' else ''}.mp4").exists())
            self.assertIn("画像：Fixture AI（公式発表より）", res[v]["credits_shown"])

    def test_l4_text_only_card(self):
        out = self.tmp / "l4text"
        res = vl.run_simple("L4", self.a["slug"], out, ["short"], self.a, tts="fake", profile="test", reviewer=lambda frames: (None, "テスト"), text_only=True, **self.fast)
        self.check(res["short"], "L4")
        self.assertEqual(res["short"]["images"], [])
        self.assertTrue(any(t["role"] == "card" for t in res["short"]["texts"]))

    def test_ladder_falls_from_l3_to_l4_when_review_rejects(self):
        calls = {"n": 0}

        def reviewer(frames):  # L3 は不合格・L4 はレビューなし（決まりで判定）
            calls["n"] += 1
            if calls["n"] == 1:
                return {v: {"ok": False, "problems": ["画像と話が合わない"]} for v in frames}, "fake"
            return None, "テスト"

        fail: list = []
        runners = {"L1": fake_runner("L1", {}, fail), "L2": fake_runner("L2", {}, fail)}
        out_dir = self.tmp / "ladder"
        out = vl.make_versions(self.a["slug"], out_dir, article=self.a, runners=runners, isolate=False, tts="fake", profile="test", reviewer=reviewer, **self.fast)
        self.assertEqual(out["full"]["level"], "L4")
        self.assertEqual(out["short"]["level"], "L4")
        l3 = next(e for e in out["full"]["ladder"] if e["level"] == "L3")
        self.assertFalse(l3["ok"])
        self.assertIn("画像のレビュー", " ".join(l3["reasons"]))
        self.assertTrue((out_dir / f"{self.a['slug']}__v3.mp4").exists())
        self.assertTrue((out_dir / f"{self.a['slug']}__v3short.mp4").exists())

    def test_l3_passes_with_reviewer(self):
        out = self.tmp / "l3"
        ok = lambda frames: ({v: {"ok": True, "problems": []} for v in frames}, "fake")  # noqa: E731
        res = vl.run_simple("L3", self.a["slug"], out, ["short"], self.a, tts="fake", profile="test", reviewer=ok, **self.fast)
        self.check(res["short"], "L3")
        self.assertEqual(res["short"]["gate"]["qa"]["vision_how"], "fake")


@unittest.skipUnless(IMPORTED, "numpy / Pillow / budoux が無い")
class StatsTest(unittest.TestCase):
    def state(self, fallback: int, n: int = 20) -> dict:
        videos = {}
        for i in range(n):
            lv = "L4" if i < fallback else "L1"
            videos[f"s{i}"] = {"at": f"2026-11-04T{i:02d}:00:00+00:00", "status": "ready", "ok": True,
                               "versions": {"short": {"ok": True, "level": lv}, "full": {"ok": True, "level": "L1"}}}  # fmt: skip
        videos["old"] = {"at": "2026-11-01T00:00:00+00:00", "status": "ready", "ok": True, "versions": {"short": {"ok": True}}}  # ladder 以前（数えない）
        return {"videos": videos}

    def test_level_stats_counts_lowest_level(self):
        st = vl.level_stats(self.state(7))
        self.assertEqual(st["n"], 20)
        self.assertEqual(st["by_level"]["L4"], 7)
        self.assertAlmostEqual(st["fallback_share"], 0.35)

    def test_alert_only_on_actions_and_over_threshold(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(stdout="[]", returncode=0)

        self.assertEqual(vl.fallback_alert(self.state(5), {"GITHUB_ACTIONS": "true"}, run), "ok")  # 25% は知らせない
        self.assertEqual(vl.fallback_alert(self.state(7), {}, run), "skip-local")
        self.assertEqual(calls, [])
        self.assertEqual(vl.fallback_alert(self.state(7), {"GITHUB_ACTIONS": "true"}, run), "created")
        self.assertTrue(any(c[:3] == ["gh", "issue", "create"] and vl.FALLBACK_LABEL in c for c in calls))

        def run_open(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(stdout='[{"number": 12}]' if cmd[:3] == ["gh", "issue", "list"] else "", returncode=0)

        self.assertEqual(vl.fallback_alert(self.state(9), {"GITHUB_ACTIONS": "true"}, run_open), "commented #12")


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
