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
| A54542 | execute | staged plan | 2026-09-21T17:53:12+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p19-expanded-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p19-expanded-pooled-model-zoo-staged-2026-02-28-70202437/stages/plan.json |  | stage_token=702024373dadecce9d52b972766902723701b757db49f0a80f226aa4e6043ef9:plan; state=ran; sha256=fa166f86349ae2a0973f0ed96c1bc7e14aca4027a47a12f5d2af9469550cefec; reason= |
| A54543 | execute | staged approval | 2026-09-21T17:53:14+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p19-expanded-pooled-model-zoo.json |  |  | stage_token=702024373dadecce9d52b972766902723701b757db49f0a80f226aa4e6043ef9:approval; state=error; reason=ValueError: approved inventory hash changed: 0000000000000000000000000000000000000000000000000000000000000000 -> 931e846d6a6ba679c0110ae55048c5e32baa882f164ebeb37bb1bc9f71b158bd |
| A54544 | execute | staged calendar | 2026-09-21T18:13:26+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p19-expanded-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p19-expanded-pooled-model-zoo-staged-2026-02-28-f57beb46/stages/calendar.json |  | stage_token=f57beb463943e443e8751d4cde791bf8c2697736e5bb4a3f204355dad7fd1f60:calendar; state=ran; sha256=0f209cbdec739f3f76d7f6c7d4cda8bfb265c72fd60fa3d21f60205dd052cc9f; reason= |
| A54545 | execute | p19-expanded-pooled-model-zoo-preflight-f57beb46-iwm-h01 walk-forward | 2026-09-21T18:13:37+00:00 | p19-expanded-pooled-model-zoo-preflight-f57beb46-iwm-h01 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p19-expanded-pooled-model-zoo-preflight-f57beb46-iwm-h01-walkforward-2026-02-28-5e9526be | /home/russell/dskit/children/intraday_equities/pipeline_runs/p19-expanded-pooled-model-zoo-preflight-f57beb46-iwm-h01-walkforward-2026-02-28-5e9526be | state=ran folds=1 hash=5e9526be asof=2026-02-28 |
| A54546 | execute | staged memory | 2026-09-21T18:13:40+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p19-expanded-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p19-expanded-pooled-model-zoo-staged-2026-02-28-f57beb46/stages/memory.json |  | stage_token=f57beb463943e443e8751d4cde791bf8c2697736e5bb4a3f204355dad7fd1f60:memory; state=ran; sha256=bf28329a66bf9c7947ab53461fd76dbe9c4b9ac19761cef712b548394697bfc3; reason= |
| A54547 | execute | staged materialize | 2026-09-21T18:13:42+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p19-expanded-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p19-expanded-pooled-model-zoo-staged-2026-02-28-f57beb46/stages/materialize.json |  | stage_token=f57beb463943e443e8751d4cde791bf8c2697736e5bb4a3f204355dad7fd1f60:materialize; state=ran; sha256=5f1664bb548751080111369db89d4b3ae8e9a289609aaa089d2745ddea80f07c; reason= |
| A54548 | execute | staged plan | 2026-09-21T18:13:48+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p19-expanded-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p19-expanded-pooled-model-zoo-staged-2026-02-28-f57beb46/stages/plan.json |  | stage_token=f57beb463943e443e8751d4cde791bf8c2697736e5bb4a3f204355dad7fd1f60:plan; state=ran; sha256=fb101e78dc1d6c232b0c2b57d7f136022be48a829d15da7cfb7abc0af5684499; reason= |
| A54549 | execute | staged approval | 2026-09-21T18:13:49+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p19-expanded-pooled-model-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p19-expanded-pooled-model-zoo-staged-2026-02-28-f57beb46/stages/approval.json |  | stage_token=f57beb463943e443e8751d4cde791bf8c2697736e5bb4a3f204355dad7fd1f60:approval; state=ran; sha256=a79fc24ca47daabb4c9cd0db121bdc2af3a79f7b9581e4e21cb2ef35e8f63d81; reason= |
| A54550 | execute | p19-eligible-fixed-model-zoo walk-forward | 2026-09-21T21:05:45+00:00 | p19-eligible-fixed-model-zoo | /home/russell/dskit/children/intraday_equities/pipeline_runs/p19-eligible-fixed-model-zoo-walkforward-2026-02-28-d4e9d8dd | /home/russell/dskit/children/intraday_equities/pipeline_runs/p19-eligible-fixed-model-zoo-walkforward-2026-02-28-d4e9d8dd | state=error folds=1 hash=d4e9d8dd asof=2026-02-28 |
| A54551 | execute | replay-report-sanity | 2026-09-24T03:21:50+00:00 | replay-report-sanity | /home/russell/wt/backtest-evaluator/children/intraday_equities/pipeline_runs/replay-report-sanity-2026-09-24-bbc7a9b9 | /home/russell/wt/backtest-evaluator/children/intraday_equities/pipeline_runs/replay-report-sanity-2026-09-24-bbc7a9b9 | state=ran hash=bbc7a9b9 asof=2026-09-24 |

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
