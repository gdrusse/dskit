## TL;DR

Execution is in progress. All 15 feature fits completed; base features won with +0.878415% paired weighted-CRPS skill in development. HPO completed and retained the default; confirmation passed the frozen numerical continuation gate; the final 27 evaluation refits are running. This improves on the earlier development result, but evaluation remains unresolved and this is not production qualification.

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

Before any separately authorized next stage, freeze a model/checkpoint, feature and universe policy, nominal seed, baseline, loss metric, label-maturity lag and acceptance rule. Use a designated untouched chronological calibration interval, then a later untouched validation interval; keep 2026 closed unless explicitly authorized. Synchronize date blocks across tickers, primary length 60 trading dates with 30-trading-date sensitivity, and check calendar span covers the 31-calendar-day target horizon. Freeze any radius/band choices before validation. A 95% paired date-block interval whose lower improvement bound exceeds zero would support the primary forecast comparison; insufficient independent blocks yield an inconclusive result. Predeclare support/fallback rules and do not tune on validation, choose a better seed afterwards or infer qualification from nine aggregate window totals.

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

The private features-report-appendix-v2.json includes all 393 configured names for each of five arms, including CORZ and names without eligible forecasts: 1,965 ticker aggregate rows, plus all 15 training/monitor curves and the frozen admission counts. Aggregate sums reconcile to the verified stage result. The final published appendix will retain aggregate-only records.

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

The backup contains 20,516 files and 278,689,524 bytes. All nine fits and 3,366 score shards verified; seven AAPL forecasts from nominal seed11/fold3 reproduced from the second copy with original roots denied. Actual-result and evaluation-configuration independent reviews both closed with zero findings. Fresh gate and candidate-freeze receipts bind the current results, recovery, chosen configuration and fixed nominal seed. No evaluation result is claimed.

## Recovery instructions for saved inference packages

Use WSL2. Keep the new backup root /home/russell/data/backups/index_options/production-universe-rerun-20261008 and the earlier production-development-20261007 backup root. The latter contains the unchanged Python environment and retained library runtime; the new prerequisites-v2 bundle contains source and model inputs. A stage bundle alone is not a portable, self-contained environment.

For a fresh verification, create a new sibling directory under the new backup root. Copy only the completed stage bundle's recovery directory and members.json into it, verify every recorded member hash, and run its archived restore-production-<stage>-v1.py (Stage1 uses restore-production-stage1-v2.py) using /usr/bin/python3 -B -S, with the fresh directory as its argument. Do not copy an existing recovery-verification.json into the fresh destination: the recipe deliberately refuses overwriting that receipt. The sibling location preserves the recipes' prerequisites-v2 lookup; moving to another host/path requires an explicitly checked relocation of dependency paths.

The archived recipes deny access to original source/data/environment roots, verify identities and checkpoint/score hashes, and reproduce a seven-forecast inference/scoring sample. Keep their full output and new recovery receipt. They do not refit models or recover an interrupted optimizer. Each model needs its exact feature order, training medians/scaler, constant-feature mask and supported ticker-head map; unsupported heads must refuse. These checks are representative inference recovery, not a full forecast rescore or production qualification.

Retain all chronological refit packages and their fixed nominal seed. Do not substitute the best later-scoring seed/checkpoint. The prior 85-name cycle remains available as historical research/rollback evidence, not a qualified live fallback. Until a model, uncertainty set and fallback are separately qualified, the future decision interface must abstain. No cleanup has been performed; same-disk second copies do not provide off-device disaster recovery.

## Execution results and pickup

Pending: complete the 27 evaluation fits already running; preserve/recover evaluation; append verified evaluation/calibration results and final recommendation. Current launch/status receipts are authoritative. Never relaunch a running job or delete this pinned checkout. The final memo will distinguish completed empirical evidence from assumptions and future work.


## Evaluation dispatch

All 27 evaluation configurations passed independent conformance review against the selected settings, nine purged windows, paired identities and pre-2026 boundaries. Expected forecasts are 99,225 per seed; per-fold parameter counts range from 19,016 to 19,394, within the frozen ceilings. The finite batch launched once after actual confirmation-gate/recovery verification, with a reserved cumulative ceiling of 133/216. Seed11 remains nominal. Runtime identity is recorded in the private evaluation-batch-launch.json; no results or production qualification are inferred from a successful launch.


## Complete aggregate appendices

The following aggregate-only artifacts include every configured ticker, including explicit exclusions and zero-forecast/ineligible entries. Positive skill means lower loss than the paired empirical reference, not a financial return. Each stage preserves ordinary, tail and weighted losses/counts, original85/restored307 cohorts, exact training and monitoring curves, epoch selection and admission evidence.

- [Feature selection: all five arms and 1,965 ticker rows](../reports/production-universe-rerun-20261008/features-appendix.json).
- [HPO: all six candidates and 2,358 ticker rows](../reports/production-universe-rerun-20261008/hpo-appendix.json).
- [Confirmation: all three seeds and 1,179 ticker rows](../reports/production-universe-rerun-20261008/confirmation-appendix.json).
- [Matched old/new base comparison: all original85 names](../reports/production-universe-rerun-20261008/old-new-original85-comparison.json).

Evaluation is still running and its complete appendix will be added after verification. The HPO appendix repeats the three verified Stage1 reference records for traceability; these must not be counted as new fits. No raw source records, per-forecast datasets or model weights are in these appendices.

## Where the models are for inference

All paths below are inside WSL2. Fitted weights stay in private data storage; GitHub receives the report, aggregate evidence and model locations/identities.

The preserved rerun packages live under `/home/russell/data/index_options/production-universe-rerun-20261008/runs/`. Evaluation uses `evaluation/seed11/pooled/base/PatchTST32/<fold>/pooled/fit/` for the nominal model at each of the nine chronological cutoffs. Seeds29 and47 are stability checks, not alternatives to select after inspecting evaluation. A completed package contains `complete.json`, outer `metadata.json`, `model/state.json` and `model/weights.pt`. The final report will identify the exact onboarded snapshot and hashes for every completed refit; evaluation fold9 is still pending at this writing.

A currently completed, verified nominal package is confirmation seed11/fold3: `/home/russell/data/index_options/ob/raw/universe-confirmation-runs-20261008/20261008T171158Z-backfill-ffb477d9/payload/files/seed11/pooled/base/PatchTST32/3/pooled/fit/`. Its fit identity is `86c44ca9422984a7951ac374a716313056bbb304d8fef9bd56169caa1484f217` and checkpoint identity is `9c02cc91ff670276c80d4cf526905a852097d6ad7bf02da0a7ece5d35feb6f15`. It supports376 ticker heads and42 ordered base features; the full392-name universe does not imply every historical checkpoint supports every name.

The runtime is `/home/russell/dskit/.venv/bin/python`, source checkout `/home/russell/dskit-production-universe-rerun-20261008`, and retained library runtime `/home/russell/data/index_options/production-development-retained-20261007/historical-advanced/runtime`. Preserve these pinned dependencies, or use their verified backup copies described above. Load the full fitted package through the existing `TorchCDF.load_checkpoint(<fit>/model)` seam and reproduce the archived recovery recipe's input preparation: exact feature order, saved training medians and the checkpoint's ticker-head encoding, then call the loaded model so its saved scaler and feature mask apply exactly once. Do not infer preprocessing or head positions from a different retraining date.

The saved output is the fitted mixture for standardized log return. Restore terminal-price units using the matching forecast reference scale and entry spot. The uncertainty handoff specifies how this nominal distribution relates to a coherent future uncertainty set. Model inference availability does not supply the missing calibrated radius, validated bands or independent qualification. The owner's next-stage MIO intent is recorded; this development cycle prepares its inputs without executing that next stage.

After evaluation recovery, the final memo will point directly to nominal seed11/fold9, its immutable onboarded package and backup, with all earlier nominal cutoffs retained for chronological inference. This is a fixed latest-cutoff reference, not a best-performing-checkpoint selection. Merge/push the completed, reviewed report and verify its remote contents.

The [model inventory](../reports/production-universe-rerun-20261008/model-inventory.json) records exact immutable source/backup paths, fit/checkpoint and constituent file hashes, input contracts, head/feature counts and training bands. The current inventory covers39 completed pre-evaluation fits; final evaluation entries remain pending. The three HPO reuse references point to existing entries instead of being counted twice. No fitted parameter values or weights are published.

The owner-requested [Claude calibration and backtest handoff](2026-10-08-claude-calibration-backtest-handoff.md) lays out the next-session decisions, calibration requirements, chronological replay contract and MIO/backtest gates. It is a future-stage plan; no MIO or strategy backtest was executed here.

The Claude handoff received an independent factual/authority review. One minor distinction between forecast-comparison block lengths and the longer calibration sensitivity check was corrected; the scoped correction review closed with zero unresolved findings. Reviewed memo SHA256:260a92a8b3a658b8b07a788a586b1ac4644da686f9886e2d8ff442cfbf245e29. Both review receipts and the exact memo are hash-verified in the private next-session-handoff-reviewed backup. This does not approve future model/calibration/MIO implementation.
