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
| A18615 | execute | p12-g3-recovery-gate3-seed18-anet-h03-part-17 walk-forward | 2026-09-05T21:25:15+00:00 | p12-g3-recovery-gate3-seed18-anet-h03-part-17 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed18-anet-h03-part-17-walkforward-2026-02-28-f45d0784 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed18-anet-h03-part-17-walkforward-2026-02-28-f45d0784 | state=ran folds=1 hash=f45d0784 asof=2026-02-28 |
| A18616 | execute | p12-g3-recovery-gate3-seed18-anet-h03-part-18 walk-forward | 2026-09-05T21:25:19+00:00 | p12-g3-recovery-gate3-seed18-anet-h03-part-18 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed18-anet-h03-part-18-walkforward-2026-02-28-a47bd8eb | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed18-anet-h03-part-18-walkforward-2026-02-28-a47bd8eb | state=ran folds=1 hash=a47bd8eb asof=2026-02-28 |
| A18617 | execute | p12-g3-recovery-gate3-seed18-anet-h03-part-19 walk-forward | 2026-09-05T21:25:20+00:00 | p12-g3-recovery-gate3-seed18-anet-h03-part-19 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed18-anet-h03-part-19-walkforward-2026-02-28-edc4d966 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed18-anet-h03-part-19-walkforward-2026-02-28-edc4d966 | state=ran folds=1 hash=edc4d966 asof=2026-02-28 |
| A18618 | execute | p12-g3-recovery-gate3-seed18-anet-h03 bounded walk-forward | 2026-09-05T21:25:23+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p12-gate3-continuation.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed18-anet-h03-walkforward-2026-02-28-9e6cb52b |  | state=ran folds=20 hash=9e6cb52b asof=2026-02-28; fold_processes=isolated; fold_workers=2; memory_limit_bytes=18253611008 |
| A18619 | execute | staged gate3_recovery | 2026-09-05T21:25:31+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p12-gate3-continuation.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-staged-2026-02-28-a1f293a2/stages/gate3_recovery.json |  | stage_token=a1f293a2508bdcef76656729b3cde045049623d4b257c0b2cc1cfd94607837ea:gate3_recovery; state=ran; sha256=098b21eaef6ee0260753d4f981ca2337bccae406b9efd394284d9b180ba03bd0; reason= |
| A18620 | execute | Correct P12 smoke artifact paths A2888-A2909 | 2026-09-05T21:32:23+00:00 | A2888-A2909; /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-walkforward-2026-02-28-ff6d5855 | /home/russell/dskit/children/intraday_equities/pipeline_runs | Append-only correction; historical rows unchanged. For A2888-A2908, replace only the removed /home/russell/wt/adr-0094 prefix with /home/russell/dskit; basenames and hashes are unchanged. Verified the main summary, all 20 part summaries, reports, walkforward records, and all 20 fold run directories exist. A2909 remains the non-decision smoke interpretation. |
| A18621 | execute | Record first P12 attempt exit 143 | 2026-09-05T21:32:24+00:00 | A2910-A2918; commit 905f32d; pid 286074 | /home/russell/p12-run.log | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-64-asset-modelability-staged-2026-02-28-03eb0907 | Append-only termination record: first P12 attempt started 2026-09-05T10:52:39Z and was terminated with exit 143 at 2026-09-05T11:07:39Z. A2910-A2918 are partial cache/memory evidence only; this is not a Gate 3 result. Historical rows and artifacts are unchanged. |
| A18622 | execute | P12 Gate 3 recovery complete | 2026-09-05T21:32:25+00:00 | A12579; A12580-A12596; A12618; A12619; A12731; A18619; configs/run-p12-gate3-continuation.json | docs/memos/p12-gate3-recovery-results.md; pipeline_runs/p12-g3-recovery-staged-2026-02-28-a1f293a2/stages/gate3_recovery.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-staged-2026-02-28-a1f293a2 | Recovery of A12579 (P12 exit 1 at 2026-09-05T16:09:05Z: NRG h1 seed05 part00 finished without journal evidence). Extracted/reconstructed from complete saved evidence: LLY3 QQQ1 XLF3 XLE1 XLK5 TQQQ3 NVDA2 UPRO60 BAC1 AVGO10 NFLX3 SMH5 IWM5 XBI1 FCX1 DAL1; per-family rows A12581-A12596 prove all 19 draws and source paths. Reran: NRG1 BA1 MET1 MSTR10 NOW5 LULU5 PANW5 INTC1 CIEN5 LRCX10 TER5 BIDU2 LITE5 ADBE5 ANET3. NRG replacement part00 is A12731. Fix commit cd4fb1c; recovery commit d1230ba; label-bound fix 47bcdaa. A12618 long-label attempt failed safely and remains untouched. Final A18619 SHA-256 098b21eaef6ee0260753d4f981ca2337bccae406b9efd394284d9b180ba03bd0: 25 pass, 6 fail, 285 main walks, 5700 part journals; focused artifact/journal verification passed. |
| A18623 | research | predictive-program-calendar/2026-09-05-synthesis | 2026-09-05T21:57:31+00:00 | Predictive program calendar and validation protocol | docs/research/predictive-program-calendar/2026-09-05-synthesis.md | docs/research/predictive-program-calendar/2026-09-05-synthesis.md |  |
| A18624 | research | post-gate3-predictor-output/2026-09-05-multi-horizon-selection | 2026-09-05T23:51:11+00:00 | Full-path zoo selection across horizons | docs/research/post-gate3-predictor-output/2026-09-05-multi-horizon-selection.md | docs/research/post-gate3-predictor-output/2026-09-05-multi-horizon-selection.md |  |

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
| A12635 | Official model zoo protocol | Compare 13 individualized model families over all 25 Gate-3-approved asset-horizon pairs on one reviewed paired outer-fold protocol | children/intraday_equities/configs/run-p13-model-zoo.json; children/intraday_equities/configs/program-calendar.json; children/intraday_equities/docs/research/post-gate3-predictor-output/2026-09-05-synthesis.md; children/intraday_equities/docs/research/predictive-program-calendar/2026-09-05-synthesis.md; docs/architecture/decision-log.md#ADR-0097; docs/architecture/decision-log.md#ADR-0098; docs/architecture/decision-log.md#ADR-0099 | Y |  | research | post-gate3-predictor-output/2026-09-05-synthesis | empirical | docs/research/post-gate3-predictor-output/2026-09-05-synthesis.md |

## Evidence

Rationale files (not generated):

- [decision-framework-hpo.md](decision-framework-hpo.md)
- [decision-hl-scan.md](decision-hl-scan.md)
- [decision-horizon-criteria.md](decision-horizon-criteria.md)
- [decision-horizon-models.md](decision-horizon-models.md)
- [framework.md](framework.md)
- [hstar-go.md](hstar-go.md)
