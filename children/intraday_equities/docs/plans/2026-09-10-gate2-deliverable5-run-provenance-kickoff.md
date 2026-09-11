# Gate 2 deliverable 5 kickoff — run provenance & content-derived identity

**Status:** START NOW. **Model track:** Claude — Opus writes the first TDD
pass (one sub-agent, used once); Sonnet runs every skeptic round and every
follow-up fix, one fresh agent at a time, never in parallel.
**Suggested branch:** `claude/gate2-run-provenance-<id>`, branched from
`origin/claude/phase1-recovery-seven-gates-ao4zdj`. Verify ADR-0116 is
present in `docs/architecture/decision-log.md` after checkout.

**Push every round.** Commit and `git push` after every commit — the first
pass, every skeptic fix, everything. Do not batch pushes to the end.

## Why this exists

ADR-0116 (Gate 2 deliverable 4) made `intraday_equities.final_model.
FinalRefit` unconditionally refuse both validation and runtime, because "the
current driver owns neither immutable completed-run provenance nor
content-derived identities for materialized refit rows." This kickoff builds
that missing generic capability in `dskit.pipeline` so a *future* enabled
`FinalRefit` (not this task's job to build) can eventually verify real
evidence instead of trusting a path. This is pure `dskit`-core engineering —
no domain decision, no §11 item, no market data, no real HPO/refit execution.

## Inventory to read first (don't rebuild what exists)

`dskit/pipeline/driver.py` already has real building blocks:
- `JsonArtifact`/`resolve_json_artifact` (`node.py`, `driver.py`): outputs
  already get a canonical **content-addressed path** — read exactly how, and
  what "content-addressed" covers (the JSON body only, or also its producing
  node's identity).
- `_write_node_records`: each node gets a per-run JSON record with `status`
  ("ok"/"error"/etc.), `seconds`, and a summary of its outputs — this is
  where "did this specific node complete" already lives, but check whether
  anything today lets a DIFFERENT run verify another run's node record
  wasn't tampered with or regenerated after the fact.
- `_write_carry`/`carry.json`: run-over-run state a next run can bind to via
  `$prev` — check what identity information (if any) ties a carried value
  back to the exact document/run that produced it.
- `resolved.json`/`result.json`/`config.json` in each run dir: what already
  binds a run's outputs to the exact document identity hash it ran against.

## The gap, precisely (per ADR-0116's own conditional-future-path text)

A future `FinalRefit` needs to, for each of ten per-lead HPO winner/ledger
manifests: (1) confirm the manifest is genuinely the `hpo_ledger` output of
a node that **completed** (not errored, not partial) in some prior run, (2)
confirm that same run's `carry` independently also names it, (3) confirm
that run's result metadata **binds the pinned final-HPO document identity**
(so a stale or wrong-document run can't be substituted), (4) reconstruct the
pinned 24-candidate inventory from that evidence and confirm it's complete
and matches the ruled one-standard-error selection (this part is Gate 2's
existing `dskit.pipeline.kinds_search` machinery — reuse it, don't
reimplement). Separately, the eventual refit's own identity must derive from
**source, cache, and materialized-row content** plus the training-window
start/lockbox boundary/embargo interval, so identical fitted bytes over
different data can never attest as the same release.

None of steps (1)-(3) and the content-derived identity piece exist today as
a generic driver API. Building them is this kickoff's job.

## Scope

Add to `dskit/pipeline/driver.py` (or a new sibling module if the ADR you
write justifies one — inventory first, extend a seam before inventing a new
file): a generic, domain-blind API that, given a run directory and a
document identity hash, can (a) attest whether that run completed
successfully end to end, (b) attest whether a specific node within it
completed successfully, (c) confirm the run's `resolved.json`/`config.json`
bind the exact document identity claimed, and (d) compute a content-derived
identity over a set of materialized row artifacts (never over their JSON
manifest paths alone). This must work for ANY pipeline document, not just
`run-final-hpo.json` — nothing here may name a lead, a horizon, LightGBM, or
`intraday_equities`.

Out of scope: actually wiring `FinalRefit` to use this (that's a future,
separately-authorized deliverable per ADR-0116's own text — do not lift its
refusal). No config authored here reads real market data.

**Process (mandatory, no exceptions):** TDD (failing test first). Write one
ADR extending ADR-0116 (new ADR number, next after the highest existing one)
with exact proposed class/function signatures, and mark it "proposed," not
self-accepted. One implementer + one fresh skeptic at a time — never
parallel, never skip a re-review after a fix. Follow root `CLAUDE.md`'s OOP
standards (subclass a hook, don't branch; one job per method; `__all__`/`_`
boundary; default-deny params; docstring conventions) and tiering rules
(`dskit/pipeline/*.py` is tier-1: stdlib-only, importable with nothing
installed — check `tests/pipeline/test_purity.py`). Update
`dskit/pipeline/{README.md,CLAUDE.md}` trees. Record actions through
`dskit.journal`; never hand-edit `docs/decisioning/path.csv`. Run the
focused suites this touches (`tests/pipeline/test_driver.py` or wherever the
existing `JsonArtifact`/carry tests live, plus `test_purity.py`) — do not run
the full repository suite. Refresh `docs/RE-ENTRY.md` on wrap.
