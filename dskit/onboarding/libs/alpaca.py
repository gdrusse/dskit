"""Alpaca Market Data stock bars through the onboarding connector contract.

The pack owns Alpaca's transport, bar vocabulary, checkpoint semantics, and
free-tier SIP lag. Projects declare symbols, dates, tape, adjustment, interval,
and environment-variable names. Credential material never enters config. The
vendor SDK remains inside methods so onboarding stays importable without it.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

from ..base import AssetError, MODES, parse_utc
from ..connector import PROTOCOL, Connector

__all__ = [
    "BACKFILL_MODE",
    "BAR_FIELDS",
    "BAR_INTERVAL",
    "BAR_KEY_FIELDS",
    "BAR_STREAM",
    "DEFAULT_ADJUSTMENT",
    "DEFAULT_CHUNK_DAYS",
    "DEFAULT_END",
    "DEFAULT_FEED",
    "DEFAULT_KEY_ENV",
    "DEFAULT_LIVE_LOOKBACK_MINUTES",
    "DEFAULT_SECRET_ENV",
    "LIVE_MODE",
    "TIMEFRAME_UNITS",
    "AlpacaBarsConnector",
    "bar_timeframe",
    "resolve_credentials",
]

BACKFILL_MODE, LIVE_MODE = MODES
BAR_STREAM = "bars"
BAR_KEY_FIELDS = ("symbol", "ts")
BAR_FIELDS = (
    "symbol", "ts", "open", "high", "low", "close", "volume",
    "trade_count", "vwap",
)
TIMEFRAME_UNITS = ("Minute", "Hour", "Day", "Week", "Month")
BAR_INTERVAL = (1, "Minute")
DEFAULT_LIVE_LOOKBACK_MINUTES = 1440
DEFAULT_CHUNK_DAYS = 31
DEFAULT_FEED = "sip"
DEFAULT_ADJUSTMENT = "raw"
DEFAULT_END = ""
DEFAULT_KEY_ENV = "APCA_API_KEY_ID"
DEFAULT_SECRET_ENV = "APCA_API_SECRET_KEY"

_FEEDS = ("sip", "iex")
_ADJUSTMENTS = ("raw", "split", "dividend", "all")
_SIP_FEED, _IEX_FEED = _FEEDS
_SIP_LAG = timedelta(minutes=16)
_SIP_LAG_MINUTES = _SIP_LAG.total_seconds() / 60


def bar_timeframe(interval=None):
    """Build Alpaca's timeframe object for a resolved interval.

    Parameters
    ----------
    interval : sequence or None
        ``(amount, unit)``; ``None`` uses :data:`BAR_INTERVAL`.

    Returns
    -------
    alpaca.data.timeframe.TimeFrame
        Vendor request interval.

    Raises
    ------
    ImportError
        If the optional ``alpaca-py`` package is unavailable.
    """
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    amount, unit = BAR_INTERVAL if interval is None else interval
    return TimeFrame(amount, TimeFrameUnit[unit])


def resolve_credentials(knobs, lookup=None):
    """Resolve a named Alpaca key pair without exposing it in config.

    Parameters
    ----------
    knobs : dict
        Resolved knobs containing ``key_env`` and ``secret_env`` names.
    lookup : callable or None
        Environment lookup; defaults to ``os.environ.get``.

    Returns
    -------
    tuple
        Key id and secret value.

    Raises
    ------
    AssetError
        If either named value is absent or empty.
    """
    lookup = os.environ.get if lookup is None else lookup
    pairs = (
        (knobs["key_env"], lookup(knobs["key_env"], "")),
        (knobs["secret_env"], lookup(knobs["secret_env"], "")),
    )
    missing = [name for name, value in pairs if not value]
    if missing:
        raise AssetError(
            [f"Alpaca environment variable(s) {missing} are missing or empty"]
        )
    return pairs[0][1], pairs[1][1]


def _timeframe_problems(value):
    """Return all problems with an Alpaca timeframe declaration."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return [f"config.timeframe must be an [amount, unit] pair, got {value!r}"]
    amount, unit = value
    problems = []
    if (
        isinstance(amount, bool)
        or not isinstance(amount, int)
        or amount < 1
    ):
        problems.append(
            f"config.timeframe amount must be an int >= 1, got {amount!r}"
        )
    if unit not in TIMEFRAME_UNITS:
        problems.append(
            f"config.timeframe unit must be one of {TIMEFRAME_UNITS}, got {unit!r}"
        )
    return problems


class AlpacaBarsConnector(Connector):
    """Alpaca v2 stock bars with mode-keyed checkpoint semantics.

    Parameters
    ----------
    None
        The connector is stateless; every setting comes from config.

    Examples
    --------
    Discover a one-minute SIP bar stream without importing the SDK::

        connector = AlpacaBarsConnector()
        streams = connector.discover({
            "symbols": ["AAPL"],
            "start": "2026-01-01",
            "feed": "sip",
            "adjustment": "raw",
        })
    """

    def spec(self):
        """Declare the default-deny Alpaca configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {"params": {
            "symbols": {
                "required": True,
                "notes": "Non-empty list of stock symbols.",
            },
            "start": {
                "required": True,
                "notes": "Earliest ISO date or datetime to fetch.",
            },
            "end": {
                "notes": "Optional EXCLUSIVE ISO upper bound on the fetch "
                         "window; absent means 'up to now'. A pull never "
                         "reads a bar stamped at or after it, so a study "
                         "with a hard data cut declares the cut here rather "
                         "than trimming afterwards.",
            },
            "feed": {
                "notes": f"Market-data tape in {_FEEDS}; default {DEFAULT_FEED}.",
            },
            "adjustment": {
                "notes": "Corporate-action adjustment in "
                         f"{_ADJUSTMENTS}; default {DEFAULT_ADJUSTMENT}. "
                         "The default is UNADJUSTED: a split inside the "
                         "window then reads as a price move. Declare it "
                         "explicitly so the stored series says which scale "
                         "it is on.",
            },
            "timeframe": {
                "notes": "Bar interval [amount, unit], where unit is "
                         f"{TIMEFRAME_UNITS}; default {BAR_INTERVAL}.",
            },
            "live_lookback_minutes": {
                "notes": "First live pull's bounded history window; default "
                         f"{DEFAULT_LIVE_LOOKBACK_MINUTES}. On SIP it must exceed "
                         f"the {_SIP_LAG_MINUTES:g}-minute lag.",
            },
            "chunk_days": {
                "notes": "Maximum date span per SDK request, bounding its "
                         f"in-memory BarSet; default {DEFAULT_CHUNK_DAYS}.",
            },
            "key_env": {
                "secret": True,
                "notes": "Environment-variable name holding the Alpaca key id; "
                         f"default {DEFAULT_KEY_ENV}.",
            },
            "secret_env": {
                "secret": True,
                "notes": "Environment-variable name holding the Alpaca secret; "
                         f"default {DEFAULT_SECRET_ENV}.",
            },
        }}

    def resolve_knobs(self, config):
        """Validate config values and apply the pack's defaults.

        Parameters
        ----------
        config : dict
            Connector configuration after platform-reserved keys are removed.

        Returns
        -------
        dict
            Fully resolved Alpaca request knobs.

        Raises
        ------
        AssetError
            Listing all malformed values.
        """
        if not isinstance(config, dict):
            raise AssetError(
                [f"config must be a dict, got {type(config).__name__}"]
            )
        problems = []
        symbols = config.get("symbols")
        if (
            not isinstance(symbols, list)
            or not symbols
            or not all(isinstance(symbol, str) and symbol for symbol in symbols)
        ):
            problems.append(
                f"config.symbols must be a non-empty list of strings, got {symbols!r}"
            )
        start = config.get("start")
        if not isinstance(start, str) or not start:
            problems.append(f"config.start must be an ISO string, got {start!r}")
        end = config.get("end", DEFAULT_END)
        if not isinstance(end, str):
            problems.append(
                f"config.end must be an ISO string or absent, got {end!r}"
            )
        feed = config.get("feed", DEFAULT_FEED)
        if feed not in _FEEDS:
            problems.append(f"config.feed must be one of {_FEEDS}, got {feed!r}")
        adjustment = config.get("adjustment", DEFAULT_ADJUSTMENT)
        if adjustment not in _ADJUSTMENTS:
            problems.append(
                f"config.adjustment must be one of {_ADJUSTMENTS}, "
                f"got {adjustment!r}"
            )
        timeframe = config.get("timeframe", BAR_INTERVAL)
        problems.extend(_timeframe_problems(timeframe))
        lookback = config.get(
            "live_lookback_minutes", DEFAULT_LIVE_LOOKBACK_MINUTES
        )
        chunk_days = config.get("chunk_days", DEFAULT_CHUNK_DAYS)
        if (
            isinstance(lookback, bool)
            or not isinstance(lookback, (int, float))
            or not math.isfinite(lookback)
            or lookback <= 0
        ):
            problems.append(
                "config.live_lookback_minutes must be a positive finite number, "
                f"got {lookback!r}"
            )
        elif feed == _SIP_FEED and lookback <= _SIP_LAG_MINUTES:
            problems.append(
                "config.live_lookback_minutes must exceed the "
                f"{_SIP_LAG_MINUTES:g}-minute SIP lag, got {lookback!r}"
            )
        if (
            isinstance(chunk_days, bool)
            or not isinstance(chunk_days, int)
            or chunk_days < 1
        ):
            problems.append(
                f"config.chunk_days must be an int >= 1, got {chunk_days!r}"
            )
        key_env = config.get("key_env", DEFAULT_KEY_ENV)
        secret_env = config.get("secret_env", DEFAULT_SECRET_ENV)
        for name, value in (("key_env", key_env), ("secret_env", secret_env)):
            if not isinstance(value, str) or not value:
                problems.append(
                    f"config.{name} must be a non-empty environment-variable name"
                )
        if problems:
            raise AssetError(problems)
        start_dt = parse_utc(start)
        if end and parse_utc(end) <= start_dt:
            raise AssetError(
                [f"config.end {end!r} must be after config.start {start!r}"]
            )
        amount, unit = timeframe
        return {
            "symbols": list(symbols),
            "start": start,
            "end": end,
            "feed": feed,
            "adjustment": adjustment,
            "timeframe": (amount, unit),
            "live_lookback_minutes": lookback,
            "chunk_days": chunk_days,
            "key_env": key_env,
            "secret_env": secret_env,
        }

    def _credentials(self, knobs):
        """Resolve the named key pair at the vendor boundary."""
        return resolve_credentials(knobs)

    def _window(self, knobs, cursor, mode):
        """Return the requested start/end datetimes, or two ``None`` values."""
        start = parse_utc(knobs["start"])
        if cursor:
            durable = parse_utc(cursor)
            if durable > start:
                start = durable
        elif mode == LIVE_MODE:
            floor = datetime.now(timezone.utc) - timedelta(
                minutes=knobs["live_lookback_minutes"]
            )
            if floor > start:
                start = floor
        end = datetime.now(timezone.utc)
        if knobs["feed"] == _SIP_FEED:
            end -= _SIP_LAG
        declared_end = knobs.get("end") or ""
        if declared_end:
            bound = parse_utc(declared_end)
            if bound < end:
                end = bound
        if end <= start:
            return None, None
        return start, end

    def _fetch(self, knobs, start, end):
        """Yield normalized rows from bounded SDK request windows."""
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest

        key, secret = self._credentials(knobs)
        client = StockHistoricalDataClient(key, secret)
        current = start
        while current < end:
            chunk_end = min(
                end, current + timedelta(days=knobs["chunk_days"])
            )
            request = StockBarsRequest(
                symbol_or_symbols=knobs["symbols"],
                timeframe=bar_timeframe(knobs["timeframe"]),
                start=current,
                end=chunk_end,
                feed=DataFeed(knobs["feed"]),
                adjustment=Adjustment(knobs["adjustment"]),
                limit=None,
            )
            bars = client.get_stock_bars(request)
            for symbol, series in sorted(bars.data.items()):
                for bar in series:
                    stamp = bar.timestamp.astimezone(timezone.utc)
                    if not current <= stamp < chunk_end:
                        continue
                    yield symbol, {
                        "symbol": symbol,
                        "ts": stamp.isoformat(),
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": float(bar.volume),
                        "trade_count": (
                            None if bar.trade_count is None
                            else int(bar.trade_count)
                        ),
                        "vwap": None if bar.vwap is None else float(bar.vwap),
                    }
            current = chunk_end

    def check(self, config):
        """Validate config, credentials, and one authenticated probe.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        None
            Silence means the provider answered.

        Raises
        ------
        AssetError
            If config, credentials, SDK loading, or the probe fails.
        """
        knobs = self.resolve_knobs(config)
        key, secret = self._credentials(knobs)
        try:
            from alpaca.data.enums import DataFeed
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestBarRequest

            client = StockHistoricalDataClient(key, secret)
            client.get_stock_latest_bar(StockLatestBarRequest(
                symbol_or_symbols=knobs["symbols"][:1],
                feed=DataFeed(_IEX_FEED),
            ))
        except Exception as exc:
            raise AssetError(
                ["Alpaca authentication probe failed; check credentials and network"]
            ) from exc

    def discover(self, config):
        """Describe the normalized bar stream without touching a vendor.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        list
            One stream declaration for ``bars``.

        Raises
        ------
        AssetError
            If config values are invalid.
        """
        knobs = self.resolve_knobs(config)
        return [{
            "stream": BAR_STREAM,
            "schema": {"fields": list(BAR_FIELDS)},
            "primary_key": list(BAR_KEY_FIELDS),
            "timeframe": list(knobs["timeframe"]),
        }]

    def read(self, config, streams, state, mode):
        """Emit schema, cursor-filtered bar records, and one checkpoint.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list
            Requested streams; only ``bars`` exists.
        state : dict
            Prior mode-keyed connector checkpoint.
        mode : str
            ``backfill`` or ``live``.

        Yields
        ------
        dict
            Onboarding protocol messages.

        Raises
        ------
        AssetError
            If arguments, config, or the vendor request fail.
        """
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        if mode not in MODES:
            raise AssetError([f"mode must be one of {MODES}, got {mode!r}"])
        knobs = self.resolve_knobs(config)
        new_state = {key: dict(value) for key, value in state.items()}
        for stream in streams:
            if stream != BAR_STREAM:
                raise AssetError(
                    [f"unknown stream {stream!r}; discovered: {[BAR_STREAM]}"]
                )
            cursor = state.get(stream, {}).get("cursor", "")
            cursor_dt = parse_utc(cursor) if cursor else None
            yield {
                "protocol": PROTOCOL,
                "type": "SCHEMA",
                "stream": stream,
                "schema": {"fields": list(BAR_FIELDS)},
            }
            emitted, emitted_dt = cursor, cursor_dt
            start, end = self._window(knobs, cursor, mode)
            if start is not None:
                try:
                    rows = self._fetch(knobs, start, end)
                    for _symbol, data in rows:
                        effective = data["ts"]
                        effective_dt = parse_utc(effective)
                        if cursor_dt is not None and effective_dt <= cursor_dt:
                            continue
                        yield {
                            "protocol": PROTOCOL,
                            "type": "RECORD",
                            "stream": stream,
                            "effective_date": effective,
                            "kind": "observation",
                            "data": data,
                        }
                        if emitted_dt is None or effective_dt > emitted_dt:
                            emitted, emitted_dt = effective, effective_dt
                except AssetError:
                    raise
                except Exception as exc:
                    raise AssetError(
                        ["Alpaca bars request failed; check provider access and network"]
                    ) from exc
            new_state.setdefault(stream, {})["cursor"] = emitted
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}
