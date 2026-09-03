## Question

On the three split-free tradables (JPM, LLY, XOM) with the ADR-0059
label — residual to SPY, divided by its own causal 390-minute sigma
times sqrt(h) — do ridge or LightGBM beat no-information over 20
post-COVID folds?

## Finding

**Neither does. Both land at the rejection rate the null predicts.**

Same folds, same cohort, same label; the estimator block is the only
difference between the two documents.

| | ridge | LightGBM |
|---|---|---|
| val IC mean | +0.0064 | **+0.0136** |
| val IC median | +0.0033 | +0.0152 |
| val IC positive | 14/20 | **17/20** |
| train IC | +0.0162 | +0.1146 |
| MSPE train / val | 1.387 / 1.345 | 1.314 / 1.352 |
| Clark-West p<.05 | 4/60 (6.7%) | 3/60 (5.0%) |
| p<.10 | 6/60 | 8/60 |
| t mean (60 name-folds) | +0.101 | +0.155 |
| t > 0 | 28/60 | 31/60 |
| GO | JPM 1, LLY 2, XOM 1 | JPM 0, LLY 3, XOM 0 |

**5% is what a true null delivers at alpha=0.05.** Ridge's 6.7% and
LightGBM's 5.0% are indistinguishable from it, and the two models agree
on exactly **one** of the seven GO name-folds. A rejection neither model
reproduces, on a fold the other calls p=.20, is a fold, not an edge.

**What the label DID fix.** A0040's raw-label ridge had train IC BELOW
val IC — over-regularised on unstandardised, heteroskedastic columns.
Here train 0.0162 > val 0.0064, the ordering a fitted model should have,
and MSPE is ~1 because the label is in sigma units. The vol scaling did
its job; there was simply no signal underneath it.

**What LightGBM still does.** Train IC +0.1146 against val +0.0136 —
8.4x, the A0036 signature, on 110k rows with only six inner draws.
Its higher val IC (+0.0136 vs +0.0064) buys no significance: **its
MSPE is WORSE than ridge's on validation** (1.352 vs 1.345) while
better on train. It ranks slightly better and forecasts slightly worse,
which is what a model fitting curvature that does not generalise looks
like.

**One fold is not vol.** 2024-10-04 has val MSPE 2.18 (ridge) / 2.22
(tree) against a ~1.2 median. A causal 390-minute sigma cannot absorb a
regime the training window never saw.

## Sources

- `configs/run-multi3-h20-ridge.json` (`4fc6a970...`), 20 folds,
  A0045; `configs/run-multi3-h20-lgbm.json` (`5ac2038a...`), A0046.
- Folds: first 2022-05-06, step 63d, val 63d, embargo 5d, train 730d,
  last val inside 2025-11-30. n_train ~110k, n_val ~9.5k per fold.
- ADR-0059 (the label), ADR-0060 (estimator as a document knob; t and
  se on every curve row).
- AAPL and WMT excluded: unadjusted splits in the raw tape.
- Prior: A0040 (raw-label ridge, JPM only), A0036/A0037 (LightGBM).
