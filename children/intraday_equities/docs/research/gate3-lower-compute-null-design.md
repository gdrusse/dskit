# Gate 3: lower-compute, statistically defensible null design

Date: 2026-09-04. Revised: 2026-09-05 after adversarial review.

## Conclusion

Gate 3 refits every Gate-1 survivor under 19 whole-session label
permutations (ADR-0089). On the recorded 13-survivor cohort that is
`13 x 19 = 247` asset-local 20-fold walks, and it grows with every asset.

**A pooled null reference was proposed and FAILED review.** What survives is
one exact statistical saving and two engineering ones. None of them changes
what Gate 3 answers.

| Change | Saving | Statistical cost |
| --- | --- | --- |
| Exact early stop on the beat-all rule | 247 -> ~62 walks (4.0x) | none — identical verdict |
| Filter the tape to the asset + SPY | ~12x less tape work per fold | none — implemented |
| Run a walk's folds concurrently | ~cores x | none — implemented |

## What Gate 3 asks

*Can the same fitting procedure produce this result when the features carry
no information about the labels?*

| Failure mode | Stored-score resample | Scramble refit |
| --- | --- | --- |
| Luck across the 25 assets | yes — shared session resample | no (see below) |
| Luck across horizons | free — fixed ordered stop spends no alpha | free |
| Variance estimator wrong | no | **yes** |
| Fold geometry or embargo broken | no | **yes** |
| Feature leakage | no | **no** |
| Tuning overfit | not applicable here | not applicable here |

Two rows decide everything.

**Neither test catches feature leakage.** A sign flip never sees a leak
already baked into a stored forecast. A label scramble breaks the
feature-label link but leaves an illegal future field in the features, so a
leaked result passes the scramble. Availability and lineage checks are the
only defence.

**Gate 3 carries no multiple-testing correction at all.** ADR-0088 removed
Gate 2 from selection and placed false-signal control in the MIO as a capital
constraint, `sum_i(x_i * pi_i) <= q * sum_i(x_i)` — explicitly not weighted
Benjamini-Hochberg, with no BH prefilter. A per-asset 0.05 across 13
survivors leaves about a 49% chance of at least one false pass. That is a
consequence of where the correction now lives, not of the refit.

"Tuning overfit" is not applicable because the P11 scan carries a single fixed
LightGBM configuration, and the node's optional HPO is an inner split of fold
TRAIN, never fold val (`nodes.py`). The walk-forward `objective`/`select` pair
only names the best fold for reporting (`driver.py`); it selects no model.
With a rolling 730-day training window and a five-day embargo, the stored
`d_t` are genuinely out of sample and the estimation error is already inside
them — the case Giacomini and White's finite-rolling-window framework covers.

## Why the pooled null fails

The proposal was: spend `B` scramble draws across the survivors rather than
`B` per survivor, pool them into one null reference, and rank every asset's
observed statistic against it. It rests on `t_pool` being **asset-invariant**
under the null. It is not.

**Studentising removes scale, not location.** Under the null a fitted model is
*worse* than the constant it is scored against, because it pays estimation
noise the constant does not. That cost is a per-row bias, so the null centre
sits near `-c * sqrt(n_eff) / sigma` — it grows with sample size and shrinks
with noise level, both asset-specific. This is measured, not hypothetical:
`nineteen-shuffled-walks-...md` records LLY's null centre at **-0.37** with
spread 0.98. No second asset's centre has ever been measured.

Pooling assets whose null centres differ shifts the pooled critical value.
Simulated over centres spanning -0.15 to -1.00, the pooled test runs at about
**1.8x nominal size for the least-fitting-cost asset** — and that is the
smoothest, most modelable-looking name, so the error is anti-conservative
exactly where it is most dangerous.

**The proposed guard cannot see the violation.** `tier2_verdict` tests
`mean < 0.3 and 0.7 < sd < 1.4` — one-sided on the mean, with no lower bound.
A pooled null centred at -0.5 passes it. Pooling also averages variances, so
the widest contributor is hidden rather than exposed.

**The per-asset guard is unexecutable at the budget that made pooling
attractive.** With `B = 19` spread over 13 survivors, most assets contribute
one draw; `statistics.stdev` needs two. Estimating per-asset moments well
enough to justify pooling needs roughly ten draws per asset — 130+ walks — at
which point the per-asset test is already paid for and the saving is gone.

**And the reductio.** The refit exists because `t_pool` is not trusted to be
N(0,1) on this asset's data. Pooling assumes every asset's `t_pool` shares one
distribution. If that held, no refit would be needed at all — the p-value
would be read off the normal, as Gate 2 already did. The proposal cannot both
need the refit and assume the invariance.

Pooling also mixes horizons by construction (survivors sat at h = 1, 2, 3, 5,
10 and 60), and horizons differ in label overlap on the 30-minute scoring
lattice, hence in HAC behaviour.

## What survives

### Exact early stop

`tier2_verdict` passes an asset only when `observed_r2 > max(nulls)`. That
verdict is decided the instant ONE null beats the observed; the remaining
draws cannot change it. Stopping there is exact, not approximate.

Under the null the observed is exchangeable with its own draws, so the
expected number of draws to the first exceedance is the harmonic number
`H_19 = 3.55` (simulated: 3.56). A cohort of twelve null assets and one true
survivor costs about `12 * 3.56 + 19 = 62` walks against today's 247.

The cost is the calibration record: an early-stopped asset has too few draws
for `null_mean` and `null_sd`. That check tests the variance estimator, which
is a property of the pipeline, so it belongs at full depth on one asset rather
than at depth 19 on thirteen. Changing what Gate 3 records supersedes
ADR-0089 and needs its own ADR first.

### Two engineering savings (implemented)

**The tape was never filtered.** An asset-local walk fits and scores one
symbol, but `_derived_document` handed the scan all 25 tapes, so
`_tapes_from_bars` masked and copied every symbol's full 1-minute history from
2018 on every one of the 4,940 fold processes. Only the asset and the tape its label
declares as the residual reference are read, so a `reference_tape` filter node
now keeps those two — the residual read from the document, never restated.

**Nothing ran in parallel.** `_run_bounded_walk` drove its 20 folds through a
blocking `subprocess.run`, one at a time, and dskit has no parallel execution
anywhere. Folds are independent by construction and now go through a bounded
pool. The 17 GiB address-space envelope is divided between concurrent folds,
so the total stays what one serial fold was already allowed, and a width of 1
is the historical path byte for byte. The width is read from the process
environment, never from the document: fold count is a property of the machine,
not of what the run computes, and a graded knob would move the identity hash
and orphan every prior run each time it was tuned.

## The cheap companion test

The multiplicity question needs no refit. `max_bar` in
`dskit/pipeline/attempts.py` implements it and P11 does not call it — though
it is not unexploited capability: `score_bar` already drove it from P10's
Gate 2, the machinery ADR-0088 removed from selection. Reinstating it as a
REPORTED view, not a filter, needs a declared family count (P11 ran 66 of 200
possible cells) and a producer stage building `{cell: {session: (sum, count)}}`,
which no child stage does today.

Two details are load-bearing:

- **Recentre the columns.** Subtracting each column's mean is what imposes the
  null. Without it the critical value is wrong.
- **Keep the session-cluster standard error.** It is invariant under the sign
  flip, which is what makes the bootstrap pivotal. A Newey-West denominator is
  not flip-invariant and breaks the procedure. Note this CORRECTS P8, which
  prescribes recomputing each cell "with the same Newey-West lag"; the shipped
  `max_bar` is right and P8's text is wrong.

## Calibration

Nineteen draws are a coarse smoke check. Under independent normal draws the
sample standard deviation's relative standard error at `B = 19` is about
`1/sqrt(2 * (19 - 1)) = 16.7%`, so the `0.7 < sd < 1.4` band is certified to
roughly one significant figure. The rule is also one-sided on the mean and so
cannot fail in the conservative direction, which `nineteen-shuffled-walks-...md`
already asked to change.

A claim about the gate's SIZE needs independent outer null panels. At
`alpha = 0.05`, 100 outer runs give about five rejections with an exact 95%
interval of 1.6%--11.3%; 400 give about 20 with 3.1%--7.6%. Periodic
pipeline-version validation, never a per-asset cost.

## Time-series qualification

A whole-session shuffle is not automatically an exact permutation test. Exact
randomisation inference needs sessions exchangeable under the stated null, and
daily dependence, volatility clustering and regime shifts can violate that.
Report a refit result as a test under its documented session-scramble null.

The session is the right unit: horizons run to 60 minutes, features never
bridge a tape gap, and the scoring lattice is 30 minutes, so moving a session
moves every overlapping label with it. Do not use row permutations — ordinary
permutation need not be level for dependent series (Romano and Tirlea). Drop
half-days from the donor pool. Note that ADR-0089's asset-local fits mean no
draw preserves cross-stock correlation, so P8's "share one map across stocks"
no longer applies as written.

## Standing caveat

The 13-survivor count comes from the P11 memo, which marks itself a superseded
execution record from a mistaken configuration; ADR-0089 confirms no Gate-3
run has been started. 247 is arithmetic on a number the active document has
not yet produced.

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
- Romano, J. P. and Tirlea, M. A. (2020), *Permutation Testing for Dependence
  in Time Series*. <https://arxiv.org/abs/2009.03170>

## Related intraday-equities documents

- `docs/architecture/decision-log.md` ADR-0089 — the active Gate-1-to-Gate-3
  audit; ADR-0088 — HFDR in MIO.
- `docs/research/nineteen-shuffled-walks-lillys-three-minutes-is-not-luck-and-the-error-bars-are-sound.md`
  — the only measured null centre and spread.
- `docs/research/p8-bar-a-bootstrap-max-over-every-attempt-plus-a-day-block-scramble.md`
  — the companion test's design.
- `docs/memos/p11-modelability-pipeline.md` — the asset-local execution record
  and the 13-survivor count.
