# Re-entry

Refreshed 2026-09-03, end of the horizon-sweep session. On `main`.

---

# ▶ PICK UP HERE

## ⇒ Is the H=1 gain a forecast, or the shared `px[t]` in both sides?

**Two cells out of eighteen beat the training-mean benchmark, and both are
H=1:** `h01-ridge` +0.090% MSPE gain (val IC +0.0340, 19/20 folds
positive) and `h01-lgbm` +0.234% (+0.0538, **20/20**). Every other
model × horizon forecasts WORSE than the mean. Not overfitting — train
and val MSPE nearly equal (0.77 / 0.80) — and no leak found: features
read bars ≤ t, `sigma_t` and `beta_t` are strictly causal. It is a
martingale rejection at one minute, and it replicates across model
classes.

**The open question is WHAT is predicted, not whether.** The label is
`[log(px[t+h]/px[t]) - beta_t*log(qx[t+h]/qx[t])] / (sigma_t*sqrt(h))`,
and **`px` is the last TRADE print** (the bars carry `trade_count` /
`volume` / `vwap`; `price_field` is `close`), so it carries a ±half-spread
coin flip. `px[t]` sits in the label with a minus sign and in
`ret_lag_0 = log(px[t]/px[t-1])` with a plus — negative feature/label
correlation that exists under a perfect random walk. **ADR-0059's
transforms do not close this**: the SPY residual removes market variance
(raising bounce's share) and `sigma_t` is a scalar measurable at t.

Two discriminators, both cheap, neither finished:

1. **The H=2 / H=3 decay** (10 walks queued, running now). Bounce
   predicts the gain falls as 1/h: **~+0.045% at H=2, ~+0.030% at H=3.**
   Decay SLOWER than that ⇒ not bounce ⇒ genuine short-horizon reversion.
2. **A VWAP variant of `h01-ridge` / `h01-lgbm`** (not built, one config
   each). `vwap` averages every print in the minute, so its bounce term
   is ~b²/n instead of b². If the H=1 gain collapses under VWAP, bounce
   was the source. Caveat: VWAP is an interval average, so it smears the
   decision instant — a diagnostic, not automatically a production target.

A midquote tape, or a label that skips a bar (features to `t-1`, label
`t -> t+h`), would settle it outright. Neither exists.

**Framing the owner set:** the model is PREDICTION ONLY — an optimizer
selects downstream — so cost arithmetic is out of scope. The surviving
objection is validity: a bounce signal forecasts the next PRINT, not the
next value.

## What landed this session

**Walk-forward is ~30x faster (measured, not guessed).** `_resolve_run`
re-scanned and re-hashed all 8.9M store records PER FOLD (139 s + 44 s),
invisible because it runs before the fold's run dir exists. Content-keyed
class cache on `BarsFromStore`: **105 s/fold → 3.4 s/fold**, a 20-fold
walk 35 min → ~3.5 min. New in dskit: `dir_digest` (onboarding/base) and
`stream_dir` (observations).

**Four ADRs, all PROPOSED — none ratified:**
- **0059** — the label: `label_scale: "vol"` (÷ `sigma_t*sqrt(h)`, causal
  390-bar `rolling_std`) and `label_residual: "SPY"` (beta from 3900-bar
  rolling cross-products). One `_LeadLabel` replaces three copies of
  `log(px1/px0)`. Fixed A0040's inversion; did not create signal.
- **0060** — `estimator` is a document knob; `t_stat`/`se` on every curve
  row, `t_stat_<sym>` in metrics, per-series INFO logging.
- **0061** — `ZooEstimator` in `libs/torch_ts.py`: the ADR-0041 zoo
  reached through the sklearn contract. Splits the row BY NAME —
  `ret_lag_*` is the time axis, the other 67 columns ride as constant
  channels.
- **0062** — `lead_start`/`lead_step`/`lead_stop` are document knobs, so
  one universe serves every horizon.

**Five model classes × four horizons, 20 folds each** (JPM/LLY/XOM; AAPL
and WMT excluded for unadjusted splits). Two orderings run through the
whole grid: **gain falls with horizon, and gain falls with capacity.**
Ridge is the best forecaster at every H; the nets are worst everywhere
(−54% at H=60, train IC +0.52 against a negative val IC). At H≥20 every
model sits at the null (1.7–13.3% rejections around a 5% null).

**Rejection counts do not track skill.** `h01-gru` rejects 12/60 while
forecasting 7.4% worse than the mean. Clark–West adds back the variance a
nested model pays for estimating parameters, so it rejects on the
POPULATION claim — only the gain column separates forecast from
correction.

## Decisions awaiting the user

1. **Ratify or reject ADR-0059 / 0060 / 0061 / 0062** — the code is in the
   tree ahead of approval.
2. **Splits still unadjusted** (AAPL 2020-08-31, WMT 2024-02-26) — why
   they are excluded rather than fixed.
3. **The H protocol** from ADR-0058 is still unsettled.
4. **The zoo has no static-covariate path** — the nets got the 67 non-lag
   features as constant channels. Defensible, not what a TFT is designed
   to consume; a fair test needs per-channel lag history.
5. **An interrupted walk still journals nothing** (`_journal_execute`
   fires only after the summary; per-fold `run_document` passes
   `journal=False`). Needs an ADR.
6. **Not built, discussed:** calibration (regress y on ŷ; slope ≈1 means
   the magnitude is sizeable) and per-timestamp cross-sectional IC — what
   a downstream optimizer actually needs, which per-name pooled tests do
   not give.

## Locked

- **H and L are UNSET.** H=470/L=120 is void (A0035).
- HPO may use Dec 2025–Feb 2026. **No peek after 2026-02-28.**
- `dskit.journal` (ADR-0056). Uninitialized child refuses.
- Paper only. Test B sealed until confirm.

## Verification

```bash
python -m ruff check .
python -m pytest tests -q
(cd children/intraday_equities && python -m pytest tests -q)
```
