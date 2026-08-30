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


# -- the full loop again, compressed (ADR-0036) --------------------------------


@pytest.fixture(scope="module")
def gz_loop(tmp_path_factory):
    """init -> register-source (storage: gzip) -> acquire."""
    cwd = tmp_path_factory.mktemp("cli-gz")
    data = cwd / "data"
    data.mkdir()
    (data / "prices.csv").write_text(
        "date,close\n2026-01-02,10.5\n2026-01-05,11.0\n")
    (cwd / "suite.json").write_text(json.dumps({
        "name": "basic",
        "rules": [{"id": "rows", "target": "prices",
                   "rule": "row_count", "kwargs": {"min": 1}}],
    }))
    proc = run_cli("dskit.onboarding", "init", "--root", "ob", cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    proc = run_cli(
        "dskit.onboarding", "register-source", "vendor", "--root", "ob",
        "--catalog-source", "vendor-src", "--connector", "localfiles",
        "--config", json.dumps({
            "path": str(data), "effective_field": "date",
            "storage": {"payload_codec": "gzip",
                        "observations_codec": "gzip"},
        }),
        "--activate", cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    proc = run_cli("dskit.onboarding", "acquire", "--root", "ob",
                   "--source", "vendor", "--stream", "prices",
                   "--mode", "backfill", cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["records"] == 2
    return cwd, summary


def test_gz_loop_lands_compressed_and_flows_to_publish(gz_loop):
    cwd, summary = gz_loop
    raw = cwd / "ob" / "raw" / "vendor"
    payload_dir = next(raw.iterdir()) / "payload"
    assert (payload_dir / "prices.jsonl.gz").is_file()
    assert not (payload_dir / "prices.jsonl").exists()

    # validate -> certify -> publish are payload-blind: the whole chain
    # runs over the compressed snapshot unchanged.
    proc = run_cli("dskit.onboarding", "validate", "--root", "ob",
                   "--suite", "suite.json", "--snapshot", summary["snapshot"],
                   cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)["result"]
    proc = run_cli("dskit.onboarding", "certify", "--root", "ob",
                   "--result", result, "--decision", "certified",
                   "--by", "gibson", cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    proc = run_cli("dskit.onboarding", "publish", "--root", "ob",
                   "--dataset", "vendor-prices",
                   "--certification", proc.stdout.strip(), cwd=cwd)
    assert proc.returncode == 0, proc.stderr


def test_gz_verify_clean_then_tampered_both_ways(gz_loop):
    cwd, _summary = gz_loop
    proc = run_cli("dskit.onboarding", "verify", "--root", "ob", cwd=cwd)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["problems"] == []

    raw = cwd / "ob" / "raw" / "vendor"
    payload = next(raw.iterdir()) / "payload" / "prices.jsonl.gz"
    original = payload.read_bytes()

    # Twin (a): decompress, edit, recompress in place — content drift.
    import gzip as _gzip

    text = _gzip.decompress(original).decode("utf-8").replace("10.5", "99.9")
    payload.write_bytes(_gzip.compress(text.encode("utf-8")))
    proc = run_cli("dskit.onboarding", "verify", "--root", "ob", cwd=cwd)
    assert proc.returncode == 1
    assert any("drift" in p for p in json.loads(proc.stdout)["problems"])


def test_authorize_cli_delegates_to_connector_oauth_service(
        monkeypatch, capsys):
    from dskit.onboarding import __main__ as cli
    from dskit.onboarding.libs.schwab import SchwabBarsConnector

    events = []

    class _Service:
        def authorization_url(self):
            events.append("url")
            return "https://auth.example.test/start"

        def exchange(self, returned):
            events.append(("exchange", returned))

    monkeypatch.setattr(
        SchwabBarsConnector, "oauth_service",
        lambda self, config: events.append(("config", config)) or _Service(),
    )
    config = json.dumps({"symbols": ["AAPL"], "start": "2026-01-01"})

    assert cli.main([
        "authorize", "--connector", "schwab", "--config", config,
    ]) == 0
    assert capsys.readouterr().out.strip() == "https://auth.example.test/start"
    assert cli.main([
        "authorize", "--connector", "schwab", "--config", config,
        "--code", "https://127.0.0.1?code=abc",
    ]) == 0
    assert capsys.readouterr().out.strip() == "authorized"
    assert ("exchange", "https://127.0.0.1?code=abc") in events


def test_watch_cli_wires_interval_to_recurring_acquisition(
        tmp_path, monkeypatch):
    from dskit.onboarding import OnboardingRoot
    from dskit.onboarding import __main__ as cli

    root = OnboardingRoot.create(str(tmp_path / "watch-root"))
    calls = []
    monkeypatch.setattr(
        cli, "run_watch",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert cli.main([
        "watch", "--root", root.root, "--source", "schwab",
        "--stream", "bars", "--mode", "live", "--every-seconds", "60",
    ]) == 0
    args, kwargs = calls[0]
    assert args[2:6] == ("schwab", "bars", "live", 60.0)
    assert kwargs["origin"] == "cli"
    assert callable(kwargs["on_result"])

    # Twin (b): a blind byte flip — same emergency, no decode needed.
    blob = bytearray(original)
    blob[len(blob) // 2] ^= 0xFF
    payload.write_bytes(bytes(blob))
    proc = run_cli("dskit.onboarding", "verify", "--root", "ob", cwd=cwd)
    assert proc.returncode == 1
    assert any("drift" in p for p in json.loads(proc.stdout)["problems"])
