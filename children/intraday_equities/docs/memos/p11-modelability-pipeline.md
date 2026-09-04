# P11 asset-local modelability pipeline

> **No longer used.** Gate 2 below is historical execution evidence, not
> current stock-selection policy. ADR-0083 places HFDR in MIO.

## TL;DR

P11 tested whether a separate model for each asset could survive an ordered
horizon search and then repeat its success on untouched data. Thirteen of 25
assets reached confirmation, but none passed the fixed 25-asset correction.
There is no validated P11 model and no fallback or Gate-3 continuation.

## Execution contract

- Completed: 2026-09-04.
- Command:
  `python -m dskit.pipeline staged configs/run-p11-modelability.json --asof 2026-02-28 --adapter intraday_equities`.
- Document identity:
  `355b6198d9d4758f924af390ca28407267598772119e50182404a55df8cb416f`.
- Staged result:
  `pipeline_runs/p11-25-asset-modelability-staged-2026-02-28-355b6198/`.
- Stages, in order: `memory`, `gate1`, `gate2`. No Gate-3 stage exists.
- Frozen assets: AAPL, JPM, XOM, WMT, LLY, SPY, QQQ, XLF, XLV, XLE, XLK,
  XLP, TSLA, TQQQ, NVDA, AMD, UPRO, BAC, AMZN, AVGO, NFLX, MSFT, GOOGL,
  SMH, and IWM. META and GROUP are absent.
- Every fit and score uses one asset only. SPY remains tape-only when another
  asset needs the market-return reference; it is not a second training series.
- Gate 1 uses 20 validation folds ending before the confirmation block. Gate 2
  uses one untouched fold from 2025-12-02 through 2026-02-28. No observation
  dated 2026-03-01 or later is read.

The config, implementation, ADR-0082, attempt ledger, execution journal, and
this memo are committed. Generated fold runs and staged artifacts remain local
ignored evidence.

## Decision math

For row `t` in fold `f`, let `y_t` be the outcome, `m_t` the model
forecast, and `b_f` the constant training-mean forecast. The model's loss
advantage is

`d_t = (y_t - b_f)^2 - (y_t - m_t)^2`.

Positive `d_t` means the model made the smaller squared error. Each fold is
scaled by its benchmark mean squared error `q_f`, giving
`g_t = d_t / q_f`. Gate 1 uses

`t_pool = mean(g_t) / HAC_SE(mean(g_t))`

and the fold-level effect

`R2_f = 1 - MSE_model,f / MSE_mean,f`,

`t_fold = mean(R2_f) / (sd(R2_f) / sqrt(20))`.

A Gate-1 cell passes only when both effects are positive and both one-sided
p-values are at most 0.05. Horizons are tried in the order
`1,2,3,5,10,20,30,60`; the first failure stops the asset and selects the last
consecutive pass. P11 ran 66 of the maximum 200 cells, avoiding
`200 - 66 = 134` later cells, or `134 / 200 = 67%`.

Gate 2 converts the untouched pooled statistic to a one-sided normal tail:

`p = 1 - Phi(t_pool) = 0.5 * erfc(t_pool / sqrt(2))`.

The fixed family contains all 25 assets, so each keeps

`alpha_asset = 0.05 / 25 = 0.002`.

The reported adjusted value is
`p_adjusted = min(25 * p, 1)`, and a result passes only when
`p <= 0.002`. The union bound gives
`P(any false pass) <= sum(alpha_asset) = 25 * 0.002 = 0.05` without assuming
independent assets. Unused allocations are not recycled.

UPRO was closest:

- `p = 0.5 * erfc(2.219043 / sqrt(2)) = 0.013241891`;
- `p_adjusted = min(25 * 0.013241891, 1) = 0.331047273`;
- `0.013241891 > 0.002`, so UPRO failed.

## Memory result

The longest confirmation-shaped preflight used SPY, the largest cached asset:

- feature rows: 159,710;
- peak RSS: 1,063,534,592 bytes = 0.990 GiB;
- limit: 18,253,611,008 bytes = 17 GiB;
- headroom: 17 - 0.990 = 16.010 GiB;
- feature-cache manifest:
  `0bd3d1a9c9c66328340396c8b05d0dc69ead9876096708ff5109731a888ff760`.

The preflight passed; no asset was removed.

## Gate-1 stops and selections

The following 12 assets failed at h=1, selected nothing, never reached Gate 2,
and left h=2,3,5,10,20,30,60 unrun:

- AAPL: t_pool=1.008153, t_fold=1.143228.
- JPM: t_pool=0.397868, t_fold=0.402205.
- XOM: t_pool=-1.234723, t_fold=-0.931425.
- WMT: t_pool=0.337640, t_fold=0.362131.
- SPY: t_pool=0.074228, t_fold=0.053975.
- XLV: t_pool=0.920708, t_fold=0.732231.
- XLP: t_pool=-0.797950, t_fold=-0.749721.
- TSLA: t_pool=0.194984, t_fold=0.137289.
- AMD: t_pool=-1.383057, t_fold=-1.232469.
- AMZN: t_pool=-1.481556, t_fold=-1.774042.
- MSFT: t_pool=1.699692, t_fold=1.617524.
- GOOGL: t_pool=1.209308, t_fold=0.699420.

The 13 survivors stopped or completed as follows. Each pair of statistics is
from the stopping failure, not the selected cell:

- LLY failed h=5 (-1.891937, -1.833775), selected h=3; h=10,20,30,60 unrun.
- QQQ failed h=2 (0.527315, 0.453144), selected h=1; h=3,5,10,20,30,60 unrun.
- XLF failed h=5 (1.658054, 1.452969), selected h=3; h=10,20,30,60 unrun.
- XLE failed h=2 (0.484796, 0.470220), selected h=1; h=3,5,10,20,30,60 unrun.
- XLK failed h=10 (-1.197343, -1.317730), selected h=5; h=20,30,60 unrun.
- TQQQ failed h=5 (1.706850, 1.456884), selected h=3; h=10,20,30,60 unrun.
- NVDA failed h=3 (0.612093, 0.601557), selected h=2; h=5,10,20,30,60 unrun.
- UPRO passed through h=60 and selected h=60; no later horizon exists.
- BAC failed h=2 (2.068012, 1.636812), selected h=1; h=3,5,10,20,30,60 unrun.
- AVGO failed h=20 (0.207286, 0.234068), selected h=10; h=30,60 unrun.
- NFLX failed h=5 (0.037218, -0.020311), selected h=3; h=10,20,30,60 unrun.
- SMH failed h=10 (-0.576177, -0.612941), selected h=5; h=20,30,60 unrun.
- IWM failed h=10 (1.700390, 1.369883), selected h=5; h=20,30,60 unrun.

Thus `13 / 25 = 52%` reached confirmation. This is a selection rate, not an
estimate of how often future assets will succeed.

## Gate-2 ledger decisions

Each line gives asset, selected horizon, untouched rows, out-of-sample R2,
`t_pool`, raw p, adjusted p, and decision:

- LLY h3: n=715, R2=0.00478936, t=1.219817, p=0.111267168, adjusted=1, fail.
- QQQ h1: n=719, R2=-0.00078089, t=-0.275055, p=0.608363151, adjusted=1, fail.
- XLF h3: n=712, R2=-0.00463512, t=-1.377562, p=0.915830715, adjusted=1, fail.
- XLE h1: n=713, R2=-0.00036119, t=-0.124958, p=0.549721698, adjusted=1, fail.
- XLK h5: n=715, R2=0.00213517, t=0.758762, p=0.223997560, adjusted=1, fail.
- TQQQ h3: n=719, R2=-0.00096781, t=-0.379844, p=0.647969475, adjusted=1, fail.
- NVDA h2: n=719, R2=-0.00338511, t=-1.721665, p=0.957434883, adjusted=1, fail.
- UPRO h60: n=713, R2=0.08764090, t=2.219043, p=0.013241891,
  adjusted=0.331047273, fail.
- BAC h1: n=712, R2=0.00022704, t=0.075562, p=0.469883964, adjusted=1, fail.
- AVGO h10: n=717, R2=-0.00629265, t=-1.962564, p=0.975151558,
  adjusted=1, fail.
- NFLX h3: n=718, R2=0.00412668, t=1.072117, p=0.141833744,
  adjusted=1, fail.
- SMH h5: n=715, R2=0.00174465, t=0.507200, p=0.306007165,
  adjusted=1, fail.
- IWM h5: n=715, R2=-0.00343425, t=-1.520719, p=0.935834805,
  adjusted=1, fail.

All 13 ledger entries are final failures: `0 / 13 = 0%` of confirmations and
`0 / 25 = 0%` of the frozen cohort passed. These observed rates do not prove
that the true success rate is exactly zero.

## Verification and handoff

- The ledger contains one immutable 25-key header, 66 Gate-1 attempts, 13
  Gate-2 attempts, and 13 fixed-family result rows.
- All three stage artifacts are complete and digest-checked; no Gate-3 artifact,
  attempt, journal row, or process exists.
- Sixty focused tests passed: 56 pipeline/modelability/cache tests and four
  config, tracking, and identity tests.
- Ruff and `git diff --check` passed.
- P10 retains identity
  `b7c8efe93664c65a71407f81cd903e47503976c6d6849b9e7bb67b6089e6d8dd`.
- The full suite was not rerun. An unrelated pre-existing config pin still
  rejects `run-pb-s01-h01-lgbm-cross.json` starting in 2020 rather than 2018.

P11 stops here. Review and merge the branch if the execution contract,
fixed-family correction, and recorded no-pass result are accepted.
