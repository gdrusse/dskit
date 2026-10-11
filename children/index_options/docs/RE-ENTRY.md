# RE-ENTRY — index_options

Updated: 2026-10-10 by Claude Sonnet 5.5. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/index_options.md` (dskit repo).

## Now

- T2 (ADR-0259, `configs/run-forecast-dispersion-ic.json`, identity `1ad8d730…`) was
  RUN on 2025-12-31 from origin/main `d7d309d` and **REFUSED at `with_forecast`**:
  `row 351 ('ANET', '2024-11-05'): 'spot' is 405.12 on the stream but None in the
  table`. No IC, no t: T2 is neither passed nor failed. Memo:
  `docs/memos/2026-10-10-t2-dispersion-ic-refusal.md`; ledger A1131 (the run,
  `state=error`), A1132 (the finding).
- It is NOT the anticipated basis mismatch: the stream's close is real; the table side
  (`forecast_scale`, 99,225 saved curve rows) carries a null `spot` for that
  (symbol, quote_date). The count of such rows is unknown; the attachment stops at
  the first. Nothing was patched, re-banded or re-run.
- Environment: `/home/russell/dskit/.venv` is an editable install of the stale
  `wt/claude-sonnet-5-5`; run with `PYTHONPATH=<fresh worktree>` (same interpreter).
- Pass rule unchanged: `beyond_market` IC > 0 with t >= 2.5.
- Open small defect: `DiscreteCDFGrid.worst_expected_loss` HiGHS tolerance.

## Next

1. Owner decides how null-`spot` forecast rows are treated (expected for a saved curve
   with no as-traded close?). Any change (a drop filter on the forecast side, a census
   first) is a new pre-registered amendment to ADR-0259 and its config, recorded
   BEFORE the rerun, not a patch made under the result's eye.
2. Then rerun the same command; if T2 passes, the dispersion book (T5 S1) is the next
   build; if it fails, the premium program with the forecast as a risk filter.
3. T1, T3, T4, T5 are unbuilt. The 307-name corporate-action inventories: owner's call.

## Blocked / owner decisions

- The null-`spot` ruling above. The other refusals still possible further down the
  chain (`with_realized` spot, `max_absent_fraction` 0.1, `CurveMoments` not a
  restorable curve) have not been reached; each is a finding, none a bug to patch around.
- Closing the executable-clock timing gap still needs a full-expiry chain archive or a
  forward collection (not authorized).

## Read first

- `docs/memos/2026-10-10-t2-dispersion-ic-refusal.md`
- `docs/research/strategy-alternatives/2026-10-11-patchtst-edge-and-harvest.md`
- `configs/run-forecast-dispersion-ic.json` (its `notes` say what each node is for)
- `docs/architecture/decision-log.md`, ADR-0259

## Do not

- No 2026 data access, fits, acquisition, spending or deployment.
- Run `793013c0`, run v2 `a1c35e4b`, their backups, the model inventory and every lock
  stay unchanged. Do not launch any replay from this note.
- Do not tune rho, the parity tolerance, the moneyness band or `h_steps` against the
  result: T2's pass rule was pre-registered.
