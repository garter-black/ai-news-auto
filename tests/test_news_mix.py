"""1日の記事の配分（台帳 A41。scripts/news_mix.py・news_mix.json）の検査。通信しない・API を使わない。"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import news_mix as nm  # noqa: E402

JST = timezone(timedelta(hours=9))


class ConfigTest(unittest.TestCase):
    def test_owner_approved_shares_are_in_the_config(self):
        cats = json.loads((ROOT / "scripts" / "news_mix.json").read_text("utf-8"))["categories"]
        shares = {k: v["share"] for k, v in cats.items()}
        self.assertEqual(shares["alert"], 0.15)
        self.assertEqual(shares["money"], 0.20)
        self.assertEqual(shares["brand"], 0.25)
        self.assertEqual(shares["ai_consumer"], 0.20)
        self.assertEqual(shares["jp_corp"], 0.10)
        self.assertEqual(shares["quirky"], 0.05)
        self.assertEqual(shares["enterprise_dev"], 0.05)
        self.assertTrue(cats["enterprise_dev"].get("cap"), "企業向け・開発者向けは上限")
        self.assertAlmostEqual(sum(shares.values()), 1.0)

    def test_broken_config_falls_back(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "x.json"
            bad.write_text("{", "utf-8")
            self.assertIn("categories", nm.load_config(bad))


class ClassifyTest(unittest.TestCase):
    def test_examples(self):
        cases = [
            ({"title": "ライフネット生命の一部システムへの不正アクセスによるKDDIお客さま情報の漏えいの可能性について", "source_slug": "kddi-news"}, "alert"),
            ({"title": "LINEの古いバージョンのサポート終了について", "source_slug": "x"}, "alert"),
            ({"title": "Databricks、Claude向けOpenTelemetry連携をベータ公開", "source_slug": "databricks-blog"}, "enterprise_dev"),
            ({"title": "Microsoft、Azure IoTと連携するフィジカルAIの構成を発表", "source_slug": "microsoft-blog"}, "enterprise_dev"),
            ({"title": "ドコモ、新料金プランを発表　月額2,970円から", "source_slug": "docomo-charge"}, "money"),
            ({"title": "楽天モバイル、新型iPad miniなど4機種を11月6日に発売", "source_slug": "x"}, "brand"),
            ({"title": "ChatGPT、無料プランでも画像生成が使えるように", "source_slug": "openai-news"}, "ai_consumer"),
            ({"title": "NTT、中間決算は増収減益　通期予想を上方修正", "source_slug": "ntt-news"}, "jp_corp"),
            ({"title": "小惑星の砂から新たな有機物を発見", "source_slug": "jaxa"}, "quirky"),
        ]
        for item, want in cases:
            self.assertEqual(nm.classify(item), want, item["title"])

    def test_product_security_features_are_not_alerts(self):
        self.assertNotEqual(nm.classify({"title": "Google、新AIモデルを発表　脆弱性の発見から修正まで対応", "source_slug": "google-blog-all"}), "alert")
        self.assertNotEqual(nm.classify({"title": "Google、10月のAI発表まとめ　詐欺対策も", "source_slug": "google-blog-all"}), "alert")

    def test_ascii_brand_words_do_not_match_inside_words(self):
        self.assertNotEqual(nm.classify({"title": "Automation pipeline for online machine learning", "source_slug": "x"}), "brand")


class OrderAndCapTest(unittest.TestCase):
    def test_short_categories_first_enterprise_last_and_capped(self):
        alert = {"title": "不正アクセスによる個人情報流出の可能性について", "source_slug": "x"}
        ent = {"title": "Docker、AIコーディングエージェント向けサンドボックス", "source_slug": "docker-blog"}
        other = {"title": "A社が新しい取り組みを紹介", "source_slug": "x"}
        counts: dict[str, int] = {}
        ranked = sorted([ent, other, alert], key=lambda i: nm.order_key(i, counts))
        self.assertEqual(ranked[0], alert)
        self.assertEqual(ranked[-1], ent)
        cap = nm.cap_of("enterprise_dev")
        self.assertEqual(cap, 2)  # 30本 × 5% = 1.5 → 切り上げ
        self.assertFalse(nm.over_cap(ent, {"enterprise_dev": cap - 1}))
        self.assertTrue(nm.over_cap(ent, {"enterprise_dev": cap}))
        self.assertFalse(nm.over_cap(alert, {"alert": 99}), "上限は企業向けだけ")

    def test_today_counts(self):
        now = datetime(2026, 11, 4, 20, tzinfo=JST)
        state = {"processed_web": {
            "a": {"at": now.astimezone(timezone.utc).isoformat(), "slug": "a", "mix": "alert"},
            "b": {"at": now.astimezone(timezone.utc).isoformat(), "skipped": True, "mix": "alert"},
            "c": {"at": (now - timedelta(days=1)).astimezone(timezone.utc).isoformat(), "slug": "c", "mix": "money"},
        }}
        self.assertEqual(nm.today_counts(state, now), {"alert": 1})

    def test_pipeline_uses_the_mix(self):
        src = (ROOT / "scripts" / "web_articles.py").read_text("utf-8")
        for needle in ("news_mix.order_key(", "news_mix.over_cap(", "mix=item.get(\"_mix\")"):
            self.assertIn(needle, src)


if __name__ == "__main__":
    unittest.main()
