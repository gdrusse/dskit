"""Alpaca NBBO quotes reduced to one bid/ask per minute, at the boundary.

The raw feed is unusable as a stored asset: the five-name cohort averages
about three million NBBO updates per session, so a decade is billions of
records. What every downstream question actually needs is a *point* price
that does not bounce between the buyer's and the seller's side — the last
prevailing quote of each minute. This pack therefore reduces in flight:
pages of raw quotes stream through a fixed-size fold and only the minute
row reaches disk. Raw quotes are never stored.

The pack owns Alpaca's quote transport, the minute vocabulary, the
regular-hours calendar, request pacing, and a resumable per-symbol
cursor. Projects declare symbols, dates, tape, session hours, and the
environment-variable names holding credentials. Credential material never
enters config.

Transport is stdlib HTTP, not ``alpaca-py``: the vendor SDK materializes
a whole ``QuoteSet`` per request, which is the one thing this connector
exists to avoid.
"""

from __future__ import annotations

import gzip
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from ..base import AssetError, MODES, parse_utc
from ..connector import PROTOCOL, Connector
from .alpaca import DEFAULT_KEY_ENV, DEFAULT_SECRET_ENV, resolve_credentials

__all__ = [
    "DEFAULT_BUDGET_SECONDS",
    "DEFAULT_FEED",
    "DEFAULT_PAGE_LIMIT",
    "DEFAULT_REQUESTS_PER_MINUTE",
    "DEFAULT_RTH_END_MINUTES",
    "DEFAULT_RTH_START_MINUTES",
    "DEFAULT_SESSION_TZ",
    "QUOTE_FIELDS",
    "QUOTE_KEY_FIELDS",
    "QUOTE_STREAM",
    "AlpacaQuoteMinutesConnector",
    "minute_rows",
]

BACKFILL_MODE, LIVE_MODE = MODES
QUOTE_STREAM = "quote_minutes"
QUOTE_KEY_FIELDS = ("symbol", "ts")
QUOTE_FIELDS = (
    "symbol", "ts", "bid", "ask", "mid", "spread", "spread_bps",
    "bid_size", "ask_size", "bid_exchange", "ask_exchange",
    "quote_ts", "quote_age_ms", "n_quotes", "n_crossed", "n_locked",
)
DEFAULT_FEED = "sip"
DEFAULT_SESSION_TZ = "America/New_York"
DEFAULT_RTH_START_MINUTES = 570
DEFAULT_RTH_END_MINUTES = 960
DEFAULT_PAGE_LIMIT = 10000
DEFAULT_REQUESTS_PER_MINUTE = 180
DEFAULT_BUDGET_SECONDS = 0
DEFAULT_MAX_AGE_SECONDS = 60

_FEEDS = ("sip", "iex")
_ENDPOINT = "https://data.alpaca.markets/v2/stocks/quotes"
_RETRY_CODES = (429, 500, 502, 503, 504)
_MAX_ATTEMPTS = 8
_UTC = timezone.utc
_MINUTE_MS = 60000


def _ms(dt):
    """Epoch milliseconds for an aware datetime."""
    return int(dt.timestamp() * 1000.0)


def _quote_ms(stamp):
    """Epoch milliseconds for one RFC-3339 quote stamp.

    Alpaca stamps quotes with nanosecond precision, which
    ``datetime.fromisoformat`` cannot parse on every supported runtime,
    so the fractional part is cut to milliseconds by hand.
    """
    head, _, tail = stamp.partition(".")
    if not tail:
        return _ms(datetime.strptime(head.rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
                   .replace(tzinfo=_UTC))
    digits = tail.rstrip("Z")
    milli = int((digits + "000")[:3])
    base = datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_UTC)
    return _ms(base) + milli


def _finite(value):
    """Report whether ``value`` is a real, finite number."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def minute_rows(symbol, quotes, max_age_ms=DEFAULT_MAX_AGE_SECONDS * 1000):
    """Fold ascending raw quotes into one row per minute boundary.

    The row for minute ``t`` carries the LAST two-sided quote stamped in
    ``[t, t+60s)`` — the prevailing bid and ask at the boundary that
    closes the minute, which is the instant the minute's last trade also
    sits at. A quote that is crossed (ask below bid), locked (ask equal
    to bid), or one-sided is counted but never selected; a minute whose
    only quotes are of that kind yields no row.

    Parameters
    ----------
    symbol : str
        The symbol every quote belongs to.
    quotes : iterable
        Raw Alpaca quote dicts in ascending time order.
    max_age_ms : int
        Reject a selected quote older than this at the boundary.

    Yields
    ------
    dict
        One :data:`QUOTE_FIELDS`-shaped row per minute, in time order.
    """
    bucket = None
    state = None
    for quote in quotes:
        stamp = _quote_ms(quote["t"])
        minute = stamp - (stamp % _MINUTE_MS)
        if bucket is not None and minute != bucket:
            row = _emit(symbol, bucket, state, max_age_ms)
            if row is not None:
                yield row
            state = None
        if minute != bucket:
            bucket = minute
        if state is None:
            state = {"n": 0, "crossed": 0, "locked": 0, "best": None}
        state["n"] += 1
        bid, ask = quote.get("bp"), quote.get("ap")
        if not _finite(bid) or not _finite(ask) or bid <= 0.0 or ask <= 0.0:
            continue
        if ask < bid:
            state["crossed"] += 1
            continue
        if ask == bid:
            state["locked"] += 1
            continue
        state["best"] = (stamp, quote)
    if bucket is not None:
        row = _emit(symbol, bucket, state, max_age_ms)
        if row is not None:
            yield row


def _emit(symbol, minute, state, max_age_ms):
    """Build one minute row, or ``None`` when nothing usable was seen."""
    if state is None or state["best"] is None:
        return None
    stamp, quote = state["best"]
    age = minute + _MINUTE_MS - stamp
    if age > max_age_ms:
        return None
    bid, ask = float(quote["bp"]), float(quote["ap"])
    mid = 0.5 * (bid + ask)
    spread = ask - bid
    when = datetime.fromtimestamp(minute / 1000.0, tz=_UTC)
    quoted = datetime.fromtimestamp(stamp / 1000.0, tz=_UTC)
    return {
        "symbol": symbol,
        "ts": when.isoformat().replace("+00:00", "Z"),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread": spread,
        "spread_bps": 10000.0 * spread / mid,
        "bid_size": (None if quote.get("bs") is None else int(quote["bs"])),
        "ask_size": (None if quote.get("as") is None else int(quote["as"])),
        "bid_exchange": quote.get("bx"),
        "ask_exchange": quote.get("ax"),
        "quote_ts": quoted.isoformat().replace("+00:00", "Z"),
        "quote_age_ms": int(age),
        "n_quotes": int(state["n"]),
        "n_crossed": int(state["crossed"]),
        "n_locked": int(state["locked"]),
    }


class _Pacer:
    """A token bucket that keeps a run under the vendor's request rate."""

    def __init__(self, per_minute):
        self._gap = 60.0 / float(per_minute)
        self._next = 0.0

    def wait(self):
        """Block until the next request is allowed."""
        now = time.monotonic()
        if now < self._next:
            time.sleep(self._next - now)
            now = time.monotonic()
        self._next = now + self._gap


class AlpacaQuoteMinutesConnector(Connector):
    """Alpaca NBBO quotes, reduced to one minute-boundary row per bar.

    Parameters
    ----------
    None
        The connector is stateless; every setting comes from config.

    Examples
    --------
    Discover the reduced stream without touching the network::

        connector = AlpacaQuoteMinutesConnector()
        streams = connector.discover({
            "symbols": ["LLY"],
            "start": "2026-02-25",
            "end": "2026-02-26",
        })
        streams[0]["stream"]  # 'quote_minutes'
    """

    def spec(self):
        """Declare the default-deny quote configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {"params": {
            "symbols": {
                "required": True,
                "notes": "Symbols in PULL ORDER. Each carries its own "
                         "cursor, so a budgeted run finishes the first "
                         "name before it starts the second and the "
                         "decisive names can be declared first.",
            },
            "start": {
                "required": True,
                "notes": "Earliest ISO date to fetch (session date, "
                         "inclusive).",
            },
            "end": {
                "required": True,
                "notes": "EXCLUSIVE ISO upper bound. A study with a hard "
                         "data cut declares the cut here, so a quote at "
                         "or after it is never requested.",
            },
            "feed": {
                "notes": f"Market-data tape in {_FEEDS}; default "
                         f"{DEFAULT_FEED}. On sip these are consolidated "
                         "NBBO updates.",
            },
            "session_tz": {
                "notes": "IANA zone the session hours are expressed in; "
                         f"default {DEFAULT_SESSION_TZ}.",
            },
            "rth_start_minutes": {
                "notes": "Minutes after local midnight the fetched "
                         f"session opens; default {DEFAULT_RTH_START_MINUTES}.",
            },
            "rth_end_minutes": {
                "notes": "Minutes after local midnight the fetched "
                         f"session closes; default {DEFAULT_RTH_END_MINUTES}. "
                         "Only regular hours are requested: the reduction "
                         "is for bars a study scores, and out-of-hours "
                         "quotes are most of the raw volume.",
            },
            "max_age_seconds": {
                "notes": "Reject a boundary quote staler than this; "
                         f"default {DEFAULT_MAX_AGE_SECONDS}. At the "
                         "default no row is ever built from a quote "
                         "outside its own minute.",
            },
            "page_limit": {
                "notes": f"Raw quotes per request; default {DEFAULT_PAGE_LIMIT} "
                         "(the vendor maximum). Bounds peak memory: one "
                         "page is the largest object this connector holds.",
            },
            "requests_per_minute": {
                "notes": "Client-side pacing; default "
                         f"{DEFAULT_REQUESTS_PER_MINUTE}, under the free "
                         "tier's 200. Exceeding it earns 429s, which are "
                         "retried with backoff and cost more than pacing.",
            },
            "budget_seconds": {
                "notes": "Stop a pull after this many seconds and "
                         "checkpoint what completed; default "
                         f"{DEFAULT_BUDGET_SECONDS} (unbounded). A long "
                         "backfill is then a sequence of bounded, "
                         "resumable jobs rather than one job that loses "
                         "everything when it is interrupted.",
            },
            "key_env": {
                "secret": True,
                "notes": "Environment-variable name holding the Alpaca key "
                         f"id; default {DEFAULT_KEY_ENV}.",
            },
            "secret_env": {
                "secret": True,
                "notes": "Environment-variable name holding the Alpaca "
                         f"secret; default {DEFAULT_SECRET_ENV}.",
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
            Fully resolved request knobs.

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
            or not all(isinstance(s, str) and s for s in symbols)
        ):
            problems.append(
                f"config.symbols must be a non-empty list of strings, "
                f"got {symbols!r}"
            )
        elif len(set(symbols)) != len(symbols):
            problems.append(f"config.symbols must not repeat, got {symbols!r}")
        for name in ("start", "end"):
            value = config.get(name)
            if not isinstance(value, str) or not value:
                problems.append(
                    f"config.{name} must be an ISO string, got {value!r}"
                )
        feed = config.get("feed", DEFAULT_FEED)
        if feed not in _FEEDS:
            problems.append(f"config.feed must be one of {_FEEDS}, got {feed!r}")
        tz_name = config.get("session_tz", DEFAULT_SESSION_TZ)
        if not isinstance(tz_name, str) or not tz_name:
            problems.append(
                f"config.session_tz must be an IANA zone name, got {tz_name!r}"
            )
        else:
            try:
                from zoneinfo import ZoneInfo

                ZoneInfo(tz_name)
            except Exception:
                problems.append(f"config.session_tz {tz_name!r} is not a known zone")
        opens = config.get("rth_start_minutes", DEFAULT_RTH_START_MINUTES)
        closes = config.get("rth_end_minutes", DEFAULT_RTH_END_MINUTES)
        for name, value in (("rth_start_minutes", opens), ("rth_end_minutes", closes)):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 1440
            ):
                problems.append(
                    f"config.{name} must be an int in [0, 1440], got {value!r}"
                )
        if (
            isinstance(opens, int) and isinstance(closes, int)
            and not isinstance(opens, bool) and not isinstance(closes, bool)
            and closes <= opens
        ):
            problems.append(
                f"config.rth_end_minutes {closes} must be after "
                f"rth_start_minutes {opens}"
            )
        positives = (
            ("max_age_seconds", config.get("max_age_seconds", DEFAULT_MAX_AGE_SECONDS)),
            ("page_limit", config.get("page_limit", DEFAULT_PAGE_LIMIT)),
            ("requests_per_minute",
             config.get("requests_per_minute", DEFAULT_REQUESTS_PER_MINUTE)),
        )
        for name, value in positives:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                problems.append(f"config.{name} must be an int >= 1, got {value!r}")
        if isinstance(positives[1][1], int) and not isinstance(positives[1][1], bool):
            if positives[1][1] > DEFAULT_PAGE_LIMIT:
                problems.append(
                    f"config.page_limit must not exceed {DEFAULT_PAGE_LIMIT}, "
                    f"got {positives[1][1]!r}"
                )
        budget = config.get("budget_seconds", DEFAULT_BUDGET_SECONDS)
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            problems.append(
                f"config.budget_seconds must be an int >= 0, got {budget!r}"
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
        if parse_utc(config["end"]) <= parse_utc(config["start"]):
            raise AssetError([
                f"config.end {config['end']!r} must be after "
                f"config.start {config['start']!r}"
            ])
        return {
            "symbols": list(symbols),
            "start": config["start"],
            "end": config["end"],
            "feed": feed,
            "session_tz": tz_name,
            "rth_start_minutes": opens,
            "rth_end_minutes": closes,
            "max_age_seconds": positives[0][1],
            "page_limit": positives[1][1],
            "requests_per_minute": positives[2][1],
            "budget_seconds": budget,
            "key_env": key_env,
            "secret_env": secret_env,
        }

    def _credentials(self, knobs):
        """Resolve the named key pair at the vendor boundary."""
        return resolve_credentials(knobs)

    def _sessions(self, knobs):
        """Yield ``(start, end)`` UTC pairs, one per weekday session.

        ``start`` and ``end`` name SESSION DATES on the calendar, read
        off the declaration itself rather than off an instant projected
        into the session zone — a bound written as midnight UTC lands on
        the previous evening in New York, and would otherwise pull a
        session the study said not to read.
        """
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(knobs["session_tz"])
        first = parse_utc(knobs["start"]).date()
        last = parse_utc(knobs["end"]).date()
        bound = parse_utc(knobs["end"])
        day = first
        while day < last:
            if day.weekday() < 5:
                opens = datetime(
                    day.year, day.month, day.day, tzinfo=zone
                ) + timedelta(minutes=knobs["rth_start_minutes"])
                closes = datetime(
                    day.year, day.month, day.day, tzinfo=zone
                ) + timedelta(minutes=knobs["rth_end_minutes"])
                opens, closes = opens.astimezone(_UTC), closes.astimezone(_UTC)
                if closes > bound:
                    closes = bound
                if closes > opens:
                    yield opens, closes
            day += timedelta(days=1)

    def _request(self, url, headers, pacer):
        """One paced, retried GET returning the decoded JSON body."""
        delay = 2.0
        for attempt in range(_MAX_ATTEMPTS):
            pacer.wait()
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=180) as response:
                    raw = response.read()
                    encoding = response.headers.get("Content-Encoding", "")
                body = gzip.decompress(raw) if encoding == "gzip" else raw
                return json.loads(body)
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRY_CODES or attempt == _MAX_ATTEMPTS - 1:
                    raise
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == _MAX_ATTEMPTS - 1:
                    raise
            time.sleep(delay)
            delay = min(delay * 2.0, 60.0)
        raise AssetError(["Alpaca quote request exhausted its retries"])

    def _pages(self, symbol, start, end, knobs, headers, pacer):
        """Yield ascending raw quote lists, one per vendor page."""
        token = None
        while True:
            query = {
                "symbols": symbol,
                "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": knobs["page_limit"],
                "feed": knobs["feed"],
            }
            if token:
                query["page_token"] = token
            url = _ENDPOINT + "?" + urllib.parse.urlencode(query)
            body = self._request(url, headers, pacer)
            yield body.get("quotes", {}).get(symbol) or []
            token = body.get("next_page_token")
            if not token:
                return

    def _fetch(self, knobs, cursors):
        """Yield ``(symbol, row)`` minute rows, symbol by symbol.

        A symbol's sessions run in time order and its cursor advances
        only when a whole session is done, so an interrupted or budgeted
        run resumes on a session boundary and never half-writes a day.
        """
        key, secret = self._credentials(knobs)
        headers = {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept-Encoding": "gzip",
        }
        pacer = _Pacer(knobs["requests_per_minute"])
        budget = knobs["budget_seconds"]
        deadline = time.monotonic() + budget if budget else None
        max_age_ms = knobs["max_age_seconds"] * 1000
        for symbol in knobs["symbols"]:
            done = cursors.get(symbol, "")
            done_dt = parse_utc(done) if done else None
            for opens, closes in self._sessions(knobs):
                if done_dt is not None and closes <= done_dt:
                    continue
                if deadline is not None and time.monotonic() >= deadline:
                    return
                pages = self._pages(symbol, opens, closes, knobs, headers, pacer)
                for row in minute_rows(symbol, _flatten(pages), max_age_ms):
                    yield symbol, row
                yield symbol, {"_cursor": closes.isoformat().replace("+00:00", "Z")}

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
            If config, credentials, or the probe fails.
        """
        knobs = self.resolve_knobs(config)
        key, secret = self._credentials(knobs)
        query = {
            "symbols": knobs["symbols"][0],
            "start": "2024-01-03T15:00:00Z",
            "end": "2024-01-03T15:00:10Z",
            "limit": 1,
            "feed": knobs["feed"],
        }
        url = _ENDPOINT + "?" + urllib.parse.urlencode(query)
        try:
            self._request(
                url,
                {
                    "APCA-API-KEY-ID": key,
                    "APCA-API-SECRET-KEY": secret,
                    "Accept-Encoding": "gzip",
                },
                _Pacer(knobs["requests_per_minute"]),
            )
        except Exception as exc:
            raise AssetError([
                "Alpaca quote probe failed; check credentials, plan entitlement "
                "and network"
            ]) from exc

    def discover(self, config):
        """Describe the reduced stream without touching a vendor.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        list
            One stream declaration for ``quote_minutes``.

        Raises
        ------
        AssetError
            If config values are invalid.
        """
        self.resolve_knobs(config)
        return [{
            "stream": QUOTE_STREAM,
            "schema": {"fields": list(QUOTE_FIELDS)},
            "primary_key": list(QUOTE_KEY_FIELDS),
        }]

    def read(self, config, streams, state, mode):
        """Emit schema, minute rows, and one per-symbol checkpoint.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list
            Requested streams; only ``quote_minutes`` exists.
        state : dict
            Prior mode-keyed checkpoint, holding ``cursors`` — one
            completed-session stamp PER SYMBOL, so a symbol added to the
            cohort later backfills from ``start`` while the others
            resume.
        mode : str
            Only ``backfill``; a reduced historical quote has no live
            meaning under the free tier's delay.

        Yields
        ------
        dict
            Onboarding protocol messages.

        Raises
        ------
        AssetError
            If arguments, config, or a vendor request fail.
        """
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        if mode not in MODES:
            raise AssetError([f"mode must be one of {MODES}, got {mode!r}"])
        if mode != BACKFILL_MODE:
            raise AssetError([
                f"{QUOTE_STREAM} is a backfill-only stream, got mode {mode!r}"
            ])
        knobs = self.resolve_knobs(config)
        new_state = {key: dict(value) for key, value in state.items()}
        for stream in streams:
            if stream != QUOTE_STREAM:
                raise AssetError(
                    [f"unknown stream {stream!r}; discovered: {[QUOTE_STREAM]}"]
                )
            prior = state.get(stream, {}).get("cursors", {})
            cursors = dict(prior) if isinstance(prior, dict) else {}
            yield {
                "protocol": PROTOCOL,
                "type": "SCHEMA",
                "stream": stream,
                "schema": {"fields": list(QUOTE_FIELDS)},
            }
            try:
                for symbol, row in self._fetch(knobs, cursors):
                    mark = row.get("_cursor")
                    if mark is not None:
                        cursors[symbol] = mark
                        continue
                    yield {
                        "protocol": PROTOCOL,
                        "type": "RECORD",
                        "stream": stream,
                        "effective_date": row["ts"],
                        "kind": "observation",
                        "data": row,
                    }
            except AssetError:
                raise
            except Exception as exc:
                raise AssetError([
                    "Alpaca quote request failed; check provider access and network"
                ]) from exc
            new_state.setdefault(stream, {})["cursors"] = cursors
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}


def _flatten(pages):
    """Chain page lists into one quote iterator without joining them."""
    for page in pages:
        yield from page
