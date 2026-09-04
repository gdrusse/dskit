# R1 — Order management and pre-/post-trade risk controls: what a generic decision-execution layer must track, check and refuse

Research input for the `dskit.production` design. Domain-neutral throughout; venue-specific residue is
called out explicitly and assigned to the child. Sources are numbered `[n]` and listed in §4.

---

## 1. Findings by theme

### 1.1 Order lifecycle, identity, bookkeeping, reconciliation

**Two facts per report, never one.** FIX separates *what just happened* (`ExecType`, tag 150) from
*where the order now stands* (`OrdStatus`, tag 39). Systems that read only the status break on pending
states and on venues that send intermediate reports [1][2]. The full `OrdStatus` vocabulary is: New,
PartiallyFilled, Filled, DoneForDay, Canceled, Replaced (obsolete), PendingCancel, Stopped, Rejected,
Suspended, PendingNew, Calculated, Expired, AcceptedForBidding, PendingReplace [2]. `ExecType` adds the
post-hoc events a status alone cannot express: Restated (unsolicited change by the venue), Trade,
TradeCorrect, TradeCancel, OrderStatus (reply to a status query) [3].

**Pending states are states, not failures.** PendingNew/PendingCancel/PendingReplace are transients that
must be waited out; a cancel that returns PendingCancel has not failed [1].

**Quantity invariant.** `CumQty + LeavesQty == OrderQty` on every report; every fill carries a unique
`ExecID`, which is the dedup key for idempotent settlement [1][22].

**Identity and idempotency.** The client chooses `ClOrdID`; a replace chains a *new* `ClOrdID` to
`OrigClOrdID` (sender chains "optimistically" to the last sent id, receiver "pessimistically" to the last
accepted). A rejected replace leaves the original order untouched. `OrderQty` on a replace is the *total
intended* quantity (including already-executed), not an increment. Do not send a second replace while one
is pending [4]. Cancel rejections carry a closed reason set worth reusing verbatim: too-late-to-cancel,
unknown order, already-pending, duplicate ClOrdID [5]. After connectivity loss, an in-doubt order is
resolved with an Order Status Request; an unknown order comes back as Rejected/unknown [6].
Venues outside equities converge on the same shape: Kalshi rejects duplicate `client_order_id`s and
exposes `resting | canceled | executed` with `initial/fill/remaining` counts [7][8]; Betfair returns
`PENDING | EXECUTABLE | EXECUTION_COMPLETE | EXPIRED`, dedups on `customerRef`
(`DUPLICATE_TRANSACTION`) and carries a `marketVersion` for optimistic concurrency [9]. NautilusTrader's
enum adds two states no venue has but every client needs: `DENIED` (refused locally by risk) and `VOIDED`
(terminal after an authoritative fill correction) [10].

**Order = monotonic state machine + saga.** There is no shared transaction with a venue. A broker-side
design [22] makes the local reservation the only strongly consistent step, executes transitions as
compare-and-set (`UPDATE ... WHERE state=:expected`; zero rows ⇒ re-read), derives `ClOrdID`
deterministically from the local order id so a retry cannot spawn a second live order, commits the
routing trigger in the same transaction (outbox), and applies fills idempotently by `exec_id`. Ambiguous
outcomes (timeouts) are resolved against the venue's own record, never by resending blind.

**Position bookkeeping.** Two position models exist: *netting* (one position per instrument, flips
sign) and *hedging* (many concurrent positions, each with its own id); most crypto venues net, some FX
venues hedge, and the engine must translate when strategy and venue disagree [11]. Exposure checks must
include *working orders* as if filled, not just current positions [12 §1.2]; RTS 6 requires the ability
to compute outstanding exposure in real time [13 Art 17(3)].

**Reconciliation is a first-class loop, not an end-of-day job.** Regulation requires reconciling own
electronic trading logs against venue, broker, clearing and data-provider records, "in real-time where
[they] provide the information in real-time" [13 Art 17(3), 13(9)]. The independent source is the
*drop copy* — a venue-generated mirror of execution reports, order-state changes, rejections and
cancels, produced at execution time on a session separate from order entry [12 §4.1][14]. Breaks land in
a suspense account so the ledger stays balanced while investigated; a rising suspense balance escalates
[22]. NautilusTrader's startup sequence is a good generic template: reconcile order status → fills →
positions, in that order; infer missing events; tag orders the venue knows but the client does not as
`EXTERNAL`; **refuse to start** if reconciliation fails; then run periodic in-flight, open-order and
position checks. Two documented caveats: venue "open orders" endpoints cannot distinguish a missing order
from a recently closed one, and venues drop old trades so the lookback window matters [15].

### 1.2 The generic control taxonomy (from 15c3-5, RTS 6, FIA, FCA)

**SEC Rule 15c3-5** frames three families [16][17]:
- *Financial*: prevent orders exceeding pre-set credit/capital thresholds **in the aggregate** per account
  and firm-wide; reject erroneous orders by price/size "on an order-by-order basis or over a short period
  of time"; reject duplicative orders.
- *Regulatory*: pre-order compliance checks; restricted instruments; access limited to authorised
  persons/accounts; **immediate** post-trade execution reports to surveillance.
- *Governance*: controls under the firm's direct and exclusive control; documented; reviewed at least
  annually; CEO certification. Staff FAQ: controls must be automated and systematic *pre-trade* — a
  post-trade check "would not be reasonably designed to prevent entry"; thresholds may be adjusted
  intraday only with a documented, approved reason [17].

**MiFID II RTS 6** (Delegated Regulation 2017/589) is the most complete operational checklist [13]:

| Art. | Requirement (generic reading) |
|---|---|
| 5, 11 | Written development/testing methodology; a *designated person* authorises deployment and every material change; records of who changed what, when, approved by whom; changes communicated to traders, compliance, risk. |
| 6, 7 | Conformance test against each venue; test in an environment **separated from production** (production explicitly includes "risk control systems"); test matching logic, data flows and the kill. |
| 8 | Controlled deployment: pre-defined limits on number of instruments, price/value/number of orders, strategy positions, number of venues. |
| 10 | Annual stress test at 2× the six-month peak of messages and trade volume. |
| 12 | **Kill functionality**: cancel immediately any or all unexecuted orders at any or all venues; every order attributable to an algorithm and a trader/desk/client. |
| 13(9), 17 | Reconcile logs with venues/brokers/clearers/data providers; continuous post-trade market and credit risk assessment; when a post-trade control fires, adjust, shut down, or withdraw in an orderly way. |
| 14 | Business continuity: usage policy for the kill; shut-down procedure that does not itself create disorder; alternative means to manage outstanding orders and positions. |
| 15 | Pre-trade on order entry: **price collars** (per order *and* over a period), **max order value**, **max order volume**, **max message rate**; every sent order counts immediately; **repeated automated execution throttle** — after N repeats the system auto-disables until a designated human re-enables; market and credit limits calibrated to capital, experience, vendor reliance and *adjusted for liquidity*; auto-block when a trader lacks permission or a threshold is at risk; **overrides** only temporary, exceptional, verified by risk and authorised by a designated individual. |
| 16 | Real-time monitoring by the trader *and* an independent risk function; alerts **within five seconds**; process for remedial action including orderly withdrawal; alerts when own orders trip venue circuit breakers. |
| 28 | Record each order immediately on submission; keep five years. |

**FIA 2024 best practices** add the engineering detail regulators leave implicit [12]: limits are
checked on *new and modified* orders; "systems should prevent orders from being placed in cases where no
order size limits have been set for an instrument"; price tolerance is deviation from a *reference*
price; cancel-on-disconnect must be per-session and optional; a kill switch "may allow risk-reducing
orders while preventing risk-increasing orders", must be usable by risk staff independently of the
trading application, must not be overridable by the trader once a broker invokes it, and needs
entitlements plus explicit warnings; venues should offer an **independent order-management channel**
(view/cancel outside the trading path); **market-data reasonability checks** — time since last update,
deviation from previous price/average, spread — should alert and block orders; repeated-execution limits
belong to the trader, not the broker; **a message throttle must never reject a cancel**; self-match
prevention has modes (cancel resting/new/both, decrement) and granularities; post-trade credit limits
carry alerts at thresholds so humans can talk before a breach.

**FCA 2018 review** contrasts basic with enhanced controls: aggregate limits vs limits per 15-minute
window and per symbol; static volume/value caps vs caps relative to ADV and touch size; message caps per
day vs per window/symbol; repeated-order checks vs repeated *and rejected* order checks; naive collars vs
collars that distinguish passive/aggressive or estimate market impact. Good practice: limits at several
levels with independent risk oversight, pre-authorised staff adjusting within pre-agreed bands, alerts at
50 % and 80 % of a limit, a formal breach log, and an **algorithm inventory** listing owner, parameters,
behaviour, and every control (including kill) that applies [18]. The Finnish FSA's 2024 review defines
the vocabulary: a *hard block* cannot be bypassed by the trader alone; a *soft block* alerts or can be
self-bypassed — and flags over-reliance on a DMA provider's or venue's controls that were never assessed
[19].

### 1.3 Postmortems, mapped to the control that would have caught them

| Incident | What actually failed | Generic control |
|---|---|---|
| **Knight Capital, 2012** [20][21][23][24] | A parent/child router's cumulative-fill check had been moved in 2005 and never retested; a deprecated feature's flag bit was reused; one of eight servers missed by a manual deploy whose script "failed silently... and reported success"; 97 pre-open "Power Peg disabled" e-mails were "not designed... to be system alerts"; the 9.5 % price collar was measured against the NBBO *at receipt* and did not apply to pre-open orders; position limits ignored outstanding orders; the error account had a $2 m limit "not linked to any automated controls"; the risk tool was human-only, lagged, and did not display limits; the first response (uninstalling the new code) made it worse; no written rule on "when to disconnect a malfunctioning system". 4 m executions, 397 m shares, $460 m in 45 min. | Aggregate exposure gate that counts working orders; output-vs-input reconciliation at the gateway; collars re-evaluated against a live reference and active in every session phase; automated halt on threshold breach; alerts that are alerts; verified deploys; feature-flag hygiene; a rehearsed kill runbook. The SEC also noted a 2011 loss from *test data left in production quoting* and "no mechanism to test whether their systems were relying on stale data" [20 ¶33]. |
| **Goldman Sachs options, 2013** [25] | A configuration error turned contingent orders into live $1 orders (≈16 000 orders, 1.5 m contracts); pre-market price checks were "unreasonably wide"; an employee manually lifted circuit-breaker blocks on outgoing message rates; capital usage was computed every 30 minutes with no automated shutdown. | Collars that do not loosen by session; message throttles whose override is itself gated; continuous (not periodic) exposure; auto-halt. |
| **Citigroup, 2022** [26][27] | $444 bn basket entered instead of $58 m; no basket-, stock-, ADV- or price-level *hard* block; "hundreds of soft warnings" in one scrollable window dismissible with a single click; a reference-feed outage rendered a value field as its negative; 711 alerts not escalated during a desk handover. £61.6 m in fines. | Hard blocks at the aggregate level; soft alerts that cannot be bulk-dismissed; fail-closed on missing reference data; alert routing that survives staffing changes. |
| **Flash Crash, 2010** [28] | A sell algorithm targeting 9 % of volume "without regard to price or time" executed $4.1 bn in ~20 min instead of hours; stub quotes traded at $0.01 and $100 000; a 5-second venue pause restored liquidity. | Participation algorithms need price/time gates; never quote placeholders; pauses work. |
| **Mizuho/J-Com, 2005** [29] | "Sell 610 000 at ¥1" instead of "sell 1 at ¥610 000"; the firm tried to cancel three times; the exchange did not cancel. | Size vs outstanding-float sanity; do not design on the assumption that a cancel or bust will save you. |
| **HanMag, 2013** [30] | Puts and calls swapped in an automated profit-taking program; 36 100 trades in minutes; firm insolvent. | Repeated-execution throttle; per-strategy loss limit; sign checks on derived orders. |
| **Everbright, 2013** [30] | A rogue arbitrage system submitted a flood of buy orders, moving the index ~6 %; the firm hedged before disclosing. | Pre-submission capital check; message-rate throttle; disclosure/kill policy is governance, not code. |

The counterfactual caution [24]: listing "should-have" controls does not explain why competent people
acted as they did; alerts were background noise, rollback was a reasonable improvisation, and the SEC had
judged the same controls "reasonably designed" the day before. Design for what operators will actually
see and do under pressure — fewer, louder, actionable signals and one obvious safe action.

### 1.4 Connectivity failure handling

- **Session liveness.** Heartbeat interval negotiated at logon (5–60 s, 30 s typical); a Test Request
  probes a silent peer; missing ~3 intervals ⇒ declare the link dead and enter recovery [31][32][33].
  NautilusTrader reconnects "when no inbound frame of any kind arrives within three heartbeat intervals"
  and gives a heartbeat-less transport *no* dead-peer detection window [34].
- **Sequence continuity.** Inbound and outbound counters persisted across restarts; gaps trigger a Resend
  Request and nothing after the gap is processed until filled; resetting sequence numbers on reconnect is
  the classic mistake [31][33][35]. Daily: compare orders *submitted* (own log) with orders *received*
  (drop copy); unmatched items are suspicious [33].
- **Cancel-on-disconnect (venue-side).** Best-effort, per session, optional, triggered when the venue —
  not the client — decides a disconnect is involuntary; CME excludes GTC/GTD orders and enables it by
  default on new sessions; broker pass-through of COD is "typically unsupported" [12 §1.4][36].
- **Dead-man's switch (client-armed).** `cancelAllOrdersAfter(timeout)`: Kraken recommends a 60 s
  timeout refreshed every 15–30 s; Binance's variant has a 5 s minimum, is checked every 100 ms, and
  *rejects new orders* while tripped until a heartbeat arrives [37][38].
- **Stale data.** Keep per-stream `exchange_ts`, `received_ts`, `source`, and `age`; run a state
  machine LIVE (≤3 s) → DEGRADED (≤10 s) → STALE → FALLBACK/RECOVERING/DEAD/MARKET_CLOSED; reject updates
  older than the last accepted; require several fresh ticks before trusting a recovered feed [39].
  Thresholds are per instrument, session and consequence [40]; the FIA formulation is time-since-update,
  deviation from previous/average price, and spread [12 §3.1]. Knight's regulator flagged the absence of
  any stale-data test [20 ¶33].
- **Sequence gaps in data feeds.** The Binance local-order-book recipe generalises: each event carries
  first/last update ids; if `U > lastUpdateId + 1` the book is out of sync — discard and rebuild from a
  snapshot [41].
- **Clock sync.** RTS 25: UTC traceability, documented "exact point at which a timestamp is applied";
  1 ms for algorithmic trading, 100 µs for HFT, 1 s for manual [42].
- **Throttling.** Venues rate-limit by weight, per-key or per-IP buckets, sometimes separate read/write
  buckets; respond with 429 (+ `Retry-After` at best); clients back off exponentially with jitter capped
  ~60 s and serialise bursts through a rate-aware queue [43][44]. Polymarket documents a "Global Rate
  Limit Exceeded" that is a 5-second latency stopgap, *not* a rate limit — do not throttle on it [44].
  Throttles must never block cancels [12 §3.4].
- **Degraded modes.** A closed trading-state enum — ACTIVE (all), REDUCING (only risk-reducing orders),
  HALTED (everything but cancels denied) — is the pattern both FIA's kill semantics and Nautilus use
  [12 §1.5][45]; RTS 6 calls the exit "orderly withdrawal from the market" [13 Art 16(5)].

### 1.5 Paper/sandbox versus live

- **Same code, different door.** Alpaca: identical API, different base URL and keys [46]. IBKR: paper
  account selectable at login (or separate credentials for some user types), $1 m simulated equity,
  same permissions and data subscriptions; "minimal differences" for the API [47][48]. Kalshi: a separate
  demo host [7].
- **Fidelity gaps are structural.** Alpaca fills only when marketable, with unlimited liquidity, random
  partial fills 10 % of the time, no market impact, queue position, latency slippage, fees or borrow
  [46]. IBKR fills from top of book with no slippage, cannot simulate several order types, always
  simulates stops [47][48]. Paper results are evidence of *plumbing*, not of edge.
- **Not every venue has a sandbox.** Betfair's developer program distinguishes a delayed-data key from a
  live-data key; both bet real money (Application Keys page; not fetchable this session — prior
  knowledge). A toolkit therefore has to ship its own paper executor.
- **How mature systems gate the transition.** Separate test environment that includes the risk controls
  [13 Art 7]; deployment under pre-defined limits on instruments, order value/count, positions and venues
  [13 Art 8]; a named approver and a record of who/what/when [13 Art 5, 11]; phased, scheduled deployment
  with rollback [18 §3.8]; practitioner benchmarks of 50–100 paper trades over 2–3 months, then live at
  the smallest possible size [49]; "shadow" strategies that mirror live signals into simulated positions
  [50].

---

## 2. Design implications for `dskit.production`

Constraints honoured: stdlib-only core; behaviour from JSON; no venue in code; executor as an object;
"actually move money" is a loud, declared act.

### 2.1 Abstract classes and hooks

- **`Executor` (ABC, tier-1; venue subclass is tier-3).** Hooks: `spec()` → capability record
  (`dedupes_client_id`, `supports_replace`, `has_cancel_on_disconnect`, `has_dead_man_switch`,
  `position_model: netting|hedging`, `price_precision`, `qty_precision`); `submit(intent) → Ack`;
  `cancel(ref)`; `replace(ref, intent)` (default: cancel-then-submit; venue may override);
  `open_orders()`; `fills(since)`; `positions()`; `balances()`; `status(ref)`; `cancel_all(scope)`;
  optional `arm_dead_man(timeout_s)` and `heartbeat()`. The child supplies only translation; every
  refusal, dedup key, and state transition lives in core. Two core implementations ship:
  `ShadowExecutor` (records intents, sends nothing — the default) and `PaperExecutor` (deterministic
  simulator over the same ticks, with configurable fill rule, partial-fill probability, latency in ticks,
  slippage and fees — so paper fidelity is a *declared* assumption, not a venue's accident).
- **`OrderLedger` (tier-1).** Monotonic state machine with compare-and-set transitions; `apply(report)`
  idempotent on `exec_id`; enforces `cum + leaves == qty`; refuses illegal transitions loudly; positions
  in netting or hedging mode per `spec()`.
- **`Gate` (ABC) + `GateChain`.** `check(intent, ledger, market, clock) → Verdict(kind, gate, reason,
  measured, limit)`. Concrete tier-1 gates as registry entries: `MaxOrderQty`, `MaxOrderValue`,
  `PriceCollar` (reference chosen by config; evaluated at *send time*, both sides, every session phase),
  `MaxPosition` (counts working orders), `MaxAggregateExposure`, `MaxDailyLoss`, `MessageRate`
  (windowed; **never applied to cancels**), `RepeatedExecutionThrottle` (latches until an explicit human
  re-enable), `DuplicateIntent`, `InstrumentAllowlist`, `CalendarGate`, `DataFreshnessGate`,
  `ClockSkewGate`, `LinkGate`. A gate with no configured limit for an instrument returns BLOCK (FIA).
  Overrides are config-declared per gate with `authorised_by` and `expires_at`; there is no runtime toggle.
- **`Supervisor`.** Owns the closed `TradingState`; transitions on gate BLOCKs, link loss, staleness, loss
  limits; `kill()` cancels all working orders, moves to KILLED, and stays there until a recorded human
  act; exposes REDUCING for orderly withdrawal.
- **`Reconciler` (tier-1).** `reconcile(ledger, venue_orders, venue_fills, venue_positions,
  venue_balances) → BreakReport`; runs at start (fail-closed: no ACTIVE until clean) and on an interval;
  break policy per class from config (`halt | adopt_venue | alert`).
- **`LinkMonitor` / `FeedMonitor`.** Heartbeat and sequence-gap detection fed by the connector; publish
  `LinkState` / `DataState`; they are inputs to gates, not side-channels.
- **`ArmingToken`.** Live mode requires three agreeing declarations — `mode: "live"` in the config,
  an environment variable, and a token file created by an explicit CLI act (`arm --live --acknowledge
  "<phrase>"`) carrying expiry and the config identity hash. A `LiveExecutor` cannot be *constructed*
  without a valid token; any disagreement refuses. Default mode is `shadow`.

### 2.2 Closed vocabularies

- `OrderState`: CREATED, DENIED, SUBMITTED, ACKED, PARTIALLY_FILLED, FILLED, PENDING_CANCEL,
  PENDING_REPLACE, CANCELLED, REJECTED, EXPIRED, UNKNOWN, VOIDED.
- `ReportKind`: ACK, REJECT, FILL, CANCEL, REPLACE, EXPIRE, CORRECT, BUST, STATUS, RESTATE.
- `Verdict`: ALLOW, ALERT, BLOCK. `CancelRejectReason`: TOO_LATE, UNKNOWN_ORDER, PENDING, DUPLICATE_ID, OTHER.
- `TradingState`: ACTIVE, REDUCING, HALTED, KILLED. `Mode`: SHADOW, PAPER, LIVE.
- `DataState`: LIVE, DEGRADED, STALE, DEAD, CLOSED. `LinkState`: CONNECTED, RECOVERING, DISCONNECTED.
- `BreakClass`: MISSING_LOCAL_ORDER, MISSING_VENUE_ORDER, QTY_MISMATCH, POSITION_MISMATCH,
  BALANCE_MISMATCH, UNKNOWN_FILL.

### 2.3 Config knobs (types, bounds; defaults fail closed)

| Knob | Type / bound | Note |
|---|---|---|
| `mode` | enum SHADOW/PAPER/LIVE, default SHADOW | LIVE additionally needs env + token. |
| `limits.<instrument>.max_order_qty`, `max_order_value` | number > 0, **required** per traded instrument | absence ⇒ BLOCK. |
| `price_collar.pct` / `.reference` | 0 < pct ≤ 1; enum last/mid/prev_close/snapshot | evaluated at send time. |
| `max_position`, `max_gross_exposure`, `max_daily_loss` | number > 0 | working orders count. |
| `message_rate.max`, `.window_s` | int ≥ 1, number > 0 | cancels exempt. |
| `repeated_execution.max` | int ≥ 1 | latching. |
| `data.max_age_s` (per stream), `data.degraded_s` | number > 0, degraded < max | staleness thresholds. |
| `clock.max_skew_ms` | number > 0 | checked at start and periodically. |
| `link.heartbeat_s`, `link.missed_to_disconnect` | 5–60; int ≥ 2 | |
| `dead_man.timeout_s` | number ≥ venue minimum, or null | only if `spec()` allows. |
| `reconcile.on_start` (default true), `.interval_s`, `.lookback_min`, `.break_policy{class: action}` | bool; number > 0; int; enum | |
| `calendar` | reference to the supplied calendar | gate, not schedule. |
| `overrides[]` | `{gate, limit, authorised_by, expires_at}` | temporary by construction. |

### 2.4 Records to persist (append-only, UTC, identity-hash-linked)

Per tick: decision record (inputs hash, `DataState`, decision, resulting intent or none, every gate
verdict with measured value and limit). Per order: local id, client order id, venue id, instrument,
side, qty, price, TIF, every transition with timestamp and report id, `cum/leaves`. Per fill: `exec_id`
(unique), price, qty, fees, position after. Plus: alert log; override log (who/when/why/expiry);
`TradingState` transitions with actor (kill activations especially); reconciliation reports and breaks;
link/feed state changes; the arming token id, config identity hash and training run directory. Five-year
retention is the regulatory reference point [13 Art 28].

### 2.5 Tests to write

State machine: illegal transitions refused; duplicate `exec_id` is a no-op; quantity invariant; pending
resolves only on a final report; UNKNOWN resolves only via `status()`/reconciliation. Gates: one refusal
test per knob; missing per-instrument limit blocks; collar rejects both sides in every session phase;
`MessageRate` never blocks a cancel; throttle stays latched across ticks until re-enable; override past
`expires_at` is inert. Reconciler: seeded mismatches of each `BreakClass` produce the configured action;
start refused while a break is open. Connectivity: simulated heartbeat loss drives LinkState →
TradingState; stale feed → BLOCK; sequence gap → resync before ACTIVE. Arming: `LiveExecutor` cannot be
built without a token; token expiry, config/env/token disagreement each refuse; default constructs
`ShadowExecutor`. Parity: the same node objects under the same identity hash yield identical decisions
in replay and in the serving loop. Pin tests for every value that appears twice (gate vocabulary vs config
schema; `TradingState` names in supervisor vs journal).

### 2.6 Venue-specific residue (stays in the child's `Executor` subclass and config)

Order types and persistence (pegs, auctions, Betfair `LAPSE/PERSIST/MARKET_ON_CLOSE`); side vocabulary
(buy/sell vs yes/no vs back/lay); price units and precision (cents, odds ladders, ticks); lot sizes;
error-code mapping; whether the venue dedups client ids; replace semantics; COD and dead-man availability
and minimums; rate-limit shape; self-trade-prevention modes; netting vs hedging; session calendar; sandbox
existence and its fill model.

---

## 3. Pitfalls and anti-patterns

1. **Soft blocks as the only defence**, and alert UIs that permit bulk dismissal (Citi).
2. **Alerts that are not alerts** — e-mails to a list nobody watches (Knight's 97 BNET rejects).
3. **Monitoring without actuation** — a human-only risk screen that lags and does not show limits (PMON).
4. **Periodic exposure** (Goldman's 30-minute capital usage); exposure must be continuous and include working orders.
5. **Collars against a stale reference or absent in some session phase** (Knight pre-open; Goldman pre-market).
6. **Reusing config bits/flags; leaving dead code callable; manual multi-host deploys that report success on failure.**
7. **Rollback as first response** without a kill — it can widen the blast radius.
8. **Relying on cancel or bust** — the exchange may refuse (Mizuho); COD is best-effort and excludes GTC/GTD.
9. **Resetting sequence numbers on reconnect; treating pending as failure; resending on timeout without checking the venue.**
10. **Inferring "closed" from an open-orders endpoint; short reconciliation lookbacks.**
11. **Backoff logic that delays cancels; treating a latency stopgap as a rate limit.**
12. **Paper P&L as evidence of edge**; sharing credentials or endpoints between paper and live; live reachable by a config typo.
13. **A kill the strategy can override, or that is not attributable per algorithm/version** (RTS 6 Art 12(3)).
14. **Reviews that inventory controls instead of imagining malfunctions**, and fixes scoped to "the exact problem at hand" (SEC on Knight ¶28, ¶44).

---

## 4. Sources

1. FIXSIM, Execution Report fields/states — https://www.fixsim.com/fix-execution-report
2. OnixS FIX 4.4 dictionary, OrdStatus (39) — https://www.onixs.biz/fix-dictionary/4.4/tagNum_39.html
3. OnixS, ExecType (150) — https://www.onixs.biz/fix-dictionary/4.4/tagNum_150.html
4. OnixS, Order Cancel/Replace Request (35=G) — https://www.onixs.biz/fix-dictionary/4.4/msgType_G_71.html
5. OnixS, CxlRejReason (102) — https://www.onixs.biz/fix-dictionary/4.4/tagNum_102.html
6. OnixS, Order Status Request (35=H) — https://www.onixs.biz/fix-dictionary/4.4/msgType_H_72.html
7. Kalshi, Create your first order — https://docs.kalshi.com/getting_started/quick_start_create_order
8. Kalshi, Get orders schema — https://docs.kalshi.com/api-reference/orders/get-orders
9. Betfair Exchange API, placeOrders — https://betfair-developer-docs.atlassian.net/wiki/spaces/1smk3cen4v3lu3yomq5qye0ni/pages/2687496/placeOrders
10. NautilusTrader, Orders (OrderStatus) — https://nautilustrader.io/docs/latest/concepts/orders/
11. NautilusTrader, Positions (netting vs hedging) — https://nautilustrader.io/docs/latest/concepts/positions/
12. FIA, Best Practices for Automated Trading Risk Controls and System Safeguards (July 2024) — https://www.fia.org/sites/default/files/2024-07/FIA_WP_AUTOMATED%20TRADING%20RISK%20CONTROLS_FINAL_0.pdf
13. Commission Delegated Regulation (EU) 2017/589 (RTS 6), adopted text C(2016) 4478 — https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32017R0589 ; per-article: https://www.legislation.gov.uk/eur/2017/589/article/15
14. OnixS, Understanding FIX drop copy — https://www.onixs.biz/insights/understanding-fix-drop-copy-in-financial-trading
15. NautilusTrader, Execution reconciliation — https://nautilustrader.io/docs/latest/concepts/reconciliation/
16. 17 CFR § 240.15c3-5 — https://www.law.cornell.edu/cfr/text/17/240.15c3-5
17. SEC staff FAQ on Rule 15c3-5 — https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0
18. FCA, Algorithmic Trading Compliance in Wholesale Markets (Feb 2018) — https://www.fca.org.uk/publication/multi-firm-reviews/algorithmic-trading-compliance-wholesale-markets.pdf
19. Finnish FSA, Thematic assessment on pre-trade controls (Dec 2024) — https://www.finanssivalvonta.fi/globalassets/fi/tiedotteet-ja-julkaisut/valvottavatiedotteet/2025/teema-arvioraportti_tee-2024-03-en.pdf
20. SEC, In the Matter of Knight Capital Americas LLC, Release 34-70694 — https://www.sec.gov/files/litigation/admin/2013/34-70694.pdf
21. SEC press release 2013-222 — https://www.sec.gov/news/press-release/2013-222
22. "Design Robinhood" (order state machine, saga, drop-copy reconciliation) — https://chiraghasija.cc/posts/design-robinhood-system-design/
23. Speculative Branches, The Knight Capital Disaster — https://specbranch.com/posts/knight-capital/ ; Doug Seven, Knightmare — https://dougseven.com/2014/04/17/knightmare-a-devops-cautionary-tale/
24. Kitchen Soap, Counterfactual thinking and Knight Capital — https://www.kitchensoap.com/2013/10/29/counterfactuals-knight-capital/
25. SEC press release 2015-133 (Goldman Sachs 2013 options incident) — https://sec.gov/news/pressrelease/2015-133.html
26. Linklaters, The Citi fat-finger fines and UI design — https://financialregulation.linklaters.com/post/102j8af/the-citi-fat-finger-fines-and-ui-design
27. Banking Dive, Citi fined $78.4M over 2022 flash-crash error — https://www.bankingdive.com/news/citi-flash-crash-78-million-penalty-british-fca-pra-manual-error-2022/716810/
28. 2010 Flash Crash (SEC/CFTC findings summarised) — https://en.wikipedia.org/wiki/2010_flash_crash
29. Mizuho/J-Com 2005 — https://www.nbcnews.com/id/wbna10394551
30. QuantInsti, Changing notions of risk management (Everbright, HanMag) — https://www.slideshare.net/QuantInsti/changing-notions-of-risk-management-in-financial-markets
31. Coinbase Derivatives FIX session messages — https://docs.cdp.coinbase.com/derivatives/fix/session
32. Medium, How FIX powers modern trading: sessions, reliability, failover — https://medium.com/@s.g.manikandan/how-fix-powers-modern-trading-sessions-reliability-and-failover-explained-aede4ddcdd09
33. cloudlogic.dev, FIX protocol best practices — https://cloudlogic.dev/2025/08/12/fix-protocol-best-practices-for-institutional-trading/
34. NautilusTrader, Live trading — https://nautilustrader.io/docs/latest/concepts/live/
35. B2BITS, Sequence number handling — https://b2bits.atlassian.net/wiki/spaces/B2BITS/pages/6089910/Sequence+number+handling
36. CME Globex notice on Cancel on Disconnect for iLink 3 CGW — https://www.cmegroup.com/notices/electronic-trading/2023/11/20231127.html ; CME COD wiki — https://www.cmegroup.com/confluence/display/EPICSANDBOX/Cancel+on+Disconnect
37. Kraken, Dead man's switch (cancelAllOrdersAfter) — https://docs.kraken.com/api/docs/websocket-v1/cancelallordersafter/
38. Binance Options, Auto-Cancel All Open Orders heartbeat — https://developers.binance.com/docs/derivatives/options-trading/market-maker-endpoints/Auto-Cancel-All-Open-Orders-Heartbeat
39. EODHD, Stale price detection, REST fallback, WebSocket recovery — https://eodhd.com/financial-academy/fundamental-analysis-examples/real-time-market-data-reliability-stale-price-detection-rest-fallback-and-websocket-recovery
40. Data Intellect, Measuring stale data in trading systems — https://dataintellect.com/blog/stale-data-measuring-what-isnt-there/
41. Binance, How to manage a local order book correctly — https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/web-socket-streams.md
42. MiFID II RTS 25 clock synchronisation summary — https://www.online-ntp-validator.com/mifid-ii-clock-synchronization-rts-25.html
43. Binance, REST limits — https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits ; eToro, Rate limits & 429 playbook — https://builders.etoro.com/learn/rate-limits-and-429-handling.md
44. Polymarket US, Rate limits — https://docs.polymarket.us/api-reference/rate-limits
45. NautilusTrader, RiskEngineConfig / TradingState — https://nautilustrader.io/docs/nightly/api_reference/config/ ; https://nautilustrader.io/docs/latest/api_reference/risk/
46. Alpaca, Paper trading — https://docs.alpaca.markets/us/docs/paper-trading
47. IBKR, Paper trading limitations (TWS API) — https://www.interactivebrokers.com/docs/tws-api/doc/notes-limitations/limitations/paper-trading
48. IBKR, About paper trading accounts — https://www.ibkrguides.com/clientportal/aboutpapertradingaccounts.htm
49. Alpaca, Paper trading vs live trading: a data-backed guide — https://alpaca.markets/learn/paper-trading-vs-live-trading-a-data-backed-guide-on-when-to-start-trading-real-money
50. NinjaTrader, Shadow strategy — https://ninjatrader.com/support/helpguides/nt8/shadow_strategy.htm
