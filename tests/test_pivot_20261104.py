"""2026-11-04 オーナー承認のピボット「ちゃんと見られる記事に」: 題名・リード・本文の順番（通信しない）

報告 out/ピボット_ビューが取れるニュース_2026-11-04.md。台帳 docs/owner_rules.md の A5（題名）・A19（リード）・A42（本文の順番と長さ）。
  - 題名: 「〇〇が△△を発表」のような発表文の題名の言い直しを基本の形にしない。「〜とは？」・「要注意」・「〇選」も不可
  - リード: 1文目＝読者にとって何が変わるか → 2文目＝数字か条件 → 3文目＝今やること／まだ決まっていないこと。
    「〇〇は×日、…を発表しました」で始めない（公開前の門で作り直し）
  - すべての生成の段（動画・公式ブログ・幅広い話題・速報レーン・話題・別の切り口・型での書き直し・見出しの書き直し）が同じ決まりを使う
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import article_style as st  # noqa: E402
import headline_normalize as hn  # noqa: E402
import news_types as nt  # noqa: E402
import quality  # noqa: E402


class PressTitleTest(unittest.TestCase):
    """[A5] 発表文の題名の言い直しを基本の形にしない"""

    BAD = [
        "NTT、2026年度第2四半期決算を発表",
        "IBM Research、スライド作成AI「Palette」を発表　PresentBenchで2分野首位",
        "Googleが「Agent Compute Engine」を発表　エージェント専用の実行基盤",
        "サイバーエージェント、2026年9月期の通期決算を11月11日に発表へ",
        "良品計画、不正アクセスに関する続報を公表",
        "東京商工リサーチ、デザイン業の上半期倒産が過去最多の41件と発表",
    ]
    HIDDEN = ["「Gemini 4 Carbon」とは？", "AIエージェントの導入で注意したい5選", "スマホを使う人は要注意　新しい詐欺の手口"]
    GOOD = [
        "iPhoneのヘルスケアアプリが日本語で刷新へ　年内に無料アップデートで配信",
        "NTT、利益予想を200億円引き上げ　ドコモが好調、4〜9月は増収減益",
        "松井証券、ゆうちょ銀行の関連会社に　ゆうちょ系の運用商品を松井で販売へ",
        "Honda、初の高級電動アシストMTBを公開　重さ約24kg",
        "LINEの古い版、今日でサポート終了　対象の版と確かめ方",
        "OpenAI、新AIモデル「GPT-6.1 Terra」を公開　料金は従来の半分に",
        "「発表」アプリの新版が登場",  # 製品名の中の言葉では引っかけない
    ]

    def test_press_release_titles_are_rejected(self):
        for title in self.BAD:
            with self.subTest(title=title):
                problems = hn.title_problems(hn.normalize_title(title))
                self.assertTrue(any("発表文の題名の言い直し" in p for p in problems), problems)

    def test_hidden_answer_and_bait_titles_are_rejected(self):
        for title in self.HIDDEN:
            with self.subTest(title=title):
                self.assertTrue(hn.title_problems(hn.normalize_title(title)))

    def test_reader_impact_titles_pass(self):
        for title in self.GOOD:
            with self.subTest(title=title):
                self.assertEqual(hn.title_problems(hn.normalize_title(title)), [])

    def test_title_rewrite_refuses_a_press_release_proposal(self):
        """記事を書き出すときの見出しの書き直し（rewrite_titles.fix_one）も、発表の言い直しの案は採らない"""
        import rewrite_titles

        record = {"slug": "x", "title": "NTT、2026年度第2四半期決算を発表", "lede": "NTTの営業利益の予想は200億円上がりました。",
                  "body": "ドコモの営業利益は9,630億円の予想です。", "highlights": []}
        self.assertTrue(any("発表文" in p for p in rewrite_titles.check_title(record, "NTT、中間決算を発表")))
        self.assertEqual([p for p in rewrite_titles.check_title(record, "NTT、利益予想を200億円引き上げ　ドコモが好調") if "発表文" in p], [])

    def test_title_prompt_teaches_reader_impact_patterns(self):
        prompt = hn.TITLE_STYLE_PROMPT
        for needle in ("読者にとって何が変わるか", "発表文の題名の言い直しを基本の形にしない", "だれに影響＋何が変わる", "金額・数字を先に",
                       "いつから・期限", "対象の範囲", "日本で使えるか・無料か", "前との違い", "「〜とは」「〜とは？」で終わる問いの見出し"):
            self.assertIn(needle, prompt)
        self.assertNotIn("{", prompt)  # テンプレートに埋め込む（.format で壊れない）


class PivotLedeTest(unittest.TestCase):
    """[A19] リードの1文目を発表の言い直しにしない。3文の型"""

    BAD = [
        "ゆうちょ銀行が、松井証券の議決権の約20%を取得する予定だと、2026年11月4日に発表しました。取得するのは丸六の株式です。",
        "IBM Researchが11月3日、社内のデザインルールに沿ってスライドを作る小型AIモデル「Palette」を発表しました。",
        "Googleは11月3日、学習支援機能「Guided Learning」をGoogle Classroomで世界に提供すると発表しました。",
        "OpenAIは2026年11月3日、新しい役員の就任を明らかにしました。",
        "デジタル庁は、生成AI「源内」の利用状況アンケート調査の結果を公表しました。",
    ]
    GOOD = [
        "松井証券の口座で、ゆうちょ銀行の子会社がつくる運用商品を買えるようになる見通しです。議決権の19.99%を持ちます。金額はまだ発表されていません。",
        "NTTは11月4日、2026年度の営業利益の予想を200億円上げ、1兆7,300億円にしました。理由はドコモの好調です。",
        "iPhoneの「ヘルスケア」アプリが、日本語でも使えるようになります。配信日はまだ発表されていません。",
        "気象庁は、東京都に大雨特別警報を発表しました。",  # 警報の発表は知らせの中身そのもの
    ]

    def test_press_release_leads_fail_the_gate_on_every_path(self):
        for lede in self.BAD:
            with self.subTest(lede=lede[:30]):
                record = {"title": "t", "lede": lede, "body": "本文です。", "highlights": []}
                self.assertTrue(st.press_lede_problem(record))
                self.assertIn("A19", [code for code, _ in st.coded_problems(record)])
                # すべての段が使う共通の門（quality.every_path_problems）にも出る
                self.assertTrue(any("発表の言い直し" in p for p in quality.every_path_problems(record)))

    def test_reader_impact_leads_pass(self):
        for lede in self.GOOD:
            with self.subTest(lede=lede[:30]):
                self.assertEqual(st.press_lede_problem({"lede": lede}), "")

    def test_roundup_is_not_judged_by_the_single_news_lede(self):
        self.assertEqual(st.press_lede_problem({"type": "roundup", "lede": self.BAD[2]}), "")

    def test_lede_rule_has_three_sentences(self):
        rule = st.LEDE_RULE
        for needle in ("1文目: 読者にとって何が変わるか", "2文目: いちばん大事な数字か条件", "3文目: 読者が今やること、またはまだ決まっていないこと",
                       "発表したことだけの文で始めない"):
            self.assertIn(needle, rule)
        self.assertNotIn("{", rule)
        self.assertIn("リードは3文", st.STYLE_PROMPT)


class EveryPathUsesSharedRulesTest(unittest.TestCase):
    """（O13）すべての生成の段のプロンプトが、同じ題名・リード・本文の順番の決まりを使う"""

    def test_every_article_prompt_has_title_and_lede_rules(self):
        import main as m
        import web_articles as w

        templates = {"動画（main.py）": m.PROMPT_TEMPLATE, "公式ブログ・速報レーン（WEB）": w.WEB_PROMPT_TEMPLATE,
                     "幅広い話題（BROAD）": w.BROAD_PROMPT_TEMPLATE, "話題のキーワード（TREND）": w.TREND_PROMPT_TEMPLATE}
        for name, template in templates.items():
            with self.subTest(path=name):
                self.assertIn(st.LEDE_RULE, template)
                self.assertIn(hn.TITLE_STYLE_PROMPT, template)
                self.assertNotIn("<<LEDE_RULE>>", template)

    def test_system_instruction_and_outline_carry_the_new_structure(self):
        import main as m

        self.assertIn(st.STYLE_PROMPT, m.SYSTEM_INSTRUCTION_BASE)  # web_articles・roundup・angle・rewrite_types も同じ文を使う
        for key in nt.TYPES:
            block = nt.prompt_block(key)
            with self.subTest(key=key):
                self.assertIn("本文の最初の段落（見出しを付けない）: だれに関係するか", block)
                self.assertIn("読者が知りたいことに答えたら終わる", block)

    def test_other_writers_reuse_the_shared_prompts(self):
        """速報レーン（fast_lane → web_articles --fast-url）・別の切り口・型での書き直しは web_articles の組み立てを使う。
        見出しの書き直しは同じ TITLE_STYLE_PROMPT を使う"""
        text = {name: (ROOT / "scripts" / name).read_text("utf-8") for name in ("angle_articles.py", "rewrite_types.py", "rewrite_titles.py")}
        self.assertIn("w.build_prompt(", text["angle_articles.py"])
        self.assertIn("import web_articles as w", text["rewrite_types.py"])
        self.assertIn("hn.TITLE_STYLE_PROMPT", text["rewrite_titles.py"])

    def test_length_targets_follow_answer_then_stop(self):
        """長さの目安: 速報・料金は短め、決算・制度は長め（上限を下げる。下限は 1,600 字のまま。速報の種類だけ 800 字から＝2026-11-04 オーナー承認・A10）"""
        self.assertLessEqual(nt.get("breaking").length[1], 2000)
        self.assertLessEqual(nt.get("pricing").length[1], 2000)
        self.assertLessEqual(nt.get("product").length[1], 2200)
        self.assertEqual(nt.get("breaking").length, (800, 1200))
        for key in nt.TYPES:
            self.assertGreaterEqual(nt.get(key).length[0], 800 if key == "breaking" else 1600)
            self.assertLessEqual(nt.get(key).length[1], 2600)

    def test_body_starting_with_heading_is_recorded(self):
        self.assertTrue(any("見出しで始まっている" in w for w in st.warnings({"body": "## 中身\n本文です。"})))
        self.assertFalse(any("見出しで始まっている" in w for w in st.warnings({"body": "対象は契約者です。\n\n## 中身\n本文です。"})))


if __name__ == "__main__":
    unittest.main()
