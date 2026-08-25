# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `claude/rl-stocks-ds-kit-actnx7` (not merged — owner review) ·
**Tests:** 2046 pass, 106 skip (env with torch/sb3/gymnasium/matplotlib/
optuna/pyomo/sklearn/pyarrow installed; transformers still skips; the
other skips are inapplicable conformance-probe slots) · **ruff:** clean

**Landed this session (rl_stocks bones pass):** the owner directive —
"ensure dskit has the generalizable functionality to build rl_stocks; no
wrappers, nothing project-specific." (1) Re-inventoried the REAL
rl-stocks (~66k LOC; the prior report had analyzed a stale 1,141-LOC
snapshot) via four subsystem surveys; `child-gap-rl-stocks.md` rewritten
and now supersedes it. (2) Five generic gaps found, all closed:
**ADR-0025 accepted** (implemented fresh — the parent repo is not
reachable from this environment): `torch-train`/`torch-predict` declared
kinds, real loop (optimizer/loss/val + early stopping/grad-clip/device/
`sequence` windows), `trainlog.py`, regression metrics;
**ADR-0027** walk-forward: `walkforward` document section + CLI verb
(one run dir per fold + aggregate summary), embargo bands
(`val_start_ms` on time splits, `embargo_days` on trailing) — all
identity-preserving (new fields omitted when unset);
**ADR-0028** `libs/sb3.py` (document names the RL algo AND the
gymnasium env class; hash-pinned artifacts);
**ADR-0029** `libs/matplotlib.py` (`mpl-figure` declared marks +
`FigureNode` base);
**ADR-0030** `onboarding/coverage.py` (`CoverageLedger` — the sparse
backfill done-set; declared expected periods, audit/reconcile).
(3) Four new examples (walk-forward + torch-declared + mpl-figure run
end-to-end; sb3-train validates — the env is rightly the child's), docs
trees refreshed, ~230 new tests.

**Decisions awaiting user:** ADR-0024 (split policies + event bounds)
and ADR-0026 (report renderer parity) stay PROPOSED — rl_stocks'
needs are covered by the embargo + run-report. Below-the-line register:
restapi OAuth2 token strategy, cross-sectional-IC helper, calibration/
stats pack (see both child-gap reports).

**Next session:** owner reviews this branch (merge is /wrap's call);
optionally rule on 0024/0026; optionally incubate `children/rl_stocks`
per the revised sketch — the seams it needs all exist now.
