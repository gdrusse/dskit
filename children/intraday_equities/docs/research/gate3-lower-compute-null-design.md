# Gate 3: lower-compute, statistically defensible null design

Date: 2026-09-04.

## Conclusion

This is a research recommendation, not an execution record or a change to the
modelability rule. The statistically defensible lower-compute design is a
**two-tier test**:

1. Make the routine, global test from genuinely out-of-sample stored loss or
   prediction contributions. Resample them in shared session blocks, with no
   model refit.
2. Keep whole-pipeline session-label scrambles for the few survivors and for
   material pipeline changes. Use sequential Monte Carlo stopping rather than a
   fixed seed count.
3. Test availability-lineage controls and null-statistic calibration separately.
   Neither an untouched holdout nor a clean scramble proves no feature leakage.

This agrees with the existing P8 structure: its Tier 1 is a cheap,
session-shared test over stored loss differentials, and Tier 2 refits
session-scrambled labels for winners only. The proposed refinement is to state
the scope of each tier precisely and use a resampling-risk-bounded sequential
rule for Tier 2.

## What each layer establishes

| Evidence layer | Valid claim | Does not establish |
| --- | --- | --- |
| Frozen walk-forward / lockbox predictions | The declared procedure has forward association or loss advantage on outcomes it did not influence. | Stability in a new regime or absence of leakage present in both training and scoring. |
| Stored-score session bootstrap or dependent-data test | Uncertainty and selection multiplicity for the fixed prediction panel. | The null distribution of adaptive fitting and tuning. |
| Full pipeline, session-scrambled label refit | The same fitting path cannot easily manufacture the observed result when the input-label link is broken under the stated null. | An exact time-series p-value without exchangeable sessions, or universal proof of no leakage. |
| Availability assertions and negative controls | Known timestamp, join, and target-leakage routes are blocked. | Economic capacity or future-regime robustness. |

The refit audit therefore remains important: rescoring a stored model cannot
reveal an artefact already embedded in its predictions. It should simply be
spent where that additional protection is valuable.

## Proposed operating design

### Preserve the information boundary

At every walk origin, fit transforms, missing-value rules, universe membership,
feature selection, tuning, and candidate choice using only information then
available. Record source lineage and availability timestamps. Use an embargo at
least as long as label overlap and execution exposure.

For intraday equities, retain the complete regular-trading session as the
scramble unit. With horizons at most 60 minutes, this preserves within-session
autocorrelation, overlapping labels, time-of-day effects, and the cross-stock
vector. Training and validation maps should remain independent; one map should
be shared across stocks.

### Tier 1: score-level, dependence-aware global inference

Each origin stores a predeclared contribution such as

```text
d_t = (y_t - benchmark_t)^2 - (y_t - model_t)^2.
```

Positive `d_t` favours the model. Stack every candidate's `d_t` on common
timestamps, retain session IDs, and calculate the reported mean advantage with
the same HAC rule used in the final statistic. Obtain a global maximum or
stepdown critical value from one shared session-block resample of the whole
matrix. The shared resample is essential: it retains cross-candidate and
cross-stock dependence and calibrates the selection maximum, not unrelated
single-cell p-values.

The P8 session-sign wild bootstrap and stationary/circular-block cross-check
already have this form. Use a substantial count because these draws are
arithmetic on stored numbers: 2,000 is a reasonable floor and 10,000 gives
useful tail resolution. A forecast-evaluation test on the walk-forward panel is
also academically standard: Giacomini and White's framework is explicitly for
out-of-sample predictive ability while retaining estimation uncertainty. Keep a
final untouched tail for confirmation; rolling origins improve precision but do
not license reuse of that final tail.

### Tier 2: refit only survivors, with sequential Monte Carlo

For a Tier-1 survivor, rerun the identical preparation, tuning, and scoring
path after session-level label scrambling. Before starting, fix:

- the decision threshold `alpha`, including the family adjustment;
- a resampling-risk bound `epsilon` (for example, `0.001`); and
- a maximum operational budget. A case that remains near the boundary at that
  budget is `inconclusive`, not an optionally stopped pass.

Run scrambles in small batches. Gandy's sequential Monte Carlo test can stop
when it has decided whether the ideal infinite-resampling p-value lies on the
required side of `alpha`, with the probability that simulation stopping changes
that decision bounded by `epsilon`. A rule that stops when a running proportion
looks favourable has no equivalent guarantee.

If reporting a numerical p-value rather than a decision, use a fixed final
budget and the plus-one correction:

```text
p_MC = (1 + number of null statistics >= observed statistic) / (1 + B).
```

This avoids reporting zero from a finite simulation. In the pooled 25-asset
fit, one full scrambled refit can generate every survivor's statistic using one
shared scramble map. That both cuts compute and retains the joint variation
needed for a family decision.

## Calibration is separate from the observed-test p-value

Check the null statistic's centre and spread. A flexible fitted model can have
a slightly negative mean loss advantage against a constant benchmark because
fitting noise costs performance; an unexpectedly positive centre is the
dangerous direction. This diagnostic verifies the standard-error and null
construction, not just the rank of the observed statistic.

Nineteen refits are a coarse smoke check. When none exceeds the observed value,
the smallest plus-one p-value is `1/20 = 0.05`. Under independent normal null
draws, the sample SD's relative standard error at `B=19` is about
`1/sqrt(2*(19-1)) = 16.7%`, so it cannot precisely certify a narrow spread.

A claim about test size requires independent outer null panels—simulated null
instances or multiple non-overlapping historical anchors processed by the full
pipeline. At `alpha=0.05`, 100 outer runs have about five expected rejections
and a wide approximate 95% interval of 1.6%--11.3%; 400 have about 20 and an
interval of 3.2%--7.7%. Make this a periodic pipeline-version validation, not a
per-asset Gate 3 cost.

## Why it is faster

Let `C_fit` be one complete fitting walk, `B` full null refits, and `K` rolling
origins used to create frozen predictions.

- Repeated full-refit null testing costs about `B * C_fit` after the real run.
- Fixed-score inference costs about `K * C_fit`, then inexpensive resampling of
  the stored `N` score rows and `M` candidates.
- With `B=500` and `K=5`, the number of expensive fits is about 100 times
  lower. That is a compute comparison, not a promised 100-fold wall-clock
  speed-up: cache reuse, loading, and parallelism determine elapsed time.
- Sequential Tier-2 refits reduce expected cost when a p-value is clearly on
  one side of the threshold. They do not guarantee a saving near `alpha`; a
  hard case should consume more evidence.

P8 already captures the major saving: many cheap global score resamples over
all attempts, then expensive refits only for winners. This recommendation does
not remove Tier 2.

## Time-series qualification

A whole-session shuffle is not automatically an exact permutation test. Exact
randomisation inference requires sessions to be exchangeable under the stated
null. Session scrambling preserves the important intra-session structure, but
daily dependence, volatility clustering, and regimes can still violate that
condition.

Therefore, call a refit result a test under its documented session-scramble
null, not an unconditional exact p-value. Use session blocks or stationary
bootstrap for the routine stored-score test, sensitivity-check plausible block
lengths, and use the more conservative P8 critical value if the sign and block
bootstrap disagree. If nonstationarity is material, restrict donors by a
predeclared calendar/regime stratum or use multi-session blocks; this increases
null realism but reduces the effective donor pool and power.

Do not use row permutations. They destroy overlapping labels and temporal
dependence. Romano and Tirlea show that ordinary permutation need not be level
for dependent time series. Surrogate tests likewise require a clearly stated
null and a surrogate that retains the structure not under test.

## Leakage controls required alongside the tests

Run deterministic negative controls: impossible feature lags, deliberate input
delays, label-time offsets, and train/validation assertions that every learned
transform was fit only on permitted history. A final holdout can score well when
an illegal future field is present in both training and scoring; a label
scramble can only test the particular data path it changes. Availability and
lineage checks are the direct defence.

## Proposed future Gate 3 rule

An asset passes only if it has (1) a positive predeclared forward statistic
clearing the shared dependence-aware family correction, (2) passing availability
and negative controls, (3) a survivor refit audit decided by a sequential,
resampling-risk-bounded rule, and (4) an adequate calibration record for the
pipeline version. Report effect size and a dependence-aware interval alongside
any pass; never treat a small p-value by itself as a trading claim.

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
- Politis, D. N. and Romano, J. P. (1994), *The Stationary Bootstrap*, JASA
  89, 1303--1313. <https://www.tandfonline.com/doi/abs/10.1080/01621459.1994.10476870>
- Theiler, J., Eubank, S., Longtin, A., Galdrikian, B. and Farmer, J. D.
  (1992), *Testing for Nonlinearity in Time Series: The Method of Surrogate
  Data*, Physica D 58, 77--94.
  <https://digital.library.unt.edu/ark:/67531/metadc1094730/>
- Romano, J. P. and Tirlea, M. A. (2020), *Permutation Testing for Dependence
  in Time Series*. <https://arxiv.org/abs/2009.03170>

## Related intraday-equities documents

- `docs/research/p8-bar-a-bootstrap-max-over-every-attempt-plus-a-day-block-scramble.md`
  — current Tier-1/Tier-2 design.
- `docs/explanations/shuffle-and-retrain-test.md` — refit-audit explanation.
- `docs/memos/p10-modelability-pipeline.md` — pooled-model execution record.
