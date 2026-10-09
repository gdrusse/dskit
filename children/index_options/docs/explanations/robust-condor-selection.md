# Robust condor selection, worked by hand

## TL;DR

Robust selection picks the condor whose profit survives the forecast being
somewhat wrong, not the one that looks best if the forecast is exactly right.
In the toy example below, the exact forecast picks a narrow condor; allowing a
small, bounded forecast error picks a wide one; a larger error picks no trade.
The size of the allowed error decides the answer, and it is not yet calibrated
for real use.

This explains §3–§4 of
[Simple formulation with robustification](../research/distribution-modeling/simple-formulation-with-robustification.md).

## In plain words

Picture the forecast as sand spread along a ruler of possible expiry prices:
more sand means more likely. A condor loses money only if the price ends in
one of its two "wings", near the ends of the ruler.

Now let a skeptic move some sand. Moving sand costs effort: amount times
distance. The skeptic has a fixed effort budget and spends it to push sand
into our wings. We score each trade by its profit **after** the skeptic's best
effort, and trade only if that is still positive.

A narrow condor has a lot of sand sitting right next to its wings, so a small
push hurts it badly. A wide condor's wings are far from where the sand sits,
so the same budget does about half the damage. That is why robustness can
favour the wide condor even when the plain forecast prefers the narrow one.

Optional "bands" add per-price limits, such as "the chance of settling at or
below 90 is at most 8%". They stop the skeptic from making any single
probability implausible.

## The question

Which condor, if any, should we sell when the forecast CDF may be wrong by a
bounded amount? The concern: a model that is slightly overconfident about the
centre makes a narrow, high-credit condor look better than it is.

## The optimization

### Sets and indices

- $k\in K=\lbrace 1,\dots,m\rbrace$: grid points, with expiry prices $s_1<\dots<s_m$.
- $j\in J=\lbrace 1,\dots,m-1\rbrace$: CDF points; the gap after $s_j$ is $s_{j+1}-s_j$.
- $J_t^{\text{band}}\subseteq J$: CDF points that carry a band (the research note's $\mathcal J_t$).
- $\mathcal X_t$: eligible condors at entry time $t$; $\varnothing$ means no trade.

### Parameters (fixed before choosing)

- $S_t$: spot price at entry.
- $\widehat p_k$: nominal probability that the index settles at $s_k$.
- $\widehat Q_j=\sum_{k\le j}\widehat p_k$: the nominal CDF.
- $\ell_{x,k}=L_x(s_k)$: loss of condor $x$ per share if it settles at $s_k$.
- $c_t(x)$, $f_t(x)$: credit received and fees, per share; $M$: multiplier (100).
- $\rho_t\ge0$: the budget, as a fraction of spot; $S_t\rho_t$ is in dollars per share.
- $Q_j^{\text{lo}}\le Q_j^{\text{hi}}$: band for $j\in J_t^{\text{band}}$ (written $\ell_{t,j}, u_{t,j}$ in the research note).

### Decision variables

- Ours: $z_x\in\lbrace 0,1\rbrace$ for each $x\in\mathcal X_t$; $z_x=1$ means sell condor $x$.
- The skeptic's: $q_k\ge0$, alternative probabilities; $Q_j$, their CDF;
  $d_j\ge0$, the size of the CDF change at $s_j$.

### Inner problem: worst plausible expected loss (a linear program)

```math
R_t(x)=\max_{q,\,Q,\,d}\ \sum_{k\in K}\ell_{x,k}\,q_k\qquad\text{subject to}
```

```math
\begin{gathered}
\sum_{k\in K}q_k=1,\qquad q_k\ge0\ \ (k\in K)\qquad\text{(I1)}\\
Q_j=\sum_{k\le j}q_k\ \ (j\in J)\qquad\text{(I2)}\\
d_j\ge Q_j-\widehat Q_j,\qquad d_j\ge\widehat Q_j-Q_j\ \ (j\in J)\qquad\text{(I3)}\\
\sum_{j\in J}\frac{s_{j+1}-s_j}{S_t}\,d_j\le\rho_t\qquad\text{(I4)}\\
Q_j^{\text{lo}}\le Q_j\le Q_j^{\text{hi}}\ \ (j\in J_t^{\text{band}})\qquad\text{(I5)}
\end{gathered}
```

- **(I1) Probabilities** are nonnegative and sum to one.
- **(I2) CDF definition**: $Q_j$ is the probability of settling at or below $s_j$.
- **(I3) Absolute value**: the two inequalities make $d_j\ge|Q_j-\widehat Q_j|$.
  Since $d_j$ appears only in (I4), the optimum can always set them equal.
- **(I4) Budget** (1-Wasserstein distance): moving probability $\delta$ a
  distance $h$ costs $h\delta$. Summing gap times CDF change gives exactly that
  total, divided by spot.
- **(I5) Bands** cap the CDF at chosen points. Drop (I5) to use the budget alone.

### Outer problem: choose the trade

```math
\max_{z}\ \sum_{x\in\mathcal X_t}z_x\,M\big[c_t(x)-f_t(x)-R_t(x)\big]
\quad\text{s.t.}\quad\sum_{x\in\mathcal X_t}z_x\le1,\qquad z_x\in\lbrace 0,1\rbrace
```

All $z_x=0$ is no trade, worth 0; ties go to no trade. Each $R_t(x)$ is a
number once its LP is solved, so this is "one LP per candidate, then take the
best". The research note's dualized mixed-integer form is only for when there
are too many candidates to enumerate. With $\rho_t=0$ and zero-width bands,
$q=\widehat p$ and the choice is the nominal one.

## Worked example

**Invented teaching numbers, not project results.** Spot $S_t=100$, fees 0,
multiplier 100. The index can settle at only seven prices:

```text
price s_k       85    90    95   100   105   110   115
nominal p_k   0.01  0.04  0.20  0.50  0.20  0.04  0.01
nominal CDF   0.01  0.05  0.25  0.75  0.95  0.99  1.00
```

The real grid has many more points and support beyond every strike. Here both
condors' losses are already capped at 85 and 115, so extra points change
nothing.

Two candidates, both with 5-point wings:

- **A, narrow**: buy 90 put, sell 95 put, sell 105 call, buy 110 call. Credit 1.15.
- **B, wide**: buy 85 put, sell 90 put, sell 110 call, buy 115 call. Credit 0.70.

Loss per share at each price:

```text
price s_k    85  90  95  100  105  110  115
loss of A     5   5   0    0    0    5    5
loss of B     5   0   0    0    0    0    5
```

For example, A at 90: the sold 95 put owes 95 − 90 = 5; the bought 90 put pays 0.

### Step 1: nominal choice

- A's expected loss: 5 × 0.01 + 5 × 0.04 + 5 × 0.04 + 5 × 0.01 = 0.50.
- B's expected loss: 5 × 0.01 + 5 × 0.01 = 0.10.
- A's value: 1.15 − 0.50 = 0.65, i.e. \$65 per contract.
- B's value: 0.70 − 0.10 = 0.60, i.e. \$60 per contract.

The nominal model sells **A**.

### Step 2: the budget

Take $\rho_t=0.006$, so $S_t\rho_t$ = 100 × 0.006 = 0.60. The skeptic may move,
for example, 0.12 of probability by 5 points (0.12 × 5 = 0.60).

A condor's loss grows at most \$1 for each \$1 the price moves into a wing. So the
skeptic can add at most 0.60 to expected loss, and only by moving probability
that sits right at a wing's edge.

### Step 3: worst case for A

Moving probability from 95 to 90 costs 5 per unit and adds 5 of loss per unit:
\$1 of damage per \$1 of budget. The same holds for 105 to 110. With 0.20
available on each side, the skeptic moves 0.06 per side:

- Cost: 5 × 0.06 + 5 × 0.06 = 0.60, the whole budget.
- Worst-case probabilities: 0.01, 0.10, 0.14, 0.50, 0.14, 0.10, 0.01.
- $R_t(A)$ = 5 × (0.01 + 0.10 + 0.10 + 0.01) = 5 × 0.22 = 1.10.
- Robust value: 1.15 − 1.10 = 0.05, i.e. \$5 per contract.

### Step 4: worst case for B

The \$1-per-\$1 moves are 90 to 85 and 110 to 115, but only 0.04 sits at each:

- Cost: 5 × 0.04 + 5 × 0.04 = 0.40. Added loss: 0.40. Budget left: 0.20.
- Next cheapest is 95 to 85: distance 10 for 5 of loss, 50 cents per \$1.
- 0.20 ÷ 10 = 0.02 probability moved (0.01 per side). Added loss: 5 × 0.02 = 0.10.
- Worst-case probabilities: 0.06, 0.00, 0.19, 0.50, 0.19, 0.00, 0.06.
- $R_t(B)$ = 0.10 + 0.40 + 0.10 = 0.60; check: 5 × (0.06 + 0.06) = 0.60.
- Robust value: 0.70 − 0.60 = 0.10, i.e. \$10 per contract.

Budget check through the formula: B's worst-case CDF is 0.06, 0.06, 0.25,
0.75, 0.94, 0.94. Its changes from nominal are 0.05, 0.01, 0, 0, 0.01, 0.05.
Each gap is 5, so the cost is 5 × 0.12 = 0.60, exactly the budget.

### Step 5: decide

Values: A 0.05, B 0.10, no trade 0. The robust rule sells **B**, with a modeled
floor of \$10 per contract. That floor holds only if the truth lies inside the
chosen set; it is not a guarantee.

![Robust value vs budget, and nominal vs worst-case probabilities for A and B](robust-condor-selection.svg)

### Changing the budget

Writing $D=S_t\rho_t$:

- A's robust value is 0.65 − D.
- B's is 0.60 − D up to D = 0.40, then 0.40 − 0.5D.
- A wins below D = 0.50 (a tie at 0.50). B wins from 0.50 to 0.80.
  At D = 0.80 and above nothing beats no trade.

### Adding a band

Suppose calibration supports "at most 8% chance of settling at or below 90,
and at most 8% at or above 110". In the notation: $Q^{\text{hi}}$ = 0.08 at 90,
and $Q^{\text{lo}}$ = 0.92 at 105.

- **A**: nominal CDF at 90 is 0.05, so only 0.03 more may move to 90 or below
  on each side. Added loss: 5 × 0.03 × 2 = 0.30. $R_t(A)$ = 0.80; value 0.35.
- **B**: moving 90 to 85 leaves the CDF at 90 unchanged. The extra 0.01 from 95
  raises it to 0.06, inside the band. $R_t(B)$ stays 0.60; value 0.10.

The choice returns to **A**. Bands and the budget act together, which is why
the research note tunes both on earlier settled dates only and freezes them
before any decision.

All numbers above were checked by solving the LP directly.

## How we will calibrate rho

### Purpose and agreed starting target

The owner agreed on 2026-10-08 to start with **protection against average
expected-loss underestimation**, followed by later chronological validation and
prespecified stress-period checks. This section explains that direction; it is
not an executable calibration contract or a measured PatchTST radius.

Rho allows the optimizer to distrust the nominal probabilities. In (I4), it is
a dimensionless transport budget: moving probability 0.10 by 5 dollars at spot
100 costs 0.10 times 5 = 0.50 dollars per share, then 0.50 / 100 = 0.005 of rho.
That is neither a 0.5% probability of loss nor a 0.5% confidence level.

For a fixed candidate x, write R_t(x; rho) for the worst expected loss under
(I1)-(I4), initially without bands (I5). Increasing rho enlarges the allowed
set, so R cannot decrease for that fixed candidate. The selected candidate can
change; its realized loss need not move monotonically with rho.

The calibration question is: **how much probability movement is needed before
the decision rule stops systematically understating average losses?** This
targets a decision's expected-loss bound, not containment of the entire true
conditional CDF. A single realized loss above R is possible even with a correct
distribution: an expected loss is not a maximum realized loss.

### The residual and the smallest passing radius

Before looking at results, freeze a finite, ordered set of trial radii G and
the same candidate, credit, fee, availability and no-trade rules for each.
Let x_t(rho) be the candidate selected using only information available at t,
including the nominal forecast and that trial radius. Let Y_t be its eventual
realized expiry loss per share. For no trade, both loss and bound are zero.

```math
e_t(\rho)=Y_t(x_t(\rho))-R_t(x_t(\rho);\rho).
```

A positive residual means the realized loss exceeded the robust expected-loss
estimate; a negative residual means the allowance exceeded that realization.
Average residuals within each date using frozen eligible observations and
weights. Estimate an upper uncertainty bound U(rho) on the mean date residual
using contiguous date blocks, keeping all same-date stocks together.
Account for trying multiple radii and any additional inferential claims.

```math
\rho^*=\min\lbrace \rho\in G: U(\rho)\le0\rbrace.
```

Here "smallest" means the least conservative radius that clears the chosen
average-loss test, not the smallest radius regardless of evidence. If none
passes, the result is unsupported and the rule abstains; do not enlarge G
after seeing a failure. The formula is conditional on adequate support.
Sparse or almost-always-abstaining policies cannot earn a calibration claim
merely by accumulating zero residuals.

**Invented arithmetic, not computed calibration results:** suppose average
realized loss is 0.75 dollars per share, and two trial policies happen to select
the same trades. Their average robust bounds are 0.70 and 0.80. Their average
residuals are 0.75 - 0.70 = +0.05 and 0.75 - 0.80 = -0.05 respectively.
The first underestimates average loss. The second looks sufficient in-sample,
but could still fail if its upper uncertainty bound is positive. We have not
computed such a bound for this example. Rho cannot be recovered from these
averages alone; the distributions, price grids and payoffs determine R.

### Why not the average miss, or the average rho?

A mean loss miss is in dollars per share; rho is a spot-normalized probability
transport budget. There is no universal conversion. Even for a 1-Lipschitz
condor loss, nominal expected loss plus spot times rho is an upper bound on R,
not a general equality; the wide-condor example above shows the difference.

The sample mean also ignores uncertainty around that estimate. Signed misses
can cancel across good and bad periods. An average of separately estimated
radii has no automatic connection to an average-loss or stress-protection
target either. Historical errors inform calibration, but do not define the
protection target by themselves.

A larger radius may be chosen for a separately declared stronger objective.
It is not automatically more scientifically justified: excessive conservatism
can eliminate all trades. Conversely, passing an average-loss test does not
establish protection against severe periods, individual tail losses, or
portfolio drawdowns.

### Reuse, validation and the remaining execution contract

Existing code already implements this starting approach:
[AdaptiveWassersteinRadius and RobustCorrectionStudy](../../index_options/cdf_study.py).
The former takes radii, min_dates, block_dates, replicates, alpha and seed.
It averages residuals by date, uses a circular moving-block bootstrap,
adjusts its percentile across the trial radii, and selects the smallest
passing radius or refuses. The caller constructs selected-policy residuals
and filters to strictly earlier, settled history by symbol and requested tenor.
The [historical memo](../memos/2026-09-30-causal-decision-region-calibration-and-robust-condor.md)
records an older GPD/ETF application. Its four-date blocks, small support
threshold and grid are not adopted settings for the 31-day PatchTST stock study.
Bootstrap bounds are approximate and depend on the sampling assumptions;
multiplicity adjustment does not fix an invalid dependence model.

The next bounded calibration contract must settle these items before a run:

1. Match saved nominal seed11 chronological forecasts to supported instruments,
   exact expiry, entry-known candidate payoffs and costs. Inventory the existing
   onboarding and grid/LP seams before adding code; the old GPD archive runner
   is not demonstrated to accept the new PatchTST packages unchanged.
2. Freeze G, alpha, pooling/weights, minimum effective date-block support,
   trade-active support and abstention reporting. Start with one fixed
   calibration rule; do not average ticker radii or select a seed from later
   performance. Preserve all eligible opportunities, refusals and no trades.
3. Freeze a primary date-block length spanning the overlapping target horizon
   and a longer sensitivity length. Report effective support at both lengths.
   Many correlated ticker forecasts do not create many independent dates.
4. Use strictly settled and available prior labels. Proposed research forecast
   dates are 2024-02-06 through 2024-12-31 for calibration, with final 31-day
   labels maturing through 2025-01-31 plus availability lag; later assessment
   dates are 2025-02-04 through 2025-11-28, settling through 2025-12-29.
   These dates remain conditional on actual matched coverage. Freeze rho
   before later assessment, or separately freeze a prior-only update algorithm.
5. Predeclare stress-period definitions from entry-known information, their
   support rules and assessment criteria. Report average residuals, uncertainty,
   trade activity and stress results on later dates without selecting from
   later profits. Failure or inadequate evidence stays visible; it does not
   trigger a retrospective change to rho. Separate tail-risk criteria are
   needed for a stronger claim than average-loss protection.
6. Defer Q bands until separately supported. The same-date Bernoulli outcomes
   do not reveal a date's true conditional CDF. Preserve coherent masses and
   common Q across strikes; do not infer bands from rho or interval miss rates.

Existing 2021-2025 history has already informed development. This can support a
retrospective research assessment, not independent production qualification.
Keep 2026 entirely closed, including lookbacks. Saved checkpoints avoid refits
on their supported dates; never use a later checkpoint on an earlier date.

**Status:** explanation and agreed methodological direction only. No numerical
PatchTST rho, calibrated bands, optimizer run or trading backtest was produced
by this addition. Computing the selected-policy criterion requires actual
candidate/LP decisions; it is not a forecast-format-only check. The remaining
instrument, data and bounded-execution contract is recorded in the
[Claude handoff](../memos/2026-10-08-claude-calibration-backtest-handoff.md).
Earlier restrictions on MIO execution and trading backtests remain unchanged.

## What the real runs show

The [causal calibration memo](../memos/2026-09-30-causal-decision-region-calibration-and-robust-condor.md)
ran the budget-only optimizer on 26 SPY/QQQ forecasts. Only five had enough
settled history to set a budget; three traded and two chose no trade. Mean
realized P&L was −\$0.14 per share. This is a historical research result, not a
validated trading rule.
