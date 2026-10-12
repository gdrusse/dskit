"""``crypto_trading`` — a dskit child (copy of ``children/_skeleton``).

The child pattern (ADR-0021): dskit stays generic, THIS package holds the
tier-3 code — connectors, node kinds — and ``configs/`` holds the domain
as JSON. Stage B's nodes (feature rows, fees; the fair value, the scorer, the writer and the
manifests come from dskit) are referenced by import path
(``crypto_trading.spot_features:SpotFeatures``), not registered kinds: they name their data by
source and stream and are research-only. Import = registration: importing this package registers its node
kinds, which is exactly what ``--adapter crypto_trading`` on the pipeline
CLI does, so a document can name ``crypto_trading-*`` kinds with no flag
beyond that one import.
"""

from .connectors import SampleConnector
from .anchors import StrikeAnchors
from .decisions import DecisionRows
from .fees import FeeColumns
from .feature_selection import AblationSelect
from .kalshi_rows import CandleRows, FeeRows, MarketRows
from .market_state import MarketState
from .minute_features import MinuteFeatures
from .nodes import NODE_KINDS, EnrichRecords, SampleRecords
from .sizing import KalshiBinaryKellyMIO, KalshiKellyBacktest
from .spot_features import SpotFeatures

__all__ = [
    "AblationSelect",
    "CandleRows",
    "DecisionRows",
    "EnrichRecords",
    "FeeColumns",
    "FeeRows",
    "KalshiBinaryKellyMIO",
    "KalshiKellyBacktest",
    "MarketRows",
    "MarketState",
    "MinuteFeatures",
    "NODE_KINDS",
    "SampleConnector",
    "SampleRecords",
    "SpotFeatures",
    "StrikeAnchors",
]
