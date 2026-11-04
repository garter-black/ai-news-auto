"""scripts/article_style.py: 文体の決まり（1か所）と公開前の門（通信しない）

2026-11-03 オーナー: 「幼すぎる表現は本当にやめたい。恥ずかしい」。決まった章立てをやめ、種類ごとの型で書く（「かなり大事」）。
報告 out/記事の文章レベル比較_2026-11-03.md の自動ゲート17項目を、すべての生成の段で同じ門として守らせる。
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import article_style as st  # noqa: E402

# 古い型の記事（報告で見つかった書き方をまとめたもの）
OLD = {
    "title": "ChatGPTのOpenAI、12月から利用枠を「Codex」と共通化",
    "lede": "デジタル庁は、生成AI「源内」の利用状況アンケート調査の結果を公表しました。",
    "highlights": ["デジタル庁は、生成AI「源内」の利用状況アンケート調査の結果を公表しました。", "満足は71.7%", "31.2%は使っていない"],
    "body": "\n".join([
        "## 概要", "デジタル庁は、生成AI「源内」の利用状況アンケート調査の結果を公表しました。",
        "## 何が発表された？", "で、今回は何の発表だったの？", "満足度は71.7%です。", "職員の満足度はどうだったの？", "71.7%が満足です。",
        "## 料金は？", "元記事には料金が書かれていません。",
        "## 日本で使える？", "元記事では案内なし。",
        "## ライバルと比べてどう？", "比較は判断できません。",
        "## 自分に関係ある？", "重要な要素があります。カスタムパーツの充実です。会社員にとっても重要です。これは見逃せない話です。",
        "## まとめ", "読者が次に取るべき行動があります。公式サイトを確認することです。Settings → Usage で確かめましょう。",
    ]),
    "easy": "## ひとことで言うと\nすごい話です。\n\n## 何がすごいの？\nワクワクします。",
}

# 新しい型の記事（決算の例）
NEW = {
    "title": "Apple、7〜9月期の売上高は1145億ドル　iPhoneは17%増",
    "newsType": "earnings",
    "sourceName": "Apple",
    "lede": "Appleの7〜9月期の売上高は前年同期から12%増え、1145億ドルと過去最高でした。10〜12月期も7〜10%の増収を見込んでいます。",
    "highlights": ["iPhoneの売上高は17%増、Macは24%増。iPadは10%減でした", "日本の売上高は65億4000万ドルで、前年から1%減りました",
                   "純利益は307億ドルで、1株利益は2.10ドルでした"],
    "body": "\n\n".join([
        "製品別では、iPhoneが前年同期比17%増、Macが24%増でした。一方、iPadは10%減っています。サービスの売上高は315億ドルで、四半期として最も多い額です。",
        "ターナスCEOは、折りたたみの「iPhone Duo」を「初代以来、最も変革的なiPhone」と紹介しました。",
        "## 日本の売上は1%減、中華圏は8%増",
        "地域別では、日本の売上高が65億4000万ドルで前年から1%減りました。中華圏は8%増の158億ドルでした（Apple の財務諸表、11/3確認）。",
        "パレクCFOは、メモリの価格上昇が粗利益率を押し下げると説明しています。会社は10〜12月期の売上高を前年同期比7〜10%増と見込んでいます。",
        "## まだ分からないこと",
        "- iPhone Duo の販売台数はまだ発表されていません。",
    ]) + "\n\n" + ("製品ごとの売上の内訳は、Appleが公開した財務諸表に載っています。" * 12),
    "easy": "## ひとことで言うと\nAppleの7〜9月期は売上が過去最高でした。iPhoneとMacが伸び、iPadは減りました。",
    "verdict": {"status": "available", "japan": "ok", "price": "無料"},
}


class GateTest(unittest.TestCase):
    def test_old_style_is_rejected_with_every_benchmark_pattern(self):
        rec = dict(OLD)
        codes = {code for code, _ in st.coded_problems(rec)}
        text = "\n".join(st.problems(rec))
        for word in ("決まった型の見出し", "見出しが多すぎる", "中身のない章", "指示文の言葉", "1つの文を2つに割っている", "一問一答の口調",
                     "幼い言い方", "説明つきの社名", "つなぎ", "リードのくり返し", "かんたん版に決まった型の見出し",
                     "「〇〇を発表しました」で終わり", "要点"):
            self.assertIn(word, text, word)
        self.assertTrue({"A1", "A2", "A3", "A4", "A5", "A18", "A19"} <= codes, codes)

    def test_new_style_passes(self):
        rec = dict(NEW)
        st.autofix(rec)
        self.assertEqual(st.problems(rec), [])

    def test_heading_density(self):
        body = "## 1つ目\n" + "あ" * 400 + "\n## 2つ目\n" + "い" * 400 + "\n## 3つ目\n" + "う" * 300
        self.assertEqual(st.max_headings(body), 2)
        self.assertTrue(any("見出しが多すぎる" in p for p in st.problems({"body": body})))
        # 「まだ分からないこと」は数えない
        ok = "## 1つ目\n" + "あ" * 1200 + "\n## まだ分からないこと\n- 価格はまだ発表されていません。"
        self.assertFalse(any("見出しが多すぎる" in p for p in st.problems({"body": ok})))

    def test_quoted_words_are_not_style_problems(self):
        rec = {"body": "CEOは「すごい製品です」と話しました。同社は「業界初」としています。" + "事実の文です。" * 30}
        self.assertEqual(st.problems(rec), [])

    def test_promo_words_need_attribution(self):
        rec = {"body": "初代以来最も変革的な「iPhone Duo」が出ました。" + "事実の文です。" * 30}
        self.assertTrue(any("宣伝の言葉" in p for p in st.problems(rec)))

    def test_persona_subheads_and_nonanswer_titles(self):
        # 台帳 A24（2026-11-03）: 3つの立場の小見出しと、「〜は不明」「〜は示されていない」の見出しは出さない
        body = "事実の文です。" * 40 + "\n\n### 会社員として\n経理の仕事が速くなります。\n\n## 価格は不明\n価格は発表されていません。"
        text = " ".join(st.problems({"body": body}))
        self.assertIn("立場の小見出し", text)
        self.assertIn("答えになっていない見出し", text)
        ok = "事実の文です。" * 40 + "\n\n## まだ分からないこと\n- 価格はまだ発表されていません。"
        self.assertEqual(st.problems({"body": ok}), [])

    def test_persona_instructions_removed_at_source(self):
        import persona  # noqa: PLC0415

        self.assertNotIn("### 会社員として", persona.relate_outline())
        self.assertIn("小見出し", persona.relate_outline())
        self.assertNotIn("立場ごとの見方は、本当に関係するときだけ書く", persona.PLAIN_STYLE)

    def test_notice_candidates_are_dropped_before_writing(self):
        import web_articles as w  # noqa: PLC0415

        self.assertTrue(w.prefilter({"title": "令和8年秋の褒章について", "source_slug": "mlit"}))
        self.assertTrue(w.prefilter({"title": "製造たばこの小売定価の認可", "source_slug": "mof"}))
        self.assertIsNone(w.prefilter({"title": "OpenAIが新しいAIモデルを公開", "source_slug": "openai-news"}))

    def test_short_hollow_lede(self):
        # 2026-11-03 書き直しの見直しで見つけた形:「GoogleはGemini 4 Carbonを発表しました。」だけの1文目
        self.assertTrue(any("〇〇を発表しました" in p for p in st.problems({"lede": "GoogleはGemini 4 Carbonを発表しました。料金は2ドルです。"})))
        self.assertFalse(any("〇〇を発表しました" in p for p in st.problems(
            {"lede": "Googleは、脆弱性の発見と修正が得意な新しいAIモデル「Gemini 4 Carbon」を、一部の防御担当者向けに公開しました。"})))

    def test_minor_change_is_not_new_model(self):
        self.assertTrue(any("新型" in p for p in st.problems({"title": "トヨタの新型SUV、一部改良", "body": "一部改良です。"})))

    def test_same_number_repeated(self):
        body = "発売は2027年1月上旬です。" + "価格は500万円です。" * 8  # 8回以上は作り直し（ランクルFJの記事は8回）
        self.assertTrue(any("英語の画面名" in w for w in st.warnings({"body": "Settings → Usage で確かめます。"})))
        self.assertTrue(any("同じ数字のくり返し" in p for p in st.problems({"body": body})))
        self.assertTrue(any("同じ数字が多い" in w for w in st.warnings({"body": "価格は500万円です。" * 5})))


class BeginnerExpertTest(unittest.TestCase):
    """台帳 A31（2026-11-03 オーナー）: 初心者でもわかるが、専門家が物足りないとも思わない線"""

    def test_rule_is_in_every_prompt(self):
        import news_types  # noqa: PLC0415

        self.assertIn("専門家が物足りないとも思わない", st.STYLE_PROMPT)
        for key in news_types.TYPES:
            block = news_types.prompt_block(key)
            self.assertIn("それが何か・誰のためのものか", block, key)
            self.assertIn("専門家向けの具体", block, key)

    def test_unexplained_acronyms_are_noted(self):
        self.assertEqual(st.unexplained_terms("CVSS（深刻さの点数）は9.8です。MCP（AI とアプリをつなぐ決まり）に対応。"), [])
        self.assertEqual(st.unexplained_terms("CVSSは9.8です。AIとAPIの話です。"), ["CVSS"])
        self.assertTrue(any("言い換えていない略語" in w for w in st.warnings({"body": "MCPに対応しました。"})))


class AutofixTest(unittest.TestCase):
    def test_source_word_and_verdict(self):
        rec = {"sourceName": "Apple", "newsType": "earnings", "body": "元記事によると、売上は増えました。元記事では見通しも示しています。",
               "verdict": {"status": "available"}, "highlights": ["元記事では過去最高"]}
        changes = st.autofix(rec)
        self.assertNotIn("元記事", rec["body"])
        self.assertIn("Appleの発表によると", rec["body"])
        self.assertIsNone(rec["verdict"])
        self.assertEqual(rec["highlights"], ["発表では過去最高"])
        self.assertTrue(changes)

    def test_product_keeps_verdict(self):
        rec = {"newsType": "product", "verdict": {"status": "available"}, "body": "x"}
        st.autofix(rec)
        self.assertTrue(rec["verdict"])


class SummaryOpenerTest(unittest.TestCase):
    """2026-11-04 の点検: 新しい記事5本中4本で、本文の頭の「要約すると、」がリードの言い直しになっていた（台帳 A19）"""

    LEDE = "Amazon BedrockのAIモデル「Kimi K3」で、推論処理をインド国内に限って実行できるプロファイルが加わりました。処理先は2リージョンです。"

    def test_autofix_drops_restated_opener(self):
        rec = {"lede": self.LEDE, "highlights": ["a1", "b2", "c3"],
               "body": "要約すると、AIモデル「Kimi K3」をAmazon Bedrock経由で使うとき、処理をインド国内だけに限れるようになりました。\n\nBedrockはAWSのサービスです。",
               "easy": "要約すると、新しい事例です。\n\nB"}
        changes = st.autofix(rec)
        self.assertEqual(rec["body"], "BedrockはAWSのサービスです。")
        self.assertEqual(rec["easy"], "新しい事例です。\n\nB")
        self.assertTrue(any("要約すると" in c for c in changes))

    def test_gate_rejects_opener(self):
        rec = {"lede": self.LEDE, "title": "t", "body": "## 背景\n\n要約すると、別の話です。", "easy": ""}
        self.assertTrue(any(code == "A19" and "要約すると" in msg for code, msg in st.coded_problems(rec)))

    def test_prompts_do_not_ask_articles_for_the_opener(self):
        import persona  # noqa: PLC0415

        self.assertIn("本文・かんたん版の頭に「要約すると、」の段落を置かない", st.STYLE_PROMPT)
        self.assertNotIn("合うところでは、最初に「要約すると、」", persona.PLAIN_STYLE)


class SlugLinkTest(unittest.TestCase):
    """2026-11-04 の点検: 「これまでの流れ」に、リンクの文字が slug のままの記事へのリンクが出ていた（台帳 A36）"""

    def test_slug_text_becomes_title_and_gate_catches_it(self):
        slug = next(p.stem for p in st.ARTICLES_DIR.glob("*.json"))
        text = f"- 11月2日：確認（[{slug}](/articles/{slug})）"
        fixed = st.slug_link_titles(text)
        self.assertNotIn(f"[{slug}]", fixed)
        self.assertIn(f"](/articles/{slug})", fixed)
        self.assertEqual(st.slug_link_titles("[no-such-article-x](/articles/no-such-article-x)"), "[関連記事](/articles/no-such-article-x)")
        rec = {"title": "t", "lede": "x", "body": text, "easy": ""}
        self.assertTrue(any(code == "A36" for code, _ in st.coded_problems(rec)))
        st.autofix(rec)
        self.assertFalse(any(code == "A36" for code, _ in st.coded_problems(rec)))


class RepairTest(unittest.TestCase):
    """報告で「直す」とされた項目は、作り直す前に、その文だけ安いモデルで直す（quality.repair_language）"""

    def test_style_sentences_go_to_the_cheap_repair(self):
        from unittest.mock import patch  # noqa: PLC0415

        import quality  # noqa: PLC0415

        rec = {"title": "トヨタ、クラウンを一部改良", "lede": "トヨタはクラウンの一部改良の概要を発表しました。価格は570万円からです。",
               "body": "これはすごい改良です。新型のクラウンは11月2日に発売しました。" + "事実の文です。" * 20, "easy": "## ひとことで言うと\nx"}
        items = st.repair_items(rec)
        issues = " ".join(i for x in items for i in x["issues"])
        self.assertIn("幼い言い方", issues)
        self.assertIn("新型", issues)
        self.assertIn("リードの1文目", issues)

        def fake(system, prompt, schema, **kw):
            fixes = []
            for line in prompt.splitlines():
                if line.startswith("- id "):
                    i = int(line.split(":")[0].split()[-1])
                    sentence = line.split(": ", 1)[1]
                    new = (sentence.replace("すごい改良", "大きな改良").replace("新型の", "改良した")
                           .replace("トヨタはクラウンの一部改良の概要を発表しました。", "トヨタのクラウンが一部改良され、570万円から買えます。"))
                    fixes.append({"id": i, "text": new})
            return {"fixes": fixes}, "mock"

        with patch.object(quality.llm, "generate_json", side_effect=fake):
            found, fixed, _ = quality.repair_language(rec, "")
        self.assertGreaterEqual(fixed, 3)
        self.assertNotIn("すごい", rec["body"])
        self.assertNotIn("新型", rec["body"])
        self.assertFalse(any("〇〇を発表しました" in p for p in st.problems(rec)))


class LeadPointTest(unittest.TestCase):
    """「この記事のポイント」の1つ目は一番のニュースの言い換え（2026-11-04 オーナー決定 D4・台帳 A38）。
    Kimi K3 の記事で、1つ目が脇の話（費用80%削減）、一番のニュース（インド国内で処理）はリードにしか無かった"""

    KIMI = {
        "title": "AWS、Bedrockの「Kimi K3」でインド国内推論に対応　処理をムンバイとハイデラバードに限定",
        "lede": "Amazon BedrockのAIモデル「Kimi K3」で、推論処理をインド国内に限って実行できるプロファイルが加わりました。処理先はムンバイとハイデラバードの2リージョンです。",
        "highlights": [
            "Ultrahumanはプロンプトキャッシュで言語モデルの費用を80%削減し、複数回の会話を85%速くしたと説明",
            "Chat Completions・Responsesなど4つのAPIに対応し、既存のopenaiライブラリから呼び出せる",
            "急ぎでない処理はflex層で標準料金から50%割引",
        ],
    }

    def test_side_fact_first_is_rejected(self):
        self.assertIn("A38", {code for code, _ in st.coded_problems(self.KIMI)})

    def test_main_news_reworded_first_passes(self):
        rec = {**self.KIMI, "highlights": ["Bedrockの「Kimi K3」で、推論をインド国内のムンバイとハイデラバードだけで処理する設定を選べます",
                                           *self.KIMI["highlights"][1:]]}
        self.assertEqual(st.lead_point_problem(rec), "")
        self.assertNotIn("A38", {code for code, _ in st.coded_problems(rec)})

    def test_verbatim_copy_of_lede_is_rejected_but_not_dropped(self):
        rec = {**self.KIMI, "highlights": ["Amazon BedrockのAIモデル「Kimi K3」で、推論処理をインド国内に限って実行できるプロファイルが加わりました。",
                                           *self.KIMI["highlights"][1:]]}
        self.assertIn("写し", st.lead_point_problem(rec))
        st.autofix(rec)
        self.assertEqual(len(rec["highlights"]), 3, "1つ目（一番のニュース）は自動で外さない")

    def test_repair_item_rewrites_first_point_with_lede_context(self):
        items = [x for x in st.repair_items(self.KIMI) if x["field"] == "highlights"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["index"], 0)
        self.assertIn("インド国内", " ".join(items[0]["issues"]))
        self.assertTrue(items[0]["check"]("Ultrahumanは費用を80%削減した"), "直しても脇の話のままなら使わない")
        self.assertFalse(items[0]["check"]("Bedrockの「Kimi K3」で、推論をインド国内だけで処理する設定を選べます"))

    def test_rule_is_in_every_generation_prompt(self):
        import followup  # noqa: PLC0415
        import main as m  # noqa: PLC0415
        import roundup  # noqa: PLC0415
        import web_articles as w  # noqa: PLC0415

        for name, text in {"video": m.PROMPT_TEMPLATE, "web": w.WEB_PROMPT_TEMPLATE, "broad": w.BROAD_PROMPT_TEMPLATE,
                           "trend": w.TREND_PROMPT_TEMPLATE, "roundup": roundup.PROMPT}.items():
            with self.subTest(name=name):
                self.assertIn(st.LEAD_POINT_RULE, text)
                self.assertNotIn("リードの文をくり返さない", text)
        self.assertIn(st.LEAD_POINT_RULE, (ROOT / "scripts" / "followup.py").read_text("utf-8"))
        self.assertIn("要点）の1つ目は、一番のニュース", st.STYLE_PROMPT)


class EveryPathTest(unittest.TestCase):
    """文体の決まりは1か所（article_style.STYLE_PROMPT）にあり、すべての生成の段が使い、門で守らせる（台帳 A21）"""

    def test_style_prompt_is_in_every_system_instruction(self):
        import main as m  # noqa: PLC0415
        import roundup  # noqa: PLC0415

        self.assertIn(st.STYLE_PROMPT, m.SYSTEM_INSTRUCTION_BASE)  # 通常・速報・幅広い話題・話題のキーワード・動画・別の切り口
        self.assertIn(st.STYLE_PROMPT, roundup.SYSTEM_INSTRUCTION)  # まとめ

    def test_no_prompt_orders_fixed_chapters_or_childish_headings(self):
        import main as m  # noqa: PLC0415
        import roundup  # noqa: PLC0415
        import web_articles as w  # noqa: PLC0415

        prompts = {"video": m.PROMPT_TEMPLATE, "web": w.WEB_PROMPT_TEMPLATE, "broad": w.BROAD_PROMPT_TEMPLATE, "trend": w.TREND_PROMPT_TEMPLATE,
                   "roundup": roundup.PROMPT, "system": m.SYSTEM_INSTRUCTION_BASE, "source": w.WEB_SOURCE_NOTE + w.BROAD_SOURCE_NOTE}
        for name, text in prompts.items():
            with self.subTest(name=name):
                self.assertNotRegex(text, r"見出しは次の4つ|## 何がすごいの？ /|読者の疑問の形|構成で指定された名前をそのまま|言及なし」と明記|案内なし」と書")
                self.assertNotIn("<<EASY_RULE>>", text)

    def test_gate_is_wired_into_every_path(self):
        import quality  # noqa: PLC0415

        rec = {"sourceType": "web", "title": "x" * 30, "body": "## 料金は？\n月20ドルです。", "easy": "## ひとことで言うと\nx",
               "workMoney": {"relevance": "low", "points": ["直接の影響は小さいです。"]}}
        self.assertTrue(any("決まった型の見出し" in p for p in quality.structural_problems(rec, "ai")))
        src = {name: (ROOT / "scripts" / name).read_text("utf-8") for name in ("main.py", "roundup.py", "web_articles.py", "angle_articles.py")}
        self.assertIn("article_style.problems(record)", src["main.py"])     # 動画の記事: 作り直し1回・だめなら出さない
        self.assertIn("article_style.problems(record)", src["roundup.py"])  # まとめ: 同じ
        self.assertIn("quality.evaluate(", src["web_articles.py"])          # 通常・速報・幅広い話題・話題のキーワード: 品質ゲート
        self.assertIn("quality.evaluate(", src["angle_articles.py"])

    def test_owner_rule_codes_exist_in_ledger(self):
        ledger = (ROOT / "docs" / "owner_rules.md").read_text("utf-8")
        for code in {c for c, _ in st.coded_problems(dict(OLD))}:
            self.assertRegex(ledger, rf"\| {code} \|")


if __name__ == "__main__":
    unittest.main()
