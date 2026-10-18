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
| A0102 | execute | crypto-predict15-04-bayesian-ridge-residual | 2026-10-18T03:02:53+00:00 | crypto-predict15-04-bayesian-ridge-residual | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-04-bayesian-ridge-residual-2026-10-17-ee05c8cd | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-04-bayesian-ridge-residual-2026-10-17-ee05c8cd | state=ran hash=ee05c8cd asof=2026-10-17 |
| A0103 | execute | crypto-predict15-05-ard-sparse-residual | 2026-10-18T03:03:19+00:00 | crypto-predict15-05-ard-sparse-residual | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-05-ard-sparse-residual-2026-10-17-9f445cc3 | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-05-ard-sparse-residual-2026-10-17-9f445cc3 | state=ran hash=9f445cc3 asof=2026-10-17 |
| A0104 | execute | crypto-predict15-09-random-forest-residual | 2026-10-18T03:03:56+00:00 | crypto-predict15-09-random-forest-residual | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-09-random-forest-residual-2026-10-17-7353b017 | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-09-random-forest-residual-2026-10-17-7353b017 | state=ran hash=7353b017 asof=2026-10-17 |
| A0105 | execute | crypto-predict15-10-extra-trees-residual | 2026-10-18T03:04:37+00:00 | crypto-predict15-10-extra-trees-residual | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-10-extra-trees-residual-2026-10-17-8d1da74b | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-10-extra-trees-residual-2026-10-17-8d1da74b | state=ran hash=8d1da74b asof=2026-10-17 |
| A0106 | execute | crypto-predict15-11-histogram-brier-residual | 2026-10-18T03:05:28+00:00 | crypto-predict15-11-histogram-brier-residual | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-11-histogram-brier-residual-2026-10-17-e491425e | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-11-histogram-brier-residual-2026-10-17-e491425e | state=ran hash=e491425e asof=2026-10-17 |
| A0107 | execute | crypto-predict15-12-huber-boosted-residual | 2026-10-18T03:07:03+00:00 | crypto-predict15-12-huber-boosted-residual | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-12-huber-boosted-residual-2026-10-17-6c41fe69 | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-12-huber-boosted-residual-2026-10-17-6c41fe69 | state=ran hash=6c41fe69 asof=2026-10-17 |
| A0108 | execute | crypto-predict15-13-knn-local-residual | 2026-10-18T03:08:17+00:00 | crypto-predict15-13-knn-local-residual | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-13-knn-local-residual-2026-10-17-0cf70cb8 | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-13-knn-local-residual-2026-10-17-0cf70cb8 | state=ran hash=0cf70cb8 asof=2026-10-17 |
| A0109 | execute | crypto-predict15-14-monotone-lightgbm | 2026-10-18T03:08:54+00:00 | crypto-predict15-14-monotone-lightgbm | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-14-monotone-lightgbm-2026-10-17-79c1ecaa | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-14-monotone-lightgbm-2026-10-17-79c1ecaa | state=ran hash=79c1ecaa asof=2026-10-17 |
| A0110 | execute | crypto-predict15-15-lightgbm-l1-leaf-residual | 2026-10-18T03:09:41+00:00 | crypto-predict15-15-lightgbm-l1-leaf-residual | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-15-lightgbm-l1-leaf-residual-2026-10-17-261a5478 | /home/russell/data/crypto_trading/research/predictability15-20261017/runs/crypto-predict15-15-lightgbm-l1-leaf-residual-2026-10-17-261a5478 | state=ran hash=261a5478 asof=2026-10-17 |
| A0111 | research | predictability-15/2026-10-18-synthesis | 2026-10-18T03:20:52+00:00 | Fifteen prediction methods versus crypto market probabilities | docs/research/predictability-15/2026-10-18-synthesis.md | docs/research/predictability-15/2026-10-18-synthesis.md |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0077 | Data windows W1-W5 | Lock predictor selection/HPO, check, retrain, MIO tuning and holdout windows before any modelling | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-data-windows | judgemental |  |
| A0078 | Predictor zoo grid | 5 models x 4 losses x pooled/unpooled on W1; 15-horizon vol-standardized return target | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-zoo-grid | judgemental |  |
