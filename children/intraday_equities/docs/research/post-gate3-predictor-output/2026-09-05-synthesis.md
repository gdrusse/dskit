# Post-Gate-3 predictor output (shared models, uncertainty, robust sets)

Date: 2026-09-05

## Question

After Gate 3, how should `intraday_equities` structure the predictor:
shared backbone vs per-name models, ensembles, foundation models,
tabular features vs long lags; and what should it emit besides a point
forecast so a robust optimizer can consume calibrated uncertainty?

## Finding

**Do not default to a large transformer or a point estimate.** The
evidence favors a small pooled trunk, direct per-name heads at each
name’s selected horizon \(H_i\), equal-weight seed ensembles of a
validated small model, tabular features as incumbent, and a
quantile-plus-scenario uncertainty product whose **set geometry is
calibrated on out-of-sample joint residuals**, not on in-sample
confidence intervals.

### Output record

At time \(t\), emit one direct terminal forecast per eligible name at
that name’s \(H_i\) (not a shared horizon, not a full path). Attach a
non-crossing quantile grid, conformal interval corrections, and a
pointer to a synchronized residual-scenario bank. Downstream Pyomo
robust/DRO code needs **joint forecast-error** at the decision horizon,
including dependence. Marginal intervals are not a simultaneous region
and are not a covariance.

A prediction interval for the next *realized* return is not a
confidence set for *expected* alpha. Using the former as an
uncertainty set for \(\mu\) is usually far too conservative.

### Architecture (see `2026-09-05-shared-heads.md`)

Caruana (1997), Gu–Kelly–Xiu (2020), and Sirignano–Cont (2019) support
**pooling**, not depth. P7 already showed untuned large nets overshoot
a ~30-parameter budget on this SNR. Primary candidate: compact shared
trunk + lightweight \((name, H_i)\) heads, with independent
ridge/tree fallbacks per name and a leave-names-out check for negative
transfer (Yu et al. PCGrad). Direct multi-step targets match
heterogeneous Gate-3 horizons (Marcellino–Stock–Watson: direct is more
robust to misspecification even if iteration can win when the one-step
model is true). TFT/PatchTST/iTransformer and Chronos/TimesFM/Moirai
are **bounded challengers** (frozen or adapter-tuned first), not the
default.

### Inputs (see `2026-09-05-input-representation.md`)

Keep the engineered tabular set and ridge/LightGBM as incumbent
(Grinsztajn et al.; Gu–Kelly–Xiu). Sequence and hybrid models are
separate, equal-protocol challengers on stationary channels (returns,
ranges, volume changes), not raw prices. \(H\) is the label; \(L\) is
decision cadence — do not coarsen the one-minute path just because \(H\)
is long.

### Ensembles (see `2026-09-05-ensembles.md`)

Equal-weight **5 seeds** of the same small winner (Lakshminarayanan;
Gu–Kelly–Xiu used 10). Gains collapse when member residuals are highly
correlated. Stacking and snapshot ensembles add estimation risk or
fake diversity (Smith–Wallis; Fort–Hu–Lakshminarayanan). Ensemble
spread is not calibrated coverage.

### Uncertainty (see `2026-09-05-uncertainty-output.md`)

Pinball quantile heads + rolling, volatility-scaled CQR / adaptive
conformal (Romano et al.; Xu–Xie EnbPI; Barber et al. beyond
exchangeability). Score with proper rules (Gneiting–Raftery), not
coverage alone. MC dropout / Gaussian NGBoost / raw residuals are not
“calibrated” on fat-tailed, diurnal, regime-shifting returns.

### Robust sets (see `2026-09-05-robust-optimization-sets.md`)

Estimate sets from purged walk-forward residual vectors \(e_t=r_t-\hat\mu_t\).
Practical default: volatility-normalized residuals; shrinkage/factor
ellipsoid if a conic solver is acceptable; Bertsimas–Sim budgeted
polyhedron if order selection stays LP/MILP. Calibrate \(\Gamma\) /
radius / Wasserstein \(\varepsilon\) on **decision** metrics
(utility, turnover, no-trade rate, constraint violations), not only
set coverage (Ben-Tal–Nemirovski; Delage–Ye; Esfahani–Kuhn).
Over-wide boxes empty the book; under-calibrated intervals let the
optimizer treat noise as alpha.

### What not to do first

- Train or fully fine-tune a large transformer as the production mean.
- Emit only \(\hat\mu\).
- Feed model CI half-widths straight into a box around expected return.
- Reuse Gate 1–2 features as a constraint on later sequence models —
  they are an incumbent, not a ceiling.

## Sources

Primary citations live in the five sibling notes in this folder. Key
anchors: Caruana 1997; Gu–Kelly–Xiu 2020 RFS; Sirignano–Cont 2019;
Marcellino–Stock–Watson 2006; Grinsztajn et al. 2022; Romano–Patterson–Candès
CQR 2019; Bertsimas–Sim 2004; Esfahani–Kuhn 2018; Lakshminarayanan et
al. 2017; Gneiting–Raftery 2007.
