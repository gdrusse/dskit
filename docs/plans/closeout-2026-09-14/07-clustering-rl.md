# Clustering and RL: design, then six bounded slices

This is separate from the backtester DAG. Source:
`a450d2a39fb4e75ef240ce83a32dea7deee8489b` on
`codex/cluster-rl-framework-plan-20260913`, file
`docs/plans/2026-09-clustering-rl-framework.md`.
Read that exact plan and its §9 gates before work. This closeout guide organizes
the reset proposal; it does not approve it or supply a clean skeptic verdict.

## D0 — close the reset design

Reconcile proposed ADR-0122 with main's different ADR-0122 without rewriting
approval history. Freeze the source plan's matrix and enumerate changed
contracts. Keep the existing `FittedTransform`, sklearn and SB3 seams; no new
framework or child-side substitute.

Resolve owner approval of the reset ADR/plan, exact GLM/DeepSeek model mapping,
and any bounded dependency-install authority before RED, fits, training, tests
or environment changes where §9 requires them. Child environment, reward,
transition, order/fill design and changed configuration identities retain their
separate approval requirements. Planning approval is not an install or run grant.

Independent Phase 0 must distinguish operator-controlled local code from hostile
input: identity hashes detect drift; they do not sandbox arbitrary Python.
Review the whole design matrix with both final lenses before declaring this
document packet complete. Keep the source branch until its entire payload is
integrated or explicitly dispositioned.

## S1 — segmentation validation

After D0 gates, implement only the source plan's validation slice in the
existing seam. Prove row/feature/reserved-segment-key constraints and each
algorithm's conditional seed/parameter rules through direct classmethod calls.
Inherited `validate_params` is mode-blind: legal `fit_split` values come from
SPLIT_NAMES, while `validate_train_inputs` enforces train-only fitting.
Keep the class abstract and unregistered until required hooks exist; a test-only
subclass may expose validation without claiming production implementation.

## S2 — train state

Dependency: S1. Recheck train-only inside `fit()` before library invocation.
Use the smallest approved synthetic fixtures. Validate numeric features,
centers/labels, degeneracy and all source-defined state fields before committing
state. Test failures leave no accepted partial state. Preserve the source matrix's
algorithm-specific refusal and positive cases.

## S3 — assignment, restore and registration

Dependency: S2. Bind sidecar identity including train fit-split; assign every row
in original order, emit `segment_model_id` on its own port and numeric metrics.
Test round trips, malformed/substituted state and pre-output refusal.
Use seed-sensitive fixtures per supported algorithm: same-seed reproducibility
and actual different-seed divergence where required by the plan.
Only now register the concrete kind and finish its dedicated conformance tests.

## S4 — SB3 evaluation validation

After D0 gates; independent of S1–S3 unless the approved reset specifies otherwise.
Keep the incomplete evaluator abstract/unregistered. Test direct classmethod
validation and test-only subclasses. Enforce the exact episode budget
`n_episodes * max_episode_steps <= 1_000_000` in `validate_params`,
val/test-only evaluation and the source's complete parameter constraints.
No SB3 training is part of this slice.

## S5 — bounded manual evaluation

Dependency: S4. Use a stub policy and plain environment, not a training run.
Cover predict tuple unpacking; fresh seed/reset/step validation; termination
reason precedence and exclusive counts; bounded traces; provenance; JSON safety;
all eight source-defined atomic artifact failures after sidecar defaults and
before environment creation; and close-on-every-error behavior.

Register only when concrete. In that same candidate, update the legacy SB3
conformance table to explicitly cover its three old kinds and add the separate
one-kind evaluation suite. New stub tests must not importorskip the heavyweight
dependencies and silently pass without exercising evaluation.

## S6 — focused compatibility and feature exit

Dependencies: S3 and S5. Run the source's approved focused validation/conformance
commands with identities recorded. Do not run the whole existing
`tests/pipeline_libs/test_sb3.py`: it trains PPO. Select the registration
idempotence test only if that surface changed, plus the approved new stub suite.
Review complete clustering and evaluation matrices together for composition,
sidecars, import/registry compatibility and absence of unintended training.

Exit: all six slices and approvals have retained evidence, no unresolved
Critical/Major, and the exact registered surfaces match the approved reset.
A completed design or validation slice is not whole-feature closure.

## Finish: review, merge, push, purge, wrap

For each completed bounded packet, apply the [shared closeout procedure](README.md#mandatory-closeout).
Retain two clean independent lenses with zero unresolved in-scope Critical/Major,
focused evidence and visible minor dispositions. Update RE-ENTRY and the packet
record, merge the reviewed candidate with current main, push and verify remote
containment, then purge only the completed task branch using an expected-head
check; finish with wrap. A partial packet does not close its parent feature.
Keep an old source branch until its entire payload is contained in main or its
unmerged remainder has an explicit approved disposition. If blocked, preserve the
branch and evidence, do not merge or purge it, and wrap with the exact next gate.
