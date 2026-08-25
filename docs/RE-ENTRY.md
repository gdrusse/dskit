# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**MERGED (2026-08-25):** this branch now carries BOTH parallel sessions —
the ADR-0024/0025 faithful parent ports (which supersede main's bespoke
ADR-0025 implementation, per the ADR-0025 amendment) AND main's
ADR-0027..0030 capability set below. 0024 is accepted and landed, not
proposed. `/wrap` will rewrite this file.

**Branch:** `main` (rl_stocks bones pass merged) · **Tests:** 2056 pass,
106 skip (env with torch/sb3/gymnasium/matplotlib/optuna/pyomo/sklearn/
pyarrow; transformers still skips; other skips are inapplicable
conformance-probe slots) · **ruff:** clean

**Landed (rl_stocks bones pass + skeptic pass):** owner directive —
"ensure dskit has the generalizable functionality to build rl_stocks; no
wrappers, nothing project-specific." Re-inventoried the REAL rl-stocks
(~66k LOC; the prior report had analyzed a stale snapshot) via four
subsystem surveys; `child-gap-rl-stocks.md` rewritten. Five generic gaps
closed: **ADR-0025 accepted** (a bespoke build, superseded on merge by
the faithful parent port — see the ADR amendment): `torch-train`/
`torch-predict` declared kinds, `trainlog.py`, regression metrics
survive in ported form; **ADR-0027** `walkforward` section + verb (one run
dir per fold + summary; half-open, embargo-invariant cuts;
`val_start_ms`/`embargo_days` embargo bands — identity-preserving);
**ADR-0028** `libs/sb3.py` (declared algo + env class, hash-pinned
artifacts); **ADR-0029** `libs/matplotlib.py` (`mpl-figure` +
`FigureNode`); **ADR-0030** `onboarding/coverage.py` (`CoverageLedger`).
Four examples (three run e2e), ~260 new tests. High-effort skeptic
review: 9 findings fixed (headline: sequence-over-flat-artifact refusal;
fold-boundary embargo invariance; refused folds recorded), 1 declined
with reason (torch/sb3 artifact-protocol duplication — formats diverge
legitimately). Identity hashes proven byte-identical vs pre-pass main.

**Decisions awaiting user:** ADR-0026 (report tables) stays PROPOSED
(ADR-0024 was ratified and ported on the merged branch). Below the
line: restapi OAuth2 token strategy,
cross-sectional-IC helper, calibration/stats pack (see child-gap
reports).

**Next session:** optionally rule on 0026; optionally incubate
`children/rl_stocks` per the revised sketch — every seam it needs now
exists.
