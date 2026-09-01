"""``intraday_equities`` — a dskit child (ADR-0021).

Thin tier-3 wrappers plus JSON configs. Import = registration: importing
this package registers the child kinds, which is what
``--adapter intraday_equities`` does. ``models`` and ``live`` stay off
the import surface so plans stay cheap.
"""

from .connectors import AlpacaBars, SchwabBars
from .nodes import (
    NODE_KINDS,
    BarsFromStore,
    FeedParity,
    HorizonScan,
    KeepSymbols,
    LookbackScan,
    PortfolioSelect,
    SessionFeatureRows,
    Universe,
    WindowRows,
)

__all__ = [
    "AlpacaBars",
    "BarsFromStore",
    "FeedParity",
    "HorizonScan",
    "KeepSymbols",
    "LookbackScan",
    "NODE_KINDS",
    "PortfolioSelect",
    "SchwabBars",
    "SessionFeatureRows",
    "Universe",
    "WindowRows",
]
