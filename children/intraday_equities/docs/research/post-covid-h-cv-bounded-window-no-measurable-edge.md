# Post-COVID H* CV, bounded window: no measurable edge

Date: 2026-09-02

## Question

With the stump bug fixed, the training window actually bounded to 730
days, and no fold touching COVID in training or validation, does the
pooled LightGBM rank 5-minute returns on AAPL and JPM?

## Finding

No, not measurably. 20/20 folds ran, hash `0716701f`, first validation
2022-05-06, last 2025-08-15 to 2025-10-16.

| | n | mean val IC | sd | t | positive |
|---|---|---|---|---|---|
| all folds | 20 | +0.0073 | 0.0277 | +1.18 | 11/20 |
| clean of the AAPL split (3-20) | 18 | +0.0063 | 0.0291 | +0.91 | 9/18 |

A t of 0.91 is indistinguishable from zero, and the clean folds split
exactly nine positive and nine negative. The sequential no-information
test rejected in **4 of 20 folds and never for both names**, so under
the ADR-0058 rule there is no basis to lock H.

**The comparison that matters is with the unbounded run.** The 40-fold
pair walk, stopped at 23 folds, averaged +0.0479 over its first six
folds on the same two names with the same estimator. The only material
difference is that it trained all-prior back to 2016 while this one
trains on the 730-day window the design specifies. Correcting the
window took the apparent edge from roughly 0.048 to roughly 0.006. Most
of what looked like signal was the model reading a decade of history
that ADR-0058 never sanctioned.

**Regime observation, offered as a hypothesis and not a result.** Four
validation windows carried a volatility dislocation — autumn 2022,
August 2024 (yen carry unwind), April 2025 (tariff shock), plus fold 3
at the October 2022 bottom. Each showed a validation MSPE spike and an
IC at or below zero, and each recovered after. Splitting on that gives
-0.0150 across the dislocation folds against +0.0160 across the calm
ones. **Do not treat this as measured.** The label was chosen partly
from validation MSPE, which is not independent of the outcome, the
group holds four folds, and folds 15 and 19 are calm windows that still
went negative. Testing it properly needs a volatility threshold fixed in
advance and folds not used to construct it.

**Overfitting is the competing explanation and was not ruled out.** Mean
training IC rose from 0.0817 over folds 1-6 to 0.1397 over folds 7-13
while validation IC improved far less, widening the train/val gap by
about 40%.

## Caveats

Two names only. The AAPL 2020-08-31 split is unadjusted and sits in the
training window of folds 1-2. `LookbackScan` still trains all-prior, so
pass 2 would not be the same experiment
(`docs/adhoc/deferred_decisions.md`). The walk-forward winner is
selected on `go_frac`, which takes two distinct values here, so the
declared winner carries no information.

## Next

Do not lock H. Do not start pass 2 until the `LookbackScan` window
decision closes. Decide the split re-acquisition
(`adjustment: split`) before any run intended to lock anything.

## Sources

- `configs/run-hstar-cv-postcovid.json` (hash `0716701f`)
- `pipeline_runs/intraday-equities-hstar-cv-postcovid-walkforward-2025-11-30-0716701f/`
- `docs/research/three-declared-knobs-that-did-nothing-or-the-opposite.md`
- `docs/research/unadjusted-stock-splits-in-the-raw-alpaca-tape.md`
