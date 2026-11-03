"""2026-11-03 の記事の質の改善（無料モデルのまま）の検証。外部 API は呼ばない。

本文を書く段の tier（write）・無料枠の回数・FAQ と今日やることの必須化と書き足し・言葉のチェック（中国語の混入・訳し残しの英単語・誤訳）
と安い手直し・紹介文の「要約すると、」・判定カードと本文の食い違い・料金ページの差分の貼り付け・見張りの題名。
"""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import glossary  # noqa: E402
import llm  # noqa: E402
import page_watch  # noqa: E402
import quality  # noqa: E402
import web_articles as w  # noqa: E402

POST_OK = "クラウド担当の人に関係します。\n\n要約すると、NVIDIAが新しいツールを公開しました。\n設定が数時間で終わります。\n\nあなたの会社では使っていますか？"


def record(**extra) -> dict:
    base = {
        "title": "NVIDIAが次世代GPU向けの新ツール『AICR』を公開 設定が数時間に",
        "seoTitle": "AICRとは？使い方と料金",
        "lede": "NVIDIAの新しいツールで、止まったAI学習を人手なしで再開できるようになりました。",
        "description": "NVIDIAの新しいツールの説明です。",
        "highlights": ["NVIDIAがAICRを公開した。", "設定が数時間で終わる。", "無料で使える。"],
        "body": "## 概要\nNVIDIAの新しいツールで、止まったAI学習を人手なしで再開できるようになりました。\n\n## 日本で使える？\n使えます。誰でも入手できます。\n",
        "easy": "## ひとことで言うと\n新しい道具です。",
        "threadsPost": POST_OK,
        "threadsStyle": 3,
        "verdict": {"status": "available", "japan": "ok", "price": "無料", "free": True, "audience": "開発者", "take": "まず試してみる。"},
        "genreSlug": "ai",
        "sourceType": "web",
    }
    base.update(extra)
    return base


class LanguageChecks(unittest.TestCase):
    def test_simplified_and_chinese_phrases(self):
        self.assertEqual(quality.simplified_chars("ロボットはどう进化したの？"), ["进"])
        self.assertEqual(quality.simplified_chars("日本語の文章です。経済・発表・進化"), [])
        issues = quality.sentence_language_issues("不过、Microsoft Marketplace自体は日本からも利用可能です。")
        self.assertTrue(any("簡体字" in i for i in issues))
        self.assertTrue(quality.sentence_language_issues("但是、日本では使えます。"))
        # 「100万カ所以上」の「所以」は日本語
        self.assertEqual(quality.sentence_language_issues("100万カ所以上の発電所があります。"), [])

    def test_half_translated_english(self):
        issues = quality.sentence_language_issues("製品サポートの対象外となる初期 adopter向けのパスです。")
        self.assertTrue(any("adopter" in i for i in issues))
        # 製品名・コマンド・英語の補足・引用した画面の文字は見ない
        for ok in ("聞き手はkenmo氏が務めています。", "npmまたはpipで入れます。", "必要な調査（research）を行います。",
                   "「Add source」を押します。", "`aicr recipe` を実行します。", "Codex harnessで肩代わりします。"):
            self.assertEqual(quality.sentence_language_issues(ok), [], ok)

    def test_glossary_mistranslation(self):
        self.assertTrue(glossary.mistranslations("インプレッションのコストが下がる", "lower inference cost"))
        self.assertEqual(glossary.mistranslations("インプレッション数が伸びた", "ad impressions and inference"), [])
        self.assertIn("推論", glossary.prompt_block())
        self.assertTrue(quality.sentence_language_issues("一般的な可用性になりました。", "now in general availability"))

    def test_language_problems_cover_all_reader_fields(self):
        rec = record(faq=[{"q": "AICRは这样に使える？", "a": "無料です。"}], todayActions=["初期 adopter向けの手順を確かめる"])
        fields = {x["field"] for x in quality.language_issues(rec)}
        self.assertEqual(fields, {"faq.q", "todayActions"})
        self.assertTrue(quality.language_problems(rec))
        self.assertTrue(quality.structural_problems(rec, "ai"))

    def test_repair_language_replaces_only_bad_sentences(self):
        rec = record(body="## 概要\n初期 adopter向けのパスです。2026年に公開されました。\n\n不过、日本でも使えます。\n")
        fixes = {"fixes": [{"id": 0, "text": "早くから使い始める人向けのパスです。"}, {"id": 1, "text": "ただし、日本でも使えます。"}]}
        with patch.object(quality.llm, "generate_json", return_value=(fixes, "gemini-lite")) as call:
            found, fixed, model = quality.repair_language(rec)
        self.assertEqual((found, fixed, model), (2, 2, "gemini-lite"))
        self.assertEqual(call.call_args.kwargs["tier"], quality.CHECK_TIER)
        self.assertIn("早くから使い始める人向けのパスです。2026年に公開されました。", rec["body"])
        self.assertIn("ただし、日本でも使えます。", rec["body"])
        self.assertEqual(quality.language_issues(rec), [])

    def test_repair_language_rejects_changed_numbers_or_remaining_issue(self):
        rec = record(body="## 概要\n初期 adopter向けに2026年から提供です。\n")
        bad = {"fixes": [{"id": 0, "text": "早くから使う人向けに2027年から提供です。"}]}
        with patch.object(quality.llm, "generate_json", return_value=(bad, "m")):
            self.assertEqual(quality.repair_language(rec)[1], 0)
        self.assertIn("adopter", rec["body"])


class SummaryOpener(unittest.TestCase):
    def test_ok_and_missing(self):
        self.assertEqual(quality.summary_opener_problems(record()), [])
        rec = record(threadsPost=POST_OK.replace("要約すると、", ""))
        self.assertTrue(quality.summary_opener_problems(rec))
        # 古い形（型2以下）の紹介文は見ない
        self.assertEqual(quality.summary_opener_problems(record(threadsPost="古い形の紹介文", threadsStyle=2)), [])

    def test_fix_mechanically(self):
        rec = record(threadsPost=POST_OK.replace("要約すると、", "要約すると："))
        self.assertTrue(quality.fix_summary_opener(rec))
        self.assertEqual(quality.summary_opener_problems(rec), [])

    def test_llm_repair_when_blocks_broken(self):
        rec = record(threadsPost="一つのかたまりだけの紹介文です。")
        with patch.object(quality.llm, "generate_json", return_value=({"threads_post": POST_OK}, "lite")):
            notes = quality.pre_gate_repair(rec, log=lambda *_: None)
        self.assertEqual(rec["threadsPost"], POST_OK)
        self.assertTrue(any("紹介文" in n for n in notes))


class VerdictConsistency(unittest.TestCase):
    def test_soon_but_body_says_rolling_out(self):
        rec = record(verdict={"status": "soon", "japan": "unknown", "price": "不明", "free": None, "audience": "開発者", "take": "待つ。"},
                     lede="新機能は、すべてのユーザーに順次提供されています。",
                     body="## 概要\n新機能が出ました。\n\n## 日本で使える？\nまだ分かりません。\n")
        self.assertTrue(quality.verdict_problems(rec))
        notes = quality.align_verdict(rec)
        self.assertEqual(rec["verdict"]["status"], "limited")
        self.assertTrue(notes)
        self.assertEqual(quality.verdict_problems(rec), [])

    def test_future_sentence_is_not_rolling_out(self):
        rec = record(verdict={"status": "soon", "japan": "unknown", "price": "不明", "free": None, "audience": "a", "take": "b"},
                     lede="来月から順次提供される予定です。")
        notes = quality.align_verdict(rec)
        self.assertFalse(any("提供状況" in n for n in notes))
        self.assertEqual(rec["verdict"]["status"], "soon")

    def test_japan_unknown_aligned_and_contradiction_flagged(self):
        rec = record(verdict={"status": "available", "japan": "unknown", "price": "無料", "free": True, "audience": "a", "take": "b"},
                     body="## 日本で使える？\n日本からも使えます。ただし英語のみです。\n")
        quality.align_verdict(rec)
        self.assertEqual(rec["verdict"]["japan"], "partial")
        rec = record(body="## 日本で使える？\n日本ではまだ使えません。\n")
        self.assertTrue(quality.verdict_problems(rec))  # 判定は ok なのに本文は「使えません」

    def test_sports_and_public_cards_skipped(self):
        rec = record(verdict={"status": "soon", "japan": "ok", "kind": "public", "price": "", "free": None, "audience": "", "take": ""},
                     lede="受付は順次開始されています。")
        self.assertEqual(quality.verdict_problems(rec), [])


class RawDiff(unittest.TestCase):
    def test_raw_diff_detected(self):
        rec = record(body="## 概要\nPlan: Pro, Feature: Monthly API credits, $100 with Pro 100\n")
        self.assertTrue(quality.raw_diff_problems(rec))
        self.assertTrue(quality.raw_diff_problems(record(body="## 変更点\n- 追加: Up to $500 credits\n")))
        self.assertTrue(quality.raw_diff_problems(record(videoTitle="ChatGPTが更新: Plan: Pro, Feature: Monthly API credits")))
        self.assertEqual(quality.raw_diff_problems(record()), [])

    def test_watch_diff_title_is_japanese(self):
        title = page_watch.diff_title({"name": "ChatGPT 料金ページ", "product_name": "ChatGPT"}, [],
                                      ["Plan: Pro, Feature: Monthly API credits, $100 with Pro 100"])
        self.assertEqual(title, "ChatGPTの料金・プランのページが更新（料金の記載が変更）")
        self.assertIn("差分", w.WATCH_DIFF_NOTE)


class FaqRequiredAndFill(unittest.TestCase):
    def test_schema_requires_faq_only_for_eligible_genres(self):
        self.assertIn("faq", w.schema_for(w.WEB_SCHEMA, "ai")["required"])
        self.assertIn("today_actions", w.schema_for(w.TREND_SCHEMA, "public")["required"])
        self.assertNotIn("faq", w.schema_for(w.TREND_SCHEMA, "sports")["required"])
        self.assertNotIn("faq", w.WEB_SCHEMA["required"])  # 元の形は変えない

    def test_fill_only_missing_fields(self):
        rec = record(todayActions=["公式発表で条件を確かめる", "社内で共有する"])
        faq = [{"q": f"AICRの質問{i}は何？", "a": "本文に書かれた答えです。"} for i in range(3)]
        with patch.object(w.llm, "generate_json", return_value=({"faq": faq, "today_actions": ["x", "y"]}, "lite")) as call:
            added = w.fill_faq_actions(rec, "元記事", "")
        self.assertEqual(added, ["faq"])
        self.assertEqual(len(rec["faq"]), 3)
        self.assertEqual(rec["todayActions"], ["公式発表で条件を確かめる", "社内で共有する"])  # あるものは変えない
        self.assertEqual(call.call_args.kwargs["tier"], quality.CHECK_TIER)

    def test_no_fill_for_other_genres_or_complete(self):
        with patch.object(w.llm, "generate_json") as call:
            self.assertEqual(w.fill_faq_actions(record(genreSlug="sports"), ""), [])
            self.assertEqual(w.fill_faq_actions(record(faq=[{"q": "a？", "a": "b"}] * 3, todayActions=["a", "b"]), ""), [])
        call.assert_not_called()

    def test_clean_actions_keeps_leading_year(self):
        self.assertEqual(w.clean_actions(["2026年11月10日の発売日に店頭で確かめる。", "1. 公式ページを開く", "・社内で共有する"]),
                         ["2026年11月10日の発売日に店頭で確かめる。", "公式ページを開く", "社内で共有する"])


# 2026-11-03 の試し書き（制度）: 東京都の支援金の第7回の受付は11月9日からなのに、記事は「受付を始めています」と書いた（事実確認は100点で通った）
TOKYO_SOURCE = (
    "この度、支援金の第7回について、申請受付を開始しますのでお知らせします。\n1 申請受付について\n申請受付期間\n"
    "令和8年11月9日（月曜日）から令和9年5月31日（月曜日）まで\n申請方法\nオンライン申請（郵送による申請も可能）"
)


def tokyo_record(**extra) -> dict:
    base = dict(
        title="東京都、中小企業向けの特別高圧電力・工業用LPガス支援金の申請を受付開始　最大750万円を支給",
        lede="東京都は、支援金の第7回申請受付を始めました。直接受電する施設には最大750万円が支給されます。",
        body=("## 概要\n東京都は、支援金の第7回申請受付を始めています（東京都発表、11/2確認）。\n\n### 支援金の受付がスタート\n"
              "申請の受付期間は、令和8年11月9日から令和9年5月31日までとなっています。\n\n前回（10月1日）の受付は終わっています。\n"),
        threadsPost=POST_OK, publishedAt="2026-11-01T15:00:00+00:00", genreSlug="public",
        verdict={"status": "available", "japan": "ok", "kind": "public", "price": "最大750万円", "free": None, "audience": "中小企業",
                 "take": "要件を確かめる。", "when": "令和8年11月9日から令和9年5月31日まで"},
    )
    base.update(extra)
    return record(**base)


class FutureStart(unittest.TestCase):
    def test_dates_are_normalised(self):
        from datetime import date, datetime, timezone
        import calc_facts
        pub = datetime(2026, 11, 1, 15, tzinfo=timezone.utc)  # 日本時間 11月2日
        found = [d for d, _, _ in calc_facts.dated_mentions("令和8年11月9日から。来月1日から。2月3日まで。", pub)]
        self.assertEqual(found, [date(2026, 11, 9), date(2026, 12, 1), date(2027, 2, 3)])
        starts = quality.future_starts(TOKYO_SOURCE, pub)
        self.assertEqual([(r["label"], "受付" in r["kinds"]) for r in starts], [("11月9日", True)])
        self.assertEqual(quality.future_starts("来月1日から受付を始めます。", pub)[0]["label"], "12月1日")
        self.assertEqual(quality.future_starts("11月1日から受付を始めました。", pub), [])  # 記事の日付より前は対象外

    def test_flags_already_started_claims_only(self):
        rec = tokyo_record()
        flagged = {x["sentence"] for x in quality.future_start_issues(rec, TOKYO_SOURCE)}
        self.assertEqual(flagged, {
            "東京都は、支援金の第7回申請受付を始めました。",
            "東京都は、支援金の第7回申請受付を始めています（東京都発表、11/2確認）。",
            "### 支援金の受付がスタート",
        })
        self.assertTrue(quality.future_start_problems(rec, TOKYO_SOURCE))
        self.assertTrue(quality.structural_problems(rec, "broad", TOKYO_SOURCE))
        # 開始日を添えた言い方・予定の言い方は問題にしない
        ok = tokyo_record(lede="11月9日から受付が始まります。", body="## 概要\n第7回の受付は11月9日からスタートします。\n申請受付を始める予定です。\n")
        self.assertEqual(quality.future_start_issues(ok, TOKYO_SOURCE), [])
        # 元記事に先の開始日が無ければ何もしない
        self.assertEqual(quality.future_start_issues(rec, "申請受付を開始しました。"), [])

    def test_repair_rewrites_to_future_and_verdict_becomes_soon(self):
        rec = tokyo_record()
        fixes = {"fixes": [
            {"id": 0, "text": "東京都は、11月9日から支援金の第7回申請受付が始まります（東京都発表、11/2確認）。"},
            {"id": 1, "text": "### 11月9日から支援金の受付が始まります"},
            {"id": 2, "text": "東京都は、支援金の第7回申請受付を11月9日から始めます。"},
        ]}
        def fake(system, prompt, schema, **kwargs):
            # 直す文の順番は記事の欄の順（タイトル・リード…本文）なので、id は文の中身で合わせる
            ids = {line.split(": ", 1)[1]: int(line.split("id ")[1].split(":")[0]) for line in prompt.splitlines() if line.startswith("- id ")}
            by_text = {"始めています": fixes["fixes"][0]["text"], "スタート": fixes["fixes"][1]["text"], "始めました": fixes["fixes"][2]["text"]}
            out = [{"id": i, "text": next(v for k, v in by_text.items() if k in sentence)} for sentence, i in ids.items()]
            return {"fixes": out}, "lite"
        with patch.object(quality.llm, "generate_json", side_effect=fake):
            notes = quality.pre_gate_repair(rec, TOKYO_SOURCE, log=lambda *_: None)
        self.assertEqual(quality.future_start_issues(rec, TOKYO_SOURCE), [])
        self.assertIn("11月9日から支援金の第7回申請受付が始まります", rec["body"])
        self.assertIn("### 11月9日から支援金の受付が始まります", rec["body"])
        self.assertEqual(rec["verdict"]["status"], "soon")
        self.assertTrue(any("soon" in n for n in notes))

    def test_repair_rejects_fix_that_still_claims_started_or_adds_other_numbers(self):
        rec = tokyo_record(body="## 概要\n東京都は、支援金の第7回申請受付を始めています。\n", lede="")
        bad = {"fixes": [{"id": 0, "text": "東京都は、支援金の第7回申請受付を開始しました。"}]}
        with patch.object(quality.llm, "generate_json", return_value=(bad, "m")):
            self.assertEqual(quality.repair_language(rec, TOKYO_SOURCE)[1], 0)
        bad = {"fixes": [{"id": 0, "text": "東京都は、12月20日から支援金の第7回申請受付が始まります。"}]}
        with patch.object(quality.llm, "generate_json", return_value=(bad, "m")):
            self.assertEqual(quality.repair_language(rec, TOKYO_SOURCE)[1], 0)


class WriteTier(unittest.TestCase):
    def setUp(self):
        llm._tiers.clear()

    def tearDown(self):
        llm._tiers.clear()

    def test_write_tier_puts_strong_models_before_lite(self):
        have = set(llm.QUALITY_MODELS + llm.BULK_MODELS)
        with patch.object(llm, "available_models", return_value=have), \
                patch.object(llm, "_cloudflare_models", return_value=["cloudflare:@cf/openai/gpt-oss-120b", "cloudflare:llama"]), \
                patch.object(llm, "_groq_models", return_value=["groq:openai/gpt-oss-120b"]), \
                patch.object(llm, "_openrouter_models", return_value=["openrouter:x:free"]), \
                patch.object(llm, "_codex_models", return_value=[]):
            chosen = llm.models_for("write")
        self.assertEqual(chosen[: len(llm.QUALITY_MODELS)], llm.QUALITY_MODELS)
        lite = chosen.index("gemini-3.5-flash-lite")
        for strong in ("cloudflare:@cf/openai/gpt-oss-120b", "groq:openai/gpt-oss-120b", "openrouter:x:free"):
            self.assertLess(chosen.index(strong), lite)
        self.assertGreater(chosen.index("cloudflare:llama"), lite)
        self.assertNotIn("claude:sonnet", chosen)
        self.assertEqual(w.write_tier("bulk"), "write")
        self.assertEqual(w.write_tier("quality"), "quality")

    def test_daily_limit_and_reserve(self):
        used = {"gemini-3.5-flash": 17}
        self.assertTrue(llm.over_daily_limit("gemini-3.5-flash", "write", used))  # 速報用に3回残す
        self.assertFalse(llm.over_daily_limit("gemini-3.5-flash", "quality", used))
        self.assertTrue(llm.over_daily_limit("gemini-3.5-flash", "quality", {"gemini-3.5-flash": 20}))
        self.assertFalse(llm.over_daily_limit("gemini-3.5-flash-lite", "write", {"gemini-3.5-flash-lite": 999}))

    def test_generate_json_skips_models_over_limit(self):
        schema = {"type": "OBJECT", "properties": {"a": {"type": "STRING"}}, "required": ["a"]}
        calls = []

        def fake(model, *args, **kwargs):
            calls.append(model)
            return '{"a": "x"}'

        with patch.object(llm, "models_for", return_value=["gemini-3.5-flash", "gemini-3.5-flash-lite"]), \
                patch.object(llm, "usage_today", return_value={"gemini-3.5-flash": 20}), \
                patch.object(llm, "_record_usage"), patch.object(llm, "_call_gemini", side_effect=fake):
            data, model = llm.generate_json("s", "p", schema, tier="write")
        self.assertEqual(model, "gemini-3.5-flash-lite")
        self.assertEqual(calls, ["gemini-3.5-flash-lite"])

    def test_slow_model_cooldown(self):
        import tempfile
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as tmp, patch.object(llm, "SLOW_PATH", Path(tmp) / "slow.json"):
            now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
            llm.mark_slow("gemini-3.7-flash", now)
            self.assertTrue(llm.recently_slow("gemini-3.7-flash", now + timedelta(minutes=30)))
            self.assertFalse(llm.recently_slow("gemini-3.7-flash", now + timedelta(minutes=61)))
            self.assertFalse(llm.recently_slow("gemini-3.8-flash", now))


if __name__ == "__main__":
    unittest.main()
