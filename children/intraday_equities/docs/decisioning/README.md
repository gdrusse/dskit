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
| A18719 | execute | p14-recurrent-fusion-zoo-cache-e bounded walk-forward | 2026-09-06T15:28:22+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p14-recurrent-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p14-recurrent-fusion-zoo-cache-e-walkforward-2026-02-28-2d46b625 |  | state=ran folds=1 hash=2d46b625 asof=2026-02-28; fold_processes=isolated; fold_workers=1; memory_limit_bytes=18253611008 |
| A18720 | execute | staged memory | 2026-09-06T15:28:23+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p14-recurrent-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p14-recurrent-fusion-zoo-staged-2026-02-28-075e6ab9/stages/memory.json |  | stage_token=075e6ab952497a38927895b28eebec181b4d2b4111be176fbe7f8482f42b37c0:memory; state=ran; sha256=95d9f9ffbe8116d22cdf91ce34399da00e1053b80a0a9b74f04dd23cdb5479f7; reason= |
| A18721 | execute | staged materialize | 2026-09-06T15:28:23+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p14-recurrent-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p14-recurrent-fusion-zoo-staged-2026-02-28-075e6ab9/stages/materialize.json |  | stage_token=075e6ab952497a38927895b28eebec181b4d2b4111be176fbe7f8482f42b37c0:materialize; state=ran; sha256=278501e214fde13efa0150f64bda3d87153e4e4fa232d8c967ea5a04f122f639; reason= |
| A18722 | execute | staged plan | 2026-09-06T15:28:24+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p14-recurrent-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p14-recurrent-fusion-zoo-staged-2026-02-28-075e6ab9/stages/plan.json |  | stage_token=075e6ab952497a38927895b28eebec181b4d2b4111be176fbe7f8482f42b37c0:plan; state=ran; sha256=f3f6063c40a920791ce163beb3862d9fffdc550f9aebb4655834e8c14d099191; reason= |
| A18723 | execute | staged approval | 2026-09-06T15:28:24+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p14-recurrent-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p14-recurrent-fusion-zoo-staged-2026-02-28-075e6ab9/stages/approval.json |  | stage_token=075e6ab952497a38927895b28eebec181b4d2b4111be176fbe7f8482f42b37c0:approval; state=ran; sha256=3502d9c3597b504a4c934c521f2a693615905bd064c126b7b522962e256aa3e9; reason= |
| A18724 | execute | staged run | 2026-09-06T15:28:25+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p14-recurrent-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p14-recurrent-fusion-zoo-staged-2026-02-28-075e6ab9/stages/run.json |  | stage_token=075e6ab952497a38927895b28eebec181b4d2b4111be176fbe7f8482f42b37c0:run; state=ran; sha256=c992d0aa754f0113b132952587263d683def89ef94df7b9e26723ddc0321fc73; reason= |
| A18725 | execute | staged compare | 2026-09-06T15:28:25+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p14-recurrent-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p14-recurrent-fusion-zoo-staged-2026-02-28-075e6ab9/stages/compare.json |  | stage_token=075e6ab952497a38927895b28eebec181b4d2b4111be176fbe7f8482f42b37c0:compare; state=ran; sha256=502f5727539126526096d0e2f7d1df0468a4853b0494e0972e11bfb1bc519d3a; reason= |
| A18726 | research | zoo-feature-selection-and-hpo/2026-09-06-feature-selection | 2026-09-06T23:13:57+00:00 | Feature selection: mask families, never filter LightGBM, prune only the MLP | docs/research/zoo-feature-selection-and-hpo/2026-09-06-feature-selection.md | docs/research/zoo-feature-selection-and-hpo/2026-09-06-feature-selection.md |  |
| A18727 | research | zoo-feature-selection-and-hpo/2026-09-06-hpo-search-spaces | 2026-09-06T23:40:23+00:00 | HPO: add learning_rate, log-scale the rest, draw 24, pin one winner | docs/research/zoo-feature-selection-and-hpo/2026-09-06-hpo-search-spaces.md | docs/research/zoo-feature-selection-and-hpo/2026-09-06-hpo-search-spaces.md |  |
| A18728 | research | zoo-feature-selection-and-hpo/2026-09-06-synthesis | 2026-09-06T23:41:02+00:00 | Tune the pooled incumbent before pruning it; the inner holdout can now rank | docs/research/zoo-feature-selection-and-hpo/2026-09-06-synthesis.md | docs/research/zoo-feature-selection-and-hpo/2026-09-06-synthesis.md |  |

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
