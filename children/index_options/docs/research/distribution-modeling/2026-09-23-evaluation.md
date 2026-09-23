## Question

How do we measure that the forecast distribution is close to the true
latent distribution where it matters (the condor strike regions)?

## Finding

- **Primary score:** threshold-weighted CRPS, weight on the strike zones
  (about |z| in [0.5, 2.5]). CRPS is the integral of the Brier score over
  thresholds, so twCRPS is a region-focused Brier score: it bridges the
  latent-vs-bins question and ranks both model types on one scale.
- **Secondary:** censored / conditional likelihood (Diks-Panchenko-van Dijk).
- **At the actual strikes:** Brier score and reliability, bucketed by delta.
- **Calibration:** PIT uniformity (Diebold-Gunther-Tay), Berkowitz test,
  inspected especially in the tails.
- **Decision check:** out-of-sample condor P&L, CVaR and hit rate from trades
  each model selects against the market price.
- **Hygiene:** only proper (weighted) scores; never choose the evaluation
  subset by realized outcomes (forecaster's dilemma).

## Sources

- Gneiting & Ranjan 2011: https://www.jstor.org/stable/23243806
- Gneiting & Raftery 2007: https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf
- Diks, Panchenko & van Dijk 2011: https://ideas.repec.org/a/eee/econom/v163y2011i2p215-230.html
- Lerch et al., forecaster's dilemma: https://arxiv.org/pdf/1512.09244
- Diebold, Gunther & Tay 1998: https://www.sas.upenn.edu/~fdiebold/papers/paper16/paper16.pdf
- Berkowitz 2001: https://econpapers.repec.org/RePEc:bes:jnlbes:v:19:y:2001:i:4:p:465-74
- scoringRules (twCRPS implementation): https://cran.r-project.org/web/packages/scoringRules/vignettes/article.pdf
