"""問いかけの8つの型（2026-11-04 オーナー決定。台帳 S19）。ネット接続・LLM・投稿なし。

  1. 型の候補は生成と同じ呼び出しで出してもらう（出力の形・プロンプト）。LLM の呼び出しは増やさない
  2. 投稿のときは直近14日で少ない型から回す（2週間で各型5本以上）。直前と同じ型は続けない
  3. 選んだ型は投稿の記録（questionPattern）に残る。予約・速報・手で出す投稿のどの経路も
  4. 型を使っても今の門番（sns_rules.check_post）にそのまま通る。怒りを誘う問いは出さない
  5. 毎日の見張りは型の無い投稿を知らせる。流入の報告は型ごとに 1000表示あたりのコメント・訪問を出す

  python3 -m unittest tests.test_question_patterns
"""
from __future__ import annotations

import random
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import question_patterns as qp  # noqa: E402
import sns_rules  # noqa: E402
import threads_post as tp  # noqa: E402

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
LINK = "https://ai-tech-express.jp/articles/acme-tool"
VIDEO = [{"type": "video", "url": "https://ai-tech-express.jp/video/acme-tool-0123456789.mp4", "width": 1920, "height": 1080}]
ART = {"slug": "acme-tool", "title": "Acme が新しいAIツールを公開", "genreSlug": "ai", "_virtual": True}

# 型ごとの見本（提案の見本。どれも記事の事実だけ）
SAMPLES = {
    "ab": ("会議の資料づくりに追われている人、これは読んでおきたい話です。",
           "要約すると、Shopifyが社内の会議にスライドだけを持ち込むことを禁止しました。\n代わりに、AIで作った動く試作品を持参するルールです。",
           "会議で伝わるのは、きれいなスライドと動く試作品のどちらだと感じますか？"),
    "tradeoff": ("Discordの公開サーバーに書き込んでいる人、設定を見直す時期です。",
                 "要約すると、Discordが大きな公開サーバーの会話を検索で見つかるようにします。\n日本でも初期設定はオンです。",
                 "自分の書き込みが検索で見つかるとしたら、公開のままにしますか、外しますか？"),
    "line": ("小中学生の子どもがいる人、学校でのAIの扱いが変わります。",
             "要約すると、Googleが答えを見せずに考え方を導く学習機能を始めました。\n生徒の利用は学校が有効にするまでオフです。",
             "子どもの宿題にAIを使うなら、どこまで任せてよいと考えますか？"),
    "price": ("AIを仕事で毎日使っている人、料金プランの名前が変わりました。",
              "要約すると、AnthropicがClaudeの月200ドルのプランを「Ultra」に改名しました。\n価格は据え置きです。",
              "仕事で使うAIに、自分のお金で毎月いくらまでなら出せますか？"),
    "count": ("職場で生成AIを使い始めた人、国の職員の利用調査が出ました。",
              "要約すると、デジタル庁が職員向けの生成AIの利用調査を公表しました。\n利用者の71.7%が満足と答えています。",
              "職場で生成AIを使うようになって、1週間でどれくらい時間が浮きましたか？"),
    "expert": ("ランクル好きの人、FJの顔つきを選べるようになります。",
               "要約すると、トヨタが新型「ランドクルーザーFJ」に丸目のライトのオプションを用意します。\n発売は2027年1月以降の予定です。",
               "ランクルFJを選ぶなら、丸目と標準の顔のどちらにしますか？"),
    "japan": ("タクシーをよく使う人、日本でも無人の車が走り始めます。",
              "要約すると、トヨタとWaymoが2027年に日本で自動運転タクシーを始めると発表しました。\n車両はトヨタのEVです。",
              "運転手のいないタクシーが来たら、ためらわずに乗れますか？"),
    "future": ("デザインの仕事をしている人、業界の厳しい数字が出ています。",
               "要約すると、2026年度上半期のデザイン業の倒産は41件で過去最多でした。\n背景には生成AIによる内製化があるとしています。",
               "デザインの仕事は、これから減ると感じますか、形が変わると感じますか？"),
}


def reset_run() -> None:
    qp._RUN_PATTERNS.clear()


def rec(pattern: str, hours_ago: float, **extra) -> dict:
    when = (NOW - timedelta(hours=hours_ago)).isoformat()
    return {"postedAt": when, "dueAt": when, "format": "classic", "question": True, "questionText": "…？", "questionPattern": pattern, **extra}


class GenerationTest(unittest.TestCase):
    def test_prompt_lists_all_eight_patterns_and_the_rules(self):
        import threads_hooks

        block = threads_hooks.prompt_block(recent=[])
        for p in qp.PATTERNS:
            self.assertIn(f"- {p['key']}（{p['name']}）", block)
        self.assertIn("threads_questions", block)
        self.assertIn("無理に型に当てはめない", block)
        self.assertIn("賛成か反対かは聞かない", block)
        self.assertEqual(len(qp.KEYS), 8)

    def test_schema_has_question_candidates_in_every_generator(self):
        import main as m
        import roundup
        import web_articles as w

        for schema in (m.ARTICLE_SCHEMA, w.WEB_SCHEMA, w.TREND_SCHEMA, roundup.SCHEMA):
            self.assertIn("threads_questions", schema["properties"])
            self.assertIn("threads_questions", schema["required"])
            self.assertEqual(schema["properties"]["threads_questions"]["items"]["properties"]["pattern"]["enum"], qp.ALL_KEYS)
        for path in ("scripts/main.py", "scripts/web_articles.py", "scripts/roundup.py"):
            self.assertIn("question_patterns.record_fields(data)", (ROOT / path).read_text("utf-8"), path)

    def test_record_fields_cleans_llm_output(self):
        data = {"threads_questions": [
            {"pattern": "price", "question": "仕事用のパソコン、自分で買うならいくらまで出せますか？"},
            {"pattern": "price", "question": "同じ型の2つ目は落とす？"},
            {"pattern": "bogus", "question": "知らない型は落とす？"},
            {"pattern": "count", "question": ""},
            {"pattern": "ab", "question": "SNSを開いたとき、ふだん見るのはおすすめとフォロー中のどちらですか？"},
        ]}
        self.assertEqual([c["pattern"] for c in qp.record_fields(data)["threadsQuestions"]], ["price", "ab"])
        self.assertEqual(qp.record_fields({}), {})


class RotationTest(unittest.TestCase):
    def setUp(self):
        reset_run()

    def tearDown(self):
        reset_run()

    def test_least_used_pattern_wins_and_last_is_not_repeated(self):
        posted = {"a": rec("ab", 50), "b": rec("ab", 40), "c": rec("price", 30), "d": rec("count", 2)}
        cands = [{"pattern": k, "question": f"{k}の問い？"} for k in ("ab", "price", "count", "line")]
        self.assertEqual(qp.pick(cands, posted, NOW)["pattern"], "line")  # 0本
        two = [{"pattern": "ab", "question": "a？"}, {"pattern": "price", "question": "p？"}]
        self.assertEqual(qp.pick(two, posted, NOW)["pattern"], "price")  # ab は2本・price は1本
        only_last = [{"pattern": "count", "question": "c？"}, {"pattern": "ab", "question": "a？"}]
        self.assertEqual(qp.pick(only_last, posted, NOW)["pattern"], "ab")  # 直前の count は続けない
        old = {"z": rec("line", 24 * 20)}  # 14日より前は数えない
        self.assertEqual(qp.usage(old, NOW)["line"], 0)

    def test_plain_only_when_no_pattern_fits(self):
        cands = [{"pattern": "plain", "question": "ふだん使う地図アプリは何ですか？"}, {"pattern": "japan", "question": "j？"}]
        self.assertEqual(qp.pick(cands, {}, NOW)["pattern"], "japan")
        self.assertEqual(qp.pick(cands[:1], {}, NOW)["pattern"], "plain")
        self.assertIsNone(qp.pick([], {}, NOW))

    def test_two_weeks_give_every_pattern_at_least_five_posts(self):
        """1日7本×14日。記事ごとに自然に合う型は2〜3個（合いやすさに差をつける）。それでも各型5本以上になる"""
        rng = random.Random(4)
        weights = {"ab": 5, "tradeoff": 3, "line": 2, "price": 2, "count": 4, "expert": 1.5, "japan": 1.2, "future": 1.5}
        posted: dict = {}
        for i in range(14 * 7):
            when = NOW - timedelta(hours=(14 * 7 - i) * 3.3)
            fits: list[str] = []
            while len(fits) < rng.choice((2, 3)):
                k = rng.choices(list(weights), weights=list(weights.values()))[0]
                if k not in fits:
                    fits.append(k)
            cands = [{"pattern": k, "question": f"{k}{i}？"} for k in fits]
            choice = qp.pick(cands, posted, when)
            posted[f"s{i}"] = rec(choice["pattern"], (NOW - when).total_seconds() / 3600)
        counts = qp.usage(posted, NOW)
        self.assertTrue(all(n >= qp.TARGET_PER_WINDOW for n in counts.values()), counts)
        seq = [r["questionPattern"] for r in sorted(posted.values(), key=lambda r: r["postedAt"])]
        self.assertFalse(any(a == b for a, b in zip(seq, seq[1:]) if a != "plain"), "同じ型が続いた")

    def test_run_memory_rotates_within_one_run(self):
        cands = [{"pattern": k, "question": f"{k}？"} for k in qp.KEYS]
        picked = []
        for _ in range(8):
            c = qp.pick(cands, {}, NOW)
            qp.note_used(c["pattern"])
            picked.append(c["pattern"])
        self.assertEqual(sorted(picked), sorted(qp.KEYS))


class ComposeAndRecordTest(unittest.TestCase):
    def setUp(self):
        reset_run()

    def tearDown(self):
        reset_run()

    def article(self) -> dict:
        hook, summary, _ = SAMPLES["price"]
        return {
            "slug": "claude-ultra", "title": "Claudeの月200ドルのプランが「Ultra」に改名", "genreSlug": "ai", "threadsStyle": 3,
            "threadsPost": f"{hook}\n\n{summary}\n\n{SAMPLES['price'][2]}",
            "threadsQuestions": [{"pattern": "price", "question": SAMPLES["price"][2]},
                                 {"pattern": "count", "question": "仕事でAIに話しかける回数は、1日に何回くらいですか？"}],
            "description": "AnthropicがClaudeの月200ドルのプランをUltraに改名した。", "highlights": ["価格は据え置き"],
        }

    def test_compose_uses_rotation_and_records_pattern(self):
        posted = {"x": rec("price", 3)}  # 直前は price → count の問いを選ぶ
        with patch.object(qp, "load_posted", return_value=posted), patch.object(tp, "llm_question", side_effect=AssertionError("no LLM")):
            article = self.article()
            text = tp.compose(article, [], use_llm=True)
        self.assertTrue(text.endswith("1日に何回くらいですか？"), text)
        self.assertEqual(article["_question_pattern"], "count")
        self.assertEqual(tp.question_pattern_fields(article, article["_question"]), {"questionPattern": "count"})
        self.assertEqual(tp.question_pattern_fields(article, ""), {})

    def test_fallback_question_is_classified(self):
        article = {"slug": "old", "title": "Acmeが会議AIを公開", "genreSlug": "ai", "threadsPost": "古い形の紹介文"}
        with patch.object(qp, "load_posted", return_value={}), patch.object(tp, "llm_question", return_value=("", "")):
            tp.compose(article, [], use_llm=True)
        self.assertIn(article["_question_pattern"], qp.ALL_KEYS)
        with patch.object(qp, "load_posted", return_value={}), \
                patch.object(tp, "llm_question", return_value=("留守番電話のメッセージ、聞くのと文字で読むのと、どちらが楽ですか？", "japan")):
            article = {"slug": "old2", "title": "KPNが留守電AIを開始", "genreSlug": "ai", "threadsPost": "古い形の紹介文"}
            tp.compose(article, [], use_llm=True)
        self.assertEqual(article["_question_pattern"], "japan")

    def test_every_live_path_records_question_pattern(self):
        for path in ("scripts/threads_post.py", "scripts/breaking_post.py", "scripts/post_now.py"):
            self.assertIn("question_pattern_fields(", (ROOT / path).read_text("utf-8"), path)
        self.assertIn('fixed_pattern=str(entry.get("questionPattern")', (ROOT / "scripts/bluesky_post.py").read_text("utf-8"))

    def test_classify(self):
        cases = {
            "仕事用のパソコン、自分で買うならいくらまで出せますか？": "price",
            "子どもの宿題にAIを使うなら、どこまで任せてよいと考えますか？": "line",
            "デザインの仕事は、これから減ると感じますか、形が変わると感じますか？": "future",
            "運転手のいないタクシーが来たら、ためらわずに乗れますか？": "japan",
            "AIの答えに広告が混ざるとしたら、それでも調べものはAIで済ませますか？": "tradeoff",
            "右クリックのメニューで、ふだん本当に使う項目はいくつありますか？": "count",
            "SNSを開いたとき、ふだん見るのはおすすめとフォロー中のどちらですか？": "ab",
            "ふだん使う地図アプリは何ですか？": "plain",
        }
        for q, key in cases.items():
            self.assertEqual(qp.classify(q), key, q)


class GateTest(unittest.TestCase):
    def test_every_pattern_sample_passes_the_gate(self):
        for key, (hook, summary, question) in SAMPLES.items():
            body = f"{hook}\n\n{summary}\n\n{question}"
            text = f"{body}\n\n詳しくはこちら👇\n{LINK}?utm_source=threads&utm_medium=social&utm_campaign=classic"
            self.assertEqual(sns_rules.check_post("threads", text, VIDEO, ART, measurer=lambda url: (1200, 675)), [], key)
            self.assertTrue(tp.question_ok(question), key)
            self.assertEqual(qp.classify(question) if key != "expert" else "ab", "ab" if key == "expert" else key)

    def test_rage_bait_and_old_rules_still_rejected(self):
        for bad in ["AIに仕事を奪われるのは許せないと思いますか？", "こんな値上げ、ありえないと思いませんか？",
                    "AIの導入に賛成ですか、反対ですか？", "どちらが正しいと思いますか？",
                    "この機能、使ってみたいですか？", "コメントで教えてください？"]:
            self.assertFalse(tp.question_ok(bad), bad)
        self.assertTrue(qp.question_problems("こんな値上げ、ありえないと思いませんか？"))
        self.assertFalse(qp.question_problems(SAMPLES["line"][2]))


class AuditAndReportTest(unittest.TestCase):
    def test_audit_flags_new_post_without_pattern(self):
        import owner_rules_audit as audit

        now = audit.QUESTION_PATTERN_SINCE + timedelta(hours=12)
        at = (now - timedelta(hours=1)).isoformat()
        posted = {"posted": {
            "ok": {"postedAt": at, "format": "classic", "question": True, "questionText": "q？", "questionPattern": "ab"},
            "missing": {"postedAt": at, "format": "classic", "question": True, "questionText": "q？"},
            "bogus": {"postedAt": at, "format": "classic", "question": True, "questionText": "q？", "questionPattern": "nope"},
            "digest-20261105": {"postedAt": at, "style": "digest", "format": "digest", "question": False},
            "quake": {"postedAt": at, "format": "classic", "question": False},
            "old": {"postedAt": (audit.QUESTION_PATTERN_SINCE - timedelta(hours=1)).isoformat(), "question": True},
        }}
        found = audit.question_pattern_problems(posted, now)
        self.assertEqual(len(found), 2, found)
        self.assertTrue(all(f.startswith("[S19]") for f in found))
        self.assertTrue(any("missing" in f for f in found) and any("bogus" in f for f in found))

    def test_inflow_report_per_thousand_by_pattern(self):
        import inflow_report as r

        posts = [
            {"pattern": "ab", "impressions": 1000, "comments": 2, "visits": 10, "pending": False},
            {"pattern": "ab", "impressions": 1000, "comments": 0, "visits": 6, "pending": False},
            {"pattern": "price", "impressions": 0, "comments": 0, "visits": 0, "pending": False},
            {"pattern": "count", "impressions": 500, "comments": 9, "visits": 9, "pending": True},  # 予約中は数えない
            {"pattern": "", "impressions": 500, "comments": 1, "visits": 1, "pending": False},      # 型の記録なし
        ]
        groups = {g["pattern"]: g for g in r.pattern_breakdown(posts)}
        self.assertEqual(set(groups), {"ab", "price"})
        self.assertEqual((groups["ab"]["n"], groups["ab"]["commentsPer1k"], groups["ab"]["visitsPer1k"]), (2, 1.0, 8.0))
        self.assertIsNone(groups["price"]["commentsPer1k"])
        data = {"date": "2026-11-03", "views": 3, "visitors": 3, "bySource": [{"source": "threads", "views": 3, "visitors": 3}],
                "rows": [{"page": "a", "source": "threads", "campaign": "classic", "content": "", "views": 3, "visitors": 3}]}
        posted = {"a": {"dueAt": "2026-11-03T01:00:00+00:00", "format": "classic", "questionPattern": "tradeoff",
                        "metrics": {"views": 600, "comments": 2}, "ownReplies": 0}}
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            r.show(data, posted, {})
        self.assertIn("問いかけの型ごと（1000表示あたりのコメント・訪問）: 便利さと引き換え 1本・表示600・コメント3.3・訪問5.0", out.getvalue())


if __name__ == "__main__":
    unittest.main()
