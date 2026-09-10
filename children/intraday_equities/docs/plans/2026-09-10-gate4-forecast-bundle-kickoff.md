# Gate 4 kickoff — real forecast bundle and confirmed caps

**Status:** START NOW — §11 item 3 is ruled (2026-09-10, below).
**Model track:** GLM 5.2 writes the first TDD pass (used once); after that,
fresh reviewer/rewriter agents run every skeptic round and every follow-up
fix, one at a time, never in parallel.
**Suggested branch:** `glm/gate4-forecast-bundle-<id>`, branched from
`origin/claude/phase1-recovery-seven-gates-ao4zdj` — **not** `main`. ADR-0114
and the completed Gate 1/Gate 2 work this plan builds on live only on that
branch; `main`'s decision-log tops out at the unrelated ADR-0112. Verify
ADR-0114 is actually present in `docs/architecture/decision-log.md` after
checkout before doing anything else.

**Push every round.** Commit and `git push` after every commit — the first
pass, every skeptic fix, everything. Do not batch pushes to the end; nobody
can see unpushed work.

Full scope: `docs/plans/2026-09-08-final-model-replay-and-monitoring.md` §6
Phase 4 and §9 (reviewer lens 6, "MIO/guardrail skeptic"); file ownership:
`docs/architecture/decision-log.md` ADR-0114, "Phase 4 — real forecast bundle
and confirmed caps" section.

## §11 item 3 — RULED (2026-09-10, owner)

The P16 label is `y(t,h) = [r_i(t,t+h) - beta_i,t*r_SPY(t,t+h)] /
(sigma_i,t*sqrt(h))`, per `intraday_equities/nodes.py`'s `_LeadLabel`
(causal trailing `beta`/`sigma`, `beta_window`=3900 min, `vol_window`=390
min). `r_SPY(t,t+h)` itself is not known at decision time `t`, so a direct
algebraic inverse needs a forward SPY estimate. Ruling: assume a **zero-drift
market reference** — `E[r_SPY(t,t+h)] ~= 0` over the model's short forecast
horizons. The point-in-time gross-return forecast is therefore:

```
gross_return_i(t,h) = yhat_i(t,h) * sigma_i,t * sqrt(h)
```

using the SAME causal `sigma_i,t` (trailing std of the beta-hedged residual
return series, `vol_window`=390 min, `vol_floor`=1e-8) the label used at
training/prediction time — read it from the model bundle's manifest / the
same feature pipeline, never recomputed with different parameters. No new
SPY-forecast model is authorized by this ruling or by ADR-0114's file
inventory; do not add one. Record this ruling in your own phase ADR (fold it
into ADR-0114 the same way its "§11 item 1 — RULED" section was added) citing
this kickoff file, then proceed.

## Scope

`children/intraday_equities/intraday_equities/forecast_bundle.py` (new):
point-in-time conversion from label units to gross fractional returns per
the ruling above, plus assembly/validation of the ADR-0088/MIO bundle.
Generic calibration estimators graduate to `dskit`; this file only binds
equity fields and reference policy. Extend `nodes_capital.py` to require the
pinned confirmed `(symbol, lead)` cap and refuse absent/zero/stale/
ineligible/over-cap rows, preserving the `stat_test` survivor wire.

Do NOT read or execute against March-May data
(`configs/run-mean-confirmation.json`) — that stays unreadable regardless of
item 3's ruling, per §11 item 4 and plan §8's honesty rules, until the owner
separately unblocks it.

**Process (mandatory, no exceptions):** TDD (failing test first), one
implementer + one fresh reviewer at a time — never parallel, never skip a
re-review after a fix. Write the phase ADR extending ADR-0114 (carrying the
§11 item 3 ruling above) and get it accepted before implementation continues
past config-shape tests. Follow root `CLAUDE.md` OOP standards (subclass a
hook, don't branch; one job per method; `__all__`/`_` boundary; default-deny
params; docstring conventions). Update the child's README/AGENTS/CLAUDE
trees. Record actions through `dskit.journal`; never hand-edit
`docs/decisioning/path.csv`. Run only the focused suites named in plan §10.
Refresh `docs/RE-ENTRY.md` on wrap.
