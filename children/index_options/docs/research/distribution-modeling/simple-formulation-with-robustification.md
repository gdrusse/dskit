# Simple Formulation with Robustification

**Status:** research proposal, 2026-09-30. **Scope:** one intact, one-lot iron condor for one underlying and expiry, held to settlement. This extends the [basic formulation](basic-iron-condor-optimization.md); it is not a trading rule.

## 1. Forecast and decision regions

At entry time $t$, the current model supplies an estimate of the *physical*
expiry-price CDF $\widehat F_t(s)\approx P(S_T\le s\mid\mathcal I_t)$, obtained by evaluating its
standardized-return curve at $\log(s/S_t)/a_t$. Use the frozen, governed research
CDF as the nominal forecast. Its 401-knot output is a full curve interface,
not a claim that all conditional probabilities are known precisely.

The decision thresholds are **strikes**, not option premiums. For ordered
strikes $K_{LP}<K_{SP}<K_{SC}<K_{LC}$, the two entire wing intervals
$[K_{LP},K_{SP}]$ and $[K_{SC},K_{LC}]$ determine expected terminal spread loss.
The premium determines the initial credit. Before looking at outcomes or model
rankings, freeze one entry-known eligible option chain, quote rules, strike/width
limits, and the resulting candidate set $\mathcal X_t$. Define a common
nonnegative strike-weight function $w_t(s)$ over **all eligible wing intervals**,
with a small positive global floor. This avoids choosing scoring regions from a
model's favored trade. The same eligibility and weight rule applies to every
candidate CDF.

The current archive identifies a quote date but not a source timestamp. Its
chain must therefore be treated as an after-close research snapshot: strikes
are valid diagnostic thresholds, while recorded bid/ask credits are indicative,
not proven same-day executable prices. Any deployment claim requires a
timestamped option/spot snapshot, declared decision clock, quote-age limit and
exact expiry/settlement match.

## 2. Nominal expiry payoff

For candidate $x$, let its executable credit per share be
$c_t(x)=b_{SP}+b_{SC}-a_{LP}-a_{LC}$ and let $f_t(x)$ denote fees and a declared
execution allowance per share. At expiry, its capped intrinsic loss is

```math
L_x(S_T)=(K_{SP}-S_T)^+-(K_{LP}-S_T)^+
          +(S_T-K_{SC})^+-(S_T-K_{LC})^+.
```

For any coherent CDF $F$, including CDFs with atoms,

```math
\mathbb E_F[L_x]
=\int_{K_{LP}}^{K_{SP}}F(s)\,ds
 +\int_{K_{SC}}^{K_{LC}}[1-F(s)]\,ds.
\tag{1}
```

Thus nominal expected profit is $M[c_t(x)-f_t(x)-\mathbb E_{\widehat F_t}L_x]$,
with multiplier $M$. Four endpoint probabilities alone do not determine (1).
The original leg-binary ordering, eligibility, width and capital constraints
remain the feasible set $\mathcal X_t$; candidate enumeration is an equivalent
first implementation for the one-lot case.

## 3. Adaptive distributional uncertainty

Use one shared ambiguity set $\mathcal U_t$ for every candidate on an entry
date. Build a sorted terminal-price grid $s_1<\cdots<s_m$ containing all
eligible strikes and enough interior points to resolve every eligible wing.
Add fixed support endpoints beyond all strikes and clip outer tails there.
Convert $\widehat F_t$ to nonnegative
nominal masses $\widehat p_{t,k}$ summing to one; preserve its CDF and payoff
integrals to a stated grid tolerance. Let $q_k$ be alternative masses and
$Q_j=\sum_{k\le j}q_k$, $\widehat Q_j=\sum_{k\le j}\widehat p_{t,k}$.

The proposed ambiguity set is the following polytope:

```math
\begin{aligned}
\mathcal U_t=\{q:\;&q_k\ge0,\quad \textstyle\sum_kq_k=1,\\
&\ell_{t,j}\le Q_j\le u_{t,j}\quad(j\in\mathcal J_t),\\
&\textstyle\sum_{j=1}^{m-1}\frac{s_{j+1}-s_j}{S_t}|Q_j-\widehat Q_j|
\le\rho_t\}.
\end{aligned}\tag{2}
```

$\mathcal J_t$ includes all eligible strikes and wing-interior grid points.
Set a declared finite modeling interval $[A_t,B_t]$ strictly beyond the lowest
and highest eligible strikes, and clip nominal/outcome prices to it. Every
candidate's bounded expiry loss is unchanged by that clipping. The final
constraint is the exact one-dimensional $W_1$ distance for masses on this
*clipped* price grid. Divide each gap by entry spot $S_t$ in (2) and interpret
$\rho_t$ as a dimensionless fraction of spot; the corresponding difference in
expected capped loss is at most $S_t\rho_t$ dollars/share. Absolute values
become linear constraints with one auxiliary variable per gap. Local CDF bands
$[\ell,u]$ express greater uncertainty where historical strike calibration is
poor; $\rho_t$ limits the total relocation of mass. The joint set enforces one
nondecreasing CDF, so an adversary cannot independently push each strike probability to an incompatible
extreme. Center bands on the discretized nominal CDF and validate that the set
is nonempty; zero-width bands and $\rho_t=0$ recover the nominal model.

Start with $W_1$ alone, then admit local bands only if their jointly tuned
intersection improves the prespecified risk/precision checks. Estimate budgets
from *previously settled*, chronologically out-of-fold forecasts. Estimate
local calibration discrepancies by comparing mean strike-event frequencies
with mean predicted probabilities in prespecified, entry-known groups; use
date-clustered uncertainty for each group's discrepancy, with a simultaneous
grid adjustment if coverage is claimed across many strikes. Pool by
entry-known index, requested tenor, moneyness/wing region and declared regime,
with shrinkage to a broader group when counts are thin. Resample bounded moving
blocks of consecutive entry dates, always keeping all same-date rows together.
Predeclare block lengths no shorter than the maximum requested horizon, report
the maximum label span and block size, and repeat at a longer sensitivity
length. This captures local overlap dependence without forming transitive
overlap components that could collapse most of history into one cluster.
Freeze the eligible universe, weights, grid, support, uncertainty rule and
no-trade choice before evaluating candidates. Because one realized settlement
does not reveal a date's conditional CDF, observed Bernoulli residual quantiles
are **not** confidence bands for that latent CDF. Existing aggregate PIT guards
are likewise not conditional tail guarantees. Treat (2) as calibrated stress
limits; audit robust expected-loss bounds against *average* realized losses on
held-out date blocks, never against each single realized payoff. No finite-sample
conditional-CDF coverage guarantee is asserted. For sparse regions, widen bands toward a
predeclared fallback or decline to trade.

## 4. Robust selection, including no trade

For each feasible candidate, let $\ell_{x,k}=L_x(s_k)$. Its worst plausible
expected loss is a linear program:

```math
R_t(x)=\max_{q\in\mathcal U_t}\sum_kq_k\ell_{x,k}.
\tag{3}
```

Select

```math
x_t^*\in\arg\max_{x\in\mathcal X_t\cup\{\varnothing\}}
\begin{cases}
M[c_t(x)-f_t(x)-R_t(x)],&x\ne\varnothing,\\
0,&x=\varnothing.
\end{cases}\tag{4}
```

Break ties toward no trade. The ambiguity set is fixed **before** selecting
$x$, so every candidate faces the same plausible distribution family. Retain
the basic model's executable quotes, defined-loss and liquidity filters. If
probability-of-loss or CVaR limits are imposed, evaluate their worst cases over
the same set; a nominal-only risk limit would undermine (4). The robust value
is a modeled lower bound conditional on (2), not a guarantee of realized profit.

The ambiguity set adapts across entry dates as more expiries settle. This is a
sequence of single-entry robust decisions. Rolling or early exits would require
a multistage formulation.

First enumerate eligible four-leg candidates and solve (3) for each; cache
payoff vectors and use one sparse LP matrix per entry date. Independently
compare nominal grid integration against direct CDF integration and refine the
grid until candidate values and rankings stabilize. Include every payoff kink
and control the remaining integration error: nearest-grid projection on a
maximum-gap $h$ changes any one-share condor loss by at most $h/2$ when the
grid rounds to its nearest point. More conservatively, use $h$ as a bound at
unequal edge gaps. The clipping and transport geometry are modeling choices;
vary support and mesh in sensitivity checks. Before any run, the ADR and JSON
must fix the maximum dollar/share mesh-error tolerance and normalize the common
score weights to integrate to one over the clipped support. If
enumeration is costly, dualize (3) and embed its linear counterpart in the
leg-binary mixed-integer model, retaining the same ambiguity set and no-trade
variable. Do not assume the simple nominal leg coefficients remain valid under
the max-min objective.

## 5. Re-evaluating and improving the CDF

The current pipeline primarily selects by equal index/actual-DTE-cell CRPS
and guards 5% PIT tails. It already reports fixed-cutoff strike Brier and
synthetic-condor loss error, but those fixed standardized cutoffs are not the
actual eligible chain's wing intervals. The latest protocol-qualified CDF's
2019–2025 CRPS gain over its reference is 0.0146%, with paired intervals
crossing zero; its condor-loss MSE is slightly worse (0.076746 versus
0.076699). This calls for a decision-region audit, not an inferred payoff gain.

Use the following staged protocol on identical, chronologically valid rows:

1. **Audit:** map every entry-known eligible strike to the saved CDF; compute
   Brier score $(F_t(K)-1\lbrace S_T\le K\rbrace)^2$ at strikes, plus the proper
   threshold-weighted CRPS
   $\int w_t(s)[F_t(s)-1\lbrace S_T\le s\rbrace]^2ds$. The global floor maintains
   whole-curve identification. Report by index, requested tenor, moneyness,
   wing width and regime, alongside full CRPS and the existing tail guard.
2. **Payoff diagnosis:** for predeclared eligible spread/condor templates,
   compare predicted versus realized capped terminal loss in dollars/share;
   report signed bias, MAE/MSE, interval coverage and ranking regret.
   Evaluate a frozen nominal selection rule and the frozen robust rule with
   executable-credit assumptions and no trade. Avoid choosing templates
   after seeing realized payoffs.
3. **Bounded model challenge:** retain the current CDF as control. First test
   strike-aware calibration and predeclared wing-weighted scoring/selection;
   only if diagnostics justify it, test a small wing-weighted training loss or
   additional entry-known chain features. Freeze candidate families, weights,
   global-CRPS tolerance, tail guards and model choice on development data.
   A positive score change alone is insufficient if payoff errors or calibration
   worsen materially.
4. **Robustness calibration:** estimate (2) with a separate settled-label
   calibration window or properly nested rolling folds. Tune band widths and
   $\rho_t$ only on prior dates, using prespecified loss/risk coverage and
   opportunity-cost criteria. Compare nominal, local-band-only,
   Wasserstein-only and joint-set ablations. Validate on genuinely new dates;
   2019–2025 has already informed research and is descriptive.

Entry-known weights preserve proper scoring because they do not depend on the
realized $S_T$; the positive floor also prevents unconstrained regions. Weights
must not be chosen from the optimizer's selected condor or revised after
inspecting outcomes. Keep actual DTE as evaluation metadata only; use requested
tenor and other available-at-entry features in forecasting. This remains an
expiry-payoff model; early exits, assignment, dividends and fill quality require
their separate gates.

## Sources and local evidence

- [Basic iron-condor optimization](basic-iron-condor-optimization.md) and
  [CDF/condor research](2026-09-28-predictive-cdf-condor-research.md): payoff
  identity and current problem scope.
- [Guard-aware selection memo](../../memos/2026-09-30-guard-aware-cdf-selection.md),
  [center-only transport memo](../../memos/2026-09-30-center-only-conditioned-transport.md),
  and [ADR-0198](../../../../../docs/review-evidence/ADR-0198.md),
  [ADR-0199](../../../../../docs/review-evidence/ADR-0199.md),
  [ADR-0200](../../../../../docs/review-evidence/ADR-0200.md),
  [ADR-0201](../../../../../docs/review-evidence/ADR-0201.md) review evidence:
  frozen model sequence, scores and caveats.
- [Gneiting and Ranjan, threshold-weighted proper scores](https://doi.org/10.1198/jbes.2010.08110):
  rationale for decision-region weighted CRPS.
- [Esfahani and Kuhn, Wasserstein DRO](https://doi.org/10.1007/s10107-017-1172-1):
  distributional ambiguity and tractable reformulations. Equation (2)'s
  one-dimensional discrete form and its use here are this proposal's design.
