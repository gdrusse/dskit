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
| A1153 | research | distribution-modeling/2026-10-18-theta-chain-feature-engineering | 2026-10-18T18:15:53+00:00 | Theta chain feature engineering for the 31-day strike-region CDF | docs/research/distribution-modeling/2026-10-18-theta-chain-feature-engineering.md | docs/research/distribution-modeling/2026-10-18-theta-chain-feature-engineering.md |  |
| A1154 | research | distribution-modeling/2026-10-18-market-implied-reference-and-baselines | 2026-10-18T18:20:21+00:00 | Market-implied z reference recipe and non-neural baseline ladder | docs/research/distribution-modeling/2026-10-18-market-implied-reference-and-baselines.md | docs/research/distribution-modeling/2026-10-18-market-implied-reference-and-baselines.md |  |
| A1155 | execute | theta-strike-screen-features-census | 2026-10-18T18:22:18+00:00 | theta-strike-screen-features-census | /home/russell/data/index_options/theta-strike-screen-20261018/features/runs/theta-strike-screen-features-census-2025-12-31-1b319305 | /home/russell/data/index_options/theta-strike-screen-20261018/features/runs/theta-strike-screen-features-census-2025-12-31-1b319305 | state=error hash=1b319305 asof=2025-12-31 |
| A1156 | research | distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells | 2026-10-18T18:33:08+00:00 | Strike-screen windows, lead-time rule and mispricing cells | docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md | docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md |  |
| A1157 | execute | theta-strike-screen-features-census | 2026-10-18T18:34:42+00:00 | theta-strike-screen-features-census | /home/russell/data/index_options/theta-strike-screen-20261018/features/runs/theta-strike-screen-features-census-2025-12-31-1b319305 | /home/russell/data/index_options/theta-strike-screen-20261018/features/runs/theta-strike-screen-features-census-2025-12-31-1b319305 | state=ran hash=1b319305 asof=2025-12-31 |
| A1158 | research | distribution-modeling/2026-10-18-model-architecture-size-testing | 2026-10-18T19:15:03+00:00 | Model architecture, size and test battery for the strike-region screen | docs/research/distribution-modeling/2026-10-18-model-architecture-size-testing.md | docs/research/distribution-modeling/2026-10-18-model-architecture-size-testing.md |  |
| A1159 | execute | theta-exact-expiry-tables-smoke | 2026-10-18T19:20:18+00:00 | theta-exact-expiry-tables-smoke | /home/russell/data/index_options/theta-strike-screen-20261018/smoke/run/theta-exact-expiry-tables-smoke-2017-03-31-c2b9d71a | /home/russell/data/index_options/theta-strike-screen-20261018/smoke/run/theta-exact-expiry-tables-smoke-2017-03-31-c2b9d71a | state=ran hash=c2b9d71a asof=2017-03-31 |
| A1160 | execute | theta-exact-expiry-tables-smoke | 2026-10-18T19:20:34+00:00 | theta-exact-expiry-tables-smoke | /home/russell/data/index_options/theta-strike-screen-20261018/smoke/run/theta-exact-expiry-tables-smoke-2017-03-31-a2299d13 | /home/russell/data/index_options/theta-strike-screen-20261018/smoke/run/theta-exact-expiry-tables-smoke-2017-03-31-a2299d13 | state=ran hash=a2299d13 asof=2017-03-31 |
| A1161 | execute | theta-exact-expiry-tables-smoke-r2 | 2026-10-18T19:37:04+00:00 | theta-exact-expiry-tables-smoke-r2 | /home/russell/data/index_options/theta-strike-screen-20261018/smoke-r2/run/theta-exact-expiry-tables-smoke-r2-2017-03-31-5b07370e | /home/russell/data/index_options/theta-strike-screen-20261018/smoke-r2/run/theta-exact-expiry-tables-smoke-r2-2017-03-31-5b07370e | state=ran hash=5b07370e asof=2017-03-31 |
| A1162 | execute | theta-exact-expiry-tables-smoke-r3 | 2026-10-18T19:47:10+00:00 | theta-exact-expiry-tables-smoke-r3 | /home/russell/data/index_options/theta-strike-screen-20261018/smoke-r3/run/theta-exact-expiry-tables-smoke-r3-2017-03-31-4b92183c | /home/russell/data/index_options/theta-strike-screen-20261018/smoke-r3/run/theta-exact-expiry-tables-smoke-r3-2017-03-31-4b92183c | state=ran hash=4b92183c asof=2017-03-31 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
