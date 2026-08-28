"""``testing`` — deterministic doubles for the child's own tests.

:class:`StubBarsConnector` is :class:`AlpacaBarsConnector` with the ONE
vendor-touching method replaced: ``_fetch`` emits a deterministic
synthetic minute series instead of calling Alpaca, and credentials are
not required. Everything else — knob validation, the SIP window clamp,
cursor filtering, the message envelope — is the REAL code under test.
The dskit precedent is ``dskit/pipeline/testing.py``: doubles ship in
the package, importable by dotted path, so the onboarding registry can
resolve them in an end-to-end acquisition exactly like the production
class.

Extra knob (test-only): ``bars_per_symbol`` — how many one-minute bars
each symbol yields from ``start``
(:data:`DEFAULT_BARS_PER_SYMBOL` when undeclared).
"""

from __future__ import annotations

import math
from datetime import timedelta, timezone

from dskit.onboarding import parse_utc

from .connectors import AlpacaBarsConnector

__all__ = ["DEFAULT_BARS_PER_SYMBOL", "StubBarsConnector"]

#: How many bars each symbol yields when the config declares none —
#: one name, read by the knob gate AND by ``spec()``'s note.
DEFAULT_BARS_PER_SYMBOL = 90


class StubBarsConnector(AlpacaBarsConnector):
    """The production connector minus the network.

    Parameters
    ----------
    None
        Stateless, like the class it doubles; the extra
        ``bars_per_symbol`` knob rides on the config.

    Examples
    --------
    Pull a short deterministic history without credentials::

        conn = StubBarsConnector()
        msgs = list(conn.read({"symbols": ["AAPL"],
                               "start": "2026-01-05T14:30:00+00:00",
                               "bars_per_symbol": 10},
                              ["bars"], {}, "backfill"))
    """

    def spec(self) -> dict:
        """Extend the production catalogue with the one test-only knob.

        Returns
        -------
        dict
            ``spec()`` as :class:`AlpacaBarsConnector` declares it, with
            ``bars_per_symbol`` added.
        """
        spec = super().spec()
        spec["params"]["bars_per_symbol"] = {
            "notes": f"TEST-ONLY: bars each symbol yields from start; "
                     f"default {DEFAULT_BARS_PER_SYMBOL}.",
        }
        return spec

    def resolve_knobs(self, config) -> dict:
        """Resolve the production knobs, plus the stub's own.

        Parameters
        ----------
        config : dict
            A source config, optionally carrying ``bars_per_symbol``.

        Returns
        -------
        dict
            The production knobs plus ``bars_per_symbol`` (int).

        Raises
        ------
        AssetError
            Whatever the production gate refuses.
        """
        knobs = super().resolve_knobs(
            {k: v for k, v in config.items() if k != "bars_per_symbol"})
        knobs["bars_per_symbol"] = config.get("bars_per_symbol",
                                              DEFAULT_BARS_PER_SYMBOL)
        return knobs

    def _credentials(self, knobs) -> tuple:
        """No vendor, no credentials."""
        return "stub-key", "stub-secret"

    def check(self, config) -> None:
        """Run the knob gate only — there is no vendor to ping.

        Parameters
        ----------
        config : dict
            The config to check.

        Returns
        -------
        None
            Silence means the knobs are valid.

        Raises
        ------
        AssetError
            On any invalid knob.
        """
        self.resolve_knobs(config)

    def _fetch(self, knobs, start_dt, end_dt):
        """Yield a smooth deterministic walk, one bar per minute per symbol.

        Bars run from the CONFIG start and the window bounds filter
        them, exactly as the vendor's server would.
        """
        base = parse_utc(knobs["start"])
        for symbol in sorted(knobs["symbols"]):
            anchor = 100.0 + sum(ord(c) for c in symbol) % 50
            for i in range(knobs["bars_per_symbol"]):
                ts_dt = base + timedelta(minutes=i)
                if ts_dt < start_dt or ts_dt >= end_dt:
                    continue
                close = anchor * (1.0 + 0.001 * math.sin(i / 3.0))
                yield symbol, {
                    "symbol": symbol,
                    "ts": ts_dt.astimezone(timezone.utc).isoformat(),
                    "open": round(close * 0.999, 6),
                    "high": round(close * 1.001, 6),
                    "low": round(close * 0.998, 6),
                    "close": round(close, 6),
                    "volume": 1000.0 + 10 * i,
                    "trade_count": 10 + i,
                    "vwap": round(close, 6),
                }
