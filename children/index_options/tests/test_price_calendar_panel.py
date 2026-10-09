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


# -- the panel cache (ADR-0236 amendment): one build per data identity, reused by every stage ------

def test_a_cached_read_is_built_once_and_reused_until_an_input_moves(
        make, tmp_path, blob_store, monkeypatch):
    config = make().config                  # acquires the price source once
    cache, built, read = tmp_path/"panel-cache", [], PriceCalendarCDFPanel.read
    monkeypatch.setattr(PriceCalendarCDFPanel, "read", lambda self: built.append(1) or read(self))

    def stage(data=config, holdout=None):
        return PriceCalendarCDFPanel(data, holdout_start=holdout).cached_read(cache)[:2]

    frame, provenance = stage()
    again, provenance_again = stage()
    assert built == [1] and provenance_again == provenance
    pd.testing.assert_frame_equal(again, frame)
    pd.testing.assert_frame_equal(frame, read(PriceCalendarCDFPanel(config)))  # it IS the read
    stage({**config, "lags": 6})            # a data-config change rebuilds
    assert built == [1, 1]
    locked, _ = stage(holdout=DATES[80])    # so does a holdout, whose rows it never serves
    assert built == [1, 1, 1]
    assert (locked.quote_date < DATES[80]).all() and (locked.settlement_date < DATES[80]).all()
    assert stage(holdout=DATES[80]) and built == [1, 1, 1]
    folder = tmp_path/"prices-src"
    _price_table(close={DATES[50]: CLOSE[DATES[50]]*1.01}).to_parquet(folder/"aaa"/"p.parquet")
    blob_store.add("prices", folder)        # a new snapshot of a read source rebuilds
    moved, _ = stage(holdout=DATES[80])
    assert built == [1, 1, 1, 1]
    assert moved.loc[moved.quote_date == DATES[50], "spot"].tolist() == pytest.approx(
        [CLOSE[DATES[50]]*1.01])


def test_a_cached_read_keys_on_every_store_root_and_existing_path_the_config_names(
        make, tmp_path, monkeypatch):
    panel = make()
    legacy = tmp_path/"legacy.parquet"
    legacy.write_bytes(b"x")
    monkeypatch.setenv("HOME", str(tmp_path))
    table = {"root": str(tmp_path/"elsewhere"), "path": str(legacy), "relpath": "a/b.parquet",
             "home": "~/legacy.parquet", "here": ".", "notes": str(tmp_path/"noted.txt")}
    (tmp_path/"noted.txt").write_text("documentation")
    roots, paths = PriceCalendarCDFPanel({**panel.config, "extra": table})._locations()
    assert roots == sorted({panel.config["root"], str(tmp_path/"elsewhere")})
    # a store relpath names no file here, "~" expands, the working directory and notes are
    # never data, and a legacy file brings its source-hash sidecar
    assert paths == [str(legacy), str(legacy)+".sources.json"]


def test_every_part_of_the_cache_key_rebuilds_the_panel(make, tmp_path, monkeypatch):
    # ADR-0236 amendment 2: a key missing a part would serve a stale panel (review round 2).
    from dskit.pipeline.libs.parquet import ParquetFrameCache
    from dskit.production.release import RuntimeFingerprint

    legacy = tmp_path/"surface.parquet"
    legacy.write_bytes(b"one")
    config = {**make().config, "extra": str(legacy)}
    cache, built, read = tmp_path/"panel-cache", [], PriceCalendarCDFPanel.read
    monkeypatch.setattr(PriceCalendarCDFPanel, "read", lambda self: built.append(1) or read(self))
    fingerprint = RuntimeFingerprint.capture().to_obj()
    assert set(PriceCalendarCDFPanel(config).cache_identity(fingerprint)) == {
        "reader", "config", "holdout_start", "code", "environment", "stores", "paths"}

    def stage(cls=PriceCalendarCDFPanel):
        cls(config).cached_read(cache)
        return len(built)

    assert (stage(), stage()) == (1, 1)
    legacy.write_bytes(b"two!")                                      # a legacy file
    assert stage() == 2
    real = ParquetFrameCache.code_digest
    monkeypatch.setattr(ParquetFrameCache, "code_digest", lambda *p: "edited" + real(*p))
    assert stage() == 3                                              # the code
    capture = RuntimeFingerprint.capture
    monkeypatch.setattr(RuntimeFingerprint, "capture", classmethod(
        lambda cls: type("F", (), {"to_obj": lambda s: {**capture().to_obj(), "x": 1}})()))
    assert stage() == 4                                              # the environment

    class Other(PriceCalendarCDFPanel):
        """Another reader class."""

    assert stage(Other) == 5                                         # the reader


def test_a_cached_read_forgets_resolved_snapshots_and_reads_uncached_without_a_fingerprint(
        make, tmp_path, monkeypatch, capsys):
    from index_options import datafiles
    from dskit.production.base import ProductionError
    from dskit.production.release import RuntimeFingerprint

    panel = make()
    datafiles._SNAPSHOTS["stale"] = {"snapshot": "old"}
    panel.cached_read(tmp_path/"cache")
    assert "stale" not in datafiles._SNAPSHOTS

    def broken(cls):
        raise ProductionError(["distribution at x has no Name/Version metadata"])

    monkeypatch.setattr(RuntimeFingerprint, "capture", classmethod(broken))
    frame, provenance, state = PriceCalendarCDFPanel(panel.config).cached_read(tmp_path/"other")
    assert len(frame) and provenance["sha256"] and not (tmp_path/"other").exists()
    assert state == "off"
    assert "panel cache off" in capsys.readouterr().out


# -- per-symbol price sources (ADR-0236): disjoint universes in separate onboarded sources --------

def _second_source(tmp_path, blob_store, table):
    """Acquire ``bbb/q.parquet`` into a second source, ``prices-b``."""
    folder = tmp_path/"prices-b-src"
    (folder/"bbb").mkdir(parents=True, exist_ok=True)
    table.to_parquet(folder/"bbb"/"q.parquet")
    blob_store.add("prices-b", folder)


def test_a_symbol_may_name_its_own_price_source(make, tmp_path, blob_store):
    other = _price_table(close={d: CLOSE[d]*2 for d in DATES})
    one = make()   # acquires the default source first
    _second_source(tmp_path, blob_store, other)
    relpath = {"AAA": "aaa/p.parquet",
               "BBB": {"source": "prices-b", "stream": "files", "relpath": "bbb/q.parquet"}}
    panel = make(symbols={"AAA": "VOL", "BBB": "VOL"},
                 price_source={**one.config["price_source"], "relpath": relpath})
    frame = panel.read()
    alone = one.read()
    a, b = frame[frame.symbol == "AAA"], frame[frame.symbol == "BBB"]
    pd.testing.assert_frame_equal(a[list(alone.columns)].reset_index(drop=True),
                                  alone.reset_index(drop=True))
    assert b.spot.tolist() == pytest.approx([2*CLOSE[d] for d in b.quote_date])
    readers = panel.provenance()["readers"]
    assert readers["AAA"] == one.provenance()["readers"]["AAA"]   # a file entry is unchanged
    assert readers["BBB"]["source"] == "prices-b" and readers["BBB"]["stream"] == "files"
    assert readers["BBB"]["relpath"] == "bbb/q.parquet" and len(readers["BBB"]["sha256"]) == 64
    assert panel.provenance()["sha256"]["price:BBB"] == readers["BBB"]["sha256"]


@pytest.mark.parametrize("entry", [
    {"source": "prices-b", "stream": "files"}, {"source": "", "stream": "files", "relpath": "x"},
    {"source": "s", "stream": "files", "relpath": "x", "typo": 1}, "", 3,
    {"source": "s", "stream": "files", "relpath": "/abs/x"},
    {"source": "s", "stream": "files", "relpath": "x", "manifest_sha256": "0"*64}])
def test_a_malformed_price_location_is_refused(make, entry):
    with pytest.raises(ValueError, match="price_source"):
        make(price_source={"source": "prices", "stream": "files", "columns": COLUMNS,
                           "relpath": {"AAA": entry}})


def test_a_symbol_without_a_price_location_is_refused_by_name(make):
    with pytest.raises(ValueError, match="BBB"):
        make(symbols={"AAA": "VOL", "BBB": "VOL"}).read()


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
        touches = entry-lookback <= position <= end
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


# -- ADR-0236 amendment 3: the observation reads' vintage --------------------------------------

@pytest.mark.parametrize("vintage", [None, 1_759_670_000_000])
def test_a_declared_vintage_bounds_every_observation_read_of_the_panel(make, monkeypatch, vintage):
    # A stage that rebuilds the panel after a scheduled acquisition must read what the
    # first stage read: the index-close, market-symbol and FRED reads all take the vintage.
    seen = []

    class _Seen(_Vol):
        def __init__(self, key, params):
            super().__init__(key, params)
            self.vintage = params.get("as_of_acquisition_ms", "absent")

        def run(self, ctx, inputs):   # a read; the price projection constructs but never reads
            seen.append(("index", self.vintage))
            return super().run(ctx, inputs)

    class _Rates:
        def __init__(self, key, params):
            self.vintage = params.get("as_of_acquisition_ms", "absent")

        def run(self, ctx, inputs):
            seen.append(("fred", self.vintage))
            return {"records": [{"observation_date": d, "DFF": 5.} for d in DATES]}

        def fingerprint(self):
            return {"sha256": "fixture"}

    monkeypatch.setattr(cdf_study, "IndexCloseRows", _Seen)
    monkeypatch.setattr(cdf_study, "ObservationRows", _Rates)
    declared = {} if vintage is None else {"as_of_acquisition_ms": vintage}
    make(market_symbols={"market_vix9d": {"symbol": "VIX9D", "max_age_days": 7}},
         fred_market_symbols={"rate_dff": {"stream": "dff", "field": "DFF", "lag_sessions": 1,
                                           "max_age_days": 7}}, **declared).read()
    expected = "absent" if vintage is None else vintage
    assert seen == [("index", expected), ("index", expected), ("fred", expected)]


@pytest.mark.parametrize("vintage", [None, 1_759_670_000_000])
def test_the_surface_readers_price_read_takes_the_vintage_too(make, monkeypatch, vintage):
    # Review round 1 (M3): the option-surface reader reads its prices as observations.
    seen = []

    class _Prices(IndexCloseRows):
        def run(self, ctx, inputs):
            seen.append(self.params.get("as_of_acquisition_ms", "absent"))
            return {"records": []}

        def fingerprint(self):
            return {"sha256": "fixture"}

    declared = {} if vintage is None else {"as_of_acquisition_ms": vintage}
    panel = make(**declared)
    monkeypatch.setattr(cdf_study, "IndexCloseRows", _Prices)
    panel.config = {**panel.config, "price_source": "prices"}   # the surface reader's form
    panel.reader_fingerprints = {}
    ExactExpiryCDFPanel._price_records(panel, "AAA")
    assert seen == ["absent" if vintage is None else vintage]


@pytest.mark.parametrize("value", [-1, True, 1.5, "1759670000000"])
def test_a_malformed_vintage_refuses_the_panel_and_its_node(make, value):
    with pytest.raises(ValueError, match="as_of_acquisition_ms"):
        make(as_of_acquisition_ms=value)
    params = {"root": "x", "surface": "s.parquet", "lifecycle": "l.parquet",
              "symbols": {"QQQ": "VXN"}, "price_source": "p", "iv_source": "i",
              "since": "2020-01-01", "max_dte": 45, "lags": 22, "windows": [1, 5, 22],
              "feature_gap_days": 7, "reference_floor": 0.001, "spot_tolerance": 0.02,
              "columns": ["symbol", "quote_date"], "as_of_acquisition_ms": value}
    assert any("as_of_acquisition_ms" in p for p in ExactExpiryPanelRead.validate_params(params))
    params["as_of_acquisition_ms"] = 0
    assert not any("as_of_acquisition_ms" in p
                   for p in ExactExpiryPanelRead.validate_params(params))


def test_price_source_read_bounds_reaches_core_before_rows(make):
    panel = make()
    panel.config["price_source"]["read_bounds"] = {"date": {"end_before": "2023-02-01"}}
    rows, _ = panel._file_rows("AAA")
    assert rows
    assert all(r["date"] < "2023-02-01" for r in rows)


# -- S1 / ADR-0251: synthetic vintage metamorphisms through real processing -----------------

def _s1_chart(table, events=()):
    """A synthetic chart envelope with an explicit (possibly empty) split inventory."""
    import json

    stamp = lambda day: int(pd.Timestamp(day, tz="UTC").timestamp()) + 12*3600
    splits = {str(stamp(day)): {"date": stamp(day), "numerator": ratio, "denominator": 1}
              for day, ratio in events}
    result = {"meta": {"symbol": "AAA", "exchangeTimezoneName": "UTC"},
              "timestamp": [stamp(day) for day in table.date],
              "events": {"splits": splits},
              "indicators": {"quote": [table[["open", "high", "low", "close", "volume"]]
                                       .to_dict("list")],
                             "adjclose": [{"adjclose": table.close.tolist()}]}}
    return json.dumps({"chart": {"result": [result]}}).encode()


def _s1_normalize(body):
    """Use the strict source transform, including its real Parquet serialization."""
    import io
    from index_options.stock_bars import StockDailyBars

    normalizer = StockDailyBars({"strict_split_inventory": True, "price_decimals": 12},
                               "2023-08-01T00:00:00+00:00")
    return pd.read_parquet(io.BytesIO(normalizer.transform("AAA", body)))


def _s1_panel(make, keyed, table):
    """Real liquidity calculation and panel target/features; only keyed storage is stubbed."""
    from dskit.pipeline.libs.bar_features import DailyBarFeatures

    bars = table.rename(columns={"date": "quote_date"}).to_dict("records")
    fields = {"log_volume_ratio": "vl_log_volume_ratio",
              "log_dollar_volume": "vl_log_dollar_volume", "amihud": "vl_amihud"}
    parts = []
    for window, declared in ((22, fields), (66, {"log_volume_ratio": "vl_log_volume_ratio_66"})):
        node = DailyBarFeatures("s1", {
            "entity_field": "symbol", "date_field": "quote_date", "close_field": "close",
            "volume_field": "volume", "volume_liquidity": {"window": window, "fields": declared}})
        parts.append(node.run(None, {"bars": bars})["records"])
    assert [r["quote_date"] for r in parts[0]] == [r["quote_date"] for r in parts[1]]
    keyed.rows = [{**a, **b} for a, b in zip(*parts)]
    columns = {name: name for name in [*fields.values(), "vl_log_volume_ratio_66"]}
    panel = make(table, lags=22, windows=[1, 5, 22, 66], reference_window=22,
                 change_lags=[1, 5, 22], directional_windows=[5, 22], ohlc_windows=[5, 22],
                 corporate_actions=_actions(),
                 keyed_tables={"key": ["symbol", "quote_date"],
                               "tables": {"liquidity": {"source": "s", "stream": "t",
                                                        "columns": columns}}})
    return panel.read(), panel


@pytest.mark.parametrize("ratios", [(2.,), (.2,), (2., 3.), ()],
                         ids=["forward", "reverse", "repeated", "no-split"])
def test_s1_matching_late_vintage_preserves_pre_event_features_and_targets(make, keyed, ratios):
    # Compare origins settled BEFORE the later actions. A later coherent snapshot
    # expresses the same economic path in new share units, with its matching ledger.
    old = _price_table()
    old["volume"] = [10000 + 100*i for i in range(len(old))]
    factor = math.prod(ratios)
    new = old.copy()
    new[["open", "high", "low", "close"]] /= factor
    new["volume"] *= factor
    # A prior correctly adjusted split also lies inside some 66-bar windows.
    prior = [(DATES[50], 2.)] if ratios else []
    events = prior + [(DATES[115 + 10*i], ratio) for i, ratio in enumerate(ratios)]
    original, _ = _s1_panel(make, keyed, _s1_normalize(_s1_chart(old, prior)))
    rebased, _ = _s1_panel(make, keyed, _s1_normalize(_s1_chart(new, events)))
    a = original[original.settlement_date < DATES[110]].reset_index(drop=True)
    b = rebased[rebased.settlement_date < DATES[110]].reset_index(drop=True)
    assert len(a) >= 20 and a.vl_log_volume_ratio_66.notna().sum() >= 20
    assert a.vl_log_volume_ratio_66.isna().any()  # startup is retained
    # Price and volume carry changed units; derived features, labels and identities agree.
    assert b.spot.tolist() == pytest.approx((a.spot/factor).tolist())
    if "terminal_price" in a:
        assert b.terminal_price.tolist() == pytest.approx((a.terminal_price/factor).tolist())
    assert b.volume.tolist() == pytest.approx((a.volume*factor).tolist())
    invariant = [c for c in a if c not in ("spot", "terminal_price", "volume")]
    pd.testing.assert_frame_equal(a[invariant], b[invariant], atol=1e-10, rtol=1e-10)


def test_s1_post_origin_outcome_changes_target_but_not_origin_features(make, keyed):
    table = _price_table()
    table["volume"] = [10000 + 100*i for i in range(len(table))]
    baseline, _ = _s1_panel(make, keyed, _s1_normalize(_s1_chart(table)))
    origin = baseline[baseline.quote_date >= DATES[80]].iloc[0]
    event_day = DATES[DATES.index(origin.quote_date) + 1]
    changed = table.copy()
    # Coherent global 2:1 share units plus a genuine 10% economic move after origin.
    changed[["open", "high", "low", "close"]] /= 2
    changed["volume"] *= 2
    changed.loc[changed.date >= event_day, ["open", "high", "low", "close"]] *= 1.1
    after, panel = _s1_panel(make, keyed, _s1_normalize(_s1_chart(changed, [(event_day, 2.)])))
    result = after[after.quote_date == origin.quote_date].iloc[0]
    assert origin.quote_date < event_day <= origin.settlement_date
    assert result.terminal_return == pytest.approx(origin.terminal_return + math.log(1.1))
    assert result.spot == pytest.approx(origin.spot/2)
    assert result.terminal_price == pytest.approx(origin.terminal_price*1.1/2)
    assert result.volume == pytest.approx(origin.volume*2)
    feature_columns = [c for c in baseline if c not in
                       ("spot", "volume", "terminal_price", "terminal_return")]
    assert len(feature_columns) >= 70
    pd.testing.assert_series_equal(origin[feature_columns], result[feature_columns],
                                   check_names=False, atol=1e-10, rtol=1e-10)
    assert panel.refused["AAA"]["corporate_action_path"] == 0


@pytest.mark.parametrize("bad", ["absent-inventory", "missing-factor", "zero", "negative", "null"])
def test_s1_strict_source_refuses_unknown_or_invalid_action_factors(bad):
    import json

    body = json.loads(_s1_chart(_price_table(), [(DATES[100], 2.)]))
    result = body["chart"]["result"][0]
    event = next(iter(result["events"]["splits"].values()))
    if bad == "absent-inventory":
        del result["events"]["splits"]
    elif bad == "missing-factor":
        del event["numerator"]
    else:
        event["numerator"] = {"zero": 0, "negative": -2, "null": None}[bad]
    with pytest.raises(ValueError, match="split inventory"):
        _s1_normalize(json.dumps(body).encode())


def test_s1_inconsistent_volume_basis_is_a_detectable_negative_not_automatic_admission(make, keyed):
    table = _price_table()
    table["volume"] = [10000 + 100*i for i in range(len(table))]
    baseline, _ = _s1_panel(make, keyed, _s1_normalize(_s1_chart(table)))
    mixed = table.copy()
    mixed[["open", "high", "low", "close"]] /= 2  # deliberately leave volume on old basis
    observed, _ = _s1_panel(make, keyed, _s1_normalize(_s1_chart(mixed, [(DATES[120], 2.)])))
    a = baseline[baseline.settlement_date < DATES[110]].reset_index(drop=True)
    b = observed[observed.settlement_date < DATES[110]].reset_index(drop=True)
    assert len(a) >= 20
    # Syntactically valid factors cannot certify a vendor's economic volume basis.
    assert b.vl_log_dollar_volume.tolist() == pytest.approx(
        (a.vl_log_dollar_volume - math.log(2)).tolist())
    assert b.vl_amihud.tolist() == pytest.approx((a.vl_amihud*2).tolist(), abs=1e-18)
    assert not (abs(b.vl_log_dollar_volume - a.vl_log_dollar_volume) < 1e-10).any()
    pd.testing.assert_series_equal(a.terminal_return, b.terminal_return, atol=1e-10, rtol=1e-10)


# -- ADR-0252: action exclusion covers every return's price endpoints -----------------------

@pytest.mark.parametrize(
    "lags,windows,entry,event,drop,sparse,action_kind",
    [
        (22, [1, 5, 22, 66], 70, 4, True, False, "window"),
        (22, [1, 5, 22, 66], 70, 3, False, False, "window"),
        (11, [1, 5], 30, 19, True, False, "window"),
        (5, [1, 5, 11], 30, 19, True, False, "detected"),
        (5, [1, 5, 11], 30, 18, False, False, "detected"),
        (10, [1, 5], 80, 60, True, True, "window"),
        (10, [1, 5], 80, 59, False, True, "window"),
        (10, [1, 5], 3, 0, True, False, "window"),
        (5, [1, 5], 30, 32, True, False, "window"),
        (5, [1, 5], 30, 35, False, False, "window"),
        (5, [1, 5], 30, 25, False, False, "none"),
        (5, [1, 5], 30, 25, False, False, "empty"),
    ],
    ids=["rv66-oldest", "rv66-before-oldest", "lags-dominate", "rv-dominate",
         "detected-before-oldest", "sparse-oldest", "sparse-before-oldest",
         "first-record-clamp", "target-crossing", "after-target",
         "no-action-rule", "empty-action-rule"],
)
def test_action_span_endpoint_boundaries(make, lags, windows, entry, event, drop,
                                        sparse, action_kind):
    import numpy as np

    event_date = DATES[event]
    action = (None if action_kind == "none" else
              {"windows": {"AAA": [[event_date, event_date]]}}
              if action_kind == "window" else {})
    panel = make(lags=lags, windows=windows, corporate_actions=action)
    # Every second session is a price record in the sparse case. Ten returns
    # then require the price at session60 for an origin at session80.
    dates = DATES[::2] if sparse else DATES
    panel._records = {"AAA": [{"date": day} for day in dates]}
    panel._events = {"AAA": [event_date] if action_kind == "detected" else []}
    refused = {}
    keep = panel._path_exclusions("AAA", np.array([entry]), np.array([entry + 4]),
                                  SESSIONS, refused)
    assert keep.tolist() == [not drop]
    assert refused.get("corporate_action_path", 0) == int(drop)


@pytest.mark.parametrize("lags,windows,feature", [
    (11, [1, 5], "ret_lag_10"),
    (5, [1, 5, 11], "rv_11"),
], ids=["return-lag-endpoint", "realized-volatility-endpoint"])
def test_action_span_drops_an_oldest_price_that_changes_a_real_feature(
        make, lags, windows, feature):
    # This public-read check ties the exclusion boundary to actual feature
    # consumption, rather than just asserting the exclusion index arithmetic.
    settings = dict(lags=lags, windows=windows, reference_window=5,
                    change_lags=[1, 5], directional_windows=[2, 5])
    baseline = make(**settings).read()
    origin = baseline.iloc[30]
    first = DATES[DATES.index(origin.quote_date) - 11]
    changed = _price_table(close={first: CLOSE[first] * .9})
    changed_frame = make(changed, **settings).read()
    changed_row = changed_frame[changed_frame.quote_date == origin.quote_date].iloc[0]
    assert changed_row[feature] != pytest.approx(origin[feature])
    excluded = make(changed, corporate_actions={"windows": {"AAA": [[first, first]]}},
                    **settings).read()
    assert origin.quote_date not in set(excluded.quote_date)
