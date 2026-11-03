"""記事の SNS 投稿は動画つきだけ（2026-11-03 オーナー指示「SNS の投稿は動画のはず」。台帳 S13）と、
壊れた日本語・無理な説明・弱い呼びかけを出さない（台帳 S14）の検査。外部通信なし・何も投稿しない。

  python3 -m unittest tests.test_sns_video_required
"""
from __future__ import annotations

import ast
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sns_rules as r  # noqa: E402
import sns_video_followup as fu  # noqa: E402
import threads_post as tp  # noqa: E402

NOW = datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)  # 12:00 JST
ART = {"slug": "acme-tool", "title": "Acme が新しいAIツールを公開", "genreSlug": "ai", "_virtual": True}
LINK = "https://ai-tech-express.jp/articles/acme-tool"
GOOD = (
    "会議のメモを取っている人、これは要チェックです。\n\n"
    "要約すると、Acmeが会議の内容を自動でまとめるAIを公開しました。\n"
    "月額は1人1,000円で、日本語にも対応しています。\n\n"
    "あなたの職場では、会議の議事録を誰が書いていますか？"
)
CARD = [{"type": "image", "url": "https://ai-tech-express.jp/card/acme-tool", "width": 1200, "height": 675}]
VIDEO = [{"type": "video", "url": "https://ai-tech-express.jp/video/acme-tool-0123456789.mp4", "width": 1920, "height": 1080}]


def body(source: str) -> str:
    return f"{GOOD}\n\n詳しくはこちら👇\n{LINK}?utm_source={source}&utm_medium=social&utm_campaign=classic"


def rule8(out: list[str]) -> list[str]:
    return [v for v in out if "ルール8" in v]


class VideoRequiredGateTest(unittest.TestCase):
    def test_article_post_without_video_is_rejected_on_every_platform(self):
        reply = f"詳しくはこちら👇\n{LINK}?utm_source=x&utm_medium=social&utm_campaign=classic"
        cases = {
            "threads": (body("threads"), {}),
            "facebook": (body("facebook"), {}),
            "x": (GOOD, {"reply_text": reply}),
            "bluesky": (f"{GOOD}\nai-tech-express.jp/articles/acme-tool", {"links": [f"{LINK}?utm_source=bluesky&utm_medium=social&utm_campaign=x"]}),
        }
        for platform, (text, kw) in cases.items():
            for media in ([], CARD):  # 写真・カードだけ・何も無い → 止める
                self.assertTrue(rule8(r.check_post(platform, text, media, ART, **kw)), f"{platform} {media}")
            self.assertEqual(rule8(r.check_post(platform, text, VIDEO, ART, **kw)), [], platform)
            self.assertEqual(rule8(r.check_post(platform, text, CARD + VIDEO, ART, **kw)), [], platform)

    def test_only_morning_digest_keeps_its_image(self):
        digest = {"link": "https://ai-tech-express.jp/digest/20261104", "items": [{"slug": "a"}, {"slug": "b"}]}
        share = [{"type": "image", "url": "https://ai-tech-express.jp/digest/20261104/share.png", "width": 1200, "height": 630}]
        self.assertEqual(rule8(r.check_post("threads", "今朝のAIニュース3本\n・x", share, digest, kind="digest")), [])
        # 速報は例外にしない（地震・津波などの急ぎの安全の知らせだけ例外。UrgentExceptionTest）
        self.assertTrue(rule8(r.check_post("threads", body("threads"), CARD, {**ART, "breaking": True})))

    def test_draft_and_text_preview_are_not_published_so_they_pass(self):
        self.assertEqual(rule8(r.check_post("threads", body("threads"), CARD, ART, draft=True)), [])
        self.assertEqual(rule8(r.check_post("threads", body("threads"), CARD, ART, pending_video=True)), [])

    def test_buffer_input_with_photo_only_raises(self):
        inp = {"channelId": "c", "text": body("threads"), "mode": "customScheduled", "dueAt": "2026-11-04T03:30:00Z",
               "metadata": {"threads": {"thread": [{"text": body("threads"), "assets": [{"image": {"url": CARD[0]["url"], "width": 1200, "height": 675}}]}]}}}
        with patch.dict("os.environ", {"SNS_RULES_NO_ISSUE": "1"}):
            with self.assertRaises(r.SnsRuleViolation) as caught:
                r.enforce_buffer("threads", inp, ART, source="test", history=False)
        self.assertTrue(rule8(caught.exception.violations))
        inp["metadata"]["threads"]["thread"][0]["assets"] = [{"video": VIDEO[0] | {}}]
        inp["metadata"]["threads"]["thread"][0]["assets"] = [{"video": {k: v for k, v in VIDEO[0].items() if k != "type"}}]
        r.enforce_buffer("threads", inp, ART, source="test", history=False)  # 動画つきは通る

    def test_audit_flags_real_post_without_video(self):
        post = {"platform": "threads", "text": body("threads"), "media": CARD}
        self.assertTrue(rule8(r.audit_post(post)))


class UrgentExceptionTest(unittest.TestCase):
    """（2026-11-03 オーナー承認・台帳 S15）災害・警報など遅れると読者に害がある知らせは、動画を待たずに写真のカードで出してよい"""

    def test_urgent_safety_news_may_post_with_card(self):
        for art in ({**ART, "disaster": True}, {**ART, "urgent": True}, {**ART, "title": "気象庁、大雨特別警報を発表 静岡県に"},
                    {**ART, "title": "台風12号が接近 関東は暴風警報"}, {**ART, "title": "鹿児島で震度5強 津波の心配なし"}):
            self.assertTrue(r.is_urgent(art), art["title"])
            self.assertEqual(rule8(r.check_post("threads", body("threads"), CARD, art)), [], art["title"])

    def test_ordinary_news_mentioning_weather_still_needs_video(self):
        for title in ("地震保険の保険料が来年から上がる", "Acme が新しいAIツールを公開", "台風の時期に備える家電の選び方"):
            art = {**ART, "title": title}
            self.assertFalse(r.is_urgent(art), title)
            self.assertTrue(rule8(r.check_post("threads", body("threads"), CARD, art)), title)

    def test_urgent_breaking_posts_now_without_queue(self):
        article = {**ART, "description": "d", "seoTitle": ART["title"], "disaster": True}
        channels = {"threads": {"id": "t"}}
        with patch.object(tp, "buffer_channels", return_value=channels), \
                patch.object(tp, "publish_via_buffer", return_value={"id": "T", "via": "buffer", "permalink": ""}) as direct, \
                patch.object(tp, "queue_for_video", side_effect=AssertionError("急ぎの知らせは動画を待たない")):
            res = tp.share_now("k", article, GOOD)
        self.assertTrue(direct.called)
        self.assertEqual((res["id"], res["urgent"]), ("T", True))

    def test_bluesky_does_not_wait_for_video_on_urgent(self):
        import bluesky_post as bp

        self.assertFalse(bp.needs_video({"style": "standalone", "urgent": True}))
        self.assertTrue(bp.needs_video({"style": "standalone"}))


class GrammarGateTest(unittest.TestCase):
    def test_na_adjective_plus_shimashita_is_rejected(self):
        bad = "要約すると、NVIDIAが、DGX Spark 64GBを活用し、数コマンドでAIモデルの実行が可能しました。"
        self.assertTrue(r.grammar_problems(bad))
        for ok in ("実行が可能になりました。", "実行できるようにしました。", "発売を予定しています。", "よかったです。", "必要です。"):
            self.assertEqual(r.grammar_problems(ok), [], ok)
        for bad in ("提供しましたです。", "発表したです。", "簡単しました。"):
            self.assertTrue(r.grammar_problems(bad), bad)

    def test_gate_rejects_broken_summary_and_forced_descriptor(self):
        broken = body("threads").replace("公開しました。", "公開が可能しました。")
        self.assertTrue(any("ルール9" in v for v in r.check_post("threads", broken, VIDEO, ART)))
        forced = body("threads").replace("Acmeが", "AI半導体のNVIDIAが")
        self.assertTrue(any("無理な説明" in v for v in r.check_post("threads", forced, VIDEO, ART)))

    def test_polite_converter_never_breaks_grammar(self):
        self.assertEqual(tp.polite_sentence("NVIDIAが、数コマンドでAIモデルの実行が可能"), "NVIDIAが、数コマンドでAIモデルの実行が可能です。")
        self.assertEqual(tp.polite_sentence("新モデルを公開"), "新モデルを公開しました。")
        self.assertEqual(tp.polite_sentence("新しい枠組みを構想"), "")  # 「しました」が付くか分からない名詞は直さない
        for noun in r.NA_NOUNS:
            out = tp.polite_sentence(f"AIの利用が{noun}")
            self.assertEqual(r.grammar_problems(out), [], out)

    def test_weak_callout_templates_are_gone(self):
        import threads_hooks

        self.assertNotIn("keep", [c[0] for c in threads_hooks.CALLOUTS])
        who, group = threads_hooks.audience_of({"title": "NVIDIA、AI学習の自動復旧ツールを公開"})
        self.assertNotIn("裏側の技術が気になる", who + group)
        self.assertTrue(tp.WEAK_CALLOUT.search("AIの裏側の技術が気になる人は、押さえておきたい話です。"))


class QueueForVideoTest(unittest.TestCase):
    """threads_post は Buffer を呼ばず「動画待ち」にするだけ（写真の予約を作らない）"""

    def test_queue_does_not_call_buffer(self):
        article = {**ART, "description": "d", "thumbnail": "", "seoTitle": ART["title"]}
        channels = {"threads": {"id": "t"}, "twitter": {"id": "x"}, "facebook": {"id": "f"}}
        with patch.object(tp, "create_post", side_effect=AssertionError("Buffer を呼んではいけない")), \
                patch.object(tp, "buffer_graphql", side_effect=AssertionError("Buffer を呼んではいけない")), \
                patch.object(r, "enforce_buffer") as gate:
            res = tp.queue_for_video("k", channels, body("threads"), GOOD, LINK, article, NOW + timedelta(hours=1))
        self.assertEqual(res["status"], "awaiting_video")
        self.assertEqual(res["id"], "")
        self.assertEqual(sorted(res["pending"]["inputs"]), ["facebook", "threads", "twitter"])
        self.assertTrue(all(c.kwargs.get("pending_video") for c in gate.call_args_list))

    def test_live_paths_use_the_queue(self):
        """main と速報（share_now）は Buffer に直接予約しない（queue_for_video を使う）"""
        tree = ast.parse((ROOT / "scripts" / "threads_post.py").read_text("utf-8"))
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in ("main", "share_now")):
            calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "id", "") in ("publish_via_buffer", "publish_extra_channels")]
            # 急ぎの安全の知らせ（is_urgent の分岐の中）だけは写真で直接予約してよい（S15）
            urgent = {id(c) for node in ast.walk(fn) if isinstance(node, ast.If) and "is_urgent" in ast.unparse(node.test)
                      for stmt in node.body for c in ast.walk(stmt)}
            live = [c for c in calls if id(c) not in urgent and not any(k.arg in ("draft", "build_only") for k in c.keywords)]
            self.assertEqual(live, [], f"{fn.name} が写真の予約を直接作っている")
            self.assertTrue(any(getattr(c.func, "id", "") == "queue_for_video" for c in ast.walk(fn) if isinstance(c, ast.Call)), fn.name)


def queued_rec(slot: datetime) -> dict:
    thread_in = {"channelId": "t", "text": body("threads"), "mode": "customScheduled", "dueAt": slot.isoformat(),
                 "metadata": {"threads": {"topic": "AI", "thread": [{"text": body("threads"), "assets": [{"image": {"url": CARD[0]["url"]}}]}]}}}
    x_in = {"channelId": "x", "text": GOOD, "_offsetMin": 12,
            "metadata": {"twitter": {"thread": [{"text": GOOD, "assets": [{"image": {"url": CARD[0]["url"]}}]},
                                                {"text": f"詳しくはこちら👇\n{LINK}?utm_source=x&utm_medium=social&utm_campaign=classic", "assets": []}]}}}
    fb_in = {"channelId": "f", "text": body("facebook"), "_offsetMin": 27, "assets": [],
             "metadata": {"facebook": {"type": "post", "linkAttachment": {"url": LINK, "thumbnail": {"url": CARD[0]["url"]}}}}}
    return {"id": "", "via": "buffer", "status": "awaiting_video", "dueAt": slot.isoformat(),
            "pending": {"slot": slot.isoformat(), "inputs": {"threads": thread_in, "twitter": x_in, "facebook": fb_in}}}


def ready_video(slug: str) -> dict:
    post = {"title": "t", "url": f"https://ai-tech-express.jp/articles/{slug}", "total": 34.6, "voice": {"credit": "VOICEVOX:九州そら"},
            "script_v3": {"hook": {"text": "h"}, "summary": "s"}}
    qa = {"geometry": True, "vision": True, "version": 1}
    return {"status": "ready", "tries": 1, "versions": {"short": {"ok": True, "post": post, "qa": qa}, "full": {"ok": True, "post": post, "qa": qa}},
            "urls": {"short": f"https://ai-tech-express.jp/video/{slug}-s.mp4", "full": f"https://ai-tech-express.jp/video/{slug}-f.mp4"}}


FLAGS = {"post": True, "ab_until": "2026-11-10", "after_ab": "short"}


class PublishQueuedTest(unittest.TestCase):
    def run_queue(self, posted, videos, eligible=lambda s: True):
        sent, issues = [], []

        def creator(service, inp, article):
            # 送る直前の門番と同じ判定で、動画が付いているか確かめる（写真だけなら止まる）
            _text, _reply, media, _links = r.buffer_parts(service, inp)
            self.assertTrue(r.has_video(media), f"{service} に動画が無い: {inp}")
            self.assertNotIn("linkAttachment", (inp.get("metadata") or {}).get("facebook") or {})
            sent.append((service, inp))
            return f"id-{service}"

        state = {"videos": videos}
        with patch.object(fu, "article_of", lambda report: ART):
            out = fu.publish_queued(state, {"posted": posted}, NOW, FLAGS, creator=creator, issue=lambda s, w: issues.append((s, w)), eligible=eligible)
        return state, sent, issues, out

    def test_ready_video_is_sent_at_the_slot_with_video(self):
        slot = NOW + timedelta(hours=2)
        state, sent, issues, _ = self.run_queue({"a": queued_rec(slot)}, {"a": ready_video("a")})
        self.assertEqual([s for s, _ in sent], ["threads", "twitter", "facebook"])
        self.assertEqual(sent[0][1]["dueAt"], fu._iso(slot))  # 枠の時刻のまま
        self.assertEqual(sent[1][1]["dueAt"], fu._iso(slot + timedelta(minutes=12)))
        self.assertIn("utm_campaign=sns_video", sent[0][1]["text"])
        self.assertEqual(state["queued"]["a"]["status"], "created")
        self.assertEqual(state["videos"]["a"]["status"], "attached")
        self.assertEqual(issues, [])

    def test_late_video_delays_and_keeps_slot_order(self):
        first, second = NOW - timedelta(minutes=30), NOW - timedelta(minutes=10)
        # 前の枠（a）の動画がまだ → あとの枠（b）は動画ができていても先に出さない
        state, sent, issues, _ = self.run_queue({"a": queued_rec(first), "b": queued_rec(second)}, {"b": ready_video("b")})
        self.assertEqual(sent, [])
        self.assertEqual(state["queued"]["a"]["status"], "waiting")
        # 両方できたら、枠が過ぎていても今から遅らせて a → b の順に予約する
        state, sent, issues, _ = self.run_queue({"a": queued_rec(first), "b": queued_rec(second)}, {"a": ready_video("a"), "b": ready_video("b")})
        threads = [inp["dueAt"] for s, inp in sent if s == "threads"]
        self.assertEqual(len(threads), 2)
        self.assertLess(threads[0], threads[1])
        self.assertGreaterEqual(threads[0], fu._iso(NOW + timedelta(minutes=fu.QUEUE_LEAD_MIN)))

    def test_failed_or_unfit_video_skips_post_and_opens_issue(self):
        slot = NOW + timedelta(hours=1)
        failed = {"status": "gate_failed", "tries": 3, "reason": "full: v3: 長さ"}
        state, sent, issues, _ = self.run_queue({"a": queued_rec(slot)}, {"a": failed})
        self.assertEqual(sent, [])
        self.assertEqual(state["queued"]["a"]["status"], "skipped")
        self.assertEqual([s for s, _ in issues], ["a"])
        state, sent, issues, _ = self.run_queue({"b": queued_rec(slot)}, {}, eligible=lambda s: False)
        self.assertEqual((sent, state["queued"]["b"]["status"]), ([], "skipped"))
        late = NOW - timedelta(hours=fu.QUEUE_GIVE_UP_H + 1)
        state, sent, issues, _ = self.run_queue({"c": queued_rec(late)}, {})
        self.assertEqual((sent, state["queued"]["c"]["status"]), ([], "skipped"))

    def test_video_without_render_and_vision_qa_is_not_posted_but_rerendered(self):
        slot = NOW + timedelta(hours=1)
        old = ready_video("a")
        for ver in old["versions"].values():
            ver["qa"] = {"geometry": True, "vision": False}
        state, sent, issues, _ = self.run_queue({"a": queued_rec(slot)}, {"a": old})
        self.assertEqual((sent, issues), ([], []))
        self.assertNotIn("a", state["videos"])  # 作り直す
        self.assertIn("a", state["videosArchive"])
        self.assertEqual(state["queued"]["a"]["status"], "waiting")
        self.assertFalse(fu.qa_ok({"ok": True}, env={}))
        self.assertTrue(fu.qa_ok({"qa": {"geometry": True, "vision": True}}, env={}))

    def test_retryable_failure_waits(self):
        slot = NOW + timedelta(hours=1)
        retry = {"status": "gate_failed", "tries": 1, "reason": "full: 台本を作れない"}
        state, sent, issues, _ = self.run_queue({"a": queued_rec(slot)}, {"a": retry})
        self.assertEqual((sent, issues, state["queued"]["a"]["status"]), ([], [], "waiting"))

    def test_sync_posted_copies_ids(self):
        posted = {"posted": {"a": queued_rec(NOW), "b": queued_rec(NOW)}}
        state = {"queued": {"a": {"status": "created", "id": "T1", "dueAt": NOW.isoformat(), "channels": {"threads": "T1", "twitter": "X1"}, "variant": "short"},
                            "b": {"status": "skipped", "why": "動画を作れなかった"}}}
        self.assertEqual(fu.sync_posted(state, posted), 2)
        a, b = posted["posted"]["a"], posted["posted"]["b"]
        self.assertEqual((a["id"], a["status"], a["image"], a["channels"]["twitter"]), ("T1", "scheduled", "video", "X1"))
        self.assertNotIn("pending", a)
        self.assertEqual(b["status"], "skipped_no_video")
        self.assertEqual(fu.sync_posted(state, posted), 0)  # 何度写しても同じ


class QueueFixTest(unittest.TestCase):
    """予約中の写真の投稿を直す（下書きに戻した分を動画待ちにする）。Buffer は呼ばない"""

    def test_node_problems_finds_static_and_old_format(self):
        old = {"service": "threads", "text": "AI半導体のNVIDIAが、AI学習の自動復旧ツールを公開。\n\n" + body("threads"),
               "metadata": {"thread": [{"text": "AI半導体のNVIDIAが、AI学習の自動復旧ツールを公開。", "assets": [{"mimeType": "image/png", "source": CARD[0]["url"]}]}]}}
        slug, video, problems = fu.node_problems(old)
        self.assertEqual((slug, video), ("acme-tool", False))
        self.assertTrue(problems)

    def test_pause_input_keeps_text_and_type_and_only_drafts(self):
        fb = fu.pause_input({"id": "1", "service": "facebook", "text": "t"})
        self.assertEqual((fb["text"], fb["metadata"], fb["saveToDraft"]), ("t", {"facebook": {"type": "post"}}, True))
        x = fu.pause_input({"id": "2", "service": "twitter", "text": "a", "metadata": {"thread": [{"text": "a"}, {"text": "b"}]}})
        self.assertEqual([t["text"] for t in x["metadata"]["twitter"]["thread"]], ["a", "b"])
        r.enforce_buffer("facebook", fb, None, source="test", pausing=True)  # 下書きに戻すだけは通る
        with self.assertRaises(r.SnsRuleViolation):
            r.enforce_buffer("facebook", {**fb, "saveToDraft": False, "mode": "shareNow"}, None, source="test", pausing=True)

    def test_converted_post_waits_for_video_then_goes_out_with_video(self):
        slot = NOW + timedelta(hours=1)
        rec = queued_rec(slot)
        conv = {"paused": {"threads": {"id": "old-t", "ok": True}, "twitter": {"id": "old-x", "ok": True}}, "pending": rec["pending"], "threadsSent": False}
        posted = {"posted": {"a": {"id": "old-t", "channels": {"twitter": "old-x", "bluesky": "at://b"}, "image": "card"}}}
        state = {"converted": {"a": conv}, "videos": {"a": ready_video("a")}}
        self.assertEqual(fu.apply_converted(state, posted), 1)
        a = posted["posted"]["a"]
        self.assertEqual((a["id"], a["status"], a["channels"], a["pausedIds"]["threads"]), ("", "awaiting_video", {"bluesky": "at://b"}, "old-t"))
        self.assertEqual(fu.apply_converted(state, posted), 0)

    def test_extra_only_requeue_uses_video(self):
        rec = queued_rec(NOW - timedelta(minutes=5))
        state = {"extraQueued": {"a": {"pending": {"slot": rec["pending"]["slot"], "inputs": {"facebook": rec["pending"]["inputs"]["facebook"]}}}},
                 "videos": {"a": ready_video("a") | {"status": "attached", "variant": "short"}}}
        sent = []
        with patch.object(fu, "article_of", lambda report: ART):
            fu.publish_extra_queued(state, NOW, FLAGS, creator=lambda svc, inp, art: sent.append((svc, inp)) or "id")
        self.assertEqual([s for s, _ in sent], ["facebook"])
        self.assertTrue(r.has_video(r.buffer_parts("facebook", sent[0][1])[2]))
        self.assertEqual(state["extraQueued"]["a"]["status"], "created")


class RepostTest(unittest.TestCase):
    def test_repost_queues_approved_text_at_next_normal_slot_and_rerenders_video(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "reposts").mkdir()
            (d / "reposts" / "a.json").write_text(json.dumps({"classic": GOOD, "variant": "short", "services": ["threads", "twitter"], "bluesky": True}), "utf-8")
            (d / "posted.json").write_text(json.dumps({"posted": {"a": {"id": "T0", "channels": {"twitter": "X0"}, "dueAt": "2026-11-03T12:00:00+00:00", "image": "card"}}}), "utf-8")
            (d / "video.json").write_text(json.dumps({"videos": {"a": {"status": "gate_failed", "tries": 2}}}), "utf-8")
            (d / "extra.json").write_text(json.dumps({"a": {"uri": "at://x"}}), "utf-8")
            seen = {}

            def fake_inputs(key, chans, slug, services, slot, classic=""):
                seen.update(services=services, classic=classic, slot=slot)
                return {"inputs": {s: {"text": classic} for s in services}, "slot": slot.isoformat()}

            with patch.object(fu, "REPOSTS", d / "reposts"), patch.object(fu, "POSTED", d / "posted.json"), patch.object(fu, "STATE", d / "video.json"), \
                    patch.object(fu, "BLUESKY_EXTRA", d / "extra.json"), patch.object(fu, "requeue_inputs", fake_inputs), \
                    patch.object(tp, "buffer_channels", return_value={}):
                out = fu.repost("a", api_key="k", now=NOW)
            posted = json.loads((d / "posted.json").read_text())["posted"]["a"]
            state = json.loads((d / "video.json").read_text())
            self.assertEqual(seen["classic"], GOOD)
            self.assertEqual((posted["status"], posted["id"], posted["pending"]["forceVariant"]), ("awaiting_video", "", "short"))
            self.assertNotIn("a", state["videos"])  # 動画は作り直す
            self.assertEqual(json.loads((d / "extra.json").read_text()), {})  # Bluesky も出し直す
            self.assertGreaterEqual(seen["slot"], NOW + timedelta(minutes=90))
            self.assertIn("slot", out)


class OwnerAuditTest(unittest.TestCase):
    def test_audit_flags_post_without_video(self):
        import owner_rules_audit as audit

        sent = (NOW - timedelta(hours=2)).isoformat()
        posted = {"posted": {
            "photo": {"id": "1", "dueAt": sent, "image": "card"},
            "video": {"id": "2", "dueAt": sent, "image": "video"},
            "digest-20261104": {"id": "3", "dueAt": sent, "style": "digest"},
            "waiting": {"id": "", "dueAt": sent, "status": "awaiting_video"},
            "quake": {"id": "4", "dueAt": sent, "image": "card", "urgent": True},
        }}  # fmt: skip
        out = audit.video_record_problems(posted, {}, {}, NOW)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("[S13]") and "photo" in out[0])


if __name__ == "__main__":
    unittest.main()
