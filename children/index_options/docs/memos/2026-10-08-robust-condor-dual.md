# Robust condor: primal, dual and mixed-integer formulation

## TL;DR

The dual replaces the inner “worst plausible price distribution” calculation with linear constraints. Embedding them lets one mixed-integer linear program choose four eligible strikes, including the choice not to trade.

This memo publishes the mathematics reviewed in ADR-0255 at candidate 3f8ea439c96afa14f5776346738fa12746d82c54. It is a design record, not a completed solver, calibrated radius or backtest result.

## Contract and notation

The controlling sources are [the robustification formulation, §§3–4](../research/distribution-modeling/simple-formulation-with-robustification.md), its [worked explanation](../explanations/robust-condor-selection.md), [ADR-0255](../../../../docs/architecture/decision-log.md#adr-0255--equity-iron-condor-robust-selection-research-replay-proposed) and the [draft JSON](../../configs/run-equity-condor-robust-backtest.json).

For one ticker and entry date, omit the time subscript. The frozen replay uses a one-lot equity iron condor, expiry at 31 calendar days, all eligible strictly ordered strike combinations, and a grid consisting of the chain's distinct strikes. Own-date VWAP with an own-date close-based forecast is explicitly a same-day look-ahead assumption. Early assignment is ignored and labeled. No 2026 records may be read.

- $s_1<\cdots<s_m$: terminal-price grid in dollars/share; $S>0$: entry spot in the same units.
- $\widehat p_k$: fixed nominal probability at $s_k$, obtained from the frozen PatchTST CDF by mean-preserving placement on bounding strikes, with collapsed outer tails.
- $\widehat Q_j=\sum_{k\le j}\widehat p_k$, $J=\lbrace1,\ldots,m-1\rbrace$.
- $w_j=(s_{j+1}-s_j)/S$: dimensionless transport weight. This is **not** the separately normalized score-weight function used for forecast diagnostics.
- $q_k$: alternative probabilities; $Q_j=\sum_{k\le j}q_k$; $\rho\ge0$: fixed dimensionless W1 radius, a fraction of spot.
- $\mathcal B\subseteq J$: optional band locations, with fixed probability bounds $lo_j,hi_j$. The initial proposed run disables bands; their calibration is not supplied here.
- $L_k$: capped terminal condor loss at $s_k$, in dollars/share. $M$ is the contract multiplier; $e$ is the dollar fee per contract per entry leg.

Require nonnegative nominal masses summing to one, strictly increasing finite strikes and finite inputs. Admitted bands satisfy $0\le lo_j\le\widehat Q_j\le hi_j\le1$ and the reviewed joint-feasibility checks. The ambiguity set is common to all candidates and fixed before selection.

## 1. Inner primal: worst expected loss

For a fixed condor, let $u_j\ge0$ bound each absolute CDF displacement. The inner linear program is

```math
\begin{aligned}
R(L)=\max_{q,u}\quad &\sum_{k=1}^{m}L_kq_k\\
\text{subject to}\quad
& q_k\ge0,\qquad \sum_{k=1}^{m}q_k=1,\\
& \sum_{k\le j}q_k-u_j\le\widehat Q_j &&(j\in J),\\
&-\sum_{k\le j}q_k-u_j\le-\widehat Q_j &&(j\in J),\\
&\sum_{j\in J}w_ju_j\le\rho,\qquad u_j\ge0,\\
&\sum_{k\le j}q_k\le hi_j,\qquad
-\sum_{k\le j}q_k\le-lo_j &&(j\in\mathcal B).
\end{aligned}
```

The displacement inequalities imply $u_j\ge|Q_j-\widehat Q_j|$. This is the finite-grid W1 constraint in the formulation. Alternative strike probabilities must form one coherent CDF.

## 2. Exact LP dual

Assign a free multiplier $\lambda$ to $\sum q=1$; nonnegative $\alpha_j,\beta_j$ to the two displacement inequalities; $\gamma\ge0$ to the transport budget; and $\mu_j,\nu_j\ge0$ to upper/lower bands. Set $\mu_j=\nu_j=0$ off $\mathcal B$.

```math
\begin{aligned}
R(L)=\min_{\lambda,\alpha,\beta,\gamma,\mu,\nu}\quad
&\lambda+\sum_{j\in J}(\alpha_j-\beta_j)\widehat Q_j
+\gamma\rho+\sum_{j\in\mathcal B}(\mu_jhi_j-\nu_jlo_j)\\
\text{subject to}\quad
&\lambda+\sum_{\substack{j\in J\\j\ge k}}
(\alpha_j-\beta_j+\mu_j-\nu_j)\ge L_k
&& (k=1,\ldots,m),\\
&\alpha_j+\beta_j\le\gamma w_j &&(j\in J),\\
&\lambda\in\mathbb R,\qquad
\alpha,\beta,\gamma,\mu,\nu\ge0.
\end{aligned}
```

The Lagrangian coefficient of $q_k$ is
$L_k-\lambda-\sum_{j\ge k}(\alpha_j-\beta_j+\mu_j-\nu_j)$.
Its coefficient of $u_j$ is $\alpha_j+\beta_j-\gamma w_j$.
Both must be nonpositive when maximizing over nonnegative $q,u$; the remaining constant is the displayed dual objective. At the final grid point the sum is empty, giving $\lambda\ge L_m$.

With a nonempty ambiguity set and finite losses, the primal is feasible and bounded, so LP strong duality gives equality. Invalid or infeasible inputs must be refused. Dual variables have dollars/share units; dimensionless $\rho$ leaves $\gamma\rho$ in dollars/share.

## 3. Embed the dual in one MILP

Use binary $z$ for trade/no trade and binary $y_i^\ell$ for strike $i$ and leg $\ell\in\lbrace LP,SP,SC,LC\rbrace$: long put, short put, short call and long call. Set variables to zero where that option type is ineligible.

```math
\begin{aligned}
&\sum_i y_i^{LP}=\sum_i y_i^{SP}
=\sum_i y_i^{SC}=\sum_i y_i^{LC}=z,\\
&\sum_{i\ge k}y_i^a\le\sum_{i>k}y_i^b
\quad(k=1,\ldots,m;\ (a,b)\in
\lbrace(LP,SP),(SP,SC),(SC,LC)\rbrace),\\
&L_k(y)=\sum_i\left[
(y_i^{SP}-y_i^{LP})(s_i-s_k)^+
+(y_i^{SC}-y_i^{LC})(s_k-s_i)^+\right],\\
&z,y_i^\ell\in\lbrace0,1\rbrace.
\end{aligned}
```

Here $x^+=\max(x,0)$ is evaluated on fixed grid values, so $L_k(y)$ is linear in the binaries. The cumulative inequalities enforce strict ordering while retaining every eligible ordered combination.

Let $v_i^\ell$ be that particular put/call contract's VWAP and $h_i^\ell$ its configured haircut, both dollars/share. Net credit and entry fees per share are

```math
\begin{aligned}
c(y)&=\sum_{\ell\in\lbrace SP,SC\rbrace}\sum_i
(v_i^\ell-h_i^\ell)y_i^\ell
-\sum_{\ell\in\lbrace LP,LC\rbrace}\sum_i
(v_i^\ell+h_i^\ell)y_i^\ell,\\
f(y)&=\frac{4ez}{M}.
\end{aligned}
```

Maximize the following dollar objective subject to the binary selection constraints and **all dual constraints above with $L_k=L_k(y)$**:

```math
\max\quad M\left[
c(y)-f(y)-\lambda
-\sum_{j\in J}(\alpha_j-\beta_j)\widehat Q_j
-\gamma\rho
-\sum_{j\in\mathcal B}(\mu_jhi_j-\nu_jlo_j)
\right].
```

For fixed $y$, maximizing negative dual cost minimizes that cost, which equals the primal worst expected loss. Thus this is exactly the finite-grid robust selection objective. Radius, nominal CDF, bands and transport weights are constants during the solve; jointly optimizing them would change the problem. There are no products of decision variables.

For the draft's provisional $M=100$, $e=\$0.65$, $z=1$: $f=4(0.65)/100=\$0.026$ per share, and the objective subtracts exactly $\$2.60$. These are configured assumptions, not validated execution costs.

## 4. No trade and required checks

At $z=0$, all legs and losses are zero. All-zero dual variables are feasible and give objective zero. Weak duality prevents any other feasible dual from giving a positive objective for no trade.

After mathematically finding optimum $V^*$, minimize $z$ subject to the same constraints and objective at least $V^*-\tau$, where $\tau\ge0$ is the approved dollar tie tolerance. Zero tolerance breaks exact ties toward no trade; positive tolerance intentionally treats sufficiently small gains as ties. Implementation must retain both certificates and account for the primary solver's remaining gap; uncertified results skip.

Required post-approval tests compare the MILP against enumeration with a primal LP per candidate, with and without bands. They cover $\rho=0$ recovering nominal expected loss, empty-band refusal, no trade, unequal wings, leg-specific prices/eligibility, fees and projection payoff preservation. No new solver run was performed for this memo.

Increasing $\rho$ weakly enlarges the fixed-grid set and increases worst expected loss weakly; it cannot improve the best robust objective with everything else fixed. This does not prove any chosen radius is calibrated. Optional bands constrain the same common CDF and need separate evidence.

## Verification, limitations and handoff

The design at 3f8ea439 passed three independent GPT-6 reviews with zero Critical/Major/Minor/Nit, recorded in [RE-ENTRY](../../../../docs/RE-ENTRY.md). This memo makes that design inspectable; it neither approves ADR-0255 nor implements its proposed nodes.

Bounding-strike placement preserves nominal capped piecewise-linear payoffs, not the entire continuous CDF. A finite strike-supported ball does not literally contain continuous distributions. Whether to absorb the forecast-to-grid W1 offset into rho remains an owner decision. Calibration criterion/search, update phases, bands, mesh and score-weight tolerances remain separate gates; no numerical rho or confidence guarantee is invented.

Reproduce the contract from the candidate and draft JSON. Use published checkpoints only through a verified chronological schedule including training and monitoring-label availability. No market records, model fits, option-store copying, MIO execution or trading backtest occurred for this publication. Next: continue the owner's one-at-a-time design decisions, then obtain ADR/data-copy approval before remaining gates and tests-first implementation.
