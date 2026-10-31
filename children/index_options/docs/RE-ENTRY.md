# RE-ENTRY - index_options

Updated: 2026-10-31 by GPT-6 Astra. ADR-0288 split-aware Q2 replay code and source are reviewed; market execution waits for physical disk space.

## Now

- ADR-0288 (owner approved) maps TTD's 2021-06-17 OCC 10:1 option split across the shared ledger, funded account, shock, marks and settlement. Original entry credit, fees and contract counts stay audited; active terms convert before daily activity. Nonstandard events refuse.
- Frozen candidate `85e9187e` from base `978d72fa`; economic code snapshot `e5a01d9d` at `/home/russell/data/index_options/mio-2021q2-split-aware-20261031/code4`. Source registration and a distinct exploratory Q2 run document are under `children/index_options/configs/mio-2021q2-top3-frozen2019-20261030/`.
- OCC memo #48744 PDF is onboarded with SHA-256 `c30f23c4...`; checked event `426cb918...`, source manifest `2629156d...`. Pinned archive proves June 16 old strike 610/factor 0.1, June 17 live strike 61/factor 1.0 and unquoted old 610.
- Candidate plan `60d71e21...` is runnable under code4; 402 focused tests and diff check passed. Independent final lenses `q2_final_correctness` and `q2_final_integration`: C0/M0/minor0 each. Actual Q2 market replay has not run.
- Shared exposure registry revision 102, active none. An unused readiness freeze advanced revision 101 to 102; refresh/freeze immediately before any new claim. Previous Q2 claim `aa3fab17...` remains `abandoned_exposed`; original failed run is immutable.
- ADR-0285 storage fix remains on main: about 34 GiB of duplicate SQLite indexes deduplicated inside WSL, future indexes reuse verified cache. Windows `ext4.vhdx` remains about 870 GB; C: has about 3.3 GiB free.

## Next

- When Administrator access and recorder shutdown are available, offline compact the WSL VHDX and verify Windows C: free space. Do not interrupt the 24/7 pmquant recorder just to compact.
- Recheck matching processes and registry, then launch only the new split-aware Q2 development document from code4 with adequate Windows headroom. Verify attested close receipt, all three methods, replay parity, cash/fees/reserve and report each account separately.

## Blocked / owner decisions

- Owner cannot approve UAC now. Physical VHDX compact and the Q2 run remain pending; no development claim or new market read was made in this handoff.
- Ruff reports seven pre-existing D102/E731/E702 findings in touched files; no new lint defect was identified.

## Read first

- ../../../docs/architecture/decision-log.md — ADR-0288 and its implementation evidence.
- ../configs/mio-2021q2-top3-frozen2019-20261030/run-mio-top3-2021q2-split-aware-exploratory-20261031.json — new Q2 document.
- ../../../docs/skills/wrap.md and ../../../docs/skills/mio-report.md — integration and MIO reporting workflow.
- ../../../docs/archive/re-entry/index_options.md — previous storage/Q1 handoffs.

## Do not

- Resume old Q2 queue or claim, repeat Q1 or 2019 fits, rewrite source/failed evidence, read protected 2024+ data, or force WSL sparse conversion.
- Change Path A0002/A0003/A0008, other sessions' worktrees, or the live recorder without a safe shutdown window.
