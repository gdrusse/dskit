# Question

What does dskit already provide for the first correlation_arb study? What do
the data actually look like? How should the relationship between Kalshi
contracts and correlated instruments be modeled?

The first study prices Kalshi S&P 500 / Nasdaq-100 contracts from index
options. Coherence scans and cross-market dependence come after it.

# Finding

## 1. What dskit already has (main `80a194d`, plus branch `claude/crypto-trading-child`)

Most of the machinery exists. About half of what this study needs is on main.
The Kalshi-binary half is on the crypto branch and has not been merged.

| Need | Where it lives | Status |
|---|---|---|
| Kalshi markets, 1-minute candles, fee schedules, live books | `dskit/onboarding/libs/kalshi.py` (`KalshiConnector`) | main |
| Kalshi `/historical` archive, trades, `expiration_value`, `settlement_ts` | `onboarding/libs/kalshi_history.py` (ADR-0236) | **branch only** |
| SPX/SPXW/NDX/NDXP/SPY/QQQ chain with bid/ask, 15-min delayed, current state | `onboarding/libs/cboe.py` (`CboeConnector`, `option_chain`) | main |
| Recording a chain forward on a schedule | `python -m dskit.onboarding watch` | main |
| SPY/QQQ end-of-day chains 2008–2025 | `onboarding/libs/optionshist.py` | main |
| SPY/QQQ minute NBBO (underlying) | `onboarding/libs/alpaca_quotes.py` | main |
| Breeden–Litzenberger CDF: parity forward, isotonic call slopes, quantiles, rejection reasons | `pipeline/libs/predictive_cdf.py:6861` `OptionPriceCDF`; `OptionCDFPanel` :7113 | main |
| Black-76 | `pipeline/option_pricing.py:75` | main |
| Above/Below/Between payoffs and the `BinaryFairValue` node | `pipeline/binary_pricing.py` | **branch only** |
| Quadratic venue fee | `pipeline/fee_mechanics.py` `ProbabilityQuadraticFee` | **branch only** |
| Kill test: model vs market Brier/log by bucket, cluster SEs, naive take rule | `pipeline/binary_scoring.py:140` `BucketedBinaryScore` | **branch only** |
| Proper scores and calibration (CRPS, twCRPS, threshold Brier, PIT, Berkowitz) | `pipeline/distribution_scores.py`; `DecisionRegionScores` | main |
| Q→P transport of option-implied quantiles | `predictive_cdf.py:1305` `OptionImpliedTransportCDF` | main |
| GPD tails | `SemiparametricGPDTailCDF` :1705 | main |
| Walk-forward folds with embargo; holdout | `pipeline/driver.py:3277`; `kinds_split.py` | main |
| Joint scenarios by block resampling | `outcome_interval.py` `BlockResiduals`/`ScenarioSet` | main |
| Fractional-Kelly MILP over scenarios | `libs/pyomo.py:1674` `ScenarioUtilitySolve` | main |
| Maker/taker fill simulation (queue fraction, p_fill_on_touch, latency) | `production/executor.py:1254` `PaperExecutor` | main |
| Event-log P&L, serve loop, monitors (calibration, brier, psi) | `dskit/evaluation`, `dskit/production` | main |

**Child-side code that should graduate first.** These are generic, and both
this child and others would reuse them:
- pmquant `books.py`: mirrored asks, `net_edge`, crossed-book detection, `walk_book`.
- pmquant `FeeBook`: the dated fee schedule.
- pmquant `ladder/protocols.py`: `LadderType`/`SettlementLaw`.
- crypto_trading `MarketRows`/`CandleRows`/`DecisionRows`/`MarketState`.
- index_options `quote_problems`/`_usable_quotes`. Its `ChainQuoteRows` refuses 0DTE (`dte_min >= 1`).

**Genuinely missing.** By repo rule, each item below is generic work in dskit
and needs an ADR before code:
1. A digital/range pricer that reads a CDF curve rather than a lognormal.
2. **Executable vertical-spread bounds** on a digital, built from bid/ask.
   `OptionPriceCDF` prices on mid only.
3. An IV-smile fit for one expiry (arbitrage-free SVI or a constrained spline), as an alternative curve source.
4. A **coherence/no-arbitrage LP** over fee-adjusted bid/ask intervals of a ladder or of nested series.
5. **Dependence models.** dskit has no copula, DCC, or covariance estimator; only PCA and block resampling.
6. A sub-day as-of join. `observation-tables` works at day granularity only.
7. A maker quoting policy plus mark-out reporting.

**Data gap.** No connector gives historical intraday index-option quotes.
What exists:
- The owner's own Cboe recording: SPX, NDX, SPY, QQQ at 10:30, 15:50 and 16:20 ET, since 2026-09-23.
- optionshist: SPY/QQQ end-of-day only.

## 2. What the data look like (Kalshi API, read 2026-10-07)

**Series metadata (`GET /series/KXINX`).**
- Daily, quadratic fee, multiplier 1.
- Settlement source is given only as an example: Google Finance `.INX`.
- Market rules say the "end-of-day S&P 500 index value". Kalshi says it has modified the Source Agency and Underlying for index markets, so the settlement print is **not contractually the Cboe SPX/SPXW settlement**. It has to be checked against the official close.

**KXINX (range ladder) is structured as follows:**
- 30 markets per event: 28 buckets of 25 points, plus two tails.
- Bucket edges look like `7800`–`7824.9999`, i.e. inclusive with 0.0001 precision.
- Close is 20:00 UTC (4:00 pm ET).
- `expiration_value` is published on every market. Oct 6, 2026 settled at 7818.93.

**KXINX liquidity is thin.**
- On Oct 6, five buckets near spot each traded about 3,800–25,500 contracts.
- Every other bucket traded at or near zero.
- Oct 5's ladder was almost untraded.

**KXINXU (above/below) is far deeper.**
- One Sep 18 4 pm event had at least 41 strikes at 5-point spacing.
- About 404k contracts traded across them.
- It also lists intraday hourly events.

**Archive.** Markets closing before about 2026-08-07 sit under
`/historical/markets`, which still returns `expiration_value`. Kalshi has no
historical order book; the 1-minute `yes_bid`/`yes_ask` candles are the
historical top-of-book proxy.

**Hedge leg.**
- PM-settled SPXW options settle on component closing prices.
- NDXP options settle to the Nasdaq official close (XQC).
- Alpaca added SPX/SPXW/XSP index options on 2026-09-02, but not NDX.
- Historical minute NBBO needs a vendor:
  - Databento OPRA.PILLAR has SPX/SPXW/NDXP, with cbbo-1m from 2013. Standard costs $199/mo for 12 months of history.
  - ThetaData's options tiers.
  - Cboe DataShop's 15:45 snapshot.

## 3. How to model the relationship

The word "correlation" covers three different problems. Each needs a different
model.

**A. Same underlying: Kalshi S&P vs SPXW, Kalshi Nasdaq vs NDXP.** This is
**structural**, so no correlation needs estimating. A Kalshi "above K" is a
digital, and a range bucket is the difference of two digitals.

1. **Executable no-arbitrage band.** Convexity of the call price in strike
   brackets the digital between two vertical spreads:
   `[C_bid(K) − C_ask(K+h)]/h ≤ D(K) ≤ [C_ask(K−h) − C_bid(K)]/h`.
   Take the tighter of the call and put bands.
   - A Kalshi price outside this band, after both venues' fees, is a true hedgeable gap.
   - Kalshi's 5-point and 25-point edges fall on SPX strikes, which are 5 points apart near spot. This keeps the band reasonably tight near the money.
2. **Point estimate inside the band.**
   - Fit the forward from put-call parity.
   - Smooth one 0DTE slice in IV space: arbitrage-free SVI, or a Fengler-constrained spline. Add GEV/GPD tails beyond the last quoted strike.
   - Read the CDF at each Kalshi edge.
   - Use the Bandi–Fusari–Renò 0DTE Edgeworth expansion as a parametric cross-check.
   - Do not difference raw quotes twice.
3. **Q→P mapping.** Options carry a large intraday variance premium, so option
   Q tails are fatter than physical P. Kalshi takers additionally overpay
   longshots. Kalshi prices are therefore neither Q nor P.
   - Recalibrate Q-bucket probabilities to realized outcomes, conditioned on minutes-to-close and moneyness.
   - Use isotonic or beta calibration, or reuse `OptionImpliedTransportCDF`.
   - Quote around P, inside the executable band.
4. **Basis risk to model explicitly.** It comes from three sources:
   - the Kalshi settlement print vs the SPXW/NDXP settlement value;
   - option chains that are 15 minutes delayed in the Cboe recording;
   - Kalshi ladders that are thin away from spot.

**B. Different but correlated underlyings** (S&P vs Nasdaq on the same day,
NYC vs Philadelphia highs, BTC vs ETH).
- Fit marginals separately. Join them with a copula.
- **Finance.** Use a Student-t copula. Take its correlation from a DCC or
  realized correlation on 1–5 minute ES/NQ (or SPY/QQQ) returns, where
  thousands of observations exist. Shrink the degrees of freedom toward a
  pooled prior.
- **Weather.** Daily outcomes are too few to estimate dependence directly.
  Use NWP ensembles (GEFS, NBM), which carry physically consistent cross-city
  dependence, then:
  - post-process the marginals (EMOS);
  - restore dependence with Ensemble Copula Coupling or the Schaake shuffle.
- **The tradable signal is weaker than in A.**
  - With only marginal markets, the sole model-free constraint is the Fréchet–Hoeffding bounds. They rarely bind.
  - The usable edge is **conditional repricing**: one market has moved on news the correlated market has not absorbed yet.
  - This is a statistical bet, never riskless.

**C. Coherence within Kalshi** (bucket vs above-ladder on the same event;
nested hourly/daily/yearly series).
- KXINX bucket [L, L+25) must equal KXINXU(L) − KXINXU(L+25).
- **Detection:** an LP feasibility check on fee-adjusted bid/ask intervals.
  Infeasible means riskless arbitrage.
- **De-noising fair value:** a weighted isotonic or KL (Bregman) projection of
  the mids, with weights 1/spread².
- Needs only Kalshi data, which we can already pull.

**D. Evaluation.**
- **Forecast quality:** log score, Brier, and ranked probability score per ladder; CORP reliability diagrams; PIT. Add the variogram score for joint models, since the energy score is weak at detecting dependence errors.
- **Benchmark against both the Kalshi mid and option Q.** No log-score gain over both means no informational edge.
- **Economic value:** queue-aware maker fills, mark-outs at 1 s/10 s/60 s/5 min and at settlement, and net of the Kalshi maker formula `ceil(0.0175·M·C·P(1−P))`.

## 4. Unverified

- That `expiration_value` equals the official SPX/NDX close. Oct 6 = 7818.93 is unconfirmed; this is gate G0.1 of the plan.
- The earliest KX index event, and whether Alpaca keeps SPX option history.
- The KXINXU 4 pm depth on typical days. Only one event was sampled.

# Sources

- https://api.elections.kalshi.com/trade-api/v2/series/KXINX — series metadata, fee type, settlement source example
- https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXINX&status=settled — settled KXINX markets, `expiration_value`
- https://kalshi-public-docs.s3.amazonaws.com/contract_terms/INX.pdf — INX contract terms
- https://docs.kalshi.com/getting_started/historical_data — historical cutoff and endpoints
- https://kalshi.com/docs/kalshi-fee-schedule.pdf — fee formula
- https://www.cboe.com/tradable_products/sp_500/spx_options/specifications/ — SPXW settlement
- https://www.nasdaq.com/NDXP-factsheet — NDXP settlement
- https://docs.alpaca.markets/us/docs/index-options — Alpaca index options
- https://databento.com/catalog/opra/OPRA.PILLAR — OPRA history coverage
- https://arxiv.org/abs/1204.0646 — Gatheral & Jacquier, arbitrage-free SVI
- https://ideas.repec.org/a/taf/quantf/v9y2009i4p417-428.html — Fengler, arbitrage-free smoothing
- https://archive.nyu.edu/handle/2451/27846 — Figlewski, GEV tails
- https://doi.org/10.2139/ssrn.4503344 — Bandi, Fusari, Renò, 0DTE option pricing
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4701401 — 0DTE asset pricing, intraday variance premium
- https://arxiv.org/abs/2606.19517 — prediction markets vs option-implied probabilities
- https://www.karlwhelan.com/Papers/Kalshi.pdf — Bürgi, Deng, Whelan, Kalshi makers and takers
- https://arxiv.org/abs/1302.7149 — Ensemble Copula Coupling
- https://arxiv.org/abs/1011.1941 — Abernethy, Chen, Wortman Vaughan, Bregman projection
- https://arxiv.org/abs/1606.02825 — Kroer et al., arbitrage-free combinatorial market making
- https://repository.library.noaa.gov/view/noaa/22327 — Scheuerer & Hamill, variogram score
- https://arxiv.org/abs/2008.03033 — CORP reliability diagrams
