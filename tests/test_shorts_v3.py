"""SNS 動画 v3（画面で見せる型）のテスト。

- 描画のソースに飾りの線・帯・枠が無い（lint）
- v3 の追加ゲート（題の出しっぱなし・文字の面積・数字の文脈・グラフの倍率）が壊れた記録で落ちる
- 合成した「公式ページ」（Pillow で描いた料金表）と決まった台本・テスト用の合成音で、1本を
  480x270（横型）・10fps で実際に書き出し、品質ゲート（test プロファイル＋v3 の基準）に通る

  python3 -m unittest tests/test_shorts_v3.py -v
ネット・LLM・VOICEVOX・ブラウザは使わない。ffmpeg / numpy / Pillow / budoux が無ければ飛ばす。
"""

from __future__ import annotations

import copy
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
    from PIL import Image, ImageDraw, ImageFont

    import shorts_v3 as v3
    import video_gate as gate

    READY = shutil.which("ffmpeg") is not None
except ImportError:  # pragma: no cover
    READY = False


def article() -> dict:
    return json.loads((FIX / "fixture_ai.json").read_text("utf-8"))


SCRIPT = {
    "hook": {"text": "Fixture AIの新プラン、月3000円の価値ある？", "payoff": "月3000円", "speech": "フィクスチャーエーアイの新プラン、月3000円の価値はある？", "block": "", "target": "¥3000"},
    "beats": [
        {"keyword": "毎日使う人向け", "sub": "仕事で毎日使う人", "speech": "仕事で毎日たくさん使う人に向いたプランです。", "block": "", "target": "毎日たくさん使う人に"},
        {"keyword": "利用量5倍", "sub": "通常プランと比べて", "speech": "利用量は通常プランの5倍になります。", "block": "", "target": "通常プランの5倍"},
        {"keyword": "日本でも提供", "sub": "2026年11月から", "speech": "日本でも2026年11月から提供されます。", "block": "", "target": "2026年11月から"},
        {"keyword": "月額3000円", "sub": "", "speech": "料金は月額3000円です。", "block": "", "target": "¥3000"},
    ],
    "compare": {"beat": 2, "base": "通常", "subject": "Pro Max", "ratio": "5倍"},
    "conclusion": {"text": "毎日使う人なら検討の価値", "speech": "毎日たくさん使う人なら、検討する価値があります。"},
    "title": "Fixture AIに新プランPro Max 月額3000円で利用量は5倍",
    "summary": "要約すると、Fixture AIが新プランPro Maxを発表しました。月額3000円で、利用量は通常プランの5倍です。日本でも2026年11月から提供されます。",
    "hashtags": ["#FixtureAI", "#AI"],
}


def fake_page() -> "v3.Page":
    """料金表らしいページを Pillow で描き、1文字ずつの枠も作る（ブラウザで撮ったのと同じ形）"""
    k = v3.CAPTURE_DPR
    w, h = v3.CAPTURE_W * k, 2400 * k
    img = Image.new("RGB", (w, h), (250, 249, 245))
    d = ImageDraw.Draw(img)
    font_path = str(ROOT / "assets" / "fonts" / "NotoSansJP-Bold.otf")
    rows = [
        ("Fixture AI の料金", 44, 120),
        ("Pro Max", 36, 420),
        ("毎日たくさん使う人に", 18, 480),
        ("¥3000", 30, 540),
        ("月額", 14, 590),
        ("通常プランの5倍の利用量", 18, 760),
        ("日本では2026年11月から提供", 18, 820),
        ("価格は税込みです。", 14, 1200),
    ]
    blocks = []
    for i, (text, size, y) in enumerate(rows, 1):
        f = ImageFont.truetype(font_path, size * k)
        x = 40 * k
        d.text((x, y * k), text, font=f, fill=(20, 20, 24))
        chars = []
        for j, ch in enumerate(text):
            x0 = x + f.getlength(text[:j])
            x1 = x + f.getlength(text[: j + 1])
            chars.append((ch, (x0, y * k, x1, (y + size * 1.3) * k) if ch.strip() else None))
        rect = (x, y * k, x + f.getlength(text), (y + size * 1.3) * k)
        blocks.append(v3.Block(f"1-{i}", text, rect, chars, float(size)))
    for yy in range(1400, 2400, 40):  # 下の方の本文らしい模様
        d.text((40 * k, yy * k), "本文 " * 12, font=ImageFont.truetype(font_path, 14 * k), fill=(90, 90, 96))
    return v3.Page("https://example.com/fixture-ai/news", "example.com", np.asarray(img), blocks, "Fixture AI")


@unittest.skipUnless(READY, "ffmpeg / numpy / Pillow / budoux が必要")
class LintAndGateTest(unittest.TestCase):
    def test_renderer_has_no_lines_or_bars(self):
        self.assertEqual(gate.lint_renderer(ROOT / "scripts" / "shorts_v3.py"), [])

    def test_lint_catches_v2_bars(self):
        self.assertTrue(gate.lint_renderer(ROOT / "scripts" / "shorts_v2.py"))

    def test_number_context(self):
        bad = copy.deepcopy(SCRIPT)
        bad["beats"][3] = {"keyword": "APIクレジット", "sub": "月3000円分", "speech": "エーピーアイクレジットが月3000円分つきます。", "block": "", "target": "¥3000"}
        self.assertEqual(gate.number_context_problems(SCRIPT, article()), [])
        self.assertTrue(gate.number_context_problems(bad, article()))

    def test_text_ratio_and_title(self):
        texts = [{"role": "hook", "bbox": [64, 178, 950, 418], "t0": 0, "t1": 3.0}, {"role": "keyword", "bbox": [0, 400, 1080, 1200], "t0": 4, "t1": 6}]
        ratio, at = gate.text_ratio_max(texts, 8)
        self.assertGreater(ratio, gate.V3_TEXT_RATIO)
        self.assertAlmostEqual(at, 4.0, delta=0.11)

    def test_target_and_focus(self):
        page = fake_page()
        t = v3.find_target([page], "通常プランの5倍", keyword="利用量5倍")
        self.assertIsNotNone(t)
        self.assertEqual(t.block.id, "1-6")
        self.assertIsNotNone(t.focus)
        self.assertLess(t.focus[2] - t.focus[0], t.rect[2] - t.rect[0])
        self.assertIsNone(v3.find_target([page], "ページに無い文字", keyword=""))


@unittest.skipUnless(READY, "ffmpeg / numpy / Pillow / budoux が必要")
class GoldenRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 合成の短い台本（4場面）なので、長さの範囲だけ短い版にする（本番は 45〜75秒）
        cls._saved = (v3.TARGET_MIN, v3.TARGET_MAX, v3.LENGTH_RANGE)
        v3.TARGET_MIN, v3.TARGET_MAX, v3.LENGTH_RANGE = 20.5, 30.5, (20.0, 32.0)
        cls.tmp = Path(tempfile.mkdtemp())
        cls.report = v3.make_video(
            "fixture-ai-plan", cls.tmp, tts="fake", article=article(), pages=[fake_page()], script=copy.deepcopy(SCRIPT),
            profile="test", fps=10, out_scale=0.25, workers=2, sheet=False,
        )  # fmt: skip

    @classmethod
    def tearDownClass(cls):
        v3.TARGET_MIN, v3.TARGET_MAX, v3.LENGTH_RANGE = cls._saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_gate_passes(self):
        self.assertEqual(self.report["errors"], [])
        self.assertTrue(self.report["gate"]["ok"], self.report["gate"]["reasons"])

    def test_structure(self):
        r = self.report
        m = r["gate"]["metrics"]
        self.assertGreaterEqual(m["v3_changes"], gate.V3_MIN_CHANGES)
        self.assertTrue(gate.V3_SEC[0] <= m["duration"] <= gate.V3_SEC[1])
        self.assertTrue(gate.V3_SEPARATION[0] <= m["v3_separation_db"] <= gate.V3_SEPARATION[1])
        hook = [t for t in r["texts"] if t["role"] == "hook"][0]
        self.assertEqual(hook["t0"], 0.0)
        self.assertLessEqual(hook["t1"], gate.V3_TITLE_MAX)
        # グラフの倍率は台本のまま・途中の数字は描かない
        self.assertEqual([c["ratio"] for c in r["charts"]], ["5倍"])
        self.assertNotIn("4倍", r["rendered_strings"])
        self.assertLessEqual(r["total"] - r["end_start"], 1.5)

    def test_broken_report_fails(self):
        mp4 = self.tmp / "fixture-ai-plan__v3.mp4"
        bad = copy.deepcopy(self.report)
        bad["charts"][0]["ratio"] = "8倍"
        next(t for t in bad["texts"] if t["role"] == "hook")["t1"] = 5.0
        bad["audio"]["separation_db"] = 4.0
        res = gate.check_video(mp4, bad, article(), "test")
        joined = " ".join(res.reasons)
        self.assertFalse(res.ok)
        self.assertIn("グラフの倍率", joined)
        self.assertIn("つかみの文字", joined)
        self.assertIn("声とBGM", joined)


if __name__ == "__main__":
    unittest.main()
