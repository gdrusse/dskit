# Statistical arbitrage

DSKit child for structural equity networks and option relative-value research.
Phase one uses AAPL, AMZN, and GOOGL as tradable nodes; QQQ is a factor and
benchmark. MSFT/NVDA and Energy are separate later additions through JSON.

## What ships

- `configs/run-tech-structural.json` fits yearly precision and correlation
  graphs on prior QQQ residuals, and writes signed edges, weighted centrality,
  option surfaces and pair Z scores.
- `configs/run-tech-backtest.json` pins those outputs by SHA-256 and declares
  quote, fee, hedge, capital and calendar policy.
- `configs/run-tech-prediction-development.json` and
  `run-tech-prediction-validation.json` are the ADR-0272 prediction-only study:
  do date-t features (prior-60 Z, 5-session spread change, carried
  precision-network or correlation-gate edge weights) predict the 5- and
  20-session change of a pair's ATM-IV or skew spread? Expanding weekly Ridge
  refits on labels known before each block, scored per chronological fold
  against an expanding mean; the development document tunes one alpha on 2020
  (frozen in `configs/prediction-development-selection.json`), the validation
  document scores 2021–2025 once; `run-tech-prediction-validation-offset1.json`
  is the disclosed post-hoc next-session-label robustness run. No trade rule,
  P&L or edge claim.
- `configs/run-tech-structural-greeks.json` reads the same 444 Theta members
  once more for Theta's EOD Greeks, vendor IV, quotes, volume and open interest:
  30/90-DTE surfaces with term slope and OI aggregates, and vendor-Greek
  positioning per symbol-day. `run-tech-prediction-methods-{development,
  validation,validation-sameday}.json` are the ADR-0273 ten-method study on
  top of ADR-0272 (multi-scale Z, leg Z, smile shape, change profile, term
  structure, Greeks/OI positioning, realized vol, pooled Ridge, Lasso, boosting),
  knobs frozen in `configs/prediction-methods-selection.json`.
- `configs/run-tech-structural-universe.json` reads 30-DTE surfaces for the
  three names plus the twelve rule-selected cohort roots (ADR-0274);
  `run-tech-prediction-adr0274-{development,validation}.json` test the
  research note's three model changes: a vol-scaled label, a Kalman-OU spread
  state (`KalmanSpreadState`), and pooled / blended Ridge fit across the
  cohort's 105 pairs and scored on the three phase-one pairs; knobs frozen in
  `configs/prediction-adr0274-selection.json`.
- `statistical_arbitrage/{data,network,surface,backtest,report}.py` wraps
  generic DSKit readers, graph, pair spread and option ledger mechanisms.
- `docs/decisioning/` records scope; `docs/memos/` records evaluation.
- `tests/` covers source clocks, orders, replay, reports and plots.

## Replay

From the DSKit worktree with its WSL Python environment:

```bash
PYTHONPATH=children/statistical_arbitrage:. python -m statistical_arbitrage.backtest children/statistical_arbitrage/configs/run-tech-backtest.json
```

The runner verifies every structural input hash before reading market sources,
reads one frozen Theta stream for ATM/skew precision, correlation and all-pairs
books, then writes
`evaluation.json` and four PNGs to a new output directory. It refuses a
duplicate output identity. The results are retrospective EOD quote proxies,
not executable fill guarantees or evidence of an edge. GraphicalLassoCV has
fit iterations and chronological CV scores; it has no neural epochs. The
evaluation reports annual edge selection frequency from fitted windows.

## Prediction-only study

```bash
PYTHONPATH=children/statistical_arbitrage:. python -m dskit.pipeline run children/statistical_arbitrage/configs/run-tech-prediction-validation.json --asof <YYYY-MM-DD>
```

Every write node declares `overwrite: false`: a rerun into an existing output
directory refuses, and new paths change the document hash.

Both documents read the replay's pinned structural JSONL by SHA-256
(`RecordsFile`), rebuild the all-pairs spreads, label them with
`PairSpreadHorizonRows`, refit with `SklearnExpandingRefit` and score with
`ChronologicalForecastSkill`; rows, fits and skill tables land under
`/home/russell/data/statistical_arbitrage/prediction-20261020/<document>/`.
Memo: `docs/memos/2026-10-20-tech-prediction-only-study.md`.

## Layout

```text
children/statistical_arbitrage/
  AGENTS.md  CLAUDE.md  README.md  pyproject.toml  journal.json
  configs/    source, calendar, structural, replay and prediction-study JSON
  statistical_arbitrage/
    __init__.py  data.py  network.py  surface.py  backtest.py  report.py
  tests/      test_configs.py  test_nodes.py  test_prediction_configs.py
  docs/       RE-ENTRY.md  decisioning/  plans/  memos/
```

To extend the child, add symbols and policy to JSON, and keep reusable
mechanisms in `dskit/pipeline/libs/`. ADR-0270/0271 define the approved
phase-one scope and time rules.
