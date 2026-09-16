# DeepSeek prompt — F5a remainder, next session (2026-09-16)

You are the sole primary implementer continuing DSKIT F5a. Preserve every
dependency, review and owner gate below. Work in WSL2.

## Environment

- Task worktree: `/home/russell/wt/f5a-remainder-20260915`
- Main is `c6ad5a8` (pushed and verified). Worktree is clean.
- Interpreter: `/home/russell/dskit/.venv/bin/python`, run from the worktree with
  `PYTHONPATH=$PWD`.
- Never touch the dirty checkout at `/home/russell/dskit`.
- Preserve `origin/cursor/r5-f5a-private-plan-0f39` at `c489199…`. Never
  bulk-merge or delete it.
- Reviewer: `opencode-go/deepseek-v4-flash` is configured as the `flash-reviewer`
  subagent (`~/.config/opencode/agent/flash-reviewer.md`). **It only loads after
  an opencode restart.** Use `subagent_type: "flash-reviewer"` for Phase 0 and
  final lenses; if it reports "unknown agent type", restart opencode first, or
  fall back to `general` and record the actual reviewer model honestly.
- Fetch first, verify branch/worktree/remote.

## Read first

1. Root `AGENTS.md` / `CLAUDE.md`, then `docs/skills/implementation-workflow.md`,
   `test-drive-development.md`, `skeptic-review.md`, `wrap.md`.
2. `docs/RE-ENTRY.md` — top section is current.
3. ADR-0127 (accepted), ADR-0128 (accepted), ADR-0129 (proposed — the gate).
4. Evidence: `0164`/`0168`/`0176`/`0177` (gates + owner decisions),
   `0170`–`0173` (multiconsumer capture), `0165`–`0167` (codec).

## Where things stand

Landed and merged:
- `CapturedReplayTape.v1` codec (`dskit/production/bundles.py`).
- Multi-consumer capture (ADR-0128): one PUBLISHED root may be captured by
  multiple distinct consumer documents, keyed on `consumer_document_sha256`.
- `ReplayRun` grammar + tape-pair descriptors (P7 slice 1).

Pending (in order):
1. **Approve ADR-0129**, then build the composed-tape verification seam
   (`P7 slice 2b`). This closes P7.
2. **Packet 8** — durable consume-once authority (the F5A-R23 closure).
3. whole-F5a integration (two fresh lenses), then F3/F5b unblock.

## Step 1 — ADR-0129 (composed-tape seam)

Owner chose option A of gate `0176`. ADR-0129 is **proposed** in
`docs/architecture/decision-log.md`. Get owner approval (it may be granted
verbatim as "approve"), then:

- Add, after a committed `authorize_capture_set`, on the returned
  `CapturedAuthorizationRecord`:
  - a **one-way, single-read, session-bound** member-byte read, reusing
    `CapturedMemberHandle`'s discipline (NOT the full v1 `VerifiedCapture`/
    `CapturedBindings`/`CONSUMED` hierarchy — do not relax the v1 single-CAPTURED
    chain or one-time `CONSUMED`);
  - a **read-only, nonauthorizing** `lifecycle_captured_receipt_sha256` accessor
    keyed by `(stream, consumer_document_sha256)`.
- The accessor reaches the committed ledger only via the module-local
  record→ledger map, exposed as a public name (never a private-name cross-package
  reach; `tests/production/test_purity.py` and `tests/pipeline/test_purity.py`
  must stay green).
- Then build `ReplayTapeDataCapture`, `ReplayTapeManifestProducer`,
  `ReplayTapeManifestCapture`, and the opaque `ComposedReplayTape`, plus
  `compose_replay_tape(...)` verifying: outer manifest bytes parse/recompute,
  inner `data_capture_root` == data PUBLISH identity, inner `data_captured_receipt`
  == retained manifest-producer receipt, `source_rank_policy_sha256` == consumed
  policy pin, separate replay-data receipt == retained replay receipt. Negative
  families from plan §F3 (same-root/self-receipt, swapped receipts, missing
  hierarchy, outer-capture substitution, parent mutation/reorder, policy/root/
  receipt substitution, raw legacy ReplayTape admission, same-run/session).

**Process:** fresh clean Phase 0 skeptic (flash) → RED (synthetic fixtures, real
temp JSONL only where persistence is proven) → GREEN → two fresh sequential final
lenses (correctness/authority, then tests/integration) → integrate `--no-ff`,
push, verify, purge the contained branch, wrap RE-ENTRY. Sentinel:
`tests/production/test_capture_lifecycle.py::test_private_plan_precedes_capture`.

**Where the code lives:** the seam needs the private broker/ledger, so the
mint+verify lives in `dskit/pipeline/trust.py` (pipeline-side); production holds
only opaque results. Reconcile with the skeptic finding `0175` MAJ-4. The
`CapturedReplayTape` codec stays in `bundles.py`. Do not add a second ledger,
tape engine, or planner.

## Step 2 — Packet 8 (only after P7 fully closes)

The durable consume-once authority (F5A-R23). Frozen design: `0138`–`0144`
(migration approve; issuer = existing issued authority + owner ServeRoot;
durable `ChainLedger`/`JsonlLedger` reserve with `reserve_once -> (created, seq)`;
recovery states `OPENING/HEALTHY/UNCERTAIN/CLOSED/READONLY`; one ledger-owned
RLock; `SeriesState` sole fold; write-then-raise stays spent; query-exact-evidence,
never resend). This is a NEW packet with its own fresh Phase 0 before RED — do
not claim the `0138` design is implementation-ready.

## Scope limits (do not exceed)

No real data/replay, HPO/refit, full backtest, paper/live, deployment, or
environment changes. No unrelated branch cleanup. Synthetic fixtures only; real
temp on-disk JSONL only for persistence proof. One active subagent at a time;
no delegated implementation. Commit every evidence/RED/GREEN/verdict checkpoint;
batch corrections by defect family; after three failed correction cycles retain
a convergence checkpoint. `deployment_eligible` stays false until the owner says
otherwise.

## Next evidence number

0178.
