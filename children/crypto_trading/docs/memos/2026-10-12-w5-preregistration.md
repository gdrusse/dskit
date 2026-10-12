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

## Result (read once, 2026-10-12): FAIL

Run `crypto-w5-blend-2026-10-08-0aa3e2c8`. The first run, `-6a94138c`, computed the w5 rows but left `report_segments` at w4. Commit cf60ad8c changed only the reporting, before any W5 outcome was read. The W5 panel reproduces every W1–W4 row.

| Cell (W5, one contract) | Trades | Per trade | t |
|---|---|---|---|
| **Primary: blend, leads 8–14, 2c** | **1,874** | **−0.15c** | **−0.10** |
| Blend, leads 8–14, 1c | 4,897 | +1.27c | +1.22 |
| Blend, leads 8–14, 3c | 650 | +0.64c | +0.35 |
| Blend, leads 2–7, 2c | 1,671 | −3.02c | −1.51 |
| GRU alone, all leads, 2c | 22,519 | −0.72c | −0.85 |

- Brier, blend vs market: −0.00028 at leads 8–14 (t −1.1), so the blend is closer to the outcome than the market but the difference is not significant. The GRU alone is worse than the market (+0.0020, t +3.2).
- The blend's weights did not drift: about 0.76 mid and 0.32 GRU in logits on 2026-08-16, mid-W4 and the last W5 day. The model stayed the same while the edge it found on W4 disappeared.
- Sizing on W5 decisions only, from $1,000: half-Kelly $547 (worst drawdown 70%), quarter-Kelly $788 (46%), flat $1,005.
- Per the plan, this rule is closed. W5 is now development data, and any new rule is tested on markets closing from 2026-10-12.
