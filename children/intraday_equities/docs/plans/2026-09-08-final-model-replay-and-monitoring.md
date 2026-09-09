# Final model, capital flows, replay, and shared monitoring

**Status:** implementation plan with partial owner rulings. Do not write
implementation code until the required ADR is accepted. Do not execute market
data, HPO, calibration, replay, or the March-August lockbox merely because this
plan exists. The owner is still deciding several items in §11.

**Date:** 2026-09-08
**Child:** `children/intraday_equities`
**Primary evidence:**
`docs/memos/2026-09-08-first-mio-backtest-readiness-and-final-model-recommendation.md`,
`docs/memos/p16-feature-mask-and-final-gate-results.md`, ADR-0088, ADR-0108,
ADR-0111, and `docs/plans/2026-09-intraday-equities-mio.md`.

## 1. Execution protocol for the implementing agent

Every implementation agent, test agent, and skeptical reviewer must use
**Claude Sonnet 5**. If Sonnet 5 is unavailable, stop; do not silently
substitute another model.

1. Start in WSL2 from current `origin/main`, in a clean isolated worktree.
2. Read root `AGENTS.md`, this child's `AGENTS.md`, the evidence named above,
   the relevant package READMEs, and the actual classes' `_PARAMS` tuples.
3. Inventory before building. The registries alone are not an inventory.
4. Write one cross-layer ADR in `docs/architecture/decision-log.md`, including
   the full proposed file list below, and stop for owner approval. The ADR must
   resolve every item in §11 that its phase needs.
5. Work test-first: add the smallest failing test, run it and observe the
   expected failure, implement the smallest general solution, then rerun the
   focused tests and Ruff. Never write a large phase before its tests.
6. Use one implementer and one fresh skeptic at a time. Reviewers do not edit.
   They report Critical/Major findings with file-and-line evidence. The
   implementer fixes each finding through a new failing regression test, then
   commits. Give the latest commit to a new Sonnet 5 skeptic. Never run
   skeptical reviews in parallel against different code states.
7. A phase closes only after a fresh reviewer finds zero Critical or Major
   issues and a second fresh reviewer confirms the clean state. Minor findings
   must be fixed or explicitly accepted by the owner.
8. Run only the focused suites named in §10. Do not run the full suite.
9. Use the journal CLI for actions. Agents never edit or regenerate
   `docs/decisioning/path.csv`; §12 is an owner-only update packet.
10. No result from synthetic data is a market backtest. No replay over spent
    development data is deployment evidence.

## 2. Locked owner decisions

These are settled and must not be reopened by the implementer.

### Model and HPO

- The provisional finalist is **`lean-pooled-h10`**: pooled LightGBM using
  P16's exact 33-column drop mask. Model-family and feature-mask selection are
  closed for this build.
- There are **ten independent LightGBM heads**, one for each direct lead
  `h=1..10`. They share feature schema, category rules, search space, and
  release identity. They do not share trees or weights.
- Each lead evaluates the **same exact 24 hyperparameter combinations**. The
  candidate list is materialized once, ordered, hashed, and reused by every
  head. A lead must not draw its own random set.
- Each lead chooses its own winning parameters. Selection uses that lead's
  equal-stock, training-mean-baseline forecast-accuracy contribution—the same
  squared-error improvement semantics used by the outer model score—not
  Spearman IC.
- Use a conservative one-standard-error simplicity rule rather than raw
  argmax. The exact uncertainty unit remains an open methodological decision
  in §11; do not invent it.
- Retain the already-declared research-informed 24-trial LightGBM search
  dimensions and ranges. The P16 four-trial run did not persist a trial ledger,
  so it provides no defensible evidence for narrowing or moving the ranges.
- Every lead reports train/validation gaps, collapsed prediction variance, and
  whether the selected value lies on a searched boundary. A failed diagnostic
  makes the release NO-GO; it never triggers an unplanned second search on the
  same validation data.
- After tuning, refit all ten heads once on every allowed row through
  **2026-02-28** using their frozen per-lead winners.
- Persist one bundled pickle/joblib artifact containing all ten heads, plus one
  human-readable manifest. Hash the model bytes together with all
  schema-bearing manifest fields. The bundle is trusted executable data and
  may be loaded only from the locally produced, hash-verified release path.
- The supported refresh cadence is **63 calendar days** with a trailing
  730-day training window and the frozen design. Monthly retraining is outside
  this build.

### Calendar

- Fit through 2025-11-30.
- Do not fit or score 2025-12-01; it is the embargo session.
- Use 2025-12-02 through 2026-02-28 for finalist-only HPO.
- Refit the frozen winners through 2026-02-28 without another search.
- Nothing dated 2026-03-01 or later is read until the full pre-March procedure
  is implemented, reviewed, and frozen.

### Capital flows

- Backtest initial cash is **$10,000 USD**.
- The planned contribution is **$500 USD every other Friday at 09:30
  America/New_York**.
- The backtest configuration supports a recurring rule plus explicit dated
  overrides for skipped, moved, replacement, withdrawal, or one-off amounts.
- Replay books a declared historical contribution. Production increases
  buying power only after the broker/account source reports the cash as
  settled. A promised or scheduled bank transfer is never spendable capital.
- External flows change account value but never count as trading profit. Every
  performance view keeps starting capital, net external flows, realized PnL,
  unrealized PnL, fees, and NAV separate.

### Pipeline, gates, and observation

- Backtesting uses the pipeline structure. Do not build a parallel bespoke
  backtest script. Historical replay and production run the same decision
  graph; only feed, clock, executor, and account/cash-flow source differ.
- Replay and production emit the same versioned event bodies into the same
  ledger contract and use the same metric reductions.
- Hard-stop on stale or missing data, wrong model/schema/hash, use of an
  uncertified lead, a lead above its per-symbol cap, replay/live parity
  divergence, or account reconciliation mismatch.
- Model-performance deterioration may warn or hold only after the declared
  minimum outcome count. Numeric windows, thresholds, and the warn-versus-hold
  boundary remain open in §11.
- Build the whole MIO forecast/replay path before any market-data training or
  replay execution. TDD and synthetic integration tests still run throughout;
  this ruling forbids premature empirical runs, not tests.

## 3. Architecture to preserve

The decision graph is one graph:

```text
historical bars / live bars
        -> frozen features
        -> verified 10-head model bundle
        -> predictions in label units
        -> causal conversion to gross-return units
        -> confirmed per-symbol lead caps
        -> calibrated pi_upper + joint return scenarios
        -> EquityKellyMIO
        -> target inventory and proposed trades
        -> replay executor / paper executor
        -> one account + event ledger
        -> shared reducers, monitors, reports, and alerts
```

Replay supplies a deterministic historical clock, historical quotes, declared
cash flows, and simulated fills. Production supplies the wall clock, current
quotes, settled account state, and paper broker fills. Everything between those
boundaries is identical and pinned by release identity.

## 4. Inventory: built versus missing

### Reuse; do not rebuild

- `dskit.pipeline` already plans and runs JSON documents, walk-forward folds,
  sealed predictions, benchmark comparison, and search nodes.
- `ColumnSubsetEstimator` already enforces P16's named mask and reindexes the
  LightGBM categorical column.
- `FinalModelGates` plus generic `HorizonConquest` already implement
  training-mean improvement, multiplicity-corrected skill, seasonal
  stability, and contiguous horizon caps.
- `EquityKellyMIO` already solves one decision tick, consumes scenarios and
  `pi_upper`, requires statistical survivors, applies the scoped cost model,
  and emits targets, trades, evidence, and solver metrics.
- `dskit.production` already owns clocks, feeds, paper execution, accounting,
  hash-chained state, `cash_flow` records, reconciliation, guards, replay
  comparison, reports, eighteen monitor kinds, alerts, and Prometheus/OTel
  metric sinks.
- The production value report already keeps external cash flows out of earned
  cumulative PnL. Extend that owner; do not calculate a second PnL child-side.

### Actually missing

1. `run-final-hpo.json` still describes the pre-P16 full-feature recipes and
   cannot select/train the exact lean candidate.
2. The estimator-level child search supports only MSPE or IC, tunes each scan
   independently, emits only the winning score, discards winning parameters,
   and emits no full trial ledger.
3. There is no frozen per-lead candidate inventory, no lead-accuracy selection
   rule, and no defined standard-error unit for the locked simplicity rule.
4. The final scan returns predictions and scalar metrics only. It does not
   publish ten refitted heads as one verified release artifact.
5. Generic sklearn persistence exists, but its current `SklearnFit` does not
   forward the feature-name and categorical metadata required by
   `ColumnSubsetEstimator`; do not route around that refusal.
6. No real point-in-time publisher converts vol-scaled SPY-residual predictions
   to gross fractional returns and produces calibrated `pi_upper` plus aligned
   joint scenarios.
7. P16's lead caps are development-only and carry
   `deployment_eligible=false`. Confirmation evidence and a pinned deployable
   cap artifact do not exist.
8. `EquityKellyMIO` requires symbol survivors but does not consume and enforce
   a `(symbol, lead)` cap artifact.
9. Production can record actual cash flows, but no generic recurring-flow
   schedule materializes historical deposits and overrides for replay.
10. No stateful equity execution replay applies next-bar fills, costs, exits,
    overlapping decisions, and account roll-forward across ticks.
11. Operational and model monitors exist, but the equity decision/execution/
    portfolio events and shared economic metric catalogue are not yet wired
    end to end.

## 5. File ownership and placement

The ADR must approve this list before any new file is created. If inventory
shows an existing public seam owns a responsibility, extend it instead of
creating a second owner.

### Generic mechanisms in `dskit`

- `dskit/pipeline/kinds_search.py`: add reusable objects for a frozen candidate
  inventory, complete trial records, and a pluggable one-standard-error
  selection strategy. They operate on caller-supplied scores and know nothing
  about stocks, leads, LightGBM, or returns.
- `dskit/pipeline/libs/sklearn.py`: add a public OOP bundle artifact that writes
  and loads a mapping of named sklearn-shaped estimators as one joblib file
  plus one JSON manifest. The manifest includes ordered head names, per-head
  constructor parameters, full and surviving feature order, categorical
  encoding, training identities/cuts, library versions, and deterministic
  prediction checksum. The digest covers model bytes and schema-bearing
  manifest content. Reuse the existing verified-artifact rules.
- `dskit/pipeline/libs/sklearn.py`: fix the existing `SklearnFit` doorway or
  give the bundle writer the same inspected `feature_name(s)` and
  `categorical_feature` forwarding contract. There must be one public helper,
  not duplicate signature inspection in child code.
- `dskit/production/cashflows.py` **(new, proposed)**: standard-library-only
  `RecurringCashFlowSchedule` and immutable dated override/value objects.
  Handle `zoneinfo`, recurrence anchoring, half-open windows, DST, exact
  timestamp ordering, idempotent flow IDs, deposits, withdrawals, and
  corrections. It materializes due flow records; it never decides whether a
  real transfer settled.
- `dskit/production/compose.py`, `state.py`, and `report.py`: compose scheduled
  flows only for replay, keep production adoption settlement-driven, and add
  generic time-weighted and money-weighted performance without changing the
  existing external-flow/PnL separation.
- Reuse `dskit/production.loop.ServeLoop`, `ReplayFeed`, replay clock,
  accounting, executor, ledger, and report before proposing any new replay
  engine. If they cannot express deterministic stateful fills, the ADR must
  name the smallest missing abstract hook in the owning existing module. Do
  not create `backtest.py` as a parallel engine.
- `dskit/production/metrics.py`, `monitors.py`, and `vocab.py`: add only generic
  low-cardinality operational readings or generic reducers that are genuinely
  absent. Per-symbol/per-lead values belong in ledger artifacts and reports,
  not unbounded Prometheus labels.
- Update `dskit/production/{README.md,AGENTS.md}` trees if `cashflows.py` is
  approved, and update the sklearn/search package docs for public APIs.

### Equity-specific adapters in `children/intraday_equities`

- `intraday_equities/final_model.py` **(new, proposed)**: own only the domain
  assembly of ten lead datasets, the lead-specific outer-aligned score, the
  exact P16 lean mask, per-lead winner records, refit cuts, and calls into the
  generic search/bundle objects. No generic sampler, serializer, or hash logic
  lives here.
- `intraday_equities/forecast_bundle.py` **(new, proposed)**: point-in-time
  equity conversion from label units to gross fractional returns, plus the
  child-specific assembly and validation of the ADR-0088/MIO bundle. Generic
  calibration estimators graduate to `dskit`; this file only binds equity
  fields and reference policy.
- `intraday_equities/replay.py` **(new, proposed)**: thin equity policies for
  bar choice, execution timing, forced exits, horizon identity, and the Schwab
  cost/fill adapter. It subclasses or composes `dskit.production`; it does not
  own clocks, ledgers, account folds, or generic performance math.
- `intraday_equities/nodes_capital.py`: require the pinned confirmed cap input
  and a lead on every bundle row; refuse absent, zero, stale, ineligible, or
  over-cap rows before optimization. Preserve the existing required
  `stat_test` survivor wire.
- `intraday_equities/metrics.py` **(new, proposed)**: only equity event-field
  adapters and domain-specific metrics such as signal decay by lead. Generic
  reducers and storage remain in `dskit.production`.
- `intraday_equities/__init__.py`: import the approved new child modules so
  adapter import registers their public nodes. Update child README/AGENTS trees.

### Configuration documents

- Modify `configs/run-final-hpo.json`: select the pinned P16 comparison, declare
  only the exact `lean-pooled-h10` recipe, freeze one 24-combination inventory,
  execute per-lead selection on the correct score, and emit complete ledgers.
- Add `configs/run-final-refit.json`: read pinned HPO winners, refit ten heads
  through 2026-02-28, and write the one verified model bundle.
- Add `configs/capital-policy.json`: USD, $10,000 starting cash, the $500
  biweekly Friday rule, timezone, anchor and same-timestamp ordering once
  resolved, dated overrides, and every later-approved MIO/account knob. The
  file is pinned by digest wherever consumed.
- Add `configs/run-development-replay.json`: replay already-spent P16 evidence
  only, use the same decision graph, and force `deployment_eligible=false`.
- Add `configs/run-mean-confirmation.json`: remain unreadable until the full
  pre-March release is frozen; then consume only the approved March-May slice.
- Add `configs/run-full-system-backtest.json`: remain unreadable until the mean,
  uncertainty, caps, MIO, execution, costs, cash policy, and monitoring policy
  are frozen before 2026-06-01.
- Do not create a real-money configuration. This child remains paper-only.

## 6. TDD build sequence

### Phase 0 — ADR, owner gates, and plan-only inventories

1. Write the ADR with all public classes, parameters, outputs, event shapes,
   artifact schemas, file additions, and identity/hash effects.
2. Present §11 and the proposed Path packet in §12. Wait for owner rulings and
   ADR acceptance.
3. Add config shape tests first. `validate` and `plan` are allowed; execution
   is not.

### Phase 1 — frozen per-lead HPO evidence

1. Test that one seeded candidate inventory contains 24 unique, ordered
   combinations and every lead receives byte-identical combinations.
2. Test that each lead may select a different winner and that changing another
   lead's scores cannot change this lead's result.
3. Test the exact forecast-accuracy objective against a hand-calculated tiny
   example where IC and squared-error improvement prefer different candidates.
4. Test the approved one-standard-error rule, deterministic simplicity order,
   non-finite scores, ties, boundary flags, collapsed predictions, and complete
   trial count.
5. Test that the ledger records every candidate, score components, fit seed,
   cuts, row counts, parameters, diagnostics, selected winner, and inventory
   digest. A score-only output must fail the contract.
6. Test that no read reaches 2026-03-01 and that 2025-12-01 is excluded.

### Phase 2 — final refit and one bundle

1. Test ten exact head names `h01..h10`; missing, duplicate, or extra heads
   refuse.
2. Test each head receives only its own frozen winner while every head uses the
   identical feature/category contract and refit cut.
3. Test one joblib file and one manifest are written atomically; overwrite,
   partial write, path escape, foreign class, and untrusted/tampered bytes
   refuse.
4. Test the digest covers ordered head names, parameters, feature order,
   category map, cuts, data/cache identities, software versions, and model
   bytes.
5. Test a deterministic prediction fixture produces the manifest checksum and
   that reordered features, swapped heads, or changed predictions fail load.
6. Test the refit includes permitted rows through 2026-02-28 and performs no
   HPO.

### Phase 3 — cash flows and account state

1. Test $10,000 initial USD cash once, never as PnL or a later deposit.
2. Test $500 biweekly Friday materialization from the owner-approved anchor,
   exact 09:30 New York instants across EST/EDT, half-open window boundaries,
   restart idempotence, and no duplicate flow IDs.
3. Test dated skip, move, replace, withdrawal, and correction overrides.
4. Test replay applies declared flows in the approved same-timestamp order.
5. Test production ignores scheduled-but-unsettled money and books only the
   reconciled settled event.
6. Test external flows change cash/NAV but not realized PnL, unrealized PnL,
   trading cumulative return, or drawdown attribution. Test TWR and MWR against
   hand-calculated paths with deposits and losses.

### Phase 4 — real forecast bundle and confirmed caps

1. Write unit tests for the owner-approved inverse label transformation. A
   vol-scaled SPY-residual prediction must never be accepted as a gross return.
2. Test point-in-time availability for volatility, SPY reference return,
   residual beta, prices, outcomes, `pi_upper`, and scenario residuals.
3. Test every scenario row shares timestamp, horizon, weights, unit, and release
   identity; mixed horizons or mismatched scenario sets refuse.
4. Test confirmation caps are pinned, deployable, contiguous from h1, and from
   evidence not used to choose the P16 mask. Development caps always refuse
   deployment.
5. Test the capital node rejects a missing lead, zero cap, lead above cap,
   stale cap, wrong model release, or name absent from the statistical survivor
   set.

### Phase 5 — stateful replay through the production seams

1. Prove by test whether existing `ServeLoop` + replay feed/clock + paper
   executor can drive deterministic historical ticks. Extend the smallest
   generic hook only after a failing conformance test demonstrates the gap.
2. Pin decision-time bar availability, order timestamp, fill timestamp and
   price, fees, rejection/partial-fill behavior, holding clock, forced exit,
   and treatment of overlapping signals after §11 is ruled.
3. Test crash/restart at every boundary: cash flow, decision, intent, fill,
   outcome, exit, and checkpoint. Replaying the ledger must yield identical
   positions, cash, NAV, and metrics.
4. Test solvency, buying power, cardinality, caps, no-trade band, HFDR, CVaR,
   and cost assertions after every fill, not only after each solve.
5. Test rejected and unfunded candidates remain observable and cannot create
   phantom positions or disappear from opportunity-cost reporting.

### Phase 6 — shared event and metric contract

Define one versioned event schema and make both replay and paper production
emit it. The initial catalogue is below; values are recorded even before alert
thresholds are approved.

- **Identity/invariants:** release, model-bundle, feature-schema, cap,
  calibration, scenario, cost-policy and capital-policy digests; event time,
  known-at time, source as-of, lead, symbol, and deployment eligibility.
- **Data:** expected/received/missing/late bars, feature completeness, reference
  completeness, stale age, label/outcome coverage, and corporate-action gaps.
- **Model by symbol and lead:** count, baseline and model SSE, R2OOS, IC,
  calibration slope, prediction bias, prediction/residual dispersion, weakest
  required slice, drift statistic, and time since last successful refit.
- **Gates:** candidate count, survivor count, cap, every refusal reason,
  adjusted p-value/evidence count, performance hold state, and override state.
- **MIO:** bundle age, scenario count/effective weight, eligible/routed/sized
  names, build/solve latency, solver status, objective, expected return, CVaR,
  HFDR usage, cash/gross/cardinality/position utilization, binding constraints,
  and cleared-but-unfunded candidates.
- **Execution:** proposals, orders, acknowledgements, fills, partials,
  rejections, cancels, decision-to-submit/fill latency, arrival-to-fill
  slippage, spread, fees, implementation shortfall, fill rate, turnover,
  holding time, forced exits, and signal decay over realized latency.
- **Portfolio/capital:** starting cash, settled external flow, cash, buying
  power, gross/net exposure, per-name concentration, realized/unrealized/net
  PnL, fees, NAV, TWR, MWR, peak, drawdown, and risk-cap utilization.
- **Operations/parity:** ticks, phase latency, refusals, retries, exporter
  failures, reconciliation breaks, breaker state, health, dead-man heartbeat,
  and replay-versus-paper divergence by existing divergence class.

Do not put symbol or lead in closed telemetry label sets unless cardinality is
explicitly bounded and approved. Preserve full detail in ledger/report
artifacts; exporters may publish safe aggregates.

### Phase 7 — configurations and integration gates

1. Validate every document and record its identity.
2. Produce plan-only inventories for owner review, including exact 24 HPO
   combinations, ten heads, calendar cuts, capital events, monitor set, and all
   required artifacts. No execution command is allowed before approval hashes
   are inserted.
3. Run a deterministic synthetic end-to-end test twice from fresh directories;
   compare model-bundle hash, targets, fills, ledger head, account state, and
   metrics byte-for-byte. Label it a plumbing test.
4. Run a crash/resume synthetic test and require the same terminal state.
5. Run one developmental replay using only sealed P16 development predictions
   ending 2025-10-16. Force `deployment_eligible=false`; its purpose is to find
   code/accounting defects, not estimate returns.
6. Only after the complete MIO/replay path passes review, run finalist HPO:
   24 common combinations x 10 leads = 240 tuning fits. Then run ten refits and
   publish the one bundle.
7. Freeze the entire pre-March procedure. Only then may the owner authorize the
   March-May confirmation/calibration phase. Freeze again before June 1.
8. Run the untouched June-August full-system simulation once. August remains
   the predeclared Test B subperiod, never a second tuning set.

## 7. Required artifacts

A successful build is incomplete without all of these:

- approved ADR and owner-updated Path references;
- exact HPO inventory JSON and SHA-256;
- per-lead complete trial ledger and selected-parameter record;
- one model-bundle joblib/pickle and one verified manifest;
- deterministic prediction checksum fixture;
- pinned deployable lead-cap artifact from approved confirmation evidence;
- pinned unit-conversion, `pi_upper`, scenario, cost, capital and monitoring
  policy artifacts;
- one hash-chained replay ledger with cash flows, decisions, fills, outcomes,
  exits and checkpoints;
- machine-readable metrics/report artifact plus concise human report;
- plan-only approval inventory, synthetic parity result, developmental replay
  result, and later lockbox result, each clearly labeled by evidence status.

## 8. Refusal and honesty rules

- No fixture or synthetic result is a backtest.
- No P16 fold or developmental replay is deployment evidence.
- No March-May or June-August row is read early, including for debugging,
  schema inspection, thresholds, or sample counts.
- No missing bundle field, cap, price, account value, fill, fee, or outcome gets
  a default that fabricates readiness.
- No scheduled production contribution increases capital before settlement.
- No external contribution is profit.
- No lead trades above its confirmed cap, even if a later lead looks strong.
- No performance monitor acts before its minimum evidence count.
- No alert-only treatment for identity, staleness, cap, parity, reconciliation,
  or solvency failures; those hard-stop.
- No automatic second HPO pass after seeing the final-HPO validation result.
- No real-money configuration or live routing; paper-only remains enforced.

## 9. Skeptical review assignments

All reviewers are fresh Claude Sonnet 5 agents and run sequentially.

1. **Architecture/tiering skeptic:** OOP boundaries, generic graduation,
   duplicate owners, config identity, imports, and file placement.
2. **Model-method skeptic:** HPO objective, common inventory, per-lead
   independence, standard-error rule, diagnostics, final refit, and leakage.
3. **Artifact skeptic:** pickle trust boundary, atomicity, manifest coverage,
   tamper tests, versions, schema/category order, and reproducibility.
4. **Calendar skeptic:** every read cut, embargo, known-at time, refit boundary,
   refresh cadence, and lockbox isolation.
5. **Capital/accounting skeptic:** initial state, deposits/overrides/settlement,
   exact PnL separation, TWR/MWR, restart idempotence, and solvency.
6. **MIO/guardrail skeptic:** unit conversion, uncertainty/scenario alignment,
   `pi_upper`, lead caps, costs, fills, risk constraints, and NO-GO behavior.
7. **Replay/production-parity skeptic:** same graph/events/reducers, causal
   execution, crash recovery, reconciliation, and divergent-code detection.
8. **Observability skeptic:** metric completeness, denominator/units, label
   cardinality, minimum sample gates, alerts, and swallowed-failure counters.
9. **Final hostile skeptic:** the complete latest diff, configs, tests, plans,
   and evidence claims. Repeat with fresh reviewers until two clean rounds.

## 10. Focused verification

Add new tests beside their owners. Proposed new test files require ADR/owner
approval together with the implementation files.

- `tests/pipeline/test_kinds_search.py`
- `tests/pipeline_libs/test_sklearn.py`
- `tests/production/test_cashflows.py` (new if `cashflows.py` is approved)
- `tests/production/test_{compose,state,report,loop,feed,clock,executor}.py` as
  touched; never run unrelated production modules merely for volume
- `children/intraday_equities/tests/test_final_model.py` (new)
- `children/intraday_equities/tests/test_forecast_bundle.py` (new)
- `children/intraday_equities/tests/test_replay.py` (new)
- `children/intraday_equities/tests/test_nodes_capital.py`
- `children/intraday_equities/tests/test_model_zoo.py`
- `children/intraday_equities/tests/test_configs.py`
- Ruff only over touched Python paths; `git diff --check`; config validate and
  plan commands for the touched documents

The implementer records the exact commands and counts in the final memo. Do not
run the full repository suite unless the owner later asks.

## 11. Owner decisions still open — hard stops

The owner said the discussion is not finished. No agent may infer these.

1. The statistical unit and dependence-robust calculation for the locked
   one-standard-error HPO rule, plus the exact simplicity ordering.
2. The first biweekly Friday anchor date; holiday/non-business-day treatment;
   and whether a 09:30 contribution is available before or after a decision at
   exactly 09:30.
3. The inverse-label reference policy: how predicted vol-scaled SPY residuals
   become gross returns at each lead without future information.
4. The March-May ordering and minimum evidence for mean confirmation, cap
   confirmation, `pi_upper`, expected-alpha uncertainty, and joint scenario
   calibration. Data used to fit an uncertainty product cannot also be its
   untouched test.
5. First replay horizon policy: h1-only/non-overlapping as the memo recommends,
   or mixed confirmed caps; precise exit/expiry and overlap semantics.
6. Fill model: order type, decision/fill bar, latency, partial fills,
   rejections, spread/slippage, halts, forced exits, and mark source.
7. Performance-monitor minimum counts, windows, thresholds, and when a warning
   becomes a hold. Hard-stop invariant categories are already locked.
8. Every still-open MIO policy in `docs/plans/2026-09-intraday-equities-mio.md`
   §10: HFDR `q`, `U_pi`, contribution-aware Kelly schedule, opportunity cost,
   CVaR, cardinality, ticket/band/reserve/exposure/buying-power policy,
   `U_mu`, and cash-versus-margin account.
9. Broker, tax, settlement, PDT, wash-sale, and live account rulings. These
   cannot be settled by code and paper-only remains mandatory.
10. Numeric reporting/alert requirements beyond the metric catalogue in §6.

## 12. Owner-only Path update packet

Agents must not edit `docs/decisioning/path.csv`. The owner should add or amend
rows with repository-assigned IDs using these contents:

- **Final predictive model family and mask** — pooled LightGBM,
  `lean-pooled-h10`, exact P16 33-column drop mask; evidence P16 memo/config;
  `LOCKED=Y`; empirical.
- **Final HPO and refit protocol** — ten independent leads, common 24-combo
  inventory, per-lead outer-aligned accuracy, one-SE simplicity, refit through
  2026-02-28, 63-day refresh; this plan + final-HPO/refit configs; `LOCKED=N`
  until §11.1 is ruled, then `Y`; judgemental.
- **Model release artifact** — one ten-head pickle/joblib plus verified readable
  manifest/checksum; this plan + eventual release artifact; `LOCKED=Y`;
  judgemental.
- **Capital funding policy** — $10,000 initial USD, $500 biweekly Friday 09:30
  New York, recurring rule with dated overrides, production settlement-only,
  external flow excluded from PnL; `LOCKED=N` until §11.2 is ruled, then `Y`;
  judgemental.
- **Replay/production parity** — one pipeline decision graph, event ledger, and
  metric reductions; only boundary adapters differ; `LOCKED=Y`; judgemental.
- **Guardrail response policy** — hard-stop identity/staleness/cap/parity/
  reconciliation/solvency; evidence-qualified performance drift warns or
  holds; `LOCKED=N` until §11.7 thresholds are ruled, then `Y`; judgemental.
- Preserve A18256 as the locked fail-closed final MIO-bundle contract and add
  this plan plus the eventual publisher artifact to its relevant files. Do not
  mark its implementation complete until a real bundle exists.

## 13. Definition of done

The implementation is done only when the approved documents can produce a
verified ten-head release, a real unit-correct forecast bundle, confirmed lead
caps, stateful historical fills/accounting, declared cash flows, MIO decisions,
and identical replay/paper metric reductions; all focused tests pass; two final
Sonnet 5 skeptic rounds are clean; the owner-approved plan-only inventory is
pinned; `docs/RE-ENTRY.md`, package trees, journal and final memo are current;
and the coherent reviewed commits are merged and pushed.
