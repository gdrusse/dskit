# Deferred decisions

Not ADRs. Close before the next lock that depends on them.

## Book H from per-series h* (intraday_equities)

**Open 2026-09-01.** Each tradable name gets its own sequential H (GO ⇒ that H). Clock-mean pooling is out. ŷ is one pooled LightGBM with a symbol category.

Still open: how five H’s become one book lock.

- **A.** Keep a map (per-name H; a name may be no-GO).
- **B.** One H = min of names that GO.
- **C.** One H = median of names that GO.

Do not run the Dec–Feb TPE, confirm, or write `label_lead` until this closes. Inner-fold HPO on train (8 draws) is not that TPE. CV document: `children/intraday_equities/configs/run-hstar-cv-series.json`.

## HorizonScan and LookbackScan still train all-prior (intraday_equities)

**Open 2026-09-02.** `NoInformationScan` now accepts and applies
`splits.train_start_ms` (ADR-0050), so a declared `train_days` finally
bounds the fitted window. `HorizonScan` and `LookbackScan` carry the
identical three lines and were not changed, so both still filter
training with an upper cut only and train on everything back to the
2016 tape start.

Why it matters: L is picked by `LookbackScan` on the same forty folds as
H. If H is locked from a bounded window and L from an unbounded one,
the two passes are not the same experiment, and the ADR-0058 fold table
describes neither.

Evidence that the bound was inert: across 23 folds of the pair walk
`n_train` grew 62,368 to 142,170, monotone, +3,600 per fold. Observed
ratio 2.280 against 2.265 expected for an expanding window from 2016 and
1.000 for a true 730-day slide. After the fix `n_train` holds near
42,000 and the logged window is exactly 730.0 days.

- **A.** Thread `train_start_ms` through both nodes, matching the scan
  node, and re-run the H/L passes together.
- **B.** Leave them all-prior and amend ADR-0058 to say so, dropping the
  "2y slide" claim and the fold table.

Do not run pass 2 (L) or lock `universe.lookback` until this closes.
Research: `children/intraday_equities/docs/research/three-declared-knobs-that-did-nothing-or-the-opposite.md`.
