# RE-ENTRY - index_options

Updated: 2026-11-02 by GPT-6 Luna. ADR-0278 amendment synthetic RL implementation is reviewed and wrapped.

## Now

- Reviewed candidate `4d64f3f663446b08934f5fd05facbed5ed4f194d`; evidence checkpoint `188b71409fa9d951346a140a6fefb41935ebd9aa`. Two fresh Luna lenses: C0/M0, one disclosed deferred Minor (public MAD helper accepts nonsimplex inputs).
- Synthetic only: one-entry-to-expiry PPO smoke, 32 CPU steps, pinned SB3 restore, deterministic paired MIO validation and saved episode artifact. No real-data training, replay, or profitability conclusion.
- Checks: 996 affected functional passes / 30 skips and four docs checks; focused RL module 24 passed. Baseline purity failure and five lint findings reproduce unchanged on base; no full suite ran.
- Prior Q2-Q4 reports and memo remain unchanged and are archived below.

## Next

- Before empirical work, pin frozen predictive inputs, the method/config and dates, and an approved paired-study packet. Obtain separate owner approval before any real-data fit or comparison.

## Blocked / owner decisions

- No unresolved Critical or Major findings. The direct `MeanAbsoluteDeviation` nonsimplex-input Minor is deferred for a separately reviewed generic validation fix.
- No real-data or protected-window use is authorized by this synthetic integration.

## Read first

- [ADR-0278 amendment and review evidence](../../../docs/architecture/decision-log.md).
- [Synthetic RL pipeline](../configs/rl-entry-synthetic.json).
- [Prior Q2-Q4 reports](reports/mio-2021q2-q4-walkforward-20261101/README.md).
- [Prior Q2-Q4 execution memo](memos/2026-11-01-mio-q2-q4-walkforward.md).
- [Archived previous lane note and delivery context](../../../docs/archive/re-entry/index_options.md).

## Do not

- Read sealed or protected market outcomes, run real-data fits/replays, or make a profitability claim.
- Change locked Path A0001-A0008, other sessions' worktrees, prior reports, source evidence, or live recorders.
- Fix the deferred MAD Minor inside this reviewed candidate.
