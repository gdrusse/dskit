# Question

Which arbitrage or relative-value methods exploit correlation between assets
(stocks, ETFs, options, event contracts), and does any survive realistic US
retail costs in October 2026? Scope: equities/ETFs, options, and "indirect"
arbitrage of event contracts against correlated liquid instruments. Crypto
cross-venue gaps are already covered in
`children/crypto_trading/docs/research/data-and-arbitrage/2026-10-06-sources-and-gaps.md`
(branch `claude/crypto-trading-child`) and are not repeated.

# Finding

**Correlation is not arbitrage.** A riskless arbitrage needs a contractual
link: a conversion right, a creation/redemption mechanism, a common
settlement value, or a static replication. Correlation alone gives a
statistical convergence trade that can diverge without limit (Shleifer–Vishny
1997). Every method below falls into one of three bins.

**Bin 1: true no-arbitrage links. Real, but closed or tiny at retail latency.**

| Link | Why it binds | Retail reality |
|---|---|---|
| ETF vs basket (NAV) | Creation/redemption | Authorized participants only. Secondary-market versions are statistical; displayed premia are often stale NAV (Petajisto 2017). |
| ES futures vs SPY | Cost of carry | Median opportunity life fell from 97 ms (2005) to 7 ms (2011) (Budish–Cramton–Shim 2015). Gone at 1-minute bars. |
| SPX box / put-call parity | European, cash-settled | Box rate ≈ T-bill + 20–40 bp (van Binsbergen–Diamond–Grotteria 2021; NY Fed 2023). Cash management, not alpha. American-style boxes can blow up (Robinhood, Jan 2019). |
| Convertible share classes (BRK.A → 1,500 BRK.B) | Conversion right | One-sided bound only. GOOG/GOOGL do not convert: voting-premium trade. Unverified here. |
| Same-venue ladder sums (asks sum < $1) | Mutually exclusive outcomes | About $39.6M of the $40M Polymarket arb (Apr 2024–Apr 2025) was this kind (Saguillo et al. 2025). Kalshi ladders: violations at the 1¢ tick, ≤0.2 contracts deep (crypto_trading snapshot). |

**Bin 2: statistical convergence on correlated prices. Real premia, steady decay.**

- **Pairs trading.**
  - Distance pairs earned up to about 12%/yr for 1962–97, before costs (Gatev–Goetzmann–Rouwenhorst 2006).
  - With realistic costs and 1%/yr borrow, the baseline loses money after 1989. Refinements net about 0.12%/month, mostly from 2000–02 (Do–Faff 2012).
  - Distance, cointegration and copula methods net Sharpe 0.33, 0.34 and 0.08, assuming free shorting. Rolling Sharpe has been unattractive since about 2000 (Rad–Low–Faff 2016).
- **Factor-residual stat arb.**
  - PCA or sector-ETF residuals traded as mean-reverting (OU) processes: Sharpe 1.44 for 1997–2007, but 0.9 for 2003–07 (Avellaneda–Lee 2010).
  - Deep-learning residual stat arb on about 550 large caps: gross Sharpe about 4.2, falling to about 1.0–1.2 after costs. The cost model is only 5 bp per trade plus 1 bp per short, with no market impact (Guijarro-Ordonez–Pelger–Zanotti 2022).
  - **This is the strongest equity evidence.** It is turnover-driven, so real retail spreads cut it hard.
- **Lead-lag.** Large firms lead small firms within an industry (Hou 2007). That effect is daily-or-slower and lives in expensive small names. ES leads SPY, which leads sector ETFs, but only at millisecond horizons (Hasbrouck 2003).
- **Dual-listed twins.**
  - Rules earned up to about 10%/yr net, with uncertain timing and frequent large losses (De Jong–Rosenthal–van Dijk 2009).
  - ADR parity deviations average 4.9 bp (Gagnon–Karolyi 2010).
  - Alpaca has no foreign home-market leg.
- **Leveraged-ETF pairs** (short both legs, or TQQQ vs 3x QQQ). This is **short volatility, not arbitrage**. Gross Sharpe was 1.51 vs 1.04 for the index (2010–16), but that excludes borrow costs. Every leveraged ETF studied was hard to borrow, and fees cut profits sharply (Peterburgsky 2018; Tsalikis–Papadopoulos).

**Bin 3: options correlation trades. A premium, not a mispricing.**

- **Dispersion** (short index vol, long constituent vol).
  - It earns the correlation risk premium: S&P 500 implied correlation exceeds realized by about 5–9 points (Faria–Kosowski–Wang 2021).
  - The edge was large before 2000 and gone afterwards, once option spreads tightened (Deng, cited via Quantpedia; unverified at source).
  - Net Sharpe since 2000 is about 0.3–0.4 (Schneider–Stübinger 2020).
  - Driessen–Maenhout–Vilkov (2009) already concluded frictions block it. A full book needs 100–200 legs.
- **Cheaper version: sector-ETF dispersion.** Use nine sector ETFs plus SPX. Implied correlation from sectors forecasts about as well as from all constituents (Buss–Schönleber–Vilkov 2018). It is plausible but has no net-of-cost backtest.
- **Benchmarks.** Cboe COR1M/COR3M and the DSPX dispersion index can serve as implied-correlation benchmarks.
- **Relative value with no net-of-cost evidence found:** LETF vs underlying implied vol (Leung–Sircar 2015), VIX vs SPX, IBIT vs Deribit/CME.
- **Surface no-arbitrage violations** (calendar, butterfly) are frequent in quotes but not shown to be exploitable. At retail latency, treat them as stale quotes.

**Bin 4: event contracts vs correlated instruments ("indirect arbitrage").
Closest to our edge.**

| Pair | Hedge quality | Evidence |
|---|---|---|
| Kalshi INX/NASDAQ100 daily ranges vs SPXW/NDXP 0DTE options | **Near-static.** A bucket is a call butterfly; an above/below contract is a tight call spread (Breeden–Litzenberger). Residual risks: strike-width error, settlement-print basis, 2–4 option legs crossing spread, size mismatch (1 SPX option ≈ $100× index). | Only a 2025 student project (Stevens). It found good signal in 2022, decaying in 2024. **Daily series unstudied.** |
| Kalshi Fed decision vs ZQ/SOFR futures | Model risk: FedWatch assumes two outcomes, and EFFR vs target-range basis remains. | Kalshi had zero median/mode error the day before FOMC and beat futures; it priced the 50 bp Sept 2024 cut better (Diercks–Katz–Wright, FEDS 2026-010). |
| Kalshi CPI/jobs vs swaps/nowcast | No tradable hedge. | Kalshi mean slightly beat consensus on headline CPI. |
| Kalshi weather vs CME HDD/CDD | Different variable, window and aggregation; thin market. | The Kalshi implied high beats the National Blend of Models in 6 of 7 cities, and the NBM moves toward the market (Crosier 2026, abstract only). No cross-city coherence study found. |
| Same event across venues | Partly riskless; resolution wording differs. | Execution-aware deviations average about 2–4%; about 6% of events are cross-listed (Gebele–Matthes 2026). |

Fees decide the event bin. The Kalshi taker fee is 0.07·p(1−p), about 1.75¢
at 50¢. Makers pay 0 by default, plus the Liquidity Incentive Program until
2027-01-01. The favorite–longshot bias means tail gaps against
options-implied odds are partly a known taker overpayment, not a
cross-asset mispricing (Bürgi–Deng–Whelan 2025).

**Implications for this child, ranked:**

1. **Kalshi S&P/Nasdaq daily buckets vs SPXW/NDXP-implied digitals, quoted as
   maker, hedged with option verticals when the gap clears both legs' costs.**
   - This is the only pair with a near-static hedge.
   - We hold both datasets: the `kalshi` connector, and index_options SPX/SPY chains.
   - The question is unstudied in the literature.
   - It reuses index_options' risk-neutral CDF work and pmquant's ladder mechanics.
2. **Cross-market coherence scans on Kalshi**, for Dutch-book and monotonicity
   violations:
   - within a ladder;
   - across nested series (daily vs weekly vs yearly index; hourly vs daily crypto);
   - across correlated weather cities.
   Riskless only within a ladder or a nested series; cross-city is a correlation bet.
3. **Sector-ETF dispersion monitor.** Track the gap between COR3M/DSPX and
   realized correlation. Gate a 10-leg version on a net-of-cost backtest.
   Low priority: it is a premium, and our index_options work already harvests a related one.
4. **Factor-residual stat arb on liquid large caps at daily frequency.**
   - Only if the owner wants an equity sleeve.
   - Backtest at SIP spreads with at least 10 bp round trip and real hard-to-borrow fees.
   - The free IEX feed (about 2.5% of volume) is not enough.

**Avoid:** classic distance/cointegration pairs (decayed), intraday lead-lag
(milliseconds), ETF/NAV and futures/ETF basis (AP and HFT territory),
leveraged-ETF pair shorts as "arbitrage", and full single-stock dispersion.

**First falsification tests:**
1. For each settled Kalshi INX/NASDAQ100 daily market, compute the SPXW/NDXP
   digital price from the same minute's chain. Compare with Kalshi mid and
   with the executable side net of fees and two-leg option spread. Report the
   gap distribution by moneyness and minutes-to-close.
   **Kill if** the median executable gap is under 1¢ after costs.
2. Confirm the current Kalshi INX settlement source against the official SPX
   close, and measure the basis on edge days.
3. Run a 30-day scan of nested-series and cross-city monotonicity violations on
   Kalshi, measuring depth at the violating price.

**Caveats:**
- Deng (dispersion decay), Crosier (weather) and the Avellaneda–Lee cost assumptions were read from summaries or abstracts only.
- The deep-learning stat-arb Sharpe figures (gross about 4.2, net about 1.0–1.2) come from the authors' slides; the arXiv abstract gives no numbers and the full text could not be opened.
- The Kalshi daily-index fee multiplier is unconfirmed.
- The Stevens study is not peer-reviewed.

# Sources

Equities / ETFs
- https://ideas.repec.org/p/nbr/nberwo/7032.html — Gatev, Goetzmann, Rouwenhorst, pairs trading
- https://strathprints.strath.ac.uk/41722/ — Do & Faff 2012, pairs net of costs
- https://ideas.repec.org/a/taf/quantf/v16y2016i10p1541-1558.html — Rad, Low, Faff 2016, distance/cointegration/copula
- https://ideas.repec.org/p/zbw/iwqwdp/092015.html — Krauss, pairs-trading survey
- https://ideas.repec.org/a/taf/quantf/v10y2010i7p761-782.html — Avellaneda & Lee 2010, PCA stat arb
- https://arxiv.org/abs/2106.04028 — Guijarro-Ordonez, Pelger, Zanotti, deep-learning stat arb
- https://rpc.cfainstitute.org/research/financial-analysts-journal/2017/inefficiencies-in-the-pricing-of-exchange-traded-funds — Petajisto 2017, ETF premia
- https://www.nber.org/papers/20071 — Ben-David, Franzoni, Moussawi, ETF arbitrage
- https://www.nber.org/papers/w6572 — Froot & Dabora, twin stocks
- https://ideas.repec.org/a/oup/revfin/v13y2009i3p495-520.html — De Jong, Rosenthal, van Dijk, dual-listed companies
- https://ideas.repec.org/a/eee/jfinec/v97y2010i1p53-80.html — Gagnon & Karolyi, cross-listing parity
- https://www.nber.org/papers/w5167 — Shleifer & Vishny, limits of arbitrage
- https://ideas.repec.org/a/bla/jfinan/v58y2003i6p2375-2400.html — Hasbrouck 2003, index price discovery
- https://www.richmondfed.org/publications/research/econ_focus/2015/q3/research_spotlight — Budish, Cramton, Shim summary
- https://ideas.repec.org/a/oup/rfinst/v20y2007i4p1113-1138.html — Hou 2007, intra-industry lead-lag
- https://www.cxoadvisory.com/volatility-effects/leveraged-etf-pairs-performance — Peterburgsky, leveraged-ETF pairs
- https://ideas.repec.org/a/rsk/journ6/6983576.html — Tsalikis & Papadopoulos, leveraged-ETF shorts
- https://docs.alpaca.markets/us/docs/margin-and-short-selling — Alpaca shorting and borrow
- https://docs.alpaca.markets/us/docs/historical-stock-data-1 — Alpaca IEX vs SIP
- https://www.finra.org/sites/default/files/2026-04/Regulatory-Notice-26-10.pdf — FINRA intraday margin replaces PDT

Options
- https://ideas.repec.org/a/bla/jfinan/v64y2009i3p1377-1406.html — Driessen, Maenhout, Vilkov 2009
- https://repec.cepr.org/repec/cpr/ceprdp/DP16389.pdf — Faria, Kosowski, Wang, correlation risk premium
- https://ideas.repec.org/a/eee/glofin/v20y2009i3p289-301.html — Marshall 2009, dispersion
- https://quantpedia.com/strategies/dispersion-trading — Deng, dispersion decay (summary)
- https://ideas.repec.org/a/gam/jmathe/v8y2020i9p1627-d416291.html — Schneider & Stübinger 2020
- https://cdn.cboe.com/resources/indices/documents/Implied_Correlation-WhitePaper-v1.0.5.pdf — Cboe implied correlation
- https://www.indexologyblog.com/2023/09/27/introducing-the-dispersion-index-dspx/ — DSPX
- https://aeaweb.org/conference/2018/preliminary/paper/Rt6FbZde — Buss, Schönleber, Vilkov, sector implied correlation
- https://lbsresearch.london.edu/id/eprint/1821/1/Risk_free_interest_rates_FINAL_ACCEPT_02092021.pdf — van Binsbergen, Diamond, Grotteria, box rates
- https://libertystreeteconomics.newyorkfed.org/2023/10/options-for-calculating-risk-free-rates — NY Fed box rates
- https://pages.stern.nyu.edu/~eofek/options.pdf — Ofek, Richardson, Whitelaw, parity and short constraints
- https://ideas.repec.org/a/taf/apmtfi/v22y2015i2p162-188.html — Leung & Sircar, LETF implied vol
- https://arxiv.org/abs/1910.05750 — Acciaio & Guyon, VIX vs SPX

Event contracts
- https://www.federalreserve.gov/econres/feds/files/2026010pap.pdf — Diercks, Katz, Wright, Kalshi macro markets
- https://www.cmegroup.com/articles/2023/understanding-the-cme-group-fedwatch-tool-methodology.html — FedWatch method
- https://kalshi.com/docs/kalshi-fee-schedule.pdf — Kalshi fees
- https://help.kalshi.com/incentive-programs/liquidity-incentive-program — Kalshi maker incentive
- https://cftc.gov/filings/ptc/ptc042722kexdcm001.pdf — Kalshi INX filing
- https://fsc.stevens.edu/event-contract-mispricing-via-options-implied-probabilities/ — Appana, Kalshi vs SPXW
- https://arxiv.org/abs/2508.03474 — Saguillo et al., Polymarket arbitrage
- https://arxiv.org/html/2601.01706v1 — Gebele & Matthes, cross-venue gaps
- https://www.ifo.de/en/cesifo/publications/2026/working-paper/makers-and-takers-economics-kalshi-prediction-market — Bürgi, Deng, Whelan
- https://papers.cool/arxiv/2609.23969 — Crosier, Kalshi weather vs NBM (abstract)
- https://www.cmegroup.com/education/articles-and-reports/weather-options-overview.html — CME weather
- https://help.kalshi.com/markets/popular-markets/weather-markets — Kalshi weather rules
