# Gate 3: lower-compute, statistically defensible null design

Date: 2026-09-04. Revised: 2026-09-05.

## Conclusion

A research recommendation, not an execution record and not a change to the
modelability rule.

Gate 3 today refits every Gate-1 survivor under 19 whole-session label
permutations (ADR-0089). Each survivor gets its own 19 refits, so the
recorded 13-survivor cohort costs `13 x 19 = 247` asset-local 20-fold walks,
and the cost grows with every asset added.

**The recommendation is to pool the null refits across assets.** Under the
null the statistic's distribution is a property of the fitting procedure and
the fold geometry, not of the ticker. Spread `B` scramble draws across the
survivors, pool them into ONE null reference distribution, and rank every
asset's observed statistic against it. The test is unchanged — same refit,
same whole-session scramble, same procedure under the null — but `B` is a
budget for the cohort rather than a cost per asset.

At today's resolution that is 19 walks instead of 247. At `B = 100` it is 100
walks, still cheaper than now, with five times the resolution.

## What Gate 3 asks

*Can the same fitting procedure produce this result when the features carry
no information about the labels?*

Six things could manufacture a Gate-1 pass. What actually catches each:

| Failure mode | Stored-score resample | Scramble refit |
| --- | --- | --- |
| Luck across the 25 assets | yes — shared session resample | yes, at resolution `1/(1+B)` |
| Luck across horizons | free — fixed ordered stop spends no alpha | free |
| Variance estimator wrong | no | **yes** |
| Fold geometry or embargo broken | no | **yes** |
| Feature leakage | no | **no** |
| Tuning overfit | not applicable here | not applicable here |

Two entries decide the design.

**The refit uniquely buys the last two rows it can reach — a variance-estimator
check and a fold-geometry check — and both are properties of the PIPELINE, not
of the asset.** Running them once per survivor tests the same thing thirteen
times. That is the redundancy the pooled null removes.

**Neither test catches feature leakage.** A sign flip never sees a leak already
baked into a stored forecast. A label scramble breaks the feature-label link
but leaves an illegal future field in the features, so a leaked result passes
the scramble. Availability and lineage checks are the only defence.

"Tuning overfit" is not applicable because the P11 scan carries a single fixed
LightGBM configuration, and the node's optional HPO is an inner split of fold
TRAIN, never fold val (`nodes.py`). The walk-forward `objective`/`select` pair
only names the best fold for reporting; it selects no model. With a rolling
730-day training window and a five-day embargo, the stored `d_t` are therefore
genuinely out of sample, and the estimation error is already inside them —
Giacomini and White's framework covers exactly this case and requires the
finite rolling window we use.

## The pooled null

Each scramble draw runs the identical preparation and scoring path with whole
regular-trading sessions donating each other's labels. Nothing changes in how
one draw is produced. What changes is how the draws are spent.

Instead of `B` draws per survivor, take `B` draws total, allocated across the
survivors, and pool them. Asset `i` passes when its observed statistic ranks
above the pooled null at the declared level:

```text
p_i = (1 + number of pooled null statistics >= observed_i) / (1 + B).
```

The plus-one correction avoids reporting zero from a finite simulation
(Phipson and Smyth).

### The condition, and how it is checked

Pooling is valid when the null statistic is **asset-invariant** — a draw from
LLY and a draw from XLP come from the same distribution.

Use `t_pool = mean(g_t) / HAC_SE(mean(g_t))` as the pooled statistic, not
`r2oos`. Dividing by its own standard error removes the asset's volatility,
sample size and noise level, which is what makes the draws exchangeable.
`tier2_verdict` already computes both; today the beat-all test uses `r2oos`
and only the calibration check uses `t`.

Invariance can fail if session counts or autocorrelation differ sharply
between assets, or if the HAC standard error is biased differently for one of
them. Two guards, both free:

- The pooled null must sit near mean 0 and sd 1. This is the check
  `tier2_verdict` already performs, now on `B` draws instead of 19.
- Report each asset's own null moments alongside the pool. A contributor whose
  draws sit visibly wider is excluded before pooling, so one contaminated
  asset cannot raise the bar for the rest.

### Output

One row per asset — observed `t_pool`, its rank against the pooled null, `p_i`,
and the decision — plus ONE shared block recording the pooled null: `B`, the
contributing assets, `null_mean`, `null_sd`, `calibrated`, and the per-asset
moments. That replaces today's binary "beat all 19", which is pinned at exactly
`1/20 = 0.05` and cannot resolve any finer no matter how strong the result.

## The cheap companion test

The multiplicity question does not need a refit at all. `max_bar` in
`dskit/pipeline/attempts.py` already implements it and P11 does not call it:
build one matrix of per-timestamp loss differentials over every candidate,
recentre each column so the null holds exactly, draw one `+/-1` coin per
session shared by every column and every stock, recompute each studentised
statistic, and take the maximum. Stepdown by dropping rejected cells
(Romano and Wolf).

Two details are load-bearing and easy to get wrong:

- **Recentre the columns.** Subtracting each column's mean is what imposes the
  null. Without it the critical value is wrong.
- **Keep the session-cluster standard error.** It is invariant under the sign
  flip, which is what makes the bootstrap pivotal, and it is at least as
  conservative as a Bartlett band truncated at the label's overlap depth. A
  Newey-West denominator is NOT flip-invariant and breaks the procedure — even
  though Newey-West is the right choice for the reported statistic itself.

`B = 10,000` is arithmetic on stored numbers; 2,000 is a floor. This costs no
walks and can run every night.

## Calibration is a pipeline property, not a per-asset cost

The null statistic's centre and spread test the variance estimator itself. A
flexible model can sit slightly below zero against a constant benchmark
because fitting noise costs performance; an unexpectedly positive centre is
the dangerous direction.

Nineteen draws are a coarse smoke check. Under independent normal draws the
sample standard deviation's relative standard error at `B = 19` is about
`1/sqrt(2 * (19 - 1)) = 16.7%`, so the current `0.7 < sd < 1.4` band is being
certified to roughly one significant figure. Pooling raises `B` at constant
cost, which is the direct fix.

A claim about the gate's SIZE is a different and larger exercise: it needs
independent outer null panels — simulated null instances or non-overlapping
historical anchors put through the whole pipeline. At `alpha = 0.05`, 100
outer runs give about five expected rejections with an exact 95% interval of
1.6%--11.3%; 400 give about 20 with 3.1%--7.6%. That is a periodic
pipeline-version validation, never a per-asset Gate-3 cost.

## Compute

Features are already cached, so a scramble draw is 20 folds of LightGBM on one
asset. The count of draws is the whole cost.

| Design | Walks | Resolution |
| --- | --- | --- |
| Today: 19 seeds per survivor, 13 survivors | 247 | `p = 0.05` floor |
| Pooled, `B = 19` | 19 | `p = 0.05` floor |
| Pooled, `B = 100` | 100 | `p = 0.01` floor |

The important property is not the ratio but the scaling: `B` is a cohort
budget, so a larger cohort costs nothing extra. This is not the final set of
stocks, which is exactly why per-asset scaling is the thing to remove.

One saving is already spent and should not be double-counted. P10's pooled
architecture shares a single scramble map across every survivor at one horizon
(`modelability.py`), so one refit yields every asset's null statistic there.
ADR-0089 made the active gate asset-local (`modelability_p11.py`), one fit per
asset per seed, so that saving is not available and pooling the DRAWS is the
replacement for it.

## Time-series qualification

A whole-session shuffle is not automatically an exact permutation test. Exact
randomisation inference needs sessions to be exchangeable under the stated
null, and daily dependence, volatility clustering and regime shifts can
violate that. Report a refit result as a test under its documented
session-scramble null, not as an unconditional exact p-value.

The session is the right unit here: horizons run to 60 minutes, features never
bridge a tape gap, and the scoring lattice is 30 minutes, so moving a session
moves every overlapping label with it. Do not use row permutations — they
destroy the overlap and the autocorrelation, and ordinary permutation need not
be level for dependent series (Romano and Tirlea). Drop half-days from the
donor pool; session lengths must match. Keep the training and validation
permutation maps independent, and share one map across stocks.

For the stored-score test, cross-check the session sign-flip against a
recentred stationary or circular block bootstrap and take the more
conservative critical value if they disagree.

## Where the family correction lives

Gate 3 carries no multiple-testing correction. ADR-0088 removed Gate 2 from
stock selection and placed false-signal control in the MIO as a capital
constraint, `sum_i(x_i * pi_i) <= q * sum_i(x_i)`, which is explicitly not
weighted Benjamini-Hochberg and runs no BH prefilter. Nothing here changes
that; the pooled null reports `p_i` at the level Gate 3 declares, and the
`max_bar` companion reports the family-adjusted view separately.

## Proposed future Gate 3 rule

An asset passes only if it has (1) a positive predeclared forward statistic,
(2) passing availability and negative controls, (3) `p_i` at or below the
declared level against a pooled null whose calibration record passes, and (4)
per-asset null moments consistent with the pool. Report the effect size and a
dependence-aware interval alongside any pass; a small p-value alone is not a
trading claim.

## Sources

- Gandy, A. (2009), *Sequential Implementation of Monte Carlo Tests With
  Uniformly Bounded Resampling Risk*, JASA 104, 1504--1511.
  <https://www.tandfonline.com/doi/abs/10.1198/jasa.2009.tm08368>
- Phipson, B. and Smyth, G. K. (2010), *Permutation P-values Should Never Be
  Zero*. <https://gksmyth.github.io/pubs/PermPValuesPreprint.pdf>
- Giacomini, R. and White, H. (2006), *Tests of Conditional Predictive
  Ability*, Econometrica 74, 1545--1578.
  <https://www.eco.uc3m.es/~jgonzalo/teaching/PhdTimeSeries/GiacominiWhite.pdf>
- White, H. (2000), *A Reality Check for Data Snooping*, Econometrica 68,
  1097--1126. <https://users.ssc.wisc.edu/~behansen/718/White2000.pdf>
- Romano, J. P. and Wolf, M. (2005), *Stepwise Multiple Testing as Formalized
  Data Snooping*, Econometrica 73, 1237--1282.
  <https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1468-0262.2005.00615.x>
- Shao, X. (2010), *The Dependent Wild Bootstrap*, JASA 105, 218--235.
  <https://www.tandfonline.com/doi/abs/10.1198/jasa.2009.tm08744>
- Politis, D. N. and Romano, J. P. (1994), *The Stationary Bootstrap*, JASA
  89, 1303--1313. <https://www.tandfonline.com/doi/abs/10.1080/01621459.1994.10476870>
- Theiler, J., Eubank, S., Longtin, A., Galdrikian, B. and Farmer, J. D.
  (1992), *Testing for Nonlinearity in Time Series: The Method of Surrogate
  Data*, Physica D 58, 77--94.
  <https://digital.library.unt.edu/ark:/67531/metadc1094730/>
- Romano, J. P. and Tirlea, M. A. (2020), *Permutation Testing for Dependence
  in Time Series*. <https://arxiv.org/abs/2009.03170>

## Related intraday-equities documents

- `docs/architecture/decision-log.md` ADR-0089 — the active Gate-1-to-Gate-3
  audit this document proposes to change; ADR-0088 — HFDR in MIO.
- `docs/research/p8-bar-a-bootstrap-max-over-every-attempt-plus-a-day-block-scramble.md`
  — the Tier-1/Tier-2 design the companion test comes from.
- `docs/explanations/shuffle-and-retrain-test.md` — refit-audit explanation.
- `docs/memos/p11-modelability-pipeline.md` — the asset-local execution record
  and the 13-survivor count; `docs/memos/p10-modelability-pipeline.md` — the
  superseded pooled record.
