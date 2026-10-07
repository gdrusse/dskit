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
| A1075 | execute | perticker-zoo-417-smoke-8-search | 2026-10-05T12:20:23+00:00 | /home/russell/dskit/.venv/bin/python -m index_options.cdf_study pipeline_runs/perticker-zoo-417/smoke/w8.json --stage search --partition smoke | pipeline_runs/perticker-zoo-417/smoke |  | ADR-0236 amendment 3 smoke: per-ticker zoo, 42 tickers, one base candidate per kind, warm-up fold, real CLI stage search with DSKIT_GROUP_WORKERS=8; equivalence of 8 workers vs 1 and timing; exit 0 |
| A1076 | execute | perticker-zoo-417-smoke-1-search | 2026-10-05T12:24:07+00:00 | /home/russell/dskit/.venv/bin/python -m index_options.cdf_study pipeline_runs/perticker-zoo-417/smoke/w1.json --stage search --partition smoke | pipeline_runs/perticker-zoo-417/smoke |  | ADR-0236 amendment 3 smoke: per-ticker zoo, 42 tickers, one base candidate per kind, warm-up fold, real CLI stage search with DSKIT_GROUP_WORKERS=1; equivalence of 8 workers vs 1 and timing; exit 0 |
| A1077 | research | advanced-cdf-zoo/2026-10-06-capacity-and-architecture-plan | 2026-10-06T02:57:29+00:00 | Data-sized advanced CDF architectures for pooled and unpooled training | docs/research/advanced-cdf-zoo/2026-10-06-capacity-and-architecture-plan.md | docs/research/advanced-cdf-zoo/2026-10-06-capacity-and-architecture-plan.md |  |
| A1078 | research | advanced-cdf-zoo-design-review | 2026-10-06T03:00:24+00:00 | Phase 0 corrections and measured capacity/availability gates | docs/research/advanced-cdf-zoo/2026-10-06-capacity-and-architecture-plan.md | docs/research/advanced-cdf-zoo/2026-10-06-capacity-and-architecture-plan.md |  |
| A1079 | research | advanced-cdf-zoo-wrapper-preflight | 2026-10-06T03:32:47+00:00 | Owner-authorized fixed protocol; library adapter synthetic gates | docs/research/advanced-cdf-zoo/2026-10-06-capacity-and-architecture-plan.md |  | 13 families pass synthetic CPU/GPU training and roundtrip; Informer/TimesNet skip; 129 focused tests; implementation review pending |
| A1080 | research | advanced-cdf-zoo-skeptic-r1 | 2026-10-06T03:43:39+00:00 | Both frozen-candidate skeptic lenses; four Major corrections | docs/research/advanced-cdf-zoo/2026-10-06-capacity-and-architecture-plan.md |  | Persistence family corrected with RED/GREEN tests; BiTCN skipped; all distributions pinned; fresh reviews pending |
| A1081 | research | advanced-cdf-zoo-skeptic-r2 | 2026-10-06T03:56:45+00:00 | Full public-facade and dependency integration correction | docs/research/advanced-cdf-zoo/2026-10-06-capacity-and-architecture-plan.md |  | 164 focused pass;24pass2skip withoutNF; no added lint debt; new review candidate |
| A1082 | research | advanced-cdf identity admission correction | 2026-10-06T04:06:50+00:00 | review candidate32aee61d; R3 correctness findings | docs/research/advanced-cdf-zoo/2026-10-06-capacity-and-architecture-plan.md |  | 43focusedtests pass; real preholdout panel admission verified; no fitting; independent checkpoint and final reviews pending |
| A1083 | research | advanced-cdf convergence checkpoint | 2026-10-06T04:08:49+00:00 | integration R3 independent sibling inventory | docs/research/advanced-cdf-zoo/2026-10-06-capacity-and-architecture-plan.md |  | Predicate metadata admission closes before-filter null gap; 50focusedtests pass; final review pending |
| A1084 | execute | advanced-cdf reviewed resource pilots launched | 2026-10-06T04:15:37+00:00 | 29d3bec1; two clean R4 reviews; configs/advanced-cdf-zoo.json | pipeline_runs/advanced-cdf-zoo-20261006/results |  | one pooled CUDA plus one unpooled CPU pilot under11G total memory slice; source/config unchanged; completion unverified |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
