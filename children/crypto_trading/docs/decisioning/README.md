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
| A0183 | execute | crypto-warmup20-warmup-56-randomized-newton-trees | 2026-10-18T05:09:53+00:00 | crypto-warmup20-warmup-56-randomized-newton-trees | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-56-randomized-newton-trees-2026-10-18-cb709565 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-56-randomized-newton-trees-2026-10-18-cb709565 | state=ran hash=cb709565 asof=2026-10-18 |
| A0184 | execute | crypto-warmup20-warmup-30-modified-huber-classifier | 2026-10-18T05:10:08+00:00 | crypto-warmup20-warmup-30-modified-huber-classifier | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-30-modified-huber-classifier-2026-10-18-06a97d7c | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-30-modified-huber-classifier-2026-10-18-06a97d7c | state=ran hash=06a97d7c asof=2026-10-18 |
| A0185 | execute | crypto-warmup20-warmup-57-deep-relu-residual | 2026-10-18T05:10:21+00:00 | crypto-warmup20-warmup-57-deep-relu-residual | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-57-deep-relu-residual-2026-10-18-a89ba5f1 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-57-deep-relu-residual-2026-10-18-a89ba5f1 | state=ran hash=a89ba5f1 asof=2026-10-18 |
| A0186 | execute | crypto-warmup20-warmup-31-elastic-net-logistic-sgd | 2026-10-18T05:10:29+00:00 | crypto-warmup20-warmup-31-elastic-net-logistic-sgd | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-31-elastic-net-logistic-sgd-2026-10-18-886a92f1 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-31-elastic-net-logistic-sgd-2026-10-18-886a92f1 | state=ran hash=886a92f1 asof=2026-10-18 |
| A0187 | execute | crypto-warmup20-warmup-32-tanh-brier-residual-network | 2026-10-18T05:10:51+00:00 | crypto-warmup20-warmup-32-tanh-brier-residual-network | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-32-tanh-brier-residual-network-2026-10-18-3208b3cb | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-32-tanh-brier-residual-network-2026-10-18-3208b3cb | state=ran hash=3208b3cb asof=2026-10-18 |
| A0188 | execute | crypto-warmup20-warmup-33-weekly-refit-sparse-residual | 2026-10-18T05:11:11+00:00 | crypto-warmup20-warmup-33-weekly-refit-sparse-residual | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-33-weekly-refit-sparse-residual-2026-10-18-40700c78 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-33-weekly-refit-sparse-residual-2026-10-18-40700c78 | state=ran hash=40700c78 asof=2026-10-18 |
| A0189 | execute | crypto-warmup20-warmup-34-week-embargo-sparse-residual | 2026-10-18T05:11:32+00:00 | crypto-warmup20-warmup-34-week-embargo-sparse-residual | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-34-week-embargo-sparse-residual-2026-10-18-339ad574 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-34-week-embargo-sparse-residual-2026-10-18-339ad574 | state=ran hash=339ad574 asof=2026-10-18 |
| A0190 | execute | crypto-warmup20-warmup-35-local-regime-beta | 2026-10-18T05:11:53+00:00 | crypto-warmup20-warmup-35-local-regime-beta | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-35-local-regime-beta-2026-10-18-e2e98659 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-35-local-regime-beta-2026-10-18-e2e98659 | state=ran hash=e2e98659 asof=2026-10-18 |
| A0191 | execute | crypto-warmup20-warmup-36-penalty-matched-asset-lead-sigmoid | 2026-10-18T05:12:14+00:00 | crypto-warmup20-warmup-36-penalty-matched-asset-lead-sigmoid | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-36-penalty-matched-asset-lead-sigmoid-2026-10-18-e48ec627 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-36-penalty-matched-asset-lead-sigmoid-2026-10-18-e48ec627 | state=ran hash=e48ec627 asof=2026-10-18 |
| A0192 | execute | crypto-warmup20-warmup-37-penalty-matched-temperature | 2026-10-18T05:12:35+00:00 | crypto-warmup20-warmup-37-penalty-matched-temperature | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-37-penalty-matched-temperature-2026-10-18-404d2f8c | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/warmup/crypto-warmup20-warmup-37-penalty-matched-temperature-2026-10-18-404d2f8c | state=ran hash=404d2f8c asof=2026-10-18 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0077 | Data windows W1-W5 | Lock predictor selection/HPO, check, retrain, MIO tuning and holdout windows before any modelling | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-data-windows | judgemental |  |
| A0078 | Predictor zoo grid | 5 models x 4 losses x pooled/unpooled on W1; 15-horizon vol-standardized return target | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-zoo-grid | judgemental |  |
