"""``crypto_trading`` — a dskit child (copy of ``children/_skeleton``).

The child pattern (ADR-0021): dskit stays generic, THIS package holds the
tier-3 code — connectors, node kinds — and ``configs/`` holds the domain
as JSON. Stage B's nodes (feature rows, fair value, fees, the kill test) are
referenced by import path (``crypto_trading.spot_features:SpotFeatures``), not
registered kinds: they name their data by source and stream and are research-only. Import = registration: importing this package registers its node
kinds, which is exactly what ``--adapter crypto_trading`` on the pipeline
CLI does, so a document can name ``crypto_trading-*`` kinds with no flag
beyond that one import.
"""

from .binance_vision import BinanceBvol, BinanceKlines, ZipCsvParquet
from .connectors import SampleConnector
from .day_series import ParquetDaySeries, StreamManifests
from .decisions import DecisionRows
from .fair_value import AveragedLognormal, FairValue
from .fees import FeeColumns, QuadraticFee
from .kalshi_rows import CandleRows, FeeRows, MarketRows
from .kill_test import KillTestScore
from .market_state import MarketState
from .nodes import NODE_KINDS, EnrichRecords, SampleRecords
from .spot_features import SpotFeatures

__all__ = [
    "AveragedLognormal",
    "BinanceBvol",
    "BinanceKlines",
    "CandleRows",
    "DecisionRows",
    "EnrichRecords",
    "FairValue",
    "FeeColumns",
    "FeeRows",
    "KillTestScore",
    "MarketRows",
    "MarketState",
    "NODE_KINDS",
    "ParquetDaySeries",
    "QuadraticFee",
    "SampleConnector",
    "SampleRecords",
    "SpotFeatures",
    "StreamManifests",
    "ZipCsvParquet",
]
