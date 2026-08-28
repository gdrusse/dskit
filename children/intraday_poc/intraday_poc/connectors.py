"""``connectors`` — the child's onboarding seam (four verbs, ADR-0013).

One connector, one vendor: Alpaca Market Data v2 stock bars for the two
symbols this PoC trades. ONE registered source (``alpaca``) and ONE
config serve both pulls: the platform keys checkpoints per (source,
stream, mode), so ``backfill`` (all the history there is) and ``live``
(polled forward on a cadence) hold independent cursors without this
connector branching and without a second source (ADR-0014). A second
SOURCE NAME would be the bug this child once shipped — observations live
at ``observations/<source>/`` and a run document reads one source, so
bars acquired under another name reach no model.

Both modes therefore pull the same feed: SIP consolidated history, free
back to 2016, with ``end`` clamped 16 minutes into the past per the
free-tier gate — which a live-mode pull simply trails by that much. The
IEX feed is real-time-eligible and stays available as a knob, but the
STORE is deliberately homogeneous; the forward loop's own real-time
fetch (``live.py``) is where IEX is used.

Cursor semantics, identical to the reference ``localfiles`` connector:
state maps stream -> ``{"cursor": <max effective RFC-3339 ts emitted>}``;
a pull fetches from the cursor when there is one (else from where the
MODE says, below) and emits only bars strictly after it, then
checkpoints once. The cursor is shared
across the configured symbols (one fetch returns both); a minute in
which only ONE symbol printed is still safe — the other symbol's bar for
that minute arrives in a later pull and is client-side filtered only
against the cursor, which cannot pass it, so the next pull's fetch window
re-covers it.

**The two modes differ in exactly one place: where a pull with NO cursor
starts.** Backfill starts at ``start`` — all the history there is. Live
is a forward top-up, so it starts at most ``live_lookback_minutes`` ago:
its cursor is empty on the first pull (they are keyed per mode), and
windowing that from ``start`` would ask the vendor for the whole history
a second time and write it as a full duplicate acquisition — every
later scan then carries two copies of every bar the dedup has to
collapse. Once the live cursor exists it wins outright, however old it
is, so an outage is re-covered rather than skipped.

Config knobs (default-deny, per ``spec()``):

- ``symbols`` (required) — list of ticker strings, e.g. ["AAPL", "MSFT"].
- ``start`` (required) — ISO date/datetime; the earliest bar wanted.
- ``feed`` — ``"sip"`` (default; full consolidated tape, historical only
  on the free tier) or ``"iex"`` (real-time-eligible, ~2.5% of volume).
- ``adjustment`` — ``"raw"`` | ``"split"`` | ``"dividend"`` | ``"all"``
  (default ``"all"``: the LSTM wants price series stationary across
  corporate actions).
- ``live_lookback_minutes`` — how far back a live pull with no cursor
  reaches (default :data:`DEFAULT_LIVE_LOOKBACK_MINUTES`). Widen it if
  the backfill's tail is further behind than that; the bars between the
  backfill cursor and this floor belong to no pull.
- ``key_env`` / ``secret_env`` — NAMES of the env vars holding the
  Alpaca key pair (defaults ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY``).
  The material itself never enters a config, a snapshot, or any hash —
  the restapi pack's ``secret`` doctrine.

The BAR INTERVAL is deliberately not a knob yet (:data:`BAR_INTERVAL`):
one constant, imported by the forward loop, because the loop's own
minute cadence and the window node's gap discipline are built on it. A
``timeframe`` knob is an open TODO; what is NOT open is the agreement —
both fetch paths build their vendor ``TimeFrame`` here.

Import cost: stdlib + dskit. The vendor SDK (``alpaca-py``) is imported
strictly inside ``check``/``read``/:func:`bar_timeframe` — the same rule
as pipeline nodes' ``run()``.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

from dskit.onboarding import MODES, PROTOCOL, AssetError, Connector, parse_utc

__all__ = [
    "BACKFILL_MODE",
    "BAR_INTERVAL",
    "BAR_KEY_FIELDS",
    "BAR_STREAM",
    "DEFAULT_LIVE_LOOKBACK_MINUTES",
    "LIVE_MODE",
    "AlpacaBarsConnector",
    "bar_timeframe",
]

#: The platform's acquisition modes, unpacked rather than restated: a
#: third mode (ADR-0014 declares two) breaks this import LOUDLY instead
#: of being silently windowed as a backfill.
BACKFILL_MODE, LIVE_MODE = MODES

#: The stream this source offers and the tuple that IDENTIFIES a bar.
#: Both are public because ``nodes.py`` imports them (same package): the
#: node scans the stream this connector writes and dedupes on the key
#: this connector publishes as ``primary_key``. Two copies of either
#: value would let the reader look for a spelling the writer abandoned,
#: or the store dedupe on a different tuple than the platform advertised
#: — silently, in both directions.
BAR_STREAM = "bars"
BAR_KEY_FIELDS = ("symbol", "ts")

#: The bar interval BOTH fetch paths pull — ``(amount, TimeFrameUnit
#: member name)``. Public because ``live.py`` imports it: the store and
#: the served series must be the same series, and two literals would let
#: one move alone. See :func:`bar_timeframe`.
BAR_INTERVAL = (1, "Minute")

#: How far back a LIVE pull with no cursor reaches. One name, read by
#: the knob gate and by ``spec()``'s note — a restated literal in either
#: would advertise a default the pull does not use.
DEFAULT_LIVE_LOOKBACK_MINUTES = 1440

_FIELDS = ["symbol", "ts", "open", "high", "low", "close", "volume",
           "trade_count", "vwap"]

_FEEDS = ("sip", "iex")
_ADJUSTMENTS = ("raw", "split", "dividend", "all")

#: The free tier refuses SIP queries whose ``end`` is inside the last 15
#: minutes; clamp with a minute of slack rather than erroring mid-pull.
_SIP_LAG = timedelta(minutes=16)


def bar_timeframe():
    """Build the vendor ``TimeFrame`` for :data:`BAR_INTERVAL`.

    Returns
    -------
    alpaca.data.timeframe.TimeFrame
        The interval every fetch on this vendor asks for — the
        connector's ``_fetch`` and the forward loop's own REST pull
        alike, so the served bars are the series the store holds.

    Raises
    ------
    ImportError
        When ``alpaca-py`` is not installed; the import is inside the
        function, so planning never needs the vendor SDK.
    """
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    amount, unit = BAR_INTERVAL
    return TimeFrame(amount, TimeFrameUnit[unit])


class AlpacaBarsConnector(Connector):
    """Alpaca v2 stock bars at :data:`BAR_INTERVAL`, one stream.

    Parameters
    ----------
    None
        The connector is stateless; everything it pulls comes from the
        config each verb is handed (see the module docs for the knobs).

    Examples
    --------
    Resolve a config's knobs through the connector's own gate::

        conn = AlpacaBarsConnector()
        knobs = conn.resolve_knobs({"symbols": ["AAPL"],
                                    "start": "2021-01-01"})
        knobs["feed"]  # 'sip'
    """

    def spec(self) -> dict:
        """Declare the knob catalogue this connector accepts.

        Returns
        -------
        dict
            ``{"params": {<knob>: {"required": bool, "notes": str}}}`` —
            the platform refuses any config key not named here, and the
            notes are what a config author reads.
        """
        return {
            "params": {
                "symbols": {
                    "required": True,
                    "notes": "Ticker list, e.g. [\"AAPL\", \"MSFT\"].",
                },
                "start": {
                    "required": True,
                    "notes": "ISO date/datetime of the earliest bar wanted.",
                },
                "feed": {
                    "notes": "sip (default; historical-only on free tier) "
                             "or iex (real-time-eligible).",
                },
                "adjustment": {
                    "notes": "raw|split|dividend|all; default all.",
                },
                "live_lookback_minutes": {
                    # BUILT from the constant, never restated: a note
                    # advertising a stale default is a config lie.
                    "notes": "How far back a live-mode pull with no "
                             "cursor reaches; the history is backfill's "
                             f"job. Default {DEFAULT_LIVE_LOOKBACK_MINUTES}.",
                },
                "key_env": {
                    "notes": "Env var NAME holding the Alpaca key id; "
                             "default APCA_API_KEY_ID.",
                },
                "secret_env": {
                    "notes": "Env var NAME holding the Alpaca secret; "
                             "default APCA_API_SECRET_KEY.",
                },
            },
        }

    # -- the knob gate (public: the forward loop resolves through it) ------

    def resolve_knobs(self, config) -> dict:
        """Validate one source config and return its resolved knobs.

        The gate every verb runs first, and the one ``live.py`` calls to
        take its vendor knobs from the config the PULLER uses: resolving
        there rather than restating the defaults means a config this
        connector accepts is one the serving loop accepts too, with the
        same fallbacks.

        Parameters
        ----------
        config : dict
            The registered source config.

        Returns
        -------
        dict
            ``symbols`` (list of str), ``start`` (str), ``feed`` (str),
            ``adjustment`` (str), ``live_lookback_minutes`` (int or
            float) and the credential env-var NAMES ``key_env`` /
            ``secret_env`` — every knob resolved, defaults applied.

        Raises
        ------
        AssetError
            Listing EVERY invalid knob at once, then the stamp problems
            ``parse_utc`` finds in ``start``.
        """
        problems = []
        symbols = config.get("symbols")
        if (not isinstance(symbols, list) or not symbols
                or not all(isinstance(s, str) and s for s in symbols)):
            problems.append(
                f"config.symbols must be a non-empty list of tickers, "
                f"got {symbols!r}"
            )
        start = config.get("start")
        if not isinstance(start, str) or not start:
            problems.append(f"config.start must be an ISO string, got {start!r}")
        feed = config.get("feed", "sip")
        if feed not in _FEEDS:
            problems.append(f"config.feed must be one of {_FEEDS}, got {feed!r}")
        adjustment = config.get("adjustment", "all")
        if adjustment not in _ADJUSTMENTS:
            problems.append(
                f"config.adjustment must be one of {_ADJUSTMENTS}, "
                f"got {adjustment!r}"
            )
        lookback = config.get("live_lookback_minutes",
                              DEFAULT_LIVE_LOOKBACK_MINUTES)
        if (isinstance(lookback, bool) or not isinstance(lookback, (int, float))
                or not math.isfinite(lookback) or lookback <= 0):
            problems.append(
                f"config.live_lookback_minutes must be a positive number, "
                f"got {lookback!r}"
            )
        if problems:
            raise AssetError(problems)
        parse_utc(start)  # raises AssetError itself on a bad stamp
        return {
            "symbols": list(symbols),
            "start": start,
            "feed": feed,
            "adjustment": adjustment,
            "live_lookback_minutes": lookback,
            "key_env": config.get("key_env", "APCA_API_KEY_ID"),
            "secret_env": config.get("secret_env", "APCA_API_SECRET_KEY"),
        }

    # -- internals ---------------------------------------------------------

    def _credentials(self, knobs) -> tuple:
        key = os.environ.get(knobs["key_env"], "")
        secret = os.environ.get(knobs["secret_env"], "")
        missing = [name for name, value in
                   ((knobs["key_env"], key), (knobs["secret_env"], secret))
                   if not value]
        if missing:
            raise AssetError(
                [f"env var(s) {missing} are empty — put the Alpaca key pair "
                 "in .env (see .env.example) or export them"]
            )
        return key, secret

    def _window(self, knobs, cursor, mode) -> tuple:
        """Compute the fetch window, ``(start, end)`` or ``(None, None)``.

        From the checkpoint, else from config.start — in live mode no
        further back than its lookback — to now, clamped for SIP's
        free-tier 15-minute gate.
        """
        start_dt = parse_utc(knobs["start"])
        if cursor:
            cursor_dt = parse_utc(cursor)
            if cursor_dt > start_dt:
                start_dt = cursor_dt
        elif mode == LIVE_MODE:
            floor = datetime.now(timezone.utc) - timedelta(
                minutes=knobs["live_lookback_minutes"])
            if floor > start_dt:
                start_dt = floor
        end_dt = datetime.now(timezone.utc)
        if knobs["feed"] == "sip":
            end_dt = end_dt - _SIP_LAG
        if end_dt <= start_dt:
            return None, None  # nothing new can exist yet
        return start_dt, end_dt

    def _fetch(self, knobs, start_dt, end_dt):
        """Yield ``(symbol, bar_dict)`` ascending in time per symbol.

        The one method that talks to the vendor (tests override it);
        alpaca-py auto-paginates via page_token under the hood.
        """
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest

        key, secret = self._credentials(knobs)
        client = StockHistoricalDataClient(key, secret)
        request = StockBarsRequest(
            symbol_or_symbols=knobs["symbols"],
            timeframe=bar_timeframe(),
            start=start_dt,
            end=end_dt,
            feed=DataFeed(knobs["feed"]),
            adjustment=Adjustment(knobs["adjustment"]),
            limit=None,
        )
        bars = client.get_stock_bars(request)
        for symbol, series in sorted(bars.data.items()):
            for bar in series:
                yield symbol, {
                    "symbol": symbol,
                    "ts": bar.timestamp.astimezone(timezone.utc).isoformat(),
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": float(bar.volume),
                    "trade_count": (None if bar.trade_count is None
                                    else int(bar.trade_count)),
                    "vwap": None if bar.vwap is None else float(bar.vwap),
                }

    # -- the four verbs ----------------------------------------------------

    def check(self, config) -> None:
        """Fail fast, moving no data.

        Knobs, credentials present, and one authenticated round-trip
        (the latest bar for the first symbol).

        Parameters
        ----------
        config : dict
            The source config to check.

        Returns
        -------
        None
            Silence means the vendor answered.

        Raises
        ------
        AssetError
            On a bad knob, an empty credential env var, or a failed
            round-trip (bad keys, or no network).
        """
        knobs = self.resolve_knobs(config)
        key, secret = self._credentials(knobs)
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestBarRequest

        client = StockHistoricalDataClient(key, secret)
        try:
            client.get_stock_latest_bar(StockLatestBarRequest(
                symbol_or_symbols=knobs["symbols"][:1],
                feed=DataFeed("iex"),  # latest-bar is free only on iex
            ))
        except Exception as exc:  # vendor errors are not AssetErrors yet
            raise AssetError(
                [f"Alpaca round-trip failed: {exc} — bad keys, or no network?"]
            ) from exc

    def discover(self, config) -> list:
        """Advertise the one stream this source offers.

        Parameters
        ----------
        config : dict
            The source config, gated first so discovery never advertises
            a stream a bad config could not pull.

        Returns
        -------
        list of dict
            One entry: the :data:`BAR_STREAM` stream, its field list,
            and :data:`BAR_KEY_FIELDS` as ``primary_key``.

        Raises
        ------
        AssetError
            On any invalid knob.
        """
        self.resolve_knobs(config)
        return [{
            "stream": BAR_STREAM,
            "schema": {"fields": list(_FIELDS)},
            "primary_key": list(BAR_KEY_FIELDS),
        }]

    def read(self, config, streams, state, mode):
        """Emit SCHEMA, then cursor-filtered RECORDs, then one STATE.

        Parameters
        ----------
        config : dict
            The source config.
        streams : list of str
            The streams to pull; only :data:`BAR_STREAM` exists.
        state : dict
            The checkpoint for this (source, stream, mode), as the
            platform loaded it; ``{}`` on a first pull.
        mode : str
            :data:`BACKFILL_MODE` or :data:`LIVE_MODE` — it decides only
            where a pull with NO cursor starts (see the module docs).

        Yields
        ------
        dict
            One SCHEMA message, then one RECORD per bar strictly newer
            than the cursor, then one STATE carrying the new cursor.

        Raises
        ------
        AssetError
            On a non-dict state, an empty stream list, an unknown
            stream, or any invalid knob.
        """
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        knobs = self.resolve_knobs(config)
        new_state = {k: dict(v) for k, v in state.items()}

        for stream in streams:
            if stream != BAR_STREAM:
                raise AssetError(
                    [f"unknown stream {stream!r} — discovered: "
                     f"[{BAR_STREAM!r}]"]
                )
            cursor = state.get(stream, {}).get("cursor", "")
            cursor_dt = parse_utc(cursor) if cursor else None

            yield {"protocol": PROTOCOL, "type": "SCHEMA", "stream": stream,
                   "schema": {"fields": list(_FIELDS)}}

            emitted_max = cursor
            emitted_max_dt = cursor_dt
            start_dt, end_dt = self._window(knobs, cursor, mode)
            if start_dt is not None:
                for _symbol, data in self._fetch(knobs, start_dt, end_dt):
                    eff = data["ts"]
                    eff_dt = parse_utc(eff)
                    if cursor_dt is not None and eff_dt <= cursor_dt:
                        continue  # already durable per the checkpoint
                    yield {"protocol": PROTOCOL, "type": "RECORD",
                           "stream": stream, "effective_date": eff,
                           "kind": "observation", "data": data}
                    if emitted_max_dt is None or eff_dt > emitted_max_dt:
                        emitted_max, emitted_max_dt = eff, eff_dt
            new_state.setdefault(stream, {})["cursor"] = emitted_max

        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}
