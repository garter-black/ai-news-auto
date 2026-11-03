"""SNS 動画 v3 の QA（2026-11-04 オーナー「選択の青がずれる・『これま』だけに付く・行の頭が切れる・左の文字と話が合わない」）。

- 日本語の証拠は意味のまとまり（句点・読点・かぎかっこ）まで広げる。語の途中で切らない
- 寄るときは、証拠の文が触れている行を行の頭からまるごと・余白つきで入れる（行の頭を切らない）
- ゲートの QA（video_gate.geometry_problems）: (a) 選択の枠が文字の中・画面の中・意味のまとまり (b) 止まっている間ずっと証拠の文が余白つきで画面の中
  (c) 左のキーワードが読み上げと選んだ文（訳）の両方にある
- AI の目視の確認（偽物の LLM）: 合格なら qa.vision=true、不合格なら1回だけ作り直す、確認できなければ vision=false で投稿しない

  python3 -m unittest tests.test_shorts_v3_qa -v
ネット・LLM・VOICEVOX・ブラウザは使わない。ffmpeg / numpy / Pillow / budoux が無ければ飛ばす。
"""

from __future__ import annotations

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

try:
    import budoux  # noqa: F401
    import numpy as np

    import shorts_v3 as v3
    import test_shorts_v3 as base
    import video_gate as gate

    READY = shutil.which("ffmpeg") is not None
except ImportError:  # pragma: no cover
    READY = False

PARA = "株式会社Light＆Green代表取締役、AKARI FOODPHOTO STUDIO代表。これまで1,000件以上の撮影を手がけ、飲食店のメニュー撮影や食品・商品撮影に携わる。"


def para_page(per_line: int = 24, size: float = 18.0) -> "v3.Page":
    """長い段落（1つのブロックが何行にも折り返す）のページ。1文字ずつの枠は行ごとに左から並べる（ページの画素）"""
    k = v3.CAPTURE_DPR
    x0, y0 = 40 * k, 300 * k
    cw, lh = size * k, size * 1.6 * k
    chars = []
    for j, ch in enumerate(PARA):
        row, col = divmod(j, per_line)
        x, y = x0 + col * cw, y0 + row * lh
        chars.append((ch, (x, y, x + cw, y + size * 1.3 * k) if ch.strip() else None))
    rows = (len(PARA) + per_line - 1) // per_line
    rect = (x0, y0, x0 + per_line * cw, y0 + rows * lh)
    blk = v3.Block("1-1", PARA, rect, chars, size)
    img = np.full((2400 * k, v3.CAPTURE_W * k, 3), 250, dtype=np.uint8)
    return v3.Page("https://example.com/p", "example.com", img, [blk], "p")


@unittest.skipUnless(READY, "ffmpeg / numpy / Pillow / budoux が必要")
class PhraseTest(unittest.TestCase):
    def test_fragment_grows_to_clause(self):
        for frag in ("これま", "000件以上の撮影", "これまで1,000件以上の撮影を手がけ"):
            at = PARA.find(frag)
            a, b = v3.ja_phrase_bounds(PARA, at, at + len(frag))
            self.assertEqual(PARA[a:b], "これまで1,000件以上の撮影を手がけ、")
        self.assertFalse(v3.ja_phrase_ok("これま", PARA))
        self.assertTrue(v3.ja_phrase_ok("これまで1,000件以上の撮影を手がけ", PARA))  # 最後の読点は無くてよい

    def test_snap_target_uses_screen_text_only(self):
        item = {"target": "これま", "block": "1-1"}
        self.assertTrue(v3.snap_target(item, [para_page()]))
        self.assertEqual(item["target"], "これまで1,000件以上の撮影を手がけ、")
        self.assertIn(item["target"], PARA)  # 画面の文字そのまま（言葉は足さない）
        eng = {"target": "Manual recovery becomes", "block": "1-1"}
        self.assertFalse(v3.snap_target(eng, [para_page()]))  # 英語は訳と組なので広げない（区切りの検査で直させる）

    def test_script_problems_flags_cut_phrase(self):
        page = para_page()
        tg = v3.find_target([page], "これま", "1-1")
        self.assertIsNotNone(tg)
        self.assertFalse(v3.ja_phrase_ok("これま", tg.block.text))


@unittest.skipUnless(READY, "ffmpeg / numpy / Pillow / budoux が必要")
class FramingTest(unittest.TestCase):
    def test_zoom_keeps_line_starts_and_margins(self):
        page = para_page()
        tg = v3.find_target([page], "これまで1,000件以上の撮影を手がけ、", "1-1", keyword="1,000件以上")
        self.assertGreaterEqual(len(tg.rows), 2)  # 2行にまたがる（1行目の終わり＋2行目）
        for box in (v3.line_box(tg), v3.zoom_box(tg)):
            cam = v3.frame_box(page, 0, box, v3.ZOOM_MAX)
            view = v3.cam_view(cam)
            line_start = min(r[0] for _, r in tg.block.chars if r)
            self.assertLessEqual(view[0] + v3.QA_MARGIN_X / cam.s, line_start + 1)  # 行の頭が余白の内側
            span = tg.span
            self.assertTrue(gate._inside(list(span), list(view), v3.QA_MARGIN_X / cam.s, v3.QA_MARGIN_Y / cam.s))

    def test_old_number_zoom_cropped_line_start(self):
        """前の寄り方（数字に 2.2倍）だと、証拠の文の2行目の頭が画面の外に出ていた（オーナーの指摘の再現）"""
        page = para_page()
        tg = v3.find_target([page], "これまで1,000件以上の撮影を手がけ", "1-1", keyword="1,000件以上")
        old = v3.frame_rect(page, 0, tg.zoom_rect, 0.34, v3.ZOOM_MAX, zmin=1.6, line=tg.rect, chars=tg.line_chars)
        self.assertFalse(gate._inside(list(tg.span), list(v3.cam_view(old)), v3.QA_MARGIN_X / old.s, v3.QA_MARGIN_Y / old.s))


def geo(**over) -> dict:
    g = {"beat": 1, "page": 0, "rows": [[100, 100, 900, 140], [100, 160, 500, 200]], "line": [100, 100, 900, 200],
         "sel": [[100, 100, 900, 140], [100, 160, 500, 200]], "t0": 1.0, "t1": 3.0, "target": "これまで1,000件以上の撮影を手がけ、",
         "ja": "", "keyword": "1,000件以上", "sub": "", "speech": "これまで1,000件以上の撮影を手がけてきたプロです。",
         "views": [[1.0, 1.0, 0, 0, 1144, 952], [2.0, 1.0, 0, 0, 1144, 952]], "t_peak": 2.0, "phrase_ok": True, "margin": [70, 16]}
    g.update(over)
    return g


def report_with(*gs) -> dict:
    return {"geometry": list(gs), "script_v3": {"beats": [{"keyword": "x"}]}}


@unittest.skipUnless(READY, "ffmpeg / numpy / Pillow / budoux が必要")
class GeometryGateTest(unittest.TestCase):
    def test_good_passes(self):
        self.assertEqual(gate.geometry_problems(report_with(geo())), [])

    def test_missing_record_fails(self):
        self.assertTrue(gate.geometry_problems({"script_v3": {"beats": []}}))

    def test_a_highlight_off_text_or_fragment(self):
        p = gate.geometry_problems(report_with(geo(sel=[[100, 100, 900, 140], [600, 160, 1000, 200]])))
        self.assertTrue(any(x.startswith("QA(a)") and "はみ出し" in x for x in p), p)
        p = gate.geometry_problems(report_with(geo(phrase_ok=False, target="これま")))
        self.assertTrue(any("途中で切れて" in x for x in p), p)

    def test_b_line_start_cropped(self):
        p = gate.geometry_problems(report_with(geo(views=[[1.0, 1.0, 0, 0, 1144, 952], [2.0, 2.0, 400, 50, 972, 526]])))
        self.assertTrue(any(x.startswith("QA(b)") and "2.0秒" in x for x in p), p)
        p = gate.geometry_problems(report_with(geo(views=[[1.0, 1.0, 120, 0, 1264, 952]])))  # 余白が無い（左の端の 20px 内側）
        self.assertTrue(any(x.startswith("QA(b)") for x in p), p)

    def test_c_keyword_not_in_narration_or_evidence(self):
        p = gate.geometry_problems(report_with(geo(speech="プロに頼む前に、自分で撮ってみましょう。")))
        self.assertTrue(any("読み上げに" in x for x in p), p)
        p = gate.geometry_problems(report_with(geo(keyword="2,000件以上", speech="2,000件以上です。")))
        self.assertTrue(any("選んだ文" in x for x in p), p)

    def test_english_keyword_reads_in_katakana(self):
        self.assertTrue(gate.narration_ok("Recovery", "リカバリーが自動で直します。"))
        self.assertFalse(gate.narration_ok("夜間の停止", "朝に確認するだけです。"))


@unittest.skipUnless(READY, "ffmpeg / numpy / Pillow / budoux が必要")
class RenderQATest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = (v3.TARGET_MIN, v3.TARGET_MAX, v3.LENGTH_RANGE)
        v3.TARGET_MIN, v3.TARGET_MAX, v3.LENGTH_RANGE = 24.0, 42.0, (20.0, 45.0)
        cls.tmp = Path(tempfile.mkdtemp())
        cls.report = v3.make_video(
            "fixture-ai-plan", cls.tmp, tts="fake", article=base.article(), pages=[base.fake_page()], script=copy.deepcopy(base.SCRIPT),
            profile="test", fps=10, out_scale=0.25, workers=2, sheet=False,
        )  # fmt: skip

    @classmethod
    def tearDownClass(cls):
        v3.TARGET_MIN, v3.TARGET_MAX, v3.LENGTH_RANGE = cls._saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_geometry_recorded_and_passes(self):
        r = self.report
        self.assertTrue(r["gate"]["ok"], r["gate"]["reasons"])
        self.assertEqual(r["gate"]["qa"], {"geometry": True, "vision": False, "version": v3.QA_VERSION})
        beats = {g["beat"] for g in r["geometry"]}
        self.assertTrue({0, 1, 2, 3, 4} <= beats)
        for g in r["geometry"]:
            self.assertTrue(g["views"], g["beat"])
            self.assertTrue(g["phrase_ok"], g["target"])
        self.assertEqual(gate.geometry_problems(r), [])

    def test_hook_highlights_every_row(self):
        hook = next(g for g in self.report["geometry"] if g["beat"] == 0)
        self.assertEqual(len(hook["sel"]), len(hook["rows"]))

    def test_broken_geometry_fails_gate(self):
        bad = copy.deepcopy(self.report)
        g = next(x for x in bad["geometry"] if x["beat"] == 1)
        g["views"] = [[v[0], v[1], v[2] + 600, v[3], v[4] + 600, v[5]] for v in g["views"]]  # 右へずらす＝行の頭が切れる
        res = gate.check_video(Path(self.report["mp4"]), bad, base.article(), "test")
        self.assertFalse(res.ok)
        self.assertTrue(any("QA(b)" in x for x in res.reasons), res.reasons)
        self.assertFalse(res.metrics.get("qa_geometry"))

    # ---- AI の目視の確認（偽物） -------------------------------------------- #
    def versions(self):
        return {"full": copy.deepcopy(self.report), "short": copy.deepcopy(self.report)}

    def fake_vision(self, bad_index: int | None = None):
        calls = []

        def run(system, prompt, images, schema, **kw):
            calls.append((prompt, len(images)))
            self.assertEqual(kw.get("purpose"), "video_review")
            frames = [{"index": i, "highlight_ok": i != bad_index, "framing_ok": True, "coherent": True, "issue": "行の終わりの「これま」だけ" if i == bad_index else ""}
                      for i in range(len(images))]
            return {"frames": frames}, "fake"

        return run, calls

    def test_vision_pass_sets_qa(self):
        run, calls = self.fake_vision()
        with mock.patch("llm.generate_json_images", run):
            out = v3.qa_versions("fixture-ai-plan", self.versions(), regen=None)
        self.assertEqual(len(calls), 1)  # 2本まとめて1回
        self.assertGreater(calls[0][1], 2)
        for r in out.values():
            self.assertEqual(r["gate"]["qa"], {"geometry": True, "vision": True, "version": v3.QA_VERSION})
            self.assertTrue(r["gate"]["ok"])

    def test_vision_fail_regenerates_once(self):
        run, calls = self.fake_vision(bad_index=1)
        regens = []

        def regen(note):
            regens.append(note)
            return self.versions()

        with mock.patch("llm.generate_json_images", run):
            out = v3.qa_versions("fixture-ai-plan", self.versions(), regen=regen)
        self.assertEqual(len(regens), 1)  # 1回だけ作り直す
        self.assertIn("前回の動画の確認で見つかった問題", regens[0])
        self.assertIn("これま", regens[0])
        self.assertEqual(len(calls), 2)
        bad = [r for r in out.values() if not r["gate"]["qa"]["vision"]]
        self.assertTrue(bad)
        for r in bad:
            self.assertFalse(r["gate"]["ok"])
            self.assertTrue(any("QA(目視)" in x for x in r["gate"]["reasons"]))

    def test_vision_unavailable_blocks_posting(self):
        def boom(*a, **k):
            raise RuntimeError("画像の確認に Claude API を使えない（ANTHROPIC_API_KEY が未設定）")

        regens = []
        with mock.patch("llm.generate_json_images", boom):
            out = v3.qa_versions("fixture-ai-plan", self.versions(), regen=lambda n: regens.append(n) or self.versions())
        self.assertEqual(regens, [])  # 確認できないときは作り直さない
        for r in out.values():
            self.assertFalse(r["gate"]["qa"]["vision"])
            self.assertFalse(r["gate"]["ok"])
            self.assertTrue(any("目視の確認ができない" in x for x in r["gate"]["reasons"]))

    def test_geometry_fail_skips_vision(self):
        vs = self.versions()
        for r in vs.values():
            r["gate"]["ok"] = False
            r["gate"]["qa"]["geometry"] = False
            r["gate"]["reasons"] = ["v3: QA(b): 場面1の2.0秒で証拠の文が画面の端で切れている・余白が無い"]
        run, calls = self.fake_vision()
        with mock.patch("llm.generate_json_images", run):
            out = v3.qa_versions("fixture-ai-plan", vs, regen=lambda n: vs)
        self.assertEqual(calls, [])
        self.assertFalse(out["short"]["gate"]["qa"]["vision"])


if __name__ == "__main__":
    unittest.main()
