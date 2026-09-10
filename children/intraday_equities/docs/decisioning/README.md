# Decisioning

CSV is the store (`actions.csv`, `path.csv`). This README is **generated**
— do not edit it. Append a CSV row or run `python -m dskit.journal promote`.

## Process

Many things get tried. The Actions table is the full tape. Path to
Production is the owner-selected linear chain (a subset of those IDs).

```
acquire  →  research  →  execute  →  production
 pull         finding      fit          live
```

1. **Acquire** — `python -m dskit.onboarding` `register-source` /
   `acquire --mode backfill|live` / `validate` / `certify` / `publish`.
   `watch` is one row per process, not per pull. **Automatic.**
2. **Research** — only
   `python -m dskit.journal research "TITLE" --topic T --name N --body-file <draft>`.
   Writes `docs/research/<topic>/<YYYY-MM-DD>-<name>.md` and the row
   together. Default name is `synthesis`. No markdown in the research
   root. Never write that folder by hand. Skills: `record-research`
   and `deep-research` (Cursor, Claude, OpenCode).
3. **Execute** — `python -m dskit.pipeline run|walkforward`.
   **Automatic** after RECORD. Walk-forward is one row, not per fold.
4. **Production** — wrap `live.main` in
   `dskit.journal.hooks.production`. One row per process, not per tick.

The ledger is CSV, not a database. **Database Location** is a pointer
to that action's artifacts (onboarding root, run dir, research file).
MLflow / the asset store hold their own records when used.

**Path to Production** is human-owner-only: only the owner may add or edit a
row, including **Current Work**. Agents and hooks never write it. Every row
has a short label, purpose, relevant evidence files (pipeline run, research
markdown, or other material evidence), and **LOCKED** (`Y` / `N`). Pytest
does not record. A child without `journal.json` refuses acquire / run / live.

## Actions (latest 10)

Display only: `actions.csv` remains the complete, append-only journal.

| ID | Category | Step | Execution Date | Relevant Inputs | Relevant Outputs | Database Location | Notes |
|---|---|---|---|---|---|---|---|
| A18870 | execute | Gate 5 cycle-2: fail-closed tape/halt/knobs (skeptic round 1) | 2026-09-10T13:20:29+00:00 | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture.md | intraday_equities/replay.py; tests/test_replay.py; configs/run-development-replay.json; ../../docs/architecture/decision-log.md |  | TDD. Observable refusals (unknown_symbol, fill_bar_past_tape, expiry_past_tape, lead). Halt skips exits+entries, == compare, catch-up on next non-halt. Knobs read (fill_price_field, overlap/expiry/halt/order). ReplayClock not TestClock. Honest: not ServeLoop. ADR-0117 still proposed. test_replay 22 passed. path.csv untouched. |
| A18871 | execute | Gate 5 skeptic round 2 FAIL (method+architecture) | 2026-09-10T13:38:07+00:00 | 358cba6; cycle-1 memos | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle2.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle2.md |  | Grok re-review. Method FAIL 1C/2M (unbounded fill tape; halt types; duplicate asof). Architecture FAIL 1C same engine. path.csv untouched. |
| A18872 | execute | Gate 5 cycle-3: bound tape, bool halt, unique asof | 2026-09-10T13:39:47+00:00 | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle2.md | intraday_equities/replay.py; tests/test_replay.py |  | Fill-only window is evidence_end+2 UTC midnights. Halt flag must be JSON bool. Duplicate asof_ms refuses. test_replay 23 passed. Architecture ServeLoop gap unchanged. path.csv untouched. |
| A18873 | execute | Gate 5 skeptic round 3 FAIL; escalate (no cycle 4) | 2026-09-10T13:53:22+00:00 | abdb71f; cycle-2 memos | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle3.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle3.md |  | Grok round 3. Method FAIL 1C/1M (fill-only +1 UTC day too short for large last-day leads; np.True_). Architecture FAIL 1C third time: private bar-walk is not ServeLoop. Escalating: two+ consecutive rounds with fresh method bugs; architecture Critical unchanged three times and needs an owner-authorized serve document. No cycle-4 grind. path.csv untouched. |
| A18874 | execute | Gate 5 cycle-4: ServeLoop compose + graded fill suffix | 2026-09-10T15:30:54+00:00 | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle3.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle3.md; ADR-0117 proposed | intraday_equities/replay.py; tests/test_replay.py; configs/fill-policy.json; configs/run-development-replay.json; ../../docs/architecture/decision-log.md |  | TDD. ServeLoop+ReplayFeed+shared ReplayClock+JsonlLedger; LegPipeline submits PaperExecutor. fill_suffix_bars/weekdays graded; halt queue and same-lead override dispatch; numpy bools coerced to JSON bools. Identity hash 5adac27e.... ADR-0117 still proposed. test_replay 29 passed; test_nodes_capital passed; test_configs 5 documented Gate 2 failures. ruff clean. path.csv untouched. |
| A18875 | execute | Gate 5 cycle-5: bundles_for + cycle-4 method holes | 2026-09-10T16:25:02+00:00 | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle4.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle4.md; ADR-0117 proposed | intraday_equities/replay.py; tests/test_replay.py; ../../docs/architecture/decision-log.md |  | TDD. compose.bundles_for(tape=BarTape); overlay Cadence/decider/ReleaseIdSource/PaperExecutor. Override exits, halt-queue past tape, close-field exit 12.5, missing open/string qty refuse. ADR-0117 still proposed. test_replay+nodes_capital 91 passed. Identity 5adac27e.... path.csv untouched. |
| A18876 | execute | Gate 5 skeptic cycle 5: method FAIL 0C/1M, architecture PASS 0C/0M | 2026-09-10T19:48:48+00:00 | 673377e; A18875; cycle-4 memos | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle5.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle5.md |  | Sequential Grok. Method: cycle-4 M1-M4 closed; Major is Tick failed InvalidOperation on halted open=None/empty not _fault, last-tick peer fills vanish. Architecture PASS 0C/0M 8 nits (lying fixed-interval document is Nit). path.csv untouched. |
| A18877 | execute | Gate 5 cycle-6: failed-tick refuse + halt-skip quotes | 2026-09-10T19:51:38+00:00 | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle5.md | intraday_equities/replay.py; tests/test_replay.py |  | TDD. Skip quoting halted bars. Wrap ArithmeticError. Raise on ledger tick status failed or leftover queued fills. test_replay+nodes_capital 93 passed. ADR-0117 still proposed. path.csv untouched. |
| A18878 | execute | Gate 5 skeptic cycle 6: method FAIL 0C/1M, architecture PASS 0C/0M | 2026-09-10T20:20:21+00:00 | 5a0c89e; A18877; cycle-5 memos | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle6.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle6.md |  | Sequential Grok. Method: cycle-5 None/empty closed; Major is tick refused on non-finite open green-emptying peer. Architecture PASS 0C/0M. path.csv untouched. |
| A18879 | execute | Gate 5 cycle-7: refuse non-finite open before loop | 2026-09-10T20:22:01+00:00 | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle6.md | intraday_equities/replay.py; tests/test_replay.py |  | TDD. number_ok/Decimal.is_finite at ingest; raise on tick refused as well as failed. Halt None/empty still skip quotes. 94 passed. ADR-0117 still proposed. path.csv untouched. |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A2822 | Gate 1: stock modelability | Lock the stock-modelability selection gate | children/intraday_equities/pipeline_runs/p12-63-asset-modelability-staged-2026-02-28-2d203f5c/stages/gate1.json; children/intraday_equities/configs/program-calendar.json; docs/decisioning/framework.md | Y |  | execute | staged gate1 | empirical |  |
| A2850 | Gate 2: HFDR in MIO | Replace the retired Bonferroni screen with an MIO constraint on false-signal gross capital | docs/architecture/decision-log.md#ADR-0088; docs/decisioning/framework.md | Y |  | research | HFDR constrained in MIO | judgemental | docs/architecture/decision-log.md |
| A2851 | ~~Gate 3: shuffle refit~~ | Audit Gate-1 selections against a session-scramble refit null | docs/research/gate3-lower-compute-null-design.md; docs/architecture/decision-log.md#ADR-0089 | N | Investigating a faster valid shuffle-training solution | research | gate3-lower-compute-null-design | empirical | docs/research/gate3-lower-compute-null-design.md |
| A2887 | Gate 3: fail-fast scramble refit | Audit every Gate-1 passer against whole-session scramble refits, stopping at the first null that matches or beats the real result | docs/architecture/decision-log.md#ADR-0092; docs/architecture/decision-log.md#ADR-0093; docs/architecture/decision-log.md#ADR-0094; children/intraday_equities/docs/memos/p12-gate3-recovery-results.md; children/intraday_equities/pipeline_runs/p12-g3-recovery-staged-2026-02-28-a1f293a2/stages/gate3_recovery.json; children/intraday_equities/configs/program-calendar.json | Y |  | execute | Gate 3: fail-fast scramble audit over asset-local walks | empirical | docs/architecture/decision-log.md |
| A18039 | Per-signal pi estimator | Estimate calibrated posterior false-signal probability from out-of-fold and Gate-3 null evidence | docs/research/hfdr-mio-uncertainty/2026-09-05-local-fdr-pi.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-local-fdr-pi | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-local-fdr-pi.md |
| A18040 | Conservative pi HFDR | Bound false-signal gross capital with conservative pi inside the MIO | docs/research/hfdr-mio-uncertainty/2026-09-05-conservative-pi-hfdr.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-conservative-pi-hfdr | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-conservative-pi-hfdr.md |
| A18041 | Mean-alpha confidence intervals | Estimate uncertainty in net conditional mean alpha under temporal dependence | docs/research/hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals.md |
| A18042 | Realized-return uncertainty | Calibrate dependent predictive intervals and joint return scenarios separately from mean alpha | docs/research/hfdr-mio-uncertainty/2026-09-05-dependent-return-calibration.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-dependent-return-calibration | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-dependent-return-calibration.md |
| A18044 | Joint U_pi set | Represent joint uncertainty in false-signal probabilities for robust HFDR constraints | docs/research/hfdr-mio-uncertainty/2026-09-05-u-pi.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-u-pi | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-u-pi.md |
| A18046 | Joint U_mu set | Represent joint uncertainty in expected net alpha for conservative optimization | docs/research/hfdr-mio-uncertainty/2026-09-05-u-mu.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-u-mu | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-u-mu.md |
| A18047 | Joint U_r scenarios | Represent dependent joint net-return outcomes for CVaR drawdown and Kelly risk | docs/research/hfdr-mio-uncertainty/2026-09-05-u-r.md; docs/architecture/decision-log.md#ADR-0088 | N |  | research | hfdr-mio-uncertainty/2026-09-05-u-r | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-u-r.md |
| A18256 | Final model MIO bundle | Require every promoted final model to publish the complete versioned fail-closed MIO forecast bundle before capital eligibility | docs/research/hfdr-mio-uncertainty/2026-09-05-final-mio-forecast-bundle.md; configs/run-p13-model-zoo.json; docs/architecture/decision-log.md#ADR-0088 | Y |  | research | hfdr-mio-uncertainty/2026-09-05-final-mio-forecast-bundle | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-final-mio-forecast-bundle.md |
| A18623 | Predictive program calendar | Lock one temporal source of truth for modelability, model-zoo selection, finalist HPO/refit, uncertainty calibration, simulation, and production | children/intraday_equities/configs/program-calendar.json; children/intraday_equities/docs/research/predictive-program-calendar/2026-09-05-synthesis.md; docs/architecture/decision-log.md#ADR-0098 | Y |  | research | predictive-program-calendar/2026-09-05-synthesis | empirical | docs/research/predictive-program-calendar/2026-09-05-synthesis.md |
| A12635 | ~~Official model zoo protocol~~ | Compare 13 individualized model families over all 25 Gate-3-approved asset-horizon pairs on one reviewed paired outer-fold protocol | children/intraday_equities/configs/run-p13-model-zoo.json; children/intraday_equities/configs/program-calendar.json; children/intraday_equities/docs/research/post-gate3-predictor-output/2026-09-05-synthesis.md; children/intraday_equities/docs/research/predictive-program-calendar/2026-09-05-synthesis.md; docs/architecture/decision-log.md#ADR-0097; docs/architecture/decision-log.md#ADR-0098; docs/architecture/decision-log.md#ADR-0099 | N | Superseded by the proposed pooled LightGBM/Torch-MLP zoo | research | post-gate3-predictor-output/2026-09-05-synthesis | empirical | docs/research/post-gate3-predictor-output/2026-09-05-synthesis.md |

## Evidence

Rationale files (not generated):

- [decision-framework-hpo.md](decision-framework-hpo.md)
- [decision-hl-scan.md](decision-hl-scan.md)
- [decision-horizon-criteria.md](decision-horizon-criteria.md)
- [decision-horizon-models.md](decision-horizon-models.md)
- [framework.md](framework.md)
- [hstar-go.md](hstar-go.md)
