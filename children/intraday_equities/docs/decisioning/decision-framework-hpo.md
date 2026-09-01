# Framework HPO (50 TPE)

**Status:** complete 2026-09-01. All-prior LightGBM on the HL-scan keep. Val rank IC **+0.076**.

## Run (from `children/intraday_equities`)

```bash
python -m dskit.pipeline run configs/run-framework.json \
  --asof 2026-08-30 --adapter intraday_equities
```

## Output

| Item | Path / value |
|---|---|
| run dir | `pipeline_runs/intraday-equities-framework-2026-08-30-3ca0081e/` |
| log | [logs/framework.out](logs/framework.out) |
| n_train / n_val | 815057 / 19215 |
| best val rank IC | **0.0761** |
| winner | lr 0.0104, min_child 83, n_estimators 111, num_leaves 18 |
| ensemble | top 10% → 5 members (ADR-0052) |

Search 50 TPE in 1187s. T bakeoff and V walk-forward are next.
