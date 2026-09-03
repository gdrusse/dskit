# P9: both splits sit inside our window, and the re-pull costs eight minutes

Date: 2026-09-03

## Question

Are AAPL and WMT genuinely unusable inside the dates we actually hold,
are JPM, LLY and XOM genuinely clean, and what does correcting the
history cost?

## What we hold (read-only sweep of all 8,885,389 Alpaca bars)

| symbol | bars | first bar | last bar in store | last bar in window | session dates in window |
|---|---|---|---|---|---|
| AAPL | 1,919,230 | 2016-01-01 | 2026-08-28 | 2026-02-28 | 2622 |
| JPM | 1,233,216 | 2016-01-04 | 2026-08-28 | 2026-02-28 | 2613 |
| LLY | 1,100,863 | 2016-01-04 | 2026-08-28 | 2026-02-28 | 2602 |
| SPY | 2,136,508 | 2016-01-01 | 2026-08-28 | 2026-02-28 | 2623 |
| WMT | 1,210,046 | 2016-01-04 | 2026-08-28 | 2026-02-28 | 2617 |
| XOM | 1,285,526 | 2016-01-04 | 2026-08-28 | 2026-02-28 | 2616 |

**The history starts in 2016, not 2021.** Timestamps are UTC, so a date
here can be the evening of the previous New York session. Only prices
dated on or before 2026-02-28 were read.

## The jumps, found directly rather than assumed

Consecutive daily last prices were scanned for a ratio within ten per
cent of 1/2, 1/3, 1/4, 2, 3 or 4.

| symbol | before | after | ratio | what it is |
|---|---|---|---|---|
| AAPL | 2020-08-28, 501.10 | 2020-08-31, 129.33 | 0.258 | 4-for-1 split |
| WMT | 2024-02-24, 176.03 | 2024-02-26, 59.65 | 0.339 | 3-for-1 split |

JPM, LLY, XOM and the SPY reference have **no** split-sized jump. Their
largest one-day move in ten years is JPM +16.9% (2020-03-13), LLY +15.5%
(2023-08-08), XOM +11.9% (2020-11-09), SPY +11.3% (2025-04-09) — all
real market days. There is no grey zone between those and the two
splits, which are about six times larger.

**And the splits are inside the folds, not merely inside the store.**
The walk's first validation is 2022-05-06 with a 730-day training
window, so training reaches back to roughly 2020-05-06: AAPL's split
lands in the earliest folds' training data and WMT's lands mid-walk.
So the exclusion is justified, and undoing it requires a real fix.

## How the correction works

The split factor is shares after divided by shares before: 4 for AAPL,
3 for WMT. Every price on or before the last pre-split session is
divided by that factor and every volume is multiplied by it, so the
traded value per bar is preserved. `vwap` is a price and must be divided
too; `trade_count` is untouched. Providers follow CRSP conventions here,
and the adjustment applies only to dates before the event.

Without it, the single return that crosses the split reads as log(1/4)
= -1.386 for AAPL and log(1/3) = -1.099 for WMT. That is a change of
unit being read as a price move, and it is roughly eight times larger
than the biggest genuine move in the whole history.

**Split-only, not total-return.** A split is a change of unit and must
be removed. A dividend is a real fall in the price on the ex-date, and
our target is the short-horizon price return, so removing it would erase
a move that actually happened. Dividend adjustment also rescales the
entire prior series by (close - dividend) / close at every ex-date, so
the numbers change every quarter and a later re-pull would not reproduce
an earlier one.

**One caveat that applies to split adjustment too.** Back-adjustment
expresses history on today's share basis, so any *future* split silently
changes every earlier bar. Record the pull date as the basis, and
re-pull the whole series rather than topping it up when a new split
lands.

## What Alpaca gives us

- `adjustment` on the historical bars endpoint takes `raw`, `split`,
  `dividend` or `all`, and defaults to `raw`. Our connector already
  validates all four and hands the value to the SDK
  (`dskit/onboarding/libs/alpaca.py`), so this is one word in
  `configs/source-alpaca-backfill.json` and **no new code**.
- Re-pulling with `adjustment: "split"` is the clean fix: the vendor does
  the arithmetic, and the manifest, hashes and store registry stay
  truthful because the pull goes through onboarding.
- The corporate-actions endpoint is the alternative:
  `GET https://data.alpaca.markets/v1/corporate-actions` with
  `types=forward_split,reverse_split` returns `old_rate`, `new_rate` and
  `ex_date` per symbol. It would let us adjust in place and keep raw and
  adjusted side by side. Only worth it if we need both scales.
- Two reported defects to check after any pull: Alpaca's split table has
  had missing entries (a batch was fixed through July 2025), and on the
  SIP feed the 00:00-00:59 hour of a split day was reported to come back
  unadjusted. Re-running the jump scan above catches both in minutes.

## Every split for the names in play, 2016-01-01 to 2026-02-28

| symbol | splits in the window |
|---|---|
| AAPL | 4-for-1, 2020-08-31 — **in our data** |
| WMT | 3-for-1, 2024-02-26 — **in our data** |
| JPM | none (last 1987) |
| LLY | none (last 1997) |
| XOM | none (last 2001) |
| SPY | none |
| QQQ | none (last 2000) |
| XLF | 1231-for-1000, 2016-09-19 (the XLRE spin-off) |
| XLV | none |
| XLP | none |
| XLE | **2-for-1, trading 2025-12-05** |
| XLK | **2-for-1, trading 2025-12-05** |

The XLE and XLK splits are new (State Street split XLK, XLY, XLE, XLU
and XLB together; record 2025-12-02, payable 2025-12-04). We hold none
of these ETFs today except SPY, so this only bites the moment sector
ETFs are added — which the feature work is likely to want.

## Cost

- **Re-pull.** The existing 2016-to-2026 backfill of six symbols —
  8.9M bars, 230 MB — took **eight minutes** of wall clock: manifest
  `acquired_at` 19:39:32Z, cursor written 19:47:35Z, on 2026-08-30. At
  `chunk_days` 31 that is about 128 requests. The pull is not the cost.
- **Adjust in place.** About four minutes of compute over the same file,
  but it writes into `ob/` outside the connector, so the manifest hash
  and store registry stop describing what is on disk. Cheaper and worse.
- **The real cost is downstream.** Split adjustment changes every AAPL
  and WMT price, so every number ever recorded on those two names is
  void, and the horizon grid has to be re-run with five names instead of
  three: 30 walks at about 3.5 minutes each, serialised, is roughly two
  hours of machine time.

## Recommended fix

1. Register a **second** source, `alpaca-sip-split`, with
   `adjustment: "split"` and the same symbols and 2016 start. Keep the
   existing raw source: its note says raw exists so the Schwab live
   overlap compares like with like, and that reason still holds.
   An ADR should record the two-source split. **~1 h**
2. Acquire the backfill from 2016-01-01 into the new tree. **~0.5 h**
   (eight minutes of it is the pull).
3. Re-run the jump scan on the new tree. Expect zero split-sized jumps
   on all six symbols, and check the AAPL and WMT split days bar by bar
   for the reported SIP first-hour defect. **~0.25 h**
4. Point the universe and run configs at the new source and drop the
   `names` filter, restoring AAPL and WMT to five tradable names.
   **~0.75 h**
5. Re-run the horizon grid on five names, then test one shared
   look-ahead against five per-stock ones. **~2 h machine, ~0.5 h to
   read**

About **2.5 hours of hands-on work** plus **2 hours of runs**.

## Sources

- Our own store: `children/intraday_equities/ob/observations/alpaca-sip/20260830T193932Z-backfill-85e59188/bars.jsonl.gz`, its `manifest.json`, and `ob/state/alpaca-sip/bars-backfill.json`
- `configs/source-alpaca-backfill.json`, `configs/universe-jpm-h20.json`, `configs/run-multi3-h01-ridge.json`
- `dskit/onboarding/libs/alpaca.py` — accepted `adjustment` values, `chunk_days`
- Prior finding: `docs/research/unadjusted-stock-splits-in-the-raw-alpaca-tape.md`
- Alpaca corporate actions endpoint — https://docs.alpaca.markets/us/reference/corporateactions-1
- Alpaca `Adjustment` enum (raw/split/dividend/all) — https://alpaca.markets/sdks/python/api_reference/data/enums.html
- Alpaca forum, split adjustment defects and SIP first-hour bug — https://forum.alpaca.markets/t/data-is-not-adjusted-for-splits-despite-adjustment-split-flag/7753
- Split vs dividend adjustment arithmetic — https://help.stockcharts.com/data-and-ticker-symbols/data-availability/price-data-adjustments and https://help.yahoo.com/kb/SLN28256.html
- State Street share splits for XLK/XLY/XLE/XLU/XLB — https://investors.statestreet.com/investor-news-events/press-releases/news-details/2025/State-Street-Investment-Management-Announces-Share-Splits-for-Five-Select-Sector-SPDR-ETFs/default.aspx
- Split histories — https://www.stocksplithistory.com/ (AAPL, WMT, JPM, XOM, XLE, XLK, XLF, XLV, XLP)
