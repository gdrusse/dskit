# RE-ENTRY — stock_options

Updated: 2026-10-10 by Claude Fable 5.1. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/stock_options.md` (dskit repo).

## Now

- ADR-0212 bootstrap accepted (2026-10-01): a thin child with no strategy,
  optimizer, accounting or execution code. AMZN is the baseline, MSFT the
  liquidity replication. Neither is evidence of profitability.
- ADR-0213 offline archive conversion done: the AMZN conversion and the
  QQQ/AMZN JSON feature-gap interfaces passed two lenses (candidate `a0128bf3`,
  C0/M0/m0/N1). AMZN: 7,390 price rows and 65,372 daily option trade bars in the
  30-45 DTE window; 915 eligible CDF rows over 549 dates; the exact-42-DTE
  winner covers 90 dates; 146 candidate fields are not materialized (13,140
  feature-gap cells, `pipeline_runs/amzn-feature-availability/feature-gaps.jsonl`).
- Free trade bars carry no historical bid/ask, IV or open interest, so
  executable P&L is unsupported. Readers still use per-run onboarding roots,
  not the stock store (dskit `TODO.md`).
- No activity recorded since 2026-10-01.

## Next

Materialize the missing derivable feature families on the fixed cohort, inspect
source release/vintage clocks and rerun the gap audit before any feature
selection, PCA or model training. AMZN returns/RV/OHLC need preparation. Pull
further stock histories, then inventory the generic forecast, chain and
optimizer seams before any strategy run.

## Blocked / owner decisions

None recorded.

## Read first

- `docs/plans/README.md` (the conversion runbook)
- `../../docs/memos/2026-09-30-stock-options-source-and-starting-symbol.md`
- ADR-0212 and ADR-0213

## Do not

- Never import `children/index_options` or any sibling child.
- No paper or live orders. Never pair adjusted price levels with unadjusted strikes.
- Options never tune the first predictive model.
