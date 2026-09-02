# Question

How should L (feature lookback) and H (forecast horizon) actually be
determined for `children/intraday_equities`, and what empirical test
should decide them? Specifically: is the fixed-alpha sequential
no-information walk (`max_informative_horizon`, ADR-0057/0058) a sound
estimator of a maximum informative horizon; what is the right
multiplicity family; should L and H be chosen jointly or sequentially;
is 1-SE MSPE the right selection criterion at near-zero SNR; and what
protocol would decide both defensibly on the ~3.4 years of post-COVID
5-minute data this child actually has?

No run was performed. Everything below is either a reading of the code
as it stands or arithmetic on numbers already recorded in
`docs/decisioning/` and `docs/research/`.

# Finding

**The horizon walk is not measuring a property of the market. It is
measuring the sample size.** Under the most likely alternative
(a short-lived edge diluted into a longer return) the walk's stopping
point has a closed form, `h* ~= 5 * rho_5 * sqrt(n) / z_alpha`, which
grows like the square root of the tape length and contains no
information the pair `(rho_5, n)` does not already contain. At the edge
this child has actually measured (+0.0073) that formula returns
**5.7 minutes**, so 233 of the 234 grid points cannot reject at any
alpha and are dead weight. Everything else follows from that.

## 0. The sample budget, stated once

From `run-hstar-cv-postcovid.json`: 20 folds x 63 calendar days ~= 43
RTH days = **860 RTH days** of out-of-sample coverage; 390 RTH minutes
/ 5 = **78 bars per name per day**; so **n ~= 67,000** five-minute rows
per name, `sqrt(n) ~= 259`.

Minimum detectable `|rho_h|` (one-sided 5%, 80% power), where a horizon
of h minutes overlaps `h/5` consecutive rows so `n_eff = 335,400 / h`:

    MDE(h) = 2.487 / sqrt(n_eff) = 0.00429 * sqrt(h)

| h (min) | 5 | 15 | 30 | 60 | 390 | 1170 |
|---|---|---|---|---|---|---|
| n_eff | 67,080 | 22,360 | 11,180 | 5,590 | 860 | 287 |
| MDE \|rho\| | 0.0096 | 0.0166 | 0.0235 | 0.0333 | 0.0848 | 0.147 |

The measured pooled validation IC on this exact sample is **+0.0073**
(`post-covid-h-cv-bounded-window-no-measurable-edge.md`, t = 1.18 over
20 folds). It is below the MDE at *every* horizon on the grid,
including the first one.

## 1. The sequential walk for H is not sound here

### 1a. GO is one test, not 234

`max_informative_horizon` returns `h_star = None` unless the *first*
row rejects, and `_walk_no_information_series` sets
`go = 1.0 if h_star is not None else 0.0`. So the GO decision is
decided entirely by the `lead=5` test. The remaining 233 tests only set
H's *value*. This is deliberate (the `NoInformationScan` docstring says
"GO iff the reject run starts at the first lead") but it means the walk
buys no evidence for the gate and spends the entire multiplicity budget
on a quantity that never gates anything.

### 1b. Under dilution, h* is a power statistic with a closed form

Let a one-shot 5-minute edge have correlation `rho_5` with the 5-minute
return, and let the h-minute return be the sum of `h/5` such returns.
Then `rho_h = rho_5 * sqrt(5/h)`, and the Clark-West t (which is
`corr * sqrt(n_eff)` to first order) is

    t_h = rho_5 * sqrt(5/h) * sqrt(5n/h) = rho_5 * sqrt(n) * (5/h)

`t_h` decays as **1/h** for reasons that have nothing to do with
information running out. Setting `t_h = z_alpha` gives

    h* = 5 * rho_5 * sqrt(n) / z_alpha = 787 * rho_5   (minutes, this sample)

* `rho_5 = 0.0073` (measured) -> h* = **5.7 min** -> only lead 5 can reject.
* `rho_5 = 0.02` -> h* = 16 min. `rho_5 = 0.05` -> h* = 39 min.
* Doubling the tape multiplies h* by sqrt(2).

An estimator whose value grows without bound in T is reporting T, not a
horizon. This is the concrete form of the docstring's own admission
that a fixed-alpha sequence "is not a consistent selector".

### 1c. Under persistence, h* is the grid endpoint

If instead the predictable component is a drift that survives the whole
window, `rho_h = rho_5 * sqrt(h/5)` and `t_h = rho_5 * sqrt(n)` is
**flat in h**, so the walk rejects everywhere and h* = `lead_stop` =
1170 by construction. Combined with 1b: **h_star is effectively
bimodal** — about 5, or the grid endpoint. It carries roughly one bit,
and that bit is "does the signal accumulate or dilute", which is a
question better asked directly.

### 1d. Non-monotone predictability breaks the stop rule in both directions

Breitung and Knuppel's stop-at-first-failure is licensed by
Patton-Timmermann's result that MSE is increasing in horizon for a
*scalar stationary series under squared-error loss* — a rationality
bound on an optimal forecast, not a theorem about a fitted tree's rank
correlation with a traded residual. If IC(h) is hump-shaped or
sign-flipping (reversal at 5 min, momentum at a session), the
hypotheses are not nested and the walk is invalid: it truncates at the
first dip and can never see the second lobe. `max_informative_horizon`
explicitly ignores later rows after a fail, so the failure is silent.

The reverse failure is worse. Adjacent leads share `(h-5)/h` of the
same overlapping rows, so consecutive p-values are near-perfectly
dependent. The "run length" is therefore the **first-passage time of a
very smooth random function across a fixed threshold**, whose sampling
distribution is enormously dispersed. One lucky draw persists across
dozens of consecutive leads; one unlucky one truncates. So h_star is
simultaneously unstable and, conditional on surviving, upward biased.

### 1e. The HAC is asking for inference the sample cannot supply

`lags = lead // 5 - 1` reaches **233** Bartlett lags on a fold
validation window of ~3,350 rows, and `newey_west_mean` compares the
result to a **standard normal** (`_norm_sf`). At `b = 233/3350 ~= 0.07`
Kiefer-Vogelsang fixed-b asymptotics say the normal critical value is
wrong; Lazarus-Lewis-Stock-Watson's own Bartlett recommendation for
T = 3,350 is `S = 1.3*sqrt(T) ~= 75` **with** fixed-b critical values —
which directly contradicts the MA(h-1) requirement of 233. When the
bandwidth a consistent HAC needs exceeds the bandwidth any HAC can
support, the answer is not a better kernel; it is a shorter horizon or
a longer sample. Separately, Hodrick (1992) and Ang-Bekaert (2007) show
Newey-West with overlapping returns is severely undersized in small
samples and the Hodrick 1B estimator is materially better sized.

### 1f. Better estimator of a maximum informative horizon

There is no good point estimator at this sample size, and pretending
otherwise is the error. The defensible objects, in order of preference:

1. **Do not estimate H statistically.** H is a product parameter, not a
   population parameter: it is the holding period at which expected
   edge exceeds round-trip cost (Garleanu-Pedersen; Novy-Marx-Velikov).
   Fix it from the cost model, then run *one* test at that H.
2. **A horizon confidence set, not a point.** Report
   `{h : H_0(h) rejected}` with a joint error rate. This is exactly
   Quaedvlieg's uniform/average SPA over a forecast path plus the
   multi-horizon Model Confidence Set (Hansen-Lunde-Nason). It gives an
   honest "we can speak about h <= 30, nothing beyond" instead of a
   spuriously precise 470.
3. **If a point is mandatory**, use Breitung-Knuppel's *encompassing
   regression* form (regress `y_{t+h}` on `(yhat - mu)` and test the
   slope) with a shrinking alpha, and pre-declare the alpha schedule.
   Note this changes the estimand: CW / encompassing tests
   `beta > 0` (does yhat correlate at all), whereas "the forecast is
   actually useful under MSPE" is `beta > 1/2`. Since the tree is
   trained at lead 5 and scored against y(h), it is not the conditional
   mean of y(h), so `beats_mean` (raw MSPE) will essentially never
   fire; only the CW statistic ever moves. That should be stated in the
   decision document, because "H = 470" currently reads as "the
   forecast is useful out to 470 minutes" when what was tested is "the
   forecast is not exactly orthogonal to the 470-minute return".

## 2. Multiplicity: the family is smaller than it looks, and it is not the binding constraint

**The wrong family** is 234 leads x N names x 40 folds. Folds are not
40 replications of a hypothesis; they are 40 re-estimations of one
model inside one out-of-sample path. Giacomini-White is explicit that
the rolling scheme *is* the sampling experiment — the object of
inference is the whole OOS path, not each window.

**`go_frac` is a vote count.** Counting fold-level rejections and then
taking `select: max` over folds is exactly the procedure Hedges and
Olkin (1980) showed to be *inconsistent*: when per-study power is below
1/2, the probability of reaching the right conclusion **decreases** as
studies are added. With per-fold MDE around 0.03 against a true effect
near 0.007, per-fold power here is roughly the size of the test. The
research note already caught the symptom ("go_frac takes two distinct
values here, so the declared winner carries no information"); the
disease is the vote count itself.

**The right family** is `{name} x {pre-declared horizon}`, evaluated
once on the concatenated OOS path. Fold aggregation happens *inside*
the statistic (more rows), never *across* it (more tests).

**What multiplicity costs, quantitatively.** Over the full 234 x 5 =
1,170 grid, Bonferroni gives `alpha = 4.3e-5`, `z = 3.93` against
1.645. At h = 5 the MDE moves 0.0096 -> 0.0184: a 1.9x effect-size
penalty, equivalently a 3.7x sample-size penalty. Over the 40
fold x name gate tests the Bonferroni `z` is **3.02** — which lands
almost exactly on Harvey-Liu-Zhu's t > 3.0 hurdle for a new factor, a
useful sanity anchor.

The conclusion is counterintuitive and worth stating plainly:
**multiplicity is not what is killing this child.** Even full
Bonferroni over 1,170 tests leaves h = 5 detectable at
`|rho| > 0.018`. What kills it is the horizon grid: no correction, and
no correction policy, can rescue h = 1170, where the MDE is 0.147.
Shrinking the family from 1,170 to 25 buys about 0.005 of MDE at h = 5.
Dropping horizons above 30 minutes buys the entire experiment.

**Correction to use.** The tests are strongly positively dependent
(nested overlapping windows within a name; a common market factor
across names). BH is valid under positive regression dependence
(Benjamini-Yekutieli 2001) which is plausible here, and is already
registered (`bh`). The genuinely better tools — Romano-Wolf stepdown,
Hansen's SPA, White's Reality Check — exploit that dependence via a
joint bootstrap and would be materially more powerful. **Toolkit gap,
stated precisely:** `register_correction`'s contract is
`fn(pvalues, alpha) -> {name: bool}`, i.e. p-values only. Romano-Wolf
and SPA need the *bootstrap replicate matrix*, which that signature
cannot see. So stepdown cannot be added as a correction; it would have
to be a statistic-level change. Until then, use `bh` and say so.

## 3. Sequential H-then-L is not defensible; but neither is a joint grid on val

The current design (H at a fixed 66-feature set, then L at the locked
H) is one step of coordinate descent from an arbitrary starting point.
That is only valid if the loss surface separates — if `L*` does not
depend on H. There is strong prior reason to think it does not: useful
memory scales with the forecast horizon (the whole design of HAR-type
models is horizon-matched components). If `L*(H)` is increasing in H,
a single coordinate step from `L_0` does not even reach a coordinate-
wise local optimum; it needs iteration to convergence.

The deeper problem is inferential, not optimisational. Choosing H on
the val folds and then choosing L on **the same** val folds makes the
final Clark-West t at `(H*, L*)` a maximum over a selection path, not a
t-statistic. That is the classic invalid post-selection inference
(Berk-Brown-Buja-Zhang-Zhao 2013; Taylor-Tibshirani 2015). Inoue and
Rossi (2012) make the same point for exactly this knob — they show that
choosing a window size to maximise measured performance is data
snooping across window sizes, and propose tests that are robust to the
choice instead.

**Recommendation: neither order. Change the status of the two knobs.**

* **H is not selected.** It is fixed in advance from the trading
  problem, from the power table in section 0, or both. Pre-declare at
  most a handful of horizons and test them jointly.
* **L is a nuisance hyperparameter.** Select it inside the fold's inner
  train holdout — the same place `hpo_space` already lives in
  `run-hstar-cv-postcovid.json` — never on fold validation. Then L
  costs nothing in the outer multiplicity family, and the outer test is
  a genuine single hypothesis at a fixed H.

If a joint (H, L) answer is wanted anyway, do it as Inoue-Rossi
suggest: a statistic that is *robust to* the choice (sup or average
over the pre-declared L grid) rather than a statistic *at* a selected
point.

## 4. The selection criterion — and a code/doc gap

### 4a. What the code actually does (not what the documents say)

`hstar-go.md` and `framework.md` both declare: "Pick the shortest L
within 1 SE of the best mean fold MSPE (not |IC|)." **That rule is not
implemented anywhere.** `_lookback_verdict` is:

```python
peak = max(curve, key=lambda row: abs(row["ic_val"]))
thresh = abs(peak["ic_val"]) - peak["se"]
within = [row for row in curve if abs(row["ic_val"]) >= thresh]
picked = min(within, key=lambda row: row["lookback"])
```

MSPE appears nowhere in `LookbackScan` — the curve rows carry
`ic_train`, `ic_val`, `n_train`, `n_val`, `se`, `selected` and nothing
else. And the node is single-fold; the walk-forward driver's
`objective`/`select` picks a *winning fold*, it has no 1-SE
aggregation. So there is no "mean fold MSPE" anywhere in the pipeline.
This is a **fourth declared-knob-vs-runtime gap**, beside the three in
`three-declared-knobs-that-did-nothing-or-the-opposite.md`.

Two further defects in the rule as coded:

* **It is sign-blind.** `abs(row["ic_val"])` means a *negative* IC can
  win, and did: `decision-hl-scan.md` locked H = 470 and L = 120 at a
  validation rank IC of **-0.0825**. A selector that cannot tell an
  edge from an anti-edge is not a selector.
* **The SE ignores overlap.** `_ic_se(n) = 1/sqrt(n-1)` is the iid null
  SE. At `n_val = 15,675` that is 0.0080. At lead 470 the rows overlap
  94-deep, so `n_eff ~= 167` and the honest SE is **0.0777** — 9.7x
  larger (and larger still once the five names' cross-correlation is
  counted). Redo the recorded scan's 1-SE band with the honest SE and
  the threshold falls from 0.082 to 0.012; unless some L on the 30..120
  grid has |IC| under 0.012, every point qualifies and the rule returns
  `l_start` = 30. **As coded the 1-SE rule is a peak-tracker**, because
  the band is ~10x too narrow to regularise anything.

### 4b. Raw MSPE is the wrong ruler at this SNR — but not for the usual reason

The stated worry ("MSPE is biased toward the flattest model") is
directionally right but understates it. The population MSPE optimum is
the conditional mean, so shrinkage is not itself a bias. The fatal
problems are resolution and robustness:

* **Resolution.** `MSPE_mean ~= 2.6e-6`. An out-of-sample R^2 of 1e-4
  moves MSPE by 2.6e-10. Meanwhile fold-to-fold MSPE varies by *orders
  of magnitude* with the volatility regime — the pair walk recorded a
  fold at 5.16e-4, **170x** the normal 2-4e-6. A criterion whose
  between-fold variance is 10^6 times its between-model signal is
  measuring realized volatility, not skill. Any 1-SE band on mean fold
  MSPE will swallow the entire L grid and return `l_start`
  deterministically.
* **Robustness.** One unadjusted AAPL split bar accounted for
  essentially an entire fold's MSPE (5.20e-4 predicted vs 5.16e-4
  observed). Squared loss on 5-minute equity returns has no breakdown
  point worth the name.

### 4c. Do not select on the Clark-West *gap*; the CW *t* is fine

The CW mean gap is `2*(y-mu)*(yhat-mu)`. Scale `(yhat-mu)` by `c` and
the mean gap scales by `c` — so maximising the CW gap over models is
degenerate: it is maximised by inflating forecast magnitude, which is
free. Clark-West is a **test**, not a selection criterion.

The CW **t-statistic** is scale-invariant (mean and SE both scale by
`c`) and, to first order, equals `corr(y_h, yhat) * sqrt(n_eff)`. That
gives a clean unification worth writing down:

> **The right criterion is the signed correlation between yhat and y_h,
> studentized by a standard error that respects the overlap and the
> cross-sectional dependence. That single quantity is simultaneously
> the Clark-West t and a correctly-standard-errored IC.**

It is signed (fixes 4a), scale-free, regime-normalised (fixes 4b's
resolution problem), rank-robust if Spearman is used in place of
Pearson (fixes 4b's outlier problem), and — crucially — it is the *same
ruler the gate uses*, so selection and inference are not measured with
different instruments. Campbell-Thompson's out-of-sample R^2
(`1 - MSPE_model/MSPE_mean`) is an acceptable second choice for the
same regime-normalisation reason; Pesaran-Timmermann directional
accuracy is the robust fallback.

Trade-off to accept honestly: a t-statistic can be inflated by variance
shrinkage as well as by mean improvement, so report the raw mean gap
and `n_eff` alongside it, and never let a t rise on a shrinking
denominator alone.

## 5. The protocol I would actually run

### Stage 0 — pre-registration (before any val row is read)

One journaled document, hashed, containing: the horizon set, the L
grid, the estimator and its HPO space, the estimand, the criterion, the
multiplicity procedure, the decision threshold, and an explicit
**abandon rule**. It must also contain the sentence "we will not re-run
this with a different grid", because the credibility of everything
downstream rests on it (Harvey-Liu-Zhu; Bailey-Borwein-Lopez de
Prado-Zhu on backtest overfitting).

### Stage 1 — horizons chosen by a power calculation, not by the tape

From section 0, pre-declare **H in {5, 10, 15, 20, 30} minutes only**.
Justification, stated in the document: under dilution, detectability
requires `rho_5 > 0.00192 * h`, so h = 30 already demands `rho_5 >
0.058` — 8x the measured 0.0073 — and h = 390 demands `rho_5 > 0.75`,
which is impossible. **This dataset can speak about horizons under
about 30 minutes and about nothing longer.** Writing 1170 on a grid
that cannot reach 30 is the single largest methodological error in the
current design.

### Stage 2 — L as a nuisance hyperparameter

L grid `{30, 60, 120}` (3 points), selected **inside the existing inner
train holdout** alongside `hpo_space`, per fold, on train data only.
Fold validation never sees an L comparison. L then contributes zero to
the outer family.

### Stage 3 — splits

Keep `run-hstar-cv-postcovid.json` as-is: first val 2022-05-06, 20
folds, `step_days=63`, `val_days=63`, **`train_days=730` now actually
wired**, no fold touching COVID. Two changes:

* Purge, don't just embargo: drop training rows whose H-minute label
  window crosses the train/val cut. `embargo_days=5` was sized for
  `lead_stop`; at H <= 30 min one session is sufficient and the extra
  embargo is only costing rows.
* Blockers that must close first, all already documented: re-acquire
  with `adjustment: split` (or add the `|label| > 0.5` refusal as the
  cheap complement), and fix `LookbackScan`'s all-prior training so
  pass 2 is the same experiment as pass 1.

### Stage 4 — one statistic, on the concatenated path

Concatenate the 20 disjoint validation windows into **one time-ordered
OOS series per name** (~67,000 rows). The fold is a re-estimation unit;
the OOS path is the inference unit.

Score = `clark_west_series(y_h, yhat, mu=train_mean)` — already
exported. Then **cluster by trading week** and feed the existing
`stat_test` kind with `method: "studentized"`
(`cluster_bootstrap_t`). This is the important practical trick:
week-clusters absorb every overlap up to 1,950 minutes **inside a
cluster**, so the 233-lag Newey-West problem of section 1e simply
disappears — no bandwidth, no fixed-b critical value, no kernel choice.
172 week-clusters over the OOS path is comfortable for a bootstrap-t.

### Stage 5 — estimand: the book first, names as diagnostics

Primary hypothesis: the **book's** implemented return
(`intraday_equities-portfolio` output) has positive mean at the
pre-declared H. That is one series, so it satisfies the toolkit's
per-instrument doctrine without pooling anything, and it is the loss
the child actually pays. Per-name tests are the secondary/diagnostic
layer, corrected with `bh` over `{5 names} x {5 horizons}` = 25
hypotheses at FDR 10%. Pre-declare whether the target is the raw or the
SPY-residual return: a dollar-neutral book cannot monetise a market-
timing component, so testing raw returns can reject on something
untradeable.

### Stage 6 — the confirmation problem, stated honestly

To detect `rho = 0.0073` at 80% power needs
`n = (2.487/0.0073)^2 ~= 116,000` five-minute rows = **1,488 RTH days
~= 5.9 years per name**. Across five names whose effective independence
is perhaps 2 (large-cap intraday returns are strongly co-moving), that
is roughly 3 years per name — i.e. **the entire post-COVID sample, all
five names, in one pooled test, is the whole budget.** There is none
spare for a 234-lead grid, and none spare for a separate confirm block.

Consequently the declared confirm window (2026-03-01 -> 2026-05-31, 63
RTH days) has essentially no power: ~3,354 rows per name, ~6,700
effective across the book, MDE `|rho| ~= 0.030` — **4x the effect it
would be confirming.** Three honest options, to be chosen in advance:

1. Extend confirm to >= 1 year of untouched data, accepting the delay.
2. Demote confirm to a sign/sanity check with a pre-declared
   non-inferiority margin, and make the real decision on the 2022-2025
   evidence under BH, saying so before the run.
3. Change the estimand to one with a larger effect — a *conditional*
   edge in a pre-declared state (the note's volatility-dislocation
   hypothesis is the obvious candidate, and it is currently
   unfalsifiable because the split was chosen after seeing the folds).

### Stage 7 — the decision rule, one number

GO iff the book's week-clustered studentized bootstrap p at the
pre-declared H clears the pre-declared threshold, **and** at least one
name survives BH at FDR 10%. No `go_frac`. No `select: max` over folds.
No re-walk.

## What I would change first, in order

1. Delete the 234-lead grid; replace with `{5, 10, 15, 20, 30}`.
2. Stop using `go_frac` as a walk-forward objective — it is a vote count.
3. Fix `_lookback_verdict`: sign it, and give it an overlap-aware SE —
   or, better, move L into the inner HPO and delete the outer scan.
4. Reconcile `hstar-go.md`/`framework.md` with the code: the documented
   1-SE-mean-fold-MSPE rule for L does not exist.
5. Aggregate to one OOS path with week clusters; retire the 233-lag
   Newey-West path for anything above a few minutes.
6. Close the `adjustment: split` and `LookbackScan` all-prior blockers
   before any run intended to lock.

# Sources

## Literature

- Breitung, J. and Knuppel, M. (2021). "How far can we forecast?
  Statistical tests of the predictive content." *Journal of Applied
  Econometrics* 36(4), 369-392. Defines h* by the no-information null;
  two test classes (MSPE vs evaluation-sample variance, and MSPE vs the
  recursive mean) with comparison via the encompassing principle;
  asymptotics for n/T -> 0. https://doi.org/10.1002/jae.2817
- Patton, A. J. and Timmermann, A. (2012). "Forecast Rationality Tests
  Based on Multi-Horizon Bounds." *Journal of Business & Economic
  Statistics* 30(1), 1-17. The monotonicity bound (MSE increasing in
  horizon) that licenses stop-at-first-failure — a rationality
  restriction on an optimal forecast, not a property of an arbitrary
  fitted signal.
  https://public.econ.duke.edu/~ap172/Patton_Timmermann_bounds_JBES_2012.pdf
- Quaedvlieg, R. (2021). "Multi-Horizon Forecast Comparison." *JBES*
  39(1), 40-53. Uniform and average SPA over a forecast path, plus a
  multi-horizon Model Confidence Set. The right tool for a horizon
  *set*. https://doi.org/10.1080/07350015.2019.1620074
- Clark, T. E. and West, K. D. (2007). "Approximately normal tests for
  equal predictive accuracy in nested models." *Journal of
  Econometrics* 138(1), 291-311. The MSPE adjustment used by
  `clark_west_series`; a test, not a selection criterion.
- Diebold, F. X. and Mariano, R. S. (1995); Diebold, F. X. (2015),
  "Comparing Predictive Accuracy, Twenty Years Later," *JBES* 33(1).
  Why naive DM is mis-sized for nested comparisons.
- Giacomini, R. and White, H. (2006). "Tests of Conditional Predictive
  Ability." *Econometrica* 74(6), 1545-1578. The rolling/recursive
  *scheme* is the sampling experiment — inference is over the OOS path.
- Hansen, P. R. (2005). "A Test for Superior Predictive Ability."
  *JBES* 23(4), 365-380. Studentized statistic, sample-dependent null;
  more powerful and less sensitive to poor alternatives than the
  Reality Check. https://doi.org/10.1198/073500105000000063
- White, H. (2000). "A Reality Check for Data Snooping."
  *Econometrica* 68(5), 1097-1126.
- Romano, J. P. and Wolf, M. (2005). "Stepwise Multiple Testing as
  Formalized Data Snooping." *Econometrica* 73(4), 1237-1282. FWER
  stepdown exploiting joint dependence — needs the bootstrap replicate
  matrix, which the toolkit's p-value-only correction API cannot pass.
- Hansen, P. R., Lunde, A. and Nason, J. M. (2011). "The Model
  Confidence Set." *Econometrica* 79(2), 453-497.
- Benjamini, Y. and Yekutieli, D. (2001). BH validity under positive
  regression dependence. *Annals of Statistics* 29(4), 1165-1188.
- Hedges, L. V. and Olkin, I. (1980). "Vote-counting methods in
  research synthesis." *Psychological Bulletin* 88(2), 359-369.
  Vote counting is inconsistent: power falls as studies are added when
  per-study power is below 1/2. This is `go_frac`.
- Lazarus, E., Lewis, D. J., Stock, J. H. and Watson, M. W. (2018).
  "HAR Inference: Recommendations for Practice." *JBES* 36(4), 541-559
  (+ discussion, 560-575). Recommends Newey-West `S = 1.3*T^(1/2)` with
  Kiefer-Vogelsang fixed-b critical values; the textbook
  `S = 0.75*T^(1/3)` is too small. https://ebenlazarus.github.io/HARRecommendations.pdf
- Kiefer, N. M. and Vogelsang, T. J. (2005). Fixed-b asymptotics for
  HAC tests. *Econometric Theory* 21, 1130-1164.
- Newey, W. K. and West, K. D. (1987); Hansen, L. P. and Hodrick, R. J.
  (1980). The MA(h-1) overlap structure.
- Hodrick, R. J. (1992). "Dividend Yields and Expected Stock Returns:
  Alternative Procedures for Inference and Measurement." *RFS* 5(3),
  357-386. The 1B estimator; Monte Carlo evidence that it is better
  sized than Newey-West with overlapping returns.
- Ang, A. and Bekaert, G. (2007). "Stock Return Predictability: Is It
  There?" *RFS* 20(3), 651-707. Monte Carlo: Hodrick errors give the
  most conservative, best-sized statistics with overlapping data.
- Boudoukh, J., Richardson, M. and Whitelaw, R. F. (2008). "The Myth of
  Long-Horizon Predictability." *RFS* 21(4), 1577-1605.
- Inoue, A. and Rossi, B. (2012). "Out-of-Sample Forecast Tests Robust
  to the Choice of Window Size." *JBES* 30(3), 432-453. Choosing a
  window to maximise measured performance is data snooping across
  window sizes; use statistics robust to the choice.
  https://doi.org/10.1080/07350015.2012.693850
- Berk, R., Brown, L., Buja, A., Zhang, K. and Zhao, L. (2013). "Valid
  Post-Selection Inference." *Annals of Statistics* 41(2), 802-837;
  Taylor, J. and Tibshirani, R. (2015), *PNAS* 112(25), 7629-7634.
- Chen, Y. and Yang, Y. (2021). "The One Standard Error Rule for Model
  Selection: Does It Work?" *Stats* 4(4), 868-892. The 1-SE standard
  error estimate is biased 50-100% in either direction in practice and
  the rule often performs worse than plain CV; no evidence it
  consistently outperforms. https://doi.org/10.3390/stats4040051
- Hastie, T., Tibshirani, R. and Friedman, J. (2009). *ESL*, s7.10, on
  the 1-SE rule's origin (Breiman et al. 1984, CART).
- Campbell, J. Y. and Thompson, S. B. (2008). "Predicting Excess Stock
  Returns Out of Sample: Can Anything Beat the Historical Average?"
  *RFS* 21(4), 1509-1531. Out-of-sample R^2 as the regime-normalised
  criterion; tiny R^2 can still be economically meaningful.
- Welch, I. and Goyal, A. (2008). "A Comprehensive Look at the
  Empirical Performance of Equity Premium Prediction." *RFS* 21(4).
- Pesaran, M. H. and Timmermann, A. (1992). "A Simple Nonparametric
  Test of Predictive Performance." *JBES* 10(4), 461-465.
- Harvey, C. R., Liu, Y. and Zhu, H. (2016). "...and the Cross-Section
  of Expected Returns." *RFS* 29(1), 5-68. A new factor needs t > 3.0;
  framework allows correlated tests and publication bias.
- Bailey, D. H., Borwein, J. M., Lopez de Prado, M. and Zhu, Q. J.
  (2014). "Pseudo-Mathematics and Financial Charlatanism: The Effects
  of Backtest Overfitting on Out-of-Sample Performance." *Notices of
  the AMS* 61(5), 458-471.
- Lopez de Prado, M. (2018). *Advances in Financial Machine Learning*,
  ch. 7 — purged k-fold CV and embargo.
- Politis, D. N. and Romano, J. P. (1994). The stationary bootstrap.
  *JASA* 89(428), 1303-1313.
- Garleanu, N. and Pedersen, L. H. (2013). "Dynamic Trading with
  Predictable Returns and Transaction Costs." *Journal of Finance*
  68(6), 2309-2340. The trading horizon follows from alpha decay and
  costs, not from a hypothesis test.
- Novy-Marx, R. and Velikov, M. (2016). "A Taxonomy of Anomalies and
  Their Trading Costs." *RFS* 29(1), 104-147.
- Corsi, F. (2009). "A Simple Approximate Long-Memory Model of Realized
  Volatility." *JFEC* 7(2), 174-196. Horizon-matched lookback
  components — the reason L* is expected to depend on H.

## Repo

- `dskit/pipeline/stats.py` — `max_informative_horizon`,
  `no_information_test`, `clark_west_series`, `newey_west_mean`
  (`_norm_sf` normal critical value), `cluster_bootstrap_t`,
  `benjamini_hochberg`, `bonferroni`, `register_correction`
  (p-value-only signature).
- `dskit/pipeline/kinds_stats.py` — the `stat_test` kind
  (`method: studentized`, correction registry).
- `children/intraday_equities/intraday_equities/nodes.py` —
  `_walk_no_information_series` (`lags = lead//5 - 1`;
  `go = h_star is not None`), `NoInformationScan`, `LookbackScan`,
  `_lookback_verdict`, `_ic_se`, `_lookback_grid`, `PortfolioSelect`.
- `dskit/pipeline/driver.py` — walk-forward `objective`/`select`
  chooses a winning fold; no 1-SE aggregation exists.
- `children/intraday_equities/configs/universe.json` — `lead_start` 5,
  `lead_step` 5, `lead_stop` 1170 (234 leads), `l_start` 30,
  `l_step` 5, `lookback_stop` 120.
- `children/intraday_equities/configs/run-hstar-cv-postcovid.json` —
  20 folds, `train_days` 730, `first` 2022-05-06,
  `objective: $scan.metrics.go_frac`, `select: max`.
- `docs/decisioning/hstar-go.md`, `docs/decisioning/framework.md` —
  the declared (unimplemented) 1-SE mean-fold-MSPE rule for L.
- `docs/decisioning/decision-hl-scan.md` — H 470 / L 120 locked at a
  validation rank IC of -0.0825; `n_val` 15,675; 234 leads.
- `docs/research/post-covid-h-cv-bounded-window-no-measurable-edge.md`
  — mean val IC +0.0073, t 1.18, GO in 4/20 folds.
- `docs/research/three-declared-knobs-that-did-nothing-or-the-opposite.md`
- `docs/research/unadjusted-stock-splits-in-the-raw-alpaca-tape.md`
- `docs/research/h-star-estimand-breitung-knuppel-no-information-test.md`
  — the identification argument this note builds on.
