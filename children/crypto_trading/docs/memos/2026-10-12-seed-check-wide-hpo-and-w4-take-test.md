# Seed check, wider HPO and the W4 take test (2026-10-11 to 2026-10-12)

## TL;DR

The GRU pick holds up over 3 seeds, and a wider 21-candidate HPO found nothing that beats the zoo default out of sample, so the predictor stays the default GRU. On W4 (Kalshi 15-minute BTC/ETH, 2026-08-01 to 2026-09-15), simple take-at-the-touch trading on its fair value is **not reliably profitable**: the best cell (margin 5c) made +$53 on 5,638 one-contract trades (+0.94c a trade, t = 1.22), well inside noise after testing 5 margins. The GRU is the best of the four fair values at every margin, but a small edge over the others is not an edge over the market. Largest caveat: candle-close quotes, taker only, today's fee schedule.

## Execution contract

- Seed check: `gru-crps_tail-pooled`, `patchtst-nll-pooled`, `cnn1d-nll-pooled` (best cell per architecture) re-run on W1 (14 folds) with seeds 29 and 47 beside the zoo's 11; everything else identical (`batch-seedcheck`).
- Wider HPO: `workflow-hpo.json` with `hpo.space` (16 seeded draws, seed 20261011) plus bundles a to d and the default, on W1 folds 1 to 4; winner frozen, then tuned vs default on folds 5 to 14 (`hpo-wide/`).
- W4: runbook B8. Panel `panel-minute-w4` (`minute-w1w4`, reproduces every W1/W2 row, checked); W3 refit = default GRU, every minute (3,167,852 examples), trained to 2026-07-30 (2026-07-31 embargo), early stop at epoch 17; `run-w4-take.json` (model pinned by `state_hash`), margins 0, 1, 2, 3, 5c, one contract, taker. W5 (from 2026-09-15) not read; readers cut every market, anchor and candle at the W5 start.
- Code: `claude/crypto-hpo-wide-20261011` (f7f3bd24), `claude/crypto-w4-take-20261011` (4c158a20, da43eb69; ADR-0262). Both Haiku-reviewed: no Critical/Major.

## Seed check (mean weighted CRPS, W1, lower is better)

| Cell | Seed 11 | 29 | 47 | Mean |
|---|---|---|---|---|
| GRU crps_tail pooled | 2.24696 | 2.24692 | 2.24739 | **2.24709** |
| PatchTST nll pooled | 2.25006 | 2.24986 | 2.25014 | 2.25002 |
| 1D-CNN nll pooled | 2.25128 | 2.25166 | 2.25128 | 2.25141 |

Seed-averaged, per fold: GRU minus PatchTST = -0.0029 (paired SE 0.0007), GRU better in 14 of 14 folds; GRU minus 1D-CNN = -0.0043 (SE 0.0004), 14 of 14. The spread across seeds (SD about 0.0003) is a tenth of the gap, so the pick is not a seed accident. GRU vs empirical baseline, 3-seed: -1.09%.

## Wider HPO

Warmup winner: draw s10 (width 32, one layer, trunk [64, 32], dropout 0.22, lr 8.6e-4, weight decay 4.5e-4, batch 1024), 2.25101 vs default 2.25153. Out of sample on folds 5 to 14: tuned minus default = -0.00017 (SE 0.00043), not distinguishable from zero. Decision: keep the default (simpler, and already the one checked on W2).

## W4: the predictor on Binance

Weighted CRPS over W4: GRU 2.4135, empirical 2.4485 (-1.4%), EWMA-Student 2.6071. W4 is harder than W2 for every model (the level is higher), but the gain over the baseline held.

## W4: the take test

Rule: buy YES at the ask when fair minus mid exceeds the fee plus the margin, or NO at 1 minus the bid in the mirror case; hold to settlement; one contract. 25,270 decision rows with a two-sided quote; all four fair values priced every row, so the comparisons are on identical rows.

| Margin | GRU P&L (trades, t) | RMS | EWMA | BVOL |
|---|---|---|---|---|
| 0c | -$76.85 (17,687, -1.24) | -$122.44 | -$103.48 | -$343.97 |
| 1c | -$49.53 (14,243, -0.81) | -$83.95 | -$82.25 | -$328.15 |
| 2c | -$4.11 (11,543, -0.07) | -$58.36 | -$50.65 | -$323.01 |
| 3c | +$19.23 (9,357, +0.36) | -$44.88 | -$30.95 | -$293.18 |
| 5c | +$46.39 (6,220, +0.98) | +$12.29 | +$7.44 | -$285.47 |

Selecting on the executable price instead (fair minus ask minus fee above the margin) gives the GRU -$62.23 at 0c, +$21.62 at 2c, +$39.26 at 3c and **+$53.03 at 5c** (5,638 trades, +0.0094 a trade, SE 0.0077, t = 1.22, hit rate 0.335). By lead at 5c (mid rule): 2 minutes is the only consistently positive lead; 5 minutes is negative at every margin.

Reading: profit per trade rises with the margin, which is what a real but thin edge looks like; but no cell is significant (the best t is 1.2, and 5 margins x 4 models x 2 rules were looked at), and 45 days with day-block errors cannot separate +1c a trade from zero. Fees take most of the gross edge at small margins.

## Caveats

- Quotes are 1-minute candle closes, not a book: no depth, no queue, no fill risk, no latency (the fair value ignores the seconds to the fill and the 60-second settlement average).
- Taker only; maker quoting (the README's intent) is not tested. Fee schedule is today's, for one contract rounded up to the cent.
- W4 is also the 15-minute kill test's development segment; the owner authorized its use here.
- One model, one seed for the refit, 45 days. Margins were predeclared but several were read: picking 5c now is a W4-tuned choice, to be confirmed on W5 only once.

## Handoff

Not profitable enough to act on. Options: confirm the 5c rule once on W5 (the holdout, owner's call); test maker quoting, which needs book or trade data (`kalshi-crypto-books-15m`, `kalshi-history-trades-15m` are not acquired); or improve the forecast at the 2-minute lead where the edge looks largest. A Kelly MIO is not worth building until a take rule shows an edge.

## Addendum: market vs our distribution, lead map (2026-10-12, W4 only)

- Market beats the GRU on its own (Brier/log loss, every segment), but a logistic blend of mid and GRU (full-W4 weights 0.75 mid, 0.35 GRU, in logits) adds information. Not a stale-quote effect: the quote reprices about 95% of the last minute's spot move, and the GRU weight is unchanged controlling for it. It concentrates after large early moves (the market prices YES too close to 50c).
- `configs/run-w4-leads.json` (run `crypto-w4-leads-2026-09-15-96caf482`, 110,864 rows) rebuilds the decision table at every admissible lead, 2 to 14; on leads 2, 5 and 10 its rows match the take run's (25,270 rows, max |fair_gru| difference 2.4e-7, mid identical). The config scores fair_gru alone; the blend below is computed from its table by the research scripts (refit daily on all earlier days, scored out of sample on days 16 to 46, taker after fee):

| Leads | Margin | Trades | P&L | Per trade | t |
|---|---|---|---|---|---|
| 8–14 | 1c | 6,612 | +$249 | +3.8c | 3.9 |
| 8–14 | 2c | 2,939 | +$153 | +5.2c | 4.7 |
| 8–14 | 3c | 1,221 | +$77 | +6.3c | 3.7 |
| 2–7 | 2c | 2,509 | +$43 | +1.7c | 1.5 |

- At 2c, leads 8–14: both OOS halves positive (+6.6c, t 5.1; +4.3c, t 2.6); BTC +5.5c (t 4.4), ETH +4.2c (t 1.8); YES and NO both positive; one trade per market (earliest lead) +4.4c (t 3.9). Leads 7–12 are each t > 2 at 2c; the gain rises with the lead and fades after 12.
- Per-lead fits and adding momentum features do not beat the pooled two-input blend. The model-free momentum rule is weak once out of sample.
- Caveats: about 590 cells searched over these studies, and the lead range was chosen after looking at W4. The halves decline. Candle quotes; the fill is at the decision-time quote (the 5 s execution lag moves the fair value's horizon, not the price); one contract; today's fees. The blend scripts are exploratory and outside the repo (session scratchpad `research/leads/`); a rerunnable blend needs a pipeline node, which comes after an ADR.
- Next: write down the rule (pooled mid+GRU logistic, refit expanding, leads 8–14, taker, 2c margin), then the one W5 read when the owner approves.
- **Rerun from the repo (ADR-0264, `configs/run-w4-blend.json`, run `crypto-w4-blend-2026-09-15-af52e6cb`).** `SklearnExpandingRefit` refits the blend every UTC day from 2026-08-16. Each fit uses only markets that had closed before that day. `BucketedBinaryScore` scores it with the mid-side rule. Leads 8–14: 1c, +2.8c × 9,232 (t 3.0); 2c, +4.2c × 4,209 (t 3.8, +$176); 3c, +5.0c × 1,793 (t 3.7). Leads 2–7 are at most t 1.6. GRU alone on the same rows is about 0 at every margin. The trade counts differ from the scratchpad figures because the rule triggers on the mid, not the executable side, and because training is cut by close time.
- Review minors, not fixed: the `logit` clip has a code default (1e-6), and the config declares 1e-4. `known_field` is close time, which ignores the few seconds before a result is published. A misspelled feature name only marks every row `bad_feature` instead of refusing the run. With `predict`, a one-class fit raises an error instead of returning a named status.
