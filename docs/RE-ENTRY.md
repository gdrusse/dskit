# Re-entry

Refreshed 2026-09-01 after merging to `main` (H/L lock, HPO, ADR-0056 journal).

---

# ▶ PICK UP HERE

**State: on `main`. H=470, L=120, 28 keep. Journal package live. T bakeoff is next.**

## Next session

T bakeoff `{1y, 2y, 3y, 5y, all-prior}` (`train_days`, ADR-0050). Then V walk-forward. Do not reopen August 2026. Markets/grid change only in `configs/universe.json`. Owner `journal promote` for path-to-production rows.

## Locked

- H = **470**, L = **120**, keep = 28 names. Action `lookback` stays 30.
- `dskit.journal` (ADR-0056): CSV ledger + generated `docs/decisioning/README.md`. Uninitialized child refuses.
- Paper only. Test B (August 2026) unassigned.

## Verification

```bash
python -m ruff check .
python -m pytest tests/journal tests/children/test_skeleton.py tests/pipeline tests/onboarding -q
```
