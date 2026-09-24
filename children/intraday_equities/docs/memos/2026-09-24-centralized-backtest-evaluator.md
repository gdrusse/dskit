# Centralized backtest evaluator (ADR-0183) — first real report

## TL;DR

`dskit.evaluation` turns any backtest into a standalone, readable report
from one append-only event log (`events.jsonl`, schema `dskit-eval-v1`):
`summary.md` (plain-English "What happened", verdict against pre-registered
criteria, statistics, census, provenance), a self-contained `report.html`
(inline SVG, no JS/CDN: trading P&L, drawdown, account equity with
deposits marked, trades on price with a hover "why" per marker, cash and
exposure, distributions, per-day table, decision log), `decisions.csv` and
`trades.csv`. First real run: intraday_equities development replay
2025-10-13..16 — verdict FAIL: +$6.00 gross, $186.46 fees, -$180.46 net over
1,290 round trips; mean chosen forecast 0.018 bp vs 4.43 bp round-trip fees.

## Delivered

- dskit (tier 1, new package, may import pipeline + production; neither
  imports it): `events.py` (schema v1, look-ahead + census checks),
  `book.py` (fills folded through `production.accounting.WindowBook` — no
  second P&L fold; TWR via `production.report.PerformanceCalculator`),
  `statistics.py`, `criteria.py`, `units.py`, `narrative.py`,
  `provenance.py`, `svg.py`, `sections.py`, `report.py`, `nodes.py`
  (`EvaluationReport`), `__main__.py` (`render`). `pipeline/stats.py` gains
  Sharpe/PSR/DSR/profit factor/payoff ratio; `kinds_report.csv_text` public.
- intraday_equities (tier 3): `PortfolioSelect.candidates` (every scored
  symbol, rank, chosen), replay `cash_flows`/`cash` outputs and
  `decision_ms`/exit `reason` links, `evaluation.ReplayEvents` mapper,
  `configs/run-replay-report.json`.

## Commands

```bash
cd children/intraday_equities
PYTHONPATH=<repo> python -m dskit.pipeline run configs/run-replay-report.json --asof <today> --adapter intraday_equities
python -m dskit.evaluation render <run>/report/events.jsonl --out <dir>   # re-render any past run
```

Output: `pipeline_runs/<run>/report/`. The replay takes ~10 minutes.

## Review and verification

Research (LEAN, pyfolio/quantstats, vectorbt, Alphalens, NautilusTrader,
Bailey-Lopez de Prado, Lo 2002) and a full dskit reuse sweep preceded the
ADR. Sonnet lenses: core (0 Critical/Major), child (0 Critical; the Major
was a missing ADR amendment, added), readability (0 Critical/Major;
narrative numbers checked independently against trades.csv). Tests:
tests/evaluation 163 passed (incl. purity: stdlib + dskit.pipeline +
dskit.production only), stats file 188, scanning suites 2,447 passed;
child test_evaluation 8, nodes/replay 85. Six child `test_configs.py`
failures pre-exist on the base (other configs). No full suite.

## Limits and next bounded action

Phase 2 (ADR-0183): `solve` events from `PyomoSolve` doorways (MIO status,
objective, binding constraints), inference diagnostics (calibration by
decile, rank IC), ledger-backed decision findings (retain the replay
ledger). Phase 3: index_options `CondorBacktest` and pmquant adapters,
`RunReport` convergence, optional matplotlib PNG backend. Re-rendering an
old log cannot recover provenance it never recorded.
