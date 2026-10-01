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
| A0012 | acquire | register-source underlying | 2026-10-01T18:21:51+00:00 | --root ./pipeline_runs/amzn-independent-20261001/source --source  --stream  --mode | 8190b2677c1209e29364edc4b7de48e66f8bf08177ba54e8d0e208e01b0f7256 | ./pipeline_runs/amzn-independent-20261001/source | connector=dskit.onboarding.libs.yahoo:YahooChartArchiveConnector |
| A0013 | acquire | backfill underlying/prices | 2026-10-01T18:21:52+00:00 | --root ./pipeline_runs/amzn-independent-20261001/source --source underlying --stream prices --mode backfill | d01e8723de85de21e3db4cf0a53f0d8cfdff83afcf48392e003dc03a80946f1e | ./pipeline_runs/amzn-independent-20261001/source |  |
| A0014 | execute | stock-cdf-horizon-coverage | 2026-10-01T18:22:02+00:00 | stock-cdf-horizon-coverage | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-independent-20261001/outputs/stock-cdf-horizon-coverage-2026-10-01-fe0df3a8 | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-independent-20261001/outputs/stock-cdf-horizon-coverage-2026-10-01-fe0df3a8 | state=ran hash=fe0df3a8 asof=2026-10-01 |
| A0015 | acquire | register-source options | 2026-10-01T18:43:13+00:00 | --root ./pipeline_runs/option-archive-source-v2 --source  --stream  --mode | f4834f9dec34fa190d629c241e424b53047a604a04bac13d488d89b97ae2d08e | ./pipeline_runs/option-archive-source-v2 | connector=dskit.onboarding.libs.alpaca:AlpacaOptionArchiveConnector |
| A0016 | acquire | backfill options/contracts | 2026-10-01T18:43:14+00:00 | --root ./pipeline_runs/option-archive-source-v2 --source options --stream contracts --mode backfill | 9ceffb975834a70f5cf3faae4f914a25d0bf1aedde79933bac767e630b7ec57b | ./pipeline_runs/option-archive-source-v2 |  |
| A0017 | acquire | backfill options/bars | 2026-10-01T18:43:17+00:00 | --root ./pipeline_runs/option-archive-source-v2 --source options --stream bars --mode backfill | 834c6e5ffafe30969ffa0cd41868a2855f21b56d286fa366dcf990d8bb9782e2 | ./pipeline_runs/option-archive-source-v2 |  |
| A0018 | acquire | backfill options/snapshots | 2026-10-01T18:43:18+00:00 | --root ./pipeline_runs/option-archive-source-v2 --source options --stream snapshots --mode backfill | 70a54ea56f8f18b70f5289469878fbe862245c68aa2620a9b0ede0430e407fcd | ./pipeline_runs/option-archive-source-v2 |  |
| A0019 | acquire | register-source underlying | 2026-10-01T18:43:18+00:00 | --root ./pipeline_runs/option-archive-source-v2 --source  --stream  --mode | b762437124797b837adf7baf5c0766bdc4d301d46464b91186298bc2f88c13aa | ./pipeline_runs/option-archive-source-v2 | connector=dskit.onboarding.libs.yahoo:YahooChartArchiveConnector |
| A0020 | acquire | backfill underlying/prices | 2026-10-01T18:43:18+00:00 | --root ./pipeline_runs/option-archive-source-v2 --source underlying --stream prices --mode backfill | 977aff84bdce7c0e07b4252949d4684c2f1f91c0a3cfdd0b06ad71765a3eaa1f | ./pipeline_runs/option-archive-source-v2 |  |
| A0021 | execute | stock-cdf-horizon-coverage | 2026-10-01T18:43:30+00:00 | stock-cdf-horizon-coverage | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/stock-cdf-horizon-coverage-2026-10-01-72b70805 | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/stock-cdf-horizon-coverage-2026-10-01-72b70805 | state=ran hash=72b70805 asof=2026-10-01 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — |
