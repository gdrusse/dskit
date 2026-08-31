# Decision horizon models

**Status:** tree leads a 2-epoch scalar bakeoff at H=1165. Not a production pick.

Lockbox unread. Same val rows as the scan (`n_val` 18635). Features are the 30 one-minute lags; the scan's +0.087 used top-k of the full session set. `n_ahead` stays 1 (one-pick wants a scalar).

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
