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
   `python -m dskit.journal research "TITLE" --body-file <draft>`.
   Writes `docs/research/<slug>.md` and the row together. Never write
   that folder by hand. Skills: `record-research` (Cursor + Claude;
   Claude `/research`).
3. **Execute** — `python -m dskit.pipeline run|walkforward`.
   **Automatic** after RECORD. Walk-forward is one row, not per fold.
4. **Production** — wrap `live.main` in
   `dskit.journal.hooks.production`. One row per process, not per tick.

The ledger is CSV, not a database. **Database Location** is a pointer
to that action's artifacts (onboarding root, run dir, research file).
MLflow / the asset store hold their own records when used.

**Path to Production** is owner-only:
`python -m dskit.journal promote <ID> --criteria empirical|judgemental|n/a`.
Hooks never write it. Pytest does not record. A child without
`journal.json` refuses acquire / run / live.

## Actions

| ID | Category | Step | Execution Date | Relevant Inputs | Relevant Outputs | Database Location | Notes |
|---|---|---|---|---|---|---|---|
| A0001 | acquire | alpaca SIP backfill | 2026-08-31T00:00:00+00:00 | configs/source-alpaca-backfill.json | ob/observations/alpaca-sip | ob | retrospective; artifacts may be incomplete |
| A0002 | acquire | schwab live source | 2026-08-31T00:00:00+00:00 | configs/source-schwab-live.json | ob/observations/schwab | ob | retrospective; artifacts may be incomplete |
| A0003 | execute | hl-scan | 2026-08-31T00:00:00+00:00 | configs/run-hl-scan.json | docs/decisioning/logs/hl-scan.out | docs/decisioning/logs/ | retrospective; artifacts may be incomplete |
| A0004 | execute | framework HPO | 2026-09-01T00:00:00+00:00 | configs/run-framework.json | docs/decisioning/logs/framework.out | docs/decisioning/logs/ | retrospective; artifacts may be incomplete |
| A0005 | execute | horizon models | 2026-08-31T00:00:00+00:00 | configs/run-horizon-models.json | docs/decisioning/decision-horizon-models.md | docs/decisioning/ | retrospective; artifacts may be incomplete; TFT / T bakeoff still open |
| A0006 | research | hl-scan-h-vs-l-166-features-weak-regularization | 2026-09-01T23:29:21+00:00 | HL-scan H vs L, 166 features, weak regularization | docs/research/hl-scan-h-vs-l-166-features-weak-regularization.md | docs/research/hl-scan-h-vs-l-166-features-weak-regularization.md |  |
| A0007 | research | max-confident-h-one-score-cs-ic-decay-hac-se | 2026-09-02T00:10:17+00:00 | Max confident H: one score, CS-IC decay, HAC SE | docs/research/max-confident-h-one-score-cs-ic-decay-hac-se.md | docs/research/max-confident-h-one-score-cs-ic-decay-hac-se.md |  |
| A0008 | research | max-confident-h-one-score-cs-ic-decay-hac-se | 2026-09-02T00:14:38+00:00 | provenance + heterogeneous H addendum | docs/research/max-confident-h-one-score-cs-ic-decay-hac-se.md | docs/research/max-confident-h-one-score-cs-ic-decay-hac-se.md | edited in place; academic vs recipe; common H not per-name |
| A0009 | research | h-star-estimand-breitung-knuppel-no-information-test | 2026-09-02T00:18:19+00:00 | H-star estimand: Breitung-Knuppel no-information test | docs/research/h-star-estimand-breitung-knuppel-no-information-test.md | docs/research/h-star-estimand-breitung-knuppel-no-information-test.md |  |
| A0010 | research | hstar-go | 2026-09-02T00:59:01+00:00 | H* GO dataset and splits | docs/decisioning/hstar-go.md | docs/decisioning/hstar-go.md | ADR-0058; Dec-Feb val spent; GO Mar-May; confirm Jun-Jul; August unread |
| A0011 | research | hstar-go | 2026-09-02T01:03:13+00:00 | H* GO sliding CV + HPO thru Feb | docs/decisioning/hstar-go.md | docs/decisioning/hstar-go.md | revised ADR-0058: 40-fold 3y slide thru 2025-11-30 for H and L; HPO Dec-Feb; untouched from 2026-03-01 |
| A0012 | research | hstar-cv-walkforward | 2026-09-02T01:13:58+00:00 | configs/run-hstar-cv.json --asof 2025-11-30 | configs/run-hstar-cv.json | configs/run-hstar-cv.json | 40-fold 3y slide; no-information scan; last val 2025-11-30; hash 82c0b9e1; not yet run |
| A0013 | execute | intraday-equities-hstar-cv-series walk-forward | 2026-09-02T05:21:09+00:00 | intraday-equities-hstar-cv-series | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-hstar-cv-series-walkforward-2025-11-30-b5967dff | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-hstar-cv-series-walkforward-2025-11-30-b5967dff | state=ran folds=40 hash=b5967dff asof=2025-11-30 |
| A0014 | research | h-cv-66-feat-pooled-lgbm-ic-0-14-40-one-name-go | 2026-09-02T05:24:24+00:00 | H* CV 66-feat pooled LGBM: IC=0, 14/40 one-name GO | docs/research/h-cv-66-feat-pooled-lgbm-ic-0-14-40-one-name-go.md | docs/research/h-cv-66-feat-pooled-lgbm-ic-0-14-40-one-name-go.md |  |
| A0015 | execute | fit | 2026-09-02T05:24:31+00:00 | run.json | /runs/x | /runs/x | ok |
| A0016 | production | paper loop | 2026-09-02T05:24:31+00:00 | live.py |  | . |  |
| A0017 | production | paper loop | 2026-09-02T05:24:31+00:00 |  |  |  | RuntimeError: boom |
| A0018 | execute | fit | 2026-09-02T05:24:31+00:00 | configs/run.json |  |  |  |
| A0019 | execute | x | 2026-09-02T05:24:31+00:00 |  |  |  |  |
| A0020 | execute | hl-scan | 2026-09-02T05:24:32+00:00 | configs/run-hl-scan.json | pipeline_runs/x | pipeline_runs/x |  |
| A0021 | execute | x | 2026-09-02T05:24:32+00:00 |  |  |  |  |
| A0022 | execute | intraday-equities-perf-probe walk-forward | 2026-09-02T14:42:48+00:00 | intraday-equities-perf-probe | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-perf-probe-walkforward-2025-11-30-c1e55420 | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-perf-probe-walkforward-2025-11-30-c1e55420 | state=ran folds=3 hash=c1e55420 asof=2025-11-30 |
| A0023 | execute | intraday-equities-perf-probe walk-forward | 2026-09-02T14:50:21+00:00 | intraday-equities-perf-probe | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-perf-probe-walkforward-2025-11-30-c1e55420 | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-perf-probe-walkforward-2025-11-30-c1e55420 | state=ran folds=3 hash=c1e55420 asof=2025-11-30 |
| A0024 | execute | intraday-equities-perf-probe walk-forward | 2026-09-02T14:57:34+00:00 | intraday-equities-perf-probe | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-perf-probe-walkforward-2025-11-30-c1e55420 | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-perf-probe-walkforward-2025-11-30-c1e55420 | state=ran folds=3 hash=c1e55420 asof=2025-11-30 |
| A0025 | research | three-declared-knobs-that-did-nothing-or-the-opposite | 2026-09-02T17:42:38+00:00 | Three declared knobs that did nothing, or the opposite | docs/research/three-declared-knobs-that-did-nothing-or-the-opposite.md | docs/research/three-declared-knobs-that-did-nothing-or-the-opposite.md |  |
| A0026 | research | unadjusted-stock-splits-in-the-raw-alpaca-tape | 2026-09-02T17:42:38+00:00 | Unadjusted stock splits in the raw Alpaca tape | docs/research/unadjusted-stock-splits-in-the-raw-alpaca-tape.md | docs/research/unadjusted-stock-splits-in-the-raw-alpaca-tape.md |  |
| A0027 | research | feature-build-speedups-72-minutes-of-a-walk-to-27-seconds | 2026-09-02T17:42:38+00:00 | Feature-build speedups: 72 minutes of a walk to 27 seconds | docs/research/feature-build-speedups-72-minutes-of-a-walk-to-27-seconds.md | docs/research/feature-build-speedups-72-minutes-of-a-walk-to-27-seconds.md |  |
| A0028 | research | VOID A0013/A0014: b5967dff measured a stump, not the market | 2026-09-02T17:42:54+00:00 | docs/decisioning/hstar-go.md | docs/research/three-declared-knobs-that-did-nothing-or-the-opposite.md |  | min_split_gain=0.02 vs label variance 2.6e-06 made every tree one leaf; IC=0 was by construction on train and val. Do not cite A0013 go_frac/IC/GO counts. train_days was also inert (node ignored splits.train_start_ms) so training was all-prior from 2016. Superseded by intraday-equities-hstar-cv-postcovid. |
| A0029 | execute | intraday-equities-hstar-cv-postcovid walk-forward | 2026-09-02T18:22:59+00:00 | intraday-equities-hstar-cv-postcovid | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-hstar-cv-postcovid-walkforward-2025-11-30-0716701f | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-hstar-cv-postcovid-walkforward-2025-11-30-0716701f | state=ran folds=20 hash=0716701f asof=2025-11-30 |
| A0030 | research | post-covid-h-cv-bounded-window-no-measurable-edge | 2026-09-02T18:23:44+00:00 | Post-COVID H* CV, bounded window: no measurable edge | docs/research/post-covid-h-cv-bounded-window-no-measurable-edge.md | docs/research/post-covid-h-cv-bounded-window-no-measurable-edge.md |  |
| A0031 | execute | intraday-equities-hstar-cv-clean2022 walk-forward | 2026-09-02T20:56:01+00:00 | intraday-equities-hstar-cv-clean2022 | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-hstar-cv-clean2022-walkforward-2025-11-30-c09ca192 | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-hstar-cv-clean2022-walkforward-2025-11-30-c09ca192 | state=ran folds=11 hash=c09ca192 asof=2025-11-30 |
| A0032 | execute | intraday-equities-zoo | 2026-09-02T21:12:55+00:00 | configs/run-zoo.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-zoo-2025-11-30-5f6b7fa9 | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-zoo-2025-11-30-5f6b7fa9 | state=ran hash=5f6b7fa9 asof=2025-11-30 |
| A0033 | research | l-and-h-selection-the-h-walk-measures-sample-size-not-horizon | 2026-09-02T21:15:56+00:00 | L and H selection: the h* walk measures sample size, not horizon | docs/research/l-and-h-selection-the-h-walk-measures-sample-size-not-horizon.md | docs/research/l-and-h-selection-the-h-walk-measures-sample-size-not-horizon.md |  |
| A0034 | execute | intraday-equities-zoo-180 | 2026-09-02T22:27:06+00:00 | configs/run-zoo-180.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-zoo-180-2025-11-30-09977c85 | /home/russell/dskit/children/intraday_equities/pipeline_runs/intraday-equities-zoo-180-2025-11-30-09977c85 | state=ran hash=09977c85 asof=2025-11-30 |

## Path to Production

| ID | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|
| — | — | — | — | — |

## Evidence

Rationale files (not generated):

- [decision-framework-hpo.md](decision-framework-hpo.md)
- [decision-hl-scan.md](decision-hl-scan.md)
- [decision-horizon-criteria.md](decision-horizon-criteria.md)
- [decision-horizon-models.md](decision-horizon-models.md)
- [framework.md](framework.md)
- [hstar-go.md](hstar-go.md)
