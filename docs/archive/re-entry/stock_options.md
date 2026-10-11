# RE-ENTRY archive — stock_options

Previous re-entry notes for this lane, newest first. `/wrap` moves the note it
replaces to the top of this file; nothing is ever edited or removed here. Not
read at session start. Older history: `2026-10-10-consolidated-history.md`.

---

## Conversion and feature-gap audits delivered (2026-10-01)

AMZN conversion and both QQQ/AMZN JSON feature-gap interfaces are complete.
Final candidate a0128bf3 passed two fresh independent lenses C0/M0/m0/N1;
only deferred Nit is copied QQQ prose in AMZN writer metadata. Code, identities,
outputs and predicates are correct. Final runtime/config/test/evidence blobs
are locked; subsequent edits are closure documentation and automatic journals.

Published exact gap files:
- children/index_options/pipeline_runs/qqq-feature-availability/feature-gaps.jsonl
- children/stock_options/pipeline_runs/amzn-feature-availability/feature-gaps.jsonl

Sibling family-gaps.jsonl, rows.jsonl, verification.json and runs/<run>/
{carry,config,result}.json provide family failures, complete retained rows,
hashes, source/clock caveats, counts and date windows. QQQ retains 1,497 dates:
71,880 feature-gap cells. AMZN retains 90 selected 42-DTE dates: nine CDF
fields complete; 146 candidate fields not materialized, 13,140 feature-gap
cells. These are prepared-data gaps, not proof all underlying sources are absent.

Both runbooks are children/<child>/docs/plans/README.md. Those two runner JSONs
are retired (their committed evidence stays); the ticker/source-neutral successors
are index_options/configs/run-step1-expiry-coverage.json and
run-step2-feature-availability.json. Use existing source
onboarding and fresh run_root/three writer paths for repeats; committed outputs
refuse overwrite. WSL2, project venv, hard6GiB/no-swap/1740sec limits.
Independent AMZN rerun byte-matched all three JSONL files in19.72sec/<1GiB.
Conversion yields915eligibleCDFrows/549dates; exact42-DTE winner90dates.

Next bounded task: materialize missing derivable families on the fixed QQQ
cohort, inspect source release/vintage clocks, rerun gaps before feature
selection/PCA/model training. AMZN returns/RV/OHLC need preparation; historical
trade bars lack historical quote/IV/OI fields and current data cannot fill them.


## Stock-options bootstrap accepted (2026-10-01)

- ADR-0212 is accepted and the exact thin `children/stock_options` bootstrap
  exists. It has no strategy code or backtest. AMZN is the baseline and MSFT
  the replication candidate.
- Pulled 7,390 AMZN price rows and 65,372 multiplier-100 daily option trade bars in
  the 30-45 DTE window. Historical bid/ask quotes remain unavailable for free.
- Memo: `docs/memos/2026-09-30-stock-options-source-and-starting-symbol.md`.
- Design: freeze the stock/market-only forecast in pre-option development, then
  use option-era data as the mixed-integer optimizer's point-in-time action set.
- Review lock: 449700a7 passed two independent skeptic lenses at C0/M0 and
  39 focused checks; later changes only merge current-main docs and record wrap.
- Limit: free bars lack complete historical action sets and bid/ask quotes, so
  executable P&L remains unsupported.
- Next: pull additional stock histories, then inventory the generic forecast,
  chain and optimizer seams before any strategy run.

