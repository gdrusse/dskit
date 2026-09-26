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
| A0126 | acquire | backfill optionshist-chain/option_chain | 2026-09-26T01:03:52+00:00 | --root /home/russell/data/index_options/ob --source optionshist-chain --stream option_chain --mode backfill | f16680d27c0f35cd29647fa2c549d2a41f83e29e2a71ba432804e57db0fe7f06 | /home/russell/data/index_options/ob |  |
| A0127 | acquire | backfill optionshist-chain/option_chain | 2026-09-26T01:05:36+00:00 | --root /home/russell/data/index_options/ob --source optionshist-chain --stream option_chain --mode backfill | 362deb92e4c2a64f43fdcc9c22e5b78470a997708cd6c120ba97f8d3e0ff0841 | /home/russell/data/index_options/ob |  |
| A0128 | acquire | backfill optionshist-chain/option_chain | 2026-09-26T01:07:12+00:00 | --root /home/russell/data/index_options/ob --source optionshist-chain --stream option_chain --mode backfill | 5fc892d12c670905312c6b75eff0c805b59329bd7ad0a4d955f470824e53d7da | /home/russell/data/index_options/ob |  |
| A0129 | acquire | backfill optionshist-chain/option_chain | 2026-09-26T01:08:57+00:00 | --root /home/russell/data/index_options/ob --source optionshist-chain --stream option_chain --mode backfill | 7ab82eaf14c9235dc5d285056b35c847bc2f3a2b494f6d525b503882a627b9be | /home/russell/data/index_options/ob |  |
| A0130 | acquire | backfill optionshist-chain/option_chain | 2026-09-26T01:10:40+00:00 | --root /home/russell/data/index_options/ob --source optionshist-chain --stream option_chain --mode backfill | f730f1234f5791ae26f99b5a4f00809d440ad7119352124f59b9a5e192419416 | /home/russell/data/index_options/ob |  |
| A0131 | acquire | backfill optionshist-chain/option_chain | 2026-09-26T01:12:36+00:00 | --root /home/russell/data/index_options/ob --source optionshist-chain --stream option_chain --mode backfill | d406ba1b3aa4347ddde99fa340aab9ea62581e0a080c2a3501602c6ae9541449 | /home/russell/data/index_options/ob |  |
| A0132 | acquire | backfill optionshist-chain/option_chain | 2026-09-26T01:14:29+00:00 | --root /home/russell/data/index_options/ob --source optionshist-chain --stream option_chain --mode backfill | aef102d9fa17ac4cb7a3315385db69c45b22300a16a3a6b9fee5a0ec4e689f42 | /home/russell/data/index_options/ob |  |
| A0133 | acquire | backfill optionshist-chain/option_chain | 2026-09-26T01:16:17+00:00 | --root /home/russell/data/index_options/ob --source optionshist-chain --stream option_chain --mode backfill | 6b55d97dfdc07debf12d4007b282a25bffd334621ab01c3702660334cfb477dd | /home/russell/data/index_options/ob |  |
| A0134 | acquire | backfill optionshist-chain/option_chain | 2026-09-26T01:17:32+00:00 | --root /home/russell/data/index_options/ob --source optionshist-chain --stream option_chain --mode backfill | b046ee87e29b0b0293b0fa44a697014e6643de298d157af3c5144bf80c233971 | /home/russell/data/index_options/ob |  |
| A0135 | acquire | backfill optionshist-chain/option_chain | 2026-09-26T01:17:33+00:00 | --root /home/russell/data/index_options/ob --source optionshist-chain --stream option_chain --mode backfill |  | /home/russell/data/index_options/ob |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| A0001 | Distribution modeling approach | Model the physical terminal distribution in vol-standardized log-moneyness; distribution-regression challenger; drop fixed bins | children/index_options/docs/research/distribution-modeling/2026-09-23-approach.md | Y |  | research | distribution-modeling/2026-09-23-approach | judgemental | docs/research/distribution-modeling/2026-09-23-approach.md |
| A0002 | Distribution evaluation metrics | Score forecasts with strike-zone twCRPS, censored likelihood, Brier at strikes, PIT/Berkowitz calibration, and OOS condor P&L/CVaR | children/index_options/docs/research/distribution-modeling/2026-09-23-evaluation.md | N |  | research | distribution-modeling/2026-09-23-evaluation | judgemental | docs/research/distribution-modeling/2026-09-23-evaluation.md |
