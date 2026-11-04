"""オーナーのルールの毎日の点検（scripts/owner_rules_audit.py）の判定のテスト。本番は開かない。"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import owner_rules_audit as audit  # noqa: E402

LONG_BODY = "## 何が発表された？\n\n" + "トヨタ自動車は新しい安全機能を発表しました。対象は2027年モデルです。" * 60


def rec(**kw):
    base = {"slug": "x", "title": "トヨタ、新しい安全機能を2027年モデルに搭載", "body": LONG_BODY, "easy": "## ひとことで言うと\n\n安全機能が増えます。",
            "faq": [{"q": "いつから？", "a": "2027年モデルからです。"}], "channelSlug": "toyota"}
    base.update(kw)
    return base


def ids(lines):
    return {re.match(r"\[([A-Z]+\d+)\]", x).group(1) for x in lines}


class TitleTest(unittest.TestCase):
    def test_forced_descriptor_for_famous_company(self):
        self.assertIn("A5", ids(audit.title_problems("AI半導体のNVIDIAが学習の自動復旧ツール公開")))
        self.assertIn("A5", ids(audit.title_problems("ChatGPTのOpenAIが新モデル　料金は半分")))
        self.assertEqual([], audit.title_problems("NVIDIAが学習の自動復旧ツール公開　止まったAI開発を再開"))
        self.assertEqual([], audit.title_problems("Alphabet傘下のGoogleが新サービス"))

    def test_odd_punctuation(self):
        self.assertIn("A5", ids(audit.title_problems("法人向け「セキュアSAMBA」、Mac用アプリ提供開始")))
        self.assertIn("A5", ids(audit.title_problems("新サービス開始。料金は無料")))
        self.assertIn("A5", ids(audit.title_problems("新サービスが登場！")))
        self.assertEqual([], audit.title_problems("Canvaがデザインアプリ「Affinity」iPad版を公開　写真編集も1本に"))


class RecordTest(unittest.TestCase):
    def test_clean_article_passes(self):
        self.assertEqual([], audit.record_problems(rec()))

    def test_template_headings(self):
        for h in ("自分に関係ある？", "もう使える？取引できる？", "何がすごいの？"):
            self.assertIn("A1", ids(audit.record_problems(rec(easy=f"## ひとことで言うと\n\n増えます。\n\n## {h}\n\nトヨタは2027年モデルから搭載します。"))), h)
        # 型の小見出しは「幼い言い回し」と2回数えない
        found = audit.record_problems(rec(easy="## 何がすごいの？\n\n説明です。"))
        self.assertNotIn("A4", ids(found))
        # 読者に見える形で数える: 材料の無い比較の章は表示で隠れる・記事ごとの見出しがあれば言い換えて出る
        hidden = rec(easy="## ひとことで言うと\n\n増えます。\n\n## ライバルと比べてどう？\n\n元記事には、他社製品との比較は書かれていません。")
        self.assertNotIn("A1", ids(audit.record_problems(hidden)))
        titled = rec(easy="## ひとことで言うと\n\n増えます。\n\n## 自分に関係ある？\n\nトヨタは2027年モデルから搭載します。",
                     sectionTitles={"easy": {"自分に関係ある？": "対象になる車"}})
        self.assertNotIn("A1", ids(audit.record_problems(titled)))
        # 本番のページを読めたときは、小見出しはページの見出しで数える（article_page_problems）
        self.assertNotIn("A1", ids(audit.record_problems(rec(easy="## 自分に関係ある？\n\nトヨタは2027年モデルから搭載します。"), page_headings=True)))

    def test_non_answer_filler_childish(self):
        # 本文の「元記事には〜書かれていません」は表示で文ごと消える（lib/article-sections.ts）。読者に見える欄に残れば数える
        self.assertNotIn("A2", ids(audit.record_problems(rec(body=LONG_BODY + "\n\n元記事には、他社との比較は書かれていません。"))))
        self.assertIn("A2", ids(audit.record_problems(rec(lede="料金は元記事には書かれていません。"))))
        self.assertIn("A2", ids(audit.record_problems(rec(description="元記事だけでは判断できません。"))))
        self.assertNotIn("A2", ids(audit.record_problems(rec(faq=[{"q": "料金は？", "a": "公式サイトで確認してください（元記事では価格情報は言及なし）。"}]))))
        self.assertIn("A3", ids(audit.record_problems(rec(lede="会社員にとっても重要です。"))))
        self.assertIn("A3", ids(audit.record_problems(rec(lede="大きな一歩といえます。"))))
        self.assertIn("A4", ids(audit.record_problems(rec(lede="これはすごい機能です。"))))
        self.assertIn("A4", ids(audit.record_problems(rec(lede="ざっくり言うと便利です。"))))

    def test_calling_out_register(self):
        # 判定のひとこと・本文の「〜してみましょう」は表示で「〜してみるとよいでしょう」になる。言い換えない欄に残れば数える（A17）
        self.assertNotIn("A17", ids(audit.record_problems(rec(verdict={"status": "available", "take": "公式サイトで試してみましょう。"}))))
        self.assertNotIn("A17", ids(audit.record_problems(rec(body=LONG_BODY + "\n\nまずは無料版を試してみましょう。"))))
        self.assertIn("A17", ids(audit.record_problems(rec(lede="公式サイトで試してみましょう。"))))
        self.assertNotIn("A17", ids(audit.record_problems(rec(lede="「一緒に試しましょう」と呼びかけました。"))))

    def test_low_value_types(self):
        self.assertIn("A10", ids(audit.record_problems(rec(body="短い。", faq=[]))))

    def test_news_type_template_after_cutover(self):
        # A18・A19（2026-11-03 オーナー決定: 種類ごとの型・リードで新しさを言い切る）。切り替えより後の Web 記事だけを見る
        new = dict(sourceType="web", generatedAt="2026-11-04T00:00:00+00:00")
        found = ids(audit.record_problems(rec(**new)))
        self.assertIn("A18", found)  # newsType が無い
        many = "\n\n".join(f"## 見出し{i}\n\n" + "事実の文です。" * 20 for i in range(6))
        found = ids(audit.record_problems(rec(newsType="product", body=many, lede="トヨタは新しい安全機能の概要を発表しました。", **new)))
        self.assertTrue({"A18", "A19"} <= found, found)
        good = "安全機能は2027年モデルの全車に付きます。" * 40 + "\n\n## 日本の販売店で選べる\n\n" + "価格は据え置きです。" * 40
        self.assertFalse({"A18", "A19"} & ids(audit.record_problems(rec(newsType="product", body=good, lede="トヨタの2027年モデルに新しい安全機能が付きます。価格は据え置きです。", **new))))
        # 切り替えより前の記事は見ない
        self.assertNotIn("A18", ids(audit.record_problems(rec(sourceType="web", generatedAt="2026-11-01T00:00:00+00:00"))))


class PageTest(unittest.TestCase):
    AD = '<a rel="sponsored nofollow noopener noreferrer" href="#">PR</a>'

    def test_labels_ads_ai_owner(self):
        html = f"<h1>題名</h1><h2>3秒でわかる要点</h2><p>本文</p>{self.AD}"
        self.assertEqual({"A6"}, ids(audit.article_page_problems(html, rec())))
        self.assertIn("A11", ids(audit.article_page_problems("<h1>題名</h1>", rec())))
        self.assertIn("I4", ids(audit.article_page_problems(f"<h1>題名</h1>{self.AD}", rec(imageCredit={"kind": "ai"}))))
        self.assertNotIn("I4", ids(audit.article_page_problems(f"<h1>題名</h1><span>AI生成</span>{self.AD}", rec(imageCredit={"kind": "ai"}))))
        self.assertIn("W6", ids(audit.article_page_problems(f"<p>小室</p>{self.AD}", rec())))

    def test_component_headings_counted_on_the_page(self):
        html = f"<h2>自分に関係ある？</h2>{self.AD}"
        self.assertIn("A1", ids(audit.article_page_problems(html, rec())))
        self.assertNotIn("A1", ids(audit.article_page_problems(f"<h2>影響を受ける人</h2>{self.AD}", rec(easy="## 自分に関係ある？\n\n説明です。"))))

    def test_persona_subheads_and_nonanswer_titles(self):
        """A24: 立場の小見出しと、見出しの言葉だけ変えた中身なしの章（2026-11-03 オーナー決定）"""
        self.assertIn("A24", ids(audit.article_page_problems(f"<h3>会社員として</h3><p>説明</p>{self.AD}", rec())))
        self.assertIn("A24", ids(audit.article_page_problems(f"<p><strong>副業・独立を考えている人</strong></p>{self.AD}", rec())))
        self.assertIn("A24", ids(audit.article_page_problems(f"<h2>日本向けの提供条件はまだ分からない</h2>{self.AD}", rec())))
        self.assertNotIn("A24", ids(audit.article_page_problems(f"<h2>まだ分からないこと</h2><h2>影響を受ける人</h2>{self.AD}", rec())))
        # JSON だけで見るとき: 表示の段で消えるので、整えたあとには残らない
        body = "## 自分にはどう関係する？\n\n### 会社員として\n\n社内でPSDを開けます。\n\n## 数字で見ると？\n\n比べられる数字は示されていません。\n"
        r = rec(body=body, sectionTitles={"body": {"数字で見ると？": "比べられる数字は示されていない"}})
        self.assertNotIn("A24", ids(audit.record_problems(r)))

    def test_summary_titles_pass_headline_gate(self):
        """A25: 朝のまとめ・週のまとめの題名（2026-11-03 オーナー決定）"""
        bad = '<a data-title-gate="" href="/a">ChatGPTのOpenAIが電力対策</a><a href="/b" data-title-gate="">Mistral Large 4とは？日本で使える？料金も</a>'
        self.assertEqual({"A25"}, ids(audit.summary_page_problems("/weekly/2026-44", bad)))
        self.assertEqual(2, len(audit.summary_page_problems("/weekly/2026-44", bad)))
        good = '<a data-title-gate="" href="/a">OpenAI、電力対策「Grid Protectors」を発表</a>'
        self.assertEqual([], audit.summary_page_problems("/digest/20261103", good))
        self.assertIn("A25", ids(audit.summary_page_problems("/digest/20261103", "<p>題名なし</p>")))

    def test_one_slot_each_and_no_data(self):
        twice = f'<aside data-aff-pos="ranking-inline">{self.AD}</aside><aside data-aff-pos="ranking-inline"></aside>'
        self.assertIn("W10", ids(audit.article_page_problems(twice, rec())))
        signup = f'{self.AD}<section data-newsletter-signup="article"></section><section data-newsletter-signup="footer"></section>'
        self.assertIn("W10", ids(audit.article_page_problems(signup, rec())))
        once = f'<aside data-aff-pos="ranking-inline">{self.AD}</aside><aside data-aff-pos="ranking-rail"></aside><section data-newsletter-signup="article"></section>'
        self.assertNotIn("W10", ids(audit.article_page_problems(once, rec())))
        self.assertIn("W11", ids(audit.article_page_problems(f"<table><tr><td>記載なし</td></tr></table>{self.AD}", rec())))
        self.assertIn("W11", ids(audit.static_page_problems("/ai-pricing", "<p>税：ページに税の記載なし</p>")))
        self.assertIn("W10", ids(audit.static_page_problems("/newsletter", '<section data-newsletter-signup="page"></section><section data-newsletter-signup="footer"></section>')))

    def test_static_pages(self):
        ok = f"<p>{audit.CONTACT_EMAIL}</p><p>代表 小室</p>"
        self.assertEqual([], audit.static_page_problems("/about", ok))
        self.assertIn("W5", ids(audit.static_page_problems("/contact", "<p>お問い合わせ</p>")))
        self.assertIn("W5", ids(audit.static_page_problems("/contact", f"<p>{audit.CONTACT_EMAIL} owner@gmail.com</p>")))
        self.assertIn("W6", ids(audit.static_page_problems("/", "<p>小室</p>")))
        self.assertIn("A12", ids(audit.static_page_problems("/editorial-policy", "<p>全記事を編集部が確認しています</p>")))


class SnsTest(unittest.TestCase):
    def test_rule_ids_and_one_post_per_article(self):
        def judge(post):
            return post.get("_v", [])

        posts = [
            {"platform": "bluesky", "url": "u1", "text": "https://ai-tech-express.jp/articles/a", "_v": ["[ルール6] 1行目が読者への呼びかけになっていない"]},
            {"platform": "bluesky", "url": "u2", "text": "https://ai-tech-express.jp/articles/a", "_v": ["[ルール6] 速報ではない記事に【速報】を付けている"]},
            {"platform": "bluesky", "url": "u3", "text": "x", "_v": ["[ルール6] 「発表からN分で掲載」の書き出し", "[ルール7] 要約の文が「です・ます」になっていない",
                                                                     "[ルール1] 本文に記事のリンクがない", "[ルール2] 画像が 16:9 でない", "[ルール3] 「ざっくり」"]},
            {"platform": "bluesky", "url": "u4", "text": "y", "reply": True, "_v": ["[ルール1] x"]},
        ]
        self.assertEqual({"S1", "S2", "S3", "S4", "S6", "S7", "S8", "S10"}, ids(audit.sns_problems(posts, judge)))

    def test_comment_hours(self):
        self.assertFalse(audit.comment_time_ok("10:19"))
        self.assertTrue(audit.comment_time_ok("10:20"))
        self.assertTrue(audit.comment_time_ok("22:59"))
        self.assertFalse(audit.comment_time_ok("23:00"))
        self.assertEqual(1, len(audit.comment_log_problems([{"date": "2026-11-04", "time": "09:50"}, {"date": "2026-11-04", "time": "12:00"}])))


class CoverageTest(unittest.TestCase):
    def test_every_audit_rule_can_be_reported(self):
        src = Path(audit.__file__).read_text("utf-8")
        for rid in audit.AUDIT_RULES:
            self.assertIn(f"[{rid}]", src, rid)



class ProductHubTest(unittest.TestCase):
    """W12・W13（2026-11-03）: 図鑑・ハブのページに関係のない写真・決まった型の見出し・「元記事には」を出さない。一覧に載せた製品ページは中身があり検索に出す。終了したツールは 410"""

    def test_no_article_or_stock_images_on_products(self):
        bad = '<img src="https://ai-tech-express.jp/img/openai-bug-bounty.sm.webp"><h2>料金プラン</h2>'
        self.assertIn("W13", ids(audit.hub_page_problems("/products", bad)))
        self.assertIn("W13", ids(audit.hub_page_problems("/products/codex", '<img src="/cover/codex-news?v=3">')))
        self.assertIn("W13", ids(audit.hub_page_problems("/products/chatgpt-work", '<img src="https://images.unsplash.com/photo-1">')))
        self.assertEqual([], audit.hub_page_problems("/products", '<span data-product-visual="placeholder"><svg></svg></span><h1>AIサービス図鑑</h1>'))
        # 公式プレスキットのロゴ（KV の /img/brand-<名前>）は使ってよい
        self.assertEqual([], audit.hub_page_problems("/products/claude", '<img src="https://ai-tech-express.jp/img/brand-claude-icon-0ea91b42.jpg">'))

    def test_template_headings_and_non_answers(self):
        self.assertIn("W13", ids(audit.hub_page_problems("/products/codex", "<h2>何ができる？</h2>")))
        self.assertIn("W13", ids(audit.hub_page_problems("/products/codex", "<p>元記事には料金の記載がありません。</p>")))
        self.assertIn("W11", ids(audit.hub_page_problems("/products/codex", "<dd>記載なし</dd>")))
        self.assertEqual([], audit.hub_page_problems("/products/codex", "<h2>主な機能・仕様</h2><h2>料金プラン</h2>"))

    def test_listed_product_must_have_substance_and_be_indexed(self):
        thin = "<h1>Codex</h1><h2>関連記事</h2>"
        self.assertIn("W13", ids(audit.listed_product_problems("/products/codex", thin)))
        full = "<h2>料金プラン</h2><h2>日本での提供状況</h2><h2>主な機能・仕様</h2><h2>発表・更新の履歴</h2>"
        self.assertEqual([], audit.listed_product_problems("/products/codex", full))
        self.assertIn("W13", ids(audit.listed_product_problems("/products/codex", '<meta name="robots" content="noindex, follow">' + full)))
        self.assertEqual(["/products/codex", "/products/gpt-6-astra"], audit.product_links('<a href="/products/codex">a</a><a href="/products/gpt-6-astra">b</a><a href="/products/codex">c</a>'))

    def test_retired_usd_jpy_is_not_linked(self):
        self.assertIn("W12", ids(audit.hub_page_problems("/guides", '<a href="/tools/usd-jpy">円換算</a>')))
        self.assertIn("/tools/usd-jpy", audit.RETIRED_TOOL_PAGES)


class SourcePageTest(unittest.TestCase):
    """W17・W18（2026-11-03 オーナー決定 2・9）: 報道機関の発信元ページを「公式発表」と呼ばない・検索に出さない。選び方ガイドに別の種類の決まり文句・「〜しましょう」を出さない"""

    NOINDEX = '<meta name="robots" content="noindex, follow">'

    def test_outlet_channel(self):
        self.assertTrue(audit.outlet_channel("radar-itmedia.co.jp"))
        self.assertTrue(audit.outlet_channel("trends"))
        self.assertFalse(audit.outlet_channel("radar-official"))
        self.assertFalse(audit.outlet_channel("openai"))

    def test_source_page(self):
        bad = '<meta name="description" content="ITmedia の公式発表をAIが解析"><main><p>ITmedia で公開された公式ブログ・プレスリリース</p></main>'
        self.assertEqual({"W17"}, ids(audit.source_page_problems("/channels/radar-itmedia.co.jp", bad)))
        self.assertEqual(3, len(audit.source_page_problems("/channels/radar-itmedia.co.jp", bad)))
        ok = f'{self.NOINDEX}<meta name="description" content="ITmediaの報道をもとにした記事"><main><p>ITmediaの報道をもとにした記事 3 本です。</p></main>'
        self.assertEqual([], audit.source_page_problems("/channels/radar-itmedia.co.jp", ok))
        # 各社の公式発表が混ざる radar-official は「公式発表」と書いてよいが、noindex にする
        self.assertEqual([], audit.source_page_problems("/channels/radar-official", f"{self.NOINDEX}<main><p>公式発表</p></main>"))
        self.assertIn("W17", ids(audit.source_page_problems("/channels/radar-official", "<main><p>公式発表</p></main>")))

    def test_choose_page(self):
        self.assertIn("W18", ids(audit.choose_page_problems("/choose/ai-career", "<p>速度はすべて公称値（規格上の最大値）です。</p>")))
        self.assertIn("W18", ids(audit.choose_page_problems("/choose/ai-career", "<p>登録前に確認しましょう。</p>")))
        self.assertEqual([], audit.choose_page_problems("/choose/ai-career", "<p>登録前に確認しておくと安心です。</p>"))

class PricingGoneLiveTest(unittest.TestCase):
    """W21・W22・W23（2026-11-03）: AI料金表の為替は1つ・出典は公式／410 ページはサイトの見た目／帯の「速報」は本当の速報だけ"""

    def test_pricing_page_one_fx_and_official_source(self):
        good = "<p>1ドル＝157.90円（2026/11/02・ECB参照レート）</p><a>公式：Claude 料金ページ（11/3確認）</a><p>1ドル＝157.90円で計算</p>"
        self.assertEqual([], audit.pricing_page_problems(good))
        self.assertIn("W21", ids(audit.pricing_page_problems("<p>1ドル＝150円で計算</p><p>1ドル＝157.90円</p><a>公式：x</a>")))
        self.assertIn("W21", ids(audit.pricing_page_problems("<p>1ドル＝157.90円</p><span>9/25の記事で確認</span>")))
        self.assertIn("W21", ids(audit.pricing_page_problems("<p>公式：x</p>")))

    def test_gone_page_has_site_layout(self):
        good = '<header class="site-header"></header><div data-gone-page><a href="/articles/gemini-4">x</a></div><footer></footer>'
        self.assertEqual([], audit.gone_page_problems(410, good))
        self.assertIn("W22", ids(audit.gone_page_problems(200, good)))
        self.assertIn("W22", ids(audit.gone_page_problems(410, "<main><h1>この記事は取り下げました</h1></main>")))
        self.assertTrue(all(p.startswith("/articles/") for p in audit.retired_sample()))

    def test_live_line_red_only_for_breaking(self):
        html = '<div class="live-line"><div><span data-live-label="速報" class="bg-live">速報</span><a href="/articles/samba">kubell</a></div></div>'
        self.assertIn("W23", ids(audit.live_line_problems(html, {"samba": {"slug": "samba"}})))
        self.assertEqual([], audit.live_line_problems(html, {"samba": {"slug": "samba", "breaking": True}}))
        latest = html.replace('data-live-label="速報" class="bg-live">速報', 'data-live-label="最新" class="bg-accent">最新')
        self.assertEqual([], audit.live_line_problems(latest, {"samba": {"slug": "samba"}}))
        # 前の作り（決め打ちの「速報」）も見つける
        old = '<div class="live-line"><div><span class="bg-live">速報</span><a href="/articles/samba">kubell</a></div></div>'
        self.assertIn("W23", ids(audit.live_line_problems(old, {"samba": {"slug": "samba"}})))

class RelatedAndAdBoxTest(unittest.TestCase):
    """W19・W20（2026-11-03）: 関連記事に地震の記事を混ぜない。広告に「WEB SERVICE」の板を出さない"""

    def test_quake_link_in_related_is_reported(self):
        page = '<section aria-labelledby="related-heading"><a href="/articles/quake-20261029000759">【地震】</a></section>'
        self.assertIn("W19", ids(audit.related_problems(page, {"slug": "nenkin-no-hi-2026-mhlw", "title": "年金の日"})))
        nxt = '<section><h2 id="next-read-heading">次に読む</h2><a href="/articles/quake-1">x</a></section>'
        self.assertIn("W19", ids(audit.related_problems(nxt, {"slug": "a", "title": "b"})))
        # 地震の記事どうしは問題にしない。関連の欄の外（本文など）のリンクも数えない
        self.assertEqual(audit.related_problems(page, {"slug": "quake-2", "title": "【地震】x", "channelSlug": "jma"}), [])
        self.assertEqual(audit.related_problems('<p><a href="/articles/quake-1">x</a></p>', {"slug": "a", "title": "b"}), [])

    def test_web_service_board_is_reported(self):
        html = '<main><a rel="sponsored nofollow">x</a><span>WEB SERVICE</span></main>'
        self.assertIn("W20", ids(audit.article_page_problems(html, {"slug": "a", "title": "b"})))


class NoticeTest(unittest.TestCase):
    """A26（2026-11-03 オーナー決定 1）: お知らせ・告知・統計の転載の記事を残さない（直近7日に SNS に出したものは除く）"""

    def test_notice_flagged_unless_recent_sns(self):
        record = {"slug": "tobacco", "title": "財務省、製造たばこの小売定価の認可情報を随時公開"}
        self.assertEqual({"A26"}, ids(audit.notice_problems(record, set())))
        self.assertEqual([], audit.notice_problems(record, {"tobacco"}))
        self.assertEqual([], audit.notice_problems({"slug": "a", "title": "Apple、7〜9月期の売上高が12％増"}, set()))



class RetireOwnerDecisionTest(unittest.TestCase):
    """2026-11-04 オーナー決定: D3 他人の動画の要約は残さない（台帳 A10）・D5 お知らせ型は SNS／公開から7日で毎週自動で取り下げる（台帳 A26）"""

    TOBACCO = "財務省、製造たばこの小売定価の認可情報を随時公開"

    def setUp(self):
        import retire_articles  # noqa: PLC0415

        self.r = retire_articles
        self._posts = retire_articles.sns_posts

    def tearDown(self):
        self.r.sns_posts = self._posts

    def test_notice_kept_for_seven_days_from_sns_or_publish(self):
        from datetime import datetime, timedelta, timezone  # noqa: PLC0415

        now = datetime(2026, 11, 20, tzinfo=timezone.utc)
        iso = lambda days: (now - timedelta(days=days)).isoformat()  # noqa: E731
        arts = [
            {"slug": "posted-3d", "title": self.TOBACCO, "publishedAt": iso(30)},
            {"slug": "posted-8d", "title": self.TOBACCO, "publishedAt": iso(30)},
            {"slug": "fresh", "title": self.TOBACCO, "publishedAt": iso(2)},
            {"slug": "old", "title": self.TOBACCO, "publishedAt": iso(10)},
            {"slug": "news", "title": "Apple、7〜9月期の売上高が12％増", "publishedAt": iso(30)},
        ]
        self.r.sns_posts = lambda everywhere=False: {"posted-3d": now - timedelta(days=3), "posted-8d": now - timedelta(days=8)}
        picked, kept = self.r.plan_notice(arts, now)
        self.assertEqual(set(picked), {"posted-8d", "old"})
        self.assertEqual({k["slug"] for k in kept}, {"posted-3d", "fresh"})
        # 毎日の点検は、週1回の自動の取り下げが動かずに過ぎたもの（7日＋8日）だけを違反にする
        self.assertEqual([], audit.notice_problems({"slug": "x", "title": self.TOBACCO, "publishedAt": (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()}, {}))
        self.assertEqual({"A26"}, ids(audit.notice_problems({"slug": "x", "title": self.TOBACCO, "publishedAt": (datetime.now(timezone.utc) - timedelta(days=16)).isoformat()}, {})))

    def test_weekly_workflow_retires_and_deploys(self):
        wf = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "notice_retire.yml").read_text("utf-8")
        self.assertRegex(wf, r'cron: "\d+ \d+ \* \* \d"')
        self.assertIn("scripts/retire_articles.py --notice --apply", wf)
        self.assertIn("scripts/git_safe_push.py", wf)
        self.assertIn("gh workflow run deploy_cloudflare.yml", wf)
        self.assertNotRegex(wf, r"ANTHROPIC_API_KEY|BUFFER|threads_post|post_now", "LLM も SNS も使わない")

    def test_creator_videos_are_all_retired(self):
        import indexnow  # noqa: PLC0415

        arts = [{"slug": "a", "channelSlug": "two-minute-papers", "title": "t"}, {"slug": "b", "channelSlug": "openai", "title": "t"}]
        self.assertEqual(set(self.r.plan_creator(arts)), {"a"})
        self.assertEqual({"A10"}, ids(audit.creator_video_problems(arts[0])))
        self.assertEqual([], audit.creator_video_problems(arts[1]))
        live = [a for a in self.r.load_articles() if a.get("channelSlug") in indexnow.CREATOR_CHANNELS]
        self.assertEqual([], [a["slug"] for a in live], "他人の動画の要約の記事が残っている（retire_articles.py --creator --apply）")


class TitleTagTest(unittest.TestCase):
    """A29: 題名は1つ（<title>・og:title・twitter:title が見出し h1 と同じ）"""

    H1 = "OpenAI、セキュリティのバグ報奨金を増額　重大な欠陥は最大3万ドルに"

    def page(self, tab, og=None):
        og = og or self.H1
        return (f'<html><head><title>{tab} - サイソクニュース</title><meta property="og:title" content="{og}"/>'
                f'<meta name="twitter:title" content="{og}"/></head><body><h1>{self.H1}</h1></body></html>')

    def test_seo_title_in_tab_is_flagged(self):
        bad = self.page("OpenAIのバグ報奨金4倍超とは？セキュリティ強化策を解説")
        self.assertEqual({"A29"}, ids(audit.title_tag_problems(bad)))

    def test_same_title_everywhere_passes(self):
        self.assertEqual([], audit.title_tag_problems(self.page(self.H1)))

    def test_og_title_mismatch_and_descriptor(self):
        self.assertEqual({"A29"}, ids(audit.title_tag_problems(self.page(self.H1, og="ChatGPTのOpenAIが報奨金を増額"))))


class LabelTest(unittest.TestCase):
    """A30: ジャンル・タグの付け間違い（scripts/labels.py）"""

    def test_misfiled_genre_and_mixed_tags(self):
        bad = rec(title="kubell、法人向けストレージ「セキュアSAMBA」にMac用アプリ", genreSlug="ai", tags=["マイクロソフト", "Microsoft"])
        self.assertEqual({"A30"}, ids(audit.label_problems(bad)))
        self.assertEqual(2, len(audit.label_problems(bad)))
        good = rec(title="OpenAI、セキュリティのバグ報奨金を増額", genreSlug="ai", tags=["OpenAI", "セキュリティ"])
        self.assertEqual([], audit.label_problems(good))

class BlockOrderTest(unittest.TestCase):
    """A28（2026-11-03）: 記事ページで要約の欄を2つ出さない・目次は本文の前"""

    def test_duplicate_summary_and_toc_position(self):
        bad = ('<h2 id="points-heading">この記事のポイント</h2><h2 id="easy-1">要点</h2><h2 id="easy-2">a</h2>'
               '<h2 id="toc-heading">目次</h2><h2 id="sec-1">b</h2>')
        self.assertEqual(2, len(audit.block_order_problems(bad)))
        self.assertEqual({"A28"}, ids(audit.block_order_problems(bad)))
        good = '<h2 id="points-heading">この記事のポイント</h2><h2 id="toc-heading">目次</h2><h2 id="easy-1">a</h2><h2 id="sec-1">b</h2>'
        self.assertEqual([], audit.block_order_problems(good))


class FoundationReviewTest(unittest.TestCase):
    """2026-11-04 の土台の見直しで、毎日の見張りがすり抜けていたもの"""

    def test_inline_persona_label_is_caught(self):
        html = "<ul><li>会社員として：社内の調達で手間が減ります。</li></ul>"
        self.assertTrue(audit.PERSONA_HEADING_HTML.search(html))
        self.assertTrue(audit.PERSONA_HEADING_HTML.search("<p><strong>副業・独立を考えている人</strong>：参考になります。</p>"))
        self.assertFalse(audit.PERSONA_HEADING_HTML.search("<p>会社員としての働き方が変わります。</p>"))

    def test_legacy_backlog_counts_old_articles(self):
        old = {"title": "t", "lede": "x", "body": "## 料金は？\n\n元記事には料金が書かれていません。\n", "easy": ""}
        new = {"title": "t", "lede": "A社の売上高は前年同期比12%増の1,145億ドルでした。", "body": "iPhoneは17%増えました。", "easy": ""}
        backlog = audit.legacy_backlog([old, new])
        self.assertEqual(backlog["total"], 2)
        self.assertEqual(backlog["failing"], 1)
        self.assertIn("参考（違反の件数には数えない）", audit.render({}, 20, backlog))


if __name__ == "__main__":
    unittest.main()
