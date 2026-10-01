# Plans

## Approved bootstrap

ADR-0212 created this thin stock-specific child. The next bounded study trains
and selects a stock/market-only physical forecast before the option period, then
uses option data only as the mixed-integer optimizer's point-in-time option
action set and economics.

Use split-adjusted prices and scale-free features. Retain correctly adjusted
split rows and genuine shocks; exclude only predeclared data-quality or
contract-identity failures. Apply the earnings, dividend, assignment and
adjusted-deliverable gates documented in ADR-0212.

No runnable config is approved here. First inventory the generic forecast,
walk-forward, chain and optimizer seams. Any missing reusable mechanism belongs
in `dskit` under a separate ADR and focused tests.
