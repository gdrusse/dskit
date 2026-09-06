"""CLI e2e in a fresh interpreter: init, record, research, promote, exec."""

import os
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def run_cli(*argv, cwd, extra_env=None):
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env["DSKIT_JOURNAL_TESTS"] = "1"
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "dskit.journal", *argv],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def _shape(cwd):
    (cwd / "pyproject.toml").write_text("[project]\nname='x'\n")
    (cwd / "configs").mkdir()


def test_cli_loop(tmp_path):
    _shape(tmp_path)
    proc = run_cli("init", "--root", str(tmp_path), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    proc = run_cli(
        "record",
        "--category",
        "execute",
        "--step",
        "fit",
        "--inputs",
        "configs/run.json",
        "--root",
        str(tmp_path),
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "A0001"
    proc = run_cli(
        "promote", "A0001", "--criteria", "empirical",
        "--label", "fit", "--purpose", "validate",
        "--relevant-files", "configs/run.json", "--locked", "N",
        "--root", str(tmp_path), cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    proc = run_cli("research", "why gaps", "--root", str(tmp_path), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    from datetime import datetime, timezone

    day = datetime.now(timezone.utc).date().isoformat()
    research = tmp_path / "docs" / "research" / "why-gaps" / f"{day}-synthesis.md"
    assert research.is_file()
    assert not (tmp_path / "docs" / "research" / "why-gaps.md").exists()
    draft = tmp_path / "draft.md"
    draft.write_text("# why body-file\n\n## Finding\n\nit works\n")
    proc = run_cli(
        "research",
        "why body-file",
        "--topic",
        "why-gaps",
        "--name",
        "body-file",
        "--body-file",
        str(draft),
        "--root",
        str(tmp_path),
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    filled = tmp_path / "docs" / "research" / "why-gaps" / f"{day}-body-file.md"
    assert "it works" in filled.read_text()
    readme = (tmp_path / "docs" / "decisioning" / "README.md").read_text()
    assert "A0001" in readme and "empirical" in readme
    assert "why-gaps" in readme
    proc = run_cli(
        "exec",
        "--category",
        "acquire",
        "--step",
        "echo",
        "--root",
        str(tmp_path),
        "--",
        sys.executable,
        "-c",
        "print('ok')",
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("A0004")


def test_cli_refuses_uninitialized(tmp_path):
    _shape(tmp_path)
    proc = run_cli(
        "record", "--category", "execute", "--step", "x", "--root", str(tmp_path),
        cwd=tmp_path,
    )
    assert proc.returncode == 1
    assert "journal init" in proc.stderr
