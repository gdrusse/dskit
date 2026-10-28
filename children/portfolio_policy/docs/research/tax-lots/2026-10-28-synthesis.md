# Tax lots from Schwab transactions: FIFO reconstruction and holding periods

## Question

The liquidation tax is computed on aggregate positions (average price x shares,
every holding `default_holding`), because Schwab's positions endpoint carries no
lots. The real tax depends on each purchase's date (short vs long term) and basis.
(a) What does Schwab's Trader API transactions endpoint return, and with what
limits? (b) Which lot-matching rule applies when no election is known? (c) How is
a lot's holding period counted for long-term treatment?

## Finding

**(a) Endpoint.** `GET /trader/v1/accounts/{accountHash}/transactions` with
`startDate`, `endDate` (ISO-8601 instants, `yyyy-MM-dd'T'HH:mm:ss.SSSZ`), `types`
(required; one of TRADE, RECEIVE_AND_DELIVER, DIVIDEND_OR_INTEREST,
ACH_RECEIPT, ACH_DISBURSEMENT, CASH_RECEIPT, CASH_DISBURSEMENT,
ELECTRONIC_FUND, WIRE_OUT, WIRE_IN, JOURNAL, MEMORANDUM, MARGIN_CALL,
MONEY_MARKET, SMA_ADJUSTMENT) and optional `symbol`. The path takes the account
HASH (`hashValue` from `accountNumbers`), never the number. Limits: at most one
year per request and at most 3,000 transactions per response (Schwabdev docs);
how far BACK history goes is not settled: one client documents "will not pull
back transactions older than 1 year" (schwabr, ported from TD Ameritrade),
another a 60-day start (schwab-py, likely copied from the TDA orders call). The
body is a list of transactions: `activityId`, `time`, `type` (the TRADE the
filter matches; `activityType` is EXECUTION / TRANSFER / ...), `status`,
`tradeDate`, `settlementDate`, `netAmount` (signed net cash, fees included),
`transferItems[]`. Each item: `instrument` (`assetType`, `symbol`, `type`, ...),
`amount` (shares, signed), `cost`, `price`, `feeType` (fee lines: COMMISSION,
SEC_FEE, TAF_FEE, ...) and `positionEffect` (OPENING / CLOSING / AUTOMATIC /
UNKNOWN). ETFs are typed `COLLECTIVE_INVESTMENT` (type EXCHANGE_TRADED_FUND) in
the transaction instrument schema, not EQUITY. **Confidence:** field names and the
type vocabulary are consistent across three independent SDKs (Rust schwab-sdk,
Rust schwab models, Ruby schwab_rb); sign conventions are not documented anywhere
public (one SDK's fixture shows `cost` +1500 against `netAmount` -1500 for a buy),
so the build reads quantity from `amount`, cash from `netAmount`, and refuses by
name any shape it does not recognise. The real shape is confirmed only by the
first real pull.

**(b) Lot matching.** The default rule is FIFO: stock sold without adequate
identification "is charged against the earliest lot the taxpayer purchased or
acquired" (26 CFR 1.1012-1(c)(1)(i)); specific identification through the broker
(1.1012-1(c)(3), a standing order 1.1012-1(c)(8)) overrides it. Schwab's default
is FIFO for stocks; for RICs (mutual funds and CERTAIN ETFs) the default was
AVERAGE COST in accounts opened before October 2024 (FIFO after). Schwab also
offers LIFO, low-cost, high-cost, Tax Lot Optimizer and specified lot. Under
average cost the shares sold are "deemed to be the shares first acquired"
(1.1012-1(e)(7)(ii)), so the holding-period split stays FIFO while the basis per
share is the average. So: FIFO is the right first method, the lot method is an
owner declaration per account, and an ETF such as VTI in an older account may be
on average cost in Schwab's books.

**(c) Holding period.** Counting begins the day after the trade date and includes
the sale's trade date; long term means held MORE than one year: bought
2007-02-05 and sold 2008-02-05 is short, sold 2008-02-06 long (IRS Pub. 550). As a
rule: long iff `sale date > acquisition date + 1 calendar year`. 366 days held
equals that except when the year spans 29 February (then 367 are needed); the
document's `long_term_days` 366 misclassifies a lot only on that one day.

**Build recommendation.** A tier-2 `schwab_transactions` pack (one row per kept
transfer item, windows of at most a year from a declared `start`, a refusal at the
3,000 cap, non-kept and fee lines counted); a tier-1 `TaxLots` (trades -> open
lots by a declared method, FIFO now, a sell beyond the open quantity refuses or,
when the document allows, flags the key `history_incomplete`) and `LotPositions`
(lots joined to the positions snapshot, quantity reconciled, value pro rata; an
unreconciled key refuses or falls back to its aggregate row). Every account's
history should reach back to its earliest open purchase; where the API cannot,
Schwab's own lot export (Unrealized Gain/Loss, lot details) onboarded through
`localfiles` feeds the same `TaxLots` by field name.

## Sources

- [schwab-sdk 0.4.0 transactions.rs (docs.rs)](https://docs.rs/crate/schwab-sdk/0.4.0/source/src/transactions.rs)
- [schwab 0.3.0 models/trader.rs (docs.rs)](https://docs.rs/crate/schwab/0.3.0/source/src/models/trader.rs)
- [schwab_rb TransferItem (rubydoc)](https://www.rubydoc.info/gems/schwab_rb/SchwabRb/DataObjects/TransferItem)
- [Schwabdev Account and Trading (market-quant gitbook)](https://market-quant.gitbook.io/home/api-reference/data-providers/charles-schwab-api/account-and-trading)
- [charlesschwabapi get_transactions.R](https://rdrr.io/cran/charlesschwabapi/src/R/get_transactions.R)
- [schwabr schwab_transactSearch](https://rdrr.io/cran/schwabr/man/schwab_transactSearch.html)
- [schwab-py client: get_transactions](https://schwab-py.readthedocs.io/en/latest/client.html#schwab.client.Client.get_transactions)
- [Dashtam Schwab transactions API reference](https://faiyaz7283.github.io/dashtam-api/reference/infrastructure/providers/schwab/api/transactions_api)
- [Schwab: Save on Taxes: Know Your Cost Basis](https://www.schwab.com/learn/story/save-on-taxes-know-your-cost-basis)
- [26 CFR 1.1012-1 (Cornell LII)](https://www.law.cornell.edu/cfr/text/26/1.1012-1)
- [IRS Publication 550, ch. 4, Holding Period (UNT web archive)](https://webarchive.library.unt.edu/eot2008/20090506054259/http:/www.irs.gov/publications/p550/ch04.html)
