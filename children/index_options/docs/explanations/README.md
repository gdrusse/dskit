# Synthetic index-option condor

## TL;DR

This example checks the arithmetic of a limited-loss four-option position.
It does not predict prices, prove profit, or establish that quotes could fill.

## Terms and example

An option gives its buyer a contractual payoff. A put pays max(strike minus
settlement value, 0); a call pays max(settlement value minus strike, 0).
The strike is the contract's reference level. Expiry ends the contract.
European exercise is at expiry; cash settlement transfers money, not shares.
PM settlement uses the contract's declared afternoon settlement convention.
The multiplier converts index points into dollars; this invented product uses
100 dollars per point. One spread holds one of each of four legs.

An iron condor here buys the 480 put, sells the 485 put, sells the 515 call,
and buys the 520 call. Buying costs the ask (seller's quote); selling receives
the bid (buyer's quote). These invented quotes give:

1. Put-side credit = 4.20 received - 2.60 paid = 1.60 points.
2. Call-side credit = 2.80 received - 1.80 paid = 1.00 points.
3. Total credit = 1.60 + 1.00 = 2.60 points.
4. Entry cash = 2.60 * 100 * 1 spread = USD 260.
5. At settlement 500 all four intrinsic payoffs are zero.
6. Gross outcome = 260 + 0 = USD 260.
7. With USD 8 whole-outcome fees, net outcome = 260 - 8 = USD 252.

At 470, the bought put pays (480 - 470) * 100 = USD 1,000.
The sold put owes (485 - 470) * 100 = USD 1,500. Calls pay zero.
Net outcome = 260 + 1,000 - 1,500 - 8 = USD -248.
At 530 the call side similarly owes USD 500 net, yielding USD -248.

Each wing is five points wide. Maximum terminal loss before fees is
(5 - 2.60) * 100 = USD 240; after fees, USD 248. Unequal wings use the wider
wing for this bound. Positive cashflow is received; negative cashflow is paid.
Count scales all leg cashflows, while the configured fee is already the total.

Bid/ask sizes are quoted quantities, not guaranteed package fills. No margin,
early-exit, tax, financing or execution model is supplied. Known-at is a
synthetic availability claim; acquired-at is when DSKIT actually ingested it.
Their distinction matters before any historical research. Future tutorials
belong here and use the record-explanation procedure, not the research ledger.
