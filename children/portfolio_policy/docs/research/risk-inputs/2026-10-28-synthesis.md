# Risk inputs from Schwab daily bars: beta and volatility

## Question

The hold-vs-redeploy decision prices each holding's hold return with a beta, and the
position summary reports a beta and an annual volatility per holding. Today the owner
types the betas by hand and the volatility is null. Both are to come from Schwab daily
bars through onboarding and the existing `DailyBarFeatures` (`market_relative.beta_window`,
`realized_vol`). Which estimator and window should each use for an individual investor's
"should I keep holding this" read, and what should a reader of the numbers know?

## Finding

**Beta: the OLS slope of the holding's daily log returns on SPY's over the last 252
sessions (one year), Blume-adjusted toward 1: `beta = 0.67 x raw + 0.33`.** One line of
justification: a one-year daily beta predicts the next period's beta about twice as well
as a five-year monthly one (R² about 50% against 20-30%, Levi & Welch 2017), and pulling
it part of the way to 1 is the standard correction for estimation noise and for betas'
drift toward the mean (Blume 1975; Bloomberg's adjusted beta is exactly
`0.67 x raw + 0.33`; Levi & Welch shrink a second time by about 25% toward roughly
0.9-1 for large stocks). The adjusted beta prices the hold return (it is the forecast);
the raw beta is reported beside it (it is the fit).

**Volatility: the root mean square of the last 252 daily log returns, times
`sqrt(252)`** (the existing `realized_vol`, which assumes a zero daily mean). One line:
252 daily log returns scaled by the square root of 252 trading days is the textbook
historical volatility, one year is the horizon an owner reads, and the zero-mean form
(the RiskMetrics convention) differs from the demeaned sample standard deviation by
well under 1% of the value on daily data (about 0.2% at a 10% drift and 20% volatility;
about 0.4% at a 50% drift and 30% volatility).

What a reader should know:

- **A one-year daily beta is noisy.** Its standard error is about the residual daily
  volatility over (the market's daily volatility x sqrt(252)): about 0.01-0.02 for a
  broad fund, 0.08-0.15 for a single stock. It moves from year to year; the adjustment pulls
  it a third of the way toward 1 for that reason.
- **It will not match a quote page.** Yahoo shows "Beta (5Y Monthly)" and Bloomberg
  defaults to two years of weekly returns against the S&P 500. Single-stock betas
  differ by 0.1-0.3 across windows and frequencies; none of them is the "true" one.
- **Daily betas understate thinly traded stocks** (non-synchronous trading). That does
  not bite on liquid stocks and ETFs, which is what this portfolio holds.
- **Both sides are price returns from the same pull.** The benchmark is SPY's own daily
  bars, so the regression compares like with like; dividends are in neither.
- **Volatility is backward-looking and annualized under independent returns.** It is a
  year of history, not a forecast. The portfolio figure stays the weighted sum, an upper
  bound (A0002).
- **Data needed.** 252 returns need 253 closes; a `start` at least 400 calendar days
  before the decision day leaves room for holidays. A holding with less history gets no
  number, and the decision run stops on it by name rather than guess.
- **Adjustment is unverified.** Schwab does not document whether daily closes are split-
  or dividend-adjusted. An unadjusted split would appear as one huge daily return and
  inflate both numbers for a year: check a holding with a recent split on the first real
  pull. Ex-dividend drops (0.2-1% a quarter) add negligible noise.
- **Session dating.** Schwab stamps a daily candle at 00:00 Central time of its session
  (05:00 UTC in daylight time; observed 2026-09-14T05:00:00Z for the 2026-09-14 session)
  and clips a request by calendar day, so the session date is the New York calendar date
  of the stamp.

Mapping onto dskit: `DailyBarFeatures` with `realized_vol.windows [252]` and
`market_relative.beta_window 252` (its beta is the rolling covariance over the rolling
variance of one-bar log returns, the OLS slope), the benchmark wired as the SPY rows of
the same bar stream; `ComputeFields` annualizes (`x sqrt(252)`, 252 being trading days a
year, not the window) and adjusts the beta.

## Sources

- Levi, Y. & Welch, I. (2017), *Best Practice for Cost-of-Capital Estimates*, JFQA 52(2) —
  https://www.cambridge.org/core/services/aop-cambridge-core/content/view/AD8B94381E86DCD7F03A73E1DF472BF4/S0022109017000114a.pdf/best_practice_for_costofcapital_estimates.pdf
  (abstract: https://ideas.repec.org/a/cup/jfinqa/v52y2017i02p427-463_00.html)
- Blume, M. (1975), *Betas and Their Regression Tendencies*, J. Finance 30(3) —
  https://ideas.repec.org/r/bla/jfinan/v30y1975i3p785-95.html
- Bloomberg defaults (two years weekly vs SPX) and `Adjusted beta = 0.67 x Raw beta + 0.33 x 1.0`,
  BYU library guide — https://guides.lib.byu.edu/bloomberg/Beta
- Provider differences (Bloomberg, FactSet weekly), Babson library guide —
  https://libguides.babson.edu/beta/Differences
- Yahoo "Beta (5Y Monthly)" on the Statistics tab —
  https://grandavehousing.calpoly.edu/news/aapl-stock-beta-a-deep
- Historical volatility: log returns, sample SD, x sqrt(252) —
  https://www.macroption.com/historical-volatility-calculation/
- RiskMetrics Technical Document (1996), zero-mean daily volatility —
  https://www.msci.com/research-and-insights/paper/1996-riskmetrics-technical-document
- Schwab daily candles stamped 05:00 UTC (00:00 Central, daylight time) and clipped by
  calendar day, observed — https://github.com/l3a0/marketlake/issues/420
- schwab-py price-history parameters (no adjustment statement) —
  https://schwab-py.readthedocs.io/en/latest/client.html
