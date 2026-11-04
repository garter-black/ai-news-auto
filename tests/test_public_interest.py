"""多くの人が読む公共性の高いニュース（台帳 A39・A40。scripts/public_interest.py）の検査。通信しない・API を使わない。

A40: 官公庁・会社自身の発表で、情報漏えい・ミサイル・災害・リコール・詐欺・暮らしのお金の変更の話題は「大きなニュース」として先に作る。
     政党の申し入れ・個人の事件・告知・統計の転載・報道が出典のものは回さない。1日の上限あり。検索に出す主題に入れる。
A39: どの経路の記事にも、煽り・推測・私人の実名・政治の意見を止め、確かなことと分かっていないことを分けさせ、
     ミサイル・災害の記事には公式の安全情報のリンクを入れる。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import public_interest as pi  # noqa: E402

JST = timezone(timedelta(hours=9))


class CategoryTest(unittest.TestCase):
    def test_real_titles_are_classified(self):
        cases = {
            "北朝鮮のミサイル等関連情報（続報）について": "missile",
            "高市総理は北朝鮮からの弾道ミサイルの可能性があるものの発射に関する指示を行いました": "missile",
            "弊社委託先が利用するメール配信サービスへの不正アクセスによるメールアドレス流出についてのお詫び": "breach",
            "ライフネット生命の一部システムへの不正アクセスによるKDDIお客さま情報の漏えいの可能性について": "breach",
            "消費生活用製品の重大製品事故:リコール製品で火災等(スピーカー(充電式))(10月30日)": "recall",
            "令和8年熊本地震罹災証明書（り災証明書）のオンライン申請について": "disaster",
            "特別高圧電力を利用する中小企業者の電気料金の一部を支援": "money",
        }
        for title, want in cases.items():
            self.assertEqual(pi.category(title), want, title)

    def test_summary_alone_does_not_make_a_topic(self):
        # 概要の一言（被災者支援など）で寄贈・見学ツアー・大臣会見を公共性の話題にしない
        self.assertIsNone(pi.category("全世界で100万点を超えるヒートテックを寄贈", "被災者支援の一環として"))
        self.assertIsNone(pi.category("葉梨大臣記者会見(令和8年11月2日)", "個人情報流出について"))


class CandidateTest(unittest.TestCase):
    def item(self, title, kind="gov", url="https://www.mod.go.jp/j/press/news/2026/10/29d.html", **kw):
        return {"title": title, "summary": "", "url": url, "kind": kind, **kw}

    def test_official_items_are_candidates(self):
        self.assertTrue(pi.is_candidate(self.item("北朝鮮のミサイル等関連情報（続報）について")))
        self.assertTrue(pi.is_candidate(self.item("当社管理ウェブサイトにおける不正アクセスおよび個人情報流出の可能性について", kind="company",
                                                  url="https://www.tokyu-fudosan-hd.co.jp/news/x")))

    def test_politics_personal_crime_notices_and_stats_are_not(self):
        for title in (
            "高市総理は日本維新の会・物価高騰対策に関する提言申入れを受けました",
            "木原内閣官房長官は日本維新の会・サイバー危機への緊急提言申入れを受けました",
            "不正アクセスの疑いで会社員の男を逮捕",
            "「特殊詐欺被害防止強化月間」に！",
            "令和８年９月末の特殊詐欺の認知・検挙状況等について",
            "弾道ミサイルを想定した住民避難訓練の実施",
            "中央最低賃金審議会 (目安に関する小委員会)",
        ):
            self.assertFalse(pi.is_candidate(self.item(title)), title)

    def test_outlets_wire_and_radar_are_never_sources(self):
        # W31: 報道が出典のものは回さない（公式の発表が出てから）
        self.assertFalse(pi.is_candidate(self.item("北朝鮮が弾道ミサイルを発射", url="https://www3.nhk.or.jp/news/x.html")))
        self.assertFalse(pi.is_candidate(self.item("不正アクセスによる情報流出のお詫び", kind="wire")))
        self.assertFalse(pi.is_candidate(self.item("不正アクセスによる情報流出のお詫び", kind="derivative")))
        self.assertFalse(pi.is_candidate(self.item("北朝鮮のミサイル等関連情報", radar=True)))

    def test_company_can_announce_its_own_breach_but_not_missiles_or_disasters(self):
        self.assertFalse(pi.is_candidate(self.item("鉱工業生産26年9月－熊本地震や台風・豪雨の影響", kind="company")))
        self.assertFalse(pi.is_candidate(self.item("北朝鮮の弾道ミサイル発射と市場への影響", kind="company")))

    def test_product_ads_are_not_incidents(self):
        self.assertFalse(pi.is_candidate(self.item("サイバー攻撃対策サービスの提供開始について", kind="company")))
        self.assertTrue(pi.is_candidate(self.item("不正アクセスによる情報漏えい等の事案を踏まえたセキュリティ対策の徹底について（注意喚起）")))

    def test_quake_reports_stay_with_the_template(self):
        # A15: 地震・津波の発生の記事は scripts/quake.py の型だけ
        self.assertIsNotNone(pi.candidate_problem("震度6弱の地震による被害状況（第2報）"))
        self.assertIsNone(pi.candidate_problem("令和8年熊本地震罹災証明書（り災証明書）のオンライン申請について"))

    def test_only_missiles_and_disasters_from_government_are_breaking(self):
        self.assertTrue(pi.breaking_ok(self.item("北朝鮮のミサイル等関連情報（続報）について")))
        self.assertFalse(pi.breaking_ok(self.item("不正アクセスによる情報流出のお詫び")))
        self.assertFalse(pi.breaking_ok(self.item("北朝鮮のミサイル等関連情報", kind="company")))
        # 被災者の手続きの案内は災害の話題でも速報にしない（S2）
        self.assertFalse(pi.breaking_ok(self.item("令和8年熊本地震罹災証明書（り災証明書）のオンライン申請について")))
        self.assertTrue(pi.breaking_ok(self.item("令和8年台風第30号に関する特別警報の発表について")))

    def test_daily_cap_counts_only_created_articles_today(self):
        now = datetime(2026, 11, 4, 15, tzinfo=JST)
        today = now.astimezone(timezone.utc).isoformat()
        yesterday = (now - timedelta(days=1)).astimezone(timezone.utc).isoformat()
        state = {"processed_web": {
            "a": {"at": today, "slug": "x", "publicInterest": "breach"},
            "b": {"at": today, "skipped": True, "publicInterest": "breach"},
            "c": {"at": yesterday, "slug": "y", "publicInterest": "missile"},
            "d": {"at": today, "slug": "z"},
        }}
        self.assertEqual(pi.done_today(state, now), 1)


class SelectionTest(unittest.TestCase):
    """web_articles の選び方: 公共性の候補は大きなニュースとして先に作り、AI のフィード・速報レーンは対象外"""

    def test_pick_and_purpose(self):
        import web_articles as w

        item = {"title": "北朝鮮のミサイル等関連情報（続報）について", "summary": "", "url": "https://www.mod.go.jp/j/press/news/2026/10/29d.html",
                "source_slug": "mod-news", "published_at": datetime.now(timezone.utc)}
        self.assertEqual(w.public_interest_pick(dict(item), 4), "missile")
        self.assertIsNone(w.public_interest_pick(dict(item), 0), "1日の上限に達したら選ばない")
        self.assertIsNone(w.public_interest_pick(dict(item, source_slug="openai-news"), 4), "AI のフィードは対象外")
        self.assertIsNone(w.public_interest_pick(dict(item, fast=True), 4), "速報レーンは別の経路（W31 の門）")
        picked = dict(item, _public_interest="missile")
        self.assertEqual(w.article_purpose(picked, "public", major=True), "breaking")
        breach = {"title": "不正アクセスによる個人情報流出の可能性について", "summary": "", "url": "https://www.sumitomo-rd.co.jp/news/x",
                  "source_slug": "sumitomo-rd", "published_at": datetime.now(timezone.utc), "_public_interest": "breach"}
        self.assertEqual(w.article_purpose(breach, "real-estate", major=True), "sns", "流出は速報の段を使わない（予算）")

    def test_trend_signal_moves_it_first(self):
        import web_articles as w

        item = {"title": "ビックカメラ 不正アクセスによる個人情報流出の可能性について", "summary": "", "url": "https://www.biccamera.co.jp/x",
                "source_slug": "sumitomo-rd", "published_at": datetime.now(timezone.utc)}
        self.assertEqual(w.public_interest_pick(item, 4, [{"title": "ビックカメラ", "traffic": 20000}]), "breach")
        self.assertEqual(item.get("_trend_hit"), "ビックカメラ")

    def test_new_official_feeds_are_registered(self):
        import feeds

        mod = feeds.FEEDS_BY_SLUG["mod-news"]
        self.assertEqual((mod["kind"], mod["section"]), ("gov", "public"))
        self.assertEqual(feeds.FEEDS_BY_SLUG["npa-new"]["kind"], "gov")

    def test_reopen_is_once_and_recent_only(self):
        import web_articles as w

        now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
        state = {"processed_web": {
            "a": {"at": (now - timedelta(hours=3)).isoformat(), "skipped": True, "reason": "prefilter: サイトの主題外（…）"},
            "b": {"at": (now - timedelta(hours=60)).isoformat(), "skipped": True, "reason": "prefilter: サイトの主題外（…）"},
            "c": {"at": (now - timedelta(hours=3)).isoformat(), "skipped": True, "reason": "quality: 薄い"},
        }}
        self.assertEqual(w.reopen_public_interest(state, now), 1)
        self.assertNotIn("a", state["processed_web"])
        state["processed_web"]["a"] = {"at": now.isoformat(), "skipped": True, "reason": "prefilter: サイトの主題外（…）"}
        self.assertEqual(w.reopen_public_interest(state, now), 0, "同じ候補は1回だけ見直す")


class SensitiveGateTest(unittest.TestCase):
    """A39: どの経路の記事にも当てる門（quality.every_path_problems から呼ぶ）"""

    def rec(self, **kw):
        base = {"title": "北朝鮮が弾道ミサイルの可能性があるものを発射、EEZ外に落下と推定", "lede": "防衛省によると、北朝鮮が発射した。",
                "body": "## 発射の概要\n\n防衛省によると、北朝鮮は12時36分頃に発射した。被害の報告は現時点で確認されていない。\n\n"
                        "[防衛省](https://www.mod.go.jp/j/press/news/index.html)",
                "easy": "", "genreSlug": "public", "publicInterest": "missile"}
        return {**base, **kw}

    def test_clean_record_passes(self):
        self.assertEqual(pi.sensitive_problems(self.rec()), [])

    def test_sensational_speculation_and_names_are_rejected(self):
        self.assertTrue(pi.sensitive_problems(self.rec(title="衝撃、北朝鮮がミサイル発射")))
        self.assertTrue(pi.sensitive_problems(self.rec(body=self.rec()["body"] + "\n\nおそらく新型だと思われる。")))
        any_article = {"title": "A社の発表", "lede": "", "body": "山田太郎容疑者（34）が…", "easy": "", "genreSlug": "business"}
        self.assertTrue(pi.sensitive_problems(any_article), "私人の実名はどの記事でも止める")

    def test_unknowns_and_safety_links_are_required(self):
        body = "## 発射の概要\n\n防衛省によると、北朝鮮は12時36分頃に発射した。"
        problems = " ".join(pi.sensitive_problems(self.rec(body=body)))
        self.assertIn("分かっていない", problems)
        self.assertIn("安全情報", problems)

    def test_autofix_inserts_official_safety_links_once(self):
        rec = self.rec(body="## 発射の概要\n\n現時点で被害は確認されていません。")
        self.assertTrue(pi.autofix(rec))
        self.assertIn("kokuminhogo.go.jp", rec["body"])
        self.assertTrue(rec["body"].startswith("> 安全に関する公式情報"))
        self.assertEqual(pi.autofix(rec), [], "2回目は何もしない")
        self.assertEqual(pi.sensitive_problems(rec), [])

    def test_political_opinion_is_rejected(self):
        rec = {"title": "政府が物価高対策を決定", "lede": "", "body": "自民党の案は愚策だ。", "easy": "", "genreSlug": "public"}
        self.assertTrue(any("中立" in p for p in pi.sensitive_problems(rec)))

    def test_product_articles_are_not_caught(self):
        rec = {"title": "Microsoft、ランサムウェア対策の新機能を提供開始", "lede": "", "body": "## 新機能\n\n新しい機能が使える。", "easy": "",
               "genreSlug": "ai"}
        self.assertEqual(pi.sensitive_problems(rec), [])

    def test_every_writing_path_uses_this_gate(self):
        import quality

        bad = self.rec(title="衝撃、北朝鮮がミサイル発射")
        self.assertTrue(any("煽る" in p for p in quality.every_path_problems(bad)))
        # 書き換える段（続報・見出しの書き直し）は新しく増えた分だけ
        self.assertTrue(quality.added_problems(self.rec(), bad))
        self.assertEqual(quality.added_problems(bad, dict(bad)), [])
        # 書き出す前の手直しと書き出しの段で、安全情報のリンクが入る
        for path, needle in (("scripts/quality.py", "public_interest.autofix(record)"), ("scripts/main.py", "public_interest.autofix(record)")):
            self.assertIn(needle, (ROOT / path).read_text("utf-8"), path)


class FocusTest(unittest.TestCase):
    """検索に出す主題（indexnow.is_focus と lib/indexing.ts）に公共性の話題が入る"""

    def test_focus_and_publish_gate(self):
        import indexnow

        art = {"title": "防衛省、北朝鮮の弾道ミサイル発射を発表", "genreSlug": "public", "tags": []}
        self.assertTrue(indexnow.is_focus(art))
        self.assertTrue(indexnow.is_focus({"title": "消費者庁、充電式スピーカーのリコール製品で火災", "genreSlug": "lifestyle"}))
        self.assertFalse(indexnow.is_focus({"title": "都庁でイルミネーション点灯", "genreSlug": "public"}))
        self.assertFalse(indexnow.candidate_off_focus("public", "不正アクセスによる情報漏えい等に関する注意喚起"))

    def test_site_rules_carry_the_same_regex(self):
        rules = json.loads((ROOT / "lib" / "index-rules.json").read_text("utf-8"))
        self.assertEqual(rules["publicInterestTopic"]["source"], pi.FOCUS_TOPIC_RE.pattern)
        self.assertEqual(rules["publicInterestGenres"], list(pi.FOCUS_GENRES))
        self.assertIn("isPublicInterest(article)", (ROOT / "lib" / "indexing.ts").read_text("utf-8"))


class TrendSignalTest(unittest.TestCase):
    def test_signals_are_saved_and_not_rewritten_when_same(self):
        import trends

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trend_signals.json"
            raw = [{"title": "ビックカメラ", "traffic": 20000, "news": [{"title": "報道の見出し"}]}]
            self.assertEqual(trends.save_signals(raw, path), 1)
            before = path.stat().st_mtime_ns
            trends.save_signals(raw, path)
            self.assertEqual(path.stat().st_mtime_ns, before)
            self.assertEqual(pi.load_signals(path)[0]["title"], "ビックカメラ")


if __name__ == "__main__":
    unittest.main()


class PressSourceGateTest(unittest.TestCase):
    """W31 をすべての経路に: 報道機関・ニュースブログ・話題の報道まとめを出典にした記事は書かない・公開しない（2026-11-04。
    ゴリミーの記事を出典にした記事が2本公開されていた）"""

    def rec(self, **kw):
        return {"title": "Apple、Mac整備済製品に24モデル追加", "lede": "", "body": "## 追加\n\n本文。", "easy": "", "genreSlug": "tech-gadget", **kw}

    def test_outlet_sources_are_rejected_on_every_path(self):
        import indexnow
        import quality

        for kw in ({"channelSlug": "radar-gori.me", "sourceUrl": "https://gori.me/sale/apple-refurbished/172065"},
                   {"channelSlug": "fast", "sourceUrl": "https://www.bloomberg.com/news/articles/2026-11-02/x"},
                   {"channelSlug": "trends", "sourceUrl": "https://www.nytimes.com/2026/10/28/x"}):
            bad = self.rec(**kw)
            self.assertTrue(pi.press_source_problems(bad), kw)
            self.assertTrue(any("W31" in p for p in quality.every_path_problems(bad)), kw)
            self.assertEqual(indexnow.publish_block_reason(bad), "press", kw)

    def test_official_sources_pass(self):
        for kw in ({"channelSlug": "radar-official", "sourceUrl": "https://www.apple.com/jp/newsroom/2026/11/x/"},
                   {"channelSlug": "mod-news", "sourceUrl": "https://www.mod.go.jp/j/press/news/2026/10/29d.html"},
                   {"channelSlug": "tsr", "sourceUrl": "https://www.tsr-net.co.jp/data/detail/x.html"}):  # A37 は札で扱う
            self.assertEqual(pi.press_source_problems(self.rec(**kw)), [], kw)

    def test_fast_lane_skips_outlet_urls_before_writing(self):
        import web_articles as w

        state = {"processed_web": {}, "major_tries": {}, "failed_web": {}}
        meta = json.dumps([{"url": "https://gori.me/macbookpro/172049", "title": "Apple、iPadOSとmacOSの統合を否定", "via": "radar"}])
        items, results = w.build_fast_items(["https://gori.me/macbookpro/172049"], meta, state, datetime.now(timezone.utc))
        self.assertEqual(items, [])
        self.assertTrue(all(r["outcome"] == "skipped" for r in results.values()), results)

    def test_trend_lane_is_off_by_default(self):
        import web_articles as w

        self.assertFalse(w.TREND_FROM_PRESS)


class TuningTest(unittest.TestCase):
    """選び方の重みは scripts/public_interest_rules.json で変えられる（2026-11-04 オーナー: 後から調整しやすく）"""

    def test_rules_file_is_read_and_broken_file_falls_back(self):
        rules = json.loads((ROOT / "scripts" / "public_interest_rules.json").read_text("utf-8"))
        self.assertEqual(pi.PRIORITY, rules["priority"])
        self.assertEqual(pi.BREAKING_CATEGORIES, tuple(rules["breaking"]))
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "r.json"
            bad.write_text("{壊れた", "utf-8")
            self.assertEqual(pi.load_rules(bad)["daily_cap"], pi.DEFAULT_RULES["daily_cap"])

    def test_short_missile_article_is_not_thin_but_others_are(self):
        import indexnow

        body = "## 発射の概要\n\n" + "あ" * 900
        missile = {"title": "北朝鮮が弾道ミサイルを発射", "genreSlug": "public", "body": body, "verdict": {"x": 1}}
        breach = {"title": "不正アクセスによる情報流出の可能性", "genreSlug": "public", "body": body, "verdict": {"x": 1}}
        self.assertNotEqual(indexnow.noindex_reason(missile), "thin")
        self.assertEqual(indexnow.noindex_reason(breach), "thin")
        rules = json.loads((ROOT / "lib" / "index-rules.json").read_text("utf-8"))
        self.assertEqual(rules["thinBodyPublicInterest"], pi.SHORT_BODY)
