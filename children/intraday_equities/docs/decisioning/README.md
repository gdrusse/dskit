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

**Path to Production** is owner-only:
`python -m dskit.journal promote <ID> --criteria empirical|judgemental|n/a`.
Hooks never write it. Pytest does not record. A child without
`journal.json` refuses acquire / run / live.

## Actions

| ID | Category | Step | Execution Date | Relevant Inputs | Relevant Outputs | Database Location | Notes |
|---|---|---|---|---|---|---|---|
| A0001 | acquire | alpaca SIP backfill | 2026-08-31T00:00:00+00:00 | configs/source-alpaca-backfill.json | ob/observations/alpaca-sip | ob | retrospective; artifacts may be incomplete |
| A0002 | acquire | schwab live source | 2026-08-31T00:00:00+00:00 | configs/source-schwab-live.json | ob/observations/schwab | ob | retrospective; artifacts may be incomplete |
| A0003 | execute | hl-scan | 2026-08-31T00:00:00+00:00 | configs/run-hl-scan.json | docs/decisioning/logs/hl-scan.out | docs/decisioning/logs/ | retrospective; artifacts may be incomplete |
| A0004 | execute | framework HPO | 2026-09-01T00:00:00+00:00 | configs/run-framework.json | docs/decisioning/logs/framework.out | docs/decisioning/logs/ | retrospective; artifacts may be incomplete |
| A0005 | execute | horizon models | 2026-08-31T00:00:00+00:00 | configs/run-horizon-models.json | docs/decisioning/decision-horizon-models.md | docs/decisioning/ | retrospective; artifacts may be incomplete; TFT / T bakeoff still open |

## Path to Production

| ID | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|
| — | — | — | — | — |

## Evidence

Rationale files (not generated):

- [decision-framework-hpo.md](decision-framework-hpo.md)
- [decision-hl-scan.md](decision-hl-scan.md)
- [decision-horizon-criteria.md](decision-horizon-criteria.md)
- [decision-horizon-models.md](decision-horizon-models.md)
- [framework.md](framework.md)
