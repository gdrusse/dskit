# 2019 MIO standardized reports

Eight reports from the completed available-size runs: **252 account days and
53 decision dates per method**. Every report passed the saved-result totals gate.
This publication reused all MIO results and all 56 existing stress cases; it ran
no MIO, predictive fit or stress replay.

## Read the results

The summaries open on GitHub. Download an HTML file to view its charts, daily
ledger, decisions, refusal reasons, optimizer detail and glossary in a browser.
For individual option legs and daily expiries use **Every leg and expiry**.
Each method/cohort is a separate account; do not combine returns across them.

- **limited36 / nominal_lp** — net profit $259.05; 45 filled books. [Summary](limited36/nominal_lp/summary.md) · [HTML (download to view)](limited36/nominal_lp/report.html) · [Daily ledger](limited36/nominal_lp/ledger.csv) · [Every leg and expiry](../mio-execution-stress-2019-available-size-20261029-v4/limited36/control/nominal_lp.md).

- **limited36 / w1_003_lp** — net profit $-568.65; 10 filled books. [Summary](limited36/w1_003_lp/summary.md) · [HTML (download to view)](limited36/w1_003_lp/report.html) · [Daily ledger](limited36/w1_003_lp/ledger.csv) · [Every leg and expiry](../mio-execution-stress-2019-available-size-20261029-v4/limited36/control/w1_003_lp.md).

- **limited36 / w1_003_shock10_lp** — net profit $-2,092.90; 10 filled books. [Summary](limited36/w1_003_shock10_lp/summary.md) · [HTML (download to view)](limited36/w1_003_shock10_lp/report.html) · [Daily ledger](limited36/w1_003_shock10_lp/ledger.csv) · [Every leg and expiry](../mio-execution-stress-2019-available-size-20261029-v4/limited36/control/w1_003_shock10_lp.md).

- **limited36 / w1_003_component25_lp** — net profit $-1,653.30; 10 filled books. [Summary](limited36/w1_003_component25_lp/summary.md) · [HTML (download to view)](limited36/w1_003_component25_lp/report.html) · [Daily ledger](limited36/w1_003_component25_lp/ledger.csv) · [Every leg and expiry](../mio-execution-stress-2019-available-size-20261029-v4/limited36/control/w1_003_component25_lp.md).

- **broad212 / nominal_lp** — net profit $67,911.40; 163 filled books. [Summary](broad212/nominal_lp/summary.md) · [HTML (download to view)](broad212/nominal_lp/report.html) · [Daily ledger](broad212/nominal_lp/ledger.csv) · [Every leg and expiry](../mio-execution-stress-2019-available-size-20261029-v4/broad212/control/nominal_lp.md).

- **broad212 / w1_003_lp** — net profit $34,930.55; 29 filled books. [Summary](broad212/w1_003_lp/summary.md) · [HTML (download to view)](broad212/w1_003_lp/report.html) · [Daily ledger](broad212/w1_003_lp/ledger.csv) · [Every leg and expiry](../mio-execution-stress-2019-available-size-20261029-v4/broad212/control/w1_003_lp.md).

- **broad212 / w1_003_shock10_lp** — net profit $96,028.30; 62 filled books. [Summary](broad212/w1_003_shock10_lp/summary.md) · [HTML (download to view)](broad212/w1_003_shock10_lp/report.html) · [Daily ledger](broad212/w1_003_shock10_lp/ledger.csv) · [Every leg and expiry](../mio-execution-stress-2019-available-size-20261029-v4/broad212/control/w1_003_shock10_lp.md).

- **broad212 / w1_003_component25_lp** — net profit $11,593.50; 42 filled books. [Summary](broad212/w1_003_component25_lp/summary.md) · [HTML (download to view)](broad212/w1_003_component25_lp/report.html) · [Daily ledger](broad212/w1_003_component25_lp/ledger.csv) · [Every leg and expiry](../mio-execution-stress-2019-available-size-20261029-v4/broad212/control/w1_003_component25_lp.md).

## Exact requested and filled quantities

- Limited36: [orders and fills](limited36/orders-and-fills.csv), [trades](limited36/trades.csv), [refused orders](limited36/rejected-orders.csv), [daily accounts](limited36/daily-account.csv).
- Broad212: [orders and fills](broad212/orders-and-fills.csv), [trades](broad212/trades.csv), [refused orders](broad212/rejected-orders.csv), [daily accounts](broad212/daily-account.csv).

Filter each CSV's `method` column. `selected_quantity` is the optimizer's original
requested leg quantity; `filled_quantity` is the executed quantity. A **book** is
one chosen group of option legs. The standard report treats that entire book as
one unit, so its trade `qty=1` is not one option contract. All closing trades in
these runs are expiry settlements, not early sales.

**Ledger caveat, also printed beside each ledger:** selection columns are grouped
by attempted fill date. For refused books, `selected_contracts` can reflect a
reduced fill-stage quantity. Four daily rows understate original demand by 30
contracts in total; cash, profit, actual fills and book counts are unaffected.
Use the exact orders-and-fills CSV above for original requests. Decision CSV
`detail` holds candidate-book and selected-book counts; its generic `candidates`
column does not represent the MIO opportunity count.

## Basis, source and stress

Use **Saved marked figures (authoritative)** for MIO marked TWR/drawdown. Generic
event charts/statistics value open books at cost between entry and expiry; those
figures can differ. Profit is net of execution fees. Intraday report times are
ordering slots over saved dates, not observed intraday execution timestamps.

The inherited predictive input is the previously accepted **selected_mlp64_s29**
recipe, with distinct pinned H1/H2 producers and combined saved forecasts. This
publication did not re-rank or refit it. Original producer receipts do not attest
the original output bytes; current saved payload bytes are separately pinned.
Each report's identity table names those sources and its reused coverage.

[Existing stress report](../mio-execution-stress-2019-available-size-20261029-v4/report.md)
contains eight exact zero-shock controls and 48 stressed cases. All 56 completed.
The HTML/summary stress sections retain original local evidence paths; this index
supplies GitHub links. Pointwise expiry shocks are conditional scenarios, not a
coherent market path or proof of broker execution.

## Reuse and reproducibility

Use `$mio-report` with MIO method(s), cohort and date range(s). It defaults to the
prior accepted predictive model unless explicitly overridden. Fully covered
requests reuse results. The existing MIO engine cannot resume an account across
missing intervals, so a continuous partial extension needs an explicit account
choice; the skill will not silently reset cash or rerun covered dates. The current
reporter also has no bounded subrange view; it identifies requested rows in a
clearly labelled full-source report.

[Artifact hashes and run identities](artifact-index.json) ·
[Verification](verification.json) ·
[Canonical skill](../../../../../docs/skills/mio-report.md) ·
[Follow-through memo](../../memos/2026-10-29-mio-report-skill.md).

Generated HTML, Markdown and CSV files are byte-identical mirrors of the saved
report outputs; this index is navigation. Source order/trade/account extracts are
also byte-identical. Actual HTML content/structure, Markdown and CSVs were
inspected. Browser automation was unavailable, so no pixel-level layout check is
claimed. Original MIO/stress evidence, viewer, Path and TDD are preserved.
