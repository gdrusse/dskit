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
| A54568 | execute | staged hpo_document | 2026-09-26T07:33:36+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-retrain-simulation.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/retrain-simulation-staged-2026-02-28-4bfcaaea/stages/hpo_document.json |  | stage_token=4bfcaaea8a2a45875b71dc556c08bf276483abbf10436c40968c52a560c121fc:hpo_document; state=ran; sha256=35c562a79da2b9b4393756165a5b6bbaf5836db4e9d8782052d0d14635f8e923; reason= |
| A54569 | execute | lean-pooled-h10-warmup-hpo walk-forward | 2026-09-26T08:04:08+00:00 | lean-pooled-h10-warmup-hpo | /home/russell/dskit/children/intraday_equities/pipeline_runs/lean-pooled-h10-warmup-hpo-walkforward-2026-02-28-7a2b4381 | /home/russell/dskit/children/intraday_equities/pipeline_runs/lean-pooled-h10-warmup-hpo-walkforward-2026-02-28-7a2b4381 | state=ran folds=1 hash=7a2b4381 asof=2026-02-28 |
| A54570 | execute | staged hpo | 2026-09-26T08:04:09+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-retrain-simulation.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/retrain-simulation-staged-2026-02-28-4bfcaaea/stages/hpo.json |  | stage_token=4bfcaaea8a2a45875b71dc556c08bf276483abbf10436c40968c52a560c121fc:hpo; state=ran; sha256=0757f67c0b89be2203f8fa4a1b335c06b19ce8087b5cd9a93ca18e77db815992; reason= |
| A54571 | execute | staged winners | 2026-09-26T08:04:11+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-retrain-simulation.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/retrain-simulation-staged-2026-02-28-4bfcaaea/stages/winners.json |  | stage_token=4bfcaaea8a2a45875b71dc556c08bf276483abbf10436c40968c52a560c121fc:winners; state=ran; sha256=3c434792a1668fa8856dc268286552f2f647ba0c1916a477b622f87ad1663698; reason= |
| A54572 | execute | staged walk_document | 2026-09-26T08:04:13+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-retrain-simulation.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/retrain-simulation-staged-2026-02-28-4bfcaaea/stages/walk_document.json |  | stage_token=4bfcaaea8a2a45875b71dc556c08bf276483abbf10436c40968c52a560c121fc:walk_document; state=ran; sha256=3643db773b725ee69df3fa9469277fcbf1bba990de5d17a80f2b65955bc109bd; reason= |
| A54573 | execute | lean-pooled-h10-minute-walk walk-forward | 2026-09-26T08:55:33+00:00 | lean-pooled-h10-minute-walk | /home/russell/dskit/children/intraday_equities/pipeline_runs/lean-pooled-h10-minute-walk-walkforward-2026-02-28-14aa945c | /home/russell/dskit/children/intraday_equities/pipeline_runs/lean-pooled-h10-minute-walk-walkforward-2026-02-28-14aa945c | state=ran folds=20 hash=14aa945c asof=2026-02-28 |
| A54574 | execute | staged walk | 2026-09-26T08:55:34+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-retrain-simulation.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/retrain-simulation-staged-2026-02-28-4bfcaaea/stages/walk.json |  | stage_token=4bfcaaea8a2a45875b71dc556c08bf276483abbf10436c40968c52a560c121fc:walk; state=ran; sha256=a4a55da0dd2065928c4499fe30da6871a09f9d1d24195ad851525b164808deea; reason= |
| A54575 | execute | staged inventory | 2026-09-26T08:55:36+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-retrain-simulation.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/retrain-simulation-staged-2026-02-28-4bfcaaea/stages/inventory.json |  | stage_token=4bfcaaea8a2a45875b71dc556c08bf276483abbf10436c40968c52a560c121fc:inventory; state=ran; sha256=e42bf7f5f40c17980f8c9ec76f74c8701687220cf778eca3268199a5dfe46fd4; reason= |
| A54576 | execute | staged gates | 2026-09-26T08:55:41+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-retrain-simulation.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/retrain-simulation-staged-2026-02-28-4bfcaaea/stages/gates.json |  | stage_token=4bfcaaea8a2a45875b71dc556c08bf276483abbf10436c40968c52a560c121fc:gates; state=ran; sha256=33756fe05c7ccaed7f4838d3a9e277bdc42f68ea159be988e09aa250b27ca62c; reason= |
| A54577 | research | mio-joint-policy/2026-09-26-evaluation-and-literature | 2026-09-26T13:47:05+00:00 | MIO policy evaluation and multi-period literature (ADR-0188 research) | docs/research/mio-joint-policy/2026-09-26-evaluation-and-literature.md | docs/research/mio-joint-policy/2026-09-26-evaluation-and-literature.md |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A2822 | Gate 1: stock modelability | Lock the stock-modelability selection gate | children/intraday_equities/pipeline_runs/p12-63-asset-modelability-staged-2026-02-28-2d203f5c/stages/gate1.json; children/intraday_equities/configs/program-calendar.json; docs/decisioning/framework.md | Y |  | execute | staged gate1 | empirical |  |
| A2850 | Gate 2: HFDR in MIO | Replace the retired Bonferroni screen with an MIO constraint on false-signal gross capital | docs/architecture/decision-log.md#ADR-0088; docs/decisioning/framework.md | Y |  | research | HFDR constrained in MIO | judgemental | docs/architecture/decision-log.md |
| A2851 | ~~Gate 3: shuffle refit~~ | Audit Gate-1 selections against a session-scramble refit null | docs/research/gate3-lower-compute-null-design.md; docs/architecture/decision-log.md#ADR-0089 | N | Investigating a faster valid shuffle-training solution | research | gate3-lower-compute-null-design | empirical | docs/research/gate3-lower-compute-null-design.md |
| A2887 | Gate 3: fail-fast scramble refit | Audit every Gate-1 passer against whole-session scramble refits, stopping at the first null that matches or beats the real result | docs/architecture/decision-log.md#ADR-0092; docs/architecture/decision-log.md#ADR-0093; docs/architecture/decision-log.md#ADR-0094; children/intraday_equities/docs/memos/p12-gate3-recovery-results.md; children/intraday_equities/pipeline_runs/p12-g3-recovery-staged-2026-02-28-a1f293a2/stages/gate3_recovery.json; children/intraday_equities/configs/program-calendar.json | Y |  | execute | Gate 3: fail-fast scramble audit over asset-local walks | empirical | docs/architecture/decision-log.md |
| A18039 | Per-signal pi estimator | Estimate calibrated posterior false-signal probability from out-of-fold and Gate-3 null evidence | docs/research/hfdr-mio-uncertainty/2026-09-05-local-fdr-pi.md; docs/architecture/decision-log.md#ADR-0088 | Y | Estimator build in flight (ADR-0149, generic dskit tier) | research | hfdr-mio-uncertainty/2026-09-05-local-fdr-pi | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-local-fdr-pi.md |
| A18040 | Conservative pi HFDR | Bound false-signal gross capital with conservative pi inside the MIO | docs/research/hfdr-mio-uncertainty/2026-09-05-conservative-pi-hfdr.md; docs/architecture/decision-log.md#ADR-0088 | Y | SUPERSEDED BY EVIDENCE: pi_upper withdrawn as a bound (measured coverage 53-84 pct vs 95 nominal) and renamed pi_widened; constraint input needs re-decision, see A18044 joint set | research | hfdr-mio-uncertainty/2026-09-05-conservative-pi-hfdr | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-conservative-pi-hfdr.md |
| A18041 | Mean-alpha confidence intervals | Estimate uncertainty in net conditional mean alpha under temporal dependence | docs/research/hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals.md; docs/architecture/decision-log.md#ADR-0088 | Y | Prerequisite for A18046 (U_mu); build in flight | research | hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-mean-alpha-intervals.md |
| A18042 | Realized-return uncertainty | Calibrate dependent predictive intervals and joint return scenarios separately from mean alpha | docs/research/hfdr-mio-uncertainty/2026-09-05-dependent-return-calibration.md; docs/architecture/decision-log.md#ADR-0088 | Y | Feeds A18047 scenarios -> tangent-plane objective and CVaR block; build in flight | research | hfdr-mio-uncertainty/2026-09-05-dependent-return-calibration | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-dependent-return-calibration.md |
| A18044 | Joint U_pi set | Represent joint uncertainty in false-signal probabilities for robust HFDR constraints | docs/research/hfdr-mio-uncertainty/2026-09-05-u-pi.md; docs/architecture/decision-log.md#ADR-0088 | Y | Build the joint set; per-signal pi_upper measured under-covering (53-84% vs 95% nominal), so A18040 alone is insufficient | research | hfdr-mio-uncertainty/2026-09-05-u-pi | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-u-pi.md |
| A18046 | Joint U_mu set | Represent joint uncertainty in expected net alpha for conservative optimization | docs/research/hfdr-mio-uncertainty/2026-09-05-u-mu.md; docs/architecture/decision-log.md#ADR-0088 | Y | Build the joint set from the A18041 whole-session cross-asset refits; budgeted (Gamma) not box worst-case so the three sets do not all bind at once; must not absorb realized shocks (those are U_r) | research | hfdr-mio-uncertainty/2026-09-05-u-mu | judgemental | docs/research/hfdr-mio-uncertainty/2026-09-05-u-mu.md |
| A18047 | Joint U_r scenarios | Represent dependent joint net-return outcomes for CVaR drawdown and Kelly risk | docs/research/hfdr-mio-uncertainty/2026-09-05-u-r.md; docs/architecture/decision-log.md#ADR-0088 | Y | Build AFTER A18042 is reviewed and corrected; scope against what A18042 actually delivers - likely a reduction to n_scenarios_max plus weights and provenance, not a second calibrator | research | hfdr-mio-uncertainty/2026-09-05-u-r | empirical | docs/research/hfdr-mio-uncertainty/2026-09-05-u-r.md |
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
