# RE-ENTRY — index_options

Updated: 2026-10-28 by ChatGPT. One-day mark/IV coverage packet reviewed and executed; prior note archived in docs/archive/re-entry/index_options.md.

## Now

- Opt-in `stable_tail_v1` pricing and 2019 stress account-coverage driver built at candidate `5df492c4` from main `6f1b667b` under owner-approved ADR-0278 amendment. Legacy pricing and the original 2019 eight MIO/stress runs are unchanged.
- Two independent GPT-6 Luna final reviews: C0/M0; disclosed Minor2 (no separate stage regression test, manual started-attempt recovery) and Nit1. Focused tests 202 passed/6 skipped, docs 4 passed, lint clean; no full suite.
- Distinct opt-in JSON pipelines completed 813/814 original envelope cases: 232 limited and 581 broad. The one RIOT far-tail mark stays refused; all 335 zero-IV cases stay unavailable. New attested attempts and account/book JSON+CSV are pinned under `/home/russell/data/index_options/mio-broad-2019-20261027/stress-coverage-v2`.
- Complete active common-horizon rows rose 588→915 of 1,124; 209 remain incomplete (208 IV, one RIOT). All 588 previously complete active rows and the $116,535.08 three-book June 17 loss are unchanged. This is diagnostic coverage, not a risk pass.
- Original results-pins SHA-256 `2bbd7e9247bf46c016745dde8436e9a7394e770e52031f9024baebd049fbf7d2` and original task-index/CSV hashes were checked unchanged. Original data, checkouts, bundles and private viewer were preserved.

## Next

1. With the owner, decide whether a one-day mark admission policy is needed. It needs a separate loss denominator/limit, held-only breach handling and timing-attested IV before a build or 2019 MIO variant.
2. Resolve the 47 source zero-IV quote semantics from an authoritative source before any replacement-input policy. Incomplete rows cannot yield a maximum-loss claim.

## Blocked / owner decisions

- No one-day mark admission threshold or new 2019 MIO variant is approved. The existing opt-in `filled_account` terminal gate is built but has not been run on these 2019 accounts.
- Path A0003/A0008 and TDD remain unchanged; the owner will update TDD together.
- The 2019 results are development evidence; limited cells were selected retrospectively, and model-zoo predictions include a trained-MLP fallback.

## Read first

- docs/memos/2026-10-28-mio-2019-stress-diagnosis.md — updated findings and exact original/new JSON/CSV pins.
- docs/memos/2026-10-28-mio-2019-comparison.md — eight original runs and execution semantics.
- configs/mio-2019-results-pins.json — original results/source pins.
- docs/architecture/decision-log.md — ADR-0278 mark/IV amendment.
- index_options/mio_stress_coverage.py — opt-in coverage driver.

## Do not

- Invent IV, widen legacy pricing, duplicate original MIO/stress attempts, open 2020+, retrain, pull providers, resume optional hindsight or edit Path/TDD.
- Treat 209 incomplete rows, complete-case maxima, the terminal gate or the June 17 mark as a passing one-day risk bound.
- Remove the viewer, original checkout, artifacts or data.
