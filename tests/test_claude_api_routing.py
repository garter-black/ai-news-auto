"""Claude API（クレジット）の経路: 段ごとのモデル（Sonnet/Haiku。自動の処理では Opus を使わない＝2026-11-04 オーナー決定）、
予算に合わせた上げ下げ、CLI は速報・SNS・動画だけ、クレジット切れ・料金の記録。実際の API には一切つながない（_api_request をモックする）。"""

import re

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
        for key in [k for k in os.environ if k.startswith(("CLAUDE_API_", "LLM_DEFAULT_", "CLAUDE_ALLOW_OPUS", "CLAUDE_MODEL"))]:
            os.environ.pop(key)
        self.patches = [
            patch.object(llm, "API_STATE_DIR", tmp),
            patch.object(llm, "USAGE_PATH", tmp / "llm_usage.json"),
            patch.object(llm, "LLM_STATE_DIR", tmp),  # 手元の state/llm_usage_*.json（今日の CLI の回数）を読まない
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


FLOOR = 1.20 + 0.20  # 守る段 + SNS・中心の Haiku（AdaptiveBudgetTests の見込みで）
REST_HAIKU, SNS_UP, CORE_UP, REST_UP = 0.105, 1.235, 2.565, 1.995
FULL = FLOOR + REST_HAIKU + SNS_UP + CORE_UP  # SNS・中心 Sonnet、その他 Haiku までの1日の額


def levels(plan):
    return {t: round(v, 4) for t, v in plan["levels"].items() if t in ("sns", "core", "rest")}


class RoutingTests(Base):
    def test_breaking_uses_sonnet_api_first(self):
        with patch.object(llm, "_api_request", return_value=fake_message()) as api, \
             patch.object(llm, "models_for") as old_route, patch.object(llm, "_call_claude") as cli:
            data, model = llm.generate_json("sys", "prompt", SCHEMA, visibility="high", purpose="breaking")
        self.assertEqual((data, model), ({"answer": "api"}, "anthropic-api:claude-sonnet-5-5"))
        self.assertEqual(api.call_args.args[0], "claude-sonnet-5-5")
        old_route.assert_not_called()
        cli.assert_not_called()

    def test_tier_models_at_baseline(self):
        with patch.dict(os.environ, {"CLAUDE_API_UPGRADES": "0", "CLAUDE_API_DAILY_BUDGET_USD": "50"}):
            got = {t: llm.api_choose(t, record=False)[0] for t in llm.API_TIERS}
        self.assertEqual(got, {
            "breaking": "claude-sonnet-5-5", "video": "claude-sonnet-5-5", "video_review": "claude-sonnet-5-5",
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
                # 速報は API がだめなら無料のモデルへ（動画は Claude だけ: VideoClaudeOnlyTest）
                self.assertEqual(llm.generate_json("s", "p", SCHEMA, purpose="breaking")[1], "gemini-test")
        self.assertEqual(llm.api_ledger_summary()["calls_today"]["breaking"], 5)  # 返事があった分の料金は数える（キー不足は1回出し直し）

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
    1回の料金はこのテスト用に固定: Sonnet で 速報0.11・動画0.05・レビュー0.02・補助0.04・記事0.10。Haiku=0.05倍）
    守る段の1日の額 = 速報 0.77 + 動画 0.25 + レビュー 0.10 + 補助 0.08 = $1.20（どれも Sonnet/Haiku のまま。Opus は使わない）
    SNS・中心の下限（Haiku）$0.20 と合わせて FLOOR = $1.40。その他 Haiku $0.105、SNS Sonnet へ +$1.235、中心 Sonnet へ +$2.565、
    その他 Sonnet へ +$1.995"""

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

    def test_95_dollars_rest_haiku_sns_sonnet_core_partly_sonnet(self):
        # 守る段が Sonnet になって $1.00 浮いた分で、SNS はまるごと Sonnet、残りで中心の一部も Sonnet に
        got = levels(self.plan(95 / 30))
        self.assertEqual((got["rest"], got["sns"]), (1.0, 2.0))
        self.assertAlmostEqual(got["core"], 1 + (95 / 30 - FLOOR - REST_HAIKU - SNS_UP) / CORE_UP, places=3)

    def test_190_dollars_full_baseline_then_rest_upgrades(self):
        got = levels(self.plan(190 / 30))
        self.assertEqual((got["sns"], got["core"]), (2.0, 2.0))  # Sonnet が上限（Opus に上げない）
        self.assertAlmostEqual(got["rest"], 1 + (190 / 30 - FULL) / REST_UP, places=3)  # 余りは その他 を Sonnet に

    def test_degrade_order_core_then_sns_then_rest_last(self):
        self.assertEqual(levels(self.plan(FULL, CLAUDE_API_UPGRADES="0")), {"sns": 2.0, "core": 2.0, "rest": 1.0})
        self.assertEqual(levels(self.plan(FULL)), {"sns": 2.0, "core": 2.0, "rest": 1.0})  # ちょうどなら上げも下げもしない
        got = levels(self.plan(FULL - 1.0))            # 足りない → まず中心の一部を Haiku に
        self.assertEqual((got["sns"], got["rest"]), (2.0, 1.0))
        self.assertTrue(1 < got["core"] < 2)
        got = levels(self.plan(FLOOR + REST_HAIKU + 0.5))  # もっと足りない → 中心は Haiku、SNS の一部だけ Sonnet（その他は Haiku のまま）
        self.assertEqual((got["core"], got["rest"]), (1.0, 1.0))
        self.assertTrue(1 < got["sns"] < 2)
        got = levels(self.plan(FLOOR + 0.05))          # 最後に その他 を無料へ
        self.assertEqual((got["sns"], got["core"]), (1.0, 1.0))
        self.assertTrue(0 < got["rest"] < 1)
        self.assertEqual(levels(self.plan(1.0)), {"sns": 1.0, "core": 1.0, "rest": 0.0})  # 足りなくても SNS・中心は Haiku

    def test_breaking_and_video_never_degrade(self):
        with patch.dict(os.environ, {"CLAUDE_API_DAILY_BUDGET_USD": "0.01"}):
            plan = llm.api_plan()
            for tier, spec in llm.API_TIERS.items():
                if spec[3]:  # 守る段は下限より下げない
                    self.assertEqual(plan["levels"][tier], spec[1], tier)
            self.assertEqual(llm.api_choose("breaking", record=False)[0], "claude-sonnet-5-5")
            self.assertEqual(llm.api_choose("video", record=False)[0], "claude-sonnet-5-5")
            self.assertEqual(llm.api_choose("video_review", record=False)[0], "claude-sonnet-5-5")
            self.assertEqual(llm.api_choose("sns", record=False)[0], "claude-haiku-5-5")
            self.assertIsNone(llm.api_choose("rest", record=False)[0])  # 予算がほぼ無い日は その他 だけ無料

    def test_underspend_upgrades_next_tiers(self):
        self.assertEqual(levels(self.plan(20)), {"sns": 2.0, "core": 2.0, "rest": 2.0})  # 余っても Sonnet まで
        got = levels(self.plan(FULL + 1.0))  # 余り $1.00 → その他 の約半分を Sonnet に（SNS・中心は Opus に上げない）
        self.assertAlmostEqual(got["rest"], 1 + 1.0 / REST_UP, places=3)
        self.assertEqual((got["sns"], got["core"]), (2.0, 2.0))
        self.assertEqual(levels(self.plan(20, CLAUDE_API_UPGRADES="0")), {"sns": 2.0, "core": 2.0, "rest": 1.0})

    def test_check_upgrades_to_sonnet_first_when_budget_allows(self):
        with patch.object(llm, "API_UPGRADE_STEPS", [("check", 1, 2), ("rest", 1, 2)]), \
             patch.dict(llm.API_DEMAND_DEFAULT, {"check": 10}), patch.dict(llm.API_COST_MID_DEFAULT, {"check": 0.04}):
            check_up = 10 * 0.04 * 0.95
            got = self.plan(FULL + 10 * 0.04 * 0.05 + check_up / 2)["levels"]
            self.assertAlmostEqual(got["check"], 1.5, places=3)  # 確認の半分を Sonnet に
            self.assertEqual(got["rest"], 1.0)                   # その他はそのあと
            self.assertEqual(self.plan(50)["levels"]["check"], 2.0)

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
        morning = levels(self.plan(2.0))  # まる1日分の見込みには足りない → SNS の一部だけ Sonnet
        self.assertTrue(1 < morning["sns"] < 2)
        with patch.object(llm, "_now", return_value=evening):
            got = levels(self.plan(2.0))  # 残りの見込みは $1.3 ほど → SNS・中心は Sonnet、余りで その他 も上げられる
        self.assertEqual((got["sns"], got["core"]), (2.0, 2.0))
        self.assertGreater(got["rest"], 1.0)

    def test_monthly_budget_reached_only_protected_continue(self):
        self.write_ledger("claude_api_usage.json", {"runs": {"2026-11-05": {"a": {"usd": 60.0}}}})
        self.write_ledger("claude_api_usage_video.json", {"runs": {"2026-11-06": {"b": {"usd": 35.5}}}})
        self.assertIsNone(llm.api_choose("core", record=False)[0])
        self.assertEqual(llm.api_choose("breaking", record=False)[0], "claude-sonnet-5-5")  # 予備 $4 の範囲
        self.assertEqual(llm.api_choose("video", record=False)[0], "claude-sonnet-5-5")
        self.assertIsNone(llm.api_choose("sns", record=False)[0])  # 下げられる段は予算に到達したら API を使わない
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
        self.assertAlmostEqual(entry["usd"], 0.07 + 0.07, places=6)  # 速報も Sonnet $0.07（Opus なら $0.14 だった）+ SNS Sonnet $0.07
        self.assertEqual(entry["calls"], {"breaking": 1, "sns": 1})
        self.assertEqual(entry["req"], {"breaking": 1, "sns": 1})
        self.assertEqual(set(entry["by"]), {"breaking|2", "sns|2"})  # 段×モデルごとの使用額
        self.assertEqual((entry["in"], entry["out"]), (20_000, 10_000))
        self.assertEqual(json.loads(llm.USAGE_PATH.read_text())[llm._today()]["anthropic-api:claude-sonnet-5-5"], 2)

    def test_old_opus_records_still_counted_as_opus(self):
        llm.record_api_usage("breaking", "claude-opus-5-5", {"output_tokens": 10_000})  # 昔の記録・手で試した分
        entry = json.loads((llm.API_STATE_DIR / "claude_api_usage.json").read_text())["runs"]["2026-11-10"]
        stat = next(iter(entry.values()))["by"]
        self.assertAlmostEqual(stat["breaking|3"]["usd"], 0.20, places=6)

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


class NoOpusPolicyTests(Base):
    """台帳 O8: 自動の処理では Opus を使わない（2026-11-04 オーナー決定。費用を抑えるため）"""

    ROOT = Path(__file__).resolve().parents[1]

    def test_no_tier_can_reach_level_3(self):
        self.assertEqual(llm.API_MAX_LEVEL, 2)
        for tier, (base, low, high, _protected, _label) in llm.API_TIERS.items():
            self.assertLessEqual(max(base, low, high), 2, tier)
        for tier, low, high in llm.API_STEPS + llm.API_UPGRADE_STEPS:
            self.assertLessEqual(high, 2, tier)
        for daily in (0.01, 3.0, 50.0, 10_000.0):  # 予算がどれだけ余っても Sonnet まで
            with patch.dict(os.environ, {"CLAUDE_API_DAILY_BUDGET_USD": str(daily)}):
                plan = llm.api_plan()
                for tier, value in plan["levels"].items():
                    self.assertLessEqual(value, 2.0, (daily, tier))
                for tier in llm.API_TIERS:
                    model = llm.api_choose(tier, record=False)[0]
                    self.assertNotIn("opus", (model or "").lower(), (daily, tier))

    def test_choose_never_returns_opus_even_if_plan_says_3(self):
        llm._api_summary_logged.add("2026-11-10")
        with patch.object(llm, "api_plan", return_value={"levels": {t: 3.0 for t in llm.API_TIERS}}):
            for tier in llm.API_TIERS:
                self.assertNotIn("opus", llm.api_choose(tier)[0], tier)
        self.assertNotIn("opus", llm.api_level_model(3))

    def test_env_overrides_cannot_switch_to_opus(self):
        env = {"CLAUDE_API_MODEL_LOW": "claude-opus-5-5", "CLAUDE_API_MODEL_MID": "claude-opus-5-5",
               "CLAUDE_API_MODEL_TOP": "claude-opus-5-5", "CLAUDE_MODEL": "opus", "CLAUDE_API_DAILY_BUDGET_USD": "50"}
        with patch.dict(os.environ, env):
            for level in (1, 2, 3):
                self.assertEqual(llm.api_level_model(level), "claude-sonnet-5-5", level)
            self.assertEqual(llm.claude_cli_model(), "sonnet")
            with patch.object(llm, "claude_enabled", return_value=True):
                self.assertEqual(llm._claude_models(), ["claude:sonnet"])
        self.assertEqual(llm.CLAUDE_DEFAULT_MODEL, "sonnet")
        with patch.dict(os.environ, {**env, "CLAUDE_ALLOW_OPUS": "1"}):  # 手で試すときだけ（ワークフローでは使わない）
            self.assertEqual(llm.claude_cli_model(), "opus")

    def test_cli_fallback_does_not_request_opus(self):
        os.environ.pop("ANTHROPIC_API_KEY")
        with patch.dict(os.environ, {"CLAUDE_MODEL": "opus"}), \
             patch.object(llm, "claude_enabled", return_value=True), \
             patch.object(llm, "_call_claude", return_value='{"answer": "cli"}') as cli, \
             patch.object(llm, "models_for", return_value=[]):
            self.assertEqual(llm.generate_json("s", "p", SCHEMA, purpose="breaking")[1], "claude:sonnet")
        self.assertEqual(cli.call_args.args[0], "claude:sonnet")

    def test_no_workflow_sets_an_opus_model(self):
        bad = []
        for path in sorted((self.ROOT / ".github" / "workflows").glob("*.y*ml")):
            for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
                code = re.sub(r"(^|\s)#.*$", "", line)  # コメントは除く
                if re.search(r"opus|CLAUDE_ALLOW_OPUS", code, re.I):
                    bad.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual(bad, [], "ワークフローで Opus を指定している（自動の処理では Sonnet まで）")

    def test_no_script_forces_opus(self):
        bad = []
        pattern = re.compile(r"""--model["',\s=]+["']?opus|CLAUDE_(?:API_)?MODEL[A-Z_]*["']?\s*[:=,]\s*["']?(?:claude-)?opus|CLAUDE_ALLOW_OPUS["']?\s*[:=,]\s*["']?1""", re.I)
        for path in sorted((self.ROOT / "scripts").rglob("*")):
            if path.suffix not in (".py", ".sh", ".mjs", ".js", ".ts") or path.name == "llm.py":
                continue
            for n, line in enumerate(path.read_text("utf-8", errors="ignore").splitlines(), 1):
                if pattern.search(line):
                    bad.append(f"{path.relative_to(self.ROOT)}:{n}: {line.strip()}")
        self.assertEqual(bad, [])


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


class TrialScopeTest(unittest.TestCase):
    """（2026-11-04）見本・試走・手動の確かめ（*_trial）の回数は、本番の1日の上限と別に数える"""

    def test_trial_calls_do_not_eat_production_cap(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import llm

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            today = llm._jst_day()
            run = {"runs": {today: {"r1": {"usd": 0.5, "calls": {"video": 25}}}}}
            (d / "claude_api_usage_video_trial.json").write_text(json.dumps(run))
            (d / "claude_api_usage_video.json").write_text(json.dumps({"runs": {today: {"r2": {"usd": 0.1, "calls": {"video": 3}}}}}))
            with patch.object(llm, "API_STATE_DIR", d):
                with patch.dict("os.environ", {"CLAUDE_API_LEDGER": "video", "LLM_WORKFLOW": "video"}):
                    s = llm.api_ledger_summary()
                    self.assertEqual(s["calls_today"].get("video"), 3)  # 本番は本番の分だけ
                    self.assertAlmostEqual(s["spent_today"], 0.6)  # お金は合わせる
                with patch.dict("os.environ", {"CLAUDE_API_LEDGER": "video_trial", "LLM_WORKFLOW": "video_trial"}):
                    self.assertTrue(llm.is_trial())
                    self.assertEqual(llm.api_ledger_summary()["calls_today"].get("video"), 25)

    def test_trial_workflows_use_trial_ledgers(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        for name in ("shorts_v3.yml", "video_template_samples.yml", "video_ladder_dryrun.yml"):
            text = (root / name).read_text("utf-8")
            self.assertIn("CLAUDE_API_LEDGER: video_trial", text, name)
            self.assertIn("LLM_WORKFLOW: video_trial", text, name)
        self.assertIn("CLAUDE_API_LEDGER: video\n", (root / "sns_video.yml").read_text("utf-8"))


class MaintenanceScopeTest(unittest.TestCase):
    """（2026-11-04）今ある記事の手入れ（types・titles などの書き直し）の回数は、新しい記事の1日の上限と別に数える。
    記事の型の書き直しが「中心」を127回使い、新しい投資・マネー／不動産の記事が無料モデルに落ちて全部不合格になった"""

    def test_rewrite_jobs_do_not_eat_article_caps(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import llm

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            today = llm._jst_day()
            (d / "claude_api_usage_types.json").write_text(json.dumps({"runs": {today: {"r1": {"usd": 9.0, "calls": {"core": 127}}}}}))
            (d / "claude_api_usage_titles.json").write_text(json.dumps({"runs": {today: {"r3": {"usd": 1.0, "calls": {"core": 5}}}}}))
            (d / "claude_api_usage.json").write_text(json.dumps({"runs": {today: {"r2": {"usd": 1.0, "calls": {"core": 4}}}}}))
            with patch.object(llm, "API_STATE_DIR", d):
                with patch.dict("os.environ", {"CLAUDE_API_LEDGER": "", "LLM_WORKFLOW": "articles"}):
                    s = llm.api_ledger_summary()
                    self.assertEqual(s["calls_today"].get("core"), 4)  # 新しい記事は新しい記事の分だけ
                    self.assertAlmostEqual(s["spent_today"], 11.0)  # お金（今日の目安額）は全部を合わせる
                with patch.dict("os.environ", {"CLAUDE_API_LEDGER": "types", "LLM_WORKFLOW": "products"}):
                    self.assertEqual(llm.api_ledger_summary()["calls_today"].get("core"), 132)  # 手入れどうしは合わせる

    def test_rewrite_workflows_use_maintenance_ledgers(self):
        from pathlib import Path

        import llm

        root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        for name, ledger in (("types_rewrite.yml", "types"), ("title_rewrite.yml", "titles")):
            self.assertIn(f"CLAUDE_API_LEDGER: {ledger}", (root / name).read_text("utf-8"), name)
            self.assertIn(ledger, llm.MAINTENANCE_LEDGERS)
        self.assertNotIn("video", llm.MAINTENANCE_LEDGERS)  # SNS の動画は本番と同じ上限


class VideoClaudeOnlyTest(unittest.TestCase):
    """（2026-11-04 決定）動画の台本・画像の確認は Claude だけ。上限なら本当の理由を出す（Gemini に回さない）"""

    def test_video_never_falls_back_to_free_models_and_says_cap(self):
        import llm

        called = []
        with patch.object(llm, "_try_anthropic_api", return_value=None), patch.object(llm, "_claude_models", return_value=[]), \
                patch.object(llm, "models_for", side_effect=lambda tier: called.append(tier) or ["gemini-x"]), \
                patch.object(llm, "api_ledger_summary", return_value={"calls_today": {"video": 60}}), \
                patch.object(llm, "_daily_calls_cap", return_value=60), patch.object(llm, "api_block_reason", return_value="video の今日の安全上限 60 回に到達"), \
                patch.object(llm, "cli_purpose_calls", return_value=150), patch.object(llm, "cli_purpose_cap", return_value=150), \
                patch.object(llm, "claude_enabled", return_value=True):
            with self.assertRaises(RuntimeError) as caught:
                llm.generate_json("s", "p", {"type": "OBJECT", "properties": {"a": {"type": "STRING"}}, "required": ["a"]}, purpose="video")
        self.assertEqual(called, [])  # 無料のモデルの一覧すら作らない
        self.assertIn("Claude の1日の上限に達しました（台本用 60/60 回・CLI 150/150 回）", str(caught.exception))
        self.assertNotIn("GEMINI", str(caught.exception))


class VideoCliAfterApiCapTest(unittest.TestCase):
    """（2026-11-04 オーナー「止まるの勿体無い」）動画の API の上限のあとは Claude CLI（サブスクリプション）で続ける。
    CLI はほかの仕事の上限（CLAUDE_DAILY_MAX）と別に、台本・画像の確認ごとに数える（既定 150 回）"""

    SCHEMA = {"type": "OBJECT", "properties": {"a": {"type": "STRING"}}, "required": ["a"]}

    def test_script_continues_on_cli_even_when_shared_cli_cap_is_used(self):
        import llm

        with patch.object(llm, "_try_anthropic_api", return_value=None), patch.object(llm, "_claude_models", return_value=["claude:sonnet"]), \
                patch.object(llm, "claude_calls_today", return_value=40), patch.object(llm, "_claude_daily_max", return_value=40), \
                patch.object(llm, "cli_purpose_calls", return_value=12), patch.object(llm, "_call_claude", return_value='{"a": "ok"}') as cli, \
                patch.object(llm, "_record_usage") as rec:
            data, model = llm.generate_json("s", "p", self.SCHEMA, purpose="video")
        self.assertEqual((data, model), ({"a": "ok"}, "claude:sonnet"))
        self.assertTrue(cli.called)
        rec.assert_called_with("claude:sonnet#video")  # 台本用として別に数える

    def test_cli_cap_for_video_is_large_and_separate(self):
        import llm

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CLI_DAILY_VIDEO", None)
            self.assertGreaterEqual(llm.cli_purpose_cap("video"), 150)
            self.assertGreaterEqual(llm.cli_purpose_cap("video_review"), 150)
        self.assertEqual(llm.cli_usage_key("claude:sonnet", "breaking"), "claude:sonnet")

    def test_image_review_falls_back_to_cli(self):
        import llm

        with patch.object(llm, "api_choose", return_value=(None, "video_review の今日の安全上限 40 回に到達")), \
                patch.object(llm, "_claude_models", return_value=["claude:sonnet"]), patch.object(llm, "cli_purpose_calls", return_value=0), \
                patch.object(llm, "_call_claude_images", return_value='{"a": "ok"}') as cli, patch.object(llm, "_record_usage"):
            data, model = llm.generate_json_images("s", "p", [b"jpg"], self.SCHEMA)
        self.assertEqual((data, model), ({"a": "ok"}, "claude:sonnet"))
        self.assertEqual(cli.call_args.args[3], [b"jpg"])

    def test_both_caps_reached_gives_clear_message(self):
        import llm

        with patch.object(llm, "_try_anthropic_api", return_value=None), patch.object(llm, "_claude_models", return_value=["claude:sonnet"]), \
                patch.object(llm, "cli_purpose_calls", return_value=150), patch.object(llm, "cli_purpose_cap", return_value=150), \
                patch.object(llm, "claude_enabled", return_value=True), patch.object(llm, "api_ledger_summary", return_value={"calls_today": {"video": 60}}), \
                patch.object(llm, "_daily_calls_cap", return_value=60), patch.object(llm, "api_block_reason", return_value="video の今日の安全上限 60 回に到達"):
            with self.assertRaises(RuntimeError) as caught:
                llm.generate_json("s", "p", self.SCHEMA, purpose="video")
        self.assertIn("Claude の1日の上限に達しました（台本用 60/60 回・CLI 150/150 回）", str(caught.exception))
