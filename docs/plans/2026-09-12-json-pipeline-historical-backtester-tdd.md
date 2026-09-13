# JSON-pipeline historical backtester — TDD master plan

**Status:** approved architecture; synthetic implementation only. This document
is the single orchestration contract. It authorizes no data/market/lockbox read,
HPO, refit, replay, paper/live order, release use, or full backtest.

**Accepted-source pins:** this plan interprets ADR-0122 from
`fe59c8442aefed2c2b372ebe50b2279878d97422` (secure final-model release),
ADR-0123 from `4f2bfdbe66fbb2037d032a9c434864ae775d668a` (external forecast
trust boundary), and ADR-0124 from `77697edf823810e0832f02f73511638b681a9834`
(fenced transactional replay lifecycle). A plan/release/study identity includes
ADR-0125 from accepted commit `7a71f3933f6360dd36ddfe6e86d4a9c6a4fcb2a0`
and ADR-0126 from accepted commit `97900efd66bfed44cc403c567c04c4e383c05c24`.
The owner explicitly approved both on 2026-09-12; acceptance deltas passed Terra
with 0 Critical, 0 Major, and 0 Minor findings. These approvals approve neither
implementation nor any real execution. A plan/release/study identity includes all
five commit identities; a substituted ADR text or commit refuses.

## P0 — gates, fidelity, and immutable review workflow

DSKit remains generic; `children/intraday_equities` contains only label, cap, MIO,
execution-profile, market-rule, and thin adapter policy. The sole orchestrator is
normal versioned `PipelineDocument` JSON: no `BacktestSpec`, parallel DSL,
parser/planner, simulator, ledger, or returns engine.

Fidelity requires the attested release graph, captured inputs, causal tape order,
injected logical clock/RNG, captured execution profile, production order/effect
route, one ChainLedger accounting source, valuation/returns, and the same journal,
checkpoint, recovery, outbox, monitoring, hold, and rotation paths. Queue position,
hidden liquidity, venue routing, adverse selection, impact, NBBO reconstruction,
or any unimplemented microstructure is an explicit rejected profile option.

G0: ADR owner accepts boundaries. G1: security owner accepts launcher, codecs,
WORM, clock, revocation, and keys. G2: data owner accepts manifest/provenance/
licenses/sources. G3: model owner accepts split/HPO/refit/release evidence. G4:
risk owner accepts calibration/FDR/scenarios/caps/MIO. G5: execution/risk owner
accepts profile/simulator broker. G6: operations owner accepts journal/recovery/
outbox/holds/rotation. G7: research owner accepts one historical study only after
G1--G6. Each is signed, time-bounded, revocation-checked, identity-bound evidence.

Before any execution PipelineDocument exists, a distinct G1+G2
`DatasetCaptureAuthorization.v1` may authorize one external data-capture broker read
of named sources, scope, licenses, schema, and media into a WORM raw-event capture.
That producer may only `PRODUCE -> SEAL -> PUBLISH`: it yields immutable roots and
publication receipts plus broker-readable metadata, never a CAPTURED receipt or
worker consumption before a consumer document exists. It is security/data policy
evidence, not a study or execution permit; synthetic uses only a fixed-fixture
authority with `deployment_eligible=false`. No implementation slice performs a real
capture. The later study scopes bind the exact published-to-captured chain and never
imply a pre-authorization historical read or consumption.
Port-specific consumer capture/admission occurs only after each relevant consumer
PipelineDocument hash is frozen; G1/G2 owns raw read/capture/publish, while study
evidence later binds rather than authorizes that exact chain.

`ReviewEvidenceManifest.v1` is signed append-only evidence for slice/dependency
identity, Sol RED test+command+observed failure, Sol implementation commit, Sol
GREEN command/result, artifacts, corrections, and reviews. Each signed
`ReviewVerdict.v1` binds reviewer, commit/range, scope, severity findings, and
clean conclusion. `ReviewExit.v1` is a verifier result, not prose: it verifies the
complete dependency binding, exactly one active skeptic history, every Sol RED ->
Sol commit -> Sol GREEN sequence, every Terra-only correction, and **zero unresolved
Critical/Major** findings. Every Minor remains signed in its `ReviewVerdict`, bound
into the manifest, and visibly records a disposition, owner, and rationale
(`deferred`, `accepted`, or `corrected`); changing a finding's severity cannot hide
a Critical/Major. Only one Terra skeptic is active. Any correction, at any severity,
is Terra-authored with regression evidence. Two fresh, sequential, independent Terra
reviews are CLEAN only when they each find zero unresolved Critical/Major; neither
may review its own correction. No full suite absent direct integration necessity.

`SliceDependencyManifest.v1` is signed and binds the slice DAG, prerequisites,
commits/evidence, gates, environment/policy identities, allowed commands, and
capture outputs. Scheduler and launcher enforce it: omitted, reordered,
substituted, or unlisted slices refuse.

ADR-0125 controls study chronology. Before gates, the signed
`HistoricalStudyScopeIntent.v1` WORM singleton keyed by `study_id` binds only the
root PublishedInputSet.v2, closed tape-data/tape-manifest/A1--A4 ActionIntent DAG,
exactly control/crash-restart ReplayIntents, and environment/profile/component/
candidate/policy identity. It has no gate, PEA/CES/BVP/CAS, capture, authority, or
future output: policy intent is not execution authority.

`HistoricalStudyScopeAuthorization.v2` is the same WORM singleton. It atomically
consumes exactly that ScopeIntent, sorted G0--G7 evidence, and every root bootstrap
ActionIntent/PEA/CES/BVP/CAS tuple. It binds the complete one-use DAG, intent/digest
sets, identities, gates, and bootstrap tuples; root StageAdmission reuses its
bootstrap tuple and non-roots await declared PUBLISHED predecessors. Each action then
requires one StageAdmission and one consumed ActionExecutionAdmission binding that
tuple, one logical execution/run, recovery journal/fence, and one capture/session.

After stages publish, pre-final replay PIS/CES/PEA/BVP/CAS chains complete.
`HistoricalStudyManifest.v2` is the WORM singleton that binds the unique scope
authorization/intent, G0--G7 set, exhaustive sorted StageAdmissions and outputs
(including A4 release), and exactly the two pre-capture FinalReplayEntry.v1 tuples.
It contains no actual capture/receipt/set/session/post-final field. One post-final
FinalReplayAdmission per named replay consumes its sole run/session. No phase permits
retuning, another release/dataset/execution, paper/live, or a second study.

## Dependency DAG and universal slice form

`SliceDependencyManifest.v1.requires` is the machine-checkable DAG (all listed
nodes must have a verified `ReviewExit.v1`):

```json
{"F1":[],"F2":["F1"],"F4":["F2"],"F3":["F1","F2","F4"],
 "F5":["F1","F2","F4"],"A1":["F1","F2","F4","F5"],
 "A2":["A1"],"A3":["A2","F2","F4","F5"],"A4":["A3"],
 "B0":["F1","F2","F4","F5"],"B1":["B0"],"B2":["B1"],
 "B3":["B2"],"E1":["F1","F2","F3","F4"],"C0":["F1","F2","F4"],
 "C1":["B3","E1","C0"],"R1":["F2","F3","F4","F5","C0"],
 "R2":["R1","C0"],"R3":["R2"],"R4":["R3","C0"],
 "R5":["R4","E1","C1"],"I1":["F1","F2","F5","A4","C1","R5"],"I2":["I1"]}
```

Every slice below lists reuse, RED, minimal GREEN/focused test category, and
fail-closed exit and inherits the P0 Sol-first/Terra lifecycle.
The prose introduces F3's causal contract before F4's lifecycle section, but the
signed DAG is authoritative: scheduler/launcher execute F4 before F3.

## F1 — ordinary PipelineDocument JSON

**Reuse:** `dskit/pipeline/document.py:PipelineDocument`, `NodeSpec`, and
`StageSpec`; the node-map document/driver is current, while `stages.py:plan_stages`
is the predecessor stage-list seam. There is no `RunConfig` in this grammar.
Reuse the current planner/identity hash, `driver.py:run_document`, and registry.
`PipelineDocument.to_obj` already omits later-added `walkforward` and `foreach`
sections when absent; the execution block follows that omission precedent rather
than the older always-emitted nullable sections.
**RED:** parser unknown-key/version/migration/default normalization, canonical plan
hash and digest-change, secret literal, normal wire, illegal captured wire/topology,
unsupported-profile, direct ServeDocument CLI/config/construction/parsing, alternate
independently hashed document, field/default/captured-binding substitution, and
live/replay composition parity, pipeline-imports-production refusal, user-authored
`stages`, `$prev`/carry, and raw model-load/artifact-path refusal tests. Add the
pre-change representative `PipelineDocument` canonical-JSON-byte/hash golden to
`tests/pipeline/test_document.py`, then prove absent-block parse/round-trip/to_obj
preserves those bytes, hash, and `run_document` identity exactly; prove null/empty
is refused, absent differs from present, and an explicit migration changes identity.
Add ReplayRun two-capture descriptor grammar/identity tests for missing/extra/
duplicate/aliased/nested descriptors and preplanning broker-admission refusal.

The complete default-deny `execution_backtest` v1 grammar is:

```json
{"execution_backtest":{"schema_version":"dskit.execution-backtest/v1",
 "purpose":"synthetic|historical-simulator",
 "event_envelope_schema":"dskit.event-envelope/v2",
 "source_rank_policy_sha256":"<lowercase-sha256>",
 "execution_profile_sha256":"<lowercase-sha256>",
 "environment_identity_sha256":"<lowercase-sha256>"}}
```

Every shown member is required if the block exists; only standard `notes` is
optional and excluded from identity. The block itself is optional only for ordinary
non-production PipelineDocument use and cannot create a serve/replay runtime when
absent. `purpose` is `synthetic` or `historical-simulator` only; the latter requires
the verified study phase. All digests are exact lowercase SHA-256 and must match the
verified captured ports/runtime. There are no implicit v1 defaults. Older shapes must
go through an explicit versioned migration to canonical v1, then revalidate and
rehash; unknown keys, omitted required members, a changed normalized default, or a
noncanonical/mismatched digest refuses. The normal PipelineDocument canonical hash
includes this normalized block and all legal content references. `$node.output` stays
same-run; `$captured_artifact` is only producer -> seal -> publish -> later frozen
consumer document/port -> capture -> later consumer. Pre-document descriptors may
name only immutable PUBLISHED roots/receipts, never a placeholder or CAPTURED receipt.
Capture stages are derived only from those legal graph ports and lifecycle receipts,
then compiled by the existing planner into staged runs; there is no second
stage-language member to drift from the graph.
`source_rank_policy_sha256` specifically names the pre-document F3
SourceRosterCapture policy and must equal the data producer's resolved
`source_roster` descriptor policy at broker admission; it is never derived from or a
digest of a resulting tape.

**Replay tape inputs are ordinary document identity.** Every node resolved as the
generic `ReplayRun` consumer in an execution PipelineDocument declares exactly two
canonical top-level `NodeSpec.inputs` values: `tape_manifest` and `tape_data`. Each
value is the complete legal `$captured_artifact` descriptor; no third captured
descriptor, alias key, duplicate descriptor, nested/list/map descriptor, or missing
member is legal on that node. The two raw descriptors are normal normalized
PipelineDocument canonical JSON/hash/plan material, and their complete canonical
values are carried unchanged in `PlannedRuntimeContract`; they are not ambient
configuration, a later path lookup, or a `tape_digest` field in
`execution_backtest`/EnvironmentIdentity. The frozen descriptors must resolve to
PUBLISHED outer-manifest/data roots and publication receipts. Only then, in the
broker route, does descriptor parsing derive the two exact ConsumerCapturedPorts and
authorization entries, issue their port-bound CAPTURED receipts, and authorize the
distinct later consumer LaunchSession before ordinary planning or ServeRoot/root
creation. Only after this admission may the planner produce the staged program. The
later inner `tape_digest` is transaction identity, not a PipelineDocument field.

**Absence is canonical compatibility, never a null default.** When absent,
`execution_backtest` is absent from the normalized `PipelineDocument` object,
`to_obj` output, canonical JSON bytes, config hash, plan hash, and run identity—not
present as `null`, `{}`, an inferred version, or another default. `from_obj` must
therefore preserve every pre-existing representative legacy document's serialized
bytes and hash byte-for-byte; the focused golden regression freezes both before the
field is introduced. A present, fully normalized v1 block is emitted and wholly hash
material. Moving a legacy document into execution mode is an explicit new document/
migration that adds the block and intentionally has a new canonical and run identity;
no parser, planner, CLI, or launcher default may silently perform that migration.

If `execution_backtest` exists, user-authored `PipelineDocument.stages` is forbidden:
`PipelineDocument.from_obj`, the ordinary planner, every CLI entry point (including
the auto-stage `run_staged` route), and the secure launcher reject it before any
provider/filesystem open, node/adapter import, object construction, or
`plan_stages` call. Trusted producer -> seal -> capture -> consumer barriers are
derived solely from legal captured ports and verified lifecycle receipts. A document
without `execution_backtest` may retain legacy `stages`, but is ordinary non-runtime
pipeline work and cannot produce a serve/replay view; its explicit migration must
remove stages and emit the v1 execution block before bridge admission.

Execution mode recursively rejects every semantic prior-run/carry reference at
parse, plan, CLI, and launcher preflight: `$prev` strings/objects and all grammar-
defined variants in node inputs, sources, params, outputs, artifacts, defaults,
stages, or reference expressions. It opens no parent run, carry, provider, or path.
The only cross-run value is a later-distinct `$captured_artifact` whose completed
PRODUCED -> SEALED -> PUBLISHED -> CAPTURED -> CONSUMED receipts authorize its exact
consumer port. Focused tests instrument provider/filesystem opens and assert zero
opens for every rejected `$prev` variant.

Execution mode also rejects every explicit or default-resolved
`NodeSpec.mode:"load"`, `artifact` pin, and every declared raw artifact/read/path
field before node construction. It does **not** try to recognize path-shaped strings:
execution nodes/adapters instead have manifest-approved signed/digested registrations
and closed, versioned per-node parameter schemas. A parameter not in that exact
schema—including a renamed path, artifact, URI, reader, or loader knob—refuses before
construction. The only model load is private production-bridge `CapturedModelLoad`:
it is derived from the current node's `CapturedBindings` and a `VerifiedCapture`,
never JSON/NodeSpec, and invokes the native codec (for example
`load_text_bundle(VerifiedCapture)`) behind the `TrainableNode` load template. A raw
path, artifact, or reader never reaches a node constructor or load method. Focused
pre-construction and renamed-poison-parameter tests prove refusal without
file/provider access or model import.

**Public execution-entry denial.** `dskit.pipeline.driver.run_document` is a public
ordinary-pipeline entry only. It accepts an in-memory `PipelineDocument` and its
first operation is the in-memory `execution_backtest`-presence guard: any present
block raises before `plan_document`, environment/getenv loading, provider or
filesystem access, registry/adaptor import, node construction, or run-directory
work. Its legacy string/path overload is retired with an explicit migration
diagnostic before opening that path; ordinary callers first use the normal document
loader and then the same public guard, so ordinary documents with an absent block
continue unchanged. Public stage runners, walk-forward helpers, CLI `run`, and every
other driver API perform the same in-memory guard; an execution document supplied to
any public API is never a compatibility fallback. All public CLI `plan`, `run`,
`staged`, walk-forward, and node-map `validate` routes are reordered through one
side-effect-free `load_and_preflight_public_document`: it may perform the one
explicit user-config read needed to parse/normalize a PipelineDocument, then checks
`execution_backtest` **before** `_import_adapters`, registry access, planning,
node/output construction, env/getenv, or provider/run/output filesystem work. A
present block emits only the public refusal; it has no compatibility execution route.
An absent ordinary document may then import its adapters and retain current behavior.

**Public planning-entry denial.** Inventory and gate every public planning surface:
`dskit.pipeline.planner.plan`, exported `dskit.pipeline.plan`,
`dskit.pipeline.driver.plan_document`, stage planning, and exported
`dskit.pipeline.resolve_uses`. For an in-memory PipelineDocument each document-
accepting facade's literal first operation is the same execution-block guard; a
present block refuses before `resolve_uses`, registry lookup, `uses`/adapter/class
import, planner work, filesystem/provider/environment use, node construction, or
output. The current bare `resolve_uses(uses, registry)` cannot see a document, so it
is retired from the public execution-capable API: ordinary implementation uses the
private `_resolve_uses_ordinary` only after its facade's absent-block guard, while the
public replacement accepts an in-memory PipelineDocument first and performs that
guard before resolving. Old bare callers receive an explicit ordinary-compatibility
migration diagnostic, never execution behavior. **RED:** poison `uses`/adapter tests
in `tests/pipeline/test_planner.py`, `test_node.py`, `test_driver.py`, `test_stages.py`,
and `test_main.py` exercise every export with an execution document and assert zero
resolution/import/filesystem/environment/node/output activity.

The broker route alone calls a private, non-exported generic planning kernel, only
after the production bridge has verified the opaque LaunchSession/permit, canonical
document, captures/authorization set, release, and EnvironmentIdentity. That kernel
is not a public alias, import, CLI, kwargs/dict/claim/duck-type extension, and it
does not relax any ordinary public facade. **Exit:** no public plan/resolve surface
can resolve a `uses` value or produce a plan for an execution document.

The external broker is the sole execution route. From a broker-owned immutable
document/capture store it verifies the permit and constructs a private,
non-exported verified-driver invocation through the production-owned
`PipelineRuntimeBridge`; that entry accepts the bridge's nonconstructible runtime
and the opaque, broker-issued process/run/purpose-bound `LaunchSession` only. It has
no public import, CLI, path, overload, keyword, claim/dict, duck-type, or optional
session argument. The bridge validates the exact document/plan/release/capture/
environment bindings before it invokes the private entry. **RED:** invoke every
public driver/stage/walk-forward/CLI surface with an execution document plus absent,
forged, replayed, expired, wrong-process/run/purpose, dict, and duck-typed sessions;
poison/mocking assertions prove zero planning, getenv/env load, provider/filesystem
open, registry/import, node construction, or output in
`tests/pipeline/test_driver.py`, `test_stages.py`, `test_walkforward.py`, and
`test_main.py`. The CLI poison-adapter test permits exactly the explicit config read
to parse/normalize and proves zero adapter import, provider/run/output filesystem
open, planning, node construction, and output; ordinary no-block document tests
remain byte-identical. The private entry is production-owned and may call an internal
generic execution kernel only through the bridge; `dskit.pipeline` does not import or
name production types.
**Exit:** a public API, supplied path, optional kwarg, or non-opaque session cannot
reach execution.

Pipeline remains dependency-pure: it emits only generic immutable
`PlannedRuntimeContract` data containing the normalized PipelineDocument/plan hashes,
execution policy, declared capture-port identities, and audit digests; it never
imports, names, constructs, or validates `dskit.production` types. A production-owned
`PipelineRuntimeBridge` consumes that generic contract plus broker-verified captures
and derives non-user-constructible `PipelineServeRuntime`/ServeLoop bindings. Its
identity is a canonical derivative of the contract's PipelineDocument/plan hashes,
captured-binding audit digests, release, and EnvironmentIdentity; it has no JSON
loader, `to_obj`, independent config path, or hash authority. The production bridge
may reuse internal `production.document.ServeDocument` validators/read views only
behind its exact projection; Pipeline never imports that module and the projection
cannot parse, construct, select, substitute, or independently identify a process.

Direct `ServeDocument.load`/`from_obj`/constructor use, a ServeDocument positional
CLI/config, or a caller-supplied serve view refuses for secure, replay, historical,
and new live operation. Existing standalone live users receive an explicit,
non-executing migration/compatibility diagnostic: a production-side one-shot
converter may read the legacy document solely to emit a normalized PipelineDocument
or refuse an unmappable field; it never starts ServeLoop. Thus legacy use is neither
silently reinterpreted nor silently broken. **GREEN/test:** focused pipeline grammar,
migration/hash/purity tests and production bridge tests, including the committed
legacy canonical-byte/hash/run-identity golden, absent/present/round-trip matrix,
and explicit-migration identity change. **Exit:** malformed, mutable, secret,
same-DAG-capture, nullable/defaulted execution block, changed legacy canonical bytes/
hash/run identity, pipeline-to-production import, or independently authored/hashed
operational document refuses before planning, data, adapter load, or lifecycle action.

## F2 — external launcher, codecs, EnvironmentIdentity

**Reuse:** ADR-0122 external OS-admin launcher and secure CLI refusal, ADR-0123's
generic capture driver seam, and `node.py:TrainableNode` placement. The authoritative
pipeline trust capability is ADR-0123's opaque `LaunchSession`. ADR-0122's
`LaunchContext` is its source-document label for the same broker-issued external
launch capability, not a second DSKit API, constructor, verifier, or trust root.
**RED:** forged/expired/revoked/replayed/wrong process-purpose-release-profile
permit and poisoned import/runtime/environment tests, including public-driver/session
forgery and a malicious signed-test component attempting all ambient capabilities.
**GREEN/test:** focused secure-launch/driver tests for canonical signed release/
runtime/permit/broker/hold/review/dependency/study codecs. The external launcher
owns image bytes, keys, trusted clock, revocation, and isolated import view; DSKit
has no fallback verifier.
The sole public **authority** API names are `ImmutableSnapshotProvider.describe`/
`open_member`, `ReleaseKeyring.verify`, `TrustedClock.now_ms`,
`TrustedRuntimeVerifier`, and opaque `LaunchSession`; all authority instances come
from one external broker/trust root. `LaunchContext` is never another public API
name in implementation, configuration, or code; the broker alone maps ADR-0122's
launch contract into ADR-0123's authoritative `LaunchSession`.

ADR-0122's driver-owned `PreparedCapture`, `VerifiedCapture`,
`CapturedMemberHandle`, and `CapturedRelease` are not another authority API.
`PreparedCapture` is trusted-driver internal and is not exported. `VerifiedCapture`,
`CapturedMemberHandle`, `CapturedRelease`, `CapturedJsonArtifact.value`/`audit`,
`CapturedLifecyclePort`, and per-node `CapturedBindings` are exported only as opaque
nonconstructible **consumption values** for typed method signatures: a library may
consume `load_text_bundle(VerifiedCapture)` and a node may use only its own
`CapturedBindings.require(input)`. They have no public constructor, `from_obj`,
mint, copy, pickle/JSON, path, provider, credential, signing, or reopen API.
Application data cannot construct a provider, keyring, clock, verifier, lifecycle
authority, session, capture, receipt, resolver, or any such value. The single broker
issues every value through the verified LaunchSession and lifecycle receipts.

**Execution capability boundary.** The broker admits only an execution-safe,
signed-and-digested release manifest's registered node/adapter identities, exact code
and import-view digests, and closed versioned parameter schemas. The private verified
driver constructs only its restricted registry and gives such a component an
`ExecutionNodeContext`, never `NodeContext`: it exposes no `run_dir`, path, provider,
secrets/env, previous-run carry, tracker, release reader, ambient clock, or ambient
RNG. Its only data/capabilities are declared same-run inputs, its own mediated
`CapturedBindings`, injected logical-clock and opaque deterministic-RNG capabilities,
and a mediated metric/output/capture writer. Training and release output require
captured inputs and `PreparedCapture`/the writer; neither grants a raw output path.
F3's `CapturedEventDataset` is likewise a broker-derived opaque captured input with
only its single-pass stream capability, never a provider/path/network handle.
Direct `NodeContext`, arbitrary/custom node class, ordinary registry, or arbitrary
adapter is refused before construction.

The external OS sandbox binds that measured release: it denies ambient filesystem,
network, process/subprocess, environment, wall-clock, random source, and import
escalation; the measured import view contains only approved modules and all other
imports refuse. **RED:** a malicious registered test component attempts `ctx.run_dir`,
`os`/`open`/`pathlib`, environment lookup, network, subprocess, ambient time/random,
and an unapproved import, plus renamed path-like parameter names. Mocks prove denial
before external I/O and before any output/capture receipt in focused
`tests/production/test_verifier.py`, `test_sessions.py`, and a new
`test_execution_sandbox.py`. **Exit:** an unmeasured component, nonclosed schema,
ordinary context/registry, ambient capability, or raw input/output path refuses
before node construction.
`EnvironmentIdentity.v1` binds image/interpreter/stdlib/DSKit/dependency/native
digests, OS/kernel/architecture, locale, timezone plus tzdata digest/version,
canonicalization and logical-clock/RNG algorithm/version, and execution profile.
**Exit:** missing or mismatched external authority/environment refuses before import.

## F3 — EventEnvelope F1/F2 causal total order

**Reuse:** `production/feed.py:ReplayFeed`, `clock.py:ReplayClock`, and canonical
ledger records; extend their event-order contract, not a parallel tape engine.
F4's generic WORM lifecycle is intentionally a prerequisite of F3: raw source and
roster captures need it, while F4 itself has no F3 dependency. F3 first creates a
pre-document, immutable `SourceRosterCapture.v1`; this is an F3 substep, not a new
DAG node, so `F3 <- F1,F2,F4` remains acyclic. For an historical purpose the data
owner signs G2 authorization for it before any data access; synthetic uses the
equivalent nondeployment authority only. The roster is
PUBLISHED (not CAPTURED) before the execution PipelineDocument is frozen and contains
the complete authorized source universe, scope/time bounds, source-provenance
identities, policy version, and its `SourceRankPolicy.v1`:

```json
{"schema_version":"dskit.source-roster-capture/v1",
 "scope":{"availability_start_ms":0,"availability_end_ms":0,
          "source_provenance_sha256":"<sha256>"},
 "source_ids":["<canonical-id>"],
 "policy":{"schema_version":"dskit.source-rank-policy/v1","sources":[
   {"source_id":"<canonical-id>","rank":0}],"policy_sha256":"<sha256>"}}
```

`source_ids` is canonical sorted/unique; the trusted roster authority derives
`policy.sources` in that order with contiguous ranks `0..n-1`, and `policy_sha256`
omits itself. The scope is a complete provenance-attested source universe for its
declared availability interval—not merely sources observed in resulting events—so it
includes authorized zero-event sources. The execution block pins this already-
published policy digest, and the tape-data producer document freezes an exact
top-level `source_roster` `$captured_artifact` descriptor pointing at its immutable
root/publication receipt. Only after that consumer document hash exists may F5 derive
its ConsumerCapturedPort, issue the CAPTURED receipt, and allow the later consumer
LaunchSession to CONSUME it. Thus no policy is derived from the resulting tape.
Missing, late, unknown, duplicate, substituted, noncanonical, noncontiguous, or
rank-changed roster/policy data refuses.

The separate external data-capture broker next acts only under the exact signed,
time-bounded, revocation-checked G1+G2 `DatasetCaptureAuthorization.v1`. It may read
the authorization's named sources exactly once, only within the named scope/license/
schema/media policy, and writes a WORM `RawEventDatasetCapture.v1` with canonical
provenance, availability bounds, media and ordered-member digests, source-roster/
policy digest, and correction/bust metadata. Its inner default-deny manifest is:

```json
{"schema_version":"dskit.raw-event-dataset-capture/v1",
 "dataset_capture_authorization_sha256":"<sha256>",
 "scope":{"availability_start_ms":0,"availability_end_ms":0,
          "source_provenance_sha256":"<sha256>"},
 "source_roster_root_sha256":"<sha256>",
 "source_roster_publication_receipt_sha256":"<sha256>",
 "source_roster_policy_sha256":"<sha256>","license_digests":["<sha256>"],
 "event_schema":"dskit.raw-event/v1","media_type":"application/x-ndjson",
 "ordered_member_digests":["<sha256>"],
 "correction_bust_metadata_sha256":"<sha256>"}
```

F4 supplies the separate PUBLISHED receipt for that immutable root; CAPTURED is
deliberately absent until its exact consumer document/port exists. A synthetic
fixture authority emits the same canonical manifest from fixed fixture bytes with
`deployment_eligible=false`. This is the only source provider/filesystem/network
operation: no execution worker/provider receives it. Only after both source roster
and raw dataset artifacts are PUBLISHED may the execution PipelineDocument freeze.
Its generic `ReplayTapeDataCapture` producer declares exactly the two top-level
captured node inputs `raw_event_dataset` and `source_roster` (no alias/extra/nested
captured descriptors). F5 then derives both exact ports from the frozen document,
verifies the roots/publication receipts, issues port-bound CAPTURED receipts, and
only then admits the distinct consumer run; its authorization/receipt/member/purpose
equality and CapturedAuthorizationSet bind both.

**RED:** F1/F2 migration, DST/timezone/tzdata, source/exchange/receive/availability
causality, pre-document roster cycle/late-roster, roster descriptor/digest/source
swap, rank-policy substitution, unknown/duplicate/missing/swapped mappings, unknown
event source, rank change, zero-event source, same-availability tie ordering,
duplicate ID, correction/bust chain, canonical bytes/digest, shuffle, and restart-
terminal-identity tests. Raw-dataset RED covers missing/extra/swapped dataset or
roster, wrong scope/source/license/schema/receipt/member/digest/purpose, mutation,
early/late availability, raw provider/filesystem/network attempt, replay/reopen/seek,
and restart refusal before output. **GREEN/test:** focused roster/raw-dataset/
envelope/order tests use the exact deterministic synthetic fixture and derive the
rank only from the verified pre-document policy; add focused
`tests/production/test_bundles.py`, `test_feed.py`, and
`test_captured_event_dataset.py` coverage for the default-deny codec/one-pass stream.
Those tests also prove CAPTURED-before-document/hash, placeholder/self hash,
prefreeze port, publish omission, same run/session, descriptor mutation after freeze,
and wrong consumer-doc hash refuse while the exact publish->freeze->port->CAPTURED->
session->CONSUME order succeeds. Envelopes bind
source/event IDs, source sequence, source, exchange, receive, and derived
availability instants, timezone/tzdata, provenance, schema/media/payload digest,
the derived rank plus `source_rank_policy_sha256`, and
`corrects_event_id`/chain position/prior digest. Order is availability, derived
source rank, source sequence, correction position, payload digest, event ID.

`ReplayTape` currently has no digest or verified-capture identity. F3 therefore
adds an acyclic generic capture hierarchy, not a parallel tape engine. First,
`ReplayTapeDataCapture` is a parent F4 WORM capture: one data-producer run writes
the canonical F3-ordered envelope bytes while verifying every event source belongs
to its consumed pre-document roster and binds that exact policy digest; it does not
derive a new policy. The broker alone derives from the verified raw-dataset capture
an opaque `CapturedEventDataset` through a default-deny `RawEventDatasetCodec.v1`.
Its restricted `ExecutionNodeContext` exposes only a single-pass `iter_events()`
stream plus the mediated output writer: it has no path, provider, network, raw
member, reopen, seek, reset, replay, or ambient source access. The stream emits only
the canonical captured records and enforces the captured availability bounds and
correction/bust metadata. It then receives a PUBLISHED receipt for that **data**
root. Second, the distinct later `ReplayTapeManifestProducer` consumer document is
frozen against that published root/receipt; only then does the broker derive its
exact port, issue its CAPTURED receipt, and start its distinct consumer LaunchSession
to consume the parent. It derives the inner canonical `ReplayTapeManifest`
(`CapturedReplayTape.v1`) bytes:

```json
{"schema_version":"dskit.captured-replay-tape/v1",
 "event_envelope_schema":"dskit.event-envelope/v2",
 "data_capture_root":"<sha256>","data_captured_receipt":"<sha256>",
 "source_rank_policy_sha256":"<sha256>","envelope_count":0,
 "ordered_envelope_digests":["<sha256>"],
 "ordered_envelopes_sha256":"<sha256>","tape_digest":"<sha256>"}
```

`production/bundles.py` owns the default-deny v1 parser/canonical bytes and its
private verification seam. `ordered_envelope_digests` is the complete F3-sorted
sequence of canonical envelope-byte digests; `ordered_envelopes_sha256` hashes its
canonical array and `tape_digest` hashes the inner canonical object with only itself
omitted. `data_capture_root`/`data_captured_receipt` name the exact parent data root
and the manifest-producer's port-bound parent CAPTURED receipt whose WORM members
contain those bytes and policy. The manifest producer then PUBLISHES (not CAPTURES)
those inner manifest bytes in its own, separate `ReplayTapeManifestCapture` WORM
root/receipt. It never records its own root or receipt in the inner manifest: the
hierarchy is parent data publish -> later manifest consumer/capture -> manifest
publish -> third consumer, never same-root/self-receipt/cycle.

Third, `ReplayRun` is a distinct later consumer run. Its broker authorization has
the exact sorted pair of consumer ports for the outer manifest capture and the
referenced parent data capture; the replay process receives neither as a reopenable
handle. Its replay consumer document/hash must first freeze descriptors resolving to
the two PUBLISHED roots/receipts. The broker then derives the replay-specific ports,
issues two replay-specific CAPTURED receipts, and starts a new replay LaunchSession.
It verifies outer manifest `VerifiedCapture`/bytes; verifies the inner prior
manifest-producer data receipt against the parent root/members/policy; verifies the
separate replay-data CAPTURED receipt against the replay data port; and only then
issues one opaque composed tape capability that alone can become the runtime
`ReplayTape`. Same run/session consumption and raw legacy `ReplayTape` admission
refuse. The manifest capture's own root/receipt is bound by its consumer port, new
LaunchSession, study and plan evidence, and existing captured-binding/permit/binding-
digest plus R1 frozen manifest/input/artifact evidence—not by hashing it into its own
bytes and not by adding an ADR-0124 key.

The policy and inner `CapturedReplayTape.v1` digests are captured receipt evidence
resolved from the exact normal PipelineDocument `tape_manifest`/`tape_data`
descriptors and their broker authorization entries; neither `execution_backtest` nor
EnvironmentIdentity claims to pin a `tape_digest`. Its verified inner `tape_digest`
fills ADR-0126's post-admission `tape_digest` fields in the V2 header, intent,
and frozen plan. The descriptors and outer capture identity remain ordinary
document-plan/admission/frozen evidence; their exact V2 binding carries through replay
identity, cache/checkpoint preimages, ledger head, terminal projection, result, and
report provenance. **RED:**
raw-tape admission, unknown/extra/missing inner member,
canonical-byte/digest, same-root/self-receipt, swapped parent/manifest receipts,
missing hierarchy, same-run/session, outer-capture substitution, parent mutation/
reorder, policy/root/receipt substitution, reordered digest list, and restart-
identity tests fail first. Recovery byte-compares the existing ADR identities before
using a tape or resuming. Any direct transaction, result, cache, or checkpoint field
addition requires a separately accepted versioned ADR evolution, default-deny
migration, and focused old/new-schema refusal tests. **Exit:** ambient time,
unverified/raw tape, rank-policy/capture mismatch, self-reference/cycle, ambiguity,
impossible time, or missing provenance rejects.

## F4 — immutable capture/WORM lifecycle

**Reuse:** ADR-0122 `PreparedCapture`, `VerifiedCapture`,
`CapturedMemberHandle`, `CapturedRelease`, and run/snapshot/data-provider seams.
**RED:** every transition, post-seal mutation, partial/duplicate member, WORM
capability, recovery, consumer substitution, CAPTURED-before-consumer-document/hash,
placeholder/self hash, prefreeze port, publish omission, descriptor mutation after
freeze, wrong document hash, same-run/session, and symlink/hardlink/path leakage
tests.
**GREEN/test:** focused generic capture tests for the exact WORM CAS chain
`PRODUCED -> SEALED -> PUBLISHED -> CAPTURED -> CONSUMED`. A broker-owned external
`LifecycleAuthority` alone writes append-only signed receipts. Each receipt binds
producer/run/node/output/document identity, predecessor receipt digest, monotonic
sequence, immutable root/snapshot/member/publication identities, actor measured
runtime, nonce, trusted instant, signer/key, and the ConsumerCapturedPort when
applicable. `PUBLISHED` requires the immutable CAS root and a signed publication
receipt. A producer may stop there before any consumer document exists. Only a
frozen consumer document/hash with a complete descriptor for that PUBLISHED root can
derive a live exact `ConsumerCapturedPort`; only then can the broker issue CAPTURED
for the exact `{consumer_document_sha256,node,input,purpose}` and authorize a new,
distinct consumer LaunchSession. `CONSUMED` is that later consumer run using the
exact port. Handles expose no path/reopen/dict/JSON interface. **Exit:** CAPTURED
before a consumer document/hash, placeholder/self/frozen-mutation/wrong-doc port,
same-run, skipped, reordered, replayed, duplicate-nonce, non-WORM, unsealed,
uncaptured, unverified, cross-plan, TOCTOU, or invalid-path bytes cannot be consumed.

Within that single authority flow, only the trusted driver receives a private
`PreparedCapture` at PRODUCED/SEALED; that producer `LaunchSession` ends before the
later consumer run. After PUBLISHED and document freeze, the broker verifies the
linked publication receipt/descriptor, derives the exact ConsumerCapturedPort,
records CAPTURED, and issues a **new**, process- and run-bound consumer LaunchSession;
same-run or same-session replay refuses. That consumer session obtains a
`VerifiedCapture`, which yields verified `CapturedMemberHandle` values for a native loader such as
`load_text_bundle(VerifiedCapture)`. For a planned consumer port, the driver derives
one `CapturedLifecyclePort`/`CapturedJsonArtifact` from those verified retained
bytes, exposes it only through that node's `CapturedBindings`, and records CONSUMED.
`CapturedRelease` is the opaque post-capture result returned to the driver. These
are one-way consumption transitions, never a second trust root or reopen mechanism.
Focused lifecycle tests prove producer-session termination, new consumer-session
binding, exact publish->document/port->capture->consumer order, receipt/port linkage,
and same-run/session replay refusal.

## F5 — captured ports, driver, and decider injection

**Reuse:** existing planner/driver/staged-runner data contracts and the forecast
handoff's opaque `CapturedArtifactPort`; ordinary `NodeContext` remains only an
ordinary-pipeline contract, while execution uses F2's `ExecutionNodeContext`.
**RED:** legal grammar/nesting/stage boundary, descriptor forgery, sorted
authorization comparison, broker/receipt before construction, runtime/consumer/
release substitution, restricted-worker, closed per-node schema/manifest identity,
direct ordinary context/registry/custom-node use, and restart binding tests, plus
execution-mode `stages`, every `$prev`/carry spelling, and artifact/read/path/model-
load or renamed-param poison rejection before planner staging, provider/filesystem
open, broker construction, node import, or output writer creation. For ReplayRun,
RED additionally covers `tape_manifest`-only, `tape_data`-only, missing outer or
parent, extra/duplicate/aliased/nested descriptors, swapped outer/parent roots,
snapshots, producers, members, or receipts, inner digest/policy/count/order, purpose
and authorization-descriptor swaps, and equal ConsumerCapturedPorts with different
captures—all before planning/root creation. CapturedAuthorizationSet RED covers
unknown/extra/missing/duplicate/unsorted entry, self-digest, port/descriptor/resolved/
live-authorization substitution, CAPTURED-before-doc/hash, placeholder/self hash,
prefreeze port, publish omission, descriptor mutation after freeze, wrong doc hash,
same-run/session, happy exact publish->freeze->port->CAPTURED->session->CONSUME
order, and restart identity refusal.
**GREEN/test:** the only legal descriptor is the complete value of a declared node
input:

```json
{"$captured_artifact":{"root_ref":"release://forecast/v42","snapshot_version":"42","document_sha256":"<sha256>","node":"pit_bundle","output":"bundle","purpose":"synthetic"}}
```

The parser derives, not accepts, the exact
`ConsumerCapturedPort={consumer_document_sha256,consumer_node,consumer_input,purpose}`.
It rejects the descriptor in params, outputs, defaults, lists, maps, carry,
artifacts, or any nested/non-input location. The planner creates non-JSON
`CapturedArtifactPort` values. ADR-0125 defines the canonical default-deny
`CapturedAuthorizationSet.v2`: it binds study, consumed action/replay execution
authority, logical execution/run, subject, PEA/CES/BVP/planned-set/CAS chain, sorted
one-for-one port-authorizations and lifecycle-captured receipts, issuance basis, and
signature. Its entries are exactly the planned-entry, port-authorization, and
lifecycle-receipt digests sorted by planned entry; no V1/alternate resolved projection
exists.

The only accepted chronology is PUBLISHED input -> phase-correct PIS -> PEA ->
private BVP/PlannedCaptureSet (PCE) -> byte equality P(CES)==P(BVP) -> CAS ->
Scope/StageAdmission (or FinalReplayAdmission) -> consumed execution admission ->
per-PCE CapturedPortAuthorization and LifecycleCapturedReceipt -> signed
CapturedAuthorizationSet.v2 -> distinct LaunchSession -> CONSUME. PEA, CES, BVP,
PCE, and CAS are planning/admission evidence only: no member open, import, node
construction, session, run, or CAPTURED receipt occurs before private planning and
the consumed one-use authority. CAS is never a mutable scope or execution authority.

The broker resolves the frozen normal consumer document's complete descriptor to the
already PUBLISHED immutable root and flat PIS projection, privately plans the exact
typed capture set, and rejects a substituted, extra, missing, unordered, or unequal
PCE at every equality boundary. Only the complete V2 captured set binds the
nonserializable LaunchSession and runtime binding; its digest enters V2 header/frozen
plan, recovery, terminal projection/result, report, and study evidence. Focused F5/I1
RED tests cover plan-before-capture, authority/run/session linkage, equality sets,
publication/descriptor/receipt substitution, and restart identity.

For `ReplayTapeDataCapture`, exactly named `raw_event_dataset` and `source_roster`
top-level descriptors are both mandatory and are separate CapturedAuthorizationSet
entries. The broker verifies the raw-dataset authorization, immutable root, complete
member sequence, PUBLISHED receipts, scope/source/license/schema/media and
availability/provenance identities; from that frozen producer document it then
derives the two exact ports and records two port-specific CAPTURED receipts. It
verifies the raw policy/source universe/purpose/publication chain against the roster
entry and execution block, then admits the later producer session. Any missing/
extra/swapped descriptor, scope/source/license/schema/receipt/member/digest/purpose
mismatch, changed raw data, prefreeze port, or CAPTURED-before-document refuses
before the stream, planner, or root exists.

For ReplayRun's exactly named `tape_manifest`/`tape_data` descriptors, the manifest
entry's verified inner bytes must equal the data entry's resolved root, PUBLISHED
members, source-rank policy, envelope count, ordered digest list, and recomputed
inner `tape_digest`. Its inner `data_captured_receipt` must verify the **prior**
manifest-producer port for that parent root; the ReplayRun data entry instead has its
own later replay-specific CAPTURED receipt, derived from the frozen replay document.
Only then does F3 issue the composed tape capability. Before node/branch construction
the broker injects a fresh
non-enumerable `CapturedBindings` for only the current node; `require(input)` cannot
discover, copy, serialize, reopen, or forward another port. Carry/records retain
audit only; it is never a prior-run value. Injected decider receives immutable
captured ports, EnvironmentIdentity, and only F2's logical/opaque capabilities.
The bridge resolves every node/adapter only from the signed release manifest and
passes its exact closed schema's normalized values; it neither passes NodeSpec
params wholesale nor searches their strings for paths. **Exit:** untrusted workers
cannot access paths, roots/keys/clock/storage/credentials; an execution document
with user stages, a `$prev`/carry variant, unapproved component/schema, purpose/
authorization/descriptor mismatch, ordinary context/registry, ambient, nested,
raw-artifact, or noncaptured decision input refuses.

## A1--A4 — model release

### A1 — generic TrainableNode/splits

**Reuse:** `node.py:TrainableNode`, fitted-node conformance, and existing split/row
seams. **RED:** chronological split, row/source/cache/window/schema/order identity,
embargo/purge, immutable manifest, leakage, and execution-safe registered-template/
closed-param-schema/ExecutionNodeContext refusal tests. **GREEN/test:** smallest
generic split evidence/contract with focused pipeline node/split and final-model
tests; the verified execution template receives causal rows only through captured
bindings and emits evidence/model members only through the mediated capture writer.
**Exit:** no training absent verified causal rows/split, or through an ordinary
context, arbitrary node, or raw input/output path.

### A2 — ten-head HPO/refit and exact 1-SE

**Reuse:** child HPO ledger/evidence and ADR-0122 captured evidence adapters.
**RED:** exact `scan_h01`--`scan_h10`, shared inventory, labels, causal per-day
contributions, bootstrap code/seed/config, candidate order/simplicity, stale/missing
metrics, exact one-standard-error and refit rows. **GREEN/test:** generic verifier,
child policy only, and focused HPO/final-model tests. **Exit:** mixed capture,
approximate threshold, or nonreconstructable winner/rows refuses.

### A3 — native LightGBM codec only

**Reuse:** ADR-0122 generic tier-2 `pipeline.libs.lightgbm` seam. **RED:**
canonical `dskit.lightgbm-text-bundle/v1`, ordered text members, feature/category/
runtime identities, fixture prediction, pickle/joblib refusal, and execution-mode
JSON `mode:load`/artifact/read/path or renamed-parameter poison before a node
constructor, codec import, or provider/filesystem open. **GREEN/test:** verified
synthetic-fixture writer/loader accepts only the bridge-injected opaque
`CapturedModelLoad`/`VerifiedCapture` from current `CapturedBindings`, then calls
the native codec behind the manifest-approved `TrainableNode` execution template;
`tests/pipeline_libs/test_lightgbm_release.py`. No `NodeSpec`, artifact pin, raw
member/path, model value, ordinary context, or unapproved constructor reaches a
loader. **Exit:** paths, object deserializers, missing/extra/reordered/tampered
members, ordinary node/context, or any config-origin model load refuses.

### A4 — FinalRefit captured release

**Reuse:** A2, F4, `TrainableNode`, and native loader. **RED:** one fit/winner,
no load-time search/refit, no artifact/path output, exact rows, capture/release
binding, and launch-only construction. **GREEN/test:** `FinalRefit : TrainableNode`
publishes synthetic opaque `CapturedRelease` only after capture; focused final-model/
secure-driver tests. **Exit:** real refit/HPO and unverified/nonopaque release use
refuse until G3/G7 exactly as authorized.

## B0--B3 — forecast/calibration/capital

### B0 — equity labels/PIT

**Reuse:** generic port contracts and thin child label-state adapter; ordinary
pipelines may use `NodeContext`, while execution uses only F2's restricted context.
**RED:** observation/label availability, delayed labels, sessions, capture barriers,
leakage, and ordinary-context/path/provider refusal. **GREEN/test:** generic temporal
contract plus child adapter; focused PIT/label tests. **Exit:** unavailable/future/
cross-stage label or feature, or an ambient execution capability, refuses.

### B1 — causal calibration/confirmation

**Reuse:** forecast handoff pair evidence and external
`dskit.confirmation-proof/v1`. **RED:** disjoint fit/test, availability, fit-before-
test, late/mutable/replay, and policy/model/cap swaps. **GREEN/test:** generic fit/
apply/confirmation and signer recomputation with focused calibration tests. **Exit:**
no decision uses calibration without current signed causal confirmation.

### B2 — local-FDR/shared scenarios/oracle

**Reuse:** statistical node seams; child keeps HFDR inside MIO. **RED:** bounded
canonical FDR, pair provenance, common-instant entity/scenario/weight/missingness
matrix, seed/order/restart/generator version, and independent seeded oracle fixtures
with exact expected canonical values generated outside code under test. **GREEN/test:**
generic evidence and focused statistic/scenario oracle tests. **Exit:** absent
oracle, provenance, seed, or valid FDR refuses optimizer input.

### B3 — V3 caps

**Reuse:** delivered Gate 4 forecast-bundle/cap/MIO policy; do not rebuild it.
**RED:** timestamp/policy/model/calibration/scenario/bundle identity, empty-held
authorization, conflict/monotonicity/infeasibility/substitution. **GREEN/test:**
generic trusted cap port plus thin equity V3 adapter and focused forecast-bundle/
nodes-capital tests. **Exit:** stale, empty-unauthorized, conflicting, or untrusted
cap stops MIO.

## E1 — captured EquityExecutionProfile

**Reuse:** child session/fill policy and F3/F4. **RED:** canonical default-deny,
profile/release match, price/FX/borrow/session omissions. **GREEN/test:** captured
child profile and focused execution-profile/replay tests. It declares symbols,
calendars/half days/sessions/halts/auctions, splits/dividends, borrow/short/margin/
financing, tick/lot/bands, order/TIF/cancel-replace, fees, valuation/FX, and only
implemented realism. **Exit:** no order, valuation, or simulated fill without it.

## C0--C1 — one ledger/accounting and MIO

### C0 — ChainLedger and AccountState

**Reuse:** extend `production/records.py:AccountState`,
`production/state.py:SeriesState` as the sole ordered persistent ledger fold,
`production/ledger.py:ChainLedger`/`ServeRoot`, cashflow schedule/composer, and
report consumers; no second ledger/returns simulator. `production/accounting.py`
remains snapshot/validation/derived-accounting utility: it consumes snapshots but
does not own a persistent fold. **RED:** `tests/production/test_state.py` proves
that only `SeriesState.apply` folds ledger records into AccountState plus the
ledger-derived NAV/TWR/MWR state required for its snapshot; it also covers balance,
genesis/lineage, V1/V2 flows/timing/supersession, correction/bust, stale price/FX,
split/dividend, return identities, and restart. **GREEN/test:** make the smallest
SeriesState/records extensions and snapshot/report consumer updates, then run focused
`test_state.py` with directly affected ledger/cashflow/report tests. Initial
positivity is only `initial_flow`; V1, scheduled deposits/withdrawals/corrections
remain compatible. **Exit:** a second fold, an accounting.py persistent mutation,
or unbalanced/stale/missing/incompatible/nonancestor evidence refuses.

### C1 — MIO consumes B3 + E1 + C0

**Reuse:** generic optimization ports and thin `EquityKellyMIO`; retain Gate 4
boundary. **RED:** cap/profile/account identity/freshness, deterministic ordering,
feasibility, held/mandatory exit, and no-effect. **GREEN/test:** trusted generic
ports produce only order-intent proposals; focused optimization/capital tests.
**Exit:** no executable intent without verified B3/E1/C0 and feasible solve.

## R1--R5 — replay/operations

**Sole replay route:** generic `dskit.production.replay.ReplayRun` is the only
runnable replay entry point. Remove runnable `report.Replay` and route
`production.__main__.ReplayVerb` only through `ReplayRun`; it accepts only an opaque,
process-bound `VerifiedReplayExecutionPermit` returned by the external permit broker
from an opaque CLI permit handle. It never accepts claims, a key, a signature, a
path, or a constructible permit. The broker verifies image, release, document,
tape, replay, series/genesis, process, purpose, expiry, clock, and revocation before
root creation. `SyntheticReplayAuthority` exists only in production replay tests,
creates `deployment_eligible=false` synthetic permits, and is neither exported,
registered, CLI-accepted, nor usable for paper/live.

### R1 — persisted effect intents/idempotency

**Reuse:** `ServeRoot`, ChainLedger cache/recovery, and broker/executor seams.
**RED:** crash state, competing lease, pre-head, duplicate, timeout, query-before-
resend, known/unknown response, and idempotency tests. **GREEN/test:** focused
journal tests add durable discoverable transaction journal binding series/genesis/
plan/release/environment/tick/lifecycle/pre-head/intent bytes-digests/idempotency/
cache-result/recovery. A fenced lease spans BEGIN through cache/result commit and
recovery; broker query request/response evidence precedes resend. **Exit:** unknown,
contradictory/unavailable broker state and blind resend refuse effects.

ADR-0126 replaces every V1 replay transaction/result/ACK/cursor assumption.
`ReplayTransaction.v2` names only `FrozenReplayPlan.v2`; V1 journals are
inspect-only and cannot migrate, abandon, replace, hand off, continue, or share V2
study/replay/series/genesis/transaction identity or state. A V2 run starts fresh.
BootstrapIdentity, immutable header intent, identities, static result inputs, and
the selected committed predecessor/genesis sentinel precede separately signed
InitialReservationAuthority and InitialLeaseAcquisition. Only their exact bootstrap
reservation may read the committed head/prior cursor and construct EffectIntent,
QueryRequest, EffectSlotSet, pre-plan broker policy, verifier map, result intent,
projection spec, and the embedded canonical frozen plan. It atomically persists full
plan bytes in the FROZEN journal snapshot; only then can a plan-bound lease elevation
and broker authorization exist. The plan freezes pre-head, records, snapshot/cache,
inbox/outbox, effect slots, policy/verifier map, result intent/projection, and phase
order, never a terminal result. Before FROZEN recovery may restore/evaluate once; at
or after it validates the committed-head lineage and publishes only frozen suffixes.

### R2 — postings/outbox/ACK/cursor/result

**Reuse:** ChainLedger, ServeRoot, snapshot/cache/report. **RED:** exact V2
bootstrap reservation/lease elevation, frozen-plan bytes, per-series committed-head
lineage, ACK keyring/revocation/signature, sorted cursor, effect query-before-resend,
receipt substitution, projection/result/terminal-manifest, and every fsync/rename
cutpoint. **GREEN/test:** use frozen `EffectSlotSet.v2`, durable
EffectIntentActivation/Attempt/Resolution evidence, `EventAckEvidence.v2`,
`PostEffectReceiptSet.v2`, and `EventCursorSet.v2`; derive `ReplayResult.v2`
only after verified effects, then write the terminal projection/bundle/manifest and
external commit signature. **Exit:** no result/cursor is visible before the selected
signed committed head; ambiguous or nonancestor evidence refuses progression.

### R3 — loop dispatch/binding/checkpoint/recovery after R1/R2

**Reuse:** `production/loop.py:ServeLoop`, `compose.py:handlers_for`,
`CommandProcessor`, `Checkpoint`, ReplayClock/Feed, and recovery. ServeLoop,
`bundles_for`, `handlers_for`, and the command processor accept only the production
bridge's derived `PipelineServeRuntime`, never a user-authored `ServeDocument` or
pipeline-created production type; its bound generic contract, pipeline document/plan,
capture/release/environment identities are carried into live and replay binding
digests. **RED:** live
byte/order equivalence, provisional isolation, handler/processor digest, preview/
cache, lifecycle crash/restore, direct serve-view/config rejection, runtime field/
default/capture substitution, live/replay composition parity, and injected crashes
after outbox drain, every
deferred effect receipt, result write, cursor replacement, and pre/post COMMITTED.
Each crash schedule asserts ADR-0126 V2 ordering and either resumes the exact
frozen suffix or refuses; it never derives a result/cursor before effects. **GREEN/test:**
extract `_tick_once`; live delegates unchanged, replay selects transaction mode;
focused loop tests add mode checkpoint writer and exception-safe transaction-scoped
recording/bundles/handlers/processor/schedule/release/ports/clock. Post-FROZEN
recovery requires a durable externally authorized handoff successor, verifies its
plan-bound lease and committed-head predecessor, then follows: record barrier ->
snapshot barrier -> checkpoint/cache -> outbox -> durable activation/attempt ->
query-before-resend -> receipt/ACK -> cursor/result projection -> payload fsync and
terminal-manifest rename -> signed committed-head selection and atomic series-state
replace. A pre-effect abort is possible only with NO_EFFECT; after activation,
attempt, receipt, or external emission the transaction is non-abandonable and must
continue or fail closed.
`ReplayRun` and live mode compose through the same production-bridge runtime view;
only replay adds transaction mode. `ReplayRun` drives **three** lifecycle transactions under
that same journal, lease, and frozen-plan rule: `startup` for recovery/reconciliation
mutations, one `tick`
per F3 tape tick, and `shutdown` for stop/final checkpoint/result/teardown and
deferred effects. Every startup/tick/shutdown mutation or effect must be planable,
idempotent, frozen, and lease-held; one that cannot be staged refuses before it
runs. **Exit:** no provisional leak, reordered result/cursor, or replay mutation
outside a verified lifecycle transaction.

### R4 — holds/cashflow V2/returns/monitoring/rotation

**Reuse:** guards/control/compose/`approve_hold`, report returns, and existing
calendar. **RED:** signed hold expiry/revocation/release, record-only, V1/V2,
monitor order, return derivation, rotation overlap/gap/restart. **GREEN/test:**
focused tests add release-pinned signed hold capability, V2 policy/schedule digest,
ledger-derived results/returns, and sibling rolling calendar. **Exit:** missing
authority/evidence refuses actions, flows, reports, or rotation.

### R5 — child realism adapters

**Bypass inventory and migration:** current
`children/intraday_equities/intraday_equities/replay.py` imports
`ServeDocument`/`ServeLoop`; `EquityReplay` calls `ServeDocument.from_obj` and
constructs `ServeLoop`; `ReplayAdapter` delegates to it; registered
`DevelopmentReplay` reaches `ReplayAdapter`; and the child package currently exports
the latter two. All are production-capable direct-construction bypasses and must be
removed, disabled with a migration refusal, or migrated before any production path.
No child source may import/construct `ServeDocument`, `ServeLoop`, `ReplayRun`, a
permit, or a broker authority; `EquityReplay`, `ReplayAdapter`, and the old
`DevelopmentReplay` entrypoint cease to be package/registry production surfaces.

The replacement is ordinary PipelineDocument plus registered child decider,
execution-profile, fill-policy, and event-mapping adapters selected by the trusted
production-side `PipelineRuntimeBridge`; `ReplayRun` is the sole caller of the
production replay path. Existing development-replay documents and public adapter
calls receive an explicit non-executing migration diagnostic to the pipeline JSON or
refuse an unmappable policy. A synthetic fixture helper, if retained, lives only in
tests, is nonexported/unregistered, uses `SyntheticReplayAuthority` with
`deployment_eligible=false`, and cannot accept a CLI/config/production permit.

**RED:** child/import-graph/AST tests reject every listed direct import,
construction, package export, registry entry, CLI route, and legacy call; migration
diagnostic, production-bridge routing, exact fixture/profile/tape order, restart
identity, and unsupported-knob tests also fail first. **GREEN/test:** thin
deterministic tape-driven latency/slippage/fee/partial-fill/reject/cancel/session/
halt/auction/corporate-action adapters under the bridge with focused child and
production replay tests. **Exit:** any child bypass, ambient market/wall clock,
non-synthetic authority, or silent approximation refuses.

## I1 — layered synthetic normal-pipeline acceptance

Use one normal pipeline JSON/staged capture path:
`released model -> calibration/caps -> PIT forecasts -> MIO -> weekly contributions
-> orders/fills -> ChainLedger/AccountState -> NAV/TWR/MWR -> monitoring/holds ->
rolling retraining/release`.

RED/focused GREEN categories are config-negative/plan hash; execution-block
user-stage rejection at parser/planner/CLI/secure launcher (including auto-stage,
with an instrumented assertion that `plan_stages` was never called); every `$prev`/
carry spelling and mode/load/artifact/read/path or renamed-parameter poison rejection
before provider/filesystem open/import/construction; every public driver/stage/
walk-forward/CLI API's missing/forged/replayed/duck-typed session denial with mocked
zero plan/getenv/env/provider/fs/registry/import/construction/output; CLI plan/run
poison-adapter tests permit only the explicit config read needed to parse/normalize,
then assert zero adapter import, provider/run/output filesystem open, planning, node
construction, and output; public `planner.plan`, exported `pipeline.plan`,
`plan_document`, stage-plan, and `resolve_uses` poison-uses/adapter tests with zero
resolution/import/fs/env/node/output; direct-ServeDocument CLI/config/constructor
rejection; PipelineServeRuntime field/default/capture substitution; pipeline-to-
production import-graph refusal; production-bridge live/replay composition parity;
pre-document SourceRosterCapture cycle/late/descriptor/digest/source/rank/unknown-
source/zero-event/restart refusal; closed execution component-schema/manifest and
restricted-context tests; malicious-node `run_dir`, filesystem, environment, network,
subprocess, ambient time/random, and import-escalation denial before I/O/output;
G1/G2 DatasetCaptureAuthorization and deterministic fixed-fixture raw-event capture
tests, including raw dataset/roster descriptor, scope/source/license/schema/receipt/
member/digest/purpose, availability/correction, mutation, reopen/seek/replay/restart,
and provider/filesystem/network refusal before output; every capture edge's exact
PUBLISH -> frozen consumer document/hash -> derived port -> CAPTURED -> new session
-> CONSUME order, including CAPTURED-before-doc/hash, placeholder/self hash,
prefreeze port, publish omission, same run/session, post-freeze descriptor mutation,
wrong doc hash, and uninterrupted/restart equality; child direct-construction/export/
registry/CLI refusal; ReplayRun's exact top-level manifest/data descriptor pair and
complete CapturedAuthorizationSet entry/set-digest equality before planning/root
creation (including all capture/receipt/member/policy/count/order/purpose/auth-set
swaps); port contract; accounting property/metamorphic; PIT/leakage; event/effect/
outbox/ACK; lifecycle; and crash at every persisted boundary. This layer also runs the
committed pre-execution
PipelineDocument golden: no block must retain exact legacy canonical bytes/hash/run
identity after parse and round-trip, while a present block must change identity and
be fully hash material. Synthetic uninterrupted and crash/restart schedules must
have identical release/artifact identities, ledger head, checkpoint, account state,
outbox/cursors/events, metrics, and report. Any difference fails; no real tape/HPO/
refit/paper/live work occurs.

## I2 — exactly one gated historical simulator study

After the separate G1/G2 `DatasetCaptureAuthorization` has read/captured/published
the raw dataset and F5 has admitted each frozen consumer document into its exact
port-bound capture chain, `HistoricalStudyScopeAuthorization` first permits only its
fixed A1--A4 release creation; then its same-study WORM `HistoricalStudyManifest`
permits only the named control/crash executions against that captured dataset,
envelopes/data, actual verified release, EnvironmentIdentity, profile, and simulator
broker. The broker checks the appropriate phase before every node/root/action; the
study scope has no raw-provider/capture permission. Preserve I1 identity equality.
Neither phase authorizes paper/live, another dataset/execution, retuning, another
release, or a full/lockbox backtest. **RED/test:** focused manifest/broker tests
prove G0--G7 signer/key/revocation/time and source-commit substitution, pre-refit
release circularity, post-refit release substitution, retune/extra-execution, raw-
capture-before-authorization, and pre-data/node/root/action refusal. **GREEN:** only
the two-phase WORM verifier; no historical capture or execution is run by this
implementation slice.

## Existing reuse inventory — do not rebuild Gate 2/4/5

Pipeline already provides `PipelineDocument` (`document.py:1760`), `TrainableNode`
(`node.py:912`), public ordinary-only `run_document` (`driver.py:2315`), staged
runner, and split/fitted conformance. ADR-0122 fixes the approved launcher/capture/
native-LightGBM placement.
Gate 2 already has child HPO/final-model evidence, fail-closed refit config, and
`children/intraday_equities/tests/test_final_model.py`; it is A input, not capture
authority. Gate 4 already has `forecast_bundle.py`, capital nodes, and focused
forecast-bundle/nodes-capital tests; extend provenance ports, not policy. Gate 5
already has ReplayClock, ReplayFeed, ServeLoop, ServeRoot, ChainLedger, cashflow
composer, Replay/CLI, recovery, and report seams. Its child replay helpers are the
R5 bypass inventory to migrate/remove, not reusable production composition. R1--R5
extend the generic seams through the production bridge, never a child backtester or
second accounting engine.
