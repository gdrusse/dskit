# Question

Are there equities that are easier to model and detect short-horizon (one to five minutes ahead) edge on than the current five (AAPL, JPM, LLY, WMT, XOM), available on Alpaca from the same 2016 back history, so a second cohort can test whether the first is simply a hard batch? Is crypto a candidate?

# Finding

**Rule of thumb.** At an out-of-sample skill of about 0.3%, the expected gain per trade is roughly 0.045 times the typical three-minute move, so the move divided by the half-spread must be about 25 to 45 or more to clear the spread. LLY sits near 8 (move about 17bp, half-spread 1.5 to 3bp) and cannot; AAPL is near 50 but is simply efficient.

**Shortlist** (all Alpaca SIP, same ticker since before 2016-01-04, verified 2026-09-03; adjustment=split):

1. **TSLA** - retail market-order flow gives short-horizon momentum; move about 30bp against a 0.3bp half-spread (ratio near 100). Splits 5:1 2020-08-31, 3:1 2022-08-25.
2. **TQQQ** - a clocked 15:30-16:00 rebalancing flow, documented; a three-times move on a one-cent spread (about 40 / 0.7). Several forward splits inside the window. The rolling-beta SPY residual absorbs the leverage, so the label needs no change.
3. **NVDA** - the largest retail inflows of 2024-25, semis leader; about 25 / 0.3. Splits 4:1 2021-07-20, 10:1 2024-06-10.
4. **AMD** - NVDA and SOXX follower; cross-stock lagged returns are the proven one-minute signal (Chinco et al.); about 28 / 0.7. No splits.
5. **META** - about 20 / 0.3. FB renamed META 2022-06-09; Alpaca returns the old bars under META by default.
6. **AMZN** - about 17 / 0.3. 20:1 split 2022-06-06.

Skipped: VXX (the note was replaced 2019-01), UVXY and SVXY (leverage cut 2018-02-28). Cheap large-tick stocks are the most predictable names in the literature (Ait-Sahalia), but that is queue and bounce, with the spread as large as the move.

**Backfill first:** TSLA, TQQQ, NVDA, AMD - in that order. TSLA has the most flow and volatility per basis point of spread; TQQQ is the only candidate with a timed mechanism, and 15:30 is a scored instant; NVDA leads a semis cross-stock block; AMD tests the lead-lag against NVDA.

**Calibration.** Bar-only machine learning on SPY reaches about 0.24% skill at five minutes ahead (2001-2016). The project figure of 0.30% is typical for lagged-return models, not evidence of a hard batch.

**Crypto: no.** Alpaca crypto minute bars begin 2021-01-01 (own probe; the forum says 2020-04-08 for older endpoints), so there is no 2018 history. Fees are 15/25bp maker/taker against a BTC three-minute move near 14bp: a 0.3%-skill edge (about 0.6bp) is roughly thirty times below cost. Trading is 24/7, which breaks the regular-hours filter and the day-as-shuffle unit. It would also need a new connector.

**Action taken.** A second split-adjusted source, alpaca-sip-split-b (configs/source-alpaca-split-b-backfill.json), same window and knobs as alpaca-sip-split, was registered and backfilled for the four names.

# Sources

- https://www.nber.org/papers/w30366 - Ait-Sahalia et al., which stocks are predictable
- https://onlinelibrary.wiley.com/doi/10.1111/jofi.12733 - Chinco et al., one-minute cross-stock LASSO
- https://www.sciencedirect.com/science/article/abs/pii/S0304405X21001598 - Baltussen et al., leveraged-ETF last-half-hour momentum
- https://academic.oup.com/rof/article-abstract/20/6/2379/2418138 - Shum et al., leveraged-ETF end-of-day rebalancing
- https://www.researchgate.net/publication/343949189_Intraday_market_predictability_A_machine_learning_approach - SPY five-minute skill 0.24%
- https://onlinelibrary.wiley.com/doi/10.1111/mafi.12413 - Kolm et al., information-rich stocks are predictable
- https://finance.yahoo.com/news/10-stocks-retail-investors-craved-in-2024-161231103.html - retail inflows NVDA, TSLA, AMD
- https://docs.alpaca.markets/us/docs/market-data-faq - Alpaca asof rename mapping
- https://forum.alpaca.markets/t/cant-get-btc-historical-data/10206 - Alpaca crypto history start
- https://docs.alpaca.markets/us/docs/crypto-fees - Alpaca crypto fees
