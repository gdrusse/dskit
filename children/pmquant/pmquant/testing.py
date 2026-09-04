"""Deterministic doubles for the child's own tests — a synthetic ladder world.

Every test in this child that needs data needs the SAME data: a store
shaped exactly like the parent project's — the point-in-time ledger
under ``history/predexon_l2_pit/`` and the settlement/strike parquet
under ``kalshi/markets/`` — small enough to build in milliseconds and
byte-identical on every machine. :class:`SyntheticLadderWorld` is that
store in memory; :func:`write_parent_store` lays it on disk in the
parent's own layout; :class:`SyntheticLadderConnector` streams it through
the onboarding seam, so the child's data nodes are tested over a REAL
acquisition (the skeleton's ``SampleConnector`` pattern: an in-code
source, no filesystem, no network); :func:`acquire_synthetic` is the
register-activate-pull ceremony every such test would otherwise repeat.

The world is deliberately mispriced, the way ``dskit.pipeline.testing``
is: each event has one true winning rung with probability :data:`WINNER_Q`,
and the market's asks are shrunk toward uniform by ``shrink`` — a
stylized favorite-longshot bias, exaggerated so a model that un-shrinks
beats the market decisively at test-sized samples. A small
lead-dependent jitter keeps one contract's 21 books distinct; a fraction
``one_sided_rate`` of lead rows lose their ask side, so the unusable-row
path is exercised too. Everything derives from sha256 hashes of
``(seed, key)`` — no RNG state, so two worlds with one seed are one world.

The stream names and dedup keys are IMPORTED from :mod:`pmquant.nodes_data`
(the reader): a writer double that restated them could drift from what
the nodes look for.

Import cost: stdlib + dskit + this package; pyarrow is imported inside
:func:`write_parent_store` only.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import timedelta

from dskit.onboarding import (
    PROTOCOL,
    AssetError,
    Connector,
    OnboardingRoot,
    parse_utc,
    run_acquisition,
)
from dskit.pipeline.node import class_ref

from .ladder.protocols import (
    DEFAULT_DUR_CAPS_H,
    DEFAULT_MIN_ABS_LEAD_S,
    LEAD_FRACS,
    LeadGrid,
    lead_key,
    venue_of,
)
from .nodes_data import (
    MARKET_KEY_FIELDS,
    MARKETS_STREAM,
    PIT_KEY_FIELDS,
    PIT_STREAM,
    datetime_to_ms,
)

__all__ = [
    "BOOK_ENCODERS",
    "CONNECTOR_REF",
    "DEFAULT_EVENTS_PER_SERIES",
    "DEFAULT_KNOBS",
    "DEFAULT_ONE_SIDED_RATE",
    "DEFAULT_RUNGS",
    "DEFAULT_SEED",
    "DEFAULT_SERIES",
    "DEFAULT_SHRINK",
    "DEFAULT_SPREAD",
    "DEFAULT_START_DATE",
    "EVENT_LIFE_H",
    "MARKET_COLUMNS",
    "MARKET_FLOAT_COLUMNS",
    "PIT_FIELDS",
    "SyntheticLadderConnector",
    "SyntheticLadderWorld",
    "WINNER_Q",
    "acquire_synthetic",
    "resolve_knobs",
    "world_problems",
    "write_parent_store",
]

#: The world's knobs, each with ONE default — read by the world's
#: signature, by :func:`resolve_knobs`, and by the connector's ``spec()``
#: notes, so no consumer can advertise a stale value.
DEFAULT_SEED = 7
DEFAULT_SERIES = ("KXSYNA", "KXSYNB", "POLYSYNC")
DEFAULT_EVENTS_PER_SERIES = 60
DEFAULT_RUNGS = 3
DEFAULT_START_DATE = "2026-01-05"
DEFAULT_SHRINK = 0.5
DEFAULT_SPREAD = 0.04
DEFAULT_ONE_SIDED_RATE = 0.05

#: knob -> default, the connector's catalogue and the world's resolver.
DEFAULT_KNOBS = {
    "seed": DEFAULT_SEED,
    "series": DEFAULT_SERIES,
    "events_per_series": DEFAULT_EVENTS_PER_SERIES,
    "rungs": DEFAULT_RUNGS,
    "start_date": DEFAULT_START_DATE,
    "shrink": DEFAULT_SHRINK,
    "spread": DEFAULT_SPREAD,
    "one_sided_rate": DEFAULT_ONE_SIDED_RATE,
}

#: What each knob means — ``spec()`` builds its notes from this table and
#: the default above it, so the catalogue a config author reads is never
#: hand-written prose that can go stale.
_KNOB_NOTES = {
    "seed": "Drives every hash-derived draw (int)",
    "series": "Series tickers; each needs a declared venue prefix (KX* Kalshi, POLY* Polymarket)",
    "events_per_series": "Events per series, one per day from start_date (int >= 1)",
    "rungs": "Rung contracts per event, tiling the line as a partition ladder (int >= 2)",
    "start_date": (
        "ISO date of the first event's close (UTC midnight); keep the whole "
        "span in the past — acquisition refuses an observation dated after "
        "acquired_at"
    ),
    "shrink": "How far asks sit from the truth toward uniform, in [0, 1]",
    "spread": "Ask minus bid, dollars, in (0, 0.98)",
    "one_sided_rate": "Fraction of lead rows that lose their ask side, in [0, 1]",
}

#: An event's life: over 24h, so the daily 48h duration cap covers the
#: whole life and every one of the 21 leads lands inside it.
EVENT_LIFE_H = 26

#: The true probability on the winning rung; the rest split the remainder.
WINNER_Q = 0.80

#: The parent's 14-column settlement/strike schema, in column order.
MARKET_COLUMNS = (
    "ticker",
    "event_ticker",
    "series_ticker",
    "strike_type",
    "floor_strike",
    "cap_strike",
    "status",
    "result",
    "open_time",
    "close_time",
    "yes_sub_title",
    "yes_bid",
    "yes_ask",
    "last_price",
)

#: The float columns of :data:`MARKET_COLUMNS`; ``None`` in a connector
#: row becomes NaN in the parquet (the parent's null).
MARKET_FLOAT_COLUMNS = ("floor_strike", "cap_strike", "yes_bid", "yes_ask", "last_price")

#: The ledger row's keys, sorted (``json.dumps(sort_keys=True)`` order).
PIT_FIELDS = (
    "admissible",
    "book_json",
    "chosen_ts_ms",
    "contract_ticker",
    "epoch_ts_ms",
    "event_ticker",
    "kind",
    "lead_frac",
    "quality_ok",
    "reason",
    "series",
    "staleness_ms",
    "usable",
)

_BIAS_MASS = 0.5  # probability mass the per-series favourite rung gets
_STRIKE_BASE = 50.0
_STRIKE_STEP = 5.0
_SERIES_STRIKE_OFFSET = 10.0
_JITTER = 0.01
_TICK = 0.01
_PRICE_DP = 4
_PRICE_FLOOR = 0.01
_PRICE_CEIL = 0.99
_MIN_SIZE, _MAX_SIZE = 10, 60
_MAX_STALENESS_MS = 5_000
_SETTLED_STATUS = "settled"
_ORIGIN = "pmquant.testing"


def _unit(tag):
    """Draw a deterministic uniform in [0, 1) from a string tag."""
    digest = hashlib.sha256(tag.encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _number(value):
    """Say whether ``value`` is a finite real number (bool excluded)."""
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _iso_ms(epoch_ms):
    """Spell an epoch-ms instant as ISO-8601 UTC with milliseconds (integer arithmetic)."""
    secs, ms = divmod(int(epoch_ms), 1000)
    stamp = parse_utc("1970-01-01") + timedelta(seconds=secs)
    return f"{stamp:%Y-%m-%dT%H:%M:%S}.{ms:03d}+00:00"


def _iso_z(epoch_ms):
    """Spell an epoch-ms instant the way the parent's store does: second precision, ``Z``."""
    stamp = parse_utc("1970-01-01") + timedelta(seconds=int(epoch_ms) // 1000)
    return f"{stamp:%Y-%m-%dT%H:%M:%SZ}"


def _kalshi_book(yes_bids, ask, one_sided, levels):
    """Encode Kalshi-native: resting bids on both sides (NO bids at ``1 − ask``)."""
    no_bids = [] if one_sided else levels(round(1.0 - ask, _PRICE_DP), -_TICK, "nb")
    return {"yes_dollars": yes_bids, "no_dollars": no_bids}


def _poly_book(yes_bids, ask, one_sided, levels):
    """Encode Polymarket-native: the token's own asks, ascending."""
    asks = [] if one_sided else levels(ask, _TICK, "ya")
    return {"yes_dollars": yes_bids, "yes_asks": asks}


#: venue -> its book encoder — the dispatch table a third venue joins.
BOOK_ENCODERS = {"kalshi": _kalshi_book, "polymarket": _poly_book}


def resolve_knobs(config):
    """Merge a connector config over :data:`DEFAULT_KNOBS`.

    Parameters
    ----------
    config : dict
        A source config; keys outside the knob table (``notes``) are ignored.

    Returns
    -------
    dict
        Every knob, declared or defaulted.
    """
    return {name: config.get(name, default) for name, default in DEFAULT_KNOBS.items()}


def world_problems(knobs):
    """List problems with a full knob set, empty when none.

    The one validator behind :class:`SyntheticLadderWorld` (which raises
    ``ValueError``) and :meth:`SyntheticLadderConnector.check` (which
    raises ``AssetError``).

    Parameters
    ----------
    knobs : dict
        Every knob in :data:`DEFAULT_KNOBS`, as :func:`resolve_knobs` returns.

    Returns
    -------
    list of str
        One message per problem.
    """
    problems = []
    seed = knobs.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        problems.append(f"seed must be an int, got {seed!r}")
    series = knobs.get("series")
    if not isinstance(series, (list, tuple)) or not series:
        problems.append(f"series must be a non-empty list of tickers, got {series!r}")
    else:
        for name in series:
            if not isinstance(name, str) or not name:
                problems.append(f"series entries must be non-empty strings, got {name!r}")
            elif venue_of(name, default=None) is None:
                problems.append(
                    f"series {name!r} matches no declared venue prefix — spell it "
                    "KX* (Kalshi) or POLY* (Polymarket)"
                )
        if len(set(map(str, series))) != len(series):
            problems.append(f"series must not repeat a ticker, got {list(series)!r}")
    for name, floor in (("events_per_series", 1), ("rungs", 2)):
        value = knobs.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < floor:
            problems.append(f"{name} must be an int >= {floor}, got {value!r}")
    try:
        parse_utc(knobs.get("start_date"))
    except AssetError as exc:
        problems.append(f"start_date: {exc}")
    for name in ("shrink", "one_sided_rate"):
        value = knobs.get(name)
        if not _number(value) or not 0.0 <= value <= 1.0:
            problems.append(f"{name} must be a number in [0, 1], got {value!r}")
    spread = knobs.get("spread")
    if not _number(spread) or not 0.0 < spread < _PRICE_CEIL - _PRICE_FLOOR:
        problems.append(
            f"spread must be a number in (0, {_PRICE_CEIL - _PRICE_FLOOR:g}), got {spread!r}"
        )
    return problems


class SyntheticLadderWorld:
    """Build a deterministic in-memory ladder world in the parent's two store shapes.

    Parameters
    ----------
    seed : int
        Drives every hash-derived draw; default :data:`DEFAULT_SEED`.
    series : sequence of str
        Series tickers, each carrying a declared venue prefix (``KX*``
        Kalshi, ``POLY*`` Polymarket); default :data:`DEFAULT_SERIES`.
    events_per_series : int
        Events per series, one per day from ``start_date``; default
        :data:`DEFAULT_EVENTS_PER_SERIES`.
    rungs : int
        Rung contracts per event (>= 2), tiling the line as a PARTITION
        ladder — ``less`` at the bottom, ``between`` in the middle,
        ``greater`` at the top; default :data:`DEFAULT_RUNGS`.
    start_date : str
        ISO date of the first event's CLOSE (UTC midnight); each event
        opens :data:`EVENT_LIFE_H` hours earlier; default
        :data:`DEFAULT_START_DATE`.
    shrink : float
        How far the market's asks sit from the truth toward uniform:
        ``ask = 1/C + shrink * (q_true − 1/C)``; default :data:`DEFAULT_SHRINK`.
    spread : float
        Ask minus bid, dollars; default :data:`DEFAULT_SPREAD`.
    one_sided_rate : float
        Fraction of lead rows that lose their ask side and become
        unusable (``reason="low_quality"``); default
        :data:`DEFAULT_ONE_SIDED_RATE`.

    Examples
    --------
    Two rungs, four events, one Kalshi series::

        world = SyntheticLadderWorld(seed=1, series=("KXA",), events_per_series=4, rungs=2)
        len(world.pit_rows("KXA"))     # 176: 4 events x 2 rungs x (21 leads + 1 settle)
        world.events("KXA")[0]         # 'KXA-260105'
        world.outcomes()["KXA-260105-R0"] in (True, False)   # True
    """

    def __init__(
        self,
        seed=DEFAULT_SEED,
        series=DEFAULT_SERIES,
        events_per_series=DEFAULT_EVENTS_PER_SERIES,
        rungs=DEFAULT_RUNGS,
        start_date=DEFAULT_START_DATE,
        shrink=DEFAULT_SHRINK,
        spread=DEFAULT_SPREAD,
        one_sided_rate=DEFAULT_ONE_SIDED_RATE,
    ):
        knobs = {
            "seed": seed,
            "series": series,
            "events_per_series": events_per_series,
            "rungs": rungs,
            "start_date": start_date,
            "shrink": shrink,
            "spread": spread,
            "one_sided_rate": one_sided_rate,
        }
        problems = world_problems(knobs)
        if problems:
            raise ValueError("; ".join(problems))
        self.seed = int(seed)
        self.series = tuple(series)
        self.events_per_series = int(events_per_series)
        self.rungs = int(rungs)
        self.start_date = start_date
        self.shrink = float(shrink)
        self.spread = float(spread)
        self.one_sided_rate = float(one_sided_rate)
        self._grid = LeadGrid(LEAD_FRACS, DEFAULT_DUR_CAPS_H, DEFAULT_MIN_ABS_LEAD_S)
        self._events = {}
        self._contracts = {}
        self._open_ms = {}
        self._close_ms = {}
        self._pit = {}
        self._markets = {}
        self._outcomes = {}
        start = parse_utc(start_date)
        for index, name in enumerate(self.series):
            self._build_series(index, name, start)

    # -- construction ------------------------------------------------------

    def _build_series(self, index, series, start):
        """Build one series' events, ledger rows and settlement rows."""
        offset = _STRIKE_BASE + index * _SERIES_STRIKE_OFFSET
        strikes = [offset + k * _STRIKE_STEP for k in range(self.rungs - 1)]
        pit, markets, events = [], [], []
        for i in range(self.events_per_series):
            close_dt = start + timedelta(days=i)
            close_ms = datetime_to_ms(close_dt)
            open_ms = close_ms - EVENT_LIFE_H * 3_600_000
            event = f"{series}-{close_dt:%y%m%d}"
            events.append(event)
            self._open_ms[event] = open_ms
            self._close_ms[event] = close_ms
            winner = self._winner(index, event)
            contracts = []
            for j in range(self.rungs):
                contract = f"{event}-R{j}"
                contracts.append(contract)
                q_true = WINNER_Q if j == winner else (1.0 - WINNER_Q) / (self.rungs - 1)
                self._outcomes[contract] = j == winner
                bid = ask = None
                for lead_frac, epoch_ms in self._grid.epochs(open_ms, close_ms):
                    bid, ask = self._quotes(contract, lead_frac, q_true)
                    pit.append(self._lead_row(series, event, contract, lead_frac, epoch_ms, bid, ask))
                pit.append(self._settle_row(series, event, contract, close_ms))
                markets.append(
                    self._market_row(series, event, contract, j, strikes, winner, bid, ask)
                )
            self._contracts[event] = tuple(contracts)
        pit.sort(key=lambda row: (row["epoch_ts_ms"], row["contract_ticker"]))
        markets.sort(key=lambda row: (row["close_time"], row["ticker"]))
        self._events[series] = tuple(events)
        self._pit[series] = pit
        self._markets[series] = markets

    def _winner(self, index, event):
        """Draw the event's true winning rung, biased toward the series' favourite."""
        u = _unit(f"win:{self.seed}:{event}")
        if u < _BIAS_MASS:
            return index % self.rungs
        return int((u - _BIAS_MASS) / (1.0 - _BIAS_MASS) * self.rungs) % self.rungs

    def _quotes(self, contract, lead_frac, q_true):
        """Price one contract at one lead: the shrunk, jittered ask and its bid."""
        uniform = 1.0 / self.rungs
        jitter = (2.0 * _unit(f"jit:{self.seed}:{contract}:{lead_key(lead_frac)}") - 1.0) * _JITTER
        ask = uniform + self.shrink * (q_true - uniform) + jitter
        ask = min(max(ask, _PRICE_FLOOR + self.spread), _PRICE_CEIL)
        return round(ask - self.spread, _PRICE_DP), round(ask, _PRICE_DP)

    def _levels(self, tag):
        """Build a ladder factory: ``(best, step, side) -> [[price, size], ...]``, 1-2 levels."""

        def levels(best, step, side):
            n = 1 + int(_unit(f"{side}:{tag}:n") >= 0.5)
            out = []
            for k in range(n):
                price = round(best + k * step, _PRICE_DP)
                if not _PRICE_FLOOR <= price <= _PRICE_CEIL:
                    break
                size = _MIN_SIZE + int(_unit(f"{side}:{tag}:{k}") * (_MAX_SIZE - _MIN_SIZE + 1))
                out.append([price, size])
            return out

        return levels

    def _book_json(self, series, tag, bid, ask, one_sided):
        """Render one venue-native book as the ledger's JSON string."""
        levels = self._levels(tag)
        encoder = BOOK_ENCODERS[venue_of(series)]
        fp = encoder(levels(bid, -_TICK, "yb"), ask, one_sided, levels)
        return json.dumps({"orderbook_fp": fp}, sort_keys=True)

    def _lead_row(self, series, event, contract, lead_frac, epoch_ms, bid, ask):
        """Build one ledger lead row."""
        tag = f"{self.seed}:{contract}:{lead_key(lead_frac)}"
        one_sided = _unit(f"os:{tag}") < self.one_sided_rate
        staleness = int(_unit(f"stale:{tag}") * _MAX_STALENESS_MS)
        return {
            "admissible": True,
            "book_json": self._book_json(series, tag, bid, ask, one_sided),
            "chosen_ts_ms": epoch_ms - staleness,
            "contract_ticker": contract,
            "epoch_ts_ms": epoch_ms,
            "event_ticker": event,
            "kind": "lead",
            "lead_frac": float(lead_frac),
            "quality_ok": not one_sided,
            "reason": "low_quality" if one_sided else "ok",
            "series": series,
            "staleness_ms": staleness,
            "usable": not one_sided,
        }

    @staticmethod
    def _settle_row(series, event, contract, close_ms):
        """Build one ledger settle row: no book, nothing admissible."""
        return {
            "admissible": False,
            "book_json": None,
            "chosen_ts_ms": None,
            "contract_ticker": contract,
            "epoch_ts_ms": close_ms,
            "event_ticker": event,
            "kind": "settle",
            "lead_frac": None,
            "quality_ok": False,
            "reason": "settle",
            "series": series,
            "staleness_ms": None,
            "usable": False,
        }

    def _rung_geometry(self, j, strikes):
        """Give ``(strike_type, floor, cap, yes_sub_title)`` for rung ``j``."""
        if j == 0:
            return "less", None, strikes[0], f"{strikes[0]:g} or below"
        if j == self.rungs - 1:
            return "greater", strikes[-1], None, f"{strikes[-1]:g} or above"
        return "between", strikes[j - 1], strikes[j], f"{strikes[j - 1]:g} to {strikes[j]:g}"

    def _market_row(self, series, event, contract, j, strikes, winner, bid, ask):
        """Build one 14-column settlement/strike row (last lead's quotes)."""
        strike_type, floor, cap, title = self._rung_geometry(j, strikes)
        return {
            "ticker": contract,
            "event_ticker": event,
            "series_ticker": series,
            "strike_type": strike_type,
            "floor_strike": floor,
            "cap_strike": cap,
            "status": _SETTLED_STATUS,
            "result": "yes" if j == winner else "no",
            "open_time": _iso_z(self._open_ms[event]),
            "close_time": _iso_z(self._close_ms[event]),
            "yes_sub_title": title,
            "yes_bid": bid,
            "yes_ask": ask,
            "last_price": round((bid + ask) / 2.0, _PRICE_DP),
        }

    # -- the public read surface ------------------------------------------------

    def _known(self, table, key, what):
        """Look ``key`` up in ``table`` or refuse naming it and the known keys."""
        if key not in table:
            raise ValueError(f"unknown {what} {key!r} — this world holds {sorted(table)[:8]}")
        return table[key]

    def events(self, series):
        """List one series' event tickers, chronological.

        Parameters
        ----------
        series : str
            A series of this world.

        Returns
        -------
        list of str
            The event tickers.

        Raises
        ------
        ValueError
            On a series this world does not hold.
        """
        return list(self._known(self._events, series, "series"))

    def contracts(self, event):
        """List one event's contract tickers, rung order.

        Parameters
        ----------
        event : str
            An event of this world.

        Returns
        -------
        list of str
            The contract tickers.

        Raises
        ------
        ValueError
            On an event this world does not hold.
        """
        return list(self._known(self._contracts, event, "event"))

    def open_ms(self, event):
        """Give one event's open instant (int epoch ms); refuses an unknown event."""
        return self._known(self._open_ms, event, "event")

    def close_ms(self, event):
        """Give one event's close instant (int epoch ms); refuses an unknown event."""
        return self._known(self._close_ms, event, "event")

    def pit_rows(self, series):
        """Give one series' ledger rows, sorted by ``(epoch_ts_ms, contract_ticker)``.

        Parameters
        ----------
        series : str
            A series of this world.

        Returns
        -------
        list of dict
            Fresh copies of the rows (13 keys each — :data:`PIT_FIELDS`);
            ``book_json`` is a JSON string, ``None`` on a settle row.

        Raises
        ------
        ValueError
            On a series this world does not hold.
        """
        return [dict(row) for row in self._known(self._pit, series, "series")]

    def market_rows(self, series):
        """Give one series' settlement/strike rows, sorted by ``(close_time, ticker)``.

        Parameters
        ----------
        series : str
            A series of this world.

        Returns
        -------
        list of dict
            Fresh copies, keyed by :data:`MARKET_COLUMNS`; a missing
            strike is ``None`` (the parquet writer turns it into NaN).

        Raises
        ------
        ValueError
            On a series this world does not hold.
        """
        return [dict(row) for row in self._known(self._markets, series, "series")]

    def outcomes(self):
        """Give every contract's settled-YES bool (dict), all series."""
        return dict(self._outcomes)


def write_parent_store(world, data_dir):
    """Write the world in the parent's on-disk layout.

    ``<data_dir>/kalshi/markets/<series>.parquet`` (exactly
    :data:`MARKET_COLUMNS`, in order; strings as strings, the
    :data:`MARKET_FLOAT_COLUMNS` as float64 with NaN for ``None``) and
    ``<data_dir>/history/predexon_l2_pit/<series>.ndjson`` (one
    ``json.dumps(row, sort_keys=True)`` per line). Imports pyarrow here.

    Parameters
    ----------
    world : SyntheticLadderWorld
        The world to lay down.
    data_dir : str
        The data root; subdirectories are created.

    Returns
    -------
    dict
        ``series -> {"markets": <parquet path>, "pit": <ndjson path>}``.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    markets_dir = os.path.join(data_dir, "kalshi", "markets")
    pit_dir = os.path.join(data_dir, "history", "predexon_l2_pit")
    os.makedirs(markets_dir, exist_ok=True)
    os.makedirs(pit_dir, exist_ok=True)
    written = {}
    for series in world.series:
        rows = world.market_rows(series)
        columns = {}
        for column in MARKET_COLUMNS:
            values = [row[column] for row in rows]
            if column in MARKET_FLOAT_COLUMNS:
                columns[column] = pa.array(
                    [math.nan if v is None else float(v) for v in values], type=pa.float64()
                )
            else:
                columns[column] = pa.array([str(v) for v in values], type=pa.string())
        markets_path = os.path.join(markets_dir, f"{series}.parquet")
        pq.write_table(pa.table(columns), markets_path)
        pit_path = os.path.join(pit_dir, f"{series}.ndjson")
        with open(pit_path, "w", encoding="utf-8") as fh:
            for row in world.pit_rows(series):
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        written[series] = {"markets": markets_path, "pit": pit_path}
    return written


class SyntheticLadderConnector(Connector):
    """Stream a :class:`SyntheticLadderWorld` through the four-verb seam.

    Two streams: :data:`~pmquant.nodes_data.MARKETS_STREAM` (one RECORD per
    settlement row, ``effective_date`` its ``close_time``) and
    :data:`~pmquant.nodes_data.PIT_STREAM` (one RECORD per ledger row,
    ``effective_date`` the ISO-with-milliseconds spelling of
    ``epoch_ts_ms``; ``book_json`` stays a JSON string). Cursor semantics
    as the skeleton's sample: state maps stream -> ``{"cursor": <max
    effective_date emitted>}``, a pull emits only rows strictly after it,
    and the platform keys checkpoints per mode. Every ``data`` is
    JSON-serializable (missing strikes are ``None``, never NaN).

    Parameters
    ----------
    None
        Stateless; the knobs (:data:`DEFAULT_KNOBS`) ride on the config.

    Examples
    --------
    One pull of both streams from a fresh cursor::

        conn = SyntheticLadderConnector()
        msgs = list(conn.read({"events_per_series": 2}, ["markets", "pit"], {}, "backfill"))
        msgs[0]["type"], msgs[-1]["type"]   # ('SCHEMA', 'STATE')
    """

    def spec(self):
        """Declare the knobs — every one optional, notes built from the defaults.

        Returns
        -------
        dict
            ``{"params": {knob: {"notes": ...}}}``.
        """
        return {
            "params": {
                name: {"notes": f"{_KNOB_NOTES[name]}; default {default!r}."}
                for name, default in DEFAULT_KNOBS.items()
            }
        }

    def check(self, config):
        """Refuse a config the world could not be built from; move no data.

        Parameters
        ----------
        config : dict
            The knobs supplied at run time.

        Raises
        ------
        AssetError
            Listing every problem :func:`world_problems` finds.
        """
        problems = world_problems(resolve_knobs(config))
        if problems:
            raise AssetError(problems)

    def discover(self, config):
        """Name the two streams, their fields and the READER's primary keys.

        Parameters
        ----------
        config : dict
            Unused — the streams do not depend on the knobs.

        Returns
        -------
        list of dict
            ``{"stream", "schema", "primary_key"}`` for markets and pit.
        """
        return [
            {
                "stream": MARKETS_STREAM,
                "schema": {"fields": list(MARKET_COLUMNS)},
                "primary_key": list(MARKET_KEY_FIELDS),
            },
            {
                "stream": PIT_STREAM,
                "schema": {"fields": list(PIT_FIELDS)},
                "primary_key": list(PIT_KEY_FIELDS),
            },
        ]

    @staticmethod
    def _rows(world, stream):
        """List ``(effective_date, data)`` for one stream, in effective order."""
        if stream == MARKETS_STREAM:
            rows = [row for s in world.series for row in world.market_rows(s)]
            rows.sort(key=lambda row: (row["close_time"], row["ticker"]))
            return [(row["close_time"], row) for row in rows]
        if stream == PIT_STREAM:
            rows = [row for s in world.series for row in world.pit_rows(s)]
            rows.sort(key=lambda row: (row["epoch_ts_ms"], row["contract_ticker"]))
            return [(_iso_ms(row["epoch_ts_ms"]), row) for row in rows]
        raise AssetError(
            [f"unknown stream {stream!r} — discovered: {[MARKETS_STREAM, PIT_STREAM]!r}"]
        )

    def read(self, config, streams, state, mode):
        """Emit SCHEMA, then cursor-filtered RECORDs per stream, then one STATE.

        Parameters
        ----------
        config : dict
            Knobs already validated by ``check_config``.
        streams : list of str
            Which streams to pull.
        state : dict
            The last checkpoint for this (source, stream, mode); ``{}`` first.
        mode : str
            ``"backfill"`` or ``"live"`` — the same logic either way.

        Yields
        ------
        dict
            Envelope messages.

        Raises
        ------
        AssetError
            On a malformed ``state``/``streams`` or an unknown stream.
        """
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        world = SyntheticLadderWorld(**resolve_knobs(config))
        new_state = {k: dict(v) for k, v in state.items()}
        for stream in streams:
            rows = self._rows(world, stream)
            cursor = state.get(stream, {}).get("cursor", "")
            cursor_dt = parse_utc(cursor) if cursor else None
            fields = next(s["schema"] for s in self.discover(config) if s["stream"] == stream)
            yield {"protocol": PROTOCOL, "type": "SCHEMA", "stream": stream, "schema": fields}
            emitted_max = cursor
            for effective, data in rows:
                if cursor_dt is not None and parse_utc(effective) <= cursor_dt:
                    continue
                yield {
                    "protocol": PROTOCOL,
                    "type": "RECORD",
                    "stream": stream,
                    "effective_date": effective,
                    "kind": "observation",
                    "data": data,
                }
                emitted_max = effective
            new_state.setdefault(stream, {})["cursor"] = emitted_max
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}


#: The import reference a source config names — spelled by the toolkit's
#: own ``class_ref``, never retyped.
CONNECTOR_REF = class_ref(SyntheticLadderConnector)


def acquire_synthetic(root_dir, config, source_name="synthetic"):
    """Create an onboarding root and pull both synthetic streams into it.

    Parameters
    ----------
    root_dir : str
        Where to create the root (refused if already initialized).
    config : dict
        The connector config — any subset of :data:`DEFAULT_KNOBS`.
    source_name : str
        The source alias to register and activate; default ``"synthetic"``.

    Returns
    -------
    tuple
        ``(root, registry, source_name)`` — the
        :class:`~dskit.onboarding.OnboardingRoot`, its registry, and the
        alias, ready for a data node's ``root``/``source`` params.
    """
    root = OnboardingRoot.create(str(root_dir))
    registry = root.registry()
    vid = registry.register(
        "source_config",
        {
            "name": source_name,
            "catalog_source": f"{source_name}-src",
            "connector": CONNECTOR_REF,
            "config": dict(config),
        },
        origin=_ORIGIN,
    )
    registry.transition(vid, "active", origin=_ORIGIN)
    for stream in (MARKETS_STREAM, PIT_STREAM):
        run_acquisition(root, registry, source_name, stream, "backfill", origin=_ORIGIN)
    return root, registry, source_name
