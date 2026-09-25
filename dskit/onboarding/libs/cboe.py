"""Cboe public index history and delayed option chains through the onboarding contract (ADR-0182).

Cboe's CDN serves, with no credential, a daily price-history CSV per index
and a 15-minute-delayed full option chain per underlying as JSON. A project
declares only its universe (``symbols``) and its pacing policy in config;
this pack owns the transport, the CSV and OCC parsing and the clock.

Two streams, provider-shaped (Cboe's own field names and units; a child
normalizes into its own vocabulary):

- ``index_daily`` — one row per ``(symbol, date)`` from
  ``/api/global/us_indices/daily_prices/{SYMBOL}_History.csv``. The file
  is either ``DATE,OPEN,HIGH,LOW,CLOSE`` (VIX) or ``DATE,<SYMBOL>`` — a
  single value column read as the close (SPX); ``open``/``high``/``low``
  are None when the file has no such column. ``DATE`` is ``MM/DD/YYYY``
  and is emitted ISO. The cursor is the max date emitted, and a pull emits
  only dates strictly after it — the ``localfiles`` semantics, identical
  in both modes (the platform keys the cursors apart). Every file is
  re-downloaded whole: the CDN serves no range. A malformed row, a
  repeated date or an unknown header refuses the pull, naming the file
  and line.
- ``option_chain`` — one row per ``(option, quote_time)`` from
  ``/api/global/delayed_quotes/options/_{SYMBOL}.json`` — Cboe prefixes an
  INDEX with ``_``; an ETF or stock (declared in ``equity_symbols``) is
  served at ``{SYMBOL}.json`` with no prefix. The OCC
  ``option`` symbol (``SPXW261016C07000000``) is parsed into ``root``,
  ``expiry`` (ISO), ``right`` (``call``/``put``) and ``strike`` (the last
  eight digits / 1000); quotes, sizes, ``iv``, open interest, volume and
  greeks are the venue's; ``underlying_price`` is the payload's
  ``data.current_price``. **The chain is current state:** every pull
  records the whole (filtered) chain, no cursor filter applies, and the
  cursor is recorded but never consulted. ``last_trade_time`` is kept
  verbatim — the venue's naive America/New_York wall clock, or None.

**The quote instant.** The payload's top-level ``timestamp``
(``YYYY-MM-DD HH:MM:SS``) carries no zone. It was America/New_York
wall-clock time until 2026-09-24, when Cboe switched it to UTC without
notice (``last_trade_time`` stayed New York). The zone is therefore
resolved against the pull's own clock: the stamp is read as New York
time unless that reading lies more than ``CLOCK_SKEW_S`` after the
moment the chain was fetched, in which case it is read as UTC (a
snapshot is stamped when served, so the New York reading of a UTC stamp
is 4-5 hours in the future); a stamp in the future under both readings
is refused. The result becomes both ``quote_time``
and the row's ``effective_date``. Two pulls of one unchanged delayed
snapshot therefore collide on their key and dedup keeps one — a re-pull,
not a duplicate. In the repeated hour of a DST fall-back the earlier
(EDT) reading is taken (``fold=0``). ``max_dte`` counts calendar days
from the quote's New York date to ``expiry``.

**Transport.** Every request goes through one injectable
``getter(url, params) -> str | bytes`` (the raw body; bytes are decoded
UTF-8, a BOM dropped); CSV and JSON parsing sit above it, as do pacing
(``pace_s`` between requests) and retry with exponential backoff on HTTP
429/5xx and network errors (``retries``, honoring a numeric
``Retry-After``; every single wait is capped at ``MAX_BACKOFF_S``
seconds), so a scripted getter exercises all of them. The default getter
is stdlib urllib under the ``timeout_s`` and ``user_agent`` knobs. The CDN
answers an unknown symbol with HTTP 403, which is refused, never retried.

**Provider zeros.** Cboe sends ``0`` for a missing bid/ask/iv on an
illiquid strike; the pack keeps it as sent (provider-shaped), so a ``0``
bid means "no market", never "priced at zero" — a reader must treat it
deliberately. **One cursor per stream**: ``index_daily`` filters every
symbol against the same max date, so a symbol appended to an
already-backfilled source would be silently skipped — register a new
source (or a fresh root) to add one.

Import cost: stdlib only.
"""

from __future__ import annotations

import csv
import functools
import io
import json
import re
import time
import urllib.error
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..base import AssetError, MODES, _raise_if, parse_utc
from ..connector import MAX_BACKOFF_S, PROTOCOL, Connector, backoff, retry_after
from .kalshi import _finite, _real

__all__ = [
    "CHAIN_FIELDS",
    "CHAIN_KEY_FIELDS",
    "CHAIN_STREAM",
    "DEFAULT_BASE_URL",
    "DEFAULT_PACE_S",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_USER_AGENT",
    "INDEX_FIELDS",
    "INDEX_KEY_FIELDS",
    "INDEX_STREAM",
    "MAX_BACKOFF_S",
    "QUOTE_TZ",
    "STREAMS",
    "CboeConnector",
    "parse_occ",
]

INDEX_STREAM = "index_daily"
CHAIN_STREAM = "option_chain"
INDEX_KEY_FIELDS = ("symbol", "date")
INDEX_FIELDS = ("symbol", "date", "open", "high", "low", "close")
CHAIN_KEY_FIELDS = ("option", "quote_time")
CHAIN_FIELDS = (
    "underlying", "option", "root", "expiry", "right", "strike", "bid",
    "bid_size", "ask", "ask_size", "iv", "open_interest", "volume", "delta",
    "gamma", "vega", "theta", "last_trade_price", "last_trade_time",
    "underlying_price", "quote_time",
)
#: The zone Cboe's chain ``timestamp`` is written in.
QUOTE_TZ = "America/New_York"

#: Seconds a chain stamp may lie after the fetch clock before its reading is rejected.
CLOCK_SKEW_S = 300
DEFAULT_BASE_URL = "https://cdn.cboe.com"
DEFAULT_PACE_S = 0.5
DEFAULT_RETRIES = 4
#: A full SPX chain is ~13.5 MB; the timeout covers the whole body.
DEFAULT_TIMEOUT_S = 120
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; dskit-onboarding)"

_STREAMS = {
    CHAIN_STREAM: (CHAIN_FIELDS, CHAIN_KEY_FIELDS),
    INDEX_STREAM: (INDEX_FIELDS, INDEX_KEY_FIELDS),
}
STREAMS = tuple(sorted(_STREAMS))

_RETRY_STATUSES = (429, 500, 502, 503, 504)
_INDEX_PATH = "/api/global/us_indices/daily_prices/{}_History.csv"
_CHAIN_PATH = "/api/global/delayed_quotes/options/_{}.json"
#: An ETF/stock chain carries no index underscore.
_EQUITY_CHAIN_PATH = "/api/global/delayed_quotes/options/{}.json"
#: A symbol is one URL path segment; Cboe spells indices upper-case.
#: \Z, not $ — $ forgives a trailing newline (ADR-0020).
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._-]*\Z")
#: OCC: root (1-6), YYMMDD, C/P, strike x 1000 in eight digits.
_OCC = re.compile(r"^([A-Z0-9]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})\Z")
_RIGHTS = {"C": "call", "P": "put"}
_OHLC = ("OPEN", "HIGH", "LOW", "CLOSE")


def parse_occ(symbol):
    """Split an OCC option symbol into ``(root, expiry ISO, right, strike)``.

    Parameters
    ----------
    symbol : str
        e.g. ``"SPXW261016C07000000"`` or ``"XSP261016P00575500"``.

    Returns
    -------
    tuple
        ``(root, expiry, right, strike)`` — ``right`` is ``"call"`` or
        ``"put"``; ``strike`` is the eight-digit field / 1000 as a float.

    Raises
    ------
    AssetError
        If ``symbol`` is not an OCC symbol or names an impossible date.

    Examples
    --------
    ::

        parse_occ("SPXW261016C07000000")  # ('SPXW', '2026-10-16', 'call', 7000.0)
        parse_occ("XSP261016P00575500")   # ('XSP', '2026-10-16', 'put', 575.5)
    """
    match = _OCC.match(symbol) if isinstance(symbol, str) else None
    if match is None:
        raise AssetError([f"not an OCC option symbol: {symbol!r}"])
    root, yy, mm, dd, right, strike = match.groups()
    try:
        expiry = date(2000 + int(yy), int(mm), int(dd))
    except ValueError as exc:
        raise AssetError([f"OCC symbol {symbol!r} names no date: {exc}"]) from exc
    return root, expiry.isoformat(), _RIGHTS[right], int(strike) / 1000.0


def _quote_instant(stamp, where, fetched):
    """Return the aware UTC instant of a zoneless ``YYYY-MM-DD HH:MM:SS`` stamp.

    The New York reading wins unless it lies more than ``CLOCK_SKEW_S``
    after ``fetched`` (the aware UTC fetch instant); then the UTC reading
    is used, and a stamp in the future under both readings is refused.
    """
    try:
        naive = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError) as exc:
        raise AssetError(
            [f"{where}: timestamp must be 'YYYY-MM-DD HH:MM:SS' "
             f"({QUOTE_TZ} or UTC), got {stamp!r}"]
        ) from exc
    latest = fetched + timedelta(seconds=CLOCK_SKEW_S)
    for zone in (ZoneInfo(QUOTE_TZ), timezone.utc):
        instant = naive.replace(tzinfo=zone).astimezone(timezone.utc)
        if instant <= latest:
            return instant
    raise AssetError(
        [f"{where}: timestamp {stamp!r} is after the fetch instant "
         f"{fetched.isoformat()} as both {QUOTE_TZ} and UTC time"]
    )


def _body_text(body):
    """Return a transport body as text: bytes decoded UTF-8, a leading BOM dropped."""
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body).decode("utf-8")
    if not isinstance(body, str):
        raise TypeError(f"body is {type(body).__name__}, not str or bytes")
    return body.lstrip("\ufeff")


def _index_rows(symbol, text, where):
    """Yield ``index_daily`` rows for one history CSV; refuse any malformed line."""
    lines = csv.reader(io.StringIO(text))
    header = next(lines, None)
    names = [h.strip().upper() for h in header or []]
    if not names or names[0] != "DATE" or len(set(names)) != len(names):
        raise AssetError([f"{where}: header must start with DATE, got {header!r}"])
    if len(names) == 2 and names[1] not in _OHLC[:3]:
        column = {"CLOSE": 1}  # DATE,<SYMBOL>: the single value is the close
    elif "CLOSE" in names and set(names[1:]) <= set(_OHLC):
        column = {name: names.index(name) for name in _OHLC if name in names}
    else:
        raise AssetError(
            [f"{where}: header must be DATE,<value> or DATE plus "
             f"{'/'.join(_OHLC)} columns, got {header!r}"]
        )
    seen = set()
    for line, cells in enumerate(lines, start=2):
        if not any(cell.strip() for cell in cells):
            continue
        at = f"{where} line {line}"
        if len(cells) != len(names):
            raise AssetError(
                [f"{at}: {len(cells)} cell(s) under a {len(names)}-column header"]
            )
        try:
            day = datetime.strptime(cells[0].strip(), "%m/%d/%Y").date().isoformat()
        except ValueError as exc:
            raise AssetError(
                [f"{at}: DATE must be MM/DD/YYYY, got {cells[0]!r}"]
            ) from exc
        if day in seen:
            raise AssetError([f"{at}: date {day} repeats"])
        seen.add(day)
        values = dict.fromkeys(_OHLC)
        for name, index in column.items():
            raw = cells[index].strip()
            values[name] = _finite(raw)
            if values[name] is None:
                raise AssetError([f"{at}: {name} is not a finite number, got {raw!r}"])
        yield {
            "symbol": symbol,
            "date": day,
            "open": values["OPEN"],
            "high": values["HIGH"],
            "low": values["LOW"],
            "close": values["CLOSE"],
        }


def _chain_row(symbol, raw, quote_time, underlying_price, where):
    """Build the 21-field ``option_chain`` row for one option object; an OCC ``option`` is required."""
    if not isinstance(raw, dict):
        raise AssetError([f"{where}: option is not a dict, got {type(raw).__name__}"])
    option = raw.get("option")
    try:
        root, expiry, right, strike = parse_occ(option)
    except AssetError as exc:
        raise AssetError([f"{where}: {problem}" for problem in exc.errors]) from exc
    traded = raw.get("last_trade_time")
    return {
        "underlying": symbol,
        "option": option,
        "root": root,
        "expiry": expiry,
        "right": right,
        "strike": strike,
        "bid": _finite(raw.get("bid")),
        "bid_size": _finite(raw.get("bid_size")),
        "ask": _finite(raw.get("ask")),
        "ask_size": _finite(raw.get("ask_size")),
        "iv": _finite(raw.get("iv")),
        "open_interest": _finite(raw.get("open_interest")),
        "volume": _finite(raw.get("volume")),
        "delta": _finite(raw.get("delta")),
        "gamma": _finite(raw.get("gamma")),
        "vega": _finite(raw.get("vega")),
        "theta": _finite(raw.get("theta")),
        "last_trade_price": _finite(raw.get("last_trade_price")),
        "last_trade_time": traded if isinstance(traded, str) and traded else None,
        "underlying_price": underlying_price,
        "quote_time": quote_time,
    }


def _symbol_list(errors, config, name, *, required):
    """Append an error unless knob ``name`` is a list of distinct symbols; return it."""
    value = config.get(name)
    if value is None and not required:
        return None
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(s, str) and _SYMBOL.match(s) for s in value)
    ):
        errors.append(
            f"config.{name} must be a non-empty list of upper-case symbols, "
            f"got {value!r}"
        )
    elif len(set(value)) != len(value):
        errors.append(f"config.{name} must not repeat, got {value!r}")
    return value


class CboeConnector(Connector):
    r"""Cboe public data: daily index history and delayed option chains.

    Parameters
    ----------
    getter : callable or None
        ``getter(url, params) -> str | bytes`` — ONE HTTP GET attempt:
        return the raw body on success, raise ``urllib.error.HTTPError``
        on any other status and ``urllib.error.URLError`` (any
        ``OSError``) on a transport failure. ``params`` is always empty
        (every endpoint is a bare path). ``None`` means stdlib urllib
        under the ``timeout_s`` and ``user_agent`` knobs. Parsing, pacing
        and retry sit above the getter.
    sleeper : callable or None
        ``sleeper(seconds)`` for pacing and backoff; ``None`` means
        ``time.sleep``.
    clock : callable or None
        ``clock() -> datetime`` (aware) read right after each chain fetch
        to resolve the stamp's zone; ``None`` means the UTC wall clock.

    Examples
    --------
    Pull one index history through a scripted transport::

        connector = CboeConnector(
            getter=lambda url, params: "DATE,SPX\n01/02/1975,70.23\n",
            sleeper=lambda seconds: None,
        )
        messages = list(connector.read(
            {"symbols": ["SPX"]}, ["index_daily"], {}, "backfill"))
        messages[1]["data"]["close"]  # 70.23
    """

    def __init__(self, getter=None, sleeper=None, clock=None):
        problems = [
            f"{name} must be callable, got {type(value).__name__}"
            for name, value in (("getter", getter), ("sleeper", sleeper), ("clock", clock))
            if value is not None and not callable(value)
        ]
        _raise_if(problems)
        self._getter = getter
        self._sleeper = time.sleep if sleeper is None else sleeper
        self._clock = (lambda: datetime.now(timezone.utc)) if clock is None else clock
        self._paced = False

    def spec(self):
        """Declare the default-deny Cboe configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {"params": {
            "symbols": {
                "required": True,
                "notes": "Non-empty list of Cboe symbols (e.g. SPX, VIX, XSP) — "
                         "the universe every stream walks, in this order.",
            },
            "equity_symbols": {
                "notes": "Optional subset of `symbols` that are ETFs/stocks: their "
                         "`option_chain` URL has no index underscore (SPY.json, not "
                         "_SPY.json). Declared, never guessed from a 403.",
            },
            "roots": {
                "notes": "Optional OCC root allowlist for `option_chain` (e.g. "
                         "['SPXW', 'XSP']); absent keeps every root.",
            },
            "max_dte": {
                "notes": "Optional int >= 0: `option_chain` keeps expiries 0..N "
                         "calendar days after the quote's New York date; "
                         "absent keeps every expiry (an expired one included).",
            },
            "pace_s": {
                "notes": f"Seconds slept between requests; default {DEFAULT_PACE_S}.",
            },
            "retries": {
                "notes": "Extra attempts on HTTP 429/5xx and network errors, "
                         "exponential backoff honoring a numeric Retry-After, "
                         f"each wait capped at {MAX_BACKOFF_S} seconds; "
                         f"default {DEFAULT_RETRIES}.",
            },
            "timeout_s": {
                "notes": "Per-request timeout in seconds for the default "
                         f"transport; default {DEFAULT_TIMEOUT_S}.",
            },
            "user_agent": {
                "notes": "User-Agent header the default transport sends; "
                         f"default {DEFAULT_USER_AGENT}.",
            },
            "base_url": {
                "notes": f"CDN root; default {DEFAULT_BASE_URL}.",
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
            Fully resolved request knobs; ``base_url`` without a trailing
            slash, ``roots`` / ``max_dte`` None when absent.

        Raises
        ------
        AssetError
            Listing all malformed values.
        """
        if not isinstance(config, dict):
            raise AssetError([f"config must be a dict, got {type(config).__name__}"])
        problems = []
        symbols = _symbol_list(problems, config, "symbols", required=True)
        roots = _symbol_list(problems, config, "roots", required=False)
        equities = _symbol_list(problems, config, "equity_symbols", required=False)
        if equities is not None and symbols is not None \
                and not set(equities) <= set(symbols):
            problems.append(
                f"config.equity_symbols must be a subset of symbols, got "
                f"{sorted(set(equities) - set(symbols))!r} outside it"
            )
        max_dte = config.get("max_dte")
        if max_dte is not None and (
            isinstance(max_dte, bool) or not isinstance(max_dte, int) or max_dte < 0
        ):
            problems.append(f"config.max_dte must be an int >= 0, got {max_dte!r}")
        retries = config.get("retries", DEFAULT_RETRIES)
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            problems.append(f"config.retries must be an int >= 0, got {retries!r}")
        pace_s = config.get("pace_s", DEFAULT_PACE_S)
        if not _real(pace_s) or pace_s < 0:
            problems.append(f"config.pace_s must be a number >= 0, got {pace_s!r}")
        timeout_s = config.get("timeout_s", DEFAULT_TIMEOUT_S)
        if not _real(timeout_s) or timeout_s <= 0:
            problems.append(
                f"config.timeout_s must be a positive number, got {timeout_s!r}"
            )
        user_agent = config.get("user_agent", DEFAULT_USER_AGENT)
        if not isinstance(user_agent, str) or not user_agent.strip():
            problems.append(
                f"config.user_agent must be a non-empty string, got {user_agent!r}"
            )
        base_url = config.get("base_url", DEFAULT_BASE_URL)
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            problems.append(f"config.base_url must be an http(s) URL, got {base_url!r}")
        _raise_if(problems)
        return {
            "symbols": list(symbols),
            "roots": None if roots is None else list(roots),
            "equity_symbols": [] if equities is None else list(equities),
            "max_dte": max_dte,
            "retries": retries,
            "pace_s": pace_s,
            "timeout_s": timeout_s,
            "user_agent": user_agent,
            "base_url": base_url.rstrip("/"),
        }

    # -- transport ---------------------------------------------------------

    def _http_get(self, url, params, timeout_s, user_agent):
        """Perform one stdlib urllib GET and return the raw body — the default transport."""
        import urllib.request

        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.read()

    def _transport(self, knobs):
        """Return the ``(url, params) -> str | bytes`` callable for this pull."""
        if self._getter is not None:
            return self._getter
        return functools.partial(
            self._http_get, timeout_s=knobs["timeout_s"], user_agent=knobs["user_agent"]
        )

    def _pace(self, pace_s):
        """Sleep the pacing gap before every request but the connector's first."""
        if self._paced and pace_s > 0:
            self._sleeper(pace_s)
        self._paced = True

    def _get(self, knobs, path):
        """One paced, retried GET under ``base_url``; returns ``(url, text)``."""
        url = knobs["base_url"] + path
        transport = self._transport(knobs)
        self._pace(knobs["pace_s"])
        last = delay = None
        for attempt in range(knobs["retries"] + 1):
            if attempt:
                self._sleeper(delay)
            try:
                body = transport(url, {})
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRY_STATUSES:
                    raise AssetError([f"Cboe GET {url}: HTTP {exc.code}"]) from exc
                last = f"HTTP {exc.code}"
                delay = retry_after(exc.headers, backoff(attempt + 1))
            except OSError as exc:
                last = f"network error: {exc}"
                delay = backoff(attempt + 1)
            else:
                try:
                    return url, _body_text(body)
                except (TypeError, UnicodeDecodeError) as exc:
                    raise AssetError(
                        [f"Cboe GET {url}: response is not UTF-8 text: {exc}"]
                    ) from exc
        raise AssetError(
            [f"Cboe GET {url}: giving up after {knobs['retries'] + 1} "
             f"attempt(s); last failure: {last}"]
        )

    def _history(self, knobs, symbol):
        """Yield ``index_daily`` rows of one symbol's history file, in file order."""
        url, text = self._get(knobs, _INDEX_PATH.format(urllib.parse.quote(symbol)))
        yield from _index_rows(symbol, text, url)

    # -- the venue's endpoints -----------------------------------------------

    def _pull_index(self, knobs, cursor_dt):
        """Yield ``(effective_date, row)`` per history row dated after the cursor."""
        for symbol in knobs["symbols"]:
            for row in self._history(knobs, symbol):
                if cursor_dt is not None and parse_utc(row["date"]) <= cursor_dt:
                    continue
                yield row["date"], row

    def _pull_chain(self, knobs, cursor_dt):
        """Yield ``(effective_date, row)`` per option of every chain — never cursor-filtered."""
        roots = None if knobs["roots"] is None else set(knobs["roots"])
        for symbol in knobs["symbols"]:
            path = _EQUITY_CHAIN_PATH if symbol in knobs["equity_symbols"] else _CHAIN_PATH
            url, text = self._get(knobs, path.format(urllib.parse.quote(symbol)))
            fetched = self._clock().astimezone(timezone.utc)
            try:
                body = json.loads(text)
            except ValueError as exc:
                raise AssetError([f"Cboe GET {url}: response is not JSON: {exc}"]) from exc
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, dict) or not isinstance(data.get("options"), list):
                raise AssetError([f"{url}: payload lacks a 'data.options' list"])
            quoted = _quote_instant(body.get("timestamp"), url, fetched)
            quote_time = quoted.isoformat()
            quote_day = quoted.astimezone(ZoneInfo(QUOTE_TZ)).date()
            underlying_price = _finite(data.get("current_price"))
            for i, raw in enumerate(data["options"]):
                row = _chain_row(
                    symbol, raw, quote_time, underlying_price, f"{url} option {i}"
                )
                if roots is not None and row["root"] not in roots:
                    continue
                if knobs["max_dte"] is not None:
                    dte = (date.fromisoformat(row["expiry"]) - quote_day).days
                    if not 0 <= dte <= knobs["max_dte"]:
                        continue
                yield quote_time, row

    def _pullers(self):
        """Stream name -> the generator that pulls it."""
        return {CHAIN_STREAM: self._pull_chain, INDEX_STREAM: self._pull_index}

    # -- the four verbs ------------------------------------------------------

    def check(self, config):
        """Validate config and read the first symbol's history file once.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        None
            Silence means the CDN served a parseable history for the first
            symbol (the small file; a chain runs to megabytes).

        Raises
        ------
        AssetError
            If config is invalid, the ping fails after its retries, or the
            file does not parse.
        """
        knobs = self.resolve_knobs(config)
        for _row in self._history(knobs, knobs["symbols"][0]):
            pass

    def discover(self, config):
        """Describe the two provider-shaped streams without touching the CDN.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        list
            One ``{stream, schema, primary_key}`` declaration per stream,
            by name.

        Raises
        ------
        AssetError
            If config values are invalid.
        """
        self.resolve_knobs(config)
        return [
            {"stream": name, "schema": {"fields": list(fields)},
             "primary_key": list(key)}
            for name, (fields, key) in sorted(_STREAMS.items())
        ]

    def read(self, config, streams, state, mode):
        """Emit schema, records and one checkpoint per requested stream.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list
            Requested streams among :data:`STREAMS`.
        state : dict
            Prior mode-keyed checkpoint: ``{stream: {"cursor": ISO}}``.
        mode : str
            ``backfill`` or ``live`` — the pull is identical in both; the
            platform keys the cursors apart.

        Yields
        ------
        dict
            Onboarding protocol messages.

        Raises
        ------
        AssetError
            If arguments, config or the CDN's responses are invalid.
        """
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        bad = [k for k, v in state.items() if not isinstance(v, dict)]
        if bad:
            raise AssetError([f"state.{k} must be a dict, got {state[k]!r}" for k in bad])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        if mode not in MODES:
            raise AssetError([f"mode must be one of {MODES}, got {mode!r}"])
        knobs = self.resolve_knobs(config)
        pullers = self._pullers()
        unknown = [s for s in streams if s not in pullers]
        if unknown:
            raise AssetError(
                [f"unknown stream(s) {unknown}; discovered: {list(STREAMS)}"]
            )
        new_state = {key: dict(value) for key, value in state.items()}
        for stream in streams:
            cursor = state.get(stream, {}).get("cursor", "")
            cursor_dt = parse_utc(cursor) if cursor else None
            yield {
                "protocol": PROTOCOL,
                "type": "SCHEMA",
                "stream": stream,
                "schema": {"fields": list(_STREAMS[stream][0])},
            }
            emitted, emitted_dt = cursor, cursor_dt
            for effective, data in pullers[stream](knobs, cursor_dt):
                effective_dt = parse_utc(effective)
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
            new_state.setdefault(stream, {})["cursor"] = emitted
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}
