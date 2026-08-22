"""Shared fixtures: a governed registry, and one REAL pipeline run.

The run-dir fixture invokes ``python -m dskit.pipeline`` in a fresh
interpreter — no imports cross the boundary in either direction
(ADR-0008), so ingest tests exercise the actual seam: files on disk.
Session-scoped because one run serves every test that reads it.
"""

import os
import pathlib
import subprocess
import sys

import pytest

from dskit.assets import FileStore, Registry, default_model

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MINIMAL_DOC = REPO_ROOT / "examples" / "pipeline" / "nodemap-minimal.json"


@pytest.fixture
def model():
    return default_model()


@pytest.fixture
def registry(tmp_path, model):
    """A registry over a fresh store governed by the default model."""
    return Registry(FileStore.create(str(tmp_path / "store"), model), model)


@pytest.fixture(scope="session")
def run_dir(tmp_path_factory):
    """A completed REAL run of the minimal example document."""
    cwd = tmp_path_factory.mktemp("pipeline")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "dskit.pipeline", "run", str(MINIMAL_DOC),
         "--asof", "2026-01-01"],
        cwd=cwd, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    runs = sorted((cwd / "pipeline_runs").iterdir())
    assert len(runs) == 1, runs
    return str(runs[0])
