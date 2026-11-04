"""すべてのテストが GitHub Actions で動いているかの見張り（2026-11-04: どこにも入っていないテストが16本あった）。

  python3 -m unittest tests.test_ci_wiring
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
WORKFLOWS = ROOT / ".github" / "workflows"


class CiWiringTest(unittest.TestCase):
    def test_all_tests_workflow_runs_every_test_file(self):
        """all_tests.yml が tests/ の Python と Node のテストを全部動かす形のまま（一部だけに絞っていない）"""
        text = (WORKFLOWS / "all_tests.yml").read_text("utf-8")
        self.assertRegex(text, r"python -m pytest[^\n]*\btests/(?:\s|$)")
        self.assertIn("node --test tests/test_*.cjs", text)
        self.assertNotRegex(text, r"--ignore|--deselect|-k\s")  # 外したテストがあれば、ここで分かる
        self.assertRegex(text, r"(?m)^\s*push:")

    def test_every_test_file_follows_the_naming_rule(self):
        """テストを書いたファイルは test_*.py / test_*.cjs の名前にする（名前が違うと全部動かす形でも拾われない）"""
        stray = []
        for path in sorted(TESTS.rglob("*.py")):
            if path.name.startswith("test_") or "fixtures" in path.parts or path.name == "__init__.py":
                continue
            src = path.read_text("utf-8", errors="ignore")
            if re.search(r"^\s*def test_|unittest\.TestCase", src, re.M):
                stray.append(str(path.relative_to(ROOT)))
        for path in sorted(TESTS.glob("*.cjs")):
            if path.name.startswith("test_") or path.name.endswith(".browser.cjs"):
                continue  # *.browser.cjs はブラウザの中で動かす部品（テストではない）
            if re.search(r"node:test|\btest\(", path.read_text("utf-8", errors="ignore")):
                stray.append(str(path.relative_to(ROOT)))
        self.assertEqual(stray, [], "テストの名前が test_ で始まっていない（CI で動かない）")


if __name__ == "__main__":
    unittest.main()
