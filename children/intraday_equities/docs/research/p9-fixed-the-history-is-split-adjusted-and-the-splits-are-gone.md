# P9 fixed: the history is split-adjusted, and the splits are gone

Date: 2026-09-03

## Question

P9 showed AAPL's 2020 4-for-1 and WMT's 2024 3-for-1 sit inside the
history, so both names were excluded from every run. Does asking the
vendor for split-adjusted bars remove them, and does anything else of
that kind survive?

## What was done

A second source, `alpaca-sip-split`, declares `adjustment: "split"` and
twelve symbols. The old raw source is untouched and still on disk, so a
raw-versus-adjusted comparison stays possible. The pull took 13m41s and
peaked at 623 MB of memory. No walk-forward, no training, no pipeline run.

The study's cut is now declared to the connector as `end`, an exclusive
bound, so bars after 2026-02-28 were never requested rather than trimmed
afterwards.

## Before — the raw store, same scan

| symbol | bars | first | last | split-sized jumps | largest daily move |
|---|---|---|---|---|---|
| AAPL | 1,919,230 | 2016-01-01 | 2026-08-28 | **1** | 74.2% (2020-08-31) |
| JPM | 1,233,216 | 2016-01-04 | 2026-08-28 | 0 | 16.9% (2020-03-13) |
| LLY | 1,100,863 | 2016-01-04 | 2026-08-28 | 0 | 15.5% (2023-08-08) |
| SPY | 2,136,508 | 2016-01-01 | 2026-08-28 | 0 | 11.3% (2025-04-09) |
| WMT | 1,210,046 | 2016-01-04 | 2026-08-28 | **1** | 66.1% (2024-02-26) |
| XOM | 1,285,526 | 2016-01-04 | 2026-08-28 | 0 | 11.9% (2020-11-09) |

AAPL 2020-08-28 501.10 to 2020-08-31 129.33, ratio 0.258. WMT 2024-02-24
176.03 to 2024-02-26 59.65, ratio 0.339. Both reproduce P9 exactly, which
is what makes the same scan trustworthy on the new store.

## After — the split-adjusted store

Consecutive daily last prices, scanned for a ratio within ten per cent of
1/4, 1/3, 1/2, 2, 3 or 4.

| symbol | bars | first | last | dates | split-sized jumps | largest daily move |
|---|---|---|---|---|---|---|
| AAPL | 1,817,818 | 2016-01-01 | 2026-02-28 | 2622 | **0** | 16.7% (2025-04-09) |
| JPM | 1,171,053 | 2016-01-04 | 2026-02-28 | 2613 | **0** | 16.9% (2020-03-13) |
| LLY | 1,041,436 | 2016-01-04 | 2026-02-28 | 2602 | **0** | 15.5% (2023-08-08) |
| QQQ | 1,941,758 | 2016-01-01 | 2026-02-28 | 2622 | **0** | 13.6% (2025-04-09) |
| SPY | 2,021,938 | 2016-01-01 | 2026-02-28 | 2623 | **0** | 11.3% (2025-04-09) |
| WMT | 1,139,469 | 2016-01-04 | 2026-02-28 | 2617 | **0** | 11.9% (2025-04-09) |
| XLE | 1,221,802 | 2016-01-01 | 2026-02-28 | 2614 | **0** | 17.5% (2020-03-09) |
| XLF | 1,250,106 | 2016-01-01 | 2026-02-28 | 2613 | **0** | 18.2% (2016-09-19) |
| XLK | 1,096,467 | 2016-01-04 | 2026-02-28 | 2601 | **0** | 15.4% (2025-04-09) |
| XLP | 1,032,353 | 2016-01-04 | 2026-02-28 | 2579 | **0** | 10.1% (2020-03-12) |
| XLV | 1,040,874 | 2016-01-04 | 2026-02-28 | 2583 | **0** | 8.7% (2020-03-16) |
| XOM | 1,216,759 | 2016-01-04 | 2026-02-28 | 2616 | **0** | 11.9% (2020-11-09) |

15,991,833 bars. **No split-sized jump on any symbol.** AAPL's largest
day falls from 74.2% to 16.7% and WMT's from 66.1% to 11.9%; every other
symbol's largest day is unchanged, which is the right answer — adjustment
must not touch a name with nothing to adjust.

No bar is dated later than 2026-02-28. The last one is
2026-02-28T00:59:00Z, the evening of the Friday session, because
2026-02-28 is a Saturday. All three snapshots re-hash against their
manifests.

## The two December 2025 fund splits are corrected

XLE and XLK both split 2-for-1 on 2025-12-05. Checked bar by bar against
the vendor on both scales:

| symbol | scale | 12-04 | 12-05 | ratio |
|---|---|---|---|---|
| XLE | raw | 92.38 | 46.20 | 0.5001 |
| XLE | split | 46.19 | 46.20 | 1.0002 |
| XLK | raw | 291.06 | 146.68 | 0.5040 |
| XLK | split | 145.53 | 146.68 | 1.0079 |

## One thing the fix does NOT cover, and it is XLF

XLF falls 18.2% on 2016-09-19, and the vendor returns the same numbers on
both scales — so this is not corrected and will not be. That date is the
XLRE spin-off, which State Street carried out partly as a 1231-for-1000
share split; 1000/1231 is 0.812 against the 0.818 observed, so most or
all of that fall is a change of unit, not a price move.

It passes the check asked for, because 0.818 is nowhere near 1/2 or 1/3.
It is the same defect as the splits, only smaller — and at 18.2% it is
the same size as JPM's largest genuine day, so no threshold separates it.
It is one day out of 2613, at the very start of the window. Two honest
options: start XLF's usable history at 2016-09-20, or leave it and accept
one corrupt row. That is a modelling call, not a data one, so nothing was
changed.

## What has not been done

Run and universe documents still point at the raw source and still
exclude AAPL and WMT. Repointing them voids every AAPL and WMT number
recorded so far and needs the horizon grid re-run on five names, which is
the next piece of work, not this one.

## Sources

- New store: `ob/observations/alpaca-sip-split/20260903T041646Z-backfill-6cb4e778/bars.jsonl.gz`, 378 MB, and its manifest
- Old store, unchanged: `ob/observations/alpaca-sip/20260830T193932Z-backfill-85e59188/bars.jsonl.gz`
- `configs/source-alpaca-split-backfill.json`, `configs/source-alpaca-backfill.json`
- `dskit/onboarding/libs/alpaca.py` — the `end` bound and the adjustment note
- ADR-0063, `docs/architecture/decision-log.md`
- Prior finding: `docs/research/p9-both-splits-sit-inside-our-window-and-the-re-pull-costs-eight-minutes.md`
