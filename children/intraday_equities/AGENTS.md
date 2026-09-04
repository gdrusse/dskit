Default answer: outcome first, max 5 lines. Expand only if I ask.

# AGENTS.md — intraday_equities

Agent orientation — see README.md for operator commands.

## The child rules (ADR-0021)

- **Never edit dskit from here.** Generic gaps graduate upstream
  (ADR-0046/0047 already did). Domain stays here.
- **The domain lives in `configs/`.** Cohort, cadence, costs, and
  feed-parity tolerances are document data.
- **Import = registration.** `--adapter intraday_equities` imports
  this package. Never pass `owned=True`.
- **A vendor knob is a `spec()` knob.** Symbols, start, feed,
  adjustment, overlap, and codecs are config.
- **Live reads the shipped documents.** It never restates lookback,
  horizon, or tradable names.
- **Paper only.** Real-money enablement is a separate owner decision
  and this package refuses it.
- **Decisioning is a journal (ADR-0056).** `journal.json` is the marker.
  Actions append `docs/decisioning/actions.csv`; README is generated.
  Path to production is owner `journal promote` only. Hooks record
  pipeline runs and onboarding verbs. `live.main` is wrapped in
  `production()`. Research writes `docs/research/`.
- **Standalone explanations live in `docs/explanations/`.** They explain
  child-specific methods or results without becoming decision records or
  journaled research.

## Invariants

- One-minute raw bars; coarser views are derived (`event-grid`).
- Cohort, holidays, scales, and the horizon go/no-go live in
  `configs/universe.json`. Widening the market set is a config edit
  (universe + both sources + both suites), never a code edit.
- Alpaca SIP backfill from 2016, `adjustment: raw`; Schwab live with
  overlap. Separate immutable sources.
- Action documents differ only in `name`/`notes`, `label_lead`, and
  `event-grid.period_ms`.
- Latest six months are the lockbox (`splits.test_end_ms`).
  Holdouts: H/L walk-forward through 2025-11-30; HPO Dec 2025–Feb 2026;
  untouched from 2026-03-01 (`docs/decisioning/hstar-go.md`, ADR-0058).
  Per-name H (one pooled tree); book collapse deferred (`docs/adhoc/deferred_decisions.md`
  at the repo root).
  Training framework: `docs/decisioning/framework.md`.
- Every run document tracks to one local MLflow experiment
  (`intraday_equities`). HPO maximizes `$select.metrics.rank_ic`.
  Fill rate / delay decay wait on a fill model.

## Layout

```
intraday_equities/   # auth, connectors, nodes, models, live, testing
configs/             # universe + sources, suites, scan/action/HPO/train
journal.json         # dskit.journal marker
docs/decisioning/    # actions.csv + path.csv; README generated
docs/research/       # research markdown
docs/explanations/   # standalone worked explanations
tests/               # conftest + connectors/nodes/configs/live
```

Keep this tree and README.md current when files change.