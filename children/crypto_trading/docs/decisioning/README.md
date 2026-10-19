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
| A0373 | acquire | register-source retained-history-binance-ethusdt-1m-20261019 | 2026-10-19T14:27:10+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | 895b6fc58b531bddd7eead722e10010738dc4a64b2cba6a20db0e5345dbf5b8c | /home/russell/data/crypto_trading/ob | connector=localblobs |
| A0374 | acquire | backfill retained-history-binance-ethusdt-1m-20261019/files | 2026-10-19T14:27:20+00:00 | --root /home/russell/data/crypto_trading/ob --source retained-history-binance-ethusdt-1m-20261019 --stream files --mode backfill | 61b0071d3d0c314635a9432d61a95a10c3cb453e0049cc513efbc821205324b4 | /home/russell/data/crypto_trading/ob |  |
| A0375 | acquire | register-source retained-history-binance-ethbvol-20261019 | 2026-10-19T14:27:23+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | fd8de5e6ec32687dfe76f6f1e20a1326af1d3b8c82e0aee174b073d386ff4605 | /home/russell/data/crypto_trading/ob | connector=localblobs |
| A0376 | acquire | backfill retained-history-binance-ethbvol-20261019/files | 2026-10-19T14:27:38+00:00 | --root /home/russell/data/crypto_trading/ob --source retained-history-binance-ethbvol-20261019 --stream files --mode backfill | c657f5d574fde7005360e99139753a2db7f1b0c3f05f6a4643aa51c683ed8705 | /home/russell/data/crypto_trading/ob |  |
| A0377 | execute | crypto-retained-panel | 2026-10-19T14:28:21+00:00 | crypto-retained-panel | /home/russell/data/crypto_trading/research/retained-history-20261019/preparation-runs/crypto-retained-panel-2026-10-19-9d72ab4a | /home/russell/data/crypto_trading/research/retained-history-20261019/preparation-runs/crypto-retained-panel-2026-10-19-9d72ab4a | state=ran hash=9d72ab4a asof=2026-10-19 |
| A0378 | acquire | register-source retained-history-minute-panel-20261019 | 2026-10-19T14:28:23+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | 34ff1bb5943b413eeca0a3520029df1ff3a5515926ec00583c9ac01341686e63 | /home/russell/data/crypto_trading/ob | connector=localblobs |
| A0379 | acquire | backfill retained-history-minute-panel-20261019/files | 2026-10-19T14:28:27+00:00 | --root /home/russell/data/crypto_trading/ob --source retained-history-minute-panel-20261019 --stream files --mode backfill | ba0b3024d159afdbf5a6692b88fe26878cad0e7816bcdced7a951dca8534c2c0 | /home/russell/data/crypto_trading/ob |  |
| A0380 | execute | crypto-retained-oct08-12-features | 2026-10-19T14:28:31+00:00 | crypto-retained-oct08-12-features | /home/russell/data/crypto_trading/research/retained-history-20261019/preparation-runs/crypto-retained-oct08-12-features-2026-10-19-a0d935bd | /home/russell/data/crypto_trading/research/retained-history-20261019/preparation-runs/crypto-retained-oct08-12-features-2026-10-19-a0d935bd | state=ran hash=a0d935bd asof=2026-10-19 |
| A0381 | acquire | register-source retained-history-oct08-12-raw-20261019 | 2026-10-19T14:28:32+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | ee084c61325911da68d14eea65b0d6a5cb6f70f19fd41855007c806dd3a3955d | /home/russell/data/crypto_trading/ob | connector=localblobs |
| A0382 | acquire | backfill retained-history-oct08-12-raw-20261019/files | 2026-10-19T14:28:32+00:00 | --root /home/russell/data/crypto_trading/ob --source retained-history-oct08-12-raw-20261019 --stream files --mode backfill | 016054a0b71dfdf79afec74f7f711aef3dd6cfc11404b28c75a5319f1854024a | /home/russell/data/crypto_trading/ob |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0077 | Data windows W1-W5 | Lock predictor selection/HPO, check, retrain, MIO tuning and holdout windows before any modelling | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-data-windows | judgemental |  |
| A0078 | Predictor zoo grid | 5 models x 4 losses x pooled/unpooled on W1; 15-horizon vol-standardized return target | children/crypto_trading/docs/plans/2026-10-09-data-windows.md | Y |  | research | plan/2026-10-09-zoo-grid | judgemental |  |
