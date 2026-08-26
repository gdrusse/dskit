# Capability-gap report — pmquant as a dskit child

> **Superseded in part (2026-08-26).** The "pmquant as a thin child" sketch below is
> superseded by `docs/children_design_proposals/pmquant.md`, whose provenance block
> lists the eight points it changes (kind naming, backend tags, dependencies, the
> schwab split, Tier-B capture, the cross-feed validators, one engine-parity residual,
> and two COVERED rows revisited as TODOs). The COVERED table and the classification
> method below stand.

**Verdict: pmquant already contains dskit — its engine is the parent fork and
was a strict superset; after the owner-ratified parity ports (ADR-0022…0026,
2026-08-25) the generic engine gap is CLOSED, and everything else in pmquant is
child material.** No pmquant-specific code belongs in dskit.

Evidence base: full-tree inventory 2026-08-25 (132k LOC package / 127k LOC tests).
pmquant has already performed the generic/domain split internally: `pmquant/pipeline`
is venue-purity-tested generic; `pipeline_kalshi` + `pipeline_schwab` are adapters —
the generic/domain split proven at scale (their sibling-package NAMING predates
ADR-0032 and does not survive migration).

## The engine relationship

`dskit/pipeline` is a rename-extraction of `pmquant/pipeline` (8 of 24 shared files
byte-identical after rename; zero kinds in dskit absent from pmquant). Capability
diff, pmquant-side only:

| pmquant engine capability | Status in dskit |
|---|---|
| `concat` / `join` / `derive` flow kinds | **PORTED** this session (ADR-0022) |
| `table-file` / `table-write` (`kinds_table.py`) | **PORTED** this session (ADR-0023) |
| split policies + event bounds (`split_policy.py`, `Node.event_bounds`, driver binding) | **PORTED** (ADR-0024, owner-ratified) |
| declared-model seam (`import_library_class`, `torch-train`/`torch-predict`/`transformers-fit`) + `trainlog.py` | **PORTED** (ADR-0025, owner-ratified; the driver stderr curve-streaming residual is ported too) |
| report renderer parity (CSV export, `max_rows`/truncation, table/ledger helpers) | **PORTED** (ADR-0026, owner-ratified — full module, boundary resolved via records.py) |
| mlflow tracking sink | stays adapter-side by design (`SINK_KINDS` seam is COVERED; the pack is child code) |

With the parity ports landed, `pipeline_kalshi`'s content can run on dskit's
engine with only an import rename — and per ADR-0032 it lands as modules of
`children/pmquant` (`nodes_*.py`, `backend.py`, …), never as a
`pipeline_<venue>` package: the package name does not survive migration.

## Classification by functional area

**COVERED** — the dskit mechanism exists; migration is a config/import change.

| pmquant area | dskit mechanism |
|---|---|
| `pipeline/` core (node, document, planner, driver, runner, conformance, metrics, stats, records, env, features, io, protocols, registry, resolve, testing, synthetic) | `dskit/pipeline` — same files, same grammar |
| `pipeline/libs/` 6 packs | `dskit/pipeline/libs` (minus the ADR-0025 declared classes) |
| `pipeline/kinds_table.py`, flow verbs | ported (ADR-0022/23) |
| `data/{acquire,registry,source_base,validate}.py` + state cursors (the dormant acquisition harness) | `dskit/onboarding` — Connector four-verb seam, `run_acquisition`, per-(source,stream,mode) cursors, declarative suites; onboarding is strictly stronger (WORM Merkle snapshots, certify, publish outbox) |
| `runspec/` (config → canonical hash → named artifacts) | document identity hash + `{name}-{asof}-{hash8}` run dirs |
| `hpo/` (enumerate→train→score→select) | `hpo-grid`, `optuna-search`, the `ctx.rerun` search seam |
| `verdict/bootstrap.py`, `core/calibration/fdr.py` (BH) | `stats.cluster_bootstrap_pvalue`, `stat_test` kind, `CORRECTIONS` |
| `core/atomic.py` | `assets.base.atomic_write_json`, onboarding `durable_write_*` |
| run/artifact cataloguing (`lab/store`, ad-hoc run dirs) | `dskit.assets` registry + `ingest_run` + lineage |
| time-varying fee lookup (pmquant's own lesson: it IS `derive`+`join`+a table) | `table-file` + `join` + `derive`, post-port |

**WRAPPER** — domain code; lives in the child, composing dskit seams.

| pmquant area | child wrapper it becomes |
|---|---|
| `pipeline_kalshi/` (10 `kalshi-*` kinds, backend tags, `fractional-kelly-mio`, mlflow sink) | already IS the child — nodes/backend/tracking modules |
| `pipeline_schwab/` | modules of the same child (`nodes_schwab.py`, …) — a venue split is a module, not a package (ADR-0032); a second child only if the projects genuinely separate |
| `core/markets`, `core/fees`, `core/venues`, `core/book/features`, `core/pathdyn`, `core/models`, screening domain parts | child domain modules the nodes wrap |
| `data/sources/*`, recorders, `snapshot_store`, `poly_ladder`, `predexon_*` | onboarding **Connector packs** in the child (REST/WSS pulls → WORM snapshots → suites replace the bespoke validate/report scripts) |
| `simulator/`, `portfolio/` (MIO), `arb/`, `ladder/` (transformer), `jobless/`, `music/`, `tsa/`, `scout/`, `lab/` domain layers | child engines behind `capital`/`train`/`signal` nodes (`PyomoSolve`, `TorchTrain` bases) |
| `scripts/` | child scripts + onboarding CLI invocations; fee books already sit in `configs/` as `table-file` inputs |

"Thin" describes the **seam**, not the volume: the domain engines (simulator, MIO,
ladder) stay big — they just live in the child, wrapped by nodes, configured by JSON.

**GAP** — genuinely generic, dskit-side. Implemented: ADR-0022/23 (above). Proposed:
ADR-0024/25/26. Flagged below the line (no ADR yet — say the word):

- **Calibration/stats pack** — beta recalibration, CORP isotonic reliability,
  LOEO/expanding cross-fit, Efron lfdr (`core/calibration/*`, `verdict/varpi.py`).
  Domain-free statistics; a `libs/`-family design job.
- **Scoring/distributions pack** — `PredictiveDistribution` + proper-scoring rules
  (`core/scoring.py`, `core/distributions.py`). Generic; large surface.
- **Onboarding backfill ergonomics** — guardrailed parallel job runner, advisory
  file locks, which-days-pulled fetch manifest (`data/acquire.py`, `locks.py`,
  `core/data/fetch_manifest.py`). Onboarding covers correctness; these add scale.
- **Job orchestrator** — CPU-parallel/GPU-serial scheduler (`lab/orchestrate/`).

## pmquant as a thin child — sketch

`children/pmquant/` per ADR-0021 (files one line each; today's `pipeline_kalshi` +
`configs/` map onto it nearly 1:1):

```
children/pmquant/
├── pyproject.toml            # depends on dskit[sklearn,torch,pyomo,optuna]; domain deps free
├── pmquant/
│   ├── __init__.py           # import = registration of every kind/backend/sink below
│   ├── backend.py            # kalshi + polymarket backend tags (BackendRegistry)
│   ├── nodes_data.py         # kalshi-predexon-source, kalshi-settlement, kalshi-ladder-panels
│   ├── nodes_model.py        # kalshi-fit-rows, kalshi-causal-beta, kalshi-declared-qhat, kalshi-signal-qhat, kalshi-market-implied
│   ├── nodes_capital.py      # kalshi-kelly-mio, kalshi-walk-forward-replay + fractional-kelly-mio optimizer
│   ├── tracking.py           # mlflow Tracker (SINK_KINDS pack)
│   ├── connectors.py         # predexon / kalshi REST / polymarket connectors (four-verb)
│   ├── fees.py               # the exact Kalshi fee formula — the one true domain constant
│   ├── ladder_model.py       # the rung-attention transformer as a TorchAdapter subclass
│   ├── mio.py, simulator.py, …  # the domain engines the capital/replay nodes wrap
├── configs/
│   ├── asset-model.json      # ladder-event / checkpoint / backtest-run kinds
│   ├── source-predexon.json, source-kalshi.json, source-poly.json
│   ├── suite-ladder.json     # the usable-event bar as declarative rules
│   ├── fees-kalshi.json, fees-poly.json   # already exist as table-file inputs
│   └── run-kalshi.json, run-poly.json, run-joint.json  # the 15/22-node documents — already exist
└── tests/                    # conformance over the kind registry + connector contracts
```

Stale-doc note for the owner: pmquant's root CLAUDE.md still claims the data layer
is shared from rl_stocks via `core/data/stores.py` — that file no longer exists and
D-147/D-148 made rl_stocks study-only. Worth fixing on the pmquant side.
