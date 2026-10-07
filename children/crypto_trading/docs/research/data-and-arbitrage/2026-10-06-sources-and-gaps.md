# Question

Which crypto data sources give us the deepest, most scalable access from a US
host, and do any arbitrage or near-arbitrage gaps survive fees? Gaps checked:
across platforms, within one venue, and between highly correlated markets.

# Finding

**Data (probed 2026-10-06 from a US container).** Free data covers spot, perps,
open interest, funding, full order books, option trades with implied vol, and
prediction markets.

- **Binance Vision:** bulk zips. Spot from 2017-08, USDⓈ-M from 2020-01. Includes
  klines, trades, funding, metrics/OI, bookDepth and the BVOL index. The licence
  is CC BY-NC-SA and forbids live proprietary trading, so it is research only.
- **OKX:** free tick order books at 400 levels (about 388 MB a day for BTC-SWAP).
- **Bybit:** free order-book dumps.
- **Deribit:** every trade since 2016 with implied vol.
- **US spot (Coinbase from 2015, Kraken from 2013, Bitstamp from 2011):** REST
  history plus free live WebSocket books. Coinbase and Kraken are among the
  exchanges in the CF Benchmarks BRTI index that Kalshi settles on.
- **Kalshi:** reads need no key, including 1-minute candles. Data before
  2026-08-07 sits under `/historical/*`, and there is no historical order book.
- **Polymarket:** reads work, but US IPs can only close positions.

Paid data:
- **Synchronized cross-venue books:** Tardis.dev, $350/mo (academic) to $700/mo,
  history from 2019. No free archive shares one clock across venues; the free
  alternative is recording our own WebSockets from one host.
- **CME:** Databento, $0.50/GB.
- **IBIT options (OPRA):** ThetaData from $40/mo or Alpaca at $99/mo.

**Arbitrage (literature, plus a live snapshot 2026-10-06 15:17–16:07 UTC).**

| Candidate | Result |
|---|---|
| Kalshi ladder coherence | No gap net of fees. Violations were at the 1c tick, with depth of 0.2 contracts or less. |
| Kalshi vs Polymarket, same noon expiry | 2–9c net, 3–534 contracts of depth. Always: buy Polymarket YES, buy Kalshi NO. Not riskless: settlement sources differ (BRTI 60-second average vs a single Binance print). Gaps were widest when spot sat near the strike. Polymarket's main exchange is closed to US users. |
| Kalshi vs Deribit options | Model gaps of 1–5c, inside model error. Deribit call spreads cost 10–35c in probability terms, so this is a relative-value signal, not an arb. |
| Kalshi calibration, 30 min before close | 41,990 markets checked, 3,739 with two-sided quotes (Sep 8–Oct 6). Calibrated within noise, with a mild longshot tilt. Every naive take loses money after spread and fees. |
| Cross-exchange spot | At most 3.8 bps with tiny depth, against fees of 25–60 bps per leg. Not for us. |
| Perp carry (Kalshi BTCPERP, Coinbase CFM, Kraken US) | Basis has been below T-bills since Feb 2026; Kalshi funding reached +16.6%/yr on Oct 6. This is carry, not arbitrage, and each leg is margined separately. |
| Latency (15-minute contracts), IBIT/FBTC/BITB, MSTR, DEX–CEX | Not capturable by us. |

**Implications:**
- There is no riskless arb for us. Kalshi takers face a calibrated market.
- The edge has to come from a fair value that beats the market's mid, used as a
  maker.
- The other candidate is cross-venue relative value against Polymarket US. Its
  15-minute BTC markets launched 2026-09-22. If they settle on the same
  60-second BRTI average (rulebook unconfirmed), the pair becomes close to
  identical.
- Tests to run next:
  1. Log both venues' order books once a second for 7 days.
  2. Run the kill test on the realized-vol fair value.

# Sources

- https://data.binance.vision/Binance_Vision-Terms_of_Use.pdf — Binance Vision licence
- https://docs.kalshi.com/getting_started/historical_data — Kalshi historical cutoff
- https://docs.kalshi.com/getting_started/rate_limits — Kalshi rate limits
- https://docs.polymarket.com/api-reference/geoblock — Polymarket geoblock
- https://docs.polymarket.com/trading/fees — Polymarket crypto fees
- https://support.deribit.com/hc/en-us/articles/25973087226909 — Deribit trade history
- https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits — Coinbase rate limits
- https://tardis.dev — Tardis pricing and coverage
- https://databento.com/catalog/cme/GLBX.MDP3/options/BTC — CME via Databento
- https://thetadata.net/subscribe — ThetaData pricing
- https://arxiv.org/abs/2508.03474 — Polymarket arbitrage, $40M (Apr 2024–Apr 2025)
- https://arxiv.org/abs/2601.01706 — cross-platform prediction-market price gaps
- https://arxiv.org/abs/2606.19517 — Polymarket vs option-implied probabilities
- https://arxiv.org/abs/2607.26245 — Polymarket 15-minute latency study
- https://ris.uni-paderborn.de/record/34449 — Crépellière et al., cross-exchange arbitrage
- https://www.fairgambling.com/news/polymarket-us-15-minute-bitcoin-markets — Polymarket US 15-minute BTC
- https://www.cftc.gov/PressRoom/PressReleases/9240-26 — Kalshi BTC perpetual approval
- https://www.tftc.io/bitcoin-futures-basis-below-treasury-yields-2026 — CME basis vs Treasuries
- Live snapshot scripts and raw data: session scratchpad `arb_snapshot/` (not committed)
