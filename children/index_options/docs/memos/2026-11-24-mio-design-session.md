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
definition, its dimension, the existing sets that index it and, where a value is
carried from one decision to the next, an update with the new assignment. Every
symbol is defined in the stage that uses it, and a "Why" note follows each step
whose reason is not obvious. This is a proposal on the session branch, not in
the Working Draft. Nothing was fitted, replayed or read from protected data; no
code, config, ledger or Path row changed.

Sources. Stages 1 and 4 to 7 restate definitions agreed on 25 November. The
update lines of stages 6 and 7 are written out here for the first time, and
their eligibility test uses the earliest usable cutoff proposed in step 1.
Stages 2 and 3 describe the locked forecast recipe. Their sizes were read from
the recipe file, its fit template and the fit code; the functions that set them
are identical in this branch and at the commit recorded in the 2020Q1 forecast
receipt. No saved fit was opened and no code was run. Stages 8 to 11 rest on
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
- $`x\in X`$ means $x$ is a member of the set $X$. $`\lvert X\rvert`$ is the
  number of members of $X$. $`\#\lbrace\cdot\rbrace`$ is the number of items
  that meet the condition inside the braces. $`X\cup Y`$ joins two sets.
- $`\mathbb R`$ is the real numbers and $`\mathbb Z`$ the integers; a subscript
  such as $`\ge 0`$ restricts them. $`\mathbb R^{19}`$ is a list of 19 real
  numbers. $`[0,1]`$ is the numbers from 0 to 1.
- $`f:X\to Y`$ means $f$ takes a member of $X$ and returns a member of $Y$.
  $`f^{-1}`$ is its inverse: it returns the input that gives a stated output.
  $`x\mapsto y`$ means $x$ is sent to $y$.
- A hat marks a forecast or an estimate. A prime, as in $`d'`$, marks the next
  cutoff. A tilde marks a simulated value. Bold marks a list of numbers.
- A bar inside a subscript, as in $`u_{n\rho\mid a}`$, reads "for the forecast
  issued at origin $a$". A bar inside brackets, as in
  $`(z\mid\mathcal F_d)`$, reads "given the information $`\mathcal F_d`$".
- $\ln$ is the natural logarithm, $\sum$ a sum and a root sign the square
  root. $\max$ takes the larger of two numbers; $\min$ takes the smallest
  member of a set.
- CDF is short for cumulative distribution function: the probability that a
  quantity is at or below a stated value.

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

Every stage below is per stock and expiry, so it carries $n$ with $t$ or $\rho$
only. The contract indices $i$, $j$ and $k$ enter in stage 11.

Two sets and one mark are not yet in the Working Draft:

```math
\mathcal R_n:=
\begin{cases}
\lbrace 1,\dots,8\rbrace, & n\text{ a weekly name},\\
\lbrace 1,2,3\rbrace, & n\text{ a monthly-only name}.
\end{cases}
```

```math
\mathcal C := \lbrace (n,\rho) :\ n\in N,\ \rho\in\mathcal R_n \rbrace,\qquad
\lvert\mathcal C\rvert=\sum_{n\in N}\lvert\mathcal R_n\rvert\le 166\times 8=1328
```

**Symbols.**

- $`\mathcal R_n`$ (proposed in step 1): the ranks used for stock $n$. A0006
  sets ranks 1 to 8 for a stock with weekly expiries and positions 1 to 3 for a
  stock with monthly expiries only.
- $`\mathcal C`$ (new): the set of all cells. A cell $`(n,\rho)`$ is one stock
  with one rank: the group whose forecasts are counted and calibrated together.
- $`d'`$ (new): the next cutoff session after $d$. If every session is
  processed, $`d'=d+1`$.

At origin $d$ the cell $`(n,\rho)`$ and the forecast $`(n,t)`$ name the same
thing when $t$ is the number of calendar days from $d$ to the expiry of rank
$\rho$. In every update below, the left side belongs to the next cutoff and the
right side uses the snapshot at $d$, as in the holdings update.

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
- $\ell$: a counter that steps back over the last 22 sessions; 0 is session $d$.
- $`\chi_{n,d}`$: the root mean square of the last 22 one-session log returns of
  stock $n$, ending at session $d$. It is zero or positive. Code field `rv_22`.
- $`\eta_{nt}`$: the number of trading sessions from session $d$ to the session
  whose close is the expiry price. A positive integer. Code field
  `sessions_to_expiry`.
- $`\kappa_{nt}`$: the scale that this forecast's return is divided by. It is
  positive. Code field `reference_scale`.
- $`p_{nt}`$: the price of stock $n$ at that expiry. It is not known at $d$.
- $`Z_{nt}`$: the normalized expiry log return. This is what the model forecasts.

**Dimension.** Indexed by $`n\in N`$ and $`t\in T`$: one $\chi$ per stock, 166
numbers, and one $\eta$, $\kappa$ and $Z$ per forecast.

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
  It is not the forecast horizon: the horizon enters through $\eta$.
- **Why the square root of the session count.** The typical size of a return
  grows roughly with the square root of the number of sessions it covers.
  Multiplying the one-session scale by that root stretches it to the forecast's
  own horizon.
- **Why the floor of 0.001.** It keeps the scale positive for a stock that has
  barely moved, so the division is always defined.
- **Why these exact numbers.** 22 and 0.001 are the preprocessing the locked
  model was trained with. Changing either would change what the trained networks
  expect, so they are restated here, not chosen.

**Update (agreed, restated).** When the next session is observed:

```math
\chi_{n,d'}\ \leftarrow\ \sqrt{\frac{1}{22}\sum_{\ell=0}^{21} r_{n,d'-\ell}^{2}},\qquad
\kappa_{nt}\ \leftarrow\ \max\left(\chi_{n,d'},\ 0.001\right)\sqrt{\eta_{nt}}
```

Here $`d'`$ is the next cutoff session, $`r_{n,d'-\ell}`$ is the log return
$\ell$ sessions before it, and $`\eta_{nt}`$ on the right is recounted from
$`d'`$. The window slides by one session: the oldest squared return leaves and
the newest enters. Carried state: each stock's last 22 squared returns,
22 x 166 = 3,652 numbers.

### Stage 2. Model input (new symbol)

For a forecast of stock $n$ at the expiry $t$ calendar days after origin $d$:

```math
q_{nt}^{\text{mkt}}\in\mathbb R^{19},\qquad
\left(q_{nt}^{\text{mkt}},\ \ln\eta_{nt},\ t/\eta_{nt}\right)\in\mathbb R^{21} \qquad\text{(new)}
```

**Symbols.**

- $n$, $t$, $d$: the stock, the calendar days to the expiry, and the origin
  session.
- $`q_{nt}^{\text{mkt}}`$ (new): a list of 19 numbers. Each is a quantile of the
  normalized return $`Z_{nt}`$ under the distribution implied by that expiry's
  option prices: the value that the return falls below with a stated
  probability. The 19 probabilities are 0.01, 0.025, 0.05, 0.1, 0.15, 0.2, 0.25,
  0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.975 and 0.99. Code
  fields `rn_q_0100` to `rn_q_9900`.
- $`\eta_{nt}`$: the number of trading sessions to the expiry-price session, as
  in stage 1. $`\ln\eta_{nt}`$ is its natural logarithm. Code field
  `log_sessions_to_expiry`.
- $`t/\eta_{nt}`$: calendar days per trading session over the forecast's life.
  For one full week it is 7/5 = 1.4. Code field `calendar_days_per_session`.
- $`\mathbb R^{19}`$, $`\mathbb R^{21}`$: lists of 19 and of 21 real numbers.

**Dimension.** 21 numbers per forecast, indexed by $`n\in N`$ and $`t\in T`$.
The study lists 327 candidate feature columns; the locked recipe reads these 21.

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

Five networks, one per seed (11, 29, 47, 53, 71), share one design and serve all
166 stocks. The 21 inputs are first standardized with the fit's own means and
scales.

```math
\begin{aligned}
\operatorname{enc} &: \mathbb R^{19}\to\mathbb R^{8} \qquad\text{(layers } 19\to 64\to 8\text{)},\\
\operatorname{head} &: \mathbb R^{8+2}\to\mathbb R^{9} \qquad\text{(layers } 10\to 8\to 9\text{)}.
\end{aligned}
```

The head takes the encoder's 8 values and the two calendar numbers:

```math
\operatorname{head}\left(\operatorname{enc}\left(q_{nt}^{\text{mkt}}\right),\ \ln\eta_{nt},\ t/\eta_{nt}\right)\ \mapsto\ \left(\text{wt},\ \text{loc},\ \text{sd}\right)\in\mathbb R^{3}\times\mathbb R^{3}\times\mathbb R^{3}
```

**Symbols.**

- $`\operatorname{enc}`$ (new): the encoder, a function that takes the 19 market
  quantiles and returns 8 numbers. "Layers 19 to 64 to 8" gives the sizes of its
  input, its one hidden layer and its output.
- $`\operatorname{head}`$ (new): a function that takes those 8 numbers and the
  two calendar numbers, 10 in all, and returns 9 numbers through one hidden
  layer of 8.
- $`q_{nt}^{\text{mkt}}`$, $`\ln\eta_{nt}`$, $`t/\eta_{nt}`$: the model input of
  stage 2 for stock $n$ and the expiry $t$ calendar days away: the 19 market
  quantiles, the logarithm of the session count and the calendar days per
  session.
- wt, loc, sd (new): the three weights, three centres and three standard
  deviations that one network returns. The weights are positive and sum to 1;
  the standard deviations are positive. Stage 4 uses them.
- $`\mathbb R^{3}\times\mathbb R^{3}\times\mathbb R^{3}`$: three lists of three
  real numbers.

**Dimension.** Each network has (19 x 64 + 64) + (64 x 8 + 8) + (10 x 8 + 8) +
(8 x 9 + 9) = 1,969 trained weights, 9,845 over the five seeds, plus 42
standardization constants. That count is mine, from the layer sizes in the code.

**Why.**

- **Why five networks.** Training starts from random numbers set by the seed,
  so two seeds give slightly different networks. Pooling five reduces the effect
  of one lucky or unlucky start. A0008 fixes the five seeds.
- **Why the centres are constants.** This is the recipe's "fixed location": the
  input can move weight between the centres and widen or narrow each curve, but
  it cannot move the centres. The choice is A0008's, restated here.
- **Why standardize the inputs.** The 21 inputs differ in size. Subtracting each
  one's training mean and dividing by its training spread puts them on one scale
  for training.
- **Why these layer sizes.** 64 and 8 are tuned values locked in A0008.

**Update (locked, A0008).** At the first session of each calendar quarter:

```math
\left(\operatorname{enc},\ \operatorname{head},\ \text{loc}\right)\ \leftarrow\ \operatorname{fit}\left(\text{all mature history before the quarter starts}\right)
```

Here $`\operatorname{fit}`$ is the locked training procedure of A0008 (learning
rate 0.0001, dropout 0.11, weight decay 0.045, 6 epochs); it returns the trained
encoder, head and centres of each seed and resets the standardization
constants. Mature history means forecasts whose outcome had settled. Within the
quarter the five networks stay fixed.

**Why each quarter, and why only mature history.** Retraining each quarter lets
the networks learn from the newest outcomes. Using only outcomes settled before
the quarter starts means no forecast in the quarter is trained on its own
future.

### Stage 4. Forecast CDF (agreed)

```math
\widehat\Psi_{nt}^{Z}(z\mid\mathcal F_d)=\sum_{\ell=1}^{15}\text{wt}_{\ell,nt}\ \Phi\left(\frac{z-\text{loc}_{\ell}}{\text{sd}_{\ell,nt}}\right),\qquad
\sum_{\ell=1}^{15}\text{wt}_{\ell,nt}=1,\qquad \widehat\Psi_{nt}^{Z}:\mathbb R\to[0,1]
```

In price coordinates, as agreed:

```math
\widehat\Psi_{nt}(p\mid\mathcal F_d)=\widehat\Psi_{nt}^{Z}\left(\frac{\ln(p/S_{n,d})}{\kappa_{nt}}\mid\mathcal F_d\right),\qquad p>0
```

**Symbols.**

- $n$, $t$, $d$: the stock, the calendar days to the expiry, and the origin.
- $`\mathcal F_d`$: the information available by the cutoff of session $d$.
- $z$: any value of the normalized return.
- $`\widehat\Psi_{nt}^{Z}(z\mid\mathcal F_d)`$: the forecast probability, made
  with that information, that the normalized return $`Z_{nt}`$ of stage 1 is at
  or below $z$. The superscript $Z$ marks normalized-return units.
- $\ell$: here a counter over the 15 mixture components. Components 1 to 3 come
  from the first seed, 4 to 6 from the second, and so on.
- $`\text{wt}_{\ell,nt}`$: the weight of component $\ell$ in this forecast. Each
  seed's three weights sum to one fifth, so all 15 sum to 1.
- $`\text{loc}_{\ell}`$: the centre of component $\ell$, in normalized-return
  units. It is the same for every forecast of a quarter.
- $`\text{sd}_{\ell,nt}`$: the standard deviation, or width, of component
  $\ell$ in this forecast. It is positive.
- $\Phi$: the standard normal CDF.
- $p$: any price level, in dollars per share.
- $`\widehat\Psi_{nt}(p\mid\mathcal F_d)`$: the same forecast in price terms:
  the probability that the expiry price $`p_{nt}`$ is at or below $p$.
- $`S_{n,d}`$, $`\kappa_{nt}`$: the closing price at the origin and the scale
  of stage 1.

**Dimension.** Indexed by $`n\in N`$ and $`t\in T`$. A forecast is 45 numbers:
15 weights, 15 centres and 15 standard deviations. Those are saved, and so is
the curve at 13 values of $z$ from -3 to 3 (25 on the fine grid).

**Why.**

- **Why a mixture.** One normal curve cannot be lopsided or fat-tailed. A
  weighted sum of several can, and it stays a smooth, valid CDF that can be
  evaluated exactly at any point.
- **Why one fifth per seed.** Averaging the five networks' CDFs with equal
  weight is the linear pool that A0008 locks. An average of mixtures is again a
  mixture, here with 15 components.
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
  of a past forecast.
- $`E_{n\rho\mid a}`$ (proposed in step 1): the expiry date of that forecast.
- $`t_{n\rho\mid a}`$, $`\eta_{n\rho\mid a}`$: its calendar days and its trading
  sessions to expiry, counted at its origin.
- $`d^{\star}_{n\rho\mid a}`$ (proposed in step 1): the earliest usable cutoff,
  the first cutoff session at which the forecast's outcome may be used.
- $`S_{n,\,a+\eta_{n\rho\mid a}}`$: the closing price of stock $n$ on the
  expiry-price session. It is the realized expiry price.
- $`\mathcal F_a`$: the information available by the cutoff of session $a$.
- $`\widehat\Psi_{nt}(\cdot\mid\mathcal F_a)`$: the forecast CDF of stage 4 in
  price terms, exactly as issued at origin $a$.
- $`u_{n\rho\mid a}`$: the raw PIT of that forecast, a number from 0 to 1. PIT
  is short for probability integral transform.

**Dimension.** One number per settled forecast, shared by its contracts.
Indexed by $`n\in N`$, $`\rho\in\mathcal R_n`$ and the origins $`a<d`$.

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
- $`M_{n,\rho,d}`$: the number of the cell's forecasts whose outcome is usable
  by cutoff $d$.
- $`A_{n,\rho,d}(\beta)`$: the number of those whose raw PIT is at or below
  $\beta$.

**Dimension.** Indexed by $`(n,\rho)\in\mathcal C`$ and $`\beta\in\mathcal Q`$:
1 + 11 = 12 integers per cell, at most 15,936 in all.

**Why.**

- **Why counts, not every score.** The correction of stage 7 needs only, for
  each threshold, the share of scores at or below it. Twelve integers per cell
  hold that. The storage never grows and an update is an addition.
- **Why per cell.** Forecast errors differ by stock and by horizon. A0006 sets
  the cell as the group that is calibrated together.
- **What the counts cannot do.** They do not record which scores occurred
  together, so they cannot supply the dependence between cells. Stage 9 keeps
  that record.

**Update.**

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
\widehat\Gamma_{n,\rho,d}(y):=\frac{\beta_{+}-y}{\beta_{+}-\beta_{-}}\ \widehat\Gamma_{n,\rho,d}(\beta_{-})+\frac{y-\beta_{-}}{\beta_{+}-\beta_{-}}\ \widehat\Gamma_{n,\rho,d}(\beta_{+}),\qquad \beta_{-}\le y\le\beta_{+}
```

**Symbols.**

- $n$, $\rho$, $d$: the stock and rank of the cell, and the current cutoff.
- $\beta$, $`\mathcal Q`$: one calibration threshold and the configured set of
  11.
- $`M_{n,\rho,d}`$, $`A_{n,\rho,d}(\beta)`$: the cell's counters of stage 6.
- $\zeta$: the strength, one positive number shared by all cells. A larger value
  pulls the map toward no correction. Its value is not chosen yet.
- $`\widehat\Gamma_{n,\rho,d}`$: the correction map of the cell at cutoff $d$.
  It takes a probability from 0 to 1 and returns a corrected probability from 0
  to 1.
- $y$: any probability level from 0 to 1.
- $`\beta_{-}`$, $`\beta_{+}`$: the neighbouring points just below and just
  above $y$, among the 11 thresholds and the two ends 0 and 1.

**Dimension.** Indexed like the counters. The map is fixed by 11 numbers per
cell, at most 14,608 per cutoff.

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

**Update.** After the counters:

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
- $`w_{n\rho\mid a}`$ (proposed in step 1): its corrected PIT, a number from 0
  to 1.

**Dimension.** One curve and one number per cell and origin. Indexed by
$`(n,\rho)\in\mathcal C`$ and the origin.

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
  forecast at origin $a$.
- $`d^{\star}_{n\rho\mid a}`$: a forecast's earliest usable cutoff (stage 5).
- $`\mathcal O_{a,d}`$ (proposed in step 1): the cells of origin $a$ whose
  outcome is usable by cutoff $d$.
- $`u_{n\rho\mid a}`$, $`w_{n\rho\mid a}`$: the raw and the corrected PIT.
- $`\mathbf w_a`$: the joint observation of origin $a$: the list of its
  corrected PITs, one per cell in $`\mathcal C_a`$. At cutoff $d$ only the
  entries in $`\mathcal O_{a,d}`$ are known.
- $`\mathcal H_d`$ (new): the joint record at cutoff $d$: every settled row.

**Dimension.** A joint observation has at most 1,328 entries. The joint record
is indexed by the origins $`a<d`$ and the cells of $`\mathcal C_a`$. The 2020Q1
forecast receipt records 69,687 rows over 62 sessions, about 1,120 per session,
so every session is an origin. Over the roughly 1,070 sessions of open
forecasts that is about 1.2 million rows, and never more than about 1.4 million.

**Why.**

- **Why one observation per origin.** A solve holds forecasts all made at the
  same cutoff. Dependence must therefore be measured between scores of
  forecasts made on the same day.
- **Why cells, not calendar dates, as coordinates.** A cell means the same thing
  at every origin, the same stock at the same place in line, so its scores can
  be compared across origins.
- **Why keep every row.** Dependence needs to know which scores occurred
  together. The counters of stage 6 discard that.

**Update.**

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
\operatorname{Cop}_d:[0,1]^{\lvert\mathcal C_d\rvert}\to[0,1],\qquad
\operatorname{Cop}_d(\mathbf y):=\widehat{\Pr}\left(w_{n\rho\mid d}\le y_{n\rho}\ \text{ for all }(n,\rho)\in\mathcal C_d\ \mid\ \mathcal F_d\right)
```

**Symbols.**

- $d$: the current cutoff and origin. $`\mathcal F_d`$: the information
  available by its cutoff.
- $`(n,\rho)`$: a cell, one stock $n$ with one original expiry rank $\rho$.
- $`\mathcal C_d`$: the cells that receive a valid forecast at $d$ (stage 9,
  with $d$ as the origin).
- $`w_{n\rho\mid d}`$: the corrected PIT that the forecast issued now for cell
  $`(n,\rho)`$ will have once it settles. It is not known at $d$.
- $`\mathbf y`$, $`y_{n\rho}`$: a list of levels from 0 to 1, one per cell, and
  its entry for cell $`(n,\rho)`$.
- $`\widehat{\Pr}`$: a forecast probability.
- $`\operatorname{Cop}_d`$ (new, a placeholder name because $C$ is the
  premium): the copula at $d$. It returns the forecast probability that every
  cell's corrected PIT is at or below its level.

**Dimension.** The number of cells forecast at $d$, at most 1,328. By definition
each entry alone is spread evenly from 0 to 1. If a correlation-matrix family is
chosen, its state is one symmetric matrix of that size: at most 881,128 distinct
pairs, against about 1,070 overlapping origins.

**Why.**

- **Why a copula.** Stage 8 gives each cell its own corrected CDF but says
  nothing about cells moving together. The copula holds only that linkage.
  Keeping the two apart lets each be estimated and checked on its own.
- **Why it is needed.** Portfolio tail risk depends on many positions losing
  together. Without the linkage that risk cannot be measured.

**Update: not defined.** It belongs to step 2. The counters cannot supply it.

### Stage 11. Joint scenarios (open; shape only)

```math
\tilde{\mathbf w}\sim\operatorname{Cop}_d,\qquad
\tilde p_{nt} := \widehat\Psi_{nt}^{-1}\left(\widehat\Gamma_{n,\rho,d}^{-1}\left(\tilde w_{n\rho}\right)\mid\mathcal F_d\right),\qquad t=t_{n\rho\mid d}
```

**Symbols.**

- $d$: the current cutoff and origin. $`\mathcal F_d`$: the information
  available by its cutoff.
- $`(n,\rho)`$: a cell, one stock $n$ with one original expiry rank $\rho$.
- $`\operatorname{Cop}_d`$: the copula of stage 10. The mark $\sim$ reads "is
  drawn at random from".
- $`\tilde{\mathbf w}`$, $`\tilde w_{n\rho}`$ (new): one simulated list of
  corrected PITs, one per cell forecast at $d$, and its entry for cell
  $`(n,\rho)`$.
- $`\widehat\Gamma_{n,\rho,d}^{-1}`$: the inverse of the cell's correction map
  of stage 7: it returns the raw probability that the map sends to a stated
  corrected probability.
- $`\widehat\Psi_{nt}^{-1}(\cdot\mid\mathcal F_d)`$: the inverse of the forecast
  CDF of stage 4 in price terms: it returns the price at which that CDF equals a
  stated probability.
- $`t_{n\rho\mid d}`$: the calendar days from $d$ to the expiry of rank $\rho$.
- $`\tilde p_{nt}`$ (new): the simulated expiry price of stock $n$ at that
  expiry.

Each simulated price then replaces the known price $`p_{nt}`$ in the agreed
payoff $`V_{knt}^{ij}`$, the expiry payoff of one contract with side $`i\in I`$,
type $`j\in J`$, contract index $`k\in K_{nt}^{j}`$, stock $n$ and expiry $t$.

**Dimension.** The number of scenarios times the number of cells forecast at
$d$, at most 1,328 prices per scenario. The number of scenarios is not chosen.

**Why.**

- **Why invert.** A draw is a score from 0 to 1, but payoffs need prices.
  Inverting the corrected CDF returns the price whose corrected probability
  equals the score. Each cell's simulated prices then follow its own corrected
  forecast, while the draw carries the linkage between cells.
- **Why the inverses exist.** The map and the mixture CDF are strictly
  increasing, so for a score strictly between 0 and 1 there is exactly one
  price.

**No update.** Scenarios are drawn again at every solve, because the forecasts,
the maps and the cells change with the cutoff.

### Order of one update

Names in the block: `chi`, `eta`, `kappa`, `zeta`, `beta` and `rho` are the
Greek letters above; `d_star` is the earliest usable cutoff; `Psi_hat` and
`Gamma_hat` are the forecast CDF and the correction map; `F[a]` is the
information at origin `a`; `S[n, session]` is the closing price of stock `n` at
that session; `M` and `A` are the counters; `u` and `w` are the raw and the
corrected PIT; `H` is the joint record; `<-` is the assignment.

```text
UPDATE from cutoff d to the next cutoff d'

1. Observe returns, expiry-price records and option quotes through d'.

2. Preprocess: chi[n,d'] <- 22-session RMS; then eta and kappa per forecast.

3. Settle. FOR each (a, n, rho) with d < d_star[n,rho|a] <= d':
       u[n,rho|a] <- Psi_hat[n,t](S[n, a + eta[n,rho|a]] | F[a])    (Appendix C)
       w[n,rho|a] <- Gamma_hat[n,rho,a](u[n,rho|a])                 (map of origin a)
       APPEND row (a, n, rho, u, w, and the step 1 fields) to H.

4. Count:  M[n,rho,d']       <- M[n,rho,d]       + new rows of the cell
           A[n,rho,d'](beta) <- A[n,rho,d](beta) + new rows with u <= beta

5. Map:    Gamma_hat[n,rho,d'](beta) <-
               (A[n,rho,d'](beta) + zeta * beta) / (M[n,rho,d'] + zeta)
           Store it as version d'. Keep earlier versions that open forecasts use.

6. At a quarter start only: refit the five networks (locked, A0008).

7. Forecast. FOR each cell with valid inputs at d':
       Build the 21 inputs; evaluate the 45 mixture numbers.
       Save Psi_hat[n,t](. | F[d']) with map version d'.

8. Copula state: step 2, not defined.

9. Solve with the forecasts and maps of d'.
```

**Why this order.** Step 3 comes before steps 4 and 5 so that every new score is
made with the map of its own origin before its outcome changes any counter.
Step 7 comes after step 5 so that each new forecast is issued with the newest
map.

### Sizes at a glance

| Stage | Object | Size | Carried to the next cutoff |
|---|---|---|---|
| 1 | Return window | 22 squared returns per stock: 3,652 | Yes, every session |
| 2 | Model input | 21 numbers per forecast, of 327 candidate features | No, rebuilt |
| 3 | Networks | 1,969 weights per seed: 9,845 | Yes, refitted each quarter |
| 4 | Forecast CDF | 45 numbers per forecast | No, kept as issued |
| 5 | Raw PIT | 1 number per settled forecast | Created at settlement |
| 6 | Counters | 12 integers per cell: at most 15,936 | Yes, incremented |
| 7 | Correction map | 11 numbers per cell: at most 14,608 per version | Yes, recomputed |
| 8 | Corrected PIT | 1 number per settled forecast | Created at settlement |
| 9 | Joint record | About 1.2 million rows | Yes, appended |
| 10 | Copula | Dimension at most 1,328 | Step 2 |
| 11 | Scenarios | Number of scenarios x at most 1,328 prices | No, redrawn |

To confirm, with (a) recommended in each:

1. Names of the network's parts: (a) roman words (enc, head, wt, loc, sd), as
   bid and ask already are; (b) single letters of your choice.
2. Update clock: (a) counters, maps and the joint record update at every
   session even when the solve is weekly, as the agreed daily variance update
   already does; (b) they update only at solves.
3. Issued maps: (a) keep each version until its forecasts settle, since older
   versions can be rebuilt from the joint record; (b) keep every version.
4. New symbols here ($`q_{nt}^{\text{mkt}}`$, $`\mathcal C`$, $`\mathcal H_d`$,
   $`d'`$, bold for a list, the tilde and the placeholder Cop): (a) accept;
   (b) rename.

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
