# RE-ENTRY - index_options

Updated 2026-11-11 by GPT-6.1 Sol.

## Now

- **ADR-0313 Kelly research methods.** Landed at source HEAD `fb592daa`. Exact Kelly search supports a sealed replay hedge, point-mean calibration with a warm control, and the combined arm. Both Terra lenses reported zero Critical and Major findings. The final review also cleared the timing counters amendment.
- **Verification.** Post-merge checks passed 248 focused tests and 11 parity cases. The ledger CLI subprocess failure (`ModuleNotFoundError: dskit.pipeline.hedge_overlay`) reproduces on the baseline. Other previously documented shared suite failures remain open.
- **A0010.** The nine formulation specs and digests remain locked. The frozen stitched 2019Q4 base job has a running receipt. The successor queue requires each prior packet's attested complete close. ADR-0297 has yet to run a real worker parity unit.
- **Forecasts.** A0008's fixed_loc recipe `b10d67deebb5` is pinned through 2023 over 166 names. Forecasts and scores were produced under ADR-0298 and remain unopened.

## Next

1. Keep the frozen stitched 2019Q4 queue intact. Verify the inner attested close before successors. The owner reviews the four selection quarters and records three picks before opening the A0010 holdout.
2. Once authorized, prepare separate Kelly research and timing documents, registries and output roots. Use matched clocks, build and sources for the replay hedge, exact-search twin and no-search control.
3. Apply the ADR-0313 real-data and pre-registration gates in order. Synthetic tests establish parity. Timing and research outcomes remain unmeasured.

## Blocked / owner decisions

- Approval for real data, timing, attempts and new operational files is pending.
- ADR-0297 worker parity, storage headroom and plain WSL result import remain open. The prior lane notes retain the storage and reuse handoff.

## Read first

- [ADR-0313 and Kelly amendments](../../../docs/architecture/decision-log.md)
- [Locked Path](decisioning/path.csv)
- [MIO queue runbook](plans/mio-parallel/RUNBOOK.md)

## Do not

- Edit the A0010 formulation specs, digests or Path. Keep the development seal at `2021-07-01`.
- Open protected economics, forecasts, scores or run logs. Keep 2022–2023 MIO economics sealed until the owner records three picks. Do no MIO or data work on 2024+.
- Launch a real ADR-0313 arm before its gates. Keep the WSL 6 GB and Windows 2 GB memory reserves, and the C: 3 GB free-space floor.
