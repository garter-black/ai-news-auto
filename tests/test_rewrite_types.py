"""scripts/rewrite_types.py: 今ある記事の書き直しで、変えてはいけないものを守る（通信しない）"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import rewrite_types as rt  # noqa: E402


class BudgetTest(unittest.TestCase):
    """記事づくりと同じ月の予算の中で、今日使ってよい額の分だけ書き直す（2026-11-03 コーディネーター: 予算は 11/4〜12/3 が 90 ドル）"""

    def summary(self, spent, today, days):
        return {"budget": 90.0, "spent": spent, "spent_today": today, "days_left": days}

    def test_allowance_follows_the_daily_share(self):
        from unittest.mock import patch  # noqa: PLC0415

        with patch.object(rt.llm, "api_ledger_summary", return_value=self.summary(30.0, 0.5, 30)), \
                patch.object(rt.llm, "api_block_reason", return_value=None):
            n, why = rt.budget_allowance(10)
        self.assertEqual(n, int((60 / 30 - 0.5) // rt.EST_COST))  # 1日 2ドル − 今日 0.5ドル = 1.5ドル → 6本
        with patch.object(rt.llm, "api_ledger_summary", return_value=self.summary(30.0, 2.5, 30)), \
                patch.object(rt.llm, "api_block_reason", return_value=None):
            self.assertEqual(rt.budget_allowance(10)[0], 0)  # 今日の分を使い切っていれば書かない
        with patch.object(rt.llm, "api_ledger_summary", return_value=self.summary(0.0, 0.0, 30)), \
                patch.object(rt.llm, "api_block_reason", return_value="今月の予算に到達"):
            self.assertEqual(rt.budget_allowance(10)[0], 0)

    def test_workflow_caps_at_ten_and_keeps_shared_caps(self):
        wf = (rt.ROOT / ".github" / "workflows" / "types_rewrite.yml").read_text("utf-8")
        self.assertIn("inputs.max || '10'", wf)
        self.assertNotIn("CLAUDE_API_DAILY_CALLS", wf)  # 本番と同じ1日の安全上限を使う
        self.assertIn("scripts/deploy_tests.py", wf)
        self.assertIn("--body-images", wf)


class KeepTest(unittest.TestCase):
    def test_correction_note_is_kept_verbatim_at_top(self):
        old = "**編集部訂正（2026年10月31日）**：掲載時は日本での提供を確認できていませんでした。\n\n## 概要\nx"
        body = rt.keep_sections(old, "新しい本文です。")
        self.assertTrue(body.startswith("**編集部訂正（2026年10月31日）**：掲載時は日本での提供を確認できていませんでした。"))

    def test_corrected_articles_are_not_rewritten(self):
        base = {"slug": "x", "sourceType": "web", "sourceUrl": "https://example.com/a", "body": "a" * 2000, "genreSlug": "ai",
                "title": "OpenAIが新しいAIモデルを公開", "verdict": {"status": "available"}}
        self.assertFalse(rt.eligible({**base, "body": "**編集部訂正（2026年10月31日）**：…" + "a" * 2000}, set()))
        slug = next(iter(rt.corrected_slugs()))
        self.assertFalse(rt.eligible({**base, "slug": slug}, set()))

    def test_japan_verdict_is_not_rejudged(self):
        old = {"verdict": {"japan": "ok"}}
        new = {"verdict": {"japan": "unknown", "status": "available"}}
        rt.keep_verified(old, new)
        self.assertEqual(new["verdict"]["japan"], "ok")
        table = {"verdict": {"japan": "partial", "japanSource": {"name": "Claude"}}}
        rt.keep_verified(old, table)
        self.assertEqual(table["verdict"]["japan"], "partial")  # 日本での提供の表が決めた値はそのまま

    def test_updates_and_rival_table_survive(self):
        old = ("## 概要\nx\n\n## 続報（11月3日）：料金が決まった\n月20ドル。\n\n出典: [A](https://a)\n\n"
               "## 何が発表された？\ny\n\n## 同じクラスの製品と比べると？\n| 比べること | A | B |\n|---|---|---|\n| 料金 | 1 | 2 |\n")
        new = "本文です。\n\n## 日本で使えるのは12月から\nz\n\n## まだ分からないこと\n- 価格はまだ発表されていません。"
        body = rt.keep_sections(old, new)
        self.assertTrue(body.startswith("## 続報（11月3日）：料金が決まった"))
        self.assertIn("## 同じクラスの製品と比べると？", body)
        self.assertLess(body.index("同じクラスの製品"), body.index("## まだ分からないこと"))
        self.assertNotIn("## 概要", body)

    def test_only_old_style_official_articles(self):
        base = {"slug": "x", "sourceType": "web", "sourceUrl": "https://example.com/a", "body": "a" * 2000, "genreSlug": "ai",
                "title": "OpenAIが新しいAIモデルを公開", "verdict": {"status": "available"}}
        self.assertTrue(rt.eligible(dict(base), set()))
        self.assertFalse(rt.eligible({**base, "newsType": "product"}, set()))      # もう新しい型
        self.assertFalse(rt.eligible({**base, "typesRewrittenAt": "x"}, set()))
        self.assertFalse(rt.eligible({**base, "type": "roundup"}, set()))
        self.assertFalse(rt.eligible(dict(base), {"x"}))                           # 取り下げた記事
        self.assertFalse(rt.eligible({**base, "sourceType": "video"}, set()))

    def test_title_and_dates_are_not_replaced(self):
        for key in ("slug", "title", "seoTitle", "publishedAt", "generatedAt", "updates", "updatedAt", "thumbnail", "threadsPost"):
            self.assertNotIn(key, rt.REPLACE)


if __name__ == "__main__":
    unittest.main()
