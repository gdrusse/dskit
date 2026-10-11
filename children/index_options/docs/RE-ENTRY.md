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
