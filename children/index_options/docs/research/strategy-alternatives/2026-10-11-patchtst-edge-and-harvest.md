## Question

The pooled PatchTST forecast is traded through an iron-condor MIO, and every
replay arm lost. Before another structure or another optimizer: what does this
forecast predict better than the option market, where (location, scale, tails,
shape; which names, regimes), and which payoff and objective would turn that into
money most efficiently? Evidence: the 2024-25 evaluation (99,225 paired forecasts
per seed), the two condor replays (131 and 1,259 trades), the SPY/QQQ/IWM studies
of September and the literature. No new fit, replay or 2026 access; the tests at
the end run on evidence already saved.

## Finding

### 1. The answer

**The measured edge is a small, pooled gain over an unconditional reference, and
every component measured against the market is either a risk premium or an
error.** Against the market, the forecast's location carries no name-level
information (it is one +2%/month equity drift), its scale is 11-17% too narrow
where the market is right to within 2%, its put side "sees" the put premium that
compensates crash risk, and its residual shape/skew content is about zero. The
condor harvests exactly those three things: the drift as a short put (long
stock), the narrowness (winner's curse) and thin prints. And the replay traded
only the 85-name cohort on which the forecast's measured skill is about zero.
No structure fixes that. The forecast has to be anchored to the market first;
the candidates for genuine edge that remain are (a) relative dispersion across
names and (b) earnings windows, both untested here and both supported by the
cross-sectional option literature. If (a) survives its test, the efficient
harvest is a market-neutral dispersion book of symmetric defined-risk vol
structures across many names, not one condor per context. If it fails, the
forecast is a risk filter and the P&L source is the premium program.

### 2. What the forecast measurably does

Score: weighted CRPS = CRPS + tail CRPS on |z| in [0.5, 2.5], against
`HorizonEmpiricalCDF`, each name's empirical distribution of raw log returns at
the horizon over its training rows. The reference ignores today's volatility.
Skill = 100 x (1 - model loss / reference loss), ratio of sums.

| Stage | Window | Seed 11 | Seeds 29 / 47 |
|---|---|---|---|
| Development (selection) | 2021-22, 86,328 forecasts | +0.878% | |
| Confirmation | 2023, 51,228 | +1.332% | +0.818% / +1.123% |
| Evaluation | 2024-25, 99,225 | +0.279% | +0.241% / +0.254% |
| Evaluation, original 85 names | 21,587 | +0.106% | +0.118% / -0.112% |
| Evaluation, restored 307 names | 77,638 | +0.327% | +0.275% / +0.356% |

- Six of nine windows positive (fold range -0.62% to +1.10%); 185 of 392 names
  positive. The earlier 85-name cycle (2026-10-07) was negative on the same
  2024-25 windows for all three seeds (-0.08%, -0.19%, -0.20%); the 392-name
  rerun lifted the identical 21,297 original-85 development forecasts from
  -0.74% to +0.50%, but their evaluation skill stayed near zero.
- Name-level skill is realization noise, not a property of the name: the three
  seeds agree within a period (rank correlation 0.86 and 0.82) while a name's
  2023 skill predicts its 2024-25 skill with rank correlation 0.07 (377 names).
  The 2023 top quartile pooled to +0.72% in 2024-25, the bottom quartile to
  -0.21%. Skill is flat across volatility terciles (+0.24%, +0.28%, +0.30%).
  The 30 names with under 300 training origins score +0.92%, the other 351
  +0.15%: part of the gain is the reference's weakness on short histories (the
  417-name zoo found the same: 21 short-history names supplied 0.31 of the
  CNN's 0.36 points). There is no "trade the names it knows" rule.
- The 12-architecture pooled zoo spans +0.46% (PatchTST) to -0.18% on the same
  reference; every unpooled model is negative (-1.7% to -3.1%). Its option
  features were mostly imputed constants (available from 2024 only), so their
  negative result says nothing about implied volatility as an input.
- Calibration (seed 11, 2024-25): central intervals cover 47.7% at nominal 50%,
  76.0% at 80%, 91.6% at 95%, 96.8% at 99%; PIT mean 0.514; the top PIT decile
  holds 13.3% of outcomes. Read as a Gaussian, the 50% interval is 5% too
  narrow, the 95% interval 13%, the 99% interval 19%: the misfit grows into the
  tails, so a scale factor alone does not repair it. The ETF line shows the
  same shape: outcomes fall below the forecast 5th percentile 7.8-11.7% of the
  time for every model there, while the upper tail is about right.
- A filtered-historical-simulation reference (the shape of z rescaled by
  today's vol) was flagged on 2026-09-28 and never run; skill against it is
  unknown and smaller.

### 3. The forecast against the market (2025 entry contexts, v2 replay)

| Component | Forecast vs market | Realized | Verdict |
|---|---|---|---|
| Location | mean +1.9% (median) above the parity forward | PIT mean 0.50 on traded contexts; the claimed put-side edge has slope 0.15 on realized (clustered SE 0.21, corr 0.035) | one pooled drift; no name-level information |
| Scale | log-return sd 0.89x ATM implied; straddle 0.88x market; ATM implied vol 10.4 points lower | realized rms move 1.02 implied sd | market right, forecast too narrow |
| Left tail | 95%-moneyness put valued at 0.62x market | short puts claimed +$414k, realized +$98k | the put premium (compensation), not mispricing |
| Right tail | 105% call at 1.04x market; +10% implied vol 0.4 points lower | short calls claimed +$9.7k, realized -$58k | fair on average, wrong conditionally |
| Shape/skew | after moving the mean to the forward, the scale to the straddle and smoothing prints: +$4.6k of a $396k claim | | about zero |

Four facts decide the strategy question:

1. **Probabilities at the chosen strikes are fair on average but wrong
   conditionally.** P below the short put 48.4% vs 50.3% realized; above the
   short call 27.6% vs 28.5%. By forecast P(loss) bin the claim falls 511, 409,
   278, 173, 101 while realized runs -50, -75, +122, -182, +142: the two
   highest-claim bins lose. The optimizer selects the forecast's errors.
2. **The money sits in the market's quantities, not the model's.** The claim
   decomposes into drift 59%, scale 32%, prints 20%; re-selecting on a
   market-consistent forecast with smooth prices claims +$58k and realizes
   -$163k. The oracle control (same selector, true outcome) earns +$1.87M: the
   plumbing is sound, the input is the loss.
3. **Every trade was on the cohort without skill.** Basis refusals removed all
   35,355 restored-cohort contexts; the 1,259 trades are original-85 names,
   whose evaluation skill is +0.11%, +0.12%, -0.11% by seed. The cohort that
   scores +0.33% has never been priced against an option chain.
4. **The SPY/QQQ/IWM line reached the same verdict.** Every conditional rung
   beats the empirical rung (p <= 0.0005), none beats VIX, and no rung's strikes
   beat VIX strikes (+$79/trade, t 1.32, versus $0-40 with worse tails). The
   strongest challenger there is the transported option-implied CDF: +2.75%
   CRPS vs empirical, +1.85% vs the incumbent (120-date block interval [0.39%,
   3.29%]), failing only the PIT tail guards; causal option-chain FEATURES added
   to a physical model did not improve CRPS consistently; physical-feature
   center conditioning added +0.015%. At real listed wings the transport's
   gain shrinks to +1.2% (QQQ +2.3%, SPY +0.1%, intervals crossing zero). The
   decision-weighted MLPs: SPY recurring but regime-variable (+9.6% in 2020,
   -0.75% in 2022), failing the 30-date precision bound; QQQ negative in every
   dense band. Three index robust-condor trades, mean -$1.21/share. The
   conditional edge the ETF models do have over the empirical reference sits
   on the upside tail (SPY upside +4.5%, downside +0.7%), where the market
   prices calls about fairly, and at short horizons: standalone learners are
   +1.5% to +3.5% at 1-7 days and -2.5% to -4.9% at 30-45 days, the blends
   +0.5% to +0.8% there. The 31-day decision horizon is where the physical
   models have least to say. The information is in the option surface.

### 4. Why the condor is the wrong carrier for this forecast

For any listed payoff g, E_P[g] - E_Q[g] = the integral of g'(s)(F_Q(s) - F_P(s)).
For a condor body or strangle, g' is +1 below the put strike and -1 above the
call strike, so the expected edge under P equals Q's excess tail mass below the
put plus Q's excess survival mass above the call. A mean shift of P adds to the
put term and subtracts from the call term: the nominal objective therefore sells
puts in the money (median K/S 1.004, 52.6% ITM, net delta +24 shares per lot,
74% of trades net long, P&L correlation 0.45 with delta times the stock return).
A pure narrowing of P raises both terms: that is the winner's curse (traded
contexts have PIT below 0.10 at 15.6% vs 10.3% on no-trades). The condor pays
both known biases in full, and its wings are priced by the market where the
forecast has no tail information (tail CRPS skill +0.21% vs the unconditional
reference, none measured against the market). February 2025 (-$155k on 125
trades) and October (-$55k) made the year because 125 simultaneous short puts
are one position: the book carried the common market factor, which per-context
robustness cannot address. The radius bounds a mis-specified marginal; it cannot
protect a book that is all long the same market.

### 5. Where edge could still be, ranked

| Rank | Candidate | Prior evidence | Owned data to test it | Status |
|---|---|---|---|---|
| 1 | **Relative dispersion across names**: after the level bias, does rank(forecast sd / implied sd) predict rank(realized move / implied sd)? | Goyal & Saretto 2009: sorting on realized-minus-implied vol and trading straddles long-short earns significant monthly returns, robust to factors; Do, Foster & Gray 2016: much of it goes to bid/ask; Cao & Han 2013: delta-hedged option returns fall monotonically with idiosyncratic vol; Vasquez 2017: IV term-structure slope sorts straddles; Bali, Beckmeyer, Moerke & Weigert 2023 (RFS): nonlinear ML on 12M delta-hedged single-stock options, >2%/month after costs, implied vol the strongest predictor, stock features add on top | 2,796 calibration + 2,772 entry contexts, 72-79 names, 91 dates, with the smile already extracted in the selection receipt | untested; the forecast's inputs (rv, momentum, liquidity) are a subset of that literature's; implied vol is not an input |
| 2 | **Earnings inside the 31-day window**: about a third of entries span an announcement; the model has no calendar, so its under-dispersion is plausibly concentrated there | Gao, Xing & Zhang 2018 (JFQA): ATM straddles held from three days before through the announcement average +3.34%; the market under-prices announcement uncertainty, more for small, volatile, illiquid names | none owned; proxy = the front-month vs next-month implied-vol jump in the chain, or an acquired calendar | untested; a data gap for the owner |
| 3 | **Timing of the premium**: VRP sign and term-structure slope per name | Johnson 2017, Cheng 2019 (index); untested on single names | per-name implied vol from the owned chain | no model needed |
| 4 | Location (direction) | slope 0.15 +/- 0.21; the momentum arm scores 0.10 points below base | | none; anchor to the forward |
| 5 | Tails and wings | puts at 0.62x market = premium; GPD and isotonic layers hurt at real wings (ETF) | | none; wings are risk management, priced by the market |

### 6. How to harvest it efficiently (design, pending the tests)

- **Anchor the forecast to the market before it touches an objective.** P*:
  mean set to the parity forward (no name-level directional information
  exists); scale set to the ATM straddle times (1 + kappa x signal), where
  signal is the forecast's standardized deviation from implied and kappa is the
  out-of-sample reliability from T2 (zero until measured; the slope 0.15 is the
  first estimate). Robustness (rho) then bounds what is left. The ETF evidence
  says the same thing from the other side: transporting the implied CDF beat
  adding chain features to a physical model, so the model's job is the
  deviation from the market, not a rediscovery of it.
- **Trade the component with edge with the payoff that integrates it.** A scale
  view is a symmetric strangle around the forward, defined-risk (an iron condor
  with far wings when short, a long iron condor when long), OTM shorts only,
  wing width capped at 5% of spot: the only in-sample selector whose monthly
  SE was near its mean. Verticals are for CDF-level views, which the forecast
  does not have. Nothing is sold through an earnings window until rank 2 is
  tested.
- **Make it a book, not a trade.** Long dispersion where forecast > implied,
  short where forecast < implied, dollar-risk weighted, net delta near zero and
  net vega bounded so the common factor nets out; caps per name and per date.
  This is the only way an edge of a few tenths of a percent of CRPS becomes a
  usable Sharpe: breadth across 300+ names and ten entry dates a month with the
  factor hedged. The present MIO (one condor per context, argmax of claimed
  edge, no spot tie, no size bound) becomes a portfolio allocation with those
  constraints; the dual/MILP machinery, projection and settlement accounting
  are reused, the objective and the constraint set change.
- **Gate the short side** by the per-name VRP sign (implied above the corrected
  forecast scale by a margin) and skip inverted term structures; the index
  evidence is the prior, T5 measures it on single names.
- **If (a) fails T1/T2, stop using the forecast for selection.** The P&L source
  is then the premium program (which side, when, how much: the survey's ranked
  list), with the forecast as a risk filter (size down names whose anchored
  forecast has a fat left tail), and the modeling budget moves to the input
  that carries information: the option surface itself, as the reference scale
  or as the base to transport.

### 7. Pre-registered tests on saved evidence (no fits, no 2026 access)

All on the v2 run's 2,710 entry contexts and the 2,796 calibration contexts
with the smile method of the selection receipt; uncertainty by 31-day date
blocks; thresholds frozen here. Limitation stated up front: this is the
original-85 cohort, the only one with a price basis; the cohort where the
forecast shows skill cannot be priced until the 307 action inventories exist,
which makes those inventories the single largest data gap for this question.

- **T1 Scorecard P versus Q.** Log score, CRPS and strike Brier at the listed
  strikes for: (i) raw empirical reference, (ii) FHS reference, (iii) forecast
  P, (iv) market Q from the smoothed smile (no study in this child has yet
  scored the raw market CDF as a reference), (v) P with mean -> forward, (vi) P
  with mean -> forward and scale -> straddle, (vii) the encompassing mixture
  w P + (1 - w) Q with w fitted on 2024 and scored on 2025. Pass: (v) or (vi)
  beats (iv) with the lower 95% block bound above zero, or w > 0 with the same
  bound.
- **T2 Scale information coefficient.** Per date, Spearman of forecast sd /
  implied sd against realized |log move| / implied sd; t over 91 dates by
  block bootstrap; repeated without earnings-window contexts (T4 proxy) and by
  month. Pass: mean IC > 0 with t >= 2.5; kappa = the regression slope.
- **T3 Direction information coefficient.** Same for forecast mean - forward
  against realized log return. Expected about zero; it confirms the anchor.
- **T4 Earnings split.** PIT coverage and condor P&L with and without an
  announcement proxy inside the window.
- **T5 Paper books, frozen before looking.** S1 market-anchored symmetric
  structures long-short by the T2 signal, delta-neutral, OTM only, 5% wings;
  S2 short-only with the VRP-sign gate; S3 the nominal condor (control); S4 an
  unconditional 16-20 delta / 5 delta put spread (premium control). Report
  monthly P&L, block SEs and each book's attribution into premium (Q against
  empirical P) and forecast (anchored P against empirical P).

Do not: rerun the condor with the nominal objective; tune rho or the parity
tolerance against profits; run HPO or another architecture zoo before T1/T2
(twelve pooled families already sit within 0.6 points of each other); widen
the universe before the basis gaps close.

### 8. Model changes that follow (each its own ADR; none needed for T1-T5)

The option-implied CDF as the base the model transports, or implied vol as the
reference scale, so the model learns the deviation from the market; an FHS
comparison reference; an earnings-in-window feature once a calendar is owned;
a heavier-tailed output family before any post-hoc map (`pilot-crps-student.json`
is the ready experiment): every PIT, isotonic or beta recalibration tried on
the indices (ADR-0189, 0190, 0191, 0203) worsened CRPS, so a map such as
`DynamicPITRecalibratedCDF` is tested on new dates or not at all.

## Sources

Repository evidence (children/index_options unless stated; the per-name,
persistence, tercile and history-length numbers in section 2 were computed from
the evaluation and confirmation appendices on 2026-10-11):
- docs/memos/2026-10-08-restored-universe-patchtst.md; docs/reports/production-universe-rerun-20261008/{evaluation-summary,evaluation-appendix,confirmation-appendix}.json; docs/memos/2026-10-07-production-development-stage0.md (the 85-name cycle).
- docs/memos/2026-10-08-forecast-mio-evaluation-scheme.md; configs/universe-feature-selection-20261008.json (42 features, reference).
- docs/memos/2026-10-09-robust-condor-replay.md; docs/memos/2026-10-09-condor-executable-clock-result.md; docs/reports/robust-condor-replay-20261009/{summary,calibration}.json.
- docs/research/distribution-modeling/2026-10-09-condor-selection-intuition.md (decomposition, leg table, in-sample selectors).
- docs/memos/2026-10-08-robust-condor-dual.md; docs/explanations/robust-condor-selection.md (objective, rho).
- docs/memos/2026-10-07-advanced-architecture-results.md; docs/memos/2026-10-05-pooled-zoo-417.md; docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md (zoos).
- ETF line: docs/research/real-data-backtest/2026-09-24-zoo-vs-vix.md; docs/memos/2026-09-29-risk-neutral-cdf-architecture-comparison.md; docs/memos/2026-09-29-predictive-cdf-option-surface.md; docs/memos/2026-09-30-decision-region-signal-synthesis.md; docs/memos/2026-09-30-actual-listed-wing-paired-audit.md; docs/memos/2026-09-30-center-only-conditioned-transport.md; docs/memos/2026-10-01-spy-decision-region-persistence.md; docs/memos/2026-10-01-qqq-decision-strike-diagnosis.md; docs/memos/2026-09-30-causal-decision-region-calibration-and-robust-condor.md; docs/memos/2026-09-27-index-options-modelability-ranking.md.
- ETF CDF zoo: docs/memos/2026-09-28-predictive-cdf-{comparison,hpo,methods,refinement,downside}.md; docs/memos/2026-09-30-{dynamic-tail-calibration-and-gpd,tail-constrained-quantile-blend,tail-data-feature-ablation,guard-aware-cdf-selection}.md.
- docs/research/strategy-alternatives/2026-09-30-{survey,synthesis}.md (premium, timing, the payoff identity); docs/research/distribution-modeling/2026-10-05-performance-pilots.md (the FHS gap, the CRPS ceiling, pilot overlays).
- dskit/pipeline/libs/predictive_cdf.py: HorizonEmpiricalCDF, DynamicPITRecalibratedCDF.

Literature:
- Goyal & Saretto 2009, Cross-section of option returns and volatility, JFE 94: https://personal.utdallas.edu/~axs125732/CrossOptionsJFE.pdf
- Do, Foster & Gray 2016, The profitability of volatility spread trading on ASX equity options, J. Futures Markets 36(2): https://ideas.repec.org/a/wly/jfutmk/v36y2016i2p107-126.html
- Cao & Han 2013, Cross section of option returns and idiosyncratic stock volatility, JFE 108: https://optionmetrics.com/research/j-cao-and-b-han-cross-section-of-option-returns-and-idiosyncratic-stock-volatility/
- Vasquez 2017, Equity volatility term structures and the cross section of option returns, JFQA 52(6): https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/equity-volatility-term-structures-and-the-cross-section-of-option-returns/F0A40E99FD2458367DD9A56A89783D38
- Bali, Beckmeyer, Moerke & Weigert 2023, Option return predictability with machine learning and big data, RFS 36(9): https://ideas.repec.org/a/oup/rfinst/v36y2023i9p3548-3602..html
- Gao, Xing & Zhang 2018, Anticipating uncertainty: straddles around earnings announcements, JFQA: https://resolve.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/anticipating-uncertainty-straddles-around-earnings-announcements/7B34877AD5E06304BA3C55FBA3219FDD
- Johnson 2017, Cheng 2019, Faias & Santa-Clara 2017, Israelov & Nielsen 2015, Dew-Becker & Giglio 2025: links in the 2026-09-30 survey.
