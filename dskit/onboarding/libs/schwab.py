"""Schwab REST connectors: price-history bars and account positions.

Phase 1 wraps REST price-history candles; ADR-0279 adds a point-in-time
``positions`` snapshot of every linked account. The connector
re-requests a declared overlap on live pulls so corrected closed minutes become
new bitemporal evidence; downstream observation scanning chooses the latest
acquisition. OAuth values and token paths are named through environment
variables, and token refresh is delegated to the generic onboarding service.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from ..base import AssetError, MODES, parse_utc
from ..connector import MAX_BACKOFF_S, PROTOCOL, Connector, backoff
from ..oauth import OAuth2TokenService
from .alpaca import BAR_FIELDS, BAR_KEY_FIELDS, BAR_STREAM

__all__ = [
    "BAR_INTERVAL",
    "DEFAULT_CALLBACK_URL_ENV",
    "DEFAULT_CLIENT_ID_ENV",
    "DEFAULT_CLIENT_SECRET_ENV",
    "DEFAULT_LIVE_LOOKBACK_MINUTES",
    "DEFAULT_OVERLAP_MINUTES",
    "DEFAULT_TOKEN_PATH_ENV",
    "POSITION_FIELDS",
    "POSITION_KEY_FIELDS",
    "POSITION_STREAM",
    "SchwabBarsConnector",
    "SchwabPositionsConnector",
]

BAR_INTERVAL = (1, "Minute")
DEFAULT_LIVE_LOOKBACK_MINUTES = 120
DEFAULT_OVERLAP_MINUTES = 5
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
_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_RETRIES = 3
_RETRY_STATUSES = (429, 500, 502, 503, 504)


def _positive_number(problems, name, value):
    """Append one problem unless value is a positive finite number."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        problems.append(f"config.{name} must be a positive number, got {value!r}")



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


class _SchwabConnector(Connector):
    """OAuth, clock and retrying JSON transport shared by the Schwab packs."""

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
                [f"Schwab request failed for {label!r} (HTTP {exc.code})"]
            ) from exc
        if not 200 <= status < 300:
            if status in _RETRY_STATUSES:
                raise ConnectionError(f"HTTP {status}")
            raise AssetError(
                [f"Schwab request failed for {label!r} (HTTP {status})"]
            )
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise AssetError(
                [f"Schwab request for {label!r} returned malformed JSON"]
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
            [f"Schwab request for {label!r} failed after "
             f"{knobs['max_retries'] + 1} attempt(s): {last}"]
        )


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
            **_SHARED_SPEC,
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
        if not isinstance(body, dict):
            raise AssetError(
                [f"Schwab price history for {symbol!r} returned a non-object"]
            )
        return body

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
        candles = body.get("candles")
        if not isinstance(candles, list):
            raise AssetError(
                [f"Schwab price history for {symbol!r} lacks a candles list"]
            )
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
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        if mode not in MODES:
            raise AssetError([f"mode must be one of {MODES}, got {mode!r}"])
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


class SchwabPositionsConnector(_SchwabConnector):
    """Point-in-time positions of every linked Schwab account (ADR-0279).

    Each pull emits one row per account and symbol dated ``as_of`` = the UTC
    calendar day it was taken (a pull after 20:00 New York time is dated the
    next day); account numbers never leave the connector — ``account`` is
    Schwab's own ``hashValue`` from the accountNumbers endpoint. The four
    position numbers are required on every row and must be finite; a symbol
    must already be a non-empty string; an account response without a
    ``positions`` list refuses. Nothing is defaulted or coerced.
    ``cost_basis`` is ``averagePrice x net quantity`` and so is a per-share
    basis only for shares: option rows carry no contract multiplier here,
    and a consumer that values them filters ``asset_type`` first. A backfill
    pull skips a day already checkpointed; a live pull always re-snapshots,
    and bitemporal dedup keeps the latest acquisition.

    Parameters
    ----------
    None
        The connector is stateless; config and checkpoints supply all state.
    """

    def spec(self):
        """Declare the default-deny positions configuration catalogue."""
        return {"params": dict(_SHARED_SPEC)}

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

    def _hashes(self, token, knobs):
        """Return ``{accountNumber: hashValue}`` from Schwab's own account-number digest list."""
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
                raise AssetError(
                    [f"Schwab account {a_index} has no positions; the response must carry "
                     "an explicit positions list (empty when the account holds nothing)"]
                )
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
                        [f"Schwab position {p_index} in account {a_index} is malformed"]
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
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        if mode not in MODES:
            raise AssetError([f"mode must be one of {MODES}, got {mode!r}"])
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
