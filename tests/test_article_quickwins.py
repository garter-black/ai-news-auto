"""記事の「すぐできること」（2026-11-03 の比較調査）の検証。外部 API は呼ばない。

日本での提供の表・ジャンル別の判定・読者像の3つの立場・「筆者」の書き換えと品質ゲート・題名の約束・FAQ と今日やること・seoTitle。
"""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import genres  # noqa: E402
import japan_availability as ja  # noqa: E402
import persona  # noqa: E402
import quality  # noqa: E402
import web_articles as w  # noqa: E402

CLAUDE_BODY = """## 概要
Claudeの最上位プランがUltraになりました。

## 日本で使える？

まだ分かりません。元記事には、日本での提供状況の記載がありません。

- 提供地域や日本語への対応は、元記事では確認できていません。
- 円建ての価格も記載がありません。

## まとめ
月200ドルです。"""


def web_record(**changes):
    rec = {
        "sourceType": "web",
        "title": "対話AI「Claude」の最上位プランが「Ultra」に 月200ドル",
        "seoTitle": "Claude Ultraとは？料金と日本での使い方",
        "lede": "Anthropicによると、Ultraは月200ドルです。",
        "highlights": ["月200ドル"],
        "body": CLAUDE_BODY,
        "easy": "## ひとことで言うと\n月200ドルの大きなプランです。",
        "workMoney": {"relevance": "medium", "points": ["Proの上限に何度も当たる人は、Ultraを検討できます。"]},
    }
    rec.update(changes)
    return rec


class JapanTableTests(unittest.TestCase):
    def test_table_is_valid_and_dated(self):
        data = json.loads((ROOT / "content" / "japan_availability.json").read_text("utf-8"))
        ids = set()
        for item in data["items"]:
            self.assertIn(item["japan"], ("ok", "partial", "no", "unknown"), item["id"])
            self.assertTrue(item["sources"], item["id"])
            for src in item["sources"]:
                self.assertTrue(src["url"].startswith("https://"), item["id"])
                datetime.strptime(src["checkedAt"], "%Y-%m-%d")
            self.assertNotIn(item["id"], ids)
            ids.add(item["id"])
        self.assertEqual(len(ja.load()), len(data["items"]))  # 正規表現がすべて有効

    def test_main_product_matching(self):
        self.assertEqual(ja.main_product("GitHub Copilotに新機能")["id"], "github-copilot")
        self.assertEqual(ja.main_product("Windows 11の右クリックメニュー")["id"], "windows")
        self.assertIsNone(ja.main_product("WordPressでAIブログ"))
        self.assertIsNone(ja.main_product("Gemini Robotics 2 を発表"))

    def test_unknown_becomes_ok_only_for_available(self):
        verdict = {"status": "available", "japan": "unknown", "price": "月200ドル"}
        fixed = ja.apply_to_verdict(verdict, web_record())
        self.assertEqual(fixed["japan"], "ok")
        self.assertEqual(fixed["japanSource"]["url"], "https://www.anthropic.com/supported-countries")
        for status in ("limited", "soon", "demo"):
            # 一部の人だけ・提供予定の新機能は、製品が日本で使えても unknown のまま
            self.assertEqual(ja.apply_to_verdict({**verdict, "status": status}, web_record())["japan"], "unknown")
        self.assertEqual(ja.apply_to_verdict(verdict, web_record(title="新しいAIの研究", seoTitle=""))["japan"], "unknown")

    def test_japan_section_rewritten_from_table(self):
        rec = web_record()
        rec["verdict"] = ja.apply_to_verdict({"status": "available", "japan": "unknown"}, rec)
        body = ja.fix_japan_section(rec["body"], rec)
        section = body.split("## 日本で使える？")[1].split("## まとめ")[0]
        self.assertTrue(section.strip().startswith("使えます。Claudeは日本でも提供されています"))
        self.assertNotIn("まだ分かりません", section)
        self.assertNotIn("提供地域や日本語への対応は", section)
        self.assertIn("円建ての価格も記載がありません", section)  # 表と食い違わない箇条書きは残す
        # 判定を直していない（limited など）記事は本文も変えない
        rec2 = web_record(verdict={"status": "limited", "japan": "unknown"})
        self.assertEqual(ja.fix_japan_section(rec2["body"], rec2), rec2["body"])

    def test_prompt_and_check_blocks(self):
        block = ja.prompt_block("Claude Ultraが登場", "")
        self.assertIn("当サイトで確認済みの日本での提供状況", block)
        self.assertIn("unknown にする", block)
        self.assertIn("anthropic.com/supported-countries", ja.check_block("Claude Ultraが登場"))
        self.assertEqual(ja.prompt_block("大相撲の九州場所", ""), "")


class GenreVerdictTests(unittest.TestCase):
    def test_sports_verdict_has_no_product_wording(self):
        raw = {"status": "demo", "japan": "unknown", "price": "3,000円〜", "free": False, "audience": "現地観戦したい人",
               "take": "抽選に申し込みましょう", "when": "11月29日 19:00", "broadcast": "日本テレビ系で生中継"}
        v = w.genre_verdict(raw, "sports", {"title": "なでしこジャパン対ブラジル チケット追加販売"})
        self.assertEqual(v["kind"], "sports")
        self.assertEqual(v["status"], "soon")
        self.assertEqual(v["broadcast"], "日本テレビ系で生中継")
        self.assertEqual(v["when"], "11月29日 19:00")

    def test_data_topics_have_no_card(self):
        raw = {"status": "available", "japan": "ok", "price": "なし", "audience": "経営者", "take": "確認しましょう"}
        self.assertIsNone(w.genre_verdict(raw, "business", {"title": "円安倒産、10月は前年同月比で増加 東京商工リサーチ調べ"}))
        self.assertIsNotNone(w.genre_verdict(raw, "business", {"title": "トヨタが新型ランドクルーザーを発売"}))

    def test_public_verdict(self):
        raw = {"status": "soon", "japan": "unknown", "price": "最大750万円", "audience": "中小企業", "take": "公募要領を確認",
               "when": "12月1日から"}
        v = w.genre_verdict(raw, "public", {"title": "東京都の電気・ガス代の補助"})
        self.assertEqual((v["kind"], v["when"], v["japan"]), ("public", "12月1日から", "ok"))

    def test_extra_fields_by_genre(self):
        self.assertIn("faq: 読者が検索しそうな疑問", w.extra_fields_block("ai"))
        self.assertIn("空配列 [] にする", w.extra_fields_block("sports"))
        self.assertIn("観戦・参加できるか", w.extra_fields_block("sports"))
        self.assertIn("null", w.extra_fields_block("business"))


class PersonaViewpointTests(unittest.TestCase):
    def test_outlines_use_persona_viewpoints(self):
        for genre in (genres.AI, genres.BUSINESS, genres.PUBLIC, genres.TECH_GADGET, genres.LIFESTYLE):
            for view in persona.VIEWPOINTS:
                self.assertIn(f"### {view}", genre.outline, genre.slug)
        for genre in genres.ALL_GENRES:
            self.assertNotIn("エンジニアなら", genre.outline, genre.slug)

    def test_gate_rejects_old_viewpoints(self):
        rec = web_record(body=CLAUDE_BODY.replace("## まとめ", "## 自分にはどう関係する？\n### エンジニアなら\n使えます。\n\n## まとめ"),
                         seoTitle="Claude Ultraとは？料金")
        self.assertTrue(any("古い立場" in p for p in quality.structural_problems(rec, "ai")))


class FirstPersonTests(unittest.TestCase):
    def test_gate_rejects_first_person_testing(self):
        for text in ("筆者のテスト環境では21項目が9項目になりました。", "編集部で試したところ、速くなりました。", "実際に使ってみた結果、便利でした。"):
            with self.subTest(text=text):
                rec = web_record(body=CLAUDE_BODY + "\n\n" + text, seoTitle="Claude Ultraとは？料金")
                self.assertTrue(any("自分で試した" in p for p in quality.structural_problems(rec, "ai")))
        ok = web_record(body=CLAUDE_BODY + "\n\nマイナビニュースの記者が試したところ、21項目が9項目になりました。", seoTitle="Claude Ultraとは？料金")
        self.assertFalse(any("自分で試した" in p for p in quality.structural_problems(ok, "ai")))
        self.assertEqual(quality.first_person_problems({**ok, "body": "筆者のテスト環境では", "ownTest": True}), [])

    def test_press_first_person_is_rewritten(self):
        rec = {"body": "筆者のテスト環境では21項目でした。数値は筆者1台の結果です。", "easy": "", "lede": "", "highlights": ["筆者が確認"]}
        w.fix_press_first_person(rec, "マイナビニュース")
        self.assertEqual(rec["body"], "マイナビニュースの記者のテスト環境では21項目でした。数値はマイナビニュースの記者1台の結果です。")
        self.assertEqual(rec["highlights"], ["マイナビニュースの記者が確認"])


class PromiseTests(unittest.TestCase):
    def test_japan_promise_needs_answer(self):
        problems = quality.promise_problems(web_record())
        self.assertTrue(any("日本で使えるか" in p for p in problems))
        rec = web_record()
        rec["verdict"] = ja.apply_to_verdict({"status": "available", "japan": "unknown"}, rec)
        rec["body"] = ja.fix_japan_section(rec["body"], rec)
        self.assertEqual(quality.promise_problems(rec), [])

    def test_price_howto_when_promises(self):
        self.assertTrue(quality.promise_problems(web_record(title="新サービスの料金", seoTitle="", body="## 概要\n始まります。")))
        self.assertTrue(quality.promise_problems(web_record(title="新機能の使い方", seoTitle="", body="## 概要\n便利です。")))
        self.assertFalse(quality.promise_problems(web_record(title="新機能の使い方", seoTitle="", body="## 使い方\n1. 開く\n2. 押す")))
        self.assertTrue(quality.promise_problems(web_record(title="新制度はいつから？", seoTitle="", body="## 概要\n始まります。")))
        self.assertFalse(quality.promise_problems(web_record(title="新制度はいつから？", seoTitle="", body="## 概要\n12月1日から始まります。")))


class FaqAndSeoTests(unittest.TestCase):
    def test_clean_faq_and_actions(self):
        rows = [{"q": "無料で使える？", "a": "使えません。月200ドルです。"}, {"q": "日本で使える？", "a": "使えます。"},
                {"q": "いつから？", "a": "元記事には書かれていません。"}, {"q": "誰に向いている？", "a": "上限に当たる人です。"}]
        self.assertEqual([r["q"] for r in w.clean_faq(rows)], ["無料で使える？", "日本で使える？", "誰に向いている？"])
        self.assertEqual(w.clean_faq(rows[:2]), [])  # 3問に満たなければ出さない
        self.assertEqual(w.clean_actions(["1. 設定を開く", "上限を確かめる", "上限を確かめる"]), ["設定を開く", "上限を確かめる"])
        self.assertEqual(w.clean_actions(["ひとつだけ"]), [])

    def test_faq_shape_in_gate_and_fact_check(self):
        rec = web_record(seoTitle="Claude Ultraとは？料金", faq=[{"q": "無料？", "a": "いいえ"}], todayActions=["必ず儲かる銘柄を買う", "設定を見る"])
        problems = quality.faq_problems(rec)
        self.assertTrue(any("3〜5問" in p for p in problems))
        self.assertTrue(any("投資助言" in p for p in problems))
        text = quality._article_for_check({**rec, "faq": [{"q": "無料？", "a": "いいえ"}] * 3})
        self.assertIn("よくある質問", text)
        self.assertIn("今日やること", text)

    def test_empty_seo_title_is_filled(self):
        item = {"id": "test1234", "url": "https://example.test/news", "source_name": "公式", "title": "x",
                "source_slug": "example", "published_at": datetime.now(timezone.utc)}
        data = {"title": "防衛特別所得税とは？2027年1月から所得税に1%上乗せ、会社員の手取りはどう変わる",
                "seo_title": "", "description": "d", "body_markdown": "## 概要\n本文", "easy_markdown": "## ひとことで言うと\nx", "slug_en": "t"}
        with patch.object(w.m, "unique_slug", return_value="t"), patch.object(w.m, "match_affiliates", return_value=[]):
            rec = w.build_web_record(item, data, {}, "mock", "test")
        self.assertEqual(rec["seoTitle"], "防衛特別所得税とは？2027年1月から所得税に1%上乗せ")
        self.assertLessEqual(w._width(rec["seoTitle"]), w.SEO_MAX_WIDTH)


if __name__ == "__main__":
    unittest.main()
