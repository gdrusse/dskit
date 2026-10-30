# MIO 2021 Q1: top three 2019 broad212 methods

Three independently funded simulated accounts, 2021-01-04 through 2021-03-31 (61 account sessions and 13 weekly decisions). Methods were selected by **2019** broad212 net profit. This is an **amended replay**: the first Q1 attempt failed on GME numerical integration; its failed receipt remains published in the shared registry. The approved retry changed only the projection integration tolerance from 1e-8 to 1e-6. All three methods completed with recorded replay parity.

The predictor is the accepted `selected_mlp64_s29` neural recipe fit and early-stopped on labels settled through 2019, followed by the causal M53 rolling blend. It was not refit on 2020 labels. LLY and SMCI had no admitted Q1 forecast but remained in the fixed 212-name universe.

## Reports

- [w1_003_shock10_lp](w1_003_shock10_lp/summary.md): **-$107.50** net; [HTML](w1_003_shock10_lp/report.html) · [61-day ledger](w1_003_shock10_lp/ledger.csv)
- [nominal_lp](nominal_lp/summary.md): **-$3,827.85** net; [HTML](nominal_lp/report.html) · [61-day ledger](nominal_lp/ledger.csv)
- [w1_003_lp](w1_003_lp/summary.md): **-$207.55** net; [HTML](w1_003_lp/report.html) · [61-day ledger](w1_003_lp/ledger.csv)

Each account received $16,000 in contributions. These are after-fee marked results from the saved MIO result, not the event chart's at-cost equity. The methods are separate accounts; their profits should not be summed as one strategy.

## Saved-field extracts

- [Trades](trades.csv)
- [Rejected orders](rejected-orders.csv)
- [Optimizer-selected and actually filled order legs](orders-and-fills.csv)
- [Daily account cash, reserve, holdings and marked equity](daily-account.csv)

For refused orders, a report ledger's selected-contract count may reflect a reduced fill-stage quantity. Use `selected_quantity` and `filled_quantity` in the order-leg CSV for original demand and execution. Each report includes the event totals gate, refusals, expiry detail, marked figures, and exact artifact links.

## Scope and evidence

Q1 execution and expiry stress scenarios were **not run**. The separate 2019 stress evidence does not apply to these trades. Simulated displayed-depth fills are not broker liquidity or proof of future profitability. The comparison node labels all non-fresh work generically as development; the actual registry permit is **amended_replay**.

The source receipt, source config/run and report config/run hashes and every mirrored file hash are in [artifact-index.json](artifact-index.json). The full attested 19.8 MB source result and original failed attempt remain in the local evidence root; this GitHub mirror preserves the generated report files and direct saved-field extracts.
