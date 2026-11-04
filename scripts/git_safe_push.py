#!/usr/bin/env python3
"""ワークフローのコミットを、ぶつかっても落とさずに main へ push する（2026-11-03 auto_run の記事が push できずに消えた件）。

これまでの `git pull --rebase` は、別のジョブ（見出しの書き直し・画像の見回り・編集部の確認など）が同じ記事 JSON の
別の所を直していても行単位でぶつかり、解決されないまま3回失敗して、その回の新しい記事ごと消えていた。

このスクリプトは、いまの HEAD（この回のコミット。複数でもよい）を次のように最新の main に載せ直す。
  1. origin/main を取り直す。main が進んでいなければそのまま push
  2. 進んでいれば、この回の変更をファイルごとに最新の main の上へ当て直す（git の rebase は使わない＝未解決のまま残らない）
     - この回が新しく作ったファイル（記事）: そのまま置く（決して捨てない）
     - この回だけが変えたファイル: この回の中身にする
     - 両方が変えた JSON: 項目ごとの3方向マージ。この回が変えた項目だけこの回の値、ほかの人が直した項目はそのまま残す
       （記事以外の配列は「足した分・消した分」で合わせる。使用回数の記録は数を足し合わせる）
     - 両方が変えた JSON 以外: git merge-file で合わせ、合わなければこの回の中身
     - この回が消したファイル（引退した記事）: 消す / main で消されたファイル: 消えたまま
  3. --regen のコマンド（索引の作り直しなど、同じ入力から同じ結果が出る後処理）を最新の中身で動かす
  4. 1つのコミットにまとめて push。ほかの push と入れ違ったら、少し待って（ゆらぎ付き）1 からやり直す
失敗しても、作業ツリーはこの回のコミットの状態に戻し、コミットしていなかった変更も元に戻す（未解決のファイルを残さない）。

  python3 scripts/git_safe_push.py                       # HEAD を origin/main へ
  python3 scripts/git_safe_push.py --regen "python scripts/radar.py coverage"
  python3 scripts/git_safe_push.py --wait-idle auto_run.yml   # 記事づくりの回が走っている間は push を待つ（最大 --wait-max 秒）
  python3 scripts/git_safe_push.py --strict              # 同じ項目を両方が違う値に変えていたら、上書きせずに失敗する
終了コード: 0 = push した（または push するものが無い） / 1 = push できなかった
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# 数を足し合わせる記録（両方が同じ数を増やしていたら、増えた分を足す）
COUNTER_PATTERNS = ("content/llm_usage*.json", "state/claude_api_usage*.json", "state/llm_usage*.json")
# 記事: 配列（タグ・要点など）は中身の順番に意味があるので混ぜず、この回が変えていればこの回の値にする
ARTICLE_PATTERNS = ("content/articles/*.json",)


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - デバッグ用
        return "MISSING"


MISSING = _Missing()


class StrictConflict(Exception):
    pass


def log(message: str) -> None:
    print(f"[safe-push] {message}", flush=True)


# ---------------------------------------------------------------- JSON の3方向マージ

def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _key(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _merge_list(base: list, ours: list, theirs: list) -> list:
    base_keys = {_key(x) for x in base}
    our_keys = {_key(x) for x in ours}
    removed = base_keys - our_keys
    result = [x for x in theirs if _key(x) not in removed]
    seen = {_key(x) for x in result}
    for item in ours:
        k = _key(item)
        if k not in base_keys and k not in seen:
            result.append(item)
            seen.add(k)
    return result


def merge_values(base, ours, theirs, *, counters: bool = False, lists: bool = True,
                 path: tuple = (), conflicts: list | None = None):
    """JSON の値を3方向でマージする。MISSING は「その項目が無い」。この回（ours）が変えた所だけ ours を採る"""
    if conflicts is None:
        conflicts = []
    if ours == theirs or (ours is MISSING and theirs is MISSING):
        return ours
    if ours == base or (ours is MISSING and base is MISSING):
        return theirs
    if theirs == base or (theirs is MISSING and base is MISSING):
        return ours
    # 両方が違う値に変えた
    if isinstance(ours, dict) and isinstance(theirs, dict) and (isinstance(base, dict) or base is MISSING):
        b = base if isinstance(base, dict) else {}
        keys = list(ours) + [k for k in theirs if k not in ours] + [k for k in b if k not in ours and k not in theirs]
        result = {}
        for k in keys:
            value = merge_values(b.get(k, MISSING), ours.get(k, MISSING), theirs.get(k, MISSING),
                                 counters=counters, lists=lists, path=path + (k,), conflicts=conflicts)
            if value is not MISSING:
                result[k] = value
        return result
    if counters and _is_num(ours) and _is_num(theirs) and (_is_num(base) or base is MISSING):
        b = base if _is_num(base) else 0
        total = theirs + (ours - b)
        if total >= 0:
            return round(total, 6) if isinstance(total, float) else total
    if lists and isinstance(ours, list) and isinstance(theirs, list) and (isinstance(base, list) or base is MISSING):
        return _merge_list(base if isinstance(base, list) else [], ours, theirs)
    conflicts.append("/".join(str(p) for p in path) or "(全体)")
    return ours


def _no_dup_pairs(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"同じ項目が2つあります: {k}")
        seen[k] = v
    return seen


def parse_json(data: bytes | None):
    if data is None:
        return MISSING
    return json.loads(data.decode("utf-8"), object_pairs_hook=_no_dup_pairs)


def dump_like(value, sample: bytes | None) -> bytes:
    """sample（元のファイル）と同じ書き方（字下げ・日本語のまま・末尾の改行）で書く"""
    text = (sample or b"").decode("utf-8", "replace")
    indent = None
    match = re.search(r"\n( +)\S", text[:2000])
    if match:
        indent = len(match.group(1))
    elif "\n\t" in text[:2000]:
        indent = "\t"
    elif not text:
        indent = 2
    separators = None
    if indent is None and re.search(r'":\S', text) and '": ' not in text:
        separators = (",", ":")
    ensure_ascii = bool(text) and all(ord(c) < 128 for c in text) and "\\u" in text
    out = json.dumps(value, ensure_ascii=ensure_ascii, indent=indent, separators=separators)
    if not text or text.endswith("\n"):
        out += "\n"
    return out.encode("utf-8")


def _matches(path: str, patterns) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def merge_file(path: str, base: bytes | None, ours: bytes | None, theirs: bytes | None, *, strict: bool = False):
    """1つのファイルを3方向でマージした中身（None = 消す）と、両方が同じ所を変えた項目の一覧を返す"""
    if ours == theirs:
        return theirs, []
    if theirs == base:
        return ours, []
    if ours == base:
        return theirs, []
    # 両方が変えた
    if ours is None:  # この回が消した（引退した記事など）。消すのを優先する
        if strict:
            raise StrictConflict(f"{path}: この回は消したが、main でも変わっている")
        log(f"{path}: この回で消したファイル（main でも直されていたが消すのを優先）")
        return None, ["(削除)"]
    if theirs is None:  # main で消された（引退）。消えたままにする
        log(f"{path}: main で消されたので、この回の変更は当てません")
        return None, []
    if path.endswith(".json"):
        try:
            b, o, t = parse_json(base), parse_json(ours), parse_json(theirs)
        except Exception as error:  # noqa: BLE001 - 読めない JSON は文字のマージに回す
            log(f"{path}: JSON として読めないので文字でマージします（{str(error)[:80]}）")
        else:
            conflicts: list = []
            merged = merge_values(b, o, t, counters=_matches(path, COUNTER_PATTERNS),
                                  lists=not _matches(path, ARTICLE_PATTERNS), conflicts=conflicts)
            if conflicts and strict:
                raise StrictConflict(f"{path}: 両方が同じ項目を変えています: {', '.join(conflicts[:5])}")
            if conflicts:
                log(f"{path}: 両方が変えた項目はこの回の値にしました: {', '.join(conflicts[:8])}")
            return dump_like(merged, ours), conflicts
    merged = _merge_text(base or b"", ours, theirs)
    if merged is not None:
        return merged, []
    if strict:
        raise StrictConflict(f"{path}: 文字のマージがぶつかりました")
    log(f"{path}: 文字のマージがぶつかったので、この回の中身にしました")
    return ours, ["(全体)"]


def _merge_text(base: bytes, ours: bytes, theirs: bytes) -> bytes | None:
    if b"\0" in base + ours + theirs:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, data in (("ours", ours), ("base", base), ("theirs", theirs)):
            p = Path(tmp) / name
            p.write_bytes(data)
            paths.append(str(p))
        proc = subprocess.run(["git", "merge-file", "-p", *paths], capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


# ---------------------------------------------------------------- git

def git(*args: str, check: bool = True, input: bytes | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], capture_output=True, input=input)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} が失敗: {proc.stderr.decode('utf-8', 'replace').strip()[:400]}")
    return proc


def git_out(*args: str) -> str:
    return git(*args).stdout.decode("utf-8").strip()


def blob(rev: str, path: str) -> bytes | None:
    proc = git("cat-file", "blob", f"{rev}:{path}", check=False)
    return proc.stdout if proc.returncode == 0 else None


def repo_root() -> Path:
    return Path(git_out("rev-parse", "--show-toplevel"))


def abort_leftovers() -> None:
    gitdir = Path(git_out("rev-parse", "--git-dir"))
    if (gitdir / "rebase-merge").exists() or (gitdir / "rebase-apply").exists():
        git("rebase", "--abort", check=False)
    if (gitdir / "MERGE_HEAD").exists():
        git("merge", "--abort", check=False)
    if (gitdir / "CHERRY_PICK_HEAD").exists():
        git("cherry-pick", "--abort", check=False)


def changed_paths(base: str, head: str) -> list[tuple[str, str]]:
    raw = git("diff", "--no-renames", "--name-status", "-z", base, head).stdout.decode("utf-8")
    parts = [p for p in raw.split("\0") if p]
    return [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]


def write_path(root: Path, path: str, data: bytes | None) -> None:
    target = root / path
    if data is None:
        if target.exists() or target.is_symlink():
            target.unlink()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def commit_message(base: str, ours: str) -> str:
    raw = git("log", "--reverse", "--format=%B%x00", f"{base}..{ours}").stdout.decode("utf-8")
    messages = [m.strip() for m in raw.split("\0") if m.strip()]
    if not messages:
        return "chore: 自動の更新"
    if len(messages) == 1:
        return messages[0]
    title = messages[-1].splitlines()[0]
    return title + "\n\n" + "\n".join(f"- {m.splitlines()[0]}" for m in messages)


def rebuild(ours: str, theirs: str, root: Path, *, strict: bool, regen: list[str]) -> str | None:
    """この回の変更（base..ours）を theirs の上に当て直したコミットを作る。当てる変更が無ければ None"""
    base = git_out("merge-base", ours, theirs)
    changes = changed_paths(base, ours)
    git("reset", "-q", "--hard", theirs)
    touched = []
    kept_new = 0
    for status, path in changes:
        b, o, t = blob(base, path), blob(ours, path), blob(theirs, path)
        result, _ = merge_file(path, b, o, t, strict=strict)
        if status == "A" and o is not None and result is not None:
            kept_new += 1
        if result == t:
            continue
        write_path(root, path, result)
        touched.append(path)
    for cmd in regen:
        proc = subprocess.run(cmd, shell=True, cwd=root)
        if proc.returncode != 0:
            log(f"::warning::後処理が失敗しました（続けます）: {cmd}")
    if touched:
        git("add", "-A", "--", *touched)
    git("add", "-u")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return None
    git("commit", "-q", "--no-verify", "-F", "-", input=commit_message(base, ours).encode("utf-8"))
    log(f"最新の main（{theirs[:8]}）の上に載せ直しました: {len(touched)} ファイル（新しいファイル {kept_new}）")
    return git_out("rev-parse", "HEAD")


def wait_idle(workflow: str, max_seconds: int) -> None:
    """記事づくりの回（workflow）が走っている間は待つ。gh が無い・読めないときは待たない"""
    deadline = time.time() + max_seconds
    own = os.environ.get("GITHUB_RUN_ID", "")
    while True:
        try:
            proc = subprocess.run(["gh", "run", "list", "--workflow", workflow, "--status", "in_progress",
                                   "--json", "databaseId", "--jq", ".[].databaseId"],
                                  capture_output=True, text=True, timeout=60)
        except Exception as error:  # noqa: BLE001
            log(f"{workflow} の状態を読めませんでした（待たずに進みます）: {error}")
            return
        if proc.returncode != 0:
            log(f"{workflow} の状態を読めませんでした（待たずに進みます）: {proc.stderr.strip()[:200]}")
            return
        running = [r for r in proc.stdout.split() if r and r != own]
        if not running:
            return
        if time.time() >= deadline:
            log(f"{workflow} が {max_seconds} 秒たっても終わらないので、待たずに進みます（ぶつかってもマージします）")
            return
        log(f"{workflow} が実行中（{', '.join(running)}）なので、終わるまで push を待ちます")
        time.sleep(20)


def capture_dirty(root: Path) -> dict[str, bytes | None]:
    """コミットしていない変更（追跡しているファイル）を覚えておく"""
    raw = git("diff", "--name-only", "-z", "HEAD").stdout.decode("utf-8")
    dirty = {}
    for path in [p for p in raw.split("\0") if p]:
        target = root / path
        dirty[path] = target.read_bytes() if target.is_file() else None
    return dirty


def restore_dirty(root: Path, dirty: dict[str, bytes | None], ours: str) -> bool:
    """覚えておいた変更を、いまの HEAD の上に戻す（HEAD 側も変わっていれば3方向でマージ）"""
    ok = True
    for path, mine in dirty.items():
        try:
            result, _ = merge_file(path, blob(ours, path), mine, blob("HEAD", path))
            write_path(root, path, result)
        except Exception as error:  # noqa: BLE001
            ok = False
            log(f"::warning::コミットしていなかった変更を戻せませんでした: {path}（{error}）")
    return ok


def push(remote: str, branch: str) -> bool:
    proc = git("push", remote, f"HEAD:{branch}", check=False)
    if proc.returncode != 0:
        log(f"push が通りませんでした: {proc.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    return proc.returncode == 0


def safe_push(*, remote: str = "origin", branch: str = "main", attempts: int = 6, strict: bool = False,
              regen: list[str] | None = None, sleep=time.sleep, gate=None) -> int:
    root = repo_root()
    abort_leftovers()
    regen = regen or []
    ours = git_out("rev-parse", "HEAD")
    dirty = capture_dirty(root)
    stash = ""
    if dirty:
        # 念のための控え（成功したら消す）。中身は dirty から戻す
        git("stash", "push", "-q", "-m", "git_safe_push backup")
        stash = git_out("rev-parse", "stash@{0}")
    pushed = False
    try:
        for attempt in range(1, attempts + 1):
            fetched = git("fetch", "-q", remote, branch, check=False)
            if fetched.returncode != 0:
                log(f"fetch に失敗しました（{attempt}/{attempts}）: {fetched.stderr.decode('utf-8', 'replace').strip()[:200]}")
                sleep(random.uniform(2, 6) * attempt)
                continue
            theirs = git_out("rev-parse", "FETCH_HEAD")
            git("reset", "-q", "--hard", ours)
            if git("merge-base", "--is-ancestor", theirs, ours, check=False).returncode == 0:
                if theirs == ours or git_out("rev-list", "--count", f"{theirs}..{ours}") == "0":
                    log("push するコミットはありません")
                    pushed = True
                    break
            else:
                try:
                    head = rebuild(ours, theirs, root, strict=strict, regen=regen)
                except StrictConflict as error:
                    log(f"::warning::{error}（上書きせずにやめます）")
                    return 1
                if head is None:
                    log("最新の main にこの回の変更はもう入っています（push するものはありません）")
                    pushed = True
                    break
            if gate is not None and not gate(root):
                log("::error::公開と同じテストに通らないので push しません（壊れたデータを main に入れない。scripts/deploy_tests.py）")
                return 1
            if push(remote, branch):
                log(f"push しました（{attempt} 回目）: {git_out('rev-parse', '--short', 'HEAD')}")
                pushed = True
                break
            wait = random.uniform(3, 8) * attempt
            log(f"ほかの push と入れ違いました。{wait:.0f} 秒待ってやり直します（{attempt}/{attempts}）")
            sleep(wait)
        if not pushed:
            log(f"::error::{attempts} 回やっても push できませんでした")
        return 0 if pushed else 1
    except Exception as error:  # noqa: BLE001
        log(f"::error::push の途中で失敗しました: {error}")
        return 1
    finally:
        abort_leftovers()
        if not pushed:
            git("reset", "-q", "--hard", ours, check=False)  # この回のコミットの状態に戻す（未解決のファイルを残さない）
        if dirty:
            restored = restore_dirty(root, dirty, ours)
            if restored and stash:
                listing = git_out("stash", "list", "--format=%H")
                if stash in listing.split():
                    index = listing.split().index(stash)
                    git("stash", "drop", "-q", f"stash@{{{index}}}", check=False)


def deploy_gate(root: Path) -> bool:
    """push の直前に、公開（deploy_cloudflare.yml）と同じテストを載せ直した中身で動かす（2026-11-04 オーナー「直すだけでなく予防して」）。
    テストの一覧は scripts/deploy_tests.py がワークフローのファイルから毎回読む（一覧は1か所）。SAFE_PUSH_NO_GATE=1 で止められる"""
    if os.environ.get("SAFE_PUSH_NO_GATE") == "1":
        return True
    proc = subprocess.run([sys.executable, str(root / "scripts" / "deploy_tests.py")], cwd=root, capture_output=True, text=True)
    if proc.returncode != 0:
        log((proc.stdout + proc.stderr)[-3000:])
    return proc.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--strict", action="store_true", help="同じ項目を両方が変えていたら上書きせずに失敗する")
    parser.add_argument("--regen", action="append", default=[], help="載せ直したあとに動かす後処理（何度でも指定できる）")
    parser.add_argument("--wait-idle", default="", help="このワークフローが実行中なら push を待つ（例: auto_run.yml）")
    parser.add_argument("--wait-max", type=int, default=1200)
    args = parser.parse_args(argv)
    if args.wait_idle:
        wait_idle(args.wait_idle, args.wait_max)
    return safe_push(remote=args.remote, branch=args.branch, attempts=args.attempts, strict=args.strict, regen=args.regen,
                     gate=deploy_gate)


if __name__ == "__main__":
    sys.exit(main())
