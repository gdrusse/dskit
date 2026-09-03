# Runs now read the clean prices, from 2018

Date: 2026-09-03

## What changed

Every run document but one now reads the split-adjusted store and starts
at 2018-01-01. The one exception compares the two live feeds, which only
works on the as-traded prices. All five stocks are scored again.

## The fund with the fake 18% drop

XLF fell 18.2% on 19 September 2016 because it handed out part of itself,
not because it lost value. The vendor does not fix that on either price
scale. The study now starts in 2018, so that day is out of reach and the
fund is kept whole. 2018 was picked for the training window: it sits two
years and four months before the earliest day any run reads, so no fold
moves and the window could still be doubled.

## Two knobs a run can now set for itself

How often a row is formed (every 1, 5 or 10 minutes) and which price is
used (last trade or the minute's average). A third price, the midpoint of
buy and sell, is named and waiting for the data. A run can also say it
wants to be judged every 30 minutes whatever its row spacing, so runs
formed at different speeds are compared on the same moments.

## Checked, without running anything

All 56 run documents load. The store answers for all five stocks and all
seven funds, 2016-01-01 to 2026-02-28. The minute-average price is
present and usable on every one of 135,799 sampled bars from 2018 on. No
model was fitted and no walk was run.

## Sources

- `configs/run-*.json`, `configs/universe.json`
- `intraday_equities/nodes.py`
- ADR-0065 and ADR-0066, `docs/architecture/decision-log.md`
- `docs/research/p9-fixed-the-history-is-split-adjusted-and-the-splits-are-gone.md`
