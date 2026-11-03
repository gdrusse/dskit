## Question

Across ten materially different, causal correction methods, is there a learned forecast correction that improves the frozen final M53 CDF rather than merely re-optimizing its existing blend?

## Protocol and invariants

- This is exploratory only. Locked A0002 (13-node threshold-weighted CRPS), A0003/A0004/A0008 (causal expanding folds), and A0005 (no promotion without annual consistency) remain binding.
- The frozen baseline is M53 `scores/edge-method-53-qa-mlp64-rolling-by-dispersion/forecasts.parquet`: SHA-256 `ff4cfcfcddf550d8efb74e7dbfead0ab5fb343b028fed254077212ee06723120`. Its fit description hash is `f9ddf3ff2f45a27f0fd9c37065828ec4a8e471e65b0d2317108cfb433167e04d`.
- The panel is `panel/panel-cache/frame.parquet`, SHA-256 `186def50f18e23523365807942ab4862c135c6dac1361445711daeb384c25144`. The source checkout was `b955892f676ad66893cdcd330de20173eefd0712`.
- Eligible baseline rows are exactly `band == val` and `fitted == True`: 527,079 rows, unique on `(symbol, quote_date, expiry)`. The panel key is unique and the one-to-one left join returned all 527,079 rows with no missing outcome. This is the only join used.
- `y = terminal_return / reference_scale`. For CDF values F at z = -3, -2.5, ..., 3, the score is the locked trapezoidal weighted CRPS `mean(sum_j c_j (F_j - 1[y <= z_j])^2)`, with the A0002 0.25 endpoint/zero weights. The exact M53 scores reproduced: inner fit 0.551365087, chronological monitor 0.494971212, F3/2021 0.500383638, F4/2022 0.518033460, F5/2023 0.527467805.
- History for F3 is only F1/F2 rows whose `settlement_date < 2021-01-01` (172,328 rows). It is split by quote-date order into 137,862 inner-fit and 34,466 chronological-monitor rows. F3 (119,812 rows) is the primary 2021 screen. 2020 enters only as already-settled training history; no 2020 score ranks a candidate.
- All location heads use `r = y - M53_median`, no intercept, `delta = clip(X beta, -1, 1)`, and corrected CDF `F(z - delta)`. Thus the explicit zero head is exactly M53. Ridge alpha grid: 1e3, 1e4, 1e5, 1e6, 1e7, 1e8. Alpha uses the chronological monitor; after alpha selection, F3 refits on all legal history. Missing numeric features are history-mean imputed; standardization is history-only.

## Ten-method scorecard

| # | methodology / lead | inner-fit gain | monitor gain | F3 / 2021 gain | verdict |
| ---: | --- | ---: | ---: | ---: | --- |
| 1 | 12 univariate three-tertile location regimes; best non-null `rv_22`, alpha 1e8 | -0.0014% | -0.0073% | -0.0033% | null/M53 wins |
| 2 | 12 univariate five-quintile location regimes; `rv_22`, alpha 1e8 | -0.0026% | -0.0062% | -0.0024% | null/M53 wins |
| 3 | 3x3 `rv_22` x skew location regime, alpha 1e8 | -0.0023% | -0.0063% | -0.0019% | null/M53 wins |
| 4 | 3x3 `own_iv` x `rn_iqr` location regime, alpha 1e8 | -0.0023% | -0.0062% | -0.0024% | null/M53 wins |
| 5 | 3x3 DTE x relative-spread location regime, alpha 1e8 | -0.0018% | -0.0067% | -0.0026% | null/M53 wins |
| 6 | three-tertile `rv_22` conditional CDF scale | +0.1146% | -0.5592% | -0.2747% | train/monitor divergence; stop |
| 7 | three-tertile `rn_iqr` conditional CDF scale | +0.0322% | -0.3341% | 0.0000% | final refit is the exact null |
| 8 | three-tertile `own_iv` M53/normal-CDF mixture | 0.0000% | 0.0000% | 0.0000% | exact null selected |
| 9 | PCA residual ridge, 1/2/3/5/8 PCs | +0.0844% | +0.8760% | -0.4928% | monitor-leading PC1 alpha 1e3 fails F3; near-null F3 best is only +0.0007% |
| 10 | actual two-step forward ridge from 46 quote-time features | +0.0749% | +0.0803% | +0.1268% | F4 wins but F5 loses; not stable |

Methods 1–5 are independent segmentation designs. Method 6 applies `F(z / s_g)` with a causal three-bin `rv_22` group scale. Method 7 is the equivalent `rn_iqr` scale test. Method 8 uses `lambda_g F(z) + (1-lambda_g) Phi(z)` by causal `own_iv` tercile. Method 9 fits PCA only on inner history. Every method has an admissible exact-null candidate.

## Forward selection: complete candidate ranks and stop rationale

This is a genuine forward-selection record, not a two-feature anecdote. Each remaining feature receives its own monitor-selected alpha; every remaining feature is scored on inner fit, monitor, and refit F3. The complete rankings appear below. Selection is explicitly **2021-primary among monitor-positive candidates**, per the owner instruction to use 2021 rather than COVID 2020. The monitor-only leader is also reported and never silently substituted.

Stage 1: monitor rank #1 was `own_iv` (+0.8000% monitor) but it lost -0.3124% in F3, so it was rejected. The 2021-primary, monitor-positive lead was `rn_median` (+0.2813% monitor; +0.0911% F3; alpha 1e3).

Stage 2, anchored on the frozen `rn_median` prefix: monitor rank #1 `own_iv` (+0.8983% monitor) lost -0.2127% in F3 and was rejected. The 2021-primary, monitor-positive lead was `chain_log_curvature25` (alpha 1e5; +0.0803% monitor; +0.1268% F3).

Frozen annual confirmation for that pair did **not** use F4/F5 to select a feature or alpha: F4 was 0.516202060 versus M53 0.518033460 (+0.3535%), while F5 was 0.528255333 versus 0.527467805 (-0.1493%). The one-feature `rn_median` head has the same pattern: F4 +0.3617%, F5 -0.1735%. The row-weighted F4–F5 number is deliberately not a decision statistic: the explicit 2023 loss rejects stability under A0005. A third step would be post-failure mining, so selection stopped here.

## Reproducibility

- WSL2 was used because it had measured 9.7 GB free before dispatch and compatible Python 3.12.3 / NumPy 2.5.4 / pandas 3.1.0 / PyArrow 19.0.1. The live MIO process was not interrupted. Windows was unavailable and the Mac had an incompatible Python/revision; neither was used.
- Primary ten-method harness SHA-256: `643fbb8043f60116cb85a3e869d4600ce9aaf2c0433d937555b2f3d0345def7a`; anchored forward harness: `bf8d791b8ffd045db4dbec1471b26359e7d627e341ec4a73750a15ab93614de1`; frozen one-feature and pair confirmation harnesses: `a2e2531ce1a5f99561e697af2f8de07f342778e52427cea37bcf3863c3b9c673`, `9ebb2f607c085b913e7cc80bc08bbe616f9b9ee299008c223e15fd0aaca8adeb`.

## Complete primary-method leaderboards

All gains are percentage improvements over the corresponding M53 score; negative means worse. `null=1` is the exact M53 correction. These are the full candidate records, including the zero-correction rows.

### 01_univariate_3bin_location

| candidate | alpha / transform | fit gain | monitor gain | F3 gain | null |
| --- | --- | ---: | ---: | ---: | ---: |
| rv_22 | 1e+03 | -0.954816% | -6.425825% | -3.576641% | 0 |
| rv_22 | 1e+04 | -0.377031% | -5.029022% | -2.837700% | 0 |
| rv_22 | 1e+05 | +0.377627% | -1.482324% | -0.836321% | 0 |
| rv_22 | 1e+06 | +0.089726% | -0.178133% | -0.094107% | 0 |
| rv_22 | 1e+07 | +0.007792% | -0.023048% | -0.011513% | 0 |
| rv_22 | 1e+08 | -0.001362% | -0.007338% | -0.003292% | 0 |
| rv_22 | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| own_iv | 1e+03 | -1.314054% | -7.018339% | -3.048341% | 0 |
| own_iv | 1e+04 | -0.698437% | -5.537235% | -2.395167% | 0 |
| own_iv | 1e+05 | +0.229009% | -1.691417% | -0.684312% | 0 |
| own_iv | 1e+06 | +0.067020% | -0.208441% | -0.075982% | 0 |
| own_iv | 1e+07 | +0.005395% | -0.026229% | -0.009648% | 0 |
| own_iv | 1e+08 | -0.001605% | -0.007666% | -0.003068% | 0 |
| own_iv | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| chain_log_skew25 | 1e+03 | -1.377131% | -7.444120% | -2.290449% | 0 |
| chain_log_skew25 | 1e+04 | -0.765093% | -5.820868% | -1.866969% | 0 |
| chain_log_skew25 | 1e+05 | +0.156851% | -1.740375% | -0.624534% | 0 |
| chain_log_skew25 | 1e+06 | +0.050917% | -0.213491% | -0.079399% | 0 |
| chain_log_skew25 | 1e+07 | +0.003458% | -0.026907% | -0.010639% | 0 |
| chain_log_skew25 | 1e+08 | -0.001959% | -0.007902% | -0.003632% | 0 |
| chain_log_skew25 | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| chain_log_curvature25 | 1e+03 | -1.306849% | -6.884547% | -1.934959% | 0 |
| chain_log_curvature25 | 1e+04 | -0.695479% | -5.384885% | -1.569458% | 0 |
| chain_log_curvature25 | 1e+05 | +0.195035% | -1.612835% | -0.517832% | 0 |
| chain_log_curvature25 | 1e+06 | +0.056977% | -0.198507% | -0.066164% | 0 |
| chain_log_curvature25 | 1e+07 | +0.004101% | -0.025399% | -0.009253% | 0 |
| chain_log_curvature25 | 1e+08 | -0.001893% | -0.007761% | -0.003447% | 0 |
| chain_log_curvature25 | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| chain_log_rel_spread | 1e+03 | -1.176017% | -7.980803% | -2.505283% | 0 |
| chain_log_rel_spread | 1e+04 | -0.511262% | -6.303249% | -1.976175% | 0 |
| chain_log_rel_spread | 1e+05 | +0.389557% | -1.933034% | -0.555433% | 0 |
| chain_log_rel_spread | 1e+06 | +0.096124% | -0.238205% | -0.058932% | 0 |
| chain_log_rel_spread | 1e+07 | +0.008529% | -0.029260% | -0.007915% | 0 |
| chain_log_rel_spread | 1e+08 | -0.001287% | -0.007962% | -0.002932% | 0 |
| chain_log_rel_spread | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| rn_median | 1e+03 | -1.436942% | -7.683185% | -2.371616% | 0 |
| rn_median | 1e+04 | -0.810535% | -6.069624% | -1.926561% | 0 |
| rn_median | 1e+05 | +0.175771% | -1.863354% | -0.633217% | 0 |
| rn_median | 1e+06 | +0.058897% | -0.229966% | -0.078818% | 0 |
| rn_median | 1e+07 | +0.004542% | -0.028425% | -0.010131% | 0 |
| rn_median | 1e+08 | -0.001689% | -0.007878% | -0.003156% | 0 |
| rn_median | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| rn_iqr | 1e+03 | -1.048722% | -8.549899% | -3.209832% | 0 |
| rn_iqr | 1e+04 | -0.471537% | -6.705995% | -2.596529% | 0 |
| rn_iqr | 1e+05 | +0.322037% | -1.995167% | -0.812469% | 0 |
| rn_iqr | 1e+06 | +0.080525% | -0.239987% | -0.093546% | 0 |
| rn_iqr | 1e+07 | +0.006812% | -0.029342% | -0.011474% | 0 |
| rn_iqr | 1e+08 | -0.001461% | -0.007969% | -0.003289% | 0 |
| rn_iqr | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| term_slope_prev | 1e+03 | -1.449947% | -8.019048% | -2.691058% | 0 |
| term_slope_prev | 1e+04 | -0.755569% | -6.194700% | -2.132103% | 0 |
| term_slope_prev | 1e+05 | +0.197850% | -1.802741% | -0.640345% | 0 |
| term_slope_prev | 1e+06 | +0.056886% | -0.219489% | -0.075472% | 0 |
| term_slope_prev | 1e+07 | +0.005126% | -0.027043% | -0.009382% | 0 |
| term_slope_prev | 1e+08 | -0.000711% | -0.007440% | -0.002723% | 0 |
| term_slope_prev | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| implied_minus_realized_variance | 1e+03 | -0.868544% | -10.278202% | -3.549639% | 0 |
| implied_minus_realized_variance | 1e+04 | -0.283297% | -7.998496% | -2.898551% | 0 |
| implied_minus_realized_variance | 1e+05 | +0.438898% | -2.297437% | -0.956986% | 0 |
| implied_minus_realized_variance | 1e+06 | +0.100082% | -0.267880% | -0.116262% | 0 |
| implied_minus_realized_variance | 1e+07 | +0.008897% | -0.032049% | -0.013832% | 0 |
| implied_minus_realized_variance | 1e+08 | -0.001250% | -0.008257% | -0.003481% | 0 |
| implied_minus_realized_variance | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| log_calendar_dte | 1e+03 | -1.608900% | -8.829322% | -2.922060% | 0 |
| log_calendar_dte | 1e+04 | -0.931401% | -7.032156% | -2.380481% | 0 |
| log_calendar_dte | 1e+05 | +0.184559% | -2.214900% | -0.772677% | 0 |
| log_calendar_dte | 1e+06 | +0.066896% | -0.275766% | -0.092552% | 0 |
| log_calendar_dte | 1e+07 | +0.005495% | -0.033130% | -0.011449% | 0 |
| log_calendar_dte | 1e+08 | -0.001592% | -0.008350% | -0.003287% | 0 |
| log_calendar_dte | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| life_fraction_calendar | 1e+03 | -1.488259% | -8.537638% | -2.863495% | 0 |
| life_fraction_calendar | 1e+04 | -0.834863% | -6.736619% | -2.326406% | 0 |
| life_fraction_calendar | 1e+05 | +0.185235% | -2.060758% | -0.753831% | 0 |
| life_fraction_calendar | 1e+06 | +0.061279% | -0.253422% | -0.091363% | 0 |
| life_fraction_calendar | 1e+07 | +0.004804% | -0.030806% | -0.011360% | 0 |
| life_fraction_calendar | 1e+08 | -0.001662% | -0.008117% | -0.003278% | 0 |
| life_fraction_calendar | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| momentum_22 | 1e+03 | -1.530982% | -8.077158% | -2.904426% | 0 |
| momentum_22 | 1e+04 | -0.879672% | -6.363331% | -2.359841% | 0 |
| momentum_22 | 1e+05 | +0.161308% | -1.930460% | -0.763397% | 0 |
| momentum_22 | 1e+06 | +0.057777% | -0.235935% | -0.092212% | 0 |
| momentum_22 | 1e+07 | +0.004438% | -0.028995% | -0.011440% | 0 |
| momentum_22 | 1e+08 | -0.001699% | -0.007935% | -0.003286% | 0 |
| momentum_22 | null | +0.000000% | +0.000000% | +0.000000% | 1 |

### 02_univariate_5bin_location

| candidate | alpha / transform | fit gain | monitor gain | F3 gain | null |
| --- | --- | ---: | ---: | ---: | ---: |
| rv_22 | 1e+03 | -0.697193% | -6.629235% | -3.874508% | 0 |
| rv_22 | 1e+04 | +0.089365% | -4.453612% | -2.704723% | 0 |
| rv_22 | 1e+05 | +0.433110% | -0.926920% | -0.585703% | 0 |
| rv_22 | 1e+06 | +0.069378% | -0.102027% | -0.061631% | 0 |
| rv_22 | 1e+07 | +0.004313% | -0.014921% | -0.007774% | 0 |
| rv_22 | 1e+08 | -0.002612% | -0.006200% | -0.002420% | 0 |
| rv_22 | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| own_iv | 1e+03 | -1.254875% | -6.829439% | -2.929233% | 0 |
| own_iv | 1e+04 | -0.416239% | -4.744985% | -2.003958% | 0 |
| own_iv | 1e+05 | +0.230366% | -1.105201% | -0.419231% | 0 |
| own_iv | 1e+06 | +0.042575% | -0.128690% | -0.044829% | 0 |
| own_iv | 1e+07 | +0.002424% | -0.018038% | -0.006564% | 0 |
| own_iv | 1e+08 | -0.001908% | -0.006845% | -0.002760% | 0 |
| own_iv | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| chain_log_skew25 | 1e+03 | -1.300152% | -7.168343% | -2.241085% | 0 |
| chain_log_skew25 | 1e+04 | -0.477627% | -4.911274% | -1.627780% | 0 |
| chain_log_skew25 | 1e+05 | +0.171150% | -1.117552% | -0.411752% | 0 |
| chain_log_skew25 | 1e+06 | +0.032279% | -0.129661% | -0.049235% | 0 |
| chain_log_skew25 | 1e+07 | +0.001171% | -0.018299% | -0.007542% | 0 |
| chain_log_skew25 | 1e+08 | -0.002193% | -0.007039% | -0.003322% | 0 |
| chain_log_skew25 | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| chain_log_curvature25 | 1e+03 | -1.177312% | -6.563440% | -1.774268% | 0 |
| chain_log_curvature25 | 1e+04 | -0.361915% | -4.491207% | -1.262583% | 0 |
| chain_log_curvature25 | 1e+05 | +0.218247% | -1.018940% | -0.301009% | 0 |
| chain_log_curvature25 | 1e+06 | +0.038652% | -0.118516% | -0.035698% | 0 |
| chain_log_curvature25 | 1e+07 | +0.001832% | -0.017182% | -0.006114% | 0 |
| chain_log_curvature25 | 1e+08 | -0.002125% | -0.006937% | -0.003132% | 0 |
| chain_log_curvature25 | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| chain_log_rel_spread | 1e+03 | -1.081258% | -7.867973% | -2.402160% | 0 |
| chain_log_rel_spread | 1e+04 | -0.186766% | -5.468109% | -1.648737% | 0 |
| chain_log_rel_spread | 1e+05 | +0.362878% | -1.273682% | -0.329674% | 0 |
| chain_log_rel_spread | 1e+06 | +0.062003% | -0.147557% | -0.033646% | 0 |
| chain_log_rel_spread | 1e+07 | +0.004457% | -0.019937% | -0.005451% | 0 |
| chain_log_rel_spread | 1e+08 | -0.001702% | -0.007027% | -0.002686% | 0 |
| chain_log_rel_spread | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| rn_median | 1e+03 | -1.368523% | -7.418885% | -2.249433% | 0 |
| rn_median | 1e+04 | -0.520447% | -5.155848% | -1.615863% | 0 |
| rn_median | 1e+05 | +0.188593% | -1.201012% | -0.390785% | 0 |
| rn_median | 1e+06 | +0.036916% | -0.139411% | -0.045196% | 0 |
| rn_median | 1e+07 | +0.001841% | -0.019114% | -0.006689% | 0 |
| rn_median | 1e+08 | -0.001964% | -0.006945% | -0.002811% | 0 |
| rn_median | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| rn_iqr | 1e+03 | -0.851220% | -8.477748% | -3.211063% | 0 |
| rn_iqr | 1e+04 | -0.102736% | -5.816412% | -2.300150% | 0 |
| rn_iqr | 1e+05 | +0.327660% | -1.300136% | -0.527935% | 0 |
| rn_iqr | 1e+06 | +0.054909% | -0.147385% | -0.057220% | 0 |
| rn_iqr | 1e+07 | +0.003694% | -0.019874% | -0.007845% | 0 |
| rn_iqr | 1e+08 | -0.001778% | -0.007020% | -0.002926% | 0 |
| rn_iqr | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| term_slope_prev | 1e+03 | -1.378531% | -7.899640% | -2.645806% | 0 |
| term_slope_prev | 1e+04 | -0.451821% | -5.319870% | -1.842026% | 0 |
| term_slope_prev | 1e+05 | +0.197354% | -1.181276% | -0.413503% | 0 |
| term_slope_prev | 1e+06 | +0.036076% | -0.135978% | -0.046253% | 0 |
| term_slope_prev | 1e+07 | +0.002628% | -0.018462% | -0.006426% | 0 |
| term_slope_prev | 1e+08 | -0.000966% | -0.006579% | -0.002427% | 0 |
| term_slope_prev | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| implied_minus_realized_variance | 1e+03 | -0.588559% | -11.715871% | -3.914052% | 0 |
| implied_minus_realized_variance | 1e+04 | +0.244735% | -7.855612% | -2.836270% | 0 |
| implied_minus_realized_variance | 1e+05 | +0.516864% | -1.651720% | -0.689684% | 0 |
| implied_minus_realized_variance | 1e+06 | +0.082069% | -0.179808% | -0.077214% | 0 |
| implied_minus_realized_variance | 1e+07 | +0.006279% | -0.023071% | -0.009727% | 0 |
| implied_minus_realized_variance | 1e+08 | -0.001768% | -0.007360% | -0.002949% | 0 |
| implied_minus_realized_variance | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| log_calendar_dte | 1e+03 | -1.559195% | -8.692553% | -2.871802% | 0 |
| log_calendar_dte | 1e+04 | -0.617245% | -6.086849% | -2.070643% | 0 |
| log_calendar_dte | 1e+05 | +0.204536% | -1.442184% | -0.492551% | 0 |
| log_calendar_dte | 1e+06 | +0.041560% | -0.167400% | -0.055137% | 0 |
| log_calendar_dte | 1e+07 | +0.002354% | -0.021954% | -0.007666% | 0 |
| log_calendar_dte | 1e+08 | -0.001912% | -0.007229% | -0.002909% | 0 |
| log_calendar_dte | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| life_fraction_calendar | 1e+03 | -1.430269% | -8.451089% | -2.848172% | 0 |
| life_fraction_calendar | 1e+04 | -0.540278% | -5.866142% | -2.059592% | 0 |
| life_fraction_calendar | 1e+05 | +0.197923% | -1.363588% | -0.499616% | 0 |
| life_fraction_calendar | 1e+06 | +0.038658% | -0.157579% | -0.056903% | 0 |
| life_fraction_calendar | 1e+07 | +0.002026% | -0.020950% | -0.007861% | 0 |
| life_fraction_calendar | 1e+08 | -0.001946% | -0.007128% | -0.002928% | 0 |
| life_fraction_calendar | null | +0.000000% | +0.000000% | +0.000000% | 1 |
| momentum_22 | 1e+03 | -1.503774% | -7.769730% | -3.020435% | 0 |
| momentum_22 | 1e+04 | -0.594928% | -5.360664% | -2.178187% | 0 |
| momentum_22 | 1e+05 | +0.185355% | -1.220460% | -0.522869% | 0 |
| momentum_22 | 1e+06 | +0.037362% | -0.139970% | -0.059069% | 0 |
| momentum_22 | 1e+07 | +0.001897% | -0.019147% | -0.008073% | 0 |
| momentum_22 | 1e+08 | -0.001958% | -0.006948% | -0.002949% | 0 |
| momentum_22 | null | +0.000000% | +0.000000% | +0.000000% | 1 |

### 03_2d_rv_skew_location

| candidate | alpha / transform | fit gain | monitor gain | F3 gain | null |
| --- | --- | ---: | ---: | ---: | ---: |
| rv22_x_skew | 1e+03 | -0.732656% | -5.833565% | -3.074375% | 0 |
| rv22_x_skew | 1e+04 | +0.157435% | -3.167932% | -1.734252% | 0 |
| rv22_x_skew | 1e+05 | +0.219621% | -0.530465% | -0.292829% | 0 |
| rv22_x_skew | 1e+06 | +0.027881% | -0.060785% | -0.031756% | 0 |
| rv22_x_skew | 1e+07 | +0.000578% | -0.011308% | -0.004644% | 0 |
| rv22_x_skew | 1e+08 | -0.002254% | -0.006338% | -0.001927% | 0 |
| rv22_x_skew | null | +0.000000% | +0.000000% | +0.000000% | 1 |

### 04_2d_iv_rniqr_location

| candidate | alpha / transform | fit gain | monitor gain | F3 gain | null |
| --- | --- | ---: | ---: | ---: | ---: |
| iv_x_rniqr | 1e+03 | -0.469562% | -9.902239% | -3.383421% | 0 |
| iv_x_rniqr | 1e+04 | +0.457279% | -4.813723% | -1.869238% | 0 |
| iv_x_rniqr | 1e+05 | +0.305174% | -0.705927% | -0.283283% | 0 |
| iv_x_rniqr | 1e+06 | +0.037905% | -0.076552% | -0.029338% | 0 |
| iv_x_rniqr | 1e+07 | +0.001427% | -0.012634% | -0.004864% | 0 |
| iv_x_rniqr | 1e+08 | -0.002342% | -0.006241% | -0.002439% | 0 |
| iv_x_rniqr | null | +0.000000% | +0.000000% | +0.000000% | 1 |

### 05_2d_dte_spread_location

| candidate | alpha / transform | fit gain | monitor gain | F3 gain | null |
| --- | --- | ---: | ---: | ---: | ---: |
| dte_x_spread | 1e+03 | -1.073376% | -7.896110% | -2.410682% | 0 |
| dte_x_spread | 1e+04 | -0.010709% | -4.851093% | -1.461414% | 0 |
| dte_x_spread | 1e+05 | +0.328146% | -0.976308% | -0.246696% | 0 |
| dte_x_spread | 1e+06 | +0.049728% | -0.111428% | -0.025239% | 0 |
| dte_x_spread | 1e+07 | +0.003085% | -0.016260% | -0.004626% | 0 |
| dte_x_spread | 1e+08 | -0.001840% | -0.006659% | -0.002604% | 0 |
| dte_x_spread | null | +0.000000% | +0.000000% | +0.000000% | 1 |

### 06_rv22_segment_scale

| candidate | alpha / transform | fit gain | monitor gain | F3 gain | null |
| --- | --- | ---: | ---: | ---: | ---: |
| rv22_segment_scale | inner=1.1,1.1,0.9; full=1.0,1.1,1.0 | +0.114599% | -0.559212% | -0.274749% | 0 |
| rv22_segment_scale__null | null | +0.000000% | +0.000000% | +0.000000% | 1 |

### 07_rniqr_segment_scale

| candidate | alpha / transform | fit gain | monitor gain | F3 gain | null |
| --- | --- | ---: | ---: | ---: | ---: |
| rniqr_segment_scale | inner=1.0,1.0,1.1; full=1.0,1.0,1.0 | +0.032187% | -0.334121% | +0.000000% | 1 |
| rniqr_segment_scale__null | null | +0.000000% | +0.000000% | +0.000000% | 1 |

### 08_owniv_segment_normal_mix

| candidate | alpha / transform | fit gain | monitor gain | F3 gain | null |
| --- | --- | ---: | ---: | ---: | ---: |
| owniv_segment_normal_mix | inner=1.0,1.0,1.0; full=1.0,1.0,1.0 | +0.000000% | +0.000000% | +0.000000% | 1 |
| owniv_segment_normal_mix__null | null | +0.000000% | +0.000000% | +0.000000% | 1 |

### 09_pca_ridge

| candidate | alpha / transform | fit gain | monitor gain | F3 gain | null |
| --- | --- | ---: | ---: | ---: | ---: |
| pc1 | 1e+03 | +0.084376% | +0.876010% | -0.492845% | 0 |
| pc1 | 1e+04 | +0.091475% | +0.872692% | -0.486328% | 0 |
| pc1 | 1e+05 | +0.150520% | +0.839838% | -0.426609% | 0 |
| pc1 | 1e+06 | +0.285956% | +0.578122% | -0.155839% | 0 |
| pc1 | 1e+07 | +0.091671% | +0.127122% | -0.001078% | 0 |
| pc1 | 1e+08 | +0.005632% | +0.011299% | +0.000665% | 0 |
| pc2 | 1e+03 | +0.060431% | +0.448012% | -0.732516% | 0 |
| pc2 | 1e+04 | +0.069621% | +0.450445% | -0.723663% | 0 |
| pc2 | 1e+05 | +0.147204% | +0.467837% | -0.644078% | 0 |
| pc2 | 1e+06 | +0.325682% | +0.416651% | -0.270618% | 0 |
| pc2 | 1e+07 | +0.104422% | +0.103226% | -0.020588% | 0 |
| pc2 | 1e+08 | +0.006778% | +0.008591% | -0.001588% | 0 |
| pc3 | 1e+03 | -0.278578% | -1.556143% | -1.373562% | 0 |
| pc3 | 1e+04 | -0.247827% | -1.502714% | -1.351258% | 0 |
| pc3 | 1e+05 | -0.014681% | -1.081964% | -1.163564% | 0 |
| pc3 | 1e+06 | +0.421879% | -0.049290% | -0.463942% | 0 |
| pc3 | 1e+07 | +0.133085% | +0.050069% | -0.046504% | 0 |
| pc3 | 1e+08 | +0.011959% | +0.002126% | -0.004930% | 0 |
| pc5 | 1e+03 | -0.187806% | -1.906357% | -1.337868% | 0 |
| pc5 | 1e+04 | -0.156599% | -1.840201% | -1.315511% | 0 |
| pc5 | 1e+05 | +0.075723% | -1.320050% | -1.123343% | 0 |
| pc5 | 1e+06 | +0.468287% | -0.101803% | -0.435295% | 0 |
| pc5 | 1e+07 | +0.139600% | +0.044513% | -0.041707% | 0 |
| pc5 | 1e+08 | +0.012547% | +0.001591% | -0.004380% | 0 |
| pc8 | 1e+03 | -0.448065% | -2.354127% | -1.320929% | 0 |
| pc8 | 1e+04 | -0.389187% | -2.245336% | -1.290509% | 0 |
| pc8 | 1e+05 | +0.005175% | -1.483978% | -1.055532% | 0 |
| pc8 | 1e+06 | +0.508434% | -0.071357% | -0.383192% | 0 |
| pc8 | 1e+07 | +0.147286% | +0.052053% | -0.033538% | 0 |
| pc8 | 1e+08 | +0.013611% | +0.002422% | -0.003445% | 0 |
| pca_null | null | +0.000000% | +0.000000% | +0.000000% | 1 |

### 10_forward_ridge — stage 1, all 46 remaining features

| feature | alpha | fit gain | monitor gain | F3 gain |
| --- | ---: | ---: | ---: | ---: |
| own_iv | 1e+03 | +0.185611% | +0.799980% | -0.312394% |
| chain_log_atm_iv | 1e+03 | +0.133330% | +0.643720% | -0.074542% |
| up_rv_5 | 1e+03 | -0.058681% | +0.538871% | -0.462633% |
| rv_5 | 1e+03 | +0.051567% | +0.497812% | -0.256074% |
| rv_22 | 1e+04 | +0.020749% | +0.429152% | -0.852677% |
| up_rv_22 | 1e+05 | +0.199876% | +0.357749% | -0.561268% |
| down_rv_22 | 1e+03 | +0.105485% | +0.320698% | -0.565283% |
| rn_median | 1e+03 | +0.012814% | +0.281308% | +0.091090% |
| down_rv_5 | 1e+03 | +0.054146% | +0.117760% | -0.046411% |
| rv_1 | 1e+03 | +0.011774% | +0.079029% | -0.015768% |
| chain_log_quote_depth | 1e+04 | +0.046695% | +0.075367% | -0.319570% |
| rn_right_tail_integral | 1e+06 | +0.139028% | +0.051772% | -0.003491% |
| chain_log_skew25 | 1e+03 | -0.015089% | +0.050095% | -0.012528% |
| chain_log_atm_iv_change_5 | 1e+05 | +0.177016% | +0.045569% | -0.282087% |
| momentum_5 | 1e+05 | -0.032930% | +0.036651% | -0.106542% |
| chain_log_atm_iv_change_1 | 1e+05 | +0.019531% | +0.033249% | -0.079398% |
| momentum_22 | 1e+05 | -0.089189% | +0.025446% | -0.095081% |
| rv_66 | 1e+06 | +0.231654% | +0.001387% | -0.077711% |
| chain_log_atm_iv_change_22 | 1e+06 | +0.008387% | +0.000219% | -0.010905% |
| chain_liquidity_asymmetry | 1e+08 | -0.012279% | -0.002636% | +0.000743% |
| chain_log_vega_oi | 1e+08 | -0.014013% | -0.002663% | +0.002073% |
| chain_log_skew25_change_22 | 1e+08 | -0.005527% | -0.002742% | -0.003979% |
| chain_log_rel_spread | 1e+08 | -0.006359% | -0.002820% | +0.003308% |
| chain_log_contracts | 1e+08 | -0.007626% | -0.002820% | +0.000449% |
| chain_log_curvature25_change_22 | 1e+08 | -0.006292% | -0.002919% | -0.003349% |
| chain_log_put_call_oi | 1e+08 | -0.013798% | -0.002997% | +0.001108% |
| chain_log_delta_oi | 1e+08 | -0.013232% | -0.003155% | +0.001736% |
| chain_log_gamma_oi | 1e+08 | -0.013208% | -0.003219% | +0.001446% |
| rn_skewness | 1e+08 | -0.011118% | -0.003220% | +0.000615% |
| rn_iqr | 1e+06 | +0.081832% | -0.003293% | -0.018882% |
| chain_log_open_interest | 1e+08 | -0.013175% | -0.003576% | +0.000298% |
| chain_log_curvature25_change_1 | 1e+08 | -0.011526% | -0.003728% | +0.000066% |
| chain_log_skew25_change_1 | 1e+06 | -0.009545% | -0.003849% | -0.001640% |
| chain_put_call_volume_imbalance | 1e+08 | -0.011842% | -0.004132% | +0.000800% |
| term_slope_next | 1e+08 | -0.007055% | -0.004171% | +0.000405% |
| term_slope_prev | 1e+08 | -0.007045% | -0.004223% | +0.002033% |
| chain_log_curvature25_change_5 | 1e+08 | -0.008297% | -0.004322% | -0.000793% |
| chain_log_volume | 1e+08 | -0.011744% | -0.004334% | +0.001837% |
| rn_kurtosis | 1e+08 | -0.010046% | -0.004382% | -0.000268% |
| series_age_calendar | 1e+08 | -0.007435% | -0.004704% | -0.002516% |
| chain_log_skew25_change_5 | 1e+08 | -0.008235% | -0.005013% | -0.001859% |
| chain_log_curvature25 | 1e+07 | -0.002029% | -0.005935% | +0.003598% |
| rn_left_tail_integral | 1e+08 | -0.005052% | -0.006183% | -0.001599% |
| life_fraction_calendar | 1e+08 | -0.011487% | -0.006447% | -0.000331% |
| implied_minus_realized_variance | 1e+08 | -0.001832% | -0.006649% | -0.002249% |
| log_calendar_dte | 1e+08 | -0.006747% | -0.007053% | -0.001239% |

### 10_forward_ridge — stage 2, all 45 remaining additions after `rn_median`

| added feature | alpha | fit gain | monitor gain | F3 gain |
| --- | ---: | ---: | ---: | ---: |
| own_iv | 1e+03 | +0.174791% | +0.898314% | -0.212667% |
| chain_log_atm_iv | 1e+03 | +0.117880% | +0.734984% | +0.009694% |
| up_rv_5 | 1e+03 | -0.052079% | +0.707556% | -0.356457% |
| rv_5 | 1e+03 | +0.054975% | +0.703781% | -0.142074% |
| rv_22 | 1e+04 | +0.020313% | +0.488893% | -0.743619% |
| down_rv_22 | 1e+04 | +0.150980% | +0.458013% | -0.390722% |
| up_rv_22 | 1e+05 | +0.196748% | +0.440285% | -0.480366% |
| down_rv_5 | 1e+03 | +0.057386% | +0.382913% | +0.064038% |
| chain_log_quote_depth | 1e+03 | +0.050878% | +0.355437% | -0.211968% |
| rv_1 | 1e+03 | +0.023379% | +0.342638% | +0.083484% |
| chain_log_atm_iv_change_1 | 1e+03 | -0.001657% | +0.318697% | -0.065730% |
| chain_log_skew25 | 1e+03 | +0.002424% | +0.299462% | +0.071485% |
| chain_liquidity_asymmetry | 1e+03 | +0.029056% | +0.282167% | +0.103114% |
| momentum_22 | 1e+03 | -0.152558% | +0.281536% | -0.072525% |
| chain_log_put_call_oi | 1e+03 | +0.032373% | +0.280822% | +0.100636% |
| chain_log_curvature25_change_1 | 1e+03 | +0.013080% | +0.280580% | +0.091105% |
| chain_log_curvature25_change_5 | 1e+03 | +0.013458% | +0.277946% | +0.091040% |
| chain_log_skew25_change_1 | 1e+03 | +0.015468% | +0.275637% | +0.083342% |
| chain_log_curvature25_change_22 | 1e+03 | +0.014800% | +0.275399% | +0.091190% |
| chain_put_call_volume_imbalance | 1e+03 | +0.003387% | +0.259850% | +0.091121% |
| chain_log_gamma_oi | 1e+03 | +0.004217% | +0.241978% | +0.117552% |
| momentum_5 | 1e+04 | -0.100085% | +0.235092% | -0.100393% |
| chain_log_atm_iv_change_5 | 1e+05 | +0.191776% | +0.232691% | -0.197589% |
| chain_log_volume | 1e+03 | -0.002913% | +0.228808% | +0.106531% |
| term_slope_prev | 1e+03 | +0.018198% | +0.191976% | +0.085404% |
| chain_log_delta_oi | 1e+03 | +0.001068% | +0.189616% | +0.093470% |
| chain_log_atm_iv_change_22 | 1e+05 | +0.080812% | +0.181622% | +0.019973% |
| chain_log_skew25_change_22 | 1e+04 | +0.028124% | +0.177324% | +0.063479% |
| chain_log_vega_oi | 1e+03 | +0.006796% | +0.177074% | +0.094576% |
| chain_log_skew25_change_5 | 1e+04 | +0.030416% | +0.173501% | +0.055257% |
| chain_log_open_interest | 1e+03 | -0.001483% | +0.128313% | +0.068029% |
| term_slope_next | 1e+04 | +0.006242% | +0.102512% | +0.063153% |
| rn_right_tail_integral | 1e+05 | +0.365403% | +0.093450% | -0.147673% |
| chain_log_curvature25 | 1e+05 | +0.074901% | +0.080279% | +0.126837% |
| series_age_calendar | 1e+05 | +0.071870% | +0.051527% | -0.088246% |
| rv_66 | 1e+06 | +0.232487% | +0.038639% | -0.043867% |
| rn_iqr | 1e+06 | +0.085376% | +0.037097% | +0.013736% |
| chain_log_rel_spread | 1e+06 | +0.132864% | +0.009730% | +0.114070% |
| rn_kurtosis | 1e+06 | +0.009678% | +0.004748% | +0.020600% |
| chain_log_contracts | 1e+07 | +0.000981% | -0.001939% | +0.007727% |
| rn_skewness | 1e+08 | -0.011336% | -0.003563% | +0.002974% |
| life_fraction_calendar | 1e+08 | -0.011595% | -0.004851% | +0.001671% |
| log_calendar_dte | 1e+08 | -0.007924% | -0.005347% | +0.001672% |
| rn_left_tail_integral | 1e+08 | -0.004832% | -0.005475% | -0.000298% |
| implied_minus_realized_variance | 1e+08 | -0.003461% | -0.005791% | +0.001675% |

## Decision

No ten-method challenger is accepted and no sealed 2024–2025 holdout was opened. Segmentation, conditional shape correction, PCA, and the two complete forward-selection steps produced either exact nulls, clear train/monitor divergence, or a F3/F4 pattern that reversed in 2023. M53 remains unchanged. The narrow lead is a regime-sensitive `rn_median` location effect, but it is not a stable standalone learned model.
