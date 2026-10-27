# ADR-0279 base machinery — build and review record (2026-10-27)

## TL;DR

The hold-vs-redeploy framework is built end to end and passed two independent
skeptic lenses (0 Critical, 4 Major found and fixed). Nothing has touched a
real Schwab account yet: every number in the two documents is a placeholder
the owner sets before the first real run, and both end-to-end tests run on a
synthetic onboarding root with `localfiles` standing in for the Schwab pull.

## Execution contract

- Branch `portfolio-policy-scaffold`, base `a16c2bd` (main), candidate head
  as committed after this memo (see `git log`), author Claude Fable 5.1.
- Owner authorizations in this session: "work autonomously" (2026-10-27
  11:00), "permission to build" (11:13), "build all the machinery" (11:19),
  "I want to be able to use this skeptic reviewed library asap" (12:04).
- No live orders, no real pull, no lockbox, no backtest run. Research child.

## What was built (implementation evidence)

Tier 1, `dskit/pipeline`, stdlib + core, all by import path, all
`serving_effect` "pure":

| Module | Kinds | Tests |
|---|---|---|
| `liquidation.py` | `LiquidationTax`, `HoldVsRedeploy`, `require_number`, `MAX_COUNT` / `check_count_param` | `tests/pipeline/test_liquidation.py` (38 tests, 109 cases) |
| `hold_return.py` | `MarketHoldReturn`, `BetaHoldReturns` | `test_hold_return.py` (21 tests, 92 cases) |
| `active_hurdle.py` | `ActiveHurdle` | `test_active_hurdle.py` (20 tests, 51 cases) |
| `decision_outcomes.py` | `DecisionOutcomes`, `annualized_growth`, `check_key_fields_param` | `test_decision_outcomes.py` (19 tests, 50 cases) |
| `position_summary.py` | `PositionSummary` (research A0002) | `test_position_summary.py` (25 tests, 49 cases) |
| `node.py` | `check_number_param` | `test_node.py` (12 parametrized) |

Tier 2, `dskit/onboarding/libs/schwab.py`: `SchwabPositionsConnector`
(`schwab_positions`) on a shared `_SchwabConnector` base with the bars
pack; `tests/onboarding/test_schwab_positions.py` (22 tests, 28 cases, five of
them over the real transport with `urllib.request.urlopen` scripted),
`test_schwab.py` (11 tests, 15 cases: the original 8 plus the transport cases
keeping the "Schwab price history" nouns and the URL-encoding case).

Tier 3, `children/portfolio_policy` (JSON only): `configs/run-hold-vs-redeploy.json`
(positions → snapshot filter → tax; market → betas; active_returns →
hurdle; → decide → `decisions/<run>.jsonl`) and
`configs/run-decision-outcomes.json` (decisions via `localtables` + a later
snapshot → outcomes → `outcomes/<run>.jsonl`); `tests/test_hold_vs_redeploy.py`
runs both in their shipped shape; `tests/test_configs.py` pins the decision
day declared twice in one document.

Research A0001 (`docs/research/hold-return/2026-10-27-synthesis.md`) grounds
the hold-return model: building blocks (yield + growth + inflation + CAPE
drift) scaled by beta, never a trailing mean.

## The decision rule, in plain terms

For each holding on one snapshot day:

1. **Tax of selling today.** `gain = value - basis`; `tax = gain x rate`
   (long or short rate by holding class; a loss pays nothing unless
   `loss_offset`); `net = value - tax`. Example from the e2e test: VTI value
   2500, basis 2000, long rate 0.238 → tax 119, net 2381.
2. **What holding is worth at the horizon.** `hold_value = value x (1 + r_hold)^H`,
   with `r_hold = r_f + beta x (r_market - r_f)` and `r_market = dividend
   yield + real growth + inflation + ((fair CAPE / CAPE)^(1/N) - 1)`; then the
   gain over the original basis is taxed once: `hold_net = hold_value -
   max(hold_value - basis, 0) x terminal rate`.
3. **What the active alternative may claim.** From the backtest's per-period
   net returns: `r_active = (mean - E[max null Sharpe over trials] x sd) x
   periods per year`, withheld (`None`) when the deflated Sharpe probability
   is below `min_dsr` (and refused outright for a constant series, which has no
   Sharpe). Then, under `annual` tax timing and with `t` the short-term rate:
   `net_rate = r_active - cost`; `taxed = net_rate - max(net_rate, 0) x t` (a
   negative rate gets no refund); `used = taxed - premium` (the premium is
   never tax-shielded); `redeploy_net = net x (1 + used)^H`. Under `terminal`
   timing the rate is `net_rate - premium` and the gain over the after-tax
   start is taxed once at the horizon.
4. **Decision.** `exit` only when `redeploy_net > hold_net`; otherwise, and
   whenever the hurdle was withheld, `hold`.

Later, `DecisionOutcomes` scores step 2 against a later snapshot: projected
price growth `((hold_value / value)^(1/H) - income_rate)^elapsed` (the forecast
is a total return and the snapshot's value is price only, so the income is
removed first; `income_rate` is the market dividend yield) vs realized
per-share growth; the active side is scored where it runs.

## Review evidence

- **Lens 1** (correctness/authority, Claude general-purpose subagent, on
  `b7e85fb`): 3 Major, 0 Critical, 16 Minor, 6 Nit. Majors: the decision
  document read every snapshot; the redeploy path was taxed once at the
  long-term rate; outcomes joined on symbol alone. All fixed in `5b932df`
  with the Minors that were defects (shared owners, zero/variance/rate
  refusals, Schwab `hashValue` and required numbers, retry dedup).
- **Lens 2** (test-quality/integration, independent subagent, on `5b932df`):
  1 Major (a test asserted the wrong refusal; the one-date refusal was
  untested), 0 Critical; 20 arithmetic mutations killed, 13 survivors all
  in untested refusal/summary lines. Fixed in the closing commit: the
  one-date and zero-quantity refusals, `check_number_param` bounds,
  `min_dsr` inclusivity, short-position basis and UTC day, the e2e
  decision asserted by hand (hold_net 2680.58 vs redeploy_net 3052.07),
  the decision day pinned inside the document, README status and the
  outcomes sequence.
- **Sonnet loop** (a Sonnet reviewer, fix rounds by Claude Sonnet 5.5, branch
  `portfolio-policy-sonnet-loop`). Round 1: 5 Major fixed in `2f5cad9` (short and
  zero rows, wired-rate bounds, `min_periods`, `horizon_years` vs
  `reversion_years`, `income_rate`). Round 2: 2 Major fixed in the round-2 commit:
  (1) one undecided short or zero-quantity row in the later snapshot refused the
  whole scoring run, fixed by `later.where` filtering equity longs AND by
  `DecisionOutcomes` reading value and quantity only for decision-matched keys
  (structural one-date and duplicate-key refusals still cover every row); (2)
  single-value fixtures, now with a second value each for `horizon_years` 3,
  `periods_per_year` 12, `risk_free` 0.02, `long_term_days` 366 and
  `default_holding` "short" (each shown to fail when its parameter is hardcoded).
  With the Minors: `RATE_BOUNDS` (-1, 2), bars/positions error nouns restored,
  one `require_number`, child e2e expectations read from the documents, Limits
  disclosed, the `risk_premium` note. Round 3: 1 Major fixed in this commit, the
  hardcoding family swept to the end (`MarketHoldReturn` yield / growth /
  inflation, both `LiquidationTax` rates, `HoldVsRedeploy`'s terminal and annual
  rates, and every field-name param, each shown to fail when hardcoded in a
  scratch copy), plus Minors: a constant series (`[0.0123] * 52` read as an
  admissible 64% hurdle) refuses against `stats.NO_VARIANCE_RTOL`; an acquisition
  date after `as_of` refuses; `DecisionOutcomes` reads `horizon_years` through
  `require_number`; `MAX_COUNT` (10**6) refuses a huge `reversion_years`, `trials`
  or `periods_per_year` before an OverflowError; one parametrized bad-declaration
  test per node pins every `validate_params` refusal line (every refusal statement
  in the four modules and the positions pack is now killed by a test, checked by
  neutering each in turn). Details: the "Sonnet round 3 corrections" amendment in
  the decision log.
- **Position summary** (`dskit/pipeline/position_summary.py`, research A0002; Opus
  build `84eba56`, merge `b82e886`, then its own Sonnet loop). Round 1: 2 Major
  fixed in this commit (a loss and a zero-basis row, and both kinks of the
  break-even, were untested: now hand-worked through `LiquidationTax` and tied back
  through `HoldVsRedeploy`), 5 Minor and the Nits; each mutant (row or portfolio
  gain floored or un-netted, branching on `g` for `g + premium`, a wired `None`
  table read as unwired, horizon and `why` guards) survived the old tests and is
  killed now. `annualized_growth` and `check_key_fields_param` have one owner
  (`decision_outcomes`). Tests: `test_position_summary` 25 tests / 49 cases (was 14 / 19),
  `test_decision_outcomes` 19 / 50 (was 17 / 48), `test_liquidation` 38 / 109 (was 37 / 108).
- Lens 2 also executed the README sequence literally from a scratch child
  (exit 0, rows written, execute row journaled) and `pipeline validate` on
  both documents (OK).

## Checks

- `python -m pytest tests/pipeline/test_liquidation.py tests/pipeline/test_hold_return.py
  tests/pipeline/test_active_hurdle.py tests/pipeline/test_decision_outcomes.py
  tests/onboarding/test_schwab_positions.py tests/onboarding/test_schwab.py
  tests/pipeline/test_node.py tests/docs -q` → 403 passed after round 3 (147 after round 2,
  126 before it; most of the growth is parametrized bad-declaration cases).
- `python -m pytest tests/pipeline/test_position_summary.py tests/pipeline/test_decision_outcomes.py
  tests/pipeline/test_liquidation.py tests/pipeline/test_hold_return.py tests/docs -q` → 304 passed
  after position-summary round 1 (271 before).
- `python -m pytest tests/onboarding -q -k schwab` → 44 passed, 4 skipped.
- `cd children/portfolio_policy && python -m pytest tests -q` → 67 passed, 9 skipped (65 at merge `b82e886`, before position-summary round 1)
  (skips: skeleton conformance for kinds the child does not declare); both documents
  `validate` clean.
- Full `tests/pipeline tests/onboarding tests/docs tests/children` run on
  `b7e85fb`: only pre-existing or environmental failures — `alpaca` optional
  dependency absent, a root-user chmod test, a Python 3.13 dataclass case in
  `test_uncertainty_set`, the `parquet.py` purity gate, and `crypto_trading`'s
  own suite (182 failures on `main` before this branch).

## Disclosed backlog (Minor / Nit, not fixed)

Round 4 of the Sonnet loop (reviewer on `75fe315`) returned CLEAN — 0
Critical, 0 Major — closing the loop; its Minors, left open and disclosed:

- A snapshot date matching no rows runs to "RAN" and writes an empty
  `decisions/<run>.jsonl`; the scoring document refuses an empty snapshot,
  so the two disagree. Fix: refuse empty `records` in `LiquidationTax` and
  `HoldVsRedeploy`.
- Summary aggregates (`redeploy_net` total, `stage`, `rows`, `min_dsr`,
  `admissible`, `observed_as_of`) are unasserted; `advantage > 0` survives a
  mutation to `> 1.0` (no test with a small positive advantage).
- Live-mode same-day re-pulls: a symbol missing from the second pull persists
  from the first for that day (docstring overclaims; README uses backfill).
- Huge ints (`10**400`) for `cape`, `fair_cape`, `betas`, `default_beta` or a
  row value raise a bare `OverflowError` rather than a named refusal.
- Shared Schwab transport: default `max_retries`, each `_RETRY_STATUSES`
  entry, the 2xx range edges and the `secret` flags are unpinned.

- `test_active_hurdle` pins E[max null Sharpe] from one recorded evaluation
  rather than a closed form (the owner formula has its own tests).
- The hurdle's arithmetic per-period mean is compounded geometrically
  downstream (~sd²/2 a year: tens of bp to about a point; left to `risk_premium`,
  which ships at a 0.0 placeholder the owner sets from the backtest's volatility).
- Limits, not modelled (also in the child README): every account is taxed as
  taxable; the hold path's dividends are untaxed; a split or spin-off is read as
  a return by `DecisionOutcomes`; the `EQUITY` filter's reach for ETFs and mutual
  funds is unverified until a real pull; filtered-out rows are not counted.
- Refusal lines without a dedicated test: none in the four ADR-0279 modules or the
  positions pack (the base `_request` HTTP-status mapping, `_hashes`, positions `read`
  arguments and every `validate_params` line are covered). The Phase-1
  `SchwabBarsConnector` (not ADR-0279) still has 11 untested refusals: config and
  `read` argument guards and candle shape.
- `ActiveHurdle`'s defensive "deflation undefined" refusal is reached only by a
  monkeypatched `deflated_sharpe_ratio`; no real series is known to trigger it.
- `long_term_days` cannot express "more than one year" across a leap day
  exactly; the document declares 366.
- Stage 4 scoring is `DecisionOutcomes` rows, not a `dskit.evaluation`
  render (ADR amended).
- A `dskit.production` serve document is deferred; the cadence is a weekly
  scheduled `pipeline run`.

## Deliberately not run

- Any Schwab OAuth session, `acquire` against the real API, or a real decision.
- Any child backtest to feed `active`/`returns`; the owner picks the child
  and declares its honest `trials`.

## Reproducibility and handoff

- Identity: both documents hash at validate (`python -m dskit.pipeline
  validate configs/<doc>.json` from the child root); change a placeholder
  and the hash moves on purpose.
- First real run: README "Hold vs redeploy" block, then "Scoring a past
  decision run" a week or more later.
- Status: merged to `main` (`c0e8989`) on owner authorization. A final-round
  convergence lens on the post-lens-2 delta (tests, one document value,
  docs; no module code) returned CLEAR; its one Minor — the scoring
  sequence this memo referenced was missing from the README — is fixed in
  the follow-up commit.
