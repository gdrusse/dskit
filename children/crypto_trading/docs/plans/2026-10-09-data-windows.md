# Data windows: predictor, retrain, MIO tuning, holdout

Owner-approved 2026-10-09 (UTC, end-exclusive). Nothing here has been run.

The predictor forecasts the 15-minute BTC/ETH move from Binance 1-minute bars
(2023-06-20 to 2026-10-08) and needs no Kalshi data. Kalshi data enters only
at MIO tuning and the final holdout.

| # | Window | Dates | Used for |
|---|---|---|---|
| W1 | Selection | 2023-06-20 to 2026-05-01 | model zoo, feature selection, HPO; expanding walk-forward folds |
| gap | Embargo | 2026-05-01 to 2026-05-02 | dropped from fit and scoring |
| W2 | Predictor check | 2026-05-02 to 2026-08-01 | one read of the predictor alone, scored on the Binance move; no Kalshi prices, fees or MIO |
| W3 | Retrain | 2023-06-20 to 2026-08-01 | one refit after W2 with the model, features and hyperparameters frozen from W1; then frozen again |
| W4 | MIO tuning | 2026-08-01 to 2026-09-15 | tune the MIO on Kalshi, predictor frozen; first window with Kalshi quotes, fees and basis |
| W5 | Holdout | 2026-09-15 onward | read once, after everything is frozen; the cut is fixed in `run-features-15m.json` |

Rules:
- W2 is read once; a re-read or a change after seeing it is a new decision.
- Nothing fitted or chosen on W2, W4 or W5 may be reused for the next
  window's selection.
- The predictor is scored against Binance. The Kalshi settlement index differs
  (the basis), which first appears in W4.
- The MIO itself is not finalized; W4 fixes only its tuning window.

Open: Aug 1 is where the live Kalshi API's history ends, not a verified
launch date. The archive pull (`kalshi_history`, runbook 7) may reveal earlier
15-minute markets. If it does, W2/W3's end and W4's start move to the first
Kalshi settled market, by owner decision, before W2 is read.

## Predictor target and zoo axes (owner direction 2026-10-09)

- Samples: every minute t, per asset. Target: vol-standardized log return of
  price from t to t+h, h = 1..15, one model, 15 outputs. At use time only the
  horizon to the live contract's close is read.
- The output head is a zoo axis beside the architecture: quantiles (pinball
  loss), parametric (Student-t) and mixture heads, scored on one common grid
  (CRPS, pinball, PIT calibration) per horizon on W1 walk-forward folds.
- Baselines in every comparison: vol-standardized empirical distribution and
  an EWMA-volatility Student-t. Beating the baseline is the bar.
- The settlement average and the index-Binance basis are not modelled here;
  they enter at W4.
