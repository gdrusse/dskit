## Question

Can the quarterly M53 predictive component be locked, and is any recipe change warranted?

## Finding

**Locked by the owner on 2026-11-04 (Path A0008).** M53 keeps its four blend components (MLP, market_cal, pooled, baseline) with causal quarterly weights fixed within each quarter. The MLP keeps all 90 inputs and trains for a fixed epoch 1 with no per-fit epoch selection, refit quarterly on all mature data. The model slot is the equal-weight quantile average of seeds 11/29/47, blended afterward.

**All of this is post-exposure development on 2020-2023, not validation.** The holdout stays sealed.

- **The MLP learns a little.** Epoch 1 beats random weights, but most of that is unconditional shape. Inputs add 1.6%, 1.8%, 0.25% and 2.1% over a no-input model by year, mostly from market inputs. Later training fails going forward while an interleaved holdout stays flat (one window, two seeds), so this reads as non-transfer, not classic overfitting.
- **Epoch selection was noise.** Picks other than 1 sit within 0.3-0.8 noise SDs. Seed 11's 45 epochs in 2021 cost 1.2% standalone and 0.07-0.12% in M53.
- **No recipe change passes in the blend.** Against frozen seed 11 M53: `fixed_loc_market_only` +0.157% (0.014, 0.300) wins 2 of 4 years; `market_only` +0.044% (-0.017, 0.105), 2 of 4; `recipe_e1` +0.035% (-0.004, 0.074), 4 of 4, chosen by the pre-set not-worse rule, not a shown gain. Standalone gains of up to +0.99% mostly vanish in the blend.
- **Seed policy.** The 3-seed bag gains +0.026% (0.002, 0.051) over seed 11; the case is removing an arbitrary seed, not score.
- **Blend.** Removing the MLP costs 0.089% (0.011, 0.167), and 60-day block intervals include zero. No removal improves the score.
- **A0005.** Versus horizon_empirical it passes, +2.18% (1.54, 2.83). Versus raw market it passes on the point estimate only, +0.72% (-0.21, 1.65), carried by 2021. The edge over market_cal fades: .84, .77, .33, .25 by year.
- **Timing.** Six leak tests were negative, and the gain survives re-anchoring the label to the next open or close. An after-close decision with a next-session fill is the safe clock.

**Limits written into the lock.**

- **Universe.** 68 of 118 names were ranked on data ending 2026-10-02, and the cohort stops alphabetically at LLY. Claims are conditional on these survivors. The holdout would inherit this.
- **Not produced yet.** Forecast caches for the exact locked recipe don't exist; the bag was scored from existing fits and `recipe_e1` was fit for seed 11 only. Warmup rows still carry old selected epochs (23, 1, 53).
- **MIO predates the lock.** The 17 quarterly MIO runs and 2021Q1 use frozen seed 11 forecasts with annual epoch pins. Their economics are unopened.
- **Deferred input fixes.** Sentinels leave 5 dead IV inputs (clipping gained +0.02%, not significant) and market inputs are imputed unflagged on 42-50% of fit rows. A clip needs an ADR first; neither is part of this lock.
- **Weak spots.** The epoch and recipe evidence rests on exposed years, and the non-transfer reading on one window. The 5-session exclusion threshold for MIO was set after seeing gap profiles.

## Sources

Evidence under `/home/russell/data/index_options/learning-diagnostics-20261104`, with SHA-256 prefixes:

- `report.md` (561148399c77): learning curves
- `input-audit/report.md` (234eb5c0036d): timing, cohort, sentinels
- `recipe-test/report.md` (65432271478d): pre-registered standalone test
- `blend-analysis/report.md` (cb9dbbcd17ef): seeds, blend, A0005, A0007, regimes
- `m53-recipe-test/report.md` (3c50c2835639): quarterly M53 test and rule
