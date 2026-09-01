# Re-entry

Refreshed 2026-09-01 after HL-scan + framework HPO.

---

# ▶ PICK UP HERE

**State: H=470, L=120, 28 keep (no lags). 50 TPE LightGBM val IC +0.076. T bakeoff is next.**

Branch: `cursor/horizon-scan-signal-b625` (merged to `main` this wrap).

## Next session

T bakeoff `{1y, 2y, 3y, 5y, all-prior}` (`train_days`, ADR-0050). Then V walk-forward (H-length embargo, 36–48 folds). Do not reopen August 2026. Markets/grid change only in `configs/universe.json`.

## Locked

- H = **470**, L = **120** (`scan.picked_lookback`), keep = 28 names. Action `lookback` stays 30. Evidence: `children/intraday_equities/docs/decisioning/`.
- One-minute raw bars; coarser views via `event-grid`.
- Paper only. Test B (August 2026) unassigned.

## Verification

```bash
python -m ruff check .
python -m pytest children/intraday_equities/tests tests/children/test_skeleton.py tests/pipeline_libs/test_torch_ts.py tests/pipeline_libs/test_numpy.py tests/pipeline_libs/test_sklearn.py tests/pipeline -q
```
