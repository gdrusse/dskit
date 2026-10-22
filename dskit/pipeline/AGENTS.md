Default answer: outcome first, max 5 lines. Expand only if I ask.

# AGENTS.md — dskit.pipeline

Orientation for an agent working inside this package. Read the package
[README.md](README.md) first for what it does; this file is how to work
on it without breaking its rulings.

## Conventions

- **Errors accumulate.** Validators append to a list; one `ConfigError`
  reports every problem. Never raise on the first.
- **An invalid object can never exist** — validation in `__post_init__`;
  every `from_obj` rejects unknown keys (default-deny).
- **The canonical hash recipe is pinned** (sorted keys, compact, ASCII,
  NaN refused, `notes` stripped at every level). `dskit.assets` asserts
  byte-parity with it — never "improve" the recipe.
- **Two grammars coexist.** The node-map document (`document.py`,
  `driver.py`) is current; the stage-list config (`base.py`,
  `runner.py`) is the predecessor, unchanged while the migration
  completes. Know which one a symbol belongs to before editing.
- **Roles are declared by the class**, never by the config; planner
  rules attach to roles. New rules go in `planner.py`, not in nodes.

## Extension points and gotchas

Both live in [EXTENDING.md](EXTENDING.md): every registry and doorway
(node kinds, trainable kinds, library packs, metrics, split policies, fitted
transforms, feature selectors, fold execution, benchmarks, search values,
bundles, run attestation, uncertainty intake and sets, conformance) and the
rulings that bite when you extend them. Open it before adding a seam.

## Contents

```
dskit/pipeline/
├── __init__.py        public surface; auto-registers the default kinds
├── __main__.py        the CLI: python -m dskit.pipeline
├── document.py        PipelineDocument / NodeSpec / ROLES + MODES / splits + walkforward
│                      + foreach (ADR-0039: stores what was written, derives what runs) / refs
├── node.py            Node + TrainableNode ABCs, NodeContext, registry, register_node_kind
├── planner.py         document -> Plan; role rules live here
├── driver.py          run_document: LOAD..RECORD, $prev, journal hook
│                      (ADR-0056); run_walk_forward (ADR-0027);
│                      RunAttestation + content_identity (ADR-0119);
│                      attested_output + row_set_identity (ADR-0166)
├── stages.py          journal-backed staged DAG execution and resume (ADR-0081)
├── event_wire.py      closed shared raw-event v1/v2 wire declarations (ADR-0169)
├── trust.py           opaque capture handles + WORM lifecycle (ADR-0122/0123 F4)
├── workflow.py        manifest runner: chained pipeline steps from neutral templates (ADR-0227)
├── workflow_hooks.py  study strategies: candidate generators, stage sequence, collectors, no-gain rule (ADR-0229)
├── workflow_report.py verified report from a workflow ledger: md + html + csv, refuses on hash mismatch
├── workflow_batch.py  workflow-batch: one workflow process per lane, parallel, retry, resume, batch.json
├── workflow_rollup.py cross-lane table (csv + sortable html): skill intervals, verdict, failures
├── benchmarks.py      JSON model-zoo plan/run/paired-compare stages (ADR-0097)
├── conquest.py        HorizonConquest: per-(unit,horizon) contiguous cap over
│                      config-declared checks + slice stability (ADR-0107)
├── folds.py           BoundedFoldRunner: capped, width-bounded fold spawning; declared_width;
│                      spawn is the hook, measure_one the one reading (ADR-0093)
├── runs.py            the READER: scan_runs/format_runs over a run root (`runs` verb)
├── predictions.py     every scored validation row -> one parquet per run (ADR-0064)
├── ordering.py        calibration slope + per-timestamp cross-sectional IC,
│                      with the <5-name usability refusal (ADR-0068, `ordering` verb)
├── attempts.py        AttemptRegistry + session-block max_bar + tier-2 seam
│                      (ADR-0069, `bar` verb)
├── outcome_interval.py block-conformal predictive intervals + joint scenario sets
│                      over dependent residual vectors (ADR-0155)
├── split_policy.py    split policies (record/event-open/event-close) + EventBounds
├── kinds_flow.py      filter, event-grid, derive, concat, join, groupby, keyby,
│                      weekday-onehot, attach-by-identity — flow verbs (keyby: records ->
│                      the keyed side table join reads; weekday-onehot: ISO date -> 0/1
│                      weekday columns)
├── kinds_banking.py   event-bank, eligibility, banking-report — the ★BANKING
│                      accrual -> gate -> ledger spine
├── kinds_split.py     holdout-cut, rolling-origin-plan — the evaluation protocol:
│                      holdout locked first, count-sized rolling folds, warm-up vs
│                      scored roles; label_reaches is the one purge rule (ADR-0215)
├── kinds_availability.py  family-availability (ADR-0226)
├── kinds_compute.py   ComputeFields (by import path, ADR-0265 Amendment 5): per-row arithmetic over declared fields, `keep` projection, text / list / item; null and
│                      out-of-domain results are None and counted
├── kinds_series.py    StampSeriesStats (by import path, ADR-0265 Amendment 5): stamped values to a per-group week / month series, Newey-West, Sharpe + calendar-block
│                      bootstrap interval, hurdle, paired / unpaired contrast
├── kinds_rank.py      TrailingRank: top-N entities by an aggregate over the trailing window (by import path)
├── kinds_run_write.py RecordsWriteRun (records-write-run, a RecordsWrite subclass): `{run}` in the file stem, `run_id` on every row (by import path, ADR-0247)
├── kinds_table.py     table-file, table-write, records-write (+ the FileWrite base, ADR-0085),
│                      horizon-pairs (dated close paired with the close H days later, ADR-0228;
│                      one series or one per `symbol_field`, ADR-0259)
├── kinds_stats.py     owned validate + stat_test; CrossSectionIC (by import path, ADR-0259):
│                      per-instant rank IC of named measures across names, a benchmark
│                      partialled out, split/month summaries, drop census; QuantileSortBook (by import
│                      path, ADR-0265): per-stamp quantile long-short book, cost per side, NW + bootstrap + BH
├── kinds_search.py    hpo-grid + top-trials (ctx.rerun seam);
│                      CandidateInventory, TrialLedger, OneStandardErrorSelector, SelectionRecord
│                      are generic search values (ADR-0113).
├── kinds_report.py    owned run-report
├── fitted.py          FittedTransform family: standardize, apply-transform,
│                      FeatureSelector
├── conformance.py     conformance_suite + NodeProbe
├── release_rotation.py pure ReleaseRotationCalendar values: explicit UTC
│                      anchor/cadence/training/embargo -> bounded pinned windows
├── synthetic_nodes.py demo/test nodes, private registries only
├── distribution_scores.py sample-set forecast scores: CRPS, threshold-weighted CRPS,
│                      Brier at thresholds, PIT KS, Berkowitz; ScoreDistributions node (ADR-0168)
├── distribution_models.py EmpiricalLocationScale: fitted standardized-label shape,
│                      relative_scale hook (ADR-0168); ScaleModelLocationScale +
│                      LinearScaleLocationScale (log-HAR) fitted-scale rungs (ADR-0181)
├── synthetic_paths.py SynthGjrPaths: seeded GJR-GARCH-t price path, known truth (ADR-0168)
├── metrics.py         logloss / brier / squared_error / absolute_error / pinball + register_metric
├── trainlog.py        TrainingCurve + probability metrics (declared-model telemetry)
├── stats.py           cluster bootstraps (plain, studentized-t) + correction
│                      registry; no-information vs mean (Clark–West, h*);
│                      P&L series summaries lower_tail_mean + max_drawdown;
│                      sharpe_ratio (n + CI), PSR, DSR, profit_factor, payoff_ratio;
│                      expanding_percentile (look-ahead-free mid-rank, ADR-0194) +
│                      expanding_quantile (look-ahead-free running quantile, linear
│                      interpolation, ADR-0196)
│                      NO_VARIANCE_RTOL: newey_west_mean / across_fold_t report t None
│                      for a spread within that fraction of the largest magnitude
│                      (ADR-0195; the ONE owner of the float-noise no-variance rule)
│                      CalendarBlockBootstrap + EdgePaddedCalendarBlockBootstrap:
│                      calendar-day block resampling, one `_blocks_for` hook (ADR-0258)
│                      sample_quantile: interpolated quantile of a whole sample (ADR-0265)
├── binary_pricing.py  AveragedLognormal law, Above / Below / Between payoffs, BinaryFairValue node (by import path, ADR-0246)
├── binary_curve.py   CurveSurvival + CurveBinaryFairValue: price a binary from any CDF curve's knots; row_key + fields_key, the one id/chain/right key rule: str or non-bool int scalars (by import path, ADR-0257)
├── digital_bounds.py executable vertical-spread bounds on a digital (DigitalBounds; per-edge proven float error, EDGE_MAX_ERROR, PRICE_FLOOR) + quote_problems (by import path, ADR-0257)
├── binary_decisions.py DecisionRows + QuoteState + the shared binary-market column names (by import path, ADR-0256)
├── binary_scoring.py  BucketedBinaryScore: model vs market, settlement-cut held-out gate, cluster-robust errors (by import path, ADR-0246)
├── fee_mechanics.py   venue-neutral fee mechanics (FeeModel / RoundingPolicy ABCs, CeilToTick, JSON spec); no venue table, ADR-0245
├── option_pricing.py black76 (European on a forward) + black76_delta (its
│                      forward delta, ADR-0193) + VolIndexSmileQuotes:
│                      proxy leg bid/ask from a vol-index close, IV clamped
│                      to [floor, ceiling], a smile that dips below zero refused
│                      (ADR-0182 tier placement); black76_implied_vol (safeguarded Newton, ADR-0265)
├── option_structures.py leg-set algebra (credit, payoff, max loss, quote_defect) graduated from
│                      the index_options child; names no structure (ADR-0265)
├── false_signal.py    pi_hat + a widened pi_widened per signal, from
│                      out-of-fold evidence and a scramble null (ADR-0152);
│                      pi_widened is a widened point estimate, not a bound
├── mean_interval.py   MeanEvidence + MeanIntervalEstimator: mean, dependence-
│                      aware SE, two-sided bounds; dependence never defaulted;
│                      ConfidenceInterval only where coverage was measured
├── uncertainty_set.py budgeted uncertainty sets: worst case over a set,
│                      robust counterpart, weighted realizations
├── uncertainty_intake.py  whether an artifact may inform THIS decision:
│                      attestation + demand, six refusals, admission_problems
│                      answering from the REGISTRY at use time, and a
│                      ProbabilityUpperBound family no intake answers
│                      (ADR-0165)
├── records.py         MarketRecord + accounting seams + WEEKDAY_TAGS / weekday_flags
├── protocols.py       structural Protocols
├── env.py             env + redacting Secrets
├── testing.py         SyntheticBackend, MemoryTracker, register_synthetic
├── base.py            stage-list grammar + config_hash + registries
├── runner.py          stage-list Runner (legacy)
├── features.py        stage-list stream transforms
├── io.py, resolve.py  stage-list load/save + resolution
├── registry.py        venue-backend registry (no venues ship)
├── libs/predictive_cdf.py offline conditional CDFs, pooled heads and JSON HPO (ADR-0189/0190);
│                      CurveMoments: a saved curve's analytic mean/sd onto its row (ADR-0259)
├── libs/              numpy, sklearn, torch + torch_ts (ADR-0041 zoo),
│                      transformers (+ the pretrained encode/classify/forecast
│                      trio over an acquired snapshot, ADR-0083), kronos
│                      (frozen causal session states, ADR-0102), optuna,
│                      pyomo, sb3, matplotlib, lightgbm (a fitted volatility-
│                      scale rung, ADR-0181),
│                      mlflow (tracking SINK pack, no nodes),
│                      observations (the `observations` data kind over the onboarding read seam, ADR-0077; ObservationUnitRows: member-order stream deduplicated by unit only, memory O(units), ADR-0265),
│                      parquet (DateBoundedParquet: projected date predicates, ADR-0248; ParquetRows, an onboarded parquet file as records, ADR-0228; ParquetFrameCache, a frame memo, ADR-0236 amendment),
│                      bar_features (daily-bar-features, trade-bar-features: volume/liquidity, market-relative and option-trade bar features per entity and date; underlying_close writes the moneyness denominator out, ADR-0259)
│                      observation_tables (observation-tables: keyed onboarded tables attached onto a stream, ADR-0226 amendment),
│                      parquet_series (ParquetSeries + prior_index + the stream-manifests node, ADR-0243),
│                      vol_estimators (VolEstimatorFeatures: rms / EWMA / high-low variance with a gap rule, by import path, ADR-0244),
│                      option_ledger (OptionStructureLedger: NBBO open, mid marks, settle/exit, optional Black-76 delta hedge, by import path, ADR-0265),
│                      array_panel, horizon_cdf, panel_score, panel_torch (the panel zoo: one memory-mapped panel, per-horizon CDF heads on one common grid, walk-forward lanes; by import path, ADR-0260; PanelHorizonFairValue prices a binary from a forecaster's CDF, ADR-0262)
│                      binary_market_rows (BinaryMarketRows / QuoteBarRows / FeeScheduleRows / FeeColumns, every field a param, ADR-0256),
│                      binary_coherence (BinaryCoherence: LP feasibility + 1/spread² projection across linked binaries, a PyomoSolve subclass, ADR-0257),
│                      chain_census (ChainCensus + quote_state: per-root coverage and quote quality of a daily option-chain stream, by import path, ADR-0265),
│                      chain_features (ChainDayFeatures: ATM/wing IV, skew, term, straddle, spread, OI/volume per symbol-date-DTE from NBBO mids, by import path, ADR-0265),
│                      chain_greeks (ChainDayPositioning: vendor EOD Greeks x open interest per symbol-day, per-Greek spot powers, OI/volume ratios, counted exclusions, by import path, ADR-0273; `profiles` strike-by-expiry buckets + ChainDayDeltaSurface: vendor IV at fixed vendor deltas, vega-weighted IV, theta/vega carry, ADR-0275),
│                      kalman_spread (KalmanSpreadState: random-walk level + AR(1) deviation per pair, parameters fitted on prior-only windows at declared fit dates, expected h-session change, by import path, ADR-0274),
│                      chain_events (ChainEventDetector + ChainEventCalibrator: earnings-like events from the front-IV crush in the chain-day features, no calendar; a retrospective labeller, by import path, ADR-0265)
│                      chain_parquet (ParquetChainRows: Parquet-tree source of the chain nodes), chain_panel (ChainPanelBuild: per-date panel, resumable per root), chain_reuse (ReuseDetector), root_stitch (RootStitch: renames + reuse breaks), by import path, ADR-0265 Amendment 3
│                      chain_orders (ChainStructureOrders: expiry + strike selection by atm / delta / max open interest / mirror from a daily chain, orders for the ledger, by import path, ADR-0265 Amendment 5); ParquetStreamRows takes `include` / `iso_fields` / `where`; DailyBarFeatures takes `realized_vol`; QuoteBook is chain_features' public quote book
│                      cdf_grid_methods (GridCDFMethod: one forecaster per fold on a declared CDF grid from a scored-rows parquet by pit_map / linear_pool / best_component / binary_gbm / beta_pool / quantile_average (level_bands, weight_features) / location_scale kinds, learning rules fit_band / prior_validation / rolling (refit_days blocks), optional half_life_days recency weights, components joined from other scored-rows files, grid twCRPS, paired tables with date and group clustered errors, by import path, ADR-0276)
├── README.md          user-facing docs
├── EXTENDING.md       extension points + gotchas (moved out of this file 2026-10-10)
└── AGENTS.md          this file
```

Keep both trees (here and in README.md) current when files change.

**ADR-0260 panel zoo gotchas.** One `min_run` serves every lane (>= the widest `(window-1)*stride+1`); `PanelScore`
refuses a model that declares another. Labels are stored as given, already divided by their scale; nothing divides
twice. Head wings / bounds equal the `CommonGrid` values (the grid refuses a mismatch). A lane is `PanelTrain ->
PanelScore` in a `walkforward` document; the unpooled regime is one lane per asset (a template cannot fan in).

ADR-0213: predictive_cdf.OptionPriceCDF owns the shared parity/isotonic proxy.
ExpiryCloseLabels and OptionCDFPanel expose default-deny JSON policies and
content-addressed options, labels, curves and panel artifacts. Provider trade
closes never masquerade as quotes; future/unverified outcomes remain null.
