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
| A0071 | acquire | validate | 2026-10-09T04:28:33+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | d350273a9820b6607b6081efdac784eb0defbc50af1c1471ce3178e9274ea783 | /home/russell/data/crypto_trading/ob | gating=pass suite=configs/suite-kalshi-crypto-candles.json |
| A0072 | acquire | backfill kalshi-crypto-candles-eth/candles | 2026-10-09T04:28:40+00:00 | --root /home/russell/data/crypto_trading/ob --source kalshi-crypto-candles-eth --stream candles --mode backfill | 902943ce13b830f07ba5671d454a24fb3f6cead4eb85a28098e114c38bcf7abc | /home/russell/data/crypto_trading/ob |  |
| A0073 | acquire | validate | 2026-10-09T04:28:40+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | b62110eeb25c2ee295a4d699b53912f3b59334ea18f2818ddcdce1b61db8b36a | /home/russell/data/crypto_trading/ob | gating=pass suite=configs/suite-kalshi-crypto-candles.json |
| A0074 | execute | crypto-features-15m | 2026-10-09T04:29:53+00:00 | crypto-features-15m | /home/russell/wt/crypto-completion-20261008/children/crypto_trading/pipeline_runs/crypto-features-15m-2026-10-09-d7e56be5 | /home/russell/wt/crypto-completion-20261008/children/crypto_trading/pipeline_runs/crypto-features-15m-2026-10-09-d7e56be5 | state=ran hash=d7e56be5 asof=2026-10-09 |
| A0075 | acquire | backfill features-15m/decision_features-crypto-features-15m-2026-10-09-d7e56be5 | 2026-10-09T04:30:04+00:00 | --root /home/russell/data/crypto_trading/ob --source features-15m --stream decision_features-crypto-features-15m-2026-10-09-d7e56be5 --mode backfill | 25122c7cf5b8b4e094acede66ddda06bfad15dcd9a76625d29b761110b77a5fc | /home/russell/data/crypto_trading/ob |  |
| A0076 | research | memo/2026-10-09-stage-a-refresh-and-15m-rerun | 2026-10-09T04:33:35+00:00 | Stage A refresh through 2026-10-08/09 and 15M rerun (items 1) | docs/memos/2026-10-09-stage-a-refresh-and-15m-rerun.md |  | development-only; held-out not enabled; new stream decision_features-crypto-features-15m-2026-10-09-d7e56be5 |
| A0077 | research | plan/2026-10-09-data-windows | 2026-10-09T16:40:29+00:00 | Owner-approved data windows W1-W5 | docs/plans/2026-10-09-data-windows.md |  | plan only; nothing run |
| A0078 | research | plan/2026-10-09-zoo-grid | 2026-10-09T17:09:37+00:00 | Owner-approved predictor zoo grid and target | docs/plans/2026-10-09-data-windows.md |  | plan only; nothing run |
| A0079 | acquire | register-source panel-minute | 2026-10-10T03:00:08+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | 15126db80b6543cb5d1193a00e604501349d4acad6346bc30180c6dbd2868c42 | /home/russell/data/crypto_trading/ob | connector=localblobs |
| A0080 | acquire | backfill panel-minute/files | 2026-10-10T03:00:10+00:00 | --root /home/russell/data/crypto_trading/ob --source panel-minute --stream files --mode backfill | 57fc9d448bbe2752bfc89314b13434725ed0e5865106731279e62228982fa459 | /home/russell/data/crypto_trading/ob |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0077 | Data windows W1-W5 | Lock predictor selection/HPO, check, retrain, MIO tuning and holdout windows before any modelling | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-data-windows | judgemental |  |
| A0078 | Predictor zoo grid | 5 models x 4 losses x pooled/unpooled on W1; 15-horizon vol-standardized return target | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-zoo-grid | judgemental |  |
