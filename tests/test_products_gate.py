"""図鑑（/products）の原稿の門番（2026-11-03 オーナーの指摘「内容がすごく薄い」・台帳 W13）。

scripts/products.py は、答えになっていない文（記載なし・元記事には・未定など）を原稿に入れない。
図鑑と公式データのつなぎ（content/product_sources.json）は、実在する製品・料金の行・日本での提供の表だけを指す。
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import products  # noqa: E402


class NonAnswerTest(unittest.TestCase):
    def test_non_answers_are_rejected(self):
        for text in (
            "記載なし",
            "具体的な価格の言及なし",
            "発表の時点では、日本国内での直接的な利用手順について元記事に記載はありません。",
            "一般向けの提供時期は未定です。",
            "発表の時点では、日本での提供について案内はありません。",
        ):
            self.assertTrue(products.non_answer(text), text)
        self.assertFalse(products.non_answer("ChatGPTとCodexを合わせた残りの量は、SettingsのUsageで確認できます。"))

    def test_check_draft_drops_non_answer_items(self):
        by_slug = {"a": {"slug": "a", "title": "Codexの利用枠", "body": "利用枠はSettingsのUsageで確認できます。", "highlights": []}}
        draft = {
            "overview": {"text": "Codexは、OpenAIの開発ツールです。料金は元記事に記載がありません。", "sources": ["a"]},
            "facts": [{"label": "日本での提供", "value": "記載なし", "sources": ["a"]}],
            "capabilities": [{"text": "利用枠はSettingsのUsageで確認できます。", "sources": ["a"]}],
            "pricing": [{"text": "料金の言及はありません。", "sources": ["a"]}],
            "japan": [], "howto": [], "differences": [], "faq": [], "timeline": [],
        }
        checked, problems = products.check_draft(draft, by_slug)
        self.assertEqual(checked["overview"]["text"], "Codexは、OpenAIの開発ツールです。")
        self.assertEqual(checked.get("facts", []), [])
        self.assertEqual(checked.get("pricing", []), [])
        self.assertEqual(len(checked["capabilities"]), 1)
        self.assertTrue(any("答えになっていない" in p for p in problems))


class EditorialPagesTest(unittest.TestCase):
    def test_editorial_product_pages_are_not_rewritten(self):
        # ChatGPT・Claude・Gemini の図鑑ページは編集部がまとめた（2026-11-03 オーナー承認）。products.py は LLM で書き直さない
        src = (ROOT / "scripts" / "products.py").read_text(encoding="utf-8")
        self.assertIn('current.get("refreshMode") == "editorial"', src)
        for slug in ("chatgpt", "claude", "gemini"):
            data = json.loads((ROOT / "content" / "products" / f"{slug}.json").read_text(encoding="utf-8"))
            self.assertEqual(data.get("refreshMode"), "editorial", slug)
            self.assertGreaterEqual(len(data["timeline"]), 2, slug)
            for item in [data["overview"], *data["capabilities"], *data["differences"]]:
                self.assertFalse(products.non_answer(item["text"]), item["text"])
                self.assertTrue(item["sources"], f"{slug}: 出典の記事が無い →「{item['text'][:30]}」")
                for s in item["sources"]:
                    self.assertTrue((ROOT / "content" / "articles" / f"{s}.json").exists(), s)


class SourcesTest(unittest.TestCase):
    def test_product_sources_point_to_real_rows(self):
        sources = json.loads((ROOT / "content" / "product_sources.json").read_text(encoding="utf-8"))["items"]
        japan = {i["id"]: i for i in json.loads((ROOT / "content" / "japan_availability.json").read_text(encoding="utf-8"))["items"]}
        self.assertGreaterEqual(len(sources), 3)
        for slug, src in sources.items():
            self.assertTrue((ROOT / "content" / "products" / f"{slug}.json").exists(), slug)
            link = src["pricing"]
            pricing = json.loads((ROOT / "content" / "pricing" / f"{link['product']}.json").read_text(encoding="utf-8"))
            plans = {p["id"] for p in pricing.get("plans", [])}
            models = {m["id"] for m in pricing.get("models", []) if m.get("status") != "not_listed"}
            for pid in link.get("plans", []):
                self.assertIn(pid, plans, f"{slug}: プラン {pid} が {link['product']} に無い")
            for mid in link.get("models", []):
                self.assertIn(mid, models, f"{slug}: モデル {mid} が {link['product']} の料金ページに無い")
            self.assertTrue(src.get("why"), f"{slug}: 根拠（why）が無い")
            if src.get("japan"):
                self.assertIn(src["japan"], japan, slug)
                self.assertNotEqual(japan[src["japan"]]["japan"], "unknown", slug)
            if src.get("image"):
                # 公式プレスキットの画像は自前の KV（/img/brand-<名前>）に置き、出典を書く（記事の写真・素材は使わない）
                self.assertRegex(src["image"]["src"], r"^https://ai-tech-express\.jp/img/brand-[a-z0-9-]+\.jpg$", slug)
                self.assertTrue(src["image"]["credit"] and src["image"]["sourceUrl"].startswith("https://"), slug)

    def test_business_plans_really_cover_codex_and_work(self):
        # Codex・ChatGPT Work に Business の行をつなぐ根拠は、公式の料金ヘルプの文言（limits）にその名前があること
        pricing = json.loads((ROOT / "content" / "pricing" / "chatgpt.json").read_text(encoding="utf-8"))
        limits = {p["id"]: " ".join(p.get("limits", [])) for p in pricing["plans"]}
        sources = json.loads((ROOT / "content" / "product_sources.json").read_text(encoding="utf-8"))["items"]
        for slug, word in (("codex", "Codex"), ("chatgpt-work", "ChatGPT Work")):
            for pid in sources[slug]["pricing"]["plans"]:
                self.assertIn(word, limits[pid], f"{slug}: {pid} の公式の文言に {word} が無い")


if __name__ == "__main__":
    unittest.main()
