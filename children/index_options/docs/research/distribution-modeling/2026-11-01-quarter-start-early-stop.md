## Question
Should the selected MLP64 use patience and early stopping on a recent validation band before each quarter, while Q3 and Q4 remain untouched test periods?

## Finding
Yes. Early stopping uses a chronological validation band known before the forecast starts; the traded quarter is test evidence, never the monitor that selects weights or epochs. The validation band's labels must have settled strictly before quarter start. Recent validation helps with drift, but the Q3 and Q4 outcomes already inspected here make any new H2 comparison retrospective development, not a fresh holdout.

The saved 2019-calibrated fit used 2019-08-30..2019-12-26 monitoring and selected epoch 1 (51 epochs run with patience 50; summary SHA256 d7d858f11b1937d802ca3d950235086499585f22633281f020dfb4600cc786f3). A saved 2020-calibrated fit used 2020-09-03..2020-12-30 monitoring and selected epoch 78 (128 epochs run; SHA256 0f417ecb0670061cdc22594fa0425d9e28b883898c8260fb4bf5f2bf81559896). Therefore neither fixed 150 nor a blindly transferred epoch 1 is defensible.

Owner constraint: Q3 and Q4 are never model-selection validation. Use pre-Q3 evidence, including settled Q2, to choose the stopping rule. For each quarter, refit the selected architecture on all labels settled before its start, then freeze weights and M53 blend weights for that quarter. Q3 outcomes may enter Q4 *training* after settlement, while Q3 remains excluded from validation under the owner constraint. Preserve old fixed-2019 and in-quarter rolling runs as exposed evidence.

The owner authorized config-only completion and waived a new ADR. The existing generic CDFExperiment refit_full_train option combines the mature fit and calibration bands; patience is disabled only during that final refit, after the epoch count has been selected. This run explicitly transfers the selected number of epochs, not the number of optimizer steps: expanding data therefore increases optimizer steps. The selection run, full-refit config, band dates, weights and diagnostics are pinned. The focused module checks passed (86 passed, 2 skipped).

Corrected settlement coverage supersedes the initial epoch-4 result. Pre-Q2 selects epoch 46 after 96 epochs; pre-Q3 selects epoch 36 after 86. Pre-Q3 monitor loss decreases from 0.822575 before training to 0.793174 at epoch 1 and 0.789470 at epoch 36, with nonzero parameter change. Q4 reuses the pre-Q3 stopping length and includes mature Q3 labels in training; neither Q3 nor Q4 selects epochs. M53 uses only settled prior forecast outcomes to fit one blend at each quarter start.

The saved technical-design.json explicitly records max 150 epochs, patience 50 and no additional HPO search for the M53 neural component. Earlier L1/matrix screening used patience 10. M53 itself was chosen from 67 complete saved forecasts using pooled 2020–2023 twCRPS; this is retrospective model selection, not untouched 2021 evidence. Quarter replays keep the accepted seed29 component and all three portfolio methods fixed.

## Sources
- Lindholm and Palmborg, original research, *Efficient use of data for LSTM mortality forecasting* (2022): chronological last-fraction validation is the standard early-stop method for time series. https://link.springer.com/article/10.1007/s13385-022-00307-3
- Goodfellow, Bengio and Courville, *Deep Learning*, chapter 7, algorithm 7.2: select stopping length on validation, then reinitialize and retrain on all available training data; notes the optimizer-steps-versus-epochs ambiguity. https://www.deeplearningbook.org/contents/regularization.html
- Hewamalage, Ackermann and Bergmeir, original research, *Forecast evaluation for data scientists* (2023): rolling-origin updates transfer past test observations into later training, with leakage controls. https://pmc.ncbi.nlm.nih.gov/articles/PMC9718476/
- scikit-learn TimeSeriesSplit documentation: expanding chronological splits and a gap at the boundary. https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
