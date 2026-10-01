# Stock-options source and starting symbol

## TL;DR

Start a separate thin `stock_options` child with AMZN as the baseline and MSFT
as the second-symbol check. We pulled long price histories and 69,043 AMZN daily
option trade bars; historical bid/ask quotes and a strategy backtest remain absent.

## Execution contract

This source and symbol audit ran on 2026-09-30 from branch
`codex/amzn-stock-options`, rebased base `c001450d`. Scope was long stock-price history,
free option coverage, a starting stock, and explicit stock-specific caveats.
No new child, strategy run, paper/live order or paid-data purchase was approved.

## Price evidence

`/home/russell/data/stock_options/long_history_audit/AMZN-yahoo-chart-1997-2026.json`
contains 7,390 daily records from 1997-05-15 through 2026-09-30. Returned dates
are ordered and unique, and OHLCV fields are non-null. SHA-256 is
`982acac92f17e808bdca33d4361069cd98646bfd272b06fb4b735cadef26ff66`.
The series is split-adjusted and reports four splits. It is appropriate for
return/regime features; pre-2022 values cannot be paired with raw option strikes.

`/home/russell/data/stock_options/long_history_audit/MSFT-yahoo-chart-2003-2026.json`
contains 5,934 daily records from 2003-03-03 through 2026-09-30, with the same
field checks. SHA-256 is
`bcbcc4ccc491a1db21580a0a4cfc20b765b6c67c32ccd34a5a07fd94b74bfc8f`.
The final day in both files may be provisional. Both files contain every one of
the 5,030 XNYS sessions from 2006-10-02 through 2026-09-30, with valid OHLCV.
The calendar package did not cover earlier dates. Across 1,553 overlapping IEX
sessions, adjusted closes were within 1%: median absolute relative difference
was 0.0199% for AMZN and 0.0208% for MSFT, with maxima 0.925% and 0.363%.
IEX is a single-exchange sanity check, not official-close reconciliation.

The request shapes were Yahoo chart `GET /v8/finance/chart/{symbol}` with
`interval=1d`, `events=div,splits`, AMZN period 1997-05-01 to 2026-10-01 and
MSFT period 2003-03-01 to 2026-10-01. Validation parsed JSON, counted rows,
checked timestamp order/uniqueness and null OHLCV fields, then computed SHA-256.

## Free option evidence

Comparable Alpaca indicative pulls for 2026-10-02 through 2026-11-06 returned
972 AMZN snapshots in one page and 1,413 MSFT snapshots in two pages. SHA-256
values are `c154653859d05cebd3c7fac4a86cf6b1cd666e82b8029a495296c2ccae0ab115`
and `cc643411436e9b9d6f6e1cba78eb1355f4887745dcf9e5eba9ed5197ddbd715d`.
Both had fresh, positive, sized quotes sufficient for a four-leg candidate in
all 10 expiries. AMZN's median relative spread was 10.87% versus MSFT's 11.05%,
and daily option volume summed to 414,505 versus 238,983. These modified
indicative quotes show current source suitability, not executable fills.

Alpaca's attempted 2016-2026 IEX pull returned 1,553 daily stock bars per symbol
from 2020-07-27 onward; SIP was denied. The free option tier does allow bars
and trades older than 15 minutes. Earlier 403 responses included a recent or
future end time and did not prove that historical option bars were unavailable.

The retained inactive-contract pull contains 23,254 AMZN contracts across 207
expiries, SHA-256
`7dc73b1466d34243b9e72c045223af5f32486acf4369168dcda53d79c3ee23dc`.
Two nonstandard `1AMZN` deliverables were excluded. The final gzip contains
69,043 unique daily OHLCV trade bars for 9,459 standard contracts and 136
expiries, all exactly 30-45 DTE with valid OHLCV and no duplicate symbol/time
keys. Its SHA-256 is
`66a19de363de7c3ea8ef1b5ea524d5d85c3e446434d16e50df2222316dce9651`.
It spans trade dates 2024-01-18 through 2026-08-27 and has no bid/ask quotes.

## Source audit

The public ORATS 2024-01-03 near-close sample is stored at
`/home/russell/data/stock_options/source_audit/ORATS_SMV_Strikes_20240103.zip`,
SHA-256 `e04a37310b0f453fedc03a000c55adfc517a119279150173916ebdc5fb514570`.
It contains 1,449 MSFT and 883 AMZN rows. In the proposed 30-45 DTE window,
178/205 MSFT rows (86.83%) and 113/120 AMZN rows (94.17%) had positive,
ordered call and put bid/ask quotes. MSFT had greater breadth, while AMZN had
the higher valid fraction. One date cannot establish historical liquidity.
The bulk sample omits quote sizes required by the current backtester, although
the provider's historical API documents them.

The free option source supports current indicative chains and historical trade
bars since February 2024. It cannot estimate bid/ask-side iron-condor fills,
returns or risk because historical quotes are unavailable.

## Starting point

AMZN is the better baseline for the clarified free-tier scope. Its accessible
option history begins after the June 2022 split, it has no dividend history,
and the comparable current chain has enough fresh breadth with slightly tighter
relative spreads and greater daily volume. MSFT remains a useful comparator
with more listed rows, but its quarterly dividends add an assignment dependency.
These are data-operability reasons, not evidence of strategy profitability.

The proposed first diagnostic is a nonoverlapping, defined-risk iron condor,
reviewed weekly with 30-45 days to expiry. A run must refuse earnings and
ex-dividend windows until point-in-time calendars, their known-at rules, early
assignment and share accounting are approved. Freeze one decision timestamp;
selected leg quotes must be no more than 15 minutes old and the underlying must
be timestamped within 60 seconds. Until then it is a terminal
payoff design only and cannot report realized strategy performance.

## Failures and limits

No historical option quote series was pulled. Stooq returned a browser check;
Alpaca SIP was denied; ORATS supplied one public sample date. No strategy ran.
No profitability, fill quality, assignment, pre-2006 session completeness,
licensing, or future provider-availability claim is supported.

## Sources

- [Amazon split history](https://ir.aboutamazon.com/faqs/)
- [Microsoft split history](https://www.microsoft.com/en-us/investor/faq)
- [ORATS near-end-of-day history](https://orats.com/near-eod-data)
- [ORATS historical API fields](https://orats.com/docs/historical-data-api)
- [Alpaca historical options data](https://docs.alpaca.markets/docs/historical-option-data)

## Reproducibility and handoff

ADR-0211 now proposes the exact initial `stock_options` child structure and
keeps generic mechanisms upstream. Approval is required before creating it.
A point-in-time earnings/ex-dividend source remains a pre-run dependency.
