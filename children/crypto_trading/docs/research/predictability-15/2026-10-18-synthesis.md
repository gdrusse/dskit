## Question
Can 15 new chronological models predict 15-minute crypto settlement better than contemporaneous market probabilities, including specific lead/calibration cells?

## Finding
All 15 JSON pipelines completed on the same 137,737 forecasts from 28,373 Kalshi BTC/ETH contracts. Each saves prediction, contract, cell, lead, asset/lead and calibration outputs. Four methods improve overall beyond fitted market-only calibration in exploratory historical screens; none localizes an incremental gain to a cell after the 1,500-cell screen. Best new method is ARD residual, with log loss 0.000804 below raw market and 0.000583 below calibrated market. Reused history and sequential design preclude confirmation claims. Stop broad searching; preserve the existing frozen predictive confirmation ending January 15, 2027 UTC. No trading analysis or protected outcome read was performed.

## Sources
- ../../memos/2026-10-17-predictability-15-methods.md
- ../../memos/2026-10-17-predictability15-evidence.json
- /home/russell/data/crypto_trading/research/predictability15-20261017
- https://proceedings.mlr.press/v54/kull17a.html
- https://scikit-learn.org/stable/modules/linear_model.html
- https://lightgbm.readthedocs.io/en/latest/Parameters.html
