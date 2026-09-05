# Shared backbone vs per-name heads vs foundation models

## Question

After Gate 3, should `intraday_equities` use independent name-specific models, a shared backbone with specialized heads, same-architecture ensembles, a pretrained time-series foundation model, or a mixture—given dozens of stocks, name-specific horizons, minute data, and extremely weak signal?

## Finding

Use a **partially pooled mixture**, not one architecture everywhere:

1. **Primary candidate:** a small shared cross-name representation with lightweight name-specific output heads.
2. **Mandatory controls and fallbacks:** independently fitted ridge/tree models for every name.
3. **Optional stabilization:** small same-architecture ensembles only after an architecture demonstrates repeatable out-of-sample value.
4. **Foundation models:** frozen zero-shot challengers first; parameter-efficient adaptation only if they clear that benchmark. Do not train or fully fine-tune a large transformer by default.

### What the predictor should emit

At decision time \(t\), emit exactly one direct forecast per eligible name:

\[
\hat{\mathbf y}_t =
\left(
\widehat r_{1,t\rightarrow t+H_1},
\ldots,
\widehat r_{N,t\rightarrow t+H_N}
\right),
\]

where \(H_i\) is the horizon selected for name \(i\) by the modelability gate. Thus AAPL might emit a 10-minute forward return while JPM emits a 30-minute return. This is **not** one common-horizon vector and need not be a complete trajectory from minute 1 through \(H_i\). A practical output record should carry `name`, `asof`, `horizon`, `forecast`, and—if validated—predictive scale or ensemble dispersion.

Train each output directly against that name’s \(H_i\)-ahead return. Marcellino, Stock, and Watson distinguish direct horizon-specific estimation from recursively iterating a one-step model: iteration is more efficient when the one-step specification is correct, while direct estimation is more robust to misspecification. Their macroeconomic experiment favored iteration, so the literature does not establish a universal direct-forecast victory [4]. Here, however, recursive minute-return prediction would require an unusually trustworthy transition model and repeatedly feed predictions back into it. Because Gate 3 has already selected heterogeneous terminal horizons, direct terminal targets align the statistical objective with the delivered forecast and avoid unnecessary rollout error.

### Why sharing should be tested first

Caruana’s multi-task-learning result is the central statistical argument: related tasks can use shared hidden representations as an inductive bias, improving generalization when each task alone has limited effective data [1]. The finance evidence also favors pooling. Gu, Kelly, and Xiu estimate one function shared across stocks rather than independent stock models; the pooled panel stabilizes expected-return estimation, and shallow trees and neural networks outperform many linear alternatives through nonlinear interactions [2]. Sirignano and Cont go further: a universal model trained across many US equities outperformed asset-specific models and generalized to stocks omitted from training [3].

Those papers do **not** justify a large transformer here. Gu–Kelly–Xiu used a vastly broader panel and found performance peak at moderate depth before declining [2]. Sirignano–Cont used billions of order-book quotes and transactions, not a modest panel of one-minute features [3]. Their transferable conclusion is pooling, not model size.

The first shared candidate should therefore be deliberately small: a pooled linear/tree baseline or compact shared trunk, followed by a low-capacity residual head for each name. The trunk learns common effects such as volatility, liquidity, market movement, and lag-shape responses; each head calibrates the representation to that name and its selected \(H_i\). Horizon may be supplied as a conditioning value, but the emitted head remains tied to the pair \((i,H_i)\). Names with insufficient evidence for specialization can use the global head.

### When sharing helps—and when it hurts

Sharing helps when names exhibit stable, similarly signed feature effects, comparable target scaling, and positive transfer under chronological validation. It is especially useful when per-name samples are too small to estimate separate nonlinear models reliably [1–3].

It hurts when liquidity regimes, microstructure, sector exposures, or chosen horizons demand incompatible representations. Multi-task gradients can conflict; Yu et al. define conflict through negative gradient inner products and show that projecting conflicting gradients can improve multi-task optimization [11]. The correct safeguard is empirical, not architectural optimism:

- compare every name’s shared-head score with its independent baseline;
- inspect fold-by-fold deterioration, not only average cohort gain;
- measure task-gradient cosine similarity if using a neural trunk;
- split incompatible names into learned or validation-selected clusters, or fall back to independent models;
- weight losses so high-volatility or high-sample names do not dominate.

A shared model should survive a **leave-some-names-out** test as well as ordinary walk-forward testing. Generalization to unseen names is the strongest evidence that the trunk learned transferable structure rather than memorized identities, matching the test used by Sirignano and Cont [3].

### Transformer caveats

TFT is designed for multi-horizon forecasting with static covariates, known future inputs, observed historical inputs, recurrent processing, attention, gating, and variable selection [5]. That machinery is valuable when all future steps or quantiles matter, but it is unnecessarily parameter-heavy when each stock contributes one selected terminal horizon.

PatchTST patches long histories efficiently and shares transformer weights across univariate channels, making it a plausible challenger when longer raw lag sequences matter [6]. Its channel-independent design, however, does not explicitly learn contemporaneous cross-name relations. iTransformer takes the opposite approach: it tokenizes variables and attends across them, which can model cross-series dependence [7]. Its evidence comes from generic synchronized multivariate benchmarks, not weak intraday equity returns; changing membership, missing bars, and unstable cross-name relationships make its inductive bias something to test, not assume.

Accordingly, test compact PatchTST/iTransformer variants only after ridge, boosted trees, and a small pooled network. Equalize information sets, chronology, search budget, and parameter count as far as practical. A transformer win that required materially more tuning attempts is not comparable evidence.

### Foundation models and ensembles

Chronos, TimesFM, and Moirai demonstrate competitive zero-shot forecasting across broad collections of nonfinancial time series [8–10]. Their pretraining is potentially useful precisely because local signal is weak. But their generic objectives emphasize forecasting observable series dynamics; they were not pretrained specifically to extract tiny conditional means from intraday returns. Their large parameter counts also make from-scratch training incompatible with the observed budget.

Use them in this order:

1. frozen zero-shot or frozen-encoder evaluation;
2. a trained linear/small head on frozen representations;
3. LoRA/adapters or last-block adaptation on the pooled cohort;
4. full unfreezing only if substantially larger, leakage-safe evidence supports it.

Official Chronos, TimesFM, and Moirai tooling supports fine-tuning or parameter-efficient adaptation [13–15], but capability is not evidence that adaptation will beat simple models.

Finally, ensemble only validated architectures. Gu–Kelly–Xiu average networks initialized with multiple seeds to reduce stochastic prediction variance [2], while deep ensembles can improve calibration and uncertainty estimates [12]. In this low-SNR setting, three to five seeds of the **same small winning specification** are defensible. Ensembling several overfit architectures merely averages expensive noise.

The recommended decision is therefore: **small shared trunk plus direct \((name,H_i)\) heads, guarded by independent per-name baselines and fallbacks; seed ensembles after validation; transformers and frozen foundation models as bounded challengers.**

## Sources

1. Caruana, R. (1997), “Multitask Learning,” *Machine Learning* 28, 41–75. DOI: [10.1023/A:1007379606734](https://doi.org/10.1023/A:1007379606734).
2. Gu, S., Kelly, B., and Xiu, D. (2020), “Empirical Asset Pricing via Machine Learning,” *Review of Financial Studies* 33(5), 2223–2273. DOI: [10.1093/rfs/hhaa009](https://doi.org/10.1093/rfs/hhaa009).
3. Sirignano, J. and Cont, R. (2019), “Universal Features of Price Formation in Financial Markets,” *Quantitative Finance* 19(9), 1449–1459. DOI: [10.1080/14697688.2019.1622295](https://doi.org/10.1080/14697688.2019.1622295).
4. Marcellino, M., Stock, J. H., and Watson, M. W. (2006), “A Comparison of Direct and Iterated Multistep AR Methods,” *Journal of Econometrics* 135, 499–526. DOI: [10.1016/j.jeconom.2005.07.020](https://doi.org/10.1016/j.jeconom.2005.07.020).
5. Lim, B., Arık, S. Ö., Loeff, N., and Pfister, T. (2021), “Temporal Fusion Transformers,” *International Journal of Forecasting* 37(4). DOI: [10.1016/j.ijforecast.2021.03.012](https://doi.org/10.1016/j.ijforecast.2021.03.012).
6. Nie, Y., Nguyen, N. H., Sinthong, P., and Kalagnanam, J. (2023), “A Time Series Is Worth 64 Words,” *ICLR 2023*. [arXiv:2211.14730](https://arxiv.org/abs/2211.14730).
7. Liu, Y. et al. (2024), “iTransformer,” *ICLR 2024 Spotlight*. [arXiv:2310.06625](https://arxiv.org/abs/2310.06625).
8. Ansari, A. F. et al. (2024), “Chronos: Learning the Language of Time Series,” *TMLR*. [arXiv:2403.07815](https://arxiv.org/abs/2403.07815).
9. Das, A. et al. (2024), “A Decoder-Only Foundation Model for Time-Series Forecasting,” *ICML 2024*. [arXiv:2310.10688](https://arxiv.org/abs/2310.10688).
10. Woo, G. et al. (2024), “Unified Training of Universal Time Series Forecasting Transformers,” *ICML 2024*. [PMLR 235](https://proceedings.mlr.press/v235/woo24a.html).
11. Yu, T. et al. (2020), “Gradient Surgery for Multi-Task Learning,” *NeurIPS 2020*. [Paper](https://papers.neurips.cc/paper/2020/hash/3fe78a8acf5fda99de95303940a2420c-Abstract.html).
12. Lakshminarayanan, B., Pritzel, A., and Blundell, C. (2017), “Simple and Scalable Predictive Uncertainty Estimation Using Deep Ensembles,” *NeurIPS 2017*. [arXiv:1612.01474](https://arxiv.org/abs/1612.01474).
13. Amazon Science, [Chronos official training and fine-tuning documentation](https://github.com/amazon-science/chronos-forecasting/tree/main/scripts).
14. Google Research, [TimesFM official LoRA fine-tuning documentation](https://github.com/google-research/timesfm/tree/master/timesfm-forecasting/examples/finetuning).
15. Salesforce AI Research, [Uni2TS/Moirai official fine-tuning framework](https://github.com/SalesforceAIResearch/uni2ts).
