# Re-entry

Refreshed 2026-09-02, end of the ridge/row-loss session. On `main`.

---

# ▶ PICK UP HERE

**State: on `main`, tests green** (child 169, dskit 2787).

## ⇒ FIRST THING NEXT SESSION: make the walk-forward fast

A 20-fold walk costs ~35 minutes and **~95% of it is invisible to the node
timings.** Measured on `d39f7952`: each fold's own work is 4–7 s (`session` 3.2 s,
`scan` 0.6 s; `alpaca` and `features` are memoized to 0.000 s after fold
1), but **~105 s elapses between folds** — before the fold run dir
exists, so no node timing shows it. The process is `R` at 90% of one
core and 11.5 GB RSS during that window, so it is real compute in the
driver's per-fold LOAD→PLAN→RESOLVE, not idle waiting. **Cause not yet
identified — profile it, do not guess** (one guess this session was
already wrong: the node timings say `features: 0.000s`, which misled).

Two cheap levers alongside it:
- `BarsFromStore` has no date bound (`_PARAMS` is `root, source,
  universe, stream, ts_field, shared_fields`), so every run reads all six
  symbols from 2016 even when the walk starts in 2022. ~5 min fixed cost.
- The walk re-runs the whole document per fold; only `scan` depends on
  the fold cuts.

## What landed this session

**H=470 / L=120 VOIDED (A0035).** `_lookback_verdict` ranks by
`abs(ic_val)`, so L=120 was locked at val rank IC **−0.0825**; `_ic_se`
uses the iid null `1/sqrt(n-1)`, ~10x too small at lead 470 where rows
overlap 94-deep. Banner on `decision-hl-scan.md`; `universe.json` notes
now call 470/120 placeholders (values left — moving them moves the hash).

**Row loss found and fixed (A0038).** A scale with `cross_session:
false` and width W emits NaN for the first W minutes of **every**
session, and `_frame_matrix` drops a row on any non-finite column. `3h`
= 180 of 390 RTH minutes = 53.8% usable — matching the ~54% seen in
every fold. My first hypothesis (the `cross_session: true` scales) was
**backwards**: those cost a one-time warmup and are free over years.
Flipping `2h`/`3h`/`60m` to cross-session took `n_train` 20,913 →
**36,962** and `n_val` 1,763 → **3,107** (1.77x). Trade: those windows
now bridge the overnight gap, which changes what they mean.

**LightGBM overfits at every setting (A0036/A0037).** JPM, H∈{5,10,15,20},
66 features + 20 one-minute lags, 32-draw HPO on IC. Seven folds: train
IC 0.10–0.31 against val IC ~0, mean val IC **−0.008**, 6/7 negative,
zero GO. The inner holdout carries no information about fold validation
(best inner score → worst val), so **the 32-draw search selects noise.**
Not degenerate — `yhat_sd` 7.5e-05..3.2e-04, the stump guard passed.

**Ridge fixes overfitting, then underfits (A0040).** Six folds, rows
recovered: train IC 0.009–0.016 **below** val IC ~0.02 — over-regularized,
because the 86 features are unstandardized and alpha sweeps 1→1e5.
Clark–West p at lead 5: `.245 .694 .234 .018 .520 .586` — **1/6 below
0.05, median 0.383**, only fold 4 GO (h*=20). Stopped at 6/20 for speed.

**Toolkit gap: an interrupted walk records nothing.** `_journal_execute`
fires only after `_write_walkforward_summary` (driver.py:2400) and every
per-fold `run_document` passes `journal=False` (1991). Both stopped runs
here wrote N fold dirs and zero `actions.csv` rows; A0037/A0040 were
written by hand. Contradicts `run_document`'s own docstring. Needs an ADR.

**Also:** `_fit_estimator` passed LightGBM's `categorical_feature` to
every estimator (crashed Ridge) — fixed with `_accepts_categorical`.
`test_run_docs_do_not_restate_the_cohort` banned universe variants; it
now allows one but pins its cohort keys to `universe.json`.
`SessionFeatureRows` mutates its input `records` (latent; one node run
per pipeline, but unsafe for in-process A/B).

## Decisions awaiting the user

1. **Splits are unadjusted** (AAPL 2020-08-31, WMT 2024-02-26; the only
   corrupt values in 8.9M bars). Fix is `adjustment: split` plus a
   re-acquisition; it breaks comparability with everything recorded.
2. **`HorizonScan` / `LookbackScan` still train all-prior** — pass 2 would
   not be the same experiment as pass 1 (`docs/adhoc/deferred_decisions.md`).
3. **Protocol for H.** A0033/A0035 retired the sequential walk (it
   measures sample size: `h* ≈ 5·rho_5·sqrt(n)/z_alpha`). Proposed
   replacement, unratified: pre-declare H from cost or power, select L
   inside the inner train holdout, one statistic per name on contiguous
   forward blocks, report the block sequence — never a pooled mean.
   Owner call needed before anything locks.
4. **Zoo cannot take the 66 features** — no per-channel lag history exists;
   only the return is lagged.

5. **Vol-normalised label** — predict `return / rolling_vol` rather than
   raw return, so the model stops spending capacity on the volatility
   scale. Needs a node change, so ADR first. Not built.
6. **Standardise features before ridge** — the underfitting in A0040 is
   a scaling artifact. `intraday_equities/models.py` is empty and is the
   designated home for a `StandardScaler`+`Ridge` wrapper.

## Next session

**Speed first** (top of this file). Then decide 1–3. Nothing locks, no
pass 2, no Dec–Feb TPE, no peek after 2026-02-28 until then.

## Locked

- **H and L are UNSET.** H=470/L=120 is void (A0035); `universe.json` still carries them as placeholders. Book collapse deferred.
- HPO may use Dec 2025–Feb 2026. **No peek after 2026-02-28.**
- Action `lookback` stays 30.
- `dskit.journal` (ADR-0056). Uninitialized child refuses.
- Paper only. Test B sits inside Jun–Aug backtest, sealed until confirm.

## Verification

```bash
python -m ruff check .
python -m pytest tests/journal tests/children/test_skeleton.py tests/pipeline tests/onboarding -q
(cd children/intraday_equities && python -m pytest tests -q)
```
