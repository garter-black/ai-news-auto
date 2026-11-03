"""章の門（scripts/section_gate.py）。表示の段（lib/article-sections.ts）と同じ結果になる見本は tests/fixtures/section_gate_cases.json"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import section_gate as g  # noqa: E402
import section_polish as sp  # noqa: E402

CASES = json.loads((ROOT / "tests" / "fixtures" / "section_gate_cases.json").read_text("utf-8"))


class SectionGateTest(unittest.TestCase):
    def test_fixtures(self):
        for case in CASES:
            out, report = g.clean_markdown(case["markdown"])
            self.assertEqual(out, case["expected"], case["name"])
            self.assertEqual(report["hidden"], case["hidden"], case["name"])

    def test_rival_without_material_is_hidden(self):
        md = "## ライバルと比べてどう？\n\n元記事には、他社製品との比較は書かれていません。ここは編集部注です。iPadで使えるデザインアプリには、Adobeの製品群などがあります。\n\n性能や使い勝手の優劣は、元記事からは判断できません。\n"
        self.assertEqual(g.clean_markdown(md)[1]["hidden"], ["ライバルと比べてどう？"])

    def test_rival_with_numbers_is_kept(self):
        md = "## 競合サービスとの比較\n\n| 項目 | A | B |\n| --- | --- | --- |\n| 価格 | 月20ドル | 月25ドル |\n| 上限 | 100回 | 80回 |\n"
        self.assertEqual(g.clean_markdown(md)[1]["hidden"], [])

    def test_outdated_rival_is_hidden(self):
        md = "## ライバルと比べてどう？\n\nGPT-4oは月20ドル、今回は月10ドルです。\n"
        self.assertEqual(g.clean_markdown(md)[1]["hidden"], ["ライバルと比べてどう？"])

    def test_filler_without_facts_is_dropped_but_facts_stay(self):
        md = "## 自分に関係ある？\n\n2027年1月以降に新しい車の購入を考えている人に関係があります。レトロ調のデザインが好きな人にとっても重要です。具体的な選択肢が増えるという点でチェックしておきたい情報といえます。\n"
        out, _ = g.clean_markdown(md)
        self.assertIn("2027年1月以降", out)
        self.assertNotIn("重要です", out)
        self.assertNotIn("チェックしておきたい", out)

    def test_price_section_with_price_is_kept(self):
        md = "## 料金は？\n\nブルームバーグによると、価格は229ドルです。発売日は11月6日です。日本円での価格は、元記事では言及なしです。\n"
        self.assertEqual(g.clean_markdown(md)[1]["hidden"], [])

    def test_generation_keeps_index_headings(self):
        md = "## 日本で使える？\n\n日本での提供は、元記事には書かれていません。\n"
        self.assertEqual(g.clean_markdown(md)[1]["hidden"], ["日本で使える？"])
        self.assertEqual(g.clean_markdown(md, keep_index_headings=True)[1]["hidden"], [])

    def test_style_problems(self):
        record = {"body": "## 概要\n\nSUV市場において重要な要素があります。カスタムパーツの充実です。\n", "easy": "## ひとことで言うと\n\nこれはすごい話です。\n"}
        problems = " ".join(g.style_problems(record))
        self.assertIn("割っている", problems)
        self.assertIn("幼い言い方", problems)


class SectionPolishCheckTest(unittest.TestCase):
    OLD = "2027年1月以降に新しい車の購入を考えている人に関係があります。レトロ調のデザインが好きな人にとっても重要です。価格は元記事に書かれていません（11月2日時点）。"

    def test_rewrite_keeps_numbers(self):
        new = "2027年1月以降に車の購入を考えている人向けの選択肢です。"
        self.assertEqual(sp.rewrite_problems("自分に関係ある？", self.OLD, new, self.OLD), [])

    def test_rewrite_losing_numbers_is_rejected(self):
        self.assertTrue(sp.rewrite_problems("自分に関係ある？", self.OLD, "車の購入を考えている人向けです。", self.OLD))

    def test_rewrite_adding_numbers_is_rejected(self):
        new = "2027年1月以降、300万円で買える車です。"
        self.assertTrue(any("増えた" in p for p in sp.rewrite_problems("自分に関係ある？", self.OLD, new, self.OLD)))

    def test_titles(self):
        self.assertTrue(sp.valid_title("ランクルFJを検討中の人は何が変わる？", "自分に関係ある？", "", "ランクルFJ ランドクルーザー"))
        self.assertFalse(sp.valid_title("自分に関係ある？", "自分に関係ある？", "", ""))
        self.assertFalse(sp.valid_title("価格は300万円から", "料金は？", "", "価格"))  # 本文に無い数字
        self.assertFalse(sp.valid_title("何がすごいのか", "何がすごいの？", "", ""))

    def test_tables_are_masked(self):
        content = "前文です。\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n後文です。"
        masked, tables = sp.mask_tables(content)
        self.assertNotIn("|", masked)
        self.assertEqual(sp.unmask_tables(masked, tables), content)


if __name__ == "__main__":
    unittest.main()
