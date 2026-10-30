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
| A0011 | acquire | backfill schwab/positions | 2026-10-29T23:15:44+00:00 | --root ./ob --source schwab --stream positions --mode backfill |  | ./ob |  |
| A0012 | execute | portfolio_policy-hold-vs-redeploy | 2026-10-29T23:15:44+00:00 | portfolio_policy-hold-vs-redeploy | /home/russell/wt/portfolio-first-pull-20261029/children/portfolio_policy/pipeline_runs/portfolio_policy-hold-vs-redeploy-2026-10-29-776b8512 | /home/russell/wt/portfolio-first-pull-20261029/children/portfolio_policy/pipeline_runs/portfolio_policy-hold-vs-redeploy-2026-10-29-776b8512 | state=error hash=776b8512 asof=2026-10-29 |
| A0013 | acquire | backfill schwab_trades/trades | 2026-10-30T04:00:53+00:00 | --root ./ob --source schwab_trades --stream trades --mode backfill | 74c013217afc439acd8398c418dc5b78cfce11a92479a94ff1c2831b8cd5ab07 | ./ob |  |
| A0014 | acquire | backfill schwab/positions | 2026-10-30T04:00:55+00:00 | --root ./ob --source schwab --stream positions --mode backfill | fd95a7ee5e428bf8e03d871d428c3868e19dfd1091074baa909c15988403039e | ./ob |  |
| A0015 | execute | portfolio_policy-hold-vs-redeploy | 2026-10-30T04:00:56+00:00 | portfolio_policy-hold-vs-redeploy | /home/russell/wt/portfolio-first-pull-20261029/children/portfolio_policy/pipeline_runs/portfolio_policy-hold-vs-redeploy-2026-10-30-d1f7bed4 | /home/russell/wt/portfolio-first-pull-20261029/children/portfolio_policy/pipeline_runs/portfolio_policy-hold-vs-redeploy-2026-10-30-d1f7bed4 | state=ran hash=d1f7bed4 asof=2026-10-30 |
| A0016 | acquire | register-source decisions | 2026-10-30T04:00:56+00:00 | --root ./ob --source  --stream  --mode | ba798973e756d74221d400444fc2e50520421d44a317c985e1f220d04012e2aa | ./ob | connector=localtables |
| A0017 | acquire | backfill decisions/portfolio_policy-hold-vs-redeploy-2026-10-30-d1f7bed4 | 2026-10-30T04:00:56+00:00 | --root ./ob --source decisions --stream portfolio_policy-hold-vs-redeploy-2026-10-30-d1f7bed4 --mode backfill | 6a9fa13b23884cc24a2ff5e8e734342b911c62342f9bda9358e4c02e75398bb7 | ./ob |  |
| A0018 | acquire | register-source risks | 2026-10-30T04:00:56+00:00 | --root ./ob --source  --stream  --mode | c3fbc894bb03f5b36a3d2e37dc063b711edd45e8560b454434a0d3d2b0366cf8 | ./ob | connector=localtables |
| A0019 | acquire | backfill risks/portfolio_policy-hold-vs-redeploy-2026-10-30-d1f7bed4 | 2026-10-30T04:00:56+00:00 | --root ./ob --source risks --stream portfolio_policy-hold-vs-redeploy-2026-10-30-d1f7bed4 --mode backfill | 0b64b821aa31b2f5f1f1f064fc71279a29517d5f1d2153c07dcca7fd52a5ee5d | ./ob |  |
| A0020 | execute | portfolio_policy-hold-report | 2026-10-30T04:00:56+00:00 | portfolio_policy-hold-report | /home/russell/wt/portfolio-first-pull-20261029/children/portfolio_policy/pipeline_runs/portfolio_policy-hold-report-2026-10-30-fa7996e1 | /home/russell/wt/portfolio-first-pull-20261029/children/portfolio_policy/pipeline_runs/portfolio_policy-hold-report-2026-10-30-fa7996e1 | state=ran hash=fa7996e1 asof=2026-10-30 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — |
