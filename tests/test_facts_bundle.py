"""確かな事実の束・数字の計算部品・リンク先の取り込み・お金の記事の章立て・数字の照合ゲートの検証。外部 API・ネットワークは使わない。"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import calc_facts as cf  # noqa: E402
import facts_bundle as fb  # noqa: E402
import quality  # noqa: E402
import source_links as sl  # noqa: E402
import ui_claims as ui  # noqa: E402
import web_articles as w  # noqa: E402

FX = {"rate": 157.9, "date": "2026-11-02", "source": "ECB参照レート"}
RAKUTEN = (
    "楽天証券は、2026年11月に、証券総合口座数が国内証券会社単体で最多の1,500万口座超となったことをお知らせします。"
    "2026年4月に1,400万口座に到達してから、約7カ月で100万口座増となりました。\n"
    "（注1）各社ウェブサイト上での開示情報により集計。SBI証券は単独口座数非開示のため2021年3月末時点の口座数\n"
    "【手数料等およびリスクの説明について】"
)
TSR = (
    "2026年10月の「円安」倒産は、6件（前年同月比±0.0％）だった。1-10月累計は67件（前年同期比19.6％増、前年同期56件）で、"
    "2カ月を残して前年（1-12月）の65件を超えた。\n"
    "負債総額は20億7,700万円（前年同月比47.0％増）で、2カ月連続で前年同月を大幅に上回った。最大の倒産は、教材販売の（株）A（兵庫）の負債11億円。"
)
LADDER = [
    {"plan": "Free", "usd_month": 0, "limits": ["制限された使用量"]},
    {"plan": "Pro", "usd_month": 20, "limits": ["Free より多い使用量"]},
    {"plan": "Max", "usd_month": 100, "limits": ["Pro の 5 倍の使用量（5 時間ごと・毎週）"]},
    {"plan": "Ultra", "usd_month": 200, "limits": ["5 時間制限なし、Pro の 10 倍の使用量（毎週）"]},
]


class ExtractTest(unittest.TestCase):
    def test_japanese_numbers(self):
        cases = {"20億7,700万円": (2077000000, "JPY"), "3万1,580円": (31580, "JPY"), "1,500万口座": (15000000, "口座"),
                 "$1.5 billion": (1.5e9, "USD"), "47.0％増": (47.0, "%"), "Proの10倍": (10, "x"), "US$200/month": (200, "USD")}
        for text, (value, unit) in cases.items():
            q = [q for q in cf.extract(text) if q.unit][0]
            self.assertAlmostEqual(q.value, value, msg=text)
            self.assertEqual(q.unit, unit, msg=text)

    def test_dates_are_not_checked(self):
        self.assertFalse(any(cf.checkable(q) for q in cf.extract("2026年11月3日に5時間ごと、第3回")))

    def test_approx_tolerance(self):
        q = cf.extract("約14万口座")[0]
        self.assertTrue(q.approx)
        self.assertTrue(cf.supported(q, [142857.0]))
        self.assertFalse(cf.supported(cf.extract("14万口座")[0], [142857.0]))


class ComputeTest(unittest.TestCase):
    def texts(self, text, **kw):
        return [c.text for c in cf.compute(text, **kw)]

    def test_pace(self):
        out = self.texts(RAKUTEN)
        self.assertTrue(any("1カ月あたり約14.3万口座" in t and "÷ 7カ月" in t for t in out), out)

    def test_annual_change_share(self):
        out = "\n".join(self.texts(TSR))
        self.assertIn("1年で約80.4件", out)
        self.assertIn("11件増、＋19.6%", out)
        self.assertIn("2件増", out)  # 前年1年間の65件との差
        self.assertIn("53.0%（11億円 ÷ 20億7,700万円）", out)
        self.assertIn("前年同月は約14億1,300万円", out)  # 47.0％増からの逆算

    def test_progress_and_per_unit(self):
        out = "\n".join(self.texts("取得価額の総額は272億円、取得した株式の総数は1億5,700万株。取得価額の上限2,000億円に対して、累計の取得価額は1,048億円となった。"))
        self.assertIn("52.4%", out)
        self.assertIn("1株あたり約173円", out)

    def test_ladder_same_price_per_unit(self):
        out = cf.ladder(LADDER)
        self.assertIn("どれも$20で同じ", out[0].text)
        self.assertTrue(any(c.text.startswith("Max：$100/月") and "Pro（$20/月）と同じ" in c.text for c in out))

    def test_yen_with_rate_and_date(self):
        out = cf.yen([(200.0, "Ultraの月額")], FX, {200.0})
        self.assertIn("約31,580円（税別の目安）", out[0].text)
        self.assertIn("約34,740円", out[0].text)
        self.assertIn("1ドル=157.90円（2026-11-02", out[0].text)
        self.assertEqual(cf.yen([(200.0, "")], None), [])

    def test_relative_dates_from_source_date(self):
        pub = datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc)
        out = "\n".join(self.texts("来月１日から、免税制度のリファンド方式への移行がスタートします。来年4月から引下げ。", published=pub))
        self.assertIn("「来月1日」＝2026年11月1日", out)
        self.assertIn("「来年4月」＝2027年4月", out)
        dec = datetime(2026, 12, 10, tzinfo=timezone.utc)
        self.assertIn("2027年1月1日", "\n".join(self.texts("来月1日から", published=dec)))

    def test_past_comparison(self):
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        past = [{"slug": "old", "title": "楽天証券が1,400万口座", "at": now - timedelta(days=200), "text": "楽天証券の証券総合口座数が1,400万口座を突破"}]
        out = "\n".join(self.texts("楽天証券の証券総合口座数が1,500万口座を超えた。", past_rows=past, now=now))
        self.assertIn("100万口座増", out)
        self.assertIn("1カ月あたり約15.2万口座", out)


class NumberGateTest(unittest.TestCase):
    def record(self, body, facts=None):
        rec = {"sourceType": "web", "title": "楽天証券の口座数", "lede": "", "highlights": [], "body": body, "easy": ""}
        if facts:
            rec["_facts"] = facts
        return rec

    def test_invented_number_fails(self):
        problems = quality.number_problems(self.record("1カ月あたり約20万口座のペースです。"), RAKUTEN)
        self.assertTrue(problems)
        self.assertIn("20万口座", problems[0])

    def test_source_and_computed_numbers_pass(self):
        bundle = fb.Bundle(computed=cf.compute(RAKUTEN))
        rec = self.record("1,500万口座を超えました。1カ月あたり約14.3万口座のペースです（サイソク編集部の計算）。", bundle.meta())
        self.assertEqual(quality.number_problems(rec, RAKUTEN), [])

    def test_computed_number_needs_mark(self):
        bundle = fb.Bundle(computed=cf.compute(RAKUTEN))
        rec = self.record("1カ月あたり約14.3万口座増えています。", bundle.meta())
        self.assertTrue(any("計算した数字" in p for p in quality.number_problems(rec, RAKUTEN)))

    def test_bundle_number_needs_attribution(self):
        group = fb.Group("pricing:claude", "料金", "Anthropic公式の料金ページ", "https://claude.com/pricing", "2026-11-03",
                         "| Max | $100/月 |", marks=["Anthropic公式", "料金ページ", "出典："])
        rec = self.record("Maxは月100ドルです。", fb.Bundle(groups=[group]).meta())
        self.assertTrue(any("出典が書かれていない" in p for p in quality.number_problems(rec, "Ultraは月200ドル")))
        rec["body"] = "Maxは月100ドルです（Anthropic公式の料金ページ、11/3確認）。"
        self.assertEqual(quality.number_problems(rec, "Ultraは月200ドル"), [])

    def test_no_source_skips(self):
        self.assertEqual(quality.number_problems(self.record("月100ドル"), ""), [])

    def test_structural_uses_context(self):
        rec = self.record("過去記事では1,400万口座でした。")
        self.assertTrue(quality.number_problems(rec, "1,500万口座"))
        self.assertEqual(quality.number_problems(rec, "1,500万口座", "- 2026-04-01「楽天証券1,400万口座」"), [])


class BundleTest(unittest.TestCase):
    def test_build_rakuten_note_and_pace(self):
        item = {"title": "楽天証券、証券総合口座数1,500万口座突破のお知らせ", "url": "https://corp.rakuten.co.jp/news/x.html", "source_name": "楽天グループ"}
        with patch.object(fb, "load_fx", return_value=None):
            bundle = fb.build(item, RAKUTEN, [], "business", None)
        block = bundle.prompt_block()
        self.assertIn("SBI証券は単独口座数非開示", block)
        self.assertNotIn("手数料等およびリスク", block)
        self.assertIn("1カ月あたり約14.3万口座", block)
        self.assertIn("計算済みの数字の使い方", block)
        self.assertNotIn("## この数字はどう読む？", block)  # 決まった章は作らせない（2026-11-03）
        self.assertFalse(bundle.money)
        self.assertIn("SBI証券", bundle.check_block())

    def test_build_claude_pricing_ladder(self):
        item = {"title": "What is the Ultra plan? Claude", "url": "https://support.claude.com/en/articles/1", "source_name": "Anthropic公式"}
        text = "Ultra is Claude's highest individual plan at $200 per month with 10x Pro's weekly usage."
        with patch.object(fb, "load_fx", return_value=FX):
            bundle = fb.build(item, text, [], "ai", None)
        self.assertTrue(bundle.ladder)
        labels = [g.label for g in bundle.groups]
        self.assertEqual(labels[0], "料金")
        self.assertIn("他社との比較", labels)  # Ultra（最上位の段）を ChatGPT Pro などと比べる
        self.assertEqual(bundle.ladder.get("compare"), "plan:top")
        joined = "\n".join(c.text for c in bundle.computed)
        self.assertIn("1倍分", joined)
        self.assertIn("円（税別の目安）", joined)
        body = "## 概要\nUltraです。\n\n## 料金は？\n月200ドルです。\n\n## 日本で使える？\n使えます。"
        new = fb.insert_ladder(body, bundle)
        self.assertIn("のプランと料金（公式の料金と円の目安）", new)
        self.assertLess(new.index("プランと料金"), new.index("## 日本で使える？"))
        self.assertEqual(fb.insert_ladder(new, bundle), new)

    def test_copied_table_is_replaced(self):
        table = "| プラン | 料金（公式） |\n|---|---|\n| Pro | $20/月 |\n\n出典：公式"
        bundle = fb.Bundle(ladder={"name": "Claude", "markdown": table, "product": "claude", "cite": ""})
        body = "## 料金は？\n月200ドル。\n\nClaudeのプランと料金\n\n| プラン | 料金（公式） |\n|---|---|\n| Pro | $20/月 |\n\n出典：公式\n\n## まとめ\nおわり"
        new = fb.insert_ladder(body, bundle)
        self.assertEqual(new.count("| Pro | $20/月 |"), 1)
        self.assertIn("### Claudeのプランと料金", new)
        self.assertLess(new.index("### Claude"), new.index("## まとめ"))

    def test_links_follow_with_fake_fetcher(self):
        page = {"final_url": "https://support.claude.com/en/articles/1-ultra", "links": [
            {"url": "https://claude.com/pricing", "text": "pricing", "main": True},
            {"url": "https://support.claude.com/en/articles/2-max-plan", "text": "What is the Max plan?", "main": True},
            {"url": "https://twitter.com/claudeai", "text": "Twitter", "main": False},
            {"url": "https://example.com/pricing", "text": "pricing", "main": True},
        ]}
        calls = []

        def fetcher(url, timeout):
            calls.append(url)
            return {"text": "Pricing\nMax plan is $100 per month with 5x Pro usage.\nUltra plan is $200 per month.", "title": "Plans"}

        got = sl.follow(page, page["final_url"], "What is the Ultra plan? Max", "Anthropic公式", fetcher=fetcher)
        self.assertLessEqual(len(calls), 2)
        self.assertNotIn("https://example.com/pricing", calls)
        self.assertNotIn("https://twitter.com/claudeai", calls)
        self.assertIn("https://claude.com/pricing", calls)
        self.assertEqual({g["name"] for g in got}, {"Anthropic公式", "Anthropic公式ヘルプ"})
        groups = fb.link_groups(got)
        self.assertEqual(groups[0].label, "関連ページ")

    def test_money_links_public(self):
        links = [{"url": "https://www.nta.go.jp/taxes/shiraberu/xxx.htm", "text": "国税庁の手続の案内", "main": True},
                 {"url": "https://www.mof.go.jp/tax_policy/summary/a.pdf", "text": "税制改正の概要（PDF）", "main": True}]
        self.assertEqual(sl.pick(links, "https://www.mof.go.jp/public_relations/conference/x.html", "免税制度", money=False)[0]["url"],
                         links[1]["url"])  # 同じ財務省は追う
        picked = sl.pick(links, "https://example.co.jp/news", "免税制度の見直し", money=True)
        self.assertEqual(len(picked), 2)
        self.assertEqual(sl.site_name(links[0]["url"]), "国税庁")

    def test_excerpt_keeps_relevant_lines(self):
        text = "\n".join(["メニュー項目です"] * 200 + ["免税制度のリファンド方式は2026年11月1日から始まります"] + ["関係ない行です"] * 200)
        out = sl.excerpt(text, "免税 リファンド", cap=300)
        self.assertIn("リファンド方式", out)
        self.assertLessEqual(len(out), 320)


class MoneyTemplateTest(unittest.TestCase):
    def test_statement_date(self):
        pub = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
        item = {"url": "https://www.mof.go.jp/public_relations/conference/my20261030.html", "published_at": pub}
        self.assertEqual(fb.statement_date(item, "【冒頭発言】まず…").strftime("%Y-%m-%d"), "2026-10-30")
        self.assertEqual(fb.statement_date({"url": "https://x.jp/a", "published_at": pub}, "2026年11月1日\n本文").strftime("%Y-%m-%d"), "2026-11-01")
        self.assertIsNone(fb.statement_date({"url": "https://x.jp/a", "published_at": pub, "published_guessed": True}, "本文"))

    def test_money_topic(self):
        self.assertTrue(fb.is_money_topic("public", "防衛特別所得税、2027年1月から"))
        self.assertTrue(fb.is_money_topic("business", "片山財務相、外国人旅行者の免税リファンド方式への移行"))
        self.assertFalse(fb.is_money_topic("business", "楽天証券、証券総合口座数1,500万口座突破"))
        self.assertFalse(fb.is_money_topic("ai", "Claudeの税務機能"))

    def test_compose_uses_money_outline(self):
        item = {"id": "x", "title": "国税庁、年末調整の手続きを変更", "url": "https://www.nta.go.jp/x", "source_name": "国税庁",
                "source_slug": "nta", "published_at": datetime(2026, 11, 2, tzinfo=timezone.utc)}
        with patch.object(fb, "load_fx", return_value=None):
            out = w.compose_with_facts(item, "年末調整の手続きが変わります。対象は給与所得者です。", "public", [], "ふつう")
        prompt = out[2]
        # 2026-11-03 オーナー決定: 決まった章立てをやめ、ニュースの種類ごとの型で書く（scripts/news_types.py）。
        # お金・税・制度の記事は「規制・制度・行政」の型。早見表・自分の場合の確かめ方は、中身の順番の目安として残す（見出しの名前は決めない）
        self.assertEqual(item["newsType"], "policy")
        for word in ("規制・制度・行政", "対象者", "開始日・期限・締め切り", "手続きと窓口", "早見表", "自分の場合の確かめ方"):
            self.assertIn(word, prompt)
        for heading in ("## 何が変わる？", "## いつから？", "## 誰が対象？", "## 自分に関係ある？"):
            self.assertNotIn(heading, prompt)
        self.assertTrue(out[6].money)

    def test_money_outline_gate(self):
        rec = {"body": "## 概要\nx\n\n## ガソリン補助金はどう変わる？\ny", "_facts": {"money": True}}
        self.assertTrue(quality.money_outline_problems(rec))
        rec["body"] = "## 概要\nx\n## 何が変わる？\na\n## いつから？\nb\n## 誰が対象？\nc\n## 自分の場合の確かめ方\n1. d"
        self.assertEqual(quality.money_outline_problems(rec), [])
        self.assertEqual(quality.money_outline_problems({"body": "## 概要", "_facts": {"money": False}}), [])
        # ニュースの種類の型で書いた記事（newsType あり）は、決まった章立てを求めない
        self.assertEqual(quality.money_outline_problems({"body": "## 補助は12月まで", "_facts": {"money": True}, "newsType": "policy"}), [])

    def test_source_note_allows_bundle(self):
        self.assertIn("確認済みの事実の束", w.WEB_SOURCE_NOTE)
        self.assertIn("自分でしない", w.WEB_SOURCE_NOTE)


class ChartsTest(unittest.TestCase):
    def test_attach_charts(self):
        rec = {"title": "Claudeの料金プランに月200ドルのUltra", "tags": [], "body": "## 料金は？\n月200ドルです。", "sourceName": "Anthropic"}
        w.attach_charts(rec)
        self.assertEqual([c["kind"] for c in rec.get("charts") or []], ["ladder"])
        plain = {"title": "新しい研究", "body": "## 概要\n数字なし"}
        w.attach_charts(plain)
        self.assertNotIn("charts", plain)


CLAUDE_SRC = "Check your usage in Settings > Usage. Billing information: your billing address must match. Ultra is $200 per month."
INVENTED = """## 日本で使える？
使えます。日本のユーザーが試すための手順は以下の通りです。

- 公式のClaudeウェブサイトにアクセスする。
- アカウントにログインまたは新規登録を行う。
- 設定画面の支払いページからUltraプランを選択して申し込む。
- 本人確認が完了していることを確認し、利用を開始する。

## 料金は？
使用量は「設定」>「使用状況」で確かめられます。資料は「Add source」ボタンで足します。"""


class UiStepsTest(unittest.TestCase):
    def test_supported_and_invented(self):
        allowed = ui.Allowed(CLAUDE_SRC)
        self.assertEqual(ui.unsupported("使用量は「設定」>「使用状況」で確かめられます。", allowed), [])
        self.assertEqual(ui.unsupported("設定 > 使用状況で確認できます。", allowed), [])
        self.assertTrue(ui.unsupported("設定画面の支払いページから申し込む。", allowed, True))  # 道順が資料に無い
        self.assertTrue(ui.unsupported("Settings › Billing を開く。", allowed))
        self.assertTrue(ui.unsupported("「Add source」ボタンを押す。", allowed))
        self.assertEqual(ui.unsupported("「作る→直す→質問する」が1か所で回せます。", allowed), [])  # 流れの説明は道順ではない
        self.assertEqual(ui.unsupported("1,400万口座 → 1,500万口座に増えた。", allowed), [])
        self.assertEqual(ui.unsupported("アプリごとのメニュー項目を出すかどうかも切り替えられます。", allowed), [])

    def test_rewrite_invented_steps(self):
        allowed = ui.Allowed(CLAUDE_SRC)
        new, replaced = ui.rewrite(INVENTED, allowed, "https://support.claude.com/x", "Anthropic公式ヘルプ")
        self.assertIn("- 公式ヘルプの手順を確認してください（[Anthropic公式ヘルプ](https://support.claude.com/x)）。", new)
        self.assertNotIn("支払いページ", new)
        self.assertNotIn("Add source", new)
        self.assertIn("「設定」>「使用状況」", new)  # 元記事にある道順は残す
        self.assertIn("- 公式のClaudeウェブサイトにアクセスする。", new)  # 場所を作っていない一般的な手順は残す
        self.assertEqual(ui.record_problems({"body": new}, allowed), [])

    def test_gate_and_build(self):
        rec = {"sourceType": "web", "body": INVENTED, "faq": [{"q": "どう申し込む？", "a": "「Upgrade plan」ボタンを押します。"}]}
        problems = quality.ui_step_problems(rec, CLAUDE_SRC)
        self.assertTrue(problems and "画面の手順" in problems[0])
        self.assertEqual(quality.ui_step_problems(rec, ""), [])
        item = {"url": "https://support.claude.com/en/articles/1", "source_name": "Anthropic公式", "content": ""}
        record = {"body": INVENTED, "easy": ""}
        w.fix_ui_steps(record, item, {"text": CLAUDE_SRC}, None)
        self.assertIn("公式ヘルプの手順を確認してください（[公式ヘルプ](https://support.claude.com/en/articles/1)）", record["body"])


class SourcesTest(unittest.TestCase):
    def test_fact_sources(self):
        group = fb.Group("pricing:claude", "料金", "Anthropic公式の料金ページ", "https://claude.com/pricing", "2026-11-03",
                         "| Max | $100/月 |", marks=["Anthropic公式"])
        note = fb.Group("note", "元記事の注記", "楽天", "", "", "注記", show=False)
        bundle = fb.Bundle(groups=[group, note], computed=cf.compute(RAKUTEN))
        rec = {"body": "Maxは月100ドル（Anthropic公式の料金ページ、11/3確認）。約14.3万口座（サイソク編集部の計算）", "_facts": bundle.meta(),
               "verdict": {"japanSource": {"name": "Claude", "url": "https://www.anthropic.com/supported-countries", "checkedAt": "2026-11-03"}}}
        w.finalize_facts(rec, "Ultraは月200ドル")
        labels = [s["label"] for s in rec["factSources"]]
        self.assertEqual(labels, ["料金", "日本での提供", "計算"])
        self.assertNotIn("_facts", rec)


if __name__ == "__main__":
    unittest.main()
