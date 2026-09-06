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
| A18756 | execute | P14 recurrent-fusion model-zoo result | 2026-09-06T18:35:19+00:00 | configs/run-p14-recurrent-fusion-zoo.json; pipeline_runs/p14-recurrent-fusion-zoo-staged-2026-02-28-70b5a399/stages/compare.json | docs/memos/p14-recurrent-fusion-model-zoo-results.md |  | 20/20 folds each; LSTM mean=0.001174414; GRU mean=-0.000416719; paired p=0.109186; no promotion or final refit; benchmark=70b5a399; compare_sha256=12b893e4706919018ecdf15837b4c4abc4acaaeea94c6f6afa9c36d70dacfe75 |
| A18757 | execute | staged calendar | 2026-09-06T19:07:15+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p15-temporal-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p15-temporal-fusion-zoo-staged-2026-02-28-362d94d5/stages/calendar.json |  | stage_token=362d94d5033e34484e69113064a3b003d7b6337ebc447812d3d2f3061e58ef16:calendar; state=ran; sha256=1fb7ebb9234ce5aa7c9d41499073dd6b192c8d12574efabb5ba4f28e848713de; reason= |
| A18758 | execute | staged memory | 2026-09-06T19:07:19+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p15-temporal-fusion-zoo.json |  |  | stage_token=362d94d5033e34484e69113064a3b003d7b6337ebc447812d3d2f3061e58ef16:memory; state=error; reason=ValueError: RUSAGE_CHILDREN.ru_maxrss is already 2668: this process has reaped a child, so a one-child memory reading is impossible here |
| A18759 | execute | p15-temporal-fusion-zoo-preflight-362d94d5-iwm-h01 walk-forward | 2026-09-06T19:08:57+00:00 | p15-temporal-fusion-zoo-preflight-362d94d5-iwm-h01 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p15-temporal-fusion-zoo-preflight-362d94d5-iwm-h01-walkforward-2026-02-28-1e1aae9a | /home/russell/dskit/children/intraday_equities/pipeline_runs/p15-temporal-fusion-zoo-preflight-362d94d5-iwm-h01-walkforward-2026-02-28-1e1aae9a | state=ran folds=1 hash=1e1aae9a asof=2026-02-28 |
| A18760 | execute | staged memory | 2026-09-06T19:08:58+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p15-temporal-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p15-temporal-fusion-zoo-staged-2026-02-28-362d94d5/stages/memory.json |  | stage_token=362d94d5033e34484e69113064a3b003d7b6337ebc447812d3d2f3061e58ef16:memory; state=ran; sha256=483866846a059d275b88b55c5d2a9dd92ab2071b419884de85984ca259fd8a60; reason= |
| A18761 | execute | staged materialize | 2026-09-06T19:08:59+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p15-temporal-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p15-temporal-fusion-zoo-staged-2026-02-28-362d94d5/stages/materialize.json |  | stage_token=362d94d5033e34484e69113064a3b003d7b6337ebc447812d3d2f3061e58ef16:materialize; state=ran; sha256=e015a2f6a047b048407072a18981af6c848315642988f5e9e684abe1552cff07; reason= |
| A18762 | execute | staged plan | 2026-09-06T19:08:59+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p15-temporal-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p15-temporal-fusion-zoo-staged-2026-02-28-362d94d5/stages/plan.json |  | stage_token=362d94d5033e34484e69113064a3b003d7b6337ebc447812d3d2f3061e58ef16:plan; state=ran; sha256=93936c7d6552e1960aaaba201f0a31b5061605d0eb3b5898f1660cd7186f6e23; reason= |
| A18763 | execute | staged approval | 2026-09-06T19:09:00+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p15-temporal-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p15-temporal-fusion-zoo-staged-2026-02-28-362d94d5/stages/approval.json |  | stage_token=362d94d5033e34484e69113064a3b003d7b6337ebc447812d3d2f3061e58ef16:approval; state=ran; sha256=6c78cff13df308abb33587b8437d876bb238a43d8178ab1f53272300f38d7338; reason= |
| A18764 | execute | staged run | 2026-09-06T19:09:01+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p15-temporal-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p15-temporal-fusion-zoo-staged-2026-02-28-362d94d5/stages/run.json |  | stage_token=362d94d5033e34484e69113064a3b003d7b6337ebc447812d3d2f3061e58ef16:run; state=ran; sha256=15c04b33e6434ecd197beb02f2d6a644913925a5f1f1c6401d0636a6f9b6a6a9; reason= |
| A18765 | execute | staged compare | 2026-09-06T19:09:01+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p15-temporal-fusion-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p15-temporal-fusion-zoo-staged-2026-02-28-362d94d5/stages/compare.json |  | stage_token=362d94d5033e34484e69113064a3b003d7b6337ebc447812d3d2f3061e58ef16:compare; state=ran; sha256=d7be1209b9300185bea2b2d4b0c095b8451328ca961db13fee8bc4f981f25858; reason= |

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
