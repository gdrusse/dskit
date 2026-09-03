# P8 bar: a bootstrap max over every attempt, plus a day-block scramble

Date: 2026-09-03. Plan: docs/plans/2026-09-horizon-search.md, P8.
Sits ON TOP of P5 (p5-skill-rule-pooled-diebold-mariano-against-the-training-mean.md).
P5 says how one cell is scored. P8 says how high one cell must score once
hundreds of cells were tried on the same data.

## Question

Tonight adds row spacings {1,5,10} x horizons {1,2,3,5,10,20,30,60} x three
price definitions x several feature blocks x several model settings. That is
hundreds of scores on ONE dataset, and neighbouring cells (h=2 and h=3, or
close and vwap) are almost the same test. What is the fair bar, and how do you
shuffle the answers when the answers overlap in time?

## Finding

### 1. Which correction fits

- **Bonferroni / Holm** control the chance of ANY false win, but treat h=2 and
  h=3 as two separate tries when they are nearly one. Correct, far too harsh
  here. Keep as a reported floor, not the rule.
- **Benjamini-Hochberg** controls the share of wins that are false. Valid under
  positive dependence (Benjamini-Yekutieli 2001, PRDS), which our grid has.
  **Benjamini-Yekutieli** is BH divided by c(K)=1+1/2+...+1/K (about ln K +
  0.577; K=200 gives 5.9), valid under any dependence. Harvey-Liu-Zhu use BY.
  A good SECOND lens for "which horizons look promising", the wrong tool for
  "we have found the horizon".
- **White's Reality Check (2000)** is the right SHAPE: many forecasts against
  one benchmark, one dataset, resampled in blocks so dependence is kept. But it
  is a single-step max test and it is wrecked by bad models, and we have many
  (the nets at -54%). Not used raw.
- **Hansen's SPA (2005)** is the Reality Check studentised and recentred, so
  hopeless cells stop draining power. Gives ONE p-value: "is anything in this
  grid better than the mean at all". Use it as the global gate.
- **Romano-Wolf StepM (2005, Econometrica; adjusted p-values 2016)** is the test
  we want. Studentised statistics, one shared block resample across ALL cells,
  so the true correlation between neighbouring horizons is carried exactly, and
  a stepdown loop that names WHICH cells win while holding the family-wise
  error at 5%. This is the primary bar.
- **Model Confidence Set (Hansen-Lunde-Nason 2011)** answers "which models are
  indistinguishable from the best", not "which beat the mean". Optional, for
  choosing among survivors. Not the bar.

Dependence handling is the whole argument: only the resampling procedures
(RC / SPA / StepM / MCS) learn from the data itself that h=2 and h=3 are one
test rather than two.

### 2. What finance says about the height of the bar

- **Harvey-Liu-Zhu (2016, RFS)**: with 316 published factors, and many more
  tried and never published, the honest hurdle for a NEW claim is **t > 3.0**,
  not 2.0; their Bonferroni/Holm cutoffs run about 3.6-4.0 and BY at 1% about
  3.4-3.7. They pick BY because it survives arbitrary dependence. t = 3.0
  one-sided is p = 0.00135, which is a Bonferroni 5% bar over 37 tries, so 3.0
  is roughly what a few dozen genuinely different attempts already costs.
- **Bailey-Lopez de Prado, Deflated Sharpe Ratio (2014)**: the expected best of
  N independent tries under a true null is
  E[max] = (1-g) Phi^-1(1 - 1/N) + g Phi^-1(1 - 1/(N e)), g = 0.5772.
  It is stated for Sharpe ratios but it is just the expected maximum of N
  standard-normal draws, and **under the null P5's DM statistic is N(0,1)**, so
  it transfers verbatim with SR replaced by DM. N=180 gives 2.73, N=240 gives
  2.83. That is the CENTRE of the luck distribution, so it is a floor, not a
  pass mark.
- **Lopez de Prado-Lewis (2019)**: N must be the number of EFFECTIVELY
  uncorrelated trials, found by clustering the trial correlation matrix, never
  the raw count.
- **Novy-Marx (2015, NBER 21329)**: picking the best k of n signals is about as
  biased as picking the best of n^k, and the required critical values are
  "several times" the usual ones. Our feature-block x model-setting search is
  exactly this.
- **Bailey-Borwein-Lopez de Prado-Zhu (2014)**: the probability of backtest
  overfitting goes to 1 as the number of configurations grows, whatever the
  true signal is. Counting attempts is not optional.

Does it transfer from a Sharpe ratio to a squared-error test? Yes. All of the
above are statements about the maximum of many noisy statistics. The only thing
that must hold is that each cell's statistic is roughly N(0,1) under its own
null, which P5's studentised Diebold-Mariano gives (Newey-West lag at least
h_steps-1, Harvey-Leybourne-Newbold factor). Nothing in the argument is
Sharpe-specific.

### 3. How to scramble a time series with overlapping answers

Plain row shuffling is wrong twice over: it destroys the minute-to-minute
autocorrelation AND it breaks the overlap between labels (an h=30 label shares
29 minutes with the next one). Romano-Tirlea (2020) show plain permutation is
not even approximately level under dependence, and that studentising the
statistic is what restores it.

**The exchangeable unit is a whole trading session (one RTH day).** A session is
self-contained for every horizon we test (h <= 60 min, and features never bridge
a tape gap), so moving a session moves every overlapping label with it. Sessions,
not minutes, not rows.

**Tier 1 - scramble the score, no refit (every cell, every night).**
P5 already requires the per-timestamp loss differential d_t. Build one matrix D
(rows = validation timestamps, columns = every cell in the family) and a session
id per row.
- Recentre each column (subtract its mean). This imposes the null exactly.
- For b = 1..B: draw one +1/-1 coin per SESSION, shared by every column and every
  stock; multiply each row by its session's coin; recompute each cell's DM with
  the same Newey-West lag; record the maximum across cells.
- This is Shao's dependent wild bootstrap with block weights: nothing is
  reordered, so within-session autocorrelation and label overlap are untouched,
  and because the coins are shared the cross-cell and cross-stock dependence is
  carried exactly.
- Cross-check with a recentred circular block bootstrap over sessions
  (Politis-Romano 1994 stationary bootstrap; Politis-White 2004 automatic block
  length on d_t, and if it exceeds one session use that many sessions per
  block). If the two critical values differ by more than 0.2, take the larger.
- **B = 10,000** (cheap: it is arithmetic on stored numbers). Minimum 2,000.
- Output: c* = 95th percentile of the maxima. Adjusted p for cell k is
  (1 + #{b: max_b >= DM_k}) / (1 + B); stepdown by dropping rejected cells and
  recomputing the maximum over what is left (Romano-Wolf 2016).

**Tier 2 - scramble the answers and refit (survivors only, ~100 runs).**
Inside each fold, list the RTH sessions of the training window and of the
validation window separately. Draw one permutation of the training sessions and
an independent one of the validation sessions. Give the label row at (session i,
minute m) the label computed from session pi(i) at minute m. Same permutation for
every symbol. Features, folds, embargo, seeds and HPO: unchanged.
- PRESERVED: within-session autocorrelation, the h-minute overlap, the
  time-of-day shape, day-level volatility clustering, and the cross-stock
  correlation at each minute.
- DESTROYED: only the link between the features at t and the return over
  [t, t+h]. That is exactly the null we want.
- Drop half-days from the permutation pool (unequal session lengths).
- **B = 100** runs. Two checks: the observed R2oos must beat the largest of the
  100 scrambled R2oos; and the 100 scrambled DM values must sit near mean 0 and
  sd 1. If they do not, the variance estimator is wrong and every p-value in the
  project is wrong. That check alone is worth the six hours.

### 4. Counting the attempts honestly

Report three numbers, always:
1. **K**, the literal count of cells in the family, cumulative over the whole
   horizon search, including the 30 walks already run and every failure. One
   family per outcome unit: JPM, LLY, XOM, and the group.
2. **K_eff by eigenvalues**: on the K x K correlation matrix of the d_t columns
   (all on the same timestamps, so it is directly computable), Li-Ji (2005):
   K_eff = sum_i [ 1{lam_i >= 1} + (lam_i - floor(lam_i)) ]. Equivalently
   cluster the columns and count clusters (Lopez de Prado-Lewis).
3. **K_eff implied by the bootstrap**: K_imp = 0.05 / (1 - Phi(c*)). This is the
   self-consistent one. It says how many genuinely independent tries our grid
   was worth, and it is the number the write-up should quote.

One free saving: P5's rule already walks h = 1, 2, 3, ... and stops at the first
failure. A pre-specified fixed sequence controls the family-wise error along
that axis with NO alpha spent, so the horizon axis is not what inflates K. Row
spacing, price definition, feature block and model settings are.

### 5. THE BAR (implementable)

Family F(u): every (row spacing, price definition, feature block, model config,
horizon) cell ever scored against outcome unit u. K = |F(u)|.
Statistic: P5's DM_pool, unchanged. Null imposed by the Tier 1 session sign-flip
and the recentred session block bootstrap above, B = 10,000.

A horizon PASSES for unit u only if ALL of:
1. **P5, unchanged**: DM_pool >= 1.645 and t_fold >= 1.729.
2. **Bootstrap max**: DM_pool >= max(c*, 3.0). c* is the 95th percentile of the
   scrambled maxima; 3.0 is Harvey-Liu-Zhu's floor, present so that a grid of
   near-identical cells cannot hand back a soft c*. For scale: Bonferroni 5% at
   K=180 is 3.45 and at K=400 is 3.66; the deflated-Sharpe expected maximum at
   N=180-240 is 2.7-2.8.
3. **Stepdown**: Romano-Wolf adjusted p <= 0.05. Identical to (2) for the top
   cell, more forgiving for the rest, still 5% family-wise.
4. **Size, not just sign**: R2oos_pool > 0 with its lower 5% band above 0.
5. **Tier 2 scramble**, once, for the winner: observed R2oos above all 100
   scrambled walks.

Secondary lens, reported but never used to certify: Benjamini-Hochberg at
q = 0.10 over the same family for the "worth another look" list; Benjamini-
Yekutieli (divide q by c(K)) for the arbitrary-dependence version.

The sentence to write: "JPM, h = 2: DM 3.7, adjusted p 0.02. Clears the bar for
187 attempts, worth about 19 independent ones. R2oos +0.08% [+0.02, +0.14]. None
of 100 scrambled walks reached it."

Two things this bar cannot do. It cannot repair a cell chosen after looking, so
the family count must come from the journal and not from memory. And if every
cell fails, that is the answer.

Ready implementations: arch.bootstrap.SPA, StepM and MCS take losses as a
T x k array plus a T-vector benchmark, with bootstrap="stationary" or
"circular", studentize=True, reps and block_size. StepM.superior_models gives
the FWER-controlled winners; SPA.pvalues gives the global gate.

## Sources

- White 2000, Econometrica 68(5), Reality Check for data snooping.
- Hansen 2005, JBES 23(4), A Test for Superior Predictive Ability: https://cdr.lib.unc.edu/downloads/zp38wf793
- Romano & Wolf 2005, Econometrica 73(4), StepM: https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1468-0262.2005.00615.x
- Romano, Shaikh & Wolf, multiple testing overview: https://home.uchicago.edu/~amshaikh/webfiles/multiplereview.pdf
- Clarke, Romano & Wolf 2020, Stata Journal, adjusted p-values: https://docs.iza.org/dp12845.pdf
- Benjamini & Yekutieli 2001, Annals of Statistics 29(4), FDR under dependence (PRDS, and the c(m) harmonic factor).
- Harvey, Liu & Zhu 2016, RFS 29(1), t > 3.0: https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF
- Bailey & Lopez de Prado 2014, JPM, Deflated Sharpe Ratio: https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
- Bailey, Borwein, Lopez de Prado & Zhu 2014, Probability of Backtest Overfitting: https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
- Lopez de Prado & Lewis 2019, Quantitative Finance 19(9), effective number of trials: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3167017
- Novy-Marx 2015, NBER 21329, Backtesting Strategies Based on Multiple Signals: https://www.nber.org/system/files/working_papers/w21329/w21329.pdf
- Politis & White 2004, Econometric Reviews 23(1), automatic block length: https://public.econ.duke.edu/~ap172/Politis_White_2004.pdf (correction: Patton, Politis & White 2009, https://public.econ.duke.edu/~ap172/Patton_Politis_White_2009.pdf)
- Shao 2010, JASA 105(489), The Dependent Wild Bootstrap: https://www.tandfonline.com/doi/abs/10.1198/jasa.2009.tm08744
- Romano & Tirlea 2020, Permutation Testing for Dependence in Time Series: https://arxiv.org/pdf/2009.03170
- Li & Ji 2005, Heredity 95, effective number of tests from eigenvalues: https://www.nature.com/articles/6800717
- arch multiple-comparison reference (SPA / StepM / MCS): https://bashtage.github.io/arch/multiple-comparison/multiple-comparison-reference.html
