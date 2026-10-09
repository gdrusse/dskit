# Condor executable-clock contract and plan — 2026-10-09

## TL;DR

[ADR-0256](../../../../docs/architecture/decision-log.md) (PROPOSED, write-and-wait) specifies fixes for all seven audit gaps. **No code, data, fit or replay ran.** The strongest honest label for the result is a *bar-proxy executable-clock replay*: the owned data are daily trade bars and cannot establish quotes, depth, partial fills or assignment, so nothing here may be called production-realistic.

## Gap → disposition

| # | Gap | Disposition | Evidence needed |
|---|---|---|---|
| 1 | Close forecast + same-day VWAP | Decide after the t close; one order for the next session t'; fill from t' bars only. Expiry stays the forecast's own, so the 31-day horizon contract holds and only the hold shortens. A fresh t' forecast is a 30-day horizon the model was not trained for: not done | t' option bars (owned, not yet extracted) |
| 2 | Daily bars ≠ fills | Four fill scenarios side by side (`session_vwap` primary, `open_print`, `limit_touch`, `adverse_extreme`), all-or-none, unfilled counted, no replacement. Bars bound the fill assumption; they do not prove it | t' coverage census before any launch |
| 3 | Synthetic expiry close | Exercise-by-exception threshold, share delivery liquidated at the next as-traded open, early assignment bracketed by existing `american_short_charge`; pin-ambiguous trades flagged | stock open + dividend rows (owned, not in the current extract) |
| 4 | 24 simultaneous condors | Separate `AccountReplay`: capital, concurrency, per-symbol/date caps, defined-risk reservation, ex-ante priority; cash basis only | declared capital fractions |
| 5 | Every rho unsupported | Edge-padded calendar-block sampler (below); abstention still stands when support or the bound fails | dates only |
| 6 | Zero relative gap rejected 1,128 solves | Absolute $1e-6 gap kept; relative gap becomes a diagnostic; exact-arithmetic objective parity becomes the gate; review before any outcome join | saved certificates |
| 7 | Only original85 traded | Unchanged: restored307 lacks verified inventories and acquisition is barred. Results claim original85 only | none obtainable |

## Why rho was unsupported (dates only)

The ADR-0255 sampler draws only complete non-wrapping blocks starting at observed dates. The final history date is then covered only if a start sits exactly B−1 days earlier. In the saved [calibration](../reports/robust-condor-replay-20261009/calibration.json) every zero-inclusion date, in all ten primary and ten sensitivity updates, is a trailing date: the final date alone in 18 of 20 updates, four in the April primary, five in the May sensitivity. The failure belongs to the date geometry, not the residuals, so no radius could ever have passed. The proposed geometry uses every calendar-day start from first−(B−1) to last and truncates blocks at the history edges; each date then lies in exactly B starts. It leaves grid, alpha, replicates, seed and the smallest-passing rule alone, and makes no coverage claim. I saw the first-month residual means while reading the replay memo; the choice was made from the date sets, and that exposure is disclosed.

## What existing data supports

- **Supports:** next-session and path OHLC/VWAP/volume/trade_count per contract (owned `option-universe-100/300`, admitted-window rows classed `other_dte`: 2,482,111 and 2,371,825 lines, pre-2026 only); stock open, close and dividend_amount; settlement closes; 84 explicit split inventories.
- **Does not:** bid/ask/size (null), intraday timestamps (`session_label`), depth, broker fees, margin rules, exercise instructions or assignment notices, daily marks other than stale trade prints.
- **Consequence:** qualification-grade execution evidence needs contemporaneous quotes, which only a forward collection can supply. This is documented, not worked around.

## Remaining assumptions

All 2024-2025 is already inspected, so any revised result is development evidence. Haircut tiers, fees and the participation cap are assumptions. Defined-risk reservation is a proxy for broker margin. The 84 split inventories are assumed complete; 307 histories stay unknown. Overnight spot movement between decision and fill is realized in the fill prices but never forecast.

## Finite plan and approvals

Phases A–D are in the ADR: focused TDD, two skeptic lenses, three data-only gates, then ONE revised replay with a new identity (about 17 minutes, upstream stages double as a determinism check). Approval is needed for: `participation_cap` value, the capital fractions (1, 1/2, 1/4 of unconstrained peak reservation), `session_vwap` as primary, relative gap as diagnostic with parity as the gate, and the t' / path-bar intake projection.

## Verification and locations

- Checked against saved aggregates only: the 20 zero-inclusion sets above; run timings from `run.log` (masses 684 s, select 314 s, rho 23 s); the sweep found no existing next-session fill, margin, account-admission or edge-padded sampler (existing seams reused: `CalendarBlockBootstrap`, `american_short_charge`, `structure_max_loss`, WindowBook).
- Review record: see the closure paragraph appended below when the reviews complete.
- Unchanged and still valid: [model inventory](../reports/production-universe-rerun-20261008/model-inventory.json) (all 66 packages and backups under `/home/russell/data/backups/index_options/production-universe-rerun-20261008/`); run `/home/russell/data/index_options/production-universe-rerun-20261008/condor-replay-run-v1/equity-condor-robust-backtest-2025-12-31-793013c0`; private audit `/home/russell/data/index_options/production-development-audit-20261007/universe-rerun-20261008/`.
- Execution this session: none. No 2026 access, acquisition, fit, spend, full suite or deployment.
