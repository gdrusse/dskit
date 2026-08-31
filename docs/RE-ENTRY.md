# Re-entry

Refreshed 2026-08-31 after wrapping the horizon go and the H=1165 bakeoff.

---

# ▶ PICK UP HERE

**State: horizon is 1165. Tree won a 2-epoch scalar bakeoff. Path output is next.**

Branch: `cursor/horizon-scan-signal-b625` (PR #6).

## Next session

A 1165-step `n_ahead` path (ADR-0049; one-pick still wants a scalar), or HPO the tree at H=1165. Do not reopen the lockbox. Markets/grid change only in `configs/universe.json`.

## Locked

- Farthest confident lead: **1165** RTH minutes. Evidence: `children/intraday_equities/docs/decisioning/`.
- One-minute raw bars; coarser views via `event-grid`.
- Paper only. Latest six months stay locked.

## Verification

```bash
python -m ruff check .
python -m pytest children/intraday_equities/tests tests/children/test_skeleton.py tests/pipeline_libs/test_torch_ts.py tests/pipeline_libs/test_numpy.py tests/pipeline_libs/test_sklearn.py -q
```
