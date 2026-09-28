## Decision

Select **Ridge on the 27-feature core** as the research champion for forecasting
forward realized volatility in the 21 SPY/QQQ/IWM horizon cells. ElasticNet has
the best point estimate, but its advantage over Ridge is only 0.018% of Ridge's
pooled MSE and disappears under year-block and symbol-year cluster bootstrap.
The existing rule is to prefer the simplest model not detectably worse.

Keep **TCN as the recent-regime challenger** and **MLP as the required neural
benchmark**. This is a variability-model decision only. It does not authorize
an option entry, a distribution forecast, a condor backtest, or trading.

## Question

After gathering a broader, reliable daily feature space, which features and
model architecture best predict the next 1, 2, 3, 5, 10, 15, or 22 sessions of
realized volatility for the seven ADR-0187 buckets on SPY, QQQ, and IWM?

## Protocol

The run was deliberately separated into two time periods.

1. **Feature-family selection, 2013-2018.** Nine representative cells covered
   SPY/QQQ/IWM at 1, 5, and 22 sessions. Ridge and LightGBM tested six feature
   bundles on identical complete-case rows. The frozen rule selected the
   smallest bundle within 0.5% of the best mean relative MSE.
2. **Architecture zoo, 2019-2025.** Eighteen learned models used the exact same
   selected 27 columns, train/validation dates, targets, and expanding folds in
   all 21 cells. The primary score was equal-cell mean MSE divided by the
   expanding-train mean forecast's MSE; lower is better. The 2022-2025 period
   was reported separately rather than used to rewrite the primary rule.

Features ended on the forecast date. A training row was admitted only when its
target end was strictly before validation began. The target was the per-session
root mean square of future close-to-close log returns, matching
`ForwardRealizedVol` rather than an annualized volatility.

The 18-model zoo comprised Ridge, ElasticNet, random forest, Extra Trees,
histogram gradient boosting, LightGBM, DLinear, NLinear, MLP, LSTM, GRU,
LSTM-attention, GRU-attention, TCN, CNN1D, PatchTST, Transformer, and TFT-lite.
The 12 Torch architectures used two seeds, 20 epochs, standardized train-only
inputs and target, and a nonnegative forecast clamp. Static columns were
broadcast along the same 22-step return path for sequence architectures; the
tabular models received the identical flattened columns.

The run produced 648 feature-selection fold-fits and 2,646 zoo fold-fits. Each
zoo model was evaluated on 147 cell-year folds and 36,527 validation-row
predictions. Raw evidence is under ignored local run root
`pipeline_runs/feature_zoo_20260928/`.

## Feature selection

The candidate space contained:

- 22 daily return lags;
- realized volatility over 1, 5, 22, and 66 sessions;
- downside/upside semivariance, momentum, drawdown, and volatility-of-volatility;
- the ETF-specific implied-volatility index: VIX for SPY, VXN for QQQ, RVX for IWM;
- VIX term structure, VVIX, and SKEW;
- gold, oil, emerging-market, silver, and Treasury volatility indices and their
  spreads to VIX.

Mean relative MSE across the Ridge and LightGBM selectors ranked the bundles:

1. core, 27 features: **0.67694**;
2. HAR plus own IV, 5 features: 0.69398;
3. core plus asymmetry, 41 features: 0.72803;
4. core plus term/tail, 38 features: 0.75929;
5. core plus cross-asset volatility, 37 features: 0.77364;
6. full, 62 features: 0.82594.

The selected core is the 22 return lags, `rv_1`, `rv_5`, `rv_22`, `rv_66`, and
`own_iv`. The wider data were useful because they were tested and rejected, not
because they were assumed valuable. Their failure does not prove they contain
no information; it says these direct daily levels/transforms did not survive
this nested out-of-sample screen.

FRED was excluded because the acquired files are latest-vintage and historical
release-time semantics are not encoded. The 53.4-million-row option chain was
also excluded: there is no approved causal daily surface-feature node, and this
run did not invent an unreviewed aggregation. Those are data-contract gaps, not
silent missing values.

## Zoo result

The primary equal-cell relative-MSE ranking was:

1. ElasticNet 0.56156
2. Ridge 0.56167
3. **MLP 0.56690**
4. TCN 0.56976
5. CNN1D 0.58606
6. PatchTST 0.59169
7. LightGBM 0.62068
8. random forest 0.62094
9. Extra Trees 0.62158
10. histogram gradient boosting 0.63094
11. NLinear 0.63497
12. LSTM 0.63720
13. DLinear 0.63764
14. GRU 0.64020
15. LSTM-attention 0.65048
16. Transformer 0.65185
17. GRU-attention 0.66191
18. TFT-lite 0.68150

ElasticNet's pooled skill was 42.42% versus the expanding-train mean and 39.36%
versus trailing-volatility persistence. It had positive skill in all 21 cells
and beat Ridge in 106 of 147 folds. However, its pooled improvement over Ridge
was only 0.018%. A bootstrap that resampled the 21 symbol-year clusters put the
normalized loss difference at -0.00010 with a 95% interval of
[-0.00031, 0.00006]; a seven-year block bootstrap also crossed zero. That is a
tie for model-selection purposes, so Ridge wins on simplicity.

MLP was the best neural architecture and ranked third overall. It was 0.78%
worse than Ridge on pooled MSE, beat Ridge on 60 of 147 folds, and still had
positive skill in every cell. This is a respectable result, but not evidence
that its extra nonlinearity improves the primary loss.

TCN ranked fourth overall and was 1.45% worse than Ridge over 2019-2025, but it
improved on Ridge by 1.92% in pooled MSE during 2022-2025. GRU and CNN1D showed
similar recent gains of 1.76% and 1.47%. TCN is therefore the right challenger
for future folds, not the current champion.

LightGBM was 10.38% worse than Ridge on pooled MSE despite using the same
features. More flexible trees did not uncover enough stable interaction signal
to offset their variance. All 18 models nevertheless had positive aggregate
skill in all 21 cells versus the expanding mean; the durable signal is mainly
in the causal volatility/return state, not in a sophisticated architecture.

As a secondary diagnostic, CNN1D and TCN produced substantially better mean
QLIKE than Ridge. That matters for a later positive scale/distribution model,
but QLIKE was not the registered selection objective and cannot replace MSE
after seeing the result.

## Compute correction

The zoo was CPU-pinned by the research harness for deterministic four-worker
comparison and MacBook-comparable timing. WSL CUDA was **not** blocked. After
the run, PyTorch 2.11.0+cu128 reported CUDA 12.8, one NVIDIA GeForce RTX 5060 Ti,
and completed a CUDA 2048-by-2048 matrix multiply. My earlier statement that
the WSL CUDA path was unavailable was wrong. Future Torch-only screens can use
`device="cuda"`; this completed CPU run does not need repetition for validity.

## Final model decision

- **Champion:** Ridge with the 27-feature core.
- **Point-estimate runner-up:** ElasticNet; statistically and economically tied
  with Ridge at this resolution.
- **Neural benchmark:** MLP.
- **Recent-regime challenger:** TCN, to be judged only on new forward folds.
- **Not selected:** LightGBM, recurrent attention models, Transformer, TFT-lite,
  and the rejected feature families.

The next useful modeling step is not trading. It is to build a reviewed,
point-in-time option-surface feature node and a positive distributional scale
head, then test whether the surface adds stable signed-region distribution
skill beyond this Ridge champion on future lockbox folds.

## Evidence and reproducibility

The local evidence digests are:

- protocol: `4ae68c81539b99134ac9351ea80d098e07f20d7bc357f4581ae3d41a96ae4afa`
- feature decision: `f1903bc3d770f5f5edc0f6bc3838ce62aa64c38bcb69f60e4911f9a6cbc2c4e1`
- 648 feature folds: `df700b16dfe8bf8f9e6af83c4e66ff859a5b88b4091eba62a3a5a7700f40d406`
- 2,646 zoo folds: `ad4fc4f792498e64f5191a9ed359268f1af3f3e5af1adcc8874e76f7a0e5a5b9`
- zoo summary: `0cbb4a34cd2977d161c6d952aabd40fe6af558f3a2154a2bbb233d0ab6c13873`

Related context: `2026-09-27-feature-acquisition.md` documents the acquired
sources and research basis; `2026-09-28-temporal-mlp-gru-screen.md` records the
earlier return-lag-only screen that this controlled comparison supersedes.
