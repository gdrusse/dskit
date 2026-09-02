# HL-scan (H / L / keep)

**Status:** complete 2026-08-31. LightGBM on the full session set. Val IC is **negative**; go is `|IC|` vs 2 SE.

## Run (from `children/intraday_equities`)

```bash
python -m dskit.pipeline run configs/run-hl-scan.json \
  --asof 2026-08-30 --adapter intraday_equities
```

## Output

| Item | Path / value |
|---|---|
| run dir | `pipeline_runs/intraday-equities-hl-scan-2026-08-30-c60b2910/` |
| `go` / `go_anchor` / `go_band` | 1 / 1 / 1 |
| `farthest_confident_lead` (H) | **470** |
| `peak_lead` / `peak_ic` / rank IC at H | 440 / −0.090 / −0.0825 |
| L (1-SE of peak \|IC\|) | **120** (= `lookback_stop`) |
| keep | **28** names; **no `ret_lag_*`** |
| `n_val` / `n_leads` | 15675 / 234 |

## Pins

- `horizon.label_lead` = 470
- `scan.picked_lookback` = 120
- `keep_features` = the 28 names in `universe.json`
- `universe.lookback` stays **30** (action-window length). Session L and action lookback are different knobs.

Train IC ~0.39 vs val IC −0.08: the scan overfits; keep dropped every lag.

## Next

Superseded as an H estimand by [hstar-go.md](hstar-go.md) (ADR-0058). T bakeoff waits on GO+confirm.
