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
| A1067 | execute | pooled-zoo-417-step7 | 2026-10-05T09:14:53+00:00 | /home/russell/dskit/.venv/bin/python -m dskit.pipeline workflow configs/workflow-pooled-zoo-417.json --only step7 | pipeline_runs/pooled-zoo-417 |  | ADR-0236 pooled zoo full run: python -m dskit.pipeline workflow configs/workflow-pooled-zoo-417.json --only step7; exit 0 |
| A1068 | execute | pooled-zoo-417-report | 2026-10-05T09:14:58+00:00 | /home/russell/dskit/.venv/bin/python -m dskit.pipeline workflow configs/workflow-pooled-zoo-417.json --only report | pipeline_runs/pooled-zoo-417 |  | ADR-0236 pooled zoo full run: python -m dskit.pipeline workflow configs/workflow-pooled-zoo-417.json --only report; exit 0 |
| A1069 | execute | perticker-zoo-417-bench | 2026-10-05T11:15:27+00:00 | /home/russell/dskit/.venv/bin/python -W ignore /home/russell/pt_bench.py | pipeline_runs/perticker-zoo-417/bench |  | ADR-0236 amendment 3 benchmark: one per-ticker fit+score per kind (mlp gru lstm cnn lgbm) on CPU (1 thread) and CUDA, batch 512, 3 real tickers, warm-up fold, DTE 31; picks device and group workers; exit 1 |
| A1070 | execute | perticker-zoo-417-bench | 2026-10-05T11:16:51+00:00 | /home/russell/dskit/.venv/bin/python -W ignore /home/russell/pt_bench.py | pipeline_runs/perticker-zoo-417/bench |  | ADR-0236 amendment 3 benchmark: one per-ticker fit+score per kind (mlp gru lstm cnn lgbm) on CPU (1 thread) and CUDA, batch 512, 3 real tickers, warm-up fold, DTE 31; picks device and group workers; exit 0 |
| A1071 | execute | perticker-zoo-417-smoke-8-panel | 2026-10-05T11:36:26+00:00 | /home/russell/dskit/.venv/bin/python -m index_options.cdf_study pipeline_runs/perticker-zoo-417/smoke/w8.json --stage panel | pipeline_runs/perticker-zoo-417/smoke |  | ADR-0236 amendment 3 smoke: per-ticker zoo, 42 tickers, one base candidate per kind, warm-up fold, real CLI stage panel with DSKIT_GROUP_WORKERS=8; equivalence of 8 workers vs 1 and timing; exit 0 |
| A1072 | execute | perticker-zoo-417-smoke-8-search | 2026-10-05T11:37:45+00:00 | /home/russell/dskit/.venv/bin/python -m index_options.cdf_study pipeline_runs/perticker-zoo-417/smoke/w8.json --stage search --partition smoke | pipeline_runs/perticker-zoo-417/smoke |  | ADR-0236 amendment 3 smoke: per-ticker zoo, 42 tickers, one base candidate per kind, warm-up fold, real CLI stage search with DSKIT_GROUP_WORKERS=8; equivalence of 8 workers vs 1 and timing; exit 0 |
| A1073 | execute | perticker-zoo-417-smoke-1-search | 2026-10-05T11:41:45+00:00 | /home/russell/dskit/.venv/bin/python -m index_options.cdf_study pipeline_runs/perticker-zoo-417/smoke/w1.json --stage search --partition smoke | pipeline_runs/perticker-zoo-417/smoke |  | ADR-0236 amendment 3 smoke: per-ticker zoo, 42 tickers, one base candidate per kind, warm-up fold, real CLI stage search with DSKIT_GROUP_WORKERS=1; equivalence of 8 workers vs 1 and timing; exit 0 |
| A1074 | execute | perticker-zoo-417-smoke-8-panel | 2026-10-05T12:18:58+00:00 | /home/russell/dskit/.venv/bin/python -m index_options.cdf_study pipeline_runs/perticker-zoo-417/smoke/w8.json --stage panel | pipeline_runs/perticker-zoo-417/smoke |  | ADR-0236 amendment 3 smoke: per-ticker zoo, 42 tickers, one base candidate per kind, warm-up fold, real CLI stage panel with DSKIT_GROUP_WORKERS=8; equivalence of 8 workers vs 1 and timing; exit 0 |
| A1075 | execute | perticker-zoo-417-smoke-8-search | 2026-10-05T12:20:23+00:00 | /home/russell/dskit/.venv/bin/python -m index_options.cdf_study pipeline_runs/perticker-zoo-417/smoke/w8.json --stage search --partition smoke | pipeline_runs/perticker-zoo-417/smoke |  | ADR-0236 amendment 3 smoke: per-ticker zoo, 42 tickers, one base candidate per kind, warm-up fold, real CLI stage search with DSKIT_GROUP_WORKERS=8; equivalence of 8 workers vs 1 and timing; exit 0 |
| A1076 | execute | perticker-zoo-417-smoke-1-search | 2026-10-05T12:24:07+00:00 | /home/russell/dskit/.venv/bin/python -m index_options.cdf_study pipeline_runs/perticker-zoo-417/smoke/w1.json --stage search --partition smoke | pipeline_runs/perticker-zoo-417/smoke |  | ADR-0236 amendment 3 smoke: per-ticker zoo, 42 tickers, one base candidate per kind, warm-up fold, real CLI stage search with DSKIT_GROUP_WORKERS=1; equivalence of 8 workers vs 1 and timing; exit 0 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
