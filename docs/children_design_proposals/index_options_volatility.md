# Index Options Volatility Research

## TL;DR

Build a research system that forecasts market volatility and downside risk, then tests whether those forecasts improve the selection and sizing of limited-risk index-option portfolios after realistic trading costs. The intended outcome is both a credible quantitative-finance research project and, only if evidence and operational checks justify it, a small live trading pilot. Profitability is an open hypothesis: better machine learning, attractive backtests, and frequent winning trades do not establish a deployable edge.

**Status:** explanatory research proposal, not an approved implementation or trading mandate.

**Prepared:** September 22, 2026.

**Audience:** a researcher comfortable with statistics and machine learning but new to derivatives.

**Evidence status:** no strategy backtest, live trading result, or expected return is claimed in this document. All numerical examples are invented teaching examples, not market quotes or recommendations.

This document is self-contained. It does not require an earlier conversation, another project, or familiarity with any particular software repository.

## Contents

1. [Purpose and research question](#1-purpose-and-research-question)
2. [The proposed first project](#2-the-proposed-first-project)
3. [Financial instruments and essential terms](#3-financial-instruments-and-essential-terms)
4. [Where an economic return might come from](#4-where-an-economic-return-might-come-from)
5. [Worked option-payoff examples](#5-worked-option-payoff-examples)
6. [What the models actually predict](#6-what-the-models-actually-predict)
7. [Portfolio construction and mixed-integer optimization](#7-portfolio-construction-and-mixed-integer-optimization)
8. [Data requirements and acquisition checks](#8-data-requirements-and-acquisition-checks)
9. [Research and validation practices](#9-research-and-validation-practices)
10. [Execution, accounting, and deployment](#10-execution-accounting-and-deployment)
11. [Risks, resources, and operating constraints](#11-risks-resources-and-operating-constraints)
12. [Milestones and decision gates](#12-milestones-and-decision-gates)
13. [Learning and career deliverables](#13-learning-and-career-deliverables)
14. [Decisions required before implementation](#14-decisions-required-before-implementation)
15. [Glossary of modeling and evaluation terms](#15-glossary-of-modeling-and-evaluation-terms)
16. [Sources and suggested reading](#16-sources-and-suggested-reading)

## 1. Purpose and research question

The project has four objectives:

- Learn a genuinely new financial instrument: exchange-traded index options.
- Apply substantive ML and optimization to a financially meaningful problem.
- Investigate a plausible source of investment returns with a realistic retail deployment path.
- Produce a reproducible research artifact demonstrating skills relevant to systematic and derivatives-oriented quantitative roles.

The central question is:

> Do forecasts of future volatility and tail risk improve the net return–risk tradeoff of a constrained index-option strategy relative to simple, implementable alternatives?

This differs from forecasting which individual stocks will rise. An option's value depends on the distribution of future outcomes, its nonlinear payoff, remaining time, and the price the market charges for taking risk. Being right about the direction of the index is neither necessary nor sufficient to make money on an option position.

Three successes must be assessed separately:

1. **Scientific success:** a valid, reproducible answer to the hypothesis, including a negative answer.
2. **Operational success:** a system that handles real instruments, costs, orders, accounting, and failures correctly.
3. **Investment success:** sufficiently attractive prospective net returns for the capital and risks involved.

A project can achieve the first two without achieving the third. Deployment should never be used to rescue an unconvincing research result.

## 2. The proposed first project

### 2.1 Narrow scope

The first candidate is a **forecast-conditioned, defined-risk index-option strategy** on one broad equity index. Begin with same-expiration **iron condors**, explained in Section 5: a combination of a protected put sale and a protected call sale. A no-trade decision is always permitted.

This is a proposed experimental specification, not an empirically optimal strategy:

- **Underlying market:** one broad equity index, initially the S&P 500 family.
- **Instrument candidates:** SPX or smaller XSP cash-settled index options. Choose one execution product after a data, cost, broker, and capital audit; do not assume they have interchangeable execution quality.
- **Entry maturity:** options with 30–60 calendar days remaining. Freeze a deterministic expiry-selection rule within that window before testing.
- **New-entry cadence:** weekly, with portfolio state and risks checked every trading day.
- **Initial holding rule:** hold intact positions to their official cash settlement, except for a separately specified emergency-risk procedure.
- **Initial modeling:** classical volatility benchmarks, then a small gradient-boosted model and an explicit scenario model.
- **Initial sizing:** conservative deterministic sizing; evaluate MIO as a separate improvement rather than assuming it helps.
- **Excluded initially:** naked option sales, same-day-expiry trading, single-stock earnings bets, continuous delta hedging, leverage used to boost reported returns, and autonomous live RL exploration.

The hold-to-settlement rule deliberately makes the first experiment simpler: terminal contract payoffs are known functions of the settlement value. Positions may last roughly one to two months. A later study can examine earlier exits, but then it must model future option prices and execution, not only terminal index values.

Daily monitoring is still necessary. A bounded terminal payoff does not eliminate mark-to-market losses, changing buying-power requirements, outages, or the possibility that an emergency exit is costly.

### 2.2 Why these contracts are candidates

SPX and XSP are options on an index, not ownership of an ETF. Cboe describes XSP as one-tenth the SPX index scale, with a $100 multiplier, European exercise, and cash settlement. European exercise removes early exercise; cash settlement avoids delivery of an unwanted stock position. Neither eliminates market loss. Contract size alone is insufficient: compare executable quotes, fees, available expirations, and broker support. [Cboe XSP specifications](https://www.cboe.com/tradable_products/sp_500/mini_spx_options/specifications)

SPY options are a possible later alternative, but they are options on an exchange-traded fund. Their exercise and delivery mechanics require a different assignment and settlement implementation; they must not silently substitute for cash-settled index contracts. Consult the current [OCC options disclosure](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document).

## 3. Financial instruments and essential terms

### 3.1 The contract

An **index** is a calculated measure of a basket of securities. It is not itself a share that can be purchased. An **ETF**, or exchange-traded fund, is a tradable fund that may track that index. A **derivative** is a contract whose value depends on an underlying asset or reference value.

A **call** has a payoff that increases when the underlying settles above its **strike**, the contract's specified reference level. A **put** pays when settlement is below its strike. **Expiry** is when the option ends; the contract's official **settlement value** determines its final cash payoff. That value need not equal an arbitrary closing price downloaded from a market-data service.

For a European cash-settled option, define:

- `S_T`: the official index settlement value at expiry `T`.
- `K`: the strike, in index points.
- `M`: the multiplier, in dollars per index point.
- `max(a, b)`: the larger of `a` and `b`.

The buyer's terminal cash receipt is:

```text
Call receipt = M × max(S_T − K, 0)
Put receipt  = M × max(K − S_T, 0)
```

The seller owes the corresponding receipt. These are **payoffs**, not profits: the price paid or received for the contract must also be included.

The **premium** is that price. A quote of `2.40` with a $100 multiplier means $240 per contract, before fees. **Long** means holding a purchased contract; **short** means having sold a contract and retaining its obligation. A **leg** is one component of a multi-option position.

**In the money** means positive immediate exercise value; **out of the money** means no immediate exercise value. An out-of-the-money option can still have a positive price because it may finish in the money. **Notional exposure** describes the reference scale of the contract; it is different from premium, margin, and possible loss.

### 3.2 Volatility and the option surface

**Return** is a relative price change. A **log return** is `log(new price / old price)`. **Variance** measures dispersion in returns; **volatility** is its square root. In this document, quoted volatility percentages are annualized unless explicitly labeled otherwise.

**Realized volatility** summarizes movement that actually occurred. Its estimate depends on sampling frequency, treatment of overnight returns, and annualization. **Implied volatility**, or IV, is the volatility input that makes an option-pricing formula reproduce an observed option price under that formula's assumptions. It is a useful quoting language, not a promise about future movement. [OIC volatility overview](https://www.optionseducation.org/videolibrary/historical-volatility)

A **volatility smile or skew** describes how implied volatility varies across strikes for one expiry. The **term structure** describes how it varies across maturities. The **volatility surface** combines strike and maturity. **Moneyness** locates a strike relative to the underlying or forward price. **Forward price** is the price implied for future delivery after considering financing and distributions.

Twenty percent annualized volatility does not mean “the index will move 20% next month.” Under a simplified constant-volatility scaling approximation:

```text
Annualized volatility                 = 0.20
Trading-year fraction for 21 sessions = 21 / 252 = 1 / 12
Approximate 21-session volatility     = 0.20 × sqrt(1 / 12)
                                     ≈ 0.0577 = 5.77%
```

This is a scale estimate, not a bound or calibrated probability interval. Actual markets have jumps, changing volatility, and heavier tails than a simple Gaussian model. Calendar-day option expiry and trading-day return labels must be aligned explicitly.

### 3.3 Risk sensitivities: the Greeks

The **Greeks** summarize local sensitivities of an option's modeled value:

- **Delta:** sensitivity to the underlying level. A short put generally has positive delta, so it is not automatically direction-neutral.
- **Gamma:** how delta changes as the underlying moves. It matters because option payoffs are curved rather than linear.
- **Vega:** sensitivity to implied volatility. Confirm whether a system reports a change for one percentage point or for a full unit of volatility.
- **Theta:** sensitivity to time passing under a stated convention. Time decay is not guaranteed profit; other risks can dominate it.
- **Rho:** sensitivity to interest rates.

For a multi-leg position, quantities, signs, and multipliers must be incorporated consistently. First-order Greek approximations are useful for diagnostics, but large market moves require full repricing. A portfolio with near-zero delta can still have substantial gamma, volatility, or crash exposure.

### 3.4 Trading and capital terms

The **bid** is a quoted buying price; the **ask** is a quoted selling price. Their difference is the **bid–ask spread**. The midpoint is a valuation convention, not an executable fill guarantee. **Liquidity** is the ability to transact an intended size at reasonable cost; yesterday's volume or open interest does not guarantee it.

**Slippage** is the difference between a chosen reference price and execution. **Margin** or **buying power** is collateral required by the broker; it is not a measurement of the strategy's true worst-case loss. **Mark-to-market P&L** values positions before expiry. **P&L** means profit and loss.

**Collateral yield** is interest earned on eligible uncommitted or collateral cash. **Financing cost** is what borrowing or carrying exposures costs. Both belong in the accounting. **Drawdown** is the decline from a previous portfolio-equity peak. **Tail risk** concerns infrequent but severe losses, including losses larger than the historical sample suggests.

## 4. Where an economic return might come from

### 4.1 Risk premium is not the same as mispricing

Investors may pay to transfer downside and volatility risk. Historical S&P 500 option studies document differences between implied and subsequently realized volatility, often described as a volatility risk premium. Cboe's summary of Bondarenko's put-writing study provides one historical example, but its hypothetical cash-secured put benchmark is not the protected, forecast-conditioned strategy proposed here. It does not prove that buying protective wings and paying retail costs preserves the result. [Cboe research summary](https://www.cboe.com/insights/posts/white-paper-shows-volatility-risk-premium-facilitated-higher-risk-adjusted-returns-for-put-index)

There are two distinct hypotheses:

1. Taking certain option risks may earn compensation over time.
2. Our forecasts and decisions may improve the compensation received for a given level of risk.

Only the second is evidence of incremental value from this research system. A profitable short-option portfolio may primarily be collecting insurance-like compensation while occasionally suffering large losses. Buying protection can reduce those losses but costs money and may consume the expected premium.

### 4.2 Physical versus risk-neutral probabilities

The **physical distribution**, often labeled `P`, describes forecasts of what will actually happen. The **risk-neutral distribution**, labeled `Q`, is a pricing representation that incorporates market prices of risk; it is not generally the actual frequency distribution of future outcomes.

Option prices constrain `Q` under a pricing framework. Historical outcomes help estimate `P`. The difference between the two can be rational compensation for risk rather than an error to arbitrage away.

Accordingly, “market IV is 24%, my volatility forecast is 18%, therefore sell” is not a complete trading argument. Strike location, skew, jump risk, protection cost, financing, execution, and forecast uncertainty all matter. Option delta is not automatically a real-world probability of expiring in the money.

### 4.3 What the evidence does not promise

Goyal and Saretto examined 46 previously studied equity-option strategies and found that factor adjustment and transaction costs substantially weakened the apparent anomalies; none retained significant alpha after costs in their analysis. Their single-stock-option study does not directly test this index-spread proposal, but it is strong reason to demand rigorous cost and risk controls rather than extrapolate attractive gross returns. [Goyal and Saretto, 2025](https://academic.oup.com/rfs/article/38/6/1783/8010873)

There is no established retail advantage asserted here. The practical appeal is that low-frequency research can be conducted without competing on microsecond speed. Success still depends on data, forecasting, disciplined sizing, and executable prices.

## 5. Worked option-payoff examples

All prices, probabilities, fees, and account sizes in this section are invented. They illustrate mechanics, not expected performance. Premiums below are assumed execution prices; hypothetical additional costs are stated separately to avoid counting the bid–ask spread twice.

### 5.1 A protected put sale

Assume a cash-settled index is at 500, with a $100-per-point multiplier. Two puts share the same expiry and settlement convention:

1. Sell one 485-strike put for 4.20 points: receive `4.20 × $100 = $420`.
2. Buy one 480-strike put for 2.60 points: pay `2.60 × $100 = $260`.
3. Net premium received: `$420 − $260 = $160`.
4. Strike width: `485 − 480 = 5` points, or `$500`.

This is a **put credit spread**, also called a bull put spread. Its protected short put benefits from avoiding a sufficiently large decline. [OIC spread mechanics](https://prd-web.optionseducation.org/strategies/all-strategies/bull-put-spread-credit-put-spread)

For terminal settlement `S_T`, before costs and interest:

```text
P&L = $160 − $100 × max(485 − S_T, 0)
           + $100 × max(480 − S_T, 0)
```

Consider four settlements:

- **At 500:** neither put pays. P&L is `$160`.
- **At 483:** the short put costs `(485 − 483) × $100 = $200`; the long put pays zero. P&L is `$160 − $200 = −$40`.
- **At 480:** the short put costs `$500`; the long put pays zero. P&L is `$160 − $500 = −$340`.
- **At 475:** the short put costs `$1,000`; the long put pays `$500`. P&L is `$160 − $1,000 + $500 = −$340`.

Thus, the maximum contractual profit is $160 and the maximum contractual loss is $340 before costs. The expiry break-even level is `485 − 1.60 = 483.40`. With an invented $4 of additional total costs, the maximum net profit becomes $156 and the maximum net loss becomes $344.

These bounds require the specified matched legs to remain intact. Different expirations, mismatched quantities, or an accidentally unhedged short leg are different positions. The maximum-loss figure is not a guaranteed broker margin requirement or a guaranteed early-exit execution price.

### 5.2 The first candidate family: an iron condor

Add a protected call sale with the same expiry and settlement:

1. Sell one 515-strike call for 2.80 points: receive $280.
2. Buy one 520-strike call for 1.80 points: pay $180.
3. The call spread adds `$280 − $180 = $100` of credit.
4. Total credit across all four legs is `$160 + $100 = $260`.

This **iron condor** combines the put credit spread and a call credit spread. With equal five-point wings, its terminal P&L is:

```text
$260
− $100 × max(485 − S_T, 0)
+ $100 × max(480 − S_T, 0)
− $100 × max(S_T − 515, 0)
+ $100 × max(S_T − 520, 0)
```

At settlement:

- Between 485 and 515, all options have zero payoff; profit is $260 before costs.
- At 483 or 517, one short option costs $200; profit is $60 before costs.
- At or below 480, or at or above 520, the losing wing costs $500; loss is `$500 − $260 = $240` before costs.

Only one side can be breached at the same terminal settlement. This is why the maximum loss for this matched, equal-width condor is $240 rather than the sum of two isolated spread-loss figures. Unequal widths require using the larger terminal wing loss. Different expiries do not share this simple formula.

With invented total additional costs of $8, maximum net profit is $252 and maximum net loss is $248. Break-even settlements before costs are `485 − 2.60 = 482.40` and `515 + 2.60 = 517.60`.

This is not riskless or perfectly direction-neutral. Its risk changes with the index level, volatility surface, and remaining time. Multiple condors on the same index are strongly related exposures, not independent diversification.

### 5.3 A high win rate can hide a weak strategy

Return to the isolated put spread and use an intentionally crude two-outcome model: it either earns its maximum $160 or loses its maximum $340. Real spreads also have intermediate outcomes.

Assume a model claims a 70% chance of the first outcome and 30% of the second:

```text
Expected gain contribution = 0.70 × $160 = $112
Expected loss contribution = 0.30 × $340 = $102
Expected gross P&L         = $112 − $102 = $10
After $4 additional costs  = $10 − $4 = $6
```

If the true probabilities are instead 65% and 35%:

```text
Expected gross P&L = 0.65 × $160 − 0.35 × $340
                   = $104 − $119 = −$15
After costs        = −$15 − $4 = −$19
```

A small probability-estimation error flips the sign. The model must earn the right to allocate capital through calibration and uncertainty assessment, not just a high classification accuracy.

If this one position belonged to a $10,000 dedicated account, its $156 maximum net win would add only `156 / 10,000 = 1.56%` to account equity. A percentage computed using only the broker's minimum margin is not the account return. Repeating the maximum win every month is not a legitimate annual-return forecast.

## 6. What the models actually predict

### 6.1 Start with volatility, but do not stop there

Forecast the distribution of index outcomes over each candidate's actual holding horizon. A volatility forecast is one component, not a full description of that distribution: skewness, jumps, return–volatility dependence, and tails affect option payoffs.

For the unhedged hold-to-settlement experiment, two market paths ending at the same settlement value produce the same contractual condor payoff even if their realized volatility differs dramatically. Volatility forecasting is therefore a useful input to distribution modeling, not the strategy's payoff target. This is not a direct trade in realized variance.

An initial model ladder is:

1. **Persistence baseline:** recent realized variance predicts future variance.
2. **HAR-style baseline:** a regression combines short-, medium-, and longer-window realized-variance measures. HAR means heterogeneous autoregressive; Corsi's model motivates using multiple time scales. [Corsi, 2009](https://doi.org/10.1093/jjfinec/nbp001)
3. **Gradient-boosted regression:** predict log future variance, or its residual relative to the baseline, using a bounded feature set.
4. **Conditional scenario construction:** use those forecasts with an explicit, validated return-path model to estimate the distribution of spread outcomes.

Start with a small candidate set of features: recent realized variance at multiple scales, negative-return variation, large-move indicators, observed IV level, skew, term-structure slope, and known calendar events. Use only values published by the decision time. Feature selection and transformations are fitted on training data, not on the final evaluation period.

Do not begin with a large architecture search. A compact boosted-tree model is the proposed starting model because it permits nonlinear interactions while remaining easy to benchmark and diagnose. This is an engineering choice, not a claim that trees dominate all alternatives.

### 6.2 Label and timing contract

Define the target before choosing the model. One possible label is total realized variance between the permitted entry timestamp and the option's settlement timestamp. It must include the intended overnight component and use a consistent measurement convention.

Train either a small, prespecified set of horizons or a maturity-conditioned model. Do not equate a 21-trading-session volatility forecast with every contract labeled “30 days.” Weekends, holidays, partial sessions, and expiry conventions matter.

For log-variance regression, exponentiating a predicted mean log variance does not generally recover mean variance. Use a documented transformation/bias treatment or evaluate predictions on their actual target scale; do not quietly relabel them as an expected variance forecast.

Daily close-to-close squared returns are a noisy proxy. If intraday observations are unavailable, describe the model as using a lower-frequency variance proxy rather than claiming a full high-frequency realized-variance implementation. Overnight and intraday components should not be omitted or counted twice.

### 6.3 Turning a forecast into scenarios

A practical first scenario engine can resample blocks of historical returns using information available at each training cutoff, condition their scale on the variance forecast, and preserve an explicit overnight/jump treatment. A parametric heavy-tailed model is a competing specification, not unquestioned truth.

For every scenario, compute the contractual terminal payoff of every candidate portfolio. Estimate expected P&L, downside quantiles, and scenario loss concentrations after costs and financing. Simulate correlated paths jointly for outstanding positions; candidates on the same index cannot be sampled independently.

This bridge is a major research risk. Matching average volatility does not guarantee correct downside probabilities. Validate predictive intervals and exceedances out of sample, compare multiple defensible scenario specifications, and report sensitivity to tail assumptions. Add severe deterministic stresses even when they are rare or absent in the sample; label them as stresses rather than estimated probabilities.

### 6.4 Option pricing and surface fitting

Historical execution P&L should use actual admissible quotes and official settlements, not a model's preferred “fair prices.” A pricing library and fitted surface remain useful for IV extraction, Greeks, diagnostics, and stress repricing.

The **Black–Scholes–Merton model** is a useful introductory reference for European option pricing. It relates option value to the underlying level, strike, time to expiry, interest rate, dividend assumptions, and volatility under idealized market and return-process assumptions. Inverting its price formula gives an implied-volatility quote. Those assumptions do not become true because the inversion fits a traded price: the market's different IVs across strikes are themselves evidence against treating one constant volatility as a complete description. Use the model as a consistent pricing/quoting baseline, not as an oracle for actual return probabilities. A **stochastic-volatility model** instead allows volatility itself to evolve randomly; that is a possible later modeling extension. [OIC pricing explanation](https://prd-web.optionseducation.org/advancedconcepts/black-scholes-formula)

**No-arbitrage constraints** enforce consistency among modeled option prices under stated assumptions. For example, an unconstrained interpolator can imply impossible strike relationships. Gatheral and Jacquier provide conditions for static-arbitrage-free SVI surfaces. SVI is a parameterization of implied total variance; arbitrary SVI parameters do not automatically satisfy those conditions. [Gatheral and Jacquier](https://arxiv.org/abs/1204.0646)

Surface fitting is not alpha by itself. A smoother fitting today's quotes closely has not predicted tomorrow's P&L. Do not manufacture favorable fills from interpolated prices for missing or stale quotes.

The initial hold-to-settlement experiment needs terminal-payoff scenarios for expected-return estimation, but it still needs credible interim stress valuation and liquidity checks. Any later early-exit strategy additionally requires a model of future surface states and exit costs.

### 6.5 Where deep learning and RL belong

**Deep learning**, or DL, can later model conditional distributions, option surfaces, or return–volatility dynamics. Add it only with a hypothesis about what it captures beyond the simpler model, a fixed comparison budget, and out-of-sample evidence.

**Reinforcement learning**, or RL, learns sequential decisions from states, actions, and a reward or risk objective. A later hedging study could use:

- State: current contracts, Greeks, remaining time, market features, and cash.
- Action: bounded hedge adjustment or a no-adjustment decision.
- Objective: reduce hedging cost and adverse P&L under an explicit risk measure.
- Benchmarks: periodic delta hedging and a no-trade-band policy that hedges only when exposure crosses a threshold.

**Hedging** means taking an offsetting exposure to reduce a specified risk. **Delta hedging** offsets the sensitivity to small underlying moves, for example with an appropriate underlying proxy or futures position. It does not automatically remove volatility risk, jumps, or losses from imperfect hedge matching.

The Deep Hedging literature establishes a framework for learning under trading frictions, including synthetic-market experiments; it is not evidence that an RL agent will generate live retail alpha. [Bühler and coauthors](https://arxiv.org/abs/1802.03042)

Hedging and alpha are separate questions. A better hedge may reduce the cost of an existing obligation without creating a profitable opportunity to originate that obligation. Hedge instruments add their own basis, financing, trading-hour, and execution risks. Initial RL work belongs in simulation, with stress tests for simulator misspecification—not exploration with real capital.

## 7. Portfolio construction and mixed-integer optimization

### 7.1 Why an optimizer might help

For each decision, enumerate a small, prespecified menu of admissible condors. Within each condor, all four legs share an underlying, expiry, settlement convention, and matched wing quantities. Different candidates may have different strikes, wing widths, or expiries. The menu must respect quote quality and must not be expanded after inspecting the final test result.

**Mixed-integer optimization**, or MIO, can choose integer numbers of these portfolios. Its purpose is to respect discreteness and constraints, not to repair inaccurate forecasts. If one candidate is allowed at a time, a simple rule may be just as effective and more transparent. Compare them.

### 7.2 An illustrative optimization problem

Fix a decision time `t` and a common valuation date `H` no earlier than the latest expiry included in this optimization. Express every premium, fee, settlement receipt/payment, and released cash balance in dollars at `H` under a stated financing convention. Early settlements remain in cash until `H`; do not assume unmodeled future trades or free reinvestment at the strategy's advertised return.

Measure forward changes from current marked portfolio equity, relative to holding the same equity in the specified cash alternative until `H`. In particular, the existing book starts from today's marks: do not add its already-earned gains or entry premiums again. Different-expiry outcomes can only be aggregated after this time and accounting alignment. Use a separate daily path ledger and constraints for interim buying power, liquidity, and losses; terminal CVaR alone does not ensure that positions can be funded along the way.

Let:

- `j` index candidate new positions; `s` index joint market scenarios.
- `n_j` be the nonnegative integer number of units of candidate `j`.
- `y_j` be a binary indicator equal to one when candidate `j` is selected.
- `g_js` be candidate `j`'s incremental scenario P&L per unit at `H`, including signed entry/settlement cashflows and subtracting variable costs and the appropriate cash-opportunity/financing effects.
- `b_s` be the existing book's forward incremental scenario P&L from its current marks to `H`, relative to the same cash alternative.
- `f_j` be any additional fixed ticket cost, carried to `H` and not already in `g_js`.
- `p_s` be scenario probabilities, nonnegative and summing to one.

Define total scenario P&L:

```text
G_s = b_s + sum_j(n_j × g_js) − sum_j(f_j × y_j)
L_s = −G_s
```

Here `L_s` is loss; a larger value is worse. An illustrative objective is:

```text
Maximize expected G_s − λ × CVaR_q(L)
```

`λ` is a prespecified nonnegative risk-aversion weight. **CVaR**, conditional value at risk or expected shortfall, measures average loss in the selected worst tail. At confidence `q = 0.95`, the target is the worst 5% of the modeled loss distribution. It is not a worst-case guarantee.

For a finite scenario set, a standard formulation introduces threshold `a` and nonnegative auxiliary variables `u_s`:

```text
CVaR_q = a + sum_s(p_s × u_s) / (1 − q)
u_s >= L_s − a
u_s >= 0
0 <= n_j <= N_j × y_j
y_j <= n_j
```

`N_j` is a finite position-unit limit. The last two constraints, together with integer `n_j` and binary `y_j`, make activation equivalent to taking at least one unit. One condor unit contains four option contracts; enforce actual contract limits after expanding/netting the legs. With a fixed scenario matrix and linear constraints, this can be implemented as a mixed-integer linear program. Nonlinear risk terms or different execution models change the problem class.

Require explicit constraints on cash reserves, broker buying power, contractual payoff exposure, total contracts, expiry concentration, and stress losses. A simple Greek limit is useful but cannot replace nonlinear stress testing. Use consistent dollar units, and account for shared legs and portfolio netting before generating actual orders.

The solver also needs a timeout policy and an independent post-solve checker. A solver's optimality gap describes the numerical solution to the supplied model; it says nothing about whether the forecasts are correct or the strategy is safe.

Official Gurobi examples illustrate integer decisions and fixed/variable transaction costs in portfolio rebalancing. The formulation above is a proposed adaptation to option scenarios, not a reproduced result from those examples. Check licensing before choosing a solver. [Gurobi finance examples](https://gurobi-finance.readthedocs.io/en/latest/modeling_notebooks/rebalancing.html)

### 7.3 Respect model uncertainty

The optimizer will favor small estimated differences unless constrained. Use conservative position caps, minimum estimated improvement over costs, and sensitivity tests across forecast and scenario specifications. Estimate any uncertainty discount from training/validation evidence rather than calling a heuristic a certified confidence bound.

Always compare with a simple risk-budgeted rule using the same forecasts, candidate set, and costs. MIO earns its complexity only if it improves implementability or the evaluated return–risk tradeoff.

## 8. Data requirements and acquisition checks

### 8.1 Minimum dataset

Acquire and version:

- Historical option chains with stable contract identifiers, strike, option type, expiry, multiplier, exercise style, settlement convention, bid, ask, quote sizes, and timestamps.
- Underlying index observations and, if used, properly aligned intraday returns; do not substitute a scaled ETF without documenting basis and distribution differences.
- Official settlement values, exchange calendars, trading-session boundaries, and contract listings/delistings.
- Interest-rate and collateral-yield series known at each decision date; explicit dividend/forward inputs for pricing where needed.
- Historical fees where available, plus conservative documented assumptions for unavailable schedules.
- Point-in-time event-calendar information and publication timestamps for any external features.

Vendor volume, open interest, and analytics may become available at different times. The data contract must record when each field was actually knowable, not merely which date it describes.

### 8.2 What available data can and cannot prove

Cboe's Option EOD Summary describes 15:45 Eastern and end-of-day bid/ask snapshots, optional IV/Greek calculations, and historical coverage beginning in 2012. Its description also distinguishes access to index values from access to option records. Verify the product's current licensing, fields, per-symbol history, and full-period quote before purchase. A catalog start date does not establish complete XSP coverage. [Cboe data specification](https://datashop.cboe.com/option-eod-summary)

An end-of-day dataset can support a low-frequency research prototype. It cannot reproduce intraday queue position, exact complex-order fills, or what happened during an outage. If the production order policy uses intraday events, the simulator needs corresponding data or an explicitly conservative approximation.

### 8.3 Quality gates

Reject or quarantine malformed contracts, impossible spreads, missing settlement metadata, crossed/stale quotes, and mismatched timestamps. Record every exclusion and its effect on coverage. Use thresholds defined on training data and market mechanics, not chosen to improve final-test P&L.

Missing data during volatile periods is especially dangerous. A strategy that silently skips its hardest days can look excellent. Report missingness by market regime, including the consequences for open positions; never assume a missing quote permits a favorable exit.

Version raw data, transformations, source entitlements, model inputs, and corrections. Respect data licenses: a public research repository may include code, schemas, and synthetic fixtures without redistributing proprietary quotes.

## 9. Research and validation practices

### 9.1 Write the experiment contract first

Before model fitting, record the universe, entry and exit rules, decision timestamps, candidate-menu construction, features, targets, cost treatment, benchmarks, hyperparameter budget, metrics, splits, and rejection criteria. Preserve all attempted variants, including failed ones.

The claim should be narrow: for example, “this forecast improves risk-adjusted net outcomes relative to a fixed-rule condor under these execution assumptions.” Do not convert it into “ML options trading is profitable” without evidence.

### 9.2 Use time-respecting evaluation

Use an outer **walk-forward** evaluation: train on earlier dates and evaluate on later dates. Tune within earlier training/validation windows. A final **lockbox** period is withheld until the research specification is frozen; repeated inspection spends that protection.

Keep all contracts from the same decision date in the same fold. Thousands of strikes on one date are not thousands of independent market histories. Group uncertainty estimates by date or suitable time blocks.

Labels overlap when different entry dates share future returns or expiries. **Purge** training observations whose label intervals intersect validation or test intervals. Apply an appropriate time gap where the actual feature/label design requires it; a ceremonial one-day embargo does not solve a two-month overlap. Training labels must have fully matured before each simulated fit date.

If positions span a model-refit boundary, replay their actual continuing ownership. Do not liquidate and restart for free at every fold. Research lockbox discipline and live operational continuity are different responsibilities.

### 9.3 Measure forecast quality separately

For variance forecasts, use more than squared error. One candidate is the variance forecasting loss `v / v_hat − log(v / v_hat) − 1`, for positive realized variance proxy `v` and positive forecast `v_hat`, with a documented numerical policy near zero. This is a QLIKE-style loss; it evaluates a forecast, not profitability.

For distribution forecasts, evaluate interval coverage, quantile loss, and the frequency and clustering of downside exceedances. A nominal 95% interval should not be marketed as protective if severe events consistently occur outside it. Tail data are scarce; express uncertainty instead of pretending calibration is precisely known.

For all metrics, compare with persistence, the classical baseline, and appropriately maturity-matched market-implied information. Inspect both quiet and stressed periods. Log-space accuracy alone is insufficient if the strategy fails on high-variance days.

### 9.4 Measure economic outcomes honestly

Report account-level returns on committed capital, including idle cash, losses, costs, and financing. Include net P&L, maximum drawdown, expected shortfall, turnover, number of trades, worst days, exposure concentration, and sensitivity to adverse fills.

**Sharpe ratio** compares excess return with return variability, but it can hide infrequent option losses. Show the underlying return series and tail behavior rather than advertising one annualized number. Confidence intervals must acknowledge serial dependence and overlapping positions; do not treat every option row as an independent observation.

Benchmark against:

1. Cash or a realistic short-term government-bill alternative, with implementable costs/yield.
2. A fixed-rule, risk-budgeted condor using the same instrument, entry schedule, candidate constraints, and accounting.
3. Classical forecast-driven selection under the same constraints.
4. ML with simple sizing versus the same ML with MIO.
5. A broad-equity benchmark as exposure context, not as the only relevant comparator.

Risk-match comparisons where practicable. Investigate whether improvement is mostly lower exposure, more cash, market direction, volatility-risk exposure, or actual selection value. A simpler strategy taking less risk may explain an apparent ML improvement.

### 9.5 Falsification and stress tests

The strategy must survive attempts to disprove it:

- Increase costs and worsen fill assumptions; remove midpoint-fill optimism.
- Delay execution; vary reasonable quote-age and size assumptions.
- Remove feature families and compare with matched-budget simpler models.
- Test alternative defensible tail models and large index/volatility shocks.
- Evaluate missing-data days, delayed settlements, rejected orders, and broken feeds.
- Examine whether one period or a few favorable trades explain the result.
- Account for the total number of strategy/model variants tried.

A modest positive result with a wide confidence interval is preliminary evidence, not a production certificate. If the result depends on untradeable marks, omitted crashes, or a fragile modeling choice, stop or narrow the claim.

## 10. Execution, accounting, and deployment

### 10.1 Information must precede execution

If the forecast uses an end-of-day snapshot, it cannot also fill at that snapshot's price. Use a later eligible quote, such as a prespecified window in the next session, and recheck the candidate against then-current quotes and constraints.

Do not imply that separately visible best quotes for four legs guarantee a simultaneous package fill. A conservative first backtest can buy at asks and sell at bids with size/age filters, then stress costs further. A production implementation should use broker-supported combination limit orders where suitable, while explicitly handling rejection, partial quantity fills, timeout, and cancellation.

The system must verify matched protection after every fill event. No fallback may quietly turn a rejected combination order into naked option exposure. A timeout does not prove an order was canceled; query authoritative broker state before retrying.

### 10.2 One position ledger

The accounting system records orders, fills, quantities, premium cashflows, fees, collateral, interest, unrealized marks, expiries, and settlement. It reconciles against the broker. Daily account equity and realized terminal P&L must agree through an auditable cash-and-position identity.

Track economic exposure by contract, not by strategy nickname. Two candidate condors may share a leg; net them correctly without accidentally removing required protection. Existing positions count against every portfolio risk limit.

Reject ambiguous exercise/settlement metadata. AM- and PM-settled products must not be treated as interchangeable. Verify the exact contract specifications and current [OCC disclosure](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document) before implementing settlement behavior.

### 10.3 Staged release

**Historical replay:** tests financial accounting and behavior against recorded data. It estimates historical outcomes under assumptions, not actual achievable fills.

**Shadow mode:** consumes live data and produces decisions without submitting orders. Compare live feature values and decisions with the offline pipeline; retain rejected decisions as well as accepted ones.

**Paper trading:** submits simulated orders. It checks scheduling, state transitions, and broker integration, but paper fills may be much more generous than real fills.

**Limited live pilot:** requires a separate explicit funding decision, confirmed permissions, a small loss budget, and working reconciliation. Its purpose includes measuring real execution. It is not automatically warranted by a positive backtest.

**Scaling:** requires more than a profitable month. Review capital efficiency, capacity, actual costs, tail exposure, and operational history before increasing risk. There is no guaranteed number of weeks that proves profitability.

### 10.4 Safety and failure behavior

Prespecify account- and strategy-level risk budgets with the owner. Required controls include stale-data refusal, quote-quality checks, model/version checks, contract and settlement validation, maximum order/position limits, minimum cash reserves, exposure and loss limits, and a manual stop mechanism.

Separate “stop new entries” from “cancel open orders” and “close existing positions.” Stopping a process does not close a position. A forced liquidation can realize poor prices; emergency procedures need executable limits, escalation, and an explicit owner/operator role.

Test duplicate submissions, restart recovery, partial fills, unknown order status, disconnections, daylight-saving/session changes, insufficient buying power, and settlement-day behavior. Critical operations must be idempotent: retrying an action must not accidentally double the trade.

## 11. Risks, resources, and operating constraints

### 11.1 Risks that remain even with defined-risk spreads

- **Market and tail risk:** many positions can lose together; a bounded loss can still be too large for the account.
- **Model risk:** the estimated distribution may omit jumps, misstate probabilities, or decay as market behavior changes.
- **Liquidity risk:** protective legs and exits can become expensive or unavailable at expected prices.
- **Operational risk:** mismatched fills, wrong multipliers, stale state, and bad settlement logic can defeat the intended payoff protection.
- **Capital risk:** broker requirements or available buying power may change; credit received is not free deployable profit.
- **Research risk:** repeated tuning, look-ahead, and favorable sample selection can create a convincing fiction.

No risk limit creates a guaranteed account floor. Contractual payoff bounds assume intact matched positions and correct settlement; execution, fees, financing, and other account exposures require separate treatment.

### 11.2 Capital and data economics

Before implementation, set both a research budget and a loss budget. Include historical-data purchases, ongoing feeds, broker/exchange fees, solver licenses if any, monitoring, and operator time. An economically positive trade signal can still be uneconomic after fixed research expenses.

For illustration only: $1,200 of annual fixed data expense is `1,200 / 10,000 = 12%` of a $10,000 account, before a single trade. The same expense is 1.2% of a $100,000 account. These are arithmetic examples, not quoted vendor prices or recommended account sizes.

Separate research-career spending from investment performance, but disclose both. Do not hide a losing investment experiment by excluding necessary live operating costs.

### 11.3 Compute and software

Begin with one underlying and a narrow expiry range. CPU-based volatility regressions and compact boosted models are the first target; large neural models are optional later research. Benchmark actual data volume and solver latency rather than assuming hardware adequacy from memory size alone.

Use immutable raw snapshots, reproducible environments, tracked experiments, deterministic seeds where applicable, and versioned model artifacts. Research and execution should share tested feature/contract conventions, with credentials isolated from notebooks and logs.

This proposal does not assume an existing toolkit already implements options chains, Greeks, settlement, broker combinations, or the proposed optimizer. Audit reusable components first. Any new software architecture requires its own approval; this document authorizes neither a new package nor live execution.

### 11.4 Account and jurisdiction

Confirm country of residence, broker availability, options permissions, account type, margin policy, market-data entitlements, and applicable tax treatment before selecting contracts. Cash-settled products can have different tax and operational treatment from ETF options. Obtain qualified advice where needed; no specific tax outcome is assumed here.

## 12. Milestones and decision gates

These are acceptance milestones, not promises about elapsed time or returns.

### Milestone 1 — Instrument and data feasibility

Deliver a contract-mechanics note, sample data audit, budget estimate, and broker-capability checklist. Manually verify several contract payoffs and settlement records.

**Proceed only if:** the proposed product is accessible, data are sufficiently complete and licensed, and minimum practical sizing fits the agreed capital and loss budgets. Otherwise change the scope before substantial modeling work.

### Milestone 2 — Transparent baseline and accounting

Implement payoff tests, an event-driven position ledger, conservative fills, and the fixed-rule strategy. Reproduce the toy examples independently and reconcile hand-calculated real historical samples where licensed data permit.

**Proceed only if:** there is no unresolved material accounting or timing defect and costs are represented explicitly. A losing baseline is informative; it does not justify optimistic fill assumptions.

### Milestone 3 — Forecast and distribution evaluation

Compare persistence, HAR-style, and compact ML models with frozen temporal splits. Evaluate variance and tail calibration, using only matured labels. Document negative results and sensitivity to scenario construction.

**Proceed to ML-conditioned trading claims only if:** improvements are stable enough to justify the next experiment. If forecasting does not improve, retain the baseline and report that result.

### Milestone 4 — Incremental economic-value test

Compare fixed-rule, classical-model, ML-rule-sized, and ML-plus-MIO variants on the same candidate menu and accounting basis. Freeze the specification before opening the final lockbox.

**Proceed toward deployment only if:** prospective net-return potential is credible relative to cash and risk-matched simple strategies, remains plausible under stressed costs, and fits the owner's risk budget. Predetermine numerical hurdles after the budget and available sample are known, before inspecting lockbox outcomes.

### Milestone 5 — Shadow and paper readiness

Deliver reproducible artifacts, live/offline parity checks, broker reconciliation, operational dashboards, and failure drills. Observe several complete entry-to-settlement lifecycles, including restarts and controlled failure tests.

**Proceed only if:** the operational checklist is satisfied. This gate establishes systems readiness, not statistical proof of profitability.

### Milestone 6 — Separately approved live experiment

Use only explicitly authorized capital and risk limits. Compare realized fees, fills, latency, and slippage with research assumptions. Report the short and noisy live record honestly.

**Stop new risk or reassess if:** costs erase the modeled advantage, calibration deteriorates, data become unreliable, risk limits are breached, or broker positions cannot be reconciled. Scaling requires a new evidence review and owner decision.

## 13. Learning and career deliverables

The project should produce an inspectable body of work, not just a notebook with a high backtest Sharpe ratio:

- A concise research paper explaining the hypothesis, financial mechanism, data, baselines, costs, inference, and limitations.
- A tested derivatives-pricing/payoff component and a documented understanding of IV, Greeks, settlement, and risk-neutral versus physical distributions.
- A point-in-time dataset specification and data-quality report, with redistribution-safe sample fixtures.
- Walk-forward forecast and portfolio experiments, including ablations and unsuccessful variants.
- A scenario-based integer-allocation comparison with feasibility checks and interpretable constraints.
- A paper deployment with reproducible decisions, reconciliation, monitoring, and incident handling.

These artifacts demonstrate the connection between statistical modeling, portfolio decisions, and real market mechanics. Public quantitative-research roles explicitly include predictive models and portfolio optimization; this does not mean one project replaces broader interview preparation. [Citadel role description](https://www.citadel.com/careers/details/global-quantitative-strategies-quantitative-researcher/)

A possible résumé description, **only after the work is completed**, would be:

> Built and evaluated a point-in-time index-options research pipeline combining volatility forecasts, scenario-based risk controls, and integer portfolio selection; benchmarked net outcomes under walk-forward validation and deployed a reconciled paper-trading service.

Replace generic wording with measured, reproducible results when available. Say “paper” when it is paper, and do not describe simulated fills or hypothetical P&L as a live track record. A well-explained negative result can still demonstrate strong research judgment.

## 14. Decisions required before implementation

The following remain deliberately unresolved:

1. **Primary emphasis:** systematic alpha research, derivatives modeling/hedging, or production quant engineering. The core project supports all three, but later extensions should prioritize one.
2. **Jurisdiction, broker, and account permissions.** These determine actual product access and operational constraints.
3. **Pilot capital and maximum acceptable loss.** No funding amount or risk percentage is approved by this proposal.
4. **Historical and recurring data budget.** Verify a complete sample before purchasing a large package.
5. **Execution product:** SPX, XSP, or an explicitly redesigned alternative after the feasibility audit.
6. **Frozen initial rule details:** expiry selection, weekly decision time, admissible strikes/widths, quote filters, costs, stress set, and emergency handling.
7. **Research budget:** number of model variants, split design, final lockbox, and economic hurdles.
8. **Approval boundary:** document approval, software implementation, data purchases, paper connectivity, and live funding are separate actions.

The next useful action is a bounded feasibility audit answering these questions—not a large neural-network training run.

## 15. Glossary of modeling and evaluation terms

- **Alpha:** return unexplained by the chosen benchmark or risk model. It depends on that model; positive P&L alone is not alpha.
- **Ablation:** remove or simplify a component to measure its incremental contribution.
- **Backtest:** historical simulation of a decision rule, conditional on data and execution assumptions.
- **Basis risk:** risk that a proxy or hedge does not move exactly like the exposure being managed.
- **Calibration:** agreement between predicted probabilities or intervals and observed frequencies across appropriate samples.
- **Capacity:** the amount of capital a strategy can handle before execution or other constraints materially degrade results.
- **CVaR / expected shortfall:** modeled average loss in a chosen worst tail; not a guarantee against larger loss.
- **Distribution shift:** a change in the relationship between inputs and outcomes relative to the training period.
- **Embargo / time gap:** exclusion of observations near an evaluation boundary when required by the dependence and information structure.
- **Feature leakage:** use of information not available at the stated decision time, including through preprocessing or revised data.
- **Hyperparameter:** a model or fitting choice selected outside the fitted coefficient calculation.
- **Lockbox:** evaluation data withheld from development until the specification is frozen.
- **MIO:** optimization with some integer-valued decisions; useful for discrete contracts and logical constraints.
- **Out of sample:** observations not used to fit or select the tested rule; repeated adaptive inspection can invalidate the claim.
- **Point in time:** the exact information available at a historical decision, respecting publication and revision timestamps.
- **Purging:** removing training observations whose future label intervals overlap evaluation intervals.
- **Regime:** a descriptive market condition, such as elevated volatility; a hindsight regime label is not an available trading feature.
- **Risk-adjusted performance:** return assessed together with exposures and losses, rather than return alone.
- **Scenario:** one specified possible future market path or state; probabilities and stress severity must be distinguished.
- **Survivorship bias:** evaluating only instruments or observations that remain in a later dataset, excluding unfavorable disappearances.
- **Turnover:** the amount of trading relative to portfolio size under a stated convention.
- **Walk-forward evaluation:** repeated training on the past and evaluation on later periods while preserving the historical information boundary.

## 16. Sources and suggested reading

Sources were checked during preparation. Product terms, permissions, data fields, fees, and availability must be rechecked before implementation. Published results below describe their authors' samples and methods; none establishes profitability of the proposed strategy.

### Instruments and market mechanics

1. [Cboe — Mini-SPX/XSP contract specifications](https://www.cboe.com/tradable_products/sp_500/mini_spx_options/specifications). Verify contract size, multiplier, exercise, trading hours, and settlement.
2. [OCC — Characteristics and Risks of Standardized Options](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document). Read the current disclosure and supplements before trading; the glossary here is not a substitute.
3. [OIC — Bull Put Spread](https://prd-web.optionseducation.org/strategies/all-strategies/bull-put-spread-credit-put-spread). Payoff mechanics of one building block; note that account and assignment behavior depends on the contract used.
4. [OIC — Historical Volatility](https://www.optionseducation.org/videolibrary/historical-volatility). Introductory distinction between observed movement and implied volatility.

### Empirical motivation and counterevidence

5. [Cboe — Summary of Bondarenko's historical put-writing research](https://www.cboe.com/insights/posts/white-paper-shows-volatility-risk-premium-facilitated-higher-risk-adjusted-returns-for-put-index). Exchange-hosted historical benchmark evidence, not an independent retail implementation or this protected-spread strategy.
6. [Goyal and Saretto — Can Equity Option Returns Be Explained by a Factor Model? IPCA Says Yes](https://academic.oup.com/rfs/article/38/6/1783/8010873). Review of Financial Studies, 2025; evidence on costs and risk explanations in previously studied equity-option strategies.

### Forecasting, pricing, and control

7. [Corsi — A Simple Approximate Long-Memory Model of Realized Volatility](https://doi.org/10.1093/jjfinec/nbp001). Journal of Financial Econometrics, 2009; a classical benchmark motivation, not a trading-profit claim.
8. [Gatheral and Jacquier — Arbitrage-free SVI volatility surfaces](https://arxiv.org/abs/1204.0646). Conditions for consistent implied-volatility surface parameterization.
9. [Bühler, Gonon, Teichmann, and Wood — Deep Hedging](https://arxiv.org/abs/1802.03042). Learning hedging policies under frictions; distinguish synthetic-market demonstrations from live investment returns.
10. [Gurobi — Rebalancing with Transaction Costs](https://gurobi-finance.readthedocs.io/en/latest/modeling_notebooks/rebalancing.html). An implementation reference for discrete choices and costs, not validation of the option strategy.

### Data and professional context

11. [Cboe DataShop — Option EOD Summary](https://datashop.cboe.com/option-eod-summary). Snapshot fields, historical coverage, and licensing caveats; audit exact symbol coverage and quote an actual purchase separately.
12. [Citadel — Global Quantitative Strategies researcher role](https://www.citadel.com/careers/details/global-quantitative-strategies-quantitative-researcher/). An example of industry work connecting predictive modeling and portfolio optimization, not a guarantee of employment.

### Additional introductory pricing reference

13. [OIC — Black-Scholes Formula](https://prd-web.optionseducation.org/advancedconcepts/black-scholes-formula). The role and limitations of a theoretical option-pricing model; a useful companion to Section 6.4.

**Bottom line:** pursue a bounded, falsifiable derivatives research project. Learn the instrument, establish a trustworthy baseline, test whether modeling adds economic value, and earn each deployment step with separate evidence. The proposal's value is its disciplined research path, not a promise that options trading will be profitable.
