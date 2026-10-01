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
| A0631 | acquire | backfill cdf-horizon-panel/input_panel | 2026-10-01T17:05:20+00:00 | --root ./pipeline_runs/cdf-horizon-source --source cdf-horizon-panel --stream input_panel --mode backfill | 8815723bdfc9a93e925f82bcb7f027307888ace6b9770d37878c9f17cb4b82ec | ./pipeline_runs/cdf-horizon-source |  |
| A0632 | execute | qqq-cdf-horizon-coverage | 2026-10-01T17:05:52+00:00 | qqq-cdf-horizon-coverage | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/qqq-cdf-horizon-coverage-2026-10-01-21153f83 | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/qqq-cdf-horizon-coverage-2026-10-01-21153f83 | state=ran hash=21153f83 asof=2026-10-01 |
| A0633 | execute | cdf-horizon-coverage | 2026-10-01T17:20:54+00:00 | cdf-horizon-coverage | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/cdf-horizon-coverage-2026-10-01-b6357960 | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/cdf-horizon-coverage-2026-10-01-b6357960 | state=ran hash=b6357960 asof=2026-10-01 |
| A0634 | execute | cdf-horizon-coverage | 2026-10-01T17:22:11+00:00 | cdf-horizon-coverage | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/cdf-horizon-coverage-2026-10-01-6ae71aa4 | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/cdf-horizon-coverage-2026-10-01-6ae71aa4 | state=ran hash=6ae71aa4 asof=2026-10-01 |
| A0635 | execute | cdf-horizon-coverage | 2026-10-01T17:41:50+00:00 | cdf-horizon-coverage | /tmp/cdf-runbook-independent-ay3zrjn6/runs/cdf-horizon-coverage-2026-10-01-6ae71aa4 | /tmp/cdf-runbook-independent-ay3zrjn6/runs/cdf-horizon-coverage-2026-10-01-6ae71aa4 | state=ran hash=6ae71aa4 asof=2026-10-01 |
| A0636 | execute | cdf-horizon-coverage | 2026-10-01T17:44:05+00:00 | cdf-horizon-coverage | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/runbook-reproduction-20261001/runs/cdf-horizon-coverage-2026-10-01-6ae71aa4 | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/runbook-reproduction-20261001/runs/cdf-horizon-coverage-2026-10-01-6ae71aa4 | state=ran hash=6ae71aa4 asof=2026-10-01 |
| A0637 | execute | qqq-feature-availability | 2026-10-01T18:29:05+00:00 | qqq-feature-availability | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/qqq-feature-availability/runs/qqq-feature-availability-2026-10-01-c04f9fa5 | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/qqq-feature-availability/runs/qqq-feature-availability-2026-10-01-c04f9fa5 | state=error hash=c04f9fa5 asof=2026-10-01 |
| A0638 | execute | qqq-feature-availability | 2026-10-01T18:30:28+00:00 | qqq-feature-availability | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/qqq-feature-availability/runs/qqq-feature-availability-2026-10-01-d16c7295 | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/qqq-feature-availability/runs/qqq-feature-availability-2026-10-01-d16c7295 | state=ran hash=d16c7295 asof=2026-10-01 |
| A0639 | execute | qqq-feature-availability | 2026-10-01T18:33:37+00:00 | qqq-feature-availability | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/qqq-feature-availability/runs/qqq-feature-availability-2026-10-01-c88c0f43 | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/qqq-feature-availability/runs/qqq-feature-availability-2026-10-01-c88c0f43 | state=ran hash=c88c0f43 asof=2026-10-01 |
| A0640 | execute | qqq-feature-availability | 2026-10-01T18:36:39+00:00 | qqq-feature-availability | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/qqq-feature-availability/runs/qqq-feature-availability-2026-10-01-44bbebfb | /home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/qqq-feature-availability/runs/qqq-feature-availability-2026-10-01-44bbebfb | state=ran hash=44bbebfb asof=2026-10-01 |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
