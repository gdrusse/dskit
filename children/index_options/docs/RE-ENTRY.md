# RE-ENTRY — index_options

Updated: 2026-10-27 by GPT-6 Luna. Previous note archived in docs/archive/re-entry/index_options.md.

## Now

- Owner-authorized full-calendar-2019 weekly MIO warm-up is running on reviewed code candidate 6b97ede9205996fd1893f4bb3b12cd5a41284896. All 116 supported ticker-DTE pairs, DTE 1–60, four methods and existing M53 configuration remain fixed. Intake indexed all 116 pairs in 176.924 seconds; the method-start event is present, but no decision or return event is reported yet.
- Fresh SQLite design reviews: correctness and integration both C0/M0/m0/N0. Focused evidence: 119 protocol/index tests, 86 weekly tests and exact manifest passed per the retained integration report.
- Import completed once at registry revision 31 and the full-year development packet is open. The original helper receipt-shape failure is retained; metadata-only recovery verified the saved ordinary control/carry and current registry effect without repeating import.
- Worker: dskit-mio-2019-20261027.service, invocation b24240e192d74a37bed11357a3f0bfa6; run a8d4295ab7945cbc47d7b7a7f4ea703e322ad15c07d5fe3ed94d71f59a129b70, document aa41e9a69f1fa0abf7fdf87bb55003b48f87bfe8ce7c9beb8d6c78af52eb0219. Live status is in external execution-pins.json, events and worker.log.

## Next

1. Monitor the existing worker only. After all methods complete, inspect the full event stream and retained run artifact before comparison or explicit review.
2. Record measured resource use and verified solve/return summaries; update the TDD only after actual results are inspected and owner-authorized.

## Blocked / owner decisions

- Comparison and review are not authorized until the running methods complete and their result artifacts are inspected. No outcome or performance conclusion is available yet.

## Read first

- docs/architecture/decision-log.md — ADR-0278 implementation, reviews, runtime launch and helper recovery evidence.
- configs/mio-warmup-pins.json — tracked launch snapshot; external execution-pins.json is live mutable worker state.
- children/index_options/README.md — MIO packet interface and limits.

## Do not

- Change production code or the five frozen study/source JSONs while the worker runs.
- Rerun the import, repeat the study, start comparison/review early, or alter Path/TDD before actual results are inspected.
- Treat the work as predictive selection, promotion, a holdout, or a profitability claim.
