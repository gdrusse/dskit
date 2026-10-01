Default answer: outcome first, max 5 lines. Expand only if asked.

# AGENTS.md — stock_options

This is the thin, standalone child for research on listed options on individual
stocks. Generic mechanisms belong in `dskit`; this child owns stock-specific
configuration, constraints and evidence. It must never import a sibling child.

## Study contract

- Keep the prediction and option action set separate. The first physical-return
  forecast uses point-in-time stock and market data only. Option strikes,
  premiums, implied volatility, open interest and chosen legs are not predictors.
- Train and select the forecast before the first scored option date. All labels
  must settle before that date, followed by an embargo at least as long as the
  maximum 45-calendar-day target horizon.
- Option-era data define the mixed-integer optimizer's point-in-time option
  action set and economics. They cannot select the predictive model or tune the
  optimizer from test outcomes.
- Use split-adjusted prices and scale-free features. Retain correctly adjusted
  split rows and genuine market shocks. Exclude only predeclared data-quality or
  contract-identity failures. Never pair adjusted historical price levels with
  unadjusted strikes.
- Label every stock-specific assumption. Earnings, dividends, early exercise,
  share assignment and adjusted deliverables differ from index options.
- AMZN is the first baseline and MSFT is the immediate liquidity replication.
  Neither choice is evidence of profitability.
- The bootstrap has no connector, strategy config, optimizer, accounting or
  execution path. Add reusable machinery upstream under its own approved ADR.
- `dskit` is the only project dependency. Keep this child position-independent.
- `journal.json` marks the child. Research must use `dskit.journal`; only the
  owner changes Path to Production.
- No paper or live order path is authorized.
