"""オーナーのルール（docs/owner_rules.md）のうち、ほかのテストが無かったものを守らせる小さなテスト。"""
import importlib.util
import random
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


class GapsTest(unittest.TestCase):
    def test_quake_never_uses_llm(self):
        """[A15] 地震・津波の記事は AI に書かせない（気象庁のデータを決まった書式に入れるだけ）"""
        src = (ROOT / "scripts" / "quake.py").read_text("utf-8")
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
        self.assertIsNone(re.search(r"^\s*(?:import|from)\s+(?:llm|anthropic|google\.genai|openai)\b", code, re.M))
        self.assertNotIn("generate_json(", code)
        self.assertNotIn("generate_text(", code)

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow が無い（CI では requirements.txt で入る）")
    def test_text_only_figure_is_rejected(self):
        """[I3] 文字だけの図（白地に文字・ロゴ）を記事の写真にしない。写真らしい画像は通す"""
        from PIL import Image, ImageDraw

        import images

        figure = Image.new("RGB", (1200, 630), "white")
        draw = ImageDraw.Draw(figure)
        for i in range(8):
            draw.rectangle((100, 80 + i * 60, 1100 - i * 70, 110 + i * 60), fill=(20, 20, 20))
        self.assertTrue(images.image_problem(figure, 800))

        rnd = random.Random(1)
        photo = Image.new("RGB", (240, 126))
        photo.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(240 * 126)])
        self.assertIsNone(images.image_problem(photo.resize((1200, 630)), 800))


if __name__ == "__main__":
    unittest.main()
