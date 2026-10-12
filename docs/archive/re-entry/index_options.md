# RE-ENTRY archive — index_options

Previous re-entry notes for this lane, newest first. `/wrap` moves the note it
replaces to the top of this file; nothing is ever edited or removed here. Not
read at session start. Older history: `2026-10-10-consolidated-history.md`.

---

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

---

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

---

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

---

# RE-ENTRY — index_options

Updated: 2026-10-11 by Claude Fable 5.1. Rewritten whole at every `/wrap`; the note
this replaced is at the top of `docs/archive/re-entry/index_options.md` (dskit repo).

## Now

- Research note recorded (A1127, edited A1128):
  `docs/research/strategy-alternatives/2026-10-11-patchtst-edge-and-harvest.md`.
  Its answer: the forecast's measured edge is +0.28% weighted CRPS over an
  unconditional raw-return reference (2024-25; 185/392 names; ticker skill
  does not persist, rank corr 0.07); against the market every measured
  component is premium or error (drift +1.9%/month = one pooled equity
  premium, slope 0.15 +/- 0.21; scale 0.89x the ATM straddle where realized
  moves were 1.02x implied; puts valued at 0.62x market = the put premium;
  shape residual +$4.6k of a $396k claim). The condor harvests exactly those,
  and every replay trade was on the original-85 cohort where evaluation skill
  is +0.11/+0.12/-0.11% by seed; the restored 307 (+0.33%) has no price basis.
- Replay v2 `a1c35e4b` stands as the last run: nominal 1,259 trades net
  -$8,395, all seven robust arms negative; diagnostics found no code bug
  (MILP exact, oracle control +$1.87M). Backup `condor-replay-v2-complete-v1`.
- 2026-10-10: the performance-pilot overlays (`configs/pilot-*.json`) merged;
  ledger rows A1123-A1126.
- Open small defect: `DiscreteCDFGrid.worst_expected_loss` HiGHS tolerance
  (~6.7e-7/share at rho > 0).

## Next

The owner decides, in this order:
1. Run the note's pre-registered tests T1-T5 on the saved v2 evidence
   (compute only; no fits, no 2026 access): the P-vs-Q scorecard, the scale
   and direction information coefficients, the earnings split, four frozen
   paper books (market-anchored dispersion book, gated short-only, the
   nominal condor as control, an unconditional put spread as premium control).
2. Whether to acquire corporate-action inventories for the restored 307 names,
   the only cohort with measured skill and the one that cannot be priced.
3. Direction after T1/T2: a market-neutral dispersion book (if scale carries
   cross-sectional information) or the premium program with the forecast as a
   risk filter (if not). No condor replay with the nominal objective either way.

## Blocked / owner decisions

- The three decisions above. Nothing runs until one is taken.
- Closing the executable-clock timing gap still needs a full-expiry chain
  archive or a forward collection (not authorized).

## Read first

- `docs/research/strategy-alternatives/2026-10-11-patchtst-edge-and-harvest.md`
- `docs/research/distribution-modeling/2026-10-09-condor-selection-intuition.md`
- `docs/memos/2026-10-09-condor-executable-clock-result.md`
- `docs/research/strategy-alternatives/2026-09-30-synthesis.md`

## Do not

- No 2026 data access, fits, acquisition, spending or deployment.
- Run `793013c0`, run v2 `a1c35e4b`, their backups, the model inventory and
  every lock stay unchanged. Do not launch any replay from this note.
- Do not tune rho, the parity tolerance or any threshold against the 2025 profits.

---
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

---

# Active handoff — 2026-10-09 (final): replay v2 complete under the accepted same-day fill

Owner accepted the same-day average fill as the assumption (the owned archive has no
next-session bar). Replay v2 a1c35e4b (config run-equity-condor-robust-backtest-v2.json,
identity 0135e9ff, candidate 383a6af3) ran clean: rho supported 10/10, passing 2025-10/11
at 0.01; nominal 1,259 trades net -$8,395 (131 original trades reproduce +$5,354.85 exactly;
the 1,128 previously refused solves -$13,750); all seven robust arms net negative in their
two trading months; 115 parity_failed refusals above the frozen 1e-5 stand; account study
refuses on 42 non-positive decision reservations. Read
children/index_options/docs/memos/2026-10-09-condor-executable-clock-result.md.
Backup condor-replay-v2-complete-v1 (22 files, sha256sums). Diagnostics: no code bug, MILP
exact (primal/dual harness), oracle control +$1.87M; research note
docs/research/distribution-modeling/2026-10-09-condor-selection-intuition.md: the $396k
claimed edge is 59% forecast drift above the market forward, dispersion and favourable
prints; the short put is leveraged long stock. Open small defect: DiscreteCDFGrid.
worst_expected_loss HiGHS tolerance (~6.7e-7/share at rho>0). Next: owner decides the
selector/forecast direction (shrink toward market, OTM + wing cap) before any replay. No 2026 access,
fits, acquisition, spending or deployment.

---

