# Deferred decisions

Not ADRs. Close before the next lock that depends on them.

## Book H from per-series h* (intraday_equities)

**Open 2026-09-01.** Each tradable name gets its own sequential H (GO ⇒ that H). Clock-mean pooling is out. ŷ is one pooled LightGBM with a symbol category.

Still open: how five H’s become one book lock.

- **A.** Keep a map (per-name H; a name may be no-GO).
- **B.** One H = min of names that GO.
- **C.** One H = median of names that GO.

Do not run the Dec–Feb TPE, confirm, or write `label_lead` until this closes. Inner-fold HPO on train (8 draws) is not that TPE. CV document: `children/intraday_equities/configs/run-hstar-cv-series.json`.
