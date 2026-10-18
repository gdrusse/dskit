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
| A0327 | execute | crypto-polymarket20-validation-11-ard-newton | 2026-10-18T17:56:27+00:00 | crypto-polymarket20-validation-11-ard-newton | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-11-ard-newton-2026-10-18-13fc35aa | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-11-ard-newton-2026-10-18-13fc35aa | state=ran hash=13fc35aa asof=2026-10-18 |
| A0328 | execute | crypto-polymarket20-validation-12-ridge-newton | 2026-10-18T17:56:37+00:00 | crypto-polymarket20-validation-12-ridge-newton | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-12-ridge-newton-2026-10-18-f6d797f1 | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-12-ridge-newton-2026-10-18-f6d797f1 | state=ran hash=f6d797f1 asof=2026-10-18 |
| A0329 | execute | crypto-polymarket20-validation-13-bayesian-newton | 2026-10-18T17:56:48+00:00 | crypto-polymarket20-validation-13-bayesian-newton | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-13-bayesian-newton-2026-10-18-c1e256ba | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-13-bayesian-newton-2026-10-18-c1e256ba | state=ran hash=c1e256ba asof=2026-10-18 |
| A0330 | execute | crypto-polymarket20-validation-14-spline-newton | 2026-10-18T17:56:59+00:00 | crypto-polymarket20-validation-14-spline-newton | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-14-spline-newton-2026-10-18-51385da7 | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-14-spline-newton-2026-10-18-51385da7 | state=ran hash=51385da7 asof=2026-10-18 |
| A0331 | execute | crypto-polymarket20-validation-15-radial-newton | 2026-10-18T17:57:08+00:00 | crypto-polymarket20-validation-15-radial-newton | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-15-radial-newton-2026-10-18-59b9c8c6 | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-15-radial-newton-2026-10-18-59b9c8c6 | state=ran hash=59b9c8c6 asof=2026-10-18 |
| A0332 | execute | crypto-polymarket20-validation-16-age-newton | 2026-10-18T17:57:19+00:00 | crypto-polymarket20-validation-16-age-newton | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-16-age-newton-2026-10-18-8f2f946b | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-16-age-newton-2026-10-18-8f2f946b | state=ran hash=8f2f946b asof=2026-10-18 |
| A0333 | execute | crypto-polymarket20-validation-17-histogram-brier | 2026-10-18T17:57:30+00:00 | crypto-polymarket20-validation-17-histogram-brier | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-17-histogram-brier-2026-10-18-8fc1f92d | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-17-histogram-brier-2026-10-18-8fc1f92d | state=ran hash=8fc1f92d asof=2026-10-18 |
| A0334 | execute | crypto-polymarket20-validation-18-histogram-logloss | 2026-10-18T17:57:39+00:00 | crypto-polymarket20-validation-18-histogram-logloss | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-18-histogram-logloss-2026-10-18-63e8df74 | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-18-histogram-logloss-2026-10-18-63e8df74 | state=ran hash=63e8df74 asof=2026-10-18 |
| A0335 | execute | crypto-polymarket20-validation-19-forest-brier | 2026-10-18T17:57:50+00:00 | crypto-polymarket20-validation-19-forest-brier | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-19-forest-brier-2026-10-18-0bfc244e | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-19-forest-brier-2026-10-18-0bfc244e | state=ran hash=0bfc244e asof=2026-10-18 |
| A0336 | execute | crypto-polymarket20-validation-20-tanh-brier | 2026-10-18T17:58:02+00:00 | crypto-polymarket20-validation-20-tanh-brier | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-20-tanh-brier-2026-10-18-b8f47755 | /home/russell/data/crypto_trading/research/polymarket-predictability-20261018/runs/validation/crypto-polymarket20-validation-20-tanh-brier-2026-10-18-b8f47755 | state=ran hash=b8f47755 asof=2026-10-18 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0077 | Data windows W1-W5 | Lock predictor selection/HPO, check, retrain, MIO tuning and holdout windows before any modelling | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-data-windows | judgemental |  |
| A0078 | Predictor zoo grid | 5 models x 4 losses x pooled/unpooled on W1; 15-horizon vol-standardized return target | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-zoo-grid | judgemental |  |
