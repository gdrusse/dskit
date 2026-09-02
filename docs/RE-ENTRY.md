# Re-entry

Refreshed 2026-09-02, end of the H* correctness session. On `main`.

---

# ▶ PICK UP HERE

**State: on `main`, tests green** (child 169, dskit 2787).

**What landed (2026-09-02).** Three declared knobs were inert or inverted:
`min_split_gain: 0.02` made every LightGBM tree a stump (the `b5967dff`
result is VOID, A0028); `train_days` never reached the scan node, so every
walk trained all-prior from 2016; `subsample` is a no-op without
`subsample_freq`. First two fixed, third left as a modelling call. Added a
degenerate-forecast guard, `train_start_ms` support, a selectable HPO
objective, a 50-70x rolling-extrema rewrite, feature-build memoization
(72 min → 27 s per walk), the resolved document at the top of every
`run.log`, and torch_ts zoo registration. Journal A0025–A0033.

**Result: no measurable edge over the mean.** Post-COVID 20-fold bounded
walk (AAPL/JPM): mean val IC +0.006, GO 4/40, 0 survive BH. Clean 2022+
walk (JPM/XOM, split-free): GO 1/22, median p 0.469. Zoo LSTM/GRU/TFT at
30- and 180-minute lag windows: every model worse than predicting the
average. Earlier positives came from training on a decade the design
never sanctioned. **Do not lock H.**

## Decisions awaiting the user

1. **Splits are unadjusted** (AAPL 2020-08-31, WMT 2024-02-26; the only
   corrupt values in 8.9M bars). Fix is `adjustment: split` plus a
   re-acquisition; it breaks comparability with everything recorded.
2. **`HorizonScan` / `LookbackScan` still train all-prior** — pass 2 would
   not be the same experiment as pass 1 (`docs/adhoc/deferred_decisions.md`).
3. **Research A0033** finds the H walk measures sample size, the documented
   L rule does not exist in code (it selects on |IC|, sign-blind — H=470/L=120
   was locked at val IC **−0.0825**), and the confirm block is ~4x
   underpowered. Needs an owner call on protocol before any lock.
4. **Zoo cannot take the 66 features** — no per-channel lag history exists;
   only the return is lagged.

## Next session

Decide 1–3. Nothing locks, no pass 2, no Dec–Feb TPE, no peek after
2026-02-28 until then.

## Locked

- H/L from sliding CV through Nov 2025 (per-name `h*`, MSPE L). Book collapse deferred. Not |IC| H=470.
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
