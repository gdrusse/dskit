"""ingest.py against a REAL run — the drift-catcher of ADR-0008.

The ``run_dir`` fixture executes ``python -m dskit.pipeline`` in a fresh
interpreter, so if the run-dir format the pipeline writes ever drifts
from what ingest reads, THIS file fails — the boundary's only contract
is the directory, and this is where it is enforced.
"""

import pytest

from dskit.assets import AssetError, Lineage, ingest_run


# -- normal ----------------------------------------------------------------


def test_real_run_ingests(registry, run_dir):
    summary = ingest_run(registry, run_dir)
    run = registry.get(summary["run"])
    assert run.payload["status"] == "ran"
    assert run.payload["name"].startswith("nodemap-minimal-2026-01-01-")
    assert len(run.payload["config_hash"]) == 64
    # the minimal document trains one model and reports node outputs
    assert summary["artifacts"] and summary["outputs"]
    assert summary["edges_added"] == len(summary["artifacts"]) + len(summary["outputs"])


def test_everything_descends_from_the_run(registry, run_dir):
    summary = ingest_run(registry, run_dir)
    lin = Lineage(registry)
    assert lin.descendants(summary["run"]) == sorted(
        summary["artifacts"] + summary["outputs"]
    )
    for edge in lin.edges():
        assert edge["phase"] == "execution" and edge["origin"] == "ingest-run"


def test_artifact_identity_anchors_on_bytes(registry, run_dir):
    summary = ingest_run(registry, run_dir)
    artifact = registry.get(summary["artifacts"][0])
    assert len(artifact.payload["digest"]) == 64
    assert artifact.refs["run"] == summary["run"]


# -- edge ------------------------------------------------------------------


def test_reingest_is_a_complete_noop(registry, run_dir):
    first = ingest_run(registry, run_dir)
    events_before = len(list(registry.store.iter_events()))
    second = ingest_run(registry, run_dir)
    assert second == {**first, "edges_added": 0}
    assert len(list(registry.store.iter_events())) == events_before


# -- failure ---------------------------------------------------------------


def test_missing_dir_refused(registry, tmp_path):
    with pytest.raises(AssetError, match="does not exist"):
        ingest_run(registry, str(tmp_path / "nowhere"))


def test_dir_without_result_json_refused(registry, tmp_path):
    with pytest.raises(AssetError, match="result.json"):
        ingest_run(registry, str(tmp_path))


def test_incomplete_result_json_refused(registry, tmp_path):
    (tmp_path / "result.json").write_text('{"name": "x"}')
    with pytest.raises(AssetError) as exc:
        ingest_run(registry, str(tmp_path))
    assert len(exc.value.errors) == 4  # asof, state, document_hash, run_hash
