# Tabular features vs long lagged sequences after Gate 3

## Question

For a post-Gate-3 intraday equity model, should the input remain the engineered tabular feature set, become a long sequence of lagged returns/OHLCV bars, or combine both? How should this choice interact with per-name forecast horizon \(H\) and sampling period \(L\)?

## Finding

**Keep engineered features and LightGBM/ridge as the incumbent; test normalized raw sequences and a hybrid as separate challengers. Do not replace the tabular representation merely because transformers can consume longer histories.**

The tabular baseline has the strongest prior for this setting. Grinsztajn, Oyallon, and Varoquaux find that tree ensembles outperform neural networks on typical medium-sized tabular problems because trees tolerate irrelevant columns, heavy-tailed or irregular relationships, and axis-aligned effects. McElfresh et al.’s broader 176-dataset study qualifies the claim—model differences are often small and tuning matters—but again finds boosted trees especially robust to skewness and irregularity. Intraday returns, volumes, volatility estimates, clock effects, and cross-asset residuals have exactly those characteristics.

Gu, Kelly, and Xiu provide the finance-specific reason not to discard engineered inputs. Their monthly-equity study finds that momentum, liquidity, and volatility are dominant signals and that predictive gains come mainly from nonlinear interactions. Trees and shallow neural networks capture those interactions; deeper networks do not automatically improve them. Their frequency and target differ from this project, so the paper is not direct evidence for intraday LightGBM, but it supports retaining economically meaningful summaries rather than assuming raw prices contain a readily learnable grammar.

A raw-sequence model is nevertheless a legitimate challenger because feature engineering is lossy. Rolling return, realized-volatility, and momentum columns compress the path and may discard reversal timing, volatility clustering, volume-price ordering, or a motif occurring at an unknown scale. DeepLOB demonstrates that learned spatial-temporal representations can work on very large, information-rich limit-order-book sequences and transfer across instruments. That result should not be overextended: full book states contain order-flow structure absent from one-minute OHLCV bars.

Generic transformer evidence is also mixed. Zeng et al. show that simple linear mappings of raw windows beat several elaborate long-horizon transformers. PatchTST then shows that patching and self-supervised pretraining can make long contexts useful. TFT is relevant when static attributes, observed covariates, known future variables, and multiple horizons must be combined, but it is not evidence that raw lags alone beat trees. Thus a transformer earns a trial when it receives a genuinely sequential representation, adequate pooled data, and architecture-appropriate pretraining—not when tabular columns are merely repeated along a synthetic time axis.

Foundation models most clearly require raw or lightly normalized histories because their pretrained tokenizer and attention layers expect temporal observations. TimesFM consumes patched scalar histories; Kronos is more directly relevant because it was pretrained on over 12 billion multivariate financial K-lines across markets, granularities, and exchanges. Kronos reports strong zero-shot financial forecasting results from OHLCV-like inputs plus calendar embeddings. However, those benchmarks do not establish superiority for this project’s SPY-residual, volatility-scaled, per-name return label, execution cadence, or locked walk-forward periods. A foundation model should therefore be evaluated as a frozen or lightly fine-tuned challenger, not treated as a replacement on reputation.

“Raw” should not mean absolute price levels. Use causal, split-consistent, stationary channels such as log return, open-to-close return, high-low range, close location within the bar, log-volume change or normalized volume, and perhaps market/sector returns. Preserve the one-minute path and session boundaries. Absolute OHLC prices expose scale, corporate-action, and regime shifts that a model must waste capacity relearning.

The clean post-Gate-3 comparison is:

1. **Tabular incumbent:** current engineered features, including short lags and multi-scale momentum/volatility/liquidity summaries, with ridge and LightGBM.
2. **Sequence challenger:** approximately one session of one-minute normalized bar channels, preferably patched; compare a simple linear-window model before a transformer.
3. **Hybrid challenger:** a sequence encoder whose embedding is concatenated with the engineered tabular vector. This is the strongest conceptual candidate because the sequence branch can learn path shape while the tabular branch preserves low-variance summaries, cross-sectional context, and clock/calendar information.

Require each challenger to beat the incumbent on the same locked folds, names, labels, scoring instants, costs, and many-attempts correction. Ablate sequence-only versus features-only versus hybrid; otherwise a hybrid win cannot establish whether the sequence contributed anything.

Finally, **\(H\) and \(L\) are different controls and should not be coupled mechanically**. \(H\) is the label lead; \(L\) is how often a decision row is formed. A history of \(W\) minutes contains \(W/L\) observations only if the input itself is downsampled at \(L\). It is preferable to retain one-minute input bars while evaluating decisions every \(L\) minutes, so changing decision cadence does not erase microstructure. If \(H/L>1\), adjacent labels overlap; that reduces effective sample size and requires embargo and overlap-aware inference, but it does not justify coarsening the input. Per-name \(H\) should control each name’s target/output head or model, while the candidate context lengths remain fixed in clock time—for example 20 minutes, two hours, and one session—so names are compared on equivalent information. Longer \(H\) may benefit from longer or multi-resolution context, but this must be tested rather than imposed; the project’s existing evidence that denser \(L\) helped short-\(H\) prediction warns against sacrificing minute-level observations.

## Sources

- Grinsztajn, Oyallon, and Varoquaux (2022), “Why do tree-based models still outperform deep learning on typical tabular data?” [NeurIPS proceedings](https://proceedings.neurips.cc/paper_files/paper/2022/hash/0378c7692da36807bdec87ab043cdadc-Abstract-Datasets_and_Benchmarks.html).
- McElfresh et al. (2023), “When Do Neural Nets Outperform Boosted Trees on Tabular Data?” [arXiv:2305.02997](https://arxiv.org/abs/2305.02997).
- Gu, Kelly, and Xiu (2020), “Empirical Asset Pricing via Machine Learning,” *Review of Financial Studies*. [DOI:10.1093/rfs/hhaa009](https://doi.org/10.1093/rfs/hhaa009).
- Zhang, Zohren, and Roberts (2019), “DeepLOB.” [DOI:10.1109/TSP.2019.2907260](https://doi.org/10.1109/TSP.2019.2907260).
- Zeng et al. (2023), “Are Transformers Effective for Time Series Forecasting?” [DOI:10.1609/aaai.v37i9.26317](https://doi.org/10.1609/aaai.v37i9.26317).
- Nie et al. (2023), “A Time Series Is Worth 64 Words: Long-term Forecasting with Transformers.” [arXiv:2211.14730](https://arxiv.org/abs/2211.14730).
- Lim et al. (2021), “Temporal Fusion Transformers for Interpretable Multi-horizon Time Series Forecasting.” [DOI:10.1016/j.ijforecast.2021.03.012](https://doi.org/10.1016/j.ijforecast.2021.03.012).
- Das et al. (2024), “A Decoder-Only Foundation Model for Time-Series Forecasting.” [arXiv:2310.10688](https://arxiv.org/abs/2310.10688).
- Shi et al. (2025), “Kronos: A Foundation Model for the Language of Financial Markets.” [arXiv:2508.02739](https://arxiv.org/abs/2508.02739).
