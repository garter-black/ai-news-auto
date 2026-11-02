"""同じ発表の判定（scripts/story.py）と、ニュース用サイトマップの基準（scripts/indexnow.py news_reason）。ネットワークは使わない。"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import indexnow  # noqa: E402
import story  # noqa: E402

BODY = "## 日本で使える？\n" + "本文" * 900


def art(slug, title, gen, **kw):
    base = {"slug": slug, "title": title, "generatedAt": gen, "publishedAt": kw.pop("pub", gen), "genreSlug": "ai",
            "keywords": [], "body": BODY, "verdict": {"japan": "ok"}, "channelSlug": kw.pop("ch", "c")}
    base.update(kw)
    return base


def same(a, b, df=None):
    sa, sb = story.Story(a), story.Story(b)
    return story.same_story(sa, sb, df if df is not None else story.doc_freq([sa, sb]))


class UrlKeyTest(unittest.TestCase):
    def test_normalizes_query_slash_locale_and_redirects(self):
        a = story.url_keys("https://www.openai.com/blog/introducing-x/?utm_source=t")
        b = story.url_keys("https://openai.com/index/introducing-x")
        self.assertTrue(a & b)
        self.assertTrue(story.url_keys("https://blog.google/intl/ja-jp/products/gemini-x/") & story.url_keys("https://blog.google/products/gemini-x"))

    def test_youtube_and_entry_fragment(self):
        self.assertEqual(story.url_keys("https://www.youtube.com/watch?v=abcdefghijk&t=3"), {"yt:abcdefghijk"})
        a = story.url_keys("https://support.claude.com/en/articles/1-release-notes#entry-2026-10-30")
        b = story.url_keys("https://support.claude.com/en/articles/1-release-notes#entry-2026-10-31")
        self.assertFalse(a & b)


class SameStoryTest(unittest.TestCase):
    def test_product_name_with_company(self):
        a = art("a", "Googleが「世界モデル」の新版Genie 4を発表", "2026-10-28T01:00:00+00:00", videoTitle="Introducing Genie 4",
                keywords=["Genie 4", "Google DeepMind"])
        b = art("b", "Google DeepMindが新AI『Genie 4』発表 映像と音声が連動", "2026-11-01T18:00:00+00:00", pub="2026-10-27T16:00:00+00:00",
                keywords=["Genie 4", "世界モデル"])
        self.assertTrue(same(a, b))

    def test_variant_names_are_different_products(self):
        a = art("a", "OpenAIが新AI「GPT-6 Astra」を発表", "2026-10-20T00:00:00+00:00", videoTitle="Introducing GPT-6 Astra")
        b = art("b", "OpenAIが新AI「GPT-6 Sol」を公開", "2026-10-21T00:00:00+00:00", videoTitle="Introducing GPT-6 Sol")
        self.assertIsNone(same(a, b))

    def test_same_channel_case_study_split_into_videos(self):
        a = art("a", "OpenAIが紹介する法律AI『Legora』とは？", "2026-10-28T12:00:00+00:00", pub="2026-10-28T09:45:18+00:00", ch="openai",
                keywords=["Legora", "OpenAI"])
        b = art("b", "OpenAIがAI法務 Legora 事例を公開 契約書レビューも自動化", "2026-10-29T12:00:00+00:00", pub="2026-10-28T09:45:14+00:00",
                ch="openai", keywords=["Legora", "OpenAI", "ChatGPT"])
        self.assertIn("同じ発信元", same(a, b))

    def test_publisher_name_is_not_a_topic(self):
        a = art("a", "データ基盤『Databricks』がSAPと提携", "2026-11-01T21:00:00+00:00", ch="databricks-blog", channel="Databricks公式ブログ")
        b = art("b", "富裕層向け資産管理のAI導入はなぜ止まる？『Databricks』が提唱", "2026-11-02T00:00:00+00:00", ch="databricks-blog",
                channel="Databricks公式ブログ")
        self.assertIsNone(same(a, b))

    def test_same_launch_day_different_products(self):
        a = art("a", "GoogleがPixel Watch 5を発表、オフラインGeminiと呼吸緊急検出を搭載", "2026-10-20T18:00:00+00:00", ch="google-blog",
                keywords=["Pixel Watch 5", "Gemini", "ヘルスケア"])
        b = art("b", "Googleの紛失防止タグ「Pixel Tag」が11月11日発売。薄型で電池交換も簡単", "2026-10-20T18:05:00+00:00", ch="google-blog",
                keywords=["Pixel Tag", "紛失防止", "発売日"])
        self.assertIsNone(same(a, b))

    def test_reupload_with_same_source_title(self):
        a = art("a", "ZendeskがChatGPT Enterpriseを全社導入", "2026-10-30T12:00:00+00:00", videoTitle="ChatGPT Enterprise at Zendesk: Putting Company Data to Work")
        b = art("b", "カスタマーサポート大手Zendeskが社内で600のAIアプリ", "2026-11-01T00:00:00+00:00",
                videoTitle="ChatGPT Enterprise at Zendesk: Putting Company Data to Work")
        self.assertEqual(same(a, b), "元の題名が同じ")

    def test_similar_template_titles_of_different_products_are_kept(self):
        a = art("a", "AppleがM6チップ搭載の新iMacを発表、最高18倍の高速化", "2026-10-27T00:00:00+00:00", genreSlug="tech-gadget")
        b = art("b", "Appleが「M6」搭載の新14インチMacBook Proを発表、最大24時間駆動", "2026-10-27T00:10:00+00:00", genreSlug="tech-gadget")
        self.assertIsNone(same(a, b))

    def test_window_and_genre(self):
        a = art("a", "Googleが新版Genie 4を発表", "2026-10-01T00:00:00+00:00")
        b = art("b", "Google DeepMindが新AI『Genie 4』発表", "2026-10-20T00:00:00+00:00")
        self.assertIsNone(same(a, b))
        c = art("c", "リベ大が語る「Genie 4」と新NISA", "2026-10-02T00:00:00+00:00", genreSlug="investment")
        self.assertIsNone(same(a, c))

    def test_angle_article_is_not_duplicate(self):
        base = art("base", "OpenAIが新AIモデル「GPT-6.1 Terra」", "2026-10-27T01:00:00+00:00", sourceUrl="https://openai.com/index/t")
        angle = art("angle", "ChatGPTの「GPT-6.1 Terra」は誰が使える？プラン別に整理", "2026-10-27T02:00:00+00:00", sourceUrl="https://openai.com/index/t/",
                    relatedSlugs=["base"], body=BODY + "\n[元記事](/articles/base)")
        dup = art("dup", "OpenAIの新モデル GPT-6.1 Terra が登場", "2026-10-27T03:00:00+00:00", sourceUrl="https://openai.com/index/t")
        stories = [story.Story(x) for x in (base, angle, dup)]
        result = story.plan(stories, story.doc_freq(stories))
        self.assertEqual(result, {"dup": ("base", "同じ出典URL")})
        explicit = art("x", "GPT-6.1 Terra のExcelデモは何ができる？", "2026-10-27T04:00:00+00:00", sourceUrl="https://openai.com/index/t", angleOf="base")
        stories.append(story.Story(explicit))
        self.assertNotIn("x", story.plan(stories, story.doc_freq(stories)))

    def test_keeper_is_earliest_indexable_and_manual_is_respected(self):
        thin = art("thin", "Googleが新版Genie 4を発表", "2026-10-28T00:00:00+00:00", body="短い", sourceUrl="https://blog.google/g4")
        full = art("full", "Google DeepMindが新AI『Genie 4』発表", "2026-10-28T02:00:00+00:00", sourceUrl="https://blog.google/g4/")
        manual = art("manual", "Genie 4 の解説", "2026-10-28T03:00:00+00:00", sourceUrl="https://blog.google/g4", indexable=True)
        stories = [story.Story(x) for x in (thin, full, manual)]
        result = story.plan(stories, story.doc_freq(stories))
        self.assertEqual(result["full"][0], "manual")
        self.assertEqual(result["thin"][0], "manual")
        result = story.plan(stories[:2], story.doc_freq(stories[:2]))
        self.assertEqual(result, {"thin": ("full", "同じ出典URL")})


class NewsSitemapTest(unittest.TestCase):
    NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)

    def reason(self, **kw):
        a = art("a", "Google Gemini の新機能", kw.pop("gen", "2026-11-02T06:00:00+00:00"), **kw)
        return indexnow.news_reason(a, set(), self.NOW)

    def test_rules(self):
        self.assertIsNone(self.reason(pub="2026-11-02T05:00:00+00:00"))
        self.assertEqual(self.reason(pub="2026-10-27T16:00:00+00:00"), "stale-source")
        self.assertEqual(self.reason(sourcePublishedAt="2026-10-29T00:00:00+00:00"), "stale-source")
        self.assertEqual(self.reason(gen="2026-10-30T00:00:00+00:00"), "old")
        self.assertEqual(self.reason(duplicateOf="b"), "duplicate")
        self.assertEqual(self.reason(angleOf="b"), "angle")
        self.assertEqual(self.reason(late=True), "late")
        self.assertEqual(self.reason(type="roundup"), "roundup")

    def test_duplicate_slugs_reads_duplicate_of(self):
        arts = [art("a", "x", "2026-11-01T00:00:00+00:00"), art("b", "y", "2026-11-01T01:00:00+00:00", duplicateOf="a")]
        self.assertEqual(indexnow.duplicate_slugs(arts), {"b"})


if __name__ == "__main__":
    unittest.main()
