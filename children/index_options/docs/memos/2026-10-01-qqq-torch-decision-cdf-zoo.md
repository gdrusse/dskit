# QQQ Torch CDF: decision-region zoo

## TL;DR

The generic JSON-driven Torch framework and bounded zoo are complete. All 12
challengers lost to empirical on 2018 development. The selected MLP achieved
+2.043% primary skill in 2019, but both uncertainty intervals crossed zero.
Neither encoder passes the decision-region acceptance rule.

## Execution contract

The owner authorized ADR-0211 on 2026-10-01, superseding the earlier no-zoo
recommendation. Base: clean origin/main 8c269a46; reviewed implementation:
d2bbdd96. This is offline descriptive research, not a trading/profitability claim.

The response is terminal log return divided by entry-known reference scale.
The child joins exact QQQ/date/expiry snapshots, enumerates eligible listed
$5 wings, deduplicates their endpoints, and maps strikes to log(strike/spot)/scale.
Put and call sides each receive half the row weight. Outcomes cannot choose
strikes, wings, weights, or eligibility. The date-only archive supports indicative
after-close decisions, not executable quotes or a fully point-in-time acquired-data
claim. Sources, identities and clock are hashed.

Purged 2018 folds select one finalist per encoder before frozen 2019 evaluation.
Fit and calibration labels settle before their boundaries; 2018 development
also excludes labels settling in 2019. Both encoders use 22 entry-known return
lags plus context. The GRU reads oldest to newest within a row, never across
forecasts. All models share identities, features, scales, regions and splits.

## Implementation and search

TorchCDF extends the existing analytic Gaussian-mixture seam. JSON chooses MLP
or GRU and weighted proper-score terms; generic code contains no index, provider,
or column choice. DecisionRegionScores consumes generic identity-bound
thresholds; the child owns listed-wing eligibility and source-clock validation.

Twelve candidates = two encoders × three losses × two learning rates
(0.001/0.003). All use three mixture components, [32,16] output layers, 12 fixed
epochs, batch size 512, seed 11 and deterministic CUDA. GRU has one 16-unit
recurrent layer. No early stopping or later-year reselection. Calibration is
disabled; saved raw/calibrated rows are identical and raw wins the tie.

Losses: Brier = 0.1 NLL + local Brier; log = 0.1 NLL + local Bernoulli log score;
balanced = 0.1 NLL + local Brier + 0.25 local log score. NLL uses all rows.
Local terms divide by eligible rows; minibatches use the full-training eligible
fraction so empty batches cannot dilute the declared weight. Float64 preserves
exact threshold/outcome events in training and reporting.

Primary skill = 100 × (1 − mean over exact-DTE cells of candidate Brier /
empirical Brier). Each cell averages paired eligible forecasts, each forecast
uses side-balanced listed thresholds. HPO uses 44 development cells; later
evaluation uses 45. The purged 2018 26-day cell is absent.

Acceptance requires both 30/60-date bootstrap lower bounds above zero and
equal-cell absolute local calibration-bias increase at most 0.01. Circular
date blocks retain same-date pairs; 1,000 replicates. Global/tail/constructed-wing
scores are diagnostics only. Intervals are pointwise, not multiplicity-adjusted;
historical years were previously inspected, so even a pass remains descriptive.

## Development losses, skill and observations

Common counts: training 7,799 rows / 7,239 eligible / 167,154 thresholds /
1,452 dates; calibration 1,534 / 1,487 / 48,138 / 250; validation 1,550 /
1,540 / 75,052 / 249. Composite magnitudes across different losses are not
rankings. Training skill is in-sample; validation skill is out-of-sample.

| Candidate | Train loss | Validation loss | Train skill % | 2018 skill % |
|---|---:|---:|---:|---:|
| mlp_brier_slow | 0.226378 | 0.298807 | +2.583 | -15.376 |
| mlp_brier_fast | 0.210254 | 0.317014 | +8.912 | -22.223 |
| mlp_log_slow | 0.427533 | 0.604393 | +2.603 | -15.720 |
| mlp_log_fast | 0.397972 | 0.665509 | +8.760 | -25.580 |
| mlp_balanced_slow | 0.300376 | 0.412146 | +2.630 | -15.788 |
| mlp_balanced_fast | 0.278991 | 0.441429 | +9.018 | -23.376 |
| gru_brier_slow | 0.223474 | 0.295309 | +4.051 | -12.919 |
| gru_brier_fast | 0.211774 | 0.305507 | +8.401 | -21.015 |
| gru_log_slow | 0.423570 | 0.598088 | +3.761 | -13.402 |
| gru_log_fast | 0.398528 | 0.659260 | +8.284 | -28.697 |
| gru_balanced_slow | 0.296884 | 0.407155 | +3.983 | -13.533 |
| gru_balanced_fast | 0.280149 | 0.427551 | +8.533 | -23.949 |

The frozen finalists, mlp_brier_slow and gru_brier_slow, are the least-bad
development challengers, not development successes.

## Frozen 2019 results

Refit training: 9,419 rows / 8,812 eligible / 218,061 thresholds / 1,703 dates.
Calibration: 1,550 / 1,540 / 75,052 / 249.
Validation: 1,677 / 1,676 / 90,795 / 251, across 60 expiry series.
Excluded observations are counted, never scored as zero.

| Finalist / band | Composite loss | NLL | Local Brier | Decision skill % |
|---|---:|---:|---:|---:|
| mlp_brier_slow / training | 0.224824 | 1.325264 | 0.092298 | +0.977 |
| mlp_brier_slow / calibration | 0.298487 | 1.542281 | 0.144259 | -9.885 |
| mlp_brier_slow / validation | 0.253221 | 1.399726 | 0.113248 | +2.043 |
| gru_brier_slow / training | 0.217539 | 1.281103 | 0.089429 | +3.753 |
| gru_brier_slow / calibration | 0.300151 | 1.534694 | 0.146682 | -14.674 |
| gru_brier_slow / validation | 0.262377 | 1.439589 | 0.118418 | -1.944 |

For example, MLP validation loss = 0.1 × 1.399726 + 0.113248 = 0.253221.
NLL averages 1,677 rows; local Brier averages 1,676 eligible rows. Telemetry
uses the actual configured empirical fit and preprocessing. Calibration is
held out from fitting but is not the final test.

| Finalist | Skill % | 30-date interval % | 60-date interval % | Local bias increase | Accept? |
|---|---:|---:|---:|---:|---|
| mlp_brier_slow | +2.043 | [-9.708, +6.550] | [-8.567, +6.834] | -0.007675 | No |
| gru_brier_slow | -1.944 | [-21.228, +7.894] | [-20.515, +8.456] | -0.005798 | No |

The MLP's mean of 45 paired cell-score ratios is 0.979566:
100 × (1 − 0.979566) = +2.0434%. Its lower bounds −9.7075% and −8.5671%
both fail the required zero. Bias change −0.007675 passes the +0.01 limit.
GRU also passes local bias and fails both intervals.

Equal-DTE weighting matters: pooled row-average Brier is worse than empirical
for both finalists (MLP 0.113248; GRU 0.118418; empirical 0.109361). The positive
MLP primary point estimate is therefore not a broad row-average gain.
Global CRPS row means are 0.589682/0.600470 versus empirical 0.562838;
tail-score means are 0.364390/0.364014 versus 0.346241. These are diagnostics only.

## Execution and verification
Six final standard CLI stages completed in 251.381s combined (4m11s).
Maximum process RSS was 2.434 GiB; peak Torch CUDA allocation 257.2 MiB.
Every invocation had enforced systemd MemoryMax=6G, MemorySwapMax=0 and
RuntimeMaxSec=1740; recorded swap peaks were zero. Full service times include
data loading; completion stage times exclude earlier loading. Some short-lived
service summaries under-report memory, so peak above uses process ru_maxrss.
CUDA allocation excludes driver memory. GPU: RTX 5060 Ti.

Focused checks: 233 passed across the CDF pack, child CDF and pipeline purity;
26 upstream deprecation warnings. No unrelated full suite. Ruff adds zero
violations versus base. Independent final correctness and integration lenses
both returned C0/M0/m0/n0 on d2bbdd96; integration independently recomputed
all 12 development skills/counts and checked both evaluation partitions.
See docs/review-evidence/ADR-0211.md for retained findings and corrections.

Preserved attempts: a 23s pre-fit interruption after review; a 26s metadata
census refusal; two completed search-only screens superseded by the population
denominator correction. None supplied final selections or later evidence.
WSL briefly returned E_UNEXPECTED, then recovered without a shared-instance
restart. No completed result was overwritten or reconstructed from partial output.

Only the 12-candidate zoo ran. The two-candidate smoke JSON was validated but
deliberately not executed. No SPY run, unseen-period validation, broader search,
optimizer/backtest, provider pull, paper/live execution or deployment occurred.

## Reproduction and handoff

Use configs/run-predictive-cdf-qqq-torch-zoo.json through the existing
python -m index_options.cdf_study CLI. Ordered stage arguments:

1. --stage search --partition mlp
2. --stage search --partition gru
3. --stage select
4. --stage evaluate --partition development
5. --stage evaluate --partition later
6. --stage report

From the WSL child directory, append one argument set to this command:

    systemd-run --user --wait --pipe --property=MemoryMax=6G \
      --property=MemorySwapMax=0 --property=RuntimeMaxSec=1740 \
      --working-directory="$PWD" /usr/bin/env \
      PYTHONPATH="$PWD/../..:$PWD" OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
      CUBLAS_WORKSPACE_CONFIG=:4096:8 /home/russell/dskit/.venv/bin/python \
      -m index_options.cdf_study configs/run-predictive-cdf-qqq-torch-zoo.json

Completed roots refuse overwrite: reruns need new output paths in copied JSON.
The standard two-epoch smoke document uses the same stages and both encoders.

Local run root: `/home/russell/dskit-torch-decision-cdf/children/index_options/pipeline_runs/qqq_torch_cdf_20261001_r2`.
Logs: sibling `qqq_torch_logs/final-*.log`. Counts preserve loss terms,
eligible/total/threshold/date denominators and training mini-batch curves.
Protocols, source hashes, versions, panels, scores, frozen curves, selection
and hash-checked completion manifests remain local. Generated artifacts are
Git-ignored; this memo and code/configuration are tracked.

- Full experiment identity: `2c2fb7419edfdc0915b0153e3b6dabbfba92289d7ecebd2ff5bc989831bd2005`.
- selection/selected.json SHA256: `37158e4ff6456cfe874845cc2e14e0f1b234551b62775ca137b2f53d7714d2ec`.
- report/comparison.json SHA256: `f3025a819e12a4221359223f6194a5180e37295fb4c2ec7bb83123e32af71740`.
- report/complete.json SHA256: `ac23595dad9e6d2b1e9dfb5994367cca1fbfeccca7d75ac7300aa500ceab8af7`.

The bounded study is complete. Keep the empirical reference and both
challengers unaccepted. Further tuning or a new-year test needs a separately
declared study; these results do not authorize optimization.
