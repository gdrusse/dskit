"""``intraday_poc`` — a dskit child (ADR-0021): the two-stock intraday
proof of concept.

AAPL and MSFT 1-minute bars (Alpaca), an LSTM next-bar-return model per
symbol through the declared torch seam, and a pick-exactly-one selector
on the PyomoSolve doorway — dskit stays generic, THIS package holds the
tier-3 code, and ``configs/`` holds the domain as JSON. Import =
registration: importing this package registers its node kinds, which is
exactly what ``--adapter intraday_poc`` on the pipeline CLI does.

Deliberately NOT imported here: ``models`` (torch at module top — the
declared seam resolves it inside ``run()``) and ``live`` (the forward
loop, a __main__ entry). Both stay run-path so plans stay cheap.
"""

from .connectors import AlpacaBarsConnector
from .nodes import (
    NODE_KINDS,
    BarsFromStore,
    ForecastRows,
    SelectOne,
    WindowRows,
)

__all__ = [
    "AlpacaBarsConnector",
    "BarsFromStore",
    "ForecastRows",
    "NODE_KINDS",
    "SelectOne",
    "WindowRows",
]
