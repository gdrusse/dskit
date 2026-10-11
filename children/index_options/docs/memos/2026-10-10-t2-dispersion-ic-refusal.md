# T2 forecast-dispersion IC — run REFUSED at with_forecast, 2026-10-10

## TL;DR

The pre-registered T2 run (ADR-0259, config identity `1ad8d730…`, `--asof 2025-12-31`, origin/main `d7d309d`) **refused at `with_forecast`**. No IC, no t, no pass/fail exists: T2 is neither passed nor failed. The refusal is the finding; nothing was patched, re-banded or re-run.

```
with_forecast: row 351 ('ANET', '2024-11-05'): 'spot' is 405.12 on the stream
but None in the table — the sources disagree about a field they must share
```

Run `forecast-dispersion-ic-2025-12-31-64c466f0` (A1131, `state=error`), under
`/home/russell/data/index_options/production-universe-rerun-20261008/forecast-dispersion-ic/`.
Nodes 1–9 ok (`forecast_scale` 99,225 rows); `with_forecast` errored; `with_realized` onward not run; `report.md` is the error banner only.

## What it is, and is not

- Not the "basis mismatch" the note anticipated (two different prices): the stream side holds a real close (405.12); the **table side (`forecast_scale.records`, the saved forecast curves) carries `spot` null** for that (symbol, quote_date).
- It is the first disagreement hit, not a census: the count of forecast rows with null `spot` is unknown. The attachment stops at the first.
- `implied_priced` already drops straddle rows with null spot; no equivalent filter exists on the forecast side, by design (the attachment's exactness is the guard).

## Environment note

The shared venv (`/home/russell/dskit/.venv`) is an editable install of the stale worktree `wt/claude-sonnet-5-5`, which lacks ADR-0259's nodes (first attempt: "invalid config", 3 unresolved components). The run used `PYTHONPATH=<fresh worktree>` with the same interpreter and command. Results do not depend on it.

## Decision for the owner

Whether null-`spot` forecast rows are expected (a saved-curve row with no as-traded close) and how the config should treat them is a config/ADR amendment, i.e. a new pre-registered change, not a re-run. Do not tune the band, `h_steps` or any threshold; none of them is implicated.
