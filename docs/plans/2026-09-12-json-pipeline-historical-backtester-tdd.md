# JSON-pipeline historical backtester — TDD master plan

**Status:** approved architecture; synthetic implementation only. This document
is the single orchestration contract. It authorizes no data/market/lockbox read,
HPO, refit, replay, paper/live order, release use, or full backtest.

**Accepted-source pins:** this plan interprets ADR-0122 from
`fe59c8442aefed2c2b372ebe50b2279878d97422` (secure final-model release),
ADR-0123 from `4f2bfdbe66fbb2037d032a9c434864ae775d668a` (external forecast
trust boundary), and ADR-0124 from `77697edf823810e0832f02f73511638b681a9834`
(fenced transactional replay lifecycle). A plan/release/study identity includes
these three commit identities; a substituted ADR text or commit refuses.

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

`HistoricalStudyScopeAuthorization.v1` resolves the release circularity before any
historical read: it is a signed pre-refit authorization for one WORM `study_id`, one
frozen dataset/tape/capture set, normalized pipeline/environment/profile, fixed
candidate inventory/selection policy/seeds, and exactly A1--A4's permitted actions.
It binds G0--G7 scope-evidence digests, signer, key/version, revocation result,
issuance/validity, and approved identities; G3 is the pre-refit causal-method scope,
not a nonexistent release. The broker verifies every binding before every data open,
node construction, root creation, or A1--A4 action. It permits one captured release
creation only, no retune, no additional candidates/seeds, no control/crash run, and
no paper/live action.

After that capture, `HistoricalStudyManifest.v1` finalizes the same WORM `study_id`.
It binds the scope-authorization digest, actual release/capture identity, one frozen
dataset/tape/capture set, exact normalized pipeline/environment/profile, and exactly
named control plus crash/restart executions. It repeats, for **each** G0--G7, the
evidence digest, signer, key/version, revocation result, issuance/validity, and
approved identity, including the post-refit G3 outcome evidence. The broker verifies
it before every control/crash data, node, root, or simulator action. Thus the actual
release is bound after creation without authorizing retuning, another release, extra
executions, paper/live, or a second study.

## Dependency DAG and universal slice form

`SliceDependencyManifest.v1.requires` is the machine-checkable DAG (all listed
nodes must have a verified `ReviewExit.v1`):

```json
{"F1":[],"F2":["F1"],"F3":["F1","F2"],"F4":["F2","F3"],
 "F5":["F1","F2","F4"],"A1":["F1","F2","F4","F5"],
 "A2":["A1"],"A3":["A2","F2","F4"],"A4":["A3"],
 "B0":["F1","F2","F4","F5"],"B1":["B0"],"B2":["B1"],
 "B3":["B2"],"E1":["F1","F2","F3","F4"],"C0":["F1","F2","F4"],
 "C1":["B3","E1","C0"],"R1":["F2","F3","F4","F5","C0"],
 "R2":["R1","C0"],"R3":["R2"],"R4":["R3","C0"],
 "R5":["R4","E1","C1"],"I1":["A4","C1","R5"],"I2":["I1"]}
```

Every slice below lists reuse, RED, minimal GREEN/focused test category, and
fail-closed exit and inherits the P0 Sol-first/Terra lifecycle.

## F1 — ordinary PipelineDocument JSON

**Reuse:** `dskit/pipeline/document.py:PipelineDocument`, `NodeSpec`, and
`StageSpec`; the node-map document/driver is current, while `stages.py:plan_stages`
is the predecessor stage-list seam. There is no `RunConfig` in this grammar.
Reuse the current planner/identity hash, `driver.py:run_document`, and registry.
**RED:** parser unknown-key/version/migration/default normalization, canonical plan
hash and digest-change, secret literal, normal wire, illegal captured wire/topology,
unsupported-profile, direct ServeDocument CLI/config/construction/parsing, alternate
independently hashed document, field/default/captured-binding substitution, and
live/replay composition parity, pipeline-imports-production refusal, user-authored
`stages`, `$prev`/carry, and raw model-load/artifact-path refusal tests.

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
same-run; `$captured_artifact` is only producer -> seal -> external capture ->
consumer. Capture stages are derived only from those legal graph ports and their
completed lifecycle receipts, then compiled by the existing planner into staged runs;
there is no second stage-language member to drift from the graph.

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
`NodeSpec.mode:"load"`, `artifact` pin, raw artifact/read/path field, and
model-path-shaped value before node construction. The only model load is private
production-bridge `CapturedModelLoad`: it is derived from
the current node's `CapturedBindings` and a `VerifiedCapture`, never JSON/NodeSpec,
and invokes the native codec (for example `load_text_bundle(VerifiedCapture)`) behind
the `TrainableNode` load template. A path or artifact never reaches a node constructor
or load method. Focused pre-construction and poison-path tests prove refusal without
file/provider access or model import.

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
migration/hash/purity tests and production bridge tests. **Exit:** malformed,
mutable, secret, same-DAG-capture, pipeline-to-production import, or independently
authored/hashed operational document refuses before planning, data, adapter load, or
lifecycle action.

## F2 — external launcher, codecs, EnvironmentIdentity

**Reuse:** ADR-0122 external OS-admin launcher and secure CLI refusal, ADR-0123's
generic capture driver seam, and `node.py:TrainableNode` placement. The authoritative
pipeline trust capability is ADR-0123's opaque `LaunchSession`. ADR-0122's
`LaunchContext` is its source-document label for the same broker-issued external
launch capability, not a second DSKit API, constructor, verifier, or trust root.
**RED:** forged/expired/revoked/replayed/wrong process-purpose-release-profile
permit and poisoned import/runtime/environment tests. **GREEN/test:** focused
secure-launch/driver tests for canonical signed release/runtime/permit/broker/hold/
review/dependency/study codecs. The external launcher owns image bytes, keys,
trusted clock, revocation, and isolated import view; DSKit has no fallback verifier.
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
`EnvironmentIdentity.v1` binds image/interpreter/stdlib/DSKit/dependency/native
digests, OS/kernel/architecture, locale, timezone plus tzdata digest/version,
canonicalization and logical-clock/RNG algorithm/version, and execution profile.
**Exit:** missing or mismatched external authority/environment refuses before import.

## F3 — EventEnvelope F1/F2 causal total order

**Reuse:** `production/feed.py:ReplayFeed`, `clock.py:ReplayClock`, and canonical
ledger records; extend their event-order contract, not a parallel tape engine.
`SourceRankPolicy.v1` is a default-deny captured canonical object:

```json
{"schema_version":"dskit.source-rank-policy/v1","sources":[
  {"source_id":"<canonical-id>","rank":0}],"policy_sha256":"<sha256>"}
```

The trusted capture authority derives `sources` from the captured tape's complete
normalized source-identifier roster: entries are sorted by `source_id`, ranks are
the contiguous `0..n-1` entry positions, and `policy_sha256` omits itself. Thus the
mapping is unique and total for that tape; callers never supply a rank. Empty,
unknown, duplicate, missing, noncanonical, noncontiguous, or swapped source/rank
mappings refuse.

**RED:** F1/F2 migration, DST/timezone/tzdata, source/exchange/receive/availability
causality, rank-policy substitution, unknown/duplicate/missing/swapped mappings,
same-availability tie ordering, duplicate ID, correction/bust chain, canonical
bytes/digest, shuffle, and restart-terminal-identity tests. **GREEN/test:** focused
envelope/order tests derive the rank from the verified policy. Envelopes bind
source/event IDs, source sequence, source, exchange, receive, and derived
availability instants, timezone/tzdata, provenance, schema/media/payload digest,
the derived rank plus `source_rank_policy_sha256`, and
`corrects_event_id`/chain position/prior digest. Order is availability, derived
source rank, source sequence, correction position, payload digest, event ID.

`ReplayTape` currently has no digest or verified-capture identity. F3 therefore
adds one generic production-owned capture manifest, not a parallel tape engine:

```json
{"schema_version":"dskit.captured-replay-tape/v1",
 "event_envelope_schema":"dskit.event-envelope/v2",
 "capture_root_sha256":"<sha256>","captured_receipt_sha256":"<sha256>",
 "source_rank_policy_sha256":"<sha256>","envelope_count":0,
 "ordered_envelope_digests":["<sha256>"],
 "ordered_envelopes_sha256":"<sha256>","tape_digest":"<sha256>"}
```

`production/bundles.py` owns the default-deny v1 parser/canonical bytes and the
private verification seam. `ordered_envelope_digests` is the complete F3-sorted
sequence of canonical envelope-byte digests; `ordered_envelopes_sha256` hashes its
canonical array and `tape_digest` hashes the canonical object with only itself
omitted. The root/receipt must be the exact F4 CAPTURED WORM members containing
those bytes, and the policy digest must be the derived F3 policy for that same
roster. The trusted production bridge verifies this manifest through
`VerifiedCapture` and alone turns it into a runtime `ReplayTape`; a raw legacy
`ReplayTape` has no admission to secure, replay, historical, or new-live runtime.

The policy and `CapturedReplayTape.v1` digests are receipt evidence and are pinned
by `execution_backtest`/EnvironmentIdentity. Its verified `tape_digest` fills the
already accepted `tape_digest` keys of ADR-0124's `ReplayTransaction.v1` and
`FrozenReplayPlan.v1`; those identities in turn bind replay ID, frozen cache bytes,
checkpoint, ledger head, `ReplayResult`, and report provenance without changing
their exact default-deny schemas. **RED:** raw-tape admission, unknown/extra/missing
manifest member, canonical-byte/digest, policy/root/receipt substitution, reordered
digest list, and restart-identity tests fail first. Recovery byte-compares the
existing ADR identities before using a tape or resuming. Any direct transaction,
result, cache, or checkpoint field addition requires a separately accepted
versioned ADR evolution, default-deny migration, and focused old/new-schema refusal
tests. **Exit:** ambient time, unverified/raw tape, rank-policy/capture mismatch,
ambiguity, cycles, impossible time, or missing provenance rejects.

## F4 — immutable capture/WORM lifecycle

**Reuse:** ADR-0122 `PreparedCapture`, `VerifiedCapture`,
`CapturedMemberHandle`, `CapturedRelease`, and run/snapshot/data-provider seams.
**RED:** every transition, post-seal mutation, partial/duplicate member, WORM
capability, recovery, consumer substitution, symlink/hardlink/path leakage tests.
**GREEN/test:** focused generic capture tests for the exact WORM CAS chain
`PRODUCED -> SEALED -> PUBLISHED -> CAPTURED -> CONSUMED`. A broker-owned external
`LifecycleAuthority` alone writes append-only signed receipts. Each receipt binds
producer/run/node/output/document identity, predecessor receipt digest, monotonic
sequence, immutable root/snapshot/member/publication identities, actor measured
runtime, nonce, trusted instant, signer/key, and the ConsumerCapturedPort when
applicable. `PUBLISHED` requires the immutable CAS root and a signed publication
receipt; `CAPTURED` requires that exact receipt plus live port authorization;
`CONSUMED` is a distinct, later consumer run using the exact port. Handles expose no
path/reopen/dict/JSON interface. **Exit:** same-run, skipped, reordered, replayed,
duplicate-nonce, non-WORM, unsealed, uncaptured, unverified, cross-plan, TOCTOU, or
invalid-path bytes cannot be consumed.

Within that single authority flow, only the trusted driver receives a private
`PreparedCapture` at PRODUCED/SEALED; that producer `LaunchSession` ends before the
later consumer run. After PUBLISHED/CAPTURED, the broker issues a **new**, process-
and run-bound consumer `LaunchSession` only after verifying the linked receipts and
exact `ConsumerCapturedPort`; same-run or same-session replay refuses. That consumer
session obtains a `VerifiedCapture`, which yields verified `CapturedMemberHandle`
values for a native loader such as
`load_text_bundle(VerifiedCapture)`. For a planned consumer port, the driver derives
one `CapturedLifecyclePort`/`CapturedJsonArtifact` from those verified retained
bytes, exposes it only through that node's `CapturedBindings`, and records CONSUMED.
`CapturedRelease` is the opaque post-capture result returned to the driver. These
are one-way consumption transitions, never a second trust root or reopen mechanism.
Focused lifecycle tests prove producer-session termination, new consumer-session
binding, receipt/port linkage, and same-run/session replay refusal.

## F5 — captured ports, driver, and decider injection

**Reuse:** existing planner/driver/NodeContext/staged runner and the forecast
handoff's opaque `CapturedArtifactPort`. **RED:** legal grammar/nesting/stage
boundary, descriptor forgery, sorted authorization comparison, broker/receipt before
construction, runtime/consumer/release substitution, restricted-worker, and restart
binding tests, plus execution-mode `stages`, every `$prev`/carry spelling, and
artifact/read/path/model-load poison rejection before planner staging, provider/
filesystem open, broker construction, or node import. **GREEN/test:** the only legal
descriptor is the complete value of a declared node input:

```json
{"$captured_artifact":{"root_ref":"release://forecast/v42","snapshot_version":"42","document_sha256":"<sha256>","node":"pit_bundle","output":"bundle","purpose":"paper"}}
```

The parser derives, not accepts, the exact
`ConsumerCapturedPort={consumer_document_sha256,consumer_node,consumer_input,purpose}`.
It rejects the descriptor in params, outputs, defaults, lists, maps, carry,
artifacts, or any nested/non-input location. The planner creates non-JSON
`CapturedArtifactPort` values and compares the complete sorted planned
ConsumerCapturedPort set exactly to the complete sorted broker authorization set.
Before node/branch construction the broker verifies one live authorization and one
matching `PUBLISHED` then `CAPTURED` receipt for every port. It injects a fresh
non-enumerable `CapturedBindings` for only the current node; `require(input)` cannot
discover, copy, serialize, reopen, or forward another port. Carry/records retain
audit only; it is never a prior-run value. Injected decider receives immutable
captured ports, EnvironmentIdentity, logical clock, RNG, and F3 contract.
**Exit:** untrusted workers cannot access paths, roots/keys/clock/storage/
credentials; an execution document with user stages, a `$prev`/carry variant,
ambient, nested, ordinary, raw-artifact, or noncaptured decision input refuses.

## A1--A4 — model release

### A1 — generic TrainableNode/splits

**Reuse:** `node.py:TrainableNode`, fitted-node conformance, and existing split/row
seams. **RED:** chronological split, row/source/cache/window/schema/order identity,
embargo/purge, immutable manifest, and leakage tests. **GREEN/test:** smallest
generic split evidence/contract with focused pipeline node/split and final-model
tests. **Exit:** no training absent verified causal rows/split.

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
JSON `mode:load`/artifact/read/path poison before a node constructor, codec import,
or provider/filesystem open. **GREEN/test:** verified synthetic-fixture writer/
loader accepts only the bridge-injected opaque `CapturedModelLoad`/`VerifiedCapture`
from current `CapturedBindings`, then calls the native codec behind `TrainableNode`;
`tests/pipeline_libs/test_lightgbm_release.py`. No `NodeSpec`, artifact pin, raw
member/path, or model value reaches a constructor or loader. **Exit:** paths, object
deserializers, missing/extra/reordered/tampered members, or any config-origin model
load refuses.

### A4 — FinalRefit captured release

**Reuse:** A2, F4, `TrainableNode`, and native loader. **RED:** one fit/winner,
no load-time search/refit, no artifact/path output, exact rows, capture/release
binding, and launch-only construction. **GREEN/test:** `FinalRefit : TrainableNode`
publishes synthetic opaque `CapturedRelease` only after capture; focused final-model/
secure-driver tests. **Exit:** real refit/HPO and unverified/nonopaque release use
refuse until G3/G7 exactly as authorized.

## B0--B3 — forecast/calibration/capital

### B0 — equity labels/PIT

**Reuse:** generic NodeContext/ports and thin child label-state adapter. **RED:**
observation/label availability, delayed labels, sessions, capture barriers, leakage.
**GREEN/test:** generic temporal contract plus child adapter; focused PIT/label tests.
**Exit:** unavailable/future/cross-stage label or feature refuses.

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

`ReplayTransaction.v1` and its `FrozenReplayPlan.v1` are canonical default-deny
ServeRoot-owned objects keyed below the series root. The lease binds immutable
`series_id`, `genesis_sha256`, transaction identity, and a monotonic fencing token;
each ledger append, cache/checkpoint, inbox move, outbox/ACK, result, and deferred
effect receipt requires the current token. The frozen plan binds manifest/input/
artifact identities, pre-head, frozen canonical records and append instants, snapshot
and cache intent bytes/digests, control move order, outbox items, result, deferred
effects/idempotency keys, and phase order. It freezes effect intents, not future
graph decisions: before FROZEN recovery may restore pre-head and evaluate once; at
or after FROZEN it verifies the exact prefix and publishes only the missing frozen
suffix. Any changed identity, intent, order, append instant, or preimage refuses.

### R2 — postings/outbox/ACK/cursor/result

**Reuse:** ChainLedger, ServeRoot, snapshot/cache/report. **RED:** posting/outbox
atomicity, ACK signature/duplicate, sorted cursor, emit/restart, correction/bust,
self-excluding result digest/cache/terminal identity. **GREEN/test:** focused tests
route result/fill/correction/bust to canonical balanced postings, ledger-derived
outbox, signed idempotent `EventAckEvidence.v1`, sorted `EventCursorSet.v1`, frozen
snapshot preview/exact cache intents, and `ReplayResult` digest omitting
`result_sha256`. **Exit:** ambiguous ACK/cursor/result refuses progression.

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
Each crash schedule asserts the ADR-0124 order and either resumes the exact frozen
suffix or refuses; it never derives a result/cursor before effects. **GREEN/test:**
extract `_tick_once`; live delegates unchanged, replay selects transaction mode;
focused loop tests add mode checkpoint writer and exception-safe transaction-scoped
recording/bundles/handlers/processor/schedule/release/ports/clock. Post-FROZEN
recovery publishes the exact suffix under the current fence, then follows the exact
durable order: record barrier -> snapshot barrier -> checkpoint/cache -> outbox ->
effects -> result/cursor -> `COMMITTED`. Every append, cache replacement, outbox/
ACK, deferred-effect receipt, result write, and cursor replacement verifies the
current fence token and frozen preimage; cursor replacement is atomic and the final
commit is durable only after both result and cursor are valid ledger-derived values.
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
carry spelling and mode/load/artifact/read/path poison rejection before provider/
filesystem open/import/construction; direct-ServeDocument CLI/config/constructor
rejection; PipelineServeRuntime field/default/capture substitution; pipeline-to-
production import-graph refusal; production-bridge live/replay composition parity;
child direct-construction/export/registry/CLI refusal; port contract; accounting
property/metamorphic; PIT/leakage; event/effect/outbox/ACK; lifecycle; and crash at
every persisted boundary. Synthetic uninterrupted and crash/restart schedules must
have identical release/artifact identities, ledger head, checkpoint, account state,
outbox/cursors/events, metrics, and report. Any difference fails; no real tape/HPO/
refit/paper/live work occurs.

## I2 — exactly one gated historical simulator study

`HistoricalStudyScopeAuthorization` first permits only its fixed A1--A4 release
creation; then its same-study WORM `HistoricalStudyManifest` permits only the named
control/crash executions against captured envelopes/data, that actual verified
release, EnvironmentIdentity, profile, and simulator broker. The broker checks the
appropriate phase before every data/node/root/action. Preserve I1 identity equality.
Neither phase authorizes paper/live, another dataset/execution, retuning, another
release, or a full/lockbox backtest. **RED/test:** focused manifest/broker tests
prove G0--G7 signer/key/revocation/time and source-commit substitution, pre-refit
release circularity, post-refit release substitution, retune/extra-execution, and
pre-data/node/root/action refusal. **GREEN:** only the two-phase WORM verifier;
no historical execution is run by this implementation slice.

## Existing reuse inventory — do not rebuild Gate 2/4/5

Pipeline already provides `PipelineDocument` (`document.py:1760`), `TrainableNode`
(`node.py:912`), `run_document` (`driver.py:2315`), staged runner, and split/fitted
conformance. ADR-0122 fixes the approved launcher/capture/native-LightGBM placement.
Gate 2 already has child HPO/final-model evidence, fail-closed refit config, and
`children/intraday_equities/tests/test_final_model.py`; it is A input, not capture
authority. Gate 4 already has `forecast_bundle.py`, capital nodes, and focused
forecast-bundle/nodes-capital tests; extend provenance ports, not policy. Gate 5
already has ReplayClock, ReplayFeed, ServeLoop, ServeRoot, ChainLedger, cashflow
composer, Replay/CLI, recovery, and report seams. Its child replay helpers are the
R5 bypass inventory to migrate/remove, not reusable production composition. R1--R5
extend the generic seams through the production bridge, never a child backtester or
second accounting engine.
