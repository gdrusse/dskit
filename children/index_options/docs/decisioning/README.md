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
| A1086 | execute | advanced-cdf-delivery-review-closed | 2026-10-07T18:55:01+00:00 | Immutable candidate5b150ca578b7eada44ff05aa5cbba03d138e18a7; original frozen run29d3bec1; final artifact and browser audits. | Two fresh independent final review outputs embedded in final-verification.json; final memo closure. |  | Correctness331 focused tests, zeroCritical/Major/Minor. Integration1288 focused tests, zeroCritical/Major; documentary stylesheet dependency Minor resolved with path/hash/retention record accepted by reviewer. No behavioral changes after review. Owner-authorized normal main publication and contained-branch cleanup next; no datasets or weights published. |
| A1087 | execute | advanced-cdf-verified-delivery | 2026-10-07T18:56:51+00:00 | Reviewed release d4d00d395a6fe44f35139ebb5f2180178054afb0 on GitHub main; two final zeroCritical/Major reviews. | Four report blobs verified identical on remote main, local repository and visualization copies; final memo and reproducibility receipt; task remote branch deletion verified. |  | Atomic fast-forward publication to existing origin succeeded. Exact remote branch head containment checked; expected-head guarded deletion succeeded. Three earlier already-merged remote branches were also removed; all other active/unreviewed work preserved. No source datasets or fitted weights uploaded. Evidence-only final closeout; code/config/report bytes unchanged. |
| A1088 | research | advanced-cdf-zoo/2026-10-07-production-proposal | 2026-10-07T19:14:41+00:00 | Propose pooled CDF production candidate and validation schedule | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md |  |
| A1089 | research | cdf-production-proposal-review-closed | 2026-10-07T19:22:04+00:00 | Candidate ca9b2102; two independent design lenses; final audited reports. | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | Both lenses zero Critical/Major; one shared TiDE rounding Minor corrected editorially. Full review outputs retained. Proposal publication only; no HPO/training/holdout/deployment authorization. |
| A1090 | execute | cdf-production-proposal-verified-wrap | 2026-10-07T19:23:09+00:00 | Reviewed proposal ca9b2102; evidence-only closure 77ca80ac verified on existing GitHub main. | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md; docs/RE-ENTRY.md |  | User requested push and wrap. Remote proposal bytes/hash verified; all four report blobs unchanged. Both reviews zero Critical/Major and shared rounding Minor resolved. No new model/config/training/holdout activity. Task remote branch absent; preserve historical artifacts and other checkouts. |
| A1091 | research | cdf-history-covid-split-amendment | 2026-10-07T19:45:51+00:00 | Owner requested all usable owned historical data, explicit COVID validation, and stock-split safeguards before next end-to-end pre-holdout run. | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | H1 coverage reconciliation, C1 February-June 2020 replacing H2, S1 corporate-action and consistent-vintage checks. Budget216 unchanged. Next-session implementation/execution conditional on fresh gates; no training/holdout/provider access performed. Independent amendment review pending. |
| A1092 | research | cdf-history-covid-split-review-closed | 2026-10-07T19:53:10+00:00 | Immutable amendment e78ec1be; two fresh independent GPT-6 design lenses. | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | Both lenses zero Critical/Major/Minor/Nit; actual reports retained. One focused manifest test passed. Evidence-only closure; no executable/config/report changes and no training. Parent repeated final status/diff/hash check successfully after reviewer tool failure. Next-session Stage0 implementation and bounded Stages1-4 authorized conditional on gates; protected holdout remains excluded. |
| A1093 | research | production-retention-uncertainty-amendment | 2026-10-07T19:59:25+00:00 | Owner artifact retention and uncertainty-set request; checkpoint and robustification source inventory; primary research | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | R1/U1 design and handoff; no implementation or training; independent review pending. |
| A1094 | research | production-retention-uncertainty-review-closure | 2026-10-07T20:04:04+00:00 | Candidate 44b06166; two independent sequential R1/U1 design reviews | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | Both lenses zero Critical/Major/Minor/Nit; actual outputs retained; focused test passed; evidence-only append; implementation/calibration/restore remain future gates. |
| A1095 | research | mio-handoff-and-stage0-source-census | 2026-10-07T20:11:08+00:00 | Owner MIO clarification and continuation; configured source metadata and pre2026 date-only census | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md | Editorial MIO restatement plus first-pass source evidence; reviewed U1 unchanged; no fits or production claim; backup preference and Stage0 gates remain open. |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
