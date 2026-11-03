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
            out, report = g.clean_markdown(case["markdown"], titles=case.get("titles"))
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

    def test_register_and_lists_and_overcut(self):
        old = "比較が行われました。商用モデルに匹敵する結果を示しました。"
        self.assertTrue(any("です・ます" in p for p in sp.rewrite_problems("x", old, "比較が行われた。", old)))
        lst = "数字は次のとおりです。\n\n- 要素：数千\n- 言語：23\n- 人数：最大6人"
        self.assertTrue(any("箇条書き" in p for p in sp.rewrite_problems("x", lst, "要素は数千、言語は23、人数は最大6人です。", lst)))
        long = "クレジットの項目はあるのに、金額が空欄の状態でした。今回は100ドルに置き換わっています。何ドル分もらえるのかが、一目で分かります。前の文には金額がありませんでした。"
        self.assertTrue(any("削りすぎ" in p for p in sp.rewrite_problems("x", long, "100ドルです。", long)))

    def test_titles(self):
        self.assertTrue(sp.valid_title("ランクルFJを検討中の人は何が変わる？", "自分に関係ある？", "", "ランクルFJ ランドクルーザー"))
        self.assertFalse(sp.valid_title("自分に関係ある？", "自分に関係ある？", "", ""))
        self.assertFalse(sp.valid_title("価格は300万円から", "料金は？", "", "価格"))  # 本文に無い数字
        self.assertFalse(sp.valid_title("何がすごいのか", "何がすごいの？", "", ""))

    def test_professional_register(self):
        """「〜しましょう」「〜してみましょう」は「〜するとよいでしょう」に（2026-11-03 オーナーのルール A17）。
        表示の段（lib/article-sections.ts）と同じ見本 tests/fixtures/register_cases.json"""
        cases = json.loads((ROOT / "tests" / "fixtures" / "register_cases.json").read_text("utf-8"))
        for sentence, want in cases["sentences"]:
            self.assertEqual(g.professional(sentence), want, sentence)
        for take, want in cases["takes"]:
            self.assertEqual(g.clean_take(take), want, take)
        # 生成の門: 判定のひとことの「〜しましょう」は書き出しの前に直し（apply）、直せない文だけ問題にする
        record = {"body": "## 概要\n\nA社が発表しました。", "easy": "", "verdict": {"take": "公式サイトで試してみましょう。"}}
        g.apply(record)
        self.assertEqual(record["verdict"]["take"], "公式サイトで試してみるとよいでしょう。")
        self.assertEqual([], [p for p in g.style_problems(record) if "しましょう" in p])
        stuck = {"body": "## 概要\n\nウェブましょう。", "easy": ""}
        self.assertTrue(any("しましょう" in p for p in g.style_problems(stuck)))

    def test_no_data_cells_and_source_non_answers(self):
        """表の「記載なし」は空欄（全部空の列は列ごと消す）。「元記事には書かれていません」は文ごと消す（2026-11-03 W11・A2）"""
        md = ("## 他社との比較\n\n| 項目 | A | B | C |\n|---|---|---|---|\n| 重さ | 公式に記載なし | 1.2kg | 公式に記載なし |\n| 価格 | 10万円 | 12万円 | — |\n\n"
              "- 「公式に記載なし」は、その会社の公式ページに書かれていない項目です。\n\n"
              "## 料金\n\n本体は10万円です（元記事では税の扱いは言及なし）。保証の料金は、元記事には書かれていません。\n")
        out, _ = g.clean_markdown(md)
        self.assertNotIn("記載なし", out)
        self.assertNotIn("元記事", out)
        self.assertIn("| 重さ |  | 1.2kg |", out)
        self.assertNotIn("| C |", out)
        self.assertIn("空欄は、", out)
        self.assertIn("本体は10万円です。", out)
        # 確かめ済みの比較表（scripts/rivals.py）も、表の「公式に記載なし」は空欄にする（文は消さない）
        rival = f"## {g.DATA_RIVAL_HEADING}\n\n| 項目 | A | B |\n|---|---|---|\n| 重さ | 1.2kg | 公式に記載なし |\n| 価格 | 10万円 | 12万円 |\n\n- 「公式に記載なし」は公式ページに無い項目です。\n"
        out, _ = g.clean_markdown(rival)
        self.assertNotIn("記載なし", out)
        self.assertIn("| 重さ | 1.2kg |  |", out)

    def test_tables_are_masked(self):
        content = "前文です。\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n後文です。"
        masked, tables = sp.mask_tables(content)
        self.assertNotIn("|", masked)
        self.assertEqual(sp.unmask_tables(masked, tables), content)


if __name__ == "__main__":
    unittest.main()
