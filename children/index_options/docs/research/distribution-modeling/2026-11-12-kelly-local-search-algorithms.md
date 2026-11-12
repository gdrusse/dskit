# Kelly local search (ADR-0307) and multi-start local search (ADR-0315): the algorithms as built

**Status:** reference write-up, 2026-11-12. Describes code, not a proposal. Nothing here authorizes a run.

**Sources (read, not paraphrased from memory).**

| What | Commit | Files |
|---|---|---|
| ADR-0307 local search (`ls`) and Amendment 1 (FAST options) | `2a29d8f8` (`origin/main` at the time of the FAST worktree) | `children/index_options/index_options/kelly_local.py`, `children/index_options/index_options/ledger_studies.py`, `dskit/pipeline/libs/pyomo.py`, `docs/architecture/decision-log.md` |
| ADR-0315 multi-start | `711107ab` (HEAD of `claude/mio-ls-multistart-20261112`; at the time of writing `origin/main` also points here) | `children/index_options/index_options/kelly_multistart.py`, the same `ledger_studies.py` and `pyomo.py` |

`kelly_local.py` is the same file at both commits. Line numbers below are from `2a29d8f8`, except where a citation says `@711107ab`. ADR-0315 was still under review when this was written and may change. The multi-start description is of `711107ab`.

---

## 1. The weekly decision problem

### 1.1 Candidates

Each decision week the study builds one **group** per candidate book (a symbol and expiry) (`ledger_studies.py:4624-4626`). A group carries:

- `capacity`: an integer upper bound for each item. The items are the contract sides of the group's structures. There are two sides per structure, so `candidate_contracts = sum(len(capacity)//2)` (`ledger_studies.py:4580`).
- `exclusive`: pairs of items (the two sides of one structure) that cannot both be positive.
- `reserve_rows`: linear lower bounds on the group's reserve. The group reserve is `max(0, max_r R_r · q_g)`. These rows include the 20% short-notional floor (`_charge_coefficients`, `ledger_studies.py:4626`).
- `linear_rows`: `coefficients · q_g <= upper`.
- `grid`, `masses`, `losses`, `credit`: the forecast's settlement atoms, their probabilities, the loss of each item at each atom, and the premium.

The program also has:

- shared **caps** over groups: `sum_{g in cap} reserve_g <= upper`;
- optional **joint rows** across groups: `sum_g J_g · q_g <= upper`.

The decision variable is a nonnegative integer vector `q` with one entry per item.

### 1.2 The one-stage fractional-Kelly program (ADR-0299)

The search climbs the one-stage program's objective. It never optimizes the dollar program.

- **Fraction to risk aversion.** `gamma = 1/fraction` (`ledger_studies.py:4590`). So `kelly050` has gamma 2 and `kelly075` has gamma 4/3.
- **Utility.** `tangent_utility` (`pyomo.py:174`) is `u(w) = ln(w/w0)` when gamma <= 1. Otherwise it is CRRA: `u(w) = ((w/w0)^(1-gamma) - 1)/(1-gamma)`.
- **Reference wealth.** `w0 = account.remaining(symbols, 1.)`: funding less the reserves of open books (`ledger_studies.py:4587`).
- **Wealth floor.** `wealth_lo = wealth_floor x w0`, which is `.01 w0` (`ledger_studies.py:4593`). It is a hard bound in every scenario.
- **Scenarios.** The scenarios are **not** sampled joint settlements. Sampled settlements belong to the ADR-0300 sizer, and ADR-0307 explicitly chose not to use them (decision-log "Why this objective…", line 36047). The scenarios are:
  - the kelly block's 16 basket moves, `KELLY_MOVES = ±.01, ±.02, ±.035, ±.05, ±.075, ±.1, ±.125, ±.15`;
  - plus one quiet cell (`market:0`).

  That is at most 17 scenarios.
- **Scenario weights.** Each weight is the mass that a Student t gives to the move's Voronoi cell in log space (`_move_weights`, `ledger_studies.py:3173-3225`). The t has 4 degrees of freedom and is scaled to the equal-weight basket's trailing daily volatility (`beta_lookback` 60 sessions) times `sqrt(sessions in the mean candidate DTE)`.
- **Scenario wealth.** For each move, a book's scenario term is its terminal profit at spot `x (1 + beta_name x move)` minus its no-move profit. That term is then centred so that the probability-weighted deviation is zero (`_move_terms`, `_centred_scenarios`, `ledger_studies.py:3114-3155`). Scenario `s` has wealth

  `W_s(q) = w0 + sum_i q_i (E_i + T_{i,s})`

  where `E_i = credit_i - masses · losses_i` is the item's expected profit under the forecast (`_expected_profit`, `pyomo.py:4366`).
- **Dispersion charge.** Idiosyncratic risk enters as a second-order charge per name, not as scenarios (`_charge_plan`, `pyomo.py:~4400-4446`):
  - For each charged group, `M_g` is the book's payoff dispersion over its own atoms (`_AtomDeviation`). It is the larger of `1.25 x` the MAD and the Cauchy-Schwarz lower bounds on the sd.
  - `S_name = sum_{g in name} M_g`.
  - The charge is `slope x S_name^2` with `slope = gamma/(2 w0)`.
- **Objective.** The exact objective is

  `V(q) = w0 x sum_s p_s u(W_s(q))  -  sum_name slope x S_name(q)^2`

  This is `TransportActionScore.evaluate(q)["utility"]["exact"]` (`pyomo.py:5972`). It is not the tangent envelope that the integer program carries.
- **Cap.** One cash-rule cap applies: the new reserve may not exceed `remaining(symbols, portfolio_fraction - cash_buffer)`, which is `.95` of uncommitted funding (`_limits`, `ledger_studies.py:3527-3531`).
- **No joint rows in practice.** A kelly method declares no `shock`, so its one-stage program has no joint rows unless exposure limits add some. The shock rows built at `ledger_studies.py:4650` are only used to record losses.

**Cash is the zero book.** At `q = 0`, every `W_s = w0`, so `u = 0`, `S = 0` and `V(0) = 0`. Cash is always feasible, because every cap and joint upper bound is nonnegative. Any book with `V < 0` is therefore worse than cash under the program's own objective.

### 1.3 The stage-one dollar program (`w1_010_shock10`)

ADR-0307 starts its climb from the selection of a dollar MILP: the "sized arms' stage one", built inside `_local_search_week` (`ledger_studies.py:3998-4004`).

- **Groups.** The same configured groups, at radius `start.rho = .10` (W1-robust: credit minus the W1-worst expected loss within radius `.10 x scale`).
- **Joint rows.** One joint row per `SHOCK10` move. `SHOCK10 = {moves: [-.1, -.05, .05, .1], loss_fraction: .1, beta_lookback: 60}`. Each row requires the portfolio's terminal loss at the shocked spot to be at most `.10 x funding` (`_shock_rows`, `ledger_studies.py:3095-3105`).
- **Caps.** The dollar caps (`_limits(..., stage1=True)`, `ledger_studies.py:3537-3545`): portfolio, weekly (`weekly_fraction x funding`), per-name and component. The kelly base declares `portfolio_fraction 1`, `name_fraction 1` and `component_fraction None`.
- **Objective.** The dollar objective of `TransportPortfolioSolve`, with no utility block.

---

## 2. ADR-0307 local search (`ls`)

### 2.1 The week pipeline

```
_local_search_week(day, method, kelly, program, ...):                      # ledger_studies.py:3983
    dollar  = stage-one program (§1.3)
    solver  = TransportPortfolioSolve(document solver block
                                      [+ time_limit = stage1_time_limit if declared])
    first   = solver.run(dollar)            | live
            | solver._recorded(...)         | recorded replay
            | solver.derived(...)           | worker actions
    screen  = solver.last_screen            # recorded as kelly.screen
    timed_out = "timeout" in first
    incumbent = timed_out and incumbent_start and first.candidate is not None
    scorer  = TransportActionScore(program)  # the one-stage program, never built as a model
    start   = first.candidate.quantities if incumbent
              else None (cash)           if timed_out
              else first.quantities
    out     = KellyLocalSearch(scorer, budgets [+ cash_floor, repair]).run(start, kind)
    if timed_out and not incumbent: start_kind = "stage1_timeout_cash"
    replay checks (§5)
```

### 2.2 Group LP screen (inside stage one)

Every kelly document must declare `group_screen: "lp"` (`KELLY_GROUP_SCREEN`, `ledger_studies.py:3029`). Before stage one builds its MILP, `_screen_metadata` (`pyomo.py:4512-4554`) works as follows:

1. Each group's own LP relaxation is solved alone with `scipy.optimize.linprog(method="highs")` (`_group_lp_bound`, `pyomo.py:4449-4510`). The relaxation:
   - ignores caps and joint rows;
   - keeps capacities, linear rows, the sparse W1 dual (potentials and a radius multiplier) and any risk-charge epigraph;
   - drops integrality only.
2. A group is kept when its bound is greater than `parity_tolerance`.
3. If no group passes, the best one is kept as `fallback`.
4. `group_screen_keep` (ADR-0301) is refused with `local_search`, so no top-k cut is applied.

The screen restricts the candidate set. A group with no standalone dollar edge is dropped, even if it would have loosened a joint row. `last_screen` (`{rule, candidates, kept, screened, fallback}`) is recorded as `kelly.screen`.

### 2.3 Stage-one MILP and its solver block

Stage one runs under the **document's** solver block. `_gap_within` (`ledger_studies.py:3031-3055`) refuses a kelly document unless it declares every item in this table:

| Key | Requirement (kelly method value) |
|---|---|
| `solver` | `appsi_highs` (needed for `timeout_policy: return`) |
| `timeout_policy` | `"return"` |
| `solver_options.time_limit` | >= method `time_limit` = **2,400 s** |
| `absolute_gap`, `solver_options.mip_abs_gap` | <= **1** (dollar) |
| `relative_gap`, `solver_options.mip_rel_gap` | <= **.10** |
| `solver_options.mip_feasibility_tolerance` | <= **1e-9** (at the HiGHS default of 1e-6 the charge variable sits up to ~0.006 under its lines) |
| `parity_tolerance` | >= **1e-3** |
| `group_screen` | `"lp"` |

The values in this table are the validated bounds and the test helper's declaration (`_for_kelly`, `test_ledger_studies.py:3440-3450`). The production documents live outside the repo. The FAST variant replaces only `solver_options.time_limit`, with `stage1_time_limit` (`ledger_studies.py:4006-4009`).

**Solve.** In return mode, `_solve_once` (`pyomo.py:4775-4794`) offers a cash warm start and calls HiGHS with `load_solutions=False`. When the result has no solution, it marks `_transport_no_incumbent`.

**Extraction.** `extract` (`pyomo.py:5712-5779`) reprices the incumbent independently. It refuses any objective, bound or valuation miss larger than `parity_tolerance`, and it replaces a value <= 0 with cash. It then classifies a native `maxTimeLimit` (`_timeout_reason`, `pyomo.py:5263-5271`):

- `missing_bound` if there is no bound;
- `gap` if `bound - value > max(absolute_gap, relative_gap x |value|)`;
- otherwise certified.

A timeout returns the envelope `{timeout, candidate, certificates}`. The candidate is the checked incumbent.

**No incumbent: `_empty_timeout`** (`pyomo.py:5273-5306`). A `maxTimeLimit` with no solution is `no_incumbent`. An `error` termination with no solution, objective, bound or gap is `solver_error` (commit `a42c4d80`). Both return `{timeout: reason, candidate: None}`. On replay, the evidence must match the rebuilt model's variable and constraint counts. The recorded reason must also be the one the certificate implies.

### 2.4 Start kinds

| `start_kind` | When | Start book |
|---|---|---|
| `stage1` | Stage one finished (certified). This includes a stage one whose selection is cash: the code records `stage1` with an all-zero start. | stage-one quantities |
| `stage1_timeout_cash` | Stage one timed out (`gap`, `missing_bound`, `no_incumbent`, `solver_error`) and no incumbent start applies | cash |
| `infeasible_start_cash` | The stage-one book fails `scorer.check_feasible` on the Kelly program (without `repair`, or a repair that reached cash) | cash |
| `stage1_incumbent` | FAST only: timeout with `candidate` present and `incumbent_start: true` | timeout candidate |
| `repaired_stage1`, `repaired_stage1_incumbent` | FAST only: infeasible start repaired to a nonempty feasible book | repaired book |

A `solver_error` is a stage-one timeout reason (`first["timeout"]`). It is not a separate `start_kind`. It yields `stage1_timeout_cash`, and under FAST it is also recorded in `kelly.local.stage1_timeout`. Relevant code: `kelly_local.py:400-411`, `ledger_studies.py:4032-4045`.

### 2.5 Data layout, built once (`_build`, `kelly_local.py:107-176`)

- Items are flattened: there are `N` items, with group offsets `off`.
- `rank` is the item rank: groups sorted by `id`, then item index. Every tie breaks on this rank.
- `D[i, s] = E_i + T_{i,s}`. This is the N x S payoff matrix (about 154,000 x 17, 19 MB at production scale).
- Each group has reserve rows `R_g`, linear rows `(lin_coef_g, lin_up_g)` and exclusive partner lists.
- Each cap has a member list. Each joint row has a column `J[:, k]`.
- Per name there is a `slope`. Each charged group has its `_AtomDeviation` measure and atom payoff rows. Each item has `own_i`, its own one-contract spread.

The search keeps this state for the current `q`:

- `W` (S vector), per-group `Rq`, `res`, `lin`, `pay`, `spread`;
- `capsum`, `jtot`, `keytot` (the sum of spreads per name);
- one-contract-add masks `dres` and `lin_ok` (`_refresh_group`, `_refresh_totals`, `_load`, `kelly_local.py:180-217`).

`value()` returns the exact `V(q)` (`kelly_local.py:227-230`).

### 2.6 Exact incremental trial (`_trial`, `kelly_local.py:234-284`)

A trial takes `changes = {item: ±k}`. It returns the exact objective after the move, or `None` if the move is infeasible. Every rule is a hard check, never a penalty:

```
for each changed item: 0 <= q_i + dq_i <= cap_i                     else None
no item > 0 whose exclusive partner > 0 (after the change)          else None
W' = W + sum dq_i D[i];   min_s W'_s >= wealth_lo - tol             else None
joint: jtot + sum dq_i J[i] <= j_up + tol                           else None
per touched group g: lin_g + lin_coef_g[:, cols]·step <= lin_up + tol else None
                     reserve'_g = max(0, max_r(Rq_g + R_g[:, cols]·step))
                     spread'_g  = price x measure(pay_g + rows_g[:, cols]·step)
caps: capsum_c + sum_g∈c (reserve'_g - res_g) <= cap_up_c + tol     else None
return w0 · p·u(W')  -  slopes · keytot'^2
```

`tol` is the scorer's `parity_tolerance`. Each call increments `evaluations`. The tests hold this trial to `TransportActionScore` (feasibility and exact value) on random books and moves in three program shapes.

### 2.7 Move set and candidate ranking

The search has three moves:

- **add**: `{b: +1}`
- **remove**: `{a: -1}`
- **swap**: `{a: -1, b: +1}`, where `b` may be in the same group as `a` or another one

**First-order estimates** (`_estimates`, `kelly_local.py:293-302`), with `u'` taken from `tangent_utility`:

```
marginal_i  = sum_s D[i,s] · w0 · p_s · u'(W_s)          # one mat-vec
charge_i    = 2 · slope_name(i) · keytot_name(i) · own_i # bound on the charge's first-order growth
add_gain_i    = marginal_i - charge_i
remove_gain_i = charge_i - marginal_i
```

**Shortlists** (`_shortlists`, `kelly_local.py:304-321`):

- `eligible` = items with `q < cap` whose exclusive partner is not held.
- **Swap pool** = the top `adds_evaluated` eligible items by `add_gain` (ties by rank). The pool ignores cap and fit checks on purpose, so a swap can free the reserve an add needs.
- **Add shortlist** = the top `adds_evaluated` items that are eligible **and** pass the cached one-contract fit checks:
  - linear rows (`lin_ok`);
  - reserve growth `dres` within the tightest slack of the caps;
  - joint rows;
  - the wealth floor in every scenario.
- **Removals**: every held item.
- **Swaps**: every (held `a`, pool `b`, `a != b`) pair, ranked by `remove_gain_a + add_gain_b` (ties by rank of a, then rank of b). The top `swaps_evaluated` are kept.

The shortlist only ranks moves. The exact trial decides which one is taken.

### 2.8 Acceptance, climb, termination (`_best_move`, `_climb`, `kelly_local.py:323-370`)

```
_best_move():
    current = V(q)
    for each candidate move m (adds, all removals, top swaps):
        v = _trial(m); skip if None
        key = (-(v - current), kind{add 0, remove 1, swap 2}, ranks)
    best = argmin key                      # steepest ascent, deterministic ties
    accept only if best.gain > IMPROVEMENT x w0   (IMPROVEMENT = 1e-9)

_climb():
    repeat up to `moves` times:
        m = _best_move(); if none: return (values, kinds, local_optimum=True)
        apply m (refresh touched groups and totals)
    return (values, kinds, local_optimum=False)   # budget exhausted
```

- **Termination.** The climb stops at a local optimum of the evaluated neighbourhood, or when `moves` accepted moves have been spent.
- **Monotonicity.** Values never decrease.
- **Meaning of `local_optimum`.** It means no evaluated move improves the objective. It does not mean that no single-contract move does.

### 2.9 Restarts, seed, determinism (`run`, `_perturbed`, `kelly_local.py:414-431, 527-538`)

- `restarts` extra climbs are run. Each starts from the best book so far, with each held contract unit dropped independently with probability `PERTURBATION = .25`.
- The randomness comes from `numpy.random.default_rng(seed)`, over units ordered by rank.
- A perturbation is skipped and counted in `restarts_skipped` if it drops nothing or if the result fails `check_feasible`.
- The best book across climbs is kept. A later climb replaces it only if it beats it by more than `1e-9 w0`.
- **As built, every registered `ls` method declares `restarts: 0`**, so there is one climb.
- Determinism: every choice depends only on the program, the parameters and the seed. Nothing reads the clock (`seconds` is returned outside the record).

### 2.10 FAST options (ADR-0307 Amendment 1)

All four options are opt-in. When a key is absent, behaviour and record are byte-identical to the original.

| Key | Where | Effect |
|---|---|---|
| `stage1_time_limit` (s > 0) | `ledger_studies.py:4006-4009` | Replaces the stage-one HiGHS `time_limit`. Nothing else in the solver block changes. |
| `incumbent_start` (bool) | `ledger_studies.py:4035-4045` | A timeout that carries a `candidate` (reason `gap` or `missing_bound`) starts from that candidate (`stage1_incumbent`). `kelly.local.stage1_timeout` records the reason, or `None` if stage one finished. |
| `cash_floor` (bool) | `kelly_local.py:432-439` | If the best end value is below `V(0)`, trade cash. `kelly.local.cash_floor` records whether this happened. |
| `repair` (bool) | `kelly_local.py:405-408, 506-525` | Repairs an infeasible start by greedy removal (below). `kelly.local.repaired_contracts` counts the removals. |

**Repair.** While the book is not empty and is not accepted (`_excess() > 0` or the scorer refuses it), remove one contract of the held item that minimizes the tuple:

1. the change in summed breach;
2. `-V` after the removal;
3. rank.

The summed breach (`_excess`, `kelly_local.py:461-478`) adds up excess capacity, the overlap of exclusive pairs, linear-row excess, wealth-floor shortfall per scenario, joint-row excess and cap excess. It mixes units, so it is used only for ranking. The repair ends at a feasible book, at worst cash.

### 2.11 Final checks and record (`kelly_local.py:440-457`)

- The returned book goes through `scorer.evaluate`.
- If `|exact - end_value| > 1e-9 x max(1, |exact|, w0)`, the search raises an error. This is a defect, not a skipped week.
- The record `kelly.local` is `{start_kind, start_value, end_value, start_contracts, end_contracts, moves, evaluations, local_optimum, budget_exhausted, restarts, restarts_skipped, move_counts{add, remove, swap}}`, plus the declared FAST fields.

### 2.12 Registered parameter values

`kelly050_ls_lp` (`test_ledger_studies.py:6489-6495`) is the following, at fraction `.5`:

```
_KELLY_BASE = {rho: 0, portfolio_fraction: 1, name_fraction: 1, component_fraction: None,
               short_notional_fraction: .2}
KELLY = {n_tangents: 32, core: 2., wealth_floor: .01, moves: KELLY_MOVES, beta_lookback: 60,
         cash_buffer: .05, gap: {absolute: 1, relative: .10}, time_limit: 2400}
LOCAL_SEARCH = {moves: 200, adds_evaluated: 64, swaps_evaluated: 64, restarts: 0, seed: 20261109,
                start: {rho: .10, shock: SHOCK10}}
```

`kelly075_ls_lp` is not in `LOCAL_FORMULATIONS`, which holds `kelly025_ls_lp` and `kelly050_ls_lp`. The replay-hedge pairing test defines it as `kelly050_ls_lp` with `fraction .75` (`test_ledger_studies.py:6392-6395`).

| Parameter | `kelly050_ls_lp` | `kelly075_ls_lp` | `kelly050_ls_fast_lp` | `kelly075_ls_fast_lp` |
|---|---|---|---|---|
| fraction / gamma | .5 / 2 | .75 / 1.333 | .5 / 2 | .75 / 1.333 |
| moves | 200 | 200 | 200 | 200 |
| adds_evaluated | 64 | 64 | 64 | 64 |
| swaps_evaluated | 64 | 64 | 64 | 64 |
| restarts | 0 | 0 | 0 | 0 |
| seed | 20261109 | 20261109 | 20261109 | 20261109 |
| start | rho .10, SHOCK10 | same | same | same |
| stage1_time_limit | (document, >= 2,400 s) | (document) | **300 s** | **300 s** |
| incumbent_start | off | off | **true** | **true** |
| cash_floor | off | off | **true** | **true** |
| repair | off | off | **true** | **true** |

The FAST rows come from `FAST_LOCAL_SEARCH = {**LOCAL_SEARCH, stage1_time_limit: 300., incumbent_start: True, cash_floor: True, repair: True}` (`test_ledger_studies.py:6498-6503`).

`local_search` is refused together with any of `max_groups`, `accept_uncertified`, `search`, `warm_start` or `outer_approx` (`_LOCAL_SEARCH_EXCLUDES`, `ledger_studies.py:2423`).

---

## 3. ADR-0315 multi-start local search (`@711107ab`)

### 3.1 Overview

`KellyMultiStart(KellyLocalSearch)` solves **no stage-one MILP**. A method declares `local_search.multistart` instead of `start`, and `_local_search_week` dispatches to `_multistart_week` (`ledger_studies.py@711107ab:4011-4012, 4089-4133`). The scorer, the trial, `value()` and the add, remove and swap moves are inherited unchanged.

### 3.2 Per-group LP books

`lp_books()` (`kelly_multistart.py@711107ab:179-186`) works on the **one-stage program's** groups (radius 0). For each group it calls `TransportPortfolioSolve._group_lp_relaxation(group)` (`pyomo.py@711107ab:4460`), the screen's LP from §2.2. The call returns `(bound, actions)`, and the fractional actions are clipped to `[0, capacity]`. `_group_lp_bound` now returns `_group_lp_relaxation(group)[0]`, which is the same LP and value as before.

### 3.3 Start generation (`starts`, `kelly_multistart.py@711107ab:197-226`)

```
live  = groups with bound > parity_tolerance
kept  = top `top_groups` of live by (-bound, group id)
round(g, s) = min(floor(s·book_g + 1/2), cap); for each exclusive pair with both > 0,
              zero the smaller (tie: keep the lower index)
starts (deduplicated by sparse book, first kept, stop at `starts`):
    1. cash
    2. for s in scales: for g in kept: round(g, s)                      ("group")
    3. for s in scales: for j = 2..|kept|: union of round(kept[:j], s)  ("union")
    4. rng = default_rng(seed); up to DRAWS(20) x starts draws:
         size ~ U{1..|kept|}; subset ~ choice without replacement;
         each member at a random declared scale                         ("random")
```

`digest(starts)` is the SHA-256 of the compact JSON of `[[kind, sorted([flat item, qty])], ...]`, in start order (`:229-233`).

### 3.4 Batch scoring and choosing the climbs (`run_multistart`, `:237-290`)

1. Load cash. Score every start with one `_trial(start)` from the cash state. Cash itself scores `V(0)`. An infeasible start scores `None` and is never climbed. These trials count toward `evaluations`.
2. Take the `climbed` best feasible starts by `(-score, start index)`.
3. **Evaluation budget (optional `evaluations`).** Before climb `n` of `k`, `stop = evals + max(0, budget - evals) // (k - n)`. `_climb(stop)` checks `evaluations >= stop` only at move boundaries, so a single move can overshoot the stop. A stopped climb counts as `budget_exhausted`.
4. Climb each chosen start with the inherited moves plus the pair move. A climb's end book replaces the best only if it beats it by more than `1e-9 w0`, so ties go to the earlier climb.

### 3.5 Pair (spread) add (`_pair_moves`, `_best_move`, `:110-158`)

```
lead  = top adds_evaluated eligible items by add_gain (fit NOT required)
pairs = {(a, b): a in lead, b eligible in a's group, b != a, b not a's exclusive partner},
        normalized to rank(a) < rank(b), deduplicated
rank pairs by -(add_gain_a + add_gain_b), then rank a, then rank b; keep pairs_evaluated
move  = {a: +1, b: +1}, scored by the exact trial
```

A pair is taken only if it clears `1e-9 w0` and is **strictly** better than the best single move (`_best_move`, `:145-158`). On a tie the single move wins. `move_counts` adds a `pair` count. Setting `pairs_evaluated: 0` disables the pair move.

### 3.6 Cash floor and final checks

The floor is built in, not optional: if no feasible climb exists, or the best end value is below `V(0)`, the week trades cash and records `cash_floor: true` (`:285-287`). The parity check against `scorer.evaluate` is the same as ADR-0307's.

### 3.7 Knobs and registered values

The declaration is validated by `validate_multistart` (`:77-99`):

- `restarts` must be 0;
- the FAST keys `cash_floor` and `repair` are refused, and so are `stage1_time_limit` and `incumbent_start`, because the budget-key set must match exactly;
- `scales` must be a nonempty list of distinct numbers in (0, 1];
- `start` and `multistart` are exclusive (`ledger_studies.py@711107ab:2941-2947`).

| Key | Meaning | `kelly050/075_ls_multistart_lp` (ADR-0315) |
|---|---|---|
| fraction | Kelly fraction | .5 / .75 |
| moves | accepted moves per climb | 1000 |
| adds_evaluated | add shortlist and pair leads | 64 |
| swaps_evaluated | swap shortlist | 64 |
| restarts | must be 0 | 0 |
| seed | random-start generator | 20261112 |
| top_groups | LP books kept | 16 |
| starts | starts generated (cash included) | 128 |
| climbed | starts climbed | 8 |
| pairs_evaluated | pair shortlist | 64 |
| evaluations | cap on exact trials over all climbs | 1,000,000 |
| scales | rounding fractions | [1, .5, .25, .125] |

### 3.8 Record and refusal

`kelly.local` carries ADR-0307's fields with `start_kind: "multistart"`, `restarts: 0` and `move_counts.pair`. It also carries `multistart`:

- `seed`, `declared`;
- `lp_live`, `lp_groups`, `lp_values`;
- `starts`, `starts_feasible`, `starts_digest`;
- `cash_value`;
- `climbs[{start, kind, start_value, end_value, moves, local_optimum}]`;
- `chosen_start`, `chosen_kind`, `cash_floor`.

`kelly.screen` holds the LP counts. The week has no `stage1` and one certificate (`local_search`, uncertified, no bound) (`:302-318`; `ledger_studies.py@711107ab:4132-4133`).

The recorded route reruns the search and refuses in these cases:

1. A different selection.
2. A different `seed`, `starts_digest` or `chosen_start` (`ledger_studies.py@711107ab:4124-4131`).
3. Any other difference, refused by the whole-result comparison.

---

## 4. Replay-hedge pairing (hedged variants)

The owner paired `kelly050_ls_lp` and `kelly075_ls_lp` with the sealed **replay** hedge (decision-log 2026-11-12 pairing, line 36067). This uses ADR-0312/0313's `hedge` block, `FixedDeltaForwardHedge` with `mode: "replay"`, on distinct ids `*_ls_replay_hedge_lp`.

- **What is unhedged.** The stage-one selection and the local objective. The hedge does not enter either program.
- **What the hedge adds.** After each filled book, the overlay attaches a cash-settled forward. Its size is `-ratio x Black-76 forward delta` per item, netted and truncated to one whole quantity per book. Its reserve, costs (fee, half-spread, borrow, hard-to-borrow proxy) and settlement then feed the account before later decisions. So hedged and unhedged arms trade the same week-one books, and later weeks can differ through the account.
- **Same pairing for the other families.** The FAST variants (`*_ls_fast_replay_hedge_lp`) and the multi-start variants (`*_ls_multistart_replay_hedge_lp`) pair the same way, with no new code.
- **ADR-0314.** ADR-0314's daily long-stock inventory is a separate, opt-in replay adapter over saved MIO output (Amendments 1-2). It is not part of the `ls` decision.

---

## 5. Recorded evidence and replay parity

**Recorded per `ls` week:**

- `stage1`: the selection, or the timeout envelope, with its certificates;
- `certificates`: stage one's, then the search's `{strategy_outcome: local_search, certified: false, objective: end_value, bound/gap/seconds/bound_source: None}`;
- `kelly.local` (§2.11), `kelly.screen`, `kelly.solve`;
- `selection`, `books`, `status` (`selected` or `cash`).

None of the decision fields read the clock.

**Recorded route** (`ledger_studies.py:4024-4030, 4068-4069`):

1. `solver._recorded` rechecks the recorded stage one, including any timeout envelope (`_empty_timeout` for no-incumbent and solver-error weeks).
2. The climb is re-run.
3. A selection that differs is refused.
4. The whole-result comparison then holds every `kelly.local` field.

**Derived route (method worker)** (`:4011-4023, 4049-4067`):

1. Stage one is re-derived from the worker's integer actions and certificate through `solver.derived`. Every valuation is done on this host.
2. The climb is re-run.
3. The result is refused if the claimed final actions differ, or if the one search certificate differs. The objective is compared within `parity_tolerance`.

**Live reproducibility.** Replay is exact. Two **live** runs of a FAST week whose stage one hits the 300 s cap can still differ, because which incumbent HiGHS holds at the cap depends on the host and clock (Amendment 1, "Replay parity").

**Multi-start.** There is no MILP, so both routes rerun the whole search (§3.8). The LPs come from scipy's HiGHS, so a different build could return a different vertex of a degenerate LP. A replay would then refuse; it would not pass silently.

---

## 6. Empirical results so far

These figures were relayed by the coordinator. They are not recomputed in this document. Quarters are listed in the owner's registered order.

| Method | 2019Q4 | 2021Q2 | 2020Q1 | 2021Q1 |
|---|---|---|---|---|
| `kelly050_ls_lp` | +3,237 | +10,345 | +17,486 | +11,349 |
| `kelly075_ls_lp` | +6,718 | +10,829 | +21,646 | rerun pending |

- **Climb.** 0 to 78 accepted moves a week and 64 to 10,677 exact evaluations. It never exhausted the 200-move budget. ADR-0315 reports that climbs are almost pure removal (for example 136 to 16 contracts), at about 28,000 evaluations a second, so about one second a week.
- **Wall time.** 0.9 to 3.5 h a quarter, almost all of it the stage-one dollar MILP (up to 2,400 s on a week that times out).
- **Pre-registered reading.** Weeks whose `start_kind` is not `stage1` are reported separately (ADR-0307 reading).
- **Known caveat (ADR-0307).** The objective inherits ADR-0299's defects:
  - Idiosyncratic risk is a second-order charge per name and is independent across names, so the optimum concentrates in the book with the highest edge-to-dispersion ratio.
  - Books are sized on the forecast mean, which the calibration gate found to **anti-rank** outcomes.

  A better optimizer of this objective is therefore a purer bet on that mean, and it can trade worse. This is why FAST and multi-start are separately registered methods, never substitutes for the `ls` arms.
- **Status.** Every result is uncertified. `end_value` is only a lower bound on the one-stage optimum.
