# Re-entry

Refreshed 2026-08-30 after wrapping the horizon-scan / decisioning setup.

---

# ▶ PICK UP HERE

**State: scan is wired; the real-data run is not. Horizon is undecided.**

Branch: `cursor/horizon-scan-signal-b625` (PR #6, stacked on the MLflow
branch). Do not merge until the evidence file is filled.

## Next session — decision horizon criteria

From `children/intraday_equities` (data already at `./ob` → the onboarding
root):

```bash
python -m dskit.pipeline run configs/run-horizon-scan.json \
  --asof 2026-08-30 --adapter intraday_equities
```

Then write the run dir, `go` / `farthest_confident_lead` / ICs into
`docs/decisioning/decision-horizon-criteria.md` and flip the grid row.

Rules:

- Lockbox unread. Labels stop at `val_end_ms`. Do not open test rows.
- Markets and the grid change only in `configs/universe.json` (+ sources
  and suites). Not in nodes.
- No go without that evidence file.

Success: a farthest confident lead we would use in production, or an
explicit no-go.

## Locked

- One-minute raw bars; coarser views via `event-grid`.
- Cohort / holidays / scales / go-no-go knobs: `configs/universe.json`.
- Paper only. Latest six months stay locked.

## Verification

```bash
python -m ruff check .
python -m pytest children/intraday_equities/tests tests/children/test_skeleton.py -q
```
