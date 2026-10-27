# Estimating `hold_return` for the hold-vs-redeploy decision

## Question

For each long-term holding (index funds and single stocks), what estimate of
the expected annual return over a 5-year horizon is both the most accurate
and the most intuitive to explain, and how should it feed
`HoldVsRedeploy.hold_return` (ADR-0279)?

## Finding

**Recommendation: a building-block estimate, not a trailing average.**
For the market (and so every index fund):

    hold_return = dividend yield + expected real earnings growth
                + valuation reversion toward fair CAPE + inflation

For a single stock: market `hold_return` scaled by the stock's beta, i.e.
`r_f + beta x (market hold_return - r_f)`, with no stock-specific term. The
intuition is one sentence: *a holding is expected to pay what the market is
priced to deliver, times how much market risk it carries*.

Why, in order of weight:

1. **Trailing averages are the noisiest estimator available.** Fama-French
   (2002) estimate the 1951-2000 real equity premium three ways: realized
   average 7.43%/yr (s.e. 2.43%), earnings-growth model 4.32% (s.e. 1.93%),
   dividend-growth model 2.55% (s.e. 0.74%). The fundamental estimates are
   two to three times more precise, and the realized average is biased
   upward by a half-century of valuation expansion that cannot repeat
   indefinitely. Merton (1980) is the older statement of the same fact: the
   standard error of a mean falls only with calendar time, not with sampling
   frequency, so no amount of daily data rescues a 5-10 year window.
2. **Valuation-aware building blocks beat the historical mean out of
   sample.** The yield + growth + CAPE-reversion framework (the Vanguard
   "fair-value CAPE" family) reports >30% lower mean-square error than the
   historical-mean benchmark on 10- and 20-year forecasts. CAPE alone is the
   most reliable single component; yield and growth help when combined with
   it.
3. **Single-stock means are not estimable at all on a usable sample.**
   Shrinkage results (James-Stein, Jorion 1986) show that pulling every
   stock's sample mean toward one common target lowers error; the extreme
   and most defensible shrinkage for a handful of holdings is all the way to
   the market, via beta. A stock-specific expected return is a forecast of
   alpha, which is exactly what the active side of this comparison is
   supposed to earn — so the hold side should claim none.
4. **Intuition.** Three named inputs the owner can read off public data
   (dividend yield, long-run earnings growth, current vs fair CAPE) and one
   per-stock number (beta) explain every decision row; a trailing mean
   explains nothing except what the last N years happened to do.

What this means for the comparison's other side: `redeploy_return` from a
child's backtest must be haircut for selection and overfitting before it is
a hurdle (deflated Sharpe ratio, Bailey & Lopez de Prado 2014). The
`risk_premium` param is where that haircut lives until the child reports a
deflated figure itself.

Proposed implementation (not built; awaiting owner):

- A tier-1 node `MarketHoldReturn` (building blocks as params: `dividend_yield`,
  `real_growth`, `inflation`, `cape`, `fair_cape`, `reversion_years`) that
  emits one number and records its decomposition; a sibling `BetaHoldReturns`
  that reads the positions' symbols and a beta table (declared or estimated
  from the dskit bars/yahoo connector) and emits `{symbol: rate}`.
- `HoldVsRedeploy` gains an optional `hold_returns` INPUT port that overrides
  the `hold_return` param, so the estimate is wired, never pasted.
- Interim: the owner can set `hold_return` as a `{symbol: rate}` mapping
  computed by hand from the same blocks; the document already accepts it.

## Sources

- Fama, E. & French, K. (2002), *The Equity Premium*, J. Finance —
  https://www.evidenceinvestor.com/wp-content/uploads/2016/08/The-Equity-Premium-Fama-French-2001.pdf
  (CFA Digest summary: https://rpc.cfainstitute.org/-/media/documents/article/cfa-digest/2002/dig-v32-n4-1174-pdf.pdf)
- Merton, R. (1980), *On Estimating the Expected Return on the Market* —
  https://www.nber.org/system/files/working_papers/w0444/w0444.pdf
- Davis, Aliaga-Diaz, Thomas & Tolani (Vanguard), *Improving U.S. stock return
  forecasts: a fair-value CAPE approach*; summary with the MSE result —
  https://alphaarchitect.com/long-term-expected-returns/ and
  https://theideafarm.com/portfolio-management/improving-u-s-stock-return-forecasts-a-fair-value-cape-approach/
- Shrinkage of expected returns (James-Stein, Jorion 1986), MOSEK Portfolio
  Optimization Cookbook §4 — https://docs.mosek.com/portfolio-cookbook/estimationerror.html
- Bailey, D. & Lopez de Prado, M. (2014), *The Deflated Sharpe Ratio* —
  https://papers.ssrn.com/abstract=2460551
