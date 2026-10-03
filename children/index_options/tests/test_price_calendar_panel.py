"""``PriceCalendarCDFPanel`` (ADR-0230): a panel built from a daily price file alone.

Prices come from a real ``localblobs`` store read through ``ParquetRows`` (the blob_store
fixture); only the volatility-index reader is stubbed. Expected values are restated here from the
exchange calendar and the price table, never read back from the reader under test.
"""

import math

import exchange_calendars as xc
import pandas as pd
import pytest

from dskit.pipeline.libs.observation_tables import ObservationTables
from dskit.pipeline.libs.parquet import ParquetRows
from index_options import cdf_study
from index_options.cdf_study import ExactExpiryCDFPanel, PriceCalendarCDFPanel, panel_class
from index_options.nodes import ExactExpiryPanelRead
from index_options.observations import IndexCloseRows

HORIZON = 7
SESSIONS = xc.get_calendar("XNYS").sessions_in_range("2023-01-03", "2023-07-31").tz_localize(None)
DATES = SESSIONS.strftime("%Y-%m-%d").tolist()
COLUMNS = {name: name for name in ("date", "close", "open", "high", "low", "volume",
                                   "dividend_amount", "split_coefficient")}
WARMUP_SESSIONS = 5   # rv_5 needs five returns, so six closes: the first row is the sixth date
CLOSE = {d: 100+i*.3+3*math.sin(i) for i, d in enumerate(DATES)}


def _price_table(drop=(), **overrides):
    """One ticker's price table; ``overrides`` maps a column to ``{date: value}`` changes."""
    rows = []
    for d in DATES:
        if d in drop:
            continue
        close = CLOSE[d]
        rows.append({"date": d, "close": close, "open": close*.99, "high": close*1.01,
                     "low": close*.98, "volume": 1000.+len(rows), "dividend_amount": 0.,
                     "split_coefficient": 1.})
    table = pd.DataFrame(rows)
    for column, changes in overrides.items():
        for d, value in changes.items():
            table.loc[table.date == d, column] = value
    return table


class _Vol(IndexCloseRows):
    """The real envelope projection with a fixed volatility-index read."""

    def __init__(self, key, params):
        super().__init__(key, {"root": "r", "source": "s", "symbol": params["symbol"]})
        self.symbol = params["symbol"]

    def run(self, ctx, inputs):
        return {"records": [{"date": d, "close": 20., "asof_ms": 0, "instrument": self.symbol,
                             "contract": self.symbol, "group": self.symbol} for d in DATES]}

    def fingerprint(self):
        return {"sha256": "fixture"}


@pytest.fixture
def make(tmp_path, blob_store, monkeypatch):
    """Build a panel config over one price table; extra keys override the defaults."""
    monkeypatch.setattr(cdf_study, "IndexCloseRows", _Vol)

    def build(table=None, holdout_start=None, **over):
        folder = tmp_path/"prices-src"
        (folder/"aaa").mkdir(parents=True, exist_ok=True)
        (_price_table() if table is None else table).to_parquet(folder/"aaa"/"p.parquet")
        blob_store.add("prices", folder)
        config = {
            "root": blob_store.path, "reader": "price_calendar", "symbols": {"AAA": "VOL"},
            "price_source": {"source": "prices", "stream": "files",
                             "relpath": {"AAA": "aaa/p.parquet"}, "columns": COLUMNS},
            "iv_source": "i", "since": "2023-01-01", "max_dte": 45, "exact_dte": HORIZON,
            "lags": 5, "windows": [1, 5], "feature_gap_days": 7, "reference_floor": .001,
            "spot_tolerance": .02, "reference_window": 5, "change_lags": [1, 5],
            "directional_windows": [2, 5], "periods_per_year": 252, "calendar": "XNYS",
            "calendar_pad_days": 30, "dividend_field": "dividend_amount", **over}
        return PriceCalendarCDFPanel(config, holdout_start=holdout_start)
    return build


ALL_SESSIONS = xc.get_calendar("XNYS").sessions_in_range("2023-01-03", "2023-09-30"
                                                         ).strftime("%Y-%m-%d").tolist()


def _settlement(date):
    """The last session on or before ``date`` plus the horizon (an expiry that is no session)."""
    expiry = (pd.Timestamp(date)+pd.Timedelta(days=HORIZON)).strftime("%Y-%m-%d")
    return max(s for s in ALL_SESSIONS if s <= expiry)


def _expiry(date):
    return (pd.Timestamp(date)+pd.Timedelta(days=HORIZON)).strftime("%Y-%m-%d")


def _expected_quote_dates():
    """Entry dates after the warm-up whose settlement session has a price in the file."""
    return [d for d in DATES[WARMUP_SESSIONS:]
            if _settlement(d) <= DATES[-1] and _expiry(d) == _settlement(d)]


def test_one_row_per_price_date_settling_on_the_last_session_at_or_before_the_expiry(make):
    frame = make().read()
    assert frame.quote_date.tolist() == _expected_quote_dates()
    assert frame.settlement_date.tolist() == [_settlement(d) for d in frame.quote_date]
    assert (frame.actual_calendar_dte == HORIZON).all()
    assert (pd.to_datetime(frame.expiry)-pd.to_datetime(frame.quote_date)).dt.days.eq(HORIZON).all()
    assert (frame.first_seen_date == frame.quote_date).all()
    assert (frame.series_age_calendar == 0).all()


def test_an_expiry_on_a_market_holiday_leaves_no_row_so_every_cell_is_one_horizon(make):
    frame = make().read()
    assert "2023-03-31" not in set(frame.quote_date)   # expiry 2023-04-07, Good Friday
    assert set(frame.actual_calendar_dte) == {HORIZON}


def test_the_target_is_the_log_close_ratio_to_the_settlement_session(make):
    frame = make().read()
    for row in frame.itertuples():
        assert row.terminal_return == pytest.approx(math.log(CLOSE[row.settlement_date]/CLOSE[row.quote_date]))
        assert row.spot == pytest.approx(CLOSE[row.quote_date])
        assert row.settlement_date <= row.expiry


def test_ohlc_and_volume_columns_are_read_from_the_same_file(make):
    frame = make(ohlc_windows=[5]).read()
    assert {"range_variance_5", "overnight_variance_5"} <= set(frame)
    assert frame.range_variance_5.notna().any()


def test_a_missing_interior_close_refuses_every_path_through_it(make):
    hole = DATES[30]
    panel = make(_price_table(drop={hole}))
    frame = panel.read()
    assert panel.refused["AAA"]["incomplete_path"] > 0
    for row in frame.itertuples():
        assert not row.quote_date <= hole <= row.settlement_date


def test_a_split_flag_is_accepted_and_the_close_is_taken_as_given(make):
    table = _price_table(split_coefficient={DATES[40]: 2.})
    frame = make(table).read()
    row = frame[frame.quote_date == DATES[40]].iloc[0]
    assert row.spot == pytest.approx(CLOSE[DATES[40]])


def test_the_read_is_deterministic(make):
    first, second = make().read(), make().read()
    pd.testing.assert_frame_equal(first, second)


def test_no_option_chain_columns_and_no_spot_check(make):
    frame = make(spot_tolerance=1e-12).read()   # nothing to compare the close with
    assert not [c for c in frame if c.startswith(("chain_", "rn_"))]


def test_holdout_lock_matches_the_base_rule(make):
    start = DATES[80]
    full = make().read()
    locked_panel = make(holdout_start=start)
    locked = locked_panel.read()
    kept = full[(full.quote_date < start) & (full.settlement_date < start)].reset_index(drop=True)
    pd.testing.assert_frame_equal(locked, kept)
    cohort = [(d, _settlement(d)) for d in DATES if _expiry(d) == _settlement(d)]   # every row the lock sees, warm-up included
    assert locked_panel.refused["AAA"]["holdout_locked"] == sum(
        1 for d, e in cohort if d >= start or e >= start)


def test_the_provenance_names_the_price_file_hash(make):
    panel = make()
    panel.read()
    finger = panel.provenance()["readers"]["AAA"]
    assert finger["relpath"] == "aaa/p.parquet" and len(finger["sha256"]) == 64
    assert panel.provenance()["sha256"] == {"price:AAA": finger["sha256"]}


# -- keyed tables ------------------------------------------------------------------------------

class _Table(ObservationTables):
    """An in-memory keyed table instead of a store read; matching is the real code's."""

    rows = []

    def _read(self, ctx, name, spec):
        return list(type(self).rows), {"rows": len(type(self).rows), "sha256": "t"*64}


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr("dskit.pipeline.libs.observation_tables.ObservationTables", _Table)
    _Table.rows = [{"symbol": "AAA", "quote_date": d, "liq": float(i)} for i, d in enumerate(DATES)]
    return _Table


def _spec(**table):
    return {"key": ["symbol", "quote_date"],
            "tables": {"liq": {"source": "s", "stream": "t", "columns": {"liq": "liq_x"}, **table}}}


def test_a_keyed_table_is_attached_by_symbol_and_date(make, keyed):
    frame = make(keyed_tables=_spec()).read()
    base = make().read()
    assert len(frame) == len(base) and frame.quote_date.tolist() == base.quote_date.tolist()
    assert frame.liq_x.tolist() == [float(DATES.index(d)) for d in frame.quote_date]
    assert list(frame.columns[:len(base.columns)]) == list(base.columns)


def test_an_as_of_table_takes_the_strictly_earlier_row_with_companions(make, keyed):
    frame = make(keyed_tables=_spec(max_age_days=5)).read()
    first = frame.iloc[0]
    assert first.liq_x == DATES.index(first.quote_date)-1
    assert first.liq_x_age_days >= 1 and first.liq_x_missing == 0


def test_a_keyed_column_that_collides_with_a_panel_column_is_refused(make, keyed):
    spec = _spec()
    spec["tables"]["liq"]["columns"] = {"liq": "spot"}
    with pytest.raises(ValueError, match="spot"):
        make(keyed_tables=spec).read()


def test_keyed_tables_also_attach_through_the_base_reader(tmp_path, monkeypatch, keyed):
    from test_study_literals import _config
    config = _config(tmp_path, monkeypatch, keyed_tables={
        "key": ["symbol", "quote_date"],
        "tables": {"liq": {"source": "s", "stream": "t", "columns": {"liq": "liq_x"}}}})
    keyed.rows = [{"symbol": "QQQ", "quote_date": d, "liq": 1.} for d in
                  pd.bdate_range("2023-01-02", "2023-06-30").strftime("%Y-%m-%d")]
    frame = ExactExpiryCDFPanel(config).read()
    assert (frame.liq_x == 1.).all()


# -- corporate actions -------------------------------------------------------------------------

def _actions(**over):
    return {"max_dividend_yield": .05, "max_abs_jump": .4, "windows": {}, **over}


def test_a_dividend_spike_drops_the_rows_whose_features_or_label_touch_it(make):
    spike = DATES[60]
    table = _price_table(dividend_amount={spike: CLOSE[spike]*.5})
    panel = make(table, corporate_actions=_actions())
    frame = panel.read()
    base = make(table).read()
    position = DATES.index(spike)
    lookback = 5
    for d in base.quote_date:
        entry = DATES.index(d)
        end = DATES.index(base.settlement_date[base.quote_date == d].iloc[0])
        touches = entry-lookback+1 <= position <= end
        assert (d in set(frame.quote_date)) == (not touches), d
    assert panel.refused["AAA"]["corporate_action_path"] == len(base)-len(frame) > 0


def test_a_jump_counts_only_with_a_split_flag(make):
    day = DATES[60]
    jump = {day: CLOSE[day]*3}
    unflagged = make(_price_table(close=jump), corporate_actions=_actions())
    kept_unflagged = len(unflagged.read())     # read before the next table replaces the file
    flagged = make(_price_table(close=jump, split_coefficient={day: 3.}),
                   corporate_actions=_actions())
    assert kept_unflagged > len(flagged.read())
    assert unflagged.refused["AAA"]["corporate_action_path"] == 0
    assert flagged.refused["AAA"]["corporate_action_path"] > 0


def test_a_declared_window_excludes_paths_that_touch_it(make):
    lo, hi = DATES[50], DATES[52]
    panel = make(corporate_actions=_actions(windows={"AAA": [[lo, hi]]}))
    frame = panel.read()
    for row in frame.itertuples():
        entry, end = DATES.index(row.quote_date), DATES.index(row.settlement_date)
        assert not (entry-5+1 <= DATES.index(hi) and DATES.index(lo) <= end)


def test_empty_corporate_actions_exclude_nothing(make):
    base = make().read()
    panel = make(corporate_actions={})
    pd.testing.assert_frame_equal(panel.read(), base)
    assert panel.refused["AAA"].get("corporate_action_path", 0) == 0


# -- validation ---------------------------------------------------------------------------------

def test_the_reader_is_selected_by_name(make):
    assert panel_class({}) is cdf_study.ExactExpiryCDFPanel
    assert panel_class({"reader": "price_calendar"}) is PriceCalendarCDFPanel
    with pytest.raises(ValueError, match="reader"):
        panel_class({"reader": "nope"})


@pytest.mark.parametrize("change, match", [
    ({"reader": "nope"}, "reader"),
    ({"exact_dte": None}, "exact_dte"),
    ({"price_source": "prices"}, "price_source"),
    ({"price_source": {"source": "p"}}, "price_source"),
    ({"surface": "s.parquet"}, "surface"),
    ({"raw_chain": {"proxy_probabilities": [.5]}}, "raw_chain"),
    ({"keyed_tables": {"key": ["symbol"], "tables": {}, "surprise": 1}}, "keyed_tables"),
    ({"corporate_actions": {"max_abs_jump": -1}}, "max_abs_jump"),
    ({"corporate_actions": {"windows": {"AAA": [["2023-02-01"]]}}}, "windows"),
    ({"corporate_actions": {"windows": {"AAA": [["2023-03-01", "2023-02-01"]]}}}, "windows"),
    ({"corporate_actions": {"surprise": 1}}, "surprise"),
])
def test_a_bad_reader_config_is_refused_before_any_read(make, change, match):
    with pytest.raises(ValueError, match=match):
        make(**change)


def test_null_option_source_keys_are_ignored_by_the_calendar_reader(make):
    nulls = {"surface": None, "lifecycle": None, "chain_features": None, "raw_chain": None}
    pd.testing.assert_frame_equal(make(**nulls).read(), make().read())


def test_the_base_reader_refuses_corporate_actions(tmp_path, monkeypatch):
    from test_study_literals import _config
    with pytest.raises(ValueError, match="corporate_actions"):
        ExactExpiryCDFPanel(_config(tmp_path, monkeypatch, corporate_actions=_actions()))


def test_the_node_accepts_the_reader_and_needs_no_surface_with_it():
    params = {"root": "x", "reader": "price_calendar", "symbols": {"AAA": "VOL"},
              "price_source": {"source": "p", "stream": "files", "relpath": {"AAA": "a.parquet"},
                               "columns": COLUMNS},
              "iv_source": "i", "since": "2023-01-01", "max_dte": 45, "exact_dte": 7, "lags": 5,
              "windows": [1, 5], "feature_gap_days": 7, "reference_floor": .001,
              "spot_tolerance": .02, "columns": ["symbol", "quote_date"],
              "directional_windows": [5], "change_lags": [1, 5], "reference_window": 5}
    assert ExactExpiryPanelRead.validate_params(params) == []
    without = {k: v for k, v in params.items() if k != "reader"}
    problems = ExactExpiryPanelRead.validate_params(without)
    assert any("surface" in p for p in problems) and any("lifecycle" in p for p in problems)
    assert any("reader" in p for p in ExactExpiryPanelRead.validate_params({**params, "reader": "x"}))


def test_the_node_refuses_corporate_actions_without_the_calendar_reader():
    base = {"root": "x", "surface": "s", "lifecycle": "l", "symbols": {"AAA": "V"},
            "price_source": "p", "iv_source": "i", "since": "2023-01-01", "max_dte": 45,
            "lags": 5, "windows": [1, 5], "feature_gap_days": 7, "reference_floor": .001,
            "spot_tolerance": .02, "columns": ["symbol"], "corporate_actions": {}}
    assert any("corporate_actions" in p for p in ExactExpiryPanelRead.validate_params(base))


# -- the exclusion span counts price RECORDS, as the features do ---------------------------------

def _raw_split_table(split_at, drop=()):
    """Closes quoted 4:1 higher before ``split_at``, with the split flagged on that day."""
    table = _price_table(drop=set(drop))
    before = table.date < DATES[split_at]
    table.loc[before, ["close", "open", "high", "low"]] *= 4
    table.loc[table.date == DATES[split_at], "split_coefficient"] = 4.
    return table


@pytest.mark.parametrize("missing", [[50], [40, 45, 50]])
def test_missing_records_do_not_let_an_event_reach_a_feature_window(make, missing):
    split, windows = 30, [5, 22]
    panel = make(_raw_split_table(split, drop={DATES[i] for i in missing}),
                 corporate_actions=_actions(), lags=10, windows=windows, reference_window=22,
                 change_lags=[5], directional_windows=[5, 10])
    frame = panel.read()
    records = [d for i, d in enumerate(DATES) if i not in missing]
    for row in frame.itertuples():
        position = records.index(row.quote_date)         # the feature window is RECORDS
        oldest = records[max(position-max(10, *windows)+1, 0)]
        assert not oldest <= DATES[split] <= row.settlement_date, row.quote_date
    assert frame.rv_22.max() < .1                     # no 4:1 return inside any kept window


# -- vocabularies, refusals and clean errors ---------------------------------------------------------

def test_the_price_vocabulary_and_option_keys_are_pinned():
    assert set(cdf_study.PRICE_FIELDS) == {"date", "close", "open", "high", "low", "volume",
                                           "dividend_amount", "split_coefficient"}
    assert set(cdf_study.OPTION_SOURCE_KEYS) == {
        "surface", "lifecycle", "chain_features", "raw_chain", "surface_features",
        "matched_dte_vrp", "decision_regions", "macro_event_calendars"}


@pytest.mark.parametrize("key, value", [("decision_regions", {"panel_years": [2023]}),
                                        ("macro_event_calendars", {"x": []}),
                                        ("surface_features", True), ("matched_dte_vrp", True)])
def test_the_calendar_reader_refuses_option_keys_at_construction(make, key, value):
    with pytest.raises(ValueError, match=key):
        make(**{key: value})


def test_the_base_reader_refuses_a_reader_that_is_not_its_own(tmp_path, monkeypatch):
    from test_study_literals import _config
    with pytest.raises(ValueError, match="reader"):
        ExactExpiryCDFPanel(_config(tmp_path, monkeypatch, reader="price_calendar"))


def test_the_node_refuses_a_null_exact_dte_as_run_does():
    params = {"root": "x", "surface": "s", "lifecycle": "l", "symbols": {"AAA": "V"},
              "price_source": "p", "iv_source": "i", "since": "2023-01-01", "max_dte": 45,
              "lags": 22, "windows": [1, 5, 22], "feature_gap_days": 7, "reference_floor": .001,
              "spot_tolerance": .02, "columns": ["symbol"], "exact_dte": None}
    assert any("exact_dte" in p for p in ExactExpiryPanelRead.validate_params(params))


def test_the_date_stamp_is_utc_midnight_milliseconds():
    assert PriceCalendarCDFPanel._stamp("2023-01-03") == 1672704000000


@pytest.mark.parametrize("change", [{"close": {DATES[10]: -1.}}, {"close": {DATES[10]: 0.}},
                                    {"split_coefficient": {DATES[10]: 0.}},
                                    {"split_coefficient": {DATES[10]: -2.}}])
def test_a_bad_price_or_coefficient_is_refused_naming_symbol_and_date(make, change):
    with pytest.raises(ValueError, match=f"AAA {DATES[10]}|AAA.*{DATES[10]}"):
        make(_price_table(**change), corporate_actions=_actions()).read()


def test_a_repeated_price_date_is_refused_by_name(make):
    table = _price_table()
    table = pd.concat([table, table.iloc[[10]]], ignore_index=True)
    with pytest.raises(ValueError, match=f"AAA.*repeats.*{DATES[10]}"):
        make(table).read()


def test_a_flagged_jump_on_a_non_session_record_is_flagged_on_the_next_session(make):
    saturday = "2023-03-04"
    table = _price_table()
    table = pd.concat([table, pd.DataFrame([{
        "date": saturday, "close": CLOSE["2023-03-03"]*3, "open": CLOSE["2023-03-03"]*3,
        "high": CLOSE["2023-03-03"]*3.1, "low": CLOSE["2023-03-03"]*2.9, "volume": 1.,
        "dividend_amount": 0., "split_coefficient": 3.}])]).sort_values("date", ignore_index=True)
    frame = make(table, corporate_actions=_actions()).read()
    records = table.date.tolist()
    kept_with_event = [r.quote_date for r in frame.itertuples()
                       if records[max(records.index(r.quote_date)-5+1, 0)] <= saturday <= r.settlement_date]
    assert kept_with_event == []
    assert any(frame.quote_date < saturday) and any(frame.quote_date > saturday)


@pytest.mark.parametrize("reader", [["a"], {}, 3])
def test_an_unhashable_or_non_string_reader_is_refused_cleanly(reader):
    assert any("reader" in p for p in cdf_study.panel_reader_problems({"reader": reader}))
    with pytest.raises(ValueError, match="reader"):
        panel_class({"reader": reader})


# -- price_source.window: entries only, features keep their lookback (ADR-0230) ---------------

WINDOW_START, WINDOW_END = DATES[40], DATES[100]


def _window(**over):
    return {"field": "date", "start": WINDOW_START, "end": WINDOW_END, **over}


def _with_window(make, window):
    table = _price_table()
    config_source = {"source": "prices", "stream": "files",
                     "relpath": {"AAA": "aaa/p.parquet"}, "columns": COLUMNS, "window": window}
    return make(table, price_source=config_source)


def test_a_windowed_panel_is_the_full_panel_cut_to_entries_and_settlements_inside_it(make):
    full = make().read()
    frame = _with_window(make, _window()).read()
    kept = full[(full.quote_date >= WINDOW_START) & (full.settlement_date <= WINDOW_END)]
    pd.testing.assert_frame_equal(frame, kept.reset_index(drop=True))
    assert frame.quote_date.min() >= WINDOW_START and frame.settlement_date.max() <= WINDOW_END
    assert len(frame) > 0


def test_features_of_the_first_windowed_entry_use_records_before_the_window_start(make):
    frame = _with_window(make, _window()).read()
    first = frame.iloc[0]
    assert first.quote_date >= WINDOW_START
    closes = [CLOSE[d] for d in DATES if d <= first.quote_date][-6:]
    expected = [math.log(b/a) for a, b in zip(closes, closes[1:])]
    assert first.ret_lag_0 == pytest.approx(expected[-1])
    assert first.ret_lag_4 == pytest.approx(expected[0])
    assert frame.rv_5.notna().all()


def test_a_start_only_and_an_end_only_window_each_cut_one_side(make):
    full = make().read()
    start_only = _with_window(make, {"field": "date", "start": WINDOW_START}).read()
    end_only = _with_window(make, {"field": "date", "end": WINDOW_END}).read()
    pd.testing.assert_frame_equal(
        start_only, full[full.quote_date >= WINDOW_START].reset_index(drop=True))
    pd.testing.assert_frame_equal(
        end_only, full[full.settlement_date <= WINDOW_END].reset_index(drop=True))


def test_the_windowed_cohort_equals_step_1s_target_dates(make):
    """Step 1 = ParquetRows window then HorizonPairs; step 1b must list the same entry dates."""
    from dskit.pipeline.kinds_table import HorizonPairs

    cut = ParquetRows.cut(_price_table().to_dict("records"), _window())
    step1 = HorizonPairs("pairs", {
        "horizon_days": HORIZON, "fallback": "previous", "symbol": "AAA", "date_field": "date",
        "close_field": "close", "fields": {
            "symbol": "s", "date": "d", "settlement_date": "x", "entry_close": "c0",
            "settle_close": "c1", "terminal_return": "r", "period": "p"}}
    ).run(None, {"records": cut})["records"]
    listed = [r["d"] for r in step1 if _expiry(r["d"]) == _settlement(r["d"])]
    assert _with_window(make, _window()).read().quote_date.tolist() == listed
