"""検索に出せない記事（主題外・日本の読者に役立つ情報がない・薄い）を公開しない門と、noindex の記事の取り下げ計画の確認（2026-11-03）。
実コンテンツ・ネットワーク・LLM は使わない。"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import indexnow  # noqa: E402
import retire_articles  # noqa: E402

NOW = datetime(2026, 11, 3, 10, tzinfo=timezone.utc)
JAPAN_BODY = "## 概要\n" + "あ" * 1600 + "\n## 日本で使える？\n使えます。"


def article(slug: str, **extra) -> dict:
    base = {
        "slug": slug, "title": "OpenAIが新しいAIモデルを発表", "genreSlug": "ai", "channelSlug": "openai-news",
        "body": JAPAN_BODY, "sourceUrl": f"https://example.test/{slug}", "generatedAt": "2026-11-01T00:00:00+00:00",
    }
    base.update(extra)
    return base


class PublishGate(unittest.TestCase):
    def test_on_topic_article_with_japan_value_passes(self):
        self.assertIsNone(indexnow.publish_block_reason(article("ok")))

    def test_off_topic_article_is_blocked_without_retry(self):
        a = article("beer", title="キリンビール、冬限定の缶チューハイを新発売", genreSlug="lifestyle", channelSlug="kirin")
        self.assertEqual(indexnow.publish_block_reason(a), "off-focus")
        self.assertNotIn("off-focus", indexnow.PUBLISH_RETRYABLE)

    def test_no_japan_value_is_blocked_and_retryable(self):
        a = article("invest", title="楽天証券のつなぎ売りの解説", genreSlug="investment", channelSlug="rakuten-sec", body="## 概要\n" + "い" * 1600)
        self.assertEqual(indexnow.publish_block_reason(a), "no-japan-value")
        self.assertIn("no-japan-value", indexnow.PUBLISH_RETRYABLE)
        self.assertIn("日本", indexnow.PUBLISH_FEEDBACK["no-japan-value"])

    def test_thin_is_blocked_and_retryable(self):
        a = article("short", body="## 概要\n短い")
        self.assertEqual(indexnow.publish_block_reason(a), "thin")
        self.assertIn("thin", indexnow.PUBLISH_RETRYABLE)

    def test_disaster_roundup_and_manual_are_not_blocked(self):
        self.assertIsNone(indexnow.publish_block_reason(article("quake", body="短い", disaster=True, genreSlug="public")))
        self.assertIsNone(indexnow.publish_block_reason(article("matome", body="短い", type="roundup")))
        self.assertIsNone(indexnow.publish_block_reason(article("manual", body="短い", indexable=True)))

    def test_duplicates_are_left_to_story(self):
        self.assertIsNone(indexnow.publish_block_reason(article("dup", duplicateOf="other")))

    def test_gate_matches_site_noindex(self):
        """公開前の門で止める理由は、サイトが noindex にする理由と同じ判定から出る"""
        for a in (
            article("g1", title="任天堂、新作ゲームの体験版を配信", genreSlug="entertainment", channelSlug="nintendo-topics"),
            article("g2", body="短い"),
        ):
            self.assertEqual(indexnow.publish_block_reason(a), indexnow.noindex_reason(a))


class CandidateOffFocus(unittest.TestCase):
    def test_off_topic_lanes_are_skipped_before_generation(self):
        self.assertTrue(indexnow.candidate_off_focus("entertainment", "PS5の新作ゲームが発売"))
        self.assertTrue(indexnow.candidate_off_focus("science", "理研が細胞のらせん構造の仕組みを解明"))
        self.assertTrue(indexnow.candidate_off_focus("lifestyle", "サントリーの新しいラテが発売"))
        self.assertTrue(indexnow.candidate_off_focus("public", "東京都が糖尿病予防のライトアップを実施"))

    def test_ai_money_and_focus_lanes_pass(self):
        self.assertFalse(indexnow.candidate_off_focus("science", "理研が生成AIで新素材を探索"))
        self.assertFalse(indexnow.candidate_off_focus("public", "国税庁、住宅ローン控除の手続きを案内"))
        self.assertFalse(indexnow.candidate_off_focus("business", "中堅企業が新工場"))
        self.assertFalse(indexnow.candidate_off_focus("ai", "なんでも"))
        self.assertFalse(indexnow.candidate_off_focus("sports", "Jリーグの試合結果"))
        self.assertFalse(indexnow.candidate_off_focus("", "分野不明"))
        self.assertFalse(indexnow.candidate_off_focus("_unsorted", "分野不明"))

    def test_web_prefilter_uses_the_gate(self):
        import web_articles

        item = {"title": "PlayStation 5の11月の注目ゲーム7本を紹介", "summary": "", "section": "entertainment", "source_slug": "playstation-blog-jp"}
        self.assertIn("主題外", web_articles.broad_prefilter(item) or "")
        with patch.dict("os.environ", {"PUBLISH_GATE": "0"}):
            self.assertIsNone(web_articles.publish_blocked(article("x", body="短い")))
        self.assertEqual(web_articles.publish_blocked(article("x", body="短い")), "thin")


class RetireNoindexPlan(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.threads, self.bluesky, self.video = root / "threads.json", root / "bluesky.json", root / "video.json"
        self.threads.write_text(json.dumps({"posted": {"posted-threads": {"postedAt": "2026-11-01T00:00:00+00:00"},
                                                       "posted-long-ago": {"postedAt": "2026-10-01T00:00:00+00:00"}}}))
        self.bluesky.write_text(json.dumps({"posted-bluesky": {"at": "2026-11-02T00:00:00+00:00"}}))
        self.video.write_text(json.dumps({"videos": {"posted-video": {"status": "posted", "postedAt": "2026-11-02T00:00:00+00:00"}}}))
        for name, path in (("THREADS", self.threads), ("BLUESKY", self.bluesky), ("SNS_VIDEO", self.video)):
            p = patch.object(retire_articles, name, path)
            p.start()
            self.addCleanup(p.stop)

    def plan(self, articles):
        return retire_articles.plan_noindex(articles, NOW)

    def test_off_topic_is_retired_with_410_and_sns_posts_are_kept(self):
        off = dict(title="サントリーの新しいラテが発売", genreSlug="lifestyle", channelSlug="suntory")
        articles = [
            article("ok"),
            article("off", **off),
            article("posted-threads", **off),
            article("posted-bluesky", **off),
            article("posted-video", **off),
            article("posted-long-ago", **off),
            article("quake", body="短い", disaster=True),
            article("manual", body="短い", indexable=False),
        ]
        picked, kept = self.plan(articles)
        self.assertEqual(set(picked), {"off", "posted-long-ago"})
        self.assertEqual(picked["off"]["reason"], "off-focus")
        self.assertNotIn("redirect", picked["off"])
        self.assertEqual({k["slug"] for k in kept}, {"posted-threads", "posted-bluesky", "posted-video", "quake"})

    def test_duplicates_redirect_to_the_keeper(self):
        articles = [
            article("keeper", sourceUrl="https://example.test/same", body=JAPAN_BODY + "長い"),
            article("copy", sourceUrl="https://example.test/same/", title="別の題名"),
            article("marked", duplicateOf="keeper", title="さらに別の題名"),
        ]
        picked, _ = self.plan(articles)
        self.assertEqual(picked["copy"], {**picked["copy"], "reason": "duplicate", "redirect": "keeper"})
        self.assertEqual(picked["marked"]["redirect"], "keeper")
        self.assertNotIn("keeper", picked)

    def test_representative_redirect_and_roundup_handling(self):
        src = dict(title="楽天証券のつなぎ売りの解説", genreSlug="investment", channelSlug="rakuten-sec", body="## 概要\n" + "い" * 1600)
        articles = [
            article("rakuten-sec-cross-trade-genwatashi", **src),
            article("understanding-gyakuhibu-margin-trading-risks", **src),
            article("rakuten-sec-cross-trade-margin-category", **src),
            article("gyakuhibu-cross-trade-complete-guide-matome", type="roundup", sources=[
                {"slug": "rakuten-sec-cross-trade-genwatashi"}, {"slug": "understanding-gyakuhibu-margin-trading-risks"},
                {"slug": "rakuten-sec-cross-trade-margin-category"}]),
            article("other-src", title="サントリーの新しいラテが発売", genreSlug="lifestyle", channelSlug="suntory"),
            article("orphan-matome", type="roundup", sources=[{"slug": "other-src"}, {"slug": "ok"}]),
            article("ok"),
        ]
        picked, _ = self.plan(articles)
        for slug in ("rakuten-sec-cross-trade-genwatashi", "understanding-gyakuhibu-margin-trading-risks", "rakuten-sec-cross-trade-margin-category"):
            self.assertEqual(picked[slug]["redirect"], "gyakuhibu-cross-trade-complete-guide-matome")
        # 取り下げた元記事の転送先になっているまとめは残す。元記事が1本しか残らないまとめは一緒に取り下げる
        self.assertNotIn("gyakuhibu-cross-trade-complete-guide-matome", picked)
        self.assertEqual(picked["orphan-matome"]["reason"], "roundup-of-retired")


class RewriterSelfLinks(unittest.TestCase):
    def test_links_redirected_to_the_article_itself_become_plain_text(self):
        rw = retire_articles.Rewriter({"gone": {"redirect": "home"}, "dead": {}})
        rw.self_slug = "home"
        self.assertEqual(rw.text("[元記事](/articles/gone) と [別](/articles/dead)"), "元記事 と 別")
        self.assertEqual(rw.slugs(["gone", "x"]), ["x"])
        rw.self_slug = "elsewhere"
        self.assertEqual(rw.text("[元記事](/articles/gone)"), "[元記事](/articles/home)")


if __name__ == "__main__":
    unittest.main()
