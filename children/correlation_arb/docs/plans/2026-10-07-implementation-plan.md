# correlation_arb implementation plan (2026-10-07)

## Problem

Price Kalshi S&P 500 / Nasdaq-100 contracts (KXINX, KXINXU, KXNASDAQ100,
KXNASDAQ100U) from SPXW/NDXP option quotes. Find out whether gaps survive
fees, then quote as a maker.

Later stages extend the same machinery:
- coherence scans across Kalshi ladders and nested series;
- dependence across correlated markets (S&P↔Nasdaq, weather cities).

Evidence base:
- `docs/research/landscape/2026-10-07-correlation-arbitrage-survey.md`
- `docs/research/data-and-modeling/2026-10-07-inventory-data-and-dependence.md`

## Rule this plan follows

The child stays a wrapper: JSON configs plus thin tier-3 code. Every missing
capability is built generically in `dskit/`. Each item marked **ADR** is
written into `docs/architecture/decision-log.md` and **waits for owner
approval** before any code.

## Owner decisions needed before Stage 1

1. **Merge the crypto branch's dskit modules to main** (ADR-0236..0244).
   This brings `kalshi_history`, `binary_pricing`, `fee_mechanics`,
   `binary_scoring`, `parquet_series` and `RecordsWriteRun`. Without them
   this child would duplicate them, which repo rules forbid.
2. **Choose the option-history source:**
   - (a) your existing Cboe recording since 2026-09-23. Free; snapshots at
     15:50 and 16:20, 15 minutes delayed. Enough for a pilot.
   - (b) Databento OPRA.PILLAR cbbo-1m. Covers SPX/SPXW/NDXP; $199/mo
     Standard gives 12 months of history.
   - (c) ThetaData.
   - Recommendation: start with (a) and buy (b) only if Stage 1 passes on (a).
3. **Raise the Cboe recording to every 5 minutes, 15:00–16:20 ET**, for SPX
   and NDX (roots SPXW, NDXP, `max_dte` 1). Start this now; it costs nothing
   and every week of delay is lost data.

## Stage 0: data gates (configs only; no new code)

| Gate | What | Kill / proceed |
|---|---|---|
| G0.1 settlement basis | For every settled KXINX/KXNASDAQ100 event: `expiration_value` vs the official close (Cboe `index_daily` SPX; NDX via Nasdaq). Also check against SPXW/NDXP settlement where available. | Proceed if \|diff\| ≤ 0.01 on ≥ 95% of days. Otherwise model basis as a noise term, and stop if it exceeds ~2 points routinely. |
| G0.2 Kalshi coverage | Archive plus live pull of the four series: markets, 1-minute candles, trades. Count events, history start, strikes, and the share of minutes 15:00–16:00 with a two-sided quote, by distance from spot. Also volume by strike. | Proceed if ≥ 60 events with two-sided quotes on ≥ 5 near-spot strikes. |
| G0.3 option leg | From the Cboe recording, count days with a usable SPXW and NDXP 0DTE chain at 15:50. Report the quote-quality rejection rate. | Proceed if ≥ 10 days; keep recording regardless. |
| G0.4 live books | Start the `orderbooks` recorder (`watch`, every 10 s, 14:30–16:00) for the four series. Kalshi keeps no historical books. | Needed for Stage 4. |

Child files to write:
- `configs/source-kalshi-index.json` and `source-kalshi-history-index.json` (copy from crypto_trading);
- `source-kalshi-index-books.json`, `source-cboe-index-0dte.json`;
- matching `suite-*.json`;
- `docs/plans/<date>-wsl-data-pull-runbook.md`.

Done when each gate is filed as a memo.

## Stage 1: option-implied fair value and the executable band (the kill test)

**dskit work:**
1. **ADR — `OptionDigitalBounds`.** Executable vertical-spread bounds on above/below/between digitals from bid/ask, taking the tighter of the call and put bands. Reuses `quote_problems`/`_usable_quotes` (graduate from index_options in the same ADR).
2. **ADR — `CurveBinaryFairValue`.** A sibling of `BinaryFairValue` that evaluates the existing `Above/Below/Between` payoffs on any `GridCurve` CDF, e.g. `OptionPriceCDF` output. Not a new payoff vocabulary.
3. **ADR — sub-day as-of join.** Kalshi decision time I → latest option snapshot ≤ I − delay. Extend the `ObservationTables` as-of finder, or add a `join` mode.
4. **Graduate the Kalshi row readers:**
   - crypto_trading `MarketRows`, `CandleRows`, `DecisionRows`, `MarketState`;
   - pmquant `asks_from_bids`, `net_edge`, `FeeBook`.
   These move into a dskit Kalshi-rows module. **ADR**, since they move code out of two children.

**Child work:**
- `configs/run-kill-index.json` chains: `MarketRows` → `DecisionRows` (leads of 5/15/30/60 minutes before 16:00) → as-of option snapshot → `OptionPriceCDF` → `CurveBinaryFairValue` + `OptionDigitalBounds` → `FeeColumns` → `BucketedBinaryScore` → `RecordsWriteRun`.
- No child Python beyond a series-to-underlying map, if config cannot express it.

**Outputs:**
- the gap distribution `Kalshi_exec − band`, net of both venues' fees, by moneyness and lead time;
- Brier and log score of option-Q vs Kalshi mid on settled outcomes.

**Kill:** median executable gap below 1¢ after costs **and** option-Q has no log-score gain over the Kalshi mid. Then stop the S&P/Nasdaq leg and write a memo.

## Stage 2: point estimate and Q→P (only if Stage 1 passes)

1. **ADR — single-expiry smile fit.** Arbitrage-free SVI (Gatheral–Jacquier) or a Fengler-constrained spline in IV vs log-moneyness, emitted on the `GridCurve` contract so every downstream node works unchanged. Tails come from the existing `SemiparametricGPDTailCDF`.
2. Q→P: reuse `OptionImpliedTransportCDF`, or isotonic recalibration on settled outcomes conditioned on lead time and moneyness. **Walk-forward with embargo** (`rolling-origin-plan`); never fit on the evaluation day.
3. Score with RPS, log score, CORP reliability and PIT against both the Kalshi mid and the raw Q.

Done when the calibrated P beats both benchmarks out of sample, or a memo says it doesn't.

## Stage 3: Kalshi coherence scan (runs in parallel with Stages 1–2; Kalshi data only)

1. **ADR — `LadderCoherence`.** A `PyomoSolve` subclass with a non-capital role. Inputs are fee-adjusted bid/ask intervals of linked contracts. It reports:
   - LP feasibility (infeasible means riskless arbitrage);
   - the violating set and the executable size;
   - a weighted isotonic / KL projection of mids as a de-noised fair value.
   Relations are declared in config: partition sums, monotone thresholds, `bucket = above(L) − above(L+w)`.
2. Child config `run-coherence-index.json` for KXINX ↔ KXINXU and KXNASDAQ100 ↔ KXNASDAQ100U. The same node later serves nested hourly/daily series.

**Kill:** fewer than ~1 violation a week with at least 10 contracts of depth after fees, on the recorded books.

## Stage 4: maker simulation

1. **ADR — maker quoting proposer.** A production `Proposer` that rests quotes at fair ± a configurable margin, clipped inside the executable band. Logs `decision/order/fill/mark` events with mark-outs at 1 s/10 s/60 s/5 min and at settlement.
2. `PaperExecutor` on the recorded books, with `fill_rule: cross` and a conservative `queue_frac`. Uses the Kalshi maker fee via `fee_mechanics`.
3. Sizing: subclass `ScenarioUtilitySolve` with digital payoffs, following pmquant `KellyMIO`.

Done when net P&L after fees and mark-outs is reported with cluster-robust intervals by moneyness decile. Promotion to shadow is an owner decision.

## Stage 5: correlated-underlying dependence (gated on Stages 1–3)

1. **ADR — dependence pack.** An `OutcomeCalibrator` subclass that emits the existing `ScenarioSet`, so `ScenarioUtilitySolve` consumes it unchanged.
   - Gaussian and Student-t copulas over given marginals.
   - Correlation from realized or DCC estimates on intraday returns. Use SPY/QQQ minute NBBO from `alpaca_quotes`, or ES/NQ if a source is added.
   - Wraps a standard library (e.g. `scipy.stats`, `arch`), per the libs rule.
2. **S&P ↔ Nasdaq:** joint P of KXINX × KXNASDAQ100 buckets. Test:
   - Fréchet bounds;
   - conditional repricing — does one ladder lag the other after an index move?
3. **Weather** (cross-city highs) is a separate later track. It needs NBM/GEFS ensemble data plus Ensemble Copula Coupling. Scope it only if the S&P↔Nasdaq conditional test shows a lag worth trading.
4. Scores: variogram + energy score for the joint forecast; mark-outs for the trade.

## Sequence and effort (rough)

- Week 0: owner decisions 1–3; start the recorders; Stage 0 configs and runbook.
- Weeks 1–2: ADRs for Stage 1 items 1–4 and Stage 3 item 1, written and awaiting approval. Implement after approval with skeptic review.
- Week 3: kill-test and coherence runs, then memos.
- Later: Stages 2, 4 and 5 only on a pass.

## Done when

Stage 1 and Stage 3 each have a memo with an explicit pass or kill. Every new
capability lives in `dskit/` with an approved ADR. The child holds only
configs, a runbook, and at most a few lines of mapping code.
