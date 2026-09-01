# Decision horizon models

**Status:** incomplete. The recorded run used 30 one-minute lags, not the full session set. Tree-vs-DL at H=1165 is not a pick. Re-bake only after pipeline #1 locks H/L/features.

## Run (from `children/intraday_equities`)

```bash
python -m dskit.pipeline run configs/run-horizon-models.json \
  --asof 2026-08-30 --adapter intraday_equities
```

## Output

| Item | Path / value |
|---|---|
| run dir | `pipeline_runs/intraday-equities-horizon-models-2026-08-30-87368cbb/` |
| `n_train` / `n_val` | 815100 / 18635 |
| tree `rank_ic` / hit | **0.0189** / 0.574 |
| lstm / gru / transformer `rank_ic` | −0.0045 / −0.0048 / −0.0076 |
| ridge `rank_ic` | −0.0207 |
| torch `val_loss` (2 epochs) | ~0.0008 all three |

## Result

Tree is the only positive one-pick IC. The RNNs and transformer were not HPO'd. A 1165-step path is a later document.
