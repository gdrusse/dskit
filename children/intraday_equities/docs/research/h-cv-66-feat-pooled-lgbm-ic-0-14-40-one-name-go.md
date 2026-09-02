# ~~H* CV 66-feat pooled LGBM: IC=0, 14/40 one-name GO~~ — VOID

Date: 2026-09-02 (voided 2026-09-02)

> ### ⚠️ THIS WRITE-UP IS INCORRECT — DO NOT CITE
>
> Every number below came from a model that never split. `min_split_gain: 0.02` sat above any gain reachable at a 5-minute label variance of ~2.6e-06, so LightGBM built one leaf, ŷ was the train mean, and IC was 0 by construction on **train** as well as val. The finding is about a config bug, not about intraday predictability. The corrected record is in `docs/decisioning/hstar-go.md`.

## Question

Does one LightGBM (symbol category, 66 session+momentum features, inner-train HPO) reject no-information from h=5 per name on the 40-fold 2y slide through 2025-11-30?

## Finding

~~40/40 ran (`b5967dff`, A0013). Mean go_frac=0.07: 14 folds with exactly one name GO, never two. Train and val Spearman IC were 0.0 every fold — ŷ does not rank. GO counts: AAPL 7 (H 5–75), WMT 4 (H 5–1170), JPM/XOM/LLY 1 each. Mean p at h=5 ≈ 0.32–0.53. Inner HPO ran (hpo_mspe on every fold) and did not produce a ranking signal. Do not lock H or start L/TPE.~~

**Corrected.** The inner HPO could not have rescued anything: `min_split_gain` was absent from `hpo_space`, so all eight draws inherited the stump. ŷ having no rank variance is the entire reason IC printed as exactly 0.0000, and a constant forecast equal to μ drives the Clark–West adjusted gap to about 0, which makes the 14 one-name GOs noise rather than signal. Whether ŷ ranks at five minutes is still **unanswered**.

## Sources

- `configs/run-hstar-cv-series.json` (hash b5967dff)
- `pipeline_runs/intraday-equities-hstar-cv-series-walkforward-2025-11-30-b5967dff/`
- `docs/decisioning/logs/hstar-cv-series-b5967dff.out`
- fold `carry.json` scan metrics
