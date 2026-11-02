"""scripts/article_charts.py：記事の表から図（charts）を決まった手順で作る部品の検査。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import article_charts as ac  # noqa: E402


def article(body, title="テスト", **extra):
    return {"slug": "t", "title": title, "body": body, "tags": [], "sourceName": "公式発表", "sourceUrl": "https://example.com/a", **extra}


TABLE = """## 数字で見るとどう変わった？

| 比べること | 前 | 今回 | 変化 |
| --- | --- | --- | --- |
| 並ぶ項目数 | 21項目 | 9項目 | **12項目減** |
| 速さ | 数日〜数週間 | 1日以内 | 大幅 |

## まとめ
"""


class ParseValue(unittest.TestCase):
    def test_plain_values(self):
        self.assertEqual(ac.parse_value("21項目")["value"], 21)
        self.assertEqual(ac.parse_value("$200")["unit"], "ドル")
        v = ac.parse_value("1,500万口座")
        self.assertEqual((v["value"], v["unit"]), (15_000_000, "口座"))
        self.assertEqual(ac.parse_value("3.0％ (8月)")["note"], "8月")

    def test_fuzzy_values_are_rejected(self):
        for cell in ["数日〜数週間", "25時間超", "3時間未満", "最大10コア", "言及なし", "数千行", "非公表", "基準"]:
            self.assertIsNone(ac.parse_value(cell), cell)

    def test_non_period_note_stays_in_display(self):
        v = ac.parse_value("1.18倍（推計・前月）")
        self.assertIsNone(v["note"])
        self.assertIn("推計", v["display"])


class BeforeAfter(unittest.TestCase):
    def test_before_after_table(self):
        charts = ac.derive_charts(article(TABLE))
        self.assertEqual(len(charts), 1)
        c = charts[0]
        self.assertEqual(c["kind"], "before_after")
        self.assertEqual(c["afterHeading"], "数字で見るとどう変わった？")
        items = c["groups"][0]["items"]
        self.assertEqual([i["display"] for i in items], ["21項目", "9項目"])
        self.assertTrue(items[1]["highlight"])
        self.assertEqual(c["source"]["url"], "https://example.com/a")

    def test_needs_two_comparable_values(self):
        body = TABLE.replace("| 並ぶ項目数 | 21項目 | 9項目 | **12項目減** |", "| 価格 | 200ドル | 200ドル | 据え置き |")
        self.assertEqual(ac.derive_charts(article(body)), [])
        body = TABLE.replace("21項目 | 9項目", "21項目 | 9ドル")
        self.assertEqual(ac.derive_charts(article(body)), [])

    def test_huge_ratio_is_not_charted(self):
        body = TABLE.replace("21項目 | 9項目", "1件 | 2416件")
        self.assertEqual(ac.derive_charts(article(body)), [])

    def test_competitor_tables_are_ignored(self):
        body = "## ライバル\n\n| 比べること | A社 | B社 | 有利なのは |\n| --- | --- | --- | --- |\n| 料金 | 20ドル | 25ドル | A |\n"
        self.assertEqual(ac.derive_charts(article(body)), [])

    def test_from_to_needs_a_dated_before_value(self):
        body = "## 概要\n2026年4月に1,400万口座に到達して以来、1,500万口座を突破。\n\n## 数字で見ると？\n\n| 項目 | 数値 | 出典 |\n| --- | --- | --- |\n| 口座増の期間 | 約7カ月（1400万から1500万） | 発表 |\n"
        charts = ac.derive_charts(article(body, publishedAt="2026-11-02T00:00:00+00:00"))
        self.assertEqual(charts[0]["title"], "口座数の変化")
        self.assertEqual([i["label"] for i in charts[0]["groups"][0]["items"]], ["2026年4月", "2026年11月（今回）"])
        undated = body.replace("2026年4月に", "")
        self.assertEqual(ac.derive_charts(article(undated)), [])


class Ladder(unittest.TestCase):
    def test_plan_article_gets_ladder(self):
        a = article("## 料金は？\n月200ドル\n", title="対話AI「Claude」の最上位プランが「Ultra」に 月200ドル")
        charts = ac.derive_charts(a)
        self.assertEqual(charts, [{"kind": "ladder", "product": "claude", "highlight": "ultra", "afterHeading": "料金は？"}])

    def test_seo_title_template_does_not_trigger_ladder(self):
        a = article("## 概要\n", title="ChatGPTのOpenAIがOracleと提携", seoTitle="OpenAI・Oracle提携とは？ChatGPTの料金")
        self.assertEqual(ac.derive_charts(a), [])

    def test_model_news_does_not_get_plan_ladder(self):
        a = article("## 概要\n", title="Googleの生成AI『Gemini』に最新モデル 料金も手頃に")
        self.assertEqual(ac.derive_charts(a), [])


if __name__ == "__main__":
    unittest.main()
