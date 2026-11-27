# MIO design session and cadence experiment handoff

## TL;DR

25 November: the working formulation now includes marginal calibration and its
chronological selection procedure. Resume with PIT alignment across assets and
expiry ranks, then define the copula. No calibration fitting, validation or joint
scenario experiment was run in this session.

26 November: step 1 (PIT alignment) is proposed in the next section and awaits
owner confirmation. It is on the session branch only; nothing was fitted or run.

27 November: the section after step 1 writes the whole chain, from the model's
inputs to joint scenarios, in formulation form with dimensions and updates. It
is also a proposal awaiting confirmation; nothing was fitted or run.

## 26 November proposal: step 1, PIT alignment (awaiting confirmation)

Owner: Russell; author: Claude Fable 5.2, from a draft by Claude Opus 5.5 in the
same session. This is a proposal: not an agreed definition, not a result, and
not yet in the Working Draft. It sits on the session branch so the math renders
on GitHub. Nothing was fitted, replayed or read from protected data; no code,
config, ledger or Path row changed.

The Page could not be opened from this session (it needs the owner's sign-in).
The six symbols marked "new" are therefore unchecked against it.

Reused unchanged: cutoff session $d$, historical origin $a$ before $d$ on the
same session clock, stock $n$ in $N$, calendar DTE $t$, session count $\eta$ and
original expiry rank $\rho$. The agreed CDF, raw PIT, counters and correction
map are restated below for one cell.

**Cells and targets.** A cell is a stock and an original expiry rank (A0006).
The rank is the expiry's place in line on the day the forecast is made: rank 1
is the stock's nearest listed Friday expiry, rank 2 is the one after it. A cell
is the group whose PITs are counted and calibrated together.

```math
n\in N,\qquad \rho\in\mathcal R_n,\qquad
\mathcal R_n:=
\begin{cases}
\lbrace 1,\dots,8\rbrace, & n\text{ a weekly name},\\
\lbrace 1,2,3\rbrace, & n\text{ a monthly-only name}.
\end{cases}
\qquad\text{(new)}
```

At origin $a$:

```math
\begin{aligned}
E_{n\rho\mid a} &:= \text{the expiry that A0006 ranks }\rho\text{ for stock }n\text{ at origin }a, \qquad\text{(new)}\\
t_{n\rho\mid a} &:= \text{calendar days from }a\text{ to }E_{n\rho\mid a},\\
\eta_{n\rho\mid a} &:= \text{trading sessions from }a\text{ to that expiry-price observation}.
\end{aligned}
```

The rank is fixed when the forecast is issued. It is an identity label and is
never re-ranked as the contract approaches expiry. For example, a forecast made
on Monday 6 March 2023 with rank 2 targets Friday 17 March, 11 calendar days and
9 trading sessions ahead. A week later that date is the nearest Friday, but the
forecast keeps rank 2.

**Earliest usable cutoff.** The first cutoff at which the PIT may be used:

```math
d^{\star}_{n\rho\mid a} := \min\lbrace\, d\ge a+\eta_{n\rho\mid a} :\ \text{the expiry-price record is available by cutoff }d \,\rbrace \qquad\text{(new)}
```

**CDF, raw PIT and correction for one cell.** These were agreed on 25 November.
They are restated here with the cell index and the earliest usable cutoff; the
counter and map symbols follow the fitting-methods report.

The forecast issued at origin $a$ for stock $n$ and the expiry at calendar DTE
$`t=t_{n\rho\mid a}`$ has the CDF below: the forecast probability that the
expiry price is at or below $p$.

```math
\widehat\Psi_{nt}(p\mid\mathcal F_a):=\widehat{\Pr}(p_{nt}\le p\mid\mathcal F_a)
```

The raw PIT reads that CDF at the price that actually occurred. It is the agreed
$`u_{nt\mid a}`$, filed under its cell.

```math
u_{n\rho\mid a}:=\widehat\Psi_{nt}\left(S_{n,\,a+\eta_{n\rho\mid a}}\mid\mathcal F_a\right)\in[0,1]
```

At cutoff $d$ the cell's counters, over the configured thresholds
$`\beta\in\mathcal Q`$, are:

```math
\begin{aligned}
M_{n,\rho,d} &:= \#\lbrace a<d :\ d^{\star}_{n\rho\mid a}\le d \rbrace,\\
A_{n,\rho,d}(\beta) &:= \#\lbrace a<d :\ d^{\star}_{n\rho\mid a}\le d,\ u_{n\rho\mid a}\le\beta \rbrace.
\end{aligned}
```

The correction map is set at each threshold, anchored at both ends and linear in
between. The one shared strength $\zeta$ is positive; a larger value pulls the
map toward no correction.

```math
\widehat\Gamma_{n,\rho,d}(\beta):=\frac{A_{n,\rho,d}(\beta)+\zeta\beta}{M_{n,\rho,d}+\zeta},
\qquad \widehat\Gamma_{n,\rho,d}(0):=0,\qquad \widehat\Gamma_{n,\rho,d}(1):=1
```

The corrected CDF and the corrected PIT use the map issued with the forecast at
$a$, never today's map:

```math
\begin{aligned}
p &\mapsto \widehat\Gamma_{n,\rho,a}\left(\widehat\Psi_{nt}(p\mid\mathcal F_a)\right),\\
w_{n\rho\mid a} &:= \widehat\Gamma_{n,\rho,a}\left(u_{n\rho\mid a}\right)\in[0,1]. \qquad\text{(new)}
\end{aligned}
```

The corrected PIT $w$ is the copula input. Raw $u$ and corrected $w$ are both
kept. Where no map was issued at $a$, for example during warmup, $w$ is not
defined here; that fallback stays open.

**Joint observation.** All forecasts issued at one origin form one joint
observation. Its coordinates are cells, not calendar dates. This mirrors the
solve, where every forecast in the book shares one origin.

```math
\begin{aligned}
\mathcal C_a &:= \lbrace (n,\rho) :\ n\in N,\ \rho\in\mathcal R_n,\ \text{a valid forecast was issued at }a \rbrace, \qquad\text{(new)}\\
\mathcal O_{a,d} &:= \lbrace (n,\rho)\in\mathcal C_a :\ d^{\star}_{n\rho\mid a}\le d \rbrace. \qquad\text{(new)}
\end{aligned}
```

Two cells of one origin are admissible as a pair at cutoff $d$ once both have
settled:

```math
(n,\rho),\,(n',\rho')\in\mathcal O_{a,d}
\iff
d\ge\max\left(d^{\star}_{n\rho\mid a},\ d^{\star}_{n'\rho'\mid a}\right).
```

A cell is **pending** while it is in $`\mathcal C_a`$ but not yet in
$`\mathcal O_{a,d}`$, and **absent** when it is not in $`\mathcal C_a`$ or is
marked `NEEDS_REPAIR`. Neither is ever imputed. While 2024 onward stays sealed
(A0003, A0010) the cutoff never passes the end of 2023, so a cell expiring later
stays pending. Consecutive origins cover largely the same returns; that lowers
the independent information and is left to the later uncertainty rules.

```text
INPUT cutoff d; Appendix C output (u records, PENDING, NEEDS_REPAIR); issued maps

FOR each u record (a, n, t) from Appendix C:
    rho    <- original expiry rank of that forecast (A0006)
    d_star <- first cutoff at which its expiry price was available

    IF the map issued at a cannot be verified:
        Mark NEEDS_REPAIR; record the reason; CONTINUE.

    w <- Gamma_hat[n,rho,a](u)

    APPEND row (a, n, rho, E, t, eta, d_star, u, w,
                forecast, map and outcome versions).

Settled[a,d] <- appended rows of origin a        (every such row has d_star <= d)
A pair of rows of origin a is usable once d >= the larger of their two d_star.

OUTPUT appended rows. PENDING and NEEDS_REPAIR records stay separate.
       Nothing is imputed.
```

Storage plan: one append-only row per $(a,n,\rho)$, partitioned by the earliest
usable cutoff. The open forecasts cover 2019Q4 to 2023Q4, about 1,070 sessions;
with at most 166 x 8 = 1,328 cells per origin that is at most about 1.4 million
rows, an estimated tens of megabytes, assuming one origin per session (the
2020Q1 forecast receipt supports this: 69,687 rows over 62 sessions). Marginal
counters cannot recover this joint record. Nothing has been written.

Existing dependence code, not yet reconciled with this step:
`index_options/kelly_sizer.py` (`joint_atoms`, a one-factor Student t copula,
ADR-0300) and `index_options/joint_scenarios.py` (`EmpiricalCopula`, which
block-bootstraps whole days of the pinned `copula_bars` closes, sums each name's
returns to its horizon and ranks the draws). Both take dependence from daily
returns aligned on calendar days and need no PIT history. Whether step 2 extends
`JointScenarioGenerator` or estimates a copula from the PIT rows above is an
owner choice; in the first case these rows serve validation only.

To confirm, with (a) recommended in each:

1. Cell identity: (a) $(n,\rho)$ with $E$ recorded, following A0006; (b) align
   on a shared expiry date.
2. Copula input: (a) corrected $w$, because scenarios use the corrected
   marginals; (b) raw $u$.
3. Admission: (a) pairwise, as defined above; (b) whole origin only, which waits
   about eight weeks and drops any origin with an absent cell.
4. Origins: (a) every origin, with overlap treated later; (b) a non-overlapping
   subsample.

## 27 November proposal: the forecast-to-scenario chain in formulation form (awaiting confirmation)

Owner: Russell; author: Claude Fable 5.2. Each stage from the model's inputs to
joint scenarios is written as it would read in the MIO formulation: its
definition, the sets its indices run over, its dimension and, where a value is
carried from one decision to the next, an update with the new assignment. Every
object carries all the indices it varies with, every symbol is defined in the
stage that uses it, and a "Why" note follows each step whose reason is not
obvious. This is a proposal on the session branch, not in the Working Draft.
Nothing was fitted, replayed or read from protected data; no code, config,
ledger or Path row changed.

Sources. Stages 1 and 4 to 7 restate definitions agreed on 25 November. The
update lines of stages 6 and 7 are written out here for the first time, and
their eligibility test uses the earliest usable cutoff proposed in step 1.
Stages 2 and 3 describe the locked forecast recipe. Their sizes were read from
the recipe file, its fit template, the planned 2020Q1 fit document and the fit
code; the functions that set them, and the estimator of the market quantiles,
are identical in this branch and at the commit recorded in the 2020Q1 forecast
receipt. The panel that holds the quantiles was built before that run, and its
own commit was not traced. No saved fit was opened and no code was run. Stages 8 to 11 rest on
step 1 above and on step 2, which is not yet proposed. The Page could not be
opened, so every symbol marked "new" is unchecked against it. A0002, A0006,
A0008 and A0009 are locked rows of the Path, the owner's decision ledger:
scoring, the cell definition, the forecast recipe and the stock list.

### Reading the notation

These general marks are defined once. Every other symbol is defined again in
each stage that uses it.

- $`:=`$ means "is defined as". $`\leftarrow`$ means "is assigned": the left
  side is the value at the next cutoff, and the right side uses the values held
  now.
- $`n\in N`$ means $n$ is a member of the set $N$. $`\lvert N\rvert`$ is the
  number of members of $N$. $`\#\lbrace\cdot\rbrace`$ is the number of items
  that meet the condition inside the braces. $\cup$ joins two sets.
- $`\mathbb R`$ is the real numbers and $`\mathbb Z`$ the integers; a subscript
  such as $`\ge 0`$ restricts them. $`[0,1]`$ is the numbers from 0 to 1. A
  superscript on $`\mathbb R`$ or on $`[0,1]`$ is the length of a list:
  $`\mathbb R^{19}`$ is a list of 19 real numbers.
- An arrow between two sets, as in $`\mathbb R\to[0,1]`$, describes a function
  that takes a member of the first and returns a member of the second. A
  superscript $-1$ on a function marks its inverse, which returns the input
  that gives a stated output. $\mapsto$ reads "is sent to".
- A bracket with a set under it, as in $`\left(\cdot\right)_{n\in N}`$, is a
  list with one entry for each member of that set.
- A hat marks a forecast or an estimate. A prime, as in $`d'`$, marks the next
  cutoff. A tilde marks a simulated value. Bold marks a list of numbers.
- A bar inside brackets, as in $`(z\mid\mathcal F_d)`$, reads "given the
  information $`\mathcal F_d`$".
- $\ln$ is the natural logarithm and a root sign the square root. $\sum$ is a
  sum; the lines under and over it give the range of its counter. A superscript
  2 is a square. $\max$ takes the larger of two numbers; $\min$ takes the
  smallest member of a set.
- CDF is short for cumulative distribution function: the probability that a
  quantity is at or below a stated value.

Three rules govern superscripts and subscripts.

- **Placement.** As in the Working Draft's $`(\cdot)_{knt}^{ij}`$, a superscript
  says which kind of piece is meant and a subscript says which stock, expiry,
  rank or session. There the superscripts are the transaction side and the
  option type; here they are also the seed and the mixture component. A value
  read at a level takes the level in brackets, as in $`A_{n,\rho,d}(\beta)`$.
- **Origin.** A quantity with the subscripts $nt$ belongs to the forecast issued
  at the current cutoff $d$, and $d$ is left out, as in the Working Draft. The
  same quantity for a forecast issued at an earlier session $a$ adds a bar and
  the origin, as in $`\eta_{nt\mid a}`$. Each such definition therefore holds at
  every origin.
- **Nothing undefined.** Every superscript and subscript is an index over a set
  named in the stage that uses it. The only exceptions are these marks, which
  are not indices and are defined where they appear: the hat, prime, tilde and
  bold above; the star in $`d^{\star}`$; the superscript $-1$ of an inverse; the
  superscript $Z$ of the forecast CDF; the word market on the set
  $`\mathcal Q^{\text{market}}`$; and the signs on $`\beta_{-}`$, $`\beta_{+}`$,
  $`s_{-}`$ and $`s_{+}`$. Anything else is written out in words.

### Existing sets and clocks

```math
\begin{aligned}
&i\in I=\lbrace b,s\rbrace,\qquad j\in J=\lbrace c,p\rbrace,\qquad n\in N,\qquad t\in T,\qquad k\in K_{nt}^{j},\\
&d:\ \text{current cutoff session and forecast origin},\qquad a<d:\ \text{historical origin},\\
&\beta\in\mathcal Q:\ \text{calibration thresholds},\qquad \rho:\ \text{original expiry rank}.
\end{aligned}
```

**Symbols.** All are already in the MIO and are reused unchanged.

- $i$, $I$, $b$, $s$: the transaction side, one of the set $I$; $b$ is buy and
  $s$ is sell.
- $j$, $J$, $c$, $p$: the option type, one of the set $J$; $c$ is call and $p$
  is put. Elsewhere $p$ with a subscript, or as the argument of a CDF, is a
  price.
- $n$, $N$: one stock, and the stock universe of 166 names (A0009).
- $t$, $T$: the remaining calendar days to an expiry, and the set of such
  values in use.
- $k$, $`K_{nt}^{j}`$: one option contract, and the contracts available for
  stock $n$, expiry $t$ and type $j$.
- $d$: the current cutoff session. Sessions are trading days numbered in order.
  The information used is what was available by this session's cutoff, and the
  forecasts issued now have $d$ as their origin.
- $a$: an earlier session at which a forecast was issued; its origin.
- $\beta$, $`\mathcal Q`$: one calibration threshold, a probability level
  between 0 and 1, and the configured set of them. The agreed default has 11,
  from 0.01 to 0.99.
- $\rho$: the original expiry rank, the expiry's place in line on the day the
  forecast is issued. For a stock with weekly expiries, rank 1 is its nearest
  listed Friday expiry and rank 2 the one after it. A forecast keeps its rank
  for life.

The sets below are not yet in the Working Draft. They are what the indices of
this section run over.

```math
\begin{aligned}
\rho&\in\mathcal R_n, &&\mathcal R_n:=\begin{cases}\lbrace 1,\dots,8\rbrace,& n\text{ a weekly name},\\ \lbrace 1,2,3\rbrace,& n\text{ a monthly-only name},\end{cases}\\
(n,\rho)&\in\mathcal C, &&\mathcal C:=\lbrace (n,\rho):\ n\in N,\ \rho\in\mathcal R_n\rbrace,\\
\beta&\in\mathcal Q^{\text{market}}, &&\mathcal Q^{\text{market}}:\ \text{probability levels of the market quantiles},\\
e&\in\mathcal E, &&\mathcal E:\ \text{seeds},\\
y&\in Y, &&Y:\ \text{mixture components of one seed},\\
\omega&\in\Omega, &&\Omega:\ \text{scenarios}.
\end{aligned}
```

**Symbols.**

- $`\mathcal R_n`$ (proposed in step 1): the ranks used for stock $n$. A0006
  sets ranks 1 to 8 for a stock with weekly expiries and positions 1 to 3 for a
  stock with monthly expiries only.
- $`\mathcal C`$ (new): the set of all cells. A cell $`(n,\rho)`$ is one stock
  with one rank: the group whose forecasts are counted and calibrated together.
  It has at most 166 x 8 = 1,328 members.
- $`\mathcal Q^{\text{market}}`$ (new): the probability levels at which the
  option market's quantiles are read; $\beta$ in it is one level. The word
  market is a typed-out label, not an index. There are 19 levels: 0.01, 0.025,
  0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9,
  0.95, 0.975 and 0.99. It is a different grid from the calibration thresholds
  $`\mathcal Q`$; in both, $\beta$ is a probability level.
- $e$, $`\mathcal E`$ (new): one seed, and the set of seeds. A seed fixes the
  random start of one trained network. There are 5: 11, 29, 47, 53 and 71.
- $y$, $Y$ (new): one mixture component of a seed's network, and the set of
  them. There are 3.
- $\omega$, $\Omega$ (new): one scenario, and the set of scenarios. Its size is
  not chosen.
- $`d'`$ (new): the next cutoff session after $d$. If every session is
  processed, $`d'=d+1`$.

At origin $d$ the cell $`(n,\rho)`$ and the forecast $`(n,t)`$ name the same
thing when $t$ is the number of calendar days from $d$ to the expiry of rank
$\rho$. Every stage below is per stock and expiry, so it carries $n$ with $t$ or
$\rho$; the contract indices $i$, $j$ and $k$ enter in stage 11. In every
update, the left side belongs to the next cutoff and the right side uses the
snapshot at $d$, as in the holdings update.

### Stage 1. Preprocessing (agreed)

```math
\begin{aligned}
r_{n,d}&:=\ln\left(\frac{S_{n,d}}{S_{n,d-1}}\right),\\
\chi_{n,d}&:=\sqrt{\frac{1}{22}\sum_{\ell=0}^{21} r_{n,d-\ell}^{2}},\\
\kappa_{nt}&:=\max\left(\chi_{n,d},\ 0.001\right)\sqrt{\eta_{nt}},\\
Z_{nt}&:=\frac{\ln(p_{nt}/S_{n,d})}{\kappa_{nt}}.
\end{aligned}
```

**Symbols.**

- $n$, $t$, $d$: the stock, the calendar days to the expiry, and the current
  cutoff session.
- $`S_{n,d}`$: the split-adjusted closing price of stock $n$ at session $d$, in
  dollars per share. It is positive.
- $`r_{n,d}`$: the log return of stock $n$ over session $d$.
- $\ell$: a counter that steps back over the last 22 sessions. The subscript
  $`d-\ell`$ is the session $\ell$ sessions before $d$, so 0 is session $d$
  itself. The superscript 2 is a square.
- $`\chi_{n,d}`$: the root mean square of the last 22 one-session log returns of
  stock $n$, ending at session $d$. It is zero or positive. Code field `rv_22`.
- $`\eta_{nt}`$: for stock $n$ and the expiry $t$ calendar days after $d$, the
  number of trading sessions from session $d$ to the session whose close is the
  expiry price. A positive integer. Code field `sessions_to_expiry`.
- $`\kappa_{nt}`$: the scale that the return of stock $n$ to expiry $t$ is
  divided by. It is positive. Code field `reference_scale`.
- $`p_{nt}`$: the price of stock $n$ at the expiry $t$ calendar days after $d$.
  It is not known at $d$.
- $`Z_{nt}`$: the normalized log return of stock $n$ to expiry $t$. This is what
  the model forecasts.

**Indices and dimension.** The first two lines hold for every stock $`n\in N`$
and every session $d$: one $r$ and one $\chi$ per stock and session, so
$`\lvert N\rvert`$ = 166 of each at a cutoff. The last two hold for every stock
$`n\in N`$ and every expiry $`t\in T`$ forecast at $d$: one $\eta$, $\kappa$ and
$Z$ per forecast. At an earlier origin $a$ they read $`\chi_{n,a}`$,
$`\eta_{nt\mid a}`$, $`\kappa_{nt\mid a}`$ and $`Z_{nt\mid a}`$.

**Why.**

- **What the scale measures.** The root mean square of the last 22 daily log
  returns says how much the stock has typically moved in one session lately.
  It uses only past prices, so it is known at the origin.
- **Why divide the return by a scale.** Stocks differ widely in how much they
  move, and a two-month return moves more than a one-week return. Dividing by a
  scale known at the origin puts every forecast in comparable units, so one
  network can serve all 166 stocks and every expiry. The earlier comparison memo
  also gives numerical stability as the reason.
- **Why 22.** It is the look-back window of the scale, about one trading month.
  It is not the forecast horizon: the horizon enters through $`\eta_{nt}`$.
- **Why the square root of the session count.** The typical size of a return
  grows roughly with the square root of the number of sessions it covers.
  Multiplying the one-session scale by that root stretches it to the forecast's
  own horizon.
- **Why the floor of 0.001.** It keeps the scale positive for a stock that has
  barely moved, so the division is always defined.
- **Why these exact numbers.** 22 and 0.001 are the preprocessing the locked
  model was trained with. Changing either would change what the trained networks
  expect, so they are restated here, not chosen.

**Update (agreed, restated).** When the next session is observed, for every
stock $`n\in N`$ and every expiry $`t\in T`$ forecast at $`d'`$:

```math
\chi_{n,d'}\ \leftarrow\ \sqrt{\frac{1}{22}\sum_{\ell=0}^{21} r_{n,d'-\ell}^{2}},\qquad
\kappa_{nt}\ \leftarrow\ \max\left(\chi_{n,d'},\ 0.001\right)\sqrt{\eta_{nt}}
```

Here $`d'`$ is the next cutoff session, $`r_{n,d'-\ell}`$ is the log return of
stock $n$, $\ell$ sessions before it, and $`\eta_{nt}`$ and $`\kappa_{nt}`$ are
now counted from the origin $`d'`$. The window slides by one session: the oldest
squared return leaves and the newest enters. Carried state: each stock's last 22
squared returns, 22 x $`\lvert N\rvert`$ = 3,652 numbers.

### Stage 2. Model input (new symbols)

```math
q_{nt}(\beta)\in\mathbb R\quad\text{for }\beta\in\mathcal Q^{\text{market}},\qquad
\left(\left(q_{nt}(\beta)\right)_{\beta\in\mathcal Q^{\text{market}}},\ \ln\eta_{nt},\ t/\eta_{nt}\right)\in\mathbb R^{\lvert\mathcal Q^{\text{market}}\rvert+2}
```

**Symbols.**

- $n$, $t$: the stock and the calendar days to the expiry. The origin is the
  current cutoff session $d$, left out of the subscripts.
- $\beta$, $`\mathcal Q^{\text{market}}`$ (new): one probability level, and the
  set of 19 levels at which the market's quantiles are read. The word market is
  a typed-out label, not an index.
- $`q_{nt}(\beta)`$ (new): the market-implied quantile for stock $n$ and expiry
  $t$ at level $\beta$. Under the distribution implied by that expiry's option
  prices, the normalized return $`Z_{nt}`$ of stage 1 falls at or below this
  value with probability $\beta$. Code fields `rn_q_0100` to `rn_q_9900`.
- $`\eta_{nt}`$: the number of trading sessions to the expiry-price session, as
  in stage 1. $`\ln\eta_{nt}`$ is its natural logarithm. Code field
  `log_sessions_to_expiry`.
- $`t/\eta_{nt}`$: calendar days per trading session over the forecast's life.
  For one full week it is 7/5 = 1.4. Code field `calendar_days_per_session`.

**Where the quantiles come from.** They are built from the end-of-day option
quotes of stock $n$ for the expiry $t$ calendar days after $d$, in five steps:

```math
\begin{aligned}
\text{mid}_{nt}^{j}(s)&:=\tfrac12\left(\text{bid}_{nt}^{j}(s)+\text{ask}_{nt}^{j}(s)\right),\\
\text{fwd}_{nt}&:=\operatorname{median}\lbrace\, s+\text{mid}_{nt}^{c}(s)-\text{mid}_{nt}^{p}(s) :\ s\text{ quoted for both a call and a put}\,\rbrace,\\
\text{cq}_{nt}(s)&:=\begin{cases}\text{mid}_{nt}^{c}(s), & s\ge\text{fwd}_{nt},\\ \text{mid}_{nt}^{p}(s)+\text{fwd}_{nt}-s, & s\le\text{fwd}_{nt},\end{cases}\\
\widehat G_{nt}\left(\tfrac12(s_{-}+s_{+})\right)&:=1+\operatorname{iso}\left(\frac{\text{cq}_{nt}(s_{+})-\text{cq}_{nt}(s_{-})}{s_{+}-s_{-}}\right),\\
q_{nt}(\beta)&:=\frac{1}{\kappa_{nt}}\ \ln\left(\frac{\widehat G_{nt}^{-1}(\beta)}{S_{n,d}}\right),\qquad \beta\in\mathcal Q^{\text{market}}.
\end{aligned}
```

**Symbols of the five steps.** All but the first three entries are new.

- $n$, $t$, $d$: the stock, the calendar days to the expiry, and the origin
  session. $j$: the option type; as a superscript, $c$ is call and $p$ is put.
- $s$: a strike price in dollars per share, read in brackets. A contract is
  named here by its strike; in the Working Draft the same contract is named by
  its index $k$. $`s_{-}`$, $`s_{+}`$: two neighbouring strikes, the lower and
  the higher; the signs are labels, not indices.
- $`\text{bid}_{nt}^{j}(s)`$, $`\text{ask}_{nt}^{j}(s)`$: the Working Draft's
  per-share bid and ask of the option of type $j$ on stock $n$, expiry $t$ and
  strike $s$, read at the close of session $d$. Only valid quotes are used: bid
  above zero and ask at least the bid.
- $`\text{mid}_{nt}^{j}(s)`$: the midpoint of that quote.
- $`\text{fwd}_{nt}`$: the forward price of stock $n$ for that expiry implied by
  the quotes. The median is the middle value over the strikes that have both.
- $`\text{cq}_{nt}(s)`$: the call-equivalent quote at strike $s$: the call's
  midpoint at or above the forward, and the put's midpoint restated as a call
  price at or below it.
- $`\operatorname{iso}`$: a smoothing that changes the slopes as little as
  possible so that they never fall from one strike to the next and stay between
  -1 and 0.
- $`\widehat G_{nt}`$: the market-implied CDF of the expiry price: under the
  option quotes, the probability that stock $n$ finishes at or below a strike.
  It is set at the midpoint of each pair of neighbouring strikes, joined by
  straight lines in the logarithm of the strike, and completed with 0 at the
  lowest strike kept and 1 at the highest. $`\widehat G_{nt}^{-1}(\beta)`$ is
  the price at which it equals $\beta$; the superscript $-1$ marks the inverse.
- $`S_{n,d}`$, $`\kappa_{nt}`$: the closing price at the origin and the scale of
  stage 1.

**Why each step.**

- **Why the midpoint.** It is the middle of what buyers bid and sellers ask,
  the usual single price for a quoted option.
- **Why the forward.** A call and a put at one strike differ in price by the
  forward minus the strike. Each strike quoted on both sides therefore gives
  the forward, and the median guards against one bad quote. The same relation
  restates a put as a call.
- **Why the two cases.** Each strike uses its out-of-the-money contract: the
  put below the forward and the call above it.
- **Why the slope gives a probability.** Raising a call's strike by one dollar
  lowers its payoff by one dollar whenever the stock ends above the strike, and
  by nothing otherwise. So the fall in price per dollar of strike is the
  probability of ending above it, and 1 plus the slope is the probability of
  ending at or below it.
- **Why smooth the slopes.** Quotes are noisy, so raw slopes can run the wrong
  way, and a probability cannot fall as the strike rises.
- **Why divide by the scale.** It puts the quantiles in the same normalized
  units as the return being forecast.
- **Why it is only a proxy.** Option prices mix probability with the price of
  risk; these stock options can be exercised early, which the steps ignore; and
  no interest or dividend adjustment is applied.

**When there are no quantiles.** If the chain is too thin (no strike quoted on
both sides, too few strikes on either side of the forward, or gaps that are too
wide), no quantiles are produced for that forecast. The fit code then replaces
each missing input with its median over the training rows, and the network is
not told that it was filled. How often this happens was not measured here.

**Indices and dimension.** One input list for every stock $`n\in N`$ and every
expiry $`t\in T`$ forecast at $d$. It has
$`\lvert\mathcal Q^{\text{market}}\rvert+2`$ = 19 + 2 = 21 numbers. At an earlier
origin $a$ it reads $`q_{nt\mid a}(\beta)`$ and
$`\eta_{nt\mid a}`$.

**This is the whole input.** The study lists 327 candidate feature columns,
among them past returns, realized volatility, implied-volatility level, volume
and open interest. The locked recipe reads none of those: its inputs are these
21 numbers only, as the planned 2020Q1 fit document confirms. The stock's own
price history enters in one place, the scale $`\kappa_{nt}`$ of stage 1, which
divides both the quantiles and the return being forecast.

**Why.**

- **Why the market's quantiles.** Option prices already hold the market's view
  of where the stock may finish. The locked recipe gives the network that view
  and the two calendar numbers, and nothing else. The choice came from the model
  selection recorded in A0008; it is restated here, not argued again.
- **Why they are a proxy.** Option prices mix probability with the price of
  risk, so these quantiles are not real-world probabilities. The network is
  trained on realized outcomes to turn them into a real-world forecast.
- **Why the two calendar numbers.** One network serves every horizon, so it must
  be told the horizon. The logarithm gives its length; the ratio tells it how
  much of the period is weekends and holidays.

**No update.** The input is rebuilt at each origin from that session's quotes.

### Stage 3. Network (locked recipe, A0008)

For every seed $`e\in\mathcal E`$ there is one network. It has two parts:

```math
\operatorname{enc}_{d}^{e}:\ \mathbb R^{\lvert\mathcal Q^{\text{market}}\rvert}\to\mathbb R^{8},\qquad
\operatorname{head}_{d}^{e}:\ \mathbb R^{8+2}\to\mathbb R^{3\lvert Y\rvert},\qquad e\in\mathcal E
```

For every stock $`n\in N`$ and every expiry $`t\in T`$ forecast at $d$, it
returns one weight, one centre and one standard deviation per component:

```math
\left(\text{wt}_{nt}^{ey},\ \text{loc}_{d}^{ey},\ \text{sd}_{nt}^{ey}\right)_{y\in Y}
:=\operatorname{head}_{d}^{e}\left(\operatorname{enc}_{d}^{e}\left(\left(q_{nt}(\beta)\right)_{\beta\in\mathcal Q^{\text{market}}}\right),\ \ln\eta_{nt},\ t/\eta_{nt}\right)
```

```math
\text{wt}_{nt}^{ey}>0,\qquad \sum_{y\in Y}\text{wt}_{nt}^{ey}=1,\qquad \text{sd}_{nt}^{ey}>0.1
```

**Symbols.**

- $e$, $`\mathcal E`$ (new): one seed, and the set of 5 seeds.
- $y$, $Y$ (new): one mixture component, and the set of 3 components of one
  seed's network.
- $d$: the current cutoff session. $n$, $t$: the stock and the calendar days to
  the expiry.
- $`\operatorname{enc}_{d}^{e}`$ (new): the encoder of seed $e$ in force at
  session $d$. It takes the market quantiles and returns 8 numbers, through one
  hidden layer of 64. It includes the standardization of its inputs.
- $`\operatorname{head}_{d}^{e}`$ (new): the head of seed $e$ in force at
  session $d$. It takes the encoder's 8 numbers and the two calendar numbers,
  through one hidden layer of 8, and returns 3 numbers per component. It
  includes the standardization of the two calendar numbers.
- $`q_{nt}(\beta)`$, $\beta$, $`\mathcal Q^{\text{market}}`$: the
  market-implied quantile at level $\beta$ and the set of 19 levels (stage 2).
- $`\ln\eta_{nt}`$, $`t/\eta_{nt}`$: the logarithm of the session count and the
  calendar days per session (stage 2).
- $`\text{wt}_{nt}^{ey}`$ (new): the weight that seed $e$ gives component $y$
  in the forecast for stock $n$ and expiry $t$.
- $`\text{loc}_{d}^{ey}`$ (new): the centre of component $y$ of seed $e$, in
  normalized-return units. It has no $n$ or $t$: it is a constant of the head,
  the same for every stock and expiry.
- $`\text{sd}_{nt}^{ey}`$ (new): the standard deviation, or width, of component
  $y$ of seed $e$ in that forecast, in normalized-return units.

**Indices and dimension.** One encoder and one head for every seed
$`e\in\mathcal E`$; each pair serves every stock and expiry. One weight and one
standard deviation for every seed $e$, component $`y\in Y`$, stock $`n\in N`$
and expiry $`t\in T`$; one centre for every seed and component. Each network has
(19 x 64 + 64) + (64 x 8 + 8) + (10 x 8 + 8) + (8 x 9 + 9) = 1,969 trained
weights, where 19 is $`\lvert\mathcal Q^{\text{market}}\rvert`$ and 9 is
$`3\lvert Y\rvert`$. Over the seeds that is 1,969 x
$`\lvert\mathcal E\rvert`$ = 9,845, plus 2 x 21 = 42 standardization constants.
The count is mine, from the layer sizes in the code. At an earlier origin $a$
the outputs read $`\text{wt}_{nt\mid a}^{ey}`$, $`\text{loc}_{a}^{ey}`$ and
$`\text{sd}_{nt\mid a}^{ey}`$.

**Why.**

- **Why several seeds.** Training starts from random numbers set by the seed,
  so two seeds give slightly different networks. Pooling five reduces the effect
  of one lucky or unlucky start. A0008 fixes the five seeds.
- **Why the centres are constants.** This is the recipe's "fixed location": the
  input can move weight between the centres and widen or narrow each curve, but
  it cannot move the centres. The choice is A0008's, restated here.
- **Why the weights sum to 1.** So that each seed's mixture is a probability
  distribution.
- **Why the width has a floor of 0.1.** The code adds it to every width, so no
  component can be narrower than that.
- **Why standardize the inputs.** The 21 inputs differ in size. Subtracting each
  one's training mean and dividing by its training spread puts them on one scale
  for training.
- **Why 64, 8 and 8.** The encoder's 64 and 8 are tuned values locked in A0008.
  The head's 8 is set in the recipe's fit template.

**Update (locked, A0008).** For every seed $`e\in\mathcal E`$:

```math
\left(\operatorname{enc}_{d'}^{e},\ \operatorname{head}_{d'}^{e}\right)\ \leftarrow\
\begin{cases}
\operatorname{fit}^{e}\left(\text{all mature history before }d'\right), & d'\text{ the first session of a quarter},\\
\left(\operatorname{enc}_{d}^{e},\ \operatorname{head}_{d}^{e}\right), & \text{otherwise}.
\end{cases}
```

Here $`d'`$ is the next cutoff session and $`\operatorname{fit}^{e}`$ is the
locked training procedure of A0008 run with seed $e$ (learning rate 0.0001,
dropout 0.11, weight decay 0.045, 6 epochs). It returns that seed's trained
encoder and head, with the centres $`\text{loc}_{d'}^{ey}`$ and the
standardization constants inside them. Mature history means forecasts whose
outcome had settled.

**Why each quarter, and why only mature history.** Retraining each quarter lets
the networks learn from the newest outcomes. Using only outcomes settled before
the quarter starts means no forecast in the quarter is trained on its own
future.

### Stage 4. Forecast CDF (agreed)

```math
\widehat\Psi_{nt}^{Z}(z\mid\mathcal F_d)=\frac{1}{\lvert\mathcal E\rvert}\sum_{e\in\mathcal E}\ \sum_{y\in Y}\text{wt}_{nt}^{ey}\ \Phi\left(\frac{z-\text{loc}_{d}^{ey}}{\text{sd}_{nt}^{ey}}\right),\qquad \widehat\Psi_{nt}^{Z}:\mathbb R\to[0,1]
```

In price coordinates, as agreed:

```math
\widehat\Psi_{nt}(p\mid\mathcal F_d)=\widehat\Psi_{nt}^{Z}\left(\frac{\ln(p/S_{n,d})}{\kappa_{nt}}\mid\mathcal F_d\right),\qquad p>0
```

**Symbols.**

- $n$, $t$, $d$: the stock, the calendar days to the expiry, and the origin.
- $`\mathcal F_d`$: the information available by the cutoff of session $d$.
- $z$: any value of the normalized return.
- $`\widehat\Psi_{nt}^{Z}(z\mid\mathcal F_d)`$: for stock $n$ and expiry $t$,
  the forecast probability, made with that information, that the normalized
  return $`Z_{nt}`$ of stage 1 is at or below $z$. The superscript $Z$ is a
  label for normalized-return units, not an index.
- $e$, $`\mathcal E`$: one seed and the set of 5 seeds. $y$, $Y$: one mixture
  component and the set of 3 components of a seed.
- $`\text{wt}_{nt}^{ey}`$, $`\text{loc}_{d}^{ey}`$, $`\text{sd}_{nt}^{ey}`$: the
  weight, centre and standard deviation of component $y$ of seed $e$ (stage 3).
- $\Phi$: the standard normal CDF.
- $p$: any price level, in dollars per share.
- $`\widehat\Psi_{nt}(p\mid\mathcal F_d)`$: the same forecast in price terms:
  the probability that the expiry price $`p_{nt}`$ is at or below $p$.
- $`S_{n,d}`$, $`\kappa_{nt}`$: the closing price at the origin and the scale
  of stage 1.

**Indices and dimension.** One CDF for every stock $`n\in N`$ and every expiry
$`t\in T`$ forecast at $d$. It has
$`\lvert\mathcal E\rvert\,\lvert Y\rvert`$ = 5 x 3 = 15 components, so a
forecast is $`3\lvert\mathcal E\rvert\,\lvert Y\rvert`$ = 45 numbers: 15
weights, 15 centres and 15 standard deviations. Those are saved, with each
weight already divided by the number of seeds, and so is the curve at 13 values
of $z$ from -3 to 3 (25 on the fine grid). At an earlier origin $a$ the same
two lines hold with $`\mathcal F_a`$, $`S_{n,a}`$, $`\kappa_{nt\mid a}`$ and the
stage 3 outputs of origin $a$.

**Why.**

- **Why a mixture.** One normal curve cannot be lopsided or fat-tailed. A
  weighted sum of several can, and it stays a smooth, valid CDF that can be
  evaluated exactly at any point.
- **Why the average over seeds.** Averaging the seeds' CDFs with equal weight is
  the linear pool that A0008 locks. An average of mixtures is again a mixture.
- **Why two coordinate systems.** The network works in normalized units, but
  payoffs need dollars. The second line converts: a price $p$ corresponds to the
  normalized return $`\ln(p/S_{n,d})/\kappa_{nt}`$.
- **Why 13 saved points.** They are the grid on which A0002 scores forecasts.
  The PIT of stage 5 needs the curve at the exact realized price, which the 45
  numbers give.

**No update.** A forecast is evaluated once, at its origin, and kept as issued.

### Stage 5. Raw PIT (agreed)

```math
\begin{aligned}
E_{n\rho\mid a}&:=\text{the expiry that holds rank }\rho\text{ for stock }n\text{ at origin }a,\\
t_{n\rho\mid a}&:=\text{calendar days from }a\text{ to }E_{n\rho\mid a},\\
\eta_{n\rho\mid a}&:=\text{trading sessions from }a\text{ to the session whose close is the expiry price},\\
d^{\star}_{n\rho\mid a}&:=\min\lbrace\, d\ge a+\eta_{n\rho\mid a} :\ \text{the expiry-price record is available by cutoff }d \,\rbrace,\\
u_{n\rho\mid a}&:=\widehat\Psi_{nt}\left(S_{n,\,a+\eta_{n\rho\mid a}}\mid\mathcal F_a\right)\in[0,1],\qquad t=t_{n\rho\mid a}.
\end{aligned}
```

**Symbols.**

- $n$, $\rho$, $a$: the stock, the original expiry rank, and the origin session
  of a past forecast. In every subscript of this stage they appear in that
  order, with the origin after the bar.
- $`E_{n\rho\mid a}`$ (proposed in step 1): the expiry date of that forecast.
- $`t_{n\rho\mid a}`$, $`\eta_{n\rho\mid a}`$: its calendar days and its trading
  sessions to expiry, counted at its origin.
- $`d^{\star}_{n\rho\mid a}`$ (proposed in step 1): the earliest usable cutoff,
  the first cutoff session at which the forecast's outcome may be used. The
  star is a mark that names this cutoff, not an index.
- $`S_{n,\,a+\eta_{n\rho\mid a}}`$: the closing price of stock $n$ at the
  session reached $`\eta_{n\rho\mid a}`$ sessions after $a$, which is the
  expiry-price session. It is the realized expiry price.
- $`\mathcal F_a`$: the information available by the cutoff of session $a$.
- $`\widehat\Psi_{nt}(\cdot\mid\mathcal F_a)`$: the forecast CDF of stage 4 in
  price terms, exactly as issued at origin $a$.
- $`u_{n\rho\mid a}`$: the raw PIT of that forecast, a number from 0 to 1. PIT
  is short for probability integral transform.

**Indices and dimension.** One of each for every stock $`n\in N`$, every rank
$`\rho\in\mathcal R_n`$ and every origin $`a<d`$ at which that cell received a
forecast. The raw PIT exists once the outcome has settled; it is one number,
shared by all the contracts of that stock and expiry.

**Why.**

- **Why read the CDF at the realized price.** It turns each outcome into a
  score from 0 to 1: the probability the forecast gave to a price at or below
  the one that occurred. If the forecasts were perfectly calibrated, these
  scores would be spread evenly from 0 to 1; bunching shows where they are
  wrong. It also puts every stock and horizon on one scale.
- **Why the forecast as issued.** The score must judge what was actually
  forecast at $a$, with that day's price, scale and model version.
- **Why an earliest usable cutoff.** An outcome may be used only once it has
  happened and its record was on hand. Otherwise the history would hold
  information we did not have at the time.
- **Why the rank is fixed at issue.** A0006 groups forecasts by the rank the
  expiry had when the forecast was made, so a group always holds forecasts of a
  similar horizon.

**Update.** Created once, at the first cutoff $`d'`$ with
$`d^{\star}_{n\rho\mid a}\le d'`$. Never revised.

### Stage 6. Counters (agreed)

```math
\begin{aligned}
M_{n,\rho,d} &:= \#\lbrace a<d :\ d^{\star}_{n\rho\mid a}\le d \rbrace\in\mathbb Z_{\ge 0},\\
A_{n,\rho,d}(\beta) &:= \#\lbrace a<d :\ d^{\star}_{n\rho\mid a}\le d,\ u_{n\rho\mid a}\le\beta \rbrace\in\mathbb Z_{\ge 0},\qquad \beta\in\mathcal Q.
\end{aligned}
```

**Symbols.**

- $n$, $\rho$, $d$: the stock and rank of the cell, and the current cutoff.
- $a$: the origin of a past forecast of that cell.
- $`d^{\star}_{n\rho\mid a}`$: that forecast's earliest usable cutoff (stage 5).
- $`u_{n\rho\mid a}`$: its raw PIT (stage 5).
- $\beta$, $`\mathcal Q`$: one calibration threshold and the configured set of
  11.
- $`M_{n,\rho,d}`$: for the cell of stock $n$ and rank $\rho$, the number of
  its forecasts whose outcome is usable by cutoff $d$.
- $`A_{n,\rho,d}(\beta)`$: for the same cell and cutoff, the number of those
  whose raw PIT is at or below the threshold $\beta$.

**Indices and dimension.** One $M$ for every cell $`(n,\rho)\in\mathcal C`$ and
one $A$ for every cell and every threshold $`\beta\in\mathcal Q`$, at each
cutoff $d$. That is $`1+\lvert\mathcal Q\rvert`$ = 12 integers per cell and
$`(1+\lvert\mathcal Q\rvert)\,\lvert\mathcal C\rvert`$, at most 15,936, in all.

**Why.**

- **Why counts, not every score.** The correction of stage 7 needs only, for
  each threshold, the share of scores at or below it. Twelve integers per cell
  hold that. The storage never grows and an update is an addition.
- **Why per cell.** Forecast errors differ by stock and by horizon. A0006 sets
  the cell as the group that is calibrated together.
- **What the counts cannot do.** They do not record which scores occurred
  together, so they cannot supply the dependence between cells. Stage 9 keeps
  that record.

**Update.** For every cell $`(n,\rho)\in\mathcal C`$:

```math
\begin{aligned}
M_{n,\rho,d'} &\leftarrow M_{n,\rho,d}+\#\lbrace a :\ d<d^{\star}_{n\rho\mid a}\le d' \rbrace,\\
A_{n,\rho,d'}(\beta) &\leftarrow A_{n,\rho,d}(\beta)+\#\lbrace a :\ d<d^{\star}_{n\rho\mid a}\le d',\ u_{n\rho\mid a}\le\beta \rbrace,\qquad \beta\in\mathcal Q.
\end{aligned}
```

Here $`d'`$ is the next cutoff session, and the condition
$`d<d^{\star}_{n\rho\mid a}\le d'`$ picks the forecasts whose outcome became
usable since $d$. Both counters start at zero. Only those forecasts are read; no
PIT history is scanned again.

### Stage 7. Correction map (agreed working map)

```math
\widehat\Gamma_{n,\rho,d}(\beta):=\frac{A_{n,\rho,d}(\beta)+\zeta\beta}{M_{n,\rho,d}+\zeta}\quad\text{for }\beta\in\mathcal Q,\qquad
\widehat\Gamma_{n,\rho,d}(0):=0,\qquad \widehat\Gamma_{n,\rho,d}(1):=1
```

Between two neighbouring points the map is a straight line:

```math
\widehat\Gamma_{n,\rho,d}(u):=\frac{\beta_{+}-u}{\beta_{+}-\beta_{-}}\ \widehat\Gamma_{n,\rho,d}(\beta_{-})+\frac{u-\beta_{-}}{\beta_{+}-\beta_{-}}\ \widehat\Gamma_{n,\rho,d}(\beta_{+}),\qquad \beta_{-}\le u\le\beta_{+}
```

**Symbols.**

- $n$, $\rho$, $d$: the stock and rank of the cell, and the current cutoff.
- $\beta$, $`\mathcal Q`$: one calibration threshold and the configured set of
  11.
- $`M_{n,\rho,d}`$, $`A_{n,\rho,d}(\beta)`$: the cell's counters of stage 6.
- $\zeta$: the strength, one positive number shared by all cells. A larger value
  pulls the map toward no correction. Its value is not chosen yet.
- $`\widehat\Gamma_{n,\rho,d}`$: the correction map of the cell of stock $n$ and
  rank $\rho$ at cutoff $d$. It takes a probability from 0 to 1 and returns a
  corrected probability from 0 to 1.
- $u$: without subscripts, any raw PIT value from 0 to 1.
- $`\beta_{-}`$, $`\beta_{+}`$: the neighbouring points just below and just
  above $u$, among the 11 thresholds and the two ends 0 and 1. The signs are
  labels, not indices.

**Indices and dimension.** One map for every cell $`(n,\rho)\in\mathcal C`$ at
each cutoff $d$. It is fixed by its values at the thresholds:
$`\lvert\mathcal Q\rvert`$ = 11 numbers per cell and
$`\lvert\mathcal Q\rvert\,\lvert\mathcal C\rvert`$, at most 14,608, per cutoff.

**Why.**

- **Why this formula.** Write $A$ and $M$ for the two counters above. The share
  of past scores at or below $\beta$ is $A/M$. If the forecasts were right,
  that share would be $\beta$ itself. The formula is a weighted average of the
  two, shown here for $M$ above zero:

```math
\frac{A+\zeta\beta}{M+\zeta}=\frac{M}{M+\zeta}\cdot\frac{A}{M}+\frac{\zeta}{M+\zeta}\cdot\beta
```

- **What that gives.** With no outcomes the map returns $\beta$, which is no
  correction. As outcomes accumulate it moves toward the observed share.
- **Why the strength.** It sets how many outcomes it takes before the data
  outweigh "no correction". It is not a count of independent observations.
- **Why fixed ends and straight lines.** They make the map continuous and
  strictly increasing from (0, 0) to (1, 1). The corrected forecast is then
  still a valid CDF, and the map can be inverted, which stage 11 needs.
- **What 11 thresholds can fix.** They split the scores into twelve bands. The
  map corrects how much probability each band holds, not the shape inside a
  band, so it cannot repair a wrong far tail.

**Update.** After the counters, for every cell $`(n,\rho)\in\mathcal C`$:

```math
\widehat\Gamma_{n,\rho,d'}(\beta)\ \leftarrow\ \frac{A_{n,\rho,d'}(\beta)+\zeta\beta}{M_{n,\rho,d'}+\zeta},\qquad \beta\in\mathcal Q
```

Here $`d'`$ is the next cutoff session and the counters on the right are the
updated ones of stage 6. The map issued at $d$ is kept, not overwritten, until
every forecast issued with it has settled.

**Why keep the issued map.** A forecast must be corrected with what was known at
its own origin. Using a later map would let later outcomes into an earlier
score.

### Stage 8. Corrected CDF and corrected PIT

The composition was agreed; the symbol $w$ was proposed in step 1.

```math
\begin{aligned}
p &\mapsto \widehat\Gamma_{n,\rho,d}\left(\widehat\Psi_{nt}(p\mid\mathcal F_d)\right)\in[0,1], &&t=t_{n\rho\mid d},\\
w_{n\rho\mid a} &:= \widehat\Gamma_{n,\rho,a}\left(u_{n\rho\mid a}\right)\in[0,1].
\end{aligned}
```

**Symbols.**

- $n$, $\rho$: the stock and rank of the cell. $d$: the current cutoff and
  origin. $a$: the origin of a past forecast.
- $p$: any price level, in dollars per share.
- $`t_{n\rho\mid d}`$: the calendar days from $d$ to the expiry of rank $\rho$.
- $`\widehat\Psi_{nt}(p\mid\mathcal F_d)`$: the forecast CDF of stage 4 in
  price terms, issued at $d$ with the information $`\mathcal F_d`$.
- $`\widehat\Gamma_{n,\rho,d}`$, $`\widehat\Gamma_{n,\rho,a}`$: the cell's
  correction map of stage 7, as issued at $d$ and as issued at $a$.
- $`u_{n\rho\mid a}`$: the raw PIT of the past forecast (stage 5).
- $`w_{n\rho\mid a}`$ (proposed in step 1): the corrected PIT of the forecast
  for stock $n$ and rank $\rho$ issued at origin $a$, a number from 0 to 1.

**Indices and dimension.** One corrected CDF for every cell
$`(n,\rho)\in\mathcal C`$ forecast at $d$, and one corrected PIT for every cell
and every origin $`a<d`$ whose outcome has settled.

**Why.**

- **Why compose the two.** The base forecast says the price will be at or below
  $p$ with some probability. The map replaces that probability with the share
  of past cases in which the outcome did fall at or below a level given the same
  probability. The first line is the forecast with its measured miscalibration
  removed.
- **Why the corrected PIT.** Scenarios are drawn through the corrected CDFs, so
  dependence must be measured on scores made the same way.

**Update.** $`w_{n\rho\mid a}`$ is computed once, at settlement, with the stored
map of origin $a$.

### Stage 9. Joint observation and joint record (proposed in step 1; one new symbol)

```math
\begin{aligned}
\mathcal C_a &:= \lbrace (n,\rho)\in\mathcal C :\ \text{a valid forecast was issued at }a \rbrace,\\
\mathcal O_{a,d} &:= \lbrace (n,\rho)\in\mathcal C_a :\ d^{\star}_{n\rho\mid a}\le d \rbrace,\\
\mathbf w_a &:= \left(w_{n\rho\mid a}\right)_{(n,\rho)\in\mathcal C_a}\in[0,1]^{\lvert\mathcal C_a\rvert},\\
\mathcal H_d &:= \lbrace \left(a,\ n,\ \rho,\ u_{n\rho\mid a},\ w_{n\rho\mid a}\right) :\ a<d,\ (n,\rho)\in\mathcal O_{a,d} \rbrace .
\end{aligned}
```

**Symbols.**

- $a$, $d$: an origin session and the current cutoff session.
- $`(n,\rho)`$, $`\mathcal C`$: a cell, one stock with one rank, and the set of
  all cells.
- $`\mathcal C_a`$ (proposed in step 1): the cells that received a valid
  forecast at origin $a$. The subscript is the origin.
- $`d^{\star}_{n\rho\mid a}`$: the earliest usable cutoff of the forecast for
  stock $n$ and rank $\rho$ issued at origin $a$ (stage 5). The star is a mark,
  not an index.
- $`\mathcal O_{a,d}`$ (proposed in step 1): the cells of origin $a$ whose
  outcome is usable by cutoff $d$. The subscripts are the origin and the
  cutoff.
- $`u_{n\rho\mid a}`$, $`w_{n\rho\mid a}`$: the raw and the corrected PIT.
- $`\mathbf w_a`$: the joint observation of origin $a$: the list of its
  corrected PITs, one per cell in $`\mathcal C_a`$. At cutoff $d$ only the
  entries in $`\mathcal O_{a,d}`$ are known.
- $`\mathcal H_d`$ (new): the joint record at cutoff $d$: every settled row.

**Indices and dimension.** One joint observation for every origin $`a<d`$, with
$`\lvert\mathcal C_a\rvert`$ entries, at most $`\lvert\mathcal C\rvert`$ =
1,328. The joint record has one row for every origin $`a<d`$ and every cell in
$`\mathcal O_{a,d}`$. The 2020Q1 forecast receipt records 69,687 rows over 62
sessions, about 1,120 per session, so every session is an origin. Over the
roughly 1,070 sessions of open forecasts that is about 1.2 million rows, and
never more than about 1.4 million.

**Why.**

- **Why one observation per origin.** A solve holds forecasts all made at the
  same cutoff. Dependence must therefore be measured between scores of
  forecasts made on the same day.
- **Why cells, not calendar dates, as coordinates.** A cell means the same thing
  at every origin, the same stock at the same place in line, so its scores can
  be compared across origins.
- **Why keep every row.** Dependence needs to know which scores occurred
  together. The counters of stage 6 discard that.

**Update.** For every origin $`a<d'`$:

```math
\begin{aligned}
\mathcal O_{a,d'} &\leftarrow \mathcal O_{a,d}\cup\lbrace (n,\rho)\in\mathcal C_a :\ d<d^{\star}_{n\rho\mid a}\le d' \rbrace,\\
\mathcal H_{d'} &\leftarrow \mathcal H_d\cup\lbrace \left(a,\ n,\ \rho,\ u_{n\rho\mid a},\ w_{n\rho\mid a}\right) :\ d<d^{\star}_{n\rho\mid a}\le d' \rbrace.
\end{aligned}
```

Here $`d'`$ is the next cutoff session, and the condition picks the forecasts
whose outcome became usable since $d$. Rows are appended, never rewritten,
because a settled score does not change.

### Stage 10. Copula (step 2, not yet defined)

Only the object and its size can be stated now.

```math
\operatorname{Cop}_d:=\text{the joint CDF of }\left(w_{n\rho\mid d}\right)_{(n,\rho)\in\mathcal C_d},\qquad
\operatorname{Cop}_d:[0,1]^{\lvert\mathcal C_d\rvert}\to[0,1]
```

**Symbols.**

- $d$: the current cutoff and origin.
- $`(n,\rho)`$: a cell, one stock $n$ with one original expiry rank $\rho$.
- $`\mathcal C_d`$: the cells that receive a valid forecast at $d$ (stage 9,
  with $d$ as the origin).
- $`w_{n\rho\mid d}`$: the corrected PIT that the forecast issued now for cell
  $`(n,\rho)`$ will have once it settles. It is not known at $d$.
- $`\operatorname{Cop}_d`$ (new, a placeholder name because $C$ is the
  premium): the copula at cutoff $d$; the subscript is the cutoff. A joint CDF
  takes one level from 0 to 1 for each
  cell and returns the forecast probability that every cell's corrected PIT is
  at or below its own level.

**Indices and dimension.** One copula at each cutoff $d$. Its dimension is
$`\lvert\mathcal C_d\rvert`$, at most $`\lvert\mathcal C\rvert`$ = 1,328. By
definition each entry alone is spread evenly from 0 to 1. If a
correlation-matrix family is chosen, its state is one symmetric matrix of that
size: at most 881,128 distinct pairs, against about 1,070 overlapping origins.

**Why.**

- **Why a copula.** Stage 8 gives each cell its own corrected CDF but says
  nothing about cells moving together. The copula holds only that linkage.
  Keeping the two apart lets each be estimated and checked on its own.
- **Why it is needed.** Portfolio tail risk depends on many positions losing
  together. Without the linkage that risk cannot be measured.

**Update: not defined.** It belongs to step 2. The counters cannot supply it.

### Stage 11. Joint scenarios (open; shape only)

```math
\begin{aligned}
\tilde{\mathbf w}(\omega)&:=\left(\tilde w_{n\rho}(\omega)\right)_{(n,\rho)\in\mathcal C_d}\sim\operatorname{Cop}_d, &&\omega\in\Omega,\\
\tilde p_{nt}(\omega)&:=\widehat\Psi_{nt}^{-1}\left(\widehat\Gamma_{n,\rho,d}^{-1}\left(\tilde w_{n\rho}(\omega)\right)\mid\mathcal F_d\right), &&t=t_{n\rho\mid d},\\
V_{knt}^{ic}(\omega)&:=m\left(\tilde p_{nt}(\omega)-s_{knt}^{ic}\right)\mathbf 1\lbrace \tilde p_{nt}(\omega)\ge s_{knt}^{ic}\rbrace,\\
V_{knt}^{ip}(\omega)&:=m\left(s_{knt}^{ip}-\tilde p_{nt}(\omega)\right)\mathbf 1\lbrace \tilde p_{nt}(\omega)\le s_{knt}^{ip}\rbrace.
\end{aligned}
```

**Symbols.**

- $d$: the current cutoff and origin. $`\mathcal F_d`$: the information
  available by its cutoff.
- $\omega$, $\Omega$ (new): one scenario and the set of scenarios.
- $`(n,\rho)`$, $`\mathcal C_d`$: a cell, and the cells forecast at $d$.
- $`\operatorname{Cop}_d`$: the copula of stage 10. The mark $\sim$ reads "is
  drawn at random from".
- $`\tilde w_{n\rho}(\omega)`$, $`\tilde{\mathbf w}(\omega)`$ (new): the
  simulated corrected PIT of cell $`(n,\rho)`$ in scenario $\omega$, and the
  list of them over the cells.
- $`\widehat\Gamma_{n,\rho,d}^{-1}`$: the inverse of the correction map of
  stage 7 for the cell of stock $n$ and rank $\rho$ at cutoff $d$. The
  superscript $-1$ marks the inverse: it returns the raw probability that the
  map sends to a stated corrected probability.
- $`\widehat\Psi_{nt}^{-1}(\cdot\mid\mathcal F_d)`$: the inverse of the forecast
  CDF of stage 4 in price terms: it returns the price at which that CDF equals a
  stated probability.
- $`t_{n\rho\mid d}`$: the calendar days from $d$ to the expiry of rank $\rho$.
- $`\tilde p_{nt}(\omega)`$ (new): the simulated expiry price of stock $n$ at
  that expiry in scenario $\omega$.
- $i$, $k$: the transaction side, in $I$, and the contract, in $`K_{nt}^{j}`$.
  The superscripts $ic$ and $ip$ fix the option type $j$ to call and to put.
- $m$: the contract multiplier, 100 shares.
- $`s_{knt}^{ic}`$, $`s_{knt}^{ip}`$: the strike of that call or put, in dollars
  per share.
- $`\mathbf 1\lbrace\cdot\rbrace`$: 1 if the condition inside is true, else 0.
- $`V_{knt}^{ic}(\omega)`$, $`V_{knt}^{ip}(\omega)`$ (new): the agreed expiry
  payoff of one call or put, read at the scenario's price instead of a known
  price.

**Indices and dimension.** One simulated score and one simulated price for
every scenario $`\omega\in\Omega`$ and every cell $`(n,\rho)\in\mathcal C_d`$:
$`\lvert\Omega\rvert\,\lvert\mathcal C_d\rvert`$ prices, at most 1,328 per
scenario. One payoff for every scenario, side $`i\in I`$, type $`j\in J`$,
contract $`k\in K_{nt}^{j}`$, stock and expiry. The number of scenarios is not
chosen.

**Why.**

- **Why invert.** A draw is a score from 0 to 1, but payoffs need prices.
  Inverting the corrected CDF returns the price whose corrected probability
  equals the score. Each cell's simulated prices then follow its own corrected
  forecast, while the draw carries the linkage between cells.
- **Why the inverses exist.** The map and the mixture CDF are strictly
  increasing, so for a score strictly between 0 and 1 there is exactly one
  price.
- **Why the payoff lines.** They are the agreed call and put payoffs with the
  scenario's price in place of the known price. This is where the contract
  indices enter; nothing else about them changes.

**No update.** Scenarios are drawn again at every solve, because the forecasts,
the maps and the cells change with the cutoff.

### Order of one update

Names in the block: `chi`, `eta`, `kappa`, `zeta`, `beta` and `rho` are the
Greek letters above; `d_star` is the earliest usable cutoff; `Psi_hat` and
`Gamma_hat` are the forecast CDF and the correction map; `F[a]` is the
information at origin `a`; `S[n, session]` is the closing price of stock `n` at
that session; `M` and `A` are the counters; `u` and `w` are the raw and the
corrected PIT; `H` is the joint record; `enc[e]` and `head[e]` are the encoder
and head of seed `e`; `q[n,t](beta)` is the market-implied quantile; `wt`,
`loc` and `sd` are the mixture weights, centres and standard deviations,
indexed by seed `e` and component `y`; `<-` is the assignment.

```text
UPDATE from cutoff d to the next cutoff d'

1. Observe returns, expiry-price records and option quotes through d'.

2. Preprocess. FOR each stock n: chi[n,d'] <- 22-session RMS.
               FOR each forecast (n,t): recount eta[n,t]; kappa[n,t] <- scale.

3. Settle. FOR each (a, n, rho) with d < d_star[n,rho|a] <= d':
       u[n,rho|a] <- Psi_hat[n,t](S[n, a + eta[n,rho|a]] | F[a])    (Appendix C)
       w[n,rho|a] <- Gamma_hat[n,rho,a](u[n,rho|a])                 (map of origin a)
       APPEND row (a, n, rho, u, w, and the step 1 fields) to H.

4. Count. FOR each cell (n,rho), each threshold beta:
       M[n,rho,d']       <- M[n,rho,d]       + new rows of the cell
       A[n,rho,d'](beta) <- A[n,rho,d](beta) + new rows with u <= beta

5. Map. FOR each cell (n,rho), each threshold beta:
       Gamma_hat[n,rho,d'](beta) <-
           (A[n,rho,d'](beta) + zeta * beta) / (M[n,rho,d'] + zeta)
       Store it as version d'. Keep earlier versions that open forecasts use.

6. At a quarter start only. FOR each seed e:
       (enc[e], head[e]) <- fit[e](mature history)       (locked, A0008)

7. Forecast. FOR each cell (n,rho) with valid inputs at d', t its calendar days:
       Build the inputs q[n,t](beta), ln eta[n,t], t / eta[n,t].
       FOR each seed e, component y: evaluate wt[e,y,n,t], loc[e,y], sd[e,y,n,t].
       Save Psi_hat[n,t](. | F[d']) with map version d'.

8. Copula state: step 2, not defined.

9. Solve with the forecasts and maps of d'.
```

**Why this order.** Step 3 comes before steps 4 and 5 so that every new score is
made with the map of its own origin before its outcome changes any counter.
Step 7 comes after step 5 so that each new forecast is issued with the newest
map.

### Sizes at a glance

| Stage | Object | One for every | Size in general | Size now |
|---|---|---|---|---|
| 1 | Return window | stock | 22 per stock | 3,652 |
| 2 | Model input | stock and expiry | $`\lvert\mathcal Q^{\text{market}}\rvert+2`$ numbers | 21 |
| 3 | Network | seed | 1,969 weights per seed | 9,845 |
| 4 | Forecast CDF | stock and expiry | $`3\lvert\mathcal E\rvert\,\lvert Y\rvert`$ numbers | 45 |
| 5 | Raw PIT | cell and settled origin | 1 number | 1 |
| 6 | Counters | cell | $`1+\lvert\mathcal Q\rvert`$ integers | 12 per cell, at most 15,936 |
| 7 | Correction map | cell and cutoff | $`\lvert\mathcal Q\rvert`$ numbers | 11 per cell, at most 14,608 |
| 8 | Corrected PIT | cell and settled origin | 1 number | 1 |
| 9 | Joint record | settled cell and origin | 1 row | about 1.2 million rows |
| 10 | Copula | cutoff | dimension $`\lvert\mathcal C_d\rvert`$ | at most 1,328 |
| 11 | Scenario prices | scenario and cell | $`\lvert\Omega\rvert\,\lvert\mathcal C_d\rvert`$ prices | not chosen |

Carried to the next cutoff: the return window (every session), the networks
(refitted each quarter), the counters (incremented), the correction maps
(recomputed, old versions kept while in use) and the joint record (appended).
Rebuilt each time: the model input, the forecast CDF and the scenarios. The raw
and corrected PIT are created once, at settlement.

To confirm, with (a) recommended in each:

1. Names and indices of the network's parts: (a) roman words with the seed and
   the component as superscripts, as in $`\text{wt}_{nt}^{ey}`$, following bid
   and ask and the $`(\cdot)_{knt}^{ij}`$ placement; (b) single letters of your
   choice.
2. Update clock: (a) counters, maps and the joint record update at every
   session even when the solve is weekly, as the agreed daily variance update
   already does; (b) they update only at solves.
3. Issued maps: (a) keep each version until its forecasts settle, since older
   versions can be rebuilt from the joint record; (b) keep every version.
4. New sets and symbols here ($`\mathcal Q^{\text{market}}`$, $`\mathcal E`$,
   $Y$, $\Omega$ with their members $e$, $y$ and $\omega$; the market quantile
   $`q_{nt}(\beta)`$ and the names used to build it (mid, fwd, cq, iso and
   $`\widehat G_{nt}`$); $`\mathcal C`$, $`\mathcal H_d`$, $`d'`$, bold for a
   list, the tilde and the placeholder Cop): (a) accept; (b) rename.
5. Scenario payoff lines of stage 11: (a) keep them as the shape of the agreed
   payoff under a scenario; (b) leave them out until joint scenarios are agreed.

Step 1's four choices above stay open.

## 25 November formulation checkpoint

Owner: Russell; author: GPT-6. The owner requested “wrap, merge, and purge”.
This is an editorial handoff of previously agreed definitions, not a new model,
execution approval, or change to locked Path rows. Base:
`0d90f01edd659ed32b6b88aa08a3682fbbb4f6fc` (remote main).
Allowed repository changes: this memo, the child re-entry note, its archive, and
the index_options row of the root re-entry index. No code, tests, configs, Path,
ledgers, protected outcomes, or other lanes are changed.

Canonical document: [DSKit MIO Formulation Working Draft](https://chatgpt.com/space/page_d967bc9d4dac81919850190243a4439d),
saved sequence 22, 292 blocks. Sequence 21 contains the agreed definitions;
sequence 22 changes only the restart pointer and labels the old cadence status
as historical. Both replacement paragraphs were read back exactly. Rendered UI
was not rechecked. Attached reading copies predate the latest definitions.

Agreed components in the main body and appendices:

- Daily observed log returns, conditional mean, mean-zero innovations and
  conditional variance. A zero expected innovation does not mean zero variance;
  the appendix derives this and separates true quantities from estimates.
- Daily observation/cutoff d, historical origin a, calendar DTE t, session count
  eta, future risk-session offset g and original expiry rank rho. Tau is retired;
  v remains occupied. Weekly solving does not redefine the daily return input.
- Current frozen forecast preprocessing and the smooth mixture CDF, then one
  raw PIT per stock-expiry-origin, shared by contracts using that forecast.
  Only mature outcomes actually available by cutoff enter the update.
- Expanding per-stock/per-original-rank total and threshold counts. The threshold
  grid is a configuration input; its agreed default spans 0.01 to 0.99 with
  11 thresholds. Store counters incrementally, not an unlimited PIT vector.
- A corrected CDF obtained by composing the base CDF with a monotone endpoint-
  anchored map. Historical corrected PITs use the map issued with the forecast,
  never today's map. Keep raw and corrected assessment streams separate.
- A working map with knots (A + zeta * beta)/(M + zeta), linearly interpolated
  between thresholds and endpoints. Zeta is one configurable positive strength
  shared across groups; it is not an independent-observation count.
- Chronological warmup, tuning and later evaluation. Compare strength candidates
  on the same eligible mature cohort and A0002's primary proper score; freeze
  the chosen strength and update policy before later evaluation. Counter updates
  may continue causally. Selection is not proof of improvement over identity.

Open before implementation: numerical strengths/dates, aggregation weights,
minimum evidence, uncertainty under overlapping outcomes, recent monitoring,
pass/fail tolerances, promotion/fallback actions and corrected-stream counter
notation. EWMA initialization uses prior dates; its numerical configuration and
mean estimator remain open. No parameters were selected by data in this session.
The working calibration family is not an empirically validated winner.

Next: define a joint observation's origin, target-date and cutoff alignment,
missing/maturing dimensions and original expiry-rank identity. Then propose the
copula and compact joint-state retention. Marginal threshold counts cannot
recover joint dependence. Risk-checkpoint forecasts, IV evolution, scenario
wealth/cash, CVaR constraints and feasible dynamic hedging remain downstream.
Each new definition must be shown mathematically for owner confirmation.

The strategy is ongoing; daily/weekly solving and individual contracts up to
60 calendar DTE are different clocks. The cadence study remains separately
owned. The earlier memo below records its 24 November checkpoint only; inspect
its latest receipts before using those counts or launching a retry.

Research completed, preserved outside Git:

- `/home/russell/reports/MIO recalibration fitting methods.md` (synthesis).
- `/home/russell/research_notes/MIO recalibration fitting methods/`:
  `methods.md`, `validation.md`, `streaming.md` (source-backed assessments).
- `/home/russell/mio-formulation-view-20261125/` (earlier reading copies).

The report compares empirical monotone recalibration, stabilization, alternatives
and streaming limits. It distinguishes marginal calibration from copula fitting.
The frozen fixed_loc linear pool does not already apply this PIT correction;
the existing market-calibration helper concerns the market baseline. No helper
was changed or wired into the predictive/MIO process here.

Path A0002 controls scoring; A0003 controls windows; A0006 controls asset/expiry
rank; A0008/A0009 freeze forecast recipe/universe; A0010 remains unchanged.
2019 and 2020–2023 have prior model-selection exposure, so warmup/development
validation there is not a newly clean test. Protected 2024+ outcomes were not
read, and this handoff does not authorize opening them.

## 25 November verification and closure

Acceptance: retain the agreed draft and research, record an unambiguous restart,
archive the replaced lane note verbatim, preserve unrelated history and remove
only this task's clean, merged worktree/branch. This is pure editorial scope;
under skeptic-review.md it receives accuracy/link and documentation checks,
not production-code review. No behavioral contracts or formulas changed at wrap.
Verification: `PYTHONPATH=. /home/russell/dskit/.venv/bin/python -m pytest
 tests/docs/test_budgets.py -q` passed all 4 tests (0.09 s); `git diff --check`
passed. Exact archive preservation, historical memo preservation, local Markdown
links and the four-file allowed scope were checked. The child note is 40 lines.
Page sequence 22 readback matched both edits and retained 292 blocks. No full
suite, model fit, replay, protected-data read or UI-render check was performed.
Final publication and task-only worktree removal are verified in the Git/tool
receipts for this wrap; no empirical result is implied by these checks.

Local main began at `aa1d73fd94d799c3fececd54a047eb8331a83869`, 21 commits ahead of
remote main. Publish only this isolated handoff, then integrate locally without
publishing unrelated commits. Preserve the cadence checkout and other sessions.
Durable research and reading copies remain; purge targets only the temporary
wrap worktree and its contained local branch after successful publication.

## Historical checkpoint: 24 November 2026

The remainder preserves the earlier handoff verbatim except its top heading.
Its tau notation and current-status wording are superseded by the checkpoint
above; its frozen cadence identities and receipts remain useful references.

## TL;DR

Continue the mathematical MIO from cash and holdings, then define portfolio risk
and hedging. The corrected daily/weekly experiment is unfinished: zero of six
arms completed. Disk headroom recovered; the prepared retry still needs RAM admission.

## Working document and agreed notation

Our [MIO working draft](https://chatgpt.com/space/page_d967bc9d4dac81919850190243a4439d)
has a Resume here section. It is distinct from the older implemented Kelly memo.
Keep replies brief and restate each agreed addition mathematically.

At a fixed decision date, the agreed deterministic foundation is

$$
i\in\{b,s\},\quad j\in\{c,p\},\quad n\in N,\quad t\in T,\quad k\in K^j_{nt},
\qquad x^{ij}_{knt}\in\mathbb Z_{\ge0},\qquad \mathcal P=(B,\{h^{ij}_{knt}\}).
$$

x denotes current trades; h and B are known holdings and cash. The research
baseline uses m=100, F=$0.65 per executed contract, C for buys=100 times ask,
and C for sells=100 times bid. Per-contract expiry profits are

$$
\pi^{bj}_{knt}=V^{bj}_{knt}-C^{bj}_{knt}-F^{bj}_{knt},\qquad
\pi^{sj}_{knt}=C^{sj}_{knt}-V^{sj}_{knt}-F^{sj}_{knt}.
$$

For one immutable expiry E and decision dates separated by calendar days,

$$
t=E-\tau,\qquad \Delta=\tau'-\tau,\qquad t'=t-\Delta.
$$

Net actual filled trades against previous holdings, then carry surviving contracts
to t'. Keep entry premiums/fees as history; refresh current quotes. Expiry payoff
V differs from resale premium C. A contract's payoff formula stays fixed, but its
forecast expectation can change. Deterministic V stays fixed only with the same
expiry price and terms. Exercise/assignment needs its own cash/stock bookkeeping.

Still open: cash-flow and portfolio-value equations; joint asset scenarios,
VaR/CVaR or other tail-loss control; concentration/correlation; hedge actions and
frequency; the final growth objective. No production risk architecture or daily
cadence was accepted in this session. Resume these choices step by step.

## Empirical contract and status

Owner authorized daily-versus-weekly evidence on 2026-11-24. The frozen comparison
uses A0008 saved fixed_loc forecasts (five seeds), A0009's 166 names, 1–60 DTE,
$10,000 initial cash, no deposits, next-session EOD bid/ask fills and the recorded
filled-account risk policy. It holds positions to expiry, with no early exits or
hedge trades. This direct research runner is separate from A0010 and is not an
official sealed MIOStudyContract packet execution.

Entries: full 2020Q1 and 2021Q1; the 2021Q2-labelled episode enters in April only.
Settlement tails end before 2020-06-01, 2021-06-01 and 2021-07-01 respectively.
These are development windows, not clean holdout proof. No 2024+ read, refit,
holdout opening or live trading is authorized by this wrap.

The original diagnostic admitted contracts expiring on the next fill session
into optimization. It was invalidated; never use its economics to select cadence.
Corrected candidate 22128802 excludes expiry on/before the next actual fill date.
Corrected 2020Q1 weekly attempt 2 hit its 5 GiB hard cap after 12/14 decisions
(exit 137, oom-kill, 2505 seconds). **0/6 valid corrected receipts exist.**
The prepared serial retry reserves 6.2 GiB RAM, caps the process at 6 GiB and disables
swap. It has not run; fitting every arm is unproven. No study job starts at wrap.

## Frozen identities and evidence

- Code: `22128802100b0372d965536ac25447d0b0c18329`, parent `5b7440ec63b36ec32467f325c91563fa21ab8552`.
- Driver SHA256: `3be553b73270cf8afb1c3726e9c67bdaa03266f0afaf299a9c62966648abca2b`.
- Analysis SHA256: `04cb1bd07f4b8c6f9e8562d46423059e23b85e0faa27cc059a444142dbc748fc`.
- Corrected root: `/home/russell/data/index_options/runs/cadence-study/v20261124-fill-v2`.
- Reused intake: sibling `v20261124`; retain its configs, receipts and bounded input/index blobs.
- Config/code hashes and reviews: corrected `verification.json`, per-episode `config.json`, `protocol.json`, `review-reuse-tests.json`.
- Exact serial retry commands/budgets: corrected `jobs-solve-6g.json`; these intentionally retain the frozen checkout path.

Analysis requires all six corrected receipts and checks provenance/accounting.
It reports net growth, drawdown, tail loss, reserve capital-days, fees/fills,
DTE attribution and doubled execution-cost sensitivity. Overlapping 2021 episodes
stay together in time-block resampling. Relative daily dominance is not positive
edge over cash; inspect absolute results too. Cost stress holds actions fixed and
is not a feasible reoptimization. American assignment and executable liquidation
marks remain unmodeled. The experiment cannot decide early-exit/hedging frequency.

## Resume exactly

In WSL, restore the frozen checkout if purge removed it. Do not run worktree-add
over an existing path or modify the path-bound frozen configs:

```bash
git -C /home/russell/dskit worktree add --detach /home/russell/wt/codex-cadence-fill-20261124 22128802100b0372d965536ac25447d0b0c18329
python3 /home/russell/storage-investigation/storage_guard.py status
```

Compare driver/analysis/config/code hashes with verification.json. The driver
sets candidate import paths explicitly; never repoint the shared editable venv.
Inspect receipts first, then select the first missing job's exact command from
jobs-solve-6g.json. It already wraps storage_guard and systemd-run. Run serially;
preserve failed logs and stop on hash mismatch, refusal, OOM or failure. Reuse
intake. Only after six valid corrected receipts run frozen analysis.py against
the corrected root, then discuss the recommendation with the owner.

## Storage and continuity

10,643,554,118 bytes across 58,542 files were moved and verified on D; DSKit/shared
PMQuant inputs stayed on C. Old paths work. Gears 5 and the empty D image were
retired by explicit request. Windows measured C 17.3 GB/D 734.2 GB free; normal
reserves C 10/D 150 GiB remain. The task exception is retired. No WSL shutdown or
compaction occurred; do not attribute C recovery solely to WSL file moves.
Recorder and Claude remote session remained active.

Receipts: `/home/russell/storage-investigation/non-dskit-migration-20261124/completion-receipt.json`
and `/home/russell/storage-investigation/2026-11-24-d-cleanup.json`.
Locations: `/home/russell/pmquant/docs/data-locations.md`.

## Review and integration

Two fresh wrap lenses reviewed the complete five-file diff at 22128802 against
696931b0: correctness passed 27 focused tests plus 8 independent probes;
tests/integration passed 87 focused tests plus 8 independent probes. Both found
Critical 0 / Major 0 / Minor 0 / Nit 0 in the code diff. The prior analysis reviews also
passed both lenses; their exact records remain in verification.json.

Deferred external-driver Minor: reuse binding equates Python true with numeric 1;
strict parameter validation rejects before solving and every frozen config uses
literal true. No driver change is made during wrap. No real results, sealed data
or full suite were read/run for these reviews. Runtime, cash-relative edge and
production readiness remain unproven.

Initial local main held 20 unrelated unpublished commits at 33093080; origin/main
was 696931b0. Preserve the unrelated local work. This session publishes only its
reviewed code and handoff. Final commits and purge receipts are appended to the
existing external verification.json. Remove only contained clean session branches
and worktrees. Preserve canonical data, evidence and services; the frozen code
remains reachable through the merged history.
