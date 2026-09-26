"""ADR-0188 formulation B, T12: ScenarioUtilitySolve latency on realistic joint instances.

Test plan item 8, "Solver trust" (children/intraday_equities/docs/research/
mio-joint-policy/2026-09-26-from-scratch-formulation.md): 100+ randomized
N = 12, S = 128 instances solve with median under 3 s. This file is that
item's LATENCY half; the 10 s time-limit halt is tested on its own.

The instances and the timing harness live in ``tools/mio_latency_bench.py``
(seeded; one common factor across names, one path across horizons; ``K_i``
exit tranches per name; see its docstring), loaded by path because
``tools/`` is not a package (the ``tests/tools`` precedent), so the CLI and
this suite time the identical program.

* The default run is a 10-instance SMOKE, the full run's first ten seeds:
  every solve optimal, every target and trade whole and non-negative, every
  tranche split summing to its target. Per-solve seconds are RECORDED
  (``record_property``, and printed), never asserted: a shared machine's
  timings are not a contract. It solves under the 10 s halt, so a hang
  fails the default run fast instead of stalling it.
* The FULL benchmark (100 instances; end-to-end median < 3 s and p99 < 10 s)
  is marked ``slow`` and runs only under ``DSKIT_LATENCY_FULL=1``. ``slow`` is
  registered but not deselected by default, so the environment gate is what
  keeps the default run fast (the ``test_kronos`` precedent).
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import pathlib

import numpy as np
import pytest

_BENCH_PATH = pathlib.Path(__file__).resolve().parents[2] / "tools" / "mio_latency_bench.py"


def _load_bench():
    """Import ``tools/mio_latency_bench.py`` by path (``tools/`` is not a package)."""
    spec = importlib.util.spec_from_file_location("mio_latency_bench", _BENCH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = _load_bench()

SMOKE_INSTANCES = 10
FULL_INSTANCES = 100
FULL_GATE = "DSKIT_LATENCY_FULL"


class TestInstanceGenerator:
    def test_a_seed_regenerates_the_same_instance_float_for_float(self):
        first = bench.make_instance(bench.BASE_SEED)
        assert bench.make_instance(bench.BASE_SEED)["digest"] == first["digest"]
        assert bench.make_instance(bench.BASE_SEED + 1)["digest"] != first["digest"]

    @pytest.mark.parametrize("offset", range(SMOKE_INSTANCES))
    def test_every_instance_has_the_formulation_b_shape(self, offset):
        instance = bench.make_instance(bench.BASE_SEED + offset)
        names, rows, account = instance["names"], instance["rows"], instance["account"]
        assert names == list(bench.NAMES)
        weights = np.asarray(instance["weights"])
        assert weights.shape == (bench.N_SCENARIOS,)
        assert weights.sum() == pytest.approx(1.0)
        marks = sum(rows[name]["price"] * rows[name]["held"] for name in names)
        gross = account["cash"] + marks
        assert account["buying_power"] == account["cash"]
        assert account["gross_limit"] == pytest.approx(gross)  # no leverage
        # The envelope holds every reachable scenario wealth: a book of at
        # most `gross` dollars moving by the largest scenario return, less a
        # generous bound on what trading and exiting it can cost.
        w0 = gross + account.get("carried_wealth", 0.0)
        move = max(float(np.abs(values).max()) for values in instance["r"].values())
        friction = max(
            (row["cost_buy"] + row["cost_sell"] + row["exit_cost_per_share"]) / row["price"]
            for row in rows.values()
        )
        assert account["wealth_hi"] >= w0 + gross * move
        assert 0.0 < account["wealth_lo"] <= w0 - gross * (move + friction)
        for name in names:
            row, k = rows[name], instance["tranches"][name]
            assert k in bench.TRANCHE_CHOICES
            assert np.asarray(instance["r"][name]).shape == (k, bench.N_SCENARIOS)
            assert 20.0 <= row["price"] <= 1500.0
            assert isinstance(row["held"], int) and row["held"] >= 0
            assert row["x_max"] == pytest.approx(max(gross, row["price"] * row["held"]))
            assert 0.0 < row["cost_buy"] < row["cost_sell"] == row["exit_cost_per_share"]
        params = instance["params"]
        assert bench.LatencySolve.validate_params(params) == []
        # Formulation B: the CVaR row is on; no cardinality, no minimum ticket.
        assert params["cvar_alpha"] == 0.95 and params["cvar_limit"] is not None
        assert params["cardinality"] is None and params["min_ticket"] == 0.0
        assert params["risk_aversion_gamma"] == 2.0 and params["n_tangents"] == 32

    def test_the_batch_draws_the_regimes_the_benchmark_claims(self):
        batch = [bench.make_instance(bench.BASE_SEED + j) for j in range(SMOKE_INSTANCES)]
        held = [inst["rows"][name]["held"] > 0 for inst in batch for name in inst["names"]]
        assert 0.3 < np.mean(held) < 0.7  # about half the names held
        drift = [float(np.mean(inst["r"][name][0])) for inst in batch for name in inst["names"]]
        assert min(drift) < 0.0 < max(drift)  # some names drift down
        carried = [inst["account"].get("carried_wealth", 0.0) for inst in batch]
        assert any(c > 0.0 for c in carried) and any(c == 0.0 for c in carried)
        widths = {k for inst in batch for k in inst["tranches"].values()}
        assert widths == set(bench.TRANCHE_CHOICES)

    def test_scenarios_are_joint_across_names_and_horizons(self):
        instance = bench.make_instance(bench.BASE_SEED)
        names = instance["names"]
        first_minute = np.array([instance["r"][name][0] for name in names])
        across_names = np.corrcoef(first_minute)[~np.eye(len(names), dtype=bool)]
        assert across_names.mean() > 0.15  # one common factor: the names co-move
        for name in names:
            path = np.asarray(instance["r"][name])
            # one path: exiting at minute 2 shares minute 1's shock
            assert np.corrcoef(path[0], path[1])[0, 1] > 0.5

    def test_an_infeasible_draw_is_refused_by_name(self, monkeypatch):
        monkeypatch.setattr(bench, "CVAR_LIMIT", (1e-4, 2e-4))  # below any exit fee
        with pytest.raises(ValueError, match="CVaR cap"):
            for offset in range(SMOKE_INSTANCES):  # the first seed holding anything
                bench.make_instance(bench.BASE_SEED + offset)


class TestLatencySmoke:
    def test_ten_instances_solve_optimal_with_whole_targets(self, tmp_path, record_property):
        # Solved under the 10 s halt, not the 60 s hang guard: a regression
        # fails the default run fast instead of stalling it for minutes.
        records, summary = bench.run_benchmark(
            SMOKE_INSTANCES, time_limit=bench.P99_BUDGET_S, run_dir=str(tmp_path)
        )
        print(bench.describe(records, summary))
        record_property("wall_seconds", [round(r["wall_seconds"], 4) for r in records])
        record_property("solve_seconds", [r["solve_seconds"] for r in records])
        failed = [(r["seed"], r["termination"], r["error"]) for r in records if not bench.solved(r)]
        assert failed == []
        assert [r["seed"] for r in records] == [bench.BASE_SEED + j for j in range(SMOKE_INSTANCES)]
        for record in records:
            instance = bench.make_instance(record["seed"])
            assert record["digest"] == instance["digest"]
            target, trades, split = record["target"], record["trades"], record["tranche_split"]
            for name in instance["names"]:
                buy = trades.get(name, {}).get("buy", 0)
                sell = trades.get(name, {}).get("sell", 0)
                assert isinstance(buy, int) and isinstance(sell, int)
                assert buy >= 0 and sell >= 0 and not (buy and sell)
                shares = target.get(name, 0)
                assert isinstance(shares, int) and shares >= 0
                assert shares == instance["rows"][name]["held"] + buy - sell
                assert min(split[name]) >= 0.0
                assert sum(split[name]) == pytest.approx(shares, abs=1e-6 * max(1, shares))
            # the doorway builds tranche variables only for K_i > 1
            tranche_rows = sum(k for k in instance["tranches"].values() if k > 1)
            assert record["tranche_variables"] == tranche_rows
            assert math.isfinite(record["solve_seconds"]) and record["solve_seconds"] > 0.0
            assert record["wall_seconds"] >= record["solve_seconds"]


@pytest.mark.slow
class TestLatencyFull:
    def test_one_hundred_instances_meet_the_latency_budget(self, tmp_path):
        if os.environ.get(FULL_GATE) != "1":
            pytest.skip(f"set {FULL_GATE}=1 for the {FULL_INSTANCES}-instance latency benchmark")
        records, summary = bench.run_benchmark(FULL_INSTANCES, run_dir=str(tmp_path))
        print(bench.describe(records, summary))
        print(f"machine: {json.dumps(bench.machine_info(), sort_keys=True)}")
        failed = [(r["seed"], r["termination"], r["error"]) for r in records if not bench.solved(r)]
        assert failed == []
        wall = np.array([r["wall_seconds"] for r in records])
        assert len(wall) == FULL_INSTANCES
        assert np.median(wall) < bench.MEDIAN_BUDGET_S
        assert np.percentile(wall, 99) < bench.P99_BUDGET_S
