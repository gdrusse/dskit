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
| A0038 | execute | staged plan | 2026-09-24T02:06:37+00:00 | /home/russell/wt/index-options-real-data/children/index_options/configs/run-real-zoo.json | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-zoo-staged-2026-09-23-71673101/stages/plan.json |  | stage_token=716731013552d086e18ddb91726a4281ddeae0622a1b1be0ec24a239f262f17c:plan; state=ran; sha256=71243dec29abda70ec2d1f04425c65bf549a4d997a638ef9d21e9671c5f19ed6; reason= |
| A0039 | execute | staged approval | 2026-09-24T02:06:37+00:00 | /home/russell/wt/index-options-real-data/children/index_options/configs/run-real-zoo.json | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-zoo-staged-2026-09-23-71673101/stages/approval.json |  | stage_token=716731013552d086e18ddb91726a4281ddeae0622a1b1be0ec24a239f262f17c:approval; state=ran; sha256=7f0560dd7c218a6b4d8589511b2c2c4cde43d1711b69da98f3fd4d789bc56254; reason= |
| A0040 | execute | index-options-real-distribution walk-forward | 2026-09-24T02:07:05+00:00 | index-options-real-distribution | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-distribution-walkforward-2026-09-23-7fd69204 | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-distribution-walkforward-2026-09-23-7fd69204 | state=ran folds=32 hash=7fd69204 asof=2026-09-23 |
| A0041 | execute | index-options-real-har walk-forward | 2026-09-24T02:07:34+00:00 | index-options-real-har | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-har-walkforward-2026-09-23-f87deec2 | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-har-walkforward-2026-09-23-f87deec2 | state=ran folds=32 hash=f87deec2 asof=2026-09-23 |
| A0042 | execute | index-options-real-lightgbm walk-forward | 2026-09-24T02:10:55+00:00 | index-options-real-lightgbm | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-lightgbm-walkforward-2026-09-23-cc07b9f5 | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-lightgbm-walkforward-2026-09-23-cc07b9f5 | state=ran folds=32 hash=cc07b9f5 asof=2026-09-23 |
| A0043 | execute | index-options-real-vix walk-forward | 2026-09-24T02:11:25+00:00 | index-options-real-vix | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-vix-walkforward-2026-09-23-161fed73 | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-vix-walkforward-2026-09-23-161fed73 | state=ran folds=32 hash=161fed73 asof=2026-09-23 |
| A0044 | execute | index-options-real-har-vix walk-forward | 2026-09-24T02:11:54+00:00 | index-options-real-har-vix | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-har-vix-walkforward-2026-09-23-da0d6948 | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-har-vix-walkforward-2026-09-23-da0d6948 | state=ran folds=32 hash=da0d6948 asof=2026-09-23 |
| A0045 | execute | index-options-real-lightgbm-vix walk-forward | 2026-09-24T02:13:43+00:00 | index-options-real-lightgbm-vix | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-lightgbm-vix-walkforward-2026-09-23-9a2612e0 | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-lightgbm-vix-walkforward-2026-09-23-9a2612e0 | state=ran folds=32 hash=9a2612e0 asof=2026-09-23 |
| A0046 | execute | staged run | 2026-09-24T02:13:43+00:00 | /home/russell/wt/index-options-real-data/children/index_options/configs/run-real-zoo.json | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-zoo-staged-2026-09-23-71673101/stages/run.json |  | stage_token=716731013552d086e18ddb91726a4281ddeae0622a1b1be0ec24a239f262f17c:run; state=ran; sha256=23a7fc96cafbe9dffa63e1c722a1541d9af48696b3db420626a281af43440a71; reason= |
| A0047 | execute | staged compare | 2026-09-24T02:13:43+00:00 | /home/russell/wt/index-options-real-data/children/index_options/configs/run-real-zoo.json | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-zoo-staged-2026-09-23-71673101/stages/compare.json |  | stage_token=716731013552d086e18ddb91726a4281ddeae0622a1b1be0ec24a239f262f17c:compare; state=ran; sha256=4950c4ce7839ea5498429aea150e2d6465b0959a95385d01eadf9ffe51586a27; reason= |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
