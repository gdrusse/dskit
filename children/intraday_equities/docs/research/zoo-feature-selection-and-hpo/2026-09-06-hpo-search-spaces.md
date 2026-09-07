# HPO: add learning_rate, log-scale the rest, draw 24, pin one winner

## Question

Four random draws from a 729- (LightGBM) or 1458-point (MLP) categorical product
screened P13 — "an exploratory screen, not an exhaustive optimized ceiling".
What space, budget and procedure are defensible for the pooled LightGBM
incumbent, the pooled MLP challenger and the P14 recurrent candidates, and which
part of the answer is a config edit versus a generic gap in dskit?

## Finding

**Decision.** Keep seeded random search in the scan node; raise `hpo_trials`
4 → 24; rebuild the LightGBM space around `learning_rate`, `min_child_samples`,
`reg_lambda`, `reg_alpha` on log ladders, dropping `max_depth` and
`colsample_bytree`; rebuild the MLP space around shrinkage, not capacity; make the
inner objective the outer path score; emit the trial ledger and per-fold winner;
pin ONE winner before the finalist window.

### 1. What the pooled inner holdout resolves — the JPM verdict does not transfer

**FACT (code).** `nodes.py:5504` builds the inner holdout with **no
`score_period_ms`**, unlike the outer val call at `:5466`: it is the full 5-minute
row set (`universe-p13-pooled.json` `period_ms` 300000), not the 30-minute lattice.

**INFERENCE (rows).** JPM measured ~1,800 val / ~20,900 train rows per name per
730-day fold at 5-minute spacing after ~54% row loss. Assuming the same retention
over 25 names: inner holdout ≈ **45,000 rows**, inner train ≈ 470,000, fold train
≈ 520,000 — lower bounds if the cohort's ETFs retain more.

**INFERENCE (MDE).** `SE(IC) ≈ 1/sqrt(n_eff)` near zero IC (Fisher-z), with
`n_eff = n / (DEFF_x · DEFF_overlap)`, `DEFF_x = 1 + (m-1)·rho` for m = 25 names
per stamp and rho the residual cross-correlation surviving the vol-scaled
SPY-residual label; overlap ≈ 1 at the 1-minute training lead, ≈ 1.5 at 10
minutes. rho = 0.05 / 0.15 / 0.30 → n_eff ≈ 20,500 / 9,800 / 5,500 → SE(IC) ≈
0.0070 / 0.0101 / 0.0135. Trials are **paired**, so ranking uses
`SE(dIC) ≈ sqrt(2(1-rho_ab)/n_eff)`; regularization-only differences give
rho_ab ≈ 0.9-0.95, hence **SE(dIC) ≈ 0.0032-0.0045** and a 2-SE resolvable gap of
**0.006-0.009 IC**. JPM's single-name holdout resolved ~0.059, so the pooled one
is 5-8× sharper: "32 draws is a random number generator" is a fact about 1,800
single-name rows, not 45,000 pooled ones. Gaps under ~0.006 IC stay unrankable,
which is why the budget below is finite.

### 2. Four draws is coverage failure, not variance control

**FACT (Bergstra & Bengio 2012).** Random search wins because these surfaces have
low effective dimensionality; K draws hit the top-q fraction with probability
`1-(1-q)^K`, so q = 5% needs ~60 draws for 95%. **INFERENCE.** `1-0.95^K`: K=4 →
**18.5%**, 8 → 34%, 16 → 56%, 24 → 71%, 32 → 81%, 64 → 96%. With 6 knobs × 3
levels and 4 uniform draws a level is missed with probability `(2/3)^4 = 0.198`,
so **~3.6 of P13's 18 (knob, level) cells were never evaluated**. Inner selection
bias grows only as `SE(dIC)·sqrt(2 ln K)`: 0.0075 (K=4) → 0.0106 (16) → 0.0118
(32). So 4 → 24 multiplies coverage 3.8× and optimism 1.4×, and the optimism never
reaches the reported number (§7.6). **Set `hpo_trials: 24`** — 71% coverage, every
marginal level visited with probability ≈ 0.99997, ~6× P13's per-candidate fit
cost (overnight for the two tabular families against P13's 7h25m).

### 3. LightGBM: half the current dimensions sit on the least tunable knobs

**FACT (Probst, Boulesteix & Bischl 2019, xgboost, AUC).** Tunability: `booster`
0.008, `eta` 0.005, then `min_child_weight`/`subsample`/`lambda`/`alpha`/`nrounds`
0.002, then `colsample_bytree`/`colsample_bylevel`/`max_depth` 0.001; their ranges
apply a `2^x` (log) transform to `eta`, `min_child_weight`, `lambda`, `alpha` and
none to `subsample`, `colsample_bytree`, `max_depth`, `nrounds`. Mapped to the
child: the most tunable non-structural knob, `learning_rate`, is **absent from
`hpo_space` entirely** (pinned 0.01); the next tier is present but on arithmetic
lists; the least tunable pair takes 2 of the 6 dimensions.

- **ADD `learning_rate`**, log; it couples with `n_estimators` through fitted
  amplitude, so hold rounds at 600 and let a small rate shrink. **Do not**
  early-stop rounds on the inner holdout — the winner is refit on 11× the rows.
- **DROP `max_depth`**, pin 4. **FACT (LightGBM docs):** "let it be smaller than
  `2^(max_depth)`" — at `num_leaves` ≤ 16 with `max_depth` ≥ 4 the cap never
  binds, so 6 of the current 9 `(num_leaves, max_depth)` cells are duplicates.
- **DROP `colsample_bytree`**, pin 0.5 — least-tunable tier, and 61 columns is not
  wide; for per-split decorrelation use `colsample_bynode`, pinned.
- **RE-SCALE.** `min_child_samples` → `[250,1000,4000]` (P7 argued 1000-4000, which
  the current top rung barely enters); `reg_alpha [0,0.1,1]` → `[0,0.3,3]`;
  `reg_lambda` over four decades; `num_leaves` extended down to `[2,4,8,16]`,
  P7's restrained region starting below the current floor.
- **Pinned, never searched:** `subsample` 0.7 with `subsample_freq` 1 (in-repo:
  inert without the frequency); `min_split_gain` absent (in-repo: 0.02 against a
  ~2.6e-06 label variance made stumps in every draw; if ever declared it needs a
  variance-scaled guard); `min_data_per_group` 1000 against a split on a thin name
  (25 levels × ~20,000 rows — **INFERENCE:** `cat_smooth`/`cat_l2` target high
  cardinality and cannot bind here, so they are not worth dimensions).
- **Separate arms, not dimensions:** `extra_trees: true`, `path_smooth > 0` (both
  on LightGBM's own over-fitting list), `max_bin: 63`, and `linear_tree` — a
  different model class that belongs in the zoo.

**Is a 3-point list an adequate proxy for a log-uniform range at K=24?** For
`num_leaves`/`max_depth` yes, genuinely discrete; for `learning_rate`,
`reg_lambda`, `reg_alpha`, `min_child_samples` **no** — it forces you to guess the
decade before searching it. A 4-point log ladder is the honest compromise in the
present grammar; a true log-uniform draw is gap G1.

### 4. The MLP searches capacity when the binding constraint is shrinkage

**INFERENCE.** P7's rule (~4/R² rows per free number, R² ≈ 0.001 → ~4,000 rows
each) gave ~30 numbers at 117,000 rows; pooled at ~520,000 the budget is **~130**.
The P13 base MLP (61 continuous + 8-dim embedding → 69 inputs, hidden 64, depth 2)
carries ≈ **8,900 weights**, ~68× over; the top corner (128 × 3) ≈ 30,000, ~230×.
**Every point in the searched space is far over budget**, so
`hidden_size`/`hidden_depth` choose between over-budget and further-over-budget
while `weight_decay` and `dropout` set effective capacity — why P7 said ridge's 88
parameters "fit" because they are "shrunk hard". P13 agrees: MLP fold sd 0.005516
vs LightGBM's 0.002358, 2.3× the variance at an indistinguishable mean —
under-regularized, not mis-sized. So `hidden_size [32,64]`, `hidden_depth [1,2]`,
**pin `embedding_dim: 8`** (25 symbols, 200 parameters) and `batch_size: 4096` (it
trades against `lr`), and spend the freed dims on `weight_decay`/`dropout`.

**`epochs` vs a monitored stop. FACT (code):** `libs/torch.py` has a real
checkpoint seam — `monitor`, `patience`, best-state restore (ADR-0035/0054, TODO
item 13 landed) — but on the **pipeline node**. `CategoricalEmbeddingMLPRegressor`
(`torch_ts.py:853`) has no `monitor`, no `patience`, no validation split: fixed
`epochs`, last-epoch weights. So the only stop rule is `epochs` in `hpo_space` —
legitimate (P7: "early stopping chosen on purged data") but it costs a dimension
and picks one number for the whole refit (gap G3). Keep `epochs [5,10,20]` for now.

**Seed ensembles (gap G2).** `hpo-grid` has `seeds` — each trial scored as a mean
over an ensemble. The child has none and pins `seed: 0`, so **every MLP and
recurrent number in P13/P14 is a single-seed draw**. P7 cites 5 seeds as buying
most of the variance reduction; here the seed spread is plausibly the size of the
family gap under test.

### 5. P14: the space widened while the budget stayed at 4

**FACT (config).** `run-p14-recurrent-fusion-zoo.json` searches **10 knobs** —
26,244 points — at `hpo_trials: 4`. Coverage is still 18.5% and ~20% of every
knob's levels go unvisited, so the run cannot report which region it explored.
**Cut to 4 dimensions** — `context_length [30,60,120]` (the one architectural
question the run exists to answer), `weight_decay`, `dropout`, `lr` — pinning
`hidden_size: 16`, `num_layers: 1`, `static_projection_dim: 16`,
`embedding_dim: 8`, `batch_size: 2048`, `epochs: 6`, at `hpo_trials: 16`. That is
P7's item-5 discipline: same architecture, an order of magnitude smaller.

### 6. Procedure: random search, not TPE, at these budgets

**FACT (Optuna docs).** `TPESampler.n_startup_trials` defaults to **10**: until 10
trials finish TPE *is* random sampling. **INFERENCE:** at K = 4 or 8 seeded TPE
and seeded random search are the same algorithm; at 16 only 6 trials are
model-guided; at 32, 22 are. TPE earns its place from about K ≥ 32 and only on a
smooth surface — doubtful when SE(dIC) ≈ 0.004 against between-trial gaps of the
same size. Revisit only if a 24-draw ledger shows a reproducible per-knob gradient
across folds.

**Per-fold vs one global tune.** ADR-0043: per-fold re-tuning **measures the
tuning procedure**, is not nested CV, and shipping means pinning the winner and
dropping the search (moving the hash by design). Its distinct-winner diagnostic
covers search *nodes*; `benchmarks.py:381` refuses those under walk-forward, so
the child's estimator-level search sits outside it and emits only
`metrics["hpo_ic"]` — the winning *score*, not the winning *params*, not the
ledger (gap G4). That count is the decision rule for experiment 2.

**Near ties: the 1-SE rule** (Breiman et al. 1984; ESL §7.10) — among trials
within 0.004 of the best take the smallest `num_leaves`, then the largest
`min_child_samples`, then the largest `reg_lambda`. **FACT (Schneider, Bischl &
Feurer 2025):** "overtuning" is "more common than previously assumed", and in ~10%
of cases the selected configuration generalizes *worse than the default or the
first configuration tried*, worst in the small-data regime.

**Refine across runs, not within a fold** — a local second stage on the same
45,000 rows compounds selection bias with nothing new to check it; centre the next
run's space on this run's ledger. **Do not build Hyperband/ASHA. FACT
(`libs/optuna.py` docstring):** pruning is deliberately absent because the
`ctx.rerun` seam returns exactly one float per trial, so pruner knobs would be
dead configuration; `_tune_estimator` has the same shape, and at R² ≈ 0.1% a
low-fidelity ranking is the noisy ranking JPM condemned.

### 7. Honesty guards

1. **The purge is right and stays** — `_hpo_cuts` carves the inner val from the
   end of fold train with a 5-day embargo; the fold's own val is never read.
2. **The inner objective does not match the outer score. FACT (code):** inner =
   Spearman IC at the **single training lead** on **un-latticed 5-minute rows**;
   outer = `$path.metrics.path_score`, horizon-weighted over ten leads on the
   **30-minute lattice**. Add `hpo_objective: "path"`; keep `"ic"` as fallback;
   never `"mspe"`, whose own docstring says it selects toward underfitting. In
   passing, `"mspe"` is defaulted twice (`nodes.py:3276`, `:5516`) — the "a default
   belongs to ONE name" shape.
3. **Emit the winner params and the full trial ledger** — only the winning score
   is recorded today, so §6's rule cannot be evaluated at all. Cheapest fix here.
4. **Pin before the finalist window (ADR-0043)** — nothing reaches
   train ≤ 2025-11-30 / embargo 2025-12-01 / validate 2025-12-02..2026-02-28
   carrying twenty per-fold winners; pin one setting, drop the search, take the
   hash move. **Never tune on the lockbox:** nothing at or after 2026-03-01.
5. **Inner multiplicity needs no correction. FACT (Cawley & Talbot 2010; Varma &
   Simon 2006):** the bias needing correction is in an estimate computed on the
   *same* data used for selection. The outer 20 folds are never used for
   selection, so raising `hpo_trials` 4 → 24 **does not inflate the reported path
   scores**; it inflates the inner score, which is why that is never evidence.
6. **Outer multiplicity does** — White's Reality Check (2000) and the Deflated
   Sharpe Ratio (Bailey & López de Prado 2014) apply to the number of *strategies
   compared on the reported sample*, and `BenchmarkCompare`'s all-pairs Bonferroni
   is that correction: adding **families** grows its denominator, **trials** do not.

### 8. Proposed spaces (the child's existing list grammar)

LightGBM base: `n_estimators` 600 unchanged; `max_depth` 4, `colsample_bytree`
0.5, `min_data_per_group` 1000 pinned; `subsample` 0.7 + `subsample_freq` 1 kept.

```json
{
  "hpo_trials": 24, "hpo_seed": 0, "hpo_val_days": 63,
  "hpo_embargo_days": 5, "hpo_objective": "path",
  "hpo_space": {
    "learning_rate": [0.003, 0.01, 0.03], "num_leaves": [2, 4, 8, 16],
    "min_child_samples": [250, 1000, 4000],
    "reg_lambda": [1.0, 10.0, 100.0, 1000.0], "reg_alpha": [0.0, 0.3, 3.0]
  }
}
```

MLP — pin `embedding_dim: 8`, `batch_size: 4096`, `seed: 0`, `standardize: true`.

```json
{
  "hpo_trials": 24, "hpo_seed": 0, "hpo_val_days": 63,
  "hpo_embargo_days": 5, "hpo_objective": "path",
  "hpo_space": {
    "lr": [0.0003, 0.001, 0.003], "weight_decay": [0.0001, 0.001, 0.01, 0.1],
    "dropout": [0.0, 0.1, 0.2, 0.3], "hidden_size": [32, 64],
    "hidden_depth": [1, 2], "epochs": [5, 10, 20]
  }
}
```

Both are 576 combinations, 24 drawn (4.2%): LightGBM five dimensions with four log
ladders, MLP six with three on shrinkage. **`hpo_val_days`: keep 63.** Doubling to
126 cuts SE(dIC) by sqrt(2) but shortens the inner train 662 → 599 days, choosing
a winner for a model with 10% less history than the one refit; two rotating 63-day
holdouts averaged buy the same sqrt(2) with no mismatch (gap G5).

### 9. Generic gaps for dskit

- **G1 — log-uniform range specs for an estimator-level search. Tier 1 grammar
  reuse; ADR candidate.** `libs/optuna.py::_spec_problems` is "the ONE place the
  range form's internals are defined", and the child restates a discrete random
  search `hpo-grid` already implements. Either (a) lift `_spec_problems` plus a
  sampler into a tier-1 helper both packs and a child search import, or (b) let
  `BenchmarkPlan` admit a search node wired to an *inner-training* score node so
  the child stops carrying its own searcher. (b) is deeper; (a) unblocks §8 now.
- **G2 — a seed-ensemble knob for estimator-level search. Tier 1.** `hpo-grid`'s
  `seeds` has no equivalent, so every neural number here is single-seed.
- **G3 — validation-monitored stopping for the sklearn-shaped torch estimators.
  Tier 2 (`libs/torch_ts.py`).** `libs/torch.py` has `monitor`/`patience`/restore;
  the two `Categorical*Regressor` estimators have none, so `epochs` must be bought
  with a search dimension.
- **G4 — a trial ledger and winner record on any search, estimator-level included.
  Tier 1 contract, tier 3 emission.** ADR-0043 §3/§4 already require `search_meta`
  and a distinct-winner count for search nodes.
- **G5 — k rotating inner holdouts inside fold train. Tier 3, then tier 1.**
  `_hpo_cuts` returns one cut; two or three averaged halve selection noise without
  shortening the inner train.

### 10. Ranked experiments

1. **P15a — pooled LightGBM, §8 space, `hpo_trials: 24`, `hpo_objective: "path"`,
   20 outer folds, otherwise byte-identical to P13.** Adopt if the mean path score
   beats 0.006401 by more than paired HAC 2 SE (P13's comparison SE was 0.001234,
   so roughly +0.0025), taking the 1-SE-simplest trial rather than the argmax;
   reject the widening if it does not clear that and no knob shows a direction.
2. **P15b — emit the ledger (G4) and read the distinct-winner count.** Cheap, and
   the precondition for pinning. ≥ 10 distinct winners over 20 folds → the surface
   is noise: pin the modal-or-simplest setting and drop the search. ≤ 5 distinct
   with a mode in ≥ 8 folds → keep per-fold tuning as measurement.
3. **P15c — pooled MLP, §8 shrinkage-weighted space, `hpo_trials: 24`.** The test
   is **fold sd**, not the mean: a fall from 0.005516 toward 0.002358 at an
   unchanged mean confirms §4; sd staying high means the family is variance-bound
   and the next lever is G2, not architecture.
4. **P14 revision — 4 searched dimensions, `hpo_trials: 16`, small pinned
   architecture.** Does any `context_length` beat the LightGBM incumbent within
   Bonferroni? If not, the recurrent arm closes and `context_length` is answered.
5. **Finalist pin, only after 1-3.** One setting, search dropped, one run on the
   finalist HPO calendar. No decision rule — it records the pinned candidate, it
   does not choose among them. Nothing auto-promotes.

### 11. What not to do

Do not early-stop rounds on the inner holdout then refit on 11× the rows; do not
add `min_split_gain` without a variance-scaled guard, or search `subsample`
without `subsample_freq`; do not switch to TPE at K ≤ 16, where it is random
search with a dependency attached; do not build Hyperband/ASHA; do not report an
inner score as evidence; do not tighten the outer Bonferroni because trials went
up, since trials do not enter it and candidates do; do not tune the two families
on different objectives, because the comparison is paired only if the selection
functional is shared; and do not touch data from 2026-03-01.

## Sources

- Probst, P., Boulesteix, A.-L., & Bischl, B. (2019). "Tunability: Importance of
  Hyperparameters of Machine Learning Algorithms." *JMLR* 20(53), 1-32.
  https://jmlr.org/papers/v20/18-444.html · https://arxiv.org/abs/1802.09596
  (tunability ordering and the range/trafo table read from the ar5iv rendering)
- Bischl, B., Binder, M., Lang, M., Pielok, T., Richter, J., Coors, S., et al.
  (2023). "Hyperparameter optimization: Foundations, algorithms, best practices,
  and open challenges." *WIREs Data Mining and Knowledge Discovery* 13(2), e1484.
  https://doi.org/10.1002/widm.1484 · https://arxiv.org/abs/2107.05847 (not fetched)
- van Rijn, J. N., & Hutter, F. (2018). "Hyperparameter Importance Across
  Datasets." *KDD '18*. https://arxiv.org/pdf/1710.04725 (not fetched)
- Bergstra, J., & Bengio, Y. (2012). "Random Search for Hyper-Parameter
  Optimization." *JMLR* 13, 281-305. https://jmlr.org/papers/v13/bergstra12a.html
  (not fetched — PDF would not decode; the `1-(1-q)^K` identity is elementary and
  is stated here as arithmetic, not as a quotation)
- Bergstra, J., Bardenet, R., Bengio, Y., & Kégl, B. (2011). "Algorithms for
  Hyper-Parameter Optimization." *NIPS 24*.
  http://papers.neurips.cc/paper/4443-algorithms-for-hyper-parameter-optimization.pdf
  (not fetched)
- Akiba, T., Sano, S., Yanase, T., Ohta, T., & Koyama, M. (2019). "Optuna: A
  Next-generation Hyperparameter Optimization Framework." *KDD '19*.
  https://arxiv.org/abs/1907.10902 (not fetched). `TPESampler.n_startup_trials`
  default 10 confirmed at
  https://optuna.readthedocs.io/en/stable/reference/samplers/generated/optuna.samplers.TPESampler.html
- Li, L., Jamieson, K., DeSalvo, G., Rostamizadeh, A., & Talwalkar, A. (2018).
  "Hyperband: A Novel Bandit-Based Approach to Hyperparameter Optimization."
  *JMLR* 18(185), 1-52. https://jmlr.org/papers/v18/16-558.html (not fetched)
- Grinsztajn, L., Oyallon, E., & Varoquaux, G. (2022). "Why do tree-based models
  still outperform deep learning on tabular data?" *NeurIPS 2022 D&B*.
  https://arxiv.org/abs/2207.08815 (abstract fetched — a 20,000 compute-hour
  search per learner; the search-space appendix was not fetched)
- Schneider, L., Bischl, B., & Feurer, M. (2025). "Overtuning in Hyperparameter
  Optimization." *AutoML Conf. 2025*. https://arxiv.org/abs/2506.19540
- Cawley, G. C., & Talbot, N. L. C. (2010). "On Over-fitting in Model Selection
  and Subsequent Selection Bias in Performance Evaluation." *JMLR* 11, 2079-2107.
  https://jmlr.org/papers/v11/cawley10a.html (not fetched)
- Varma, S., & Simon, R. (2006). "Bias in error estimation when using
  cross-validation for model selection." *BMC Bioinformatics* 7:91.
  https://doi.org/10.1186/1471-2105-7-91 (not fetched)
- White, H. (2000). "A Reality Check for Data Snooping." *Econometrica* 68(5),
  1097-1126. https://doi.org/10.1111/1468-0262.00152 (not fetched)
- Bailey, D. H., & López de Prado, M. (2014). "The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting and Non-Normality."
  *Journal of Portfolio Management* 40(5).
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551 (not fetched)
- Breiman, L., Friedman, J., Olshen, R., & Stone, C. (1984). *Classification and
  Regression Trees* (the one-standard-error rule) (not fetched); Hastie,
  Tibshirani & Friedman, *The Elements of Statistical Learning* §7.10,
  https://hastie.su.domains/ElemStatLearn/ (not fetched)
- LightGBM documentation, "Parameters Tuning."
  https://lightgbm.readthedocs.io/en/latest/Parameters-Tuning.html

Repo files relied on:

- /home/user/dskit/children/intraday_equities/configs/run-p13-pooled-model-zoo.json
- /home/user/dskit/children/intraday_equities/configs/run-p14-recurrent-fusion-zoo.json
- /home/user/dskit/children/intraday_equities/configs/universe-p13-pooled.json
- /home/user/dskit/children/intraday_equities/intraday_equities/nodes.py
  (`_hpo_cuts` 3236, `_hpo_combos` 3244, `_tune_estimator` 3268, `_fit_estimator`
  3099, HPO call site 5432-5535, `NoInformationScan` 5040)
- /home/user/dskit/children/intraday_equities/intraday_equities/model_zoo.py
  (`_template_problems` 317, path-score objective 1168)
- /home/user/dskit/children/intraday_equities/docs/memos/p13-pooled-kronos-model-zoo-results.md
- /home/user/dskit/children/intraday_equities/docs/memos/2026-09-05-predictive-model-strategy-audit.md
- /home/user/dskit/children/intraday_equities/docs/research/jpm-h-20-6-of-7-folds-negative-hpo-tunes-noise.md
- /home/user/dskit/children/intraday_equities/docs/research/p7-model-size-for-this-noise-level-a-ranked-shortlist-and-a-hold-on-the-big-models.md
- /home/user/dskit/children/intraday_equities/docs/research/three-declared-knobs-that-did-nothing-or-the-opposite.md
- /home/user/dskit/dskit/pipeline/kinds_search.py (`HpoGrid`, `seeds`, sha256 subsample)
- /home/user/dskit/dskit/pipeline/libs/optuna.py (`_spec_problems`, the pruning refusal)
- /home/user/dskit/dskit/pipeline/libs/torch.py (`monitor`/`patience`, ADR-0035/0054)
- /home/user/dskit/dskit/pipeline/libs/torch_ts.py (`CategoricalEmbeddingMLPRegressor` 853)
- /home/user/dskit/dskit/pipeline/benchmarks.py (search-node refusal, line 381)
- /home/user/dskit/docs/architecture/decision-log.md (ADR-0043 ~1851, ADR-0044 ~1905)
- /home/user/dskit/TODO.md (item 13 landed; the HPO-prerequisite list ~865-930)
