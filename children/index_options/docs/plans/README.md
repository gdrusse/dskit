# Research stages

S0: finite synthetic ingestion, exact contract/quote identity, condor expiry
cashflows, persisted artifacts, isolation and independent software review.
No profitability experiment is authorized by completing S0.

S1 needs separate owner approval for a bounded provider/sample budget and
adapter manifest. Verify PM series/expired-chain coverage, settlement source,
publication clocks, revision policy, quote quality, license/two-machine rights,
cost and storage. No provider has passed acceptance.

S2 needs an approved experiment and run authorization: forecast realized
variance or return distributions, compare simple baselines with ML, use
time-ordered overlap-purged validation and train-only transforms, calibrate
uncertainty, model costs and preserve untouched final evaluation.

S3: audit the existing Pyomo pack's credit-spread cash/margin semantics before
MIO allocation. Compare integer spread choices with a simple risk-budget
heuristic. Do not implement solver plumbing inside this child.

S4: independently approved broker, accounting, settlement/reconciliation,
risk limits, monitoring and authority contracts, with paper evidence before
live approval. RL hedging and deep models are optional later questions.
Neither model sophistication nor backtest profit guarantees an edge.

## Stock-options reuse audit (ADR-0211, proposed)

**Stock-specific:** the proposed work belongs in a separate thin
`children/stock_options` child. Stock options, earnings windows, contract
adjustments and
share assignment need separate checks. The existing ETF mechanics are reusable,
but the SPY/QQQ/IWM grid, VIX proxy and split refusal cannot be copied silently.

The accessible pilot source is Alpaca indicative. On 2026-09-30 its AMZN chain
returned 972 snapshots across 10 expiries (2026-10-02 through 2026-11-06);
21 raw AMZN IEX daily bars were also pulled. The two raw JSON files are outside
the checkout at /home/russell/data/stock_options/amzn/alpaca_indicative.
Their SHA-256 values are
8bb801d7efa5f036ef17dff44f1bc156df8470fce1263525122e61ff6aeb8d0f
(chain) and
a59c6b9d8991f5c2fdaf349103bb5bd999f549cdb9b4bce319d208c2b90bf72e
(stock bars). They carry no credential values.

A second free pull contains 7,390 field-complete AMZN daily price records from 1997-05-15
through 2026-09-30 (SHA-256
982acac92f17e808bdca33d4361069cd98646bfd272b06fb4b735cadef26ff66).
It is a Yahoo chart export stored outside the checkout at
/home/russell/data/stock_options/long_history_audit. It has complete OHLCV,
ordered unique dates and four split events. The values are split-adjusted, so
pre-2022 prices cannot be paired with raw historical option strikes. This is
price history, not option history, and its last record may be provisional.

The matching MSFT pull contains 5,934 field-complete daily records from
2003-03-03 through 2026-09-30, SHA-256
bcbcc4ccc491a1db21580a0a4cfc20b765b6c67c32ccd34a5a07fd94b74bfc8f.
It is stored beside AMZN. Both files contain all 5,030 expected XNYS sessions
from 2006-10-02 through 2026-09-30 and valid OHLCV. Across 1,553 overlapping
IEX sessions, adjusted closes were within 1%; this is a single-exchange sanity
check, not official-close reconciliation.

The free Alpaca tier enumerated 23,254 inactive AMZN contracts from February
2024 onward. Requiring the OCC `AMZN` root and source multiplier 100 retained
65,372 unique, valid daily trade bars across 8,892 symbols and 126 expiries in
the 30-45 DTE window, SHA-256
49d7b4f5da463b8b551c1673fc667e0a8bb3baebb4313e3b6b1ef0e1c52ff9c8.
These bars have no bid/ask. Historical executable-price work still requires an
entitled quote source. Proposed first cadence:
daily end-of-day chain and stock closes, weekly entry review, 30–45-day
same-expiry condors with nonoverlapping positions. Minute bars and faster
sampling wait for an execution question and suitable data access. No code or
strategy run follows until ADR-0211 is approved.

Use AMZN first and MSFT as the immediate comparator. All accessible AMZN option
history is post-split and Amazon has paid no dividends, simplifying the first
assignment study. In the two available 30/37-DTE expiries, MSFT had tighter
median spreads and greater volume. The 44-DTE request returned no free snapshot
for either symbol; retained artifacts do not establish active-contract coverage.
AMZN is the operationally simpler baseline; MSFT is the liquidity benchmark. This is not evidence of a profitable strategy.
