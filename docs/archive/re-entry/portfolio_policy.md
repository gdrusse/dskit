# Archive — portfolio_policy lane notes

<!-- archived 2026-10-28 at wrap (three Schwab slices integrated, final lens CLEAN) -->

# RE-ENTRY — portfolio_policy

Updated: 2026-10-28 by Claude Sonnet 5.5 (merge of `main`, risk inputs + tax lots, into `hold-report`).
Rewritten whole at every `/wrap`; the replaced note goes to `docs/archive/re-entry/portfolio_policy.md`.

## Now

On `main`: ADR-0279 base machinery and `PositionSummary` (A0002), both review loops closed
CLEAN, plus the risk-inputs and tax-lots slices (`ec50281`; neither lens-reviewed after their
merge). Branch `hold-report` adds the hold VERDICT slice (two Sonnet rounds) on top of them
and is not lens-reviewed since merging `main`. One decision document
runs both slices:

- RISK INPUTS (research A0003, ADR-0279 amendments "risk inputs"): tier 2 `schwab_daily`
  (closed daily candles as `bars` keyed `(symbol, ts)`, `ts` = the New York session date;
  today's never; live re-requests `overlap_days` 5); tier 1 `kinds_flow.DateOrderedGroupBy`
  (`max_lag_days` + `reference_date` refuse a stale or look-ahead group), `BetaHoldReturns`
  optional `betas` port, `ComputeFields` `integer`. Chain: bars -> benchmark (SPY) ->
  risk_features (252-session RMS vol, OLS beta) -> risk_day (`ts <=` decision day) ->
  risk_latest -> risk (x sqrt(252); Blume 0.67 raw + 0.33) -> vols / risk_betas ->
  `betas.betas` and `summary.volatilities`; `risks` writes `risk/<run>.jsonl`. No beta typed.
- TAX LOTS (research A0004, ADR-0279 amendments "tax lots"): tier 2 `schwab_transactions`
  (`trades` stream, `status` VALID only, `start` kept in STATE); tier 1 `tax_lots.py`
  (`TaxLots` FIFO, `LotPositions` reconciled per holding), `DecisionOutcomes.match_fields`.
  Chain: trades -> lots -> lot_positions (snapshot) -> tax (`acquired_field`) -> decide ->
  summary per lot (`lot_id`); outcomes per lot, matched per holding. `betas` still reads
  the snapshot (symbol rows); every lot of a symbol shares its wired beta and volatility.
- The decision day is in FIVE places, pinned by a test: `snapshot.where[as_of]`, `lots.as_of`,
  `tax.as_of`, `risk_day.where[ts]`, `risk_latest.reference_date`.
- VERDICT (ADR-0279 amendment "hold verdict"; two Sonnet rounds): tier 1 `HoldVerdict`
  (`hold_verdict.py`), `run-hold-report.json` (reads the published decisions and risk
  streams, summary per lot), `weekly.sh <day>` (positions pull, decide with the day set in
  all five places, publish, report). `report/<run>.md`: one line per lot; it needs the
  trades and bars pulls done first; a rerun of a day refuses (README says what to delete).
- README: one runbook, three Schwab pulls, `mkdir -p decisions summary risk`, then steps
  4-5 by hand or `bash weekly.sh <day>`; "Reading the verdict"; Limits cover both slices.

Round 1 of the tax-lots and risk-inputs Sonnet loops is fixed. No real Schwab pull yet; every
rate in the documents is a placeholder.

## Next

1. Merge-level review of `hold-report` (two independent lenses; the join of the risk chain,
   the lot path and the verdict page is the new surface), then merge it to `main`.
2. Owner: the first real run from the README runbook, recorded with the memo skill. On that
   first pull check: a holding with a recent split (Schwab's close adjustment is
   undocumented), ETF / fund `asset_type` in positions and trades (`COLLECTIVE_INVESTMENT`),
   the trades census, `lot_positions` `basis_gap_max`, and `risk/<run>.jsonl` `as_of`.

## Blocked / owner decisions

- Document placeholders: market blocks, `risk_free`, tax rates, horizon, `trials`, `min_dsr`,
  `min_periods`, `redeploy_cost`, `risk_premium`, the decision day, the trades `start`
  (pinned pairs are tested).
- Lot method per account: FIFO ships; funds and some ETFs in accounts opened before October
  2024 default to average cost (not built); `basis_gap` shows a mismatch on the first run.
- `allow_incomplete` (ships false): how far back Schwab serves transactions is unverified;
  holdings older than it refuse until the owner allows the aggregate fallback or onboards a lot
  export. `lot_positions.tolerance` ships 0.0 shares (pinned).
- An INVALID (cancelled) transaction refuses the pull for good: skip it, or move `start` past
  it? Wash sales are not modelled (they show as a `basis_gap`).
- Deferred, owner may want: a default volatility for a holding without a year of bars; split
  detection.
- Which child's backtest feeds `active` / `returns`, and its honest `trials`.
- Cadence: a weekly scheduled `pipeline run` vs a `dskit.production` serve document (deferred).

## Read first

- `README.md` ("First real run from Schwab", Limits)
- `docs/memos/2026-10-27-adr-0279-base-machinery.md`
- `docs/architecture/decision-log.md` § ADR-0279 and amendments (last: hold verdict)
- `docs/research/risk-inputs/2026-10-28-synthesis.md` (A0003)
- `docs/research/tax-lots/2026-10-28-synthesis.md` (A0004)

## Do not

- No live orders, ever (research child; decisions are advisory).
- No real Schwab pull before the owner sets the document values.
- Do not type a beta or a return into the document: all arrive wired.
- Do not change the decision day in one place only (five, pinned by a test).

<!-- archived 2026-10-28 (risk inputs from Schwab daily bars, branch schwab-risk-inputs) -->

# RE-ENTRY — portfolio_policy

Updated: 2026-10-27 by Claude Sonnet 5.5 (position-summary round 1). Rewritten whole at every `/wrap`;
the replaced note goes to `docs/archive/re-entry/portfolio_policy.md`.

## Now

ADR-0279 base machinery is on `main`: tier-1 kinds by import path, all
`pure` — `liquidation.py` (`LiquidationTax`, `HoldVsRedeploy`),
`hold_return.py` (`MarketHoldReturn`, `BetaHoldReturns`),
`active_hurdle.py` (`ActiveHurdle`), `decision_outcomes.py`
(`DecisionOutcomes`); tier-2 `schwab_positions`; the child is JSON:
`run-hold-vs-redeploy.json` (positions → equity longs → tax; market →
betas; active_returns → hurdle; → decide → `decisions/<run>.jsonl`) and
`run-decision-outcomes.json` (→ `outcomes/<run>.jsonl`). Review history:
two skeptic lenses, then a Sonnet review/fix loop of four rounds (5 → 2 →
1 → 0 Major; 0 Critical throughout), round 4 CLEAN on `75fe315`: 403
dskit tests + 63 child tests green; mutation sweeps left no surviving
refusal or hardcode in the ADR-0279 modules. Record and backlog:
`docs/memos/2026-10-27-adr-0279-base-machinery.md`. No real Schwab pull
yet; every rate in the documents is a placeholder, and
`decide.risk_premium` 0.0 is one to set from the backtest's volatility.

In flight: branch `portfolio-summary` (Opus, research A0002 +
`dskit/pipeline/position_summary.py`, `PositionSummary`: weight, expected
return, beta, volatility (port only), embedded gain, tax cost, break-even
return and opportunity cost per holding, one portfolio roll-up in `metrics`;
the document's `summary` node writes `summary/<run>.jsonl`). `main`'s four
Sonnet rounds are merged into it. Its own Sonnet loop: round 1 fixed 2 Major
(loss and zero-basis rows, the break-even kinks; 304 dskit + 67 child tests
green); round 2 review is next, and it stays off `main` until the loop is clean.

## Next

Merge `portfolio-summary` after its Sonnet loop is clean. Then the owner
sets the decision document's placeholders (blocks, betas, tax rates — pinned
pairs are tested — `trials`, `min_dsr`, `min_periods`, `risk_premium`, the
snapshot day), chooses the backtest that feeds `active`/`returns`, and runs
the README sequence for the first real snapshot + decision; record it with
the `memo` skill.

## Blocked / owner decisions

- Volatility source for the summary: yahoo chart file per symbol vs Alpaca
  daily bars; 252-day window on adjusted closes proposed (then wire
  realized_vol -> filter -> ComputeFields -> keyby into `summary`).
- Lot-level data: a Schwab lot CSV through `localfiles` (then declare
  `acquired_field`) vs aggregate-only.
- Which child's backtest feeds `active` / `returns`, and its honest `trials`.
- Cadence: a weekly scheduled `pipeline run` vs a `dskit.production` serve
  document (deferred: advisory, no order path).

## Read first

- `README.md`
- `docs/memos/2026-10-27-adr-0279-base-machinery.md`
- `docs/architecture/decision-log.md` § ADR-0279 and amendments
- `docs/research/hold-return/2026-10-27-synthesis.md`
- `docs/research/position-summary/2026-10-27-synthesis.md`

## Do not

- No live orders, ever (research child; decisions are advisory).
- No real Schwab pull before the owner sets the document values.
- Do not paste a return into either document: both arrive wired.

<!-- archived 2026-10-27 at wrap (Sonnet loop closed) -->

# RE-ENTRY — portfolio_policy

Updated: 2026-10-27 by Claude Sonnet 5.5. Rewritten whole at every `/wrap`;
the replaced note goes to `docs/archive/re-entry/portfolio_policy.md`.

## Now

ADR-0279 approved 2026-10-27 with four amendments (owner: "work
autonomously", "permission to build", "build all the machinery").
Base machinery complete in dskit, all tier-1 by import path, all `pure`:
`onboarding/libs/schwab.py` (`schwab_positions`), `pipeline/liquidation.py`
(`LiquidationTax`, `HoldVsRedeploy`), `pipeline/hold_return.py`
(`MarketHoldReturn`, `BetaHoldReturns`), `pipeline/active_hurdle.py`
(`ActiveHurdle`), `pipeline/decision_outcomes.py` (`DecisionOutcomes`).
Child is JSON only: `run-hold-vs-redeploy.json` (positions → equity longs →
tax; market → betas; active_returns → hurdle; → decide →
`decisions/<run>.jsonl`) and `run-decision-outcomes.json` (that file via
localtables + a later snapshot → `outcomes/<run>.jsonl`). Two skeptic lenses
passed, then a Sonnet loop. Round 1 (5 Major): short/zero rows refuse and the
snapshot drops shorts; wired and computed rates held to one `RATE_BOUNDS`;
`hurdle.min_periods` (52) required; `market.horizon_years` pinned to the
decision's and capped by `reversion_years`; `outcomes.income_rate` (0.013 =
the dividend yield) removes income from the projected growth. Round 2 (2
Major): the scoring run reads only decision-matched later rows (and `later`
filters equity longs), so an undecided short cannot refuse it; second-value
tests for the first hardcodable params. Round 3 (1 Major): that sweep finished
(every block, rate and field-name param, each shown to fail when hardcoded);
a constant return series and an acquisition date after `as_of` now refuse;
counts are capped at `MAX_COUNT` (10**6). Also: `RATE_BOUNDS` is -100%..+200%, bars/
positions errors keep their nouns, one `require_number`, the child e2e reads
rates from the documents, and the README lists Limits (taxable-only accounts,
untaxed hold dividends, splits, ETF `asset_type`, uncounted exclusions).
Record: `docs/memos/2026-10-27-adr-0279-base-machinery.md` and the decision
log. No real Schwab pull yet; every rate in the documents is a placeholder,
and `decide.risk_premium` 0.0 is one to set from the backtest's volatility.

## Next

Owner: set the decision document's blocks (dividend yield, CAPE, fair
CAPE; `outcomes.income_rate` must follow the yield, a test pins it), betas,
tax rates (`decide`'s two must follow `tax`'s, pinned too), `trials`, `min_dsr`, `min_periods` and the snapshot day; choose
which child's backtest feeds `active`/`returns`; run the README's command
sequence for the first real snapshot + decision, then record it with the
`memo` skill.

## Blocked / owner decisions

- Lot-level data: a Schwab lot CSV through `localfiles` (then declare
  `acquired_field`) vs aggregate-only; both work, lots are more accurate.
- Which child's backtest feeds `active` / `returns`, and its honest `trials`.
- Cadence: a weekly scheduled `pipeline run` vs a `dskit.production` serve
  document (deferred: advisory, no order path).

## Read first

- `README.md`
- `docs/memos/2026-10-27-adr-0279-base-machinery.md`
- `docs/architecture/decision-log.md` § ADR-0279 and amendments
- `docs/research/hold-return/2026-10-27-synthesis.md`

## Do not

- No live orders, ever (research child; decisions are advisory).
- No real Schwab pull before the owner sets the document values.
- Do not paste a return into either document: both arrive wired.


Append-only; newest first.
