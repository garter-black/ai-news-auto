"""消えた製品ページを 404 にせず、親の製品ページ（無ければ図鑑の一覧）へ 301 で転送する（台帳 W30。2026-11-04）。

scripts/retire_articles.py は、記事を取り下げて関係する記事・出典を失った製品ページを消すとき、content/retired.json の
products に転送先（親の製品の slug）を残す。cloudflare/worker.ts はそれを読んで /products/<slug> を 301 にする。
2026-11-04: Zendesk の記事を取り下げたら /products/chatgpt-enterprise が 404 になった件の再発防止。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import retire_articles  # noqa: E402


class ParentProductTest(unittest.TestCase):
    def test_longest_live_prefix_is_the_parent(self):
        live = {"chatgpt", "gpt-6", "gpt-6-1-terra", "gemini-4", "gemini-4-argon", "claude"}
        self.assertEqual(retire_articles.parent_product("chatgpt-enterprise", live), "chatgpt")
        self.assertEqual(retire_articles.parent_product("gpt-6-sol", live), "gpt-6")
        self.assertEqual(retire_articles.parent_product("gemini-4-argon-pro", live), "gemini-4-argon")
        self.assertEqual(retire_articles.parent_product("claude-opus-5-5", live), "claude")

    def test_no_parent_means_products_index(self):
        live = {"chatgpt", "gemini"}
        self.assertEqual(retire_articles.parent_product("jev", live), "")
        self.assertEqual(retire_articles.parent_product("chatgptx", live), "")  # 「slug + -」で始まるときだけ


class ApplyRecordsRedirectTest(unittest.TestCase):
    def test_removed_product_gets_parent_redirect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            arts, prods = root / "content" / "articles", root / "content" / "products"
            arts.mkdir(parents=True)
            prods.mkdir(parents=True)
            (arts / "old-a.json").write_text(json.dumps({"slug": "old-a", "title": "古い記事"}), "utf-8")
            (arts / "live-b.json").write_text(json.dumps({"slug": "live-b", "title": "残る記事"}), "utf-8")
            (prods / "foo.json").write_text(json.dumps({"slug": "foo", "name": "Foo", "articles": ["live-b"],
                                                        "overview": {"text": "Foo", "sources": ["live-b"]}}), "utf-8")
            (prods / "foo-pro.json").write_text(json.dumps({"slug": "foo-pro", "name": "Foo Pro", "articles": ["old-a"],
                                                            "overview": {"text": "Foo Pro", "sources": ["old-a"]}}), "utf-8")
            retired = root / "content" / "retired.json"
            with patch.object(retire_articles, "ROOT", root), patch.object(retire_articles, "ARTICLES", arts), \
                    patch.object(retire_articles, "RETIRED", retired), patch.object(retire_articles, "DATA_FILES", []):
                stats = retire_articles.apply({"old-a": {"reason": "thin", "title": "古い記事"}}, datetime.now(timezone.utc))
            self.assertEqual(stats["products_removed"], ["foo-pro"])
            self.assertFalse((prods / "foo-pro.json").exists())
            store = json.loads(retired.read_text("utf-8"))
            self.assertEqual(store["products"]["foo-pro"]["redirect"], "foo")
            self.assertEqual(store["products"]["foo-pro"]["name"], "Foo Pro")
            self.assertIn("old-a", store["articles"])

    def test_recreated_product_drops_the_redirect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "content" / "products").mkdir(parents=True)
            (root / "content" / "products" / "bar.json").write_text("{}", "utf-8")
            store = {"articles": {}, "products": {"bar": {"reason": "no-sources", "redirect": "x"}}}
            with patch.object(retire_articles, "ROOT", root):
                retire_articles.record_removed_products(store, [], "2026-11-04T00:00:00+09:00")
            self.assertNotIn("bar", store["products"])


class RegeneratedProductTest(unittest.TestCase):
    def test_forget_products_drops_record_when_page_is_made_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            retired = Path(tmp) / "retired.json"
            retired.write_text(json.dumps({"articles": {}, "products": {"foo-pro": {"reason": "no-sources", "redirect": "foo"},
                                                                         "bar": {"reason": "no-sources"}}}), "utf-8")
            with patch.object(retire_articles, "RETIRED", retired):
                self.assertEqual(retire_articles.forget_products(["foo-pro", "zzz"]), ["foo-pro"])
            self.assertEqual(list(json.loads(retired.read_text("utf-8"))["products"]), ["bar"])

    def test_product_generator_calls_forget_on_save_and_sync(self):
        src = (ROOT / "scripts" / "products.py").read_text("utf-8")
        self.assertGreaterEqual(src.count("retire_articles.forget_products("), 2)


class LiveDataTest(unittest.TestCase):
    def test_retired_products_point_at_live_pages(self):
        store = json.loads((ROOT / "content" / "retired.json").read_text("utf-8"))
        live = {p.stem for p in (ROOT / "content" / "products").glob("*.json")}
        for slug, entry in (store.get("products") or {}).items():
            self.assertNotIn(slug, live, f"{slug} は製品ページが残っているのに転送の記録がある")
            if entry.get("redirect"):
                self.assertIn(entry["redirect"], live, f"{slug} の転送先 {entry['redirect']} の製品ページが無い")

    def test_worker_redirects_retired_products(self):
        worker = (ROOT / "cloudflare" / "worker.ts").read_text("utf-8")
        self.assertIn("retiredProductResponse", worker)
        self.assertIn("data.products", worker)
        self.assertIn("status: 301", worker)


if __name__ == "__main__":
    unittest.main()
