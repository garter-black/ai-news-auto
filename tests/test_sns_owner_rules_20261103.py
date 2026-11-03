"""2026-11-03 オーナー承認その2 の4つ（ネット接続・投稿なし）。

  1. 1行目は読者への呼びかけ（言い回しは続けない）。【速報】は本当の速報だけ
  2. 通常枠の 12:15・21:00 は AI 以外の記事（1週間のテスト。slotTest で記録）
  3. 問いかけは記事を読まなくても答えられる好み・経験
  4. 学習の目当ては投稿1本あたりの訪問（無ければ表示 × 割合）

  python3 -m unittest tests.test_sns_owner_rules_20261103
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import inflow_report  # noqa: E402
import sns_rules  # noqa: E402
import threads_hooks  # noqa: E402
import threads_learn  # noqa: E402
import threads_post as tp  # noqa: E402

JST = timezone(timedelta(hours=9))


class CalloutTest(unittest.TestCase):
    def test_audience_from_article(self):
        cases = {
            "Hondaが初の高級電動アシストマウンテンバイク「RN0」を公開": "自転車好き",
            "トヨタのランドクルーザー「FJ」、外装オプションを新規設定": "車好き",
            "Canvaがデザインアプリ「Affinity」iPad版を公開": "iPad",
            "楽天証券の総合口座が1,500万を突破": "証券口座",
            "GoogleのGTIGがARTEXの悪用を確認": "セキュリティ担当",
        }
        for title, word in cases.items():
            person, group = threads_hooks.audience_of({"title": title, "genreSlug": "business"})
            self.assertIn(word, person + group, title)

    def test_callout_varies_and_passes_gate(self):
        article = {"slug": "a", "title": "ChatGPTに新機能", "genreSlug": "ai", "verdict": {"status": "available", "free": True}}
        seen = []
        last: list[str] = []
        for _ in range(6):
            key, line = threads_hooks.callout(article, last)
            self.assertTrue(sns_rules.callout_ok(line), line)
            self.assertNotIn(key, last)
            seen.append(key)
            last = [key]
        self.assertGreater(len(set(seen)), 1)

    def test_good_news_word_only_for_positive_news(self):
        bad = {"slug": "b", "title": "攻撃ツールARTEXの悪用を確認", "verdict": {"status": "available", "free": True}}
        for prev in ([], ["check"], ["see"], ["know"], ["keep"], ["careful"]):
            self.assertNotIn("朗報", threads_hooks.callout(bad, prev)[1])
        neutral = {"slug": "c", "title": "Acmeが研究成果を公開", "verdict": {"status": "demo"}}
        for prev in ([], ["check"], ["see"], ["know"], ["keep"]):
            self.assertNotIn("朗報", threads_hooks.callout(neutral, prev)[1])
            self.assertNotIn("確認を", threads_hooks.callout(neutral, prev)[1])

    def test_breaking_prefix_follows_cover_rule(self):
        line = "車好きの人、これは要チェックです。"
        self.assertEqual(threads_hooks.with_breaking(line, {"breaking": True}), "【速報】" + line)
        self.assertEqual(threads_hooks.with_breaking(line, {"speedMinutes": 20}), "【速報】" + line)
        self.assertEqual(threads_hooks.with_breaking("【速報】" + line, {}), line)
        self.assertEqual(threads_hooks.with_breaking("【速報】" + line, {"breaking": True, "type": "roundup"}), line)

    def test_prompt_asks_for_callout_and_answerable_question(self):
        block = threads_hooks.prompt_block([])
        self.assertIn("必ず呼びかけ型", block)
        self.assertIn("記事を読まなくても答えられる", block)
        self.assertNotIn("先に言うと、日本からはまだ使えません。", block)


class NonAiSlotTest(unittest.TestCase):
    def test_two_slots_during_test_week(self):
        def at(day, hour, minute):
            return datetime(2026, 11, day, hour, minute, tzinfo=JST).astimezone(timezone.utc)
        self.assertTrue(tp.non_ai_slot(at(4, 12, 15)))
        self.assertTrue(tp.non_ai_slot(at(10, 21, 0)))
        self.assertFalse(tp.non_ai_slot(at(4, 18, 30)))
        self.assertFalse(tp.non_ai_slot(at(11, 12, 15)))  # テスト期間（1週間）の後
        self.assertFalse(tp.non_ai_slot(None))
        with patch.dict("os.environ", {"SNS_NON_AI_SLOTS": "off"}):
            self.assertFalse(tp.non_ai_slot(at(4, 12, 15)))
        self.assertEqual(len(tp.NON_AI_SLOTS_JST), 2)

    def test_everyday_topics_first(self):
        car = {"title": "トヨタのランドクルーザー「FJ」を発売", "genreSlug": "business"}
        remote = {"title": "財務省が研究会の議事要旨を公開", "genreSlug": "business"}
        self.assertGreater(tp.everyday_score(car), tp.everyday_score(remote))


class LearningVisitsTest(unittest.TestCase):
    def run_learning(self, state, visits, now):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "learning.json"
            with patch.object(sys, "argv", ["threads_learn.py"]), \
                 patch.object(threads_learn, "load_state", return_value=state), \
                 patch.object(threads_learn, "save_state"), \
                 patch.object(threads_learn, "LEARNING_PATH", output), \
                 patch.object(threads_learn, "fetch_visits", return_value=visits), \
                 patch.object(threads_learn, "inflow_token_present", return_value=True), \
                 patch.object(threads_learn, "fetch_metrics", side_effect=AssertionError("Buffer を呼ばない")), \
                 patch.object(threads_learn, "datetime", wraps=datetime) as fake, \
                 patch.dict("os.environ", {"BUFFER_API_KEY": ""}):
                fake.now.return_value = now
                self.assertEqual(threads_learn.main(), 0)
            return json.loads(output.read_text())

    def test_visits_are_the_target_with_impression_fallback(self):
        now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
        common = {"style": "standalone", "format": "classic"}
        state = {"posted": {
            # 計測開始後に出た投稿: 訪問が目当て（表示は少なくても訪問が多い型が勝つ）
            "a": {**common, "hook": "callout", "opener": "check", "sentAt": "2026-11-04T00:00:00Z",
                  "metricsUpdatedAt": "2026-11-05T00:00:00Z", "metrics": {"views": 50}},
            "b": {**common, "hook": "callout", "opener": "see", "sentAt": "2026-11-04T03:00:00Z",
                  "metricsUpdatedAt": "2026-11-05T00:00:00Z", "metrics": {"views": 900}},
            # 計測前の投稿: 表示 × 割合 で置き換える
            "old": {**common, "hook": "number", "sentAt": "2026-11-01T00:00:00Z",
                    "metricsUpdatedAt": "2026-11-02T11:00:00Z", "metrics": {"views": 1000}},
            # 出たばかり: 入れない
            "fresh": {**common, "hook": "free", "sentAt": "2026-11-05T10:00:00Z", "metrics": {"views": 0}},
        }}
        result = self.run_learning(state, {"a": 30, "b": 1}, now)
        self.assertEqual(result["target"], "visits")
        self.assertEqual(result["postsWithVisits"], 2)
        self.assertEqual(result["freshSkipped"], 1)
        self.assertEqual(state["posted"]["a"]["visits"], 30)
        self.assertNotIn("visits", state["posted"]["old"])
        opener = result["axes"]["opener"]
        self.assertGreater(opener["check"]["score"], opener["see"]["score"])
        self.assertEqual(opener["check"]["avgVisits"], 30)
        # 置き換えの割合 = 訪問 / 表示（そろった投稿が3本未満なら既定の2%）
        self.assertEqual(result["visitRate"], threads_learn.DEFAULT_VISIT_RATE)
        self.assertEqual(result["axes"]["hook"]["number"]["avgVisits"], 20.0)
        self.assertNotIn("free", result["axes"]["hook"])
        self.assertIn("genre", result["axes"])

    def test_weak_hooks_use_visits(self):
        hooks = {k: {"n": 5, "avgViews": 100, "avgEngagement": 1, "avgVisits": 2} for k in ("check", "breaking", "question")}
        hooks["question"].update(avgViews=500, avgVisits=0.1, avgEngagement=0.1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "threads_learning.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"axes": {"hook": hooks}}), "utf-8")
            with patch.object(threads_hooks, "ROOT", Path(directory)):
                self.assertEqual(threads_hooks.weak_hooks(), ["question"])


class InflowReportTestGroups(unittest.TestCase):
    def test_hook_and_slot_test_are_shown(self):
        data = {"date": "2026-11-04", "views": 5, "visitors": 5, "bySource": [{"source": "threads", "views": 5, "visitors": 5}],
                "rows": [{"page": "car", "source": "threads", "campaign": "classic", "content": "", "views": 4, "visitors": 4},
                         {"page": "ai1", "source": "threads", "campaign": "classic", "content": "", "views": 1, "visitors": 1}]}
        posted = {
            "car": {"sentAt": "2026-11-04T03:15:00Z", "format": "classic", "hook": "callout", "opener": "check", "slotTest": "non_ai"},
            "ai1": {"sentAt": "2026-11-04T09:30:00Z", "format": "classic", "hook": "callout", "opener": "good", "slotTest": "ai"},
            "ai2": {"sentAt": "2026-11-04T06:30:00Z", "format": "classic", "hook": "callout", "opener": "see", "slotTest": "ai"},
        }
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            inflow_report.show(data, posted, {})
        text = out.getvalue()
        self.assertIn("classic/呼びかけ・要チェック", text)
        self.assertIn("AI以外の枠 1本・訪問4（1本あたり 4.0）", text)
        self.assertIn("AIの枠 2本・訪問1（1本あたり 0.5）", text)


class GeneratedPostsPassGate(unittest.TestCase):
    """実際の記事（content/articles）から新しい経路で作った本文が、どの SNS でも門番を通る"""

    def test_real_articles(self):
        root = Path(__file__).resolve().parents[1] / "content" / "articles"
        slugs = sorted(p.stem for p in root.glob("*.json"))[:0]
        for slug in ("honda-rn0-emtb-eicma-2026", "affinity-for-ipad-canva-release", "toyota-land-cruiser-fj-options"):
            path = root / f"{slug}.json"
            if path.exists():
                slugs.append(slug)
        if not slugs:
            self.skipTest("記事のファイルが無い")
        for slug in slugs:
            article = json.loads((root / f"{slug}.json").read_text("utf-8"))
            with patch.object(tp, "recent_openers", MagicMock(return_value=[])):
                text = tp.compose(article, [])
            for platform in ("threads", "facebook"):
                body = tp.with_link(text, article, platform, "classic")
                out = sns_rules.check_post(platform, body, [], article)
                self.assertEqual(out, [], f"{slug} {platform}\n{body}")
            x = tp.compose_x(article, text)
            reply = f"{tp.link_reply_text(article)}\n{tp.utm_link(f'{tp.site_url()}/articles/{slug}', 'x', 'classic')}"
            self.assertEqual(sns_rules.check_post("x", x, [], article, reply_text=reply), [], f"{slug} x\n{x}")


class PoliteSummaryTest(unittest.TestCase):
    """要約は やさしい です・ます の短い文・主語つき（2026-11-03 オーナー指示。sns_rules のルール7）"""

    def test_plain_endings_become_polite(self):
        cases = {
            "公的年金制度の普及や啓発活動を展開する。": "公的年金制度の呼びかけや呼びかけ",
            "ポスター掲示などを実施する。": "ポスター掲示などを行います。",
            "52カ月連続である。": "52カ月連続です。",
            "累計は前年を超えた。": "累計は前年を超えました。",
            "トヨタが新型車を発表": "トヨタが新型車を発表しました。",
            "料金は月500ドル。": "料金は月500ドルです。",
            "生活設計を考えることを呼びかける。": "生活設計を考えることを呼びかけます。",
            "日本ではまだ使えない。": "日本ではまだ使えません。",
            "価格は未発表です。": "価格は未発表です。",
        }
        for plain, want in cases.items():
            got = tp.polite_sentence(plain)
            self.assertTrue(sns_rules.POLITE_END.search(got), (plain, got))
            if want.endswith("。"):
                self.assertEqual(got, want)
        self.assertIsNone(sns_rules.HARD_WORDS.search(tp.polite_sentence("市区町村と協働し、記録検索機能の周知などを実施する。")))

    def test_subject_is_added(self):
        article = {"title": "厚生労働省、「年金の日」・「ねんきん月間」を11月に実施", "sourceName": "厚生労働省"}
        self.assertEqual(tp.subject_of(article), "厚生労働省")
        self.assertTrue(tp.with_subject("毎年11月30日を「年金の日」とします。", article).startswith("厚生労働省が、"))
        self.assertEqual(tp.with_subject("Hondaが公開しました。", {"title": "Hondaが公開"}), "Hondaが公開しました。")
        self.assertEqual(tp.subject_of({"title": "マイナビニュース、調査", "sourceName": "マイナビニュース"}), "")

    def test_gate_rejects_stiff_summary(self):
        art = {"slug": "acme-tool", "title": "Acme が新しいAIツールを公開", "genreSlug": "ai", "_virtual": True}
        stiff = ("会議のメモを取っている人、これは要チェックです。\n\n"
                 "要約すると、毎年11月30日を「年金の日」として普及や啓発活動を展開する。\nポスター掲示などを実施する。\n\n"
                 "あなたの職場では、会議の議事録を誰が書いていますか？\n\n詳しくはこちら👇\n"
                 "https://ai-tech-express.jp/articles/acme-tool?utm_source=threads&utm_medium=social&utm_campaign=classic")
        out = [v for v in sns_rules.check_post("threads", stiff, [], art) if "ルール7" in v]
        self.assertTrue(any("です・ます" in v for v in out), out)
        self.assertTrue(any("硬い言葉" in v for v in out), out)

    def _summary_of(self, article: dict) -> tuple[str, str]:
        with patch.object(tp, "recent_openers", MagicMock(return_value=[])):
            text = tp.compose(article, [])
        return text, tp.post_blocks_of(text)[1]

    def test_real_article_summary_is_polite_without_llm(self):
        # 記事のファイルは見出しの書き直しなどでボットが毎日書き換えるので、2026-11-03 の記事をそのまま写した見本で確かめる
        # （本番の記事の題名が「厚労省、…」に変わって、この試験がルールと関係なく落ちた。2026-11-03）
        path = Path(__file__).resolve().parent / "fixtures" / "sns" / "nenkin-no-hi-2026-mhlw.json"
        article = json.loads(path.read_text("utf-8"))
        # 見本の記事（2026-11-03 にお知らせの転載として取り下げ・台帳 A26）。記事のファイルが無くても文面の門だけを確かめる
        article["_virtual"] = True
        text, summary = self._summary_of(article)
        self.assertEqual(sns_rules.impolite_sentences(summary), [], summary)
        self.assertIsNone(sns_rules.HARD_WORDS.search(summary), summary)
        self.assertIn("厚生労働省", summary)
        self.assertEqual(sns_rules.check_post("threads", tp.with_link(text, article, "threads", "classic"), [], article), [])

    def test_live_article_summary_keeps_its_subject(self):
        # 今の記事（題名が書き直されていても）でも、要約の1文目は記事の主役（題名の頭の「厚労省」など）で始まり、門を通る
        path = Path(__file__).resolve().parents[1] / "content" / "articles" / "nenkin-no-hi-2026-mhlw.json"
        if not path.exists():
            self.skipTest("記事のファイルが無い")
        article = json.loads(path.read_text("utf-8"))
        text, summary = self._summary_of(article)
        subject = tp.subject_of(article)
        self.assertTrue(subject, article.get("title"))
        self.assertIn(subject, summary.splitlines()[0])
        self.assertEqual(sns_rules.impolite_sentences(summary), [], summary)
        self.assertEqual(sns_rules.check_post("threads", tp.with_link(text, article, "threads", "classic"), [], article), [])

if __name__ == "__main__":
    unittest.main()
