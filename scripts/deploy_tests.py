#!/usr/bin/env python3
"""公開（.github/workflows/deploy_cloudflare.yml）と同じテストを、ここで全部動かす（記事を直すジョブが push する前に使う）。

テストの行はワークフローのファイルから毎回読む（ほかの作業がテストを足しても、ここを直さずに同じものが動く）:
  node --test … / node scripts/test_*.mjs / python3 -m unittest …
1つでも落ちたら終了コード 1（push しない）。2026-11-03: 書き直しの push が公開のテストを壊し、30分公開が止まった件の再発防止。

  python3 scripts/deploy_tests.py          # 全部動かす
  python3 scripts/deploy_tests.py --list   # 動かすコマンドを表示するだけ
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "deploy_cloudflare.yml"
TEST_LINE = re.compile(r"^\s*(?:-\s*)?(?:run:\s*)?((?:node --test|node scripts/test_\w+\.mjs|python3? -m unittest)\b.*)$")


def commands() -> list[str]:
    out = []
    for line in WORKFLOW.read_text("utf-8").splitlines():
        m = TEST_LINE.match(line)
        if m and m.group(1) not in out:
            out.append(m.group(1).strip())
    return out


def main() -> int:
    cmds = commands()
    if "--list" in sys.argv:
        print("\n".join(cmds))
        return 0
    if not (ROOT / "node_modules").exists():
        # npm ci をしていないジョブ（記事づくり・SNS など）から git_safe_push が呼ぶとき。Python のテストだけ動かす（データの門はこちら）
        print("[deploy-tests] node_modules が無いので node のテストは飛ばします（公開の回では全部動きます）", flush=True)
        cmds = [c for c in cmds if not c.startswith("node")]
    failed = []
    for cmd in cmds:
        print(f"[deploy-tests] {cmd}", flush=True)
        if subprocess.run(cmd, shell=True, cwd=ROOT).returncode != 0:
            failed.append(cmd)
    if failed:
        print(f"[deploy-tests] 失敗 {len(failed)} 件: " + " / ".join(c[:80] for c in failed), flush=True)
        return 1
    print(f"[deploy-tests] すべて通りました（{len(cmds)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
