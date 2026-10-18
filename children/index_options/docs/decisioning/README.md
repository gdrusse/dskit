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
| A1148 | acquire | register-source bars-split | 2026-10-12T04:24:43+00:00 | --root /home/russell/data/stock_options/option-universe-300b-underlying --source  --stream  --mode | 3dbf346477d82c046b435baf4acd4e8c0eb501cce96103235786b3f4b59227fd | /home/russell/data/stock_options/option-universe-300b-underlying | connector=dskit.onboarding.libs.alpaca:AlpacaBarsConnector |
| A1149 | acquire | backfill bars-raw/bars | 2026-10-12T04:24:57+00:00 | --root /home/russell/data/stock_options/option-universe-300b-underlying --source bars-raw --stream bars --mode backfill | 506f8c239b7e574ad714ce2a209236990d40cef3cdf32f45a19b95250205454e | /home/russell/data/stock_options/option-universe-300b-underlying |  |
| A1150 | acquire | backfill bars-split/bars | 2026-10-12T04:25:15+00:00 | --root /home/russell/data/stock_options/option-universe-300b-underlying --source bars-split --stream bars --mode backfill | 58b3c0e3c3f75ad7d13f4583c00b6fcb07a95740ddf355687a2d90707998dd77 | /home/russell/data/stock_options/option-universe-300b-underlying |  |
| A1151 | execute | Second-300 option pull (ADR-0235 amd): 285 names, thin chains | 2026-10-12T04:28:40+00:00 | configs/source-option-universe-300b.json; option_universe_300b.json (ranks 301-600, 20-session volume) | /home/russell/data/stock_options/option-universe-300b (+ -underlying raw/split bars) |  | contracts 1,013,002; option bars 972,202; raw and split SIP daily bars 166,003 each, window 2024-02-01..2025-12-31 (no 2026). 11,056 exact-DTE31 symbol-dates over 285 names; market curve eligible only 18.4% (vs 49% first set); straddle ratio available 54%. Pull ~1h (03:21-04:22Z). |
| A1152 | execute | Second-300 tests (no model): HAR-vs-implied fails; 75-90 shoulder not replicated | 2026-10-12T04:28:40+00:00 | scratch agent_300b/{build300b,tests300b}.py (rules in docstring); t300b.pkl | none (scratch analysis) |  | B HAR-vs-implied straddle: ALL IC +0.029 t 1.43 (need 2.5), 2024 +0.052 t 2.02, 2025 +0.005 t 0.19; quintile spread gross +2.4% net of 8% -5.7%. C region vs market CDF (2037 rows, 180 names): HAR 75-90% +0.66 [-1.87,+3.37] (first set +4.0 [1.25,7.16]) NOT replicated; centre market better -6.6 (CI incl 0); whole CRPS market-HAR -0.44x1e3 [-1.13,+0.24] tie; realized freq .107/.139/.485/.137/.133. Same 2024-25 dates so market factor shared; only names differ. Supersedes the shoulder as a candidate. |
| A1153 | research | distribution-modeling/2026-10-18-theta-chain-feature-engineering | 2026-10-18T18:15:53+00:00 | Theta chain feature engineering for the 31-day strike-region CDF | docs/research/distribution-modeling/2026-10-18-theta-chain-feature-engineering.md | docs/research/distribution-modeling/2026-10-18-theta-chain-feature-engineering.md |  |
| A1154 | research | distribution-modeling/2026-10-18-market-implied-reference-and-baselines | 2026-10-18T18:20:21+00:00 | Market-implied z reference recipe and non-neural baseline ladder | docs/research/distribution-modeling/2026-10-18-market-implied-reference-and-baselines.md | docs/research/distribution-modeling/2026-10-18-market-implied-reference-and-baselines.md |  |
| A1155 | execute | theta-strike-screen-features-census | 2026-10-18T18:22:18+00:00 | theta-strike-screen-features-census | /home/russell/data/index_options/theta-strike-screen-20261018/features/runs/theta-strike-screen-features-census-2025-12-31-1b319305 | /home/russell/data/index_options/theta-strike-screen-20261018/features/runs/theta-strike-screen-features-census-2025-12-31-1b319305 | state=error hash=1b319305 asof=2025-12-31 |
| A1156 | research | distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells | 2026-10-18T18:33:08+00:00 | Strike-screen windows, lead-time rule and mispricing cells | docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md | docs/research/distribution-modeling/2026-10-18-strike-screen-windows-lead-time-cells.md |  |
| A1157 | execute | theta-strike-screen-features-census | 2026-10-18T18:34:42+00:00 | theta-strike-screen-features-census | /home/russell/data/index_options/theta-strike-screen-20261018/features/runs/theta-strike-screen-features-census-2025-12-31-1b319305 | /home/russell/data/index_options/theta-strike-screen-20261018/features/runs/theta-strike-screen-features-census-2025-12-31-1b319305 | state=ran hash=1b319305 asof=2025-12-31 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
