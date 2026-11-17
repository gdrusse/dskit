# The weekly index-option MIO: exact formulation and how it is solved

Date: 2026-11-17. Scope: the lead method family `kelly075_ls_fast_hedged_static_*_s_lp`. The tilt-only lead
`kelly075_ls_fast_hedged_static_dte_s_lp` ("sdte" in the run notes) is written out in full. The research grid varies
`dte_tilt`, `accuracy`, `kelly.fraction` and `local_search.start.rho` around it.

This memo is a description of code. It reports no new result. Every claim about behaviour cites the code.

## 0. Revision, sources and how to read the citations

- **Code revision.** All `file:line` citations are at commit `b40a378588735566f699427782a5cd2be413579f` ("MIO future-quarter
  run: lift the development seal to the owner cap 2024-01-01"). The unseen-quarter bundle
  `2020q2-fsdte-t40` was built from this commit. Its `study.json` code digests match this commit's `ledger_studies.py`,
  `kelly_local.py`, `kelly_sizer.py`, `mio_worker.py` and `pyomo.py` byte for byte (checked 2026-11-17, SHA-256).
- **The development-quarter runs.** These used `b6e083d0`. The diff `b6e083d0..b40a3785` touches only memory release
  (`_release_week`, `_return_freed_memory`, `ParityForwards.release`), worker logging and the seal date
  (`dskit/pipeline/method_blocks.py:36`, now `2024-01-01`). The formulation is the same at both, but the line numbers
  differ.
- **Availability.** Neither `b40a3785` nor `b6e083d0` is an ancestor of `origin/main` (`78c18df9`) on 2026-11-17. At
  that date `b40a3785` was only on the local branch `claude/mio-future-seal-20261116`. A reader on GitHub can follow
  these citations only once that commit is pushed. On `origin/main` the same functions exist at other lines, or not at
  all.
- **Path abbreviations** (all relative to the repo root):

| tag | file |
|---|---|
| LS | `children/index_options/index_options/ledger_studies.py` |
| KL | `children/index_options/index_options/kelly_local.py` |
| ND | `children/index_options/index_options/nodes.py` |
| CT | `children/index_options/index_options/contracts.py` |
| MW | `children/index_options/index_options/mio_worker.py` |
| PY | `dskit/pipeline/libs/pyomo.py` |
| HO | `dskit/pipeline/hedge_overlay.py` |
| OL | `dskit/pipeline/libs/option_ledger.py` |
| DL | `docs/architecture/decision-log.md` |

- **Method block.** The parameter values below are taken from the method block in the bundle
  `2020q2-fsdte-t40/study.json` (`domain.params.methods[0]`, id `kelly075_ls_fast_hedged_static_dte_s_lp`). The block
  is reproduced in Appendix A.
- **Outside the repo.** The evidence for the limitations in section 9 sits in run notes on the D: drive. These are
  cited by path; they are not part of this commit.

## 1. The weekly clock: what is known when, and what carries over

**Decision days.** The study's calendar is the sessions of the packet's clocks. A decision day is the first session of
each ISO week among the entry sessions (LS:3101-3106).

The account loop walks every session in order (LS:5272). Each session runs these steps in this order:

1. Split adjustments are applied (LS:5273). The hedge's daily re-strike is a no-op for this static hedge (LS:5275,
   HO:196).
2. Deposits and settlements: `account.advance(day, settlements)` credits any due deposit and settles every open book
   that expires today at today's close (LS:5276, LS:4909-4929, OL:1392-1438).
3. Fills: last decision's books are filled at this session's quotes, then hedged and admitted (LS:5277-5314). See
   section 7.
4. On a decision day: the menu is built and the week is solved (LS:5315-5661).
5. A daily account snapshot is recorded (LS:5694-5698).

So a book chosen on decision day $t$ fills on the next session. It is held to its expiry and settles at that day's close.

**Data used at decision day $t$.** All of it is dated $t$ or earlier:

- The forecast rows quoted on $t$. Each is a CDF at 13 points of $z=\ln(S_T/S_t)/\text{reference\_scale}$, one row per
  (name, expiry) (LS:4142-4150).
- The day-$t$ end-of-day option quotes: bid, ask and the displayed sizes (LS:4152-4158).
- The day-$t$ basis factor and spot.
- Daily closes strictly before $t$, used for the betas and the basket volatility (LS:3985-3992).

The one exception is `settleable_only`. It reads the basis series after $t$, but only to remove candidates that could
not be carried to expiry; it never prices one (LS:3802-3830).

**Candidates.** A candidate group $g$ is one (name, expiry) pair that meets all of these:

- it has a forecast and a context row on $t$;
- its DTE is inside the name's selected pair band. In this study every pair is DTE 1 to 60: the `domain.pairs` of the
  bundle, read by LS:3419-3428 through LS:5935;
- the settlement date equals the expiry;
- the expiry is before the tail end (LS:4142-4146);
- it is settleable (LS:5327-5330);
- it carries a finite beta window (LS:5405-5417).

**What carries from week to week.** Only the account carries
(`FundedOptionAccount`, OL:1342-1524):

- *funding* $\Phi$: contributions plus realized P&L (OL:1388-1390). The study starts at 10,000 and adds 1,000 every
  14 days (study params, Appendix A);
- *cash*;
- the *open books*, with their reserves.

These reach the next week's program through four channels:

- the wealth to size on, $w_0 = \max(0,\Phi) - \Omega$, where $\Omega$ is the sum of the open reserves (LS:5419,
  OL:1440-1463);
- the cash cap of each stage;
- the stage-one shock-row budget, which reads funding;
- the admission check at the fill.

Everything else in the week's program is a function of the day's data and the parameters (see also
`D:\mio-analysis-20261115\fast-search\PROFILE.md` section 2).

## 2. Notation

| symbol | meaning | code |
|---|---|---|
| $t$ | decision day | LS:5315 |
| $n \in \mathcal N$ | names (the document's `symbols`) | LS:3172 |
| $g \in \mathcal G_t$ | candidate group: one name $n(g)$, one expiry $T_g$, DTE $d_g$ | LS:4187-4198 |
| $j \in \mathcal I_g$ | item: one (right, strike $K_j$, side $\sigma_j\in\{+1,-1\}$) of a usable quote, plus hedged twins | LS:4167-4177, LS:1649-1673 |
| $k \in \mathcal K_g$ | atom of the group's outcome grid: support price $s_{gk}$, mass $p_{gk}$ | ND:4828-4853 |
| $S_g$ | the group's spot on the decision basis | ND:4814 |
| $\bar q_j$ | item capacity: displayed size on the traded side at $t$ (ask size to buy, bid size to sell) | ND:5015-5019, LS:4175 |
| $\pi_j$ | execution price per share at $t$: mid $+\,\sigma_j\times$ half-spread, i.e. ask to buy and bid to sell | LS:4173, ND:4776-4780 |
| $u_j$ | hedge shares per contract ($0$ for a plain item) | LS:1675-1690, HO:592-642 |
| $F_{0,g}$ | the decision session's parity forward (hedge reference) | LS:1575, LS:1605 |
| $\kappa_j$ | program cost per contract of the hedge (both trades of $u_j$ at $F_0$) | HO:394-396, LS:1690-1691 |
| $c_j$ | credit per contract: $-\sigma_j\pi_j\cdot 100 - 0.65 - \kappa_j$ | LS:4180, LS:1691 |
| $\ell_j(S)$ | loss per contract at underlying $S$: $-100\,\sigma_j\,\text{payoff}_j(S) - u_j(S-F_{0,g})$ | LS:3645-3657 |
| $e_j$ | expected profit per contract under the forecast: $c_j - \sum_k p_{gk}\ell_j(s_{gk})$ | LS:2683-2684, PY:4397-4403 |
| $m_g$ | edge multiplier (accuracy weight $\times$ DTE tilt) | LS:2672-2685 |
| $\Phi,\ \Omega,\ \Omega_n$ | funding, all open reserves, the open reserves of name $n$ | OL:1388, OL:1440-1463 |
| $w_0$ | sizing wealth $\max(0,\Phi)-\Omega$ | LS:5419 |
| $f,\ \gamma$ | Kelly fraction; $\gamma = 1/f$ | LS:5422 |
| $\eta$ | `name_spread` (the utility's `charge_scale`); $1$ when absent | LS:5502-5504 |
| $\beta_n$ | name beta on the equal-weight basket, 60 sessions before $t$ | LS:4006-4019 |
| $\mu \in \mathcal M$ | Kelly market moves (16 values, Appendix A) | Appendix A |
| $\mathcal M^{(1)}$ | stage-one shock moves $\{-.10,-.05,.05,.10\}$ | Appendix A |
| $x_j \in \{0,\dots,\bar q_j\}$ | **decision variable**: contracts of item $j$ (integer) | PY:4792-4793 |
| $R_g(x_g)$ | reserve of group $g$ (below) | PY:6137-6142 |

Per-share prices are divided by the basis factor (CT:229-237). The multiplier is 100 and the fee is 0.65 per contract
(the bridge `solver` block, Appendix A).

## 3. The candidate menu (shared by both stages)

### 3.1 Items, losses, credits

The quotes used are every quote of the day's expiry for which `usable_quote` holds:

- the basis factor matches;
- $0 < \text{bid} < \text{ask}$;
- both sizes are at least 1;
- $(\text{ask}-\text{bid})/\text{mid} \le 0.5$.

This is CT:229-237, ND:4735-4743 and ND:5007-5008. The weekly study admits every quoted strike: `_AllQuotedOptionContext`
overrides the strike filter to `True` (LS:1265-1269), so `max_abs_z` does not restrict strikes here.

Each usable (right, strike) gives two items, a buy and a sell (LS:4168-4177). The buy and the sell of one contract are
an exclusive pair (LS:4191). The bridge's `max_total_contracts` is not read by the weekly study. A grep of LS finds no
use of it; the capacity is the displayed size.

**Outcome grid.** The support is the sorted usable strikes plus the two prices at $z=\pm3$ reference scales
(ND:4830-4831). The forecast CDF is linearly interpolated in $z$ between its 13 points, clipped to 0 and 1 outside
them (ND:4834-4836). It is placed on the support by `MeanPreservingCDFGrid`, which preserves linear payoffs between
support points (ND:4837-4838). The masses $p_{gk}$ are that projection's (ND:4853). A forecast CDF that falls between
points by no more than $10^{-9}$ is raised to its running maximum and recorded (ND:4805-4813).

**Per-item arrays** (LS:4178-4194):

- credit $c_j$;
- loss row $\ell_j(s_{gk})$ at each atom;
- reserve rows: the net loss $\ell_j(S)-c_j$ at $S\in\{0\}\cup\{K\}$, plus the row $-c_j$;
- one linear row (below).

### 3.2 The program-mode hedge, as an optional twin

The method's hedge is `fixed_delta_forward` in `program` mode with `ratio` 1.0 (Appendix A). The local search is the
"hedged search" (LS:3307-3318), so the hedge is optional contract by contract (LS:5224).

For each group with a decision forward:

1. Each item is priced through the overlay. Its units are $u_j = -1.0\times\Delta_{F,j}$, the per-contract Black-76
   forward delta (HO:592-642).
2. The status is `ok` or a refusal code. Examples are `leg_no_iv`, or a short hedge on a name the hard-to-borrow proxy
   flags (HO:618-641).
3. The plain item always stays.
4. Every item with status `ok` and nonzero units gains a hedged twin (LS:1598-1599, LS:1649-1673). The twin is
   exclusive with its original. It is also exclusive with every partner the original was exclusive with, so each
   contract is held at most once, hedged or not (LS:1664-1667).
5. A group without a decision forward keeps its plain items only (LS:1576-1581).

The rewrite (LS:1675-1709) then sets the following. Plain items have $u_j=0$ and are unchanged by it.

- hedged loss rows $\ell_j(S) = -100\sigma_j\,\text{payoff}_j(S) - u_j(S-F_0)$ (LS:1698, LS:3654-3657);
- credit net of the program hedge cost, $c_j \leftarrow c_j - \kappa_j$ (LS:1690-1691);
- reserve rows on the hedged loss at $\{0\}\cup\{K\}$, plus $-c_j$, plus the two netted margin rows
  $\pm 0.15\,F_0\,u_j$ (LS:1692-1699, HO:352-359);
- the linear (slope) row
  $$\sum_j\big(-\sigma_j\,\mathbb 1[\text{call}_j] - u_j/100\big)\,x_j \;\le\; 0,$$
  which says the book's loss cannot rise without bound as $S\to\infty$ (LS:1696, LS:1700). Without a hedge it reads
  "no net short calls" (LS:4193-4194).

### 3.3 Capital-charge floor

After the hedge, each group gets the short-notional row, $0.2\cdot K_j\cdot 100$ for a sold item and 0 otherwise. It is
appended to its reserve rows (LS:5461-5463, LS:3939-3947). The reserve of a group is

$$R_g(x_g) \;=\; \max\Big\{0,\ \max_{r} a_{gr}^\top x_g\Big\},$$

taken over all of its reserve rows $a_{gr}$ (PY:6137-6142, KL:185-186). This is the hedged maximum loss over $0$ and the
strikes (bounded above by the slope row), the debit, the netted hedge margin and the 20% short-notional floor, whichever
is largest. At the book level the same floor is applied by the one `_floor` (LS:3949-3954, LS:4364-4398).

### 3.4 The edge multiplier (ADR-0318)

For each group, $m_g = w_{n(g)}\cdot\text{tilt}(d_g)$, rounded to 9 places (LS:2679-2680), where

$$\text{tilt}(d) = 1 - \lambda\Big(1-\frac{\min(\max(d,0),60)}{60}\Big),\qquad \lambda=\texttt{dte\_tilt}$$

(LS:2665-2670; with $\lambda=0.5$: 1 at 60 days, 0.75 at 30, about 0.51 at 1 day; DL:37414).

$w_n$ is the accuracy weight (LS:2608-2656). It is 1 when `accuracy` is absent, as in sdte. On the accuracy cells
(`shrink_rows` 60) it is the linear-pool weight $\sum ab/\sum aa$ over forecast rows that settled strictly before $t$.
It is shrunk twice: the pool toward 1, and the name toward the pool. Both are bounded to $[0,1]$ (DL:37412).

The multiplier enters as the group's `value_adjust`, $(m_g-1)\,e_j$ per item (LS:2683-2685). So the objective sees
expected profit $m_g e_j$ in place of $e_j$. Losses, scenario terms, the dispersion charge, reserves and caps are not
touched (LS:2511-2515, PY:4272-4291). Because the adjust is written into the groups before either stage is built
(LS:5467-5472), stage one, its LP screen and the climb all read the same weighted edge.

## 4. Stage one: the dollar MILP (the climb's start)

Stage one is the program `dollar` built in `_local_search_week` (LS:4702-4707). It has these parts:

- **Groups.** The configured groups of section 3 with radius $\rho=$ `start.rho` (0.10 for sdte) (LS:4703). The W1
  scale is the spot, since no `radius_scale` is declared (LS:4206-4211).
- **Caps.** `_limits(..., solve=True, stage1=True)`. For a kelly method that call skips the kelly branch and returns the
  dollar caps (LS:4213-4241).
- **Joint rows.** One per stage-one shock move (LS:3664-3675).

### 4.1 Objective

The groups kept by the LP screen form the set $A$ (section 4.3). Stage one maximizes (PY:4819-4842)

$$\max_{x}\ \sum_{g\in A}\Big[\big(c_g + (m_g-1)e_g\big)^\top x_g \;-\; \sup_{Q:\,W_1(Q,P_g)\le\rho} \mathbb E_Q\big[\ell_g^\top x_g\big]\Big].$$

Here $P_g$ is the forecast distribution on the group's grid. $W_1$ is the 1-Wasserstein distance with ground cost
$|s-s'|/S_g$.

The sup is attached in its sparse dual form (PY:4823, PY:3370-3389):

$$\min_{\phi_g,\ \lambda_g\ge0}\ \sum_k p_{gk}\phi_{gk} + \rho\lambda_g
\quad\text{s.t.}\quad \phi_{gk}\ge \ell_{gk}^\top x_g,\quad |\phi_{g,k+1}-\phi_{gk}|\le \lambda_g\,\frac{s_{g,k+1}-s_{gk}}{S_g}.$$

Equivalently, each group contributes $m_g\,e_g^\top x_g$ minus its W1 deduction (worst minus nominal expected loss).
Groups outside $A$ are not in the model: their actions are zero (PY:4789-4794).

### 4.2 Constraints

- **Capacities and integrality:** $x_j\in\{0,\dots,\bar q_j\}$ (PY:4792-4793).
- **Exclusive sides:** a binary per exclusive pair, $x_a\le \bar q_a z$ and $x_b\le\bar q_b(1-z)$ (PY:4807-4811). A
  pair whose round trip is provably costly gets no binary (PY:4669-4684, PY:4795-4799). The extraction cancels such a
  round trip if the solver returns one; this is the incumbent's canonicalization (PY:5423-5429).
- **Group linear rows:** the slope row of 3.2 (PY:4814-4818).
- **Reserve epigraph:** $r_g\ge a_{gr}^\top x_g$ for every reserve row, and $r_g\ge 0$ (PY:4794, PY:4812-4813).
- **Reserve caps** (LS:4233-4236, with `portfolio_fraction` = `name_fraction` = 1 and `component_fraction` null, so no
  component rows; each upper bound floored at 0 by LS:4243-4251):
  $$\sum_{g} r_g \le \max(0,\Phi)-\Omega,\qquad \sum_g r_g \le 0.25\max(0,\Phi),\qquad \sum_{g:\,n(g)=n} r_g \le \max(0,\Phi)-\Omega_n\ \ \forall n.$$
  The 0.25 is the study's `weekly_fraction`. The cash buffer does not apply in stage one.
- **Shock rows** (`start.shock`, LS:4704-4707, LS:3664-3697). For $\mu\in\{-.10,-.05,.05,.10\}$:
  $$\sum_g\sum_j \big(\ell_j\big(\max(0,S_g(1+\beta_{n(g)}\mu))\big) - c_j\big)\,x_j \;\le\; 0.10\cdot\max(0,\Phi).$$
  Here $\beta$ comes from `_betas(t, 60)` over all names (LS:3994-4004), and $\ell$ includes the hedge for twins.
- **Not present:** no wealth floor, no utility, no dispersion charge.

### 4.3 The group LP screen

The document declares `group_screen: "lp"` (Appendix A; required for a kelly document, LS:3597-3599, LS:3625). Before
the model is built, each stage-one group's own LP relaxation is solved alone: the same objective and its own rows,
integrality dropped, caps and joint rows ignored (PY:4494-4554, `scipy.optimize.linprog`, method `highs`).

- A group whose relaxed value is at most `parity_tolerance` (0.001) is left out of the model (PY:4597-4616).
- If no group survives, the best one is kept.
- No `group_screen_keep` is declared.

The screen is a candidate restriction, not an exact reduction. It drops groups with no standalone dollar edge even
where they could hedge a joint row (PY:3985-3992).

**The screen applies to stage one only.** The climb's scorer is built on all configured groups
(`TransportActionScore(program, ...)`, LS:4745; KL:107-122 lays out every group). The `kelly.screen` field of a week's
record is therefore stage one's screen (LS:4740, LS:5646-5647).

### 4.4 Its result is only a start

The stage-one selection is never traded. It seeds the climb (section 6.2). Section 6.1 covers time limits,
incumbents and audits.

## 5. Stage two: the exact Kelly objective (what is traded)

The week's one-stage program is assembled at LS:5477-5504. It is never built as a model; it is only scored
(LS:5515-5518). Its parts are:

- `groups`: the configured groups with radius 0, which a kelly method must declare (LS:3541; PY:4375);
- `caps`: one cap (LS:4222-4227);
- `utility`: LS:5494-5504.

### 5.1 Scenarios (at most 17)

**Move weights** (LS:3743-3795):

- $\sigma_d$ is the population std of the daily log returns of the equal-weight basket of names with a finite window,
  over the 60 sessions strictly before $t$.
- $h$ is the mean DTE of the week's candidates, one count per candidate group (LS:3735-3741).
- $n_h$ is the number of sessions in the $\text{round}(h)$ calendar days ending at $t$, at least 1 (LS:3727-3732).
- $\sigma_h=\sigma_d\sqrt{n_h}$.
- A Student $t$ with 4 degrees of freedom and scale $\sigma_h/\sqrt{2}$ gives each point of
  $\{0\}\cup\{\ln(1+\mu):\mu\in\mathcal M\}$ the mass of its Voronoi cell on the real line.
- The quiet cell gets $\omega_0=1-\sum_\mu\omega_\mu$.
- A move with zero weight is not modelled (LS:3715). With 16 moves that leaves at most 17 scenarios.

**Profit terms per scenario** (LS:5492, LS:3684-3725). For move $\mu$ and item $j$ of group $g$, the profit deviation
against no move is

$$\delta_{\mu j} = \ell_j(S_g) - \ell_j\big(\max(0,S_g(1+\beta_{n(g)}\mu))\big),$$

with the betas from `_kelly_estimate` (finite names only, LS:4021-4034). The weighted mean is removed so that each
item's expected profit stays its forecast's:

$$\bar\delta_j=\frac{\sum_\mu\omega_\mu\delta_{\mu j}}{\omega_0+\sum_\mu\omega_\mu},\qquad
\tau_{\mu j}=\delta_{\mu j}-\bar\delta_j,\qquad \tau_{0j}=-\bar\delta_j .$$

**Scenario wealth** (PY:6204-6213, KL:124-132):

$$W_s(x) = w_0 + \sum_g\sum_j\big(m_g e_j + \tau_{sj}\big)\,x_j,\qquad s\in\{0\}\cup\mathcal M .$$

Here $m_g e_j$ is the tilt-adjusted expected profit (`_effective_credit`, PY:4286-4291).

### 5.2 Utility

CRRA with $\gamma=1/f$; for sdte $f=0.75$, so $\gamma=4/3$ (PY:174-219):

$$u_\gamma(W)=\frac{(W/w_0)^{1-\gamma}-1}{1-\gamma}\quad(\gamma>1),\qquad u_1(W)=\ln(W/w_0).$$

Both are zero at $w_0$, so cash scores exactly 0.

### 5.3 Dispersion charge

**Per-group measure.** For each group with at least two positive-mass atoms, sorted by price, the measure is built from
the book's terminal payoff at each atom (PY:4438-4474, PY:3670-3706):

- the payoff is $v_k = -\ell_{gk}^\top x_g$; the credit cancels in a deviation;
- the masses are renormalized to $\hat p_k$;
- $\bar v=\sum_k\hat p_kv_k$ and $t_k=|v_k-\bar v|$.

$$M_g(x_g)=\max\Big\{1.25\sum_k\hat p_kt_k,\ \max_{j<K}\frac{\sum_{k\le j}\hat p_kt_k}{\sqrt{\sum_{k\le j}\hat p_k}},\ \max_{j<K}\frac{\sum_{k> K-1-j}\hat p_kt_k}{\sqrt{\sum_{k> K-1-j}\hat p_k}},\ \max_k\sqrt{\hat p_k}\,t_k\Big\}.$$

That is 1.25 times the mean absolute deviation (`SD_PER_MAD`, PY:4436), or the largest Cauchy-Schwarz lower bound on
the sd over a lowest set, a highest set or a single atom. A group whose items never differ across atoms carries no
charge (PY:4470-4471).

**Per-name charge.** The groups of one name are summed, as if perfectly correlated:
$S_n=\sum_{g:\,n(g)=n}M_g$ (PY:4450-4453, PY:4473). The charge is (PY:4475-4478)

$$C(x)=\frac{\eta\,\gamma}{2w_0}\sum_n S_n(x)^2 .$$

`name_spread` sets $\eta$. sdte does not declare it, so $\eta=1$.

### 5.4 The exact objective and its feasible set

$$\boxed{\;\max_{x}\ F(x)= w_0\sum_{s}\omega_s\,u_\gamma\big(W_s(x)\big)\;-\;\frac{\eta\gamma}{2w_0}\sum_n S_n(x)^2\;}$$

This is `KellyLocalSearch.value` (KL:227-230) and the scorer's `utility.exact` (PY:6259). The parity check that ties
the two is at KL:443-446.

Constraints (PY:6176-6202; KL:234-284 checks the same rules incrementally). Every inequality has slack
`parity_tolerance` $=10^{-3}$:

- $x_j\in\{0,\dots,\bar q_j\}$, integer;
- exclusive pairs: no two both positive;
- group linear (slope) rows $\le 0$;
- **one cash cap** over all groups (LS:4222-4227, `cash_buffer` 0.05):
  $$\sum_g R_g(x_g)\ \le\ \max\big(0,\ 0.95\max(0,\Phi)-\Omega\big);$$
- **wealth floor** in every scenario: $W_s(x)\ge 0.01\,w_0$ (`wealth_floor` 0.01, LS:5425, PY:6198-6201);
- no joint rows: the kelly program has none; the shock rows are recorded but not imposed (LS:5493, LS:5637-5638).

Compared with stage one, stage two has:

- no 25% weekly budget;
- no per-name caps;
- no shock rows;
- no W1 radius.

ADR-0299 removed these for kelly methods on purpose (DL:35992). The validation enforces it: `rho` 0, fractions 1,
`component_fraction` null, no `shock` key (LS:3515-3543).

### 5.5 Envelope versus exact utility

`TransportPortfolioSolve` would maximize a piecewise-linear surrogate. It uses $w_0\sum_s\omega_s\hat u(W_s)$, where
$\hat u$ is the least of the tangent lines of $u_\gamma$ at these knots (PY:3817-3861, PY:4426-4433):

- `n_tangents` = 32 geometric knots on $[0.01w_0,\ w_0+\text{highest reachable gain}]$;
- 32 more on the core band $[w_0/2,\ 2w_0]$ (`core` 2.0);
- $w_0$ itself.

It also replaces $S_n^2$ by the maximum of tangent lines under it (`_ChargeLines`, PY:3912-3940). The envelope
over-states $u$ between knots and the lines under-state the charge.

The climb does not use either. It scores the exact $u_\gamma$ (`tangent_utility`) and the exact square (KL:227-230,
KL:283-284). The envelope value is computed only for the record (`selection.value`, `utility.envelope`,
`envelope_error`; PY:6238-6265 and PY:6321).

So `n_tangents` and `core` do not change a local-search decision. They must still form a valid envelope
($0<\text{wealth\_lo}<w_0$): PY:3846-3847 and PY:4369.

### 5.6 What the objective means near cash (an interpretation, not code)

For small books, $w_0u_\gamma(W)\approx (W-w_0) - \frac{\gamma}{2w_0}(W-w_0)^2$. So $F$ is roughly the expected profit
less $\gamma/(2w_0)$ times two terms:

- the scenario second moment, which carries the joint market risk;
- $\sum_n S_n^2$, each name's own dispersion, treated as independent across names.

The scorer records how the charge's $S$ compares with the book's exact sd and the "effective fraction"
$f\cdot(\text{sd}/S)^2$ (PY:6220-6236). The label $f$ is nominal when that ratio is not 1.

## 6. How a week is solved

### 6.1 Stage one under HiGHS

**Solver settings.** The document's solver block is `appsi_highs` with these options (Appendix A):

- `threads` 1;
- `mip_rel_gap` 0.1 and `mip_abs_gap` 1;
- `mip_feasibility_tolerance` 1e-9;
- `time_limit` 2400;
- `timeout_policy: "return"`;
- `absolute_gap` 1, `relative_gap` 0.1 and `parity_tolerance` 0.001 for the certificate check.

The options are handed to HiGHS verbatim (PY:697-707, PY:737-738). `TransportPortfolioSolve` does not override
`_solver_options`, unlike the two classes that pin a seed (PY:782, PY:1817). `random_seed` is not declared, so HiGHS
runs at its own default (PY:4087 records that default as 0). The method's `local_search.seed` (20261109) is not a
HiGHS option. It seeds the climb's restart generator (KL:416), which is unused with `restarts` 0.

**Stage-one time limit.** `stage1_time_limit` 300 replaces the HiGHS `time_limit` for stage one only (LS:4715-4718).

**Incumbent audit.** Because the search is hedged and the policy is `return`, stage one also runs with
`incumbent_audit: "uncertified"` (LS:4709-4714). A dollar incumbent is priced at its integers before it is recorded
(PY:4859-4888). An incumbent whose value checks miss comes back as an `incumbent_audit` envelope with its book as the
`candidate`, not as a refusal (PY:5953-6002).

**Sequence of a stage-one solve:**

1. The LP screen (4.3).
2. Model build (PY:4762-4849); cash is the initial point.
3. HiGHS, offered the feasible cash start when the interface supports warm starts (PY:4890-4909).
4. Extraction (PY:5917-6003).

**Outcomes:**

- *Finished.* The selection is certified within the gap: allowance $\max(1,\ 0.1\,|\text{value}|)$ (PY:5987-5993). A
  value $\le 0$ is replaced by cash (PY:5985-5986).
- *Stopped at 300 s with an incumbent above the gap.* A `timeout: "gap"` envelope with a `candidate` (PY:5378-5386,
  PY:5998-6002).
- *Stopped with no incumbent.* `no_incumbent`. An error termination with no solution is `solver_error`
  (PY:5388-5421).
- *Audit miss.* `incumbent_audit`, as above.

**Start rule.** With `incumbent_start` true, any envelope that carries a `candidate` starts the climb from that
candidate, recorded as `stage1_incumbent` (LS:4744-4749). This covers the gap and audit cases. An envelope with no
candidate starts from cash, recorded as `stage1_timeout_cash` (LS:4751-4752). A finished stage one starts from its
selection (`stage1`).

**What 2400 s means here.** The document's `time_limit` 2400 and the kelly `time_limit` 2400 are validation
requirements: the document must allow at least the method's limit (LS:3601-3625). Neither bounds the week at run time:

- stage one runs under 300 s;
- the climb reads no clock (KL:34-36);
- a local-search week never returns a `timeout` key, so it is never `timeout_skipped` (LS:5527, LS:5572).

The 300 s is HiGHS's own clock. The screen and the model build come on top. The run notes record capped stage-one calls
at 316-369 s wall (`PROFILE.md` section 1).

### 6.2 The climb (`KellyLocalSearch`, ADR-0307 and its Amendment 1)

The parameters are `moves` 200, `adds_evaluated` 64, `swaps_evaluated` 64, `restarts` 0, `seed` 20261109,
`cash_floor` true and `repair` true. The stage-one keys are stripped before the search sees the block (LS:4746-4747,
KL:73-75).

1. **Layout, once** (KL:107-176). Items are laid out flat. The payoff matrix $D_{js}=m_ge_j+\tau_{sj}$ is built, so
   $W = w_0 + xD$, along with the reserve rows, linear rows, exclusive partners, cap membership, and each item's own
   per-contract dispersion (an upper bound on how far one contract can move its group's $M$).
2. **Start feasibility** (KL:400-411). The start is checked against the *kelly* program. It can fail because:
   - the cash cap is 0.95 of funding, where stage one allowed the full funding;
   - stage one has no wealth floor;
   - rounding.

   With `repair`, an infeasible start loses one contract at a time (KL:506-525). Each step removes the contract that
   leaves the smallest summed breach, then the highest objective, then the lowest item rank, until the scorer accepts
   the book; at worst that is cash. The record kind is `repaired_stage1` or `repaired_stage1_incumbent`, or
   `infeasible_start_cash` if the repair reaches cash.
3. **Each move** (KL:323-349), steepest ascent:
   - *First-order ranking* (KL:293-302): the add gain is $w_0\sum_s\omega_su'_\gamma(W_s)D_{js} - 2\,\frac{\eta\gamma}{2w_0}S_{n(j)}\,\text{own}_j$,
     and the removal gain is its negative.
   - *Candidates* (KL:304-321):
     - the 64 best feasible adds, pre-filtered on capacity, exclusivity, linear rows, reserve-cap slack, joint rows and
       the wealth floor;
     - every removal of a held item;
     - the 64 best swaps (remove one held $a$, add one $b$ from the top-64 add pool, any group).
   - *Exact evaluation*: each candidate is scored by `_trial`, which recomputes $W$, the touched groups' reserves and
     spreads and the name totals, and returns None if any rule fails (KL:234-284).
   - *Choice*: the best strictly improving move is taken if it gains more than $10^{-9}w_0$. Ties go to the larger
     gain, then add before remove before swap, then item rank (group id, then item index) (KL:338-349, KL:118-122).
4. **Stop** at a local optimum (no evaluated move improves) or after 200 accepted moves (KL:359-370). The record
   carries `local_optimum` and `budget_exhausted` (KL:447-451).
5. **Restarts** 0: none (KL:417-431).
6. **Cash floor** (KL:432-439). If the end value is below cash's exact value (0), the week trades cash.
7. **Parity** (KL:443-446). The end selection is re-scored by `scorer.evaluate`. A relative miss above $10^{-9}$ is a
   defect and raises.

The week's certificate is `{"strategy_outcome": "local_search", "certified": false, "objective": end_value, "bound":
None, "gap": None}` (LS:4682, LS:4758-4759).

### 6.3 Determinism and replay

The climb, the repair and the floor read no clock and break every tie by item rank. They are deterministic functions of
the program and the start (KL:34-36; DL:36099). Workers pin every BLAS/OpenMP pool to one thread (MW:76-77), and HiGHS
runs on one thread.

The one source of host and load dependence is a stage one stopped by its 300 s clock. Which incumbent it holds at that
moment depends on the machine (DL:36099; `PROFILE.md` section 2 caveat). In the batch measured there, 72 of 365
unit-weeks hit the cap.

A recorded or derived replay does not re-solve stage one live:

- *recorded*: `_recorded` re-checks the recorded stage one (LS:4735-4739);
- *derived*: `solver.derived` re-derives it from the worker's actions and certificate (LS:4720-4732).

In both cases the climb is re-run and the week is refused if the final quantities differ (LS:4760-4780).

### 6.4 What the result is, plainly

The traded book is a **local optimum of the exact objective $F$**, with respect to the evaluated neighbourhood only:

- the top 64 adds, all removals and the top 64 swaps;
- or simply the point reached after 200 moves.

It is **not** a global optimum, and no bound or gap is computed. It depends on the stage-one start, which is itself a
time-capped dollar MILP of a different objective.

This is by design. The one-stage MIP that would maximize the same objective directly (ADR-0299) did not finish on real
weeks. It had 300,000 to 430,000 rows, and 12 of 13 weeks of 2021Q1 were skipped. The top-100 restriction (ADR-0301)
still declined 36 and failed 19 of 55 weeks (DL:36063). ADR-0307 replaced it with the climb and lists these limits
itself (DL:36085).

## 7. Hedge, fills, settlement, and the two pricing routes

**Books.** Each group with a positive action becomes a book (LS:4364-4410). Its credit is
$\sum_j-\sigma_j\pi_j\cdot100\,x_j-0.65\sum_jx_j$, without the program hedge cost; the cost goes into the
`program_hedge` record.

Its reserve is recomputed physically and must match the program's reserve within 0.001. For a hedged book this uses the
untruncated net units $\sum_ju_jx_j$ and the margin floor $0.15\,F_0\,|\cdot|$. A miss raises `selected physical-domain
reserve parity failed` (LS:4386-4398). Legs taken through a twin carry `hedged: True` (LS:1672).

**Fill at the next session** (LS:4859-4907):

1. Each leg needs a usable quote that session (else `missing_fill_quote`).
2. Sizing is `available_size` (Appendix A). With $g$ the gcd of the leg quantities, every leg executes $k\,q_j/g$
   contracts. $k$ is the largest whole multiple the traded-side size shows, at most $g$ (LS:4837-4857). Too little
   depth for one unit is `insufficient_fill_size`.
3. Prices are the next session's ask to buy and bid to sell.
4. Credit, max loss and reserve are recomputed at the fill prices.
5. `stress_extra` is 0.25 of the spread (the bridge `stress_spread_fraction`), recorded for the stress P&L only.

**Hedge at the fill** (LS:1764-1818):

- If the search took no leg hedged, the book fills unhedged (`unhedged_chosen`).
- Otherwise the forward units $U=\operatorname{trunc}\big(\sum_j u_jq_j\big)$ come from the decision session's deltas,
  truncated once (HO:329-350). They are struck at the fill session's parity forward, excluding the book's strikes.
- The reserve becomes $\max(\text{hedged reserve}+\text{closing-cost allowance},\ 0.15\,|U|\,F_{\text{fill}})$.
- A hedged book whose forward leg cannot be attached does not fill at all: `hedge_unattached:<status>` (LS:5285-5293).

**Admission** (LS:5304-5313, OL:1465-1524). The batch is admitted atomically. The cap at the fill is the cash rule
without the buffer: new reserves within $\max(0,\Phi)-\Omega$ (LS:4223-4227). If the whole batch does not fit, every
book is refused with `repriced_shared_cap`.

**Settlement.** On its expiry day at that day's close, a book pays its fill credit plus $100\times$ the option payoff
at $S_T$. A hedged book adds $U(S_T-F_{\text{fill}})$ less the leg's trade costs (LS:4909-4929, `forward_leg_net_settle`).
Books are held to expiry: there is no early exit, and American early assignment is not modelled (LS:6101).

**Marks.** Daily marks for the equity curve, drawdown and TWR come from the shared ledger (LS:4931-5110). The
`_finish` step checks the ledger P&L and reserves against the account's (LS:5050-5064).

**Two pricing routes for a recorded run:**

- **Official and full** (`MIOPacketStudy.run`, LS:6057-6081; worker pricing MW:741-788). The worker's action file
  carries, per week, stage one's actions and certificate and the final quantities (MW:695-738). Pricing then runs
  twice, each time on a fresh study:
  1. `_method(derived=...)`, which re-derives stage one, re-runs the climb and refuses a different book;
  2. the exact `_method(recorded=result)` replay. Its result must be identical (LS:6032-6055).
- **Fast route** (`D:\mio-analysis-20261115\acc-build\run\replay_keep_acc.py`, outside the repo). It patches
  `_local_search_week` to one call: `TransportActionScore(program).evaluate(recorded picks)`, which is exactly what
  the climb ends on (KL:443). It requires that value to equal the recorded objective within the parity tolerance
  (lines 89-123 of that script). There is no stage one, no climb and no second replay. The menu, books, fills, hedge
  and settlement are the same code. The lockstep validation (V1) matched the full runs to the cent on all 14 weeks of
  2019Q4 (`D:\mio-analysis-20261115\fast-search\lockstep\LOCKSTEP.md` section 2). The proof given up is "this host's
  search reproduces the picks".

## 8. Where each knob enters

| knob | value in sdte | enters | stage one (incl. LP screen) | climb objective | code |
|---|---|---|---|---|---|
| `dte_tilt` $\lambda$ | 0.5 | $m_g$ via `value_adjust` on every item's expected profit | yes | yes (mean wealth only) | LS:2665-2685, LS:5467-5472 |
| `accuracy.shrink_rows` | absent (grid: 60) | $w_n$ inside $m_g$, same route | yes | yes | LS:2608-2656 |
| `kelly.fraction` $f$ | 0.75 | $\gamma=1/f$ in $u_\gamma$ and in the charge slope $\eta\gamma/(2w_0)$ | no | yes | LS:5422, LS:5494-5497, PY:4475 |
| `local_search.start.rho` | 0.10 | W1 radius of every stage-one group; also its LP screen | yes | no (radius 0) | LS:4703, PY:4820-4823, PY:4532-4533 |
| `name_spread` $\eta$ | absent (= 1) | `utility.charge_scale`, multiplies every name's charge | no | yes | LS:5502-5504, PY:4475-4478 |

No knob touches the scenario terms, losses, reserves, caps, hedge or fills directly (LS:2511-2515). Each still moves
later weeks through the account path (section 1). A knob that only enters stage one, like `start.rho`, can change the
traded book only through the start it gives the climb.

## 9. Known limitations, with evidence

1. **Only 17 scenarios for joint risk.**
   - The joint market risk is 16 basket moves plus a quiet cell (LS:3743-3795, Appendix A). The largest move is
     $\pm15\%$ of the basket, times beta.
   - A larger crash is priced at the outer cell's move. The weight of the whole tail beyond the last cell boundary sits
     on that one point (LS:3784-3794).
   - The tail law is fixed: Student $t$ with 4 degrees of freedom (LS:2962-2964), with volatility from 60 trailing
     sessions.
   - Name-specific risk is not in the scenarios. It enters only as the second-order charge $\sum_n S_n^2$, which treats
     names as independent and a name's expiries as perfectly correlated (PY:4450-4453).
   - Each name's own outcome grid is truncated at $z=\pm3$ reference scales (ND:4830-4836), so the forecast puts no mass
     beyond that.
2. **Profit is concentrated in a few weeks.** sdte's 2020Q1 total is +31,368. Two books made 98% of it:
   - the APA book opened 27 Jan 2020 (+20,610);
   - the APA book opened 3 Feb 2020 (+10,187).

   Both were opened before the crash and paid in it; through the crash weeks the method opened almost nothing
   (`D:\mio-analysis-20261115\GRID-MORNING-20261117.md` lines 56-65). Removing the best single book takes sdte's
   four-quarter total from about +84,114 to +63,505 (`D:\mio-analysis-20261115\stress\out\sdte.md` lines 45-48). The
   lockstep screen found the same pattern. In 2020Q1, the no-accuracy cells' lead over the centre came almost entirely
   from weeks 5-6 (27 Jan and 3 Feb), and it was negative once the best two weeks were removed (`LOCKSTEP.md`
   section 2, V3).
3. **Unseen quarters lost money.** These runs used the same formulation at `b40a3785`.

   | run | 2020Q2 | 2020Q3 |
   |---|---|---|
   | fsdte (sdte) | +5,449 | -5,694 |
   | fsdte25 | +3,815 | -7,897 |
   | fg075r010 | +3,520 | -4,482 |
   | fsbase | -2,235 | not run (stopped) |

   fsdte's 2020Q3 loss came from many small losing books, the worst the 31 Aug week (15 books, -2,460), with a maximum
   drawdown of -42%. Its 2020Q2 gain was mostly one book (26 May, +3,911). Source:
   `D:\mio-analysis-20261115\GRID-MORNING-20261117.md` lines 45-63 and `GRID-OVERNIGHT.md` lines 278-296. Every
   candidate was stopped under the owner's negative-quarter rule.

   Separately, ADR-0318 states that any result on the four development quarters is in-sample for the tilt and
   name-spread knobs (DL:37424).
4. **Both 2021Q1 solves failed on the round-2 memory code.** That code is `79860494`, which is **not** `b40a3785` and
   not an ancestor of it.
   - *sdte25*, week 5: the pricing LP found the rounded stage-one integers infeasible. The log line reads "recorded
     actions could not be priced: infeasible apart from their rows". At `b40a3785` the corresponding refusal reads
     "... infeasible within their rows" (PY:5970).
   - *s100dte*, week 9: "selected physical-domain reserve parity failed". This is the book check at LS:4397-4398.
   - Source: `D:\mio-analysis-20261115\GRID-OVERNIGHT.md` lines 157-160 and 290-294, and `GRID-MORNING-20261117.md`
     lines 69-70 and 79.
   - The suspected cause is round 2's single scorer copy (a shared-object mutation). That is unconfirmed: nothing has
     been re-solved.
   - sdte's own 2021Q1 on the older code priced (+26,782).
5. **Other standing limits** (from code):
   - No certificate (DL:36085).
   - Capped stage-one weeks are not reproducible across hosts (6.3).
   - Fills are a next-session end-of-day NBBO proxy sized by displayed depth.
   - The hedge cost source is declared `placeholder` (Appendix A).
   - American assignment is not modelled (LS:6101).

## 10. Points left unclear

- **HiGHS seed.** No `random_seed` is declared. The solve relies on HiGHS's default, which the code comment at PY:4087
  gives as 0. This was not checked against the installed `highspy`.
- **ADR-0302.** It has no entry of its own in `decision-log.md` at this revision. It is referenced only through ADR-0307
  (DL:36063) and the ADR-0313 port note (DL:36744). Its keys (`search`, `warm_start`) are refused on a local-search
  method (LS:2924, LS:3503), so it plays no part in this formulation.
- **The 2021Q1 failures.** Their cause is open (9.4). This memo describes `b40a3785`, not the round-2 code they ran on.

## Appendix A. The method block and study parameters (bundle `2020q2-fsdte-t40/study.json`)

Method `kelly075_ls_fast_hedged_static_dte_s_lp`:

```json
{"id": "kelly075_ls_fast_hedged_static_dte_s_lp", "rho": 0.0, "portfolio_fraction": 1.0, "name_fraction": 1.0,
 "component_fraction": null, "short_notional_fraction": 0.2,
 "kelly": {"fraction": 0.75, "n_tangents": 32, "core": 2.0, "wealth_floor": 0.01, "cash_buffer": 0.05,
           "beta_lookback": 60, "gap": {"absolute": 1.0, "relative": 0.1}, "time_limit": 2400,
           "settleable_only": true, "dte_tilt": 0.5,
           "moves": [-0.15, -0.125, -0.1, -0.075, -0.05, -0.035, -0.02, -0.01,
                     0.01, 0.02, 0.035, 0.05, 0.075, 0.1, 0.125, 0.15],
           "local_search": {"moves": 200, "adds_evaluated": 64, "swaps_evaluated": 64, "restarts": 0,
                            "seed": 20261109, "stage1_time_limit": 300.0, "incumbent_start": true,
                            "cash_floor": true, "repair": true,
                            "start": {"rho": 0.1, "shock": {"beta_lookback": 60, "loss_fraction": 0.1,
                                                            "moves": [-0.1, -0.05, 0.05, 0.1]}}}},
 "hedge": {"kind": "fixed_delta_forward", "mode": "program", "ratio": 1.0, "condition": null,
           "delta_source": "black76_chain_forward", "iv_source": "vendor_else_mid", "rate": 0.0,
           "margin_fraction": 0.15, "small_delta": 0.0, "round_units": true,
           "hedge_cost_source": "placeholder", "hedge_fee_per_share": 0.005, "hedge_half_spread_bps": 5.0,
           "hedge_borrow_rate": 0.005, "htb_lookback": 6, "htb_vol_threshold": 2.5, "htb_jump_threshold": 0.75}}
```

The grid cells change only `dte_tilt` (0, .25, .5, .75; tilt 0 means the key is absent), `accuracy`
(absent or `{"shrink_rows": 60}`), `kelly.fraction` (.5, .75, 1.0) and `local_search.start.rho` (.05, .10, .20). The
scheme is pre-registered in `LOCKSTEP.md` section 5. `name_spread` is off.

Study parameters that the formulation reads:

- `initial` 10,000;
- `deposit` 1,000 every 14 days;
- `weekly_fraction` 0.25 (stage one only);
- `correlation_min_pairs` 20, the minimum history for betas and move weights;
- `fill_sizing` `"available_size"`;
- `hedge_parity` `{"parity_strikes": 5, "min_pairs": 2, "max_pair_spread_frac": 0.5}`;
- bridge `min_quote_size` 1, `max_relative_spread` 0.5, `stress_spread_fraction` 0.25, `solver.multiplier` 100,
  `solver.fee_per_contract_usd` 0.65;
- the pairs: every name, DTE 1 to 60.

The study solver block:

```json
{"solver": "appsi_highs", "absolute_gap": 1, "relative_gap": 0.1, "parity_tolerance": 0.001,
 "group_screen": "lp", "timeout_policy": "return",
 "solver_options": {"threads": 1, "mip_rel_gap": 0.1, "mip_abs_gap": 1,
                    "mip_feasibility_tolerance": 1e-09, "time_limit": 2400}}
```
