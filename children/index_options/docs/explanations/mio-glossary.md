# MIO run-report glossary

## TL;DR

This page defines every term in an MIO run report in plain words, then walks
one real 2019 book and some invented numbers through the arithmetic. Every
result is retrospective 2019 development evidence with simulated end-of-day
fills, never broker liquidity. Nothing here claims the method is profitable.

## What the report answers

MIO is the weekly option-portfolio study. Each week it picks option orders
with an optimizer, then a replay fills them at the next session's saved quotes,
holds them to expiry and keeps a daily account. The report lists what happened
so a reader can check it, line by line. Terms are defined before they are used;
the code each definition comes from is named at the end.

## Options and prices

- **Ticker**: a stock's short symbol (BLK is BlackRock).
- **Underlying**: the stock an option is written on. Its **settlement price**
  is the underlying's closing price on the expiry date, used to pay the option.
- **Option**: a contract whose payoff depends on the settlement price. The
  replay models it as cash settled (money moves, not shares). Real US
  single-stock options are physically settled; assignment is not modeled.
- **Call / put**: a call pays `max(settlement - strike, 0)` per share; a put
  pays `max(strike - settlement, 0)`. A buyer pays up front for that right.
- **Strike**: the reference price the payoff is measured from.
- **Expiry**: the date the contract ends and settles.
- **DTE**: calendar days from the decision date to expiry (weekends count).
- **Contract**: one option, covering 100 shares (the multiplier), so a price of
  1.65 means $165.00 per contract.
- **Leg**: one option in a position (one right, one strike, bought or sold).
- **Book**: one order for one ticker and one expiry, made of one or more legs.
  A book is accepted or refused as a whole.
- **Quote**: one contract's posted bid, ask and sizes at a time.
- **Bid / ask**: the best price a buyer will pay (bid) and the best a seller
  will accept (ask). You buy at the ask and sell at the bid.
- **Spread**: ask minus bid, the cost of trading right away. Mid is halfway.
- **Displayed size (depth)**: how many contracts the quote shows at the bid
  (`bid_size`) or ask (`ask_size`). A buy is limited by ask size, a sale by bid size.

## Runs, cohorts and methods

A **cohort** is one set of tickers and dates a run covers. `limited36` is 36
sparse ticker-and-DTE cells over 21 tickers; `broad212` is 212 tickers. A
**method** is one fixed optimizer setting replayed on the cohort: `nominal_lp`
has no robustness radius (rho 0); the `w1_003_*` methods add a 0.03 radius, and
`shock10` and `component25` also add a 10% shock-loss limit or a 25% group
limit. Each cohort has four methods. A **trade** is one filled book. **P&L**
is profit and loss in dollars; unless stated it is net of the entry fee.

## What happens to an order

The optimizer decides after a close. The fill is attempted at the next session
using that session's quotes (a decision on Monday fills on Tuesday).

- **Fill**: the order executes at the ask for each bought leg and the bid for
  each sold leg, for the displayed quantity it can get.
- **Partial fill**: with `fill_sizing` set to `available_size`, a book may fill
  fewer contracts than chosen. Write the legs' quantities as `g * u`, where `g`
  is their greatest common divisor and `u` the smallest whole-number ratio. The
  replay fills `k` copies of that ratio, `k = min(g, depth_i // u_i)` over the
  legs, so every leg shrinks together and the ratio never changes. If any leg
  shows less than one ratio unit (`depth_i < u_i`), the whole book is refused.
  Selected and filled quantities are both shown. Single-leg books follow the
  same rule with `u = 1`. Without this setting a book fills in full or not at all.
- **Refusal**: a book that did not fill, with one saved reason code:
  - `missing_fill_quote`: a leg had no usable quote at the fill session: no
    row, a different split basis, a zero bid, a crossed quote (ask below
    bid), a missing or non-integer size, a size below the minimum, or a spread
    wider than the allowed relative spread.
  - `insufficient_fill_size`: a leg's displayed depth was too small (above).
  - `fill_basis_or_session`: the fill day was on or after expiry, or the price
    basis (the split-adjustment factor) differed from the decision's.
  - `repriced_shared_cap`: the books passed the quote and depth checks, but
    repriced at the fill session's saved end-of-day quotes they would exceed
    the account's shared limits (funding, per-name, per-week shares). The
    whole day's batch is refused together; nothing partial survives.
  - `invalid_stressed_fill_price` (stress only): a shocked price was not positive.
  - Before any order exists, a ticker can be skipped with a candidate reason:
    `no_eligible_forecast_expiry`, `insufficient_eligible_legs` or
    `missing spot, scale or basis`. These are listed apart from refusals.
- **Fee**: a per-contract charge at entry (0.65 dollars in the 2019 documents).
  Expiry carries none.
- **Premium**: the cash for the legs alone. **Credit** is what the account
  receives after fees; **debit** is a negative credit. Buying costs premium,
  so a long book has negative credit; selling gives positive credit.
- **Reserve**: cash set aside for an open book so its worst-case loss is
  covered. It is the largest of the book's maximum loss, its debit, and the
  **capital-charge floor**, a minimum the method sets (here 20% of the strike
  value, `strike * 100`, per sold contract). Headroom for new books is
  funding (defined under The account) minus reserves.
- **Settlement**: at expiry the legs' payoffs are paid out in cash at that
  day's underlying close. **Realized P&L** of a book is its credit plus
  `100 * payoff` summed over its legs. A bought leg's payoff is zero or
  positive, a sold leg's zero or negative. For a long call book bought at a
  net debit, an expired-worthless result equals the debit lost.

## The account

- **Contributions / deposits**: money the owner adds. The 2019 runs start with
  $10,000 and add $1,000 every 14 days. A deposit is not profit.
- **Funding**: contributions plus realized P&L, leaving out open books'
  premiums. **Cash** (the daily ledger's `cash`): funding plus the entry
  credits of open books, a debit counting negative.
- **Marked equity**: funding plus a mark on each open book (its entry credit
  plus what closing it at the day's quote mid would net). When a quote is
  missing the last mark is carried forward and flagged.
- **NAV**: a unit price that starts at 1.0 and each day multiplies by
  `equity / (previous equity + deposit)`. Deposits do not move it.
- **TWR** (time-weighted return): final NAV minus 1. Because it weights each
  period by its own return, not its dollars, it can disagree in sign with
  profit (`final equity - contributions`); see the teaching numbers.
- **Drawdown**: how far NAV sits below its previous peak, as a fraction
  (negative). Max drawdown is the worst one.
- **Which basis a number uses.** The MIO result (`summary.csv`, `result.json`,
  the daily ledger curve) is MARKED: its TWR, drawdown and equity use daily
  mids, as defined above. The report's own event-based statistics (TWR,
  drawdown, Sharpe from `dskit.evaluation`) value open books AT COST between
  fills (see Report mechanics), so they differ from the marked figures. Per
  ADR-0283 the report headline is built from events; the marked figures are
  the MIO result's and stay in the saved-figures and ledger tables. The report
  carries a basis banner, and its at-cost statistics (TWR, drawdown, Sharpe,
  exposure) are labelled "(at cost)".

## Performance statistics

All are descriptive, over the filled books (trades) unless said otherwise.

- **Hit rate**: share of trades with realized P&L above zero.
- **Profit factor**: total of the winning trades divided by the absolute total
  of the losing ones. Undefined without a loss. Read it with the trade count.
- **ES95 / CVaR5**: the average of the worst 5% of trades (`ceil(0.05 n)`
  trades, at least one). More negative is worse.
- **Sharpe**: mean divided by standard deviation of daily returns, not
  annualised. It says nothing about profit unless the sample is large.

## Optimizer and forecasts

- **Optimizer objective**: the score of the chosen basket in dollars: credit
  minus the worst-case expected terminal loss under the model's own forecast.
  It is a score, not a result: the BLK book below scored an expected
  +$2,188.86 and lost $938.90.
  **Bound**: the best score the solver proves is possible. **Gap**: how far
  the objective sits under the bound (a ratio in the certificate; the saved
  CSV also gives USD). A solve is accepted when its audited absolute gap is at
  most $50. This is an optimization certificate, not a forecast error.
- **Timeout**: the solver hit its time limit. The week's status is
  `timeout_skipped` and no new orders are placed that week.
- **Decision status** (the pinned 2019 methods): `selected` (orders chosen),
  `cash` (solved, chose nothing), `no_eligible_candidates`, `timeout_skipped`,
  `no_entry_fill_session` and `existing_cap_breach_cash` (no headroom left).
  `shock_risk_input` and `shock_held_breach` exist only with the opt-in shock
  gate, which these methods do not use.
- **Fitted vs fallback forecast**: a forecast is fitted when the method learned
  from earlier rows; otherwise it is the fallback. Reports count both per decision.

## Stress and control

A stress scenario replays the same saved selections with one change and asks
what would have happened. The replay never re-optimizes.

- **Control (Gate 0)**: the replay with no change. It must reproduce the
  original run exactly (results and CSV rows); any mismatch stops the run. The
  report shows Gate 0 before the grid; the eight controls are four methods in
  each of two cohorts (`limited36`, `broad212`).
- **Unsupported case**: a stressed replay that cannot continue, for example
  equity turning non-positive, a settlement loss beyond its reserve, or books
  left unsettled. The report names the date and reason and gives no final profit.
- **The seven cases**: `control`; `adverse_price` (buys fill at ask plus 25% of
  the spread, sales at bid minus 25%); `half_size` (displayed depth cut to half,
  rounded down); `expiry_down_15` and `expiry_up_15` (the underlying at expiry
  multiplied by 0.85 or 1.15); `joint_down_15` and `joint_up_15` (all three
  changes together). With available-size sizing, depth shortfalls partly fill
  instead of refusing outright.

## Report mechanics

- **Event equity between fills is at cost.** The report's event view values an
  open book at what it cost on entry and moves it only at its closing fill, so
  it shows no between-fill gains or losses. The **marked daily curve** (in the
  ledger table) uses the daily mid marks above. Both end at the same equity.
- **Exact-match gate**: before drawing anything, the report recomputes net P&L
  and final equity from its events and requires each to equal the run's
  `result.json` to the cent (difference under $0.005). If not, it refuses and
  writes no report.

## Worked example: a real 2019 book

Actual 2019 development evidence, limited36 cohort, `nominal_lp` method, from
`limited36/trades.csv`. Simulated end-of-day fill, not broker liquidity.

The book `2019-01-07:BLK:2019-02-08` was decided at the 7 Jan 2019 close, filled
8 Jan and expires 8 Feb: DTE = 24 days left in January + 8 = 32. Two bought calls:

1. Leg A: 5 contracts, strike 450, filled at the ask 1.65.
2. Leg B: 1 contract, strike 455, filled at the ask 1.10.
3. Leg A cash = 5 * 1.65 * 100 = $825.00.
4. Leg B cash = 1 * 1.10 * 100 = $110.00.
5. Premium = 825.00 + 110.00 = $935.00 paid, saved as -935.00.
6. Fee = 6 contracts * 0.65 = $3.90.
7. Credit = -935.00 - 3.90 = -$938.90 (a debit; the most the book can lose).
8. Reserve = $938.90.
9. At expiry the saved realized P&L is -938.90. Payoff = P&L - credit =
   -938.90 - (-938.90) = $0, so both calls expired worthless.

The ratio 5:1 has `g = 1`, so this book cannot shrink; it fills fully or
is refused. The same cohort and method's marked account result
(`summary.csv`): contributions $35,000, final equity $35,259.05, so profit =
35,259.05 - 35,000 = $259.05. Its marked TWR is -0.5877 and marked max
drawdown -0.8555: a positive profit with a negative TWR. The report's
at-cost event statistics differ from these marked figures.
That is a single descriptive 2019 result, not evidence the method works.

## Teaching numbers (invented)

**Partial fill.** Chosen: 10 calls at strike 450 and 2 at 455. Shown ask sizes:
7 and 1.

1. `g = gcd(10, 2) = 2`, so `u = (5, 1)`.
2. Each leg needs one unit: 7 >= 5 and 1 >= 1, so the book is not refused.
3. `k = min(2, 7 // 5, 1 // 1) = min(2, 1, 1) = 1`.
4. Filled: 5 and 1 contracts, half the chosen amount, ratio kept.
   Fee = 6 * 0.65 = $3.90. If the first leg showed 4, `4 < 5`: refused
   `insufficient_fill_size`.

**TWR against profit.** Start with $1,000 and no deposits.

1. Equity falls to $500: return = 500 / 1,000 - 1 = -50%, NAV = 0.50.
2. Deposit $1,000. The next base is 500 + 1,000 = $1,500.
3. Equity rises to $2,100: ratio = 2,100 / 1,500 = 1.40, NAV = 0.50 * 1.40 = 0.70.
4. TWR = 0.70 - 1 = -30%. Contributions = $2,000, so profit = 2,100 - 2,000 = +$100.

**Trade statistics.** Five trades: +300, +100, -50, -200, -400.

1. Hit rate = 2 winners / 5 = 0.40.
2. Wins = 400, losses = 650, so profit factor = 400 / 650 = 0.62.
3. ES95 tail = `ceil(0.05 * 5) = 1` trade, so the worst: -400.

## Limits

Prices and depth are saved end-of-day quotes, a proxy, not what a broker would
fill. Margin, assignment and financing are not modeled. 2019 is development
only; no row here supports a claim that the method makes money.

## Where each definition lives

`index_options/ledger_studies.py`: `WeeklyOptionPortfolioStudy._fill`,
`_prepare`, `_limits`, the decision statuses and certificates.
`index_options/mio_execution_stress.py`: the seven scenarios, control, unsupported
metrics and the metrics dict. `dskit/evaluation/statistics.py`: hit rate, profit
factor, ES95. Run units: `column-guide.json` beside the run output. The report
contract is `run_report` in [study.json](../plans/mio-json/study.json) and
ADR-0283 in the [decision log](../../../../docs/architecture/decision-log.md).
