# Three declared knobs that did nothing, or the opposite

Date: 2026-09-02

## Question

The 40-fold H* series run `b5967dff` reported Spearman IC of exactly 0
on **train and val** in all 40 folds. Is that a fact about intraday
equity returns, or about the code?

## Finding

About the code, three separate times. All three are knobs the documents
declare and the runtime silently ignores or inverts. None produced a
warning.

**1. `min_split_gain: 0.02` made every tree a stump.** Only
`run-hstar-cv-series.json` set it. The 5-minute log-return label has
variance near 2.6e-06, so no split gain at that scale can reach 0.02.
LightGBM built a single leaf; the forecast was a constant equal to the
train mean. A constant has no rank variance, which is the only reason
`_pearson` returned exactly 0.0000. Reproduced with the same params and
label scale: 1 node and 1 unique prediction, against 2900 nodes with the
knob removed. The knob was absent from `hpo_space`, so all eight HPO
draws inherited it and none could escape. `b5967dff` measured nothing;
its `go_frac` of 0.07 is noise, because Clark-West on a forecast equal
to mu gives an adjusted gap of about 0.

**2. `train_days: 730` never reached the node, so training was
all-prior.** The walk-forward driver computes the left bound correctly
and stamps `train_start_ms` into every fold's resolved config
(ADR-0050). `NoInformationScan` neither declared nor read that param,
and `_scan_fold_stamped` filtered training with an upper cut only. So a
documented "2y slide" actually trained on everything back to the
2016-01-01 tape start, growing by one validation period per fold.
Measured over 23 folds: `n_train` went 62,368 to 142,170, monotone,
+3,600 each fold. Expected ratio under a true 730-day slide is 1.000;
under an expanding window from 2016 it is 2.265; observed was 2.280.
ADR-0058's fold table is therefore wrong about what ran: fold 40 trained
on roughly ten years, not two. This also means COVID and the AAPL split
never aged out of any fold after they first appeared.

**3. `subsample` is inert without `subsample_freq`.** LightGBM ignores
row bagging unless the frequency is positive, and `subsample_freq`
appears nowhere in the repository. Verified: predictions with
`subsample: 0.6` are bit-identical to `subsample: 1.0`, and differ only
once the frequency is set. Three configs declare it — `universe.json` at
0.8, the series and pair configs at 0.6 — so no model this child has
fitted has ever been row-bagged, while the documents claim it was.

## Fixed

`min_split_gain` removed. The scan node now accepts and applies
`train_start_ms`, refuses a bound at or past the cut, and logs the
fitted window on every fold so an inert bound cannot be silent again. A
degenerate-forecast guard raises when the forecast varies by less than
1e-8 of the label's own standard deviation, with a regression test that
reproduces the stump. `subsample` is left alone deliberately: enabling
bagging changes regularization and therefore results, which is a
modelling decision, not a bug fix.

## Sources

- `configs/run-hstar-cv-series.json`, `configs/universe.json`
- `intraday_equities/nodes.py` — `_scan_fold_stamped`, `NoInformationScan`
- `dskit/pipeline/driver.py` — walk-forward cut arithmetic
- `dskit/pipeline/base.py` — `train_start_ms` contract (ADR-0050)
- `docs/decisioning/hstar-go.md` — the voided result entry
- `docs/decisioning/logs/hstar-cv-series-b5967dff.out`
