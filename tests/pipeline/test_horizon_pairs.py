"""HorizonPairs (ADR-0228): each dated record paired with the one horizon_days later."""

import math

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.kinds_table import HorizonPairs
from dskit.pipeline.node import DEFAULT_NODE_KINDS, NodeContext

FIELDS = {"symbol": "sym", "date": "dt", "settlement_date": "sd", "entry_close": "ec",
          "settle_close": "sc", "terminal_return": "tr", "period": "yr"}


def _params(**over):
    base = {"horizon_days": 3, "fallback": "previous", "symbol": "X",
            "date_field": "d", "close_field": "c", "fields": dict(FIELDS)}
    base.update(over)
    return base


def _ctx():
    return NodeContext(name="t", asof="2024-02-01", run_dir="/nonexistent")


def _run(rows, **over):
    return HorizonPairs("h", _params(**over)).run(_ctx(), {"records": rows})["records"]


# 2024-01-01 is a Monday; 05 Fri, 06-07 weekend, 08 Mon, 09 Tue.
BARS = [{"d": d, "c": c} for d, c in [
    ("2024-01-01", 100.0), ("2024-01-02", 101.0), ("2024-01-03", 102.0),
    ("2024-01-04", 103.0), ("2024-01-05", 104.0), ("2024-01-08", 105.0),
    ("2024-01-09", 106.0)]]


def test_registered_kind():
    from dskit.pipeline.kinds_table import register
    register()
    assert DEFAULT_NODE_KINDS.get("horizon-pairs")[0] is HorizonPairs


def test_exact_target_pairs_and_fields_come_from_params():
    out = _run(BARS)
    first = out[0]
    assert first == {"sym": "X", "dt": "2024-01-01", "sd": "2024-01-04", "ec": 100.0,
                     "sc": 103.0, "tr": math.log(103.0 / 100.0), "yr": 2024}


def test_weekend_target_falls_back_to_previous_close():
    row = next(r for r in _run(BARS) if r["dt"] == "2024-01-04")  # target Sun 07 -> Fri 05
    assert row["sd"] == "2024-01-05"


def test_none_drops_entries_whose_target_has_no_close():
    dts = [r["dt"] for r in _run(BARS, fallback="none")]
    assert "2024-01-04" not in dts and "2024-01-01" in dts


def test_tail_beyond_last_close_is_dropped_not_nulled():
    dts = [r["dt"] for r in _run(BARS)]
    assert dts[-1] == "2024-01-05" and "2024-01-08" not in dts


def test_zero_span_settlement_is_dropped():
    rows = [{"d": "2024-01-05", "c": 1.0}, {"d": "2024-01-09", "c": 2.0}]
    # target of the 5th is the 6th: previous close is the 5th itself -> dropped
    assert _run(rows, horizon_days=1) == []


def test_duplicate_dates_and_bad_closes_refuse():
    with pytest.raises(ValueError, match="2024-01-01"):
        _run([BARS[0], dict(BARS[0])])
    for bad in (0.0, -1.0, "9", None, float("nan"), True):
        with pytest.raises(ValueError, match="positive finite"):
            _run([{"d": "2024-01-01", "c": bad}])


def test_unsorted_input_is_paired_by_date():
    assert _run(list(reversed(BARS))) == _run(BARS)


def test_validate_params_default_deny():
    for bad in (_params(typo=1), _params(horizon_days=0), _params(horizon_days=True),
                _params(fallback="next"), _params(symbol=""),
                _params(fields={k: v for k, v in FIELDS.items() if k != "period"}),
                _params(fields={**FIELDS, "extra": "z"}),
                _params(fields={**FIELDS, "terminal_return": FIELDS["date"]})):
        assert HorizonPairs.validate_params(bad)
    assert HorizonPairs.validate_params(_params()) == []
    with pytest.raises(ConfigError):
        HorizonPairs("h", _params(typo=1))


def test_node_refs_pass_validation():
    assert HorizonPairs.validate_params(_params(symbol="$each", horizon_days="$a.b")) == []


# -- symbol_field: one series per value of an input field (ADR-0259) ----------

MULTI = [{"s": s, "d": d, "c": c} for s, d, c in [
    ("B", "2024-01-01", 10.0), ("A", "2024-01-01", 100.0), ("A", "2024-01-04", 103.0),
    ("B", "2024-01-04", 20.0), ("A", "2024-01-05", 104.0), ("B", "2024-01-05", 30.0),
    ("A", "2024-01-09", 110.0)]]


def _multi(rows=MULTI, **over):
    params = _params(symbol_field="s", **over)
    del params["symbol"]
    return HorizonPairs("h", params).run(_ctx(), {"records": rows})["records"]


def test_symbol_field_pairs_each_series_within_itself_in_series_then_date_order():
    out = _multi()
    # A's 04 entry targets Sun 07 and settles on its previous close, Fri 05; B's 04 entry
    # targets a day past B's OWN last close and is dropped; A's 05 entry would settle on
    # itself (no close on 08) and is dropped.
    assert [(r["sym"], r["dt"], r["sd"], r["ec"], r["sc"]) for r in out] == [
        ("A", "2024-01-01", "2024-01-04", 100.0, 103.0),
        ("A", "2024-01-04", "2024-01-05", 103.0, 104.0),
        ("B", "2024-01-01", "2024-01-04", 10.0, 20.0)]
    assert out[0]["tr"] == pytest.approx(math.log(1.03)) and out[2]["tr"] == pytest.approx(math.log(2.0))


def test_symbol_field_refuses_a_missing_or_blank_series_and_a_duplicate_date_within_one():
    for bad in ({"d": "2024-01-01", "c": 1.0}, {"s": "", "d": "2024-01-01", "c": 1.0},
                {"s": 7, "d": "2024-01-01", "c": 1.0}):
        with pytest.raises(ValueError, match="'s' must be a non-empty string"):
            _multi([bad])
    with pytest.raises(ValueError, match="2024-01-01"):
        _multi([MULTI[1], dict(MULTI[1]), MULTI[2]])
    assert len(_multi()) == 3  # two series may share a date


def test_exactly_one_of_symbol_and_symbol_field():
    assert HorizonPairs.validate_params(_params(symbol_field="s"))  # both
    neither = _params()
    del neither["symbol"]
    assert HorizonPairs.validate_params(neither)
    only = _params(symbol_field="s")
    del only["symbol"]
    assert HorizonPairs.validate_params(only) == []
    assert HorizonPairs.validate_params({**only, "symbol_field": ""})
    assert HorizonPairs.validate_params({**only, "symbol_field": "$each"}) == []
