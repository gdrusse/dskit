## Question

Only two model sizes were tried, and the big ones lost badly: the three
nets forecast 5% to 54% worse than simply guessing the training average,
with high in-sample ordering and negative out-of-sample ordering. What
size of model actually suits 2M bars per stock, three stocks, and a
signal this weak? What sits between a ridge and a transformer, does
averaging several small models help, and is another run of the big
models worth the machine time?

## Finding

**1. The signal is a fraction of a percent, and that sets the size.**
Published out-of-sample fits for stock returns are tiny: 0.24% for
five-minute S&P 500 ETF returns from lasso, elastic net and random
forest, rising to 0.26% when several models are averaged. Monthly
single-stock work peaks near 0.40%. A rough count follows from that: to
see a signal worth R-squared of 0.1% at two standard errors you need
about 4/0.001 = 4,000 rows per free number the model estimates. One fold
trains on about 117,000 rows (730 days, one row per 5 minutes, three
names), so the budget is roughly **30 freely-estimated numbers**. Ridge
has 88 and shrinks them hard, so it fits. The GRU as run has about
**9,800** weights, the LSTM about 13,000, the TFT about 5,000 - between
170 and 440 times over budget. Nothing else needs explaining.

**2. The literature says the same thing twice.** Gu, Kelly and Xiu ran
every method side by side on 30,000 stocks over 60 years with about 900
inputs. Neural nets won - but performance **peaks at one to three hidden
layers of 32/16/8 units and falls with every layer added**, which they
attribute to small data and a tiny signal. Away from finance, Grinsztajn
et al. show trees still beat nets on medium tabular data and that nets
are specifically **not robust to uninformative inputs** - which is
exactly our 67 constant channels. Israel, Kelly and Moskowitz make the
general point: return prediction is the small-data, low-signal corner of
the subject.

**3. Where nets have genuinely won, they had breadth we do not have.**
Sirignano and Cont: billions of order-book events across about 500
stocks, and the model trained on ALL stocks beat per-stock models.
Fischer and Krauss: every S&P 500 name over 23 years, and the edge died
after 2010. Gu, Kelly and Xiu: 30,000 names. The winning ingredient is
thousands of assets sharing one model, not depth. We have three names.
Kelly, Malamud and Zhou are the honest counter-example - more parameters
than rows CAN help - but only under heavy ridge shrinkage, which is a
shrunken linear model, not a free-running net.

**4. The middle ground is mostly one line of config each.** PLS
(`PLSRegression`, which scales its own inputs) with 1 to 8 components is
a supervised, shrunken linear model and the natural step past ridge.
Random forest and extra trees are averages of hundreds of shallow models
by construction, need no scaling, and cost about what LightGBM costs.
Our LightGBM has never been tried in its most restrained region: the
tuning grid bottoms out at 15 leaves and 100 rows per leaf, when the
budget above argues for 2 to 4 leaves and 1,000 to 4,000 rows per leaf.
`ZooEstimator` already carries `nlinear` (a scaled linear model) and
`mlp`, and its `weight_decay` knob is exactly an L2 penalty - so a small,
decayed net needs no new code. Plain elastic net does NOT fit today:
nothing in `_fit_estimator` scales the columns and a config cannot
express an sklearn Pipeline.

**5. Averaging helps most precisely here.** The forecast-combination
literature (Timmermann; Smith and Wallis) finds the plain equal-weight
average beats cleverly weighted ones because it estimates no weights -
the gain is pure variance reduction, which is nearly the whole error
when the signal is this small. Gu, Kelly and Xiu average **10 random
seeds** as standard practice. Ensemble studies find **5 members buy most
of the gain and past 10 the improvement sits inside the noise**. Our
nets ran on a single seed, so a large part of what we recorded may be
seed luck. Seed averaging needs a small wrapper (an ADR); random forest
and extra trees give the same effect today for free.

**6. Four settings caused the memorisation, all fixable in config.**
The net runs used `weight_decay: 0.0`, a fixed 30 epochs with no stop
rule, `lr: 0.003`, and no tuning block at all - while ridge and LightGBM
each got a 6-trial search on a purged inner holdout (63 days, 5-day
embargo). `hpo_space` is a generic grid over any estimator setting, so
putting `epochs` in it **is** early stopping chosen on purged data, with
no new code. Reference settings: weight decay 1e-4 to 1e-1 (start 1e-2),
learning rate 1e-4 to 1e-3, dropout 0.1 to 0.3, batch 4,096, and for the
GRU `hidden_size: 4` instead of 32, which cuts it from 9,800 weights to
about 900 - inside a factor of 30 of the budget rather than 340.

**7. How to feed a sequence model properly.** The row is split by name:
`ret_lag_0..19` becomes a 20-step time axis and the other 67 columns are
each broadcast as a **constant** channel. So the model sees 68 channels
of which 67 never move - a per-row fingerprint that is trivially
memorised and carries no dynamics, which matches the high training
ordering and negative validation ordering exactly. A correct feed gives
every channel its own lag history: rows by steps by channels, with all
channels varying. Typical depths elsewhere: 100 steps for order-book
nets, 240 for daily LSTM work, about 30 periods for transformer studies
on stocks. For minute bars, 30 to 60 minutes of history on a handful of
channels is the right target - and that is P3 work, not a config change.

**8. Ranked shortlist.** Everything below holds the cohort, the folds
(20 from 2022-05-06, 63-day step and validation, 5-day embargo, 730-day
train), the ADR-0059 label and `hpo_objective: ic` identical, and runs
at H=1, 2 and 3 first - the only cells where anything was ever positive.

1. **LightGBM, much harder held back.** `num_leaves 4, max_depth 2,
   min_child_samples 2000, learning_rate 0.005, n_estimators 800,
   reg_lambda 100, subsample 0.5 (subsample_freq 1), colsample_bytree
   0.3`; search `num_leaves [2,4,8]`, `min_child_samples
   [1000,2000,4000]`, `reg_lambda [10,100,1000]`. Already the best cell
   at H=1, and this region was never searched. About 4 minutes.
2. **Extra trees.** `sklearn.ensemble.ExtraTreesRegressor`, 300 trees,
   `max_depth 6, min_samples_leaf 500, max_features 0.3, n_jobs 4,
   random_state 0`; search depth [4,6,8], leaf [200,500,1000]. The
   cheapest honest test of averaging. Then `RandomForestRegressor` on
   the same grid.
3. **PLS.** `sklearn.cross_decomposition.PLSRegression`, search
   `n_components [1,2,3,5,8]`. Scales itself; nothing else to set.
4. **Scaled linear.** `ZooEstimator` with `arch: nlinear`, `epochs 20`,
   `lr 1e-3`, `batch_size 4096`; search `weight_decay
   [1e-5,1e-4,1e-3,1e-2,1e-1]`. Shows how much of the ridge result is
   just the unscaled columns.
5. **Small net - the decisive capacity test.** `ZooEstimator` with
   `arch: gru`, `arch_params {"gru": {"hidden_size": 4}}`, `lr 3e-4`,
   `batch_size 4096`, `weight_decay 1e-2`; search `epochs [2,5,10,20]`
   and `weight_decay [1e-3,1e-2,1e-1]`. Same architecture that failed,
   11 times smaller, with a stop rule chosen on purged data.
6. **Small MLP.** `arch: mlp`, `hidden_size 4`, same regime - only if
   item 5 is not clearly worse than ridge.
7. **Seed averaging** (needs an ADR): average 5 fits of whichever of 5
   or 6 survives, over seeds 0 to 4.

**9. Should the big models run again? No - not now.** Four things would
have to be true first: (a) a per-channel lag block exists so the nets
receive real history instead of 67 frozen channels (P3); (b) the small
net at item 5 at least matches ridge - if 900 weights cannot, 10,000
will not; (c) seed averaging is in place, since every net number we hold
comes from a single seed; (d) more names, ideally the five that P9
restores, because every published net win rests on hundreds to thousands
of assets. Until then the nets are the slowest arm on the board and the
expected return per machine-hour is far higher in items 1 to 4.

## Sources

- Gu, Kelly, Xiu, "Empirical Asset Pricing via Machine Learning", RFS
  2020 (https://academic.oup.com/rfs/article/33/5/2223/5758276);
  hyperparameters (L1 1e-5 to 1e-3, lr 0.01, batch 10,000, 100 epochs,
  patience 5, Adam, batch norm, 10 seeds) via a replication
  (https://github.com/duongtran14/Partial-replication-of-Gu-Kelly-Xiu-2020-Empirical-Asset-Pricing-via-Machine-Learning.)
- "Intraday Market Predictability: A Machine Learning Approach",
  Journal of Financial Econometrics 2023 - 0.24% out-of-sample R-squared
  at five minutes, 0.26% combined
  (https://academic.oup.com/jfec/article-abstract/21/2/485/6400345)
- Chinco, Clark-Joseph, Ye, "Sparse Signals in the Cross-Section of
  Returns", Journal of Finance 2019 - lasso beats OLS by 23% at one
  minute (https://onlinelibrary.wiley.com/doi/10.1111/jofi.12733)
- Aleti, Bollerslev, Siggaard, "Intraday Market Return Predictability
  Culled from the Factor Zoo", Management Science 2025
  (https://doi.org/10.1287/mnsc.2023.01657)
- Grinsztajn, Oyallon, Varoquaux, "Why do tree-based models still
  outperform deep learning on tabular data?", NeurIPS 2022
  (https://arxiv.org/abs/2207.08815)
- Sirignano, Cont, "Universal features of price formation in financial
  markets", Quantitative Finance 2019 (https://arxiv.org/abs/1803.06917)
- Fischer, Krauss, "Deep learning with long short-term memory networks
  for financial market predictions", EJOR 2018
  (https://www.sciencedirect.com/science/article/abs/pii/S0377221717310652)
- Kelly, Malamud, Zhou, "The Virtue of Complexity in Return Prediction",
  Journal of Finance 2024
  (https://onlinelibrary.wiley.com/doi/10.1111/jofi.13298)
- Israel, Kelly, Moskowitz, "Can Machines Learn Finance?", 2020
  (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3624052);
  Kelly, Xiu, "Financial Machine Learning", NBER 31502
  (https://www.nber.org/system/files/working_papers/w31502/w31502.pdf)
- Timmermann; Smith and Wallis - the forecast combination puzzle, review
  at (https://arxiv.org/pdf/2205.04216)
- Lakshminarayanan et al. 2017 and follow-ups on ensemble size - five
  members buy most of the gain, past ten is marginal
  (https://arxiv.org/html/2409.02628)
- Zhang, Zohren, Roberts, "DeepLOB" - 100-step windows
  (https://arxiv.org/abs/1808.03668); Lim, Arik, Loeff, Pfister,
  "Temporal Fusion Transformers", IJF 2021
  (https://www.sciencedirect.com/science/article/pii/S0169207021000637)
- In-tree evidence: `dskit/pipeline/libs/torch_ts.py` (ESTIMATOR_DEFAULTS
  has weight_decay 0.0 and no stop rule; the ZooEstimator docstring says
  every non-lag column is "broadcast as a constant channel"),
  `children/intraday_equities/intraday_equities/nodes.py:2060
  _fit_estimator` (no column scaling, so no sklearn Pipeline) and `:2198
  _tune_estimator` (hpo_space is a generic grid over estimator_params),
  `configs/run-multi3-h01-{ridge,lgbm,gru,tft}.json`.
