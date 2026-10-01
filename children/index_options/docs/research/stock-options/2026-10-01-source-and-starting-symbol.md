## Question

Which stock and free data should anchor the first separate stock-options study?

## Finding

Use AMZN as the baseline and MSFT as the replication. Retained daily-price
files contain 7,390 split-adjusted AMZN rows and 5,934 MSFT rows, including all
5,030 expected sessions since 2006-10-02. The free Alpaca tier yielded 69,043
valid AMZN daily option trade bars for the 30-45 DTE window from February 2024
onward, but no historical bid/ask quotes. Comparable current chains show enough
fresh four-leg breadth in all 10 expiries for both symbols. AMZN avoids MSFT's
dividend assignment dependency and its accessible options are post-split. No
historical option-performance claim is supported. Keep the new project in a
thin `stock_options` child, keep generic
mechanisms upstream, and refuse strategy runs until point-in-time earnings and
ex-dividend sources and assignment accounting are approved.

## Sources

- https://ir.aboutamazon.com/faqs/
- https://www.microsoft.com/en-us/investor/faq
- https://orats.com/near-eod-data
- https://orats.com/docs/historical-data-api
- https://docs.alpaca.markets/docs/historical-option-data
- `docs/memos/2026-09-30-stock-options-source-and-starting-symbol.md`
