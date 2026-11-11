# RE-ENTRY - index_options

Updated 2026-11-10 by GPT-6.1 Sol.

## Now

- **ADR-0313.** Candidate `a7c5f411` cleared two independent Terra reviews with zero Critical/Major. A0010 ids and spec digests are enforced in both template instantiation and weekly study validation. The synthetic hedged template fills and settles a forward leg, reconciles with the shared ledger, and verifies recorded replay. The author passed 154 focused tests without ambient `PYTHONPATH`.
- **Verification.** Frozen `b13b987c` and main `f94b1774` each had 38 failures and 8 errors in the directory sweep. The only branch-specific CLI test failure passes on `a7c5f411`. See the ADR-0313 closure evidence for the comparison.
- **Forecasts.** A0008's fixed_loc recipe `b10d67deebb5` is pinned through 2023 over 166 names. Forecasts and scores were produced under ADR-0298 and remain unopened. No 2024+ work.
- **A0010.** The ADR-0297 queue and nine formulations remain locked. Existing plain WSL runs used four formulations. Their scheduler receipts may still show running after the scheduler stopped, so use the result artifact to establish closure. The queue has never run a real parity unit.

## Next

1. Integrate the reviewed ADR-0313 candidate through the repository merge gate.
2. Use ADR-0313's ordered gates for any future diagnostics or research execution. The synthetic closure gives no empirical verdict.
3. For A0010, confirm the existing 2019Q4 and 2020Q1 WSL artifacts closed, then resume the prior handoff only after addressing storage and the worker parity prerequisite. The owner reviews the four selection quarters and records three picks before opening the holdout.

## Blocked / owner decisions

- Shared directory-suite failures remain open. ADR-0313's real diagnostics and arm runs still depend on the ordered gates and pre-registration.
- ADR-0297 worker parity, storage headroom and the plain WSL result import remain open. See the prior lane note in the archive for the storage and reuse handoff.

## Read first

- [ADR-0313 and independent closure evidence](../../../docs/architecture/decision-log.md#adr-0313--one-integrated-mio-build-calibrated-edge-in-the-objective-the-hedge-overlay-in-program-mode-and-optional-greeks-exposure-limits-attached-per-method-through-one-owner-approved-under-owner-autonomy-instruction-2026-11-09-cleared-by-the-independent-fable-code-grounded-review-at-ced8611)
- [Locked Path](decisioning/path.csv)
- [MIO queue runbook](plans/mio-parallel/RUNBOOK.md)

## Do not

- Edit the A0010 formulation specs, digests, or Path. Keep the ADR-0313 development seal at `2021-07-01`.
- Open protected economics, forecasts, scores or run logs. Keep 2022–2023 MIO economics sealed until the owner records the three A0010 picks. Do no MIO or data work on 2024+.
- Start a real ADR-0313 arm before its gates. Keep the WSL 6 GB and Windows 2 GB memory reserves, and the C: 3 GB free-space floor.
