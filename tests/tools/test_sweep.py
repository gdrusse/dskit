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


@pytest.mark.parametrize("subject", ["fixup! add widget", "squash! add widget",
                                     "amend! add widget"])
def test_autosquash_subjects_pass(repo, subject):
    repo.write("pkg/widget.py", "class Widget:\n    pass\n")
    done = repo.commit(subject)
    assert done.returncode == 0, done.stderr


def test_budget_bounds_many_slow_worktrees(repo, tmp_path):
    import json
    import time

    budget = 2.0
    with open(os.path.join(SWEEP_DIR, "sweep.json")) as fh:
        cfg = json.load(fh)
    cfg.update(budget_s=budget, budget_reserve_s=0.5,
               worktree_timeout_s=30, worktree_workers=4)
    cfg_path = tmp_path / "sweep-budget.json"
    cfg_path.write_text(json.dumps(cfg))
    for i in range(20):
        repo.git("worktree", "add", "-q", "-b", f"lane{i}",
                 str(tmp_path / f"wt{i}"))
    # A git whose ls-files hangs in a CHILD process (the worst case: the
    # grandchild holds the pipe), everything else real.
    fake = tmp_path / "bin"
    fake.mkdir()
    real = shutil.which("git")
    (fake / "git").write_text(
        "#!/bin/sh\n"
        'case " $* " in *" ls-files "*) sleep 60 ;; esac\n'
        f'exec "{real}" "$@"\n')
    (fake / "git").chmod(0o755)
    # git puts its exec-path first on a hook's PATH, so set ours inside it.
    hook = tmp_path / "hooks" / "commit-msg"
    hook.write_text(f'#!/bin/sh\nPATH="{fake}:$PATH" exec "{sys.executable}" '
                    f'"{SWEEP}" --config "{cfg_path}" --commit-msg "$1"\n')
    repo.write("pkg/slow.py", "class SlowThing:\n    pass\n")
    repo.git("add", "-A")
    t0 = time.monotonic()
    done = repo.git("commit", "-q", "-m", "add slow", check=False)
    elapsed = time.monotonic() - t0
    assert elapsed < budget + 1.5, elapsed
    assert "not searched (budget)" in done.stderr
    assert done.returncode != 0 and "REFUSED" in done.stderr  # rule holds


def _fake_git_hook(repo, tmp_path, case_body, **overrides):
    """Point the hook at a sweep.json with ``overrides`` and a fake git.

    ``case_body`` is the inside of a ``case " $* " in ... esac`` that runs
    before the real git; git puts its exec-path first on a hook's PATH,
    so the fake is prepended inside the hook itself.
    """
    import json

    with open(os.path.join(SWEEP_DIR, "sweep.json")) as fh:
        cfg = json.load(fh)
    cfg.update(overrides)
    cfg_path = tmp_path / "sweep-test.json"
    cfg_path.write_text(json.dumps(cfg))
    fake = tmp_path / "fakebin"
    fake.mkdir(exist_ok=True)
    real = shutil.which("git")
    (fake / "git").write_text(
        f'#!/bin/sh\ncase " $* " in\n{case_body}\nesac\nexec "{real}" "$@"\n')
    (fake / "git").chmod(0o755)
    hook = tmp_path / "hooks" / "commit-msg"
    hook.write_text(f'#!/bin/sh\nPATH="{fake}:$PATH" exec "{sys.executable}" '
                    f'"{SWEEP}" --config "{cfg_path}" --commit-msg "$1"\n')
    return cfg


def test_skip_is_recorded_even_when_git_hangs(repo, tmp_path):
    import time

    cfg = _fake_git_hook(
        repo, tmp_path,
        '  *" --abbrev-ref "*|*" var "*|*" --git-common-dir "*) sleep 60 ;;',
        budget_s=2, skip_git_timeout_s=1)
    repo.write("pkg/urgent.py", "def urgent():\n    pass\n")
    env = dict(repo.env, SWEEP_SKIP="prod down")
    t0 = time.monotonic()
    done = repo.commit("urgent", env=env)
    elapsed = time.monotonic() - t0
    assert done.returncode == 0, done.stderr
    assert "Traceback" not in done.stderr
    assert elapsed < cfg["budget_s"] + 4, elapsed  # inside the outer cap
    body = repo.git("log", "-1", "--format=%B").stdout
    assert "Sweep-Skipped: prod down" in body
    with open(os.path.join(repo.path, ".git", "sweep-skips.log")) as fh:
        assert "prod down" in fh.read()


def test_unexpected_error_fails_open_without_traceback(repo, tmp_path):
    _fake_git_hook(repo, tmp_path,
                   '  *" diff "*) echo "boom" >&2; exit 2 ;;')
    repo.write("pkg/other.py", "def other():\n    pass\n")
    done = repo.commit("no trailer, but git is broken")
    assert done.returncode == 0, done.stderr
    assert "internal error, commit NOT checked" in done.stderr
    assert "Traceback" not in done.stderr


def test_inventory_failure_still_enforces_the_trailer(repo, tmp_path):
    _fake_git_hook(repo, tmp_path,
                   '  *" for-each-ref "*) echo "boom" >&2; exit 2 ;;')
    repo.write("pkg/third.py", "def third():\n    pass\n")
    done = repo.commit("no trailer")
    assert done.returncode != 0 and "REFUSED" in done.stderr
    assert "matches unavailable" in done.stderr
    assert "Traceback" not in done.stderr
