# Basic iron-condor optimization formulation

**Status:** research formulation  
**Scope:** select one long put, one short put, one short call, and one long call
for a single underlying and expiry. This document defines the smallest useful
model; it does not authorize trading.

## Inputs

At entry time (t), all candidate contracts share expiry (T). Let (S_T)
have the physical predictive CDF (F_t(s)=P(S_T\le s\mid\mathcal I_t)).

For each put (i\in\mathcal P) and call (j\in\mathcal C), the inputs are:

- strike (K_i) or (K_j);
- executable bid (b_i) or (b_j);
- executable ask (a_i) or (a_j); and
- contract multiplier (M), normally 100 for ETF options.

Precompute expected intrinsic values from the CDF:

\[
v_i^P=E[(K_i-S_T)^+]
     =\int_{-\infty}^{K_i}F_t(s)\,ds,
\]

\[
v_j^C=E[(S_T-K_j)^+]
     =\int_{K_j}^{\infty}(1-F_t(s))\,ds.
\]

These are constants when the optimizer runs.

## Decisions

Use binary variables:

- (x_i^{LP}=1) when put (i) is the long put;
- (x_i^{SP}=1) when put (i) is the short put;
- (x_j^{SC}=1) when call (j) is the short call; and
- (x_j^{LC}=1) when call (j) is the long call.

Exactly one contract is selected for each leg:

\[
\sum_{i\in\mathcal P}x_i^{LP}=1,\qquad
\sum_{i\in\mathcal P}x_i^{SP}=1,
\]

\[
\sum_{j\in\mathcal C}x_j^{SC}=1,\qquad
\sum_{j\in\mathcal C}x_j^{LC}=1.
\]

Let \(\delta>0\) be the minimum strike increment. Enforce the iron-condor
ordering:

\[
\sum_i K_i x_i^{LP}+\delta\le\sum_i K_i x_i^{SP},
\]

\[
\sum_i K_i x_i^{SP}+\delta\le\sum_j K_j x_j^{SC},
\]

\[
\sum_j K_j x_j^{SC}+\delta\le\sum_j K_j x_j^{LC}.
\]

Thus (K_{LP}<K_{SP}<K_{SC}<K_{LC}).

## Objective

A long option is purchased at its ask and a short option is sold at its bid.
The expected profit per share of each possible leg is therefore:

\[
\begin{aligned}
LP_i &: v_i^P-a_i,\\
SP_i &: b_i-v_i^P,\\
SC_j &: b_j-v_j^C,\\
LC_j &: v_j^C-a_j.
\end{aligned}
\]

The basic mixed-integer linear program is:

\[
\max_x\;M\left[
\sum_i x_i^{LP}(v_i^P-a_i)
+\sum_i x_i^{SP}(b_i-v_i^P)
+\sum_j x_j^{SC}(b_j-v_j^C)
+\sum_j x_j^{LC}(v_j^C-a_j)
\right].
\]

Equivalently, it maximizes:

\[
\text{initial executable credit}
-E_F[\text{terminal intrinsic loss}].
\]

Transaction fees can be subtracted from each selected-leg coefficient. Because
the CDF integrals and executable prices are precomputed constants, the core
model is linear in binary decisions.

## Minimal practical constraints

The mathematical core can select economically unattractive wide-wing trades.
A first usable research version should also require:

1. positive minimum net credit;
2. maximum put-side and call-side width;
3. maximum defined loss or margin requirement;
4. minimum bid, quote size, open interest, and acceptable spread;
5. no stale, crossed, adjusted, or otherwise ineligible contract; and
6. a declared limit on probability of loss or expected shortfall.

Return on capital is not the same objective as dollar profit. It may be handled
by enumerating an allowed capital budget, or by a separately validated
fractional/mixed-integer formulation. Tail calibration of (F_t), executable
prices, dividends, assignment, fees, and prospective shadow validation remain
deployment gates.
