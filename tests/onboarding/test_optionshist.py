"""libs/optionshist.py: the philippdubach end-of-day option-chain archive, through the contract.

Every test builds a tiny synthetic archive with pyarrow in ``tmp_path`` —
the published layout (``<symbol>/options_<YYYY>.parquet`` plus
``<symbol>/underlying_prices.parquet``) — and pins its files by sha256
computed HERE with hashlib, independently of the module under test.
"""

import hashlib
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from dskit.assets.base import AssetError  # noqa: E402
from dskit.onboarding import (  # noqa: E402
    check_config,
    check_message,
    load_state,
    resolve_connector,
    run_acquisition,
    scan_stream,
)
from dskit.onboarding.libs import cboe  # noqa: E402
from dskit.onboarding.libs.optionshist import (  # noqa: E402
    OPTION_COLUMNS,
    UNDERLYING_COLUMNS,
    OptionsHistConnector,
)

URL = "https://github.com/anahatsingh-ui/options-dataset-hist"
COMMIT = "37f6c456fe1a4775c875673fb8ef907d5cd2fd66"


def _utc(day, hour):
    """The expected UTC ISO spelling of ``day`` at ``hour``:00 — restated literally."""
    return f"{day}T{hour:02d}:00:00+00:00"


def opt(occ, day, **over):
    """One archive row with every published column; ``over`` replaces values."""
    root_len = len(occ) - 15
    yy, mm, dd = occ[root_len:root_len + 2], occ[root_len + 2:root_len + 4], \
        occ[root_len + 4:root_len + 6]
    right = occ[root_len + 6]
    row = {
        "contract_id": occ,
        "symbol": occ[:root_len],
        "expiration": f"20{yy}-{mm}-{dd}",
        "strike": round(int(occ[-8:]) / 1000.0, 2),
        "type": "call" if right == "C" else "put",
        "last": 1.25,
        "mark": 1.3,
        "bid": 1.2,
        "bid_size": 10,
        "ask": 1.4,
        "ask_size": 20,
        "volume": 7,
        "open_interest": 300,
        "date": day,
        "implied_volatility": 0.1875,
        "delta": 0.45,
        "gamma": 0.03,
        "theta": -0.12,
        "vega": 0.5,
        "rho": 0.02,
        "in_the_money": 0,
    }
    row.update(over)
    return row


def write_rows(path, rows, drop=()):
    """Write ``rows`` as one parquet file, minus the ``drop`` columns."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    table = pa.Table.from_pylist(rows)
    keep = [name for name in table.column_names if name not in drop]
    pq.write_table(table.select(keep), path)


def closes(symbol, pairs):
    """``underlying_prices`` rows for ``(date, close)`` pairs."""
    return [{"id": i, "symbol": symbol, "date": d, "open": c, "high": c, "low": c,
             "close": c, "adjusted_close": c, "volume": 1, "dividend_amount": 0.0,
             "split_coefficient": 1.0, "created_at": "2025-12-15 15:56:25"}
            for i, (d, c) in enumerate(pairs, start=1)]


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def pins(base, symbols=None):
    """Pin every parquet file under ``base`` (or under ``symbols``' directories) by sha256."""
    out = {}
    subs = sorted(os.listdir(base)) if symbols is None else [s.lower() for s in symbols]
    for sub in subs:
        for name in sorted(os.listdir(os.path.join(base, sub))):
            if name.endswith(".parquet"):
                out[f"{sub}/{name}"] = sha256(os.path.join(base, sub, name))
    return out


SPY_2024 = [
    opt("SPY250117P00450000", "2024-12-31"),
    opt("SPY250117C00450000", "2024-12-31"),
]
SPY_2025 = [
    # deliberately unsorted within and across dates
    opt("SPY250321C00600000", "2025-03-10"),
    opt("SPY250117C00600000", "2025-01-02", in_the_money=1),
    opt("SPY250117P00600000", "2025-01-02"),
    opt("SPY250321C00600000", "2025-03-07"),
    opt("SPY250919C00600000", "2025-07-01"),
]
QQQ_2025 = [
    # an adjusted contract: OCC strike 259.779, the column rounds it to 259.78
    opt("QQQ250117C00259779", "2025-01-02"),
    opt("QQQ250321P00500000", "2025-03-10"),
]


@pytest.fixture
def archive(tmp_path):
    """A two-symbol archive: SPY 2024+2025, QQQ 2025, and their closes."""
    base = tmp_path / "archive"
    write_rows(str(base / "spy" / "options_2024.parquet"), SPY_2024)
    write_rows(str(base / "spy" / "options_2025.parquet"), SPY_2025)
    write_rows(str(base / "spy" / "underlying_prices.parquet"), closes("SPY", [
        ("2024-12-31", 586.08), ("2025-01-02", 584.64), ("2025-03-07", 575.92),
        ("2025-03-10", 560.58), ("2025-07-01", 617.65)]))
    write_rows(str(base / "qqq" / "options_2025.parquet"), QQQ_2025)
    write_rows(str(base / "qqq" / "underlying_prices.parquet"), closes("QQQ", [
        ("2025-01-02", 510.23), ("2025-03-10", 466.23)]))
    return str(base)


def config(base, **over):
    symbols = over.get("symbols", ["SPY", "QQQ"])
    cfg = {"path": base, "symbols": symbols, "source_url": URL, "source_commit": COMMIT}
    if isinstance(symbols, list) and symbols and all(
            isinstance(s, str) and os.path.isdir(os.path.join(base, s.lower()))
            for s in symbols):
        cfg["files"] = pins(base, symbols)
    else:
        cfg["files"] = pins(base, ["SPY"])
    cfg.update(over)
    return cfg


def read(cfg, state=None, mode="backfill"):
    msgs = list(OptionsHistConnector().read(cfg, ["option_chain"], state or {}, mode))
    for msg in msgs:
        assert check_message(msg) is not None  # every message envelope-valid
    return msgs


def records(msgs):
    return [m for m in msgs if m["type"] == "RECORD"]


def cursor_of(msgs):
    return msgs[-1]["state"]["option_chain"]["cursor"]


# -- the contract surface ---------------------------------------------------------


def test_spec_passes_its_own_gate_and_denies_unknown_knobs(archive):
    conn = OptionsHistConnector()
    check_config(conn, config(archive))
    check_config(conn, config(archive, max_days=5))
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, config(archive, roots=["SPY"]))


def test_registered_kind_resolves():
    assert resolve_connector("optionshist") is OptionsHistConnector


def test_discover_declares_the_cboe_chain_stream(archive):
    assert OptionsHistConnector().discover(config(archive)) == [{
        "stream": "option_chain",
        "schema": {"fields": list(cboe.CHAIN_FIELDS)},
        "primary_key": list(cboe.CHAIN_KEY_FIELDS),
    }]


def test_check_accepts_the_pinned_archive(archive):
    OptionsHistConnector().check(config(archive))


def test_read_validates_its_arguments(archive):
    conn = OptionsHistConnector()
    cfg = config(archive)
    for streams, state, mode, match in [
        (["index_daily"], {}, "backfill", "unknown stream"),
        ([], {}, "backfill", "streams"),
        (["option_chain"], [], "backfill", "state"),
        (["option_chain"], {"option_chain": "x"}, "backfill", "state"),
        (["option_chain"], {}, "replay", "mode"),
    ]:
        with pytest.raises(AssetError, match=match):
            list(conn.read(cfg, streams, state, mode))


# -- field mapping ------------------------------------------------------------------


def test_row_maps_onto_the_cboe_chain_shape(archive):
    recs = records(read(config(archive)))
    first = recs[0]
    assert first["stream"] == "option_chain" and first["kind"] == "observation"
    assert first["data"] == {
        "underlying": "SPY",
        "option": "SPY250117C00450000",
        "root": "SPY",
        "expiry": "2025-01-17",
        "right": "call",
        "strike": 450.0,
        "bid": 1.2,
        "bid_size": 10.0,
        "ask": 1.4,
        "ask_size": 20.0,
        "iv": 0.1875,
        "open_interest": 300.0,
        "volume": 7.0,
        "delta": 0.45,
        "gamma": 0.03,
        "vega": 0.5,
        "theta": -0.12,
        "last_trade_price": 1.25,
        "last_trade_time": None,
        "underlying_price": 586.08,
        "quote_time": _utc("2024-12-31", 21),
    }
    # exactly the Cboe pack's 21 fields on every row — no mark, rho, in_the_money
    assert all(tuple(sorted(r["data"])) == tuple(sorted(cboe.CHAIN_FIELDS)) for r in recs)


def test_strike_root_expiry_right_come_from_the_occ_symbol(archive):
    recs = records(read(config(archive)))
    qqq = next(r["data"] for r in recs if r["data"]["option"] == "QQQ250117C00259779")
    assert (qqq["root"], qqq["expiry"], qqq["right"], qqq["strike"]) == \
        cboe.parse_occ("QQQ250117C00259779") == ("QQQ", "2025-01-17", "call", 259.779)
    assert qqq["underlying"] == "QQQ" and qqq["underlying_price"] == 510.23


def test_in_the_money_is_ignored(tmp_path, archive):
    flipped = str(tmp_path / "flipped")
    write_rows(os.path.join(flipped, "spy", "options_2024.parquet"),
               [dict(r, in_the_money=1 - r["in_the_money"]) for r in SPY_2024])
    write_rows(os.path.join(flipped, "spy", "options_2025.parquet"),
               [dict(r, in_the_money=1 - r["in_the_money"]) for r in SPY_2025])
    absent = str(tmp_path / "absent")
    write_rows(os.path.join(absent, "spy", "options_2024.parquet"), SPY_2024,
               drop=("in_the_money",))
    write_rows(os.path.join(absent, "spy", "options_2025.parquet"), SPY_2025,
               drop=("in_the_money",))
    for base in (flipped, absent):
        write_rows(os.path.join(base, "spy", "underlying_prices.parquet"),
                   pq.read_table(os.path.join(archive, "spy", "underlying_prices.parquet"))
                   .to_pylist())
    datas = [[r["data"] for r in records(read(config(b, symbols=["SPY"])))]
             for b in (archive, flipped, absent)]
    assert datas[0] == datas[1] == datas[2] and len(datas[0]) == 7


def test_missing_values_become_none_like_the_cboe_pack(tmp_path):
    base = str(tmp_path / "a")
    write_rows(os.path.join(base, "spy", "options_2025.parquet"), [
        opt("SPY250117C00600000", "2025-01-02", implied_volatility=None,
            bid=float("nan"), delta=float("inf"))])
    write_rows(os.path.join(base, "spy", "underlying_prices.parquet"),
               closes("SPY", [("2025-01-02", 584.64)]))
    data = records(read(config(base, symbols=["SPY"])))[0]["data"]
    assert data["iv"] is None and data["bid"] is None and data["delta"] is None


def test_a_date_without_an_underlying_close_keeps_the_rows_and_logs_it(tmp_path):
    base = str(tmp_path / "a")
    write_rows(os.path.join(base, "spy", "options_2024.parquet"), [
        opt("SPY240119C00470000", "2024-01-12"),
        opt("SPY240119C00470000", "2024-01-15"),  # a holiday row, no close that day
    ])
    write_rows(os.path.join(base, "spy", "underlying_prices.parquet"),
               closes("SPY", [("2024-01-12", 476.68)]))
    msgs = read(config(base, symbols=["SPY"]))
    recs = records(msgs)
    assert [r["data"]["underlying_price"] for r in recs] == [476.68, None]
    logs = [m for m in msgs if m["type"] == "LOG"]
    assert any("2024-01-15" in m["message"] and "SPY" in m["message"] for m in logs)


# -- the clock ----------------------------------------------------------------------


@pytest.mark.parametrize("day, hour", [
    ("2024-12-31", 21),  # EST
    ("2025-01-02", 21),  # EST
    ("2025-03-07", 21),  # last Friday of EST
    ("2025-03-10", 20),  # first Monday of EDT (spring forward 2025-03-09)
    ("2025-07-01", 20),  # EDT
])
def test_quote_time_is_the_new_york_close_in_utc_across_dst(archive, day, hour):
    recs = [r for r in records(read(config(archive))) if r["data"]["quote_time"][:10] == day]
    assert recs
    for rec in recs:
        assert rec["data"]["quote_time"] == _utc(day, hour)
        assert rec["effective_date"] == rec["data"]["quote_time"]
        # independent check through zoneinfo
        local = datetime.fromisoformat(rec["effective_date"]).astimezone(
            ZoneInfo("America/New_York"))
        assert (local.date(), local.hour, local.minute) == (date.fromisoformat(day), 16, 0)


def test_fall_back_close_is_eastern_standard(tmp_path):
    base = str(tmp_path / "a")
    write_rows(os.path.join(base, "spy", "options_2024.parquet"), [
        opt("SPY241115C00570000", "2024-11-01"),  # EDT
        opt("SPY241115C00570000", "2024-11-04"),  # EST (fall back 2024-11-03)
    ])
    write_rows(os.path.join(base, "spy", "underlying_prices.parquet"),
               closes("SPY", [("2024-11-01", 571.04), ("2024-11-04", 569.81)]))
    assert [r["effective_date"] for r in records(read(config(base, symbols=["SPY"])))] == \
        [_utc("2024-11-01", 20), _utc("2024-11-04", 21)]


# -- fail closed: columns --------------------------------------------------------------


@pytest.mark.parametrize("column", OPTION_COLUMNS)
def test_a_missing_option_column_refuses_before_any_record(tmp_path, column):
    base = str(tmp_path / "a")
    write_rows(os.path.join(base, "spy", "options_2025.parquet"), SPY_2025, drop=(column,))
    write_rows(os.path.join(base, "spy", "underlying_prices.parquet"),
               closes("SPY", [("2025-01-02", 584.64)]))
    cfg = config(base, symbols=["SPY"])
    with pytest.raises(AssetError, match=column):
        OptionsHistConnector().check(cfg)
    emitted = []
    with pytest.raises(AssetError, match=column):
        for msg in OptionsHistConnector().read(cfg, ["option_chain"], {}, "backfill"):
            emitted.append(msg)
    assert not records(emitted)


@pytest.mark.parametrize("column", UNDERLYING_COLUMNS)
def test_a_missing_underlying_column_refuses(tmp_path, column):
    base = str(tmp_path / "a")
    write_rows(os.path.join(base, "spy", "options_2025.parquet"), SPY_2025)
    write_rows(os.path.join(base, "spy", "underlying_prices.parquet"),
               closes("SPY", [("2025-01-02", 584.64)]), drop=(column,))
    cfg = config(base, symbols=["SPY"])
    with pytest.raises(AssetError, match=column):
        OptionsHistConnector().check(cfg)
    with pytest.raises(AssetError, match=column):
        read(cfg)


def test_the_required_columns_are_the_mapped_ones():
    # restated on purpose: the columns the mapping reads, and never in_the_money
    assert set(OPTION_COLUMNS) == {
        "contract_id", "symbol", "expiration", "strike", "type", "last", "bid",
        "bid_size", "ask", "ask_size", "volume", "open_interest", "date",
        "implied_volatility", "delta", "gamma", "theta", "vega"}
    assert set(UNDERLYING_COLUMNS) == {"symbol", "date", "close"}


# -- fail closed: provenance -------------------------------------------------------------


def test_a_changed_file_refuses_by_hash(archive):
    cfg = config(archive)
    write_rows(os.path.join(archive, "spy", "options_2025.parquet"),
               SPY_2025 + [opt("SPY250117C00610000", "2025-01-02")])
    with pytest.raises(AssetError, match="spy/options_2025.parquet.*sha256"):
        read(cfg)


def test_an_unpinned_file_refuses(archive):
    cfg = config(archive)
    write_rows(os.path.join(archive, "spy", "options_2026.parquet"),
               [opt("SPY260116C00600000", "2026-01-02")])
    for call in (lambda: OptionsHistConnector().check(cfg), lambda: read(cfg)):
        with pytest.raises(AssetError, match="spy/options_2026.parquet.*not pinned"):
            call()


def test_a_pinned_file_that_is_missing_refuses(archive):
    cfg = config(archive)
    os.remove(os.path.join(archive, "qqq", "options_2025.parquet"))
    for call in (lambda: OptionsHistConnector().check(cfg), lambda: read(cfg)):
        with pytest.raises(AssetError, match="qqq/options_2025.parquet"):
            call()


@pytest.mark.parametrize("over, match", [
    ({"files": {}}, "files"),
    ({"files": {"spy/options_2025.parquet": "abc"}}, "sha256"),
    ({"files": {"../spy/options_2025.parquet": "0" * 64}}, "files"),
    ({"source_commit": "main"}, "source_commit"),
    ({"source_url": ""}, "source_url"),
    ({"source_url": "ftp://x"}, "source_url"),
    ({"symbols": []}, "symbols"),
    ({"symbols": ["spy"]}, "symbols"),
    ({"symbols": ["SPY", "SPY"]}, "symbols"),
    ({"max_days": 0}, "max_days"),
    ({"max_days": True}, "max_days"),
    ({"max_days": 2.0}, "max_days"),
    ({"path": ""}, "path"),
])
def test_bad_knobs_refuse(archive, over, match):
    with pytest.raises(AssetError, match=match):
        OptionsHistConnector().check(config(archive, **over))


def test_a_pin_outside_the_declared_symbols_refuses(archive):
    cfg = config(archive, symbols=["SPY"], files=pins(archive))  # pins name the qqq files
    with pytest.raises(AssetError, match="qqq/options_2025.parquet"):
        OptionsHistConnector().check(cfg)


def test_the_provenance_is_logged(archive):
    logs = [m["message"] for m in read(config(archive)) if m["type"] == "LOG"]
    assert any(URL in m and COMMIT in m for m in logs)


# -- fail closed: row integrity ------------------------------------------------------------


@pytest.mark.parametrize("bad, match", [
    (opt("SPY250117C00600000", "2025-01-02", symbol="QQQ"), "symbol"),
    (opt("SPY250117C00600000", "2025-01-02", expiration="2025-01-18"), "expiration"),
    (opt("SPY250117C00600000", "2025-01-02", type="put"), "type"),
    (opt("SPY250117C00600000", "2025-01-02", strike=600.01), "strike"),
    (opt("SPY250117C00600000", "2025-01-02", contract_id="SPY 250117C600"), "OCC"),
    (opt("SPY250117C00600000", "2024-01-02"), "year"),
    (opt("SPY250117C00600000", "01/02/2025"), "date"),
    (opt("SPY250117C00600000", None), "date"),
])
def test_an_inconsistent_row_refuses(tmp_path, bad, match):
    base = str(tmp_path / "a")
    write_rows(os.path.join(base, "spy", "options_2025.parquet"),
               [opt("SPY250117P00600000", "2025-01-02"), bad])
    write_rows(os.path.join(base, "spy", "underlying_prices.parquet"),
               closes("SPY", [("2025-01-02", 584.64)]))
    with pytest.raises(AssetError, match=match):
        read(config(base, symbols=["SPY"]))


def test_a_repeated_contract_day_refuses(tmp_path):
    base = str(tmp_path / "a")
    write_rows(os.path.join(base, "spy", "options_2025.parquet"),
               [opt("SPY250117C00600000", "2025-01-02"),
                opt("SPY250117C00600000", "2025-01-02", bid=1.1)])
    write_rows(os.path.join(base, "spy", "underlying_prices.parquet"),
               closes("SPY", [("2025-01-02", 584.64)]))
    with pytest.raises(AssetError, match="SPY250117C00600000.*repeat"):
        read(config(base, symbols=["SPY"]))


@pytest.mark.parametrize("rows, match", [
    (closes("SPY", [("2025-01-02", 584.64), ("2025-01-02", 584.7)]), "repeat"),
    (closes("QQQ", [("2025-01-02", 584.64)]), "symbol"),
    (closes("SPY", [("2025-01-02", float("nan"))]), "close"),
])
def test_a_bad_underlying_file_refuses(tmp_path, rows, match):
    base = str(tmp_path / "a")
    write_rows(os.path.join(base, "spy", "options_2025.parquet"),
               [opt("SPY250117C00600000", "2025-01-02")])
    write_rows(os.path.join(base, "spy", "underlying_prices.parquet"), rows)
    with pytest.raises(AssetError, match=match):
        read(config(base, symbols=["SPY"]))


# -- cursor / backfill ---------------------------------------------------------------------


ALL_KEYS = [
    ("2024-12-31", "SPY", "SPY250117C00450000"),
    ("2024-12-31", "SPY", "SPY250117P00450000"),
    ("2025-01-02", "QQQ", "QQQ250117C00259779"),
    ("2025-01-02", "SPY", "SPY250117C00600000"),
    ("2025-01-02", "SPY", "SPY250117P00600000"),
    ("2025-03-07", "SPY", "SPY250321C00600000"),
    ("2025-03-10", "QQQ", "QQQ250321P00500000"),
    ("2025-03-10", "SPY", "SPY250321C00600000"),
    ("2025-07-01", "SPY", "SPY250919C00600000"),
]


def _keys(recs):
    return [(r["data"]["quote_time"][:10], r["data"]["underlying"], r["data"]["option"])
            for r in recs]


@pytest.mark.parametrize("mode", ["backfill", "live"])
def test_first_pull_emits_everything_in_date_underlying_option_order(archive, mode):
    msgs = read(config(archive), mode=mode)
    assert msgs[0]["type"] == "SCHEMA" and msgs[-1]["type"] == "STATE"
    assert _keys(records(msgs)) == ALL_KEYS
    assert cursor_of(msgs) == _utc("2025-07-01", 20)


@pytest.mark.parametrize("mode", ["backfill", "live"])
def test_a_pull_emits_only_dates_after_the_cursor(archive, mode):
    state = {"option_chain": {"cursor": _utc("2025-01-02", 21)}, "other": {"x": 1}}
    msgs = read(config(archive), state=state, mode=mode)
    assert _keys(records(msgs)) == ALL_KEYS[5:]
    assert msgs[-1]["state"] == {"option_chain": {"cursor": _utc("2025-07-01", 20)},
                                 "other": {"x": 1}}


def test_nothing_new_keeps_the_cursor(archive):
    state = {"option_chain": {"cursor": _utc("2025-07-01", 20)}}
    msgs = read(config(archive), state=state)
    assert not records(msgs) and cursor_of(msgs) == _utc("2025-07-01", 20)


def test_max_days_walks_forward_in_whole_days_without_gaps_or_repeats(archive):
    cfg = config(archive, max_days=2)
    state, seen, pulls = {}, [], []
    while True:
        msgs = read(cfg, state=state)
        recs = records(msgs)
        if not recs:
            break
        pulls.append(sorted({k[0] for k in _keys(recs)}))
        seen.extend(_keys(recs))
        state = msgs[-1]["state"]
    assert pulls == [["2024-12-31", "2025-01-02"], ["2025-03-07", "2025-03-10"],
                     ["2025-07-01"]]
    assert seen == ALL_KEYS


def test_a_cursor_at_a_years_last_close_never_reads_that_year(archive):
    # the 2024 file is not parquet (pinned by its true hash): a pull whose cursor
    # is 2024's last possible close must never open it
    cfg = config(archive)
    with open(os.path.join(archive, "spy", "options_2024.parquet"), "wb") as fh:
        fh.write(b"not parquet")
    cfg["files"]["spy/options_2024.parquet"] = sha256(
        os.path.join(archive, "spy", "options_2024.parquet"))
    msgs = read(cfg, state={"option_chain": {"cursor": _utc("2024-12-31", 21)}})
    assert _keys(records(msgs)) == ALL_KEYS[2:]


def test_a_malformed_cursor_refuses(archive):
    with pytest.raises(AssetError):
        read(config(archive), state={"option_chain": {"cursor": "not a date"}})


# -- end to end through the platform --------------------------------------------------------


@pytest.fixture
def hist_source(registry, archive):
    vid = registry.register("source_config", {
        "name": "optionshist",
        "catalog_source": "optionshist-src",
        "connector": "optionshist",
        "config": dict(config(archive, max_days=3),
                       storage={"payload_codec": "gzip", "observations_codec": "gzip"}),
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    return vid


def test_backfill_lands_the_chain_in_the_store_in_bounded_pulls(root, registry, hist_source):
    first = run_acquisition(root, registry, "optionshist", "option_chain", "backfill")
    assert first["records"] == 6 and first["state_saved"]
    assert load_state(root, "optionshist", "option_chain", "backfill") == {
        "option_chain": {"cursor": _utc("2025-03-07", 21)}}
    second = run_acquisition(root, registry, "optionshist", "option_chain", "backfill")
    assert second["records"] == 3
    third = run_acquisition(root, registry, "optionshist", "option_chain", "backfill")
    assert third["records"] == 0
    rows = scan_stream(root.root, "optionshist", "option_chain",
                       key_fields=cboe.CHAIN_KEY_FIELDS)
    assert sorted((r["quote_time"][:10], r["underlying"], r["option"]) for r in rows) == \
        ALL_KEYS
    assert {r["quote_time"] for r in rows} >= {_utc("2025-03-10", 20), _utc("2024-12-31", 21)}
