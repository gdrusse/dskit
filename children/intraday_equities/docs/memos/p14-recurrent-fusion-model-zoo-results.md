# P14 recurrent-fusion model-zoo results

## Outcome

The corrected, owner-approved P14 model zoo completed all 20 paired outer
folds for both recurrent candidates and all ten direct leads. The pooled LSTM
ranked first by mean path score and is the selected simpler frontier. The
paired test did not detect a performance difference between LSTM and GRU.

| rank | candidate | mean path score | fold standard deviation |
| ---: | --- | ---: | ---: |
| 1 | pooled OHLCV LSTM fusion | 0.001174 | 0.008930 |
| 2 | pooled OHLCV GRU fusion | -0.000417 | 0.008949 |

GRU minus LSTM was -0.001591 with HAC standard error 0.000993 and
`p=0.109186`; the single-pair adjusted threshold was 0.05. The comparison
therefore selected the lower-rank LSTM frontier without claiming a
statistically reliable win.

The positive LSTM mean is fragile. LSTM was positive in 12 of 20 folds, with
median 0.000678, but its mean excluding the best 2023-05-19 fold was
-0.000498. GRU was positive in 10 of 20 folds, with median 0.000012, and its
mean excluding that same best fold was -0.001913. Neither candidate is
promotion evidence.

## What was tested

Both models used the fixed 25 P12 Gate-3 survivors, twenty paired 63-day
validation folds, a 730-day training window, five-day embargo, data no later
than 2026-02-28, the ten-lead direct path, and equal-stock/equal-within-stock
weighting. Every direct head ran four purged train-only HPO trials.

At each eligible 30-minute origin, the recurrent tower received only the
latest 30, 60, or 120 one-minute OHLCV slots. A separate linear projection
received the declared origin-time calendar, overnight, and SPY side features;
a learned symbol embedding joined both towers only at the final linear head.
HPO covered context length, recurrent hidden width and depth, side-projection
width, symbol-embedding width, epochs, learning rate, weight decay, batch size,
and dropout for each family independently.

Prices were transformed into causal log ratios to the origin close, volume
was log-scaled, and all fitted moments used training rows only. The experiment
did not pool side information into the recurrent state and did not fine-tune a
foundation model.

## Sparse-minute failure and correction

The first launch stopped in LSTM fold four because MSTR had no complete scored
path: a strict 120-consecutive-minute requirement left only one sequence
origin in that validation period. The minute aggregate omits no-trade minutes,
so strict contiguity was not a valid representation of the available tape.

The corrected, explicit policy fills only gaps of at most five minutes. A
missing slot carries the last observed close into open/high/low/close and sets
volume to zero. It is causal, and gaps longer than five minutes and all session
boundaries remain ineligible. The strict policy remains the default for other
documents. Focused tests cover the fill and long-gap refusal.

The corrected MSTR cache contains 9,504 origins overall and 198 in the failed
fold's validation interval, versus one before the fix. Both corrected
candidates subsequently completed all 20 folds.

## Interpretation

This ablation does not support replacing the P13 engineered tabular frontier
with a simple recurrent OHLCV tower. The LSTM's mean is positive but small,
unstable across folds, and dominated by one interval; the GRU mean is slightly
negative. P13 native LightGBM remains the practical development frontier.

That P13 conclusion is contextual rather than a new paired statistical claim:
P14 has sequence-eligibility filtering and was compared only within its own
two-candidate approved inventory. A formal six-candidate conclusion would
require a separately approved joint rerun on one common origin set. The
current evidence does not justify that compute expense before a sharper
sequence hypothesis exists.

No production object or finalist refit was written. Model-zoo fold fits are
ephemeral evaluation objects; the durable result is the comparison artifact.
No candidate was auto-promoted, and ADR-0104 remains accepted but not locked.

## Execution and memory notes

The corrected approved run began its final staging at 16:19 UTC and completed
comparison at 18:16 UTC, about 1 hour 57 minutes. It used one fold worker and
left the pmquant websocket recorder running. The corrected four-cache rebuild
took about 15 minutes before the final run. Cache-A build peak RSS was
9,115,414,528 bytes (about 8.49 GiB), below the 18,253,611,008-byte limit.

Corrected cache manifest SHA-256 values:

- A: `9355ac92f0727c9b64a3f4ae72bd36ecb5a192eda0d432215afa3f7b489bacc3`
- C: `444afa763efbbbe784af58c54b1befaddc18b14d1efbf201d10e934d68475a56`
- D: `bcf959e731fdf0b199b6d3d9fb5aaf62e491abe11aae0ae21bb56bf655fe4fc1`
- E: `9cea52d0022cf9a3118f4339cdd9db95479de6102002510b157ceda32be77bc9`

Final Major/Critical Bugbot review was clear. The scoped P14 suite passed with
253 tests and 21 skips; Ruff, configuration validation at benchmark identity
`70b5a399…`, and `git diff --check` were clean. A broader config-enumerator run
also exposed four unrelated existing failures tied to
`run-p12-gate3-continuation.json`; none names or reads P14 code.

## Immutable evidence

- configuration: `configs/run-p14-recurrent-fusion-zoo.json`
- benchmark identity:
  `70b5a3998e9148852fef4e84637a2e41b631c7f776db9d2a40b4d660d9ae0036`
- approved inventory:
  `85e1fb8cbacba2d1c3524a2decc3cb95b1d6af732d009b337728538078f9bcfc`
- staged run:
  `pipeline_runs/p14-recurrent-fusion-zoo-staged-2026-02-28-70b5a399`
- comparison artifact: `stages/compare.json`, SHA-256
  `12b893e4706919018ecdf15837b4c4abc4acaaeea94c6f6afa9c36d70dacfe75`
- selected frontier summary:
  `pipeline_runs/lstm-pooled-h10-walkforward-2026-02-28-def2f1fe`
- candidate and comparison journal evidence: A18752-A18755
- result memo journal record: A18756
