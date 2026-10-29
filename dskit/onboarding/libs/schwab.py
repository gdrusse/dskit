"""Schwab REST connectors: price-history bars, daily bars, account positions and transactions.

Phase 1 wraps REST price-history candles; ADR-0279 adds a point-in-time
``positions`` snapshot of every linked account, closed DAILY candles keyed by
their New York session date (research A0003: the risk inputs of a hold decision)
and the ``trades`` stream of each account's transactions (the history tax lots
are rebuilt from). Both bar connectors re-request a declared overlap on live
pulls so corrected closed bars become new bitemporal evidence; downstream
observation scanning chooses the latest acquisition. OAuth values and token
paths are named through environment variables, and token refresh is delegated
to the generic onboarding service; one authorization serves all four packs.

The packs share one transport (:class:`_SchwabConnector`): a bearer-token GET,
retried on throttling, server and network failures, whose refusals name the
pack's request through ``_NOUN`` ("Schwab price history" for minute bars,
"Schwab daily history" for daily bars, "Schwab accounts" for positions,
"Schwab transactions" for trades), and one reader of the accountNumbers
endpoint (``_hashes``).
"""

from __future__ import annotations

import copy
import functools
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..base import AssetError, MODES, parse_utc
from ..connector import MAX_BACKOFF_S, PROTOCOL, Connector, backoff
from ..oauth import OAuth2TokenService
from .alpaca import BAR_FIELDS, BAR_KEY_FIELDS, BAR_STREAM

__all__ = [
    "BAR_INTERVAL",
    "DAILY_INTERVAL",
    "DEFAULT_CALLBACK_URL_ENV",
    "DEFAULT_CLIENT_ID_ENV",
    "DEFAULT_CLIENT_SECRET_ENV",
    "DEFAULT_LIVE_LOOKBACK_MINUTES",
    "DEFAULT_OVERLAP_DAYS",
    "DEFAULT_OVERLAP_MINUTES",
    "DEFAULT_TOKEN_PATH_ENV",
    "DEFAULT_WINDOW_DAYS",
    "INSTRUMENT_ASSET_TYPES",
    "MAX_TRANSACTIONS",
    "POSITION_EFFECTS",
    "POSITION_FIELDS",
    "POSITION_KEY_FIELDS",
    "POSITION_STREAM",
    "TRANSACTION_FIELDS",
    "TRANSACTION_KEY_FIELDS",
    "TRANSACTION_STREAM",
    "TRANSACTION_TYPES",
    "SchwabBarsConnector",
    "SchwabDailyBarsConnector",
    "SchwabPositionsConnector",
    "SchwabTransactionsConnector",
]

BAR_INTERVAL = (1, "Minute")
DAILY_INTERVAL = (1, "Day")
DEFAULT_LIVE_LOOKBACK_MINUTES = 120
DEFAULT_OVERLAP_MINUTES = 5
DEFAULT_OVERLAP_DAYS = 5
DEFAULT_CLIENT_ID_ENV = "SCHWAB_APP_KEY"
DEFAULT_CLIENT_SECRET_ENV = "SCHWAB_APP_SECRET"
DEFAULT_CALLBACK_URL_ENV = "SCHWAB_CALLBACK_URL"
DEFAULT_TOKEN_PATH_ENV = "SCHWAB_TOKEN_PATH"

_AUTHORIZATION_URL = "https://api.schwabapi.com/v1/oauth/authorize"
_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
_PRICE_HISTORY_URL = "https://api.schwabapi.com/marketdata/v1/pricehistory"
_ACCOUNTS_URL = "https://api.schwabapi.com/trader/v1/accounts"
_ACCOUNT_NUMBERS_URL = "https://api.schwabapi.com/trader/v1/accounts/accountNumbers"
POSITION_STREAM = "positions"
POSITION_FIELDS = (
    "account", "symbol", "asset_type", "as_of", "long_quantity",
    "short_quantity", "average_price", "cost_basis", "market_value",
)
POSITION_KEY_FIELDS = ("account", "symbol", "as_of")
_POSITION_NUMBERS = ("longQuantity", "shortQuantity", "averagePrice", "marketValue")
# Schwab omits ``positions`` (rather than sending ``[]``) for an account that holds nothing,
# as the first real pull showed (ADR-0279 amendment, 2026-10-29). These ``currentBalances``
# fields, every one present and exactly zero, are the proof an account without the key is
# empty. A money-market fund is held as a position, so its value is listed; cash is a
# balance, not a position, so ``cashBalance`` is not.
_EMPTY_ACCOUNT_VALUES = (
    "longMarketValue", "shortMarketValue", "longOptionMarketValue",
    "shortOptionMarketValue", "mutualFundValue", "bondValue", "moneyMarketFund",
)
TRANSACTION_STREAM = "trades"
TRANSACTION_FIELDS = (
    "account", "transaction_id", "trade_date", "time", "symbol", "asset_type", "quantity",
    "price", "amount", "position_effect",
)
TRANSACTION_KEY_FIELDS = ("account", "transaction_id", "symbol")
#: Schwab's closed vocabulary for a transaction's ``type`` (the ``types`` filter).
TRANSACTION_TYPES = (
    "TRADE", "RECEIVE_AND_DELIVER", "DIVIDEND_OR_INTEREST", "ACH_RECEIPT",
    "ACH_DISBURSEMENT", "CASH_RECEIPT", "CASH_DISBURSEMENT", "ELECTRONIC_FUND",
    "WIRE_OUT", "WIRE_IN", "JOURNAL", "MEMORANDUM", "MARGIN_CALL", "MONEY_MARKET",
    "SMA_ADJUSTMENT",
)
#: Schwab's closed vocabulary for a transfer item instrument's ``assetType``.
INSTRUMENT_ASSET_TYPES = (
    "EQUITY", "OPTION", "INDEX", "MUTUAL_FUND", "CASH_EQUIVALENT", "FIXED_INCOME",
    "CURRENCY", "COLLECTIVE_INVESTMENT", "FOREX", "FUTURE", "PRODUCT",
)
#: Schwab's closed vocabulary for a transfer item's ``positionEffect``.
POSITION_EFFECTS = ("OPENING", "CLOSING", "AUTOMATIC", "UNKNOWN")
#: The widest window one transactions request may span, in days (Schwab: one year).
DEFAULT_WINDOW_DAYS = 365
#: Schwab's per-response transaction cap: a window returning this many may be truncated.
MAX_TRANSACTIONS = 3000
_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_RETRIES = 3
_RETRY_STATUSES = (429, 500, 502, 503, 504)
#: The zone a US equity session is dated in, and the New York wall clock of its regular open.
_SESSION_ZONE_KEY = "America/New_York"
_SESSION_OPEN = (9, 30)
#: Calendar days the daily pack's ``check`` probe asks for, ending at the pull's New York date.
_PROBE_DAYS = 7
_CANDLE_NUMBERS = ("open", "high", "low", "close", "volume")


@functools.cache
def _session_zone():
    """Return the session zone, built at first use: importing the pack needs no tz database."""
    return ZoneInfo(_SESSION_ZONE_KEY)


def _positive_number(problems, name, value):
    """Append one problem unless value is a positive finite number."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        problems.append(f"config.{name} must be a positive number, got {value!r}")


def _symbols_knob(problems, config):
    """Return ``config['symbols']``, recording a problem unless it is a non-empty list of names."""
    symbols = config.get("symbols")
    if (
        not isinstance(symbols, list)
        or not symbols
        or not all(isinstance(symbol, str) and symbol for symbol in symbols)
    ):
        problems.append(
            f"config.symbols must be a non-empty list of strings, got {symbols!r}"
        )
    return symbols


def _iso_date(value):
    """Return the date an exact ISO ``YYYY-MM-DD`` string names, else None."""
    if not isinstance(value, str):
        return None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    return day if day.isoformat() == value else None


def _env_knobs(problems, config):
    """Resolve the four OAuth environment-variable names, recording problems."""
    envs = {
        "client_id_env": config.get("client_id_env", DEFAULT_CLIENT_ID_ENV),
        "client_secret_env": config.get(
            "client_secret_env", DEFAULT_CLIENT_SECRET_ENV
        ),
        "callback_url_env": config.get(
            "callback_url_env", DEFAULT_CALLBACK_URL_ENV
        ),
        "token_path_env": config.get("token_path_env", DEFAULT_TOKEN_PATH_ENV),
    }
    for name, value in envs.items():
        if not isinstance(value, str) or not value:
            problems.append(
                f"config.{name} must be a non-empty environment-variable name"
            )
    return envs


def _transport_knobs(problems, config):
    """Resolve timeout and max_retries, recording problems."""
    timeout = config.get("timeout", _DEFAULT_TIMEOUT)
    _positive_number(problems, "timeout", timeout)
    max_retries = config.get("max_retries", _DEFAULT_MAX_RETRIES)
    if (
        isinstance(max_retries, bool)
        or not isinstance(max_retries, int)
        or max_retries < 0
    ):
        problems.append(
            f"config.max_retries must be an int >= 0, got {max_retries!r}"
        )
    return {"timeout": timeout, "max_retries": max_retries}


_SHARED_SPEC = {
    "client_id_env": {
        "secret": True,
        "notes": "Environment-variable name holding the app key; "
                 f"default {DEFAULT_CLIENT_ID_ENV}.",
    },
    "client_secret_env": {
        "secret": True,
        "notes": "Environment-variable name holding the app secret; "
                 f"default {DEFAULT_CLIENT_SECRET_ENV}.",
    },
    "callback_url_env": {
        "secret": True,
        "notes": "Environment-variable name holding the callback URL; "
                 f"default {DEFAULT_CALLBACK_URL_ENV}.",
    },
    "token_path_env": {
        "secret": True,
        "notes": "Environment-variable name holding the token path; "
                 f"default {DEFAULT_TOKEN_PATH_ENV}.",
    },
    "timeout": {
        "notes": f"HTTP timeout in seconds; default {_DEFAULT_TIMEOUT}.",
    },
    "max_retries": {
        "notes": "Extra attempts on throttling, server, and network "
                 f"failures, every wait capped at {MAX_BACKOFF_S:g} s; "
                 f"default {_DEFAULT_MAX_RETRIES}.",
    },
}


def _shared_spec():
    """Return a fresh copy of the knobs both packs declare, so no spec aliases another's."""
    return copy.deepcopy(_SHARED_SPEC)


class _SchwabConnector(Connector):
    """OAuth, clock and retrying JSON transport shared by the Schwab packs.

    ``_NOUN`` names what the pack requests in every transport refusal ("Schwab
    price history failed for 'AAPL' (HTTP 404)"); each pack overrides it.
    """

    _NOUN = "Schwab request"

    def _oauth_service(self, knobs):
        """Build the generic token service from named environment values."""
        return OAuth2TokenService(
            client_id_env=knobs["client_id_env"],
            client_secret_env=knobs["client_secret_env"],
            callback_url_env=knobs["callback_url_env"],
            token_path_env=knobs["token_path_env"],
            authorization_url=_AUTHORIZATION_URL,
            token_url=_TOKEN_URL,
        )

    def oauth_service(self, config):
        """Build the OAuth service used by manual authorization.

        Parameters
        ----------
        config : dict
            Schwab source configuration.

        Returns
        -------
        OAuth2TokenService
            Service bound to Schwab endpoints and named environment values.

        Raises
        ------
        AssetError
            If connector config is invalid.
        """
        return self._oauth_service(self.resolve_knobs(config))

    def _access_token(self, knobs):
        """Resolve or refresh bearer material immediately before a request."""
        return self._oauth_service(knobs).ensure_access_token()

    def _now(self):
        """Return the current UTC instant through a test seam."""
        return datetime.now(timezone.utc)

    def _request(self, token, label, url, params, timeout):
        """Perform one authenticated GET and decode the JSON body."""
        if params:
            url = f"{url}?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.getcode()
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in _RETRY_STATUSES:
                raise ConnectionError(f"HTTP {exc.code}") from exc
            raise AssetError(
                [f"{self._NOUN} failed for {label!r} (HTTP {exc.code})"]
            ) from exc
        if not 200 <= status < 300:
            if status in _RETRY_STATUSES:
                raise ConnectionError(f"HTTP {status}")
            raise AssetError(
                [f"{self._NOUN} failed for {label!r} (HTTP {status})"]
            )
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise AssetError(
                [f"{self._NOUN} for {label!r} returned malformed JSON"]
            ) from exc

    def _get(self, token, label, url, params, knobs):
        """Retry transient failures through the single transport seam."""
        last = None
        for attempt in range(knobs["max_retries"] + 1):
            if attempt:
                time.sleep(backoff(attempt))
            try:
                return self._request(token, label, url, params, knobs["timeout"])
            except (ConnectionError, OSError) as exc:
                last = str(exc)
        raise AssetError(
            [f"{self._NOUN} for {label!r} failed after "
             f"{knobs['max_retries'] + 1} attempt(s): {last}"]
        )

    def _hashes(self, token, knobs):
        """Return ``{accountNumber: hashValue}`` from Schwab's own account-number digest list.

        The one reader of the accountNumbers endpoint for every account-level pack
        (positions, transactions); the number never leaves the connector.
        """
        body = self._get(token, "accountNumbers", _ACCOUNT_NUMBERS_URL, {}, knobs)
        if not isinstance(body, list):
            raise AssetError(["Schwab accountNumbers response is not a list"])
        hashes = {}
        for index, entry in enumerate(body):
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("accountNumber"), str)
                or not isinstance(entry.get("hashValue"), str)
                or not entry["hashValue"]
            ):
                raise AssetError([f"Schwab accountNumbers entry {index} is malformed"])
            hashes[entry["accountNumber"]] = entry["hashValue"]
        return hashes

    @staticmethod
    def _check_read_args(state, streams, mode):
        """Refuse a ``read`` call whose checkpoint, stream list or mode is unusable."""
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        if mode not in MODES:
            raise AssetError([f"mode must be one of {MODES}, got {mode!r}"])

    def _require_object(self, body, label):
        """Return ``body`` when it is a JSON object, else refuse naming the request."""
        if not isinstance(body, dict):
            raise AssetError([f"{self._NOUN} for {label!r} returned a non-object"])
        return body

    def _candles(self, body, symbol):
        """Return a price-history response's ``candles`` list, refusing a body without one."""
        candles = self._require_object(body, symbol).get("candles")
        if not isinstance(candles, list):
            raise AssetError([f"{self._NOUN} for {symbol!r} lacks a candles list"])
        return candles


class SchwabBarsConnector(_SchwabConnector):
    """Closed one-minute Schwab candles with overlap-aware live pulls.

    Parameters
    ----------
    None
        The connector is stateless; config and checkpoints supply all state.

    Examples
    --------
    Discover the provider-neutral bar schema offline::

        connector = SchwabBarsConnector()
        streams = connector.discover({
            "symbols": ["AAPL"],
            "start": "2026-01-01",
        })
    """

    _NOUN = "Schwab price history"

    def spec(self):
        """Declare the default-deny Schwab configuration catalogue.

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
            "timeframe": {
                "notes": "Phase-1 REST interval; must be "
                         f"{list(BAR_INTERVAL)}.",
            },
            "live_lookback_minutes": {
                "notes": "First live pull's bounded history window; default "
                         f"{DEFAULT_LIVE_LOOKBACK_MINUTES}.",
            },
            "overlap_minutes": {
                "notes": "Closed minutes re-requested on every live pull; "
                         f"default {DEFAULT_OVERLAP_MINUTES}.",
            },
            **_shared_spec(),
        }}

    def resolve_knobs(self, config):
        """Validate config and resolve every Schwab request setting.

        Parameters
        ----------
        config : dict
            Connector configuration after reserved keys are removed.

        Returns
        -------
        dict
            Fully resolved request and OAuth settings.

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
        symbols = _symbols_knob(problems, config)
        start = config.get("start")
        if not isinstance(start, str) or not start:
            problems.append(f"config.start must be an ISO string, got {start!r}")
        timeframe = config.get("timeframe", BAR_INTERVAL)
        if (
            not isinstance(timeframe, (list, tuple))
            or tuple(timeframe) != BAR_INTERVAL
        ):
            problems.append(
                f"config.timeframe must be {list(BAR_INTERVAL)}, got {timeframe!r}"
            )
        lookback = config.get(
            "live_lookback_minutes", DEFAULT_LIVE_LOOKBACK_MINUTES
        )
        overlap = config.get("overlap_minutes", DEFAULT_OVERLAP_MINUTES)
        _positive_number(problems, "live_lookback_minutes", lookback)
        _positive_number(problems, "overlap_minutes", overlap)
        transport = _transport_knobs(problems, config)
        envs = _env_knobs(problems, config)
        if problems:
            raise AssetError(problems)
        parse_utc(start)
        return {
            "symbols": list(symbols),
            "start": start,
            "timeframe": BAR_INTERVAL,
            "live_lookback_minutes": lookback,
            "overlap_minutes": overlap,
            **transport,
            **envs,
        }

    def _window(self, knobs, cursor, mode):
        """Return a provider window whose end excludes the open minute."""
        end = self._now().replace(second=0, microsecond=0)
        configured = parse_utc(knobs["start"])
        if cursor:
            start = parse_utc(cursor)
            if mode == "live":
                start -= timedelta(minutes=knobs["overlap_minutes"])
            if start < configured:
                start = configured
        elif mode == "live":
            start = max(
                configured,
                end - timedelta(minutes=knobs["live_lookback_minutes"]),
            )
        else:
            start = configured
        if end <= start:
            return None, None
        return start, end

    def _fetch(self, token, symbol, params, timeout):
        """Perform one price-history GET and require a JSON object."""
        body = super()._request(token, symbol, _PRICE_HISTORY_URL, params, timeout)
        return self._require_object(body, symbol)

    def _request(self, token, label, url, params, timeout):
        """Route the bars pack's one request through its ``_fetch`` seam."""
        return self._fetch(token, label, params, timeout)

    def _get_json(self, token, symbol, params, knobs):
        """Return one price-history object; the base transport retries transient failures."""
        return self._get(token, symbol, _PRICE_HISTORY_URL, {"symbol": symbol, **params}, knobs)

    def _params(self, start, end):
        """Build the fixed Phase-1 one-minute price-history query."""
        return {
            "periodType": "day",
            "frequencyType": "minute",
            "frequency": BAR_INTERVAL[0],
            "startDate": int(start.timestamp() * 1000),
            "endDate": int(end.timestamp() * 1000),
            "needExtendedHoursData": "true",
            "needPreviousClose": "false",
        }

    def _rows(self, body, symbol, start, end):
        """Validate and normalize closed candles from one response."""
        candles = self._candles(body, symbol)
        rows = []
        for index, candle in enumerate(candles):
            if not isinstance(candle, dict):
                raise AssetError(
                    [f"Schwab candle {index} for {symbol!r} is not an object"]
                )
            try:
                stamp_ms = candle["datetime"]
                if (
                    isinstance(stamp_ms, bool)
                    or not isinstance(stamp_ms, (int, float))
                ):
                    raise ValueError("datetime is not numeric")
                stamp = datetime.fromtimestamp(
                    stamp_ms / 1000, tz=timezone.utc
                )
                row = {
                    "symbol": symbol,
                    "ts": stamp.isoformat(),
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "volume": float(candle["volume"]),
                    "trade_count": None,
                    "vwap": None,
                }
            except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
                raise AssetError(
                    [f"Schwab candle {index} for {symbol!r} is malformed"]
                ) from exc
            if start <= stamp < end:
                rows.append(row)
        return rows

    def check(self, config):
        """Validate OAuth and perform one non-persisting history probe.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        None
            Silence means OAuth and the endpoint answered.

        Raises
        ------
        AssetError
            If config, OAuth, or the provider request fails.
        """
        knobs = self.resolve_knobs(config)
        token = self._access_token(knobs)
        end = self._now().replace(second=0, microsecond=0)
        start = end - timedelta(minutes=1)
        self._get_json(
            token, knobs["symbols"][0], self._params(start, end), knobs
        )

    def discover(self, config):
        """Describe the provider-neutral bar stream offline.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        list
            One ``bars`` stream declaration.

        Raises
        ------
        AssetError
            If config is invalid.
        """
        knobs = self.resolve_knobs(config)
        return [{
            "stream": BAR_STREAM,
            "schema": {"fields": list(BAR_FIELDS)},
            "primary_key": list(BAR_KEY_FIELDS),
            "timeframe": list(knobs["timeframe"]),
        }]

    def read(self, config, streams, state, mode):
        """Emit closed bars, including live overlap, then checkpoint.

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
            If arguments, config, OAuth, or provider data are invalid.
        """
        self._check_read_args(state, streams, mode)
        knobs = self.resolve_knobs(config)
        token = self._access_token(knobs)
        new_state = {key: dict(value) for key, value in state.items()}
        for stream in streams:
            if stream != BAR_STREAM:
                raise AssetError(
                    [f"unknown stream {stream!r}; discovered: {[BAR_STREAM]}"]
                )
            yield {
                "protocol": PROTOCOL,
                "type": "SCHEMA",
                "stream": stream,
                "schema": {"fields": list(BAR_FIELDS)},
            }
            cursor = state.get(stream, {}).get("cursor", "")
            cursor_dt = parse_utc(cursor) if cursor else None
            emitted, emitted_dt = cursor, cursor_dt
            start, end = self._window(knobs, cursor, mode)
            rows = []
            if start is not None:
                params = self._params(start, end)
                for symbol in sorted(knobs["symbols"]):
                    body = self._get_json(token, symbol, params, knobs)
                    rows.extend(self._rows(body, symbol, start, end))
            rows.sort(key=lambda row: (parse_utc(row["ts"]), row["symbol"]))
            for row in rows:
                effective = row["ts"]
                effective_dt = parse_utc(effective)
                if (
                    mode == "backfill"
                    and cursor_dt is not None
                    and effective_dt <= cursor_dt
                ):
                    continue
                yield {
                    "protocol": PROTOCOL,
                    "type": "RECORD",
                    "stream": stream,
                    "effective_date": effective,
                    "kind": "observation",
                    "data": row,
                }
                if emitted_dt is None or effective_dt > emitted_dt:
                    emitted, emitted_dt = effective, effective_dt
            new_state.setdefault(stream, {})["cursor"] = emitted
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}


class SchwabDailyBarsConnector(_SchwabConnector):
    """Closed daily Schwab candles, keyed by their New York session date (ADR-0279, A0003).

    Emits the provider-neutral ``bars`` stream keyed ``(symbol, ts)``, with ``ts`` (and
    the record's ``effective_date``) the session date as ISO ``YYYY-MM-DD``, the OHLCV
    numbers as floats and ``trade_count`` / ``vwap`` null (Schwab sends neither). One
    request per symbol: ``periodType=year``, ``frequencyType=daily``, ``frequency=1``,
    ``startDate`` = 00:00 New York of the first day wanted, ``endDate`` = 00:00 New York
    of the pull's own New York date, regular hours only, no previous close.

    **Session date.** Schwab stamps a daily candle at 00:00 Central time of its session
    (05:00 UTC in daylight time, 06:00 UTC in standard time) and clips a request by
    calendar day. The session date is the America/New_York calendar date of the stamp's
    ``datetime`` (01:00 New York). A stamp whose New York wall time is at or after the
    09:30 open refuses: it is not the session-start label this mapping reads (a stamp at
    00:00 UTC, say, is 20:00 New York the evening before), so its session would be a
    guess. Two candles on one session date refuse too.

    **Closed.** A session is closed when its date is strictly before the pull's New York
    date; today's candle is never emitted, at any hour (the provider may return it, since
    it clips by calendar day). Every candle in a response must be well formed (finite
    numbers, never booleans), today's included.

    **Cursor.** The last emitted session date. A backfill resumes the day after it; a
    live pull re-requests ``overlap_days`` calendar days before it (never before
    ``start``), so a corrected close lands as new bitemporal evidence and dedup keeps the
    latest acquisition. Without a cursor both modes read from ``start``. Nothing closed
    since the cursor means no request and an unmoved cursor.

    Whether the closes are split- or dividend-adjusted is undocumented by Schwab; a
    consumer computing returns should check one holding with a recent split.

    Parameters
    ----------
    None
        The connector is stateless; config and checkpoints supply all state.

    Examples
    --------
    Discover the daily bar stream offline::

        connector = SchwabDailyBarsConnector()
        streams = connector.discover({"symbols": ["SPY", "AAPL"], "start": "2025-09-01"})
    """

    _NOUN = "Schwab daily history"

    def spec(self):
        """Declare the default-deny daily-bars configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {"params": {
            "symbols": {
                "required": True,
                "notes": "Non-empty list of symbols; a benchmark (SPY, say) is one of them.",
            },
            "start": {
                "required": True,
                "notes": "Earliest session date to fetch, ISO YYYY-MM-DD.",
            },
            "overlap_days": {
                "notes": "Calendar days before the cursor a live pull re-requests, so late "
                         f"corrections land as new evidence; default {DEFAULT_OVERLAP_DAYS}.",
            },
            **_shared_spec(),
        }}

    def resolve_knobs(self, config):
        """Validate config and resolve every daily-history request setting.

        Parameters
        ----------
        config : dict
            Connector configuration after reserved keys are removed.

        Returns
        -------
        dict
            Fully resolved request and OAuth settings.

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
        symbols = _symbols_knob(problems, config)
        start = config.get("start")
        if _iso_date(start) is None:
            problems.append(f"config.start must be an ISO date YYYY-MM-DD, got {start!r}")
        overlap = config.get("overlap_days", DEFAULT_OVERLAP_DAYS)
        if isinstance(overlap, bool) or not isinstance(overlap, int) or overlap < 1:
            problems.append(f"config.overlap_days must be an int >= 1, got {overlap!r}")
        transport = _transport_knobs(problems, config)
        envs = _env_knobs(problems, config)
        if problems:
            raise AssetError(problems)
        return {
            "symbols": list(symbols),
            "start": start,
            "overlap_days": overlap,
            **transport,
            **envs,
        }

    def _today(self):
        """Return the pull's New York calendar date: no session on or after it is closed."""
        return self._now().astimezone(_session_zone()).date()

    @staticmethod
    def _first_day(knobs, cursor, mode):
        """Return the first session date to request under the checkpoint and mode."""
        configured = date.fromisoformat(knobs["start"])
        if not cursor:
            return configured
        last = _iso_date(cursor)
        if last is None:
            raise AssetError([f"daily bars checkpoint cursor {cursor!r} is not an ISO date"])
        if mode == "live":
            back = knobs["overlap_days"]
            if back >= (last - configured).days:
                return configured
            return last - timedelta(days=back)
        return max(configured, last + timedelta(days=1))

    @staticmethod
    def _midnight_ms(day):
        """Return 00:00 New York of ``day`` as epoch milliseconds."""
        midnight = datetime(day.year, day.month, day.day, tzinfo=_session_zone())
        return int(midnight.timestamp()) * 1000

    def _params(self, first, today):
        """Build the daily price-history query from ``first`` up to (not including) ``today``."""
        return {
            "periodType": "year",
            "frequencyType": "daily",
            "frequency": DAILY_INTERVAL[0],
            "startDate": self._midnight_ms(first),
            "endDate": self._midnight_ms(today),
            "needExtendedHoursData": "false",
            "needPreviousClose": "false",
        }

    def _history(self, token, symbol, params, knobs):
        """Return one symbol's price-history response; the base transport retries."""
        return self._get(token, symbol, _PRICE_HISTORY_URL, {"symbol": symbol, **params}, knobs)

    def _session(self, index, symbol, candle):
        """Return ``(session date, row)`` of one candle, refusing what cannot be read exactly."""
        if not isinstance(candle, dict):
            raise AssetError(
                [f"Schwab daily candle {index} for {symbol!r} is not an object"]
            )
        try:
            stamp_ms = candle["datetime"]
            numbers = {name: candle[name] for name in _CANDLE_NUMBERS}
            if any(isinstance(v, bool) or not isinstance(v, (int, float))
                   for v in (stamp_ms, *numbers.values())):
                raise ValueError("a candle field is not numeric")
            values = {name: float(v) for name, v in numbers.items()}
            if not all(math.isfinite(v) for v in values.values()):
                raise ValueError("a candle number is not finite")
            local = datetime.fromtimestamp(stamp_ms / 1000, tz=_session_zone())
        except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
            raise AssetError(
                [f"Schwab daily candle {index} for {symbol!r} is malformed"]
            ) from exc
        if (local.hour, local.minute) >= _SESSION_OPEN:
            raise AssetError([
                f"Schwab daily candle {index} for {symbol!r} is stamped {local:%H:%M} "
                f"New York, at or after the {_SESSION_OPEN[0]:02d}:{_SESSION_OPEN[1]:02d} "
                "open: not the session-start stamp its date is read from, so its session "
                "would be a guess"
            ])
        day = local.date()
        return day, {"symbol": symbol, "ts": day.isoformat(), **values,
                     "trade_count": None, "vwap": None}

    def _rows(self, body, symbol, first, today):
        """Validate every candle of one response; return the closed sessions from ``first``."""
        seen, rows = {}, []
        for index, candle in enumerate(self._candles(body, symbol)):
            day, row = self._session(index, symbol, candle)
            if day in seen:
                raise AssetError([
                    f"Schwab daily candles {seen[day]} and {index} for {symbol!r} are "
                    f"both session {day.isoformat()}"
                ])
            seen[day] = index
            if first <= day < today:
                rows.append(row)
        return rows

    def check(self, config):
        """Validate OAuth and probe the last week of the first symbol, persisting nothing.

        The response is read the way :meth:`read` reads it (every candle well formed and
        mapped to its session), so silence means the endpoint answered with price history.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        None
            Silence means OAuth and the endpoint answered.

        Raises
        ------
        AssetError
            If config, OAuth, the provider request or the response body fails.
        """
        knobs = self.resolve_knobs(config)
        token = self._access_token(knobs)
        today = self._today()
        first = today - timedelta(days=_PROBE_DAYS)
        symbol = knobs["symbols"][0]
        body = self._history(token, symbol, self._params(first, today), knobs)
        self._rows(body, symbol, first, today)

    def discover(self, config):
        """Describe the provider-neutral daily bar stream offline.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        list
            One ``bars`` stream declaration.

        Raises
        ------
        AssetError
            If config is invalid.
        """
        self.resolve_knobs(config)
        return [{
            "stream": BAR_STREAM,
            "schema": {"fields": list(BAR_FIELDS)},
            "primary_key": list(BAR_KEY_FIELDS),
            "timeframe": list(DAILY_INTERVAL),
        }]

    def read(self, config, streams, state, mode):
        """Emit every closed session from the first day the mode asks for, then checkpoint.

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
            If arguments, config, the checkpoint, OAuth, or provider data are invalid.
        """
        self._check_read_args(state, streams, mode)
        knobs = self.resolve_knobs(config)
        new_state = {key: dict(value) for key, value in state.items()}
        today = self._today()
        token = None
        for stream in streams:
            if stream != BAR_STREAM:
                raise AssetError(
                    [f"unknown stream {stream!r}; discovered: {[BAR_STREAM]}"]
                )
            yield {
                "protocol": PROTOCOL,
                "type": "SCHEMA",
                "stream": stream,
                "schema": {"fields": list(BAR_FIELDS)},
            }
            cursor = state.get(stream, {}).get("cursor", "")
            first = self._first_day(knobs, cursor, mode)
            rows = []
            if first < today:
                token = token or self._access_token(knobs)
                params = self._params(first, today)
                for symbol in sorted(set(knobs["symbols"])):
                    body = self._history(token, symbol, params, knobs)
                    rows.extend(self._rows(body, symbol, first, today))
            rows.sort(key=lambda row: (row["ts"], row["symbol"]))
            emitted = cursor
            for row in rows:
                yield {
                    "protocol": PROTOCOL,
                    "type": "RECORD",
                    "stream": stream,
                    "effective_date": row["ts"],
                    "kind": "observation",
                    "data": row,
                }
                emitted = max(emitted, row["ts"])
            new_state.setdefault(stream, {})["cursor"] = emitted
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}


class SchwabPositionsConnector(_SchwabConnector):
    """Point-in-time positions of every linked Schwab account (ADR-0279).

    Each pull emits one row per account and symbol dated ``as_of`` = the UTC
    calendar day it was taken (a pull after 20:00 New York time, 19:00 in
    standard time, is dated the next day); account numbers never leave the
    connector — ``account`` is Schwab's own ``hashValue`` from the
    accountNumbers endpoint. The four position numbers are required on every
    row and must be finite; a symbol must already be a non-empty string. An
    account without a ``positions`` key emits no rows only when its
    ``currentBalances`` prove it empty (every ``_EMPTY_ACCOUNT_VALUES`` field
    present and exactly zero: Schwab omits the key for an account holding
    nothing); otherwise it refuses. Nothing is defaulted or coerced.
    ``cost_basis`` is ``averagePrice x net quantity`` and so is a per-share
    basis only for shares: option rows carry no contract multiplier here,
    and a consumer that values them filters ``asset_type`` first. A backfill
    pull skips a day already checkpointed; a live pull always re-snapshots,
    and bitemporal dedup keeps the latest acquisition.

    Limits, disclosed rather than modelled. The account's ``type`` (cash, margin,
    IRA) is not read, so every linked account is treated as taxable downstream.
    ``asset_type`` is the instrument's ``assetType`` as Schwab sends it; the first
    real pull (2026-10-29) typed ETFs ``COLLECTIVE_INVESTMENT`` and mutual funds
    ``MUTUAL_FUND``, so a consumer that filters ``asset_type == "EQUITY"`` drops
    them without saying so.

    Parameters
    ----------
    None
        The connector is stateless; config and checkpoints supply all state.
    """

    _NOUN = "Schwab accounts"

    def spec(self):
        """Declare the default-deny positions configuration catalogue."""
        return {"params": _shared_spec()}

    def resolve_knobs(self, config):
        """Validate config and resolve OAuth and transport settings.

        Parameters
        ----------
        config : dict
            Connector configuration after reserved keys are removed.

        Returns
        -------
        dict
            Resolved OAuth and transport settings.

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
        transport = _transport_knobs(problems, config)
        envs = _env_knobs(problems, config)
        if problems:
            raise AssetError(problems)
        return {**transport, **envs}

    @staticmethod
    def _symbol_note(position):
        """Return `` (symbol 'X')`` when the position names a usable symbol, else nothing."""
        instrument = position.get("instrument") if isinstance(position, dict) else None
        symbol = instrument.get("symbol") if isinstance(instrument, dict) else None
        return f" (symbol {symbol!r})" if isinstance(symbol, str) and symbol else ""

    @staticmethod
    def _not_empty(account):
        """Return why ``account``'s balances fail to prove it holds nothing, else ``""``.

        The proof is every field of ``_EMPTY_ACCOUNT_VALUES`` present in ``currentBalances``
        as a non-bool number equal to zero; NaN and infinity are not zero. The first failing
        field names the reason.
        """
        if "currentBalances" not in account:
            return "no currentBalances"
        balances = account["currentBalances"]
        if not isinstance(balances, dict):
            return "currentBalances is not a mapping"
        for name in _EMPTY_ACCOUNT_VALUES:
            if name not in balances:
                return f"{name} is missing"
            value = balances[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"{name} is not a number"
            if value != 0:
                return f"{name} is not zero"
        return ""

    def _rows(self, body, hashes, as_of):
        """Validate and normalize every position in an accounts response."""
        if not isinstance(body, list):
            raise AssetError(["Schwab accounts response is not an accounts list"])
        rows = []
        for a_index, entry in enumerate(body):
            account = entry.get("securitiesAccount") if isinstance(entry, dict) else None
            if not isinstance(account, dict) or "accountNumber" not in account:
                raise AssetError([f"Schwab account {a_index} is malformed"])
            number = account["accountNumber"]
            if number not in hashes:
                raise AssetError(
                    [f"Schwab account {a_index} has no hashValue in accountNumbers"]
                )
            if "positions" not in account:
                # an absent list is "holds nothing" only when the balances say so; anything
                # else could be a truncated response, and guessing [] would drop holdings
                reason = self._not_empty(account)
                if reason:
                    raise AssetError(
                        [f"Schwab account {a_index} has no positions and its "
                         f"currentBalances do not show it empty: {reason}"]
                    )
                continue
            positions = account["positions"]
            if not isinstance(positions, list):
                raise AssetError([f"Schwab account {a_index} positions is not a list"])
            for p_index, position in enumerate(positions):
                try:
                    instrument = position["instrument"]
                    symbol = instrument["symbol"]
                    if not isinstance(symbol, str) or not symbol:
                        raise ValueError("a position symbol is not a non-empty string")
                    numbers = {name: position[name] for name in _POSITION_NUMBERS}
                    if any(isinstance(v, bool) or not isinstance(v, (int, float))
                           for v in numbers.values()):
                        raise ValueError("a position number is not numeric")
                    # float() raises OverflowError for an int no float can hold
                    values = {name: float(v) for name, v in numbers.items()}
                    if not all(math.isfinite(v) for v in values.values()):
                        raise ValueError("a position number is not finite")
                    long_qty = values["longQuantity"]
                    short_qty = values["shortQuantity"]
                    average = values["averagePrice"]
                    cost_basis = average * (long_qty - short_qty)
                    if not math.isfinite(cost_basis):
                        raise ValueError("a position cost basis is not finite")
                    rows.append({
                        "account": hashes[number],
                        "symbol": symbol,
                        "asset_type": instrument.get("assetType"),
                        "as_of": as_of,
                        "long_quantity": long_qty,
                        "short_quantity": short_qty,
                        "average_price": average,
                        "cost_basis": cost_basis,
                        "market_value": values["marketValue"],
                    })
                except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
                    raise AssetError(
                        [f"Schwab position {p_index} in account {a_index} is malformed"
                         f"{self._symbol_note(position)}"]
                    ) from exc
        rows.sort(key=lambda row: (row["account"], row["symbol"]))
        return rows

    def check(self, config):
        """Validate OAuth and perform one non-persisting accounts probe."""
        knobs = self.resolve_knobs(config)
        token = self._access_token(knobs)
        self._get(token, "accounts", _ACCOUNTS_URL, {}, knobs)

    def discover(self, config):
        """Describe the ``positions`` stream offline."""
        self.resolve_knobs(config)
        return [{
            "stream": POSITION_STREAM,
            "schema": {"fields": list(POSITION_FIELDS)},
            "primary_key": list(POSITION_KEY_FIELDS),
        }]

    def read(self, config, streams, state, mode):
        """Emit one positions snapshot dated today, then checkpoint the day.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list
            Requested streams; only ``positions`` exists.
        state : dict
            Prior mode-keyed connector checkpoint.
        mode : str
            ``backfill`` (skip a checkpointed day) or ``live`` (always pull).

        Yields
        ------
        dict
            Onboarding protocol messages.

        Raises
        ------
        AssetError
            If arguments, config, OAuth, or provider data are invalid.
        """
        self._check_read_args(state, streams, mode)
        knobs = self.resolve_knobs(config)
        new_state = {key: dict(value) for key, value in state.items()}
        as_of = self._now().date().isoformat()
        for stream in streams:
            if stream != POSITION_STREAM:
                raise AssetError(
                    [f"unknown stream {stream!r}; discovered: {[POSITION_STREAM]}"]
                )
            yield {
                "protocol": PROTOCOL,
                "type": "SCHEMA",
                "stream": stream,
                "schema": {"fields": list(POSITION_FIELDS)},
            }
            cursor = state.get(stream, {}).get("cursor", "")
            if mode == "backfill" and cursor == as_of:
                new_state.setdefault(stream, {})["cursor"] = cursor
                continue
            token = self._access_token(knobs)
            hashes = self._hashes(token, knobs)
            body = self._get(
                token, "accounts", _ACCOUNTS_URL, {"fields": "positions"}, knobs
            )
            for row in self._rows(body, hashes, as_of):
                yield {
                    "protocol": PROTOCOL,
                    "type": "RECORD",
                    "stream": stream,
                    "effective_date": as_of,
                    "kind": "observation",
                    "data": row,
                }
            new_state.setdefault(stream, {})["cursor"] = as_of
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}



def _ms_stamp(instant):
    """Return ``instant`` in the query form Schwab documents: ``yyyy-MM-ddTHH:mm:ss.SSSZ``, UTC."""
    utc = instant.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def _vocabulary_list(problems, config, name, default, allowed, what):
    """Resolve a list knob that must name distinct members of a closed vocabulary."""
    value = config.get(name, default)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item in allowed for item in value)
        or len(set(value)) != len(value)
    ):
        problems.append(
            f"config.{name} must be a non-empty list of distinct {what} {list(allowed)}, "
            f"got {value!r}"
        )
    return value


def _finite(value):
    """Return ``float(value)`` for a finite, non-bool number; raise ValueError otherwise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")
    number = float(value)       # OverflowError for an int no float can hold
    if not math.isfinite(number):
        raise ValueError("not finite")
    return number


class SchwabTransactionsConnector(_SchwabConnector):
    """Every linked account's transactions as a ``trades`` stream (ADR-0279).

    The history tax lots are rebuilt from. Accounts come from the accountNumbers
    endpoint (``_hashes``); each is pulled from ``/accounts/{hashValue}/transactions``
    in half-open windows of at most ``window_days`` (Schwab allows one year per
    request), from midnight UTC of the cursor day (else ``start``) to the pull
    instant. The cursor is the latest trade date emitted (never moved back) and its
    day is requested again in BOTH modes: a pull that skipped it would lose a trade
    booked after an earlier same-day pull, and bitemporal dedup on the key keeps one
    copy of every trade seen twice. A window that returns ``MAX_TRANSACTIONS`` (Schwab's
    per-response cap) refuses: the window may be truncated, so lower ``window_days``.
    The stream's STATE stores the ``start`` of the last pull: a LOWER ``start`` later
    clears the cursor (it vouches only for trades from the old start on) and re-pulls
    from the new ``start``; an equal or higher one resumes from the cursor.

    One row per KEPT transfer item: an item whose instrument ``assetType`` is in
    ``asset_types``. ``account`` is the hashValue (no account number leaves the
    connector), ``transaction_id`` the ``activityId``, ``trade_date`` the calendar
    date of ``tradeDate`` in the offset Schwab writes it, ``time`` the transaction's
    ``time`` exactly as Schwab writes it (an ISO-8601 instant, no conversion: it orders
    the fills of one day downstream), ``quantity`` the item's
    ``amount`` (signed shares: + bought, - sold), ``price`` the item's ``price``,
    ``amount`` the transaction's ``netAmount`` (signed net cash, fees included:
    negative is cash paid) when the transaction carries exactly one kept item, and
    null when it carries several (its cash cannot be split between them),
    ``position_effect`` the item's ``positionEffect`` or null when absent. Key
    ``(account, transaction_id, symbol)``; a key seen twice in one pull is one row
    when identical and refuses when not.

    Nothing is silently dropped: an item carrying ``feeType`` is a fee line (its
    cash is already in ``netAmount``) and an item of another asset type is
    skipped; both are counted, with the transactions read, the rows kept and the
    transactions whose amount is null, in a LOG message (printed by ``acquire``)
    and in the stream's STATE entry under ``last_pull``.

    REFUSED, by transaction and item index, nothing guessed: a body that is not a list; a
    transaction without an int ``activityId``, ISO-8601 string ``tradeDate`` and ``time``, a
    string ``status``, a finite ``netAmount`` and a ``transferItems`` list; a ``type`` outside
    the requested ``types``; a ``status`` other than ``VALID`` (a PENDING trade has not settled
    and an INVALID one never will: neither may become a lot, so the pull stops until the
    transaction resolves or ``start`` moves past it); an item without a string ``assetType``,
    a non-string ``feeType``, and for a kept item an empty symbol, a zero or non-finite share
    amount, a negative or non-finite price or a ``positionEffect`` outside Schwab's
    vocabulary; a key seen twice with different content; a window at the response cap.

    ASSUMED, from Schwab's public SDKs rather than its (login-only) spec, and not checkable
    here, so each rests on the downstream reconciliation (a wrong one shows as an oversell or
    a share-count mismatch, never as a quiet number): an item's ``amount`` is the signed
    share count; the date of ``tradeDate`` in its own offset is the trade date (an execution
    after 20:00 New York written in UTC would be dated the next day); ``time`` orders a day's
    fills (compared as the string Schwab wrote, exact while one offset is used); ``types``
    with several values is sent comma-separated (a response of another type refuses above);
    how far back Schwab serves history is unverified (one client documents a one-year
    lookback), so a purchase older than the API reaches leaves its lots incomplete; shares
    that move outside the requested ``types`` (a transfer in, a split, a spin-off, a
    reinvestment) are not seen; and ETFs are typed ``COLLECTIVE_INVESTMENT`` in Schwab's
    transaction schema while the default ``asset_types`` keeps ``EQUITY`` only, so the skipped
    census of the first real pull says whether to widen it.

    Parameters
    ----------
    None
        The connector is stateless; config and checkpoints supply all state.
    """

    _NOUN = "Schwab transactions"

    def spec(self):
        """Declare the default-deny transactions configuration catalogue."""
        return {"params": {
            "start": {
                "required": True,
                "notes": "Earliest trade date to request, ISO YYYY-MM-DD: on or before "
                         "the earliest purchase still held.",
            },
            "types": {
                "notes": "Schwab transaction types requested; default ['TRADE']; members "
                         f"of {list(TRANSACTION_TYPES)}.",
            },
            "asset_types": {
                "notes": "Instrument asset types kept as rows; default ['EQUITY']; members "
                         f"of {list(INSTRUMENT_ASSET_TYPES)}. ETFs may arrive as "
                         "COLLECTIVE_INVESTMENT.",
            },
            "window_days": {
                "notes": f"Days one request spans, 1..{DEFAULT_WINDOW_DAYS}; default "
                         f"{DEFAULT_WINDOW_DAYS}. Lower it when a window reaches the "
                         f"{MAX_TRANSACTIONS}-transaction cap.",
            },
            **_shared_spec(),
        }}

    def resolve_knobs(self, config):
        """Validate config and resolve every transactions request setting.

        Parameters
        ----------
        config : dict
            Connector configuration after reserved keys are removed.

        Returns
        -------
        dict
            Resolved window, filter, OAuth and transport settings; ``start`` is a date.

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
        start = config.get("start")
        start_day = _iso_date(start)
        if start_day is None:
            problems.append(f"config.start must be an ISO date YYYY-MM-DD, got {start!r}")
        types = _vocabulary_list(problems, config, "types", ["TRADE"], TRANSACTION_TYPES,
                                 "Schwab transaction types")
        asset_types = _vocabulary_list(problems, config, "asset_types", ["EQUITY"],
                                       INSTRUMENT_ASSET_TYPES, "Schwab instrument asset types")
        window = config.get("window_days", DEFAULT_WINDOW_DAYS)
        if (
            isinstance(window, bool)
            or not isinstance(window, int)
            or not 1 <= window <= DEFAULT_WINDOW_DAYS
        ):
            problems.append(
                f"config.window_days must be an int in [1, {DEFAULT_WINDOW_DAYS}], got {window!r}"
            )
        transport = _transport_knobs(problems, config)
        envs = _env_knobs(problems, config)
        if problems:
            raise AssetError(problems)
        return {
            "start": start_day,
            "types": list(types),
            "asset_types": list(asset_types),
            "window_days": window,
            **transport,
            **envs,
        }

    @staticmethod
    def _windows(begin, now, days):
        """Yield half-open ``(start, end)`` windows of at most ``days`` from ``begin`` to ``now``."""
        start = datetime(begin.year, begin.month, begin.day, tzinfo=timezone.utc)
        while start < now:
            end = min(start + timedelta(days=days), now)
            yield start, end
            start = end

    @staticmethod
    def _item(item, kept_types, census):
        """Return a kept item's row fields, or None for a counted fee line or skipped item."""
        asset = item["instrument"]["assetType"]
        if not isinstance(asset, str) or not asset:
            raise ValueError("an instrument assetType is not a non-empty string")
        fee = item.get("feeType")
        if fee is not None:
            if not isinstance(fee, str):
                raise ValueError("a feeType is not a string")
            census["fee_items"] += 1
            return None
        if asset not in kept_types:
            census["skipped"][asset] = census["skipped"].get(asset, 0) + 1
            return None
        symbol = item["instrument"]["symbol"]
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("a kept item's symbol is not a non-empty string")
        quantity = _finite(item["amount"])
        if quantity == 0.0:
            raise ValueError("a kept item moves no shares")
        price = _finite(item["price"])
        if price < 0.0:
            raise ValueError("a kept item's price is negative")
        effect = item.get("positionEffect")
        if effect is not None and effect not in POSITION_EFFECTS:
            raise ValueError("a positionEffect is outside Schwab's vocabulary")
        return {"symbol": symbol, "asset_type": asset, "quantity": quantity,
                "price": price, "position_effect": effect}

    def _rows(self, body, knobs, account, label, census):
        """Validate one window's transactions and return its rows, counting the rest."""
        if not isinstance(body, list):
            raise AssetError([f"Schwab transactions response for {label} is not a list"])
        if len(body) >= MAX_TRANSACTIONS:
            raise AssetError(
                [f"Schwab transactions for {label} returned {len(body)} transactions, the "
                 "response cap: the window may be truncated; lower window_days"]
            )
        rows = []
        for t_index, transaction in enumerate(body):
            where = f"transaction {t_index} for {label}"
            try:
                activity = transaction["activityId"]
                if isinstance(activity, bool) or not isinstance(activity, int):
                    raise ValueError("an activityId is not an int")
                kind = transaction["type"]
                stamp = transaction["tradeDate"]
                if not isinstance(stamp, str):
                    raise ValueError("a tradeDate is not a string")
                trade_date = datetime.fromisoformat(stamp).date().isoformat()
                time_ = transaction["time"]
                if not isinstance(time_, str):
                    raise ValueError("a time is not a string")
                datetime.fromisoformat(time_)
                status = transaction["status"]
                if not isinstance(status, str):
                    raise ValueError("a status is not a string")
                net = _finite(transaction["netAmount"])
                items = transaction["transferItems"]
                if not isinstance(items, list):
                    raise ValueError("transferItems is not a list")
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise AssetError([f"Schwab {where} is malformed"]) from exc
            if kind not in knobs["types"]:
                raise AssetError(
                    [f"Schwab {where} has type {kind!r}, not one requested {knobs['types']}"]
                )
            if status != "VALID":
                raise AssetError(
                    [f"Schwab {where} has status {status!r}, not VALID: only a valid "
                     "transaction is a trade"]
                )
            census["transactions"] += 1
            kept = []
            for i_index, item in enumerate(items):
                try:
                    fields = self._item(item, knobs["asset_types"], census)
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    raise AssetError([f"Schwab item {i_index} of {where} is malformed"]) from exc
                if fields is not None:
                    kept.append(fields)
            if len(kept) > 1:
                census["unattributed"] += 1
            amount = net if len(kept) == 1 else None
            for fields in kept:
                rows.append({
                    "account": account, "transaction_id": activity, "trade_date": trade_date,
                    "time": time_, "symbol": fields["symbol"], "asset_type": fields["asset_type"],
                    "quantity": fields["quantity"], "price": fields["price"],
                    "amount": amount, "position_effect": fields["position_effect"],
                })
        return rows

    def check(self, config):
        """Validate OAuth and perform one non-persisting accountNumbers probe."""
        knobs = self.resolve_knobs(config)
        self._hashes(self._access_token(knobs), knobs)

    def discover(self, config):
        """Describe the ``trades`` stream offline."""
        self.resolve_knobs(config)
        return [{
            "stream": TRANSACTION_STREAM,
            "schema": {"fields": list(TRANSACTION_FIELDS)},
            "primary_key": list(TRANSACTION_KEY_FIELDS),
        }]

    @staticmethod
    def _resume(knobs, entry):
        """Return ``(begin, cursor)``: the day to pull from and the cursor that still stands.

        The cursor day (never before ``start``), unless ``start`` is LOWER than the one the last
        pull stored: its cursor then vouches only for trades from the old start on, so it is
        cleared and the pull begins at the new ``start``.
        """
        cursor, begin = entry.get("cursor", ""), knobs["start"]
        if "start" in entry:
            stored = entry["start"]
            try:
                if begin < date.fromisoformat(stored):
                    return begin, ""
            except (TypeError, ValueError) as exc:
                raise AssetError([f"state start is not an ISO date, got {stored!r}"]) from exc
        if cursor and parse_utc(cursor).date() > begin:
            begin = parse_utc(cursor).date()
        return begin, cursor

    def _pull(self, knobs, begin):
        """Return ``(rows, census)``: every account's kept items from ``begin`` to now, deduplicated."""
        now = self._now()
        token = self._access_token(knobs)
        hashes = self._hashes(token, knobs)
        census = {"transactions": 0, "rows": 0, "fee_items": 0, "skipped": {},
                  "unattributed": 0}
        seen = {}
        for a_index, account in enumerate(sorted(hashes.values())):
            url = f"{_ACCOUNTS_URL}/{urllib.parse.quote(account, safe='')}/transactions"
            for start, end in self._windows(begin, now, knobs["window_days"]):
                label = f"account {a_index} {start.date()}..{end.date()}"
                params = {"startDate": _ms_stamp(start), "endDate": _ms_stamp(end),
                          "types": ",".join(knobs["types"])}
                body = self._get(token, label, url, params, knobs)
                for row in self._rows(body, knobs, account, label, census):
                    key = tuple(row[field] for field in TRANSACTION_KEY_FIELDS)
                    if key in seen and seen[key] != row:
                        raise AssetError(
                            [f"Schwab transactions carry key {key!r} twice with different "
                             "content"]
                        )
                    seen[key] = row
        rows = sorted(seen.values(), key=lambda row: (
            row["trade_date"], row["account"], row["transaction_id"], row["symbol"]))
        census["rows"] = len(rows)
        census["skipped"] = dict(sorted(census["skipped"].items()))
        return rows, census

    def read(self, config, streams, state, mode):
        """Emit every kept transfer item since the cursor day (else ``start``), then checkpoint.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list
            Requested streams; only ``trades`` exists.
        state : dict
            Prior mode-keyed connector checkpoint.
        mode : str
            ``backfill`` or ``live``; both re-request the cursor day.

        Yields
        ------
        dict
            Onboarding protocol messages, the census as a LOG before the STATE.

        Raises
        ------
        AssetError
            If arguments, config, OAuth, or provider data are invalid.
        """
        self._check_read_args(state, streams, mode)
        knobs = self.resolve_knobs(config)
        new_state = {key: dict(value) for key, value in state.items()}
        for stream in streams:
            if stream != TRANSACTION_STREAM:
                raise AssetError(
                    [f"unknown stream {stream!r}; discovered: {[TRANSACTION_STREAM]}"]
                )
            yield {
                "protocol": PROTOCOL,
                "type": "SCHEMA",
                "stream": stream,
                "schema": {"fields": list(TRANSACTION_FIELDS)},
            }
            begin, cursor = self._resume(knobs, state.get(stream, {}))
            rows, census = self._pull(knobs, begin)
            for row in rows:
                yield {
                    "protocol": PROTOCOL,
                    "type": "RECORD",
                    "stream": stream,
                    "effective_date": row["trade_date"],
                    "kind": "observation",
                    "data": row,
                }
            yield {
                "protocol": PROTOCOL,
                "type": "LOG",
                "level": "info",
                "message": (
                    f"{self._NOUN}: {census['transactions']} transaction(s) -> "
                    f"{census['rows']} row(s); {census['fee_items']} fee line(s); skipped by "
                    f"asset type {census['skipped']}; {census['unattributed']} transaction(s) "
                    "with several kept items carry a null amount"
                ),
            }
            entry = new_state.setdefault(stream, {})
            # the latest trade date seen, never earlier than the cursor (a late posting can
            # carry an older trade date than the window it arrived in)
            entry["cursor"] = max([cursor, *(row["trade_date"] for row in rows[-1:])])
            entry["start"] = knobs["start"].isoformat()
            entry["last_pull"] = census
        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}
