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
| `liquidation.py` | `LiquidationTax`, `HoldVsRedeploy` | `tests/pipeline/test_liquidation.py` (14) |
| `hold_return.py` | `MarketHoldReturn`, `BetaHoldReturns` | `test_hold_return.py` (7) |
| `active_hurdle.py` | `ActiveHurdle` | `test_active_hurdle.py` (7) |
| `decision_outcomes.py` | `DecisionOutcomes` | `test_decision_outcomes.py` (5) |
| `node.py` | `check_number_param` | `test_node.py` (11 parametrized) |

Tier 2, `dskit/onboarding/libs/schwab.py`: `SchwabPositionsConnector`
(`schwab_positions`) on a shared `_SchwabConnector` base with the bars
pack; `tests/onboarding/test_schwab_positions.py` (7), bars tests unchanged
and green (8).

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
   is below `min_dsr`. Then `redeploy_net = net x (1 + (r_active - cost -
   premium) x (1 - short rate))^H` under `annual` tax timing.
4. **Decision.** `exit` only when `redeploy_net > hold_net`; otherwise, and
   whenever the hurdle was withheld, `hold`.

Later, `DecisionOutcomes` scores step 2 against a later snapshot: projected
growth pro rata `(hold_value / value)^(elapsed / H)` vs realized per-share
growth; the active side is scored where it runs.

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
- Lens 2 also executed the README sequence literally from a scratch child
  (exit 0, rows written, execute row journaled) and `pipeline validate` on
  both documents (OK).

## Checks

- `python -m pytest tests/pipeline/test_liquidation.py tests/pipeline/test_hold_return.py
  tests/pipeline/test_active_hurdle.py tests/pipeline/test_decision_outcomes.py
  tests/onboarding/test_schwab_positions.py tests/onboarding/test_schwab.py
  tests/docs -q` → green (see commit message for the count).
- `cd children/portfolio_policy && python -m pytest tests -q` → 58 passed, 9 skipped
  (skips: skeleton conformance for kinds the child does not declare).
- Full `tests/pipeline tests/onboarding tests/docs tests/children` run on
  `b7e85fb`: only pre-existing or environmental failures — `alpaca` optional
  dependency absent, a root-user chmod test, a Python 3.13 dataclass case in
  `test_uncertainty_set`, the `parquet.py` purity gate, and `crypto_trading`'s
  own suite (182 failures on `main` before this branch).

## Disclosed backlog (Minor / Nit, not fixed)

- `test_active_hurdle` pins E[max null Sharpe] from one recorded evaluation
  rather than a closed form (the owner formula has its own tests).
- The hurdle's arithmetic per-period mean is compounded geometrically
  downstream (~sd²/2 per period overstatement, a few bp/yr; left to
  `risk_premium`).
- Refusal lines without a dedicated test: several range/name checks in
  `HoldVsRedeploy` and `MarketHoldReturn`, `_hashes` non-list, positions
  `read` unknown-stream/mode, base `_request` HTTP-status mapping.
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
