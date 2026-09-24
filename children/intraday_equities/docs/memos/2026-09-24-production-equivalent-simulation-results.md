# Production-equivalent historical simulation: results (ADR-0184)

## TL;DR

The strategy ran on real split-adjusted 1-minute bars from 2022-09-09 through 2025-10-16, the way production would run it. It ended with a NAV of **$18,066.16** on **$16,580.00** contributed. Net realised P&L was **+$1,486.16**, after **$1,463.24** in fees (gross before fees: +$2,949.40). There were **457** round trips, with entries on **269 of 779** trading days. This is **developmental post-selection evidence** (`deployment_eligible=false`). It is not a deployable backtest.

## What ran

- **Pipeline.** One document, `configs/run-development-simulation.json`, run by `python -m dskit.pipeline run ... --adapter intraday_equities`. The graph is `bars_a`, `bars_e` (`intraday_equities-bars`, bounded reads) -> `bars` (`concat`) -> `publish` (`intraday_equities-forecast-publisher`) -> `decide` (`intraday_equities-mio-decider`) -> `simulate` (`intraday_equities-development-simulation`) -> `report` (`intraday_equities-simulation-report`) -> `write_fills`, `write_refused`, `write_daily` (`records-write`) and `write_summary` (`table-write`).
- **Provenance.** Commit `bb1ef42`, branch `claude/prod-sim-20260923`, run on 2026-09-24. The full document hash is `6c2523c860048a93e2211ea9676e7e5c085ce30f947b73ff59a61208ae205f1a` (run `…-84740cdd`). The smoke hash is `7bc14c93a4c5aed90f0546319ca992c0fee27ee74db581f1013cfe0aa117466f` (run `…-smoke-…-84dca895`). After the run, the ADR was renumbered from 0182 to 0184, which edited the config `notes`. Re-running the committed documents will therefore produce different document hashes, with no logic change.
- **Retraining.** One release per P16 walk-forward fold, folds 2..19. Each model was trained strictly before its fold start. Calibration used prior-fold residuals from the trailing 730 days, with the cutoff at each release.
- **Decisions.** On the 30-minute lattice, one `EquityKellyMIO` solve runs per tick for each lead group, in ascending lead order, on live cash. Fills happen at the next bar's open, with Schwab costs (`configs/fill-policy.json`). Exits are forced after the unit's lead.
- **Funding.** A $1,000 seed plus $20 per trading day (ADR-0176, `configs/cash-flow-policy.json`): $1,000 + 779 x $20 = $16,580.
- **Universe.** The 11 units admitted by the P16 gates, at their capped horizons (minutes): PANW 1; LLY 2, LULU 2; LITE 3, TER 3, XLK 3; ADBE 4; CIEN 5, NOW 5; MSTR 6; LRCX 10.

## Results (full run, verified from `development-simulation-summary.json` and `-daily.jsonl`)

| Metric | Value |
|---|---:|
| Final NAV / contributed | $18,066.16 / $16,580.00 |
| Net P&L (realised; unrealised 0) / fees | +$1,486.16 / $1,463.24 |
| Round trips / days with entries | 457 / 269 of 779 |
| Max drawdown (fill-resolution) / max daily drawdown | $792.46 / $738.59 |
| Gross notional / turnover (gross / mean NAV) | $6,574,994.61 / 712.1x |
| Refusals | 50 `insufficient_cash` (MSTR 45, LITE 3, TER 1, NOW 1) |
| `mio_refused` / skips / max NAV discrepancy | 0 / 0 / 0.0 |

Net P&L by calendar year: 2022 (from Sep 9) -$63.87; 2023 +$32.47; 2024 +$184.94; 2025 (to Oct 16) +$1,332.62.

| Symbol | Lead | Round trips | Fees | Net P&L |
|---|---:|---:|---:|---:|
| MSTR | 6 | 339 | 940.15 | +291.01 |
| LITE | 3 | 71 | 316.33 | +706.82 |
| LULU | 2 | 17 | 100.82 | +85.50 |
| TER | 3 | 12 | 54.21 | +538.70 |
| NOW | 5 | 9 | 28.39 | +72.37 |
| CIEN | 5 | 7 | 22.52 | -206.50 |
| ADBE | 4 | 2 | 0.83 | -1.73 |

LLY, LRCX, PANW and XLK never traded. Measured interval coverage per lead ranged from 0.905 to 0.988 against the nominal 0.95.

The smoke run covered fold 2 only (2022-09-09..2022-11-10, 45 days). It ended with a NAV of $1,877.75 on $1,900 contributed: net -$22.25, fees $8.55, 12 round trips. This equals the full run's fold-2 closing cash, which serves as a determinism check. It is not separate evidence.

**Runtime.** The full run took 6:26:28 wall time with a 5.56 GB max RSS, exit 0. The smoke took 21:14 with 0.72 GB. The ADR estimate for the full run was about 3.5 h.

## Caveats

- `deployment_eligible=false`. The results are `developmental_post_selection`: the same P16 folds chose the model, the mask and the gates.
- `cap_evidence_look_ahead`: the gate admission, meaning which units trade, at which horizon and with which caps, uses evidence through 2025-10-16. A 2022 tick therefore uses a universe chosen with later information.
- Not modelled: T+1 settlement, good-faith violations and PDT rules. Sale proceeds are reused immediately, which is optimistic for a small cash account. Halts are not modelled either (`halted=false`; absent minutes are simply absent bars).
- Prices are split-adjusted. Integer-share sizing is distorted before later splits (LRCX, MSTR and PANW split inside the window).
- Lead groups are solved in ascending order and share one cash budget, which biases allocation toward shorter leads.
- Concentration: MSTR accounts for 74% of round trips, 64% of fees and 45 of the 50 refusals. LITE and TER together provide 84% of net P&L. About 90% of net P&L came in 2025.
- Fees were 50% of gross P&L. The result is thin relative to costs.

## Preserved outputs

`/home/russell/dskit/children/intraday_equities/pipeline_runs/` holds both run directories, the `development-simulation{,-smoke}-{fills,refused,daily}.jsonl` and `-summary.json` files, and the logs `development-simulation-full.log` and `development-simulation-smoke.log`. All 44 run files were sha256-verified against the worktree originals, and the logs were byte-compared. Journal rows A54551 (smoke) and A54552 (full) record the original worktree paths.

## Next steps (owner decisions, ADR-0184 agent-decided questions)

The highest priority is (9), settlement, good-faith and PDT modelling. The other questions: (1) fold schedule as the retrain rule; (2) cadence and one lead per unit; (4) calibration window; (6)-(7) false-signal and coverage floors; (8) the MIO risk placeholders; (10) split-adjusted sizing; (13) no `stat_test` wire into capital. Operationally: rule on tmpfs scratch for runtime, and decide whether an untouched post-2025-10-16 window should be the next exam.
