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
| A0262 | execute | crypto-pocket-tuning-warmup-11-p38-strength-050 | 2026-10-18T14:18:20+00:00 | crypto-pocket-tuning-warmup-11-p38-strength-050 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-11-p38-strength-050-2026-10-18-d68c17e6 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-11-p38-strength-050-2026-10-18-d68c17e6 | state=ran hash=d68c17e6 asof=2026-10-18 |
| A0263 | execute | crypto-pocket-tuning-warmup-12-p38-strength-075 | 2026-10-18T14:18:44+00:00 | crypto-pocket-tuning-warmup-12-p38-strength-075 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-12-p38-strength-075-2026-10-18-7c1d302b | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-12-p38-strength-075-2026-10-18-7c1d302b | state=ran hash=7c1d302b asof=2026-10-18 |
| A0264 | execute | crypto-pocket-tuning-warmup-13-p24-pruning-1000 | 2026-10-18T14:19:04+00:00 | crypto-pocket-tuning-warmup-13-p24-pruning-1000 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-13-p24-pruning-1000-2026-10-18-0b6f69b8 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-13-p24-pruning-1000-2026-10-18-0b6f69b8 | state=ran hash=0b6f69b8 asof=2026-10-18 |
| A0265 | execute | crypto-pocket-tuning-warmup-14-p24-pruning-100000 | 2026-10-18T14:19:24+00:00 | crypto-pocket-tuning-warmup-14-p24-pruning-100000 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-14-p24-pruning-100000-2026-10-18-0a022808 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-14-p24-pruning-100000-2026-10-18-0a022808 | state=ran hash=0a022808 asof=2026-10-18 |
| A0266 | execute | crypto-pocket-tuning-warmup-15-p48-pruning-1000 | 2026-10-18T14:19:49+00:00 | crypto-pocket-tuning-warmup-15-p48-pruning-1000 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-15-p48-pruning-1000-2026-10-18-be904e58 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-15-p48-pruning-1000-2026-10-18-be904e58 | state=ran hash=be904e58 asof=2026-10-18 |
| A0267 | execute | crypto-pocket-tuning-warmup-16-p48-pruning-100000 | 2026-10-18T14:20:13+00:00 | crypto-pocket-tuning-warmup-16-p48-pruning-100000 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-16-p48-pruning-100000-2026-10-18-30443955 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-16-p48-pruning-100000-2026-10-18-30443955 | state=ran hash=30443955 asof=2026-10-18 |
| A0268 | execute | crypto-pocket-tuning-warmup-17-p44-pruning-1000 | 2026-10-18T14:20:39+00:00 | crypto-pocket-tuning-warmup-17-p44-pruning-1000 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-17-p44-pruning-1000-2026-10-18-59cca655 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-17-p44-pruning-1000-2026-10-18-59cca655 | state=ran hash=59cca655 asof=2026-10-18 |
| A0269 | execute | crypto-pocket-tuning-warmup-18-p44-pruning-100000 | 2026-10-18T14:21:04+00:00 | crypto-pocket-tuning-warmup-18-p44-pruning-100000 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-18-p44-pruning-100000-2026-10-18-2c307816 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-18-p44-pruning-100000-2026-10-18-2c307816 | state=ran hash=2c307816 asof=2026-10-18 |
| A0270 | execute | crypto-pocket-tuning-warmup-19-p48-vol-boundary-050 | 2026-10-18T14:21:29+00:00 | crypto-pocket-tuning-warmup-19-p48-vol-boundary-050 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-19-p48-vol-boundary-050-2026-10-18-478d7273 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-19-p48-vol-boundary-050-2026-10-18-478d7273 | state=ran hash=478d7273 asof=2026-10-18 |
| A0271 | execute | crypto-pocket-tuning-warmup-20-p48-vol-boundary-200 | 2026-10-18T14:21:55+00:00 | crypto-pocket-tuning-warmup-20-p48-vol-boundary-200 | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-20-p48-vol-boundary-200-2026-10-18-08b915cf | /home/russell/data/crypto_trading/research/pocket-tuning-20261018/runs/warmup/crypto-pocket-tuning-warmup-20-p48-vol-boundary-200-2026-10-18-08b915cf | state=ran hash=08b915cf asof=2026-10-18 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0077 | Data windows W1-W5 | Lock predictor selection/HPO, check, retrain, MIO tuning and holdout windows before any modelling | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-data-windows | judgemental |  |
| A0078 | Predictor zoo grid | 5 models x 4 losses x pooled/unpooled on W1; 15-horizon vol-standardized return target | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-zoo-grid | judgemental |  |
