# Joint simulation Fold 2 result — 2026-09-28

## Disposition

Candidate `22d3689` completed Fold 2 (`[2022-09-09, 2022-11-11)`) in run
`joint-simulation-2026-09-28-4ed41813` and handed its flat portfolio to
Fold 3. The run was deliberately stopped during Fold 3 after live monitoring
exposed an early-close replay clock/feed defect. Fold 2 ends before the
affected 2022-11-25 boundary and remains valid. The full folds 2–19 simulation
must restart from clean state on corrected candidate `260d97ed`; the official
ADR-0183 evaluator remains pending until that simulation publishes its
buffered artifacts.

The Fold 2 result uses fill-policy digest
`0ef3f05dfcd4d0a90511d830b9704779e84a48a253cc2c30993822cea890e5da`,
which prices the mandatory session-close exit at the close and obtains each
close from the bounded XNYS calendar.

## Fold 2 result

- Closing NAV/cash: `$10,444.5118916454190048244` (`$10,444.51`).
- Portfolio cost basis (cumulative contributed capital): `$12,500`.
- P&L versus contributed capital: `-$2,055.4881083545809951756`
  (`-$2,055.49`).
- Open positions: none; open-security basis: `$0`.
- Reconstructed decision fills: `20,933` across 45 sessions.
- Refused ticks: zero; no oversells or negative-cash states were observed.
- Every session ended flat. On the final session, 2022-11-10, the portfolio
  was flat by 15:56 America/New_York and gained `$142.13` versus the prior
  session's close.
- Reconstructed daily-close maximum dollar drawdown: `$2,219.478935659970`
  from cumulative-P&L peak `$21.861724330825` on 2022-09-09 to trough
  `-$2,197.617211329145` on 2022-11-09.

The fill count and daily-close drawdown are live monitor reconstructions, not
completed evaluator outputs. The official evaluator can differ from the
daily-close drawdown if its configured curve includes a deeper intraday mark.

The Fold 2 boundary is independently evidenced by Fold 3's first replay
ledger state. At `2022-11-11 09:30 America/New_York` it books a carry deposit
of `10444.5118916454190094947` USD and records no positions. The small final
decimal difference is serialization at the fold boundary; it does not affect
the cent-level result.

## Timing

- Pipeline start: `2026-09-28 09:59:33.670 America/New_York`.
- Simulation start: `2026-09-28 10:04:48.990 America/New_York`.
- First Fold 2 solve: `2026-09-28 10:05:06.354 America/New_York`.
- Last Fold 2 solve: `2026-09-28 13:05:43.082 America/New_York`.
- Fold 3 replay creation, proving Fold 2 had returned:
  `2026-09-28 13:12:08.803 America/New_York`.
- Fold 2 solve span: `3:00:36.728`.
- Simulation-start to Fold 3 handoff: `3:07:19.813`.
- Pipeline-start to Fold 3 handoff: `3:12:35.133`.

The pipeline log is
`pipeline_runs/joint-simulation-2026-09-28-4ed41813/run.log`. The run used the
18 GiB address-space cap and one fold worker; the joint Pyomo/HiGHS model was
persistent and used warm starts when the solve topology was unchanged.

## Operational observation

Late-session trades were allowed only on paths truncated to the minutes left
before the calendar close. Some positions were sold by the optimizer before
the final minute and some reached the close backstop. All 45 Fold 2 sessions
were liquid by the close, with no execution refusal or portfolio-limit breach.
This validates the operational contract, not economic quality: the official
evaluator's attribution remains the authority after the full run completes.

## Early-close incident and correction

Fold 3 remained valid through 2022-11-25 12:59 America/New_York and was flat
there at `$11,254.039256` NAV on `$13,000` contributed basis. The raw source
also contained 75 bars after Black Friday's 13:00 close. The calendar correctly
refused those ticks, but their unconsumed replay-feed records then lagged the
Monday cadence: the Monday 09:30 tick consumed the Friday 13:00 feed record.
That stale clock rejected 63 Monday proposals after tentative private book and
cash mutation, and a later accepted sale used the phantom position.

Candidate `260d97ed` removes calendar-closed bars from the session-share tape
and refuses before a subsequent decision whenever a prior proposal lacks an
execution acknowledgement. Both regressions fail on parent `2fa9645b` and
pass on the candidate. All 195 replay tests pass, the joint config validates,
and two fresh Terra skeptic lenses closed at C0/M0. One classification-only
minor is deferred: a symbol represented solely by a closed raw bar reports
`unknown_symbol` rather than `calendar_closed`; it still cannot trade.

## Superseded attempt

The earlier interrupted run `joint-simulation-2026-09-28-36bec3d6` reported
provisional Fold 2 NAV `$10,949.33` and P&L `-$1,550.67`. Do not use those
figures: that attempt used the superseded fill-policy digest
`4a5df9dfb31314f6dd75c573ea9c20a3ff9e264905140033f99d12d6af11e890`,
which used the bar open for the mandatory exit and inferred session close from
the last observed bar.

## Next action

Restart folds 2–19 from clean state on `260d97ed`, run the ADR-0183 evaluator
on the published artifacts, and replace the reconstructed drawdown and
attribution with the official report while retaining this Fold 2 boundary as
an operational audit.
