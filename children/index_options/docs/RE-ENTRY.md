# RE-ENTRY - index_options

Updated: 2026-10-28 by GPT-6 Astra. The 2019 fixed-selection execution and expiry replay is complete; previous note archived.

## Now

- ADR-0280's seven-case contract ran for four saved methods in each of limited36 and broad212: 56/56 cases complete, 252 daily sessions each. New output: /home/russell/data/index_options/mio-execution-stress-2019-20261028-v1.
- Eight zero-shock controls exactly matched pinned selections, fills, refusals, prices, fees, daily account rows and profits; original census 915 selected, 224 filled, 691 refused. Stress output has 14,112 daily rows and 6,405 selected-book dispositions.
- Every case has a readable daybook showing possible books/contracts, selected orders, next-session fills/refusals, expiry profit or loss, cash and equity. report.md links all daybooks; summary.csv, daily.csv, dispositions.csv and pins.json provide machine-readable evidence. All 121 output-file pins and account reconciliations passed.
- Candidate 1035e1c812c203851f71e1d675dc6ac3b186f366 had 14 focused tests pass and two independent final skeptic lenses, each C0/M0/m0/n0. Full suite deliberately unrun. New pins.json SHA-256 391206b77fb7a1e3c75f26873c6da4d6a9069f4af3c4f2c667e8faed2d84cedc.
- Original data, eight results, viewer, Path A0003/A0008 and TDD remain intact. This is 2019 development evidence; saved future selections are fixed while stressed cash and eligibility change.

## Next

1. Read the execution memo and report. Interpret profit together with filled/selected counts, refusals, expiry losses and drawdown; the joint downside is not necessarily the worst result for a mixed option book.
2. Any adaptive reselection, coherent market path or 2020+ validation needs its own authorized design and run.

## Blocked / owner decisions

- No blocker remains for this 2019 replay. This run does not authorize 2020+, live execution, promotion or a new scenario.
- End-of-day bid/ask quotes are execution proxies; pointwise 15% expiry outcomes are conditional shocks, not predictions.

## Read first

- docs/memos/2026-10-28-mio-execution-expiry-stress-replay.md - run contract, verification, results and limits.
- /home/russell/data/index_options/mio-execution-stress-2019-20261028-v1/report.md - all 56 cases and daybooks.
- configs/mio-2019-execution-stress.json - exact seven-case input and source pins.
- configs/mio-2019-results-pins.json - original eight-run evidence identity.
- docs/architecture/decision-log.md - ADR-0280.

## Do not

- Treat price-only held-book repricing as this execution replay, or a selected but refused order as filled.
- Open 2020+, retrain, pull providers, run live trading, reselect under stress, or edit Path/TDD without a separate decision.
- Remove the viewer, original checkout, artifacts or data.
