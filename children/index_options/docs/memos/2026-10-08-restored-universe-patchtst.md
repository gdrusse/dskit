## TL;DR

All 66 new fits completed without failures. Feature selection and HPO retained the base PatchTST32 default; confirmation passed its frozen continuation gate. Across 99,225 paired 2024–2025 forecasts per seed, evaluation weighted-CRPS skills are +0.278858%, +0.240921% and +0.254481% for seeds 11/29/47. Seed11 remains nominal. Gains are modest, and its nominal95% interval covers only91.616% of outcomes. All stage recovery checks passed. The [inference inventory](../reports/production-universe-rerun-20261008/model-inventory.json) contains66 unique fitted packages. Closing reviews and publication are recorded below; no production qualification or calibrated uncertainty set is claimed.

## Frozen execution contract

The owner requested the existing zoo-style pooled PatchTST process with HPO and scheduled fresh retraining. Core model, experiment runner, feature families and training settings are unchanged from the recent 85-name cycle. CNN and other challenger work is deferred. Its unmerged prototype is retained privately; no new runtime code is in this candidate.

Five existing feature bundles are compared on three purged 2021–2022 development windows. HPO then compares learning rates 0.0001, 0.0003 and 0.001 with fusion dropout 0 or 0.1, keeping the existing PatchTST32 architecture and head width 2. Three exact selected-arm default cells reuse this cycle's Stage-1 checkpoints after identity verification. Nothing is reused from the earlier 85-name fits.

Frozen settings are confirmed on the three 2023 windows with seeds 11, 29 and 47. All three aggregate paired skills must be strictly positive before the nine 2024–2025 evaluation windows can run for each seed. Seed 11 remains nominal. Each refit starts fresh with all eligible earlier settled rows; recent settled data is appended and older eligible history stays. Last-40-origin early-stopping bands and selection-stage settlement purges are unchanged. No dedicated COVID tuning fold is used; eligible COVID history remains in later training.

Maximum new fits are 15 feature + 15 HPO + 9 confirmation + 27 conditional evaluation = 66. Prior conservative charges are 67, giving at most 133 within the original 216 ceiling. Pilots count; failed or interrupted fits do not authorize automatic retries. Full interrupted-optimizer continuation is not supported.

## Data admission and accounting

The panel contains 457,919 rows for 392 symbols. It restores 307 previously excluded names alongside the prior 85. Every restored name retains its missing-action-inventory status. Public searches covering all 308 excluded names are discovery evidence: a search without a confirmed event is not proof of no split.

CORZ is excluded for its confirmed January 23, 2024 equity cancellation and recapitalization, not mislabeled as a simple stock split. WULF's December 14, 2021 merger/cash/CVR event and ONDS's November 16, 2020 reverse-split trading date use explicit crossing-span exclusions. Other original action rules remain. The universe is retrospective, and close-feature publication clocks remain assumptions.

The source ledger reconciles all 841,559 owned source dates: 457,919 admitted, 365,940 exact nominal expiries falling off the exchange calendar, 8,624 nominal targets reaching 2026, 4,719 missing reference scales, 3,857 action-path exclusions, 487 excluded CORZ dates and 13 incomplete price paths. Residual: zero. All admitted spot/terminal prices, log-return targets and four liquidity features were checked against bounded source projections.

Older AMZN and MSFT sources remain included. MSFT adds 3,233 pre-2016 dates; every consumed bar field matches exactly over 2,514 overlapping dates. Small differences in unused adjusted_close are preserved and disclosed, not mixed into model fields. Dates span eligible quote history from 1997-06-17 through 2025-11-28, with settlements through 2025-12-29. No unavailable options data is invented.

The three development windows admit 303, 319 and 354 heads respectively because of history and label availability. Their held-out development identities total 86,328 per complete feature arm. These are forecasts, not independent observations.

## Why comparison with the zoo needs care

The earlier zoo's promising result and the recent negative result differed in universe, architecture width, features and evaluation/selection periods. This rerun holds the recent model protocol fixed while restoring the wider cohort, but reruns feature/HPO selection and adds older MSFT history. Improvement would be evidence that the expanded process works better here, not a causal proof that universe restriction alone caused the decline.

The final analysis reports the original 85 names and restored names separately on the same forecasts. This is descriptive and adds no model fits or selection rule. HPO includes its default candidate, so the complete development winner cannot have a worse development objective than that default; later windows can still perform worse.

## Verification and retention

Candidate 025f4bd5 passed two independent configuration/execution reviews with zero Critical/Major/Minor/Nit. The second lens independently recomputed all 15 feature/fold cells, verified 2,727 backup files and reran the source/config/dependency/panel recovery with original roots denied. Seven no-fit identity/rejection probes passed. Existing unchanged code reviews remain valid; no full suite or shared-environment upgrade was run.

The first actual fit used 16,991 parameters, selected epoch 28 and stopped after 48 epochs. Its 303 score shards passed hash checks; seven AAPL distributions and CRPS values reproduced from the second copy within rtol 1e-6 and atol 1e-7. This recovery sample is not a full re-score or a statistical qualification result.

Primary artifacts are under the private production-universe-rerun-20261008 data root; dated audit evidence is under production-development-audit-20261007/universe-rerun-20261008. Backups are beside existing backups under production-universe-rerun-20261008. These copies share a physical disk and are not disaster recovery. Source snapshots, trial configurations, checkpoint weights, per-forecast records and failures remain private; GitHub receives only configuration and aggregate documentation.

## Uncertainty handoff and independent qualification

Keep full fitted mixture distributions and settled chronological outcomes, with model/checkpoint, feature order, head mapping, reference scale and source identities. These are per-ticker marginal distributions; shared pooled training does not produce a joint cross-ticker distribution. A future portfolio-level use requires a separately specified dependence contract. For future MIO use, the proposed coherent set places nonnegative masses q on one increasing terminal-price grid, with sum(q)=1. Its cumulative masses define a single monotone CDF. The spot-normalized discrete Wasserstein-1 distance from nominal p is the sum of each price-grid gap times the absolute cumulative-mass difference, divided by entry spot. Price grid and spot share quote-currency units; radius is dimensionless.

Any future handoff must contain nominal probabilities, grid/spot and units, radius and calibration identity, optional separately validated CDF bands, model/checkpoint identity, support/grid policy and fallback. Bands constrain that same cumulative distribution and must preserve nonemptiness. Preserve full forecast tails; payoff-specific finite clipping is not a general tail guarantee. No MIO solver or decision-layer study is implemented or run here.

Nine existing synthetic arithmetic/format assertions passed again for this rerun: normalized transport distance, identity, price/spot unit invariance and rejection examples for invalid masses, order, radius and bands. This is a finite-support format example, not a production validator, empirical radius, discretization guarantee or MIO execution.

Early-stopping data is not independent calibration. The repeatedly inspected 2021–2025 periods are developmental. A numerical radius, validated CDF bands and a confidence guarantee require separately justified post-selection calibration evidence and a predeclared criterion; a capped-payoff radius study also requires the excluded decision layer. Until then, radius and calibration identity remain null and future decision use must abstain. Descriptive PIT and interval coverage do not fill that gap.

Before any separately authorized next stage, freeze a model/checkpoint, feature and universe policy, nominal seed, baseline, loss metric, label-maturity lag and acceptance rule. Use a designated untouched chronological calibration interval, then a later untouched validation interval; keep 2026 closed unless explicitly authorized. Synchronize date blocks across tickers, primary length 60 trading dates with 30-trading-date sensitivity, and check calendar span covers the 31-calendar-day target horizon. Freeze any radius/band choices before validation. These60/30-date blocks describe the forecast-comparison proposal; calibration must also predeclare a sensitivity block longer than its primary block, as the Claude handoff explains. A 95% paired date-block interval whose lower improvement bound exceeds zero would support the primary forecast comparison; insufficient independent blocks yield an inconclusive result. Predeclare support/fallback rules and do not tune on validation, choose a better seed afterwards or infer qualification from nine aggregate window totals.

The concrete next-stage proposal retains the previous cycle's starting design: 126 eligible exchange sessions for calibration, wait until all labels plus the frozen publication lag mature, then 252 later sessions for validation and wait again. Exact dates and point-in-time membership must be fixed before accessing those observations. Keep protected 2026 entirely closed, including any feature lookback; a post-2026 start needs enough later warm-up history. These durations are proposed collection targets, not evidence of enough independent information. Prespecify the primary paired weighted-skill gate and a tail-loss gate (upper 95% bound for relative tail-loss deterioration below the previously proposed 0.5% margin). Too few effective date blocks or intervals too wide to decide means inconclusive, not automatic qualification or extra development fits. Radius calibration still needs its own approved criterion and evidence.

## Final memo requirements — owner follow-up

Include training performance (saved loss/monitoring histories, best/total epochs,
early stopping and fit duration), every HPO candidate's paired development result
and the selection rationale, confirmation/validation and conditional evaluation
results by fold and seed, and per-ticker forecast performance. Report forecast
counts, ordinary/tail/weighted CRPS and paired empirical-reference skill where
available. Show original85 versus restored names separately; retain failures,
missing/ineligible results and the distinction between training, selection and
later evidence. Include a complete per-ticker aggregate appendix or linked report,
not only best/worst names. Describe only metrics actually retained; do not invent
training accuracy, independent calibration or causal universe attribution.
This is a reporting clarification, not an extra fit or changed selection rule.

## Feature-stage results

All five arms completed three folds with 86,328 paired forecasts each, with no failed cells. Weighted skills were base +0.878415%, minus momentum +0.781624%, minus liquidity +0.742713%, minus directional +0.827032% and minus long volatility +0.740148%. The frozen minimum summed loss-ratio rule selected base; there was no post-result rule change.

On this new base model's saved forecasts, the original 85 names have 21,297 forecasts and +0.503541% skill; the restored cohort has 65,031 forecasts and +1.002395% skill. Both cohorts improve on their paired empirical references. This is a within-rerun descriptive split, not a controlled estimate of the universe change.

The private features-report-appendix-v2.json includes all 393 configured names for each of five arms, including CORZ and names without eligible forecasts: 1,965 ticker aggregate rows, plus all 15 training/monitor curves and the frozen admission counts. Aggregate sums reconcile to the verified stage result. The published appendix retains aggregate-only records.

Stage1 recovery verified all 15 fitted packages and 4,880 score shards, then reproduced seven AAPL forecasts from base/fold3 with original source/data/environment paths blocked. The bundle contains 29,565 files and 386,530,478 bytes; no original was removed. HPO conformance review passed with zero findings. The 15 new HPO fits were reserved at a cumulative ceiling of 97/216 and launched once, alongside three exact same-cycle Stage1 references.

## Matched comparison with the prior 85-name cycle

An additional read-only audit matched all 21,297 original-cohort development forecasts from the old base model to this rerun's base model. Forecast identities, settlements and every empirical-baseline loss are identical. Old weighted loss was 21,673.776421 against baseline 21,515.304265; new loss is 21,406.965914. Weighted skill therefore changes from -0.736555% to +0.503541%, a 1.231029% reduction in model loss. Ordinary and tail skills change from -0.539750%/-1.081683% to +0.717610%/+0.128139%.

This verifies improvement on the same original-cohort development questions, rather than only a different mix of scoring tickers. The training pool and older MSFT history changed together, along with the number of pooled heads; it is still not a controlled estimate of universe alone. It compares base against base before HPO and does not establish later-period or zoo-protocol equivalence. All 85 names remain in the aggregate appendix, including zero-forecast entries with null skills.

Two initial read-only reporting attempts refused because the selected columns omitted settlement_date and because a zero-forecast ticker had a zero loss denominator. The projection and null-skill handling were corrected; original scripts/logs remain private. These were analysis errors, not failed model fits or data changes. The completed comparison is old-new-base-original85-comparison.json. Independent review verified all six fit identities, 486 score shards and aggregate/fold/ticker arithmetic, with zero findings. Comparison inputs, corrected/initial recipes, logs, results and review are copied with verified hashes in matched-comparison-addendum.

## Saved training and early stopping

The selected base arm's actual saved composite losses (CRPS plus tail CRPS on standardized log returns) are:

- Fold 1: online training loss 1.162686 to 1.063373; best monitoring loss 1.030305 at epoch 28, stopped after 48 epochs; fit 159.0 seconds.
- Fold 2: online training loss 1.148830 to 1.055936; best monitoring loss 0.917657 at epoch 23, stopped after 43 epochs; fit 161.7 seconds.
- Fold 3: online training loss 1.139720 to 1.049333; best monitoring loss 1.032192 at epoch 17, stopped after 37 epochs; fit 161.5 seconds.

Training values are online minibatch-weighted epoch losses with dropout active, not a re-score of the restored best checkpoint. Monitoring uses the purged early-stopping band. Every base fit stopped 20 epochs after its selected monitoring epoch, and the best checkpoint was restored. These monitoring values are not independent calibration evidence; they also use different observations from the paired development scores.

## HPO results and selection

All six candidates completed the same 86,328 paired development forecasts. Fifteen new fits completed with zero skipped/failed cells; three exact Stage1 fits were reused. The minimum summed weighted-loss ratio selected the original base default, so HPO did not degrade the development objective and did not improve it.

- trial0: learning rate 0.0001, fusion dropout 0.0, weighted skill +0.779288% (three new fits).
- trial1: learning rate 0.0001, fusion dropout 0.1, weighted skill +0.856526% (three new fits).
- trial2: learning rate 0.0003, fusion dropout 0.0, weighted skill +0.735892% (three new fits).
- trial3: learning rate 0.0003, fusion dropout 0.1, weighted skill +0.878415% (selected; three verified Stage1 references).
- trial4: learning rate 0.001, fusion dropout 0.0, weighted skill +0.584287% (three new fits).
- trial5: learning rate 0.001, fusion dropout 0.1, weighted skill +0.606249% (three new fits).

The selected settings are PatchTST32/head2 with all 42 base features, learning rate 0.0003 and fusion dropout 0.1. Backbone settings are unchanged. Selection used no confirmation/evaluation data. All trial training curves, per-fold scores and 2,358 complete configured-ticker/candidate aggregate rows are retained in hpo-report-appendix-v2.json. Its 18 trial/fold records include three reused fits, not 18 new fits. At HPO completion this cycle had completed 30 unique fits; cumulative conservative charge was 97/216.

## HPO recovery and confirmation launch

Independent conformance review verified all six HPO score datasets, 18 fit references, the default selection and all nine resolved confirmation cells with zero findings. The HPO backup contains 29,687 files and 399,935,021 bytes. All 15 new HPO fits and 4,880 score shards verified; seven trial5/fold3 AAPL forecasts reproduced with original paths denied. This fixed recovery probe is not the selected winner. The selected default's Stage1 backup separately reproduced base/fold3 inference.

Nine fresh confirmation fits launched once after both gates passed. Each seed has planned forecast counts 16,245, 17,787 and 17,196 across the three folds (51,228 total). Parameter counts are 18,854, 18,908 and 18,962, within the unchanged ceilings. The final confirmation settlements end January 29, 2024, before the evaluation boundary. At that launch no confirmation result or Stage4 eligibility was claimed. Conservative cumulative reserved ceiling was 106/216.

## Confirmation results

All nine fresh fits completed without failures/skips. Each seed has 51,228 paired forecasts across three 2023 windows:

- Seed 11: +1.331748% weighted skill; fold skills +1.632178%, +0.964598%, +1.422019%.
- Seed 29: +0.818424% weighted skill; fold skills +1.550833%, -0.250455%, +1.202415%.
- Seed 47: +1.122986% weighted skill; fold skills +1.756640%, +1.131666%, +0.574877%.

Every seed's summed paired weighted loss is below its empirical reference, so the prespecified strictly-positive-all-three-seeds gate passes. This is a developmental continuation gate, not a significance or production-qualification test. Nominal seed remains 11; no better-seed switch is allowed. The complete training, fold, cohort and 1,179 configured-ticker/seed aggregate rows are in confirmation-report-appendix-v2.json.

Confirmation cohort differences remain visible. Original85 weighted skills are -0.043181%, +0.170570% and +0.049018% for seeds11/29/47 (11,171 forecasts each); restored-cohort skills are +1.688151%, +0.986357% and +1.401374% (40,057 each). Seed29's second window is negative (-0.250455%). The frozen gate concerns each seed's aggregate, not every subgroup/window. None of these observations authorizes removing weak tickers or changing settings.

Nominal confirmation central-interval coverage is 24,742/51,228 (48.298%) for a nominal50%, 39,866/51,228 (77.821%) for80%, 47,542/51,228 (92.805%) for95%, and49,898/51,228 (97.404%) for99%. Mean PIT is0.504408; tail/interval undercoverage remains visible despite positive weighted skill. These are descriptive, dependent development observations, not independently calibrated CDF bands or a confidence guarantee. No post-result correction or radius is fitted.

The backup contains 20,516 files and 278,689,524 bytes. All nine fits and 3,366 score shards verified; seven AAPL forecasts from nominal seed11/fold3 reproduced from the second copy with original roots denied. Actual-result and evaluation-configuration independent reviews both closed with zero findings. Fresh gate and candidate-freeze receipts bind the current results, recovery, chosen configuration and fixed nominal seed. This was the pre-evaluation transition; completed evaluation results appear below.

## Recovery instructions for saved inference packages

Use WSL2. Keep the new backup root /home/russell/data/backups/index_options/production-universe-rerun-20261008 and the earlier production-development-20261007 backup root. The latter contains the unchanged Python environment and retained library runtime; the new prerequisites-v2 bundle contains source and model inputs. A stage bundle alone is not a portable, self-contained environment.

For a fresh verification, create a new sibling directory under the new backup root. Copy only the completed stage bundle's recovery directory and members.json into it, verify every recorded member hash, and run its archived restore-production-<stage>-v1.py (Stage1 uses restore-production-stage1-v2.py) using /usr/bin/python3 -B -S, with the fresh directory as its argument. Do not copy an existing recovery-verification.json into the fresh destination: the recipe deliberately refuses overwriting that receipt. The sibling location preserves the recipes' prerequisites-v2 lookup; moving to another host/path requires an explicitly checked relocation of dependency paths.

The archived recipes deny access to original source/data/environment roots, verify identities and checkpoint/score hashes, and reproduce a seven-forecast inference/scoring sample. Keep their full output and new recovery receipt. They do not refit models or recover an interrupted optimizer. Each model needs its exact feature order, training medians/scaler, constant-feature mask and supported ticker-head map; unsupported heads must refuse. These checks are representative inference recovery, not a full forecast rescore or production qualification.

Retain all chronological refit packages and their fixed nominal seed. Do not substitute the best later-scoring seed/checkpoint. The prior 85-name cycle remains available as historical research/rollback evidence, not a qualified live fallback. Until a model, uncertainty set and fallback are separately qualified, the future decision interface must abstain. No cleanup has been performed; same-disk second copies do not provide off-device disaster recovery.

## Execution results and pickup

The finite experiment cycle is complete:15 feature +15 new HPO +9 confirmation +27 evaluation fits, with no new-cycle fit failures. Three HPO references reuse this rerun's Stage1 packages. All stage backups passed recovery checks. No fit job remains, and this closed cycle authorizes no further retries/search. Preserve the pinned checkout and all source/fit/forecast artifacts. Both scoped closing reviews closed with zero findings. Use the linked Claude memo for the next-session calibration walkthrough; actual publication and branch cleanup are recorded in the private final-wrap-receipt.json.


## Evaluation dispatch

All 27 evaluation configurations passed independent conformance review against the selected settings, nine purged windows, paired identities and pre-2026 boundaries. Expected forecasts are 99,225 per seed; per-fold parameter counts range from 19,016 to 19,394, within the frozen ceilings. The finite batch launched once after actual confirmation-gate/recovery verification, with a reserved cumulative ceiling of 133/216. Seed11 remains nominal. Runtime identity is recorded in the private evaluation-batch-launch.json; no results or production qualification are inferred from a successful launch.


## Complete aggregate appendices

The following aggregate-only artifacts include every configured ticker, including explicit exclusions and zero-forecast/ineligible entries. Positive skill means lower loss than the paired empirical reference, not a financial return. Each stage preserves ordinary, tail and weighted losses/counts, original85/restored307 cohorts, exact training and monitoring curves, epoch selection and admission evidence.

- [Feature selection: all five arms and 1,965 ticker rows](../reports/production-universe-rerun-20261008/features-appendix.json).
- [HPO: all six candidates and 2,358 ticker rows](../reports/production-universe-rerun-20261008/hpo-appendix.json).
- [Confirmation: all three seeds and 1,179 ticker rows](../reports/production-universe-rerun-20261008/confirmation-appendix.json).
- [Matched old/new base comparison: all original85 names](../reports/production-universe-rerun-20261008/old-new-original85-comparison.json).

The complete evaluation [ticker/training appendix](../reports/production-universe-rerun-20261008/evaluation-appendix.json) and [fold/seed summary](../reports/production-universe-rerun-20261008/evaluation-summary.json) are now included. The HPO appendix repeats the three verified Stage1 reference records for traceability; these must not be counted as new fits. No raw source records, per-forecast datasets or model weights are in these appendices.

## Where the models are for inference

All paths below are inside WSL2. Fitted weights stay in private data storage; GitHub receives the report, aggregate evidence and model locations/identities.

The preserved rerun packages live under `/home/russell/data/index_options/production-universe-rerun-20261008/runs/`. Evaluation uses `evaluation/seed11/pooled/base/PatchTST32/<fold>/pooled/fit/` for the nominal model at each of the nine chronological cutoffs. Seeds29 and47 are stability checks, not alternatives to select after inspecting evaluation. A completed package contains `complete.json`, outer `metadata.json`, `model/state.json` and `model/weights.pt`. The complete inventory identifies the immutable snapshot, exact file hashes and backup path for every refit. All27 evaluation packages are recovered and available for inference.

The earlier verified confirmation seed11/fold3 package is retained at: `/home/russell/data/index_options/ob/raw/universe-confirmation-runs-20261008/20261008T171158Z-backfill-ffb477d9/payload/files/seed11/pooled/base/PatchTST32/3/pooled/fit/`. Its fit identity is `86c44ca9422984a7951ac374a716313056bbb304d8fef9bd56169caa1484f217` and checkpoint identity is `9c02cc91ff670276c80d4cf526905a852097d6ad7bf02da0a7ece5d35feb6f15`. It supports376 ticker heads and42 ordered base features; the full392-name universe does not imply every historical checkpoint supports every name.

The runtime is `/home/russell/dskit/.venv/bin/python`, source checkout `/home/russell/dskit-production-universe-rerun-20261008`, and retained library runtime `/home/russell/data/index_options/production-development-retained-20261007/historical-advanced/runtime`. Preserve these pinned dependencies, or use their verified backup copies described above. Load the full fitted package through the existing `TorchCDF.load_checkpoint(<fit>/model)` seam and reproduce the archived recovery recipe's input preparation: exact feature order, saved training medians and the checkpoint's ticker-head encoding, then call the loaded model so its saved scaler and feature mask apply exactly once. Do not infer preprocessing or head positions from a different retraining date.

The saved output is the fitted mixture for standardized log return. Restore terminal-price units using the matching forecast reference scale and entry spot. The uncertainty handoff specifies how this nominal distribution relates to a coherent future uncertainty set. Model inference availability does not supply the missing calibrated radius, validated bands or independent qualification. The owner's next-stage MIO intent is recorded; this development cycle prepares its inputs without executing that next stage.

The fixed latest evaluation reference is **seed11/fold9**, with390 supported heads,42 ordered features and19,340 parameters. Its immutable onboarded fit is:
/home/russell/data/index_options/ob/raw/universe-evaluation-runs-20261008/20261008T202544Z-backfill-28ec35cd/payload/files/seed11/pooled/base/PatchTST32/9/pooled/fit.
Source universe-evaluation-runs-20261008, stream files, snapshot 20261008T202544Z-backfill-28ec35cd. Fit identity 7336c15cbb9ec3989d852ea9a7e0fc15317a3be2988fbe7c79badb6d4b4d6894; checkpoint identity 1ec5ee6e18d056744215e5a482813f94b7c167cbdb4e9317f548c155dbff29b0.
Its verified second copy is /home/russell/data/backups/index_options/production-universe-rerun-20261008/evaluation-complete/recovery/runs/evaluation/seed11/pooled/base/PatchTST32/9/pooled/fit.

This package trained through quote2025-04-29/settlement2025-05-30, monitored through quote2025-09-09/settlement2025-10-10, then forecast2025-10-13–11-28. Best epoch61 was restored after81 total epochs. Never use this later checkpoint for earlier historical decisions. All earlier nominal cutoffs are retained for chronological inference; the latest pointer is fixed by cutoff, not chosen from later scores.

The [model inventory](../reports/production-universe-rerun-20261008/model-inventory.json) records exact immutable source/backup paths, fit/checkpoint and constituent file hashes, input contracts, head/feature counts and training bands. The final inventory covers66 unique completed fits, including all27 evaluation packages, and verifies every listed original/backup constituent hash including complete.json. The three HPO reuse references point to existing entries instead of being counted twice. No fitted parameter values or weights are published.

The owner-requested [Claude calibration and backtest handoff](2026-10-08-claude-calibration-backtest-handoff.md) lays out the next-session decisions, calibration requirements, chronological replay contract and MIO/backtest gates. It is a future-stage plan; no MIO or strategy backtest was executed here.

The Claude handoff received an independent factual/authority review. One minor distinction between forecast-comparison block lengths and the longer calibration sensitivity check was corrected; the scoped correction review closed with zero unresolved findings. Reviewed memo SHA256:260a92a8b3a658b8b07a788a586b1ac4644da686f9886e2d8ff442cfbf245e29. Both review receipts and the exact memo are hash-verified in the private next-session-handoff-reviewed backup. This does not approve future model/calibration/MIO implementation.

## Owned stock-option prices for the next stage

Metadata/directory inspection located two existing stores: original stock universe at `/home/russell/wt/index-options-iterative-validation/children/stock_options/pipeline_runs/option-universe-100/root`, and added300 universe at `/home/russell/data/stock_options/option-universe-300`. Both have source `options`, with raw acquisitions under `raw/options/` and normalized observations under `observations/options/`. The configured collection is contract metadata plus daily option trade bars for30–45 nominal days to expiry, starting2024-02-01; this is not complete historical executable bid/ask coverage for every strike/expiry/ticker. Fees, spreads and slippage are separate backtest inputs. No protected2026 option rows were opened to establish these locations.

The original store remains inside a historical worktree: preserve that directory, inventory its manifest/coverage and onboard/retain any relocation before using it across future runs. Do not delete it as checkout cleanup. This directory/config discovery does not certify every configured ticker or put/call is present, point-in-time availability, or matching horizons. The separate `/home/russell/data/options_archives/philippdubach_full` archive is SPY/QQQ/IWM history, not general392-stock chain coverage. Forecast model fitting here uses underlying prices; option-price acquisition is not part of the current rerun.

## Proposed dates for the next-session MIO study

At the owner's request for a date recommendation, the proposed retrospective split is calibration forecast dates2024-02-06 through2024-12-31, wait for their31-day labels through2025-01-31 plus verified publication lag, then backtest entries2025-02-04 through2025-11-28 with settlements no later than2025-12-29. These are proposed bounds, conditional on actual forecast/contract/quote coverage and availability; they are not a completed coverage census, approved execution config or guarantee of adequate independent sample size.2026 remains closed.

This uses2024 because the stock-option collection is configured to start2024-02-01; matching2023 stock-option prices have not been established. The2023 forecasts remain supporting distribution diagnostics. Restrict decisions to contracts whose expiry and observation clock match the validated forecast horizon; the30–45DTE collection does not make the31-day forecast valid at every collected maturity.

Calibrate rho first against a predefined robustness criterion, then admit Q bands only if justified. Freeze the calibration method before the2025 replay. A predeclared monthly update may use only then-settled, available prior outcomes, with fixed pooling, window, criterion and fallback rules. This tests a fixed adaptive procedure, not a constant rho; distinguish it from a separate fixed-parameter validation claim. Never rewrite earlier decisions or choose the method from later profits. Keep date-block dependence, simultaneous-band limitations, price-grid/tail and executable-quote assumptions visible.

This future decision-study split does not change the completed2024–2025 forecasting evaluation, model/seed selection, fit budget or existing artifacts. It remains retrospective research because these periods have informed development. No MIO, calibration fit, optimizer or strategy backtest was executed to prepare this recommendation.


## Final evaluation results

All 27 fresh evaluation fits completed, with zero failures or skipped cells. Each seed forecasts the same 99,225 identities; all paired empirical-reference values match exactly across seeds. There are 297,675 seed/forecast records, not 297,675 independent observations. All quote and settlement dates remain before2026. Selection, nominal seed and settings were frozen before this stage.

- Seed 11: weighted skill +0.278858%; ordinary +0.315857%; tail +0.213234%. Fold1–9 weighted skills: -0.616739%, +1.082882%, +0.419183%, -0.609946%, +0.403913%, +1.096829%, +0.350068%, +0.448160%, -0.001174%.
- Seed 29: weighted skill +0.240921%; ordinary +0.266795%; tail +0.195030%. Fold1–9 weighted skills: -1.211563%, +1.175047%, +0.388843%, -0.514958%, +0.542779%, +1.120079%, +0.176927%, +0.450462%, +0.101223%.
- Seed 47: weighted skill +0.254481%; ordinary +0.255980%; tail +0.251821%. Fold1–9 weighted skills: -0.438538%, +0.929089%, +0.331204%, -0.361083%, +0.536512%, +0.790219%, +0.508730%, -0.188421%, +0.403564%.

Forecast windows and counts per seed: fold1 2024-02-06–04-22 (11,306); fold2 2024-04-23–07-08 (11,299); fold3 2024-07-09–09-20 (11,366); fold4 2024-09-23–12-02 (11,444); fold5 2024-12-03–2025-02-21 (11,452); fold6 2025-02-24–05-09 (11,587); fold7 2025-05-12–07-28 (11,652); fold8 2025-07-29–10-10 (11,729); fold9 2025-10-13–11-28 (7,390). Final settlements end2025-12-29.

Nominal weighted loss is102,651.038837 versus paired empirical102,938.089597. Six of nine windows and185 of392 tickers have positive nominal skill; positive aggregate performance does not imply broad ticker-level dominance. Nominal fold1 (-0.616739%), fold4 (-0.609946%) and fold9 (-0.001174%) remain negative. Seeds29/47 have7/6 positive windows and188/199 positive tickers. Every leave-one-window-out aggregate remains positive; this is descriptive sensitivity, not a significance test. Full fold/seed sums and diagnostic records are in the [evaluation summary](../reports/production-universe-rerun-20261008/evaluation-summary.json).

Nominal interval coverage is47,315/99,225 (47.685%) at50%, 75,444 (76.033%) at80%, 90,906 (91.616%) at95%, and96,079 (96.829%) at99%. Mean PIT is0.513995; its upper decile has13,166 outcomes versus9,922.5 under an ideal uniform reference. These overlapping outcomes show descriptive tail/interval undercoverage. Seed29 had one floating-point CDF overshoot of2.22e-16, within the predeclared8-machine-epsilon tolerance; the raw bound and adjustment are retained. No substantive probability correction, radius or band was fitted.

The default won HPO on development dates; later-period skill is smaller than development (+0.878415%) and nominal confirmation (+1.331748%). HPO guarantees only that the selected complete candidate does not lose to its included default on that selection objective. It cannot guarantee later improvement. The earlier zoo, old85 cycle and this restored-universe cycle use different protocols; the matched original85 development comparison is the only exact identity/baseline comparison reported here.

## Evaluation cohorts and saved training

- Seed 11: original85 21,587 forecasts, weighted skill +0.106270%; restored307 77,638 forecasts, weighted skill +0.326588%.
- Seed 29: original85 21,587 forecasts, weighted skill +0.118338%; restored307 77,638 forecasts, weighted skill +0.274822%.
- Seed 47: original85 21,587 forecasts, weighted skill -0.112423%; restored307 77,638 forecasts, weighted skill +0.355951%.

Seed47 original85 remains negative (-0.112423%); the universe restoration does not make every cohort/seed improve. No ticker or seed is removed after evaluation. The [complete evaluation appendix](../reports/production-universe-rerun-20261008/evaluation-appendix.json) includes all1,179 configured ticker/seed rows, 27 training records and exact per-fold admission counts/reasons. CORZ is an explicit exclusion. All cohort/ticker loss sums reconcile to the stage results.

Nominal seed11 saved training histories by fold (online first→last training loss; best monitoring loss; best/total epochs; fit duration):

- Fold1: 1.109940→1.036738; 1.081841; 8/28; 180.3s.
- Fold2: 1.112168→1.030765; 1.012473; 29/49; 318.3s.
- Fold3: 1.113080→1.032238; 0.998442; 32/52; 349.8s.
- Fold4: 1.109061→1.028380; 0.996931; 42/62; 429.0s.
- Fold5: 1.106537→1.030002; 1.019873; 33/53; 398.3s.
- Fold6: 1.101961→1.023384; 1.163661; 64/84; 636.3s.
- Fold7: 1.110367→1.036671; 1.111784; 11/31; 239.8s.
- Fold8: 1.106646→1.036951; 0.870374; 12/32; 251.0s.
- Fold9: 1.108060→1.026857; 1.049701; 61/81; 675.2s.

The complete appendix retains every epoch for all three seeds. These are saved online training losses and purged early-stopping losses, not training accuracy, restored-checkpoint training re-scores or independent calibration. The final nominal fit has423,197 training rows,15,566 monitoring rows and7,390 evaluation forecasts,19,340 parameters and390 supported heads. Its training labels end2025-05-30; monitoring labels end2025-10-10 before the first forecast2025-10-13.

## Recommendation and qualification boundary

Retain the frozen base PatchTST32 configuration and nominal seed11 as the forecasting research candidate for the next calibration walkthrough. Its development winner remained the included default; all confirmation and evaluation seed aggregates beat their empirical references. Evaluation gains are small, less than half of nominal ticker aggregates are positive, and interval coverage is materially below nominal. Do not promote it to a production-qualified champion from these results.

There is no qualified learned reserve from this single-family cycle. Preserve seeds29/47 as stability evidence, all feature/HPO trials as research artifacts, and the old85 cycle as historical rollback evidence; none is an evaluation-selected replacement. The paired empirical forecast remains the research comparator, not an automatically qualified live fallback. Future decision use must abstain until the model, uncertainty calibration, data/clock contract and fallback have separately passed their stated qualification gates.

Implemented and exercised here: existing DS Kit JSON configurations, source reconciliation/admission, finite feature/HPO selection, frozen confirmation/evaluation, chronological checkpoint retention and aggregate reporting. Tested here: configuration/identity/purge/paired-baseline checks, checkpoint/score hashes and nine synthetic uncertainty-format checks. Empirically observed here: the reported developmental forecast losses, cohort/ticker variation and interval undercoverage. Proposed only: rho/Q-band calibration, future independent qualification and the next-session MIO/backtest study. No MIO solver, optimization, trading backtest, deployment or2026 access occurred.

All66 new fits completed without failure; with the preserved prior67 charges, the cumulative conservative charge is133/216. The three HPO default references are reused Stage1 identities, not three extra fits. No automatic retries, model additions or budget expansion occurred. A report-only field-name error while rendering training prose was corrected using the existing appendix schema; its private reporting-events.jsonl entry is retained, with no effect on models, forecasts or selection.

## Final recovery and publication record

Evaluation recovery verified62,903 copied files (520,946,894 bytes), all27 fit identities and10,383 score shards. It reproduced seven AAPL forecasts and CRPS values from fixed nominal seed11/fold9 with original source, data, runtime and worktree roots denied. Maximum mixture/metric differences remained within rtol1e-6 and atol1e-7; no fit was started. Receipt SHA256:90bd47449fbea8cc86d73eb54f6d11ae8cd02c97105928b55a60d6d1925e2dec.

The exact verified recovery invocation was:

```text
/usr/bin/python3 -B -S /home/russell/data/backups/index_options/production-universe-rerun-20261008/evaluation-complete/recovery/audit/restore-production-evaluation-v1.py /home/russell/data/backups/index_options/production-universe-rerun-20261008/evaluation-complete
```

Do not rerun against that completed destination: it refuses replacing its receipt. For another recovery test, use a fresh sibling copy as described above. Preserve prerequisites-v2 and the old cycle's dependency backup. Same-disk copies are not off-device disaster recovery.

The original execution candidate025f4bd5 and all66 pinned source files remain unchanged; core/tests are byte-identical to d4df4ab0. Only JSON experiment instances, documentation, aggregate reports and journal evidence were added in this lane. All new report data is aggregate-only; weights, source records, per-forecast data and private receipts remain outside GitHub. Two fresh scoped correctness/authority and integration/evidence reviews closed candidate d9ec2b8dba7116109cdc46a481dfb40d94bdf034, each with zero Critical/Major/Minor/Nit. No correction was required; subsequent changes append closure evidence and update completion status only.


Final reviewers: /root/final_report_correctness (GPT-6), report final-closeout-correctness.txt SHA256a17c202d7f97f6b95a334a530ff32fb98755a6b9d7683dfa486f5f670b1fb0a7; /root/final_report_integration (GPT-6), report final-closeout-integration.txt SHA2562f96246cc043c63795a19ba5fbb27b3e41252219fc42eab3c8d17ae2a18e224b. Exact reports and final-review-lock.json are retained under the private audit root. The first lens independently checked all evaluation records/aggregates/PIT and69 training occurrences; the second checked all66 packages, backup manifests,190 dependency versions and41,967 retained dependency files, publication and handoff boundaries. No fits, full suite or future-stage execution were added by review.

The owner authorized /wrap, main merge/push and safe task-branch purge. The private final-wrap-receipt.json records actual remote verification and branch cleanup. Preserve all historical and current inference/data/dependency artifacts and the pinned checkout; purge refers only to safely merged task branch references. The next owner prompt will define the MIO portion.
