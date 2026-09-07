# Feature selection: mask families, never filter LightGBM, prune only the MLP

## Question

For the next pooled zoo iteration (LightGBM incumbent, Torch MLP challenger, P14 LSTM/GRU
fusion), what feature-selection policy should each family use over the frozen ~81-column P12
matrix, where in the child's train-only envelope it can live, whether `k` should be tuned
jointly with model knobs on the 63-day purged inner holdout, which column families are
redundant, and how a selection policy becomes a fair paired candidate at out-of-sample
R-squared of 0.1-0.6 percent.

## Finding

**Decision.** (1) Pooled LightGBM: no data-driven selector. Keep all 81 frozen columns plus
`symbol_code`; column subsampling (`colsample_bytree`, already searched over 0.3/0.5/0.7)
is the only selection it needs. The one selection-shaped experiment worth its compute is a
paired FIXED family-mask ablation, because it answers which families carry the h=1..10
edge and feeds the P14 side list. (2) Pooled MLP: selection is worth testing, first as the
same fixed masks, then as a train-only embedded rule (LightGBM gain on the inner-train,
top-k, `symbol_code` passed through to the embedding), never as `f_regression` and never
with `k` drawn by a 4-trial random search. (3) Sequence fusion: no selector; the declared
`sequence.feature_names` list is the policy, ablated by config edits only. Details follow.

1. **Why not for LightGBM.** FACT (Grinsztajn et al. 2022, Sec. 5.3): removing up to half
   the features in increasing random-forest importance "reduces the performance gap between
   MLPs (ResNet) and the other models", and adding uninformative Gaussian features "widens
   the gap"; tree-based models are the robust side of that comparison. FACT (McElfresh et
   al. 2023, 176 datasets): GBDTs are "much better than NNs at handling skewed or
   heavy-tailed feature distributions". FACT (LightGBM docs): `feature_fraction`
   (`colsample_bytree`) draws a random column subset per tree; `feature_fraction_bynode`
   (`colsample_bynode`) per node — a random-subspace regulariser the incumbent already
   runs at 0.5 with 0.3 in its space. FACT (Guyon and Elisseeff 2003, Sec. 3.3): "a
   variable that is completely useless by itself can provide a significant performance
   improvement when taken with others" — a univariate pre-filter in front of a tree can
   delete one half of an interaction the tree would have found (the P2 `gap_x_open30`
   shape). FACT (memo): the incumbent was positive in all 20 folds with the full matrix.
   INFERENCE: the expected effect of a fitted selector on LightGBM here is zero to slightly
   negative, and its cost is a fold-varying survivor set that serving must then pin.

2. **Why maybe for the MLP.** FACT (memo): MLP mean path score 0.005322 with fold SD
   0.005516 against LightGBM 0.006401 with SD 0.002358; the difference 0.001078 had HAC SE
   0.001234, p=0.38. INFERENCE: the MLP's 2.3x fold variance is the symptom Grinsztajn's
   finding predicts for a net fed 81 columns of which many are near-duplicates (point 7),
   so the realistic prize is variance reduction to LightGBM's level, bounded by roughly
   the 0.001 gap — not a win over the tree. That bounds the compute worth spending.

3. **Where selection can live in this child.** FACT (`benchmarks.py:358-386`):
   `BenchmarkPlan` refuses any candidate with a generic search node ("use an
   inner-training search instead"), so ADR-0044 flow 2 (a document space over
   `select.k`) cannot run inside a zoo; the only tuning envelope is the scan node's
   `hpo_space` -> `_hpo_combos` -> `_tune_estimator` (`nodes.py:3244-3320`), i.e.
   selection must be an ESTIMATOR-level step named by `estimator`/`estimator_params`.
   FACT (`benchmarks.py:387-398`, config `contract_paths`): `pipeline.features_*` and
   `pipeline.universe` are contract-pinned, so a candidate cannot vary `lookback`,
   `scales`, `momentum_horizons` or `keep_features`; a paired ablation must mask columns
   inside the estimator. FACT (`model_zoo.py:75-136`): `EmpiricalSelectRegressor` (a)
   scores with `f_regression`, (b) removes `symbol_code` from the design matrix
   entirely, not just from the ranking, and (c) never forwards `categorical_feature`
   or `feature_names` — wrapping the pooled LightGBM or the embedding MLP in it changes
   the representation, not only the columns. FACT (`nodes.py:3469-3487` and LightGBM
   docs): `_column_weights` reads `feature_importances_`, whose `LGBMRegressor` default
   `importance_type` is `'split'` (counts of uses), not gain; the child's only existing
   importance rule has therefore ranked by split count. FACT (hl-scan note): the 28-name
   `universe.keep_features` list was chosen at H=470 with validation on Dec 2025-Feb
   2026 — the finalist HPO window. INFERENCE: reusing that list in any development
   candidate leaks the finalist window; treat it as void for P13-era work. GAP (dskit,
   tier 2, ADR): an estimator adapter that fits any named estimator on a DECLARED column
   subset (or on the top-k of a train-fit importance rule), passes named columns through
   untouched, and forwards `categorical_feature`/`feature_names` — the mechanism is
   domain-neutral; the child supplies only name lists. `sklearn-select` and
   `torch-importance` are node-level members and stay the right home for a plain
   walk-forward document and for pinning a final survivor list in a sidecar.

4. **Selector families for a noisy, serially dependent, pooled panel.**

   | family | score | cost per fold | pitfall here | verdict |
   |---|---|---|---|---|
   | `f_regression` | Pearson-F, linear | trivial | FACT (sklearn docs): a linear univariate test; misses `mom_*`-type ratios and interactions; fat tails | no |
   | Spearman abs IC | rank, robust | trivial | univariate, winner's curse at top-k | filter of choice if any |
   | mutual info | kNN entropy (Kraskov) | O(n log n) x 81 | FACT (sklearn docs): adds noise, biased at weak dependence; INFERENCE: unreliable at 0.1% R2 | no |
   | L1 / elastic net | embedded, linear | cheap | needs scaling; sparse-in-features hurt at this SNR (point 8) | no |
   | LightGBM gain | embedded, nonlinear | one fit | FACT (Strobl 2007): impurity importance favours continuous/high-cardinality columns; binary flags under-ranked; set `importance_type: gain` | rule of choice for the MLP |
   | RFE / forward | wrapper | 81 fits or more | FACT (Cawley-Talbot 2010): overfits the selection criterion when its variance is non-negligible | no |
   | permutation / MDA | model-based, OOS | 81 refits or shuffles | FACT (sklearn docs): correlated features mask each other; needs purged holdout | diagnostic only |
   | clustered MDA/MDI | group-level | clustering + MDA | FACT (mlfinlab docs): built to defeat substitution effects; the frozen families ARE natural clusters | use fixed family masks instead |
   | stability selection | subsample frequency | 50-100 refits | FACT (Meinshausen-Buhlmann 2010): threshold 0.6-0.9, n/2 subsamples, E[V] bound | one-off diagnostic |
   | Boruta | shadow features | many RF fits | FACT (Kursa-Rudnicki 2010): all-relevant, keeps correlated pairs | too costly in-loop |
   | null importance | target shuffles | 30-100 fits | FACT (Altmann 2010): 50-100 permutations, p-value per feature | the honest threshold test |

   Two rules cut across all of them. FACT (Ambroise-McLachlan 2002; Varma-Simon 2006;
   ESL 7.10.2): selecting on data the evaluation later reads biases the estimate — in
   Varma-Simon's null data a same-loop CV reported 37.8% error where the truth was 50%.
   The child's scan envelope is safe by construction only when the rule runs INSIDE
   `_tune_estimator` or the estimator's `fit` on fold-train rows. INFERENCE (arithmetic):
   the inner holdout is cut on the 5-minute training grid (`nodes.py:5504-5514` passes no
   `score_period_ms`), about 25 x 44 x 78 = 86k rows per lead before NaN drops; with
   cross-sectional and overlap dependence n_eff is perhaps 30-60k, so SE(IC) is about
   0.004-0.006 and the expected maximum of 81 null |IC|s is roughly 0.015 — any
   threshold-based filter must clear that, which is why `k` (or a shuffled-target null)
   beats a threshold. FACT (Harvey-Liu-Zhu 2016): the multiple-testing hurdle for a new
   predictor is a t-statistic near 3. Instability: FACT (Nogueira et al. 2018) gives a
   stability statistic with confidence intervals; GAP (dskit, tier 1, ADR): ADR-0043
   prints distinct search winners per fold but nothing records per-fold survivor sets or
   their Jaccard overlap; GAP (child): log the survivor names in the scan `metrics`.

5. **Should `k` be tuned jointly, and over what range?** Not with the present budget.
   FACT (`_hpo_combos`): `hpo_trials: 4` draws four combos from the product space
   (LightGBM: 3^6 = 729), so adding `k_features` makes `k` a random draw, not a tuned
   knob. FACT (jpm-h-20 note): on a ~1,800-row asset-local inner holdout, 32 draws
   produced inner IC uncorrelated with validation IC. INFERENCE: the pooled inner holdout
   is 40-50x larger (point 4) and can separate coarse choices such as 20 vs 40 vs all,
   not fine steps; FACT (Cawley-Talbot 2010, Sec. 4.4): over-fitting the selection
   criterion "is likely to be most severe when the sample of data is small and the number
   of hyper-parameters to be tuned is relatively large", and joint tuning adds degrees of
   freedom. Ordering: selecting and tuning on the same inner holdout double-dips it,
   which makes the INNER score optimistic but leaves the 20 outer folds honest — the cost
   is a noisier choice, not a biased report. Policy: pin `k` per candidate and let the
   outer folds decide (E2/E3); if `k` is ever tuned, use the asset-local P13 grid
   `[10, 20, 40, 70]` reduced to `[20, 40, all]` and raise `hpo_trials` to 12-16 for that
   candidate only, inside the finalist HPO calendar, never in the development screen.

6. **Score choice.** `f_regression` is the wrong default: FACT (sklearn docs) it is the
   Pearson correlation turned into an F statistic. The label is vol-scaled but still
   heavy-tailed, and the objective is `hpo_objective: ic` (Spearman), so a univariate
   filter should rank by Spearman |IC| — the child's own fallback in `_column_weights`
   already does this when no importance exists. For the MLP the better rule is embedded:
   LightGBM gain fitted on the inner-train with the P13 winner's parameters, because it
   sees interactions and is the incumbent's own view of the columns; correct its bias
   against the ten binary/calendar flags by always-keeping them (`_always_keep` exists,
   `nodes.py:3407`).

7. **Redundant families and the cheapest honest test.** Column census (FACT,
   `session_feature_names` + `_emit_feature_names`, `lookback: 20`, five scales, five
   momentum horizons, no industries in `universe-p13-pooled.json`): 20 lags + 25 scale
   stats + 14 clock/calendar + 2 SPY + 5 `mom_` + 15 extra-horizon = 81, plus
   `symbol_code` for LightGBM. FACT (`nodes.py:1403-1434`): `mom_<tag> = ret_<tag> /
   rv_<tag>` exactly. INFERENCE: `ret_3m/5m/15m` are (up to gap handling) sums of
   `ret_lag_0..2/0..4/0..14`; `residual_SPY` is linear in `ret_lag_0` and `ref_ret_SPY`;
   `tod_sin/cos` are deterministic in `minutes_from_open`; `rv/range/vol/amihud` at 5m
   and 15m are noisy short-window twins of their 60m versions. Trees are indifferent to
   these duplicates; a net is not. Prior evidence is thin: FACT: P2, P3a and P3b are
   research-only ("nothing run"); the only run selection is the hl-scan (H=470, val on
   the finalist window): all 120 lags and all 5m/15m stats dropped, 1s/3s stats all kept
   — INFERENCE: not transferable to h=1..10, where the short lags are the plausible
   carriers. Cheapest honest test: fixed family masks (not fitted, so no inner holdout,
   no leakage, no survivor drift), paired on the same 20 folds — a fitted selector adds a
   `k` choice and fold-varying survivors for no extra information about families.

8. **Evidence protocol and the expected gain.** A selection policy becomes a candidate by
   being a template in the same `PooledGate3ZooCandidates` inventory: same P12 cache,
   same 20 folds, same `hpo_val_days`/`hpo_embargo_days`, `feature_policy` stating the
   mask or rule, a lower `compute_rank` when it drops columns, paired NW comparison and
   all-pairs Bonferroni. Keep inventories to 3-4 candidates: 4 gives six pairs at 0.0083,
   6 gives fifteen at 0.0033. INFERENCE: two LightGBM variants share most fold noise, so
   the paired SE will sit well under the 0.0012 seen for LightGBM vs MLP and deficits near
   0.001 become detectable; the tie-break then selects the masked candidate when no
   deficit is detectable, which is exactly the outcome a redundancy claim predicts. Effect
   sizes at this SNR: FACT (GKX 2020, monthly stocks): ENet 0.11% versus PCR 0.26%, GBRT
   0.34%, NN3 0.40%, with "the improvement of dimension reduction over variable selection
   via elastic net suggests that characteristics are partially redundant and fundamentally
   noisy"; FACT (Kozak-Nagel-Santosh 2020): sparse characteristic models underperform
   dense L2-shrunk ones; FACT (Aleti-Bollerslev-Siggaard 2025, 15-minute market return,
   218 factor portfolios): Lasso 0.151% and AdaLasso 0.137% versus Ridge 0.172%, PCR
   0.172%, Ensemble 0.212%, with GBRT swinging from -0.161% to 0.128% across
   specifications; FACT (Chinco et al. 2019, 1-minute, 6,000 candidates): LASSO adds 1.2pp
   over an AR(3) but selects 12.7 stocks on average and "less than 5% of the predictors
   selected by the LASSO are used for more than 15 minutes in a row" — selection pays only
   where the signal is genuinely sparse and short-lived, which our fixed families are not.
   INFERENCE: expect the LightGBM mask candidates within +-0.001 path score of the
   incumbent, the MLP mask/prune candidates to move mean by at most ~0.001 and fold SD
   toward 0.0024.

9. **Concrete configuration.** Available today, no code: keep the LightGBM template
   unchanged and add `"colsample_bynode": [0.5, 1.0]` to its `hpo_space` only if a
   guarded check (three-declared-knobs note) shows predictions move; put
   `"importance_type": "gain"` in any `estimator_params` whose importances are read.
   After the tier-2 adapter (point 3) lands, the mask candidate reads, in the template
   `model` block: `"estimator": "<dskit adapter path>"`, `"estimator_params": {"estimator":
   "lightgbm.LGBMRegressor", "drop": [<family names>], "passthrough": ["symbol_code"],
   <P13 winner params>}`, `hpo_space` as P13; the MLP prune candidate:
   `"estimator_params": {"estimator": "dskit.pipeline.libs.torch_ts.CategoricalEmbeddingMLPRegressor",
   "rank_estimator": "lightgbm.LGBMRegressor", "rank_params": {<P13 LightGBM winner>,
   "importance_type": "gain"}, "k": 40, "always_keep": [<calendar flags>], "passthrough":
   ["symbol_code"], <MLP params>}`. Null importance and survivor-stability reporting are
   GAPs (tier 2 `FeatureSelector` member by import path; tier 1 summary statistic).

10. **Ranked experiments.**
    - E1 (LightGBM family masks, paired): candidates = incumbent; drop `ret_lag_5..19`;
      drop the 2s/1w/3s families (`ret_/rv_/mom_2s`, `ret_/rv_/mom_1w`, the five `_3s`
      stats and `mom_3s`); drop the calendar tail (`dow_*`, `month_*`, `after_holiday`,
      `session_gap_days`). Rule: a mask with no detectable deficit is selected by the
      existing tie-break; a detectable deficit certifies the family. Needs the adapter.
    - E2 (MLP with E1's surviving mask, paired against the P13 MLP): rule = lowest fold SD
      among candidates with no detectable deficit to LightGBM; success = SD falls toward
      0.0024. Run after E1, reusing its mask, so the inventory stays at 3.
    - E3 (MLP embedded prune, `k` in {20, 40} as two candidates, LightGBM-gain rank on
      inner-train, flags always kept, `symbol_code` passed through): same rule as E2.
    - E4 (null-importance diagnostic, not a candidate): per fold at leads 1 and 10, 30
      target shuffles of the P13 LightGBM, gain importance; report per family the count of
      folds in which actual gain exceeds the 95th null percentile and the pairwise Jaccard
      of survivor sets. Rule: a family below the null in 15 or more folds becomes the next
      mask; all families above it closes selection as a topic. About 1,200 fits.
    - E5 (only if E2/E3 gain): joint `k` x MLP knobs with `hpo_trials` 12-16 inside the
      finalist HPO calendar.
    - P14: no change now; next iteration ablates `sequence.feature_names` (drop
      `dow_*`/`month_*`; add `rv_60m`, `amihud_1s`) as two paired candidates.

11. **What not to do.** Do not wrap either pooled family in `EmpiricalSelectRegressor`
    as it stands (loses `symbol_code`, `f_regression`, no kwargs forwarding). Do not put
    `k_features` into a 4-trial `hpo_space`. Do not rank by the default split-count
    `feature_importances_`. Do not reuse `universe.keep_features` or any list chosen on
    the Dec 2025-Feb 2026 window or the lockbox. Do not pre-screen columns on pooled data
    outside the folds and then benchmark the keep list. Do not run Boruta, RFE or
    stability selection inside the inner loop. Do not exceed four candidates per
    inventory. Do not read "no detectable difference" as equivalence.

## Sources

- Grinsztajn, Oyallon, Varoquaux (2022), "Why do tree-based models still outperform deep learning on typical tabular data?", NeurIPS Datasets and Benchmarks. https://arxiv.org/abs/2207.08815 (full text via https://ar5iv.labs.arxiv.org/html/2207.08815)
- McElfresh et al. (2023), "When Do Neural Nets Outperform Boosted Trees on Tabular Data?", NeurIPS. https://arxiv.org/abs/2305.02997 (abstract)
- Gu, Kelly, Xiu (2020), "Empirical Asset Pricing via Machine Learning", Review of Financial Studies 33(5). https://doi.org/10.1093/rfs/hhaa009 (text via https://dachxiu.chicagobooth.edu/download/ML.pdf)
- Aleti, Bollerslev, Siggaard (2025), "Intraday Market Return Predictability Culled from the Factor Zoo", Management Science 71(9). https://doi.org/10.1287/mnsc.2023.01657 (text via https://public.econ.duke.edu/~boller/Papers/HFML.pdf)
- Chinco, Clark-Joseph, Ye (2019), "Sparse Signals in the Cross-Section of Returns", Journal of Finance 74(1). https://doi.org/10.1111/jofi.12733 (text via https://www.alexchinco.com/sparse-signals-in-cross-section.pdf)
- Kozak, Nagel, Santosh (2020), "Shrinking the Cross-Section", Journal of Financial Economics. NBER w24070, https://www.nber.org/papers/w24070 (abstract)
- Huddleston, Liu, Stentoft (2023), "Intraday Market Predictability: A Machine Learning Approach", Journal of Financial Econometrics 21(2). https://academic.oup.com/jfec/article-abstract/21/2/485/6400345 (not fetched; abstract via https://ouci.dntb.gov.ua/en/works/42NOWDe4/)
- Harvey, Liu, Zhu (2016), "... and the Cross-Section of Expected Returns", Review of Financial Studies. NBER w20592, https://www.nber.org/papers/w20592 (abstract)
- Cawley, Talbot (2010), "On Over-fitting in Model Selection and Subsequent Selection Bias in Performance Evaluation", JMLR 11. https://jmlr.org/papers/v11/cawley10a.html
- Varma, Simon (2006), "Bias in error estimation when using cross-validation for model selection", BMC Bioinformatics 7:91. https://pmc.ncbi.nlm.nih.gov/articles/PMC1397873/
- Ambroise, McLachlan (2002), "Selection bias in gene extraction on the basis of microarray gene-expression data", PNAS 99(10). https://doi.org/10.1073/pnas.102102699 (abstract via Europe PMC)
- Hastie, Tibshirani, Friedman (2009), The Elements of Statistical Learning, 2nd ed., Sec. 7.10.2 "The Wrong and Right Way to Do Cross-validation", Springer. https://hastie.su.domains/ElemStatLearn/ (not fetched)
- Meinshausen, Buhlmann (2010), "Stability Selection", JRSS-B 72(4). https://arxiv.org/abs/0809.2932
- Altmann, Tolosi, Sander, Lengauer (2010), "Permutation importance: a corrected feature importance measure", Bioinformatics 26(10). https://doi.org/10.1093/bioinformatics/btq134
- Kursa, Rudnicki (2010), "Feature Selection with the Boruta Package", Journal of Statistical Software 36(11). https://doi.org/10.18637/jss.v036.i11
- Strobl, Boulesteix, Zeileis, Hothorn (2007), "Bias in random forest variable importance measures", BMC Bioinformatics 8:25. https://pmc.ncbi.nlm.nih.gov/articles/PMC1796903/
- Nogueira, Sechidis, Brown (2018), "On the Stability of Feature Selection Algorithms", JMLR 18. https://jmlr.org/papers/v18/17-514.html (abstract)
- Guyon, Elisseeff (2003), "An Introduction to Variable and Feature Selection", JMLR 3. https://jmlr.org/papers/v3/guyon03a.html
- Lopez de Prado (2018), Advances in Financial Machine Learning, ch. 8 "Feature Importance", Wiley (not fetched); Lopez de Prado (2020), "Clustered Feature Importance", SSRN 3517595, https://ssrn.com/abstract=3517595 (not fetched); method description via mlfinlab docs, https://random-docs.readthedocs.io/en/latest/implementations/feature_importance.html
- Grellier, "Feature Selection with Null Importances", Kaggle notebook. https://www.kaggle.com/code/ogrellier/feature-selection-with-null-importances
- LightGBM documentation, Parameters (`feature_fraction`, `feature_fraction_bynode`, `bagging_freq`, importance types). https://lightgbm.readthedocs.io/en/latest/Parameters.html ; LGBMRegressor `importance_type` default `'split'`. https://lightgbm.readthedocs.io/en/latest/pythonapi/lightgbm.LGBMRegressor.html
- scikit-learn documentation: `f_regression` https://scikit-learn.org/stable/modules/generated/sklearn.feature_selection.f_regression.html ; `mutual_info_regression` https://scikit-learn.org/stable/modules/generated/sklearn.feature_selection.mutual_info_regression.html ; permutation importance caveats https://scikit-learn.org/stable/modules/permutation_importance.html

Repo files relied on:

- /home/user/dskit/children/intraday_equities/docs/memos/p13-pooled-kronos-model-zoo-results.md
- /home/user/dskit/children/intraday_equities/docs/memos/2026-09-05-predictive-model-strategy-audit.md
- /home/user/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json
- /home/user/dskit/children/intraday_equities/configs/run-p13-model-zoo.json (asset-local `k_features` grid)
- /home/user/dskit/children/intraday_equities/configs/run-p14-recurrent-fusion-zoo.json
- /home/user/dskit/children/intraday_equities/configs/universe-p13-pooled.json
- /home/user/dskit/children/intraday_equities/intraday_equities/model_zoo.py (lines 75-206, 419-466)
- /home/user/dskit/children/intraday_equities/intraday_equities/nodes.py (`session_feature_names` 1200, `_emit_feature_names` 1257, scale/momentum columns 1403-1434, `_tune_estimator` family 3236-3320, `_always_keep` 3407, `_keep_by_importance`/`_column_weights` 3440-3487, `_scan_fold_stamped` 4429, scan `_PARAMS` 5085, HPO call 5502-5543, `LookbackScan` keep rule 5829-5846, `keep_features` 6134)
- /home/user/dskit/children/intraday_equities/intraday_equities/features.py (ADR-0071 blocks)
- /home/user/dskit/children/intraday_equities/docs/research/jpm-h-20-6-of-7-folds-negative-hpo-tunes-noise.md
- /home/user/dskit/children/intraday_equities/docs/research/p7-model-size-for-this-noise-level-a-ranked-shortlist-and-a-hold-on-the-big-models.md
- /home/user/dskit/children/intraday_equities/docs/research/p2-time-of-day-feature-block-and-a-per-bucket-horizon-test.md
- /home/user/dskit/children/intraday_equities/docs/research/p3a-bar-derived-inputs-that-may-extend-the-horizon.md
- /home/user/dskit/children/intraday_equities/docs/research/p3b-cross-stock-market-and-sector-inputs-for-1-60-min-returns.md
- /home/user/dskit/children/intraday_equities/docs/research/hl-scan-h-vs-l-166-features-weak-regularization.md
- /home/user/dskit/children/intraday_equities/docs/research/three-declared-knobs-that-did-nothing-or-the-opposite.md
- /home/user/dskit/children/intraday_equities/docs/research/post-gate3-predictor-output/2026-09-05-input-representation.md
- /home/user/dskit/children/intraday_equities/docs/research/post-gate3-predictor-output/2026-09-05-synthesis.md
- /home/user/dskit/dskit/pipeline/fitted.py (`FeatureSelector` 1333)
- /home/user/dskit/dskit/pipeline/libs/sklearn.py (`SklearnSelect` 993)
- /home/user/dskit/dskit/pipeline/libs/torch.py (`TorchImportance` 2556)
- /home/user/dskit/dskit/pipeline/libs/torch_ts.py (`CategoricalEmbeddingMLPRegressor` 853, `CategoricalRecurrentFusionRegressor` 1132)
- /home/user/dskit/dskit/pipeline/benchmarks.py (search-node refusal 358-386, contract paths 387-398)
- /home/user/dskit/docs/architecture/decision-log.md (ADR-0042, ADR-0043, ADR-0044, lines 1759-1935)
- /home/user/dskit/TODO.md (lines 931-1043, owner selection ruling)
- /home/user/dskit/CLAUDE.md, /home/user/dskit/children/intraday_equities/CLAUDE.md, /home/user/dskit/dskit/pipeline/CLAUDE.md
