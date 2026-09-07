# Horizon-cap gate design: which per-(stock,horizon) checks are needed

## Question

After the model zoo selects a final architecture, we produce a forecast
per (stock, horizon) pair. We need gates that (a) confirm each horizon's
prediction is genuinely informative and stable, and (b) cap a stock's
horizon at the furthest contiguous horizon where prediction remains good,
rather than trusting a terminal H_i from Gate 3 while intermediate
horizons are weak (e.g. good at h=10 for AAPL but bad at h=5).

## Finding

The existing toolkit already owns most primitives; the gap is a generic
horizon-cap seam that chains them with a contiguity + regime-stability
rule. What literature and the repo already support:

1. **Beats-benchmark per horizon (necessary, not sufficient).**
   `no_information_test` (Clark-West vs the train mean, ADR-0057) and the
   sequential `max_informative_horizon` (Breitung-Knuppel, fixed alpha) are
   the repo's single owners. Conquering every horizon h=1..H is the
   "uniform SPA" (uSPA) intersection-union idea the benchmarks already
   implement as `_family_spa`; the literature (uSPA/aSPA, Hansen 2005;
   multi-horizon uniform SPA) backs "every horizon must beat".

2. **Contiguity / SAFE-style conquest.** A cap should stop at the first
   horizon whose prediction no longer passes, not at a terminal H_i —
   monotone conquest down the horizon ladder is the standard "forecast
   horizon" safety rule (Breitung-Knuppel sequential h* is exactly this,
   stopped short of Patton-Timmermann monotonicity, which remains the
   caller's assumption in `max_informative_horizon`).

3. **Regime / temporal stability.** A model that wins overall but is
   negative in a season, a weekday, a month, or a session segment is not
   trustworthy. The repo has no generic "stability across a declared
   slicing dimension" node; the DM/MCS and cluster-bootstrap machinery
   (`cluster_bootstrap_t`, session clusters) is the right primitive but
   is benchmark-scoped today. This is the genuinely NEW capability to
   graduate.

4. **Over/underfit via train-val gap.** No generic owner today; a
   predeclared empirical gap bound (e.g. train-val R2/IC delta) with an
   explicit tolerance in config is the standard guard.

Additional gates considered and judged OUT of scope for this ADR
(they belong to selection/HPO, not per-pair gating): per-horizon feature
importance, horizon-aware feature selection (its own research, ADR-0100
context), and MCS/HCS model-set membership (a model comparison, not a
per-pair production gate).

## Sources

- White (2000) / Hansen (2005) SPA; uSPA/aSPA multi-horizon extension
  ("Multi-Horizon Uniform Superior Predictive Ability Revisited", JBES).
- Breitung & Knuppel sequential h* (already `max_informative_horizon`).
- ESN intraday multi-horizon paper (arXiv 2504.19623): per-horizon DM +
  MCS tests are the standard confirmation.
- Existing repo owners: `no_information_test`, `max_informative_horizon`,
  `cluster_bootstrap_t`, `_family_spa` (benchmarks), `beat_all`/`tier2_verdict`.
