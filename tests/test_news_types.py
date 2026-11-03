"""scripts/news_types.py: ニュースの種類ごとの型（通信しない）

2026-11-03 オーナー決定（「かなり大事」）: 決まった章立てをやめ、ニュースの種類ごとに記事を組み立てる。材料の無い章は作らない。
報告 out/記事の文章レベル比較_2026-11-03.md: 決算にも脆弱性にも同じ章立て（料金は？／日本で使える？…）を使い、章の2割が「書かれていません」だった。
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import facts_bundle  # noqa: E402
import news_types as nt  # noqa: E402
import source_links  # noqa: E402

# 報告で比べた8組（題名・資料の冒頭・URL・ジャンル）と、ほかの代表的な種類
CASES = [
    ("What is the Ultra plan?", "The Ultra plan costs $200 per month and offers 10x the usage of Pro. Weekly usage limits apply.",
     "https://support.claude.com/en/articles/17728952-what-is-the-ultra-plan", "", "pricing"),
    ("ChatGPTの「Chat」と「Work」を統合、利用枠はCodexと共通に", "12月から、Plus・Pro の利用枠は Codex と共通になります。",
     "https://help.openai.com/en/articles/20001625", "", "pricing"),
    ("Apple、第4四半期の業績を発表", "売上高は1,145億ドルで、前年同期比12パーセント増加しました。1株当たり利益は2.10ドル。",
     "https://www.apple.com/jp/newsroom/2026/11/apple-reports-fourth-quarter-results/", "tech-gadget", "earnings"),
    ("Anthropic, PBC - S-1/A-1", "Amendment No. 1 to Form S-1 Registration Statement. Class A common stock.",
     "https://www.sec.gov/Archives/edgar/data/2130357/000162828026068676/anthropicpbc-sx1ax1.htm", "", "filing"),
    ("注意喚起: BrotliのC言語実装における境界外書き込みの脆弱性（CVE-2026-111497）に関する注意喚起 (公開)",
     "Brotli 1.2.0 より前のバージョンに影響します。CVSS 9.8。修正版へのアップデートを推奨します。",
     "https://www.jpcert.or.jp/at/2026/at260034.html", "tech-gadget", "security"),
    ("クラウン（エステート）を一部改良", "トヨタ自動車は、クラウン（エステート）を一部改良し、11月2日に発売しました。価格は570万円から。",
     "https://global.toyota/jp/newsroom/toyota/44936557.html", "business", "product"),
    ("ランドクルーザー“FJ”に丸目ヘッドランプとアドベンチャーバンパーを外装オプションとして新規設定",
     "ランドクルーザー“FJ”に外装オプションを新規設定し、2027年1月上旬以降に発売します。", "https://global.toyota/jp/newsroom/toyota/44936636.html",
     "business", "product"),
    ("＜EICMA 2026＞ Honda初のハイエンド電動アシストマウンテンバイク「Honda RN0」を世界初公開", "EICMA 2026 で世界初公開しました。",
     "https://global.honda/jp/news/2026/2261103e.html", "business", "product"),
    ("ガバメントAI 源内 利用状況アンケート調査（第1回）の結果を公表しました", "回答者の71.7%が満足と回答。調査期間は10月1日から10月14日。",
     "https://www.digital.go.jp/policies/genai/x", "public", "research"),
    ("Salesforce Signs Definitive Agreement to Acquire Cresta", "Salesforce will acquire Cresta for approximately $3 billion.",
     "https://www.salesforce.com/news/x", "", "deal"),
    ("代表取締役社長の異動に関するお知らせ", "2027年1月1日付で、田中太郎が代表取締役社長に就任します。", "https://example.co.jp/news", "business", "people"),
    ("ＥＣＢ政策理事会－利上げを示唆しつつも今回は利上げ見送り", "政策金利は据え置き。", "https://www.nli-research.co.jp/x", "business", "policy"),
    ("週刊生成AI with AWS – 2026/10/26 週", "今週の主なアップデートをまとめます。", "https://aws.amazon.com/jp/blogs/x", "", "event"),
    ("How MoEngage turned over 400 APIs into agent tools with AgentCore Gateway", "In this post we show how.", "https://aws.amazon.com/blogs/x", "", "explainer"),
    ("【ホットピ！】フットサル日本代表、大分で強豪モロッコと対戦", "11月6日と8日に試合を行います。", "https://www.jfa.jp/x", "sports", "sports"),
    ("石川県で震度5強の地震が発生", "11月3日21時10分ごろ、地震がありました。", "https://example.jp/x", "", "breaking"),
    ("Introducing SFX v3", "Our new sound effects model is available today for all users.", "https://elevenlabs.io/blog/x", "", "product"),
]


class ClassifyTest(unittest.TestCase):
    def test_benchmark_and_common_types(self):
        for title, text, url, genre, want in CASES:
            with self.subTest(title=title[:30]):
                self.assertEqual(nt.classify(title, text, url=url, genre=genre), want)

    def test_words_in_body_alone_do_not_make_a_product_a_vulnerability(self):
        # 本文に「脆弱性」「上限」が少し出るだけの発表は、発表のまま（料金・脆弱性の型にしない）
        title = "GitHub Agentic Workflows generally available"
        text = "Agentic workflows are now generally available. Security reviews and usage limits are documented."
        self.assertEqual(nt.classify(title, text), "product")

    def test_statistics_with_disaster_words_is_not_breaking(self):
        self.assertEqual(nt.classify("鉱工業生産26年9月－熊本地震や台風・豪雨の影響を受けながらも、7-9月期は小幅な増産を確保", "鉱工業生産指数"),
                         "research")

    def test_every_type_has_a_template(self):
        for key in nt.PRIORITY:
            self.assertIn(key, nt.TYPES)


class PromptTest(unittest.TestCase):
    FIXED = ("## 料金は？", "## 日本で使える？", "## 自分に関係ある？", "## ライバルと比べてどう？", "## もう使える？", "## 何がすごいの？",
             "## 何が発表された？", "読者の疑問の形")

    def test_no_fixed_chapters_and_lead_rule(self):
        for key in nt.TYPES:
            block = nt.prompt_block(key)
            with self.subTest(key=key):
                for bad in self.FIXED:
                    self.assertNotIn(bad, block)
                self.assertIn("1文目で「今回なにが新しいか」を言い切る", block)
                self.assertIn("材料の無い章は作らない", block)
                self.assertIn("1,000字につき1つまで", block)
                self.assertIn("「## 概要」の章は作らない", block)
                self.assertIn("まだ分からないこと", block)
                self.assertIn("1,600字以上", block)  # 公開前の門（indexnow の thin）と同じ長さ

    def test_type_specific_must_haves(self):
        self.assertIn("前年", nt.prompt_block("earnings"))
        self.assertIn("見通し", nt.prompt_block("earnings"))
        self.assertIn("CVSS", nt.prompt_block("security"))
        self.assertIn("対象のバージョン", nt.prompt_block("security"))
        self.assertIn("自動更新か", nt.prompt_block("security"))
        self.assertIn("悪用", nt.prompt_block("security"))
        self.assertIn("発売日", nt.prompt_block("product"))
        self.assertIn("日本での提供", nt.prompt_block("product"))
        self.assertIn("後付け", nt.prompt_block("product"))
        self.assertIn("対象者", nt.prompt_block("policy"))
        self.assertIn("手続き", nt.prompt_block("policy"))

    def test_non_verdict_types_need_a_japan_heading(self):
        # 判定カードを出さない種類は、公開前の門（no-japan-value）に合う見出しを必ず1つ書かせる
        for key, t in nt.TYPES.items():
            block = nt.prompt_block(key)
            if not t.verdict:
                self.assertIn("見出しの少なくとも1つは日本の読者に関係する章", block, key)

    def test_easy_rule_has_no_template_questions(self):
        self.assertIn("## ひとことで言うと", nt.EASY_RULE)
        self.assertNotIn("## 何がすごいの？", nt.EASY_RULE)

    def test_clean_field_notes_drops_fixed_chapter_lines(self):
        notes = "- 「## 自分に関係ある？」では、どんな人に影響があるか書く。\n- tags には業界名を含める。\n- 元記事に無ければ「言及なし」と書く。"
        self.assertEqual(nt.clean_field_notes(notes), "- tags には業界名を含める。")


class MissingFactsTest(unittest.TestCase):
    def test_earnings_yoy_and_guidance(self):
        src = "売上高は1,145億ドルで、前年同期比12%増。10〜12月期の売上高は7〜10%の増加を見込む（業績予想）。純利益は300億ドル。"
        missing = {m.key for m in nt.missing_facts("earnings", "売上高は1,145億ドルでした。", src)}
        self.assertEqual(missing, {"yoy", "guidance", "profit"})
        body = "売上高は1,145億ドルで前年同期から12%増えました。純利益は300億ドル。会社は10〜12月期に7〜10%増を見込んでいます。"
        self.assertEqual(nt.missing_facts("earnings", body, src), [])

    def test_security_versions_fix_exploit(self):
        src = "Brotli 1.2.0 より前のバージョンが影響を受けます。CVSS 9.8。修正版にアップデートしてください。悪用は確認されていません。"
        missing = {m.key for m in nt.missing_facts("security", "Brotliに欠陥が見つかりました。", src)}
        self.assertEqual(missing, {"severity", "versions", "fix", "exploit"})

    def test_nothing_required_when_source_lacks_it(self):
        # 資料に無い事実は求めない（推測で埋めさせない）
        self.assertEqual(nt.missing_facts("product", "新しい機能が使えるようになりました。", "新しい機能を公開しました。"), [])

    def test_feedback_names_the_fact(self):
        fb = nt.missing_feedback("security", [m for m in nt.get("security").must if m.key == "versions"])
        self.assertIn("対象のバージョン", fb[0])

    def test_verdict_scope(self):
        for key in ("earnings", "security", "deal", "filing", "research", "people", "event"):
            self.assertFalse(nt.allows_verdict(key), key)
        for key in ("product", "pricing", "policy"):
            self.assertTrue(nt.allows_verdict(key), key)


class ComposeTest(unittest.TestCase):
    def test_compose_uses_the_type_template(self):
        import web_articles as w  # noqa: PLC0415

        item = {"id": "x", "title": "Apple、第4四半期の業績を発表", "url": "https://www.apple.com/jp/newsroom/2026/11/x/",
                "source_name": "Apple Newsroom（日本）", "source_slug": "apple-jp", "published_at": datetime(2026, 11, 2, tzinfo=timezone.utc)}
        with patch.object(facts_bundle, "load_fx", return_value=None):
            out = w.compose_with_facts(item, "売上高は1,145億ドルで、前年同期比12パーセント増加しました。", "tech-gadget", [], "ふつう")
        prompt, system = out[2], out[1]
        self.assertEqual(item["newsType"], "earnings")
        self.assertIn("この記事の種類: 決算・業績", prompt)
        self.assertNotIn("## 自分に関係ある？", prompt)
        self.assertNotIn("## スペックと価格は？", prompt)
        self.assertNotIn("## 何がすごいの？", prompt)
        self.assertIn("サイソクの文体", system)  # 文体の決まり（scripts/article_style.py）がすべての生成に入る

    def test_record_keeps_type_and_drops_verdict_for_earnings(self):
        import web_articles as w  # noqa: PLC0415

        item = {"id": "abcdef12", "title": "Apple、第4四半期の業績を発表", "url": "https://www.apple.com/jp/newsroom/x/", "source_name": "Apple",
                "source_slug": "apple-jp", "published_at": datetime(2026, 11, 2, tzinfo=timezone.utc), "newsType": "earnings"}
        data = {"title": "Apple、7〜9月期の売上高1145億ドル　iPhoneは17%増", "description": "d", "body_markdown": "本文", "lede": "リード",
                "genre": "tech-gadget", "verdict": {"status": "available", "japan": "ok", "price": "無料", "free": True, "audience": "誰でも", "take": "x"}}
        record = w.build_web_record(item, data, {"image": ""}, "m", "explainer")
        self.assertEqual(record["newsType"], "earnings")
        self.assertIsNone(record["verdict"])


class DeepReadingTest(unittest.TestCase):
    def test_security_follows_vendor_release_not_unrelated_pages(self):
        words = source_links._words("注意喚起: BrotliのC言語実装における境界外書き込みの脆弱性（CVE-2026-111497）")
        src = "https://www.jpcert.or.jp/at/2026/at260034.html"
        vendor = {"url": "https://github.com/google/brotli/releases/tag/v1.2.0", "text": "Brotli v1.2.0 release"}
        unrelated = {"url": "https://www.jpcert.or.jp/magazine/security/ransom-faq.html", "text": "侵入型ランサムウェア攻撃を受けたら読むFAQ"}
        blog = {"url": "https://www.aikido.dev/blog/python-packages-vulnerable-brotli-cve-2026-111497", "text": "blog"}
        self.assertGreaterEqual(source_links.score_link(vendor, src, words, False, "security"), source_links.MIN_SCORE)
        self.assertLess(source_links.score_link(unrelated, src, words, False, "security"), source_links.MIN_SCORE)
        self.assertLess(source_links.score_link(blog, src, words, False, "security"), 0)

    def test_earnings_prefers_financial_statements(self):
        words = source_links._words("Apple、第4四半期の業績を発表")
        src = "https://www.apple.com/jp/newsroom/2026/11/apple-reports-fourth-quarter-results/"
        pdf = {"url": "https://www.apple.com/newsroom/pdfs/fy2026q4/FY26_Q4_Consolidated_Financial_Statements.pdf", "text": "財務諸表"}
        other = {"url": "https://www.apple.com/jp/iphone/", "text": "iPhone"}
        picked = source_links.pick([other, pdf], src, "Apple、第4四半期の業績を発表", news_type="earnings")
        self.assertEqual(picked[0]["url"], pdf["url"])
        self.assertGreater(source_links.TYPE_EXCERPT_CAP["earnings"], source_links.EXCERPT_CAP)

    def test_bundle_gives_deep_types_longer_excerpts(self):
        b = facts_bundle.Bundle(news_type="earnings")
        self.assertGreater(b.cap("関連ページ"), facts_bundle.GROUP_CAP["関連ページ"])
        self.assertEqual(facts_bundle.Bundle().cap("関連ページ"), facts_bundle.GROUP_CAP["関連ページ"])

    def test_long_filing_continuation_is_excerpted(self):
        # 上場の届出書は生成に渡す本文（12,000字）が表紙で切れる。後半から業績の数字の行だけを抜き出して束に入れる
        head = "Cover page. " * 1000
        tail = ("Risk factors text. " * 300 + "\nRevenue grew to $17.0 billion for the three months ended September 30, 2026.\n"
                + "Net loss was $(104,039) million for the nine months ended September 30, 2026.\n"
                + "Approximately $69.8 billion of these commitments are due in the 12 months following September 30, 2026.\n"
                + "Results of Operations for the three months ended September 30, 2026 compared to 2025.\n" + "Other text. " * 300)
        page = {"text": head, "full_text": head + tail}
        g = facts_bundle.continuation_group({"title": "Anthropic, PBC - S-1/A-1", "source_name": "SEC", "url": "https://www.sec.gov/x"}, page,
                                            "filing", datetime(2026, 11, 3, tzinfo=timezone.utc))
        self.assertIsNotNone(g)
        self.assertIn("$17.0 billion", g.body)
        self.assertFalse(g.show)  # 元の資料そのものなので出典欄には出さない
        self.assertIsNone(facts_bundle.continuation_group({"title": "x"}, page, "product", datetime(2026, 11, 3, tzinfo=timezone.utc)))

    def test_millions_tables_are_usable_numbers(self):
        import calc_facts  # noqa: PLC0415

        vals = calc_facts.values("Net sales by reportable segment (In millions) Japan $ 6,540 $ 6,636")
        self.assertIn(6.54e9, vals)
        self.assertNotIn(6.54e9, calc_facts.values("Japan 6,540 units"))


if __name__ == "__main__":
    unittest.main()
