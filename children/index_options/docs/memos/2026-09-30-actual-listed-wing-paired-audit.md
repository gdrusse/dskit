# Actual-listed-wing paired audit

## Decision

Do not claim established decision-region predictive power and do not tune the
optimizer yet. On a much larger paired actual-chain panel, option transport had
positive average wing-score skill, driven almost entirely by QQQ, but both
30- and 60-date intervals crossed zero. The governed conservative blend was
closer to empirical on the primary proper score and was slightly worse for SPY.

The most defensible conclusion is: **QQQ has suggestive local signal; SPY does
not; neither candidate clears a joint SPY/QQQ signal gate.**

## Frozen comparison

The audit used the ADR-0200 guard-aware archive's unchanged raw forecasts:

- `horizon_empirical`, the reference;
- `tail_blend_very_conservative`, the governed physical forecast;
- `option_proxy_transport`, the strongest earlier option-surface challenger.

No model was trained or recalibrated. The deterministic panel selected 1,000
evenly spaced 2018 development identities for SPY and 1,000 for QQQ. All three
models saw identical entry/expiry identities, chain rows, candidate rules, and
deduplicated wing intervals. Eight identities had no eligible fixed-width
condor, leaving 1,992 paired forecasts across 249 quote dates.

The prepared input contains 344,932 chain rows. Evaluation scored 1,761,423
candidate/model rows and 67,073 unique within-forecast wing templates. Each
forecast has total weight one. Circular moving-date-block resampling carries
all forecasts and both indexes on a sampled quote date together.

## Primary actual-wing score

Mean refined positive-floor wing scores were 0.112619 for empirical, 0.111131
for option transport, and 0.112304 for the conservative blend.

Option transport had +1.207% equal-index skill versus empirical. Its 30-date
interval was [-2.277%, +5.794%] and its 60-date interval was
[-1.740%, +6.442%]. By index it was +2.314% for QQQ and only +0.100% for SPY.

The conservative blend had +0.249% equal-index skill. Its 30-date interval was
[-1.506%, +2.514%] and its 60-date interval was [-1.319%, +2.639%]. By index it
was +0.551% for QQQ and -0.053% for SPY.

Neither model establishes positive primary local skill. Neither interval
excludes zero, and the conservative blend fails even the pointwise requirement
that both indexes improve.

## Secondary diagnostics

For actual-strike Brier error, option transport had +0.885% overall skill:
+2.066% for QQQ and -0.297% for SPY. Both block intervals crossed zero. The
conservative blend had +0.098% overall: +0.385% for QQQ and -0.188% for SPY.

For condor terminal-loss MSE, option transport had +3.099% overall skill but
split +7.641% for QQQ and -1.444% for SPY; both intervals crossed zero. The
conservative blend had +3.186% overall, +5.272% for QQQ, and +1.101% for SPY.
Its 60-date interval was [+0.547%, +6.914%], while the 30-date interval was
[-0.114%, +7.167%]. This is a useful secondary lead, but it cannot override the
inconclusive proper wing score or establish profitability.

## Numerical and resource evidence

The first immutable attempt refused its overly tight 0.005 per-row quadrature
bound. The r2 replacement used the previously validated 0.05 bound and passed
the numerical check, but PyArrow attempted to create a worker thread while the
process was at its declared address-space ceiling. The r3 replacement retained
the same panel, models, scores, and bound, and wrote candidate evidence in
single-threaded 100,000-row batches.

R3's mean coarse/refined score difference was 0.001278 and its maximum was
0.011648, below 0.05; coarse and refined model rankings matched. Preparation
took 1.35 seconds at 698 MiB RSS. Evaluation took 10:55 at 2.86 GiB RSS. The
2,000-replicate report took 1.35 seconds at 1.42 GiB RSS. All stages stayed
below 30 minutes and 6 GiB. The focused suite passed 46 tests.

## Interpretation and next investigation

The earlier synthetic-cutoff and fixed-condor gains materially overstated the
evidence available at actual listed decision boundaries. Option-surface signal
survives directionally for QQQ but is absent or negative on several SPY
diagnostics. Pooling them into one optimizer would hide that heterogeneity.

The next bounded investigation should not be another architecture zoo. Freeze
the current three models and determine whether QQQ's local gain persists on
the already-defined later partitions, using the same actual-wing score and
block protocol. Treat those years as descriptive because they have been
inspected for other endpoints. If QQQ persists while SPY does not, subsequent
feature selection and optimization should be index-specific. If it does not,
stop the option-transport path rather than tuning the optimizer around noise.

Generated evidence is under
`pipeline_runs/cdf_actual_wing_paired_audit_r3_20260930`; the two refused roots
remain preserved. The archive is date-only and after-close, so this audit has
no executable or trading authority.
