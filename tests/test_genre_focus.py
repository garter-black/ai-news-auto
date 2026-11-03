"""投資・マネー／不動産／エンタメの扱う中身と1日の最低本数（scripts/genre_focus.py。2026-11-03 オーナー決定・台帳 A22・A23）。

1. 振り分け: 混ざったフィードの投資・不動産・エンタメの話題を、それぞれのレーンへ（関係ない話題は動かさない）
2. 生成前の足切り: そのジャンルで扱う話題は通し、お知らせ・統計の書き写し・告知・ゴシップは落とす（両方向の例）
3. 検索に出す・公開してよい: エンタメはビジネス・テックの目線だけ
4. 投資助言・売買のすすめ・取引のやり方の言い回しを品質ゲートで落とす。投資・マネーの記事には短い免責文
5. 1日の最低本数: 候補があり、今日まだ届いていないジャンルを先に回す（候補が無ければ何もしない）
6. 発信元: 新しいフィードがジャンルに付いている。PDF をそのまま本文にしない
ネットワークは使わない。
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import feeds  # noqa: E402
import genre_focus  # noqa: E402
import genres  # noqa: E402
import indexnow  # noqa: E402
import quality  # noqa: E402

JST = timezone(timedelta(hours=9))


class RouteTest(unittest.TestCase):
    def test_money_and_entertainment_topics_move_to_their_lanes(self):
        cases = [
            ("金融庁、NISAの口座開設の手続きを簡素化", "public", "investment"),
            ("当面の金融政策運営について", "business", "investment"),
            ("〇〇証券、投資信託の購入手数料を無料に", "business", "investment"),
            ("ＥＣＢ政策理事会－利上げを示唆しつつも今回は利上げ見送り", "business", "investment"),
            ("令和9年地価公示の結果を公表", "business", "real-estate"),
            ("大手5行、住宅ローンの変動金利を引き上げ", "business", "real-estate"),
            ("住宅ローン控除の手続きを案内", "public", "real-estate"),
            ("Netflix、広告つきプランの月額料金を値上げ", "tech-gadget", "entertainment"),
            ("任天堂、上期の決算を発表", "business", "entertainment"),
        ]
        for title, section, want in cases:
            with self.subTest(title=title):
                self.assertEqual(genre_focus.route_section({"title": title}, section), want)

    def test_unrelated_or_routine_items_stay(self):
        cases = [
            ("新しいスマートフォンを発売", "tech-gadget"),
            ("物流で業務提携", "business"),
            ("ソフトバンク 剰余金の配当（中間配当）に関するお知らせ", "business"),
            ("2026年10月の売買状況について", "business"),
            ("サントリーの新しいラテ", "lifestyle"),
            ("Netflixの社内カルチャーを紹介", "tech-gadget"),  # 料金・決算などのビジネスの目印がない
        ]
        for title, section in cases:
            with self.subTest(title=title):
                self.assertEqual(genre_focus.route_section({"title": title}, section), section)
        # エンタメ・スポーツ・科学のフィードは動かさない
        self.assertEqual(genre_focus.route_section({"title": "日本代表のスポンサー契約で住宅ローン"}, "sports"), "sports")
        self.assertIsNone(genre_focus.route_section({"title": "NISA"}, None))

    def test_word_boundaries(self):
        """Netflix の中の etf・REITS のような英字の一部で投資・不動産と判定しない"""
        self.assertIsNone(genre_focus.ROUTE_INVESTMENT_RE.search("Netflix の新機能"))
        self.assertTrue(genre_focus.ROUTE_INVESTMENT_RE.search("日銀のETF売却"))


class CandidateTest(unittest.TestCase):
    PASS = [
        ("investment", "[JPX総研]「TOPIX Next-tier」の算出開始について"),
        ("investment", "2027年3月期第2四半期末の有価証券含み損に関するお知らせ"),
        ("investment", "楽天証券、新NISAのクレジットカード積立の上限を月10万円に"),
        ("real-estate", "東急リバブル、ホテル不動産の事業性検討を最短3営業日・20万円で"),
        ("real-estate", "マンションの顔認証「FreeiD」を標準装備した物件が竣工"),
        ("real-estate", "フラット35の11月の金利、最低1.95%に上昇"),
        ("entertainment", "カプコン、中間決算は前年同期比で増収増益"),
        ("entertainment", "「アニメ産業レポート2026」刊行・2025年のアニメ産業市場規模 速報値 発表"),
        ("entertainment", "ABEMA、学生向けの音楽サブスクを月額480円で提供"),
        ("entertainment", "Spotify、生成AIでプレイリストを作る機能を日本で提供"),
    ]
    BLOCK = [
        ("investment", "[JPX,東証,OSE,TOCOM]2026年10月の売買状況について"),  # 統計の書き写し
        ("investment", "自己株式の取得状況に関するお知らせ"),
        ("investment", "金融審議会ワーキング・グループ（第３回）の開催について公表しました。"),
        ("investment", "〇〇証券に対する行政処分について"),
        ("investment", "本日の一部報道について"),  # 投資の話題ではない
        ("real-estate", "京都府立植物園の夜間イベント NIGHT GARDEN"),
        ("real-estate", "学生情報センターのサステナビリティ推進 電力の100%実質再エネ化"),
        ("real-estate", "介護施設の転居の実態を調査"),
        ("entertainment", "人気俳優の熱愛が発覚"),
        ("entertainment", "映画『〇〇』予告編を解禁　追加キャストも発表"),
        ("entertainment", "11月発売のPS5注目ソフトはこれだ！【今月のおすすめ新作ガイド】"),
        ("entertainment", "『HELLDIVERS 2』で記念ギフトを配布"),
        ("entertainment", "『ピクミン4』の体験版が配信開始"),
        ("entertainment", "人気アニメのコラボグッズを発売、価格は1500円"),
    ]

    def test_focused_topics_pass(self):
        for lane, title in self.PASS:
            with self.subTest(title=title):
                self.assertIsNone(genre_focus.candidate_problem(lane, title))

    def test_gossip_notices_and_transcriptions_fail(self):
        for lane, title in self.BLOCK:
            with self.subTest(title=title):
                self.assertTrue(genre_focus.candidate_problem(lane, title))

    def test_other_lanes_are_left_to_their_own_rules(self):
        self.assertIsNone(genre_focus.candidate_problem("business", "なんでも"))
        self.assertIsNone(genre_focus.candidate_problem("ai", "なんでも"))

    def test_web_prefilter_uses_it(self):
        import web_articles

        ok = {"title": "フラット35の11月の金利、最低1.95%に上昇", "summary": "", "section": "real-estate", "source_slug": "mlit-press"}
        self.assertIsNone(web_articles.broad_prefilter(ok))
        ng = {"title": "[JPX,東証,OSE,TOCOM]2026年10月の売買状況について", "summary": "", "section": "investment", "source_slug": "jpx-news"}
        self.assertIn("書き写し", web_articles.broad_prefilter(ng) or "")
        ent = {"title": "カプコン、中間決算は前年同期比で増収増益", "summary": "", "section": "entertainment", "source_slug": "capcom-ir"}
        self.assertIsNone(web_articles.broad_prefilter(ent))
        self.assertFalse(indexnow.candidate_off_focus("entertainment", ent["title"]))
        self.assertTrue(indexnow.candidate_off_focus("entertainment", "『ピクミン4』の体験版が配信開始"))

    def test_tag_sections_routes(self):
        import web_articles

        items = [{"title": "当面の金融政策運営について", "summary": "", "source_slug": "boj"},
                 {"title": "令和9年地価公示の結果を公表", "summary": "", "source_slug": "mlit-press"},
                 {"title": "消費者物価指数", "summary": "", "source_slug": "stat"}]
        web_articles.tag_sections(items)
        self.assertEqual([i["section"] for i in items], ["investment", "real-estate", "business"])


class FocusTest(unittest.TestCase):
    def article(self, **kw):
        return {"slug": "x", "body": "## 日本で使えるのは12月から\n" + "あ" * 2000, "verdict": None, **kw}

    def test_entertainment_business_angle_is_in_focus(self):
        for title in ("カプコン、中間決算は増収増益　販売本数は前年比1.3倍", "Netflix、広告つきプランの月額を890円に",
                      "アニメ産業の市場規模、2025年は3.8兆円"):
            with self.subTest(title=title):
                a = self.article(genreSlug="entertainment", title=title)
                self.assertTrue(indexnow.is_focus(a))
                self.assertIsNone(indexnow.publish_block_reason(a))

    def test_entertainment_listing_and_gossip_stay_off_focus(self):
        for title in ("映画の予告編を公開　キャストも発表", "人気アニメのコラボグッズを発売、価格は1500円", "PS5版の新作に無料コスチューム"):
            with self.subTest(title=title):
                a = self.article(genreSlug="entertainment", title=title)
                self.assertFalse(indexnow.is_focus(a))
                self.assertEqual(indexnow.publish_block_reason(a), "off-focus")

    def test_money_genres_are_in_focus(self):
        self.assertTrue(indexnow.is_focus(self.article(genreSlug="investment", title="金融庁がNISAの手続きを簡素化")))
        self.assertTrue(indexnow.is_focus(self.article(genreSlug="real-estate", title="公示地価が上昇")))


class AdviceTest(unittest.TestCase):
    def record(self, **kw):
        base = {"genreSlug": "investment", "title": "金融庁、NISAの手続きを簡素化", "body": "制度の説明です。",
                "disclaimer": genres.INVESTMENT.disclaimer}
        base.update(kw)
        return base

    def test_advice_and_trade_howto_are_rejected(self):
        bad = [
            {"body": "今が買い時です。"},
            {"body": "## 注目銘柄はこの3社\n説明"},
            {"body": "目標株価は5000円とみられます。"},
            {"workMoney": {"relevance": "high", "points": ["高配当株の買い増しがおすすめです。"]}},
            {"body": "## つなぎ売りのやり方\n信用口座で売る"},
            {"body": "## 注文方法\n成行注文で買います"},
            {"faq": [{"q": "いつ買えばいい？", "a": "押し目買いが基本です。"}]},
            {"title": "必ず儲かるNISAの使い方"},
        ]
        for extra in bad:
            with self.subTest(extra=extra):
                self.assertTrue(genre_focus.advice_problems(self.record(**extra)))

    def test_plain_information_passes(self):
        good = [
            {"body": "金融庁は、NISAの口座を開く手続きを12月から簡素化すると発表しました。対象は新しく口座を開く人です。"},
            {"body": "カプコンの上期の売上高は前年同期比18%増の800億円でした。会社は通期の見通しを据え置いています。", "genreSlug": "entertainment"},
            {"body": "予約・注文方法は公式サイトで案内されています。", "genreSlug": "tech-gadget", "disclaimer": ""},
            {"body": "家電の買い場所を選ぶ", "genreSlug": "lifestyle", "disclaimer": ""},
        ]
        for extra in good:
            with self.subTest(extra=extra):
                self.assertEqual(genre_focus.advice_problems(self.record(**extra)), [])

    def test_investment_needs_short_disclaimer(self):
        self.assertTrue(genre_focus.advice_problems(self.record(disclaimer="本記事は自動生成したものです。")))
        self.assertIn(genre_focus.ADVICE_DISCLAIMER_MARK, genres.INVESTMENT.disclaimer)
        # 以前の動画の記事の言い方（投資助言にも該当しません）でも免責文として数える
        self.assertEqual(genre_focus.advice_problems(self.record(disclaimer="投資助言にも該当しません。")), [])

    def test_quality_gate_runs_it(self):
        record = self.record(body="今が買い時です。", sourceType="web")
        problems = quality.structural_problems(record, "broad")
        self.assertTrue(any("投資助言" in p for p in problems), problems)

    def test_web_disclaimer_is_the_short_one(self):
        import web_articles

        for genre in (genres.INVESTMENT, genres.REAL_ESTATE):
            text = web_articles.web_disclaimer(genre)
            self.assertEqual(text, genre.disclaimer)
            self.assertLess(len(text), 130)
            self.assertNotIn("動画", text)


class PacingTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        self.now = datetime(2026, 11, 3, 12, 0, tzinfo=JST)

    def write(self, slug, genre):
        (self.dir / f"{slug}.json").write_text(json.dumps({"slug": slug, "genreSlug": genre}), "utf-8")

    def state(self, *slugs):
        at = self.now.isoformat()
        return {"processed_web": {f"id-{s}": {"slug": s, "at": at} for s in slugs}}

    def test_short_genres_with_candidates_go_first(self):
        self.write("a", "investment")
        lanes = {"investment": [1], "real-estate": [1], "entertainment": [1], "business": [1]}
        with patch.dict("os.environ", {"GENRE_DAILY_MIN": ""}):
            got = genre_focus.boost_lanes(lanes, self.state("a"), self.now, self.dir)
        self.assertEqual(got, ["real-estate", "investment", "entertainment"])

    def test_no_candidates_no_boost_and_met_minimum(self):
        for i in range(2):
            self.write(f"r{i}", "real-estate")
        lanes = {"investment": [], "real-estate": [1], "entertainment": [1]}
        got = genre_focus.boost_lanes(lanes, self.state("r0", "r1"), self.now, self.dir)
        self.assertEqual(got, ["entertainment"])  # 投資は候補なし・不動産は最低本数に届いた

    def test_retired_articles_do_not_count_and_off_switch(self):
        lanes = {"investment": [1]}
        self.assertEqual(genre_focus.boost_lanes(lanes, self.state("gone"), self.now, self.dir), ["investment"])
        with patch.dict("os.environ", {"GENRE_DAILY_MIN": "off"}):
            self.assertEqual(genre_focus.boost_lanes(lanes, {}, self.now, self.dir), [])

    def test_lane_order_puts_boosted_lanes_after_ai(self):
        import web_articles

        lanes = {k: [1] for k in ["ai", "business", "public", "investment", "real-estate", "entertainment", "sports"]}
        with patch.object(genre_focus, "boost_lanes", return_value=["real-estate", "entertainment"]):
            order = web_articles.lane_order(lanes, self.now)
        self.assertEqual(order[:5], ["ai", "real-estate", "entertainment", "business", "public"])
        self.assertEqual(order[-1], "sports")
        self.assertIn("investment", order)


class ReplaceOnFirstTest(unittest.TestCase):
    """新しい質の良い投資・マネーの記事が出た回に、古い記事（証券会社の動画の要約）を取り下げる一回きりの決まり（2026-11-04 オーナー承認）"""

    OLD = "gyakuhibu-cross-trade-complete-guide-matome"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.dir = self.root / "content" / "articles"
        self.dir.mkdir(parents=True)
        self.write({"slug": self.OLD, "genreSlug": "investment", "title": "株主優待の「つなぎ売り」入門　失敗しないコツ",
                    "generatedAt": "2026-08-12T01:04:16+00:00", "channelSlug": "rakuten-sec", "body": "あ" * 3000,
                    "videoUrl": "https://www.youtube.com/watch?v=x"})
        self.write({"slug": "ai-news", "genreSlug": "ai", "title": "新しいAIモデル", "relatedSlugs": [self.OLD],
                    "generatedAt": "2026-11-03T20:00:00+00:00", "body": "## 日本で使える\n" + "あ" * 2000})

    def write(self, data):
        (self.dir / f"{data['slug']}.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")

    def good(self, slug="nisa-fee", **kw):
        data = {"slug": slug, "genreSlug": "investment", "title": "〇〇証券、投資信託の購入手数料を12月から無料に",
                "generatedAt": "2026-11-04T01:00:00+00:00", "body": "## 料金は12月から無料\n" + "あ" * 2000,
                "disclaimer": genres.INVESTMENT.disclaimer}
        data.update(kw)
        self.write(data)

    def test_armed_until_a_quality_article_appears(self):
        self.assertEqual(genre_focus.replacement_plan(self.dir), {})
        # 合図にならない: 古い記事・別ジャンル・薄い・お知らせの転載・投資助言の言い回し・免責文なし
        self.good("before", generatedAt="2026-11-03T10:00:00+00:00")
        self.good("other", genreSlug="business")
        self.good("thin", body="短い")
        self.good("notice", title="決算発表日のお知らせ　11月10日に発表予定")
        self.good("advice", body="## 料金は12月から無料\n今が買い時です。" + "あ" * 2000)
        self.good("nodisc", disclaimer="本記事は自動生成です。")
        self.assertEqual(genre_focus.replacement_plan(self.dir), {})

    def test_fires_with_410_when_no_related_article(self):
        self.good()
        plan = genre_focus.replacement_plan(self.dir)
        self.assertEqual(list(plan), [self.OLD])
        self.assertEqual(plan[self.OLD]["replacedBy"], "nisa-fee")
        self.assertNotIn("redirect", plan[self.OLD])  # 410
        self.assertEqual(plan[self.OLD]["reason"], "investment-advice")

    def test_redirects_to_related_live_article(self):
        self.good()
        self.good("yutai-guide", title="株主優待の制度変更　〇〇社が信用取引の条件を見直し", generatedAt="2026-11-04T02:00:00+00:00")
        self.assertEqual(genre_focus.replacement_plan(self.dir)[self.OLD]["redirect"], "yutai-guide")

    def test_apply_uses_the_retirement_mechanism_once(self):
        import retire_articles

        self.good()
        retired = self.root / "content" / "retired.json"
        with patch.object(genre_focus, "ARTICLES_DIR", self.dir), patch.object(retire_articles, "ARTICLES", self.dir), \
                patch.object(retire_articles, "RETIRED", retired), patch.object(retire_articles, "ROOT", self.root):
            genre_focus.retire_replaced(apply_changes=True)
            self.assertFalse((self.dir / f"{self.OLD}.json").exists())
            store = json.loads(retired.read_text("utf-8"))["articles"]
            self.assertEqual(store[self.OLD]["reason"], "investment-advice")
            # 関連記事のリンクも外れる
            self.assertNotIn(self.OLD, json.loads((self.dir / "ai-news.json").read_text("utf-8")).get("relatedSlugs", []))
            # 二度目は何もしない（一回きり）
            self.assertEqual(genre_focus.retire_replaced(apply_changes=True), {})

    def test_rule_points_at_the_real_article_and_runs_in_the_pipeline(self):
        rule = genre_focus.REPLACE_ON_FIRST[0]
        self.assertEqual(rule["retire"], self.OLD)
        workflow = (ROOT / ".github" / "workflows" / "auto_run.yml").read_text("utf-8")
        self.assertIn("genre_focus.py --retire-replaced", workflow)


class NoticeAlignmentTest(unittest.TestCase):
    """全体のお知らせ・統計の門（scripts/notice_gate.py）を先に使い、投資・不動産に特有の知らせだけを足す"""

    def test_global_gate_is_used(self):
        for lane, title in (("investment", "2027年3月期の決算発表日のお知らせ"), ("real-estate", "住宅着工統計の速報値を公表"),
                            ("entertainment", "〇〇賞の受章者を発表")):
            with self.subTest(title=title):
                self.assertIn("お知らせ・統計の転載", genre_focus.candidate_problem(lane, title) or "")
        self.assertEqual(genre_focus.route_section({"title": "住宅ローン金利の議事要旨を公開"}, "business"), "business")

    def test_genre_specific_notices_still_blocked_and_analysis_passes(self):
        self.assertIn("書き写し", genre_focus.candidate_problem("investment", "2026年10月の売買状況について") or "")
        self.assertIsNone(genre_focus.candidate_problem("investment", "家計調査の数字が示すNISAの広がりを分析", source_slug="nli-research"))


class SourcesTest(unittest.TestCase):
    def test_lanes_and_feeds(self):
        import web_articles

        for slug in ("investment", "real-estate"):
            self.assertIn(slug, web_articles.BROAD_SLUGS)
        sections = {}
        for f in feeds.BROAD_FEEDS:
            sections.setdefault(f["section"], []).append(f["slug"])
        self.assertGreaterEqual(len(sections.get("investment", [])), 3)
        self.assertGreaterEqual(len(sections.get("real-estate", [])), 3)
        self.assertGreaterEqual(len(sections.get("entertainment", [])), 4)
        self.assertNotIn("prtimes-all", feeds.FEEDS_BY_SLUG)

    def test_pdf_is_not_read_as_text(self):
        with patch.object(feeds, "fetch", return_value=(b"%PDF-1.7\n1 0 obj", "https://example.com/a.pdf")):
            with self.assertRaises(ValueError):
                feeds._fetch_direct("https://example.com/a.pdf")


if __name__ == "__main__":
    unittest.main()
