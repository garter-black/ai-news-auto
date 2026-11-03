"""記事づくりが止まらないことの確認（2026-11-03 オーナー「何回も記事作成が止まるのはやめたい」）。

- Gemini が全部 429 のとき / Claude API が使えないとき、それぞれ別の経路で記事（本文の生成＋事実確認）ができる
- 無料枠のジョブごとの取り分（記事・速報の分はほかのジョブが使えない）
- 0本の回が続いたときの見張り（state/pipeline_health.json と issue）
実際の API・GitHub には一切つながない。
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import llm  # noqa: E402
import pipeline_health  # noqa: E402
import quality  # noqa: E402
import web_articles as w  # noqa: E402

ARTICLE = json.dumps({k: (True if k == "is_relevant" else ([] if k in ("keywords", "tags", "highlights", "timetable", "mentioned_tools") else ({} if k in ("verdict", "work_money") else "x")))
                      for k in w.WEB_SCHEMA["required"]}, ensure_ascii=False)
FACT = '{"grounded": true, "score": 90, "problems": [], "fatal": false}'
NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)


def fake_message(text):
    usage = SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=usage)


def free_reply(model, system, prompt, schema, *args):
    return FACT if "grounded" in schema.get("required", []) else ARTICLE


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        env = {"CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ACCOUNT_ID": "a", "OPENROUTER_API_KEY": "o", "GROQ_API_KEY": "g",
               "OPENROUTER_MODELS": "x/free-model:free", "GROQ_MODELS": "openai/gpt-oss-120b", "ANTHROPIC_API_KEY": "test-not-real"}
        self.env = patch.dict(os.environ, env)
        self.env.start()
        for key in [k for k in os.environ if k.startswith(("CLAUDE_API_", "LLM_DEFAULT_", "LLM_WORKFLOW", "LLM_DAILY_LIMIT_"))]:
            os.environ.pop(key)
        os.environ.pop("GITHUB_WORKFLOW", None)
        self.patches = [
            patch.object(llm, "API_STATE_DIR", tmp),
            patch.object(llm, "LLM_STATE_DIR", tmp),
            patch.object(llm, "USAGE_PATH", tmp / "llm_usage.json"),
            patch.object(llm, "SLOW_PATH", tmp / "llm_slow.json"),
            patch.object(llm, "_now", return_value=NOW),
            patch.object(llm, "available_models", return_value=set(llm.QUALITY_MODELS + llm.BULK_MODELS)),
            patch.object(llm, "_groq_models", return_value=["groq:openai/gpt-oss-120b"]),
            patch.object(llm, "_claude_models", return_value=[]),
            patch.object(llm, "_codex_models", return_value=[]),
            patch.object(llm, "_http_json", side_effect=AssertionError("no network")),
            patch.object(pipeline_health, "HEALTH_PATH", tmp / "pipeline_health.json"),
            patch("time.sleep"),
        ]
        for p in self.patches:
            p.start()
        self.reset()

    def reset(self):
        llm._tiers.clear()
        llm._exhausted.clear()
        llm._limit_logged.clear()
        llm._api_summary_logged.clear()
        llm._api_off = None
        llm._api_transient_failures = 0

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        self.reset()
        self.tmp.cleanup()

    def make_article(self, purpose="core"):
        """記事1本: 本文の生成（write）→ 品質ゲートの事実確認（quality.fact_check）"""
        data, writer = llm.generate_json("sys", "prompt", w.WEB_SCHEMA, tier="write", purpose=purpose)
        verdict = quality.fact_check({"title": data["title"], "body": "x"}, "source")
        return writer, verdict["model"], verdict["grounded"]


class ProviderChainTests(Base):
    def test_every_tier_has_non_gemini_fallbacks(self):
        for tier in ("quality", "write", "bulk"):
            chain = llm.models_for(tier)
            cf = chain.index("cloudflare:@cf/openai/gpt-oss-120b")
            self.assertLess(cf, chain.index("openrouter:x/free-model:free"), tier)
            self.assertLess(chain.index("openrouter:x/free-model:free"), chain.index("groq:openai/gpt-oss-120b"), tier)
            self.assertTrue(any(m.startswith("gemini") for m in chain[:cf]), tier)  # Gemini が先

    def test_all_gemini_429_and_claude_api_unavailable_still_makes_articles(self):
        os.environ.pop("ANTHROPIC_API_KEY")
        with patch.object(llm, "_call_gemini", side_effect=RuntimeError("429 RESOURCE_EXHAUSTED PerDay")) as gemini, \
             patch.object(llm, "_call_openai_compatible", side_effect=free_reply):
            writer, checker, grounded = self.make_article()
        self.assertEqual(writer, "cloudflare:@cf/openai/gpt-oss-120b")
        self.assertEqual(checker, "cloudflare:@cf/openai/gpt-oss-120b")
        self.assertTrue(grounded)
        self.assertTrue(gemini.called)
        self.assertFalse(llm.all_exhausted("bulk"))  # 早く終えない

    def test_all_gemini_429_and_cloudflare_down_uses_openrouter_then_groq(self):
        os.environ.pop("ANTHROPIC_API_KEY")

        def only(provider):
            def reply(model, *args):
                if not model.startswith(provider):
                    raise RuntimeError("503 unavailable")
                return free_reply(model, *args)
            return reply

        for provider, expected in (("openrouter", "openrouter:x/free-model:free"), ("groq", "groq:openai/gpt-oss-120b")):
            self.reset()
            with patch.object(llm, "_call_gemini", side_effect=RuntimeError("429 RESOURCE_EXHAUSTED")), \
                 patch.object(llm, "_call_openai_compatible", side_effect=only(provider)):
                writer, checker, _ = self.make_article()
            self.assertEqual((writer, checker), (expected, expected))

    def test_all_gemini_429_with_claude_api_uses_api_for_article_and_gate(self):
        def api(model, system, prompt, schema, max_tokens, use_schema):
            return fake_message(FACT if "grounded" in schema.get("required", []) else ARTICLE)

        with patch.object(llm, "_call_gemini", side_effect=RuntimeError("429 RESOURCE_EXHAUSTED")) as gemini, \
             patch.object(llm, "_call_openai_compatible") as others, patch.object(llm, "_api_request", side_effect=api):
            writer, checker, grounded = self.make_article()
        self.assertTrue(writer.startswith("anthropic-api:"))
        self.assertTrue(checker.startswith("anthropic-api:"))
        self.assertTrue(grounded)
        gemini.assert_not_called()
        others.assert_not_called()

    def test_claude_api_unavailable_falls_back_to_gemini(self):
        with patch.dict(os.environ, {"CLAUDE_API_DISABLE": "1"}), \
             patch.object(llm, "_call_gemini", side_effect=lambda m, s, p, schema, *a, **k: free_reply(m, s, p, schema)), \
             patch.object(llm, "_call_openai_compatible") as others:
            writer, checker, _ = self.make_article()
        self.assertTrue(writer.startswith("gemini"))
        self.assertTrue(checker.startswith("gemini"))
        others.assert_not_called()

    def test_claude_api_errors_fall_back_to_free_models(self):
        with patch.object(llm, "_api_request", side_effect=RuntimeError("503 overloaded")), \
             patch.object(llm, "_call_gemini", side_effect=RuntimeError("429 RESOURCE_EXHAUSTED")), \
             patch.object(llm, "_call_openai_compatible", side_effect=free_reply):
            writer, checker, _ = self.make_article()
        self.assertEqual((writer, checker), ("cloudflare:@cf/openai/gpt-oss-120b", "cloudflare:@cf/openai/gpt-oss-120b"))


class QuotaShareTests(Base):
    def test_shares_leave_at_least_60_percent_for_articles(self):
        self.assertLessEqual(sum(llm.WORKFLOW_SHARES.values()), 0.40 + 1e-9)
        self.assertIsNone(llm.workflow_share_cap("gemini-3.5-flash", "articles"))
        self.assertIsNone(llm.workflow_share_cap("gemini-3.5-flash", "breaking"))
        self.assertEqual(llm.workflow_share_cap("gemini-3.5-flash", "video"), 2)
        self.assertEqual(llm.workflow_share_cap("gemini-3.5-flash", "unknown_job"), 0)
        self.assertIsNone(llm.workflow_share_cap("cloudflare:@cf/openai/gpt-oss-120b", "video"))  # 数の無いモデルは分けない

    def test_other_job_skips_gemini_after_its_share(self):
        os.environ.pop("ANTHROPIC_API_KEY")
        with patch.dict(os.environ, {"LLM_WORKFLOW": "evergreen"}), \
             patch.object(llm, "_call_gemini", side_effect=lambda m, s, p, schema, *a, **k: FACT) as gemini, \
             patch.object(llm, "_call_openai_compatible", side_effect=free_reply):
            models = [llm.generate_json("s", "p", quality.FACT_SCHEMA, tier="quality")[1] for _ in range(4)]
            self.assertEqual(models[:2], ["gemini-3.5-flash", "gemini-3.5-flash"])  # 20回の 10% = 2回
            self.assertEqual(models[2], "gemini-3.6-flash")
            by_job = llm.workflow_usage_by_job()
        self.assertEqual(by_job["evergreen"]["gemini-3.5-flash"], 2)
        self.assertTrue((Path(self.tmp.name) / "llm_usage_evergreen.json").exists())
        self.assertEqual(gemini.call_count, 4)

    def test_articles_can_use_what_others_left_and_counts_add_up(self):
        os.environ.pop("ANTHROPIC_API_KEY")
        (Path(self.tmp.name) / "llm_usage_video.json").write_text(json.dumps({llm._today(): {"video": {"gemini-3.5-flash": 2}}}))
        with patch.dict(os.environ, {"LLM_WORKFLOW": "articles"}), \
             patch.object(llm, "_call_gemini", side_effect=lambda m, s, p, schema, *a, **k: FACT):
            for _ in range(3):
                self.assertEqual(llm.generate_json("s", "p", quality.FACT_SCHEMA, tier="quality")[1], "gemini-3.5-flash")
        self.assertEqual(llm.usage_today()["gemini-3.5-flash"], 5)  # 記事 3 + 動画 2
        self.assertEqual(llm.workflow_usage_by_job()["articles"]["gemini-3.5-flash"], 3)

    def test_workflow_name_from_github(self):
        with patch.dict(os.environ, {"GITHUB_WORKFLOW": "Auto Article Pipeline"}):
            self.assertEqual(llm.current_workflow(), "articles")
        with patch.dict(os.environ, {"GITHUB_WORKFLOW": "Auto Article Pipeline", "LLM_WORKFLOW": "breaking"}):
            self.assertEqual(llm.current_workflow(), "breaking")


class HealthTests(Base):
    def test_two_zero_runs_with_candidates_mark_stalled_then_recover(self):
        self.assertEqual(pipeline_health.record(0, 0, "候補なし"), {})  # 候補が無い回は数えない
        first = pipeline_health.record(0, 5, "品質ゲート不合格: 事実確認を実行できなかった")
        self.assertEqual((first["consecutive_zero"], first.get("status")), (1, None))
        second = pipeline_health.record(0, 4, "無料モデルも Claude API も使えない")
        self.assertEqual((second["consecutive_zero"], second["status"]), (2, "stalled"))
        saved = json.loads(pipeline_health.HEALTH_PATH.read_text())
        self.assertEqual(saved["reason"], "無料モデルも Claude API も使えない")
        ok = pipeline_health.record(3, 4)
        self.assertEqual((ok["status"], ok["consecutive_zero"]), ("ok", 0))
        self.assertNotIn("stalled_since", ok)

    def test_file_is_not_rewritten_when_nothing_changes(self):
        pipeline_health.record(2, 5)
        before = pipeline_health.HEALTH_PATH.stat().st_mtime_ns
        pipeline_health.record(1, 5)
        self.assertEqual(pipeline_health.HEALTH_PATH.stat().st_mtime_ns, before)

    def test_alert_opens_issue_once_and_closes_after_recovery(self):
        pipeline_health.record(0, 5, "x")
        pipeline_health.record(0, 5, "y")
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            out = "https://github.com/o/r/issues/42" if cmd[1:3] == ["issue", "create"] else ""
            return SimpleNamespace(stdout=out, returncode=0)

        with patch("subprocess.run", side_effect=run):
            pipeline_health.alert()
            pipeline_health.alert()  # 2回目は立てない
            self.assertEqual(sum(1 for c in calls if c[1:3] == ["issue", "create"]), 1)
            self.assertIn("pipeline-stalled", calls[[c[1:3] for c in calls].index(["issue", "create"])])
            self.assertEqual(pipeline_health.load()["issue"], "42")
            pipeline_health.record(1, 5)
            pipeline_health.alert()
        self.assertIn(["gh", "issue", "close", "42", "--comment"], [c[:5] for c in calls])
        self.assertNotIn("issue", pipeline_health.load())


if __name__ == "__main__":
    unittest.main()
