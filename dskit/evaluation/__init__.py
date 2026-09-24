"""dskit.evaluation — a centralized backtest evaluator (ADR-0183).

One append-only event log (schema ``dskit-eval-v1``) is the source of
truth for what a backtest did and why; :class:`BacktestReport` is a pure
function of it that writes ``events.jsonl``, ``summary.md``, a
self-contained ``report.html`` and two CSVs. P&L folds through
``dskit.production.accounting.WindowBook`` and the estimators live in
``dskit.pipeline.stats``, so nothing here is a second copy.

Tier 1: stdlib plus ``dskit.pipeline`` and ``dskit.production``; neither
of those may import this package. Importing it registers nothing.
"""

from dskit.evaluation.book import EvaluationBook, RoundTrip
from dskit.evaluation.criteria import Criterion, Scorecard, Verdict
from dskit.evaluation.events import SCHEMA, EvaluationError, Event, EventLog
from dskit.evaluation.nodes import EvaluationReport
from dskit.evaluation.report import BacktestReport
from dskit.evaluation.statistics import StatisticsTable

__all__ = [
    "SCHEMA",
    "BacktestReport",
    "Criterion",
    "EvaluationBook",
    "EvaluationError",
    "EvaluationReport",
    "Event",
    "EventLog",
    "RoundTrip",
    "Scorecard",
    "StatisticsTable",
    "Verdict",
]
