"""SNS 動画 v2 のゴールデンテスト。決まった台本・テスト用の合成音・合成した写真で
3デザインを小さな解像度（270x480・10fps）で実際に書き出し、品質ゲート（test プロファイル）と
構造（長さ・区間の順番・つかみが0秒・文字が安全領域内）を確かめる。画素の一致は見ない。

  python3 -m unittest tests/test_shorts_v2_golden.py -v
ネット・LLM・VOICEVOX は使わない。ffmpeg / Pillow / numpy / budoux が無い環境では飛ばす。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
FIX = ROOT / "tests" / "fixtures" / "video"

try:
    import budoux  # noqa: F401
    import numpy as np
    from PIL import Image

    import shorts
    import shorts_v2 as v2
    import video_gate as gate

    READY = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
except ImportError:  # pragma: no cover
    READY = False


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text("utf-8"))


def fake_photo(_url: str, dark: bool = False):
    """写真の代わり（横長のグラデーション＋図形）。明るい写真と暗い写真の両方を試す"""
    w, h = 1200, 675
    x = np.linspace(0, 1, w)[None, :, None]
    y = np.linspace(0, 1, h)[:, None, None]
    base = np.array([40, 60, 90]) if dark else np.array([200, 210, 225])
    arr = base + 50 * x * np.array([1, 0.5, 0]) - 40 * y
    img = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGB")
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    for i in range(6):
        d.ellipse((100 + i * 170, 200, 220 + i * 170, 320), fill=(255, 255, 255) if dark else (30, 40, 60))
    return img


@unittest.skipUnless(READY, "ffmpeg / Pillow / numpy / budoux が必要")
class GoldenRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls._fetch, cls._usable = shorts.fetch_image, v2.usable_photo
        v2.usable_photo = lambda a: True

    @classmethod
    def tearDownClass(cls):
        shorts.fetch_image, v2.usable_photo = cls._fetch, cls._usable
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def render(self, fixture: str, designs: list[str], dark: bool = False) -> list[dict]:
        shorts.fetch_image = lambda url: fake_photo(url, dark)
        article = load(f"{fixture}.json")
        script = load(f"{fixture}.script.json")
        return v2.make_videos(
            article["slug"], designs, self.tmp, tts="fake", profile="test", script=script, article=article,
            fps=10, preset="ultrafast", out_scale=0.25, sheet=False, pages=False,
        )  # fmt: skip

    def assert_structure(self, report: dict):
        self.assertTrue(report["gate"]["ok"], report["gate"]["reasons"])
        m = report["gate"]["metrics"]
        self.assertEqual(m["size"], "270x480")
        self.assertTrue(gate.MIN_SEC <= m["duration"] <= gate.MAX_SEC)
        self.assertAlmostEqual(m["lufs"], -14.0, delta=1.5)
        kinds = [u["kind"] for u in report["units"]]
        self.assertEqual(kinds[0], "hook")
        self.assertEqual(kinds[-1], "use")
        self.assertEqual(sum(1 for k in kinds if k == "point") >= 3, True)
        self.assertEqual(report["units"][0]["chunks"][0]["start"], 0.0)
        for t in report["texts"]:
            if t.get("safe", True):
                self.assertTrue(gate._inside(t["bbox"], gate.SAFE), t)
        roles = {t["role"] for t in report["texts"]}
        self.assertTrue({"hook", "caption", "label", "logo", "end_cta", "end_url", "end_credit"} <= roles, roles)
        self.assertIn("ai-tech-express.jp", report["credits_shown"])
        self.assertGreater(report["end_start"], 0)
        self.assertGreaterEqual(report["total"] - report["end_start"], v2.END_MIN - 0.01)

    def test_three_designs_bright_photo(self):
        reports = self.render("fixture_ai", ["A", "B", "C"])
        self.assertEqual([r["design"] for r in reports], ["A", "B", "C"])
        for r in reports:
            self.assert_structure(r)
            # 公式発表の写真のクレジットが画面に出ている
            self.assertTrue(any("Fixture AI（公式発表より）" in c for c in r["credits_shown"]), r["credits_shown"])
            # 数字カード（字幕に同じ数字が出る要点だけ）
            self.assertIn("number", {t["role"] for t in r["texts"]})
        # 公式ページを撮らない（テストはネット無し）ので C は「数字カード型」
        self.assertEqual(reports[2]["design_name"], "数字カード型")

    def test_dark_photo_money_article(self):
        for r in self.render("fixture_money", ["A", "B"], dark=True):
            self.assert_structure(r)
            self.assertTrue(any("AI生成" in c for c in r["credits_shown"]))

    def test_text_that_does_not_fit_fails_safely(self):
        """入らない長さの字幕は縮めすぎずに「不合格（動画なし）」にする。例外で止まらない"""
        script = load("fixture_ai.script.json")
        script["hook"]["caption"] = "Fixture AIに新プラン" * 6
        article = load("fixture_ai.json")
        shorts.fetch_image = fake_photo
        reports = v2.make_videos(article["slug"], ["A"], self.tmp, tts="fake", profile="test", script=script, article=article, fps=10, preset="ultrafast", out_scale=0.25, sheet=False, pages=False, tag="_long")
        self.assertFalse(reports[0]["gate"]["ok"])
        self.assertTrue(any("入りません" in r or "はみ出" in r for r in reports[0]["gate"]["reasons"]), reports[0]["gate"]["reasons"])

    def test_wrap_keeps_numbers_and_words(self):
        f = shorts.font("black", 72)
        rows = v2.wrap_jp("最初のモデル「Sawara Small」は2027年春に公開予定", f, 830)
        self.assertTrue(any("2027年春" in r for r in rows), rows)
        self.assertFalse(any(r.startswith(("、", "。", "ー")) for r in rows))


if __name__ == "__main__":
    unittest.main()
