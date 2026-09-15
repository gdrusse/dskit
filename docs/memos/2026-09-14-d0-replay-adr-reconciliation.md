# D0 — Replay ADR/plan reconciliation (2026-09-14)

Reconciles the replay design delta between the pinned replay-ops source branch,
current `main`, and the controlling master manifest. Documentation only: no
replay code is implemented, no signed/runtime identity or protocol semantic is
changed, and R1–R3 closure is not claimed.

## Pins

- Replay source: `origin/codex/r5-replay-ops-20260911` @
  `97900efd66bfed44cc403c567c04c4e383c05c24`.
- Current main (at reconciliation): `origin/main` (post-Q2).
- Controlling manifest: `docs/plans/2026-09-12-json-pipeline-historical-backtester-tdd.md`
  (on main) — its R1–R5 section already contains V2 test targets and is not
  replaced wholesale.

## 1 — ADR-0126 identity collision

`ADR-0126` names two different accepted decisions:

- **Source branch** (replay-ops): `## ADR-0126 -- Deferred terminal projection
  for fenced replay` — accepted 2026-09-12, explicitly owner-approved; corrects
  source `ADR-0124`. It is the replay V2 protocol (FrozenReplayPlan.v2 etc.).
- **Main**: `## ADR-0126 -- F4 development-broker Major threat model is
  single-surface` — accepted 2026-09-13, owner reply "shrink"; F4 capture work,
  unrelated to replay.

The controlling manifest's R1–R3 text cites "ADR-0126 storage contract" and
"ADR-0126 replaces every V1 replay transaction…". Those citations are the
**replay** ADR (source branch), but on main the identifier `ADR-0126` resolves to
the F4 threat model. The cross-reference is therefore human-label
ambiguous/colliding on main, not broken: the manifest's pin to commit
`97900efd66bfed44cc403c567c04c4e383c05c24` keeps the replay decision
deterministically resolvable.

## 2 — Still-missing Replay V2 rules

Not captured in any accepted ADR on main; present in source `ADR-0126` (+ the
manifest R1–R5 targets). These are the V2 rules to land under a fresh identity:

- `FrozenReplayPlan.v2` — exact-key default-deny schema with no `result` member;
  `plan_sha256` omits itself; binds pre-head, records, snapshot/cache,
  inbox/outbox, effect slots, broker policy, verifier map, result intent,
  projection, and phase order.
- `ReplayResultIntent.v2`, `ResultProjectionSpec.v2`,
  `FrozenEmitterVerifierMap.v2` — deferred terminal projection; the final
  cursor/result derives only from verified post-effect evidence.
- `ReplayTransaction.v2` names only `FrozenReplayPlan.v2`; V1 journals are
  inspect-only and cannot migrate/share V2 identity.
- `BootstrapIdentity.v2`, `ExternalAuthorityEnvelope.v2`,
  `BootstrapScope.v2` / `PlanBoundScope.v2`, `ReplayLeaseHandoff.v2` — the
  bootstrap reservation → plan freeze → lease-elevation ordering.
- `ReplayTransactionHeaderIntent.v2` — immutable pre-plan header identity;
  series-head / series-state bindings, including `Reservation.v2` and state
  generation; and the `InitialReservationAuthority.v2` →
  `InitialLeaseAcquisition.v2` → `ReplayPlanLeaseElevation.v2` →
  `ReplayLeaseHandoff.v2` authority chain.
- `EffectIntent.v2`, `QueryRequest.v2`, `EffectSlotSet.v2`,
  `EffectIntentActivation.v2`, `EffectDispatchAttempt.v2`,
  `EffectResolutionEvidence.v2`, `EffectProgress.v2`,
  `PostEffectReceiptSet.v2`, and `PreEffectAbortAuthority.v2`; plus the
  post-plan `ReplayBrokerTransactionAuthorization.v2`.
- `ReplayTransactionEvent.v2` and `ReplayCommitEvent.v2`, including commit
  signer key/version/usage/algorithm; and the terminal bundle, manifest,
  witness, and projection pair.
- Storage contract: `AtomicJournalReplace` / `AtomicSeriesStateReplace` (rooted
  safe digest keys, lstat owner/mode/link checks, O_NOFOLLOW, O_EXCL
  same-directory temporaries, file fsync + rename/no-replace genesis +
  directory fsync).
- Sole replay route: generic `dskit.production.replay.ReplayRun` + opaque
  process-bound `VerifiedReplayExecutionPermit`; `report.Replay` removed as a
  runnable public API; `SyntheticReplayAuthority` test-only and nonexported.
- R5 child-bypass removal: `EquityReplay` / `ReplayAdapter` /
  `DevelopmentReplay` direct `ServeDocument`/`ServeLoop` construction is a
  production-capable bypass and must be removed/refused/migrated.

The complete V2 schema inventory lives in source `ADR-0126` and is not
exhaustively reproduced here.

## 3 — Already-integrated text

- `ADR-0120` (Phase 5 replay policies) — byte-identical in source and main.
- `ADR-0121` (Gate 4 forecast bundle) — identical in source and main except
  for a trailing `---` separator before the next heading; semantics are
  identical.
- The master manifest itself (R1–R5 V2 targets) is already on main.
- Main `ADR-0126` (F4 threat model) is a separate accepted decision and stays.

## 4 — Runtime/schema references (current main)

Present (V1-era / pre-replay seams, unchanged by this packet):

- `dskit/production/report.py:1604` `class Replay` (runnable public API to
  remove/route).
- `dskit/production/__main__.py:1522` `ReplayVerb`.
- `feed.py` `ReplayFeed`, `bundles.py` `ReplayTape`, `clock.py` `ReplayClock`,
  `compose.py` `ReplayCashFlowComposer`.
- Child bypass in `children/intraday_equities/intraday_equities/replay.py`:
  imports `ServeDocument`/`ServeLoop`; `EquityReplay`, `ReplayAdapter`,
  `DevelopmentReplay`; registry entry `intraday_equities-development-replay`.

Absent (V2 only; not implemented on main):

- `dskit/production/replay.py` `ReplayRun`; `ledger.py` `AtomicJournalReplace`;
  `state.py` `AtomicSeriesStateReplace`; `ServeLoop._tick_once` extraction;
  `FrozenReplayPlan.v2`, `ReplayTransaction.v2`, `EventCursorSet.v2`,
  `ReplayResultIntent.v2`, `SyntheticReplayAuthority`.

## 5 — Collision-free ADR identity

Main's highest accepted ADR is `0126`; `0127` and `0128` are unallocated across
main and all known source branches. Under the `next_id = max + 1` convention:

- Replay V2 decision (source `ADR-0126`, "Deferred terminal projection for
  fenced replay") → **`ADR-0127`**.
- Replay V1 transactional design (source `ADR-0124`, "Proposed transactional
  replay and operations seams") → **`ADR-0128`**, partially amended by
  **`ADR-0127`** (source `ADR-0126` already states it "Corrects ADR-0124").

Approval provenance is preserved, not rewritten: the accepted 2026-09-12 status
and owner approval travel with the renumbered ADR; this mapping records the
identity reallocation only. The identifier reallocation is **not** claimed to be
semantically harmless — it resolves a real collision and is documented so the
historical append-only evidence is untouched.

ADR-0124 is not wholly superseded: every remaining fence, immutable-plan,
identity, outbox, and owner-gate clause stays authoritative and must be
preserved. A required follow-on is to migrate the controlling manifest's
`ADR-0126` citations to the renumbered replay ADR and reconcile any plan-blob
or evidence identity that references `ADR-0126`; this memo does not edit the
manifest or any cited blob.

## Delegated decisions

- **ADR-identity allocation** (`0127` for the replay V2 decision, `0128` for the
  partially amended V1) is a delegated-owner decision within the "ADR-identity
  decisions" authority for these packets. Rationale: collision-free sequential
  `next_id`; preserves approval provenance; does not rewrite append-only
  evidence; does not modify any signed/runtime identity or protocol semantic.

- **No protocol-semantic decision is required by this packet.** The mapping only
  reallocates identifiers and inventories the delta; no semantic, signed/runtime,
  or schema identity is changed, so no owner protocol approval is invoked. Any
  later protocol change still requires the corresponding owner approval.

## Limits / non-goals

- No replay code implemented; no `ReplayRun`, no V2 schemas, no `_tick_once`
  extraction.
- No modification of signed/runtime identities, no replay run, no R1–R3 closure
  claimed.
- `origin/codex/r5-replay-ops-20260911` is preserved; D0 does not authorize
  deleting it.
- This is not R1's ReviewExit and is not permission to run replay.
