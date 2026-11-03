"""料金の段の表（scripts/pricing.py・scripts/pricing_watch.py）。保存した公式ページで読み取りを確かめる。ネットワークは使わない。"""
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import page_watch  # noqa: E402
import pricing  # noqa: E402
import pricing_watch as w  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "pricing"
TODAY = date(2026, 11, 3)
NOW = datetime(2026, 11, 3, 0, 30, tzinfo=timezone.utc)
FX = {"base": "USD", "quote": "JPY", "rate": 157.9, "date": "2026-11-02", "source": "ECB参照レート（ユーロ経由で計算）", "sourceUrl": w.ECB_URL}
ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time='2026-11-02'><Cube currency='USD' rate='1.1216'/><Cube currency='JPY' rate='177.10'/></Cube></Cube></gesmes:Envelope>"""

FILES = {
    "https://claude.com/pricing": "claude_pricing_ja.html",
    "https://platform.claude.com/docs/en/about-claude/pricing.md": "claude_api_pricing.md",
    "https://developers.openai.com/api/docs/pricing.md": "openai_api_pricing.md",
    "https://aistudio.google.com/docs/pricing.md": "gemini_api_pricing.md",
    "https://gemini.google/jp/subscriptions/?hl=ja": "gemini_subscriptions_jp.html",
    "https://www.microsoft.com/ja-jp/microsoft-365/buy/compare-all-microsoft-365-products": "ms365_compare_jp.html",
    "https://www.microsoft.com/ja-jp/microsoft-365/copilot/business": "ms_copilot_business_jp.md",
    "https://www.microsoft.com/ja-jp/microsoft-365/copilot/enterprise": "ms_copilot_enterprise_jp.md",
    "https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus": "openai_help_plus.md",
    "https://help.openai.com/en/articles/9793128-about-chatgpt-pro-tiers": "openai_help_pro.md",
    "https://help.openai.com/en/articles/8792828-what-is-chatgpt-business": "openai_help_business.md",
}


def fixture(name: str) -> str:
    return (FIX / name).read_text("utf-8")


def fixture_fetcher(source: dict, edits: dict | None = None) -> str:
    text = fixture(FILES[source["url"]])
    for a, b in (edits or {}).get(source["url"], []):
        assert a in text, a
        text = text.replace(a, b)
    return text


def docs_for(product: str) -> dict:
    return {s["key"]: fixture_fetcher(s) for s in w.SPECS[product]["sources"]}


def prices(plan: dict) -> list[tuple]:
    return [(p["amount"], p["currency"], p.get("billing")) for p in plan["prices"]]


def by_id(items: list[dict]) -> dict:
    return {i["id"]: i for i in items}


class ParserTest(unittest.TestCase):
    def test_claude_japanese_and_english_pages(self):
        for name in ("claude_pricing_ja.html", "claude_pricing_en.html"):
            plans = by_id(w.extract_claude({"pricing": fixture(name)}, TODAY)["plans"])
            self.assertEqual(prices(plans["free"]), [(0, "USD", None)])
            self.assertEqual(prices(plans["pro"]), [(17, "USD", "annual"), (20, "USD", "monthly")])
            self.assertEqual(plans["pro"]["prices"][0]["billedAs"], 200)
            self.assertEqual(prices(plans["max"]), [(100, "USD", "monthly")])
            self.assertEqual(prices(plans["ultra"]), [(200, "USD", "monthly")])
            self.assertEqual(prices(plans["team-standard"]), [(20, "USD", "annual"), (25, "USD", "monthly")])
            self.assertEqual(prices(plans["team-premium"]), [(100, "USD", "annual"), (125, "USD", "monthly")])
            self.assertEqual(prices(plans["enterprise"]), [(20, "USD", "annual")])
            self.assertTrue(all(p["taxIncluded"] is False for pl in plans.values() for p in pl["prices"]))
        ja = by_id(w.extract_claude({"pricing": fixture("claude_pricing_ja.html")}, TODAY)["plans"])
        self.assertIn("Pro の 10 倍の使用量（毎週）", ja["ultra"]["limits"])
        self.assertIn("5 時間制限なし", ja["ultra"]["limits"])

    def test_claude_api(self):
        models = w.extract_claude_api({"pricing": fixture("claude_api_pricing.md")}, TODAY)["models"]
        opus = models[0]
        self.assertEqual((opus["id"], opus["input"], opus["output"], opus["cacheRead"]), ("claude-opus-5-5", 4, 20, 0.2))
        self.assertIn("0.05x", opus["notes"][0])
        haiku = [m for m in models if m["id"] == "claude-haiku-5-5"]
        self.assertEqual([(m["input"], m["output"], m["primary"]) for m in haiku], [(0.1, 0.5, True), (0.5, 2.5, False)])

    def test_openai_api_uses_standard_not_batch(self):
        models = w.extract_openai_api({"pricing": fixture("openai_api_pricing.md")}, TODAY)["models"]
        main = {m["id"]: m for m in models if m["primary"]}
        self.assertEqual((main["gpt-6-astra"]["input"], main["gpt-6-astra"]["output"]), (10, 50))
        self.assertEqual((main["gpt-6.1-sol"]["input"], main["gpt-6.1-sol"]["output"]), (2, 10))
        self.assertEqual((main["gpt-6.1-terra"]["input"], main["gpt-6.1-terra"]["output"]), (1, 5))
        self.assertEqual(main["gpt-6-astra"]["tier"], "入力≤272K")
        long = [m for m in models if m["id"] == "gpt-6-astra" and not m["primary"]][0]
        self.assertEqual((long["input"], long["output"]), (20, 75))

    def test_gemini_api_scheduled_price_and_missing_model(self):
        md = fixture("gemini_api_pricing.md")
        models = {m["id"]: m for m in w.extract_gemini_api({"pricing": md}, TODAY)["models"]}
        argon = models["gemini-4-argon"]
        self.assertEqual((argon["input"], argon["output"], argon["cacheRead"]), (2, 10, 0.1))
        self.assertEqual(argon["scheduled"], [{"from": "2026-12-01", "input": 4, "output": 20, "cacheRead": 0.2}])
        self.assertEqual(models["gemini-4-carbon"]["status"], "not_listed")
        # 予定日を過ぎたら新しい価格が「今の価格」になる
        later = {m["id"]: m for m in w.extract_gemini_api({"pricing": md}, date(2026, 12, 1))["models"]}
        self.assertEqual((later["gemini-4-argon"]["input"], later["gemini-4-argon"]["output"]), (4, 20))
        self.assertNotIn("scheduled", later["gemini-4-argon"])

    def test_gemini_plans_in_yen(self):
        plans = by_id(w.extract_gemini(docs_for("gemini"), TODAY)["plans"])
        self.assertEqual(prices(plans["ai-plus"]), [(725, "JPY", "monthly")])
        self.assertEqual(prices(plans["ai-pro"]), [(2900, "JPY", "monthly")])
        self.assertEqual(prices(plans["ai-ultra-5x"]), [(14500, "JPY", "monthly")])
        self.assertEqual(prices(plans["ai-ultra-20x"]), [(32000, "JPY", "monthly")])
        self.assertEqual(plans["enterprise-business"]["prices"][0]["amount"], 21)

    def test_copilot_plans(self):
        plans = by_id(w.extract_copilot(docs_for("copilot"), TODAY)["plans"])
        self.assertEqual(prices(plans["m365-personal"]), [(2130, "JPY", "monthly"), (21300, "JPY", "annual")])
        self.assertEqual(prices(plans["m365-premium"]), [(3200, "JPY", "monthly"), (32000, "JPY", "annual")])
        biz = plans["m365-copilot-business"]["prices"]
        self.assertEqual([(p["amount"], p["billing"], p.get("regular")) for p in biz], [(2698, "annual", 3148), (3778, "monthly", None)])
        self.assertTrue(all(p["taxIncluded"] is False for p in biz))
        self.assertEqual(prices(plans["m365-copilot"]), [(4497, "JPY", "annual"), (4722, "JPY", "monthly")])

    def test_chatgpt_help_center(self):
        plans = by_id(w.extract_chatgpt(docs_for("chatgpt"), TODAY)["plans"])
        self.assertEqual(prices(plans["plus"]), [(20, "USD", "monthly")])
        self.assertEqual([plans[i]["prices"][0]["amount"] for i in ("pro-100", "pro-200", "pro-500")], [100, 200, 500])
        self.assertEqual(prices(plans["business-premium"]), [(100, "USD", "annual"), (125, "USD", "monthly")])

    def test_broken_page_raises(self):
        with self.assertRaises(w.ParseError):
            w.extract_claude({"pricing": "<html><body>Pricing coming soon</body></html>"}, TODAY)
        with self.assertRaises(w.ParseError):
            w.extract_openai_api({"pricing": "# Pricing\nnothing here"}, TODAY)


class FxTest(unittest.TestCase):
    def test_ecb_cross_rate(self):
        fx = w.parse_ecb(ECB_XML)
        self.assertEqual((fx["rate"], fx["date"]), (157.9, "2026-11-02"))

    def test_frankfurter(self):
        fx = w.parse_frankfurter('{"amount":1.0,"base":"USD","date":"2026-11-02","rates":{"JPY":157.9}}')
        self.assertEqual(fx["rate"], 157.9)

    def test_absurd_rate_is_rejected(self):
        with self.assertRaises(w.ParseError):
            w.parse_frankfurter('{"date":"2026-11-02","rates":{"JPY":1.5}}')

    def test_fallback_to_previous_rate(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "_fx.json").write_text(json.dumps(FX), "utf-8")

            def down(url):
                raise OSError("offline")

            self.assertEqual(w.get_fx(down, Path(d))["rate"], 157.9)
            self.assertEqual(w.get_fx(lambda url: ECB_XML if "ecb" in url else "", Path(d))["source"], FX["source"])
            frank = w.get_fx(lambda url: (_ for _ in ()).throw(OSError()) if "ecb" in url else '{"date":"2026-11-03","rates":{"JPY":158.4}}', Path(d))
            self.assertEqual((frank["rate"], frank["date"]), (158.4, "2026-11-03"))

    def test_yen_estimate_with_tax(self):
        est = pricing.yen_estimate(20, "USD", FX, False, jp_tax=True)
        self.assertEqual((est["exTax"], est["inclTax"], est["rate"], est["rateDate"]), (3158, 3474, 157.9, "2026-11-02"))
        self.assertTrue(est["estimate"])
        self.assertIsNone(pricing.yen_estimate(20, "USD", FX, False, jp_tax=False)["inclTax"])
        # 円の公式価格（税の記載なし）は目安を作らない。税抜きと書かれた円は 10% を足す
        self.assertIsNone(pricing.yen_estimate(2900, "JPY", FX, None, jp_tax=True))
        self.assertEqual(pricing.yen_estimate(3778, "JPY", FX, False, jp_tax=True)["inclTax"], 4156)
        self.assertIsNone(pricing.yen_estimate(20, "USD", {"rate": 0}, False, jp_tax=True))

    def test_api_yen_and_text(self):
        est = pricing.api_yen({"currency": "USD", "input": 4, "output": 20, "cacheRead": 0.2}, FX)
        self.assertEqual((est["input"], est["output"], est["cacheRead"], est["taxIncluded"]), (632, 3158, 31.6, False))
        self.assertEqual(pricing.yen_text(3474), "約3,470円")
        self.assertEqual(pricing.jpy_text({"jpy": pricing.yen_estimate(20, "USD", FX, False, True)}), "約3,470円（税込みの目安）")
        self.assertEqual(pricing.money(0.125, "USD"), "$0.125")
        self.assertEqual(pricing.money(2.5, "USD"), "$2.50")
        self.assertEqual(pricing.money(16800, "JPY"), "16,800円")


class WatchTest(unittest.TestCase):
    """書く・書かない（空や一部だけの表を書かない）・変更の記録・知らせ"""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.base = self.dir / "pricing"
        shutil.copytree(ROOT / "content" / "pricing", self.base)
        for extra in ("_changelog.json",):
            (self.base / extra).unlink(missing_ok=True)
        self.state = self.dir / "state" / "pricing_watch.json"
        self.news = self.dir / "state" / "pricing_news.json"

    def run_watch(self, only, edits=None, now=NOW, confirm=True, fetcher=None):
        return w.run(only=only, fetcher=fetcher or (lambda s: fixture_fetcher(s, edits)), http=lambda url: ECB_XML, now=now,
                     base=self.base, state_path=self.state, news_path=self.news, confirm=confirm)

    def snapshot(self, product):
        data = json.loads((self.base / f"{product}.json").read_text("utf-8"))
        return w.fingerprint(data)

    def test_unchanged_pages_write_no_news(self):
        report = self.run_watch(["claude", "claude-api", "openai-api", "gemini-api", "gemini", "copilot", "chatgpt", "perplexity"])
        self.assertTrue(all(p["status"] in ("ok", "manual") for p in report["products"].values()), report["products"])
        self.assertFalse(self.news.exists())
        self.assertFalse((self.base / "_changelog.json").exists())

    def test_broken_page_keeps_old_table(self):
        before = self.snapshot("claude")
        report = self.run_watch(["claude"], fetcher=lambda s: "<html><body>maintenance</body></html>")
        self.assertEqual(report["products"]["claude"]["status"], "error")
        self.assertEqual(self.snapshot("claude"), before)
        state = json.loads(self.state.read_text("utf-8"))
        self.assertEqual(state["products"]["claude"]["failures"], 1)

    def test_page_missing_a_plan_is_not_written(self):
        before = self.snapshot("claude")
        # Ultra の見出しが消えた（ページの形が変わった）→ 一部だけの表なので書かない
        edits = {"https://claude.com/pricing": [("<h3>Ultra</h3>", "<h3>Ultra plan</h3>")]}
        report = self.run_watch(["claude"], edits=edits)
        self.assertEqual(report["products"]["claude"]["status"], "error")
        self.assertEqual(self.snapshot("claude"), before)

    def test_validate_rejects_partial_and_empty(self):
        old = pricing.load("claude", self.base)
        partial = json.loads(json.dumps(old))
        partial["plans"] = [p for p in partial["plans"] if p["id"] != "max"]
        problems = w.validate(w.SPECS["claude"], partial, old)
        self.assertTrue(any("max" in p for p in problems))
        self.assertEqual(w.validate(w.SPECS["claude"], {"kind": "plan", "plans": []}, old), ["表が空"])
        swapped = json.loads(json.dumps(old))
        by = {p["id"]: p for p in swapped["plans"]}
        by["pro"]["prices"], by["max"]["prices"] = by["max"]["prices"], by["pro"]["prices"]
        self.assertTrue(any("並び" in p for p in w.validate(w.SPECS["claude"], swapped, old)))

    def test_assert_complete_never_writes_partial(self):
        path = self.base / "claude.json"
        before = path.read_text("utf-8")
        data = json.loads(before)
        data["plans"][1]["prices"] = []
        with self.assertRaises(ValueError):
            w.write_product(data, self.base)
        with self.assertRaises(ValueError):
            w.write_product({**data, "plans": []}, self.base)
        self.assertEqual(path.read_text("utf-8"), before)

    def test_price_change_is_logged_and_offered_as_news(self):
        edits = {"https://claude.com/pricing": [("月払いの場合は <span>$20</span>", "月払いの場合は <span>$25</span>")]}
        report = self.run_watch(["claude"], edits=edits)
        self.assertEqual(report["products"]["claude"]["status"], "changed")
        data = pricing.load("claude", self.base)
        pro = {p["id"]: p for p in data["plans"]}["pro"]
        self.assertIn(25, [p["amount"] for p in pro["prices"]])
        self.assertEqual(data["updatedAt"], "2026-11-03")
        log = json.loads((self.base / "_changelog.json").read_text("utf-8"))
        self.assertTrue(any(r["product"] == "claude" and r["new"] == 25 for r in log))
        news = json.loads(self.news.read_text("utf-8"))["pending"]
        self.assertEqual(len(news), 1)
        self.assertIn("料金が変更", news[0]["title"])
        self.assertEqual(news[0]["source_slug"], "watch-pricing-claude")
        # page_watch が他の見張りの候補と一緒に渡す
        with patch.object(page_watch, "STATE_PATH", self.news.with_name("page_watch.json")), patch.object(page_watch, "load_watches", lambda: []):
            items = page_watch.check(now=NOW)
        self.assertEqual([i["id"] for i in items], [news[0]["id"]])

    def test_flickering_page_is_not_written(self):
        before = self.snapshot("claude")
        calls = {"n": 0}

        def flicker(source):
            calls["n"] += 1
            text = fixture_fetcher(source)
            return text.replace("月払いの場合は <span>$20</span>", "月払いの場合は <span>$25</span>") if calls["n"] == 1 else text

        report = self.run_watch(["claude"], fetcher=flicker)
        self.assertEqual(report["products"]["claude"]["status"], "unstable")
        self.assertEqual(self.snapshot("claude"), before)

    def test_big_change_waits_for_the_next_run(self):
        md = fixture("claude_api_pricing.md")
        line = next(l for l in md.split("\n") if l.startswith("| Claude Opus 5.5 "))
        edits = {"https://platform.claude.com/docs/en/about-claude/pricing.md": [(line, line.replace("$4 / MTok", "$40 / MTok", 1))]}
        first = self.run_watch(["claude-api"], edits=edits)
        self.assertEqual(first["products"]["claude-api"]["status"], "hold")
        self.assertEqual(pricing.load("claude-api", self.base)["models"][0]["input"], 4)
        second = self.run_watch(["claude-api"], edits=edits)
        self.assertEqual(second["products"]["claude-api"]["status"], "changed")
        self.assertEqual(pricing.load("claude-api", self.base)["models"][0]["input"], 40)

    def test_manual_prices_survive_an_automatic_update(self):
        self.run_watch(["chatgpt"])
        plans = {p["id"]: p for p in pricing.load("chatgpt", self.base)["plans"]}
        self.assertEqual([p["amount"] for p in plans["plus"]["prices"]], [3000, 20])
        self.assertEqual(plans["plus"]["prices"][0]["src"], "manual")
        self.assertIn("go", plans)
        self.assertTrue(any("API クレジット" in x for x in plans["pro-500"]["limits"]))


class LibraryTest(unittest.TestCase):
    def test_ladder_markdown_has_source_and_date(self):
        md = pricing.ladder_markdown("claude")
        self.assertIn("| Pro |", md)
        self.assertIn("確認日：", md)
        self.assertIn("https://claude.com/pricing", md)
        self.assertIn("目安", md)

    def test_comparison_spans_companies(self):
        cmp = pricing.comparison("standard")
        products = {r["product"] for r in cmp["rows"]}
        self.assertTrue({"claude", "chatgpt", "gemini"} <= products)
        api = pricing.comparison("flagship", kind="api")
        self.assertEqual({r["product"] for r in api["rows"]}, {"claude-api", "openai-api", "gemini-api"})
        self.assertTrue(all(r["context"] != "入力>272K" for r in api["rows"]))

    def test_github_copilot_is_not_microsoft_365_copilot(self):
        """2026-11-04 の点検: GitHub Copilot の記事に Microsoft 365 の Copilot の料金表が出ていた（台帳 A32）"""
        self.assertNotIn("copilot", pricing.find_products("GitHubがJetBrainsの「Air」にCopilotを提供　クラウド実行もCopilotの契約で"))
        self.assertNotIn("copilot", pricing.find_products("GitHub Copilot、新しいプランを追加"))
        self.assertIn("copilot", pricing.find_products("Microsoft 365 Copilot、料金を改定"))
        self.assertIn("copilot", pricing.find_products("Copilot Pro が値上げ"))

    def test_find_products_and_facts(self):
        self.assertEqual(pricing.find_products("Claude Ultra と ChatGPT Pro を比べる"), ["claude", "chatgpt"])
        self.assertIn("当サイトで確認済みの料金", pricing.facts_block("Gemini 4 Argon の API 料金"))
        self.assertEqual(pricing.facts_block("きょうの天気"), "")


if __name__ == "__main__":
    unittest.main()
