# Quant-finance foundations for an ML practitioner

This is a practical map from financial products to the ML research workflow. It is educational material, not investment advice or a trading recommendation.

## 1. Financial instruments are tradable contracts

**A financial instrument is a claim whose payoff depends on a business, borrower, asset, or future event.**

The first split is equity (ownership), debt (a loan), and derivatives (a contract whose payoff depends on something else). A stock is an ownership claim on a corporation's assets and profits; a bond is a loan that promises interest and principal repayment. Creditors normally rank ahead of common shareholders if the firm fails. See [Investor.gov on stocks](https://www.investor.gov/introduction-investing/investing-basics/investment-products/stocks) and [bonds](https://www.investor.gov/introduction-investing/investing-basics/investment-products/bonds-or-fixed-income-products).

For ML, the contract defines the label's economics. Next-minute AAPL return forecasts an ownership claim; a Kalshi YES probability forecasts a bounded binary payoff. Do not transfer a loss function or sizing rule between them before stating the payoff.

## 2. Stocks and bonds are different contracts

**A common share can benefit from company growth but has no promised payout, while a bond has promised cash flows but credit and interest-rate risk.**

Stockholders can receive price appreciation or dividends. Bondholders receive coupons and principal if the issuer performs. Bond prices usually move opposite to market interest rates: a new high-yield bond makes an old low-coupon bond less attractive. Bonds carry credit, rate, inflation, and liquidity risk; they are not just safe stocks. [Investor.gov's bond FAQ](https://www.investor.gov/introduction-investing/investing-basics/investment-products/bonds-or-fixed-income-products/bonds) is a concise overview.

For ML, equity research usually targets returns or rankings. Fixed-income research may target yield changes, default risk, or the yield curve. A price-model fit does not automatically measure total investor return after coupons, dividends, and financing.

## 3. ETFs are traded wrappers around portfolios

**An ETF pools holdings and lets investors trade shares in that pool during market hours.**

An ETF may hold hundreds of stocks or bonds, or a narrow slice of one sector. Its market price can differ from NAV, the value of holdings less liabilities per share. ETFs can diversify, but their name does not guarantee diversification: inspect holdings and concentration. [The SEC ETF overview](https://www.investor.gov/introduction-investing/investing-basics/investment-products/mutual-funds-and-exchange-traded-2) explains the structure.

For intraday ML, broad or sector ETFs are useful context features and strong baselines. A stock model may look skilled only because it predicts the market's shared move. Test incremental out-of-sample value beyond a benchmark ETF.

## 4. Prediction markets are bounded event claims

**A YES/NO contract pays a fixed amount conditional on an event, so its price is a market-implied probability-like quantity after frictions.**

If YES settles at $1 on the event and $0 otherwise, a $0.62 price is the market's approximate valuation of that payoff. Fees, bid-ask spread, liquidity, risk preferences, and market design mean it is not automatically a perfect probability. It is nevertheless a strong baseline because it aggregates current participants' information.

This is why pmquant compares venue price `p` with calibrated `q-hat`, rather than treating each contract as a blank-slate classifier. The useful ML question is whether the model beats the market after costs at the actual tradable decision time.

## 5. Price, return, and total return differ

**Price is a level, return is a relative change, and total return includes cash distributions.**

For price `P_t`, simple return is `r_(t+1) = P_(t+1) / P_t - 1`; log return is `log(P_(t+1) / P_t)`. Returns make differently priced assets comparable and compose through time. Total return adds dividends; raw prices can jump mechanically on ex-dividend dates or stock splits.

For ML, predict a target that matches the decision. In dskit's intraday child, `WindowRows`, `SessionFeatureRows`, and `ReturnWindows` make features from information available at time `t` and labels from future movement over `[t, t+h]`. Preserve vendor adjustment conventions or a signal can be a corporate-action artifact.

## 6. Trading is an execution problem as well as a prediction problem

**The bid is the best advertised buy price, the ask is the best advertised sell price, and their gap is an immediate trading cost.**

A market buy generally executes near the ask; a market sell near the bid. A market order prioritizes execution but not price; a limit order sets a worst acceptable price but can remain unfilled. The last trade is neither a guaranteed buy nor sell price. [Investor.gov's order guide](https://www.investor.gov/introduction-investing/investing-basics/how-stock-markets-work/types-orders) and [FINRA's liquidity guide](https://syndication.finra.org/content/understanding-disclosure-documents) are useful primers.

For ML, a prediction is not a trade:

```text
expected net P&L = expected gross payoff - spread - fees - slippage - financing
```

Slippage is the adverse difference between assumed and achieved price. dskit's `FeedParity` and `PortfolioSelect` keep data and decision layers separate. pmquant likewise carries order-book depth and exact fees into its optimizer.

## 7. Liquidity and market impact limit usable edge

**Liquidity is the ability to trade meaningful size quickly near a fair price, while market impact is the price movement caused by your own order.**

A thin book may quote a tempting price for 10 shares but require progressively worse prices for 1,000. Volume is useful but incomplete; depth by price, spread, volatility, and other traders matter. A realistic backtest needs available quantity and bid/ask where turnover is material.

For ML, capacity is an out-of-sample constraint like latency in production ML. A model can rank opportunities correctly yet fail if it assumes every order fills at the midpoint. pmquant's L2 panels and mixed-integer optimizer explicitly constrain positions by depth, caps, and fee-adjusted edge.

## 8. Long, short, cash, and leverage are exposures

**A long position benefits when its asset rises, a short position benefits when it falls, and leverage magnifies both gains and losses.**

To short a stock, an investor generally borrows it, sells it, and later buys it back to return it; a rising price causes loss. Buying on margin borrows from a broker to enlarge a position. Both introduce financing, collateral, and forced-liquidation risk. Read [FINRA on stocks](https://www.finra.org/investors/investing/investment-products/stocks) and [margin calls](https://www.finra.org/investors/insights/margin-calls) before modeling either side.

For ML, a negative prediction does not authorize a short. First specify instruments, borrow availability, margin requirements, and worst-case loss. A bounded prediction contract and a short equity position have different risk geometry.

## 9. Portfolio risk comes from co-movement

**A portfolio is a set of positions whose joint behavior, especially correlation, determines risk more than each position alone.**

Many technology stocks are not independent bets: they may fall together on a rates or sector shock. Portfolio return is approximately the weighted sum of returns, while portfolio variance includes every pairwise covariance. [Investor.gov's asset-allocation guide](https://www.investor.gov/introduction-investing/getting-started/asset-allocation) cautions that narrowly focused funds may not diversify.

For ML, correlated predictions should not each receive full independent allocation. Evidence is correlated too: many tickers at the same timestamp share market information. dskit's `PortfolioSelect` and pmquant's `pmquant-kelly-mio` turn uncertain forecasts into jointly constrained positions.

## 10. Risk is more than volatility

**Risk is the possibility and shape of unfavorable outcomes, not simply the standard deviation of returns.**

Volatility summarizes ordinary variation, but a strategy can have low daily volatility and rare catastrophic losses. *Drawdown* is the fall from a prior portfolio peak; *tail risk* concerns unusually bad outcomes. Calibration error is financial risk too because overconfident probabilities produce excessive position sizes.

For ML, report more than mean P&L or RMSE: inspect net returns, turnover, maximum drawdown, exposure, and stressed periods. pmquant's fractional-Kelly sizing responds to probability uncertainty; calibration matters when position size is nonlinear in a probability.

## 11. Factors are shared drivers and strong baselines

**A factor is a common source of returns that can explain why many assets move together.**

The market factor captures broad equity movement; size, value, profitability, investment, momentum, sector, and interest-rate exposures are other common drivers. A factor model regresses strategy return on shared drivers; its unexplained residual is often called alpha, but that is a claim to test, not a reward for fitting a regression. The [Kenneth R. French Data Library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_Library.html) publishes daily and monthly Fama/French factor returns.

For ML, factor controls are baselines and confounder checks. If P&L vanishes after controlling for market and sector movement, a model may have learned beta rather than a distinct signal.

## 12. Backtesting is historical decision simulation

**A backtest asks what would have happened if the exact strategy, information set, and execution assumptions had existed at each historical decision time.**

Chronology is central: features use only information available by the decision timestamp; labels are in the future; train, validation, and test remain ordered. Avoid look-ahead bias, survivorship bias, revision leakage, and prices that were unavailable when a decision was made. Walk-forward validation repeatedly fits on the past and evaluates later unseen intervals, often with an embargo gap.

For ML, random K-fold validation is generally wrong for dependent time series. dskit's `pipeline walkforward`, locked holdouts, `HorizonScan`, and `NoInformationScan` encode the right discipline. In pmquant, point-in-time books, settlement labels, and decision-time lead grids serve the same role.

## 13. Multiple testing creates false discoveries

**When many features, assets, horizons, models, and hyperparameters are tried, some impressive results will appear from noise alone.**

Trying 200 variants at a 5% threshold can produce roughly 10 apparent successes in a no-signal world. Selecting a validation winner inflates reported results. Use a predeclared research ladder, untouched holdouts, nested tuning, correction across the tested family, and forward paper trading.

For ML, this is hyperparameter search plus adaptive data analysis, made harsher by finance's low signal-to-noise ratio. pmquant uses per-market tests and a Benjamini-Hochberg false-discovery-rate sweep before sizing; intraday_equities uses no-information and shuffled-label tests plus walk-forward evidence.

## 14. Calibration connects probability predictions to sizing

**A forecast is calibrated when events assigned probability about 70% occur about 70% of the time over comparable cases.**

Ranking can be good while calibration is poor. Two models can rank contracts identically, but one that says 0.99 for a 0.70 event induces far more aggressive sizing. Calibration plots, Brier score, log loss, and out-of-sample reliability curves expose different aspects of the mismatch.

For ML, pmquant is the clearest local example: market price is a baseline probability; the system estimates calibrated `q-hat`, tests whether the gap is real, then applies fractional Kelly. For equities, calibration can mean a calibrated return distribution or probability of positive *net* return.

## 15. Kelly sizing is a decision rule, not a source of edge

**Kelly sizing chooses a stake that maximizes expected logarithmic wealth growth under a specified probabilistic payoff model.**

For a simplified even-money bet with win probability `q`, full Kelly is `2q - 1`; real formulas depend on odds and fees. It assumes the probability and payoff model are right, so practical systems use fractional sizing, position caps, and scenario constraints. It cannot turn a zero-edge forecast into a positive-edge strategy.

For ML, separate forecast, decision, and risk layers. A model emits a distribution or score; a decision layer converts it to trades after costs; an optimizer selects feasible joint sizes. pmquant's MIO consumes probabilities, fee rules, depth, and constraints, while dskit's `PortfolioSelect` follows the same separation for equity returns.

## 16. The working sequence is data -> forecast -> decision -> evidence

**Quantitative finance applies statistics and ML only after defining the instrument, decision time, execution rules, and risk limits.**

1. Define the instrument and payoff in one paragraph.
2. State the decision timestamp and information available then.
3. Define a horizon-specific label and a simple benchmark.
4. Build a point-in-time, versioned data set.
5. Run chronological, cost-aware walk-forward evaluation.
6. Correct for search, inspect calibration and tails, then paper trade before risking capital.

dskit maps directly to this sequence: onboarding connectors preserve observations; child nodes make features and labels; pipeline documents make experiments reproducible; walk-forward runs enforce time order; and selection/optimization nodes translate forecasts into constrained actions.

## Glossary

**A compact vocabulary keeps the statistical discussion attached to the real financial object.**

- **Asset / instrument:** a tradable financial claim or contract.
- **Equity / stock:** an ownership claim on a corporation.
- **Bond:** a loan with promised interest and principal repayment.
- **ETF:** an exchange-traded pooled portfolio of holdings.
- **Return:** proportional price change; total return adds distributions.
- **Bid / ask / spread:** best displayed buyer price / seller price / their gap.
- **Liquidity:** ease of trading meaningful size near a fair price.
- **Position:** an exposure held long or short.
- **Portfolio:** all positions held together.
- **Alpha:** return unexplained by chosen benchmarks; a hypothesis, not profit.
- **Factor:** shared driver of many returns.
- **Calibration:** agreement between stated probabilities and long-run frequencies.
- **Backtest:** historical simulation of the complete decision process.
- **Drawdown:** decline from a prior portfolio peak.

## Where to go next

**The fastest route is to learn one market deeply while keeping the product, data, and decision rule small.**

Start with intraday equities to learn market microstructure, returns, and execution. Start with pmquant for the closest bridge from probabilistic ML to a bounded payoff and calibration. In either case, trace one run document from source through label, forecast, evaluation, and selection before changing a model family.

Primary references: [SEC EDGAR filing guide](https://www.sec.gov/search-filings/edgar-search-assistance/using-edgar-research-investments), [Investor.gov product basics](https://www.investor.gov/introduction-investing/investing-basics/investment-products), [Investor.gov order types](https://www.investor.gov/introduction-investing/investing-basics/how-stock-markets-work/types-orders), and [FINRA on margin and short selling](https://www.finra.org/investors/investing/investment-products/stocks).
