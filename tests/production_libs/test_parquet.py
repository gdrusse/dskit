"""`libs/parquet.py` — the `run` reference over a run's predictions (§5.10.2).

The pack exists so a drift monitor compares live decisions against exactly
the distribution the model was validated on, rather than against an
operator's saved profile. Three refusals carry that promise and each is
asserted here rather than described:

* **No I/O when it is built.** `Snapshot`'s precedent (§5.10.2): a document
  is validated on machines that hold no run directory, so a reference that
  read at construction would make `validate` need the artifact. Every test
  that proves this deletes the file BETWEEN construction and `sample()`.
* **`add()` ignores.** A run reference is a fixed anchor. `Monitor` retires
  every observation LEAVING the window into every reference unconditionally,
  so refusing there would crash the serve loop on the first window roll
  rather than protect anything; declining to incorporate — `Snapshot`'s
  contract for a fixed population — is what keeps the anchor still, and the
  test that matters asserts the population is unchanged by a roll.
* **An ambiguous run refuses.** Two scoring nodes and no `node` named is two
  distributions that may not predict the same thing; a statistic over that
  mixture would mean nothing and nothing would say so.
* **`fingerprint()` is the file's digest**, so `plan` binds it into the
  release like any other artifact and a re-scored run cannot move a live
  alarm threshold under a fixed release hash.

Nothing here reads back a digest the code produced: the fingerprint is
asserted by what MOVES it (the predictions) and what must not (anything
else in the run directory).
"""

import json
import os

import pytest

pa = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq

from dskit.pipeline.predictions import PREDICTIONS_FILE
from dskit.production.base import ProductionError
from dskit.production.libs.parquet import DEFAULT_REFERENCE_MAX_ROWS, RunReference
from dskit.production.monitors import MONITOR_KINDS, REFERENCE_KINDS, Reference

# ---------------------------------------------------------------------------
# A run directory, in the `dskit.pipeline.predictions` layout
# ---------------------------------------------------------------------------


def write_run(root, values, node="score", column="yhat", extra=None):
    """Write one node's predictions file under `<root>/artifacts/<node>/`."""
    directory = os.path.join(str(root), "artifacts", node)
    os.makedirs(directory, exist_ok=True)
    columns = {column: list(values), "series": ["A"] * len(values)}
    columns.update(extra or {})
    pq.write_table(pa.table(columns), os.path.join(directory, PREDICTIONS_FILE))
    return str(root)


@pytest.fixture
def run_dir(tmp_path):
    """A run whose one score node saved ten predictions."""
    return write_run(tmp_path / "run", [float(v) for v in range(10)])


def predictions_path(run_dir, node="score"):
    return os.path.join(run_dir, "artifacts", node, PREDICTIONS_FILE)


def reference(run_dir, **params):
    """The reference a monitor would build for the field `yhat`."""
    return RunReference({"run_dir": run_dir, **params}, field="yhat")


# ---------------------------------------------------------------------------
# The registration §4.3 calls import
# ---------------------------------------------------------------------------


def test_the_pack_registers_run_into_the_reference_family():
    assert REFERENCE_KINDS.resolve("run") is RunReference
    assert "run" in REFERENCE_KINDS
    assert issubclass(RunReference, Reference)
    assert REFERENCE_KINDS.family == "reference"


def test_a_document_selects_it_through_the_ordinary_uses_site(run_dir):
    monitor = MONITOR_KINDS.resolve("psi")(
        {
            "field": "yhat",
            "bins": 2,
            "reference": {"uses": "run", "params": {"run_dir": run_dir}},
            "window": {"kind": "count", "n": 4},
            "threshold": {"kind": "constant", "max": 1e9},
            "min_n": 4,
        },
        name="pred_shift",
    )
    for value in (0.0, 1.0, 2.0, 3.0):
        monitor.observe({"kind": "decision", "legs": [{"yhat": value}]})
    verdict = monitor.verdict()
    assert verdict.n_ref == 10
    assert verdict.status == "ok"


# ---------------------------------------------------------------------------
# Construction — default-deny, and no I/O
# ---------------------------------------------------------------------------


def test_the_params_are_exactly_the_four_of_the_plan():
    assert set(RunReference._PARAMS) == {"run_dir", "node", "column", "max_rows"}


def test_a_node_that_is_not_a_name_refuses(run_dir):
    with pytest.raises(ProductionError) as exc:
        reference(run_dir, node=3)
    assert "node" in str(exc.value)


def test_an_unknown_param_refuses_naming_it(run_dir):
    with pytest.raises(ProductionError) as exc:
        reference(run_dir, colunm="yhat")
    assert "colunm" in str(exc.value)


def test_notes_are_allowed_beside_the_knobs(run_dir):
    reference(run_dir, notes="the anchor is the run this release was cut from")


def test_run_dir_is_required():
    with pytest.raises(ProductionError) as exc:
        RunReference({}, field="yhat")
    assert "run_dir" in str(exc.value)


def test_a_non_positive_max_rows_refuses(run_dir):
    for bad in (0, -1):
        with pytest.raises(ProductionError) as exc:
            reference(run_dir, max_rows=bad)
        assert "max_rows" in str(exc.value)


def test_it_refuses_at_construction_with_neither_a_column_nor_a_field(run_dir):
    with pytest.raises(ProductionError) as exc:
        RunReference({"run_dir": run_dir})
    assert "column" in str(exc.value)


def test_constructing_it_reads_nothing(run_dir):
    """§5.10.2: "a missing file, missing column or non-numeric column refuses
    there and not at construction, following `Snapshot`'s precedent that a
    reference performs no I/O when it is built"."""
    built = reference(run_dir)
    os.remove(predictions_path(run_dir))
    # had it read when it was built, it would still be able to answer
    with pytest.raises(ProductionError) as exc:
        built.sample()
    assert PREDICTIONS_FILE in str(exc.value)


def test_a_reference_over_a_directory_that_does_not_exist_still_builds(tmp_path):
    built = RunReference({"run_dir": str(tmp_path / "nowhere")}, field="yhat")
    with pytest.raises(ProductionError) as exc:
        built.sample()
    assert "nowhere" in str(exc.value)


# ---------------------------------------------------------------------------
# sample() — read once, lazily
# ---------------------------------------------------------------------------


def test_sample_reads_the_column_the_owning_monitor_named(run_dir):
    assert reference(run_dir).sample() == tuple(float(v) for v in range(10))


def test_an_explicit_column_overrides_the_monitors_field(tmp_path):
    root = write_run(tmp_path / "run", [1.0] * 4, column="yhat", extra={"y": [9.0] * 4})
    assert RunReference({"run_dir": root, "column": "y"}, field="yhat").sample() == (
        9.0,
        9.0,
        9.0,
        9.0,
    )


def test_it_reads_once_and_never_again(run_dir):
    built = reference(run_dir)
    first = built.sample()
    os.remove(predictions_path(run_dir))
    assert built.sample() == first


def test_a_missing_column_refuses_at_sample_naming_it(run_dir):
    built = RunReference({"run_dir": run_dir, "column": "prediction"}, field="yhat")
    with pytest.raises(ProductionError) as exc:
        built.sample()
    assert "prediction" in str(exc.value)


def test_a_non_numeric_column_refuses_at_sample_naming_it(tmp_path):
    root = write_run(tmp_path / "run", [1.0] * 4)
    built = RunReference({"run_dir": root, "column": "series"}, field="yhat")
    with pytest.raises(ProductionError) as exc:
        built.sample()
    assert "series" in str(exc.value)


def test_a_run_that_saved_no_predictions_refuses_at_sample(tmp_path):
    root = str(tmp_path / "run")
    os.makedirs(os.path.join(root, "artifacts", "score"))
    built = RunReference({"run_dir": root}, field="yhat")
    with pytest.raises(ProductionError) as exc:
        built.sample()
    assert PREDICTIONS_FILE in str(exc.value)


def test_one_scoring_node_needs_no_node_param(run_dir):
    assert len(reference(run_dir).sample()) == 10


def test_two_scoring_nodes_and_no_node_named_refuses_naming_both(tmp_path):
    """Concatenating two nodes' rows would mix distributions that may not
    predict the same thing, and no statistic over that mixture means
    anything. Fail closed, and say which nodes it found."""
    root = tmp_path / "run"
    write_run(root, [1.0, 2.0], node="score_a")
    write_run(root, [3.0, 4.0], node="score_b")
    with pytest.raises(ProductionError) as exc:
        reference(str(root)).sample()
    assert "score_a" in str(exc.value)
    assert "score_b" in str(exc.value)


def test_the_node_param_selects_one_scoring_nodes_predictions(tmp_path):
    root = tmp_path / "run"
    write_run(root, [1.0, 2.0], node="score_a")
    write_run(root, [3.0, 4.0], node="score_b")
    assert reference(str(root), node="score_b").sample() == (3.0, 4.0)
    assert reference(str(root), node="score_a").sample() == (1.0, 2.0)


def test_a_node_that_saved_no_predictions_refuses_naming_it(tmp_path):
    root = write_run(tmp_path / "run", [1.0, 2.0], node="score_a")
    with pytest.raises(ProductionError) as exc:
        reference(root, node="scoring").sample()
    assert "scoring" in str(exc.value)
    assert "score_a" in str(exc.value)


# ---------------------------------------------------------------------------
# max_rows — a cap that still stands for the whole file
# ---------------------------------------------------------------------------


def test_max_rows_spreads_its_sample_across_the_file_rather_than_taking_its_head(tmp_path):
    """The head of a predictions file is one series' block, so a head
    truncation would make the anchor stand for one instrument. The sample is
    evenly spaced instead, and is deterministic."""
    root = write_run(tmp_path / "run", [float(v) for v in range(100)])
    assert reference(root, max_rows=10).sample() == tuple(float(v) for v in range(0, 100, 10))


def test_a_file_shorter_than_max_rows_is_taken_whole(run_dir):
    assert len(reference(run_dir, max_rows=1_000).sample()) == 10


def test_the_default_cap_is_a_named_constant_the_reference_reads(tmp_path):
    assert isinstance(DEFAULT_REFERENCE_MAX_ROWS, int)
    assert DEFAULT_REFERENCE_MAX_ROWS >= 1
    root = write_run(tmp_path / "run", [float(v) for v in range(20)])
    bare = reference(root).sample()
    spelled = reference(root, max_rows=DEFAULT_REFERENCE_MAX_ROWS).sample()
    assert bare == spelled


# ---------------------------------------------------------------------------
# add() — the refusal that keeps the anchor fixed
# ---------------------------------------------------------------------------


def test_add_ignores_the_value_so_the_anchor_never_moves(run_dir):
    """Declining to incorporate is not accepting: the anchor is exactly what
    the run scored, before and after the loop offers it anything."""
    built = reference(run_dir)
    before = built.sample()
    assert built.add(1.0) is None
    built.add(99.0)
    assert built.sample() == before == tuple(float(v) for v in range(10))


def test_fit_ignores_the_records_it_is_offered_rather_than_refusing(run_dir):
    """`Monitor.fit` offers every training value to every reference, so a
    run reference that refused there could not be fitted at all. The run's
    own rows are the population; the offered values are simply not it."""
    built = reference(run_dir)
    built.fit([1.0, 2.0, 3.0])
    assert built.sample() == tuple(float(v) for v in range(10))


def test_a_rolling_window_never_moves_the_anchor(run_dir):
    """`Monitor` retires every observation LEAVING the window into every
    reference, unconditionally — which is why `add()` ignores rather than
    refuses. The roll must not raise, and (the assertion that matters) the
    population must be identical afterwards: a run reference that absorbed
    those values would drift with the thing it measures drift against."""
    monitor = MONITOR_KINDS.resolve("psi")(
        {
            "field": "yhat",
            "bins": 2,
            "reference": {"uses": "run", "params": {"run_dir": run_dir}},
            "window": {"kind": "count", "n": 2},
            "threshold": {"kind": "constant", "max": 1e9},
            "min_n": 2,
        },
        name="pred_shift",
    )
    for value in (0.0, 1.0):
        monitor.observe({"kind": "decision", "legs": [{"yhat": value}]})
    anchor = json.dumps(monitor.state()["references"][0])

    for value in (2.0, 3.0, 4.0, 5.0):  # every one of these rolls the window
        monitor.observe({"kind": "decision", "legs": [{"yhat": value}]})
    assert json.dumps(monitor.state()["references"][0]) == anchor
    assert monitor.verdict().n_ref == 10


# ---------------------------------------------------------------------------
# fingerprint() — what `plan` binds into the release
# ---------------------------------------------------------------------------


def test_the_fingerprint_is_a_digest_and_is_stable(run_dir):
    built = reference(run_dir)
    first = built.fingerprint()
    assert len(first) == 64
    assert set(first) <= set("0123456789abcdef")
    assert built.fingerprint() == first


def test_the_fingerprint_moves_when_the_predictions_do(tmp_path):
    root = write_run(tmp_path / "run", [1.0, 2.0, 3.0])
    before = reference(root).fingerprint()
    write_run(tmp_path / "run", [1.0, 2.0, 4.0])
    assert reference(root).fingerprint() != before


def test_the_fingerprint_ignores_everything_else_in_the_run_directory(tmp_path):
    root = write_run(tmp_path / "run", [1.0, 2.0, 3.0])
    before = reference(root).fingerprint()
    with open(os.path.join(root, "run.json"), "w", encoding="utf-8") as handle:
        json.dump({"unrelated": True}, handle)
    assert reference(root).fingerprint() == before


def test_two_runs_with_the_same_rows_in_different_nodes_fingerprint_differently(tmp_path):
    left = write_run(tmp_path / "left", [1.0, 2.0], node="score")
    right = write_run(tmp_path / "right", [1.0, 2.0], node="scoring")
    assert reference(left).fingerprint() != reference(right).fingerprint()


def test_the_fingerprint_follows_the_node_selection(tmp_path):
    root = tmp_path / "run"
    write_run(root, [1.0, 2.0], node="score_a")
    write_run(root, [1.0, 2.0], node="score_b")
    assert reference(str(root), node="score_a").fingerprint() != reference(
        str(root), node="score_b"
    ).fingerprint()
    with pytest.raises(ProductionError):
        reference(str(root)).fingerprint()


def test_the_fingerprint_of_a_run_that_saved_nothing_refuses(tmp_path):
    built = RunReference({"run_dir": str(tmp_path / "nowhere")}, field="yhat")
    with pytest.raises(ProductionError):
        built.fingerprint()


# ---------------------------------------------------------------------------
# state / restore — the population rides in the §6 snapshot
# ---------------------------------------------------------------------------


def test_state_round_trips_through_json(run_dir):
    built = reference(run_dir)
    built.sample()
    restored = reference(run_dir)
    restored.restore(json.loads(json.dumps(built.state())))
    assert restored.sample() == built.sample()


def test_a_restored_reference_never_rereads_the_file(run_dir):
    """A restart replays the snapshot, not the artifact: the population that
    was folded is the population that comes back, even if the run directory
    has since gone."""
    built = reference(run_dir)
    built.sample()
    saved = json.loads(json.dumps(built.state()))
    os.remove(predictions_path(run_dir))

    restored = reference(run_dir)
    restored.restore(saved)
    assert restored.sample() == tuple(float(v) for v in range(10))
