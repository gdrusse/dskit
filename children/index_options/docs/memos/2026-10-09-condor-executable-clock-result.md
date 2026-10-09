# Condor executable-clock correction — result, 2026-10-09

## TL;DR

The ADR-0256 code is built, reviewed and merged, and the pre-registered data gate was run first: **the revised replay did not launch**, because the owned option archive holds no next-session bar for any decision contract. On every session the archive carries only contracts at 31–45 calendar days to expiry; a contract chosen at the close of t has 30 days left on t' and is absent from the stores. 0 of 286,120 liquid decision-day rows had a t' bar (share 0.000 against the 0.50 gate), by structure, not by liquidity. No proxy was substituted. Run 793013c0's same-day-look-ahead P&L stays the only completed replay and stays labeled as such.

## What changed in the repo (candidate `497842b0`)

| Finding | Code | Review |
|---|---|---|
| 1–2 timing/fill | `ExactDteBarChain fill_session next` derives t' from the fills stream's calendar; `RobustCondorBatchSelect` reprices all four legs at t' with the arm's rule through one owner `liquid_leg_haircut`, all-or-none, unfilled counted by reason; `CondorExpirySettle` dates entries at `fill_date` | S3 lenses: 1 Major each (t' from context bars; peak-reservation test) — closed |
| 3 lifecycle | per-leg `itm` / `pin_zone` flags, signed `would_deliver_shares`; P&L stays intrinsic; nothing simulated | clean after boundary pins |
| 4 account | `ledger_studies account` read-only study: decision-time admission, caps 10/1/3/1.0, reservation = decision max loss with fees once, release at saved settlement, capital 1, 1/2, 1/4 of the nominal peak | 1 Major test gap (sequential peak) — closed |
| 5 rho support | `EdgePaddedCalendarBlockBootstrap` + span floor; `block_geometry` optional knob; legacy identity `8580a179…` pinned | padded supported 20/20 updates, legacy 0/20 (`adr0256-support-table-v1.json`) |
| 6 numerical | `max_relative_gap: null` diagnostic; `objective_parity_usd` gate (frozen 1e-5 from a 300-solve no-outcome dry run; rule value 1.0000106e-5 rounded to the tighter side); `> tie_tolerance_usd` pin; `primal_failed`, `context_error` counted | S1+S2 lenses: 0 Major |
| 7 cohort | unchanged; original85-only claim | — |

Executable-clock config `configs/run-equity-condor-executable-clock.json`, identity `41273680…`, validates and plans; never run. Focused suites: 1,525+ child tests and the stats/purity sets pass (the purity failure on `parquet.py` and six subprocess tests pre-exist on `main` via the shared venv's stale editable install). The child exact-manifest test, already failing on `main` (484 vs 478), is corrected to 485.

## The data finding, precisely

- Decision dates: 91 (2024-02-06..2025-11-25). Fill sessions t' exist for 90 (2025-11-25 has no later pre-2026 session).
- Owned stores `option-universe-100/300`: per session, contracts at DTE 31–45 only (independent sample of 300,000 raw rows: 13 distinct DTEs, minimum 31). The `other_dte` rows cited earlier are DTE 32–45 on decision dates, not DTE 30 on t'.
- Therefore no decision contract has any owned price after the decision day until expiry. Next-session fills, open prints, limit touches and path marks are all unsupported by owned data for this universe. The gap cannot be closed by a different proxy on the same archive.
- What would close it: a chain archive with full expiries per session (or the same vendor's DTE-30 pull) for the 392 names, or a forward collection. Both are acquisitions and were not authorized.

## Remaining assumptions (unchanged)

No bid/ask (accepted); early assignment ignored (flagged); account constraints as a post-hoc study; 84 split inventories assumed complete; restored307 unknown; all of 2024-2025 already inspected.

## Verification and locations

- Reviews (Sonnet, retained privately under the universe-rerun-20261008 audit root): `adr0256-phase0-{correctness,integration,convergence}-review-v1.txt`, `adr0256-s12-{correctness,integration}-review-v1.txt`, `adr0256-s3-{correctness,integration}-review-v1.txt`, `adr0256-delta-review-v1.txt`. Zero unresolved Critical/Major; backlog listed in the ADR.
- Gates: `adr0256-support-table-v1.json`, `adr0256-parity-dryrun-v1.json`, `adr0256-fills-census-v1.json` (+ recipe, spec, logs).
- Model inventory, run 793013c0, its backups and the private audit are unchanged (see the [replay memo](2026-10-09-robust-condor-replay.md)).
- Execution this session: the no-outcome parity dry run (300 solves) and the fills census over owned raw stores; no fits, 2026 access, acquisition, spending, replay or deployment.
