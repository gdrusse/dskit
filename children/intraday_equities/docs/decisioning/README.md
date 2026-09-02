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
