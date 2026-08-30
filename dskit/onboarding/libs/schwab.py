"""Schwab price-history bars normalized to the onboarding bar vocabulary.

Phase 1 deliberately wraps only REST price-history candles. The connector
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
from ..connector import PROTOCOL, Connector
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
    "SchwabBarsConnector",
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
_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_RETRIES = 3
_BACKOFF_SECONDS = 0.5
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


class SchwabBarsConnector(Connector):
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
                         f"failures; default {_DEFAULT_MAX_RETRIES}.",
            },
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
        timeout = config.get("timeout", _DEFAULT_TIMEOUT)
        _positive_number(problems, "live_lookback_minutes", lookback)
        _positive_number(problems, "overlap_minutes", overlap)
        _positive_number(problems, "timeout", timeout)
        max_retries = config.get("max_retries", _DEFAULT_MAX_RETRIES)
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or max_retries < 0
        ):
            problems.append(
                "config.max_retries must be an int >= 0, "
                f"got {max_retries!r}"
            )
        envs = {
            "client_id_env": config.get(
                "client_id_env", DEFAULT_CLIENT_ID_ENV
            ),
            "client_secret_env": config.get(
                "client_secret_env", DEFAULT_CLIENT_SECRET_ENV
            ),
            "callback_url_env": config.get(
                "callback_url_env", DEFAULT_CALLBACK_URL_ENV
            ),
            "token_path_env": config.get(
                "token_path_env", DEFAULT_TOKEN_PATH_ENV
            ),
        }
        for name, value in envs.items():
            if not isinstance(value, str) or not value:
                problems.append(
                    f"config.{name} must be a non-empty environment-variable name"
                )
        if problems:
            raise AssetError(problems)
        parse_utc(start)
        return {
            "symbols": list(symbols),
            "start": start,
            "timeframe": BAR_INTERVAL,
            "live_lookback_minutes": lookback,
            "overlap_minutes": overlap,
            "timeout": timeout,
            "max_retries": max_retries,
            **envs,
        }

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
        """Current UTC instant; a seam for closed-minute tests."""
        return datetime.now(timezone.utc)

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
        """Perform one authenticated GET and decode a JSON object."""
        url = f"{_PRICE_HISTORY_URL}?" + urllib.parse.urlencode(params)
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
                [f"Schwab price history failed for {symbol!r} (HTTP {exc.code})"]
            ) from exc
        if not 200 <= status < 300:
            if status in _RETRY_STATUSES:
                raise ConnectionError(f"HTTP {status}")
            raise AssetError(
                [f"Schwab price history failed for {symbol!r} (HTTP {status})"]
            )
        try:
            body = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise AssetError(
                [f"Schwab price history for {symbol!r} returned malformed JSON"]
            ) from exc
        if not isinstance(body, dict):
            raise AssetError(
                [f"Schwab price history for {symbol!r} returned a non-object"]
            )
        return body

    def _get_json(self, token, symbol, params, knobs):
        """Retry transient failures through the single transport seam."""
        last = None
        for attempt in range(knobs["max_retries"] + 1):
            if attempt:
                time.sleep(_BACKOFF_SECONDS * (2 ** (attempt - 1)))
            try:
                return self._fetch(token, symbol, params, knobs["timeout"])
            except (ConnectionError, OSError) as exc:
                last = str(exc)
        raise AssetError(
            [f"Schwab price history for {symbol!r} failed after "
             f"{knobs['max_retries'] + 1} attempt(s): {last}"]
        )

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
