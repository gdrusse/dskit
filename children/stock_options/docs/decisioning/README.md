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
| A0023 | acquire | backfill options/contracts | 2026-10-01T18:50:37+00:00 | --root ./pipeline_runs/amzn-independent-final-20261001/source --source options --stream contracts --mode backfill | e3fb6d5a6b16116bf6327563e05278eead40abe39ad14b1fc48bbcc14028f06b | ./pipeline_runs/amzn-independent-final-20261001/source |  |
| A0024 | acquire | backfill options/bars | 2026-10-01T18:50:41+00:00 | --root ./pipeline_runs/amzn-independent-final-20261001/source --source options --stream bars --mode backfill | 131a317d5a34f33d03648aedd4f5b8ca73f6396049d6c92e7fe68cd2c59749ce | ./pipeline_runs/amzn-independent-final-20261001/source |  |
| A0025 | acquire | backfill options/snapshots | 2026-10-01T18:50:41+00:00 | --root ./pipeline_runs/amzn-independent-final-20261001/source --source options --stream snapshots --mode backfill | 0ee7d6e636a3ed565b3b5942f556a5dd522c65eb7e5ef1ed27b73b76c47021e0 | ./pipeline_runs/amzn-independent-final-20261001/source |  |
| A0026 | acquire | register-source underlying | 2026-10-01T18:50:41+00:00 | --root ./pipeline_runs/amzn-independent-final-20261001/source --source  --stream  --mode | b762437124797b837adf7baf5c0766bdc4d301d46464b91186298bc2f88c13aa | ./pipeline_runs/amzn-independent-final-20261001/source | connector=dskit.onboarding.libs.yahoo:YahooChartArchiveConnector |
| A0027 | acquire | backfill underlying/prices | 2026-10-01T18:50:41+00:00 | --root ./pipeline_runs/amzn-independent-final-20261001/source --source underlying --stream prices --mode backfill | 8232cd5d815b8e36322bcc4b8167ca0795b60a046a65525432e6d066b3f46563 | ./pipeline_runs/amzn-independent-final-20261001/source |  |
| A0028 | execute | stock-cdf-horizon-coverage | 2026-10-01T18:50:51+00:00 | stock-cdf-horizon-coverage | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-independent-final-20261001/outputs/stock-cdf-horizon-coverage-2026-10-01-a36da995 | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-independent-final-20261001/outputs/stock-cdf-horizon-coverage-2026-10-01-a36da995 | state=ran hash=a36da995 asof=2026-10-01 |
| A0029 | execute | amzn-feature-availability | 2026-10-01T18:57:31+00:00 | amzn-feature-availability | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-feature-availability/runs/amzn-feature-availability-2026-10-01-2df67b10 | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-feature-availability/runs/amzn-feature-availability-2026-10-01-2df67b10 | state=error hash=2df67b10 asof=2026-10-01 |
| A0030 | execute | amzn-feature-availability | 2026-10-01T19:00:15+00:00 | amzn-feature-availability | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-feature-availability/runs/amzn-feature-availability-2026-10-01-2df67b10 | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-feature-availability/runs/amzn-feature-availability-2026-10-01-2df67b10 | state=error hash=2df67b10 asof=2026-10-01 |
| A0031 | execute | amzn-feature-availability | 2026-10-01T19:01:39+00:00 | amzn-feature-availability | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-feature-availability/runs/amzn-feature-availability-2026-10-01-cfe3c76c | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-feature-availability/runs/amzn-feature-availability-2026-10-01-cfe3c76c | state=ran hash=cfe3c76c asof=2026-10-01 |
| A0032 | execute | amzn-feature-availability | 2026-10-01T19:05:18+00:00 | amzn-feature-availability | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-gap-independent-20261001/runs/amzn-feature-availability-2026-10-01-bbcfc0c2 | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-gap-independent-20261001/runs/amzn-feature-availability-2026-10-01-bbcfc0c2 | state=ran hash=bbcfc0c2 asof=2026-10-01 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — |
