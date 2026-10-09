"""Coherence across linked binaries (ADR-0257 part 3): LP feasibility, the riskless set, the projection.

Every expected arbitrage is worked by hand in the comments: the legs, the credit and the fact that
the position pays nothing net in every outcome the declared relations allow.
"""

import itertools
import json
import math
import random
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyomo")
pytest.importorskip("highspy")

from dskit.pipeline import binary_curve
from dskit.pipeline.base import ConfigError
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.libs import binary_coherence
from dskit.pipeline.libs.binary_coherence import RELATION_KINDS, RELATIONS, STATUS_OK, BinaryCoherence, Relation
from dskit.pipeline.libs.pyomo import PyomoSolve
from dskit.pipeline.planner import plan
from tests.pipeline.test_binary_curve import ABSENT, SPEC, SPEC_IDS, spec_cell, Three

BASE = {"id_field": "id", "bid_field": "bid", "ask_field": "ask", "fair_field": "coherent",
        "min_spread": 0.01, "solver": "highs"}
FLOOR = binary_coherence.MIN_SPREAD_FLOOR


def contract(cid, bid, ask, **extra):
    return {"id": cid, "bid": bid, "ask": ask, **extra}


def run(rows, **params):
    return BinaryCoherence("coh", {**BASE, **params}).run(None, {"records": rows})


# -- a planted bucket-vs-threshold violation --------------------------------------------------------


def test_finds_a_planted_bucket_versus_threshold_violation_with_its_direction_and_size():
    # A = above(100), B = above(110), X = between(100, 110) = A - B.
    # A - B is worth at most ask A - bid B = 0.52 - 0.20 = 0.32, yet X bids 0.35:
    # sell X at 0.35, buy A at 0.52, sell B at 0.20 -> credit 0.03; payout X - A + B = 0 in every outcome.
    rows = [contract("A", 0.50, 0.52, bs=40, az=7), contract("B", 0.20, 0.22, bs=9, az=50),
            contract("X", 0.35, 0.37, bs=12, az=50)]
    out = run(rows, differences=[["X", "A", "B"]], chains=[["A", "B"]],
              bid_size_field="bs", ask_size_field="az")
    arb = out["arbitrage"]
    assert arb["feasible"] is False
    assert arb["violation"] == pytest.approx(0.03, abs=1e-9)
    legs = {leg["id"]: (leg["side"], leg["units"]) for leg in arb["legs"]}
    assert legs == {"X": ("sell", pytest.approx(1.0)), "A": ("buy", pytest.approx(1.0)),
                    "B": ("sell", pytest.approx(1.0))}
    assert arb["credit_per_unit"] == pytest.approx(0.03)
    assert [r["kind"] for r in arb["relations"]] == ["differences"]
    # executable size: the thinnest leg -- buy A against 7 offered
    assert arb["executable_units"] == pytest.approx(7.0)


def test_an_underpriced_range_is_bought_against_the_thresholds():
    # The other direction of the same identity: X = A - B is worth at least bid A - ask B
    # = 0.50 - 0.22 = 0.28, yet X offers at 0.25. Buy X at 0.25, sell A at 0.50, buy B at 0.22:
    # credit -0.25 + 0.50 - 0.22 = 0.03. Payout X - A + B: (A, B, X) in outcome S < 100 is
    # (0, 0, 0) -> 0; 100 <= S < 110 is (1, 0, 1) -> 1 - 1 + 0 = 0; S >= 110 is (1, 1, 0) -> 0 - 1 + 1 = 0.
    rows = [contract("A", 0.50, 0.52), contract("B", 0.20, 0.22), contract("X", 0.23, 0.25)]
    arb = run(rows, differences=[["X", "A", "B"]])["arbitrage"]
    assert arb["feasible"] is False
    assert arb["violation"] == pytest.approx(0.03, abs=1e-9)
    legs = {leg["id"]: (leg["side"], leg["units"], leg["price"]) for leg in arb["legs"]}
    assert legs == {"X": ("buy", pytest.approx(1.0), 0.25), "A": ("sell", pytest.approx(1.0), 0.50),
                    "B": ("buy", pytest.approx(1.0), 0.22)}
    assert arb["credit_per_unit"] == pytest.approx(0.03)
    assert arb["relations"] == [{"kind": "differences", "index": 0, "ids": ["X", "A", "B"]}]


def test_a_partition_that_bids_over_one_is_a_sell_everything_arbitrage():
    rows = [contract("lo", 0.30, 0.32), contract("mid", 0.40, 0.42), contract("hi", 0.33, 0.35)]
    arb = run(rows, partitions=[["lo", "mid", "hi"]])["arbitrage"]
    # bids sum to 1.03: sell all three, owe exactly 1 at settlement
    assert arb["feasible"] is False and arb["violation"] == pytest.approx(0.03)
    assert {leg["id"]: leg["side"] for leg in arb["legs"]} == {"lo": "sell", "mid": "sell", "hi": "sell"}
    assert arb["executable_units"] is None  # no size columns named


def test_a_partition_whose_asks_sum_under_one_is_a_buy_everything_arbitrage():
    # asks sum to 0.30 + 0.38 + 0.29 = 0.97: buy all three, receive exactly 1 at settlement
    rows = [contract("lo", 0.28, 0.30), contract("mid", 0.36, 0.38), contract("hi", 0.27, 0.29)]
    out = run(rows, partitions=[["lo", "mid", "hi"]])
    arb = out["arbitrage"]
    assert arb["feasible"] is False and arb["violation"] == pytest.approx(0.03)
    assert {leg["id"]: leg["side"] for leg in arb["legs"]} == {"lo": "buy", "mid": "buy", "hi": "buy"}
    fair = {r["id"]: r["coherent"] for r in out["records"]}
    assert sum(fair.values()) == pytest.approx(1.0), "the projection must land on the coherent set"


def test_the_tolerance_decides_whether_a_small_violation_counts():
    rows = [contract("lo", 0.28, 0.30), contract("mid", 0.36, 0.38), contract("hi", 0.27, 0.29)]
    assert run(rows, partitions=[["lo", "mid", "hi"]])["arbitrage"]["feasible"] is False
    loose = run(rows, partitions=[["lo", "mid", "hi"]], tolerance=0.05)["arbitrage"]
    assert loose["feasible"] is True
    assert loose["legs"] == [] and loose["relations"] == [], "a feasible verdict reports no position at all"
    assert loose["credit_per_unit"] == 0.0 and loose["executable_units"] is None


def test_the_executable_size_divides_each_legs_size_by_its_units():
    # A + C = 1 and C = A - B force B = 2A - 1 (so A >= 1/2). Sell half a B at 0.33 and one C at 0.80:
    # liability 0.5 (2A - 1) + (1 - A) = 0.5 in every outcome, credit 0.165 + 0.80 = 0.965, profit 0.465.
    # B's bid size 10 covers 10 / 0.5 = 20 position units, C's 30 covers 30: the position is 20, not 10.
    rows = [contract("A", 0.66, 0.68, bs=99, az=99), contract("B", 0.33, 0.35, bs=10, az=99),
            contract("C", 0.80, 0.82, bs=30, az=99)]
    arb = run(rows, chains=[["A", "B"]], differences=[["C", "A", "B"]], partitions=[["A", "C"]],
              bid_size_field="bs", ask_size_field="az")["arbitrage"]
    assert arb["violation"] == pytest.approx(0.465)
    assert {leg["id"]: (leg["side"], leg["units"]) for leg in arb["legs"]} == {
        "B": ("sell", pytest.approx(0.5)), "C": ("sell", pytest.approx(1.0))}
    assert arb["executable_units"] == pytest.approx(20.0)


def test_a_negative_or_missing_leg_size_executes_nothing():
    rows = [contract("lo", 0.30, 0.32, bs=-5, az=9), contract("mid", 0.40, 0.42, bs=8, az=9),
            contract("hi", 0.33, 0.35, bs=None, az=9)]
    arb = run(rows, partitions=[["lo", "mid", "hi"]], bid_size_field="bs", ask_size_field="az")["arbitrage"]
    assert arb["executable_units"] == 0.0
    rows[0]["bs"], rows[2]["bs"] = 6, 7
    assert run(rows, partitions=[["lo", "mid", "hi"]], bid_size_field="bs",
               ask_size_field="az")["arbitrage"]["executable_units"] == pytest.approx(6.0)


def test_the_unit_interval_bounds_the_lp_itself():
    # X1 = A - B1 and X2 = A - B2 with A <= 1 cap each range at 0.9, yet both bid 0.95: widening 0.05
    # each, 0.10 in all. Without the [0, 1] bound A could rise to 1.05 for a total widening of 0.05.
    rows = [contract("A", 0.95, 1.0), contract("X1", 0.95, 0.95), contract("X2", 0.95, 0.95),
            contract("B1", 0.10, 0.10), contract("B2", 0.10, 0.10)]
    arb = run(rows, differences=[["X1", "A", "B1"], ["X2", "A", "B2"]])["arbitrage"]
    assert arb["violation"] == pytest.approx(0.10)


def test_the_projection_stays_inside_the_unit_interval_where_the_unconstrained_one_would_not():
    # mids A 0.02, B 0.98, X 0.50 with X = A - B: unconstrained, X would land near -0.96
    rows = [contract("A", 0.01, 0.03), contract("B", 0.97, 0.99), contract("X", 0.20, 0.80)]
    fair = {r["id"]: r["coherent"] for r in run(rows, differences=[["X", "A", "B"]])["records"]}
    assert all(0.0 <= v <= 1.0 for v in fair.values()), fair
    assert fair["X"] == pytest.approx(0.0, abs=1e-6)
    assert fair["A"] == pytest.approx(fair["B"], abs=1e-6)


def test_the_tolerance_gates_only_the_verdict_never_a_legs_units():
    # A + C = 1, C = A - B, A >= B: violation 0.9 from buying A (1 unit) and selling half a B.
    # A tolerance of 0.7 still calls it infeasible, and must not drop B's 0.5 units: a position
    # without them is not riskless.
    rows = [contract("A", 0.06, 0.08), contract("B", 0.96, 0.98), contract("C", 0.02, 0.04)]
    relations = {"chains": [["A", "B"]], "differences": [["C", "A", "B"]], "partitions": [["A", "C"]]}
    exact = run(rows, **relations)["arbitrage"]
    assert {leg["id"]: leg["units"] for leg in exact["legs"]} == {"A": pytest.approx(1.0), "B": pytest.approx(0.5)}
    loose = run(rows, tolerance=0.7, **relations)["arbitrage"]
    assert loose["feasible"] is False
    assert {leg["id"]: leg["units"] for leg in loose["legs"]} == {"A": pytest.approx(1.0), "B": pytest.approx(0.5)}
    assert loose["relations"] == exact["relations"]


def test_leg_dust_is_one_named_constant_relative_to_the_largest_dual():
    assert binary_coherence.LEG_DUST == 1e-9 and "LEG_DUST" in binary_coherence.__all__


def test_leg_dust_scales_with_the_largest_dual_never_an_absolute_floor(monkeypatch):
    # an LP whose duals all come back scaled by 1e-12 (a solver that scales its objective) still
    # names the same legs: dust is relative to the largest |dual|, never a fixed absolute number. Only the LP's duals
    # are scaled: the projection QP's multipliers are the certificate's evidence, and scaled ones are refused.
    from pyomo.environ import Suffix

    real_solve = BinaryCoherence._solve

    def scaled(self, solver, model):
        results = real_solve(self, solver, model)
        if "projection" not in model.name and isinstance(getattr(model, "dual", None), Suffix):
            for row in list(model.dual):
                model.dual[row] *= 1e-12
        return results

    rows = [contract("k100", 0.38, 0.40), contract("k110", 0.45, 0.47)]
    monkeypatch.setattr(BinaryCoherence, "_solve", scaled)
    arb = run(rows, chains=[["k100", "k110"]])["arbitrage"]
    assert {leg["id"]: leg["side"] for leg in arb["legs"]} == {"k100": "buy", "k110": "sell"}
    assert arb["relations"] == [{"kind": "chains", "index": 0, "ids": ["k100", "k110"]}]


def test_an_infeasible_verdict_with_no_recoverable_legs_is_refused_by_name(monkeypatch):
    monkeypatch.setattr(BinaryCoherence, "_legs", lambda self, *args: [])
    with pytest.raises(RuntimeError, match="no riskless position"):
        run([contract("k100", 0.38, 0.40), contract("k110", 0.45, 0.47)], chains=[["k100", "k110"]])


def test_an_infeasible_verdict_whose_legs_cannot_profit_is_refused_by_name(monkeypatch):
    # a sale-only position owes at settlement, so it profits at most its credit: here 0
    sold = [{"id": "k110", "side": "sell", "units": 1.0, "price": 0.0}]
    monkeypatch.setattr(BinaryCoherence, "_legs", lambda self, *args: sold)
    with pytest.raises(RuntimeError, match="cannot profit"):
        run([contract("k100", 0.38, 0.40), contract("k110", 0.45, 0.47)], chains=[["k100", "k110"]])


def test_a_buy_everything_arbitrage_has_a_negative_credit_and_is_still_reported():
    # paying 0.97 for a partition that settles at exactly 1: the net premium is negative, the
    # position's best profit (credit + bought units) is positive, so it is not refused
    rows = [contract("lo", 0.28, 0.30), contract("mid", 0.36, 0.38), contract("hi", 0.27, 0.29)]
    arb = run(rows, partitions=[["lo", "mid", "hi"]])["arbitrage"]
    assert arb["credit_per_unit"] == pytest.approx(-0.97) and len(arb["legs"]) == 3


def test_a_solve_that_does_not_finish_optimal_is_refused_by_name(monkeypatch):
    class Stopped:
        class solver:
            termination_condition = "maxTimeLimit"

    monkeypatch.setattr(BinaryCoherence, "_solve", lambda self, solver, model: Stopped())
    with pytest.raises(RuntimeError, match="widening LP.*maxTimeLimit"):
        run(LADDER, **LADDER_RELATIONS)


def test_contradictory_relations_raise_rather_than_report_a_verdict():
    rows = [contract("A", 0.4, 0.6), contract("B", 0.4, 0.6), contract("C", 0.4, 0.6)]
    with pytest.raises(Exception):
        run(rows, partitions=[["A", "B"], ["A", "C"], ["B", "C"]], differences=[["A", "B", "C"]])


def test_an_inverted_threshold_ladder_is_found_on_the_chain():
    # above(100) asks 0.40 but above(110) bids 0.45: buy the lower strike, sell the higher, credit 0.05
    rows = [contract("k100", 0.38, 0.40), contract("k110", 0.45, 0.47)]
    arb = run(rows, chains=[["k100", "k110"]])["arbitrage"]
    assert arb["violation"] == pytest.approx(0.05)
    assert {leg["id"]: leg["side"] for leg in arb["legs"]} == {"k100": "buy", "k110": "sell"}
    assert arb["relations"] == [{"kind": "chains", "index": 0, "ids": ["k100", "k110"]}]


# -- a coherent ladder passes, and its projection is the identity ---------------------------------------


LADDER = [contract("a90", 0.80, 0.84), contract("a100", 0.50, 0.54), contract("a110", 0.20, 0.24),
          contract("b90_100", 0.28, 0.32), contract("b100_110", 0.28, 0.32),
          contract("lt90", 0.16, 0.20), contract("ge110", 0.20, 0.24)]
LADDER_RELATIONS = {
    "chains": [["a90", "a100", "a110"]],
    "differences": [["b90_100", "a90", "a100"], ["b100_110", "a100", "a110"]],
    "partitions": [["lt90", "b90_100", "b100_110", "ge110"]],
}


def test_a_coherent_ladder_is_feasible_with_no_legs():
    out = run(LADDER, **LADDER_RELATIONS)
    assert out["arbitrage"]["feasible"] is True
    assert out["arbitrage"]["legs"] == [] and out["arbitrage"]["relations"] == []
    assert out["arbitrage"]["violation"] == pytest.approx(0.0, abs=1e-9)


def test_the_projection_is_the_identity_on_already_coherent_mids():
    out = run(LADDER, **LADDER_RELATIONS)
    for row in out["records"]:
        assert row["coherent_status"] == "ok"
        assert row["coherent"] == pytest.approx((row["bid"] + row["ask"]) / 2, abs=1e-6)


def test_the_projection_repairs_an_inverted_pair_by_inverse_squared_spread_weights():
    # mids 0.40 (spread 0.02) and 0.50 (spread 0.04) violate p1 >= p2; the weighted projection
    # pools them: (w1 m1 + w2 m2) / (w1 + w2) with w = 1/spread^2 = 2500 and 625 -> 0.42
    rows = [contract("k1", 0.39, 0.41), contract("k2", 0.48, 0.52)]
    out = run(rows, chains=[["k1", "k2"]])
    got = {r["id"]: r["coherent"] for r in out["records"]}
    assert got["k1"] == pytest.approx(0.42, abs=1e-6) and got["k2"] == pytest.approx(0.42, abs=1e-6)


def test_a_spread_below_the_floor_is_weighted_at_the_floor():
    # spreads 0 and 0.04 with a floor of 0.02: weights 2500 and 625, as above
    rows = [contract("k1", 0.40, 0.40), contract("k2", 0.48, 0.52)]
    got = {r["id"]: r["coherent"] for r in run(rows, chains=[["k1", "k2"]], min_spread=0.02)["records"]}
    assert got["k1"] == pytest.approx(0.42, abs=1e-6)


# -- unusable quotes and relations -----------------------------------------------------------------------


def test_an_unusable_quote_is_marked_and_its_relations_are_skipped_by_name():
    rows = [contract("k1", 0.50, 0.40), contract("k2", 0.30, 0.32), contract("k3", "0.3", 0.2),
            contract("k4", 0.10, 0.12)]
    out = run(rows, chains=[["k1", "k2"], ["k2", "k4"]], partitions=[["k3", "k4"]])
    status = {r["id"]: r["coherent_status"] for r in out["records"]}
    assert status == {"k1": "bad_quote", "k2": "ok", "k3": "bad_quote", "k4": "ok"}
    skipped = out["summary"]["skipped_relations"]
    assert {(s["kind"], s["index"]) for s in skipped} == {("chains", 0), ("partitions", 0)}
    assert out["arbitrage"]["feasible"] is True


def test_a_relation_naming_an_unknown_id_is_skipped_by_name():
    out = run([contract("k1", 0.5, 0.52)], chains=[["k1", "ghost"]])
    assert out["summary"]["skipped_relations"][0]["missing"] == ["ghost"]
    assert out["records"][0]["coherent_status"] == "unrelated"


@pytest.mark.parametrize("bid, ask", [
    (1.2, 1.3), (0.1, -0.2), (0.5, 0.4), ("0.1", 0.2), (True, 0.2), (float("nan"), 0.5), (0.1, float("inf")),
    (0.1, "0.2"), (0.1, False)])
def test_a_quote_no_binary_can_honour_crossed_or_not_a_number_is_refused(bid, ask):
    out = run([contract("k1", bid, ask), contract("k2", 0.1, 0.2)], chains=[["k1", "k2"]])
    assert out["records"][0]["coherent_status"] == "bad_quote"
    assert out["summary"]["skipped_relations"][0]["bad_quote"] == ["k1"]


# -- independent oracles: written here, never reading the node -------------------------------------------

REPO = Path(__file__).resolve().parents[2]
GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
EXTREMES = (0.0, 1e-9, 1e-6, 1e-3, 0.999, 0.999999, 1.0)
MIN_SPREADS = (0.01, 0.1, 1.0)   # the floor, an interior point, the ceiling of the supported domain


def two_sided(bid, ask):
    """The side rule restated: a sale needs bid > 0, a purchase needs 0 < ask < 1 (and bid <= ask)."""
    return bid is not None and ask is not None and bid > 0 and 0 < ask < 1


def pava_nonincreasing(values, weights):
    """Weighted pool-adjacent-violators for a NON-INCREASING fit (mids lie in [0, 1]: no clipping needed)."""
    blocks = []
    for value, weight in zip(values, weights):
        blocks.append([value, weight, 1])
        while len(blocks) > 1 and blocks[-2][0] < blocks[-1][0]:
            m2, w2, c2 = blocks.pop()
            m1, w1, c1 = blocks.pop()
            blocks.append([(m1 * w1 + m2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    return [mean for mean, _, count in blocks for _ in range(count)]


def water_fill(mids, spreads, at_most):
    """argmin sum(((p - m) / s)^2) s.t. sum p = 1 (<= 1 when ``at_most``), 0 <= p <= 1: p_i = clip(m_i + lam s_i^2)."""
    if not mids or (at_most and sum(mids) <= 1.0):
        return list(mids)
    scale = [s * s for s in spreads]
    hi = 1.0 / min(scale) + 1.0
    lo = -hi
    for _ in range(400):
        lam = (lo + hi) / 2
        if sum(min(1.0, max(0.0, m + lam * c)) for m, c in zip(mids, scale)) < 1.0:
            lo = lam
        else:
            hi = lam
    return [min(1.0, max(0.0, m + (lo + hi) / 2 * c)) for m, c in zip(mids, scale)]


def expected_fair(kind, quotes, min_spread):
    """The oracle projection of ``quotes`` ((bid, ask) per contract, in relation order); None unless two-sided.

    A contract with a missing side is a free variable: in a chain it drops out (its neighbours only have to
    be ordered), in a partition it absorbs any shortfall, so the others only need to sum to at most one.
    """
    at = [i for i, (bid, ask) in enumerate(quotes) if two_sided(bid, ask)]
    mids = [(quotes[i][0] + quotes[i][1]) / 2 for i in at]
    spreads = [max(quotes[i][1] - quotes[i][0], min_spread) for i in at]
    if kind == "chains":
        fitted = pava_nonincreasing(mids, [1 / s ** 2 for s in spreads])
    else:
        fitted = water_fill(mids, spreads, at_most=len(at) < len(quotes))
    out = [None] * len(quotes)
    for i, value in zip(at, fitted):
        out[i] = value
    return out


def draw_quote(rng):
    """One (bid, ask) with bid <= ask: uniform, grid (0 and 1 included), zero-spread and extreme-edge draws."""
    roll = rng.random()
    if roll < 0.5:
        bid, ask = sorted((rng.uniform(0.001, 0.999), rng.uniform(0.001, 0.999)))
        return (bid, bid) if rng.random() < 0.15 else (bid, ask)
    pool = GRID if roll < 0.8 else EXTREMES
    return tuple(sorted((rng.choice(pool), rng.choice(pool))))


def ladder_cases(kind, count, seed):
    rng = random.Random(seed)
    for _ in range(count):
        quotes = [draw_quote(rng) for _ in range(rng.randint(2, 8))]
        if kind == "chains" and rng.random() < 0.25:
            quotes.sort(key=lambda q: -(q[0] + q[1]))   # mids non-increasing: the projection is the identity
        yield quotes, rng.choice(MIN_SPREADS)


def fair_of(out):
    return {r["id"]: r["coherent"] for r in out["records"]}


# -- the solver hang, in a subprocess so a regression fails instead of hanging the suite -------------------

HANG_SCRIPT = """
import json, sys
from dskit.pipeline.libs.binary_coherence import BinaryCoherence
case = json.loads(sys.argv[1])
params = {"id_field": "id", "bid_field": "bid", "ask_field": "ask", "fair_field": "f", "solver": "highs",
          "min_spread": case["min_spread"], case["kind"]: [list(case["quotes"])]}
rows = [{"id": cid, "bid": bid, "ask": ask} for cid, (bid, ask) in case["quotes"].items()]
out = BinaryCoherence("hang", params).run(None, {"records": rows})
print(json.dumps({r["id"]: r["f"] for r in out["records"]}))
"""

# kind, quotes by id, min_spread. HiGHS' QP solver hung on each of these (exact-zero quotes) and on the
# strictly-positive twins below, which are TWO-SIDED under the side rule, so they still reach the QP. They hung at
# min_spread 1e-4 / 1e-3, now outside the supported domain; the quotes are kept and run at the floor.
VERBATIM_HANGS = {
    "coherent chain": ("chains", {"a": [0.0, 1.0], "b": [0.0, 0.0]}, 0.01),
    "partition of three": ("partitions", {"a": [0.5, 0.53], "b": [0.0, 0.0], "c": [0.0, 0.5]}, 0.01),
    "partition of four": ("partitions", {"a": [0.0, 0.5], "b": [0.0, 0.0], "c": [0.0, 0.3], "d": [0.0, 0.5]}, 0.01),
}
TWO_SIDED_HANGS = {
    "coherent chain": ("chains", {"a": [1e-9, 0.999999], "b": [1e-9, 1e-9]}, 0.01),
    "partition of three": ("partitions", {"a": [0.5, 0.53], "b": [1e-9, 1e-9], "c": [1e-9, 0.5]}, 0.01),
    "partition of four": ("partitions",
                          {"a": [1e-9, 0.5], "b": [1e-9, 1e-9], "c": [1e-9, 0.3], "d": [1e-9, 0.5]}, 0.01),
    "wide first bucket": ("partitions", {"a": [0.001, 0.99], "b": [0.3, 0.99], "c": [1e-9, 1e-6]}, 0.01),
}


def run_in_subprocess(kind, quotes, min_spread):
    case = {"kind": kind, "quotes": quotes, "min_spread": min_spread}
    try:
        done = subprocess.run([sys.executable, "-c", HANG_SCRIPT, json.dumps(case)], cwd=REPO, timeout=60,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        pytest.fail(f"the projection did not finish within 60 s (a solver hang): {case}")
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.splitlines()[-1])


@pytest.mark.parametrize("name", sorted(VERBATIM_HANGS))
def test_quotes_that_hung_the_solver_finish_and_agree_with_the_oracle(name):
    # exact-zero quotes are one-sided under the side rule: those contracts float, the others are projected
    kind, quotes, min_spread = VERBATIM_HANGS[name]
    got = run_in_subprocess(kind, quotes, min_spread)
    want = expected_fair(kind, [tuple(q) for q in quotes.values()], min_spread)
    assert [got[cid] for cid in quotes] == [pytest.approx(w, abs=1e-6) if w is not None else None for w in want]


@pytest.mark.parametrize("name", sorted(TWO_SIDED_HANGS))
def test_two_sided_quotes_that_hung_the_solver_finish_with_the_oracles_answer(name):
    kind, quotes, min_spread = TWO_SIDED_HANGS[name]
    got = run_in_subprocess(kind, quotes, min_spread)
    want = expected_fair(kind, [tuple(q) for q in quotes.values()], min_spread)
    assert all(w is not None for w in want), "every contract here is two-sided"
    assert [got[cid] for cid in quotes] == [pytest.approx(w, abs=1e-6) for w in want]


def test_an_already_coherent_set_that_hung_the_solver_returns_exactly_its_mids():
    kind, quotes, min_spread = TWO_SIDED_HANGS["coherent chain"]
    got = run_in_subprocess(kind, quotes, min_spread)
    assert got == {cid: (bid + ask) / 2 for cid, (bid, ask) in quotes.items()}


# -- the projection is the true weighted least-squares point ---------------------------------------------


def test_the_projection_is_the_true_weighted_least_squares_point_not_a_scaled_approximation():
    # a is pinned near 0 with the largest weight (spread floored to 0.01: w = 1e4, against b's 6.25); scaling the
    # objective by that largest weight made HiGHS return "optimal" wrong points at the retired floor 1e-4, where a's
    # weight was 1e8 (b = 0.5501, c = 0.2673, d = 0.1826). Water-filling by hand (p_i = mid_i + lam / w_i,
    # sum p = 1, w_i = 1 / spread_i^2, nothing clipped) gives lam = 0.70320 and b 0.61251, c 0.25703, d 0.13039.
    quotes = [(1e-9, 1e-9), (0.3, 0.7), (0.2, 0.3), (0.01, 0.2)]
    rows = [contract(cid, bid, ask) for cid, (bid, ask) in zip("abcd", quotes)]
    fair = fair_of(run(rows, partitions=[list("abcd")], min_spread=0.01))
    assert fair["b"] == pytest.approx(0.61251, abs=2e-5)
    assert fair["c"] == pytest.approx(0.25703, abs=2e-5)
    assert fair["d"] == pytest.approx(0.13039, abs=2e-5)
    want = expected_fair("partitions", quotes, 0.01)
    assert [fair[cid] for cid in "abcd"] == [pytest.approx(w, abs=1e-6) for w in want]
    assert sum(fair.values()) == pytest.approx(1.0, abs=1e-7)


@pytest.mark.parametrize("kind", ["chains", "partitions"])
def test_the_projection_matches_an_independent_oracle_on_random_ladders(kind):
    # chains: weighted pool-adjacent-violators; partitions: water-filling. 150 seeded ladders of 2-8 contracts
    for n, (quotes, min_spread) in enumerate(ladder_cases(kind, 150, seed=2026)):
        ids = [f"c{i}" for i in range(len(quotes))]
        out = run([contract(cid, bid, ask) for cid, (bid, ask) in zip(ids, quotes)], min_spread=min_spread,
                  **{kind: [ids]})
        where = f"{kind} case {n}: {quotes} min_spread {min_spread}"
        for row, want in zip(out["records"], expected_fair(kind, quotes, min_spread)):
            if want is None:
                assert row["coherent"] is None and row["coherent_status"] == "bad_quote", where
            else:
                assert row["coherent_status"] == "ok", where
                assert row["coherent"] == pytest.approx(want, abs=1e-6), where


# -- one exact oracle per structure, written here and never reading the node ---------------------------------
#
# The PAVA and water-filling oracles above cover one relation kind at a time. These cover the kinds COMBINED,
# with one-sided contracts and weights spanning 8e3 (the supported domain, MIN_SPREAD_FLOOR). Where a contract
# has no objective term (one-sided) the oracles give it weight ZERO, not a small one: a weight of 1e-6 moved the
# two-sided values by up to 8e-7 over 400 strike worlds, most of the 1e-6 tolerance the tests assert.

SPREAD_DRAWS = (0.0, 1e-6, 1e-4, 1e-2, 0.3, 0.9)   # at the floor 0.01 the weights span (0.9 / 0.01)^2 = 8100


def draw_two_sided(rng):
    """A two-sided (bid, ask): a spread from SPREAD_DRAWS (or uniform), shrunk to fit strictly inside (0, 1)."""
    spread = rng.choice(SPREAD_DRAWS) if rng.random() < 0.75 else rng.uniform(0.0, 0.95)
    mid = rng.uniform(0.02, 0.98)
    spread = min(spread, 1.96 * min(mid, 1 - mid))
    return mid - spread / 2, mid + spread / 2


def draw_world_quote(rng):
    """Mostly two-sided; else one side only or none. Every draw is a valid quote, so no relation is skipped."""
    roll, price = rng.random(), round(rng.uniform(0.05, 0.95), 3)
    if roll < 0.72:
        return draw_two_sided(rng)
    if roll < 0.80:
        return price, None          # bid only
    if roll < 0.86:
        return price, 1.0           # an ask of one binds nothing
    if roll < 0.92:
        return None, price          # ask only
    return (0.0, price) if roll < 0.96 else (None, None)    # a bid of zero is no sale; nothing at all


def weight_ratio(quotes, min_spread):
    """Largest over smallest projection weight among the two-sided quotes (1 when there are none)."""
    weights = [1 / max(ask - bid, min_spread) ** 2 for bid, ask in quotes if two_sided(bid, ask)]
    return max(weights) / min(weights) if weights else 1.0


def least_squares_summing_to_one(a, b):
    """argmin |a x - b|^2 subject to sum(x) = 1, by the null-space method (``a`` has k columns)."""
    k = a.shape[1]
    start = np.full(k, 1.0 / k)
    if k == 1:
        return start
    null = np.linalg.svd(np.ones((1, k)))[2][1:].T          # k x (k - 1), orthonormal, spans sum(x) = 0
    return start + null @ np.linalg.lstsq(a @ null, b - a @ start, rcond=None)[0]


def payout_simplex_optimum(payout, quotes, min_spread):
    """Exact projection of a strike world: p = P pi, pi >= 0, sum pi = 1 over its regions.

    Minimises ``sum w_i (p_i - mid_i)^2`` over the two-sided contracts by enumerating the SUPPORT of pi: on each
    support the optimum is an equality-constrained least squares, and an optimal pi of minimal support is the
    unique solution of its own support's problem, so the best feasible candidate is the optimum.
    """
    ids = [cid for cid in payout if two_sided(*quotes[cid])]
    if not ids:
        return {}
    regions = len(payout[ids[0]])
    rows = np.array([payout[cid] for cid in ids], float)
    mids = np.array([sum(quotes[cid]) / 2 for cid in ids])
    root_w = np.array([1 / max(quotes[cid][1] - quotes[cid][0], min_spread) for cid in ids])
    best_cost, best = math.inf, None
    for mask in range(1, 2 ** regions):
        support = [r for r in range(regions) if mask >> r & 1]
        pi = least_squares_summing_to_one(root_w[:, None] * rows[:, support], root_w * mids)
        if pi.min() < -1e-12:
            continue
        cost = float(np.sum((root_w * (rows[:, support] @ pi - mids)) ** 2))
        if cost < best_cost:
            best_cost, best = cost, rows[:, support] @ pi
    return dict(zip(ids, best.tolist()))


# Each relation kind's rows as ({id: coefficient}, rhs) pairs: (equalities coef.p = rhs, inequalities coef.p >= rhs).
# Keyed by kind, so a kind added to RELATIONS without rows here fails the census test below.
ORACLE_ROWS = {
    "partitions": lambda ids: ([({cid: 1 for cid in ids}, 1.0)], []),
    "chains": lambda ids: ([], [({a: 1, b: -1}, 0.0) for a, b in zip(ids, ids[1:])]),
    "differences": lambda ids: ([({ids[0]: 1, ids[1]: -1, ids[2]: 1}, 0.0)], []),
}


def active_set_optimum(quotes, relations, min_spread):
    """Exact projection of any small system, by enumerating which inequalities are active.

    ``quotes`` maps each LINKED id to its (bid, ask); ``relations`` is the node's params shape. Every subset of
    the relation inequalities (and every free / at-0 / at-1 state per variable) is tried as an equality system:
    ``pinv`` finds a point (kept only if the system is consistent to 1e-9), the null space is then searched for
    the least-squares point, and the best candidate that satisfies every inequality is the optimum. None when no
    candidate is feasible (the relations admit no probability vector).
    """
    ids = list(quotes)
    n, where = len(ids), {cid: i for i, cid in enumerate(ids)}
    equalities, inequalities = [], []
    for kind, id_lists in relations.items():
        for listed in id_lists:
            found_eq, found_ge = ORACLE_ROWS[kind](listed)
            equalities += found_eq
            inequalities += found_ge

    def vector(coefs):
        out = np.zeros(n)
        for cid, coef in coefs.items():
            out[where[cid]] = coef
        return out

    eq_rows = [(vector(c), r) for c, r in equalities]
    ge_rows = [(vector(c), r) for c, r in inequalities]
    two = [i for i, cid in enumerate(ids) if two_sided(*quotes[cid])]
    pick = np.zeros((len(two), n))
    for row, i in enumerate(two):
        pick[row, i] = 1 / max(quotes[ids[i]][1] - quotes[ids[i]][0], min_spread)
    target = pick @ np.array([sum(quotes[cid]) / 2 if two_sided(*quotes[cid]) else 0.0 for cid in ids])
    best_cost, best = math.inf, None
    for active in itertools.product((False, True), repeat=len(ge_rows)):
        for pins in itertools.product((None, 0.0, 1.0), repeat=n):
            rows = [a for a, _ in eq_rows] + [a for (a, _), on in zip(ge_rows, active) if on]
            rhs = [r for _, r in eq_rows] + [r for (_, r), on in zip(ge_rows, active) if on]
            for i, pinned in enumerate(pins):
                if pinned is not None:
                    rows.append(vector({ids[i]: 1}))
                    rhs.append(pinned)
            if rows:
                matrix, right = np.array(rows), np.array(rhs)
                point = np.linalg.pinv(matrix) @ right
                if np.max(np.abs(matrix @ point - right)) > 1e-9:
                    continue
                singular, basis = np.linalg.svd(matrix)[1:]
                null = basis[int(np.sum(singular > 1e-10)):].T
            else:
                point, null = np.zeros(n), np.eye(n)
            if null.shape[1]:
                point = point + null @ (np.linalg.pinv(pick @ null) @ (target - pick @ point))
            if point.min() < -1e-10 or point.max() > 1 + 1e-10 or any(a @ point < r - 1e-10 for a, r in ge_rows):
                continue
            cost = float(np.sum((pick @ point - target) ** 2))
            if cost < best_cost:
                best_cost, best = cost, point
    return None if best is None else {ids[i]: float(best[i]) for i in two}


def mixed_system(rng):
    """Up to six ids and one to three relations of any kinds; the enumeration size is capped at 6000 active sets."""
    while True:
        ids = [f"c{i}" for i in range(rng.randint(3, 6))]
        relations = {}
        for _ in range(rng.randint(1, 3)):
            kind = rng.choice(RELATION_KINDS)
            size = 3 if kind == "differences" else rng.randint(2, min(4, len(ids)))
            relations.setdefault(kind, []).append(rng.sample(ids, size))
        chain_rows = sum(len(listed) - 1 for listed in relations.get("chains", []))
        if 2 ** chain_rows * 3 ** len(ids) <= 6000:
            return ids, relations


SPREAD_FLOORS = (FLOOR, 0.1, 1.0)   # the floor, an interior point, the ceiling


def assert_projected(out, quotes, want, linked, where):
    """Each row against the oracle: its value if linked and two-sided, else None and the status that says why."""
    for row in out["records"]:
        cid = row["id"]
        if cid in want:
            assert row["coherent_status"] == "ok", f"{where}: {cid}"
            assert row["coherent"] == pytest.approx(want[cid], abs=1e-6), f"{where}: {cid}"
        else:
            status = "unrelated" if two_sided(*quotes[cid]) and cid not in linked else "bad_quote"
            assert (row["coherent"], row["coherent_status"]) == (None, status), f"{where}: {cid}"


@pytest.mark.parametrize("min_spread", SPREAD_FLOORS)
def test_the_projection_equals_the_exact_payout_simplex_optimum_on_strike_worlds(min_spread):
    # 4 to 14 contracts: chain + differences + partition over m strikes, a mix of two-sided and one-sided quotes
    rng = random.Random(f"simplex {min_spread}")
    ratios, one_sided = [], 0
    for case in range(60):
        payout, chain, differences, partition = strike_world(rng.randint(2, 7))
        quotes = {cid: draw_world_quote(rng) for cid in payout}
        one_sided += sum(not two_sided(*q) for q in quotes.values())
        ratios.append(weight_ratio(quotes.values(), min_spread))
        out = run([contract(cid, *q) for cid, q in quotes.items()], chains=[chain], differences=differences,
                  partitions=[partition], min_spread=min_spread)
        want = payout_simplex_optimum(payout, quotes, min_spread)
        assert_projected(out, quotes, want, set(payout), f"strike world {case}: {quotes}")
    assert max(ratios) > 0.4 * (0.9 / min_spread) ** 2, "the draws must reach the widest weight ratio min_spread allows"
    assert one_sided > 40, "the draws must include one-sided contracts"


@pytest.mark.parametrize("min_spread", SPREAD_FLOORS)
def test_the_projection_equals_the_exact_active_set_optimum_on_small_mixed_systems(min_spread):
    rng = random.Random(f"active set {min_spread}")
    evaluated = infeasible = 0
    while evaluated < 20:
        ids, relations = mixed_system(rng)
        quotes = {cid: draw_world_quote(rng) for cid in ids}
        rows = [contract(cid, *q) for cid, q in quotes.items()]
        linked = {cid for listed in relations.values() for found in listed for cid in found}
        want = active_set_optimum({cid: quotes[cid] for cid in ids if cid in linked}, relations, min_spread)
        where = f"{relations} {quotes}"
        if want is None:
            infeasible += 1
            with pytest.raises(RuntimeError, match="widening LP"):
                run(rows, min_spread=min_spread, **relations)
            continue
        evaluated += 1
        assert_projected(run(rows, min_spread=min_spread, **relations), quotes, want, linked, where)
    assert infeasible < 20, "the draws must mostly admit a probability vector"



# -- hand repros: the two shapes that separate a correct bound from a mutant that drops one ------------------


def test_a_projection_that_hits_the_unit_bound_keeps_it_active_while_the_rest_move():
    # X = A - B with mids A 0.99 (spread 0.01), B 0.30 (0.02), X 0.99 (0.01); weights 1e4, 2500, 1e4 (floor 0.01).
    # Unconstrained: normal equations 2a - b = 1.98 and 5b = 4a - 3.66 give a = 1.04 > 1. With a = 1 the b-equation
    # b - 0.3 = 4 (a - b - 0.99) gives 5b = 0.34: b = 0.068 and X = 1 - 0.068 = 0.932. A dropped p <= 1 bound would
    # return a = 1.04 (clipped to 1) with b = 0.1 and X = 0.94.
    rows = [contract("A", 0.985, 0.995), contract("B", 0.29, 0.31), contract("X", 0.985, 0.995)]
    fair = fair_of(run(rows, differences=[["X", "A", "B"]], min_spread=0.01))
    assert fair == {"A": pytest.approx(1.0, abs=1e-6), "B": pytest.approx(0.068, abs=1e-6),
                    "X": pytest.approx(0.932, abs=1e-6)}


def test_a_one_sided_member_still_binds_the_relation_through_its_unit_bound():
    # A has a bid only (an ask of one binds nothing): it floats in [0, 1] but X = A - B still needs X + B <= 1.
    # Mids X 0.70, B 0.50 (equal weights) sum to 1.2: project onto X + B = 1, taking 0.1 from each. A = X + B = 1.
    # A dropped relation (or a free variable bounded by 2) would leave X 0.7 and B 0.5.
    rows = [contract("A", 0.40, 1.0), contract("B", 0.49, 0.51), contract("X", 0.69, 0.71)]
    out = run(rows, differences=[["X", "A", "B"]])
    assert fair_of(out) == {"A": None, "B": pytest.approx(0.4, abs=1e-6), "X": pytest.approx(0.6, abs=1e-6)}
    assert out["records"][0]["coherent_status"] == "bad_quote"


# -- every relation kind has an oracle-backed projection case and an LP per-position case (census) -----------

# kind -> (label, quotes by id, relations, min_spread): checked against active_set_optimum, never against the node
PROJECTION_CASES = {
    "chains": [
        ("an inverted pair", {"k1": (0.39, 0.41), "k2": (0.48, 0.52)}, {"chains": [["k1", "k2"]]}, 0.01),
        ("a violation in the middle of three", {"a": (0.59, 0.61), "b": (0.29, 0.31), "c": (0.39, 0.41)},
         {"chains": [["a", "b", "c"]]}, 0.01),
        ("a one-sided member between two violators", {"a": (0.59, 0.61), "b": (0.40, None), "c": (0.69, 0.71)},
         {"chains": [["a", "b", "c"]]}, 0.01),
    ],
    "partitions": [
        ("asks under one", {"lo": (0.28, 0.30), "mid": (0.36, 0.38), "hi": (0.27, 0.29)},
         {"partitions": [["lo", "mid", "hi"]]}, 0.01),
        ("an excess clipped at zero", {"a": (0.01, 0.03), "b": (0.97, 0.99), "c": (0.97, 0.99)},
         {"partitions": [["a", "b", "c"]]}, 0.01),
        ("an excess the one-sided member cannot absorb", {"a": (0.59, 0.61), "b": (0.69, 0.71), "c": (None, 0.9)},
         {"partitions": [["a", "b", "c"]]}, 0.01),
    ],
    "differences": [
        ("the planted bucket violation", {"A": (0.50, 0.52), "B": (0.20, 0.22), "X": (0.35, 0.37)},
         {"differences": [["X", "A", "B"]]}, 0.01),
        ("the unit bound active", {"A": (0.985, 0.995), "B": (0.29, 0.31), "X": (0.985, 0.995)},
         {"differences": [["X", "A", "B"]]}, 0.01),
        ("a one-sided threshold", {"A": (0.40, 1.0), "B": (0.49, 0.51), "X": (0.69, 0.71)},
         {"differences": [["X", "A", "B"]]}, 0.01),
    ],
}
PROJECTION_PARAMS = [pytest.param(kind, case, id=f"{kind}: {case[0]}")
                     for kind, cases in PROJECTION_CASES.items() for case in cases]

# kind -> (label, quotes, relations, {id: (side, price)} legs, violation): each hand-worked
LP_POSITION_CASES = {
    "chains": [
        # c2 bids 0.81 over c1's ask 0.76: sell c2 at 0.81, buy c1 at 0.76 (c1 >= c2), credit 0.05, worst payout 0
        ("the second pair inverted", {"c0": (0.89, 0.91), "c1": (0.74, 0.76), "c2": (0.81, 0.83)},
         {"chains": [["c0", "c1", "c2"]]}, {"c2": ("sell", 0.81), "c1": ("buy", 0.76)}, 0.05),
    ],
    "partitions": [
        # bids sum to 1.03: sell all three, owe exactly 1
        ("bids over one", {"lo": (0.30, 0.32), "mid": (0.40, 0.42), "hi": (0.33, 0.35)},
         {"partitions": [["lo", "mid", "hi"]]}, {"lo": ("sell", 0.30), "mid": ("sell", 0.40), "hi": ("sell", 0.33)},
         0.03),
        # asks sum to 0.97: buy all three, receive exactly 1
        ("asks under one", {"lo": (0.28, 0.30), "mid": (0.36, 0.38), "hi": (0.27, 0.29)},
         {"partitions": [["lo", "mid", "hi"]]}, {"lo": ("buy", 0.30), "mid": ("buy", 0.38), "hi": ("buy", 0.29)},
         0.03),
    ],
    "differences": [
        # X = A - B is worth at most ask A - bid B = 0.32, yet X bids 0.35
        ("the range bids over its thresholds", {"A": (0.50, 0.52), "B": (0.20, 0.22), "X": (0.35, 0.37)},
         {"differences": [["X", "A", "B"]]}, {"X": ("sell", 0.35), "A": ("buy", 0.52), "B": ("sell", 0.20)}, 0.03),
        # X = A - B is worth at least bid A - ask B = 0.28, yet X offers at 0.25
        ("the range offers under its thresholds", {"A": (0.50, 0.52), "B": (0.20, 0.22), "X": (0.23, 0.25)},
         {"differences": [["X", "A", "B"]]}, {"X": ("buy", 0.25), "A": ("sell", 0.50), "B": ("buy", 0.22)}, 0.03),
    ],
}
LP_PARAMS = [pytest.param(kind, case, id=f"{kind}: {case[0]}")
             for kind, cases in LP_POSITION_CASES.items() for case in cases]


def test_every_relation_kind_has_oracle_rows_a_projection_case_and_an_lp_position_case():
    kinds = set(RELATION_KINDS)
    assert set(ORACLE_ROWS) == set(PROJECTION_CASES) == set(LP_POSITION_CASES) == kinds
    for table in (PROJECTION_CASES, LP_POSITION_CASES):
        for kind, cases in table.items():
            assert cases and all(kind in case[2] for case in cases), f"{kind} needs cases that declare it"


@pytest.mark.parametrize("kind, case", PROJECTION_PARAMS)
def test_the_projection_cases_of_each_relation_kind_match_the_exact_active_set_optimum(kind, case):
    label, quotes, relations, min_spread = case
    linked = {cid for listed in relations.values() for found in listed for cid in found}
    want = active_set_optimum({cid: q for cid, q in quotes.items() if cid in linked}, relations, min_spread)
    out = run([contract(cid, *q) for cid, q in quotes.items()], min_spread=min_spread, **relations)
    assert_projected(out, quotes, want, linked, label)


@pytest.mark.parametrize("kind, case", LP_PARAMS)
def test_the_lp_position_cases_of_each_relation_kind_name_the_legs_the_violation_and_the_relation(kind, case):
    label, quotes, relations, legs, violation = case
    arb = run([contract(cid, *q) for cid, q in quotes.items()], **relations)["arbitrage"]
    assert arb["feasible"] is False and arb["violation"] == pytest.approx(violation, abs=1e-9), label
    assert {leg["id"]: (leg["side"], leg["price"]) for leg in arb["legs"]} == legs, label
    assert [r["kind"] for r in arb["relations"]] == [kind], label


# -- the supported domain: min_spread in [floor, ceiling], and ladders of 30 at exactly the floor ------------------


def test_the_supported_domain_and_the_certificate_limit_are_three_pinned_public_constants():
    # restated here on purpose: a pin read from the module under test would assert nothing
    assert binary_coherence.MIN_SPREAD_FLOOR == 0.01
    assert binary_coherence.MIN_SPREAD_CEILING == 1.0
    assert binary_coherence.PROJECTION_BOUND_LIMIT == 1e-2
    for name in ("MIN_SPREAD_FLOOR", "MIN_SPREAD_CEILING", "PROJECTION_BOUND_LIMIT"):
        assert name in binary_coherence.__all__


def wide_ladder(rng, kind, count=30):
    """``count`` two-sided quotes with one spread-0.9 contract (weight ~1.2) and one zero-spread (weight 1/FLOOR^2)."""
    wide, tight = rng.sample(range(count), 2)
    if kind == "chains":
        mids = sorted((rng.uniform(0.03, 0.97) for _ in range(count)), reverse=True)
        mids = [min(0.97, max(0.03, m + rng.uniform(-0.04, 0.04))) for m in mids]    # noise breaks the order
        mids[wide] = 0.5
    else:
        raw = [rng.random() + 0.2 for _ in range(count)]
        others = rng.uniform(0.9, 1.1) - 0.5                                          # the rest sum to about 0.5
        mids = [r / sum(raw) * others for r in raw]
        mids[wide] = 0.5
    quotes = []
    for i, mid in enumerate(mids):
        spread = 0.9 if i == wide else 0.0 if i == tight else min(rng.choice(SPREAD_DRAWS), 1.96 * min(mid, 1 - mid))
        quotes.append((mid - spread / 2, mid + spread / 2))
    return quotes


@pytest.mark.parametrize("kind", ["chains", "partitions"])
def test_thirty_contract_ladders_at_exactly_the_floor_match_pava_and_water_filling(kind):
    rng = random.Random(f"wide ladders {kind}")
    for case in range(25):
        quotes = wide_ladder(rng, kind)
        assert weight_ratio(quotes, FLOOR) >= 0.99 * (0.9 / FLOOR) ** 2, "the weights must span the full ratio"
        ids = [f"c{i}" for i in range(len(quotes))]
        out = run(rows_of(ids, quotes), min_spread=FLOOR, **{kind: [ids]})
        for row, want in zip(out["records"], expected_fair(kind, quotes, FLOOR)):
            assert row["coherent"] == pytest.approx(want, abs=1e-6), f"{kind} ladder {case}: {row['id']}"


@pytest.mark.parametrize("edge", [0.01, 1.0])
def test_min_spread_is_accepted_at_exactly_each_edge_of_the_supported_domain(edge):
    params = {**BASE, "min_spread": edge, "chains": [["a", "b"]]}
    assert BinaryCoherence.validate_params(params) == []
    BinaryCoherence("coh", params)


# (min_spread, the constant it broke, that constant as the message prints it, why the message gives)
OUTSIDE_THE_DOMAIN = [
    pytest.param(math.nextafter(0.01, 0), "MIN_SPREAD_FLOOR", "0.01", "suboptimal", id="just below the floor"),
    pytest.param(1e-4, "MIN_SPREAD_FLOOR", "0.01", "suboptimal", id="the retired floor 1e-4"),
    pytest.param(1e-9, "MIN_SPREAD_FLOOR", "0.01", "suboptimal", id="1e-9"),
    pytest.param(math.nextafter(1.0, 2), "MIN_SPREAD_CEILING", "1", "equally", id="just above the ceiling"),
    pytest.param(1.5, "MIN_SPREAD_CEILING", "1", "equally", id="1.5"),
    pytest.param(1e4, "MIN_SPREAD_CEILING", "1", "equally", id="1e4"),
]


@pytest.mark.parametrize("spread, name, shown, why", OUTSIDE_THE_DOMAIN)
def test_min_spread_outside_the_supported_domain_is_refused_naming_the_edge_it_broke_and_why(spread, name, shown, why):
    params = {**BASE, "min_spread": spread, "chains": [["a", "b"]]}
    problems = BinaryCoherence.validate_params(params)
    assert len(problems) == 1 and "min_spread" in problems[0] and repr(spread) in problems[0]
    assert f"{name} {shown}:" in problems[0] and why in problems[0]
    with pytest.raises(Exception, match=name):
        BinaryCoherence("coh", params)


@pytest.mark.parametrize("spread", [5e-3, 1e-3, 1e-4, 5e-5, 1e-5, 1e-6, 1e-7, 1e-9, 1e-12])
def test_the_min_spreads_that_measured_silent_wrong_answers_are_refused(spread):
    # HiGHS returned suboptimal points as optimal: 1 in 12,000 draws at 1e-5, 64 at 1e-6, 411 at 1e-7, and (the
    # candidate-9 lens) error up to 0.033 on a 173-contract chain at 1e-4
    problems = BinaryCoherence.validate_params({**BASE, "min_spread": spread, "chains": [["a", "b"]]})
    assert any("min_spread" in p and "MIN_SPREAD_FLOOR" in p for p in problems)


def plan_with_min_spread(spread):
    """Plan a bar reader feeding the node, as a document wires it; ``ConfigError`` lists the node's params problems."""
    reader = {"root": "./store", "source": "venue", "stream": "bars", "ticker_field": "id", "end_field": "end_s",
              "bid_field": "bid", "ask_field": "ask", "price_field": "last", "volume_field": "qty",
              "open_interest_field": "oi"}
    node = {**BASE, "min_spread": spread, "chains": [["a", "b"]]}
    return plan(PipelineDocument.from_obj({"name": "coherence-plan", "pipeline": {
        "bars": {"uses": "dskit.pipeline.libs.binary_market_rows:QuoteBarRows", "params": reader},
        "rows": {"uses": f"{binary_coherence.__name__}:BinaryCoherence", "inputs": {"records": "$bars.records"},
                 "params": node}}}))


@pytest.mark.parametrize("spread, name", [(1e-4, "MIN_SPREAD_FLOOR"), (1e4, "MIN_SPREAD_CEILING")])
def test_the_candidate_9_lens_repros_are_refused_when_the_document_is_planned(spread, name):
    # the lens ran a 173-contract chain at min_spread 1e-4 (HiGHS: status ok, error up to 0.033) and min_spread 1e4
    # (every contract 1.0, status ok): neither document may reach a run
    with pytest.raises(ConfigError, match=rf"pipeline\.rows: min_spread {spread!r} is .*{name}"):
        plan_with_min_spread(spread)
    for edge in (0.01, 1.0):
        assert plan_with_min_spread(edge) is not None


# -- the certificate's soundness: the reported bound covers the error against an independent exact oracle ----------

BOUND_SLACK = 1e-7   # HiGHS' feasibility tolerance: the bound certifies a point that is feasible only up to it


def assert_certified(out, want, where):
    """The run was not refused, its bound is a float inside the limit, and every oracle value lies within the bound."""
    bound = out["summary"]["projection_bound"]
    assert isinstance(bound, float) and 0.0 <= bound <= binary_coherence.PROJECTION_BOUND_LIMIT, f"{where}: {bound}"
    for row in out["records"]:
        if row["id"] in want:
            gap = abs(row["coherent"] - want[row["id"]])
            assert gap <= bound + BOUND_SLACK, f"{where}: {row['id']} is {gap:.3g} off, the bound is {bound:.3g}"
    return bound


@pytest.mark.parametrize("min_spread", SPREAD_FLOORS)
def test_the_bound_covers_the_error_and_stays_inside_the_limit_on_strike_worlds(min_spread):
    rng = random.Random(f"certified strike worlds {min_spread}")
    for case in range(60):
        payout, chain, differences, partition = strike_world(rng.randint(2, 7))
        quotes = {cid: draw_world_quote(rng) for cid in payout}
        out = run([contract(cid, *q) for cid, q in quotes.items()], chains=[chain], differences=differences,
                  partitions=[partition], min_spread=min_spread)
        want = payout_simplex_optimum(payout, quotes, min_spread)
        assert_certified(out, want, f"strike world {case}: {quotes}")


def big_ladder(rng, kind):
    """50-120 two-sided quotes: 30% zero-spread, the rest with the spread of a ``draw_two_sided`` draw."""
    count = rng.randint(50, 120)
    spreads = [0.0 if rng.random() < 0.3 else ask - bid for bid, ask in (draw_two_sided(rng) for _ in range(count))]
    if kind == "chains":
        mids = sorted((rng.uniform(0.03, 0.97) for _ in range(count)), reverse=True)
        mids = [min(0.97, max(0.03, m + rng.uniform(-0.06, 0.06))) for m in mids]    # noise breaks the order
    else:
        raw = [rng.random() ** 3 + 0.002 for _ in range(count)]                       # a few heavy buckets
        total = rng.uniform(0.8, 1.2)
        mids = [r / sum(raw) * total for r in raw]
    fitted = [min(spread, 1.96 * min(mid, 1 - mid)) for mid, spread in zip(mids, spreads)]
    return [(mid - spread / 2, mid + spread / 2) for mid, spread in zip(mids, fitted)]


@pytest.mark.parametrize("kind", ["chains", "partitions"])
@pytest.mark.parametrize("min_spread", SPREAD_FLOORS)
def test_the_bound_covers_the_error_and_stays_inside_the_limit_on_ladders_of_50_to_120(kind, min_spread):
    rng = random.Random(f"certified {kind} {min_spread}")
    for case in range(10):
        quotes = big_ladder(rng, kind)
        ids = [f"c{i}" for i in range(len(quotes))]
        out = run(rows_of(ids, quotes), min_spread=min_spread, **{kind: [ids]})
        want = dict(zip(ids, expected_fair(kind, quotes, min_spread)))
        assert_certified(out, want, f"{kind} ladder {case} of {len(quotes)} contracts")


def lens_chain(rng, count=173, tight=46):
    """The candidate-9 lens shape: ``count`` chain members, ``tight`` zero-spread, the rest spread 0.9, mids near .5."""
    zero = set(rng.sample(range(count), tight))
    mids = [rng.uniform(0.46, 0.54) for _ in range(count)]
    return [(mid - spread / 2, mid + spread / 2)
            for mid, spread in ((m, 0.0 if i in zero else 0.9) for i, m in enumerate(mids))]


@pytest.mark.parametrize("seed", range(3))
def test_a_173_contract_chain_shaped_like_the_lens_repro_equals_pava_within_its_bound(seed):
    # at min_spread 1e-4 HiGHS returned "optimal" points up to 0.033 off on a chain like this; at the floor it must
    # agree with PAVA to 1e-6, and the bound must cover the (far smaller) error it actually has
    quotes = lens_chain(random.Random(f"lens {seed}"))
    ids = [f"c{i}" for i in range(len(quotes))]
    assert (len(quotes), sum(ask - bid == 0 for bid, ask in quotes)) == (173, 46)
    assert weight_ratio(quotes, FLOOR) == pytest.approx((0.9 / FLOOR) ** 2)
    out = run(rows_of(ids, quotes), min_spread=FLOOR, chains=[ids])
    want = expected_fair("chains", quotes, FLOOR)
    assert_certified(out, dict(zip(ids, want)), f"lens chain {seed}")
    for row, expected in zip(out["records"], want):
        assert row["coherent"] == pytest.approx(expected, abs=1e-6), row["id"]


# -- an exactly coherent set is returned untouched, without a solve ----------------------------------------


def spy_on_solves(monkeypatch):
    seen = []
    real = BinaryCoherence._solve

    def spy(self, solver, model):
        seen.append(model.name)
        return real(self, solver, model)

    monkeypatch.setattr(BinaryCoherence, "_solve", spy)
    return seen


def test_exactly_coherent_mids_are_returned_unchanged_and_the_projection_is_never_solved(monkeypatch):
    # mids 0.6 > 0.4 > 0.2 hold exactly (compared on exact fractions of the float mids), so only the LP runs
    seen = spy_on_solves(monkeypatch)
    rows = [contract("a", 0.59, 0.61), contract("b", 0.39, 0.41), contract("c", 0.19, 0.21)]
    out = run(rows, chains=[["a", "b", "c"]])
    assert len(seen) == 1, f"only the LP is solved, got {seen}"
    for row in out["records"]:
        assert row["coherent"] == (row["bid"] + row["ask"]) / 2 and row["coherent_status"] == "ok"


def test_mids_coherent_only_to_float_noise_still_run_the_projection_and_come_back_as_the_identity(monkeypatch):
    # 0.1 + 0.2 + 0.7 is 1 in floats but not in exact arithmetic: no tolerance-based shortcut may skip the QP
    seen = spy_on_solves(monkeypatch)
    rows = [contract("a", 0.1, 0.1), contract("b", 0.2, 0.2), contract("c", 0.7, 0.7)]
    out = run(rows, partitions=[["a", "b", "c"]])
    assert len(seen) == 2, f"the LP and the projection QP, got {seen}"
    assert fair_of(out) == {"a": pytest.approx(0.1, abs=1e-6), "b": pytest.approx(0.2, abs=1e-6),
                            "c": pytest.approx(0.7, abs=1e-6)}


def test_one_floating_contract_in_a_relation_still_projects_the_others(monkeypatch):
    # not every linked contract is two-sided, so the exact-identity shortcut does not apply
    seen = spy_on_solves(monkeypatch)
    out = run([contract("a", 0.59, 0.61), contract("b", 0.39, None)], chains=[["a", "b"]])
    assert len(seen) == 2 and fair_of(out) == {"a": pytest.approx(0.6, abs=1e-6), "b": None}


# -- solver limits and refusals ---------------------------------------------------------------------------


def test_highs_gets_both_limits_under_the_documents_own_options():
    assert binary_coherence.HIGHS_SOLVERS == ("highs", "appsi_highs")
    assert binary_coherence.DEFAULT_QP_ITERATION_LIMIT == 100_000 and binary_coherence.DEFAULT_TIME_LIMIT_S == 60.0
    for name in ("DEFAULT_QP_ITERATION_LIMIT", "DEFAULT_TIME_LIMIT_S", "HIGHS_SOLVERS", "PROJECTION_TOLERANCE"):
        assert name in binary_coherence.__all__
    for solver in binary_coherence.HIGHS_SOLVERS:
        node = BinaryCoherence("coh", {**BASE, "solver": solver, "chains": [["a", "b"]]})
        assert node._solver_options() == {"qp_iteration_limit": 100_000, "time_limit": 60.0}
        own = BinaryCoherence("coh", {**BASE, "solver": solver, "chains": [["a", "b"]],
                                      "solver_options": {"time_limit": 5.0, "threads": 1}})
        assert own._solver_options() == {"qp_iteration_limit": 100_000, "time_limit": 5.0, "threads": 1}


def test_another_solver_gets_only_the_documents_options_because_its_option_names_differ():
    node = BinaryCoherence("coh", {**BASE, "solver": "ipopt", "chains": [["a", "b"]]})
    assert node._solver_options() == {}
    own = BinaryCoherence("coh", {**BASE, "solver": "ipopt", "chains": [["a", "b"]], "solver_options": {"max_iter": 3}})
    assert own._solver_options() == {"max_iter": 3}


def test_the_limits_reach_the_solver(monkeypatch):
    seen = {}
    real = BinaryCoherence._resolve_solver

    def watch(self):
        solver = real(self)
        seen.update(dict(solver.options))
        return solver

    monkeypatch.setattr(BinaryCoherence, "_resolve_solver", watch)
    run(LADDER, solver_options={"time_limit": 30.0}, **LADDER_RELATIONS)
    assert seen["qp_iteration_limit"] == 100_000 and seen["time_limit"] == 30.0


def test_a_projection_stopped_by_its_iteration_limit_is_refused_by_name():
    rows = [contract(f"k{i}", 0.05 + 0.1 * (i % 2) * 3 - 0.01, 0.05 + 0.1 * (i % 2) * 3 + 0.01 + 0.005 * i)
            for i in range(8)]
    ids = [r["id"] for r in rows]
    with pytest.raises(RuntimeError, match=r"projection QP.*maxIterations.*solver_options"):
        run(rows, chains=[ids], solver_options={"qp_iteration_limit": 1})


def test_a_limit_stop_names_solver_options_as_the_place_to_raise_it(monkeypatch):
    class Stopped:
        class solver:
            termination_condition = "maxTimeLimit"

    monkeypatch.setattr(BinaryCoherence, "_solve", lambda self, solver, model: Stopped())
    with pytest.raises(RuntimeError, match="solver_options"):
        run(LADDER, **LADDER_RELATIONS)


def failing_solver(monkeypatch, program):
    """Make the solver RAISE on the named program ("widening" LP or "projection" QP) and solve the other for real."""
    real = BinaryCoherence._resolve_solver

    class Wrapper:
        def __init__(self, solver):
            self.solver = solver

        def solve(self, model, *args, **kwargs):
            if program in model.name or (program == "widening" and "projection" not in model.name):
                raise ValueError("boom: the backend fell over")
            return self.solver.solve(model, *args, **kwargs)

    monkeypatch.setattr(BinaryCoherence, "_resolve_solver", lambda self: Wrapper(real(self)))


@pytest.mark.parametrize("program, label", [("widening", "widening LP"), ("projection", "projection QP")])
def test_a_solver_exception_is_reraised_naming_the_node_and_the_program(monkeypatch, program, label):
    failing_solver(monkeypatch, program)
    with pytest.raises(RuntimeError) as caught:
        run(LADDER, **LADDER_RELATIONS)
    message = str(caught.value)
    assert "coh" in message and label in message and "boom: the backend fell over" in message
    assert isinstance(caught.value.__cause__, ValueError)


def test_an_optimal_projection_that_breaks_a_relation_is_refused_by_name(monkeypatch):
    class Optimal:
        class solver:
            termination_condition = "optimal"

    real = BinaryCoherence._solve

    def lying(self, solver, model):
        # the projection "solves" without moving: every variable stays at its start, the mids, which break the chain
        return Optimal() if "projection" in model.name else real(self, solver, model)

    monkeypatch.setattr(BinaryCoherence, "_solve", lying)
    assert binary_coherence.PROJECTION_TOLERANCE == 1e-7
    with pytest.raises(RuntimeError, match=r"projection QP.*chains\[0\]|chains\[0\].*projection QP"):
        run([contract("k100", 0.38, 0.40), contract("k110", 0.45, 0.47)], chains=[["k100", "k110"]])


# -- the relation guard reads every row of every relation; solved values are clipped into [0, 1] -------------


def tamper_projection(monkeypatch, edit):
    """Solve the projection for real, then let ``edit(model)`` move the solved values before the guard reads them."""
    real = BinaryCoherence._solve

    def solve(self, solver, model):
        results = real(self, solver, model)
        if "projection" in model.name:
            edit(model)
        return results

    monkeypatch.setattr(BinaryCoherence, "_solve", solve)


GUARD_CHAINS = [["a0", "a1", "a2"], ["b0", "b1", "b2"]]
GUARD_QUOTES = [(0.39, 0.41), (0.49, 0.51), (0.19, 0.21), (0.89, 0.91), (0.59, 0.61), (0.29, 0.31)]   # a0 < a1: QP runs


@pytest.mark.parametrize("index, row", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_the_relation_guard_fires_on_a_later_relation_and_a_later_row(monkeypatch, index, row):
    from pyomo.environ import value

    ids = sum(GUARD_CHAINS, [])
    run(rows_of(ids, GUARD_QUOTES), chains=GUARD_CHAINS)            # untampered, every row passes the guard
    chain = GUARD_CHAINS[index]

    def lift_above_its_predecessor(model):
        # spreads are 0.02, so a contract's variable is its probability / 0.02; chain[row + 1] rises 0.05 over chain[row]
        model.units[chain[row + 1]].set_value((value(model._probability[chain[row]]) + 0.05) / 0.02)

    tamper_projection(monkeypatch, lift_above_its_predecessor)
    with pytest.raises(RuntimeError, match=rf"projection QP.*chains\[{index}\] row {row} by 0\.05"):
        run(rows_of(ids, GUARD_QUOTES), chains=GUARD_CHAINS)


# (quotes of A, B, X with X = A - B, the contract whose optimum sits ON a bound, its quoted spread, the bound):
# the optimum is the bound itself, so a value a hair beyond it is within the certificate's tolerance
ON_A_BOUND = [
    pytest.param(((0.985, 0.995), (0.29, 0.31), (0.985, 0.995)), "A", 0.01, 1.0, id="A is 1 (the unit bound active)"),
    pytest.param(((0.01, 0.03), (0.97, 0.99), (0.20, 0.80)), "X", 0.6, 0.0, id="X is 0"),
]


@pytest.mark.parametrize("quotes, name, spread, bound", ON_A_BOUND)
def test_a_solved_value_a_hair_outside_the_unit_interval_is_clipped_into_it(monkeypatch, quotes, name, spread, bound):
    def nudge_outside(model):
        model.units[name].set_value((bound + (1e-9 if bound else -1e-9)) / spread)

    rows = [contract(cid, *q) for cid, q in zip("ABX", quotes)]
    assert fair_of(run(rows, differences=[["X", "A", "B"]]))[name] == pytest.approx(bound, abs=1e-6)
    tamper_projection(monkeypatch, nudge_outside)
    assert fair_of(run(rows, differences=[["X", "A", "B"]]))[name] == bound, "only the clip can bring it back"


# -- the certificate: a projection is accepted on its duality-gap bound, never on the solver's word -----------------

# Four wide buckets quoted 0.10 / 0.50 (spread 0.4, so s = 0.4 and weight 6.25). The mids sum to 1.2; the projection
# takes 0.05 off each, p = 0.25. In whitened units that is u = 0.625 against a centre of 0.75: P = 4 * 0.125^2 = 0.0625.
WIDE_BUCKETS = ["w0", "w1", "w2", "w3"]
WIDE_ROWS = [contract(cid, 0.10, 0.50) for cid in WIDE_BUCKETS]
WIDE_SPREAD = 0.4


def refused_bound(message):
    """The error bound a refusal message names."""
    return float(re.search(r"error bound ([0-9.e+-]+)", message).group(1))


def move_feasibly(model):
    """Move 0.03 of probability from w1 to w0: the sum, the bounds and the relation row still hold."""
    model.units["w0"].set_value(model.units["w0"].value + 0.03 / WIDE_SPREAD)
    model.units["w1"].set_value(model.units["w1"].value - 0.03 / WIDE_SPREAD)


def zero_duals(model):
    """A correct point with useless multipliers."""
    for row in list(model.dual):
        model.dual[row] = 0.0


def flip_duals(model):
    """The right magnitudes with the wrong signs."""
    for row in list(model.dual):
        model.dual[row] = -model.dual[row]


def test_a_point_that_is_the_optimum_carries_a_bound_far_inside_the_limit():
    bound = run(WIDE_ROWS, partitions=[WIDE_BUCKETS])["summary"]["projection_bound"]
    assert 0.0 <= bound < 1e-5, "the solver's own gap is ~1e-12, so the bound is its square root times s"


def test_a_point_moved_feasibly_off_the_optimum_is_refused_naming_the_node_the_projection_the_bound_and_the_limit(
        monkeypatch):
    tamper_projection(monkeypatch, move_feasibly)
    with pytest.raises(RuntimeError) as caught:
        run(WIDE_ROWS, partitions=[WIDE_BUCKETS])
    message = str(caught.value)
    assert message.startswith("coh: the projection QP") and "PROJECTION_BOUND_LIMIT 0.01" in message
    assert refused_bound(message) >= 0.03, "the bound must cover the 0.03 the point was moved by"


def test_the_bound_of_a_moved_point_covers_the_distance_it_was_moved(monkeypatch):
    # lift the limit so the run reports instead of refusing: the bound is the certificate, the limit only the verdict
    monkeypatch.setattr(binary_coherence, "PROJECTION_BOUND_LIMIT", 10.0)
    tamper_projection(monkeypatch, move_feasibly)
    out = run(WIDE_ROWS, partitions=[WIDE_BUCKETS])
    fair = fair_of(out)
    assert [abs(fair[cid] - 0.25) for cid in ("w0", "w1")] == [pytest.approx(0.03, abs=1e-6)] * 2
    assert out["summary"]["projection_bound"] >= 0.03


def test_a_correct_point_with_zeroed_multipliers_is_refused_and_its_bound_is_the_closed_form(monkeypatch):
    # lambda = 0: the dual value is 0 and the Lagrangian at x is P, so the bound is s * (sqrt(P) + sqrt(P)) with
    # P = 4 * (0.125)^2: 0.4 * 2 * 0.25 = 0.2
    tamper_projection(monkeypatch, zero_duals)
    with pytest.raises(RuntimeError, match="coh: the projection QP.*error bound 0.2 .*PROJECTION_BOUND_LIMIT 0.01"):
        run(WIDE_ROWS, partitions=[WIDE_BUCKETS])
    monkeypatch.setattr(binary_coherence, "PROJECTION_BOUND_LIMIT", 10.0)
    assert run(WIDE_ROWS, partitions=[WIDE_BUCKETS])["summary"]["projection_bound"] == pytest.approx(0.2, rel=1e-6)


def test_a_floating_contract_takes_the_end_of_its_box_the_multipliers_favour(monkeypatch):
    # f quotes a bid only, so it floats in [0, 1]; the partition forces f = 0.4 and the buckets stay at their mids.
    # With a multiplier of 2 injected on the row the Lagrangian pulls g = 0.8 on each bucket's unit (centre 0.75,
    # ubar 1.15) and 2 on f, which sits at 1: D = 2 + 2 * (0.16 - 0.92) - 2 = -1.52. At the solver's point
    # Lx = 2 + 2 * (0 - 0.6) - 0.8 = 0 and P = 0, so the bound is 0.4 * 2 * sqrt(1.52).
    def inject(model):
        for row in list(model.dual):
            model.dual[row] = 2.0

    monkeypatch.setattr(binary_coherence, "PROJECTION_BOUND_LIMIT", 10.0)
    tamper_projection(monkeypatch, inject)
    rows = [contract("w0", 0.10, 0.50), contract("w1", 0.10, 0.50), contract("f", 0.5, 1.0)]
    bound = run(rows, partitions=[["w0", "w1", "f"]])["summary"]["projection_bound"]
    assert bound == pytest.approx(0.4 * 2 * math.sqrt(1.52), rel=1e-6)


def test_a_floating_contracts_raw_value_outside_its_box_is_read_clipped(monkeypatch):
    # a + f = 1 with f floating; the point is moved to a = -0.2, f = 1.2 (the row still holds) and a multiplier of 2
    # is injected. Clipped into the box that is a = 0, f = 1: D = 2 + (0.16 - 0.92) - 2 = -0.76, Lx = 2 + 0.5625 - 2
    # and P = 0.5625, so the bound is 0.4 * 2 * sqrt(1.3225) = 0.92 (the raw f would give 0.844).
    def leave_the_box(model):
        model.units["a"].set_value(-0.2 / 0.4)
        model.floating["f"].set_value(1.2)
        for row in list(model.dual):
            model.dual[row] = 2.0

    monkeypatch.setattr(binary_coherence, "PROJECTION_BOUND_LIMIT", 10.0)
    tamper_projection(monkeypatch, leave_the_box)
    bound = run([contract("a", 0.10, 0.50), contract("f", 0.5, 1.0)], partitions=[["a", "f"]])[
        "summary"]["projection_bound"]
    assert bound == pytest.approx(0.92, rel=1e-6)


def test_the_bound_is_taken_at_the_clipped_point_that_is_returned_not_at_the_raw_one(monkeypatch):
    # mids 0.50 and 0.54 (s = 0.4) sum to 1.04; the point is moved to p = (1.05, -0.05), which still sums to 1 and
    # keeps the row, but is outside [0, 1]. It is RETURNED as (1, 0), so the zeroed-multiplier bound is read there:
    # P = ((1 - 0.5) / 0.4)^2 + ((0 - 0.54) / 0.4)^2 = 3.385 and the bound is 0.4 * 2 * sqrt(3.385) = 1.472
    # (the raw point would give 1.613).
    def leave_the_box(model):
        model.units["a"].set_value(1.05 / 0.4)
        model.units["b"].set_value(-0.05 / 0.4)
        zero_duals(model)

    monkeypatch.setattr(binary_coherence, "PROJECTION_BOUND_LIMIT", 10.0)
    tamper_projection(monkeypatch, leave_the_box)
    out = run([contract("a", 0.30, 0.70), contract("b", 0.34, 0.74)], partitions=[["a", "b"]])
    assert fair_of(out) == {"a": 1.0, "b": 0.0}
    assert out["summary"]["projection_bound"] == pytest.approx(0.4 * 2 * math.sqrt(1.5625 + 1.8225), rel=1e-6)


def test_a_nan_multiplier_is_no_evidence_and_the_projection_is_refused(monkeypatch):
    def poison(model):
        for row in list(model.dual):
            model.dual[row] = math.nan

    tamper_projection(monkeypatch, poison)
    with pytest.raises(RuntimeError, match="coh: the projection QP.*error bound inf .*PROJECTION_BOUND_LIMIT 0.01"):
        run(WIDE_ROWS, partitions=[WIDE_BUCKETS])


def test_a_multiplier_of_the_wrong_sign_on_an_inequality_row_counts_as_zero(monkeypatch):
    # a one-way row (a chain) only accepts a multiplier of its own sign: a flipped one is clamped to 0, so flipping
    # every dual of a chain-only system gives the zeroed bound. k1 0.40 +/- 0.01 under k2 0.50 +/- 0.02 pool to 0.42
    # each: u = (21, 10.5) against centres (20, 12.5), P = 1 + 4 = 5 and the bound is 0.04 * 2 * sqrt(5)
    rows, chains = [contract("k1", 0.39, 0.41), contract("k2", 0.48, 0.52)], [["k1", "k2"]]
    monkeypatch.setattr(binary_coherence, "PROJECTION_BOUND_LIMIT", 10.0)
    bounds = {}
    for label, edit in (("zeroed", zero_duals), ("flipped", flip_duals)):
        with monkeypatch.context() as patch:
            tamper_projection(patch, edit)
            bounds[label] = run(rows, chains=chains)["summary"]["projection_bound"]
    assert bounds["zeroed"] == pytest.approx(0.04 * 2 * math.sqrt(5), rel=1e-6)
    assert bounds["flipped"] == pytest.approx(bounds["zeroed"], rel=1e-9)


def test_a_rows_constant_term_moves_to_the_bound_of_the_lagrangian():
    # the Lagrangian uses only variable terms: a multiplier of 2 on x + 0.25 >= 0.5 pulls 2 on x and has the bound
    # 0.5 - 0.25 (no relation today carries a constant, so only this hand-built row exercises the subtraction)
    import types

    from pyomo.environ import Constraint, ConcreteModel, Suffix, Var

    model = ConcreteModel()
    model.dual = Suffix(direction=Suffix.IMPORT)
    model.x = Var(bounds=(0.0, 1.0))
    model.rows_0 = Constraint(expr=model.x + 0.25 >= 0.5)
    model.dual[model.rows_0] = 2.0
    node = BinaryCoherence("coh", {**BASE, "chains": [["a", "b"]]})
    pull, offset = node._relation_pull(model, [types.SimpleNamespace(kind="rows", index=0)])
    assert pull == {id(model.x): pytest.approx(2.0)} and offset == pytest.approx(0.5)


# -- what projection_bound says: 0.0 without a solve, None without a projection, a float otherwise ---------------


def test_the_projection_bound_is_zero_on_the_exact_shortcut_where_nothing_was_solved(monkeypatch):
    seen = spy_on_solves(monkeypatch)
    rows = [contract("a", 0.59, 0.61), contract("b", 0.39, 0.41), contract("c", 0.19, 0.21)]
    bound = run(rows, chains=[["a", "b", "c"]])["summary"]["projection_bound"]
    assert bound == 0.0 and isinstance(bound, float) and len(seen) == 1, "only the LP was solved"


@pytest.mark.parametrize("rows, relations", [
    pytest.param([contract("k1", 0.5, 0.52)], {"chains": [["k1", "ghost"]]}, id="no relation is active"),
    pytest.param([contract("a", None, 0.5), contract("b", 0.6, None)], {"chains": [["a", "b"]]},
                 id="nothing two-sided to project"),
])
def test_the_projection_bound_is_none_when_no_projection_ran(rows, relations):
    assert run(rows, **relations)["summary"]["projection_bound"] is None


def test_the_projection_bound_is_a_float_inside_the_limit_when_a_projection_was_solved(monkeypatch):
    seen = spy_on_solves(monkeypatch)
    bound = run([contract("k1", 0.39, 0.41), contract("k2", 0.48, 0.52)], chains=[["k1", "k2"]])[
        "summary"]["projection_bound"]
    assert len(seen) == 2 and isinstance(bound, float) and 0.0 <= bound <= binary_coherence.PROJECTION_BOUND_LIMIT
    mixed = run([contract("a", 0.59, 0.61), contract("b", 0.39, None)], chains=[["a", "b"]])
    assert isinstance(mixed["summary"]["projection_bound"], float), "a floating member does not skip the QP"


# -- tolerance 0, and the output-column collisions ------------------------------------------------------------


def test_tolerance_zero_calls_a_coherent_set_feasible_and_reports_a_violation_of_one_nano_unit():
    coherent = run(LADDER, tolerance=0, **LADDER_RELATIONS)["arbitrage"]
    assert coherent["feasible"] is True and coherent["legs"] == [] and coherent["violation"] <= 1e-12
    rows = [contract("k100", 0.38, 0.40), contract("k110", 0.40 + 1e-9, 0.47)]       # k110 bids 1e-9 over k100's ask
    tight = {"primal_feasibility_tolerance": 1e-10}                                    # see the next test: why
    strict = run(rows, tolerance=0, chains=[["k100", "k110"]], solver_options=tight)["arbitrage"]
    assert strict["feasible"] is False and strict["violation"] == pytest.approx(1e-9, rel=1e-3)
    assert {leg["id"]: leg["side"] for leg in strict["legs"]} == {"k100": "buy", "k110": "sell"}
    default = run(rows, chains=[["k100", "k110"]], solver_options=tight)["arbitrage"]
    assert default["feasible"] is True, "the default tolerance 1e-7 reads a 1e-9 violation as coherent"


def test_the_lp_resolves_a_violation_only_down_to_highs_own_feasibility_tolerance():
    # recorded limit (module docstring): at HiGHS' default primal feasibility tolerance 1e-7 the LP returns 0 for a
    # smaller violation whatever `tolerance` says; one above it is reported to full precision
    def inverted_by(over):
        return [contract("k100", 0.38, 0.40), contract("k110", 0.40 + over, 0.47)]

    below = run(inverted_by(5e-8), tolerance=0, chains=[["k100", "k110"]])["arbitrage"]
    assert below["feasible"] is True and below["violation"] == 0.0
    above = run(inverted_by(5e-7), tolerance=0, chains=[["k100", "k110"]])["arbitrage"]
    assert above["feasible"] is False and above["violation"] == pytest.approx(5e-7, rel=1e-6)


SIZED = {**BASE, "chains": [["a", "b"]], "bid_size_field": "bs", "ask_size_field": "az"}


@pytest.mark.parametrize("field", ["id_field", "bid_field", "ask_field", "bid_size_field", "ask_size_field"])
@pytest.mark.parametrize("column", ["coherent", "coherent_status"])
def test_a_fair_field_that_would_overwrite_any_input_column_or_the_status_column_is_refused(field, column):
    # fair_field 'coherent' writes 'coherent' and 'coherent_status': no input knob may name either
    problems = BinaryCoherence.validate_params({**SIZED, field: column})
    assert any("overwrite" in p and column in p for p in problems), problems
    assert not BinaryCoherence.validate_params(SIZED), "the same params with distinct columns are fine"


def test_an_empty_id_field_is_refused_by_name():
    problems = BinaryCoherence.validate_params({**BASE, "id_field": "", "chains": [["a", "b"]]})
    assert any("id_field" in p for p in problems)


# -- the QP hint is for a solver that may not take a QP, never for the one that does ---------------------------


class _Raises:
    def solve(self, model):
        raise ValueError("boom")


def solver_error(solver, program):
    """The message of the RuntimeError ``_solve`` raises when ``solver`` raises on ``program`` (a stub model)."""
    import types

    node = BinaryCoherence("coh", {**BASE, "solver": solver, "chains": [["a", "b"]]})
    with pytest.raises(RuntimeError) as caught:
        node._solve(_Raises(), types.SimpleNamespace(_program=program))
    return str(caught.value)


@pytest.mark.parametrize("solver, hinted", [("appsi_highs", True), ("ipopt", True), ("highs", False)])
def test_the_qp_hint_is_given_unless_the_solver_is_the_interface_that_takes_a_qp(solver, hinted):
    message = solver_error(solver, "the projection QP")
    assert "boom" in message and f"solver {solver!r} raised on the projection QP" in message
    assert ("needs a QP solver, e.g. 'highs'" in message) is hinted


def test_the_qp_hint_is_never_given_for_the_lp():
    for solver in ("highs", "appsi_highs", "ipopt"):
        assert "QP solver" not in solver_error(solver, "the widening LP")


def test_a_highs_failure_on_the_projection_does_not_blame_the_solver_choice(monkeypatch):
    failing_solver(monkeypatch, "projection")
    with pytest.raises(RuntimeError) as caught:
        run(LADDER, **LADDER_RELATIONS)
    assert "boom: the backend fell over" in str(caught.value) and "QP solver" not in str(caught.value)



# -- the side rule: one reading of a quote (ruling B) ------------------------------------------------------

# (bid, ask, which side is usable): a chain puts the contract where only its usable side can matter
ONE_SIDED = [
    pytest.param(0.4, None, "sale", id="bid only"),
    pytest.param(1.0, None, "sale", id="bid of one is a sale at one"),
    pytest.param(0.4, 0.0, "sale", id="an ask of zero is no offer"),
    pytest.param(0.4, 1.0, "sale", id="an ask of one is non-binding"),
    pytest.param(0.4, 1.2, "sale", id="an ask above one is non-binding"),
    pytest.param(None, 0.6, "purchase", id="ask only"),
    pytest.param(0.0, 0.6, "purchase", id="a bid of zero is no sale"),
    pytest.param(-0.1, 0.6, "purchase", id="a negative bid is no sale"),
]


@pytest.mark.parametrize("bid, ask, usable", ONE_SIDED)
def test_a_one_sided_contract_stays_in_its_relation_floats_and_never_trades_its_missing_side(bid, ask, usable):
    if usable == "sale":
        # x <= y <= 0.15 yet x bids `bid`: sell x at its bid, buy y at 0.15
        rows, chains = [contract("y", 0.10, 0.15), contract("x", bid, ask)], [["y", "x"]]
        want = {"x": ("sell", bid), "y": ("buy", 0.15)}
    else:
        # x >= y >= 0.70 yet x asks `ask`: buy x at its ask, sell y at 0.70
        rows, chains = [contract("x", bid, ask), contract("y", 0.70, 0.72)], [["x", "y"]]
        want = {"x": ("buy", ask), "y": ("sell", 0.70)}
    out = run(rows, chains=chains)
    arb = out["arbitrage"]
    assert arb["feasible"] is False
    assert {leg["id"]: (leg["side"], leg["price"]) for leg in arb["legs"]} == want
    assert arb["credit_per_unit"] == pytest.approx(abs(want["x"][1] - want["y"][1]))
    assert out["summary"]["skipped_relations"] == [] and out["summary"]["relations"] == 1
    status = {r["id"]: r["coherent_status"] for r in out["records"]}
    assert status == {"x": "bad_quote", "y": "ok"}
    assert fair_of(out)["x"] is None and fair_of(out)["y"] is not None


def test_two_one_sided_contracts_trade_only_the_sides_they_have():
    # a only asks 0.50, b only bids 0.60, a >= b: buy a at 0.50, sell b at 0.60, credit 0.10; neither is projected
    out = run([contract("a", None, 0.50), contract("b", 0.60, None)], chains=[["a", "b"]])
    arb = out["arbitrage"]
    assert arb["feasible"] is False and arb["credit_per_unit"] == pytest.approx(0.10)
    assert {leg["id"]: (leg["side"], leg["price"]) for leg in arb["legs"]} == {"a": ("buy", 0.50), "b": ("sell", 0.60)}
    assert [r["coherent_status"] for r in out["records"]] == ["bad_quote", "bad_quote"]
    assert [r["coherent"] for r in out["records"]] == [None, None]
    assert out["summary"]["projected"] == 0


def test_a_zero_ask_is_no_offer_never_a_free_contract():
    # a (0, 0) used to report "buy a at 0.0" to complete the partition; it floats, so the set is feasible
    out = run([contract("a", 0.0, 0.0), contract("b", 0.5, 0.6)], partitions=[["a", "b"]])
    arb = out["arbitrage"]
    assert arb["feasible"] is True and arb["legs"] == [] and arb["relations"] == []
    status = {r["id"]: r["coherent_status"] for r in out["records"]}
    assert status == {"a": "bad_quote", "b": "ok"}
    assert fair_of(out)["a"] is None and fair_of(out)["b"] == pytest.approx(0.55, abs=1e-6)


def test_contracts_with_no_usable_side_at_all_leave_the_relation_feasible_and_unsolved(monkeypatch):
    seen = spy_on_solves(monkeypatch)
    out = run([contract("a", None, None), contract("b", 0.0, 0.0), contract("c", -1.0, 1.0)],
              partitions=[["a", "b", "c"]])
    assert out["arbitrage"]["feasible"] is True and out["summary"]["relations"] == 1
    assert [r["coherent_status"] for r in out["records"]] == ["bad_quote"] * 3
    assert len(seen) == 1, f"nothing to project, so no projection QP: {seen}"


def test_a_one_sided_contract_outside_every_relation_is_still_reported_bad_quote():
    out = run([contract("a", 0.4, None), contract("k1", 0.5, 0.52), contract("k2", 0.4, 0.42)], chains=[["k1", "k2"]])
    assert [r["coherent_status"] for r in out["records"]] == ["bad_quote", "ok", "ok"]
    assert out["records"][0]["coherent"] is None


@pytest.mark.parametrize("bid, ask", [(1.0000001, None), (None, -0.1), (0.2, 0.1)])
def test_a_problem_on_one_side_alone_is_refused_and_skips_its_relation_by_name(bid, ask):
    out = run([contract("k1", bid, ask), contract("k2", 0.1, 0.2)], chains=[["k1", "k2"]])
    assert out["records"][0]["coherent_status"] == "bad_quote"
    assert out["summary"]["skipped_relations"][0]["bad_quote"] == ["k1"] and out["summary"]["relations"] == 0


# -- every relation encoding at every position (the family D4 names) --------------------------------------


def chain_quotes(n):
    """A coherent n-chain: mids 0.9, 0.75, ... each quoted +/-0.01."""
    mids = [round(0.9 - 0.15 * j, 2) for j in range(n)]
    return [[round(m - 0.01, 2), round(m + 0.01, 2)] for m in mids]


def rows_of(ids, quotes):
    return [contract(cid, bid, ask) for cid, (bid, ask) in zip(ids, quotes)]


CHAIN_POSITIONS = [(n, k) for n in range(2, 6) for k in range(n - 1)]


@pytest.mark.parametrize("n, k", CHAIN_POSITIONS)
def test_a_chain_inversion_is_found_at_each_adjacent_position(n, k):
    # the bid of contract k+1 sits 0.05 above the ask of contract k: sell k+1 at its bid, buy k at its ask
    ids, quotes = [f"c{j}" for j in range(n)], chain_quotes(n)
    quotes[k + 1] = [quotes[k][1] + 0.05, quotes[k][1] + 0.07]
    arb = run(rows_of(ids, quotes), chains=[ids])["arbitrage"]
    assert arb["feasible"] is False and arb["violation"] == pytest.approx(0.05, abs=1e-9)
    assert {leg["id"]: (leg["side"], leg["units"], leg["price"]) for leg in arb["legs"]} == {
        ids[k + 1]: ("sell", pytest.approx(1.0), quotes[k + 1][0]), ids[k]: ("buy", pytest.approx(1.0), quotes[k][1])}
    assert arb["credit_per_unit"] == pytest.approx(quotes[k + 1][0] - quotes[k][1])
    assert arb["relations"] == [{"kind": "chains", "index": 0, "ids": ids}]


@pytest.mark.parametrize("n, k", CHAIN_POSITIONS)
def test_a_chain_whose_neighbours_just_touch_is_feasible_at_each_adjacent_position(n, k):
    ids, quotes = [f"c{j}" for j in range(n)], chain_quotes(n)
    quotes[k + 1] = [quotes[k][1], quotes[k][1] + 0.02]     # bid of k+1 == ask of k, exactly
    arb = run(rows_of(ids, quotes), chains=[ids])["arbitrage"]
    assert arb["feasible"] is True and arb["legs"] == [] and arb["relations"] == []


PARTITION_POSITIONS = [(n, k, way) for n in range(2, 6) for k in range(n) for way in ("sell", "buy")]


@pytest.mark.parametrize("n, k, way", PARTITION_POSITIONS)
def test_a_partition_arbitrage_is_found_whichever_bucket_carries_the_excess(n, k, way):
    ids, mid = [f"b{j}" for j in range(n)], 1.0 / n
    quotes = [[mid - 0.01, mid + 0.01] for _ in ids]        # bids sum to 1 - 0.01 n, asks to 1 + 0.01 n
    if way == "sell":                                        # bids sum to 1.03
        quotes[k] = [mid + 0.01 * n + 0.02, mid + 0.01 * n + 0.04]
    else:                                                    # asks sum to 0.97
        quotes[k] = [mid - 0.01 * n - 0.04, mid - 0.01 * n - 0.02]
    arb = run(rows_of(ids, quotes), partitions=[ids])["arbitrage"]
    assert arb["feasible"] is False and arb["violation"] == pytest.approx(0.03, abs=1e-9)
    side, price = ("sell", 0) if way == "sell" else ("buy", 1)
    assert {leg["id"]: (leg["side"], leg["units"], leg["price"]) for leg in arb["legs"]} == {
        cid: (side, pytest.approx(1.0), q[price]) for cid, q in zip(ids, quotes)}
    assert arb["relations"] == [{"kind": "partitions", "index": 0, "ids": ids}]


def test_a_difference_is_found_in_each_of_its_two_directions():
    # X = A - B. Direction 1: X bids 0.35 over A - B <= ask A - bid B = 0.32: sell X, buy A, sell B.
    over = run([contract("A", 0.50, 0.52), contract("B", 0.20, 0.22), contract("X", 0.35, 0.37)],
               differences=[["X", "A", "B"]])["arbitrage"]
    assert {leg["id"]: (leg["side"], leg["price"]) for leg in over["legs"]} == {
        "X": ("sell", 0.35), "A": ("buy", 0.52), "B": ("sell", 0.20)}
    assert over["credit_per_unit"] == pytest.approx(0.03)
    # Direction 2: X asks 0.25 under A - B >= bid A - ask B = 0.28: buy X, sell A, buy B.
    under = run([contract("A", 0.50, 0.52), contract("B", 0.20, 0.22), contract("X", 0.23, 0.25)],
                differences=[["X", "A", "B"]])["arbitrage"]
    assert {leg["id"]: (leg["side"], leg["price"]) for leg in under["legs"]} == {
        "X": ("buy", 0.25), "A": ("sell", 0.50), "B": ("buy", 0.22)}
    assert under["credit_per_unit"] == pytest.approx(0.03)
    assert [r["index"] for r in over["relations"] + under["relations"]] == [0, 0]


RELATION_IDS = [("chains", ["c0", "c1", "c2"]), ("partitions", ["b0", "b1", "b2"]), ("differences", ["X", "A", "B"])]
RELATION_POSITIONS = [(kind, ids, i) for kind, ids in RELATION_IDS for i in range(3)]
GOOD_QUOTES = {"c0": (0.8, 0.82), "c1": (0.5, 0.52), "c2": (0.2, 0.22), "b0": (0.3, 0.32), "b1": (0.3, 0.32),
               "b2": (0.3, 0.32), "X": (0.3, 0.32), "A": (0.8, 0.82), "B": (0.5, 0.52)}


@pytest.mark.parametrize("kind, ids, i", RELATION_POSITIONS)
def test_a_relation_is_skipped_naming_whichever_of_its_ids_has_no_row(kind, ids, i):
    rows = [contract(cid, *GOOD_QUOTES[cid]) for j, cid in enumerate(ids) if j != i]
    out = run(rows, **{kind: [ids]})
    assert out["summary"]["skipped_relations"] == [
        {"kind": kind, "index": 0, "ids": ids, "missing": [ids[i]], "bad_quote": []}]
    assert out["summary"]["relations"] == 0 and out["arbitrage"]["feasible"] is True
    assert {r["coherent_status"] for r in out["records"]} == {"unrelated"}


@pytest.mark.parametrize("kind, ids, i", RELATION_POSITIONS)
def test_a_relation_is_skipped_naming_whichever_of_its_ids_has_an_unusable_quote(kind, ids, i):
    rows = [contract(cid, *GOOD_QUOTES[cid]) for cid in ids]
    rows[i] = contract(ids[i], 0.6, 0.4)                    # crossed
    out = run(rows, **{kind: [ids]})
    assert out["summary"]["skipped_relations"] == [
        {"kind": kind, "index": 0, "ids": ids, "missing": [], "bad_quote": [ids[i]]}]
    assert out["records"][i]["coherent_status"] == "bad_quote"
    assert out["summary"]["relations"] == 0 and out["arbitrage"]["feasible"] is True


# -- a reported position pays in the worst outcome, on random strike worlds -----------------------------


def strike_world(m):
    """Contracts on strikes K1 < ... < Km; the m + 1 regions are S < K1, K_j <= S < K_{j+1}, S >= Km.

    Returns ``(payout, chain, differences, partition)``: each contract's 0/1 payout per region, derived from its
    own strikes (above pays where S >= K_j, between where K_j <= S < K_{j+1}, below where S < K_1), and the
    relations that are TRUE in this world.
    """
    strikes = [100 + 10 * j for j in range(m)]
    sampled = [strikes[0] - 5, *(k + 5 for k in strikes)]         # one S inside each region
    spans = {"below": (None, strikes[0])}
    spans.update({f"above{j}": (strikes[j - 1], None) for j in range(1, m + 1)})
    spans.update({f"between{j}": (strikes[j - 1], strikes[j]) for j in range(1, m)})
    payout = {cid: [int((low is None or s >= low) and (high is None or s < high)) for s in sampled]
              for cid, (low, high) in spans.items()}
    chain = [f"above{j}" for j in range(1, m + 1)]
    differences = [[f"between{j}", f"above{j}", f"above{j + 1}"] for j in range(1, m)]
    partition = ["below", *[f"between{j}" for j in range(1, m)], f"above{m}"]
    return payout, chain, differences, partition


def coherent_quotes(rng, count, ordered):
    """Quotes that bracket one coherent point: sorted descending (a chain) or summing to one (a partition)."""
    raw = [rng.random() + 0.05 for _ in range(count)]
    points = sorted(raw, reverse=True) if ordered else [x / sum(raw) for x in raw]
    if ordered:
        points = [p / max(raw) * 0.95 for p in points]
    half = rng.choice((0.0, 0.005, 0.02))
    return [(p - half, p + half) for p in points]


def world_quotes(rng, ids, family):
    if rng.random() < 0.35:
        return coherent_quotes(rng, len(ids), ordered=family == "chains")
    return [draw_quote(rng) for _ in ids]


def worst_region_profit(arb, payout):
    """The position's profit in the worst region, recomputed from its legs alone."""
    credit = sum(leg["units"] * leg["price"] * (1 if leg["side"] == "sell" else -1) for leg in arb["legs"])
    assert credit == pytest.approx(arb["credit_per_unit"], abs=1e-9)
    regions = len(next(iter(payout.values())))
    return min(credit + sum(leg["units"] * payout[leg["id"]][r] * (1 if leg["side"] == "buy" else -1)
                            for leg in arb["legs"]) for r in range(regions))


def bounds_of(bid, ask):
    """The probability interval a quote allows under the side rule: bid binds above 0, ask binds below 1."""
    return (bid if 0 < bid <= 1 else 0.0), (ask if 0 < ask < 1 else 1.0)


VERDICT_SLACK = 1e-7   # the default tolerance restated: a violation at or below it is read as coherent


def chain_feasible(quotes):
    """Greedy: keep each p as large as the asks and the chain allow; feasible iff it still meets every bid."""
    p = 1.0
    for bid, ask in quotes:
        lo, hi = bounds_of(bid, ask)
        p = min(p, hi)
        if p < lo - VERDICT_SLACK:
            return False
    return True


def partition_feasible(quotes):
    limits = [bounds_of(bid, ask) for bid, ask in quotes]
    return sum(lo for lo, _ in limits) <= 1 + VERDICT_SLACK and sum(hi for _, hi in limits) >= 1 - VERDICT_SLACK


@pytest.mark.parametrize("family", ["chains", "partitions", "all"])
def test_every_reported_riskless_position_pays_in_the_worst_outcome(family):
    rng = random.Random(f"strike worlds {family}")
    verdicts = {True: 0, False: 0}
    for case in range(120):
        payout, chain, differences, partition = strike_world(rng.randint(2, 4))
        if family == "chains":
            ids, relations = chain, {"chains": [chain]}
        elif family == "partitions":
            ids, relations = partition, {"partitions": [partition]}
        else:
            ids, relations = list(payout), {"chains": [chain], "differences": differences, "partitions": [partition]}
        quotes = world_quotes(rng, ids, family)
        where = f"{family} case {case}: {dict(zip(ids, quotes))}"
        arb = run(rows_of(ids, quotes), **relations)["arbitrage"]
        verdicts[arb["feasible"]] += 1
        if family == "chains":
            assert arb["feasible"] is chain_feasible(quotes), where
        if family == "partitions":
            assert arb["feasible"] is partition_feasible(quotes), where
        if not arb["feasible"]:
            worst = worst_region_profit(arb, payout)
            assert worst > 0 and worst >= arb["violation"] - 1e-7, where
    assert verdicts[True] > 5 and verdicts[False] > 5, f"the draws must reach both verdicts, got {verdicts}"


# -- params and contract -------------------------------------------------------------------------------


@pytest.mark.parametrize("bad, needle", [
    ({"chains": [["a"]]}, "chains"),
    ({"partitions": [["a"]]}, "partitions"),
    ({"chains": [["a", 1.5]]}, "chains"),
    ({"chains": [["a", "b"]], "bid_size_field": "", "ask_size_field": "az"}, "bid_size_field"),
    ({"chains": "a,b"}, "chains"),
    ({"partitions": [["a", "a"]]}, "partitions"),
    ({"differences": [["x", "a"]]}, "differences"),
    ({"differences": [["x", "a", "x"]]}, "differences"),
    ({}, "relation"),
    ({"chains": [["a", "b"]], "min_spread": 0}, "min_spread"),
    ({"chains": [["a", "b"]], "tolerance": -1}, "tolerance"),
    ({"chains": [["a", "b"]], "bid_size_field": "bs"}, "size"),
    ({"chains": [["a", "b"]], "fair_field": "bid"}, "overwrite"),
    ({"chains": [["a", "b"]], "typo": 1}, "unknown"),
])
def test_params_are_refused_by_name(bad, needle):
    assert any(needle in p for p in BinaryCoherence.validate_params({**BASE, **bad}))


@pytest.mark.parametrize("name", ["id_field", "bid_field", "ask_field", "fair_field", "min_spread", "solver"])
def test_every_required_knob_is_named_when_missing(name):
    params = {**{k: v for k, v in BASE.items() if k != name}, "chains": [["a", "b"]]}
    assert any(name in p for p in BinaryCoherence.validate_params(params))


def test_a_solver_that_cannot_take_the_quadratic_projection_is_refused_by_name():
    # pyomo's appsi HiGHS interface refuses a degree-2 objective; the refusal must name the projection
    with pytest.raises(RuntimeError, match="coh.*projection QP.*quadratic objective"):
        run(LADDER, solver="appsi_highs", **LADDER_RELATIONS)


def test_it_subclasses_the_pyomo_doorway_with_a_non_capital_role():
    assert issubclass(BinaryCoherence, PyomoSolve)
    assert BinaryCoherence.role == "transform"
    assert BinaryCoherence.outputs == ("records", "arbitrage", "summary")
    assert set(RELATIONS) == {"partitions", "chains", "differences"}
    assert all(issubclass(cls, Relation) for cls in RELATIONS.values())
    with pytest.raises(TypeError):
        Relation(0, ["a", "b"])  # abstract
    assert binary_coherence.NODE_KINDS == ()
    assert not [n for n in binary_coherence.__all__ if n.startswith("_")]


def test_duplicate_ids_and_non_list_records_are_refused_at_the_input():
    node = BinaryCoherence("coh", {**BASE, "chains": [["a", "b"]]})
    assert node.validate_inputs({"records": [contract("a", .1, .2), contract("a", .1, .2)]})
    assert node.validate_inputs({"records": iter([])})
    assert node.validate_inputs({"records": [contract("a", .1, .2)]}) == []


# -- every id through row_key: the SPEC table end to end (R3/R5) -----------------------------------------


def _spec(*kinds):
    rows = [r for r in SPEC if r[2] in kinds]
    return pytest.mark.parametrize("a, b, expected", rows, ids=[i for i, r in zip(SPEC_IDS, SPEC) if r[2] in kinds])


def _row(cid, bid, ask):
    row = {"bid": bid, "ask": ask}
    if cid is not ABSENT:
        row["id"] = cid
    return row


@_spec("same", "different")
def test_the_spec_table_decides_duplicate_record_ids(a, b, expected):
    node = BinaryCoherence("coh", {**BASE, "chains": [["p", "q"]]})
    problems = node.validate_inputs({"records": [_row(a, .1, .2), _row(b, .1, .2)]})
    assert bool(problems) is (expected == "same")


@_spec("refused", "missing")
def test_the_spec_table_refuses_a_record_id_that_is_not_a_key(a, b, expected):
    problems = BinaryCoherence("coh", {**BASE, "chains": [["p", "q"]]}).validate_inputs({"records": [_row(a, .1, .2)]})
    assert len(problems) == 1 and ("missing" if expected == "missing" else "refused") in problems[0]


@_spec("same", "different")
def test_the_spec_table_decides_whether_a_relation_id_names_a_row(a, b, expected):
    # the relation declares a; the row carries b. The inverted ladder is found only when they are one id.
    out = run([_row(b, 0.38, 0.40), contract("k110", 0.45, 0.47)], chains=[[a, "k110"]])
    if expected == "same":
        assert out["arbitrage"]["feasible"] is False
        assert {leg["side"] for leg in out["arbitrage"]["legs"]} == {"buy", "sell"}
        assert out["records"][0]["coherent_status"] == STATUS_OK
    else:
        assert out["summary"]["skipped_relations"][0]["missing"] == [binary_curve.row_key(a)[0]]
        assert out["records"][0]["coherent_status"] == "unrelated"


@_spec("refused", "missing")
def test_the_spec_table_refuses_a_relation_id_that_is_not_a_key_by_name(a, b, expected):
    for kind in ("chains", "partitions"):
        problems = BinaryCoherence.validate_params({**BASE, kind: [[spec_cell(a), "k110"]]})
        assert any(kind in p and "cannot be a key" in p for p in problems), problems


class _EqLiar(str):
    """A str id whose ``__eq__`` lies: only row_key's reading of its characters may match it."""

    def __eq__(self, other):
        return False

    __hash__ = str.__hash__


@pytest.mark.parametrize("twin", [np.str_("a"), _EqLiar("a")])
def test_relation_ids_are_normalised_before_the_repeat_check(twin):
    problems = BinaryCoherence.validate_params({**BASE, "chains": [["a", twin]]})
    assert any("more than once" in p for p in problems)


def test_a_record_id_whose_eq_lies_joins_its_relation_by_its_characters():
    out = run([_row(_EqLiar("k100"), 0.38, 0.40), contract("k110", 0.45, 0.47)], chains=[["k100", "k110"]])
    assert out["arbitrage"]["feasible"] is False and out["records"][0]["coherent_status"] == STATUS_OK
    assert not BinaryCoherence.validate_params({**BASE, "chains": [[3, "3"]]}), "3 and '3' are two ids"


def test_a_row_whose_id_is_not_a_key_is_marked_by_name_when_run_directly():
    out = run([_row(True, 0.38, 0.40), contract("k110", 0.45, 0.47), _row(ABSENT, 0.1, 0.2)],
              chains=[["k100", "k110"]])
    assert [r["coherent_status"] for r in out["records"]] == ["bad_id", "unrelated", "bad_id"]
    assert "bad_id" in binary_coherence.STATUSES


def test_numpy_and_enum_ids_join_their_plain_relation_ids():
    rows = [_row(np.str_("k100"), 0.38, 0.40), _row(Three.THREE, 0.45, 0.47)]
    arb = run(rows, chains=[["k100", np.int64(3)]])["arbitrage"]
    assert {leg["id"]: leg["side"] for leg in arb["legs"]} == {"k100": "buy", 3: "sell"}
    assert all(type(leg["id"]) in (str, int) for leg in arb["legs"])


def test_the_coherence_pack_has_no_private_id_rule_of_its_own():
    import inspect

    source = inspect.getsource(binary_coherence)
    assert "def _id_ok" not in source and "ids.count(" not in source
    assert binary_coherence.row_key is binary_curve.row_key


def test_the_coherence_pack_uses_the_binary_curve_owners_of_the_name_and_collision_rules():
    import inspect

    source = inspect.getsource(binary_coherence)
    assert binary_coherence.name_ok is binary_curve.name_ok
    assert binary_coherence.output_collision_problems is binary_curve.output_collision_problems
    assert "def _name_ok" not in source and "_name_ok(" not in source
    assert "which would overwrite" not in source and "clash" not in source and "PAYOFFS.get" not in source


def _probes(tmp_path):
    return {"binary-coherence": NodeProbe(
        params={**BASE, **LADDER_RELATIONS}, required=("id_field", "bid_field", "ask_field", "fair_field",
                                                        "min_spread", "solver"),
        inputs={"records": [dict(r) for r in LADDER]}, stream_ports=("records",), runnable=True)}


TestBinaryCoherenceConformance = conformance_suite(
    registry=(("binary-coherence", BinaryCoherence),), module="dskit.pipeline.libs.binary_coherence",
    probes=_probes, expected_roles={"binary-coherence": "transform"}, name="TestBinaryCoherenceConformance")
