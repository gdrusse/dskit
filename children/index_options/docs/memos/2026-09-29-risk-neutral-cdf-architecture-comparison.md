# Risk-neutral CDF bridge and architecture comparison

**Date:** 2026-09-29
**Decision protocol:** ADR-0193
**Status:** completed offline research; no trading or deployment authority

## Decision

Retain the existing 25% empirical/MLP blend as the governed incumbent for now.
The transported option-implied CDF is the strongest challenger tested: on the
reused 2019–2025 evaluation it delivered 2.75% equal-cell CRPS skill versus the
horizon empirical baseline and 1.85% versus the incumbent. The 120-entry-date
block interval versus the incumbent was `[0.39%, 3.29%]`, and the improvement
also appeared in tail CRPS, strike Brier score, raw-return CRPS and the bounded
condor-loss diagnostic.

It does **not** pass the predeclared promotion guard. Development PIT tails were
worse than the incumbent for both IWM tails and the SPY lower tail. The result is therefore
promising model evidence, not a production replacement. The next experiment
should focus narrowly on chronological tail calibration of this challenger,
without reopening the architecture zoo.

## What was built

One standard JSON document drives preparation, grouped search, selection, four
evaluation partitions and reporting. No one-off execution script is part of the
implementation. The generic dskit CDF pack now includes:

- a native CatBoost MultiQuantile CDF;
- a conditional one-dimensional rational-quadratic spline flow;
- mask-safe DeepSets and Set Transformer mixture-density CDFs;
- an option-implied CDF transport with training-only empirical tail splices and
  unchanged-incumbent fallback on ineligible rows; and
- a generic train-only PCA augmentation wrapper, selected by the rich blend; and
- the existing quantile-regression forest under the richer feature space.

The raw-chain preparation stage emits one fixed nine-node tensor for each
`(symbol, quote_date, expiry)` identity. Each node contains log moneyness,
implied volatility, relative spread, open interest and displayed depth plus an
explicit mask. Invalid/crossed quotes, nonpositive IV, negative size/open
interest and duplicate strike/right contracts are deterministically masked
before either proxy or tensor construction. Contract order cannot affect the
tensor or either set encoder. The source schema has no adjusted-deliverable
field; this run can therefore use only the archive's standard-contract rows and
does not claim to detect adjusted deliverables.

For the risk-neutral proxy, the stage estimates a robust parity forward, forms
OTM call-equivalent prices, projects call slopes to the decreasing/convex
no-arbitrage cone, removes only uninformative outer intervals whose digitals are
exactly zero or one, and recovers interior quantiles. Listed strikes are not
renormalized to total probability one. Forecast tails are spliced to the
training-only horizon empirical CDF by multiplicatively scaling left CDF mass
and right survival mass at the proxy endpoints. This remains an American-ETF market proxy,
not an exact European density and not a physical probability law.

## Data and features

The build scanned the pinned 53,407,120-row SPY/QQQ/IWM options archive and
prepared 109,850 chain identities. After the pre-existing close-path,
settlement and reference-scale refusals, the modeling panel contained 109,355
rows.

Proxy eligibility was 96.02% overall after the stricter quote/IV guards:

| Index | Eligible | Panel rows | Coverage |
|---|---:|---:|---:|
| SPY | 41,850 | 44,770 | 93.48% |
| QQQ | 32,355 | 32,639 | 99.13% |
| IWM | 30,798 | 31,946 | 96.41% |

Ineligible rows retained the exact incumbent forecast, so every model was
scored on the same identities. On the 67,244 active 2019–2025 rows, the
challenger had 3.30% raw-row CRPS skill versus empirical and 2.28% versus the
incumbent.

The panel declared 199 feature columns; actual settlement DTE was metadata only,
leaving 198 eligible predictors. They included prior returns/RV/expiry lifecycle; ten
surface summaries; 1/5/22-session ATM/skew/curvature changes; neighboring-
expiry slopes; implied-minus-realized variance; liquidity asymmetry; six
risk-neutral moments/tail integrals; the 54 raw-chain tensor fields; proxy
diagnostics and nine proxy quantiles. The richer blend also appended three
surface PCs fitted separately inside every training fold.

Market inputs were lagged and age bounded: six Cboe volatility/shape series;
fed funds, 3-month and 10-year Treasury rates; high-yield OAS; financial and
nonfinancial commercial-paper rates plus their spread; broad dollar; WTI; and
NFCI/ANFCI. Cboe closes used a one-calendar-day lag. Daily FRED observations
became available only after one complete intervening XNYS session and had a
seven-calendar-day maximum age; weekly financial-condition series used a
seven-day lag and 14-day maximum age. Coverage was 96.98%–100% for the Cboe
family, 81.24%–100% for the long-history FRED families, and 21.07%
for the short retained high-yield backfill. Values, ages and missingness masks
were retained; age and staleness are measured from the source observation date,
not the later availability date, and missing values were train-only
median-imputed. Actual settlement DTE remained reporting metadata and was
excluded from every learned model.

## Training, selection and evaluation

Every annual fold for evaluation year `Y` used:

1. **fit:** entries before `Y-1`, with every target settled before `Y-1`;
2. **calibration:** entries during `Y-1`, with every target settled before `Y`;
3. **validation:** entries during `Y`.

Pooled training grew from 16,777 rows / 1,762 entry dates for the 2016 fold to
88,465 rows / 4,023 entry dates for the 2025 fold. The 2016–2018 development
period contained 18,616 forecasts across 752 entry dates and 643 expiry series.
Only those years chose one raw/calibrated finalist per architecture. The freeze
preceded all later scores. The reused 2019–2025 evaluation contained 68,084
forecasts, 1,746 dates and 3,495 expiry series:

| Partition | Years | Forecasts | Entry dates | Expiry series |
|---|---|---:|---:|---:|
| Development replay | 2016–2018 | 18,616 | 752 | 643 |
| Early | 2019–2021 | 25,478 | 755 | 945 |
| Middle | 2022–2023 | 22,072 | 501 | 1,188 |
| Late | 2024–2025 | 20,534 | 490 | 1,444 |

Selection minimized equal-index/exact-calendar-DTE-cell CRPS relative to the
fixed horizon empirical reference. Rich/PCA, option transport, CatBoost,
forest and controls selected raw output; spline, DeepSets and Set Transformer
selected calibrated output. Scores are paired row-for-row. The uncertainty calculation resamples
whole entry-date blocks of 60 or 120 dates, keeping indexes and expiries on a
date together. These are pointwise research intervals, not multiplicity-
adjusted guarantees.

## Architecture results

Evaluation results below use the frozen 2016–2018 selection and all 68,084
2019–2025 forecasts per model.

| Frozen model | Mean standardized CRPS | Equal-cell skill vs empirical |
|---|---:|---:|
| Option proxy + PIT transport | **0.5869** | **+2.75%** |
| Incumbent 25% blend | 0.6004 | +0.93% |
| Horizon empirical | 0.6067 | 0.00% |
| Pooled one-normal MLP | 0.6060 | -0.60% |
| Rich-feature/PCA fixed 15% blend | 0.6116 | -0.60% |
| CatBoost MultiQuantile, depth 4 | 0.6291 | -3.94% |
| Quantile forest | 0.6867 | -13.47% |
| DeepSets mixture | 0.7874 | -27.02% |
| Set Transformer mixture | 0.7828 | -27.59% |
| Spline flow | 1.1503 | -78.28% |

The option challenger was the only new method with a 120-date block interval
strictly above zero against both references:

| Reference | Point skill | 95% block interval |
|---|---:|---:|
| Horizon empirical | +2.75% | `[+1.14%, +4.46%]` |
| Incumbent 25% blend | +1.85% | `[+0.39%, +3.29%]` |

### Skill by index

These are equal-exact-day-cell skills versus horizon empirical; `n` is the
number of paired forecasts across the 45 actual calendar-DTE cells.

| Model / metric | SPY skill (`n=27,608`) | QQQ skill (`n=21,493`) | IWM skill (`n=18,983`) |
|---|---:|---:|---:|
| Option transport — CRPS | **+3.96%** | **+3.91%** | +0.38% |
| Incumbent — CRPS | +1.05% | +1.24% | +0.49% |
| Rich/PCA blend — CRPS | -0.24% | -0.24% | -1.31% |
| CatBoost — CRPS | -2.92% | -2.73% | -6.17% |
| Option transport — tail CRPS | **+4.52%** | **+4.64%** | +0.84% |
| Option transport — strike Brier | **+4.38%** | **+4.70%** | +0.82% |
| Option transport — condor-loss MSE | **+8.29%** | **+8.34%** | +3.49% |

The complete 45-day grids, including the observation count in every cell, are
in `report/skill_by_exact_day.csv`. Mean raw-return CRPS skill for the option
challenger was +4.13%; bounded-condor-loss MSE skill was +6.70%.

## Why the challenger is not promoted

The development guard required strict CRPS improvement, full paired coverage,
and no worse absolute below-5% or above-95% PIT deviation for **every** index.
The option transport passed mean CRPS and coverage but failed the tail clause:

| Index | Tail | Incumbent absolute deviation | Option transport | Pass? |
|---|---|---:|---:|---|
| IWM | below 5% | 0.0018 | 0.0237 | No |
| IWM | above 95% | 0.0249 | 0.0284 | No |
| QQQ | below 5% | 0.0145 | 0.0056 | Yes |
| QQQ | above 95% | 0.0335 | 0.0287 | Yes |
| SPY | below 5% | 0.0074 | 0.0260 | No |
| SPY | above 95% | 0.0339 | 0.0161 | Yes |

The failure is specific and actionable: the option surface materially improves
location/shape and expected-loss scores, but its transported lower-tail mass is
too aggressive for IWM and SPY. A calibration-only follow-up should test
index-specific lower-tail maps or a conservative convex blend with the
incumbent. It should keep the frozen feature/proxy preparation and avoid another
broad zoo.

## Public-model probes

Chronos-2, TimesFM-3, Moirai-2 and TabPFN were inventoried but excluded from
historical selection and promotion before loading weights. None supplied
retained evidence that its pretraining corpus ended before the first 2016
development entry. Fine-tuning on local folds cannot undo unknown pretraining
exposure. Their explicit `descriptive_only` statuses are retained in
`report/public_probes.json`; no performance claim is made for them.

## Resource and test evidence

- Raw archive preparation: 109,850 identities; 16m10s final full rebuild;
  1.74 GiB peak RSS. A resumable implementation-, metadata- and
  source-content-hashed
  annual cache prevents
  partial scans from being mistaken for final output.
- Search/evaluation stages: all under 30 minutes; heaviest observed stage was
  late evaluation at 5.35 GiB peak RSS.
- Artifact audit: all 13 canonical stages were complete; 542 listed files
  matched their SHA-256 digests; every stage shared one protocol identity and
  the same frozen selection hash. The raw-chain source-manifest digest also
  matched the digest retained in data provenance.
- Final focused implementation/adapter/purity suite: 136 passed. Additional
  edge-case tests cover probability-mass tail splicing, train-only PCA,
  invalid/crossed/duplicate quotes, causal surface dynamics, one-sided chains,
  all-masked contract sets, permutation identity, padded-node safety, daily
  FRED exchange-session availability and observation-age staleness,
  source-content and spot-metadata cache invalidation,
  ordered CatBoost quantiles and finite ordered spline distributions.
- CUDA was available under WSL2 (`torch 2.11.0+cu128`); declared CUDA models
  ran on GPU with deterministic algorithms and no silent CPU fallback.
- A broader child-config invocation also exposed 23 unrelated baseline
  failures: its exact-file manifest predates the already merged CDF documents,
  and 21 grid tests still expect `PENDING-PLAN-REVIEW` although the shipped
  grid JSONs retain the prior owner approval. ADR-0193 did not alter those grid
  documents or rewrite that historical test contract.

## Reproducibility and artifacts

Canonical document:
`children/index_options/configs/run-predictive-cdf-risk-neutral.json`.

Standard stage order:

```text
python -m index_options.cdf_study <config> --stage prepare
python -m index_options.cdf_study <config> --stage search --partition <group>
python -m index_options.cdf_study <config> --stage select
python -m index_options.cdf_study <config> --stage evaluate --partition <partition>
python -m index_options.cdf_study <config> --stage report
```

Completed artifacts are under
`children/index_options/pipeline_runs/predictive_cdf_risk_neutral_20260929/`.
Every canonical search, selection, evaluation and report directory ends in a
completion manifest with config, panel, provenance, implementation, dependency
and file hashes. Interrupted directories are visibly suffixed and have no
completion manifest. Completed pre-final directories are also visibly suffixed,
retain their older implementation identities and were not consumed by canonical
selection.

This evaluation period has already been inspected repeatedly in the research
program. It is not a fresh holdout, and none of these results authorize option
selection, expected-return claims or live trading.
