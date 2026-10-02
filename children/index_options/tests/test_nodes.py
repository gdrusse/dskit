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
    "matched_dte_vrp", "columns"}
PANEL_READ_REQUIRED = PANEL_READ_KEYS - {
    "chain_features", "raw_chain", "market_symbols", "fred_market_symbols", "surface_features",
    "ohlc_windows", "matched_dte_vrp"}


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
    assert ExactExpiryPanelRead.outputs == ("records", "provenance")
    assert ExactExpiryPanelRead.serving_effect({}, {}) == "forbidden"
    assert "ExactExpiryPanelRead" in __import__("index_options.nodes", fromlist=["x"]).__all__


def test_the_panel_readers_params_are_the_tail_data_read_keys_plus_columns(child_root):
    data = json.loads((child_root / "configs/run-predictive-cdf-tail-data.json").read_text())["data"]
    assert set(ExactExpiryPanelRead._PARAMS) == PANEL_READ_KEYS == (set(data) - {"archive_root"}
                                                                    | {"columns"})
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
