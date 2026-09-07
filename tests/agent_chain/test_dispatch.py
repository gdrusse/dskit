"""Tests for .agent-chain/dispatch.py's command-building and dispatch logic."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".agent-chain"))
import dispatch as ac  # noqa: E402


CONFIG = {
    "claude": {
        "binary": "claude",
        "promptArg": "-p",
        "modelFlag": "--model",
        "models": {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku"},
        "autoApproveFlags": ["--permission-mode", "bypassPermissions"],
        "cwdFlag": None,
    },
    "codex": {
        "binary": "codex",
        "subcommand": "exec",
        "promptArg": "positional",
        "modelFlag": "-m",
        "models": {},
        "autoApproveFlags": ["--full-auto"],
        "cwdFlag": "-C",
    },
}


def test_build_command_claude_named_prompt_flag():
    argv = ac.build_command("claude", "sonnet", "do the thing", CONFIG)
    assert argv == [
        "claude", "--permission-mode", "bypassPermissions",
        "--model", "sonnet", "-p", "do the thing",
    ]


def test_build_command_codex_positional_prompt_with_subcommand():
    argv = ac.build_command("codex", None, "do the thing", CONFIG)
    assert argv == ["codex", "exec", "--full-auto", "do the thing"]


def test_build_command_unmapped_tier_omits_model_flag():
    argv = ac.build_command("codex", "opus", "do the thing", CONFIG)
    assert "-m" not in argv
    assert argv == ["codex", "exec", "--full-auto", "do the thing"]


def test_build_command_cwd_only_applied_when_adapter_declares_cwd_flag():
    argv = ac.build_command("codex", None, "do the thing", CONFIG, cwd="/tmp/x")
    assert argv == ["codex", "exec", "--full-auto", "-C", "/tmp/x", "do the thing"]


def test_build_command_unknown_tool_raises():
    with pytest.raises(KeyError):
        ac.build_command("nonexistent", "sonnet", "x", CONFIG)


def test_load_tools_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ac.load_tools_config(tmp_path / "nope.json")


def test_load_tools_config_invalid_json_raises(tmp_path):
    bad = tmp_path / "tools.json"
    bad.write_text("{not valid json")
    with pytest.raises(ValueError):
        ac.load_tools_config(bad)


def test_dispatch_success_returns_stripped_stdout(monkeypatch):
    def fake_run(argv, capture_output, text, timeout, cwd):
        return subprocess.CompletedProcess(argv, 0, stdout="result text\n", stderr="")

    monkeypatch.setattr(ac.subprocess, "run", fake_run)
    out = ac.dispatch("claude", "sonnet", "hi", config=CONFIG)
    assert out == "result text"


def test_dispatch_nonzero_exit_raises_runtimeerror(monkeypatch):
    def fake_run(argv, capture_output, text, timeout, cwd):
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="boom")

    monkeypatch.setattr(ac.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="boom"):
        ac.dispatch("claude", "sonnet", "hi", config=CONFIG)
