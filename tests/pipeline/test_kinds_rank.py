"""TrailingRank: rank entities by an aggregate over their trailing sessions."""

import json

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.kinds_rank import TrailingRank
from dskit.pipeline.node import NodeContext

# sessions d1..d4, three entities; "old" only traded on d1 (outside window 2)
ROWS = [
    {"sym": "A", "day": "d1", "vol": 100.0}, {"sym": "A", "day": "d3", "vol": 1.0},
    {"sym": "A", "day": "d4", "vol": 1.0},
    {"sym": "B", "day": "d2", "vol": 50.0}, {"sym": "B", "day": "d3", "vol": 5.0},
    {"sym": "B", "day": "d4", "vol": 5.0},
    {"sym": "C", "day": "d3", "vol": 5.0}, {"sym": "C", "day": "d4", "vol": 5.0},
    {"sym": "OLD", "day": "d1", "vol": 999.0},
]


def _params(**over):
    base = {"entity_field": "sym", "window_field": "day", "value_field": "vol",
            "window": 2, "top_n": 10}
    base.update(over)
    return base


def _run(rows=ROWS, **over):
    ctx = NodeContext(name="t", asof="2026-10-03", run_dir="/nonexistent")
    return TrailingRank("r", _params(**over)).run(ctx, {"records": rows})


def test_window_is_the_last_n_distinct_values_and_rank_is_descending():
    out = _run()["records"]
    assert [(r["rank"], r["sym"], r["vol_sum"]) for r in out] == [
        (1, "B", 10.0), (2, "C", 10.0), (3, "A", 2.0)]


def test_ties_break_by_entity_ascending():
    assert [r["sym"] for r in _run()["records"][:2]] == ["B", "C"]


def test_entity_absent_from_the_window_is_not_a_candidate():
    assert "OLD" not in [r["sym"] for r in _run()["records"]]
    assert _run()["metrics"]["candidates"] == 3


def test_top_n_truncates_and_exclude_removes_before_ranking():
    out = _run(top_n=1, exclude=["B"])["records"]
    assert [r["sym"] for r in out] == [("C")]
    assert _run(top_n=1, exclude=["B"])["metrics"]["excluded"] == 1


def test_exclude_file(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"tickers": ["B", "C"]}))
    out = _run(exclude_file=str(path), exclude_key="tickers")["records"]
    assert [r["sym"] for r in out] == ["A"]


@pytest.mark.parametrize("aggregate,expect", [("mean", 5.0), ("max", 5.0), ("count", 2)])
def test_aggregates(aggregate, expect):
    out = _run(aggregate=aggregate)["records"]
    row = next(r for r in out if r["sym"] == "B")
    assert row["vol_" + aggregate] == expect


def test_min_sessions_drops_thin_entities():
    out = _run(rows=ROWS + [{"sym": "Z", "day": "d4", "vol": 1e6}], min_sessions=2)
    assert "Z" not in [r["sym"] for r in out["records"]]


def test_table_output_carries_ordered_entities_and_rows():
    table = _run(entities_key="tickers", rows_key="ranking")["table"]
    assert table["tickers"] == ["B", "C", "A"]
    assert table["ranking"][0]["rank"] == 1


def test_window_longer_than_history_refuses():
    with pytest.raises(ValueError, match="only 4 distinct"):
        _run(window=5)


def test_fewer_candidates_than_top_n_refuses_when_demanded():
    with pytest.raises(ValueError, match="top_n"):
        _run(top_n=10, require_top_n=True)


def test_non_numeric_value_refuses_by_name():
    with pytest.raises(ValueError, match="row 0"):
        _run(rows=[{"sym": "A", "day": "d1", "vol": "x"}], window=1)


def test_default_deny_params():
    problems = TrailingRank.validate_params(_params(typo=1))
    assert any("typo" in p for p in problems)
    for missing in ("entity_field", "window_field", "value_field", "window", "top_n"):
        params = _params()
        del params[missing]
        assert TrailingRank.validate_params(params), missing
    assert TrailingRank.validate_params(_params(aggregate="median"))
    assert TrailingRank.validate_params(_params(exclude_file="f"))  # key required
    with pytest.raises(ConfigError):
        TrailingRank("r", _params(window=0))


def test_row_missing_a_field_is_a_value_error():
    with pytest.raises(ValueError, match="row 1"):
        _run(rows=[{"sym": "A", "day": "d1", "vol": 1.0}, {"sym": "A", "vol": 1.0}], window=1)


def test_equal_output_keys_and_oversized_min_sessions_refuse():
    assert TrailingRank.validate_params(_params(entities_key="x", rows_key="x"))
    assert TrailingRank.validate_params(_params(min_sessions=3))
