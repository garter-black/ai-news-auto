"""読者像（scripts/persona.py）に沿った記事・SNS・ジャンル配分の決まりを確かめる。外部 API は呼ばない。"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import main as m  # noqa: E402
import persona  # noqa: E402
import quality  # noqa: E402
import threads_hooks  # noqa: E402
import threads_post as tp  # noqa: E402
import web_articles as w  # noqa: E402

JST = timezone(timedelta(hours=9))


class SportsScopeTest(unittest.TestCase):
    """オーナー決定: 日本のスポーツだけ。海外で活躍する日本人選手は扱う。世界的な大会は海外選手も扱う"""

    def test_japanese_leagues_and_athletes_abroad_are_in(self):
        for title in ["プロ野球・日本シリーズ第7戦の日程を発表", "Jリーグ、来季の開幕日を発表", "大相撲九州場所の番付発表",
                      "大谷翔平が2打席連続本塁打", "三笘薫がプレミアリーグで先制ゴール", "なでしこジャパン、代表メンバー発表"]:
            self.assertTrue(persona.sports_scope(title, japanese_source=False)[0], title)

    def test_foreign_athletes_and_leagues_are_out(self):
        for title in ["レイカーズが開幕3連勝", "セリエA第10節の結果まとめ", "ドジャースが優勝パレード", "NBAファイナルの日程が決定"]:
            ok, why = persona.sports_scope(title)
            self.assertFalse(ok, title)
            self.assertIn("海外", why)

    def test_global_events_include_foreign_athletes(self):
        for title in ["パリ五輪、米国の選手が世界新記録", "FIFAワールドカップ、ブラジルが決勝へ", "WBC、米国代表のメンバー発表",
                      "世界陸上、ケニアの選手が金メダル", "ラグビーW杯の組み合わせ決定", "世界水泳で豪州が優勝"]:
            self.assertTrue(persona.sports_scope(title, japanese_source=False)[0], title)

    def test_unknown_story_depends_on_source(self):
        self.assertTrue(persona.sports_scope("新しい大会の開催が決定", japanese_source=True)[0])
        self.assertFalse(persona.sports_scope("新しい大会の開催が決定", japanese_source=False)[0])

    def test_broad_prefilter_drops_foreign_sports(self):
        base = {"summary": "", "section": "sports", "source_slug": "jfa"}
        self.assertIsNone(w.broad_prefilter({**base, "title": "SAMURAI BLUE（日本代表）メンバー発表のお知らせ"}))
        reason = w.broad_prefilter({**base, "title": "チャンピオンズリーグ決勝の会場が決定しました"})
        self.assertIn("スポーツの対象外", reason or "")

    def test_sports_rule_is_in_genre_prompt(self):
        import genres
        self.assertIn(persona.SPORTS_SCOPE_RULE, genres.SPORTS.topic_guard)
        self.assertIn(persona.SPORTS_SCOPE_RULE, genres.SPORTS.persona)


class GenreBalanceTest(unittest.TestCase):
    def test_priority_lanes_first_and_sports_last(self):
        lanes = {slug: [] for slug in ["ai", *w.BROAD_SLUGS]}
        for hour in range(24):
            order = w.lane_order(lanes, datetime(2026, 11, 2, hour, tzinfo=JST))
            self.assertEqual(order[:3], ["ai", "business", "public"])
            self.assertEqual(order[-1], "sports")
            self.assertEqual(sorted(order), sorted(lanes))  # どのレーンも外さない（本数は減らさない）

    def test_priority_lanes_get_two_turns(self):
        self.assertEqual(w.lane_turns("business"), 2)
        self.assertEqual(w.lane_turns("public"), 2)
        self.assertEqual(w.lane_turns("sports"), 1)

    def test_money_topics_sorted_first_in_lane(self):
        self.assertEqual(w.persona_rank({"title": "中小企業向けの補助金、申請受付を開始"}), 0)
        self.assertEqual(w.persona_rank({"title": "新しい物流センターが完成"}), 1)

    def test_sports_daily_cap_counts_today_only(self):
        now = datetime(2026, 11, 2, 15, tzinfo=JST)
        today = now.astimezone(timezone.utc).isoformat()
        yesterday = (now - timedelta(days=1)).astimezone(timezone.utc).isoformat()
        state = {"processed_web": {
            "a": {"slug": "a", "section": "sports", "at": today},
            "b": {"slug": "b", "section": "sports", "at": today},
            "c": {"slug": "c", "section": "business", "at": today},
            "d": {"slug": "d", "section": "sports", "at": yesterday},
            "e": {"section": "sports", "at": today, "skipped": True},
        }}
        self.assertEqual(w.sports_today(state, now), 2)
        self.assertTrue(3 <= persona.SPORTS_DAILY_CAP <= 5)


def web_record(**extra):
    return {
        "sourceType": "web",
        "title": "新しい会計サービスを個人事業主向けに提供開始と発表",
        "lede": "リード", "highlights": ["要点"],
        "body": "## 何が変わる？\n公式発表によると、月額980円で使えます。",
        "easy": "## ひとことで言うと\n月額980円の会計サービスです。",
        **extra,
    }


class WorkMoneyTest(unittest.TestCase):
    def test_schema_and_prompts_ask_for_work_money(self):
        self.assertIn("work_money", m.ARTICLE_SCHEMA["required"])
        self.assertIn("work_money", w.WEB_SCHEMA["required"])
        self.assertIn("work_money", w.TREND_SCHEMA["required"])
        self.assertIn(persona.SYSTEM_BLOCK, m.SYSTEM_INSTRUCTION_BASE)
        item = {"source_name": "公式", "title": "t", "published_at": datetime.now(timezone.utc), "url": "https://example.com/a"}
        self.assertIn(persona.WORK_MONEY_FIELD, w.build_prompt(item, "本文"))

    def test_clean_work_money(self):
        self.assertIsNone(persona.clean_work_money(None))
        self.assertIsNone(persona.clean_work_money({"relevance": "high", "points": ["", "  "]}))
        wm = persona.clean_work_money({"relevance": "?", "points": ["・a", "a", "b", "c", "d"]})
        self.assertEqual(wm, {"relevance": "medium", "points": ["a", "b", "c"]})

    def test_gate_requires_work_money_on_new_web_articles(self):
        problems = quality.structural_problems(web_record(), "ai")
        self.assertTrue(any("work_money" in p for p in problems))
        ok = web_record(workMoney={"relevance": "high", "points": ["個人事業主は月額980円で帳簿づけを試せます。"]})
        self.assertEqual(quality.structural_problems(ok, "ai"), [])

    def test_gate_rejects_investment_advice_and_hype(self):
        for bad in ["この銘柄は今が買い時です。", "副業で確実に稼げる方法です。"]:
            rec = web_record(workMoney={"relevance": "high", "points": [bad]})
            self.assertTrue(any("投資助言" in p for p in quality.structural_problems(rec, "ai")), bad)

    def test_fact_check_sees_work_money_before_body(self):
        rec = web_record(workMoney={"relevance": "high", "points": ["月額980円で試せます。"]}, body="x" * 20000)
        text = quality._article_for_check(rec)
        self.assertIn(persona.WORK_MONEY_HEADING, text)
        self.assertLess(text.index(persona.WORK_MONEY_HEADING), text.index("本文:"))
        self.assertIn("仕事・お金にどう活かせる", quality.FACT_PROMPT)


class SnsPersonaTest(unittest.TestCase):
    def test_hooks_extend_the_original_13(self):
        original = ["question", "finally", "number", "before_after", "contrarian", "empathy", "conclusion", "callout",
                    "surprise", "breaking", "caution", "free", "future"]
        self.assertEqual(threads_hooks.HOOK_KEYS[:13], original)
        self.assertIn("use_case", threads_hooks.HOOK_KEYS)
        self.assertIn("money", threads_hooks.HOOK_KEYS)
        self.assertEqual(len(set(threads_hooks.HOOK_KEYS)), len(threads_hooks.HOOK_KEYS))
        block = threads_hooks.prompt_block(recent=[])
        self.assertIn(persona.SNS_BLOCK, block)

    def test_work_line_only_when_backed_and_relevant(self):
        article = {"slug": "s", "workMoney": {"relevance": "high", "points": ["経理担当は月末の照合作業を試せます。"]}}
        self.assertEqual(tp.work_line(article), "経理担当は月末の照合作業を試せます。")
        self.assertEqual(tp.work_line({**article, "workMoney": {"relevance": "low", "points": ["影響は小さいです。"]}}), "")
        self.assertEqual(tp.work_line({**article, "workMoney": {"relevance": "high", "points": ["今が買い時です。"]}}), "")
        self.assertEqual(tp.work_line({"slug": "s"}), "")

    def test_use_case_opener_needs_available_and_high_relevance(self):
        base = {"slug": "s", "seoTitle": "会計アプリ｜料金", "verdict": {"status": "available", "japan": "ok", "price": "月980円", "free": False}}
        keys = [k for k, _ in tp.openers({**base, "workMoney": {"relevance": "high", "points": ["x"]}})]
        self.assertIn("use_case", keys)
        self.assertIn("money", keys)
        soon = {**base, "verdict": {**base["verdict"], "status": "soon"}, "workMoney": {"relevance": "high", "points": ["x"]}}
        self.assertNotIn("use_case", [k for k, _ in tp.openers(soon)])
        self.assertNotIn("use_case", [k for k, _ in tp.openers(base)])


class CrosspostPersonaTest(unittest.TestCase):
    def test_pick_top_uses_persona_topics_without_sports(self):
        from unittest.mock import patch
        import crosspost_drafts  # noqa: F401  （ペルソナの切り口がプロンプトに入っていること）
        import digest
        now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
        at = (now - timedelta(hours=1)).isoformat()
        rows = [
            {"slug": "ai", "genreSlug": "ai", "generatedAt": at, "body": "x" * 3000},
            {"slug": "money", "genreSlug": "investment", "generatedAt": at, "body": "x" * 3000},
            {"slug": "tax", "genreSlug": "public", "title": "ふるさと納税の新しい仕組み", "generatedAt": at, "body": "x"},
            {"slug": "sports", "genreSlug": "sports", "title": "日本代表の試合日程", "generatedAt": at, "body": "x" * 6000},
            {"slug": "fun", "genreSlug": "entertainment", "generatedAt": at, "body": "x" * 6000},
        ]
        with patch.object(digest, "load_articles", return_value=rows), patch.object(digest, "is_distribution_eligible", return_value=True):
            picked = [a["slug"] for a in digest.pick_top(24, 5, now, persona_topics=True)]
            legacy = [a["slug"] for a in digest.pick_top(24, 5, now)]
        self.assertEqual(set(picked), {"ai", "money", "tax"})
        self.assertIn("sports", legacy)  # 既存の呼び出し（メルマガなど）は変えない
        self.assertIn("副業・独立を考えている人", crosspost_drafts.SYSTEM)
        self.assertNotIn("開発者）", crosspost_drafts.SYSTEM)


class PlainStyleTest(unittest.TestCase):
    def test_shared_tone_reaches_article_and_sns_prompts(self):
        self.assertIn(persona.PLAIN_STYLE, m.SYSTEM_INSTRUCTION_BASE)
        self.assertNotIn("ざっくり言うと", persona.WORK_MONEY_FIELD + persona.PLAIN_STYLE + persona.SNS_BLOCK)
        self.assertIn("要約すると", persona.PLAIN_STYLE)
        self.assertIn("硬い言い回し", persona.SNS_BLOCK)
        import evergreen
        self.assertIn(persona.PLAIN_STYLE, evergreen.SYSTEM)

    def test_crosspost_style_check(self):
        import crosspost_drafts as c
        plain = ("要約すると、会計ソフトが月額980円で使えるようになりました。\n\n"
                 "たとえば、毎月の領収書の整理が楽になります。つまり、手作業が減ります。")
        self.assertEqual(c.style_problems(plain), [])
        stiff = ("本サービスにおいては、利用者が多様な業務を効率的に処理することが可能となっており、今後の展開を見据えた設計となっております。\n\n"
                 "記事では料金への言及はありませんが、注意が必要と言えるでしょう。")
        problems = " ".join(c.style_problems(stiff))
        self.assertIn("文が長い", problems)
        self.assertIn("使わない言い回し", problems)
        self.assertIn("要約すると", problems)
        self.assertEqual(c.style_problems("**要約すると**、短い文です。"), [])
        casual = " ".join(c.style_problems("要約すると、ざっくり言えば便利になります。"))
        self.assertIn("ざっくり", casual)
        self.assertIn("「要約すると、」で始まる", c.SYSTEM)
        self.assertNotIn("「ざっくり言うと」", c.SYSTEM)
        self.assertIn(persona.PLAIN_STYLE_EXAMPLE, c.SYSTEM)
        self.assertIn("サイソクニュース", c.footer({"slug": "s", "title": "t"}, "hatena"))


if __name__ == "__main__":
    unittest.main()
