# P3(b): cross-stock, market and sector inputs for 1-60 min returns

Date: 2026-09-03. Research only; nothing run. Feeds P3 in docs/plans/2026-09-horizon-search.md.

## Question

Do market, sector and peer inputs push the horizon at which we beat the mean past the two-bar wall (H<=2)? What extra symbols are worth pulling, and what would need data we do not have?

## Finding (short)

The index-to-stock lead is real but, for large caps, mostly used up inside 1-5 minutes; it can plausibly move the wall from 2 to ~5 bars, not to 30-60. The one cross-market input the literature ties to a 5-60 min horizon is reversal of the stock's own market- and sector-hedged residual (temporary liquidity imbalances lasting under an hour). Peer lagged returns are sparse and short-lived; with four peers they will overfit. The big missing piece is order-flow imbalance, which is nearly all contemporaneous and dies within minutes anyway.

## Deliverable 1: ranked shortlist (all from 1-min bars; r = log close ratio)

Symbols now in the store: AAPL JPM XOM WMT LLY SPY. Pull: XLF (JPM), XLV (LLY), XLE (XOM), XLK (AAPL), XLP (WMT), QQQ. Alpaca SIP bars, same onboarding path.

| # | column | formula | lookback | extra symbols |
|---|---|---|---|---|
| 1 | `res_spy_cum_{30,60}` | sum_{k<W} (r_own[t-k] - beta_t r_SPY[t-k]); beta_t = rolling cov/var over 3900 bars | 30, 60 bars | none |
| 2 | `res_sec_cum_{5,30}` | sum_{k<W} (r_own[t-k] - r_ETF[t-k]), ETF per row above | 5, 30 bars | XLF XLV XLE XLK XLP |
| 3 | `spy_lag_{1..5}` | r_SPY[t-k], k=1..5 (today only contemporaneous `ref_ret_SPY` enters) | 5 bars | none |
| 4 | `sec_lag_{0..2}`, `sec_cum_{5,30}` | r_ETF[t-k]; sums over 5 and 30 bars | 30 bars | as row 2 |
| 5 | `res_spy_lag_{1..5}` | beta-hedged residual at lags 1..5 (upgrade of `residual_SPY`, which is own minus SPY with beta=1) | 5 bars | none |
| 6 | `spy_open_ret` | log(SPY[t]/SPY[09:30 open]); market intraday momentum, first half-hour predicts last | session | none |
| 7 | `spy_rv_30`, `spy_rv_390` | std of r_SPY over 30 / 390 bars (market vol regime; interacts with everything) | 390 bars | none |
| 8 | `corr_spy_60` | rolling corr(r_own, r_SPY) over 60 bars (comovement regime) | 60 bars | none |
| 9 | `peer_res_lag_{1,2}` | mean over other tradables j of res_spy_j[t-k] | 2 bars | none (low rank: sparse, overfits) |
| 10 | `spy_qqq_gap_5` | 5-bar sum of r_SPY - r_QQQ (growth/defensive rotation; matters for AAPL, LLY) | 5 bars | QQQ |

Test order: 1, 2 alone first (they are the only 5-60 min candidates); then 3-5 (expect gain at H 3-5 only); 6-8 as regime conditioners for the trees; 9-10 last.

## Needs data we do not have

- Order-flow / quote imbalance, L2 depth: contemporaneous R2 65-84% but forward R2 at 1 min ~0, decays within minutes; effective horizon ~two price changes. Buildable later from Alpaca SIP trades+quotes, not bars.
- Microprice / queue imbalance: R2 30-48% at 5-second horizon; irrelevant at 1-60 min.
- Earnings / news timestamps: reaction done in 5-10 min; lagged returns of announcing stocks are the peers worth listening to. Needs a calendar with times.
- Short interest: daily/multi-day signal, nothing at minutes.

## Documented failures

- Spurious lead-lag from stale prints and nonsynchronous trading; frequently traded names appear to lead by construction; bid-ask bounce in both legs.
- Epps effect: 1-min correlations understate true comovement, so corr-regime inputs are noisy; lead-lag has shrunk over the years (fits on 2016 data will not hold in 2025).
- Peer lagged returns: predictors are sparse, unexpected and short-lived; with four peers and a fixed feature set this is noise.
- Cross-asset OFI adds nothing once own multi-level OFI is known; for us it is moot without OFI.

## Sources

- Chordia, Sarkar, Subrahmanyam, Liquidity dynamics and cross-autocorrelations (JFQA 2011): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1016117
- Chordia, Roll, Subrahmanyam, Speed of convergence to market efficiency (JFE 2005): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=600121
- Hou, Industry information diffusion and the lead-lag effect (RFS 2007): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1151155
- Cohen, Frazzini, Economic links and predictable returns (JF 2008): http://www.econ.yale.edu/~shiller/behfin/2006-04/cohen-frazzini.pdf
- Heston, Korajczyk, Sadka, Intraday patterns in the cross-section (JF 2010): https://arxiv.org/pdf/1005.3535
- Aleti, Bollerslev, Siggaard, Intraday market return predictability culled from the factor zoo (MS 2025): https://ideas.repec.org/a/inm/ormnsc/v71y2025i9p7731-7751.html
- Chinco, Clark-Joseph, Ye, Sparse signals in the cross-section of returns (JF 2019): https://www.nber.org/papers/w23933
- Huth, Abergel, High-frequency lead/lag relationships (2014): https://arxiv.org/abs/1111.7103
- Buccheri, Corsi, Peluso, Multi-asset lagged adjustment model (JBES 2021): https://openaccess.city.ac.uk/id/eprint/23591/
- Curme et al., Statistically validated intraday lead-lag (QF 2015): https://arxiv.org/abs/1401.0462
- Toth, Kertesz, Increasing market efficiency / Epps effect: https://arxiv.org/pdf/physics/0506071
- Fung et al., Sampling frequency and index-stock lead-lag (JFM 2015): https://onlinelibrary.wiley.com/doi/10.1002/fut.21715
- Buckle, Chen, Guo, Tong, Do ETFs lead the price moves? (2018): https://www.sciencedirect.com/science/article/abs/pii/S1057521917301904
- Cont, Cucuringu, Zhang, Cross-impact of order flow imbalance (QF 2023): https://arxiv.org/html/2112.13213v4
- Kolm, Turiel, Westray, Deep order flow imbalance (MF 2023): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3900141
- Anderson et al., Sources of stock return autocorrelation: https://eml.berkeley.edu/~anderson/Sources-042212.pdf
- Intraday comovement rises through the session, beta dispersion falls: https://www.sciencedirect.com/science/article/abs/pii/S1386418124000120
- Earnings release timing / speed of reaction: https://www.sciencedirect.com/science/article/abs/pii/S0304405X25000182 ; https://www.gsb.stanford.edu/faculty-research/publications/intraday-speed-adjustment-stock-prices-earnings-dividend-announcements
