# H* CV 66-feat pooled LGBM: IC=0, 14/40 one-name GO

Date: 2026-09-02

## Question

Does one LightGBM (symbol category, 66 session+momentum features, inner-train HPO) reject no-information from h=5 per name on the 40-fold 2y slide through 2025-11-30?

## Finding

40/40 ran (`b5967dff`, A0013). Mean go_frac=0.07: 14 folds with exactly one name GO, never two. Train and val Spearman IC were 0.0 every fold — ŷ does not rank. GO counts: AAPL 7 (H 5–75), WMT 4 (H 5–1170), JPM/XOM/LLY 1 each. Mean p at h=5 ≈ 0.32–0.53. Inner HPO ran (hpo_mspe on every fold) and did not produce a ranking signal. Do not lock H or start L/TPE.

## Sources

- `configs/run-hstar-cv-series.json` (hash b5967dff)
- `pipeline_runs/intraday-equities-hstar-cv-series-walkforward-2025-11-30-b5967dff/`
- `docs/decisioning/logs/hstar-cv-series-b5967dff.out`
- fold `carry.json` scan metrics
