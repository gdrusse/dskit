# Intraday equities replay/operations infrastructure — round-5 handoff

Status: stopped at the five-round skeptic-review cutoff. This is an unmerged design
handoff based on `origin/main` at `4876ace`; it contains no implementation and
authorizes no market-data, replay, paper, or lockbox execution.

## Goal and ownership boundary

Build general DSKit production/replay infrastructure that executes a pinned released
decision graph, persists restartable accounting evidence, incorporates authorized
cash flows, emits post-commit events, supports authenticated monitor holds, and
rotates rolling releases. Keep intraday-equities code to release-bound decision,
capital-policy, event-mapping, signal-decay, and exchange-session adapters.

## Design closed by review

- Generic `ReplayRun` composes existing `ServeLoop`, `ReplayClock`,
  `ReplayFeed`, `ReleaseManifest`, `ChainLedger`, recovery, snapshots, and
  checkpoints. It does not create a parallel returns simulator.
- Child `EquityReplayDecider` executes the release-pinned graph at every replay
  tick. Paper/full runs reject tapes containing precomputed decisions, weights,
  orders, fills, or returns; a separate synthetic adapter is development-only.
- Stable replay identity and cursor-derived process/tick/leg/client/plan IDs support
  idempotency. Frozen per-tick journals prevent graph re-evaluation after a crash.
- Existing cashflow types remain authoritative. V2 adds policy/schedule digests and
  one initial flow, preserving every scheduled `flow_kind` and `supersedes`.
  Only the initial flow is one positive external unsuperseded deposit.
- Event outbox ordering is ledger barrier -> emit with stable event ID -> ACK ->
  atomic cursor. The cursor is ledger-ancestry-bound and stale ancestors rebuild.
- Monitor response v2 maps an authorized `hold` to the existing
  `guard_state`/fold/snapshot/expiry/`approve_hold` path. Unauthorized holds are
  record-only; halt remains breaker-only.
- A sibling `RollingReleaseRotationCalendar` permits overlapping trailing training
  windows while preserving the existing non-overlap calendar. Child session mapping
  owns exchange-day policy.
- `ReplayResult` is a ServeRoot-owned cache of identities derived entirely from
  the authoritative ledger; stale caches rebuild and ahead/non-ancestor caches fail.

## Transactional replay design

Add a generic `TickTransactionMode` seam to `dskit.production.loop`.
`ServeLoop._tick` dispatches either to the unchanged live `_tick_once` path or
to a transaction mode. Recovery gives the mode first opportunity to resolve an
incomplete replay transaction.

`ReplayTransactionalMode` owns:

```text
BEGIN
  -> PROVISIONAL
  -> FROZEN
  -> PUBLISHING
  -> RECORDS_BARRIERED
  -> SNAPSHOTTED
  -> CHECKPOINTED
  -> COMMITTED
```

It journals replay identity, cursor, replay-clock state, ID allocation, pre-head,
pre-checkpoint, canonical caller records, append instants, snapshot intent, and
deferred checkpoint. Before FROZEN, recovery restores pre-tick state and evaluates
the graph. At or after FROZEN, it never re-evaluates: the ledger must be an exact
prefix of the journal and only the missing suffix is published. Then it barriers,
persists the frozen snapshot and barriers again, writes the checkpoint, drains the
event outbox, runs deferred external after-tick effects, and advances the
ledger-derived cursor.

A `TransactionalLedger` implements the existing ledger contract against a
provisional state. A transaction-bound command processor stages receipts/control
side effects and moves inbox entries only after publication. Unstageable external
side-effect collaborators are refused in transactional replay.

## Remaining round-4 skeptic findings

Two Major integration gaps remain:

1. **Checkpoint dispatch.** On current main,
   `ServeLoop._write_checkpoint()` directly constructs `Checkpoint` and writes
   `recording.inbox.serve_root.checkpoint_cache`; replacing a hypothetical
   `recording.checkpoint` field does nothing. Add a mode-owned
   `_checkpoint/_write_checkpoint` dispatch or injectable checkpoint-writer
   protocol. Transactional mode must capture the exact intended checkpoint and
   prevent provisional heads from ever reaching the real cache. Live mode must
   preserve byte-for-byte current behavior.
2. **Handler rebinding.** `ServeLoop.__init__` constructs `self._handlers` with
   `handlers_for(..., self.bundles, ...)`; those handlers close over the original
   recording/ledger/state. Replacing `loop.recording` and `_processor` alone
   leaves observation/control writes on the live collaborators. Transaction mode
   must construct and install transaction-bound bundles, handlers, and processor as
   one scope, use them for all observation/control operations, and restore the
   originals only after commit or abort.

## Exact next patch shape

- Add `CheckpointWriter` (or equivalent narrow internal protocol) and make
  `ServeLoop._write_checkpoint` delegate to it. The default writer executes the
  current code unchanged; the replay writer stores a frozen intent and validates
  pre-head/terminal-head on commit.
- Add a scoped collaborator-binding helper that builds transaction bundles,
  `handlers_for`, and command processor from the same provisional recording.
  Installation and restoration must be exception-safe and cover `_recorded_bodies`,
  monitor observations, commands, approvals, and all control handlers.
- The frozen journal must bind the expected handler/processor/config class digests
  so a resume cannot install a different control path.
- Commit/abort and recovery must restore original collaborators exactly once.
  External after-tick effects occur only after the real ledger/checkpoint commit.

## Other required APIs

- Version existing `RecurringCashFlowSchedule` serialization and
  `ReplayCashFlowComposer` backward-compatibly. V2 evidence adds immutable
  schedule and policy digests; `_authorizes` validates them without changing
  deposits, withdrawals, adjustments, or correction supersession.
- Add ServeRoot-owned replay-transaction, event-cursor, and replay-result accessors.
- Persist `EventCursor.v1` with replay/emitter identities, last ACK sequence/hash,
  and last seen ledger head. Reject ahead/non-ancestor or identity mismatches.
- Normalize legacy monitor strings and v2 action objects; use an authorized,
  registered `MonitorHoldGuard` to emit existing guard-state records.
- Preserve the old rotation calendar; add a generic rolling calendar and thin
  exchange-session release plan with model/data/cache/policy availability pins.

## Focused TDD

- Inject crashes before/after every transaction state, frozen append, record
  barrier, snapshot barrier, checkpoint write, event ACK, and collaborator restore;
  compare canonical ledger envelopes, head, checkpoint, cursor, results, events,
  metrics, and report with uninterrupted replay.
- Assert provisional checkpoints never touch the real cache and live-mode
  checkpoint bytes/order remain unchanged.
- Assert every handler/control/observation path uses the provisional recording;
  commands and approvals cannot leak to the live state before commit.
- Reject changed handler/processor digest, pre-head, frozen record, append instant,
  tape/cadence/clock/ID allocation, and non-prefix recovery.
- Preserve v1 cashflow tests; cover V2 deposits, withdrawals, adjustments,
  supersession, initial-capital constraints, and digest substitution.
- Cover outbox ACK crash windows, monitor hold authorization/restart/expiry, result
  cache tamper/staleness, rolling windows, release identity, lockbox, and paper gates.

Use focused tests only; do not run a replay, HPO, refit, market data, or the full
test suite.

## Owner gates

- Accept the cross-layer ADR for transactional ServeLoop hooks, durable journals,
  outbox cursor, monitor-control mapping, and rolling calendar.
- Ratify capital timing/amount/settlement/correction policy.
- Ratify event retention/redaction/sink reliability and hold authority/scope/TTL.
- Ratify release cadence/session mapping/training/embargo windows and artifact
  availability.
- Separately authorize any frozen full-system lockbox run. Paper remains the only
  executable operating rung before that authorization.

## Prompt for the next agent

Implement this lane from branch `handoff/replay-ops-round5-20260911` in a new
isolated worktree, using Grok 4.6 with strict TDD and one Terra skeptic reviewer at
a time. Treat `origin/main` as correct. First close the two Remaining round-4
findings by adding a real checkpoint-writer dispatch and transaction-scoped
bundles/handlers/processor binding around the existing ServeLoop path. Then
implement the already-reviewed replay journal/recovery, compatible cashflow V2,
post-commit event outbox, monitor hold mapping, ledger-derived ReplayResult, and
rolling release calendar, followed by thin intraday-equities adapters/configs.
Preserve all ADR/owner gates, release identity checks, paper-only refusal, and
crash-equivalence tests. Use WSL2 and focused tests only; do not run replay, HPO,
refit, market data, or read the lockbox.
