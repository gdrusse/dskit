# ADR-0256 correction and replay v2 — result, 2026-10-09

## TL;DR

Run v2 (`a1c35e4b…`, config `run-equity-condor-robust-backtest-v2.json`, identity `0135e9ff…`, candidate `383a6af3`) completed all ten stages, exit 0, in 27 minutes. With the sampler fix the robust radius is supported in all ten monthly updates and passes in two (October and November 2025 at 0.01); with the ill-posed zero-relative-gap rule replaced by the absolute-gap plus parity gate, the nominal arm admits 1,259 trades instead of 131. **Every arm is net negative**: nominal −$8,395 (the original 131 trades reproduce their +$5,354.85 exactly; the 1,128 previously refused solves lose −$13,750), and the seven robust arms, trading only in October–November, lose −$18k to −$48k each. The fill is the owner-accepted same-day average price (look-ahead disclosed); this is development evidence, not a qualification. No proxy was substituted for data we do not own.

## What changed (code locked at `497842b0`, config `383a6af3`)

| Finding | Disposition |
|---|---|
| 1 timing | Owner decision: same-day VWAP fill accepted as the assumption. The next-session fill path (`fill_session: "next"`) is built and reviewed but has no data: the owned archive carries only DTE 31–45 per session, so a decision contract never has a t' bar (0 of 286,120 liquid rows; `adr0256-fills-census-v1.json`) |
| 2 fills | One owner for the liquidity rule and tiered haircut (`liquid_leg_haircut`); unfilled accounting exists but is unused under same-day fills |
| 3 lifecycle | Per-leg `itm`/`pin_zone` flags and signed `would_deliver_shares`; P&L stays intrinsic; nothing simulated |
| 4 account | `ledger_studies account` built; on v2 it **refuses**: 42 decisions (10 nominal, 13 haircut_0, …) carry a non-positive decision reservation (credit ≥ wider wing at the decision marks after haircuts), a stale-VWAP artefact the study will not rank. No account-level P&L is claimed |
| 5 rho | `EdgePaddedCalendarBlockBootstrap`, span floor, optional `block_geometry`; legacy identity `8580a179…` pinned |
| 6 numerical | `max_relative_gap: null` diagnostic; `objective_parity_usd` 1e-5 frozen before the run from a 300-solve dry run; `> tie_tolerance_usd` pinned |
| 7 cohort | unchanged; original85 only |

Eight Sonnet lenses (three Phase-0, four implementation, one delta) closed with zero unresolved Critical/Major; reports and the disclosed backlog are under the private audit root as `adr0256-*-review-v1.txt`.

## Results and denominators

Decisions: 2,710 nominal contexts (1,451 no-trade, 1,259 trade, 0 refused). Each robust arm: 2,710 contexts, of which 2,193 `missing_calibrated_rho` (months with no passing radius), 42 `missing_calibration_tier`, and 517 decided in October–November.

| Arm | Trades | Net P&L | Mean | Median | Wins | Worst | Best | Net without best |
|---|---|---|---|---|---|---|---|---|
| nominal | 1,259 | −$8,395.19 | −$6.67 | $64.55 | 744 (59.1%) | −$42,283.41 | $14,124.72 | −$22,519.92 |
| rho_base | 174 | −$29,368.08 | −$168.78 | $19.51 | 88 | −$13,750.91 | $8,392.18 | −$37,760.26 |
| haircut_0 | 213 | −$18,243.93 | −$85.65 | $36.14 | 118 | −$13,108.72 | $8,615.16 | −$26,859.09 |
| haircut_half | 191 | −$37,400.04 | −$195.81 | $28.42 | 99 | −$13,429.81 | $8,503.67 | −$45,903.71 |
| haircut_double | 140 | −$29,346.80 | −$209.62 | −$8.51 | 68 | −$12,543.88 | $8,169.20 | −$37,516.00 |
| liquidity_3 | 232 | −$48,192.79 | −$207.73 | $24.01 | 122 | −$12,892.70 | $8,395.18 | −$56,587.97 |
| liquidity_10 | 103 | −$36,619.67 | −$355.53 | −$17.46 | 49 | −$12,300.00 | $4,793.17 | −$41,412.84 |
| liquidity_20 | 68 | −$27,121.20 | −$398.84 | −$34.71 | 32 | −$12,608.28 | $4,750.30 | −$31,871.49 |

Arms are alternative policies over the same contexts; their P&L must not be summed. Nominal by decision month: Feb −$154,711 (125 trades), Mar −$8,830, Apr +$18,006, May +$34,683, Jun +$62,885, Jul +$1,149, Aug +$7,805, Sep +$42,100, Oct −$54,640, Nov +$43,158. In the two months the robust arms traded, nominal made −$11,482 over 266 trades; every robust arm lost in October and gained in November. The medians are positive while the means are negative: losses are concentrated in a few large settlements, and the empirical fifth percentile of the original run (−$2,385) was not a bound.

Calibration (`monthly` in the run's calibration artifact): all ten primary and sensitivity updates supported (48–83 dates, 60 truncated edge blocks each); the primary passes only in 2025-10 and 2025-11 at the largest grid value 0.01 (upper bounds −0.036 and −0.114); the sensitivity horizon passes at 0.01 from August. No radius was chosen from profit; the grid, alpha and rule are the frozen ones.

Numerical: 12,543 gated solves; 12,428 passed parity (median discrepancy 0, p99 1.6e-6, max 9.9e-6); **115 refused `parity_failed`** with discrepancies up to 6.67e-5 — above the 1e-5 frozen from a dry run whose 60 contexts held only 3 trades per radius. The refusals stand; the tolerance is not revised after results. 57 further decisions refused `tie_floor_failed`. Nominal had zero refusals.

Lifecycle disclosure: 2,502 ITM legs, 11 pin-zone legs, 1,406 of 2,380 condors (59%) would leave a share position at expiry under exercise-by-exception. Settlement is intrinsic at the as-traded close; assignment, delivery and dividends are not simulated.

Determinism: the masses stage reproduced in 684.77 s (684.19 s in v1) and the 131 overlapping nominal trades settle to the same cent (max |ΔP&L| = 0.0).

## Assumptions still carried

Same-day average fill (owner-accepted; a look-ahead relative to a close-time decision); haircut tiers, $0.65/leg fee, no bid/ask; early assignment ignored; no account constraints in selection; 84 split inventories assumed complete; restored307 unknown; 2024–2025 already inspected.

## Locations

- Run: `/home/russell/data/index_options/production-universe-rerun-20261008/condor-replay-run-v2/equity-condor-robust-backtest-v2-2025-12-31-a1c35e4b` (22 files; evidence.json holds every decision, solve, certificate, outcome and refusal). Second copy: `/home/russell/data/backups/index_options/production-universe-rerun-20261008/condor-replay-v2-complete-v1` (same disk; `sha256sums.txt`).
- Audit: `condor-replay-v2-launch.json`, `condor-replay-v2.log`, `condor-replay-v2-aggregate-v1.json`, `condor-replay-v2-comparison-v1.json`, `condor-replay-v2-account-study-v1.txt` (the refusal), the ADR-0256 gate receipts and review reports, all under `/home/russell/data/index_options/production-development-audit-20261007/universe-rerun-20261008/`.
- Models: unchanged — [model inventory](../reports/production-universe-rerun-20261008/model-inventory.json); run 793013c0 and its backups untouched.
- Execution this session: the parity dry run, the fills census over owned raw stores, and this one replay. No fits, 2026 access, acquisition, spending, deployment or live trading.
