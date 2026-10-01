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

## Horizon-coverage process check

The generic JSON horizon selector was reproduced independently on the existing
QQQ/SPY/IWM prepared panel; see the current runbook and AMZN source audit in
[the coverage plan](../../../index_options/docs/plans/README.md).
AMZN's supplied historical bars, contract metadata and current indicative chain
are raw inputs, not a historical prepared CDF/target panel. The automated
selector cannot produce genuine stock CDF-coverage counts from these files by
changing ticker alone. Upstream preparation and a validated source contract
remain prerequisites; no stock CDF result or raw-chain preparation code exists
in this bootstrap. This diagnostic does not change the stock/market-only
predictor contract above.

The owner subsequently requested the missing conversion layer. Proposed
ADR-0213 defines reusable archive normalization, expiry-close outcome labels,
and the index method's extracted price-slope CDF proxy behind standard JSON
nodes. Historical trade-price and current indicative-quote inputs retain
separate quality labels. Implementation awaits the repository's required ADR
approval; do not treat the preceding schema diagnosis as a permanent refusal
to support this source.
