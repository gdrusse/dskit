# P6 ordering vs size: a calibration slope and a per-timestamp cross-sectional IC

Date: 2026-09-03. Plan: docs/plans/2026-09-horizon-search.md, P6.

## Question

Ordering survives ~30x further out than accuracy (LightGBM val IC +0.054
at h=1, still +0.020 at h=60, 19-20/20 folds positive, while the MSPE gain
is -1.05%). Can "how far ahead" honestly be two numbers? What exactly must
be measured, and how weak is that measurement with three names?

## Finding

**Yes, but only if the ordering number is a different number than the one
we have.** Our val_ic is a Spearman correlation over CONCATENATED
(name, time) rows. That pools the time-series dimension into the
cross-sectional one: a model that only says "all three names rise now"
scores well pooled and gives a selector choosing among names at one
instant nothing at all. Practitioner guidance is explicit -- never pool
asset-dates into a single correlation; compute the correlation WITHIN each
timestamp and average over timestamps (Qlib's RankIC; microalphas). The
pooled/within gap is a plain Simpson's-paradox risk. So the +0.054 is not
evidence for the optimizer yet, in either direction.

**Ranking is the right target when the downstream decision is selection
with fixed sizing.** The learning-to-rank literature (Poh et al. 2020;
LambdaRankIC 2026) argues regress-then-sort optimises the wrong loss: MSE
is dominated by a few large-|y| rows that are not where the stable alpha
is, while the decision only uses the order. **It is the wrong target when
the downstream sizes positions.** Mean-variance needs alphas in return
units commensurate with risk and cost: w = Sigma^-1 alpha / lambda. Ranks
break this in three ways, only one of which is repairable:

- *Scale* is repairable -- a uniform scale error in alpha is absorbed by
  re-tuning risk aversion (Grinold-Kahn).
- *Shape is not.* Ranks are a nonlinear monotone map: a name barely ahead
  and a name far ahead get identical alphas. The optimiser takes the
  spacing literally.
- *Conviction is destroyed.* Ranks have the same dispersion every instant,
  so the optimiser cannot tell a strong moment from a flat one, cannot
  compare expected return against cost, and trades at full size always.
  With N=3 the rank vector has exactly 6 possible values, so the optimiser
  can only ever emit 6 portfolios.

The standard bridge is Grinold's alpha = volatility x IC x score: train
for order if you like, then calibrate score -> expected return as a
separate low-parameter step. That step is precisely the slope below.

**Calibration (Mincer-Zarnowitz).** Regress y = a + b*yhat + e, test
(a,b) = (0,1) jointly with HAC errors. b ~ 1: magnitudes are usable.
0 < b << 1: real information at the wrong scale -- the forecast
over-reacts. b ~ 0 with rank IC > 0: only the order is usable.
The link to our wall is exact: for any forecast with Pearson r against y,
rescaling by b* = cov(y,yhat)/var(yhat) gives R2 = r^2 >= 0. **So a
negative gain together with r > 0 is a pure scaling failure that ONE
number per horizon repairs.** If shrinking by a train-estimated b lifts
the h=3..60 gain above zero, the two-bar wall was an amplitude artefact,
not an information wall. If b is indistinguishable from 0 while rank IC
stays positive, there is no magnitude to recover and the two horizons are
genuinely different answers.

**Positive rank correlation with negative accuracy is a known pattern**
with three causes that must be told apart:

1. *Scale/shrinkage* -- yhat too volatile. Rank correlation is
   scale-invariant, MSE is not. Diagnosed by b in (0,1) with t(b) > 2.
   Exploitable after recalibration.
2. *Sign but not size* -- direction right on many small moves, wrong on
   the few large ones. Rank weights all rows equally, MSE weights by
   squared error. Diagnosed by rank IC >> Pearson IC. Survives fixed-size
   selection, dies under size-weighted positions.
3. *Static tilt / volatility artefact* -- yhat tracks a slow characteristic
   (sigma_t, industry), so the ordering is structurally stable rather than
   timed. **This is the dangerous one at N=3** and it is not exploitable:
   it is a constant tilt, and it vanishes under cross-sectional demeaning.
   ADR-0059's /sigma_t*sqrt(h) label closes part of it, but sigma_t is
   still a feature the model can track. Note also that bounce cannot
   explain h=60: the shared px[t] channel is ~1/60 of the variance there.

## DELIVERABLE: two numbers per horizon, and how to test them

Both are O(n) over arrays the scan already materialises; no new fits, no
measurable cost on the 3.5 min walk. ADR-0067 first (plan rule).

**Number 1 -- calibration slope, per fold x lead x symbol.**
In `_walk_no_information_series` (nodes.py:3109) val_y and yhat already
exist per (symbol, lead). Add to the curve row: `mz_slope`,
`mz_intercept`, `mz_slope_se` (Newey-West, lags = max(lead //
period_minutes - 1, 0), the same lag the row already carries),
`mz_t_vs_1 = (b-1)/se`, `mz_t_vs_0 = b/se`, `pearson_r`.
Add `shrunk_gain`: fit (a,b) on the fold's TRAIN pairs only, apply
yhat' = a + b*yhat to val, recompute R2_oos against the training mean.
Fitting b on the same val rows you then score makes the gain non-negative
by construction -- the val slope is a diagnostic, the train-fitted
shrinkage is the honest test.
Test per horizon: mean b over the 20 folds with a t-test across folds
(df 19, the P5 fold-cluster form), and the P5 pooled DM rule applied to
the shrunk forecast.

**Number 2 -- per-timestamp cross-sectional IC with a HAC t.**
`_scan_fold_stamped` (nodes.py:3030) already returns val stamps and
`_walk_no_information_series` throws them away at line 3120. Keep them;
return (stamps, y, yhat) per lead; join across symbols on stamp in
`NoInformationScan.run` (nodes.py:3542, beside the existing metric
assembly at 3654).
Per stamp with >= 3 finite pairs: rho_t = `_spearman`(yhat_t, y_t)
(nodes.py:1951). Then feed the stamp-ordered series to
`newey_west_mean(rho, lags = max(lead // period_minutes - 1, 0))`
(dskit/pipeline/stats.py:391) -- it is already a one-sided HAC test of
mean <= 0. Emit `xs_ic`, `xs_ic_se`, `xs_ic_t`, `xs_ic_p`,
`xs_ic_n_stamps`, `xs_ic_frac_pos`.
**Emit the guard twin too:** `xs_ic_dm*`, identical but with each name's
validation-window mean removed from BOTH yhat and y first. That separates
timing skill from a static name tilt -- cause 3 above. At N=3 this is the
single most important column.
Optional third line, the economic reading: per stamp, long the top-ranked
name and short the bottom-ranked, one spread return per stamp, same
newey_west_mean. It converts an ordering claim into a P&L claim with no
optimiser.
Pre-registered PASS(order) for a horizon: pooled `xs_ic_t` >= 1.645 AND
>= 13/20 folds with positive mean rho AND `xs_ic_dm` retaining at least
half of `xs_ic`. PASS(magnitude) stays the P5 DM rule, unchanged. Report
both horizons; they are allowed to differ.

**WARNING -- how weak this is with three names.** Enumerated, not
estimated: with N=3 a per-timestamp Spearman takes only FOUR values,
{-1, -0.5, +0.5, +1}. It can never be 0. Under the null the six orderings
give sd = 1/sqrt(N-1) = 0.707 and a best possible one-sided p of 1/6 =
0.167, so no single timestamp can ever be significant, and cross-sectional
demeaning leaves 2 degrees of freedom -- the third name's residual is
determined by the other two. The measure is really a rescaled three-way
hit rate. Power: detecting a true mean IC of 0.02 at t=2 needs
(2*0.707/0.02)^2 ~ 5,000 INDEPENDENT stamps. We have ~3.3k stamps per fold
and ~67k pooled (5-min rows, 63-day val windows, 20 folds), but at h=60
the labels overlap 11-deep, inflating variance by up to ~12x -> ~5.6k
effective. **So the pooled series is just barely powered and every
per-fold cross-sectional t is noise.** Only report the pooled number.
Breadth is fatal on its own terms too: IR = IC*sqrt(BR) with BR ~ 2
independent choices per instant.
**At N=5 (P9 restores AAPL and WMT) this changes materially:** 120
orderings on a 0.1 grid, sd falls 0.707 -> 0.5, a single stamp can reach
p = 1/120 = 0.008, the stamps needed for the same detection fall to
~2,500, demeaning leaves 4 df, and breadth doubles (IR up ~41%). Measured
IC is also biased and high-variance on small universes (arXiv 2010.08601).
**Recommendation: build both numbers now, but treat the N=3
cross-sectional verdict as provisional and re-read it after P9.**

## Sources

- Poh, Roberts, Zohren, Rebonato 2020, Building Cross-Sectional Systematic
  Strategies By Learning to Rank. https://arxiv.org/abs/2012.07149
- LambdaRankIC: Directly Optimizing Rank IC for Financial Prediction.
  https://arxiv.org/html/2605.00501
- microalphas, Information Coefficient: per-date cross-section, never pool
  asset-dates, hundreds of names for reliability.
  https://microalphas.com/information-coefficient/
- FinTSB (arXiv 2502.18834): IC/RankIC/ICIR as daily cross-sectional means.
  https://arxiv.org/pdf/2502.18834
- Yan 2020, Information Coefficient as a Performance Measure of Stock
  Selection Models: small-universe bias and IC volatility.
  https://arxiv.org/pdf/2010.08601
- Grinold 1994, Alpha is Volatility Times IC Times Score (JPM); Grinold &
  Kahn, Active Portfolio Management: alpha scaling, IR = IC*sqrt(BR).
  https://analystprep.com/study-notes/cfa-level-2/state-and-interpret-the-fundamental-law-of-active-portfolio-management-including-its-component-terms-transfer-coefficient-information-coefficient-breadth-and-active-risk-aggressiveness/
- Mincer & Zarnowitz 1969; MZ specification, joint (0,1) test, HAC errors,
  slope as scale efficiency, recalibration from the fitted line.
  https://metricgate.com/docs/mincer-zarnowitz-forecast-test/
- Newey-West with overlapping h-step observations -> MA(h-1).
  https://warwick.ac.uk/fac/soc/wbs/subjects/finance/faculty1/anthony_neuberger/improved.pdf
- When Alpha Breaks (arXiv 2603.13252): ranking losses vs conflating rank
  failure with idiosyncratic volatility. https://arxiv.org/html/2603.13252v1
- Neglected Heterogeneity, Simpson's Paradox and the Anatomy of Least
  Squares: pooled vs within-group coefficients can reverse.
  https://www.degruyterbrill.com/document/doi/10.1515/jem-2023-0028/html
- Heston, Korajczyk & Sadka 2010, Intraday Patterns in the Cross-Section of
  Stock Returns. https://arxiv.org/pdf/1005.3535
- Bollerslev et al., Intraday Market Return Predictability Culled from the
  Factor Zoo: 5-min cross-sectional predictability net of costs.
  https://public.econ.duke.edu/~boller/Papers/HFML.pdf
