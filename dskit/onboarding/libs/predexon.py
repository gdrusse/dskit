"""Predexon's Kalshi L2 order-book history through the onboarding connector contract.

Predexon archives Kalshi order books as sequenced snapshots and serves them
by contract ticker and time window from one keyed REST endpoint. A project
that models Kalshi ladders needs that history as WORM evidence behind a
resumable cursor, and the parent that first pulled it carried the whole
stack — transport, pacing, retry policy, pagination, dedup, checkpoint —
as a project script (ADR-0075). This pack owns that stack once; a child
declares tickers, the coverage window, the pacing it is entitled to, and
the NAME of the environment variable holding its key.

What the pack decides, and why:

- Every request goes through one injectable getter, a clock, and a
  sleeper, so pacing and retry are tested without a network or a wait. A
  moving-deadline limiter spaces EVERY attempt, retries included; the
  first call never waits and work done between calls is credited.
- 429s honour a numeric ``Retry-After`` and otherwise back off from a
  declared floor, doubling; server faults get a floor on attempts because
  the vendor's 5xx bursts outlast a three-try budget; other 4xx refuse at
  once, naming the URL and status. The key travels in a header, never in
  the URL, so no error message can carry it.
- Pagination follows ``pagination_key`` while ``has_more``. ``has_more``
  with a null key and an exhausted ``max_pages`` both LOG and stop — never
  loop — and the cursor covers what was fetched, so the next pull resumes
  rather than re-reads.
- One RECORD per snapshot. The ladders are normalized to
  ``[[price_dollars, size], ...]`` — YES bids descending, YES asks
  ascending, cents refused outside ``[0, 100]`` — the shape every ladder
  consumer wants and the parent's stores held; the vendor's ``best_*`` and
  ``*_depth`` summary fields ride through VERBATIM in the vendor's own
  units. A snapshot without an integer ``sequence`` refuses the whole
  ticker: the dedup key is ``(timestamp, sequence)``, and a silent default
  there was a real data defect upstream.
- The cursor is per ticker under the stream key —
  ``state[stream][ticker] = {"timestamp": ms, "sequence": int}`` — so a
  ticker added later backfills from ``coverage_start`` while the rest
  resume, and a snapshot at or before the pair is skipped on a re-pull.

:func:`native_book` re-expresses an emitted record's ladders as
Kalshi-native YES and NO bids, the projection the parent's books used.

Import cost: stdlib only; the default transport is ``urllib``.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from ..base import AssetError, MODES, parse_utc
from ..connector import PROTOCOL, Connector

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_END",
    "DEFAULT_KEY_ENV",
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_PAGES",
    "DEFAULT_MIN_INTERVAL_S",
    "DEFAULT_RETRIES",
    "DEFAULT_RETRY_FLOOR_S",
    "DEFAULT_TIMEOUT_S",
    "L2_FIELDS",
    "L2_KEY_FIELDS",
    "L2_STREAM",
    "LIMIT_BOUNDS",
    "ORDERBOOKS_PATH",
    "SERVER_FAULT_ATTEMPTS_FLOOR",
    "PredexonConnector",
    "RateLimiter",
    "native_book",
    "resolve_api_key",
    "urllib_get",
]

L2_STREAM = "l2_snapshots"
L2_KEY_FIELDS = ("ticker", "timestamp", "sequence")
L2_FIELDS = (
    "ticker", "timestamp", "sequence", "best_bid", "best_ask",
    "bid_depth", "ask_depth", "yes_bids", "yes_asks",
)
ORDERBOOKS_PATH = "/v2/kalshi/orderbooks"
DEFAULT_BASE_URL = "https://api.predexon.com"
DEFAULT_KEY_ENV = "PREDEXON_API_KEY"
DEFAULT_END = ""
DEFAULT_LIMIT = 200
LIMIT_BOUNDS = (1, 200)
DEFAULT_MIN_INTERVAL_S = 1.0
DEFAULT_RETRIES = 3
SERVER_FAULT_ATTEMPTS_FLOOR = 6
DEFAULT_RETRY_FLOOR_S = 2.0
DEFAULT_MAX_PAGES = 50
DEFAULT_TIMEOUT_S = 30

_KEY_HEADER = "x-api-key"
_RETRY_AFTER_HEADER = "retry-after"
_THROTTLED = 429
_PROBE_LIMIT = 1
#: Error bodies are quoted at most this far, the API key redacted first.
_EXCERPT_CHARS = 200
_REDACTED = "<api key>"
_SNAPSHOT_LIST_KEYS = ("data", "orderbooks")
_VENDOR_FIELDS = ("best_bid", "best_ask", "bid_depth", "ask_depth")
_CENTS_BOUNDS = (0, 100)
_CENTS_PER_DOLLAR = 100
_DOLLAR_DECIMALS = 4
_DOLLAR_BOUNDS = (0.0, 1.0)
_UTC = timezone.utc
_EPOCH = datetime(1970, 1, 1, tzinfo=_UTC)
_MILLISECOND = timedelta(milliseconds=1)


def urllib_get(url, params, headers, timeout):
    """Perform one HTTP GET over stdlib urllib — the default getter.

    This is the getter contract: anything injected in its place must
    accept and return the same shapes.

    Parameters
    ----------
    url : str
        Scheme, host, and path — no query string.
    params : dict
        Query parameters, URL-encoded here.
    headers : dict
        Request headers; the API key rides here.
    timeout : float
        Socket timeout in seconds.

    Returns
    -------
    tuple
        ``(status, headers, body)`` — int status, dict of response
        headers, raw body bytes. Non-2xx statuses are RETURNED (the
        caller decides what is retryable); only transport faults raise.

    Raises
    ------
    OSError
        On a transport fault — a refused connection, DNS failure, or
        timeout (``urllib.error.URLError`` is an ``OSError``).
    """
    full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    request = urllib.request.Request(full, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.getcode(), dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        found = dict(exc.headers.items()) if exc.headers is not None else {}
        return exc.code, found, exc.read()


def resolve_api_key(knobs, lookup=None):
    """Resolve the Predexon API key named by config without exposing it.

    Parameters
    ----------
    knobs : dict
        Resolved knobs carrying ``api_key_env``, the variable NAME.
    lookup : callable or None
        Environment lookup; defaults to ``os.environ.get``.

    Returns
    -------
    str
        The key material, for the request header only.

    Raises
    ------
    AssetError
        Naming the VARIABLE — never a value — when it is absent or empty.
    """
    lookup = os.environ.get if lookup is None else lookup
    name = knobs["api_key_env"]
    value = lookup(name, "")
    if not value:
        raise AssetError(
            [f"Predexon environment variable {name!r} is missing or empty — "
             "config carries the NAME, the environment carries the material"]
        )
    return value


def native_book(snapshot):
    """Express a record's ladders as Kalshi-native YES and NO bids.

    Predexon publishes YES asks; Kalshi's native book is two bid ladders,
    where a YES ask at ``p`` is a NO bid at ``1 - p``. Pure: reads the
    pack's own ``data`` shape (``[[price_dollars, size], ...]``) and
    returns both ladders best-first.

    Parameters
    ----------
    snapshot : dict
        A record's ``data`` — or anything with ``yes_bids`` / ``yes_asks``
        as ``[[price_dollars, size], ...]``; a missing side reads as empty.

    Returns
    -------
    dict
        ``{"yes_dollars": [[p, size], ...], "no_dollars": [[round(1 - p,
        4), size], ...]}``, both sorted by price DESCENDING.

    Raises
    ------
    AssetError
        If the input is not a dict, a side is not a list of pairs, or a
        price is not a dollar figure in ``[0, 1]``.
    """
    if not isinstance(snapshot, dict):
        raise AssetError(
            [f"snapshot must be a dict, got {type(snapshot).__name__}"]
        )
    yes = _dollar_levels(snapshot.get("yes_bids"), "yes_bids")
    asks = _dollar_levels(snapshot.get("yes_asks"), "yes_asks")
    no = [[round(1.0 - price, _DOLLAR_DECIMALS), size] for price, size in asks]
    return {
        "yes_dollars": sorted(yes, key=lambda level: level[0], reverse=True),
        "no_dollars": sorted(no, key=lambda level: level[0], reverse=True),
    }


def _finite(value):
    """Report whether ``value`` is a real, finite, non-bool number."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _integral(value):
    """Report whether ``value`` is a whole number (a bool is not)."""
    return _finite(value) and float(value).is_integer()


def _iso_ms(value):
    """Convert an ISO date/datetime string to epoch milliseconds, exactly."""
    return (parse_utc(value) - _EPOCH) // _MILLISECOND


def _ms_iso(ms):
    """Convert epoch milliseconds to an ISO UTC stamp with milliseconds, exactly."""
    return (_EPOCH + ms * _MILLISECOND).isoformat(timespec="milliseconds")


def _dollar_levels(levels, name):
    """Validate ``[[dollars, size], ...]`` levels; ``None`` reads as empty."""
    if levels is None:
        return []
    if not isinstance(levels, list):
        raise AssetError(
            [f"{name} must be a list of [price, size] pairs, got {levels!r}"]
        )
    out = []
    for i, level in enumerate(levels):
        if not isinstance(level, (list, tuple)) or len(level) != 2:
            raise AssetError(
                [f"{name} level {i} must be a [price, size] pair, got {level!r}"]
            )
        price, size = level
        if not _finite(price) or not _DOLLAR_BOUNDS[0] <= price <= _DOLLAR_BOUNDS[1]:
            raise AssetError(
                [f"{name} level {i}: price must be a dollar figure in "
                 f"{list(_DOLLAR_BOUNDS)}, got {price!r}"]
            )
        out.append([price, size])
    return out


def _cents_ladder(levels, name, descending, where):
    """Normalize vendor ``{price: cents, size}`` levels to sorted dollar pairs."""
    if levels is None:
        return []
    if not isinstance(levels, list):
        raise AssetError([f"{where} {name} must be a list of levels, got {levels!r}"])
    out = []
    for i, level in enumerate(levels):
        if not isinstance(level, dict):
            raise AssetError(
                [f"{where} {name} level {i} is not an object, got {level!r}"]
            )
        cents = level.get("price")
        if not _finite(cents) or not _CENTS_BOUNDS[0] <= cents <= _CENTS_BOUNDS[1]:
            raise AssetError(
                [f"{where} {name} level {i} price must be cents in "
                 f"{list(_CENTS_BOUNDS)}, got {cents!r}"]
            )
        out.append([round(cents / _CENTS_PER_DOLLAR, _DOLLAR_DECIMALS), level.get("size")])
    out.sort(key=lambda level: level[0], reverse=descending)
    return out


def _row(ticker, snapshot, index, label):
    """Normalize one vendor snapshot into the record ``data``, field order pinned."""
    where = f"{label}: snapshot {index}"
    stamp = snapshot.get("timestamp")
    if not _integral(stamp):
        raise AssetError(
            [f"{where} timestamp must be epoch milliseconds, got {stamp!r}"]
        )
    sequence = snapshot.get("sequence")
    if not _integral(sequence):
        raise AssetError(
            [f"{where} at timestamp {int(stamp)} has no integer sequence "
             f"(got {sequence!r}) — the dedup key is (timestamp, sequence), "
             "so the whole ticker is refused"]
        )
    values = {
        "ticker": ticker,
        "timestamp": int(stamp),
        "sequence": int(sequence),
        **{field: snapshot.get(field) for field in _VENDOR_FIELDS},
        "yes_bids": _cents_ladder(snapshot.get("yes_bids"), "yes_bids", True, where),
        "yes_asks": _cents_ladder(snapshot.get("yes_asks"), "yes_asks", False, where),
    }
    return {field: values[field] for field in L2_FIELDS}


def _snapshot_list(body, label):
    """Return one page's snapshot list: under ``data`` or, tolerated, ``orderbooks``."""
    for key in _SNAPSHOT_LIST_KEYS:
        batch = body.get(key)
        if isinstance(batch, list):
            for i, snapshot in enumerate(batch):
                if not isinstance(snapshot, dict):
                    raise AssetError(
                        [f"{label}: snapshot {i} is not an object, got "
                         f"{type(snapshot).__name__}"]
                    )
            return batch
    raise AssetError(
        [f"{label}: response carries no snapshot list under "
         f"{list(_SNAPSHOT_LIST_KEYS)}"]
    )


def _pagination(body, label):
    """Return ``(has_more, next_key)`` for one page; an absent block means no more."""
    block = body.get("pagination")
    if block is None:
        return False, None
    if not isinstance(block, dict):
        raise AssetError([f"{label}: pagination must be an object, got {block!r}"])
    key = block.get("pagination_key")
    if key is not None and not isinstance(key, str):
        raise AssetError(
            [f"{label}: pagination_key must be a string or null, got {key!r}"]
        )
    return bool(block.get("has_more")), key or None


def _json_object(body, url, label):
    """Decode a response body as one JSON object, or refuse loudly."""
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AssetError(
            [f"{label}: response from {url} is not JSON: {exc}"]
        ) from exc
    if not isinstance(parsed, dict):
        raise AssetError([f"{label}: response from {url} is not a JSON object"])
    return parsed


def _excerpt(body, secret):
    """Return the head of an error body for a message, the key redacted BEFORE the cut."""
    text = body.decode("utf-8", "replace")
    if secret:
        text = text.replace(secret, _REDACTED)
    return text[:_EXCERPT_CHARS]


def _retry_after(headers):
    """Return the seconds a numeric ``Retry-After`` header asks for, else ``None``."""
    for name, value in headers.items():
        if str(name).lower() == _RETRY_AFTER_HEADER:
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                return None
    return None


class RateLimiter:
    """Space calls at least ``min_interval_s`` apart on a moving deadline.

    Parameters
    ----------
    min_interval_s : float
        Seconds that must separate consecutive ``wait`` returns; ``0``
        disables pacing.
    clock : callable
        Returns the current time in seconds; only differences matter.
    sleeper : callable
        Blocks for the given number of seconds.

    Examples
    --------
    Pace a loop to one request per second::

        limiter = RateLimiter(1.0, time.time, time.sleep)
        for url in urls:
            limiter.wait()  # the first call returns at once
            fetch(url)
    """

    def __init__(self, min_interval_s, clock, sleeper):
        self._gap = float(min_interval_s)
        self._clock = clock
        self._sleeper = sleeper
        self._last = None

    def wait(self):
        """Block until the interval since the previous call has elapsed.

        Returns
        -------
        None
            Work done between calls is credited; a clock that steps
            backwards reads as no time elapsed, never as credit.
        """
        now = self._clock()
        if self._last is not None:
            elapsed = max(0.0, now - self._last)
            if elapsed < self._gap:
                self._sleeper(self._gap - elapsed)
                now = max(self._clock(), self._last + self._gap)
        self._last = now


class PredexonConnector(Connector):
    """Predexon Kalshi L2 snapshots, paced, retried, and cursored per ticker.

    Parameters
    ----------
    getter : callable or None
        ``getter(url, params, headers, timeout) -> (status, headers,
        body)``; defaults to :func:`urllib_get`. The one transport seam.
    clock : callable or None
        Epoch seconds; defaults to ``time.time``. Paces requests and
        supplies the window end when ``end`` is not declared.
    sleeper : callable or None
        Blocks for a number of seconds; defaults to ``time.sleep``. Every
        wait — pacing and backoff — goes through it.

    Examples
    --------
    Pull one contract's books for a day, paced at one request per second::

        connector = PredexonConnector()
        config = {
            "tickers": ["KXHIGHNY-26MAR01-B50"],
            "coverage_start": "2026-03-01",
            "end": "2026-03-02",
        }
        messages = list(connector.read(config, ["l2_snapshots"], {}, "backfill"))

    Drive it without a network — the seams a test injects::

        connector = PredexonConnector(
            getter=lambda url, params, headers, timeout: (200, {}, b'{"data": []}'),
            clock=lambda: 0.0,
            sleeper=lambda seconds: None,
        )
    """

    def __init__(self, getter=None, clock=None, sleeper=None):
        self._getter = urllib_get if getter is None else getter
        self._clock = time.time if clock is None else clock
        self._sleeper = time.sleep if sleeper is None else sleeper

    def spec(self):
        """Declare the default-deny Predexon configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {"params": {
            "tickers": {
                "required": True,
                "notes": "Contract tickers in PULL ORDER, each with its own "
                         "cursor: a ticker added later backfills from "
                         "coverage_start while the others resume.",
            },
            "coverage_start": {
                "required": True,
                "notes": "ISO date/datetime the history starts at; the first "
                         "pull's window opens here, later pulls open at the "
                         "ticker's cursor.",
            },
            "end": {
                "notes": "Optional EXCLUSIVE ISO upper bound; absent means "
                         "'up to now'. A study with a hard data cut declares "
                         "it here rather than trimming afterwards.",
            },
            "base_url": {
                "notes": f"API root; default {DEFAULT_BASE_URL}. The "
                         f"orderbooks path {ORDERBOOKS_PATH} appends.",
            },
            "api_key_env": {
                "secret": True,
                "notes": "Environment-variable NAME holding the API key, sent "
                         f"as the {_KEY_HEADER} header; default {DEFAULT_KEY_ENV}. "
                         "An empty variable refuses by name.",
            },
            "limit": {
                "notes": f"Snapshots per page in {list(LIMIT_BOUNDS)}; default "
                         f"{DEFAULT_LIMIT}, the vendor maximum. Fewer pages "
                         "means fewer paced requests.",
            },
            "min_interval_s": {
                "notes": "Seconds between consecutive requests, retries "
                         f"included; default {DEFAULT_MIN_INTERVAL_S} (the "
                         "vendor's one-request-per-second bucket). 0 disables "
                         "pacing and earns 429s instead.",
            },
            "retries": {
                "notes": "Attempts per request on throttling and network "
                         f"faults; default {DEFAULT_RETRIES}. Server faults "
                         f"(5xx) get at least {SERVER_FAULT_ATTEMPTS_FLOOR} "
                         "attempts because their bursts outlast a short budget.",
            },
            "retry_floor_s": {
                "notes": "First backoff in seconds, doubling per attempt; "
                         f"default {DEFAULT_RETRY_FLOOR_S}. A numeric "
                         "Retry-After on a 429 overrides it.",
            },
            "max_pages": {
                "notes": "Pages followed per ticker per pull; default "
                         f"{DEFAULT_MAX_PAGES}. Exhausting it LOGs and stops; "
                         "the cursor covers what was fetched, so the next "
                         "pull resumes.",
            },
            "timeout_s": {
                "notes": f"Socket timeout per request in seconds; default "
                         f"{DEFAULT_TIMEOUT_S}. A timeout is retried like a "
                         "network fault.",
            },
        }}

    def resolve_knobs(self, config):
        """Validate config values and apply the pack's defaults.

        Parameters
        ----------
        config : dict
            Connector configuration after platform-reserved keys are
            removed.

        Returns
        -------
        dict
            Fully resolved knobs, plus ``coverage_start_ms`` and ``end_ms``
            (``None`` when ``end`` is not declared).

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
        tickers = config.get("tickers")
        if (
            not isinstance(tickers, list)
            or not tickers
            or not all(isinstance(t, str) and t for t in tickers)
        ):
            problems.append(
                f"config.tickers must be a non-empty list of strings, got {tickers!r}"
            )
        elif len(set(tickers)) != len(tickers):
            problems.append(f"config.tickers must not repeat, got {tickers!r}")
        coverage_start = config.get("coverage_start")
        problems.extend(_iso_problems("coverage_start", coverage_start))
        end = config.get("end", DEFAULT_END)
        if end != DEFAULT_END:
            problems.extend(_iso_problems("end", end))
        base_url = config.get("base_url", DEFAULT_BASE_URL)
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            problems.append(f"config.base_url must be an http(s) URL, got {base_url!r}")
        api_key_env = config.get("api_key_env", DEFAULT_KEY_ENV)
        if not isinstance(api_key_env, str) or not api_key_env:
            problems.append(
                "config.api_key_env must be a non-empty environment-variable name"
            )
        limit = config.get("limit", DEFAULT_LIMIT)
        low, high = LIMIT_BOUNDS
        if isinstance(limit, bool) or not isinstance(limit, int) or not low <= limit <= high:
            problems.append(
                f"config.limit must be an int in {list(LIMIT_BOUNDS)}, got {limit!r}"
            )
        # Read each knob ONCE: the value validated here is the value returned.
        counts = {
            "retries": config.get("retries", DEFAULT_RETRIES),
            "max_pages": config.get("max_pages", DEFAULT_MAX_PAGES),
        }
        for name, value in counts.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                problems.append(f"config.{name} must be an int >= 1, got {value!r}")
        seconds = {
            "min_interval_s": config.get("min_interval_s", DEFAULT_MIN_INTERVAL_S),
            "retry_floor_s": config.get("retry_floor_s", DEFAULT_RETRY_FLOOR_S),
        }
        for name, value in seconds.items():
            if not _finite(value) or value < 0:
                problems.append(
                    f"config.{name} must be a finite number >= 0, got {value!r}"
                )
        timeout_s = config.get("timeout_s", DEFAULT_TIMEOUT_S)
        if not _finite(timeout_s) or timeout_s <= 0:
            problems.append(
                f"config.timeout_s must be a positive number, got {timeout_s!r}"
            )
        if problems:
            raise AssetError(problems)
        coverage_start_ms = _iso_ms(coverage_start)
        end_ms = _iso_ms(end) if end else None
        if end_ms is not None and end_ms <= coverage_start_ms:
            raise AssetError(
                [f"config.end {end!r} must be after config.coverage_start "
                 f"{coverage_start!r}"]
            )
        return {
            "tickers": list(tickers),
            "coverage_start": coverage_start,
            "coverage_start_ms": coverage_start_ms,
            "end": end,
            "end_ms": end_ms,
            "base_url": base_url,
            "api_key_env": api_key_env,
            "limit": limit,
            **seconds,
            **counts,
            "timeout_s": timeout_s,
        }

    def _headers(self, knobs):
        """Resolve the key at the request boundary; it lives only in this header."""
        return {_KEY_HEADER: resolve_api_key(knobs), "Accept": "application/json"}

    def _url(self, knobs):
        """Build the endpoint URL under the declared root, query-free."""
        return knobs["base_url"].rstrip("/") + ORDERBOOKS_PATH

    def _limiter(self, knobs):
        """Build a fresh limiter over the injected clock and sleeper."""
        return RateLimiter(knobs["min_interval_s"], self._clock, self._sleeper)

    def _window(self, knobs, cursor):
        """Return ``(start_ms, end_ms)``: cursor else coverage start, to ``end`` else now."""
        start_ms = knobs["coverage_start_ms"] if cursor is None else cursor[0]
        end_ms = knobs["end_ms"]
        if end_ms is None:
            end_ms = int(self._clock() * 1000)
        return start_ms, end_ms

    def _get_json(self, url, params, headers, knobs, limiter, label):
        """Run one paced, retried GET and return its JSON object body."""
        attempts = 0
        while True:
            attempts += 1
            limiter.wait()
            try:
                status, found, body = self._getter(url, params, headers, knobs["timeout_s"])
            except OSError as exc:
                failure, ceiling = f"network error: {exc}", knobs["retries"]
                delay = _backoff(knobs, attempts)
            else:
                if 200 <= status < 300:
                    return _json_object(body, url, label)
                if status == _THROTTLED:
                    failure, ceiling = f"HTTP {status}", knobs["retries"]
                    delay = _retry_after(found)
                    if delay is None:
                        delay = _backoff(knobs, attempts)
                elif 500 <= status < 600:
                    failure = f"HTTP {status}"
                    ceiling = max(knobs["retries"], SERVER_FAULT_ATTEMPTS_FLOOR)
                    delay = _backoff(knobs, attempts)
                else:
                    raise AssetError(
                        [f"{label}: HTTP {status} from {url}: "
                         f"{_excerpt(body, headers.get(_KEY_HEADER))!r}"]
                    )
            if attempts >= ceiling:
                raise AssetError(
                    [f"{label}: {failure} — giving up on {url} after "
                     f"{attempts} attempt(s)"]
                )
            if delay > 0:
                self._sleeper(delay)

    def _collect(self, knobs, ticker, start_ms, end_ms, headers, limiter):
        """Collect one ticker's window of snapshots, plus the LOG lines the walk produced."""
        label = f"ticker {ticker!r}"
        url = self._url(knobs)
        params = {"ticker": ticker, "start_time": start_ms, "end_time": end_ms,
                  "limit": knobs["limit"]}
        snapshots, logs = [], []
        key, pages = None, 0
        while True:
            query = dict(params)
            if key is not None:
                query["pagination_key"] = key
            body = self._get_json(url, query, headers, knobs, limiter, label)
            snapshots.extend(_snapshot_list(body, label))
            pages += 1
            has_more, next_key = _pagination(body, label)
            if not has_more:
                break
            if next_key is None:
                logs.append(
                    f"{label}: has_more is true but pagination_key is null after "
                    f"{pages} page(s) — stopping rather than looping; the cursor "
                    "covers what was fetched"
                )
                break
            if next_key == key:
                raise AssetError(
                    [f"{label}: pagination_key {next_key!r} did not advance — "
                     "refusing an infinite loop"]
                )
            if pages >= knobs["max_pages"]:
                logs.append(
                    f"{label}: max_pages {knobs['max_pages']} reached with more "
                    "remaining — stopping; the next pull resumes from the cursor"
                )
                break
            key = next_key
        return snapshots, logs

    def _cursors(self, stream_state, tickers):
        """Validate every ticker's cursor pair (or None) before any request is spent."""
        if not isinstance(stream_state, dict):
            raise AssetError(
                [f"state for {L2_STREAM!r} must be a dict of per-ticker cursors, "
                 f"got {stream_state!r}"]
            )
        cursors = {}
        for ticker in tickers:
            cursor = stream_state.get(ticker)
            if cursor is None:
                cursors[ticker] = None
                continue
            if (
                not isinstance(cursor, dict)
                or set(cursor) != {"timestamp", "sequence"}
                or not _integral(cursor["timestamp"])
                or not _integral(cursor["sequence"])
            ):
                raise AssetError(
                    [f"ticker {ticker!r}: cursor must be {{'timestamp': ms, "
                     f"'sequence': int}}, got {cursor!r}"]
                )
            cursors[ticker] = (int(cursor["timestamp"]), int(cursor["sequence"]))
        return cursors

    def _rows(self, ticker, snapshots):
        """Return normalized rows, deduplicated on ``(timestamp, sequence)``, ascending."""
        label = f"ticker {ticker!r}"
        seen = {}
        for index, snapshot in enumerate(snapshots):
            row = _row(ticker, snapshot, index, label)
            seen.setdefault((row["timestamp"], row["sequence"]), row)
        return [seen[key] for key in sorted(seen)]

    def check(self, config):
        """Validate config and the key, then probe one snapshot.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        None
            Silence means the endpoint answered with a snapshot list.

        Raises
        ------
        AssetError
            If config, the named key, or the probe fails — or the window
            is empty because ``coverage_start`` is not before now.
        """
        knobs = self.resolve_knobs(config)
        headers = self._headers(knobs)
        ticker = knobs["tickers"][0]
        label = f"ticker {ticker!r}"
        start_ms, end_ms = self._window(knobs, None)
        if start_ms >= end_ms:
            raise AssetError(
                [f"config.coverage_start {knobs['coverage_start']!r} is not before "
                 f"the window end ({end_ms} ms) — nothing to probe"]
            )
        params = {"ticker": ticker, "start_time": start_ms, "end_time": end_ms,
                  "limit": _PROBE_LIMIT}
        body = self._get_json(
            self._url(knobs), params, headers, knobs, self._limiter(knobs), label
        )
        _snapshot_list(body, label)

    def discover(self, config):
        """Describe the snapshot stream without touching the vendor.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        list
            One stream declaration for ``l2_snapshots``.

        Raises
        ------
        AssetError
            If config values are invalid.
        """
        self.resolve_knobs(config)
        return [{
            "stream": L2_STREAM,
            "schema": {"fields": list(L2_FIELDS)},
            "primary_key": list(L2_KEY_FIELDS),
        }]

    def read(self, config, streams, state, mode):
        """Emit the schema, cursor-filtered snapshots, and one checkpoint.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list
            Requested streams; only ``l2_snapshots`` exists.
        state : dict
            Prior mode-keyed checkpoint: ``{stream: {ticker: {"timestamp",
            "sequence"}}}``.
        mode : str
            ``backfill`` or ``live``; the logic is identical, the platform
            keys the checkpoints apart.

        Yields
        ------
        dict
            Onboarding protocol messages — SCHEMA, LOG, RECORD, STATE.

        Raises
        ------
        AssetError
            If arguments, config, the key, a response, or a snapshot are
            invalid.
        """
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        if mode not in MODES:
            raise AssetError([f"mode must be one of {MODES}, got {mode!r}"])
        knobs = self.resolve_knobs(config)
        headers = self._headers(knobs)
        limiter = self._limiter(knobs)
        new_state = {key: dict(value) for key, value in state.items()
                     if isinstance(value, dict)}
        for stream in streams:
            if stream != L2_STREAM:
                raise AssetError(
                    [f"unknown stream {stream!r}; discovered: {[L2_STREAM]}"]
                )
            cursors = self._cursors(state.get(stream, {}), knobs["tickers"])
            stream_state = new_state.setdefault(stream, {})
            yield {
                "protocol": PROTOCOL,
                "type": "SCHEMA",
                "stream": stream,
                "schema": {"fields": list(L2_FIELDS)},
            }
            for ticker in knobs["tickers"]:
                cursor = cursors[ticker]
                start_ms, end_ms = self._window(knobs, cursor)
                if start_ms >= end_ms:
                    yield {
                        "protocol": PROTOCOL, "type": "LOG", "level": "info",
                        "message": f"ticker {ticker!r}: window start {start_ms} is "
                                   f"not before end {end_ms} — nothing to pull",
                    }
                    continue
                snapshots, logs = self._collect(
                    knobs, ticker, start_ms, end_ms, headers, limiter
                )
                for message in logs:
                    yield {"protocol": PROTOCOL, "type": "LOG",
                           "level": "warning", "message": message}
                latest = cursor
                for row in self._rows(ticker, snapshots):
                    key = (row["timestamp"], row["sequence"])
                    if cursor is not None and key <= cursor:
                        continue  # already durable per the checkpoint
                    yield {
                        "protocol": PROTOCOL,
                        "type": "RECORD",
                        "stream": stream,
                        "effective_date": _ms_iso(row["timestamp"]),
                        "kind": "observation",
                        "data": row,
                    }
                    if latest is None or key > latest:
                        latest = key
                if latest is not None:
                    stream_state[ticker] = {"timestamp": latest[0], "sequence": latest[1]}
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}


def _iso_problems(name, value):
    """Return the problems with one ISO knob: not a string, empty, or unparseable."""
    if not isinstance(value, str) or not value:
        return [f"config.{name} must be an ISO date/datetime string, got {value!r}"]
    try:
        parse_utc(value)
    except AssetError:
        return [f"config.{name} must be an ISO date/datetime, got {value!r}"]
    return []


def _backoff(knobs, attempt):
    """Return the backoff after the ``attempt``-th failure: the floor, doubled per attempt."""
    return knobs["retry_floor_s"] * (2 ** (attempt - 1))
