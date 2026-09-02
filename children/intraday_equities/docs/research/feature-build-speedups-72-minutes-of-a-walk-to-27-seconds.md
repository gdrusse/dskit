# Feature-build speedups: 72 minutes of a walk to 27 seconds

Date: 2026-09-02

## Question

A 40-fold walk took about three hours, and the feature node was 108
seconds of every fold. Where does the time go, and is any of it
avoidable without changing a number?

## Finding

Two changes account for essentially all of it, and both are numerically
exact.

**1. `_rolling_extrema` was a per-bar Python loop — 81% of the node.**
`dskit/pipeline/libs/numpy.py`. The monotonic deque is already O(n); the
cost was interpreter overhead, a boxed scalar and a ufunc NaN dispatch
per element, called ten times per symbol per fold. Replaced with a van
Herk / Gil-Werman block extremum: two `maximum.accumulate` passes over
the series reshaped into width-wide blocks.

| window | shipped | van Herk | speedup |
|---|---|---|---|
| 5 | 1.361s | 0.027s | 51x |
| 60 | 1.398s | 0.020s | 70x |
| 1170 | 1.394s | 0.028s | 50x |

Verified bit-exact over 384 cases: both directions, widths 1 to 1170,
NaN densities to 50%, every length around the window boundary, plus
all-NaN, zero and constant inputs. Zero mismatches, NaN placement
included. The shipped version being flat in width confirms the diagnosis.

**2. The feature node is fold-invariant and was rebuilt 40 times.** Its
inputs are the session records and the universe spec; nothing upstream
reads `$splits`. The log proves it — exactly 1,248,034 rows on every
fold. Now memoized on a signature of length, three sampled rows, spec
and params. The build must be cached on the **class**, not the instance:
the driver constructs fresh nodes per fold, so an instance attribute is
never read again. The outer list is copied out so downstream filtering
cannot corrupt the cache, while the arrays are shared.

| | before | after |
|---|---|---|
| feature node, fold 1 | 108.3s | 27.2s |
| feature node, folds 2+ | 108.3s | 0.000s |
| across 40 folds | 72 min | 27s |

**Equivalence.** Fold metrics are identical to every printed digit
across the first three folds before and after: train and val MSPE, train
and val IC, forecast spread, and row counts.

## Not taken

Three audits produced more than was applied. `dskit/pipeline/stats.py`
is Tier 1 and stdlib-only, stated in the module docstring, the root tier
table and ADR-0057, with numpy in the blocked-import list and a purity
test enforcing it. Its Newey-West Bartlett loop is O(n·lags) and is 99%
of `no_information_test`, but the fix is a pure-Python moving-sum
identity that is O(n+lags) and measures 166x at lags 233 — faster than
the numpy FFT alternative, so there is no argument for breaking the
tier. Also outstanding: 234 `model.predict` calls per series where one
would do, a training matrix built and discarded in the horizon walk at
roughly 16 GB of gather per symbol per fold, and
`cluster_bootstrap_pvalue` re-summing each drawn cluster where its own
sibling already hoists that sum.

## Sources

- `dskit/pipeline/libs/numpy.py` — `_rolling_extrema`
- `intraday_equities/nodes.py` — `SessionFeatureRows`
- `tests/pipeline_libs/test_numpy.py`, `tests/test_nodes.py`
