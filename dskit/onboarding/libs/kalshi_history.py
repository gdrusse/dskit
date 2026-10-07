"""Kalshi trade-API v2 HISTORY through the onboarding contract (ADR-0236).

The live Kalshi API keeps settled markets only back to a moving archive
cutoff; older markets, trades and candlesticks sit under ``/historical/*``.
The cutoff is served (``GET /historical/cutoff``), so the pack reads it once
per pull and carries no date of its own. It is a STANDALONE sibling of
ADR-0075's ``kalshi`` pack: it subclasses nothing there and imports only the
public (``__all__``) names of ``kalshi``, ``connector`` and ``base``, so the
transport and row rules are restated here and
``tests/onboarding/test_kalshi_history.py`` pins every shared field equal to
``kalshi``'s own output on the same payloads.

Four provider-shaped streams (the venue's field names and units):

- ``markets`` — ``kalshi``'s fourteen fields plus ``expiration_value`` (float,
  or None while unsettled or non-numeric), ``settlement_ts`` (the venue's ISO
  text) and ``volume`` (``volume_fp``). Key ``ticker``; a full re-pull.
- ``candles`` — ``kalshi``'s eleven-field row, key ``(ticker, ts)``. Live
  markets per EVENT (every strike in one call, following ``adjusted_end_ts``)
  or in ticker batches; archived ones one by one from
  ``/historical/markets/{ticker}/candlesticks`` (no ``_dollars`` / ``_fp``).
- ``trades`` — key ``trade_id``, dated ``created_time``: ``yes_price``
  (dollars), ``count``, ``taker_side``. Pulled per market.
- ``orderbooks`` — ``kalshi``'s row plus ``observed_at``, the instant that
  book's own response returned; ``captured_at`` (the pull's minute) is the key.

**The label is known only at settlement.** A ``markets`` row is dated at its
``close_time`` (the capture minute while open), but ``expiration_value`` and
``result`` exist only from ``settlement_ts``, minutes after the close: gate a
join on ``settlement_ts``, never on ``effective_date``. Only ``orderbooks``
carry a per-row read instant; an open market's quotes may be read up to one
pass after its capture minute.

**Routing.** A market belongs to exactly one archive. One that settled BEFORE
the venue's ``market_settled_ts`` is served by ``/historical/markets``; the
live listing may still return it for a while, and that copy is dropped when
the archive listed it too. A live copy the archive did NOT list is kept and
read through the live endpoints, never lost. One settled AT the cutoff or
later is live. Trades split on ``trades_created_ts``: the part of a market's
life before it is asked of ``/historical/trades`` (``max_ts`` = the cutoff),
the rest of ``/markets/trades`` (``min_ts`` = the cutoff, or the cursor if
later), so a market that straddles it is asked of both. The archive leg runs
only when ``settled`` is among the ``statuses``; ``orderbooks`` are live only
and never read the cutoff.

**Cursors, and why a closed market is listed.** ``candles`` and ``trades``
skip a market that closed at or before the cursor, and ``trades`` asks for
``min_ts`` = the cursor. Both are sound only if every market that existed when
the cursor was set was listed then. A market between its close and its
settlement is ``closed`` for the venue, so the default ``statuses`` list
``closed`` too; leave it out and such a market can be skipped for good. A trade
created after the capture instant is not emitted: the next pull asks for it
again. So nothing a pull emits is dated after its capture, and a venue clock
ahead of this host's cannot make acquisition refuse a future-dated row.

**Candle budgets.** The event endpoint cuts a response off at
``adjusted_end_ts``; the pack follows it and refuses a response that does not
advance. The batch and archive endpoints refuse a request whose window is over
a ceiling, counting the window and not the candles that exist: tickers x
periods (``max_candles``) and periods per market (``archive_max_candles``).
The pack packs consecutive live markets into a batch request while the product
fits, slices a window over the budget, and emits a candle shared by two
slices once.

**The capture instant and transport.** As in ``kalshi``: rows the venue does
not date carry the pull's capture instant, the connector clock sampled once
per ``read`` and floored to the minute; a candle that ends after it is still
forming and is dropped. Every request goes through one injectable
``getter(url, params) -> dict``; pacing, retry with backoff on HTTP 429/5xx
and network errors (each wait capped at ``MAX_BACKOFF_S``) and the page walk
sit above it. No credential; stdlib only.
"""

from __future__ import annotations

import abc
import functools
import json
import math
import time
import urllib.error
import urllib.parse
from collections import namedtuple
from datetime import datetime, timedelta, timezone

from ..base import AssetError, MODES, parse_utc
from ..connector import MAX_BACKOFF_S, PROTOCOL, Connector, backoff, retry_after
from .kalshi import (
    CANDLE_FIELDS,
    CANDLE_KEY_FIELDS,
    CANDLE_STREAM,
    DEFAULT_BASE_URL,
    DEFAULT_LIMIT,
    DEFAULT_MAX_PAGES,
    DEFAULT_PACE_S,
    DEFAULT_PERIOD_INTERVAL,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_S,
    MARKET_FIELDS as KALSHI_MARKET_FIELDS,
    MARKET_KEY_FIELDS,
    MARKET_STREAM,
    OPEN_STATUS,
    ORDERBOOK_FIELDS as KALSHI_ORDERBOOK_FIELDS,
    ORDERBOOK_KEY_FIELDS,
    ORDERBOOK_STREAM,
    SETTLED_STATUS,
)

__all__ = [
    "CANDLE_FIELDS",
    "CANDLE_GROUPINGS",
    "CANDLE_KEY_FIELDS",
    "CANDLE_STREAM",
    "CLOSED_STATUS",
    "DEFAULT_ARCHIVE_MAX_CANDLES",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CANDLE_GROUPING",
    "DEFAULT_MAX_CANDLES",
    "DEFAULT_STATUSES",
    "GROUPING_BATCH",
    "GROUPING_EVENT",
    "MARKET_FIELDS",
    "MARKET_KEY_FIELDS",
    "MARKET_STREAM",
    "ORDERBOOK_FIELDS",
    "ORDERBOOK_KEY_FIELDS",
    "ORDERBOOK_STREAM",
    "STREAMS",
    "TRADE_FIELDS",
    "TRADE_KEY_FIELDS",
    "TRADE_STREAM",
    "KalshiHistoryConnector",
]

TRADE_STREAM = "trades"
TRADE_KEY_FIELDS = ("trade_id",)
TRADE_FIELDS = (
    "trade_id", "ticker", "created_time", "yes_price", "count", "taker_side",
)
#: The settlement facts a ``markets`` row adds to ``kalshi``'s fourteen fields.
MARKET_FIELDS = KALSHI_MARKET_FIELDS + ("expiration_value", "settlement_ts", "volume")
#: ``kalshi``'s book row plus the instant that book's response returned.
ORDERBOOK_FIELDS = KALSHI_ORDERBOOK_FIELDS + ("observed_at",)

#: A market after its close and before its settlement. Listed by default so no
#: market is unlisted for a pull (module docs, "Cursors").
CLOSED_STATUS = "closed"
DEFAULT_STATUSES = (SETTLED_STATUS, CLOSED_STATUS, OPEN_STATUS)
GROUPING_EVENT = "event"
GROUPING_BATCH = "batch"
DEFAULT_CANDLE_GROUPING = GROUPING_EVENT
#: Tickers per batch candle request: the venue's own ceiling (it answers
#: HTTP 400 "max markets: 100" above it).
DEFAULT_BATCH_SIZE = 100
#: Candles one batch request may ask for, tickers x periods: the venue's own
#: ceiling (HTTP 400 "max candlesticks: 10000" above it).
DEFAULT_MAX_CANDLES = 10000
#: Candles one archived market's request may ask for: the venue's own ceiling
#: (``/historical/markets/{ticker}/candlesticks`` answers HTTP 400 "max
#: candlesticks: 5000" above it, probed 2026-10-07).
DEFAULT_ARCHIVE_MAX_CANDLES = 5000

_RETRY_STATUSES = (429, 500, 502, 503, 504)
_USER_AGENT = "dskit-onboarding"
#: Candle window when a market's ``open_time`` is missing or unparseable:
#: this much history before its end.
_FALLBACK_WINDOW = timedelta(days=14)

_CUTOFF_PATH = "/historical/cutoff"
#: Which key of the cutoff body bounds which archive.
_CUTOFF_KEYS = {"markets": "market_settled_ts", "trades": "trades_created_ts"}
_MARKETS_PATH = "/markets"
_ARCHIVE_MARKETS_PATH = "/historical/markets"
_TRADES_PATH = "/markets/trades"
_ARCHIVE_TRADES_PATH = "/historical/trades"
_BATCH_CANDLES_PATH = "/markets/candlesticks"


def _now():
    """Return the current instant, aware, UTC — the default clock."""
    return datetime.now(timezone.utc)


def _capture_minute(now):
    """Return the pull's capture instant: ``now`` floored to the minute."""
    return now.replace(second=0, microsecond=0)


def _finite(value):
    """``float(value)`` when it parses to a finite number, else None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _real(value):
    """Report whether ``value`` is a real, finite, non-bool number — a numeric string is not."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _int_at_least(value, floor):
    """Report whether ``value`` is an int >= ``floor`` (a bool is not)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= floor


def _price(value):
    """Return a ``*_dollars`` price as a float in [0, 1]; None when absent or outside it."""
    number = _finite(value)
    return number if number is not None and 0.0 <= number <= 1.0 else None


def _text(value):
    """Return a payload string, or ``""`` when absent or not a string."""
    return value if isinstance(value, str) else ""


def _instant(value):
    """Return an aware UTC datetime for a venue ISO string; None when absent or unparseable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_utc(value)
    except AssetError:
        return None


def _quote(segment):
    """Return a ticker encoded as one URL path segment."""
    return urllib.parse.quote(segment, safe="")


def _epoch(when):
    """Whole epoch seconds of an aware instant (floored)."""
    return int(when.timestamp())


def _from_epoch(ts, where):
    """Return the aware UTC instant of epoch seconds ``ts``; AssetError when it is not one."""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise AssetError(
            [f"{where}: end_period_ts {ts!r} is not a representable instant"]
        ) from exc


def _dict(value):
    """``value`` when it is a dict, else an empty one."""
    return value if isinstance(value, dict) else {}


def _quoted(obj, name):
    """Return a candle's ``name`` price: the live ``<name>_dollars`` as ``kalshi`` reads it, else the archive's bare ``<name>`` as a [0, 1] price."""
    value = obj.get(f"{name}_dollars")
    return _finite(value) if value is not None else _price(obj.get(name))


def _fp(raw, name):
    """Return a candle's ``name`` count: the live ``<name>_fp``, else the archive's bare ``<name>``."""
    value = raw.get(f"{name}_fp")
    return _finite(value if value is not None else raw.get(name))


def _window(start, end, capture):
    """``(start_ts, end_ts)`` epoch seconds of a request window clamped at the capture; None if empty."""
    low, high = _epoch(start), _epoch(min(end, capture))
    return (low, high) if low < high else None


def _market_row(raw, where):
    """Build the 17-field ``markets`` row for one market object; a ticker is required."""
    if not isinstance(raw, dict):
        raise AssetError(
            [f"{where}: market object is not a dict, got {type(raw).__name__}"]
        )
    ticker = raw.get("ticker")
    if not isinstance(ticker, str) or not ticker:
        raise AssetError([f"{where}: market object lacks a ticker"])
    return {
        "ticker": ticker,
        "event_ticker": _text(raw.get("event_ticker")),
        "series_ticker": _text(raw.get("series_ticker")) or ticker.split("-", 1)[0],
        "strike_type": _text(raw.get("strike_type")),
        "floor_strike": _finite(raw.get("floor_strike")),
        "cap_strike": _finite(raw.get("cap_strike")),
        "status": _text(raw.get("status")),
        "result": _text(raw.get("result")),
        "open_time": _text(raw.get("open_time")),
        "close_time": _text(raw.get("close_time")),
        "yes_sub_title": _text(raw.get("yes_sub_title")) or _text(raw.get("subtitle")),
        "yes_bid": _price(raw.get("yes_bid_dollars")),
        "yes_ask": _price(raw.get("yes_ask_dollars")),
        "last_price": _price(raw.get("last_price_dollars")),
        "expiration_value": _finite(raw.get("expiration_value")),
        "settlement_ts": _text(raw.get("settlement_ts")),
        "volume": _finite(raw.get("volume_fp")),
    }


def _candle_row(ticker, raw, where):
    """Build the ``candles`` row for one candlestick object; ``end_period_ts`` is required."""
    if not isinstance(raw, dict):
        raise AssetError([f"{where}: candle is not a dict, got {type(raw).__name__}"])
    ts = _finite(raw.get("end_period_ts"))
    if ts is None:
        raise AssetError([f"{where}: candle lacks a numeric end_period_ts"])
    price = _dict(raw.get("price"))
    return {
        "ticker": ticker,
        "ts": int(ts),
        "open": _quoted(price, "open"),
        "high": _quoted(price, "high"),
        "low": _quoted(price, "low"),
        "close": _quoted(price, "close"),
        "mean": _quoted(price, "mean"),
        "yes_bid_close": _quoted(_dict(raw.get("yes_bid")), "close"),
        "yes_ask_close": _quoted(_dict(raw.get("yes_ask")), "close"),
        "volume": _fp(raw, "volume"),
        "open_interest": _fp(raw, "open_interest"),
    }


def _trade_row(raw, ticker, where):
    """Build the ``trades`` row for one trade object; ``trade_id`` and ``created_time`` are required."""
    if not isinstance(raw, dict):
        raise AssetError([f"{where}: trade is not a dict, got {type(raw).__name__}"])
    trade_id = raw.get("trade_id")
    if not isinstance(trade_id, str) or not trade_id:
        raise AssetError([f"{where}: trade lacks a trade_id"])
    created = _text(raw.get("created_time"))
    if _instant(created) is None:
        raise AssetError(
            [f"{where}: trade {trade_id!r} lacks an ISO created_time, got "
             f"{raw.get('created_time')!r}"]
        )
    return {
        "trade_id": trade_id,
        "ticker": _text(raw.get("ticker")) or ticker,
        "created_time": created,
        "yes_price": _price(raw.get("yes_price_dollars")),
        "count": _finite(raw.get("count_fp")),
        "taker_side": _text(raw.get("taker_side")),
    }


def _levels(raw, cents):
    """Clean ``[price, size]`` levels in dollars, best (highest) first."""
    if not isinstance(raw, list):
        return []
    out = []
    for level in raw:
        if not isinstance(level, (list, tuple)) or len(level) != 2:
            continue
        price, size = _finite(level[0]), _finite(level[1])
        if price is None or size is None:
            continue
        if cents:
            price /= 100.0
        if not 0.0 <= price <= 1.0 or size <= 0.0:
            continue
        out.append([price, size])
    out.sort(key=lambda level: level[0], reverse=True)
    return out


def _book(payload):
    """``(yes_bids, no_bids)`` from an orderbook payload: the dollar book, else cents."""
    fp = payload.get("orderbook_fp")
    if isinstance(fp, dict):
        return _levels(fp.get("yes_dollars"), False), _levels(fp.get("no_dollars"), False)
    legacy = payload.get("orderbook")
    if isinstance(legacy, dict):
        return _levels(legacy.get("yes"), True), _levels(legacy.get("no"), True)
    return [], []


class _Transport:
    """One paced, retried JSON GET under ``base_url``; the pack's own copy of the transport rules."""

    def __init__(self, knobs, getter, sleeper):
        self._knobs = knobs
        self._sleeper = sleeper
        self._getter = getter or functools.partial(
            self._urllib_get, timeout_s=knobs["timeout_s"])
        self._paced = False

    @staticmethod
    def _urllib_get(url, params, timeout_s):
        """Perform one stdlib urllib GET and decode the body as JSON — the default transport."""
        import urllib.request

        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read()
        return json.loads(body.decode("utf-8"))

    def _pace(self):
        """Sleep the pacing gap before every request but the transport's first."""
        if self._paced and self._knobs["pace_s"] > 0:
            self._sleeper(self._knobs["pace_s"])
        self._paced = True

    def get(self, path, params=None):
        """GET ``path`` under ``base_url``, None-valued params dropped; return the JSON object."""
        query = {k: v for k, v in (params or {}).items() if v is not None}
        url = self._knobs["base_url"] + path
        shown = f"{url}?{urllib.parse.urlencode(query)}" if query else url
        self._pace()
        last = delay = None
        for attempt in range(self._knobs["retries"] + 1):
            if attempt:
                self._sleeper(delay)
            try:
                body = self._getter(url, query)
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRY_STATUSES:
                    raise AssetError([f"Kalshi GET {shown}: HTTP {exc.code}"]) from exc
                last = f"HTTP {exc.code}"
                delay = retry_after(exc.headers, backoff(attempt + 1))
            except OSError as exc:
                last = f"network error: {exc}"
                delay = backoff(attempt + 1)
            except ValueError as exc:
                raise AssetError(
                    [f"Kalshi GET {shown}: response is not JSON: {exc}"]
                ) from exc
            else:
                if not isinstance(body, dict):
                    raise AssetError(
                        [f"Kalshi GET {shown}: response is not a JSON object"]
                    )
                return body
        raise AssetError(
            [f"Kalshi GET {shown}: giving up after {self._knobs['retries'] + 1} "
             f"attempt(s); last failure: {last}"]
        )


class _Venue:
    """The endpoints every stream shares: the archive cutoff (read once) and cursor-paged lists."""

    def __init__(self, transport, knobs):
        self._transport = transport
        self._knobs = knobs
        self._cutoffs = None

    def get(self, path, params=None):
        """One GET through the transport."""
        return self._transport.get(path, params)

    def cutoff(self, which):
        """Return the archive boundary ``which`` (``markets`` or ``trades``) as an aware instant."""
        if self._cutoffs is None:
            self._cutoffs = self.get(_CUTOFF_PATH)
        key = _CUTOFF_KEYS[which]
        value = self._cutoffs.get(key)
        when = _instant(value)
        if when is None:
            raise AssetError(
                [f"GET {_CUTOFF_PATH}: {key!r} is missing or not an ISO instant, "
                 f"got {value!r}"]
            )
        return when

    def pages(self, path, params, key, where):
        """Yield ``(label, raw)`` for every item of ``key`` across the cursor pages of ``path``."""
        cursor = None
        for page in range(self._knobs["max_pages"]):
            body = self.get(path, {**params, "limit": self._knobs["limit"], "cursor": cursor})
            label = f"{where} page {page}"
            rows = body.get(key) or []
            if not isinstance(rows, list):
                raise AssetError([f"{label}: {key!r} is not a list"])
            for i, raw in enumerate(rows):
                yield f"{label} item {i}", raw
            following = body.get("cursor") or None
            if not following or not rows:
                return
            if not isinstance(following, str):
                raise AssetError([f"{label}: cursor is not a string, got {following!r}"])
            if following == cursor:
                raise AssetError(
                    [f"{label}: cursor did not advance; refusing an infinite loop"]
                )
            cursor = following
        raise AssetError(
            [f"{where}: still paging after {self._knobs['max_pages']} page(s); "
             "raise max_pages rather than truncate the walk"]
        )


_Listed = namedtuple("_Listed", ("archived", "row"))


class _MarketWalk:
    """Every market of a series, each owned by exactly one archive (module docs, "Routing")."""

    def __init__(self, venue, knobs):
        self._venue = venue
        self._knobs = knobs

    def listed(self, series, statuses=None):
        """Yield ``_Listed(archived, row)``: the archive's markets, then each live status's."""
        statuses = self._knobs["statuses"] if statuses is None else statuses
        archive = SETTLED_STATUS in statuses
        boundary = self._venue.cutoff("markets") if archive else None
        held = set()
        if archive:
            walk = self._venue.pages(
                _ARCHIVE_MARKETS_PATH, {"series_ticker": series}, "markets",
                f"series {series!r} archive")
            for label, raw in walk:
                row = _market_row(raw, label)
                held.add(row["ticker"])
                yield _Listed(True, row)
        for status in statuses:
            walk = self._venue.pages(
                _MARKETS_PATH, {"series_ticker": series, "status": status}, "markets",
                f"series {series!r} status {status!r}")
            for label, raw in walk:
                row = _market_row(raw, label)
                if archive and self._overlap(row, boundary, held):
                    continue  # the archive leg already listed it
                yield _Listed(False, row)

    @staticmethod
    def _overlap(row, boundary, held):
        """Report whether a live row is the copy of a market the archive listed and settled before the boundary.

        A live row the archive did not list is kept even when it settled
        before the boundary: dropping it would lose the market silently.
        """
        settled = _instant(row["settlement_ts"])
        return settled is not None and settled < boundary and row["ticker"] in held


class _Span:
    """One listed market's life: its ticker, event and the window its candles live in."""

    def __init__(self, row, capture):
        self.ticker = row["ticker"]
        self.event = row["event_ticker"]
        self.opened = _instant(row["open_time"])
        self.close = _instant(row["close_time"])
        self.end = self.close if self.close is not None else capture
        self.start = self.opened if self.opened is not None else self.end - _FALLBACK_WINDOW

    def closed_by(self, cursor_dt):
        """Report whether the market closed at or before the cursor, so it cannot gain rows."""
        return (
            cursor_dt is not None and self.close is not None and self.close <= cursor_dt
        )

    def window(self, capture):
        """Return ``(start_ts, end_ts)`` for this market alone; None when empty."""
        return _window(self.start, self.end, capture)


class _CandleSource(abc.ABC):
    """How one series' markets are requested as candles; subclasses supply the endpoint walk."""

    def __init__(self, venue, knobs):
        self._venue = venue
        self._knobs = knobs
        self._step = knobs["period_interval"] * 60

    @abc.abstractmethod
    def candles(self, series, spans, capture):
        """Yield ``(ticker, raw_candle, where)`` for every market in ``spans``."""

    @staticmethod
    def _periods(low, high, step):
        """Periods a window counts for a venue's budget (both ends inclusive)."""
        return (high - low) // step + 1

    def _slices(self, low, high, step, budget):
        """Return the windows covering ``[low, high]``: one, or slices when it exceeds ``budget`` periods."""
        if self._periods(low, high, step) <= budget:
            return [(low, high)]
        longest = (budget - 1) * step
        slices, start = [], low
        while True:
            end = min(start + longest, high)
            slices.append((start, end))
            if end >= high:
                return slices
            start = end

    @staticmethod
    def _fresh(seen, ticker, raw):
        """Report whether this candle is new to ``seen``; a malformed one always passes (it refuses later)."""
        ts = _finite(raw.get("end_period_ts")) if isinstance(raw, dict) else None
        if ts is None:
            return True
        key = (ticker, ts)
        if key in seen:
            return False
        seen.add(key)
        return True

    @staticmethod
    def _candle_list(value, what):
        """Return ``value`` as a list of candles, refusing anything else."""
        candles = value or []
        if not isinstance(candles, list):
            raise AssetError([f"{what} is not a list"])
        return candles


class _ArchiveCandles(_CandleSource):
    """Archived markets, one request each, from ``/historical/markets/{ticker}/candlesticks``."""

    def candles(self, series, spans, capture):
        """Yield the archive's candles for each market in ``spans``, sliced to the venue's ceiling."""
        budget = self._knobs["archive_max_candles"]
        for span in spans:
            window = span.window(capture)
            if window is None:
                continue
            seen = set()
            for start, end in self._slices(window[0], window[1], self._step, budget):
                yield from self._request(span.ticker, start, end, seen)

    def _request(self, ticker, start, end, seen):
        """Make one archive request and yield its candles not already in ``seen``."""
        body = self._venue.get(
            f"{_ARCHIVE_MARKETS_PATH}/{_quote(ticker)}/candlesticks",
            {"start_ts": start, "end_ts": end,
             "period_interval": self._knobs["period_interval"]},
        )
        candles = self._candle_list(
            body.get("candlesticks"), f"market {ticker!r}: 'candlesticks'")
        for i, raw in enumerate(candles):
            if self._fresh(seen, ticker, raw):
                yield ticker, raw, f"market {ticker!r} window {start}-{end} candle {i}"


class _EventCandles(_CandleSource):
    """Live markets per EVENT: every strike in one request, following ``adjusted_end_ts``."""

    def candles(self, series, spans, capture):
        """Yield the candles of every event in ``spans``, one request chain per event."""
        events = {}
        for span in spans:
            if not span.event:
                raise AssetError(
                    [f"market {span.ticker!r} lacks an event_ticker, so candle_grouping "
                     f"'{GROUPING_EVENT}' cannot place it; use '{GROUPING_BATCH}'"]
                )
            events.setdefault(span.event, []).append(span)
        for event, members in events.items():
            yield from self._event(series, event, members, capture)

    def _event(self, series, event, members, capture):
        """Walk one event's window call by call until ``adjusted_end_ts`` reaches its end."""
        known = {member.ticker for member in members}
        window = _window(
            min(member.start for member in members),
            max(member.end for member in members), capture)
        if window is None:
            return
        start, end = window
        path = f"/series/{_quote(series)}/events/{_quote(event)}/candlesticks"
        seen = set()
        while True:
            body = self._venue.get(path, {
                "start_ts": start, "end_ts": end,
                "period_interval": self._knobs["period_interval"]})
            adjusted = self._adjusted(body, event)
            for ticker, candles in self._aligned(body, event):
                if ticker not in known:
                    continue
                for i, raw in enumerate(candles):
                    if self._fresh(seen, ticker, raw):
                        yield ticker, raw, f"event {event!r} market {ticker!r} candle {i}"
            if adjusted >= end:
                return
            if adjusted <= start:
                raise AssetError(
                    [f"event {event!r}: adjusted_end_ts {adjusted} made no progress past "
                     f"start_ts {start}; refusing an infinite loop"]
                )
            start = adjusted

    @staticmethod
    def _adjusted(body, event):
        """Return the response's ``adjusted_end_ts`` as an int, refusing a missing one."""
        adjusted = _finite(body.get("adjusted_end_ts"))
        if adjusted is None:
            raise AssetError(
                [f"event {event!r}: response lacks a numeric adjusted_end_ts, so the "
                 "pull cannot tell whether it was cut off"]
            )
        return int(adjusted)

    def _aligned(self, body, event):
        """Yield ``(ticker, candles)`` pairing ``market_tickers`` with ``market_candlesticks``."""
        tickers = body.get("market_tickers")
        lists = body.get("market_candlesticks")
        if not isinstance(tickers, list) or not isinstance(lists, list):
            raise AssetError(
                [f"event {event!r}: 'market_tickers' and 'market_candlesticks' must "
                 "both be lists"]
            )
        if len(tickers) != len(lists):
            raise AssetError(
                [f"event {event!r}: misaligned response, {len(tickers)} ticker(s) "
                 f"but {len(lists)} candle list(s)"]
            )
        for ticker, candles in zip(tickers, lists):
            if not isinstance(ticker, str):
                raise AssetError([f"event {event!r}: market_tickers holds {ticker!r}, not a ticker"])
            yield ticker, self._candle_list(
                candles, f"event {event!r} candle list for {ticker!r}")


class _BatchCandles(_CandleSource):
    """Live markets in ticker batches from ``GET /markets/candlesticks``, packed to the venue's budget."""

    def candles(self, series, spans, capture):
        """Yield the candles of every planned batch request."""
        for tickers, slices in self._plan(spans, capture):
            seen = set()
            for start, end in slices:
                yield from self._request(tickers, start, end, seen)

    def _plan(self, spans, capture):
        """Yield ``(tickers, slices)``: consecutive markets packed while tickers x periods fit."""
        step = self._step
        budget, size = self._knobs["max_candles"], self._knobs["batch_size"]
        windows = []
        for span in spans:
            window = span.window(capture)
            if window is not None:
                windows.append((span.ticker, window))
        windows.sort(key=lambda pair: pair[1])
        chunk, low, high = [], None, None
        for ticker, (start, end) in windows:
            new_low = start if low is None else min(low, start)
            new_high = end if high is None else max(high, end)
            if chunk and (
                len(chunk) + 1 > size
                or (len(chunk) + 1) * self._periods(new_low, new_high, step) > budget
            ):
                yield chunk, self._slices(low, high, step, budget)
                chunk, new_low, new_high = [], start, end
            chunk.append(ticker)
            low, high = new_low, new_high
        if chunk:
            yield chunk, self._slices(low, high, step, budget)

    def _request(self, tickers, start, end, seen):
        """Make one batch request and yield its candles, keyed by ``market_ticker``."""
        body = self._venue.get(_BATCH_CANDLES_PATH, {
            "market_tickers": ",".join(tickers), "start_ts": start, "end_ts": end,
            "period_interval": self._knobs["period_interval"]})
        listed = body.get("markets")
        if not isinstance(listed, list):
            raise AssetError(["batch candlesticks: 'markets' is not a list"])
        answered = {
            entry["market_ticker"]: entry.get("candlesticks")
            for entry in listed
            if isinstance(entry, dict) and isinstance(entry.get("market_ticker"), str)
        }
        missing = [ticker for ticker in tickers if ticker not in answered]
        if missing:
            raise AssetError(
                [f"batch candlesticks response lacks requested market(s) {missing}"]
            )
        for ticker in tickers:
            candles = self._candle_list(
                answered[ticker], f"batch candlesticks for {ticker!r}")
            for i, raw in enumerate(candles):
                if self._fresh(seen, ticker, raw):
                    yield ticker, raw, f"batch market {ticker!r} candle {i}"


#: ``candle_grouping`` value -> the strategy that requests live candles.
_GROUPINGS = {GROUPING_BATCH: _BatchCandles, GROUPING_EVENT: _EventCandles}
CANDLE_GROUPINGS = tuple(sorted(_GROUPINGS))


class _Pull:
    """What one ``read`` shares across its streams: knobs, the venue, the market walk, the clocks."""

    def __init__(self, knobs, getter, sleeper, clock):
        self.knobs = knobs
        self._clock = clock
        self.capture = _capture_minute(self.now())
        self.venue = _Venue(_Transport(knobs, getter, sleeper), knobs)
        self.walk = _MarketWalk(self.venue, knobs)

    def now(self):
        """Sample the clock: an aware instant, in UTC."""
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise AssetError(
                [f"clock must return an aware datetime, got {value!r}"]
            )
        return value.astimezone(timezone.utc)


class _Stream(abc.ABC):
    """One stream's pull: its schema is class data, its rows come from :meth:`rows`."""

    NAME = ""
    FIELDS = ()
    KEY_FIELDS = ()

    def __init__(self, pull):
        self._pull = pull
        self._venue = pull.venue
        self._walk = pull.walk
        self._knobs = pull.knobs

    @abc.abstractmethod
    def rows(self, cursor_dt):
        """Yield ``(effective_date, row)`` for the pull; ``cursor_dt`` is the prior checkpoint or None."""


class _MarketsStream(_Stream):
    """Every market of every series; a full re-pull, never cursor-filtered."""

    NAME, FIELDS, KEY_FIELDS = MARKET_STREAM, MARKET_FIELDS, MARKET_KEY_FIELDS

    def rows(self, cursor_dt):
        """Yield each market dated at its close, or at the capture while it is still open."""
        capture = self._pull.capture
        stamp = capture.isoformat()
        for series in self._knobs["series"]:
            for listed in self._walk.listed(series):
                row = listed.row
                close = _instant(row["close_time"])
                effective = row["close_time"] if close is not None and close <= capture else stamp
                yield effective, row


class _CandlesStream(_Stream):
    """Candlesticks of every market that can still gain one."""

    NAME, FIELDS, KEY_FIELDS = CANDLE_STREAM, CANDLE_FIELDS, CANDLE_KEY_FIELDS

    def rows(self, cursor_dt):
        """Yield each complete candle; archived markets one by one, live ones by the chosen grouping."""
        capture = self._pull.capture
        archive = _ArchiveCandles(self._venue, self._knobs)
        live = _GROUPINGS[self._knobs["candle_grouping"]](self._venue, self._knobs)
        for series in self._knobs["series"]:
            waiting = []
            for listed in self._walk.listed(series):
                span = _Span(listed.row, capture)
                if span.closed_by(cursor_dt):
                    continue
                if listed.archived:
                    yield from self._dated(archive.candles(series, [span], capture), capture)
                else:
                    waiting.append(span)
            yield from self._dated(live.candles(series, waiting, capture), capture)

    @staticmethod
    def _dated(found, capture):
        """Turn raw candles into dated rows, dropping one that ends after the capture (still forming)."""
        for ticker, raw, where in found:
            row = _candle_row(ticker, raw, where)
            when = _from_epoch(row["ts"], where)
            if when > capture:
                continue
            yield when.isoformat(), row


class _TradesStream(_Stream):
    """Trades of every market, each period asked of the archive that owns it."""

    NAME, FIELDS, KEY_FIELDS = TRADE_STREAM, TRADE_FIELDS, TRADE_KEY_FIELDS

    def rows(self, cursor_dt):
        """Yield each trade dated at its ``created_time``."""
        capture = self._pull.capture
        boundary = self._venue.cutoff("trades")
        for series in self._knobs["series"]:
            for listed in self._walk.listed(series):
                span = _Span(listed.row, capture)
                if span.closed_by(cursor_dt):
                    continue
                seen = set()
                for path, extra in self._legs(span, boundary, cursor_dt):
                    yield from self._trades(span.ticker, path, extra, seen)

    @staticmethod
    def _legs(span, boundary, cursor_dt):
        """Return the ``(path, params)`` requests a market needs: archive before the boundary, live from it."""
        boundary_ts = _epoch(boundary)
        since = None if cursor_dt is None else _epoch(cursor_dt)
        legs = []
        if (span.opened is None or span.opened < boundary) and (
            cursor_dt is None or cursor_dt < boundary
        ):
            legs.append((_ARCHIVE_TRADES_PATH, {"max_ts": boundary_ts, "min_ts": since}))
        if span.close is None or span.close >= boundary:
            legs.append((_TRADES_PATH, {"min_ts": max(boundary_ts, since or boundary_ts)}))
        return legs

    def _trades(self, ticker, path, extra, seen):
        """Yield ``(created_time, row)`` for each trade of one market on one endpoint, not after the capture and not yet in ``seen``."""
        capture = self._pull.capture
        walk = self._venue.pages(
            path, {"ticker": ticker, **extra}, "trades", f"trades of {ticker!r} via {path}")
        for label, raw in walk:
            row = _trade_row(raw, ticker, label)
            if _instant(row["created_time"]) > capture:
                continue  # the next pull asks for it again (module docs, "Cursors")
            if row["trade_id"] in seen:
                continue  # the boundary second came back from both archives
            seen.add(row["trade_id"])
            yield row["created_time"], row


class _OrderbooksStream(_Stream):
    """The resting book of every open market, each stamped with when its response returned."""

    NAME, FIELDS, KEY_FIELDS = ORDERBOOK_STREAM, ORDERBOOK_FIELDS, ORDERBOOK_KEY_FIELDS

    def rows(self, cursor_dt):
        """Yield each book dated at the capture minute, with its own ``observed_at``."""
        captured_at = self._pull.capture.isoformat()
        for series in self._knobs["series"]:
            for listed in self._walk.listed(series, (OPEN_STATUS,)):
                market = listed.row
                body = self._venue.get(f"/markets/{_quote(market['ticker'])}/orderbook")
                observed_at = self._pull.now().isoformat()
                yes_bids, no_bids = _book(body)
                yield captured_at, {
                    "ticker": market["ticker"],
                    "event_ticker": market["event_ticker"],
                    "series_ticker": market["series_ticker"],
                    "captured_at": captured_at,
                    "yes_bids": yes_bids,
                    "no_bids": no_bids,
                    "strike_type": market["strike_type"],
                    "floor_strike": market["floor_strike"],
                    "cap_strike": market["cap_strike"],
                    "close_time": market["close_time"],
                    "observed_at": observed_at,
                }


_STREAM_CLASSES = {
    cls.NAME: cls
    for cls in (_CandlesStream, _MarketsStream, _OrderbooksStream, _TradesStream)
}
STREAMS = tuple(sorted(_STREAM_CLASSES))


class KalshiHistoryConnector(Connector):
    """Kalshi trade-API v2 history: markets, candles, trades and books, archive-aware.

    Parameters
    ----------
    getter : callable or None
        ``getter(url, params) -> dict`` — ONE HTTP GET attempt: return the
        decoded JSON object on success, raise ``urllib.error.HTTPError`` on
        any other status and ``urllib.error.URLError`` (any ``OSError``) on a
        transport failure. ``params`` never carries a None value. ``None``
        means stdlib urllib under the ``timeout_s`` knob. Pacing, retry and
        pagination sit above the getter.
    sleeper : callable or None
        ``sleeper(seconds)`` for pacing and backoff; ``None`` means
        ``time.sleep``.
    clock : callable or None
        ``clock() -> datetime`` (aware) — sampled once per ``read`` and
        floored to the minute as the capture instant, and once more per
        orderbook for its ``observed_at``; ``None`` means the current time.

    Examples
    --------
    Read the realised settlement value of one archived market through a
    scripted transport::

        market = {"ticker": "KXBTCD-26AUG0617-T73749.99",
                  "event_ticker": "KXBTCD-26AUG0617",
                  "close_time": "2026-08-06T21:00:00Z",
                  "expiration_value": "64396.95"}
        bodies = {
            "/historical/cutoff": {"market_settled_ts": "2026-08-07T00:00:00Z",
                                   "trades_created_ts": "2026-08-07T00:00:00Z"},
            "/historical/markets": {"markets": [market], "cursor": ""},
            "/markets": {"markets": [], "cursor": ""},
        }
        connector = KalshiHistoryConnector(
            getter=lambda url, params: bodies[url.split("/trade-api/v2")[1]],
            sleeper=lambda seconds: None,
        )
        messages = list(connector.read(
            {"series": ["KXBTCD"], "statuses": ["settled"]}, ["markets"], {}, "live"))
        messages[1]["data"]["expiration_value"]  # 64396.95
    """

    def __init__(self, getter=None, sleeper=None, clock=None):
        problems = [
            f"{name} must be callable, got {type(value).__name__}"
            for name, value in (("getter", getter), ("sleeper", sleeper), ("clock", clock))
            if value is not None and not callable(value)
        ]
        if problems:
            raise AssetError(problems)
        self._getter = getter
        self._sleeper = time.sleep if sleeper is None else sleeper
        self._clock = _now if clock is None else clock

    def spec(self):
        """Declare the default-deny Kalshi-history configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {"params": {
            "series": {
                "required": True,
                "notes": "Non-empty list of series tickers (e.g. KXBTCD) — the "
                         "universe every stream walks, in this order.",
            },
            "statuses": {
                "notes": "Market statuses the streams list live, one server-side "
                         f"query each; default {list(DEFAULT_STATUSES)}. "
                         f"'{SETTLED_STATUS}' also switches on the archive leg "
                         "(GET /historical/markets, trades before the cutoff). "
                         f"'{CLOSED_STATUS}' lists a market between its close and "
                         "its settlement: leave it out and the candle and trade "
                         "cursors can skip such a market for good.",
            },
            "limit": {
                "notes": f"Items requested per page; default {DEFAULT_LIMIT}.",
            },
            "max_pages": {
                "notes": "Page cap per walk — the pull refuses rather than "
                         f"truncates when it is reached; default {DEFAULT_MAX_PAGES}.",
            },
            "period_interval": {
                "notes": "Candle width in minutes (Kalshi serves 1, 60 and 1440); "
                         f"default {DEFAULT_PERIOD_INTERVAL}.",
            },
            "candle_grouping": {
                "notes": "How LIVE markets' candles are requested: "
                         f"'{GROUPING_EVENT}' (every strike of an event per request, "
                         "following adjusted_end_ts; needs event_ticker) or "
                         f"'{GROUPING_BATCH}' (consecutive markets per request, "
                         "capped by batch_size and max_candles). Archived markets "
                         f"are always requested one by one; default {DEFAULT_CANDLE_GROUPING}.",
            },
            "batch_size": {
                "notes": "Tickers per batch candle request; default "
                         f"{DEFAULT_BATCH_SIZE}, the venue's own ceiling.",
            },
            "max_candles": {
                "notes": "Candle budget of one batch request: tickers x periods of "
                         "its window may not exceed it (the venue counts the window, "
                         "not the candles that exist, and refuses a request over it); "
                         f"default {DEFAULT_MAX_CANDLES}. At least 2.",
            },
            "archive_max_candles": {
                "notes": "Candle budget of one ARCHIVED market's request: the "
                         "periods of its window (both ends counted) may not exceed "
                         "it, and a longer life is requested in slices (the venue "
                         "answers HTTP 400 above its own ceiling); "
                         f"default {DEFAULT_ARCHIVE_MAX_CANDLES}. At least 2.",
            },
            "pace_s": {
                "notes": "Seconds slept between requests; default "
                         f"{DEFAULT_PACE_S}. The public API is throttled per IP "
                         "to roughly serial throughput, so pacing beats fanning "
                         "out and 429 retries.",
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
            "base_url": {
                "notes": f"REST root; default {DEFAULT_BASE_URL}. Kalshi serves "
                         "the same API from its api.elections.kalshi.com host.",
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
            Fully resolved request knobs; ``base_url`` without a trailing slash.

        Raises
        ------
        AssetError
            Listing all malformed values.
        """
        if not isinstance(config, dict):
            raise AssetError([f"config must be a dict, got {type(config).__name__}"])
        problems = []
        series = config.get("series")
        if (
            not isinstance(series, list)
            or not series
            or not all(isinstance(s, str) and s for s in series)
        ):
            problems.append(
                f"config.series must be a non-empty list of series tickers, "
                f"got {series!r}"
            )
        elif len(set(series)) != len(series):
            problems.append(f"config.series must not repeat, got {series!r}")
        statuses = config.get("statuses", list(DEFAULT_STATUSES))
        if (
            not isinstance(statuses, (list, tuple))
            or not statuses
            or not all(isinstance(s, str) and s for s in statuses)
        ):
            problems.append(
                f"config.statuses must be a non-empty list of market statuses, "
                f"got {statuses!r}"
            )
        elif len(set(statuses)) != len(statuses):
            problems.append(f"config.statuses must not repeat, got {statuses!r}")
        counts = {
            "limit": (config.get("limit", DEFAULT_LIMIT), 1),
            "max_pages": (config.get("max_pages", DEFAULT_MAX_PAGES), 1),
            "period_interval": (config.get("period_interval", DEFAULT_PERIOD_INTERVAL), 1),
            "batch_size": (config.get("batch_size", DEFAULT_BATCH_SIZE), 1),
            "max_candles": (config.get("max_candles", DEFAULT_MAX_CANDLES), 2),
            "archive_max_candles": (
                config.get("archive_max_candles", DEFAULT_ARCHIVE_MAX_CANDLES), 2),
        }
        for name, (value, floor) in counts.items():
            if not _int_at_least(value, floor):
                problems.append(f"config.{name} must be an int >= {floor}, got {value!r}")
        grouping = config.get("candle_grouping", DEFAULT_CANDLE_GROUPING)
        if not isinstance(grouping, str) or grouping not in _GROUPINGS:
            problems.append(
                f"config.candle_grouping must be one of {list(CANDLE_GROUPINGS)}, "
                f"got {grouping!r}"
            )
        retries = config.get("retries", DEFAULT_RETRIES)
        if not _int_at_least(retries, 0):
            problems.append(f"config.retries must be an int >= 0, got {retries!r}")
        pace_s = config.get("pace_s", DEFAULT_PACE_S)
        if not _real(pace_s) or pace_s < 0:
            problems.append(f"config.pace_s must be a number >= 0, got {pace_s!r}")
        timeout_s = config.get("timeout_s", DEFAULT_TIMEOUT_S)
        if not _real(timeout_s) or timeout_s <= 0:
            problems.append(
                f"config.timeout_s must be a positive number, got {timeout_s!r}"
            )
        base_url = config.get("base_url", DEFAULT_BASE_URL)
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            problems.append(f"config.base_url must be an http(s) URL, got {base_url!r}")
        if problems:
            raise AssetError(problems)
        return {
            "series": list(series),
            "statuses": list(statuses),
            **{name: value for name, (value, _floor) in counts.items()},
            "candle_grouping": grouping,
            "retries": retries,
            "pace_s": pace_s,
            "timeout_s": timeout_s,
            "base_url": base_url.rstrip("/"),
        }

    def check(self, config):
        """Validate config and read the archive cutoff once.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        None
            Silence means the venue answered ``GET /historical/cutoff`` with
            both boundaries the pack routes by.

        Raises
        ------
        AssetError
            If config is invalid, the ping fails after its retries, or the
            cutoff body lacks a boundary.
        """
        knobs = self.resolve_knobs(config)
        venue = _Venue(_Transport(knobs, self._getter, self._sleeper), knobs)
        for which in _CUTOFF_KEYS:
            venue.cutoff(which)

    def discover(self, config):
        """Describe the four provider-shaped streams without touching the venue.

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
            {"stream": name, "schema": {"fields": list(cls.FIELDS)},
             "primary_key": list(cls.KEY_FIELDS)}
            for name, cls in sorted(_STREAM_CLASSES.items())
        ]

    def read(self, config, streams, state, mode):
        """Emit schema, records and one checkpoint per requested stream.

        Parameters
        ----------
        config : dict
            Connector configuration after platform-reserved keys are removed.
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
            If arguments, config, the venue's responses, or the walk are
            invalid.
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
        unknown = [s for s in streams if s not in _STREAM_CLASSES]
        if unknown:
            raise AssetError(
                [f"unknown stream(s) {unknown}; discovered: {list(STREAMS)}"]
            )
        pull = _Pull(knobs, self._getter, self._sleeper, self._clock)
        new_state = {key: dict(value) for key, value in state.items()}
        for name in streams:
            stream = _STREAM_CLASSES[name](pull)
            cursor = state.get(name, {}).get("cursor", "")
            cursor_dt = parse_utc(cursor) if cursor else None
            yield {
                "protocol": PROTOCOL,
                "type": "SCHEMA",
                "stream": name,
                "schema": {"fields": list(stream.FIELDS)},
            }
            emitted, emitted_dt = cursor, cursor_dt
            for effective, data in stream.rows(cursor_dt):
                effective_dt = parse_utc(effective)
                yield {
                    "protocol": PROTOCOL,
                    "type": "RECORD",
                    "stream": name,
                    "effective_date": effective,
                    "kind": "observation",
                    "data": data,
                }
                if emitted_dt is None or effective_dt > emitted_dt:
                    emitted, emitted_dt = effective, effective_dt
            new_state.setdefault(name, {})["cursor"] = emitted
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}
