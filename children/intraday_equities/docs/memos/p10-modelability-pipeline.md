# P10 modelability pipeline handoff

> **No longer used.** Gate 2 and Gate 3 below are historical execution
> evidence, not current stock-selection policy. ADR-0083 places HFDR in MIO.

Completed 2026-09-04 under document
`b7c8efe93664c65a71407f81cd903e47503976c6d6849b9e7bb67b6089e6d8dd`.
The durable result is
`pipeline_runs/p10-25-asset-modelability-staged-2026-02-28-b7c8efe9/`.
Generated fold runs and the feature cache are ignored working artifacts; the
config, code, attempt ledger, journal, ADR, and this memo are committed.

## Study contract

- Cutoff: 2026-02-28; nothing from 2026-03-01 onward was read.
- Horizons: 1, 2, 3, 5, 10, 20, 30, and 60 minutes.
- Fit universe, unchanged at every gate: AAPL, JPM, XOM, WMT, LLY, SPY, QQQ,
  XLF, XLV, XLE, XLK, XLP, TSLA, TQQQ, NVDA, AMD, UPRO, BAC, AMZN, AVGO,
  NFLX, MSFT, GOOGL, SMH, and IWM. META is absent.
- `alpaca-sip-split-c` supplies UPRO, BAC, AMZN, AVGO, NFLX, MSFT, GOOGL,
  SMH, and IWM.
- SPY's own target is its volatility-scaled raw forward return; the other 24
  targets retain SPY residualisation. This makes SPY a real 25th fit series.
- GROUP is suppressed throughout.

## Implementation

ADR-0081 adds a generic staged document/runner. Each stage has a stable token,
digest-checked JSON artifact, and append-only journal row. Resume trusts only
matching journal plus artifact evidence. Partial child walks remain refusals.

P10 uses six stages: memory preflight, Gate-1 walks, Gate-1 reduction, Gate 2,
Gate-3 walks, and Gate-3 reduction. Gate-3 scoring is selected after fitting,
so every real and scrambled fold still refits the identical pooled 25-asset
model. Walks run as isolated one-fold child processes and aggregate afterward.

The memory path transfers consumed source lists, uses float32 feature/design
arrays, atomically writes a SHA-256-manifested cache, verifies it once per
staged invocation, and opens arrays read-only by memory map in fold children.

## Memory evidence

The required most-recent 25-asset-fold preflight passed:

- peak RSS: 17,066,532,864 bytes (15.90 GiB);
- limit: 18,253,611,008 bytes (17 GiB);
- cache manifest SHA-256:
  `0bd3d1a9c9c66328340396c8b05d0dc69ead9876096708ff5109731a888ff760`;
- stage artifact: `stages/memory.json`.

No asset was removed to meet the limit.

## Gate outcomes

Gate 1 wrote and registered all 200 asset-horizon cells before filtering.
Eleven assets retained a consecutive horizon:

- 20 minutes: LLY, TQQQ, UPRO;
- 10 minutes: XLF, XLK, AVGO, NFLX, IWM;
- 3 minutes: QQQ, SMH;
- 1 minute: BAC.

Gate 2 used one study-wide 200-cell, 865-session max-statistic family. Its
critical value was `3.421996593475342`. It tested only each Gate-1-selected
horizon, with no shorter fallback, and retained QQQ at 3 minutes and NFLX at
10 minutes.

Gate 3 ran seeds 0 through 18 for both survivors. Every null refitted the same
25-asset pooled model. Both real cells beat all 19 nulls, but both failed the
frozen null-spread calibration:

- QQQ@3: null mean -0.793, SD 0.608;
- NFLX@10: null mean 0.032, SD 0.667.

The accepted SD interval is `(0.7, 1.4)`, so P10 has no final modelability
pass. Do not relax that threshold after observing these results. The next work
is a prospective explanation and remediation of the narrow null spreads.

## Verification and repository note

- P10 config validation passed with the document hash above.
- Ruff passed over every changed Python module and focused test.
- Core pipeline plus skeleton: 295 tests passed.
- Child modelability, feature-cache, and node suites: 183 passed, 11 skipped.
- Artifact audit: six completed stages, 200 Gate-1 cells, a 200-cell Gate-2
  family, 38 Gate-3 null walks, exactly 25 final rows, no META or GROUP, and
  zero final passes.

`docs/decisioning/actions.csv` is append-only execution evidence. Five emitted
error rows end with a space after an empty exception message, so `git diff
--check` reports those historical rows. Preserve them; rewriting the ledger to
remove whitespace would alter journal history.
