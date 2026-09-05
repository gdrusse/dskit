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

**Path to Production** is human-owner-only: only the owner may add or edit a
row, including **Current Work**. Agents and hooks never write it. Every row
has a short label, purpose, relevant evidence files (pipeline run, research
markdown, or other material evidence), and **LOCKED** (`Y` / `N`). Pytest
does not record. A child without `journal.json` refuses acquire / run / live.

## Actions (latest 10)

Display only: `actions.csv` remains the complete, append-only journal.

| ID | Category | Step | Execution Date | Relevant Inputs | Relevant Outputs | Database Location | Notes |
|---|---|---|---|---|---|---|---|
| A2904 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-16 walk-forward | 2026-09-05T08:40:27+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-16 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-16-walkforward-2026-02-28-3e4312e9 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-16-walkforward-2026-02-28-3e4312e9 | state=ran folds=1 hash=3e4312e9 asof=2026-02-28 |
| A2905 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-17 walk-forward | 2026-09-05T08:40:28+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-17 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-17-walkforward-2026-02-28-0914eb08 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-17-walkforward-2026-02-28-0914eb08 | state=ran folds=1 hash=0914eb08 asof=2026-02-28 |
| A2906 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-18 walk-forward | 2026-09-05T08:40:31+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-18 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-18-walkforward-2026-02-28-6d52a22f | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-18-walkforward-2026-02-28-6d52a22f | state=ran folds=1 hash=6d52a22f asof=2026-02-28 |
| A2907 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-19 walk-forward | 2026-09-05T08:40:32+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-19 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-19-walkforward-2026-02-28-35deb94e | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-19-walkforward-2026-02-28-35deb94e | state=ran folds=1 hash=35deb94e asof=2026-02-28 |
| A2908 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01 bounded walk-forward | 2026-09-05T08:40:33+00:00 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/run-p12-smoke-orcl.json | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-walkforward-2026-02-28-ff6d5855 |  | state=ran folds=20 hash=ff6d5855 asof=2026-02-28; fold_processes=isolated; fold_workers=2; memory_limit_bytes=18253611008 |
| A2909 | execute | P12 smoke evidence: one real asset-local null draw (ORCL h1 seed 0) | 2026-09-05T09:16:41+00:00 | pipeline_runs/run-p12-smoke-orcl.json | pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-walkforward-2026-02-28-ff6d5855 | pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-walkforward-2026-02-28-ff6d5855 | Rows A2888-A2908 are this walk: derived by modelability_study.asset_walk_document over the group-D cache with label_scramble_seed=0 and run through the same seam, outside the staged document, because ORCL failed Gate 1 at h1 and the staged run drew no null. Scored: observed r2oos -3.35e-05 (t_pool 0.111), null r2oos -2.81e-04 (t_pool -0.686), beat_all True. Evidence that the asset-local scramble walk runs on the group cache; not a Gate-3 decision (ADR-0094) |
| A2910 | execute | p12-64-asset-modelability-cache-e walk-forward | 2026-09-05T10:58:42+00:00 | p12-64-asset-modelability-cache-e | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-64-asset-modelability-cache-e-walkforward-2026-02-28-b986e52c | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-64-asset-modelability-cache-e-walkforward-2026-02-28-b986e52c | state=ran folds=1 hash=b986e52c asof=2026-02-28 |
| A2911 | execute | p12-64-asset-modelability-cache-a-part-00 walk-forward | 2026-09-05T11:01:55+00:00 | p12-64-asset-modelability-cache-a-part-00 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-64-asset-modelability-cache-a-part-00-walkforward-2026-02-28-b04f32ec | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-64-asset-modelability-cache-a-part-00-walkforward-2026-02-28-b04f32ec | state=ran folds=1 hash=b04f32ec asof=2026-02-28 |
| A2912 | execute | p12-64-asset-modelability-cache-a bounded walk-forward | 2026-09-05T11:01:56+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p12-modelability.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-64-asset-modelability-cache-a-walkforward-2026-02-28-0673c45d |  | state=ran folds=1 hash=0673c45d asof=2026-02-28; fold_processes=isolated; fold_workers=2; memory_limit_bytes=18253611008 |
| A2913 | research | p12-cohort-tape-check-unadjusted-corporate-actions | 2026-09-05T11:02:57+00:00 | P12 cohort tape check: unadjusted corporate actions | docs/research/p12-cohort-tape-check-unadjusted-corporate-actions.md | docs/research/p12-cohort-tape-check-unadjusted-corporate-actions.md |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A2822 | Gate 1: stock modelability | Lock the stock-modelability selection gate | children/intraday_equities/pipeline_runs/p11-25-asset-modelability-staged-2026-02-28-355b6198/stages/gate1.json; docs/decisioning/framework.md | Y |  | execute | staged gate1 | empirical |  |
| A2850 | Gate 2: HFDR in MIO | Replace the retired Bonferroni screen with an MIO constraint on false-signal gross capital | docs/architecture/decision-log.md#ADR-0088; docs/decisioning/framework.md | Y |  | research | HFDR constrained in MIO | judgemental | docs/architecture/decision-log.md |
| A2851 | ~~Gate 3: shuffle refit~~ | Audit Gate-1 selections against a session-scramble refit null | docs/research/gate3-lower-compute-null-design.md; docs/architecture/decision-log.md#ADR-0089 | N | Investigating a faster valid shuffle-training solution | research | gate3-lower-compute-null-design | empirical | docs/research/gate3-lower-compute-null-design.md |
| A2887 | Gate 3: fail-fast scramble refit | Audit every Gate-1 passer against whole-session scramble refits, stopping at the first null that matches or beats the real result | docs/architecture/decision-log.md#ADR-0092; docs/architecture/decision-log.md#ADR-0093; docs/architecture/decision-log.md#ADR-0094; children/intraday_equities/configs/run-p12-modelability.json | N |  | execute | Gate 3: fail-fast scramble audit over asset-local walks | empirical | docs/architecture/decision-log.md |

## Evidence

Rationale files (not generated):

- [decision-framework-hpo.md](decision-framework-hpo.md)
- [decision-hl-scan.md](decision-hl-scan.md)
- [decision-horizon-criteria.md](decision-horizon-criteria.md)
- [decision-horizon-models.md](decision-horizon-models.md)
- [framework.md](framework.md)
- [hstar-go.md](hstar-go.md)
