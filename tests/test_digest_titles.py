"""朝のまとめ・週のまとめの題名も題名の門番を通す（2026-11-03 オーナー決定・台帳 A25）。外部通信なし。

- 説明つきの社名（「ChatGPTのOpenAIが」）を外す規則は Python（scripts/headline_normalize.py）とサイト（lib/headline.ts）で同じ
  （同じ見本 tests/fixtures/headline_gate_cases.json を tests/test_digest_titles.cjs も使う）
- 週のまとめの題名は記事の見出し（h1）。検索向けの「〇〇とは？日本で使える？料金も」を並べない
- 週のまとめの会社は発信元の名前を先に見る（「Apple Creator Studio」が Google になっていた）
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import digest_page  # noqa: E402
import headline_normalize as hn  # noqa: E402
import weekly  # noqa: E402

CASES = json.loads((ROOT / "tests" / "fixtures" / "headline_gate_cases.json").read_text("utf-8"))
SEO_QUESTION = re.compile(r"とは[？?]|日本で(?:も)?使える[？?]|の買い方|使い方と料金|料金も$|料金と提供状況")


def article(title: str, **extra) -> dict:
    return {"slug": "x", "title": title, "channel": "", "sourceName": "", "tags": [], **extra}


class DescriptorParityTest(unittest.TestCase):
    def test_same_cases_as_site(self):
        for before, after in CASES:
            self.assertEqual(hn.strip_known_descriptors(before), after, before)

    def test_company_list_matches_site(self):
        src = (ROOT / "lib" / "headline.ts").read_text("utf-8")
        m = re.search(r"WELL_KNOWN_COMPANIES: readonly string\[\] = \[(.*?)\];", src, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(set(re.findall(r'"([^"]+)"', m.group(1))), set(hn.WELL_KNOWN_COMPANIES))


class WeeklyTitleTest(unittest.TestCase):
    def test_uses_news_headline_not_seo_question(self):
        a = article("Mistral AI、「Mistral Large 4」の重みを公開", seoTitle="Mistral Large 4とは？日本で使える？料金も")
        self.assertEqual(weekly.item_title(a), "Mistral AIが「Mistral Large 4」の重みを公開")
        self.assertEqual(weekly.item_title(article("ChatGPTのOpenAIが新機能")), "OpenAIが新機能")

    def test_company_prefers_source_name(self):
        a = article("Appleが「Creator Studio」を大型更新", sourceName="Apple Newsroom（日本）", tags=["Pixelmator Pro", "Final Cut Pro"])
        self.assertEqual(weekly.company_of(a), "Apple")
        self.assertEqual(weekly.company_of(article("Pixelmator Proに新機能", tags=["Pixelmator Pro"])), "")

    def test_real_articles_pass_the_gate(self):
        for path in sorted((ROOT / "content" / "articles").glob("*.json"))[:400]:
            data = json.loads(path.read_text("utf-8"))
            title = weekly.item_title(data)
            self.assertFalse(SEO_QUESTION.search(title), title)
            self.assertFalse(hn._descriptor_matches(title), title)  # noqa: SLF001


class DigestTitleTest(unittest.TestCase):
    def test_short_headline_strips_descriptors(self):
        self.assertEqual(digest_page.short_headline(article("ChatGPTのOpenAIが電力対策「Grid Protectors」")), "OpenAIが電力対策「Grid Protectors」")

    def test_real_articles_pass_the_gate(self):
        for path in sorted((ROOT / "content" / "articles").glob("*.json"))[:400]:
            data = json.loads(path.read_text("utf-8"))
            got = digest_page.short_headline(data)
            self.assertFalse(hn._descriptor_matches(got), got)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
