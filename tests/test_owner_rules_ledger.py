"""オーナーのルールの台帳（docs/owner_rules.md）の検査。

台帳に書いた「強制」が本当にあるか（ファイル・テストが実在し、GitHub Actions で動いているか）、
毎日の見張り（scripts/owner_rules_audit.py）の番号と台帳の「点検」の印がずれていないかを確かめる。
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "owner_rules.md"
WORKFLOWS = "\n".join(p.read_text("utf-8") for p in sorted((ROOT / ".github" / "workflows").glob("*.yml")))
sys.path.insert(0, str(ROOT / "scripts"))

ROW = re.compile(r"^\|\s*([SIAWO]\d+)\s*\|(.+)\|\s*$")


def rows():
    out = []
    for line in LEDGER.read_text("utf-8").splitlines():
        m = ROW.match(line)
        if m:
            cells = [c.strip() for c in m.group(2).split("|")]
            out.append((m.group(1), cells))
    return out


def refs(cell):
    """強制の欄の `…` のうち、ファイルを指すもの（path, 名前 or None）"""
    out = []
    for token in re.findall(r"`([^`]+)`", cell):
        token = token.split(" ")[0]
        path, _, name = token.partition("::")
        if path.startswith("~") or "/" not in path or not re.search(r"\.[a-z]+$", path):
            continue
        out.append((path, name or None))
    return out


def in_workflow(path):
    name = Path(path).name
    module = "tests." + Path(path).stem
    return name in WORKFLOWS or re.search(rf"{re.escape(module)}\b", WORKFLOWS) is not None


class LedgerTest(unittest.TestCase):
    def test_rows_are_complete_and_unique(self):
        found = rows()
        self.assertGreaterEqual(len(found), 40)
        ids = [r[0] for r in found]
        self.assertEqual(len(ids), len(set(ids)), "番号が重なっている")
        for rid, cells in found:
            self.assertEqual(len(cells), 5, f"{rid}: 欄の数（ルール・日付・なぜ・強制・点検）")
            rule, date, why, enforce, _ = cells
            self.assertTrue(rule and why, rid)
            self.assertRegex(date, r"^20\d\d-\d\d-\d\d$", rid)
            self.assertTrue(enforce.startswith("会話") or refs(enforce), f"{rid}: 強制の欄にファイルが無い（会話の決まりなら「会話」と書く）")

    def test_enforcement_exists_and_runs(self):
        for rid, cells in rows():
            enforce = cells[3]
            if enforce.startswith("会話") and not refs(enforce):
                continue
            running = False
            for path, name in refs(enforce):
                file = ROOT / path
                self.assertTrue(file.exists(), f"{rid}: {path} が無い")
                if name:
                    src = file.read_text("utf-8")
                    self.assertTrue(re.search(rf"(?:def|class) {re.escape(name)}\b", src) or name in src, f"{rid}: {path} に {name} が無い")
                if path.startswith("tests/"):
                    self.assertTrue(in_workflow(path), f"{rid}: {path} が GitHub Actions で動いていない（動かないテストは強制にならない）")
                    running = True
                elif in_workflow(path):
                    running = True
            self.assertTrue(running, f"{rid}: 強制のどれも GitHub Actions で動いていない（テストか、見張りのワークフローを付ける）")

    def test_audit_marks_match_the_audit_script(self):
        import owner_rules_audit

        marked = {rid for rid, cells in rows() if "●" in cells[4]}
        self.assertEqual(marked, set(owner_rules_audit.AUDIT_RULES))

    def test_agents_md_points_here(self):
        agents = (ROOT / "AGENTS.md").read_text("utf-8")
        self.assertTrue("docs/owner_rules.md" in agents, "AGENTS.md に台帳の決まりが無い")


if __name__ == "__main__":
    unittest.main()
