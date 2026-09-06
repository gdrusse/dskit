## Proposed decision

Estimate uncertainty in the conditional mean separately from realized-return
noise. For every live candidate emit `mu_hat`, a lower/upper confidence or
credible interval for net alpha, the calibration-window identity, model hash,
and method. The interval is built only from out-of-fold errors and refit
variation; it must include feature selection, HPO, and seed/refit uncertainty
that the production procedure actually incurs.

Use whole-session block or stationary bootstrap resamples so serial dependence
and same-session cross-sectional dependence are not destroyed. Report empirical
coverage and width on a later, untouched calibration segment.

## Research backing

The stationary bootstrap was designed to construct standard errors and
confidence regions for weakly dependent stationary observations:

- Politis and Romano (1994), *The Stationary Bootstrap*:
  https://doi.org/10.1080/01621459.1994.10476870
- Politis and White (2004), *Automatic Block-Length Selection for the Dependent
  Bootstrap*: https://doi.org/10.1111/j.1467-9892.2004.00387.x

**Decision criterion: empirical.** Coverage, stability, and block length are
measurable; the interval is not accepted until its prospective calibration
passes.
