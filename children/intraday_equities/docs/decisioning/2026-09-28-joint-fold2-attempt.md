# Joint simulation Fold 2 attempt — 2026-09-28

## Disposition

The interrupted `joint-simulation-2026-09-28-36bec3d6` attempt completed
Fold 2 (`[2022-09-09, 2022-11-11)`) and began Fold 3. Its Fold 2 boundary
was flat, but its financial result is provisional and must not be presented
as the corrected backtest result: it used fill-policy digest
`4a5df9dfb31314f6dd75c573ea9c20a3ff9e264905140033f99d12d6af11e890`,
which priced the mandatory close exit at the bar open and derived the session
close from the last observed bar. Candidate `22d3689` replaces that policy
with the bounded XNYS calendar and close-price digest
`0ef3f05dfcd4d0a90511d830b9704779e84a48a253cc2c30993822cea890e5da`.

## Observed Fold 2 boundary

- Closing NAV/cash: `$10,949.3323185029935119126` (`$10,949.33`).
- Portfolio cost basis (contributed capital): `$12,500`.
- Open positions: none; open-security basis: `$0`.
- P&L versus portfolio cost basis: `-$1,550.6676814970065`
  (`-$1,550.67`).
- Reconstructed decision fills: `20,992`.
- Reconstructed evaluator-style maximum dollar drawdown: `$1,907.7359`
  (cumulative-P&L peak `$21.8617` on 2022-09-09 to trough
  `-$1,885.8742` on 2022-10-12).

The count and drawdown are monitor reconstructions, not completed evaluator
outputs: the simulation node buffers the official artifacts until the full
folds 2–19 node finishes, and this attempt was deliberately stopped during
Fold 3.

The Fold 2 boundary is evidenced by the next replay segment's first ledger
state: at `2022-11-11 09:30 America/New_York` it books a carry deposit of
`10949.3323185029935119126` USD and records `positions: {}`. The surviving
temporary evidence is
`/tmp/gate5-replay-7ckzo3yh/serve/8538ee3b-a93b-4fce-af65-c09decaed8e9/ledger/ledger.0001.jsonl`
(cash-flow row 5; first snapshot row 8).

## Timing

- Pipeline start: `2026-09-27 23:05:29.420 America/New_York`.
- Simulation start: `2026-09-27 23:10:36.915 America/New_York`.
- Last Fold 2 tick: `2026-09-28 02:09:30.185 America/New_York`
  (`2:58:53.270` after simulation start).
- Fold 3 replay creation, proving Fold 2 had returned:
  `2026-09-28 02:14:24.806 America/New_York`.
- Fold 2 therefore finalized within `3:03:48` of simulation time
  (`3:08:55` from pipeline start).
- The whole interrupted attempt ran `9:30:51`, including `9:25:44` in the
  simulation node, before it was stopped after the old policy was observed
  processing Black Friday post-close bars in Fold 3.

The pipeline log is
`pipeline_runs/joint-simulation-2026-09-28-36bec3d6/run.log`; it records the
pipeline and simulation starts. Because the process was stopped rather than
completed, it contains no final evaluation report.

The monitor reconstruction is preserved in the Codex rollout at
`C:\Users\russe\.codex\sessions\2026\09\27\rollout-2026-09-27T10-44-55-01a0e353-7eb6-7cf1-9db9-c43525a683c3.jsonl`:
line 12598 is the last Fold 2 tick, 12605 is its flat end-of-day
reconstruction, 12676 is the Fold 3 carry ledger, and 12713 applies the
three previously omitted `$500` deposits to the Fold 2 boundary figure.

Fold 3 remained diagnostically valid through the 2022-11-23 close
(`$12,065.14`, flat); 2022-11-25 and later state from this
attempt is invalid because the old policy continued after the 13:00 XNYS
close.

## Next action

Run the reviewed `22d3689` candidate end to end. Treat its Fold 2 NAV, P&L,
drawdown, trade attribution, and flat-close checks as the first reportable
result; the figures above are diagnostic evidence only.
