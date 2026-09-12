# JSON-pipeline historical backtester — TDD master plan

**Status:** approved architecture; synthetic implementation only. This document
is the single orchestration contract. It authorizes no data/market/lockbox read,
HPO, refit, replay, paper/live order, release use, or full backtest.

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
identity, RED test+command+observed failure, Sol implementation commit, GREEN
command/result, artifacts, corrections, and reviews. Each signed
`ReviewVerdict.v1` binds reviewer, commit/range, scope, severity findings, and
clean conclusion. Sol writes/runs focused RED then smallest GREEN. Only one Terra
skeptic is active. Terra fixes **all severities**, adds regression evidence, and
reviews repeat through zero Critical/Major. Two fresh independent sequential Terra
clean verdicts are required for lane/final exit; no reviewer approves its own fix.
No full suite absent direct integration necessity.

`SliceDependencyManifest.v1` is signed and binds the slice DAG, prerequisites,
commits/evidence, gates, environment/policy identities, allowed commands, and
capture outputs. Scheduler and launcher enforce it: omitted, reordered,
substituted, or unlisted slices refuse.

`HistoricalStudyManifest.v1` is signed only after G1--G7. It binds one frozen
dataset/tape/capture set, one normalized pipeline/release/environment/profile, and
exactly named control plus crash/restart executions. G7 may authorize A1--A4
historical training/HPO/selection/refit on that one frozen dataset only through a
simulator broker. It never authorizes paper/live or a second study.

## Dependency DAG and universal slice form

`P0 -> F1 -> F2 -> F3 -> F4 -> F5`; then `A1 -> A2 -> A3 -> A4`,
`B0 -> B1 -> B2 -> B3`, `E1`, and `C0` may proceed; `C1` needs `B3+E1+C0`;
`R1 -> R2 -> R3 -> R4 -> R5`; `I1` needs `A4+B3+E1+C1+R5`; `I2` needs I1 and
G1--G7. Every slice below lists reuse, RED, minimal GREEN/focused test category,
and fail-closed exit and inherits the P0 Sol-first/Terra lifecycle.

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

**Reuse:** ADR-0122 external OS-admin launcher, opaque `LaunchContext`, generic
capture driver seam, secure CLI refusal, and `node.py:TrainableNode` placement.
**RED:** forged/expired/revoked/replayed/wrong process-purpose-release-profile
permit and poisoned import/runtime/environment tests. **GREEN/test:** focused
secure-launch/driver tests for canonical signed release/runtime/permit/broker/hold/
review/dependency/study codecs. The external launcher owns image bytes, keys,
trusted clock, revocation, and isolated import view; DSKit has no fallback verifier.
`EnvironmentIdentity.v1` binds image/interpreter/stdlib/DSKit/dependency/native
digests, OS/kernel/architecture, locale, timezone plus tzdata digest/version,
canonicalization and logical-clock/RNG algorithm/version, and execution profile.
**Exit:** missing or mismatched external authority/environment refuses before import.

## F3 — EventEnvelope F1/F2 causal total order

**Reuse:** `production/feed.py:ReplayFeed`, `clock.py:ReplayClock`, and canonical
ledger records. **RED:** F1/F2 migration, DST/timezone/tzdata, source/exchange/
receive/availability causality, tie/sequence, duplicate ID, correction/bust chain,
canonical bytes/digest, shuffle, and restart tests. **GREEN/test:** focused envelope
codec/order tests. Envelopes bind source/event IDs, source sequence, source,
exchange, receive, and derived availability instants, timezone/tzdata, provenance,
schema/media/payload digest, and `corrects_event_id`/chain position/prior digest.
Order is availability, source rank, source sequence, correction position, payload
digest, event ID. **Exit:** ambient time, ambiguity, cycles, impossible time, or
missing provenance rejects; no alternate tape engine.

## F4 — immutable capture/WORM lifecycle

**Reuse:** ADR-0122 `PreparedCapture`, `VerifiedCapture`,
`CapturedMemberHandle`, `CapturedRelease`, and run/snapshot/data-provider seams.
**RED:** every transition, post-seal mutation, partial/duplicate member, WORM
capability, recovery, consumer substitution, symlink/hardlink/path leakage tests.
**GREEN/test:** focused generic capture tests for `produce -> seal -> external
verify/capture/sign -> publish -> authorized open`. The attestation binds producer
document/run/code/runtime/environment, terminal state, source manifest, ordered
relative media/byte/digest members, purpose/consumer/WORM identity. Handles expose
no path/reopen/dict/JSON interface. **Exit:** mutable, unsealed, uncaptured,
unverified, cross-plan, TOCTOU, or invalid-path bytes cannot be consumed.

## F5 — captured ports, driver, and decider injection

**Reuse:** existing planner/driver/NodeContext/staged runner and the forecast
handoff's opaque `CapturedArtifactPort`. **RED:** port grammar/stage boundary,
descriptor forgery, runtime/consumer/release substitution, restricted-worker, and
restart-binding tests. **GREEN/test:** focused driver tests make allowed
`$captured_artifact` a non-JSON port; the trusted driver verifies exact root,
snapshot, producer/run/document/node/output/member digest, release/runtime/time,
then injects one opaque capability. Carry/records retain audit descriptor only.
Injected decider receives immutable captured ports, EnvironmentIdentity, logical
clock, RNG, and F3 contract. **Exit:** untrusted workers cannot access paths,
roots/keys/clock/storage/credentials; ambient or noncaptured decision input refuses.

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

**Reuse:** `production/ledger.py:ChainLedger`/`ServeRoot`, cashflow schedule/
composer, and report performance; no second ledger/returns simulator. **RED:**
balance properties, genesis/lineage, V1/V2 flows/timing/supersession, correction/
bust, stale price/FX, split/dividend, NAV/TWR/MWR/restart. **GREEN/test:** genesis,
external-flow evidence, canonical valuation, double-entry `AccountState`, and
focused ledger/cashflow/report tests. Initial positivity is only `initial_flow`;
V1, scheduled deposits/withdrawals/corrections remain compatible. **Exit:**
unbalanced, stale/missing/incompatible/nonancestor evidence stops accounting.

### C1 — MIO consumes B3 + E1 + C0

**Reuse:** generic optimization ports and thin `EquityKellyMIO`; retain Gate 4
boundary. **RED:** cap/profile/account identity/freshness, deterministic ordering,
feasibility, held/mandatory exit, and no-effect. **GREEN/test:** trusted generic
ports produce only order-intent proposals; focused optimization/capital tests.
**Exit:** no executable intent without verified B3/E1/C0 and feasible solve.

## R1--R5 — replay/operations

### R1 — persisted effect intents/idempotency

**Reuse:** `ServeRoot`, ChainLedger cache/recovery, and broker/executor seams.
**RED:** crash state, competing lease, pre-head, duplicate, timeout, query-before-
resend, known/unknown response, and idempotency tests. **GREEN/test:** focused
journal tests add durable discoverable transaction journal binding series/genesis/
plan/release/environment/tick/lifecycle/pre-head/intent bytes-digests/idempotency/
cache-result/recovery. A fenced lease spans BEGIN through cache/result commit and
recovery; broker query request/response evidence precedes resend. **Exit:** unknown,
contradictory/unavailable broker state and blind resend refuse effects.

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
cache, lifecycle crash/restore. **GREEN/test:** extract `_tick_once`; live delegates
unchanged, replay selects transaction mode; focused loop tests add mode checkpoint
writer and exception-safe transaction-scoped recording/bundles/handlers/processor/
schedule/release/ports/clock. Post-FROZEN recovery publishes exact suffix then
atomically commits ledger/snapshot/checkpoint/cache/result/cursor before deferred
effects. **Exit:** no provisional leak or replay mutation outside transaction.

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

Only an exact G1--G7 `HistoricalStudyManifest` can run the normal pipeline against
captured envelopes/data, verified release, EnvironmentIdentity, profile, and
simulator broker for its named control/crash executions. Preserve I1 identity
equality. It authorizes neither paper/live, another dataset/execution, another real
release, nor a full/lockbox backtest.

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
