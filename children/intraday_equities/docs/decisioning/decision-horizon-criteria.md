# Decision horizon criteria

**Status:** open. No production horizon until this run is logged here.

Farthest RTH lead where val rank IC still clears the universe go/no-go.
Lockbox (`splits.test_end_ms`) is unread: labels may not land after
`val_end_ms`. Widen markets or the grid by editing `configs/universe.json`
(plus sources/suites). Do not edit nodes.

## Run (from `children/intraday_equities`)

```bash
python -m dskit.pipeline run configs/run-horizon-scan.json \
  --asof 2026-08-30 --adapter intraday_equities
```

## Configs

- `configs/universe.json` — cohort, holidays, scales, `horizon.*`
- `configs/run-horizon-scan.json` — graph; cuts stop at `val_end_ms`

## Output (fill after the run)

| Item | Path / value |
|---|---|
| run dir | `pipeline_runs/intraday-equities-horizon-scan-2026-08-30-<hash8>/` |
| curve | that dir `nodes/*scan*` / `result.json` |
| MLflow | `mlruns.db` experiment `intraday_equities` |
| `go` / `go_anchor` / `go_band` | |
| `farthest_confident_lead` | |
| `peak_lead` / `peak_ic` / `rank_ic` | |

## Result

undecided — run not executed.
