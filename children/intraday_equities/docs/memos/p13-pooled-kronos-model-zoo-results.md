# P13 pooled and Kronos model-zoo results

## Outcome

The owner-approved four-candidate P13 model zoo completed all 20 outer folds
for all ten direct leads. Native pooled LightGBM ranked first and is the
selected simplest candidate. Pooled Torch MLP was close enough that the
paired test did not detect a difference. Both frozen-Kronos representations
were materially worse than both tabular baselines in this experiment.

| rank | candidate | mean path score | fold standard deviation |
| ---: | --- | ---: | ---: |
| 1 | pooled LightGBM | 0.006401 | 0.002358 |
| 2 | pooled Torch MLP | 0.005322 | 0.005516 |
| 3 | Kronos hidden + LightGBM | 0.001175 | 0.001247 |
| 4 | Kronos hidden + Torch MLP | -0.003852 | 0.004458 |

LightGBM minus Torch MLP was 0.001078 with HAC standard error 0.001234 and
`p=0.382351`; the all-pairs Bonferroni threshold was 0.008333. The comparison
therefore selected the lower-compute LightGBM frontier candidate and did not
claim that it statistically beat the tabular MLP.

The Kronos challengers did not tie the baselines. Kronos+LightGBM trailed
native LightGBM by 0.005226 (`p=8.92e-30`) and the tabular MLP by 0.004148
(`p=6.81e-05`). Kronos+MLP trailed native LightGBM by 0.010253
(`p=1.12e-21`) and the tabular MLP by 0.009175 (`p=7.07e-20`).

## What was tested

The common outer protocol used the 25 P12 Gate-3 survivors, twenty 63-day
validation folds, a 730-day training window, five-day embargo, the exact
ten-lead direct path, equal stock/equal within-stock weighting, and data no
later than 2026-02-28. Every candidate used four purged inner HPO trials.
That search budget is an exploratory screen, not an exhaustive optimized
ceiling; MLP width and depth were included in its search.

The two Kronos candidates used locally verified, manifest-pinned
`NeoQuasar/Kronos-Tokenizer-base` and `NeoQuasar/Kronos-small` snapshots with
the official source pinned at revision
`67b630e67f6a18c9e9be918d9b4337c960db1e9a`. The backbone was frozen. Each
30-minute origin received the final 512-dimensional hidden state from an
exact upstream-style mean/std-normalized, session-local causal OHLCVA prefix.
Only the declared calendar features, SPY return, and symbol representation
were fused beside it; the richer P12 OHLCV-derived feature matrix remained
specific to the two tabular baselines.

## Interpretation

Pooling itself is supported: both pooled tabular candidates produced positive
mean outer-fold improvement, and the simpler native LightGBM was positive in
all twenty folds. The result does not support replacing the engineered P12
representation with frozen Kronos hidden states. The frozen hidden state may
discard useful cross-session or absolute-scale information, and four HPO
trials cannot rule out better heads. The negative Kronos+MLP mean also warns
against assuming that a neural head automatically extracts the useful signal.

The next defensible Kronos experiment, if pursued, is a separately approved
ablation that adds the frozen hidden state to the full nonduplicative P12
feature set, rather than substituting nearly all engineered features. Fine
tuning should wait until that cheaper ablation shows incremental value. The
Kronos pretraining corpus is not disclosed with enough detail to establish
stock/date contamination certainty, so all Kronos results remain exploratory
and are not promotion evidence.

## Execution and memory notes

The first cache-build attempt was killed by the WSL system OOM before model
training because the inherited P12 D universe materialized unused breadth
names. P13 D/E cache universes were narrowed to the approved cohort members
plus SPY, preserving every data and label geometry field; the second attempt
completed. The earlier false `RUSAGE_CHILDREN` failure was traced to launching
through a WSL login shell and was avoided by direct WSL process launch.

Final hostile review also found that slices of each padded Kronos hidden batch
would retain their full backing arrays. The implementation now copies each
final hidden row immediately; an ownership regression and a real pinned
128-session GPU smoke both pass. Final Bugbot review reported no remaining
Major or Critical finding.

The approved run started at 04:41 UTC and completed comparison at 12:06 UTC
(about 7 hours 25 minutes). Candidate completion rows are A18704-A18707;
staged run and compare rows are A18708-A18709. The pmquant websocket recorder
was left running throughout.

## Immutable evidence

- configuration: `configs/run-p13-pooled-model-zoo.json`
- benchmark identity:
  `b017ea1b2235d24e6b7f20d141350db0c3c32a2127e032c22939335f85bab7f6`
- approved inventory:
  `8731619d0eabb93246ebf72b6993f4a2e858c05bb775eb4a0ef5f6c589b33605`
- staged run:
  `pipeline_runs/p13-pooled-model-zoo-staged-2026-02-28-b017ea1b`
- comparison artifact: `stages/compare.json`, SHA-256
  `924e630dbb41bae0d4baae3c6538512a9bca56aaffd6d4d37604d1d3db60dfb1`
- selected frontier summary:
  `pipeline_runs/lgbm-pooled-h10-walkforward-2026-02-28-9a5e6806`

No candidate was auto-promoted, and the P13 path remains marked non-locked.
