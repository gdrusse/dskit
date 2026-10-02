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
| A0031 | execute | amzn-feature-availability | 2026-10-01T19:01:39+00:00 | amzn-feature-availability | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-feature-availability/runs/amzn-feature-availability-2026-10-01-cfe3c76c | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-feature-availability/runs/amzn-feature-availability-2026-10-01-cfe3c76c | state=ran hash=cfe3c76c asof=2026-10-01 |
| A0032 | execute | amzn-feature-availability | 2026-10-01T19:05:18+00:00 | amzn-feature-availability | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-gap-independent-20261001/runs/amzn-feature-availability-2026-10-01-bbcfc0c2 | /home/russell/dskit-torch-decision-cdf/children/stock_options/pipeline_runs/amzn-gap-independent-20261001/runs/amzn-feature-availability-2026-10-01-bbcfc0c2 | state=ran hash=bbcfc0c2 asof=2026-10-01 |
| A0033 | acquire | register-source stock-amzn-alpaca | 2026-10-02T16:01:34+00:00 | --root /home/russell/data/stock_options/ob --source  --stream  --mode | d9ff4c558c07fc4fd02d0d82a2261cc5971d828b4474b68568a1ac5fbd8b6504 | /home/russell/data/stock_options/ob | connector=localblobs |
| A0034 | acquire | register-source stock-msft-alpaca | 2026-10-02T16:01:34+00:00 | --root /home/russell/data/stock_options/ob --source  --stream  --mode | c726408180f4edef9efc01255d31b8b791f32a337f1fe660889fca6a41f78f6b | /home/russell/data/stock_options/ob | connector=localblobs |
| A0035 | acquire | register-source stock-long-history-audit | 2026-10-02T16:01:34+00:00 | --root /home/russell/data/stock_options/ob --source  --stream  --mode | a42542d963b45fe8325298cbc48cddbeafd174ed749b4584707e481f8311e9e7 | /home/russell/data/stock_options/ob | connector=localblobs |
| A0036 | acquire | register-source stock-orats-smv-sample | 2026-10-02T16:01:34+00:00 | --root /home/russell/data/stock_options/ob --source  --stream  --mode | 01337ad4330e6289340d3a9c09e35aeb687d2c9123bd04ffc2054b45dae8cd0a | /home/russell/data/stock_options/ob | connector=localblobs |
| A0037 | acquire | backfill stock-amzn-alpaca/files | 2026-10-02T16:01:35+00:00 | --root /home/russell/data/stock_options/ob --source stock-amzn-alpaca --stream files --mode backfill | b31bcc9e078c3570114296e49445936c3d236e1f0bf01a4d05dac74f2347688c | /home/russell/data/stock_options/ob |  |
| A0038 | acquire | backfill stock-msft-alpaca/files | 2026-10-02T16:01:36+00:00 | --root /home/russell/data/stock_options/ob --source stock-msft-alpaca --stream files --mode backfill | b0640bfb3fa3990a47eabbb376e1cdfee8c1c7ba89c9b9f2eb5b5552af23ce59 | /home/russell/data/stock_options/ob |  |
| A0039 | acquire | backfill stock-long-history-audit/files | 2026-10-02T16:01:36+00:00 | --root /home/russell/data/stock_options/ob --source stock-long-history-audit --stream files --mode backfill | 8e723b2103c5ca3ea8b12a44a5ea518081ee8ed971ee8932666f6edbaf046741 | /home/russell/data/stock_options/ob |  |
| A0040 | acquire | backfill stock-orats-smv-sample/files | 2026-10-02T16:01:36+00:00 | --root /home/russell/data/stock_options/ob --source stock-orats-smv-sample --stream files --mode backfill | 3867cac3c3fc84f3d981e78fdaa880320ace44576fa4db4a627432b5bebdd1e4 | /home/russell/data/stock_options/ob |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — |
