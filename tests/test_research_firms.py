"""調査会社・情報会社（東京商工リサーチ・帝国データバンクなど）の記事は「報道」（2026-11-04 オーナー決定 D2・台帳 A37）。通信しない。"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import images  # noqa: E402
import owner_rules_audit  # noqa: E402
import research_firms  # noqa: E402
import web_articles  # noqa: E402


def web(slug: str, url: str, name: str) -> dict:
    return {"sourceType": "web", "channelSlug": slug, "sourceUrl": url, "sourceName": name, "channel": name}


class ResearchFirmTest(unittest.TestCase):
    def test_list_has_tsr_and_tdb(self):
        self.assertEqual(research_firms.firm_name("tsr"), "東京商工リサーチ")
        self.assertEqual(research_firms.firm_name("radar-official", "https://www.tdb.co.jp/report/x"), "帝国データバンク")
        self.assertEqual(research_firms.firm_name("openai-news", "https://openai.com/index/x"), "")

    def test_research_firm_site_is_not_official_source_for_photos(self):
        self.assertIsNone(images.official_source(web("tsr", "https://www.tsr-net.co.jp/data/detail/1.html", "東京商工リサーチ")))
        self.assertIsNone(images.official_source(web("radar-official", "https://www.tdb.co.jp/report/x", "帝国データバンク")))
        images.official_slugs()
        self.assertIsNotNone(images._host_in("www.tsr-net.co.jp", images.MEDIA_HOSTS))
        self.assertEqual(images.official_source(web("nvidia-blog", "https://blogs.nvidia.com/blog/x/", "NVIDIA公式ブログ")), {"wire": ""})

    def test_disclaimer_says_report_not_official(self):
        for base in (
            "本記事は公式発表の文章をもとにAIが要約・解説して自動生成したものであり、特定の企業・金融商品への投資を勧誘するものではありません。",
            "本記事は官公庁などの公式発表の文章をもとにAIが要約・解説して自動生成したものです。",
            "",
        ):
            text = research_firms.disclaimer("東京商工リサーチ", base)
            self.assertIn("報道（東京商工リサーチの調査・取材記事）", text)
            self.assertIsNone(research_firms.OFFICIAL_CLAIM.search(text), text)
        self.assertIn("投資を勧誘するものではありません", research_firms.disclaimer("東京商工リサーチ", "本記事は公式発表の文章をもとにAIが要約・解説して自動生成したものであり、特定の企業・金融商品への投資を勧誘するものではありません。"))

    def test_generation_marks_research_firm_items(self):
        item = {"source_slug": "tsr", "url": "https://www.tsr-net.co.jp/data/detail/1.html", "source_name": "東京商工リサーチ", "title": "t"}
        self.assertEqual(web_articles.research_firm(item), "東京商工リサーチ")
        self.assertIn("調査・取材記事", web_articles.source_header({**item, "published_at": None}))
        self.assertIn("公式発表ではありません", web_articles.RESEARCH_SOURCE_NOTE.format(name="東京商工リサーチ"))

    def test_audit_flags_official_label_on_research_firm_article(self):
        bad = {
            **web("tsr", "https://www.tsr-net.co.jp/data/detail/1.html", "東京商工リサーチ"),
            "slug": "x",
            "disclaimer": "本記事は公式発表の文章をもとにAIが要約・解説して自動生成したものです。",
            "imageCredit": {"kind": "official", "org": "東京商工リサーチ", "sourceUrl": "https://www.tsr-net.co.jp/data/detail/1.html"},
        }
        problems = owner_rules_audit.research_firm_problems(bad)
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(all(p.startswith("[A37]") for p in problems))
        good = {**bad, "disclaimer": research_firms.disclaimer("東京商工リサーチ"), "imageCredit": {"kind": "ai"}}
        self.assertEqual(owner_rules_audit.research_firm_problems(good), [])
        self.assertEqual(owner_rules_audit.research_firm_problems(web("openai-news", "https://openai.com/x", "OpenAI")), [])

    def test_live_articles_follow_the_rule(self):
        """今ある記事にも、調査会社・情報会社の記事の「公式発表」の免責文・公式写真が残っていない"""
        for path in sorted((ROOT / "content" / "articles").glob("*.json")):
            record = json.loads(path.read_text("utf-8"))
            self.assertEqual(owner_rules_audit.research_firm_problems(record), [], path.name)


if __name__ == "__main__":
    unittest.main()
