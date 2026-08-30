"""Thin cohort wrappers over dskit's Alpaca and Schwab bar packs.

ADR-0046 owns transport, credentials, windows, normalization, and
checkpointing. This module only names the child's classes and keeps
one-minute bars as the declared source interval.
"""

from __future__ import annotations

from dskit.onboarding.libs import alpaca as _alpaca
from dskit.onboarding.libs import schwab as _schwab

__all__ = ["AlpacaBars", "SchwabBars"]


class AlpacaBars(_alpaca.AlpacaBarsConnector):
    """Alpaca SIP bars with this child's one-minute source policy.

    Parameters
    ----------
    None
        The connector is stateless; config supplies every setting.

    Examples
    --------
    Resolve knobs without touching Alpaca::

        connector = AlpacaBars()
        knobs = connector.resolve_knobs({
            "symbols": ["AAPL"],
            "start": "2016-01-01",
            "feed": "sip",
            "adjustment": "raw",
        })
        knobs["timeframe"]  # (1, 'Minute')
    """


class SchwabBars(_schwab.SchwabBarsConnector):
    """Schwab price-history bars with this child's one-minute source policy.

    Parameters
    ----------
    None
        The connector is stateless; config supplies every setting.

    Examples
    --------
    Discover the shared bar schema offline::

        connector = SchwabBars()
        streams = connector.discover({
            "symbols": ["AAPL"],
            "start": "2016-01-01",
        })
        streams[0]["stream"]  # 'bars'
    """
