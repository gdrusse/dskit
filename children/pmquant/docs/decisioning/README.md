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
| A0024 | acquire | acquire the synthetic ladder world into ./onboarding_root (alias synthetic) | 2026-09-04T08:09:41+00:00 | pmquant.testing.acquire_synthetic(seed 11, KXSYNA+KXSYNB, 80 events/series) | streams pit + markets, backfill | ./onboarding_root | exec refused an 82-char step AFTER running the command; recorded by hand |
| A0025 | acquire | probe: polymarket pack download() against the real HF dataset (meta object) | 2026-09-04T11:57:03+00:00 | /home/user/dskit/.venv/bin/python /tmp/claude-0/-home-user/66401f5f-c522-5eae-9358-ca57f5e784df/scratchpad/hf_probe.py |  |  | Anonymous access through the container proxy; absent path -> None; no hour file pulled (360 MB each).; exit 0 |
| A0026 | execute | round 2: child + acquire + observations suites after ADR-0079 (commit stamp) | 2026-09-04T12:00:14+00:00 | pytest children/pmquant tests/pipeline_libs/test_observations.py tests/onboarding/test_acquire.py | 442 passed, 38 skipped |  | The child's acquire_synthetic and readers ride run_acquisition; ADR-0079 did not move them; exit 0 |
| A0027 | execute | round 2: final full dskit suite before commit | 2026-09-04T12:11:36+00:00 | /home/user/dskit/.venv/bin/python -m pytest /home/user/dskit/tests -q -p no:cacheprovider |  |  | Expected residue: optuna not installed (21), two root-user chmod tests, intraday_equities' known start-date failure.; exit 1 |
| A0028 | research | round-2-open-todos-closed-adr-0079-commit-stamp-one-backoff-ceiling-hf-archive-p | 2026-09-04T12:11:55+00:00 | Round 2: open TODOs closed (ADR-0079 commit stamp, one backoff ceiling, HF archive path verified) | docs/research/round-2-open-todos-closed-adr-0079-commit-stamp-one-backoff-ceiling-hf-archive-path-verified.md | docs/research/round-2-open-todos-closed-adr-0079-commit-stamp-one-backoff-ceiling-hf-archive-path-verified.md |  |
| A0029 | acquire | real e2e: live Gamma events -> token ids -> one real pmxt archive hour off HF | 2026-09-04T12:15:36+00:00 | /home/user/dskit/.venv/bin/python /tmp/claude-0/-home-user/66401f5f-c522-5eae-9358-ca57f5e784df/scratchpad/hf_e2e.py |  |  | highest-temperature-in-nyc, hour 2026-08-09T12; ~360 MB download, cleanup on; temp onboarding root.; exit 1 |
| A0030 | acquire | real e2e: live Gamma events -> token ids -> one real pmxt hour off HF (london) | 2026-09-04T12:18:53+00:00 | polymarket events: series_slugs london-daily-weather 2026-08-08..11; archive_hours: hour 2026-08-09T12, 66 token ids, cleanup on | events 33 markets (fee_rate 0.05); archive 139,733 rows (139,525 price_change + 208 book) from one ~360 MB hour file | /tmp/poly-e2e-05ublbtx/ob (temp root) | FOUND: archive key (asset_id, ts, event_type) not unique -> scan_stream refuses; same price level updated up to 3x in one ms; fix = seq within (asset_id, ts). exec refused an 85-char step after running; recorded by hand |
| A0031 | acquire | real e2e round 3: archive_hours resolves tokens from the slug; seq key; one hour | 2026-09-04T12:26:15+00:00 | polymarket archive_hours: series_slugs london-daily-weather 2026-08-08..11, hours [2026-08-09T12], no token_ids, cleanup on | 139,733 records (139,525 price_change + 208 book), 44 assets, max seq 3; scan_stream dedup read-back OK | /tmp/poly-e2e3-5zrpri3p/ob (temp root) | 34 s incl. the ~360 MB download; sync-state meta consulted; exec refused an 87-char step after running; recorded by hand |
| A0032 | execute | round 3: final full dskit suite before commit | 2026-09-04T12:35:26+00:00 | /home/user/dskit/.venv/bin/python -m pytest /home/user/dskit/tests -q -p no:cacheprovider |  |  | Expected residue: optuna not installed (21), two root-user chmod tests, intraday_equities' known start-date failure.; exit 1 |
| A0033 | research | round-3-the-polymarket-path-proven-on-real-data-the-archive-key-was-not-unique-s | 2026-09-04T12:35:54+00:00 | Round 3: the Polymarket path proven on real data; the archive key was not unique (seq); ADR-0080 | docs/research/round-3-the-polymarket-path-proven-on-real-data-the-archive-key-was-not-unique-seq-adr-0080.md | docs/research/round-3-the-polymarket-path-proven-on-real-data-the-archive-key-was-not-unique-seq-adr-0080.md |  |

## Path to Production

| ID | Label | Purpose | Relevant Files | LOCKED | Current Work (owner only) | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — |
