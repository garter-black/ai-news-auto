"""Claude API（クレジット）の経路: 目立つものだけ API → CLI → 無料、ほかは無料だけ。予算・クレジット切れ・料金の記録。
実際の API には一切つながない（_api_request をモックする）。"""

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

SCHEMA = {"type": "OBJECT", "properties": {"answer": {"type": "STRING"}}, "required": ["answer"]}
NOW = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)  # 期間 11/4〜12/4 の途中（日本時間 11/10）


def fake_message(text='{"answer": "api"}', stop="end_turn", inp=1000, out=500, read=0, write=0):
    usage = SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=read, cache_creation_input_tokens=write)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, usage=usage)


class ApiError(Exception):
    def __init__(self, message, status_code=None, type_=None):
        super().__init__(message)
        self.status_code = status_code
        self.type = type_


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}, clear=False)
        self.env.start()
        for key in [k for k in os.environ if k.startswith(("CLAUDE_API_", "LLM_DEFAULT_"))]:
            os.environ.pop(key)
        self.patches = [
            patch.object(llm, "API_STATE_DIR", tmp),
            patch.object(llm, "USAGE_PATH", tmp / "llm_usage.json"),
            patch.object(llm, "_now", return_value=NOW),
        ]
        for p in self.patches:
            p.start()
        llm._api_off = None
        llm._api_transient_failures = 0
        llm._exhausted.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        llm._api_off = None
        llm._api_transient_failures = 0
        llm._exhausted.clear()
        self.tmp.cleanup()

    def write_ledger(self, name, data):
        (llm.API_STATE_DIR / name).write_text(json.dumps(data), "utf-8")


class RoutingTests(Base):
    def test_high_visibility_uses_api_first(self):
        with patch.object(llm, "_api_request", return_value=fake_message()) as api, \
             patch.object(llm, "models_for") as old_route, patch.object(llm, "_call_claude") as cli:
            data, model = llm.generate_json("sys", "prompt", SCHEMA, visibility="high", purpose="breaking")
        self.assertEqual(data, {"answer": "api"})
        self.assertEqual(model, "anthropic-api:claude-opus-5-5")
        self.assertEqual(api.call_args.args[0], "claude-opus-5-5")
        old_route.assert_not_called()
        cli.assert_not_called()

    def test_models_per_purpose(self):
        self.assertEqual(llm.api_model_for("breaking"), "claude-opus-5-5")
        self.assertEqual(llm.api_model_for("video"), "claude-opus-5-5")
        self.assertEqual(llm.api_model_for("sns"), "claude-sonnet-5-5")
        self.assertEqual(llm.api_model_for("video_review"), "claude-sonnet-5-5")
        with patch.dict(os.environ, {"CLAUDE_API_MODEL_SNS": "claude-opus-5-5"}):
            self.assertEqual(llm.api_model_for("sns"), "claude-opus-5-5")

    def test_normal_items_never_use_api_or_cli(self):
        with patch.object(llm, "_api_request") as api, \
             patch.object(llm, "_claude_models", return_value=["claude:sonnet"]), \
             patch.object(llm, "_call_claude") as cli, \
             patch.object(llm, "models_for", return_value=["claude:sonnet", "gemini-test"]), \
             patch.object(llm, "_call_gemini", return_value='{"answer": "free"}'):
            data, model = llm.generate_json("sys", "prompt", SCHEMA, tier="quality")
        self.assertEqual((data, model), ({"answer": "free"}, "gemini-test"))
        api.assert_not_called()
        cli.assert_not_called()

    def test_high_visibility_falls_back_to_cli_then_free(self):
        os.environ.pop("ANTHROPIC_API_KEY")  # API が使えない → CLI
        with patch.object(llm, "_claude_models", return_value=["claude:sonnet"]), \
             patch.object(llm, "_call_claude", return_value='{"answer": "cli"}'), \
             patch.object(llm, "models_for", return_value=["gemini-test"]):
            self.assertEqual(llm.generate_json("s", "p", SCHEMA, visibility="high", purpose="sns")[1], "claude:sonnet")
        with patch.object(llm, "_claude_models", return_value=["claude:sonnet"]), \
             patch.object(llm, "_call_claude", side_effect=llm.CliQuotaError("usage limit")), \
             patch.object(llm, "models_for", return_value=["gemini-test"]), \
             patch.object(llm, "_call_gemini", return_value='{"answer": "free"}'):
            self.assertEqual(llm.generate_json("s", "p", SCHEMA, visibility="high", purpose="sns")[1], "gemini-test")

    def test_api_failure_falls_back_without_raising(self):
        with patch.object(llm, "_api_request", side_effect=ApiError("overloaded", 529)), \
             patch.object(llm, "_claude_models", return_value=[]), \
             patch.object(llm, "models_for", return_value=["gemini-test"]), \
             patch.object(llm, "_call_gemini", return_value='{"answer": "free"}'):
            self.assertEqual(llm.generate_json("s", "p", SCHEMA, visibility="high", purpose="breaking")[1], "gemini-test")
            llm.generate_json("s", "p", SCHEMA, visibility="high", purpose="breaking")
        self.assertIsNotNone(llm._api_off)  # 一時的なエラーが2回続いたら、この実行では使わない

    def test_refusal_bad_json_and_missing_keys_fall_back(self):
        for message in (fake_message(stop="refusal"), fake_message(stop="max_tokens"), fake_message(text="nope"), fake_message(text='{"x": 1}')):
            with patch.object(llm, "_api_request", return_value=message), \
                 patch.object(llm, "_claude_models", return_value=[]), \
                 patch.object(llm, "models_for", return_value=["gemini-test"]), \
                 patch.object(llm, "_call_gemini", return_value='{"answer": "free"}'):
                self.assertEqual(llm.generate_json("s", "p", SCHEMA, visibility="high", purpose="video")[1], "gemini-test")
        self.assertEqual(llm.api_ledger_summary()["calls_today"]["video"], 4)  # 返事があった分の料金は数える

    def test_invalid_schema_retries_once_without_structured_output(self):
        with patch.object(llm, "_api_request", side_effect=[ApiError("bad schema", 400), fake_message()]) as api:
            data, _ = llm._try_anthropic_api("s", "p", SCHEMA, 4096, "breaking")
        self.assertEqual(data, {"answer": "api"})
        self.assertEqual([c.args[5] for c in api.call_args_list], [True, False])

    def test_unknown_purpose_does_not_use_api(self):
        self.assertIn("対象外", llm.api_block_reason("article"))

    def test_env_default_visibility_for_video_job(self):
        with patch.dict(os.environ, {"LLM_DEFAULT_VISIBILITY": "high", "LLM_DEFAULT_PURPOSE": "video"}), \
             patch.object(llm, "_api_request", return_value=fake_message()):
            self.assertEqual(llm.generate("s", "p", SCHEMA)[1], "anthropic-api:claude-opus-5-5")
        self.assertEqual(llm.api_ledger_summary()["calls_today"], {"video": 1})

    def test_all_exhausted_counts_claude_only_for_high(self):
        llm._exhausted.update({"gemini-test"})
        with patch.object(llm, "models_for", return_value=["gemini-test"]), patch.object(llm, "_claude_models", return_value=[]):
            self.assertTrue(llm.all_exhausted("quality"))
            self.assertFalse(llm.all_exhausted("quality", visibility="high"))
            os.environ.pop("ANTHROPIC_API_KEY")
            self.assertTrue(llm.all_exhausted("quality", visibility="high"))

    def test_quality_tier_no_longer_contains_cli(self):
        llm._tiers.clear()
        try:
            with patch.object(llm, "available_models", return_value={"gemini-3.5-flash"}), \
                 patch.object(llm, "_claude_models", return_value=["claude:sonnet"]):
                self.assertEqual(llm.models_for("quality"), ["gemini-3.5-flash"])
                self.assertNotIn("claude:sonnet", llm.models_for("bulk"))
        finally:
            llm._tiers.clear()


class BudgetTests(Base):
    def ledger(self, day, usd, calls=None, key="gh-1-1"):
        return {"runs": {day: {key: {"usd": usd, "calls": calls or {}}}}}

    def test_monthly_budget_stops_api(self):
        self.write_ledger("claude_api_usage.json", self.ledger("2026-11-05", 60.0))
        self.write_ledger("claude_api_usage_video.json", self.ledger("2026-11-06", 35.5))
        self.assertIn("予算", llm.api_block_reason("breaking"))
        with patch.object(llm, "_api_request") as api, patch.object(llm, "_claude_models", return_value=[]), \
             patch.object(llm, "models_for", return_value=["gemini-test"]), \
             patch.object(llm, "_call_gemini", return_value='{"answer": "free"}'):
            self.assertEqual(llm.generate_json("s", "p", SCHEMA, visibility="high", purpose="breaking")[1], "gemini-test")
        api.assert_not_called()

    def test_previous_period_does_not_count(self):
        self.write_ledger("claude_api_usage.json", self.ledger("2026-11-03", 94.0))
        self.assertIsNone(llm.api_block_reason("breaking"))

    def test_daily_pacing_and_breaking_reserve(self):
        # 期間の残り 24日（11/10〜12/3）。前日までに $11 使用 → 今日の目安 (95-11)/24 = $3.5
        self.write_ledger("claude_api_usage.json", {"runs": {"2026-11-05": {"a": {"usd": 11.0}}, "2026-11-10": {"b": {"usd": 2.9}}}})
        s = llm.api_ledger_summary()
        self.assertEqual(s["days_left"], 24)
        self.assertAlmostEqual(s["daily_limit"], 3.5, places=3)
        self.assertIsNone(llm.api_block_reason("breaking"))       # 速報は目安いっぱいまで
        self.assertIn("目安", llm.api_block_reason("sns"))         # sns・video は 75% ($2.625) で止まる
        self.assertIn("目安", llm.api_block_reason("video"))

    def test_daily_call_caps_per_class(self):
        self.write_ledger("claude_api_usage.json", self.ledger("2026-11-10", 0.5, {"sns": 10, "breaking": 3}))
        self.assertIn("上限", llm.api_block_reason("sns"))
        self.assertIsNone(llm.api_block_reason("breaking"))
        with patch.dict(os.environ, {"CLAUDE_API_DAILY_CALLS_SNS": "12"}):
            self.assertIsNone(llm.api_block_reason("sns"))

    def test_env_budget_override(self):
        self.write_ledger("claude_api_usage.json", self.ledger("2026-11-05", 30.0))
        with patch.dict(os.environ, {"CLAUDE_API_MONTHLY_BUDGET_USD": "25"}):
            self.assertIn("予算", llm.api_block_reason("breaking"))


class ExhaustedTests(Base):
    def test_credit_error_marks_exhausted_until_refill(self):
        error = ApiError("Your credit balance is too low to access the Anthropic API.", 400, "invalid_request_error")
        with patch.object(llm, "_api_request", side_effect=error) as api, \
             patch.object(llm, "_claude_models", return_value=[]), \
             patch.object(llm, "models_for", return_value=["gemini-test"]), \
             patch.object(llm, "_call_gemini", return_value='{"answer": "free"}'):
            self.assertEqual(llm.generate_json("s", "p", SCHEMA, visibility="high", purpose="breaking")[1], "gemini-test")
            self.assertEqual(api.call_count, 1)  # 構造化出力の再試行はしない
        saved = json.loads((llm.API_STATE_DIR / "claude_api_usage.json").read_text())
        self.assertEqual(saved["exhausted_until"], "2026-12-04T00:00:00Z")
        llm._api_off = None  # 別の実行でも、印のファイルを見て使わない
        self.assertIn("クレジット切れ", llm.api_block_reason("video"))
        with patch.object(llm, "_now", return_value=datetime(2026, 12, 4, 0, 1, tzinfo=timezone.utc)):
            self.assertIsNone(llm.api_block_reason("video"))  # 補充日を過ぎたら再開

    def test_billing_error_kinds(self):
        self.assertEqual(llm._api_error_kind(ApiError("x", 402, "billing_error")), "exhausted")
        self.assertEqual(llm._api_error_kind(ApiError("Payment required", None)), "exhausted")
        self.assertEqual(llm._api_error_kind(ApiError("invalid x-api-key", 401)), "auth")
        self.assertEqual(llm._api_error_kind(ApiError("rate_limit_error", 429)), "transient")
        self.assertEqual(llm._api_error_kind(ApiError("bad", 400)), "invalid")

    def test_exhausted_right_after_refill_waits_only_hours(self):
        with patch.object(llm, "_now", return_value=datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)):
            self.assertEqual(llm.mark_api_exhausted("credit balance"), "2026-11-04T05:00:00Z")

    def test_exhausted_mark_in_other_ledger_is_respected(self):
        self.write_ledger("claude_api_usage_video.json", {"exhausted_until": "2026-12-04T00:00:00Z", "exhausted_reason": "credit"})
        self.assertIn("クレジット切れ", llm.api_block_reason("breaking"))

    def test_auth_error_disables_for_run_only(self):
        with patch.object(llm, "_api_request", side_effect=ApiError("invalid x-api-key", 401)):
            self.assertIsNone(llm._try_anthropic_api("s", "p", SCHEMA, 1024, "breaking"))
        self.assertIn("認証", llm.api_block_reason("breaking"))
        self.assertFalse((llm.API_STATE_DIR / "claude_api_usage.json").exists())


class CostTests(Base):
    def test_cost_formula(self):
        usage = {"input_tokens": 10_000, "output_tokens": 5_000, "cache_read_input_tokens": 20_000, "cache_creation_input_tokens": 4_000}
        # Opus 5.5: 入力 $4 / 出力 $20 / キャッシュ読み $0.20 / 書き込み 1.25×$4
        self.assertAlmostEqual(llm.api_cost("claude-opus-5-5", usage), 0.04 + 0.10 + 0.004 + 0.02, places=6)
        self.assertAlmostEqual(llm.api_cost("claude-sonnet-5-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000}), 12.0)
        self.assertAlmostEqual(llm.api_cost("claude-haiku-5-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000}), 0.6)
        self.assertAlmostEqual(llm.api_cost("unknown-model", {"output_tokens": 1_000_000}), 20.0)  # 知らないモデルは多めに

    def test_record_accumulates_per_run_and_day(self):
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "77", "GITHUB_RUN_ATTEMPT": "1"}):
            with patch.object(llm, "_api_request", return_value=fake_message(inp=10_000, out=5_000)):
                llm._try_anthropic_api("s", "p", SCHEMA, 1024, "breaking")
                llm._try_anthropic_api("s", "p", SCHEMA, 1024, "sns")
        entry = json.loads((llm.API_STATE_DIR / "claude_api_usage.json").read_text())["runs"]["2026-11-10"]["gh-77-1"]
        self.assertAlmostEqual(entry["usd"], 0.14 + 0.07, places=6)  # Opus $0.14 + Sonnet $0.07
        self.assertEqual(entry["calls"], {"breaking": 1, "sns": 1})
        self.assertEqual((entry["in"], entry["out"]), (20_000, 10_000))
        self.assertEqual(json.loads(llm.USAGE_PATH.read_text())[llm._today()]["anthropic-api:claude-opus-5-5"], 1)

    def test_ledger_name_from_env(self):
        with patch.dict(os.environ, {"CLAUDE_API_LEDGER": "video"}):
            llm.record_api_usage("video", "claude-opus-5-5", {"output_tokens": 100})
        self.assertTrue((llm.API_STATE_DIR / "claude_api_usage_video.json").exists())

    def test_merge_keeps_both_runs_and_larger_same_run(self):
        mine = {"runs": {"2026-11-10": {"gh-2-1": {"usd": 0.5}, "gh-1-1": {"usd": 0.2}}}, "exhausted_until": "2026-12-04T00:00:00Z"}
        dest = {"runs": {"2026-11-10": {"gh-1-1": {"usd": 0.3}}, "2026-11-09": {"gh-0-1": {"usd": 1.0}}}}
        merged = llm.merge_api_usage(mine, dest)
        self.assertEqual(merged["runs"]["2026-11-10"], {"gh-2-1": {"usd": 0.5}, "gh-1-1": {"usd": 0.3}})
        self.assertEqual(merged["runs"]["2026-11-09"], {"gh-0-1": {"usd": 1.0}})
        self.assertEqual(merged["exhausted_until"], "2026-12-04T00:00:00Z")

    def test_old_days_are_pruned_to_history(self):
        self.write_ledger("claude_api_usage.json", {"runs": {"2026-09-20": {"a": {"usd": 50.0}}, "2026-10-20": {"b": {"usd": 1.0}}}})
        llm.record_api_usage("breaking", "claude-opus-5-5", {"output_tokens": 0})
        saved = json.loads((llm.API_STATE_DIR / "claude_api_usage.json").read_text())
        self.assertNotIn("2026-09-20", saved["runs"])
        self.assertIn("2026-10-20", saved["runs"])  # 前の期間までは残す
        self.assertEqual(saved["history"], {"2026-09": 50.0})

    def test_structured_output_schema(self):
        schema = {"type": "OBJECT", "propertyOrdering": ["a"], "properties": {
            "a": {"type": "STRING", "maxLength": 10, "nullable": True},
            "b": {"type": "ARRAY", "minItems": 1, "items": {"type": "OBJECT", "properties": {"c": {"type": "INTEGER", "minimum": 0}}, "required": ["c", "zz"]}},
        }, "required": ["a"]}
        out = llm._api_schema(schema)
        self.assertEqual(out["additionalProperties"], False)
        self.assertEqual(out["properties"]["a"], {"type": ["string", "null"]})
        item = out["properties"]["b"]["items"]
        self.assertEqual(item, {"type": "object", "properties": {"c": {"type": "integer"}}, "required": ["c"], "additionalProperties": False})
        self.assertNotIn("minItems", out["properties"]["b"])


class SnsSelectionTests(unittest.TestCase):
    def test_sns_likely_matches_threads_score_signal(self):
        import web_articles as w

        self.assertTrue(w.sns_likely({"title": "Introducing GPT-6", "source_name": "OpenAI"}))
        self.assertTrue(w.sns_likely({"title": "Gemini の新機能", "source_name": "Google Japan Blog"}))
        self.assertFalse(w.sns_likely({"title": "Azure SQL のアップデート", "source_name": "Microsoft"}))
        self.assertFalse(w.sns_likely({"title": "新しいワークフロー機能", "source_name": "Notion"}))


if __name__ == "__main__":
    unittest.main()
