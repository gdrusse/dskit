"""The CLI, end to end — the full loop in a fresh interpreter, plus the
cross-package handoff: publish into the outbox, sync into a P1 catalog
(ADR-0012 exercised as it will actually run: two CLIs, files between)."""

import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def run_cli(module, *argv, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", module, *argv],
        cwd=cwd, env=env, capture_output=True, text=True,
    )


def test_init_backend_sqlite(tmp_path):
    # ADR-0018: the backend choice reaches the P2 store from the CLI.
    proc = run_cli("dskit.onboarding", "init", "--root", "ob",
                   "--backend", "sqlite", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "ob" / "store" / "store.sqlite").is_file()


@pytest.fixture(scope="module")
def loop(tmp_path_factory):
    """init -> register-source -> acquire, shared by the checks below."""
    cwd = tmp_path_factory.mktemp("cli")
    data = cwd / "data"
    data.mkdir()
    (data / "prices.csv").write_text(
        "date,close\n2026-01-02,10.5\n2026-01-05,11.0\n")
    (cwd / "suite.json").write_text(json.dumps({
        "name": "basic",
        "rules": [{"id": "rows", "target": "prices",
                   "rule": "row_count", "kwargs": {"min": 1}}],
    }))
    (cwd / "blocker.json").write_text(json.dumps({
        "name": "blocker",
        "rules": [{"id": "rows", "target": "prices",
                   "rule": "row_count", "kwargs": {"min": 100}}],
    }))

    proc = run_cli("dskit.onboarding", "init", "--root", "ob", cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    proc = run_cli(
        "dskit.onboarding", "register-source", "vendor", "--root", "ob",
        "--catalog-source", "vendor-src", "--connector", "localfiles",
        "--config", json.dumps({"path": str(data), "effective_field": "date"}),
        "--activate", cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    proc = run_cli("dskit.onboarding", "acquire", "--root", "ob",
                   "--source", "vendor", "--stream", "prices",
                   "--mode", "backfill", cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["records"] == 2
    return cwd, summary


def test_validate_block_exits_3(loop):
    cwd, summary = loop
    proc = run_cli("dskit.onboarding", "validate", "--root", "ob",
                   "--suite", "blocker.json", "--snapshot", summary["snapshot"],
                   cwd=cwd)
    assert proc.returncode == 3, proc.stderr  # a block is a RESULT
    assert json.loads(proc.stdout)["gating"] == "block"


def test_full_loop_through_sync(loop):
    cwd, summary = loop
    proc = run_cli("dskit.onboarding", "validate", "--root", "ob",
                   "--suite", "suite.json", "--snapshot", summary["snapshot"],
                   cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)["result"]

    proc = run_cli("dskit.onboarding", "certify", "--root", "ob",
                   "--result", result, "--decision", "certified",
                   "--by", "gibson", cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    cert = proc.stdout.strip()

    proc = run_cli("dskit.onboarding", "publish", "--root", "ob",
                   "--dataset", "vendor-prices", "--certification", cert,
                   cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    manifest_path = json.loads(proc.stdout)["manifest_path"]
    assert os.path.isfile(manifest_path)

    # The P1 half: a catalog with the dataset, then the outbox scan.
    for argv in (
        ("init", "--store", "catalog"),
        ("register", "source", "--store", "catalog",
         "--payload", '{"name": "vendor-src"}'),
    ):
        proc = run_cli("dskit.assets", *argv, cwd=cwd)
        assert proc.returncode == 0, proc.stderr
    src_vid = proc.stdout.strip()
    proc = run_cli("dskit.assets", "register", "dataset", "--store", "catalog",
                   "--payload", '{"name": "vendor-prices"}',
                   "--ref", f"source={src_vid}", cwd=cwd)
    assert proc.returncode == 0, proc.stderr

    proc = run_cli("dskit.assets", "sync-published", "ob/published",
                   "--store", "catalog", cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    synced = json.loads(proc.stdout)
    assert len(synced["registered"]) == 1 and not synced["failed"]

    # Rescan: anti-entropy is free.
    proc = run_cli("dskit.assets", "sync-published", "ob/published",
                   "--store", "catalog", cwd=cwd)
    synced = json.loads(proc.stdout)
    assert synced["registered"] == [] and synced["existing"] == 1


def test_verify_clean_then_tampered(loop):
    cwd, _summary = loop
    proc = run_cli("dskit.onboarding", "verify", "--root", "ob", cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["problems"] == []

    raw = cwd / "ob" / "raw" / "vendor"
    payload = next(raw.iterdir()) / "payload" / "prices.jsonl"
    payload.write_text(payload.read_text().replace("10.5", "99.9"))
    proc = run_cli("dskit.onboarding", "verify", "--root", "ob", cwd=cwd)
    assert proc.returncode == 1  # tampered WORM storage is an emergency
    assert any("drift" in p for p in json.loads(proc.stdout)["problems"])


def test_errors_exit_1_listing_problems(tmp_path):
    proc = run_cli("dskit.onboarding", "acquire", "--root", "nope",
                   "--source", "x", "--stream", "y", "--mode", "live",
                   cwd=tmp_path)
    assert proc.returncode == 1
    assert "not an initialized onboarding root" in proc.stderr
