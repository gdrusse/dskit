# Condor executable-clock contract and plan — 2026-10-09

## TL;DR

[ADR-0258](../../../../docs/architecture/decision-log.md) (revision 4, owner-approved contract, trimmed to the minimal correction) specifies fixes for the seven confirmed findings. **No code, data, fit or replay ran this session.** The strongest honest label for the result is a *bar-proxy replay on an executable decision clock*: the decision can be made after the close and ordered for the next session, but the owned data are daily trade bars and cannot establish quotes, depth, partial fills or assignment, so nothing here may be called production-realistic. If no radius is supported again, the run is the nominal comparator only. The audit's separate publication-clock item is not addressed.

## Finding → disposition

| # | Finding | Disposition | Evidence needed |
|---|---|---|---|
| 1 | Close forecast + same-day VWAP | Decide after the t close; one order for the next session t'; fill from t' bars only. Expiry stays the forecast's own, so the 31-day horizon contract holds and only the hold shortens. A fresh t' forecast is a 30-day horizon the model was not trained for: not done | t' option bars (owned, not extracted) |
| 2 | Daily bars ≠ fills | One scenario: t' VWAP with the arm's haircut, all-or-none, unfilled counted, no replacement. Bars bound the assumption; they do not prove it (other scenarios deferred) | t' coverage census before launch |
| 3 | Synthetic expiry close | Disclosure only: per-leg ITM, pin-zone and would-deliver flags counted; no delivery, liquidation or assignment simulated (owned stock bars have only an as-traded `close`) | as-traded open/dividend columns (deferred) |
| 4 | 24 simultaneous condors | Read-only account study over saved decisions/outcomes: decision-time admission with frozen caps, reservation = decision max loss, ex-ante priority, three capital fractions; not a margin or mark-to-market model | frozen knobs |
| 5 | Every rho unsupported | Edge-padded calendar-block sampler plus a non-overlapping-block floor; abstention stands when support or the bound fails | dates only |
| 6 | Zero relative gap rejected 1,128 solves | Absolute $1e-6 gap kept (all 1,128 refusals would pass it); relative gap becomes a diagnostic; independent-primal parity becomes the gate, its tolerance frozen from a no-outcome dry run at radius > 0, failures counted as skips | saved certificates |
| 7 | Only original85 traded | Unchanged: restored307 lacks verified inventories and acquisition is barred. Original85-only claims | none obtainable |

## Why rho was unsupported (dates only)

The ADR-0255 sampler draws only complete non-wrapping blocks starting at observed dates, so the final history date is covered only if a start sits exactly B−1 days earlier. In the saved [calibration](../reports/robust-condor-replay-20261009/calibration.json) every zero-inclusion date, in all ten primary and ten sensitivity updates, is a trailing date: the final date alone in 18 of 20, four in the April primary, five in the May sensitivity. No radius could ever have passed, whatever the residuals. The proposed geometry starts a block at every calendar day from first−(B−1) to last and truncates at the history edges; each date then lies in exactly B starts. That is inclusion, not equal weight (simulation: the first date is over-weighted by 3.1% at B=31 and 8.1% at B=62). It makes no coverage claim. The author had read the first-month residual means before proposing it; the choice rests on the date sets and is disclosed in the ADR.

## What existing data supports

- **Supports:** next-session bars (OHLC/VWAP/volume/trade_count) per contract from the owned `option-universe-100/300` rows (the 2,482,111 and 2,371,825 admitted-window `other_dte` lines prove ownership, not coverage of the legs a decision picks; a pre-registered census gate at 0.50 decides launch); stock open, close and dividend_amount (split-adjusted; conversion via the 84 inventories is required); settlement closes. A read-only pass of the extract found 90.6% of t-liquid rows with volume >= 10 and a median of 7 liquid rows per context; no thinness claim is made either way.
- **Does not:** bid/ask/size (null), intraday timestamps (`session_label`), depth, broker fees, margin rules, exercise instructions, assignment notices, marks other than stale trade prints, special-dividend or merger handling, borrow.
- **Consequence:** qualification-grade execution evidence needs contemporaneous quotes, which only a forward collection can supply.

## Remaining assumptions and values for approval

All 2024-2025 is already inspected, so a revised result is development evidence. Haircut tiers, fees and the participation cap are assumptions; defined-risk reservation is a proxy for broker margin; the 84 inventories are assumed complete; 307 histories stay unknown. Overnight spot movement between decision and fill is realized in fill prices, never forecast. Owner-approved values (2026-10-09), chosen from convention and units, not from P&L: `exercise_threshold_usd` 0.01, `max_concurrent` 10, `max_per_symbol` 1, `max_per_date` 3, `max_reserved_fraction` 1.0, capital fractions 1, 1/2, 1/4 of the nominal arm's peak reservation, entry fee $0.65/leg, census gate 0.50; `objective_parity_usd` is frozen by the dry-run rule. `participation_cap` and `carry_rate` were approved but are unused after the trim.

## Finite plan

Three parallel Sonnet TDD slices (sampler; gap/parity; fill clock + account study), two fresh Sonnet lenses, three data-only gates, then ONE separately authorized replay with a new identity and a memo. Deferred items are listed in the ADR and are not authorized.

## Verification and locations

- Checked against saved aggregates only: the 20 zero-inclusion sets; run timings from `run.log` (masses 684 s, select 314 s, rho 23 s). The sweep found no existing bar-fill, account-admission or edge-padded sampler; the inventory reviewed `executor` fill rules, `encumbrance` and `nodes_capital` and found them quote-, live- or share-specific.
- Phase-0 lenses (Sonnet): correctness/authority on 1d070604, 0 Critical/3 Major/12 Minor/3 Nit; tests/integration on b569b48f, 0 Critical/3 Major/9 Minor/2 Nit. Both are retained as `adr0256-phase0-correctness-review-v1.txt` and `adr0256-phase0-integration-review-v1.txt` in the private audit; revision 3 addressed them and revision 4 trimmed the scope at the owner's direction, so deferred items drop out rather than being resolved. A third fresh lens on revision 3 is recorded beside them.
- Unchanged and still valid: [model inventory](../reports/production-universe-rerun-20261008/model-inventory.json) (all 66 packages; backups under `/home/russell/data/backups/index_options/production-universe-rerun-20261008/`); run `/home/russell/data/index_options/production-universe-rerun-20261008/condor-replay-run-v1/equity-condor-robust-backtest-2025-12-31-793013c0`; private audit `/home/russell/data/index_options/production-development-audit-20261007/universe-rerun-20261008/`.
- Execution this session: none. No 2026 access, acquisition, fit, spend, full suite or deployment.
