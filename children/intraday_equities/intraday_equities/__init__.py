"""``intraday_equities`` — a dskit child (ADR-0021).

Thin tier-3 wrappers plus JSON configs. Import = registration: importing
this package registers the child kinds, which is what
``--adapter intraday_equities`` does. ``models`` and ``live`` stay off
the import surface so plans stay cheap.

Importing this package ALSO registers the ``torch_ts`` zoo pack
(ADR-0041/0051), so documents may name ``torch-ts-train`` /
``torch-ts-predict`` and sweep ``arch`` across the architecture
registry. That import costs about 40ms and does NOT pull in torch —
the tier-2 pack names the library only inside a method — so plans
stay cheap exactly as before.
"""

from dskit.pipeline.libs.torch_ts import register as _register_torch_ts

from .connectors import AlpacaBars, SchwabBars
from .feature_cache import SessionFeatureCache
from .nodes import (
    NODE_KINDS,
    BarsFromStore,
    FeedParity,
    FoldFeatureStats,
    HorizonScan,
    KeepSymbols,
    LookbackScan,
    NoInformationScan,
    PortfolioSelect,
    SessionFeatureRows,
    Universe,
    WindowRows,
)

__all__ = [
    "AlpacaBars",
    "BarsFromStore",
    "FeedParity",
    "FoldFeatureStats",
    "HorizonScan",
    "KeepSymbols",
    "LookbackScan",
    "NoInformationScan",
    "NODE_KINDS",
    "SessionFeatureCache",
    "PortfolioSelect",
    "SchwabBars",
    "SessionFeatureRows",
    "Universe",
    "WindowRows",
]

_register_torch_ts()
