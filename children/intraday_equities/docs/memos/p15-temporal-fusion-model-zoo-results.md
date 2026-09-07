# P15 temporal-fusion model-zoo results

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

## Nine-model reference across completed zoos

This reference includes every model in the three completed zoos. Rankings
across P13, P14, and P15 are descriptive only: the studies did not all use the
same eligible origin rows or approved inventory, so cross-zoo differences are
not paired hypothesis tests. Statistical claims remain confined to each zoo's
own comparison artifact.

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

The broad evidence still favors P13 pooled native LightGBM as the practical
development frontier: it has the highest descriptive mean, the lowest-cost
strong representation, and the only result positive in all twenty folds.
P15 adds a useful negative result: changing recurrent sequence bias to
convolution or causal attention did not beat a flattened linear sequence
baseline on the common P14/P15 sequence-eligible design.

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
