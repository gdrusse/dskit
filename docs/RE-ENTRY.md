# Re-entry

Refreshed 2026-09-03, end of the horizon-sweep session. On `main`.

---

# ▶ PICK UP HERE

## ⇒ The two-bar wall: rank survives where accuracy dies

**30 walks complete** (5 models × H=1/2/3/20/30/60, 20 folds each,
JPM/LLY/XOM). Clark–West gain (model MSPE vs the training mean's):

| H | ridge | lgbm | gru | lstm | tft |
|---|---|---|---|---|---|
| 1 | **+0.0898** | **+0.2337** | −7.40 | −10.61 | −4.90 |
| 2 | **+0.0096** | **+0.0796** | −7.72 | −11.17 | −5.04 |
| 3 | −0.0206 | −0.0049 | −8.42 | −11.72 | −5.25 |
| 20 | −0.0285 | −0.6512 | −11.86 | −12.88 | −17.01 |
| 30 | −0.0948 | −1.1620 | −39.57 | −37.43 | −28.04 |
| 60 | −0.1144 | −1.0530 | −53.71 | −47.93 | −38.83 |

**Four positive cells out of thirty. All at H ≤ 2, all low-capacity.**
Both curves cross zero between H=2 and H=3 — a two-bar wall. The nets
never enter positive territory at any horizon, and their gain worsens
monotonically with capacity and with h.

**The pre-registered 1/h bounce prediction is FALSIFIED — and the test
did not cleanly replace it.** Predicted +0.045% at H=2 from +0.090% at
H=1. Ridge delivered +0.0096% (a **9.4x** fall, far too fast for a
fixed-size bounce term against variance growing like h). LightGBM
delivered +0.0796% (a **2.9x** fall, close to the predicted 2x). So the
two models disagree about the decay: ridge's rules bounce out, LightGBM's
is consistent with it. **Mechanism still unnamed.**

**The unexplained thing worth chasing: val IC decays far more slowly than
the gain and stays positive nearly everywhere** (LightGBM +0.054 → +0.030
at H=3, still +0.020 at H=60; 19–20/20 folds positive) while its MSPE
gain has gone to −1.05%. Rank information persists ~30x further out in
horizon than forecast accuracy. For a PREDICTION-ONLY model feeding a
selecting optimizer, that gap is the whole question, and nothing here
measures it.

**Next, in order:**
1. **Calibration** — regress y on ŷ per fold. Slope ≈1 ⇒ magnitude is
   sizeable; slope ≪1 ⇒ only the ranking is usable, which is what the
   IC/gain divergence hints. Small addition to the scan node.
2. **Per-timestamp cross-sectional IC** — an optimizer chooses AMONG
   names at one instant; every number above is pooled per name over time.
3. **VWAP variant of `h01-ridge` / `h01-lgbm`** (not built, one config
   each). `vwap` averages every print in the minute, so its bounce term
   is ~b²/n. If the H=1 gain collapses under VWAP, bounce was the source
   after all. Caveat: VWAP is an interval average, so it smears the
   decision instant — diagnostic, not a production target.

**Why bounce was suspected:** `px` is the last TRADE print (bars carry
`trade_count`/`volume`/`vwap`; `price_field` is `close`), so it carries a
±half-spread coin flip. `px[t]` sits in the label with a minus sign and in
`ret_lag_0 = log(px[t]/px[t-1])` with a plus. ADR-0059's transforms do
NOT close this: the SPY residual removes market variance (raising
bounce's share) and `sigma_t` is a scalar measurable at t.

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
