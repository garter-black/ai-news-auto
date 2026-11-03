"""取り下げた種類の記事を作り直さない・取り下げ一覧と記事がぶつからないことの確認（2026-11-03）。"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import channels  # noqa: E402
import dedupe  # noqa: E402
import feeds  # noqa: E402
import indexnow  # noqa: E402


def retired() -> dict:
    path = ROOT / "content" / "retired.json"
    return json.loads(path.read_text(encoding="utf-8")).get("articles", {}) if path.exists() else {}


class RetiredSources(unittest.TestCase):
    def test_creator_channels_are_not_active(self):
        active = [c.slug for c in channels.CHANNELS if c.active and c.slug in indexnow.CREATOR_CHANNELS]
        self.assertEqual(active, [], "他人の動画のチャンネルから新しい記事を作らない")

    def test_wire_feeds_are_not_read(self):
        slugs = {f["slug"] for f in feeds.FEEDS + feeds.BROAD_FEEDS}
        self.assertFalse(slugs & indexnow.WIRE_SOURCES, "プレスリリース配信から記事を作らない")
        self.assertFalse(set(feeds.FEEDS_BY_SLUG) & indexnow.WIRE_SOURCES)

    def test_retired_articles_are_gone_and_redirects_are_live(self):
        items = retired()
        for slug, entry in items.items():
            with self.subTest(slug=slug):
                self.assertFalse((ROOT / "content" / "articles" / f"{slug}.json").exists(), "取り下げた記事の JSON が残っている")
                if entry.get("redirect"):
                    self.assertTrue((ROOT / "content" / "articles" / f"{entry['redirect']}.json").exists(), "転送先の記事が無い")

    def test_retired_source_urls_block_regeneration(self):
        items = [e for e in retired().values() if e.get("sourceUrl")]
        if not items:
            self.skipTest("出典URLのある取り下げ記事なし")
        index = dedupe.load_recent(1)
        self.assertTrue(index.has_url(items[0]["sourceUrl"]))


if __name__ == "__main__":
    unittest.main()
