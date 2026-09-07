# Pooled model-zoo results (P15/P16, P17 partial)

## Outcome

The owner-approved P15 model zoo completed all 20 paired outer folds for its
three candidates and all ten direct leads. Ridge ranked first by mean path
score and was selected as the simplest statistically indistinguishable
frontier. Neither the TCN nor the Transformer showed evidence of improvement
over the linear baseline.

| rank | candidate | mean path score | fold standard deviation |
| ---: | --- | ---: | ---: |
| 1 | pooled OHLCV Ridge fusion | 0.001346 | 0.009173 |
| 2 | pooled OHLCV small Transformer fusion | 0.000835 | 0.010375 |
| 3 | pooled OHLCV TCN fusion | 0.000409 | 0.008933 |

Ridge minus TCN was 0.000937 (HAC standard error 0.000778,
`p=0.228414`); Ridge minus Transformer was 0.000511 (HAC standard error
0.000596, `p=0.390826`); TCN minus Transformer was -0.000426 (HAC standard
error 0.000641, `p=0.506887`). None passed the three-pair Bonferroni threshold
of 0.016667.

All three candidates had their best fold at 2023-05-19. Ridge was positive in
14/20 folds, with median 0.001527, but its mean excluding that best fold was
-0.000067. TCN was positive in 11/20 folds, with median 0.000491 and a
best-fold-excluded mean of -0.001088. Transformer was positive in 10/20 folds,
with median 0.000098 and a best-fold-excluded mean of -0.000992. The small
positive means are therefore not robust promotion evidence.

## P16 TFT-lite vs Ridge (sequence zoo)

P16 paired a TFT-lite candidate (channel VSN, LSTM encoder, gated attention —
ADR-0051) against a bit-reproducible re-run of the P15 Ridge frontier on the
identical session-local one-minute OHLCV sequence design. The TFT tower added
a learned symbol embedding and the shared late-fusion head; its HPO swept
`context_length`, width, depth, `nhead`, projection/embedding dims, epochs,
`lr`, `weight_decay`, `batch_size`, and `dropout`.

| rank | candidate | mean path score | fold standard deviation |
| ---: | --- | ---: | ---: |
| 1 | pooled OHLCV Ridge fusion (re-run) | 0.001346 | 0.009173 |
| 2 | pooled OHLCV TFT-lite fusion | 0.000608 | 0.005520 |

Ridge minus TFT was 0.000738 (HAC standard error 0.001270, `p=0.561241`) —
no detectable difference under Bonferroni (threshold 0.05, single pair). TFT
was positive in 9/20 folds; its positive mean rode one strong fold (0.010953
at fold 7) and is negative excluding it. The TFT's mean sits slightly below
the P15 Transformer (0.000835) and above the P15 TCN (0.000409), so adding
gated attention over the LSTM encoder did not rescue the sequence tower.

## Eleven-model reference across completed zoos

This reference includes every model across the completed zoos. Rankings
across P13, P14, P15, and P16 are descriptive only: the studies did not all
use the same eligible origin rows or approved inventory, so cross-zoo
differences are not paired hypothesis tests. Statistical claims remain
confined to each zoo's own comparison artifact.

| zoo | model | mean path score | fold standard deviation | within-zoo reading |
| --- | --- | ---: | ---: | --- |
| P13 | pooled LightGBM | 0.006401 | 0.002358 | selected simplest frontier; positive in 20/20 folds |
| P13 | pooled Torch MLP | 0.005322 | 0.005516 | not detectably different from LightGBM |
| P13 | Kronos hidden + LightGBM | 0.001175 | 0.001247 | detectably worse than both P13 tabular baselines |
| P13 | Kronos hidden + Torch MLP | -0.003852 | 0.004458 | detectably worse than both P13 tabular baselines |
| P14 | pooled OHLCV LSTM fusion | 0.001174 | 0.008930 | selected simpler recurrent frontier; fragile across folds |
| P14 | pooled OHLCV GRU fusion | -0.000417 | 0.008949 | not detectably different from LSTM |
| P15 | pooled OHLCV Ridge fusion | 0.001346 | 0.009173 | selected simplest P15 frontier; fragile across folds |
| P15 | pooled OHLCV small Transformer fusion | 0.000835 | 0.010375 | no detected improvement over Ridge |
| P15 | pooled OHLCV TCN fusion | 0.000409 | 0.008933 | no detected improvement over Ridge |
| P16 | pooled OHLCV TFT-lite fusion | 0.000608 | 0.005520 | no detected improvement over the re-run Ridge frontier |
| P16 | pooled OHLCV Ridge (re-run) | 0.001346 | 0.009173 | bit-reproducible re-run of the P15 Ridge frontier |

*P17 (pooled RandomForest) did not finish — see the note below.*

The broad evidence still favors P13 pooled native LightGBM as the practical
development frontier: it has the highest descriptive mean, the lowest-cost
strong representation, and the only result positive in all twenty folds.
P15 and P16 add useful negative results: neither recurrent fusion nor
attention-based channel fusion (TFT) beat the flattened linear sequence
baseline on the common P14/P15/P16 sequence-eligible design.

### P17 RandomForest — terminated early; two-fold descriptive observation

*P17 paired a pooled `RandomForestRegressor` (native `max_features`, `symbol_code`
as a numeric code) against a bit-reproducible re-run of the P13 LightGBM
frontier on the identical tabular cohort, folds, and leads. The RandomForest
was terminated by owner decision after two of twenty folds at ~28 minutes per
lead (an estimated ~90-hour total, CPU-bound), so no paired comparison
artifact exists. The cut is recorded in the journal; RF's two completed folds
are partial evidence, not a result.*

*The direct head-to-head over those two folds and all ten leads is descriptive
only: the per-symbol out-of-sample R² deltas flip sign between folds (fold 1
favors LightGBM by ~0.001 mean; fold 2 favors RF by ~0.0003 mean), the
horizon-decay shape is similar (signal strongest at lead 1, fading to ~0 by
lead 10), and the same symbols light up for both models. With only two folds,
no paired comparison artifact, and no prespecified equivalence margin, this
does not establish equivalence or statistical indistinguishability. The owner
stopped the run because its roughly 50× fit cost did not justify four further
CPU-days for this exploratory check. No RandomForest candidate was promoted
or refit.*

The read-only cross-benchmark selector was rerun after P15 completion. It
accepted all three pinned sources and all nine candidates, and selected P13
pooled LightGBM by the declared maximum-mean rule. This is a deterministic
descriptive ranking, not a cross-study significance claim or promotion.

## What was tested

P15 reused P14's verified one-minute sequence caches and eligibility policy:
the fixed 25 P12 Gate-3 survivors, twenty paired 63-day validation folds, a
730-day training window, five-day embargo, data no later than 2026-02-28, ten
direct leads, and equal-stock/equal-within-stock path weighting. Every direct
head and outer fold ran four purged train-only HPO trials using IC.

All candidates tuned 30-, 60-, or 120-minute causal contexts. Ridge flattened
the transformed OHLCV window and combined it with side features and one-hot
symbol identity, fitting deterministic LSQR. The TCN used causal dilated
convolutions, a side-feature projection, and learned symbol embedding. The
small Transformer used causal masking, learned positions, a side-feature
projection, and learned symbol embedding. Neural searches also covered width,
depth, regularization, optimization, and training-size choices declared in
the approved configuration.

## Interpretation

P15 does not support more sequence-model complexity for this representation.
Its three within-zoo pairwise tests all failed to reject equal performance,
and each candidate's positive mean depended on one common validation period.
Ridge's slight descriptive lead over P14 LSTM is not a significance claim;
P14 and P15 are separate benchmark inventories even though they share the
sequence cache and design.

No production object or finalist refit was written. Fold fits are ephemeral
evaluation objects; the durable result is the comparison artifact. No
candidate was auto-promoted. ADR-0105 remains accepted and not locked.

## Execution and verification

Final staging began at 19:10 UTC and comparison completed at 22:48 UTC, about
3 hours 38 minutes. Execution used one fold worker, reused the four P14-v2
caches, stayed within the approved memory design, and left the pmquant
websocket recorder running.

The three candidate summaries each report `state=ran`, 20 folds, and 20 scored
folds. Scoped temporal-model tests, configuration validation, Ruff, and
`git diff --check` were clean at wrap. The broader prelaunch suite had six
known unrelated failures: four around the P12 continuation config enumerator
and two existing Kronos purity assertions; none reads the P15 candidate paths.

## Immutable evidence

- configuration: `configs/run-p15-temporal-fusion-zoo.json`
- benchmark identity:
  `5e88726bb863d8368c68b8a9532e878a2e8f72d1aef32952a35201afcff4d8ff`
- approved inventory:
  `65bbbfa44752b21f1a817e6b47d7e02984e56fbe8a5392a830a478cd977e6058`
- staged run:
  `pipeline_runs/p15-temporal-fusion-zoo-staged-2026-02-28-5e88726b`
- comparison artifact: `stages/compare.json`, SHA-256
  `47bfa13576dc3bf14d8e2a09617e5b2630f8252435c166d659b5495f37104738`
- Ridge summary:
  `pipeline_runs/ridge-pooled-h10-walkforward-2026-02-28-027b0eb0`
- TCN summary:
  `pipeline_runs/tcn-pooled-h10-walkforward-2026-02-28-6765350e`
- Transformer summary:
  `pipeline_runs/transformer-pooled-h10-walkforward-2026-02-28-8c0feaa6`
- candidate and comparison journal evidence: A18772-A18776
- result memo journal record: A18777
- nine-model selector:
  `pipeline_runs/model-select-staged-2026-02-28-ef2e8f37/stages/select.json`
- selector artifact SHA-256:
  `df474f644fa918edead34a4e034ebdbb415c2d819e37cd178df35be92a4bcf0e`

## Immutable evidence — P16 and P17

- P16 configuration: `configs/run-p16-tft-fusion-zoo.json`
- P16 benchmark identity: `18568e4401f0168c3ef4340e131ff95e3b1f61e9ab0c0fc581a211301613d349`
- P16 approved inventory: `dadff5d74c4ebe1a86f3bf93c7964d81e7ad5c5c291ec776a0678b6f351ae73f`
- P16 staged run: `pipeline_runs/p16-tft-fusion-zoo-staged-2026-02-28-18568e44`
- P16 comparison artifact: `stages/compare.json`, SHA-256
  `eb96d00cd14a4ead361ef54dcbd050f2aaf64e002c0175800196d73f4da1f859`
- TFT summary: `pipeline_runs/tft-pooled-h10-walkforward-2026-02-28-f52a027c`
- P16 Ridge summary: `pipeline_runs/ridge-p16-pooled-h10-walkforward-2026-02-28-6f928fc1`
- P17 configuration: `configs/run-p17-randomforest-zoo.json`
- P17 approved inventory: `c97fb8d47dfd88e84495e1a457a0adf93c8d5b3a7c1caf55da0263b3c47ce081`
- P17 terminated after two RF folds (owner decision); the paired LightGBM
  re-run completed: `pipeline_runs/lgbm-p17-pooled-h10-walkforward-2026-02-28-c9b824ba`
  (mean 0.006401, a bit-reproducible match to the P13 LightGBM).
- journal evidence: P16 A18784, A18788, and A18791-A18798 (including TFT walk
  A18796); P17 A18783, A18785-A18787, A18789-A18790, and A18799-A18800
  (including LightGBM A18799 and the RF termination/result memo A18800).
  Correction A18801 supersedes only A18800's statistical wording.
