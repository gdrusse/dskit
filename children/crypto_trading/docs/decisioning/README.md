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
| A0241 | execute | crypto-warmup20-validation-47-disagreement-only-newton | 2026-10-18T05:22:46+00:00 | crypto-warmup20-validation-47-disagreement-only-newton | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-47-disagreement-only-newton-2026-10-18-967c8150 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-47-disagreement-only-newton-2026-10-18-967c8150 | state=ran hash=967c8150 asof=2026-10-18 |
| A0242 | execute | crypto-warmup20-validation-50-distance-neighbor-residual | 2026-10-18T05:23:15+00:00 | crypto-warmup20-validation-50-distance-neighbor-residual | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-50-distance-neighbor-residual-2026-10-18-44dda134 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-50-distance-neighbor-residual-2026-10-18-44dda134 | state=ran hash=44dda134 asof=2026-10-18 |
| A0243 | execute | crypto-warmup20-validation-52-gaussian-naive-bayes | 2026-10-18T05:23:25+00:00 | crypto-warmup20-validation-52-gaussian-naive-bayes | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-52-gaussian-naive-bayes-2026-10-18-a7ba6bb0 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-52-gaussian-naive-bayes-2026-10-18-a7ba6bb0 | state=ran hash=a7ba6bb0 asof=2026-10-18 |
| A0244 | execute | crypto-warmup20-validation-53-shrinkage-discriminant | 2026-10-18T05:23:32+00:00 | crypto-warmup20-validation-53-shrinkage-discriminant | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-53-shrinkage-discriminant-2026-10-18-b2f9cea5 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-53-shrinkage-discriminant-2026-10-18-b2f9cea5 | state=ran hash=b2f9cea5 asof=2026-10-18 |
| A0245 | execute | crypto-warmup20-validation-51-latent-pls-residual | 2026-10-18T05:23:33+00:00 | crypto-warmup20-validation-51-latent-pls-residual | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-51-latent-pls-residual-2026-10-18-b6acf5f1 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-51-latent-pls-residual-2026-10-18-b6acf5f1 | state=ran hash=b6acf5f1 asof=2026-10-18 |
| A0246 | execute | crypto-warmup20-validation-54-quadratic-discriminant | 2026-10-18T05:24:00+00:00 | crypto-warmup20-validation-54-quadratic-discriminant | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-54-quadratic-discriminant-2026-10-18-2695b4ec | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-54-quadratic-discriminant-2026-10-18-2695b4ec | state=ran hash=2695b4ec asof=2026-10-18 |
| A0247 | execute | crypto-warmup20-validation-56-randomized-newton-trees | 2026-10-18T05:24:11+00:00 | crypto-warmup20-validation-56-randomized-newton-trees | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-56-randomized-newton-trees-2026-10-18-3f437dd8 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-56-randomized-newton-trees-2026-10-18-3f437dd8 | state=ran hash=3f437dd8 asof=2026-10-18 |
| A0248 | execute | crypto-warmup20-validation-55-histogram-logloss | 2026-10-18T05:24:19+00:00 | crypto-warmup20-validation-55-histogram-logloss | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-55-histogram-logloss-2026-10-18-b953bdf9 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-55-histogram-logloss-2026-10-18-b953bdf9 | state=ran hash=b953bdf9 asof=2026-10-18 |
| A0249 | execute | crypto-warmup20-validation-57-deep-relu-residual | 2026-10-18T05:24:19+00:00 | crypto-warmup20-validation-57-deep-relu-residual | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-57-deep-relu-residual-2026-10-18-8043ba04 | /home/russell/data/crypto_trading/research/warmup20-20261018/runs/validation/crypto-warmup20-validation-57-deep-relu-residual-2026-10-18-8043ba04 | state=ran hash=8043ba04 asof=2026-10-18 |
| A0250 | research | warmup20/2026-10-18-synthesis | 2026-10-18T05:33:02+00:00 | Aligned warm-up validation of fifty-seven probability experiments | docs/research/warmup20/2026-10-18-synthesis.md | docs/research/warmup20/2026-10-18-synthesis.md |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0077 | Data windows W1-W5 | Lock predictor selection/HPO, check, retrain, MIO tuning and holdout windows before any modelling | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-data-windows | judgemental |  |
| A0078 | Predictor zoo grid | 5 models x 4 losses x pooled/unpooled on W1; 15-horizon vol-standardized return target | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-zoo-grid | judgemental |  |
