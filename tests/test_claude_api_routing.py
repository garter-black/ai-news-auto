"""Claude API（クレジット）の経路: 段ごとのモデル（Opus/Sonnet/Haiku）、予算に合わせた上げ下げ、CLI は速報・SNS・動画だけ、
クレジット切れ・料金の記録。実際の API には一切つながない（_api_request をモックする）。"""

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
NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)  # 日本時間 11/10 0:00（期間 11/4〜12/4 の途中・残り24日・今日まるごと残り）


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
        llm._limit_logged.clear()
        llm._api_summary_logged.clear()

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


def levels(plan):
    return {t: round(v, 4) for t, v in plan["levels"].items() if t in ("sns", "core", "rest")}


class RoutingTests(Base):
    def test_breaking_uses_opus_api_first(self):
        with patch.object(llm, "_api_request", return_value=fake_message()) as api, \
             patch.object(llm, "models_for") as old_route, patch.object(llm, "_call_claude") as cli:
            data, model = llm.generate_json("sys", "prompt", SCHEMA, visibility="high", purpose="breaking")
        self.assertEqual((data, model), ({"answer": "api"}, "anthropic-api:claude-opus-5-5"))
        old_route.assert_not_called()
        cli.assert_not_called()

    def test_tier_models_at_baseline(self):
        with patch.dict(os.environ, {"CLAUDE_API_UPGRADES": "0", "CLAUDE_API_DAILY_BUDGET_USD": "50"}):
            got = {t: llm.api_choose(t, record=False)[0] for t in llm.API_TIERS}
        self.assertEqual(got, {
            "breaking": "claude-opus-5-5", "video": "claude-opus-5-5", "video_review": "claude-sonnet-5-5",
            "check": "claude-haiku-5-5", "helper": "claude-haiku-5-5", "sns": "claude-sonnet-5-5", "core": "claude-sonnet-5-5", "rest": "claude-haiku-5-5",
        })
        with patch.dict(os.environ, {"CLAUDE_API_UPGRADES": "0", "CLAUDE_API_DAILY_BUDGET_USD": "50", "CLAUDE_API_MODEL_LOW": "claude-x"}):
            self.assertEqual(llm.api_choose("rest", record=False)[0], "claude-x")

    def test_no_purpose_never_uses_api_or_cli(self):
        with patch.object(llm, "_api_request") as api, \
             patch.object(llm, "_claude_models", return_value=["claude:sonnet"]), \
             patch.object(llm, "_call_claude") as cli, \
             patch.object(llm, "models_for", return_value=["claude:sonnet", "gemini-test"]), \
             patch.object(llm, "_call_gemini", return_value='{"answer": "free"}'):
            self.assertEqual(llm.generate_json("sys", "prompt", SCHEMA, tier="quality")[1], "gemini-test")
        api.assert_not_called()
        cli.assert_not_called()

    def test_rest_on_haiku_and_free_when_degraded(self):
        with patch.dict(os.environ, {"CLAUDE_API_DAILY_BUDGET_USD": "50", "CLAUDE_API_UPGRADES": "0"}), \
             patch.object(llm, "_api_request", return_value=fake_message()) as api:
            self.assertEqual(llm.generate_json("s", "p", SCHEMA, purpose="rest")[1], "anthropic-api:claude-haiku-5-5")
        with patch.dict(os.environ, {"CLAUDE_API_DAILY_BUDGET_USD": "0.5"}), \
             patch.object(llm, "_api_request") as api, \
             patch.object(llm, "_claude_models", return_value=["claude:sonnet"]), patch.object(llm, "_call_claude") as cli, \
             patch.object(llm, "models_for", return_value=["gemini-test"]), \
             patch.object(llm, "_call_gemini", return_value='{"answer": "free"}'):
            self.assertEqual(llm.generate_json("s", "p", SCHEMA, purpose="rest")[1], "gemini-test")
        api.assert_not_called()
        cli.assert_not_called()  # その他・中心は Claude CLI を使わない

    def test_cli_only_for_breaking_sns_video(self):
        os.environ.pop("ANTHROPIC_API_KEY")
        for purpose, expected in (("sns", "claude:sonnet"), ("video", "claude:sonnet"), ("core", "gemini-test"), ("helper", "gemini-test")):
            with patch.object(llm, "_claude_models", return_value=["claude:sonnet"]), \
                 patch.object(llm, "_call_claude", return_value='{"answer": "cli"}'), \
                 patch.object(llm, "models_for", return_value=["gemini-test"]), \
                 patch.object(llm, "_call_gemini", return_value='{"answer": "free"}'):
                self.assertEqual(llm.generate_json("s", "p", SCHEMA, purpose=purpose)[1], expected, purpose)

    def test_api_failure_falls_back_without_raising(self):
        with patch.object(llm, "_api_request", side_effect=ApiError("overloaded", 529)), \
             patch.object(llm, "_claude_models", return_value=[]), \
             patch.object(llm, "models_for", return_value=["gemini-test"]), \
             patch.object(llm, "_call_gemini", return_value='{"answer": "free"}'):
            self.assertEqual(llm.generate_json("s", "p", SCHEMA, purpose="breaking")[1], "gemini-test")
            llm.generate_json("s", "p", SCHEMA, purpose="breaking")
        self.assertIsNotNone(llm._api_off)  # 一時的なエラーが2回続いたら、この実行では使わない

    def test_refusal_bad_json_and_missing_keys_fall_back(self):
        for message in (fake_message(stop="refusal"), fake_message(stop="max_tokens"), fake_message(text="nope"), fake_message(text='{"x": 1}')):
            with patch.object(llm, "_api_request", return_value=message), \
                 patch.object(llm, "_claude_models", return_value=[]), \
                 patch.object(llm, "models_for", return_value=["gemini-test"]), \
                 patch.object(llm, "_call_gemini", return_value='{"answer": "free"}'):
                self.assertEqual(llm.generate_json("s", "p", SCHEMA, purpose="video")[1], "gemini-test")
        self.assertEqual(llm.api_ledger_summary()["calls_today"]["video"], 5)  # 返事があった分の料金は数える（キー不足は1回出し直し）

    def test_invalid_schema_retries_once_without_structured_output(self):
        with patch.object(llm, "_api_request", side_effect=[ApiError("bad schema", 400), fake_message()]) as api:
            data, _ = llm._try_anthropic_api("s", "p", SCHEMA, 4096, "breaking")
        self.assertEqual(data, {"answer": "api"})
        self.assertEqual([c.args[5] for c in api.call_args_list], [True, False])

    def test_unknown_purpose_does_not_use_api(self):
        self.assertIn("対象外", llm.api_block_reason("article"))

    def test_env_default_purpose(self):
        with patch.dict(os.environ, {"LLM_DEFAULT_PURPOSE": "helper"}), \
             patch.object(llm, "_api_request", return_value=fake_message()):
            self.assertEqual(llm.generate("s", "p", SCHEMA)[1], "anthropic-api:claude-haiku-5-5")
        self.assertEqual(llm.api_ledger_summary()["calls_today"], {"helper": 1})

    def test_all_exhausted_counts_claude_only_for_high(self):
        llm._exhausted.update({"gemini-test"})
        with patch.object(llm, "models_for", return_value=["gemini-test"]), patch.object(llm, "_claude_models", return_value=["claude:sonnet"]):
            self.assertFalse(llm.all_exhausted("quality"))  # API が使えるうちは尽きていない
            os.environ.pop("ANTHROPIC_API_KEY")
            self.assertTrue(llm.all_exhausted("quality"))
            self.assertFalse(llm.all_exhausted("quality", visibility="high"))  # 速報は CLI がある
            llm._exhausted.add("claude:sonnet")
            self.assertTrue(llm.all_exhausted("quality", visibility="high"))

    def test_quality_tier_no_longer_contains_cli(self):
        llm._tiers.clear()
        try:
            with patch.object(llm, "available_models", return_value={"gemini-3.5-flash"}), \
                 patch.object(llm, "_claude_models", return_value=["claude:sonnet"]):
                self.assertNotIn("claude:sonnet", llm.models_for("quality"))
                self.assertNotIn("claude:sonnet", llm.models_for("bulk"))
        finally:
            llm._tiers.clear()


class AdaptiveBudgetTests(Base):
    """予算に合わせた上げ下げ（仮の見込み: 1日 速報7・動画5+5・補助40・SNS13・中心27・その他21 回。
    1回の料金はこのテスト用に固定: Sonnet で 速報0.11・動画0.05・レビュー0.02・補助0.04・記事0.10。Opus=2倍・Haiku=0.05倍）"""

    def setUp(self):
        super().setUp()
        costs = patch.dict(llm.API_COST_MID_DEFAULT, {"breaking": 0.11, "video": 0.05, "video_review": 0.02, "helper": 0.04,
                                                      "sns": 0.10, "core": 0.10, "rest": 0.10})
        costs.start()
        self.addCleanup(costs.stop)
        demand = patch.dict(llm.API_DEMAND_DEFAULT, {"check": 0, "helper": 40})  # 確認の段はこのテストでは数えない
        demand.start()
        self.addCleanup(demand.stop)
        steps = patch.object(llm, "API_UPGRADE_STEPS", [st for st in llm.API_UPGRADE_STEPS if st[0] != "check"])
        steps.start()
        self.addCleanup(steps.stop)

    def plan(self, daily, **env):
        with patch.dict(os.environ, {"CLAUDE_API_DAILY_BUDGET_USD": str(daily), **env}):
            return llm.api_plan()

    def test_95_dollars_rest_haiku_sns_partly_sonnet_core_haiku(self):
        # 守る段 $2.22 + SNS・中心の Haiku $0.20 = $2.42、その他 Haiku $0.105。残り $0.64 で SNS の約半分を Sonnet に
        got = levels(self.plan(95 / 30))
        self.assertEqual((got["rest"], got["core"]), (1.0, 1.0))
        self.assertAlmostEqual(got["sns"], 1 + (95 / 30 - 2.42 - 0.105) / 1.235, places=3)

    def test_190_dollars_full_baseline(self):
        got = levels(self.plan(190 / 30))
        self.assertEqual((got["core"], got["rest"]), (2.0, 1.0))
        self.assertGreaterEqual(got["sns"], 2.0)

    def test_degrade_order_core_then_sns_then_rest_last(self):
        full = 2.42 + 1.235 + 2.565 + 0.105  # SNS・中心 Sonnet、その他 Haiku までの1日の額
        self.assertEqual(levels(self.plan(full, CLAUDE_API_UPGRADES="0")), {"sns": 2.0, "core": 2.0, "rest": 1.0})
        got = levels(self.plan(full - 1.0))            # 足りない → まず中心の一部を Haiku に
        self.assertEqual((got["sns"], got["rest"]), (2.0, 1.0))
        self.assertTrue(1 < got["core"] < 2)
        got = levels(self.plan(2.42 + 0.105 + 0.5))    # もっと足りない → 中心は Haiku、SNS の一部だけ Sonnet（その他は Haiku のまま）
        self.assertEqual((got["core"], got["rest"]), (1.0, 1.0))
        self.assertTrue(1 < got["sns"] < 2)
        got = levels(self.plan(2.42 + 0.05))           # 最後に その他 を無料へ
        self.assertEqual((got["sns"], got["core"]), (1.0, 1.0))
        self.assertTrue(0 < got["rest"] < 1)
        self.assertEqual(levels(self.plan(1.0)), {"sns": 1.0, "core": 1.0, "rest": 0.0})  # 足りなくても SNS・中心は Haiku

    def test_breaking_and_video_never_degrade(self):
        with patch.dict(os.environ, {"CLAUDE_API_DAILY_BUDGET_USD": "0.01"}):
            self.assertEqual(llm.api_choose("breaking", record=False)[0], "claude-opus-5-5")
            self.assertEqual(llm.api_choose("video", record=False)[0], "claude-opus-5-5")
            self.assertEqual(llm.api_choose("video_review", record=False)[0], "claude-sonnet-5-5")
            self.assertEqual(llm.api_choose("sns", record=False)[0], "claude-haiku-5-5")
            self.assertIsNone(llm.api_choose("rest", record=False)[0])  # 予算がほぼ無い日は その他 だけ無料

    def test_underspend_upgrades_next_tiers(self):
        self.assertEqual(levels(self.plan(20)), {"sns": 3.0, "core": 3.0, "rest": 2.0})
        got = levels(self.plan(2.42 + 1.235 + 2.565 + 0.105 + 0.65))  # 余り $0.65 → SNS の半分を Opus に
        self.assertAlmostEqual(got["sns"], 2.5, places=2)
        self.assertEqual((got["core"], got["rest"]), (2.0, 1.0))
        self.assertEqual(levels(self.plan(20, CLAUDE_API_UPGRADES="0")), {"sns": 2.0, "core": 2.0, "rest": 1.0})

    def test_allowance_from_remaining_budget_and_days(self):
        # 前日までに $11 使用・残り24日 → 今日の目安 (95-11)/24 = $3.5。使いすぎた日は翌日の目安が下がる
        self.write_ledger("claude_api_usage.json", {"runs": {"2026-11-05": {"a": {"usd": 11.0}}}})
        s = llm.api_ledger_summary()
        self.assertEqual(s["days_left"], 24)
        self.assertAlmostEqual(s["daily_limit"], 3.5, places=3)
        with patch.dict(os.environ, {"CLAUDE_API_MONTHLY_BUDGET_USD": "190"}):
            self.assertAlmostEqual(llm.api_ledger_summary()["daily_limit"], (190 - 11) / 24, places=3)

    def test_overspent_today_degrades_for_rest_of_day(self):
        self.write_ledger("claude_api_usage.json", {"runs": {"2026-11-10": {"a": {"usd": 5.0}}}})
        with patch.dict(os.environ, {"CLAUDE_API_DAILY_BUDGET_USD": "4"}):
            self.assertEqual(levels(llm.api_plan()), {"sns": 1.0, "core": 1.0, "rest": 0.0})

    def test_less_of_day_left_needs_less_room(self):
        evening = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)  # 日本時間 18:00（残り 1/4 日）
        with patch.object(llm, "_now", return_value=evening):
            got = levels(self.plan(2.0))  # 残りの見込みは $0.6 ほど → 余りで上げられる
        self.assertGreater(got["sns"], 2.0)

    def test_monthly_budget_reached_only_protected_continue(self):
        self.write_ledger("claude_api_usage.json", {"runs": {"2026-11-05": {"a": {"usd": 60.0}}}})
        self.write_ledger("claude_api_usage_video.json", {"runs": {"2026-11-06": {"b": {"usd": 35.5}}}})
        self.assertIsNone(llm.api_choose("core", record=False)[0])
        self.assertEqual(llm.api_choose("breaking", record=False)[0], "claude-opus-5-5")  # 予備 $4 の範囲
        self.write_ledger("claude_api_usage_video.json", {"runs": {"2026-11-06": {"b": {"usd": 39.5}}}})
        self.assertIn("予算", llm.api_choose("breaking", record=False)[1])

    def test_previous_period_does_not_count(self):
        self.write_ledger("claude_api_usage.json", {"runs": {"2026-11-03": {"a": {"usd": 94.0}}}})
        self.assertEqual(llm.api_ledger_summary()["spent"], 0)

    def test_fractional_level_mixes_models_evenly(self):
        llm._api_summary_logged.add("2026-11-10")  # 要約の1行はこのテストでは出さない
        with patch.object(llm, "api_plan", return_value={"levels": {"core": 1.5}}):
            got = [llm.api_choose("core")[0] for _ in range(4)]
        self.assertEqual(got, ["claude-haiku-5-5", "claude-sonnet-5-5"] * 2)
        self.assertEqual(llm.api_ledger_summary()["req_today"], {"core": 4})

    def test_demand_and_cost_learned_from_history(self):
        by = {"core|2": {"n": 4, "usd": 0.6}}
        self.write_ledger("claude_api_usage.json", {"runs": {
            "2026-11-08": {"a": {"req": {"core": 10, "rest": 4}, "by": by}},
            "2026-11-09": {"b": {"req": {"core": 20}}},
        }})
        s = llm.api_ledger_summary()
        self.assertEqual((s["demand"]["core"], s["demand"]["rest"], s["demand"]["breaking"]), (15, 2, 0))
        self.assertAlmostEqual(llm.api_call_cost("core", 2, s), 0.15)
        self.assertAlmostEqual(llm.api_call_cost("core", 1, s), 0.005)  # 実績が無いモデルは仮の値

    def test_daily_safety_caps(self):
        self.write_ledger("claude_api_usage.json", {"runs": {"2026-11-10": {"a": {"usd": 0.5, "calls": {"sns": 30}}}}})
        self.assertIn("安全上限", llm.api_block_reason("sns"))
        self.assertIsNone(llm.api_block_reason("breaking"))
        with patch.dict(os.environ, {"CLAUDE_API_DAILY_CALLS_SNS": "40"}):
            self.assertIsNone(llm.api_block_reason("sns"))

    def test_summary_line_shows_tier_spend_and_mix(self):
        llm.record_api_usage("breaking", "claude-opus-5-5", {"output_tokens": 10_000})
        llm.record_api_usage("core", "claude-sonnet-5-5", {"output_tokens": 10_000})
        line = llm.api_summary_line()
        self.assertIn("速報 $0.20", line)
        self.assertIn("中心 $0.10", line)
        self.assertIn("配分: SNS", line)


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
        saved = json.loads((llm.API_STATE_DIR / "claude_api_usage.json").read_text())
        self.assertNotIn("exhausted_until", saved)  # 次の実行ではまた試す（キーを直せば使える）


class CostTests(Base):
    def test_cost_formula(self):
        usage = {"input_tokens": 10_000, "output_tokens": 5_000, "cache_read_input_tokens": 20_000, "cache_creation_input_tokens": 4_000}
        # Opus 5.5: 入力 $4 / 出力 $20 / キャッシュ読み $0.20 / 書き込み 1.25×$4
        self.assertAlmostEqual(llm.api_cost("claude-opus-5-5", usage), 0.04 + 0.10 + 0.004 + 0.02, places=6)
        self.assertAlmostEqual(llm.api_cost("claude-sonnet-5-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000}), 12.0)
        self.assertAlmostEqual(llm.api_cost("claude-haiku-5-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000}), 0.6)
        self.assertAlmostEqual(llm.api_cost("unknown-model", {"output_tokens": 1_000_000}), 20.0)  # 知らないモデルは多めに

    def test_record_accumulates_per_run_and_day(self):
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "77", "GITHUB_RUN_ATTEMPT": "1", "CLAUDE_API_UPGRADES": "0", "CLAUDE_API_DAILY_BUDGET_USD": "50"}):
            with patch.object(llm, "_api_request", return_value=fake_message(inp=10_000, out=5_000)):
                llm._try_anthropic_api("s", "p", SCHEMA, 1024, "breaking")
                llm._try_anthropic_api("s", "p", SCHEMA, 1024, "sns")
        entry = json.loads((llm.API_STATE_DIR / "claude_api_usage.json").read_text())["runs"]["2026-11-10"]["gh-77-1"]
        self.assertAlmostEqual(entry["usd"], 0.14 + 0.07, places=6)  # Opus $0.14 + Sonnet $0.07
        self.assertEqual(entry["calls"], {"breaking": 1, "sns": 1})
        self.assertEqual(entry["req"], {"breaking": 1, "sns": 1})
        self.assertEqual(set(entry["by"]), {"breaking|3", "sns|2"})  # 段×モデルごとの使用額
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


class UrgentFixTests(Base):
    def test_property_names_like_title_are_kept(self):
        schema = {"type": "OBJECT", "properties": {"title": {"type": "STRING", "title": "x"}, "pattern": {"type": "STRING"},
                                                     "example": {"type": "OBJECT", "properties": {"title": {"type": "STRING"}}, "required": ["title"]}},
                  "required": ["title", "pattern", "example"]}
        out = llm._api_schema(schema)
        self.assertEqual(set(out["properties"]), {"title", "pattern", "example"})
        self.assertEqual(out["required"], ["title", "pattern", "example"])
        self.assertEqual(out["properties"]["title"], {"type": "string"})  # 値の中の注釈 title は外す
        self.assertEqual(out["properties"]["example"]["required"], ["title"])

    def test_all_article_and_check_schemas_survive_conversion(self):
        import followup
        import quality
        import web_articles as w

        for schema in (w.WEB_SCHEMA, w.TREND_SCHEMA, w.FILL_SCHEMA, quality.FACT_SCHEMA, quality.WEB_FACT_SCHEMA,
                       quality.POLISH_SCHEMA, quality.SURGICAL_SCHEMA, followup.CHECK_SCHEMA, followup.UPDATE_SCHEMA):
            out = llm._api_schema(schema)
            self.assertEqual(set(out["properties"]), set(schema["properties"]))
            self.assertEqual(set(out["required"]), set(schema["required"]))

    def test_missing_keys_get_one_repair_retry(self):
        with patch.object(llm, "_api_request", side_effect=[fake_message(text='{"x": 1}'), fake_message()]) as api:
            data, model = llm._try_anthropic_api("s", "PROMPT", SCHEMA, 4096, "breaking")
        self.assertEqual(data, {"answer": "api"})
        self.assertIn("必須キーが足りませんでした: answer", api.call_args_list[1].args[2])

    def test_gate_and_helpers_use_api_when_free_models_are_exhausted(self):
        with patch.object(llm, "_api_request", return_value=fake_message()) as api, \
             patch.object(llm, "models_for", return_value=["gemini-test"]), \
             patch.object(llm, "_call_gemini", side_effect=RuntimeError("429 RESOURCE_EXHAUSTED")):
            self.assertEqual(llm.generate_json("s", "p", SCHEMA, tier="quality", purpose="check")[1], "anthropic-api:claude-haiku-5-5")
        self.assertEqual(api.call_count, 1)

    def test_not_exhausted_while_api_is_available(self):
        llm._exhausted.update({"gemini-test"})
        with patch.object(llm, "models_for", return_value=["gemini-test"]):
            self.assertFalse(llm.all_exhausted("bulk"))
            with patch.dict(os.environ, {"CLAUDE_API_DISABLE": "1"}):
                self.assertTrue(llm.all_exhausted("bulk"))


if __name__ == "__main__":
    unittest.main()
