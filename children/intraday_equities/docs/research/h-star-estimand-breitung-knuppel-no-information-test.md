# Question

What is a *rigorous* definition of maximum confident horizon H, and which published tests actually identify it? The previous note mixed practitioner IC-decay heuristics with HAC; that is not a theorem.

# Finding

## 1. Estimand

Fix a loss L, a forecast Ŷ_{t+h|t} made from information I_t, and an uninformative benchmark (typically the unconditional mean μ, or a recursive/rolling mean known at t).

Breitung and Knüppel (2021, *Journal of Applied Econometrics*), “How far can we forecast?”, define a maximum informative horizon h* by the **no-information** null, under quadratic loss:

\[
H_0(h):\quad
\mathbb{E}\bigl[(Y_{t+h}-\hat Y_{t+h|t})^2\bigr]
\;\ge\;
\mathbb{E}\bigl[(Y_{t+h}-\mu)^2\bigr]
\quad\text{for all }h>h^*.
\]

If Ŷ is exactly the conditional mean, this is the same as \(\mathbb{E}[Y_{t+h}\mid I_t]=\mu\) (constant mean). If Ŷ is noisy or estimated, those two nulls split:

- Mincer–Zarnowitz \(Y_{t+h}=\alpha+\beta\hat Y_{t+h|t}+v\).
- \(\beta=0\): no linear map of Ŷ beats the mean (uncorrelated).
- \(\beta\le 1/2\): Ŷ is no better than an equal-weight mix with the mean (true no-information for MSPE). Alternative is \(\beta>1/2\).

**H is not “farthest |IC| within 1 SE of the peak.”** That is an ad hoc location on a curve. The academic object is the **largest h at which we still reject no-information**, under a stated loss and a stated benchmark.

Failing to reject is not proof of no skill (Breitung–Knüppel, p. 373). For a consistent *selector* of h* they require the test size α → 0 (e.g. a BIC-like critical value κ log n). A fixed 5% sequential rule is a *test sequence*, not a consistent estimator of h*.

## 2. How they pick h* (and the assumption it needs)

Walk h = 1, 2, … with a **consistent** test of H_0(h). Stop at the first non-rejection. Set h* to the last rejected horizon.

This stop-at-first-fail is justified only if **uninformative at h implies uninformative at every larger horizon.** They cite Patton and Timmermann (2012, §2.2): for a *scalar stationary series under MSPE*, forecast-error variance is monotone in h. Then the hypotheses are nested in that direction and you may stop.

**That monotonicity is not a theorem for rank IC of a trading signal.** A reversal signal can be informative at 5 minutes, dead at 30, and informative again (or sign-flipped) at a session. A hump-shaped IC(h) violates the nesting. If we do not assume nesting, sequential stop is invalid; we need a **joint** test over a set of horizons (Quaedvlieg 2021 uniform SPA) or we must pre-declare a one-sided grid and live with the assumption.

## 3. Inference details the IC note skipped

- Overlap: h-step errors are MA(h−1) even under the null (Hansen–Hodrick 1980). HAC lag is not `1/√n_rows`.
- Nested benchmark: comparing a model to the mean is nested. Naive Diebold–Mariano is wrongly sized. Clark–West (2007) MSPE-adj / encompassing t, or Breitung–Knüppel’s nested DM, are the relevant statistics.
- Estimated Ŷ: West (1996) vs Giacomini–White (2006). GW keeps estimation error in the sampling experiment (rolling/recursive *method*). A single frozen train / single val split describes **one historical episode**; it is not the GW experiment.
- Long overlapping horizons can look more predictable than they are (Boudoukh, Richardson, Whitelaw). That is why “farthest h that still has a tiny t-stat” is a known trap.

## 4. Why CS Spearman with five names is a weak estimand

Grinold’s IC is CS corr then average over t. Asymptotics want either large N or a well-behaved time series of IC_t. Spearman on N = 5 is a discrete 5-point correlation. Power is in T, after overlap. Fama–MacBeth-style t-stats with a tiny cross-section are not a free lunch (Petersen 2009 on panel SEs; Shanken-style corrections are for a different parameter).

The **portfolio** this child actually trades is a single zero-investment return series. That series is the loss-relevant Y. Testing whether the book’s overlapping h-period return has positive mean (HAC, lag ~ h/step), or whether a Mincer–Zarnowitz of the *implemented* score vs that return rejects no-information, is the object that matches the product. Five separate H_i are five applications of the same test plus a multiple-testing correction (Holm), not a richer theory. Mixed label_leads in one rank-based optimizer remain ill-posed.

## 5. What would be rigorous *here*

State, before looking at val:

1. **Target.** One frozen Ŷ_t (one model, one training target). For each h, the outcome is either (A) each name’s residual return t → t+h, or (B) the book’s implemented return t → t+h. (B) is the decision loss. (A) is a diagnostic.
2. **Null.** Breitung–Knüppel no-information vs the mean (or vs zero for a dollar-neutral book), **signed**. Quadratic or a pre-declared portfolio loss. Not |Spearman|.
3. **Test.** Encompassing / MZ HAC t (β > 1/2 or β > 0), Clark–West if nested, overlap lag h−1. Optional: rolling-window GW if we re-estimate.
4. **h*.** Sequential from short h upward **only if we accept Patton–Timmermann monotonicity for this loss**. If we do not, stop using “first fail” and instead report the set {h : H_0(h) rejected} with a joint error rate (Holm on the grid, or Quaedvlieg uSPA on a pre-registered band).
5. **α.** Fixed 5% for a *description* of this val window; shrinking α if we claim a consistent selector.
6. **Confirm** on a later window (Test A). Do not search Ŷ on the same val.

Until those six are written down, H is not identified. The last LightGBM |IC| lock is not an estimator of h*.

# Sources

- Breitung, J. and Knüppel, M. (2021), “How far can we forecast? Statistical tests of the predictive content,” *Journal of Applied Econometrics* 36(4), 369–392. **This is the paper that defines and tests h*.**
- Patton, A. and Timmermann, A. (2012), forecast-error variance increasing in horizon (cited by BK for monotonicity).
- Mincer, J. and Zarnowitz, V. (1969), evaluation regression.
- Diebold, F. and Mariano, R. (1995); Clark, T. and West, K. (2007); Clark, T. and McCracken, M. (2001); West, K. (1996); Giacomini, R. and White, H. (2006, *Econometrica*).
- Hansen, L. and Hodrick, R. (1980); Newey, W. and West, K. (1987); Hodrick, R. (1992).
- Boudoukh, J., Richardson, M., and Whitelaw, R., long-horizon predictability.
- Quaedvlieg, R. (2021), *JBES*, uniform/average multi-horizon SPA.
- Hansen, P., Lunde, A., and Nason, J. (2011), model confidence set; Foltas et al., horizon confidence sets (which *model* at each h, not h* itself).
- Grinold (1989); Fama and MacBeth (1973); Petersen (2009).
- Prior child notes: `max-confident-h-one-score-cs-ic-decay-hac-se.md` (heuristic); this note supersedes it as the identification argument.
