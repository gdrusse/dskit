# R5 — Guardrails, limits, kill switches and safe degradation

**Scope.** The refusal layer between a model's proposal and an executed action, for
ANY live decision application (equities, prediction markets, sports betting, a
non-financial decision system). Researched 2026-09-04 for the design of
`dskit.production`. Everything below is domain-neutral; venue names appear only as
evidence of what practitioners actually enforce.

---

## 1. Findings by theme

### 1.1 The limit taxonomy used in practice

Regulators, industry bodies and open-source frameworks converge on one short list.

**MiFID II RTS 6 (EU, binding on algorithmic traders).** Art. 15 mandates, per
instrument: (a) *price collars* "which automatically block or cancel orders that do
not meet set price parameters ... both on an order-by-order basis and over a
specified period of time"; (b) *maximum order values*; (c) *maximum order volumes*;
(d) *maximum message limits*; and, Art. 15(3), *repeated automated execution
throttles* "which control the number of times an algorithmic trading strategy has
been applied. After a pre-determined number of repeated executions, the trading
system shall be automatically disabled until re-enabled by a designated staff
member." Art. 15(4) requires *market and credit risk limits* scaled to capital,
experience and "reliance on third party vendors"; Art. 15(5) requires automatic
block/cancel "where those orders risk compromising the investment firm's own risk
thresholds", applied "on exposures to individual clients, financial instruments,
traders, trading desks or the investment firm as a whole" (i.e. per-key AND
aggregate scope). Art. 8 (controlled deployment) requires *pre-deployment* limits on
"the number of financial instruments being traded; the price, value and numbers of
orders; the strategy positions; and the number of trading venues". Art. 17
(post-trade) adds continuous exposure assessment, "maximum long and short and
overall strategy positions", and *real-time reconciliation* of the firm's own logs
against venue/broker/clearer records. [S1]

**SEC Rule 15c3-5 (US market access).** Controls must be applied *pre-trade*,
"systematically limit the financial exposure", "prevent the entry of orders that
exceed appropriate pre-set credit or capital thresholds in the aggregate", prevent
erroneous orders (size, price, duplicates), and be under the broker's "direct and
exclusive control". [S2][S3]

**FIA 2024 best practices** carry the same list with operational teeth: *maximum
order size* ("fat-finger"; **"Systems should prevent orders from being placed in
cases where no order size limits have been set for an instrument"**); *maximum
intraday position* (**"both current positions and working orders should be
evaluated"**; "Warnings may be employed when the limit is close to being breached";
limits managed by "authorized staff independent of trading activities"); *price
tolerance* vs a reference price; *cancel-on-disconnect*; *kill switches*; *market
data reasonability*; *repeated automated execution limits*; *message throttles*
(**"a message throttle should never reject an order cancellation request"**);
*self-match prevention*; *drop-copy reconciliation*. [S4]

**Frameworks.** NautilusTrader's `RiskEngine` sits on the submit/modify path in
backtest, sandbox and live alike and checks instrument existence, price/quantity
precision and min/max, `max_notional_per_order` (per instrument),
`max_order_submit_rate` / `max_order_modify_rate` (default 100/s), reduce-only
consistency and the trading state; failure emits `OrderDenied` with a standardized
reason code; **cancels bypass the engine entirely**. [S5][S6] Freqtrade's
"protections" are windowed limits with a lock: `StoplossGuard` (N stop-loss exits in
`lookback_period` → lock for `stop_duration`), `MaxDrawdown` (peak-to-trough over the
lookback), `LowProfitPairs`, `CooldownPeriod`; each takes `lookback_period[_candles]`,
`trade_limit`, `stop_duration[_candles]`, `unlock_at "HH:MM"`, `only_per_pair`,
`only_per_side`; they are "evaluated in the sequence they are defined" and may be
stacked with different windows. [S7] Hummingbot's kill switch is one number,
`kill_switch_rate` (e.g. −5%), on continuously mark-to-market performance — it trips
without a new trade. [S8] flumine (Betfair) validates every order through *client
controls* (`MaxTransactionCount` vs the venue's 5,000/hour) and *trading controls*:
`OrderValidation` (size, price ladder, minimum stake), `MarketValidation` (market
open), `ExecutionValidation` ("OrderStream is not connected, execution of orders is
blocked"), `StrategyExposure` (`max_order_exposure`, `max_selection_exposure`,
`max_market_exposure`, custom `validate_order`); a failure sets status `Violation`
with `violation_msg`; `force=True` bypasses controls so that cancels always go
through. Custom controls subclass `BaseControl` and implement
`_validate(order, package_type)`. [S9][S10]

**Betting-specific.** Full Kelly "requires accurate probability values"; an
overestimated p "increasing the risk of ruin"; practitioners use half/quarter Kelly
"to reduce the chance of ruin, reduce volatility, and account for model error", and
add a hard cap of a few percent of bankroll per bet regardless of the formula.
[S11][S12] Venue constraints: minimum stake (£2 on Betfair), 1,000
transactions/second rate limit, a *heartbeat API* that cancels unmatched LIMIT bets
if the client stops heartbeating (`preferredTimeoutSeconds` → `actualTimeoutSeconds`,
with `actionPerformed` reporting NONE / submitted / failed / some-not-cancelled),
and sportsbooks that reject on "price changed" — so *odds staleness* (quote age vs
acceptance latency) is a real check, not a nicety. [S13][S14][S15]

**Prediction-market-specific.** Prices are probabilities: the venue rejects orders
outside [0.01, 0.99] and tightens tick size near the extremes; per-market *position
limits* exist (commonly $25k, larger for some markets); rate limits are token
buckets with separate read/write budgets; exposure is worst-case loss at
*resolution*, and every market has a hard end after which no action exists.
[S16][S17][S18]

**Generic vs domain-specific.** Every item above is *a numeric measure of
(proposal, state), over a window, against a bound, at a scope*: order size,
notional, price deviation from reference, position/exposure **including working
orders**, P&L per period, drawdown, consecutive losses, decision rate, message rate,
open-order count, repeat count, concentration, leverage, data age, model
confidence. What differs by domain is only (i) how the measure is computed — equity
notional = price×qty; lay-bet liability = stake×(odds−1); binary-contract worst
case = qty×price; Kelly fraction from (p, odds) — (ii) the reference for collars,
and (iii) which *calendar* keys the windows (session, event start, resolution).

### 1.2 Kill switches and halts

**What the law asks.** RTS 6 Art. 12: the firm "shall be able to cancel immediately,
as an emergency measure, any or all of its unexecuted orders submitted to any or all
trading venues", including orders from individual traders/desks/clients, and must
"identify which trading algorithm and which trader ... is responsible for each
order" — i.e. attribution is a precondition for a *granular* kill. Art. 16: real-time
monitoring by the trader AND an independent risk function ("two lines of defence"),
"real-time alerts shall be generated within five seconds after the relevant event",
and remedial action "including, where necessary, an orderly withdrawal from the
market". [S1] ESMA's 2026 briefing splits controls into **hard blocks** (mandatory;
traders may not override "either directly ... or indirectly (e.g. by slicing blocked
orders to circumvent the set parameters)"; parameters set jointly by trading, risk
and compliance; "where possible ... hard coded within an algorithm") and **soft
blocks** (lower thresholds; alert; override "require[s] active input from the
trader"); firms must "collect statistics ... indicating at least the number of times
[each control has] been triggered"; and "changing thresholds, kill switch logic, or
alert triggers" is a material change that must be re-tested before deployment. [S19]

**Industry definition.** FIA: a kill switch "immediately disables all trading
activity for a particular participant ... typically preventing the ability to enter
new orders and cancelling all working orders. It also may allow for risk-reducing
orders while preventing risk-increasing orders." It is "a last resort when other
actions have failed", should be "granular ... to identify individual trading
systems", operable "both by the trader and by the person responsible for risk", with
"a registration process and entitlement system" naming who may press it, "explicit
warnings informing the authorized users of the consequences", and a trader "should
not be able to override a kill switch invoked by the broker". [S4]

**Exchange-level breakers.** Single-stock LULD bands (5% / 10% around a 5-minute
reference) and market-wide halts at 7% / 13% / 20% (15-minute halts; level 3 closes
the day). During a halt, cancels and modifications of resting orders are accepted;
"all other new orders will be rejected". [S20][S21] Cancel-on-disconnect (CME:
enabled by default; cancels resting orders for the disconnected session, not GTC/GTD;
"it is the user's responsibility to reenter") and Betfair's heartbeat cancel
**orders, never positions**, and both venues warn cancellation is best-effort. [S22][S14]

**What a halt must do — and why not flatten.** NautilusTrader's trading states are
the cleanest model: `ACTIVE` ("submit and modify commands operate normally"),
`REDUCING` ("only submit or modify commands that do not increase exposure are
accepted"), `HALTED` ("new submit and modify commands are denied. Cancels still pass
through"). [S5] Flattening is *itself* a risk-taking act: market orders "can fill
materially worse than the last quoted price, particularly in fast, gapping, or
illiquid markets" [S23]; Knight's remediation under fire — uninstalling the new code
from the seven healthy servers — "worsened the problem" [S24]. FIA notes COD "adds to
risk" in some situations, which is why it is optional. Conclusion: *halt* = refuse
new decisions + cancel working orders (best effort, outcome recorded) + optionally
enter reduce-only; *flatten* is a separate act a human initiates.

**Re-arming.** RTS 6 15(3): "disabled until re-enabled by a designated staff
member"; FIA 3.2: "until an authorized person re-enables it"; prop-firm daily-loss
lockouts hold "until the next trading session" and are distinct from the
account-ending maximum drawdown [S25]. Software circuit breakers (Fowler) self-reset
through a half-open trial call, but "Operations staff should be able to trip or reset
breakers" [S26]. The distinction to carry into design: **throttles and cooldowns
auto-expire** (Freqtrade `stop_duration`, `unlock_at`); **kills never self-reset.**

**Knight Capital, 1 Aug 2012** (SEC order): 212 parent orders → millions of child
orders, 4 million executions, ~$3.5bn long / ~$3.15bn short, $460M loss in 45
minutes. The order's specific findings [S24]: (a) "did not have sufficient controls
to monitor the output from SMARS, such as a control to compare orders leaving SMARS
with those that entered it"; (b) "did not have procedures in place to halt SMARS's
operations in response to its own aberrant activity"; (c) the 9.5% price collar "did
not apply to orders ... intended to ... participate in the opening auction"; (d)
position limits "did not account for the firm's exposure from outstanding orders";
(e) no aggregate capital threshold "linked to automated controls that would prevent
the entry of orders" — the risk tool PMON was post-execution, "relied entirely on
human monitoring", "did not generate automated alerts", "did not display the
limits", and lagged under load; (f) 97 "Power Peg disabled" e-mails before the open
that "Knight did not design ... to be system alerts"; (g) no incident-response
procedure — the firm "needed clear guidance ... as to when to disconnect a
malfunctioning system"; (h) an earlier loss where a desk "mistakenly continued to use
the test data" and "did not have a mechanism to test whether their systems were
relying on stale data"; (i) no second-technician deployment review.

### 1.3 Data-quality gates and "no decision" as an outcome

FIA 3.1: validate incoming data on "the time since the last update was received,
previous price, bid/offer spread or deviation from an average price"; on deviation
"an alert should be provided flagging market data may be stale, and any orders
should be blocked while the deviation is investigated". [S4] ESMA lists "coverage
checks for positions in securities or in cash, warnings in case of old data feeds"
among expected controls. [S19] Practice: a per-feed ladder LIVE (age ≤ t₁) →
DEGRADED (≤ t₂) → STALE (fallback requested) → DEAD ("block trading") →
RECOVERING (require N consecutive fresh ticks before LIVE again); "reject ticks older
than the latest accepted timestamp"; label every value's provenance; "do not let
stale prices trigger alerts, trades, or automated decisions". [S27] Fixed age
thresholds misfire in quiet markets; fitting each feed's inter-arrival distribution
gives a probability-based staleness alarm that distinguishes "quiet" from "broken".
[S28] Schema-level checks (TFDV): missing features, type mismatch, out-of-range
numerics, out-of-domain categoricals, presence below a minimum fraction,
training/serving skew, drift between spans. [S29]

Model-output sanity: a probability-priced venue refuses prices outside [0.01,
0.99] [S17]; NaN/out-of-range outputs must be refused before sizing; model-derived
sizes must be capped (Kelly). Abstention is settled theory — Chow's reject option,
selective prediction with a confidence threshold τ ("predict ... if its associated
confidence is at least τ; otherwise the model abstains"), SelectiveNet — trading
coverage for lower risk. [S30][S31] So "no decision" is a *proposal outcome* to
record and count, never an error path.

### 1.4 Mode ladders and promotion

Frameworks: Freqtrade `dry_run` "Defaults to true"; live needs `dry_run: false` AND
exchange keys AND a fresh database, with the warning "the bot will engage your money"
[S32]. Hummingbot paper-trade accounts with editable simulated balances [S33].
NautilusTrader runs the same RiskEngine in backtest, sandbox and live [S5]. flumine:
`paper_trade=True` (live data, simulated execution) and native simulation [S9]. RTS 6
Art. 8 is the regulatory form of "live, small": predefined limits on instruments,
price/value/number of orders, positions and venues *before* deployment [S1]. ML
delivery: shadow mode ("production traffic and data is run through a newly deployed
version ... without that service or model actually returning the response") then
canary (route a small population, monitor, widen or roll back) [S34][S35]. Arming
controls: the two-man rule (no single individual can perform the critical act; launch
keys "positioned too far apart for one person to reach both") [S36]; maker-checker
("no self-approval", "segregation of duties ... enforced at the code level", immutable
audit of who/when) [S37]; break-glass / just-in-time access (grants that expire
automatically, e.g. after 30 minutes, every request and approval logged) [S38]. The
dskit children already sit on the bottom rung: `TradingClient(..., paper=True)`
hardcoded, "refusal by default", and the closeout ruling that `paper=True` stays.

### 1.5 Anomalies on the decision stream

Recognised patterns: *repeated identical orders* ("a common indication of a faulty
algorithm"; count per instrument per unit time, both frequency and total quantity)
[S39]; *repeated automated execution* (an identical order "filled and then re-enters
the market without human intervention") [S4]; *message-rate throttles* and
venue-level *order-to-trade ratios* (RTS 9); *self-match prevention* (a loop that
crosses itself); *action-vs-proposal reconciliation* (Knight's missing "orders
leaving vs entering" control; RTS 6 17(3) real-time reconciliation with venue
records). **Zillow Offers** is the non-market analogue: the model's error exceeded the
margin, adverse selection meant only overpriced offers were accepted, management
"started bidding more for cookie-cutter homes than what their algorithmic model
predicted", volume scaled while error widened, "no evidence suggests Zillow
implemented circuit-breakers when forecast error widened" — $569M of write-downs.
[S40][S41] Guards that would have caught each: a rolling realised-vs-predicted error
bound; an acceptance-rate anomaly; a cap on *new* exposure per period (ramp); a
proposal-vs-action divergence check; a repeat/flip counter.

### 1.6 Human in the loop

RTS 6 15(6): submitting a blocked order is permitted only "in relation to a specific
trade on a temporary basis and in exceptional circumstances ... subject to
verification by the risk management function and authorisation by a designated
individual" [S1]; ESMA soft blocks "require active input from the trader to be
overridden" [S19]. LangGraph's `interrupt()` shows the mechanics: pause, persist the
exact state through a checkpointer so the pause survives a restart, resume with
`Command(resume=...)`, decisions approve / reject / edit — and the caveat that the
node "restarts ... from the beginning" on resume, so "side effects called before
interrupt must be idempotent" [S42]. Maker-checker: the initiator cannot approve;
"log every state transition with immutable timestamps and user identifiers" [S37].

---

## 2. Design implications for `dskit.production`

### 2.1 The `Guard` ABC and a closed verdict vocabulary

One abstract hook, `check(proposal, state) -> Finding`, on a `Guard(ABC)` that
subclasses `Node` conventions (class-level `role`, default-deny params tuple,
`validate_params`). No `if kind ==` anywhere: each guard is a subclass or a
registry entry; measures are strategy objects.

Closed verdict vocabulary, ordered as a severity lattice (the chain's composite
verdict is the maximum; **every guard runs and every finding is recorded** — ESMA's
per-control trigger statistics need the losers too):

| verdict | meaning | who acts |
|---|---|---|
| `allow` | pass unchanged | executor |
| `warn` | pass; alert + count toward escalation (ESMA soft block) | monitor |
| `amend` | pass a *reduced* proposal (clip to bound); original + amended both recorded; opt-in per limit, never for hard limits | executor |
| `refuse` | this proposal dies with a reason; the loop continues (flumine `Violation`, Nautilus `OrderDenied`) | — |
| `hold` | needs a human; queued with a TTL; expires as `refuse` | approver |
| `halt` | trip the breaker: no further proposals; cancel working orders per policy; human re-arm | operator |

`Finding = {guard, measure, value, bound, window, scope_key, verdict, reason}`.
"No decision" is a proposal with `action: "none"`, allowed and counted — not a
verdict. `halt` maps onto the pipeline's existing "a halt is a result" exit code 3.

### 2.2 The universal proposal and state

A proposal carries only domain-neutral numbers the child computes: `id`, `ts`,
`key` (instrument/market/event), `action ∈ {open, increase, reduce, close, none}`,
`quantity`, `price | null`, `reference_price`, `exposure` (worst-case loss the child
defines), `direction`, `confidence | null`, `model_id`, `inputs_ts` (age of the
newest input), `notes`. `state` is read-only: positions and working orders per key,
realised/unrealised P&L series, decision/receipt history, feed ages, calendar,
breaker + arming state. `reduces_exposure(proposal, state)` is the one mechanism the
core owns generically (sign of delta vs position) so `REDUCING` mode works for any
domain.

### 2.3 The generic `Limit` family: measure × window × bound × scope

One concrete `Limit(Guard)` parameterised, not one class per rule:

```jsonc
"guards": {
  "size":     {"uses": "limit", "params": {"measure": "quantity", "bound": {"max": 100}, "on_breach": "refuse"}},
  "notional": {"uses": "limit", "params": {"measure": "exposure", "bound": {"max": 1000}, "warn_at": 0.8, "on_breach": "refuse"}},
  "collar":   {"uses": "limit", "params": {"measure": "price_deviation", "bound": {"max": 0.02}, "on_breach": "refuse"}},
  "position": {"uses": "limit", "params": {"measure": "exposure_after", "scope": "per_key", "include_working": true, "bound": {"max": 5000}, "on_breach": "refuse"}},
  "gross":    {"uses": "limit", "params": {"measure": "exposure_after", "scope": "aggregate", "bound": {"max": 20000}, "on_breach": "refuse"}},
  "day_loss": {"uses": "limit", "params": {"measure": "pnl", "window": {"calendar": "session"}, "bound": {"min": -500}, "on_breach": "halt"}},
  "drawdown": {"uses": "limit", "params": {"measure": "drawdown", "window": {"duration": "P7D"}, "bound": {"max": 0.1}, "on_breach": "halt"}},
  "streak":   {"uses": "limit", "params": {"measure": "consecutive_losses", "bound": {"max": 5}, "on_breach": "pause", "pause": {"calendar": "next_session"}}},
  "rate":     {"uses": "limit", "params": {"measure": "decision_count", "window": {"duration": "PT1M"}, "bound": {"max": 10}, "on_breach": "refuse"}},
  "repeat":   {"uses": "limit", "params": {"measure": "identical_count", "window": {"count": 20}, "bound": {"max": 3}, "on_breach": "halt"}},
  "flip":     {"uses": "limit", "params": {"measure": "direction_changes", "scope": "per_key", "window": {"duration": "PT10M"}, "bound": {"max": 2}, "on_breach": "pause", "pause": {"duration": "PT30M"}}},
  "open":     {"uses": "limit", "params": {"measure": "open_orders", "bound": {"max": 5}, "on_breach": "refuse"}},
  "stale":    {"uses": "limit", "params": {"measure": "input_age", "bound": {"max": "PT30S"}, "on_breach": "refuse"}},
  "dead":     {"uses": "limit", "params": {"measure": "feed_age", "bound": {"max": "PT5M"}, "on_breach": "halt"}},
  "sane":     {"uses": "range", "params": {"field": "confidence", "min": 0, "max": 1, "nan": "refuse"}},
  "abstain":  {"uses": "limit", "params": {"measure": "confidence", "bound": {"min": 0.55}, "on_breach": "refuse"}},
  "approve":  {"uses": "limit", "params": {"measure": "exposure", "bound": {"max": 2500}, "on_breach": "hold", "hold": {"ttl": "PT10M"}}},
  "kelly":    {"uses": "limit", "params": {"measure": "bankroll_fraction", "bound": {"max": 0.03}, "on_breach": "amend"}}
}
```

Knob shapes (types/bounds, default-deny, refused if unknown):
`measure` — name from a core registry of stdlib measures over proposal/state fields
(`quantity, exposure, exposure_after, price_deviation, pnl, drawdown,
consecutive_losses, decision_count, identical_count, direction_changes, open_orders,
input_age, feed_age, confidence, bankroll_fraction, error_vs_realised,
acceptance_rate`) or a `pkg.module:Measure` the child supplies (the pyomo "doorway"
pattern: mechanism in the pack, formula in the child). `window` — exactly one of
`{}` (instantaneous), `{"duration": ISO-8601}`, `{"count": int ≥ 1}`,
`{"calendar": "session" | "day" | "event"}` (resolved by the *supplied* calendar,
never a clock). `bound` — `max` and/or `min`, numbers or durations, inclusive.
`warn_at` — fraction in (0, 1) of the bound at which a `warn` fires (the soft
block). `scope` — `"aggregate"` | `"per_key"` | `{"group": "<field>"}`.
`include_working` — bool, default `true` (Knight/FIA). `on_breach` —
`refuse | amend | pause | hold | halt`; `amend` valid only for measures with a
single scalable field; `pause` needs `pause: {duration | calendar}`; `hold` needs
`hold: {ttl}`. Numbers in the example are illustrations, not defaults — code holds
no thresholds, and a document that arms beyond `shadow` without at least one
per-proposal size limit and one period loss limit is refused at plan time (FIA:
"prevent orders ... where no order size limits have been set").

### 2.4 Two state machines: the mode ladder and the breaker

Keep them orthogonal; effective permission = `min(mode, breaker)`.

**Mode ladder** (who may act on a proposal): `shadow` (record only) → `paper`
(simulated executor) → `live_limited` (real executor; allow-list of keys; a tighter
limits overlay; expiry) → `live`. Promotion is one rung at a time, by a human, as a
*signed arming record*, never a config key: `{mode, armed_by: [principal,
principal], armed_at, armed_until, allowlist, limits_overlay, config_identity}`. Two
distinct principals for anything ≥ `live_limited` (two-man rule / maker-checker:
the proposer of the arming cannot be its approver). `armed_until` is mandatory and
checked on *every* proposal (break-glass expiry); the arming is bound to the
config's identity hash so a changed document is not armed. Demotion is free: any
actor, any time, no ceremony — the safe direction is always open.

**Breaker** (whether anything may act at all): `active` → `reducing` → `halted`.
Automatic trips: any guard's `halt`, feed `DEAD`, executor heartbeat lost,
reconciliation mismatch (receipts ≠ proposals), repeat/flip anomaly. Operator
trips: a file flag, a signal and a CLI verb, each recorded with principal + reason.
`halted` means: no new proposals; cancels always pass (never throttled); working
orders cancelled on entry per `on_halt: {"cancel_open": true, "flatten": false}`
with the cancellation *outcome* recorded (`none | submitted | all_failed |
partial | unknown`, Betfair's vocabulary); `flatten` never defaults on — a human
enters `reducing` and the model's reduce-only proposals do it under the same
guards. **Reset is human-only**: `halted → active` requires a reset record naming
the trip it acknowledges, after `cooling_off` has elapsed; never a timer, never a
restart (the breaker state is persisted and reloaded, so a crash-loop cannot re-arm
itself). `pause` is the auto-expiring cousin: it lives in guard state, not the
breaker, and lifts at its duration/calendar boundary.

**Executor object.** `Executor(ABC)`: `submit(proposal) -> receipt`,
`cancel(scope) -> outcome`, `working()`, `positions()`, `heartbeat()`. Core ships
`ShadowExecutor` and `PaperExecutor`; the child supplies the live one. Moving real
money is loud and declared: a live executor refuses to construct without an
arming record ≥ `live_limited` whose expiry is in the future and whose
`config_identity` matches; every live `submit` logs at WARNING with the arming id;
there is no config key that turns paper into live — `paper=True` stays hardcoded
in children, exactly as today.

### 2.5 Records (every refusal carries its reason)

Append-only JSONL, one file per stream, keyed by config identity + asof:
`decisions.jsonl` (proposal hash, all findings, composite verdict, mode, breaker
state, arming id, receipt or `shadow`), `trips.jsonl` (what tripped, values,
cancellation outcome), `arming.jsonl` (every promotion/demotion with principals and
expiry), `approvals.jsonl` (hold → approve/reject/expire, principal ≠ proposer),
`breaker.json` (current state — the file a restart reloads), plus per-guard trigger
counts for recalibration (ESMA §92). Idempotency: the executor keys submissions by
`proposal.id` so a resume after a `hold` cannot double-submit.

### 2.6 Tests to write

Lattice: composite = max, order pinned by one tuple. Every non-`allow` finding
carries `value`, `bound`, `reason`. `halt` persists across process restart; reset
without a human record, before cooling-off, or naming a stale trip is refused.
Arming: two distinct principals; expiry enforced per proposal; identity mismatch
refuses; demotion always succeeds. Executor: live constructor refuses without a
valid arming; cancels pass in `halted`; `reducing` refuses any proposal that
`reduces_exposure` says increases. Limits: `include_working` counts open orders;
calendar windows reset at the supplied session boundary, not midnight; `amend` never
exceeds the bound and records the original; hypothesis property — for any P&L
sequence, `day_loss` halts before the period loss exceeds `bound − max single
loss`. Anomalies: an identical-proposal loop trips within `bound` repeats; a
flip-flop sequence pauses. Data: stale input refuses, dead feed halts, NaN/out-of-range
refuses, abstention is allowed and counted. Plan-time: a document armed beyond
`shadow` with no size limit or no loss limit is refused; unknown knobs are refused
(default-deny); a pin test that `Limit._PARAMS` and the documented knob table agree.

---

## 3. Pitfalls and anti-patterns

1. **Monitoring not wired to entry.** Knight's PMON watched positions but could not
   stop orders; a limit that only alerts is not a limit.
2. **Alerts nobody owns.** 97 e-mails before the open. Every `warn`/`halt` needs a
   named recipient and a recorded acknowledgement.
3. **Exposure that ignores working orders.** Both Knight and FIA: count what is
   resting, not just what is filled.
4. **Collars that skip a regime** (pre-open, auction, illiquid) or float on a stale
   reference. The reference price needs its own age check.
5. **Remediation that adds activity** — auto-flatten, uninstalling code mid-incident.
   Halt stops; it does not trade.
6. **Self-resetting kills.** Half-open is for dependency breakers, not for money.
   Throttles expire; kills do not.
7. **One flag between paper and live** (`dry_run: false`). Arming must be a signed,
   expiring, two-principal record bound to the config's identity — never a knob.
8. **Silent clipping / slicing.** ESMA forbids circumventing hard blocks by slicing;
   `amend` is opt-in, recorded, and never on hard limits.
9. **The trader calibrates the limits.** Independent second line; changes to
   thresholds are material and re-tested.
10. **Unknown key ⇒ no limit ⇒ allowed.** Default-deny: no limit, no order.
11. **No stale-data test.** Knight quoted off test data in 2011; label provenance,
    gate on age, distinguish quiet from dead.
12. **Throttles that reject cancels.** Cancels bypass everything.
13. **Sizing straight from the model.** Cap fractions; refuse NaN; bound probabilities.
14. **Overriding the model under commercial pressure and scaling while error widens**
    (Zillow). A realised-error breaker and a ramp cap exist to say no to the owner.
15. **Non-idempotent side effects before an approval pause** — double submits on resume.
16. **False halts from quiet markets** erode trust until someone disables the guard;
    model the cadence, do not hardcode an age.
17. **Thresholds in code.** Every number above is config; code holds mechanisms.
18. **A kill with no attribution.** Without per-strategy/per-key identity you can
    only kill everything.
19. **Recording the verdict without the value.** No value, no recalibration.
20. **Trusting best-effort cancellation.** Record the outcome; assume "unknown" until
    reconciled.

---

## 4. Sources

- [S1] RTS 6 — Commission Delegated Regulation (EU) 2017/589, Arts. 8, 12, 15, 16, 17: https://ec.europa.eu/finance/securities/docs/isd/mifid/rts/160719-rts-6_en.pdf
- [S2] 17 CFR § 240.15c3-5 (Market Access Rule): https://www.law.cornell.edu/cfr/text/17/240.15c3-5
- [S3] SEC staff FAQ on Rule 15c3-5: https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0
- [S4] FIA, *Best Practices for Automated Trading Risk Controls and System Safeguards* (July 2024): https://www.fia.org/sites/default/files/2024-07/FIA_WP_AUTOMATED%20TRADING%20RISK%20CONTROLS_FINAL_0.pdf
- [S5] NautilusTrader — Execution concepts (RiskEngine, TradingState, OrderDenied): https://nautilustrader.io/docs/latest/concepts/execution/
- [S6] NautilusTrader — Risk API reference (RiskEngineConfig): https://nautilustrader.io/docs/latest/api_reference/risk/
- [S7] Freqtrade — Plugins / Protections: https://www.freqtrade.io/en/stable/plugins/
- [S8] Hummingbot — Kill Switch: https://hummingbot.org/client/global-configs/kill-switch/ ; Paper Trade: https://hummingbot.org/client/global-configs/paper-trade/
- [S9] flumine — Controls: https://betcode-org.github.io/flumine/controls/
- [S10] flumine — `tradingcontrols.py`: https://raw.githubusercontent.com/betcode-org/flumine/master/flumine/controls/tradingcontrols.py
- [S11] Kelly criterion (fractional Kelly, estimation error): https://en.wikipedia.org/wiki/Kelly_criterion
- [S12] Pinnacle — fractional Kelly: https://www.pinnacle.com/betting-resources/en/betting-strategy/revisiting-the-kelly-criterion-part-2-fractional-kelly/gbd27z9nljvgflgg ; per-bet caps: https://www.betstamp.com/education/kelly-criterion
- [S13] Betfair — TOO_MANY_REQUESTS / transaction limits: https://support.developer.betfair.com/hc/en-us/articles/360000406111-Why-am-I-receiving-the-TOO-MANY-REQUESTS-error
- [S14] Betfair — Heartbeat API: https://docs.developer.betfair.com/display/1smk3cen4v3lu3yomq5qye0ni/Heartbeat+API
- [S15] "Price changed" rejections at sportsbooks: https://www.covers.com/industry/why-my-sports-bet-accepted-then-rejected-common-causes-explained
- [S16] Kalshi — Rate limits and tiers: https://docs.kalshi.com/getting_started/rate_limits ; position limits: https://near.blog/kalshi-cftc-market-limits/ ; rulebook (position accountability): https://www.cftc.gov/filings/orgrules/rules1114248723.pdf
- [S17] Polymarket — price bounds 0.01–0.99 (CLOB rejection): https://github.com/Polymarket/py-clob-client/issues/218 ; rate limits: https://docs.polymarket.com/quickstart/introduction/rate-limits
- [S18] Polymarket — no size limits: https://docs.polymarket.com/polymarket-learn/trading/no-limits
- [S19] ESMA, *Supervisory Briefing on Algorithmic Trading in the EU* (Feb 2026), §§70–99: https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf
- [S20] Investor.gov — circuit breakers / LULD: https://www.investor.gov/introduction-investing/investing-basics/glossary/stock-market-circuit-breakers
- [S21] NYSE — Market-Wide Circuit Breakers FAQ (order handling during halts): https://www.nyse.com/publicdocs/nyse/NYSE_MWCB_FAQ.pdf
- [S22] CME — Cancel on Disconnect: https://cmegroupclientsite.atlassian.net/wiki/display/EPICSANDBOX/Cancel+on+Disconnect
- [S23] Flatten-all mechanics and market-order caveats: https://staxinvesting.com/blog/what-is-flatten-all-in-trading-meaning-and-uses-explained
- [S24] SEC, *In the Matter of Knight Capital Americas LLC*, Release 34-70694 (2013): https://www.sec.gov/files/litigation/admin/2013/34-70694.pdf
- [S25] Prop-firm daily loss limit vs maximum drawdown (lockout semantics): https://the5ers.com/prop-firm-drawdown-rules-explained-daily-max-and-trailing-limits-in-2026/
- [S26] Fowler — Circuit Breaker: https://martinfowler.com/bliki/CircuitBreaker.html
- [S27] EODHD — stale price detection, state ladder: https://eodhd.com/financial-academy/fundamental-analysis-examples/real-time-market-data-reliability-stale-price-detection-rest-fallback-and-websocket-recovery
- [S28] Data Intellect — measuring stale data (inter-arrival model): https://dataintellect.com/blog/stale-data-measuring-what-isnt-there/
- [S29] TensorFlow Data Validation — anomaly types: https://www.tensorflow.org/tfx/guide/tfdv
- [S30] Optimal strategies for reject-option classifiers (JMLR 2023): https://jmlr.org/papers/volume24/21-0048/21-0048.pdf
- [S31] SelectiveNet (Geifman & El-Yaniv 2019): https://arxiv.org/pdf/1901.09192
- [S32] Freqtrade — Configuration (`dry_run` default, going live): https://www.freqtrade.io/en/stable/configuration/
- [S33] Hummingbot — Paper Trade: https://hummingbot.org/client/global-configs/paper-trade/
- [S34] Shadow-mode deployment for ML: https://christophergs.com/machine%20learning/2019/03/30/deploying-machine-learning-applications-in-shadow-mode/
- [S35] Fowler — Canary Release: https://martinfowler.com/bliki/CanaryRelease.html
- [S36] Two-man rule: https://en.wikipedia.org/wiki/Two-man_rule
- [S37] Maker-checker (four-eyes): https://en.wikipedia.org/wiki/Maker-checker ; implementation notes: https://www.opcito.com/blogs/maker-checker-implementation-guide-for-secure-fintech-systems
- [S38] Break-glass / just-in-time access with automatic expiry: https://www.britive.com/resource/blog/break-glass-account-management-best-practices ; https://www.lumos.com/blog/planning-for-emergency-access
- [S39] Real-time risk controls patent citing the 15c3-5 "repeat order / runaway algorithm test": https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10489857
- [S40] Stanford GSB — why Zillow Offers imploded: https://gsb.stanford.edu/insights/flip-flop-why-zillows-algorithmic-home-buying-venture-imploded
- [S41] insideAI News — Zillow Offers post-mortem: https://insideainews.com/2021/12/13/the-500mm-debacle-at-zillow-offers-what-went-wrong-with-the-ai-models/
- [S42] LangGraph — interrupts (human-in-the-loop, idempotency caveat): https://docs.langchain.com/oss/python/langgraph/interrupts
- Also consulted: CFTC Electronic Trading Risk Principles (2020): https://www.federalregister.gov/documents/2020/07/15/2020-14381/electronic-trading-risk-principles ; FINRA Market Access topic page: https://www.finra.org/rules-guidance/key-topics/market-access ; Cboe LULD FAQ: https://www.cboe.com/document/tech-spec/document/technical-specifications/cboe-limit-updown-faq
