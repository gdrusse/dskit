# Focused tuning and stable probability pockets

## Best candidate options

Study in progress. No new winner is declared. Method 24 remains the reference; methods 48, 44 and 38 are the other parent families.

## Execution contract fixed before runs

Owner request: further iterate on the best methods, tune relevant settings and check whether predictions beat market in specific pockets. Base: d92ab16c4308597565e47780c7b2a47e58f45af8. Allowed changes: child JSON configurations, focused tests, this memo/evidence, generated research journal and final lane handoff. No toolkit mechanism, upstream panel rebuild, inherited GRU retraining, trading, deployment or protected forward outcome read.

Twenty settings comprise four exact parent controls and sixteen perturbations. For each parent 24/48/44/38, test market-logit correction strengths .25/.50/.75. For each ARD parent 24/48/44, also test coefficient-precision pruning thresholds 1,000/100,000 at strength .50 (default 10,000). For parent 48 also test fixed volatility boundaries at half/twice its previous level, at strength .50. This is a bounded one-factor-at-a-time sensitivity study, not an exhaustive interaction grid.

Every setting has warm-up and validation JSONs with the same six full output views as the aligned study: forecasts, contract scores, asset/lead/market-decile cells, leads, asset/leads and lead/decile calibration. All candidate and benchmark training schedules, data rows, features and comparator definitions remain identical to that study. Warm-up scoring: February 19-April 19, 2026, 52,999 forecasts / 11,213 contracts; causal fits February 19 and March 21. Validation: May 2-July 31, 84,728 forecasts / 17,158 contracts; all corrections and fitted comparators train once at May 2, using settlement plus one day strictly before cutoff. The inherited GRU retains its original causal update schedule. Data are Kalshi BTC/ETH; market probability is the acquired one-minute candle-midpoint proxy.

All these dates have already been inspected. Parent choices use that earlier knowledge. This study can test historical robustness, but cannot supply an untouched test or erase previous selection. Keep the separate protected forward protocol unchanged.

## Warm-up-only tuning and pocket selection

Split the 60 warm-up days into four consecutive 15-day windows, and also two 30-day halves. Use retained causal predictions, never training predictions. A global setting qualifies only when mean log-loss differences versus both raw market and its calibrated benchmark are negative in both halves, and mean Brier difference versus raw market is nonpositive in both halves. Select at most one setting per parent family, ranked by its worst-half calibrated-market log-loss difference (smaller is better), then full-period calibrated difference, then setting ID. This prevents a large single-period gain from dominating the choice.

For local pockets, examine all 100 fixed asset/lead/market-decile cells for every setting. Require at least 200 observations and 20 distinct days overall, and at least 30 observations / 5 days in each of the four windows. Require negative log-loss differences versus raw AND calibrated market in both halves, negative differences versus both in at least three of four windows, and nonpositive full-period Brier differences versus both. Within each parent family, rank qualifying setting/cell pairs by worst-half calibrated log-loss difference, then full-period calibrated difference, setting ID, asset, lead and bin. Select at most two distinct cell identities per parent, keeping the best qualifying setting for each cell. Global and pocket settings can differ because their declared objectives differ. Do not fill vacancies or relax rules if none qualify.

Freeze all setting IDs, cell identities, source/configuration/script hashes, warm-up output hashes and these rules in a committed selection lock before validation runs. All twenty settings still receive complete validation outputs; unselected results remain exploratory. There is no tuning after validation.

## Validation scoring and decision rule

Compare each frozen global setting and each frozen cell against raw market, logistic market recalibration, prior method 05, prior method 24 and its own exact parent control. Negative paired log-loss/Brier differences favor the candidate. Use contract-averaged scores globally and one contract observation at each cell lead. Report day-cluster and fixed-seven-day-cluster one-sided t uncertainty, with Bonferroni correction separately for each comparator and selection family: at most four global choices and eight pockets. Family size is the actual frozen number; an empty family has no passes. Cross-comparator families are separate, not a joint five-comparator claim.

A daily screen requires negative log loss, corrected p below .05 and nonpositive Brier difference versus that same comparator. A robust pocket must additionally pass the seven-day screen and have negative log-loss differences in both validation halves (May 2-June 15 and June 16-July 31). Report actual sample sizes and realized YES, mean market and model probabilities. Distinguish improvement over raw market from improvement beyond simple recalibration, and tuning gains from changes versus the exact parent.

All twenty-setting / 2,000-cell maps are exploratory, with the stated family correction per comparator. No unselected success replaces a frozen choice. The main decision is whether stable warm-up pockets repeat and whether tuning adds anything beyond the exact parent controls. If none does, retain the simpler reference and stop tuning these settings on this history.

## Implementation and verification

Reuse the existing generic expanding refitter, compute/group/score and immutable-output nodes. Focused tests must verify all 40 documents, identical source and comparator definitions, causal feature/fit boundaries and exact parent controls. The author audit checks every output identity, score, aggregate and fit census. Two fresh independent final reviews are required by the repository workflow. Full suite is not requested.

Initial RED: the focused tests correctly failed because the forty requested documents and four control pairs were absent. No models had run. Sweep found no competing pocket-tuning implementation; the larger synonym sweep hit its 0.05-second text-search budget, and the focused sweep completed across 76 refs / 74 other worktrees.

## References and reproducibility

- [Chronological evaluation and model selection](https://scikit-learn.org/stable/modules/cross_validation.html)
- [ARD coefficient pruning parameter](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.ARDRegression.html)
- [Previous aligned study](2026-10-18-warmup20-aligned-methods.md)
- Artifacts: /home/russell/data/crypto_trading/research/pocket-tuning-20261018.

### Operational retry before selection

Warm-up setting 01 initially stopped at the first writer because the requested results directory was absent. Its model fit completed, but no completed six-output result existed. The failed run and original log are retained; only output placement for its retry changed. The folder was explicitly created, and the retry uses a new run root without overwriting the failure. This is a recorded execution failure, not a completed experiment or a statistical-plan change.
