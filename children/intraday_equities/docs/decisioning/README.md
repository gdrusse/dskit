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
| A2899 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-11 walk-forward | 2026-09-05T08:40:15+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-11 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-11-walkforward-2026-02-28-422e92ce | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-11-walkforward-2026-02-28-422e92ce | state=ran folds=1 hash=422e92ce asof=2026-02-28 |
| A2900 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-12 walk-forward | 2026-09-05T08:40:19+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-12 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-12-walkforward-2026-02-28-c401e1bd | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-12-walkforward-2026-02-28-c401e1bd | state=ran folds=1 hash=c401e1bd asof=2026-02-28 |
| A2901 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-13 walk-forward | 2026-09-05T08:40:19+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-13 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-13-walkforward-2026-02-28-ce7315cd | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-13-walkforward-2026-02-28-ce7315cd | state=ran folds=1 hash=ce7315cd asof=2026-02-28 |
| A2902 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-14 walk-forward | 2026-09-05T08:40:23+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-14 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-14-walkforward-2026-02-28-85958919 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-14-walkforward-2026-02-28-85958919 | state=ran folds=1 hash=85958919 asof=2026-02-28 |
| A2903 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-15 walk-forward | 2026-09-05T08:40:24+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-15 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-15-walkforward-2026-02-28-8bb1918b | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-15-walkforward-2026-02-28-8bb1918b | state=ran folds=1 hash=8bb1918b asof=2026-02-28 |
| A2904 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-16 walk-forward | 2026-09-05T08:40:27+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-16 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-16-walkforward-2026-02-28-3e4312e9 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-16-walkforward-2026-02-28-3e4312e9 | state=ran folds=1 hash=3e4312e9 asof=2026-02-28 |
| A2905 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-17 walk-forward | 2026-09-05T08:40:28+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-17 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-17-walkforward-2026-02-28-0914eb08 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-17-walkforward-2026-02-28-0914eb08 | state=ran folds=1 hash=0914eb08 asof=2026-02-28 |
| A2906 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-18 walk-forward | 2026-09-05T08:40:31+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-18 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-18-walkforward-2026-02-28-6d52a22f | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-18-walkforward-2026-02-28-6d52a22f | state=ran folds=1 hash=6d52a22f asof=2026-02-28 |
| A2907 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01-part-19 walk-forward | 2026-09-05T08:40:32+00:00 | p12-smoke-orcl-gate3-seed00-orcl-h01-part-19 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-19-walkforward-2026-02-28-35deb94e | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-part-19-walkforward-2026-02-28-35deb94e | state=ran folds=1 hash=35deb94e asof=2026-02-28 |
| A2908 | execute | p12-smoke-orcl-gate3-seed00-orcl-h01 bounded walk-forward | 2026-09-05T08:40:33+00:00 | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/run-p12-smoke-orcl.json | /home/russell/wt/adr-0094/children/intraday_equities/pipeline_runs/p12-smoke-orcl-gate3-seed00-orcl-h01-walkforward-2026-02-28-ff6d5855 |  | state=ran folds=20 hash=ff6d5855 asof=2026-02-28; fold_processes=isolated; fold_workers=2; memory_limit_bytes=18253611008 |

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
