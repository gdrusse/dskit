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
