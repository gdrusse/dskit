"""Kalshi public trade-API v2 market data through the onboarding contract (ADR-0075).

Kalshi serves market listings, candlesticks, per-series fee schedules and
resting order books over unauthenticated REST. A project used to carry that
plumbing — transport, pacing, retry, cursor pagination and the row shapes —
inside its own scripts; this pack owns it once, and a project declares only
its universe (``series``) and its pacing policy in config.

Four streams, provider-shaped (the venue's own field names and units; a
child normalizes into its own vocabulary):

- ``markets`` — one row per market, key ``ticker``: strikes, lifecycle
  status, settlement ``result``, open/close instants and the best YES
  quotes. **A full re-pull by design.** Settlement lands AFTER close, so a
  market first listed open is re-emitted on every pull until its ``result``
  is filled; the stream never filters by cursor, and bitemporal dedup keeps
  the latest evidence per ``ticker``. The checkpoint cursor is recorded
  (the max ``effective_date`` emitted) but never consulted.
- ``candles`` — one row per candlestick, key ``(ticker, ts)``, requested
  per market over its open-to-close life at ``period_interval`` minutes.
  The cursor is the max candle instant emitted; a market whose
  ``close_time`` is at or before it cannot gain candles and is skipped,
  every other market is re-pulled whole (dedup, again). A candle whose
  period ends after the capture instant is still forming and is dropped —
  it arrives complete on the next pull.
- ``fee_schedules`` — one row per series, key ``(series_ticker,
  retrieved)``: ``fee_type`` / ``fee_multiplier`` as ``GET /series``
  reports them. The venue serves current state only, so every pull records
  a fresh row and no cursor filter applies.
- ``orderbooks`` — one row per OPEN market, key ``(ticker, captured_at)``:
  resting YES and NO bids as ``[price_dollars, size]`` levels, best first.
  Kalshi's book is one-sided per outcome — a YES ask is the mirror of a NO
  bid at ``1 - price``. The pack does NOT mirror: it stores the bids the
  venue serves and leaves the convention to the reader.

**The capture instant.** Rows the venue does not date (``retrieved``,
``captured_at``, an open market's listing) are dated at the pull's capture
instant: the connector clock sampled once per ``read`` and FLOORED TO THE
MINUTE. The platform stamps ``acquired_at`` at COMMIT, after ``read`` has
finished (ADR-0079), so a capture instant inside the pull can never
post-date it; the minute floor is what makes a pull ONE capture: two
pulls inside a minute collide on their key and the later acquisition
wins — the designed re-pull behaviour.

**Transport.** Every request goes through one injectable
``getter(url, params) -> dict``; pacing (``pace_s`` between requests), retry
with exponential backoff on HTTP 429/5xx and network errors (``retries``,
honoring a numeric ``Retry-After``; every single wait is capped at
``MAX_BACKOFF_S`` seconds, so no server can park a pull for hours) and the
page walk sit above it, so a scripted getter exercises all of them. The
default getter is stdlib urllib
under the ``timeout_s`` knob. No credential: the endpoints are public.
``base_url`` moves the host; Kalshi serves the same API from its
``external-api`` and ``api.elections`` hosts.

Import cost: stdlib only.
"""

from __future__ import annotations

import functools
import json
import math
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

from ..base import AssetError, MODES, parse_utc
from ..connector import MAX_BACKOFF_S, PROTOCOL, Connector

__all__ = [
    "CANDLE_FIELDS",
    "CANDLE_KEY_FIELDS",
    "CANDLE_STREAM",
    "DEFAULT_BASE_URL",
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_PAGES",
    "DEFAULT_PACE_S",
    "DEFAULT_PERIOD_INTERVAL",
    "DEFAULT_RETRIES",
    "DEFAULT_STATUSES",
    "DEFAULT_TIMEOUT_S",
    "FEE_FIELDS",
    "FEE_KEY_FIELDS",
    "FEE_STREAM",
    "MARKET_FIELDS",
    "MARKET_KEY_FIELDS",
    "MARKET_STREAM",
    "MAX_BACKOFF_S",
    "OPEN_STATUS",
    "ORDERBOOK_FIELDS",
    "ORDERBOOK_KEY_FIELDS",
    "ORDERBOOK_STREAM",
    "SETTLED_STATUS",
    "STREAMS",
    "KalshiConnector",
]

MARKET_STREAM = "markets"
CANDLE_STREAM = "candles"
FEE_STREAM = "fee_schedules"
ORDERBOOK_STREAM = "orderbooks"
MARKET_KEY_FIELDS = ("ticker",)
MARKET_FIELDS = (
    "ticker", "event_ticker", "series_ticker", "strike_type", "floor_strike",
    "cap_strike", "status", "result", "open_time", "close_time",
    "yes_sub_title", "yes_bid", "yes_ask", "last_price",
)
CANDLE_KEY_FIELDS = ("ticker", "ts")
CANDLE_FIELDS = (
    "ticker", "ts", "open", "high", "low", "close", "mean", "yes_bid_close",
    "yes_ask_close", "volume", "open_interest",
)
FEE_KEY_FIELDS = ("series_ticker", "retrieved")
FEE_FIELDS = (
    "series_ticker", "fee_type", "fee_multiplier", "title", "category",
    "frequency", "retrieved",
)
ORDERBOOK_KEY_FIELDS = ("ticker", "captured_at")
ORDERBOOK_FIELDS = (
    "ticker", "event_ticker", "series_ticker", "captured_at", "yes_bids",
    "no_bids", "strike_type", "floor_strike", "cap_strike", "close_time",
)
#: The venue's status vocabulary the pack itself relies on: ``open`` is
#: what the ``orderbooks`` stream lists (a closed market has no book).
SETTLED_STATUS = "settled"
OPEN_STATUS = "open"
DEFAULT_STATUSES = (SETTLED_STATUS, OPEN_STATUS)
DEFAULT_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_LIMIT = 1000
DEFAULT_MAX_PAGES = 10000
DEFAULT_PERIOD_INTERVAL = 60
DEFAULT_PACE_S = 0.2
DEFAULT_RETRIES = 4
DEFAULT_TIMEOUT_S = 30

_STREAMS = {
    CANDLE_STREAM: (CANDLE_FIELDS, CANDLE_KEY_FIELDS),
    FEE_STREAM: (FEE_FIELDS, FEE_KEY_FIELDS),
    MARKET_STREAM: (MARKET_FIELDS, MARKET_KEY_FIELDS),
    ORDERBOOK_STREAM: (ORDERBOOK_FIELDS, ORDERBOOK_KEY_FIELDS),
}
STREAMS = tuple(sorted(_STREAMS))

_RETRY_STATUSES = (429, 500, 502, 503, 504)
#: Backoff base in seconds, doubled per failed attempt up to ``MAX_BACKOFF_S``.
_BACKOFF_S = 0.5
_USER_AGENT = "dskit-onboarding"
#: Candle window when a market's ``open_time`` is missing or unparseable:
#: this much history before its end.
_FALLBACK_WINDOW = timedelta(days=14)
_MARKETS_PATH = "/markets"


def _now():
    """Return the current instant, aware, UTC — the default clock."""
    return datetime.now(timezone.utc)


def _capture_minute(now):
    """Return the pull's capture instant: ``now`` floored to the minute (module docs)."""
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


def _backoff(attempt):
    """Exponential backoff for the ``attempt``-th failure, capped at ``MAX_BACKOFF_S``."""
    return min(_BACKOFF_S * 2 ** attempt, MAX_BACKOFF_S)


def _retry_after(headers, fallback):
    """Seconds to wait: a numeric ``Retry-After`` capped at ``MAX_BACKOFF_S``, else ``fallback``."""
    try:
        value = float(headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        return fallback
    return min(max(0.0, value), MAX_BACKOFF_S)


def _market_row(raw, where):
    """Build the 14-field ``markets`` row for one market object; a ticker is required."""
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
    }


def _candle_row(ticker, raw, where):
    """Build the ``candles`` row for one candlestick object; ``end_period_ts`` is required."""
    if not isinstance(raw, dict):
        raise AssetError([f"{where}: candle is not a dict, got {type(raw).__name__}"])
    ts = _finite(raw.get("end_period_ts"))
    if ts is None:
        raise AssetError([f"{where}: candle lacks a numeric end_period_ts"])
    price = raw.get("price") if isinstance(raw.get("price"), dict) else {}
    yes_bid = raw.get("yes_bid") if isinstance(raw.get("yes_bid"), dict) else {}
    yes_ask = raw.get("yes_ask") if isinstance(raw.get("yes_ask"), dict) else {}
    return {
        "ticker": ticker,
        "ts": int(ts),
        "open": _finite(price.get("open_dollars")),
        "high": _finite(price.get("high_dollars")),
        "low": _finite(price.get("low_dollars")),
        "close": _finite(price.get("close_dollars")),
        "mean": _finite(price.get("mean_dollars")),
        "yes_bid_close": _finite(yes_bid.get("close_dollars")),
        "yes_ask_close": _finite(yes_ask.get("close_dollars")),
        "volume": _finite(raw.get("volume_fp")),
        "open_interest": _finite(raw.get("open_interest_fp")),
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


class KalshiConnector(Connector):
    """Kalshi trade-API v2 public market data: markets, candles, fees, books.

    Parameters
    ----------
    getter : callable or None
        ``getter(url, params) -> dict`` — ONE HTTP GET attempt: return the
        decoded JSON object on success, raise ``urllib.error.HTTPError``
        on any other status and ``urllib.error.URLError`` (any
        ``OSError``) on a transport failure. ``params`` never carries a
        None value. ``None`` means stdlib urllib under the ``timeout_s``
        knob. Pacing, retry and pagination sit above the getter.
    sleeper : callable or None
        ``sleeper(seconds)`` for pacing and backoff; ``None`` means
        ``time.sleep``.
    clock : callable or None
        ``clock() -> datetime`` (aware, UTC) — sampled once per ``read``
        and floored to the minute as the capture instant; ``None`` means
        the current time.

    Examples
    --------
    Pull one series' fee schedule through a scripted transport::

        connector = KalshiConnector(
            getter=lambda url, params: {"series": {"fee_type": "quadratic",
                                                   "fee_multiplier": 1}},
            sleeper=lambda seconds: None,
        )
        messages = list(connector.read(
            {"series": ["KXHIGHNY"]}, ["fee_schedules"], {}, "live"))
        messages[1]["data"]["fee_type"]  # 'quadratic'
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
        self._paced = False

    def spec(self):
        """Declare the default-deny Kalshi configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {"params": {
            "series": {
                "required": True,
                "notes": "Non-empty list of series tickers (e.g. KXHIGHNY) — "
                         "the universe every stream walks, in this order.",
            },
            "statuses": {
                "notes": "Market statuses the `markets` and `candles` streams "
                         "list, one server-side query each; default "
                         f"{list(DEFAULT_STATUSES)}. Kalshi spells a settled "
                         "market's payload status 'finalized'; rows keep "
                         "whatever the payload says.",
            },
            "limit": {
                "notes": f"Markets requested per page; default {DEFAULT_LIMIT}.",
            },
            "max_pages": {
                "notes": "Page cap per (series, status) walk — the pull refuses "
                         "rather than truncates when it is reached; default "
                         f"{DEFAULT_MAX_PAGES}.",
            },
            "period_interval": {
                "notes": "Candle width in minutes (Kalshi serves 1, 60 and 1440); "
                         f"default {DEFAULT_PERIOD_INTERVAL}.",
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
        counts = {
            "limit": config.get("limit", DEFAULT_LIMIT),
            "max_pages": config.get("max_pages", DEFAULT_MAX_PAGES),
            "period_interval": config.get("period_interval", DEFAULT_PERIOD_INTERVAL),
        }
        for name, value in counts.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                problems.append(f"config.{name} must be an int >= 1, got {value!r}")
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
        base_url = config.get("base_url", DEFAULT_BASE_URL)
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            problems.append(f"config.base_url must be an http(s) URL, got {base_url!r}")
        if problems:
            raise AssetError(problems)
        return {
            "series": list(series),
            "statuses": list(statuses),
            **counts,
            "retries": retries,
            "pace_s": pace_s,
            "timeout_s": timeout_s,
            "base_url": base_url.rstrip("/"),
        }

    # -- transport ---------------------------------------------------------

    def _http_get(self, url, params, timeout_s):
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

    def _transport(self, knobs):
        """Return the ``(url, params) -> dict`` callable for this pull."""
        if self._getter is not None:
            return self._getter
        return functools.partial(self._http_get, timeout_s=knobs["timeout_s"])

    def _pace(self, pace_s):
        """Sleep the pacing gap before every request but the connector's first."""
        if self._paced and pace_s > 0:
            self._sleeper(pace_s)
        self._paced = True

    def _get(self, knobs, path, params=None):
        """One paced, retried GET under ``base_url``; None-valued params are dropped."""
        query = {k: v for k, v in (params or {}).items() if v is not None}
        url = knobs["base_url"] + path
        shown = f"{url}?{urllib.parse.urlencode(query)}" if query else url
        transport = self._transport(knobs)
        self._pace(knobs["pace_s"])
        last = delay = None
        for attempt in range(knobs["retries"] + 1):
            if attempt:
                self._sleeper(delay)
            try:
                body = transport(url, query)
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRY_STATUSES:
                    raise AssetError([f"Kalshi GET {shown}: HTTP {exc.code}"]) from exc
                last = f"HTTP {exc.code}"
                delay = _retry_after(exc.headers, _backoff(attempt))
            except OSError as exc:
                last = f"network error: {exc}"
                delay = _backoff(attempt)
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
            [f"Kalshi GET {shown}: giving up after {knobs['retries'] + 1} "
             f"attempt(s); last failure: {last}"]
        )

    # -- the venue's endpoints -----------------------------------------------

    def _markets(self, knobs, series, statuses):
        """Yield ``markets`` rows for one series across statuses and cursor pages."""
        for status in statuses:
            cursor = None
            for page in range(knobs["max_pages"]):
                body = self._get(knobs, _MARKETS_PATH, {
                    "series_ticker": series, "status": status,
                    "limit": knobs["limit"], "cursor": cursor,
                })
                where = f"series {series!r} status {status!r} page {page}"
                rows = body.get("markets") or []
                if not isinstance(rows, list):
                    raise AssetError([f"{where}: 'markets' is not a list"])
                for i, raw in enumerate(rows):
                    yield _market_row(raw, f"{where} market {i}")
                following = body.get("cursor") or None
                if not following or not rows:
                    break
                if following == cursor:
                    raise AssetError(
                        [f"{where}: cursor did not advance; refusing an infinite loop"]
                    )
                cursor = following
            else:
                raise AssetError(
                    [f"series {series!r} status {status!r}: still paging after "
                     f"{knobs['max_pages']} page(s); raise max_pages rather than "
                     "truncate the walk"]
                )

    def _pull_markets(self, knobs, capture, cursor_dt):
        """Yield ``(effective_date, row)`` for every market — never cursor-filtered."""
        stamp = capture.isoformat()
        for series in knobs["series"]:
            for row in self._markets(knobs, series, knobs["statuses"]):
                close = _instant(row["close_time"])
                effective = row["close_time"] if close is not None and close <= capture else stamp
                yield effective, row

    def _pull_candles(self, knobs, capture, cursor_dt):
        """Yield ``(effective_date, row)`` per candle of every market still able to gain one."""
        for series in knobs["series"]:
            for market in self._markets(knobs, series, knobs["statuses"]):
                close = _instant(market["close_time"])
                if cursor_dt is not None and close is not None and close <= cursor_dt:
                    continue
                end = close if close is not None else capture
                opened = _instant(market["open_time"])
                start = opened if opened is not None else end - _FALLBACK_WINDOW
                ticker = market["ticker"]
                body = self._get(
                    knobs,
                    f"/series/{_quote(series)}/markets/{_quote(ticker)}/candlesticks",
                    {"start_ts": int(start.timestamp()), "end_ts": int(end.timestamp()),
                     "period_interval": knobs["period_interval"]},
                )
                candles = body.get("candlesticks") or []
                if not isinstance(candles, list):
                    raise AssetError([f"market {ticker!r}: 'candlesticks' is not a list"])
                for i, raw in enumerate(candles):
                    row = _candle_row(ticker, raw, f"market {ticker!r} candle {i}")
                    when = datetime.fromtimestamp(row["ts"], tz=timezone.utc)
                    if when > capture:
                        continue  # still forming; complete on the next pull
                    yield when.isoformat(), row

    def _pull_fees(self, knobs, capture, cursor_dt):
        """Yield ``(effective_date, row)`` per series from ``GET /series``."""
        retrieved = capture.isoformat()
        for series in knobs["series"]:
            body = self._get(knobs, f"/series/{_quote(series)}")
            obj = body.get("series")
            if not isinstance(obj, dict):
                raise AssetError(
                    [f"series {series!r}: payload lacks a 'series' object"]
                )
            yield retrieved, {
                "series_ticker": series,
                "fee_type": obj.get("fee_type"),
                "fee_multiplier": _finite(obj.get("fee_multiplier")),
                "title": obj.get("title"),
                "category": obj.get("category"),
                "frequency": obj.get("frequency"),
                "retrieved": retrieved,
            }

    def _pull_orderbooks(self, knobs, capture, cursor_dt):
        """Yield ``(effective_date, row)`` per open market's resting book."""
        captured_at = capture.isoformat()
        for series in knobs["series"]:
            for market in self._markets(knobs, series, (OPEN_STATUS,)):
                body = self._get(knobs, f"/markets/{_quote(market['ticker'])}/orderbook")
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
                }

    def _pullers(self):
        """Stream name -> the generator that pulls it."""
        return {
            CANDLE_STREAM: self._pull_candles,
            FEE_STREAM: self._pull_fees,
            MARKET_STREAM: self._pull_markets,
            ORDERBOOK_STREAM: self._pull_orderbooks,
        }

    # -- the four verbs ------------------------------------------------------

    def check(self, config):
        """Validate config and ping the first series once.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        None
            Silence means the venue answered ``GET /series/<first series>``.

        Raises
        ------
        AssetError
            If config is invalid or the ping fails after its retries.
        """
        knobs = self.resolve_knobs(config)
        self._get(knobs, f"/series/{_quote(knobs['series'][0])}")

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
        pullers = self._pullers()
        unknown = [s for s in streams if s not in pullers]
        if unknown:
            raise AssetError(
                [f"unknown stream(s) {unknown}; discovered: {list(STREAMS)}"]
            )
        capture = _capture_minute(self._clock())
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
            for effective, data in pullers[stream](knobs, capture, cursor_dt):
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
