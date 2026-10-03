"""StockDailyBars: the chart-JSON -> archive-style parquet reshape (the httpblobs transform)."""

import io
import json

import pytest

pq = pytest.importorskip("pyarrow.parquet")

from index_options.stock_bars import COLUMNS, StockDailyBars  # noqa: E402

AS_OF = "2026-10-03T12:00:00+00:00"
# 2026-01-05 / 06 / 07 / 08 at 14:30 UTC (09:30 New York); a 2:1 split on the 07th.
STAMPS = [1767623400, 1767709800, 1767796200, 1767882600]
SPLIT_DAY = 1767796200


def chart(**over):
    result = {
        "meta": {"symbol": "ABC", "exchangeTimezoneName": "America/New_York"},
        "timestamp": STAMPS,
        "events": {"splits": {str(SPLIT_DAY): {"date": SPLIT_DAY, "numerator": 2.0, "denominator": 1.0}},
                   "dividends": {str(STAMPS[1]): {"date": STAMPS[1], "amount": 0.5}}},
        "indicators": {
            "quote": [{"open": [5.0, 5.5, 6.0, 6.5], "high": [6, 6, 7, 7], "low": [4, 5, 5, 6],
                       "close": [5.5, 5.75, 6.5, 6.75], "volume": [200, 400, 300, 100]}],
            "adjclose": [{"adjclose": [2.7, 2.8, 3.2, 3.3]}],
        },
    }
    result.update(over)
    return json.dumps({"chart": {"result": [result], "error": None}}).encode()


def frame(body, **params):
    data = StockDailyBars(params, AS_OF).transform("ABC", body)
    return pq.read_table(io.BytesIO(data)).to_pandas()


def test_schema_dates_and_declared_created_at():
    df = frame(chart())
    assert list(df.columns) == list(COLUMNS)
    assert list(df.date) == ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    assert set(df.created_at) == {"2026-10-03 12:00:00"} and list(df.id) == [1, 2, 3, 4]


def test_prices_and_volume_are_stored_as_given_on_one_split_adjusted_basis():
    df = frame(chart())
    assert list(df.close) == [5.5, 5.75, 6.5, 6.75]
    assert list(df.volume) == [200, 400, 300, 100]         # Yahoo's volume, never rescaled
    assert list(df.split_coefficient) == [1.0, 1.0, 2.0, 1.0]
    assert list(df.dividend_amount) == [0.0, 0.5, 0.0, 0.0]
    assert list(df.adjusted_close) == [2.7, 2.8, 3.2, 3.3]


def test_a_split_day_shows_no_close_jump():
    import math
    df = frame(chart(indicators={"quote": [{"open": [5, 5, 5, 5], "high": [6, 6, 6, 6], "low": [4, 4, 4, 4],
                                            "close": [5.0, 5.1, 5.2, 5.3], "volume": [1, 1, 1, 1]}],
                                 "adjclose": [{"adjclose": [1, 2, 3, 4]}]}))
    jumps = [abs(math.log(b / a)) for a, b in zip(df.close, df.close[1:])]
    assert max(jumps) < 0.05  # a raw rebuild would jump by log(2) on the split day


def test_aapl_2020_split_real_numbers_stay_continuous():
    """The real AAPL chart numbers around the 4:1 split of 2020-08-31 (Yahoo is
    already split-adjusted, so volume and close are continuous across it)."""
    import math
    days = [("2020-08-27", 1598535000, 125.01000213623047, 155552400),
            ("2020-08-28", 1598621400, 124.80750274658203, 187630000),
            ("2020-08-31", 1598880600, 129.0399932861328, 225702700)]
    quote = {"open": [d[2] for d in days], "high": [d[2] for d in days], "low": [d[2] for d in days],
             "close": [d[2] for d in days], "volume": [d[3] for d in days]}
    split = {"splits": {"1598880600": {"date": 1598880600, "numerator": 4.0, "denominator": 1.0}}}
    df = frame(chart(timestamp=[d[1] for d in days], events=split,
                     indicators={"quote": [quote], "adjclose": [{"adjclose": [1.0, 1.0, 1.0]}]})).set_index("date")
    assert list(df.index) == [d[0] for d in days]
    assert list(df.volume) == [155552400, 187630000, 225702700]
    assert abs(math.log(df.close["2020-08-31"] / df.close["2020-08-28"])) < 0.05
    assert max(df.volume) / min(df.volume) < 2  # a x4 rescale would break this
    assert df.split_coefficient["2020-08-31"] == 4.0


def test_null_rows_are_dropped_and_ids_stay_dense():
    q = json.loads(chart())["chart"]["result"][0]["indicators"]["quote"][0]
    q["close"][1] = None
    body = chart(indicators={"quote": [q], "adjclose": [{"adjclose": [1, 2, 3, 4]}]})
    df = frame(body)
    assert list(df.date) == ["2026-01-05", "2026-01-07", "2026-01-08"] and list(df.id) == [1, 2, 3]


def test_transform_is_deterministic():
    assert StockDailyBars({}, AS_OF).transform("ABC", chart()) == StockDailyBars({}, AS_OF).transform("ABC", chart())


def test_refusals():
    with pytest.raises(ValueError, match="unknown params"):
        StockDailyBars({"nope": 1}, AS_OF)
    with pytest.raises(ValueError, match="price_decimals"):
        StockDailyBars({"price_decimals": -1}, AS_OF)
    with pytest.raises(ValueError, match="no result"):
        StockDailyBars({}, AS_OF).transform("ABC", b'{"chart": {"result": null, "error": {"code": "x"}}}')
    bad = chart(timestamp=STAMPS[:2])
    with pytest.raises(ValueError, match="aligned"):
        StockDailyBars({}, AS_OF).transform("ABC", bad)


def test_rounding_params_apply():
    q = json.loads(chart())["chart"]["result"][0]["indicators"]["quote"][0]
    q["close"][0] = 5.123456
    body = chart(indicators={"quote": [q], "adjclose": [{"adjclose": [1, 2, 3, 4]}]})
    assert frame(body, price_decimals=1).close[0] == round(5.123456, 1)


def test_note_lists_splits_and_flags_irregular_ratios():
    t = StockDailyBars({}, AS_OF)
    assert t.note("ABC", chart()) == "splits 2026-01-07:2"
    odd = {"splits": {str(SPLIT_DAY): {"date": SPLIT_DAY, "numerator": 1.319, "denominator": 1.0}}}
    assert "IRREGULAR" in t.note("ABC", chart(events=odd))
    reverse = {"splits": {str(SPLIT_DAY): {"date": SPLIT_DAY, "numerator": 1.0, "denominator": 8.0}}}
    assert "IRREGULAR" not in t.note("ABC", chart(events=reverse))
    assert t.note("ABC", chart(events={})) is None
