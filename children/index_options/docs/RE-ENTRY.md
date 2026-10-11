# RE-ENTRY — index_options

Updated: 2026-10-11 by Claude Fable 5.1. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/index_options.md` (dskit repo).

## Now

- T2 of the edge-and-harvest note is BUILT, not run (ADR-0259 PROPOSED, built at
  the owner's instruction; ledger A1129, amended A1130):
  `configs/run-forecast-dispersion-ic.json` (identity `1ad8d730…`; validate and
  plan clean, 17 nodes). Per quote date, the partial Spearman across names of
  forecast sd with |realized 31-day log move| GIVEN the ATM straddle ratio,
  Newey–West t over the dates (`h_steps` 5), split by phase and by month;
  controls `forecast_alone` and `implied_alone` on the same rows. Inputs: the
  v2 replay's saved `condor-replay-inputs-20261009-v5` only; nothing fitted.
- AMENDED before any run (skeptic review): the note's ratio form
  (forecast/implied vs |move|/implied) is Pearson's spurious ratio correlation;
  a pure-noise forecast scored t = 7.2 under it on synthetic rows. The partial
  form asks the same "beyond the market" question without the artifact. The
  review also caught the attachments writing fields unprefixed (the first
  document would have scored nothing); fixed, dry-run proven.
- JSON alone could not express it; the generic additions in dskit:
  `CrossSectionIC` (kinds_stats, by import path) on `ordering.partial_spearman`
  and a `control` column, `CurveMoments` and `MixtureCurve.moments()`
  (predictive_cdf), `horizon-pairs` `symbol_field`, `TradeBarFeatures`
  `underlying_close`. Tests in the suites ADR-0259 names.
- Pass rule: `beyond_market` IC > 0 with t >= 2.5. Both attachments agree on
  `spot`; a disagreement REFUSES the run and is itself the finding (price
  basis), not an IC.
- Research note A1127/A1128, replay v2 `a1c35e4b` and its diagnostics stand
  unchanged (the archived note carries the numbers).
- Open small defect: `DiscreteCDFGrid.worst_expected_loss` HiGHS tolerance.

## Next

1. Owner runs T2 on the WSL host that holds `/home/russell/data/index_options/ob`:
   `cd ~/dskit/children/index_options && python -m dskit.pipeline run
   configs/run-forecast-dispersion-ic.json --asof 2025-12-31`; read `report.md`
   and the `report` node's `evidence.md` (edge section) in the run directory
   under `run_root`; record the outcome (`python -m dskit.journal record
   --category execute …`; a memo if durable).
2. If T2 passes: the market-anchored dispersion book (the note's T5 S1) is the
   next build, its own ADR. If it fails: the premium program with the forecast
   as a risk filter, and the forecast leaves selection.
3. T1 (P-vs-Q scorecard), T3, T4 and T5 are unbuilt; T1 needs the smoothed-smile
   market CDF as a reference, which no node supplies yet.
4. The 307-name corporate-action inventories: still the owner's call.

## Blocked / owner decisions

- The run itself (data on the WSL host). Refusals worth knowing beforehand:
  `with_forecast`/`with_realized` "sources disagree" on `spot` = basis mismatch;
  `max_absent_fraction` 0.1 exceeded = over 10% of straddle rows without a
  forecast or a pair; `CurveMoments` "not a restorable curve" = a saved family
  other than mixture/student_mixture. Each is a finding; none is a bug to patch
  around.
- Closing the executable-clock timing gap still needs a full-expiry chain
  archive or a forward collection (not authorized).

## Read first

- `docs/research/strategy-alternatives/2026-10-11-patchtst-edge-and-harvest.md`
  (the pre-registered tests; T2)
- `configs/run-forecast-dispersion-ic.json` (its `notes` say what each node is for)
- `docs/architecture/decision-log.md`, ADR-0259
- `docs/memos/2026-10-09-condor-executable-clock-result.md`

## Do not

- No 2026 data access, fits, acquisition, spending or deployment.
- Run `793013c0`, run v2 `a1c35e4b`, their backups, the model inventory and
  every lock stay unchanged. Do not launch any replay from this note.
- Do not tune rho, the parity tolerance, the moneyness band or `h_steps`
  against the result: T2's pass rule was pre-registered.
