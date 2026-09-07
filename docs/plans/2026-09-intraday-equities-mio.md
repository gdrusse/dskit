# The intraday_equities live MIO — design proposal

**Status:** PROPOSAL. Not approved, not built. **ADR-0109 must be written and
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
money periodically added**. Maximizing expected log wealth each tick is exactly
the growth-optimal (Kelly) policy, and asymptotically dominates any essentially
different strategy (Kelly 1956, *Bell System Technical Journal* 35(4);
Breiman 1961, *4th Berkeley Symposium*). An unset horizon is the case Kelly is
*for* — it needs no terminal date.

Two well-documented cautions, both handled below:

- Kelly's effective risk aversion is `1/W`, so it bets large and is **very**
  sensitive to errors in `mu` (MacLean, Thorp & Ziemba 2010, *Quantitative
  Finance* 10(7)). Fractional Kelly is the standard remedy; here the fraction is
  a declared knob and the `mu` error is handled explicitly by the robust
  counterpart in §5.
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

**Design consequence.** The Kelly fraction is not a constant. It is

```
f_t = f_base * g( PV_contrib / W_t )
```

with `g` a bounded, non-decreasing schedule declared in config, `f_t` clamped to
`[f_min, f_max]`. Early on, a bad path is repaired by the next deposit and a
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

**(a) A reservation hurdle (the shadow price of capital).** The dual on the
budget constraint *is* the marginal value of one more dollar. The tradition that
actually estimates such a price against a stream of forthcoming random requests
is network revenue management: a bid-price control accepts a request only if its
value exceeds the bid price of the capacity it consumes, and is asymptotically
optimal as demand and capacity scale together (Talluri & van Ryzin 1998,
*Management Science* 44(11)).

Implement as a **pre-solve filter**, not a constraint — it is cheaper and it
keeps the MILP clean:

```
fund candidate i only if   mu_net_i  >=  lambda_t
```

where `lambda_t` is a declared quantile of the historical distribution of best
net alpha per dollar arriving in the next `H` minutes, **conditioned on
time-of-day** (the open and the close are not the same market). `lambda_t` is
read from a calibration artifact pinned by hash; the optimizer must never
estimate it from the candidates it is choosing among.

**Self-check that costs nothing:** the solver already reports the dual on
`gross_budget` in the LP relaxation. Log it every tick. If the realized dual and
the configured `lambda_t` disagree systematically, `lambda_t` is miscalibrated.
That is a free, continuous validation of the hardest number in this design.

**(b) A no-trade band.** Under proportional costs the optimal policy is a
wedge-shaped inaction region — you trade to the nearest boundary, never to the
target (Constantinides 1986, *JPE* 94(4); Davis & Norman 1990, *Math. of OR*
15(4); Liu 2004, *Journal of Finance* 59(1)). A "rebalance to the optimum every
tick" loop is *provably wrong* under costs. The MIO therefore emits a **target**,
and a band suppresses `Δx_i` below `band_bps` of the position. Without this the
strategy pays the round trip in §3.4 repeatedly for noise.

**(c) Counterfactual logging (Perold).** Implementation shortfall splits into
execution cost on shares filled and **opportunity cost on shares never filled**
(Perold 1988, *Journal of Portfolio Management* 14(3); Wagner & Edwards 1993,
*FAJ* 49(1)). Every tick, record every candidate that cleared the hurdle but was
**not** funded, and mark it at its horizon. This is the only way to tell an
over-tight `q` or `lambda_t` from a well-set one. It is a ledger row, not an
optimizer term.

**(d) Cash is not idle.** Holding cash retains the option to take a better
signal later. The objective must not penalize unspent capital — with a
`gross_budget ≤ B` inequality (not equality) and the hurdle in (a), cash is the
default and every dollar deployed must earn its way past `lambda_t`.

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

**Round-trip hurdle ≈ 4.6 bps ≈ 0.046%.** Any `mu_i` below that is noise, before
any opportunity cost is charged. This number belongs in the config as
`min_edge_bps` and should be refused if set below the measured spread.

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

**So: haircut the input AND keep the constraint.** Use
`mu_eff_i = (1 − pi_i) · mu_i` in the objective (posterior-mean shrinkage toward
the null), and retain the ADR-0088 constraint as the portfolio-level cap.
Doing only the constraint is a known failure mode.

One caution to record: FDR estimators are **conservative under low power**
(Andrikogiannopoulou & Papakonstantinou 2019, *Journal of Finance* 74(5), show
~65% of ±2% true-alpha funds misclassified as zero). If `pi_upper` is also
conservative, the design is conservative twice. **Calibrate `q` against realized
hit rates, not against a nominal FDR level.** — OWNER.

---

## 4. Measured tractability — the binding section

Everything here was measured in this container: pyomo 6.10.1, highspy, HiGHS via
`appsi_highs`, single thread, `mip_rel_gap=0`, `random_seed=0`. Timings are
**build + solve**, worst case over independent random instances with the full
constraint set (integer shares, binding cardinality, binding HFDR, CVaR cap,
Bertsimas–Sim robust term). Do not re-derive these; re-run the harness if the
formulation changes.

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

**S = 512 breaks the budget.** The cost is super-linear in scenarios once the
binaries have to work. `S ≤ 256` is a hard config ceiling, refused at validate.

### 4.2 How many tangent knots (n=40, S=256, reference K=256, 6 seeds)

| K | worst total | share of reference E[log W] | names differing |
|---|---|---|---|
| 16 | 1.62 s | 99.88 % | 0 |
| **32** | **1.99 s** | **100.00 %** | **0** |
| 64 | 2.61 s | 99.99 % | 1 |
| 128 | 4.78 s | 100.00 % | 0 |
| 256 | 7.10 s | 100.00 % | 0 |

**K = 32.** It is exact to the reference on every seed and picks identical
names. Note `pmquant.DEFAULT_N_TANGENTS = 128` — 2.4× the cost for no gain on
this problem class. The graduated pack must take `n_tangents` as a knob and
default it per-caller, not inherit 128.

### 4.3 The surrogate was tested and rejected

The Levy–Markowitz/Pulley argument (§3.1) says a cheap linear risk surrogate
should be nearly as good at minutes horizons. We built two and scored their
chosen portfolios on the **same exact criterion**, E[log W] over the scenario
set, against the exact tangent program:

| form | worst time | exact log-growth captured |
|---|---|---|
| Exact log, tangent planes, K=32 | 2.0 s | 100 % (definitionally) |
| CVaR-only linear surrogate | 0.65 s | **76 – 87 %**, and on 1 of 8 seeds it **allocated nothing at all** |
| Mean-absolute-deviation + CVaR | **hit the 60 s limit** | n/a — never proved optimal |

Two findings, both counter to the prior expectation:

1. **The surrogate is not free.** It gives up 13–24 % of the growth rate and has
   a pathological zero-allocation mode, because its `gamma`/`kappa` weights have
   no principled mapping to a Kelly fraction — they are hand-tuned, and a
   mis-tuned pair refuses to trade. The exact program needs no such tuning:
   `kelly_fraction` is the only risk knob and its meaning is exact.
2. **The MAD risk term is catastrophically slower**, not faster, than the tangent
   form — its dense equality rows coupling every `x` to the portfolio mean wreck
   branch-and-bound. Row count is a bad proxy for MILP difficulty.

**Therefore: use the exact-log tangent program.** It costs ~2 s at the operating
point, which we have, and buys back a fifth of the compounding rate, which we
cannot get anywhere else. Do not "optimize" this into a mean-variance objective.

### 4.4 Operating point, pinned

```
n_candidates <= 40      (hard refuse above 64)
S            =  256     (hard refuse above 256)
K            =   32
solver       = appsi_highs, threads=1, random_seed=0, mip_rel_gap=0
time_limit   =    8.0 s  -- a BREAKER, not a tuning knob
```

**Worst measured: 3.0 s. Budget: 10 s. Headroom: ~3×.**

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

One decision tick. Index `i = 1..n` over candidates, `o = 1..S` over joint
net-return scenarios, `j = 1..K` over tangent knots.

### 5.1 Pre-solve filtering (cheap, and it shrinks the MILP)

A candidate enters the program only if **all** hold:

1. It is in the Gate-1 / Gate-3 eligible set carried by the bundle.
2. Its bundle entry is present, fresh, schema-compatible and hash-verifiable.
3. `mu_net_i = (1 − pi_i)·mu_i − cost_i ≥ max(min_edge_bps, lambda_t)` — the
   §3.3(a) reservation hurdle and the §3.4 friction floor.
4. Its quote is fresh, the market is not halted/locked/crossed, and its price
   clears `min_price` (the per-share TAF argument, §3.4).

If the eligible set is empty: **return zero positions and never wake the
solver** — the `BudgetedSelect` precedent. An empty gate that still solves is
capital treating its gate as decoration.

### 5.2 Variables

| symbol | domain | meaning |
|---|---|---|
| `q_i` | non-negative integer, `≤ floor(B / price_i)` | shares of candidate `i` |
| `y_i` | binary | candidate `i` is held |
| `W_o` | real, `[w_lo, w_hi]` | wealth in scenario `o` |
| `t_o` | real | utility surrogate in scenario `o` |
| `eta`, `z_o ≥ 0` | real | Rockafellar–Uryasev CVaR pair |
| `theta ≥ 0`, `p_i ≥ 0` | real | Bertsimas–Sim dual variables |

Let `x_i = price_i · q_i` (an expression, not a variable).

### 5.3 Constraints

```
(C1)  x_i <= x_max_i * y_i                      big-M link
(C2)  x_i >= min_ticket * y_i                   min ticket
(C3)  sum_i y_i <= cardinality
(C4)  sum_i x_i <= B                            gross budget  [INEQUALITY -- see 3.3(d)]
(C5)  sum_i (pi_upper_i - q) * x_i <= 0         ADR-0088 HFDR, robust form
(C6)  W_o == W0 + sum_i r_oi * x_i - cost       per-scenario wealth
(C7)  t_o <= u(k_j) + u'(k_j) * (W_o - k_j)     tangent planes, all (o, j)
(C8)  z_o >= -(sum_i r_oi * x_i) - eta          CVaR excess
(C9)  eta + (1/(1-beta)) * sum_o w_o * z_o <= cvar_limit
(C10) theta + p_i >= dev_i * x_i                Bertsimas-Sim on mu
```

`cost = sum_i (spread_bps_i * 1e-4 * x_i + taf_per_share * q_i)` — per-share and
per-notional terms kept separate (§3.4).

**(C5) is the locked ADR-0088 policy.** Note it is linear in `x`, and its robust
counterpart over a **rectangular** `U_pi` is exactly the substitution
`pi_i → pi_upper_i` shown. If `U_pi` is later a finite scenario set, add one
copy of (C5) per retained scenario — still linear, still cheap. **OWNER:** the
geometry of `U_pi` (box vs. scenario) is A18044 and is not settled.

### 5.4 Objective

```
maximize   sum_o w_o * t_o  -  epsilon * (Gamma * theta + sum_i p_i)
```

The first term is the fractional-Kelly expected utility; `u` is `ln(W/W0)` at
full Kelly and CRRA with `gamma = 1/f_t` below it (reuse `pmquant.mio.utility_at`
verbatim — it is correct and tested; do **not** rewrite it). The second term is
the budgeted-robust penalty on `mu` estimation error with budget `Gamma` and
per-name deviation `dev_i = kappa · sd_mu_i` drawn from `U_mu`.

### 5.5 Post-solve — assertions, not constraints

Recompute **exactly** from the integer solution and **raise** on violation:

- `outlay ≤ B` (a budget that does not bind is the defect `BudgetedSelect`
  documents),
- `W_o > 0` in every scenario,
- realized `E[log W]` and CVaR, re-billed at the exact per-share fee,
- `sum_i pi_upper_i·x_i ≤ q·sum_i x_i` on the exact numbers.

Refuse a solver termination that is not `optimal`. A `time_limit` hit is a
**halt** (exit code 3), not a degraded fill.

### 5.6 From target to orders

The MIO emits a **target** vector. Apply the §3.3(b) no-trade band, then hand
`Δ` to the `Proposer` seam in `dskit/production/decider.py`, which turns head
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
- per-scenario wealth rows from a caller-supplied payoff matrix,
- the CVaR (Rockafellar–Uryasev) block,
- the Bertsimas–Sim budgeted-robust block,
- the empty-gate short circuit and the post-solve exact recompute,
- knobs: `kelly_fraction`, `n_tangents`, `n_scenarios_max`, `cvar_alpha`,
  `cvar_limit`, `robust_budget`, `cardinality`, `min_ticket`, `solver`,
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
- the ADR-0088 HFDR row (C5) — this is *this project's* policy, not a general
  optimizer feature,
- the `lambda_t` reservation hurdle and the no-trade band,
- the counterfactual (unfunded-candidate) ledger rows.

**A `stat_test` node must be wired into its inputs** or the document refuses to
plan (`planner.py:603`). The eligibility gate is that node.

---

## 7. Input contract — the forecast bundle

Path A18256 locks this: **the MIO must refuse a missing, stale,
schema-incompatible or unverifiable bundle.** Fail closed, by name, before any
model is built. Required per candidate:

`decision_ts` · `entity` · `holding_horizon_minutes` · `model_release_id` ·
`mu_gross` · `mu_net` (cost-adjusted) · `pi_hat` · `pi_upper` · `U_pi`
provenance · `U_mu` (as `sd_mu` + `kappa` for the budgeted set) · `U_r`
(scenario matrix + weights + block rule + seed) · Gate-1/Gate-3 eligibility ·
every model/calibration/null/data hash · units · horizon semantics · coverage
targets · **expiry**.

**Portfolio state is a separate input and the bundle must never invent it:**
current positions, cash, live buying power, quotes, lot sizes, exposure limits.

Two hard rules for the implementing agent:

- **Normalize or separate incompatible holding horizons before they enter one
  scenario set** (A18042/A18047). A 1-minute and a 60-minute candidate in the
  same `W_o` row is an arithmetic error, not a modeling choice.
- **A serving path never restates a training knob** (CLAUDE.md). Read horizon,
  feature set and cost assumptions from the run dir or source config — never
  re-spell them in the serve document.

---

## 8. Implementation order

Each step ends green before the next begins. TDD, per the `dskit.production`
precedent.

1. **ADR-0109** in `docs/architecture/decision-log.md`. Wait for approval.
   Nothing below starts first.
2. **`ScenarioUtilitySolve`** in `dskit/pipeline/libs/pyomo.py` + tests in
   `tests/pipeline_libs/`. Must pass the purity gate (pyomo imported inside
   run-path methods only) and the conformance bar (declared `outputs`).
3. **Refactor `pmquant.mio` onto the base.** Its existing suite must stay green
   and byte-identical in behavior — this is the regression net for step 2.
4. **`EquityKellyMIO`** in the child + `configs/run-mio-*.json`.
5. **The calibration artifacts** — `lambda_t` by time-of-day bucket, `pi_upper`,
   `U_mu`, `U_r`. These are Path A18039–A18047 and are **empirical**; they
   cannot be config constants.
6. **Shadow mode.** Run the whole loop under `dskit.production` in `shadow`,
   then `paper`, logging the §3.3(c) counterfactual and the §3.3(a) dual-vs-
   `lambda_t` check. **No live capital until both read sane.**

---

## 9. Tests required

| Test | Why |
|---|---|
| Empty gate → zero positions, solver never invoked | the `BudgetedSelect` precedent |
| Budget binds on the exact recompute | the F-220 defect class |
| HFDR (C5) holds on the exact recompute | ADR-0088 is a *locked* policy |
| Stale/missing/unverifiable bundle → refuse by name | A18256 fail-closed |
| Mixed holding horizons in one scenario set → refuse | §7 |
| Solve at (n=40, S=256, K=32) completes under 8 s | the §4 envelope is a claim; pin it |
| `S > 256` or `n > 64` → refuse at validate | the envelope is a *contract* |
| Identical inputs → identical share vector, twice | determinism (§4.4) |
| `test_default_agrees_everywhere` for `n_tangents`, `kelly_fraction`, `cvar_alpha` | CLAUDE.md: a default belongs to ONE name |
| Cost model is per-share **and** per-notional | §3.4's 15× price dependence |

---

## 10. Owner decisions still open

None of these may be invented by the implementing agent.

1. `q` in the HFDR constraint — and it must be calibrated against **realized hit
   rates**, not a nominal FDR level (§3.5).
2. `U_pi` geometry: rectangular box or finite scenario set (Path A18044).
3. `f_base`, `f_min`, `f_max`, the shape of `g`, and the discount rate behind
   `PV_contrib` (§3.2).
4. `lambda_t`: which quantile, which time-of-day buckets, which lookback.
5. `cvar_alpha`, `cvar_limit`, `cardinality`, `min_ticket`, `band_bps`.
6. `Gamma` (robust budget) and `kappa` (deviation multiplier).
7. Cash account vs. margin — see §11.

---

## 11. Risks and things this design does not solve

- **Signal decay during decide-and-execute.** A ~2 s solve plus broker round trip
  plus queue time is a real fraction of a 1-minute alpha's half-life. Slower-
  decaying signals deserve more weight (Gârleanu & Pedersen 2013, *Journal of
  Finance* 68(6)). **Measure realized decay and refuse any horizon whose
  half-life is under ~5× decision latency.** This may disqualify h=1 outright —
  which matters, because h=1 is where this child's only positive gain cell lives.
- **PDT: the rule changed.** FINRA Regulatory Notice 26-10 (2026-04-20) replaces
  Rule 4210's day-trading provisions *in their entirety* — including the pattern-
  day-trader designation and the $25,000 minimum — with an intraday margin
  deficit standard effective 2026-06-04. **Verify Schwab's own implementation
  before relying on this**; it could not be confirmed from Schwab's site.
- **A cash account cannot run this strategy.** T+1 settlement makes repeated
  intraday round trips good-faith violations; three in 12 months triggers a
  90-day settled-cash-only restriction. Use margin. — OWNER.
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
6.10.1 + highspy. They are **not** committed — they were scratch instruments,
and CLAUDE.md forbids unrequested files. The implementing agent should rebuild
them as a proper benchmark under `tests/` or `children/intraday_equities/`
**after** ADR-0109 is approved, because §4.4's envelope is a contract the code
must keep, and a contract with no test is a claim.

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
