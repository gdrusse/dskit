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
| A12660 | execute | p12-g3-recovery-gate3-seed01-nrg-h01-part-13 walk-forward | 2026-09-05T17:49:16+00:00 | p12-g3-recovery-gate3-seed01-nrg-h01-part-13 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-13-walkforward-2026-02-28-480f427f | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-13-walkforward-2026-02-28-480f427f | state=ran folds=1 hash=480f427f asof=2026-02-28 |
| A12661 | execute | p12-g3-recovery-gate3-seed01-nrg-h01-part-14 walk-forward | 2026-09-05T17:49:20+00:00 | p12-g3-recovery-gate3-seed01-nrg-h01-part-14 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-14-walkforward-2026-02-28-775fea84 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-14-walkforward-2026-02-28-775fea84 | state=ran folds=1 hash=775fea84 asof=2026-02-28 |
| A12662 | execute | p12-g3-recovery-gate3-seed01-nrg-h01-part-15 walk-forward | 2026-09-05T17:49:21+00:00 | p12-g3-recovery-gate3-seed01-nrg-h01-part-15 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-15-walkforward-2026-02-28-84e9f957 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-15-walkforward-2026-02-28-84e9f957 | state=ran folds=1 hash=84e9f957 asof=2026-02-28 |
| A12663 | execute | p12-g3-recovery-gate3-seed01-nrg-h01-part-16 walk-forward | 2026-09-05T17:49:25+00:00 | p12-g3-recovery-gate3-seed01-nrg-h01-part-16 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-16-walkforward-2026-02-28-b459c05c | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-16-walkforward-2026-02-28-b459c05c | state=ran folds=1 hash=b459c05c asof=2026-02-28 |
| A12664 | execute | p12-g3-recovery-gate3-seed01-nrg-h01-part-17 walk-forward | 2026-09-05T17:49:26+00:00 | p12-g3-recovery-gate3-seed01-nrg-h01-part-17 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-17-walkforward-2026-02-28-40ad643c | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-17-walkforward-2026-02-28-40ad643c | state=ran folds=1 hash=40ad643c asof=2026-02-28 |
| A12665 | execute | p12-g3-recovery-gate3-seed01-nrg-h01-part-18 walk-forward | 2026-09-05T17:49:29+00:00 | p12-g3-recovery-gate3-seed01-nrg-h01-part-18 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-18-walkforward-2026-02-28-946a3e4e | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-18-walkforward-2026-02-28-946a3e4e | state=ran folds=1 hash=946a3e4e asof=2026-02-28 |
| A12666 | execute | p12-g3-recovery-gate3-seed01-nrg-h01-part-19 walk-forward | 2026-09-05T17:49:30+00:00 | p12-g3-recovery-gate3-seed01-nrg-h01-part-19 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-19-walkforward-2026-02-28-69a1a004 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-part-19-walkforward-2026-02-28-69a1a004 | state=ran folds=1 hash=69a1a004 asof=2026-02-28 |
| A12667 | execute | p12-g3-recovery-gate3-seed01-nrg-h01 bounded walk-forward | 2026-09-05T17:49:33+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p12-gate3-continuation.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed01-nrg-h01-walkforward-2026-02-28-ee39bc57 |  | state=ran folds=20 hash=ee39bc57 asof=2026-02-28; fold_processes=isolated; fold_workers=2; memory_limit_bytes=18253611008 |
| A12668 | execute | p12-g3-recovery-gate3-seed02-nrg-h01-part-01 walk-forward | 2026-09-05T17:49:34+00:00 | p12-g3-recovery-gate3-seed02-nrg-h01-part-01 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed02-nrg-h01-part-01-walkforward-2026-02-28-fbe9f278 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed02-nrg-h01-part-01-walkforward-2026-02-28-fbe9f278 | state=ran folds=1 hash=fbe9f278 asof=2026-02-28 |
| A12669 | execute | p12-g3-recovery-gate3-seed02-nrg-h01-part-00 walk-forward | 2026-09-05T17:49:34+00:00 | p12-g3-recovery-gate3-seed02-nrg-h01-part-00 | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed02-nrg-h01-part-00-walkforward-2026-02-28-b03cf60d | /home/russell/dskit/children/intraday_equities/pipeline_runs/p12-g3-recovery-gate3-seed02-nrg-h01-part-00-walkforward-2026-02-28-b03cf60d | state=ran folds=1 hash=b03cf60d asof=2026-02-28 |

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
