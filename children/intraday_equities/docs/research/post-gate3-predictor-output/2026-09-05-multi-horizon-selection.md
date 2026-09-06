## Question

For each Gate-3-approved asset with terminal horizon \(H\), should the model zoo
emit and evaluate forecasts at every lead \(k=1,\ldots,H\), and how should a
single deployable model be selected when relative performance can change by
horizon?

## Finding

**Yes: the zoo should treat each candidate as a full-path forecasting system.**
Every candidate for an approved `(asset, H)` must emit forecasts for the common
lead grid `1..H`, and the evidence record must retain metrics at every lead.
Gate 3 certified the terminal `(asset, H)` family; it did **not** separately
certify every shorter lead. The shorter-lead results are new zoo evidence and
must not be relabeled as Gate-3 passes.

### Forecast construction

Test both research-supported full-path strategies under the same information
set and walk-forward origins:

1. **Direct:** fit a distinct target/head for each lead and concatenate the
   forecasts into one path.
2. **Joint multiple-output:** fit one model whose target is the vector
   `(y_1, ..., y_H)`.

Ben Taieb, Sorjamaa, and Bontempi show why joint multiple-output forecasting is
a real candidate: independently fitted direct forecasts need not preserve the
dependence structure of the path. This is evidence to compare direct and joint
heads, not a theorem that one will win here. Recursive one-step rollout may be
kept as a challenger, but it should not substitute for genuinely trained
horizon-specific outputs.

Use identical rolling origins, features available at the origin, outer folds,
and a purge/embargo based on the maximum lead `H`. For the primary path score,
use only origins where all `1..H` outcomes are observable. Tashman's review
supports rolling-origin, repeatedly re-estimated out-of-sample evaluation; the
common-origin and max-`H` rules are judgmental safeguards for comparability and
leakage control in this program.

### What to measure

Persist a loss tensor by `asset × candidate × outer_fold × origin × lead`.
For each lead report at least benchmark-relative squared-error skill, IC and
calibration slope; for uncertainty forecasts report a proper marginal score
(pinball/interval score or CRPS), realized coverage, and width. Also score the
joint predictive path with an energy score and a variogram score. Gneiting and
Raftery support proper scoring of predictive distributions; Scheuerer and
Hamill show that the variogram score is more sensitive to misspecified
dependence than the energy score in their experiments.

Do not average raw MSE across leads: return variance and overlap change with
lead, so long leads can dominate mechanically. Define before seeing zoo
results a benchmark-relative, horizon-normalized multivariate loss at each
forecast origin, for example

\[
D_{m,o}=\sum_{k=1}^{H} w_k
\frac{\ell(y_{o,k},\hat y_{m,o,k})-
      \ell(y_{o,k},\hat y_{0,o,k})}{s_k},
\qquad w_k\ge 0,\quad \sum_k w_k=1,
\]

where model `0` is the frozen no-information benchmark and each scale `s_k`
is estimated and frozen using training-only data. Capistran supports using a
multivariate loss aligned with the user's preferences instead of informally
averaging horizon errors. The exact normalization and weights are
**judgmental**. Until the MIO supplies a locked horizon utility, equal weights
are the neutral default; any capital/decision weights must be fixed before
opening outer results.

### Model selection

Use three outputs, each with a different job:

1. **Per-horizon diagnosis:** construct Horizon Confidence Sets across all zoo
   candidates. Fosten and Gutknecht's procedure allows the superior set to
   change by horizon while controlling the multiple testing induced by looking
   across horizons.
2. **Path-level selection:** apply Quaedvlieg's multi-horizon average-SPA/MCS
   comparison to the predeclared multivariate loss `D`. Hansen, Lunde, and
   Nason's MCS principle keeps a statistically superior set instead of forcing
   a spurious unique winner when the data cannot distinguish models.
3. **Deployment choice:** among the surviving path models, prefer stability
   across outer folds, then smaller worst-horizon regret, then lower complexity.
   This tie-break order is **judgmental** and must be frozen before results.

Report Quaedvlieg's uniform-SPA result as a robustness column: it asks whether
a candidate is superior across the whole path. Do not require significance at
every lead as an automatic gate; with weak signal that can discard useful path
models. Instead, flag horizons at which the candidate is clearly dominated and
let the predeclared loss and confidence set determine selection.

If no single architecture is adequate across all leads, a composite with a
different model at different leads is permissible only if those choices are
made inside each training/inner-validation fold. The completed composite must
then be rescored as one untouched outer-fold path candidate. Picking the best
outer result independently at every lead would leak model selection into the
reported evidence and multiply false discoveries.

For inference, resample whole trading sessions so contemporaneous dependence
among leads, overlapping outcomes, and intraday serial dependence stay
together. The exact resampling unit is **judgmental** for this data design;
Diebold and Mariano establish forecast comparison under serially and
contemporaneously correlated errors, while the MCS/multi-horizon procedures
provide the multiple-model framework.

**Recommendation:** amend P13 before execution so every enabled candidate is a
direct-head or true multi-output `1..H` path forecaster; preserve per-lead
evidence; select a path-level superior set with a frozen normalized loss; use
Horizon Confidence Sets as the diagnostic, not as permission to cherry-pick
one outer-fold winner per lead. This is a research recommendation and is **not
locked**.

## Sources

- Ben Taieb, S., Sorjamaa, A., and Bontempi, G. (2010), “Multiple-output
  modeling for multi-step-ahead time series forecasting,” *Neurocomputing*
  73(10–12), 1950–1957. https://doi.org/10.1016/j.neucom.2009.11.030
- Capistran, C. (2006), “On comparing multi-horizon forecasts,” *Economics
  Letters* 93(2), 176–181. https://doi.org/10.1016/j.econlet.2006.04.010
- Quaedvlieg, R. (2021), “Multi-Horizon Forecast Comparison,” *Journal of
  Business & Economic Statistics* 39(1), 40–53.
  https://doi.org/10.1080/07350015.2019.1620074
- Fosten, J. and Gutknecht, D. (2021), “Horizon confidence sets,” *Empirical
  Economics* 61, 667–692. https://doi.org/10.1007/s00181-020-01891-7
- Hansen, P. R., Lunde, A., and Nason, J. M. (2011), “The Model Confidence
  Set,” *Econometrica* 79(2), 453–497. https://doi.org/10.3982/ECTA5771
- Diebold, F. X. and Mariano, R. S. (1995), “Comparing Predictive Accuracy,”
  *Journal of Business & Economic Statistics* 13(3), 253–263.
  https://doi.org/10.1080/07350015.1995.10524599
- Tashman, L. J. (2000), “Out-of-sample tests of forecasting accuracy: an
  analysis and review,” *International Journal of Forecasting* 16(4), 437–450.
  https://doi.org/10.1016/S0169-2070(00)00065-0
- Gneiting, T. and Raftery, A. E. (2007), “Strictly Proper Scoring Rules,
  Prediction, and Estimation,” *JASA* 102(477), 359–378.
  https://doi.org/10.1198/016214506000001437
- Scheuerer, M. and Hamill, T. M. (2015), “Variogram-Based Proper Scoring Rules
  for Probabilistic Forecasts of Multivariate Quantities,” *Monthly Weather
  Review* 143(4), 1321–1334.
  https://doi.org/10.1175/MWR-D-14-00269.1
