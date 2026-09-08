# Intraday-equities MIO: technical implementation and decision wiring

## TL;DR

The proposed mixed-integer optimizer converts one verified forecast bundle,
one frozen account snapshot, and one frozen quote set into an integer target
portfolio and signed trades. It maximizes scenario-weighted concave utility
subject to cash, buying-power, exposure, false-signal-capital, tail-loss,
cardinality, cost, and no-trade-band constraints. The mathematical design is
complete enough to implement, but the equity optimizer, its calibration
artifacts, and ADR-0111 do not exist yet; nothing described here is currently a
live allocation path.

## Scope and current status

This document is the technical companion to
`docs/plans/2026-09-intraday-equities-mio.md`. It specifies one decision tick:
given forecasts and the account state at time $t$, choose whole-share target
positions and the trades needed to reach them.

It does not choose or train the predictive model, route orders, grant trading
authority, or bypass production guards. The completed P16 LightGBM study is
developmental post-selection evidence. Its `lean-pooled-h10` result and horizon
caps do **not** currently feed an MIO or authorize deployment.

The repository already contains:

- the generic `PyomoSolve`/HiGHS doorway in `dskit/pipeline/libs/pyomo.py`;
- a working related optimizer in `children/pmquant/pmquant/mio.py`;
- immutable production records and the live decision loop in
  `dskit/production/`; and
- the reviewed equity-MIO design proposal.

The repository does not yet contain `ScenarioUtilityProgram`,
`EquityKellyMIO`, an equity MIO proposer, a `run-mio-*.json` document, the
required calibration artifacts, or an approved ADR-0111.

## Runtime placement

The intended information flow is:

```text
released model + current market rows
        │
        ▼
Decider.evaluate() ──► verified forecast/scenario head outputs
        │
        ├──► Proposer.candidates() ──► stable, unsized Candidate records
        │
Accounting.snapshot() ───────────────► frozen AccountState
        │
        ▼
EquityKellyMIOProposer.proposals()
        │  shared ScenarioUtilityProgram + HiGHS
        ▼
signed Proposal records
        │
        ▼
guards → permits → hash-chained ledger → paper/live executor
```

This placement matters. In the current production loop,
`Decider.evaluate()` runs before the account snapshot is handed to the
proposer. The live `AccountState` is available to `Proposer.proposals()`, not
to an ordinary pipeline head. Therefore a live state-dependent optimizer
cannot be implemented only as the proposed `EquityKellyMIO` head and then
passed to `TargetPositions`: that head would have sized against no current
account.

The implementation should have one reusable, pure model builder/solver engine:

- `ScenarioUtilityProgram` owns variables, common constraints, utility
  tangents, tail-loss construction, solver invocation, and exact recompute.
- `ScenarioUtilitySolve(PyomoSolve)` wraps that engine for offline pipeline,
  replay, and conformance runs.
- `EquityKellyMIOProposer(Proposer)` invokes the same engine from
  `proposals(head_outputs, candidates, state, provenance)` for live decisions.
- An optional child `EquityKellyMIO(ScenarioUtilitySolve)` remains useful for
  recorded-state shadow runs, but it is not the live account-state seam.

This adjustment belongs in ADR-0111 because the earlier proposal describes the
node but does not close this live-state handoff.

## Sets and indices

For one decision time $t$:

- $i \in I$ indexes the union of currently held stocks and newly eligible
  stocks.
- $o \in \Omega=\{1,\ldots,S\}$ indexes joint future-return scenarios.
- $j \in J=\{1,\ldots,K\}$ indexes tangent knots used to linearize concave
  utility.
- $\omega_o>0$ is scenario $o$'s probability, with
  $\sum_{o\in\Omega}\omega_o=1$.

A currently held stock stays in $I$ even if it is no longer eligible for a new
purchase. It must carry current uncertainty evidence or an explicit forced-exit
instruction; otherwise the decision refuses.

## Input notation and project lineage

The source boundary is explicit:

| Input | Intended runtime producer | Current repository state |
|---|---|---|
| Symbols and allowed horizons | `children/intraday_equities/configs/universe-p13-pooled.json` through the child `Universe` node and the selected release | Universe machinery exists; a deployable MIO release does not |
| $\mu_i^{gross}$, $H_i$, expiry, model/data hashes | A served prediction head derived from the final released run, never a separately restated serving model | P16 recommends a mask on developmental evidence; no eligible final release publishes this bundle |
| $r_{oi}^{gross}$, $\omega_o$, $U_\mu$, $U_r$ | A hash-pinned joint-scenario calibration artifact wired into the forecast bundle | Required by A18039–A18047; not built for this equity MIO |
| $\widehat\pi_i$, $\pi_i^{upper}$, $U_\pi$ | A causal false-signal calibration artifact in the same bundle | Required; producer and approved uncertainty geometry are missing |
| $e_i$ and $f_i$ | Replayed release-bound statistical verdicts plus explicit expiry/forced-exit policy | Generic recorded verdict support exists; no approved equity MIO bundle emits the complete flags |
| $h_i$, $C_0$, $W_0$, $BP_0$, working orders | `Accounting.snapshot()` as immutable `AccountState` | Generic paper/recorded accounting exists; a Schwab live-account implementation is not established in this child |
| $p_i$ and quote timestamp | The tick's single frozen entry read, converted to `QuoteSet` and bound by digest | Generic production contract exists; the equity release must provide the required fresh bid/ask/mid quote shape |
| Spread, TAF, Section 31, $\rho_i$ | Dated, hash-pinned Schwab/account cost artifact | Specified by the proposal; no equity MIO cost artifact is present |
| $\lambda_t^{bps}$ | Time-of-day/horizon opportunity-cost calibration artifact | Not built |
| $q_{fdr}$, $\beta$, $L_{CVaR}$, limits, band, reserve, $\gamma$ | ADR-0111-approved run/policy configuration | Values remain owner decisions |
| Solver and deterministic options | dskit `PyomoSolve` configuration, HiGHS via `appsi_highs` | Generic doorway exists |

“Missing” here is intentional and fail-closed: an implementation must create and
pin the producer rather than substitute a constant or infer it from conversation.

### Forecast and eligibility inputs

For each $i$:

- $\mu_i^{gross}$ is the model's gross expected return over the declared
  holding horizon.
- $r_{oi}^{gross}$ is stock $i$'s gross return in joint scenario $o$.
- $\widehat\pi_i$ is the point estimate that the signal is false; it reduces
  the scenario mean.
- $\pi_i^{upper}$ is the conservative upper bound used in the portfolio-level
  false-signal-capital constraint.
- $e_i\in\{0,1\}$ says whether the upstream Gate-1/Gate-3 evidence permits a
  new position.
- $f_i\in\{0,1\}$ is an explicit forced-exit instruction; $f_i=1$ requires a
  zero target even when the stock is currently held.
- $H_i$ is the declared holding horizon and $T_i^{expiry}$ is the forecast
  expiry.

These fields must eventually come from one release-bound forecast-bundle head
in `children/intraday_equities`. The bundle contract is locked conceptually by
Path A18256 and the uncertainty work in A18039–A18047, but no bundle publisher
currently produces the complete contract. It must bind the model release ID,
training/data/calibration hashes, scenario-generation rule and seed, units,
coverage, horizon semantics, and expiry.

The current P16 gate artifacts may inform a future approved release, but they
cannot be read directly as live eligibility: they explicitly say
`developmental_post_selection` and `deployment_eligible=false`.

The scenario producer must encode parameter uncertainty consistently. Under
the current proposed recentering rule,

$$
r_{oi}
=r_{oi}^{gross}-\mu_i^{gross}
+\left(1-\widehat\pi_i\right)\mu_i^{gross}
=r_{oi}^{gross}-\widehat\pi_i\mu_i^{gross}.
$$

The forecast bundle supplies the resulting $r_{oi}$; the optimizer verifies
the provenance rather than inventing a new uncertainty transform at decision
time.

### Account and quote inputs

For each $i$:

- $h_i\in\mathbb Z_{\ge0}$ is the currently held share count.
- $p_i>0$ is the frozen decision price under one declared quote convention.
- $\ell_i\in\mathbb Z_{\ge1}$ is the permitted share-lot increment.
- $\bar x_i$ is the per-stock exposure cap.

At account level:

- $C_0$ is current cash.
- $W_0$ is marked opening wealth.
- $BP_0$ is broker-reported buying power.
- $C_{reserve}$ is the required cash reserve.
- $X_{gross}$ is the total gross-exposure ceiling.

`Accounting.snapshot()` in `dskit/production/accounting.py` supplies the
immutable `AccountState`: balances, positions, working orders, risk evidence,
source digests, and its observation time. The tick's frozen market entry and
`Proposer.quotes()` supply the `Quote`/`QuoteSet`. Their digests and oldest
timestamps become production provenance. The MIO must refuse timestamp or
valuation mismatches rather than reconcile them heuristically.

### Trading-cost inputs

For each $i$:

- $s_i^{bps}$ is the assumed one-way half-spread in basis points.
- $f_i^{TAF}$ is the sell-side per-share TAF rate and
  $\bar f_i^{TAF}$ its per-order cap.
- $f_i^{31}$ is the dated sell-side Section 31 rate in basis points.
- $\rho_i\in[0,1]$ is the broker/account-specific fraction of same-decision
  sale proceeds that may count toward buying power.

These values belong in a dated, provenance-bound Schwab cost artifact or source
configuration. They are not model outputs and must not be hard-coded in the
optimizer. The account type and exact proposed-order grouping determine how
sale credits and capped fees apply.

### Policy and calibration inputs

The following are document/config inputs approved by ADR-0111:

- $q_{fdr}$: maximum capital-weighted false-signal probability;
- $\beta$: tail-loss confidence level and $L_{CVaR}$: its loss ceiling;
- $N_{max}$: maximum number of held names;
- $m_{ticket}$: minimum position value;
- $B^{bps}$: no-trade-band width;
- $\gamma\ge1$: utility risk-aversion parameter;
- $K$: tangent count and $S_{max}$: scenario ceiling; and
- solver name/options, including deterministic HiGHS settings.

The opportunity charge $\lambda_t^{bps}$ must come from a separately built,
hash-pinned calibration artifact indexed by time-of-day and normalized horizon.
It measures the value of keeping capital available for a better near-future
signal. No such equity calibration artifact exists yet.

## Pre-solve validation and eligibility

A new candidate may enter only when:

$$
e_i=1,
$$

its bundle entry is fresh and hash-verifiable, its quote is fresh and the market
is neither halted, locked nor crossed, and $p_i$ exceeds the configured minimum
price. These are safety and data-integrity checks. The optimizer must not remove
a name merely because its standalone expected return looks small; it may reduce
joint portfolio loss.

A held name with $e_i=0$ may be held or reduced but not increased. A held name
with $f_i=1$ must be fully exited.

Only the empty-new-candidate **and** empty-inventory case may return zero without
calling the solver. Existing inventory still requires a sell/hold/buy decision.

Incompatible holding horizons may not share one scenario-wealth row. They must
be normalized under an approved rule or solved as separate scenario sets.

## Decision variables

For each $i$:

- $b_i,s_i\in\ell_i\mathbb Z_{\ge0}$: shares bought and sold now, in permitted
  lot increments;
- $q_i\in\mathbb Z_{\ge0}$: target shares;
- $y_i\in\{0,1\}$: whether the target position is active;
- $a_i\in\{0,1\}$: whether any trade is active; and
- $d_i\in\{0,1\}$: active direction, where 1 permits a buy and 0 a sell.

This proposal is long-only: $h_i,q_i\ge0$. A negative live position is outside
the model contract and must trigger an explicit operational path rather than be
silently clipped to zero.

For each scenario $o$:

- $W_o^{actual}$: executable terminal wealth after entry and liquidation costs;
- $W_o^{utility}$: terminal wealth after the opportunity charge;
- $t_o$: the linearized utility value; and
- $z_o\ge0$: loss above the tail-loss threshold.

The scalar $\eta\in\mathbb R$ is the optimized tail-loss threshold.

Derived values are

$$
x_i=p_iq_i,\qquad v_i^+=p_ib_i,\qquad v_i^-=p_is_i.
$$

Here $x_i$ is target exposure, while $v_i^+$ and $v_i^-$ are buy and sell
notional. Costs attach to trades, never to unchanged inventory.

The lot-rounded no-trade threshold is fixed before solving:

$$
band_i=\ell_i\left\lceil
\frac{B^{bps}10^{-4}\max(p_ih_i,m_{ticket})}{p_i\ell_i}
\right\rceil.
$$

A mandatory exit sets $band_i=0$. Every big-$M$ bound below must be derived from
the tighter of inventory, position, exposure, cash, and buying-power limits. If
a finite valid bound cannot be proved, the solve refuses.

## Mixed-integer optimization program

### Inventory, position, and portfolio constraints

For every $i$:

$$
q_i=h_i+b_i-s_i. \tag{C1}
$$

New risk and forced exits are constrained explicitly:

$$
b_i\le M_i^{buy}e_i,
\qquad
q_i\le M_i^{target}(1-f_i). \tag{C1a}
$$

Thus an ineligible held stock can only stay unchanged or shrink, while a
forced-exit row sets its target to zero.

Target activation and minimum position value are

$$
x_i\le \bar x_i y_i,
\qquad
x_i\ge m_{ticket}y_i. \tag{C2}
$$

The portfolio position-count cap is

$$
\sum_{i\in I}y_i\le N_{max}. \tag{C3}
$$

The post-trade cash identity is

$$
C_{after}=C_0+\sum_i v_i^- -\sum_i v_i^+ - Cost_{entry}. \tag{C4}
$$

Cash and buying-power limits are

$$
C_{after}\ge C_{reserve}, \tag{C5a}
$$

$$
\sum_i v_i^+ + Cost_{entry}
\le BP_0+\sum_i\rho_i v_i^-. \tag{C5b}
$$

The gross-exposure ceiling is deliberately an inequality, so cash may remain
unused:

$$
\sum_i x_i\le X_{gross}. \tag{C6}
$$

The locked false-signal-capital rule is

$$
\sum_i \pi_i^{upper}x_i
\le q_{fdr}\sum_i x_i,
$$

or equivalently

$$
\sum_i(\pi_i^{upper}-q_{fdr})x_i\le0. \tag{C7}
$$

The point estimate $\widehat\pi_i$ changes the scenario mean; the upper bound
$\pi_i^{upper}$ protects this separate portfolio-level rule. Neither replaces
the other.

### Direction and no-trade constraints

Buy and sell cannot both be active:

$$
0\le b_i\le M_i^{buy}d_i,
\qquad
0\le s_i\le M_i^{sell}(1-d_i). \tag{C8a}
$$

The no-trade band is enforced inside the optimization:

$$
band_i a_i\le b_i+s_i\le M_i^{trade}a_i. \tag{C8b}
$$

Thus $a_i=0$ forces no trade, while $a_i=1$ requires a change at least as large
as the band. No post-solve rounding may alter the target.

### Exact entry costs

Let the variable sell-side TAF charge be

$$
TAF_i(s_i)=\min(f_i^{TAF}s_i,\bar f_i^{TAF}),
$$

implemented with an exact binary piecewise-linear construction for the proposed
order grouping. Then

$$
Cost_{entry}=\sum_i\left[
s_i^{bps}10^{-4}(v_i^++v_i^-)
+TAF_i(s_i)
+f_i^{31}10^{-4}v_i^-
\right].
$$

TAF and Section 31 are sell-only. The spread applies to changed notional on both
sides. The same fee must never be subtracted upstream and again here.

### Scenario wealth and opportunity charge

For every scenario $o$:

$$
W_o^{actual}=C_{after}
+\sum_i(1+r_{oi})x_i
-Cost_{exit,o}(q). \tag{C9}
$$

`Cost_exit` prices liquidation at the declared horizon convention, including
exit half-spread and sell-only capped fees. This makes the objective use
round-trip economics.

Capital deployed now cannot be used for a better signal arriving soon. The
calibrated opportunity charge gives

$$
W_o^{utility}=W_o^{actual}
-\lambda_t^{bps}10^{-4}\sum_i x_i. \tag{C10}
$$

Unspent cash is not penalized.

### Concave utility approximation

Use dimensionless wealth $w=W/W_0$ and define

$$
u_\gamma(W)=
\begin{cases}
\log(w), & \gamma=1,\\[4pt]
\dfrac{w^{1-\gamma}-1}{1-\gamma}, & \gamma>1.
\end{cases}
$$

This normalization makes utility comparable across account sizes without mixing
dollars into a dimensionless objective. For tangent knots $k_j$ spanning
verified positive wealth bounds,

$$
t_o\le u(k_j)+u'(k_j)(W_o^{utility}-k_j),
\qquad \forall o,j. \tag{C11}
$$

The minimum of these tangent upper bounds is a piecewise-linear approximation
that HiGHS can optimize together with whole-share decisions.

The existing `pmquant.mio.utility_at` accepts a Kelly fraction rather than
$\gamma$. Until the API is renamed, the adapter must call it with

$$
kelly\_fraction=1/\gamma,
$$

not with $\gamma$ itself.

### Tail-loss constraint

Define scenario loss relative to opening wealth:

$$
L_o=W_0-W_o^{actual}.
$$

The Rockafellar–Uryasev linear construction is

$$
z_o\ge L_o-\eta,\qquad z_o\ge0, \tag{C12a}
$$

$$
\eta+\frac{1}{1-\beta}\sum_o\omega_oz_o
\le L_{CVaR}. \tag{C12b}
$$

This limit uses actual wealth, so both entry and scenario-liquidation costs are
inside the loss calculation.

### Objective

The solver maximizes scenario-weighted utility:

$$
\max\;\sum_{o\in\Omega}\omega_ot_o. \tag{OBJ}
$$

The opportunity charge is already inside $W_o^{utility}$; adding a second
standalone return hurdle would double-count it and could discard useful hedges.

## Solver and exact post-solve verification

The implementation uses Pyomo with HiGHS through `appsi_highs`. The proposed
production settings include one thread, random seed 0, and zero relative MIP
gap. A non-optimal termination refuses; a time-limit result is a halt, not a
best-effort portfolio.

After solving, recompute from the final integer target and signed trade vector:

- inventory conservation and lot integrality;
- exact cash and broker buying power;
- per-stock and total exposure;
- position count and minimum ticket;
- no-trade-band and direction rules;
- exact grouped entry and exit fees;
- positive actual and utility wealth in every scenario;
- scenario utility and tail loss; and
- $\sum_i\pi_i^{upper}x_i\le q_{fdr}\sum_i x_i$.

Any failed recompute raises. Assertions verify the optimizer output; they never
modify it.

## Output contract

The solver result should contain, for every $i$:

- `instrument`;
- `held_qty = h_i`;
- `target_qty = q_i`;
- `buy_qty = b_i` and `sell_qty = s_i`;
- derived `side` and absolute `trade_qty`;
- target exposure and exact expected entry cost;
- forecast expiry and holding horizon; and
- stable input, scenario, policy, account-state, quote, and solver digests.

It should also emit portfolio evidence: objective value, actual utility
recompute, tail-loss value and slack, false-signal-capital value and slack,
cash/buying-power/exposure slack, binding constraints, unfunded eligible
candidates, solver status, elapsed time, and deterministic settings.

Zero-delta rows are audit evidence but become abstentions, not broker orders.

## How outputs become decisions

The live proposer follows the existing `dskit.production` contract:

1. `candidates(head_outputs)` turns the complete release-bound tradable universe
   into stable, unsized `Candidate(id, instrument, scope_keys)` records. Its head
   data carries verified forecasts for eligible names and explicit hold/reduce or
   forced-exit status for the rest. Covering the full possible inventory is
   necessary because candidate creation happens before the account snapshot;
   candidate identity must not depend on account state.
2. `Accounting.snapshot()` freezes `AccountState` for those scope keys.
3. `proposals(...)` verifies the forecast/account/quote timestamps and digests,
   solves the MIO once, and maps each nonzero signed delta to a `Proposal`.
4. Each `Proposal` carries side, quantity, reference price, exposure, expiry,
   prediction metadata, and the tick's `Provenance` digests.
5. Production guards evaluate the proposals and account evidence. Permits bind
   the decision-plan, account-risk, quote, coverage, and policy digests.
6. The hash-chained ledger records the proposed and allowed decisions before the
   executor submits anything.
7. The paper or live executor owns venue translation, order submission,
   acknowledgements, fills, rejections, and breakers. The MIO never calls Schwab.

Because the MIO solves trades jointly, the proposer must preserve one portfolio
decision identity across all resulting proposal legs. A broker rejection of one
leg can invalidate the intended joint portfolio; the production policy must
halt or explicitly re-solve from a fresh account snapshot rather than submit
the remaining legs as though nothing changed.

## Offline, shadow, paper, and live use

The same pure solver engine supports four contexts:

- **Offline replay:** a pipeline node consumes recorded forecasts, quotes, and
  account states to test invariants and compare against an enumeration oracle.
- **Shadow:** the live loop reads real inputs and records MIO proposals, but no
  orders are submitted.
- **Paper:** the standard production guards and executor simulate orders and
  fills while preserving the same decision/ledger contracts.
- **Live:** requires separate owner authorization after calibration, shadow,
  paper, broker-rule, tax, latency, and operational reviews.

No stage may silently promote itself to the next context.

## Required implementation files

Subject to ADR-0111 approval, the intended change set is:

- `dskit/pipeline/libs/pyomo.py`: shared `ScenarioUtilityProgram` and the
  `ScenarioUtilitySolve` node wrapper;
- `tests/pipeline_libs/test_pyomo.py` or a focused companion: utility tangent,
  tail-loss, determinism, refusal, and oracle tests;
- `children/pmquant/pmquant/mio.py`: refactor onto the shared engine with its
  current behavior pinned;
- `children/intraday_equities/intraday_equities/nodes_capital.py`: equity input
  validation, cost construction, offline node, and live MIO proposer;
- `children/intraday_equities/configs/run-mio-*.json`: recorded replay, shadow,
  and paper documents; and
- child tests covering buy, sell, hold, forced exit, empty sets, costs, mixed
  horizons, stale evidence, all constraints, exact recompute, and output-to-
  proposal mapping.

## Unresolved decisions

ADR-0111 must still approve $q_{fdr}$, uncertainty geometry, contribution-aware
risk schedule, $\lambda_t^{bps}$ calibration, $\beta$, $L_{CVaR}$,
$N_{max}$, $m_{ticket}$, $B^{bps}$, cash reserve, exposure and buying-power
policies, account type, and the treatment of mean-return uncertainty.

Until those values and the missing producers exist, the formulation is an
implementation specification—not an executable equity allocation policy.
