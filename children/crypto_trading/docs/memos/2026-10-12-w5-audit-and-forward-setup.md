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

## Maker simulation, first pass (research `research/maker/`, candle proxy over all of W5)

Maker fee is 0: the fee schedule is quadratic and "makers pay nothing" (`fees.py`). One contract rests at the touch until the next decision (60 s), leads 8–14. Fills are counted two ways: conservative (the next candle trades through our price) and optimistic (it touches our price).

| Rule | Fills/day | Conservative | Optimistic |
|---|---|---|---|
| Touch, no signal (adverse-selection baseline) | about 2,400 | −1.47c (t −17) | +0.14c |
| Bid the favourite when mid ≥ .80 (sell the long shot) | about 240 | −0.09c (t −0.1) | +1.21c (t 1.3) |
| Quote with the early move (abs(ropen_z) > 1) | about 390 | −0.63c (t −0.7) | +1.05c (t 1.3) |
| Blend-skewed, ±1–3c | about 2,000 | −0.8 to −1.0c (t −5 to −10) | ≤ 0 |

- Resting 60 s at the touch costs about 1.8c in adverse selection against the 0.5c half-spread earned.
- The two biases turn positive only under optimistic fills and are nowhere near significant.
- On one real-tape day (33 markets, 2026-10-07) the fill rates match the proxy.
- **The verdict waits for the real tape.** The W5 trade pull runs at about 23 s a market, so it lands in about 24–28 h, not 13. `logs/maker-when-ready.sh` runs `research/maker/run.sh` on it automatically and writes `research/maker/report.out`.
- Only if the move-with rule is positive under a queue-aware fill model on the real tape does it get a forward pre-registration, scored on the recorded books.

## Maker simulation on the real tape (2026-10-15): no maker edge

Source `kalshi-history-trades-15m-w5`, pulled as six widened close windows after two WSL crashes (C: was full); 86.9M trades, all 4,368 W5 markets. Same rules and orders as the proxy; fills from the prints: conservative (traded through our price), queue (at least 1,000 contracts at our price, about a touch-size queue) and optimistic (any trade at our price).

| Rule (cancel at the next decision) | Fills/day | Conservative | Queue | Optimistic |
|---|---|---|---|---|
| Touch, no signal | 2,260 | −1.47c (t −17) | −0.95c (t −14) | +0.08c (t 2.3) |
| Sell the long shot (mid ≥ .80) | 220–270 | −0.09c (t −0.1) | +0.50c (t 0.5) | +1.18c (t 1.3) |
| Quote with the early move | 350–430 | −0.63c (t −0.7) | +0.25c (t 0.3) | +1.01c (t 1.2) |
| Blend-skewed, ±1–3c | 1,700–2,100 | −0.8 to −1.0c (t −5 to −10) | −0.65 to −0.73c | −0.14 to −0.40c |

- The adverse selection is real: about 1.5–1.8c against the 0.4–0.5c half-spread earned, at every lead and on both sides.
- The two biases still carry signal (+1.6 to +1.9c), but adverse selection takes almost all of it. The move-with rule is +0.25c, t 0.3 under queue fills, and +0.02c / +0.45c on the two halves. That is not positive in any useful sense, so it gets no forward pre-registration (the rule above). Cancelling at the close changes nothing.
- The blend gives makers nothing either: about −1c whatever the skew.
- **Verdict:** the maker line is closed, like the taker rule on W5. The only live test is the overnight forward rule, read on 2026-11-11.

Ops: the chunked pull is `logs/trades-w5-chunks.sh` (reboot-safe through `trades-w5-resume.sh`). `logs/host-disk-ok.sh` now holds heavy jobs when C: has under 20 GB free. The crashes zeroed the store's event-log tail and left six record files empty; the repair (quarantine plus exact rebuilds) is in `logs/store-repair-20261014/`. Order books are missing for the two outages: 10-13 05:28 to 10-13 22:45 and 10-14 16:21 to 22:32.
