# Kickoff prompt — resume the `dskit.production` build

Paste the block below as the first message of the new session.

---

Resume the autonomous `dskit.production` build that was paused on 2026-09-05.
Work on branch `claude/dskit-production-build-3g17vw` (already pushed; it is
off `main` @ 03d797c). Never commit directly to `main`.

Read first, in this order, before doing anything:
1. `docs/RE-ENTRY.md` — the pause point.
2. `docs/new_package_proposals/build-notes/BUILD-LOG.md` — group status,
   every pinned API decision per group, the orchestrator rulings R1–R8, the
   plan gaps found, and the "TO RESUME" list. This is the memory of the
   previous session; every decision in it is binding unless the plan
   contradicts it, in which case stop and reconcile.
3. `docs/new_package_proposals/build-notes/BRIEF.md` — the shared brief every
   subagent reads. Copy it to your scratchpad and point every agent at it.
4. `docs/new_package_proposals/build-notes/SEAM-DESIGN.md` — the binding
   design note for the ADR-0091 pipeline seam (highest-risk item).
5. `docs/new_package_proposals/production.md` — the plan, in full. §4–§7 are
   the contracts, §8 the structure, §10 the TDD order, §5.16 the producer
   table, §11 the phases.
6. `CLAUDE.md` and `AGENTS.md` at the repo root — repo law.

The original mandate stands unchanged: build the whole package — phases 1, 2,
2b and 3 — to the plan, TDD in §10's module order (tests first and red, then
the smallest implementation to green, then a skeptic review of the pair),
with Stage 0 (specify phase 2 in the plan to phase-1 standard, check_plan.py
CLEAN, skeptic-reviewed) before any phase-2 code. Model assignment: Fable for
the first build of each module; Opus for all test authoring, specification,
first-round reviews and any rebuild after a review; Sonnet for later reviews.
At most 5 subagents at once. Invariants at every commit: the five purity
gates (`tests/{assets,journal,onboarding,pipeline,production}/test_purity.py`),
the 20 pinned sha256 literals under `tests/` unmoved, `tests/pipeline/
test_driver.py` and `test_kinds_search.py` passing untouched, ruff clean, the
skeleton pin updated with any skeleton change. Plan and code agree at every
commit; no plan change merges without a skeptic review. When the plan is
wrong: bookkeeping → fix and note; safety-critical → stop that module, write
it up, fix plan and code, Opus reviews that decision.

Current state: G1 foundations (`vocab/base/redact/records`, purity gate; 510
tests) and G4 `ledger.py` (154 tests) are green and committed. Red tests are
committed for document/release, clock/sessions/cadence, state,
resilience/metrics, ids/bundles/policy (+ `policy_golden.json`), monitors,
breaker/arming/coordination, guards, and the pipeline seam
(`tests/pipeline/test_{subgraph_runner,execution_policy,serving_effect,
serving_contract}.py`). Three agents were stopped mid-work: `state.py` (may be
absent or partial), the seam build (only `dskit/pipeline/policy.py` was
started — run `git diff main -- dskit/pipeline` and `git status` first), and
the first skeptic review of G1+G4 (not started). Environment: run
`pip install -e ".[dev]"` plus `pip install numpy scikit-learn joblib pyarrow
optuna pyomo highspy` to reproduce the fully green baseline; two tests fail
only because the sandbox runs as root (test_mlflow unwritable-parent,
test_runs unlistable-dir) — pre-existing, not ours.

Immediate order of work, keeping five slots busy:
1. F5 — Fable builds `dskit/production/state.py` to `tests/production/
   test_state.py` (rulings R1–R6: nested envelope, snapshot payload,
   applied-fill log, economic_seq, authority bodies, cancel_outcome kind).
2. F11 — Fable builds the ADR-0091 seam to the four red pipeline test files
   per SEAM-DESIGN.md and the "T11 … DONE" pins in the build log.
3. R1 — Opus skeptic review + fix of G1+G4 (including the duplicated money
   walk `records._json_value` vs `ledger._check_money` → one owner in
   `base.py`, and a public `fsync_dir` export in `dskit/onboarding/base.py`).
4. Fable builds against committed red tests: F2 document/release, F3
   clock/sessions/cadence, F6 resilience/metrics, F10 ids/bundles/policy,
   F15 monitors; after F5: F8 guards, F9 breaker/arming/coordination; after
   F11: the G12 feed/decider test author (+ `tests/production/conftest.py`
   synthetic run over a temp onboarding root).
5. Remaining Opus test authors: G7 control/accounting (apply ruling R8:
   accounting re-anchors every requirement at its own at_ms), G13
   reconcile/readiness, G14 verifier/executor, G16 alerts/health, G17 leg,
   G18 compose/loop, G19 `__main__` + e2e, G20 test_oop/test_producers; and
   S0 (phase-2 spec) as soon as a slot frees.
6. Opus first-round reviews of every built group, Sonnet re-reviews, fixes.
7. Batch the plan edits the build log lists (rulings + bookkeeping gaps),
   skeptic-review them, run `python docs/new_package_proposals/check_plan.py`.
8. Docs/examples/skeleton (§9.2, §9.3), phases 2, 2b, 3, then the wrap list
   from the original task (full suite, ruff, gates, hashes, ADR-0090/0091 →
   accepted, TODO/README/CLAUDE/AGENTS, merge only if fully green, summary).

Keep `docs/new_package_proposals/build-notes/BUILD-LOG.md` updated as the
durable memory (append each group's pins and every ruling); commit and push
often. Before any merge to `main`, delete `docs/new_package_proposals/
build-notes/` (branch-only working files) and refresh `docs/RE-ENTRY.md`.
Work autonomously; do not stop for approval on anything the plan decides.
