"""Startup hooks refresh remote refs without changing an active checkout."""

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _git(directory, *args):
    return subprocess.check_output(
        ["git", "-C", str(directory), *args], text=True
    ).strip()


def _commit(directory, message):
    _git(directory, "add", ".")
    _git(
        directory,
        "-c", "user.name=DSKit test fixture",
        "-c", "user.email=fixture@example.invalid",
        "commit", "-m", message,
    )


def _startup_command(platform, checkout):
    if platform == "claude":
        obj = json.loads((ROOT / ".claude/settings.json").read_text())
        return obj["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    if platform == "cursor":
        obj = json.loads((ROOT / ".cursor/hooks.json").read_text())
        return obj["hooks"]["sessionStart"][0]["command"]
    # Exercise the plugin's actual Git shell template. This is not an
    # OpenCode event-loader test; the plugin's dispatch remains unchanged.
    source = (ROOT / ".opencode/plugin/session-pull.ts").read_text()
    match = re.search(r"await \$\x60([^\x60]+)\x60", source)
    assert match, "OpenCode startup Git invocation was not found"
    return match[1].replace("${directory}", shlex.quote(str(checkout)))


def _checkout_state(checkout):
    return (
        _git(checkout, "rev-parse", "HEAD"),
        _git(checkout, "status", "--porcelain"),
        _git(checkout, "diff", "--binary"),
        _git(checkout, "diff", "--cached", "--binary"),
        (checkout / "upstream.txt").read_bytes(),
        (checkout / "untracked.txt").read_bytes(),
    )


@pytest.mark.parametrize("platform", ["claude", "cursor", "opencode"])
def test_startup_refreshes_refs_without_fast_forwarding_dirty_checkout(
    tmp_path, platform
):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-b", "main")
    (upstream / "user.txt").write_text("base user file\n")
    (upstream / "upstream.txt").write_text("base remote file\n")
    _commit(upstream, "base")

    checkout = tmp_path / "checkout with spaces"
    subprocess.run(
        ["git", "clone", str(upstream), str(checkout)],
        check=True, capture_output=True, text=True,
    )
    (checkout / "user.txt").write_text("unfinished user work\n")
    (checkout / "staged.txt").write_text("unfinished staged work\n")
    _git(checkout, "add", "staged.txt")
    (checkout / "untracked.txt").write_text("untracked work\n")
    before = _checkout_state(checkout)

    (upstream / "upstream.txt").write_text("new upstream change\n")
    _commit(upstream, "advance a disjoint file")
    latest = _git(upstream, "rev-parse", "HEAD")
    assert _git(checkout, "rev-parse", "origin/main") != latest

    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(checkout))
    subprocess.run(
        ["bash", "-c", _startup_command(platform, checkout)],
        cwd=checkout, env=env, check=True, capture_output=True, text=True,
    )

    assert _git(checkout, "rev-parse", "origin/main") == latest
    assert _checkout_state(checkout) == before
