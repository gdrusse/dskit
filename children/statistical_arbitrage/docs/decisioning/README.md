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
| A0001 | research | Lock phase-one universe and approved child layout | 2026-10-20T02:45:04+00:00 | owner decisions; parent ADR-0270; DSKit inventory | docs/architecture/decision-log.md#adr-0270; children/statistical_arbitrage | local design worktree | AAPL/AMZN/GOOGL option nodes; QQQ factor/benchmark; later MSFT/NVDA and energy; no fit or backtest |
| A0002 | execute | Independent audit of accepted v6 evidence (two read-only lenses) | 2026-10-20T22:53:37+00:00 | ADR-0270; ADR-0271; code 76b4179e; frozen v6 run; memo 2026-10-20-tech-phase-one-evaluation | docs/memos/2026-10-20-v6-independent-audit.md | /home/russell/data/statistical_arbitrage/replay-20261020 (unchanged) | No arithmetic or lookahead error; two Major interpretation findings: skew books trade ATM straddles; no trade possible before 2022-06-27. No-go stands. |
| A0003 | execute | ADR-0272 prediction-only study: development, validation, offset-1 runs | 2026-10-20T22:53:43+00:00 | pinned surface_rows/graph_edges/corr_graph_edges; configs/run-tech-prediction-*.json | docs/memos/2026-10-20-tech-prediction-only-study.md | /home/russell/data/statistical_arbitrage/prediction-20261020 | Lagged-spread Z predicts ATM pair-spread change (executable clock R2oos +0.20 at 20 sessions, +0.08 at 5); skew predictability is one-day measurement reversal; network edges add nothing. No trade rule or P&L. |
| A0004 | execute | ADR-0273 ten-method study with Theta Greeks/OI: pass, dev, two validations | 2026-10-21T00:20:58+00:00 | pinned Theta members; configs/run-tech-structural-greeks.json; run-tech-prediction-methods-*.json | docs/memos/2026-10-20-tech-prediction-ten-methods.md | /home/russell/data/statistical_arbitrage/{structural-greeks-20261020,prediction-methods-20261020} | Primary next-session label: no method meets the pre-registered rule; term-structure slope is the only candidate (ATM 20d +0.069, 4/5 folds, p 0.035 not BH); vendor-Greek positioning, realized vol, Lasso, boosting worse. No trade rule or P&L. |
| A0005 | research | prediction-methods/2026-10-21-better-models | 2026-10-21T00:59:33+00:00 | Better models for relative IV prediction: pooling, state space, scoring | docs/research/prediction-methods/2026-10-21-better-models.md | docs/research/prediction-methods/2026-10-21-better-models.md |  |
| A0006 | execute | ADR-0274: scaled label, Kalman-OU state, cohort pooling (pass, dev, validation) | 2026-10-21T01:57:32+00:00 | pinned 3-name surface; configs/run-tech-structural-universe.json; run-tech-prediction-adr0274-*.json | docs/memos/2026-10-21-tech-prediction-adr0274.md | /home/russell/data/statistical_arbitrage/{structural-universe-20261021,prediction-adr0274-20261021} | Vol-scaled label meets the pre-registered rule on ATM 20d (R2oos 0.199 -> 0.242, BH, 4/5 folds); Kalman-OU adds nothing; cohort pooling marginal at 5d, collapses at 20d (scale mixing). No trade rule or P&L. |
| A0007 | research | data-inventory/2026-10-21-theta-and-sources | 2026-10-21T02:27:49+00:00 | Data inventory for the relative-IV studies: Theta, stock bars, rates, gaps | docs/research/data-inventory/2026-10-21-theta-and-sources.md | docs/research/data-inventory/2026-10-21-theta-and-sources.md |  |
| A0008 | execute | ADR-0274 round 2: Kalman forecast aligned to the next-session label, rerun | 2026-10-21T02:36:47+00:00 | review finding F1 on 099ec0af; configs/run-tech-prediction-adr0274-*.json (round 2) | docs/memos/2026-10-21-tech-prediction-adr0274.md | /home/russell/data/statistical_arbitrage/prediction-adr0274-20261021/{development-r2,validation-r2} | Only the (B) rows change: kalman_direct ATM +0.058/+0.145, skew +0.011/+0.053 (not significant); (A) and (C) identical; selection unchanged. |
| A0009 | execute | ADR-0275 fifteen methods: vendor pass, dev and validation (two rounds) | 2026-10-21T18:03:49+00:00 | pinned Theta members; configs/run-tech-structural-vendor.json; run-tech-prediction-fifteen-*.json | docs/memos/2026-10-21-tech-prediction-fifteen-methods.md | /home/russell/data/statistical_arbitrage/{structural-vendor-20261021,prediction-fifteen-20261021} | Rule met on ATM only: two-stage residual (5d, 20d), term curve (5d), pooled-scaled blend (20d). Vendor Greeks/smile/flow/carry/Cboe/market features lose. Skew none. No trade rule or P&L. |
| A0010 | research | Owner ratified ADR-0272 to ADR-0275 | 2026-10-21T18:42:15+00:00 | owner message 2026-10-21 | docs/architecture/decision-log.md | repository | Status changed from proposed to approved; no result changed. |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Phase-one tech scope | Keep universe, source clock, reuse boundary and validation intent fixed while implementation contracts are reviewed | ../../docs/architecture/decision-log.md#ADR-0270; README.md; configs/asset-model.json; configs/validation-calendar.json | Y |  | research | Lock phase-one universe and approved child layout | judgemental | local design worktree |
