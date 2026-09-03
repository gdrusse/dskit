## Question

Is `log(px[t+h]/px[t])` the right target for the JPM H<=20 scan, and if
not, what replaces it?

## Finding

**It is the weakest defensible choice, for two reasons that belong to
the LABEL and not to the estimator.**

1. **Heteroskedastic weighting.** Squared loss weights each row by its
   label's variance, so the open hour (roughly 5-10x midday variance)
   dominates the fit. A0040's ridge underfits the whole day to survive
   the open — train IC 0.009-0.016 BELOW val IC ~0.02. Pooled IC on one
   name ranks ACROSS time, so the ranks partly encode minute-of-day.
2. **An unforecastable market component.** At 5-20 minutes most of a
   single name's variance is the market move. It enters the label as
   noise, while the features already carry `residual_SPY`.

**Judgement call (owner-directed, unratified): build both, run neither
yet.** ADR-0059 is written and the code is in, default-off:

- `label_scale: "vol"` divides by `sigma_t * sqrt(h)`, `sigma_t` a
  causal `rolling_std` of the 1-minute log return over
  `vol_window_minutes` (390, one session). This is WLS on the raw
  return; the estimand becomes a risk-adjusted return, which is what
  inverse-vol sizing trades.
- `label_residual: "SPY"` subtracts `beta_t * y_ref(t, t+h)`, `beta_t` =
  `rolling_sum(r*r_ref)/rolling_sum(r_ref^2)` over
  `beta_window_minutes` (3900, ten sessions).
- Composed, the residual comes first and the vol of the RESIDUAL scales
  it. Session boundaries are blanked so an overnight jump never enters
  sigma or beta. A reference with no tape is a loud refusal.

One `_LeadLabel` object now defines the label; `_raw_lead_return` is the
single spelling of the raw return, replacing three copies.

**What this costs.** `train_mspe`/`val_mspe` become LABEL units: a
vol-normalised run's MSPE is NOT comparable with a raw run's, and the
Clark-West / no-information verdict tests a different estimand. `val_ic`
stays rank-based and comparable. A0040's per-fold numbers do not carry
over — reading these labels needs a fresh fold sequence.

**Not addressed by any label change**, and still bounding what a fold
sequence can show: labels at lead 20 overlap 19-deep while `_ic_se` uses
the iid null (A0035), and close-to-close on trade prices carries
bid-ask bounce that a midquote tape would remove more cheaply than any
reformulation.

## Sources

- `docs/architecture/decision-log.md` ADR-0059 (proposed, unratified).
- `intraday_equities/nodes.py` — `_LeadLabel`, `_raw_lead_return`,
  `_bar_returns`, `_align_returns`, `LABEL_PARAMS`.
- `tests/test_nodes.py` — causality, residual, sigma, refusals, the
  knob-coverage pin.
- A0040 (ridge underfit), A0035 (the SE null), A0036/A0037 (LightGBM).
- Identity unmoved: `run-jpm-h20-ridge.json` still `d39f7952...`.
