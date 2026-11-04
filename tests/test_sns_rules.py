"""SNS ルールの門番（scripts/sns_rules.py）のテストと、投稿 API を呼ぶ所がすべて門番を通っているかの検査。

  python3 -m unittest tests.test_sns_rules
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sns_rules as r  # noqa: E402

ART = {"slug": "acme-tool", "title": "Acme が新しいAIツールを公開", "genreSlug": "ai", "_virtual": True}
LINK = "https://ai-tech-express.jp/articles/acme-tool"
GOOD = (
    "会議のメモを取っている人、これは要チェックです。\n\n"
    "要約すると、Acmeが会議の内容を自動でまとめるAIを公開しました。\n"
    "月額は1人1,000円で、日本語にも対応しています。\n\n"
    "あなたの職場では、会議の議事録を誰が書いていますか？"
)


def size(w, h):
    return lambda url: (w, h)


def with_link(text, source):
    return f"{text}\n\n詳しくはこちら👇\n{LINK}?utm_source={source}&utm_medium=social&utm_campaign=classic&utm_content=video_body"


CARD = [{"type": "image", "url": "https://ai-tech-express.jp/card/acme-tool"}]
VIDEO = [{"type": "video", "url": "https://ai-tech-express.jp/video/acme-tool-0123456789.mp4", "width": 1920, "height": 1080}]
# 記事の投稿は動画つきだけ（ルール8。2026-11-03）。ふつうの投稿の見本は動画つき
MEDIA = VIDEO


class CheckPostTest(unittest.TestCase):
    def check(self, platform, text, media=MEDIA, article=ART, measurer=size(1200, 675), **kw):
        return r.check_post(platform, text, media, article, measurer=measurer, **kw)

    def test_good_posts_pass_on_every_platform(self):
        self.assertEqual(self.check("threads", with_link(GOOD, "threads")), [])
        self.assertEqual(self.check("facebook", with_link(GOOD, "facebook")), [])
        reply = f"詳しくはこちら👇\n{LINK}?utm_source=x&utm_medium=social&utm_campaign=classic&utm_content=video_reply"
        self.assertEqual(self.check("twitter", GOOD + "\n\n#AI", reply_text=reply), [])
        bsky = f"{GOOD}\nai-tech-express.jp/articles/acme-tool"
        self.assertEqual(self.check("bluesky", bsky, links=[f"{LINK}?utm_source=bluesky&utm_medium=social&utm_campaign=fill&utm_content=video_body"]), [])

    def test_link_placement_and_utm(self):
        self.assertTrue(any("本文に記事のリンクがない" in v for v in self.check("threads", GOOD)))
        x_body = with_link(GOOD, "x")
        self.assertTrue(any("X は記事のリンクを本文ではなく返信" in v for v in self.check("x", x_body)))
        self.assertTrue(any("utm_source=threads" in v for v in self.check("threads", f"{GOOD}\n\n{LINK}")))
        # 返信（リンクカード）にだけリンクがある Threads の形（hook_thread）は止める
        self.assertTrue(any("本文に記事のリンクがない" in v for v in self.check("threads", GOOD, reply_text="")))

    def test_poll_without_article_is_blocked(self):
        poll = "AIの問いかけ（2026/11/03）\n仕事で使うならどれ？\n\nChatGPT／Copilot／Gemini\n\n番号でコメントしてもらえるとうれしいです。"
        out = self.check("threads", poll, media=[], article=None)
        joined = " ".join(out)
        self.assertIn("記事のない投稿", joined)
        self.assertIn("番号でコメント", joined)
        self.assertIn("アンケート", joined)

    def test_banned_and_generic_phrases(self):
        for phrase in ("ざっくり言うと、新しいAIです。", "気軽にコメントください。"):
            text = with_link(GOOD.replace("会議のメモを取っている人、これは要チェックです。", phrase), "threads")
            self.assertTrue(any("ルール3" in v or "ルール4" in v for v in self.check("threads", text)), phrase)
        generic = with_link(GOOD.replace("あなたの職場では、会議の議事録を誰が書いていますか？", "どう思いますか？"), "threads")
        self.assertTrue(any("決まり文句" in v for v in self.check("threads", generic)))
        specific = with_link(GOOD.replace("あなたの職場では、会議の議事録を誰が書いていますか？", "AIの電気代が自分の電気料金に響くか、気になりますか？"), "threads")
        self.assertFalse(any("決まり文句" in v for v in self.check("threads", specific)))

    def test_structure(self):
        double = GOOD.replace("月額は", "要約すると、月額は")
        self.assertTrue(any("2回" in v for v in self.check("threads", with_link(double, "threads"))))
        no_question = with_link(GOOD.rsplit("\n\n", 1)[0], "threads")
        self.assertTrue(any("問いかけ" in v for v in self.check("threads", no_question)))
        no_summary = with_link(GOOD.replace("要約すると、", ""), "threads")
        self.assertTrue(any("要約" in v for v in self.check("threads", no_summary)))

    def test_english_sentence(self):
        text = with_link(GOOD.replace("月額は1人1,000円で、日本語にも対応しています。", "This tool is available for everyone today."), "threads")
        self.assertTrue(any("英語" in v for v in self.check("threads", text)))
        names = with_link(GOOD.replace("Acmeが", "Google Distributed Cloudの"), "threads")
        self.assertFalse(any("英語" in v for v in self.check("threads", names)))

    def test_media_aspect_and_text_band(self):
        self.assertTrue(any("16:9 でない" in v for v in self.check("threads", with_link(GOOD, "threads"), media=CARD + VIDEO, measurer=size(1080, 1350))))
        self.assertEqual(self.check("threads", with_link(GOOD, "threads"), media=CARD + VIDEO, measurer=size(1200, 630)), [])
        cover = [{"type": "image", "url": "https://ai-tech-express.jp/cover/acme-tool"}]
        self.assertTrue(any("文字の帯" in v for v in self.check("threads", with_link(GOOD, "threads"), media=cover)))
        video = [{"type": "video", "url": "https://ai-tech-express.jp/video/a.mp4", "width": 1080, "height": 1920}]
        self.assertTrue(any("動画が 16:9 でない" in v for v in self.check("threads", with_link(GOOD, "threads"), media=video)))
        unknown = [{"type": "image", "url": "https://example.com/x.jpg"}]
        self.assertTrue(any("確かめられない" in v for v in self.check("threads", with_link(GOOD, "threads"), media=unknown, measurer=lambda u: None)))

    def test_lengths(self):
        long = with_link(GOOD.replace("月額は", "月額は" + "あ" * 400), "threads")
        self.assertTrue(any("長すぎる" in v for v in self.check("threads", long)))
        reply = f"詳しくはこちら👇\n{LINK}?utm_source=x&utm_medium=social&utm_campaign=classic&utm_content=video_reply"
        self.assertTrue(any("X の本文が長すぎる" in v for v in self.check("x", GOOD + "あ" * 100, reply_text=reply)))

    def test_sports_scope(self):
        art = {**ART, "genreSlug": "sports", "title": "レイカーズが開幕戦に勝利", "tags": ["NBA"]}
        self.assertTrue(any("スポーツ" in v for v in self.check("threads", with_link(GOOD, "threads"), article=art)))
        art = {**ART, "genreSlug": "sports", "title": "大谷翔平が本塁打", "tags": ["MLB"]}
        self.assertFalse(any("スポーツ" in v for v in self.check("threads", with_link(GOOD, "threads"), article=art)))

    def test_one_post_per_article(self):
        state = {"posted": {"acme-tool": {"id": "t1", "channels": {"twitter": "x1"}}}}
        self.assertTrue(any("ルール5" in v for v in self.check("threads", with_link(GOOD, "threads"), history=True, state=state)))
        self.assertFalse(any("ルール5" in v for v in self.check("facebook", with_link(GOOD, "facebook"), history=True, state=state)))
        self.assertTrue(any("ルール5" in v for v in self.check("threads", with_link(GOOD, "threads"), kind="video_followup")))

    def test_digest(self):
        link = "https://ai-tech-express.jp/digest/20261103"
        text = "今朝のAIニュース3本（2026/11/03）\n\n・GoogleのAI「Gemini 4」など10月の新発表まとめ\n・ElevenLabsが音響効果モデルを公開"
        art = {"link": link, "items": ["a", "b"]}
        share = [{"type": "image", "url": f"{link}/share.png?v=1"}]
        body = f"{text}\n\nほかの新着もサイトにまとめています👇\n{link}?utm_source=threads&utm_medium=social&utm_campaign=digest&utm_content=digest_body"
        self.assertEqual(r.check_post("threads", body, share, art, kind="digest", measurer=size(1200, 630)), [])
        bad = text.replace("・ElevenLabsが音響効果モデルを公開", "・Google DeepMind")
        out = r.check_post("threads", bad, share, {"link": "https://ai-tech-express.jp/news", "items": ["a", "b"]}, kind="digest", measurer=size(1200, 630))
        self.assertTrue(any("号のページ" in v for v in out))
        self.assertTrue(any("見出しが短すぎ" in v for v in out))
        self.assertTrue(any("本文に記事のリンクがない" in v for v in out))

    def test_buffer_parts_threads_hook_thread_is_blocked(self):
        inp = {"text": GOOD, "metadata": {"threads": {"thread": [
            {"text": GOOD, "assets": [{"image": {"url": "https://ai-tech-express.jp/cover/acme-tool"}}]},
            {"text": "詳しくはこちら👇", "metadata": {"threads": {"linkAttachment": {"url": LINK + "?utm_source=threads&utm_medium=social"}}}},
        ]}}}
        with self.assertRaises(r.SnsRuleViolation) as ctx:
            r.enforce_buffer("threads", inp, ART, source="test", measurer=size(1200, 630))
        joined = " ".join(ctx.exception.violations)
        self.assertIn("本文に記事のリンクがない", joined)
        self.assertIn("文字の帯", joined)

    def test_measure_bytes(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (1200).to_bytes(4, "big") + (675).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
        self.assertEqual(r.measure_bytes(png), (1200, 675))
        # mp4（faststart）の映像トラックの tkhd: version 0 の固定部分のあとに 16.16 固定小数の幅・高さ
        tkhd = b"tkhd" + b"\x00" * 4 + b"\x00" * 20 + b"\x00" * 8 + b"\x00" * 8 + b"\x00" * 36 + (1920 << 16).to_bytes(4, "big") + (1080 << 16).to_bytes(4, "big")
        self.assertEqual(r.measure_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 20 + b"moov" + tkhd), (1920, 1080))

    def test_known_card_size_matches_site(self):
        """予備の大きさの表が、サイトのカード（app/card の CARD_DESIGN と lib/card-designs.tsx）とずれていないか"""
        route = (ROOT / "app/card/[slug]/route.tsx").read_text("utf-8")
        design = re.search(r'const CARD_DESIGN[^=]*=\s*"(\w+)"', route).group(1)
        sizes = (ROOT / "lib/card-designs.tsx").read_text("utf-8")
        m = re.search(rf"\b{design}: \{{ width: (\d+), height: (\d+) \}}", sizes)
        self.assertIsNotNone(m, f"カードのデザイン {design} の大きさが見つからない")
        self.assertEqual(r.known_size("https://ai-tech-express.jp/card/x"), (int(m.group(1)), int(m.group(2))))
        w, h = int(m.group(1)), int(m.group(2))
        self.assertLessEqual(abs(w / h - r.ASPECT) / r.ASPECT, r.ASPECT_TOL, "SNS のカードが 16:9 でない")


# ---- 投稿 API を呼ぶ所の検査（新しく作った経路が門番を飛ばしていないか） ----

# 投稿を作る・中身を変える API（Buffer の GraphQL・Bluesky の AT Protocol・Threads 公式 API）
POSTING_API = re.compile(
    r"\b(?:createPost|editPost|updatePost|createIdea)\s*\(|com\.atproto\.repo\.createRecord|/threads_publish\b|f\"\{user_id\}/threads\""
)
GATE_CALL = re.compile(r"\bsns_rules\.(?:enforce|enforce_buffer|check_post)\s*\(")
SCAN = ["scripts", "ops", "lib", "app", "cloudflare"]
SKIP = {"scripts/sns_rules.py"}


def code_files():
    for base in SCAN:
        for path in (ROOT / base).rglob("*"):
            if path.suffix in (".py", ".ts", ".tsx", ".js", ".mjs", ".cjs") and "node_modules" not in path.parts:
                rel = path.relative_to(ROOT).as_posix()
                if rel not in SKIP:
                    yield rel, path


def enclosing_function(tree: ast.AST, line: int):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= (node.end_lineno or node.lineno):
            if best is None or node.lineno > best.lineno:
                best = node
    return best


class CallSiteTest(unittest.TestCase):
    def test_every_posting_api_call_goes_through_the_gate(self):
        problems = []
        found = 0
        for rel, path in code_files():
            text = path.read_text("utf-8", errors="ignore")
            hits = [text.count("\n", 0, m.start()) + 1 for m in POSTING_API.finditer(text)]
            if not hits:
                continue
            if path.suffix != ".py":
                problems.append(f"{rel}: 投稿 API を Python 以外から呼んでいる（sns_rules を通せない）")
                continue
            tree = ast.parse(text)
            lines = text.splitlines()
            for line in hits:
                found += 1
                fn = enclosing_function(tree, line)
                if fn is None:
                    problems.append(f"{rel}:{line}: 関数の外で投稿 API を呼んでいる")
                    continue
                before = "\n".join(lines[fn.lineno - 1:line])
                if not GATE_CALL.search(before):
                    problems.append(f"{rel}:{line}: {fn.name}() が投稿 API の前に sns_rules.enforce を呼んでいない")
        self.assertGreaterEqual(found, 3, "投稿 API の呼び出しが見つからない（検査の正規表現が古い）")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_buffer_endpoint_only_in_threads_post(self):
        """Buffer の API を直接呼ぶのは threads_post.buffer_graphql だけ（ほかは必ずそこを通る）"""
        users = [rel for rel, path in code_files() if "api.buffer.com" in path.read_text("utf-8", errors="ignore")]
        self.assertEqual(users, ["scripts/threads_post.py"])

    def test_buffer_graphql_refuses_ungated_posting_mutation(self):
        import threads_post as tp

        with self.assertRaises(r.SnsRuleViolation):
            tp.buffer_graphql("k", "mutation($input: CreatePostInput!) { createPost(input: $input) { __typename } }", {})


class CalloutRuleTest(unittest.TestCase):
    """ルール6（2026-11-03 オーナー承認その2）: 1行目は読者への呼びかけ・【速報】は本当の速報だけ・使わない書き出し・答えやすい問い"""

    def check(self, platform, text, article=ART, **kw):
        return r.check_post(platform, text, CARD, article, measurer=size(1200, 675), **kw)

    def rule6(self, out):
        return [v for v in out if "ルール6" in v]

    def test_first_line_must_call_out_reader(self):
        for bad in ("会議のメモ、まだ手で取っていますか。", "結論：個人なら無料で使えます。", "1日で1,211件。"):
            text = with_link(GOOD.replace("会議のメモを取っている人、これは要チェックです。", bad), "threads")
            self.assertTrue(any("呼びかけ" in v for v in self.rule6(self.check("threads", text))), bad)
        for ok in ("マウンテンバイク好きの人、これは要チェックです。", "コードを書く開発者の皆さん、朗報です。",
                   "電気代が気になる人に、知っておいてほしい話です。", "株や業績をチェックしている人へ。"):
            text = with_link(GOOD.replace("会議のメモを取っている人、これは要チェックです。", ok), "threads")
            self.assertEqual(self.rule6(self.check("threads", text)), [], ok)

    def test_banned_openers(self):
        for bad in ("先に言うと、まだ一般には使えません。", "先に言うと、日本は対象外です。", "【速報】発表から12分で掲載"):
            text = with_link(GOOD.replace("会議のメモを取っている人、これは要チェックです。", bad), "threads")
            out = self.rule6(self.check("threads", text, article={**ART, "breaking": True}))
            self.assertTrue(any("先に言うと" in v or "発表からN分" in v for v in out), bad)

    def test_breaking_label_only_for_fast_news(self):
        text = with_link("【速報】" + GOOD, "threads")
        self.assertTrue(any("速報ではない記事" in v for v in self.check("threads", text)))
        self.assertEqual(self.rule6(self.check("threads", text, article={**ART, "breaking": True})), [])
        self.assertEqual(self.rule6(self.check("threads", text, article={**ART, "speedMinutes": 14})), [])
        self.assertTrue(any("速報ではない記事" in v for v in self.check("threads", text, article={**ART, "breaking": True, "type": "roundup"})))

    def test_question_answerable_without_article(self):
        for bad in ("あなたの仕事で、いちばん気になった発表はどれですか？", "この機能、仕事で使ってみたいですか？", "無料なら試してみますか？"):
            text = with_link(GOOD.replace("あなたの職場では、会議の議事録を誰が書いていますか？", bad), "threads")
            self.assertTrue(any("記事を読まないと答えられない" in v for v in self.check("threads", text)), bad)
        for ok in ("趣味の道具にかけるお金、何を重視して選びますか？", "夏と冬、電気代が高いと感じるのはどっちですか？"):
            self.assertFalse(r.question_needs_article(ok), ok)

    def test_x_body_and_other_platforms_follow_rule6(self):
        reply = f"詳しくはこちら👇\n{LINK}?utm_source=x&utm_medium=social&utm_campaign=classic&utm_content=video_reply"
        bad = GOOD.replace("会議のメモを取っている人、これは要チェックです。", "会議のメモ、まだ手で取っていますか。")
        self.assertTrue(self.rule6(self.check("x", bad + "\n\n#AI", reply_text=reply)))
        # X は字数で書き出しを落とした形（要約から始まる）も通す
        self.assertEqual(self.rule6(self.check("x", GOOD.split("\n\n", 1)[1] + "\n\n#AI", reply_text=reply)), [])
        self.assertTrue(self.rule6(self.check("facebook", with_link(bad, "facebook"))))
        bsky = f"{bad}\nai-tech-express.jp/articles/acme-tool"
        self.assertTrue(self.rule6(self.check("bluesky", bsky, links=[f"{LINK}?utm_source=bluesky&utm_medium=social&utm_campaign=fill&utm_content=video_body"])))

    def test_digest_and_disaster_are_exempt(self):
        quake = {**ART, "disaster": True}
        text = with_link("【地震】千葉県北東部で最大震度5弱", "threads")
        self.assertEqual(self.rule6(self.check("threads", text, article=quake)), [])


if __name__ == "__main__":
    unittest.main()
