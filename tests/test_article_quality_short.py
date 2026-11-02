"""短い速報を字数で落とさず、事実・有用性の判定を迂回しないことを検証。外部 API は呼ばない。"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import quality
import web_articles


def article():
    return {
        "sourceType": "web",
        "title": "新しい機能を日本から使えるか公式発表を確認",
        "lede": "新機能の提供対象を公式発表から整理します。",
        "highlights": ["日本の利用者も対象です"],
        "body": "## 何が変わる？\n公式発表によると、日本でも新機能を使えます。\n\n## 料金は？\n料金は発表されていません。",
        "easy": "## ひとことで言うと\n新機能は日本でも使えます。料金はまだ不明です。",
        "workMoney": {"relevance": "medium", "points": ["日本の利用者は今日から試せます。料金は発表を待ってから判断しましょう。"]},
    }


class ShortNewsGateTests(unittest.TestCase):
    def result(self, **kwargs):
        return {"grounded": True, "score": 90, "problems": [], "fatal": False, "useful": True, **kwargs}

    def check(self, response):
        with patch.object(quality.llm, "generate_json", return_value=(response, "mock")) as call:
            verdict = quality.evaluate(article(), "日本で新機能を提供する公式発表", "ai")
        self.assertEqual(call.call_count, 1)
        self.assertIn("useful", call.call_args.args[2]["required"])
        return verdict

    def test_under_2000_and_short_easy_accepted_when_useful(self):
        self.assertLess(len(article()["body"]), 2000)
        self.assertLess(len(article()["easy"]), 400)
        self.assertTrue(self.check(self.result())["ok"])

    def test_unhelpful_cannot_bypass_via_major_surgical_path(self):
        verdict = self.check(self.result(useful=False))
        self.assertFalse(verdict["ok"])
        self.assertFalse(verdict["soft"])
        self.assertFalse(quality.surgical_ok(verdict))

    def test_missing_usefulness_fails_closed(self):
        data = self.result()
        del data["useful"]
        self.assertFalse(self.check(data)["ok"])
        self.assertFalse(self.check(self.result(useful="true"))["ok"])

    def test_invented_fact_and_checker_error_fail_closed(self):
        self.assertFalse(self.check(self.result(grounded=False, fatal=True))["ok"])
        with patch.object(quality.llm, "generate_json", side_effect=RuntimeError("unavailable")):
            verdict = quality.evaluate(article(), "公式発表", "ai")
        self.assertTrue(verdict["unavailable"])
        self.assertFalse(verdict["ok"])

    def test_empty_text_is_rejected_before_api(self):
        for changes in ({"body": "## 見出し\n"}, {"easy": "## ひとことで言うと\n"}):
            with self.subTest(changes=changes), patch.object(quality.llm, "generate_json") as call:
                self.assertFalse(quality.evaluate({**article(), **changes}, "公式発表", "ai")["ok"])
                call.assert_not_called()

    def test_existing_fact_check_callers_keep_old_schema(self):
        with patch.object(quality.llm, "generate_json", return_value=(self.result(), "mock")) as call:
            result = quality.fact_check(article(), "公式発表")
        self.assertNotIn("useful", result)
        self.assertNotIn("useful", call.call_args.args[2]["required"])

    def test_builder_accepts_short_body_but_still_rejects_empty(self):
        item = {"id": "test1234", "url": "https://example.test/news", "source_name": "公式",
                "title": "新機能の発表", "source_slug": "example", "published_at": datetime.now(timezone.utc)}
        data = {"title": article()["title"], "description": "公式発表を確認", "body_markdown": article()["body"],
                "easy_markdown": article()["easy"], "slug_en": "test-news"}
        with patch.object(web_articles.m, "unique_slug", return_value="test-news"), \
             patch.object(web_articles.m, "match_affiliates", return_value=[]):
            record = web_articles.build_web_record(item, data, {}, "mock", "test")
            self.assertLess(len(record["body"]), 800)
            with self.assertRaises(ValueError):
                web_articles.build_web_record(item, {**data, "body_markdown": ""}, {}, "mock", "test")


if __name__ == "__main__":
    unittest.main()
