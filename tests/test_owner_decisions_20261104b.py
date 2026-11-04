"""2026-11-04 オーナー承認（2回目）の決まりの検査。

- W33: 報道を出典にした話題の記事（TREND_FROM_PRESS）は作らない（既定 0。どのワークフローも 1 にしない）
- A43: ゲーム機・ゲームのサービスの公式の発表（本体・料金・セール）は有名ブランドのレーンで扱う（レビュー・ゴシップ・報道は外す）
- A10: 速報・お知らせ・注意喚起・ミサイル・災害の記事は本文800字から公開・検索に出す（ほかは1,500字のまま）
- A44: 事実の束は同じ会社の IR の開示資料（適時開示の PDF）まで読み、読めなかった資料があるのに「未発表」と書かせない
"""
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import facts_bundle  # noqa: E402
import genre_focus  # noqa: E402
import indexnow  # noqa: E402
import news_mix  # noqa: E402
import news_types  # noqa: E402
import public_interest  # noqa: E402
import quality  # noqa: E402
import retire_articles  # noqa: E402
import source_links  # noqa: E402
import web_articles  # noqa: E402

LONG_JA = "これは本文です。" * 120  # 960字


class TrendFromPressOffTest(unittest.TestCase):
    def test_default_off_and_no_workflow_turns_it_on(self):
        self.assertFalse(web_articles.TREND_FROM_PRESS)
        for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
            text = wf.read_text("utf-8")
            self.assertNotRegex(text, r"TREND_FROM_PRESS\s*[:=]\s*['\"]?1", wf.name)


class OfficialGameNewsTest(unittest.TestCase):
    OK = [
        ("PS5 Proの価格を11月20日に改定", "playstation-blog-jp"),
        ("Nintendo Switch 2 本体の国内発売日が決定", "nintendo-topics"),
        ("Xbox Game Pass Ultimateの月額料金を変更", "xbox-wire-jp"),
        ("PlayStation Storeでブラックフライデーセールを開催", "playstation-blog-jp"),
    ]
    NG = [
        ("『新作RPG』レビュー：Switch 2で遊んでみた", "nintendo-topics"),  # レビュー
        ("PlayStation Storeでセールを開催", "gamespark"),  # 公式でない
        ("『新作RPG』本日発売", "nintendo-topics"),  # ソフトの告知（ゲーム機・サービスの話ではない）
        ("Nintendo Direct 2026.11 配信番組のお知らせ", "nintendo-topics"),
        ("PS5 発売記念の声優インタビュー", "playstation-blog-jp"),
    ]

    def test_official_hardware_service_price_sale_pass_every_gate(self):
        for title, src in self.OK:
            self.assertTrue(genre_focus.official_game_news(title, src), title)
            self.assertIsNone(genre_focus.candidate_problem("entertainment", title, "", src), title)
            self.assertFalse(indexnow.candidate_off_focus("entertainment", title, "", src), title)
            self.assertTrue(indexnow.is_focus({"genreSlug": "entertainment", "title": title, "channelSlug": src}), title)
            self.assertIn(news_mix.classify({"title": title, "source_slug": src}), ("brand", "money"), title)

    def test_reviews_gossip_software_and_press_stay_out(self):
        for title, src in self.NG:
            self.assertFalse(genre_focus.official_game_news(title, src), title)
            self.assertFalse(indexnow.is_focus({"genreSlug": "entertainment", "title": title, "channelSlug": src}), title)
        self.assertIsNotNone(genre_focus.candidate_problem("entertainment", "人気俳優の熱愛が発覚", "", "playstation-blog-jp"))

    def test_site_rules_carry_the_same_lists(self):
        rules = indexnow.index_rules()
        self.assertEqual(rules["gameOfficialSources"], sorted(genre_focus.GAME_OFFICIAL_SOURCES))
        self.assertEqual(rules["gamePlatform"]["source"], genre_focus.GAME_PLATFORM_RE.pattern)
        self.assertFalse(indexnow.rules_drift(), "lib/index-rules.json を python3 scripts/indexnow.py --export-rules で書き出す")


class ShortArticleFloorTest(unittest.TestCase):
    def rec(self, **kw):
        base = {"slug": "x", "sourceType": "web", "channelSlug": "official", "genreSlug": "ai", "title": "新機能",
                "body": LONG_JA + "\n\n## 日本での使い方\n日本でも使えます。", "verdict": {"japan": "ok"}}
        return {**base, **kw}

    def test_breaking_and_notice_types_publish_from_800(self):
        self.assertEqual(public_interest.SHORT_BODY, 800)
        for extra in ({"breaking": True}, {"newsType": "breaking"}, {"title": "Microsoft 365で障害、Outlookがつながりにくい"},
                      {"title": "国税庁を名乗るフィッシングメールに注意喚起"}, {"genreSlug": "public", "title": "北朝鮮が弾道ミサイルを発射"}):
            r = self.rec(**extra)
            self.assertTrue(public_interest.short_ok(r), extra)
            self.assertIsNone(indexnow.noindex_reason(r), extra)
            self.assertIsNone(indexnow.publish_block_reason(r), extra)
            self.assertFalse(retire_articles.is_thin(r), extra)

    def test_other_articles_keep_1500(self):
        r = self.rec()
        self.assertFalse(public_interest.short_ok(r))
        self.assertEqual(indexnow.noindex_reason(r), "thin")
        self.assertEqual(indexnow.THIN_BODY, 1500)
        self.assertFalse(public_interest.short_ok(self.rec(genreSlug="public", title="障害者手帳のアプリ")))
        tiny = self.rec(breaking=True, body="短い。" * 100)
        self.assertEqual(indexnow.noindex_reason(tiny), "thin")

    def test_prompt_length_matches_the_gate(self):
        self.assertEqual(news_types.get("breaking").length[0], 800)
        self.assertEqual(news_types.SHORT_FLOOR, public_interest.SHORT_BODY)
        self.assertEqual(news_types.LONG_FLOOR, indexnow.THIN_BODY)
        self.assertIn("必ず800字以上", news_types.prompt_block("breaking"))
        self.assertIn("必ず1,600字以上", news_types.prompt_block("product"))
        self.assertIn("1,500字に届かない", news_types.prompt_block("product"))


TDNET_HTML = """
<tr><td class="oddnew-L kjTime" noWrap>15:30</td>
<td class="oddnew-M kjCode" noWrap>71820</td>
<td class="oddnew-M kjName" noWrap>ゆうちょ銀行            </td>
<td class="oddnew-M kjTitle" align="left"><a href="140120261030561959.pdf" target="_blank">松井証券株式会社との資本業務提携に関するお知らせ</a></td>
</tr>
<tr><td class="evennew-L kjTime" noWrap>15:30</td>
<td class="evennew-M kjCode" noWrap>12340</td>
<td class="evennew-M kjName" noWrap>別の会社</td>
<td class="evennew-M kjTitle" align="left"><a href="999.pdf" target="_blank">松井証券株式会社との資本業務提携に関するお知らせ</a></td>
</tr>
"""
IR_PDF_TEXT = ("松井証券株式会社との資本業務提携に関するお知らせ\n当行は、市場外の相対取引にて、松井証券の普通株式51,489,700株"
               "（発行済株式総数に対する割合は19.86％、議決権比率は19.99％）を取得し、筆頭株主となる予定です。\n"
               "本資本業務提携の開始日 2026年12月28日（予定）\n") * 2
TITLE = "ゆうちょ銀行、松井証券の議決権の約20%を取得　資本業務提携"
SRC = "https://www.jp-bank.japanpost.jp/aboutus/press/2026/pdf/pr261104.pdf"
WHEN = datetime(2026, 11, 4, 7, 0, tzinfo=timezone.utc)


class IrDocumentsTest(unittest.TestCase):
    def test_tdnet_rows_and_company_match(self):
        rows = source_links.tdnet_rows(TDNET_HTML)
        self.assertEqual(rows[0]["url"], "https://www.release.tdnet.info/inbs/140120261030561959.pdf")
        docs = source_links.ir_documents(None, SRC, TITLE, "ゆうちょ銀行", WHEN, fetcher=lambda u, t: {},
                                         list_fetcher=lambda day, n: TDNET_HTML if n == 1 else "")
        self.assertEqual([d["url"] for d in docs], ["https://www.release.tdnet.info/inbs/140120261030561959.pdf"])

    def test_ir_host_linked_from_official_page_counts_as_same_company(self):
        link = {"url": "https://ssl4.eir-parts.net/doc/7182/tdnet/2904076/00.pdf", "text": "松井証券株式会社との資本業務提携に関するお知らせ"}
        self.assertGreater(source_links.score_link(link, "https://www.jp-bank.japanpost.jp/ir/news/", source_links._words(TITLE), False, "deal"), 0)
        other = {"url": "https://news.example.com/doc/1.pdf", "text": "松井証券株式会社との資本業務提携"}
        self.assertEqual(source_links.score_link(other, "https://www.jp-bank.japanpost.jp/ir/news/", source_links._words(TITLE), False, "deal"), -1)

    def test_bundle_reads_the_ir_pdf(self):
        def fetcher(url, timeout):
            return {"text": IR_PDF_TEXT, "title": "", "pdf": True}

        item = {"title": TITLE, "url": SRC, "source_name": "ゆうちょ銀行", "published_at": WHEN}
        bundle = facts_bundle.build(item, "ゆうちょ銀行は松井証券と資本業務提携を結びます。" * 5, [], "investment", {"links": []},
                                    fetcher=fetcher, list_fetcher=lambda day, n: TDNET_HTML if n == 1 else "", news_type="deal")
        block = bundle.prompt_block()
        self.assertIn("51,489,700株", block)
        self.assertIn("IR（適時開示）", block)
        self.assertEqual(bundle.unread_docs, [])

    def test_unread_ir_pdf_blocks_undisclosed_claims(self):
        def broken(url, timeout):
            raise ValueError("PDF の文字を取り出せない")

        item = {"title": TITLE, "url": SRC, "source_name": "ゆうちょ銀行", "published_at": WHEN}
        bundle = facts_bundle.build(item, "ゆうちょ銀行は松井証券と資本業務提携を結びます。" * 5, [], "investment", {"links": []},
                                    fetcher=broken, list_fetcher=lambda day, n: TDNET_HTML if n == 1 else "", news_type="deal")
        self.assertEqual(bundle.unread_docs, ["https://www.release.tdnet.info/inbs/140120261030561959.pdf"])
        self.assertIn("読めなかった開示資料", bundle.prompt_block())
        record = {"title": TITLE, "lede": "a", "body": "## まだ分からないこと\n- 取得する株数は、まだ発表されていません。", "_facts": bundle.meta()}
        self.assertTrue(facts_bundle.undisclosed_problems(record))
        self.assertTrue(any("A44" in p for p in quality.every_path_problems(record)))
        record["body"] = "## まだ分からないこと\n- 取得の金額は、発表の本文では触れていません。"
        self.assertEqual(facts_bundle.undisclosed_problems(record), [])
        record["body"] = "取得する株数は、まだ発表されていません。"
        record["_facts"] = {**bundle.meta(), "unreadDocs": []}
        self.assertEqual(facts_bundle.undisclosed_problems(record), [])

    def test_non_ir_topics_do_not_search_tdnet(self):
        self.assertFalse(source_links.ir_wanted("Gemini に新しい画像生成の機能", "product", "Google は新機能を発表しました。"))
        self.assertTrue(source_links.ir_wanted("ゆうちょ銀行、松井証券と資本業務提携", "", ""))
        self.assertTrue(source_links.ir_wanted("新製品", "earnings", ""))

    def test_every_writing_path_runs_the_guard(self):
        src = (ROOT / "scripts" / "quality.py").read_text("utf-8")
        self.assertRegex(src, r"def every_path_problems[\s\S]{0,900}facts_bundle\.undisclosed_problems")
        self.assertRegex(src, r"def structural_problems[\s\S]{0,4000}facts_bundle\.undisclosed_problems")
        self.assertTrue(re.search(r"^pypdf", (ROOT / "requirements.txt").read_text("utf-8"), re.M))


if __name__ == "__main__":
    unittest.main()
