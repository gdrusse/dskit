# RE-ENTRY — index_options

Updated: 2026-10-10 by Claude Sonnet 5.5. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/index_options.md` (dskit repo).

## Now

- **T2 FAILED its pre-registered rule** (ADR-0259, amendment 2; run `a3f9db74`, config
  `c3a202f0…`, 18 nodes): `beyond_market` IC 0.033, t 1.15, 91 stamps (needs t >= 2.5).
  Controls: `forecast_alone` 0.411 (t 22.0), `implied_alone` 0.453 (t 29.2): the forecast
  orders names well, the market orders them better, and the forecast adds nothing
  demonstrated beyond it. By phase: calibration -0.014 (t -0.36), entry 0.086 (t 2.20).
  Memo `docs/memos/2026-10-10-t2-dispersion-ic-result.md`; ledger A1131-A1135.
- The first run REFUSED (null `spot` on a saved forecast row). Census: 80.7% of saved
  forecast rows have null `spot` by the replay's basis gate. Fix before any IC: counted
  `forecast_priced` filter; the 0.1 absent guard and the pass rule unchanged (155 of 5,723
  straddle rows absent). Scored on basis-reconciled names only.
- Environment: `/home/russell/dskit/.venv` is an editable install of the stale
  `wt/claude-sonnet-5-5`; run with `PYTHONPATH=<fresh worktree>`.
- Open small defect: `DiscreteCDFGrid.worst_expected_loss` HiGHS tolerance.

## Next

1. Owner reads T2: per the pre-registered branch, the premium program with the forecast
   as a risk filter; the forecast leaves selection; the dispersion book (T5 S1) is not
   justified by this test.
2. T1 (P-vs-Q scorecard), T3, T4, T5 are unbuilt; T1 needs a smoothed-smile market CDF.
3. The 307-name corporate-action inventories (they would widen T2's basis-reconciled
   sample): still the owner's call.

## Blocked / owner decisions

- Whether to act on the T2 failure, and whether to widen the sample via the inventories.
- Closing the executable-clock timing gap still needs a full-expiry chain archive or a
  forward collection (not authorized).

## Read first

- `docs/memos/2026-10-10-t2-dispersion-ic-result.md`
- `docs/research/strategy-alternatives/2026-10-11-patchtst-edge-and-harvest.md`
- `docs/architecture/decision-log.md`, ADR-0259 (amendments 1 and 2)
- `configs/run-forecast-dispersion-ic.json`

## Do not

- No 2026 data access, fits, acquisition, spending or deployment.
- Run `793013c0`, run v2 `a1c35e4b`, their backups, the model inventory and every lock
  stay unchanged. Do not launch any replay from this note.
- Do not rerun T2 hoping for a different answer or tune the band, `h_steps` or any
  threshold; the rule was pre-registered and it failed.
