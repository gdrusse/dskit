"""Coherence across linked binaries (ADR-0249 part 3): LP feasibility, the riskless set, the projection.

Every expected arbitrage is worked by hand in the comments: the legs, the credit and the fact that
the position pays nothing net in every outcome the declared relations allow.
"""

import json
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyomo")
pytest.importorskip("highspy")

from dskit.pipeline import binary_curve
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.libs import binary_coherence
from dskit.pipeline.libs.binary_coherence import RELATIONS, STATUS_OK, BinaryCoherence, Relation
from dskit.pipeline.libs.pyomo import PyomoSolve
from tests.pipeline.test_binary_curve import ABSENT, SPEC, SPEC_IDS, spec_cell, Three

BASE = {"id_field": "id", "bid_field": "bid", "ask_field": "ask", "fair_field": "coherent",
        "min_spread": 0.01, "solver": "highs"}


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
    # names the same legs: dust is relative to the largest |dual|, never a fixed absolute number
    from pyomo.environ import Suffix

    real_solve = BinaryCoherence._solve

    def scaled(self, solver, model):
        results = real_solve(self, solver, model)
        if isinstance(getattr(model, "dual", None), Suffix):
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
MIN_SPREADS = (0.01, 0.001, 1e-4)


def two_sided(bid, ask):
    """The side rule restated: a sale needs bid > 0, a purchase needs 0 < ask < 1 (and bid <= ask)."""
    return bid > 0 and 0 < ask < 1


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
# strictly-positive twins below, which are TWO-SIDED under the side rule, so they still reach the QP.
VERBATIM_HANGS = {
    "coherent chain": ("chains", {"a": [0.0, 1.0], "b": [0.0, 0.0]}, 0.01),
    "partition of three": ("partitions", {"a": [0.5, 0.53], "b": [0.0, 0.0], "c": [0.0, 0.5]}, 1e-4),
    "partition of four": ("partitions", {"a": [0.0, 0.5], "b": [0.0, 0.0], "c": [0.0, 0.3], "d": [0.0, 0.5]}, 1e-3),
}
TWO_SIDED_HANGS = {
    "coherent chain": ("chains", {"a": [1e-9, 0.999999], "b": [1e-9, 1e-9]}, 0.01),
    "partition of three": ("partitions", {"a": [0.5, 0.53], "b": [1e-9, 1e-9], "c": [1e-9, 0.5]}, 1e-4),
    "partition of four": ("partitions",
                          {"a": [1e-9, 0.5], "b": [1e-9, 1e-9], "c": [1e-9, 0.3], "d": [1e-9, 0.5]}, 1e-3),
    "wide first bucket": ("partitions", {"a": [0.001, 0.99], "b": [0.3, 0.99], "c": [1e-9, 1e-6]}, 1e-3),
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
    # a is pinned near 0 with a weight 1e8 times the floor's neighbours'; scaling the objective by that largest
    # weight made HiGHS return "optimal" b = 0.5501, c = 0.2673, d = 0.1826. Water-filling by hand
    # (p_i = clip(mid_i + lam / w_i), sum p = 1, w_i = 1 / spread_i^2) gives b 0.61257, c 0.25704, d 0.13040.
    quotes = [(1e-9, 1e-9), (0.3, 0.7), (0.2, 0.3), (0.01, 0.2)]
    rows = [contract(cid, bid, ask) for cid, (bid, ask) in zip("abcd", quotes)]
    fair = fair_of(run(rows, partitions=[list("abcd")], min_spread=1e-4))
    assert fair["b"] == pytest.approx(0.61257, abs=2e-5)
    assert fair["c"] == pytest.approx(0.25704, abs=2e-5)
    assert fair["d"] == pytest.approx(0.13040, abs=2e-5)
    want = expected_fair("partitions", quotes, 1e-4)
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

    Returns ``(payout, chain, differences, partition)``: each contract's payout per region and the
    relations that are TRUE in this world (above_j pays in regions j.., between_j in region j, below in 0).
    """
    regions = range(m + 1)
    payout = {f"above{j}": [int(r >= j) for r in regions] for j in range(1, m + 1)}
    payout.update({f"between{j}": [int(r == j) for r in regions] for j in range(1, m)})
    payout["below"] = [int(r == 0) for r in regions]
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
    assert "from dskit.pipeline.binary_curve import row_key" in source


def _probes(tmp_path):
    return {"binary-coherence": NodeProbe(
        params={**BASE, **LADDER_RELATIONS}, required=("id_field", "bid_field", "ask_field", "fair_field",
                                                        "min_spread", "solver"),
        inputs={"records": [dict(r) for r in LADDER]}, stream_ports=("records",), runnable=True)}


TestBinaryCoherenceConformance = conformance_suite(
    registry=(("binary-coherence", BinaryCoherence),), module="dskit.pipeline.libs.binary_coherence",
    probes=_probes, expected_roles={"binary-coherence": "transform"}, name="TestBinaryCoherenceConformance")
