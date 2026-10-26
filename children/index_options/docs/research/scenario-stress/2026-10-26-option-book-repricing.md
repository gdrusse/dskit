## Question

How should a small JSON-defined European option book support scenario and stress tests?

## Finding

CME describes option risk scenarios as combinations of underlying price, implied-volatility and time changes, valued as hypothetical contract gains or losses. Its broader what-if tool also varies rates. Basel guidance calls for full portfolio revaluation for nonlinear option exposures. Use one explicit baseline forward/rate and named, signed option legs; each JSON scenario changes forward, IV, elapsed time and rate, then reprices every leg. Report the per-leg and total change from the model baseline and the worst loss among only the declared scenarios. This is a deterministic what-if diagnostic: it does not estimate scenario probability, tail coverage, fills, fees or margin.

## Sources

- [CME SPAN methodology overview](https://www.cmegroup.com/solutions/risk-management/performance-bonds-margins/span-methodology-overview.html)
- [BIS Basel stress-testing guidance on full revaluation](https://www.bis.org/committees/bcbs/basel-framework/standard/mar/99/inforce/2019-12-15/published/2019-12-15)
