"""SWEEP THE REPO BEFORE BUILDING -- tools/sweep end to end.

Each test builds a throwaway repo, points its hooks at a commit-msg that
runs ``tools/sweep/sweep.py`` exactly as the installed shim does, and
drives real ``git commit`` / ``git merge`` / ``git revert`` calls.
"""

import os
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SWEEP_DIR = os.path.join(REPO_ROOT, "tools", "sweep")
SWEEP = os.path.join(SWEEP_DIR, "sweep.py")

pytestmark = pytest.mark.skipif(shutil.which("git") is None,
                                reason="needs git")

TRAILER = ("Sweep: searched ReplayDriver/replay on origin/main, branches "
           "and worktrees; nothing equivalent exists")


def _env(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="t@example.com",
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
        HOME=str(tmp_path),
    )
    env.pop("SWEEP_SKIP", None)
    return env


class Repo:
    def __init__(self, path, env):
        self.path, self.env = str(path), env

    def git(self, *args, check=True, env=None):
        proc = subprocess.run(["git", "-C", self.path, *args],
                              capture_output=True, text=True,
                              env=env or self.env)
        if check and proc.returncode != 0:
            raise AssertionError(proc.stdout + proc.stderr)
        return proc

    def write(self, rel, text):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def commit(self, msg, *paths, env=None):
        self.git("add", *(paths or ("-A",)))
        return self.git("commit", "-q", "-m", msg, check=False, env=env)


@pytest.fixture
def repo(tmp_path):
    env = _env(tmp_path)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "commit-msg"
    hook.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{SWEEP}" '
                    '--commit-msg "$1"\n')
    hook.chmod(0o755)
    r = Repo(tmp_path / "repo", env)
    os.makedirs(r.path)
    r.git("init", "-q", "-b", "main")
    r.git("config", "core.hooksPath", str(hooks))
    r.write("pkg/core.py", "def existing(x):\n    return x\n")
    r.write("README.md", "hi\n")
    # The seed commit adds files, so it is acknowledged like any other.
    assert r.commit("seed\n\n" + TRAILER).returncode == 0
    return r


def test_duplicate_class_on_another_branch_is_reported_and_needs_trailer(repo):
    repo.git("checkout", "-q", "-b", "other")
    repo.write("pkg/driver.py", "class ReplayDriver:\n    pass\n")
    assert repo.commit("other lane\n\n" + TRAILER).returncode == 0
    repo.git("checkout", "-q", "main")
    repo.write("pkg/core.py",
               "def existing(x):\n    return x\n\n\n"
               "class ReplayDriver:\n    pass\n")
    refused = repo.commit("add a driver")
    assert refused.returncode != 0
    assert "[class] ReplayDriver" in refused.stderr
    assert "pkg/driver.py" in refused.stderr and "@ other" in refused.stderr
    assert "REFUSED" in refused.stderr
    assert repo.commit("add a driver\n\n" + TRAILER).returncode == 0


def test_pure_edit_passes_without_trailer(repo):
    repo.write("pkg/core.py", "def existing(x):\n    return x + 1\n")
    done = repo.commit("tweak existing")
    assert done.returncode == 0, done.stderr
    assert "sweep:" not in done.stderr


def test_signature_edit_and_move_are_not_new(repo):
    repo.write("pkg/core.py", "def existing(x, y=0):\n    return x + y\n")
    assert repo.commit("widen existing").returncode == 0
    repo.write("pkg/core.py", "")
    repo.write("pkg/core2.py", "def existing(x, y=0):\n    return x + y\n")
    repo.git("rm", "-q", "--cached", "pkg/core.py")
    os.remove(os.path.join(repo.path, "pkg/core.py"))
    assert repo.commit("move it").returncode == 0


def test_new_file_requires_trailer(repo):
    repo.write("pkg/widget.json", '{"a": 1}\n')
    refused = repo.commit("add widget config")
    assert refused.returncode != 0
    assert "[file] pkg/widget.json" in refused.stderr
    short = repo.commit("add widget config\n\nSweep: looked")
    assert short.returncode != 0  # a token trailer is not an acknowledgment
    assert repo.commit("add widget config\n\n" + TRAILER).returncode == 0


def test_docs_and_tests_only_pass(repo):
    repo.write("docs/new-note.md", "# note\n")
    repo.write("tests/test_new.py", "def test_x():\n    pass\n")
    done = repo.commit("docs and tests")
    assert done.returncode == 0, done.stderr


def test_merge_commit_passes(repo):
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/feature.py", "class Feature:\n    pass\n")
    assert repo.commit("feature\n\n" + TRAILER).returncode == 0
    repo.git("checkout", "-q", "main")
    repo.write("pkg/core.py", "def existing(x):\n    return 2 * x\n")
    assert repo.commit("edit main").returncode == 0
    merged = repo.git("merge", "--no-ff", "-q", "-m", "Merge feature",
                      "feature", check=False)
    assert merged.returncode == 0, merged.stderr
    assert repo.git("rev-parse", "HEAD^2").returncode == 0


def test_revert_of_a_deletion_passes(repo):
    repo.git("rm", "-q", "pkg/core.py")
    assert repo.commit("drop core").returncode == 0
    done = repo.git("revert", "--no-edit", "HEAD", check=False)
    assert done.returncode == 0, done.stderr


def test_skip_is_logged_and_stamped(repo):
    repo.write("pkg/hotfix.py", "def hotfix():\n    pass\n")
    env = dict(repo.env, SWEEP_SKIP="prod down, sweep after")
    assert repo.commit("hotfix", env=env).returncode == 0
    body = repo.git("log", "-1", "--format=%B").stdout
    assert "Sweep-Skipped: prod down, sweep after" in body
    common = repo.git("rev-parse", "--git-common-dir").stdout.strip()
    log = os.path.join(repo.path, common, "sweep-skips.log")
    with open(log, encoding="utf-8") as fh:
        assert "prod down, sweep after" in fh.read()


def test_other_worktree_untracked_file_is_found(repo, tmp_path):
    wt = str(tmp_path / "wt2")
    repo.git("worktree", "add", "-q", "-b", "lane2", wt)
    os.makedirs(os.path.join(wt, "pkg"), exist_ok=True)
    with open(os.path.join(wt, "pkg", "calib.py"), "w") as fh:
        fh.write("class CalibrationLens:\n    pass\n")
    repo.write("pkg/lens.py", "class CalibrationLens:\n    pass\n")
    refused = repo.commit("add lens")
    assert refused.returncode != 0
    assert "pkg/calib.py" in refused.stderr and "wt:wt2" in refused.stderr


def test_term_search_cli(repo):
    out = subprocess.run([sys.executable, SWEEP, "existing"], cwd=repo.path,
                         capture_output=True, text=True, env=repo.env)
    assert out.returncode == 0
    assert "= existing  pkg/core.py:1" in out.stdout


def test_merge_with_a_plain_message_still_passes(repo):
    repo.git("checkout", "-q", "-b", "lane")
    repo.write("pkg/lane.py", "class Lane:\n    pass\n")
    assert repo.commit("lane\n\n" + TRAILER).returncode == 0
    repo.git("checkout", "-q", "main")
    repo.write("pkg/core.py", "def existing(x):\n    return 3 * x\n")
    assert repo.commit("edit main").returncode == 0
    merged = repo.git("merge", "--no-ff", "-q", "-m", "combine lanes",
                      "lane", check=False)
    assert merged.returncode == 0, merged.stderr


def test_commit_with_pathspec_uses_the_real_index(repo):
    # `git commit <path>` hands the hook a temporary GIT_INDEX_FILE.
    repo.write("pkg/core.py", "def existing(x):\n    return -x\n")
    repo.write("pkg/extra.py", "def extra():\n    pass\n")
    repo.git("add", "pkg/extra.py")
    refused = repo.git("commit", "-q", "-m", "add extra", "pkg/extra.py",
                       check=False)
    assert refused.returncode != 0 and "[file] pkg/extra.py" in refused.stderr


def test_install_shim_runs_the_worktree_hook_and_removes_cleanly(tmp_path):
    env = _env(tmp_path)
    r = Repo(tmp_path / "r", env)
    os.makedirs(r.path)
    r.git("init", "-q", "-b", "main")
    shutil.copytree(SWEEP_DIR, os.path.join(r.path, "tools", "sweep"))
    os.makedirs(os.path.join(r.path, ".githooks"))
    shutil.copy(os.path.join(REPO_ROOT, ".githooks", "commit-msg"),
                os.path.join(r.path, ".githooks", "commit-msg"))
    hooks = os.path.join(r.path, ".git", "hooks")
    os.makedirs(hooks, exist_ok=True)
    prior = os.path.join(hooks, "commit-msg")
    with open(prior, "w") as fh:
        fh.write("#!/bin/sh\necho prior-ran >&2\n")
    os.chmod(prior, 0o755)
    inst = subprocess.run(["sh", "tools/sweep/install.sh"], cwd=r.path,
                          capture_output=True, text=True, env=env)
    assert inst.returncode == 0, inst.stderr
    r.write("pkg/new.py", "def fresh():\n    pass\n")
    refused = r.commit("new")
    assert refused.returncode != 0
    assert "prior-ran" in refused.stderr and "REFUSED" in refused.stderr
    assert r.commit("new\n\n" + TRAILER).returncode == 0
    subprocess.run(["sh", "tools/sweep/install.sh", "--remove"], cwd=r.path,
                   check=True, capture_output=True, env=env)
    with open(prior) as fh:
        assert "prior-ran" in fh.read()


def test_remind_script_emits_context_only_for_dskit(tmp_path):
    remind = os.path.join(SWEEP_DIR, "remind.sh")
    env = dict(os.environ, TMPDIR=str(tmp_path))

    def run(mode, payload):
        return subprocess.run(["sh", remind, mode, "UserPromptSubmit"],
                              input=payload, capture_output=True, text=True,
                              env=env).stdout

    assert run("touch", '{"session_id":"s1","prompt":"fix pmquant"}') == ""
    first = run("touch", '{"session_id":"s1","prompt":"build it in dskit"}')
    assert "SWEEP THE REPO BEFORE BUILDING" in first
    assert '"hookEventName":"UserPromptSubmit"' in first
    assert run("touch", '{"session_id":"s1","prompt":"dskit again"}') == ""
    new = '{"tool_input":{"file_path":"/x/dskit/nope/new.py"}}'
    assert "additionalContext" in run("write", new)
    there = tmp_path / "dskit" / "old.py"
    there.parent.mkdir()
    there.write_text("x = 1\n")
    existing = '{"tool_input":{"file_path":"' + str(there) + '"}}'
    assert run("write", existing) == ""  # editing, not creating
    out = run("session", '{"cwd":"/elsewhere"}')
    assert "additionalContext" in out
