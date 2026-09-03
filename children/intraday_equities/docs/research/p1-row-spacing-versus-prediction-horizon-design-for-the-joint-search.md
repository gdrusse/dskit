## Question

Rows every 5 minutes and a look-ahead of h minutes were fixed together.
Both are free on a 1-minute tape. What do practitioners and the literature
do about row spacing s versus horizon h, what does s < h cost (overlap),
and which cells of s∈{1,2,3,5,10} × h∈{1,2,3,5,10,15,20,30,45,60} should
run first, scored so that every cell is judged on the same instants?

## Finding

**1. Spacing and horizon are separate knobs everywhere in the literature;
nobody ties s to h.** Econometrics samples every bar and lets labels
overlap (Hansen–Hodrick 1980; Hodrick 1992), correcting standard errors
rather than thinning rows. Britten-Jones, Neuberger & Nolte show the
overlapping regression is strictly more efficient than the non-overlapping
one — the gain comes from the extra 1-bar innovations, not from the
inflated n. Practitioner precedent: Numerai rows weekly, targets 20 days
(overlap 4), purge = 8 eras (2× the overlap). Forecasts are *direct*
(one model per h), which Marcellino–Stock–Watson find more robust to
misspecification than iterating a 1-step model; keep direct. Event bars
(volume/dollar) change the clock rather than the spacing — defer; P2
covers the time-of-day part of that argument.

**2. Overlap arithmetic.** With s < h, adjacent labels share (h−s)/h of
their return, residuals are MA(h/s − 1), and the noise part of the label
has n_eff ≈ n·s/h. Current s=5 cells at H=20/30/60 already overlap 4–12×;
their per-fold t-stats assume independence and are overstated. Lopez de
Prado's uniqueness u_t = 1/c_t is constant (c_t = h/s) under regular
spacing, so uniqueness weights do nothing here — they matter only for
irregular (triple-barrier) labels. What does matter: **purge** training
rows whose label window crosses the test-fold start (last h minutes of
train) and **embargo** h minutes after each test fold. Boudoukh–
Richardson–Whitelaw: under the null, overlapping estimators at adjacent
horizons are ~perfectly correlated, so a smooth gain-vs-h curve is
expected by construction and is not 10 independent confirmations.

**3. Do dense rows help small models?** Theory says yes-but-bounded:
ridge gains the efficiency of the extra innovations and nothing more;
LightGBM sees h/s near-duplicate rows and its `min_child_samples`,
early stopping and leaf statistics act as if n were h/s times larger —
overfit risk, mitigated by scaling `min_child_samples` by h/s or
`bagging_fraction` ≈ s/h. The pure test of "more rows, zero overlap" is
h=1 at s=1 (5× the rows of s=5, no overlap); if that does not move the
H=1 gain, density is not the lever. Also: the training horizon need not
equal the scored horizon (Label Horizon Paradox, arXiv 2602.03395) — a
later axis, not this grid.

**4. Design.** *Hold fixed:* features (all lags at 1-minute resolution,
so s changes row density only, never feature content), label (ADR-0059
knobs), fold dates and as-of, hyperparameters (wave 1), stocks JPM/LLY/XOM,
models ridge + lgbm only (nets negative everywhere; 30 walks).
*Score every cell on the 30-minute lattice* — LCM of all five spacings —
the label y(t,h) is the same number for every s at a lattice instant, so
differences are model-only. Lattice labels do not overlap for h ≤ 30 →
plain SEs valid there; h=45/60 overlap once → HAC (Bartlett) lag 1.
Secondary lattice of 10 minutes for s∈{1,2,5,10} (3 does not divide 10).
*Compare cells pairwise* with a Diebold–Mariano test on paired loss
differentials at identical instants against the s=5 baseline cell —
paired at the same timestamps is far more powerful than per-cell t.
Panel across the three stocks: cluster by timestamp (Driscoll–Kraay).
*Wave 1 (≈24 cells):* s∈{1,5,10} × h∈{1,2,3,5,10,20,30,60}; s=5 has
six already. The s=h cells (1,1),(5,5),(10,10) are the no-overlap
references. *Wave 2:* s∈{2,3} only if s=1 beats s=5 anywhere; h∈{15,45}
only if the curve is not monotone between neighbours; LightGBM
`min_child_samples` scaled by h/s where s < h.
*Purge/embargo* h minutes at every fold boundary in every cell.

## Sources

- Hansen & Hodrick 1980; Hodrick 1992 — overlapping-observation SEs
  (https://alexchinco.com/standard-error-estimation-with-overlapping-samples/)
- Britten-Jones, Neuberger, Nolte, "Improved inference and estimation in
  regression with overlapping observations"
  (https://warwick.ac.uk/fac/soc/wbs/subjects/finance/faculty1/anthony_neuberger/improved.pdf)
- Boudoukh, Richardson, Whitelaw, "The myth of long-horizon
  predictability", RFS 2008 (https://www.nber.org/papers/w11841)
- Marcellino, Stock, Watson, direct vs iterated multistep, J. Econometrics
  2006 (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=771945)
- Lopez de Prado, AFML ch. 4 & 7 — uniqueness, purged k-fold, embargo
  (https://reasonabledeviations.com/notes/adv_fin_ml/)
- Numerai docs — weekly eras, 20-day targets, purge 8 eras
  (https://docs.numer.ai/numerai-tournament/data)
- "The Label Horizon Paradox" (https://arxiv.org/html/2602.03395)
- Alternative bars survey (https://hudsonthames.org/machine-learning-trading-essentials-part-1-financial-data-structures/)
