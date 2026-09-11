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
  Path and Current Work are human-owner-only. Each Path row has ID, label,
  purpose, relevant files, and `LOCKED` (`Y`/`N`). The generated README
  displays the full Path and latest 10 Actions; CSV history is append-only.
  Hooks record pipeline runs and onboarding verbs. `live.main` is wrapped in
  `production()`. Research writes `docs/research/<topic>/<YYYY-MM-DD>-<name>.md`
  through the CLI (no markdown in that root; `<date>-synthesis.md` is the
  task summary).
- **Standalone explanations live in `docs/explanations/`.** They explain
  child-specific methods or results without becoming decision records or
  journaled research.
- **Implementation plans live in `docs/plans/`.** They record approved scope,
  file ownership, test order, and explicit owner gates before code changes.
- **Durable handoffs live in `docs/memos/`.** Use them for completed-study
  evidence, implementation outcomes, and operational caveats.

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
- Every market-data run document tracks to one local MLflow experiment
  (`intraday_equities`). HPO maximizes `$select.metrics.rank_ic`.
  `configs/run-development-replay.json` is not a market-data run: no
  tracking sink, `deployment_eligible=false`, fill knobs in
  `configs/fill-policy.json`.
- The stopped asset-local P13 remains reproducible in
  `configs/run-p13-model-zoo.json`. ADR-0102's active replacement is
  `configs/run-p13-pooled-model-zoo.json`: pooled LightGBM and embedding Torch
  MLP with separate inner HPO and the same inventory approval barrier.
- P14's exploratory extension is `configs/run-p14-recurrent-fusion-zoo.json`:
  paired pooled LSTM/GRU late fusion over session-local one-minute OHLCV and an
  explicit side-feature projection. It is not locked or automatically promoted.
- P15's exploratory extension is `configs/run-p15-temporal-fusion-zoo.json`:
  one-hot Ridge, causal TCN, and small causal Transformer candidates share the
  exact P14 sequence rows and side features. It is not locked or auto-promoted.
- `configs/run-final-hpo.json` is the `final_hpo` phase: `BenchmarkSelect`
  names the winner (max mean path score over the three pinned compare
  artifacts) and `FinalistCandidate` builds THAT candidate's document. It
  restates no model name and refuses a selection it has no recipe for. One
  fit, not a walk — the phase declares no fold schedule. Constructed and
  validated (`2db8e95a…`); it has NOT been run.
- `configs/run-final-refit.json` is a non-executable contract. The driver has
  no immutable completed-run attestation and no content-derived identity for
  ten labelled input wires. Filling its pins cannot enable planning;
  `final_model.FinalRefit` fails closed until those upstream contracts exist.
- Telemetry carries no symbol and no lead (ADR-0117). `metrics.py` maps this
  project's field names onto the generic event catalogue and returns the
  per-lead decay profile as artifact DATA; the metric registry refuses either
  as a label. Nothing here sets a threshold — plan §11 item 7 is open.

## Machine knobs

- `INTRADAY_EQUITIES_FOLD_WORKERS` sets fold-process width (default 1).
  Environment, never a document: a graded knob would move the identity
  hash and orphan prior runs whenever it was tuned. Each fold keeps the
  full 17 GiB cap, so total memory scales with the width.

## Layout

```
intraday_equities/   # auth, connectors, nodes, nodes_capital, metrics, models, live, testing, replay
configs/             # universe + sources, suites/model-zoo, scan/action/HPO/train, fill-policy, development-replay
journal.json         # dskit.journal marker
docs/decisioning/    # actions.csv + path.csv; README generated
docs/research/       # topic folders; <date>-synthesis.md + dated notes
docs/explanations/   # standalone worked explanations
docs/plans/          # implementation plans: scope, file ownership, owner gates
docs/memos/          # durable implementation and operational handoffs
tests/               # conftest + connectors/nodes/configs/live
```

Keep this tree and README.md current when files change.
