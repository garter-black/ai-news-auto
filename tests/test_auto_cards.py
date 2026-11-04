"""台本が場面ごとに画面の道具（カード）を選ぶ旗 SNS_VIDEO_AUTO_CARDS のテスト（ネット・LLM なし）。

- 旗が off（既定）: 台本の形・決まり・ゲートは今までと同じ
- 旗が on: 台本の形に cards・決まりにカードの説明・カードの検査（記事に無い数字・続けて同じ道具・左の一言と同じ言葉）
- ゲート: 旗が on で作った動画は道具2種類以上、選択の青と訳もカードも無い画面は4秒まで

  python3 -m unittest tests/test_auto_cards.py -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import shorts_v3 as v3
    import video_gate as gate

    READY = True
except ImportError:  # pragma: no cover
    READY = False

ARTICLE = {
    "slug": "x", "title": "NVIDIAが学習の自動復旧ソフトを公開",
    "lede": "NVIDIAは学習の障害を自動で直すソフトを公開しました。検知までは平均69秒です。",
    "body": "手作業だった復旧を自動にします。検知・特定・隔離・再開の4つを行います。", "highlights": [], "tags": [],
}  # fmt: skip
BEATS = [{"label": "これは何", "keyword": "夜中の対応が減る", "speech": "", "target": ""}, {"label": "速さ", "keyword": "平均69秒で検知", "speech": "", "target": ""},
         {"label": "流れ", "keyword": "4つを自動で", "speech": "", "target": ""}]  # fmt: skip


@unittest.skipUnless(READY, "numpy / Pillow が無い")
class FlagOffTest(unittest.TestCase):
    def test_off_is_unchanged(self):
        with mock.patch.dict(os.environ, {"SNS_VIDEO_AUTO_CARDS": "off"}):
            self.assertIs(v3.script_schema(), v3.SCRIPT_SCHEMA)
            self.assertNotIn("cards", v3.SCRIPT_SCHEMA["properties"])
            self.assertNotIn("画面の道具", v3.script_system())
            self.assertEqual(v3.card_problems({"beats": BEATS, "cards": []}, ARTICLE) and [], [])

    def test_gate_off_ignores_tools_and_idle(self):
        rep = {"ladders": [], "charts": [], "geometry": [], "subtitles": []}
        self.assertEqual(gate.page_idle_problems({**rep, "geometry": [{"t0": 0, "t1": 30, "target": "検知"}]}, 30), [])


@unittest.skipUnless(READY, "numpy / Pillow が無い")
class FlagOnTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"SNS_VIDEO_AUTO_CARDS": "on"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_schema_and_rules(self):
        self.assertIn("cards", v3.script_schema()["properties"])
        self.assertIn("cards", v3.script_schema()["required"])
        self.assertIn("画面の道具", v3.script_system())

    def test_good_cards_pass(self):
        cards = [{"beat": 2, "mode": "figures", "title": "検知の速さ", "rows": [{"label": "検知まで", "text": "", "value": "平均69秒"}]},
                 {"beat": 3, "mode": "specs", "title": "自動の流れ", "rows": [{"label": "", "text": "検知", "value": ""}, {"label": "", "text": "隔離", "value": ""}]}]  # fmt: skip
        self.assertEqual(v3.card_problems({"beats": BEATS, "cards": cards}, ARTICLE), [])

    def test_bad_cards_fail(self):
        cards = [{"beat": 2, "mode": "figures", "title": "検知の速さ", "rows": [{"label": "検知まで", "text": "", "value": "平均42秒"}]},  # 記事に無い数字
                 {"beat": 3, "mode": "figures", "title": "4つを自動で", "rows": [{"label": "流れ", "text": "4つを自動で", "value": "4つ"}]}]  # 同じ道具が続く・左と同じ言葉
        probs = " ".join(v3.card_problems({"beats": BEATS, "cards": cards}, ARTICLE))
        self.assertIn("記事に無い数字 42", probs)
        self.assertIn("2種類以上", probs)
        self.assertIn("続けて同じ道具", probs)
        self.assertIn("左の一言", probs)

    def test_short_version_keeps_cards_on_new_beats(self):
        script = {"hook": {"speech": "a"}, "beats": [{"speech": "b" * 75, "label": str(i)} for i in range(5)], "conclusion": {"speech": "c"},
                  "cards": [{"beat": 3, "mode": "figures", "rows": [{"label": "a", "value": "1"}]}], "short_beats": [3]}  # fmt: skip
        short = v3.short_script(script)
        self.assertEqual([c["beat"] for c in short["cards"]], [2])

    def test_normalize_for_renderer(self):
        got = v3.normalize_cards({"cards": [{"beat": 1, "mode": "specs", "rows": [{"label": "", "text": "検知", "value": ""}]}]})
        self.assertEqual(got[0]["rows"][0]["name"], "検知")
        self.assertEqual(got[0]["source"], "記事より")


@unittest.skipUnless(READY, "numpy / Pillow が無い")
class GateTest(unittest.TestCase):
    def test_page_without_highlight_and_caption_over_4s_fails(self):
        geo = [{"t0": 0.7, "t1": 3.0, "target": "GitHub Copilot is now available", "ja": "使えるように"},
               {"t0": 3.0, "t1": 12.0, "target": "Track multiple agent sessions.", "ja": "複数の作業を追える"}]  # fmt: skip
        rep = {"geometry": geo, "subtitles": [{"t0": 0.7, "t1": 3.0}, {"t0": 3.0, "t1": 5.0}], "ladders": [{"t0": 10.0, "t1": 12.0}], "charts": []}
        self.assertTrue(gate.page_idle_problems(rep, 12.0))  # 5〜10秒は英文に青だけで訳が無い
        rep["subtitles"].append({"t0": 5.0, "t1": 10.0})
        self.assertEqual(gate.page_idle_problems(rep, 12.0), [])

    def test_japanese_target_needs_only_highlight(self):
        rep = {"geometry": [{"t0": 0, "t1": 20, "target": "手作業だった復旧を自動にします", "ja": ""}], "subtitles": [], "ladders": [], "charts": []}
        self.assertEqual(gate.page_idle_problems(rep, 20), [])


@unittest.skipUnless(READY, "numpy / Pillow が無い")
class V3BarTest(unittest.TestCase):
    """report["gate"]["qa"]["v3"]（投稿の条件）: 道具2種類以上・青と訳の無い画面4秒まで・左は短い持ち帰る一言でカードと同じ言葉ではない"""

    def report(self, modes=("timeline", "checklist"), kw="契約そのまま", row="複数の作業を同時に頼める"):
        return {"ladders": [{"t0": 4 + 8 * i, "t1": 8 + 8 * i, "mode": m} for i, m in enumerate(modes)], "charts": [],
                "geometry": [{"t0": 0, "t1": 30, "target": "手作業だった復旧を自動にします", "ja": ""}], "subtitles": [],
                "script_v3": {"beats": [{"keyword": kw}], "cards": [{"beat": 1, "mode": modes[0], "rows": [{"label": "手元", "text": row}]}]}}  # fmt: skip

    def test_good(self):
        self.assertEqual(gate.v3_bar_problems(self.report(), 30), [])

    def test_one_tool_fails(self):
        self.assertTrue(any("道具" in p for p in gate.v3_bar_problems(self.report(modes=("timeline",)), 30)))

    def test_headline_same_as_card_fails(self):
        self.assertTrue(any("同じ言葉" in p for p in gate.v3_bar_problems(self.report(kw="同時に頼める"), 30)))

    def test_english_page_without_caption_fails(self):
        r = self.report()
        r["geometry"] = [{"t0": 0, "t1": 30, "target": "Track multiple agent sessions.", "ja": ""}]
        self.assertTrue(any("訳" in p for p in gate.v3_bar_problems(r, 30)))


@unittest.skipUnless(READY, "numpy / Pillow が無い")
class VoiceRolesTest(unittest.TestCase):
    """2026-11-04 オーナー承認の声: つかみはゆっくり・「ただし」の前は間・締めはゆっくり（九州そら・速さ1.38 が基準）"""

    def test_roles(self):
        self.assertEqual(v3.line_role(v3.Line("hook", 0, "a")), "hook")
        self.assertEqual(v3.line_role(v3.Line("beat", 2, "ただし、上限は決められません。")), "turn")
        self.assertEqual(v3.line_role(v3.Line("beat", 1, "手元でも使えます。")), "normal")
        self.assertEqual(v3.line_role(v3.Line("conclusion", 3, "確かめましょう。")), "end")
        self.assertAlmostEqual(v3.V3_VOICE["speed"] * v3.VOICE_ROLES["hook"][0], 1.32, places=2)
        self.assertEqual(v3.VOICE_ROLES["turn"][3], 0.45)
        self.assertTrue(v3.VOICE_ROLES_ON)


if __name__ == "__main__":
    unittest.main()
