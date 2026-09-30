# Strategy alternatives memo: what to model after the condor

## Question

Beyond the iron condor, which index-option strategies have a credible
positive expected return that this child can model with the data and
models it already holds? This is the synthesis. The full evidence is in
`docs/research/strategy-alternatives/2026-09-30-survey.md`.

## Finding

### What the evidence says

1. **Selling options on the S&P no longer earns much on its own.**
   - Since about 2010, index-option alphas are indistinguishable from zero (Dew-Becker & Giglio 2025).
   - Cboe's own iron-condor benchmark, CNDR, returned 0.9%/yr from 2007 to Sep 2026 (t 0.70). That is below T-bills (recomputed here from Cboe's file).
   - The cash-secured put-write benchmark, PUT, now earns about its equity beta.
   - Our condor's +$79/trade (t 1.32) is exactly what this predicts. Another fixed *shape* will not help. Any lift has to come from which side we sell, when we sell, how much we sell, and relative value across strikes, tenors and indices.
2. **The premium sits on the put side.**
   - OTM puts are the most overpriced options; OTM calls are roughly fair, or overpriced only far OTM.
   - The condor's call spread probably adds negative-beta drag and little premium.
   - The wings the condor buys are the most overpriced options on the surface.
3. **Timing has the best peer-reviewed support.**
   - Stand aside when the VIX term structure inverts, i.e. VIX/VIX3M ≥ 1 or the slope is in its bottom quintile (Johnson 2017; Simon & Campasano 2014).
   - Sell only when the volatility premium is positive (Cheng 2019: real-time Sharpe 0.87 vs 0.57).
   - HAR ≈ VIX in our tests, so the VIX *level* adds nothing. The usable signal is the *sign* of VIX² minus recent realized variance, plus the curve's slope.
   - VVIX has a weak prior. SKEW and 200-day moving-average filters serve only as placebos.
4. **Our CDF can pick payoffs, not just strikes.**
   - For any listed-strike payoff g: E_P[g] − E_Q[g] = ∫ g′·(F_Q − F_P).
   - A vertical spread is a bet on the CDF, which our model forecasts best. A butterfly is a bet on the density, which our model forecasts worst.
   - Faias & Santa-Clara 2017 optimize held-to-expiry SPX option portfolios under a bootstrapped P at bid/ask. They report an out-of-sample Sharpe of 0.82 (1996–2013) with positive skew. This is the closest published template, though its sample predates the post-2010 decline.
   - Theory says the optimal shape is condor-like already (q/p is high in both tails). The gains should come from width, asymmetry and size.
5. **"Positive upside" through long convexity mostly loses standalone.**
   - Protective puts, tail programs, VIX calls, and unconditional or pre-event straddles all lose money on average.
   - The exceptions:
     - long short-dated straddles when the VIX curve is inverted, with evidence only at a one-day horizon;
     - long ATM/ITM call spreads, which carry equity beta and are untested;
     - trend-following, which does not use options.
   - Any hedge sleeve must beat simply running a smaller condor at the same CVaR5 (Israelov 2019).
6. **Short-dated options are not a shortcut.**
   - 0DTE short vol has no positive net Sharpe once costs are charged correctly (Vilkov's Aug 2026 correction; Almeida et al.).
   - The weekly put-write benchmark (WPUT) had a lower Sharpe than the monthly one (PUT), before costs.
   - 1DTE close-to-settlement captures the overnight premium, but daily ETF expiries only date from 2022.

### Recommended order of work (each step pre-registered, walk-forward)

| Step | What | Why first | Data |
|---|---|---|---|
| 1 | Split existing condor P&L into put spread vs call spread, then test a put credit spread (16–20Δ / 5Δ, 30–45 DTE, held to settlement) against delta-matched SPY | No new infrastructure; tests finding 2 | Existing runs + ETF EOD chains |
| 2 | Entry gate: VIX/VIX3M ≥ 1, slope bottom quintile (expanding), VIX² − RV²₂₁ ≤ 0 | Strongest peer-reviewed timing evidence | Cboe daily term structure (VIX3M from ~2007/08) |
| 3 | CDF-scored payoff choice: ≤4 legs from a small grid (short Δ {10,16,20,25,30} × width {1,2,3} strikes × tenors); robust edge = 10% quantile across ensemble draws; CRRA γ 5–10 or edge/CVaR | Uses the predictive CDF directly | Needs F_Q from the chain (SVI) |
| 4 | Sizing (1/VIX², 1/RV², constant, ∝VIX) and cross-index/tenor allocation by standardized net edge | Answers how much, and where | Existing |
| 5 | Sleeves: long straddle when curve inverted; ATM/ITM call spread; budgeted put spread, each vs the down-sizing test | Positive-upside candidates | EOD chains + marks |

Not pursued, with the reason for each:

- 0DTE intraday: not testable with end-of-day data, and the net evidence is negative.
- Naked strangles: margin calls force exits.
- Put ratios and backspreads: a naked tail, or negative expected value.
- Iron fly: negative since 2007.
- VIX calls: no chains in our data.
- Deep-OTM tail programs: the worst expected value on the surface.

### Protocol that keeps the answer honest

**Signals and fills**
- Lag every Cboe vol-index signal by one day; they close at 16:15 while the index closes at 16:00.
- Fill at bid/ask as the base case.
- Model SPY/QQQ/IWM early assignment. SPX monthlies are AM-settled; SPXW and XSP are PM-settled.
- Carry fees as config.

**Statistics**
- Non-overlapping or staggered weekly tranches, with HAC standard errors (lag ≥ holding period) and a block bootstrap.
- Deflate for every rule tried.
- Report pre/post 2010 and pre/post May 2022, with and without the top three crash episodes.

**Attribution**
- Split P&L into *premium* (Q against the empirical P) and *forecast* (ML P against the empirical P). Only the forecast part is evidence that the models add value.

**Lockbox**
- The 2019–2025 ETF years have already been viewed by the CDF studies.
- The only never-seen data is the recorder's SPX/XSP chains from Sep 2026 onward. Reserve them for confirmation.

### Capability that would graduate to dskit (each needs an ADR, then approval, before code)

- **(a)** A risk-neutral CDF fitted from a quote chain: smoothing in implied-vol space or SVI, kept within bid/ask, with fitted tails.
  - It is generic library plumbing, so it belongs in a tier-2 pack, next to `dskit/pipeline/option_pricing.py`.
- **(b)** A scorer for any European payoff: expected value under a predictive CDF, minus its executable bid/ask cost.
- **(c)** In the child: a multi-leg defined-risk backtest built as a subclass of the existing `_CondorBacktestBase` hooks, rather than a new loop beside it.

## Sources

- Full survey with every figure and link: `docs/research/strategy-alternatives/2026-09-30-survey.md`.
- Dew-Becker & Giglio 2025: https://www.chicagofed.org/-/media/publications/working-papers/2025/wp2025-17.pdf
- Cboe CNDR history: https://cdn.cboe.com/api/global/us_indices/daily_prices/CNDR_History.csv
- Johnson 2017: https://www.travislakejohnson.com/pdfs/Johnson%20VIXTS%202017%20(JFQA).pdf
- Cheng 2019: https://utoronto.scholaris.ca/bitstreams/682f3c99-5d7d-4dee-b4b4-dafa83785523/download
- Faias & Santa-Clara 2017: https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/optimal-option-portfolio-strategies-deepening-the-puzzle-of-index-option-mispricing/4DEEA14BDEA84871016233C2EC12C86B
- Bondarenko 2014: https://www3.gmu.edu/schools/vse/seor/studentprojects/graduate/2009Fall/ISG/Investment_Optimization/Resources_files/Bondarenko-Puts.pdf
- Israelov 2019: https://images.aqr.com/-/media/AQR/Documents/Journal-Articles/Pathetic-Protection-JAI-Wint19.pdf
- Vilkov 0DTE KNOWN-ISSUES: https://github.com/vilkovgr/0dte-strategies/blob/main/KNOWN-ISSUES.md
- Bakshi, Madan & Panayotov 2010: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1462543
