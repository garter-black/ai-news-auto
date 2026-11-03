"""見出しの書き方をそろえる後処理（scripts/headline_normalize.py）の検証。外部 API は呼ばない。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import headline_normalize as h  # noqa: E402
import quality  # noqa: E402


class BracketTest(unittest.TestCase):
    def test_product_name_uses_kagi(self):
        self.assertEqual(h.normalize_title("Googleの生成AI『Gemini』に新機能"), "Googleの生成AI「Gemini」に新機能")

    def test_quotes_become_kagi(self):
        self.assertEqual(h.normalize_title("トヨタのランドクルーザー“FJ”を発売"), "トヨタのランドクルーザー「FJ」を発売")

    def test_works_keep_double_kagi(self):
        self.assertEqual(h.normalize_title("劇場アニメ『魔法使いの夜』の公開記念"), "劇場アニメ『魔法使いの夜』の公開記念")
        self.assertEqual(h.normalize_title("『紅の砂漠』が新DLC", {"genreSlug": "entertainment"}), "『紅の砂漠』が新DLC")

    def test_quote_inside_kagi_is_kept(self):
        self.assertEqual(h.normalize_title("「『AI』と働く」講座を開催"), "「『AI』と働く」講座を開催")


class SeparatorTest(unittest.TestCase):
    def test_space_after_japanese_becomes_fullwidth(self):
        self.assertEqual(
            h.normalize_title("AI半導体のNVIDIAがツール公開 止まったAI開発を再開"),
            "AI半導体のNVIDIAがツール公開　止まったAI開発を再開",
        )

    def test_spaces_between_latin_words_stay(self):
        self.assertEqual(h.normalize_title("新AI「Gemini 4 Argon」を発表"), "新AI「Gemini 4 Argon」を発表")
        self.assertEqual(h.normalize_title("Mrs. GREEN APPLEが就任"), "Mrs. GREEN APPLEが就任")

    def test_dashes_bars_and_period(self):
        self.assertEqual(h.normalize_title("AI Gatewayで提供開始 – 画像入力対応"), "AI Gatewayで提供開始　画像入力対応")
        self.assertEqual(h.normalize_title("原産地表示違反｜対象商品"), "原産地表示違反　対象商品")
        self.assertEqual(h.normalize_title("共同展開へ。クラウドを一本化。"), "共同展開へ　クラウドを一本化")

    def test_hyphen_inside_names_stays(self):
        self.assertEqual(h.normalize_title("GPT-5とWi-Fi 7の対応"), "GPT-5とWi-Fi 7の対応")
        self.assertEqual(h.normalize_title("東京―大阪間の新路線"), "東京―大阪間の新路線")


class CompanyTest(unittest.TestCase):
    def test_foreign_companies_in_latin(self):
        self.assertEqual(h.normalize_title("マイクロソフトのCopilot Code登場"), "MicrosoftのCopilot Code登場")
        self.assertEqual(h.normalize_title("グーグルとオープンAIが提携"), "GoogleとOpenAIが提携")
        self.assertEqual(h.normalize_title("ＯｐｅｎＡＩのＧＰＴ－６"), "OpenAIのGPT-6")

    def test_words_that_are_not_companies_stay(self):
        self.assertEqual(h.normalize_title("グーグルマップに新機能"), "グーグルマップに新機能")
        self.assertEqual(h.normalize_title("アマゾン川の調査結果"), "アマゾン川の調査結果")
        self.assertEqual(h.normalize_title("パイナップルの値上げ"), "パイナップルの値上げ")

    def test_japanese_companies_unchanged(self):
        self.assertEqual(h.normalize_title("ソフトバンクとトヨタ自動車が提携"), "ソフトバンクとトヨタ自動車が提携")


class EraTest(unittest.TestCase):
    def test_reiwa_becomes_seireki(self):
        self.assertEqual(h.normalize_title("防災庁が令和8年11月2日に発足"), "防災庁が2026年11月2日に発足")
        self.assertEqual(h.normalize_title("令和元年度の決算"), "2019年度の決算")

    def test_reiwa_kept_when_source_title_uses_it(self):
        record = {"videoTitle": "令和8年度消防防災科学技術研究推進制度の採択"}
        self.assertEqual(h.normalize_title("消防庁、令和8年度の研究課題を採択", record), "消防庁、令和8年度の研究課題を採択")

    def test_era_names_that_are_not_years_stay(self):
        self.assertEqual(h.normalize_title("令和の虎が話題に"), "令和の虎が話題に")

    def test_body_gets_seireki_once(self):
        body = "令和8年度の予算です。令和8年度も続きます。令和8年11月に開始。"
        self.assertEqual(h.add_seireki_in_body(body), "令和8年度（2026年度）の予算です。令和8年度も続きます。令和8年（2026年）11月に開始。")
        self.assertEqual(h.add_seireki_in_body("令和8年（2026年）に開始"), "令和8年（2026年）に開始")


class ApplyTest(unittest.TestCase):
    def test_apply_changes_title_seo_and_body_but_not_facts(self):
        record = {
            "title": "マイクロソフトが『Copilot』に新機能 令和8年から提供",
            "seoTitle": "Copilotの新機能｜使い方",
            "body": "## 概要\n令和8年から提供されます。料金は月30ドルです。",
            "videoTitle": "Introducing new Copilot features",
        }
        changes = h.apply(record)
        self.assertEqual(record["title"], "Microsoftが「Copilot」に新機能　2026年から提供")
        self.assertEqual(record["seoTitle"], "Copilotの新機能　使い方")
        self.assertIn("令和8年（2026年）から提供", record["body"])
        self.assertIn("月30ドル", record["body"])
        self.assertTrue(changes)

    def test_idempotent(self):
        title = "Googleの生成AI「Gemini」に新機能　文書やPDFをチャット内で作成・編集"
        self.assertEqual(h.normalize_title(h.normalize_title(title)), title)


class WarningTest(unittest.TestCase):
    def test_long_title_warns_but_short_does_not(self):
        long_title = "カスタマーサポート大手のZendeskがChatGPT Enterpriseを全社導入 社内で600のAIアプリを自作"
        self.assertTrue(quality.title_warnings({"title": long_title}))
        self.assertEqual(quality.title_warnings({"title": "OpenAIがZendesk事例を公開"}), [])


if __name__ == "__main__":
    unittest.main()
