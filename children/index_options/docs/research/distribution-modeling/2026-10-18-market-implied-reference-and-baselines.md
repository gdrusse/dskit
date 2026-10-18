## Question

For the ADR-0269/0270 forecast object, the CDF of z = log(P_T/P_0)/reference_scale
(reference_scale = max(rv_22, 0.001)·sqrt(sessions_to_expiry), strike K at
z_K = log(K/P_0)/reference_scale, scores CRPS / tail_crps / threshold Brier on
Z = {-3..3 step 0.5} / PIT): (Q1) which small ladder of strong non-neural baselines
should every candidate beat, with formulas, owners and leakage rules; (Q2) how to
build the market-implied distribution of z from Theta EOD chains so it is a correct,
like-for-like competitor. Research only: read-only on code and data; no neural fit;
one numerical probe of the existing proxy on seven 2016-2019 chains (AAPL, JPM;
row groups chosen from Parquet footers so no record dated >= 2020 was decoded).
Feature engineering is another agent's lane and is not covered.

## Finding

### Inventory (file:line, base 81c0e2eb + the uncommitted ADR-0270 configs)

- `dskit/pipeline/libs/predictive_cdf.py`: `CDFEstimator` ABC 1239 (`fit(x, y, cal_x,
  cal_y)`, `curve(x)`); `HorizonEmpiricalCDF` 1266 (per-key quantiles of the RAW
  terminal return `y·reference`, returned divided by the row's reference, 1307-1359);
  `ScaledEmpiricalCDF` 1361 (ridge of log|y| on x, shape from calibration rows);
  `MonotoneCDF` 1428 (LightGBM distribution regression at declared thresholds);
  `QuantileCDF` 1679 (LightGBM quantiles, sort rearrangement, `tail_width`);
  `CatBoostQuantileCDF` 1742, `QuantileForestCDF` 2585, `NGBoostCDF` 2657;
  `OptionImpliedTransportCDF` 1814 (proxy quantile columns -> `GridCurve` with
  `tail_width` 4 beyond the outer levels, training-only PIT transport of
  `transport_knots` (0 = identity), empirical tail splice outside
  [p_lo, p_hi] at 1943-1965, per-horizon fallback); `DynamicPITRecalibratedCDF`
  1997 (causal rolling PIT map, delayed-label admission); `BetaTransformedCurve`
  1211 / `BetaTransformedPoolCDF` 2114; `SemiparametricGPDTailCDF` 2214;
  `ConvexCurve` 1135; `GridCurve` 866 (`moments` 899 exact; `cdf` 1024; `quantile`
  1044); `CalibratedCurve` 1067; `StudentMixtureCurve` 793;
  `ChronologicalCDFStudy` 5850 (JSON estimator imports, `comparison_references`,
  `calibration_knots`, purged splits; `scores` 6472 = midpoint-quantile CRPS +
  tail quadrature); `OptionPriceCDF` 7588: `parity_forwards` 7629 (strike +
  growth·(C−P), median per side, ALL paired strikes), `estimate` 7659 (growth
  validated and applied once to marks 7705-7709; forward = median of pair forwards
  7716; OTM call-equivalents only, 7721-7725; isotonic slopes in [−1, 0] weighted by
  strike width 7737-7742; interior knots with mass in (1e-4, 1−1e-4) 7745-7749;
  endpoint atoms `finite_support_endpoint_completion` 7753-7760; diagnostics
  forward_ratio, projection_distance, forward_dispersion, mass 7769-7781).
- `dskit/pipeline/libs/panel_score.py`: `EmpiricalBaseline` 520 (pooled quantiles of
  the already-standardized labels, `EmpiricalHorizonFit`), `EwmaStudentBaseline` 564
  (`StudentScaleFit`, `horizon_cdf.py` 879: y_h ~ t_df(0, c_h·vol/reference), df by
  fit NLL over a declared list, c_h by EM; `StudentForecaster` 362). Panel-pack
  nodes over `ArrayPanel`, not `CDFEstimator`s.
- `dskit/pipeline/distribution_models.py`: `LinearScaleLocationScale` 597 = log-HAR
  (Corsi 2009) ridge of log forward vol on log features, FittedTransform grammar.
- `dskit/pipeline/libs/cdf_research.py`: `OptionPriceProxy` 122 (one record per
  request: `market_eligible`, `market_reasons`, `proxy_q_i` = quantile / scale,
  `forward_ratio`; drops the owner's other diagnostics; marks = mid; refuses any
  non-positive/crossed quote in a group 255-262; growth must be positive 236-240);
  `CDFReferenceScores` 569 (per group: `market_raw` knots 0 and `market_calibrated`
  21 fitted on rows settled before `cutoff`; emits mean_z, sd_z, lower_probability at
  ONE decision strike, crps, tail_crps, weighted_crps 746-816; exclusive claim and
  precharged attempts); `PairedForecastScores` 818 (exact pairing, scale/strike/
  trained_through guards 877-909; ratio = model sd / market sd, tail_gap at the
  decision strike).
- `dskit/pipeline/libs/cdf_experiment.py` 153: the reference must be
  `HorizonEmpiricalCDF`; 418-436: refit per ticker on the fit band, `baseline_x` =
  [horizon, reference], y = target/reference.
- Child: `children/index_options/configs/run-theta-market-proxy.json` (proxy
  template, probabilities 9 levels, min_forward_pairs 3, min_wing_nodes 3,
  max_inner_gap 0.08, max_outer_gap 0.12, guards null);
  `run-theta-market-reference-scores.json` (features proxy_q_0..8, eligible 9,
  condition [actual_calendar_dte] 10, reference 11, knots 401, tail_width 4,
  transports raw 0 / calibrated 21, cutoff 2020-01-01);
  `run-strike-screen-market-requests.json` (M24, spot = Theta `underlying_price`,
  listed-expiry restriction, same-day DGS3MO, growth = exp(DGS3MO/100 ·
  actual_calendar_dte/365), `decision_strike_z` 0, clock
  `after_date_close_indicative_not_executable`; BLOCKED G1);
  `run-strike-screen-exact-expiry-quotes.json` (bid > 0, strike > 0, ask >= bid;
  composite semi-join by observation-tables; G2/G3 noted);
  `children/index_options/index_options/cdf_study.py`: `ExactExpiryCDFPanel` 2764
  needs onboarded `surface` and `lifecycle` Parquet files keyed (symbol, quote_date,
  expiry) with `chain_log_atm_iv` / `chain_log_skew25` / `chain_log_curvature25`
  (3104, 3250-3259), the volatility-index mapping and the price source; the Theta
  export produces none of these today. `FROZEN_PANEL_CONVENTIONS` 2492.
- Memo `docs/memos/2026-10-15-theta-amendment6.md`, sections "Proposed
  market-relative pocket search" and "Approved implementation" (2026-10-16): the
  proxy is a reference, not a physical truth; undiscounted parity has since gained
  the growth seam; American limitations retained; exact-expiry pins, action/rate
  clocks and schema parity still gate execution.
- Stage 0 (`stage0/market.json`): M24 2019-2020: 6,524 panel origins, 1,990
  (30.5 %) with the +31-day settlement listed, all with a same-day DGS3MO print.
- Prior notes: `2026-09-23-model-ladder.md` names the families (R0-R4) without
  owners, formulas or leakage rules; `2026-09-28-cdf-distribution-assumptions-hpo.md`
  separates physical from risk-neutral. This note does not repeat them.
- GARCH wrappers: `grep -rn -i "import arch|from arch|statsmodels|garch"
  dskit/ --include=*.py` finds only `synthetic_paths.py` (a GJR-GARCH-t path
  SIMULATOR, a test truth) and a docstring; `arch` and `statsmodels` are not
  installed in `/home/russell/dskit/.venv` (ModuleNotFoundError). Not found.

### Probe evidence (seven chains, Tuesday origins so that +31 days is a Friday)

Mid quotes, bid > 0, ask >= bid, growth = exp(DGS3MO/100·dte/365), current
template parameters, the owner's own OTM selection. All 31-day chains eligible.

| chain | pairs | fwd IQR/S | ATM spread/S | F/S − e^{rτ} | mean_Q − log(F/S) vs −½Var | median_Q − log(F/S) | Var_Q/(IV²τ) | interior mass |
|---|---|---|---|---|---|---|---|---|
| AAPL 2018-06-19 → 07-20 | 33 | 0.0020 | 0.0011 | +0.0010 | −0.0019 vs −0.0018 | +0.0022 | 1.22 | 0.996 |
| AAPL 2017-03-21 → 04-21 | 36 | 0.0004 | 0.0006 | +0.0006 | −0.0013 vs −0.0012 | +0.0017 | 1.35 | 0.993 |
| JPM 2018-10-16 → 11-16 | 9 | 0.0033 | 0.0013 | −0.0004 | −0.0034 vs −0.0021 | +0.0041 | 1.17 | 0.917 |
| AAPL 2019-01-15 → 02-15 (ex-div 02-08 inside) | 30 | 0.0042 | 0.0016 | −0.0039 | −0.0051 vs −0.0048 | +0.0030 | 1.16 | 0.998 |

Read: the parity forward is pinned to about the ATM spread; the dividend is
absorbed by the forward (−0.39 % vs 0.73/153.07 = 0.48 % less the American put
premium); the Q mean of the log return equals log(F/S) − ½Var_Q to 1e-4 on clean
chains (martingale + Jensen), the median sits above it (negative skew); the Q
variance exceeds ATM IV²τ by 16-35 % (the smile); the JPM monthly with 9 pairs and
20 OTM strikes leaves 8.3 % of mass in the two endpoint atoms, which then define
q01 and q99. Neighbour-expiry interpolation (24 d and 38 d → the listed 31 d),
quantile-wise linear in τ after √τ normalisation ("z-linear") or in signed squared
quantile ("total variance"): rms error 0.0021 / 0.0070 / 0.0083 / 0.0052 log-return
(4-13 % of the exact sd), max error at q01 up to 0.067 (JPM; the exact chain itself is
the thin one); sd/√τ is not monotone across 24/31/38 d (JPM 0.192/0.223/0.205):
chain noise exceeds the term-structure signal. Quote clock: `timestamp` is the last
NBBO update (15:53-15:59:55 ET on touched strikes, 00:00 placeholder on 790/1,462
untouched rows), `underlying_timestamp` 17:29 ET with `underlying_price` = the
official close (185.69 on 2018-06-19). Data hygiene: zero bids 13-20 % of rows
(deep wings), crossed 0; 2016 carries 16.2 % of rows with null bid/ask/
underlying_price/IV (whole OI-only symbol-days, e.g. BAC 2016-11-15), 2017-2019
0.9-2.3 % (footer null counts, no decode); DGS3MO text has 111 empty-string prints
in 2,631 rows (holidays) besides "." — the requests template filters only ".".
Export gaps: the probe's reader found no rows for JPM 2019-08-06 and CAT 2019, but a verification on the frozen export (2026-10-18, projected symbol/date, every snapshot of each root) shows CAT 2019 present in all twelve months (18,256-24,754 rows per month) and JPM 2019-08 present (18,512 rows); the probe read a subset of each root's snapshot files. A single-day absence, if any, must be re-checked per root through every snapshot before it is called a gap.

### Q1 — baseline ladder (fit on the fold's fit band only: rows whose
settlement_date < fit_end, the 40-origin monitor gap kept; every tuning knob frozen
in the registration or chosen on the monitor/calibration band; refit per fold)

| rung | formula (z units) | owner / missing piece | fit cost | why it is a fair strike-region bar |
|---|---|---|---|---|
| (a) per-symbol horizon-matched empirical | F(z) = ECDF{ r_i : symbol s, horizon n_i = n } evaluated at z·σ_ref(today): the UNCONDITIONAL raw-return law in today's units | `HorizonEmpiricalCDF` (1266); fit per ticker in `CDFExperiment` 427 | O(N log N) per group, ms | encodes per-name tail thickness and horizon; but it ignores volatility clustering: its z-width shrinks when σ_ref rises, so it is underdispersed in stress and overdispersed in calm. "Beats horizon_empirical" is a LOW bar; keep as the inherited control, not the eligibility gate |
| (b) pooled standardized empirical | F(z) = ECDF{ z_i } over all symbols and fit rows (optionally per horizon bucket) | panel pack `EmpiricalBaseline` 520 / `EmpiricalHorizonFit` 748 only. Missing: a `CDFEstimator` (either a `standardized=True` switch on `HorizonEmpiricalCDF` so `shapes` quantile `y` instead of `y·reference` and `curve` skips the division, or a 25-line `PooledStandardizedEmpiricalCDF`) | ms | full pass-through of the scale forecast (rv_22·√n) with a pooled fat-tailed shape; the natural null for "the model's extra information beyond realized vol". Recommend as the eligibility gate instead of (a) |
| (c) Student-t / EWMA location-scale | z ~ μ + c·(v_t/σ_ref)·t_ν, v_t an EWMA vol (λ 0.94 ≈ half-life 11 d, or the panel's rv columns), c and ν fitted (EM, NLL over ν ∈ {3,4,5,6,8,12}), μ = 0 or the fit-band mean of z | `StudentScaleFit` (horizon_cdf.py 879) + `StudentMixtureCurve` (793). Missing: a ~40-line `StudentScaleCDF(CDFEstimator)` with params `vol_index`, `reference_index`, `degrees`: `fit` = `StudentScaleFit(degrees).fit(torch y, torch vol/reference)`; `curve` = `StudentMixtureCurve(weights 1, means μ, scales c·vol/reference, degrees ν)`. Needs torch; the EWMA column comes from `VolEstimatorFeatures` (`vol_estimators.py` 439) if the panel lacks it | seconds | parametric, shrinks the scale toward the fit-band multiplier c (c < 1 when rv_22 over-predicts); tests whether a heavy-tailed location-scale family already captures the 0.5-z threshold structure |
| (d) HAR-RV scale + shape | log σ̂_fwd = β0 + β1 log rv_1 + β5 log rv_5 + β22 log rv_22 (+ log rv_66, log range vol); scale s = σ̂_fwd·√n/σ_ref; F(z) = G(z/s) with G Gaussian, Student or the empirical shape of calibration-band residuals | `ScaledEmpiricalCDF` (1361) IS this rung with x = the log rv features (its regression target log max(|y|, floor) differs from log RV by a constant absorbed in the intercept; `alpha` ridge; shape from `cal_y`); the exact log-HAR fit lives in `LinearScaleLocationScale` (distribution_models.py 597) in the FittedTransform grammar. Nothing missing except the calibration band (`CDFReferenceScores` passes `cal=None`; `ChronologicalCDFStudy` supplies it) | ms (ridge) | the volatility-forecast literature's workhorse at monthly horizons (Corsi 2009; Andersen-Bollerslev-Diebold-Labys 2003); a candidate must add information beyond the HAR term structure to win in any strike region |
| (e) GARCH(1,1)-t / realized GARCH | σ²_{t+1} = ω + α ε²_t + β σ²_t; n-session variance Σ_n = n σ̄² + (σ²_{t+1} − σ̄²)(1 − φ^n)/(1 − φ), φ = α+β; scale √Σ_n/σ_ref; shape t_ν or filtered historical simulation; GJR adds γ ε²_t 1{ε_t<0} (leverage; needed for equity skew); realized GARCH replaces ε² by the realized measure | NOT FOUND (grep above; no `libs/arch.py`, `arch`/`statsmodels` absent). Would need a tier-2 pack (an ADR) and daily close series per symbol (the onboarded price files, not the origin panel) | 0.1-1 s MLE per symbol per refit; 118 names × folds: minutes | Hansen-Lunde 2005: hard to beat GARCH(1,1) for FX, but equity needs leverage terms; HAR with realized measures matches or beats it at 1-month horizons, so (d) with a GJR-style asymmetric feature (down-move rv) is the cheaper equivalent. Recommend DEFER unless (d) is beaten and someone asks whether a conditional-variance recursion adds skill |
| (f) tree quantile / distribution regression | QuantileCDF: q_p(x) by LightGBM pinball at p ∈ P, sorted; MonotoneCDF: P(z ≤ c_k | x) by one monotone-in-c_k boosted classifier on the STRIKE GRID c_k ∈ Z (distribution regression; the direct estimator of the threshold Brier target) | `QuantileCDF` (1679), `MonotoneCDF` (1428), `QuantileForestCDF`, `NGBoostCDF`, `CatBoostQuantileCDF`; features = the 42 price/history columns. Missing only the frozen-panel reader (ADR-0270 §3) | QuantileCDF: |P| boosters × 100-140 k rows, 10-60 s per fold; MonotoneCDF one booster on N×|Z| expanded rows | a nonlinear conditional model on the same inputs without sequence structure: isolates what the encoder adds. MonotoneCDF at Z is the honest baseline for the per-threshold Brier heatmap |

Integration note: `ChronologicalCDFStudy` (5850, keys `models`, `reference_model`,
`comparison_references`, `calibration_knots`) already runs any named `CDFEstimator`
from JSON with purged splits and a calibration band; `CDFExperiment` accepts only
`HorizonEmpiricalCDF` as reference (153) and Torch adapters as candidates. Rungs
(b)-(f) therefore run through `ChronologicalCDFStudy` on the same frozen panel and
folds, or `CDFExperiment` gains a `comparison_references` list (one ADR item). The
scoring is identical (`ChronologicalCDFStudy.scores`). Proper scores: CRPS and the
threshold-weighted CRPS are strictly proper (Gneiting-Raftery 2007; Gneiting-Ranjan
2011); Brier at fixed thresholds is proper per threshold; PIT uniformity is a
calibration check, not a ranking (Gneiting-Balabdaoui-Raftery 2007; Diebold-
Gunther-Tay 1998). A baseline is a fair bar only if it is scored on the identical
identities with the identical σ_ref (the `PairedForecastScores` guards already
refuse otherwise).

### Q2 — the market-implied distribution of z, end to end

**1. Expiry matching.** Recommend (i) exact listed expiry as the PRIMARY
comparison cohort, declared outcome-independent, every model scored on exactly
those identities (current plan). Reasons: the probe's interpolation error (rms
4-13 % of sd, up to 0.07 log-return at q01) is of the order of the differences the
screen is trying to detect, is largest in the tails the strike grid cares about, and
the single-stock term structure between 24 and 38 days is dominated by chain noise.
Coverage: 30.5 % of M24 origins (1,990 in 2019-2020) because the panel samples
origins on roughly half the sessions and only Tuesday origins land on a Friday; over
118 names and 2016-2019 this is roughly 10 k identities, enough for CRPS contrasts
with date-block bootstrap. (ii) Interpolation is a disclosed SENSITIVITY only:
quantile-wise linear in τ of q_p/√τ between the two bracketing listed expiries with
each gap ≤ 7 calendar days (equivalent, per quantile, to linear total implied variance
σ_p²τ, the CBOE VIX near/next-term rule), preceded by a leave-one-listed-expiry-out
self-test whose error distribution is published; no owner exists (gap G-G). Figlewski
2010 and Malz 2014 interpolate inside one expiry (IV against strike or delta), not
across expiries; across-expiry interpolation of single-stock RNDs has no clean
literature backing and is exposed to earnings dates between the two expiries.
(iii) Redefining the target to listed expiries is the right LONG-RUN design (an
option strategy trades listed expiries; `ExactExpiryCDFPanel` does this), but it
needs the surface/lifecycle inputs the Theta pipeline does not yet produce and, under
ADR-0269 §1, a target change is a separate registered experiment, not this pilot.

**2. Forward, discounting, quotes, support.** F = median_K [K + e^{rτ}(C_K − P_K)]
with r = DGS3MO/100 and τ = calendar days/365 (`parity_forwards`; continuous vs
bond-equivalent compounding differs by < 1e-4 at τ ≈ 0.085). Dividends enter only
through F (the options and the target both settle on the ex-dividend close); never
subtract them again. American exercise: C − P = e^{−rτ}(F − K) fails by the early-
exercise premium, largest for ITM puts and, before an ex-date, ITM calls; OTM
contracts carry the smallest premium, so (a) take the forward from pairs NEAR THE
MONEY (|log(K/S)| ≤ 0.10; the owner today uses all paired strikes: gap G-A, the
median limits but does not remove the ITM-put bias) and (b) build the CDF from OTM
puts for K ≤ F and OTM calls for K ≥ F, which `estimate` already does (7721-7725).
Weekly/monthly PM-settled equity options expire on the Friday close; a holiday
Friday moves the expiration to Thursday and the panel's "last session on or before"
rule matches it. Quote hygiene before the proxy: drop null or zero bid, ask < bid,
non-positive strike (template), and ADD: ask ≤ 0, relative spread (ask − bid)/mid > 1,
and a whole-identity refusal "quotes_absent" when every contract of the day is
OI-only (2016). Use mids; keep a bid-based and ask-based variant as a sensitivity
only. Minimum support: ≥ 5 forward pairs near the money, ≥ 5 OTM strikes per wing,
strike gap ≤ 0.05 log around F and ≤ 0.10 log elsewhere (a 0.5-z bin is 0.025-0.05
log-return for σ_ref 0.05-0.10; the template's 0.08/0.12 lets one gap span 1-2 z).
Monotonicity: the isotonic projection of digital slopes onto [−1, 0] (7737-7742) is
the shape-restricted estimator of Aït-Sahalia-Duarte 2003 and enforces butterfly
(convexity) arbitrage-freeness; projection_distance ≤ 5e-4·S on the probe, so mids
are nearly arbitrage-free. Tails: the owner completes with atoms at the first and last
OTM strike (7753-7760), so q01/q99 are endpoint interpolations whenever the wings
are thin (JPM: 8 % of mass in atoms); `OptionImpliedTransportCDF` then splices the
per-symbol horizon-empirical conditional tail beyond the outer levels (1943-1965).
That splice is correct for the P-reference (`market_calibrated`) but makes
`market_raw` a Q body with P tails; for a pure Q reference extrapolate the wings in
IV (flat IV of the last OTM strike = lognormal tail, or Figlewski's GEV matched at the
last two knots) using `black76_implied_vol` (gap G-B). Report mass, projection
distance, forward dispersion, pairs and strike counts per identity (the owner computes
them; `OptionPriceProxy` drops them: gap G-C).

**3. Risk-neutral vs physical.** q(x) = p(x)·m(x)/E_P[m] with a pricing kernel m
decreasing in wealth: relative to P, Q (a) shifts the mean down by the equity
premium (E_P[r] − log(F/S) ≈ 0.4-0.6 % per month), (b) is over-dispersed (variance
risk premium: Q variance > expected realized variance; Carr-Wu 2009, Bakshi-Kapadia
2003; weaker and noisier on single names than on the index), (c) carries a fatter
left tail / steeper skew than realized (Jackwerth 2000; Aït-Sahalia-Lo 2000;
Christoffersen-Heston-Jacobs 2013). A raw Q distribution scored as a physical forecast
therefore shows a PIT histogram that is ∩-shaped (outcomes concentrate in the centre
of a too-wide law) and shifted right (mean PIT > 0.5), with the far-left-tail bin
under-populated — Fackler-King 1990 and Liu-Shackleton-Taylor-Xu 2007 report
exactly this for index options. A U-shaped raw PIT would NOT be the premium: it
indicates a truncated/under-dispersed proxy (thin wings, endpoint atoms), i.e. a
construction defect to fix first. Two fair comparisons: (a) `market_raw` (transport
knots 0): "model beats market_raw" means only that the physical forecast is better
than a biased risk-neutral law, achievable by removing a constant premium, no
information claim; "market_raw beats model" means the model is worse than a biased
reference: a calibration failure. (b) `market_calibrated`: the Q→P transport
G(u) = ECDF of PIT_Q(y) on fit rows settled before the fold's cutoff, 21 knots
(`OptionImpliedTransportCDF.fit` 1894-1920; optional per-horizon maps with shrinkage
`transport_prior_strength` and `transport_local_bounds`). This is the non-parametric
version of the calibration literature (Fackler-King; Liu et al. 2007's beta
recalibration = `BetaTransformedCurve` 1211 with (a, b) selected on calibration labels
by `BetaTransformedPoolCDF` 2114; Bliss-Panigirtzoglou 2004's power/exponential
utility tilt p ∝ q·x^{−γ} or q·e^{γx} is a one-parameter member fitted by PIT
likelihood on fit rows, cheaper and more stable in small samples, not yet an owner).
Ross recovery is not recommended: it needs the full state-price transition matrix and a
transition-independent kernel, and recovers a misspecified measure when the kernel has
a permanent component (Borovička-Hansen-Scheinkman 2016); single-stock EOD chains
cannot supply the matrix. Under (b): "model beats market_calibrated" is the meaningful
claim (the model carries conditional information beyond the market's view with its
average premium removed); "market_calibrated beats model" is the null that any pocket
claim must survive. The transport assumes a stable premium across the fit and score
years; 2020 is a regime break, so also report `DynamicPITRecalibratedCDF` (1997,
causal rolling map, half-life 90 d) as the honest variant, and later the combination
test (linear pool of model and market, `ConvexCurve` 1135; Gneiting-Ranjan 2013), which
is the real test of incremental information.

**4. Units and clocks.** Let the adjusted close be P^adj_t = P^un_t / a_t with a_t
piecewise constant, jumping only at corporate-action dates. The target is
z = [log(P^un_T/P^un_0) + log(a_0/a_T)]/σ_ref. On the quote date the chain is struck in
the a_0 basis, so z_K = log(K/P^un_0)/σ_ref and the proxy is a law of P^un_T on that
basis. If no action falls in (t_0, T], a_T = a_0 and the two z's coincide for every K:
invariance holds exactly, and σ_ref (a 22-session rv of adjusted returns) is itself
invariant to a multiplicative adjustment. If an action falls inside the window, the
target carries log(a_0/a_T) that the quote-date chain does not: for a pure ratio split
the OCC adjustment (strike K → K·a_0/a_T, deliverable scaled) keeps the economics and
the fix is to map the market law by the same factor; for cash special dividends,
spin-offs and mergers the panel's split-only factor is not the OCC adjustment and no
mapping restores comparability. Rule: a market-comparison row requires (1) spot-basis
agreement, |underlying_price − panel P^un_0| ≤ 0.1 % (the panel stores neither P_0 nor
a_t: gap G-J; until then the declared-basis agree field is an assumption, not a check),
and (2) no action of any kind in [t_0, T] (the panel's `corporate_actions` rule,
cdf_study.py 3775-3777, when configured); rows failing either are counted exclusions
for EVERY model on that cohort, never silently dropped for one. Ordinary cash
dividends are not actions here. Clock: Theta EOD = the day's closing NBBO (`timestamp`
15:53-16:00 ET on touched strikes) and `underlying_price` = the official 16:00 close;
the model's features are close-based too, so known_at = quote_date session close for
both sides (ADR-0270's `after_date_close_indicative_not_executable`); available_at =
decision_at = quote_date; `acquired_at` (2026) is provenance only; any executable use
starts at the next session's open against a different spot and is out of scope.

**5. Validation before use.** Unit checks (synthetic chains + the development
census): (1) parity forward: median pair forward within the ATM pair's bid-ask of
each near-the-money pair forward, IQR/S ≤ 2× ATM spread/S, F/S − e^{rτ} ∈
[−(dividend yield·τ) − 0.5 %, +0.2 %], and a known-ex-date chain shows the dividend
drop (AAPL Feb-2019: −0.39 % vs 0.48 %); (2) curve: probabilities 0 → 1,
nondecreasing, finite, quantiles strictly increasing across levels, interior mass ≥
0.95 else "thin_wings"; (3) location: mean_Q(log S_T/S) = log(F/S) − ½Var_Q within
1e-3 (probe 1e-4 on clean chains), median_Q within 0.1 z of log(F/S)/σ_ref; (4) scale:
Var_Q/(ATM IV²τ) ∈ [0.8, 1.6] using the vendor `implied_vol` of the four strikes
nearest F (probe 0.91-1.35); (5) skew: |q01| > q99 for most equity chains; (6) growth
direction: growth 1 vs e^{rτ} moves F by ≈ S(e^{rτ} − 1); (7) basis invariance:
scaling strikes, marks and spot by a constant leaves the z quantiles unchanged; (8)
American robustness: inflating ITM put marks leaves the curve unchanged except
through the forward, which must be near-the-money-restricted; (9) refusal paths:
one-sided chains, duplicates, crossed, zero bids, quotes_absent days, missing rate;
(10) PIT: under market_raw on fit rows ∩-shaped with mean > 0.5 and KS rejection;
under market_calibrated on held-out rows approximately uniform; a U in the raw PIT
fails the build. Cheap sanity run (2016-2019 only, no neural fit): all 118 eligible
names, Tuesday origins whose +31 settlement is a listed Friday, settlements ≤
2019-12-31, transport fitted on rows settled before 2019-01-01, scored on 2019;
outputs: coverage census by refusal reason and year (quotes_absent will dominate
2016), the distributions of checks (1)-(5) per tercile, PIT histograms per year for
raw and calibrated, and CRPS / tail_crps / threshold-Brier-at-Z of market_raw,
market_calibrated, (a) and (b) on the identical rows with date-block bootstrap.
Cost: ~10 k identities × ~150 contracts ≈ 1.5 M projected rows, ms per proxy,
minutes in total; memory ≤ 0.5 GiB when read per symbol with footer-pruned row groups
(as the probe did), which also sidesteps gap G3.

**6. `OptionPriceProxy` defaults (proposals to be confirmed by the Stage-0 census
of the diagnostics in item 5, then frozen).** probabilities: 19 levels
[0.01, 0.025, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.70, 0.75, 0.80,
0.85, 0.90, 0.95, 0.975, 0.99] — the current 9 levels interpolate the CDF linearly
between the 0.25 and 0.75 quantiles, flattening the density exactly around the
pilot decision strike z = 0 and biasing the z-grid Brier at |z| ≤ 1; better still,
persist the owner's full (probabilities, log_returns) knot lists (gap G-C) and let the
transport consume them. min_forward_pairs 5 (near the money; probe 9-46 on liquid
names). min_wing_nodes 5 (with 3 the 1 %/99 % levels are always endpoint atoms).
max_inner_gap 0.05, max_outer_gap 0.10 (reasoning in item 2; report the median gap
in z). max_projection_distance 0.002 (probe ≤ 5e-4 on clean chains; larger means stale
or crossed marks). max_forward_dispersion 0.006 (probe 0.0003-0.0042). Transport:
21 knots fitted on fit-band rows only, per horizon with prior strength ≥ 50 if
conditioned; tail_width 4 z. Gaps (each an ADR item; the existing owner cannot):
G-A near-the-money window for the parity forward; G-B IV/lognormal or GEV wing
extrapolation for the pure-Q reference; G-C proxy records carry the full knots and all
diagnostics; G-D numeric-text parse for DGS3MO that treats both "." and "" as null
(ADR-0270 G1 plus the 111 empty prints); G-E an as-of (strictly prior) rate join;
G-F a `quotes_absent` whole-identity refusal distinct from strike/wing refusals; G-G
across-expiry interpolation with its self-test (sensitivity only); G-H the (b) pooled
standardized empirical and (c) Student rungs as `CDFEstimator`s, GARCH pack absent;
G-I a reference ladder in `CDFExperiment` (or run rungs through
`ChronologicalCDFStudy`) plus the frozen-panel reader; G-J P_0 and the adjustment
factor stored on the panel so the basis check is a refusal; G-K per-threshold local
scores of the market curve on Z (ADR-0270 §6 StrikeGridScores must accept proxy
knots as well as mixture parameters); G-L model+market combination node (later).
Also: switch the ADR-0270 eligibility gate from (a) to (b), or report both, because (a)
ignores volatility clustering.

### Recommended market-implied recipe (one paragraph)

Identity (s, t_0, T) with T a listed expiration on t_0 (exact-match cohort, declared
before outcomes). Quotes: Theta EOD rows of (s, t_0, T), bid > 0, ask ≥ bid > 0,
relative spread ≤ 1, mids; whole-identity refusals counted by reason. Rate r =
DGS3MO(t_0)/100 (as-of prior print when the day is a holiday), τ = (T − t_0)/365,
growth = e^{rτ}. Forward F = median over pairs with |log(K/S)| ≤ 0.10 of
K + growth·(C − P); dividends only through F. Body: OTM puts (K ≤ F) and calls
(K ≥ F) as call-equivalents, digital slopes isotonically projected onto [−1, 0]
weighted by strike width, F_Q(K) = 1 + slope; support rules ≥ 5 pairs, ≥ 5 OTM
strikes per wing, gaps ≤ 0.05 (inner) / 0.10 (outer) log. Tails: for market_raw a
flat-IV lognormal wing beyond the last OTM strike; for market_calibrated the
existing empirical splice beyond the 1 %/99 % knots. Units: z = log(K/P^un_0)/σ_ref
with σ_ref from the panel, P^un_0 = `underlying_price` checked against the panel's
unadjusted close, rows with any corporate action in [t_0, T] excluded for every model.
References: market_raw (identity transport) and market_calibrated (21-knot PIT map on
fit-band rows settled before the fold cutoff; dynamic variant as sensitivity). Clock:
known_at = t_0 close for market and model alike. Validation gates (item 5) must pass on
the 2016-2019 census before any model is scored against it.

## Sources

- Gneiting & Raftery 2007, strictly proper scoring rules: https://doi.org/10.1198/016214506000001437
- Gneiting, Balabdaoui & Raftery 2007, calibration and sharpness: https://doi.org/10.1111/j.1467-9868.2007.00587.x
- Gneiting & Ranjan 2011, threshold-weighted CRPS: https://doi.org/10.1198/jbes.2010.08110
- Gneiting & Ranjan 2013, combining predictive distributions: https://doi.org/10.1214/13-EJS823
- Diebold, Gunther & Tay 1998, PIT evaluation: https://doi.org/10.2307/2527342
- Corsi 2009, HAR-RV: https://doi.org/10.1093/jjfinec/nbp001
- Andersen, Bollerslev, Diebold & Labys 2003, realized volatility forecasting: https://doi.org/10.1111/1468-0262.00418
- Patton 2011, volatility forecast comparison with imperfect proxies: https://doi.org/10.1016/j.jeconom.2010.03.034
- Patton & Sheppard 2015, good and bad volatility (signed HAR terms): https://doi.org/10.1162/REST_a_00503
- Hansen & Lunde 2005, does anything beat a GARCH(1,1)?: https://doi.org/10.1002/jae.800
- Hansen, Huang & Shek 2012, Realized GARCH: https://doi.org/10.1002/jae.1234
- Breeden & Litzenberger 1978: https://doi.org/10.1086/296025
- Aït-Sahalia & Duarte 2003, shape-restricted option pricing (isotonic/convex projection): https://doi.org/10.1016/S0304-4076(03)00102-7
- Figlewski 2010, estimating the implied risk-neutral density (IV-space interpolation, GEV tails): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1256783
- Jackwerth 2004, option-implied risk-neutral distributions and risk aversion (review): https://www.cfainstitute.org/en/research/foundation/2004/option-implied-risk-neutral-distributions-and-risk-aversion
- Malz 2014, FRBNY Staff Report 677: https://www.newyorkfed.org/research/staff_reports/sr677.html
- Birru & Figlewski 2012, anatomy of a meltdown (RND dynamics): https://doi.org/10.1016/j.finmar.2011.09.001
- Cboe VIX methodology (near/next-term interpolation in total variance): https://cdn.cboe.com/api/global/us_indices/governance/Volatility_Index_Methodology_Cboe_Volatility_Index.pdf
- Bliss & Panigirtzoglou 2004, option-implied risk aversion (power/exponential tilts): https://doi.org/10.1111/j.1540-6261.2004.00637.x
- Liu, Shackleton, Taylor & Xu 2007, closed-form transformations from risk-neutral to real-world distributions (tilt and beta calibration): https://doi.org/10.1016/j.jbankfin.2006.11.002
- Fackler & King 1990, calibration of option-based probability assessments: https://doi.org/10.2307/1243146
- Jackwerth 2000, recovering risk aversion from option prices and realized returns: https://doi.org/10.1093/rfs/13.2.433
- Aït-Sahalia & Lo 2000, nonparametric risk management and implied risk aversion: https://doi.org/10.1016/S0304-4076(99)00016-0
- Carr & Wu 2009, variance risk premiums: https://doi.org/10.1093/rfs/hhn038
- Bakshi & Kapadia 2003, delta-hedged gains and the negative market volatility risk premium: https://doi.org/10.1093/rfs/hhg002
- Christoffersen, Heston & Jacobs 2013, variance-dependent pricing kernel: https://doi.org/10.1093/rfs/hhs128
- Ross 2015, the recovery theorem: https://doi.org/10.1111/jofi.12092; Borovička, Hansen & Scheinkman 2016, misspecified recovery: https://doi.org/10.1111/jofi.12404
- Shimko 1993, bounds of probability (IV-space smoothing, lognormal tails): Risk 6(4) 33-37
- Repository: `docs/architecture/decision-log.md` ADR-0269/0270; `children/index_options/docs/memos/2026-10-15-theta-amendment6.md`; `children/index_options/docs/research/distribution-modeling/2026-09-23-model-ladder.md`; probe script `/tmp/claude-1000/-home-russell-wt-strike-fast-scan-20261018/6af6a15f-e855-4a87-8ec8-f5afada7123d/scratchpad/mkt-research-fable/mkt_probe.py` (not committed).
