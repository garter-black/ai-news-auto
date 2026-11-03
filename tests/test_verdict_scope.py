"""「使える／提供前」の判定を付ける範囲（2026-11-03 の画面の点検 A）。外部 API は呼ばない。

製品・サービスの発表でないニュース（流出・買収・表彰・統計など）には判定を付けない。
記事を作る側（scripts/web_articles.py の NON_PRODUCT_RE）とサイト（lib/verdict.ts の NON_PRODUCT_PATTERN）が同じ文字列であること。
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import web_articles as w  # noqa: E402


def site_pattern() -> str:
    src = (ROOT / "lib" / "verdict.ts").read_text(encoding="utf-8")
    m = re.search(r'export const NON_PRODUCT_PATTERN =\n((?:\s*".*?"\s*\+?)+);', src)
    assert m, "lib/verdict.ts に NON_PRODUCT_PATTERN が無い"
    return "".join(re.findall(r'"(.*?)"', m.group(1)))


RAW = {"status": "available", "japan": "ok", "price": "無料", "free": True, "audience": "利用者", "take": "案内を確かめる"}


class VerdictScopeTest(unittest.TestCase):
    def test_same_pattern_as_site(self):
        self.assertEqual(w.NON_PRODUCT_RE.pattern, site_pattern())

    def test_non_product_news_has_no_verdict(self):
        for title, tags in [
            ("北陸電力の「ほくリンク」会員約55万件のメールアドレスが流出、委託先のメール配信サービスで不正アクセス", ["セキュリティ", "インシデント"]),
            ("Salesforceが音声AIのCresta買収へ　顧客対応コールセンターを自動化", ["Salesforce"]),
            ("タスク管理のアトラシアン、マーケティング業務管理部門で3年連続「リーダー」に選出", ["SaaS"]),
            ("ClaudeのAnthropicが上場申請書を修正　株は「ANT」で売買予定", ["Anthropic"]),
            ("楽天証券の総合口座が1,500万を突破、国内単体で最多に", ["証券"]),
        ]:
            record = {"title": title, "tags": tags}
            self.assertIsNone(w.genre_verdict(dict(RAW), "ai", record), title)

    def test_product_news_keeps_verdict(self):
        for title in [
            "対話AI「Claude」の最上位プランが「Ultra」に　月200ドルで利用量はProの10倍",
            "GitHub Copilotにセキュリティ拡張が登場　リポジトリの脆弱性修正がアプリ内で完結",
            "Microsoft Foundryのガードレール機能解説：AIエージェントの脱獄や個人情報漏洩を防ぐ安全対策",
            "オムロンとNTTドコモビジネスが提携、工場の製造現場をサイバー攻撃から守る新サービス",
        ]:
            record = {"title": title, "tags": ["生成AI"]}
            self.assertIsNotNone(w.genre_verdict(dict(RAW), "ai", record), title)

    def test_sports_is_not_filtered(self):
        record = {"title": "なでしこジャパン、W杯予選のメンバーに23人を選出", "tags": ["サッカー"]}
        verdict = w.genre_verdict({**RAW, "status": "soon"}, "sports", record)
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict["kind"], "sports")

    def test_prompt_asks_for_null(self):
        self.assertIn("verdict は **null**", w.extra_fields_block("ai"))
        self.assertNotIn("製品・サービスの発表でない話題", w.extra_fields_block("sports"))


if __name__ == "__main__":
    unittest.main()
