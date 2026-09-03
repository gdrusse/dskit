# P5 skill rule: pooled Diebold-Mariano against the training mean

Date: 2026-09-03. Plan: docs/plans/2026-09-horizon-search.md, P5.

## Question

How should "the model beats a constant guess at horizon h" be judged so
every model, horizon and stock is scored identically — and why is
counting Clark-West rejections the wrong evidence?

## Finding

**Clark-West is the wrong tool for this decision.** It is built for nested
models and *adds back* the parameter-estimation penalty, so it rejects the
population null even when the model's realised MSPE is worse than the
mean's (their own simulations: 61% rejections at 10% where the big model
had the lower MSPE only 47% of the time). Keep it as a diagnostic column.

**Standard practice** (Campbell-Thompson 2008; Diebold 2015): report
out-of-sample R^2 against the historical mean and test the *forecast*, not
the model, with Diebold-Mariano on the squared-error loss differential,
Newey-West variance with Bartlett lag >= h_steps-1 for overlapping labels,
N(0,1) critical values. Diebold: for comparing forecasts DM "is the only
game in town". Our bounded 730-day training window is the Giacomini-White
setting where forecasts are treated as given, so no estimation-error
correction is owed. h_steps = h / row spacing (both in minutes).

**The rule (per model x horizon).**

1. Per fold f, stock s: over validation rows t, d_t = (y_t - mu_f)^2 -
   (y_t - yhat_t)^2 with mu_f the fold's training mean (positive = model
   better); q_f = mean_t (y_t - mu_f)^2. Persist d_t with timestamps.
2. Per fold: R2oos_f = mean(d)/q_f; DM_f = mean(d)/se_NW with lag
   L = max(h_steps-1, floor(4 (n/100)^(2/9))) and the Harvey-Leybourne-
   Newbold factor sqrt((n+1-2h_steps+h_steps(h_steps-1)/n)/n).
3. Per stock, pooled: concatenate d_t/q_f over the 20 folds in time order
   (disjoint 63-day folds, 5-day embargo: one weakly dependent series;
   /q_f makes folds scale-free). DM_pool = mean/se_NW, same lag rule.
   R2oos_pool = 1 - sum(y-yhat)^2 / sum(y-mu_f)^2 over all rows.
   Fold-cluster check: t_fold = mean_f(R2oos_f) / (sd_f/sqrt(20)), df 19.
4. Group: at each timestamp average d_t/q_f across the stocks present,
   then apply step 3 to that series (Qu-Timmermann-Zhu panel DM: the
   time-series HAC on the cross-sectional average absorbs dependence
   between stocks, so n=3 is fine).
5. PASS iff DM_pool >= 1.645 (one-sided 5%) AND t_fold >= 1.729
   (one-sided 5%, df 19). Report R2oos_pool with band +/- 1.645 se as
   the size of the win. Report Clark-West beside it, never instead.
6. Horizon answer: walk h = 1, 2, ... and stop at the first FAIL (the
   existing sequential rule, now fed DM p-values). P8 adds the
   many-attempts bar; this rule does not.
7. Never count per-fold rejections (A0035). Never compare MSPE across
   label definitions; every quantity above is a within-fold ratio, so the
   ADR-0059 vol-scaled label is fine.

**Folds:** power comes from total out-of-sample rows and span, not fold
count; 20 x 63 days is ample for DM_pool. t_fold has 19 df — stay >= 10.

**Existing 30 walks:** `no_information_test` already returns mspe_model,
mspe_mean, n per fold, so R2oos_f and t_fold are computable now; DM_pool
needs the d_t series, which the scan node must persist. Until then the
sign of the "gain" column is the honest verdict and the p column is not.

## Sources

- Campbell & Thompson 2008 (NBER w11468): R2_OS definition, eq. 1.
  https://www.nber.org/system/files/working_papers/w11468/w11468.pdf
- Clark & West 2007 (NBER t0326): MSPE-adjusted; rejects with higher MSPE.
  https://www.nber.org/system/files/working_papers/t0326/t0326.pdf
- Diebold 2015, JBES 33(1): DM compares forecasts; N(0,1) critical values.
  https://www.sas.upenn.edu/~fdiebold/papers/paper113/Diebold_DM%20Test.pdf
- Harvey, Leybourne & Newbold 1997, IJF 13: small-sample factor, t(T-1).
  https://pkg.robjhyndman.com/forecast/reference/dm.test.html
- Giacomini & White 2006, Econometrica: finite window, forecasts as given.
  http://fmwww.bc.edu/EC-P/wp572.pdf
- Qu, Timmermann & Zhu: panel DM, NW on the cross-sectional average (eq. 5-7).
  https://rady.ucsd.edu/_files/faculty-research/timmermann/Panel_DM.pdf
- Clark & McCracken 2001 / McCracken 2007: MSE-F, ENC-NEW need special tables; not used.
  https://users.nber.org/~confer/2000/si2000/mccracken2.pdf
