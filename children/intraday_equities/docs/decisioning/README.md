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
| A18878 | execute | Gate 5 cycle-5: bundles_for + cycle-4 method holes | 2026-09-10T16:25:02+00:00 | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle4.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle4.md; ADR-0120 proposed | intraday_equities/replay.py; tests/test_replay.py; ../../docs/architecture/decision-log.md |  | TDD. compose.bundles_for(tape=BarTape); overlay Cadence/decider/ReleaseIdSource/PaperExecutor. Override exits, halt-queue past tape, close-field exit 12.5, missing open/string qty refuse. ADR-0120 still proposed. test_replay+nodes_capital 91 passed. Identity 5adac27e.... path.csv untouched. |
| A18879 | execute | Gate 5 skeptic cycle 5: method FAIL 0C/1M, architecture PASS 0C/0M | 2026-09-10T19:48:48+00:00 | 673377e; A18878; cycle-4 memos | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle5.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle5.md |  | Sequential Grok. Method: cycle-4 M1-M4 closed; Major is Tick failed InvalidOperation on halted open=None/empty not _fault, last-tick peer fills vanish. Architecture PASS 0C/0M 8 nits (lying fixed-interval document is Nit). path.csv untouched. |
| A18880 | execute | Gate 5 cycle-6: failed-tick refuse + halt-skip quotes | 2026-09-10T19:51:38+00:00 | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle5.md | intraday_equities/replay.py; tests/test_replay.py |  | TDD. Skip quoting halted bars. Wrap ArithmeticError. Raise on ledger tick status failed or leftover queued fills. test_replay+nodes_capital 93 passed. ADR-0120 still proposed. path.csv untouched. |
| A18881 | execute | Gate 5 skeptic cycle 6: method FAIL 0C/1M, architecture PASS 0C/0M | 2026-09-10T20:20:21+00:00 | 5a0c89e; A18880; cycle-5 memos | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle6.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle6.md |  | Sequential Grok. Method: cycle-5 None/empty closed; Major is tick refused on non-finite open green-emptying peer. Architecture PASS 0C/0M. path.csv untouched. |
| A18882 | execute | Gate 5 cycle-7: refuse non-finite open before loop | 2026-09-10T20:22:01+00:00 | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle6.md | intraday_equities/replay.py; tests/test_replay.py |  | TDD. number_ok/Decimal.is_finite at ingest; raise on tick refused as well as failed. Halt None/empty still skip quotes. 94 passed. ADR-0120 still proposed. path.csv untouched. |
| A18883 | execute | Gate 5 skeptic cycle 7 PASS (method 0C/0M, architecture 0C/0M) | 2026-09-10T20:49:41+00:00 | 5914052; A18882; cycle-6 memos | docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle7.md; docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle7.md |  | Sequential Grok. First independent round with 0 Critical and 0 Major. Method 4 minors (sticker decision_price_field; halt-last skip+refuse; np.int64 qty; unused-field nan via EntryBatch). Architecture 12 nits (placeholder fixed-interval document, reused digest, _TapeEntry never ticks, ledger.scan second reader, …). Nits/minors not edited (would restart the loop). ADR-0120 still proposed. path.csv untouched. |
| A18884 | execute | Gate 5 wrap: cycle-7 0C/0M, RE-ENTRY refreshed | 2026-09-11T01:04:34+00:00 | 5914052; A18883; cycle-7 skeptic memos | ../../docs/RE-ENTRY.md |  | /wrap. PR #10 into recovery branch, not main. ADR-0120 still proposed. path.csv untouched. Focused 94 passed. |
| A18885 | execute | Gates 6, 2, and 5 owner-approved reviewed closeout | 2026-09-11T15:07:24+00:00 | Gate 6 final skeptic 0C/0M; Gate 2 final skeptic 0C/0M; Gate 5 cycle 7 method and architecture 0C/0M | ../../docs/architecture/decision-log.md ADR-0118, ADR-0119, ADR-0120; ../../docs/RE-ENTRY.md |  | Owner authorized merge of passing skeptic-reviewed work. ADR-0120 accepted for development replay only; no deployment authorization. Integration renumbered collided append-only IDs without changing path.csv. |
| A18886 | execute | Gate 4 forecast bundle and confirmed-cap contracts | 2026-09-11T16:51:41+00:00 | docs/plans/2026-09-10-gate4-forecast-bundle-kickoff.md; ../../docs/architecture/decision-log.md ADR-0121 | intraday_equities/forecast_bundle.py; intraday_equities/nodes_capital.py; intraday_equities/testing.py; configs/run-mio-demo.json; tests/test_forecast_bundle.py; tests/test_nodes_capital.py; tests/test_configs.py; ../../docs/RE-ENTRY.md |  | TDD. 157 focused tests passed; validate/plan hash 49ac368b319e82ef71f46c52b4d28057d7d055393b0c500708be82fd54895eea; Ruff and diff check clean. Five unrelated config-policy baseline failures remain. Final review delegated to parent. No market data, HPO, refit, replay, full suite, or path.csv operation. |
| A18887 | execute | Gate 4 Terra round-1 correctness fixes | 2026-09-11T17:26:38+00:00 | 75acbfd; parent Terra correctness review | intraday_equities/forecast_bundle.py; intraday_equities/nodes_capital.py; intraday_equities/testing.py; configs/run-mio-demo.json; tests/test_forecast_bundle.py; tests/test_nodes_capital.py; tests/test_configs.py; ../../docs/architecture/decision-log.md ADR-0121; ../../docs/RE-ENTRY.md |  | TDD. Fixed log-to-simple conversion, distinct pi_hat mean recentering, hash-pinned cap provenance with deployment fail-closed, shared integer decision timestamps, and exact integer epoch validation. 175 focused tests passed; validate/plan hash b1727913b33fda2dcefb801a409ac0102f975cc919442ff86ff53c9a6edef025. No market data, HPO, refit, replay, full suite, or path.csv operation. Final independent rereview delegated to parent. |

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
