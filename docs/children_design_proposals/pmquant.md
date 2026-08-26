# Design proposal — pmquant as a dskit child

**Status: PROPOSED** — awaiting owner ratification. No code exists; this document is
the plan of record for building `children/pmquant/`.
Date: 2026-08-26. Evidence base: full-tree maps of `~/pmquant` (branch `main`, HEAD
`d12526e`) and `~/dskit` (`main`, `e0d028d`), read 2026-08-25; four adversarial
five-lens review rounds against both repos.

**Provenance.** This document supersedes the child sketch in
`docs/architecture/child-gap-pmquant.md` (§ "pmquant as a thin child") on eight
points: the `kalshi-*` kind names become `pmquant-*` with venue as a param (§2.1); the
`backend.py` module and BackendRegistry venue tags are not carried (§2.1); the
`dskit[sklearn,torch,…]` extras become the hard-dep table in §2.2; `pipeline_schwab`
is excluded rather than folded in (§12 — the gap report itself permits "a second child
only if the projects genuinely separate"; they do); the bulk book streams land outside
the WORM snapshot chain (Tier B, §3.1); the bespoke cross-feed validators do not
become suites (§4.4/§12); the gap report's engine-parity verdict gains one residual
(the unported `stat_test` evidence block, TODO-1); and two of its COVERED rows are
revisited by TODOs — the bootstrap/BH row (TODOs 2/3) and the time-varying-fee row
(TODO-11). The COVERED rows §13 does not revisit stand unchanged. This file lives at
`docs/children_design_proposals/pmquant.md` — directory name as the owner specified;
the repo-layout tree in `CLAUDE.md` gains the directory as part of this proposal
landing.

**Verdict first: pmquant re-lands as one child package — `children/pmquant/` — built
entirely on dskit's three seams. Zero dskit edits are required to start; thirteen
genuinely generic gaps are flagged as TODOs (§13), each with a named dskit home and
interim child-side handling. No pmquant code is reused as-is: every capability is
re-expressed as a connector, a node kind, an asset-model kind, or a JSON config, per
ADR-0021/0032.**

The parent repo's engine (`pmquant/pipeline/`) is already dskit (ADR-0022…0026 closed
the gap; the one residual runs pmquant→dskit and is TODO-1). Everything else in
pmquant is domain material and lands here as child code and configs. The
`pipeline_kalshi` / `pipeline_schwab` sibling packages are the retired form
(ADR-0032) and do not migrate as packages; their content maps onto the kinds in §6.

---

## 1. What we are doing with pmquant — the program this child implements

The mission (docs/01, D-040): **find Kalshi and Polymarket ladder markets whose traded
price is wrong (`p ≠ q`) and take the other side.** Detection is per-market
calibration against the market's own settled outcomes — never out-forecasting
consensus. A predictive model *detects* mispricing; the fractional-Kelly MIO
*harvests* a detected edge and never creates one. Strategy #1 (marginal
miscalibration) is primary — it has both a model-free lens (the calibration sweep,
§6.4) and the model lens (the ladder-q̂ transformer); strategy #2 (consistency
arbitrage) is secondary; external-data forecasting is dormant.

The end-to-end process (docs/23), which this child must reproduce stage for stage:

| # | Stage | What it does |
|---|---|---|
| 0 | Capture | Record full bid+ask ladder books at 21 lead times per event: Kalshi (Predexon L2 history + live REST recorders), Polymarket (pmxt archive backfill + live REST/WSS recorders), plus settlements (including the Predexon-native archive that recovers what Kalshi hard-deletes), strikes, candles, and per-series fee schedules |
| 1 | Ladder tensor | Point-in-time ladder panels: `T=21` leads × `C` rungs × 2 sides × `P` price bins (P=99 Kalshi cents, P=999 Polymarket milli-ticks); Kalshi books mirror (a YES ask *is* a NO bid at `1−a`), Polymarket books do not |
| 2 | Inventory / eligibility | Usable-event counts per series; a series enters the claims family iff ≥ 50 settled usable events closing strictly before the training cut T1 (D-137: eligibility is a data fact, no privileged subset, ever). D-138 additionally requires ≥ `min_testable_events` ask-present events to *test* — ask-presence is unknowable at resolve time, so the parent defers that half to verdict time and this child deliberately inherits the deferral (§6.4). The family spans **both venues jointly** (docs/20's N ≈ 104) |
| 3 | Splits / rolls | Frozen cuts `train < T1 ≤ val < T1c ≤ cal < T2 ≤ test`; first-observed-lead purge (relabel, never delete); pinned cuts never move; weekly walk-forward store versions v1, v2, … |
| 4 | Model | The pooled ladder-q̂ transformer (rung attention, causal over leads, settlement law carried by construction in the head), 5-seed ensemble, cross Venn–Abers calibration fitted on cal and applied to test; frozen recipe `d133-incumbent` — **supervised-from-scratch, single-stage** (E4 showed pretext pretraining does not help; E3 showed LP-FT never beat scratch on test; D-133 froze scratch — the parent's experiment-era staged trainer is deliberately not ported, §12) |
| 5 | Fees | Venue-dispatched exact fee models: Kalshi per-order ceil-to-cent with the 9-dp float guard and the 1¢ floor; Polymarket nearest-1e-5 half-up with a relative tie tolerance and **no** floor (I-217). Per-series dated rate spans keyed on the market's own close instant, threaded and never defaulted; rates applied per-source, then concat (D-153) |
| 6 | Books / fills / replay | `walk_book` best-first taker walk over recorded executable ladders; strict-PIT epoch replay with cumulative-premium budget cap, no-trade band, censoring disclosure |
| 7 | Deploy gate (level 1, D-138) | Per market: paired log-loss of the 5-seed ensemble vs **N0** (the raw stated YES ask), all 21 leads, event-clustered one-sided studentized bootstrap-t (B=10,000, seed 0, add-one p), BH across the as-of-T1 family, ≥ 50 testable events or no test — **no test, no deployment**; untested family members are reported coverage-limited, never dropped |
| 8 | Banking (docs/22) | Test-window predictions banked write-once across weekly rolls; a market is tested when its pool crosses `N₀·b^{j−1}`; weighted BH with raw weights; look records write-once with monotone watermarks; a standing PASS persists between looks (R6); a recipe re-freeze hard-resets the pool |
| 9 | Allocation | The fractional-Kelly MIO: exact-log Kelly over a joint settlement ScenarioSet, integer lots against recorded books, exact fees, deterministic HiGHS; the default-OFF robustness policy (CI-robust entry, CVaR tail, worst-case-Σ); v2 adds the frozen-ϖ capital-weighted error budget (C9 rows, joint allocator only) and the out-of-program `q_hold` trigger |
| 10 | Orders | **Deferred — do not build** (D-142) |

Program state that the migration must respect: the ladder-q̂ model is validated (two
independent windows, E5/E7a and E8); the verdict harness, banking, MIO v2 rows, and ϖ
pipeline are built but never exercised on real data; every pipeline exit-0 to date is
fixture data; and the parent's single blocking step before real runs is the
evaluator/reporting surface (I-232) — carried here as a P2 acceptance gate (§11).
docs/22 must flip PROPOSED → REGISTERED by dated owner ruling before the first
forward pooled verdict.

---

## 2. The shape of the child

Per ADR-0021 the child incubates at `children/pmquant/`, laid out exactly as its
future standalone repo, position-independent, graduating unchanged. Per ADR-0032 the
child is the whole adapter: nodes, connectors, sinks, and the asset model all live
here; a venue split is a **module**, never a package.

```
children/pmquant/
├── README.md                     # what the child does + the one-command runs (incl. the
│                                 #   catalog runbook and the weekly-roll chain, §5/§7)
├── CLAUDE.md                     # agent orientation: module map, invariants, gotchas
├── pyproject.toml                # name "pmquant"; deps per §2.2
├── .env.example                  # PREDEXON_API_KEY=, HF_TOKEN= — env-var NAMES only;
│                                 #   connectors refuse by name pointing here
├── notebooks/                    # authored companion notebooks (never generated):
│   ├── 01-data-inventory.ipynb   #   P1 · 02: P2 · 03: P3 · 04: P4 · 05: P5 (§11)
│   ├── 02-backtest-replay.ipynb
│   ├── 03-ladder-qhat.ipynb
│   ├── 04-verdict-banking.ipynb
│   └── 05-roll-varpi.ipynb
├── pmquant/
│   ├── __init__.py               # import = registration of every kind and sink below
│   ├── connectors_predexon.py    # Predexon L2 archive + settlement-archive connector
│   ├── connectors_kalshi.py      # Kalshi REST: markets/candles/fee_schedules, quotes,
│   │                             #   21-lead recorder
│   ├── connectors_polymarket.py  # pmxt backfill + Gamma/CLOB live recorders + fee schedules
│   ├── recorder.py               # python -m pmquant.recorder — capture entry point: flock
│   │                             #   single-flight, LeadGrid due-period declaration,
│   │                             #   CoverageLedger mark/missing, Tier-B landing + digest
│   │                             #   chains + `verify`, cursor persistence via onboarding
│   │                             #   save_state/load_state, the roll-pull subcommand (§4.3, §7)
│   ├── catalog.py                # python -m pmquant.catalog register-run <run_dir> |
│   │                             #   register-archive <store_path> --stream --origin —
│   │                             #   derives payloads/refs from artifacts and registers
│   │                             #   them (file-based; §5)
│   ├── roll.py                   # python -m pmquant.roll prepare — writes the week's roll
│   │                             #   documents from configs/templates/ into
│   │                             #   configs/rolls/<roll_id>/; `hpo prepare` writes one
│   │                             #   candidate document per hpo-grid.json cell into
│   │                             #   configs/hpo/<candidate_id>/ (§7)
│   ├── nodes_data.py             # 15 kinds (§6.1)
│   ├── nodes_model.py            # 10 kinds (§6.2)
│   ├── nodes_verdict.py          # 8 kinds (§6.4)
│   ├── nodes_capital.py          # 5 kinds (§6.5)
│   ├── models.py                 # LadderQhatModule + LadderPanelAdapter (torch at top —
│   │                             #   the one sanctioned exception; never imported by
│   │                             #   __init__/nodes/connectors, but the declared-ADAPTER
│   │                             #   seam does import it at PLAN time, so ladder document
│   │                             #   plans are not torch-free — §2.2 makes torch a hard dep)
│   ├── ladder/                   # the ladder data engine (subpackage; §8.1)
│   │   ├── __init__.py
│   │   ├── protocols.py          #   SettlementLaw / LadderType vocabularies, LeadGrid, MarketVocab
│   │   ├── store.py              #   SparseParquetStore; frozen store versions; meta.json pinning
│   │   ├── manifest.py           #   event manifest; law_ok / monotonicity structural checks
│   │   ├── splits.py             #   assign_splits, first-observed-lead purge, as-of eligibility
│   │   ├── panels.py             #   EventPanel tensorization; TokenFeaturizer; token datasets
│   │   └── inventory.py          #   usable / 2sd / trd layers; autodetect_series
│   ├── books.py                  # THE book stack (one, not two): BookSource protocol +
│   │                             #   PredexonPITSource / QuotesLeadsSource / CandlesDegradedSource
│   │                             #   + InMemoryBookSource, DecisionEpochRecord, walk_book,
│   │                             #   contract_inputs_from_book, asks_from_bids, CrossedBookError,
│   │                             #   CausalCellCI + build_cell_edge_pool +
│   │                             #   point_gate_side_of
│   ├── fees.py                   # kalshi_trading_fee / poly_trading_fee /
│   │                             #   trading_fee_for_series venue dispatch, fill_cost, FeeBook,
│   │                             #   fee_rates_for (§8.3)
│   ├── implied.py                # yes_interval (the one home of strike semantics),
│   │                             #   implied_distribution, MEE/threshold coherence checks
│   ├── mio.py                    # the MIO engine: v1 program + C9/ϖ rows + q_hold + the
│   │                             #   default-OFF RobustnessConfig (CI-robust entry, CVaR tail,
│   │                             #   worst-case-Σ) + the allocation explainer (§8.4)
│   ├── scenarios.py              # rank-dependence fit over settled history, Iman–Conover /
│   │                             #   checkerboard-IPF scenario construction, ScenarioSet,
│   │                             #   Σ=I ablation (§8.4)
│   ├── simulator.py              # run_simulation epoch loop, epoch schedule, capital state,
│   │                             #   Coverage counters, ContributionSchedule
│   ├── verdict.py                # D-138 statistic, studentized cluster bootstrap-t,
│   │                             #   BH/weighted-BH, look schedule + look-record persistence,
│   │                             #   Efron-lfdr ϖ fit, event-clustered boot_slope (fail-loud)
│   ├── calibration.py            # BetaCalibrator (N1 context), cross Venn–Abers (CVAP),
│   │                             #   logloss/net_entries eval helpers + CORP isotonic
│   │                             #   reliability (behind the calibration sweep)
│   ├── tracking.py               # MlflowTracker — the "mlflow" SINK_KINDS pack
│   └── testing.py                # stub connectors + synthetic fixtures (ship in-package)
├── configs/
│   ├── asset-model.json          # catalog kinds (§5)
│   ├── source-predexon.json
│   ├── source-kalshi-markets.json
│   ├── source-kalshi-quotes.json
│   ├── source-kalshi-leads.json
│   ├── source-poly-pmxt.json
│   ├── source-poly-live.json
│   ├── suite-kalshi-markets.json # one suite per Tier-A stream family (§4.4)
│   ├── suite-kalshi-candles.json
│   ├── suite-kalshi-quotes.json
│   ├── suite-kalshi-leads.json
│   ├── suite-kalshi-fees.json
│   ├── suite-predexon-settlements.json
│   ├── suite-poly-settlements.json
│   ├── suite-poly-fees.json
│   ├── fee-book-kalshi.json      # imported curated dated fee tables (table-file inputs,
│   ├── fee-book-polymarket.json  #   sha256-pinned; extended forward by the fee-book documents)
│   ├── hpo-grid.json             # the pre-declared HPO candidate set (§7)
│   ├── run-markets-build-kalshi.json
│   ├── run-markets-build-poly.json
│   ├── run-pit-build.json
│   ├── run-fee-book-kalshi.json
│   ├── run-fee-book-polymarket.json
│   ├── run-inventory.json
│   ├── run-calibration-sweep.json
│   ├── run-backtest-kalshi.json
│   ├── run-backtest-poly.json
│   ├── run-backtest-joint.json
│   ├── run-ladder-train.json
│   ├── run-verdict-pooled.json
│   ├── run-varpi-shadow.json
│   ├── templates/                # roll/HPO templates (checked in; §7):
│   │   ├── run-poly-condense.tmpl.json
│   │   ├── run-roll.tmpl.json
│   │   ├── run-ladder-train-roll.tmpl.json
│   │   ├── run-verdict-roll.tmpl.json
│   │   └── run-hpo-candidate.tmpl.json
│   ├── rolls/                    # generated per-roll documents, committed as PIT evidence:
│   │   └── <roll_id>/run-*.json  #   written by `pmquant.roll prepare` (§7)
│   └── hpo/                      # generated HPO candidate documents, committed as the
│       └── <candidate_id>/       #   study's PIT evidence (§7)
│           run-hpo-candidate.json
└── tests/                        # §10; conftest.py sys.path bootstrap as pinned by the skeleton
```

### 2.1 Naming and registration rules (all engine-enforced or convention-pinned)

- **Kind prefix is the package name verbatim:** every registered kind is
  `pmquant-<name>` (the `intraday_poc-` precedent). Kind names match
  `^[a-z][a-z0-9_-]*$`; node *keys* in documents match `^[a-z_][a-z0-9_]*$` (no
  hyphens).
- **Name fields follow the children's conventions:** the asset model is
  `pmquant-model`; every run document's `name` is `pmquant-<purpose>` — hash-material
  identity pinning the run-dir series, while the file name is not; suite `name`s are
  bare domain slugs (`kalshi-markets-basic`, `lead-books-basic`, …). `configs/` stays
  flat for the shipped documents; three sanctioned subdirectories exist —
  `configs/templates/` (checked-in roll/HPO templates), `configs/rolls/<roll_id>/`
  (generated per-roll documents), and `configs/hpo/<candidate_id>/` (generated HPO
  candidate documents) — the two generated trees committed as the roll's and the
  study's PIT evidence. The fee books are `table-file` *inputs* and sit flat as
  `fee-book-<venue>.json`.
- **Venue is a param, never a kind name.** `pmquant-ladder-source` takes
  `venue: "kalshi" | "polymarket"`; a third venue is a new accepted value plus rows in
  the fee book — never new Python (D-137/D-152 corollary).
- **Import = registration.** `pmquant/__init__.py` imports the four `nodes_*` modules
  (each ends with its `NODE_KINDS` table + `register_node_kind` loop, `owned` never
  set) and registers the `"mlflow"` sink. `--adapter pmquant` is exactly this import.
  The stage-list `BackendRegistry` is **not** used. Of the parent's `backend.py`, only
  the stage-list `KalshiBackend` class is retired; its load-bearing content is
  re-homed: `MarketImpliedSignal` and the envelope mapper → `nodes_model.py` /
  `nodes_data.py`; the shared capital param validator, the knob tables, and
  `resolve_event_cap` / `refuse_over_budget` → `mio.py`; `fee_rates_for` → `fees.py`;
  the `contributions` schedule validation → `simulator.py`.
- **Default-deny params everywhere:** each node class carries `_PARAMS`, a
  `validate_params` classmethod returning a problems list, and the child-local
  `_reject_unknown` idiom. Every declared knob must be read by code (conformance
  enforces reachability).
- **Heavy imports live inside `run()` / `read()`**, with the single sanctioned
  exception `models.py`. **No module may read environment variables at import time.**
- **The engine's `split` knob is reserved.** The child's four-block store vocabulary
  (`train/val/cal/test` at cuts `T1/T1c/T2`) is a *different* axis, so panel-block
  selection params are named **`block`**, never `split`; only nodes that genuinely
  speak the engine's split language use the reserved name.

### 2.2 `pyproject.toml`

`name = "pmquant"`, `requires-python = ">=3.11"`. Following the intraday_poc precedent
(hard deps + `find_spec` gates in tests, no extras):

- **Hard:** `dskit`, `numpy`, `scipy`, `pandas`, `pyarrow`, `requests`, `torch`,
  `highspy`, `scikit-learn`, `duckdb`, `huggingface_hub`. (`scipy` because the
  deliberately torch-free verdict/ϖ path needs it. No `optuna`: HPO is a pre-declared
  candidate grid of generated documents, §7 — the parent's own grid-not-TPE rule.)
- **Lazy-optional** (imported inside `run()`, refusal-with-instructions, never in
  `pyproject`): `mlflow` (tracking sink), `venn_abers` (CVAP), `pyscipopt`
  (mean-variance secondary objective only). The verdict machinery in `verdict.py`
  stays torch-free and consumes prediction parquets only.

Incubation note: the child's import name collides with the parent repo's `pmquant`
package. The child incubates in its own venv; every §11 parent-comparison is made from
artifact files, never by importing both packages in one interpreter.

---

## 3. Storage architecture — what lives where

### 3.1 Two capture tiers, one honest size note

dskit onboarding stores each acquired record **twice as uncompressed JSON** (bronze
`payload/` + normalized `observations/`). The parent's data root is dominated by book
archives stored gzipped (~48× compression) or as zstd parquet (~5× vs ndjson); routed
through onboarding as-is, the gz-class archives would grow **~96×** and the
parquet-class hour parts **~10×**. The design therefore splits capture into two
declared tiers:

- **Tier A — governed streams, full onboarding chain** (acquire → WORM snapshot →
  suite → certify → publish → `sync-published`): Kalshi `markets`, `candles`,
  `quotes`, `lead_books`, `fee_schedules`; Predexon `settlements_archive`; Polymarket
  `settlements`, `fee_schedules`. These are the label / metadata / fee truth — the
  classes where the WORM+evidence chain buys the most, at modest volume. (`candles`
  and `quotes` have no §7 consumer yet — they are captured-for-future because live
  capture is unrecoverable later, feeding the `CandlesDegradedSource` /
  `QuotesLeadsSource` replay backends when replay diagnostics want them; `lead_books`
  is the forward 21-lead capture whose PIT build is §6.1's second `pit-build` wiring.)
- **Tier B — bulk book archives, connector-direct landing**: `l2_snapshots`
  (Predexon), `l2_hours` (pmxt), `books_fast` / `books_slow` / `books_wss` /
  `discovery` (Polymarket live). The same four-verb connectors produce the records,
  but `python -m pmquant.recorder` invokes `read()` directly and lands rows in the
  parent-proven compressed stores (gz ndjson archive; zstd hour-part parquets, with
  fast/slow/wss parts in separate dirs pre-merged into a distinct merged dir before
  condensation — the stems collide). Compensating controls, each with a named
  implementer: rows are digest-chained at write by `recorder.py`;
  `recorder.py verify` re-checks the chains (the `onboarding verify` analogue); every
  landed store is registered as an `archive` asset by
  `python -m pmquant.catalog register-archive` (§5); and `pmquant-archive-rows`
  refuses a store whose digest chain does not verify (`require_digest`, default
  true). Tier-B cursors persist through onboarding's public `save_state`/`load_state`
  per (source, stream, mode) — ADR-0014 honored. This is a disclosed deviation from
  P2's immutable-snapshot ownership rule, driven by the size math; **TODO-8** is the
  generic fix, and **§14 Q8 asks the owner to ratify Tier B or block it on TODO-8.**

**Corpus migration:** the existing `~/pmquant_data` history is imported once, as-is,
in its proven formats — including `lab_runs/predexon_settlement/` and
`kalshi/_recovered/` (**raw acquisition data**: the exchange-native settlement archive
holding the only surviving copy of ~409 events' labels), the curated fee books, and
the settlement/strike stores. Every imported store is registered as an `archive` asset
via `catalog.py register-archive` with `origin: "parent-import"`. Nothing replays
through onboarding. Imported stores are readable on the build path through
`pmquant-archive-rows` (whose `format` covers `parquet`/`json` alongside the
compressed book formats): the settlement builds take the imported store as their
`base` and extend forward; the frozen ladder stores v1–v3 are imported for reference
but the child **rebuilds** any store version it trains on from the imported PIT data
at the pinned cuts (`pmquant-store-build` is deterministic), so P3's acceptance
exercises the child's own build path.

### 3.2 The placement table

| Parent artifact | Class | New home |
|---|---|---|
| Raw Predexon snapshot archive (`history/predexon_l2/`) | Tier B raw | connector-direct gz ndjson store; `archive` asset |
| Predexon settlement archive (`lab_runs/predexon_settlement/`, `kalshi/_recovered/`) | Tier A raw (imported + forward stream `settlements_archive`) | onboarding snapshots going forward; parent corpus imported as `archive` assets; the additive-only, decoder-proofed recovery discipline lives in `pmquant-markets-build` (§6.1) |
| Kalshi `markets` / `candles` parquets (incl. `markets_leads/`, merged on import) | Tier A raw | onboarding snapshots, streams `markets` / `candles` |
| Kalshi `quotes` / `quotes_leads` recorder output | Tier A raw (live) | onboarding snapshots, streams `quotes` / `lead_books` |
| Fee schedules (Kalshi `get_series`; Polymarket Gamma fee fields) | Tier A raw | onboarding snapshots, stream `fee_schedules` per venue connector. Kalshi rate *history* is not acquirable (current-state API), so the parent's curated dated books are imported and the fee-book documents extend them **forward**; pre-observation windows stay curated or declared UNPRICEABLE |
| pmxt hour parts, poly live hrparts (fast/slow/wss) | Tier B raw | connector-direct zstd hour-part stores; `archive` assets |
| PIT ladder ledgers (`predexon_l2_pit/*.ndjson`, `poly_l2/*.parquet`) | derived build | `run-pit-build.json` / `run-poly-condense.json` outputs; build runs `ingest-run`ed |
| Settlement/strike stores (Kalshi `kalshi/markets/*.parquet`; the Polymarket rows the parent's poly build wrote into the same store) | derived build | `run-markets-build-kalshi.json` / `run-markets-build-poly.json` outputs (`pmquant-markets-build`/`-write`), extending the imported base |
| Frozen ladder stores `processed/ladder_qhat/v{N}` | derived build | `run-roll` store-build/store-write outputs; `ladder-store` assets (v1–v3 also imported as reference `archive` assets) |
| Model checkpoints + vocab | model artifact | run-dir `artifacts/<node>/` (declared-torch sidecar); `checkpoint` assets; the 5-seed set an `ensemble` asset |
| Prediction parquets | model output | run-dir artifacts; `prediction-set` assets |
| `verdict.json`, `seed_panel.parquet` | gate evidence | run-dir artifacts; `verdict` assets |
| `processed/verdict_pool/<freeze>/roll=<id>/` + the `looks/` trail | banked evidence | the pool root: prediction partitions (`pmquant-pool-write`) and write-once look records (`pmquant-look-write`), both under the freeze's single-writer lock; `pool-roll` assets |
| HPO freeze | recipe identity | `freeze` asset (payload incl. `n_seeds`/`sd`/`se`); `pool-roll` refs `freeze`, so a re-freeze visibly orphans the old pool once the catalog step runs |
| `lab_runs/{calibration,replay_gate}`, ad-hoc run dirs, `RunLedger` | run cataloguing | dskit run dirs + `ingest-run` (RunLedger retired — landmine L11) |
| `leads_schedule.parquet`, `leads_basket.txt`, first-seen / idempotency ledgers | recorder state | connector cursor state + the onboarding CoverageLedger via `pmquant.recorder` (§4.3) |
| `history/predexon_trades/`, `lead_panel/`, acquire-harness vintage stores | not on the money path | §12 dispositions |
| MLflow (`mlflow.db`, experiment `ladder_qhat`) | metrics tracking | the child's `"mlflow"` sink, per-document `tracking.sinks` — canonical for ladder-q̂ training documents only |

Two standing parent rules become structural: **store writers are append/merge-only**,
and **pinned cuts never move in place** (the cuts live in the `ladder-store` asset
payload, whose version_id is a content hash — moving cuts mints a new asset).

---

## 4. Acquisition — the onboarding seam

Six connectors, each a four-verb `Connector`, config-driven, secrets as env-var
*names* only (refusals point at `.env.example`). Streams and modes:

| Connector (module) | Streams | Modes | What `read` emits |
|---|---|---|---|
| `PredexonL2Connector` (`connectors_predexon.py`) | `l2_snapshots` (Tier B), `settlements_archive` (Tier A) | `backfill` | one RECORD per book snapshot per contract (verbatim full-depth `yes_bids`/`yes_asks` + `timestamp`/`sequence`; dedup key `(timestamp, sequence)`); exchange-native settlement rows for closed markets |
| `KalshiMarketsConnector` (`connectors_kalshi.py`) | `markets`, `candles`, `fee_schedules` | `backfill`, `live` | settlement/strike rows (14-col schema); hourly candles; per-series `fee_type` / `fee_multiplier` from `get_series` (I-001) |
| `KalshiQuotesConnector` (`connectors_kalshi.py`) | `quotes` | `live` | one RECORD per (contract, poll): best levels + full `yes_book`/`no_book` JSON, status, strike fields |
| `KalshiLeadsConnector` (`connectors_kalshi.py`) | `lead_books` | `live` | one RECORD per (contract, lead) capture: `lead_frac`, lead/capture timestamps, signed capture lag, full books, strike fields, flattened quality facts (§4.4). The settle checkpoint is `data.capture_kind ∈ {"lead","settle"}` — never the envelope `kind`, never a NaN sentinel (I-124 retired) |
| `PolymarketPmxtConnector` (`connectors_polymarket.py`) | `l2_hours` (Tier B) | `backfill` | pmxt rows filtered to the token universe named by `tokens_path` (a file the discovery stream maintains — never hand-edited), pulled whole-file from the HF archive (Xet-safe; DuckDB-filtered locally; fresh parts dir per roll; `token_env` default `HF_TOKEN`, optional) |
| `PolymarketLiveConnector` (`connectors_polymarket.py`) | `books_fast`, `books_slow`, `books_wss`, `discovery` (Tier B), `settlements`, `fee_schedules` (Tier A) | `live` | pmxt-shaped book rows (+ `asof_ts_ms`, `book_hash`); the `discovery` stream emits routed (event → series store, token) rows maintaining the token universe; settled-event rows and fee fields from Gamma, resolved through the on-disk ticker→Gamma bridge |

Config knobs re-express the parent's operational constants as declared JSON: Predexon
`api_key_env`, `min_interval_s: 1.0` (the 1 req/s org bucket → the `recorder.py`
flock), `coverage_start`, `max_pages`; Kalshi `pace_s: 0.2`, explicit series baskets;
Poly tier periods (fast 120 s / slow 900 s, tier frozen at first sight), Gamma
discovery windows (6-hour sub-windows, 2000-offset cap, browser-like User-Agent — the
default urllib UA is 403'd), the 24 h LOCF re-seed, and fine-vs-coarse ticker-format
conventions (**must match the existing stores or dedup breaks** — a build refusal).
The up-down families are excluded from polling tiers (21 leads ~9–108 s apart,
unresolvable at 120 s) and served **only** by `books_wss`: `recorder.py` runs WSS
capture as supervised back-to-back bounded sessions (`session_minutes`; overlap
deduped keep-first), replacing the parent's daemon with the same coverage and a
scheduler-owned restart discipline.

Failure handling stays where the parent proved it: 429 `Retry-After`/backoff, 5xx
retry floors, `has_more`+null-cursor loud stop, per-event containment — all inside
`read`, raising `AssetError` for config/credential problems.

### 4.1 What is acquisition vs what is a build

**Acquisition** = pulling vendor bytes into stores (Tier A WORM snapshots; Tier B
compressed archives). **Builds** = every derived artifact (settlement/strike stores,
PIT selection, poly condensation, ladder stores, panels, fee books) — pipeline
documents reading acquired data.

What builds read, precisely. Tier-A readers (`pmquant-snapshot-rows`) consume
`observations/<source>/<acq_id>/<stream>.jsonl` — committed by `acquire` *before*
validate/certify/publish, so plain reads are **uncertified by construction**; where
certification must gate a build, `certified_only: true` resolves published manifests'
`snapshots[].manifest_hash` to snapshot dirs and reads only those (§7 states the flag
per document). Tier-B and imported stores are read by `pmquant-archive-rows` —
no certification exists there (§3.1); `require_digest` is its integrity gate. The
evidence chain makes *publication* without certification structurally impossible; it
does not police reads, and this design says so.

### 4.2 Modes and cursors

Every stream/mode pair keys its own cursor (ADR-0014). Tier-A cursors are managed by
`run_acquisition`; Tier-B cursors by `recorder.py` through `save_state`/`load_state`.
Cursors stay compact scalars; the rich recorder bookkeeping is §4.3's job.

### 4.3 The 21-lead recorder, honestly re-homed

- **The entry point is `python -m pmquant.recorder`** (in the tree, in the README
  runs, tested by `tests/test_recorder.py`). It takes the per-recorder `flock`,
  computes the **expected (event, lead) periods** from the `markets` stream +
  `LeadGrid`, declares them to the onboarding **CoverageLedger** (ADR-0030 — the
  caller declares; the toolkit never guesses a calendar), invokes acquisition (CLI
  for Tier A; direct `read()` landing for Tier B), and marks outcomes. Its
  `roll-pull` subcommand runs the weekly Tier-B chain (§7); its `verify` subcommand
  re-checks Tier-B digest chains.
- **Ledger keying:** `(source="kalshi", stream="lead_books", unit=<event_ticker>,
  period=<lead_frac as a 6-dp string, e.g. "0.980000">)`, status `fetched | no_data`
  — "attempted-but-empty" stays distinct from "never attempted".
- **The connector reads the ledger explicitly**: `source-kalshi-leads.json` carries a
  `coverage_db` path knob; `read()` opens `CoverageLedger(path)` for the first-seen /
  `already / capture / recovery / skip_too_stale / defer` classification.
  Acquisition never consults it implicitly (ADR-0030's own rule); TODO-6 tracks the
  generic acquire-side hook.
- **The lead schedule** (`f ∈ {0.98 … 0.02}` over `span = close − max(open, close −
  dur_cap)`, dur_cap 48/168/744 h, `min_abs_lead 60 s`) is domain math in
  `ladder/protocols.py` (`LeadGrid`), used identically by `recorder.py` and
  `pmquant-pit-build` — one implementation, byte-identical epochs, pinned by a parity
  test (§10).

### 4.4 Validation suites, and the usable-event bar

Suites are declarative but row-level (six rule kinds; no grouping, no cross-field
comparison, integer thresholds). The child **shapes its records so the bars become
row-level** and keeps event-level truth in pipeline nodes. One suite per Tier-A
stream family (the eight files in §2's tree); Tier-B streams are deliberately not
suite-validated (§3.1).

- `suite-kalshi-markets.json` (`kalshi-markets-basic`): `row_count{min:1}` (error),
  `not_null{field:"ticker"}` (error), `accepted_values{field:"result",
  values:["yes","no",""]}` (error), `bitemporal` (error), `unique{field:"ticker"}`
  (warn).
- `suite-kalshi-leads.json` (`lead-books-basic`): `row_count{min:1}`,
  `not_null{field:"lead_frac"}`, `in_range{field:"lead_frac", min:0.0, max:1.0}`,
  `accepted_values{field:"capture_kind", values:["lead","settle"]}`,
  `in_range{field:"depth_levels", min:0}`, `bitemporal` (all error). Absence of a
  book is data, not error (`depth_levels: 0` passes).
- The remaining six suites follow the same shape over their streams (candles OHLC
  presence/range; quotes book-JSON presence; fee rows `fee_type` vocabulary +
  `fee_multiplier` range; settlement archives `result` vocabulary + ticker
  uniqueness).
- **The usable-event definition** (ladder object at all 21 leads, one-sidedness OK,
  non-corrupt, settled) is event-level and cross-stream — NOT a suite rule: computed
  by `ladder/inventory.py` (one canonical implementation — L5 retired), reported by
  `pmquant-inventory`. TODO-7 tracks a declarative group-by rule kind.
- The cross-feed validators (`validate_book_fidelity.py`,
  `predexon_validation_report.py`) are beyond suite grammar; a later diagnostics
  document (§12) — explicitly not "become suites".

Tier-A acquisitions then walk `validate` → `certify` → `publish` →
`dskit.assets sync-published`.

---

## 5. The catalog — the assets seam

`configs/asset-model.json`, name `pmquant-model`. **Two groups of kinds.** First,
verbatim, the engine-required kinds `sync-published` and `ingest-run` register —
`source`, `dataset` (required `source` ref), `dataset_version` (ref `dataset`),
`run_observation`, `artifact` (name/digest/media_type, ref `run` →
`run_observation`), `output` — lifecycles unmodified (a ref to an undeclared kind
refuses at load; the skeleton's two-kind model supports neither engine verb and is
not a usable base). Second, the ten domain kinds:

| Kind | Governed? | Payload fields (beyond `name`) | Refs |
|---|---|---|---|
| `archive` | record-only | `path`, `format` (`gz-ndjson` \| `zstd-parquet` \| `parquet` \| `json`), `digest`, `rows`, `stream`, `origin` | `source` |
| `ladder-store` | lifecycle `draft → frozen → retired` | `store_ver`, `cuts_ms`, `venues`, `manifest_digest`, `n_events`, `n_usable` | `dataset` (optional), `archive` (optional) — two declared refs; at least one set, checked by `test_configs.py` |
| `checkpoint` | record-only | `seed`, `recipe_id`, `module_ref`, `module_params`, `vocab_digest`, `state_hash` | `ladder-store` |
| `ensemble` | record-only | `seeds`, `recipe_id`, `merge_digest`, `members` (array of checkpoint version_ids — the `feature_set.members` idiom; membership integrity checked in `test_configs.py`, so ensemble arity is not frozen into the model) | `ladder-store` |
| `prediction-set` | record-only | `block`, `run_tag`, `n_rows`, `parquet_digest` | `ensemble`, `ladder-store` |
| `verdict` | record-only | `spec_digest`, `deploy_set`, `n_tested`, `alpha`, `correction` | `prediction-set` |
| `pool-roll` | record-only | `roll_id`, `window`, `n_events`, `partition_digest` | `prediction-set`, `freeze` |
| `freeze` | record-only | `recipe_id`, `frozen_params`, `objective_val`, `n_seeds`, `sd`, `se` | `ladder-store` |
| `varpi` | record-only | `varpi_by_market`, `fit_window`, `envelope: "monotone-upper"` | `pool-roll` |
| `fee-book` | record-only | `venue`, `digest`, `retrieved`, `n_series` | `source` |

**Who registers what.** `sync-published` registers `dataset_version`s (Tier A) —
after the one-time bootstrap: one `register source …` per connector and one
`register dataset --ref source=<vid>` per Tier-A stream alias (sync refuses an
uncataloged or ambiguous alias). `ingest-run` registers
`run_observation`/`artifact`/`output` for every run (idempotent, file-based). The ten
domain kinds are registered by the child CLI `python -m pmquant.catalog`:
`register-run <run_dir>` derives payloads/refs from run-dir artifacts (nine kinds);
`register-archive <store_path> --stream <s> --origin <parent-import|tier-b>` covers
Tier-B landings and the one-time corpus import (the `archive` kind's registrar).
Payload field sets are pinned against `configs/asset-model.json` in
`test_configs.py`. Lineage claims in §3.2 hold once these steps run — the runbook
makes them part of every roll and every import. This replaces the parent's
`RunLedger` and PIT model registry; the vocab travels in the `checkpoint` payload
*and* the torch sidecar (§6.3) — closing landmine L4.

---

## 6. The pipeline seam — the kind census

**Thirty-eight kinds in four modules.** Roles are class attributes; every kind is
`pmquant-` prefixed; none is `owned`; every param cell is the complete default-deny
allowlist. `mode` contracts (conformance requires trainable-role kinds to read
`mode`): `pmquant-causal-beta`, `pmquant-declared-qhat`, `pmquant-market-implied`,
and `pmquant-signal-qhat` refuse `mode:"load"` by name. The two torch kinds subclass
the pack and therefore carry its contracts verbatim rather than re-implementing
them: `pmquant-ladder-train` extends `dskit.pipeline.libs.torch:DeclaredTrain`,
inheriting its outputs (`("signal","artifact_path","metrics")`), its fixed-epoch
loop and its sidecar format unchanged, and widening the allowlist by exactly one
knob — `monitor`, for the best-val-epoch checkpoint rule (§6.3; the generic seam is
TODO-13); `pmquant-ladder-predict` extends `DeclaredPredict`, keeping the base's
`artifact_path` input and its declared-model knobs — so the sidecar restore's
recorded-class and state-hash refusals fire unchanged — while widening the allowlist
with `block`/`leads`, overriding `outputs` to `("pred_rows","metrics")`, and adding
the vocab-digest/column-order check (§6.3). One tightening the child makes on the
base: `module` is the pack's only *required* declared knob, and on the TRAIN side an
omitted `adapter` falls back silently to the pack's flat-vector default — which would
build the wrong model and bypass `LadderPanelAdapter` entirely — so
`pmquant-ladder-train` **requires `adapter`** (must be
`"pmquant.models:LadderPanelAdapter"`). The predict side has no such hole: the
restored adapter is rebuilt from the **sidecar's** recorded params, not the node's,
and a predict node declaring a *differing* adapter is already refused by the pack's
own sidecar cross-check. `pmquant-ladder-predict` requires it anyway for
readability, and the §10 train/predict equality pin restates that check rather than
adding one — redundancy, deliberately, not a load-bearing guard.

### 6.1 `nodes_data.py` (15 kinds)

| Kind | Role | Outputs | Inputs | Params (default-deny) | Notes |
|---|---|---|---|---|---|
| `pmquant-ladder-source` | `data` | `records`, `instruments` | — | `data_dir`, `instruments` (`"auto"` or explicit list; `"all"`/`"*"` refused), `venue` | The parent's `PredexonSource` re-expressed: PIT ladder root reader; `supported_split_kinds = ("time","trailing")`; `fingerprint()` per instrument; `data_edge()`; `event_bounds()` (one of the two hooks that make `policy:"event-close"` legal). Polymarket ask-completion selected by `venue` |
| `pmquant-settlement` | `labels` | `outcomes`, `rows`, `instruments`, `metrics` | `instruments` (**optional** — when unwired the param governs) | `data_dir`, `instruments` (`"auto"` scans the store root, or an explicit list) | Reads the **built** settlement/strike store: `outcomes` = the settled-YES map (labels), `rows` = the full settlement/strike rows (strikes, open/close, result) the build kinds consume, `instruments` = the series actually read. The optional input plus `"auto"` deliberately relaxes the parent's hard "wire it from a data node" refusal, because the build documents have no ladder source to wire from — their output *is* what a ladder source would later read. Wired input wins over the param; `supported_split_kinds` declared (D-151); `fingerprint()` over the store |
| `pmquant-snapshot-rows` | `data` | `records` | — | `root`, `source`, `stream`, `certified_only` (default `true`), `published_root`, `dataset` | The **Tier-A** observation reader (the `intraday_poc-bars` shape; TODO-10): bitemporal dedup, `fingerprint()` over acquisition manifests; `certified_only` per §4.1 |
| `pmquant-archive-rows` | `data` | `records` | — | `root`, `venue`, `stream`, `format` (`gz-ndjson` \| `zstd-parquet` \| `parquet` \| `json`), `instruments`, `require_digest` (default `true` — refuses a store whose digest chain does not verify) | The **Tier-B and imported-store** reader; `fingerprint()` over the digest chains/part digests. No `certified_only` — §3.1/§4.1 |
| `pmquant-markets-build` | `transform` | `rows`, `audit`, `metrics` | `markets_rows`, `recovery_rows` (optional), `base` (optional — the imported store via `pmquant-archive-rows`) | `venue`, `strike_source` (`"api"` \| `"title"`) | The settlement/strike store build: additive merge over the base (key conflict → newer wins; base-only rows retained), the exchange-native provenance crosscheck, recovered rows audited never silently merged. **`strike_source:"api"` (Kalshi):** strikes come from the ticker/API and the decoder proof is a hard gate. **`strike_source:"title"` (Polymarket):** Gamma carries no strike fields, so strikes are derived from the question title by the **comma-aware** numeric regex — `greater` → floor = first number, `less` → cap = first number, `between` requires TWO numbers and otherwise leaves strikes null rather than minting a degenerate `floor == cap` bucket. (The old non-comma regex collapsed `61,800` to `61`; both cases are pinned in §10.) The derivation is stamped into the store's provenance (`poly_strikes: "re-derived from question titles"`), and it is what makes poly rung order — sorted by floor/cap — and ladder-type classification well defined |
| `pmquant-markets-write` | `transform` | `written`, `metrics` | `rows`, `audit` | `path`, `venue`, `overwrite` (must be `false`), `expect` | Write beside the reader; append/merge-only; shrink refuses |
| `pmquant-pit-build` | `transform` | `rows`, `metrics` | `snapshots`, `markets` | `lead_fracs`, `dur_caps_h`, `min_abs_lead_s`, `max_spread`, `venue`, `source` (`"l2_archive"` \| `"lead_books"` — the forward-capture wiring reads the `lead_books` stream with its capture-lag semantics) | LOCF selection at each (event, lead); `markets` (the settlement rows) supplies the settled universe, strikes, open/close; admissibility `t ≥ snap_ts`; staleness recorded not gated; reasons `ok/no_book/low_quality/settle`; Kalshi mirror-encode, Polymarket direct. Every row carries its `venue` |
| `pmquant-pit-write` | `transform` | `written`, `metrics` | `rows` | `path`, `venue`, `overwrite` (must be `false`), `expect` | Append/merge-only; shrink refuses; refuses rows whose `venue` differs and poly-format output for a `KX*` series (I-233, structural) |
| `pmquant-store-build` | `transform` | `store_spec`, `manifest`, `metrics` | `records`, `outcomes`, `markets` | `cuts_ms` (strictly increasing), `store_ver`, `min_events` (int ≥ 50, may be raised never lowered), `purge_window_h` (default 200) | Frozen-store content: manifest with strikes/law metadata (law_ok/monotonicity — violators stay in the manifest, excluded from training), close-time blocks, first-observed-lead purge (relabel, never delete). It also stamps **`eligible_asof_t1`** per series into the manifest — the as-of-T1 claims universe, computed from the same `min_events` bar and the same T1 the documents' eligibility chain applies — which is what every downstream *scoring* mask reads (§6.3). Deterministic |
| `pmquant-store-write` | `transform` | `written`, `metrics` | `store_spec`, `manifest` | `data_dir`, `store_ver`, `overwrite` (must be `false`), `expect` | Writes `processed/ladder_qhat/<store_ver>/` + `meta.json` with pinned cuts; an existing version refuses |
| `pmquant-ladder-panels` | `data` | `train_rows`, `val_rows`, `cal_rows`, `test_rows`, `vocab`, `metrics` | — | `data_dir`, `store_ver`, `cuts_ms` (verified against `meta.json` on open — G-147), `horizon_ms` (the store's recorded horizon; a mismatch against `meta.json` refuses — this is the **run-time half** of §7's splits pin, and it is a literal because a `data`-role node's params must be fully literal, so `roll.py prepare` stamps it and the config test ties it to `splits.test_end_ms`), `instruments`, `k_lvl`, `drop`, `limit` | Pure reader of a frozen store; `fingerprint()` = store meta + manifest digests; every emitted row carries the manifest's `eligible_asof_t1` flag (the claims-universe mask §6.3 scores on) and the `store_ver` it was read from (the first of the four banking attribution columns, §6.1 `pool-write`); **`event_bounds()` from the manifest's per-event first/last observed instants** — the hook that makes `event-close` legal in the train documents, whose only data node this is. Emitting `test_rows` is deliberate (§7's write-once banking is the protection; env-var side channels retired) |
| `pmquant-pool-write` | `transform` | `written`, `metrics` | `pred_rows`, `family` | `pool_root`, `freeze_id`, `roll_id` | Write-once banking of **in-family rows only** under the freeze's single-writer ledger lock; existing `(freeze_id, roll_id)` refuses; duplicate `(market,event,lead,rung)` a hard error; event-overlap checked on write. Every banked row carries the four attribution columns docs/22 R4 requires: `freeze_id` and `roll_id` from the params, and **`store_ver` and `ensemble_id` from the rows themselves**. `store_ver` is stamped by the panels node (it holds the knob), copied onto the prediction frame by `pmquant-ladder-predict`, and passed through by ensemble and CVAP; `ensemble_id` is minted at `pmquant-ensemble` (the `merge_digest` over its members' `state_hash` values, which cannot exist before the merge) and travels ensemble → CVAP → here. A frame missing either column refuses: an unattributed pooled row is a disclosure hole, because a look months later must be able to say which store version and which trained ensemble produced each event it is testing. `pmquant-pool-read` surfaces all four in `pool_meta` |
| `pmquant-pool-read` | `data` | `pred_rows`, `pool_meta`, `instruments` | — | `pool_root`, `freeze_id`, `rolls` (`"all"` or explicit list — literal) | Reads pooled predictions + per-market distinct ask-present counts + the partitions' attribution columns (`freeze_id`, `roll_id`, `store_ver`, `ensemble_id` — surfaced in `pool_meta`, so a pooled verdict can disclose exactly which stores and ensembles its evidence came from) + the look trail (consumed watermarks, standing verdicts — validated on read); re-runs the overlap check; `fingerprint()` over partitions + look records |
| `pmquant-inventory` | `report` | `inventory`, `metrics` | `records`, `outcomes` | `min_events` (required) | The union-of-venues inventory (the document wires both venues through `concat`): usable/2sd/trd layers, excluded series enumerated, settlement-alignment reconciliation. Verdict-first. Its `≥ min_events` column is a coverage mark, never the gate a run applies |
| `pmquant-fee-book` | `transform` | `table`, `metrics` | `base`, `fee_rows` | `venue` | Builds the dated per-series fee table: imported curated book (`base`, a `table-file`) extended forward from acquired `fee_schedules` snapshots; declared-UNPRICEABLE null spans carried through. Output is the mapping `table-write` requires (the records→table residue is TODO-11) |

### 6.2 `nodes_model.py` (10 kinds)

| Kind | Role | Outputs | Inputs | Params | Notes |
|---|---|---|---|---|---|
| `pmquant-fit-rows` | `tensor` | `rows`, `metrics` | `records`, `settled_yes` | — | Envelope → per-cell fit rows for the beta path |
| `pmquant-causal-beta` | `train` | `signal`, `metrics`, `evidence` | `fit_rows` | `min_train` (≥1, default 30) | Per-(series, lead) MAP beta recalibration; `mode:"load"` refuses |
| `pmquant-declared-qhat` | `train` | `signal`, `metrics`, `evidence` | `fit_rows`, `val_rows` | `model` (class ref, required), `model_params`, `train_end_ms` (required; wire `"$splits.train_end_ms"`), `min_train`, `group_by`, `price_field` | The per-cell declared-model bridge |
| `pmquant-signal-qhat` | `signal` | `signal` | `signal` | `price_field` | The q̂/predict dual-contract shim |
| `pmquant-market-implied` | `signal` | `signal` | — | — | The D-006 null |
| `pmquant-ladder-train` | `train` | `signal`, `artifact_path`, `metrics` | `rows`, `val_rows` | the `DeclaredTrain` allowlist (`device`, `epochs`, `features`, `label`, `loader`, `log_every`, `lr`, `max_log_lines`, `optimizer`, `optimizer_params`, `module`, `module_params`, `adapter`, `adapter_params`) widened by exactly one knob: `monitor` (must be `"claims_val_event_ll"`), with `adapter` promoted to **required** (§6 preamble) | Subclasses `dskit.pipeline.libs.torch:DeclaredTrain` and adds the one thing the pack lacks: **best-val-epoch checkpoint selection** — the persisted artifact is the epoch that minimised `monitor`, not the final epoch. `claims_val_event_ll` is the per-event val log-loss **restricted to the as-of-T1 claims universe** (`eligible_asof_t1`, §6.3), which is exactly what D-133's recipe selects on; scoring every series in the store would select a different checkpoint. There is deliberately **no patience knob**: the frozen recipe runs every declared epoch and only the *checkpoint*, never the *loop*, is selected — early stopping would train a different estimator than the one P3 must reproduce, and it is the LP-FT stage's knob in the parent, which `scratch` skips. Everything else — the fixed-epoch loop, the training curve, the sidecar format, the `mode:"load"` restore-or-refuse contract — is inherited verbatim. It also stamps `selected_epoch`, `monitor` and `monitor_value` into `metrics`, because the pack computes its inherited metrics **before** the best-state restore, and two of them will disagree with it by design: `final_loss`/`final_*` describe the last epoch, and the inherited `best_epoch`/`best_val_loss` describe a *different* selection rule (the pack's fixed `val_loss` objective, not `monitor`). `selected_epoch` alone names the epoch whose weights were persisted — and the `checkpoint` asset and P3's E8 reproduction both need to know which. §6.3 says why the rule is load-bearing; TODO-13 is the generic seam |
| `pmquant-ladder-predict` | `signal` | `pred_rows`, `metrics` | `panel_rows`, `artifact_path` (the base's checkpoint reference, wired from the train node) | the `DeclaredPredict` allowlist (`artifact`, `features`, `label`, `module`, `module_params`, `adapter`, `adapter_params` — `module` required and pinned equal to the train node's) + `block` (`val` \| `cal` \| `test`; refuses rows labeled otherwise), `leads` (must be `"all"`) | Subclasses `DeclaredPredict` (§6 preamble), so the sidecar's recorded-class and state-hash refusals fire on every restore. Predicts the **claims universe only** — events whose series carries `eligible_asof_t1` (§6.3) — so the val/cal/test frames, and therefore the pool, are eligible-only by construction, as the parent's are. Emits the frozen frame: one row per (event, step, visible rung) with `series, event, domain, step, lead, bucket, rung, y, q, ask, ask_no, ask_sz, bid_sz, partition, block`, plus the two **attribution** columns banking needs (§6.1 `pool-write`): `store_ver`, copied from the panel row, and `state_hash`, the restored sidecar's own state digest, which the pack hands back on every restore so it costs no extra port |
| `pmquant-ensemble` | `transform` | `pred_rows`, `metrics` | `member_0` … `member_4` (N-ary) | `require` (default 5) | Per-cell mean; **loud merge** (raises on coverage or `y`/`ask` disagreement — I-204); stamps `ensemble_id` onto every emitted row — the `merge_digest` over its members' `state_hash` values, which the predict frames carry (§6.2 above), so the digest names the exact five trained checkpoints and equals the `ensemble` asset's `merge_digest` in §5. It drops the per-member `state_hash` column in favour of that one digest, and passes `store_ver` through unchanged; together those are how the banked pool becomes attributable (§6.1 `pool-write`). Writes `seed_panel.parquet` as its artifact (context, never gate material) |
| `pmquant-cvap` | `transform` | `pred_rows`, `intervals`, `metrics` | `pred_rows`, `cal_rows` | `folds` (default 5), `seed` | Cross Venn–Abers: fits on the cal-block ensemble predictions, applies to the test-block ensemble; emits the test frame plus `p0`/`p1` **probability** bounds on q̂ (not the MIO's entry-gate input, which is a net-edge bound in dollars — that is `pmquant-cell-ci`, §6.5; the two are different statistics in different units and must not be conflated). Cal reaches CVAP only here — auditable in the DAG |
| `pmquant-val-objective` | `score` | `metrics`, `evidence` | `member_0` … `member_{k−1}` (N-ary — the **per-seed** val prediction frames, never an ensemble) | `split` (must be `"val"`), `min_seeds` (default 3; fewer wired members refuses — selection never sees one seed) | The HPO objective, **ensemble-free** exactly as the parent's is: `val_event_logloss` per seed — event-mean log-loss over **all visible cells, deliberately not ask-present cells** (ask-present would import the liquidity bias the gate has by necessity and the objective must not) — then the **across-seed mean** as `metrics.val_event_logloss`, with `sd`, `se`, `per_seed` and the within-1-SE band in evidence. Scoring a merged ensemble instead would be a different statistic (Jensen) and would re-rank candidates by seed diversity, which is why the ensemble never appears on this path |

The transformer is named through the declared-model seam — `module =
"pmquant.models:LadderQhatModule"`, `adapter = "pmquant.models:LadderPanelAdapter"`
— on `pmquant-ladder-train`, the child kind that extends `DeclaredTrain`. Training
is **single-stage supervised-from-scratch**: that is the D-133 frozen recipe (E4
killed SSL, E3 showed LP-FT never beat scratch on test), so the parent's staged
pretext/LP-FT trainer is experiment-era apparatus the evidence retired (§12). One
part of that recipe the pack cannot express, and it is load-bearing: the frozen
recipe keeps the **best-val-epoch** checkpoint scored on the event-level
`claims_val_event_ll`, while `TorchTrain` persists the final epoch's weights and its
`val_rows` feed curve telemetry only. At eight epochs with no early stop,
final-epoch ≠ best-epoch is the normal case, so reproducing E8 requires that rule —
hence `pmquant-ladder-train` owns it. **The seam, named:** the pack's
epoch loop exposes no per-epoch hook, so the rule rides the adapter —
`LadderPanelAdapter` computes the claims-masked per-event val log-loss at each epoch
boundary and snapshots the state dict whenever it improves, and its `fitted()` hook
(which `TorchTrain` calls after the loop and before the artifact is written) restores
that best state into the module. The snapshot site is `beliefs()` — the one
whole-val-split call the pack makes once per epoch boundary — which the pack skips
entirely when the val set is empty, silently leaving the final epoch persisted. So
`pmquant-ladder-train.validate_inputs` refuses a missing **or empty** `val_rows` by
name (raised before the loop), and §10 pins the static half: every train node wires
it. The loop itself is genuinely untouched: every
declared epoch runs, and only the checkpoint is selected. TODO-13 is the generic seam
that would retire the arrangement. Five seeds = five
`pmquant-ladder-train` nodes (seeds in `loader.seed`, everything else byte-identical
— pinned by the §10 shared-modelling-core test), merged by `pmquant-ensemble`. The
seed set is hash-material document structure, not an env var.

**The claims-universe mask.** The frozen store deliberately holds every series it
could build (v3 carries far more than the family), while the claims family is the
subset that cleared the as-of-T1 bar (docs/20's N ≈ 104). Every *scoring* surface on
the model path is masked to that family: `pmquant-ladder-predict` emits only
eligible events, `pmquant-ladder-train`'s `monitor` averages only over them,
`pmquant-val-objective` scores only them, and the cal frame `pmquant-cvap` fits on
inherits the mask with its rows. Training *inputs* are deliberately **not** masked —
the pooled model learns from the whole store, exactly as the parent's does; the mask
governs what is *scored*, *selected on*, and *banked*. Its single source is the
manifest's `eligible_asof_t1` flag stamped once at store build (§6.1) from the same
bar and cut the documents' eligibility chain applies, so the training-time universe
and the banking/verdict family cannot drift apart — §10 pins that they agree, and
`pmquant-pool-write`'s `family` input remains the authoritative banking filter.

### 6.3 The declared-model contract for the ladder

`LadderQhatModule` ctor knobs (via `module_params`): `n_leads`, `k_lvl=5`, `drop`
(production `"context"`), `time_enc` (`"transformer"` production; `"gru"` a knob),
`d_model=64`, `n_time_layers=2`, `wide_head=false`. **The market count and the vocab
digest are deliberately not document knobs** — they track the store version, not the
recipe, and the vocab genuinely grows roll to roll (one added series shifts every
embedding index at or after the insertion point). `LadderPanelAdapter` therefore
ships with empty `adapter_params`: it sizes the market embedding from the vocab the
panels node emits, builds the module with that size, and records the vocab digest
into the sidecar at fit time; at restore it refuses by name on a vocab-digest or
column-order mismatch against that sidecar (L4/L39 closed structurally, and the
per-roll document diff stays free of vocab bookkeeping). It also owns
batching/collation, the frozen 41-column token order, and artifact persistence. The settlement law is carried by construction in the head; the two
vocabularies stay distinct (`SettlementLaw` vs `LadderType`; two-tailed POLY\*HIT
families are legal multi-YES, never renormalized — L8).

### 6.4 `nodes_verdict.py` (8 kinds)

| Kind | Role | Outputs | Inputs | Params | Notes |
|---|---|---|---|---|---|
| `pmquant-ladder-validate` | `transform` | `cluster_scores`, `floor`, `metrics`, `evidence` | `pred_rows`, `family` (required) | `min_testable_events` (int; **< 50 refuses, full stop** — may be raised, never lowered), `baseline` (must `"N0"`), `leads` (must `"all"` — verifies the frame carries the full 21-lead grid per event; a filtered frame refuses), `scope` (`"window"` \| `"pooled"`) | The D-138 statistic: per event `Δ_e` = flat mean over ask-present cells of ε-clipped per-rung Bernoulli log-loss (ask) − (q̂), jointly across leads and rungs — mean not sum; ε = 1e-4 structural; NaN-ask masks both sides; duplicate cell keys a hard error. Family members below the floor (or with zero predictions) are **marked, never dropped**: `cluster_scores` carries a row per family member with `status ∈ {testable, coverage-limited}` so the downstream panel is family-complete. `scope:"window"`: an all-coverage-limited family raises. `scope:"pooled"`: no raise — early accrual is normal (R6). `floor` re-emits `min_testable_events` (N₀ IS the testability floor — one number, structurally tied). The gate never sees size |
| `pmquant-market-tests` | `transform` | `markets`, `evidence` | `cluster_scores` | `alpha` (0.05), `n_boot` (10000), `seed` (0), `correction` (must `"bh"`) | The single-window level-1 test: per testable market the one-sided event-clustered **studentized recentered bootstrap-t** with add-one p (signed-degenerate handling), then step-up BH over the tested set. `markets` is the **family-complete** panel — `(n_events, n_events_tested, Δ̄, z, p, q_adj, tested, deploy, reason, n_boot)`, with NaN z/p and `reason:"coverage-limited"` rows for withheld members (present-but-untestable observable as `n_events − n_events_tested`). Not a gate — ϖ and reporting are never gate descendants |
| `pmquant-verdict-gate` | `gate` | `survivors`, `verdict`, `evidence` | `markets` | — | **GO iff the deploy set is non-empty; NO-GO otherwise** (exit 3 = the honest empty-deploy result). Writes `verdict.json` atomically before returning — headline first (`DEPLOY`/`NO-DEPLOY: n/m tested markets pass BH(q=…)`), write-once, `allow_nan=False`. Terminal in every document |
| `pmquant-look-tests` | `transform` | `markets`, `looks`, `evidence` | `cluster_scores`, `pool_meta`, `floor` | `alpha`, `n_boot`, `seed`, `look_base` (float > 1, default 2), `budget_base` (float; < 2 refused), `budget_convention` (`"spend-first-look-full"` default \| `"lifetime-capped"`), `weights` (must `"raw"`) | The pooled/banked test: look `j` due when a market's distinct ask-present pooled count reaches `floor·look_base^{j−1}` past its **consumed watermark** (read from `pool_meta`'s look trail); due markets get the studentized test + weighted BH on `p/w` with raw weights; not-due and coverage-limited members get NaN rows with `reason` (`not-due` / `coverage-limited`). `looks` carries the write-once look record content (due set, per-market `j_m`, `w_m`, operative `q_j`, times-tested before/after — the R5 disclosure set — plus per-market deploy outcomes for R6 folding) and the convention-derived lifetime-spend disclosure (2q under spend-first-look-full at base 2). A zero-due run emits an empty-tested record — legal and meaningful |
| `pmquant-look-gate` | `gate` | `survivors`, `verdict`, `evidence` | `markets`, `looks` | — | **R6:** verdict = GO iff the **cumulative** deploy set (standing passes folded from the look trail + new passes) is non-empty; a standing PASS persists between looks; a zero-due run is a valid outcome, never an error, never a withdrawal. Terminal |
| `pmquant-look-write` | `transform` | `written`, `metrics` | `looks` | `pool_root`, `freeze_id` | The look-trail writer beside its reader (`pmquant-pool-read`): one write-once JSON record per verdict run under `<pool_root>/<freeze_id>/looks/`, atomic tmp+rename, validated on read. The record's `run_id` is **derived at execute from the run context** (`ctx.run_dir`'s basename `<name>-<asof>-<hash8>`, which orders by asof) — deliberately never a document param, because a literal id would make the pooled document runnable exactly once and dskit has no per-run reference grammar. An id that already exists refuses (a double-spent look budget), and one that does not sort strictly after the newest existing record refuses (the monotone watermark). Empty-tested records are legal and meaningful — a verdict run with no due looks still happened, and recording it keeps the trail complete. Takes the same freeze single-writer lock as `pool-write` |
| `pmquant-varpi` | `transform` | `varpi`, `evidence` | `markets` | `band` (`"point"` \| `"upper"`), `n_stability` (required when `band="upper"`; 0 refused there), `null` (`"theoretical"`), `n_sims` (int ≥ 50, required — calibrates the empirical-null trigger bands), `seed`, `numerator_p0` (must be 1), `f1` (`"gaussian"`), `dedup` (mapping, default `{}` — where the §14 Q3 ruling lands as config) | Efron two-groups lfdr on the family-complete panel: tested rows give `z = Φ⁻¹(1−p)` capped at `Φ⁻¹(B/(B+1))` (a `B < 9,999` panel refuses); **NaN rows (coverage-limited / not-due) are excluded from the FIT set and enter the OUTPUT at ϖ = 1.0** — the NA policy, so C9a excludes untested markets by non-instantiation; fewer than **10 tested, deduped markets in the fit set** refuses (a structural constant, not a knob — a three-parameter mixture on fewer points is noise, and the refusal has to bite in exactly the early-accrual regime the shadow document runs in, which a panel-count floor would never do); monotone upper envelope; trigger bands mark the output `flagged`, and a flagged fit never auto-feeds deployment. Frozen by write-once artifact + `varpi` asset; capital documents consume it only as a sha256-pinned `table-file` |
| `pmquant-calibration-sweep` | `transform` | `candidates`, `metrics`, `evidence` | `records`, `outcomes` | `n_boot` (default 5000), `seed`, `alpha`, `correction` (must `"bh"`), `min_events` | Strategy #1's model-free lens (D-040): per-market, per-(lead, price-bucket) calibration of the market's own price vs its own settled outcomes (CORP isotonic + conditional-MC diagonal null), BH-selected candidate cells. Every cell its own hypothesis; localization IS the deliverable |

**Why the deploy gate is role `gate`, not `stat_test` — and what that costs.** The
planner requires `stat_test`-role nodes to be `owned`, and children never set
`owned`. Two tests ride two homes, both honestly: the **backtest documents** keep the
toolkit's owned `stat_test` feeding capital (exactly the parent's shipped shape); the
**D-138 gate** lives in `pmquant-market-tests`/`pmquant-look-tests` + the terminal
gates. **Consequence, stated plainly:** no document can let the D-138 verdict
authorize sizing until TODO-2 lands; until then the deploy decision is
operator-mediated (a human reads `verdict.json`, then runs a capital document whose
in-document gate is the owned `stat_test`), and the ϖ-consuming capital path is
blocked on TODO-2. §9's deployment row carries the caveat.

**Runspec subsumption, field by field.** The document IS the spec (D-144); the 16
`Level1Spec` fields map:

| Level1Spec field | Where it lives now |
|---|---|
| `statistic="paired_logloss"` | structural constant of `pmquant-ladder-validate` (stamped in evidence) |
| `alternative="one_sided_better"` | structural constant of the tests kinds (stamped in evidence) |
| `alpha`, `n_boot`, `boot_seed` | params of both tests kinds |
| `correction` | `pmquant-market-tests` **only** (must `"bh"`; `"holm"`/`"none"` retired unexercised; `"lfdr_capital"` retired *as a correction* — its function is the MIO's C9/`q_cap` rows). The pooled path's weighted BH is structural, carried by `pmquant-look-tests.weights` (must `"raw"`), so that kind declares no `correction` knob |
| `baselines=("N0",)`, `require_all` | `baseline` (must `"N0"`); `require_all` retired |
| `family=AUTO` | the in-document two-venue eligibility chain wired into `family` |
| `leads="all"` | `leads` on validate (verified against the frame) |
| `min_testable_events` | validate param (≥ 50, never lowered; re-emitted as `floor`) |
| `varpi_max` | `pmquant-kelly-mio` param |
| `varpi_null`, `varpi_numerator`, `varpi_f1`, `varpi_band` | `pmquant-varpi` params |

### 6.5 `nodes_capital.py` (5 kinds)

| Kind | Role | Outputs | Inputs | Params | Notes |
|---|---|---|---|---|---|
| `pmquant-implied` | `transform` | `cell_law`, `metrics` | `records`, `signal` | `renormalize` (two-tailed families refuse renormalization by law) | Phase-1 input (i): the coherent per-event cell law |
| `pmquant-cell-ci` | `transform` | `pool`, `metrics` | `records`, `settled_yes`, `signal` | `fee_rate_by_series`, `tau`, `alpha`, `n_boot`, `seed`, `min_events` | Phase-1 input (iv): the material for the MIO's CI-robust entry gate. Wraps `books.py`'s `build_cell_edge_pool` + `point_gate_side_of`: per `(series, lead_frac)` cell it pools the *realized* net edges (`payout − price − exact fee`, **in dollars**) of settled events, choosing each event's traded side with `point_gate_side_of(signal, fee_rate_by_series, tau=tau)` — the **same q̂ and the same `tau` the allocator uses**, because a naive always-one-side rule misprices every NO-side and multi-bracket cell and corrupts the interval, and because a CI gate that disagrees with the q̂ about which cell a decision belongs to is a bug the parent already paid for. **It emits the POOL, never resolved bounds** — `{(series, lead_frac): (net_edges, events, close_ts)}` plus the bootstrap knobs — because the bound is a function of the *decision instant*: `CausalCellCI.lo_c(series, lead_frac, asof)` slices the pool to events settled strictly before `asof`. The consuming capital node builds the provider over the pool and resolves `lo_c(series, lead_frac, asof)` **once per contract, at that contract's own sizing epoch** (`pmquant-kelly-mio`, building the per-ticker bound map — a single batch-wide asof would leak into every contract sized before it) or at **every epoch instant** (`pmquant-replay`); a frozen table would leak post-decision settlements into earlier epochs, which is the exact look-ahead the gate exists to prevent. Fail-closed: a cell with no pool has no bound and cannot be gated in. Deliberately NOT the CVAP output — `p0`/`p1` are calibrated probabilities on q̂: a different statistic, in a different unit, on a different key |
| `pmquant-kelly-mio` | `capital` | `positions`, `outlay`, `lots`, `metrics`, `evidence` | `records`, `survivors`, `signal`; optional `varpi` (a sha256-pinned `table-file` wire — the frozen vector is part of the document hash), optional `cell_ci` (the net-edge **pool**, wired `"$cell_ci.pool"` from `pmquant-cell-ci`; the node builds `CausalCellCI` over it and resolves the bound at its own decision instant), optional `settled_yes` (**required iff `allocator:"joint"`**; refused otherwise) | `bankroll`, `deploy_frac`, `kelly_fraction`, `min_lot`, `fee_rate_by_series` (required; may be a `$`-ref to a merged fee table), `tau`, `depth_haircut`, `n_tangents` (default **128 on both capital kinds** — the allocation entry points default to 24 while the replay loop already passes 128, so the node layer pins one number rather than letting a document whose `size` and `replay` solve the same books answer the same question at two fidelities; that is arithmetic drift, not tuning), `event_cap`, `split`, `allocator` (`"per-event"` \| `"joint"`), `n_omega` (default 512), `sigma_identity` (bool — the Σ=I ablation; must leave the entry set unchanged), `q_cap` (required iff `varpi`; refused otherwise), `varpi_max` (default 0.5), `ci_robust_gate` (bool, default false; **`true` requires a wired `cell_ci` and `false` refuses one** — an execute-time refusal in `validate_inputs`, which the driver raises before the solve; dskit's only plan-time node hook, `validate_params`, never sees wired ports, so §10's config-test pin is the static half that catches every shipped and generated document. The guard matters because the gate switched on with no pool fail-closes every contract, and the run then deploys nothing while exiting 0 — indistinguishable from an honest no-edge result, which is the I-232 failure class) + `tau_ci`, `cvar_alpha` + `cvar_max_frac`, `sigma_robust_strength` (each defaulting to the nominal no-op — the parent's default-OFF `RobustnessConfig`, so the un-robustified program stays bit-identical) | One-epoch sizing via `mio.py` + `scenarios.py`: exact-log tangent-MILP on HiGHS, deterministic (+ the global-scheduler reset guard), integer lots (`lots` = the total integer lot count, the report renderer's zero-deploy flag input), venue-exact fees, entry gate B1 at τ (CI-robust when `ci_robust_gate` + `cell_ci` wired — closing E6a's unwired-`cell_lo` finding), dependence sizing-only. `market_of` derived from each record's series. **Wiring `varpi` forces `allocator:"joint"`** — per-event cannot carry C9 and refuses. Post-solve assertions are assertions, not constraints. Evidence carries the **allocation explanation** (§8.4): gated-in/skipped reasons per contract, binding constraints, leave-one-out growth contributions — verdict-first |
| `pmquant-replay` | `capital` | `final_bankroll`, `net_pnl`, `gross_pnl`, `n_epochs`, `twr`, `mwr`, `total_contributions`, `cumulative_contributions`, `evidence` | `records`, `survivors`, `signal`, `settled_yes`; the same optional `varpi`/`cell_ci` (the pool — the loop re-resolves `lo_c(series, lead_frac, t)` at **every** epoch instant `t`, never once for the whole replay) | the `pmquant-kelly-mio` set plus `primary_lead`, `no_trade_band`, `allow_trim`, `contributions`, `q_hold` | The 7-step strict-PIT epoch loop (`simulator.py`): settle, universe, read+query at the observed lead (the four-arg `QHatProvider` contract), cumulative-premium budget cap, allocate, `walk_book` execute with insolvency skip-and-disclose, mark. Censored positions marked at cost and disclosed. `q_hold` fired ⇒ halt new deployment in the offending markets and stop for manual review — outside the program. Evidence carries Coverage counters + per-contract dispositions + the allocation explanations (§11's I-232 gate) |
| `pmquant-arb-scan` | `transform` | `candidates`, `metrics`, `evidence` | `records` | `fee_rate_by_series` | Strategy #2 (later phase): MEE/box-spread coherence detection + stake-MILP sizing on the crossed books the MIO refuses (`CrossedBookError` → `arb_candidates`) |

The E0–E3 estimator ladder (D-006) is documents, not code: E0 the analytic floor; E1
wires `pmquant-market-implied` into the same MIO document (must size ≈ 0; profit ⇒
look-ahead); E2 each survivor as a single-contract event through the same MIO; E3 the
joint program. The parent's E2/E3 budget confound dies with the bespoke naive-Kelly
path.

---

## 7. The documents — configs census

All documents: default-deny grammar, `notes` on every node, identity-hashed, run via
`python -m dskit.pipeline run <doc> --asof <date> --adapter pmquant`. Exit 3 is a
result.

**Splits sections.** dskit requires a `splits` section wherever a node carries a
`split` param or a `$splits` ref appears; the child pins one in every such document,
always with **`"policy": "event-close"`** — `record` cuts each record on its own
instant, so a 21-lead event would straddle splits (dskit's own docs call it the
leak); the parent kept `record` only because its source lacked `event_bounds()`, and
**both** child data-role readers on these paths implement it
(`pmquant-ladder-source` from records, `pmquant-ladder-panels` from the store
manifest), so every child document flips. §10 pins: no shipped document declares
`record`, and every document declaring an event policy contains a data-role kind
implementing `event_bounds()`. The ladder documents declare
`{"kind":"time","train_end_ms":T1,"val_end_ms":T1c,"test_end_ms":<horizon>,
"policy":"event-close"}` where `<horizon>` is the store horizon (the newest instant
the store covers, from `meta.json`): engine-train = (−∞, T1] = child train;
engine-val = (T1, T1c] = child val; engine-test = (T1c, horizon] = child **cal +
test**, disambiguated by `block` params (the engine cannot name a cal block —
TODO-4). Pins, in two halves: **statically**, `splits.train_end_ms == cuts_ms[0] &&
splits.val_end_ms == cuts_ms[1] && splits.test_end_ms >= cuts_ms[2] &&
splits.test_end_ms == panels.horizon_ms`; **at run time**,
`pmquant-ladder-panels` refuses when its literal `horizon_ms` disagrees with the
horizon recorded in the store's `meta.json`. The horizon is a literal stamped by
`roll.py prepare` rather than a `$splits` reference because a `data`-role node's
params must be fully literal — the config test is what ties the two together, and
the node is what catches a store that moved. Pooled documents pin their family cut
at the **earliest banked roll's T1** — the only cut knowable to every banked event —
and print that `splits` object.

**The two-venue eligibility chain** (used by every banking/verdict document and the
joint backtest — the family is joint across venues, §1 stage 2): `kalshi_records` +
`poly_records` (two `pmquant-ladder-source`) + `kalshi_settlements` +
`poly_settlements` (two `pmquant-settlement`) → `all_records` (`concat`,
`shape:"records"`, `provenance:"venue"`, `key:["instrument","contract"]`,
`allow_empty:false`) + `all_outcomes` (`concat`, `shape:"table"`,
`allow_overlap:false` — provenance on a table is proven by refusing overlapping
keys) → `bankable` (`filter require_usable`) → `bank` (`event-bank`,
`count:"settled"`, `distinct_by:"group"`, `strictly_before:"$splits.train_end_ms"`)
→ `eligible_family` (`eligibility`, `min_events:50`). Nine nodes.

**The per-roll mechanism, stated plainly.** dskit's CLI has no param overrides and a
document's node set is fixed, hash-material JSON — and dskit's own `walkforward`
verb (ADR-0027) cannot carry the roll either: folds vary the `splits` section only,
while the roll's knobs are node params (`store_ver`, `cuts_ms`, `roll_id`, the
condense parts dir) and the cal block is inexpressible (TODO-4); TODO-12 records the
per-fold node-param gap. The weekly roll therefore runs
`python -m pmquant.roll prepare --store-ver v<k> --cuts <T1,T1c,T2>
--horizon <ms> --roll-id <id> --parts-dir <path>`, which instantiates the four
templates in `configs/templates/` into `configs/rolls/<roll_id>/` (committed as the
roll's PIT evidence — each roll deliberately mints new document hashes). The
generated set: `run-poly-condense.json` (parts dir), `run-roll.json`
(store_ver/cuts), `run-ladder-train-roll.json` (store_ver/cuts/horizon/roll_id),
`run-verdict-roll.json` (roll_id/cuts/horizon). §10 pins the template↔instance diff
to exactly those fields. The full weekly chain (all seven parent E9 steps):

1. `python -m pmquant.recorder roll-pull` — Tier-B acquisition: Gamma settled-event
   discovery (maintains the token-universe file), pmxt hour pulls into the fresh
   parts dir, poly live part merge, the resumable Predexon KX backfill
   (single-flight; 1 req/s).
2. Tier-A acquires + validate/certify/publish for the week's streams.
3. `run-markets-build-kalshi.json` + `run-markets-build-poly.json`, then
   `run-pit-build.json` + `rolls/<id>/run-poly-condense.json`.
4. `rolls/<id>/run-roll.json` — build + write store v<k>.
5. `rolls/<id>/run-ladder-train-roll.json` — train, predict, calibrate, bank.
6. `rolls/<id>/run-verdict-roll.json` — the roll's level-1 verdict.
7. `python -m pmquant.catalog register-run …` per run dir; `register-archive` for
   new Tier-B landings.

The documents:

- **`run-markets-build-kalshi.json`** (6 nodes): `markets_rows`
  (`pmquant-snapshot-rows`, stream markets, `certified_only:true`) + `recovery_rows`
  (`pmquant-snapshot-rows`, stream settlements_archive, `certified_only:true`) +
  `base` (`pmquant-archive-rows`, the imported store, format parquet) → `build`
  (`pmquant-markets-build`) → `store` (`pmquant-markets-write`) → `report`
  (`run-report`).
- **`run-markets-build-poly.json`** (5 nodes): `markets_rows`
  (`pmquant-snapshot-rows`, stream `settlements` on the Polymarket source,
  `certified_only:true`) + `base` (`pmquant-archive-rows`) → `build`
  (`venue:"polymarket"`; no recovery leg) → `store` → `report`. This is the reader
  that consumes what `suite-poly-settlements.json` certifies.
- **`run-pit-build.json`** (5 nodes): `snapshots` (`pmquant-archive-rows`, Tier-B
  `l2_snapshots`) + `markets` (`pmquant-settlement` over the built Kalshi store,
  `instruments:"auto"` — this document has no ladder source to wire a universe from,
  §6.1; the `rows` output is what `pit_rows` consumes) → `pit_rows`
  (`pmquant-pit-build`) → `pit_store` (`pmquant-pit-write`) → `report`. (A second
  shipped wiring, activated when forward F1 capture accumulates: `snapshots ←
  pmquant-snapshot-rows` over `lead_books` with `source:"lead_books"`.)
- **`run-poly-condense.json`** (template only; 5 nodes) — the same shape over
  pmxt/live hour parts, `venue:"polymarket"`, `markets ← run-markets-build-poly`'s
  store via `pmquant-settlement.rows` (`instruments:"auto"`). Like the other three
  per-roll documents it ships as a template and is always run from
  `configs/rolls/<roll_id>/`; P1's initial backfill uses a bootstrap roll id rather
  than a second, flat copy.
- **`run-fee-book-kalshi.json` / `run-fee-book-polymarket.json`** (5 nodes each):
  `base` (`table-file` over the imported `fee-book-<venue>.json`, sha256-pinned) +
  `fee_rows` (`pmquant-snapshot-rows`, stream fee_schedules, `certified_only:true`)
  → `book` (`pmquant-fee-book`) → `book_file` (`table-write`) → `report`. The
  written book is re-pinned into `configs/` by sha256; a new family series makes
  `FeeBook` raise `FeeRateUnresolved` — the refresh trigger.
- **`run-inventory.json`** (8 nodes): the two-venue source/settlement quartet →
  `all_records` (`shape:"records"`, `provenance:"venue"`) + `all_outcomes`
  (`shape:"table"`) → `inventory` (`pmquant-inventory`) → `report`. Weekly.
- **`run-calibration-sweep.json`** — the same two-venue spine feeding
  `pmquant-calibration-sweep` → `report`.
- **`run-backtest-kalshi.json`** — the parent's 15-node production document,
  re-expressed with the same node keys, wiring, and params: `ladder_records` →
  `settlements` → `bankable` → `bank` → `eligible_family` → `banking_report` →
  `primary_lead_only` (`filter`: one lead + mid band — the scoring lens only) →
  `fit_rows` (records **unfiltered**) → `qhat` (`pmquant-causal-beta`) →
  `market_baseline` → `validate` (owned, `split:"val"`, `min_events:50`) →
  `edge_test` (owned `stat_test`, `alpha:0.05, correction:"bh", n_boot:10000,
  seed:0`) → `size` (`pmquant-kelly-mio`, records ← the lens, `$prev` bankroll
  carry) → `replay` (`pmquant-replay`, records **unfiltered**) → `run_report`
  (ports printed: `validation: "$validate.evidence"`, `edge: "$edge_test.evidence"`,
  `sizing: "$size.evidence"`, `replay: "$replay.evidence"`, `survivors:
  "$edge_test.survivors"`, `lots: "$size.lots"`, `banked: "$bank.counts"`, `family:
  "$eligible_family.instruments"` — `survivors`+`lots` wired is what arms the
  renderer's LOUD zero-deploy flag, the I-232 gate). Splits:
  `{"kind":"trailing","test_days":14,"val_days":28,"train_days":"all-prior",
  "policy":"event-close"}`. One lead per run is the beta path's design; the
  all-21-leads rule governs the transformer gate. The CI-robust entry gate is
  **off** in the shipped backtest documents (`ci_robust_gate:false`), so they wire no
  `cell_ci` and the 15-node parity with the parent holds; activating it — the §14 Q7
  ruling — adds one `cell_ci` node (`pmquant-cell-ci`, `records ←
  $ladder_records.records`, `settled_yes ← $settlements.outcomes`, `signal ←
  $qhat.signal` — the same q̂ `size` and `replay` read, with `tau` and
  `fee_rate_by_series` equal to theirs) wired into `size` and `replay`, making the
  document 16 nodes. An empty family is an eligibility
  NO-GO halting every descendant including the report — exit 3 with the driver's own
  `report.md` as the surviving narrative (the parent behaves identically; §11 P2
  expects exactly this on Polymarket).
- **`run-backtest-poly.json`** — the same 15 nodes, `venue:"polymarket"`, the 3
  POLYBTCUPDOWN instruments, poly fee rates.
- **`run-backtest-joint.json`** — the parent's 22-node joint document 1:1: two
  `table-file` fee books → `concat` fee book (`shape:"table"`); the two-venue chain;
  the shared spine; `fee_rate_by_series: "$fee_book.merged"`; `replay` with
  `contributions` + `$prev` prior_total. Splits: pinned `time` cuts + `event-close`
  (two data edges — trailing is illegal; the refusal is the feature).
- **`run-ladder-train.json`** (27 nodes): `panels` → `vocab_file` (`table-write`) →
  `seed_0..4` (five `pmquant-ladder-train`, `rows ← $panels.train_rows`, `val_rows ←
  $panels.val_rows`) → `pred_val_0..4` / `pred_cal_0..4` / `pred_test_0..4` (fifteen
  `pmquant-ladder-predict`, each `panel_rows ← $panels.<block>_rows` and
  `artifact_path ← $seed_k.artifact_path`) → `ens_cal` / `ens_test`
  (`pmquant-ensemble`) → `val_score` (`pmquant-val-objective`, `member_0..4 ←
  $pred_val_k.pred_rows` — the objective is ensemble-free, so there is no `ens_val`)
  → `cvap` (`cal_rows ← $ens_cal.pred_rows`, `pred_rows ← $ens_test.pred_rows`) →
  `report`. Predictions are made once, at all 21 leads, never recomputed.
- **`run-ladder-train-roll.json`** (template; 37 nodes) — the banking variant: the
  same 27 plus the nine-node two-venue eligibility chain plus `pool_write`
  (`pmquant-pool-write`, `pred_rows ← $cvap.pred_rows`, `family ←
  $eligible_family.instruments`, literal `freeze_id`/`roll_id`). §10 pins: the two
  train documents' shared node sets are identical (the roll variant adds exactly
  those ten nodes); the plain train document contains no `pmquant-pool-write`.
- **`run-roll.json`** (template; 10 nodes) — the store build. The frozen stores are
  venue-**joint** (v2/v3 each hold the KX\* and POLY\* series in one store), so this
  document unions both venues exactly as the other cross-venue documents do:
  `kalshi_records` + `poly_records` (two `pmquant-ladder-source`) → `all_records`
  (`concat`, `shape:"records"`, `provenance:"venue"`); `kalshi_settlements` +
  `poly_settlements` (two `pmquant-settlement`, `instruments:"auto"`) →
  `all_outcomes` (`concat`, `shape:"table"`) and `all_markets` (`concat`,
  `shape:"records"`, `provenance:"venue"`, over the two `rows` outputs) →
  `store_build` (`pmquant-store-build`, the week's `store_ver`/`cuts_ms`) →
  `store_write` → `report`. A single-venue roll would silently halve the pooled
  family and leave the other venue permanently coverage-limited. It is a separate
  document from the train spine because a data-role reader (`panels`) must
  fingerprint a store that already exists at resolve time — the same reason the
  build/read split exists.
- **`run-verdict-roll.json`** (template; 14 nodes): `preds` (`pmquant-pool-read`,
  `rolls:[<roll_id>]`) + the nine-node eligibility chain → `validate_all_leads`
  (`pmquant-ladder-validate`, `family ← $eligible_family.instruments`,
  `min_testable_events:50`, `scope:"window"`) → `tests` (`pmquant-market-tests`) →
  `gate` (`pmquant-verdict-gate`, terminal) and `run_report` (`validation ←
  $validate_all_leads.evidence`, `edge ← $tests.evidence`, `banked ← $bank.counts`,
  `family ← $eligible_family.instruments` — no path from the **verdict** gate to the
  report; descending from the eligibility gate is intended, and an empty family
  halts everything at exit 3).
- **`run-verdict-pooled.json`** (15 nodes): `pool` (`pmquant-pool-read`,
  `rolls:"all"`) + the eligibility chain (splits at the earliest banked roll's T1) →
  `validate_pooled` (`scope:"pooled"`) → `look_tests` (`pmquant-look-tests`,
  `pool_meta ← $pool.pool_meta`, `floor ← $validate_pooled.floor`) → `look_write`
  (`pmquant-look-write` — the watermark consumption that makes R5 hold) + `gate`
  (`pmquant-look-gate`, terminal) + `run_report` (from validate/tests/bank/family —
  not a gate descendant). **`look_write` is declared before `gate`**: ties in the
  planner's deterministic topological order break on declaration order, and the look
  must be spent before the verdict is written — a crash between the two wastes a
  look (conservative: budget spent, no verdict) but can never double-spend one,
  which is the anti-conservative failure. §10 pins that ordering. docs/22 must be
  REGISTERED before this runs forward.
- **`run-varpi-shadow.json`** (14 nodes): `pool` + the eligibility chain →
  `validate_pooled` (`scope:"pooled"`) → `look_tests` (the same due-look-restricted
  panel — a market without a due look stays NaN and is excluded from the ϖ fit set,
  entering the output at 1.0; **no halting gate in this document**, so ϖ is produced
  regardless of deploy-set emptiness) → `varpi` (`markets ← $look_tests.markets`) →
  `run_report`. Runs beside every real pooled verdict (docs/20 §7 phase 1); no
  `look_write` — the shadow never spends look budget.
- **HPO — a pre-declared candidate grid, not a search document.** The parent's
  doctrine is grid-not-TPE, ≥ 3 seeds per candidate, selection never seeing one
  seed; dskit's search seam cannot tie one hyperparameter across the seed heads nor
  aggregate a seed ensemble into the objective (TODO-12), so the child mirrors the
  parent's study shape with documents: `configs/hpo-grid.json` declares the
  full-factorial candidate set (hash-pinned); `python -m pmquant.roll hpo prepare`
  instantiates `configs/templates/run-hpo-candidate.tmpl.json` once per candidate
  into `configs/hpo/<candidate_id>/run-hpo-candidate.json`, committed as the study's
  PIT evidence. Each candidate document is the **val-only 3-seed spine, 9 nodes**:
  `panels` → `seed_0..2` (`pmquant-ladder-train`) → `pred_val_0..2`
  (`pmquant-ladder-predict`, `block:"val"`, `artifact_path ← $seed_k.artifact_path`)
  → `val_score` (`pmquant-val-objective`, `member_0..2 ← $pred_val_k.pred_rows`,
  `min_seeds:3`) → `report` (`validation ← $val_score.evidence`). No ensemble node
  on this path — the objective is ensemble-free (§6.2) — and no cal/test/CVAP/pool
  nodes, so a candidate run can never touch test data or bank. Selection = comparing
  `val_score.metrics.val_event_logloss` (the across-seed mean) across the candidate
  run dirs, with the winner's `se` giving the within-1-SE band; the freeze =
  `catalog.py register-run` of the winner minting the `freeze` asset
  (`frozen_params`, `objective_val`, `n_seeds`, `sd`, `se`). **`frozen_params`
  carries both searchable axes** — the module axis (`module_params`) and the
  training axis (the `pmquant-ladder-train` node params the grid may name: `epochs`,
  `lr`, `loader`, `optimizer_params`) — and both are applied verbatim to all five
  seeds in the train documents (pinned by the §10 shared-modelling-core test). A new
  `freeze_id` visibly resets the pool (R3). `loader.seed` never appears in
  `hpo-grid.json` (a config-test pin — seeds are the study's replication axis, never
  a search dimension); `panels` knobs are not candidates (the store is frozen).

---

## 8. Domain engines — the child modules

Mechanisms only; every number a document can state is a param. Re-expressed, not
copied.

### 8.1 `ladder/` — the data engine
Store: per-series parquet blocks `train/val/cal/test` (+ `purged_*` relabeled, never
deleted) + `manifest.parquet` + `meta.json`, the latter carrying the pinned cuts
(verified on every open), the store horizon (§7's run-time pin), and the
provenance strings the build stamped — including `poly_strikes: "re-derived from
question titles"` wherever `strike_source:"title"` produced a series' strikes.
Splits: close-time blocks at the cuts; purge drops an event from a later block
iff its first observed lead precedes the boundary; unknown first observation assumes
the worst recorded window (200 h) and purges rather than leaks. Panels: `x ∈
ℝ^{21×C×2×P}` resting-size tensors, `seen`/`visible` masks, the frozen 41-feature
token order. Inventory: the one canonical usable/2sd/trd implementation. `LeadGrid`
shared with `recorder.py`.

### 8.2 `books.py` — ONE book stack
The `BookSource` protocol, three PIT backends + in-memory, `DecisionEpochRecord`
with best-first resting-**bid** ladders both sides, `walk_book` (best-first walk,
VWAP, one fee on the total at the venue's rounding model, `is_partial`),
`contract_inputs_from_book` (keyword-only; `CrossedBookError` → arb), exactly one
ask-mirroring implementation (`asks_from_bids`; the parent's five divergent mirrors
incl. the L6 trap are retired), and the cell-CI trio `CausalCellCI` +
`build_cell_edge_pool` + `point_gate_side_of` (the per-cell edge pool, its
asof-sliced bound, and the traded-side rule that mirrors the point entry gate).
Executable ladders built the
honest way; candle degradation stores the *bid* complement (the phantom-edge class
pinned by test).

### 8.3 `fees.py`
Two rounding models, one dispatch (I-217): `kalshi_trading_fee` (per-order
ceil-to-cent, 9-dp pre-round guard, 1¢ floor) and `poly_trading_fee` (nearest 1e-5
half-up, relative tie tolerance 1e-12, **no** floor);
`trading_fee_for_series(series, contracts, price, rate)` dispatches on the ticker's
venue — the single fill-time entry point (`walk_book` calls it). `FeeBook` resolves
dated spans **against the market's own close instant** (never the fill instant);
first-match-wins; null spans and unknown series raise by name. `fee_rates_for`
builds the book from the merged fee-table input. No rate constants in code.

### 8.4 `mio.py`, `scenarios.py`, `simulator.py`
`mio.py`: the v1 exact-log program (C1–C8), C9/C9a/C9b rows (off unless wired),
wealth bounds, tangent-plane outer approximation (reported growth always recomputed
exactly), HiGHS determinism + the global-scheduler reset guard, post-solve
assertions, the shared capital param validator/knob tables, the entry gate B1, **the
default-OFF `RobustnessConfig`** (CI-robust entry `ci_robust_gate`/`tau_ci` on
`cell_ci`, the CVaR tail-constraint rows `cvar_alpha`/`cvar_max_frac`, worst-case-Σ
`sigma_robust_strength` applied at scenario-set build) with the robustness-sweep
helper, and **the allocation explainer** — the read-only post-hoc
`AllocationExplanation` (which contracts were gated in vs skipped and why, which
constraints bind, per-position leave-one-out growth contributions), rendered
verdict-first and carried in the capital nodes' evidence. `scenarios.py`: the
rank-dependence fit over settled history (the `settled_yes` input when
`allocator:"joint"`), Iman–Conover / checkerboard-IPF construction, `ScenarioSet`,
`n_omega`, the Σ=I ablation, degenerate-law containment. `simulator.py`: the 7-step
epoch loop with the two seams (`allocate_fn` / `fill_fn` — what lets E0–E3 be pure
document wiring), the epoch schedule, Coverage counters, `ContributionSchedule`,
`q_hold` evaluation from the fills log. The four-arg `QHatProvider` seam stays the
swap point; the transformer's online implementation remains deliberately unbuilt
until markets pass the gate.

### 8.5 `verdict.py`, `calibration.py`, `implied.py`
`verdict.py`: the Δ_e statistic, studentized recentered cluster bootstrap-t (B floor
9,999 where ϖ consumes it), BH + weighted-BH (raw weights), the look schedule with
write-once look records, monotone consumed watermarks and R6 standing-verdict
folding, the Efron lfdr fit (monotone upper envelope, `p0=1` numerator, structural
family floor, simulation-calibrated trigger bands → `flagged`, the NA→ϖ=1.0
policy), and the event-clustered `boot_slope` CI — fail-loud (L15 fixed, not
inherited). Torch-free. `calibration.py`: the MAP `BetaCalibrator` (N1 context,
never a bar), cross Venn–Abers, the shared log-loss/net-entries eval helpers (the
parent's `lab/eval/qhat_eval.py`, live-path, moved here **with its six per-bug
regression tests**), the CORP isotonic reliability diagnostic. `implied.py`:
`yes_interval` (the one home of strike semantics), `implied_distribution` under both
ladder conventions, MEE/threshold coherence checks.

---

## 9. Doctrine preservation map

| Invariant | Where it lives |
|---|---|
| Event = the unit of independence | per-event cluster scores; cluster-only resampling; **`policy:"event-close"` in every shipped document** (both readers implement `event_bounds()`; `record` config-test-banned) |
| Never pool — cells, leads, markets | no node accepts a pooling knob; the sweep decides per cell; the beta path scores one lead per run |
| All 21 leads, always | `leads` (must `"all"`) on predict **and** validate, the latter verifying the frame's grid |
| N0 only; N1/N3 context | `baseline` refuses non-`"N0"` |
| The gate never sees size | validate's consumed tuple excludes sizes structurally |
| Per-rung Bernoulli; mean over cells; ε structural; NaN-ask masks both sides; untestable events present, never dropped | `verdict.py` + the family-complete two-count panel (`n_events` vs `n_events_tested`, `reason` rows) + property tests |
| Family known at T1, joint across venues; a hypothesis cannot vanish; only in-family rows bank | the two-venue eligibility chain wired into `family` on validate **and** pool-write; coverage-limited rows carried to the panel |
| The claims universe masks every SCORING surface (predict, the checkpoint monitor, the HPO objective, the CVAP cal frame) and never the training inputs | the manifest's `eligible_asof_t1` stamped once at store build (§6.3), pinned to agree with the documents' eligibility chain |
| Every banked row is attributable — freeze, roll, store version, ensemble identity (docs/22 R4) | `pmquant-pool-write`'s four attribution columns, surfaced by `pool-read` in `pool_meta` and disclosed by the pooled verdict |
| ≥ 50 settled usable pre-T1 to enter; ≥ 50 ask-present to test (deferral disclosed); one N₀ | `eligibility.min_events` + `validate.min_testable_events` (< 50 refuses, full stop) re-emitted as `floor` into the look tests |
| The GATE tests the ensemble; the HPO OBJECTIVE is ensemble-free; seeds are context; ≥ 3 seeds for any selection or claim | the ensemble is the only path to gate-consumed `pred_rows`, and no ensemble node sits on the candidate spine — `pmquant-val-objective` scores per-seed frames and reports the across-seed mean with `sd`/`se`; `min_seeds:3` makes one-seed selection structurally impossible; the freeze records `n_seeds`/`sd`/`se` |
| HPO: pre-declared full-factorial grid, val-only, seed never searched, both axes frozen together | `hpo-grid.json` (hash-pinned) + generated val-only candidate documents committed under `configs/hpo/` + the no-seed pin; `frozen_params` carries the module and training axes |
| The frozen recipe keeps the best-val-epoch checkpoint, not the last — and runs every declared epoch (no early stop) | `pmquant-ladder-train`'s `monitor` + best-state restore before the artifact is written, with the pack's fixed-epoch loop inherited unchanged and no patience knob to shorten it (§6.3; TODO-13 is the generic seam) |
| Predictions made once; duplicate pooled key = error; disjoint test windows; re-freeze resets the pool | write-once pool partitions; dup-key error on write and read; `pool-roll` refs `freeze`; candidate documents cannot bank |
| R5: raw weights; `budget_base ≥ 2`; spacing a separate knob; **watermarks consumed via write-once look records** (monotone run ids; double-spend refuses; empty-tested legal); convention-derived spend disclosure; per-market disclosure set | `pmquant-look-tests` + `pmquant-look-write` under the freeze single-writer lock |
| R6: a standing PASS persists; zero-due is a result | `pmquant-look-gate` folding the look trail |
| ϖ frozen and hashed before any solve; monotone envelope; p0=1; z from p with the B-cap; calibrated trigger bands; flagged never auto-deploys; **NA markets enter at ϖ=1.0** (excluded by C9a) | `pmquant-look-tests` (no gate ancestry) → `pmquant-varpi` → the `table-file` sha256 pin |
| q̂ and test statistics never in the MIO objective; dependence sizing-only (Σ=I invariance); q_hold outside; C9 needs joint + settled history; robustness default-OFF and bit-identical when off | `mio.py`/`scenarios.py` + oracle tests + the allocator/`settled_yes`/robustness refusals |
| D-138 verdict cannot authorize sizing in-document until TODO-2 | §6.4, operator-mediated meanwhile |
| PIT everything: pinned cuts never move; purge relabels; store meta verified on open; per-roll documents committed as PIT evidence | `ladder/` + content-addressed assets + `store-write` refusal + `configs/rolls/<id>/` |
| Usable is the weakest layer; one definition | `ladder/inventory.py` (L5 retired) |
| Kalshi mirrors, Polymarket does not; never a poly-format store for a KX series | `venue` on source/build/write; `pit-write` refuses (I-233) |
| Kalshi strikes are API facts (decoder-proved); Polymarket strikes are title-derived, comma-aware, and never collapsed to a point bucket | `pmquant-markets-build.strike_source`; the `poly_strikes` provenance stamp; the `61,800` and one-number-`between` cases pinned in §10 |
| Fees: venue-dispatched rounding; close-instant keying; null spans raise; per-source-then-concat; curated history imported never regenerated | `fees.py` + imported books + the fee-book documents |
| Settlement recovery: additive-only, decoder-proofed, crosschecked, audited | `pmquant-markets-build` |
| Budget cap on cumulative committed premium; censor-and-disclose; insolvency skip-and-record | `simulator.py` |
| Everything is a pipeline step; a write is a kind beside its reader; SINK_KINDS is tracking-only | the census; five write kinds; mlflow the only sink |
| The document declares the entire process; `mode:"load"` refuses where persisting would lie | §6 preamble |
| Verdict-first reporting; a zero-deploy run distinguishable from a broken wire | gate artifacts verdict-first; `survivors`+`lots` wired arms the renderer's LOUD flag; the allocation explainer in evidence |
| No architecture from env vars; vocab stamped into checkpoints; loud ensemble merge | §6.3 |

---

## 10. Tests

Per the child convention: `tests/` ships or the child fails; heavy deps via
`find_spec` gates; standalone and under dskit's subprocess runner. **Wall-clock
budget well under the runner's 600 s timeout** — second-scale fixtures, torch e2e at
2 seeds × 2 epochs, heavier work behind the `slow` marker.

- `tests/test_nodes.py` — one `conformance_suite` over the merged 38-kind table
  (probes per kind; `EXPECTED_ROLES` the independent census; `runnable` gates).
- `tests/test_connectors.py` — four-verb contract + acquire→validate e2e per
  connector against `testing.py` stubs; credential refusal by env-var name; cursor
  semantics.
- `tests/test_recorder.py` — the LeadGrid parity pin (recorder due-periods ==
  pit-build epochs, byte-identical), flock single-flight, the 6-dp period encoding
  through CoverageLedger, Tier-B digest-chain verify + `require_digest` refusal.
- `tests/test_configs.py` — every document validates, including every
  `configs/rolls/<id>/` and `configs/hpo/<candidate_id>/` instance. Node-set pins
  per document (the train pair differs by exactly the ten roll nodes; no
  `pmquant-pool-write` outside the roll variant; candidate documents contain no
  cal/test/CVAP/pool/ensemble nodes; no shipped document declares
  `policy:"record"`; every event-policy document contains an
  `event_bounds()`-bearing data kind; template↔instance diffs limited to the §7
  field list; `hpo-grid.json` contains no seed path). Wiring pins: every
  `pmquant-ladder-predict` node takes its `artifact_path` from a
  `pmquant-ladder-train` node in the same document; the train/predict pair declares
  identical `adapter`/`adapter_params` (and both declare an `adapter` at all — the
  flat-vector fallback must be unreachable), with no market-count or vocab knob in
  either (§6.3); `panels.k_lvl == module_params.k_lvl` and `panels.drop ==
  module_params.drop` in any document holding both (a document-declared
  `module_params` value wins over the adapter's data-implied kwargs, so the two must
  move together — including in `hpo-grid.json`); every `pmquant-ladder-train` node
  wires `val_rows` (the node itself refuses an empty one at execute — the best-epoch
  rule is silent without it); the `checkpoint` asset and the P3 reproduction read
  `selected_epoch`, never the pack's inherited `best_epoch`; a `cell_ci` port, where wired, comes from a `pmquant-cell-ci` node
  and never from `pmquant-cvap`, that node's `signal`, `tau` and
  `fee_rate_by_series` match the capital node it feeds, and `ci_robust_gate:true` holds iff a `cell_ci`
  port is wired (the shipped backtests carry `ci_robust_gate:false` and no wire,
  which passes; only a disagreeing pair fails); `size` and `replay` in one
  document resolve the same `n_tangents`; the store manifest's `eligible_asof_t1` set equals
  the eligibility chain's family on the shared fixture (the claims-universe agreement
  pin, §6.3); `look_write` is declared before `gate` in the pooled document; the report node is not a descendant of
  `pmquant-verdict-gate`/`pmquant-look-gate`. Value pins: the static splits/cuts
  half including `splits.test_end_ms == panels.horizon_ms`; fee-book sha256s;
  catalog payload field sets vs `asset-model.json` (incl. `ensemble.members`
  integrity). The **shared-modelling-core pin is two separate assertions**, since
  candidates differ from one another by design: (a) *within* any one document, all
  seed nodes carry identical `module_params` and identical training params and
  differ only in `loader.seed`; (b) the train documents' `module_params` and
  training params equal the winning candidate's `frozen_params` as recorded in the
  `freeze` asset. No cross-candidate equality is asserted.
- Domain math: `test_fees.py` (both rounding models golden incl. the
  `0.07·100·0.25` guard case and a poly tie case; close-vs-fill keying; null-span
  refusal), `test_books.py` (walk_book; mirror round-trips; candle bid-complement;
  CrossedBookError routing; the cell-CI trio — `lo_c` excludes events settling at or
  after `asof`, an empty pool fail-closes, `point_gate_side_of` prices a NO-side cell
  on the NO side, and a replay fixture whose early-epoch bound differs from its
  late-epoch bound, proving the loop re-resolves rather than freezing one table),
  `test_ladder.py` (store pinning; purge; law heads;
  usable/2sd/trd; panels `event_bounds()` vs manifest; the `horizon_ms`-vs-`meta.json`
  refusal), `test_verdict.py` (Δ_e masking/mean/dup-key; the family-complete panel:
  an all-NaN-ask event increments `n_events` not `n_events_tested`; bootstrap
  add-one p and order-independence; BH/weighted-BH; look records: write-once,
  double-spend refusal, watermark monotonicity, empty-tested legality, R6 folding;
  ϖ envelope + NA excluded from the fit set but output at 1.0 + the ten-tested-market
  fit-set floor + B-floor + flagged),
  `test_mio.py` (golden scenarios + oracle quartet — never weakened;
  E1-must-size-zero; Σ=I invariance; C9/joint/`settled_yes` refusals; robustness
  default-OFF bit-identity; the explainer's gated/skipped reasons),
  `test_calibration.py` (BetaCalibrator; CVAP fit-on-cal/apply-on-test emitting `p0`/`p1`; a test that a CVAP frame is refused where a net-edge pool is expected;
  the six qhat_eval regression tests), `test_markets_build.py` (additive merge;
  decoder-proof gate; crosscheck refusal; audit output; the title-derivation cases —
  `61,800` survives the comma, a one-number `between` leaves strikes null rather
  than minting `floor == cap`, and the `poly_strikes` provenance is stamped).
- `tests/test_e2e.py` — fixture runs of the backtest, train, train-roll, and
  verdict documents (exit codes 0/3 as designed), including the **I-232 fixture**: a
  synthetic document engineered to find an edge and deploy zero lots, asserting the
  renderer's LOUD `survivors-but-zero-lots` flag fires and the size/replay evidence
  (dispositions, Coverage counters, allocation explanation) names the reason. All
  labeled fixture.

Coverage: the parent's ≥90% gate is repo policy, not dskit's; §14 Q4.

---

## 11. Migration plan

Each phase lands with its tests green, **ships its named companion notebook plus its
one-command runs** (P0/P6 exempt — no user-facing capability), and ends with a
real-data acceptance run; no phase edits dskit. Parent comparisons are artifact-file
comparisons (§2.2).

- **P0 — scaffold.** Copy `_skeleton`, global-rename, land the modules
  empty-but-registered, the asset model, `.env.example`, the conformance/config test
  spine.
- **P1 — data.** Connectors + suites + certify/publish + `recorder.py` +
  `catalog.py` (both verbs); the corpus import (registered via `register-archive`);
  the markets-build pair + `run-pit-build` + `run-poly-condense` (instantiated from
  its template under a bootstrap roll id) + the fee-book pair
  + `run-inventory`; the `ladder/` engine. Notebook `01-data-inventory.ipynb`.
  **Acceptance:** extend-and-verify the imported settlement stores (the build's
  crosscheck against the imported base), rebuild the PIT ledgers, and reconcile the
  inventory against the parent's 2026-08-16 refresh (Kalshi 29 / 2,323 / 1,916
  usable; Polymarket 264 / 8,923 / 6,609 — dated expectations, drift explained; the
  ~409 recovered labels accounted; `trd` an upper bound on v2-recovered POLY
  series).
- **P2 — the beta backtest path + the model-free lens.** `nodes_model.py` (beta
  family) + `nodes_capital.py` + `books.py`/`fees.py`/`implied.py`/`mio.py`
  (incl. robustness + explainer)/`scenarios.py`/`simulator.py`/`calibration.py`,
  plus `pmquant-calibration-sweep` (the one `nodes_verdict.py` kind P2 lands); the
  three backtest documents + `run-calibration-sweep`. Notebook
  `02-backtest-replay.ipynb`. **Acceptance:** (i) the I-232 gate — the zero-deploy
  fixture fires the LOUD flag and the evidence names the reason without ad-hoc
  scripts; (ii) `run-backtest-kalshi.json` and `-poly.json` on real data (dated
  expectations: Kalshi GO with a family in the high teens; Polymarket an
  eligibility exit 3 — the bar working; families read from each run's
  `resolved.json`/`banking.json`).
- **P3 — the transformer path.** `models.py`, `pmquant-store-build`/`store-write`,
  panels/ladder-train/predict/ensemble/cvap/val-objective, `run-roll` +
  `run-ladder-train` + the HPO grid machinery (`hpo-grid.json`, the candidate
  template, `roll.py hpo prepare`). Notebook `03-ladder-qhat.ipynb`. **Acceptance:** rebuild the
  v2 store from imported PIT data at the pinned v2 cuts, train, reproduce the E8
  result (ensemble ≈ 0.1396 vs market 0.1461 on 130 events, paired delta CI
  excluding 0) within seed noise.
- **P4 — verdict + banking.** The seven remaining `nodes_verdict.py` kinds, pool
  nodes, `run-ladder-train-roll`, the verdict documents. Notebook
  `04-verdict-banking.ipynb`. **Acceptance:** the E8/E9 parquets have no parent
  `verdict.json` baseline (the harness never ran on real data) — the baseline is
  **minted**: run the parent's harness once on those parquets (an owner-visible
  step), then the child matches it market-for-market; failing that, anchor on the
  published E8/E9 paired deltas. Pooled document in shadow; docs/22 REGISTERED
  before any forward pooled verdict.
- **P5 — rolls + ϖ shadow.** The weekly seven-step chain scheduled;
  `run-varpi-shadow` beside every pooled verdict; the v4 roll (due) as the first
  production roll. Notebook `05-roll-varpi.ipynb`.
- **P6 — retire the parent.** Read-only history once P1–P5 match; graduation per
  the checklist.

---

## 12. Out of scope — not ported, and why

| Parent area | Disposition |
|---|---|
| `pipeline/` engine | already dskit; TODO-1 the one residual |
| `pipeline_schwab/`, `store.py` | an equities project; its own child later |
| `lab/` (~60-file exog stack) | dormant era — **except** `lab/eval/qhat_eval.py` and `lab/micro/bootstrap.py` (`boot_slope`), live-path, moved into `calibration.py`/`verdict.py` |
| `jobless/`, `music/`, `tsa/`, `core/screening/`, `gate_mappers/` | exog-era verticals/screens |
| The staged trainer (`ladder/train.py`'s pretext-pretrain and LP-FT stages, `proto`/stage-epoch/`ft_lr_scale` knobs) | experiment-era apparatus: E4 showed SSL does not help, E3 showed LP-FT never beat scratch on test, and D-133 froze **supervised-from-scratch single-stage**. What survives from that file is the single stage *plus its best-val-epoch checkpoint rule*, which `pmquant-ladder-train` carries (§6.3) — `DeclaredTrain` alone would persist the final epoch, a different estimator from the one E8 produced. Revisiting staged training is a new freeze via the HPO grid, not a port |
| C0–C5 pre-modeling gate | superseded: C1/C2 → eligibility; C3 (alignment ≥ 0.95) → P1 reconciliation + inventory evidence; C5 → the level-1 verdict |
| `validate_market` / SP-3 / `replay_gate` runners | superseded by `pmquant-calibration-sweep` (per-cell localization) + the level-1 gate; the robust-majority market lens revives as a document if wanted |
| Cross-feed validators | beyond suite grammar; a later diagnostics document |
| `.claude/markets.md` St…Vd tracker | legacy exog-path tracker |
| `core/backtest/` unit-notional harness | superseded by the replay node; its `lab/micro/fills.py` consumer goes with it |
| `scout/` | zero gate credit, ever; a later document under a scout root, never `verdict_pool/` |
| `history/predexon_trades/`, `lead_panel/` | not on the money path; optional future stream / superseded analysis artifact |
| Order placement, auth, live plumbing | **D-142: do not build** |
| `rl_stocks` coupling | dead (D-147/D-148) |
| `lab/orchestrate` scheduler | not needed: nodes run serially in-process; cross-document parallelism is the OS scheduler's |
| `build_notebook.py` | retired; notebooks are authored deliverables |
| Stage-list grammar, `KalshiBackend`, backend tags | legacy; content re-homed per §2.1 |

---

## 13. Framework gaps → TODOs

Each genuinely generic, with a named dskit home and interim child-side handling —
the child never edits dskit. Root `TODO.md` gets a pointer here; each item graduates
to an ADR only on the owner's word.

1. **`stat_test` evidence self-description** (the one unported pmquant→dskit engine
   capability): `evidence.totals` gains `test`/`statistic`/`independence_unit`/
   `n_boot`/`seed`. Home: `dskit/pipeline/kinds_stats.py`. Interim: child evidence.
2. **Studentized recentered cluster bootstrap-t** as a selectable `stat_test`
   method. Home: `dskit/pipeline/stats.py` + a `method` param. **The blocker for a
   single-document deploy→size path.** Interim: `pmquant-market-tests` + operator
   mediation.
3. **Registrable family corrections / weighted BH.** Home: a `register_correction`
   seam in `dskit/pipeline/stats.py`. Interim: `pmquant-look-tests`.
4. **A calibration split block** (`cal_start_ms` beside `val_start_ms`; trailing
   `cal_days`). Home: `base.TimeSplitConfig`. Interim: `cuts_ms` + `block` params +
   the §7 horizon mapping and pins.
5. **Calibration/scoring packs** (beta recalibration, CORP isotonic, LOEO/expanding
   cross-fit, Efron lfdr, Venn–Abers; `PredictiveDistribution` + proper scoring).
   Home: `dskit/pipeline/libs/`. Interim: `calibration.py`/`verdict.py`.
6. **Acquire-side coverage + guarded parallel acquisition.** Home:
   `dskit/onboarding/acquire.py`. Interim: `pmquant/recorder.py` + the
   `coverage_db` knob.
7. **A grouped/cardinality suite rule.** Home: a new `_RULES` entry in
   `dskit/onboarding/validate.py`. Interim: row-flattening + pipeline inventory.
8. **Compressed snapshot payloads in onboarding** (~96× on gz-class archives, ~10×
   on parquet-class). Home: `dskit/onboarding/snapshot.py`/`acquire.py`. Interim:
   Tier B (§3.1), pending §14 Q8.
9. **A generic records-write kind** (append-only record-stream write; overwrite
   refusal; `expect`; refuse-on-shrink). Home: `dskit/pipeline/kinds_table.py`, a
   `records-write` beside `table-write` (which takes a mapping, not a stream).
   Interim: the five child write kinds (role `transform` because their outputs feed
   further build steps; the domain residue stays child-side either way).
10. **A generic onboarding-observations reader kind** (`pmquant-snapshot-rows` is
    the second child implementing the identical reader — the second-child trigger).
    Home: a tier-2 pack or `dskit/pipeline/kinds_table.py`. Interim:
    `pmquant-snapshot-rows`.
11. **A records → keyed-table verb** (`groupby`/`pivot`; D-152 already defers
    `groupby` by timing, not rejection). Home: `dskit/pipeline/kinds_flow.py`.
    Interim: `pmquant-fee-book`.
12. **Search-seam expressiveness for seed-ensemble studies**: the seam cannot tie
    one hyperparameter across N seed heads, cannot aggregate a per-trial seed
    ensemble into the objective (hpo-grid's `seeds` knob binds a top-level `seed`
    param, but `DeclaredTrain`'s seed lives at `loader.seed`), and `walkforward`
    folds cannot bind per-fold node params (`store_ver`/`cuts_ms`/`roll_id`). Home:
    `dskit/pipeline/kinds_search.py` + `document.py`/`driver.py` (per-fold /
    per-trial param binding). Interim: the generated candidate/roll documents
    (`pmquant.roll`).
13. **Val-metric checkpoint selection in the torch pack**: `TorchTrain` runs a fixed
    epoch count and persists the FINAL epoch's weights. `trainlog.TrainingCurve`
    already computes `best_epoch`/`best_value` and the loop even reports them in
    `metrics` and `training_curve.json` — what is discarded is the best epoch's
    *weights* — and the curve's tracked objective is hard-coded to
    `val_loss`/`train_loss`, so no document-named metric can be monitored. A
    `monitor` + best-state-restore seam would let a
    document say "keep the epoch that minimised this metric" — the rule D-133's
    frozen recipe depends on and the reason the child needs its own train kind at
    all. Home: `dskit/pipeline/libs/torch.py`. Interim: `pmquant-ladder-train`
    (§6.3).

---

## 14. Open questions for the owner

1. **docs/22 registration** — PROPOSED → REGISTERED (dated) before
   `run-verdict-pooled.json` runs forward; the ⚙ parameters (N₀, roll cadence, look
   spacing, `budget_convention`) need their numbers affirmed.
2. **`q_hold` value and grandfathering** — the param exists here, unset by default.
3. **Cross-venue event dedup** — until ruled, the joint family treats venues'
   events as distinct; the ruling lands in `pmquant-varpi.dedup` (and the eligibility
   chain's `key`) as config.
4. **Child coverage bar** — adopt the parent's ≥90% commit gate now, or at
   graduation.
5. **MIO tuning** — D-139 declined to rule; any HPO over MIO knobs needs its own
   ruling before a candidate grid exists for it.
6. **Recorder scheduling host** — the capture schedules and the weekly chain need a
   designated host + scheduler; source configs written with that host's paths.
7. **CI-robust entry (E6a)** — `cell_ci` is producible (`pmquant-cell-ci`) and the
   `ci_robust_gate`/`tau_ci` knobs exist default-OFF; activating changes the entry
   set and needs an owner call.
8. **Tier B ratification** — the bulk book streams bypass P2's immutable-snapshot
   rule (§3.1, compensating controls named and implemented). Ratify, or block
   Tier-B capture on TODO-8.
