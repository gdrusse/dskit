"""Coherence across linked binaries (ADR-0249 part 3): LP feasibility, the riskless set, the projection.

Every expected arbitrage is worked by hand in the comments: the legs, the credit and the fact that
the position pays nothing net in every outcome the declared relations allow.
"""

import pytest

pytest.importorskip("pyomo")
pytest.importorskip("highspy")

from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.libs import binary_coherence
from dskit.pipeline.libs.binary_coherence import RELATIONS, BinaryCoherence, Relation
from dskit.pipeline.libs.pyomo import PyomoSolve

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
    rows = [contract("k1", 0.50, 0.40), contract("k2", 0.30, 0.32), contract("k3", None, 0.2),
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


@pytest.mark.parametrize("bid, ask", [(0.5, 1.2), (-0.1, 0.2), (0.5, 0.4), ("0.1", 0.2), (True, 0.2)])
def test_a_quote_outside_the_unit_interval_crossed_or_not_a_number_is_refused(bid, ask):
    out = run([contract("k1", bid, ask), contract("k2", 0.1, 0.2)], chains=[["k1", "k2"]])
    assert out["records"][0]["coherent_status"] == "bad_quote"
    assert out["summary"]["skipped_relations"][0]["bad_quote"] == ["k1"]


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
    with pytest.raises(RuntimeError, match="quadratic projection"):
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


def _probes(tmp_path):
    return {"binary-coherence": NodeProbe(
        params={**BASE, **LADDER_RELATIONS}, required=("id_field", "bid_field", "ask_field", "fair_field",
                                                        "min_spread", "solver"),
        inputs={"records": [dict(r) for r in LADDER]}, stream_ports=("records",), runnable=True)}


TestBinaryCoherenceConformance = conformance_suite(
    registry=(("binary-coherence", BinaryCoherence),), module="dskit.pipeline.libs.binary_coherence",
    probes=_probes, expected_roles={"binary-coherence": "transform"}, name="TestBinaryCoherenceConformance")
