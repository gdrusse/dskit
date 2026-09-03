"""ADR-0064: a walk saves the rows its summaries were reduced from.

Three things are pinned here. The FILE — seven compact columns, one per
scored validation row, written a block at a time. The ARITHMETIC — the
per-fold mean squared errors a run already reports are recomputable from
the saved rows, so the rows are evidence for the same fold and not a
parallel account of it. And the CONSEQUENCE — a walk whose folds saved
their rows is scored on BOTH halves of ADR-0067's rule, the pooled
Diebold-Mariano t included, which a walk of fold summaries can never be.
"""

import json
import os
import random

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from dskit.pipeline.predictions import (  # noqa: E402
    PREDICTION_COLUMNS,
    PREDICTIONS_FILE,
    PredictionWriter,
    find_predictions,
    read_prediction_series,
    read_predictions,
)
from dskit.pipeline.runs import score_walk  # noqa: E402

SERIES = ("AAA", "BBB")
LEAD = 5
PERIOD = 5


def _node_dir(run_dir):
    """The artifact directory a score node writes into."""
    return os.path.join(run_dir, "artifacts", "scan")


def _fold_block(rng, n, edge):
    """One (series, lead) block: stamps, outcomes, forecasts, benchmark."""
    stamps = [1_600_000_000_000 + i * PERIOD * 60_000 for i in range(n)]
    y = [rng.gauss(0.0, 1.0) for _ in range(n)]
    yhat = [edge * v + rng.gauss(0.0, 0.10) for v in y]
    return stamps, y, yhat, 0.02


def _mspe(y, f):
    """Mean squared error of a forecast against realized outcomes."""
    return sum((a - b) ** 2 for a, b in zip(y, f)) / len(y)


def _write_fold(root, index, cutoff, rng, n=80, edge=0.35, rows=True):
    """One fold run dir: the carry summary, and the rows behind it."""
    run_dir = os.path.join(root, f"wf-{cutoff}")
    os.makedirs(_node_dir(run_dir), exist_ok=True)
    writer = PredictionWriter(
        _node_dir(run_dir), SERIES, fold=index, period_minutes=PERIOD,
        meta={"run": f"walk-wf-{cutoff}"},
    ) if rows else None
    records = []
    for name in SERIES:
        stamps, y, yhat, mu = _fold_block(rng, n, edge)
        records.append({
            "symbol": name,
            "lead": LEAD,
            "mspe_model": _mspe(y, yhat),
            "mspe_mean": _mspe(y, [mu] * len(y)),
            "n": float(len(y)),
            "t_stat": 1.2,
            "p_value": 0.11,
        })
        if writer is not None:
            writer.append(name, LEAD, stamps, y, yhat, mu)
    if writer is not None:
        writer.close()
    with open(os.path.join(run_dir, "carry.json"), "w", encoding="utf-8") as fh:
        json.dump({"scan": {"records": records}}, fh)
    return run_dir


def _write_walk(root, n_folds=12, edge=0.35, rows=True):
    """A walk-forward summary whose folds kept their predictions."""
    rng = random.Random(11)
    summary = os.path.join(root, "walk")
    os.makedirs(summary, exist_ok=True)
    folds = []
    for i in range(n_folds):
        cutoff = f"2024-{i + 1:02d}-01"
        run_dir = _write_fold(root, i, cutoff, rng, edge=edge, rows=rows)
        folds.append({"cutoff": cutoff, "run_dir": run_dir,
                      "state": "ran", "score": 0.0})
    with open(os.path.join(summary, "walkforward.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": "walk", "asof": "2025-11-30", "folds": folds}, fh)
    return summary


class TestWriter:
    def test_the_file_carries_one_row_per_prediction(self, tmp_path):
        with PredictionWriter(str(tmp_path), SERIES, fold=3,
                              period_minutes=PERIOD) as w:
            w.append("AAA", LEAD, [1, 2, 3], [0.1, 0.2, 0.3],
                     [0.11, 0.19, 0.33], 0.05)
            w.append("BBB", LEAD, [1, 2], [0.4, 0.5], [0.41, 0.52], 0.06)
            assert w.n_rows == 5
        table = pq.read_table(os.path.join(str(tmp_path), PREDICTIONS_FILE))
        assert table.num_rows == 5
        assert table.column_names == list(PREDICTION_COLUMNS)
        assert table.column("series").to_pylist() == ["AAA"] * 3 + ["BBB"] * 2
        assert table.column("fold").to_pylist() == [3] * 5
        assert table.column("horizon").to_pylist() == [LEAD] * 5
        assert table.column("mu").to_pylist() == pytest.approx([0.05] * 3 + [0.06] * 2)

    def test_the_dtypes_are_the_compact_ones(self, tmp_path):
        with PredictionWriter(str(tmp_path), SERIES) as w:
            w.append("AAA", 1, [1], [0.5], [0.4], 0.0)
        schema = pq.read_table(os.path.join(str(tmp_path), PREDICTIONS_FILE)).schema
        assert schema.field("ts").type == pa.int64()
        assert schema.field("fold").type == pa.int16()
        assert schema.field("horizon").type == pa.int16()
        for name in ("yhat", "y", "mu"):
            assert schema.field(name).type == pa.float32()
        # The series key is dictionary-encoded, so a name costs one code
        # per row however long the name is.
        assert pa.types.is_dictionary(schema.field("series").type)

    def test_the_row_spacing_is_stamped_not_guessed(self, tmp_path):
        with PredictionWriter(str(tmp_path), SERIES, period_minutes=5) as w:
            w.append("AAA", 20, [1], [0.5], [0.4], 0.0)
        meta = pq.read_table(os.path.join(str(tmp_path), PREDICTIONS_FILE)).schema.metadata
        assert meta[b"period_minutes"] == b"5"

    def test_an_undeclared_series_is_refused(self, tmp_path):
        with PredictionWriter(str(tmp_path), SERIES) as w:
            with pytest.raises(ValueError, match="not declared"):
                w.append("ZZZ", 1, [1], [0.5], [0.4], 0.0)

    def test_a_ragged_block_is_refused(self, tmp_path):
        with PredictionWriter(str(tmp_path), SERIES) as w:
            with pytest.raises(ValueError, match="must agree"):
                w.append("AAA", 1, [1, 2], [0.5], [0.4, 0.3], 0.0)

    def test_a_series_list_must_name_something(self, tmp_path):
        with pytest.raises(ValueError, match="at least one series"):
            PredictionWriter(str(tmp_path), [])

    def test_an_empty_block_writes_nothing(self, tmp_path):
        with PredictionWriter(str(tmp_path), SERIES) as w:
            assert w.append("AAA", 1, [], [], [], 0.0) == 0
            assert w.n_rows == 0

    def test_closing_twice_is_harmless(self, tmp_path):
        w = PredictionWriter(str(tmp_path), SERIES)
        w.append("AAA", 1, [1], [0.5], [0.4], 0.0)
        assert w.close() == w.close() == w.path

    def test_a_row_costs_a_couple_of_dozen_bytes(self, tmp_path):
        """The size budget, guarded. Random floats are the pessimistic
        case — real forecasts repeat and compress harder — and a 20-fold
        walk over five names is this number times ~1.2 million."""
        rng = random.Random(5)
        n = 4000
        with PredictionWriter(str(tmp_path), SERIES, period_minutes=PERIOD) as w:
            for name in SERIES:
                stamps, y, yhat, mu = _fold_block(rng, n, 0.3)
                w.append(name, LEAD, stamps, y, yhat, mu)
        size = os.path.getsize(os.path.join(str(tmp_path), PREDICTIONS_FILE))
        assert size / (2 * n) < 25.0


class TestReader:
    def test_a_run_that_saved_nothing_reads_as_nothing(self, tmp_path):
        assert find_predictions(str(tmp_path)) == []
        assert read_predictions(str(tmp_path)) == {}
        assert read_prediction_series(str(tmp_path)) == []

    def test_the_reader_finds_the_file_under_its_node(self, tmp_path):
        run_dir = str(tmp_path)
        with PredictionWriter(_node_dir(run_dir), SERIES) as w:
            w.append("AAA", 1, [1], [0.5], [0.4], 0.0)
        assert [os.path.basename(p) for p in find_predictions(run_dir)] == [
            PREDICTIONS_FILE
        ]
        assert len(read_predictions(run_dir)["ts"]) == 1

    def test_the_gaps_are_rebuilt_from_the_rows_by_hand(self, tmp_path):
        run_dir = str(tmp_path)
        with PredictionWriter(_node_dir(run_dir), SERIES,
                              period_minutes=PERIOD) as w:
            w.append("AAA", 20, [1, 2], [1.0, 3.0], [1.0, 3.0], 2.0)
        unit = read_prediction_series(run_dir)[0]
        assert unit["symbol"] == "AAA" and unit["lead"] == 20
        # h_steps is the lead in ROW steps, which is what an overlapping
        # label's HAC band is measured in.
        assert unit["h_steps"] == 4
        assert unit["q"] == pytest.approx(1.0)
        assert unit["d"] == pytest.approx([1.0, 1.0])
        assert unit["mu"] == pytest.approx(2.0)

    def test_units_come_back_one_per_series_and_lead(self, tmp_path):
        run_dir = str(tmp_path)
        rng = random.Random(3)
        with PredictionWriter(_node_dir(run_dir), SERIES,
                              fold=4, period_minutes=PERIOD) as w:
            for name in SERIES:
                for lead in (5, 10):
                    stamps, y, yhat, mu = _fold_block(rng, 6, 0.4)
                    w.append(name, lead, stamps, y, yhat, mu)
        units = read_prediction_series(run_dir)
        assert [(u["symbol"], u["lead"]) for u in units] == [
            ("AAA", 5), ("BBB", 5), ("AAA", 10), ("BBB", 10)
        ]
        assert {u["fold"] for u in units} == {4}
        assert all(len(u["y"]) == 6 for u in units)


class TestTheRowsReproduceTheSummary:
    def test_the_reported_mspe_pair_is_recomputable_from_the_rows(self, tmp_path):
        """The whole point: a fold's summary numbers are means over the
        saved rows, so nothing was lost when they were taken."""
        summary = _write_walk(str(tmp_path), n_folds=3)
        with open(os.path.join(summary, "walkforward.json"), encoding="utf-8") as fh:
            folds = json.load(fh)["folds"]
        for fold in folds:
            units = {u["symbol"]: u for u in read_prediction_series(fold["run_dir"])}
            with open(os.path.join(fold["run_dir"], "carry.json"),
                      encoding="utf-8") as fh:
                records = json.load(fh)["scan"]["records"]
            assert len(records) == len(units) == len(SERIES)
            for record in records:
                unit = units[record["symbol"]]
                assert len(unit["y"]) == int(record["n"])
                assert _mspe(unit["y"], unit["yhat"]) == pytest.approx(
                    record["mspe_model"], rel=1e-5
                )
                assert unit["q"] == pytest.approx(record["mspe_mean"], rel=1e-5)

    def test_every_fold_stamps_its_own_ordinal(self, tmp_path):
        summary = _write_walk(str(tmp_path), n_folds=3)
        with open(os.path.join(summary, "walkforward.json"), encoding="utf-8") as fh:
            folds = json.load(fh)["folds"]
        seen = [read_predictions(f["run_dir"])["fold"][0] for f in folds]
        assert seen == [0, 1, 2]


class TestTheFullTestCanNowRun:
    def test_a_walk_that_saved_rows_answers_both_halves(self, tmp_path):
        scored = score_walk(_write_walk(str(tmp_path)))
        assert scored["exact"] is True
        assert scored["notes"] == []
        assert [row["series"] for row in scored["rows"]] == ["AAA", "BBB", "GROUP"]
        for row in scored["rows"]:
            # The pooled Diebold-Mariano t is the half a walk of fold
            # summaries can never answer; it is answered here.
            assert row["t_pool"] is not None
            assert row["t_fold"] is not None
            assert row["passes"] is True
            assert row["r2oos"] > 0.0

    def test_a_walk_without_rows_still_only_answers_one_half(self, tmp_path):
        scored = score_walk(_write_walk(str(tmp_path), rows=False))
        assert scored["exact"] is False
        assert "not recoverable" in scored["notes"][0].lower()
        assert all(row["t_pool"] is None for row in scored["rows"])

    def test_a_forecast_no_better_than_the_mean_does_not_pass(self, tmp_path):
        scored = score_walk(_write_walk(str(tmp_path), edge=0.0))
        assert scored["exact"] is True
        for row in scored["rows"]:
            assert row["passes"] is False
