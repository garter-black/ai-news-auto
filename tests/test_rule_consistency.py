"""オーナーのルールを「すべての経路で同じように」守らせているかの検査（2026-11-04 一貫性の点検）。

オーナー「私たちが決めたルールや課題をクリアできるよう、一貫性のあるサービスにしたい」。
この日、ルールが1つの経路では守られ、別の経路では守られていない例が続いた
（動画の検査が L1 だけ・重なった実行で1記事2投稿・上限で動かなかったレビューを合格扱い）。ここでは:

  1. 台帳のどのルールにも、テストか毎日の見張りか本番で動く点検が付いているか
  2. 投稿を作るすべての経路が、門番（sns_rules）に加えて二重投稿の確かめを通るか・最新の記録を読むか
  3. 記事の文章を書き出すすべての段が、同じ合格の基準（quality.every_path_problems など）を通るか
  4. 「確かめられない」を合格にしていないか（fail-closed）
を確かめる。新しい投稿の経路・記事を書く段を足したら、ここに登録しないと落ちる。何も投稿しない・通信しない。
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

WORKFLOWS = {p.name: p.read_text("utf-8") for p in sorted((ROOT / ".github" / "workflows").glob("*.yml"))}


# ---------------------------------------------------------------- 1. 台帳のどのルールにも強制がある
# 本番で動き、違反があれば赤になる点検（テストのファイルではないが、強制として数える）
CHECK_SCRIPTS = {"scripts/ui_check.mjs", "scripts/check_readability.mjs", "scripts/check_images.py", "scripts/cold_check.py"}


class LedgerCoverageTest(unittest.TestCase):
    def test_every_rule_has_a_test_or_an_audit(self):
        import owner_rules_audit
        from test_owner_rules_ledger import refs, rows

        missing = []
        for rid, cells in rows():
            enforce = cells[3]
            if enforce.startswith("会話") and not refs(enforce):
                continue  # オーナーとのやりとりの決まり（O1・O5・O6）
            paths = {p for p, _ in refs(enforce)}
            if any(p.startswith("tests/") for p in paths) or rid in owner_rules_audit.AUDIT_RULES or paths & CHECK_SCRIPTS:
                continue
            missing.append(rid)
        self.assertEqual(missing, [], "テストも毎日の見張りも本番の点検も無いルール（台帳の「強制」に足す）")


# ---------------------------------------------------------------- 2. 投稿の経路
from test_sns_rules import POSTING_API, code_files, enclosing_function  # noqa: E402

# 投稿を「作る」API と、その前に必ず呼ぶ二重投稿の確かめ（S8・S20）。編集（editPost など）は新しい投稿を作らないので対象外
CREATE_GUARDS = [
    (re.compile(r"\bcreatePost\s*\("), re.compile(r"\bensure_not_duplicate\s*\(")),
    (re.compile(r"com\.atproto\.repo\.createRecord"), re.compile(r"\bexisting_uri\s*\(")),
    (re.compile(r"/threads_publish\b|f\"\{user_id\}/threads\""), re.compile(r"\bmeta_existing_post\s*\(")),
]
POSTING_SCRIPTS = re.compile(r"scripts/(?:threads_post|breaking_post|bluesky_post|sns_video_followup|morning_digest|post_now|sns_video)\.py")


class PostingPathTest(unittest.TestCase):
    def test_every_post_creation_checks_for_duplicates_first(self):
        problems, found = [], 0
        for rel, path in code_files():
            if path.suffix != ".py":
                continue
            text = path.read_text("utf-8", errors="ignore")
            if not POSTING_API.search(text):
                continue
            tree, lines = ast.parse(text), text.splitlines()
            for api, guard in CREATE_GUARDS:
                for m in api.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    fn = enclosing_function(tree, line)
                    if fn is None:
                        continue  # 関数の外の呼び出しは test_sns_rules が止める
                    found += 1
                    if not guard.search("\n".join(lines[fn.lineno - 1:line])):
                        problems.append(f"{rel}:{line}: {fn.name}() が投稿を作る前に二重投稿を確かめていない（{guard.pattern}）")
        self.assertGreaterEqual(found, 3, "投稿を作る呼び出しが見つからない（検査の正規表現が古い）")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_posting_workflows_read_latest_main(self):
        """投稿を作り得るワークフローは、順番待ちのあとでも最新の投稿の記録を読む（S20: 古い記録で2回出た）"""
        bad = [name for name, text in WORKFLOWS.items() if POSTING_SCRIPTS.search(text) and "ref: main" not in text]
        self.assertEqual(bad, [])

    def test_no_workflow_turns_off_the_video_review(self):
        """SNS_VIDEO_REQUIRE_QA=off（試験用）を本番のワークフローで使わない（S16・S23）"""
        self.assertEqual([n for n, t in WORKFLOWS.items() if "SNS_VIDEO_REQUIRE_QA" in t], [])

    def test_meta_direct_publish_checks_threads_first(self):
        import threads_post as tp

        token = {"user_id": "u", "access_token": "t"}
        art = {"slug": "acme-tool"}
        calls = []

        def fetch(method, path, params):
            calls.append((method, path))
            return {"data": [{"id": "T1", "text": "前の投稿 https://ai-tech-express.jp/articles/acme-tool?utm_source=threads"}]}

        with patch.object(tp.sns_rules, "enforce", lambda *a, **k: None), patch.object(tp, "api", side_effect=AssertionError("作ってはいけない")):
            out = tp.publish(token, "本文", "https://ai-tech-express.jp/articles/acme-tool", art, fetch=fetch)
        self.assertEqual((out["id"], out.get("duplicate")), ("T1", True))
        self.assertEqual(calls, [("GET", "u/threads")])
        # 読めなければ出さない（RuntimeError → 呼ぶ側は次の回にやり直す）
        with self.assertRaises(RuntimeError):
            tp.meta_existing_post(token, art, fetch=lambda *a: {"error": "x"})

    def test_bluesky_reads_its_own_server_records_too(self):
        """公開のフィードに出る前（反映の遅れ）でも、自分のサーバーの記録で同じ記事の投稿を見つける"""
        import bluesky_post as bp

        def fetcher(url):
            if "getAuthorFeed" in url:
                return {"feed": []}
            return {"records": [{"uri": "at://me/post/9", "value": {"text": "… https://ai-tech-express.jp/articles/acme-tool"}}]}

        self.assertEqual(bp.existing_uri({"did": "did:me"}, "https://ai-tech-express.jp/articles/acme-tool?utm_source=bluesky", fetcher=fetcher), "at://me/post/9")

    def test_main_record_unknown_means_do_not_create(self):
        """main の最新の記録を読めない（Actions の中）→ 作らずに次の回へ（S20「確かめられなければ作らない」）"""
        import sns_video_followup as fu
        from test_sns_video_required import FLAGS, NOW, queued_rec, ready_video

        def remote(slug):
            raise fu.RemoteRecordUnknown("main の記録を読めません")

        state = {"videos": {"acme-tool": ready_video("acme-tool")}}
        sent = []
        out = fu.publish_queued(state, {"posted": {"acme-tool": queued_rec(NOW + timedelta(minutes=30))}}, NOW, FLAGS,
                                creator=lambda *a: sent.append(a) or "id", issue=lambda s, w: None, eligible=lambda s: True, remote=remote)
        self.assertEqual(sent, [])
        self.assertIn("deferred", out[0])

    def test_extra_requeue_uses_the_same_video_bar_and_owner_hold(self):
        """X・Facebook だけの出し直しも、Threads と同じ基準（検査に合格した動画だけ・ownerHold は自動で出さない）"""
        import sns_video_followup as fu
        from test_sns_video_required import ART, FLAGS, NOW, queued_rec, ready_video

        def run(video, posted):
            rec = queued_rec(NOW - timedelta(minutes=5))
            state = {"extraQueued": {"a": {"pending": {"slot": rec["pending"]["slot"], "inputs": {"facebook": rec["pending"]["inputs"]["facebook"]}}}},
                     "videos": {"a": video}}
            sent = []
            with patch.object(fu, "article_of", lambda report: ART):
                fu.publish_extra_queued(state, NOW, FLAGS, creator=lambda svc, inp, art: sent.append(svc) or "id", issue=lambda s, w: None, posted=posted)
            return sent

        unreviewed = ready_video("a")
        for v in unreviewed["versions"].values():
            v["qa"] = {"geometry": True, "vision": False}
        self.assertEqual(run(unreviewed, {"posted": {}}), [], "画像のレビューに通っていない動画を付けた")
        l4 = ready_video("a")
        for v in l4["versions"].values():
            v["level"] = "L4"
        self.assertEqual(run(l4, {"posted": {}}), [], "L4 の動画を付けた（S23）")
        self.assertEqual(run(ready_video("a"), {"posted": {"a": {"ownerHold": True}}}), [], "ownerHold の記事を自動で出した")
        self.assertEqual(run(ready_video("a"), {"posted": {}}), ["facebook"])


class SnsGateFailClosedTest(unittest.TestCase):
    """門番の中の検査は、動かせないときに合格にしない"""

    def test_descriptor_check_that_cannot_run_is_a_violation(self):
        import sns_rules

        with patch.dict(sys.modules, {"headline_normalize": None}):
            self.assertTrue(sns_rules.descriptor_problems("NVIDIAが新しいGPUを発表"))

    def test_product_check_that_cannot_run_counts_as_product(self):
        import sns_rules

        with patch.dict(sys.modules, {"images": None}):
            self.assertTrue(sns_rules.product_launch({"title": "x"}))

    def test_broken_posting_record_stops_posting(self):
        import sns_rules

        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "threads_posted.json"
            bad.write_text("{壊れた", "utf-8")
            with patch.object(sns_rules, "STATE_PATH", bad), self.assertRaises(sns_rules.PostingStateUnknown):
                sns_rules.already_posted("threads", {"slug": "a"}, "article")
            # 記録がまだ無い（初めて）は止めない
            with patch.object(sns_rules, "STATE_PATH", Path(d) / "none.json"):
                self.assertFalse(sns_rules.already_posted("threads", {"slug": "a"}, "article"))


# ---------------------------------------------------------------- 3. 記事を書き出す段
# 記事の JSON（content/articles）を書き得るスクリプトの台帳。新しいスクリプトはどちらかに登録する。
# 文章を作る・書き換える段は、すべての段で同じ合格の基準（quality.every_path_problems・quality.added_problems、
# またはそれを含む quality.structural_problems / quality.evaluate）を通す
PROSE_WRITERS = {
    "main.py": ["every_path_problems"],                  # 動画の記事（公式チャンネル）と共通の書き出し write_article
    "web_articles.py": ["quality.evaluate(", "added_problems"],  # 通常・幅広い話題・速報レーン・話題のキーワード＋続報
    "roundup.py": ["every_path_problems"],               # まとめ記事
    "angle_articles.py": ["quality.evaluate("],           # 別の切り口
    "rewrite_types.py": ["quality.evaluate("],            # 型での書き直し
    "section_polish.py": ["added_problems"],             # 章の見出し・文の書き直し（公開中の記事）
}
# 文章を作らない（決まった欄だけ直す・記録だけ足す・読むだけで別のファイルに書く）もの。理由つき
NON_PROSE = {
    "article_charts.py": "図の欄（charts）だけ",
    "article_quickfix.py": "LLM を使わない決まった置き換え（台帳 A の表示の段と同じ規則）",
    "audit.py": "ジャンルの付け替え（手で動かす）",
    "bluesky_post.py": "投稿の記録",
    "buying_guides.py": "比較ガイド（/compare。記事ではない）",
    "check_images.py": "壊れた画像の差し替え",
    "digest_page.py": "朝のまとめの号",
    "evergreen.py": "使い方ガイド（/guides。記事ではない）",
    "fast_lane.py": "速報レーンの記録（記事は web_articles が書く）",
    "fix_thumbnails.py": "画像の欄だけ",
    "headline_normalize.py": "見出しの決まった直し（LLM なし）",
    "image_focus.py": "画像の位置の欄だけ",
    "images.py": "画像・本文の画像の欄だけ",
    "indexnow.py": "検索への通知の記録",
    "label_check.py": "ジャンル・タグの欄だけ",
    "latency.py": "公開時刻の記録",
    "notice_gate.py": "判定の書き出し（lib/notice-rules.json）",
    "owner_rules_audit.py": "読むだけ（点検の結果のファイル）",
    "pipeline_health.py": "読むだけ（健康の記録）",
    "pricing_scope.py": "料金表の欄だけ",
    "pricing_table.py": "料金のデータ（content/pricing）",
    "products.py": "AIサービス図鑑（記事ではない）",
    "quake.py": "地震・津波は LLM を使わない型（A15）",
    "radar.py": "候補の記録",
    "retire_articles.py": "取り下げ（retired.json）",
    "review_sync.py": "確認の印の欄だけ",
    "rewrite_titles.py": "見出しだけ（headline_normalize.title_problems の門を通す）",
    "rivals.py": "比較表の欄だけ（quality.rival_problems の門を通す）",
    "shorts.py": "動画の記録",
    "sns_video_followup.py": "動画の埋め込みの欄だけ",
    "story.py": "重複の印（duplicateOf）だけ",
    "threads_learn.py": "投稿の学びの記録",
    "threads_post.py": "投稿の記録",
    "trial_types.py": "試し書き（trial.json。記事は書かない）",
    "video_ladder.py": "動画の記録",
}
ARTICLE_DIR_RE = re.compile(r"\bARTICLES(?:_DIR)?\b|\"content\"\s*/\s*\"articles\"|content/articles|write_article\(")
WRITE_RE = re.compile(r"\.write_text\(|json\.dump\(|write_article\(")


class ArticleWriterTest(unittest.TestCase):
    def test_every_article_writer_is_registered(self):
        found = {p.name for p in (ROOT / "scripts").glob("*.py") if ARTICLE_DIR_RE.search(t := p.read_text("utf-8")) and WRITE_RE.search(t)}
        unknown = sorted(found - set(PROSE_WRITERS) - set(NON_PROSE))
        self.assertEqual(unknown, [], "記事を書き得る新しいスクリプト。文章を作るなら PROSE_WRITERS に入れて共通の門を通す。欄だけなら NON_PROSE に理由つきで")

    def test_prose_writers_call_the_shared_gate(self):
        for name, needles in PROSE_WRITERS.items():
            text = (ROOT / "scripts" / name).read_text("utf-8")
            for needle in needles:
                self.assertIn(needle, text, f"{name} が共通の門（{needle}）を通していない")

    def test_shared_gate_covers_advice_and_first_person(self):
        import quality

        base = {"title": "新しいサービスの発表", "lede": "A社が新しいサービスを発表した。", "body": "## 発表の中身\n\nA社が新しいサービスを発表した。", "easy": "",
                "genreSlug": "ai"}
        advice = dict(base, body=base["body"] + "\n\n今が買い時で、注目銘柄です。")
        self.assertTrue(any("買い時" in p or "投資" in p or "助言" in p for p in quality.every_path_problems(advice)), quality.every_path_problems(advice))
        # 書き換える段は「新しく増えた不合格」だけを見る（古い型の記事にもとからある分では止めない）
        self.assertEqual(quality.added_problems(advice, dict(advice)), [])
        self.assertTrue(quality.added_problems(base, advice))

    def test_style_gate_that_cannot_run_is_a_failure(self):
        import article_style

        rec = {"title": "t", "lede": "l", "body": "## 発表\n\n本文です。", "easy": ""}
        with patch.dict(sys.modules, {"section_gate": None}):
            codes = [c for c, _ in article_style.coded_problems(rec)]
        self.assertIn("A21", codes)


# ---------------------------------------------------------------- 4. サイト・運用・見張り
class SiteAndOpsTest(unittest.TestCase):
    def test_no_dash_fill_in_tables(self):
        """W11: 空欄を「—」で埋めない（すべての表の部品で。前は比較ガイドと料金の段の表が「—」を出していた）"""
        bad = []
        for base in ("app", "components", "lib"):
            for path in (ROOT / base).rglob("*.ts*"):
                if path.name == "no-data.ts":
                    continue
                for i, line in enumerate(path.read_text("utf-8").splitlines(), 1):
                    if re.search(r"(?:\?\?|\|\||:)\s*\"—\"", line) and "label:" not in line:
                        bad.append(f"{path.relative_to(ROOT)}:{i}")
        self.assertEqual(bad, [])

    def test_trial_workflows_never_count_as_production(self):
        """O10: 試し書き・見本・試走のワークフローは、LLM の回数を本番と別に数える（名前の末尾 _trial）"""
        for name, text in WORKFLOWS.items():
            if not re.search(r"trial|sample|dryrun|dry_run", name):
                continue
            for key in ("LLM_WORKFLOW", "CLAUDE_API_LEDGER"):
                for value in re.findall(rf"{key}:\s*([\w-]+)", text):
                    self.assertTrue(value.endswith("_trial"), f"{name}: {key}: {value}（本番の上限に数えてしまう）")

    def test_no_workflow_enables_opus(self):
        """O8: 自動の処理で Opus を使わない（ワークフローの設定でも上げない）"""
        for name, text in WORKFLOWS.items():
            self.assertNotRegex(text, r"CLAUDE_ALLOW_OPUS|CLAUDE_MODEL:\s*[\"']?opus|claude-opus", name)

    def test_audit_reports_checks_that_could_not_run(self):
        import owner_rules_audit as audit

        audit.CHECK_ERRORS.clear()
        with patch.dict(sys.modules, {"labels": None}):
            self.assertEqual(audit.label_problems({"title": "x", "genreSlug": "ai"}), [])
        self.assertTrue(any(k.startswith("[A30]") for k in audit.CHECK_ERRORS))
        audit.CHECK_ERRORS.clear()

    def test_audit_fails_when_the_issue_cannot_be_written(self):
        import subprocess

        import owner_rules_audit as audit

        failed = subprocess.CompletedProcess([], 1, "", "gh: not logged in")
        with patch.object(audit, "_gh", lambda *a: failed):
            self.assertFalse(audit.write_issue({"/x": ["[A1] x"]}, "body"))

    def test_audit_a6_ignores_ad_product_titles_but_not_site_labels(self):
        """A6 はサイトの札・見出しの決まり。広告に出る他社の書籍の正式な名前（「60分でわかる！…」）は違反にしない（毎日の見張りの誤検知）"""
        import owner_rules_audit as audit

        ad = '<p>本も比べる</p><a rel="sponsored nofollow" href="#">60分でわかる！ AIエージェント 超入門</a><a rel="sponsored" href="#">PR</a>'
        self.assertEqual([v for v in audit.article_page_problems(f"<html><body>{ad}</body></html>", {}) if v.startswith("[A6]")], [])
        label = '<p>3分でわかる新機能</p><a rel="sponsored" href="#">PR</a>'
        self.assertTrue([v for v in audit.article_page_problems(f"<html><body>{label}</body></html>", {}) if v.startswith("[A6]")])

    def test_cold_check_does_not_pass_missing_pages(self):
        import cold_check

        page = {"path": "/x", "status": 404, "ttfb": 0.1, "cache": "HIT", "ver": "v", "error": ""}
        self.assertEqual(cold_check.judge(page, None)[0], "fail")


class OwnerDecisionTest(unittest.TestCase):
    """2026-11-04 一貫性の点検のあと、オーナーが決めた6つ"""

    def read(self, rel):
        return (ROOT / rel).read_text("utf-8")

    def test_hero_badge_is_red_only_for_breaking(self):
        src = self.read("components/HomeHero.tsx")
        self.assertIn("liveLabel(", src)
        self.assertIn("isFastNews(", src)
        self.assertNotRegex(src, r'className="rounded-sm bg-live', "トップの札がいつも赤")

    def test_list_thumbnails_show_ai_mark(self):
        self.assertIn("AI生成", self.read("components/Thumb.tsx"))
        for rel in ("components/NewsHub.tsx", "components/ArticleCard.tsx", "components/Timeline.tsx", "components/NextRead.tsx", "app/page.tsx"):
            self.assertIn('aiGenerated={', self.read(rel), rel)

    def test_share_image_headlines_use_noto(self):
        """ロゴ用の書体（Dela）は、ロゴ・サイト名・順位の数字だけ。見出しは Noto"""
        bad = []
        for base in ("app", "lib", "components"):
            for path in (ROOT / base).rglob("*.tsx"):
                if path.name == "wordmark.tsx":
                    continue  # ロゴそのもの
                lines = path.read_text("utf-8").splitlines()
                for i, line in enumerate(lines, 1):
                    near = "\n".join(lines[i - 1:i + 10])  # 書体の指定と中身が別の行のこともある
                    if 'fontFamily: "Dela"' in line and not re.search(r"サイソク|\{n\}|String\(n\)", near):
                        bad.append(f"{path.relative_to(ROOT)}:{i}")
        self.assertEqual(bad, [])

    def test_big_news_may_regenerate_three_times_and_last_try_skips_missing_fact_rewrite(self):
        import web_articles

        self.assertEqual(web_articles.MAJOR_ATTEMPTS, 3)
        src = self.read("scripts/web_articles.py")
        self.assertIn("attempts=MAJOR_ATTEMPTS if major else 2", src)
        self.assertIn("elif missing and attempt < attempts", src)  # 最後の試行では作り直さず、無い事実を書かないまま進む（A20）


if __name__ == "__main__":
    unittest.main()
