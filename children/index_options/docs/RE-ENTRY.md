# RE-ENTRY - index_options

Updated 2026-11-13 by GPT-6.1 Sol.

## Now

- ADR-0314 daily stock replay is frozen on main at `82bf83d2`; the operational v11 bundle and final queues were independently cleared by Luna and Terra. The stock sleeve uses saved option books and a daily next-open share hedge; it never re-solves options.
- All eight ADR-0307 Kelly 050/075 option-only baselines were reattested across 2019Q4, 2020Q1, 2021Q1 and 2021Q2. All eight stock arms were attempted under unchanged v11 policy: five completed with 218 fills total; 2019Q4-075 and both 2020Q1 arms are funding-infeasible at the exact settled-cash guard. They are not Z/time-outs.
- The owner comparison is [on D:](D:/dskit-stock-hedge-owner-20261113/owner-eight-comparison.html), SHA256 `a2d9162626e3eb651d9de0587d6d23430637f09b4cebd5726a8d0e19915e077f`. [Final verification](/home/russell/mnt/adr0313-research/daily-stock-4q/final-stock-eight-verification.json) SHA256 `5750b14fb6a7bf4497198fa134f096b568c893ca8b8dd25499cc81bbb0d61143` binds all eight receipts and 45 completed output files. The owner reported a separate Claude multistart2 hedge run was wrong; it shares the pinned hedge_overlay.py blob. The economic comparison is provisional until the cause and ADR-0307 impact are scoped.
- The final queue is terminal with no five-hour events or active lane worker. Its completion SHA256 is `3915bca7f3a835deadaa49ac5ad8ad07c22a62da44492a16a183d3b5d79496a7`; the transient OS exit remains independently unknown. No Claude process was stopped by this lane.

## Next

1. Scope the owner-reported Claude multistart2 hedge defect against the shared overlay and ADR-0307 baseline before treating the comparison as decision evidence. Preserve all pinned results.
2. Owner decides whether a separately named cash-feasible stock policy is worth proposing for the three infeasible arms. Any funding, cap or cost change needs an ADR, synthetic failing tests and independent Luna/Terra review.
3. Keep unrelated ADR-0313/0315 work in its own reviewed lane. Recheck WSL/Windows/C:/D: resources before any further compute.

## Blocked / owner decisions

- Unresolved shared hedge-overlay defect scope; funding policy for three infeasible stock sleeves; A0006 wording; placeholder hedge costs; parity-forward mark versus spot; hard-to-borrow unreadable window; origin_two shrinkage; E1b; gross-share hedge cost; stale test_configs manifest; and prior instantiator sign-off. This run decides none of them.

## Read first

- [ADR-0314 amendments](../../../docs/architecture/decision-log.md)
- [Locked Path](decisioning/path.csv)
- [Owner comparison](D:/dskit-stock-hedge-owner-20261113/owner-eight-comparison.html)
- [Final verification](/home/russell/mnt/adr0313-research/daily-stock-4q/final-stock-eight-verification.json)

## Do not

- Edit Path or the nine A0010 formulations; unseal 2022-2023 economics before the owner's three picks; use 2024+ data; view forecasts or scores.
- Use the provisional comparison as decision evidence until the shared hedge issue is scoped. Do not relabel a funding-infeasible arm as complete or Z, or infer OS exit zero from archived protocol completion.
