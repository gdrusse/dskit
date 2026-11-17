# Weekly index-option MIO: formulation and solve

This memo covers the lead method family `kelly075_ls_fast_hedged_static_*_s_lp`, written out for the tilt-only lead
`kelly075_ls_fast_hedged_static_dte_s_lp` (sdte). The grid varies `dte_tilt`, `accuracy`, `kelly.fraction` and
`local_search.start.rho`.

The code is at commit `b40a3785`, which matches the unseen-quarter bundles byte for byte. The development-quarter runs
used `b6e083d0`. The only differences are memory release, worker logging, the seal date and one test file; the
formulation is unchanged.

`b40a3785` is not on `origin/main`. Citation abbreviations:

| tag | file |
|---|---|
| LS | `children/index_options/index_options/ledger_studies.py` |
| KL | `children/index_options/index_options/kelly_local.py` |
| PY | `dskit/pipeline/libs/pyomo.py` |
| OL | `dskit/pipeline/libs/option_ledger.py` |
| HO | `dskit/pipeline/hedge_overlay.py` |

Parameter values come from the bundle `2020q2-fsdte-t40/study.json`.

## 1. Sets and indices

| symbol | set |
|---|---|
| $t$ | decision day: the first session of each ISO week (LS:3101) |
| $n\in\mathcal N$ | names: the study's `symbols` |
| $g\in\mathcal G_t$ | candidate groups: one (name $n(g)$, expiry $T_g$) per group, with DTE $d_g\in[1,60]$, a forecast quoted on $t$, settlement on expiry, and settleable |
| $j\in\mathcal I_g$ | items: each usable quote (right, strike $K_j$) gives a buy and a sell, with side $\sigma_j=\pm1$; each item that can be hedged also gets a hedged twin |
| $\mathcal E_g\subset\mathcal I_g^2$ | exclusive pairs: buy and sell of one contract, and each contract with its hedged twin |
| $k\in\mathcal K_g$ | atoms of the group's outcome grid: the strikes plus $z=\pm3$ |
| $r\in\mathcal R_g$ | reserve rows of group $g$ |
| $\mu\in\mathcal M$ | market moves: $\pm\{.01,.02,.035,.05,.075,.10,.125,.15\}$ (16 moves) |
| $s\in\mathcal S=\{0\}\cup\mathcal M$ | wealth scenarios: quiet plus the 16 moves, at most 17 |
| $\mu\in\mathcal M^{(1)}$ | stage-one shock moves: $\{-.10,-.05,.05,.10\}$ |

## 2. Parameters (inputs)

| symbol | meaning | where it comes from |
|---|---|---|
| $F_g(z)$, $p_{gk}$, $s_{gk}$ | forecast CDF of $z=\ln(S_T/S_t)/\text{scale}$ at 13 points, projected onto the grid as masses $p_{gk}$ at prices $s_{gk}$ | `forecasts.parquet` (`method_cdf_*`) from source `quarterly-forecasts-b10d67deebb5`. The model is recipe `b10d67de` (`fixed_loc` net, 5 seeds, linear pool). Labels are cut at the quarter start, per `<Q>/receipt.json`. The projection is `MeanPreservingCDFGrid` (nodes.py:4786) |
| $S_g$, $\text{scale}_g$ | spot and reference scale | `contexts.parquet`, same source and receipt |
| bid, ask, sizes, vendor IV | end-of-day option quotes on $t$; also on the fill day and for marks | source `theta-exact-expiry-archive`, `<sym>/options_<year>.parquet` |
| $\pi_j$ | price per share at $t$: ask to buy, bid to sell | the quotes (LS:4173) |
| $\bar q_j$ | capacity: displayed size on the traded side | quote `ask_size` or `bid_size` (nodes.py:5018) |
| $c_j$ | credit per contract: $-100\,\sigma_j\pi_j-0.65-\kappa_j$ | quotes, plus the bridge `fee_per_contract_usd` (LS:4180) |
| $\ell_{gkj}$ | loss per contract at atom $k$: $-100\,\sigma_j\,\text{payoff}_j(s_{gk})-u_j(s_{gk}-F_{0g})$ | computed (LS:3645) |
| $e_j$ | expected profit per contract: $c_j-\sum_kp_{gk}\ell_{gkj}$ | computed |
| $u_j$, $\kappa_j$, $F_{0g}$ | hedge shares: $-\Delta_F$ for a twin, 0 otherwise; the program cost of the hedge; the decision parity forward | Black-76 on the vendor IV, or mid-implied IV if absent (`hedge.iv_source`). Forward from the day's chain parity (`hedge_parity`). `hedge.ratio` 1.0, fee .005 per share, 5 bps (HO:592) |
| $a_{gr}$ | reserve rows: the net loss $\ell_j(S)-c_j$ at $S\in\{0\}\cup\{K\}$; $-c_j$; $\pm0.15F_0u_j$; and $0.2\cdot100K_j$ on each sold item | computed. `hedge.margin_fraction`, `short_notional_fraction` (LS:4184, LS:1692, LS:3939) |
| $b_g$ | slope row: $-\sigma_j\mathbb1[\text{call}]-u_j/100$ (the loss is bounded above) | computed (LS:1696) |
| $m_g$ | edge multiplier: $w_{n}\,(1-\lambda(1-\min(d_g,60)/60))$ | $\lambda$ = `kelly.dte_tilt` (0.5). $w_n$ = accuracy weight from settled forecast rows when `kelly.accuracy.shrink_rows` is set, else 1 (LS:2665, LS:2608) |
| $f$, $\gamma=1/f$ | Kelly fraction and CRRA aversion | `kelly.fraction` (0.75) |
| $\eta$ | name-spread charge multiplier | `kelly.name_spread`; 1 when absent |
| $\beta_n$ | beta of the name on the equal-weight basket | daily closes `bars/<sym>.parquet`, 60 sessions before $t$ (source `mio-wsl-market-inputs-20261105`, `kelly.beta_lookback`) |
| $\omega_s$ | scenario weights: Student $t_4$ cell masses with the basket vol scaled to the mean DTE | the same bars. `kelly.moves`; the quiet weight $\omega_0=1-\sum\omega_\mu$ (LS:3743) |
| $\tau_{sj}$ | centred profit deviation of item $j$ in scenario $s$ | computed from the shocked spot $S_g(1+\beta\mu)$ (LS:3684, LS:3700) |
| $\Phi$, $\Omega$, $\Omega_n$ | funding (contributions plus realized P&L); all open reserves; the open reserves of name $n$ | **account state carried from earlier weeks** (OL:1388, OL:1462). Starts at 10,000 (`initial`), plus 1,000 every 14 days (`deposit`) |
| $w_0$ | sizing wealth: $\max(0,\Phi)-\Omega$ | the account (LS:5419) |
| $\underline W$ | wealth floor: $0.01\,w_0$ | `kelly.wealth_floor` |
| $B$ | Kelly cash cap: $\max(0,\ 0.95\max(0,\Phi)-\Omega)$ | `kelly.cash_buffer` (0.05), the account (LS:4222, LS:4250) |
| $\rho$ | stage-one W1 radius; the ground cost is $\lvert s-s'\rvert/S_g$ | `kelly.local_search.start.rho` (0.10) |
| $\phi_w,\ L$ | stage-one weekly budget 0.25 of funding; shock loss budget 0.10 of funding | `weekly_fraction`, `start.shock.loss_fraction` |
| $\epsilon$ | tolerance on every row | solver `parity_tolerance` 0.001 |

## 3. Decision variables

| variable | domain | stage |
|---|---|---|
| $x_j$ | contracts of item $j$, integer in $[0,\bar q_j]$ | both |
| $z_e$ | binary side of exclusive pair $e$ | stage one |
| $r_g\ge0$ | reserve of group $g$ | stage one; stage two uses $R_g(x)=\max\{0,\max_ra_{gr}^\top x_g\}$ |
| $\phi_{gk}$, $\lambda_g\ge0$ | W1 dual potentials and multiplier | stage one |

## 4. The model

**(P) The one-stage fractional-Kelly program.** This is the program the traded book is chosen for. Write
$W_s(x)=w_0+\sum_g\sum_j\big(m_ge_j+\tau_{sj}\big)x_j$. Then

$$
\begin{aligned}
\text{(P)}\quad \max_{x}\quad & w_0\sum_{s\in\mathcal S}\omega_s\,u_\gamma\big(W_s(x)\big)\;-\;\frac{\eta\gamma}{2w_0}\sum_{n}\Big(\sum_{g:n(g)=n}M_g(x_g)\Big)^2 && (1)\\
\text{s.t.}\quad & x_j\in\{0,1,\dots,\bar q_j\} && (2)\\
& x_ax_b=0\quad\forall(a,b)\in\mathcal E_g && (3)\\
& b_g^\top x_g\le 0\quad\forall g && (4)\\
& \textstyle\sum_g R_g(x_g)\le B && (5)\\
& W_s(x)\ge \underline W\quad\forall s\in\mathcal S && (6)
\end{aligned}
$$

The utility is $u_\gamma(W)=\big((W/w_0)^{1-\gamma}-1\big)/(1-\gamma)$, which is 0 at $w_0$ (PY:174).

$M_g$ is the group's dispersion on the book's terminal payoffs $v_k=-\ell_{gk}^\top x_g$ over atoms with positive mass.
With $t_k=\lvert v_k-\bar v\rvert$, it is the maximum of these (PY:3694, PY:4438):

- $1.25\sum_k p_kt_k$;
- the lower-tail and upper-tail sd bounds $\sum_{k\in T}p_kt_k/\sqrt{P_T}$;
- $\max_k\sqrt{p_k}\,t_k$.

Rows (4)-(6) carry slack $\epsilon$. There is no weekly budget, per-name cap, shock row or W1 radius. A kelly method
must declare it that way (LS:3515).

**(D) Stage one, the dollar MILP.** It is only used to find a start. $A$ is the set of groups kept by the LP screen.

$$
\begin{aligned}
\text{(D)}\quad \max_{x,z,r,\phi,\lambda}\quad & \sum_{g\in A}\Big[(c_g+(m_g-1)e_g)^\top x_g-\sum_kp_{gk}\phi_{gk}-\rho\lambda_g\Big] && (7)\\
\text{s.t.}\quad & \phi_{gk}\ge\ell_{gk}^\top x_g,\quad \lvert\phi_{g,k+1}-\phi_{gk}\rvert\le\lambda_g\,(s_{g,k+1}-s_{gk})/S_g && (8)\\
& (2),\ (4);\quad x_a\le\bar q_az_e,\ x_b\le\bar q_b(1-z_e)\ \ \forall e=(a,b) && (9)\\
& r_g\ge a_{gr}^\top x_g\ \ \forall r,\qquad r_g\ge0 && (10)\\
& \textstyle\sum_gr_g\le\max(0,\Phi)-\Omega,\quad \sum_gr_g\le0.25\max(0,\Phi),\quad \sum_{n(g)=n}r_g\le\max(0,\Phi)-\Omega_n && (11)\\
& \textstyle\sum_g\big(\ell_g(S_g(1+\beta_{n(g)}\mu))-c_g\big)^\top x_g\le0.10\max(0,\Phi)\quad\forall\mu\in\mathcal M^{(1)} && (12)\\
& x_g=0\quad\forall g\notin A && (13)
\end{aligned}
$$

Rows (7)-(8) give the W1-worst expected loss in dual form (PY:3370). The screen keeps a group when its own LP relaxation
of (7)-(10), with caps and (12) dropped, is worth more than $\epsilon$ (PY:4597).

The two programs share the same menu and the same $m_g$ (LS:5467). Which knob enters where:

- `start.rho`: (D) only;
- `fraction` and `name_spread`: (P) only;
- `dte_tilt` and `accuracy`: both.

## 5. How it is solved

```text
for each session t in the calendar:                                         # LS:5272
    settle books expiring today at the close; add any due deposit           # OL:1392
        -> account (Phi, cash, open books, reserves)
    if last decision is pending:                                            # fill, LS:4859
        reprice each book at today's bid/ask; scale to displayed depth
        attach hedge U = trunc(sum u_j q_j) at today's parity forward
        admit the batch if the new reserves fit max(0,Phi) - Omega, else refuse all
    if t is a decision day:
        build menu G_t, m_g, omega, tau, w0 from the inputs above            # LS:5323-5504
        # stage one: (D) with HiGHS (appsi, threads 1, mip_rel_gap 0.1, mip_abs_gap 1, no seed set)
        A <- LP screen (one LP per group)                                   # PY:4597
        x1 <- HiGHS(D) under time_limit 300 s                               # LS:4715
        if finished: start <- x1 (cash if its value <= 0)
        elif stopped with an incumbent (gap > 10%, or audit miss): start <- incumbent
        else: start <- cash                                                 # LS:4744
        # stage two: climb (P) exactly; no model is built
        if start infeasible for (P): drop one contract at a time            # KL:506
            (smallest breach, then best objective) until feasible
        x <- start
        repeat at most 200 times:                                           # KL:359
            rank items by first-order gain from the current wealth vector
            candidates <- 64 best feasible adds
                        + every removal of a held item
                        + 64 best swaps (add side from the 64 best
                          capacity/exclusivity-eligible items; _trial
                          rejects infeasible swaps)
            score each candidate exactly on (1)-(6)                         # KL:234
            take the best gain if > 1e-9 * w0, else stop                    # KL:347
        if F(x) < F(cash) = 0: x <- cash                                    # KL:432
        record the book (objective = end_value, certified = false); fill next session
```

The result is **not a global optimum** of (P).

- Stage one stops at a 10% (or $1) gap, or at 300 s. Then it only supplies a start.
- The climb stops at a local optimum of the *evaluated* neighbourhood, or after 200 moves.
- `end_value` is only a lower bound on the optimum of (P) (decision log, ADR-0307 limits). No bound or gap is computed.
- Solving (P) directly as one MIP did not finish: 300,000 to 430,000 rows, and 12 of 13 weeks skipped on 2021Q1
  (ADR-0307 context).

The climb is deterministic: it reads no clock and breaks ties by item rank. A stage one stopped by its clock can depend
on the host.

## 6. Known limitations

- **Thin joint risk.** It is only 17 scenarios: basket moves capped at $\pm15\%\times\beta$ on a fixed Student $t_4$.
  Name-specific risk enters only through the quadratic charge, which treats names as independent. Each forecast grid
  ends at $z=\pm3$.
- **Profit is concentrated.**
  - sdte 2020Q1 made +31,368. Of that, the APA book opened 27 Jan 2020 made +20,610 and the APA book opened 3 Feb 2020
    made +10,187.
  - Without the best book, the four-quarter total falls from +84,114 to +63,505.
  - Sources: `D:\mio-analysis-20261115\GRID-MORNING-20261117.md` 56-65, and `stress\out\sdte.md` lines 9 and 45-48.
- **Unseen quarters lost money** (`GRID-MORNING-20261117.md` 45-54):

  | run | 2020Q2 | 2020Q3 |
  |---|---|---|
  | fsdte | +5,449 | -5,694 |
  | fsdte25 | +3,815 | -7,897 |
  | fg075r010 | +3,520 | -4,482 |
  | fsbase | -2,235 | not run |

  Every candidate was stopped by the negative-quarter rule. The knobs were chosen in-sample (ADR-0318).
- **Both 2021Q1 solves failed on the round-2 memory code** (`79860494`, which is not this revision).
  - sdte25, week 5: pricing-LP infeasibility.
  - s100dte, week 9: "selected physical-domain reserve parity failed" (LS:4398).
  - The cause is unresolved (`GRID-OVERNIGHT.md` 157-160, 290-294).
- **Execution is a proxy.**
  - Fills use the next session's end-of-day NBBO with displayed depth.
  - Books are held to expiry.
  - American assignment is not modelled.
  - The hedge cost source is a placeholder.
  - There is no certificate, and capped stage-one weeks may not reproduce across hosts.
