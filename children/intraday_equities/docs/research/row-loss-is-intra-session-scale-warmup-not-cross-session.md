# Question

Why do `n_train` and `n_val` come in at ~54% of what the calendar
implies in every fold of `run-jpm-h20.json` (A0037)?

# Finding

**A scale with `cross_session: false` and width W emits NaN for the
first W minutes of EVERY session, and `_frame_matrix` drops any row
with a non-finite value in any column.** The widest such scale is
`3h` = 180 minutes against a 390-minute RTH session:

    390 - 180 = 210 usable of 390 = 53.8%

which matches the observed ~54% exactly, in all seven folds.

**The earlier hypothesis in
`jpm-h-20-6-of-7-folds-negative-hpo-tunes-noise.md` §4 was wrong, and
backwards.** It blamed the `cross_session: true` scales (`2s`, `1w`).
Those cost a one-time warmup at the tape start and are effectively free
over years. The permanent cost is the *intra-session* scales, because
their warmup recurs every trading day.

## Measured (60 synthetic RTH days, JPM, 4,680 grid rows)

| configuration | all-finite rows | share |
|---|---|---|
| as declared | 2,310 | 49.4% |
| `2h`+`3h` flipped to `cross_session` | 3,630 | 77.6% |
| every momentum horizon cross-session | 3,630 | 77.6% |

Flipping two flags is 1.57x the rows, ~1.25x off the detection floor.
It is a config edit, not a code change.

## Remaining constraint after that

`universe.scales`, not `momentum_horizons`:

| scale | width | cross_session | cost |
|---|---|---|---|
| 5m | 5 | false | 1.3% / session |
| 15m | 15 | false | 3.8% / session |
| 60m | 60 | false | 15.4% / session |
| 1s | 390 | true | warmup only |
| 3s | 1170 | true | warmup only |

Flipping `60m` as well reaches ~85-90%, about 1.8x the original sample.

## The trade, which is a domain call

`cross_session: true` makes a 3-hour window at 10:00 read back through
the overnight gap into the prior afternoon. That changes what the
feature MEANS; it is not a free win. At 5-20 minute horizons the wide
scales are contextual (what regime is this) rather than directional, so
bridging the close is defensible — but it belongs in `universe.json`
notes either way. The alternative is to drop `2h`/`3h`, neither of
which survived the earlier keep-feature selection.

## Incidental: SessionFeatureRows mutates its input

Running the node twice on the same `records` list yields different
results on the second call; a `deepcopy` of the input removes the
difference. In a pipeline each node runs once, so this is a latent
hazard rather than an active defect, but it makes the node unsafe for
A/B diagnostics in one process.

# Sources

## Repo

- `intraday_equities/nodes.py` — `_frame_matrix` (2450) drops rows on
  `np.isfinite(x).all(axis=1)`; `_scan_aligned` (2499); the
  `momentum_horizons` knob on `SessionFeatureRows` (1067).
- `configs/universe-jpm-h20.json` — `scales`, and the run document's
  `momentum_horizons`.
- `configs/run-jpm-h20.json` — the seven-fold run whose row counts this
  explains.

## Prior notes

- `docs/research/jpm-h-20-6-of-7-folds-negative-hpo-tunes-noise.md` §4
  — the hypothesis this note corrects.
