# Decision horizon criteria

**Status:** go. Farthest confident lead is **1165** RTH minutes (~3 sessions).

Lockbox unread. Labels stopped at `val_end_ms`. All three anchors (390 / 780 / 1170) passed. Peak is 1110; 1165 is the longest lead still within 1 SE of that peak. Treat 1165 as the candidate, not a frozen production horizon, until cadence/HPO/lockbox.

## Run (from `children/intraday_equities`)

```bash
python -m dskit.pipeline run configs/run-horizon-scan.json \
  --asof 2026-08-30 --adapter intraday_equities
```

## Configs

- `configs/universe.json` — cohort, holidays, scales, `horizon.*`
- `configs/run-horizon-scan.json` — graph; cuts stop at `val_end_ms`

## Output

| Item | Path / value |
|---|---|
| run dir | `pipeline_runs/intraday-equities-horizon-scan-2026-08-30-52cb23a5/` |
| curve | 234 leads (summarized; metrics in `carry.json`) |
| MLflow | `mlruns.db` experiment `intraday_equities` |
| `go` / `go_anchor` / `go_band` | 1 / 1 / 1 |
| `farthest_confident_lead` | 1165 |
| `peak_lead` / `peak_ic` / `rank_ic` | 1110 / 0.0942 / 0.0874 |
| `n_val` / `n_anchors_pass` | 18635 / 3 |

## Result

go — use 1165 as the farthest confident lead. Peak IC 0.094 at 1110. Rank IC at farthest 0.087. Null SE is anti-conservative (overlapping 5-minute rows); this is the gate, not the lockbox test.
