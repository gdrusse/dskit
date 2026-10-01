# SPY decision-region persistence guard

## Decision

The frozen decision-weighted wide MLP shows economically interesting SPY
persistence, but it fails the preregistered promotion guard by a narrow margin.
Across 2020--2025 its actual-strike Brier skill versus horizon empirical is
positive overall; the 60-date paired interval lower bound is +0.020%, while the
30-date lower bound is -0.223%. Because both lower bounds had to exceed zero,
the result remains descriptive and cannot feed optimization.

## Frozen procedure

The architecture, loss, features, raw variant, and reference were frozen from
the prior six-model zoo. No HPO or variant reselection occurred. Each annual
fold fit only on outcomes settled before the prior-year calibration band and
evaluated the following year. The 2019 fold anchors the already-inspected
choice; 2020--2025 are descriptive persistence years.

The entry-known context contains every deduplicated listed strike used by an
eligible fixed-width SPY condor, joined on exact symbol, quote date, and expiry.
The later panel contains 23,141 eligible forecasts. Same-date forecasts remain
paired in 30- and 60-date circular block resamples.

The machine-readable guard required:

- both actual-strike Brier interval lower bounds above zero;
- global CRPS, tail CRPS, and lower/upper tail quantile scores no worse than
  1% versus empirical; and
- absolute 5%/95% coverage deviation no more than one percentage point worse.

## Results

Annual actual-strike Brier skill was +9.560% in 2020, +5.418% in 2021,
-0.753% in 2022, +0.551% in 2023, +0.087% in 2024, and +4.602% in 2025.
The effect is therefore recurring but regime-variable, not a single-year
artifact.

Every global/tail guard passed. Aggregate skill was +2.224% for global CRPS,
+3.594% for tail CRPS, +3.027% for the lower-tail quantile score, and +20.604%
for the upper-tail score. Absolute coverage deviation improved by 1.229
percentage points below 5% and 5.534 points above 95%.

The sole failure was local uncertainty: the 30-date interval still crossed
zero. This is a precision/effect-stability failure, not a global or tail
calibration failure. Do not weaken the frozen gate after seeing the result.

## Execution and next step

The standardized WSL2 run completed in 4m20s at 3.27 GiB RSS inside hard
30-minute/6-GiB/no-swap cgroup limits. The predictive-CDF focused suite passed
119 tests. Terra skeptic review closed at zero Critical/Major before execution.

Evidence is under
`pipeline_runs/predictive_cdf_decision_loss_spy_persistence_20261001`.
The comparison and scores SHA256 values are
`32fb37e47921d687a56095468bc312abe9f21d0eec33b28c14093231401217d7`
and `0f2ece1d6e00cf988b1222a8ab3b1657a4ee7bd5f3eef57f83bc4bebed39aa1f`.

Next, diagnose QQQ degradation at the individual entry-strike level by side,
standardized distance, exact DTE, and year. Do not expand the architecture zoo.
