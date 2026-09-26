"""Thin projections keep the parent's reader and refuse unsupported domain rows."""

import pytest

from index_options.observations import ContractRows, QuoteRows, SettlementRows


@pytest.mark.parametrize("cls", [ContractRows, QuoteRows, SettlementRows])
def test_knobs_are_narrowed_and_serving_refused(cls):
    assert cls._PARAMS == ("root", "source", "as_of_acquisition_ms")
    assert cls.serving_effect({}, {}) == "forbidden"

import copy
import json
import os
from datetime import datetime, timedelta, timezone

from dskit.onboarding import AssetError, load_suite, run_suite
from dskit.pipeline.base import ConfigError


@pytest.mark.parametrize("cls, stream, count, keys", [
    (ContractRows, "contracts", 4, ("corpus_id", "contract_id", "row_version")),
    (QuoteRows, "quotes", 4, ("corpus_id", "contract_id", "effective_at", "row_version")),
    (SettlementRows, "settlements", 3, ("corpus_id", "settlement_id", "expiry", "row_version")),
])
def test_accessors_and_real_projection(rows, store_factory, cls, stream, count, keys):
    store = store_factory(rows)
    acquired = store.acquire(stream)
    assert acquired["records"] == count
    node = cls(stream, store.node_params())
    assert node.stream() == stream
    assert node.key_fields() == keys
    assert node.ts_field() == "effective_at"
    assert node.ts_unit() == "iso"
    assert node.ts_out() == "observation_ms"
    assert node.shared_fields() == ()
    assert node.since_ms() is None
    result = node.run(None, {})["records"]
    assert len(result) == count
    assert all(type(row["observation_ms"]) is int for row in result)
    assert all(row["known_at_basis"] == "synthetic" for row in result)
    assert len({tuple(row[k] for k in keys) for row in rows[stream]}) == count


@pytest.mark.parametrize("cls", [ContractRows, QuoteRows, SettlementRows])
@pytest.mark.parametrize("knob", [
    "stream", "key_fields", "ts_field", "ts_unit", "ts_out", "shared_fields", "since_ms", "surprise",
])
def test_fixed_or_unknown_knobs_refuse(cls, knob, tmp_path):
    with pytest.raises(ConfigError, match=knob):
        cls("rows", {"root": str(tmp_path), "source": "index-fixture", knob: "wrong"})


@pytest.mark.parametrize("cls, stream", [
    (ContractRows, "contracts"), (QuoteRows, "quotes"), (SettlementRows, "settlements"),
])
@pytest.mark.parametrize("field, value", [
    ("provenance", "real"), ("known_at_basis", "guessed"),
    ("known_at", "2026-01-01"), ("schema_version", "unknown"),
])
def test_domain_projection_refuses_all_stream_families(rows, cls, stream, field, value):
    rows[stream][0][field] = value
    node = cls(stream, {"root": ".", "source": "index-fixture"})
    with pytest.raises(ValueError):
        node.project(rows[stream])


@pytest.mark.parametrize("multiplier", [0, -100, True])
def test_contract_projection_refuses_nonpositive_or_bool_multiplier(rows, multiplier):
    rows["contracts"][0]["multiplier"] = multiplier
    with pytest.raises(ValueError, match="multiplier"):
        ContractRows("contracts", {"root": ".", "source": "index-fixture"}).project(rows["contracts"])


@pytest.mark.parametrize("cls, stream", [
    (ContractRows, "contracts"), (QuoteRows, "quotes"), (SettlementRows, "settlements"),
])
def test_parent_refuses_existing_output_timestamp(rows, store_factory, cls, stream):
    rows[stream][0]["observation_ms"] = 1
    store = store_factory(rows)
    store.acquire(stream)
    with pytest.raises(AssetError, match="observation_ms"):
        cls(stream, store.node_params()).fingerprint()


def test_parent_identical_repeat_and_conflicting_winning_tie(rows, store_factory):
    rows["quotes"].append(copy.deepcopy(rows["quotes"][0]))
    store = store_factory(rows)
    store.acquire("quotes")
    assert len(QuoteRows("q", store.node_params()).run(None, {})["records"]) == 4
    rows["quotes"][-1]["ask"] = "2.61"
    conflicting = store_factory(rows, "conflicting")
    conflicting.acquire("quotes")
    with pytest.raises(AssetError):
        QuoteRows("q", conflicting.node_params()).fingerprint()


def test_read_vintage_and_later_winner_do_not_rewrite_known_at(rows, store_factory, monkeypatch):
    import dskit.onboarding.acquire as acquisition

    store = store_factory(rows)
    monkeypatch.setattr(acquisition, "utc_now", lambda: "2026-06-01T00:00:00Z")
    store.acquire("quotes")
    first = QuoteRows("q", store.node_params())
    before = first.fingerprint()
    assert next(r for r in first.run(None, {})["records"] if r["contract_id"] == "p480")["ask"] == "2.60"
    rows["quotes"][0]["ask"] = "2.61"
    store.write("quotes", rows["quotes"])
    monkeypatch.setattr(acquisition, "utc_now", lambda: "2026-06-02T00:00:00Z")
    store.acquire("quotes", "live")  # separate parent cursor, not backdated resume capture
    midpoint = (datetime(2026, 6, 1, 12, tzinfo=timezone.utc) -
                datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(milliseconds=1)
    old = QuoteRows("q", store.node_params(as_of_acquisition_ms=midpoint)).run(None, {})["records"]
    new_node = QuoteRows("q", store.node_params())
    new = new_node.run(None, {})["records"]
    assert next(r for r in old if r["contract_id"] == "p480")["ask"] == "2.60"
    assert next(r for r in new if r["contract_id"] == "p480")["ask"] == "2.61"
    assert {r["known_at"] for r in new} == {"2026-01-16T20:45:00Z"}
    assert first.fingerprint() == before
    assert new_node.fingerprint() != before
    # Event/known_at precede this cutoff, but actual ingestion does not.
    assert QuoteRows("q", store.node_params(as_of_acquisition_ms=midpoint - 86400000)).run(
        None, {}
    )["records"] == []


def test_snapshot_is_frozen_but_fresh_instance_hashes_changed_bytes(rows, store_factory):
    store = store_factory(rows)
    store.acquire("quotes")
    node = QuoteRows("q", store.node_params())
    fingerprint = node.fingerprint()
    frozen = copy.deepcopy(node.run(None, {})["records"])
    paths = store.members("quotes")
    assert len(paths) == 1
    path = paths[0]
    stat = path.stat()
    envelopes = [json.loads(line) for line in path.read_text().splitlines()]
    envelopes[0]["data"]["ask"] = "2.61"
    path.write_text("".join(json.dumps(row) + "\n" for row in envelopes))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert node.fingerprint() == fingerprint
    assert node.run(None, {})["records"] == frozen
    assert QuoteRows("q", store.node_params()).fingerprint() != fingerprint


def test_revisions_stay_distinct_and_strict_cursor_is_not_revision_capture(rows, store_factory):
    rows["quotes"].append({**rows["quotes"][0], "row_version": "v2", "ask": "2.61"})
    store = store_factory(rows)
    store.acquire("quotes")
    output = QuoteRows("q", store.node_params()).run(None, {})["records"]
    assert len(output) == 5
    rows["quotes"].append({**rows["quotes"][0], "row_version": "v3"})
    store.write("quotes", rows["quotes"])
    assert store.acquire("quotes")["records"] == 0
    assert len(QuoteRows("q", store.node_params()).run(None, {})["records"]) == 5


def test_suite_block_is_not_a_hidden_read_gate(rows, store_factory, child_root, monkeypatch):
    import dskit.onboarding.acquire as acquisition

    original = copy.deepcopy(rows["quotes"])
    rows["quotes"][0]["provenance"] = "unknown"
    store = store_factory(rows)
    monkeypatch.setattr(acquisition, "utc_now", lambda: "2026-06-01T00:00:00Z")
    acquired = store.acquire("quotes")
    suite = load_suite(str(child_root / "configs/suite-fixture.json"))
    assert run_suite(store.root, store.registry, suite, acquired["snapshot"])["gating"] == "block"
    with pytest.raises(ValueError, match="synthetic"):
        QuoteRows("q", store.node_params()).fingerprint()
    store.write("quotes", original)
    monkeypatch.setattr(acquisition, "utc_now", lambda: "2026-06-02T00:00:00Z")
    store.acquire("quotes", "live")
    # No suite was run on the new snapshot: valid winning rows read anyway.
    assert len(QuoteRows("q", store.node_params()).run(None, {})["records"]) == 4


def test_ordinary_subclass_inherits_domain_rules(rows, store_factory):
    class ResearchQuotes(QuoteRows):
        pass

    store = store_factory(rows)
    store.acquire("quotes")
    assert len(ResearchQuotes("q", store.node_params()).run(None, {})["records"]) == 4
    rows["quotes"][0]["condition_valid"] = False
    with pytest.raises(ValueError):
        ResearchQuotes("q", store.node_params()).project(rows["quotes"])

@pytest.mark.parametrize("cls, stream, wrong, equivalent", [
    (QuoteRows, "quotes", "2026-01-15T20:45:00Z", "2026-01-16T15:45:00-05:00"),
    (SettlementRows, "settlements", "2026-02-19T21:00:00Z", "2026-02-20T16:00:00-05:00"),
])
def test_projection_pins_own_clock_without_cross_leg_checks(rows, cls, stream, wrong, equivalent):
    node = cls("rows", {"root": ".", "source": "index-fixture"})
    rows[stream][0]["effective_at"] = wrong
    with pytest.raises(ValueError, match="must equal effective_at"):
        node.project(rows[stream])
    rows[stream][0]["effective_at"] = equivalent
    assert len(node.project(rows[stream])) == len(rows[stream])


# -- ADR-0187: ChainQuoteRows, the bounded archived-chain reader ----------------------------

from datetime import time  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from dskit.onboarding.libs.cboe import CHAIN_FIELDS, parse_occ  # noqa: E402

from index_options.observations import ChainQuoteRows  # noqa: E402


def _close_utc(day):
    """The archive's 16:00 New York close of ``day`` in UTC, as the pack spells it."""
    local = datetime.combine(datetime.fromisoformat(day).date(), time(16, 0),
                             tzinfo=ZoneInfo("America/New_York"))
    return local.astimezone(timezone.utc).isoformat()


def _chain(option, day, underlying_price, underlying=None, root=None, **over):
    occ_root, expiry, right, strike = parse_occ(option)
    row = {"underlying": underlying or occ_root, "option": option, "root": root or occ_root,
           "expiry": expiry, "right": right, "strike": strike, "bid": 1.0, "bid_size": 10,
           "ask": 1.2, "ask_size": 12, "iv": 0.2, "open_interest": 5, "volume": 1,
           "delta": None, "gamma": None, "vega": None, "theta": None,
           "last_trade_price": None, "last_trade_time": None,
           "underlying_price": underlying_price, "quote_time": _close_utc(day)}
    row.update(over)
    assert set(row) == set(CHAIN_FIELDS)
    return row


CHAIN_PARAMS = {"symbol": "SPY", "dte_min": 30, "dte_max": 45, "max_abs_log_moneyness": 0.2}

#: Quote date 2025-01-02 (a Thursday), SPY at 100: one row per intake rule.
CHAIN_ROWS = [
    _chain("SPY250207C00100000", "2025-01-02", 100.0),                    # kept: Fri Feb 7, DTE 36
    _chain("SPY250208C00100000", "2025-01-02", 100.0),                    # kept: SATURDAY expiry -> Fri Feb 7
    _chain("SPY250207P00099500", "2025-01-02", 100.0),                    # kept: on the 0.5 grid
    _chain("SPY250207P00082000", "2025-01-02", 100.0),                    # kept: ln(0.82) = -0.198
    _chain("SPYW250207C00100000", "2025-01-02", 100.0, underlying="SPY"),  # root != symbol
    _chain("SPY250207C00100250", "2025-01-02", 100.0),                    # off the 0.5 grid
    _chain("SPY250131C00100000", "2025-01-02", 100.0),                    # DTE 29 < 30
    _chain("SPY250221C00100000", "2025-01-02", 100.0),                    # DTE 50 > 45
    _chain("SPY250207C00125000", "2025-01-02", 100.0),                    # ln(1.25) = 0.223 > band
    _chain("SPY250207C00101000", "2025-01-02", None),                     # no underlying close
    _chain("QQQ250207C00100000", "2025-01-02", 100.0),                    # another underlying
    _chain("SPY250314C00100000", "2025-02-03", 100.0),                    # kept: a second date, DTE 39
]


def _chain_store(store_factory, rows=CHAIN_ROWS, name="chain"):
    store = store_factory({"option_chain": rows}, name, source="optionshist-chain",
                          effective_field="quote_time")
    assert store.acquire("option_chain")["records"] == len(rows)
    return store


def test_chain_reader_knobs_are_narrowed_and_serving_refused(tmp_path):
    assert ChainQuoteRows._PARAMS == ("root", "source", "since_ms", "as_of_acquisition_ms",
                                      "symbol", "dte_min", "dte_max", "max_abs_log_moneyness")
    assert ChainQuoteRows.serving_effect({}, {}) == "forbidden"
    assert ChainQuoteRows.reuse_snapshot is True
    base = {"root": str(tmp_path), "source": "optionshist-chain", **CHAIN_PARAMS}
    node = ChainQuoteRows("chain", base)
    assert (node.stream(), node.key_fields(), node.ts_field(), node.ts_unit(), node.ts_out()) == \
        ("option_chain", ("option", "quote_time"), "quote_time", "iso", "asof_ms")
    assert node.keep_values() == {"underlying": ["SPY"]}
    for knob, value in [("stream", "x"), ("key_fields", ["option"]), ("ts_field", "x"),
                        ("shared_fields", []), ("symbol", ""), ("symbol", None),
                        ("dte_min", 0), ("dte_min", 46), ("dte_max", 2.5), ("dte_max", True),
                        ("max_abs_log_moneyness", 0), ("max_abs_log_moneyness", "0.2"),
                        ("surprise", 1)]:
        with pytest.raises(ConfigError, match=knob):
            ChainQuoteRows("chain", {**base, knob: value})
    for missing in CHAIN_PARAMS:
        with pytest.raises(ConfigError, match=missing):
            ChainQuoteRows("chain", {k: v for k, v in base.items() if k != missing})


def test_chain_intake_keeps_only_the_cells_rows_and_projects_fresh_envelopes(store_factory):
    store = _chain_store(store_factory)
    node = ChainQuoteRows("chain", store.node_params(**CHAIN_PARAMS))
    out = node.run(None, {})["records"]
    # the seam's order: instant, then the OCC symbol (calls sort before puts)
    assert [(r["date"], r["expiry"], r["settle_date"], r["dte"], r["right"], r["strike"])
            for r in out] == [
        ("2025-01-02", "2025-02-07", "2025-02-07", 36, "call", 100.0),
        ("2025-01-02", "2025-02-07", "2025-02-07", 36, "put", 82.0),
        ("2025-01-02", "2025-02-07", "2025-02-07", 36, "put", 99.5),
        ("2025-01-02", "2025-02-08", "2025-02-07", 36, "call", 100.0),
        ("2025-02-03", "2025-03-14", "2025-03-14", 39, "call", 100.0),
    ]
    assert set(out[0]) == {"instrument", "date", "expiry", "settle_date", "dte", "right",
                           "strike", "bid", "ask", "bid_size", "ask_size", "iv",
                           "underlying_price", "asof_ms"}
    assert out[0]["instrument"] == "SPY" and out[0]["underlying_price"] == 100.0
    assert (out[0]["bid"], out[0]["ask"], out[0]["bid_size"], out[0]["ask_size"],
            out[0]["iv"]) == (1.0, 1.2, 10, 12, 0.2)
    assert type(out[0]["asof_ms"]) is int
    assert node.fingerprint()["rows"] == 5
    # the projection is fresh per instance: a consumer's edit reaches no other reader
    out[0]["bid"] = -1.0
    assert ChainQuoteRows("chain", store.node_params(**CHAIN_PARAMS)).run(None, {})["records"][0]["bid"] == 1.0


def test_chain_reader_scans_the_store_once_per_process(store_factory, monkeypatch):
    import dskit.onboarding.observations as seam

    store = _chain_store(store_factory)
    real, calls = seam.scan_stream, []

    def counting(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(seam, "scan_stream", counting)
    first = ChainQuoteRows("chain", store.node_params(**CHAIN_PARAMS))
    second = ChainQuoteRows("chain", store.node_params(**CHAIN_PARAMS))
    assert first.fingerprint() == second.fingerprint() and len(calls) == 1
    assert calls[0]["keep_values"] == {"underlying": ["SPY"]} and callable(calls[0]["admit"])
    assert first.run(None, {})["records"] == second.run(None, {})["records"]
    # a different bucket is a different snapshot
    wider = ChainQuoteRows("chain", store.node_params(**dict(CHAIN_PARAMS, dte_max=60)))
    assert wider.fingerprint()["rows"] == 6 and len(calls) == 2


def test_chain_reader_drops_a_zero_dte_and_reads_a_second_symbol_by_name(store_factory):
    same_day = [_chain("SPY250102C00100000", "2025-01-02", 100.0),
                _chain("SPY250103C00100000", "2025-01-02", 100.0),
                _chain("QQQ250103C00100000", "2025-01-02", 100.0)]
    store = _chain_store(store_factory, same_day, name="short")
    params = store.node_params(**dict(CHAIN_PARAMS, dte_min=1, dte_max=3))
    spy = ChainQuoteRows("chain", params).run(None, {})["records"]
    assert [(r["expiry"], r["dte"]) for r in spy] == [("2025-01-03", 1)]
    qqq = ChainQuoteRows("chain", dict(params, symbol="QQQ")).run(None, {})["records"]
    assert [(r["instrument"], r["expiry"]) for r in qqq] == [("QQQ", "2025-01-03")]
