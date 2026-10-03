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
from ..connector import PROTOCOL, Connector, backoff

from .localtables import PinnedArchiveConnector

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
    "ACTIVITY_STREAMS",
    "OPTION_CONTRACT_STREAM",
    "OPTION_BAR_STREAM",
    "AlpacaBarsConnector",
    "AlpacaOptionActivityConnector",
    "AlpacaOptionArchiveConnector",
    "AlpacaOptionFetchConnector",
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
            "symbols": symbols,
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




class AlpacaOptionArchiveConnector(PinnedArchiveConnector):
    """Normalize saved Alpaca option archives without provider requests.

    Parameters
    ----------
    None
        Paths, pins and observation clock are declared in source JSON.

    Examples
    --------
    Build the standard connector::

        reader = AlpacaOptionArchiveConnector()
    """

    STREAM_KEYS = {"contracts": ("contract",), "bars": ("contract", "quote_date"),
                   "snapshots": ("contract", "quote_date")}

    @staticmethod
    def _number(value):
        if isinstance(value, bool):
            return None
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _identity(contract):
        import re
        from datetime import date
        found = re.fullmatch(r"([A-Z0-9.]+)([0-9]{6})([CP])([0-9]{8})", contract)
        if found is None:
            raise AssetError([f"invalid OCC identity: {contract}"])
        root, expiry, right, strike = found.groups()
        expiry = date(2000+int(expiry[:2]), int(expiry[2:4]), int(expiry[4:])).isoformat()
        return {"contract": contract, "symbol": root, "root_symbol": root,
                "expiry": expiry, "type": {"C": "call", "P": "put"}[right],
                "strike": int(strike)/1000.}

    def _terms(self):
        cached = self._archive_cache.get("contracts")
        if cached is not None:
            return cached
        rows = []
        if "contracts" in self._archive_config["files"]:
            doc = self.decode(self.archive_bytes("contracts"))
            if not isinstance(doc, dict) or not isinstance(doc.get("contracts"), list):
                raise AssetError(["contracts archive requires a contracts list container"])
            rows = doc["contracts"]
        result = {}
        for raw in rows:
            item = self._identity(raw["symbol"])
            number = self._number
            matches = (item["expiry"] == raw.get("expiration_date")
                       and item["type"] == raw.get("type")
                       and item["strike"] == number(raw.get("strike_price"))
                       and item["root_symbol"] == raw.get("root_symbol"))
            if not matches:
                raise AssetError([f"contract metadata disagrees with OCC: {raw['symbol']}"])
            item.update(symbol=raw.get("underlying_symbol"), multiplier=number(
                raw.get("multiplier")), contract_size=number(raw.get("size")),
                style=raw.get("style"), contract_terms_status="metadata_present",
                metadata_asof=self._archive_config["archive_observed_at"],
                contract_source_sha256=self._archive_config["files"]["contracts"]["sha256"],
                effective_at=self._archive_config["archive_observed_at"])
            if not item["symbol"]:
                raise AssetError(["contract has no underlying_symbol"])
            prior = result.get(item["contract"])
            if prior is not None and prior != item:
                raise AssetError(["conflicting duplicate contract metadata"])
            result[item["contract"]] = item
        self._archive_cache["contracts"] = result
        return result

    def _base(self, contract):
        terms = self._terms().get(contract)
        if terms is not None:
            return dict(terms)
        return {**self._identity(contract), "multiplier": None, "contract_size": None,
                "style": None, "contract_terms_status": "unverified_contract_terms",
                "metadata_asof": None, "contract_source_sha256": None}

    def _session(self, stamp):
        from zoneinfo import ZoneInfo
        return parse_utc(stamp).astimezone(
            ZoneInfo(self._archive_config["session_timezone"])).date().isoformat()

    def _contracts(self, raw):
        yield from self._terms().values()

    def _bars(self, raw):
        for line in raw.splitlines():
            if not line.strip():
                continue
            page = self.decode(line)
            if "meta" in page:
                continue
            for contract, bars in page["bars"].items():
                for bar in bars:
                    row = self._base(contract)
                    if page.get("expiry", row["expiry"]) != row["expiry"]:
                        raise AssetError(["bar-page expiry disagrees with contract"])
                    stamp = bar["t"]
                    row.update(effective_at=stamp, source_timestamp=stamp,
                               quote_date=self._session(stamp), quote_timestamp=None,
                               price_basis="trade_close", timestamp_basis="session_label",
                               bid=None, ask=None, bid_size=None, ask_size=None,
                               implied_volatility=None, open_interest=None)
                    for target, source in (("open","o"),("high","h"),("low","l"),
                                           ("close","c"),("volume","v"),
                                           ("trade_count","n"),("vwap","vw")):
                        row[target] = self._number(bar.get(source))
                    valid = (all(row[k] is not None for k in
                                 ("open","high","low","close","volume"))
                             and 0 < row["low"] <= min(row["open"],row["close"])
                             <= max(row["open"],row["close"]) <= row["high"]
                             and row["volume"] >= 0)
                    row["mark"] = row["close"] if valid else None
                    row["observation_reasons"] = [] if valid else ["invalid_trade_bar"]
                    yield row

    def _snapshots(self, raw):
        doc = self.decode(raw)
        if not isinstance(doc, dict) or not ("pages" in doc or "snapshots" in doc):
            raise AssetError(["snapshot archive requires a pages or snapshots container"])
        pages = doc.get("pages", [doc])
        if not isinstance(pages, list):
            raise AssetError(["snapshot pages container must be a list"])
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get("snapshots"), dict):
                raise AssetError(["snapshot page requires a snapshots mapping container"])
            for contract, snap in page["snapshots"].items():
                row = self._base(contract)
                quote = snap.get("latestQuote") or {}
                stamp = quote.get("t")
                row.update(effective_at=stamp or self._archive_config["archive_observed_at"],
                           source_timestamp=stamp, quote_timestamp=stamp,
                           quote_date=self._session(stamp) if stamp else None,
                           price_basis="indicative_quote", timestamp_basis="quote_time",
                           bid=self._number(quote.get("bp")), ask=self._number(quote.get("ap")),
                           bid_size=self._number(quote.get("bs")),
                           ask_size=self._number(quote.get("as")),
                           implied_volatility=self._number(snap.get("impliedVolatility")),
                           open_interest=None, observation_reasons=[])
                bid, ask = row["bid"], row["ask"]
                valid = bid is not None and ask is not None and 0 <= bid <= ask and ask > 0
                row["mark"] = (bid+ask)/2 if valid else None
                if not valid:
                    row["observation_reasons"].append("invalid_quote")
                if stamp is None:
                    row["observation_reasons"].append("missing_quote_timestamp")
                yield row

    def normalize(self, stream, raw):
        """Decode a provider stream into canonical option observations.

        Parameters
        ----------
        stream : str
            contracts, bars or snapshots.
        raw : bytes
            Verified decompressed archive.

        Returns
        -------
        iterable
            Flat, source-labelled records.
        """
        readers = {"contracts": self._contracts, "bars": self._bars,
                   "snapshots": self._snapshots}
        return readers[stream](raw)


OPTION_CONTRACT_STREAM = "contracts"
OPTION_BAR_STREAM = "bars"
_OPTION_STREAMS = (OPTION_CONTRACT_STREAM, OPTION_BAR_STREAM)
_OPTION_STREAM_KEYS = {
    OPTION_CONTRACT_STREAM: ("contract",),
    OPTION_BAR_STREAM: ("contract", "quote_date"),
}
_DEFAULT_OPTION_MULTIPLIER = 100
_DEFAULT_OPTION_STATUS = "inactive"
_DEFAULT_MAX_SYMBOLS_PER_REQUEST = 100


class _AlpacaOptionTransport(Connector):
    """Shared Alpaca option-connector plumbing: credentials, timeout, retry.

    Parameters
    ----------
    None
        Abstract; subclasses declare ``spec``, ``discover`` and ``read``.

    Examples
    --------
    Subclass it for a connector that talks to the option endpoints::

        class MyOptionPull(_AlpacaOptionTransport):
            ...
    """

    @staticmethod
    def _symbols_knob(config, errors, required):
        """Resolve ``symbols`` or ``symbols_file`` + ``symbols_key`` to a list (or None).

        Exactly one source may be declared. A file is JSON holding the list
        under ``symbols_key``. Its path expands ``~`` and ``$VAR`` and must
        then be absolute (a working-directory-relative path would name a
        different file per launch directory), and ``symbols_sha256`` (sha256
        of the file's bytes) is REQUIRED and verified, so the stored source
        config pins the list itself and an edited file refuses instead of
        mixing two lists inside one source.
        """
        import hashlib
        import json
        import os

        listed = config.get("symbols")
        path, key = config.get("symbols_file"), config.get("symbols_key")
        digest = config.get("symbols_sha256")
        if path is None and digest is not None:
            errors.append("config.symbols_sha256 needs symbols_file")
        if listed is not None and (path is not None or key is not None):
            errors.append("config.symbols excludes symbols_file/symbols_key")
        elif (path is None) != (key is None):
            errors.append("config.symbols_file and config.symbols_key go together")
        elif path is not None:
            if not isinstance(digest, str) or not digest:
                errors.append("config.symbols_file requires config.symbols_sha256")
                return None
            try:
                path = os.path.expandvars(os.path.expanduser(path))
                if not os.path.isabs(path):
                    errors.append("config.symbols_file must be absolute after ~ and "
                                  "$VAR expansion")
                    return None
                with open(path, "rb") as handle:
                    raw = handle.read()
                doc = json.loads(raw.decode("utf-8"))
            except (OSError, TypeError, ValueError) as exc:
                errors.append(f"config.symbols_file unreadable: {exc}")
                return None
            if hashlib.sha256(raw).hexdigest() != digest:
                errors.append(f"config.symbols_sha256 does not match {path}: the "
                              "list changed since this config was written")
                return None
            listed = doc.get(key) if isinstance(doc, dict) else None
            if listed is None:
                errors.append(f"config.symbols_key {key!r} not found in {path}")
                return None
        if listed is None and not required:
            return None
        if (not isinstance(listed, (list, tuple)) or not listed
                or any(not isinstance(x, str) or not x for x in listed)
                or len(set(listed)) != len(listed)):
            errors.append("config.symbols must be a non-empty list of distinct symbols")
            return None
        return list(listed)

    @staticmethod
    def _transport_knobs(config, errors):
        """Validate the timeout, retry and credential-name knobs shared by every option pull."""
        timeout = config.get("request_timeout_seconds", 60)
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or timeout < 1):
            errors.append("config.request_timeout_seconds must be a positive number")
        max_retries = config.get("max_retries", 5)
        if (isinstance(max_retries, bool) or not isinstance(max_retries, int)
                or max_retries < 1):
            errors.append("config.max_retries must be an int >= 1")
        key_env = config.get("key_env", DEFAULT_KEY_ENV)
        secret_env = config.get("secret_env", DEFAULT_SECRET_ENV)
        for name, value in (("key_env", key_env), ("secret_env", secret_env)):
            if not isinstance(value, str) or not value:
                errors.append(f"config.{name} must be a non-empty env-var name")
        return {"request_timeout_seconds": timeout, "max_retries": max_retries,
                "key_env": key_env, "secret_env": secret_env}

    def _credentials(self, knobs):
        """Resolve the named key pair at the vendor boundary."""
        return resolve_credentials(knobs)

    @staticmethod
    def _set_socket_timeout(seconds):
        import socket
        previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(seconds)
        return previous

    @staticmethod
    def _restore_socket_timeout(previous):
        import socket
        socket.setdefaulttimeout(previous)

    def _contract_client(self, key, secret):
        from alpaca.trading.client import TradingClient
        return TradingClient(key, secret)

    def _vendor(self, knobs, call):
        """Run one vendor call, retrying timeouts with exponential backoff."""
        import time

        for attempt in range(1, knobs["max_retries"] + 1):
            try:
                return call()
            except AssetError:
                raise
            except Exception:
                if attempt == knobs["max_retries"]:
                    raise
                time.sleep(backoff(attempt))


class AlpacaOptionFetchConnector(_AlpacaOptionTransport):
    """Fetch Alpaca option contracts and daily trade bars.

    Replicates the AMZN historical pull (ADR-0213) for a config-declared
    ``symbols`` list: inactive contracts from the option-contracts endpoint and
    daily trade bars over the per-expiry DTE window. Emits the same
    ``contracts``/``bars`` stream vocabulary and row fields as
    :class:`AlpacaOptionArchiveConnector`, so the stock-options panel flow
    consumes fetched rows identically to a pinned archive. Current indicative
    quotes/snapshots are a separate pull (as they were in the archive) and are
    out of scope here; the free tier returns trade bars only, so fills stay out
    of scope too.

    Credential material is named by environment variables (``key_env`` /
    ``secret_env``), never held in config. Heavy imports stay inside methods.

    Parameters
    ----------
    None
        The connector is stateless; every setting comes from config.

    Examples
    --------
    Declare the pull without importing the SDK::

        connector = AlpacaOptionFetchConnector()
        streams = connector.discover({
            "symbols": ["AMZN", "MSFT"],
            "start": "2024-02-01",
            "end": "2026-09-30",
            "dte_min": 30, "dte_max": 45,
        })
    """

    def spec(self):
        """Declare the default-deny Alpaca option-fetch configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {"params": {
            "symbols": {
                "notes": "Non-empty list of underlying symbols to pull; or "
                         "declare symbols_file + symbols_key instead.",
            },
            "symbols_file": {
                "notes": "JSON file holding the symbol list under symbols_key "
                         "(path relative to the working directory), so a "
                         "ranking output drives the pull without restating it. "
                         "Absolute after ~/$VAR expansion; needs symbols_sha256.",
            },
            "symbols_key": {"notes": "Key of the list inside symbols_file."},
            "symbols_sha256": {"notes": "REQUIRED with symbols_file: sha256 of the file's "
                                        "bytes; a changed file refuses."},
            "start": {
                "required": True,
                "notes": "Earliest ISO date (inclusive) for contract expiration.",
            },
            "end": {
                "notes": "Optional INCLUSIVE ISO upper bound on contract expiration.",
            },
            "dte_min": {
                "notes": "Fewest days-to-expiry a bar is fetched for; default 30.",
            },
            "dte_max": {
                "notes": "Most days-to-expiry a bar is fetched for; default 45.",
            },
            "multiplier": {
                "notes": f"Standard contract multiplier gate; default "
                         f"{_DEFAULT_OPTION_MULTIPLIER}.",
            },
            "status": {
                "notes": f"Contract status filter; default {_DEFAULT_OPTION_STATUS!r}.",
            },
            "feed": {
                "notes": "Option data feed (opra | indicative); default indicative.",
            },
            "timeframe": {
                "notes": "Bar interval [amount, unit]; default [1, 'Day'].",
            },
            "max_symbols_per_request": {
                "notes": "Bar symbols per request; default "
                         f"{_DEFAULT_MAX_SYMBOLS_PER_REQUEST}.",
            },
            "request_timeout_seconds": {
                "notes": "Per-request socket timeout; default 60. A hung "
                         "provider response fails fast instead of stalling "
                         "the pull.",
            },
            "max_retries": {
                "notes": "Retries per vendor request on timeout/connection "
                         "failure, with exponential backoff; default 5.",
            },
            "key_env": {
                "secret": True,
                "notes": f"Environment variable naming the Alpaca key; "
                         f"default {DEFAULT_KEY_ENV}.",
            },
            "secret_env": {
                "secret": True,
                "notes": f"Environment variable naming the Alpaca secret; "
                         f"default {DEFAULT_SECRET_ENV}.",
            },
        }}

    def resolve_knobs(self, config):
        """Resolve and validate every knob, defaulting optional ones."""
        errors = []
        symbols = self._symbols_knob(config, errors, required=True)
        start = config.get("start")
        if not isinstance(start, str) or not start:
            errors.append("config.start must be an ISO date string")
        end = config.get("end", "")
        if end and (not isinstance(end, str) or not end):
            errors.append("config.end must be an ISO date string or absent")
        dte_min = config.get("dte_min", 30)
        dte_max = config.get("dte_max", 45)
        for name, value in (("dte_min", dte_min), ("dte_max", dte_max)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors.append(f"config.{name} must be an int >= 1")
        if not isinstance(dte_min, bool) and not isinstance(dte_max, bool) \
                and isinstance(dte_min, int) and isinstance(dte_max, int) \
                and dte_min > dte_max:
            errors.append("config.dte_min must not exceed config.dte_max")
        multiplier = config.get("multiplier", _DEFAULT_OPTION_MULTIPLIER)
        if (isinstance(multiplier, bool) or not isinstance(multiplier, int)
                or multiplier < 1):
            errors.append("config.multiplier must be a positive int")
        status = config.get("status", _DEFAULT_OPTION_STATUS)
        if not isinstance(status, str) or not status:
            errors.append("config.status must be a non-empty string")
        timeframe = config.get("timeframe", [1, "Day"])
        errors.extend(_timeframe_problems(timeframe))
        max_symbols = config.get("max_symbols_per_request",
                                 _DEFAULT_MAX_SYMBOLS_PER_REQUEST)
        if (isinstance(max_symbols, bool) or not isinstance(max_symbols, int)
                or max_symbols < 1):
            errors.append("config.max_symbols_per_request must be a positive int")
        transport = self._transport_knobs(config, errors)
        if errors:
            raise AssetError(errors)
        return {
            "symbols": list(symbols),
            "start": start,
            "end": end or None,
            "dte_min": dte_min,
            "dte_max": dte_max,
            "multiplier": multiplier,
            "status": status,
            "feed": config.get("feed", "indicative"),
            "timeframe": list(timeframe),
            "max_symbols_per_request": max_symbols,
            **transport,
        }

    def check(self, config):
        """Validate config, credentials, and one authenticated probe.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        None

        Raises
        ------
        AssetError
            If config, credentials, SDK loading, or the probe fails.
        """
        knobs = self.resolve_knobs(config)
        key, secret = self._credentials(knobs)
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import GetOptionContractsRequest

            client = TradingClient(key, secret)
            client.get_option_contracts(GetOptionContractsRequest(
                underlying_symbols=knobs["symbols"][:1],
                status=knobs["status"], limit=1))
        except Exception as exc:
            raise AssetError(
                ["Alpaca option-contracts probe failed; check credentials, "
                 "network and option-data access"]) from exc

    def discover(self, config):
        """Describe the two normalized option streams without a vendor.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        list
            One stream declaration per option stream.

        Raises
        ------
        AssetError
            If config values are invalid.
        """
        self.resolve_knobs(config)
        out = []
        for stream in _OPTION_STREAMS:
            out.append({
                "stream": stream,
                "schema": {"fields": []},
                "primary_key": list(_OPTION_STREAM_KEYS[stream]),
            })
        return out

    def read(self, config, streams, state, mode):
        """Emit schemas, normalized records and one checkpoint.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list
            Requested streams.
        state : dict
            Prior mode-keyed connector checkpoint; ``{}`` on first pull.
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
        unknown = [s for s in streams if s not in _OPTION_STREAMS]
        if unknown:
            raise AssetError([f"unknown stream(s) {unknown}; discovered: "
                              f"{list(_OPTION_STREAMS)}"])
        knobs = self.resolve_knobs(config)
        key, secret = self._credentials(knobs)
        for stream in streams:
            yield {
                "protocol": PROTOCOL, "type": "SCHEMA",
                "stream": stream, "schema": {"fields": []},
            }
        previous_timeout = self._set_socket_timeout(knobs["request_timeout_seconds"])
        try:
            contracts = self._eligible_contracts(key, secret, knobs)
            if OPTION_CONTRACT_STREAM in streams:
                yield from self._contracts(contracts)
            if OPTION_BAR_STREAM in streams:
                yield from self._bars(key, secret, knobs, contracts)
        except AssetError:
            raise
        except Exception as exc:
            raise AssetError(
                ["Alpaca option request failed or timed out; check network and "
                 f"provider access ({exc})"]) from exc
        finally:
            self._restore_socket_timeout(previous_timeout)
        new_state = {k: dict(v) for k, v in state.items()}
        new_state.setdefault("last_pull", {})["at"] = datetime.now(
            timezone.utc).isoformat()
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}

    def _eligible_contracts(self, key, secret, knobs):
        """Return the contract dicts that pass the DTE/multiplier/root gates."""
        from alpaca.trading.requests import GetOptionContractsRequest

        client = self._contract_client(key, secret)
        result = {}
        for symbol in knobs["symbols"]:
            page_token = None
            while True:
                request = GetOptionContractsRequest(
                    underlying_symbols=[symbol], status=knobs["status"],
                    expiration_date_gte=knobs["start"],
                    expiration_date_lte=knobs["end"],
                    limit=1000, page_token=page_token)
                response = self._vendor(knobs, lambda: client.get_option_contracts(request))
                for contract in response.option_contracts or []:
                    item = AlpacaOptionArchiveConnector._identity(contract.symbol)
                    size = AlpacaOptionArchiveConnector._number(contract.size)
                    if (size is None or size != float(knobs["multiplier"])
                            or item["root_symbol"] != symbol):
                        continue
                    if (item["expiry"] != str(contract.expiration_date)
                            or item["strike"] != float(contract.strike_price)):
                        continue
                    item.update(symbol=contract.underlying_symbol,
                                multiplier=size,
                                contract_size=size,
                                style=getattr(contract.style, "value", contract.style),
                                contract_terms_status="metadata_present",
                                effective_at=datetime.now(timezone.utc).replace(
                                    microsecond=0).isoformat())
                    result[item["contract"]] = item
                page_token = response.next_page_token
                if not page_token:
                    break
        return result

    def _contracts(self, contracts):
        """Emit the normalized contract stream."""
        for contract, item in sorted(contracts.items()):
            yield {
                "protocol": PROTOCOL, "type": "RECORD",
                "stream": OPTION_CONTRACT_STREAM,
                "effective_date": item["effective_at"],
                "kind": "observation",
                "data": item,
            }

    def _bars(self, key, secret, knobs, contracts):
        """Emit normalized daily trade bars over each expiry's DTE window."""
        from datetime import date, timedelta as _td

        from alpaca.data.enums import OptionsFeed
        from alpaca.data.historical import OptionHistoricalDataClient
        from alpaca.data.requests import OptionBarsRequest

        client = OptionHistoricalDataClient(key, secret)
        by_expiry = {}
        for item in contracts.values():
            by_expiry.setdefault(item["expiry"], []).append(item["contract"])
        for expiry, symbols in sorted(by_expiry.items()):
            exp = date.fromisoformat(expiry)
            start = exp - _td(days=knobs["dte_max"])
            end = exp - _td(days=knobs["dte_min"])
            for offset in range(0, len(symbols), knobs["max_symbols_per_request"]):
                batch = symbols[offset:offset + knobs["max_symbols_per_request"]]
                bars = self._vendor(knobs, lambda: client.get_option_bars(OptionBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=bar_timeframe(tuple(knobs["timeframe"])),
                    start=start, end=end,
                    feed=OptionsFeed(knobs["feed"]))))
                for contract in batch:
                    base = contracts[contract]
                    for bar in bars.data.get(contract, []):
                        yield self._bar_record(base, bar)

    def _bar_record(self, base, bar):
        stamp = bar.timestamp.astimezone(timezone.utc)
        number = AlpacaOptionArchiveConnector._number
        row = dict(base)
        row.update(effective_at=stamp.isoformat(), source_timestamp=stamp.isoformat(),
                   quote_date=stamp.date().isoformat(), quote_timestamp=None,
                   price_basis="trade_close", timestamp_basis="session_label",
                   bid=None, ask=None, bid_size=None, ask_size=None,
                   implied_volatility=None, open_interest=None)
        for target, value in (("open", bar.open), ("high", bar.high),
                              ("low", bar.low), ("close", bar.close),
                              ("volume", bar.volume),
                              ("trade_count", bar.trade_count),
                              ("vwap", bar.vwap)):
            row[target] = number(value)
        valid = (all(row[k] is not None for k in ("open", "high", "low", "close", "volume"))
                 and 0 < row["low"] <= min(row["open"], row["close"])
                 <= max(row["open"], row["close"]) <= row["high"]
                 and row["volume"] >= 0)
        row["mark"] = row["close"] if valid else None
        row["observation_reasons"] = [] if valid else ["invalid_trade_bar"]
        return {
            "protocol": PROTOCOL, "type": "RECORD",
            "stream": OPTION_BAR_STREAM,
            "effective_date": stamp.isoformat(),
            "kind": "observation",
            "data": row,
        }


UNDERLYING_STREAM = "underlyings"
ACTIVITY_STREAM = "chain_volume"
ACTIVITY_STREAMS = (UNDERLYING_STREAM, ACTIVITY_STREAM)
_ACTIVITY_KEYS = {UNDERLYING_STREAM: ("symbol",),
                  ACTIVITY_STREAM: ("symbol", "session")}
_DEFAULT_ACTIVITY_ATTRIBUTE = "has_options"
_DEFAULT_ASSET_CLASS = "us_equity"
_DEFAULT_ASSET_STATUS = "active"
_DEFAULT_CONTRACT_STATUSES = ("active", "inactive")
_CALENDAR_SPAN_PER_SESSION = 3


class AlpacaOptionActivityConnector(_AlpacaOptionTransport):
    """List optionable underlyings and total their option volume per session.

    Two streams. ``underlyings`` is every asset the trading API tags with
    ``attribute`` (default ``has_options``), optionally minus names matching
    ``exclude_name_regex``. ``chain_volume`` is, per underlying and trading
    session, the summed volume and the count of contracts that traded, taken
    over every contract of the underlying that was live in the window (the
    ``contract_statuses`` listings with expiry on or after the first session,
    so contracts that expired inside the window are counted). The window is the
    last ``sessions`` sessions of the exchange calendar up to ``end``.

    Contracts whose root differs from the underlying (adjusted-after-corporate
    action roots) are skipped, as in :class:`AlpacaOptionFetchConnector`.
    Volume is the chosen option feed's daily trade-bar volume; the default
    ``indicative`` feed is a subset of OPRA trades, so levels are partial while
    the ranking they support is what the pull is for.

    Parameters
    ----------
    None
        The connector is stateless; every setting comes from config.

    Examples
    --------
    Declare a 20-session activity pull without importing the SDK::

        connector = AlpacaOptionActivityConnector()
        streams = connector.discover({"sessions": 20, "end": "2026-10-02"})
    """

    def spec(self):
        """Declare the default-deny activity configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {"params": {
            "sessions": {"required": True,
                         "notes": "Most recent exchange sessions to total (int >= 1)."},
            "end": {"notes": "Inclusive ISO date the window ends on; default today (UTC). "
                             "Pin it so a re-run totals the same sessions."},
            "symbols": {"notes": "Optional non-empty list restricting the underlyings "
                                 "(a sample or a re-pull); default every listed asset."},
            "symbols_file": {"notes": "Alternative to symbols: JSON file holding the list "
                                      "under symbols_key (absolute after ~/$VAR expansion)."},
            "symbols_key": {"notes": "Key of the list inside symbols_file."},
            "symbols_sha256": {"notes": "REQUIRED with symbols_file: sha256 of the file's "
                                        "bytes; a changed file refuses."},
            "attribute": {"notes": "Trading-API asset attribute that marks an optionable "
                                   f"asset; default {_DEFAULT_ACTIVITY_ATTRIBUTE!r}."},
            "asset_class": {"notes": f"Asset class listed; default {_DEFAULT_ASSET_CLASS!r}."},
            "asset_status": {"notes": f"Asset status listed; default {_DEFAULT_ASSET_STATUS!r}."},
            "exclude_name_regex": {"notes": "Optional regex; assets whose name matches are "
                                            "dropped (Alpaca has no ETF flag, so a project "
                                            "that wants stocks only states its own pattern)."},
            "contract_statuses": {"notes": "Contract statuses listed per underlying; default "
                                           f"{list(_DEFAULT_CONTRACT_STATUSES)}. Include "
                                           "inactive to count contracts that expired in the window."},
            "feed": {"notes": "Option data feed (opra | indicative); default indicative."},
            "max_symbols_per_request": {"notes": "Bar symbols per request; default "
                                                 f"{_DEFAULT_MAX_SYMBOLS_PER_REQUEST}."},
            "request_timeout_seconds": {"notes": "Per-request socket timeout; default 60."},
            "max_retries": {"notes": "Retries per vendor request with backoff; default 5."},
            "key_env": {"secret": True, "notes": f"Env var naming the key; default {DEFAULT_KEY_ENV}."},
            "secret_env": {"secret": True,
                           "notes": f"Env var naming the secret; default {DEFAULT_SECRET_ENV}."},
        }}

    @staticmethod
    def _text_knob(config, name, default, errors):
        """Return a non-empty string knob or record an error."""
        value = config.get(name, default)
        if not isinstance(value, str) or not value:
            errors.append(f"config.{name} must be a non-empty string")
        return value

    def resolve_knobs(self, config):
        """Resolve and validate every knob, defaulting optional ones.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        dict
            The resolved knobs.

        Raises
        ------
        AssetError
            Naming every unusable or unknown knob.
        """
        import re
        from datetime import date

        errors = []
        unknown = sorted(set(config) - set(self.spec()["params"]) - {"notes"})
        if unknown:
            errors.append(f"unknown config key(s) {unknown}")
        sessions = config.get("sessions")
        if isinstance(sessions, bool) or not isinstance(sessions, int) or sessions < 1:
            errors.append("config.sessions must be an int >= 1")
        end = config.get("end")
        if end is not None:
            try:
                date.fromisoformat(end)
            except (TypeError, ValueError):
                errors.append("config.end must be an ISO date string or absent")
        symbols = self._symbols_knob(config, errors, required=False)
        pattern = config.get("exclude_name_regex")
        if pattern is not None:
            try:
                re.compile(pattern)
            except (TypeError, re.error):
                errors.append("config.exclude_name_regex must be a valid regex string")
        statuses = config.get("contract_statuses", list(_DEFAULT_CONTRACT_STATUSES))
        if (not isinstance(statuses, (list, tuple)) or not statuses
                or any(not isinstance(x, str) or not x for x in statuses)):
            errors.append("config.contract_statuses must be a non-empty list of strings")
        batch = config.get("max_symbols_per_request", _DEFAULT_MAX_SYMBOLS_PER_REQUEST)
        if isinstance(batch, bool) or not isinstance(batch, int) or batch < 1:
            errors.append("config.max_symbols_per_request must be a positive int")
        knobs = {
            "sessions": sessions, "end": end,
            "symbols": symbols,
            "attribute": self._text_knob(config, "attribute", _DEFAULT_ACTIVITY_ATTRIBUTE, errors),
            "asset_class": self._text_knob(config, "asset_class", _DEFAULT_ASSET_CLASS, errors),
            "asset_status": self._text_knob(config, "asset_status", _DEFAULT_ASSET_STATUS, errors),
            "feed": self._text_knob(config, "feed", "indicative", errors),
            "exclude_name_regex": pattern,
            "contract_statuses": list(statuses) if isinstance(statuses, (list, tuple)) else statuses,
            "max_symbols_per_request": batch,
            **self._transport_knobs(config, errors),
        }
        if errors:
            raise AssetError(errors)
        return knobs

    def check(self, config):
        """Validate config, credentials and one authenticated asset probe.

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
            self._contract_client(key, secret).get_clock()
        except Exception as exc:
            raise AssetError(
                ["Alpaca trading probe failed; check credentials and network"]) from exc

    def discover(self, config):
        """Describe the two activity streams without a vendor.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        list
            One declaration per stream.

        Raises
        ------
        AssetError
            If config values are invalid.
        """
        self.resolve_knobs(config)
        return [{"stream": name, "schema": {"fields": []},
                 "primary_key": list(_ACTIVITY_KEYS[name])}
                for name in ACTIVITY_STREAMS]

    def read(self, config, streams, state, mode):
        """Emit schemas, the requested streams' records and one checkpoint.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list
            Requested streams from ``underlyings`` / ``chain_volume``.
        state : dict
            Prior checkpoint; ``{}`` on first pull.
        mode : str
            ``backfill`` or ``live``.

        Yields
        ------
        dict
            Onboarding protocol messages.

        Raises
        ------
        AssetError
            If arguments, config, calendar or the vendor request fail.
        """
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        if mode not in MODES:
            raise AssetError([f"mode must be one of {MODES}, got {mode!r}"])
        unknown = [s for s in streams if s not in ACTIVITY_STREAMS]
        if unknown:
            raise AssetError([f"unknown stream(s) {unknown}; discovered: "
                              f"{list(ACTIVITY_STREAMS)}"])
        knobs = self.resolve_knobs(config)
        key, secret = self._credentials(knobs)
        for stream in streams:
            yield {"protocol": PROTOCOL, "type": "SCHEMA", "stream": stream,
                   "schema": {"fields": []}}
        previous = self._set_socket_timeout(knobs["request_timeout_seconds"])
        try:
            yield from self._emit(knobs, key, secret, streams)
        except AssetError:
            raise
        except Exception as exc:
            raise AssetError(
                ["Alpaca activity request failed or timed out; check network "
                 f"and provider access ({exc})"]) from exc
        finally:
            self._restore_socket_timeout(previous)
        new_state = {k: dict(v) for k, v in state.items()}
        new_state.setdefault("last_pull", {})["at"] = datetime.now(
            timezone.utc).isoformat()
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}

    def _emit(self, knobs, key, secret, streams):
        """Yield the requested streams' RECORD messages."""
        client = self._contract_client(key, secret)
        assets = self._underlyings(client, knobs)
        if UNDERLYING_STREAM in streams:
            stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            for row in assets:
                yield self._record(UNDERLYING_STREAM, stamp, row)
        if ACTIVITY_STREAM in streams:
            sessions = self._sessions(client, knobs)
            for row in self._chain_volume(client, key, secret, knobs, assets, sessions):
                yield self._record(ACTIVITY_STREAM, f"{row['session']}T00:00:00+00:00", row)

    @staticmethod
    def _record(stream, effective, data):
        return {"protocol": PROTOCOL, "type": "RECORD", "stream": stream,
                "effective_date": effective, "kind": "observation", "data": data}

    def _underlyings(self, client, knobs):
        """Return the listed, name-filtered underlyings sorted by symbol."""
        import re
        from alpaca.trading.enums import AssetClass, AssetStatus
        from alpaca.trading.requests import GetAssetsRequest

        request = GetAssetsRequest(
            status=AssetStatus(knobs["asset_status"]),
            asset_class=AssetClass(knobs["asset_class"]),
            attributes=knobs["attribute"])
        assets = self._vendor(knobs, lambda: client.get_all_assets(request))
        drop = knobs["exclude_name_regex"]
        drop = re.compile(drop) if drop else None
        only = None if knobs["symbols"] is None else set(knobs["symbols"])
        rows = {}
        for asset in assets:
            if only is not None and asset.symbol not in only:
                continue
            if drop is not None and drop.search(asset.name or ""):
                continue
            exchange = getattr(asset.exchange, "value", asset.exchange)
            rows[asset.symbol] = {"symbol": asset.symbol, "name": asset.name,
                                  "exchange": exchange}
        return [rows[s] for s in sorted(rows)]

    def _sessions(self, client, knobs):
        """Return the last ``sessions`` exchange-session dates up to ``end``, ascending."""
        from datetime import date
        from alpaca.trading.requests import GetCalendarRequest

        end = date.fromisoformat(knobs["end"]) if knobs["end"] else datetime.now(
            timezone.utc).date()
        span = timedelta(days=knobs["sessions"] * _CALENDAR_SPAN_PER_SESSION)
        request = GetCalendarRequest(start=end - span, end=end)
        days = self._vendor(knobs, lambda: client.get_calendar(request))
        dates = sorted({day.date for day in days if day.date <= end})
        if len(dates) < knobs["sessions"]:
            raise AssetError([f"calendar returned {len(dates)} sessions up to {end}, "
                              f"fewer than sessions={knobs['sessions']}"])
        return dates[-knobs["sessions"]:]

    def _contract_symbols(self, client, knobs, symbol, first):
        """Return the sorted live-in-window contract symbols of one underlying."""
        from alpaca.trading.requests import GetOptionContractsRequest

        found = set()
        for status in knobs["contract_statuses"]:
            page_token = None
            while True:
                request = GetOptionContractsRequest(
                    underlying_symbols=[symbol], status=status,
                    expiration_date_gte=first, limit=1000, page_token=page_token)
                response = self._vendor(
                    knobs, lambda: client.get_option_contracts(request))
                found.update(c.symbol for c in response.option_contracts or []
                             if c.root_symbol == symbol)
                page_token = response.next_page_token
                if not page_token:
                    break
        return sorted(found)

    def _chain_volume(self, client, key, secret, knobs, assets, sessions):
        """Yield one total row per underlying and session that traded."""
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.historical import OptionHistoricalDataClient
        from alpaca.data.requests import OptionBarsRequest

        data = OptionHistoricalDataClient(key, secret)
        window = {d.isoformat() for d in sessions}
        timeframe = bar_timeframe((1, "Day"))
        batch = knobs["max_symbols_per_request"]
        for asset in assets:
            symbol = asset["symbol"]
            contracts = self._contract_symbols(client, knobs, symbol, sessions[0])
            totals = {}
            for offset in range(0, len(contracts), batch):
                names = contracts[offset:offset + batch]
                bars = self._vendor(knobs, lambda: data.get_option_bars(OptionBarsRequest(
                    symbol_or_symbols=names, timeframe=timeframe,
                    start=sessions[0], end=sessions[-1] + timedelta(days=1),
                    feed=OptionsFeed(knobs["feed"]))))
                for series in bars.data.values():
                    for bar in series:
                        day = bar.timestamp.astimezone(timezone.utc).date().isoformat()
                        if day in window and bar.volume:
                            volume, traded = totals.get(day, (0.0, 0))
                            totals[day] = (volume + float(bar.volume), traded + 1)
            for day in sorted(totals):
                volume, traded = totals[day]
                yield {"symbol": symbol, "session": day, "volume": volume,
                       "contracts_traded": traded}
