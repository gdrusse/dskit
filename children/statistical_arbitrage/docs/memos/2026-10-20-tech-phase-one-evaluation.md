## TL;DR

The phase-one precision-network options books lost money after modeled execution costs. In 2020–2025, ATM returned **-21.96%** and skew **-33.94%** on $100,000; both trailed equal-weight AAPL/AMZN/GOOGL (**+282.23%**) and QQQ (**+197.61%**). Network selection reduced losses versus trading every pair, but it did not consistently beat a simple correlation gate. This retrospective study finds **no deployable statistical-arbitrage edge**.

## Execution contract

- **Accepted code:** `76b4179e1719fdf6598a8b40930069be180eb55f`, under approved ADR-0270/ADR-0271. The frozen v6 run is `/home/russell/data/statistical_arbitrage/replay-20261020/tech-paired-option-replay-causal-execution-20261020-v6-4f750fc85bae/`. Backtest config SHA-256: `4f750fc85bae01023055e160d74e38823dc7eaf970deee8ba51af8afb55abb9f`.
- **Replay command from the WSL DSKit worktree:** `PYTHONPATH=children/statistical_arbitrage:. python -m statistical_arbitrage.backtest children/statistical_arbitrage/configs/run-tech-backtest.json`.
- **Assets:** AAPL, AMZN, GOOGL option pairs. QQQ is the stock-return factor and a market comparator, never a traded option node. MSFT, NVDA and energy are outside this run.
- **Calendar:** adjusted stock prices begin 2016-01-04; frozen Theta options begin 2018-01-01. 2019 is warm-up, 2020 development scoring, and 2021–2025 retrospective annual validation. Data end before 2025-12-16. Seven annual graph fits each use the preceding 252 common sessions and three chronological cross-validation splits. The ten index_options-style 2021–2023 blocks are reporting subfolds, not additional retrains. Calendar SHA-256: `a06f6f6f1d406e42b69db3c320b0fe1ef130a2d0de96388c661812e49eb6dc1e`.
- **Signal:** 30±7-day option surfaces; ATM implied volatility or 25-delta put-minus-call skew; 60-session prior-only pair-spread Z score; entry at |Z| ≥ 2, exit at mean reversion, |Z| ≥ 4 stop, or 20-session hold cap.
- **Execution:** same next-common-session EOD NBBO quote proxy for all six books, at most one pair per book, integer vega matching, $200,000 gross underlying-notional cap, 20% maximum initial vega mismatch and quote half-spread/premium, corporate-event refusals, $0.65/contract option fee, and $0.005/share daily hedge fee. Stock hedge cash dividends are included. Initial capital is $100,000 per book; 252 sessions annualize ratios.
- **Comparators:** precision graph gate, QQQ-residual Pearson-correlation gate, and unrestricted all-pairs gate for both ATM and skew. The simple baseline is the all-pairs book with otherwise identical signal, sizing, fill, cost, and exit rules. Market comparators are daily-rebalanced equal-weight AAPL/AMZN/GOOGL and QQQ adjusted-price return streams. Their stock exposure differs from a delta-hedged option spread.
- **Evidence limits:** EOD bid/ask snapshots are historical fill proxies, not executable fill records. There is no untouched final test, GPU/neural training, paper trade, or live trade. The 2026-10-19 graph diagnostics and v4/v5 replay results are void after causal and execution corrections.

## Implementation and verification evidence

The replay pinned its structural config, resolved run, twelve structural JSONL files, source configs, calendar, and market-reader fingerprints before scoring. It saw 20,025,208 Theta rows, retained 27,242 candidate chain rows, generated 1,830 cashflows, and published six reconciled books and four plots. Across **overlapping hypothetical books**, it attempted 305 pairs, closed 290, retained 15 hedge-failed exposures, and blocked zero later pairs. These pooled counts are not one investable portfolio.

Two fresh independent skeptics reviewed the frozen candidate: `/root/final_v6_correctness` reported zero Critical/Major issues after 112 targeted tests; `/root/final_v6_integration` reported zero Critical/Major after 190 focused tests and clean Ruff. Their disclosed Minor backlog is split crossings unmodeled, hedge cashflows displayed on close rather than ex-date, and stale stock-source prose. No v6 position overlapped a known split. These are limits, not a live execution guarantee.

## Model fit and training diagnostics

GraphicalLassoCV selects a sparse precision matrix after QQQ factor residualization. Each yearly fit sees only the prior 252 sessions. `n_iter` counts numerical solver iterations, **not training epochs**. This is not a neural model; `training_epoch_loss` is null. Training log likelihood and chronological held-out CV score are the available diagnostics, not a learning curve.

| Fit date | Trained through | Alpha | Train log likelihood | Chronological CV score | Solver iterations | Selected edges | Status |
|---|---|---:|---:|---:|---:|---:|---|
| 2019-01-02 | 2018-12-31 | 0.224705 | -4.6934 | -4.5330 | 1 | 0 | no edges |
| 2020-01-02 | 2019-12-31 | 0.085936 | -4.0658 | -4.2381 | 1 | 2 | ok |
| 2021-01-04 | 2020-12-31 | 0.285400 | -4.8194 | -4.6348 | 1 | 1 | disconnected |
| 2022-01-03 | 2021-12-31 | 0.006034 | -4.3836 | -4.2868 | 1 | 3 | ok |
| 2023-01-03 | 2022-12-30 | 0.066711 | -4.5043 | -4.3995 | 2 | 2 | ok |
| 2024-01-02 | 2023-12-29 | 0.065493 | -4.1447 | -4.2116 | 2 | 2 | ok |
| 2025-01-02 | 2024-12-31 | 0.013516 | -4.5378 | -4.4164 | 1 | 3 | ok |

The 2019 warm-up fit selected no edge; the 2021 fit was disconnected. Scores vary without a monotone learning pattern. Across seven annual fits, precision selected AAPL–AMZN 5/7, AAPL–GOOGL 3/7, AMZN–GOOGL 4/7; correlation selected them 7/7, 6/7, and 7/7. These are frequencies over seven dependent windows, not success probabilities.

## Full scored period: 2020–2025

Returns are net book equity change from $100,000. Sharpe is mean daily return divided by daily standard deviation, multiplied by √252. Information ratio (IR) uses **book return minus equal-weight stock return**. Max drawdown is equity divided by prior peak minus one at its worst point. Win rate and duration count closed trades; hedge-failed exposures remain in daily P&L but are excluded from those trade statistics.

| Book | Total return | Sharpe | IR vs equal-weight | Max drawdown | Wins / closed | Mean duration, sessions |
|---|---:|---:|---:|---:|---:|---:|
| ATM precision | -21.96% | -0.82 | -1.03 | -22.94% | 8/29 | 8.28 |
| ATM correlation | -22.37% | -0.79 | -1.03 | -23.34% | 10/33 | 8.79 |
| ATM all pairs | -23.97% | -0.83 | -1.04 | -24.93% | 9/34 | 8.06 |
| Skew precision | -33.94% | -1.85 | -1.16 | -34.05% | 10/62 | 3.97 |
| Skew correlation | -32.47% | -1.64 | -1.15 | -32.58% | 11/65 | 3.98 |
| Skew all pairs | -35.88% | -1.74 | -1.18 | -35.98% | 12/67 | 3.84 |
| Equal-weight tech | +282.23% | +0.93 | — | -41.00% | — | — |
| QQQ | +197.61% | +0.85 | — | -35.12% | — | — |

The precision ATM advantage over all pairs is **+2.00 percentage points**: -21.96% − (-23.97%). Correlation ATM is **+1.60 points** over all pairs, leaving precision only **+0.40 points** ahead of correlation. Precision skew is **+1.94 points** over all pairs (-33.94% − (-35.88%)), but correlation skew is **+3.41 points** over all pairs and precision trails correlation by **1.47 points**. These are return differences, not statistically established alphas. Precision IR versus its all-pairs book is +0.19 for ATM and +0.20 for skew.

## Annual development and validation

P&L is allocated by daily mark date; a completed trade is counted in the year it **closed**. A trade crossing a boundary can therefore affect two years of P&L but appears once in the closed-trade count. Each year is scored on a fresh $100,000 comparison base; annual percentages should not be added to reproduce the full-period equity curve.

| Year / role | ATM precision | ATM correlation | ATM all pairs | Skew precision | Skew correlation | Skew all pairs | Equal-weight | QQQ | Closed precision ATM / skew |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2020 development | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | +64.14% | +48.62% | 0 / 0 |
| 2021 validation | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | +32.71% | +27.42% | 0 / 0 |
| 2022 validation | -3.63% | -3.63% | -3.63% | -1.17% | -1.17% | -1.17% | -38.06% | -32.58% | 5 / 7 |
| 2023 validation | -3.62% | -3.62% | -5.22% | -8.82% | -8.82% | -12.23% | +64.39% | +54.85% | 8 / 16 |
| 2024 validation | -7.59% | -7.99% | -7.99% | -10.40% | -8.94% | -8.94% | +38.75% | +25.58% | 7 / 18 |
| 2025 validation | -7.12% | -7.12% | -7.12% | -13.55% | -13.55% | -13.55% | +24.20% | +19.87% | 9 / 21 |

There were zero completed precision trades in 2020–2021. Every active annual precision return in 2022–2025 was negative for both ATM and skew. Market returns were also negative in 2022, but the overall study does not beat the market.

## Ten chronological reporting subfolds

These origin windows mirror the index_options calendar. They are diagnostic slices in 2021–2023, **not** separately trained models or independent tests. Each return is the P&L change within its interval from a fresh $100,000 base; trade counts use close date.

| Block: [start, end) | ATM precision | ATM correlation | ATM all pairs | Skew precision | Skew correlation | Skew all pairs | Equal-weight | QQQ | Closed precision ATM / skew |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1: [2021-01-04, 2021-03-15) | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | +0.72% | +0.55% | 0 / 0 |
| 2: [2021-04-19, 2021-06-28) | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | +2.30% | +2.30% | 0 / 0 |
| 3: [2021-08-02, 2021-10-11) | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | +0.27% | -0.82% | 0 / 0 |
| 4: [2021-11-15, 2022-01-24) | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | -8.26% | -10.78% | 0 / 0 |
| 5: [2022-02-28, 2022-05-09) | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | -14.73% | -10.45% | 0 / 0 |
| 6: [2022-06-13, 2022-08-22) | -1.98% | -1.98% | -1.98% | -0.35% | -0.35% | -0.35% | +18.83% | +11.99% | 1 / 2 |
| 7: [2022-09-26, 2022-12-05) | -0.50% | -0.50% | -0.50% | -0.44% | -0.44% | -0.44% | -5.69% | +6.18% | 2 / 3 |
| 8: [2023-01-09, 2023-03-20) | +1.10% | +1.10% | +1.10% | -0.09% | -0.09% | -3.78% | +17.39% | +13.60% | 1 / 4 |
| 9: [2023-04-24, 2023-07-03) | -2.89% | -2.89% | -1.76% | -0.33% | -0.33% | +0.24% | +17.92% | +16.84% | 3 / 3 |
| 10: [2023-08-07, 2023-10-16) | +1.00% | +1.00% | +0.80% | -1.92% | -1.92% | -2.10% | -0.47% | -1.67% | 2 / 3 |

Blocks 1–5 had no precision trade closes. Among active blocks, precision ATM was positive only in blocks 8 and 10; precision skew was negative in every active block. These thin slices do not establish a robust edge.

## Execution friction, refusals, and residual risk

| Book | Signal intents | Passed preflight | Entered | Closed | Hedge-failed exposure | Net P&L, USD |
|---|---:|---:|---:|---:|---:|---:|
| ATM precision | 194 | 110 | 34 | 29 | 5 | -21,962.45 |
| ATM correlation | 256 | 120 | 38 | 33 | 5 | -22,367.33 |
| ATM all pairs | 268 | 130 | 39 | 34 | 5 | -23,965.24 |
| Skew precision | 173 | 109 | 62 | 62 | 0 | -33,939.92 |
| Skew correlation | 223 | 112 | 65 | 65 | 0 | -32,472.38 |
| Skew all pairs | 227 | 115 | 67 | 67 | 0 | -35,879.84 |

For precision ATM, 194 intents yielded 34 entries. Preflight rejected 62 for gross notional, 17 for vega mismatch, 2 for corporate events, 2 for wide quotes, and 1 for no safe exit. Five opened ATM pairs later failed a hedge rebalance when implied volatility was unavailable; their marked P&L stays in the equity curve, while they are excluded from completed-trade win rate. Across six books, 15 such exposures were retained. There were no blocked later pairs. The simulation includes quote crossing and stated fees, but omits live queue/latency and split crossings.

## Decision

**No-go for paper or live trading as an edge claim.** The same-cost unrestricted and correlation baselines lose money; precision filtering is inconsistent against correlation; and both precision books trail market comparators by hundreds of percentage points. There is no untouched final holdout. If further research is authorized, pre-register a new hypothesis on a truly untouched later cohort and address split/hedge cashflow timing explicitly. Do not tune a new claim on this frozen study period.

## Reproducibility and handoff

- Final `evaluation.json`: `/home/russell/data/statistical_arbitrage/replay-20261020/tech-paired-option-replay-causal-execution-20261020-v6-4f750fc85bae/evaluation.json`; SHA-256 `73c8ccba8fc2907f3c3c84e826f78d89fa188e7eb4defd8e82a4d5550d52c355`.
- Four PNGs beside it: `network-atm.png`, `z-atm-network.png`, `z-skew-network.png`, and `cumulative-equity.png`.
- Structural config `configs/run-tech-structural.json`: SHA-256 `686770f0830a8b483c4c7e248e6548b55d2c7e614b34f6e2db4ea7a33ac1ba31`. Structural resolved run: `/home/russell/data/statistical_arbitrage/pipeline_runs/tech-structural-correlation-20261020-2025-12-16-366e6db1/resolved.json`; SHA-256 `75f1d529dada79ede569266a484f563e41d99712a4bbcd29897e3bda27a934be`.
- Structural JSONL rows: `/home/russell/data/statistical_arbitrage/structural-correlation-20261020/`. Their individual SHA-256 values, all stock/rate reader fingerprints, and frozen Theta member names are in `evaluation.json`. Theta member-list fingerprint: `7b1d6ab98c46648fe35f76765f2e9f006515dfb6db2ab2707e15e5226a26b27e` (444 members).
- The accepted code and v6 evidence are frozen. Earlier provisional metrics are superseded; review is complete with zero unresolved Critical/Major. Editorial closure and integration state are in the lane note.

