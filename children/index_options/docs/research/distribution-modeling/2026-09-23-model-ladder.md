## Question

Which model families should forecast the 30-60 day physical distribution of
index z (vol-standardized log-moneyness), and in what benchmark order?

## Finding

Ladder; each rung must beat the one below on the locked scores (A0002);
conformal recalibration may overlay any rung.

- **R0 naive:** (a) unconditional empirical z; (b) IV-implied lognormal,
  sigma = VIX x trailing realized/implied ratio. (b) is the hard bar: does
  anything beat the option market? numpy/scipy.
- **R1 HAR-RV scale + empirical z shape:** direct 30-60d variance target;
  then HARQ, HAR-J/SHAR. Range estimators (Parkinson/Garman-Klass) until
  intraday RV. statsmodels OLS + HAC.
- **R2 GJR/EGARCH skew-t + filtered historical simulation:** iterated daily
  paths; captures vol-return correlation (30-60d skew). `arch`. Realized
  GARCH / HEAVY only once intraday RV exists.
- **R3 direct shape:** quantile regression at strike quantiles; distribution
  regression (logit of 1{z <= c_k} over the strike grid); ordered logit.
  statsmodels. Enforce monotonicity (rearrangement).
- **R4 option-implied RND -> physical:** Bliss-Panigirtzoglou or
  Shackleton-Taylor-Yu transforms; closest published evidence at 2-4 weeks
  on S&P 500. Needs a clean surface; also test mixtures with R1/R2.
- **Overlay/ML last:** CQR / adaptive conformal (mapie); LightGBM quantile,
  NGBoost, LightGBMLSS, MDN as R3 challengers. Small effective sample makes
  overfit likely; no strong evidence they win at this horizon (unverified).

Pitfalls:
- Overlapping horizons: loss differentials are MA(h-1); use HAC (lag >= h)
  or block-bootstrap Diebold-Mariano / Giacomini-White; PIT/Berkowitz on
  non-overlapping subsamples or block-bootstrap critical values.
- ~360 non-overlapping 30d windows in 30 years; few tail events. Report CIs
  and a model confidence set, not point rankings.
- sigma_hat in z must be known at forecast time (standardization leak).
- Fix the twCRPS weight function and VRP window before looking at results.
- P&L/CVaR is a sanity check, not a model-selection criterion.

Unverified: arch HARX support; mapie time-series API; ML performance claims.

## Sources

- Corsi 2009: https://www.researchgate.net/publication/382306092
- Bollerslev, Patton & Quaedvlieg 2016 (HARQ): https://public.econ.duke.edu/~ap172/BPQ_Exploiting_Errors_JoE_2016.pdf
- Barone-Adesi et al. FHS: https://onlinelibrary.wiley.com/doi/abs/10.1002/%28SICI%291096-9934%28199908%2919%3A5%3C583%3A%3AAID-FUT5%3E3.0.CO%3B2-S
- arch forecasting: https://arch.readthedocs.io/en/latest/univariate/forecasting.html
- Hansen, Huang & Shek 2012: https://onlinelibrary.wiley.com/doi/abs/10.1002/jae.1234
- Shephard & Sheppard 2010: https://econpapers.repec.org/RePEc:jae:japmet:v:25:y:2010:i:2:p:197-231
- Engle & Manganelli CAViaR: https://www.nber.org/papers/w7341
- Foresi & Peracchi 1995: https://www.jstor.org/stable/2291056
- Chernozhukov, Fernandez-Val & Melly 2013: https://arxiv.org/abs/0904.0951
- Anatolyev & Barunik: https://arxiv.org/pdf/1711.05681
- NGBoost: https://arxiv.org/abs/1910.03225
- LightGBMLSS: https://statmixedml.github.io/LightGBMLSS/
- CQR: https://arxiv.org/abs/1905.03222
- Bliss & Panigirtzoglou 2004: https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2004.00637.x
- Shackleton, Taylor & Yu 2010: https://www.sciencedirect.com/science/article/abs/pii/S0378426610001834
- Giacomini & White 2006: https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1468-0262.2006.00718.x
