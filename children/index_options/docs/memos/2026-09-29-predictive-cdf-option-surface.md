# Option-surface, tail-loss, and adaptive-blend CDF study

## Decision

Retain `incumbent_blend_025:raw` as the research benchmark. None of the six
predeclared challengers passed the development guard, and none beat the
incumbent over 2019--2025. Do not promote a new model and do not interpret
these reused historical results as authorization to trade.

The useful conclusions are narrower:

- causal option-chain summaries did not improve whole-distribution CRPS
  consistently;
- a tail-weighted CDF loss made virtually no difference to the existing 25%
  empirical/MLP blend;
- a learned row-level blend did not discover useful conditional variation:
  its fitted weights were nearly constant within each annual fit;
- some surface/adaptive variants improved tail CRPS, strike Brier score, and
  synthetic-condor loss error, but lost materially on global CRPS and did not
  improve lower-tail coverage.

The next defensible step is not a larger blind search. Diagnose why the
option-surface features are unstable across years, then test any revised
specification on genuinely new dates.

## What is forecast

For every index entry date and exact listed expiry, the model emits a complete
CDF of the signed terminal log return, standardized by a causal reference
scale. It is a price-at-expiry distribution, not a volatility forecast.

For entry spot `S`, strike `K`, and reference scale `a`, the probability of
settling below the strike is

`P(S_expiry <= K) = F(log(K / S) / a)`.

Those probabilities can later feed iron-condor payoff integration. This study
does not select contracts, estimate option credits, or model spreads, fees,
assignment, dividends, or early exits.

The incumbent curve is

`F(z) = 0.75 F_empirical(z) + 0.125 F_MLP,seed11(z) + 0.125 F_MLP,seed29(z)`.

`F_empirical` is an index/horizon-conditioned 401-knot empirical CDF. Each MLP
has one 16-unit tanh hidden layer and predicts a Normal mean and positive scale.
The two networks use fixed seeds; their average is an ensemble, not a selected
winner.

## Changes tested

The experiment added three standardized capabilities, all configured in JSON
and run through the normal study CLI.

### Causal option-chain surface features

Ten entry-time summaries were added to the existing 42 predictors:

- log ATM implied volatility;
- log 25-delta skew and curvature;
- log relative bid/ask spread;
- log put/call open-interest ratio;
- log contract count, total open interest, and quote depth;
- indicators for a usable 25-delta pair and put/call open interest.

Volume was excluded because the archive does not provide it reliably. Missing
surface values are preserved by panel construction and imputed from training
data only. In the 109,355-row research panel, ATM IV was missing on 90 rows,
skew/curvature on 7,247, relative spread on 63, and put/call OI ratio on 3,700;
the availability flags were complete.

### Tail-weighted neural training

The MLP retained likelihood training and added a differentiable CDF Brier term
over fixed left-tail cutoffs. A zero tail weight exactly recovers the legacy
path. The selected tail candidates used weight 0.25 and otherwise retained the
incumbent architecture and fixed 25% mixture weight.

### Adaptive empirical/MLP blending

A bounded logistic gate was trained to choose a row-specific neural weight.
Its endpoints were fitted only on the purged training sample; the gate used
only the prior-year calibration labels. Gate loss gave equal weight to each
observed index/actual-DTE cell. Actual calendar DTE was carried as nonpredictive
metadata and is rejected from the empirical conditioning, gate, and MLP
predictor selectors.

The adaptive models are already calibration-label consumers, so a second
calibration map was prohibited. Saved curves retain every row's fitted blend
weight, allowing exact reconstruction.

## Predeclared comparison

Thirteen candidates formed six disjoint development groups:

1. surface only (2);
2. tail only, base features (2);
3. adaptive blend, base features (1);
4. surface plus tail (2);
5. surface plus adaptive blend (2);
6. surface plus tail plus adaptive blend (4).

Controls were the empirical CDF, pure pooled Normal MLP, and unchanged fixed
25% incumbent blend. The six development winners were frozen before any new
2019+ score was read. The selection artifact SHA-256 is
`899bb15073f52a6ca2c6a6b4b0b9bca041c84563bd8250e1c167c3cb189e5023`.

The frozen group winners were:

- `surface_fixed_025:raw`;
- `base_tail_025_fixed_025:raw`;
- `base_adaptive_cap035:raw`;
- `surface_tail_025_fixed_025:raw`;
- `surface_adaptive_cap050:raw`;
- `surface_tail_025_adaptive_cap035:raw`.

## Training, validation, and guard procedure

Forecasts are refit annually. For validation year `Y`:

- fitting uses rows whose labels settle strictly before January 1 of `Y-1`;
- the preceding calendar year is the calibration/gate sample, with labels
  purged to settle strictly before January 1 of `Y`;
- validation contains entries in `Y`;
- imputation and scaling statistics come only from the fitting rows.

Development covered 2016--2018: 18,616 forecasts and 133 observed
index/actual-DTE cells. Each group selected one model/variant by equal-cell
CRPS. Before evaluation, a guard required both lower development CRPS than the
incumbent and acceptable PIT behavior. All six challengers failed: the tail
candidate did not improve CRPS, while the other five failed the PIT guard.
The incumbent therefore remained the frozen primary.

Evaluation covered 2019--2025: 68,084 forecasts per model, 1,746 entry dates,
3,495 nominal expiry series, and all 135 index/actual-DTE cells. Annual fitting
samples ranged from 4,384 rows to 37,626; calibration samples ranged from
1,534 to 4,115; validation cohorts ranged from 1,550 to 4,316. These years
have been examined in earlier research and are not an untouched holdout.

The sample counts by index and evaluation year are:

| Index | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | Total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| SPY | 4,246 | 4,316 | 4,296 | 4,230 | 3,505 | 3,691 | 3,324 | 27,608 |
| QQQ | 1,677 | 1,717 | 3,506 | 4,173 | 3,495 | 3,591 | 3,334 | 21,493 |
| IWM | 1,669 | 1,717 | 2,334 | 4,077 | 2,592 | 3,303 | 3,291 | 18,983 |

All primary comparisons use equal weight for each of the 135 observed
index/actual-DTE cells. Positive skill is better. The full exact-day grid,
including `n` for every cell, is in `report/skill_by_exact_day.csv`.

## Development result

The incumbent's relative development CRPS was 0.989986192. Challenger relative
CRPS values were 0.988727803 (surface), 0.990007103 (tail), 0.988811313
(adaptive), 0.988771225 (surface plus tail), 0.987914381 (surface plus
adaptive), and 0.987986752 (all three). Lower point estimates alone did not
pass the predeclared PIT guard. No challenger was eligible for promotion.

## Evaluation result: 2019--2025

Each row below contains 68,084 forecasts. “Vs incumbent” is the direct
equal-cell comparison, not the difference between two rounded skill columns.

| Model | Skill vs empirical % | Skill vs incumbent % |
|---|---:|---:|
| Empirical | 0.000 | -0.837 |
| **Incumbent fixed 25% blend** | **+0.818** | **0.000** |
| Surface | +0.728 | -0.091 |
| Tail objective | +0.817 | -0.001 |
| Adaptive blend | +0.690 | -0.126 |
| Surface + tail | +0.737 | -0.083 |
| Surface + adaptive | +0.511 | -0.304 |
| Surface + tail + adaptive | +0.476 | -0.343 |
| Pure MLP | -1.917 | -2.733 |

The incumbent remained better than empirical with paired 95% block intervals
of `[+0.183%, +1.466%]` for 60-date blocks and `[+0.263%, +1.507%]` for
120-date blocks. Every challenger interval versus the incumbent crossed zero.
The tail model's point estimate was -0.000636%, with intervals
`[-0.0093%, +0.0100%]` and `[-0.0097%, +0.0101%]`: operationally the same
forecast, not incremental skill.

### Skill by index

| Model | SPY n=27,608 | QQQ n=21,493 | IWM n=18,983 |
|---|---:|---:|---:|
| Incumbent vs empirical | +0.966 | +1.227 | +0.261 |
| Surface vs incumbent | -0.082 | -0.124 | -0.069 |
| Tail objective vs incumbent | +0.003 | -0.004 | -0.000 |
| Adaptive blend vs incumbent | -0.078 | +0.001 | -0.302 |
| Surface + tail vs incumbent | -0.068 | -0.135 | -0.045 |
| Surface + adaptive vs incumbent | -0.322 | -0.068 | -0.523 |
| All three vs incumbent | -0.272 | -0.329 | -0.429 |

No architecture improved all three indexes. The tail model's tiny SPY gain is
offset by QQQ and IWM; it is far below its paired uncertainty.

### Stability by year

Surface-only skill versus the incumbent was -0.058%, -0.302%, -0.205%,
-0.107%, +0.690%, -0.046%, and -1.056% from 2019 through 2025. The single
strong 2023 result did not persist. Adaptive-only was positive in 2020, 2021,
2024, and 2025 but sharply negative in 2019, 2022, and 2023. This is regime
instability, not a dependable edge.

### Tail and payoff diagnostics

Versus empirical, the incumbent achieved +1.231% tail-CRPS skill, +1.273%
strike-Brier skill, and +2.920% synthetic-condor-loss-MSE skill. The
surface-plus-adaptive model improved those point estimates to +1.362%,
+1.459%, and +3.491%, respectively, but lost -0.304% global CRPS versus the
incumbent. Its observed below-5th-percentile rate was 8.208%, versus 8.039%
for the incumbent; nominal 5% coverage did not improve.

The synthetic-condor metric integrates fixed normalized payoffs and excludes
real credits and execution costs. It is a diagnostic, not strategy return.
These secondary improvements do not override the predeclared primary score.

### What the adaptive gate learned

The row-level weights were almost constant inside each annual/index fit. For
example, base-feature weights were about 0.280 in development, hit the 0.35
cap in 2021, fell near 0.007 in 2023, and returned near 0.347 in 2025; within
each cohort the 5th and 95th percentiles were nearly identical. The gate acted
like a noisy annual intercept rather than learning useful state-dependent
allocation. Pooling did not solve that identification problem.

## Runtime and verification

The 109,355-row panel built in 5.6 seconds with 0.46 GiB peak RSS. Six search
partitions took 63--136 seconds each and 2.30--2.54 GiB. Selection took 9
seconds. The four scoring partitions took 7:10, 10:18, 9:24, and 9:57 with
2.55--2.75 GiB peak RSS. Reporting took 37 seconds and 1.43 GiB. Every bounded
stage stayed below 30 minutes and 6 GiB.

CUDA was available in WSL2: RTX 5060 Ti, 16,311 MiB, Torch 2.11.0+cu128.
Affected tests passed: 102 passed, one optional quantile-forest test was
deselected because its dependency is absent. Ruff and `git diff --check`
passed. The study used no one-off execution scripts.

## Artifacts and recommendation

The complete machine-readable report is under
`children/index_options/pipeline_runs/predictive_cdf_option_surface_20260929/report/`.
Important files are `comparison.json`, `metrics_by_index.csv`,
`skill_by_exact_day.csv`, `raw_vs_calibrated.csv`, `convergence.json`, and
`complete.json`.

Keep the fixed 25% empirical/MLP blend as the research benchmark. Preserve the
new infrastructure because it enables causal surface features, explicit tail
losses, and auditable rowwise mixtures without special scripts. Before another
HPO iteration, examine feature drift and quote-quality normalization by year,
and redesign adaptive gating only if a diagnostic shows real within-cohort
heterogeneity. Any promotion claim should wait for new, uninspected dates.
