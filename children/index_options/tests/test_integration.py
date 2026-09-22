"""Finite, offline tests of the actual CLI, artifacts and isolated journals."""

import copy
import csv
import json
import os
import shutil
import subprocess
import sys

import pytest

from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.driver import resolve_json_artifact, run_document
from dskit.pipeline.planner import plan
from dskit.pipeline.policy import classify_plan
from dskit.production.decider import ServingExecutionPolicy
from dskit.production.records import ExecutionScope
from dskit.production.release import ReleaseManifest, RuntimeFingerprint


def _json_artifacts(root):
    """Locate persisted diagnostic JSON; positive tests must detect a real file."""
    return list(root.glob("**/artifacts/json/*.json"))


def document_for(child_root, store, run_root):
    obj = json.loads((child_root / "configs/run-fixture.json").read_text())
    for key in ("contracts", "quotes", "settlements"):
        obj["pipeline"][key]["params"] = store.node_params()
    obj["outputs"]["run_root"] = str(run_root)
    return PipelineDocument.from_obj(obj)


def cli(child_root, cwd, module, *args, expected=0):
    env = dict(os.environ, PYTHONPATH=str(child_root), DSKIT_JOURNAL_ROOT=str(cwd),
               DSKIT_JOURNAL_TESTS="1")
    done = subprocess.run(
        [sys.executable, "-m", module, *map(str, args)], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=45,
    )
    assert done.returncode == expected, (done.stdout, done.stderr)
    return done.stdout


def initialize_cli_child(child_root, directory):
    directory.mkdir()
    shutil.copytree(child_root / "fixtures", directory / "fixtures")
    shutil.copytree(child_root / "configs", directory / "configs")
    cli(child_root, directory, "dskit.journal", "init", "--root", directory)
    return directory


def actions(directory):
    with (directory / "docs/decisioning/actions.csv").open(newline="") as handle:
        return list(csv.DictReader(handle))


def test_public_cli_round_trip_and_positive_journal_isolation(child_root, tmp_path):
    first = initialize_cli_child(child_root, tmp_path / "first")
    second = initialize_cli_child(child_root, tmp_path / "second")
    paths = {p: (p / "docs/decisioning/path.csv").read_bytes() for p in (first, second)}
    child_path = (child_root / "docs/decisioning/path.csv").read_bytes()
    child_actions = (child_root / "docs/decisioning/actions.csv").read_bytes()
    first_ledger = None
    for directory in (first, second):
        cli(child_root, directory, "dskit.onboarding", "init", "--root", "./ob")
        cli(child_root, directory, "dskit.onboarding", "register-source", "index-fixture",
            "--catalog-source", "index-fixture-src", "--connector", "localfiles",
            "--config", "@configs/source-fixture.json", "--activate", "--root", "./ob")
        for stream, count in (("contracts", 4), ("quotes", 4), ("settlements", 3)):
            acquired = json.loads(cli(
                child_root, directory, "dskit.onboarding", "acquire", "--source", "index-fixture",
                "--stream", stream, "--mode", "backfill", "--root", "./ob",
            ))
            assert acquired["records"] == count
            verdict = json.loads(cli(
                child_root, directory, "dskit.onboarding", "validate",
                "--suite", "configs/suite-fixture.json", "--snapshot", acquired["snapshot"],
                "--root", "./ob",
            ))
            assert verdict["gating"] == "pass"
            assert verdict["statistics"]["rows"][stream] == count
        cli(child_root, directory, "dskit.pipeline", "run", "configs/run-fixture.json",
            "--asof", "2026-02-21")
        node_records = list((directory / "pipeline_runs").glob("**/nodes/*-diagnostic.json"))
        assert len(node_records) == 1
        record = json.loads(node_records[0].read_text())
        assert record["status"] == "ok"
        report = resolve_json_artifact(str(node_records[0].parent.parent), record["outputs"]["report"])
        assert report["net_pnl_usd"] == "252"
        assert report["kind"] == "synthetic_ex_post_diagnostic"
        assert report["decision_eligible"] is False
        recorded = actions(directory)
        assert sum(row["category"] == "acquire" for row in recorded) >= 6
        assert sum(row["category"] == "execute" for row in recorded) == 1
        assert (directory / "docs/decisioning/path.csv").read_bytes() == paths[directory]
        if directory == first:
            first_ledger = (first / "docs/decisioning/actions.csv").read_bytes()
            assert actions(second) == []
            assert not (second / "ob").exists()
            assert not (second / "pipeline_runs").exists()
        else:
            assert (first / "docs/decisioning/actions.csv").read_bytes() == first_ledger
    assert (child_root / "docs/decisioning/path.csv").read_bytes() == child_path
    assert (child_root / "docs/decisioning/actions.csv").read_bytes() == child_actions


def test_public_api_persists_digest_checked_report(rows, child_root, store_factory, tmp_path):
    store = store_factory(rows)
    for stream in rows:
        store.acquire(stream)
    result = run_document(document_for(child_root, store, tmp_path / "runs"),
                          asof="2026-02-21", journal=False)
    assert result.state == "ran"
    report = resolve_json_artifact(result.run_dir, result.outputs["diagnostic"]["report"])
    assert report["gross_pnl_usd"] == "260"
    assert report["net_pnl_usd"] == "252"
    assert report["max_loss_after_fees_usd"] == "248"
    assert len(_json_artifacts(tmp_path / "runs")) == 1


@pytest.mark.parametrize("fault", [
    "missing_stream", "corrupt_jsonl", "winning_tie", "bad_domain",
    "zero_multiplier", "negative_multiplier", "bool_multiplier", "post_expiry",
])
def test_failed_api_never_completes_diagnostic(rows, child_root, store_factory, tmp_path, fault):
    if fault == "winning_tie":
        rows["quotes"].append({**rows["quotes"][0], "ask": "2.61"})
    elif fault == "bad_domain":
        rows["quotes"][0]["provenance"] = "real"
    elif fault.endswith("multiplier"):
        rows["contracts"][0]["multiplier"] = {
            "zero_multiplier": 0, "negative_multiplier": -100, "bool_multiplier": True,
        }[fault]
    elif fault == "post_expiry":
        for row in rows["quotes"]:
            row["quote_at"] = row["effective_at"] = "2026-02-21T00:00:00Z"
    store = store_factory(rows)
    for stream in rows:
        if fault == "missing_stream" and stream == "quotes":
            continue
        store.acquire(stream)
    if fault == "corrupt_jsonl":
        store.members("quotes")[0].write_text("{broken\n")
    run_root = tmp_path / "runs"
    try:
        result = run_document(document_for(child_root, store, run_root),
                              asof="2026-02-21", journal=False)
    except ValueError:
        pass  # early RESOLVE refusal is before a run directory
    else:
        assert result.state != "ran"
        assert "diagnostic" not in result.outputs or "report" not in result.outputs["diagnostic"]
    for path in run_root.glob("**/nodes/*-diagnostic.json"):
        assert json.loads(path.read_text())["status"] != "ok"
    assert not _json_artifacts(run_root)


@pytest.mark.parametrize("multiplier", [0, -1, True])
def test_cli_refuses_bad_multiplier_without_success_artifact(child_root, tmp_path, multiplier):
    directory = initialize_cli_child(child_root, tmp_path / "invalid")
    path = directory / "fixtures/contracts.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["multiplier"] = multiplier
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    cli(child_root, directory, "dskit.onboarding", "init", "--root", "./ob")
    cli(child_root, directory, "dskit.onboarding", "register-source", "index-fixture",
        "--catalog-source", "index-fixture-src", "--connector", "localfiles",
        "--config", "@configs/source-fixture.json", "--activate", "--root", "./ob")
    for stream in ("contracts", "quotes", "settlements"):
        cli(child_root, directory, "dskit.onboarding", "acquire", "--source", "index-fixture",
            "--stream", stream, "--mode", "backfill", "--root", "./ob")
    output = cli(child_root, directory, "dskit.pipeline", "run", "configs/run-fixture.json",
                 "--asof", "2026-02-21", expected=1)
    assert "multiplier" in output
    assert not _json_artifacts(directory / "pipeline_runs")


def test_new_corpus_changes_run_input_identity(rows, child_root, store_factory, tmp_path):
    original = store_factory(rows)
    revised = copy.deepcopy(rows)
    for stream in revised.values():
        for row in stream:
            row["corpus_id"] = "condor-demo-v2"
    revised["quotes"][0]["ask"] = "2.61"
    changed = store_factory(revised, "changed")
    results = []
    for store, corpus in ((original, "condor-demo-v1"), (changed, "condor-demo-v2")):
        for stream in rows:
            store.acquire(stream)
        obj = document_for(child_root, store, tmp_path / corpus).to_obj()
        obj["pipeline"]["diagnostic"]["params"]["corpus_id"] = corpus
        results.append(run_document(PipelineDocument.from_obj(obj), asof="2026-02-21", journal=False))
    assert all(result.state == "ran" for result in results)
    assert results[0].run_hash != results[1].run_hash
    reports = [resolve_json_artifact(r.run_dir, r.outputs["diagnostic"]["report"]) for r in results]
    assert [r["net_pnl_usd"] for r in reports] == ["252", "251"]


def test_public_serving_classification_forbids_every_child_node(child_root, tmp_path):
    document = PipelineDocument.from_obj(json.loads(
        (child_root / "configs/run-fixture.json").read_text()
    ))
    # Real public classifier, no constructors/scans and no invented serving adapter.
    manifest = ReleaseManifest(
        series_id="018f0f4e-7b21-7d3a-9c31-6d8f36d806a1",
        doc_hash="c1" * 32, run_hash="d2" * 32, serving_hash="e3" * 32,
        artifacts={}, classes={}, adapter={"name": "index_options", "digest": "b6" * 32},
        feed_spec={
            "source_binding": {"source": "index-fixture", "connector": "localfiles"},
            "entity_key_fields": ["contract_id"], "event_time_field": "observation_ms",
            "digest_recipe": {"kind": "stream-digest"}, "required_keys": ["p480"],
            "required_keys_digest": "7a" * 32, "source_config_hash": "5b" * 32,
            "source_config_version": "3",
        },
        source_config={"hash": "5b" * 32, "version": "3"},
        execution_scope=ExecutionScope(venue="synthetic", account="research"),
        approval_fingerprint="c7" * 32, lease_fingerprint="d8" * 32,
        checklist_digest="e9" * 32, runtime_fingerprint=RuntimeFingerprint.capture(), created_ms=1,
    )
    policy = ServingExecutionPolicy("contracts", release=manifest, root=str(tmp_path))
    effects = classify_plan(plan(document), policy, {})
    assert effects == {key: "forbidden" for key in ("contracts", "quotes", "settlements", "diagnostic")}
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize("reference_at", [
    "2026-01-16T20:45:00Z", "2026-01-16T15:45:00-05:00", "2026-01-16T20:45:00+00:00",
])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_api_quote_instant_reference_identity(rows, child_root, store_factory, tmp_path, reference_at, ambiguous):
    from index_options.observations import QuoteRows

    if ambiguous:
        rows["quotes"].append({
            **rows["quotes"][0], "effective_at": "2026-01-16T15:45:00-05:00", "ask": "2.61",
        })
    else:
        rows["quotes"][0]["quote_at"] = reference_at
    store = store_factory(rows)
    for stream in rows:
        store.acquire(stream)
    assert len(QuoteRows("q", store.node_params()).run(None, {})["records"]) == (5 if ambiguous else 4)
    obj = document_for(child_root, store, tmp_path / "runs").to_obj()
    obj["pipeline"]["diagnostic"]["params"]["legs"][0]["quote_at"] = reference_at
    result = run_document(PipelineDocument.from_obj(obj), asof="2026-02-21", journal=False)
    if ambiguous:
        assert result.state != "ran"
        assert not _json_artifacts(tmp_path / "runs")
    else:
        assert result.state == "ran"
        assert resolve_json_artifact(
            result.run_dir, result.outputs["diagnostic"]["report"]
        )["net_pnl_usd"] == "252"


@pytest.mark.parametrize("reference_at", ["2026-01-16T20:45:00Z", "2026-01-16T15:45:00-05:00"])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_cli_quote_instant_reference_identity(child_root, tmp_path, reference_at, ambiguous):
    directory = initialize_cli_child(child_root, tmp_path / "time-case")
    quote_path = directory / "fixtures/quotes.jsonl"
    quotes = [json.loads(line) for line in quote_path.read_text().splitlines()]
    if ambiguous:
        quotes.append({
            **quotes[0], "effective_at": "2026-01-16T15:45:00-05:00", "ask": "2.61",
        })
    else:
        quotes[0]["quote_at"] = reference_at
    quote_path.write_text("".join(json.dumps(row) + "\n" for row in quotes))
    config_path = directory / "configs/run-fixture.json"
    obj = json.loads(config_path.read_text())
    obj["pipeline"]["diagnostic"]["params"]["legs"][0]["quote_at"] = reference_at
    config_path.write_text(json.dumps(obj))
    cli(child_root, directory, "dskit.onboarding", "init", "--root", "./ob")
    cli(child_root, directory, "dskit.onboarding", "register-source", "index-fixture",
        "--catalog-source", "index-fixture-src", "--connector", "localfiles",
        "--config", "@configs/source-fixture.json", "--activate", "--root", "./ob")
    for stream in ("contracts", "quotes", "settlements"):
        cli(child_root, directory, "dskit.onboarding", "acquire", "--source", "index-fixture",
            "--stream", stream, "--mode", "backfill", "--root", "./ob")
    cli(child_root, directory, "dskit.pipeline", "run", "configs/run-fixture.json",
        "--asof", "2026-02-21", expected=1 if ambiguous else 0)
    artifacts = _json_artifacts(directory / "pipeline_runs")
    if ambiguous:
        assert artifacts == []
    else:
        assert len(artifacts) == 1
        assert json.loads(artifacts[0].read_text())["net_pnl_usd"] == "252"


@pytest.mark.parametrize("fault", ["size", "quote_clock", "settlement_clock"])
def test_api_isolates_size_and_own_clock_guards(rows, child_root, store_factory, tmp_path, fault):
    if fault == "size":
        rows["quotes"][0]["ask_size"] = 1
    elif fault == "quote_clock":
        rows["quotes"][0]["effective_at"] = "2026-01-15T20:45:00Z"
    else:
        rows["settlements"][0]["effective_at"] = "2026-02-19T21:00:00Z"
    store = store_factory(rows)
    for stream in rows:
        store.acquire(stream)
    obj = document_for(child_root, store, tmp_path / "runs").to_obj()
    if fault == "size":
        obj["pipeline"]["diagnostic"]["params"]["count"] = 2
    try:
        result = run_document(PipelineDocument.from_obj(obj), asof="2026-02-21", journal=False)
    except ValueError as exc:
        assert "must equal effective_at" in str(exc)
    else:
        assert result.state != "ran"
    assert not _json_artifacts(tmp_path / "runs")
