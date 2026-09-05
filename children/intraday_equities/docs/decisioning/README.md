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
| A12570 | execute | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-09 walk-forward | 2026-09-05T16:05:49+00:00 | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-09 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-09-walkforward-2026-02-28-ca59c359 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-09-walkforward-2026-02-28-ca59c359 | state=ran folds=1 hash=ca59c359 asof=2026-02-28 |
| A12571 | execute | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-10 walk-forward | 2026-09-05T16:05:53+00:00 | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-10 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-10-walkforward-2026-02-28-68d70757 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-10-walkforward-2026-02-28-68d70757 | state=ran folds=1 hash=68d70757 asof=2026-02-28 |
| A12572 | execute | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-11 walk-forward | 2026-09-05T16:05:54+00:00 | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-11 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-11-walkforward-2026-02-28-f100df8c | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-11-walkforward-2026-02-28-f100df8c | state=ran folds=1 hash=f100df8c asof=2026-02-28 |
| A12573 | execute | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-12 walk-forward | 2026-09-05T16:05:58+00:00 | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-12 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-12-walkforward-2026-02-28-b6b717e2 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-12-walkforward-2026-02-28-b6b717e2 | state=ran folds=1 hash=b6b717e2 asof=2026-02-28 |
| A12574 | execute | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-13 walk-forward | 2026-09-05T16:05:58+00:00 | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-13 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-13-walkforward-2026-02-28-1701a09a | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-13-walkforward-2026-02-28-1701a09a | state=ran folds=1 hash=1701a09a asof=2026-02-28 |
| A12575 | execute | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-14 walk-forward | 2026-09-05T16:06:02+00:00 | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-14 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-14-walkforward-2026-02-28-682c7611 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-14-walkforward-2026-02-28-682c7611 | state=ran folds=1 hash=682c7611 asof=2026-02-28 |
| A12576 | execute | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-15 walk-forward | 2026-09-05T16:06:03+00:00 | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-15 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-15-walkforward-2026-02-28-bee1f6ef | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-15-walkforward-2026-02-28-bee1f6ef | state=ran folds=1 hash=bee1f6ef asof=2026-02-28 |
| A12577 | execute | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-16 walk-forward | 2026-09-05T16:06:07+00:00 | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-16 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-16-walkforward-2026-02-28-cad10d1f | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-16-walkforward-2026-02-28-cad10d1f | state=ran folds=1 hash=cad10d1f asof=2026-02-28 |
| A12578 | execute | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-17 walk-forward | 2026-09-05T16:06:07+00:00 | p12-63-asset-modelability-gate3-seed01-nrg-h01-part-17 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-17-walkforward-2026-02-28-4f6c5fa8 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-63-asset-modelability-gate3-seed01-nrg-h01-part-17-walkforward-2026-02-28-4f6c5fa8 | state=ran folds=1 hash=4f6c5fa8 asof=2026-02-28 |
| A12579 | execute | staged gate3_walks | 2026-09-05T16:09:04+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p12-modelability.json |  |  | stage_token=2d203f5c95c99eeedb69486cd378df6c884ce2a411840881a50a8e0844dc583b:gate3_walks; state=error; reason=RuntimeError: fold walk p12-63-asset-modelability-gate3-seed05-nrg-h01-part-00 finished without journal evidence |

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
