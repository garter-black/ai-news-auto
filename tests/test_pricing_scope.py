"""料金表は、料金が話題か直接関係する記事だけ（scripts/pricing_scope.py。台帳 A32。2026-11-03: 新薬開発の記事に Claude の料金表）"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import article_charts  # noqa: E402
import owner_rules_audit as audit  # noqa: E402
import pricing_scope as ps  # noqa: E402

TABLE = "### Claudeのプランと料金（公式の料金と円の目安）\n\n| プラン | 月額 |\n|---|---|\n| Pro | 20ドル |\n\n出典：Anthropic公式\n"
DRUG = {"slug": "claude-drug", "newsType": "research", "title": "Anthropic、新薬開発でClaudeの初期成果を公開　6組織と共同研究",
        "lede": "Anthropicは10月28日、創薬の共同研究の初期成果を公開しました。", "body": "本文です。\n\n" + TABLE + "\n## まだ分からないこと\n- x",
        "pricingProduct": "claude", "charts": [{"kind": "ladder", "product": "claude"}], "sourceType": "web"}


class ScopeTest(unittest.TestCase):
    def test_which_articles_may_show_price_tables(self):
        self.assertTrue(ps.relevant({"newsType": "pricing", "title": "Claudeの最上位プランがUltraに"}))
        self.assertTrue(ps.relevant({"newsType": "product", "title": "Canva、iPad版Affinityを公開　無料で使える"}))
        self.assertFalse(ps.relevant({"newsType": "product", "title": "GitHub、Copilotに新機能", "lede": "プロジェクト機能が加わりました。"}))
        for kind in ("research", "earnings", "security", "deal", "filing", "people", "event", "explainer"):
            self.assertFalse(ps.relevant({**DRUG, "newsType": kind}), kind)

    def test_strip_removes_table_field_and_ladder_chart(self):
        rec = dict(DRUG)
        self.assertTrue(ps.strip(rec))
        self.assertNotIn("のプランと料金", rec["body"])
        self.assertNotIn("pricingProduct", rec)
        self.assertNotIn("charts", rec)
        self.assertIn("## まだ分からないこと", rec["body"])

    def test_generation_and_audit_use_the_same_rule(self):
        self.assertIsNone(article_charts.ladder_for({**DRUG, "title": "Claudeの料金で新薬開発"}))
        self.assertIn("A32", " ".join(audit.pricing_problems(dict(DRUG))))
        self.assertEqual(audit.pricing_problems({**DRUG, "newsType": "pricing"}), [])
        src = (ROOT / "scripts" / "web_articles.py").read_text("utf-8")
        self.assertIn("pricing_scope.relevant(record)", src)

    def test_site_rule_matches(self):
        ts = (ROOT / "lib" / "price-slot.ts").read_text("utf-8")
        self.assertIn("export function pricingRelevant", ts)
        self.assertIn('article.newsType !== "product"', ts)
        self.assertIn("pricingRelevant(article)", (ROOT / "app" / "articles" / "[slug]" / "page.tsx").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
