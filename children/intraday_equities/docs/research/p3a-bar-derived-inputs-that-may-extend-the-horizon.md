# P3a — bar-derived inputs that may extend the horizon

Date: 2026-09-03. Research only (online sources); nothing run.

## Question

From 1-minute bars alone (close, vwap, volume, trade_count; high/low if the
store carries them), which inputs have documented out-of-sample value for
1–60 min returns on JPM/LLY/XOM, what fails, how much history to give
each, and which ones move the horizon rather than the 1-minute fit?

## Finding

Notation: `r_k = log(close_t / close_{t-k})` (minutes); `RV_k = sqrt(sum of
last k squared 1-min returns)`; `slot` = minute-of-day; `slot_mean(x)` =
mean of `x` in that slot over the TRAINING fold only (no peeking).

Ranked shortlist, ordered by expected horizon extension:

| # | column | formula | lookback | why |
|---|---|---|---|---|
| 1 | `ret_sameslot_1d` | 30-min return ending at the same slot one trading day earlier (`log(close_{t-390}/close_{t-420})`); also mean over days 1–5 | 390–1950 min | half-hour returns continue at exact daily lags for 40+ days [HKS] — a 30-min-horizon effect |
| 2 | `ret_since_open` | `log(close_t / open_day)` and `ret_first30 = log(close_{10:00}/prev_close)` | intraday | first half-hour predicts last half-hour; stronger on high-vol/high-volume days [Gao] |
| 3 | `overnight_gap` | `log(open_day / prev_close)` | 1 day | overnight moves reverse intraday, strongest in the first hour [LPS, DCK] |
| 4 | `ret_5/15/30/60_z` | `r_k / (RV_390/sqrt(390) * sqrt(k))` for k=5,15,30,60 | 5–60 min | within-hour reversal from liquidity imbalance [HKS, Nagel]; 15-min AR(1) R² 0.69% [ABS] |
| 5 | `rv_ratio_30_390` | `RV_30 / RV_390` (plus `log RV_30`) | 30/390 min | reversal and intraday momentum both strengthen with volatility [Nagel, Gao]; feed as `r_k × rv_ratio` to ridge |
| 6 | `vol_rel_5` | `log(sum volume last 5 min / slot_mean(same))` | 5 min, 20–60 day slot norm | volume above its time-of-day norm raises predictability [Gao, ÖRU]; interaction with #4 |
| 7 | `acf1_60` | rolling lag-1 autocorrelation of 1-min returns over 60 min | 60 min | separates bounce from true reversal; autocorr regime moves with volatility [HKS, LeBaron] |
| 8 | `tc_rel_5` | `log(sum trade_count last 5 min / slot_mean(same))` | 5 min, slot norm | trade COUNT drives volatility, size adds nothing [JKL] — a cleaner intensity gauge than volume |
| 9 | `amihud_30` | `log(sum|r_1| / sum(vwap×volume))` over 30 min | 30 min | reversal profits are larger when illiquid [Nagel, CD]; conditioner, weak alone |
| 10 | `vwap_gap` | `(close_t − vwap_t)/close_t`; 5-min version vs volume-weighted vwap | 1–5 min | bounce/imbalance proxy; mostly explains the H≤2 gain we already have (see P4) |
| 11 | `range_30` | Parkinson `log(max high / min low)` over 30 min ÷ slot norm — only if high/low exist | 30 min | range-based vol is a less noisy regime gauge than RV; within-bar timing adds ~0.3% [OHLC-T] |
| 12 | `avg_trade_size` | `log(volume_t / trade_count_t)` | 1–5 min | weakest: no information beyond trade count [JKL] |

What fails / overfits: 7,846 intraday technical rules, none survive
data-snooping correction [MCC]; 14 OHLCV momentum families on one
instrument at 5 min, none pass walk-forward [MNQ-F]; sequence models fed
raw 5-min OHLCV windows do not beat the base rate and lag order adds no
information [MNQ-S]; random forests on 1-min market returns add nothing
or hurt [ZML]. Raw prices and long raw-lag windows are noise for boosting.

History (c): trees — give lags 0,1,2,5 of `r_1` plus the rolling stats
above; do NOT hand 60+ raw lags (splits dilute, [MNQ-S] shows no gain).
Windowed inputs help GBRT only with regularisation [Elsayed]. Ridge —
cannot sum or multiply, so supply the multi-scale returns and the
interaction columns (`r_k × rv_ratio`, `r_k × vol_rel`) explicitly and
standardise. Slot norms from 20–60 training days; recompute per fold.

Horizon (d): #1–3 are documented at 30 min to end-of-day, not 1 min; #4–6
are 5–60-min effects; #7, #10, #12 act at ≤5 min. Expect small R² (0.2–0.7%
at 5–15 min in the market-index literature [HLS, ABS]) and conditioning
rather than new sign information. Cross-name lagged returns are sparse
but real at 1 min [CCY] — that is P3(b), not this doc.

## Sources

- [HKS] Heston, Korajczyk, Sadka, JF 2010 — https://arxiv.org/abs/1005.3535
- [Gao] Gao, Han, Li, Zhou, JFE 2018 — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2440866
- [LPS] Lou, Polk, Skouras, tug of war — https://personal.lse.ac.uk/polk/research/TugOfWar.pdf
- [DCK] Della Corte, Kosowski, overnight-intraday reversal — https://assets.super.so/e46b77e7-ee08-445e-b43f-4ffd88ae0a0e/files/c953a0e6-e93e-4bf7-b839-45a90cedced4.pdf
- [Nagel] Evaporating liquidity — https://www.nber.org/system/files/working_papers/w17653/w17653.pdf
- [CD] Collin-Dufresne, Daniel, liquidity and return reversals — https://files.fisher.osu.edu/department-finance/public/liquidity_and_return_reversals.pdf
- [ABS] Aleti, Bollerslev, Siggaard, Mgmt Sci 2025 — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4388560
- [HLS] Huddleston, Liu, Stentoft, JFEC 2023 — https://academic.oup.com/jfec/article-abstract/21/2/485/6400345
- [AFXZ] Aït-Sahalia, Fan, Xue, Zhou, NBER 30366 — https://www.nber.org/papers/w30366
- [CCY] Chinco, Clark-Joseph, Ye, JF 2019 — https://www.nber.org/papers/w23933
- [JKL] Jones, Kaul, Lipson, RFS 1994 — https://academic.oup.com/rfs/article-abstract/7/4/631/1597416
- [ÖRU] Volume-driven time-of-day effects — https://www.oru.se/globalassets/oru-sv/institutioner/hh/workingpapers/workingpapers2025/wp-14-2025.pdf
- [LeBaron] Serial correlation vs volatility — https://arxiv.org/pdf/0810.4912
- [MCC] Marshall, Cahan, Cahan, JEF 2008 — https://www.sciencedirect.com/science/article/abs/pii/S0927539807000588
- [MNQ-F] OHLCV signal falsification — https://arxiv.org/abs/2605.04004
- [MNQ-S] LSTM vs boosting on MNQ — https://arxiv.org/abs/2605.17724
- [ZML] Minute-level RF/LSTM market returns — https://arxiv.org/abs/2112.15108
- [OHLC-T] Timing features in 1-min OHLC — https://arxiv.org/html/2509.16137v1
- [Elsayed] Windowed GBRT — https://arxiv.org/pdf/2101.02118
