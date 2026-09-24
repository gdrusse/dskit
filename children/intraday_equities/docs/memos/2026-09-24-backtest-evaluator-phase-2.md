# Backtest evaluator phase 2 (ADR-0183) — solves, inference diagnostics, ledger findings

## TL;DR

The evaluator report now shows three new things: how the optimizer solved
(status, gap, time, which constraints bound), what the forecasts turned
into (calibration by decile, hit rate per bucket, rank IC over time), and
the production ledger's pre-trade guard checks on every order. It ran end
to end through the real `run-replay-report.json` pipeline, but on a
**synthetic** tape: the real Alpaca store (`./ob`) was not available in the
build environment, so **the real replay has not been re-run** yet.

## What was built

Contract: ADR-0183 "Amendment (2026-09-24, phase 2 …)" plus its build note
(`docs/architecture/decision-log.md`). Branch
`claude/backtest-evaluator-phase-2-yl0qif`, based on phase 1 (unmerged)
merged with `origin/main` (ADR-0184).

- **Solves.** `libs/pyomo.SolveRecord` is recorded by `PyomoSolve.run` for
  every subclass: solver, status, termination, objective, bound, relative
  gap, seconds, sizes, and per-constraint binding rows (slack; dual only
  when a `dual` Suffix exists — a MIP has none). `PortfolioSelect.solves`,
  `EquityKellyMIO.evidence["solve"]`, `MioDecider.solves` and
  `DevelopmentSimulation.solves` expose it; `ReplayEvents` maps rows to
  `solve` events; `OptimizerSection` renders them.
- **Inference diagnostics.** `evaluation/diagnostics.ForecastDiagnostics`
  pairs each candidate's score with its `outcome` and reuses
  `pipeline.ordering` (Spearman per instant, HAC summary, Mincer-Zarnowitz
  slope). `pipeline/stats.quantile_edges`/`quantile_bin` became the one
  owner of equal-count buckets (`production.monitors` now imports them).
  `ReplayEvents` emits one outcome per candidate from `labeled` (`y_next`)
  at the symbol's next bar (`outcome_lead_bars` = `window.label_lead`,
  pinned). `InferenceSection` is the only reader of outcomes;
  `decisions.csv` is pinned identical with and without them.
- **Ledger findings.** `LedgerHistory.leg_findings` (shares `legs()`'s tick
  join). `EquityReplay`/`DevelopmentReplay` take `guards` and
  `keep_ledger`, and output `findings` + `ledger`. `order.findings` shows
  in the decision log (`guard_verdict`, findings text, a (guard, verdict)
  table). `production.report.Report` was not used: it needs a serve
  document + release and refolds `WindowBook` per tick.
- **Config.** `run-replay-report.json` wires `labeled`, `solves`,
  `findings`, `ledger`, and two observational limits (quantity ≤ 10,
  notional ≤ $5,000; a breach fails the run loudly). `keep_ledger` is
  **false** — see Limits.

## End-to-end run (synthetic tape)

Command (scratch copy of the child's configs; only the `alpaca` bar node
swapped for an AR(1) one-minute tape, φ = 0.15, σ = 6 bp, six names, RTH,
2025-06-02..2025-10-21; tracking removed):
`python -m dskit.pipeline run configs/run-synth-report.json --asof 2026-09-24 --adapter intraday_equities`
→ exit 0, document hash `4261e613…`, replay 1,099 s, report 3.8 s.

| reading | value |
|---|---|
| decisions / fills / round trips | 2,872 / 2,872 / 1,436 |
| solve events | 1 (`ok/optimal`, gap 5.9e-6, 0.30 s; `one_per_t` binding on 1,436 rows) |
| outcome events / scored pairs | 7,180 / 7,180 (0 unresolved) |
| hit rate all / chosen | 55.5% / 57.2% |
| mean rank IC per instant | +0.108 (HAC t +8.38, 1,436 instants) |
| calibration slope | +16.0 (t vs 1 = +11.8: forecasts ~16x too small) |
| guard findings | 2,872 orders, all `allow` (qty 1/10; notional $49.8–$322 vs $5,000) |
| net P&L | −$95.31 (gross +$25.32, fees $120.63) — FAIL |

These numbers describe a synthetic process, not the market. The run proves
the wiring and the rendering; it says nothing about the model.

## Reviews and tests

Two independent Sonnet lenses on the build: correctness 0 Critical/Major
(1 Minor — the size bound — pinned against `dec_qty`); tests/reuse 0
findings (1,186 targeted core tests, 119 child replay/evaluation, 164
nodes_capital, 42 simulation). A delta lens reviewed the post-visual-check
display fixes. `test_configs.py`: the same 5 failures as `origin/main`
(cohort restatement, mlflow experiment, tracking sinks, split-adjusted
store, p16 feature mask). No full suite.

## Limits

- **Real replay not re-run here** (no `./ob` store in the build container).
  Run it in WSL2 from `children/intraday_equities`:
  `python -m dskit.pipeline run configs/run-replay-report.json --asof <today> --adapter intraday_equities`
  (~10–20 min; report in `pipeline_runs/<run>/report/`).
- **Kept ledgers are huge**: 4.1 GB for five sessions (production's per-tick
  accounting `snapshot`, ~450 KB each and growing with fills; ~10x gzip).
  Findings are extracted before the scratch ledger is deleted, so the
  report is complete with `keep_ledger: false`. The snapshot growth is a
  production-side cost worth its own ADR.
- The replay's guards are observational; production refusals are still
  decided in child code before the ledger, so findings are all `allow`.
- One whole-window `PortfolioSelect` solve per run; per-tick MIO solves
  appear only when `DevelopmentSimulation.solves` is wired (not yet).

## Next bounded action

Run the real replay report in WSL2 and read the Inference section (does
the ridge's real rank IC survive?), then phase 3: wire
`DevelopmentSimulation` (per-tick MIO solves) into the evaluator, then
index_options/pmquant adapters.
