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
| A0021 | acquire | validate | 2026-10-08T02:16:04+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | 4466750e982c7fdefaf7abbec26b6ab74361fef8003eb0f7ccef04d9beb3ac93 | /home/russell/data/crypto_trading/ob | gating=warn suite=configs/suite-binance-files.json |
| A0022 | acquire | backfill binance-ethbvol/files | 2026-10-08T02:39:03+00:00 | --root /home/russell/data/crypto_trading/ob --source binance-ethbvol --stream files --mode backfill | ac755adb48955af43a06e98ee658dc9b5193049ad86926dffd5ff6808374e35f | /home/russell/data/crypto_trading/ob |  |
| A0023 | acquire | validate | 2026-10-08T02:39:03+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | be8fbeee5f10e16938ae7895df8758dd38b196ff60cca5e88fa21d2180174a6a | /home/russell/data/crypto_trading/ob | gating=warn suite=configs/suite-binance-files.json |
| A0024 | acquire | backfill kalshi-crypto-candles-btc/candles | 2026-10-08T03:19:52+00:00 | --root /home/russell/data/crypto_trading/ob --source kalshi-crypto-candles-btc --stream candles --mode backfill | b9e574d34c501bde2c8d1fc7e0616f73c72a0dadfa34355c6139bb4609ddd77b | /home/russell/data/crypto_trading/ob |  |
| A0025 | acquire | validate | 2026-10-08T03:19:53+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | f45202b40178b840b9e2e6b169e9ab7cd264ef9af2937498d2987e554c96739e | /home/russell/data/crypto_trading/ob | gating=pass suite=configs/suite-kalshi-crypto-candles.json |
| A0026 | acquire | backfill kalshi-crypto-candles-eth/candles | 2026-10-08T04:00:00+00:00 | --root /home/russell/data/crypto_trading/ob --source kalshi-crypto-candles-eth --stream candles --mode backfill | f6a98effe90426610296a08d4833cdcd61d2008fab371b7a8ebce5b40e978f77 | /home/russell/data/crypto_trading/ob |  |
| A0027 | acquire | validate | 2026-10-08T04:00:01+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | 8761bb82c84045e7b28e4f77a9a5a1081c5837361f1420b4c234433e641f9ceb | /home/russell/data/crypto_trading/ob | gating=pass suite=configs/suite-kalshi-crypto-candles.json |
| A0028 | execute | crypto-features-15m | 2026-10-08T23:26:58+00:00 | crypto-features-15m | /home/russell/dskit-crypto-killtest-20261007/children/crypto_trading/pipeline_runs/crypto-features-15m-2026-10-08-d2cf6aff | /home/russell/dskit-crypto-killtest-20261007/children/crypto_trading/pipeline_runs/crypto-features-15m-2026-10-08-d2cf6aff | state=ran hash=d2cf6aff asof=2026-10-08 |
| A0029 | acquire | register-source features-15m | 2026-10-08T23:27:05+00:00 | --root /home/russell/data/crypto_trading/ob --source  --stream  --mode | ebbf661f93541f752477bdd37ef5e0b53f89a107e9305115450914002d6a0460 | /home/russell/data/crypto_trading/ob | connector=localtables |
| A0030 | acquire | backfill features-15m/decision_features-crypto-features-15m-2026-10-08-d2cf6aff | 2026-10-08T23:27:08+00:00 | --root /home/russell/data/crypto_trading/ob --source features-15m --stream decision_features-crypto-features-15m-2026-10-08-d2cf6aff --mode backfill | a62d33d7c1e9502c65d196d631dbb9800a2cc7792825d353ae983e331a01a7e0 | /home/russell/data/crypto_trading/ob |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — |
