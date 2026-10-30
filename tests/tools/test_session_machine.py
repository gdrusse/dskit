"""tools/session/machine.sh -- which machine is this agent session on?

The owner works from a Mac, a Windows PC and WSL on that PC, and the repo's
instructions grew WSL-only steps (``wsl.exe``, ``~/wt/``) that are wrong on
the Mac. Each test runs the real script under ``sh`` with a fake ``uname``
first on PATH, so every host family is exercised on whichever machine runs
the suite -- including a WSL box, whose real ``uname -r`` must not leak in.
"""

import json
import os
import re
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
MACHINE = os.path.join(REPO_ROOT, "tools", "session", "machine.sh")
REMIND = os.path.join(REPO_ROOT, "tools", "sweep", "remind.sh")

pytestmark = pytest.mark.skipif(shutil.which("sh") is None,
                                reason="needs a POSIX sh")

# (uname -s, uname -r, extra env) -> the --os word the script must print.
HOSTS = {
    "macos": ("Darwin", "25.6.0", {}),
    "wsl": ("Linux", "5.15.167.4-microsoft-standard-WSL2", {}),
    "wsl1": ("Linux", "4.4.0-19041-Microsoft", {}),
    "wsl_env_only": ("Linux", "6.1.0", {"WSL_DISTRO_NAME": "Ubuntu"}),
    "linux": ("Linux", "6.8.0-45-generic", {}),
    "windows_mingw": ("MINGW64_NT-10.0-19045", "3.4.10", {}),
    "windows_msys": ("MSYS_NT-10.0-19045", "3.4.10", {}),
    "windows_cygwin": ("CYGWIN_NT-10.0", "3.5.4", {}),
    "unknown": ("FreeBSD", "14.1-RELEASE", {}),
}
EXPECTED_OS = {
    "macos": "macos", "wsl": "wsl", "wsl1": "wsl", "wsl_env_only": "wsl",
    "linux": "linux", "windows_mingw": "windows", "windows_msys": "windows",
    "windows_cygwin": "windows", "unknown": "unknown",
}


def _fake_uname(tmp_path, sysname, release, node="box-1", machine="arm64"):
    """Put a ``uname`` that answers -s/-r/-n/-m first on a fresh PATH dir.

    ``node`` goes through ``printf '%b'``, so ``\\0ddd`` in it becomes that
    raw byte -- the only way to hand the script an invalid-UTF-8 or control
    byte deterministically across shells.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "uname"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  -s) echo '{sysname}' ;;\n"
        f"  -r) echo '{release}' ;;\n"
        f"  -n) printf '%b\\n' '{node}' ;;\n"
        f"  -m) echo '{machine}' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    script.chmod(0o755)
    return str(bin_dir)


def _run(tmp_path, host, *args, node="box-1", user="tester", cwd=None,
         extra_env=None):
    sysname, release, extra = HOSTS[host]
    bin_dir = _fake_uname(tmp_path, sysname, release, node=node)
    env = {k: v for k, v in os.environ.items() if k != "WSL_DISTRO_NAME"}
    env.update(extra)
    env.update(extra_env or {})
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    env["USER"] = user
    return subprocess.run(["sh", MACHINE, *args], capture_output=True,
                          env=env, cwd=cwd or REPO_ROOT,
                          text=True, errors="replace")


@pytest.mark.parametrize("host", sorted(HOSTS))
def test_os_word_names_every_host_family(tmp_path, host):
    proc = _run(tmp_path, host, "--os")
    assert proc.returncode == 0
    assert proc.stdout == EXPECTED_OS[host] + "\n"


def test_text_names_the_host_user_and_repo(tmp_path):
    out = _run(tmp_path, "macos", "--text", node="studio.local").stdout
    assert out.startswith("Machine: macOS")
    assert "studio.local" in out and "tester" in out
    assert REPO_ROOT in out
    assert "wsl.exe" in out  # macOS is told NOT to use it
    assert out.count("\n") == 1


def test_only_windows_is_told_to_reach_for_wsl_exe(tmp_path):
    windows = _run(tmp_path, "windows_mingw", "--text").stdout
    wsl = _run(tmp_path, "wsl", "--text").stdout
    assert "wsl.exe -e bash -lc" in windows
    assert "wsl.exe -e bash -lc" not in wsl


def test_hook_mode_emits_claude_session_start_json(tmp_path):
    proc = _run(tmp_path, "wsl")
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    assert out["additionalContext"].startswith("Machine: WSL")


def test_hook_json_survives_quotes_and_backslashes(tmp_path):
    proc = _run(tmp_path, "linux", "UserPromptSubmit",
                node='we"ird\\host', user='a"b')
    payload = json.loads(proc.stdout)  # would raise on broken escaping
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit"
    assert 'we"ird\\host' in out["additionalContext"]


def test_control_bytes_in_a_hostname_cannot_break_the_json(tmp_path):
    proc = _run(tmp_path, "macos", node="ho%st\\0001x\\0033[31m")
    out = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "ho%st" in out
    assert not re.search(r"[\x00-\x1f\x7f]", out)


def test_invalid_utf8_in_a_hostname_keeps_the_note(tmp_path):
    # BSD tr in a UTF-8 locale aborts on an invalid byte and drops the rest
    # of the line -- the whole note -- unless the script pins LC_ALL=C.
    out = _run(tmp_path, "macos", "--text", node="caf\\0351",
               extra_env={"LC_ALL": "en_US.UTF-8"}).stdout
    assert "Absolute paths written on another machine" in out


def test_mac_note_drops_only_the_wsl_part_of_a_step(tmp_path):
    # "skip steps that say to work in WSL" would let a Mac agent skip a
    # whole mandated step (fetch + worktree from origin/main). Pin that the
    # note keeps the rest of the step.
    out = _run(tmp_path, "macos", "--text").stdout
    assert "drop only that part" in out
    assert "worktree from origin/main" in out


def test_outside_a_git_repo_falls_back_to_the_cwd(tmp_path):
    outside = tmp_path / "plain"
    outside.mkdir()
    # A tmp dir that happens to sit inside some repo must not find it.
    out = _run(tmp_path, "linux", "--text", cwd=str(outside),
               extra_env={"GIT_CEILING_DIRECTORIES": str(tmp_path)}).stdout
    assert str(outside.resolve()) in out or str(outside) in out


def test_bad_flag_fails_loudly_but_hook_mode_never_blocks(tmp_path):
    bad = _run(tmp_path, "macos", "--nope")
    assert bad.returncode == 1 and "usage" in bad.stderr
    # An event name is any non-flag word; it must not be mistaken for a flag.
    assert _run(tmp_path, "macos", "SessionStart").returncode == 0


def _remind(tmp_path, host):
    sysname, release, extra = HOSTS[host]
    env = {k: v for k, v in os.environ.items() if k != "WSL_DISTRO_NAME"}
    env.update(extra)
    env["PATH"] = (_fake_uname(tmp_path, sysname, release) + os.pathsep
                   + env.get("PATH", ""))
    env["TMPDIR"] = str(tmp_path)
    return subprocess.run(["sh", REMIND, "session", "SessionStart"],
                          input='{"cwd":"/x/dskit"}', capture_output=True,
                          text=True, env=env).stdout


@pytest.mark.parametrize("host,has_hint", [
    ("windows_mingw", True), ("macos", False), ("wsl", False),
    ("linux", False),
])
def test_sweep_reminder_gives_the_wsl_hint_only_on_windows(tmp_path, host,
                                                           has_hint):
    out = _remind(tmp_path, host)
    assert "SWEEP THE REPO BEFORE BUILDING" in out
    assert ("wsl.exe" in out) is has_hint
    json.loads(out)  # still one valid hook payload


def test_sweep_reminder_differs_across_hosts_only_by_the_hint(tmp_path):
    windows = _remind(tmp_path, "windows_mingw")
    mac = _remind(tmp_path, "macos")
    hint = " (WSL: wsl.exe -e bash -lc 'cd ~/wt/<your-worktree> && " \
           "tools/sweep/sweep <term>')"
    assert hint in windows
    assert windows.replace(hint, "") == mac


def test_claude_session_start_hook_runs_the_script():
    with open(os.path.join(REPO_ROOT, ".claude", "settings.json")) as fh:
        hooks = json.load(fh)["hooks"]["SessionStart"]
    entries = [h for group in hooks for h in group["hooks"]
               if "tools/session/machine.sh" in h["command"]]
    assert len(entries) == 1
    # A SessionStart hook that hangs delays every session, and one passed a
    # flag instead of the event name would print text, not hook JSON.
    assert entries[0]["command"].endswith(" SessionStart")
    assert entries[0]["timeout"] == 5
