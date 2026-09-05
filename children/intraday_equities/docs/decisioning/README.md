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
   `python -m dskit.journal research "TITLE" --body-file <draft>`.
   Writes `docs/research/<slug>.md` and the row together. Never write
   that folder by hand. Skills: `record-research` (Cursor + Claude;
   Claude `/research`).
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
| A2848 | execute | p11-25-asset-modelability-gate2-iwm-h05 bounded walk-forward | 2026-09-04T15:38:58+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p11-modelability.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p11-25-asset-modelability-gate2-iwm-h05-walkforward-2026-02-28-ffa95473 |  | state=ran folds=1 hash=ffa95473 asof=2026-02-28; fold_processes=isolated; memory_limit_bytes=18253611008 |
| A2849 | execute | staged gate2 | 2026-09-04T15:38:58+00:00 | /home/russell/dskit/children/intraday_equities/configs/run-p11-modelability.json | /home/russell/dskit/children/intraday_equities/pipeline_runs/p11-25-asset-modelability-staged-2026-02-28-355b6198/stages/gate2.json |  | stage_token=355b6198d9d4758f924af390ca28407267598772119e50182404a55df8cb416f:gate2; state=ran; sha256=547798620fa396acfd4b0602ab2b7c07b5666d0eb562d520d510cb2401aa0990; reason= |
| A2850 | research | HFDR constrained in MIO | 2026-09-04T19:40:43+00:00 | docs/architecture/decision-log.md ADR-0088 | docs/architecture/decision-log.md | docs/architecture/decision-log.md | Owner-locked: Gate 1 is the stock-modelability gate; Gate 2 is no longer used for stock selection; MIO must constrain expected false-signal gross capital. |
| A2851 | research | gate3-lower-compute-null-design | 2026-09-04T23:33:28+00:00 | Gate 3: fail-fast scramble refit on cached features | docs/research/gate3-lower-compute-null-design.md | docs/research/gate3-lower-compute-null-design.md | Revised so Gate 3 stays the session-scramble refit; fail-fast at first beating null; drop score-bootstrap substitute and 100x claim. |
| A2852 | research | cohort-d-twenty-five-breadth-candidates | 2026-09-05T02:39:10+00:00 | Cohort D: twenty-five breadth candidates | docs/research/cohort-d-twenty-five-breadth-candidates.md | docs/research/cohort-d-twenty-five-breadth-candidates.md |  |
| A2853 | acquire | register-source alpaca-sip-split-d | 2026-09-05T02:40:17+00:00 | --root ./ob --source  --stream  --mode | 1c5c9d9b22dabac130288f78ce0a02e6a5754eb3d8efdabef46c3280a54f5f36 | ./ob | connector=intraday_equities.connectors:AlpacaBars |
| A2854 | acquire | backfill alpaca-sip-split-d/bars | 2026-09-05T03:05:53+00:00 | --root ./ob --source alpaca-sip-split-d --stream bars --mode backfill | d1e5c3b10b0144ad83fcebd79db18d38d62d8928af249265779bbad03433c4fd | ./ob |  |
| A2855 | research | cohort-e-twenty-candidates-selected-for-modelability | 2026-09-05T04:31:52+00:00 | Cohort E: twenty candidates selected for modelability | docs/research/cohort-e-twenty-candidates-selected-for-modelability.md | docs/research/cohort-e-twenty-candidates-selected-for-modelability.md |  |
| A2856 | acquire | register-source alpaca-sip-split-e | 2026-09-05T04:31:57+00:00 | --root ./ob --source  --stream  --mode | 85bda25ce4e0239b7be53c8b8450860a397e9861b2151dd6a1616193699e136f | ./ob | connector=intraday_equities.connectors:AlpacaBars |
| A2857 | acquire | backfill alpaca-sip-split-e/bars | 2026-09-05T04:57:21+00:00 | --root ./ob --source alpaca-sip-split-e --stream bars --mode backfill | 088bb14e03f122be972b2230112f52d24c892f67f0dd0827a5f6fc94d4ceaa64 | ./ob |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A2822 | Gate 1: stock modelability | Lock the stock-modelability selection gate | children/intraday_equities/pipeline_runs/p11-25-asset-modelability-staged-2026-02-28-355b6198/stages/gate1.json; docs/decisioning/framework.md | Y |  | execute | staged gate1 | empirical |  |
| A2850 | Gate 2: HFDR in MIO | Replace the retired Bonferroni screen with an MIO constraint on false-signal gross capital | docs/architecture/decision-log.md#ADR-0088; docs/decisioning/framework.md | Y |  | research | HFDR constrained in MIO | judgemental | docs/architecture/decision-log.md |
| A2851 | Gate 3: shuffle refit | Audit Gate-1 selections against a session-scramble refit null | docs/research/gate3-lower-compute-null-design.md; docs/architecture/decision-log.md#ADR-0089 | N | Investigating a faster valid shuffle-training solution | research | gate3-lower-compute-null-design | empirical | docs/research/gate3-lower-compute-null-design.md |

## Evidence

Rationale files (not generated):

- [decision-framework-hpo.md](decision-framework-hpo.md)
- [decision-hl-scan.md](decision-hl-scan.md)
- [decision-horizon-criteria.md](decision-horizon-criteria.md)
- [decision-horizon-models.md](decision-horizon-models.md)
- [framework.md](framework.md)
- [hstar-go.md](hstar-go.md)
