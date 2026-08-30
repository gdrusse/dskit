"""``intraday_poc`` — a dskit child (ADR-0021): the two-stock intraday
proof of concept.

AAPL and MSFT 1-minute bars (Alpaca), one zoo LSTM per symbol
(``torch-ts-train`` / ``arch: lstm``), and a pick-exactly-one selector
on the PyomoSolve doorway — dskit stays generic, THIS package holds the
tier-3 code, and ``configs/`` holds the domain as JSON. Import =
registration: importing this package registers its node kinds AND the
zoo pack the documents name, which is exactly what
``--adapter intraday_poc`` on the pipeline CLI does.

Deliberately NOT imported here: ``models`` (the empty bespoke-net seam
— add a class there only when the architecture is genuinely invented)
and ``live`` (the forward loop, a __main__ entry). Both stay out of the
import surface so plans stay cheap.
"""

from dskit.pipeline.libs.torch_ts import register as _register_torch_ts

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

_register_torch_ts()
