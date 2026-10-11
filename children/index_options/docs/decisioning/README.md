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
| A1120 | research | Audit PatchTST condor production realism before wrap | 2026-10-09T11:35:38+00:00 | Completed replay793013c0; frozen config; owner nodes; OCC and SEC mechanics | docs/memos/2026-10-09-robust-condor-replay.md production-realism section | /home/russell/data/index_options/production-development-audit-20261007/universe-rerun-20261008 | Read-only saved-outcome audit;24concurrent condors; all131expiry-price dates exact; material signal/fill and lifecycle differences; no newfit/replay/data acquisition; owner requested wrap |
| A1121 | research | distribution-modeling/2026-10-09-condor-selection-intuition | 2026-10-09T19:01:56+00:00 | Why the condor selector picks what it picks | docs/research/distribution-modeling/2026-10-09-condor-selection-intuition.md | docs/research/distribution-modeling/2026-10-09-condor-selection-intuition.md |  |
| A1122 | research | condor-selection-intuition-adr-renumber | 2026-10-09T20:19:55+00:00 | Why the condor selector picks what it picks | docs/research/distribution-modeling/2026-10-09-condor-selection-intuition.md | docs/research/distribution-modeling/2026-10-09-condor-selection-intuition.md |  |
| A1123 | research | distribution-modeling/2026-10-05-performance-pilots | 2026-10-05T16:29:23+00:00 | Pilot ideas to lift predictive-CDF skill over the empirical baseline | docs/research/distribution-modeling/2026-10-05-performance-pilots.md | docs/research/distribution-modeling/2026-10-05-performance-pilots.md |  |
| A1124 | research | distribution-modeling/2026-10-05-performance-pilots | 2026-10-05T16:42:47+00:00 | Pilot ideas to lift predictive-CDF skill over the empirical baseline (review corrections) | docs/research/distribution-modeling/2026-10-05-performance-pilots.md | docs/research/distribution-modeling/2026-10-05-performance-pilots.md |  |
| A1125 | research | distribution-modeling/2026-10-05-performance-pilots | 2026-10-05T16:55:35+00:00 | Pilot ideas to lift predictive-CDF skill (second review: window reading rule, training budget) | docs/research/distribution-modeling/2026-10-05-performance-pilots.md | docs/research/distribution-modeling/2026-10-05-performance-pilots.md |  |
| A1126 | research | distribution-modeling/2026-10-05-performance-pilots | 2026-10-05T17:05:55+00:00 | Pilot ideas to lift predictive-CDF skill (final review minors) | docs/research/distribution-modeling/2026-10-05-performance-pilots.md | docs/research/distribution-modeling/2026-10-05-performance-pilots.md |  |
| A1127 | research | strategy-alternatives/2026-10-11-patchtst-edge-and-harvest | 2026-10-11T01:04:29+00:00 | Where the PatchTST edge is and how to harvest it | docs/research/strategy-alternatives/2026-10-11-patchtst-edge-and-harvest.md | docs/research/strategy-alternatives/2026-10-11-patchtst-edge-and-harvest.md |  |
| A1128 | research | patchtst-edge-and-harvest-horizon-and-recalibration | 2026-10-11T01:05:09+00:00 | Where the PatchTST edge is and how to harvest it (ETF horizon and recalibration evidence folded in) | docs/research/strategy-alternatives/2026-10-11-patchtst-edge-and-harvest.md | docs/research/strategy-alternatives/2026-10-11-patchtst-edge-and-harvest.md |  |
| A1129 | execute | Build the forecast-dispersion IC test (T2) on the v2 replay inputs | 2026-10-11T02:35:00+00:00 | ADR-0259 (PROPOSED); research A1127/A1128; condor-replay-inputs-20261009-v5 (forecasts, chain, bars) | configs/run-forecast-dispersion-ic.json (hash eb5c9b3c); dskit CrossSectionIC, CurveMoments, horizon-pairs symbol_field, TradeBarFeatures underlying_close |  | Validated and planned (15 nodes, no warnings); not run (data on the WSL host). Pass per the note: relative IC > 0 with t >= 2.5; controls forecast_alone and implied_alone on the same rows. A spot disagreement refuses by design. |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
