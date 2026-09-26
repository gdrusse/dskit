# DeepSeek continuation prompt — DSKIT F5a remainder

> **2026-09-26 note:** `origin/cursor/r5-f5a-private-plan-0f39` is retired
> (deleted by owner decision A). Its F5a evidence 0001-0032 is on main at
> `docs/review-evidence/F5a/` (record 0045). Branch instructions below are
> history.

You are the sole primary implementer continuing DSKIT F5a. Finish all authorized
remaining work and preserve truthful dependency/closure gates. Read this entire
prompt before acting.

## Environment and current state

- Launch everything in WSL2. Task worktree:
  `/home/russell/wt/f5a-remainder-20260915`, current branch
  `codex/f5a-remainder-p7` (pushed, unmerged design/dependency checkpoints).
- Interpreter `/home/russell/dskit/.venv/bin/python`; task worktree is cwd and
  `PYTHONPATH=$PWD`. From Windows:
  `wsl.exe -e bash -lc 'cd /home/russell/wt/f5a-remainder-20260915 && PYTHONPATH=$PWD /home/russell/dskit/.venv/bin/python -m pytest ...'`.
- Fetch first. Main at handoff is `48ae2fb37a6f176fd52f8c983eaf9f1b37ddfad1`.
  Never touch dirty `/home/russell/dskit` checkout (stale wrap-tft-rf-runs).
- Preserve `origin/cursor/r5-f5a-private-plan-0f39` at
  `c48919944588a2b29ba047e4cb551050a1ff9868`. Never bulk-merge or delete it.
- Packets 5 and 6 are CLOSED and MERGED. Do not redo them. P6 exit0135,
  merge `ddcae6f`, wrap0136; two clean fresh Terra final lenses0133/0134;
  1594 focused tests passed. P6 branch purged. Runtime changes were only in
  pipeline/trust.py; accepted v1 receipt bytes unchanged.

## Read first, in order

1. `docs/skills/implementation-workflow.md`, `test-drive-development.md`,
   `skeptic-review.md`, `wrap.md`; root and nearest AGENTS/CLAUDE files.
2. `docs/RE-ENTRY.md` top, which records the newest commit/verdict and next number.
3. Evidence `0095` shared contract, `0096` deltas, `0099` bounded parser owner
   approval, `0100` ChainLedger owner decision, `0135`–`0143` and any later
   P8 design-review correction records named by RE-ENTRY.
4. ADR-0123, ADR-0125, actual ADR-0126 single-surface development threat model.
   Do not mistake the master plan's proposed replay-V2 ADR number for that ADR.
5. Read-only proposal:
   `git show origin/docs/f5a-deepseek-proposal-20260915:docs/memos/2026-09-15-deepseek-f5a-remainder-proposal.md`.
6. `docs/evidence/closeout/0138-f5a-p8-design-and-matrix.json`, its corrections
   `0140-f5a-p8-design-corrections-and-owner-gates.json`, final clarifications
   `0142-f5a-p8-final-design-clarifications.json`, and latest review/disposition. This is a conditional design proposal, NOT owner approval or RED.

## Decisions that must not be silently inferred

The latest user authorized completing Packet8 DESIGN ahead of Packet7, then
stopping/wrapping for this handoff. They did not approve its API migration or
implementation sequencing. Owner0100 selected ChainLedger, but also says P8 is
reached only after P5–7 close. Resolve the following with the owner before P8
implementation unless a later explicit decision is already recorded:

- Permit P8 implementation independently while P7 stays open, or keep ordering.
- Approve migration of capture-capable HistoricalStudyVerifier to require an
  authenticated owner composition capability and verified admission identity.
  Current successful v1 authority-only constructor plus six placeholder dicts
  supplies neither. Keeping a successful non-durable fallback leaves R23 open.

- Specify/approve the trusted production bootstrap issuer and its authenticated
  binding of admission, capture authority, and stable durable namespace. A public
  constructor taking an arbitrary ledger permits a fresh-namespace bypass;
  pins or another public wrapper/factory do not solve it. See0140.

Recommended choice is explicit migration plus independent P8 implementation,
with P7 and whole-F5a still open. Do not present this recommendation as approval.
Finish the exact identity-accessor contract and obtain fresh clean Phase0 once
those decisions are resolved; incorporate every retained reviewer finding.

## Packet7 — precise dependency, no fabricated implementation

Evidence0137: no ReplayRun/ReplayTapeDataCapture/ReplayTapeManifestProducer/
ReplayTapeManifestCapture implementation or verified captured-tape seam exists.
Legacy ReplayTape ABC is not that seam. Generic P5 grammar and P6 identity checks
already exist; do not manufacture RED or call an empty slice packet closure.

Replay-ops/F3 must provide resolved class-declared ReplayRun identity and verified
outer/data roots, prior manifest-producer parent CAPTURED receipt, members,
policy/count/order evidence. Then enforce exactly top-level tape_manifest and
tape_data and missing/extra/duplicate/alias/nested/swapped/equal-port-different-
capture refusals before planning/root creation. Never infer ReplayRun from a
uses string, impose the pair on every P4 replay admission, create a tape engine,
or implement unrelated F3 work without its authority. Record unavailable
interfaces; do not claim P7/F3/F5b closure while missing.

## Packet8 — recommended technical direction

Reuse ONE production ChainLedger/JsonlLedger writer and authenticated owner-bound
stable ServeRoot. Bare caller-provided ledger injection was REJECTED in0139/0140. Remove the public _spend kwdefault as authority. No temp/default path,
per-request store, second ledger engine, pipeline import of production, or new
P4 receipt writer. Freeze a verified admission reference from existing authority
closure; structural preflight and consumed=True are not grants. Stable key is
admission-derived, not process/object/nonce-derived; changed binding under the
same key conflicts rather than remints permission.

Use one ledger-owned RLock and existing append/index/barrier machinery for atomic
reserve-once returning exact (created, seq). Follow0140 health states, snapshot
cadence, body-validator owner, recovery audit and per-phase fault matrix.
0142 pins nonce validation and requires fresh complete SeriesState replay after
an uncertain fold; rebuilding the ledger index alone cannot restore the fold. Duplicate append's old seq is NEVER a new permission. Verify
recovered chain, namespace, payload consistency and duplicate IDs. Quarantine
uncertain writes; recover through the existing store before another claim.
Reservation+barrier precedes one delegate call. Write-then-raise and crash after
reservation stay spent; query outcome, never resend. Promise one durable
admission spend/at most one attempt, not exactly one broker effect across the
crash gap. Preserve deployment_eligible=false and direct accepted F4/P4 scope.

Synthetic RED must cover concurrency, clone/replay, real subprocess restart and
contention, short/partial/full write exceptions, fsync/fold failures, recovery,
unknown outcome, identity/store/default substitution, and one-consumption.
Use real temp on-disk JSONL plus process/pipe coordination, not fake memory proof.
Preserve sole SeriesState fold and closed vocab if adding admission_use.

## Workflow and delivery

DeepSeek is primary implementer. Owner-authorized reviewer model is Terra;
use fresh independent Terra Phase0 and two sequential final lenses unless the
owner changes it. Never more than one active subagent; no delegated implementation.
Record actual models, never claim a model unavailable in the environment.

Commit evidence, RED, GREEN and each verdict. Batch fixes by defect family;
after three failed correction cycles or repeated boundary failure, independent
convergence checkpoint before more patches. Two final lenses review the same
immutable candidate: correctness/authority, then tests/integration. Do not carry
approval across changed code/tests/contracts/dependencies.

Run focused affected suites, explicit sentinel
`tests/production/test_capture_lifecycle.py::test_private_plan_precedes_capture`,
changed-file Ruff and git diff --check. P6 compatibility modules are test_trust,
test_captured_authorization (pipeline), test_capture_lifecycle,
test_captured_authorization and test_adr0125_preflight (production), plus
pipeline/test_captured_artifact_grammar. Add affected ledger/state/vocab and
required purity/OOP/producer checks; no unjustified full suite.

Merge each genuinely closed packet after fetch with --no-ff from this task
worktree; push/verify remote, purge only contained branch with expected-head
check, and wrap RE-ENTRY plus elapsed design/implementation/tests/review/
corrections/integration. Do not merge blocked design as completed implementation.
Only after P5–8 and R23 truthfully close may the whole-F5a integration candidate
receive its two fresh final lenses and unblock F3/F5b.

Synthetic fixtures only. No real data, replay, training, HPO/refit, full backtest,
paper/live, deployment, environment changes, or source-branch cleanup.
