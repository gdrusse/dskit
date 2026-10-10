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
| A0078 | research | plan/2026-10-09-zoo-grid | 2026-10-09T17:09:37+00:00 | Owner-approved predictor zoo grid and target | docs/plans/2026-10-09-data-windows.md |  | plan only; nothing run |
| A0079 | acquire | register-source panel-minute | 2026-10-10T03:00:08+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | 15126db80b6543cb5d1193a00e604501349d4acad6346bc30180c6dbd2868c42 | /home/russell/data/crypto_trading/ob | connector=localblobs |
| A0080 | acquire | backfill panel-minute/files | 2026-10-10T03:00:10+00:00 | --root /home/russell/data/crypto_trading/ob --source panel-minute --stream files --mode backfill | 57fc9d448bbe2752bfc89314b13434725ed0e5865106731279e62228982fa459 | /home/russell/data/crypto_trading/ob |  |
| A0081 | execute | probe-mlp walk-forward | 2026-10-10T03:10:43+00:00 | probe-mlp | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs/probe-mlp-walkforward-2026-04-21-1fcd7002 | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs/probe-mlp-walkforward-2026-04-21-1fcd7002 | state=ran folds=1 hash=1fcd7002 asof=2026-04-21 |
| A0082 | execute | probe-cnn1d walk-forward | 2026-10-10T03:13:09+00:00 | probe-cnn1d | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs/probe-cnn1d-walkforward-2026-04-21-2eafe512 | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs/probe-cnn1d-walkforward-2026-04-21-2eafe512 | state=ran folds=1 hash=2eafe512 asof=2026-04-21 |
| A0083 | execute | probe-gru walk-forward | 2026-10-10T03:15:10+00:00 | probe-gru | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs/probe-gru-walkforward-2026-04-21-1792a2e2 | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs/probe-gru-walkforward-2026-04-21-1792a2e2 | state=ran folds=1 hash=1792a2e2 asof=2026-04-21 |
| A0084 | execute | probe-transformer walk-forward | 2026-10-10T03:17:33+00:00 | probe-transformer | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs/probe-transformer-walkforward-2026-04-21-3b5b2a11 | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs/probe-transformer-walkforward-2026-04-21-3b5b2a11 | state=ran folds=1 hash=3b5b2a11 asof=2026-04-21 |
| A0085 | execute | probe-patchtst walk-forward | 2026-10-10T03:19:53+00:00 | probe-patchtst | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs/probe-patchtst-walkforward-2026-04-21-9d181d20 | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs/probe-patchtst-walkforward-2026-04-21-9d181d20 | state=ran folds=1 hash=9d181d20 asof=2026-04-21 |
| A0086 | execute | probe-mlp-b4096 walk-forward | 2026-10-10T03:28:16+00:00 | probe-mlp-b4096 | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs-b4096/probe-mlp-b4096-walkforward-2026-04-21-fa11a0c2 | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs-b4096/probe-mlp-b4096-walkforward-2026-04-21-fa11a0c2 | state=ran folds=1 hash=fa11a0c2 asof=2026-04-21 |
| A0087 | execute | probe-patchtst-b4096 walk-forward | 2026-10-10T03:30:03+00:00 | probe-patchtst-b4096 | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs-b4096/probe-patchtst-b4096-walkforward-2026-04-21-433f93b2 | /tmp/claude-1000/-home-russell-wt-crypto-project-orientation-20261009/e14d4cc2-f352-4f4e-8b6e-444801c55e83/scratchpad/probe/runs-b4096/probe-patchtst-b4096-walkforward-2026-04-21-433f93b2 | state=ran folds=1 hash=433f93b2 asof=2026-04-21 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0077 | Data windows W1-W5 | Lock predictor selection/HPO, check, retrain, MIO tuning and holdout windows before any modelling | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-data-windows | judgemental |  |
| A0078 | Predictor zoo grid | 5 models x 4 losses x pooled/unpooled on W1; 15-horizon vol-standardized return target | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-zoo-grid | judgemental |  |
