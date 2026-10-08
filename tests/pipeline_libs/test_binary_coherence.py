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


def test_a_partition_that_bids_over_one_is_a_sell_everything_arbitrage():
    rows = [contract("lo", 0.30, 0.32), contract("mid", 0.40, 0.42), contract("hi", 0.33, 0.35)]
    arb = run(rows, partitions=[["lo", "mid", "hi"]])["arbitrage"]
    # bids sum to 1.03: sell all three, owe exactly 1 at settlement
    assert arb["feasible"] is False and arb["violation"] == pytest.approx(0.03)
    assert {leg["id"]: leg["side"] for leg in arb["legs"]} == {"lo": "sell", "mid": "sell", "hi": "sell"}
    assert arb["executable_units"] is None  # no size columns named


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


def test_a_quote_above_one_is_refused():
    out = run([contract("k1", 0.5, 1.2), contract("k2", 0.1, 0.2)], chains=[["k1", "k2"]])
    assert out["records"][0]["coherent_status"] == "bad_quote"


# -- params and contract -------------------------------------------------------------------------------


@pytest.mark.parametrize("bad, needle", [
    ({"chains": [["a"]]}, "chains"),
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
