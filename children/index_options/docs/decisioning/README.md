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
| A1013 | acquire | backfill handoff/dates | 2026-10-03T18:24:49+00:00 | --root pipeline_runs/top5-opt/MSTR/step2/onboard_MSTR_h7 --source handoff --stream dates --mode backfill | 154bb305808b1f9207e9c1dd61d364c96b105c04736eac07a2de70e7557d5284 | pipeline_runs/top5-opt/MSTR/step2/onboard_MSTR_h7 |  |
| A1014 | execute | step3-holdout-folds | 2026-10-03T18:24:49+00:00 | step3-holdout-folds | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/top5-opt/MSTR/step3/runs_MSTR_h7/step3-holdout-folds-2026-10-01-26458bf4 | /home/russell/wt/stock-lane/children/index_options/pipeline_runs/top5-opt/MSTR/step3/runs_MSTR_h7/step3-holdout-folds-2026-10-01-26458bf4 | state=ran hash=26458bf4 asof=2026-10-01 |
| A1015 | acquire | register-source option-activity | 2026-10-03T19:13:36+00:00 | --root /home/russell/data/stock_options/option-universe-300-ranking --source  --stream  --mode | 69b5839c937d44b5e9686b4262dddf8a946d3b2c5c593c890cc83bd2511f991b | /home/russell/data/stock_options/option-universe-300-ranking | connector=dskit.onboarding.libs.alpaca:AlpacaOptionActivityConnector |
| A1016 | acquire | register-source option-activity | 2026-10-03T19:13:43+00:00 | --root /home/russell/data/stock_options/option-universe-300-ranking --source  --stream  --mode | 69b5839c937d44b5e9686b4262dddf8a946d3b2c5c593c890cc83bd2511f991b | /home/russell/data/stock_options/option-universe-300-ranking | connector=dskit.onboarding.libs.alpaca:AlpacaOptionActivityConnector |
| A1017 | acquire | backfill option-activity/underlyings | 2026-10-03T19:13:44+00:00 | --root /home/russell/data/stock_options/option-universe-300-ranking --source option-activity --stream underlyings --mode backfill | fcfa85f67e03e589add3e96ca3b5ae514dbb716ce7f95df610fe11e6114d8f16 | /home/russell/data/stock_options/option-universe-300-ranking |  |
| A1018 | acquire | register-source s | 2026-10-03T19:13:58+00:00 | --root /tmp/claude-1000/-home-russell-dskit/68909909-20b5-4f41-8df8-dd0d228dee2a/scratchpad/sampleroot --source  --stream  --mode | e886d744618f650478d660e8408995ac48b270d4ba96b8082be3b7260c2c09f7 | /tmp/claude-1000/-home-russell-dskit/68909909-20b5-4f41-8df8-dd0d228dee2a/scratchpad/sampleroot | connector=dskit.onboarding.libs.alpaca:AlpacaOptionActivityConnector |
| A1019 | acquire | backfill s/chain_volume | 2026-10-03T19:14:01+00:00 | --root /tmp/claude-1000/-home-russell-dskit/68909909-20b5-4f41-8df8-dd0d228dee2a/scratchpad/sampleroot --source s --stream chain_volume --mode backfill | b90bccf44e9bf7ad2c932a4653883dbb3a32894a5c06fd4aca625b53cf710a07 | /tmp/claude-1000/-home-russell-dskit/68909909-20b5-4f41-8df8-dd0d228dee2a/scratchpad/sampleroot |  |
| A1020 | acquire | backfill option-activity/chain_volume | 2026-10-03T20:38:11+00:00 | --root /home/russell/data/stock_options/option-universe-300-ranking --source option-activity --stream chain_volume --mode backfill | 42cb72313b38008045033518737e1930b5c9a919c0b6c3e8d092abd18b6da917 | /home/russell/data/stock_options/option-universe-300-ranking |  |
| A1021 | execute | option-universe-300-rank | 2026-10-03T20:38:19+00:00 | option-universe-300-rank | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/option-universe-300-rank/runs/option-universe-300-rank-2026-10-03-602ee901 | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/option-universe-300-rank/runs/option-universe-300-rank-2026-10-03-602ee901 | state=ran hash=602ee901 asof=2026-10-03 |
| A1022 | execute | option-universe-300-rank | 2026-10-03T20:47:25+00:00 | option-universe-300-rank | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/option-universe-300-rank/runs/option-universe-300-rank-2026-10-03-b9f377d6 | /home/russell/wt/option-universe-300/children/index_options/pipeline_runs/option-universe-300-rank/runs/option-universe-300-rank-2026-10-03-b9f377d6 | state=ran hash=b9f377d6 asof=2026-10-03 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
