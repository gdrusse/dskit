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
each symbol yields from ``start``; default 90.
"""

from __future__ import annotations

import math
from datetime import timedelta, timezone

from dskit.onboarding import parse_utc

from .connectors import AlpacaBarsConnector

__all__ = ["StubBarsConnector"]


class StubBarsConnector(AlpacaBarsConnector):
    """The production connector minus the network. See module docs."""

    def spec(self) -> dict:
        spec = super().spec()
        spec["params"]["bars_per_symbol"] = {
            "notes": "TEST-ONLY: bars each symbol yields from start; "
                     "default 90.",
        }
        return spec

    def _knobs(self, config) -> dict:
        knobs = super()._knobs(
            {k: v for k, v in config.items() if k != "bars_per_symbol"})
        knobs["bars_per_symbol"] = config.get("bars_per_symbol", 90)
        return knobs

    def _credentials(self, knobs) -> tuple:
        return "stub-key", "stub-secret"

    def check(self, config) -> None:
        self._knobs(config)  # knob gate only — there is no vendor to ping

    def _fetch(self, knobs, start_dt, end_dt):
        """A smooth deterministic walk per symbol, one bar per minute
        from the CONFIG start — the window bounds filter, exactly as the
        vendor's server would."""
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
