# Stock-options source and starting symbol

## TL;DR

Start a separate thin `stock_options` child with AMZN as the baseline and MSFT
as the second-symbol check. We pulled long price histories and 65,372 AMZN daily
option trade bars; historical bid/ask quotes and a strategy backtest remain absent.

## Execution contract

This source and symbol audit ran on 2026-09-30 from branch
`codex/amzn-stock-options`, series base `4b278f17`. Scope was long stock-price history,
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
The retained AMZN and MSFT IEX files hash to
`517919ac74fdd6bab4719036726c9a3032909ddeadc14a92bb3d386a13d9fd8b`
and `374b9bffd2d42421e1f49f43e2d141fd6a3c36c7ee7eb984914cadd628d55a7a`.

The request shapes were Yahoo chart `GET /v8/finance/chart/{symbol}` with
`interval=1d`, `events=div,splits`, AMZN period 1997-05-01 to 2026-10-01 and
MSFT period 2003-03-01 to 2026-10-01. Validation parsed JSON, counted rows,
checked timestamp order/uniqueness and null OHLCV fields, then computed SHA-256.

## Free option evidence

Target-window Alpaca indicative pulls used a 2026-09-30 20:00Z decision time.
The two expiries with snapshots were 30 and 37 DTE: AMZN had 163/168 fresh,
positive, sized quotes, 8.754% median relative spread and 21,590 daily volume;
MSFT had 237/276, 7.138% and 44,839. Both supported four legs in both expiries.
The 44-DTE request returned zero snapshots for either stock; the retained
snapshot artifacts do not establish whether eligible contracts were active.
The AMZN/MSFT files hash to
`473f1e461fe85b33121895304a05d39385b3510e9f07bf76a88d1e6313896129`
and `c2d76d35bea7fa7c5409f8329499f9973b068cf2a9b92aa204fad86446132c0b`.
MSFT has stronger observed target-window liquidity; the upper window remains
unobserved. Modified indicative quotes are not executable fills.

Alpaca's attempted 2016-2026 IEX pull returned 1,553 daily stock bars per symbol
from 2020-07-27 onward; SIP was denied. The free option tier does allow bars
and trades older than 15 minutes. Earlier 403 responses included a recent or
future end time and did not prove that historical option bars were unavailable.

The retained inactive-contract pull contains 23,254 AMZN contracts across 207
expiries, SHA-256
`7dc73b1466d34243b9e72c045223af5f32486acf4369168dcda53d79c3ee23dc`.
The contract endpoint was called with `underlying_symbols=AMZN`,
`status=inactive`, 2024-02-01 through 2026-09-30 and 1,000 rows per page. Bars
used `/v1beta1/options/bars`, at most 100 symbols per request, `timeframe=1Day`,
`start=expiry-45d`, `end=expiry-29d`, `limit=10000`, ascending pagination.
Post-processing retained DTE 30-45, OCC root `AMZN`, and source multiplier 100.
Of 23,254 contracts, 1,386 failed the multiplier gate and two additional `1AMZN`
contracts failed the root gate, leaving 21,866 eligible contracts. The
multiplier gate removed 3,671 bars from 567 multiplier-0 symbols. The final
gzip contains 65,372 unique valid OHLCV bars for 8,892 contracts and 126
expiries, SHA-256
`49d7b4f5da463b8b551c1673fc667e0a8bb3baebb4313e3b6b1ef0e1c52ff9c8`.
It spans 2024-03-21 through 2026-08-26. The response does not identify its feed
and contains no bid/ask quotes, so it is activity/coverage evidence only.

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

## Prediction and option action set

Predict the physical distribution of future AMZN returns from point-in-time
stock and market features. Do not use option strikes, premiums, implied
volatility, open interest or chosen legs as predictors in the first study.
Chronologically divide the adjusted stock history into training and
development before the first scored option date. Every development label must
settle before that date; apply an embargo at least as long as the maximum
45-calendar-day forecast horizon. Freeze features, training-window and recency
rules, model, hyperparameters, and optimizer objective and constraints there.

After the cutoff, option data supply the decision-time action set and economics:
eligible standard contracts, expiries, strikes, quotes or observed trades, size
and costs. The mixed-integer optimizer consumes the frozen predictive
distribution and that contemporaneous feasible set. A predeclared rolling refit
may use only stock outcomes known before each decision; test-period option
outcomes never select the model or optimizer. The retained free trade bars omit
untraded strikes and bid/ask quotes, so they support selection-mechanics and
terminal-payoff diagnostics, not complete-action-set or executable-P&L claims.

Use corporate-action-adjusted prices and scale-free returns or features for
prediction. A correctly adjusted split is not a market loss and is not deleted.
Never pair an adjusted pre-split price level with an unadjusted historical
strike. Exclude a row only for a documented point-in-time data or contract-
identity failure, using a rule frozen before option-period testing. Keep genuine
market shocks; choose any recency window or regime weighting only in the
pre-option development period rather than deleting difficult test observations.

## Starting point

AMZN is the simpler baseline for the clarified free-tier scope. Its accessible
option history begins after the June 2022 split and it has no dividend history,
avoiding the first study's main early-assignment event. MSFT has stronger
observed target-window liquidity and remains the immediate benchmark. Choosing
AMZN prioritizes assignment simplicity over liquidity; it is not evidence of
strategy profitability.

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
- [Yahoo Finance AMZN history](https://finance.yahoo.com/quote/AMZN/history/)
- [ORATS near-end-of-day history](https://orats.com/near-eod-data)
- [ORATS historical API fields](https://orats.com/docs/historical-data-api)
- [Alpaca historical options data](https://docs.alpaca.markets/docs/historical-option-data)
- [Alpaca historical option bars](https://docs.alpaca.markets/reference/optionbars)

## Reproducibility and handoff

ADR-0212 is accepted and the exact initial `stock_options` child now exists.
Generic mechanisms remain upstream. The bootstrap contains no runnable strategy
config; those wait for an approved generic chain/condor seam in `dskit/`.
A point-in-time earnings/ex-dividend source remains a pre-run dependency.
The index_options A0626-A0629 rows retain the historical source and reuse audit
recorded before the separate child existed.
