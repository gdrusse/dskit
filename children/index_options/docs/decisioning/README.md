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
| A1108 | acquire | backfill production-older-bars-20261007/files | 2026-10-08T01:29:22+00:00 | --root /home/russell/data/index_options/ob --source production-older-bars-20261007 --stream files --mode backfill | ce5dd42689e8c4847e69d75ccd71e016705f0b0724d1c799a96801afaa9e2203 | /home/russell/data/index_options/ob |  |
| A1109 | acquire | register-source production-older-bars-20261007-v2 | 2026-10-08T01:30:03+00:00 | --root /home/russell/data/index_options/ob --source  --stream  --mode | 13024dfffc62a5d1a2e79253ecca90e825647cab0a439e3acdb0230c6faaf1d9 | /home/russell/data/index_options/ob | connector=localblobs |
| A1110 | acquire | backfill production-older-bars-20261007-v2/files | 2026-10-08T01:30:03+00:00 | --root /home/russell/data/index_options/ob --source production-older-bars-20261007-v2 --stream files --mode backfill | f456ba9df1ef668a02209d8e657d8b8d13d04312bd8af9eb4b70fc2b6a68182f | /home/russell/data/index_options/ob |  |
| A1111 | execute | Production prerequisites closed and counted pilot launched | 2026-10-08T02:36:09+00:00 | ADR0251-0253;candidate4f9e1084;production-execution-plan-v2.json | 111011-row panel;exact recovery;two clean execution reviews;pilot-v2 |  | Pre-fit CUDA environment refusal retained;no2026;85 source-qualified symbols;216 ceiling unchanged;not production qualified |
| A1112 | execute | Production pilot completed | 2026-10-08T02:37:31+00:00 | production-feature-selection-v2.json;seed11;developmentfold1 | bestcheckpoint35;55epochs;7247pairedforecasts |  | 47.421s fit;finitegradients;10943parameters;backup inference verification next;no2026 |
| A1113 | execute | Stage1 features complete and finite HPO dispatched | 2026-10-08T03:03:58+00:00 | 15verifiedfits;21297pairedforecastsperarm;frozenHPOgrid | minus_momentumselected;15newHPOfitsreserved;3verifiedreuse;stage1backuprecovery |  | Allfeaturearmsunderperformempiricalreference;noqualification;no2026;31of216conservativecharge |
| A1114 | execute | Complete bounded forecasting evaluation with no promotion | 2026-10-08T04:14:02+00:00 | Frozen PatchTST32 minus_momentum; development2021-2022; confirmation2023; evaluation2024-2025; seeds11,29,47 | 66 completed fits;67of216 conservative charge;21587 paired evaluation forecasts perseed;allcheckpoint recoveryverified;closingmemo andaggregateJSON |  | Evaluation weightedskills -0.077101%,-0.189704%,-0.199768%;no promotion/reserve/radius;no2026 orMIO;existingbehaviorreviewsclosed;finaldocumentreviews pending |
| A1115 | execute | Restored-universe PatchTST preflight and counted pilot | 2026-10-08T14:24:50+00:00 | ADR0254; candidate025f4bd5; prior67/216charges;392research-admitted symbols | 457919panel rows;841559source dates zeroresidual;303/319/354warmupheads;two clean independent reviews;backup-denied recovery;pilot launched | /home/russell/data/index_options/production-development-audit-20261007/universe-rerun-20261008 | Existing model/runner unchanged. Missing split inventories remain assumptions. Maximum66new fits. No2026/MIO/backtest/production qualification. |
| A1116 | execute | Prepare owner-requested Claude calibration/backtest handoff | 2026-10-08T17:44:53+00:00 | ADR0254; reviewed robustification formulation; verified forecasting results and model inventory | docs/memos/2026-10-08-claude-calibration-backtest-handoff.md | /home/russell/data/index_options/production-development-audit-20261007/universe-rerun-20261008 | Documentation only. Next-session walkthrough covers rho/CDF bands, inference identities, untouched versus retrospective evidence, option-data/clock/tenor gaps, realistic replay and MIO/backtest gates. No MIO, backtest, new fit or2026 access. Independent scoped memo review requested. |
| A1117 | execute | Complete restored-universe forecasting and inference recovery | 2026-10-08T20:38:40+00:00 | ADR0254; frozen base PatchTST32; no2026; prior67charges | 66unique fits; evaluation seed11/29/47 skills +0.278858/+0.240921/+0.254481%; complete training/ticker reports and recovered inference inventory | /home/russell/data/index_options/production-development-audit-20261007/universe-rerun-20261008 | All66 new fits completed without failure; cumulative133/216. Evaluation27fits/10383shards recovered, fixed seed11/fold9 inference parity. Nominal95% coverage91.616%; no production qualification, calibrated radius/bands, MIO or backtest. Final scoped reviews and wrap pending. |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
