# Predictive program calendar and validation protocol

## Question

Which dates and validation design should govern Gate 1/Gate 3 modelability,
the model zoo, finalist tuning, final fitting, uncertainty calibration, and the
untouched end-to-end simulation?

## Finding

Use one machine-readable calendar: `configs/program-calendar.json`. Gate 1,
Gate 3, and model-zoo selection share the same 20 non-overlapping rolling-origin
outer validation blocks: first cutoff 2022-05-06, 63-day step, 63-day validation,
730-day rolling train, five-day embargo. The last block is
`[2025-08-15, 2025-10-17)`, so development validation ends 2025-10-16.

The shared folds make model comparisons paired and temporally honest, but their
reuse is development selection evidence, not independent confirmation. Feature
selection and hyperparameter tuning must occur inside training data for each
outer fold. Candidate families may use individualized preprocessing, selectors,
and search spaces, while the target, scored instants, outer folds, costs, and
primary score remain fixed.

After architecture selection, finalist-only HPO fits through 2025-11-30,
embargoes the full 2025-12-01 session, and validates from 2025-12-02 through
2026-02-28. That block is spent by selection. The winner is then refit through
2026-02-28 without reopening architecture, feature, objective, or search-space
choices.

The frozen conditional-mean model is confirmed from 2026-03-01 through
2026-05-31; its genuinely out-of-sample residual vectors then calibrate the
uncertainty outputs. Those same observations do not independently validate the
newly calibrated uncertainty product. The first untouched full-system
simulation is 2026-06-01 through 2026-08-31, including Test B during August,
with mean model, uncertainty, Gate 2 capital-weighted false-positive control,
MIO, costs, and execution rules all frozen before 2026-06-01. Production is not
eligible before 2026-09-01 and owner approval.

## Alignment audit

- `configs/run-p12-modelability.json` (Gate 1 and Gate 3) declares the exact
  development outer-fold schedule above.
- Every enabled candidate materialized by `configs/run-p13-model-zoo.json`
  receives the calendar's exact fold schedule. `ProgramCalendar` pins the
  calendar digest and phase, and `BenchmarkPlan` refuses walk-forward drift.
- P13 now expands 13 enabled architecture templates across the exact 25
  A18622-approved Gate-3 asset/horizon pairs, including horizons 1/2/3/5/10.
  It pins the P12 source document, Gate-3 result, and five-group cache artifact.
  An inventory-hash approval stage makes the first invocation plan-only.
- P12 Gate 3 completed under A18622: 25 asset-horizon pairs passed and six
  failed. The official result artifact is
  `pipeline_runs/p12-g3-recovery-staged-2026-02-28-a1f293a2/stages/gate3_recovery.json`
  with SHA-256
  `098b21eaef6ee0260753d4f981ca2337bccae406b9efd394284d9b180ba03bd0`.

## Research-backed versus judgemental

Research-backed:

- Model selection and feature/HPO search must be nested inside outer evaluation;
  otherwise selection itself overfits the evaluation data (Cawley & Talbot;
  Varma & Simon).
- Temporal order must be preserved and performance estimated on future blocks;
  dependence and nonstationarity make ordinary random splits unsafe unless their
  assumptions are established (Cerqueira et al.; Bergmeir et al.; Racine).
- Repeated model comparisons require explicit data-snooping control and paired
  predictive comparison (White; Hansen; Diebold-Mariano; Giacomini-White).
- Backtest selection inflates reported performance, so the end-to-end simulator
  must remain untouched until the full bundle is frozen (Bailey et al.).

Judgemental but predeclared:

- Exactly 20 folds, 63 calendar days per validation block, 730 calendar days of
  training, and five embargo days. These are defensible engineering choices for
  this sample and maximum 60-minute horizon, not universal constants.
- The exact finalist-HPO, calibration, and simulation boundary dates. Their
  separation is methodologically required; the particular boundaries reflect
  the available data cut and operational calendar.
- Using the same March-May rows first for mean confirmation and then for
  uncertainty calibration. This conserves scarce recent data, but it forbids an
  independent uncertainty-coverage claim until June-August.

## Sources

1. Cawley, G. C., & Talbot, N. L. C. (2010), *On Over-fitting in Model
   Selection and Subsequent Selection Bias in Performance Evaluation*, JMLR.
   https://www.jmlr.org/papers/volume11/cawley10a/cawley10a.pdf
2. Varma, S., & Simon, R. (2006), *Bias in error estimation when using
   cross-validation for model selection*, BMC Bioinformatics.
   https://link.springer.com/article/10.1186/1471-2105-7-91
3. Cerqueira, V., Torgo, L., & Mozetic, I. (2020), *Evaluating time series
   forecasting models: an empirical study on performance estimation methods*,
   Machine Learning. https://arxiv.org/abs/1905.11744
4. Bergmeir, C., Hyndman, R. J., & Koo, B. (2018), *A note on the validity of
   cross-validation for evaluating autoregressive time series prediction*,
   Computational Statistics & Data Analysis.
   https://robjhyndman.com/publications/cv-time-series/
5. Racine, J. (2000), *Consistent cross-validatory model-selection for
   dependent data: hv-block cross-validation*, Journal of Econometrics.
   https://doi.org/10.1016/S0304-4076(00)00030-0
6. White, H. (2000), *A Reality Check for Data Snooping*, Econometrica.
   https://www.ssc.wisc.edu/~bhansen/718/White2000.pdf
7. Hansen, P. R. (2005), *A Test for Superior Predictive Ability*, Journal of
   Business & Economic Statistics.
   https://www.tandfonline.com/doi/abs/10.1198/073500105000000063
8. Diebold, F. X., & Mariano, R. S. (1995), *Comparing Predictive Accuracy*,
   Journal of Business & Economic Statistics.
   https://doi.org/10.1080/07350015.1995.10524599
9. Giacomini, R., & White, H. (2006), *Tests of Conditional Predictive
   Ability*, Econometrica. https://doi.org/10.1111/j.1468-0262.2006.00718.x
10. Bailey, D. H., Borwein, J. M., Lopez de Prado, M., & Zhu, Q. J. (2014),
    *Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest
    Overfitting on Out-of-Sample Performance*.
    https://carmamaths.org/resources/jon/backtest2.pdf
