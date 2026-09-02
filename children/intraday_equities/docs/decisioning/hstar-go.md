# H* GO splits

**Status:** locked 2026-09-01 (revised 2026-09-01; pooled ŷ + per-series H). ADR-0058. Toolkit: `no_information_test` (ADR-0057).

H and L come from **one sliding walk-forward through 2025-11-30**. HPO may read through Feb 2026. **Nothing after 2026-02-28 is peeked.**

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

## Result (2026-09-02)

40/40 folds ran. Hash `b5967dff`. Mean `go_frac` **0.07** (14 folds with exactly one name; never two). Train/val Spearman IC **= 0** every fold (ŷ collapsed). GO counts: AAPL 7, WMT 4, JPM/XOM/LLY 1. Do **not** lock H. A0013.

## Next

Do not lock H or run L/TPE until ŷ ranks (IC ≠ 0). Book collapse still deferred. Confirm/backtest only after HPO refit on a GO.
