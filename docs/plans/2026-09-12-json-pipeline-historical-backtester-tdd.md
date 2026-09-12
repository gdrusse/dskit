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
Critical, Major, or Minor** findings. Only one Terra skeptic is active. Terra fixes
all severities, adds regression evidence, and reviews repeat through a clean exit.
Two fresh, sequential, independent Terra clean verdicts are mandatory; neither may
review its own correction. No full suite absent direct integration necessity.

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

**Reuse:** `dskit/pipeline/document.py:PipelineDocument`, existing `RunConfig`,
planner/identity hash, `driver.py:run_document`, staged runner, and registry.
**RED:** parser unknown-key/version/migration/default normalization, canonical plan
hash and digest-change, secret literal, normal wire, illegal captured wire/topology,
and unsupported-profile tests. **GREEN/test:** add one default-deny versioned
`execution_backtest` object and focused document/driver tests. It names only
policy/migration/defaults, content-digested refs, fidelity/profile, capture stages,
and opaque external references. `$node.output` stays same-run; `$captured_artifact`
is only producer -> seal -> external capture -> consumer, compiled by the existing
planner into staged runs. **Exit:** malformed/mutable/secret/same-DAG-capture plans
refuse before planning, data access, adapter load, or lifecycle action.

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
The sole generic trust API names are `ImmutableSnapshotProvider.describe`/
`open_member`, `ReleaseKeyring.verify`, `TrustedClock.now_ms`,
`TrustedRuntimeVerifier`, opaque `LaunchSession`, `CapturedJsonArtifact.value`/
`audit`, `CapturedLifecyclePort`, and per-node `CapturedBindings`. Application data
cannot construct a provider, keyring, clock, verifier, lifecycle authority, session,
capture, receipt, or resolver. `LaunchContext` is never another public API name in
implementation, configuration, or code; the external broker alone maps the accepted
ADR-0122 launch contract into the authoritative ADR-0123 `LaunchSession`.
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

The policy schema version and digest are required tape manifest/capture members and
receipt evidence; are pinned by normalized PipelineDocument/plan and
EnvironmentIdentity; and are repeated in frozen replay plans/transactions,
checkpoint/cache intents, ReplayResult, and report identity/provenance. Recovery
byte-compares those bindings before using a tape or resuming. **Exit:** ambient
time, rank-policy mismatch, ambiguity, cycles, impossible time, or missing
provenance rejects.

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

## F5 — captured ports, driver, and decider injection

**Reuse:** existing planner/driver/NodeContext/staged runner and the forecast
handoff's opaque `CapturedArtifactPort`. **RED:** legal grammar/nesting/stage
boundary, descriptor forgery, sorted authorization comparison, broker/receipt before
construction, runtime/consumer/release substitution, restricted-worker, and restart
binding tests. **GREEN/test:** the only legal descriptor is the complete value of a
declared node input:

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
audit only. Injected decider receives immutable captured ports, EnvironmentIdentity,
logical clock, RNG, and F3 contract. **Exit:** untrusted workers cannot access paths,
roots/keys/clock/storage/credentials; ambient, nested, ordinary, or noncaptured
decision input refuses.

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
runtime identities, fixture prediction, and pickle/joblib refusal. **GREEN/test:**
verified synthetic-fixture writer/loader using `VerifiedCapture` handles only;
`tests/pipeline_libs/test_lightgbm_release.py`. **Exit:** paths, object
deserializers, missing/extra/reordered/tampered members refuse.

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

**Reuse:** extend existing `production/records.py:AccountState`,
`production/accounting.py` (which folds, validates, and consumes that record type),
`production/ledger.py:ChainLedger`/`ServeRoot`, cashflow schedule/composer, and
report performance; no second ledger/returns simulator. **RED:** balance properties,
genesis/lineage, V1/V2 flows/timing/supersession, correction/bust, stale price/FX,
split/dividend, NAV/TWR/MWR/restart. **GREEN/test:** genesis, external-flow evidence,
canonical valuation, and double-entry `AccountState` extensions in the owning
records seam with corresponding accounting-fold behavior, plus focused ledger/
cashflow/report tests. Initial positivity is only `initial_flow`; V1, scheduled
deposits/withdrawals/corrections remain compatible. **Exit:** unbalanced,
stale/missing/incompatible/nonancestor evidence stops accounting.

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
`CommandProcessor`, `Checkpoint`, ReplayClock/Feed, and recovery. **RED:** live
byte/order equivalence, provisional isolation, handler/processor digest, preview/
cache, lifecycle crash/restore, and injected crashes after outbox drain, every
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
`ReplayRun` drives **three** lifecycle transactions under that same journal, lease,
and frozen-plan rule: `startup` for recovery/reconciliation mutations, one `tick`
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

**Reuse:** child replay only after R1--R4. **RED:** exact fixture/profile/tape order,
restart identity, and unsupported knob. **GREEN/test:** thin deterministic tape-
driven latency/slippage/fee/partial-fill/reject/cancel/session/halt/auction/
corporate-action adapters with focused child tests. **Exit:** ambient market/wall-
clock access or silent approximation refuses.

## I1 — layered synthetic normal-pipeline acceptance

Use one normal pipeline JSON/staged capture path:
`released model -> calibration/caps -> PIT forecasts -> MIO -> weekly contributions
-> orders/fills -> ChainLedger/AccountState -> NAV/TWR/MWR -> monitoring/holds ->
rolling retraining/release`.

RED/focused GREEN categories are config-negative/plan hash, port contract,
accounting property/metamorphic, PIT/leakage, event/effect/outbox/ACK, lifecycle,
and crash at every persisted boundary. Synthetic uninterrupted and crash/restart
schedules must have identical release/artifact identities, ledger head, checkpoint,
account state, outbox/cursors/events, metrics, and report. Any difference fails; no
real tape/HPO/refit/paper/live work occurs.

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
already has child synthetic replay tests plus ReplayClock, ReplayFeed, ServeLoop,
ServeRoot, ChainLedger, cashflow composer, Replay/CLI, recovery, and report seams.
R1--R5 extend those seams, never a child backtester or second accounting engine.
