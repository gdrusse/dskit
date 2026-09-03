# HL-scan (H / L / keep)

**Status:** ~~complete 2026-08-31~~ **VOID 2026-09-02 (A0035).** LightGBM on the full session set.

> ### ⚠️ READ BEFORE CITING THIS DOCUMENT
>
> **H = 470 and L = 120 are selector artifacts, not measurements.** Do not
> cite `farthest_confident_lead`, `peak_lead`, the picked L, or the
> `go`/`go_anchor`/`go_band` flags as evidence about horizon or memory.
> Three independent defects, all in
> [`l-and-h-selection-the-h-walk-measures-sample-size-not-horizon.md`](../research/l-and-h-selection-the-h-walk-measures-sample-size-not-horizon.md):
>
> 1. `_lookback_verdict` ranks by `abs(ic_val)`, so an **anti**-signal can
>    win — and did: L = 120 was locked at a validation rank IC of
>    **−0.0825**.
> 2. `_ic_se(n) = 1/sqrt(n-1)` is the iid null SE. At lead 470 the rows
>    overlap 94-deep (`n_eff ≈ 167`), so the honest SE is ~10× larger and
>    the 1-SE band is a peak-tracker, not a regulariser.
> 3. The h* walk's stopping point has the closed form
>    `h* ≈ 5·rho_5·sqrt(n)/z_alpha` — it grows with tape length. It reports
>    sample size, not a horizon. At the measured edge (+0.0073) it returns
>    **5.7 minutes**.
>
> `universe.json` still carries `horizon.label_lead = 470` and
> `scan.picked_lookback = 120` — those are **placeholders now, not a lock**.
> Any run that reads them (e.g. `run-framework.json`) is not measuring H.

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

## ~~Pins~~ — VOID, not a lock

- `horizon.label_lead` = 470
- `scan.picked_lookback` = 120
- `keep_features` = the 28 names in `universe.json`
- `universe.lookback` stays **30** (action-window length). Session L and action lookback are different knobs.

Train IC ~0.39 vs val IC −0.08: the scan overfits; keep dropped every lag.

## Next

Voided outright (A0035). H and L are **unset**. See [hstar-go.md](hstar-go.md) (ADR-0058) for the replacement protocol and its own open blockers; T bakeoff waits on GO+confirm.
