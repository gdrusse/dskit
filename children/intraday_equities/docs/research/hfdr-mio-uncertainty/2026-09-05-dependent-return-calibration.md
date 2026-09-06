## Proposed decision

Estimate realized-return uncertainty as a predictive interval or joint
scenario distribution, never as the confidence interval for expected alpha.
Calibrate from time-ordered out-of-fold residuals using whole-session blocks,
preserving the simultaneous cross-asset vector and horizon semantics. Emit
coverage, tail-loss diagnostics, calibration hash, and the complete scenario
provenance.

Ordinary IID split conformal is not assumed valid here. Use a dependence-aware
randomization/block construction and evaluate rolling conditional coverage,
especially through volatility regimes.

## Research backing

Chernozhukov, Wüthrich, and Zhu extend conformal inference to dependent data
using block-structured randomization and give approximate validity under weak
dependence:

- Chernozhukov, Wüthrich, and Zhu (2018), *Exact and Robust Conformal Inference
  Methods for Predictive Machine Learning With Dependent Data*:
  https://proceedings.mlr.press/v75/chernozhukov18a.html
- Angelopoulos et al. (2024), *Conformal Risk Control*:
  https://arxiv.org/abs/2208.02814

**Decision criterion: empirical.** Coverage and tail calibration are observable
on prospective time blocks and must be validated before use.
