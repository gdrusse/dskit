## Question

Can the quarterly M53 predictive component be locked, and is any recipe change warranted?

## Finding

**Locked by the owner on 2026-11-04 (Path A0008).** M53 keeps its four blend components (MLP, market_cal, pooled, baseline) with causal quarterly weights fixed within each quarter. The MLP keeps all 90 inputs and trains for a fixed epoch 1 with no per-fit epoch selection, refit quarterly on all mature data. The model slot is the equal-weight quantile average of seeds 11/29/47. Averaging before the blend is an implementation choice, not an owner ruling; before versus after differs by +0.003 (-0.011, 0.017).

**Owner rulings added 2026-11-04 after skeptic review.** Promotion and holdout both retrain quarterly (A0004). The 2019 warmup is refit at fixed epoch 1 for every seed (A0008).

**All of this is post-exposure development on 2020-2023, not validation.** The holdout stays sealed.

**No figure below scores the locked recipe.** All three seeds at epoch 1 with an epoch-1 warmup has never been scored. Each figure names the variant it comes from.

- **The MLP learns a little.** Epoch 1 beats random weights on average, but most of that is unconditional shape. In 2022 the best random seed (.5197) beat the trained mean (.5203), and block-shuffled labels tied the recipe (-0.07%, SE 0.42). Inputs add 1.6%, 1.8%, 0.25% and 2.1% over a no-input model by year, mostly from market inputs. Later training fails going forward while an interleaved holdout stays flat (one window, two seeds), so this reads as non-transfer, not classic overfitting.
- **Epoch selection was noise.** Picks other than 1 sit within 0.3-1.1 noise SDs. Seed 11's 45 epochs in 2021 cost 1.2% standalone and 0.07-0.12% in M53.
- **Neither market arm tested in the blend passes.** Only these arms were blended, on seed 11 only, each with the old warmup bank. Against frozen seed 11 M53: `fixed_loc_market_only` +0.157% (0.014, 0.300) wins 2 of 4 years; `market_only` +0.044% (-0.017, 0.105), 2 of 4; `recipe_e1` +0.035% (-0.004, 0.074), 4 of 4, chosen by the pre-set not-worse rule, not a shown gain. `fixed_loc_market_only` failed on the year count only. Standalone gains of up to +0.99% mostly vanish in the blend.
- **Seed policy.** The 3-seed bag from existing fits gains +0.026% (0.002, 0.051) over seed 11, and its 60-day block interval (-0.010, 0.063) includes zero; the case is removing an arbitrary seed, not score.
- **Blend (bag from existing fits).** Removing the MLP costs 0.089% (0.011, 0.167), and 60-day block intervals include zero. No removal improves the score.
- **A0005 (frozen seed 11 at epochs 2/45/1/1, which this lock replaces).** Versus horizon_empirical it passes, +2.18% (1.54, 2.83). Versus raw market it passes on the point estimate only, +0.72% (-0.21, 1.65), carried by 2021. The edge over market_cal fades: .84, .77, .33, .25 by year. Whether "above zero" needs the interval to exclude zero is the owner's to resolve.
- **A0007 (pooled recipe, seed 11 gate).** Applied to the whole cohort, the regime test beats raw market in only 2 of 5 regimes, with S0 at -4.05 (-8.08, -0.02). The Path gate passes 108 cells, which clears the shared-flip p95s but not the expiry-shared p99. Whether all four baselines now apply (46 versus 3) is the owner's call.
- **Timing.** Six leak tests were negative, and the gain survives re-anchoring the label to the next open or close. An after-close decision with a next-session fill is the safe clock. These tests cannot rule out a small leak in a rare subset, the overnight effect is not fully explained, and the vendor vintage check is still open.

**Limits written into the lock.**

- **Universe.** 68 of 118 names were ranked on data ending 2026-10-02, and the cohort stops alphabetically at LLY. Scores cover 110 names and 450,563 of 712,556 forecast rows. Results apply only to these survivors. The audit rated this an owner decision; the owner has not ruled, and it is not part of the Path. The holdout would inherit it.
- **Not produced yet.** Forecast caches for the exact locked recipe don't exist; the bag was scored from existing fits and `recipe_e1` was fit for seed 11 only. Existing warmup rows carry old selected epochs (23, 1, 53) and must be refit for seeds 11 and 47.
- **MIO predates the lock.** The 17 quarterly MIO runs and 2021Q1 use frozen seed 11 forecasts with old epoch pins: 23 for 2019Q2-Q4, then 2/45/1/1 by year. Their economics are unopened.
- **Deferred input fixes.** Sentinels leave 5 dead IV inputs (clipping gained +0.02%, not significant) and market inputs are imputed unflagged on 42-50% of fit rows. A clip needs an ADR first; neither is part of this lock, and the audit had suggested fixing them in the same refit.
- **Weak spots.** The epoch and recipe evidence rests on exposed years, and the non-transfer reading on one window. The MIO exclusion principle came from the owner, who kept it for now while doubting it; the 5-session threshold and expiry check were agent-set after seeing gap profiles.

## Sources

Evidence under `/home/russell/data/index_options/learning-diagnostics-20261104`, with SHA-256 prefixes:

- `report.md` (561148399c77): learning curves
- `input-audit/report.md` (234eb5c0036d): timing, cohort, sentinels
- `recipe-test/report.md` (65432271478d): pre-registered standalone test
- `blend-analysis/report.md` (cb9dbbcd17ef): seeds, blend, A0005, A0007, regimes
- `m53-recipe-test/report.md` (3c50c2835639): quarterly M53 test and rule
