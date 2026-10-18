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
| A0126 | execute | crypto-next20-29-market-soft-label-entropy | 2026-10-18T03:51:22+00:00 | crypto-next20-29-market-soft-label-entropy | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-29-market-soft-label-entropy-2026-10-17-2ddc3676 | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-29-market-soft-label-entropy-2026-10-17-2ddc3676 | state=ran hash=2ddc3676 asof=2026-10-17 |
| A0127 | execute | crypto-next20-30-modified-huber-classifier | 2026-10-18T03:52:02+00:00 | crypto-next20-30-modified-huber-classifier | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-30-modified-huber-classifier-2026-10-17-22cf9305 | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-30-modified-huber-classifier-2026-10-17-22cf9305 | state=ran hash=22cf9305 asof=2026-10-17 |
| A0128 | execute | crypto-next20-31-elastic-net-logistic-sgd | 2026-10-18T03:52:44+00:00 | crypto-next20-31-elastic-net-logistic-sgd | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-31-elastic-net-logistic-sgd-2026-10-17-e7b3e144 | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-31-elastic-net-logistic-sgd-2026-10-17-e7b3e144 | state=ran hash=e7b3e144 asof=2026-10-17 |
| A0129 | execute | crypto-next20-32-tanh-brier-residual-network | 2026-10-18T03:53:29+00:00 | crypto-next20-32-tanh-brier-residual-network | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-32-tanh-brier-residual-network-2026-10-17-bae8f3aa | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-32-tanh-brier-residual-network-2026-10-17-bae8f3aa | state=ran hash=bae8f3aa asof=2026-10-17 |
| A0130 | execute | crypto-next20-33-weekly-refit-sparse-residual | 2026-10-18T03:54:09+00:00 | crypto-next20-33-weekly-refit-sparse-residual | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-33-weekly-refit-sparse-residual-2026-10-17-81295cad | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-33-weekly-refit-sparse-residual-2026-10-17-81295cad | state=ran hash=81295cad asof=2026-10-17 |
| A0131 | execute | crypto-next20-34-week-embargo-sparse-residual | 2026-10-18T03:54:48+00:00 | crypto-next20-34-week-embargo-sparse-residual | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-34-week-embargo-sparse-residual-2026-10-17-30d72712 | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-34-week-embargo-sparse-residual-2026-10-17-30d72712 | state=ran hash=30d72712 asof=2026-10-17 |
| A0132 | execute | crypto-next20-35-local-regime-beta | 2026-10-18T03:55:28+00:00 | crypto-next20-35-local-regime-beta | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-35-local-regime-beta-2026-10-17-f2535280 | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-35-local-regime-beta-2026-10-17-f2535280 | state=ran hash=f2535280 asof=2026-10-17 |
| A0133 | execute | crypto-next20-36-penalty-matched-asset-lead-sigmoid | 2026-10-18T03:56:13+00:00 | crypto-next20-36-penalty-matched-asset-lead-sigmoid | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-36-penalty-matched-asset-lead-sigmoid-2026-10-17-31b1aed4 | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-36-penalty-matched-asset-lead-sigmoid-2026-10-17-31b1aed4 | state=ran hash=31b1aed4 asof=2026-10-17 |
| A0134 | execute | crypto-next20-37-penalty-matched-temperature | 2026-10-18T03:56:52+00:00 | crypto-next20-37-penalty-matched-temperature | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-37-penalty-matched-temperature-2026-10-17-470bdaae | /home/russell/data/crypto_trading/research/calibration-next20-20261017/runs-final/crypto-next20-37-penalty-matched-temperature-2026-10-17-470bdaae | state=ran hash=470bdaae asof=2026-10-17 |
| A0135 | research | calibration-next22/2026-10-18-synthesis | 2026-10-18T04:01:13+00:00 | Twenty-two further tests of local crypto probability calibration | docs/research/calibration-next22/2026-10-18-synthesis.md | docs/research/calibration-next22/2026-10-18-synthesis.md |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0077 | Data windows W1-W5 | Lock predictor selection/HPO, check, retrain, MIO tuning and holdout windows before any modelling | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-data-windows | judgemental |  |
| A0078 | Predictor zoo grid | 5 models x 4 losses x pooled/unpooled on W1; 15-horizon vol-standardized return target | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-zoo-grid | judgemental |  |
