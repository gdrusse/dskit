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
| A0456 | execute | crypto-retained-tuned11 | 2026-10-19T15:31:34+00:00 | crypto-retained-tuned11 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned11-2026-10-19-4199a5d4 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned11-2026-10-19-4199a5d4 | state=ran hash=4199a5d4 asof=2026-10-19 |
| A0457 | execute | crypto-retained-tuned12 | 2026-10-19T15:31:45+00:00 | crypto-retained-tuned12 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned12-2026-10-19-809d29f1 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned12-2026-10-19-809d29f1 | state=ran hash=809d29f1 asof=2026-10-19 |
| A0458 | execute | crypto-retained-tuned13 | 2026-10-19T15:32:02+00:00 | crypto-retained-tuned13 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned13-2026-10-19-d613d3c7 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned13-2026-10-19-d613d3c7 | state=ran hash=d613d3c7 asof=2026-10-19 |
| A0459 | execute | crypto-retained-tuned14 | 2026-10-19T15:32:15+00:00 | crypto-retained-tuned14 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned14-2026-10-19-b994bc6e | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned14-2026-10-19-b994bc6e | state=ran hash=b994bc6e asof=2026-10-19 |
| A0460 | execute | crypto-retained-tuned15 | 2026-10-19T15:32:38+00:00 | crypto-retained-tuned15 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned15-2026-10-19-33746cb6 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned15-2026-10-19-33746cb6 | state=ran hash=33746cb6 asof=2026-10-19 |
| A0461 | execute | crypto-retained-tuned16 | 2026-10-19T15:32:48+00:00 | crypto-retained-tuned16 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned16-2026-10-19-21ee8dcd | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned16-2026-10-19-21ee8dcd | state=ran hash=21ee8dcd asof=2026-10-19 |
| A0462 | execute | crypto-retained-tuned17 | 2026-10-19T15:33:15+00:00 | crypto-retained-tuned17 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned17-2026-10-19-0103b04e | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned17-2026-10-19-0103b04e | state=ran hash=0103b04e asof=2026-10-19 |
| A0463 | execute | crypto-retained-tuned18 | 2026-10-19T15:33:24+00:00 | crypto-retained-tuned18 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned18-2026-10-19-c857bdf2 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned18-2026-10-19-c857bdf2 | state=ran hash=c857bdf2 asof=2026-10-19 |
| A0464 | execute | crypto-retained-tuned19 | 2026-10-19T15:33:48+00:00 | crypto-retained-tuned19 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned19-2026-10-19-7c425d88 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned19-2026-10-19-7c425d88 | state=ran hash=7c425d88 asof=2026-10-19 |
| A0465 | execute | crypto-retained-tuned20 | 2026-10-19T15:33:59+00:00 | crypto-retained-tuned20 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned20-2026-10-19-6157fd11 | /home/russell/data/crypto_trading/research/retained-history-20261019/runs/crypto-retained-tuned20-2026-10-19-6157fd11 | state=ran hash=6157fd11 asof=2026-10-19 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0077 | Data windows W1-W5 | Lock predictor selection/HPO, check, retrain, MIO tuning and holdout windows before any modelling | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-data-windows | judgemental |  |
| A0078 | Predictor zoo grid | 5 models x 4 losses x pooled/unpooled on W1; 15-horizon vol-standardized return target | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-zoo-grid | judgemental |  |
