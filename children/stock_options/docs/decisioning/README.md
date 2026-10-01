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
| A0005 | acquire | register-source underlying | 2026-10-01T18:13:46+00:00 | --root ./pipeline_runs/option-archive-source --source  --stream  --mode | 8190b2677c1209e29364edc4b7de48e66f8bf08177ba54e8d0e208e01b0f7256 | ./pipeline_runs/option-archive-source | connector=dskit.onboarding.libs.yahoo:YahooChartArchiveConnector |
| A0006 | acquire | backfill underlying/prices | 2026-10-01T18:13:46+00:00 | --root ./pipeline_runs/option-archive-source --source underlying --stream prices --mode backfill | 88a7162fd59b3ed6c84f422bd82791545f46391397b3b9965ef6196cb4a91a64 | ./pipeline_runs/option-archive-source |  |
| A0007 | execute | stock-cdf-horizon-coverage | 2026-10-01T18:14:31+00:00 | stock-cdf-horizon-coverage | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/stock-cdf-horizon-coverage-2026-10-01-1fe9e76d | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/stock-cdf-horizon-coverage-2026-10-01-1fe9e76d | state=ran hash=1fe9e76d asof=2026-10-01 |
| A0008 | acquire | register-source options | 2026-10-01T18:21:46+00:00 | --root ./pipeline_runs/amzn-independent-20261001/source --source  --stream  --mode | f4834f9dec34fa190d629c241e424b53047a604a04bac13d488d89b97ae2d08e | ./pipeline_runs/amzn-independent-20261001/source | connector=dskit.onboarding.libs.alpaca:AlpacaOptionArchiveConnector |
| A0009 | acquire | backfill options/contracts | 2026-10-01T18:21:47+00:00 | --root ./pipeline_runs/amzn-independent-20261001/source --source options --stream contracts --mode backfill | bf673b1a17184311856074de51b4d55fca16ec4ca9bc63b2cae4d446b0b9007d | ./pipeline_runs/amzn-independent-20261001/source |  |
| A0010 | acquire | backfill options/bars | 2026-10-01T18:21:51+00:00 | --root ./pipeline_runs/amzn-independent-20261001/source --source options --stream bars --mode backfill | 04d48e47e11427a9a0e56997149ad59f55be447af9c506596a8076342b05d91b | ./pipeline_runs/amzn-independent-20261001/source |  |
| A0011 | acquire | backfill options/snapshots | 2026-10-01T18:21:51+00:00 | --root ./pipeline_runs/amzn-independent-20261001/source --source options --stream snapshots --mode backfill | 546a1bf735afe15a59cd845acc947578c1827c032d4d64c833ce7271446a7f5f | ./pipeline_runs/amzn-independent-20261001/source |  |
| A0012 | acquire | register-source underlying | 2026-10-01T18:21:51+00:00 | --root ./pipeline_runs/amzn-independent-20261001/source --source  --stream  --mode | 8190b2677c1209e29364edc4b7de48e66f8bf08177ba54e8d0e208e01b0f7256 | ./pipeline_runs/amzn-independent-20261001/source | connector=dskit.onboarding.libs.yahoo:YahooChartArchiveConnector |
| A0013 | acquire | backfill underlying/prices | 2026-10-01T18:21:52+00:00 | --root ./pipeline_runs/amzn-independent-20261001/source --source underlying --stream prices --mode backfill | d01e8723de85de21e3db4cf0a53f0d8cfdff83afcf48392e003dc03a80946f1e | ./pipeline_runs/amzn-independent-20261001/source |  |
| A0014 | execute | stock-cdf-horizon-coverage | 2026-10-01T18:22:02+00:00 | stock-cdf-horizon-coverage | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-independent-20261001/outputs/stock-cdf-horizon-coverage-2026-10-01-fe0df3a8 | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-independent-20261001/outputs/stock-cdf-horizon-coverage-2026-10-01-fe0df3a8 | state=ran hash=fe0df3a8 asof=2026-10-01 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — |
