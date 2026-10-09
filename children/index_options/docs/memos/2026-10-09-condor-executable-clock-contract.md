# Condor executable-clock contract and plan — 2026-10-09

## TL;DR

[ADR-0256](../../../../docs/architecture/decision-log.md) (PROPOSED, write-and-wait) specifies fixes for the seven confirmed findings. **No code, data, fit or replay ran.** The strongest honest label for the result is a *bar-proxy executable-clock replay*: the owned data are daily trade bars and cannot establish quotes, depth, partial fills or assignment, so nothing here may be called production-realistic. The audit's separate publication-clock item is not addressed.

## Finding → disposition

| # | Finding | Disposition | Evidence needed |
|---|---|---|---|
| 1 | Close forecast + same-day VWAP | Decide after the t close; one order for the next session t'; fill from t' bars only. Expiry stays the forecast's own, so the 31-day horizon contract holds and only the hold shortens. A fresh t' forecast is a 30-day horizon the model was not trained for: not done | t' option bars (owned, not extracted) |
| 2 | Daily bars ≠ fills | Four scenarios side by side (`session_vwap` primary, `open_print`, `limit_touch`, `adverse_extreme`), all-or-none, invalid or thin fills unfilled, no replacement; unfilled orders' payoff reported as a selection diagnostic. Bars bound the assumption; they do not prove it | t' coverage census before launch |
| 3 | Synthetic expiry close | Exercise-by-exception threshold; share delivery liquidated at the next as-traded open; early assignment bracketed by `american_short_charge` (ordinary dividends only); pin cases flagged. Stock open/close/dividend must first be converted to the as-traded basis; unknown-basis names refuse | as-traded stock columns |
| 4 | 24 simultaneous condors | `AccountReplay` on decision-time facts only; reservation held from t until the fill resolves; ex-ante priority; one reference peak sets the capital levels. The defined-risk reservation does **not** bound loss on delivery trades, which are counted | declared knobs |
| 5 | Every rho unsupported | Edge-padded calendar-block sampler plus a non-overlapping-block floor; abstention stands when support or the bound fails | dates only |
| 6 | Zero relative gap rejected 1,128 solves | Absolute $1e-6 gap kept; relative gap becomes a diagnostic; independent-primal objective parity becomes the gate, failures counted as skips | saved certificates |
| 7 | Only original85 traded | Unchanged: restored307 lacks verified inventories and acquisition is barred. Original85-only claims | none obtainable |

## Why rho was unsupported (dates only)

The ADR-0255 sampler draws only complete non-wrapping blocks starting at observed dates, so the final history date is covered only if a start sits exactly B−1 days earlier. In the saved [calibration](../reports/robust-condor-replay-20261009/calibration.json) every zero-inclusion date, in all ten primary and ten sensitivity updates, is a trailing date: the final date alone in 18 of 20, four in the April primary, five in the May sensitivity. No radius could ever have passed, whatever the residuals. The proposed geometry starts a block at every calendar day from first−(B−1) to last and truncates at the history edges; each date then lies in exactly B starts. That is inclusion, not equal weight (simulation: the first date is over-weighted by 3.1% at B=31 and 8.1% at B=62). It makes no coverage claim. The author had read the first-month residual means before proposing it; the choice rests on the date sets and is disclosed in the ADR.

## What existing data supports

- **Supports:** next-session bars (OHLC/VWAP/volume/trade_count) per contract from the owned `option-universe-100/300` rows (2,482,111 and 2,371,825 admitted-window lines classed `other_dte`; pre-2026 only); stock open, close and dividend_amount (split-adjusted; conversion via the 84 inventories is required); settlement closes.
- **Does not:** bid/ask/size (null), intraday timestamps (`session_label`), depth, broker fees, margin rules, exercise instructions, assignment notices, marks other than stale trade prints, special-dividend or merger handling, borrow.
- **Consequence:** qualification-grade execution evidence needs contemporaneous quotes, which only a forward collection can supply.

## Remaining assumptions and values for approval

All 2024-2025 is already inspected, so a revised result is development evidence. Haircut tiers, fees and the participation cap are assumptions; defined-risk reservation is a proxy for broker margin; the 84 inventories are assumed complete; 307 histories stay unknown. Overnight spot movement between decision and fill is realized in fill prices, never forecast. Proposed values, chosen from convention and units, not from P&L: `participation_cap` 0.10, `objective_parity_usd` 1e-6, `exercise_threshold_usd` 0.01, `max_per_symbol` 1, `max_per_date` 3, `max_reserved_fraction` 1.0, capital fractions 1, 1/2, 1/4 of one reference peak. `carry_rate` and the fee schedule are for the owner to supply.

## Finite plan

Four phases in the ADR: focused TDD, two skeptic lenses, three data-only gates, then ONE revised replay with a new identity (about 17 minutes; upstream recomputation doubles as a determinism check). Two new dskit core modules (bar fills, capital admission) are part of the approval, since the generic parts belong in dskit and only the condor wiring stays in the child.

## Verification and locations

- Checked against saved aggregates only: the 20 zero-inclusion sets; run timings from `run.log` (masses 684 s, select 314 s, rho 23 s). The sweep found no existing bar-fill, account-admission or edge-padded sampler; the inventory reviewed `executor` fill rules, `encumbrance` and `nodes_capital` and found them quote-, live- or share-specific.
- Phase-0 correctness lens (Sonnet, candidate 1d070604): 0 Critical, 3 Major, 12 Minor, 3 Nit, all addressed in ADR revision 2; the retained report is `adr0256-phase0-correctness-review-v1.txt` in the private audit. A second lens on revision 2 is pending.
- Unchanged and still valid: [model inventory](../reports/production-universe-rerun-20261008/model-inventory.json) (all 66 packages; backups under `/home/russell/data/backups/index_options/production-universe-rerun-20261008/`); run `/home/russell/data/index_options/production-universe-rerun-20261008/condor-replay-run-v1/equity-condor-robust-backtest-2025-12-31-793013c0`; private audit `/home/russell/data/index_options/production-development-audit-20261007/universe-rerun-20261008/`.
- Execution this session: none. No 2026 access, acquisition, fit, spend, full suite or deployment.
