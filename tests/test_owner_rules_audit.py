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


if __name__ == "__main__":
    unittest.main()
