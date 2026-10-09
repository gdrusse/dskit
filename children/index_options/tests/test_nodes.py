"""The public Node facade must validate and persist the same domain result."""

import pytest

from dskit.pipeline.base import ConfigError
from index_options.nodes import CondorPayoffDiagnostic


def test_direct_node_has_exact_cashflows_and_labels(rows, params):
    report = CondorPayoffDiagnostic("diagnostic", params).run(None, rows)["report"].value
    assert report.get("net_pnl_usd") == "252"
    assert report["max_loss_after_fees_usd"] == "248"
    assert report["decision_eligible"] is False
    assert report["kind"] == "synthetic_ex_post_diagnostic"


def test_unknown_params_refuse(params):
    with pytest.raises(ConfigError, match="surprise"):
        CondorPayoffDiagnostic("diagnostic", {**params, "surprise": 1})

import copy


@pytest.mark.parametrize("stream", ["contracts", "quotes", "settlements"])
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_corpus", "provenance", "bad_unused"])
def test_direct_boundary_rejects_stream_and_reference_failures(rows, params, stream, mutation):
    if mutation == "missing":
        rows[stream] = []
    elif mutation == "duplicate":
        rows[stream].append(copy.deepcopy(rows[stream][0]))
    elif mutation == "wrong_corpus":
        rows[stream][0]["corpus_id"] = "other"
    elif mutation == "provenance":
        rows[stream][0]["provenance"] = "real"
    else:
        rows[stream].append({**rows[stream][0], "row_version": "unselected", "known_at_basis": "guessed"})
    node = CondorPayoffDiagnostic("d", params)
    assert node.validate_inputs(rows)
    with pytest.raises(ValueError):
        node.run(None, rows)


@pytest.mark.parametrize("multiplier", [0, -1, True])
def test_direct_diagnostic_refuses_bad_multiplier(rows, params, multiplier):
    rows["contracts"][0]["multiplier"] = multiplier
    with pytest.raises(ValueError, match="multiplier"):
        CondorPayoffDiagnostic("d", params).run(None, rows)


@pytest.mark.parametrize("field", ["contract_version", "quote_version", "quote_at"])
def test_missing_exact_version_is_never_guessed(rows, params, field):
    params["legs"][0][field] = "2026-01-17T20:45:00Z" if field == "quote_at" else "absent"
    with pytest.raises(ValueError, match="exactly one"):
        CondorPayoffDiagnostic("d", params).run(None, rows)


@pytest.mark.parametrize("target", ["leg", "settlement"])
def test_nested_unknown_params_refuse(params, target):
    item = params["legs"][0] if target == "leg" else params["settlement"]
    item["surprise"] = True
    with pytest.raises(ConfigError, match="surprise"):
        CondorPayoffDiagnostic("d", params)


def test_row_order_does_not_change_selected_result(rows, params):
    expected = CondorPayoffDiagnostic("d", params).run(None, rows)["report"].value
    for stream in rows.values():
        stream.reverse()
    assert CondorPayoffDiagnostic("d", params).run(None, rows)["report"].value == expected


def test_ordinary_diagnostic_subclass_retains_validation_and_serving_refusal(rows, params):
    class ResearchDiagnostic(CondorPayoffDiagnostic):
        pass

    assert ResearchDiagnostic.serving_effect(params, {}) == "forbidden"
    assert ResearchDiagnostic("d", params).run(None, rows)["report"].value["net_pnl_usd"] == "252"
    rows["contracts"][0]["multiplier"] = 0
    with pytest.raises(ValueError):
        ResearchDiagnostic("d", params).run(None, rows)

_TIME_ALIASES = (
    "2026-01-16T20:45:00Z", "2026-01-16T15:45:00-05:00", "2026-01-16T20:45:00+00:00",
)


@pytest.mark.parametrize("quote_at", _TIME_ALIASES)
@pytest.mark.parametrize("effective_at", _TIME_ALIASES)
@pytest.mark.parametrize("reference_at", _TIME_ALIASES)
def test_quote_references_match_instants_not_spellings(rows, params, quote_at, effective_at, reference_at):
    rows["quotes"][0].update(quote_at=quote_at, effective_at=effective_at)
    params["legs"][0]["quote_at"] = reference_at
    node = CondorPayoffDiagnostic("d", params)
    assert node.validate_inputs(rows) == []
    assert node.run(None, rows)["report"].value["net_pnl_usd"] == "252"


@pytest.mark.parametrize("reference_at", _TIME_ALIASES)
@pytest.mark.parametrize("ask", ["2.60", "2.61"])
def test_alias_equivalent_selected_versions_are_ambiguous(rows, params, reference_at, ask):
    rows["quotes"].append({
        **rows["quotes"][0], "effective_at": _TIME_ALIASES[1], "ask": ask,
    })
    params["legs"][0]["quote_at"] = reference_at
    node = CondorPayoffDiagnostic("d", params)
    assert node.validate_inputs(rows)
    with pytest.raises(ValueError, match="exactly one"):
        node.run(None, rows)


def test_distinct_quote_versions_at_equivalent_instants_remain_selectable(rows, params):
    rows["quotes"].append({
        **rows["quotes"][0], "effective_at": _TIME_ALIASES[1], "row_version": "v2", "ask": "2.61",
    })
    params["legs"][0].update(quote_at=_TIME_ALIASES[2], quote_version="v2")
    assert CondorPayoffDiagnostic("d", params).run(None, rows)["report"].value["net_pnl_usd"] == "251"


@pytest.mark.parametrize("leg", range(4))
@pytest.mark.parametrize("field", ["bid_size", "ask_size"])
def test_quote_size_is_checked_against_count_in_direct_node(rows, params, leg, field):
    params["count"] = 2
    rows["quotes"][leg][field] = 1
    with pytest.raises(ValueError, match="sizes"):
        CondorPayoffDiagnostic("d", params).run(None, rows)
    rows["quotes"][leg][field] = 2
    assert CondorPayoffDiagnostic("d", params).run(None, rows)["report"].value["net_pnl_usd"] == "512"


@pytest.mark.parametrize("stream, effective", [
    ("quotes", "2026-01-15T20:45:00Z"), ("settlements", "2026-02-19T21:00:00Z"),
])
def test_direct_node_refuses_isolated_effective_clock_mismatch(rows, params, stream, effective):
    rows[stream][0]["effective_at"] = effective
    with pytest.raises(ValueError, match="must equal effective_at"):
        CondorPayoffDiagnostic("d", params).run(None, rows)


# -- ADR-0194: VolRegimeSignals ------------------------------------------------------------
#
# Every expected number is restated by hand from the inputs below, never read from the node.
#
#   day   iv_index  rv_22   VIX3M
#   d0      20      0.010    25
#   d1      30      0.020    25
#   d2      24      0.015    30
#   d3      40      0.030    50
#   d4      22      0.010    22
#
# Each row reads the PREVIOUS row's iv, rv and that day's VIX3M (one-session lag), so:
#   row   ratio = iv / VIX3M   vrp = (iv / 100)^2 - 252 rv^2
#   1     20 / 25 = 0.8        0.04   - 252 * 0.0001   =  0.0148
#   2     30 / 25 = 1.2        0.09   - 252 * 0.0004   = -0.0108
#   3     24 / 30 = 0.8        0.0576 - 252 * 0.000225 =  0.0009
#   4     40 / 50 = 0.8        0.16   - 252 * 0.0009   = -0.0668
# and, with min_history 1, the mid-rank of each ratio among the EARLIER ratios:
#   row 1: none earlier -> None       row 2: 1.2 above [0.8] -> 1.0
#   row 3: 0.8 vs [0.8, 1.2]: one tie -> (0 + 1/2) / 2 = 0.25
#   row 4: 0.8 vs [0.8, 1.2, 0.8]: two ties -> (0 + 2/2) / 3 = 1/3

import itertools  # noqa: E402
import math  # noqa: E402
from datetime import date, timedelta  # noqa: E402

from index_options.nodes import VolRegimeSignals  # noqa: E402

DAYS = [(date(2024, 3, 4) + timedelta(days=k)).isoformat() for k in range(10)]
IV = [20.0, 30.0, 24.0, 40.0, 22.0]
RV = [0.010, 0.020, 0.015, 0.030, 0.010]
TERM = [25.0, 25.0, 30.0, 50.0, 22.0]
#: The four gates' names in their order, and every field the node adds — restated, never read.
GATES = ("gate_term_inverted", "gate_term_high_pct", "gate_vrp_nonpositive", "gate_any")
ADDED = ("vix_term_ratio", "vix_term_ratio_pct", "vrp") + GATES


def _signal_rows(ivs=IV, rvs=RV, instrument="SPY"):
    return [{"instrument": instrument, "asof_ms": 1000 + k, "date": DAYS[k], "close": 100.0 + k,
             "iv_index": iv, "rv_22": rv, "marker": f"{instrument}{k}"}
            for k, (iv, rv) in enumerate(zip(ivs, rvs))]


def _term(closes=TERM):
    return [{"instrument": "VIX3M", "date": DAYS[k], "close": c} for k, c in enumerate(closes)]


def _signals(rows, term, **params):
    node = VolRegimeSignals("signals", {"min_history": 1, **params})
    return node.run(None, {"rows": rows, "term": term})["rows"]


def _added(row):
    return {k: row[k] for k in ADDED}


def _assert_added(row, ratio, pct, vrp, inverted, high, nonpositive, any_gate):
    """One row's seven added fields: floats to 1e-12, the rest by identity (None is not False)."""
    for name, want in (("vix_term_ratio", ratio), ("vix_term_ratio_pct", pct), ("vrp", vrp)):
        if want is None:
            assert row[name] is None, name
        else:
            assert row[name] == pytest.approx(want, abs=1e-12), name
    for name, want in zip(GATES, (inverted, high, nonpositive, any_gate)):
        assert row[name] is want, name


def test_the_signals_are_the_previous_sessions_ratio_premium_percentile_and_gates():
    out = _signals(_signal_rows(), _term())
    assert len(out) == 5
    _assert_added(out[0], None, None, None, None, None, None, None)
    _assert_added(out[1], 0.8, None, 0.0148, False, None, False, None)
    _assert_added(out[2], 1.2, 1.0, -0.0108, True, True, True, True)
    _assert_added(out[3], 0.8, 0.25, 0.0009, False, False, False, False)
    _assert_added(out[4], 0.8, 1 / 3, -0.0668, False, False, True, True)


def test_every_input_row_comes_back_unchanged_in_input_order_and_the_input_is_untouched():
    rows, term = _signal_rows(), _term()
    before = copy.deepcopy((rows, term))
    out = _signals(rows, term)
    assert (rows, term) == before
    assert [r["marker"] for r in out] == [f"SPY{k}" for k in range(5)]
    for original, row in zip(rows, out):
        assert {k: v for k, v in row.items() if k not in ADDED} == original
        assert set(row) - set(original) == set(ADDED)
    order = (3, 0, 4, 2, 1)      # the walk is in asof order; the output is in input order
    shuffled = _signals([rows[k] for k in order], term)
    assert [r["marker"] for r in shuffled] == [f"SPY{k}" for k in order]
    assert {r["marker"]: _added(r) for r in shuffled} == {r["marker"]: _added(r) for r in out}


def test_the_same_days_iv_term_close_and_realized_vol_never_inform_that_days_row():
    # row 4's own iv is 22 and its own VIX3M 22 (ratio 1.0, would invert): it reads row 3's
    # 40 / 50 = 0.8 instead
    base = _signals(_signal_rows(), _term())
    assert base[4]["vix_term_ratio"] == pytest.approx(0.8)
    assert base[4]["gate_term_inverted"] is False
    moved = _signals(_signal_rows(ivs=IV[:4] + [99.0], rvs=RV[:4] + [0.5]),
                     _term(TERM[:4] + [3.0]))
    assert _added(moved[4]) == _added(base[4])      # its own day's data changed nothing
    # ... the same numbers reach the NEXT row, where they belong: 99 / 3 and 0.99^2 - 252 * 0.25
    nxt = _signals(_signal_rows(ivs=IV[:4] + [99.0, 10.0], rvs=RV[:4] + [0.5, 0.1]),
                   _term(TERM[:4] + [3.0, 9.0]))
    assert nxt[5]["vix_term_ratio"] == pytest.approx(33.0)
    assert nxt[5]["vrp"] == pytest.approx(0.9801 - 63.0)


def test_lag_sessions_reads_that_many_rows_back_of_the_same_instrument():
    out = _signals(_signal_rows(), _term(), lag_sessions=2)
    # row 2 reads row 0 (20 / 25), row 3 reads row 1 (30 / 25), row 4 reads row 2 (24 / 30)
    _assert_added(out[0], None, None, None, None, None, None, None)
    _assert_added(out[1], None, None, None, None, None, None, None)
    assert [r["vix_term_ratio"] for r in out][2:] == pytest.approx([0.8, 1.2, 0.8])
    assert out[3]["vrp"] == pytest.approx(0.09 - 0.1008)      # row 1's iv 30 and rv 0.02


def test_each_instrument_reads_its_own_previous_row_and_its_own_percentile_history():
    spy = _signal_rows()
    qqq = _signal_rows([50.0, 60.0, 70.0, 80.0, 90.0], [0.01] * 5, instrument="QQQ")
    out = _signals([r for pair in zip(qqq, spy) for r in pair], _term())   # interleaved
    assert [_added(r) for r in out if r["instrument"] == "SPY"] == [
        _added(r) for r in _signals(spy, _term())]
    # QQQ's ratios: 50/25, 60/25, 70/30, 80/50 = 2.0, 2.4, 2.333, 1.6 and their mid-ranks among
    # QQQ's own earlier ratios: None, above [2.0] = 1.0, middle of [2.0, 2.4] = 0.5, below all = 0
    q = [r for r in out if r["instrument"] == "QQQ"]
    assert [r["vix_term_ratio"] for r in q][1:] == pytest.approx([2.0, 2.4, 70 / 30, 1.6])
    assert [r["vix_term_ratio_pct"] for r in q][1:] == [None, 1.0, 0.5, 0.0]


def test_the_percentile_uses_only_earlier_rows():
    rows, term = _signal_rows(), _term()
    full = _signals(rows, term)
    for k in range(1, 6):      # a prefix scored alone gives the rows it has inside the whole
        assert [_added(r) for r in _signals(rows[:k], term)] == [_added(r) for r in full[:k]]
    later = _signals(_signal_rows(ivs=IV[:3] + [400.0, 1.0]), _term(TERM[:3] + [1.0, 400.0]))
    assert [_added(r) for r in later[:4]] == [_added(r) for r in full[:4]]
    assert _added(later[4]) != _added(full[4])     # only the row that reads the changed day moves


def test_min_history_counts_earlier_finite_ratios_only():
    rows, term = _signal_rows(), _term()
    out = _signals(rows, term, min_history=2)     # the first two rows with a ratio are unscored
    assert [r["vix_term_ratio_pct"] for r in out] == [None, None, None, 0.25, pytest.approx(1 / 3)]
    # row 2's ratio is missing (row 1's iv is): rows 1 and 3 hold the only finite ratios before
    # row 4, so min_history 2 scores row 4 alone; a missing ratio is no history
    holed = _signal_rows(ivs=[20.0, None, 24.0, 40.0, 22.0])
    out = _signals(holed, term, min_history=2)
    assert [r["vix_term_ratio"] for r in out][1:] == [pytest.approx(0.8), None,
                                                       pytest.approx(0.8), pytest.approx(0.8)]
    assert [r["vix_term_ratio_pct"] for r in out] == [None, None, None, None, 0.5]
    out = _signals(holed, term, min_history=1)
    assert [r["vix_term_ratio_pct"] for r in out] == [None, None, None, 0.5, 0.5]


def test_the_default_min_history_is_two_hundred_and_fifty_two_earlier_ratios():
    ivs, rvs = [20.0 + (k % 7) for k in range(300)], [0.01] * 300
    days = [(date(2020, 1, 1) + timedelta(days=k)).isoformat() for k in range(300)]
    rows = [{"instrument": "SPY", "asof_ms": k, "date": d, "iv_index": iv, "rv_22": rv}
            for k, (d, iv, rv) in enumerate(zip(days, ivs, rvs))]
    term = [{"date": d, "close": 20.0} for d in days]
    out = VolRegimeSignals("signals", {}).run(None, {"rows": rows, "term": term})["rows"]
    scored = [k for k, r in enumerate(out) if r["vix_term_ratio_pct"] is not None]
    # the first ratio is at row 1; 252 earlier ratios exist first at row 253
    assert scored[0] == 253 and scored == list(range(253, 300))


@pytest.mark.parametrize("bad", [None, 0.0, -5.0, float("nan"), float("inf"), True, "30"])
def test_a_missing_or_non_positive_iv_leaves_every_dependent_output_none_never_false(bad):
    out = _signals(_signal_rows(ivs=[IV[0], bad] + IV[2:]), _term())
    assert (out[2]["vix_term_ratio"], out[2]["vrp"], out[2]["vix_term_ratio_pct"]) == \
        (None, None, None)
    for name in GATES:
        assert out[2][name] is None, name
    # ... and only that one row: the next row reads a good previous row again
    assert out[3]["vix_term_ratio"] == pytest.approx(0.8) and out[3]["gate_any"] is False


@pytest.mark.parametrize("term", ["absent", None, 0.0, -25.0, float("nan"), "25"])
def test_a_missing_or_non_positive_term_close_blanks_the_ratio_side_and_keeps_the_premium(term):
    closes = [{"date": DAYS[k], "close": c} for k, c in enumerate(TERM)]
    if term == "absent":
        del closes[1]
    else:
        closes[1]["close"] = term      # d1's VIX3M, read by row 2
    out = _signals(_signal_rows(), closes)
    # ratio, percentile and both ratio gates unknown; the premium is unaffected: -0.0108 <= 0
    _assert_added(out[2], None, None, -0.0108, None, None, True, True)
    assert out[1]["vix_term_ratio"] == pytest.approx(0.8)    # d0's close is intact


@pytest.mark.parametrize("bad", [None, 0.0, -0.01, float("nan"), True])
def test_a_missing_or_non_positive_realized_vol_blanks_the_premium_and_keeps_the_term_side(bad):
    out = _signals(_signal_rows(rvs=[RV[0], RV[1], bad] + RV[3:]), _term())
    # row 3 reads row 2: ratio 0.8 and percentile 0.25 known and both False, premium unknown:
    # no True and one None gives gate_any None, not False
    _assert_added(out[3], 0.8, 0.25, None, False, False, None, None)
    # with the ratio side True the unknown premium does not matter: row 2 reads row 1 (fine)
    rows = _signal_rows(rvs=[RV[0], bad] + RV[2:])
    _assert_added(_signals(rows, _term())[2], 1.2, 1.0, None, True, True, None, True)


def test_a_row_lacking_the_fields_or_the_date_reads_as_unknown_not_as_an_error():
    rows = _signal_rows()
    del rows[1]["iv_index"], rows[1]["rv_22"]
    _assert_added(_signals(rows, _term())[2], None, None, None, None, None, None, None)
    rows = _signal_rows()
    del rows[1]["date"]
    out = _signals(rows, _term())[2]     # no date, no VIX3M close: only the premium is known
    _assert_added(out, None, None, -0.0108, None, None, True, True)


@pytest.mark.parametrize("states", list(itertools.product((True, False, None), repeat=3)))
def test_gate_any_is_true_if_any_true_none_if_none_true_and_any_unknown_else_false(states):
    expected = True if True in states else (None if None in states else False)
    assert VolRegimeSignals._any(states) is expected


def test_gate_any_named_cases():
    assert VolRegimeSignals._any((False, False, False)) is False
    assert VolRegimeSignals._any((False, False, None)) is None
    assert VolRegimeSignals._any((None, None, None)) is None
    assert VolRegimeSignals._any((True, None, False)) is True
    assert VolRegimeSignals._any((None, True, None)) is True


def test_the_knobs_move_the_premium_and_the_three_thresholds():
    rows, term = _signal_rows(), _term()
    out = _signals(rows, term, periods_per_year=365)
    assert out[1]["vrp"] == pytest.approx(0.04 - 365 * 0.0001)
    assert out[2]["vrp"] == pytest.approx(0.09 - 365 * 0.0004)
    # the ratio gate is >= : row 1's ratio is exactly 0.8
    assert _signals(rows, term, inverted_at=0.8)[1]["gate_term_inverted"] is True
    assert _signals(rows, term, inverted_at=0.81)[1]["gate_term_inverted"] is False
    # the percentile gate is >= : row 3's percentile is exactly 0.25
    assert _signals(rows, term, high_ratio_pct=0.25)[3]["gate_term_high_pct"] is True
    assert _signals(rows, term, high_ratio_pct=0.26)[3]["gate_term_high_pct"] is False
    # the premium gate is <= 0 : 0.5^2 - 4 * 0.25^2 is exactly zero, a hair more realized is not
    for realized, want in ((0.25, True), (0.24, False), (0.26, True)):
        exact = _signals(_signal_rows([50.0, 1.0], [realized, 0.1]), _term([10.0, 10.0]),
                         periods_per_year=4)
        assert (exact[1]["vrp"] == 0.0) is (realized == 0.25)
        assert exact[1]["gate_vrp_nonpositive"] is want, realized


def test_the_field_names_are_knobs():
    rows = [{**r, "vix": r["iv_index"], "rv_5": r["rv_22"]} for r in _signal_rows()]
    for r in rows:
        del r["iv_index"], r["rv_22"]
    renamed = _signals(rows, _term(), implied_field="vix", realized_field="rv_5")
    assert [_added(r) for r in renamed] == [_added(r) for r in _signals(_signal_rows(), _term())]


def test_the_gate_names_are_declared_once_on_the_class_in_their_order():
    assert VolRegimeSignals.GATE_FIELDS == GATES
    out = _signals(_signal_rows(), _term())
    assert all(set(GATES) <= set(row) for row in out)


def test_realized_vol_is_per_session_the_numpy_packs_convention():
    pytest.importorskip("numpy")
    from dskit.pipeline.libs.numpy import RealizedVolFeatures

    # six closes rising 2 % per step in log terms: each one-step log return is 0.02, so a
    # 3-step realized vol is sqrt(mean(0.02^2)) = 0.02 PER SESSION, never annualized
    closes = [100 * math.exp(0.02 * k) for k in range(6)]
    bars = [{"instrument": "X", "contract": "X", "group": "X", "asof_ms": k, "close": c,
             "date": DAYS[k]} for k, c in enumerate(closes)]
    features = RealizedVolFeatures("rv", {
        "fields": ["close"], "windows": [3], "carry_fields": ["instrument", "asof_ms", "date"],
    }).run(None, {"records": bars})["rows"]
    logs = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    per_session = math.sqrt(sum(r * r for r in logs[:3]) / 3)
    assert features[3]["rv_3"] == pytest.approx(per_session) == pytest.approx(0.02)
    rows = [{**f, "iv_index": 20.0} for f in features]
    out = _signals(rows, _term([25.0] * 6), realized_field="rv_3")
    # row 5 reads row 4, which has a 3-step vol of 0.02: 0.2^2 - 252 * 0.02^2 = -0.0608
    assert out[5]["vrp"] == pytest.approx(0.04 - 252 * 0.02 ** 2) == pytest.approx(-0.0608)
    assert out[5]["gate_vrp_nonpositive"] is True


def test_the_node_declares_its_role_ports_and_forbids_serving():
    assert VolRegimeSignals.role == "transform" and VolRegimeSignals.outputs == ("rows",)
    assert VolRegimeSignals.serving_effect({}, {}) == "forbidden"
    assert VolRegimeSignals._PARAMS == tuple(sorted((
        "implied_field", "realized_field", "periods_per_year", "inverted_at", "high_ratio_pct",
        "min_history", "lag_sessions")))


@pytest.mark.parametrize("change", [
    {"surprise": 1}, {"implied_field": ""}, {"implied_field": 3}, {"realized_field": ""},
    {"realized_field": None}, {"periods_per_year": 0}, {"periods_per_year": -252},
    {"periods_per_year": True}, {"periods_per_year": "252"}, {"periods_per_year": float("nan")},
    {"inverted_at": 0}, {"inverted_at": -1.0}, {"inverted_at": True},
    {"high_ratio_pct": 0}, {"high_ratio_pct": 1.01}, {"high_ratio_pct": -0.1},
    {"high_ratio_pct": True}, {"high_ratio_pct": float("nan")},
    {"min_history": 0}, {"min_history": True}, {"min_history": 1.5}, {"min_history": "252"},
    {"lag_sessions": 0}, {"lag_sessions": -1}, {"lag_sessions": True}, {"lag_sessions": 1.5},
])
def test_every_knob_refuses_a_bad_value_and_an_unknown_knob_is_named(change):
    assert VolRegimeSignals.validate_params(change)
    with pytest.raises(ConfigError, match=next(iter(change))):
        VolRegimeSignals("signals", change)


def test_the_defaults_validate_and_a_full_declaration_is_accepted():
    assert VolRegimeSignals.validate_params({}) == []
    full = {"implied_field": "iv_index", "realized_field": "rv_22", "periods_per_year": 252,
            "inverted_at": 1.0, "high_ratio_pct": 0.8, "min_history": 252, "lag_sessions": 1}
    assert VolRegimeSignals.validate_params(full) == []
    assert VolRegimeSignals.validate_params({"high_ratio_pct": 1.0, "inverted_at": 0.5}) == []


def test_the_inputs_must_be_lists_and_run_refuses_ambiguous_ones():
    node = VolRegimeSignals("signals", {})
    assert node.validate_inputs({"rows": [], "term": []}) == []
    assert any("rows" in p for p in node.validate_inputs({"rows": None, "term": []}))
    assert any("term" in p for p in node.validate_inputs({"rows": [], "term": ()}))
    assert len(node.validate_inputs({})) == 2
    rows = _signal_rows()
    with pytest.raises(ValueError, match="repeats"):
        node.run(None, {"rows": rows + [dict(rows[2])], "term": _term()})
    with pytest.raises(ValueError, match="repeats a date"):
        node.run(None, {"rows": rows, "term": _term() + [{"date": DAYS[0], "close": 26.0}]})
    with pytest.raises(ValueError, match="asof_ms"):
        node.run(None, {"rows": [{**rows[0], "asof_ms": None}], "term": _term()})
    with pytest.raises(ValueError, match="term row 1"):
        node.run(None, {"rows": rows, "term": [{"close": 25.0}]})
    # the same asof_ms on DIFFERENT instruments is no repeat
    both = rows + [{**r, "instrument": "QQQ"} for r in rows]
    assert len(node.run(None, {"rows": both, "term": _term()})["rows"]) == 10


# -- ADR-0195: the ADR-0194 review backlog on VolRegimeSignals ---------------------------------


def test_the_defaults_are_the_owners_pre_registered_numbers_typed_out_independently():
    # B-M2: the generator reads DEFAULTS, so a test that reads them too asserts nothing. These
    # literals are the owner's ADR-0194 answers (ratio >= 1.0, percentile >= 0.8, previous
    # session, 252-session history, 252 periods a year); a mutated default fails here, not
    # only as "the shipped file differs from its generator".
    assert VolRegimeSignals.DEFAULTS == {
        "implied_field": "iv_index", "realized_field": "rv_22", "periods_per_year": 252,
        "inverted_at": 1.0, "high_ratio_pct": 0.8, "min_history": 252, "lag_sessions": 1}


def _one_pair(previous_iv, term_close, iv=20.0):
    """Two rows a day apart: the second reads the first's iv and that date's VIX3M close."""
    rows = [{"instrument": "SPY", "asof_ms": k, "date": DAYS[k], "iv_index": v, "rv_22": 0.01}
            for k, v in enumerate((previous_iv, iv))]
    return rows, [{"date": DAYS[0], "close": term_close}, {"date": DAYS[1], "close": 20.0}]


@pytest.mark.parametrize("params", [{}, dict(VolRegimeSignals.DEFAULTS)],
                         ids=["no-params", "every-knob-written-out"])
def test_a_ratio_of_exactly_one_is_inverted_at_the_defaults_and_a_hair_under_is_not(params):
    node = VolRegimeSignals("signals", params)
    for previous_iv, want in ((20.0, True), (19.999999, False), (20.000001, True)):
        rows, term = _one_pair(previous_iv, 20.0)
        got = node.run(None, {"rows": rows, "term": term})["rows"][1]
        assert got["gate_term_inverted"] is want, previous_iv
    rows, term = _one_pair(20.0, 20.0)
    assert node.run(None, {"rows": rows, "term": term})["rows"][1]["vix_term_ratio"] == 1.0


def _percentile_pair(n_below, n_above):
    """Rows whose LAST row's ratio sits above ``n_below`` earlier ratios and under ``n_above``.

    The ratio is iv / 20.0 read one session back: below-rows 10.00 + 0.01 j, the row under test
    reads iv 15.0 (ratio 0.75), above-rows 20.00 + 0.01 j. With 260 earlier ratios (>= the 252
    of the default history) the mid-rank is n_below / 260 exactly, with no ties.
    """
    ivs = ([10.0 + 0.01 * j for j in range(n_below)] + [20.0 + 0.01 * j for j in range(n_above)]
           + [15.0, 20.0])          # ... the ratio row's iv, then the last row's own (unread)
    days = [(date(2000, 1, 1) + timedelta(days=k)).isoformat() for k in range(len(ivs))]
    rows = [{"instrument": "SPY", "asof_ms": k, "date": d, "iv_index": iv, "rv_22": 0.01}
            for k, (d, iv) in enumerate(zip(days, ivs))]
    return rows, [{"date": d, "close": 20.0} for d in days]


@pytest.mark.parametrize("params", [{}, dict(VolRegimeSignals.DEFAULTS)],
                         ids=["no-params", "every-knob-written-out"])
def test_a_percentile_of_exactly_point_eight_is_high_at_the_defaults_and_a_hair_under_is_not(params):
    node = VolRegimeSignals("signals", params)
    for n_below, want in ((208, True), (207, False), (209, True)):
        rows, term = _percentile_pair(n_below, 260 - n_below)
        assert len(rows) == 262
        last = node.run(None, {"rows": rows, "term": term})["rows"][-1]
        assert last["vix_term_ratio_pct"] == pytest.approx(n_below / 260, abs=1e-15), n_below
        assert last["gate_term_high_pct"] is want, n_below
        assert last["gate_term_inverted"] is False       # its own ratio is 0.75


def test_one_earlier_ratio_short_of_the_default_history_scores_no_percentile():
    rows, term = _percentile_pair(100, 151)               # 251 earlier ratios: one short of 252
    last = VolRegimeSignals("signals", {}).run(None, {"rows": rows, "term": term})["rows"][-1]
    assert last["vix_term_ratio_pct"] is None and last["gate_term_high_pct"] is None


def test_numpy_scalar_inputs_give_plain_bool_gates_which_the_backtest_accepts():
    # A-N2: `np.float64 >= 1.0` is np.bool_, which the backtest's gate guard (bool or None)
    # refuses; the node owns turning its verdicts into plain bools
    np = pytest.importorskip("numpy")
    rows = [{"instrument": "SPY", "asof_ms": k, "date": DAYS[k], "iv_index": np.float64(20.0),
             "rv_22": np.float64(0.01)} for k in range(2)]
    term = [{"date": d, "close": np.float64(25.0)} for d in DAYS[:2]]
    out = VolRegimeSignals("signals", {"min_history": 1}).run(
        None, {"rows": rows, "term": term})["rows"][1]
    for name in GATES:
        assert type(out[name]) is bool or out[name] is None, (name, type(out[name]))
    assert out["gate_term_inverted"] is False and out["gate_vrp_nonpositive"] is False
    assert out["gate_any"] is None      # the percentile is unknown with one earlier ratio


# -- ADR-0196: the lagged-rows seam and VolSizingWeights -----------------------------------------
#
# VolRegimeSignals and VolSizingWeights both read the PREVIOUS row (lag_sessions back) of the same
# instrument in asof_ms order. That mechanism (ordering, the lag, the refusals, putting the added
# fields on every row) has ONE owner, _LaggedRowSignals, with two hooks a member supplies.

import hashlib  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
from abc import ABC  # noqa: E402

import index_options.nodes as nodes_module  # noqa: E402
from index_options.nodes import VolSizingWeights  # noqa: E402

SEAM = getattr(nodes_module, "_LaggedRowSignals", None)


def _regime_scenario(seed, n_rows, instruments=("SPY", "QQQ"), rename=False):
    """Deterministic rows and term records with every awkward cell a node must read as unknown."""
    rng = random.Random(seed)
    days = [(date(2020, 1, 1) + timedelta(days=k)).isoformat() for k in range(n_rows)]
    junk = [None, 0, 0.0, -3.0, "x", True]
    rows = []
    for instrument in instruments:
        level = rng.uniform(15, 25)
        for k, day in enumerate(days):
            level = max(5.0, level + rng.gauss(0, 1.5))
            iv = rng.choice(junk) if rng.random() < 0.08 else round(level, 6)
            rv = rng.choice(junk) if rng.random() < 0.08 else round(abs(rng.gauss(0.01, 0.004)), 8)
            implied, realized = ("vix", "rv_5") if rename else ("iv_index", "rv_22")
            row = {"instrument": instrument, "asof_ms": 1000 + k, "date": day, "close": 100.0 + k,
                   implied: iv, realized: rv, "marker": f"{instrument}{k}"}
            if rng.random() < 0.05:
                del row[implied]
            if rng.random() < 0.03:
                del row["date"]
            rows.append(row)
    rng.shuffle(rows)
    term = [{"instrument": "VIX3M", "date": day,
             "close": rng.choice(junk) if rng.random() < 0.06 else round(rng.uniform(15, 28), 6)}
            for day in days if rng.random() > 0.04]
    return rows, term


#: name -> (scenario arguments, node params, sha256 of the node's whole output as JSON).
#: Frozen from VolRegimeSignals as ADR-0195 shipped it (2399882), by running these very
#: scenarios BEFORE the refactor onto the seam: every row, every added field, key order and
#: float bit included.
FROZEN_SIGNALS = {
    "two_instruments_min_history_2": (
        dict(seed=1, n_rows=40), {"min_history": 2},
        "5be6aee7032022442680b1442cc618110f4d5cf0f5b4349862987096c6c127c4"),
    "lag_2_moved_thresholds_renamed_fields": (
        dict(seed=2, n_rows=40, rename=True),
        {"lag_sessions": 2, "inverted_at": 0.9, "high_ratio_pct": 0.5, "periods_per_year": 250,
         "min_history": 3, "implied_field": "vix", "realized_field": "rv_5"},
        "d6d762b18e1b84bc90b23e44988d6bdee7f6d2255a2c21043501f63d6fb02bb4"),
    "one_instrument_default_history": (
        dict(seed=3, n_rows=360, instruments=("SPY",)), {},
        "a11e32df4d91abddf86cc0a456cda1452da9d6837ce465fc0f7a948ba09363b5"),
}


@pytest.mark.parametrize("name", FROZEN_SIGNALS)
def test_the_signals_node_is_byte_identical_to_what_adr_0195_shipped(name):
    scenario, params, digest = FROZEN_SIGNALS[name]
    rows, term = _regime_scenario(**scenario)
    out = VolRegimeSignals("signals", params).run(None, {"rows": rows, "term": term})["rows"]
    assert len(out) == len(rows)
    assert hashlib.sha256(json.dumps(out, allow_nan=False).encode()).hexdigest() == digest


def test_the_frozen_scenarios_reach_every_branch_of_the_signals():
    # a digest over a scenario that never percentiles, inverts or blanks would freeze nothing
    for name, (scenario, params, _digest) in FROZEN_SIGNALS.items():
        rows, term = _regime_scenario(**scenario)
        out = VolRegimeSignals("signals", params).run(None, {"rows": rows, "term": term})["rows"]
        assert any(r["vix_term_ratio_pct"] is not None for r in out), name
        assert any(r["vix_term_ratio"] is None for r in out), name          # a blanked reading
        assert {r["gate_any"] for r in out} == {True, False, None} or name.startswith(
            "one_instrument"), name
        assert any(r["vrp"] is None for r in out) and any(r["vrp"] is not None for r in out), name


def test_the_seam_is_one_abstract_owner_and_both_nodes_are_its_members():
    assert SEAM is not None and issubclass(SEAM, ABC)
    assert issubclass(VolRegimeSignals, SEAM) and issubclass(VolSizingWeights, SEAM)
    assert SEAM.__abstractmethods__ == frozenset({"_read", "_annotate"})
    # the mechanism is written once: a member supplies the hooks and its own knobs, nothing else
    for member in (VolRegimeSignals, VolSizingWeights):
        for owned in ("run", "_by_instrument", "_lagged", "validate_inputs", "serving_effect",
                      "_knob"):
            assert owned not in vars(member), (member.__name__, owned)


def test_a_member_missing_a_hook_refuses_at_construction_not_later():
    class NoAnnotate(SEAM):
        def _read(self, previous, context):
            return None

    class NoRead(SEAM):
        def _annotate(self, readings):
            return []

    for cls in (NoAnnotate, NoRead):
        with pytest.raises(TypeError, match="abstract"):
            cls("x", {})


class _Echo(SEAM if SEAM is not None else object):
    """The smallest member: reads the previous row's ``x`` and stamps it, with its running count."""

    def _read(self, previous, context):
        return None if previous is None else previous.get("x")

    def _annotate(self, readings):
        return [{"prev_x": r, "n_seen": sum(v is not None for v in readings[:k])}
                for k, r in enumerate(readings)]


def _echo_rows(instrument="A", n=4, **extra):
    return [{"instrument": instrument, "asof_ms": 10 + k, "x": float(k), **extra}
            for k in range(n)]


def test_the_seam_orders_lags_and_assembles_whatever_the_hooks_say():
    rows = _echo_rows("A") + _echo_rows("B", x_shift=1)
    shuffled = [rows[k] for k in (5, 2, 7, 0, 3, 6, 1, 4)]
    out = _Echo("e", {}).run(None, {"rows": shuffled})["rows"]
    assert [(r["instrument"], r["asof_ms"]) for r in out] == [
        (r["instrument"], r["asof_ms"]) for r in shuffled]             # input order, every row
    for r in out:
        k = r["asof_ms"] - 10
        assert r["prev_x"] == (None if k == 0 else float(k - 1)), r      # the row before it
        assert r["n_seen"] == max(0, k - 1)       # readings at positions before it that exist
    lagged = _Echo("e", {"lag_sessions": 2}).run(None, {"rows": rows})["rows"]
    assert [r["prev_x"] for r in lagged if r["instrument"] == "A"] == [None, None, 0.0, 1.0]


def test_the_seam_refuses_what_it_cannot_order_and_names_the_node():
    node = _Echo("e", {})
    with pytest.raises(ValueError, match=r"e: row 2 needs a numeric asof_ms"):
        node.run(None, {"rows": [_echo_rows()[0], {"instrument": "A", "x": 1.0}]})
    with pytest.raises(ValueError, match=r"e: row 1 needs a numeric asof_ms"):
        node.run(None, {"rows": ["not a row"]})
    with pytest.raises(ValueError, match=r"e: A repeats asof_ms 11"):
        node.run(None, {"rows": _echo_rows() + [dict(_echo_rows()[1])]})
    assert node.validate_inputs({"rows": []}) == []
    assert node.validate_inputs({"rows": ()}) == ["rows must be a list of forecast rows"]
    assert node.serving_effect({}, {}) == "forbidden"
    assert (node.role, node.outputs) == ("transform", ("rows",))


def test_the_seam_owns_the_shared_knobs_and_their_defaults_once():
    # implied_field / realized_field / lag_sessions / min_history: one declaration, one check
    shared = {"implied_field": "iv_index", "realized_field": "rv_22", "lag_sessions": 1,
              "min_history": 252}
    assert SEAM.SHARED_DEFAULTS == shared
    for member in (VolRegimeSignals, VolSizingWeights):
        assert {k: member.DEFAULTS[k] for k in shared} == shared
        assert set(shared) <= set(member._PARAMS)
        for bad in ({"implied_field": ""}, {"realized_field": 3}, {"lag_sessions": 0},
                    {"min_history": True}, {"lag_sessions": 1.5}):
            assert member.validate_params(bad), (member.__name__, bad)
    assert SEAM.validate_params({}) == [] and SEAM.validate_params({"lag_sessions": 3}) == []
    assert any("unknown" in p and "surprise" in p for p in SEAM.validate_params({"surprise": 1}))


# VolSizingWeights: every expected number is restated by hand from the inputs below.
#
#   row   iv   rv      the row reads its PREVIOUS row's iv and rv (one-session lag), so the
#   0     20   0.010    readings are   row 1: (20, .010)   row 2: (30, .020)   row 3: (24, .015)
#   1     30   0.020                   row 4: (40, .030)   row 5: (22, .010)
#   2     24   0.015    and with min_history 1 each weight compares today's reading to the
#   3     40   0.030    expanding MEDIAN of the EARLIER readings (row 1 has none: unknown):
#   4     22   0.010      row 2: earlier iv [20], iv^2 [400], rv^2 [1e-4]
#   5     25   0.012      row 3: [20, 30], [400, 900] -> 650, [1e-4, 4e-4] -> 2.5e-4
#                         row 4: [20, 24, 30] -> 24, [400, 576, 900] -> 576, rv^2 -> 2.25e-4
#                         row 5: [20, 24, 30, 40] -> 27, iv^2 -> (576 + 900) / 2 = 738,
#                                rv^2 [1e-4, 2.25e-4, 4e-4, 9e-4] -> 3.125e-4
#   size_inv_implied_var  = median(iv^2) / iv^2      size_inv_realized_var = median(rv^2) / rv^2
#   size_implied          = iv / median(iv)
#   row 2: 400 / 900 = 0.4444   1e-4 / 4e-4 = 0.25      30 / 20 = 1.5
#   row 3: 650 / 576 = 1.12847  2.5e-4 / 2.25e-4 = 1.1111   24 / 25 = 0.96
#   row 4: 576 / 1600 = 0.36    2.25e-4 / 9e-4 = 0.25   40 / 24 = 1.66667
#   row 5: 738 / 484 = 1.52479  3.125e-4 / 1e-4 = 3.125  22 / 27 = 0.81481

SZ_IV = [20.0, 30.0, 24.0, 40.0, 22.0, 25.0]
SZ_RV = [0.010, 0.020, 0.015, 0.030, 0.010, 0.012]
SIZE_NAMES = ("size_inv_implied_var", "size_inv_realized_var", "size_implied")
UNCLIPPED = [None, None, (400 / 900, 0.25, 1.5), (650 / 576, 2.5e-4 / 2.25e-4, 0.96),
             (0.36, 0.25, 40 / 24), (738 / 484, 3.125, 22 / 27)]


def _sizing(rows, **params):
    node = VolSizingWeights("sizing", {"min_history": 1, **params})
    return node.run(None, {"rows": rows})["rows"]


def _weights(row):
    return tuple(row[name] for name in SIZE_NAMES)


def _assert_weights(row, want):
    if want is None:
        assert _weights(row) == (None, None, None)
        return
    assert _weights(row) == pytest.approx(want, abs=1e-12)


def test_the_weights_are_the_previous_readings_against_their_expanding_median_by_hand():
    out = _sizing(_signal_rows(SZ_IV, SZ_RV))
    assert len(out) == 6
    for row, want in zip(out, UNCLIPPED):
        _assert_weights(row, want if want is None else tuple(min(4.0, max(0.25, w)) for w in want))
    assert out[2]["size_inv_realized_var"] == pytest.approx(0.25, abs=1e-12)   # on the lower clip
    # the three names are on the class, once, in this order, and the node adds exactly them
    assert VolSizingWeights.SIZE_FIELDS == SIZE_NAMES
    assert all(set(SIZE_NAMES) <= set(row) for row in out)
    assert set(out[0]) - set(_signal_rows(SZ_IV, SZ_RV)[0]) == set(SIZE_NAMES)


def test_weights_clip_at_both_ends():
    # min_weight 0.5 lifts row 2's 0.4444 / 0.25 and row 4's 0.36 / 0.25; max_weight 1.2 caps
    # row 2's 1.5, row 4's 1.6667 and row 5's 1.5248 / 3.125; what lies between is untouched
    out = _sizing(_signal_rows(SZ_IV, SZ_RV), min_weight=0.5, max_weight=1.2)
    for row, want in zip(out, UNCLIPPED):
        _assert_weights(row, want if want is None else tuple(min(1.2, max(0.5, w)) for w in want))
    assert _weights(out[2]) == (0.5, 0.5, 1.2) and _weights(out[4]) == (0.5, 0.5, 1.2)
    assert _weights(out[5])[:2] == (1.2, 1.2) and _weights(out[5])[2] == pytest.approx(22 / 27)
    assert _weights(out[3]) == pytest.approx((650 / 576, 2.5e-4 / 2.25e-4, 0.96), abs=1e-12)
    # a weight exactly on a bound is that bound; equal bounds make every weight that number
    flat = _sizing(_signal_rows(SZ_IV, SZ_RV), min_weight=1.0, max_weight=1.0)
    assert all(_weights(r) == (1.0, 1.0, 1.0) for r in flat[2:])
    assert all(_weights(r) == (None, None, None) for r in flat[:2])


def test_a_weight_is_always_a_float_even_when_the_bounds_are_integers():
    # ADR-0196 review A-N1 / B-N3: min(max_weight, max(min_weight, ratio)) returns the BOUND itself
    # when it binds, so integer bounds gave integer weights (the docstring says a plain float)
    out = _sizing(_signal_rows(SZ_IV, SZ_RV), min_weight=1, max_weight=2)
    weights = [w for row in out for w in _weights(row) if w is not None]
    assert len(weights) == 12 and all(type(w) is float for w in weights)
    # rows 2 and 4 sit on the lower bound (1), row 5's realized-variance weight on the upper (2)
    assert _weights(out[2]) == (1.0, 1.0, 1.5) and _weights(out[4])[:2] == (1.0, 1.0)
    assert _weights(out[5])[1] == 2.0
    flat = _sizing(_signal_rows(SZ_IV, SZ_RV), min_weight=1, max_weight=1)
    assert [_weights(r) for r in flat[2:]] == [(1.0, 1.0, 1.0)] * 4
    assert all(type(w) is float for r in flat[2:] for w in _weights(r))


def test_a_lag_of_two_is_the_same_weights_one_row_later():
    lag1 = _sizing(_signal_rows(SZ_IV, SZ_RV))
    lag2 = _sizing(_signal_rows(SZ_IV, SZ_RV), lag_sessions=2)
    assert _weights(lag2[0]) == _weights(lag2[1]) == _weights(lag2[2]) == (None,) * 3
    for k in range(5):
        assert _weights(lag2[k + 1]) == _weights(lag1[k]), k
    # row 3 at lag 2 reads row 1 (30, .02) against the one earlier reading, row 0's (20, .01)
    assert _weights(lag2[3]) == pytest.approx((400 / 900, 0.25, 1.5), abs=1e-12)


def test_the_same_rows_own_iv_and_realized_vol_never_inform_its_weights_and_nothing_looks_ahead():
    base = _sizing(_signal_rows(SZ_IV, SZ_RV))
    rng = random.Random(196)
    for k in range(6):
        ivs, rvs = list(SZ_IV), list(SZ_RV)
        ivs[k], rvs[k] = rng.uniform(5, 90), rng.uniform(0.001, 0.09)
        moved = _sizing(_signal_rows(ivs, rvs))
        assert [_weights(r) for r in moved[:k + 1]] == [_weights(r) for r in base[:k + 1]], k
    for k in range(1, 7):                          # a prefix is weighed the same inside the whole
        prefix = _sizing(_signal_rows(SZ_IV[:k], SZ_RV[:k]))
        assert [_weights(r) for r in prefix] == [_weights(r) for r in base[:k]], k
    assert _weights(_sizing(_signal_rows(SZ_IV[:5] + [99.0], SZ_RV[:5] + [0.5]))[5]) == \
        _weights(base[5])


def test_a_missing_or_non_positive_reading_blanks_only_the_weights_that_need_it():
    for bad in (None, 0, 0.0, -5.0, "x", True, float("nan"), float("inf")):
        ivs = [20.0, bad, 24.0, 40.0, 22.0, 25.0]
        out = _sizing(_signal_rows(ivs, SZ_RV))
        # row 2 reads the bad iv: both implied weights unknown, the realized one survives
        assert out[2]["size_inv_implied_var"] is None and out[2]["size_implied"] is None, bad
        assert out[2]["size_inv_realized_var"] == pytest.approx(0.25, abs=1e-12), bad
        # the bad reading never entered the history: row 3's median is over [20] alone (row 2 has
        # no iv), so 400 / 576 and 24 / 20 (clipped above 4? no: 1.2), not over a blank
        assert out[3]["size_inv_implied_var"] == pytest.approx(400 / 576, abs=1e-12), bad
        assert out[3]["size_implied"] == pytest.approx(24 / 20, abs=1e-12), bad
    for bad in (None, 0.0, -0.01, "x", True):
        rvs = [0.010, bad, 0.015, 0.030, 0.010, 0.012]
        out = _sizing(_signal_rows(SZ_IV, rvs))
        assert out[2]["size_inv_realized_var"] is None, bad
        assert out[2]["size_inv_implied_var"] == pytest.approx(400 / 900, abs=1e-12), bad
        assert out[2]["size_implied"] == pytest.approx(1.5, abs=1e-12), bad
        assert out[3]["size_inv_realized_var"] == pytest.approx(1e-4 / 2.25e-4, abs=1e-12), bad
    # a row without the fields at all reads as unknown, never as an error
    rows = [{"instrument": "SPY", "asof_ms": k} for k in range(3)]
    assert all(_weights(r) == (None,) * 3 for r in _sizing(rows))


def test_min_history_counts_earlier_finite_readings_exactly():
    out = _sizing(_signal_rows(SZ_IV, SZ_RV), min_history=3)
    assert [_weights(r) == (None,) * 3 for r in out] == [True, True, True, True, False, False]
    # a blank reading does not count toward the history: row 2 reads a blank, so row 5 has only
    # the readings of rows 0, 2 and 3 before row 4's
    ivs = [20.0, None, 24.0, 40.0, 22.0, 25.0]
    out = _sizing(_signal_rows(ivs, SZ_RV), min_history=3)
    assert [r["size_implied"] is None for r in out] == [True, True, True, True, True, False]
    assert out[5]["size_implied"] == pytest.approx(22 / 24)        # median of iv [20, 24, 40]
    assert out[5]["size_inv_realized_var"] is not None and out[4]["size_inv_realized_var"] is not None


def test_the_default_history_is_two_hundred_and_fifty_two_earlier_readings():
    n = 256
    rows = [{"instrument": "SPY", "asof_ms": k, "iv_index": 20.0 + (k % 7), "rv_22": 0.01 + 1e-4 * (k % 5)}
            for k in range(n)]
    out = VolSizingWeights("sizing", {}).run(None, {"rows": rows})["rows"]
    known = [k for k, r in enumerate(out) if r["size_implied"] is not None]
    assert known == list(range(253, n))      # row 253 has readings at rows 1..252: 252 earlier ones
    assert out[252]["size_implied"] is None


def test_each_instrument_reads_its_own_previous_row_and_its_own_history():
    spy = _signal_rows(SZ_IV, SZ_RV)
    qqq = _signal_rows([50.0, 60.0, 70.0, 80.0, 90.0, 100.0], [0.02] * 6, instrument="QQQ")
    out = _sizing([r for pair in zip(qqq, spy) for r in pair])             # interleaved
    assert [_weights(r) for r in out if r["instrument"] == "SPY"] == [
        _weights(r) for r in _sizing(spy)]
    # QQQ's own series: row 2 reads 60 against the median of [50]
    qqq_out = [r for r in out if r["instrument"] == "QQQ"]
    assert _weights(qqq_out[2])[2] == pytest.approx(min(4.0, 60 / 50))
    assert _weights(qqq_out[2])[1] == pytest.approx(1.0)       # a constant realized vol: ratio 1


def test_every_row_comes_back_unchanged_in_input_order_and_the_input_is_untouched():
    rows = _signal_rows(SZ_IV, SZ_RV)
    before = copy.deepcopy(rows)
    order = (3, 0, 5, 2, 1, 4)
    shuffled = [rows[k] for k in order]
    out = _sizing(shuffled)
    assert rows == before
    assert [r["marker"] for r in out] == [f"SPY{k}" for k in order]
    for original, row in zip(shuffled, out):
        assert {k: v for k, v in row.items() if k not in SIZE_NAMES} == original
    assert {r["marker"]: _weights(r) for r in out} == {
        r["marker"]: _weights(r) for r in _sizing(rows)}
    assert all(type(w) is float for r in out for w in _weights(r) if w is not None)


def test_the_field_names_are_knobs_for_the_sizing_node_too():
    rows = [{**r, "vix": r["iv_index"], "rv_5": r["rv_22"]} for r in _signal_rows(SZ_IV, SZ_RV)]
    for r in rows:
        del r["iv_index"], r["rv_22"]
    renamed = _sizing(rows, implied_field="vix", realized_field="rv_5")
    assert [_weights(r) for r in renamed] == [_weights(r) for r in _sizing(_signal_rows(SZ_IV, SZ_RV))]


def test_the_sizing_defaults_are_the_owners_numbers_typed_out_independently():
    # the generator reads DEFAULTS and writes them into 56 documents, so a test that reads them
    # too asserts nothing: these are ADR-0196's answers (previous session, 252 sessions of history,
    # weights between a quarter and four times)
    assert VolSizingWeights.DEFAULTS == {
        "implied_field": "iv_index", "realized_field": "rv_22", "lag_sessions": 1,
        "min_history": 252, "min_weight": 0.25, "max_weight": 4.0}
    assert VolSizingWeights._PARAMS == tuple(sorted((
        "implied_field", "realized_field", "lag_sessions", "min_history", "min_weight",
        "max_weight")))
    assert VolSizingWeights.SIZE_FIELDS == ("size_inv_implied_var", "size_inv_realized_var",
                                            "size_implied")
    assert VolSizingWeights.role == "transform" and VolSizingWeights.outputs == ("rows",)
    assert VolSizingWeights.serving_effect({}, {}) == "forbidden"
    # omitted knobs ARE these defaults: 253 rows is the first to carry a weight
    assert VolSizingWeights.validate_params({}) == []
    assert VolSizingWeights.validate_params(dict(VolSizingWeights.DEFAULTS)) == []


@pytest.mark.parametrize("change", [
    {"surprise": 1}, {"implied_field": ""}, {"implied_field": 3}, {"realized_field": ""},
    {"realized_field": None}, {"lag_sessions": 0}, {"lag_sessions": -1}, {"lag_sessions": True},
    {"lag_sessions": 1.5}, {"min_history": 0}, {"min_history": True}, {"min_history": 1.5},
    {"min_history": "252"}, {"min_weight": 0}, {"min_weight": -0.25}, {"min_weight": True},
    {"min_weight": "0.25"}, {"min_weight": float("nan")}, {"min_weight": float("inf")},
    {"max_weight": 0}, {"max_weight": -4.0}, {"max_weight": True}, {"max_weight": None},
    {"max_weight": float("nan")}, {"min_weight": 5.0}, {"max_weight": 0.2},
    {"min_weight": 2.0, "max_weight": 1.0},
])
def test_every_sizing_knob_refuses_a_bad_value_and_a_crossed_clip(change):
    assert VolSizingWeights.validate_params(change)
    with pytest.raises(ConfigError, match=next(iter(change))):
        VolSizingWeights("sizing", change)


def test_a_clip_with_equal_bounds_is_accepted_and_the_inputs_must_be_a_list():
    assert VolSizingWeights.validate_params({"min_weight": 1, "max_weight": 1}) == []
    node = VolSizingWeights("sizing", {})
    assert node.validate_inputs({"rows": []}) == []
    assert node.validate_inputs({"rows": None}) == ["rows must be a list of forecast rows"]
    assert node.validate_inputs({}) == ["rows must be a list of forecast rows"]
    rows = _signal_rows(SZ_IV, SZ_RV)
    with pytest.raises(ValueError, match="repeats"):
        node.run(None, {"rows": rows + [dict(rows[2])]})
    with pytest.raises(ValueError, match="asof_ms"):
        node.run(None, {"rows": [{**rows[0], "asof_ms": None}]})
    both = rows + [{**r, "instrument": "QQQ"} for r in rows]      # the same stamp on two instruments
    assert len(node.run(None, {"rows": both})["rows"]) == 12


def test_numpy_scalar_readings_give_plain_float_weights():
    np = pytest.importorskip("numpy")
    rows = [{"instrument": "SPY", "asof_ms": k, "iv_index": np.float64(SZ_IV[k]),
             "rv_22": np.float64(SZ_RV[k])} for k in range(6)]
    out = _sizing(rows)
    assert all(type(w) is float for r in out for w in _weights(r) if w is not None)
    assert _weights(out[5]) == pytest.approx(tuple(min(4.0, max(0.25, w)) for w in UNCLIPPED[5]))


# -- ExactExpiryPanelRead (ADR-0217): the child's one exact-expiry reader -------------------------

from index_options import cdf_study  # noqa: E402
from index_options.nodes import ExactExpiryPanelRead  # noqa: E402

#: The reader's knobs restated independently of the class: the tail-data `read()` keys minus
#: `archive_root` (the `prepare` stage's), plus `columns`. Pinned to the shipped document below.
PANEL_READ_KEYS = {
    "root", "surface", "lifecycle", "symbols", "price_source", "iv_source", "since", "max_dte",
    "lags", "windows", "feature_gap_days", "reference_floor", "spot_tolerance", "chain_features",
    "raw_chain", "market_symbols", "fred_market_symbols", "surface_features", "ohlc_windows",
    "matched_dte_vrp", "columns", "reference_window", "change_lags", "directional_windows",
    "periods_per_year", "calendar", "calendar_pad_days", "dividend_field",
    "exact_dte", "reader", "keyed_tables", "corporate_actions", "cohort_columns",
    "as_of_acquisition_ms"}
PANEL_READ_REQUIRED = PANEL_READ_KEYS - {
    "chain_features", "raw_chain", "market_symbols", "fred_market_symbols", "surface_features",
    "ohlc_windows", "matched_dte_vrp", "reference_window", "change_lags", "directional_windows",
    "periods_per_year", "calendar", "calendar_pad_days", "dividend_field", "exact_dte", "reader",
    "keyed_tables", "corporate_actions", "cohort_columns", "as_of_acquisition_ms"}


def _panel_read_params(**over):
    params = {
        "root": "x", "surface": "s.parquet", "lifecycle": "l.parquet", "symbols": {"QQQ": "VXN"},
        "price_source": "p", "iv_source": "i", "since": "2020-01-01", "max_dte": 45, "lags": 22,
        "windows": [1, 5, 22], "feature_gap_days": 7, "reference_floor": 0.001,
        "spot_tolerance": 0.02, "columns": ["symbol", "quote_date", "expiry", "a", "b"]}
    params.update(over)
    return params


class StubPanel:
    """Stands in for ExactExpiryCDFPanel: records its config, counts reads, returns a fixed frame."""

    configs = []
    reads = 0

    def __init__(self, config):
        self.config = config
        type(self).configs.append(dict(config))

    def read(self):
        import numpy as np
        import pandas as pd
        type(self).reads += 1
        self.refused = {"QQQ": {"non_session_quote": 0}}
        self.source_hashes = {"surface": "abc"}
        self.reader_fingerprints = {"QQQ": {"sha256": "fixture"}}
        return pd.DataFrame({
            "symbol": ["QQQ", "QQQ"], "quote_date": ["2020-01-02", "2020-01-03"],
            "expiry": ["2020-02-21", "2020-02-21"], "a": [1.5, np.nan], "b": [2, 3],
            "unrequested": [9., 9.]})

    def provenance(self):
        return {"refused": self.refused, "sha256": self.source_hashes,
                "readers": self.reader_fingerprints}


@pytest.fixture
def stub_panel(monkeypatch):
    StubPanel.configs, StubPanel.reads = [], 0
    monkeypatch.setattr(cdf_study, "ExactExpiryCDFPanel", StubPanel)
    return StubPanel


def test_the_panel_reader_declares_its_role_ports_and_forbids_serving():
    assert ExactExpiryPanelRead.role == "data"
    assert ExactExpiryPanelRead.outputs == ("records", "provenance", "cohort")
    assert ExactExpiryPanelRead.serving_effect({}, {}) == "forbidden"
    assert "ExactExpiryPanelRead" in __import__("index_options.nodes", fromlist=["x"]).__all__


def test_the_panel_readers_params_are_the_tail_data_read_keys_plus_columns(child_root):
    data = json.loads((child_root / "configs/run-predictive-cdf-tail-data.json").read_text())["data"]
    study_conventions = {"reference_window", "change_lags", "directional_windows",
                         "periods_per_year", "calendar", "calendar_pad_days", "dividend_field"}
    assert set(ExactExpiryPanelRead._PARAMS) == PANEL_READ_KEYS == (
        set(data) - {"archive_root"} | {"columns"} | study_conventions   # frozen tail config omits them
        | {"exact_dte", "reader", "keyed_tables", "corporate_actions",   # ADR-0230 additions
           "cohort_columns"}
        | {"as_of_acquisition_ms"})                                      # ADR-0236 amendment 3
    assert set(ExactExpiryPanelRead._PARAMS) & {"decision_regions", "macro_event_calendars"} == set()


@pytest.mark.parametrize("change, match", [
    ({"archive_root": "a"}, "archive_root"), ({"decision_regions": {}}, "decision_regions"),
    ({"macro_event_calendars": {}}, "macro_event_calendars"), ({"surprise": 1}, "surprise"),
    ({"columns": []}, "columns"), ({"columns": "a"}, "columns"), ({"columns": ["a", "a"]}, "columns"),
    ({"columns": ["a", ""]}, "columns"), ({"symbols": {}}, "symbols"),
    ({"symbols": {"QQQ": 3}}, "symbols"), ({"symbols": ["QQQ"]}, "symbols"),
    ({"max_dte": 0}, "max_dte"), ({"lags": True}, "lags"),
])
def test_the_panel_reader_refuses_what_it_does_not_declare_or_a_bad_value(change, match):
    problems = ExactExpiryPanelRead.validate_params(_panel_read_params(**change))
    assert any(match in p for p in problems), problems
    with pytest.raises(ConfigError, match=match):
        ExactExpiryPanelRead("panel", _panel_read_params(**change))


@pytest.mark.parametrize("name", sorted(PANEL_READ_REQUIRED))
def test_every_required_panel_reader_param_is_named_when_missing(name):
    params = _panel_read_params()
    del params[name]
    assert any(name in p for p in ExactExpiryPanelRead.validate_params(params))
    assert ExactExpiryPanelRead.validate_params(_panel_read_params()) == []


def test_the_panel_reader_emits_only_its_columns_as_plain_python_with_nulls(stub_panel):
    node = ExactExpiryPanelRead("panel", _panel_read_params())
    out = node.run(None, {})
    assert out["records"] == [
        {"symbol": "QQQ", "quote_date": "2020-01-02", "expiry": "2020-02-21", "a": 1.5, "b": 2},
        {"symbol": "QQQ", "quote_date": "2020-01-03", "expiry": "2020-02-21", "a": None, "b": 3}]
    assert [type(r["a"]) for r in out["records"]] == [float, type(None)]
    assert type(out["records"][0]["b"]) is int
    assert out["provenance"] == {"refused": {"QQQ": {"non_session_quote": 0}},
                                 "sha256": {"surface": "abc"},
                                 "readers": {"QQQ": {"sha256": "fixture"}}}
    assert "columns" not in stub_panel.configs[0]
    assert stub_panel.configs[0] == {k: v for k, v in _panel_read_params().items() if k != "columns"}


def test_a_column_the_read_lacks_refuses_by_name(stub_panel):
    node = ExactExpiryPanelRead("panel", _panel_read_params(columns=["symbol", "nope"]))
    with pytest.raises(ValueError, match="nope"):
        node.run(None, {})


def test_the_panel_reader_reads_once_and_its_fingerprint_names_what_run_emits(stub_panel):
    node = ExactExpiryPanelRead("panel", _panel_read_params())
    fingerprint = node.fingerprint()
    out = node.run(None, {})
    assert stub_panel.reads == 1
    assert fingerprint == {"kind": "ExactExpiryPanelRead", "rows": len(out["records"]),
                           "provenance": out["provenance"]}
    assert len(json.dumps(fingerprint, allow_nan=False)) < 65536
    assert node.run(None, {}) is not None and stub_panel.reads == 1
    other = ExactExpiryPanelRead("panel", _panel_read_params())
    assert other.fingerprint() == fingerprint            # same data, same identity


@pytest.mark.parametrize("name", ["surface", "lifecycle", "chain_features"])
def test_the_panel_reader_takes_a_store_reference_or_a_path_and_refuses_a_malformed_entry(
        name, stub_panel):
    good = {"source": "exact-expiry-tables", "stream": "files", "relpath": "t.parquet"}
    assert ExactExpiryPanelRead.validate_params(_panel_read_params(**{name: good})) == []
    assert ExactExpiryPanelRead.validate_params(_panel_read_params(**{name: "t.parquet"})) == []
    for bad in ({"source": "s", "stream": "files"}, {**good, "extra": 1}, 3, ""):
        problems = ExactExpiryPanelRead.validate_params(_panel_read_params(**{name: bad}))
        assert any(p.startswith(name) for p in problems), (bad, problems)
        with pytest.raises(ConfigError, match=name):
            ExactExpiryPanelRead("panel", _panel_read_params(**{name: bad}))
    node = ExactExpiryPanelRead("panel", _panel_read_params(**{name: good}))
    node.run(None, {})
    assert stub_panel.configs[-1][name] == good          # handed to the adapter unchanged


def test_cohort_columns_add_a_second_port_of_those_columns_and_stay_out_of_the_reader_config(stub_panel):
    node = ExactExpiryPanelRead("panel", _panel_read_params(cohort_columns=["symbol", "quote_date"]))
    out = node.run(None, {})
    assert out["cohort"] == [{"symbol": "QQQ", "quote_date": "2020-01-02"},
                             {"symbol": "QQQ", "quote_date": "2020-01-03"}]
    assert len(out["records"]) == 2 and "a" in out["records"][0]
    assert "cohort_columns" not in stub_panel.configs[0]


def test_without_cohort_columns_the_cohort_port_is_empty(stub_panel):
    assert ExactExpiryPanelRead("panel", _panel_read_params()).run(None, {})["cohort"] == []


def test_a_cohort_column_the_read_lacks_refuses_by_name(stub_panel):
    node = ExactExpiryPanelRead("panel", _panel_read_params(cohort_columns=["nope"]))
    with pytest.raises(ValueError, match="nope"):
        node.run(None, {})


@pytest.mark.parametrize("seed", range(15))
@pytest.mark.parametrize("banded", [False, True])
def test_robust_condor_all_strikes_matches_enumeration(seed, banded):
    import index_options.nodes as nodes
    assert hasattr(nodes, "RobustCondorSelect"), "missing exact robust condor selector"
    from itertools import combinations
    from dskit.pipeline.libs.predictive_cdf import DiscreteCDFGrid
    grid = [70., 82., 91., 103., 116., 135.]
    masses = [.05, .15, .3, .25, .15, .1]
    context = dict(decision_id="fixture", arm_id="base", symbol="SYNTH",
                   quote_date="2025-02-04", expiry="2025-03-07",
                   grid=grid, masses=masses, spot=100., rho=.003, legs={})
    for role, prices in {"LP":[20,14,8,5,3,1], "SP":[21,15,9,6,4,2],
                         "SC":[1,3,5,8,14,20], "LC":[2,4,6,9,15,21]}.items():
        context["legs"][role] = [
            dict(index=i, price=float(price), haircut=.03, contract_id=f"{role}-{i}")
            for i, price in enumerate(prices)]
    import numpy as np
    rng = np.random.default_rng(seed)
    context["rho"] = [0., .003, .03][seed % 3]
    for role in context["legs"]:
        for row in context["legs"][role]:
            row["price"] += float(rng.uniform(0, 4))
        if seed % 2:
            context["legs"][role] = [r for r in context["legs"][role] if r["index"] != 2]
    if banded:
        cumulative = np.cumsum(masses)
        context["q_lo"] = np.maximum(0., cumulative-.02).tolist()
        context["q_hi"] = np.minimum(1., cumulative+.02).tolist()
        context["q_lo"][-1] = context["q_hi"][-1] = 1.
    params = dict(multiplier=100, fee_per_contract_usd=.65, tie_tolerance_usd=1e-7,
                  max_absolute_gap_usd=1e-7, max_relative_gap=1e-8)
    node = nodes.RobustCondorSelect("select", params)
    out = node.run(None, {"context":context})
    primal = DiscreteCDFGrid.from_masses(grid, masses)
    scores = []
    for indices in combinations(range(len(grid)), 4):
        by_role = {role:{r["index"]:r for r in context["legs"][role]}
                   for role in ("LP","SP","SC","LC")}
        if any(i not in by_role[role] for role,i in zip(("LP","SP","SC","LC"),indices)):
            continue
        lp, sp, sc, lc = [grid[i] for i in indices]
        losses = [max(sp-s,0)-max(lp-s,0)+max(s-sc,0)-max(s-lc,0) for s in grid]
        credit = sum((1 if role in ("SP","SC") else -1) *
                     by_role[role][i]["price"]-.03
                     for role,i in zip(("LP","SP","SC","LC"),indices))
        scores.append(100*(credit-primal.worst_expected_loss(losses,context["rho"],100,
                      q_lo=context.get("q_lo"),q_hi=context.get("q_hi"))) - 2.60)
    assert out["decision"]["robust_value_usd"] == pytest.approx(max(0.,max(scores)), abs=1e-6)
    assert len(out["evidence"]["solves"]) == 2


def _robust_tie_fixture(credit=1., fee=0.):
    from index_options.nodes import RobustCondorSelect
    params = dict(multiplier=100, fee_per_contract_usd=fee, tie_tolerance_usd=1e-6,
                  max_absolute_gap_usd=1e-7, max_relative_gap=1e-8)
    context = dict(decision_id="fixture", arm_id="base", symbol="SYNTH",
                   quote_date="2025-02-04", expiry="2025-03-07",
                   grid=[80.,90.,110.,120.], masses=[.05,.45,.45,.05],
                   spot=100., rho=0., legs={})
    for i,role in enumerate(("LP","SP","SC","LC")):
        context["legs"][role] = [dict(index=i,price=1.+(credit/2 if role in ("SP","SC") else 0.),
                                     haircut=0.,contract_id=role)]
    return RobustCondorSelect("select",params), context


@pytest.mark.parametrize("credit,fee,status", [(1.,0.,"no_trade"), (1.02,.65,"no_trade"),
                                              (1.03,.65,"trade")])
def test_robust_condor_tie_and_fee_units(credit, fee, status):
    node, context = _robust_tie_fixture(credit, fee)
    result = node.run(None, {"context":context})
    assert result["decision"]["status"] == status
    assert len(result["evidence"]["solves"]) == 2
    if status == "trade":
        assert result["decision"]["robust_value_usd"] == pytest.approx(.4,abs=1e-7)
        assert sum(x["fee_usd"] for x in result["decision"]["legs"]) == 2.6


def test_robust_condor_empty_eligibility_retains_no_trade():
    node, context = _robust_tie_fixture(20.)
    context["legs"]["LP"] = []
    result = node.run(None, {"context":context})
    assert result["decision"]["status"] == "no_trade"
    assert result["decision"]["legs"] == []


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(rho=float("nan")),
    lambda c: c.update(spot=0.),
    lambda c: c.update(masses=[.1,.1,.1,.1]),
    lambda c: c.update(q_lo=[.1,.1,.1,1.], q_hi=[.2,.2,.2,1.]),
    lambda c: c["legs"]["LP"].append(dict(c["legs"]["LP"][0])),
    lambda c: c["legs"]["LP"][0].update(price=float("inf")),
    lambda c: c["legs"]["LP"][0].update(index=True),
    lambda c: c["legs"]["SP"][0].update(haircut=100.),
])
def test_robust_condor_invalid_context_refuses_before_solver(mutate, monkeypatch):
    node, context = _robust_tie_fixture()
    mutate(context)
    monkeypatch.setattr(node, "_resolve_solver",
                        lambda: pytest.fail("invalid input woke the solver"))
    with pytest.raises(ValueError):
        node.run(None, {"context":context})


def test_robust_condor_missing_explicit_tie_policy_refuses():
    node, _ = _robust_tie_fixture()
    params = dict(node.params)
    del params["tie_tolerance_usd"]
    with pytest.raises(ConfigError, match="tie_tolerance_usd"):
        type(node)("select", params)


def test_robust_condor_uncertified_primary_skips(monkeypatch):
    node, context = _robust_tie_fixture()
    monkeypatch.setattr(node, "_certified", lambda record: False)
    result = node.run(None, {"context":context})
    assert result["decision"]["status"] == "skipped"
    assert result["decision"]["reason"] == "uncertified_primary"
    assert len(result["evidence"]["solves"]) == 1


def test_robust_condor_uncertified_secondary_skips(monkeypatch):
    node, context = _robust_tie_fixture()
    monkeypatch.setattr(node, "_secondary_certified", lambda record: False)
    result = node.run(None, {"context":context})
    assert result["decision"]["status"] == "skipped"
    assert result["decision"]["reason"] == "uncertified_tie"
    assert "legs" not in result["decision"]
    assert len(result["evidence"]["solves"]) == 2


def test_robust_condor_solver_failure_escapes_without_a_decision(monkeypatch):
    node, context = _robust_tie_fixture()
    before = copy.deepcopy(context)

    class FailedSolver:
        def solve(self, model):
            raise RuntimeError("controlled backend failure")

    monkeypatch.setattr(node, "_resolve_solver", lambda: FailedSolver())
    with pytest.raises(RuntimeError, match="controlled backend failure"):
        node.run(None, {"context":context})
    assert node.solve_record is None
    assert node._certificates == []
    assert context == before


def test_robust_condor_binary_certificate_has_dimensionless_bounds():
    from types import SimpleNamespace
    node, _ = _robust_tie_fixture()
    assert node._secondary_certified(SimpleNamespace(
        termination="optimal", objective=1., bound=.5))
    assert not node._secondary_certified(SimpleNamespace(
        termination="optimal", objective=1., bound=0.))
    assert node._secondary_certified(SimpleNamespace(
        termination="optimal", objective=0., bound=0.))


@pytest.mark.parametrize("multiplier", [100., True, 0, -1])
def test_robust_condor_contract_multiplier_is_a_positive_integer(multiplier):
    node, _ = _robust_tie_fixture()
    with pytest.raises(ConfigError, match="multiplier"):
        type(node)("select", {**node.params, "multiplier":multiplier})


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(quote_date="not-a-date"),
    lambda c: c.update(quote_date="2025-2-4"),
    lambda c: c.update(expiry="2025-02-04"),
    lambda c: c.update(expiry="2024-01-01"),
    lambda c: c.update(expiry="2025-02-31"),
    lambda c: c["legs"]["SP"][0].update(contract_id="LP"),
    lambda c: c["legs"]["SC"][0].update(contract_id="LP"),
])
def test_robust_condor_inconsistent_identity_refuses_before_solver(mutate, monkeypatch):
    node, context = _robust_tie_fixture(5.)
    mutate(context)
    monkeypatch.setattr(node, "_resolve_solver",
                        lambda: pytest.fail("invalid identity woke the solver"))
    with pytest.raises(ValueError):
        node.run(None, {"context": context})


def test_robust_condor_same_contract_may_be_eligible_on_both_sides():
    node, context = _robust_tie_fixture(5.)
    context["legs"]["SP"].append(dict(context["legs"]["LP"][0]))
    decision = node.run(None, {"context": context})["decision"]
    assert decision["status"] == "trade"
    assert len({leg["contract"] for leg in decision["legs"]}) == 4


@pytest.mark.parametrize("upper,tolerance,status", [
    (200., 150., "skipped"), (120., 150., "no_trade"),
    (120., 50., "trade"), (100., 100., "no_trade"), (100., 99., "trade"),
])
def test_robust_condor_primary_bounds_must_certify_the_no_trade_tie(
        upper, tolerance, status, monkeypatch):
    from dataclasses import replace
    node, context = _robust_tie_fixture(2.)
    node = type(node)("select", {**node.params, "tie_tolerance_usd": tolerance,
                               "max_absolute_gap_usd": 101., "max_relative_gap": 1.1})
    build_record = node._build_solve_record
    calls = []

    def valid_uncertain_primary(*args):
        record = build_record(*args)
        calls.append(record)
        if len(calls) == 1:
            assert record.objective == pytest.approx(100.)
            return replace(record, bound=upper, gap=abs(upper-record.objective)/record.objective)
        return record

    monkeypatch.setattr(node, "_build_solve_record", valid_uncertain_primary)
    result = node.run(None, {"context": context})
    assert result["decision"]["status"] == status
    if status == "skipped":
        assert result["decision"]["reason"] == "uncertified_no_trade_tie"
        assert len(result["evidence"]["solves"]) == 1

def _robust_batch_params():
    arms = {
        "nominal": {"radius": "zero"}, "rho_base": {"radius": "calibrated"},
        "haircut_0": {"radius": "calibrated", "haircut_multiplier": 0.},
        "haircut_half": {"radius": "calibrated", "haircut_multiplier": .5},
        "haircut_double": {"radius": "calibrated", "haircut_multiplier": 2.},
        "liquidity_3": {"radius": "calibrated", "min_trade_count": 3},
        "liquidity_10": {"radius": "calibrated", "min_trade_count": 10},
        "liquidity_20": {"radius": "calibrated", "min_trade_count": 20},
    }
    return dict(multiplier=100, fee_per_contract_usd=.65, tie_tolerance_usd=1e-7,
                max_absolute_gap_usd=1e-7, max_relative_gap=1e-8,
                calibration_window={"start": "2024-01-01", "end": "2024-12-31"},
                entry_window={"start": "2025-02-04", "end": "2025-02-04"},
                protected_end_before="2026-01-01", standard_multiplier=100,
                liquidity={"min_trade_count": 5, "min_volume": 1},
                haircut_tiers={name: {"pct": .01, "floor_usd": .01}
                               for name in ("low", "mid", "high")},
                arms=arms, tier_tie_policy="symbol_ascending_rank")


def _robust_batch_inputs():
    entry = []
    for right, values in (("put", [(80., .1), (90., 3.)]),
                          ("call", [(110., 3.), (120., .1)])):
        for strike, vwap in values:
            entry.append(dict(contract=f"{right}-{strike}", symbol="AAA",
                              quote_date="2025-02-04", expiry="2025-03-07",
                              right=right, strike=strike, vwap=vwap,
                              trade_count=10, volume=1, multiplier=100))
    calibration = [dict(contract=f"cal-{symbol}", symbol=symbol,
                        quote_date="2024-06-03", expiry="2024-07-05",
                        right="put", strike=80., vwap=1., trade_count=7,
                        volume=1, multiplier=100)
                   for symbol in ("AAA", "ZZZ")]
    mass = dict(decision_id="batch-fixture", symbol="AAA", quote_date="2025-02-04",
                expiry="2025-03-07", settlement_date="2025-03-07", grid=[80., 90., 110., 120.],
                masses=[.05, .45, .45, .05], spot=100., fit_identity="fit", checkpoint_identity="checkpoint", input_identity="input", source_identity="source")
    return dict(chain=calibration + entry, projected_masses=[mass],
                rho={"2025-02": 0.}, #
                # monthly calibration map
                skips=[], holding_exclusions=[])


def test_robust_condor_batch_delegates_to_single_context_solver():
    from index_options.nodes import RobustCondorBatchSelect, RobustCondorSelect
    params, inputs = _robust_batch_params(), _robust_batch_inputs()
    batch = RobustCondorBatchSelect("batch", params)
    out = batch.run(None, inputs)
    assert out["evidence"]["tiers"] == {"AAA": "low", "ZZZ": "mid"}
    nominal = next(row for row in out["decisions"] if row["arm_id"] == "nominal")
    context = batch._batch_context(inputs["projected_masses"][0], inputs["chain"][2:], "low",
                             "nominal", 0.)
    direct = RobustCondorSelect("single", {key: params[key]
                                           for key in RobustCondorSelect._PARAMS if key in params}).run(
                                               None, {"context": context})["decision"]
    direct.update({name: inputs["projected_masses"][0][name] for name in ("fit_identity", "checkpoint_identity", "input_identity", "source_identity")})
    direct["forecast_settlement_date"] = inputs["projected_masses"][0]["settlement_date"]
    assert nominal == direct
    assert all(row["arm_id"] in params["arms"] for row in out["decisions"])
    assert all(len(row["legs"]) == 4 for row in out["selections"])


def test_robust_condor_batch_null_calibration_rho_skips_only_calibrated_arms():
    from index_options.nodes import RobustCondorBatchSelect
    inputs = _robust_batch_inputs()
    inputs["rho"] = {}
    out = RobustCondorBatchSelect("batch", _robust_batch_params()).run(None, inputs)
    assert [row["arm_id"] for row in out["decisions"]] == ["nominal"]
    assert {(row["arm_id"], row["reason"]) for row in out["skips"]} == {
        (arm, "missing_calibrated_rho") for arm in _robust_batch_params()["arms"]
        if arm != "nominal"}

class TestExactDteBarChain:
    def node(self):
        from index_options import nodes
        cls = getattr(nodes, "ExactDteBarChain", None)
        assert cls is not None, "exact-DTE adapter is missing"
        return cls("chain", {
            "fields": {key: key for key in ("contract", "symbol", "expiry", "right",
                       "strike", "date", "vwap", "trade_count", "volume", "multiplier")},
            "dte": 31, "multiplier": 100, "end_before": "2026-01-01",
            "windows": [{"name": "calibration", "start": "2024-02-06",
                         "end": "2024-12-31"},
                        {"name": "entry", "start": "2025-02-04", "end": "2025-11-28"}]})

    def row(self, **overrides):
        return dict({"contract": "A-contract", "symbol": "A", "date": "2025-02-04",
                     "expiry": "2025-03-07", "right": "put", "strike": 100.,
                     "vwap": 2., "trade_count": 10, "volume": 20, "multiplier": 100,
                     "source_identity": "saved-source"}, **overrides)

    def test_valid_record_retains_provenance_and_named_phase(self):
        out = self.node().run(None, {"records": [self.row()]})
        assert len(out["records"]) == 1 and not out["skips"]
        row = out["records"][0]
        assert (row["quote_date"], row["phase"], row["type"]) == (
            "2025-02-04", "entry", "put")
        assert row["source_identity"] == "saved-source"

    @pytest.mark.parametrize("changes,reason", [
        ({"expiry": "2025-03-08"}, "dte"),
        ({"date": "2025-12-02", "expiry": "2026-01-02"}, "boundary"),
        ({"date": "2025-01-07", "expiry": "2025-02-07"}, "window"),
        ({"vwap": float("nan")}, "vwap"),
        ({"trade_count": True}, "trade_count"),
        ({"multiplier": 10}, "multiplier"),
        ({"right": "future"}, "right"),
    ])
    def test_ineligible_rows_have_explicit_reasons(self, changes, reason):
        out = self.node().run(None, {"records": [self.row(**changes)]})
        assert not out["records"]
        assert reason in out["skips"][0]["reason"]
        assert out["skips"][0]["contract"] == "A-contract"

    def test_duplicate_contract_date_refuses_whole_input(self):
        row = self.row()
        with pytest.raises(ValueError, match="duplicate"):
            self.node().run(None, {"records": [row, dict(row)]})

    # ADR-0256: fill_session. "same" is the legacy identity; "next" joins the t' bar.
    def next_node(self, **over):
        base = self.node().params
        return type(self.node())("chain", {**base, "fill_session": "next", **over})

    def test_fill_session_same_is_the_legacy_identity(self):
        legacy = self.node().run(None, {"records": [self.row()]})
        same = type(self.node())("chain", {**self.node().params, "fill_session": "same"})
        out = same.run(None, {"records": [self.row()]})
        assert out["records"] == legacy["records"] and "fill" not in out["records"][0]
        assert out["evidence"].value == legacy["evidence"].value
        with pytest.raises(ValueError, match="fills"):
            same.run(None, {"records": [self.row()], "fills": []})

    @pytest.mark.parametrize("value", ["prev", "", None, 1, "NEXT"])
    def test_fill_session_has_two_values(self, value):
        cls = type(self.node())
        assert cls.validate_params({**self.node().params, "fill_session": value})
        assert cls.validate_params({**self.node().params, "fill_session": "next"}) == []

    def test_fill_session_next_needs_the_fills_port(self):
        with pytest.raises(ValueError, match="fills"):
            self.next_node().run(None, {"records": [self.row()]})

    def test_next_attaches_the_bar_on_the_streams_first_later_session(self):
        fills = [self.row(date="2025-02-04", vwap=9.), self.row(date="2025-02-06", vwap=3.,
                                                                trade_count=7, volume=8),
                 self.row(date="2025-02-05", vwap=2.5, trade_count=4, volume=6),
                 self.row(contract="other", date="2025-02-05", vwap=1.)]
        out = self.next_node().run(None, {"records": [self.row()], "fills": fills})
        assert out["records"][0]["fill_session"] == "2025-02-05"
        assert out["records"][0]["fill"] == {"date": "2025-02-05", "vwap": 2.5,
                                             "trade_count": 4, "volume": 6}
        assert out["records"][0]["quote_date"] < out["records"][0]["fill"]["date"] \
            < out["records"][0]["expiry"]
        assert out["evidence"].value["fill_sessions"] == 3

    def test_next_leaves_a_contract_without_a_bar_on_t_prime_unfilled_not_filled_later(self):
        # ADR-0256 S3 lens M1: t' is the stream's calendar; a later bar of this contract
        # never substitutes for the missing t' bar
        fills = [self.row(contract="other", date="2025-02-05", vwap=1.),
                 self.row(date="2025-02-06", vwap=3.)]
        out = self.next_node().run(None, {"records": [self.row()], "fills": fills})
        assert out["records"][0]["fill_session"] == "2025-02-05"
        assert out["records"][0]["fill"] is None
        # with no bar anywhere on 02-05 the calendar's next session is 02-06
        out = self.next_node().run(None, {"records": [self.row()], "fills": fills[1:]})
        assert out["records"][0]["fill_session"] == "2025-02-06"
        assert out["records"][0]["fill"]["vwap"] == 3.

    def test_next_attaches_none_when_no_later_session_precedes_expiry(self):
        fills = [self.row(date="2025-02-04"), self.row(date="2025-03-07", vwap=1.),
                 self.row(date="2025-03-10", vwap=1.)]
        out = self.next_node().run(None, {"records": [self.row()], "fills": fills})
        assert out["records"][0]["fill"] is None and out["records"][0]["fill_session"] is None
        empty = self.next_node().run(None, {"records": [self.row()], "fills": []})["records"][0]
        assert empty["fill"] is None and empty["fill_session"] is None

    def test_next_joins_each_decision_date_to_its_own_later_session(self):
        rows = [self.row(), self.row(contract="B", date="2025-02-05", expiry="2025-03-08")]
        fills = [self.row(date="2025-02-05", vwap=2.5), self.row(contract="B", date="2025-02-06", expiry="2025-03-08", vwap=2.6)]
        out = self.next_node().run(None, {"records": rows, "fills": fills})
        assert [r["fill"]["date"] for r in out["records"]] == ["2025-02-05", "2025-02-06"]
        assert [r["fill_session"] for r in out["records"]] == ["2025-02-05", "2025-02-06"]

    def test_next_refuses_fill_rows_at_or_past_the_protected_boundary(self):
        for day in ("2026-01-01", "2026-01-02"):
            with pytest.raises(ValueError, match="end_before|boundary"):
                self.next_node().run(None, {"records": [self.row()],
                                            "fills": [self.row(date=day, expiry="2026-02-01")]})

    @pytest.mark.parametrize("changes", [{"vwap": float("nan")}, {"trade_count": True},
                                         {"multiplier": 10}, {"date": "x"}, {"contract": ""}])
    def test_next_refuses_malformed_fill_rows(self, changes):
        with pytest.raises(ValueError):
            self.next_node().run(None, {"records": [self.row()],
                                        "fills": [self.row(**{"date": "2025-02-05", **changes})]})

    def test_next_refuses_duplicate_fill_bars_and_a_contract_that_disagrees(self):
        fill = self.row(date="2025-02-05")
        with pytest.raises(ValueError, match="duplicate"):
            self.next_node().run(None, {"records": [self.row()], "fills": [fill, dict(fill)]})
        with pytest.raises(ValueError, match="disagree"):
            self.next_node().run(None, {"records": [self.row()],
                                        "fills": [self.row(date="2025-02-05", strike=105.)]})

    def test_next_counts_attached_and_missing_fills_in_evidence(self):
        rows = [self.row(), self.row(contract="B")]
        out = self.next_node().run(None, {"records": rows, "fills": [self.row(date="2025-02-05")]})
        assert out["evidence"].value["fills_attached"] == 1
        assert out["evidence"].value["fills_missing"] == 1

def test_robust_batch_derived_identity_sell_only_refusal_and_skip_provenance():
    from index_options.nodes import RobustCondorBatchSelect
    params, inputs = _robust_batch_params(), _robust_batch_inputs()
    inputs["projected_masses"][0].pop("decision_id")
    inputs["rho"]={}
    node=RobustCondorBatchSelect("batch",params)
    mass=next(iter(node._mass_rows(inputs["projected_masses"]).values()))
    assert isinstance(mass["decision_id"],str) and len(mass["decision_id"])==64
    chain=[dict(row) for row in inputs["chain"][2:]]
    chain[0]["vwap"]=.001
    context=node._batch_context(mass,chain,"low","nominal",0.)
    assert any(row["contract_id"]==chain[0]["contract"] for row in context["legs"]["LP"])
    assert all(row["contract_id"]!=chain[0]["contract"] for row in context["legs"]["SP"])
    out=node.run(None,inputs)
    for row in out["skips"]+out["decisions"]:
        for key in ("fit_identity","checkpoint_identity","input_identity","source_identity"):
            assert row[key] == mass[key]


def test_robust_batch_tiers_use_even_sample_median_and_require_prior_window():
    from index_options.nodes import RobustCondorBatchSelect
    params=_robust_batch_params()
    node=RobustCondorBatchSelect("batch",params)
    rows=[dict(symbol=symbol,quote_date="2024-06-03",trade_count=count)
          for symbol,counts in (("A",[0,100]),("B",[60,60])) for count in counts]
    assert node._tiers(rows)=={"A":"low","B":"mid"}
    params["calibration_window"]["end"]="2025-02-05"
    assert RobustCondorBatchSelect.validate_params(params)

@pytest.mark.parametrize("arm,spec", [
    ("rho_base", {"radius":"zero"}),
    ("nominal", {"radius":"calibrated"}),
    ("liquidity_3", {"radius":"calibrated"}),
    ("liquidity_3", {"radius":"calibrated","min_trade_count":3,"haircut_multiplier":2}),
    ("liquidity_3", {"radius":"calibrated","min_trade_count":5}),
    ("haircut_half", {"radius":"calibrated","haircut_multiplier":2})])
def test_robust_batch_canonical_arms_cannot_bypass_or_relabel_policy(arm,spec):
    from index_options.nodes import RobustCondorBatchSelect
    params=_robust_batch_params()
    params["arms"][arm]=spec
    assert RobustCondorBatchSelect.validate_params(params)


def test_robust_batch_missing_ticker_calibration_has_no_implicit_tier():
    from index_options.nodes import RobustCondorBatchSelect
    params,inputs=_robust_batch_params(),_robust_batch_inputs()
    inputs["chain"]=[r for r in inputs["chain"]
                     if r["symbol"]!="AAA" or r["quote_date"]>"2024-12-31"]
    out=RobustCondorBatchSelect("batch",params).run(None,inputs)
    assert not out["decisions"] and not out["selections"]
    assert {r["reason"] for r in out["skips"]}=={"missing_calibration_tier"}
    assert {r["arm_id"] for r in out["skips"]}==set(params["arms"])


# -- ADR-0256 (6): relative-gap diagnostic, primal parity, ordering pin, context_error ----

ROBUST_LEGACY_CONFIG = "configs/run-equity-condor-robust-backtest.json"
ROBUST_LEGACY_HASH = "8580a1796a87c6322c5b4a98a3fc81951a295aec3ac02b7d3292ba4051f62d55"


def _parity_params(**overrides):
    return {"solver_options": {"mip_rel_gap": 0, "mip_abs_gap": 1e-7},
            "multiplier": 100, "fee_per_contract_usd": .65, "tie_tolerance_usd": 1e-6,
            "max_absolute_gap_usd": 1e-6, "max_relative_gap": 1e-8,
            "objective_parity_usd": 1e-5, **overrides}


def _without_parity(node):
    return type(node)("select", {k: v for k, v in node.params.items()
                                 if k != "objective_parity_usd"})


def _parity_node(rho=0., **overrides):
    from index_options.nodes import RobustCondorSelect
    _, context = _robust_tie_fixture(2.)
    context["rho"] = rho
    return RobustCondorSelect("select", _parity_params(**overrides)), context


def test_robust_legacy_config_still_validates_with_its_identity(child_root):
    import json
    from dskit.pipeline.document import PipelineDocument
    from index_options.nodes import RobustCondorBatchSelect
    document = json.loads((child_root / ROBUST_LEGACY_CONFIG).read_text())
    assert RobustCondorBatchSelect.validate_params(document["pipeline"]["select"]["params"]) == []
    assert PipelineDocument.from_obj(document).hash == ROBUST_LEGACY_HASH


def _shift_primary(node, monkeypatch, bound_offset=0., objective_offset=0.):
    from dataclasses import replace
    build = node._build_solve_record
    calls = []

    def shifted(*args):
        record = build(*args)
        calls.append(record)
        if len(calls) > 1:
            return record
        objective, bound = record.objective+objective_offset, record.bound+objective_offset+bound_offset
        return replace(record, objective=objective, bound=bound,
                       gap=abs(objective-bound)/max(abs(objective), 1e-10))
    monkeypatch.setattr(node, "_build_solve_record", shifted)


def test_robust_null_relative_gap_validates_and_accepts_tiny_gap(monkeypatch):
    from index_options.nodes import RobustCondorSelect
    assert RobustCondorSelect.validate_params(_parity_params(max_relative_gap=None)) == []
    assert RobustCondorSelect.validate_params({k: v for k, v in _parity_params().items()
                                               if k != "max_relative_gap"})
    assert RobustCondorSelect.validate_params(_parity_params(max_relative_gap=-1.))
    outcomes = {}
    for relative in (0., None):
        node, context = _parity_node(max_relative_gap=relative)
        _shift_primary(node, monkeypatch, bound_offset=5e-8)
        out = node.run(None, {"context": context})
        outcomes[relative] = out["decision"]
        assert out["evidence"]["solves"][0]["gap"] > 0
    assert outcomes[0.]["reason"] == "uncertified_primary"
    assert outcomes[None]["status"] == "trade"


@pytest.mark.parametrize("rho", [0., .03])
def test_robust_parity_passes_on_a_correct_solve(rho):
    grid = [70., 82., 91., 103., 116., 135.]
    from index_options.nodes import RobustCondorSelect
    context = dict(decision_id="fixture", arm_id="base", symbol="SYNTH",
                   quote_date="2025-02-04", expiry="2025-03-07", grid=grid,
                   masses=[.05, .15, .3, .25, .15, .1], spot=100., rho=rho, legs={})
    for role, prices in {"LP": [20, 14, 8, 5, 3, 1], "SP": [21, 15, 9, 6, 4, 2],
                         "SC": [1, 3, 5, 8, 14, 20], "LC": [2, 4, 6, 9, 15, 21]}.items():
        context["legs"][role] = [dict(index=i, price=float(p) + (12. if role in ("SP", "SC") else 0.), haircut=.03,
                                      contract_id=f"{role}-{i}") for i, p in enumerate(prices)]
    out = RobustCondorSelect("select", _parity_params()).run(None, {"context": context})
    assert out["decision"]["status"] == "trade"
    for certificate in out["evidence"]["solves"]:
        parity = certificate["parity"]
        assert parity["passed"] and parity["discrepancy_usd"] <= 1e-5
        assert parity["recomputed_usd"] == pytest.approx(parity["model_usd"], abs=1e-5)
    assert out["decision"]["robust_value_usd"] == pytest.approx(
        out["evidence"]["solves"][1]["parity"]["recomputed_usd"], abs=1e-9)


def test_robust_parity_absent_leaves_certificates_legacy():
    node, context = _robust_tie_fixture(2.)
    out = node.run(None, {"context": context})
    assert all("parity" not in row for row in out["evidence"]["solves"])


def test_robust_corrupted_primary_objective_fails_parity_before_the_tie_solve(monkeypatch):
    node, context = _parity_node()
    _shift_primary(node, monkeypatch, objective_offset=.5)
    out = node.run(None, {"context": context})
    assert out["decision"] == {**{k: context[k] for k in node._IDENTITY},
                               "status": "skipped", "reason": "parity_failed"}
    assert len(out["evidence"]["solves"]) == 1
    parity = out["evidence"]["solves"][0]["parity"]
    assert not parity["passed"] and parity["discrepancy_usd"] == pytest.approx(.5)


def test_robust_corrupted_tie_structure_fails_tie_parity_before_the_floor(monkeypatch):
    node, context = _parity_node()
    primal, calls = node._primal_value, []

    def drifting(legs):
        calls.append(1)
        robust, worst = primal(legs)
        return (robust-.5, worst) if len(calls) == 2 else (robust, worst)

    monkeypatch.setattr(node, "_primal_value", drifting)
    out = node.run(None, {"context": context})
    assert out["decision"]["reason"] == "parity_failed_tie"
    assert len(out["evidence"]["solves"]) == 2
    assert not out["evidence"]["solves"][1]["parity"]["passed"]


@pytest.mark.parametrize("parity", [True, False])
def test_robust_floor_failure_has_its_own_reason_in_parity_mode_and_raises_in_legacy(
        parity, monkeypatch):
    node, context = _parity_node()
    if not parity:
        node = _without_parity(node)
    certified = node._secondary_certified

    def lowered_floor(record):
        node._certificates[0]["bound"] += 1.
        return certified(record)

    monkeypatch.setattr(node, "_secondary_certified", lowered_floor)
    if parity:
        assert node.run(None, {"context": context})["decision"]["reason"] == "tie_floor_failed"
    else:
        with pytest.raises(ValueError, match="tie floor"):
            node.run(None, {"context": context})


@pytest.mark.parametrize("parity", [True, False])
def test_robust_nonintegral_solution_skips_only_in_parity_mode(parity, monkeypatch):
    import pyomo.environ as pe
    node, context = _parity_node()
    if not parity:
        node = _without_parity(node)
    real = pe.value

    def fractional(expr, *args, **kwargs):
        return .4 if getattr(expr, "name", None) == "z" else real(expr, *args, **kwargs)

    monkeypatch.setattr(pe, "value", fractional)
    if parity:
        decision = node.run(None, {"context": context})["decision"]
        assert (decision["status"], decision["reason"]) == ("skipped", "nonintegral_solution")
    else:
        with pytest.raises(ValueError, match="nonintegral"):
            node.run(None, {"context": context})


@pytest.mark.parametrize("change,fragment", [
    ({"solver_options": {"mip_abs_gap": 1e-5}}, "mip_abs_gap <= max_absolute_gap_usd"),
    ({"max_absolute_gap_usd": 1e-4, "tie_tolerance_usd": 1e-4},
     "max_absolute_gap_usd <= objective_parity_usd"),
    ({"tie_tolerance_usd": 1e-8}, "tie_tolerance_usd >= max_absolute_gap_usd"),
    ({"objective_parity_usd": 1e-6}, "objective_parity_usd > tie_tolerance_usd"),
    ({"objective_parity_usd": -1.}, "objective_parity_usd must be"),
    ({"objective_parity_usd": None}, "objective_parity_usd must be"),
])
def test_robust_parity_ordering_pin_names_the_violated_inequality(change, fragment):
    from index_options.nodes import RobustCondorSelect
    problems = RobustCondorSelect.validate_params(_parity_params(**change))
    assert any(fragment in problem for problem in problems), problems
    with pytest.raises(ConfigError):
        RobustCondorSelect("select", _parity_params(**change))


def test_robust_ordering_pin_is_off_without_parity_and_unset_mip_gap_is_unpinned():
    from index_options.nodes import RobustCondorSelect
    legacy = {k: v for k, v in _parity_params().items() if k != "objective_parity_usd"}
    legacy.update(tie_tolerance_usd=50., max_absolute_gap_usd=101., max_relative_gap=1.1)
    assert RobustCondorSelect.validate_params(legacy) == []
    unset = _parity_params(solver_options={"mip_rel_gap": 0})
    assert RobustCondorSelect.validate_params(unset) == []
    node = RobustCondorSelect("select", unset)
    _, context = _parity_node()
    parity = node.run(None, {"context": context})["evidence"]["solves"][0]["parity"]
    assert parity["mip_abs_gap"] == "unset"


def _parity_batch_params(**overrides):
    return {**_robust_batch_params(), **_parity_params(), **overrides}


def test_robust_batch_context_error_is_counted_and_the_batch_completes():
    from index_options.nodes import RobustCondorBatchSelect
    params, inputs = _parity_batch_params(), _robust_batch_inputs()
    good = dict(inputs["projected_masses"][0])
    bad = dict(good, decision_id="bad-masses", expiry="2025-03-14",
               settlement_date="2025-03-14", masses=[.1, .1, .1, .1])
    inputs["projected_masses"] = [good, bad]
    inputs["chain"] = inputs["chain"] + [dict(row, expiry="2025-03-14")
                                         for row in inputs["chain"] if row["quote_date"] == "2025-02-04"]
    out = RobustCondorBatchSelect("batch", params).run(None, inputs)
    errors = [row for row in out["skips"] if row.get("reason") == "context_error"]
    assert {row["decision_id"] for row in errors} == {"bad-masses"}
    assert len(errors) == len(params["arms"]) and all(row["detail"] for row in errors)
    assert {row["decision_id"] for row in out["decisions"]} == {"batch-fixture"}
    assert len(out["decisions"]) == len(params["arms"])


def test_robust_batch_bad_chain_row_is_context_error_only_with_parity():
    from index_options.nodes import RobustCondorBatchSelect
    inputs = _robust_batch_inputs()
    inputs["chain"][2]["vwap"] = float("nan")
    out = RobustCondorBatchSelect("batch", _parity_batch_params()).run(None, inputs)
    assert {r["reason"] for r in out["skips"]} == {"context_error"}
    with pytest.raises(ValueError, match="invalid strike, VWAP"):
        RobustCondorBatchSelect("batch", _robust_batch_params()).run(None, inputs)


def test_robust_batch_integrity_skips_are_never_context_error(monkeypatch):
    import pyomo.environ as pe
    from index_options.nodes import RobustCondorBatchSelect
    real = pe.value

    def fractional(expr, *args, **kwargs):
        return .4 if getattr(expr, "name", None) == "z" else real(expr, *args, **kwargs)

    monkeypatch.setattr(pe, "value", fractional)
    out = RobustCondorBatchSelect("batch", _parity_batch_params()).run(None, _robust_batch_inputs())
    assert {r["reason"] for r in out["skips"]} == {"nonintegral_solution"}


# -- ADR-0256 S3: the decision liquidity/haircut owner, the executable fill clock ----------------

_PIN_COUNTS = {"put-80.0": 6, "put-90.0": 12, "call-110.0": 25, "call-120.0": 4}
_PIN_VWAPS = {"put-80.0": .1, "put-90.0": 3., "call-110.0": 3., "call-120.0": .1}
_PIN_HAIRCUTS = {"low": {"pct": .6, "floor_usd": .05}, "mid": {"pct": .02, "floor_usd": .01},
                 "high": {"pct": .01, "floor_usd": .01}}
# Saved from the inline rule BEFORE the owner was extracted (base 24cba7be): (index, price, haircut, contract)
# per role, for every arm, on the pin inputs at tier "low".
_PIN_LEGACY_LEGS = {
    "nominal": {"LP": [[0, .1, .06, "put-80.0"], [1, 3., 1.7999999999999998, "put-90.0"]],
                "SP": [[0, .1, .06, "put-80.0"], [1, 3., 1.7999999999999998, "put-90.0"]],
                "SC": [[2, 3., 1.7999999999999998, "call-110.0"]],
                "LC": [[2, 3., 1.7999999999999998, "call-110.0"]]},
    "haircut_0": {"LP": [[0, .1, 0., "put-80.0"], [1, 3., 0., "put-90.0"]],
                  "SP": [[0, .1, 0., "put-80.0"], [1, 3., 0., "put-90.0"]],
                  "SC": [[2, 3., 0., "call-110.0"]], "LC": [[2, 3., 0., "call-110.0"]]},
    "haircut_half": {"LP": [[0, .1, .03, "put-80.0"], [1, 3., .8999999999999999, "put-90.0"]],
                     "SP": [[0, .1, .03, "put-80.0"], [1, 3., .8999999999999999, "put-90.0"]],
                     "SC": [[2, 3., .8999999999999999, "call-110.0"]],
                     "LC": [[2, 3., .8999999999999999, "call-110.0"]]},
    "haircut_double": {"LP": [[0, .1, .12, "put-80.0"], [1, 3., 3.5999999999999996, "put-90.0"]],
                       "SP": [], "SC": [],
                       "LC": [[2, 3., 3.5999999999999996, "call-110.0"]]},
    "liquidity_3": {"LP": [[0, .1, .06, "put-80.0"], [1, 3., 1.7999999999999998, "put-90.0"]],
                    "SP": [[0, .1, .06, "put-80.0"], [1, 3., 1.7999999999999998, "put-90.0"]],
                    "SC": [[2, 3., 1.7999999999999998, "call-110.0"], [3, .1, .06, "call-120.0"]],
                    "LC": [[2, 3., 1.7999999999999998, "call-110.0"], [3, .1, .06, "call-120.0"]]},
    "liquidity_10": {"LP": [[1, 3., 1.7999999999999998, "put-90.0"]],
                     "SP": [[1, 3., 1.7999999999999998, "put-90.0"]],
                     "SC": [[2, 3., 1.7999999999999998, "call-110.0"]],
                     "LC": [[2, 3., 1.7999999999999998, "call-110.0"]]},
    "liquidity_20": {"LP": [], "SP": [], "SC": [[2, 3., 1.7999999999999998, "call-110.0"]],
                     "LC": [[2, 3., 1.7999999999999998, "call-110.0"]]},
}
_PIN_LEGACY_DIGEST = "67bd357c47e1c8354577730b87ad3ba195c37cf585b0cb577924771da8e1b86b"


def _pin_setup():
    params, inputs = _robust_batch_params(), _robust_batch_inputs()
    params["haircut_tiers"] = {k: dict(v) for k, v in _PIN_HAIRCUTS.items()}
    for row in inputs["chain"]:
        if row["contract"] in _PIN_COUNTS:
            row["trade_count"], row["vwap"] = _PIN_COUNTS[row["contract"]], _PIN_VWAPS[row["contract"]]
    return params, inputs


def test_condor_batch_context_decisions_are_identical_after_the_owner_extraction():
    import hashlib
    import json

    from index_options.nodes import RobustCondorBatchSelect
    params, inputs = _pin_setup()
    node = RobustCondorBatchSelect("batch", params)
    mass = inputs["projected_masses"][0]
    for arm, want in _PIN_LEGACY_LEGS.items():
        legs = node._batch_context(mass, inputs["chain"][2:], "low", arm, 0.)["legs"]
        got = {role: [[r["index"], r["price"], r["haircut"], r["contract_id"]] for r in rows]
               for role, rows in legs.items()}
        assert got == want, arm
    # rho_base shares the nominal rule's legs
    assert node._batch_context(mass, inputs["chain"][2:], "low", "rho_base", 0.)["legs"] == \
        node._batch_context(mass, inputs["chain"][2:], "low", "nominal", 0.)["legs"]
    out = node.run(None, inputs)
    blob = json.dumps({k: out[k] for k in ("selections", "decisions", "skips")},
                      sort_keys=True, default=str)
    assert hashlib.sha256(blob.encode()).hexdigest() == _PIN_LEGACY_DIGEST
    assert "fill" not in out["evidence"]
    assert all("fill_date" not in row and "unfilled_reason" not in row
               for row in out["selections"] + out["decisions"])


def test_condor_liquidity_haircut_owner_is_one_module_level_rule():
    from index_options import nodes
    rule = nodes.liquid_leg_haircut
    terms, liquidity = {"pct": .01, "floor_usd": .01}, {"min_trade_count": 5, "min_volume": 1}
    assert "liquid_leg_haircut" in nodes.__all__
    # below either threshold: not eligible at all
    assert rule(2., 4, 1, liquidity, terms, 1.) is None
    assert rule(2., 5, 0, liquidity, terms, 1.) is None
    # thresholds are inclusive; haircut = multiplier * max(floor, pct * price)
    assert rule(2., 5, 1, liquidity, terms, 1.) == (.02, True)
    assert rule(2., 5, 1, liquidity, terms, 2.) == (.04, True)
    # the haircut <= price boundary decides whether a short is allowed
    assert rule(.01, 5, 1, liquidity, terms, 1.) == (.01, True)
    assert rule(.01, 5, 1, liquidity, terms, 2.) == (.02, False)
    assert rule(.0, 5, 1, liquidity, terms, 0.) == (0., True)


_FILL_CONTRACTS = ("put-80.0", "put-90.0", "call-110.0", "call-120.0")


def _fill_setup(**fill_over):
    """Pin inputs where every arm trades and every entry row carries a t' fill bar."""
    params, inputs = _robust_batch_params(), _robust_batch_inputs()
    for row in inputs["chain"]:
        row["fill_session"], row["fill"] = None, None
        if row["quote_date"] == "2025-02-04":
            row["trade_count"] = 25
            row["fill_session"] = "2025-02-05"
            row["fill"] = {"date": "2025-02-05", "vwap": row["vwap"], "trade_count": 25,
                           "volume": 5, **fill_over.get(row["contract"], {})}
    return params, inputs


def _by_arm(out):
    return {row["arm_id"]: row for row in out["selections"]}


def test_condor_batch_reprices_a_filled_selection_leg_by_leg_with_the_arms_haircut():
    from index_options.nodes import RobustCondorBatchSelect
    moved = {"put-80.0": {"vwap": .2}, "put-90.0": {"vwap": 2.5},
             "call-110.0": {"vwap": 2.5}, "call-120.0": {"vwap": .2}}
    params, inputs = _fill_setup(**moved)
    out = RobustCondorBatchSelect("batch", params).run(None, inputs)
    row = _by_arm(out)["nominal"]
    assert row["status"] == "trade" and row["fill_date"] == "2025-02-05"
    assert row["remaining_dte"] == 30 and row["quote_date"] == "2025-02-04"
    assert [leg["role"] for leg in row["legs"]] == ["LP", "SP", "SC", "LC"]
    assert [leg["price_usd_per_share"] for leg in row["legs"]] == pytest.approx(
        [.21, 2.475, 2.475, .21])
    assert [leg["decision_price_usd_per_share"] for leg in row["legs"]] == pytest.approx(
        [.11, 2.97, 2.97, .11])
    assert row["decision_credit_usd"] == pytest.approx(572.)
    assert row["fill_credit_usd"] == pytest.approx(453.)
    assert row["slippage_usd"] == pytest.approx(119.)
    # the decision list holds the very same record; the ex-ante value is untouched
    assert any(d is row for d in out["decisions"])
    assert out["evidence"]["fill"] == {"filled": len(out["selections"]), "unfilled": {},
                                       "fill_credit_nonpositive": 0, "fill_credit_ge_width": 0}


def test_condor_batch_without_a_t_prime_bar_is_unfilled_never_replaced():
    from index_options.nodes import RobustCondorBatchSelect
    params, inputs = _fill_setup()
    for row in inputs["chain"]:
        if row["contract"] == "put-90.0":
            row["fill"] = None
    out = RobustCondorBatchSelect("batch", params).run(None, inputs)
    arms = _by_arm(out)
    assert {r["status"] for r in arms.values()} == {"unfilled"}
    nominal = arms["nominal"]
    assert nominal["unfilled_reason"] == "missing_fill_bar"
    assert "fill_date" not in nominal and "fill_credit_usd" not in nominal
    assert [leg["price_usd_per_share"] for leg in nominal["legs"]] == pytest.approx(
        [.11, 2.97, 2.97, .11])
    assert out["evidence"]["fill"] == {"filled": 0, "unfilled": {
        "missing_fill_bar": len(out["selections"])},
        "fill_credit_nonpositive": 0, "fill_credit_ge_width": 0}
    # one selection per arm and decision: nothing took the place of an unfilled one
    assert len(out["selections"]) == len({(r["arm_id"], r["decision_id"])
                                          for r in out["selections"]})


def test_condor_batch_refuses_a_fill_bar_off_the_rows_fill_session():
    from index_options.nodes import RobustCondorBatchSelect
    params, inputs = _fill_setup(**{"call-120.0": {"date": "2025-02-06"}})
    with pytest.raises(ValueError, match="fill_session"):
        RobustCondorBatchSelect("batch", params).run(None, inputs)


def test_condor_batch_a_context_with_no_session_after_t_is_unfilled():
    from index_options.nodes import RobustCondorBatchSelect
    params, inputs = _fill_setup()
    for row in inputs["chain"]:
        if row["quote_date"] == "2025-02-04":
            row["fill_session"], row["fill"] = None, None
    out = RobustCondorBatchSelect("batch", params).run(None, inputs)
    assert {r["unfilled_reason"] for r in out["selections"]} == {"missing_fill_bar"}


def test_condor_batch_one_context_carries_one_fill_session():
    from index_options.nodes import RobustCondorBatchSelect
    params, inputs = _fill_setup()
    inputs["chain"][3]["fill_session"] = "2025-02-06"
    inputs["chain"][3]["fill"]["date"] = "2025-02-06"
    with pytest.raises(ValueError, match="one fill_session"):
        RobustCondorBatchSelect("batch", params).run(None, inputs)


def test_condor_batch_counts_nonpositive_and_width_reaching_fill_credits_without_refusing():
    from index_options.nodes import RobustCondorBatchSelect
    params, inputs = _fill_setup(**{"put-80.0": {"vwap": 3.}, "put-90.0": {"vwap": .5},
                                    "call-110.0": {"vwap": .5}, "call-120.0": {"vwap": 3.}})
    out = RobustCondorBatchSelect("batch", params).run(None, inputs)
    nominal = _by_arm(out)["nominal"]
    assert nominal["status"] == "trade" and nominal["fill_credit_usd"] < 0
    assert out["evidence"]["fill"]["fill_credit_nonpositive"] == len(out["selections"])
    assert out["evidence"]["fill"]["fill_credit_ge_width"] == 0
    params, inputs = _fill_setup(**{"put-80.0": {"vwap": .02}, "put-90.0": {"vwap": 12.},
                                    "call-110.0": {"vwap": 12.}, "call-120.0": {"vwap": .02}})
    out = RobustCondorBatchSelect("batch", params).run(None, inputs)
    assert _by_arm(out)["nominal"]["fill_credit_usd"] >= 1000.
    assert out["evidence"]["fill"]["fill_credit_ge_width"] == len(out["selections"])


def test_condor_batch_arm_thresholds_and_multiplier_apply_at_t_prime():
    from index_options.nodes import RobustCondorBatchSelect

    def arms(**over):
        params, inputs = _fill_setup(**over)
        return _by_arm(RobustCondorBatchSelect("batch", params).run(None, inputs))

    # trade_count 15 at t': the base rule (5) passes, liquidity_20 does not
    got = arms(**{c: {"trade_count": 15} for c in _FILL_CONTRACTS})
    assert got["nominal"]["status"] == "trade"
    assert got["liquidity_10"]["status"] == "trade"
    assert got["liquidity_20"]["status"] == "unfilled"
    assert got["liquidity_20"]["unfilled_reason"] == "fill_liquidity"
    # exactly at the arm's threshold it fills
    got = arms(**{c: {"trade_count": 20} for c in _FILL_CONTRACTS})
    assert got["liquidity_20"]["status"] == "trade"
    # the volume threshold is the base one
    assert arms(**{"put-80.0": {"volume": 0}})["nominal"]["unfilled_reason"] == "fill_liquidity"
    # short vwap .015: haircut x1 = .01 <= .015 (fills); haircut_double .02 > .015 (negative net short)
    got = arms(**{"put-90.0": {"vwap": .015}, "call-110.0": {"vwap": .015}})
    assert got["nominal"]["status"] == got["haircut_half"]["status"] == "trade"
    assert got["haircut_0"]["status"] == "trade"
    assert got["haircut_double"]["status"] == "unfilled"
    assert got["haircut_double"]["unfilled_reason"] == "negative_net_short_price"


def test_condor_batch_fill_must_be_on_every_chain_row_or_none():
    from index_options.nodes import RobustCondorBatchSelect
    params, inputs = _fill_setup()
    del inputs["chain"][3]["fill"]
    with pytest.raises(ValueError, match="fill"):
        RobustCondorBatchSelect("batch", params).run(None, inputs)


def test_condor_batch_fill_bar_must_follow_the_decision_date():
    from index_options.nodes import RobustCondorBatchSelect
    params, inputs = _fill_setup(**{"put-90.0": {"date": "2025-02-04"}})
    with pytest.raises(ValueError, match="fill"):
        RobustCondorBatchSelect("batch", params).run(None, inputs)


def test_robust_parity_validation_accumulates_on_a_malformed_solver_options_block():
    from index_options.nodes import RobustCondorSelect
    for bad in (["x"], "abc", 5):
        problems = RobustCondorSelect.validate_params(_parity_params(solver_options=bad))
        assert problems and all(isinstance(p, str) for p in problems)


def test_robust_parity_fails_closed_on_nan_and_counts_a_primal_failure(monkeypatch):
    import math
    from index_options.nodes import RobustCondorSelect
    node, context = _parity_node()
    monkeypatch.setattr(RobustCondorSelect, "_primal_value", lambda self, legs: (math.nan, math.nan))
    out = node.run(None, {"context": context})
    assert (out["decision"]["status"], out["decision"]["reason"]) == ("skipped", "parity_failed")
    node, context = _parity_node()

    def broken(self, legs):
        raise ValueError("W1 projection failed")
    monkeypatch.setattr(RobustCondorSelect, "_primal_value", broken)
    out = node.run(None, {"context": context})
    assert (out["decision"]["status"], out["decision"]["reason"]) == ("skipped", "primal_failed")
    assert out["evidence"]["solves"][0]["parity"] == {"error": "W1 projection failed", "passed": False}


def test_robust_parity_mode_batch_still_raises_a_non_context_value_error(monkeypatch):
    from dskit.pipeline.libs.pyomo import PyomoSolve
    from index_options.nodes import RobustCondorBatchSelect

    def unavailable(self, solver, model):
        raise ValueError("solver unavailable")
    monkeypatch.setattr(PyomoSolve, "_solve", unavailable)
    with pytest.raises(ValueError, match="solver unavailable"):
        RobustCondorBatchSelect("batch", _parity_batch_params()).run(None, _robust_batch_inputs())


@pytest.mark.parametrize("change", [
    {"max_absolute_gap_usd": 1e-5, "tie_tolerance_usd": 1e-5, "objective_parity_usd": 2e-5},
    {"solver_options": {"mip_rel_gap": 0, "mip_abs_gap": 1e-6}},
])
def test_robust_parity_ordering_pin_accepts_equality(change):
    from index_options.nodes import RobustCondorSelect
    assert RobustCondorSelect.validate_params(_parity_params(**change)) == []


@pytest.mark.parametrize("shift,reason", [(5e-6, None), (2e-5, "parity_failed")])
def test_robust_parity_tolerance_is_the_configured_value(monkeypatch, shift, reason):
    from index_options.nodes import RobustCondorSelect
    node, context = _parity_node()
    original = RobustCondorSelect._primal_value
    monkeypatch.setattr(RobustCondorSelect, "_primal_value",
                        lambda self, legs: tuple(v + shift for v in original(self, legs)))
    out = node.run(None, {"context": context})
    assert out["decision"].get("reason") == reason
    assert out["evidence"]["solves"][0]["parity"]["passed"] is (reason is None)


def test_robust_parity_recompute_goes_through_the_primal_owner(monkeypatch):
    from dskit.pipeline.libs.predictive_cdf import DiscreteCDFGrid
    from index_options.nodes import RobustCondorSelect
    original = DiscreteCDFGrid.worst_expected_loss
    monkeypatch.setattr(DiscreteCDFGrid, "worst_expected_loss",
                        lambda self, *a, **k: original(self, *a, **k) + .5)
    node, context = _parity_node()
    out = node.run(None, {"context": context})
    assert (out["decision"]["status"], out["decision"]["reason"]) == ("skipped", "parity_failed")
    node, context = _parity_node()
    monkeypatch.setattr(RobustCondorSelect, "_primal_value",
                        lambda self, legs: (float("nan"), float("nan")))
    assert node.run(None, {"context": context})["decision"]["reason"] == "parity_failed"


def test_condor_batch_fill_uses_the_tickers_own_haircut_tier():
    from index_options.nodes import RobustCondorBatchSelect
    params, inputs = _fill_setup()
    params["haircut_tiers"] = {"low": {"pct": .5, "floor_usd": .01},
                               "mid": {"pct": .01, "floor_usd": .01},
                               "high": {"pct": .01, "floor_usd": .01}}
    # AAA's calibration median (7) is the lowest of two symbols: tier low, pct .5
    out = RobustCondorBatchSelect("batch", params).run(None, inputs)
    nominal = _by_arm(out)["nominal"]
    assert nominal["status"] == "trade"
    for leg in nominal["legs"]:
        decision, fill = leg["decision_price_usd_per_share"], leg["price_usd_per_share"]
        bar = next(r for r in inputs["chain"] if r["contract"] == leg["contract"]
                   and r["quote_date"] == "2025-02-04")["fill"]
        expected = bar["vwap"] * (.5 if leg["side"] == "sell" else 1.5)
        assert fill == pytest.approx(expected), (leg["role"], decision, fill)


def test_fill_bar_keys_have_one_owner():
    import inspect
    from index_options import nodes
    assert nodes.FILL_BAR_KEYS == ("date", "vwap", "trade_count", "volume")
    source = inspect.getsource(nodes)
    assert source.count('("date", "vwap", "trade_count", "volume")') == 1
