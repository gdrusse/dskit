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
| A1023 | acquire | register-source options | 2026-10-03T20:51:19+00:00 | --root /home/russell/data/stock_options/option-universe-300 --source  --stream  --mode | e3f1b87c16337f228ef9a6b105f8ea34a13b719e3f7fe377ba5c0e71de7b4f77 | /home/russell/data/stock_options/option-universe-300 | connector=dskit.onboarding.libs.alpaca:AlpacaOptionFetchConnector |
| A1024 | acquire | register-source stock-daily-bars-300 | 2026-10-03T20:52:50+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | 299ad8c5a639e86222395e3aef211576aa196f0fbc264aa35828275a7d3605d9 | /home/russell/data/index_options/ob | connector=httpblobs |
| A1025 | acquire | backfill stock-daily-bars-300/files | 2026-10-03T20:56:35+00:00 | --root /home/russell/data/index_options/ob --source stock-daily-bars-300 --stream files --mode backfill | e388e332a10938d71a7a01e325d85c9fbbdcbdb733c7b9353445ba724c6f37bc | /home/russell/data/index_options/ob |  |
| A1026 | execute | features-stock-daily | 2026-10-03T20:57:47+00:00 | features-stock-daily | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/stock-features-300/features-stock-daily-2026-10-03-9cec875a | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/stock-features-300/features-stock-daily-2026-10-03-9cec875a | state=ran hash=9cec875a asof=2026-10-03 |
| A1027 | acquire | register-source stock-daily-features-300 | 2026-10-03T20:57:48+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | c13414600e2a830c1ea83c0deda497cb1815ae4a162446f65af620725fd3ac58 | /home/russell/data/index_options/ob | connector=localtables |
| A1028 | acquire | backfill stock-daily-features-300/stock_daily_features | 2026-10-03T20:58:08+00:00 | --root /home/russell/data/index_options/ob --source stock-daily-features-300 --stream stock_daily_features --mode backfill | 3258e6e7ad878b78b1118af4b1df2ec5605f80ac3d894c09584c9c9c47e61b64 | /home/russell/data/index_options/ob |  |
| A1029 | acquire | register-source stock-daily-bars-300 | 2026-10-03T21:01:03+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | 34289f52d265f7d19f25ae331eec024254b65c2425f5aee63efba28f36947539 | /home/russell/data/index_options/ob | connector=httpblobs |
| A1030 | acquire | register-source stock-daily-bars | 2026-10-03T21:05:54+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | 2402c7e3554d9097e98bde0aaea9b4d4239ef1b46f4929c4d86b1a7a0f289e17 | /home/russell/data/index_options/ob | connector=httpblobs |
| A1031 | acquire | register-source benchmark-daily-bars | 2026-10-03T21:05:54+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | 5d8c241b3be08633d0a5314f95bdd781c482bc55823e00cb6f0e95e1d22f36db | /home/russell/data/index_options/ob | connector=httpblobs |
| A1032 | acquire | backfill options/contracts | 2026-10-03T21:07:49+00:00 | --root /home/russell/data/stock_options/option-universe-300 --source options --stream contracts --mode backfill | 15db5741bd84c37ae9cbd87b0656f27d9bccf70be1c6f6c94253c46e18cfbd7b | /home/russell/data/stock_options/option-universe-300 |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
