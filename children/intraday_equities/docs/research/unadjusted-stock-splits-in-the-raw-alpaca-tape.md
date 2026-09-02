# Unadjusted stock splits in the raw Alpaca tape

Date: 2026-09-02

## Question

Fold 10 of the pair walk reported a validation MSPE of 5.16e-04, which
is 170x the normal 2e-06 to 4e-06 and 11x worse than the COVID crash
fold. Nothing in the market did that. What is it?

## Finding

Corporate actions. The source is declared `adjustment: raw`, which is a
deliberate and documented invariant, but nothing downstream compensates
for splits. A search for split, corporate-action or adjustment logic
across the child's code and configs returns nothing.

| Symbol | Effective | Last pre-split | First post-split | log return |
|---|---|---|---|---|
| AAPL | 2020-08-31 | 502.09 | 127.62 | -1.3580 |
| WMT | 2024-02-26 | 175.90 | 59.12 | -1.0914 |

A single AAPL split bar predicts a validation MSPE contribution of
5.20e-04 against the 5.16e-04 observed, so one bar is essentially the
entire fold-10 error.

**These are the only corrupt values in ten years of data.** Sweeping all
8,885,389 bars, then the 5,282,576 that survive the regular-hours
filter, finds 124 consecutive-bar moves of 5% or more. 121 are overnight
gaps and are genuine — March 2020, earnings, the 9 March 2020 oil price
war gap in XOM at -17%. Only three intraday moves exceed 5%, about one
in 1.8 million bars. The largest genuine move anywhere is LLY at -0.1666,
so the two splits are roughly 8x larger than anything real. There is no
grey zone.

**The damage is not one bar.** Labels are positional on the concatenated
regular-session tape, so Friday's last bar looks forward into Monday.
With horizons running to 1170 minutes, every row for three sessions
before a split carries a fake -75% label. Features inherit it forward:
the cross-session scales span two and five sessions, so momentum,
realized vol and the overnight gap carry it for a week after.

**Effect on the model, measured.** With one split-sized label planted in
100k training rows and the pair estimator: rank IC barely moves (val
0.0639 to 0.0591), which is why fold metrics looked healthy. But the
forecasts correlate only 0.672 with the clean model, and the worst
distortion is 17x a typical forecast magnitude. Note `min_child_samples:
400` is a floor, not an isolator — it forbids LightGBM from putting the
outlier in its own leaf and forces it to be averaged with 400 innocent
neighbours. On the decision itself the effect is a bias toward
indecision: p-values move toward 0.5 from whichever side they started,
roughly doubling with real signal and falling from 0.91 to 0.52 with
none. In a sweep across signal strengths no GO flipped at alpha 0.05.

## Remediation

`adjustment` is validated against `raw`, `split`, `dividend`, `all`, so
the fix is a one-value config change with no code. For 5-minute price
returns `split` is right rather than `all`: a split is a unit change and
must go, a dividend is a real price drop the model should arguably see.
The cost is re-acquiring the 2016-2026 backfill, and because split
adjustment back-adjusts every prior price, it changes every feature and
breaks comparability with everything recorded so far. That is an owner
decision, not taken here.

A cheap complement, not a substitute: refuse any label above 0.5 in
absolute log return. That would have caught both splits on first
contact and never fires on any genuine move in ten years, a 6.5x margin.

## Sources

- `ob/raw/alpaca-sip/*/payload/bars.jsonl.gz`
- `configs/source-alpaca-backfill.json` — `adjustment: raw`
- `dskit/onboarding/libs/alpaca.py` — accepted adjustment values
- `docs/decisioning/logs/` — pair walk folds 10-22
