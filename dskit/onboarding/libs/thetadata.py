"""ThetaData daily option chains and underlying bars through the onboarding contract.

ThetaData serves OPRA-derived history from a local Theta Terminal (a Java
process that logs in on its own and answers REST on loopback). A project that
wants the whole market's daily chains as WORM evidence needs thousands of
roots times ten years of days pulled resumably; this pack owns that stack once
(ADR-0263). A child declares roots in priority order, the data window, the
fields it wants, and how many requests may run at once.

What the pack decides, and why:

- One unit of work is one (root, trade date). The whole chain is one
  ``expiration=*`` request per endpoint, merged on (expiration, strike, right):
  the end-of-day report (``greeks/eod``, or plain ``eod`` where the plan has no
  greeks) and open interest. A contract that has open interest but no trade is
  still emitted, with its trade fields null. Vendor fields ride through
  verbatim; the known ones are always present so the table has fixed columns.
- Dates run NEWEST to OLDEST per root, roots in declared order, and the cursor
  is the oldest date done: a month that ends early still holds the recent
  years of the roots that matter. A pull does at most ``batch_units`` units, so
  one ``acquire backfill`` is bounded and the caller repeats it until a pull
  emits nothing.
- ``end`` is REQUIRED and exclusive. A pull always declares its data cut; none
  can run "up to now" by omission.
- Retry is the shared policy: 429, 5xx and transport faults wait
  ``Retry-After`` or ``backoff``; 570 (``LARGE_REQUEST``) splits the request by
  expiration; 204 and 472 are an empty day (a holiday or a root with no chain);
  any other status refuses naming the status and path. The connector holds no
  secret: the Terminal logs in itself.
- ``concurrency`` fetches units in parallel with ORDERED output, so the cursor
  stays contiguous whatever the speed.

Import cost: stdlib only; the default transport is ``urllib``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from ..base import AssetError, MODES, parse_utc
from ..connector import PROTOCOL, Connector, backoff, retry_after

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_BATCH_UNITS",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_EMPTY_GUARD",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT_S",
    "EMPTY_STATUSES",
    "LARGE_REQUEST_STATUS",
    "MAX_CONCURRENCY",
    "OPTION_EOD_STREAM",
    "OPTION_FIELDS",
    "OPTION_KEY_FIELDS",
    "UNDERLYING_FIELDS",
    "UNDERLYING_KEY_FIELDS",
    "UNDERLYING_STREAM",
    "ThetaDataConnector",
    "urllib_get",
]

OPTION_EOD_STREAM = "option_eod"
UNDERLYING_STREAM = "underlying_eod"
OPTION_KEY_FIELDS = ("symbol", "expiration", "strike", "right", "date")
UNDERLYING_KEY_FIELDS = ("symbol", "date")
OPTION_FIELDS = (
    "symbol", "expiration", "strike", "right", "date", "created", "last_trade",
    "timestamp", "open", "high", "low", "close", "volume", "count",
    "bid_size", "bid_exchange", "bid_condition", "bid",
    "ask_size", "ask_exchange", "ask_condition", "ask", "open_interest",
    "delta", "theta", "vega", "rho", "epsilon", "lambda", "gamma",
    "vanna", "charm", "vomma", "veta", "vera", "speed", "zomma", "color",
    "ultima", "d1", "d2", "dual_delta", "dual_gamma", "implied_vol",
    "iv_error", "underlying_timestamp", "underlying_price",
)
UNDERLYING_FIELDS = (
    "symbol", "date", "created", "last_trade", "open", "high", "low", "close",
    "volume", "count", "bid_size", "bid_exchange", "bid_condition", "bid",
    "ask_size", "ask_exchange", "ask_condition", "ask",
)

DEFAULT_BASE_URL = "http://127.0.0.1:25503"
DEFAULT_BATCH_UNITS = 500
DEFAULT_CONCURRENCY = 1
MAX_CONCURRENCY = 8
DEFAULT_RETRIES = 5
DEFAULT_EMPTY_GUARD = 20
DEFAULT_RETRY_BASE_S = 1.0
DEFAULT_TIMEOUT_S = 300
#: A day with no data: 204 No Content, and the Terminal's 472 "no data".
EMPTY_STATUSES = (204, 472)
LARGE_REQUEST_STATUS = 570

_EOD_PATH = "/v3/option/history/eod"
_GREEKS_PATH = "/v3/option/history/greeks/eod"
_OI_PATH = "/v3/option/history/open_interest"
_EXPIRATIONS_PATH = "/v3/option/list/expirations"
_STOCK_EOD_PATH = "/v3/stock/history/eod"
_PROBE_PATH = "/v3/stock/list/symbols"
#: Flat one-record-per-line output; the default JSON nests rows under contracts.
_FORMAT = "ndjson"
_UNKNOWN_SYMBOL = b"Unknown symbol"
_STREAMS = (OPTION_EOD_STREAM, UNDERLYING_STREAM)
_RIGHTS = {"C": "call", "CALL": "call", "P": "put", "PUT": "put"}
_EXCERPT_CHARS = 200
_THROTTLED = 429
_SERVER_FAULT = 500
_WINDOW_FACTOR = 2


def urllib_get(url, params, timeout):
    """Perform one HTTP GET over stdlib urllib — the default getter.

    Parameters
    ----------
    url : str
        Scheme, host and path — no query string.
    params : dict
        Query parameters, URL-encoded here.
    timeout : float
        Socket timeout in seconds.

    Returns
    -------
    tuple
        ``(status, headers, body)``; non-2xx statuses are RETURNED, only a
        transport fault raises.

    Raises
    ------
    OSError
        On a refused connection, DNS failure or timeout.
    """
    full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    try:
        with urllib.request.urlopen(full, timeout=timeout) as response:
            return response.getcode(), dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        found = dict(exc.headers.items()) if exc.headers is not None else {}
        return exc.code, found, exc.read()


class _TooLarge(Exception):
    """The Terminal refused a request as too large (570)."""


def _compact(day):
    """``YYYYMMDD`` for a ``date``."""
    return day.strftime("%Y%m%d")


def _weekdays_descending(first, last):
    """Weekdays from ``last`` down to ``first``, both inclusive."""
    day = last
    while day >= first:
        if day.weekday() < 5:
            yield day
        day -= timedelta(days=1)


def _iso_date(value):
    """Return ``YYYY-MM-DD`` for an ISO string, a ``YYYYMMDD`` string or an int."""
    text = str(value)
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise AssetError([f"Theta Terminal returned an unusable date {value!r}"]) from exc


def _parse(body):
    """Parse a body: a JSON array, ``{"response": [...]}``, or one record per line."""
    text = body.decode("utf-8").strip()
    if text.startswith("[") or text.startswith('{"response"'):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _rows(payload):
    """Return the record list of a JSON body: a bare list or ``{"response": [...]}``."""
    if isinstance(payload, dict) and "response" in payload:
        payload = payload["response"]
    if not isinstance(payload, list) or not all(isinstance(r, dict) for r in payload):
        raise AssetError([f"Theta Terminal returned {type(payload).__name__} that is "
                          "not a list of records (expected a list or {'response': [...]})"])
    return payload


def _ordered(work, items, workers):
    """Yield ``work(item)`` in ITEM order, ``workers`` at a time."""
    if workers == 1:
        for item in items:
            yield work(item)
        return
    window = workers * _WINDOW_FACTOR
    pool = ThreadPoolExecutor(max_workers=workers)
    pending = deque()
    try:
        for item in items:
            pending.append(pool.submit(work, item))
            if len(pending) >= window:
                yield pending.popleft().result()
        while pending:
            yield pending.popleft().result()
    finally:
        # An error or a closed generator must not wait on up to ``window``
        # in-flight units that can each retry for minutes.
        pool.shutdown(wait=False, cancel_futures=True)


class ThetaDataConnector(Connector):
    """ThetaData daily option chains and underlying bars, cursored per root.

    Parameters
    ----------
    getter : callable or None
        ``getter(url, params, timeout) -> (status, headers, body)``; default
        :func:`urllib_get`. Injected in tests.
    sleeper : callable or None
        ``sleeper(seconds)``; default ``time.sleep``.

    Examples
    --------
    ::

        connector = ThetaDataConnector()
        connector.discover({"symbols": ["SPY"], "start": "2016-01-04",
                            "end": "2026-01-01"})
    """

    def __init__(self, getter=None, sleeper=None):
        self._getter = urllib_get if getter is None else getter
        self._sleeper = time.sleep if sleeper is None else sleeper
        self._expirations_by_root = {}

    def spec(self):
        """Declare the default-deny configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {"params": {
            "symbols": {
                "required": True,
                "notes": "Option roots in PULL ORDER; each has its own cursor, "
                         "so a root added later backfills while the rest resume.",
            },
            "start": {
                "required": True,
                "notes": "Earliest trade date (inclusive).",
            },
            "end": {
                "required": True,
                "notes": "EXCLUSIVE upper bound on trade dates. Required: a pull "
                         "always declares its data cut.",
            },
            "base_url": {
                "notes": f"Theta Terminal REST address; default {DEFAULT_BASE_URL}. "
                         "Use one spelling consistently (127.0.0.1, not localhost).",
            },
            "greeks": {
                "notes": "True (default) pulls greeks/eod (Standard plan and up); "
                         "False pulls plain eod, which the free plan allows.",
            },
            "open_interest": {
                "notes": "True (default) also pulls open interest and merges it.",
            },
            "batch_units": {
                "notes": "Most (root, date) units one pull does; default "
                         f"{DEFAULT_BATCH_UNITS}. Repeat the pull until it emits nothing.",
            },
            "concurrency": {
                "notes": f"Parallel requests, 1..{MAX_CONCURRENCY}; default "
                         f"{DEFAULT_CONCURRENCY}. Stay at or below the plan's limit.",
            },
            "empty_guard": {
                "notes": "Refuse a root whose first N consecutive weekdays (newest "
                         "first) return no chain and which has never returned one; "
                         f"default {DEFAULT_EMPTY_GUARD}. Catches a misspelled root, an "
                         "index, or a plan or session fault answering 'no data'.",
            },
            "retries": {
                "notes": f"Attempts per request; default {DEFAULT_RETRIES}.",
            },
            "retry_base_s": {
                "notes": f"First retry wait in seconds; default {DEFAULT_RETRY_BASE_S}.",
            },
            "timeout_s": {
                "notes": f"Socket timeout per request; default {DEFAULT_TIMEOUT_S}.",
            },
        }}

    def resolve_knobs(self, config):
        """Validate config and apply the pack's defaults.

        Parameters
        ----------
        config : dict
            Connector configuration after platform-reserved keys are removed.

        Returns
        -------
        dict
            Fully resolved knobs, dates as ``date`` objects.

        Raises
        ------
        AssetError
            Listing all malformed values.
        """
        if not isinstance(config, dict):
            raise AssetError([f"config must be a dict, got {type(config).__name__}"])
        problems = []
        symbols = config.get("symbols")
        if (not isinstance(symbols, list) or not symbols
                or not all(isinstance(s, str) and s for s in symbols)
                or len(set(symbols)) != len(symbols)):
            problems.append("config.symbols must be a non-empty list of unique strings")
        dates = {}
        for name in ("start", "end"):
            value = config.get(name)
            try:
                dates[name] = parse_utc(value).date()
            except Exception:
                problems.append(f"config.{name} must be an ISO date, got {value!r}")
        if len(dates) == 2 and dates["end"] <= dates["start"]:
            problems.append("config.end must be after config.start")
        flags = {}
        for name in ("greeks", "open_interest"):
            flags[name] = config.get(name, True)
            if not isinstance(flags[name], bool):
                problems.append(f"config.{name} must be a boolean")
        ints = {}
        for name, default, low, high in (
            ("batch_units", DEFAULT_BATCH_UNITS, 1, None),
            ("concurrency", DEFAULT_CONCURRENCY, 1, MAX_CONCURRENCY),
            ("retries", DEFAULT_RETRIES, 1, None),
            ("empty_guard", DEFAULT_EMPTY_GUARD, 1, None),
        ):
            value = config.get(name, default)
            ints[name] = value
            if (isinstance(value, bool) or not isinstance(value, int)
                    or value < low or (high is not None and value > high)):
                bound = f"{low}..{high}" if high else f">= {low}"
                problems.append(f"config.{name} must be an int {bound}, got {value!r}")
        floats = {}
        for name, default in (("retry_base_s", DEFAULT_RETRY_BASE_S),
                              ("timeout_s", DEFAULT_TIMEOUT_S)):
            value = config.get(name, default)
            floats[name] = value
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                problems.append(f"config.{name} must be a positive number, got {value!r}")
        base_url = config.get("base_url", DEFAULT_BASE_URL)
        if not isinstance(base_url, str) or not base_url.startswith("http"):
            problems.append(f"config.base_url must be an http URL, got {base_url!r}")
        if problems:
            raise AssetError(problems)
        return {
            "symbols": list(symbols), "start": dates["start"], "end": dates["end"],
            "base_url": base_url.rstrip("/"), **flags, **ints, **floats,
        }

    # ------------------------------------------------------------------
    # transport

    def _get_json(self, knobs, path, params):
        """One request with the shared retry policy; the record list or ``[]``."""
        url = knobs["base_url"] + path
        attempts = knobs["retries"]
        for attempt in range(1, attempts + 1):
            last = attempt == attempts
            try:
                status, headers, body = self._getter(url, params, knobs["timeout_s"])
            except OSError as exc:
                if last:
                    raise AssetError([
                        f"Theta Terminal unreachable at {knobs['base_url']} after "
                        f"{attempts} attempt(s); is it running and logged in?"]) from exc
                self._sleeper(backoff(attempt, knobs["retry_base_s"]))
                continue
            if status in EMPTY_STATUSES:
                if _UNKNOWN_SYMBOL in body:
                    raise AssetError([f"Theta Terminal {path}: "
                                      f"{body[:_EXCERPT_CHARS].decode('utf-8', 'replace')}"])
                return []
            if status == LARGE_REQUEST_STATUS:
                raise _TooLarge(path)
            if status == _THROTTLED or status >= _SERVER_FAULT:
                if last:
                    raise AssetError([
                        f"Theta Terminal {path} still answering {status} after "
                        f"{attempts} attempt(s)"])
                wait = backoff(attempt, knobs["retry_base_s"])
                self._sleeper(retry_after(headers, wait))
                continue
            if status != 200:
                excerpt = body[:_EXCERPT_CHARS].decode("utf-8", "replace")
                raise AssetError([f"Theta Terminal {path} answered {status}: {excerpt}"])
            if not body.strip():
                return []
            try:
                return _rows(_parse(body))
            except ValueError as exc:
                raise AssetError([f"Theta Terminal {path} returned invalid JSON"]) from exc
        raise AssetError([f"Theta Terminal {path}: no attempt was made"])

    def _chain(self, knobs, path, symbol, day, ranged):
        """Every contract of ``symbol`` on ``day`` from one endpoint.

        ``ranged`` endpoints take start/end dates; open interest takes ``date``.
        """
        def params(expiration):
            base = {"symbol": symbol, "expiration": expiration, "format": _FORMAT}
            if ranged:
                base.update(start_date=_compact(day), end_date=_compact(day))
            else:
                base["date"] = _compact(day)
            return base

        try:
            return self._get_json(knobs, path, params("*"))
        except _TooLarge:
            pass
        rows = []
        for expiration in self._expirations(knobs, symbol):
            if expiration < day.isoformat():
                continue
            try:
                rows.extend(self._get_json(knobs, path, params(expiration)))
            except _TooLarge as exc:
                raise AssetError([f"Theta Terminal {path} {symbol} {day} expiration "
                                  f"{expiration} is too large even alone"]) from exc
        return rows

    def _expirations(self, knobs, symbol):
        """Return the root's listed expirations as ISO dates, cached per root."""
        if symbol not in self._expirations_by_root:
            listed = self._get_json(knobs, _EXPIRATIONS_PATH,
                                    {"symbol": symbol, "format": _FORMAT})
            self._expirations_by_root[symbol] = sorted(
                {_iso_date(item.get("expiration")) for item in listed})
        return self._expirations_by_root[symbol]

    # ------------------------------------------------------------------
    # normalization

    @staticmethod
    def _contract(row, where):
        right = _RIGHTS.get(str(row.get("right", "")).upper())
        if right is None:
            raise AssetError([f"Theta Terminal {where} row has unknown right "
                              f"{row.get('right')!r}"])
        try:
            return (_iso_date(row["expiration"]), float(row["strike"]), right)
        except (KeyError, TypeError, ValueError) as exc:
            raise AssetError([f"Theta Terminal {where} row lacks a usable expiration "
                              f"or strike: {str(row)[:_EXCERPT_CHARS]}"]) from exc

    def _day(self, knobs, unit):
        """Return the merged, ordered rows of one (root, date) unit."""
        symbol, day = unit
        path = _GREEKS_PATH if knobs["greeks"] else _EOD_PATH
        merged = {}
        for row in self._chain(knobs, path, symbol, day, True):
            key = self._contract(row, f"{symbol} {day} {path}")
            if key in merged:
                raise AssetError([f"Theta Terminal {path} {symbol} {day} repeats "
                                  f"contract {key}"])
            merged[key] = dict(row)
        interest = {}
        if knobs["open_interest"]:
            for row in self._chain(knobs, _OI_PATH, symbol, day, False):
                key = self._contract(row, f"{symbol} {day} {_OI_PATH}")
                if key in interest:
                    raise AssetError([f"Theta Terminal {_OI_PATH} {symbol} {day} "
                                      f"repeats contract {key}"])
                interest[key] = row.get("open_interest")
        out = []
        for key in sorted(set(merged) | set(interest)):
            data = {name: None for name in OPTION_FIELDS}
            data.update(merged.get(key, {}))
            data.update(symbol=symbol, expiration=key[0], strike=key[1],
                        right=key[2], date=day.isoformat())
            if key in interest:
                data["open_interest"] = interest[key]
            out.append(data)
        return unit, out

    def _underlying(self, knobs, symbol):
        """Return the underlying daily rows of ``symbol`` over ``[start, end)``."""
        last = knobs["end"] - timedelta(days=1)
        try:
            rows = self._get_json(knobs, _STOCK_EOD_PATH, {
                "symbol": symbol, "start_date": _compact(knobs["start"]),
                "end_date": _compact(last), "format": _FORMAT})
        except _TooLarge as exc:
            raise AssetError([f"Theta Terminal {_STOCK_EOD_PATH} {symbol} is too large; "
                              "narrow start/end"]) from exc
        if not rows:
            raise AssetError([f"Theta Terminal returned no stock bars for {symbol}; an "
                              "index or unsupported symbol, or a plan without stock data"])
        out = {}
        for row in rows:
            if not row.get("created"):
                raise AssetError([f"Theta Terminal {symbol} stock row has no `created` "
                                  "report date"])
            day = str(row["created"])[:10]
            if not knobs["start"].isoformat() <= day < knobs["end"].isoformat():
                continue
            if day in out:
                raise AssetError([f"Theta Terminal repeats {symbol} stock bar {day}"])
            data = {name: None for name in UNDERLYING_FIELDS}
            data.update(row)
            data.update(symbol=symbol, date=day)
            out[day] = data
        return [out[day] for day in sorted(out)]

    # ------------------------------------------------------------------
    # the four verbs

    def check(self, config):
        """Validate config and probe the Terminal; move no data.

        Raises
        ------
        AssetError
            If config is invalid or the Terminal does not answer.
        """
        knobs = self.resolve_knobs(config)
        self._get_json({**knobs, "retries": 1}, _PROBE_PATH, {"format": _FORMAT})

    def discover(self, config):
        """Describe the two streams without touching the Terminal.

        Returns
        -------
        list of dict
            ``option_eod`` and ``underlying_eod`` declarations.
        """
        self.resolve_knobs(config)
        return [
            {"stream": OPTION_EOD_STREAM, "schema": {"fields": list(OPTION_FIELDS)},
             "primary_key": list(OPTION_KEY_FIELDS)},
            {"stream": UNDERLYING_STREAM, "schema": {"fields": list(UNDERLYING_FIELDS)},
             "primary_key": list(UNDERLYING_KEY_FIELDS)},
        ]

    def read(self, config, streams, state, mode):
        """Emit schema, records and one checkpoint per call.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list of str
            ``option_eod`` and/or ``underlying_eod``.
        state : dict
            Prior checkpoint: ``state[option_eod][root] = {"oldest": ISO date}``,
            ``state[underlying_eod][root] = {"end": ISO date}``.
        mode : str
            Only ``backfill``: a live capture of a delayed history vendor
            would stamp yesterday as today.

        Yields
        ------
        dict
            Onboarding protocol messages.

        Raises
        ------
        AssetError
            On a bad argument, config, or Terminal failure.
        """
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        if mode not in MODES:
            raise AssetError([f"mode must be one of {MODES}, got {mode!r}"])
        if mode != MODES[0]:
            raise AssetError(["thetadata supports backfill only"])
        for stream in streams:
            if stream not in _STREAMS:
                raise AssetError([f"unknown stream {stream!r}; discovered: {list(_STREAMS)}"])
        knobs = self.resolve_knobs(config)
        new_state = {key: {sym: dict(cur) for sym, cur in value.items()}
                     for key, value in state.items()}
        for stream in streams:
            fields = OPTION_FIELDS if stream == OPTION_EOD_STREAM else UNDERLYING_FIELDS
            yield {"protocol": PROTOCOL, "type": "SCHEMA", "stream": stream,
                   "schema": {"fields": list(fields)}}
            emit = (self._option_messages if stream == OPTION_EOD_STREAM
                    else self._underlying_messages)
            yield from emit(knobs, stream, new_state.setdefault(stream, {}))
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}

    @staticmethod
    def _record(stream, data):
        return {"protocol": PROTOCOL, "type": "RECORD", "stream": stream,
                "effective_date": data["date"], "kind": "observation", "data": data}

    def _units(self, knobs, cursors):
        """Pending (root, date) units, roots in order, dates newest first."""
        last = knobs["end"] - timedelta(days=1)
        count = 0
        for symbol in knobs["symbols"]:
            top = last
            cursor = cursors.get(symbol, {})
            if cursor.get("newest") and last.isoformat() > cursor["newest"]:
                raise AssetError([
                    f"{symbol}: end {knobs['end']} is later than this store's pulled "
                    f"window (newest {cursor['newest']}); extending a store would leave "
                    "a gap, so register a new store instead"])
            if cursor.get("oldest"):
                top = min(top, date.fromisoformat(cursor["oldest"]) - timedelta(days=1))
            for day in _weekdays_descending(knobs["start"], top):
                if count >= knobs["batch_units"]:
                    return
                count += 1
                yield symbol, day

    def _option_messages(self, knobs, stream, cursors):
        units = self._units(knobs, cursors)
        newest = (knobs["end"] - timedelta(days=1)).isoformat()
        empties = {}
        for (symbol, day), rows in _ordered(
                lambda unit: self._day(knobs, unit), units, knobs["concurrency"]):
            cursor = cursors.setdefault(symbol, {})
            for data in rows:
                yield self._record(stream, data)
            if rows:
                cursor["seen"] = True
            elif not cursor.get("seen"):
                empties[symbol] = empties.get(symbol, 0) + 1
                if empties[symbol] >= knobs["empty_guard"]:
                    raise AssetError([
                        f"{symbol}: {empties[symbol]} consecutive weekdays from "
                        f"{day.isoformat()} upward returned no chain and none ever "
                        "has; check the symbol, the plan and that it is an option root"])
            cursor.setdefault("newest", newest)
            cursor["oldest"] = day.isoformat()

    def _underlying_messages(self, knobs, stream, cursors):
        for symbol in knobs["symbols"]:
            if cursors.get(symbol, {}).get("end") == knobs["end"].isoformat():
                continue
            for data in self._underlying(knobs, symbol):
                yield self._record(stream, data)
            cursors[symbol] = {"end": knobs["end"].isoformat()}
