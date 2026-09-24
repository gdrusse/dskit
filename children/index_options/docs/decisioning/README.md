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
| A0002 | research | distribution-modeling/2026-09-23-evaluation | 2026-09-23T21:05:19+00:00 | Distribution forecast evaluation metrics | docs/research/distribution-modeling/2026-09-23-evaluation.md | docs/research/distribution-modeling/2026-09-23-evaluation.md |  |
| A0003 | research | distribution-modeling/2026-09-23-model-ladder | 2026-09-23T21:16:49+00:00 | Candidate model benchmark ladder | docs/research/distribution-modeling/2026-09-23-model-ladder.md | docs/research/distribution-modeling/2026-09-23-model-ladder.md |  |
| A0004 | research | distribution-modeling/2026-09-23-ml-dl-transformers | 2026-09-23T22:27:30+00:00 | ML, DL and transformer model evidence | docs/research/distribution-modeling/2026-09-23-ml-dl-transformers.md | docs/research/distribution-modeling/2026-09-23-ml-dl-transformers.md |  |
| A0005 | acquire | register-source cboe-index | 2026-09-24T01:46:47+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | 4140d62ab11b1f5a40d21d461baaa383313159906cfc1bf91db68fdfcaea411c | /home/russell/data/index_options/ob | connector=cboe |
| A0006 | acquire | register-source cboe-chain | 2026-09-24T01:46:48+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | 24433c8353b910938b386d7aceee2db212639363b3e385f71bcf878d854e750a | /home/russell/data/index_options/ob | connector=cboe |
| A0007 | acquire | backfill cboe-index/index_daily | 2026-09-24T01:46:51+00:00 | --root /home/russell/data/index_options/ob --source cboe-index --stream index_daily --mode backfill | b50255e32743649749dcab042e80b27ccc9ad752d9b3b95179d58d220698d116 | /home/russell/data/index_options/ob |  |
| A0008 | acquire | backfill cboe-index/index_daily | 2026-09-24T01:46:56+00:00 | --root /home/russell/data/index_options/ob --source cboe-index --stream index_daily --mode backfill |  | /home/russell/data/index_options/ob |  |
| A0009 | acquire | live cboe-chain/option_chain | 2026-09-24T01:47:11+00:00 | --root /home/russell/data/index_options/ob --source cboe-chain --stream option_chain --mode live | 31cd16937a701dbe39e81135d590fefbcb09a6489d91f1debd16a63c33af6ffa | /home/russell/data/index_options/ob |  |
| A0010 | acquire | live cboe-chain/option_chain | 2026-09-24T01:47:38+00:00 | --root /home/russell/data/index_options/ob --source cboe-chain --stream option_chain --mode live | 651fbb62d79a10197828f3c07823f111dce62e68314c942da28b80721d84f2af | /home/russell/data/index_options/ob |  |
| A0011 | execute | index-options-real-distribution walk-forward | 2026-09-24T01:48:34+00:00 | index-options-real-distribution | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-distribution-walkforward-2026-09-23-f7865f91 | /home/russell/wt/index-options-real-data/children/index_options/pipeline_runs/index-options-real-distribution-walkforward-2026-09-23-f7865f91 | state=ran folds=32 hash=f7865f91 asof=2026-09-23 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
