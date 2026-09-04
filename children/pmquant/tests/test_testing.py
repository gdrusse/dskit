"""The synthetic ladder world and its connector double: deterministic,
partition-consistent, parent-shaped on disk, and acquirable end to end.

The world is what every other test in this child stands on, so its own
invariants are pinned here independently of the nodes that read it.
"""

import json
import os

import pytest

from dskit.onboarding import (
    AssetError,
    check_config,
    check_message,
    scan_stream,
)

from pmquant import testing
from pmquant.books import ladders_from_book_json
from pmquant.ladder.protocols import (
    DEFAULT_DUR_CAPS_H,
    DEFAULT_MIN_ABS_LEAD_S,
    LEAD_FRACS,
    LeadGrid,
    venue_of,
)
from pmquant.nodes_data import (
    MARKET_KEY_FIELDS,
    MARKETS_STREAM,
    PIT_KEY_FIELDS,
    PIT_STREAM,
)
from pmquant.testing import (
    DEFAULT_KNOBS,
    MARKET_COLUMNS,
    PIT_FIELDS,
    SyntheticLadderConnector,
    SyntheticLadderWorld,
    acquire_synthetic,
    resolve_knobs,
    world_problems,
    write_parent_store,
)

#: A small world for the fast checks — two venues, few events.
SMALL = {"series": ["KXSYNA", "POLYSYNC"], "events_per_series": 4, "rungs": 3}


def _rows(world):
    pit = [row for s in world.series for row in world.pit_rows(s)]
    markets = [row for s in world.series for row in world.market_rows(s)]
    return pit, markets


# -- determinism ------------------------------------------------------------


def test_two_worlds_with_one_seed_are_one_world():
    a, b = SyntheticLadderWorld(seed=3, **SMALL), SyntheticLadderWorld(seed=3, **SMALL)
    assert _rows(a) == _rows(b)
    assert a.outcomes() == b.outcomes()
    c = SyntheticLadderWorld(seed=4, **SMALL)
    assert _rows(c) != _rows(a)


def test_the_defaults_have_one_name(monkeypatch):
    """``DEFAULT_KNOBS`` is what ``resolve_knobs``, the world's signature
    and ``spec()``'s notes all read — rebinding the table must move the
    connector's view; the world's own signature reads the same constants."""
    monkeypatch.setitem(testing.DEFAULT_KNOBS, "rungs", 5)
    assert resolve_knobs({})["rungs"] == 5
    assert "default 5." in SyntheticLadderConnector().spec()["params"]["rungs"]["notes"]
    assert set(DEFAULT_KNOBS) == {
        "seed", "series", "events_per_series", "rungs", "start_date",
        "shrink", "spread", "one_sided_rate",
    }


# -- the ladder invariants ---------------------------------------------------


def test_every_event_settles_exactly_one_rung_yes():
    world = SyntheticLadderWorld(**SMALL)
    _pit, markets = _rows(world)
    by_event = {}
    for row in markets:
        by_event.setdefault(row["event_ticker"], []).append(row)
    assert len(by_event) == 2 * SMALL["events_per_series"]
    for event, rows in by_event.items():
        assert [r["result"] for r in rows].count("yes") == 1, event
        assert [r["strike_type"] for r in rows] == ["less", "between", "greater"]
        assert rows[0]["floor_strike"] is None and rows[-1]["cap_strike"] is None
        assert rows[0]["cap_strike"] == rows[1]["floor_strike"]
        assert rows[1]["cap_strike"] == rows[2]["floor_strike"]
        assert all(r["status"] and r["result"] in ("yes", "no") for r in rows)
    outcomes = world.outcomes()
    assert outcomes == {r["ticker"]: r["result"] == "yes" for r in markets}


def test_every_contract_has_21_leads_and_one_settle_row():
    world = SyntheticLadderWorld(**SMALL)
    grid = LeadGrid(LEAD_FRACS, DEFAULT_DUR_CAPS_H, DEFAULT_MIN_ABS_LEAD_S)
    for series in world.series:
        rows = world.pit_rows(series)
        assert rows == sorted(rows, key=lambda r: (r["epoch_ts_ms"], r["contract_ticker"]))
        for event in world.events(series):
            open_ms, close_ms = world.open_ms(event), world.close_ms(event)
            assert close_ms - open_ms == testing.EVENT_LIFE_H * 3_600_000
            expected = grid.epochs(open_ms, close_ms)
            assert len(expected) == 21
            for contract in world.contracts(event):
                mine = [r for r in rows if r["contract_ticker"] == contract]
                leads = [r for r in mine if r["kind"] == "lead"]
                settles = [r for r in mine if r["kind"] == "settle"]
                assert [(r["lead_frac"], r["epoch_ts_ms"]) for r in leads] == expected
                assert len(settles) == 1
                (settle,) = settles
                assert settle["epoch_ts_ms"] == close_ms
                assert settle["lead_frac"] is None and settle["book_json"] is None
                assert settle["reason"] == "settle"
                assert not (settle["admissible"] or settle["quality_ok"] or settle["usable"])
                for row in leads:
                    assert sorted(row) == list(PIT_FIELDS)
                    assert row["chosen_ts_ms"] == row["epoch_ts_ms"] - row["staleness_ms"]


def test_one_sided_rows_are_unusable_and_the_rate_is_a_knob():
    none = SyntheticLadderWorld(one_sided_rate=0.0, **SMALL)
    every = SyntheticLadderWorld(one_sided_rate=1.0, **SMALL)
    some = SyntheticLadderWorld(one_sided_rate=0.3, **SMALL)
    for world, expect_one_sided in ((none, {False}), (every, {True})):
        leads = [r for r in _rows(world)[0] if r["kind"] == "lead"]
        assert {not r["usable"] for r in leads} == expect_one_sided
    leads = [r for r in _rows(some)[0] if r["kind"] == "lead"]
    one_sided = [r for r in leads if not r["usable"]]
    assert 0 < len(one_sided) < len(leads)
    for row in one_sided:
        assert row["admissible"] and not row["quality_ok"]
        assert row["reason"] == "low_quality"
        yes_levels, no_levels = ladders_from_book_json(row["book_json"])
        assert yes_levels and not no_levels
    for row in leads:
        if row["usable"]:
            yes_levels, no_levels = ladders_from_book_json(row["book_json"])
            assert yes_levels and no_levels
            assert row["reason"] == "ok"


def test_books_are_venue_native_and_shrunk_toward_uniform():
    """A KX book carries ``no_dollars`` (resting NO bids), a POLY book its
    own ``yes_asks``; both mirror into the same bid/ask; the market's ask
    on the true winner sits BELOW the truth (the favorite is underpriced)."""
    world = SyntheticLadderWorld(**SMALL)
    seen = set()
    for series in world.series:
        venue = venue_of(series)
        for row in world.pit_rows(series):
            if row["kind"] != "lead" or not row["usable"]:
                continue
            fp = json.loads(row["book_json"])["orderbook_fp"]
            seen.add(venue)
            if venue == "kalshi":
                assert set(fp) == {"yes_dollars", "no_dollars"}
                ask = 1.0 - fp["no_dollars"][0][0]
            else:
                assert set(fp) == {"yes_dollars", "yes_asks"}
                ask = fp["yes_asks"][0][0]
            bid = fp["yes_dollars"][0][0]
            assert ask - bid == pytest.approx(world.spread, abs=1e-6)
            assert 0.01 <= bid < ask <= 0.99
            for levels in fp.values():
                assert 1 <= len(levels) <= 2
                assert all(10 <= size <= 60 for _price, size in levels)
    assert seen == {"kalshi", "polymarket"}
    # The mispricing: the winner's ask is between uniform and the truth.
    outcomes = world.outcomes()
    for series in world.series:
        rows = [r for r in world.pit_rows(series) if r["kind"] == "lead" and r["usable"]]
        for row in rows:
            fp = json.loads(row["book_json"])["orderbook_fp"]
            ask = 1.0 - fp["no_dollars"][0][0] if "no_dollars" in fp else fp["yes_asks"][0][0]
            if outcomes[row["contract_ticker"]]:
                assert 1 / 3 < ask < testing.WINNER_Q
            else:
                assert (1 - testing.WINNER_Q) / 2 < ask < 1 / 3 + 0.02


def test_world_refuses_bad_knobs_by_name():
    for bad, needle in (
        ({"rungs": 1}, "rungs"),
        ({"series": ["NOPE"]}, "NOPE"),
        ({"series": []}, "series"),
        ({"series": ["KXA", "KXA"]}, "repeat"),
        ({"start_date": "not-a-date"}, "start_date"),
        ({"shrink": 1.5}, "shrink"),
        ({"spread": 0.0}, "spread"),
        ({"one_sided_rate": -0.1}, "one_sided_rate"),
        ({"events_per_series": 0}, "events_per_series"),
        ({"seed": True}, "seed"),
    ):
        knobs = resolve_knobs(bad)
        problems = world_problems(knobs)
        assert any(needle in p for p in problems), (bad, problems)
        with pytest.raises(ValueError, match=needle):
            SyntheticLadderWorld(**knobs)
    assert world_problems(resolve_knobs({})) == []


def test_unknown_series_and_events_refuse_by_name():
    world = SyntheticLadderWorld(**SMALL)
    with pytest.raises(ValueError, match="KXNOPE"):
        world.pit_rows("KXNOPE")
    with pytest.raises(ValueError, match="KXNOPE"):
        world.market_rows("KXNOPE")
    with pytest.raises(ValueError, match="KXSYNA-000000"):
        world.close_ms("KXSYNA-000000")


# -- the parent's on-disk layout --------------------------------------------


def test_write_parent_store_lays_out_the_parents_two_stores(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    world = SyntheticLadderWorld(**SMALL)
    written = write_parent_store(world, str(tmp_path))
    assert set(written) == set(world.series)
    for series in world.series:
        markets = written[series]["markets"]
        pit = written[series]["pit"]
        assert markets == os.path.join(str(tmp_path), "kalshi", "markets", f"{series}.parquet")
        assert pit == os.path.join(
            str(tmp_path), "history", "predexon_l2_pit", f"{series}.ndjson"
        )
        table = pq.read_table(markets)
        assert table.column_names == list(MARKET_COLUMNS)
        assert len(MARKET_COLUMNS) == 14
        rows = table.to_pylist()
        expected = world.market_rows(series)
        assert len(rows) == len(expected)
        for got, want in zip(rows, expected):
            for column in MARKET_COLUMNS:
                if want[column] is None:
                    assert got[column] != got[column], column  # NaN, the parent's null
                else:
                    assert got[column] == want[column], column
        with open(pit, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        assert lines == world.pit_rows(series)
        with open(pit, encoding="utf-8") as fh:
            first = fh.readline()
        assert first == json.dumps(lines[0], sort_keys=True) + "\n"


# -- the connector double ----------------------------------------------------


def _read(conn, config, streams, state=None, mode="backfill"):
    msgs = list(conn.read(config, streams, state or {}, mode))
    for m in msgs:
        assert check_message(m) is not None
    return msgs


def test_connector_spec_passes_its_own_gate_and_default_denies():
    conn = SyntheticLadderConnector()
    check_config(conn, {})
    check_config(conn, {**SMALL, "seed": 9, "notes": "a comment"})
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {"surprise": 1})


def test_connector_check_fails_fast_on_bad_knobs():
    conn = SyntheticLadderConnector()
    conn.check(SMALL)
    with pytest.raises(AssetError, match="rungs"):
        conn.check({"rungs": 1})
    with pytest.raises(AssetError, match="NOPE"):
        conn.check({"series": ["NOPE"]})


def test_connector_discovers_both_streams_with_the_readers_keys():
    """The stream names and primary keys are the NODE module's constants,
    imported — the writer double and the reader cannot disagree."""
    streams = {s["stream"]: s for s in SyntheticLadderConnector().discover({})}
    assert set(streams) == {MARKETS_STREAM, PIT_STREAM}
    assert streams[MARKETS_STREAM]["primary_key"] == list(MARKET_KEY_FIELDS)
    assert streams[PIT_STREAM]["primary_key"] == list(PIT_KEY_FIELDS)
    assert streams[MARKETS_STREAM]["schema"] == {"fields": list(MARKET_COLUMNS)}
    assert streams[PIT_STREAM]["schema"] == {"fields": list(PIT_FIELDS)}


def test_connector_read_is_cursor_honest_and_json_safe():
    conn = SyntheticLadderConnector()
    world = SyntheticLadderWorld(**SMALL)
    pit, markets = _rows(world)

    msgs = _read(conn, SMALL, [MARKETS_STREAM, PIT_STREAM])
    types = [m["type"] for m in msgs]
    assert types[0] == "SCHEMA" and types[-1] == "STATE"
    assert types.count("SCHEMA") == 2
    records = [m for m in msgs if m["type"] == "RECORD"]
    by_stream = {}
    for m in records:
        by_stream.setdefault(m["stream"], []).append(m)
        json.dumps(m["data"], allow_nan=False)  # no NaN ever leaves the connector
        assert m["kind"] == "observation"
    assert [m["data"] for m in by_stream[MARKETS_STREAM]] == sorted(
        markets, key=lambda r: (r["close_time"], r["ticker"])
    )
    assert [m["data"] for m in by_stream[PIT_STREAM]] == sorted(
        pit, key=lambda r: (r["epoch_ts_ms"], r["contract_ticker"])
    )
    assert all(m["effective_date"] == m["data"]["close_time"] for m in by_stream[MARKETS_STREAM])
    for m in by_stream[PIT_STREAM]:
        assert m["effective_date"].endswith("+00:00")
        assert m["effective_date"][19] == "."  # millisecond precision
    for stream_msgs in by_stream.values():
        effs = [m["effective_date"] for m in stream_msgs]
        assert effs == sorted(effs)
    state = msgs[-1]["state"]
    assert set(state) == {MARKETS_STREAM, PIT_STREAM}
    assert state[PIT_STREAM]["cursor"] == by_stream[PIT_STREAM][-1]["effective_date"]

    # A re-pull from the saved cursor is an honest no-op...
    again = [m for m in _read(conn, SMALL, [PIT_STREAM], state) if m["type"] == "RECORD"]
    assert again == []
    # ...and a mid-stream cursor emits only what lies STRICTLY after it.
    mid = by_stream[PIT_STREAM][len(by_stream[PIT_STREAM]) // 2]["effective_date"]
    later = [
        m for m in _read(conn, SMALL, [PIT_STREAM], {PIT_STREAM: {"cursor": mid}})
        if m["type"] == "RECORD"
    ]
    assert later and all(m["effective_date"] > mid for m in later)
    assert later == [m for m in by_stream[PIT_STREAM] if m["effective_date"] > mid]

    with pytest.raises(AssetError, match="unknown stream"):
        list(conn.read(SMALL, ["ghost"], {}, "backfill"))


def test_acquire_synthetic_round_trips_the_row_counts(tmp_path):
    world = SyntheticLadderWorld(**SMALL)
    pit, markets = _rows(world)
    root, registry, source = acquire_synthetic(str(tmp_path / "ob"), SMALL)
    assert source == "synthetic"
    scanned = scan_stream(root.root, source, PIT_STREAM, key_fields=PIT_KEY_FIELDS)
    assert len(scanned) == len(pit)
    assert {r["contract_ticker"] for r in scanned} == {r["contract_ticker"] for r in pit}
    scanned_markets = scan_stream(
        root.root, source, MARKETS_STREAM, key_fields=MARKET_KEY_FIELDS
    )
    assert len(scanned_markets) == len(markets)
    assert sorted(r["ticker"] for r in scanned_markets) == sorted(r["ticker"] for r in markets)
    assert registry.model.name == "onboarding"
