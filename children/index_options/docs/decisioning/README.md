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
| A1168 | acquire | register-source theta-exact-expiry-archive | 2026-10-19T13:59:37+00:00 | --root /home/russell/data/index_options/theta-strike-screen-20261018/ob --source  --stream  --mode | 5c635f452418868176f4340a032b3edc50297ce810979eddd888cd34f40c276b | /home/russell/data/index_options/theta-strike-screen-20261018/ob | connector=localblobs |
| A1169 | acquire | backfill theta-exact-expiry-archive/files | 2026-10-19T13:59:48+00:00 | --root /home/russell/data/index_options/theta-strike-screen-20261018/ob --source theta-exact-expiry-archive --stream files --mode backfill | db232e0ee0a8475f83fe4914a0a861a8589cb5e484098b76931b9973c51d384b | /home/russell/data/index_options/theta-strike-screen-20261018/ob |  |
| A1170 | acquire | register-source theta-prices-index-daily | 2026-10-19T13:59:48+00:00 | --root /home/russell/data/index_options/theta-strike-screen-20261018/ob --source  --stream  --mode | eba79abf964bcba4a3a31a84c211a5c73174b93d7dbe12c8f12f4bed8227c357 | /home/russell/data/index_options/theta-strike-screen-20261018/ob | connector=localtables |
| A1171 | acquire | backfill theta-prices-index-daily/index_daily | 2026-10-19T13:59:51+00:00 | --root /home/russell/data/index_options/theta-strike-screen-20261018/ob --source theta-prices-index-daily --stream index_daily --mode backfill | 16b88f1e41f5fb3e6f8da1d6e6b499684bc41a6c7f310501408409e8c6dd49f7 | /home/russell/data/index_options/theta-strike-screen-20261018/ob |  |
| A1172 | acquire | register-source theta-own-iv-index-daily | 2026-10-19T13:59:52+00:00 | --root /home/russell/data/index_options/theta-strike-screen-20261018/ob --source  --stream  --mode | 8f048bb7a8d2fcabded6dd96fbdca858ce0114b61d045172baeb63c6b1416e90 | /home/russell/data/index_options/theta-strike-screen-20261018/ob | connector=localtables |
| A1173 | acquire | backfill theta-own-iv-index-daily/index_daily | 2026-10-19T13:59:54+00:00 | --root /home/russell/data/index_options/theta-strike-screen-20261018/ob --source theta-own-iv-index-daily --stream index_daily --mode backfill | d8f7734ceaf9c836c9c2793aeb8129a47b751de58d5eb76436d3970b34a5ca0a | /home/russell/data/index_options/theta-strike-screen-20261018/ob |  |
| A1174 | acquire | register-source theta-raw-chain-features | 2026-10-19T16:03:12+00:00 | --root /home/russell/data/index_options/theta-strike-screen-20261018/ob --source  --stream  --mode | efba25f552e43746ab00b1365371295142079204068799fdf03359deb3f8f99c | /home/russell/data/index_options/theta-strike-screen-20261018/ob | connector=localblobs |
| A1175 | acquire | backfill theta-raw-chain-features/files | 2026-10-19T16:03:13+00:00 | --root /home/russell/data/index_options/theta-strike-screen-20261018/ob --source theta-raw-chain-features --stream files --mode backfill | db8edf2475131a87354ad6fbfff55380ad5c41bcd0356a69db397a889f1bb9d2 | /home/russell/data/index_options/theta-strike-screen-20261018/ob |  |
| A1176 | execute | strike-region-scores | 2026-10-19T23:26:05+00:00 | strike-region-scores | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-matrix/strike-region-scores-2023-12-31-cb85065e | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-matrix/strike-region-scores-2023-12-31-cb85065e | state=ran hash=cb85065e asof=2023-12-31 |
| A1177 | execute | strike-region-scores | 2026-10-20T00:18:57+00:00 | strike-region-scores | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-matrix/strike-region-scores-2023-12-31-cb85065e | /home/russell/data/index_options/theta-strike-screen-20261018/scores/run-matrix/strike-region-scores-2023-12-31-cb85065e | state=error hash=cb85065e asof=2023-12-31 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
