# Question

Does a LightGBM on the 66 session features plus one-minute `ret_lag_*`
for the last 20 minutes predict JPM's 5-, 10-, 15- or 20-minute forward
return better than that name's training mean? And does a widened
hyperparameter search (32 draws spanning depth 3 / 7 leaves /
`min_child_samples` 400 through depth 8 / 63 leaves / `min_child_samples`
50, scored on IC) land the model between overfitting and underfitting?

Config `configs/run-jpm-h20.json` (hash `1c02b7ca`), universe variant
`configs/universe-jpm-h20.json`. Twenty post-COVID folds declared, first
validation 2022-05-06, `step_days=63`, `val_days=63`, `train_days=730`,
`embargo_days=5`. **Stopped by the operator after fold 7 of 20**; there
is no walk-forward summary and no `execute` ledger row (see §3).

# Finding

**Answer to both: no.** Six of seven folds are negative, the mean
validation IC is **-0.008**, and no fold reached GO at any lead on the
grid. Train IC sits at 0.10-0.31 in every fold while validation IC sits
at zero. The search never escaped that gap even though its space reached
the low-capacity corner.

## 1. The fold table

| fold | inner-holdout IC | val IC | train IC | yhat_sd | n_train | n_val | GO |
|---|---|---|---|---|---|---|---|
| 1 | 0.0352 | **+0.0400** | 0.3047 | 3.05e-04 | 20913 | 1763 | 0/1 |
| 2 | 0.0487 | -0.0104 | 0.1742 | 1.32e-04 | 20871 | 1847 | 0/1 |
| 3 | 0.0290 | -0.0300 | 0.1948 | 1.44e-04 | 20871 | 1889 | 0/1 |
| 4 | 0.0092 | -0.0280 | 0.1037 | 7.63e-05 | 20871 | 1731 | 0/1 |
| 5 | 0.0059 | -0.0014 | 0.2616 | 3.22e-04 | 20907 | 1805 | 0/1 |
| 6 | 0.0431 | -0.0093 | 0.1294 | 7.45e-05 | 20907 | 1847 | 0/1 |
| 7 | 0.0369 | -0.0167 | 0.2263 | 2.50e-04 | 20975 | 1727 | 0/1 |

Mean val IC **-0.0080**; positive in 1 of 7.

**The forecast is not degenerate.** `yhat_sd` runs 7.5e-05 to 3.2e-04
against a label sd near 1.6e-03, and the constant-forecast guard passed
every fold. This is the first run in the H series where the model
demonstrably fits something — the `b5967dff` stump failure
(`min_split_gain` 0.02 against a 2.6e-06 label variance) is not present
and cannot explain these numbers.

## 2. What the seven folds establish, and what they do not

**Established: the tuning step is a random number generator.** Inner
holdout IC ranges 0.0059 to 0.0487 and carries no information about the
fold's validation IC. The best inner score (0.0487, fold 2) produced
**-0.0104**; the worst (0.0059, fold 5) produced **-0.0014**. Across the
seven pairs the relationship is flat to inverted. The inner holdout is
63 days — roughly 1,800 rows after the row loss in §4 — which cannot
rank 32 combinations at this signal-to-noise. **Thirty-two draws on that
holdout buy nothing but variance.**

**Established: capacity is not the knob.** The space reached down to 7
leaves, depth 3, `min_child_samples` 400, `reg_lambda` 20. Seven
independent searches over that space, and every winner still shows
train IC an order of magnitude above val IC. A model with ~20,900 rows
and ~86 columns finds 0.2 of in-sample rank correlation in noise; that
is a property of the fitting problem, not of a badly chosen setting.
Tightening the grid further will lower train IC without raising val IC.

**NOT established: that JPM is unpredictable at 5-20 minutes.** A mean
of -0.008 over seven folds is well inside noise. The minimum detectable
|IC| on a single ~1,800-row fold is about 0.059; on seven pooled, about
0.022. This run cannot see an effect smaller than that and did not test
one. What it rules out is *this feature set under this fitting
procedure*, which is a much narrower claim.

## 3. Toolkit gap: an interrupted walk-forward records nothing

`driver.py` calls `_journal_execute` for a walk-forward **only after**
`_write_walkforward_summary` (line 2400), and every per-fold
`run_document` is called with `journal=False` (line 1991) so the series
produces exactly one ledger row. Correct when a series completes. But a
series stopped part-way writes **N fold run directories to disk and no
`actions.csv` row at all** — seven directories here, `c1c5a727` the
last, none of them in the ledger.

This contradicts the principle stated in `run_document`'s own
docstring — *"from the moment the run dir is written, every outcome is
RECORDED"* — and it is exactly the failure mode ADR-0056 exists to
prevent: compute was spent, evidence was produced, and the ledger is
silent. The row for this note had to be written by hand.

**Not fixed here.** A dskit change needs an ADR first (ADR before code),
and a child may not edit dskit. Recorded as a gap.

## 4. Open: roughly half the expected rows are missing

`n_train` is ~20,900 against a 730-calendar-day window (~500 RTH days x
78 five-minute bars = ~39,000 expected). `n_val` is ~1,800 against ~3,350
expected for 63 calendar days. Both are **~54% of the calendar
implication**, and the ratio is stable across all seven folds.

The one-minute tape is complete — the RTH filter kept 6,240,600 of
8,885,389 records, about 1.04M per symbol, which matches full coverage
from 2016. So rows are dropped downstream of the tape, not missing from
it.

Leading hypothesis, **untested**: the `cross_session` momentum scales.
`1w` needs 1,950 continuous minutes and `2s` needs 780, against
`universe.max_gap_minutes` of 5. If a weekend or holiday gap invalidates
those columns, whole days drop. If that is the cause, two features are
costing half of every sample — a bad trade at this signal-to-noise, and
one that has silently applied to every run in this series.

Check before the next run: count emitted grid rows per weekday with and
without the two `cross_session` scales.

## 5. What to change before running this again

1. **Fix the row loss in §4 first**, or accept it knowingly. Half the
   sample is the largest single lever available and it costs nothing to
   recover if the cause is what §4 suspects.
2. **Drop the 32-draw search.** Either pin one deliberately regularized
   setting, or give the inner holdout enough rows to rank combinations.
   The present arrangement adds variance and selects nothing.
3. **Do not tighten capacity further** expecting the train/val gap to
   close. §2 says it will not.
4. **Finish the twenty folds** before reading a mean. Seven is a partial
   series, and this project has already voided one result read from a
   partial walk.

# Sources

## Repo

- `children/intraday_equities/configs/run-jpm-h20.json` — hash
  `1c02b7ca`; `hpo_trials` 32, `hpo_objective` ic, `features.lookback` 20.
- `children/intraday_equities/configs/universe-jpm-h20.json` —
  `lead_start` 5, `lead_step` 5, `lead_stop` 20 (four leads).
- `children/intraday_equities/pipeline_runs/intraday-equities-jpm-h20-wf-*`
  — seven fold run directories, `c1c5a727` last.
- `dskit/pipeline/driver.py` — `_journal_execute` (1640), the
  walk-forward call site (2400), `journal=False` per fold (1991).
- `dskit/pipeline/stats.py` — `no_information_test`,
  `max_informative_horizon`.
- `children/intraday_equities/intraday_equities/nodes.py` —
  `NoInformationScan`, `_tune_estimator` (the mspe-underfits docstring),
  `_walk_no_information_series`, `_DEGENERATE_YHAT_REL` guard.

## Prior notes

- `docs/research/l-and-h-selection-the-h-walk-measures-sample-size-not-horizon.md`
  — why the grid stops at 20 and why the walk was retired (A0035).
- `docs/research/post-covid-h-cv-bounded-window-no-measurable-edge.md`
  — the 20-fold two-name run this one narrows.
- `docs/research/three-declared-knobs-that-did-nothing-or-the-opposite.md`
  — the declared-vs-runtime family §3 joins.
- `docs/decisioning/hstar-go.md` — the `b5967dff` stump void and the
  standing "do not lock H".
