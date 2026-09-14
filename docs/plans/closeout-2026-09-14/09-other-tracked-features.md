# Other tracked features: bounded decisions and implementation

Source: current [TODO](../../../TODO.md), especially the calibration/acquisition,
search expressiveness, pmquant findings, serving phase 2b/3 and explicit deferred
items. This is a coverage/disposition plan, not approval of proposed designs.
Re-inventory code and accepted ADRs first: stale unchecked prose is not proof
that a capability is absent. Plan 08 owns the docstring inventory.

Assign one named owner and one packet at a time. For every packet below, record
current paths/tests, actor/identity/effect matrix, approved ADR or exact unresolved
decision, focused RED and acceptance evidence. Significant design waits for ADR
approval. Model/environment policy follows the selected task, not a guessed
extension of the backtester policy. No market acquisition or training workload
is authorized by these directions.

## O1 — calibration/scoring libraries

Inventory beta calibration, CORP isotonic, cross-fit, Efron local FDR, Venn–Abers
and proper scoring rules in the existing library packs. Map shipped functionality
and the actual missing primitive to B0–B2 before choosing work; no duplicate
statistical implementation or simultaneous shared-file edits.

One missing primitive per packet, with a generic library boundary approved first.
Use independently calculated tiny examples and boundary/degenerate/invalid-input
cases, fit-versus-evaluate causality and restore identity if stateful.
Exit: that primitive and its public contract are verified; update only its TODO
coverage. The whole pack family stays open until all listed capabilities have
an accepted implementation or explicit owner disposition.

## O2 — acquire coverage, then guarded parallelism

Inventory `dskit/onboarding/acquire.py`, connector/store contracts and existing
coverage checks. First freeze what “coverage” means and when it may advance a
cursor or commit a WORM snapshot. Prove sequential missing/partial/duplicate and
complete coverage with offline connectors and zero-commit refusal.

Parallel acquisition is a separate Phase 0 packet after that contract: bounded
workers, per-source cursor ownership, commit serialization, failure/cancellation,
retry/idempotency and resource cleanup. Test deterministic barriers and injected
failures rather than timing sleeps. Exit requires equivalence to sequential
accepted content and no corrupt or falsely advanced cursor. Engine-level
multi-writer Registry/Lineage is not implicitly authorized by this seam.

## O3 — search seed ensembles and per-fold binding

Inventory `kinds_search.py`, document/driver and current searchable parameter
rules; distinguish A2's fixed ten-head recipe from missing generic expressiveness.
Approve binding/identity semantics before parser or driver changes.

Separate seed-ensemble declaration from per-fold parameter binding if either
needs a different identity rule. Tiny stub search tests must reconstruct each
trial's effective parameters and identity, forbid leakage across folds and
protect reserved/nonsearchable parameters. Exit: every legal binding has one
deterministic identity; ambiguous/substituted bindings refuse before training.

## O4 — optimizer declaration decision

Read the recorded torch SGD/default observation and existing optimizer params.
Decide explicitly whether affected declared-model kinds require an optimizer,
retain the current default, or adopt a compatible migration. Do not silently
replace SGD with AdamW or claim a learning-result improvement from config tests.

An approved change needs legacy-config/hash consequences documented, direct
validation tests and a stub asserting the optimizer actually receives the
declared parameters. No model-zoo or real training run is needed to prove routing.
Exit is the approved declaration contract plus compatibility evidence; a decision
to retain behavior is recorded as such, not reported as a runtime fix.

## O5 — pmquant fee and causal feature recipes

Read the child's research notes matching
`docs/research/skeptic-review-of-the-2026-09-04-rebuild-*.md` in its actual tree.
Two separate ADR-first packets change frozen recipes:

- Fee: compare the separable per-lot approximation with the venue formula on
  total quantity at VWAP; use hand-calculated multi-level fixtures exposing
  the recorded rate × n × Var(p) difference. Approve the mathematical/solver
  representation and identity migration before implementation.
- Features: map all four frozen panel tail features that use the eventual rung
  set to information available at each event time. Future-event perturbations
  must leave earlier features unchanged; test late arrivals and boundary times.

Reusable numerical/optimization mechanisms belong in the appropriate generic
pack, domain recipe in the child. No rewritten historical evidence or real
market run. Exit each packet only when its approved recipe, independent oracle,
causal/compatibility tests and affected frozen identities are reconciled.

## O6 — acquisition timestamp precision

Inventory `dskit/assets/base.py:utc_now`, ADR-0079 and consumers of acquired_at,
WORM and acq_id. Specify precision/canonical encoding/collision/legacy-reader
consequences before widening the current second-truncated stamp.

Use a frozen clock and full-precision observations within the commit second,
strictly future observations and exact boundaries. Prove accepted identity,
restamping, round trips and old data compatibility. Exit: the approved precision
contract is implemented with deterministic identities; no blanket relaxation
of future-observation validation.

## O7 — archive gap revisit and open-market discovery

Inventory the Polymarket connector's `pending_gap_hours`, cursor, series-slug
resolution and `closed` knob. Separate a revisit policy from token discovery/
upstream fallback when their identities differ. The old behavior is an explicit
trade-off; obtain the proposed ADR/knob authority before changing it.

Offline fixtures cover declared gap → later backfill, duplicate revisits,
not-yet-mirrored hours, interruption/restart, open/closed tokens and explicit
token precedence. Exit: declared coverage and cursor agree with retrieved
content and retries cannot silently omit or duplicate accepted observations.
No live mirror calls or real acquisition follow from this packet.

## O8 — serving_effect phase 2b

Inventory Eligibility/EventBank first, including inherited hooks, restore paths,
context assumptions and possible effects. Classify one family only with proof
from actual served execution; names or absence of obvious I/O are insufficient.

Retain Validate's split-context refusal and path-taking unsafe joblib/torch
restorers as forbidden unless a separately approved correction establishes a
safe contract. Writing table/report/search nodes remain unservable.
Focused tests must demonstrate valid derived-context behavior and forbid effects
for rejected states. Exit: justified classification and regressions for the
selected family; the entire audit is not closed by one newly eligible kind.

## O9 — serving phase 3

Four separate design/implementation tracks: exchange_calendars integration,
prometheus/OTel sinks, stream seam, onboarding migration onto resilience.
Read the approved production proposal and existing implementations before
declaring new APIs. Choose one actual consumer and one smallest useful adapter.

Require ADR/Phase 0 where identity, effects or public lifecycle changes.
Use offline calendar-boundary fixtures; stub sink failure/backpressure tests;
bounded stream cancellation/restart tests; or connector retry/budget parity
tests, respectively. Do not bundle all four or operate live services.
Exit each track with the approved public seam and affected compatibility proof;
phase 3 stays open until all intended tracks have explicit dispositions.

## O10 — explicit deferrals and stale prose

Keep engine-level multi-writer Registry/Lineage deferred until a real consumer
and approved ADR exist; store-seam concurrency is not blanket engine authority.
Keep the move-planted vid wrong-kind listing residual visible: dereference
refuses, and the proposed O(n) content-load repair has an unresolved cost/contract
decision. Neither becomes “fixed” through documentation alone.

Reconcile stale claims that no capital/TWR/MWR producer exists against C0 and
current production code, rather than building a second accounting engine.
Exit this documentation packet with accurate accepted/remaining/deferred
states and exact next decision owners, preserving genuinely open items.

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
