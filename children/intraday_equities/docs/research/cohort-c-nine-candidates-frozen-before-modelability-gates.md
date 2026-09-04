# Cohort C: nine candidates frozen before modelability gates

## Finding

Nine additions were selected before inspecting any candidate's Gate 1–3 result: **UPRO, BAC, AMZN, AVGO, NFLX, MSFT, GOOGL, SMH, IWM**. META was removed before registration or acquisition. These are candidates, not declared winners; the gates decide whether each stock or ETF is modelable.

## Frozen selection rule

Selection used only non-backtest facts: US listing, continuous ticker and useful history from 2016, Alpaca SIP coverage, high liquidity relative to likely one-to-five-minute movement, and a distinct market mechanism or useful lead-lag relationship. The set deliberately includes broad-market leveraged flow, financials, mega-cap information leaders, semiconductors, media, and small-cap breadth. No validation returns, gate scores, or downstream trading results informed the choice.

Ranked rationale:

1. **UPRO** — daily 3x SPY exposure supplies a scheduled reset/rebalancing mechanism.
2. **BAC** — liquid, continuous since 2016, and strengthens the JPM/XLF financial block.
3. **AMZN** — liquid retail/cloud information leader.
4. **AVGO** — adds a semiconductor leader beside NVDA and AMD.
5. **NFLX** — liquid, volatile, event-sensitive media name.
6. **MSFT** — market/technology leader for AAPL, QQQ, and XLK relationships.
7. **GOOGL** — liquid communication/technology leader with continuous GOOGL history.
8. **SMH** — direct semiconductor-sector state for NVDA, AMD, and AVGO.
9. **IWM** — liquid small-cap and risk-appetite state absent from the existing set.

## Acquisition evidence

The journaled `alpaca-sip-split-c` backfill used SIP, split adjustment, one-minute bars, gzip, start `2016-01-01`, and exclusive end `2026-02-28T23:59:59+00:00`. Source-config hash: `f7a6cc31f75fc7a0ac885d0b920ceb7ccbdc4cba8d428e073c42813700f31812`; snapshot: `aaa9e1d7f979e9cb282e99aedf26fc747f4729e6d2ae37da3d42958edba563f8`.

The immutable snapshot contains **12,213,670** bars, zero skipped records, all nine expected symbols, no unexpected symbols, no META, and zero timestamps at or beyond the cut. Source-scoped hash verification checked one snapshot with zero problems; raw and normalized gzip CRCs passed. Counts were AMZN 1,485,504; AVGO 1,152,495; BAC 1,433,450; GOOGL 1,267,305; IWM 1,636,465; MSFT 1,472,632; NFLX 1,268,612; SMH 1,094,861; UPRO 1,402,346. Every symbol has usable 2016 observations; BAC has 143,127 bars in 2016.

## Caveat

Liquidity and plausible structure make these efficient experiments, not predictable assets by assumption. Minute-scale effects are small, transaction costs matter, and bar data omits much order-flow information. Apply the same preregistered horizons, multiplicity correction, and shuffle/retrain gate to every candidate.

## Sources

- [Alpaca historical stock data: SIP consolidates all US exchanges](https://docs.alpaca.markets/us/docs/historical-stock-data-1)
- [Chinco, Clark-Joseph, and Ye: one-minute cross-stock lagged-return signals](https://www.nber.org/papers/w23933)
- [Ait-Sahalia et al.: high-frequency predictability and market microstructure](https://www.nber.org/papers/w30366)
- [Gao et al.: intraday momentum in actively traded ETFs](https://www.sciencedirect.com/science/article/pii/S0304405X18301351)
- [ProShares UPRO objective, liquidity, and 2009 inception](https://www.proshares.com/our-etfs/leveraged-and-inverse/upro)
- [VanEck SMH objective and 2011 inception](https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/)
- [iShares IWM liquidity and 2000 inception](https://www.ishares.com/us/products/239710/ishares-russell-2000-etf)
- [Bank of America 2016 Form 10-K](https://www.sec.gov/Archives/edgar/data/70858/000007085817000013/bac-1231201610xk.htm)
- [Nasdaq-100 March 2026 constituents](https://indexes.nasdaq.com/docs/FS_NDX.pdf)
