## Question

What could lift predictive-CDF skill over the empirical baseline (model architectures, segmentation, inputs)? Which ideas can be piloted cheaply, as JSON only, before a full run?

## Finding

### Where we stand

- **Best offline result:** the GPD-tailed transport, +1.83% CRPS skill [1.04, 2.79]. This is on the SPY/QQQ/IWM panel for 2019–25, which has been viewed many times.
- **Learned heads lose on their own.** MLP, GRU, quantile forest (QRF), NGBoost, CatBoost, flows and set models all score below the baseline. The 7-day workflow scored −2.95% for QQQ and −1.71% for IWM.
- **Overfitting is the pattern:** about +9% skill on training rows against −22% on development.
- **The realistic ceiling is about 0.5–2% CRPS.** Baruník & Hanuš got about 1.8%, and only from better inputs: the same network with the same inputs gained nothing.
- **Why the ceiling is low (derived here, Gaussian case):** a vol forecast off by a factor k costs about (k−1)²/4 in CRPS. A 20% vol error costs about 1%.

### A gap in the baseline (new)

- `HorizonEmpiricalCDF` fits quantiles of **raw** returns (`raw = y * a`). It therefore ignores today's vol.
- The standard benchmark is filtered historical simulation (FHS): a standardized shape times today's vol (McNeil & Frey 2000; Kuester et al. 2006). It was never used as a reference.
- The only vol-scaled reference tried, `ScaledEmpiricalCDF`, lost by 3.3%. Its ridge regression uses all ~200 panel columns, so overfitting is the likelier cause; that result is not evidence against vol scaling.
- **What the outcome would mean:**
  - If FHS beats the raw baseline, every skill number so far was measured against a weaker bar.
  - If FHS loses, the trailing 22-day vol scale is the weak link, and the scale is what to fix.

### What the literature says helps (strongest first)

1. **Inputs over architecture.** Implied vol, downside semivariance and range/realized measures help. HAR with implied vol beats HAR (Busch et al. 2011; Kambouroudis et al. 2021).
2. **Pooling across assets** for the vol scale. Bollerslev et al. 2018 report 20-day out-of-sample R² rising from 41% to 47% for HAR, and from 48% to 51% for its HExp extension, when one coefficient set serves every asset instead of one set per asset. Global models generally do well (Montero-Manso & Hyndman 2021).
3. **Combining forecasts, or shrinking toward the baseline.** Quantile averaging and the beta-transformed linear pool (Gneiting & Ranjan 2013). Equal weights usually beat fitted weights in small samples.
4. **Option-implied densities help at 2–4 weeks, not at 1 day** (Shackleton, Taylor & Yu 2010).
5. **Training window.** A rolling HAR with a ~630-day window and VIX was hard to beat (Audrino & Chassot 2024).
6. **Foundation models mostly lose to Log-HAR** (Brini 2026; only TTM wins, narrowly). Zero-shot return R² is negative (Rahimikia et al. 2025).
7. **Fancier heads** (flows, DeepAR, TFT, NGBoost): no credible finance CRPS gains found.

### Pilots built (JSON only, args overlays for `configs/workflow.json`)

Run from `children/index_options` with `PYTHONPATH` set to the repo root:

```
python -m dskit.pipeline workflow configs/workflow.json --args configs/pilot-base.json [--args configs/pilot-<x>.json]
```

The base runs QQQ only at 7 days, with 2 forward-selection rounds and 300 bootstrap replicates. Each run takes minutes.

| File | Tests | What changes |
|---|---|---|
| `pilot-base.json` | The baseline gap, regime segmentation, inputs over architecture | Adds references: `standardized_empirical` (FHS); two regime forests on 7 vol/implied-vol inputs, with leaves of ≥100 and ≥30 rows; and a 1-component, 8-unit, 3-seed MLP on the same inputs |
| `pilot-crps-student.json` | Loss and tail family | CRPS + wing twCRPS, Student components; step-6 grid over degrees of freedom 4/8/30 (3 candidates) |
| `pilot-regularize.json` | Variance control | Weight decay 0.01/1.0 × dropout 0/0.2 × seeds 1/5 (8 candidates) |
| `pilot-window-250.json`, `pilot-window-750.json` | Time segmentation | Training window of 250 or 750 dates, instead of 450 |
| `pilot-horizon-14.json`, `pilot-horizon-28.json` | Horizon segmentation | 14- or 28-day exact horizon, instead of 7 |

- **Where to look:**
  - `{work_dir}/report/report_QQQ/sections/comparisons.csv` gives every model's skill against every reference.
  - The step-6 `selection/candidates.jsonl` gives each grid point's warm-up skill.
- **The FHS trick:** `standardized_empirical` uses `is_QQQ` (always 1 in a one-ticker lane) as its divisor. This is valid for single-lane runs only.
- **Verified here:**
  - Every stack passes `--plan`.
  - The real hooks produced every reference and grid candidate on a synthetic QQQ-like panel, and the real `ChronologicalCDFStudy` fitted and scored them on CPU.
  - Nothing was run on real data. The store is not on this machine.

### How to read the pilots

- **A screen, not acceptance.** Shortlist an idea only if its 30- and 60-date block lower bounds beat both `horizon_empirical` and `standardized_empirical` on the scored folds. Then rerun it on the full manifest for QQQ and IWM (plus SPY).
- **Window and horizon pilots** score different dates. Compare them by skill against the references inside each run, not by raw CRPS across runs.
- **Viewed data:** every pilot uses 2019+ data that has already been seen. The never-seen Sep-2026+ recorder chains stay reserved.

### Needs code first (each needs an ADR; not built)

- **S1** — FHS as a first-class option on `HorizonEmpiricalCDF`. This removes the `is_<T>` trick and works when tickers are pooled.
- **S2** — Resolve feature names inside nested reference params. GPD tails, blends and pools over any reference would then become JSON pilots.
- **S3** — A pooled multi-ticker lane: SPY/QQQ/IWM with a shared shape and per-ticker scale (the Bollerslev "mega" pooling).
- **S4** — A `feature_indices` parameter on `ScaledEmpiricalCDF`. This gives a HAR-plus-implied-vol ridge scale on 5–7 inputs, the literature's best scale.
- **S5** — A discrete regime column (VIX/VIX3M inverted, VIX quintile) for `condition_indices`.
- **S6** — An evaluation that corrects for overlapping labels across many pilots: a Harvey–Leybourne–Newbold-corrected Diebold–Mariano test, or a model confidence set.

## Sources

- Baruník & Hanuš, Learning probability distributions in macroeconomics and finance: https://arxiv.org/abs/2204.06848
- Brini 2026, foundation models vs Log-HAR for realized vol: https://arxiv.org/abs/2607.05291
- Rahimikia et al. 2025, foundation models on returns: https://arxiv.org/abs/2511.18578
- Christensen, Siggaard & Veliyev, ML vs HAR for realized vol (JFEC 2023): https://arxiv.org/abs/2601.13014
- Audrino & Chassot 2024, HARd to beat: https://arxiv.org/abs/2406.08041
- Bollerslev, Hood, Huss & Pedersen 2018, Risk everywhere (RFS): https://public.econ.duke.edu/~boller/Published_Papers/rfs_18.pdf
- Busch, Christensen & Nielsen 2011 (JoE), implied vol in HAR: https://econpapers.repec.org/article/eeeeconom/v_3a160_3ay_3a2011_3ai_3a1_3ap_3a48-57.htm
- Kambouroudis, McMillan & Tsakou 2021 (JFM), HAR-IV and overnight returns: https://doi.org/10.1002/fut.22241
- McNeil & Frey 2000 (JEF), conditional EVT / FHS: https://doi.org/10.1016/S0927-5398(00)00012-8
- Kuester, Mittnik & Paolella 2006 (JFEC), VaR comparison: https://econpapers.repec.org/RePEc:oup:jfinec:v:4:y:2006:i:1:p:53-89
- Shackleton, Taylor & Yu 2010 (JBF), option-implied real-world densities: https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID1604526_code44638.pdf?abstractid=888671&mirid=1
- Montero-Manso & Hyndman 2021, global vs local models: https://arxiv.org/abs/2008.00444
- Gneiting & Ranjan 2013, combining predictive distributions: https://projecteuclid.org/journals/electronic-journal-of-statistics/volume-7/issue-none/Combining-predictive-distributions/10.1214/13-EJS823.full
- Harvey, Leybourne & Newbold 1997 (IJF), corrected Diebold–Mariano test: https://econpapers.repec.org/RePEc:eee:intfor:v:13:y:1997:i:2:p:281-291
- Hansen, Lunde & Nason 2011, model confidence set: https://doi.org/10.3982/ECTA5771
- Internal history: `docs/memos/2026-09-28-predictive-cdf-hpo.md`, `docs/memos/2026-09-30-dynamic-tail-calibration-and-gpd.md`, `docs/memos/2026-09-29-risk-neutral-cdf-architecture-comparison.md`, repo `docs/RE-ENTRY.md`.
- Not verified here: gain sizes in Kambouroudis et al. and in the regime-switching papers; the Baruník & Hanuš 1.8% figure is from the paper body, not the abstract.
