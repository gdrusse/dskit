"""ADR-0069: how high one cell must score once hundreds were tried.

Four things are pinned here. The LEDGER — a cell's id is its knobs, so
re-running one is not a new attempt and last week's attempt still counts.
The ARITHMETIC — the studentised statistic, the reference bars and the
session reduction against hand-worked values. The NULL — forty cells of
pure noise, resampled ten thousand times, and NOTHING clears the bar.
And the COMPOSITION — P5 still decides: a cell that fails the skill test
fails here whatever its statistic, and a significant sign with a
zero-touching win size is not a result.
"""

import json
import os
import random

import pytest

from dskit.pipeline.attempts import (
    T_FLOOR,
    AttemptRegistry,
    FixedFamilyLedger,
    bar_verdict,
    bonferroni_t,
    cell_id,
    expected_max_null,
    implied_trials,
    max_bar,
    merge_session_totals,
    session_totals,
    tier2_plan,
    tier2_verdict,
    utc_day,
)

np = pytest.importorskip("numpy")

PASSING_SKILL = {"passes": True, "t_pool": 4.0, "r2oos_pool": 0.01}


def _noise_cells(n_cells, n_sessions, rows_per_session, seed):
    """Cells whose gaps are pure noise — nothing to find, by construction."""
    rng = random.Random(seed)
    cells = {}
    for k in range(n_cells):
        totals = {}
        for s in range(n_sessions):
            gaps = [rng.gauss(0.0, 1.0) for _ in range(rows_per_session)]
            totals[s] = (sum(gaps), len(gaps))
        cells[f"cell{k:03d}"] = totals
    return cells


class TestTheCellId:
    def test_key_order_never_changes_the_id(self):
        assert cell_id({"model": "ridge", "h": 1}) == cell_id(
            {"h": 1, "model": "ridge"}
        )

    def test_a_different_knob_is_a_different_cell(self):
        assert cell_id({"model": "ridge", "h": 1}) != cell_id(
            {"model": "ridge", "h": 2}
        )

    def test_an_empty_key_is_refused(self):
        with pytest.raises(ValueError, match="non-empty mapping"):
            cell_id({})


class TestTheRegistry:
    def test_a_missing_ledger_reads_as_empty_not_as_an_error(self, tmp_path):
        assert AttemptRegistry(str(tmp_path / "none.jsonl")).count() == 0

    def test_re_running_the_same_knobs_is_not_a_new_attempt(self, tmp_path):
        reg = AttemptRegistry(str(tmp_path / "a.jsonl"))
        reg.record({"model": "ridge", "horizon": 1, "series": "JPM"}, t_pool=1.0)
        reg.record({"model": "ridge", "horizon": 1, "series": "JPM"}, t_pool=2.0)
        assert reg.count() == 1
        # The latest score wins; the COUNT, which is what the bar reads,
        # does not move.
        assert list(reg.cells().values())[0]["t_pool"] == 2.0

    def test_last_week_s_attempt_still_counts(self, tmp_path):
        path = str(tmp_path / "a.jsonl")
        AttemptRegistry(path).record({"model": "lgbm", "horizon": 3, "series": "JPM"})
        # A fresh process, tonight.
        tonight = AttemptRegistry(path)
        tonight.record({"model": "lgbm", "horizon": 60, "series": "JPM"})
        assert tonight.count() == 2

    def test_a_family_is_filtered_by_its_outcome_unit(self, tmp_path):
        reg = AttemptRegistry(str(tmp_path / "a.jsonl"))
        for unit in ("JPM", "JPM", "LLY"):
            reg.record(
                {"model": "ridge", "horizon": len(reg.cells()) + 1, "series": unit}
            )
        assert reg.count(series="JPM") == 2
        assert reg.count(series="LLY") == 1

    def test_a_corrupt_line_does_not_destroy_the_count(self, tmp_path):
        path = str(tmp_path / "a.jsonl")
        reg = AttemptRegistry(path)
        reg.record({"model": "ridge", "horizon": 1, "series": "JPM"})
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("not json\n\n")
        assert reg.count() == 1


class TestTheFixedFamilyLedger:
    def test_header_precedes_results_and_allocations_never_recycle(self, tmp_path):
        path = tmp_path / "attempts.jsonl"
        ledger = FixedFamilyLedger(path, "p11", ["A", "B"], alpha=0.05)
        header = ledger.prepare()
        result = ledger.record("A", 0.02, horizon=3)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert rows == [header, result]
        assert result["allocation"] == pytest.approx(0.025)
        assert result["adjusted_p"] == pytest.approx(0.04)
        assert result["passes"] is True
        assert "B" not in ledger.results()

    def test_arbitrary_arrival_order_has_the_same_final_decisions(self, tmp_path):
        keys = ["A", "B", "C"]
        first = FixedFamilyLedger(tmp_path / "a.jsonl", "p11", keys)
        second = FixedFamilyLedger(tmp_path / "b.jsonl", "p11", keys)
        for key, p_value in (("A", 0.01), ("B", 0.02), ("C", 0.001)):
            first.record(key, p_value)
        for key, p_value in (("C", 0.001), ("A", 0.01), ("B", 0.02)):
            second.record(key, p_value)
        assert {
            key: row["passes"] for key, row in first.results().items()
        } == {
            key: row["passes"] for key, row in second.results().items()
        }

    def test_exact_replay_is_idempotent_but_changed_result_is_refused(self, tmp_path):
        ledger = FixedFamilyLedger(tmp_path / "a.jsonl", "p11", ["A"])
        first = ledger.record("A", 0.01, horizon=2)
        assert ledger.record("A", 0.01, horizon=2) == first
        with pytest.raises(ValueError, match="changed or duplicated"):
            ledger.record("A", 0.02, horizon=2)

    def test_changed_header_and_undeclared_key_are_refused(self, tmp_path):
        path = tmp_path / "a.jsonl"
        FixedFamilyLedger(path, "p11", ["A", "B"]).prepare()
        with pytest.raises(ValueError, match="header changed"):
            FixedFamilyLedger(path, "p11", ["B", "A"]).prepare()
        with pytest.raises(ValueError, match="not in fixed family"):
            FixedFamilyLedger(path, "p12", ["A"]).record("B", 0.01)


class TestTheSessionReduction:
    def test_rows_of_one_session_reduce_to_a_sum_and_a_count(self):
        assert session_totals([0, 1000], [0.5, 1.5]) == {0: [2.0, 2]}

    def test_a_utc_day_boundary_splits_two_sessions(self):
        day = 86_400_000
        assert session_totals([0, day], [1.0, 2.0]) == {0: [1.0, 1], 1: [2.0, 1]}
        assert utc_day(day + 1) == 1

    def test_folds_merge_into_one_accumulator(self):
        acc = session_totals([0], [1.0])
        merge_session_totals(acc, session_totals([1000, 86_400_000], [2.0, 3.0]))
        assert acc == {0: [3.0, 2], 1: [3.0, 1]}

    def test_ragged_input_is_refused(self):
        with pytest.raises(ValueError, match="must agree"):
            session_totals([0, 1], [1.0])


class TestTheReferenceBars:
    def test_the_expected_best_of_many_tries_matches_the_published_value(self):
        # Bailey-Lopez de Prado's deflated-Sharpe expectation.
        assert expected_max_null(180) == pytest.approx(2.73, abs=0.01)
        assert expected_max_null(240) == pytest.approx(2.83, abs=0.01)

    def test_bonferroni_matches_the_published_value(self):
        assert bonferroni_t(180) == pytest.approx(3.45, abs=0.01)
        assert bonferroni_t(400) == pytest.approx(3.66, abs=0.01)

    def test_a_single_try_bar_is_worth_a_single_try(self):
        assert implied_trials(bonferroni_t(1)) == pytest.approx(1.0, abs=0.01)

    def test_a_harsher_bar_is_worth_more_tries(self):
        assert implied_trials(3.0) > implied_trials(2.0) > 1.0


class TestTheStatistic:
    def test_the_studentised_t_is_the_hand_worked_one(self):
        # Sessions sum to [1, 1, 1, 3] over four single-row days: the
        # mean is 1.5, the recentred sums are [-.5, -.5, -.5, 1.5], their
        # sum of squares is 3, and t = 6 / sqrt(3) = 3.4641.
        cells = {"a": {0: (1.0, 1), 1: (1.0, 1), 2: (1.0, 1), 3: (3.0, 1)}}
        row = max_bar(cells, n_boot=200, seed=1)["rows"][0]
        assert row["t"] == pytest.approx(3.4641016151377544)
        assert row["mean"] == pytest.approx(1.5)
        assert row["n_sessions"] == 4

    def test_the_pass_mark_never_falls_below_the_floor(self):
        cells = {"a": {s: (1.0, 1) for s in range(6)}}
        cells["a"][0] = (2.0, 1)
        bar = max_bar(cells, n_boot=200, seed=1)
        assert bar["pass_mark"] == max(bar["c_star"], T_FLOOR)
        assert bar["pass_mark"] >= 3.0

    def test_a_cell_with_no_variance_across_sessions_takes_no_part(self):
        cells = {
            "flat": {s: (1.0, 1) for s in range(5)},
            "real": {s: (float(s), 1) for s in range(5)},
        }
        bar = max_bar(cells, n_boot=200, seed=1)
        flat = next(r for r in bar["rows"] if r["cell"] == "flat")
        assert flat["t"] is None
        assert flat["adj_p"] is None

    def test_a_family_the_registry_knows_is_wider_than_the_one_resampled(self):
        cells = {"a": {s: (float(s), 1) for s in range(6)}}
        bar = max_bar(cells, n_boot=200, seed=1, k_declared=180)
        assert bar["k"] == 1
        assert bar["k_declared"] == 180
        assert bar["bonferroni"] == pytest.approx(3.45, abs=0.01)
        assert "LOWER bound" in bar["notes"][0]

    def test_the_bar_is_reproducible_and_seed_dependent(self):
        cells = _noise_cells(6, 40, 3, seed=4)
        assert (
            max_bar(cells, n_boot=500, seed=7)["c_star"]
            == max_bar(cells, n_boot=500, seed=7)["c_star"]
        )
        assert (
            max_bar(cells, n_boot=500, seed=7)["c_star"]
            != max_bar(cells, n_boot=500, seed=8)["c_star"]
        )

    def test_near_identical_cells_cost_the_family_almost_nothing(self):
        # Twenty copies of one cell are ONE attempt, and the shared
        # session coins are what discovers that from the data.
        base = _noise_cells(1, 60, 4, seed=12)["cell000"]
        twins = {f"t{i}": dict(base) for i in range(20)}
        spread = _noise_cells(20, 60, 4, seed=13)
        assert (
            max_bar(twins, n_boot=2000, seed=1)["c_star"]
            < max_bar(spread, n_boot=2000, seed=1)["c_star"]
        )

    def test_a_bad_replicate_count_is_refused(self):
        with pytest.raises(ValueError, match="n_boot"):
            max_bar({"a": {0: (1.0, 2)}}, n_boot=10)


class TestTheNull:
    def test_forty_cells_of_pure_noise_clear_nothing(self):
        cells = _noise_cells(40, 250, 8, seed=2026)
        bar = max_bar(cells, n_boot=10000, seed=5)
        # Every cell is offered a PASSING skill result, so only the P8
        # half of the rule can refuse it — and it does, every time.
        verdicts = [bar_verdict(name, bar, skill=PASSING_SKILL) for name in cells]
        assert not any(v["passes"] for v in verdicts)
        assert bar["rows"][0]["t"] < bar["pass_mark"]
        assert all(r["adj_p"] is None or r["adj_p"] > 0.05 for r in bar["rows"])

    def test_the_resampled_bar_sits_where_luck_puts_it(self):
        # The 95th percentile of the best-of-40 pure-noise statistics is
        # around 2.4-3.0; the floor is what carries it to 3.0.
        bar = max_bar(_noise_cells(40, 250, 8, seed=7), n_boot=4000, seed=5)
        assert 2.0 < bar["c_star"] < 3.6
        assert bar["k_implied"] > 1.0


class TestTheComposition:
    def _one_strong_cell(self):
        rng = random.Random(3)
        totals = {s: (4.0 + rng.gauss(0, 0.5), 8) for s in range(200)}
        return max_bar({"win": totals}, n_boot=2000, seed=1)

    def test_a_cell_that_clears_everything_passes(self):
        bar = self._one_strong_cell()
        out = bar_verdict("win", bar, skill=PASSING_SKILL)
        assert out["passes"] is True
        assert out["r2oos_lower"] > 0.0

    def test_p5_still_decides(self):
        bar = self._one_strong_cell()
        failed = bar_verdict("win", bar, skill={"passes": False})
        assert failed["passes"] is False
        assert "P5's skill test" in failed["reasons"][0]
        missing = bar_verdict("win", bar, skill=None)
        assert missing["passes"] is False

    def test_a_win_that_touches_zero_is_not_a_win(self):
        # A statistic that clears the mark on a mean whose one-sided
        # band still includes zero fails on SIZE, not on sign.
        rng = random.Random(4)
        totals = {s: (rng.gauss(0.0, 1.0), 4) for s in range(400)}
        totals[0] = (60.0, 4)
        bar = max_bar({"thin": totals}, n_boot=2000, seed=1)
        out = bar_verdict("thin", bar, skill=PASSING_SKILL)
        assert out["passes"] is False
        assert any("not positive" in r for r in out["reasons"])

    def test_an_unknown_cell_is_refused(self):
        with pytest.raises(ValueError, match="no row for cell"):
            bar_verdict("nope", self._one_strong_cell(), skill=PASSING_SKILL)


class TestTheExpensiveScrambleSeam:
    def test_a_plan_moves_whole_sessions_and_nothing_smaller(self):
        plans = tier2_plan([10, 11, 12, 13], n_runs=5, seed=1)
        assert len(plans) == 5
        for plan in plans:
            assert sorted(plan["donor"]) == [10, 11, 12, 13]
            assert sorted(plan["donor"].values()) == [10, 11, 12, 13]

    def test_half_days_are_dropped_from_the_pool(self):
        plan = tier2_plan([1, 2, 3, 4], n_runs=2, seed=0, drop=[2])[0]
        assert 2 not in plan["donor"]
        assert sorted(plan["donor"]) == [1, 3, 4]

    def test_a_plan_is_reproducible(self):
        assert tier2_plan([1, 2, 3, 4, 5], n_runs=3, seed=9) == tier2_plan(
            [1, 2, 3, 4, 5], n_runs=3, seed=9
        )

    def test_a_pool_too_small_to_permute_is_refused(self):
        with pytest.raises(ValueError, match="at least 2 usable sessions"):
            tier2_plan([1], n_runs=5)

    def test_the_verdict_needs_the_real_walk_to_beat_every_scramble(self):
        beaten = tier2_verdict(0.001, [0.002, -0.001], scrambled_t=[0.1, -0.2])
        assert beaten["beat_all"] is False
        assert beaten["passes"] is False

    def test_the_verdict_catches_a_broken_variance_estimator(self):
        rng = random.Random(2)
        out = tier2_verdict(
            0.05,
            [rng.gauss(0.0, 0.01) for _ in range(100)],
            scrambled_t=[rng.gauss(0.0, 3.0) for _ in range(100)],
        )
        assert out["beat_all"] is True
        assert out["calibrated"] is False
        assert any("every p-value in the project" in r for r in out["reasons"])

    def test_a_missing_calibration_check_is_named_not_ignored(self):
        out = tier2_verdict(0.05, [0.001, 0.002])
        assert out["calibrated"] is None
        assert any("was not checked" in r for r in out["reasons"])


pq = pytest.importorskip("pyarrow.parquet")

from dskit.pipeline.predictions import PredictionWriter  # noqa: E402
from dskit.pipeline.runs import format_bar, score_bar, walk_cells  # noqa: E402

LEAD = 5
PERIOD = 5
NAMES = ("AAA", "BBB", "CCC")


def _write_walk(root, n_folds=4, n_days=6, edge=0.0, seed=3):
    """A walk whose folds saved rows spread over whole trading days."""
    rng = random.Random(seed)
    summary = os.path.join(root, "walk")
    os.makedirs(summary, exist_ok=True)
    folds = []
    day = 86_400_000
    for i in range(n_folds):
        run_dir = os.path.join(root, f"wf-{i}")
        node = os.path.join(run_dir, "artifacts", "scan")
        os.makedirs(node, exist_ok=True)
        stamps = [
            (i * n_days + d) * day + m * PERIOD * 60_000
            for d in range(n_days)
            for m in range(10)
        ]
        with PredictionWriter(node, NAMES, fold=i, period_minutes=PERIOD) as w:
            for name in NAMES:
                y = [rng.gauss(0.0, 1.0) for _ in stamps]
                yhat = [edge * v + (1 - edge) * rng.gauss(0, 1) for v in y]
                w.append(name, LEAD, stamps, y, yhat, 0.0)
        folds.append(
            {
                "cutoff": f"2024-{i + 1:02d}-01",
                "run_dir": run_dir,
                "state": "ran",
                "score": 0.0,
            }
        )
    with open(os.path.join(summary, "walkforward.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": "walk", "asof": "2025-11-30", "folds": folds}, fh)
    return summary


class TestReadingARealWalk:
    def test_a_walk_reduces_to_one_cell_per_name_and_the_group(self, tmp_path):
        cells = walk_cells(
            _write_walk(str(tmp_path)), key={"model": "ridge", "spacing": 5}
        )
        units = {c["key"]["series"] for c in cells}
        assert units == {"AAA", "BBB", "CCC", "GROUP"}
        for cell in cells:
            # The ROWS are gone; only per-session sums survive, which is
            # what lets a bar hold dozens of walks at once.
            assert set(cell["totals"]) and "d" not in cell
            assert cell["key"]["model"] == "ridge"
            assert cell["skill"] is not None

    def test_a_noise_walk_clears_nothing_end_to_end(self, tmp_path):
        ledger = str(tmp_path / "attempts.jsonl")
        scored = score_bar(
            [_write_walk(str(tmp_path))],
            keys=[{"model": "ridge", "spacing": 5, "price": "close"}],
            registry=AttemptRegistry(ledger),
            n_boot=2000,
        )
        assert scored["k_registry"] == 4
        assert set(scored["families"]) == {"AAA", "BBB", "CCC", "GROUP"}
        assert not any(
            v["passes"]
            for family in scored["families"].values()
            for v in family["verdicts"]
        )
        assert "pass mark" in format_bar(scored)

    def test_group_cells_can_be_suppressed(self, tmp_path):
        cells = walk_cells(
            _write_walk(str(tmp_path)),
            key={"model": "ridge", "spacing": 5},
            group=None,
        )
        assert {cell["key"]["series"] for cell in cells} == set(NAMES)

    def test_one_injected_family_can_cover_every_asset(self, tmp_path):
        scored = score_bar(
            [_write_walk(str(tmp_path))],
            keys=[{"model": "ridge", "spacing": 5}],
            n_boot=200,
            group=None,
            family_of=lambda _cell: "whole-study",
        )
        assert scored["n_cells"] == len(NAMES)
        assert set(scored["families"]) == {"whole-study"}
        family = scored["families"]["whole-study"]
        assert family["bar"]["k"] == len(NAMES)
        assert {
            family["keys"][verdict["cell"]]["series"] for verdict in family["verdicts"]
        } == set(NAMES)

    def test_a_walk_with_no_rows_refuses_rather_than_inventing_a_bar(self, tmp_path):
        summary = os.path.join(str(tmp_path), "empty")
        os.makedirs(summary, exist_ok=True)
        with open(
            os.path.join(summary, "walkforward.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump({"name": "w", "asof": "x", "folds": []}, fh)
        with pytest.raises(ValueError, match="no walk kept"):
            score_bar([summary], n_boot=200)
