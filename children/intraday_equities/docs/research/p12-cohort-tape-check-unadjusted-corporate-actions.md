# P12 cohort tape check: unadjusted corporate actions in the split-adjusted tapes

## Finding

Every name on disk was scanned for the overnight gap (first RTH close of a
session over the last RTH close of the previous session) across the study
window from 2018-01-01 to the 2026-02-28 cut, on the feature-cache tapes
the study itself reads (P10 cache for the 25, group caches d and e for the
forty). An unadjusted split would show as a −50%, −75% or −90% gap on its
ex-date; an unadjusted spin-off or special dividend as a drop of the
distribution's value. **One name fails: WDC.** Its SanDisk spin-off, separate
trading from 2025-02-24, sits in the tape as a −23.6% overnight gap on that
date — the largest move in WDC's entire window, inside the walk's folds, and
not a market event (the market moved under 1% that day). `adjustment=split`
does not remove it, and no unadjusted `alpaca-sip` tape exists for WDC to
reconcile against (that tree holds the original six names only). **WDC is
excluded from the P12 fit cohort.** It stays in `universe-p12-e.json` and in
the group-E cache, so nothing else moves.

Every other known action is either adjusted or immaterial:

| Name | Event | Gap in tape | Verdict |
|---|---|---|---|
| WDC | SanDisk spin-off 2025-02-24 | −23.6% (its largest in the window) | **exclude** |
| MRK | Organon spin-off 2021-06-03 | −3.7% | keep; smaller than MRK's ordinary earnings gaps (−10.3%, +9.5%, −9.3%); caveat |
| MET | Brighthouse spin-off 2017-08-07 | before the window | keep |
| EOG | seven special dividends 2021–2023 | none above its ordinary gaps | keep; caveat |
| PANW | CyberArk issuance 2026-02-11; splits 2022-09-14, 2024-12-16 | +1.1%; +0.3%, +0.4% | keep; adjusted |
| NOW | 5-for-1 2025-12-18 | −0.02% | keep; adjusted |
| MSTR | 10-for-1 2024-08-08 | +5.6% | keep; adjusted |
| SHOP | 10-for-1 2022-06-29 | −3.1% | keep; adjusted |
| LRCX | 10-for-1 2024-10-03 | −1.6% | keep; adjusted |
| ANET | 4-for-1 2021-11-18, 2024-12-04 | −1.1%, +2.3% | keep; adjusted |
| FTNT | 5-for-1 2022-06-23 | +0.9% | keep; adjusted |
| XLE, XLK | splits 2025-12-19 | +0.5%, +1.0% | keep; adjusted |

The gaps above 25% elsewhere are market days, each on a known date: AMD
+34.3% (2025-10-06), NFLX −29.0% (2022-04-20), TQQQ −29.2% and UPRO −30.9%
(2020-03-16), ORCL +30.7% (2025-09-10), ANET −28.6% (2019-11-01), CIEN
−25.0% (2020-09-03), EOG −32.1% (2020-03-09), INTC +25.9% (2025-09-18) and
−25.6% (2024-08-02), MSTR −26.4% (2024-08-05), PANW −26.0% (2024-02-21).

## Rule applied

A name is excluded only when its tape carries a real-world discontinuity that
the split-only adjustment leaves in place inside the walk's window AND that
discontinuity is the largest overnight move in the name's window, so that it
would be the tape's tail event rather than an ordinary day. WDC meets both
parts; MRK meets only the first. SPY is not in the fit cohort for a design
reason, not a data one: it is the residual reference every group cache
carries, and P11's 2026-09-04 Gate 1 already rejected it at one minute.

## Method

`gap_scan.py` (session kept beside the memo), reading `<symbol>.tape.close.npy`
and `<symbol>.tape.asof_ms.npy` from each cache, grouping by UTC day (an RTH
session never crosses a UTC midnight), reporting each name's three largest
absolute gaps and the gap on every known ex-date.
