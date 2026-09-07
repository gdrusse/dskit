# The intraday_equities live MIO — design proposal

**Status:** PROPOSAL. Not approved, not built. **ADR-0111 must be written and
approved before any code is written** (CLAUDE.md: "ADR before code means WRITE
IT AND WAIT"). This document is the material that ADR summarizes.

**Date:** 2026-09-07 · **Child:** `children/intraday_equities` · **Path rows:**
A2850 (HFDR-in-MIO, locked), A18039–A18047 (the uncertainty objects),
A18256 (the forecast bundle, locked).

**Who implements this:** an agent that is not the author. Every section below
is written to be executed literally. Where a number is pinned, it is pinned
because it was **measured on this repository's machine** (§4) — do not
substitute a guess. Where a decision is the owner's, it is marked
**OWNER** and must not be invented.

---

## 1. Scope

Build the capital-sizing step for `intraday_equities`: at each live decision
tick, read one model-published forecast bundle plus live portfolio state, and
choose **how many shares of which names to hold**, subject to risk, false-signal
and cost constraints, solving in **under 10 seconds** (measured target: ≤ 3 s).

Out of scope: choosing the final predictive model (that is the P13–P16 zoo and
ADR-0107), order routing and execution (that is `dskit.production`'s executor),
and the `pi_i` / `U_*` estimators themselves (Path A18039–A18047 — this document
consumes them and pins their interface).

---

## 2. Inventory — what already exists

Read this section before concluding anything is missing.

| Need | Already built | Where |
|---|---|---|
| Generic pyomo→Node doorway | `PyomoSolve` — abstract `build_model`/`extract`, solver resolution, options pass-through, refusal-by-name | `dskit/pipeline/libs/pyomo.py` |
| Worked concrete subclass | `BudgetedSelect` — 0/1 knapsack, empty-gate short circuit, post-solve budget assertion, HiGHS determinism pins | same file |
| **A fractional-Kelly MILP over scenarios** | `pmquant.mio` — tangent-plane outer approximation of log/CRRA utility, integer lots, per-scenario wealth rows, exact post-solve recompute | `children/pmquant/pmquant/mio.py` (955 lines) |
| Capital-role safety gate | planner refuses a `capital` node with no `stat_test` survivors wired in | `dskit/pipeline/planner.py:603` |
| Optimizer-kind registry | `OPTIMIZER_KINDS` / `register_optimizer_kind`, `OptimizationConfig` | `dskit/pipeline/base.py:894` |
| Live serve loop, ledger, guards, executor | the whole `dskit.production` package (ADR-0090/0091) | `dskit/production/` |
| Decision record shapes | `Candidate`, `Proposal`, `Quote`, `AccountState`, `DecisionPlan`, `Intent` | `dskit/production/records.py` |
| Child's per-tick symbol pick | `PortfolioSelect` (role `score`) — explicitly says "Capital sizing is a later document" | `children/intraday_equities/intraday_equities/nodes.py:2885` |
| Solver | HiGHS via `appsi_highs`; `pyomo` + `highspy` are pip installs, no external binary | — |

**The gap is narrow.** The mechanism exists in `pmquant` but is trapped in a
child, wired to prediction-market vocabulary (contracts, rungs, sides,
settlement law). Per CLAUDE.md — *"Missing capability graduates INTO dskit.
Always"* and *"mechanism belongs to the pack, the domain constraint belongs to
the child"* — the concave-utility scenario MILP graduates to
`dskit/pipeline/libs/pyomo.py`, and `intraday_equities` subclasses it.

---

## 3. The decision problem

### 3.1 Objective — why maximizing per-tick E[log W] is the right long-run answer

The owner's stated goal is **long-run compounded growth on an unset horizon with
money periodically added**. For sequential, independent opportunities with a
correctly specified return law, maximizing expected log wealth is the
growth-optimal (Kelly) policy and asymptotically dominates any essentially
different strategy (Kelly 1956, *Bell System Technical Journal* 35(4);
Breiman 1961, *4th Berkeley Symposium*). The child's overlapping positions and
serially correlated signals do not satisfy those assumptions automatically;
§11 therefore limits this claim and defines the required escalation.

Two well-documented cautions, both handled below:

- Kelly's effective risk aversion is `1/W`, so it bets large and is **very**
  sensitive to errors in `mu` (MacLean, Thorp & Ziemba 2010, *Quantitative
  Finance* 10(7)). Fractional Kelly is the standard remedy; here the fraction is
  a declared knob. Parameter uncertainty must be represented in the scenario
  law; §5 deliberately does not attach a dimensionally unrelated linear penalty
  to scenario utility.
- Log utility is well approximated by `mu − sigma²/2` **for short holding
  periods and small returns** (Levy & Markowitz 1979, *AER* 69(3); Pulley 1981,
  *JFQA* 16(3)). A minutes horizon is the best case for that approximation, so a
  cheap mean-variance surrogate was a serious candidate. **We measured it and
  rejected it — see §4.3.**

### 3.2 Periodic contributions

A recurring deposit stream is a riskless asset. Standard life-cycle results show
that income uncorrelated with equities is an implicit bond holding and justifies
a **larger** risky fraction (Bodie, Merton & Samuelson 1992, *JEDC* 16(3–4),
NBER w3954; Viceira 2001, *Journal of Finance* 56(2), NBER w7409). Its present
value shrinks relative to the account as the account grows.

**Design consequence.** Ship a configurable risk-tolerance schedule:

```
f_t = f_base * g( PV_contrib / W_t )
```

with `g` bounded and non-decreasing and `f_t` clamped to `[f_min, f_max]`.
Using CRRA coefficient `gamma=1/f_t` is only a small-return approximation to
fractional Kelly; it is not exact with discrete scenarios, costs, integer lots,
or constraints and must be calibrated as a risk-aversion choice. Early on, a
bad path is repaired by the next deposit and a
larger fraction is defensible; as `PV_contrib / W_t → 0`, `f_t → f_base`.
**OWNER decision:** `f_base`, `f_min`, `f_max`, the shape of `g`, and the
discount rate behind `PV_contrib`. Ship the seam; do not invent the numbers.

*Honest limit:* no paper we found treats growth-optimal investing with exogenous
periodic inflows on an unset horizon head-on. The schedule above is a defensible
construction from adjacent results, not a cited theorem. Label it as such in the
ADR.

### 3.3 Opportunity cost — the part never explored here

Capital is finite: funding candidate A forecloses B for the holding period.
Three distinct mechanisms, all of which we are missing today:

**(a) A reservation price for capital.** The tradition that estimates such a
price against a stream of forthcoming random requests
is network revenue management: a bid-price control accepts a request only if its
value exceeds the bid price of the capacity it consumes, and is asymptotically
optimal as demand and capacity scale together (Talluri & van Ryzin 1998,
*Management Science* 44(11)).

Do **not** filter names individually on expected edge. A low standalone-return
name can reduce joint CVaR through covariance and improve the portfolio. Instead,
charge deployed capital inside the joint expected-utility objective:

```
opportunity_cost = lambda_t_bps * 1e-4 * sum_i x_i
W_utility_o = W_actual_o - opportunity_cost
```

where `lambda_t_bps` is a declared quantile of the historical distribution of best
net alpha per dollar arriving in the next `H` minutes, expressed in basis points
over the candidate's normalized horizon and **conditioned on time-of-day** (the
open and the close are not the same market). `lambda_t_bps` is read from a
calibration artifact pinned by hash; the optimizer must never estimate it from
the candidates it is choosing among. The gross-exposure LP dual may be logged as
a diagnostic, but it has utility-per-dollar units and must not be compared
directly with a basis-point return. Any certainty-equivalent normalization would
need its own derivation and validation.

**(b) A no-trade band.** Under proportional costs the optimal policy is a
wedge-shaped inaction region — you trade to the nearest boundary, never to the
target (Constantinides 1986, *JPE* 94(4); Davis & Norman 1990, *Math. of OR*
15(4); Liu 2004, *Journal of Finance* 59(1)). A "rebalance to the optimum every
tick" loop is *provably wrong* under costs. The MIO therefore optimizes signed
trade deltas from current inventory, with inaction encoded in the model. A
post-solve band is forbidden: changing an optimal target after assertions can
break cash, buying-power, HFDR, CVaR, cardinality, or positivity constraints.
Without in-model inaction the strategy pays the round trip in §3.4 repeatedly
for noise.

**(c) Counterfactual logging (Perold).** Implementation shortfall splits into
execution cost on shares filled and **opportunity cost on shares never filled**
(Perold 1988, *Journal of Portfolio Management* 14(3); Wagner & Edwards 1993,
*FAJ* 49(1)). Every tick, record every candidate that cleared the hurdle but was
**not** funded, and mark it at its horizon. This is the only way to tell an
over-tight `q` or `lambda_t_bps` from a well-set one. It is a ledger row, not an
optimizer term.

**(d) Cash is not idle.** Holding cash retains the option to take a better
signal later. The objective must not penalize unspent capital — with a
`gross_budget ≤ B` inequality (not equality) and the hurdle in (a), cash is the
default and every dollar deployed is jointly charged `lambda_t_bps` in §5.

### 3.4 Costs and frictions — what a Schwab retail account actually pays

The owner is right that there is **no commission**, and wrong that the trade is
free. Verified against primary sources:

| Item | Value | Source |
|---|---|---|
| Online US listed equity commission | **$0** | Schwab Pricing Guide, Apr 2026 |
| Broker-assisted / automated phone | $25 / $5 | same |
| SEC Section 31 fee (**sells only**) | $20.60 per $1M of covered sells from 2026-04-04 = **0.206 bps of sell notional** | SEC Fee Rate Advisory 2026-2 |
| FINRA TAF (**sells only**) | **$0.000195/share**, cap $9.79/trade (2026) | FINRA fee adjustment schedule |
| Effective spread, retail marketable order, S&P 500 name | **4.36 bps** (wholesaler), i.e. ≈2.2 bps one way | Dyhrberg, Shkilko & Werner 2025, *JFE* 168 |

**Illustrative round-trip friction ≈ 4.6 bps ≈ 0.046%.** A gross edge below its
actual round-trip cost is uneconomic before opportunity cost. Store the assumed
spread/fee inputs and a `round_trip_cost_floor_bps` validation floor in config;
do not compare a return already net of those costs to that floor again.

Two consequences the implementing agent must not miss:

1. **TAF is per share, so cost is price-dependent** — 0.013 bps on a $150 stock
   but **0.195 bps on a $10 stock**, a 15× difference. The cost model must be
   `per_share_fee / price + spread_bps`, never a flat bps. Cheap stocks are
   structurally worse for this strategy.
2. **Market impact is not our cost function.** The square-root law is a
   metaorder law in participation rate; a few-thousand-dollar order in a liquid
   large cap is a rounding error of ADV. Model the half-spread and the fees; keep
   a size term only as a *guard* that refuses illiquid names, not as an objective
   term.

### 3.5 False signals and capital — two mechanisms, not one

ADR-0088 locks the portfolio-level constraint `Σ x_i·pi_i ≤ q·Σ x_i`. That caps
false-signal *capital* but leaves `mu_i` un-haircut, so a high-`mu`, high-`pi`
name can buy its way in by dragging low-`pi` names along. The multiple-testing
literature is explicit that the haircut belongs on the expected return itself and
is strongly non-linear — high Sharpes lightly penalized, marginal ones gutted
(Harvey & Liu 2015, *JPM* 42(1); Harvey, Liu & Zhu 2016, *RFS* 29(1)).

**So: haircut the return law AND keep the constraint.** Start from gross scenario
returns, recenter each name's scenarios to weighted mean
`(1 − pi_hat_i)·mu_gross_i`; `pi_hat` is the mean-haircut input and `pi_upper`
is used only by the portfolio HFDR constraint. The scenario-generation artifact must declare how
`U_mu` informed those draws. Subtract entry and horizon-liquidation costs exactly
once in §5. Bundle field `mu_net` is audit-only and must declare a reproducible
`cost_reference`: side, shares, price, spread, fee schedule, and horizon exit.
It is never expected to match a different live order. Retain ADR-0088 as the
separate portfolio-level cap. Refuse inconsistent reference metadata, not
legitimate live-cost differences.

One caution to record: FDR estimators are **conservative under low power**
(Andrikogiannopoulou & Papakonstantinou 2019, *Journal of Finance* 74(5), show
~65% of ±2% true-alpha funds misclassified as zero). If `pi_upper` is also
conservative, the design is conservative twice. **Calibrate `q` against realized
hit rates, not against a nominal FDR level.** — OWNER.

---

## 4. Prototype tractability measurements — rerun required

Everything here was measured in this container: pyomo 6.10.1, highspy, HiGHS via
`appsi_highs`, single thread, `mip_rel_gap=0`, `random_seed=0`. Timings are
**build + solve**, worst case over independent random instances with the full
constraint set (integer shares, binding cardinality, binding HFDR, CVaR cap,
and the prototype Bertsimas–Sim term). The reviewed formulation now uses signed
trades, self-financing, transaction costs in scenario loss, and in-model
inaction. Those rows were absent from the scratch harness. Therefore the tables
below are **historical prototype evidence, not a production contract**. Commit a
reproducible harness and rerun it against the corrected formulation before
ADR-0111 pins `n`, `S`, `K`, or a latency limit.

### 4.1 The envelope (integer shares, worst of 8 seeds)

| candidates n | scenarios S | knots K | rows | worst total |
|---|---|---|---|---|
| 24 | 128 | 32 | 4,556 | 0.46 s |
| 24 | 256 | 64 | 17,228 | 1.89 s |
| **40** | **256** | **32** | **9,084** | **3.00 s** |
| 40 | 256 | 64 | 17,276 | 3.88 s |
| 40 | 512 | 64 | 34,428 | **13.63 s** ✗ |
| 64 | 256 | 32 | 9,156 | 3.25 s |
| 64 | 512 | 32 | 18,116 | **11.44 s** ✗ |

**S = 512 broke the prototype budget.** The cost was super-linear in scenarios
once the binaries had to work. Treat `S ≤ 256` as a candidate ceiling only;
the corrected benchmark must establish the validated production ceiling.

### 4.2 How many tangent knots (n=40, S=256, reference K=256, 6 seeds)

| K | worst total | share of reference E[log W] | names differing |
|---|---|---|---|
| 16 | 1.62 s | 99.88 % | 0 |
| **32** | **1.99 s** | **100.00 %** | **0** |
| 64 | 2.61 s | 99.99 % | 1 |
| 128 | 4.78 s | 100.00 % | 0 |
| 256 | 7.10 s | 100.00 % | 0 |

**Prototype candidate: K = 32.** It matched the finite `K=256` reference on
every seed and picked identical names; neither finite tangent approximation is
the analytic log objective between knots. Note `pmquant.DEFAULT_N_TANGENTS =
128` — 2.4× the cost for no gain on
this problem class. The graduated pack must take `n_tangents` as a knob and
default it per-caller, not inherit 128.

### 4.3 The surrogate was tested and rejected

The Levy–Markowitz/Pulley argument (§3.1) says a cheap linear risk surrogate
should be nearly as good at minutes horizons. We built two and scored their
chosen portfolios on the **same evaluation criterion**, E[log W] over the
scenario set, against the finite tangent program:

| form | worst time | exact log-growth captured |
|---|---|---|
| Exact log, tangent planes, K=32 | 2.0 s | 100 % (definitionally) |
| CVaR-only linear surrogate | 0.65 s | **76 – 87 %**, and on 1 of 8 seeds it **allocated nothing at all** |
| Mean-absolute-deviation + CVaR | **hit the 60 s limit** | n/a — never proved optimal |

Two findings, both counter to the prior expectation:

1. **The surrogate is not free.** It gives up 13–24 % of the growth rate and has
   a pathological zero-allocation mode, because its `gamma`/`kappa` weights have
   no principled mapping to a Kelly fraction — they are hand-tuned, and a
   mis-tuned pair refuses to trade. The log/CRRA program still requires an owner-
   calibrated `risk_aversion_gamma`; it is not an exact fractional-Kelly dial.
2. **The MAD risk term is catastrophically slower**, not faster, than the tangent
   form — its dense equality rows coupling every `x` to the portfolio mean wreck
   branch-and-bound. Row count is a bad proxy for MILP difficulty.

**Therefore: use the log-utility tangent program.** In the prototype it cost ~2 s
and bought back a fifth of the measured compounding rate. The corrected,
reproducible benchmark must confirm both claims before ADR-0111. Do not
"optimize" this into a mean-variance objective.

### 4.4 Prototype operating point — not yet pinned

```
n_candidates <= 40      (hard refuse above 64)
S            =  256     (hard refuse above 256)
K            =   32
solver       = appsi_highs, threads=1, random_seed=0, mip_rel_gap=0
time_limit   =    8.0 s  -- a BREAKER, not a tuning knob
```

**Prototype worst measured: 3.0 s. Budget: 10 s. Apparent headroom: ~3×.**
The corrected benchmark must confirm that headroom.

Determinism was verified: 5 repeated solves of an identical instance returned
byte-identical share vectors. Keep `mip_rel_gap = 0`. Relaxing it to `1e-2`
roughly halves the time for <1 % objective loss, but re-introduces tie-flapping
between machines — the exact defect `BudgetedSelect._HIGHS_DETERMINISM` exists to
prevent. **Buy the deadline with `S` and `K`, never with the gap.**

### 4.5 Solver and language — no Julia

- HiGHS solves LP, MILP and QP but **cannot solve QP with integer variables**,
  and has no conic support (HiGHS docs). So MIQP and MISOCP are both off the
  table — which is fine, because the formulation in §5 is deliberately a pure
  MILP.
- Keep uncertainty sets **polyhedral (budgeted, Bertsimas–Sim 2004, *Operations
  Research* 52(1))**, never ellipsoidal: the robust counterpart of an LP under a
  budgeted set is itself an LP of similar size. An ellipsoidal set would make it
  MISOCP and HiGHS could not solve it at all.
- HiGHS is ~5–8× off the best commercial solver on *hard* MIPLIB instances
  (Mittelmann benchmark, 2026-07). At 40 binaries that gap does not bind — we
  measured 2–3 s, not 2–3 minutes.
- **Julia/JuMP is not justified.** It buys ~2–3× on model build after a 7–28 s
  JIT warm-up and costs a second language in the stack. Our measured *build* is
  0.10–0.15 s at the operating point — 5 % of the budget. There is nothing to
  win. Recommendation: **pyomo + HiGHS, as the framework already does.**
- If a future formulation ever exceeds budget, the escalation order is: cut `S`,
  then cut `K`, then move the model build out of the hot path (build once,
  mutate coefficients through the persistent `appsi_highs` interface) — in that
  order. Do not reach for another language.

---

## 5. The formulation

One decision tick. Index `i = 1..n` over the union of currently held names and
new eligible candidates, `o = 1..S` over joint gross-return scenarios, and
`j = 1..K` over tangent knots. Every held name needs a current `pi_upper` and
scenario row or an explicit forced-exit instruction; otherwise refuse. Inputs
include opening shares `h_i`, cash `C0`, marked opening wealth `W0`, pre-trade
buying power `BP0`, and quotes. All quantities use one declared timestamp and
valuation convention.

### 5.1 Eligibility (safe, non-economic gates only)

A candidate enters the program only if **all** hold:

1. It is in the Gate-1 / Gate-3 eligible set carried by the bundle.
2. Its bundle entry is present, fresh, schema-compatible and hash-verifiable.
3. Its quote is fresh, the market is not halted/locked/crossed, and its price
   clears `min_price` (the per-share TAF argument, §3.4).
No candidate is dropped for standalone expected return; §5.4 prices capital
jointly so diversification and hedge value remain available.

Only when both the eligible-new set and current inventory are empty may the node
return zero without waking the solver. Otherwise it runs the sell/hold/buy model;
mandatory exits enter as constrained sell deltas, so their cash, fees, and
portfolio constraints remain consistent.

### 5.2 Variables

| symbol | domain | meaning |
|---|---|---|
| `b_i`, `s_i` | non-negative integer | shares bought and sold this tick |
| `q_i` | non-negative integer | target shares, `h_i + b_i - s_i` |
| `y_i` | binary | candidate `i` is held |
| `a_i` | binary | trade-active indicator used by the no-trade band |
| `d_i` | binary | active trade direction: one buys, zero sells |
| `W_actual_o` | real, `[w_lo, w_hi]` | executable terminal wealth in scenario `o` |
| `W_utility_o` | real, `[w_lo, w_hi]` | wealth after the opportunity charge |
| `t_o` | real | utility surrogate in scenario `o` |
| `eta`, `z_o ≥ 0` | real | Rockafellar–Uryasev CVaR pair |

Let target exposure `x_i = price_i·q_i`, buy notional `v_i+ = price_i·b_i`,
and sell notional `v_i- = price_i·s_i`.
Before solve, compute a fixed, lot-rounded inaction threshold:

```
band_shares_i = lot_i * ceil(
    band_bps * 1e-4 * max(price_i*h_i, min_ticket)
    / (price_i*lot_i)
)
```

Set `band_shares_i=0` for a mandatory exit. Derive finite `M_buy_i`, `M_sell_i`,
and `M_trade_i` from the tighter of instrument, exposure, inventory, cash, and
buying-power bounds; refuse if a finite valid bound cannot be proved.

### 5.3 Constraints

```
(C1)  q_i == h_i + b_i - s_i                    inventory transition
(C2)  x_i <= x_max_i * y_i; x_i >= min_ticket*y_i
(C3)  sum_i y_i <= cardinality
(C4)  C_after == C0 + sum_i v_i- - sum_i v_i+ - entry_cost
(C5)  C_after >= cash_reserve
      sum_i v_i+ + entry_cost <= BP0 + sum_i rho_i*v_i-
(C6)  sum_i x_i <= gross_limit                   gross exposure inequality
(C7)  sum_i (pi_upper_i-q_fdr)*x_i <= 0          ADR-0088 HFDR
(C8a) 0 <= b_i <= M_buy_i*d_i; 0 <= s_i <= M_sell_i*(1-d_i)
(C8b) band_shares_i*a_i <= b_i+s_i <= M_trade_i*a_i
(C9)  W_actual_o == C_after + sum_i (1+r_oi)*x_i - exit_cost_o(q)
(C10) W_utility_o == W_actual_o - lambda_t_bps*1e-4*sum_i x_i
(C11) t_o <= u(k_j)+u'(k_j)*(W_utility_o-k_j)    all (o,j)
(C12) L_o == W0-W_actual_o; z_o >= L_o-eta       actual loss includes both costs
      eta+(1/(1-beta))*sum_o w_o*z_o <= cvar_limit
```
`rho_i` is the documented same-decision sale-credit coefficient for the actual
broker/account type; never assume sale proceeds augment buying power.

`entry_cost = Σ_i[half_spread_bps_i·1e-4·(v_i+ + v_i-)
+ min(taf_per_share·s_i, taf_cap) + sec31_bps·1e-4·v_i-]`. Represent the
piecewise TAF cap with an exact binary linearization per proposed sell order.
Costs are paid on **trades**, never unchanged target inventory; TAF and Section
31 are sell-only. If the implementation batches or splits an
order, it must apply the cap using the exact proposed-order grouping.

`exit_cost_o(q)` applies the horizon quote convention to liquidating every
target share: exit half-spread on scenario sell notional plus sell-only TAF and
Section 31, including the per-order TAF cap. It is represented with the same
exact piecewise-linear construction. This proposal chooses conservative
horizon liquidation value, not an unpriced continuation value.

**(C7) is the locked ADR-0088 policy.** Note it is linear in `x`, and its robust
counterpart over a **rectangular** `U_pi` is exactly the substitution
`pi_i → pi_upper_i` shown. If `U_pi` is later a finite scenario set, add one
copy of (C7) per retained scenario — still linear, still cheap. **OWNER:** the
geometry of `U_pi` (box vs. scenario) is A18044 and is not settled.
C4/C5 are the self-financing and buying-power rows; C6 deliberately need not
bind because retaining cash has option value.

### 5.4 Objective

```
maximize   sum_o w_o * t_o
```

This is scenario expected utility. `u` is `ln(W/W0)` when `gamma=1` and CRRA
otherwise. The existing `pmquant.mio.utility_at` API accepts a Kelly-fraction
parameter and internally maps it to CRRA gamma. The adapter must therefore call
it as `utility_at(..., kelly_fraction=1/risk_aversion_gamma)` with validated
`risk_aversion_gamma >= 1`; passing gamma directly is wrong. Keep one tested
implementation, but rename or wrap its interface so the conversion is explicit.
The mapping is only the small-return approximation in §3.2, not exact fractional
Kelly for this constrained discrete problem. The former
`epsilon·(Gamma·theta+Σp_i)` term is removed: it subtracted dollar P&L units
from dimensionless utility, left `epsilon` undefined, and was not a robust
counterpart of scenario expected utility. `U_mu` must instead alter the joint
return scenarios consistently with §3.5. If ADR-0111 chooses budgeted
Bertsimas–Sim uncertainty, it must derive and test the robust counterpart of
each scenario-wealth/tangent row with consistent units; `Gamma` and `kappa`
remain owner decisions. Until then, scenario recentering is the only approved
parameter-uncertainty mechanism.

### 5.5 Post-solve — assertions, not constraints

Recompute **exactly on the final executable target and signed order vector** and
**raise** on violation:

- inventory conservation, cash, live buying power, gross exposure, min ticket,
  cardinality, and no-trade-band rows,
- `W_actual_o > 0` and `W_utility_o > 0` in every scenario,
- realized scenario utility and CVaR, re-billed from exact proposed orders with
  entry and scenario-liquidation spreads, sell-only TAF caps, and Section 31 fees,
- `sum_i pi_upper_i·x_i ≤ q·sum_i x_i` on the exact numbers.

Refuse a solver termination that is not `optimal`. A `time_limit` hit is a
**halt** (exit code 3), not a degraded fill.

### 5.6 From target to orders

The MIO emits a **target plus signed trade vector that already satisfies the
no-trade band and every post-solve assertion**. Hand that vector
to the `Proposer` seam in `dskit/production/decider.py`, which turns head
outputs into `Candidate` and `Proposal` records. **The MIO never talks to a
broker.** Everything downstream of the target is `dskit.production`'s executor,
which already owns permits, fences, breakers and the hash-chained ledger.

---

## 6. Framework placement

Per CLAUDE.md's tiering test — *could a project that has never heard of
intraday equities use it?*

### Tier 2 — graduates into `dskit/pipeline/libs/pyomo.py`

A new abstract class beside `PyomoSolve` and `BudgetedSelect`:

**`ScenarioUtilitySolve(PyomoSolve)`** — role `capital`. It owns the mechanism
and nothing about equities:

- concave-utility tangent-plane construction (`utility_at`, `wealth_bounds`,
  the `(o, j)` tangent rows),
- inventory transitions and per-scenario wealth rows from caller-supplied state
  and payoff matrices,
- the CVaR (Rockafellar–Uryasev) block,
- the empty-gate short circuit and the post-solve exact recompute,
- knobs: `risk_aversion_gamma`, `n_tangents`, `n_scenarios_max`, `cvar_alpha`,
  `cvar_limit`, `cardinality`, `min_ticket`, `solver`,
  `solver_options`.

Subclass hooks: `payoffs(inputs)` → the scenario matrix and weights;
`instruments(inputs)` → per-name price/cost/eligibility; `domain_constraints(model, ...)`
→ the caller's own rows. **These are `@abstractmethod`** — CLAUDE.md: "a hook
that only raises `NotImplementedError` lets an incomplete subclass construct fine
and fail later."

Why this is tier 2 and not tier 3: the same class sizes a prediction-market
ladder, a sports book, an ad-spend allocation, or a project portfolio. It names
no market.

**Also required:** `pmquant.mio` must be **refactored onto this base** in the
same change, or we ship the second copy of a function that CLAUDE.md forbids
("A function is never repeated across modules — the second copy is the bug").
`utility_at` and `wealth_bounds` get one home: the pack. This is the strongest
argument that the graduation is correct — it deletes duplication rather than
creating it.

### Tier 3 — stays in `children/intraday_equities/intraday_equities/`

**`EquityKellyMIO(ScenarioUtilitySolve)`** in `nodes_capital.py` (new module,
mirroring `pmquant/nodes_capital.py`). It supplies only what is domain-specific:

- the forecast-bundle reader and its fail-closed verification,
- share-lot integrality and the `min_price` refusal,
- the Schwab cost model (`spread_bps`, `taf_per_share`, `sec31_bps` on sells),
- the ADR-0088 HFDR row (C7) — this is *this project's* policy, not a general
  optimizer feature,
- the joint `lambda_t_bps` opportunity charge and the no-trade band,
- the counterfactual (unfunded-candidate) ledger rows.

**A `stat_test` node must be wired into its inputs** or the document refuses to
plan (`planner.py:603`). The eligibility gate is that node.

---

## 7. Input contract — the forecast bundle

Path A18256 locks this: **the MIO must refuse a missing, stale,
schema-incompatible or unverifiable bundle.** Fail closed, by name, before any
model is built. Required per candidate:

`decision_ts` · `entity` · `holding_horizon_minutes` · `model_release_id` ·
`mu_gross` · `mu_net` (audit-only at a fully declared `cost_reference`, not an
objective input) · `cost_reference` (side, shares, price, spread, fee schedule,
horizon exit) · `pi_hat` ·
`pi_upper` · `U_pi` provenance · `U_mu` · `U_r`
(scenario matrix + weights + block rule + seed) · Gate-1/Gate-3 eligibility ·
every model/calibration/null/data hash · units · horizon semantics · coverage
targets · **expiry**.

**Portfolio state is a separate input and the bundle must never invent it:**
current positions, cash, marked opening wealth, live buying power, quotes, lot
sizes, exposure limits, and cash reserve. Refuse mismatched timestamps or an
opening state that does not reconcile under the declared valuation convention.

Two hard rules for the implementing agent:

- **Normalize or separate incompatible holding horizons before they enter one
  scenario set** (A18042/A18047). A 1-minute and a 60-minute candidate in the
  same scenario-wealth row is an arithmetic error, not a modeling choice.
- **A serving path never restates a training knob** (CLAUDE.md). Read horizon,
  feature set and cost assumptions from the run dir or source config — never
  re-spell them in the serve document.

---

## 8. Implementation order

Each step ends green before the next begins. TDD, per the `dskit.production`
precedent.

1. **ADR-0111** in `docs/architecture/decision-log.md`. Wait for approval.
   Nothing below starts first.
2. **`ScenarioUtilitySolve`** in `dskit/pipeline/libs/pyomo.py` + tests in
   `tests/pipeline_libs/`. Must pass the purity gate (pyomo imported inside
   run-path methods only) and the conformance bar (declared `outputs`).
3. **Refactor `pmquant.mio` onto the base.** Its existing suite must stay green
   and byte-identical in behavior — this is the regression net for step 2.
4. **`EquityKellyMIO`** in the child + `configs/run-mio-*.json`.
5. **The calibration artifacts** — `lambda_t_bps` by time-of-day bucket, `pi_upper`,
   `U_mu`, `U_r`. These are Path A18039–A18047 and are **empirical**; they
   cannot be config constants.
6. **Shadow mode.** Run the whole loop under `dskit.production` in `shadow`,
   then `paper`, logging the §3.3(c) counterfactual and realized opportunity
   costs. **No live capital until both read sane.**

---

## 9. Tests required

| Test | Why |
|---|---|
| Empty eligible set plus empty inventory → zero, solver never invoked | safe short circuit |
| Held inventory plus no eligible new names still solves | decayed alpha can trigger sells |
| Negative-covariance name survives eligibility and may reduce joint CVaR | no unsafe edge filter |
| Buy, sell, hold, and full-exit cases reconcile inventory and cash | self-financing |
| Unchanged inventory incurs zero transaction cost | costs belong to deltas |
| TAF/Section 31 apply only to sells; TAF cap follows order grouping | exact billing |
| Cash and buying-power inequalities need not bind | cash has option value |
| HFDR (C7) holds on the exact recompute | ADR-0088 is a *locked* policy |
| CVaR scenario loss includes exact transaction cost | risk includes friction |
| Inaction and trades pass all final-target assertions | no post-solve mutation |
| Stale/missing/unverifiable bundle → refuse by name | A18256 fail-closed |
| Mixed holding horizons in one scenario set → refuse | §7 |
| Corrected benchmark pins the accepted `(n,S,K)` envelope | prototype is not a contract |
| Identical inputs → identical share vector, twice | determinism (§4.4) |
| `test_default_agrees_everywhere` for `n_tangents`, `risk_aversion_gamma`, `cvar_alpha` | CLAUDE.md: a default belongs to ONE name |
| Cost model is per-share **and** per-notional | §3.4's 15× price dependence |
| `mu_net` reproduces only at declared `cost_reference` | audit semantics are stable |
| Objective and CVaR include entry and scenario liquidation costs once | round-trip economics |
| Band rounding/boundaries, mandatory exits, and every finite M | valid MILP |

---

## 10. Owner decisions still open

None of these may be invented by the implementing agent.

1. `q` in the HFDR constraint — and it must be calibrated against **realized hit
   rates**, not a nominal FDR level (§3.5).
2. `U_pi` geometry: rectangular box or finite scenario set (Path A18044).
3. `f_base`, `f_min`, `f_max`, the shape of `g`, and the discount rate behind
   `PV_contrib` (§3.2).
4. `lambda_t_bps`: which quantile, time-of-day buckets, horizon, and lookback.
5. `cvar_alpha`, `cvar_limit`, `cardinality`, `min_ticket`, `band_bps`, cash
   reserve, gross exposure, and buying-power policy.
6. Whether `U_mu` is represented entirely through scenario recentering or via a
   newly derived, dimensionally consistent robust counterpart; if the latter,
   `Gamma` (robust budget) and `kappa` (deviation multiplier).
7. Cash account vs. margin — see §11.

---

## 11. Risks and things this design does not solve

- **Signal decay during decide-and-execute.** A ~2 s solve plus broker round trip
  plus queue time is a real fraction of a 1-minute alpha's half-life. Slower-
  decaying signals deserve more weight (Gârleanu & Pedersen 2013, *Journal of
  Finance* 68(6)). **Measure realized decay and refuse any horizon whose
  half-life is under ~5× decision latency.** This may disqualify h=1 outright —
  which matters, because h=1 is where this child's only positive gain cell lives.
- **PDT remains an operational constraint unless primary sources say otherwise.**
  As of this review, FINRA's current Day Trading page and Rule 4210
  interpretations still publish the pattern-day-trader designation and $25,000
  minimum. No official FINRA Regulatory Notice 26-10 was located. Do not rely on
  the prior claim that PDT was retired; verify FINRA and Schwab requirements at
  deployment time.
- **Cash-account feasibility is unresolved.** FINRA states that day trading in a
  cash account is not permitted. Settlement, good-faith, and freeriding rules
  depend on exact funding and broker treatment; the prior "three in 12 months"
  statement was unsupported. Obtain Schwab's written rules before selecting
  account type. — OWNER.
- **Wash sales are continuous.** Re-entering the same ticker every few minutes at
  a loss triggers §1091 (26 U.S.C. §1091) all year; year-end open positions carry
  deferred losses. The escape is a §475(f) mark-to-market election, which needs
  trader tax status and is filed by the *prior* year's deadline. All gains are
  short-term regardless. **This is a tax-advice question for the owner's
  accountant, not a design decision.**
- **Halts and LULD.** A 5-minute pause mid-position is an unhedgeable gap. Belongs
  in the executor's guards, not the optimizer.
- **Odd lots.** At a few thousand dollars per name and $150+ share prices, most
  orders *are* odd lots — excluded from the Rule 605 data the 4.36 bps effective
  spread is calibrated on, and outside protected-quote status. The cost estimate
  is therefore optimistic by an unknown amount.
- **Broker rate limits and rejections.** The new intraday-margin regime means a
  real-time-blocking broker can refuse a trade the MIO just emitted. Live buying
  power must be a constraint *input*, and a rejected leg needs a graceful path or
  we carry an unintended one-sided position past the horizon.
- **Shorting is excluded** (`q_i ≥ 0`). Adding it requires locate/borrow, Schwab's
  daily-discretion borrow rate, and Reg SHO close-out — none of which is in this
  cost model.
- **Sequential independence.** Maximizing per-tick E[log W] is growth-optimal when
  bets are approximately sequentially independent. Overlapping positions and
  serially correlated signals violate that. The no-trade band and the cardinality
  cap mitigate it; they do not repair it. If holding periods routinely overlap,
  the honest form is a 2–3 step MPC (Boyd et al. 2017, *Foundations and Trends in
  Optimization* 3(1)) and that is a **separate ADR**.

---

## 12. Reproducing the measurements

The §4 tables came from four harnesses run in this container against pyomo
6.10.1 + highspy. They are **not committed**, so this review cannot independently
reproduce them. They also predate the corrected signed-trade, self-financing,
cost-in-CVaR, and in-model-band formulation. The implementing agent must first
commit a proper benchmark, then rerun it before ADR-0111 is approved. Only that
corrected run may establish the operating envelope; until then §4 is historical
evidence, not a production claim.

What they did, so they can be rebuilt exactly:

1. **Envelope sweep** — full constraint set, integer shares, worst of 8 seeds
   over `n ∈ {24,40,64} × S ∈ {128,256,512} × K ∈ {32,64}`; timed build and solve
   separately.
2. **Knot sweep** — scored every `K`'s chosen portfolio on the same exact
   criterion `E[log W]`, with `K=256` as reference, 6 seeds.
3. **Fidelity comparison** — exact tangent program vs. CVaR-only surrogate vs.
   MAD+CVaR surrogate, all under identical constraints, all scored on the same
   exact criterion.
4. **Determinism check** — 5 repeated solves of one instance, comparing share
   vectors exactly.

Synthetic data used a one-factor return structure (`r = beta·f·6e-4 + eps·9e-4 +
mu`), `mu ~ |N(0, 6e-4)|`, `pi ~ U(0.45, 0.80)` clustered near `q=0.55` so the
HFDR row actually binds, `W0 = 100k`, `B = 40k`, `cardinality = 10`,
`min_ticket = $1,000`. Real `U_r` scenarios will be block-bootstrap draws, not
one-factor draws; **re-run the envelope against real scenario matrices before
trusting §4.4 in production** — correlation structure changes MILP difficulty.
The rerun must also use current inventory, cash, signed buys/sells, exact
sell-only fees, and in-model inaction, and must publish the seeds and expected
result checks needed for another machine to reproduce it.
