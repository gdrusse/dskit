## Question

Which reliable, scalable features should be gathered for modeling the full
conditional return distribution of SPY, QQQ, and IWM across the seven ADR-0187
expiry buckets, and which should be tested first?

## Finding

### Observed data now available

- The existing OptionsHistory chain archive is the highest-value raw source:
  53,407,120 contract-day rows with bid/ask and sizes, implied volatility,
  open interest, volume, delta, gamma, vega, theta, and last price. Coverage is
  SPY/IWM from 2008 and QQQ from 2011 through 2025. No new download is needed.
- The refreshed Cboe wide-index stream contains 134,922 daily observations for
  27 indices with no null closes. Relevant coverage includes VIX (1990), VVIX
  (2006), VIX1Y (2007), VIX6M (2008), VIX3M/VXN/RVX (2009), VIX9D (2011),
  SKEW (1990), and cross-asset volatility indices for rates, oil, gold, silver,
  and emerging markets.
- Thirty-one official-series CSVs were acquired into the WORM observation
  store and independently read through `scan_stream`; every stream reproduced
  its source row count. They cover nominal Treasury yields (1m-30y), real
  yields, breakevens, Fed funds, SOFR, commercial-paper funding, reverse repo,
  Fed assets, dollar, oil, economic-policy uncertainty, NFCI/ANFCI/STLFSI,
  and five credit-spread series.
- Most rate and cross-market series cover the entire study window. Important
  exceptions: SOFR begins in 2018; VIX1D begins in 2022; and the five ICE BofA
  credit series now contain only 795 rows from 2023-09-26 onward.
- FRED source CSVs encode missing observations as `.`; the local-files ingest
  preserves them as empty strings. Feature construction must explicitly parse
  these as missing and refuse silent forward-filling across an unknown value.

### Research evidence

- Option-implied volatility is a strong forecast input: Christensen and
  Prabhala find it outperforms past realized volatility and can subsume its
  information in some specifications. This supports using the option surface,
  not only a generic VIX level.
- Cboe defines VIX, VXN, and RVX as option-implied near-term volatility for
  the S&P 500, Nasdaq-100, and Russell 2000 families. Therefore VXN is the
  closest listed-volatility proxy for QQQ and RVX for IWM; SPY keeps VIX.
- Cboe's 9-day, 30-day, 3-month, 6-month, and 1-year indices expose the implied
  volatility term structure. VVIX measures volatility-of-volatility, while
  SKEW reflects option-market pricing of tail risk. These are direct candidates
  for horizon and tail-shape features.
- Patton and Sheppard show that negative realized semivariance and signed jumps
  improve out-of-sample equity-volatility forecasts from one day to three
  months. The daily ETF histories support lower-frequency signed-return,
  downside/upside semivariance, leverage, jump-proxy, drawdown, momentum, and
  volatility-of-volatility features now; true intraday realized measures would
  require a separate intraday source.
- Cboe notes that implied SPX volatility tends to exceed subsequent realized
  volatility. The implied-minus-realized gap and its slope across expiries are
  therefore economically grounded state variables, not arbitrary transforms.
- Rates, funding, credit, dollar, oil, and financial-condition data plausibly
  describe discount-rate and stress regimes. They are broad conditioning
  variables, but less directly connected to an ETF option target than the
  target's own surface.

### Ranked feature program (inference)

1. **Derive the ETF option surface from the existing chain archive.** For each
   symbol and observation date: delta- or moneyness-aligned ATM IV by expiry,
   forward variance, term slopes/curvature, put-call skew or risk reversal,
   butterfly/wing richness, surface dispersion, implied-minus-realized spread,
   volume, open interest, bid-ask width, quote depth, and Greek-weighted
   aggregates. Align strictly to information available before the forecast
   cutoff and compute robust medians/quantiles to resist bad quotes.
2. **Add underlying-specific and shape-aware Cboe features.** Use VIX for SPY,
   VXN for QQQ, RVX for IWM; then VIX term slopes, VVIX, SKEW, and cross-asset
   volatility spreads. This is likely the cleanest incremental test over the
   current HAR+VIX models.
3. **Expand realized-state features.** Add signed semivariances at 1/5/22/66
   sessions, negative-return leverage interactions, jump proxies, vol-of-vol,
   drawdown, momentum, and rolling quantiles. Keep these low-dimensional or
   regularized because the effective sample is small.
4. **Test rates and funding as regime features.** Prefer curve factors over raw
   columns: level, 2s10s and 3m10y slopes, curvature, real-rate level/slope,
   5y/10y breakevens, policy-to-market spreads, commercial-paper spreads,
   reverse-repo change, and Fed-balance-sheet change.
5. **Test cross-market stress last.** Dollar/oil returns, GVZ/OVX/VXTLT/VXSLV/
   VXEEM relative to VIX, and lagged NFCI/ANFCI/STLFSI/EPU can be useful regime
   descriptors. Admit them only after a point-in-time release calendar is
   encoded and only when they improve repeated out-of-sample scoring.

The selection rule should be “collect broadly, select narrowly”: each family
must beat the existing HAR+underlying-IV baseline in nested walk-forward tests,
with feature-family ablations and stability across folds/cells. Do not promote
a feature merely because its in-sample importance is high.

### Exclusions and uncertainty

- Exclude the downloaded ICE BofA credit series from the primary 2008-2025
  ranking: FRED now exposes only three years, and the data have redistribution
  restrictions. They can support a recent-period diagnostic only unless a
  licensed historical source is obtained.
- The current FRED extracts are latest-vintage snapshots. Market rates are
  relatively straightforward, but revised weekly/monthly composites can leak
  future revisions. Formal out-of-sample use requires ALFRED/release-vintage
  acquisition and an as-known timestamp; observation date alone is insufficient.
- Option-surface features are the strongest inference, not yet an empirical
  result for these 21 cells. Their value must be established by the same locked,
  signed-region scoring used in ADR-0187. No result here establishes tradability.

## Sources

- Cboe VIX overview and volatility risk premium: https://www.cboe.com/tradable-products/vix
- Cboe VIX term structure: https://www.cboe.com/tradable_products/vix/term_structure
- Cboe VVIX: https://www.cboe.com/us/indices/dashboard/vvix/
- Cboe Russell 2000 options / RVX: https://www.cboe.com/tradable_products/ftse_russell/russell_2000_index_options
- Cboe SKEW discussion: https://www.cboe.com/insights/posts/inside-volatility-trading-the-adventures-of-volatility-markets
- Cboe selected equity/index volatility methodology (includes VXN): https://cdn.cboe.com/api/global/us_indices/governance/Volatility_Index_Methodology_Selected_Broad_Based_Index_Equity_and_ETF_Volatility_Indices.pdf
- Christensen and Prabhala (1998), implied versus realized volatility: https://doi.org/10.1016/S0304-405X(98)00034-8
- Patton and Sheppard (2015), signed semivariance and jumps: https://public.econ.duke.edu/~ap172/Patton_Sheppard_REStat_2015.pdf
- FRED API and vintage-date documentation: https://fred.stlouisfed.org/docs/api/fred/ and https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html
- New York Fed reference rates and SOFR: https://www.newyorkfed.org/markets/reference-rates
- U.S. Treasury daily yield-curve feed: https://home.treasury.gov/treasury-daily-interest-rate-xml-feed
- ICE BofA high-yield OAS coverage and restrictions: https://fred.stlouisfed.org/series/BAMLH0A0HYM2
