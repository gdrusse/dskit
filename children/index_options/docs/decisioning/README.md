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
| A1386 | acquire | backfill theta-exact-expiry-archive/files | 2026-11-05T14:12:35+00:00 | --root /dev/shm/full-span/ob --source theta-exact-expiry-archive --stream files --mode backfill | 7e04065e1b04728e46cb41cd11b564f91aca5a63f47c3fc9f45aa86d0456d9c7 | /dev/shm/full-span/ob |  |
| A1387 | acquire | register-source theta-prices-index-daily | 2026-11-05T14:12:35+00:00 | --root /dev/shm/full-span/ob --source  --stream  --mode | d479fb3804fbf426daf411c87179355ae2e32547dcd9c37effb7e396982eac47 | /dev/shm/full-span/ob | connector=localtables |
| A1388 | acquire | backfill theta-prices-index-daily/index_daily | 2026-11-05T14:12:40+00:00 | --root /dev/shm/full-span/ob --source theta-prices-index-daily --stream index_daily --mode backfill | b9fd3f131e93623c64aaed6e2127cb96fe8218b210286f843d8b4978ca46f45c | /dev/shm/full-span/ob |  |
| A1389 | acquire | register-source theta-own-iv-index-daily | 2026-11-05T14:12:40+00:00 | --root /dev/shm/full-span/ob --source  --stream  --mode | b37a7983fcace9ca70d1477dcb287d518a1c6184ade96c90fd857ec39cb9078b | /dev/shm/full-span/ob | connector=localtables |
| A1390 | acquire | backfill theta-own-iv-index-daily/index_daily | 2026-11-05T14:12:43+00:00 | --root /dev/shm/full-span/ob --source theta-own-iv-index-daily --stream index_daily --mode backfill | a1e69a40fe2410ef865af520ddcbbb012f40600b0508258a741df9cf61f1d4ac | /dev/shm/full-span/ob |  |
| A1391 | acquire | register-source theta-raw-chain-features | 2026-11-05T14:16:56+00:00 | --root /dev/shm/full-span/ob --source  --stream  --mode | 7864cb0c6c1928a0d249fdcfcd32ebca408c93ad4530240af02890693cf0c77d | /dev/shm/full-span/ob | connector=localblobs |
| A1392 | acquire | backfill theta-raw-chain-features/files | 2026-11-05T14:16:57+00:00 | --root /dev/shm/full-span/ob --source theta-raw-chain-features --stream files --mode backfill | 5ba1012debe8d1083cdbc16faa34082f1420aa34374bf8e30f5d935da1ad3d32 | /dev/shm/full-span/ob |  |
| A1393 | execute | full-span-scores-f90 | 2026-11-05T15:51:51+00:00 | full-span-scores-f90 | /mnt/d/full-span-rerun-20261105/scores/run-f90/full-span-scores-f90-2023-12-31-f9e6d5a8 | /mnt/d/full-span-rerun-20261105/scores/run-f90/full-span-scores-f90-2023-12-31-f9e6d5a8 | state=ran hash=f9e6d5a8 asof=2023-12-31 |
| A1394 | execute | full-span-scores-fl21 | 2026-11-05T16:29:50+00:00 | full-span-scores-fl21 | /mnt/d/full-span-rerun-20261105/scores/run-fl21/full-span-scores-fl21-2023-12-31-be7c9824 | /mnt/d/full-span-rerun-20261105/scores/run-fl21/full-span-scores-fl21-2023-12-31-be7c9824 | state=ran hash=be7c9824 asof=2023-12-31 |
| A1395 | execute | full-span-scores-f21 | 2026-11-05T17:20:34+00:00 | full-span-scores-f21 | /mnt/d/full-span-rerun-20261105/scores/run-f21-cuda/full-span-scores-f21-2023-12-31-0da16692 | /mnt/d/full-span-rerun-20261105/scores/run-f21-cuda/full-span-scores-f21-2023-12-31-0da16692 | state=ran hash=0da16692 asof=2023-12-31 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Primary score: threshold-weighted CRPS with weight 1 on \|z\| in [0.5, 2.5] tapering to 0.25 at z=0 and \|z\|>=3, with full-distribution CRPS beside it; Brier and reliability at the listed strikes; PIT/Berkowitz in the tails; only proper weighted scores; never choose the evaluation subset by outcomes. Strike regions are a pooled decomposition, never a cell axis. Predictability only: no P&L or strategy economics | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | Y |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
| A0003 | Procedural windows | Development 2017-01-03..2019-12-31 (fit plus last cal_n origins as early-stop band); validation 2020-01-02..2023-12-31 reported also by sub-period; holdout 2024-01-02..2025-11-28 sealed and opened once for promoted recipes; 2026 sealed forever; a row is on the earlier side of any boundary only if its label settles before it (label_reaches) | children/index_options/docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md | Y |  | research | distribution-modeling/2026-09-23-model-ladder | judgemental | docs/research/distribution-modeling/2026-09-23-model-ladder.md |
| A0004 | Fold rungs | Screen: warm-up fold (train 2017-2018, validate 2019; timing and learning curves only, never ranked) plus one scored fit trained through 2019 and scored 2020-2023. Promotion: quarterly expanding refits for each quarter of 2020-2023 with seeds 11/29/47. Holdout: quarterly expanding refits for each quarter of 2024-2025 on the same recipe, one opening (owner decision 2026-11-04, replacing yearly refits and the single fit through 2023) | children/index_options/docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md | Y |  | research | distribution-modeling/2026-09-23-ml-dl-transformers | judgemental | docs/research/distribution-modeling/2026-09-23-ml-dl-transformers.md |
| A0005 | Promotion rule and uncertainty | Eligible only if paired CRPS skill vs horizon_empirical and vs the market-implied reference is above zero on the full validation window and in at least 3 of 4 years; two-way cluster-robust intervals (expiry and symbol); never the best cell by score | children/index_options/docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md | Y |  | acquire | register-source cboe-index | judgemental | /home/russell/data/index_options/ob |
| A0006 | Cell definition | Cell = (ticker, expiry rank). Weekly names: ranks 1-8, rank k = the k-th listed Friday expiry (the 7-day windows 1-7, 8-14, ...). Monthly-only names: expiry position 1-3, per ticker but flagged underpowered, with a pooled monthly line beside them. Thursday holiday expiries stay | children/index_options/docs/research/distribution-modeling/2026-10-20-strike-screen-technical-design.md | Y |  | acquire | register-source cboe-chain | judgemental | /home/russell/data/index_options/ob |
| A0007 | Eligibility gate | Simple: a cell passes if the model beats the raw market-implied distribution AND the per-name horizon baseline on the A0002 score in at least 3 of 4 yearly folds and at least 3 of 5 regimes (S0 pre-crash, S1 covid crash, S2 recovery, S3 rate shock, S4 2023). No multiplicity correction yet: report the number passing next to the number expected by luck. The recalibrated market and the pooled baseline join when built | children/index_options/docs/research/distribution-modeling/2026-10-20-strike-screen-technical-design.md | Y |  | acquire | backfill cboe-index/index_daily | judgemental | /home/russell/data/index_options/ob |
| A0008 | Training sequence and run | Retrain periodically for all events: quarterly expanding retrains, each of 2019Q4 and 2020Q1-2023Q4 trained on all mature history before that quarter starts, weights fixed within the quarter. Locked recipe (owner 2026-11-05): fixed_loc on the locked ticker list (A0009), learning rate 1e-4, dropout 0.11, weight decay 0.045, hidden 64, width 8, 6 epochs, no per-fit epoch selection, seeds 11/29/47/53/71 averaged by linear pool; recipe SHA-256 b10d67deebb5be51fd2dd7fcf2a5f527c28aa7a84cd39ebc3ecd3a503e9e6bdc. Chosen by the full-span zoo (2020-2023, all passed A0005), then 135 Bayesian HPO trials and the follow-up 15-cell grid, tuned on 2019. Forecasts for all 17 quarters are produced, pinned and scored; a 2020Q1 rerun reproduced them hash for hash on the WSL GPU; 2019Q4 is the tuned year, not a test. Scores unread by agents. 2024-2025 forecasts are on hold and sealed (owner 2026-11-05). Supersedes the M53 recipe locked 2026-11-04 and A0004's screen rung. | children/index_options/configs/quarterly-forecasts-run.json;children/index_options/index_options/quarterly_forecasts.py;/home/russell/data/index_options/full-span-rerun-20261105/quarterly/recipe.json;/home/russell/data/index_options/full-span-rerun-20261105/zoo/zoo-selection.json | Y |  | acquire | backfill cboe-index/index_daily | judgemental | /home/russell/data/index_options/ob |
| A0009 | Locked ticker list | The one ticker list for every new fit, forecast and MIO run: 166 names, SHA-256 8eae0a07fe2bc667e597476f00909359d12c57c6d180f3f2d954e1b907fbc90e. Rule uses dates and input presence only (full span 2017-01-03..2025-12-31, at most 5 sessions missing, complete bars and liquidity, identity check, at least 40 market-eligible rows in 2017-2019). Any change needs an owner decision and a logged reason (owner 2026-11-04: lock the ticker list so that this does not happen again; only full-span tickers). Survival bias remains and is unmeasured | children/index_options/configs/locked-ticker-list.json;children/index_options/configs/locked-ticker-list-exclusions.csv;children/index_options/index_options/ticker_lock.py | Y |  | acquire | live cboe-chain/option_chain | judgemental | /home/russell/data/index_options/ob |
| A0010 | MIO test plan | MIO test plan through 2023 only; no MIO on 2024 onward (owner 2026-11-05). Forecasts: the tuned fixed_loc model, 5 seeds, hash-pinned, no one views them. Selection: nine formulations (w1_010_shock10_lp, w1_003_shock10_lp, w1_003_shock10_mad050_lp, nominal_lp as control, w1_010z_shock10_lp, w1_010_shock10_fine_lp, w1_010_component25_shock10_lp, w1_010_name25_shock10_lp, w1_010_shock05_book_lp) on four quarters: 2019Q4, 2020Q1, 2021Q1, 2021Q2. The owner reviews those results and picks the top 3. Those 3 then run on 2022 and 2023, 8 quarters, unopened until the picks are fixed. Total 60 separate accounts. Execution: each quarter-arm has its own registry; Mac and WSL workers solve single methods; trust the Mac's answer with no routine re-solve, WSL recalculates values and refuses invalid results, and one first-unit similarity check at 1% runs before scaling up; optimality is not independently verified. Mac workers may use a RAM disk and never its SSD. Results are development evidence: past results and the model selection overlap these years, and only 2024 onward is clean. Owner 2026-11-05: the Mac is certified by a short worker check on a 1-2 week packet with no exact match; a WSL rejection stops new Mac units as soon as it is logged and is never silent; while 2022-2023 stay unopened a rejection shows unit, check name and count only. The four formulations w1_010_shock10_lp, w1_003_shock10_lp, w1_003_shock10_mad050_lp and nominal_lp run first as plain WSL documents on the four selection quarters; machine does not matter for comparing results | /home/russell/data/index_options/full-span-rerun-20261105/PLAN.md;/home/russell/data/index_options/full-span-rerun-20261105/mio-review/formulation-review.md | Y |  | acquire | live cboe-chain/option_chain | judgemental | /home/russell/data/index_options/ob |
