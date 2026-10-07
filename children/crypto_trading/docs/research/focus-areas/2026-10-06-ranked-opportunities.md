# Question

Where should a US retail ML researcher with dskit's options, prediction-market
and production infrastructure focus crypto trading research to make money net
of realistic costs (October 2026)?

# Finding

**Costs decide almost everything.** Lowest-tier spot taker fees are 25 bps
(Alpaca) to 80–90 bps (Kraken Pro, Coinbase Advanced). Short-horizon signals
are about 0.5 bp per trade gross. Only low-turnover premia, or venues where you
are the maker, survive. US perps exist now (Coinbase CFM since Jul 2025, Kraken
since Jun 2026); Hyperliquid, Bybit and Binance derivatives are closed to US persons.

**Proposed focus, ranked:**

1. **Crypto prediction-market ladders, maker side.** Kalshi KXBTC/KXBTCD/KXETH
   (hourly and daily ranges, above/below) and 15-minute up/down markets, about
   $4.6B a month. Takers overpay for longshots, and the crypto category has the
   steepest favorite–longshot bias on Kalshi (Bürgi–Deng–Whelan). Price each rung
   from an options-implied or realized-vol distribution of BTC/ETH; quote as maker.
   The free Kalshi historical API needs no auth. Reuses pmquant's ladder method and
   dskit's kalshi/polymarket/predexon connectors and predictive-CDF pack. Gap: the
   hourly ladders are unstudied.
2. **Defined-risk volatility selling on IBIT options.** Implied vol has exceeded
   realized vol roughly 70% of the time (Deribit, 2019–22; still about 1.5x in
   Sep 2025). IBIT ranks #5 of all ETFs by options volume. Use put or call spreads
   to cap a Mar-2020 tail; weekend gaps cannot be hedged. Reuses the
   index_options/stock_options option-data path.
3. **Vol-targeted BTC/ETH trend.** This is the most-replicated crypto effect, and
   its low turnover survives fees. It is beta timing, not new alpha; it serves as
   the baseline sleeve.
4. **Funding/basis carry, regime-gated only.** Binance BTC funding fell from
   11.9% a year (2024) to 2.9% (Jan–Sep 2026), now below T-bills. CME basis was
   about 5% vs 4.5% risk-free (Apr 2026). Keep a monitor (funding − fees − T-bill)
   and act only when it clears.

**Avoid:** high-frequency microstructure and cross-exchange arb (signal about
1/100 of fees), altcoin factors (survivorship and microcap effects), listing and
unlock events (bots and insiders get there first), the overnight-BTC effect (now
an ETF; daily round trips cost more than it pays), raw ML return forecasting
(naive forecasts match it). Use ML to price, size and gate the premia above.

**First falsification tests (cheap, before any model):**
1. Calibration of settled KXBTC/KXBTCD markets against a realized-vol
   distribution, net of the quadratic fee, by price bucket and lead time.
2. Delta-hedged short 30-day IBIT straddles at real bid/ask since Nov 2024.
   Reject if the mean is ≤ 0 or the worst week costs more than 12 months of premium.
3. Trend ensemble vs vol-targeted buy-and-hold, at real fees.
4. Trailing 12-month Coinbase perp funding minus four fee legs minus T-bills.

**Data, all through existing generic connectors:**
- `httpblobs`: data.binance.vision klines and funding from 2017/2020. It is
  reachable from the US; the Binance API is geoblocked with HTTP 451.
- `restapi`: Coinbase, Kraken and Deribit public history.
- `kalshi` / `predexon`: the ladders.
- `alpaca`: IBIT options.

No new connector pack is needed to start.

**Owner decision:** focus 1 overlaps pmquant's domain (Kalshi/Polymarket ladders).
Either host it here and graduate the shared ladder mechanics into dskit, or run it
inside pmquant.

**Caveats:**
- Polymarket US access for crypto up/down markets is unconfirmed.
- Coinbase fee figures come from secondary sources.
- The Polymarket-vs-options evidence conflicts: one study finds a 5–6 point
  premium, another finds good calibration once the volatility risk premium is
  netted.

# Sources

- https://www.karlwhelan.com/Papers/Kalshi.pdf — Bürgi, Deng, Whelan, Kalshi makers vs takers (2025, rev. 2026)
- https://help.kalshi.com/markets/popular-markets/crypto-markets — Kalshi crypto settlement (CF Benchmarks RTI)
- https://kalshi.com/docs/kalshi-fee-schedule.pdf — Kalshi fee formula
- https://docs.kalshi.com/getting_started/historical_data — Kalshi historical endpoints
- https://docs.polymarket.com/trading/fees — Polymarket crypto taker fees and maker rebates
- https://www.theblock.co/post/384461 — Polymarket taker fees on 15-minute markets (2026-01-06)
- https://arxiv.org/abs/2606.19517 — Polymarket BTC contracts vs option-implied probabilities
- https://ideas.repec.org/a/taf/quantf/v24y2024i7p851-869.html — Lucic and Sepp, crypto volatility risk premium
- https://arxiv.org/abs/2410.15195 — Almeida et al., BTC variance risk premium
- https://www.macroption.com/cryptocurrency-etf-options/ — IBIT and ETHA options volume ranks
- https://ideas.repec.org/a/oup/rfinst/v34y2021i6p2689-2727..html — Liu and Tsyvinski, RFS 2021
- https://ideas.repec.org/p/chf/rpseri/rp2580.html — Zarattini, Pagani, Barbon, crypto trend (2025)
- https://www.bis.org/publ/work1087.htm — Schmeling, Schrimpf, Todorov, crypto carry
- https://arxiv.org/abs/2212.06888v6 — He, Manela, Ross, von Wachter, perpetual futures
- https://www.theblock.co/post/396722 — CME basis trade unwind (Apr 2026)
- https://alexandria.unisg.ch/handle/20.500.14171/108037 — Ammann et al., survivorship in crypto factors
- https://arxiv.org/abs/2607.09426 — Kim and Hansen, order imbalance on Binance perps
- https://arxiv.org/abs/2502.09079 — Puoti et al., naive vs ML crypto forecasts
- https://www.thetie.io/insights/what-does-an-exchange-listing-actually-deliver-in-2026 — listing effects 2023–2026
- https://www.marketsmedia.com/coinbase-derivatives-to-launch-us-perpetual-style-futures-on-july-21 — Coinbase US perps
- https://www.thetradenews.com/kraken-launches-first-cftc-regulated-perpetual-futures-for-us-traders/ — Kraken US perps
- https://www.kraken.com/features/fee-schedule — Kraken fees
- https://docs.alpaca.markets/docs/crypto-fees — Alpaca crypto fees
- https://data.binance.vision — Binance public archive (funding statistics computed from it, 2026-10-06)
