# Decision-region loss zoo design

Status: implemented and run; descriptive-only, with no model promoted. Results:
`2026-09-30-decision-region-loss-zoo-results.md`.

The study predicts the standardized terminal log return. For each entry row,
the existing `EligibleCondorChain` owner enumerates every quotable $5-wide
condor under the frozen chain rule. The training context contains every unique
put/call strike used by those wings in exact coordinates
`z = log(K / S0) / reference_scale`. Strikes are deduplicated; put and call
sides each receive half the row's local mass. Rows with no eligible condor are
retained, explicitly labelled, and receive only the global loss.

The Torch objective is:

`negative log likelihood + lambda_d * strike Brier + lambda_g * global-grid Brier`.

All terms are proper scores. NLL identifies the full distribution, the positive
global-grid term protects the center and tails, and the strike term concentrates
finite-sample capacity at actual decision cutoffs. Wing expected-loss MSE remains
a diagnostic and is not optimized directly.

The zoo compares a global mixture-MLP control; small, medium, and wider
decision-weighted Gaussian-mixture MLPs; a monotone LightGBM cutoff classifier;
and the horizon empirical baseline. LightGBM is not wrapped in Torch: its
training rows are expanded to `(x, z, 1[y <= z])`, with proper weighted binary
log loss and a monotonic constraint in `z`. Its curve predicts directly at each
evaluation row's strikes, avoiding interpolation through the decision points.

All splits remain chronological and purge labels unavailable at fit/calibration
boundaries. Context records are identity-bound, source-hashed, after-close-only,
and refuse executable clocks, duplicates, invalid quotes, source mismatches and
candidate caps. The JSON limits the process to 30 minutes and 6 GiB.

Primary descriptive comparison is actual-strike Brier skill with paired date-
block intervals; global CRPS and tail diagnostics remain visible. This first zoo
cannot promote a model because no preregistered global/tail noninferiority gate
has been frozen. A challenger must pass that later gate before optimization.
