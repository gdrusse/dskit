## TL;DR

Pooled models retain small forecasting gains; unpooled advanced models do not beat the earlier unpooled LSTM on their matching completed rows. Twelve of fifteen requested architectures trained somewhere; three were excluded for documented incompatibilities. These are single-seed, descriptive results, with incomplete options coverage and no claim of statistical significance or architecture-only causation.

## Execution contract

The frozen candidate was `29d3bec1eac19ade16800384bcd7f98f094855ed`, executed in WSL2 on 2026-10-06–07 from `children/index_options/configs/advanced-cdf-zoo.json`. Its SHA256 is `1f7ba634b0168ea76d4affe7303df5e16337a17544929cdcb8fd0c496a601e0f`. The operation was `python -m dskit.pipeline.libs.cdf_experiment <config> --regime <pooled|unpooled> --workers <1|4> --worker <partition>`, with the recorded pilot/resume scripts. Final extraction on 2026-10-07 read saved results only; no new training, raw dataset read or holdout evaluation occurred.

The return target is divided by its entry-known reference scale. Existing scored outputs verify `actual_calendar_dte = 31` for all 103,961 prior pooled forecasts; the 103,901 paired advanced base forecasts match those horizon values exactly. Base models use 42 inputs; options models use 56. Nine validation windows span 2024-02-06 to 2025-12-29. Training expands from 2016 for base models; the last 40 eligible dates form the separate early-stopping band. Labels crossing a band boundary are purged. The holdout begins 2026-01-30 and remains unopened.

Training uses a three-Gaussian mixture, seed 11, learning rate 0.0003, weight decay 0.01, dropout 0.1, patience 20 and a 500-epoch ceiling. Batch limits are 512 pooled on CUDA and 64 unpooled on CPU, reduced to the training-row count when smaller. Parameter budgets choose the first admissible native configuration and head width from 8, 4, 2: pooled at most 100,000 parameters or 0.25 per training row, with a per-ticker head limit; unpooled at most 8,192 or 4 per training row.

Both training and reporting use CRPS plus tail CRPS with weight 1 over standardized intervals [−2.5, −0.5] and [0.5, 2.5]. Lower scores mean better distribution forecasts. Reporting uses 201 quantiles and 101 tail integration points. The comparison baseline is each ticker’s training-only empirical return distribution, with 401 knots.

## What completed, and what did not

The audit accounts for 69,540 expected fit outcomes: **48,073 completed fits and 21,467 skips/refusals**. Expanded across ticker/model/window slots, 96,553 scored, 42,137 were skipped, and 179,640 failed admission: all 318,330 slots are accounted for. These are accounting units, not independent observations.

Of the 21,467 terminal skips, 13,908 implement the three declared architecture exclusions, 7,435 exceed parameter budgets, 109 reach the epoch ceiling before exhausting patience, and 15 encounter a singleton-batch normalization error. Those last two categories are training refusals, not successful fits. No larger-budget replacement or silent fallback is counted as a result.

Completed pooled base fits ran 22–106 epochs and retained epochs 2–86. Completed unpooled base fits ran 21–500 and retained epochs 1–480. Final histories, finite gradients, parameter movement and optimizer-step counts were verified; a completed fit at epoch 500 exhausted patience there, unlike a ceiling refusal.

## All fifteen architecture outcomes

For a fair comparison across the twelve trained architectures and both regimes, the following base-feature results use their shared **92,231 forecasts, 373 tickers and 3,082 ticker/windows**. Each entry gives pooled / unpooled weighted-CRPS improvement over the baseline. Positive values are better. This common subset is smaller than any headline using a model’s full available coverage.

- **PatchTST**: +0.143% / -2.614%.
- **PatchTSMixer**: -0.181% / -3.126%.
- **VanillaTransformer**: +0.171% / -2.174%.
- **Informer**: no trained result. Upstream ProbSparse evaluation is stochastic and batch dependent; failed deterministic row-invariance preflight.
- **Autoformer**: -0.052% / -1.718%.
- **FEDformer**: +0.120% / -1.718%.
- **TFT**: +0.015% / -1.838%.
- **TCN**: -0.031% / -2.782%.
- **BiTCN**: no trained result. The native forward network requires future covariates; this input layout would instantiate only the backward network, so it is not counted as a bidirectional architecture.
- **DilatedRNN**: +0.020% / -1.708%.
- **TimesNet**: no trained result. Upstream FFT period selection depends on other evaluation rows; failed row-invariance preflight.
- **TSMixer**: +0.034% / -2.255%.
- **TiDE**: +0.019% / -2.333%.
- **NBEATS**: -0.105% / -2.453%.
- **KAN**: -0.009% / -2.305%.

VanillaTransformer leads this common base subset at +0.171%; PatchTST is +0.143%. Pooled scores are better than unpooled scores for all twelve on these same rows. None of these point differences establishes a reliable winner.

On all 103,901 available pooled base forecasts across 393 tickers, PatchTST improves 0.458% over the empirical baseline versus 0.252% for the prior CNN, and beats that CNN on 207/393 tickers. Seven advanced pooled architectures beat the prior CNN by their aggregate point scores. All twelve advanced unpooled architectures trail the prior LSTM on their own completed intersections, which range from 92,861 to 103,451 forecasts. Those different intersections must not be ranked against one another as if coverage were identical.

The prior comparison verifies 368 raw score-file hashes and 3,471 canonical advanced score shards. Forecast identity, settlement date, horizon, scoring resolution and empirical baseline match; the baseline score difference is exactly zero on the matched prior rows. Sixty older pooled-only forecasts are excluded. Prior models were chosen on the 2023 warm-up fold and use different learning rates, batches and widths, so this compares fitted systems rather than architecture alone.

## Denominators and a worked calculation

Let M be the sum of model scores, B the sum of empirical baseline scores and N the number of matched forecasts. Scores have standardized-return units; reported mean scores are M/N and B/N. The headline uses the ratio of sums, not an equal-weight average of ticker percentages:

```math
\mathrm{skill}=100(1-M/B),\qquad \mathrm{mean\ score}=M/N.
```

For pooled PatchTST on N = 103,901 matched forecasts: M = 106,837.746677 and B = 107,329.019505. Its mean score is 1.028264855 versus baseline 1.032993133:

```math
100(1-106837.746677/107329.019505)=+0.457726\%.
```

The threshold is zero: the aggregate forecast score is 0.458% lower than baseline. The prior CNN sum is 107,058.266450, so PatchTST’s improvement over that CNN is 100 × (1 − 106,837.746677 / 107,058.266450) = 0.205981%. Its 207/393 ticker wins are 52.67%, a separate count, not the aggregate-skill calculation. The older memo’s approximately +0.36% CNN headline used equal-ticker weighting; the matching ratio-of-sums value here is +0.252%. Neither arithmetic implies significance.

## Options: observed data, matching rows and changing widths

Options and the matched-base control use rows with **at least one** observed option field, plus minimums of 128 fit rows, 20 monitor rows and 8 non-overlapping labels. This is not a complete-case filter for all seven option fields. Only folds 40–41 produced scores, from 2025-07-29 through 2025-12-29, covering at most 17,037 paired forecasts and 300 tickers per architecture. Earlier windows are explicitly unavailable.

Volume, trade count, strike count, put share and weighted moneyness were observed throughout the completed option fit populations. Call/put-ratio coverage was 97.51–97.68% pooled and 66.92–100% unpooled; straddle-ratio coverage was 81.80–82.79% pooled and 10.38–100% unpooled. Remaining missing fields were median-imputed from training only. “Observed options” must not be read as “every option input observed on every row.”

Against matched-base controls, pooled option gains occur for PatchTSMixer (+0.492%, 17,037 forecasts) and TCN (+0.045%, 17,037). PatchTST, DilatedRNN, TSMixer, NBEATS and KAN lose; the other five trained base architectures have no paired pooled options result. Unpooled gains occur for VanillaTransformer (+1.006%, 15,426 forecasts), TCN (+1.400%, 16,838) and DilatedRNN (+0.860%, 15,366); four paired architectures lose and five have no paired result. These are within-pair differences, not a ranking across differently covered populations.

All twelve pooled option/control fit pairs have the same two-unit head and native encoder settings. Of 3,560 unpooled paired fits, 1,366 have different head widths and 477 different native encoder settings; those counts may overlap. A fixed parameter budget can therefore change the model when option inputs are added. In particular, unpooled options differences cannot be attributed solely to the extra information.

## Verification and integration are separate

The frozen-run audit passed 406,865 payload hashes, all expected outcomes, forecast pairing, temporal bounds, training histories, 65 source hashes and 190 dependency versions. An independent reconstruction checked all 48,073 fitted parameter counts and the first admissible configuration, using 898 distinct parameter shapes. Source rows were not reopened, so training-only medians, scaler values and band identity hashes were not independently recalculated from data.

All four final reports passed browser checks for independently recalculated summaries, exclusive outcome coverage, filters, metrics, windows, pagination, empty search and desktop/mobile layout, with no JavaScript errors. Supplemental options/prior browser arithmetic passed for all four reports, all three metrics, all fifteen model choices and all nine options-window filters plus the combined view. The two final reviewer verdicts and their closure are recorded below, separately from the frozen-run audit.

A Major integration provenance issue was found separately: checking absolute paths in the frozen configuration could verify the old checkout while executing changed integrated code. The integration guard now checks executing-tree source identity and must refuse that historical configuration before dataset access when those bytes differ. It must not repin old evidence or certify the frozen checkout on behalf of new code. The 59 focused checks passed (`/tmp/advanced-provenance-green.log`, identity retained in the bundle); the parent verified a CLI refusal identifying `dskit/pipeline/driver.py` before dataset access. Its exact CLI log identity and final reviewer closure remain to be appended. The frozen candidate, training and scores are unchanged.

No new confidence intervals, multiplicity correction or acceptance test were computed. One seed, one horizon, reused validation windows, differing hardware/batches and capacity-driven widths limit interpretation. More epochs show that optimization occurred; they do not establish improved future forecasts.

## Reproducibility and handoff

The four interactive reports are [pooled base](../reports/advanced-cdf-zoo-20261006/pooled-base.html), [unpooled base](../reports/advanced-cdf-zoo-20261006/unpooled-base.html), [pooled base + options](../reports/advanced-cdf-zoo-20261006/pooled-base-options.html), and [unpooled base + options](../reports/advanced-cdf-zoo-20261006/unpooled-base-options.html). The aggregate-only [final-verification.json](../reports/advanced-cdf-zoo-20261006/final-verification.json) records audit/UI summaries, all fifteen outcomes, prior comparisons, report hashes, source/dependency identities, limitations and final review closure.

Final aggregate data SHA256: `c75a30d8e98eea28ae881b72017d821e8cf75b8c7f48e9beb96dff4249775b27`. Local execution evidence remains under `/home/russell/wt/training-validation-audit/children/index_options/pipeline_runs/advanced-cdf-zoo-20261006/final-verification/`. The 16 MB per-fit reproducibility manifest is not copied into Git; its exact path/hash and extraction/capacity/report/prior-comparison recipe paths/hashes are retained in the bounded bundle. Retain that frozen workspace and local recipes. Running the historical training configuration from the changed integration checkout is expected to refuse.

Next authorized action: publish the reviewed release, verify all four reports on remote main, and remove the contained task branch. This memo does not authorize another training run, holdout evaluation, promotion or trading.


### Final delivery review closure

Candidate `5b150ca578b7eada44ff05aa5cbba03d138e18a7` passed two fresh independent lenses: correctness/authority (331 focused tests, zero Critical/Major/Minor) and tests/integration (1,288 fresh focused tests, zero Critical/Major). Reports regenerated byte-for-byte, 16 changed public modules imported without optional dependencies, and five main speedup methods retained identical syntax trees. The one integration Minor, INT-R2-01, was a missing stylesheet recipe dependency record; its exact local path, SHA256 and retention requirement are now recorded in final-verification.json as the reviewer-approved documentary correction. No code, tests, configs or report bytes changed after review.

The source-guard regression log and actual CLI refusal are retained with hashes in the verification bundle. Supplemental browser checks independently recomputed options/control and prior comparisons for all three metrics; all passed. The two full reviewer outputs, identities, scopes and limitations are embedded in the bundle. Final merge/push and contained remote-branch removal are owner-authorized; verify the release on remote main and consult the retained local remote-delivery.json receipt for exact release and deletion identifiers. This closure permits repository delivery, not another training run, holdout access or trading.
