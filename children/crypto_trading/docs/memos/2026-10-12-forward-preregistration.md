# Forward-test pre-registration: the overnight blend rule (2026-10-12)

Committed before any forward market exists. The owner asked for it on 2026-10-12 ("systematically do all of these"). The only cell that held in both W4 and W5 was found after both were seen (research `w5/`, 1 of 6 hour buckets). This test decides whether it is real.

## Window

Kalshi KXBTC15M/KXETH15M markets closing in [2026-10-12T00:00Z, 2026-11-11T00:00Z), 30 days. Data comes from the daily forward pull (Kalshi markets, fees, candles, Binance klines and BVOL). The window is not extended or cut after any of it is read. If the pull misses days, the gap is reported and the window stays as written.

## Rule (frozen)

- Fair value: the W5 documents (`run-w5-blend.json`) with only the cut and panel moved forward. Same blend: logistic regression on logit(mid) and logit(fair_gru), clip 1e-4, C 1, refit every UTC day on all markets closed before that day, from 2026-08-01. Same frozen GRU (W4 refit, state_hash `541bff85…`).
- **Primary trade:** leads 9–14, decision instant 00:00 ≤ hour < 04:00 UTC. Buy YES at the ask when fair_blend − mid > fee + 0.02, or NO at 1 − bid when mid − fair_blend > fee + 0.02. One contract, taker, held to settlement.

## Primary test (the only pass/fail)

Mean P&L per contract > 0 with day-clustered t ≥ 2.0, one-sided. Expected about 15 trades a day. At the W4/W5 size (+6c) that gives t ≈ 2.3 over 30 days; it is a weak test by design and is reported as such.

## Reported, not tested

- The W5 rule (leads 8–14, all hours, 2c).
- The 1c and 3c margins.
- A retrained-GRU arm (early stop on W5), if it exists by then.
- Brier of the blend vs the market.
- A maker-quote simulation from the recorded books (`kalshi-crypto-books-15m`, 15 s), once a quote rule is written down. It is a separate pre-registration.

## After the read

A fail closes the overnight rule. A pass is followed by a second 30-day forward window before any capital is committed.
