"""SNS 動画の品質ゲート（scripts/video_gate.py）と失敗時の約束（scripts/sns_video.py）のテスト。

  python3 -m unittest tests/test_video_gate.py -v      # pytest でも動く
ffmpeg / Pillow / numpy が無い環境では、動画を作るテストだけ飛ばす。
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
FIX = ROOT / "tests" / "fixtures" / "video"

import video_gate as gate  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
try:
    import numpy  # noqa: F401
    from PIL import ImageFont  # noqa: F401

    HAS_IMAGING = True
except ImportError:
    HAS_IMAGING = False


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text("utf-8"))


class FactCheckTest(unittest.TestCase):
    def setUp(self):
        self.article = load("fixture_ai.json")
        self.script = load("fixture_ai.script.json")

    def test_fixture_script_is_clean(self):
        self.assertEqual(gate.fact_problems(self.script, self.article), [])
        self.assertEqual(gate.title_problems(self.script, self.article), [])

    def test_number_not_in_article(self):
        s = copy.deepcopy(self.script)
        s["points"][0]["caption"] = "新プランは月額2980円です。"
        problems = gate.fact_problems(s, self.article)
        self.assertTrue(any("2980" in p for p in problems), problems)

    def test_number_in_speech_not_in_article(self):
        s = copy.deepcopy(self.script)
        s["points"][1]["speech"] = "利用量は通常プランの7倍になります。"
        self.assertTrue(any("7" in p for p in gate.fact_problems(s, self.article)))

    def test_kanji_number_forms(self):
        money = load("fixture_money.json")
        s = load("fixture_money.script.json")
        self.assertEqual(gate.fact_problems(s, money), [])
        # 「3万2000円」と「32000円」は同じ数として扱う
        self.assertEqual(gate.numbers_ok("平均は32000円", gate.number_tokens(gate.material_text(money))), [])

    def test_unknown_proper_noun(self):
        s = copy.deepcopy(self.script)
        s["points"][2]["caption"] = "Geminiとの連携も始まります。"
        self.assertTrue(any("Gemini" in p for p in gate.fact_problems(s, self.article)))

    def test_unknown_quoted_term_and_katakana(self):
        s = copy.deepcopy(self.script)
        s["use"]["caption"] = "「ウルトラ割」でサブスクリプションが安くなります。"
        problems = " ".join(gate.fact_problems(s, self.article))
        self.assertIn("ウルトラ割", problems)
        self.assertIn("サブスクリプション", problems)

    def test_claim_words_need_source(self):
        s = copy.deepcopy(self.script)
        s["hook"]["caption"] = "Fixture AIが業界最安の新プラン"
        self.assertTrue(any("最安" in p for p in gate.fact_problems(s, self.article)))

    def test_number_card_must_match_article_spelling(self):
        s = copy.deepcopy(self.script)
        s["numbers"] = [{"value": "3,000円/月", "label": "料金"}]
        self.assertTrue(any("数字カード" in p for p in gate.fact_problems(s, self.article)))

    def test_title_must_be_same_story(self):
        s = copy.deepcopy(self.script)
        s["title"] = "新しいスマホが発売"
        s["headline"] = "スマホ発売"
        self.assertTrue(gate.title_problems(s, self.article))


class TextAndTimelineTest(unittest.TestCase):
    def _report(self, **text):
        t = {"role": "caption", "text": "テスト", "bbox": [100, 1200, 800, 1300], "box": [96, 1180, 950, 1500], "size": 72, "min_size": 56, "contrast": 21.0, "missing": [], "safe": True}
        t.update(text)
        return {"texts": [t], "safe": list(gate.SAFE)}

    def check(self, report) -> list[str]:
        res = gate.GateResult(ok=False)
        gate.check_texts(report, res)
        return res.reasons

    def test_ok_text(self):
        self.assertEqual(self.check(self._report()), [])

    def test_overflow_box(self):
        self.assertTrue(self.check(self._report(bbox=[100, 1200, 980, 1300])))

    def test_outside_safe_area_bottom_and_right(self):
        bottom = self.check(self._report(bbox=[100, 1550, 800, 1600], box=[60, 1500, 950, 1700]))
        right = self.check(self._report(bbox=[700, 1200, 1000, 1300], box=[60, 1180, 1020, 1500]))
        self.assertTrue(any("安全領域" in r for r in bottom))
        self.assertTrue(any("安全領域" in r for r in right))

    def test_too_small(self):
        self.assertTrue(self.check(self._report(size=40, min_size=56)))

    def test_tofu(self):
        self.assertTrue(self.check(self._report(missing=["😀"])))

    def test_low_contrast(self):
        self.assertTrue(self.check(self._report(size=30, contrast=gate.contrast((255, 255, 255), (220, 220, 220)))))

    def test_contrast_values(self):
        self.assertAlmostEqual(gate.contrast((255, 255, 255), (0, 0, 0)), 21.0, places=1)
        self.assertGreater(gate.contrast((255, 255, 255), (29, 74, 143)), 4.5)  # 白×紺
        self.assertGreater(gate.contrast((255, 255, 255), (200, 34, 26)), 4.5)  # 白×速報レッド

    def _units(self):
        return [
            {"kind": "hook", "start": 0.12, "end": 1.8, "caption": "a", "chunks": [{"text": "つかみ", "start": 0.0, "end": 2.1}]},
            {"kind": "point", "start": 2.1, "end": 5.0, "caption": "b", "chunks": [{"text": "前半", "start": 2.04, "end": 3.6}, {"text": "後半", "start": 3.6, "end": 5.3}]},
        ]

    def timeline(self, units, total=8.0, duration=8.0):
        res = gate.GateResult(ok=False)
        gate.check_timeline({"units": units, "total": total}, duration, res)
        return res.reasons

    def test_timeline_ok(self):
        self.assertEqual(self.timeline(self._units()), [])

    def test_hook_must_show_at_zero(self):
        units = self._units()
        units[0]["chunks"][0]["start"] = 0.8
        self.assertTrue(any("つかみ" in r for r in self.timeline(units)))

    def test_caption_too_short(self):
        units = self._units()
        units[1]["chunks"] = [{"text": "前半", "start": 2.04, "end": 2.2}, {"text": "後半", "start": 2.2, "end": 5.3}]
        self.assertTrue(any("短すぎる" in r for r in self.timeline(units)))

    def test_caption_disappears_before_narration(self):
        units = self._units()
        units[1]["chunks"][-1]["end"] = 4.0
        self.assertTrue(self.timeline(units))

    def test_length_mismatch(self):
        self.assertTrue(any("ずれ" in r for r in self.timeline(self._units(), total=8.0, duration=9.0)))


@unittest.skipUnless(HAS_IMAGING, "Pillow/numpy が無い")
class GlyphTest(unittest.TestCase):
    def test_missing_glyphs(self):
        from PIL import ImageFont

        f = ImageFont.truetype(str(ROOT / "assets" / "fonts" / "NotoSansJP-Black.otf"), 40)
        self.assertEqual(gate.missing_glyphs("月額3000円の「Pro Max」、ユルゲン・シュミットフーバー", f), [])
        self.assertIn("😀", gate.missing_glyphs("値上げ😀", f))


@unittest.skipUnless(HAS_FFMPEG and HAS_IMAGING, "ffmpeg が無い")
class MediaChecksTest(unittest.TestCase):
    """わざと壊した動画で、ゲートが落とすことを確かめる"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def make(self, name: str, video: str, audio: str, seconds: float = 6, extra=()) -> Path:
        out = self.tmp / name
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", video, "-f", "lavfi", "-i", audio, "-t", str(seconds),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", *extra, str(out)],
            check=True,
        )  # fmt: skip
        return out

    def test_black_frames_detected(self):
        mp4 = self.make("black.mp4", "color=c=black:s=108x192:r=10", "sine=f=440:r=48000")
        res = gate.GateResult(ok=False)
        gate.check_visual(mp4, res)
        self.assertTrue(any("黒" in r for r in res.reasons), res.reasons)

    def test_moving_picture_passes_visual(self):
        mp4 = self.make("ok.mp4", "testsrc2=s=108x192:r=10", "sine=f=440:r=48000")
        res = gate.GateResult(ok=False)
        gate.check_visual(mp4, res)
        self.assertEqual(res.reasons, [])

    def test_faststart(self):
        slow = self.make("slow.mp4", "testsrc2=s=108x192:r=10", "sine=f=440:r=48000", 2)
        fast = self.make("fast.mp4", "testsrc2=s=108x192:r=10", "sine=f=440:r=48000", 2, ("-movflags", "+faststart"))
        self.assertFalse(gate.moov_first(slow))
        self.assertTrue(gate.moov_first(fast))

    def test_long_silence_and_loudness(self):
        # 声の代わりのサイン波 → 3秒無音 → サイン波（無音が長い・音量が基準外）
        mp4 = self.make("silent.mp4", "testsrc2=s=108x192:r=10", "aevalsrc='if(between(t,1.5,4.5),0,0.05*sin(2*PI*440*t))':s=48000", 7)
        res = gate.GateResult(ok=False)
        gate.check_audio(mp4, {"allowed_silences": [[0, 0.2]]}, 7.0, res)
        text = " ".join(res.reasons)
        self.assertIn("無音", text)
        self.assertIn("音量", text)

    def test_format_rejects_wrong_size_and_fps(self):
        mp4 = self.make("landscape.mp4", "testsrc2=s=320x180:r=25", "sine=f=440:r=48000", 6, ("-movflags", "+faststart"))
        res = gate.GateResult(ok=False)
        gate.check_format(mp4, gate.PROFILES["production"], res)
        text = " ".join(res.reasons)
        self.assertIn("9:16", text)
        self.assertIn("fps", text)

    def test_check_video_never_raises(self):
        res = gate.check_video(self.tmp / "missing.mp4", {}, {}, "production")
        self.assertFalse(res.ok)
        broken = self.tmp / "broken.mp4"
        broken.write_bytes(b"not a video")
        res = gate.check_video(broken, {"slug": "x"}, {"slug": "x"}, "production")
        self.assertFalse(res.ok)
        self.assertTrue(res.reasons)


class FailSafeContractTest(unittest.TestCase):
    """動画づくりが失敗しても、投稿は動画なしで今までどおり出る"""

    def setUp(self):
        import sns_video

        self.sv = sns_video
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disabled_by_default(self):
        self.assertIsNone(self.sv.video_for_post("x", out_dir=self.tmp, render=lambda *a: 1 / 0))

    def test_render_exception_returns_none(self):
        def boom(*_a):
            raise RuntimeError("VOICEVOX が落ちた")

        self.assertIsNone(self.sv.video_for_post("x", out_dir=self.tmp, render=boom, enabled=True))

    def test_slow_render_returns_none(self):
        def slow(*_a):
            time.sleep(0.3)
            return None

        started = time.time()
        self.assertIsNone(self.sv.video_for_post("x", out_dir=self.tmp, render=slow, enabled=True, time_budget_s=0.1))
        self.assertLess(time.time() - started, 5)

    def _write(self, ok: bool, engine: str = "voicevox", size_match: bool = True) -> Path:
        mp4 = self.tmp / "x__A.mp4"
        mp4.write_bytes(b"0" * 100)
        report = {
            "design": "A",
            "title": "t",
            "description": "d",
            "voice": {"engine": engine},
            "gate": {"ok": ok, "reasons": [] if ok else ["NG"], "metrics": {"bytes": 100 if size_match else 99, "duration": 20}},
        }
        path = self.tmp / "x__A.report.json"
        path.write_text(json.dumps(report), "utf-8")
        return path

    def test_gate_fail_returns_none(self):
        path = self._write(ok=False)
        self.assertIsNone(self.sv.video_for_post("x", out_dir=self.tmp, render=lambda *a: path, enabled=True))

    def test_preview_voice_never_attached(self):
        path = self._write(ok=True, engine="edge")
        self.assertIsNone(self.sv.video_for_post("x", out_dir=self.tmp, render=lambda *a: path, enabled=True))

    def test_tampered_file_rejected(self):
        path = self._write(ok=True, size_match=False)
        self.assertIsNone(self.sv.video_for_post("x", out_dir=self.tmp, render=lambda *a: path, enabled=True))

    def test_passing_video_returned(self):
        path = self._write(ok=True)
        video = self.sv.video_for_post("x", out_dir=self.tmp, render=lambda *a: path, enabled=True)
        self.assertIsNotNone(video)
        self.assertEqual(video.design, "A")

    def test_post_with_fallback(self):
        calls = []
        text_only = lambda: calls.append("text") or "text"  # noqa: E731
        self.assertEqual(self.sv.post_with_fallback(text_only, None, None), "text")

        def rejected(_v):
            raise self.sv.DefiniteRejection("Buffer が動画を拒否")

        video = self.sv.VideoAttachment(Path("a.mp4"), "A", "t", "d", 20, 1, {"ok": True})
        self.assertEqual(self.sv.post_with_fallback(text_only, rejected, video), "text")

        def unknown(_v):
            raise TimeoutError("受理されたか不明")

        # 受理されたか分からない失敗では、二重投稿を避けるため動画なしで出し直さない
        with self.assertRaises(TimeoutError):
            self.sv.post_with_fallback(text_only, unknown, video)
        self.assertEqual(calls, ["text", "text"])


if __name__ == "__main__":
    unittest.main()
