# W5 audit, two-window patterns and the forward setup (2026-10-12)

## TL;DR

The W5 failure is real, not a bug. The edge the blend found on W4 fell to zero on W5, as the market moved toward our model. Only four patterns hold in both W4 and W5, and two of them pay only to resting (maker) orders. So the next tests are a 30-day forward test of the one taker cell that survived (overnight, leads 9–14), and a maker simulation on real trade prints. The data for both is now being collected: an order-book recorder, a daily forward pull, bounded trade history, and a refit GRU.

## The W5 audit (research `w5/`, independent of the pipeline)

- **Labels and strikes:** labels equal the venue result on 100% of markets in both windows (8,528 W4, 4,368 W5), and so do strikes and closes. A Binance proxy agrees 96.1% in both.
- **Quotes:** taken at the decision minute on 100% of rows, with a 1c median spread in both windows. Volume and open interest are about 20% higher in W5.
- **The GRU:** status ok on every row and fed the same scale. The W5 panel is byte-identical to the W4 one on 3.39M common rows, and its inputs stay inside the training range. Its calibration on Binance did not degrade in W5.
- **Fees** are exact. **The blend** recomputes to 1e-15 from an independent refit.
- **Correction:** the agent reported that the GRU's early stopping was chosen on W4. It was not. Early stopping watches PanelTrain's `cal` band (the last 30 days of each block's own training window); the sidecar's `val_*` fields are the scored window. Tests now pin this for W2, W4 and the forward block.
- **Why W5 failed:** W4 +4.19c (se 1.15) vs W5 −0.15c (se 1.34); the difference has t 2.5.
  - The weights did not drift: frozen W4 weights give +0.31c on W5.
  - The market moved toward the GRU: correlation of logits 0.960 → 0.972, mean gap 4.1c → 3.6c, trades a day 140 → 81.
  - The GRU's increment given the mid shrank from t 4.5 to t 2.0, and was about zero in the low-volatility last 12 days.

## Patterns in both windows (about 450 two-window cells; chance gives about 0.5 strong in both)

| Pattern | W4 | W5 |
|---|---|---|
| Blend, leads 9–14, 00:00–04:00 UTC, 2c (taker) | +5.8c, t 1.9 | +7.1c, t 2.3 |
| Buying long shots (mid ≥ .80) after fee | −4.1c, t −7 | −2.7c, t −3 |
| Reversal after an early move (abs(ropen_z) > 1) | −4.4c, t −7.5 | −2.8c, t −3.4 |
| Market-implied vol below trailing 60-minute realized vol | −16%, t −13 | −13%, t −18 |

The overnight cell was chosen after seeing both windows (one of 6 hour buckets). The two biases are robust, but as taker trades they are about zero; they need resting orders.

## Built today

All reviewed by Haiku, no Critical or Major issues left, and merged.

- **ADR-0264 `SklearnExpandingRefit`** and the W4/W5 blend configs.
- **ADR-0266 `httpblobs` `entities_dates`:** Binance days advance in `live` mode without a daily config edit. Days the vendor never publishes are given up after 7 days, so they cannot stall the feed.
- **ADR-0267 `kalshi_history` `close_window`:** a trades pull limited to markets closing in a window. Completeness is recorded per series, so widening the window later costs nothing. Source `kalshi-history-trades-15m-w5`.
- **The held-out guard** now reads the forward cut, `configs/holdout-forward.json` (2026-10-12). W5 is development data.

## Running

- **Order-book recorder** for both 15-minute markets every 15 s. Restarted at boot by cron.
- **Daily forward pull** at 02:15 local: Kalshi markets, fees and candles, plus Binance in live mode (`data/crypto_trading/logs/forward-pull.sh`).
- **W5 trade-print pull:** 4,416 markets, about 66M trades, about 13 h, unvalidated (validation would need about 150 GB of memory).
- **Forward GRU refit** (runbook B9): trained through 2026-10-05. On 2026-10-07 it scored 2.396 vs empirical 2.431. Copied to `data/crypto_trading/production-fwd`.
- **Kalshi history markets:** 15-minute BTC/ETH markets exist from 2025-12-16, about 2,900 per series per month. With trade prints this period could be a third test window, scored by the W1 zoo and W2 models. Validation is skipped: it needs more than 11 GB.

## Next

- **Forward test** (pre-registered, `2026-10-12-forward-preregistration.md`): markets closing 2026-10-12 to 2026-11-11, read once on 2026-11-11.
- **Maker simulation** on the W5 trade prints, once the pull lands. A rule that works there gets its own forward pre-registration, scored on the recorded books.
- **Optional:** widen the trades window back to 2025-12-16 for the pre-W4 window, about 4 days of pulling.
