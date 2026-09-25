# Full-training execution evaluation

This is the `dskit.evaluation` report for the real-bar,
production-equivalent development simulation
`intraday-equities-development-simulation-2026-09-24-84740cdd`.

- Execution window: 2022-09-09 through 2025-10-16 (folds 2–19)
- Round trips: 457
- Gross P&L: +$2,949.40
- Fees: $1,463.24
- Net P&L: +$1,486.16
- External contributions: $16,580.00
- Verdict: `UNJUDGED` because no evaluator criteria were pre-registered
- Status: developmental post-selection evidence; `deployment_eligible=false`

Open [`report.html`](report.html) for the self-contained visual report or
[`summary.md`](summary.md) for the text version. `events.jsonl` is the canonical
evaluator input; `trades.csv` and `decisions.csv` are the complete tabular
exports.

The event log was derived from the preserved execution's fills, refusals and
daily cash-flow outputs. Those outputs did not persist forecast candidates,
market marks or per-tick optimizer solve records, so the report's inference and
optimizer sections correctly contain no such observations. Fills and cash flows
reconcile exactly to the original execution summary.

The exact MIO formulation used by the ADR-0185 retrain simulation is documented
in [`../../explanations/mio-optimizer-formulation.tex`](../../explanations/mio-optimizer-formulation.tex).
Its risk parameters are explicitly developmental placeholders, not deployment
or owner-approved production settings.
