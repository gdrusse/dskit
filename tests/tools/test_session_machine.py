"""tools/session/machine.sh -- which machine is this agent session on?

The owner works from a Mac, a Windows PC and WSL on that PC, and the repo's
instructions grew WSL-only steps (``wsl.exe``, ``~/wt/``) that are wrong on
the Mac. Each test runs the real script under ``sh`` with a fake ``uname``
first on PATH, so every host family is exercised on whichever machine runs
the suite -- including a WSL box, whose real ``uname -r`` must not leak in.
"""

import json
import os
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
    """Put a ``uname`` that answers -s/-r/-n/-m first on a fresh PATH dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "uname"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  -s) echo '{sysname}' ;;\n"
        f"  -r) echo '{release}' ;;\n"
        f"  -n) echo '{node}' ;;\n"
        f"  -m) echo '{machine}' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    script.chmod(0o755)
    return str(bin_dir)


def _run(tmp_path, host, *args, node="box-1", user="tester", cwd=None):
    sysname, release, extra = HOSTS[host]
    bin_dir = _fake_uname(tmp_path, sysname, release, node=node)
    env = {k: v for k, v in os.environ.items() if k != "WSL_DISTRO_NAME"}
    env.update(extra)
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    env["USER"] = user
    return subprocess.run(["sh", MACHINE, *args], capture_output=True,
                          text=True, env=env, cwd=cwd or REPO_ROOT)


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


def test_outside_a_git_repo_falls_back_to_the_cwd(tmp_path):
    outside = tmp_path / "plain"
    outside.mkdir()
    out = _run(tmp_path, "linux", "--text", cwd=str(outside)).stdout
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


def test_claude_session_start_hook_runs_the_script():
    with open(os.path.join(REPO_ROOT, ".claude", "settings.json")) as fh:
        hooks = json.load(fh)["hooks"]["SessionStart"]
    commands = [h["command"] for group in hooks for h in group["hooks"]]
    assert any("tools/session/machine.sh" in c for c in commands)
