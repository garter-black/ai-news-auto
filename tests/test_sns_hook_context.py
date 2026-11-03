"""書き出しの差し替えで数字・サービス名を消さない。ネット接続・投稿なし。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import threads_post as tp


class HookContextTest(unittest.TestCase):
    def setUp(self):
        self.article = {
            "title": "Acmeの未公開AIがブラウザーの欠陥を発見",
            "threadsStyle": 2,
            "threadsHook": "number",
            "threadsPost": "62件。\nAcmeのAIがブラウザーで見つけた欠陥の数です。\n一般には使えません。",
        }

    def breaking(self, article, speed="【速報】発表から16分で掲載"):
        with patch.object(tp, "speed_line", return_value=speed):
            result = tp.compose_hook(article, breaking=True)
        self.assertLessEqual(tp.x_weight(result), tp.X_LIMIT)
        return result

    def test_speed_header_is_never_used(self):
        """2026-11-03 オーナー承認その2: 「発表からN分で掲載／お届け」の書き出しは使わない（sns_rules のルール6）"""
        result = self.breaking(self.article)
        self.assertNotIn("発表から", result)
        self.assertIn(self.article["title"], result)
        self.assertEqual(result.count("【速報】"), 1)

    def test_long_context_falls_back_to_complete_title(self):
        self.article["threadsPost"] = "Acmeの文書アプリの発表。\n" + "非常に長い紹介文です。" * 20
        result = self.breaking(self.article)
        self.assertIn(self.article["title"], result)
        self.assertNotIn("非常に長い", result)

    def test_no_speed_uses_complete_title_not_orphaned_second_line(self):
        result = self.breaking(self.article, speed="")
        self.assertIn(self.article["title"], result)
        self.assertNotIn("欠陥の数です", result)

    def test_normal_original_number_and_context_stay_together(self):
        with patch.object(tp, "weak_hook_keys", return_value=[]):
            result = tp.compose_hook(self.article)
        self.assertTrue(result.startswith("62件。\nAcmeのAI"))

    def test_normal_replaced_number_uses_standalone_title(self):
        with patch.object(tp, "weak_hook_keys", return_value=["number"]), \
             patch.object(tp, "opening_line", return_value=("conclusion", "まだ一般公開前です。")):
            result = tp.compose_hook(self.article)
        self.assertIn(self.article["title"], result)
        self.assertNotIn("欠陥の数です", result)

    def test_overlapping_generated_opening_does_not_remove_full_title(self):
        self.article["title"] = "Acmeの未公開AIによるブラウザー検査で欠陥を発見"
        with patch.object(tp, "weak_hook_keys", return_value=["number"]), \
             patch.object(tp, "opening_line", return_value=("question", "Acmeの未公開AIによるブラウザー検査とは？")):
            result = tp.compose_hook(self.article)
        self.assertIn(self.article["title"], result)
        self.assertNotIn("検査とは？", result)


if __name__ == "__main__":
    unittest.main()
