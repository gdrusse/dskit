# Seed ensembles vs stacking for weak-signal return models

## Question

For weak-signal equity-return prediction, how should one choose among equal-weight independent-seed ensembles, stacking, and snapshot ensembles? How many members capture most gains, how should a fixed parameter budget be allocated, and when should different architectures be combined?

## Finding

**Default: average five independently trained copies of the same validated architecture; extend to ten only if rolling out-of-time results show worthwhile incremental gain.** There is no universal “5–10” theorem. Lakshminarayanan, Pritzel, and Blundell recommend \(M=5\) and report materially better uncertainty estimates even at that size. Gu, Kelly, and Xiu use ten independently seeded networks and average their return forecasts; their supplement explicitly records `Ensemble=10`. This is strong precedent, not proof that ten is optimal for every financial dataset.

The economics of member count are governed by error correlation. If members have equal residual variance \(\sigma^2\) and pairwise correlation \(\rho\), the average has variance approximately

\[
\sigma^2\left[\rho+\frac{1-\rho}{M}\right].
\]

With independent errors, five members remove 80% and ten remove 90% of the reducible variance. With \(\rho=0.8\), those totals become 0.84 and 0.82: ten barely improves upon five. Thus measure the ensemble’s marginal out-of-time improvement and residual correlation, rather than assuming a member count.

For tiny-SNR returns, equal weighting has an important advantage: it introduces no additional fitted parameters. Stacking can theoretically exploit unequal skill, but its meta-learner must estimate weights from scarce, serially dependent validation evidence. Wolpert’s stacking and Breiman’s stacked regression require genuinely out-of-fold level-one predictions. In finance this means purged, embargoed, temporally ordered predictions—not random cross-validation. Smith and Wallis explain why estimated forecast-combination weights often lose to simple averages: finite-sample weight error can exceed the population benefit. Highly correlated seed forecasts make that problem especially ill-conditioned. If stacking is tested, use strongly regularized, preferably nonnegative weights and compare it directly with equal weighting on untouched periods.

Snapshot ensembles save checkpoints reached during one cyclic-learning-rate trajectory. Huang et al. showed that they can improve accuracy at roughly one run’s training cost. They are attractive as a compute-constrained baseline, but they are not equivalent to independent seeds. Fort, Hu, and Lakshminarayanan found that solutions along one trajectory or its local subspace cluster in function space, whereas independent initializations explore substantially different predictive modes. For weak financial signals, snapshots may therefore provide less variance reduction and overstate epistemic coverage because their apparent parameter diversity can conceal nearly identical predictions.

A fixed parameter budget does not automatically favor one large member. Lobacheva et al. found a “memory split advantage”: several medium-width networks can outperform one wide network with the same total parameter count, while excessively thin members also underperform. Their evidence is mainly from image classification, so the relevant conclusion for returns is procedural: jointly tune member capacity and member count under the real compute/memory budget. Do not shrink members below the capacity needed to learn the signal merely to reach ten.

Deep ensembles support both point prediction and uncertainty. In Lakshminarayanan et al.’s regression construction, each member predicts a conditional mean and heteroscedastic variance; the uniformly weighted Gaussian mixture combines average within-member variance with dispersion among member means. The former represents modeled data noise and the latter model disagreement. This can improve likelihood and calibration as well as accuracy. But disagreement among networks trained on the same historical panel does **not** reveal shared misspecification, omitted predictors, common leakage, or an unseen market regime. Finance requires rolling calibration and coverage checks under regime shifts before ensemble spread is used for sizing or abstention.

Across architectures, ensemble only demonstrated complementarity—not architectural variety for its own sake. Different inductive biases can reduce shared bias, and neural ensemble search has shown that selected heterogeneous architectures can outperform fixed-architecture deep ensembles. Yet diversity is “fake” when models use the same data and converge to economically equivalent forecasts. Conversely, forcing diversity by weakening members can hurt. Compare architectures using purged out-of-time residual correlation, rank-IC complementarity, calibration, turnover, and net performance. A sensible hierarchy is: five seeds per credible architecture; establish each architecture’s standalone value; then compare equal-weight architecture means with a regularized temporal stack. Retain cross-architecture pooling only when gains survive costs and multiple periods.

## Sources

- Gu, Kelly, and Xiu (2020), [“Empirical Asset Pricing via Machine Learning”](https://doi.org/10.1093/rfs/hhaa009), *Review of Financial Studies*; [Internet Appendix](https://dachxiu.chicagobooth.edu/download/ML_supp.pdf).
- Lakshminarayanan, Pritzel, and Blundell (2017), [“Simple and Scalable Predictive Uncertainty Estimation Using Deep Ensembles”](https://proceedings.neurips.cc/paper/2017/hash/9ef2ed4b7fd2c810847ffa5fa85bce38-Abstract.html), NeurIPS.
- Hansen and Salamon (1990), [“Neural Network Ensembles”](https://doi.org/10.1109/34.58871), IEEE TPAMI.
- Wolpert (1992), [“Stacked Generalization”](https://doi.org/10.1016/S0893-6080(05)80023-1), *Neural Networks*.
- Breiman (1996), [“Stacked Regressions”](https://doi.org/10.1007/BF00117832), *Machine Learning*.
- Smith and Wallis (2009), [“A Simple Explanation of the Forecast Combination Puzzle”](https://doi.org/10.1111/j.1468-0084.2008.00541.x), *Oxford Bulletin of Economics and Statistics*.
- Huang et al. (2017), [“Snapshot Ensembles: Train 1, Get M for Free”](https://arxiv.org/abs/1704.00109), ICLR.
- Fort, Hu, and Lakshminarayanan (2019), [“Deep Ensembles: A Loss Landscape Perspective”](https://arxiv.org/abs/1912.02757).
- Lobacheva et al. (2020), [“Deep Ensembles on a Fixed Memory Budget”](https://arxiv.org/abs/2005.07292).
- Zaidi et al. (2021), [“Neural Ensemble Search for Uncertainty Estimation and Dataset Shift”](https://proceedings.neurips.cc/paper/2021/hash/41a6fd31aa2e75c3c6d427db3d17ea80-Abstract.html), NeurIPS.
