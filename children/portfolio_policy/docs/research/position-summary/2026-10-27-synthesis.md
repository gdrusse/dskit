# Position summary numbers: risk, expected return, opportunity cost

## Question

What is the smallest standard set of per-position and whole-portfolio numbers
that tells an individual investor the **risk**, the **expected return** and the
**opportunity cost of continuing to hold**, given what ADR-0279 already computes
(liquidation tax, building-block hold return x beta, deflated active hurdle,
terminal values per path)? At most ~8 per position and ~6 for the portfolio, each
with its formula, data need and the unit a reader sees.

## Finding

**Eight numbers per position, six for the portfolio. Six of the eight follow
from the `decisions/<run>.jsonl` rows alone, beta comes from the beta table the
hold return already used, and only volatility needs data the child does not
have (price history).** A summary is therefore a pure read of the decision rows
plus two optional per-symbol inputs (beta, volatility).

### Per position (one row per account x symbol)

| # | Number | Formula | Data | Reader sees |
|---|---|---|---|---|
| 1 | weight | `v / sum(v)` | rows | % of portfolio |
| 2 | expected return | `(hold_value / v) ** (1/H) - 1` (= `rf + beta x (m - rf)`, A0001) | rows | %/yr, before tax |
| 3 | volatility | annual SD of returns: daily log-return RMS x sqrt(252) | daily closes | %/yr |
| 4 | beta | the beta that priced #2 | beta table | x market |
| 5 | embedded gain | `(v - basis) / v` | rows | % of value |
| 6 | tax cost of selling | `(v - liq_net) / v` (= `liq_tax / v`) | rows | % of value |
| 7 | break-even return | the `redeploy_return` at which `redeploy_net = hold_net` | rows + redeploy terms | %/yr |
| 8 | opportunity cost | `(redeploy_net / hold_net) ** (1/H) - 1`; > 0 means exit wins | rows | %/yr, after tax |

Read together: #2-#4 are risk and return, #5-#6 the lock-in, #7-#8 the decision.
With any redeploy tax rate below 100%, a position should be exited exactly when
the active hurdle exceeds #7, which is the same statement as #8 > 0 and
`decision == exit`.

### Whole portfolio

1. **Total value** `sum(v)`.
2. **Expected return** `sum(w x r)`, %/yr before tax.
3. **Risk**: beta `sum(w x beta)` (exact: beta is linear in weights) and the
   weighted volatility `sum(w x sigma)`, which is an UPPER BOUND on portfolio
   volatility (equal only when every pair is perfectly correlated). A covariance
   estimate needs a joint return panel and is deferred.
4. **Concentration**: top weight and HHI `sum(w_s ** 2)` over symbols (accounts
   merged, so one stock in two accounts is one exposure); `1 / HHI` is the
   effective number of holdings.
5. **Tax cost of selling everything** `sum(v - liq_net) / sum(v)`, with the
   embedded gain `sum(v - basis) / sum(v)` beside it.
6. **Opportunity cost** `(sum(redeploy_net) / sum(hold_net)) ** (1/H) - 1`, with
   the exit / hold counts.

### Break-even return, closed form

With `N` = after-tax proceeds today, `T` = `hold_net`, `H` years, cost `c`,
premium `p`, redeploy tax `t`, and `g = (T / N) ** (1/H) - 1`:

- **annual** (taxed every year on a positive net rate, as `HoldVsRedeploy`):
  `R = c + (g + p) / (1 - t)` when `g + p > 0`, else `R = c + g + p` (a negative
  net rate gets no tax refund). No solution when `t = 1` and `g + p > 0`.
- **terminal** (taxed once at the horizon on the gain over `N`): the growth
  factor is `G = (T/N - t) / (1 - t)` when `T >= N` (gain branch), else
  `G = T/N`; `R = G ** (1/H) - 1 + c + p`. No solution when `t = 1` and `T > N`.
- **untaxed**: `R = c + p + g`.

A break-even outside `RATE_BOUNDS` refuses by row.

### Why these, and not the other candidates

- **Volatility, not Sharpe or max drawdown, as the risk number.** GIPS requires
  the three-year annualized ex-post standard deviation as the composite risk
  statistic, and Morningstar's standard deviation is the same thing on 36
  monthly returns. A per-holding Sharpe ratio is the wrong lens inside a
  portfolio: Sharpe (1994) warns it ignores correlations. With beta-implied
  expected returns it is also redundant, `S_i = beta_i (m - rf) / sigma_i =
  rho_i x S_m`, a restatement of correlation. Max drawdown is one
  path-dependent, backward-looking realization; it is deferred.
- **Beta.** It is the expected-return model the child already uses (A0001), it
  is the market-risk number, and it aggregates exactly.
- **Concentration.** More than 10% of a portfolio in one stock is the common
  definition of a concentrated position (Russell Investments). Single-stock
  risk is large: about 40% of Russell 3000 stocks from 1980 to 2014 suffered a
  catastrophic loss of 70% or more from peak with little recovery (J.P.
  Morgan); firm-specific variance is about 72% of a typical stock's
  (Campbell et al. 2001). HHI and its inverse (Bridgeway: the S&P 500's
  effective number of stocks is about 46) are the standard summaries.
- **Embedded gain %** is Morningstar's potential capital gains exposure:
  unrealized gains over net assets, the industry's lock-in number.
- **Tax cost of selling %** is the cash lost at sale. Kitces shows deferral is
  worth roughly `tax x growth / net`, often under 1%/yr, easily outweighed by
  moderate extra risk, which is why it is shown next to volatility.
- **Break-even return.** Practitioners frame the sell decision as the excess
  return a replacement must earn (Marotta) or the years until a replacement
  recovers the tax (RiverFront). Ours is the exact break-even under the
  declared tax timing, comparable with one number, the active hurdle.
- **Opportunity cost %/yr.** Annualizing the after-tax advantage puts it in the
  unit of a return, like Morningstar's tax cost ratio
  `1 - (1 + after-tax) / (1 + pre-tax)`.

### Volatility data path

No price history is onboarded in this child. `dskit.onboarding.libs.yahoo`
(`YahooChartArchiveConnector`) reads a pinned Yahoo chart file (sha256-checked,
no network), stream `prices`: daily open/high/low/close/volume plus a separate
`adjusted_close`, one chart (one symbol) per file. Alpaca bars also accept
`timeframe: [1, "Day"]` for several symbols. With existing kinds only:

    daily bars (observations) -> DailyBarFeatures realized_vol (window 252,
    close_field adjusted_close) -> filter (date == snapshot day) ->
    ComputeFields (x sqrt(252)) -> KeyBy (symbol -> annual sd)
    -> PositionSummary.volatilities

The input port can ship now; the document wiring waits for the owner to choose
the bar source (a yahoo archive per symbol, or Alpaca daily bars with keys).

Deferred: a covariance (or single-index) portfolio volatility, max drawdown,
per-position risk contribution.

## Sources

- GIPS: three-year annualized ex-post standard deviation required — https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/overview-of-the-global-investment-performance-standards
- Morningstar, Standard Deviation (36 monthly returns) — https://awgmain.morningstar.com/webhelp/glossary_definitions/stocks/Standard_Deviation_Three_Year.html
- Sharpe, W. (1994), *The Sharpe Ratio* (correlation caveat) — https://web.stanford.edu/~wfsharpe/art/sr/SR.htm
- Morningstar, Potential Capital Gains Exposure methodology — https://www.morningstar.com/content/dam/marketing/shared/research/methodology/678273-PotentialCapitalGainMethodology.pdf
- Morningstar, Tax Cost Ratio methodology — https://www.morningstar.com/content/dam/marketing/shared/research/methodology/678272-TaxCostRatioMethodology.pdf
- Marotta, *Using Math to Decide When to Realize Capital Gains* — https://marottaonmoney.com/using-math-to-decide-when-to-realize-capital-gains/
- RiverFront, *The Investor's Dilemma* (break-even years) — https://www.riverfrontig.com/downloads/the-investors-dilemma
- Kitces, strategies for highly appreciated investments — https://www.kitces.com/blog/strategies-highly-appreciated-investments-big-embedded-capital-gains-concentrated-stock/
- Russell Investments, concentrated stock positions (>10% definition) — https://russellinvestments.com/content/ri/us/en/insights/russell-research/2025/02/concentrated-stock-positions-high-rewards-higher-risks--what-to-.html
- J.P. Morgan, *The Agony & the Ecstasy* (2014) — https://www.chase.com/content/dam/privatebanking/en/mobile/documents/eotm/eotm_2014_09_02_agonyescstasy.pdf
- Campbell, Lettau, Malkiel & Xu (2001), idiosyncratic risk — https://www.nber.org/papers/w7590.pdf
- Bridgeway, effective number of stocks (1/HHI) — https://bridgeway.com/perspectives/how-many-stocks-are-effectively-in-the-sp500/
- Dammon, Spatt & Zhang (2001), capital gains taxes and diversification — https://ideas.repec.org/p/cmu/gsiawp/298.html
