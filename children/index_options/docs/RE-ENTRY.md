# RE-ENTRY — index_options

Updated: 2026-10-28 by ChatGPT. Historical execution and stress-scope memo wrapped; prior note archived in docs/archive/re-entry/index_options.md.

## Now

- Standalone memo `docs/memos/2026-10-28-mio-execution-and-stress-scope.md` explains the eight ordinary 2019 runs, their fill/refusal and expiry accounting, what the 813 later pricing-only jobs did, and the missing stressed-execution replay. Memo candidate `96f8ce01`, factual correction `9d43f61d`.
- Two independent GPT-6 Luna final lenses on `96f8ce01`: no Critical/Major; one narrow Minor in the copyable CSV check was fixed in `9d43f61d`. The corrected read-only command was run and reproduced all eight method rows, 224 fills and 691 refusals. Four focused docs checks and diff checks passed; no full suite.
- Eight original MIO method results are complete with saved replay parity. A fresh read-only check matched filled/rejected IDs, next-session dates, capacity, final daily accounts and profit. This supports the ordinary historical path but is not a raw-quote reconstruction.
- The 813 new `stable_tail_v1` attempts only repriced already held books; they did not rerun selection, fills, cash or expiry settlement. Existing extra-spread results also keep the original fills. End-to-end execution under changed fill and expiry conditions remains **unrun**.
- Original 2019 results pins SHA-256 `2bbd7e9247bf46c016745dde8436e9a7394e770e52031f9024baebd049fbf7d2`; original data, new coverage pins, checkouts, bundles and private viewer remain preserved. Path A0003/A0008 and TDD are unchanged.

## Next

1. Draft an owner-approvable ADR for a fixed-selection execution-and-expiry stress replay using the memo's copyable prompt. Start with an exact unperturbed control for all eight runs; specify adverse fill-price/size and ±15% expiry shocks, account propagation, outputs and limits.
2. Stop for owner approval of exact scenario choices before code or any new run. After approval, use focused tests, two independent reviews and distinct pinned 2019 attempts.

## Blocked / owner decisions

- The proposed 25% adverse-spread magnitude, half-size capacity sensitivity, expiry shocks and joint cases are **not approved**; no stressed-execution run exists.
- The fixed-selection design would hold future selected orders constant while stressed cash/holdings diverge; it is conditional execution evidence, not an adaptively reoptimized strategy test.
- 2019 is development evidence. No 2020+ window, promotion or live trading is authorized.

## Read first

- docs/memos/2026-10-28-mio-execution-and-stress-scope.md — complete explanation, evidence, limitations and next-task prompt.
- configs/mio-2019-results-pins.json — original exact source/run/output pins.
- docs/memos/2026-10-28-mio-2019-comparison.md — the eight 2019 outcome runs.
- docs/memos/2026-10-28-mio-2019-stress-diagnosis.md — original and opt-in pricing stress pins.
- docs/architecture/decision-log.md — existing ADR-0278 context; a new ADR is needed for the proposed replay.

## Do not

- Present price-only stress as order-execution stress or an incomplete model mark as a failed fill.
- Rerun identical MIO/stress JSONs, invent IV or quotes, open 2020+, retrain, pull providers, resume optional broad hindsight or edit Path/TDD.
- Remove the viewer, original checkout, artifacts or data.
