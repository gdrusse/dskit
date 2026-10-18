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
| A0284 | execute | crypto-pocket-tuning-validation-07-p44-strength-025 | 2026-10-18T14:27:35+00:00 | crypto-pocket-tuning-validation-07-p44-strength-025 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-07-p44-strength-025-2026-10-18-20f86dfc | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-07-p44-strength-025-2026-10-18-20f86dfc | state=ran hash=20f86dfc asof=2026-10-18 |
| A0285 | execute | crypto-pocket-tuning-validation-17-p44-pruning-1000 | 2026-10-18T14:27:40+00:00 | crypto-pocket-tuning-validation-17-p44-pruning-1000 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-17-p44-pruning-1000-2026-10-18-014dc1d5 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-17-p44-pruning-1000-2026-10-18-014dc1d5 | state=ran hash=014dc1d5 asof=2026-10-18 |
| A0286 | execute | crypto-pocket-tuning-validation-08-p44-strength-050 | 2026-10-18T14:28:22+00:00 | crypto-pocket-tuning-validation-08-p44-strength-050 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-08-p44-strength-050-2026-10-18-79b9af07 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-08-p44-strength-050-2026-10-18-79b9af07 | state=ran hash=79b9af07 asof=2026-10-18 |
| A0287 | execute | crypto-pocket-tuning-validation-18-p44-pruning-100000 | 2026-10-18T14:28:28+00:00 | crypto-pocket-tuning-validation-18-p44-pruning-100000 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-18-p44-pruning-100000-2026-10-18-b1444bbd | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-18-p44-pruning-100000-2026-10-18-b1444bbd | state=ran hash=b1444bbd asof=2026-10-18 |
| A0288 | execute | crypto-pocket-tuning-validation-09-p44-strength-075 | 2026-10-18T14:29:07+00:00 | crypto-pocket-tuning-validation-09-p44-strength-075 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-09-p44-strength-075-2026-10-18-56b9cc53 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-09-p44-strength-075-2026-10-18-56b9cc53 | state=ran hash=56b9cc53 asof=2026-10-18 |
| A0289 | execute | crypto-pocket-tuning-validation-19-p48-vol-boundary-050 | 2026-10-18T14:29:10+00:00 | crypto-pocket-tuning-validation-19-p48-vol-boundary-050 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-19-p48-vol-boundary-050-2026-10-18-9acd5ff4 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-19-p48-vol-boundary-050-2026-10-18-9acd5ff4 | state=ran hash=9acd5ff4 asof=2026-10-18 |
| A0290 | execute | crypto-pocket-tuning-validation-10-p38-strength-025 | 2026-10-18T14:29:52+00:00 | crypto-pocket-tuning-validation-10-p38-strength-025 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-10-p38-strength-025-2026-10-18-5a04bc4b | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-10-p38-strength-025-2026-10-18-5a04bc4b | state=ran hash=5a04bc4b asof=2026-10-18 |
| A0291 | execute | crypto-pocket-tuning-validation-20-p48-vol-boundary-200 | 2026-10-18T14:29:56+00:00 | crypto-pocket-tuning-validation-20-p48-vol-boundary-200 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-20-p48-vol-boundary-200-2026-10-18-9caea61a | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/validation/crypto-pocket-tuning-validation-20-p48-vol-boundary-200-2026-10-18-9caea61a | state=ran hash=9caea61a asof=2026-10-18 |
| A0292 | research | pocket-tuning/2026-10-18-synthesis | 2026-10-18T14:35:31+00:00 | Focused tuning found directional pockets without validated advantage | docs/research/pocket-tuning/2026-10-18-synthesis.md | docs/research/pocket-tuning/2026-10-18-synthesis.md |  |
| A0293 | research | attribution/2026-10-18-synthesis | 2026-10-18T15:58:29+00:00 | Prediction attribution and quote quality | docs/research/attribution/2026-10-18-synthesis.md | docs/research/attribution/2026-10-18-synthesis.md |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0077 | Data windows W1-W5 | Lock predictor selection/HPO, check, retrain, MIO tuning and holdout windows before any modelling | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-data-windows | judgemental |  |
| A0078 | Predictor zoo grid | 5 models x 4 losses x pooled/unpooled on W1; 15-horizon vol-standardized return target | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-zoo-grid | judgemental |  |
