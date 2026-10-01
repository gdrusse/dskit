# Stock options

Thin dskit child for research on option strategies on individual stocks. This
scope is stock-specific: earnings, dividends, early exercise, share assignment,
splits and adjusted deliverables must be handled explicitly.

## Current status

ADR-0212 approved the bootstrap; ADR-0213 adds JSON-driven offline archive
conversion using shared dskit connectors and nodes. It contains no runnable
strategy, optimizer, accounting or execution code. AMZN is the initial baseline and MSFT
is the immediate liquidity replication. Results cannot be read as evidence of
profitability.

## Research design

The first model predicts the physical terminal-return distribution from
point-in-time stock and market data. Training and development end before the
first scored option date, every label settles before that boundary, and a
45-calendar-day embargo separates development from testing.

During the test period, option data supply the mixed-integer optimizer's option
action set and decision-time economics: eligible standard contracts, expiries,
strikes, observed quotes or trades, size and costs. Options do not tune the
first predictive model. Free historical trade bars omit untraded strikes and
bid/ask quotes, so they support selection-mechanics and terminal-payoff studies,
not complete-action-set or executable-P&L claims.

Use split-adjusted stock history and scale-free returns or features. A correctly
adjusted split is retained, as are genuine market shocks. Exclusions require a
predeclared data-quality or contract-identity rule. Raw pre-split price levels
must never be paired with unadjusted option strikes.

## Boundary

`dskit` is the only dependency. Generic chain reading, forecasting, walk-forward
and optimization mechanisms graduate upstream under separate approved ADRs.
This child owns stock-specific configs, gates and evidence and never imports
`children/index_options` or another sibling.

See the repository memo
`docs/memos/2026-09-30-stock-options-source-and-starting-symbol.md` and ADR-0212.

Run the approved archive-to-CDF workflow from [the runbook](docs/plans/README.md).
