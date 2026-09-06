# H* GO splits

> **Historical calendar notice (2026-09-05).** ADR-0098 and
> `configs/program-calendar.json` supersede this document's 40-fold calendar
> for Gate 1, Gate 3, and the model zoo. Their locked development schedule is
> 20 folds beginning 2022-05-06 and ending 2025-10-16. This file remains the
> historical record of ADR-0058 and must not be used as the active date source.

**Status:** locked 2026-09-01 (revised 2026-09-01; pooled ŷ + per-series H). ADR-0058. Toolkit: `no_information_test` (ADR-0057).

H and L come from **one sliding walk-forward through 2025-11-30**. HPO may read through Feb 2026. **Nothing after 2026-02-28 is peeked.**

> ### ⚠️ READ BEFORE CITING THIS DOCUMENT
>
> **The Result section below is INCORRECT and is struck through.** The
> `b5967dff` walk measured a broken model, not the market. The splits,
> dataset and procedure above are still good; only the *result* is void.
> Do not quote its IC, its `go_frac`, or its GO counts as evidence about
> predictability. Do not use it to argue that intraday equity returns
> carry no signal at five minutes. That question is still open and
> still untested.

## Dataset

Alpaca SIP, `adjustment: raw`, RTH only, five tradable + SPY, 5-minute grid, tape from **2016-01-01**. Train label is the **5-minute** log return (`lead=5`). Features: the 46 non-lag session fields plus 20 engineered momentum/vol columns (66 total) — `mom` on the five universe scales, and `ret`/`rv`/`mom` at 3m, 2h, 3h, 2s, 1w. No `ret_lag_*`. `keep_features` (28) is unused until L. **One LightGBM**; symbol is a category. Short HPO (8 draws) on an inner train holdout; fold val is unread. Then walk `h` **per name**. Sequential `h*` at `α=0.05`. Book lock deferred (`docs/adhoc/deferred_decisions.md`).

## Walk-forward (H and L, same folds)

`walkforward`: `first=2019-01-07`, `step_days=63`, `count=40`, `val_days=63`, `embargo_days=5`, `train_days=730` (2y slide). Last val ends **2025-11-30**. Embargo is `lead_stop` (3 sessions → 5 calendar days), not H-length — H is what this pass picks.

| Fold | Train (slide 2y) | Val |
|---|---|---|
| 1 | 2017-01-02 → 2019-01-01 | 2019-01-07 → 2019-03-10 |
| 40 | 2023-09-25 → 2025-09-23 | 2025-09-29 → 2025-11-30 |

Cuts are the walk-forward driver's (`cutoff` UTC midnight, `embargo_days=5`, `train_days=730`). Fold 40 `test_end` is 2025-12-01 — December+ is unread.

**H (pass 1).** Per fold: short HPO on an **inner train holdout** (fold val unread), refit one pooled LightGBM (symbol category), walk h* **per tradable name** on that val. Five names → five H’s, one tree. Book aggregation deferred. `|IC|` is not a gate.

**L (pass 2).** Same 40 folds, label lead = locked H*. Grid `l_start=30` by 5 through `lookback_stop=120`. Pick the shortest L within 1 SE of the best mean fold MSPE (not |IC|). Do not write `universe.lookback` (stays 30, action length). Then keep: train-only importance on data through 2025-11-30.

`test_end_ms=1772323199000` (2026-02-28) on these documents so March+ is no split.

## HPO (through Feb 2026)

Train ≤ 2025-11-30 (same 1-day gap before Dec 2). Val = **2025-12-02 → 2026-02-28**. Frozen H / L / keep. ~50 TPE. T bakeoff `{1y,2y,3y,5y,all-prior}` is this window only. After the winner: **refit through 2026-02-28**, no search.

## Untouched (2026-03-01 →)

| Block | Dates | RTH days | Role |
|---|---|---|---|
| Confirm | 2026-03-01 → 2026-05-31 | 63 | Frozen `ŷ`, frozen `h*`. Reject no-information at `h=5` and at `h*`. No re-walk, no TPE. |
| Backtest | 2026-06-01 → 2026-08-31 | 64 | First path evaluation. Includes Test B (August). |
| Live | 2026-09-01 → | | After backtest. |

Fail confirm → do not open June–August.

## ~~Result (2026-09-02)~~ — VOID, INCORRECT

~~40/40 folds ran. Hash `b5967dff`. Mean `go_frac` **0.07** (14 folds with exactly one name; never two). Train/val Spearman IC **= 0** every fold (ŷ collapsed). GO counts: AAPL 7, WMT 4, JPM/XOM/LLY 1. Do **not** lock H. A0013.~~

**Why it is wrong (2026-09-02).** The run carried `min_split_gain: 0.02` in
`estimator_params`, while the 5-minute log-return label has variance near
**2.6e-06**. No split gain at that label scale can reach 0.02, so LightGBM
built a **single leaf** and ŷ was a constant equal to the train mean. A
constant forecast has no rank variance, which is the only reason Spearman IC
came out at *exactly* 0.0000 — on **train** as well as val, in all 40 folds. A
model that cannot fit 155k rows in-sample is broken, so no conclusion about the
market follows from it. The `go_frac` 0.07 is noise: Clark–West on ŷ = μ gives
an adjusted gap of about 0, so the p-values were effectively random.
Reproduced directly — same params, same label scale, one node and one unique
prediction; removing the knob alone gives 2900 nodes.

The knob appeared in no other config and was absent from `hpo_space`, so all
eight HPO draws inherited it and none could escape it.

**Fixed by:** `min_split_gain` removed from `run-hstar-cv-series.json`; the
scan node now reports `train_yhat_sd` / `val_yhat_sd` and **refuses** a fold
whose ŷ is constant, so this failure can no longer be recorded as a result.
Re-measurement runs as `run-hstar-cv-pair.json` (AAPL/JPM, same 40 folds).
**"Do not lock H" still stands** — not because the data failed, but because it
was never actually tested.

## Result (2026-09-02, current) — `0716701f`

**Supersedes the voided `b5967dff` entry above.** Config
`run-hstar-cv-postcovid.json`: AAPL/JPM, 20 folds, first validation
2022-05-06, a **real** 730-day training window, no fold touching COVID
in training or validation.

| | n | mean val IC | sd | t | positive |
|---|---|---|---|---|---|
| all folds | 20 | +0.0073 | 0.0277 | +1.18 | 11/20 |
| clean of the AAPL split | 18 | +0.0063 | 0.0291 | +0.91 | 9/18 |

GO in **4 of 20 folds, never both names**. Under the sequential rule at
α=0.05 there is no basis to lock H. A t of 0.91 is indistinguishable
from zero and the clean folds split nine-nine.

**Why the earlier numbers looked better.** The 40-fold pair walk
(stopped at 23) averaged +0.0479 over its first six folds on the same
names and estimator, but every one of its folds trained all-prior back
to 2016 because the node ignored `splits.train_start_ms`. Bounding the
window to the declared 730 days took the apparent edge from ~0.048 to
~0.006. **The fold table in the walk-forward section above describes a
window no run before `0716701f` actually used.**

Open and unresolved: stock splits are unadjusted in the raw tape (AAPL
2020-08-31 sits in folds 1-2 training); `LookbackScan` still trains
all-prior, so pass 2 would not be the same experiment. Full write-up:
`docs/research/post-covid-h-cv-bounded-window-no-measurable-edge.md`.
A0029/A0030.

## Next

Do not lock H or run L/TPE until ŷ ranks (IC ≠ 0) **on a run that passes the constant-ŷ guard**. Book collapse still deferred. Confirm/backtest only after HPO refit on a GO.
