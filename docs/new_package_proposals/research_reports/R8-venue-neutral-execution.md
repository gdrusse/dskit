# R8 — The smallest venue-neutral execution interface

Research for `dskit.production` (working name). Question: which verbs and
record shapes are COMMON across broker APIs, prediction-market APIs and
betting exchanges, and what is venue-specific residue that must stay in the
child. Eight venues were read from primary docs: Alpaca, Interactive Brokers
(TWS API; the Web API reference returned 403, so Web-API endpoint names come
from IBKR release notes), Kalshi (Trade API v2), Polymarket (CLOB + Data
API), Betfair (Exchange API-NG + stream), Coinbase Advanced Trade, Binance
Spot, and Pinnacle (a sportsbook, not an exchange). Four simulators were read
for the paper model: Backtrader, NautilusTrader, LEAN, flumine. FIX 4.4 is
used as the industry anchor for closed vocabularies.

---

## 1. Findings by theme

### 1.1 Verb surface — what every venue exposes

Table A: verbs and identity.

| Venue | Submit | Cancel | Replace / amend | Status + open orders | Fills history | Positions | Balances | Settlement | Client ref |
|---|---|---|---|---|---|---|---|---|---|
| Alpaca | POST /v2/orders (`qty` XOR `notional`) | DELETE → 204 accepted, 422 not cancelable; cancel-all = 207 per-order | PATCH → **new order id**, old → `replaced`; refused while `accepted/pending_new/pending_cancel/pending_replace`; notional orders not replaceable | GET order by id or client id; statuses = FIX OrdStatus names | activities `FILL` (`type` fill/partial_fill, `leaves_qty`, `cum_qty`, `order_status`) | `qty` + `side` long/short, `avg_entry_price`, `qty_available` | account `cash`, `buying_power`, `equity`, `accrued_fees` | none (mark-to-market; corporate actions as activities) | `client_order_id` ≤128, auto-generated if omitted |
| IBKR (TWS) | `placeOrder(orderId, contract, order)`; client-assigned integer `orderId` from `nextValidId`, `permId` from IB, `orderRef` free text | `cancelOrder(orderId)`; `reqGlobalCancel` cancels ALL incl. non-API orders | re-`placeOrder` with the same `orderId` (id kept); "not recommended" beyond price/size/tif → cancel+new | `orderStatus` callback (`filled`, `remaining`, `avgFillPrice`, `whyHeld`); "duplicate orderStatus messages" are normal | `execDetails` (`execId`, `cumQty`, `avgPrice`, `lastLiquidity`) + separate `commissionReport` keyed by `execId` | `position(account, contract, pos, avgCost)` | `accountSummary` tags (`NetLiquidation`, `TotalCashValue`, `BuyingPower`, …) | none | `orderId` per client + `permId`; Web API adds `cOID` |
| Kalshi v2 | POST create: `side` bid/ask on the YES leg, `count` fixed-point contracts, `price` dollars, `time_in_force`, `post_only`, `reduce_only`; response `fill_count`, `remaining_count`, `average_fill_price`, `average_fee_paid`, `ts_ms` | DELETE → synchronous `reduced_by`, `ts_ms`; batch cancel = per-item result + `error` | amend keeps `order_id`; queue position kept **only when decreasing size**; `decrease` (`reduce_by`/`reduce_to`) | GET order/orders; `status` ∈ {resting, canceled, executed} only | GET fills: `fill_id`, `count_fp`, `yes_price_dollars`, `is_taker`, `fee_cost` | `position_fp` signed (+YES / −NO) per market; event exposure | balance in **cents** int + `balance_dollars` string, `portfolio_value` | GET settlements: `market_result` yes/no/scalar, `revenue` (cents), `fee_cost` | `client_order_id` — duplicates **rejected** |
| Polymarket | POST /order = EIP-712 signed order (`makerAmount`/`takerAmount` 6-dec ints, `salt`); `orderType` GTC/GTD/FOK/FAK, `postOnly`; response `status` live/matched/delayed/unmatched | DELETE /order, /orders (≤3000), /cancel-market-orders, /cancel-all → `canceled` + `not_canceled{id: reason}` | **none** — "cannot be modified; cancel and replace" | GET /data/order, /data/orders; `ORDER_STATUS_LIVE/MATCHED/CANCELED/CANCELED_MARKET_RESOLVED/INVALID` | trades with their own lifecycle MATCHED→MINED→CONFIRMED / RETRYING / FAILED | Data API positions per **token** (YES and NO are separate assets): `size`, `avgPrice`, `curPrice`, `cashPnl`, `redeemable` | not documented in the index read (collateral = USDC balance/allowance) | winning token redeems for $1 via CTF `redeem` — settlement is an **action** | **none** (salt prevents replay; id is a hash) |
| Betfair | placeOrders(marketId, instructions[]): `side` BACK/LAY, `limitOrder{size, price, persistenceType, timeInForce, minFillSize, betTargetType}`; report `betId`, `sizeMatched`, `averagePriceMatched`, `orderStatus` | cancelOrders with optional `sizeReduction`; no marketId+betId ⇒ cancels ALL | replaceOrders = "bulk cancel followed by bulk place"; updateOrders changes only `persistenceType` | listCurrentOrders (`orderProjection` ALL/EXECUTABLE/EXECUTION_COMPLETE): `sizeMatched/Remaining/Cancelled/Lapsed/Voided`, `averagePriceMatched` | matched portions via listCurrentOrders / stream `mb`/`ml` ladders | no position object — matched backs/lays per runner | account funds (not read) | listClearedOrders: `betOutcome`, `profit`, `commission`, `settledDate`; `betStatus` SETTLED/VOIDED/LAPSED/CANCELLED | `customerRef` (request de-dupe, 60 s window) + `customerOrderRef` ≤32 |
| Coinbase Adv. | create: `client_order_id` + `order_configuration` variant (`market_market_ioc{quote_size|base_size}`, `limit_limit_gtc/gtd/fok`, stop-limit, bracket, TWAP…) | batch_cancel → per-order `success` + `failure_reason` (`ORDER_IS_FULLY_FILLED`, `DUPLICATE_CANCEL_REQUEST`, …) | edit keeps id; "only limit order edits supported"; status `EDIT_QUEUED` | list/get orders; `status` PENDING/OPEN/FILLED/CANCELLED/EXPIRED/FAILED/UNKNOWN_ORDER_STATUS/QUEUED/CANCEL_QUEUED/EDIT_QUEUED; `completion_percentage`, `average_filled_price`, `total_fees` | list fills: `trade_type` FILL/REVERSAL/CORRECTION/SYNTHETIC, `liquidity_indicator` MAKER/TAKER, `commission` | spot = account balances; futures positions | accounts | none | `client_order_id` — duplicate **returns the existing order** |
| Binance Spot | POST /api/v3/order: `quantity` XOR `quoteOrderQty`, `newClientOrderId`, `newOrderRespType` ACK/RESULT/FULL (FULL embeds `fills[]`) | DELETE by `orderId` or `origClientOrderId`; `cancelRestrictions` ONLY_NEW/ONLY_PARTIALLY_FILLED | cancelReplace with `STOP_ON_FAILURE`/`ALLOW_FAILURE`; results SUCCESS/FAILURE/NOT_ATTEMPTED | GET order (by either id), openOrders, allOrders; `executedQty`, `cummulativeQuoteQty` | myTrades: `commission`, `commissionAsset` (fee asset may differ), `isMaker` | balances only (`free`, `locked`) | account `balances[]` | none | `newClientOrderId` auto-generated |
| Pinnacle (book) | place straight bet: `stake`, `winRiskStake` WIN/RISK, `oddsFormat`, `lineId`, `acceptBetterLine`, `fillType` NORMAL/FILLANDKILL/FILLMAXLIMIT; response `status` ACCEPTED/PENDING_ACCEPTANCE/PROCESSED_WITH_ERROR + `errorCode` (LINE_CHANGED, INSUFFICIENT_FUNDS, DUPLICATED_REQUEST…) | none (a bet is immediate) | none | GET /bets `betlist` SETTLED/RUNNING/ALL, `betStatuses`; live bets get `betId` only once ACCEPTED (~6 s delay) | the bet IS the fill | open bets = RUNNING list | balance (not read) | `betStatus` WON/LOST/REFUNDED/CANCELLED via SETTLED list | `uniqueRequestId` — must be reused on retry; kept 30 min |

Table B: semantics.

| Venue | Order types | Time in force | Quantity / price units | Fees reported | Streaming |
|---|---|---|---|---|---|
| Alpaca | market, limit, stop, stop_limit, trailing_stop; classes bracket/oco/oto/mleg | day, gtc, opg, cls, ioc, fok | shares (fractional) or USD notional; USD price | not per fill in the FILL activity; `accrued_fees` on account | `trade_updates`: `event` (new, fill, partial_fill, canceled, expired, replaced, pending_*, order_cancel_rejected…) + full `order` |
| IBKR | LMT, MKT, STP, STP LMT, TRAIL, MIDPRICE… | DAY, GTC, IOC, OPG… | shares/contracts decimal `pos`; `cashQty` alt | `commissionReport(execId, commission, currency, realizedPNL)` — separate message | callbacks; duplicates expected |
| Kalshi | limit (v2: price + tif; `post_only`) | fill_or_kill, good_till_canceled (+`expiration_time`), immediate_or_cancel | contracts (2-dec fixed point); price = dollars 0.01–0.99 of the YES leg; NO price = 1 − YES | per fill `fee_cost`; per order `taker_fees_dollars`/`maker_fees_dollars`; rounded up to $0.000001 per fill | WS `fill`, `user_orders`, `market_positions`; envelope has `seq` "checked if you want to guarantee you received all the messages"; ping every 10 s |
| Polymarket | limit only (a "market" order is a marketable limit sized in USD for BUY, shares for SELL) | GTC, GTD (≥3 min ahead, **expires 1 min early**), FOK, FAK | shares; price 0–1 at market `tick_size`; amounts 6-dec fixed | takers only: `fee = C × feeRate × p × (1 − p)` in USDC; makers rebated | user WS: `order` PLACEMENT/UPDATE/CANCELLATION, `trade` statuses; **no sequence number**; "fetch open orders and recent trades after reconnecting" |
| Betfair | LIMIT, LIMIT_ON_CLOSE, MARKET_ON_CLOSE (SP) | persistence LAPSE/PERSIST/MARKET_ON_CLOSE; `FILL_OR_KILL` (+`minFillSize`) | `size` = backer's stake in account currency; `price` = decimal odds on a ladder; LAY liability = size × (price − 1); alt `betTargetType` PAYOUT/BACKERS_PROFIT | `commission` on `listClearedOrders` at settlement, not per match | order stream: `uo` unmatched orders (`sm`, `sr`, `sc`, `sl`, `sv`, `avp`, `status` E/EC), `mb`/`ml` matched ladders, `fullImage` |
| Coinbase | market, limit, stop_limit, bracket, TWAP, scaled | GTC, GTD, IOC, FOK (encoded in the configuration key) | `base_size` or `quote_size`; `size_in_quote` flag | per fill `commission`; order `total_fees` | user channel: `sequence_num` "to detect dropped or out-of-order messages"; `snapshot` then `update` |
| Binance | LIMIT, MARKET, STOP_LOSS(_LIMIT), TAKE_PROFIT(_LIMIT), LIMIT_MAKER | GTC, IOC, FOK | base `quantity` or `quoteOrderQty` | per fill `commission` + `commissionAsset` | `executionReport`: `x` execution type (NEW/CANCELED/REPLACED/REJECTED/TRADE/EXPIRED/TRADE_PREVENTION) + `X` order status |
| Pinnacle | a priced line; `acceptBetterLine` | immediate; `fillType` FILLANDKILL ≈ IOC, FILLMAXLIMIT ≈ IOC up to max | `stake` currency (as WIN or RISK) at `price` in a chosen odds format | none explicit (margin in odds) | none — poll `/bets` |

### 1.2 Intersection vs residue

Every venue has: **submit a priced buy/sell of one instrument for a
quantity**, **cancel** (except a sportsbook, where nothing rests),
**read one order / open orders**, **read fills** (or the bet itself),
**read balances**, and some form of **"immediate" and "resting"** lifetime.
Every venue returns **a venue reference plus status plus filled quantity and
average price**. Seven of eight accept a **client reference**; only
Polymarket has none.

Not universal (residue): market orders (Polymarket, Betfair lack them; Pinnacle
only has them), stop/trailing/bracket/auction/SP types, replace/amend (four
different semantics: new id, same id, same id but queue lost, none), partial
cancel (`sizeReduction`, `decrease`), post-only, self-trade prevention,
notional sizing, persistence at in-play, `acceptBetterLine`, batch endpoints,
positions as an object (Betfair and spot crypto have none), settlement as a
read (Kalshi/Betfair/Pinnacle) vs an **action** (Polymarket redeem) vs absent
(equities/crypto), fee timing (per fill vs at settlement), and streaming.

### 1.3 Status vocabulary — reconciliation

FIX 4.4 OrdStatus (0 New … E Pending Replace) is the superset most brokers
speak (Alpaca uses the names verbatim). The prediction/betting venues use 3–5
states. The closed neutral set that every venue maps INTO without inventing
progress:

| Neutral | Alpaca | IBKR | Kalshi | Polymarket | Betfair | Coinbase | Binance | Pinnacle |
|---|---|---|---|---|---|---|---|---|
| `pending` (sent, not acknowledged) | accepted, pending_new | ApiPending, PendingSubmit | — | delayed (matching-delay window) | PENDING (async) | PENDING, QUEUED | — | PENDING_ACCEPTANCE |
| `open` (working, nothing filled) | new | PreSubmitted, Submitted | resting, fill_count 0 | live, unmatched | EXECUTABLE, sizeMatched 0 | OPEN | NEW | ACCEPTED (running) |
| `partial` | partially_filled | Submitted + filled>0 | resting, fill_count>0 | live, size_matched>0 | EXECUTABLE, sizeMatched>0 | OPEN, filled_size>0 | PARTIALLY_FILLED | — |
| `pending_cancel` | pending_cancel | PendingCancel | — | — | — | CANCEL_QUEUED | PENDING_CANCEL | — |
| `filled` (terminal) | filled | Filled | executed | matched / MATCHED | EXECUTION_COMPLETE, sizeRemaining 0 | FILLED | FILLED | ACCEPTED (a bet is its fill) |
| `cancelled` (terminal) | canceled, done_for_day | Cancelled, ApiCancelled | canceled | CANCELED, CANCELED_MARKET_RESOLVED | EXECUTION_COMPLETE with sizeCancelled/sizeLapsed/sizeVoided | CANCELLED | CANCELED | CANCELLED |
| `expired` (terminal) | expired | — | canceled (+expiration) | (GTD expiry → canceled) | EXPIRED (FOK) | EXPIRED | EXPIRED, EXPIRED_IN_MATCH | — |
| `rejected` (terminal) | rejected | (Inactive, see below) | 4xx on create | success=false, INVALID | FAILURE report | FAILED | REJECTED | NOT_ACCEPTED, PROCESSED_WITH_ERROR |
| `replaced` (terminal for the old ref) | replaced | — | — | — | (replace = cancel + place) | — | executionReport x=REPLACED | — |
| `unknown` | — | Inactive ("not working", many reasons), whyHeld | — | — | TIMEOUT "status of the bet is unknown" | UNKNOWN_ORDER_STATUS | — | unexpected error → query by uniqueRequestId |

Rules that fell out of the mapping: (1) **terminality is preserved** — a
non-terminal venue state never maps to a terminal neutral one; (2) when a
venue lacks a state the adapter **collapses toward less certainty** (`open`
rather than `partial` when it cannot see fills; `unknown` rather than
`rejected` for IBKR `Inactive`); (3) `unknown` is a first-class state because
two venues document it and the loop must **reconcile before acting** on it;
(4) `partial` is a derived state on four venues (status + filled>0), so the
neutral record must carry `filled_qty` independently of `status`; (5) FIX
separates ExecType (what happened) from OrdStatus (where it is) — Binance and
Alpaca do too — so the neutral stream event carries both.

### 1.4 Identity and idempotency

Client references exist on 7/8 venues but with three different retry
contracts: duplicate **rejected** (Kalshi), duplicate **returns the existing
order** (Coinbase), duplicate **de-duped inside a window** (Betfair 60 s,
Pinnacle 30 min, "reuse the same uniqueRequestId" on retry). Alpaca and
Binance auto-generate one if absent. Polymarket has none, so the child must
keep its own `client_ref → venue_ref` map. Two venues document that a submit
can end in an **unknown** outcome (Betfair TIMEOUT; Pinnacle "verify the bet
was actually placed by querying /bets?uniqueRequestIds="). So the core needs a
durable ledger keyed by client_ref written BEFORE the network call.

### 1.5 Units

Quantity is shares (Alpaca, IBKR, Coinbase base, Binance base), contracts
(Kalshi 2-dec fixed point; Polymarket shares 6-dec fixed point), or stake
currency (Betfair `size`, Pinnacle `stake` with WIN/RISK meaning). Four venues
also accept **notional** sizing (Alpaca `notional`, Coinbase `quote_size`,
Binance `quoteOrderQty`, Polymarket market BUY in USD) and all four make it
mutually exclusive with quantity. Price is currency-per-unit, a probability in
(0, 1) at a market-specific tick (Kalshi YES-leg dollars; Polymarket
`tick_size` table), or decimal odds on a ladder (Betfair; Pinnacle in five odds
formats). Odds invert the "better" direction: a backer prefers HIGHER odds, a
buyer prefers LOWER price. Kalshi shows why normalising is dangerous: `side`
bid/ask is on the YES leg and NO is 1 − YES; Polymarket's NO is a different
token; Betfair's LAY liability is `size × (price − 1)`. Money itself arrives as
cents ints (Kalshi `balance`), dollar strings (`balance_dollars`), or 6-dec
integers (Polymarket) — never floats.

### 1.6 Fees

Per fill with a currency (Binance `commissionAsset` may differ from the quote
asset; IBKR commission arrives in a **separate** message keyed by `execId`;
Coinbase `commission`; Kalshi `fee_cost` rounded up per fill), formula-based
taker-only (Polymarket `C × feeRate × p × (1 − p)`), **at settlement on net
market winnings** (Betfair `commission` in listClearedOrders — summing fills
gives zero), or embedded in odds (Pinnacle). Fills can be **revised after the
fact**: Coinbase `REVERSAL/CORRECTION/SYNTHETIC`, Polymarket trade `FAILED`
after `MATCHED`, FIX ExecType Trade Correct/Trade Cancel.

### 1.7 Streaming vs polling

Every venue with a stream also tells you not to trust it alone: IBKR
"duplicate orderStatus messages"; Polymarket "do not replace authoritative
account reads or replay every change missed during a disconnection";
Kalshi `seq` and Coinbase `sequence_num` exist precisely so you can detect
gaps; Betfair sends `fullImage` resyncs. NautilusTrader's live design is the
cleanest precedent: reconciliation on start from `OrderStatusReport`,
`FillReport`, `PositionStatusReport`, with "positions remain fill-derived" and
venue positions used as a check. Polling `order/open_orders/fills` is
therefore the floor; a stream is an accelerator that must be idempotent
against the poll.

### 1.8 Paper execution models (what the simulators actually do)

- **Backtrader**: market fills at next bar open; limit fills when the bar
  range reaches the price; slippage `slip_perc`/`slip_fixed`, direction-aware,
  capped at high/low unless `slip_out`; `slip_match` defers instead of capping;
  volume fillers `FixedSize`, `FixedBarPerc(perc)`, `BarPointPerc(minmov,
  perc)` cap the executed size and leave a `Partial` order; commission via
  `CommInfoBase(commission, percabs, mult, margin)`.
- **NautilusTrader**: L1 taker fills at the best crossed quote bounded by the
  limit; residual "one tick worse"; maker limit fills "when matched by a trade
  or market move"; `FillModel(prob_fill_on_limit=1.0, prob_slippage=0.0,
  random_seed, liquidity_consumption=False)`; L2/L3 walks the book and
  partial-fills on available size; `LatencyModel(base/insert/update/cancel
  _latency_nanos)` with an inflight queue released in time order; fee models
  `MakerTakerFeeModel`, `FixedFeeModel`, `PerContractFeeModel`; bar execution
  synthesises O→H→L→C ticks (`bar_adaptive_high_low_ordering`).
- **LEAN**: `ImmediateFillModel`, "pre-built fill models assume orders
  completely fill"; slippage models `Constant`, `VolumeShare`, `MarketImpact`;
  `FeeModel.get_order_fee → OrderFee(CashAmount, currency)`; order statuses
  New/Submitted/PartiallyFilled/Filled/Canceled/Invalid/CancelPending/
  UpdateSubmitted.
- **flumine** (Betfair): on place, walk `available_to_back/lay` and take size
  per level at the AVAILABLE price (price improvement); resting orders match
  against subsequent traded volume **halved** with a position-in-queue
  counter; `place_latency 0.120`, `cancel_latency 0.170`, `update_latency
  0.150`, `replace_latency 0.280` s; LAPSE orders lapse at suspension;
  `simulation_available_prices` "will double count liquidity";
  `simulated_strategy_isolation` to avoid double-counting passive liquidity
  across strategies; statuses PENDING/CANCELLING/UPDATING/REPLACING/
  EXECUTABLE/EXECUTION_COMPLETE/EXPIRED/VIOLATION.

Common denominators: fill rule for marketable orders (touch / walk / slip),
fill rule for resting orders (touch vs trade-through, with a probability or
queue model), a size cap, a latency, a fee model, and a seed.

---

## 2. Design implications for dskit

### 2.1 The `Executor` ABC (tier 1, stdlib only)

Mirror the `Connector` four-verb discipline: cheap `spec`, side-effect-free
`check`, everything else explicit.

```python
class Executor(abc.ABC):
    def spec(self) -> dict: ...            # {"params": {knob: {required, secret, notes}}} default-deny
    def capabilities(self) -> dict: ...    # {"tifs": (...), "market_orders": bool, "notional": bool,
                                           #  "positions": "venue"|"derived", "settlements": bool,
                                           #  "stream": bool, "units": {"qty": str, "price": str, "cash": str}}
    @abc.abstractmethod
    def check(self, config) -> None: ...   # fail fast; moves no money, places nothing
    @abc.abstractmethod
    def submit(self, intent) -> Ack: ...   # one instrument, one side, one qty, limit-or-market
    @abc.abstractmethod
    def cancel(self, ref) -> Ack: ...      # cancel the FULL remainder; may return pending_cancel
    @abc.abstractmethod
    def order(self, ref) -> OrderState: ...
    @abc.abstractmethod
    def open_orders(self) -> list: ...     # OrderState, only refs this executor owns
    @abc.abstractmethod
    def fills(self, since_ms, cursor=None) -> iterator: ...   # Fill, ascending ts_ms, id-unique
    @abc.abstractmethod
    def balances(self) -> list: ...        # Balance
    def positions(self) -> list: ...       # default: derived from fills via PositionBook
    def settlements(self, since_ms) -> iterator: ...  # default: empty; capability says if real
    def replace(self, ref, intent) -> Ack: ...        # default: cancel → wait terminal → submit(new client_ref)
    def events(self) -> iterator: ...      # optional stream of (ExecEvent); default: none
```

Hook semantics: `submit` is the ONLY verb allowed to create exposure and it
must write the `Intent` to the run-dir ledger BEFORE any I/O (unknown-outcome
recovery). `cancel` acknowledges; the loop trusts only `order(ref)` reaching a
terminal status. `replace` is concrete in the core because four venues
implement four different semantics; a child overrides it only if it can keep
the contract "old ref becomes `replaced`, Ack carries the surviving ref".
`positions()` defaults to the core's fill-derived `PositionBook`; a venue that
exposes positions uses them as a reconciliation check (NautilusTrader's
"positions remain fill-derived"). Batching, partial cancel, post-only,
self-trade prevention, persistence and bracket/stop types are residue: they
travel in `Intent.extra`, validated by the executor's `spec()` default-deny,
never interpreted by the core. Money-moving is a declared act: a live
executor must be constructed `armed` by an explicit config knob AND an env
var it names in `spec()`; `submit` on an unarmed live executor raises
`NotArmed` before any I/O, and the run-dir carries the execution mode.

`Ref` = `(client_ref, venue_ref)` where either may be `None` — the venue
without client ids (Polymarket) and the venue with pending venue ids
(Pinnacle live bets, Betfair async) both need this.

### 2.2 Records and closed vocabularies

- `Intent`: `client_ref` (required, generated by the loop, UUID4),
  `instrument` (venue-native tradeable-unit id; equals `MarketRecord.contract`),
  `side` ∈ {buy, sell}, exactly one of `qty` or `notional`, `limit` (`None` =
  market; capability-gated), `tif` ∈ {gtc, gtd, ioc, fok, day} with
  `expires_ms` when gtd, `asof_ms`, `extra` (residue), `notes`. No types
  beyond limit/market in the core.
- `Ack`: `client_ref`, `venue_ref`, `status`, `ts_ms`, `filled_qty`,
  `avg_price`, `fee`, `reason`, `native`.
- `OrderState`: `Ack` fields + `instrument`, `side`, `qty`, `remaining_qty`,
  `limit`, `tif`, `created_ms`, `updated_ms`.
- `Fill`: `fill_id` (unique per executor), `venue_ref`, `client_ref`,
  `instrument`, `side`, `qty`, `price`, `fee`, `fee_currency`, `liquidity` ∈
  {maker, taker, unknown}, `status` ∈ {pending, final, reversed}, `ts_ms`,
  `native`. `status` exists because Polymarket trades can FAIL after MATCHED
  and Coinbase emits REVERSAL/CORRECTION; a reversed fill undoes its quantity
  in `PositionBook`.
- `Position`: `instrument`, `qty` signed in the executor's declared unit,
  `avg_cost`, `source` ∈ {derived, venue}, `native`. YES/NO or BACK/LAY are
  NOT netted in the core — separate instruments (Polymarket tokens) or the
  child's own exposure math (Betfair matched ladders).
- `Balance`: `currency`, `total`, `available`, `native` (Binance free/locked;
  Kalshi balance vs portfolio_value; Alpaca cash vs buying_power).
- `Settlement`: `instrument`, `outcome` (bool for binary, mark for MtM —
  handed to the existing `Accounting` seam, which already refuses the other
  family), `qty`, `payout`, `fee`, `settled_ms`, `native`.
- `ExecEvent` (stream): `kind` ∈ {ack, fill, cancel, expire, reject, replace,
  status} + the record; seq-gap or reconnect ⇒ the loop re-polls.
- Closed vocabularies pinned as tuples: `STATUSES = (pending, open, partial,
  pending_cancel, filled, cancelled, expired, rejected, replaced, unknown,
  not_sent)`, `TERMINAL = (filled, cancelled, expired, rejected, replaced,
  not_sent)`, `TIFS`, `SIDES`, `LIQUIDITY`, `FILL_STATUSES`. `not_sent` is the
  Ack-only status of a deliberate non-transmission (shadow, kill switch;
  IBKR's `transmit=False` is the venue analogue).

### 2.3 Unit handling

Native units are carried verbatim with a declared label; the core never
converts odds, cents, or fixed-point integers. `capabilities()["units"]`
declares `{"qty": ..., "price": ..., "cash": ...}` and every record produced by
that executor is in those units — pinned by a conformance test. Amounts cross
the boundary as strings/`decimal.Decimal`, not floats. Price direction: the
core's paper fill logic works in **price space** (buyer prefers lower); an
odds venue's child converts quotes to probability space before building
`MarketRecord` (which the records module already treats as venue-currency
bid/ask), and its live executor converts back inside `submit`. What must not
be normalised: the leg convention (YES-leg price vs NO token), stake vs
liability, WIN vs RISK, tick ladders, fee currency.

### 2.4 `PaperExecutor` knobs (stdlib, clock-driven by the loop's `asof_ms`)

Fed quotes via `on_quote(MarketRecord)`; no wall clock, no network.

- `fill_rule` ∈ {touch, cross, mid}: marketable orders fill at the opposite
  touch (default), require the touch strictly better than the limit ("cross",
  the conservative choice), or at mid (optimistic bound).
- `slippage` {`bps`: 0, `ticks`: 0, `tick`: size} applied against the order
  direction, capped at the limit (Backtrader's cap; Nautilus' one-tick residual).
- `resting_rule` ∈ {touch, through} + `p_fill_on_touch` (Nautilus
  `prob_fill_on_limit`) + `queue_frac` (flumine's halved traded volume as a
  knob, default 1.0 = no queue).
- `size_cap` ∈ {none, quote_size, frac: x} → partial fills with the remainder
  resting (Backtrader fillers; Nautilus depth partials).
- `latency_ms` {`submit`, `cancel`} (flumine 120/170 ms defaults are a
  reasonable documented seed); commands take effect on the first quote after
  arrival, in time order.
- `fees` {`kind`: none | per_unit | bps | maker_taker_bps | pxq_rate, …} —
  `pxq_rate` = `rate × qty × p × (1 − p)` (the prediction-market shape); a
  strategy object per kind (subclass hook, not a branch).
- `tif`: ioc cancels the remainder after the first matching quote; fok is
  all-or-nothing against `size_cap`; gtd expires at `expires_ms`; `day` is
  refused unless `session_end_ms` is configured (session knowledge is residue).
- `seed` for the probabilistic draws; `partial_fills: bool`.

`ShadowExecutor`: writes every `Intent` to the ledger, returns `Ack(status=
"not_sent", reason="shadow")`, `open_orders() == []`, `fills()` empty,
positions derived (so zero). It is the executor a child wires first.

### 2.5 Conformance battery (every executor, incl. children's fakes)

1. `spec()` is default-deny; unknown knob refused; secret knobs name env vars.
2. `check()` performs no submit/cancel (spy on the transport).
3. `submit` echoes `client_ref`; `status ∈ STATUSES`; `venue_ref is None`
   only when `status ∈ {pending, rejected, not_sent}`.
4. Same `client_ref` twice ⇒ same `venue_ref` or `DuplicateRef`; never two
   orders (idempotency contract regardless of venue flavour).
5. Ledger-before-I/O: a transport that raises after send still leaves the
   `Intent` on disk with `status=unknown`.
6. Terminal states absorb: after a terminal `order(ref)`, later calls never
   report non-terminal; `remaining_qty == 0` iff `filled`.
7. `filled_qty` is non-decreasing except through a `Fill(status=reversed)`;
   `Σ final fills == filled_qty` per ref (unit-agreement pin).
8. Unsupported `tif`/market/notional per `capabilities()` ⇒ raise before I/O.
9. `fills(since)` over overlapping windows yields no duplicate `fill_id`,
   ascending `ts_ms`.
10. Units: every record's numeric fields are `Decimal`/str; labels equal
    `capabilities()["units"]`; an `Intent` in other units is refused.
11. `positions()` (derived) equals venue positions where the capability is
    `venue` (reconciliation test with an injected divergence ⇒ loud refusal).
12. Live executors: unarmed ⇒ `NotArmed` before I/O; armed requires both knob
    and env var.
13. Shadow: any socket connect raises (monkeypatched) — proves no network.
14. Paper: deterministic under `seed`; ioc remainder cancelled; fok
    all-or-nothing; limit fills never worse than limit; touch-buy never below
    ask; `pxq_rate` fee equals the closed form; latency reorders a cancel
    that arrives after a fill into `filled`, not `cancelled`.
15. Vocabulary pin: `STATUSES`, `TIFS`, `SIDES` restated in the test (the
    deliberate independent restatement the repo standard calls for).

---

## 3. Pitfalls & anti-patterns

- Treating a submit timeout/transport error as a rejection. Betfair returns
  TIMEOUT "status of the bet is unknown"; Pinnacle says query by
  `uniqueRequestId`. Map to `unknown`, reconcile, never resubmit blindly.
- Assuming a fill is final. Polymarket trades FAIL after MATCHED; Coinbase
  REVERSAL/CORRECTION; FIX Trade Cancel. Fill needs `status`.
- Assuming cancel is synchronous. Alpaca 204 means accepted, then
  `pending_cancel`; Coinbase `CANCEL_QUEUED`; IBKR `PendingCancel`.
- Assuming replace preserves anything. Alpaca: new id; Kalshi: same id but
  queue lost unless decreasing; Betfair: cancel then place; Polymarket: none.
  Hence a concrete core `replace`, not an abstract one.
- Netting legs in the core (YES/NO, BACK/LAY). Polymarket's NO is another
  token; Betfair's matched backs and lays sit at different prices.
- Summing per-fill fees as "the fee". Betfair charges commission at
  settlement on net market winnings; fills carry none.
- Using the stream as the source of truth. Every stream doc read says
  re-poll after reconnect; IBKR duplicates; Polymarket has no sequence number.
- Unscoped cancel-all. IBKR `reqGlobalCancel` and Betfair cancelOrders with no
  marketId cancel orders this process never placed. Core cancel-all iterates
  its own ledger.
- Batch = atomic. Betfair PROCESSED_WITH_ERRORS, Polymarket `not_canceled`,
  Kalshi/Coinbase per-item results, Alpaca 207. Single-order verbs in the ABC.
- Floats for money and sizes. Cents ints, dollar strings, 6-decimal integers,
  fixed-point counts — use `decimal` at the boundary.
- GTD off-by-a-minute: Polymarket expires one minute early and refuses
  expirations under three minutes; a paper executor must not model gtd as exact
  unless told so (`extra`).
- Vocabulary inflation: do not add `delayed`, `queued`, `lapsed`, `voided` to
  the neutral set — they are `pending`/`cancelled` with a `reason`.
- Leaking session semantics (`day`, at-the-open/close, in-play persistence)
  into tier 1 — they need a calendar the core does not have.
- Retrying without the same client ref — Pinnacle and Betfair de-dupe ONLY
  on it; Kalshi rejects, Coinbase returns the original; both are what you want.

---

## 4. Sources

Alpaca: https://docs.alpaca.markets/reference/postorder ·
https://docs.alpaca.markets/docs/orders-at-alpaca ·
https://docs.alpaca.markets/docs/websocket-streaming ·
https://docs.alpaca.markets/reference/getallopenpositions ·
https://docs.alpaca.markets/reference/getaccount-1 ·
https://docs.alpaca.markets/reference/getaccountactivitiesbyactivitytype-1 ·
https://docs.alpaca.markets/reference/patchorderbyorderid-1 ·
https://docs.alpaca.markets/reference/deleteorderbyorderid-1 ·
https://docs.alpaca.markets/reference/deleteallorders-1

Interactive Brokers: https://interactivebrokers.github.io/tws-api/order_submission.html ·
https://interactivebrokers.github.io/tws-api/modifying_orders.html ·
https://interactivebrokers.github.io/tws-api/cancel_order.html ·
https://interactivebrokers.github.io/tws-api/executions_commissions.html ·
https://interactivebrokers.github.io/tws-api/positions.html ·
https://interactivebrokers.github.io/tws-api/account_summary.html ·
https://ibkrguides.com/releasenotes/api/cp-web/latest-2022.htm ·
https://raw.githubusercontent.com/erdewit/ib_insync/master/ib_insync/order.py

Kalshi: https://docs.kalshi.com/api-reference/orders/create-order-v2 ·
https://docs.kalshi.com/api-reference/orders/get-orders ·
https://docs.kalshi.com/api-reference/orders/get-order.md ·
https://docs.kalshi.com/api-reference/orders/cancel-order-v2.md ·
https://docs.kalshi.com/api-reference/orders/amend-order-v2.md ·
https://docs.kalshi.com/api-reference/orders/decrease-order-v2.md ·
https://docs.kalshi.com/api-reference/orders/batch-cancel-orders-v2.md ·
https://docs.kalshi.com/api-reference/portfolio/get-fills ·
https://docs.kalshi.com/api-reference/portfolio/get-positions ·
https://docs.kalshi.com/api-reference/portfolio/get-settlements ·
https://docs.kalshi.com/api-reference/portfolio/get-balance ·
https://docs.kalshi.com/getting_started/quick_start_create_order.md ·
https://docs.kalshi.com/getting_started/fee_rounding.md ·
https://docs.kalshi.com/websockets/user-fills.md ·
https://docs.kalshi.com/websockets/user-orders.md ·
https://docs.kalshi.com/websockets/market-positions.md ·
https://docs.kalshi.com/asyncapi.yaml

Polymarket: https://docs.polymarket.com/developers/CLOB/orders/create-order ·
https://docs.polymarket.com/concepts/order-lifecycle ·
https://docs.polymarket.com/trading/place-orders.md ·
https://docs.polymarket.com/trading/manage-orders.md ·
https://docs.polymarket.com/trading/realtime-order-updates.md ·
https://docs.polymarket.com/trading/fees.md ·
https://docs.polymarket.com/trading/positions/how-positions-work.md ·
https://docs.polymarket.com/developers/CLOB/orders/cancel-orders ·
https://docs.polymarket.com/developers/CLOB/websocket/user-channel ·
https://docs.polymarket.com/api-reference/trade/get-user-orders.md ·
https://docs.polymarket.com/api-reference/core/get-current-positions-for-a-user.md ·
https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets.md

Betfair: Betting Enums (Confluence content 2687455) https://betfair-developer-docs.atlassian.net/wiki/rest/api/content/2687455?expand=body.storage ·
placeOrders (content 2687496) https://betfair-developer-docs.atlassian.net/wiki/rest/api/content/2687496?expand=body.storage ·
https://rdrr.io/github/phillc73/abettor/man/placeOrders.html ·
https://rdrr.io/github/phillc73/abettor/man/listClearedOrders.html ·
https://raw.githubusercontent.com/betcode-org/betfair/master/betfairlightweight/endpoints/betting.py ·
https://raw.githubusercontent.com/betcode-org/betfair/master/betfairlightweight/resources/bettingresources.py ·
https://raw.githubusercontent.com/betcode-org/betfair/master/betfairlightweight/streaming/cache.py ·
https://raw.githubusercontent.com/betcode-org/betfair/master/betfairlightweight/enums.py

Coinbase Advanced Trade: https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order.md ·
https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/edit-order.md ·
https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/cancel-order.md ·
https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/list-orders.md ·
https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/list-fills.md ·
https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/websocket/user.md ·
https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/orders ·
https://coinbase.github.io/coinbase-advanced-py/coinbase.rest.html

Binance Spot: https://developers.binance.com/docs/binance-spot-api-docs/enums ·
https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints ·
https://developers.binance.com/docs/binance-spot-api-docs/rest-api/account-endpoints ·
https://developers.binance.com/docs/binance-spot-api-docs/user-data-stream

Pinnacle: https://raw.githubusercontent.com/pinnacleapi/pinnacleapi-documentation/refs/heads/master/openapi-specification/betsapi.v4-oas.yaml ·
https://raw.githubusercontent.com/pinnacleapi/pinnacleapi-documentation/master/FAQ.md ·
https://raw.githubusercontent.com/pinnacleapi/pinnacleapi-documentation/master/README.md ·
https://www.rdocumentation.org/packages/pinnacle.API/versions/2.3.3/topics/PlaceBet

Simulators: https://www.backtrader.com/docu/broker/ · https://www.backtrader.com/docu/order/ ·
https://www.backtrader.com/docu/filler/ · https://www.backtrader.com/docu/slippage/slippage/ ·
https://nautilustrader.io/docs/latest/concepts/backtesting/fill-models/ ·
https://nautilustrader.io/docs/latest/concepts/backtesting/fill-prices-and-matching/ ·
https://nautilustrader.io/docs/latest/concepts/backtesting/bar-execution/ ·
https://nautilustrader.io/docs/latest/concepts/backtesting/execution-flow/ ·
https://nautilustrader.io/docs/latest/concepts/backtesting/data-and-venues/ ·
https://nautilustrader.io/docs/latest/concepts/orders/ ·
https://nautilustrader.io/docs/latest/concepts/execution/ ·
https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/trade-fills/key-concepts ·
https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/slippage/key-concepts ·
https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/transaction-fees/key-concepts ·
https://raw.githubusercontent.com/betcode-org/flumine/master/flumine/simulation/simulatedorder.py ·
https://raw.githubusercontent.com/betcode-org/flumine/master/flumine/execution/simulatedexecution.py ·
https://raw.githubusercontent.com/betcode-org/flumine/master/flumine/config.py ·
https://raw.githubusercontent.com/betcode-org/flumine/master/flumine/order/order.py ·
https://betcode-org.github.io/flumine/advanced/

FIX 4.4 dictionary: https://www.onixs.biz/fix-dictionary/4.4/tagNum_39.html (OrdStatus) ·
https://www.onixs.biz/fix-dictionary/4.4/tagNum_150.html (ExecType) ·
https://www.onixs.biz/fix-dictionary/4.4/tagNum_59.html (TimeInForce) ·
https://www.onixs.biz/fix-dictionary/4.4/tagNum_40.html (OrdType)
