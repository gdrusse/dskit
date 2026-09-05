## Proposed decision

Estimate each live signal's false probability as a local false-discovery rate,
`pi_i = P(H_i = 0 | z_i, c_i)`, where the null means net conditional alpha is
not positive after costs. `z_i` must come from strictly out-of-fold predictive
evidence and Gate-3 session-scramble refits; `c_i` may contain prospectively
declared model, horizon, liquidity, and regime covariates.

Fit a two-groups empirical-Bayes model with an empirical null derived from the
scramble statistics. Cross-fit the calibrator and assess reliability on a
time-separated calibration sample. A raw p-value, fold pass rate, classifier
confidence, or predictive interval is not `pi_i`.

## Research backing

Efron et al. define local FDR as the posterior probability that an observation
belongs to the null component and demonstrate empirical-null estimation,
including permutation-based null samples:

- Efron et al. (2001), *Empirical Bayes Analysis of a Microarray Experiment*:
  https://genomics.princeton.edu/storeylab/papers/ETST_JASA_2001.pdf
- Efron (2004), *Large-Scale Simultaneous Hypothesis Testing: The Choice of a
  Null Hypothesis*: https://doi.org/10.1198/016214504000000089
- Efron (2007), *Size, Power and False Discovery Rates*:
  https://arxiv.org/abs/0710.2245

**Decision criterion: empirical.** The method is established; its calibration
and usefulness for this signal population must be demonstrated prospectively.
