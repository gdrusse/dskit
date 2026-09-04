"""Deterministic doubles for the child's own tests.

Each stub keeps the real knob gate and message envelope and replaces
only the vendor-touching fetch.
"""

from __future__ import annotations

import math
from datetime import timedelta, timezone

from dskit.onboarding import parse_utc

from .connectors import AlpacaBars, SchwabBars

__all__ = [
    "DEFAULT_BARS_PER_SYMBOL",
    "StubAlpacaBars",
    "StubSchwabBars",
    "synthetic_bar",
]

#: How many bars each symbol yields when the config declares none.
DEFAULT_BARS_PER_SYMBOL = 12


def synthetic_bar(symbol, stamp, index=0):
    """Build one normalized bar payload.

    Parameters
    ----------
    symbol : str
        Ticker.
    stamp : datetime.datetime
        Bar open instant.
    index : int
        Deterministic walk offset.

    Returns
    -------
    dict
        Common bar fields.
    """
    anchor = 100.0 + sum(ord(ch) for ch in symbol) % 50
    close = anchor * (1.0 + 0.001 * math.sin(index / 3.0))
    return {
        "symbol": symbol,
        "ts": stamp.astimezone(timezone.utc).isoformat(),
        "open": round(close * 0.999, 6),
        "high": round(close * 1.001, 6),
        "low": round(close * 0.998, 6),
        "close": round(close, 6),
        "volume": 1000.0 + 10 * index,
        "trade_count": 10 + index,
        "vwap": round(close, 6),
    }


def _walk(knobs, start_dt, end_dt):
    """Yield a smooth minute walk inside the requested window."""
    base = parse_utc(knobs["start"])
    count = knobs.get("bars_per_symbol", DEFAULT_BARS_PER_SYMBOL)
    for symbol in sorted(knobs["symbols"]):
        for index in range(count):
            stamp = base + timedelta(minutes=index)
            if stamp < start_dt or stamp >= end_dt:
                continue
            yield symbol, synthetic_bar(symbol, stamp, index)


class _StubMixin:
    """Shared test-only knob and credential seams."""

    def spec(self):
        """Extend the production catalogue with the stub knob.

        Returns
        -------
        dict
            Production spec plus ``bars_per_symbol``.
        """
        spec = super().spec()
        spec["params"]["bars_per_symbol"] = {
            "notes": "TEST-ONLY: bars each symbol yields from start; "
            f"default {DEFAULT_BARS_PER_SYMBOL}.",
        }
        return spec

    def resolve_knobs(self, config):
        """Resolve production knobs plus the stub's own.

        Parameters
        ----------
        config : dict
            Source config, optionally carrying ``bars_per_symbol``.

        Returns
        -------
        dict
            Resolved knobs including ``bars_per_symbol``.
        """
        knobs = super().resolve_knobs(
            {key: value for key, value in config.items() if key != "bars_per_symbol"}
        )
        knobs["bars_per_symbol"] = config.get(
            "bars_per_symbol", DEFAULT_BARS_PER_SYMBOL
        )
        return knobs

    def check(self, config):
        """Run the knob gate only.

        Parameters
        ----------
        config : dict
            Config to check.
        """
        self.resolve_knobs(config)


class StubAlpacaBars(_StubMixin, AlpacaBars):
    """Production Alpaca connector minus the network.

    Parameters
    ----------
    None
        Stateless, like the class it doubles.

    Examples
    --------
    Pull a short deterministic history without credentials::

        conn = StubAlpacaBars()
        msgs = list(conn.read(
            {"symbols": ["AAPL"], "start": "2026-01-05T14:30:00+00:00",
             "bars_per_symbol": 3},
            ["bars"], {}, "backfill",
        ))
    """

    def _credentials(self, knobs):
        """No vendor, no credentials."""
        return "stub-key", "stub-secret"

    def _fetch(self, knobs, start_dt, end_dt):
        """Yield the deterministic walk."""
        yield from _walk(knobs, start_dt, end_dt)


class StubSchwabBars(_StubMixin, SchwabBars):
    """Production Schwab connector minus the network.

    Parameters
    ----------
    None
        Stateless, like the class it doubles.

    Examples
    --------
    Resolve knobs without OAuth::

        conn = StubSchwabBars()
        knobs = conn.resolve_knobs({
            "symbols": ["AAPL"],
            "start": "2026-01-05T14:30:00+00:00",
            "bars_per_symbol": 3,
        })
        knobs["symbols"]  # ['AAPL']
    """

    def _access_token(self, knobs):
        """No vendor, no token file."""
        return "stub-token"

    def _now(self):
        """Pin the live window just after the synthetic walk."""
        knobs = getattr(self, "_last_knobs", None)
        if knobs is None:
            return parse_utc("2026-01-05T16:00:00+00:00")
        count = knobs.get("bars_per_symbol", DEFAULT_BARS_PER_SYMBOL)
        return parse_utc(knobs["start"]) + timedelta(minutes=count + 1)

    def resolve_knobs(self, config):
        """Remember knobs so ``_now`` can close the synthetic window.

        Parameters
        ----------
        config : dict
            Source configuration.

        Returns
        -------
        dict
            Resolved knobs.
        """
        knobs = super().resolve_knobs(config)
        self._last_knobs = knobs
        return knobs

    def oauth_service(self, config):
        """Return the stub authorization surface.

        Parameters
        ----------
        config : dict
            Source configuration.

        Returns
        -------
        _StubOAuth
            Object with the authorize/exchange surface.
        """
        self.resolve_knobs(config)
        return _StubOAuth()

    def _get_json(self, token, symbol, params, knobs):
        """Return vendor-shaped candles from the deterministic walk.

        Parameters
        ----------
        token : str
            Unused stub bearer.
        symbol : str
            Requested ticker.
        params : dict
            Schwab query including start/end milliseconds.
        knobs : dict
            Resolved connector knobs.

        Returns
        -------
        dict
            ``{"candles": [...]}`` in Schwab's price-history shape.
        """
        start = parse_utc(knobs["start"])
        start_ms = params.get("startDate", 0)
        end_ms = params.get("endDate", 0)
        candles = []
        for index in range(knobs.get("bars_per_symbol", DEFAULT_BARS_PER_SYMBOL)):
            stamp = start + timedelta(minutes=index)
            stamp_ms = int(stamp.timestamp() * 1000)
            if stamp_ms < start_ms or stamp_ms >= end_ms:
                continue
            bar = synthetic_bar(symbol, stamp, index)
            candles.append(
                {
                    "datetime": stamp_ms,
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "volume": bar["volume"],
                }
            )
        return {"candles": candles}


class _StubOAuth:
    """Authorization surface that never leaves the process."""

    def authorization_url(self):
        """Return a fake URL an operator would open."""
        return "https://example.invalid/oauth/authorize?response_type=code"

    def exchange(self, returned):
        """Accept any non-empty code.

        Parameters
        ----------
        returned : str
            Callback URL or raw code.

        Returns
        -------
        dict
            Token metadata.
        """
        if not returned:
            from dskit.onboarding import AssetError

            raise AssetError(["authorization code must be a non-empty string"])
        return {"access_token": "stub", "refresh_token": "stub"}
