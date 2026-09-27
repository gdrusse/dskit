# Index-options signed-region modelability ranking (ADR-0187)

## Decision

Continue modeling variability; do not treat this study as authorization to
trade. Rank the 21 SPY/QQQ/IWM horizon cells by out-of-sample improvement in
the locked signed-region twCRPS over each cell's empirical baseline. Keep the
negative and positive regions separate when interpreting where the gain comes
from.

SPY is the strongest underlying overall, but its advantage is concentrated on
the positive side. IWM is the strongest downside target. QQQ has no average
downside improvement under the selected models. IWM 30-45 days is the most
balanced next research focus: it improves both sides, ranks first on downside
skill, and has the best raw combined twCRPS.

## Scope and metric

The 21 cells are three underlyings by seven horizons: 1, 2-3, 5, 7-10, 14,
21, and 30-45 days. The primary modelability score is

`100 * (empirical twCRPS - selected-model twCRPS) / empirical twCRPS`.

Higher is better. twCRPS is integrated over two signed standardized-return
regions, `[-2.5, -0.5]` and `[0.5, 2.5]`. No absolute-value transformation is
used. The combined score is the sum of those two integrals. A separate rerun
verified combined = downside + upside to absolute tolerance `1e-12` for every
selected model and empirical baseline.

## Ranked results

The model is the simplest zoo member not detectably worse under the locked
within-zoo comparison. Side scores are diagnostic decompositions of that
already-selected model; models were not re-selected by side.

| Rank | Cell | Selected model | Combined skill | Downside skill | Upside skill |
|---:|---|---|---:|---:|---:|
| 1 | SPY 14 | HAR+VIX | +2.988% | +0.667% | +5.107% |
| 2 | SPY 30-45 | HAR+VIX | +2.983% | -0.113% | +5.582% |
| 3 | SPY 5 | HAR+VIX | +2.785% | +1.137% | +4.416% |
| 4 | SPY 21 | HAR+VIX | +2.563% | +0.220% | +4.704% |
| 5 | SPY 2-3 | HAR+VIX | +2.472% | +1.127% | +3.819% |
| 6 | IWM 30-45 | HAR+VIX | +2.388% | +1.800% | +2.974% |
| 7 | SPY 7-10 | HAR+VIX | +2.385% | +0.543% | +4.139% |
| 8 | SPY 1 | HAR+VIX | +2.352% | +1.023% | +3.642% |
| 9 | IWM 14 | HAR+VIX | +2.165% | +1.048% | +3.299% |
| 10 | IWM 21 | HAR+VIX | +1.939% | +1.073% | +2.830% |
| 11 | IWM 7-10 | VIX | +1.938% | +1.298% | +2.619% |
| 12 | QQQ 30-45 | HAR+VIX | +1.911% | -1.193% | +3.821% |
| 13 | IWM 5 | VIX | +1.602% | +1.113% | +2.118% |
| 14 | IWM 1 | VIX | +1.479% | +1.062% | +1.908% |
| 15 | QQQ 14 | HAR+VIX | +1.474% | -0.043% | +2.622% |
| 16 | QQQ 5 | HAR+VIX | +1.458% | +0.653% | +2.189% |
| 17 | QQQ 21 | HAR+VIX | +1.430% | -0.981% | +3.114% |
| 18 | IWM 2-3 | HAR+VIX | +1.245% | +0.980% | +1.525% |
| 19 | QQQ 2-3 | HAR+VIX | +1.234% | +0.439% | +1.995% |
| 20 | QQQ 1 | HAR+VIX | +1.234% | +0.605% | +1.834% |
| 21 | QQQ 7-10 | HAR+VIX | +1.196% | +0.041% | +2.155% |

Underlying means make the asymmetry clear:

| Underlying | Combined skill | Downside skill | Upside skill |
|---|---:|---:|---:|
| SPY | +2.647% | +0.658% | +4.487% |
| IWM | +1.822% | +1.196% | +2.468% |
| QQQ | +1.419% | -0.068% | +2.533% |

The locked combined-region inference remains the formal result: all 21
selected models beat their empirical baselines significantly. The signed-side
decomposition is exploratory. It has no side-specific model selection or
multiple-testing inference, so individual side ranks are research priorities,
not confirmed edges.

## Execution evidence

### Data gate

From `children/index_options`:

```bash
python -m dskit.onboarding acquire \
  --source optionshist-chain --stream index_daily --mode backfill \
  --root ~/data/index_options/ob
```

The acquisition completed as
`20260927T144623Z-backfill-d5551fe3`, snapshot prefix `a10a162a`, with
19,568 records in 1.44 seconds and 180,048 KB peak RSS.

A bounded SPY 30-45 ChainQuoteRows scan read 1,573,064 rows in 5:50.46 at
3,288,940 KB peak RSS (about 3.14 GiB), below the 30-minute and 6-GB limits.
Its fingerprint was
`1c0f108547106b99259e09c4909faa18c6b20a65ca21a1f1082f2ef158fb6ca6`.

The dividend gate passed:

| Symbol | Daily rows | Non-ex-date zero | Ex-date positive | Null/missing |
|---|---:|---:|---:|---:|
| SPY | 6,570 | 6,465 | 105 | 0 |
| QQQ | 6,474 | 6,389 | 85 | 0 |

A null inside a window remains a hard refusal for entry.

### Walkforward and zoo

All 84 full candidate walks completed: 21 cells times empirical, VIX,
HAR+VIX, and LightGBM+VIX. BenchmarkCompare produced sealed evidence for all
21 zoo comparisons. The selected simplest-not-worse model was VIX for IWM 1,
IWM 5, and IWM 7-10; HAR+VIX was selected for the other 18 cells.

Zoo comparison manifest:
`pipeline_runs/adr0187-bounded-2026-09-27/zoo-comparisons/manifest.json`.

### Bounded HPO diagnostics

The exhaustive LightGBM plan was stopped because its observed runtime implied
about two days. The bounded replacement still covered all 21 cells and both
HPO documents: earliest and latest original folds only, an exhaustive
four-point HAR ridge grid, and three deterministic LightGBM trials sampled
from the 12-point grid with seed 4.

All 42 summaries passed their trial-count audit: 42 HAR fold searches and 42
LightGBM fold searches. Endpoint choices agreed for HAR in 5/21 cells,
LightGBM in 11/21, and both in 3/21. This is a stability diagnostic, not a
full per-fold HPO estimate.

Config manifest:
`pipeline_runs/adr0187-bounded-2026-09-27/config-manifest.json`.

### Signed decomposition

The diagnostic rerun produced 84 summaries: selected model and empirical
baseline for each of two signed regions across all 21 cells. It reused the
fixed zoo selections and removed condor, chain, and backtest nodes.

Configs and logs:
`pipeline_runs/adr0187-modelability-signed-2026-09-27/`.

### Evaluator

All six evaluator reports passed census checks. Their aggregate model net P&L
values were empirical -$31,625; VIX -$29,632; HAR+VIX -$23,074;
LightGBM+VIX -$28,063; always-HAR -$51,266; and implied-HAR -$31,213.
These figures are only end-to-end plumbing sanity checks: the option-price
proxy and trading assumptions are not decision-eligible, and this memo makes
no trading claim.

Evaluator manifest:
`pipeline_runs/adr0187-bounded-2026-09-27/evaluation/manifest.json`.

## Limitations

- Side rankings reuse models selected on the combined region and are not
  protected by side-specific multiple-testing inference.
- Bounded HPO checks only the first and last folds; parameter instability is
  visible and should not be read as a tuned production result.
- Modelability is predictive score improvement, not economic value. Costs,
