"""The success criterion: ONE document runs the stat test, trains the
transformer and builds the MIO, end to end.

The document under test is the SHIPPED ``configs/run-e2e.json`` — not a
copy written for the test. The only edits are where the data lives
(``root``/``source`` on the two reader nodes), so the wiring, the knobs
and the splits that ship are exactly what is exercised here. The world
is the synthetic ladder corpus acquired through the onboarding platform,
the same way real data enters.
"""

import copy
import json
import os
from dataclasses import replace

import pytest
from dskit.pipeline.document import OutputsConfig, load_document
from dskit.pipeline.driver import run_document

import pmquant  # noqa: F401 — import = registration
from pmquant.testing import SyntheticLadderWorld, acquire_synthetic

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCUMENT = os.path.join(CHILD_ROOT, "configs", "run-e2e.json")

#: A world deep enough for trailing splits (10 test + 8 cal + 12 val days
#: leaves ~50 training events per series) and small enough for the child
#: suite's subprocess budget. Two Kalshi series, no Polymarket, so the
#: document's declared venue owns every series in the stream.
WORLD = {
    "seed": 11,
    "series": ["KXSYNA", "KXSYNB"],
    "events_per_series": 80,
    "rungs": 3,
    "start_date": "2026-01-05",
}

#: The reader nodes whose ``root`` the test relocates — the ONLY edit. The
#: shipped document already names the synthetic source alias.
READER_NODES = ("ladder_records", "settlements")
SOURCE = "synthetic"


@pytest.fixture(scope="module")
def acquired(tmp_path_factory):
    root, _registry, source = acquire_synthetic(
        str(tmp_path_factory.mktemp("e2e") / "ob"), WORLD, source_name=SOURCE
    )
    return SyntheticLadderWorld(**WORLD), root.root, source


def _relocated_document(root, source, run_root):
    """The shipped document with only the readers' ``root`` pointed at ``root``."""
    with open(DOCUMENT, encoding="utf-8") as fh:
        raw = json.load(fh)
    doc = copy.deepcopy(raw)
    for key in READER_NODES:
        assert doc["pipeline"][key]["params"]["source"] == source, key
        doc["pipeline"][key]["params"]["root"] = root
    path = os.path.join(run_root, "run-e2e.relocated.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    document = load_document(path)
    return replace(document, outputs=OutputsConfig(run_root=os.path.join(run_root, "runs")))


def test_the_relocation_touches_only_where_the_data_lives(acquired, tmp_path):
    _world, root, source = acquired
    with open(DOCUMENT, encoding="utf-8") as fh:
        shipped = json.load(fh)
    relocated = _relocated_document(root, source, str(tmp_path))
    for key, spec in shipped["pipeline"].items():
        params = dict(relocated.pipeline[key].params)
        expected = dict(spec.get("params", {}))
        if key in READER_NODES:
            expected["root"] = root
        assert params == expected, key
        assert relocated.pipeline[key].uses == spec["uses"], key


def test_one_document_runs_stat_test_transformer_and_mio(acquired, tmp_path):
    world, root, source = acquired
    document = _relocated_document(root, source, str(tmp_path))
    result = run_document(document, asof="2026-04-01")
    assert result.state == "ran", (result.state, result.error)

    outputs = result.outputs
    # -- the banking spine admitted the family the world settled -----------
    assert outputs["eligible_family"]["verdict"] == "GO"
    assert outputs["eligible_family"]["instruments"] == sorted(WORLD["series"])

    # -- the transformer trained (two seeds), was selected on val, and
    #    scored the cal band and the test block through the ensemble ------
    for seed in ("seed_0", "seed_1"):
        metrics = outputs[seed]["metrics"]
        assert os.path.isfile(outputs[seed]["artifact_path"]), seed
        assert metrics["epochs_run"] == 8 and metrics["selected_epoch"] >= 1, (seed, metrics)
        assert metrics["monitor"] == "claims_val_event_ll" and metrics["monitor_value"] > 0
    for key in ("ens_cal", "ens_test"):
        # Spent record streams are RELEASED after their last reader (ADR-0048),
        # so the frame itself is summarized here; the metrics are the record.
        assert outputs[key]["metrics"]["n_members"] == 2, key
        assert outputs[key]["metrics"]["n_cells"] > 0, key

    # -- the D-138 gate ran on the CAL band: per-series event deltas vs the
    #    market null, then the owned studentized bootstrap with BH ----------
    validation = outputs["validate"]
    assert validation["evidence"]["split"] == "cal"
    assert validation["metrics"]["beats_baseline"] is True
    assert validation["metrics"]["loss"] < validation["metrics"]["baseline_loss"]
    scores = validation["cluster_scores"]
    assert set(scores) == set(WORLD["series"])
    assert all(len(clusters) >= 5 for clusters in scores.values()), {
        k: len(v) for k, v in scores.items()
    }
    edge = outputs["edge_test"]
    assert set(edge["pvalues"]) == set(WORLD["series"])
    assert all(0.0 <= p <= 1.0 for p in edge["pvalues"].values())
    # The world is BUILT mispriced (asks shrunk halfway to uniform), and the
    # recipe un-shrinks them with a margin of two orders of magnitude, so
    # the verdict is GO for both series — anything else is a regression.
    assert edge["verdict"] == "GO"
    assert edge["survivors"] == sorted(WORLD["series"])
    assert edge["evidence"]["totals"]["correction"] == "bh"
    assert edge["evidence"]["totals"]["method"] == "studentized"

    # -- the MIO built and solved on the TEST block: every survivor's newest
    #    usable epochs were priced, gated and sized to integer lots --------
    sizing = outputs["size"]
    totals = sizing["evidence"]["totals"]
    assert sizing["evidence"]["split"] == "test"
    assert totals["n_candidates"] > 0 and totals["n_priced"] == totals["n_candidates"]
    assert sizing["lots"] > 0 and sizing["lots"] == sum(sizing["positions"].values())
    assert 0.0 < sizing["outlay"] <= totals["budget"] + 1e-9
    assert totals["budget"] == pytest.approx(10000 * 0.25)
    assert sizing["metrics"]["n_events"] > 0
    assert sizing["metrics"]["expected_log_growth"] > 0
    assert all(key.split("|")[1] in ("yes", "no") for key in sizing["positions"])

    # -- the run evaluator saw every stage and wrote the record ------------
    assert outputs["run_report"]["summary"]["stages"] >= 4
    assert outputs["run_report"]["summary"]["loud"] == 0  # survivors AND lots
    run_dir = result.run_dir
    for artifact in (
        os.path.join("artifacts", "size", "sizing.json"),
        os.path.join("artifacts", "run_report", "evidence.json"),
        os.path.join("artifacts", "run_report", "evidence.md"),
        os.path.join("artifacts", "banking_report", "banking.json"),
        "result.json",
        "report.md",
    ):
        assert os.path.isfile(os.path.join(run_dir, artifact)), artifact
    with open(outputs["run_report"]["path"], encoding="utf-8") as fh:
        evidence = json.load(fh)
    assert {"validation", "edge", "sizing"} <= set(evidence["stages"]), sorted(evidence["stages"])
    with open(os.path.join(run_dir, "report.md"), encoding="utf-8") as fh:
        report = fh.read()
    assert "RAN" in report and "| size | capital | ok |" in report
