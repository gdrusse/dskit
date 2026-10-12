# W5 pre-registration: the leads 8–14 blended take rule (2026-10-12)

Committed BEFORE any W5 row is read. The owner approved one W5 read on 2026-10-12 ("i approve").

## Window

W5 = Kalshi KXBTC15M/KXETH15M markets closing in [2026-09-15T00:00Z, 2026-10-08T00:00Z), 23 days. The end is the last full day every input covers as acquired on 2026-10-09: Binance BVOL through 2026-10-07, klines through 2026-10-08, Kalshi candles and markets to 2026-10-09 04:15. It is fixed now and is not moved after the read.

## Rule (frozen)

- Fair value: `run-w4-blend.json` unchanged except its window. The decision table runs at leads 2–14. `fair_gru` comes from the frozen W4 refit model (trained through 2026-07-30, state_hash `541bff85…`), which is not retrained. `fair_blend` comes from `SklearnExpandingRefit`: logistic regression on logit(mid) and logit(fair_gru), clip 1e-4, C 1. It is refit every UTC day on all markets that closed before that day, from 2026-08-01, with 15 warmup days. W4 therefore trains the first W5 day, and W5's own closed markets join as they close.
- Trade: leads 8–14 only. Buy YES at the ask when fair_blend − mid > fee + 0.02, or NO at 1 − bid when mid − fair_blend > fee + 0.02. One contract, taker, today's fee schedule, held to settlement (`BucketedBinaryScore`, as in W4).

## Primary test (the only pass/fail)

`take_m02`, model `fair_blend`, segment `w5`, group `lead_band=lead_08_14`: mean P&L per contract > 0 with t = pnl_mean / pnl_se (day-clustered) ≥ 2.0, one-sided. If the edge held at the W4 size, the expected t is about 3.3; at half that size it is about 1.7.

## Reported, not tested

The 1c and 3c margins, leads 2–7, each lead, BTC and ETH separately, `fair_gru` alone, Brier and log loss against the market, and half-Kelly, quarter-Kelly and flat sizing restricted to W5 decisions ($1,000 start, `run-w4-kelly.json` params). Kelly dollars stay unconfirmed without depth data.

## After the read

The result is recorded as it comes out. If the primary test fails, this rule is closed: nothing is tuned on W5, and W5 becomes development data. Any new rule is tested on markets closing from 2026-10-12 onward (forward data). If it passes, the next steps are acquiring depth data and a forward shadow test before any capital is committed.
