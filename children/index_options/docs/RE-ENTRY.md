# RE-ENTRY — index_options

Updated: 2026-10-10 by Claude Fable 5.1. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/index_options.md` (dskit repo).

## Now

- Owner accepted the same-day average fill (the owned archive has no next-session
  bar). Replay v2 `a1c35e4b` (config `run-equity-condor-robust-backtest-v2.json`,
  identity `0135e9ff`, candidate `383a6af3`) ran clean: rho supported 10/10;
  nominal 1,259 trades net -$8,395 (the 131 original trades reproduce +$5,354.85
  exactly; the 1,128 previously refused solves -$13,750); all seven robust arms
  net negative in their two trading months; 115 `parity_failed` refusals above
  the frozen 1e-5 stand; the account study refuses on 42 non-positive decision
  reservations. Backup `condor-replay-v2-complete-v1` (22 files, sha256sums).
- Diagnostics: no code bug, MILP exact (primal/dual harness), oracle control
  +$1.87M. The research note shows the $396k claimed edge is 59% forecast drift
  above the market forward, dispersion and favourable prints; the short put is
  leveraged long stock.
- 2026-10-10: the performance-pilot overlays (`configs/pilot-*.json`, note
  `docs/research/distribution-modeling/2026-10-05-performance-pilots.md`) merged
  from `claude/index-options-perf-pilots`; their ledger rows are A1123-A1126
  (renumbered from A1051-A1054 at merge, ADR-0109).
- Open small defect: `DiscreteCDFGrid.worst_expected_loss` HiGHS tolerance
  (~6.7e-7/share at rho > 0).

## Next

The owner decides the selector/forecast direction (shrink toward the market
forward, OTM + wing cap) before any replay. Nothing runs until then.

## Blocked / owner decisions

- The direction decision above.
- Closing the executable-clock timing gap needs a full-expiry chain archive or a
  forward collection; neither is authorized (ADR-0258 census: 0 of 286,120
  contracts have a t' bar in the DTE 31-45 archive).

## Read first

- `docs/memos/2026-10-09-condor-executable-clock-result.md`
- `docs/research/distribution-modeling/2026-10-09-condor-selection-intuition.md`
- ADR-0258 (tail of `docs/architecture/decision-log.md`)
- `docs/plans/README.md` (the 7-step `workflow.json` runbook)

## Do not

- No 2026 data access, fits, acquisition, spending or deployment.
- Run `793013c0`, its backups, the model inventory and every lock stay unchanged.
- Do not launch any replay from this note; a replay is a separately authorized step.
