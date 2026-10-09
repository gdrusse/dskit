# correlation_arb implementation plan (2026-10-07, revised after the dskit review)

## Problem

Price Kalshi S&P 500 / Nasdaq-100 contracts (KXINX, KXINXU, KXNASDAQ100,
KXNASDAQ100U) from SPXW/NDXP options. Find out whether gaps survive fees, then
quote as maker. Later stages scan ladder coherence, then model dependence across
correlated markets.

## Tier rule applied

- **Tier 1** (`dskit/<pkg>/*.py`): stdlib-only, domain-neutral.
- **Tier 2** (`dskit/<pkg>/libs/`): generic wrappers of a library, or a connector pack.
- **Tier 3** (this child): JSON configs plus venue facts.

Kalshi field names, fee types and series are venue facts and stay here (ADR-0245
ruling). Nothing is rebuilt that dskit already ships.

## What already exists (reuse, no new code)

| Need | dskit piece | Tier |
|---|---|---|
| Kalshi markets, archive, trades, `expiration_value`, candles | `onboarding/libs/kalshi_history.py` (ADR-0239) | 2 |
| Kalshi live books, fee schedules | `onboarding/libs/kalshi.py` (ADR-0075) | 2 |
| SPX/NDX chains with bid/ask (15-min delayed) | `onboarding/libs/cboe.py` (ADR-0182); `index_options`' `cboe-chain-wide` already records them | 2 |
| Options-implied CDF (parity forward, isotonic slopes) | `pipeline/libs/predictive_cdf.py` `OptionPriceCDF`, `OptionCDFPanel` | 2 |
| Above/Below/Between payoffs, `BinaryFairValue` | `pipeline/binary_pricing.py` (ADR-0246) | 1 |
| Quadratic fee + per-order ceiling | `pipeline/fee_mechanics.py` (ADR-0245) | 1 |
| Kill test: Brier/log vs mid, take rule, cluster SE | `pipeline/binary_scoring.py` `BucketedBinaryScore` (ADR-0246) | 1 |
| Strictly-prior sub-day as-of read | `pipeline/libs/parquet_series.py` `prior_index` (ADR-0243) | 2 |
| Run-identified table writer | `pipeline/kinds_run_write.py` (ADR-0247) | 1 |
| Q→P transport, GPD tails, PIT/CRPS scores | `predictive_cdf.py`, `distribution_scores.py` | 1–2 |
| Walk-forward with embargo | `driver.run_walk_forward`, `kinds_split.py` | 1 |
| Fill simulation, mark-outs, serve loop | `production/executor.py` `PaperExecutor`; production reporting | 1 |
| Kelly over scenarios | `pipeline/libs/pyomo.py` `ScenarioUtilitySolve` | 2 |

## What is missing (ADRs written, awaiting approval)

| Item | ADR | Tier | Module |
|---|---|---|---|
| Venue-neutral market rows, fee columns (from crypto_trading) | 0256 | 2 | `pipeline/libs/binary_market_rows.py` |
| Decision instants, quote state (from crypto_trading) | 0256 | 1 | `pipeline/binary_decisions.py` |
| Executable vertical-spread bounds; `quote_problems` graduates | 0257 | 1 | `pipeline/digital_bounds.py` |
| Price a binary from any CDF curve | 0257 | 1 | `pipeline/binary_curve.py` |
| Coherence LP + projection across linked binaries | 0257 | 2 | `pipeline/libs/binary_coherence.py` |
| Smile fit (SVI/spline) | later, Stage 2 | 2 | — |
| Maker quoting proposer | later, Stage 4 | 1 | — |
| Copula/dependence pack | later, Stage 5 | 2 | — |

## Owner decisions

1. Approve ADR-0256 and ADR-0257: (a) both (b) 0257 only (c) neither yet.
2. Choose option history:
   - (a) pilot on the Cboe recording, which is free;
   - (b) Databento cbbo-1m at $199/mo;
   - (c) ThetaData.
3. Start the `cboe-0dte` recorder, every 5 min from 15:00 to 16:20 ET: (a) yes (b) no.

## Stage 0: data gates (tier 3 only; configs written, tests green)

Sources:
- `source-kalshi-index`, which also provides fees;
- `source-kalshi-history-index`, `-candles-index` and `-trades-index`;
- `source-kalshi-index-books`;
- `source-cboe-0dte`.

The suite is `suite-kalshi-history-index-markets`. `tests/test_index_configs.py`
pins the series lists across all of them.

| Gate | Pass |
|---|---|
| G0.1 `expiration_value` vs official SPX/NDX close | \|diff\| ≤ 0.01 on ≥ 95% of days |
| G0.2 events, history start, two-sided minutes 15:00–16:00 by strike | ≥ 60 events, ≥ 5 near-spot strikes |
| G0.3 usable SPXW/NDXP 0DTE chains | ≥ 10 days |
| G0.4 live book recorder running | started |

## Stage 1: kill test (after ADR-0256/0257)

`configs/run-kill-index.json` runs this chain:

1. `BinaryMarketRows` with the Kalshi field map in its params;
2. `DecisionRows`, at leads of 5, 15, 30 and 60 minutes;
3. the chain as of I − delay, read with `prior_index`;
4. `OptionPriceCDF`;
5. `CurveBinaryFairValue` + `DigitalBounds`;
6. `FeeColumns`;
7. `BucketedBinaryScore`;
8. `RecordsWriteRun`.

**Kill if** the median executable gap is under 1¢ after fees **and** option-Q
shows no log-score gain over the Kalshi mid.

## Stage 3: coherence (parallel; Kalshi data only)

`BinaryCoherence` runs over KXINX ↔ KXINXU and NDX ↔ NDXU on recorded books.
**Kill if** there are fewer than about 1 violation a week with ≥ 10 contracts
after fees.

## Stages 2, 4, 5

These run only on a Stage 1 or Stage 3 pass, each behind its own ADR:
- **Stage 2:** smile fit + Q→P recalibration.
- **Stage 4:** maker proposer on `PaperExecutor`.
- **Stage 5:** copula dependence for S&P↔Nasdaq, then weather.

## Done when

Stage 1 and Stage 3 each have a pass/kill memo. All new capability is in dskit
under an approved ADR. This child holds only configs, the runbook and tests.
