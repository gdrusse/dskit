# Crypto model training, validation, and candidate cells

## TL;DR

**Kalshi method 24** is the best-developed overall research reference: a pooled BTC/ETH model that corrects the market price using a GRU forecast. **Setting 03** is its stronger correction for the previously selected ETH/11-minute/market-YES-10–20% pocket. Polymarket has separate BTC-only methods: model 02 was the main price-prediction lead; model 10/BTC11m/UP80–90% was a native-book return lead. Several cells look promising, but none is a confirmed profitable strategy. The proposed new staged study has not yet chosen a winner on fresh data.

## What the models use

A cell means venue, asset, minutes before close, and a 10-percentage-point bin of the venue's own UP/YES probability. A side means an actual UP/YES or DOWN/NO purchase. Lower **log loss** means better probability forecasts; it does not mean higher trading return.

Kalshi's GRU reads 60 one-minute bars, volatility and clock context, and BTC/ETH identity. It predicts a return distribution at 15 horizons, then a probability of crossing the contract strike. Method 24 applies a regularized ARD correction to the Kalshi market probability using the GRU disagreement, lead, spread, moneyness, volatility, volume, and open interest. Its correction strength is 0.50. Setting 03 uses the **same coefficients** at strength 0.75; it was selected for a pocket, not fitted only on that pocket. Method 48 is a volatility-expert challenger, not an established replacement.

Polymarket's completed 20-method study used native BTC probabilities, lead and price age. It did **not** transfer the Kalshi GRU or use spot, volume, or book depth as model inputs. Model 02 is pooled beta calibration; model 10 is a Huber residual correction; model 07 is an ARD residual correction. These are venue-native candidates. Polymarket ETH/SOL/XRP do not yet have the settled native history for a pooled multi-asset model. No single method has been selected across both venues.

## Windows used in completed work

| Study component | Training or selection | Scored period | Status |
|---|---|---|---|
| Kalshi GRU state 1 | Gradient labels before 2026-01-19; internal calibration 01-19 to 02-17 | 2026-02-19 to 04-19 | Pooled BTC/ETH input |
| Kalshi GRU state 2 | Gradient labels before 2026-04-01; internal calibration 04-01 to 04-30 | 2026-05-02 to 06-30 | Causal state update |
| Kalshi GRU state 3 | Gradient labels before 2026-05-31; internal calibration 05-31 to 06-29 | 2026-07-01 to 07-31 | July backbone update |
| Kalshi correction warm-up | Fits on 02-19 and 03-21, using published earlier outcomes and one-day embargo | 2026-02-19 to 04-19; 52,999 forecasts / 11,213 contracts | Methods and pockets chosen here |
| Kalshi correction validation | Fit on 05-02: 105,023 forecasts / 22,930 contracts; last actual training outcome 04-19 | 2026-05-02 to 07-31; 84,728 forecasts / 17,158 contracts | Historical validation, previously researched |
| Polymarket warm-up | Fits on 02-19 and 03-21: 31,018 then 45,376 training forecasts | 2026-02-19 to 04-19; 28,724 forecasts / 5,758 contracts | Native BTC method/cell screen |
| Polymarket validation | Fit on 05-02: 60,226 forecasts / 12,068 contracts; last actual training outcome 04-19 | 2026-05-02 to 07-31; 43,482 forecasts / 8,714 contracts | Historical validation, previously researched |
| Later retained audit | Saved model choices and fits | Kalshi W4 08-01 to 09-15, W5 from 09-15, and 10-08 to 10-12; native BTC books | Retrospective stress, not a fresh holdout |

Polymarket fitting used scheduled close plus 24 hours as an assumed label-availability rule; actual historical publication times were not established.

The new sequence—warm-up, method validation, later cell validation, and final evaluation—has **no new eligible windows assigned**. Previously viewed periods cannot become fresh by renaming them. Kalshi's overnight forward interval is protected from 2026-10-12 to 11-11 (read 11-11); a separate predictive interval is protected from 2026-10-17 to 2027-01-15 (read 01-15). Their overlap remains unresolved; neither releases the other.

## Epoch training losses

Only the GRU trains in epochs. It minimizes CRPS plus tail CRPS for a continuous return distribution, using AdamW, learning rate 0.0003, batch size 512, seed 11, a 500-epoch ceiling and 10-epoch early stopping. The internal calibration loss chooses the checkpoint. This loss is **not** the later binary market-comparison log loss.

| GRU state | Best epoch / epochs run | Training loss at best epoch | Internal calibration loss |
|---|---:|---:|---:|
| February–April | 14 / 24 | 2.273331 | 2.252783 |
| May–June | 10 / 20 | 2.260279 | 2.239636 |
| July | 14 / 24 | 2.256699 | 2.210312 |

The full 68 saved epoch rows appear below. Method 24 and setting 03 use ARD regression, which has **no saved epoch or iteration-loss curve**. Reconstructed in-sample setting-03 log loss versus market was 0.483771 vs 0.489053 at the February fit, 0.482762 vs 0.486991 in March, and 0.475748 vs 0.479107 in May. These are training diagnostics, not validation lift.

Polymarket model 02 is logistic beta calibration, also without epochs. Its recorded in-sample log loss versus native market was 0.508281 vs 0.508595 (02-19 fit, 31,018 rows), 0.510350 vs 0.510611 (03-21, 45,376), and 0.510677 vs 0.510878 (05-02, 60,226). Its neural challenger model 20 did not become the selected lead and reached its fixed 100-iteration cap.

<details>
<summary>All saved GRU epoch losses</summary>

An asterisk marks the saved best checkpoint. Values are rounded to six decimals; the source JSON retains full precision.

**w1-fold14: best epoch 14 of 24**

~~~text
epoch  training_loss  internal_calibration_loss  selected
    1       2.332143                   2.266636
    2       2.290670                   2.264265
    3       2.285608                   2.262906
    4       2.282636                   2.259200
    5       2.280804                   2.255881
    6       2.278858                   2.255773
    7       2.277313                   2.256117
    8       2.276086                   2.256383
    9       2.275007                   2.254419
   10       2.274638                   2.252959
   11       2.274443                   2.253484
   12       2.273280                   2.256111
   13       2.273240                   2.255430
   14       2.273331                   2.252783  *
   15       2.272367                   2.252784
   16       2.272615                   2.253040
   17       2.271999                   2.254166
   18       2.272657                   2.254950
   19       2.272172                   2.253788
   20       2.271755                   2.253032
   21       2.270962                   2.253216
   22       2.271277                   2.252806
   23       2.271177                   2.252792
   24       2.270800                   2.254772
~~~

**w2-block1: best epoch 10 of 20**

~~~text
epoch  training_loss  internal_calibration_loss  selected
    1       2.285810                   2.247688
    2       2.268629                   2.242241
    3       2.265982                   2.241608
    4       2.264549                   2.240804
    5       2.263591                   2.240567
    6       2.262754                   2.240797
    7       2.262046                   2.240770
    8       2.261414                   2.239795
    9       2.260810                   2.240012
   10       2.260279                   2.239636  *
   11       2.259703                   2.240596
   12       2.259093                   2.240630
   13       2.258487                   2.240382
   14       2.257836                   2.241239
   15       2.257279                   2.241422
   16       2.256651                   2.241511
   17       2.256104                   2.241434
   18       2.255503                   2.243035
   19       2.254931                   2.243006
   20       2.254025                   2.244171
~~~

**w2-block2: best epoch 14 of 24**

~~~text
epoch  training_loss  internal_calibration_loss  selected
    1       2.283465                   2.216430
    2       2.266858                   2.213906
    3       2.264424                   2.213359
    4       2.263133                   2.213383
    5       2.262092                   2.212920
    6       2.261327                   2.211266
    7       2.260689                   2.211961
    8       2.260013                   2.211433
    9       2.259435                   2.210876
   10       2.258907                   2.211138
   11       2.258383                   2.210557
   12       2.257828                   2.210387
   13       2.257246                   2.210397
   14       2.256699                   2.210312  *
   15       2.256106                   2.210479
   16       2.255606                   2.210763
   17       2.254863                   2.211091
   18       2.254201                   2.211178
   19       2.253540                   2.212429
   20       2.252866                   2.212956
   21       2.252186                   2.212264
   22       2.251370                   2.213752
   23       2.250752                   2.216179
   24       2.250012                   2.212797
~~~

</details>

## Validation lift over market-implied probability

Lift means (market log loss − model log loss) / market log loss × 100 on identical contracts. It is **forecast-score improvement**, not trading return.

| Same-contract comparison | Market log loss | Model log loss | Lift |
|---|---:|---:|---:|
| Kalshi method 24, all BTC/ETH validation contracts (17,158) | 0.455526 | 0.454751 | 0.170% |
| Kalshi method 24, ETH/11m/YES 10–20% (689) | 0.404722 | 0.401796 | 0.723% |
| Kalshi setting 03, same pocket (689) | 0.404722 | 0.400824 | 0.963% |
| Polymarket model 02, all BTC validation contracts (8,714) | 0.503055 | 0.502826 | 0.045% |
| Polymarket model 02, BTC/5m/UP 80–90% (884) | 0.355820 | 0.353404 | 0.679% |

For setting 03's pocket: 0.404722 − 0.400824 = 0.003898 lower log loss; 0.003898 / 0.404722 × 100 ≈ 0.963%. Its corrected day/week uncertainty tests did not pass; later W4/W5 gains reversed. Polymarket model 02's local lift was measured on older scalar prices. With fresh native books, the primary rule selected only two UP entries. It is not the default trading candidate.

## Which cells were selected or prioritized

The **frozen Kalshi tuning selection** chose eight model/cell pairs in three unique regions: setting 03 at ETH/11m/10–20% and 20–30%; setting 06 at ETH/11m/10–20% and ETH/14m/70–80%; setting 09 at those same two regions; setting 12 at ETH/11m/10–20% and 20–30%. All eight improved average validation log loss versus raw and recalibrated market; none passed the corrected robust pocket test.

The **new first wave** schedules 32 distinct regions based on already saved evidence. These are candidates for later selection, not cells selected by the new staged study:

- Kalshi, six earlier selected regions with retained two-period score direction: ETH 11m 10–20%, 14m 70–80%, 11m 20–30%, 5m 20–30%; BTC 5m 30–40%, 2m 10–20%.
- Kalshi, eleven persistent market-versus-outcome gap regions: BTC 8m 20–30%, 11m 70–80%; ETH 2m 80–90%, 5m 10–20%, 30–40%, 60–70%, 90–100%, 8m 50–60%, 90–100%, 11m 70–80%, 14m 30–40%.
- Polymarket BTC, thirteen warm-up-selected regions whose methods kept mean-score support: 2m 20–30%, 30–40%, 40–50%, 50–60%, 80–90%, 90–100%; 5m 0–10%, 20–30%, 80–90%; 8m 30–40%, 50–60%, 90–100%; 11m 60–70%.
- Polymarket BTC, two additional native-book cost leads: model 10 at 11m/UP 80–90% and model 07 at 5m/UP 90–100%.

### Original selected model-to-cell identities

These are saved **historical** selection or survival identities. They do not choose the new staged-study method or a purchase side.

- Kalshi ETH / 11m / market YES 10-20%: aligned-22, tuning-03, tuning-06, tuning-09, tuning-12.
- Kalshi ETH / 14m / market YES 70-80%: tuning-06, tuning-09.
- Kalshi ETH / 11m / market YES 20-30%: tuning-03, tuning-12.
- Kalshi ETH / 5m / market YES 20-30%: aligned-05, aligned-22, aligned-24, aligned-38, aligned-44, aligned-48.
- Kalshi BTC / 5m / market YES 30-40%: aligned-05, aligned-44, aligned-48.
- Kalshi BTC / 2m / market YES 10-20%: aligned-05, aligned-44, aligned-48.
- Polymarket BTC / 2m / market UP 20-30%: model03, model20.
- Polymarket BTC / 2m / market UP 30-40%: model20.
- Polymarket BTC / 2m / market UP 40-50%: model02, model05.
- Polymarket BTC / 2m / market UP 50-60%: model20.
- Polymarket BTC / 2m / market UP 80-90%: model02, model12, model13, model14.
- Polymarket BTC / 2m / market UP 90-100%: model02, model03, model10, model14, model15.
- Polymarket BTC / 5m / market UP 00-10%: model03, model04, model15.
- Polymarket BTC / 5m / market UP 20-30%: model11, model17.
- Polymarket BTC / 5m / market UP 80-90%: model02.
- Polymarket BTC / 8m / market UP 30-40%: model06, model07, model09, model11, model16.
- Polymarket BTC / 8m / market UP 50-60%: model16.
- Polymarket BTC / 8m / market UP 90-100%: model10.
- Polymarket BTC / 11m / market UP 60-70%: model10.

The complete register retains **298 candidate side entries, 300 blocked Polymarket ETH/SOL/XRP entries and two controls**. Among the original candidates, 223 had a positive side-matched historical cost scenario; 75 had prediction or mean-gap support without directional trade evidence. In the later retained audit, 219 of 300 assessable side/cells had at least one positive modeled scenario, but **zero passed the corrected primary or alternative cost test**. Prioritizing 32 regions changes order, not eligibility of the other positive leads.

Polymarket model 10/BTC11m/UP80–90% had 324 primary displayed-depth entries and a modeled mean return of **+3.37 cents per share**, but the fitted forecast allowed only **+0.464 cent** above ask plus fee and search-adjusted day/week p-values were 1.0. Model 07/BTC5m/UP90–100% had 271 wins in 271 observations in one alternate scenario and a +1.38-cent mean, but an unseen loss could materially change that all-win result. Kalshi method 24 had five adequately populated positive candle-cost side/cells across both historical periods; their forecast allowances were about 0.22–0.55 cent per share, and candle quotes are not verified delayed fills.

## Best method decision and what the staged study still needs

**Current reference: Kalshi method 24.** Setting 03 is the named Kalshi pocket candidate. **Polymarket has no selected multi-asset winner:** model 02 is the earlier forecast lead and model 10 the native-book cost lead. The new staged procedure must choose its own method on a distinct method-validation period before any new cell is promoted. This document does not preselect a winner from previously viewed outcomes.

First, screen already defined feature/method candidates on development data; the previous BTC/ETH W1 feature ablation already tested and dropped the cross-asset feature family for the GRU. Next, use a distinct period to select one method per eligible venue, another to select sides/cells with native executable asks and fees, and an untouched final period to evaluate the frozen combined rule. Record failed cells and retain the complete registry. Do not swap in a new winner after viewing later outcomes. Polymarket multi-asset fitting also requires native settled labels and earlier training history beyond BTC.

## Source records

- [Kalshi training explanation and exact score reconstruction](market-model-training-and-validation.md).
- [Kalshi aligned methods](../memos/2026-10-18-warmup20-aligned-methods.md), [tuning and pocket lock](../memos/2026-10-18-pocket-tuning.md), [attribution](../memos/2026-10-18-attribution.md).
- [Polymarket 20-method study](../memos/2026-10-18-polymarket-predictability.md) and [fit-loss export](../memos/2026-10-18-polymarket-predictability-training.csv).
- [Complete historical cell follow-up](../memos/2026-10-18-executable-cells.md), [retained-history audit](../memos/2026-10-19-retained-history-study.md), and [new staged JSON](../plans/2026-10-19-staged-multicurrency-selection.json).
- Exact GRU epoch curves: /home/russell/data/crypto_trading/research/training-explanation-20261018/results.json.
