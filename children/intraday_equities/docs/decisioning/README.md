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
| A18894 | acquire | register-source alpaca-sip-split-f | 2026-09-17T21:42:34+00:00 | --root ./ob --source  --stream  --mode | ac05b47a9d6a16eb9ed6d9288ab00d9ed7e0a84281a2e585c9eeb3aefc8730e3 | ./ob | connector=intraday_equities.connectors:AlpacaBars |
| A18895 | acquire | register-source alpaca-sip-split-g | 2026-09-17T21:42:35+00:00 | --root ./ob --source  --stream  --mode | eba189673ef074df995f9897b25842aac93563fef99e9ef95cf4aa02c5016a61 | ./ob | connector=intraday_equities.connectors:AlpacaBars |
| A18896 | acquire | register-source alpaca-sip-split-h | 2026-09-17T21:42:35+00:00 | --root ./ob --source  --stream  --mode | 872a8698a75f57a228685a00a29bfeb482eecfc2aad7a9311299ed8c658d8278 | ./ob | connector=intraday_equities.connectors:AlpacaBars |
| A18897 | acquire | register-source alpaca-sip-split-i | 2026-09-17T21:42:36+00:00 | --root ./ob --source  --stream  --mode | 9fc934e630d63328e752604226ffb3fb32daf573b5ac63bc7bcd200adb23c958 | ./ob | connector=intraday_equities.connectors:AlpacaBars |
| A18898 | acquire | register-source alpaca-sip-split-j | 2026-09-17T21:42:36+00:00 | --root ./ob --source  --stream  --mode | bb61d56889d791e7419ae49ce6d57c000a52f7140f8cb9fc71ce03d9832b3ddc | ./ob | connector=intraday_equities.connectors:AlpacaBars |
| A18899 | acquire | backfill alpaca-sip-split-f/bars | 2026-09-17T21:59:03+00:00 | --root ./ob --source alpaca-sip-split-f --stream bars --mode backfill | bdc1a38118d40ba2d7c589beb500174ef67f7d2753e17e976fa17a12fbf18025 | ./ob |  |
| A18900 | acquire | backfill alpaca-sip-split-g/bars | 2026-09-17T22:21:51+00:00 | --root ./ob --source alpaca-sip-split-g --stream bars --mode backfill | 576704e1e773090690d0169ed3000e4ff686799a8d0bfaf62da1b1e5cf875d93 | ./ob |  |
| A18901 | acquire | backfill alpaca-sip-split-h/bars | 2026-09-17T22:41:06+00:00 | --root ./ob --source alpaca-sip-split-h --stream bars --mode backfill | b0ffcfb3c16a642d2385879afdbeeadc71f38ae48c2e06e782bd3fc417710c4e | ./ob |  |
| A18902 | acquire | backfill alpaca-sip-split-i/bars | 2026-09-17T23:10:26+00:00 | --root ./ob --source alpaca-sip-split-i --stream bars --mode backfill | 9496e8cf8ce8127e0060f49f4ed48c78c228457172ecc44b3207736b5c7642fc | ./ob |  |
| A18903 | acquire | backfill alpaca-sip-split-j/bars | 2026-09-17T23:47:37+00:00 | --root ./ob --source alpaca-sip-split-j --stream bars --mode backfill | ca784ed215d595fa88015ab9aa554e77e6000f3a6b314abd40c7957dd89b80cd | ./ob |  |

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
