# RE-ENTRY archive — index_options

Previous re-entry notes for this lane, newest first. `/wrap` moves the note it
replaces to the top of this file; nothing is ever edited or removed here. Not
read at session start. Older history: `2026-10-10-consolidated-history.md`.

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

