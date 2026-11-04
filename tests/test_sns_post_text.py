"""SNS 投稿の本文の形（2026-11-02 オーナー承認）。ネット接続・投稿なし。

書き出し／「要約すると、」＋事実2文／読者への問いかけ の3つのかたまり。見出しは繰り返さない。リンクは返信に置く。
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import persona  # noqa: E402
import sns_rules  # noqa: E402
import threads_hooks  # noqa: E402
import threads_post as tp  # noqa: E402
import threads_voice as voice  # noqa: E402

OLD = {
    "slug": "yen-bankruptcy",
    "genreSlug": "business",
    "title": "10月の「円安」倒産は6件、52カ月連続 累計は前年超え",
    "threadsStyle": 2,
    "threadsHook": "breaking",
    "threadsPost": "【速報】東京商工リサーチの発表によると、10月の「円安」倒産は6件でした。\n1〜10月で67件になり、前年1年分を超えました。\n取引先の動向を確認してみてください。",
    "highlights": ["2026年10月の「円安」倒産は6件で、52カ月連続の発生となりました。", "1〜10月の累計は67件で、前年1年間を超えています。"],
    "description": "東京商工リサーチの円安倒産の調査を分かりやすく解説します。",
    "workMoney": {"relevance": "medium", "points": ["輸入の多い取引先がある会社に関係します。"]},
    "easy": "かんたん版の本文",
    "generatedAt": "2026-11-02T00:00:00+00:00",
}
NEW = {
    "slug": "rakuten-accounts",
    "genreSlug": "investment",
    "title": "楽天証券の口座数が1,500万を突破、国内最多",
    "threadsStyle": 3,
    "threadsHook": "number",
    "threadsPost": "証券口座を持っている人、これは要チェックです。\n\n要約すると、楽天証券の口座が1,500万を超えました。\n国内の証券会社1社では、いちばん多い数です。\n\n証券口座、どこを使っていますか？NISAは始めましたか？",
    "highlights": ["楽天証券の総合口座数が1,500万口座を突破しました。", "2026年4月の1,400万口座から約7カ月で100万口座増えました。"],
    "description": "楽天証券の口座数が1,500万を突破しました。",
    "generatedAt": "2026-11-02T00:00:00+00:00",
}


def no_weak(last_openers=None):
    """弱い型の学習と、直前の投稿の言い回し（投稿履歴）に左右されないようにする"""
    return patch.multiple(tp, weak_hook_keys=MagicMock(return_value=[]), recent_openers=MagicMock(return_value=list(last_openers or [])))


class PostShapeTest(unittest.TestCase):
    def assert_shape(self, text, article):
        self.assertEqual(tp.post_problems(text, article), [], text)
        blocks = tp.post_blocks_of(text)
        self.assertEqual(len(blocks), 3)
        self.assertTrue(blocks[1].startswith("要約すると、"))
        self.assertTrue(text.endswith("？"))
        self.assertNotIn("ざっくり", text)
        self.assertIsNone(persona.STIFF_RE.search(text))
        self.assertNotIn(article["title"], text)
        self.assertLessEqual(len(text), tp.POST_MAX_CHARS)
        self.assertNotRegex(text, r"https?://|#")

    def test_old_article_is_rebuilt_into_three_blocks(self):
        article = dict(OLD)
        with no_weak():
            text = tp.compose(article, [])
        self.assert_shape(text, article)
        # 数字は記事のまま。読者への指示（確認してみてください）や記事の紹介文は要約に入れない
        self.assertIn("6件", text)
        self.assertNotIn("確認してみてください", text)
        self.assertNotIn("解説", text)
        self.assertIn("円安", tp.post_blocks_of(text)[2])

    def test_generated_post_is_used_as_is(self):
        article = dict(NEW)
        with no_weak():
            text = tp.compose(article, [])
        self.assertEqual(text, NEW["threadsPost"])
        self.assert_shape(text, article)
        self.assertEqual(article["_question"], "証券口座、どこを使っていますか？NISAは始めましたか？")

    def test_missing_question_gets_topic_question(self):
        article = {**NEW, "threadsPost": NEW["threadsPost"].rsplit("\n\n", 1)[0]}
        with no_weak():
            text = tp.compose(article, [])
        self.assert_shape(text, article)
        self.assertIn(tp.post_blocks_of(text)[2], [q for _, qs in voice.READER_QUESTIONS for q in qs])

    def test_bait_or_advice_question_is_replaced(self):
        for bad in ["どう思うかコメントで教えてください？", "今が買い時だと思いますか？"]:
            article = {**NEW, "threadsPost": NEW["threadsPost"].rsplit("\n\n", 1)[0] + "\n\n" + bad}
            with no_weak():
                text = tp.compose(article, [])
            self.assertNotIn(bad, text)
            self.assert_shape(text, article)

    def test_stiff_summary_is_rebuilt_from_facts(self):
        article = {**NEW, "threadsPost": NEW["threadsPost"].replace("要約すると、楽天証券の口座が", "要約すると、ざっくり言えば楽天証券の口座が")}
        with no_weak():
            text = tp.compose(article, [])
        self.assert_shape(text, article)
        self.assertIn("1,500万", text)

    def test_ensure_question_appends_when_missing(self):
        article = dict(OLD)
        text = tp.ensure_question("書き出し\n\n要約すると、事実です。", article)
        self.assertTrue(text.endswith("？"))
        self.assertEqual(len(tp.post_blocks_of(text)), 3)
        self.assertEqual(tp.ensure_question("A\n\nB？", article), "A\n\nB？")

    def test_post_problems_flags_old_style(self):
        problems = " ".join(tp.post_problems("ざっくり言うと便利です。\n詳細は記事で。", OLD))
        self.assertIn("3つ", problems)
        self.assertIn("要約すると", problems)
        self.assertIn("？", problems)
        self.assertIn("ざっくり", problems)
        repeated = f"書き出し\n\n要約すると、{OLD['title']}。\n\nどうですか？"
        self.assertIn("見出し", " ".join(tp.post_problems(repeated, OLD)))

    def test_breaking_starts_with_breaking_label_and_callout(self):
        """2026-11-03 オーナー承認その2: 本当の速報だけ【速報】＋読者への呼びかけ。「発表からN分で掲載」は使わない"""
        for article in (dict(OLD, breaking=True), dict(OLD, speedMinutes=12)):
            with no_weak():
                text = tp.compose(article, [], breaking=True)
            first = text.splitlines()[0]
            self.assertTrue(first.startswith("【速報】"), first)
            self.assertTrue(sns_rules.callout_ok(first), first)
            self.assertNotIn("発表から", text)
            self.assertEqual(text.count("【速報】"), 1)
            self.assert_shape(text, article)

    def test_not_breaking_drops_breaking_label(self):
        # 紹介文に【速報】があっても、速報の記事（breaking・speedMinutes）でなければ付けない。いますぐ出す回（breaking=True）でも同じ
        for flag in (False, True):
            with no_weak():
                text = tp.compose(dict(OLD), [], breaking=flag)
            self.assertNotIn("【速報】", text)
            self.assertTrue(sns_rules.callout_ok(text.splitlines()[0]), text)

    def test_disaster_keeps_legacy_post_without_question(self):
        quake = {"slug": "q", "title": "震度5強", "disaster": True, "breaking": True, "threadsStyle": 2,
                 "threadsPost": "【速報】震度5強の地震がありました。\n津波の心配はありません。"}
        text = tp.compose(quake, [], breaking=True)
        self.assertTrue(text.startswith("【速報】震度5強"))
        self.assertFalse(text.endswith("？"))

    def test_copied_hook_example_is_not_used(self):
        copied = threads_hooks.HOOKS[7]["examples"][0]
        article = {**NEW, "threadsPost": NEW["threadsPost"].replace("7カ月で100万口座。伸びが速いです。", copied)}
        with no_weak():
            text = tp.compose(article, [])
        self.assertNotIn(copied, text)
        self.assert_shape(text, article)

    def test_negative_news_gets_no_cheerful_opener(self):
        article = {"slug": "atk", "seoTitle": "ARTEX", "title": "攻撃ツールARTEXの悪用を確認",
                   "verdict": {"status": "available", "japan": "ok", "free": True}}
        lines = [line for _, line in tp.openers(article)]
        self.assertFalse(any(line.endswith("もう試しました？") for line in lines))
        self.assertNotIn("これ、無料で試せます。", lines)

    def test_hook_rotation_avoids_recent_types(self):
        article = {"slug": "s", "seoTitle": "会計アプリ｜料金", "title": "会計アプリが値下げ", "threadsStyle": 2, "threadsPost": "",
                   "verdict": {"status": "available", "japan": "ok", "price": "月980円", "free": True},
                   "highlights": ["会計アプリの料金が月980円になりました。", "日本でもすぐ使えます。"]}
        first = dict(article)
        with no_weak():
            tp.compose(first, [])
        second = dict(article)
        with no_weak([first["_opener"]]):
            tp.compose(second, [])
        # 1行目はいつも呼びかけ。直前の投稿と同じ言い回しは続けない
        self.assertEqual((first["_hook"], second["_hook"]), ("callout", "callout"))
        self.assertNotEqual(first["_opener"], second["_opener"])


class OtherChannelsTest(unittest.TestCase):
    def test_x_fits_and_keeps_question_when_possible(self):
        for base, breaking in ((OLD, False), (NEW, False), (OLD, True)):
            article = dict(base, breaking=breaking)
            with no_weak(), patch.object(tp, "speed_line", return_value=""):
                text = tp.compose(article, [], breaking=breaking)
            x = tp.compose_x(article, text)
            self.assertLessEqual(tp.x_weight(x), tp.X_LIMIT, x)
            self.assertIn("要約すると、", x)
            if breaking:
                self.assertTrue(x.startswith("【速報】"))

    def test_x_keeps_callout_and_drops_second_sentence_first(self):
        hook = "会議のメモを取っている人、これは要チェックです。"
        text = f"{hook}\n\n要約すると、{'い' * 50}。\n{'う' * 50}。\n\nどれを使っていますか？"
        x = tp.compose_x({"title": "t", "tags": []}, text)
        self.assertTrue(x.startswith(hook), x)
        self.assertNotIn("う" * 50, x)
        self.assertIn("どれを使っていますか？", x)
        self.assertLessEqual(tp.x_weight(x), tp.X_LIMIT)

    def test_link_reply_is_short(self):
        self.assertEqual(tp.link_reply_text(OLD), "詳しくはこちら👇\n（専門用語なしの「かんたん版」もあります）")
        self.assertEqual(tp.link_reply_text({**OLD, "easy": ""}), "詳しくはこちら👇")


class ReaderQuestionTest(unittest.TestCase):
    def test_topic_questions_fit_the_reader(self):
        cases = {
            "円安倒産が52カ月連続": "円安",
            "楽天証券の口座数が1,500万を突破": "NISA",
            "住宅ローン金利が上昇": "住宅ローン",
        }
        for title, word in cases.items():
            q = voice.reader_question({"slug": "a", "title": title, "genreSlug": "business"}, "", [])
            self.assertIn(word, q)
            self.assertTrue(q.endswith("？"))

    def test_ai_tool_question_and_rotation(self):
        article = {"slug": "x", "title": "ChatGPTに新機能", "genreSlug": "ai",
                   "verdict": {"status": "available", "japan": "ok", "audience": "一般ユーザー"}}
        first = voice.reader_question(article, "ChatGPT", [])
        second = voice.reader_question(article, "ChatGPT", [first])
        self.assertNotEqual(first, second)
        self.assertTrue(second.endswith("？"))

    def test_all_questions_are_clean(self):
        questions = [q for _, qs in voice.READER_QUESTIONS for q in qs] + voice.TOOL_QUESTIONS
        questions += [q.format(name="Pixel 11 Pro") for q in voice.SUBJECT_QUESTIONS + voice.NEGATIVE_QUESTIONS] + voice.GENERIC_QUESTIONS
        for q in questions:
            self.assertTrue(q.endswith("？"), q)
            self.assertLessEqual(len(q.format(topic="ChatGPT")), tp.QUESTION_MAX, q)
            self.assertIsNone(persona.ADVICE_RE.search(q), q)
            self.assertIsNone(persona.STIFF_RE.search(q), q)
            self.assertIsNone(tp.BAIT_RE.search(q), q)
            self.assertFalse(sns_rules.question_needs_article(q), q)


class GroundedQuestionTest(unittest.TestCase):
    def test_tags_alone_do_not_pick_a_topic_question(self):
        article = {"slug": "gdc", "title": "Google、機密データ領域のAI基盤『GDC』を刷新 検索や新半導体を追加",
                   "seoTitle": "Google Distributed Cloudとは？新機能", "tags": ["セキュリティ"], "genreSlug": "ai",
                   "verdict": {"status": "available", "free": True}}
        q = voice.reader_question(article, "Google", [])
        # 記事を読まなくても答えられる問い（製品名で「使ってみたい？」とは聞かない）。タグだけで話題を決めない
        self.assertFalse(sns_rules.question_needs_article(q), q)
        self.assertNotIn("パスワード", q)
        self.assertLessEqual(len(q), 40)

    def test_subject_from_headline_quote(self):
        article = {"slug": "px", "title": "ソフトバンクが「Google Pixel 11 Pro」の料理撮影術を公開",
                   "seoTitle": "Google Pixel 11 Proの料理撮影術｜AI編集の使い方", "verdict": {"free": True}}
        q = voice.reader_question(article, "Google", [])
        self.assertIn("スマホ", q)
        self.assertFalse(sns_rules.question_needs_article(q), q)
        self.assertNotIn("無料", q)

    def test_negative_subject_question_is_not_cheerful(self):
        q = voice.reader_question({"slug": "a", "title": "ツールXの悪用を確認", "seoTitle": "ツールXとは"}, "", [])
        self.assertNotIn("使ってみたい", q)

    def test_llm_question_is_checked_and_falls_back(self):
        article = dict(OLD)
        with no_weak(), patch.object(tp, "llm_question", return_value=("輸入の多い取引先、あなたの会社にもありますか？", "plain")) as ask:
            text = tp.compose(dict(article), [], use_llm=True)
        ask.assert_called_once()
        self.assertTrue(text.endswith("輸入の多い取引先、あなたの会社にもありますか？"))
        with no_weak(), patch.object(tp, "llm_question", return_value=("", "")):
            text = tp.compose(dict(article), [], use_llm=True)
        self.assertIn("円安", tp.post_blocks_of(text)[2])
        for bad in ["コメントで教えてください？", "今が買い時ですか？", "便利です。", "あ" * 45 + "？"]:
            self.assertFalse(tp.question_ok(bad, OLD["title"]), bad)
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            self.assertEqual(tp.llm_question(OLD, OLD["title"]), ("", ""))

    def test_generated_question_skips_llm_and_fixed_question_wins(self):
        with no_weak(), patch.object(tp, "llm_question", side_effect=AssertionError("no LLM")):
            text = tp.compose(dict(NEW), [], use_llm=True)
            self.assertTrue(text.endswith("NISAは始めましたか？"))
            text = tp.compose(dict(NEW), [], fixed_question="楽天証券、使っていますか？")
        self.assertTrue(text.endswith("楽天証券、使っていますか？"))


class LinkInBodyTest(unittest.TestCase):
    """2026-11-03 オーナー決定: Threads・Bluesky・Facebook は本文にリンク。X だけ返信にリンク"""

    def test_threads_body_ends_with_link_line(self):
        with no_weak():
            body = tp.compose(dict(OLD), [])
        text = tp.with_link(body, OLD, "threads", "classic")
        self.assertTrue(text.startswith(body))
        lines = text.splitlines()
        self.assertEqual(lines[-2], "詳しくはこちら👇（かんたん版あり）")
        self.assertTrue(lines[-1].startswith(f"{tp.site_url()}/articles/{OLD['slug']}?utm_source=threads&utm_medium=social"))
        self.assertLessEqual(len(text), tp.MAX_CHARS)
        self.assertEqual(tp.with_link(body, {**OLD, "easy": ""}).splitlines()[-2], "詳しくはこちら👇")

    def test_long_body_drops_hook_first(self):
        body = "書き出し" * 20 + "\n\n要約すると、" + "あ" * 300 + "。\n\nどれですか？"
        text = tp.with_link(body, OLD)
        self.assertNotIn("書き出し", text)
        self.assertLessEqual(len(text), tp.MAX_CHARS)
        self.assertIn("utm_source=threads", text)

    def test_utm_per_platform_and_content(self):
        url = tp.utm_link("https://x.jp/a", "bluesky", "classic")
        self.assertEqual(url, "https://x.jp/a?utm_source=bluesky&utm_medium=social&utm_campaign=classic&utm_content=video_body")
        # 2026-11-05（S29）: utm_content は必ず <種類>_<位置>。X は既定で返信、動画の版は utm_campaign に移す
        self.assertTrue(tp.utm_link("https://x.jp/a", "x", "classic").endswith("&utm_content=video_reply"))
        self.assertTrue(tp.utm_link("https://x.jp/a", "threads", "classic", ptype="hook", pos="reply").endswith("&utm_content=hook_reply"))
        self.assertEqual(tp.utm_link("https://x.jp/a", "threads", "sns_video", "short"),
                         "https://x.jp/a?utm_source=threads&utm_medium=social&utm_campaign=sns_video_short&utm_content=video_body")
        self.assertTrue(tp.utm_link("https://x.jp/a", "x", "classic", "video 1").endswith("&utm_content=video%201"))
        self.assertIn("?a=1&utm_source=facebook", tp.utm_link("https://x.jp/a?a=1", "facebook", "classic"))

    def test_buffer_threads_classic_has_no_reply(self):
        sent = []
        with patch.object(tp, "create_post", side_effect=lambda key, body, **kw: sent.append(body) or "id1"):
            tp.publish_via_buffer("k", "ch", "本文\nhttps://example", "https://ai-tech-express.jp/articles/a", dict(OLD), draft=True)
            tp.publish_via_buffer("k", "ch", "本文", "https://ai-tech-express.jp/articles/a", dict(OLD), draft=True, fmt="hook_thread")
        classic, hook = (b["metadata"]["threads"]["thread"] for b in sent)
        self.assertEqual(len(classic), 1)
        self.assertEqual(classic[0]["assets"][0]["image"]["url"], tp.card_url(OLD))
        self.assertEqual(len(hook), 2)

    def test_x_keeps_link_in_reply_and_facebook_has_no_reply(self):
        sent = []
        channels = {"twitter": {"id": "t"}, "facebook": {"id": "f"}}
        with patch.object(tp, "create_post", side_effect=lambda key, body, **kw: sent.append(body) or "id"):
            tp.publish_extra_channels("k", channels, "本文？", "https://ai-tech-express.jp/articles/a", dict(OLD), None, draft=True)
        x, fb = sent
        thread = x["metadata"]["twitter"]["thread"]
        self.assertEqual(len(thread), 2)
        self.assertIn("utm_source=x&utm_medium=social", thread[1]["text"])
        self.assertNotIn("thread", fb["metadata"]["facebook"])
        self.assertIn("utm_source=facebook&utm_medium=social", fb["metadata"]["facebook"]["linkAttachment"]["url"])


class PromptTest(unittest.TestCase):
    def test_generation_prompt_asks_for_new_shape(self):
        self.assertEqual(threads_hooks.POST_STYLE, 3)
        rules = threads_hooks.POST_RULES
        for word in ("要約すると、", "問いかけ", "空行", "見出しを繰り返さない", "120〜150字"):
            self.assertIn(word, rules)
        self.assertIn("「ざっくり」は使わない", rules)
        self.assertIn(rules, threads_hooks.prompt_block(recent=[]))


if __name__ == "__main__":
    unittest.main()
