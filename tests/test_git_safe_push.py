"""scripts/git_safe_push.py のテスト（2026-11-03 auto_run が記事 JSON のぶつかりで push できず、新しい記事ごと消えた件）。

- 両方が同じ記事の別の項目を直した → 両方残る / 同じ項目 → この回の値
- この回が作った記事は必ず残る / この回が消した（引退した）記事は消える / main で消された記事は消えたまま
- 未解決のファイルを残さない・コミットしていなかった変更も戻す
- ワークフローに `git pull --rebase` の再試行が戻っていない
外部への通信なし（一時フォルダの中の git だけ）。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import git_safe_push as gsp  # noqa: E402

MISSING = gsp.MISSING


def dump(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class MergeValuesTest(unittest.TestCase):
    def test_conflicting_field_edits_keep_both_sides(self):
        base = {"slug": "a", "title": "旧", "sectionTitles": {"概要": "概要"}, "body": "本文"}
        ours = {**base, "sectionTitles": {"概要": "何が出たか"}}          # この回は見出しを直した
        theirs = {**base, "title": "新しい題名"}                         # 別のジョブは題名を直した
        merged = gsp.merge_values(base, ours, theirs, lists=False)
        self.assertEqual(merged["title"], "新しい題名")
        self.assertEqual(merged["sectionTitles"], {"概要": "何が出たか"})

    def test_same_field_ours_wins_and_is_reported(self):
        conflicts = []
        merged = gsp.merge_values({"title": "旧"}, {"title": "この回"}, {"title": "別のジョブ"}, conflicts=conflicts)
        self.assertEqual(merged["title"], "この回")
        self.assertEqual(conflicts, ["title"])

    def test_nested_dict_merges_per_key(self):
        base = {"sectionTitles": {"a": "1", "b": "2"}}
        ours = {"sectionTitles": {"a": "1x", "b": "2"}}
        theirs = {"sectionTitles": {"a": "1", "b": "2y", "c": "3"}}
        self.assertEqual(gsp.merge_values(base, ours, theirs)["sectionTitles"], {"a": "1x", "b": "2y", "c": "3"})

    def test_key_removed_by_ours_and_added_by_theirs(self):
        base = {"old": 1, "keep": 1}
        ours = {"keep": 1, "mine": 2}
        theirs = {"old": 1, "keep": 1, "theirs": 3}
        self.assertEqual(gsp.merge_values(base, ours, theirs), {"keep": 1, "mine": 2, "theirs": 3})

    def test_article_lists_are_not_mixed(self):
        base = {"tags": ["a", "b"]}
        ours = {"tags": ["a", "c"]}
        theirs = {"tags": ["a", "b", "d"]}
        self.assertEqual(gsp.merge_values(base, ours, theirs, lists=False)["tags"], ["a", "c"])

    def test_state_lists_merge_additions_and_removals(self):
        base = {"log": [{"id": 1}, {"id": 2}]}
        ours = {"log": [{"id": 2}, {"id": 3}]}            # 古いものを捨てて足した
        theirs = {"log": [{"id": 1}, {"id": 2}, {"id": 4}]}
        self.assertEqual(gsp.merge_values(base, ours, theirs)["log"], [{"id": 2}, {"id": 4}, {"id": 3}])

    def test_counters_are_added(self):
        base = {"2026-11-03": {"gemini": 10}}
        ours = {"2026-11-03": {"gemini": 13, "claude": 1}}
        theirs = {"2026-11-03": {"gemini": 15}}
        merged = gsp.merge_values(base, ours, theirs, counters=True)
        self.assertEqual(merged, {"2026-11-03": {"gemini": 18, "claude": 1}})


class MergeFileTest(unittest.TestCase):
    def test_new_file_is_kept(self):
        data = dump({"slug": "new"})
        self.assertEqual(gsp.merge_file("content/articles/new.json", None, data, None)[0], data)

    def test_both_added_same_article_keeps_theirs_only_fields(self):
        ours = dump({"slug": "x", "title": "この回"})
        theirs = dump({"slug": "x", "title": "別", "thumbnail": "t.png"})
        merged = json.loads(gsp.merge_file("content/articles/x.json", None, ours, theirs)[0])
        self.assertEqual(merged, {"slug": "x", "title": "この回", "thumbnail": "t.png"})

    def test_retired_by_ours_is_deleted_even_if_edited_on_main(self):
        base = dump({"slug": "old", "title": "a"})
        theirs = dump({"slug": "old", "title": "b"})
        self.assertIsNone(gsp.merge_file("content/articles/old.json", base, None, theirs)[0])

    def test_retired_on_main_stays_deleted(self):
        base = dump({"slug": "old", "title": "a"})
        ours = dump({"slug": "old", "title": "b"})
        self.assertIsNone(gsp.merge_file("content/articles/old.json", base, ours, None)[0])

    def test_counter_file_detected_by_path(self):
        base, ours, theirs = dump({"d": {"m": 1}}), dump({"d": {"m": 3}}), dump({"d": {"m": 2}})
        merged = json.loads(gsp.merge_file("content/llm_usage.json", base, ours, theirs)[0])
        self.assertEqual(merged, {"d": {"m": 4}})

    def test_format_is_preserved(self):
        base = dump({"a": 1, "b": "日本語"})
        ours = dump({"a": 2, "b": "日本語"})
        theirs = dump({"a": 1, "b": "日本語2"})
        out = gsp.merge_file("content/articles/x.json", base, ours, theirs)[0]
        self.assertEqual(out, dump({"a": 2, "b": "日本語2"}))

    def test_text_files_use_line_merge_then_ours(self):
        base = b"1\n2\n3\n4\n5\n"
        ours = b"1x\n2\n3\n4\n5\n"
        theirs = b"1\n2\n3\n4\n5y\n"
        self.assertEqual(gsp.merge_file("state/note.txt", base, ours, theirs)[0], b"1x\n2\n3\n4\n5y\n")
        self.assertEqual(gsp.merge_file("state/last_deploy", b"1\n", b"2\n", b"3\n")[0], b"2\n")

    def test_strict_raises_on_same_field(self):
        with self.assertRaises(gsp.StrictConflict):
            gsp.merge_file("content/products.json", dump({"p": 1}), dump({"p": 2}), dump({"p": 3}), strict=True)

    def test_duplicate_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            gsp.parse_json(b'{"a": 1, "a": 2}')


class EndToEndTest(unittest.TestCase):
    """一時フォルダに main（bare）と2つの作業コピーを作り、本物の git で確かめる"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        env = {
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        }
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.remote = base / "remote.git"
        self.run_git(base, "init", "-q", "--bare", "-b", "main", str(self.remote))
        seed = base / "seed"
        self.run_git(base, "clone", "-q", str(self.remote), str(seed))
        self.write(seed, "content/articles/shared.json", {
            "slug": "shared", "title": "元の題名", "lede": "元のリード", "body": "本文",
            "sectionTitles": {"概要": "概要", "影響": "影響"}, "tags": ["a"],
        })
        self.write(seed, "content/articles/retired.json", {"slug": "retired", "title": "古い記事"})
        self.write(seed, "content/articles/gone.json", {"slug": "gone", "title": "main で消す記事"})
        self.write(seed, "content/llm_usage.json", {"2026-11-03": {"gemini": 10}})
        self.write(seed, "state/scratch.json", {"n": 0})
        self.write(seed, "state/index.json", [])
        self.run_git(seed, "add", "-A")
        self.run_git(seed, "commit", "-q", "-m", "seed")
        self.run_git(seed, "push", "-q", "origin", "HEAD:main")
        # この回（auto_run）の作業コピー（浅い clone＝actions/checkout と同じ）
        self.ours = base / "ours"
        self.run_git(base, "clone", "-q", "--depth", "1", f"file://{self.remote}", str(self.ours))
        # 同時に動く別のジョブ
        self.other = base / "other"
        self.run_git(base, "clone", "-q", str(self.remote), str(self.other))
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)

    @staticmethod
    def run_git(cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    @staticmethod
    def write(repo: Path, path: str, value) -> None:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(dump(value))

    @staticmethod
    def read(repo: Path, path: str):
        return json.loads((repo / path).read_text("utf-8"))

    def remote_json(self, path: str):
        out = subprocess.run(["git", "--git-dir", str(self.remote), "show", f"main:{path}"],
                             capture_output=True, text=True)
        return json.loads(out.stdout) if out.returncode == 0 else None

    def other_job_pushes(self):
        """見出しの書き直しジョブ: 同じ記事の隣の行（題名・リード・見出しの別の章）を直し、引退する記事も直し、使用回数を増やす"""
        o = self.other
        art = self.read(o, "content/articles/shared.json")
        art["title"] = "別のジョブの題名"
        art["sectionTitles"]["影響"] = "暮らしへの影響"
        self.write(o, "content/articles/shared.json", art)
        self.write(o, "content/articles/retired.json", {"slug": "retired", "title": "直された古い記事"})
        (o / "content/articles/gone.json").unlink()
        self.write(o, "content/llm_usage.json", {"2026-11-03": {"gemini": 12}})
        self.run_git(o, "add", "-A")
        self.run_git(o, "commit", "-q", "-m", "other job")
        self.run_git(o, "push", "-q", "origin", "HEAD:main")

    def our_run_commits(self, *, same_field: bool = False):
        r = self.ours
        art = self.read(r, "content/articles/shared.json")
        art["lede"] = "この回のリード"
        art["sectionTitles"]["概要"] = "何が発表されたか"
        if same_field:
            art["title"] = "この回の題名"
        self.write(r, "content/articles/shared.json", art)
        self.write(r, "content/articles/brand-new.json", {"slug": "brand-new", "title": "新しい記事"})
        (r / "content/articles/retired.json").unlink()
        gone = self.read(r, "content/articles/gone.json")
        gone["title"] = "この回で直した"
        self.write(r, "content/articles/gone.json", gone)
        self.write(r, "content/llm_usage.json", {"2026-11-03": {"gemini": 13}})
        self.run_git(r, "add", "-A")
        self.run_git(r, "commit", "-q", "-m", "chore(content): 記事を自動生成 (+1本)")
        # コミットしていない変更（あとのステップがコミットする state）
        self.write(r, "state/scratch.json", {"n": 1})

    def test_plain_rebase_would_conflict(self):
        """前提の確認: これまでのやり方（git pull --rebase）ではぶつかる"""
        self.other_job_pushes()
        self.our_run_commits()
        self.run_git(self.ours, "stash")
        proc = subprocess.run(["git", "pull", "-q", "--rebase", "origin", "main"], cwd=self.ours, capture_output=True)
        self.assertNotEqual(proc.returncode, 0)

    def test_failing_deploy_gate_blocks_the_push(self):
        """公開と同じテストに通らない中身は main に入れない（2026-11-04: データの変更で公開が落ち続けた件の予防）"""
        self.other_job_pushes()
        self.our_run_commits()
        before = subprocess.run(["git", "--git-dir", str(self.remote), "rev-parse", "main"], capture_output=True, text=True).stdout
        os.chdir(self.ours)
        self.assertEqual(gsp.safe_push(sleep=lambda _s: None, gate=lambda _root: False), 1)
        after = subprocess.run(["git", "--git-dir", str(self.remote), "rev-parse", "main"], capture_output=True, text=True).stdout
        self.assertEqual(before, after)
        self.assertEqual(self.read(self.ours, "state/scratch.json"), {"n": 1})  # 手元の変更は戻っている

    def test_main_uses_the_deploy_gate(self):
        src = (Path(gsp.__file__)).read_text("utf-8")
        self.assertIn("gate=deploy_gate", src)

    def test_merges_and_pushes_without_losing_the_new_article(self):
        self.other_job_pushes()
        self.our_run_commits()
        os.chdir(self.ours)
        self.assertEqual(gsp.safe_push(sleep=lambda _s: None), 0)

        shared = self.remote_json("content/articles/shared.json")
        self.assertEqual(shared["title"], "別のジョブの題名")            # 別のジョブの直しは残る
        self.assertEqual(shared["lede"], "この回のリード")              # この回の直しも入る
        self.assertEqual(shared["sectionTitles"], {"概要": "何が発表されたか", "影響": "暮らしへの影響"})
        self.assertEqual(self.remote_json("content/articles/brand-new.json")["title"], "新しい記事")
        self.assertIsNone(self.remote_json("content/articles/retired.json"))   # この回で引退
        self.assertIsNone(self.remote_json("content/articles/gone.json"))      # main で消されたまま
        self.assertEqual(self.remote_json("content/llm_usage.json"), {"2026-11-03": {"gemini": 15}})
        msg = subprocess.run(["git", "--git-dir", str(self.remote), "log", "-1", "--format=%s", "main"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(msg, "chore(content): 記事を自動生成 (+1本)")
        # 未解決のファイルが無く、コミットしていなかった変更も戻っている
        self.assertEqual(self.run_git(self.ours, "diff", "--name-only", "--diff-filter=U"), "")
        self.assertEqual(self.read(self.ours, "state/scratch.json"), {"n": 1})
        self.assertEqual(self.run_git(self.ours, "stash", "list"), "")

    def test_fast_forward_when_main_did_not_move(self):
        self.our_run_commits()
        os.chdir(self.ours)
        self.assertEqual(gsp.safe_push(sleep=lambda _s: None), 0)
        self.assertEqual(self.remote_json("content/articles/brand-new.json")["slug"], "brand-new")

    def test_regen_runs_on_fresh_main(self):
        self.other_job_pushes()
        self.our_run_commits()
        os.chdir(self.ours)
        regen = f'{sys.executable} -c "import glob,json; json.dump(sorted(glob.glob(\'content/articles/*.json\')), open(\'state/index.json\',\'w\'))"'
        self.assertEqual(gsp.safe_push(sleep=lambda _s: None, regen=[regen]), 0)
        self.assertEqual(self.remote_json("state/index.json"), [
            "content/articles/brand-new.json", "content/articles/shared.json"])

    def test_strict_conflict_restores_the_run_commit(self):
        self.other_job_pushes()
        self.our_run_commits(same_field=True)
        ours_head = self.run_git(self.ours, "rev-parse", "HEAD")
        os.chdir(self.ours)
        self.assertEqual(gsp.safe_push(strict=True, sleep=lambda _s: None), 1)
        self.assertEqual(self.run_git(self.ours, "rev-parse", "HEAD"), ours_head)
        self.assertEqual(self.run_git(self.ours, "diff", "--name-only", "--diff-filter=U"), "")
        self.assertTrue((self.ours / "content/articles/brand-new.json").exists())
        self.assertEqual(self.read(self.ours, "state/scratch.json"), {"n": 1})

    def test_same_field_without_strict_takes_this_run(self):
        self.other_job_pushes()
        self.our_run_commits(same_field=True)
        os.chdir(self.ours)
        self.assertEqual(gsp.safe_push(sleep=lambda _s: None), 0)
        self.assertEqual(self.remote_json("content/articles/shared.json")["title"], "この回の題名")

    def test_retries_when_another_push_lands_in_between(self):
        self.our_run_commits()
        os.chdir(self.ours)
        real_push = gsp.push
        calls = []

        def racing_push(remote, branch):
            if not calls:  # 1回目の push の直前に、別のジョブが先に push する
                os.chdir(self.other)
                self.other_job_pushes()
                os.chdir(self.ours)
            calls.append(1)
            return real_push(remote, branch)

        with patch.object(gsp, "push", side_effect=racing_push):
            self.assertEqual(gsp.safe_push(sleep=lambda _s: None), 0)
        self.assertEqual(len(calls), 2)
        shared = self.remote_json("content/articles/shared.json")
        self.assertEqual((shared["title"], shared["lede"]), ("別のジョブの題名", "この回のリード"))
        self.assertEqual(self.remote_json("content/articles/brand-new.json")["slug"], "brand-new")

    def test_leftover_rebase_is_aborted(self):
        self.other_job_pushes()
        self.our_run_commits()
        self.run_git(self.ours, "stash")
        subprocess.run(["git", "pull", "-q", "--rebase", "origin", "main"], cwd=self.ours, capture_output=True)
        os.chdir(self.ours)
        self.assertEqual(gsp.safe_push(sleep=lambda _s: None), 0)
        self.assertEqual(self.remote_json("content/articles/brand-new.json")["slug"], "brand-new")


class WorkflowGuardTest(unittest.TestCase):
    def test_no_workflow_uses_plain_pull_rebase_retry(self):
        """記事・state をコミットするワークフローは git_safe_push.py を通す（未解決のまま3回失敗して記事が消えた）"""
        offenders = []
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            text = path.read_text("utf-8")
            if "pull --rebase" in text or "rebase --abort" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_article_editors_wait_for_auto_run(self):
        for name in ("section_backfill.yml", "broken_images.yml", "review_sync.yml"):
            text = (ROOT / ".github" / "workflows" / name).read_text("utf-8")
            self.assertIn("git_safe_push.py --wait-idle auto_run.yml", text, name)


if __name__ == "__main__":
    unittest.main()
