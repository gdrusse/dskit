# `dskit.production` — the production layer (proposal for ADR-0090 / ADR-0091)

**Status:** implementation-ready plan, revised 2026-09-04 after a whole-plan
invariant audit and two independent reviews. Its merge records owner approval;
implementation remains a separate change—no file under `dskit/production/` exists.

**What the owner is asked to approve.** (1) The file-by-file structure in §8.
(2) The design decisions D1–D24 in §3. (3) The pipeline change in §9.1 (a public
subgraph-rerun seam, ADR-0091). (4) The build phases and model assignment in §11.
(5) The resolved choices in §12. (6) The OOP ruling in §5.15 — the seam ABCs,
the composition-over-inheritance rule for `ServeLoop`, and the Liskov-clean
`Executor` / `SubmittingExecutor` / `Permit` split. Everything else is the
reasoning behind those.

How to read: §1–§3 are the ruling; §4–§7 are the contracts a test can be written
from; §8–§11 are what lands where and in which order; §13 traces every piece to
the report that grounds it.

---

## 1. The one-paragraph model

A **serve document** (JSON, its own identity hash) declares a production process:
which immutable **release** it serves (stable series id, run/artifact/code/source
and complete runtime fingerprints are verified, never restated), where live rows
**enter** that run's own node graph and how
wide the window is, which node keys are the decision **heads**, how head outputs
become domain-neutral **proposals**, how ticks are **scheduled** (clock, calendar,
cadence), which **guards** every proposal must pass, which **executor** acts,
which **accounting** evidence limits use, which fenced **lease** owns submit,
which **monitors** watch the decision stream, and where the **ledger** lives. One
`ServeLoop` class runs it: each tick is a fixed phase order — record start,
barrier, calendar gate, verify release, fetch, read entry, uniform-coverage gate,
evaluate heads, identify candidate scope keys, extract quotes, snapshot account,
construct proposals, guard/scope, barrier the decision plan and findings,
durably record intent, authorize, run the final submission verifier, act, record outcome, record the
terminal decision/tick, checkpoint last. Recovery closes any unfinished tick
before another begins, so every durable `tick_start` eventually
has one terminal `tick` and one `decision` in the hash-chained append-only
ledger, and one `dskit.journal` production row per normally or handled-completed process. Backtest,
shadow, paper, and live use the same decision document with validated injected
runtime objects; moving real money requires a recorded, expiring,
independently authenticated maker-checker **arming** bound to the immutable
release hash — never a config key or free-form principal name. The decision is
made only by re-executing serving-audited, read-only nodes from the training run
in `mode: load`, after every required entry key passes freshness, so the number that
reaches the venue is the number the backtest
scored.

## 2. Scope

**Generic — belongs in `dskit/production/`.** Its import rule is *stdlib +
`dskit.pipeline` + `dskit.onboarding` + `dskit.assets` + self, with
`dskit.journal` at function depth only*. This is deliberately **not** the tier-1
rule `tests/pipeline/test_purity.py` enforces on `dskit/pipeline/` (stdlib plus
`dskit.pipeline` itself, and no other `dskit.*` sibling at any depth, save the
two documented function-depth exceptions: `dskit.journal` per ADR-0056 and,
tier-2 only, the `dskit.onboarding` read seam per ADR-0077); `dskit.production` is an application of the
toolkit, not part of it, so it gets its own gate in `tests/production/
test_purity.py` and the word "tier 1" is not reused for it. Contents:
the serve-document grammar, release manifest and identity; clock / calendar /
cadence; the feed seam over onboarding; the decider (serving-document derivation
and serving-safe subgraph re-execution); proposals, guards, limits, accounting /
valuation, the breaker, authenticated arming; the executor ABC with shadow /
paper / recorded executors and a conformance battery; the ledger, checkpoint,
and reconciliation; monitors; alerts, health, heartbeats; resilience policies
(retry, circuit breaker, rate limiter, transport); outcomes, attribution, replay
parity; the readiness checklist; the CLI. Phase 1 ships all four rungs and the full
authority stack (§11), exercised end to end with fakes; the one phase-1 limit
is that a serve document may reference only nodes in the audited
`serving_effect` set (§9.1).

**Child (tier 3) — never in dskit:** the venue executor subclass (translation of
units, order types, error codes, dedup and observed native-replace semantics), the proposer when a
head's output shape is bespoke, its accounting / valuation and exposure measures,
its authenticated-approval verifier, every threshold and limit *value*, the
fenced deployment lease, calendar contents, readiness evidence, credentials (env-var
names in config, values in the environment).

**Deferred (named, not built, not documented as available):** a streaming
(`websocket`) seam; a `prometheus`/`opentelemetry` metrics pack; an
`exchange_calendars` pack; migrating the onboarding packs' hand-rolled backoff
onto `resilience.py` (its own ADR); a `sqlite` ledger pack and a `parquet`
run-reference pack are phase 2 (§11).

**Explicitly NOT closed by this proposal — the capital/report seam.** The owner's
constraint (4) names a half-built seam: `dskit/pipeline/kinds_report.py` expects a `capital` block carrying `twr`,
`mwr`, `cumulative_contributions`, `trading_pnl` (`:214-226`) and
`equity_curve` (`:1121`), and nothing in the repo produces any of them.
The `accounting` seam in this proposal is *venue* accounting — positions,
balances, fills, exposure — which is what guards and sizing require; it is a
different thing. `report.py` (phase 2) covers attribution, calibration and drawdown, not TWR/MWR
or the equity curve, and this ADR does not fill the seam.

**But phase 1 records what a later fill of that seam needs, because three of
those inputs cannot be recovered afterwards.** TWR, MWR and
`cumulative_contributions` all require the dated series of external cash
flows, and `equity_curve` requires a periodic portfolio valuation. Neither
survives in a design that folds only trading records: a deposit would be
indistinguishable from profit, and a mark would exist only inside an
`AccountState` that is never serialized. So phase 1 adds the `cash_flow`
record, the `cash` break class that routes an unexplained balance delta into
it *after* halting, as an authenticated operator act, and `tick.nav`. That is the whole cost — one
record kind, one break class, one field — and without it the seam could never
be filled for any series already running. `trading_pnl` was already
recoverable from `fill` and `outcome`; the other four now are too.

## 3. Design decisions

Each is a ruling with its reason and the alternative rejected. Reports in
brackets.

- **D1 — One package, `dskit.production`, one loop class, injected seams.** The
  seven-box consensus decomposition (clock, feed, state fold, decision, guard,
  executor, recorder/control — the fold and the recorder are two objects, §5.8.1) maps one-to-one onto the owner's constraints; every
  mature framework has all seven and the ones that lack guard/ledger/reconcile
  are the ones with documented live failures [R9]. Rejected: growing
  `children/*/live.py` (the owner's audit found the generic loop sitting in tier
  3 with most of the child's HIGH defects).
- **D2 — Mode is validated object composition, never a loop branch.** Shadow,
  paper and live bundles select compatible executor, accounting, approval and
  coordination objects (and clock/feed for replay) before construction [R7, R9].
  There is no `if mode ==` or `rung ==` anywhere in the package except
  `compose.py`, which is the one module whose job is to read the rung; the AST
  test is package-wide with that single exemption, not scoped to `loop.py`.
  The reason is
  structural, not lexical: the one place a rung could have leaked — minting a
  permit — is an injected `Authority` (§5.13.1), so there is nothing to branch
  on. The AST test rejecting `mode ==` / `rung ==` is a backstop against
  regression, not the argument; a test that only forbids two spellings can be
  satisfied by a third, which is why the seam does the work.
- **D3 — The decision is a re-execution of the run's own subgraph.** The decider
  derives a *serving document* from `<run-dir>/config.json` (trainables flipped
  to `mode: "load"` pinned to `artifacts/<key>`; search winners applied and search
  nodes dropped; gate/stat_test verdicts replayed; training splits dropped because
  serving never fits or scores a split).
  `ServingExecutionPolicy` runs during structural planning, before RESOLVE may
  construct or fingerprint a node. The closed producer API is
  `Node.serving_effect(params, verified_run_evidence) -> pure | entry_read |
  release_read | forbidden`; the base default is `forbidden`, and every
  servable class must override it explicitly. `TrainableNode` returns
  `release_read` only for manifest-pinned load mode, `ObservationRows` returns
  `entry_read`, and audited deterministic built-ins explicitly return `pure`;
  legacy, I/O-capable and unannotated classes remain forbidden. The result and
  class-code fingerprint are release evidence, never a document assertion.
  The policy defers the sole `entry_read` entirely:
  no constructor, fingerprint, `data_edge`, split materialization or `run` occurs
  before fetch and coverage gates. Missing/legacy `read`, write, network and
  unknown effects refuse. A `release_read` receives only a `ReleaseReader`
  capability that returns manifest-named, digest-checked frozen values; approved
  class fingerprints and static/conformance no-direct-I/O checks are release
  evidence. Release-bound child code is trusted, not sandboxed; malicious code is
  outside the threat model and this limitation is explicit.
  The entry must be a source root and dominate every non-release-bound path. Its
  class supplies a pure `serving_contract(params, verified_run_evidence)` with
  source locator, entity-key projection, event-time extraction, per-key digest
  recipe. The universe is not the contract's business: it comes from the
  document (§5.2) and is never inferred from dedupe `key_fields`. The configured window param must already exist in the run
  document, preserving the driver's existing-key-only override rule. After fetch,
  one entry execution returns the rows plus the seven-field `EntryBatch`
  defined in §5.2; the same
  contract validates exact uniform coverage and oldest-input freshness before
  only pure or capability-backed release descendants run. No second mutable
  source can execute. Rejected: ordinary RESOLVE before the gate, a label-only
  I/O promise, arbitrary ancestors before safety gates, or hand-built nodes
  [owner constraints 1, 3; R2 Rule #32; R6 §2.6].
- **D4 — Live rows enter through the connector seam.** The feed is `acquire
  --mode live` (`run_acquisition`) or a read of a store another `watch` process
  fills. The release pins the source-config content hash/version; every pull
  refuses if the source's ACTIVE alias resolves differently. Zero new records is
  not a dead feed: status comes from acquisition/link failure and the configured
  watermark age ladder. The run's data node reads the same onboarding root it was
  trained from [owner constraint 2; ADR-0046]. Rejected: a second vendor fetch
  beside the connector (the audited `adjustment` drift).
- **D5 — The pipeline's `clock`/`schedule` sections stay as they are.** Cadence
  belongs to the serve document: a training document describes a computation, a
  serve document describes a process, and research cadence (event-grid,
  ADR-0047) is a data fact, not a scheduler [R7 §2.10 argued the opposite; this
  proposal keeps the engine runtime-free].
- **D6 — Freshness is coverage-wide, never a maximum timestamp.** Phase 1
  requires a uniform entry snapshot. The release-bound entry declares the full
  required key set; `EntryBatch` carries the latest as-of and source digest for
  every key plus a coverage digest. Missing/duplicate/extra keys refuse, and
  `data_asof_ms = min(latest_asof_ms by required key)`. Thus one fresh instrument
  cannot hide a stale input; partial-universe acting is deferred until a
  row-level provenance contract exists. Every proposal, decision plan and live
  permit binds that same coverage/input digest and `inputs_asof_ms`; its input
  deadline is the oldest watermark plus `max_staleness_ms`. `tick_at` orders ticks, `observed_at_ms` records
  wall time, and `time.monotonic()` paces/timeouts [R7 §1.1, §2.1].
- **D7 — Calendar is config plus an injected object; cadence is a policy family;
  overrun is a strategy.** `always-open`, `weekly-sessions` (zoneinfo, local-time
  sessions, UTC arithmetic, DST-gap boundary refused), `event-window`, `composite`
  ship; `fixed-interval` (drift-free `anchor + k·period`), `aligned-bar`
  (`bar_ms + publish_delay_ms`), `at-times`, `on-data` ship; overrun
  `skip | coalesce | queue` with `max_lag_ms`, never concurrent [R7 §2.2–2.3].
- **D8 — Proposals are domain-neutral numbers; a `Proposer` hook makes them.**
  `Proposal{instrument, side, qty|notional, limit, tif, reference_price,
  exposure, confidence, prediction, baseline, expected_value}`; the core ships
  `intent-rows` (head rows already in proposal shape, field map configurable)
  and `target-positions` (target → delta against the ledger's positions); a child
  subclasses `Proposer` for a bespoke head [R5 §2.2, R6 §2.1].
- **D9 — Guards: one hook, a closed verdict lattice, every finding recorded.**
  `Guard.check(proposal, state) -> Finding`; verdicts `allow < warn < amend <
  refuse < hold < halt`; the chain records every finding with value, bound and
  reason. Before any proposal-shaped submit, a barriered `decision_plan` captures
  the original/final proposal, every finding, entry/head/candidate provenance,
  input/quote/evidence as-of+digests, risk effect/version/digest, scope verdict
  and action-matrix result. The
  terminal decision references that immutable plan; recovery terminalizes it
  without rerunning mutable state. Amendments may only reduce one declared
  scalable field. All guards
  first evaluate the original proposal; amendments compose by the strictest
  monotone reduction, conflicting amendments refuse, and the final candidate is
  re-run through every hard guard with amendment disabled. Any remaining breach
  refuses or halts. `pause` produces `hold` with a recorded `resume_at`;
  cancels bypass proposal/business guards and submit-rate budgets, but still use
  a reserved, priority transport lane with bounded retry and reconciliation; they
  are never emitted as an unbounded burst [R5 §2.1, R1 §1.2]. `Limit` is ONE
  class parameterised by measure × window × bound × scope over a stdlib measure
  registry plus a `pkg.module:Class` doorway [R5 §2.3]. Default-deny extends to
  limits: a document whose executor can reach live must declare a per-proposal
  size limit, an accounting strategy, and a period loss limit backed by that
  supplied accounting state, or `plan` refuses [R5, FIA].
- **D10 — One policy owns every action and transition matrix.** `ActionPolicy`
  owns operation `submit | cancel | query | reconcile`; accounting exclusively
  classifies a submit's mutually exclusive `risk_effect ∈ {increase, neutral,
  reduce}` against current positions and working orders. No overlapping “new”
  class exists. The matrix is rung × breaker × health × readiness × operation ×
  risk-effect × authority. Breaker is `active | reducing | halted`; health is `starting |
  ready | degraded | unhealthy | stopping`. Every cell is explicit:
  - `shadow + ready + {active | reducing}`: submits route only to
    `ShadowExecutor` and return `not_sent`; no live permit exists.
  - `paper + ready + active`: every risk effect may submit to `PaperExecutor`
    without live authority. `paper + ready + reducing` permits only proven
    reductions. Paper can never select a `LiveExecutor`.
  - `{live_limited | live} + ready + active + readiness GO`: each submit requires
    a fresh exact-intent permit derived from the ordinary arm. In `reducing`, only
    accounting-proven reductions named by a fresh `ReductionAuthorization` are
    converted by policy/arming into an exact-intent `ActPermit`: policy/arming
    supplies the authority scope, and the injected `Authority` (§5.13.1) is
    what mints the permit.
  - Starting permits only startup query/reconcile/cancel; stopping permits only
    query/reconcile/cancel. Halted, degraded and unhealthy refuse submit.
    Query/reconcile/cancel use the bounded priority policy in every state.
  `replace` is not a public executor verb. A price/size change is cancel to a
  terminal state followed by a new proposal through the complete guard,
  decision-plan, intent, authority and fencing path. Native replace events may
  be observed, never initiated by core or a child override.
  `TransitionPolicy` alone permits `active → reducing` by verified reduce/flatten,
  `halted → reducing` by verified flatten, `{active | reducing} → halted` by
  trip/HALT, and `{reducing | halted} → active` by verified resume. Leaving
  active and resume both revoke every ordinary arm; ordinary arming is issued
  only while active/ready with a release-bound GO and HALT absent. Reduction authority is issued/executed
  only while reducing. No timer changes these states. Both rule sets carry the completeness test
  and the golden table of §5.14
  [R5 §2.4 recommends orthogonal composition, which is the shape used here;
  R4 §2.1, R1 §1.4].
- **D11 — Arming is an authenticated maker-checker act, not a config key.**
  The document declares the graded rung. `arm-request` writes a signed canonical
  request bound to the `release_hash`, the exact document rung, bounded expiry,
  allowlist and limits overlay; it cannot promote the rung. `approve-arm` accepts a
  separately signed approval. The graded `arming.approval` seam resolves a
  child `ApprovalVerifier`, whose code/trust-root reference is release-bound and
  which derives principal ids from proofs — no free-form `--by` identity.
  For ≥ `live_limited`, maker and checker must differ; expiry may not exceed the
  graded `document.arming.max_duration_s`; an allowlist must be a subset and every limit
  overlay must prove it is at least as strict as the document. The final arming
  record binds the release hash and both proof digests. Serving live additionally
  requires `--armed` and `DSKIT_PRODUCTION_ARM=<release hash>`; absent or
  expired arming records `not_armed` and refuses submission; it never silently
  changes the configured executor or rung. Query, reconcile and cancel remain
  available without arming. Immediately before each ordinary live submit, the
  guard chain re-applies the current arming allowlist and overlay. `ActPermit`
  binds the plan/intent/client ref, instrument/risk effect, release, input,
  quote, evidence and risk versions/digests, scope, authority, deadline and fence.
  The executor accepts only this type. `ReductionAuthorization` is an alternative
  input to policy/arming—not execution—which the `ReductionAuthority` of §5.13.1
  converts into a permit; it permits only its pre-signed reduction
  intent digests under document limits; each digest is single-use and the whole
  authority has a short deadline
  [R5 §2.4, R1 §2.1, R3 §2.5].
- **D12 — Halt never flattens.** Halt = refuse submissions + best-effort cancel
  of working orders (outcome recorded: `none | submitted | failed | partial |
  unknown`). Flattening is a separate authenticated human act that enters
  `reducing`; the accounting strategy, not a model claim, must prove each
  proposal cannot increase absolute exposure, and every ordinary guard still
  runs. `reduce --proof` enters reducing but grants no submit authority.
  A verified `flatten-request --plan --proof` records a maker-signed
  `ReductionPlan` bound to the release, fresh risk version/digest, full canonical
  intents/digests and a short expiry; each entry's `candidate` must preserve the proposal's instrument and declare
  the scope keys its limits will be measured over — the §5.3 rule, applied at
  signing time because here the pair is human-supplied rather than proposer-
  derived — and two entries whose `(instrument, side, qty, limit)` match refuse — the check is
  over proposal content, not over `reduction_intent_digest`, which now includes
  `index` and so differs for byte-identical proposals. It then
  barrier-transitions `active | halted → reducing` without submit authority.
  `approve-flatten --request --proof` requires a distinct checker and creates a
  `ReductionAuthorization` with one single-use right per named intent digest.
  `execute-flatten --authorization --proof` verifies the closed execution
  purpose and durably queues those stored intents for the active loop, which
  processes them in canonical order through the same sequential pre-submit
  gates as model intents. Each client reference is
  `H("flatten-v1", release_hash, reduction_request_id, zero_based_intent_index,
  reduction_intent_digest)`, independent of CLI/process time, ledger sequence or retries.
  Execution stops on the first refusal, expiry or ambiguous outcome; completed
  rights stay consumed and the writer revokes all unused rights after any partial
  result, requiring a new plan. Crash recovery queries the deterministic client
  ref and may resume only the same reserved
  intent after the executor proves it was not sent. Each live reduction still
  needs an intent-bound permit and current fence token. `resume --proof --acknowledge`
  uses the verifier's reset purpose and only works after cooling-off. Transitions
  are ledgered/barriered; a prior arm cannot authorize reset or flatten. Any
  transition away from `active` revokes ordinary arming; resume returns
  `active` but unarmed, so a fresh maker-checker arm is required. Only a
  verified resume (to `active`) or verified flatten request (to `reducing`)
  may atomically retire the stable `HALT` sentinel before its transition
  barrier; a crash in between leaves the ledger folded halted
  and therefore cannot enable action. State
  persists and never changes on a timer [R5 §1.2, R1 postmortems].
- **D13 — Record before act, checkpoint last, reconcile before deciding.** The
  `tick_start` crosses a mandatory `ledger.barrier()` before work. Each completed
  pre-submit evaluation becomes a `decision_plan` and crosses a barrier before
  any proposal submit I/O. Each intent, reduction-authority use and live authorization
  then crosses its own barrier before `executor.submit`, regardless of batch
  policy. Arming, breaker, queued control results, reduction/reset and adoption
  transitions use the same barrier. An executor call that
  raises after the request leaves `unknown`, which only `executor.order(ref)`
  may resolve — never a blind resend; the checkpoint is written last. Startup
  reconciles before `READY`; mismatches halt or refuse. Adoption is never a
  startup flag or automatic policy: it is a separate authenticated, ledgered
  operator action after inspection [R7 §2.4–2.6, R3 §1.2, R1 §1.1].
- **D14 — Execution and accounting are separate venue-neutral seams.**
  `Executor` owns `spec`, `capabilities`, `check`, `cancel`, `order`,
  `open_orders`, `fills`, `balances`, positions and settlements — read, query
  and cancel only, always constructible, never armed. `SubmittingExecutor(Executor)`
  adds `submit(intent, permit, state)` (§5.15). Neither has an initiated replace verb. Read/query/cancel construction is always possible.
  Shadow/paper `submit(intent, SimulatedPermit)` needs no live authority;
  `LiveExecutor.submit` accepts only an `ActPermit`—never a raw ordinary or
  reduction authority. Policy/arming consumes either authority and supplies its scope; the injected
  `Authority` (§5.13.1) mints the exact permit. Minting has exactly one home.
  After the final barrier, the live wrapper holds the local act gate and calls
  `SubmissionVerifier.verify_and_call(intent, permit, state, native_call)`. It rehashes
  the already frozen `EntryBatch` in memory and checks its source identity and
  input deadline without rereading mutable rows. It refreshes quote, accounting,
  authority, executor identity and lease state; requires exact bound
  versions/digests; rechecks deadlines, hard guards and policy; then invokes
  native submission synchronously with no caller-visible gap.
  Any mismatch returns `not_sent`; it never replans or reauthorizes in place.
  The full permit and a timeout bounded by its remaining lifetime reach
  `_submit_native`; the child gateway atomically enforces fencing token,
  permit deadline and client-ref idempotency before sending. External fills or
  venue state can still change after the last snapshot—no in-process design can
  make those atomic—so such races are recorded `unknown` and reconciled, never
  blindly retried. A native call must honor the bounded deadline; conformance
  uses a never-returning fake to prove timeout disables further sends.
  `RiskVersion{economic_seq, executor_token, accounting_tokens}`: `economic_seq`
  advances on economic events only — an `authority_use` is a rights
  reservation, not an economic one, and must not advance it, or every reduction
  submit would fail step (7)'s exact-version recheck against the version its
  own plan bound. **The same exemption covers the leg's own `intent`**: step
  (5) appends it and the fold records it as `pending`, which is not yet a
  position reservation — an intent becomes economic when it is acknowledged,
  not when it is recorded. Without this every live submit would refuse at step
  (7) against the version step (2) bound, which is a systematic failure an
  end-to-end run would surface immediately. It changes on every position
  reservation, order/fill/correction/balance/position or evidence update; a live
  adapter lacking monotonic source tokens refuses at plan. Eleven order statuses
  preserve terminality; every `OrderState` enforces
  `filled_qty + remaining_qty == qty`; fills carry `pending | final | reversed`;
  native units are declared and money is `Decimal` [R8 §2.1–2.3].
  `Accounting.snapshot` receives every canonical measure/window/scope requirement
  and returns versioned, source-digested evidence including corrections,
  baselines and candidate scopes. Proposals act sequentially with prior
  reservations/results folded. Live requires child accounting, valuation
  freshness, approval, a fenced lease and deadline-conforming transport. Core
  ships shadow, paper and recorded execution/accounting, plus the abstract
  `LiveExecutor` wrapper; every concrete venue subclass is a child.
- **D15 — One append-only chain per explicit serve series; state is its fold.**
  `series_id` is a required operator-issued UUID, stable across releases and
  distinct from the display name, release and venue/account lease scope. A
  `series.json` genesis record binds it before the first append and a mismatch
  refuses. Process starts and stops continue that chain. The ledger assigns dense
  `seq`, `recorded_at_ms` and `prev_hash`; idempotency first looks up the
  caller's stable `id` and compares a `payload_digest` over caller-controlled
  fields only. JSONL uses one `O_APPEND` write, torn-tail recovery and segment
  continuity; `fsync` policy is graded, and safety records always cross a barrier.
  Corrections supersede, never mutate. `arming.json`, `breaker.json` and
  `checkpoint.json` are caches carrying the ledger head they project. A
  valid cache behind the ledger is discarded and rebuilt; an ahead/divergent
  cache refuses. On graceful stop the writer appends/barriers a `process` record with
  `event: stop`,
  obtains the resulting final head, then writes one journal row rendering
  `process_id` and that head into `notes` per D22; the ledger never claims its
  own final hash.
  Optional long-process anchors go only to an external sink
  [R6 §2.1–2.4, R4 §1.3].
  The serving process is the sole ledger writer. Concurrent control CLIs use a
  durable inbox with caller-generated UUID `request_id` plus payload digest:
  retries reuse the id, while legitimate repeated commands use new ids. The
  writer verifies, records and barriers commands idempotently; `HALT` remains
  an independent sentinel.
- **D16 — Monitors are strategy objects with a first-class `insufficient`.**
  `Monitor.fit/observe/verdict/state/restore`; families operational, stream,
  distribution, outcome; `Reference`, `Chunker` and `Threshold` strategies
  (`Response` is a closed vocabulary, not a strategy object); verdicts `ok | warn | alarm | insufficient`; below `min_n` never
  `ok`; keep a fixed anchor and a rolling reference [R2 §2].
- **D17 — Alert on symptoms; closed severities; sinks cannot block the loop.**
  Severities `info | warning | error | critical` are pinned across backends.
  Construction validates configuration only; reachability is a dependency probe,
  never a constructor side effect. Core sinks use transports with real socket
  deadlines. Each custom sink gets one daemon worker; a supervisor marks it
  timed-out and disables further sends without spawning replacements, bounding a
  broken sink to one stuck thread. Failures and bounded-queue drops are swallowed,
  counted and surfaced through the external dead-man [R4 §2].
- **D18 — Health is a state machine, the dead-man's switch is external.** Local
  failure → `unhealthy` (stop acting *and* stop heartbeating); dependency failure
  or staleness → `degraded` (observe, refuse acts). One supervised probe worker
  per probe publishes timestamped results; a missed deadline or dead/stuck worker
  is a failure and no replacement is spawned until it returns. A separate
  heartbeat worker emits `file` / `url` at `every_s`, keyed by `process_id`
  plus its own sequence/time, independently of ticks. It watches the atomic
  monotonic timestamp of the last successful tick; exceeding `dead_after_ms`
  makes health unhealthy and stops heartbeat. The external supervisor then pages.
  [R4 §1.2, §2.1].
- **D19 — Resilience policies are pure stdlib objects with injected clock,
  sleeper, and rng.** Outcome kinds `ok | transient | throttled | fatal |
  ambiguous`; an ambiguous WRITE may never retry, only reconcile; full-jitter
  backoff capped at `MAX_BACKOFF_S` (imported from onboarding — one ceiling);
  a breaker per scope with `min_calls` small; token buckets per scope with a
  write bulkhead of one [R3 §2].
- **D20 — Replay parity is a test the package runs, not a promise.** The ledger
  plus recorded executor responses are the tape; `replay` re-executes the same
  nodes on the recorded rows with `TestClock`, `RecordedExecutor` and a
  `RecordedIdSource`. Tick ids are allocated before `tick_start`; model decision
  and client ids derive from release/tick/leg/attempt. Flatten client refs derive
  from the release, reduction request, canonical intent index and intent digest.
  Neither formula depends on process time or ledger sequence. Exact semantic
  decision payloads must match; ledger envelopes and hashes are compared
  separately [R6 §2.6, R7 §2.9, R9].
- **D21 — Outcomes are bitemporal, joined by id, reported as-of.** `outcome`
  records carry `effective_at_ms`, `known_at_ms`, `terminal`, `supersedes`;
  reports are computed at a `known_at ≤ T` cut; derived labels use a strict
  forward as-of join; settled and marked never share a series [R6 §2.5].
- **D22 — Journal policy is one production row per normally completed process,
  written through the journal's existing signature.** `record_production(step,
  inputs, outputs, db_location, notes)` (`dskit/journal/hooks.py`) has a fixed
  field set: there is no `process_id`, `final_head_seq` or `final_head_hash`
  column, and this plan adds none — **no journal code changes**. After a
  graceful/handled serve stop the loop calls it once with `step` naming the verb
  and rung (within the 80-character `_STEP_MAX`), `db_location` the serve-series
  root, and the process id plus final ledger head rendered into `notes` in one
  documented `production-v1 process=<id> head=<seq>:<hash>` form that `verify`
  parses back. Serve never journals a tick or consumed
  command. Each mutating CLI captures its queue/synchronous result and calls
  `record_production` exactly once in `finally`; it does not use the existing
  context manager because that freezes outputs before the attempt. This covers
  `plan` (it writes an immutable release, so it is a mutating verb),
  `ready` (it appends a `readiness` record),
  `arm-request`, `approve-arm`, `disarm`, `halt`, `reduce`, `flatten-request`,
  `approve-flatten`, `execute-flatten`, `resume`, `reconcile` and `adopt`;
  read-only verbs do not journal. SIGKILL/power loss can leave a durable inbox
  command without a CLI journal row because the stores have no shared transaction;
  `verify` reports that gap. The ledger/inbox is authoritative, and the plan
  makes no impossible exactly-once claim across independent stores.
  This supersedes ADR-0056's item 3 (wrapping a child's `live.main` in the
  `journal.production()` context manager) for serve processes only; children
  that still own a loop keep using it until they port.
  Testability: `dskit/journal/record.py` returns `None` under pytest before
  touching any store, so a test can never observe a real appended row. Production
  therefore calls the journal through one injected `journal_hook` seam defaulting
  to `record_production`; tests assert against a recording fake and one
  non-pytest subprocess case proves the real call path. This is a production-side
  seam only — the journal package is unchanged.
- **D23 — Decision phases are single-threaded; auxiliary work is isolated.**
  Tick phases run sequentially for deterministic ordering [R9 Nautilus]. Alert,
  probe and heartbeat workers never execute decision code or mutate folded state;
  they communicate through bounded queues or atomic snapshots. A stuck custom
  call consumes at most its one dedicated daemon thread and turns health degraded
  or unhealthy without blocking tick shutdown.
- **D24 — A content-and-runtime-bound release is what gets armed.**
  `doc_hash` uses the pipeline recipe unchanged — `config_hash`
  (`dskit/pipeline/base.py`), which strips `notes` everywhere and drops whole
  top-level sections named in an exclusion tuple. It cannot exclude a *path*
  inside a kept section, so the grammar is shaped to that constraint rather than
  the reverse: every non-identity value lives in its own excluded top-level
  section, and nothing graded shares a section with anything excluded.
  `PRODUCTION_NON_IDENTITY_SECTIONS = ("alert_endpoints", "heartbeat",
  "placement", "env")` are dropped; `durability` (holding `fsync`, previously `ledger.fsync`)
  is graded, as are arming policy and every
  decision/guard/execution/accounting/approval/coordination knob. Rejected: a
  path-aware canonicalizer — it would be a second identity recipe beside the
  pipeline's, and D24's whole point is that serving reuses the one that already
  pins 17 document identities (16 in `tests/pipeline/test_foreach.py`, one in
  `tests/pipeline_libs/test_mlflow.py`; the other three sha256 literals under
  `tests/` are `model_hash` and file-content pins, not `config_hash`).
  Feed source and key/time/digest recipe derive from the entry's pure
  ServingContract, and `required_keys` from the serve document; both are
  normalized before hashing. `plan` also derives a
  `RuntimeFingerprint`: Python implementation/version/cache tag/ABI, platform and
  libc, project/lock digests, and the complete installed-distribution inventory
  (`name`, `version`, `direct_url`, `RECORD` digest), plus a container image digest
  when available. Decision-affecting non-secret environment values must be graded
  document fields; secrets may authorize transport but cannot alter decisions.
  The immutable `ReleaseManifest` contains `doc_hash`, run/serving hashes,
  artifacts/timestamps, resolved classes/code/adapter, `FeedSpec`, source config,
  approval/lease fingerprints, `checklist_digest` and `runtime_fingerprint`.
`plan` canonicalises the file at `document.readiness.checklist` into
`checklist_digest`, and `ready` refuses a checklist whose digest differs —
without it `doc_hash` covers the path and not the contents, so a GO could be
re-earned against a quietly shortened checklist under a fixed release and a
live arm. Same hole as `required_universe`, same fix.
  `release_hash = canonical_hash(manifest)`; arming, intents, process records and
  immutable release subdirectory use it. Startup, every tick and immediately
  before submit re-verify all hashes, artifact age and runtime fingerprint;
  runtime/distribution drift records a refusal and requires a new release/arm.
  expiry records `artifact_expired`, degrades health, refuses action, and requires
  a new release/arming. Every pull proves the pinned source hash, and every submit
  checks manifest, arming, permit and lease bindings agree.

## 4. The serve document

### 4.1 Grammar (default-deny at every level; `notes` allowed everywhere)

```jsonc
{
  "name": "yourproject-serve",
  "series_id": "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1",
  "rung": "paper",
  "notes": "Why this process exists and how to promote it — the 'why'.",
  "serving": {
    "run_dir": "pipeline_runs/train-2026-01-01-abcd1234",   // the run served; config.json + artifacts read from here
    "adapter": "yourproject",                                 // exact import, captured in the release fingerprint
    "entry": {"node": "bars", "param": "since_ms", "window_ms": 14400000},
    "heads": ["select"],                                      // node keys whose outputs form the proposals
    "required_universe": "configs/universe-serve.json",       // the exact key set every tick must cover; pinned into the release
    "proposer": {"uses": "intent-rows", "params": {"output": "picks", "fields": {"instrument": "symbol", "side": "side", "qty": "qty"}}},
    "replay": {"gate": "recorded", "stat_test": "recorded"},  // by ROLE: gate/stat_test nodes replayed from the run's records
    "max_artifact_age": "P30D"                                // refuse a run older than this (FreqAI expiration_hours)
  },
  "feed": {"uses": "entry-source", "params": {"pull": "acquire"}}, // source and coverage derive from ServingContract
  "schedule": {
    "clock": {"uses": "wall"},
    "calendar": {"uses": "weekly-sessions", "params": {"tz": "America/New_York",
                 "sessions": [{"days": ["mon", "tue", "wed", "thu", "fri"], "open": "09:30", "close": "16:00"}],
                 "holidays": ["2026-11-26"], "after_open_s": 60, "before_close_s": 120}},
    "cadence": {"uses": "aligned-bar", "params": {"bar_ms": 60000, "publish_delay_ms": 5000}},
    "overrun": {"policy": "coalesce", "max_lag_ms": 30000},
    "dead_after_ms": 600000,
    "max_staleness_ms": 120000,
    "max_quote_age_ms": 30000,
    "max_venue_skew_ms": 1000
  },
  "guards": {
    "size":     {"uses": "limit", "params": {"measure": "quantity", "bound": {"max": "100"}, "on_breach": "refuse"}},
    "exposure": {"uses": "limit", "params": {"measure": "exposure_after", "scope": "aggregate", "include_working": true,
                                             "bound": {"max": "20000"}, "warn_at": 0.8, "on_breach": "refuse"}},
    "day_loss": {"uses": "limit", "params": {"measure": "pnl", "window": {"calendar": "session"}, "bound": {"min": "-500"}, "on_breach": "halt"}},
    "stale":    {"uses": "limit", "params": {"measure": "input_age_ms", "bound": {"max": 30000}, "on_breach": "refuse"}},
    "sane":     {"uses": "range", "params": {"field": "confidence", "min": 0, "max": 1, "nan": "refuse"}}
  },
  "execution": {"uses": "paper", "params": {"fill_rule": "touch", "fees": {"kind": "bps", "bps": 5}, "seed": 7},
                "submit_timeout_ms": 5000, "on_halt": {"cancel_open": true}},
  "accounting": {"uses": "paper", "params": {}, "max_valuation_age_ms": 60000},
  "arming": {"max_duration_s": 14400, "approval": {"uses": "deny-all", "params": {}}},
  "coordination": {"scope": {"venue": "paper", "account": "strategy-a"},
                   "lease": {"uses": "process", "params": {}},
                   "ttl_ms": 30000, "renew_every_ms": 10000, "renew_timeout_ms": 2000},
  "reconcile": {"on_start": true, "every_s": 300, "on_mismatch": "halt", "lookback_ms": 86400000},
  "monitors": {
    "pred_shift": {"uses": "psi", "params": {"field": "prediction", "bins": 10,
                   "reference": {"uses": "leading", "params": {"n": 500}}, "window": {"kind": "count", "n": 300},
                   "threshold": {"kind": "alpha", "alpha": 0.01}, "response": "warn"}},
    "coverage":   {"uses": "coverage", "params": {"window": {"kind": "count", "n": 50},
                   "threshold": {"kind": "constant", "min": 0.5}, "response": "warn"}}
  },
  "health": {"failure_threshold": 3, "success_threshold": 1, "timeout_s": 1.0,   // the default HealthProbe.timeout_s
             "probes": {"disk": {"uses": "ledger-writable"}, "venue": {"uses": "executor-check", "scope": "dependency"}}},
  "durability": {"fsync": "every"},                          // GRADED: how safely records land
  "resilience": {"retry": {"max_attempts": 3, "base_s": 0.05, "throttle_base_s": 1.0, "cap_s": 20.0,
                           "jitter": "full", "retry_after": "honor", "retry_writes": "idempotent_only",
                           "budget": {"capacity": 500, "transient_cost": 14, "throttle_cost": 5, "refund": 1}},
                 "breaker": {"min_calls": 5, "failure_rate": 0.5, "open_s": 30},
                 "limiter": {"submit": {"rate_per_s": 5, "burst": 5, "max_in_flight": 1},
                             "cancel": {"rate_per_s": 10, "burst": 10, "reserved": true}},
                 "transport": {"uses": "urllib", "params": {"connect_s": 2.0, "read_s": 5.0}}},
  "lifecycle": {"cooling_off_s": 900, "shutdown_grace_s": 30},
  "readiness": {"checklist": "configs/readiness.json", "waivers": [], "valid_for_s": 86400},
  "heartbeat": {"every_s": 60, "in_degraded": false, "emitters": {"file": {"uses": "file"}}},
  "alerting": {"sinks": {"ops": {"uses": "webhook"}},                 // GRADED: which sinks exist and HOW they deliver
               "routes": [{"severity": "critical", "sinks": ["ops"]}, {"severity": "warning", "sinks": ["ops"]}],
               "group_wait_s": 30, "repeat_interval_s": 14400, "rate_limit": {"max_per_hour": 20, "burst": 5}},
  "alert_endpoints": {"ops": {"url_env": "OPS_WEBHOOK_URL", "template": "slack", "timeout_s": 5}},  // EXCLUDED: where it goes
  "placement": {"ledger_root": "./serve", "rotate": {"by": "day", "max_bytes": 268435456}, "log_dir": "./serve/logs"},
  "env": {"env_file": ".env", "require": ["OPS_WEBHOOK_URL"]}
}
```

Numbers above are illustrations. **Code holds no threshold**; every default is one
named constant read by `validate_params` and the run alike.

### 4.2 Identity

`doc_hash = config_hash(document, exclude=PRODUCTION_NON_IDENTITY_SECTIONS)` —
the pipeline recipe unchanged (`dskit/pipeline/base.py`): `notes` stripped
everywhere, sorted keys, compact, ASCII, NaN refused, and whole top-level
sections dropped — with one wrinkle a golden-identity test must respect: a
section named in `NULLED_IDENTITY_SECTIONS` (today `("tracking",)`) is set to
`None` and its key stays in the hash material rather than being removed
(`base.py:226-238`). No production section is nulled, so all four excluded
sections are genuinely dropped. `PRODUCTION_NON_IDENTITY_SECTIONS = ("alert_endpoints", "heartbeat",
"placement", "env")`.

**Why alerting is split in two.** Emptying `routes` under a live arm would
silence the paging path D17 treats as a safety control, so alerting policy is
graded: `routes`, `group_wait_s`, `repeat_interval_s`, `rate_limit`, **and the
sink kinds** — switching a route's only sink from `webhook` to `memory`
silences it exactly as effectively as emptying `routes`, so the delivery
mechanism is policy, not placement. Only the endpoint values move to the
excluded `alert_endpoints`: the env-var name holding the URL, the message
template, the socket timeout. Every alert is ledgered regardless of delivery
(§6's `alert` record carries per-sink outcomes), so the exposure this closes is
latency of human awareness rather than loss of evidence.

That recipe can only drop **whole top-level sections**, so the grammar is
partitioned to suit it — this is the reason `durability`, `resilience`,
`lifecycle` and `readiness` are top-level rather than nested under `ledger`,
`execution` or `health`. Every remaining section is graded: `series_id`, `rung`,
`serving`, `feed`, `schedule`, `guards`, `execution`, `accounting`, `arming`,
`coordination`, `reconcile`, `monitors`, `health`, `durability`, `resilience`,
`lifecycle`, `readiness` and `alerting`. Eighteen graded sections plus four excluded plus
`name` and `notes` account for every key in §4.1; `validate` refuses a top-level
key that is in neither list, so the partition cannot silently drift.

The stable root is `<placement.ledger_root>/<series_id>/`; `series.json` must
match before the ledger opens, and immutable release metadata lives under
`releases/<release_hash>/`. The display name never selects storage. `plan`
materialises the manifest from §5.3.1. Changing anything that can affect data,
decisions, permissions, durability, valuation, or actions therefore creates a
new release; relocating storage or notification endpoints does not.

### 4.3 `uses` resolution

Every `uses` is a registered kind name or a `pkg.module:Class` reference, resolved
exactly as pipeline nodes and onboarding connectors are (import = registration);
each family has its own registry, and every `uses` in §4.1 resolves through
exactly one of them: `CLOCK_KINDS`, `CALENDAR_KINDS`, `CADENCE_KINDS`,
`FEED_KINDS`, `PROPOSER_KINDS`, `GUARD_KINDS`, `MEASURE_KINDS`,
`EXECUTOR_KINDS`, `ACCOUNTING_KINDS`, `APPROVAL_KINDS`, `LEASE_KINDS`,
`MONITOR_KINDS`, `REFERENCE_KINDS`, `CHUNKER_KINDS`, `THRESHOLD_KINDS`,
`PROBE_KINDS`, `ALERT_SINK_KINDS`, `HEARTBEAT_KINDS`, `TRANSPORT_KINDS`,
`FEE_KINDS`.
Registering a name twice refuses. A child registers nothing and references its
classes by path.

`ALERT_SINK_KINDS` is deliberately not called `SINK_KINDS`: that name is already
taken by `dskit/pipeline/base.py`'s tracking-sink registry, and both would carry
a kind named `memory`. A test asserts the two registries are disjoint objects
and that neither name is importable from the other package.

`uses`, `kind` and `measure` are one mechanism at three depths: `uses` selects
a family member — at the top level, and also nested where the nested thing is
itself a registry family (`reference: {"uses": "leading", "params": {"n":
500}}`, which takes the same `{uses, params}` shape as every other `uses`
site); `measure` selects a `MEASURE_KINDS` entry inside a guard; and `kind`
selects a nested strategy inside one
(`window: {"kind": "count"}` → `CHUNKER_KINDS`, `threshold: {"kind":
"alpha"}` → `THRESHOLD_KINDS`, `fees: {"kind": "bps"}` → `FEE_KINDS`).
Both resolve through the registries above and neither may be read with an
`if kind ==` chain; `FEE_KINDS` exists so the five fee strategies of §5.7 are
registry-resolved like everything else rather than selected by string; it is
the one concept with both a registry and a `vocab.py` tuple
(`FEE_KIND_NAMES`), and a test pins that their key sets are equal.
A `uses` or `kind` whose family has no registry is a validation error, not a
default — `test_document.py` enumerates the §4.1 grammar and asserts every
selector site names a registry in this list.

## 5. The seams

Conventions for every class below: `_PARAMS` default-deny with
`reject_unknown_params` imported from `dskit.pipeline.node`; validation in
`__post_init__`/`validate_params` accumulating every problem into one
`ProductionError`; abstract hooks are `@abstractmethod`; `__all__` is the API;
NumPy docstrings with an instantiating example; no type hints in signatures.

### 5.0 `vocab.py`, `base.py`, `redact.py`

The three modules the TDD order builds first, so their contracts are here rather
than only as a line in §8.

- `vocab.py`: every closed set in §8's list as a module-level tuple, plus
  `VERDICT_ORDER` (the `allow < warn < amend < refuse < hold < halt` index map),
  `TERMINAL_STATUSES ⊂ STATUSES`, `SEVERITY_LEVELS` (the pinned PagerDuty / OTel
  / syslog / `logging` map) and `EXIT_CODES`.
  `RUNGS = ("shadow", "paper", "live_limited", "live")` — exactly four, and
  the order is the ladder. Backtest is **not** a rung: it is a replay
  configuration of `shadow` (recorded clock, feed and executor), which is why
  D20 can call replay a swap of objects rather than a fifth cell in D10's
  matrix. No logic, no imports beyond stdlib,
  `__all__` is the whole surface. Nothing anywhere else may define a closed set.
- `base.py`: `ProductionError` (accumulate-every-problem, one raise);
  `reject_unknown_params` re-exported from `dskit.pipeline.node` and the
  accumulate-errors checkers from `dskit.assets.base` (the same re-export
  idiom `dskit/onboarding/base.py` already uses); ms/UTC helpers; `canonical_bytes(obj)` and
  `record_hash(prev_hash, envelope)` — the one sha256-canonical idiom §6 names,
  defined once.
- `redact.py`: `Secrets` resolution through `dskit.pipeline.env.load_env` over
  `env.env_file`, refusing at `plan` when any name in `env.require` is unset;
  and
  `redact(text)` applied by every log line, alert body and recorded `reason`.
  Webhook URLs, proof bytes and env-var values are credentials. A test proves no
  secret can reach a ledger record, an alert payload or a log line.

### 5.1 `clock.py`, `sessions.py`, `cadence.py`

- `Clock(ABC)`: `now_ms()`, `monotonic()`, `sleep_until(epoch_ms, wake)` (returns
  early when `wake()` is true). `WallClock` (≤ 1 s sleep slices so a stop flag is
  honoured), `TestClock(set, advance)`, `ReplayClock` — both are `Clock`
  implementations composing one shared `ManualTime` value; `ReplayClock` is
  advanced by the replay feed and does NOT subclass `TestClock`, since the
  relationship is shared mechanism, not is-a. Invariant: nothing in the package compares wall stamps to order
  events.
- `Calendar(ABC)`: `is_open(ms)`, `next_open(after_ms)`, `next_close(after_ms)`.
  `AlwaysOpen`; `WeeklySessions` params `tz` (IANA, required), `sessions[]`
  (`days`, `open`, `close` as `HH:MM`), `holidays[]` (sorted unique
  `YYYY-MM-DD`), `special_closes[]`, `blackouts[]` (UTC ISO `from`/`until`),
  `after_open_s`, `before_close_s` (ints ≥ 0); a boundary in a DST gap refuses at
  validation. `EventWindow` (`start_ms`, `lead_ms`, `until_ms`); `Composite`
  (intersection). Windows for guards (`{"calendar": "session"}`) resolve through
  the same object.
- `Cadence(ABC)`: `next_tick(after_ms, calendar)`. `FixedInterval(period_ms ≥
  1000, anchor_ms)`, `AlignedBar(bar_ms ≥ 1000, publish_delay_ms ≥ 0)`,
  `AtTimes(times[], relative ∈ {open, close, clock})`, `OnData(poll_ms)`.
  `Overrun(policy ∈ {skip, coalesce, queue}, max_lag_ms)`; default `coalesce`;
  the tick record names absorbed ticks.

### 5.2 `feed.py`

`ServingContract{source_binding, entity_key_fields, event_time_field,
digest_recipe}` is returned by the entry class's pure
`serving_contract(params, verified_run_evidence)`. Four fields, and deliberately
**no** universe: the method is pure, pipeline-side and takes no document, so it
cannot know the required key set.

The required key set is the serve document's `document.serving.required_universe` — an
inline sorted key list, or a path to a JSON list — which `plan` reads,
canonicalises and binds into the `FeedSpec` inside the immutable
`ReleaseManifest` as `required_keys` + `required_keys_digest`; absence refuses
at `plan`. It never passes through `ServingContract`.

It is **not** read back from the run dir: `driver._release_spent` replaces spent
record streams with summaries (ADR-0048, `driver.py:747`), and `ObservationRows`
emits exactly such a stream, so neither the entry's rows nor the universe node's
recorded outputs survive a completed run. Declaring the universe in the document
and pinning it in the release is the only source that is both durable and
release-verified, and it keeps the rule that entity identity is never inferred
from dedupe `key_fields`.
`Feed(ABC).pull(tick_at_ms)` fetches through the contract's source binding and
returns acquisition/link status only. Core ships one implementation,
`EntrySourceFeed`, parameterised by `pull ∈ {acquire, store}` — `acquire`
calls `run_acquisition(..., mode="live")` through the onboarding root and
registry, `store` reads what a separate `watch` process fills and derives
staleness through `scan_stream` — rather than two near-identical classes. After the pull, the deferred entry executes
once and the same contract's `snapshot(entry_outputs)` produces
`EntryBatch{outputs, watermarks_by_key, required_keys_digest, coverage_digest,
data_asof_ms, inputs_digest, source_config_hash}`. Thus rows, key projection,
event time and per-key digests describe exactly the frozen snapshot descendants
receive, rather than a second feed read.
`FeedSpec{source_binding, entity_key_fields, event_time_field, digest_recipe,
required_keys, required_keys_digest, source_config_hash, source_config_version}`
is release-bound. The serve document may select `pull: acquire | store` but
cannot restate locator or coverage. Missing/duplicate/extra keys, malformed event
time, normalized-binding disagreement or source alias/hash/version drift refuses.
Zero new rows is valid only while every key is fresh; stale/dead derives from the
oldest watermark, while connector/link/identity failure is immediately dead.
`FEED_STATUSES = ("live", "degraded", "stale", "dead", "closed")` and
`LINK_STATES = ("connected", "recovering", "disconnected")`; the feed reports
the first, the executor the second, and D10's matrix reads both.
`ReplayFeed` uses the recorded contract and EntryBatch. Tests prove acquisition
and entry use one normalized binding and that snapshot metadata hashes the exact
entry output.

### 5.3 `decider.py` (+ the pipeline seam, §9.1)

- `serving_document(run_document, run_dir, heads, replay) -> PipelineDocument`:
  pure derivation over `document.expanded` (instances, never templates —
  ADR-0039 forbids a template pin): every node in `TRAINABLE_ROLES` with
  effective mode `train` becomes `mode: "load"` with `artifact:
  "<run_dir>/artifacts/<key>"`; search winners (from `nodes/NN-<key>.json`
  `winner`) are applied with the driver's own `_apply_param_override` rule and
  the search nodes are dropped; nodes of role `gate` / `stat_test` named in
  `serving.replay` are replaced by a `RecordedOutputs` node (§5.3 note) emitting
  the run's recorded outputs (refusing a summarised record); the document is cut
  to `ancestors(heads) ∪ heads`; `foreach` and `splits` are dropped (serving
  neither fits nor scores); a needed node carrying `$prev` refuses;
  `env`/`tracking`/`outputs` are dropped. The derived document's hash is
  recorded beside the run's hash on every `process` record.
- `Decider.prepare(release, registry, asof, base_run_dir)` re-verifies the release.
  A new structural planning pass resolves class metadata and graph edges without
  constructing/fingerprinting sources. `ServingExecutionPolicy` runs there,
  identifies and defers the sole `entry_read` before ordinary RESOLVE, and
  refuses any bypassing mutable source. Before construction it calls each
  class's pure `serving_effect(params, verified_run_evidence)`; the closed
  result and class fingerprint enter the release. Base/unannotated classes are
  `forbidden`, so policy never infers an effect from role, method names or
  document text. The base pass then constructs/executes
  only pure nodes or approved `release_read` nodes through `ReleaseReader`;
  it never calls the entry constructor, fingerprint, `data_edge`, split logic or
  run method. The entry must be a source root and dominate every dynamic path.
  `read_entry(tick_at_ms)` applies the one window override — setting the run
  document path `serving.entry.node`.`serving.entry.param` to
  `tick_at_ms - serving.entry.window_ms`, a path that must already exist in the
  run document —constructs and executes only the entry, then
  its `ServingContract.snapshot` creates the EntryBatch from those exact outputs.
  The loop validates source identity, exact coverage, every key's watermark and
  input digest before `evaluate(entry_batch)` executes only pure or
  capability-backed release descendants with the frozen entry binding. No second
  mutable snapshot can occur.
  The configured proposer first returns stable, unsized
  `Candidate{id, instrument, scope_keys}` values and extracts quotes from those
  immutable head outputs. Accounting takes the deduplicated union of the requirements the measures
  already returned per candidate scope key and snapshots against the quotes; only then does
  `Proposer.proposals(head_outputs, candidates, account_state, provenance)
  -> list[Proposal]`
  construct an ordered proposal list. A proposal must preserve its candidate's id and instrument, and the
  candidate's declared scope keys are what its limits are measured over; missing/extra/changed keys or duplicate ids refuse.
  Candidate/scope derivation is release-bound proposer code and cannot depend on
  mutable account state.
  Candidate, quote and proposal construction may not rerun a node.
- `Proposer(ABC)`: `candidates(head_outputs) -> list[Candidate]`,
  `proposals(head_outputs, candidates, state, provenance) -> list[Proposal]`
  where `provenance` is the frozen
  `Provenance{inputs_asof_ms, inputs_digest, coverage_digest, quote_asof_ms,
  quote_digest}` the tick built from the `EntryBatch` and `QuoteSet`. It is
  passed in rather than stamped on afterwards for the same reason
  `EvidenceRequirement` is born complete (§5.5): a child proposer that computed
  its own `inputs_digest` would silently disagree with the frozen batch, and
  D6 requires every proposal to bind that exact digest,
  `quotes(head_outputs) -> list[Quote]` is pure and state-independent (default:
  rows shaped like `dskit.pipeline.records.MarketRecord`, read through that
  module's accessors — not production's own `records.py`). `IntentRows` (`output`, `fields` map,
  `default_tif`), `TargetPositions` (`output`, `fields`, diff against
  `state.positions`).
- `RecordedOutputs` must satisfy the replaced role's planner rules. The resolved
  default is to replay full recorded gate/stat-test outputs; absent or summarised
  evidence refuses rather than recomputing a training-time verdict on live data.
- Refusals at `plan`: entry node absent; window param not in `_PARAMS` or its
  full path absent from the run document; `ServingContract` missing or `document.serving.required_universe` absent; entry not a source root or not dominating every dynamic path; any
  non-entry mutable read; a `release_read` outside the manifest; a trainable
  without an artifact dir; an artifact older
  than `max_artifact_age`; a needed search node without a recorded winner; a
  `$prev` reference; unsafe/unknown node effects; import or code-fingerprint
  failure; source, artifact, serving-document, run, or release mismatch.

### 5.3.1 `release.py`

`ReleaseReader` reaches a node as the `NodeContext.release_reader` field —
`None` for every ordinary run, set by the structural planner only for a node
it classified `release_read`. That is the single cross-package touch point,
and §9.1 lists it as a `node.py` change. It is
the only capability a `release_read` node receives, and
the one API the pipeline planner and production must agree on:
`get(name) -> value` returns the manifest-named artifact for `name`,
verifying its recorded digest before returning and raising
`ProductionError` when `name` is not in the manifest or the digest differs;
`names() -> tuple` lists what this node is permitted to read. There is no
path, no handle and no write verb, so a release read cannot reach the
filesystem or a mutable store. It is constructed per node from the
manifest, holds no open file, and is handed over by
`ExecutionPolicy.reader(key)` (§9.1).

`ReleaseManifest` is immutable canonical JSON containing `series_id`,
`doc_hash`, `run_hash`, `serving_hash`, every artifact digest/timestamp, every
resolved class/code and adapter fingerprint, derived `FeedSpec`, source-config,
graded expected `ExecutionScope`, approval-verifier and lease fingerprints,
and the D24 `RuntimeFingerprint`.
The runtime inventory is sorted/default-deny and computed without network I/O;
missing distribution metadata or a required lock digest refuses live. `plan`
resolves and verifies those inputs once, writes `release.json`, then
computes `release_hash = canonical_hash(manifest)`. `serve`, arming, process,
tick, decision, intent and adoption records all name that hash. Startup re-hashes
every local/runtime input; every tick and immediately-before-submit check repeats
content/runtime and artifact-age validation, while every pull
verifies source identity. A live expiry requires a new release and new ordinary
arming; replay uses its recorded clock and never refreshes an expired artifact.
Expiry/drift records a refusal and degrades health before action. Mutable paths
are never identity. Filesystem mtimes are never age authority: missing or
future-dated artifact timestamps refuse, and a timestamp change alters the
manifest.

### 5.4 `records.py` — value objects

All money/quantity/price fields are `Decimal` (strings in JSON); instants are
epoch-ms ints; each record has `to_obj`/`from_obj` (default-deny) and refuses a
non-finite number.

- `ExecutionScope{venue, account}`, a canonical non-secret ownership domain.
- `DuplicateRef` is a `reason` value, not an exception: a venue that rejects a
  re-used `client_ref` rather than returning the original order yields
  `Ack(status="rejected", reason="duplicate_ref")`, preserving the rule that
  `submit` always returns an `Ack`.
- `Quote{instrument, bid, ask, mid, asof_ms}`;
  `QuoteSet{quotes, quote_digest, min_asof_ms}`.
- `Candidate{id, instrument, scope_keys}`.
- `Proposal{id, instrument, side ∈ {buy, sell, none}, qty | notional, limit | None,
  tif ∈ TIFS, expires_ms, reference_price, exposure, direction, confidence,
  prediction, baseline, expected_value, inputs_asof_ms, inputs_digest,
  coverage_digest, quote_asof_ms, quote_digest, extra}`.
- `Finding{guard, measure, value, bound, window, scope_key, verdict, reason}`.
- `Intent{client_ref, decision_plan_id, decision_plan_digest, proposal,
  created_ms, authority_id, release_hash, inputs_asof_ms, inputs_digest,
  coverage_digest, quote_asof_ms, quote_digest, evidence_asof_ms,
  evidence_digest, risk_version, risk_state_digest}`. This is the sole canonical
  Intent type; ledger rows serialize it rather than define another schema.
- `STATUSES = ("pending", "open", "partial", "pending_cancel", "filled",
  "cancelled", "expired", "rejected", "replaced", "unknown", "not_sent")` —
  eleven, of which `TERMINAL_STATUSES = ("filled", "cancelled", "expired",
  "rejected", "replaced", "not_sent")`; a venue lacking a state collapses
  toward less certainty, never toward more.
  `TIFS = ("ioc", "fok", "gtc", "gtd", "day")`.
  `Ack{client_ref, venue_ref, status ∈ STATUSES, ts_ms, filled_qty, avg_price, fee,
  reason, native}`; `OrderState` = `Ack` + `instrument, side, qty, remaining_qty,
  limit, tif, created_ms, updated_ms` and must satisfy
  `filled_qty + remaining_qty == qty`; `Fill{fill_id, venue_ref, client_ref,
  instrument, side, qty, price, fee, fee_currency, liquidity ∈ {maker, taker,
  unknown}, status ∈ {pending, final, reversed}, ts_ms, native}`;
  `Position{instrument, qty, avg_cost, source ∈ {derived, venue}, native}`;
  `Balance{currency, total, available, native}`; `Settlement{instrument, outcome,
  qty, payout, fee, settled_ms, native}`.
- `Alert{fingerprint, severity ∈ SEVERITIES, status ∈ {firing, resolved}, summary,
  source, tick_id, at_ms, labels}`; `Verdict{status ∈ {ok, warn, alarm, insufficient},
  statistic, threshold, n_ref, n_cur, window, slice, provisional}`.
- `InputWatermark{key, latest_asof_ms, source_digest}`;
  `EntryBatch{outputs, watermarks_by_key, required_keys_digest, coverage_digest,
  data_asof_ms, inputs_digest, source_config_hash}` where `data_asof_ms` is the
  minimum watermark;
  `GateResult{gate, passed, reason, at_ms}` — one per checked gate, the element
  type of `DecisionPlan.gate_results[]`;
  `FeedResult{status, acq_id, records_added, source_config_hash, at_ms}`;
  `FeedAge{key, age_ms, watermark_ms}`;
  `ScopeVerdict{allowed, scope_key, reason}`;
  `PolicyRequest{operation, risk_effect, rung, breaker, health, readiness,
  authority, origin, pending_control}` — what `Rule.veto` receives;
  `TickStart{tick_id, tick_at_ms, release_hash}`;
  `TickResult{tick_id, status, data_asof_ms, coverage_digest, inputs_digest, decision_plan_ids, legs, findings,
  observed_at_ms, nav, latency_ms, leg_latency_ms, refusal_reason, error,
  feed{status, acq_id,
  records_added, source_config_hash, required_keys_digest, watermarks_by_key,
  coverage_digest}}` — the `feed` block is a member because §6's `tick` record
  requires all seven and five of them live only in `FeedResult`/`EntryBatch`,
  which are phase-local; the loop never sees an `EntryBatch` and so could not
  add them. Everything above is what the phases
  produce, so a phase never writes a record itself. The loop adds only the
  fields it alone holds — `tick_at`, `calendar`, `overrun_absorbed[]`,
  `health`, `breaker`, `rung` — when it writes §6's
  terminal `tick` and `decision`;
  `LegEvaluation{original, final, findings, gate_results, scope_verdict,
  account, risk_effect, risk_version, risk_state_digest}` — the frozen
  accumulator `LegPipeline`'s steps thread, and the sole input to `plan()`;
  every `DecisionPlan` field is one of its members, a `LegBindings` member, an
  id from `IdSource`, or step 4's own `result` — §5.16 is the authority on which.
  `DecisionPlan{plan_id, inputs_asof_ms, inputs_digest, coverage_digest,
  quote_asof_ms, quote_digest, evidence_asof_ms, evidence_digest,
  provenance_digests, original, final, findings, gate_results, scope_verdict,
  risk_effect, risk_version, risk_state_digest, result}`;
  `ReductionIntent{release_hash, request_id, index, candidate, proposal,
  risk_state_digest, expires_ms}` — `candidate` is a full `Candidate`
  (`id`, `instrument`, `scope_keys`) and is signed with the rest, because
  scope keys live on the candidate and not on the proposal: without it a
  reduction leg contributes nothing to `GuardChain.requirements`, accounting
  snapshots no evidence for its scope, and every guard refuses for missing
  evidence — the opposite of what the path exists to do. Signing it also means
  the maker approves the scope the limits will be measured over, not just the
  order. This is what a maker actually signs at
  `flatten-request` time, and the only thing that can be signed then: a full
  `Intent` names a `DecisionPlan` that will not exist until a later tick, an
  `authority_id` the checker has not yet issued, and input/quote digests that
  belong to a tick that has not run. `reduction_intent_digest =
  canonical_hash(ReductionIntent)` over exactly those seven fields in that
  order. **It is a different hash of a different object from `intent_digest`,
  and the two are never spelled the same way.** `reduction_intent_digest` is
  what the maker signs, what the single-use right names, what `authority_use`
  reserves, and what the flatten `client_ref` derives from; `intent_digest` is
  the hash of the full `Intent` the leg builds at step (5). `ActPermit` binds
  **both** — `intent_digest` always, and `reduction_right_digest` (the
  `reduction_intent_digest` of the right being consumed, `None` for a model
  leg) — because the verifier must recompute the first to prove the order is
  the one planned and match the second to prove the right authorises it. Step (5) builds the
  full `Intent` from it plus this leg's plan and bindings, so the venue
  receives an order whose economic content — instrument, side, quantity, limit
  — is byte-identical to what was signed, while its tick-local bindings are
  this tick's. That identity is the property D12 needs, and it is checkable:
  `test_leg.py` rebuilds the `ReductionIntent` using `release_hash` and
  `proposal` **from the constructed `Intent`** — `candidate` is not an `Intent`
  field and comes from `bindings.reduction.signed` with the other four — and `request_id`, `index`,
  `expires_ms` and `risk_state_digest` from `bindings.reduction.signed` — the
  last four are not `Intent` fields and cannot be recovered from one — then
  asserts `reduction_intent_digest` is unchanged. That pins **economic
  content** — the order that reaches the venue is the order signed. Scope is
  guaranteed structurally rather than by this check: the signed `candidate` is
  the leg's only source of scope keys, so there is nothing for it to diverge
  from.
  `ReductionIntent.risk_state_digest` is deliberately **not** re-verified at
  execution: positions move legitimately as earlier legs of the same plan
  fill, so requiring it to match would make every multi-intent plan fail after
  its first leg. It records the state the maker inspected, and
  `Accounting.classify` refusing anything that is not a proven reduction is
  what defends the gap — stated here rather than left as an unexamined
  binding.
  `ReductionPlan{release_hash, risk_state_digest, intents,
  reduction_intent_digests, expires_ms}` (its `intents` are `ReductionIntent`s);
  `ReductionAuthorization{authority_id, release_hash, request_id,
  reduction_intent_digests, expires_ms}`;
  `ActPermit{authority_id, decision_plan_digest, release_hash, intent_digest,
  client_ref, instrument, risk_effect, inputs_asof_ms, inputs_digest,
  coverage_digest, quote_asof_ms, quote_digest, evidence_asof_ms,
  evidence_digest, authority_scope_digest, reduction_right_digest,
  risk_version, risk_state_digest,
  readiness_digest, readiness_until_ms, lease_scope, fencing_token,
  safety_epoch_digest, valid_until_ms, checked_at_ms}`.
  `Permit` is a frozen dataclass base — deliberately NOT a seam ABC, so the
  §5.15 seam-ABC rule (every seam ABC has an abstract hook) does not reach
  it — sharing (`plan_id`,
  `decision_plan_digest`, `client_ref`, `valid_until_ms`); `SimulatedPermit`
  adds nothing outward-authorising and is what shadow/paper/recorded executors
  receive, while `ActPermit` above is the live binding. `SubmittingExecutor.submit`
  is typed against `Permit`, so every subclass accepts the base contract and only
  `LiveExecutor` narrows by refusing a non-`ActPermit` — a type refusal, not a
  stronger precondition on the shared signature.
  Both are defined only in `records.py`; only an `Authority` (§5.13.1)
  constructs an `ActPermit`.
  The safety epoch covers release/runtime, readiness, calendar, required-key
  coverage and watermark vector, input/quote/evidence/risk versions and digests,
  executor link/scope, health, breaker, rung, risk effect, authority, pending-control state and lease.
  Any change invalidates it. `valid_until_ms` is the nine-term minimum defined
  once in §5.13 step (6) and never restated here.
- `intent_digest = canonical_hash(Intent minus client_ref)` — `client_ref` is
  excluded because on the flatten path it derives from
  `reduction_intent_digest` and on the model path from the release/tick/leg
  tuple, so it is an identifier of the intent rather than part of it;
  `authority_id` **is** included, since two intents authorised under different
  arms must not hash alike. `decision_plan_digest =
  canonical_hash(DecisionPlan)` over its eighteen fields in declared order.
  Both sit here beside `requirement_digest` because §5.16 binds them and a
  digest without a stated recipe is not a binding.
  `EvidenceRequirement{measure, window_kind, window_arg, scope_key,
  window_start_ms, window_end_ms, baseline_at_ms, include_working}` — what a
  `Measure` declares it needs before sizing, and the unit accounting snapshots
  against. `requirement_digest = canonical_hash(EvidenceRequirement)` over
  exactly those fields in that order, with instants as epoch-ms ints and
  `window_arg` normalised (a duration to ms, a count to an int, a calendar
  window to its resolved `[start, end)` bounds) so two measures asking for the
  same evidence produce the same digest and accounting fetches it once.
  It carries no value: it is the question, `MeasureEvidence` is the answer.
- `MeasureEvidence{requirement_digest, value, sample_count, window_start_ms,
  window_end_ms, scope_key, effective_at_ms, known_at_ms, source_digests}`;
  `RiskVersion{economic_seq, executor_token, accounting_tokens}`;
  `AccountState{risk_version, asof_ms, evidence_digest, balances, positions,
  working, measure_evidence{requirement_digest: {scope_key: evidence}},
  source_digests}`. `risk_digest()` hashes canonical balances, positions,
  working orders and evidence values/window bounds/source digests but excludes
  observation-only timestamps; freshness is separately deadline-bound and every
  economic correction changes a source digest/version.

### 5.5 `guards.py`

- `Guard(ABC)`: class attribute `_PARAMS`; `validate_params` classmethod;
  `@abstractmethod check(proposal, state) -> Finding`, where `state` is the
  frozen `TickState` of §5.8.1 — a view, never the fold itself, so a guard
  cannot mutate what it judges, and `state.account` is its economic authority. Stale or incomplete accounting evidence refuses.
- `GuardChain(guards)`: `requirements(candidates, at_ms, calendar) ->
  tuple[EvidenceRequirement]` collects what every configured measure declares
  over every candidate scope key — passing each `Limit`'s `include_working`
  into its measure — and deduplicates by `requirement_digest` — this is the union `Accounting.snapshot`
  receives, and the chain is its only producer. `check_all` then evaluates
  every guard against the original proposal and
  records every finding. Verdicts use
  `allow < warn < amend < refuse < hold < halt`; amendments can only reduce one
  declared scalable field, compose by the strictest monotone reduction, and
  conflict by refusing. The final candidate is re-run through every hard guard
  with amendment disabled; any remaining breach refuses or halts. `hold` and `pause` append a
  `guard_state` record (§6) that `SeriesState` folds, so `resume_at`/`held_until`
  survive a restart — R9 names restart amnesia as the anti-pattern the fold
  exists to prevent, and a pause held only in a strategy object would be
  exactly that. A `hold` expires as `refuse` at its `ttl` (phase 2 adds an
  `approve` verb); `halt` trips the breaker.
- `Limit(Guard)`: `measure` (registered name or class ref), `window` ∈ `{}` |
  `{duration}` | `{count}` | `{calendar: session|day|event}`, `bound` (`max`
  and/or `min`, decimal strings or ints, inclusive), `warn_at ∈ (0,1)`, `scope`
  ∈ `aggregate | per_key | {group: field}`, `include_working` (default true),
  `on_breach ∈ {refuse, amend, pause, hold, halt}` (`amend` only for scalable
  measures; `pause` needs `pause: {duration | calendar}`; `hold` needs `hold:
  {ttl}`).
- `Measure(ABC)`: deterministic
  `requirements(candidate, window, scope_key, at_ms, calendar, include_working)
  -> tuple[EvidenceRequirement]` plus
  `value(proposal, state, window, scope_key) -> Decimal | float`.
  `at_ms` and `calendar` are what let the measure resolve a `{calendar}`
  window to the `[start, end)` bounds its own `requirement_digest` is computed
  over; without them the digest could not be formed and two measures asking
  the same question could not deduplicate. `include_working` is a `Limit` param passed *into* `requirements`, so the
  object is born complete and frozen with its digest computed in
  `__post_init__`. Nothing stamps a constructed requirement afterwards: an
  earlier draft had `Measure` build it and `Limit` mutate it, which made a
  frozen value object temporally coupled and let a child measure that computed
  its own digest silently fail to deduplicate. Every
  monetary or quantity measure returns `Decimal` (§5.4 admits no float in money);
  only dimensionless ratios — `bankroll_fraction`, `confidence` — may be
  `float`, and a test pins which registry entry is which.
  Proposal-local measures such as quantity return no evidence requirement;
  history/account-dependent measures declare every source value accounting must
  snapshot before sizing. A guard refuses if declared evidence is absent.
  Registry (stdlib): `quantity, notional, exposure, exposure_after,
  price_deviation, pnl, drawdown, consecutive_losses, decision_count,
  identical_count, direction_changes, open_orders, input_age_ms, feed_age_ms,
  confidence, bankroll_fraction, error_vs_realised`. A child's exposure formula
  is a `Measure` subclass referenced by path.
  A `Measure` answers a question about **one proposal against a snapshotted
  account state** at decision time. It is deliberately NOT the abstraction
  §5.10's operational monitors use, which answer questions about the **decision
  record stream** over a rolling window and receive neither a proposal nor an
  `AccountState`. Two hooks on one class would force every measure to implement
  a method most of them cannot answer; §5.15 records why these are two concepts
  rather than one.
- `RangeGuard(Guard)`: `field`, `min`, `max`, `nan ∈ {refuse, allow}`.
- Cancels never pass through the chain (a structural rule pinned by a test).
- After ordinary guards, `GuardChain.check_authority_scope` re-runs the active
  ordinary-arm or reduction-authorization allowlist and overlay against the exact
  final proposal immediately before permit.

### 5.6 `breaker.py` and `arming.py`

- `Breaker`: states `active | reducing | halted`; `trip(reason, actor)` from a
  guard `halt`, `feed.dead`, `executor.link_lost`, `reconcile.mismatch`,
  `operator`; `reduce(actor)`; `reset(actor, acknowledges_trip_id)` refused
  before `cooling_off_s` elapses or without a trip id; breaker state is the ledger fold;
  `breaker.json` is only a head-bound cache, validated against that fold before
  `READY` and rebuilt when it is behind; every transition is a `trip`
  record; the kill-switch file `HALT` in the serve root is polled by the
  independent control worker at subsecond cadence (§5.8) and re-checked at every
  tick boundary;
  on entering `halted` the loop cancels working orders when
  `document.execution.on_halt.cancel_open`
  and records the outcome vocabulary.
- `ArmingState{authority_id, release_hash, rung, maker, checker, armed_at_ms,
  armed_until_ms, allowlist, limits_overlay, request_proof_digest,
  approval_proof_digest}` is the frozen folded value, and `Arming(document,
  release)` is the service that reads it — the same value/service split
  `Readiness`/`ReadinessResult` and `Breaker`/breaker-state already use, and
  the one §5.15's "one concept, one class" rule requires. `Safety.arming` is
  the service.
  `ArmRequest{release_hash, rung, allowlist, limits_overlay, requested_until_ms,
  request_proof}` and `ArmApproval{request_digest, approval_proof}` are its
  inputs. `Arming.check_conjunction(invocation, view, origin, reduction, rung, at_ms)` is
  the single place D11's live conjunction is evaluated, and it is
  **origin-aware**. Four conjuncts apply to every origin: the document rung,
  `--armed`, `DSKIT_PRODUCTION_ARM` and the release hash must all agree. The
  fifth differs — a model leg requires a current unexpired **ordinary arm**; a
  reduction leg requires a current unexpired **unconsumed reduction right for
  its own `reduction.digest`** — the specific right, which is why the digest is
  an argument and not left as "some right is unconsumed" — and must not require an ordinary arm,
  because D10 and D12 both revoke ordinary arming on leaving `active` and
  forbid reissuing it while `reducing`. Demanding one there would refuse every
  live flatten leg — removing the emergency de-risking path at exactly the
  rungs it exists for. No caller re-derives any of this; §5.13 step (3) is the
  caller, and it passes `origin` rather than branching around the check. The
  conjunction applies at `live_limited` and `live` only: `rung` is an argument
  so the check itself decides that, rather than a caller testing the rung and
  skipping it, which D2 forbids outside `compose.py`. At `shadow`/`paper` it
  returns satisfied — no live permit exists to gate.
  `ApprovalVerifier(ABC).verify(canonical_bytes, proof, purpose) ->
  VerifiedPrincipal{id, proof_digest}`. It is resolved from the graded
  `document.arming.approval {uses, params}` object; params may name trust-root env vars
  but never contain secret material. Trust roots are public, content-digested
  inputs in the release; only private-key/service credentials stay secret.
  `deny-all` is the core shadow/paper default;
  a live plan requires a child class path. Construction resolves secrets once,
  performs no network I/O, validates trust-root shape, and its class/code/params
  fingerprint is release-bound. Maker and checker differ for ≥ `live_limited`;
  purposes are closed: `arm_request | arm_approval | reduce | flatten_request |
  flatten_approval | execute_flatten | resume | adopt`; role authorization is verifier-owned.
  expiry is mandatory and bounded by `document.arming.max_duration_s`; allowlists may only
  narrow and overlays must be provably stricter. `Arming` binds the release and
  proofs. `current` reads `SeriesState` (§5.8.1); `arming.json` is only a head-bound cache.
  Immediately before submit, an `Authority` (§5.13.1) applies the current scope
  from this fold to the exact intent and issues a bound `ActPermit` with input,
  quote, evidence and risk bindings; `arming.py` supplies the scope, not the
  permit. The
  `SubmissionVerifier` rechecks those bindings after the final barrier; the
  executor recomputes/compares the result.
  A reduction authority folds the full stored `ReductionPlan` plus the checker
  approval and grants one right for each unique intent digest. Before a reduction
  permit, the sole writer appends/barriers its unique `authority_use`; that
  reservation is never erased or reused. Recovery of a reserved intent reuses its
  deterministic client ref, queries first, and can append a replacement
  `authorization` with the current fence only after `proved_not_sent`; an
  ambiguous result halts the remaining plan. All control requests, approvals,
  authority issue/revocation/use, breaker transitions, adoption and command
  results have the closed schemas in §6 and are folded from the ledger, not from
  inbox files. Disarm, query, reconcile and cancel remain usable.

### 5.7 `executor.py`

The hierarchy is the §5.15 Liskov split: `Executor(ABC)` carries read, query and
cancel and is always constructible; `SubmittingExecutor(Executor)` adds
`submit(intent, permit, state)` with `permit` required of every subclass;
`ShadowExecutor`, `PaperExecutor` and `RecordedExecutor` take a
`SimulatedPermit`, `LiveExecutor` accepts only an `ActPermit` and refuses any
other permit **by type** — refuses meaning it returns
`Ack(not_sent, reason="permit_type")`, never raises. No subclass strengthens a precondition of its base.

- `Executor(ABC)`: `spec()` (default-deny knobs; secret knobs name env vars),
  `capabilities()` (`tifs`, `market_orders`, `notional`, `positions ∈ {venue,
  derived}`, `settlements`, `stream`, `dedupe ∈ {replays, rejects, window, none}`,
  `units {qty, price, cash}`,
  `position_model ∈ {netting, hedging}`,
  `fencing ∈ {none, submit_token}`); `@abstractmethod check(config)`,
  `execution_scope() -> ExecutionScope`,
  `cancel(ref) -> Ack`, `order(ref) -> OrderState`,
  `open_orders()`, `fills(since_ms, cursor=None)`, `balances()`; concrete
  `positions()` — the executor reports only what the venue says, and returns
  nothing when `capabilities().positions != "venue"`. Fill derivation is
  `PositionBook` (`apply(fill)`, `reverse(fill_id)`,
  `positions() -> tuple[Position]`, `net_qty(instrument)`) owned by
  `SeriesState` (§5.8.1), so ours and theirs are two clearly separate sides; a `positions: venue` child
  overrides `positions()` without inheriting dead derivation code —
  `settlements(since_ms)` (empty), `events()` (none), `venue_time_ms()` (`None` when the venue exposes no clock; a live document whose
  executor returns `None` must set `document.schedule.max_venue_skew_ms` to `null`
  explicitly, so an unmeasurable skew is a declared choice rather than a silently
  vacuous gate),
  `cancel_all()` (iterates only refs this executor owns).
- `ShadowExecutor`: records nothing itself; `submit` returns
  `Ack(status="not_sent", reason="shadow")`; any socket use raises (pinned by a
  monkeypatched test).
- `PaperExecutor`: fed `on_quote(Quote)` by the loop; knobs `fill_rule ∈ {touch,
  cross, mid}`, `slippage {bps, ticks, tick}`, `resting_rule ∈ {touch, through}`,
  `p_fill_on_touch`, `queue_frac`, `size_cap ∈ {none, quote_size, frac}`,
  `latency_ms {submit, cancel}`, `fees {kind ∈ none | per_unit | bps |
  maker_taker_bps | pxq_rate}` — `Fee(ABC).charge(qty, price, liquidity)
  -> Decimal`, one subclass per kind, registered in `FEE_KINDS` — TIF
  handling (`ioc`,
  `fok`, `gtd`; `day` refused without `session_end_ms`), `seed`, `partial_fills`.
  Deterministic under `seed`; no wall clock, no network. Every knob in this
  bullet is a graded `execution.params` field; §4.1 illustrates three of them
  and `validate_params` default-denies the rest into the same block.
- `RecordedExecutor`: replays the tape's acks/fills for replay parity.
- `LiveExecutor` can always construct its read/query/cancel channel. Its bounded,
  authenticated `execution_scope()` result must equal the graded document and
  release scope at startup, every tick and the final gate; disagreement refuses.
  Submission accepts only `ActPermit`. Its wrapper holds the act gate
  and delegates the indivisible local verify/call sequence to
  `SubmissionVerifier.verify_and_call`; the callback is
  `@abstractmethod _submit_native(intent, permit, timeout_ms)` — abstract, so
  core ships the wrapper and only a child's venue subclass is constructible. The gateway checks fencing,
  deadline and idempotency atomically. Ordinary or reduction authorities are
  never executor inputs. `submit` always RETURNS an `Ack` and never raises for
  a permission fact: a non-`ActPermit` is `Ack(not_sent, reason="permit_type")`,
  missing or stale authority is `Ack(not_sent, reason="not_armed")`,
  verification mismatch is `not_sent`, and a timeout or raise after possible
  I/O is `unknown`. `NotArmed` stays an internal exception inside the wrapper
  and never crosses the `SubmittingExecutor` contract, so no subclass raises
  where its base promises a value.
  Failures never disable reconciliation or cancellation.

- `executor_conformance_suite(cls, params, quotes)`: a pytest class builder (the
  node `conformance_suite` precedent) running the closed battery [R8 §2.5]:
  default-deny spec; `check` performs no submit; `client_ref` echoed; same
  `client_ref` twice ⇒ same `venue_ref` or `Ack(rejected, "duplicate_ref")`; terminal absorption;
  `filled_qty` monotone except reversed; `filled_qty + remaining_qty == qty`;
  capability gating before I/O; no duplicate `fill_id`; units pinned; derived
  vs venue positions agree; unarmed/raw authority refused; no initiated replace
  API; shadow has no network; paper deterministic; stale/mismatched
  input/quote/evidence/risk/intent/fence bindings refuse; timeout cannot exceed
  permit/lease lifetime and disables later sends; two instances prove a stale
  fencing token cannot act.

### 5.7.1 `accounting.py`

`Accounting(ABC)` has two abstract hooks.
`value(state_view, quotes, at_ms) -> Decimal | None` returns the marked
portfolio value that becomes `tick.nav` — `None` when a required mark is
missing, which is recorded rather than guessed. It is an `Accounting` hook
because `AccountState` carries no valuation and the loop must not compute a
second one. Single-currency accounts only: there is no FX seam in this ADR, so
a document whose `balances` span currencies must leave `nav` null.
`classify(proposal, state) -> risk_effect ∈ RISK_EFFECTS` is the one D10 means
by "accounting exclusively classifies a submit's risk effect" and D12 by "the
accounting strategy, not a model claim, must prove each proposal cannot
increase absolute exposure" — without it `DecisionPlan.risk_effect`,
`ActPermit.risk_effect` and `PolicyRequest.risk_effect` have no producer and a
child writing live accounting would never learn it must implement this.
`snapshot(state_view, executor, quotes, at_ms, requirements,
calendar) -> AccountState` (`state_view` is §5.8.1's frozen `StateView`) is independent of execution. `requirements` is the
deduplicated canonical union returned by every configured measure for every
candidate × window × scope, including all duration/count/session/day/event
boundaries, expanded over every candidate `scope_key`. Each required pair has one fresh
`MeasureEvidence`; missing evidence refuses. Implementations
fold timestamped fills, cash flows, marks and superseding corrections as-of the
tick, explicitly reversing busted fills, and include the starting baseline for
each window. Live snapshots also return monotonic executor/accounting source
tokens; absence, regression or reuse with changed contents refuses. Quotes must
satisfy `max_valuation_age_ms`. Core
`PaperAccounting` and `RecordedAccounting` are deterministic. Live requires a
child implementation; ambiguous conversion, stale evidence or unsupported
requirements refuse at plan/tick.

### 5.7.2 `coordination.py`

`Lease(ABC)` has `acquire(scope, holder, ttl_ms) -> LeasePermit`,
`renew(permit) -> LeasePermit`, `current(scope) -> LeasePermit | None`, and
`release(permit)`; `LeasePermit` contains scope, holder, monotonic
`fencing_token` and expiry. `coordination.scope` is the graded canonical
`ExecutionScope{venue, account}`, not a release id, so old and new releases
contend for the same ownership domain. The account id is opaque but not a
credential. `LiveExecutor.execution_scope()` obtains the authenticated actual
scope; startup, each tick and the final verifier require exact equality among
actual, document, release, lease and `ActPermit` scopes. Core `ProcessLease`
is valid only for shadow/paper. Every live plan resolves a child lease class,
acquires the declared scope before reconciliation, renews it independently of
ticks, and passes the current scope/token through `ActPermit`.
`LiveExecutor.capabilities().fencing` must be `submit_token` and the child
gateway must atomically reject stale tokens. Loss/renewal failure makes health
unhealthy and disables submit while preserving query/reconcile/cancel. Renewal
runs in a supervised worker with a bounded call timeout; the document requires
`ttl_ms > 2 * (renew_every_ms + renew_timeout_ms)`, and any missed renewal
deadline invalidates the local permit without waiting for nominal expiry.

### 5.8 `ledger.py` and `control.py`

- `Ledger(ABC)`: `append(record) -> seq`, `append_many`, `barrier()`,
  `scan(kind=None, since_seq=0)`, `head() -> (seq, hash)`, `verify()`,
  `snapshot(state)`, `latest_snapshot()`. Before assigning ledger fields,
  append computes `payload_digest` over caller-controlled canonical content,
  looks up the stable caller `id`, and returns the prior sequence only when that
  digest matches; a different payload refuses. It then assigns dense `seq`,
  `recorded_at_ms`, and `prev_hash`, and computes
  `hash = sha256(prev_hash + canonical(envelope − hash))`. Readers tolerate
  unknown fields and upcast `schema_version`.
- `JsonlLedger`: one serialised line per single `write()` on `O_APPEND`; `fsync ∈
  {every, batch:{n, ms}, none}` from `document.durability.fsync` (`none` legal only at
  `shadow`); `flock` for the
  process lifetime; torn-tail recovery and segment continuity; directory fsync
  on segment creation; never copytruncate. Rotation is
  `placement.rotate.by ∈ {size, day, process}` (with `max_bytes` for `size`)
  into `ledger.NNNN.jsonl`; a new segment carries the prior segment's final
  `seq` and `prev_hash` so the chain is continuous across files, and
  `verify()` returns `first_bad_seq | None`. The genesis `prev_hash` is 64
  zeros. No money field is ever a float in a record. `barrier()` flushes through fsync.
  Every `tick_start` before work, decision plan and intent before submit, and
  arming, breaker, authorization/use, reduction/reset or adoption transition crosses it regardless
  of batch policy. Only the process holding `serve.lock` may open the ledger for
  append; there is never a concurrent CLI ledger writer.
- `ControlInbox` (implemented in `control.py`) is the sole write path from a
  control CLI to a running serve process. The caller supplies a UUID operation
  `request_id` and reuses it only for retries; the CLI canonicalizes the command,
  stores its independent payload digest, and writes
  `commands/inbox/<request_id>.json` with exclusive create, fsyncs the file and
  directory, and returns success once durably queued. The loop consumes
  non-HALT commands before a tick or between completed legs, never between an
  intent and its outcome. Presence of a pending mutating command blocks the next
  pre-submit action gate until that command is applied or rejected. An
  `execute-flatten` command owns its sequential cycle against model ticks, while
  HALT and newly queued controls may still stop it between legs. The writer
  re-verifies proof/release/expiry,
  appends and barriers the
  resulting records as the sole ledger writer, then atomically moves the file to
  `commands/applied` or `commands/rejected`; replay after a crash is
  idempotent only when both `request_id` and payload digest match; reuse with a
  different payload refuses. If no serve process owns the
  lock, non-executing commands may acquire it and run the same `CommandProcessor`
  synchronously. `execute-flatten` requires an active ready loop, and is moved to `applied` when
  its cycle is *queued*, not when it completes — otherwise the pending-control
  gate of §5.8 would block the cycle's own first leg. The in-flight cycle's
  durable marker is its `authority_use` reservations.
- `halt` additionally creates the out-of-band `HALT` sentinel atomically
  before queueing its audit command. The loop and its independent control worker
  poll that sentinel at subsecond cadence; the worker only sets an in-process
  stop flag and never appends. Thus stopping does not depend on the
  decision loop, inbox health, or ledger availability; startup observes it before
  reconciliation/action. Later processing records the halt when possible.
- The file lock protects only processes sharing that filesystem. Every live plan
  requires the configured fenced child `Lease` from §5.7.2; a local lock or
  unfenced lease can never be configured as sufficient.
- `ServeRoot(root, series_id)` owns the layout below: it creates and validates
  `series.json`, hands out the paths, and is the only thing that knows the
  directory shape — no other module builds a serve path by concatenation.
- `Checkpoint` is an atomic cache of `release_hash, last_tick_at,
  last_completed_tick_at, pending[], positions_snapshot_at, schema_version`.
  Every cache also names its projected `head_seq/head_hash`: a verified
  ledger-ancestor cache is stale and rebuilt; an unknown/ahead/divergent head
  refuses. Crash between ledger barrier and cache replacement is therefore normal.
- Serve root layout:

```
<placement.ledger_root>/<series_id>/   stable serve-series root
├── series.json        immutable genesis binding; mismatch refuses
├── arming.json        optional head-bound cache; authority is the ledger fold
├── breaker.json       optional head-bound cache; authority is the ledger fold
├── checkpoint.json    optional head-bound cache; authority is the ledger fold
├── HALT               stable cross-release kill switch; absent = not halted by file
├── serve.lock         same-filesystem, cross-release writer lock
├── commands/
│   ├── inbox/         fsynced caller-UUID control requests with payload digests
│   ├── applied/       terminal accepted command receipts
│   └── rejected/      terminal refused command receipts (ledger remains authoritative)
├── heartbeat.json     file heartbeat (process_id + sequence + time + status)
├── ledger/            ledger.0001.jsonl … (one chain across releases)
└── releases/<release_hash>/
    ├── document.json  the serve document verbatim
    ├── release.json   immutable ReleaseManifest
    └── process-<id>/base/  base-pass run dir (config/plan/resolved/nodes)
```

### 5.8.1 `state.py` — the fold

R9's consensus decomposition has seven boxes, and box 3 (the state cache /
blotter) is a different object from box 7 (the append-only recorder). An earlier
draft of D1 merged them, which left `ledger_state` as an untyped argument in two
seam signatures and let position state be derived three separate ways. This
section names box 3.

- `SeriesState`: the single fold over the ledger and the sole owner of derived
  state. `apply(record)` folds one record, and `Ledger.append` calls it — the fold is
  never behind the chain, which is what makes a mid-tick snapshot meaningful; `snapshot() -> StateView` returns an
  immutable read-only view; `head() -> (seq, hash)` reports what it has folded.
  It owns positions (through `PositionBook`), working orders, pending client
  refs, the breaker state, the current arming, the current readiness result,
  held/paused guard state, monitor state, the pending-control set (which
  commands are queued and unconsumed), and the **reduction projection** — the
  current `ReductionAuthorization`, its per-digest rights, and which of them
  `authority_use` has already reserved. Nothing else folds the ledger:
  `Breaker.current`, `Arming.current`, `Readiness.current`, `Accounting`,
  `Guard`, `Reconciler` and startup recovery all read this object, and the
  head-bound JSON files remain caches of *its* projection.
- `StateView` is the frozen projection of the fold, and **only** of the fold:
  `positions`, `working`, `pending`, `balances`, `decision_history`, `breaker`,
  `arming`, `readiness`, `guard_holds`, `reduction`, `pending_control`,
  `risk_version`, `head_seq`, `head_hash`. `Accounting.snapshot` and `Reconciler.run` take it as their
  first argument (both previously written as an untyped `ledger_state`).
- `TickState{view, account, feed_status, feed_ages, calendar}` is what a
  `Guard` receives as `state` — one declared type. The tick assembles the
  first one; **each leg rebuilds it at step (2) from a fresh
  `SeriesState.snapshot()`**, because an earlier leg of the same tick can trip
  the breaker, take a hold, or add a working reservation, and a guard judging
  leg 3 against leg 1's fold is judging a state that no longer exists. It
  carries **no rung**: the permission ladder is the action matrix's axis
  (§5.14), no guard or measure in §5.5 needs it, and a child `Measure` is
  referenced by path — outside every in-tree AST test — so handing it the rung
  would put the one value D2 exists to keep out of decision code within reach
  of the one class the tests cannot see. The last three members are
  deliberately not in `StateView` because they are not folds: `feed_status`/`feed_ages` are *this* tick's fetch result (a folded
  feed age would report the previous completed tick's staleness, which is
  exactly the bug `input_age_ms` exists to catch) and `calendar` is an injected
  collaborator.
- **`AccountState` is the sole authority for economics inside a guard.**
  `TickState.view` and `TickState.account` overlap on positions, working orders
  and balances, and they are derived differently on purpose: the view is the
  fold at head, the account is the correction-aware snapshot with prior legs'
  reservations folded (§5.13 step 2). A `Measure` reads `state.account`; the
  view is there for provenance and for non-economic history. `vocab.ECONOMIC_
  ATTRS = ("positions", "working", "balances")` names the overlap and a test
  pins that no `Measure` reads any of them off `state.view` — `working` and
  `balances` matter as much as `positions`, since `exposure_after`,
  `open_orders` and `bankroll_fraction` are exactly the measures that would
  reach for the fold at head and miss prior legs' reservations.
  Both are frozen and setterless, so a guard cannot mutate what it judges.
- **One fold, not three.** `PositionBook` belongs to `SeriesState`, not to
  `Executor`: the executor reports what the venue says (`positions()` when
  `capabilities().positions == "venue"`, otherwise nothing) and `SeriesState`
  derives ours from folded fills, so `Reconciler` has exactly two sides to
  compare and it is unambiguous which is which. `Accounting` values that fold
  against quotes; it does not maintain a second one.
- `Recovery(ledger, state, id_source, executor)` lives here, not in `leg.py`
  (the `executor` is what `order(ref)` is called on — recovery cannot resolve
  an ambiguous client ref without it): it replays
  `SeriesState.apply` from the last `snapshot` record forward, then closes
  unmatched `tick_start`/`decision_plan`/`intent` records into terminal `tick`
  and `decision` records, queries any ambiguous client ref and never resends.
  It runs before the scheduler exists and has nothing to do with a leg, so
  restart amnesia is structurally impossible rather than a rule each subsystem
  remembers.

### 5.9 `reconcile.py`

`Reconciler.run(state_view, executor, scope) -> ReconReport{breaks[], status,
ours_digest, theirs_digest}` (only the reconciler holds both sides)
resolves every pending ref through `executor.order(ref)` and compares open
orders, balances and settlements; it compares fill-derived against venue
positions only when `capabilities().positions == "venue"`, since against a
`derived` executor that comparison is vacuous. Breaks are `timing | missing_in_ledger |
missing_at_venue | quantity | price | fee | state | settlement | cash`, with severity
`info | warn | block`. `document.reconcile.on_mismatch` is the automatic policy and admits only
`halt | refuse`; unknown venue
orders are `external`, never silently made ours.
A `cash` break is a balance delta no fill, settlement or fee explains — a
deposit or withdrawal. It is the one break class with a resolution other than
halt-or-refuse: `adopt` on a `cash` break appends a `cash_flow` record
carrying the **amount and timestamp as values**, not merely the delta digest
every other adoption records. That asymmetry is deliberate — a digest is
enough to prove what was adopted, but returns cannot be computed from a hash,
and this is the only moment the amount is knowable. `lookback_ms` bounds how far back fills and settlements are queried, since an
open-orders endpoint cannot distinguish missing from recently closed. It runs
before `READY` when `document.reconcile.on_start`, every `document.reconcile.every_s`
thereafter, and always appends a `recon` record without synthesising a
venue action. Adoption is a separate authenticated operator command naming the
break ids and release hash; after inspection it records the delta, crosses
`ledger.barrier()`, updates the fold, and immediately reconciles again.

### 5.10 `monitors.py`

- `Monitor(ABC)`: `_PARAMS`; `fit(reference)`; `@abstractmethod observe(record)`;
  `@abstractmethod verdict() -> Verdict`; `state()`/`restore(state)` (JSON-able,
  folded by `SeriesState` and durably carried by the §6 `snapshot` record;
  `Checkpoint` does not hold it).
- Strategies, each an ABC with one abstract hook —
  `Reference(ABC).sample() -> tuple` (the comparison population),
  `Chunker(ABC).chunks(records) -> Iterator[tuple]` (how observations are cut
  into windows), `Threshold(ABC).breached(statistic, n_ref, n_cur) -> bool`.
  `Response` is a closed vocabulary (`RESPONSES`), not a strategy object.
  `Reference` (`leading(n)`, `rolling(window)`, `snapshot(path)` — a
  saved `Profile`; phase 2 `run` over the run's predictions parquet via the
  parquet pack); `Chunker` (`count(n)`, `period(iso)`, `sliding(n, step)`);
  `Threshold` (`constant`, `reference_std(k)`, `alpha` — PSI benchmark
  `(1/n+1/m)·(B−1+z_α√(2(B−1)))` and the Kolmogorov series, both via
  `statistics.NormalDist`/`math`); `Response ∈ {log, warn, halt}` (phase 2:
  `fallback`, `rollback` as operator acts).
- Families (phase 1): `OperationalMonitor` → `Staleness`, `DecisionRate`,
  `Coverage` (the abstaining fraction), `LatencyPercentiles`, `RefusalCount` —
  each a subclass whose `observe(record)` reads named fields of the decision
  and tick records (`data_asof_ms`, `status`, `legs[].final`,
  `latency_ms`, `refusal_reason` respectively) and whose `verdict()` reduces
  its `Chunker`'s current window. These are subclasses rather than one
  parameterised class because each reads a different record field, which is
  the only thing that varies — there is no shared numeric parameter to lift;
  `StreamMonitor` → `PageHinkley`, `TrackingSignal`; `DistributionMonitor` →
  `PSI`, `KS` (bins from reference quantiles at `fit`). Phase 2: `DDM`, `ADWIN`,
  `JensenShannon`, `LInf`; `OutcomeMonitor` → `Calibration` (ECE), `Brier`
  (Murphy terms), `Skill` (BSS and Diebold–Mariano against the leg's stored
  `baseline`, reusing `dskit.pipeline` metrics/stats), `PredictionBias`;
  `ParityMonitor` — phase 2, defined with `replay` in §5.13's `report.py`
  bullet: it observes the replay divergence classes rather than a data
  statistic, which is why it is the one family with no phase-1 member.
- `Profile` value object (per-field count, missing, min, max, sum, sumsq,
  quantile bins, top-k) with `merge()`.
- Rules pinned by tests: below `min_n` never `ok`; last partial chunk never `ok`;
  outcome verdicts `provisional` until `label_coverage`; a fixed anchor AND a
  rolling reference when both are declared; `alarm` with `response: halt` trips
  the breaker.

### 5.11 `alerts.py` and `health.py`

- `AlertSink(ABC)`: construction validates configuration only.
  `@abstractmethod send(alert)`; `close()`. Core network sinks use transports
  with real socket deadlines. Each custom sink has one daemon worker; a timeout
  disables that sink without replacement, bounding a permanently stuck call to
  one thread. Kinds: `log`, `memory`, `email` and `webhook`, each taking
  `url_env` (the env-var NAME holding the endpoint — never the endpoint),
  `template` and `timeout_s`; a sink that names a var absent from
  `env.require` refuses at `plan`. Reachability is reported by supervised health probes,
  never constructor side effects.
- `AlertRouter`: fingerprint dedup; `group_wait_s` [0, 600] default 30;
  `repeat_interval_s` [60, 86400] default 14400; per-severity routes; token-bucket
  rate limit from `document.alerting.rate_limit{max_per_hour, burst}` (`critical` bypasses
  the limit, not dedup); a bounded
  `queue.Queue` consumed by one worker thread; `put_nowait` overflow and every
  sink exception swallowed and counted (`alert_sink_failures_total`,
  `alerts_suppressed_total{why}`); `status: resolved` emitted on recovery. Phase
  2: inhibition, silences, escalation, `ack`, sqlite state across restarts.
- `SEVERITIES = ("info", "warning", "error", "critical")` pinned to PagerDuty,
  OTel `SeverityNumber` (9/13/17/21), syslog (6/4/3/2), `logging` (20/30/40/50).
- `Health`: `starting → {ready | degraded | unhealthy} → stopping`; `HealthProbe(ABC)`
  (`name`, `scope ∈ {local, dependency}`, `timeout_s`, `@abstractmethod check() ->
  ProbeResult`, where `ProbeResult{ok, at_ms, detail}` is a frozen value object
  and a raise or a timeout is recorded as `ok=False` with the reason in
  `detail`); kinds `ledger-writable` (local), `executor-check` (dependency),
  `feed-age` (dependency); `failure_threshold`/`success_threshold` hysteresis;
  transitions (not levels) raise alerts; `unhealthy` stops acting AND
  heartbeating; `degraded` observes and refuses acts.
- `Heartbeat` has its own supervised worker and cadence independent of tick
  duration; each emission uses `process_id`, its own sequence and time—not a tick
  id. Emitters are `file` (atomic rewrite of `heartbeat.json`) and deadline-bound
  `url` (POST of `{process_id, sequence, at_ms, status}`; 2xx is success, any
  other result counts a failure and never blocks). `every_s` must be at least 1
  and no greater than the cadence period. Phase 2 adds a `systemd` emitter
  (`NOTIFY_SOCKET` datagrams: `READY=1`, `WATCHDOG=1`, `STATUS=`, `STOPPING=1`).
  `HeartbeatEmitter(ABC).emit(payload)` is the hook; core ships the two kinds
  above. The worker observes an
  atomic last-successful-tick monotonic stamp; when `dead_after_ms` elapses it
  transitions health to unhealthy and stops emitting, allowing the external
  dead-man to page. It is sent in `ready`, and in `degraded` only when `document.heartbeat.in_degraded`.
- `flock(LOCK_EX | LOCK_NB)` prevents a second process on the same filesystem;
  the §5.7.2 fenced lease covers other hosts. Signals wake within 1 s;
  `ticking` finishes the phase and never stops between act and record-outcome;
  `shutdown_grace_s` [1, 300] must be under the supervisor's grace.

### 5.11.1 `metrics.py`

Counters exist because §5.11 swallows sink failures and bounded-queue drops; a
swallowed failure that is not counted is invisible. The registry is the one
place a count lives.

- The declared name and label-value tables are `METRIC_NAMES` and
  `METRIC_LABEL_VALUES` in `vocab.py`, not in `metrics.py` — §5.0's rule that
  no closed set lives outside `vocab.py` admits no exception; `metrics.py`
  reads them.
- `Metrics`: `counter(name, labels=())`, `gauge(name, labels=())`,
  `histogram(name, labels=(), buckets=None)` each return a handle
  (`inc(n=1)` / `set(v)` / `observe(v)`). Names are declared at construction
  from a closed table; asking for an undeclared name raises `ProductionError`.
- Naming is Prometheus-shaped and pinned by a test: `snake_case`, base units in
  the suffix (`_seconds`, `_bytes`, `_total` for monotonic counters), no
  units elsewhere.
- **Label sets are closed.** A metric declares its label *names* and the
  permitted *values* per name. An undeclared name refuses at declaration; an
  undeclared value is dropped into the reserved value `other` and increments
  `metrics_label_cardinality_dropped_total` — never unbounded growth, never a
  raise on the hot path. `labels_max_cardinality` bounds the product.
- Values are process-local ints/floats, not `Decimal`: metrics are operational
  telemetry and never an input to a decision, a guard or a record. Nothing in
  `policy.py`, `guards.py` or `accounting.py` may read them.
- `flush()` appends one JSON object per tick to `<placement.log_dir>/
  metrics.jsonl` (`{at_ms, tick_id, metrics: {name: {labels: value}}}`) and is
  called by the loop after `observe`, outside every barrier. A flush failure is
  counted and swallowed like a sink failure; it can never fail a tick.
- The declared table for phase 1: `ticks_total{status}`,
  `tick_seconds{phase}`, `decisions_total{result}`, `proposals_total{verdict}`,
  `submits_total{rung, risk_effect, outcome}`, `refusals_total{reason}` (its values are `TICK_STATUSES` refusal members plus
  the guard verdict names — a closed set like every other label),
  `alert_sink_failures_total{sink}`, `alerts_suppressed_total{why}`,
  `monitor_verdicts_total{monitor, status}`, `recon_breaks_total{class}`,
  `ledger_append_seconds`, `metrics_label_cardinality_dropped_total`.
  §5.11's two counter names resolve here and nowhere else.
- Phase 3 `prometheus`/`opentelemetry` packs subscribe to this registry;
  `metrics.py` imports neither and knows nothing about them.

### 5.12 `resilience.py`

`Classifier(ABC)` (`classify(outcome) -> kind ∈ {ok, transient, throttled, fatal,
ambiguous}`; default HTTP classifier: code first, status second; 408/429/5xx and
connection faults retryable; other 4xx fatal); `Retry` (`max_attempts` [1,10]=3,
`base_s`=0.05, `throttle_base_s`=1.0, `cap_s` default 20.0 and bounded by
the imported `MAX_BACKOFF_S = 60.0`, `jitter ∈ {full, equal, none}`,
`retry_after ∈ {honor, ignore}` always capped, `retry_writes ∈
{never, idempotent_only}`, `budget{capacity 500, transient_cost 14,
throttle_cost 5, refund 1}`; `decide(attempt, kind, is_write) ∈ {retry, give_up,
reconcile}`; an ambiguous write never yields `retry`); `CircuitBreaker` (states
`closed | open | half_open | forced_open | metrics_only`; `trip`/`reset`; one per
scope; `min_calls`=5; `failure_rate`=0.5; `open_s`=30; business rejections not
counted); `RateLimiter` (token buckets per scope from
`document.resilience.limiter.{submit,cancel}{rate_per_s, burst, max_in_flight}`,
`max_in_flight` default 1 for writes, `reserved` (the cancel lane keeps its
capacity even when the submit lane is exhausted), `observe(headers)` capped by
`MAX_BACKOFF_S`). It has distinct submit
and cancel lanes: cancel capacity is reserved and has priority, but is still
bounded. `cancel_all` sends sequentially; transient/429 outcomes retry only
within configured attempts/deadline while honoring capped Retry-After, ambiguous
outcomes query then reconcile, and exhaustion records `unknown` and makes health
unhealthy rather than flooding the venue. `Transport(ABC)`
(`send(method, url, headers, body, timeout{connect_s, read_s}) -> (status, headers,
body)`; `UrllibTransport`; `None` timeout refused). All take injected `clock`,
`sleeper`, `rng`. Every knob above is read from the graded `resilience` section
of §4.1 — none is a code constant. Phase 2: `Signer(ABC)` with `HmacSigner`,
skew window, time probe.

R3's separate `Idempotency` write-ahead ledger is **deliberately not built**: its
job — a durable record written before I/O, keyed by a deterministic client
reference, refusing key reuse under a different payload — is already discharged
by the D13 barrier chain (`decision_plan` → `intent` → `authorization`, each
fsynced before `executor.submit`), the D20 client-ref derivation, and
`Ledger.append`'s `payload_digest` idempotency. A second store would be a second
source of truth for the same fact and could disagree with the ledger after a
crash. The venue-side half R3 also names — `dedupe ∈ {replays, rejects, window,
none}` — survives as an executor capability (§5.7), and `on_ambiguous` is not a
knob because D13 fixes the answer: query, never resend.

### 5.13 `loop.py`, `outcomes.py`, `report.py`, `readiness.py`

- `ServeLoop(document, release, schedule, data, decision, safety, execution,
  recording, observability)`: two values plus **seven** collaborator bundles,
  each a frozen dataclass validated at construction, rather than thirty
  positional arguments —
  `Schedule{clock, calendar, cadence, overrun}`, `Data{feed, decider}`,
  `Decision{guards, monitors}`,
  `Safety{breaker, arming, authorities, readiness, invocation, action_policy,
  transition_policy, submission_verifier}` — `invocation` is the frozen
  `Invocation{armed, env_release_hash, once, max_ticks}` that `__main__`
  builds from `--armed`, `DSKIT_PRODUCTION_ARM`, `--once` and `--max-ticks`;
  without it `Arming.check_conjunction` (§5.6) would be evaluated by an object
  that cannot see two of its three inputs, `Execution{executor, accounting, lease, resilience}`,
  `Recording{ledger, state, inbox, reconciler, checkpoint, journal_hook,
  id_source}` — `state` is the `SeriesState` of §5.8.1, and it is a bundle
  member because six declared APIs take a `state_view` and nothing else could
  supply one,
  `Observability{metrics, alerts, health, heartbeat}`. The seven bundle dataclasses live in their own
  `bundles.py`, ahead of both `leg` and `compose` in the §10 order, because
  `LegPipeline` takes six of them as constructor arguments and `compose` builds
  them — putting them in either module makes the order cyclic. D2's "no branch on mode"
  only holds if the policy objects, the control inbox and the reconciler arrive
  the same way the executor does; the bundles are what make that legible and are
  what `mode` composition (D2) selects. Lifecycle `init → locked → leased → reconciling → ready →
  {waiting ⇄ ticking} → stopping → stopped`, plus persisted `halted` and
  restartable `faulted`. `IdSource(ABC).next_tick_id(tick_at_ms)` / `.leg_id(tick_id, index)` /
  `.plan_id(tick_id, leg_index)` / `.client_ref(...)` allocate deterministic ids before a `tick_start`; core
  ships `ReleaseIdSource` (derives from release/tick/leg/attempt per D20) and
  `RecordedIdSource` (replays the tape's ids); `ledger.barrier()` completes before any tick work. Phases are
  `gate`, `verify_release` (content/runtime hashes plus artifact age), `fetch`,
  `read_entry`, `coverage` (exact required keys, per-key source and oldest
  watermark), `evaluate`, `candidates`, `quotes`, `account`, and
  `propose`. Model proposals are sorted by stable candidate id and never pre-authorized as a
  batch. A reduction cycle is its own tick: it carries only the plan's legs, in
  maker-approved `index` order, never interleaved with model legs — which is
  what makes D12's "execution stops on the first refusal" mean the plan's own
  order rather than a candidate-id sort. For each proposal the tick constructs a `LegPipeline` (§5.13.1) and runs it;
  the eight steps below are that class's methods, not prose inside the loop.
  Strictly sequentially:
  (1) run ordinary guards and any monotone amendment;
  (2) take a fresh `recording.state.snapshot()` — prior legs of this tick have
  appended and folded their own reservations and acks, and only a fresh fold
  carries them — then refresh the account snapshot against it, and re-run hard guards and authority scope without amendment;
  (3) immediately re-evaluate release/runtime, readiness GO, calendar, source
  coverage and every key's watermark age, quote age/digest, accounting evidence
  age/digest, venue skew against `document.schedule.max_venue_skew_ms`, link/scope,
  health, breaker, document rung, mutually
  exclusive risk effect, authority scope and lease—without rereading decision nodes;
  (4) append a `decision_plan` containing entry/head/candidate provenance,
  original/final proposals, every finding/gate, input/quote/evidence as-of and
  digests, risk effect/version/digest and scope verdict, then barrier it; a
  refusal terminalizes as `not_sent` without an intent;
  (5) append the canonical exact Intent serialized from that plan and barrier it;
  (6) with `view = recording.state.snapshot()` — a **fresh** fold, and then a
  refusal if any member the plan and intent already bound has moved since —
  `arming` and `readiness` for a model leg, and `readiness` plus the leg's own
  right in `view.reduction` for a reduction leg, since that is the authority a
  reduction bound. Freezing the whole view instead would be
  unsafe: `breaker`, `guard_holds`, `working` and `pending` are exactly the
  members an earlier leg of this same tick can change — an earlier leg's
  `halt` verdict trips the breaker, and a `reduce` consumed between legs moves
  it to `reducing` — and a later leg reading a stale `active` would mint a
  live permit the action matrix forbids. Consistency is what the *bound*
  members need; freshness is what the *decision* members need, and refusing on
  drift gives both. Then
  `breaker = safety.breaker.current(view)`,
  `permit = safety.authorities.for_origin(origin, breaker).mint(intent,
  plan, view)` — a table lookup on declared values, no rung test.
  `Authority(ABC).mint` is the seam that makes D2 structurally true
  here: `SimulatedAuthority` (shadow/paper) returns a `SimulatedPermit` and
  writes nothing; `LiveAuthority` derives an `ActPermit` from the same
  readiness/input/quote/evidence/risk versions and digests and appends the
  `authorization`; `ReductionAuthority` appends the `authority_use` first and
  then the `authorization`. The bundle carries whichever one the rung selected
  at construction (D2), so `loop.py` contains no `rung ==` to spell differently. `valid_until_ms` is the minimum of proposal expiry,
  oldest-input watermark + `document.schedule.max_staleness_ms`, oldest quote +
  `document.schedule.max_quote_age_ms`, accounting as-of +
  `document.accounting.max_valuation_age_ms`, readiness GO + `document.readiness.valid_for_s`,
  calendar close, authority expiry, lease expiry and
  `document.execution.submit_timeout_ms`.
  Whatever an authority mints, it barriers its records before any submit I/O;
  a `SimulatedAuthority` has none to write;
  (7) after that final barrier, the live wrapper holds the act gate and
  `SubmissionVerifier.verify_and_call` rehashes the frozen `EntryBatch` and
  checks its source identity/deadline without rereading rows; it refreshes
  quote/account/authority/executor-scope/lease versions, rechecks every hard gate,
  and synchronously invokes native I/O
  with the full permit and bounded timeout. Any mismatch is `not_sent`; possible
  post-I/O timeout is `unknown`. External venue changes after the last snapshot
  are an acknowledged non-atomic boundary handled by reconciliation;
  (8) record/fold the outcome before considering the next proposal.
  Before the decision-plan barrier, changed state may be fully re-evaluated and
  recorded. After it, any bound input, quote, evidence or risk version/digest
  change refuses the attempt. A refusal terminates that intent as
  `not_sent`; an ambiguous outcome stops all later legs until
  reconciliation. Thus cumulative exposure, working orders, position/message
  limits and group scopes include every earlier leg. Finally the loop
  `observe`s and writes `checkpoint` last.
  `Tick` is a concrete template-method class — not an ABC, because nothing
  subclasses it and the invariant it carries is the order `run` walks, not the
  abstractness of its steps. `run(tick_at_ms) ->
  TickResult` is **concrete and final**: it walks `vocab.TICK_PHASES` in order,
  times each one into `latency_ms`, and no subclass can reorder or skip a
  phase, which is what makes D23's fixed phase order and §6's pinned latency
  keys structural rather than advisory. The ten phase methods — `gate`,
  `verify_release`, `fetch`, `read_entry`, `coverage`, `evaluate`,
  `candidates`, `quotes`, `account`, `propose` — are concrete, overridable
  methods, and each takes and returns declared values rather than mutating
  `self`, so the dataflow between phases is part of the contract:
  `gate(tick_at_ms) -> GateResult` · `verify_release() -> None` (refuses) ·
  `fetch(tick_at_ms) -> FeedResult` · `read_entry(tick_at_ms) -> EntryBatch` ·
  `coverage(batch) -> tuple[FeedAge]` (refuses on any gap, and returns the
  per-key ages — `clock.now_ms()` minus each `EntryBatch.watermarks_by_key`
  entry — because `feed_age_ms` is a registered measure and nothing else
  computes them) · `evaluate(batch) -> (head_outputs, head_digest)` — the digest
  is returned, not reconstructed, because `head_outputs` never leaves the tick
  and `DecisionPlan.provenance_digests` requires it ·
  `candidates(head_outputs) -> tuple[Candidate]` ·
  `quotes(head_outputs) -> QuoteSet` ·
  `account(candidates, quotes, at_ms) -> (AccountState,
  tuple[EvidenceRequirement])` — the requirement union is returned, not
  discarded, because `LegPipeline.refresh` must call `Accounting.snapshot`
  with it and cannot rebuild it from one proposal ·
  `propose(head_outputs, candidates, account, provenance) -> tuple[Proposal]`.
  `run` threads those values; a phase holds no scratch state between calls.
  After `account`, `run` assembles the `TickState` every guard receives —
  `view` from `recording.state.snapshot()`, `account` from the `account`
  phase, `feed_status` from `fetch`'s `FeedResult`, `feed_ages` from
  `coverage`, `calendar` from `schedule` — and puts it in each leg's
  `LegBindings`. It is a tick product, not something a leg can reconstruct:
  two of its five members are this tick's fetch result and exist nowhere else.
  `Tick(document, release, schedule, data, decision, safety, execution,
  recording, observability, tick_id, reduction_cycle=None)` takes the same
  seven bundles the loop holds, the allocated tick id, and — for a reduction
  cycle only — the stored `ReductionPlan` and its authorization. That argument
  is how `execute-flatten`'s candidates reach `Tick.account`: `candidates`
  returns the proposer's candidates for a model tick and the plan's signed
  `ReductionIntent.candidate`s for a cycle, and `propose` likewise returns the
  plan's stored proposals. Without the parameter the contribution §5.13.1
  requires has no caller, and an implementer inventing the route is where the
  scope-key guarantee would be lost; it is constructed once per tick and owns
  no state between ticks, and core needs no subclass of it. There is deliberately
  **no** `ReplayTick`: replay is already the five-object swap D2 and D20 rest
  on (`ReplayClock`, `ReplayFeed`, `RecordedExecutor`, `RecordedAccounting`,
  `RecordedIdSource`), and a replay subclass would be a second mechanism for a
  variation the injected seams already express — and would contradict §5.15's
  ruling that the rungs differ only by which objects were injected.
  `ServeLoop` composes a `Tick`; nothing subclasses `ServeLoop` itself. Query,
  reconcile and cancel stay available in every rung/breaker/health state.
  A process crash cannot guarantee a `finally` write; startup folds unmatched
  plans/intents and `tick_start` records into terminal `failed` ticks and their
  decisions (the recovery itself is a `process` record with `recovered`, §6), preserving recorded findings without rerunning them, before
  scheduling new work. Thus every started tick eventually has exactly one terminal
  `tick` and one `decision`, with status `decided | skipped:closed |
  skipped:stale | skipped:skew | skipped:halted | skipped:degraded |
  skipped:no_coverage | refused | failed` and all findings. Exit codes are
  0 stopped · 1 error · 3 halted · 4 already running · 5 refused (readiness
  NO-GO, or a control verb refused because the series state forbids it).
  Root `CLAUDE.md` and `AGENTS.md` carry the same line: "Exit codes: **0** ran ·
  **3** halted at a NO-GO gate / `validate` gated `block` (a halt is a result) ·
  **1** error." That gives 3 three meanings at once — halted, a NO-GO gate, and
  a gated `block` — and this package must keep the first two apart: a
  breaker-halted series needs operator action and refuses submissions, while a
  readiness NO-GO means nothing is wrong and the checklist is simply not yet
  satisfied. 3 keeps halted, 5 takes the refusal, 4 is already-running;
  **this is a deliberate extension of the root convention, and §9.3 rewrites
  that line in both files rather than silently diverging from it.** `--once` runs one tick;
  `--max-ticks N` bounds completed ticks.
- `outcomes.py` (phase 2): join settlements (`executor.settlements`) and derived
  labels (strict forward as-of over the store via `scan_stream`) into `outcome`
  records with `known_at_ms`; supersede chain; `current_outcome(leg)`; as-of cut.
- `report.py` (phase 2): attribution per leg (surprise, implementation shortfall
  split pinned to sum, fill rate, markouts, closing-value skill), calibration
  (Brier + Murphy on exact stratification, ECE, BSS vs stored baseline),
  cumulative value and drawdown; the replay parity diff with
  `divergence ∈ {data, nondeterminism, version, guard, state, execution}`;
  markdown and JSON emitters (the `runs.py` pipe-escape rule reused).
- `readiness.py` (phase 1): `Readiness(document, release)` with
  `evaluate(at_ms) -> ReadinessResult{verdict, items, readiness_digest,
  evaluated_at_ms, valid_until_ms}` and `current(state_view, at_ms) -> ReadinessResult |
  None` — read from `StateView.readiness` (§5.8.1), never by folding the
  ledger again, and unexpired at `at_ms`, which the loop supplies from its
  injected clock as every other freshness check does. `Breaker.current` and
  `Arming.current` take the same `(state_view)` shape for the same reason. `readiness_digest = canonical_hash(release_hash, items)` where `items` is
  sorted by `item` and each contributes exactly
  `(item, required, evidence, waiver, passed)` in that order — the same
  "exactly those fields in that order" standard `requirement_digest` follows,
  because `ActPermit` and the safety epoch bind this value.
  The checklist is a JSON file (`item`, `required`, `evidence`,
  `waiver`) evaluated to GO / NO-GO; NO-GO exits 5. The evaluation is appended
  as a `readiness` record (§6) and barriered, so the GO is durable,
  release-bound and expiring (`evaluated_at_ms + document.readiness.valid_for_s`)
  rather than recomputed at submit time; the action matrix and `ActPermit`
  read that record. `ready` writes it the way §5.8 rules every control verb
  writes: queued through the durable inbox when a serve process holds the lock,
  or, when none does, by taking the lock itself and running the same
  `CommandProcessor` synchronously — the serving process remains the sole
  ledger writer either way. It journals like every other mutating verb. Every live rung requires a
  current GO record bound to the exact release before arming or submit. Unwaivable
  foundation items include release/runtime verification, executor conformance,
  authenticated execution-scope equality, clean startup reconciliation, fenced
  lease capability and required safety controls. Outcome evidence extends it in phase 2.

### 5.13.1 `leg.py` — the submission pipeline

The eight steps above carry every barrier, every reservation, the cumulative-risk
folding and the one place money leaves the process. They are a class, not a
procedure inside the loop.

- `LegPipeline(document, release, bindings, schedule, decision, safety,
  execution, recording, observability)` — `document` is present because step
  (3) enforces four document thresholds
  (`document.schedule.max_staleness_ms`, `document.schedule.max_quote_age_ms`,
  `document.schedule.max_venue_skew_ms`,
  `document.accounting.max_valuation_age_ms`) and §4.1 rules that code holds
  no threshold; `schedule` is present because the leg needs the injected
  clock: step (2) re-snapshots at an `at_ms`, step (3) checks watermark, quote,
  evidence and skew ages, `fold` reports `leg_latency_ms`, and `ActPermit`
  carries `checked_at_ms`/`valid_until_ms`. Reaching for `time.time()` instead
  would break the D20 replay parity that rests on the injected clock.
  It is a **concrete class**, not an ABC — the invariant is that `run` is final,
  not that the steps are abstract, and the tick must be able to construct it.
  `bindings` is the frozen
  `LegBindings{proposal, origin, entry_batch, head_digest, quotes, state,
  requirements, reduction, release, rung, tick_id, leg_id, leg_index}` —
  `state` is the `TickState` the tick assembled (which carries `account`,
  `calendar` and the readiness GO, so none is a separate member), and step (2)
  rebuilds it with the refreshed account before re-running the hard guards.
  `requirements` is the deduplicated `EvidenceRequirement` tuple
  `GuardChain.requirements` produced for the whole tick — the leg cannot
  recompute it, because that call needs every candidate and a leg holds one
  proposal, and step (2) cannot call `Accounting.snapshot` without it.
  `reduction` is `None` for a model leg and otherwise carries the signed
  `ReductionIntent`, its `reduction_intent_digest` and the single-use right being
  consumed; it is what
  makes "the digest the right names is the digest that reaches the venue"
  checkable rather than aspirational: it holds `signed` (the `ReductionIntent`,
  including its signed `candidate`), its `reduction_intent_digest`, and the right
  being consumed.
  **`execute-flatten` contributes each stored `ReductionIntent.candidate`
  through `Tick(reduction_cycle=…)`** (§5.13), so they are in the candidate set
  before `Tick.account` runs, so their scope keys
  are in the requirement union and their guards find the evidence they demand.
  No proposer runs for a stored plan — the candidate is signed, not derived,
  which is why it had to be a `ReductionIntent` field — without that a reduction leg
  refuses for missing evidence, which is the opposite of what the path is for.
  **A reduction leg's bindings are assembled by `execute-flatten`'s cycle, not
  by `propose`**: its proposal comes from the stored `ReductionPlan`, and its
  `entry_batch`, `head_digest` and `quotes` are those of the cycle tick itself
  (`Tick.run` assembles every leg's bindings, for both origins), since D12 sends stored intents through the same sequential
  pre-submit gates as model intents — `head_digest` is
  the head-output provenance §6 requires in `provenance_digests`, which the
  leg cannot otherwise see because `head_outputs` never leaves the tick. The tick assembles these once and every
  step rebinds against. `release` and `rung` are members because step (3)
  re-evaluates release/runtime and the document rung, and steps (4)–(5) bind
  `release_hash` into the `DecisionPlan` and `Intent`.
  `origin ∈ {model, reduction}` is where the leg's proposal comes from, and it
  is a declared value, not a mode. Both origins construct their `Intent` at
  step (5) — a pre-signed full `Intent` is impossible, since half its fields
  name a plan and an authority that do not exist at signing time (§5.4). What
  a reduction leg carries is the signed `ReductionIntent`, whose
  `reduction_intent_digest` the single-use right names; step (5) builds the
  full `Intent` around it, and the §5.4 rebuild check pins that the economic
  content and scope reaching the venue are what was signed.
  `run() -> LegResult` is **concrete and final** and walks `vocab.LEG_STEPS`, so
  "record before act" is enforced by the base rather than by convention inside
  eight methods. Each step returns what the next one and the records need,
  threaded through a frozen `LegEvaluation` accumulator rather than scratch on
  `self`:
  `guard() -> (Proposal, tuple[Finding])` — the possibly-amended final proposal ·
  `refresh(final, findings) -> (AccountState, ScopeVerdict, risk_effect,
  tuple[Finding])` — `risk_effect` here, from
  `execution.accounting.classify(final, state)`, because this is the step that
  holds both the final proposal and the refreshed account ·
  `rebind(account) -> tuple[GateResult]` (refuses on any changed
  version/digest) ·
  `plan(evaluation) -> DecisionPlan` (appends + barriers; `evaluation` carries
  the original and final proposals, every finding, the gate results and the
  scope verdict, which is how all eighteen `DecisionPlan` fields are reachable) ·
  `intent(plan) -> Intent` (appends + barriers; for `origin == reduction` it
  builds the `Intent` around `bindings.reduction.signed` and refuses if the
  rebuilt `reduction_intent_digest` does not match the right being consumed) ·
  `authorize(intent, plan) -> Permit` ·
  `act(intent, permit) -> Ack` ·
  `fold(intent, permit, ack, findings) -> LegResult{result, leg_id, plan_id,
  plan_digest, final, client_ref, intent, ack, findings, leg_latency_ms}`. `plan_id`,
  `plan_digest` and `final` are members because a guard refusal terminalizes
  without an `Intent` (step (4)), and §6's `decision.legs[]` and
  `decision_plan_ids[]` still have to be written for that leg — which is the
  most common non-trivial outcome, not an edge case.
  `vocab.LEG_STEPS` is those eight names in order;
  `vocab.LEG_LATENCY_BUCKETS` is the three §6 buckets, with the mapping
  `guard → (1)-(3)`, `authorize → (4)-(6)`, `act → (7)` pinned by a test and
  step (8) charged to the tick. The two tuples are separate because three
  step names and three bucket names collide while meaning different spans.
- `Authority(clock, calendar, arming, lease, health, executor, document,
  release, ledger, inbox)` — constructed only by `compose.py`, and it takes those
  ten because `ActPermit` binds them: `valid_until_ms` is the minimum over
  **all nine terms §5.13 step (6) lists** — stated once there and never
  restated, because an authority that dropped proposal expiry, input
  staleness, quote age, evidence age or readiness validity would mint a permit
  that outlives the data it binds; `lease_scope`/`fencing_token` come from the
  `Lease`; and `safety_epoch_digest` covers calendar, health, executor
  link/scope, rung and pending-control state. A permit cannot be minted from
  `(intent, plan, state_view)` alone, which is why the constructor is stated
  rather than left implicit. `inbox` is the tenth because the epoch covers pending-control state. That
  state has **one** owner: `ControlInbox` is the writer and `SeriesState`
  folds a `control_request` into `StateView.pending_control` when it is
  queued, so the fold is authoritative and the inbox files are its spool. The
  `Authority` reads the projection like everything else; `inbox` is in the
  constructor so a queued-but-unfolded command cannot be missed at the moment
  a permit is minted. §5.8's rule that a pending mutating command blocks the
  next pre-submit gate is enforced by `ActionPolicy`, which reads
  `PolicyRequest.pending_control`.
  `Authority.mint(intent, plan, state_view) -> Permit` is the seam D2 needs.
  `SimulatedAuthority` writes nothing and returns a `SimulatedPermit`;
  `LiveAuthority` mints an `ActPermit` and appends/barriers `authorization`;
  `ReductionAuthority` appends/barriers its single-use `authority_use` first,
  then the `authorization`. All three build the permit from `records.ActPermit`
  and apply the current scope through `arming.py`'s `Arming` fold — **minting
  lives here, and `arming.py` owns proofs, the fold and scope application
  only** — D10, D11 and D14 say the same, and Appendix A's tree matches.
- **The authority axis is `(rung, origin)`, not rung alone.** A reduction is
  selected by where the intent came from, and it is legal only while the breaker
  is `reducing` (D10, D12) — which is a state the running loop enters, not a
  property of the document. So `Safety` carries an `AuthorityTable`, not one
  object: `authorities.for_origin(origin, breaker)` returns the
  `SimulatedAuthority`/`LiveAuthority` for `model`; for `reduction` a
  `SimulatedAuthority` at `shadow`/`paper` (D10 permits proven reductions there,
  and no live permit exists to mint) and the `ReductionAuthority` at
  `live_limited`/`live`, and refuses a reduction outside `reducing` and any live
  authority the rung does not permit. `compose.py` builds that table from the
  rung; the leg reads it by `origin`. A table lookup keyed by a declared value
  is not the branch D2 forbids — what D2 forbids is the loop asking what rung it
  is, and neither the tick nor the leg does. There is deliberately **no**
  `AUTHORITY_KINDS`: an authority mints the object that authorises real money,
  so it is closed to core and must not be reachable through the document's
  `pkg.module:Class` doorway.
- `compose.py` is the composition root, and the one legal place a rung is read.
  `bundles_for(document, release, registry) -> (Schedule, Data, Decision,
  Safety, Execution, Recording, Observability)` holds the closed rung →
  {executor, accounting, authority, approval, coordination} table D2 rests on,
  refuses an incompatible combination at construction (`paper` can never select
  a `LiveExecutor`), and is the only module permitted to instantiate an
  `Authority` by name. `test_purity.py`'s AST ban on `rung ==` covers every
  module except this one, and `test_compose.py` enumerates the table — without
  a named owner the branch would simply relocate to `__main__.py`, which D2
  did not anticipate because it guarded a spelling rather than a place.
- `ServeLoop` is therefore the scheduler, not the composition root: lifecycle FSM,
  cadence/overrun, control-inbox consumption, `Tick` construction, monitor
  `observe`, metrics flush, checkpoint, journal row and exit code. It does not
  contain the submission sequence.

### 5.14 `policy.py` — the cross-cutting invariant matrix

`ActionPolicy.permits(request: PolicyRequest) -> PolicyDecision{allowed, reason}`
(one argument, so the eight-field request and the call cannot drift apart),
`TransitionPolicy.permits(from_state, to_state, cause, proof) ->
PolicyDecision{allowed, reason}` (named so it cannot be confused with the
`Decision` collaborator bundle in `loop.py`) and
`SubmissionVerifier(executor, accounting, lease, arming, guards, action_policy,
release, inbox, calendar, document, clock)` with
`verify_and_call(intent, permit, state, native_call) -> Ack`, where `state` is
the leg's step-(2) `TickState` — `LiveExecutor` receives it alongside the
permit, since `SubmittingExecutor.submit(intent, permit, state)` is the only
route it has. `release` because D24 re-verifies hashes, artifact age and the
runtime fingerprint immediately before submit; `action_policy` because the
gate rechecks policy and a `PolicyRequest` needs breaker, health and
readiness; `inbox` and `calendar` because `safety_epoch_digest` covers
pending-control state and the calendar, and a digest the permit binds must be
recomputable by whatever rechecks it — the same argument
that justifies the `Authority`'s ten collaborators applies here and was not made
before: a gate that refreshes quote, accounting, authority, executor identity and
lease, and rechecks deadlines, hard guards and policy, cannot do any of it from
`(intent, permit, native_call)`. `compose.py` builds it and hands it to both
`Safety` and the `LiveExecutor` wrapper, which is the one object held twice by
design. These three are
the sole owners of the following rules; callers cannot
duplicate or extend them by branching.

**How the matrix is actually represented.** The permission space is
4 rungs × 3 breaker × 5 health × 2 readiness × 4 operations × 3 risk effects
× 2 origins × authority — several thousand combinations — so it is *not* enumerated cell by
cell. It is an ordered tuple of named `Rule` objects, each of which may veto
with a reason (`Rule.veto(request: PolicyRequest) -> str | None`, §5.4), which is what R5 §2.4
actually recommends: keep the axes orthogonal and compose them. Two tests
give the audit value a hand-written matrix was reaching for without the
tautology of asserting the rules against themselves:

- **completeness** — every combination in the product is either vetoed by at
  least one *named* rule or allowed by an explicit allow-rule; a combination
  that falls through unclassified fails the test rather than defaulting;
- **a golden table** — the full decision table is generated from the rules and
  checked in, so any change shows the owner exactly which combinations moved.

This replaces an earlier draft that claimed to enumerate every cell
explicitly; that claim could not have been kept, and a hand-copied matrix
would have been a second copy of the same misconception.

- **External effects.** Only `query`, `reconcile`, `cancel` and `submit` exist;
  each submit has exactly one accounting-classified `risk_effect` (`increase`,
  `neutral`, or `reduce`). Query/reconcile/cancel follow their
  bounded priority lane; every submit follows proposal → complete decision plan
  → intent → authority/use → final verifier → native call. There is no public
  replace shortcut.
- **State and authority.** Every rung × health × breaker × readiness × operation × risk-effect × origin
cell — `origin` included, since origin-keyed rules would otherwise leave
combinations the completeness test never enumerates — and every
  breaker transition is explicit. Ordinary arms exist only in active/ready;
  reduction authority exists only in reducing; leaving active or resuming revokes
  ordinary arms; halted can enter reducing only through verified flatten.
- **Freshness.** Release/runtime, exact required-key coverage, the per-key
  watermark/source vector, quotes, accounting evidence, executor link, calendar,
  authority and lease each have a named version/digest and deadline. The plan,
  intent and permit bind them; the final verifier requires exact equality and
  rechecks deadlines.
- **Identity and ownership.** `series_id → release_hash → process/tick →`
  `decision_plan → intent → authorization → client_ref` is recorded, while the
  venue/account lease scope deliberately spans releases. Display names, wall
  time and ledger sequence never select storage or derive client refs.
- **Crash cuts.** Tests cut after every barrier and immediately before/after
  native I/O. Recovery folds durable state, terminalizes incomplete plans/ticks,
  queries an ambiguous client ref, never blindly resends, never reuses a consumed
  reduction right, and processes pending controls before another submission.

### 5.15 The four pillars — how this package is object-oriented

The owner's recorded design process names the OOP pillars alongside TDD and the
skeptic review. This section is the ruling on each, and `test_oop.py` enforces
the parts a test can reach. The governing rule is **one concept, one class,
parameterised — never a family of near-identical classes and never a `kind`
branch**.

**Abstraction.** Twenty registry-resolved seam ABCs carry the parts a
document selects by name — one per registry in §4.3, and a test pins the
two lists against each other: `Clock`, `Calendar`, `Cadence`,
`Feed`, `Proposer`, `Guard`, `Measure`, `Executor`, `Accounting`, `Lease`,
`Monitor`, `Reference`, `Chunker`, `Threshold`, `AlertSink`,
`HealthProbe`, `Transport`, `ApprovalVerifier`, `Fee`, `HeartbeatEmitter`.
Seven further ABCs are structural rather than registry-resolved, named here so
the count is not mistaken for the whole surface: `SubmittingExecutor` and the abstract
`LiveExecutor` wrapper (§5.7),
`Ledger` and `Classifier` (phase 1 ships one implementation each, so neither
has a `uses` site until `libs/sqlite.py` lands and adds `LEDGER_KINDS`),
`IdSource` (§5.13), `Authority` (§5.13.1 — closed to core, so deliberately
unregistered) and pipeline-side `ExecutionPolicy` (§9.1). `Tick` and
`LegPipeline` are concrete classes with final `run` methods, not ABCs: the
invariant each carries is the fixed order `run` walks, and making their steps
abstract would buy nothing while forcing a subclass that has no second
implementation. `Rule` is a concrete strategy in `policy.py`. `Permit` is a frozen dataclass base, not an ABC. Each seam ABC
declares its hooks
`@abstractmethod` so an incomplete subclass fails at construction, not at the
first live tick. The serve document names *what* ("`uses`: `weekly-sessions`"),
never *how*; no caller may instantiate a concrete class by name.

**Encapsulation.** Folded state has exactly one owner and no setter. Guards
receive a read-only `TickState` (§5.8.1) and cannot mutate it. `arming.json`,
`breaker.json` and `checkpoint.json` are private projections of the ledger fold,
never inputs — a divergent cache refuses rather than being trusted. Secrets live
behind `redact.py` and never cross a record boundary. `__all__` plus the `_`
prefix is the API contract; the closed matrices in `policy.py` are reachable only
through `ActionPolicy` / `TransitionPolicy` / `SubmissionVerifier`, so no caller
can re-derive a permission by branching.

**Inheritance — used for is-a, never for code sharing.** The real hierarchies
are `Limit(Guard)` and `RangeGuard(Guard)`; `ShadowExecutor` / `PaperExecutor` /
`RecordedExecutor` / `LiveExecutor` under `Executor`; the monitor families
(`OperationalMonitor`, `StreamMonitor`, `DistributionMonitor`) under `Monitor`
with concrete statistics beneath them; the sink and probe families; and
`Authority → {SimulatedAuthority, LiveAuthority, ReductionAuthority}`, which
is the seam that lets a leg mint a permit without asking its rung. `Tick` is
deliberately NOT a hierarchy: `run` is final and core ships one
implementation, because replay varies by injected objects rather than by
subclass (§5.13). Where the
relationship is has-a, the plan composes instead: `ServeLoop` **contains** its
clock, feed, executor and policies rather than subclassing anything, which is
why D2 can forbid a mode branch — swapping a rung swaps an object, not a code
path. `GuardChain`, `AlertRouter` and `Reconciler` are likewise composites.

**Polymorphism.** `ServeLoop` runs one code path at every rung: it calls
`executor.submit(...)`, `accounting.snapshot(...)`, `monitor.observe(...)` and
never asks what concrete class answered. Backtest / shadow / paper / live differ
only by which objects were injected — that symmetry *is* the replay-parity
guarantee in D20. What keeps it honest is that no rung-dependent decision is
left in the loop to branch on: the permit comes from an injected `Authority`
(§5.13.1) and the tape from injected clock/feed/executor, so the AST test is a
regression backstop rather than the mechanism. `Measure` is the reuse win: one `Limit` class over a
registry of measures replaces the dozen bespoke guard classes the surveyed
frameworks each grew.

**Liskov, explicitly.** A subclass may never strengthen a precondition, so
`submit` is **not** `submit(intent, permit=None)` with `LiveExecutor` demanding
more than its base. The hierarchy is split instead:

- `Executor(ABC)` — `spec`, `capabilities`, `check`, `execution_scope`, `order`,
  `open_orders`, `fills`, `balances`, `positions`, `settlements`, `cancel`,
  `cancel_all`. Read, query and cancel only; always constructible, never armed.
- `SubmittingExecutor(Executor)` — adds `submit(intent, permit, state)`, where `permit`
  is **required** for every subclass.
- `Permit` — a frozen dataclass base, not an ABC (§5.4) — with
  `SimulatedPermit` (shadow/paper: carries the decision-plan
  digest, authorises nothing outward) and `ActPermit` (live: the full §5.4
  binding). `LiveExecutor.submit` refuses any permit that is not an `ActPermit`
  by type, not by flag, and refusing means returning
  `Ack(not_sent, reason="permit_type")` — no subclass raises where its base
  returns a value.

The base contract is therefore total: **`submit` returns an `Ack` describing
what happened, including refusal**. That is what every subclass honours, and a
caller holding an `Executor` can always recover and cancel without knowing the
rung. Being precise about the residue: `LiveExecutor` handed a
`SimulatedPermit` returns `not_sent` where `PaperExecutor` would have
submitted, so behaviour still varies by subclass — the split removes the
*precondition* asymmetry (no subclass demands more than its base signature
promises) rather than making the two interchangeable, and the `Authority` seam
(§5.13.1) removes the caller-side asymmetry by minting the right permit type
at construction. Claiming more than that would be rhetoric. The
conformance battery runs against the base contract, so a child's venue subclass
is proven by the same suite that proves `PaperExecutor`.

**Two concepts that look like one.** A guard `Measure` and an operational
`Monitor` both produce a number from the running system, and an earlier draft
merged them into one registry. They are not one concept: a `Measure` takes
`(proposal, AccountState)` and answers at decision time about a single
candidate; a `Monitor` takes a stream of already-recorded decisions and
answers over a rolling window. Merging them forces a two-hook interface where
every implementer must stub the half it cannot answer — interface segregation
traded away for a smaller registry count. The "one concept, one class" rule
(§5.15 opening) is about one concept, and this is two; the test that pins the
ABC list against the registry list is what keeps the distinction visible.

**Where inheritance is deliberately refused.** `RecordedOutputs` substitutes for
a gate/stat_test node by satisfying that role's planner rules, not by subclassing
the node it replaces. Children never subclass `ServeLoop`, `GuardChain` or any
policy — the extension points are the ABCs above plus `Proposer` and `Measure`,
and `test_purity.py` fails a child that imports a private production name.

### 5.16 Producers — every field back to the object that fills it

Four rounds of design review found the same class of defect four times: a
sentence would name a collaborator that no bundle carried, or a record field
that nothing produced. Prose cannot catch that; a table can. This section is
the closure check, and `test_producers.py` asserts it mechanically — every
field named here resolves to a declared attribute of a declared object, and
every object named here appears in §8.

**The seven bundles, in full.** `compose.bundles_for(document, release,
registry)` returns exactly these, each a frozen dataclass validated at
construction:

| bundle | members |
|---|---|
| `Schedule` | `clock`, `calendar`, `cadence`, `overrun` |
| `Data` | `feed`, `decider` (the `Decider` owns the configured `Proposer`, which is how `Tick.candidates`/`quotes`/`propose` reach it) |
| `Decision` | `guards`, `monitors` |
| `Safety` | `breaker`, `arming`, `authorities`, `readiness`, `invocation`, `action_policy`, `transition_policy`, `submission_verifier` |
| `Execution` | `executor`, `accounting`, `lease`, `resilience` (the `Retry`/`CircuitBreaker`/`RateLimiter`/`Transport` set of §5.12, held as one `ResiliencePolicies` value) |
| `Recording` | `ledger`, `state`, `inbox`, `reconciler`, `checkpoint`, `journal_hook`, `id_source` |
| `Observability` | `metrics`, `alerts`, `health`, `heartbeat` |

`ServeLoop(document, release, schedule, data, decision, safety, execution,
recording, observability)`;
`Tick(document, release, schedule, data, decision, safety, execution,
recording, observability, tick_id)`;
`LegPipeline(document, release, bindings, schedule, decision, safety,
execution, recording, observability)`.

**A naming rule this table exists to enforce.** Nine names are simultaneously a
serve-document section and a bundle member — `accounting`, `arming`,
`feed`, `guards`, `health`, `heartbeat`, `monitors`, `readiness`,
`resilience` — plus `schedule` and `execution`, which name a bundle *type*.
That collision is what let a leg be specified to read
`schedule.max_venue_skew_ms` while holding a `Schedule` bundle with no such
member. Throughout §5 a document read is always written
`document.<section>.<key>` and a bundle read is always the bare member.
The spelling rule is enforced on the *package source*, not on this document:
`test_producers.py` fails a module that reads a document key without going
through the `ServeDocument` object. A shipped test must not depend on a
proposal file in `docs/`, which would make this document a permanent build
artifact of the package; `check_plan.py`, the authoring tool beside this document, applies the same rule
to the prose, and it is an authoring tool, not a test.

**`DecisionPlan` — all eighteen fields.**

| field | producer |
|---|---|
| `plan_id` | `recording.id_source.plan_id(tick_id, leg_index)` |
| `inputs_asof_ms`, `inputs_digest`, `coverage_digest` | `bindings.entry_batch` |
| `quote_asof_ms`, `quote_digest` | `bindings.quotes` (`QuoteSet`) |
| `evidence_asof_ms`, `evidence_digest` | `LegEvaluation.account` — the **refreshed** snapshot from step 2, never `bindings.state.account`, so the plan, intent and permit all bind one `AccountState` |
| `provenance_digests` | `bindings.entry_batch` + `bindings.head_digest` + `bindings.proposal.id` |
| `original` | `bindings.proposal` |
| `final` | `LegEvaluation.final` (step 1) |
| `findings` | `LegEvaluation.findings` (steps 1–2) |
| `gate_results` | `LegEvaluation.gate_results` (step 3) |
| `scope_verdict` | `LegEvaluation.scope_verdict` (step 2) |
| `risk_effect` | `LegEvaluation.risk_effect`, set by step 2 from `execution.accounting.classify(final, state)` |
| `risk_version`, `risk_state_digest` | `LegEvaluation.account` |
| `result` | step 4's own verdict |

**`LegResult` — every field, since §6's `decision.legs[]` is written from it.**

| field | producer |
|---|---|
| `result` | step 8's own verdict |
| `leg_id` | `bindings.leg_id` (allocated by `recording.id_source.leg_id`) |
| `plan_id`, `plan_digest`, `final` | the `DecisionPlan` from step 4 |
| `intent` | step 5 — `None` on a guard refusal, which is why the three above are members |
| `ack` | step 7 |
| `findings` | `LegEvaluation.findings` |
| `leg_latency_ms` | `run`, keyed by `LEG_LATENCY_BUCKETS` |
| `client_ref` | `recording.id_source.client_ref(...)`, allocated by `run` **before step (1)**, not at step 5 — a guard refusal terminalizes at step (4) and never reaches step (5), yet §6's `decision.legs[]` requires a `client_ref` for every leg. Ids derive from stable semantic inputs (D20), so allocating early costs nothing and is replay-identical |

**`ActPermit` — where the bindings that are not plain copies come from.** `valid_until_ms` the nine-term minimum of
§5.13 step (6); `checked_at_ms` from `schedule.clock`; `lease_scope`,
`fencing_token` from `execution.lease`; `readiness_digest`,
`readiness_until_ms` from `bindings.state.view.readiness`; `authority_id` and `authority_scope_digest` from the applied ordinary `Arming`
scope for a model leg, and from the `ReductionAuthorization` (its
`authority_id`, and a scope digest over its `reduction_intent_digests` under the
document limits) for a reduction leg — during `reducing` no ordinary arm exists,
so sourcing them from `arming` alone would leave both empty on the one path that
needs them;
`safety_epoch_digest` over release/runtime, readiness, calendar, coverage and
watermarks, input/quote/evidence/risk versions, executor link/scope, health,
breaker, rung, risk effect, authority and pending-control state — which is why
the `Authority` constructor takes ten collaborators and not three.
`intent_digest` is computed by the §5.4 recipe over the `Intent` the leg just
built; `reduction_right_digest` is copied from `bindings.reduction.digest`
(`None` for a model leg); `decision_plan_digest` likewise over the `DecisionPlan` from step
(4) — both are digests the permit binds, not fields it copies, which is why
they need recipes and not sources. `instrument` is
`intent.proposal.instrument`, one level down rather than a direct copy. Those
three plus the nine named above are the ones that are not a plain copy; every remaining field is copied from one of those two, and
`plan_id` is inherited from the `Permit` base. A count is deliberately not
written here — `test_producers.py`'s completeness assertion is what keeps the
row and `dataclasses.fields(ActPermit)` equal, and prose arithmetic in this
section has been wrong three times.

**`TickResult`.** `tick_id` from `recording.id_source`; `observed_at_ms` from `schedule.clock`; `nav` from `execution.accounting.value(view, quotes, at_ms)` during the
`account` phase (`null` when valuation was
unavailable — a recorded fact, not a gap, since an equity curve with a hole in
it must say so); `status`,
`refusal_reason`, `error` from whichever phase refused; `data_asof_ms`,
`coverage_digest`, `inputs_digest` and the seven-member `feed` block from
`fetch`'s `FeedResult` and `read_entry`'s `EntryBatch`, which is why `feed` is
a `TickResult` member and not something the loop adds; `decision_plan_ids`,
`legs`, `findings`, `leg_latency_ms` from the `LegResult`s; `latency_ms` from
`run`. The loop adds only what it alone holds — `tick_at`, `calendar`,
`overrun_absorbed[]`, `health`, `breaker`, `rung` — when it writes §6's `tick`.

**Input records — the half an earlier draft of this section missed.** Walking
only the records the leg *writes* is what let `LegPipeline` be specified to read
four document thresholds it had no route to, and `TickState` be required by every
guard while nothing produced one. The records a step *reads* are walked too:

| record | producer |
|---|---|
| `TickState{view, account, feed_status, feed_ages, calendar}` | assembled by `Tick.run` after the `account` phase (§5.13) and carried into each `LegBindings` |
| `LegBindings` (13 fields) | assembled by `Tick.run` per proposal; `proposal`/`origin` from `propose` (or from `execute-flatten`'s stored plan when `origin == reduction`), `entry_batch`/`head_digest` from `read_entry`/`evaluate`, `quotes` from `quotes`, `state` the `TickState`, `requirements` the second return of `account`, `reduction` `None` for a model leg and otherwise the signed `ReductionIntent` + digest + right, `release`/`rung` from the loop, `tick_id`/`leg_id`/`leg_index` from `recording.id_source` |
| `Intent` (16 fields) | step 5; `client_ref` from `recording.id_source`, `created_ms` from `schedule.clock`, `release_hash` from `bindings.release`, `decision_plan_id` from step 4 and `decision_plan_digest` by the §5.4 recipe over that plan, `proposal` the `LegEvaluation.final`, `authority_id` from `bindings.state.view.arming` for a model leg and from `bindings.reduction` for a reduction leg (`None` at shadow/paper, where no ordinary arm exists), `inputs_*`/`coverage_digest` from `bindings.entry_batch`, `quote_*` from `bindings.quotes`, and `evidence_*`/`risk_*` from `LegEvaluation.account` — the same one the plan binds |
| `PolicyRequest` (9 fields) | assembled by the caller of `ActionPolicy.permits`: `operation` at the call site, `risk_effect` from `LegEvaluation`, `rung`/`origin` from `bindings`, `breaker` from the **fresh** `recording.state.snapshot()` of steps (2)/(3)/(6) — never the tick-assembly view, or a trip raised inside this tick is invisible — `readiness` and `authority` from that same fresh view (`arming` for a model leg, `bindings.reduction` for a reduction leg), `health` from `observability.health`, `pending_control` from that same fresh view |
| `LegEvaluation` (9 fields) | steps 1–3, threaded (`risk_effect` from step 2's `execution.accounting.classify`) |
| `StateView` (14 fields) | `SeriesState.snapshot()` — every member is a projection of the fold and nothing else writes one |
| `Proposal` | `Data.decider`'s `Proposer.proposals(head_outputs, candidates, state, provenance)`. The economic fields — `instrument`, `side`, `qty`/`notional`, `limit`, `tif`, `reference_price`, `exposure`, `confidence`, `prediction`, `baseline`, `expected_value`, `direction`, `expires_ms`, `extra` — are the proposer's own decision, read from the head outputs through its field map (`intent-rows`) or its target diff (`target-positions`); `id` is the `Candidate.id` it must preserve; the five provenance fields come from the `Provenance` the tick passes in, never stamped on afterwards |
| `Provenance` (5 fields) | assembled by `Tick.run` from the `EntryBatch` (`read_entry`) and `QuoteSet` (`quotes`) |

**The rule this table encodes.** A record field with no producer, a collaborator
no bundle carries, or a call whose arguments no caller can supply, is a plan
defect and not an implementation detail to be settled later. `ServeRoot` is the
one deliberate exception: it is a construction-time dependency of `Ledger`,
`Breaker` and `ControlInbox` rather than a bundle member, and it is named here so
the exception is explicit rather than an oversight.

**`test_producers.py` is a real check, not a restatement.** The table above lands
in code as a module-level
`PRODUCERS: dict[tuple[str, str], str]` mapping `(record, field)` to a dotted
producer path, with a sentinel for the handful whose producer is a step verdict
rather than an attribute. Two assertions, and the second is what makes it bite:

- **resolution** — every dotted path resolves by `getattr` chain against the
  built classes, so a renamed collaborator fails here;
- **completeness** — for every record **this table walks** (the twelve above;
  extending the table extends the test, and that is the intended way to add
  one), `{field for (rec, field) in PRODUCERS if rec == R} ==
  {f.name for f in dataclasses.fields(R)}`. A field added to a record without a
  producer fails; a producer naming a field that no longer exists fails.

The completeness half is the half an earlier draft lacked. It catches
**record-field** holes — a field added without a producer, a producer naming a
field that no longer exists. It does **not** reach constructor arity or a
phase's return shape: those stay hand-reviewed, and two of them have been
missed that way already.

## 6. Ledger records

Envelope on every record: `kind, id, payload_digest, seq, series_id, process_id,
release_hash, recorded_at_ms, schema_version, prev_hash, hash`. Ledger-assigned
fields never enter `payload_digest`. `IdSource` derives tick, decision, leg and
model client ids from stable semantic inputs before append, independent of
sequence or wall time. Flatten refs are
`H("flatten-v1", release_hash, reduction_request_id, intent_index,
reduction_intent_digest)`; replay injects `RecordedIdSource`. Content uses the existing
sha256-canonical idiom.

| kind | one per | body |
|---|---|---|
| `process` | start / stop / recovered | `event ∈ {start, stop, recovered}`, `series_id`, `release_hash`, `doc_hash`, `serving_hash`, `run_hash`, `artifact_digests`, `source_config_hash`, `runtime_fingerprint`, `rung`, `executor_kind`, `code_version`; stop adds `exit_code`. After its barrier, one journal row is written whose `notes` render the process id and final head in the D22 `production-v1` form |
| `tick_start` | scheduled tick | `tick_id`, `tick_at_ms`, `release_hash` |
| `tick` | terminal tick | `tick_id`, `tick_at`, `data_asof_ms`, `observed_at_ms`, `status`, `feed{status, acq_id, records_added, source_config_hash, required_keys_digest, watermarks_by_key, coverage_digest}`, `inputs_digest`, `nav` (the marked portfolio value at this tick, single-currency only, `null` when a mark is missing or balances span currencies), `calendar`, `overrun_absorbed[]` (the tick instants this tick coalesced or skipped), `latency_ms{gate, verify_release, fetch, read_entry, coverage, evaluate, candidates, quotes, account, propose}` (one key per §5.13 `Tick` phase method, pinned by a test) and `leg_latency_ms` keyed by `vocab.LEG_LATENCY_BUCKETS` and summed over the tick's legs — `guard` spans §5.13.1 steps (1)–(3), `authorize` (4)–(6), `act` (7); step (8) records the outcome and is charged to the tick. These are spans over `LEG_STEPS`, not step names, which is why the two vocabularies are separate, `health`, `breaker`, `rung`, `refusal_reason`, `error{class, text}` |
| `decision` | tick (exactly one) | `tick_id`, `decision_plan_ids[]`, `decision_plan_digests[]`, `legs[]{leg_id, instrument, prediction, confidence, baseline, expected_value, reference_price, proposal, findings[], final, client_ref}` — `leg_id`, `findings[]`, `final` and `client_ref` are the leg's own; `proposal` carries the serialized final `Proposal`; every remaining field is copied from that `Proposal` (§5.4) under the same name — a no-op tick has `final: none` per leg or zero legs with `reason` |
| `decision_plan` | proposal after complete pre-submit evaluation (barrier before proposal submit) | `plan_id`, entry/head/candidate provenance, original/final proposal, input/quote/evidence as-of+digests, `findings[]`, `gate_results[]`, scope verdict, `risk_effect`, `risk_version`, `risk_state_digest`, `result ∈ {submit, not_sent}` |
| `intent` | proposal selected for possible submit | the canonical `records.Intent` value object; no second schema |
| `authorization` | permitted live intent (barrier before submit) | serialized `ActPermit` plus `authority_use_id` (null on the ordinary path); no raw authority is executable |
| `control_request` | durable CLI request | `request_id`, `purpose`, canonical payload (full reduction intents when applicable), `release_hash`, derived principal/proof digest, expiry |
| `control_approval` | maker-checker approval | `request_id`, `purpose`, checker principal/proof digest, verified payload digest |
| `authority` | issue / disarm / revoke / expire | `authority_id`, `kind ∈ {ordinary, reduction}`, request/approval ids, release/rung, expiry, allowlist/overlay, reduction intent digests, reason |
| `authority_use` | consume/reserve one reduction right (barrier before authorization) | unique `(authority_id, reduction_intent_digest)`, `client_ref`, `reserved_at_ms`; recovery may reference this reservation but no second intent may |
| `order_event` | executor/loop report | `client_ref`, `venue_ref`, `event ∈ {not_sent, ack, reject, fill, partial_fill, cancel, expire, replaced_by_venue, unknown, status}` — `replaced_by_venue` is observed only (D10); no executor verb initiates it, `status`, `venue_ts_ms`, `recv_at_ms`, `reason` |
| `fill` | execution | the `Fill` record |
| `cash_flow` | money entering or leaving the account other than by trading | `effective_at_ms`, `known_at_ms`, `supersedes` (bitemporal per D21, since a flow found by reconciliation days later has effective ≠ known and a wrongly adopted amount must be correctable rather than merely offset), `currency`, `amount` (signed `Decimal`), `kind ∈ CASH_FLOW_KINDS`, `external` (true for a deposit or withdrawal, false for an interest or fee accrual the venue applied), `source` is always `venue` (the amount is the reconciler's computed delta, never an operator-supplied number); `kind` and `external` come from the operator's proof and never default to `external: true`, `evidence` (the balance delta and recon break it explains, or the operator's note), `id = H("cash-flow-v1", release_hash, control_request_id, break_id)` so a crash-replayed `adopt` cannot append the same money twice. It is appended **before** the `adoption` record and inside the same barrier, so a crash between them cannot leave a break marked resolved with no amount recorded — **without this record an external deposit is indistinguishable from trading profit for the rest of the series' life**. `SeriesState.apply(cash_flow)` adjusts `StateView.balances` and advances `economic_seq` — it is a balance update, which D14 already counts as economic — so the fold carries both the money and the reason it moved, and the re-reconcile after adoption clears the break instead of reproducing it. Nothing downstream can separate them later, so it is recorded when it happens or never. **Every economic measure partitions the fold on `external`**: `pnl`, `drawdown`, `consecutive_losses` and `error_vs_realised` read trading records only and never see an external flow, while `bankroll_fraction` and `exposure` read the capital base *including* it. An external flow changes what you have, never what you earned — without that partition an adopted deposit would inflate a `pnl` halt guard into headroom, and `document.reconcile.lookback_ms` guarantees that any fill older than the window is classified `cash` and adoptable, so this is a routine mis-classification rather than an attack. `test_guards.py` pins that a `cash_flow` cannot move a `pnl` bound |
| `outcome` | label arrival / mark / correction | `leg_id`, `kind ∈ {settled, marked, voided, partial, corrected}`, `effective_at_ms`, `known_at_ms`, `value`, `weight`, `terminal`, `supersedes`, `source` |
| `guard_state` | a guard hold or pause | `guard`, `scope_key`, `kind ∈ {hold, pause}`, `reason`, `held_until_ms`, `resume_at_ms`, `finding` — folded by `SeriesState` like breaker and arming, so a restart cannot resume a paused strategy early |
| `readiness` | a `ready` evaluation | `release_hash`, `verdict ∈ READINESS_VERDICTS`, `items[]{item, required, evidence, waiver, passed}`, `readiness_digest`, `evaluated_at_ms`, `valid_until_ms` — the durable GO the action matrix reads and `ActPermit` binds; `ready` is the only verb that writes it |
| `recon` | reconciliation run | `scope`, `ours_digest`, `theirs_digest`, `breaks[]`, `status`, `action` |
| `trip` | breaker transition, including reduce/reset/halt | `from`, `to`, `reason`, `actor`, `control_request_id`, proof/principal digest, acknowledged trip id, `cancel_outcome` |
| `adoption` | authenticated venue-break adoption | `control_request_id`, principal/proof digest, named break ids, delta digest, before/after recon ids |
| `command_result` | consumed inbox request | `request_id`, `status ∈ {applied, rejected}`, emitted record ids, reason |
| `monitor` | verdict change / window close | `monitor`, `slice`, `window`, `statistic`, `threshold`, `status`, `provisional` |
| `alert` | firing / resolved | the `Alert` record + per-sink outcomes |
| `health` | transition | `from`, `to`, `cause`, `probe_evidence` |
| `snapshot` | every N records | `at_seq`, `state_digest`, `state` — **every `StateView` member** (positions, working orders, pending refs, balances, decision history, breaker, arming, readiness, guard holds, reduction projection, pending control, risk version) **plus monitor state**, which §5.10 requires the snapshot to carry and which is not a `StateView` member — dropping it would reset every drift window on restart, and a monitor below `min_n` cannot alarm until it refills. `risk_version`'s `executor_token`/`accounting_tokens` are live session tokens re-acquired on restart, not restored. Since `Recovery` replays `SeriesState.apply` from the last snapshot forward and cannot restore a member the snapshot never carried |

## 7. CLI — `python -m dskit.production`

| verb | does | exit |
|---|---|---|
| `validate <doc>` | shape and document identity | 0 / 1 |
| `plan <doc>` | derive/verify the serving document and emit the immutable release | 0 / 1 |
| `serve <doc> [--once] [--max-ticks N] [--armed]` | run the loop against the document's release | 0 / 1 / 3 / 4 / 5 |
| `arm-request <doc> --until TS --proof FILE [--allow I]…` | queue authenticated maker request for the document's rung | 0 / 1 / 5 |
| `approve-arm <doc> --request ID --proof FILE` / `disarm <doc>` | queue checker approval or safe demotion | 0 / 1 / 5 |
| `halt <doc> --reason` / `reduce <doc> --proof FILE` | set out-of-band halt and queue audit, or queue authenticated reducing transition | 0 / 1 / 5 |
| `flatten-request <doc> --plan FILE --proof FILE` / `approve-flatten <doc> --request ID --proof FILE` | queue maker-checker reduction plan/authorization | 0 / 1 / 5 |
| `execute-flatten <doc> --authorization ID --proof FILE` | queue authenticated execution of stored reduction intents by an active ready loop | 0 / 1 / 5 |
| `resume <doc> --acknowledge TRIP --proof FILE` | queue authenticated reset after cooling-off | 0 / 1 / 5 |
| `status <doc>` | rung, breaker, health, last tick, pending refs, control inbox/results, head hash | 0 / 1 |
| `verify <doc>` | walk the ledger chain; compare the head to the journal anchor | 0 / 1 |
| `reconcile <doc>` / `adopt <doc> --break ID… --proof FILE` | queue reconciliation, or queue authenticated adoption of named breaks | 0 / 1 / 5 |
| `replay <serve-dir>` | phase 2: parity report | 0 / 1 |
| `outcomes <doc>` / `report <doc> [--asof T]` | phase 2 | 0 / 1 |
| `ready <doc>` | release-bound readiness GO / NO-GO (required for live rungs) | 0 / 1 / 5 |

Only operational flags live on `serve` (`--once`, `--max-ticks`, `--armed`).
Adapter selection and every semantic knob live in the document. Authenticated
human acts use dedicated ledgered verbs; no CLI option silently changes semantics.
For mutating verbs, exit 0 means the request is durably queued or synchronously
applied, not that an asynchronously queued command has taken effect; `status`
shows its terminal receipt. Read-only verbs never take the writer lock.

## 8. Package structure — file by file

```
dskit/production/
├── __init__.py        public surface (curated re-exports only, no logic)
├── __main__.py        CLI: validate | plan | serve | arm-request | approve-arm | disarm | halt | reduce | flatten-request | approve-flatten | execute-flatten | resume | status | verify | reconcile | adopt | replay | outcomes | report | ready
├── base.py            ProductionError; checkers re-exported from dskit.assets.base; ms/utc helpers; canonical record hashing
├── vocab.py           EVERY closed vocabulary, one module: RUNGS, VERDICTS (lattice), STATUSES + TERMINAL, TIFS, SIDES, FILL_STATUSES,
│                      SEVERITIES (+ the pinned level map), HEALTH_STATES, BREAKER_STATES, LOOP_STATES, TICK_STATUSES, RECORD_KINDS,
│                      BREAK_CLASSES, BREAK_SEVERITIES, DIVERGENCE_CLASSES, MONITOR_STATUSES, RESPONSES, FEED_STATUSES, LINK_STATES,
│                      OUTCOME_KINDS, RISK_EFFECTS, OPERATIONS, APPROVAL_PURPOSES, ORDER_EVENTS, CANCEL_OUTCOMES, AUTHORITY_ROLES (ordinary|reduction, the record field),
│                      COMMAND_STATUSES, LIQUIDITY, POSITION_SOURCES, PROBE_SCOPES, EXIT_CODES, PULL_MODES, ALERT_STATUSES,
│                      PLAN_RESULTS, FSYNC_MODES, ROTATE_BY, ON_BREACH, LIMIT_SCOPES, NAN_POLICY, FILL_RULES, RESTING_RULES,
│                      SIZE_CAPS, FEE_KIND_NAMES, DEDUPE_MODES, POSITION_MODELS, FENCING_MODES,
│                      RESILIENCE_OUTCOMES (ok|transient|throttled|fatal|ambiguous — distinct from OUTCOME_KINDS),
│                      RETRY_DECISIONS, JITTER_MODES, RETRY_AFTER_MODES, RETRY_WRITE_MODES, OVERRUN_POLICIES,
│                      TICK_PHASES (the ten Tick method names, in order), LEG_STEPS (the eight LegPipeline method
│                      names, in order), LEG_LATENCY_BUCKETS (guard|authorize|act, the §6 spans),
│                      WINDOW_KINDS (none|duration|count|calendar), CALENDAR_WINDOWS (session|day|event),
│                      LEG_ORIGINS (model|reduction), GUARD_STATE_KINDS (hold|pause), PROCESS_EVENTS (start|stop|recovered),
│                      ECONOMIC_ATTRS (positions|working|balances),
│                      CASH_FLOW_KINDS (deposit|withdrawal|adjustment — the only kinds `adopt` can emit; `interest` and `fee` would need a venue-reported cash-flow producer this ADR does not add),
│                      READINESS_VERDICTS (go|no_go), METRIC_NAMES + METRIC_LABEL_VALUES (§5.11.1's tables live here,
│                      not in metrics.py), BREAK_ORIGINS (ours|external),
│                      AT_TIMES_RELATIVE, CALENDAR_WINDOWS, ON_MISMATCH, RECON_ACTIONS, TRIP_REASONS,
│                      CIRCUIT_STATES (closed|open|half_open|forced_open|metrics_only — distinct from BREAKER_STATES)
├── document.py        ServeDocument with required series/rung, Accounting, Arming, Coordination; default-deny; identity paths
├── release.py         ReleaseManifest; ReleaseReader (the capability handed to release_read nodes); class/code/adapter/
│                      source/artifact/runtime fingerprints; release verification
├── records.py         Quote, Candidate, Proposal, Finding, InputWatermark, EntryBatch, TickStart, DecisionPlan, ReductionPlan,
│                      ReductionIntent, ReductionAuthorization, EvidenceRequirement + MeasureEvidence, RiskVersion,
│                      AccountState,
│                      QuoteSet, GateResult, FeedResult, FeedAge, ScopeVerdict, PolicyRequest, Provenance;
│                      LeasePermit is coordination.py's, not here; Permit (frozen dataclass base) + SimulatedPermit + ActPermit, TickResult, Intent,
│                      execution/monitoring values
├── clock.py           Clock ABC; ManualTime (the settable instant TestClock and ReplayClock each compose);
│                      WallClock, TestClock, ReplayClock; CLOCK_KINDS
├── sessions.py        Calendar ABC; AlwaysOpen, WeeklySessions, EventWindow, Composite; CALENDAR_KINDS
├── cadence.py         Cadence ABC; FixedInterval, AlignedBar, AtTimes, OnData; Overrun; CADENCE_KINDS
├── control.py         ControlInbox (the atomic request/result spool, testable alone); CommandProcessor, which owns no
│                      verb logic and dispatches to handlers injected by compose.py — which is why the spool can be
│                      built at its place in the §10 order while breaker/arming/reconcile/readiness come later
├── feed.py            Feed ABC; ServingContract + FeedSpec; EntrySourceFeed; snapshot coverage/digests; ReplayFeed; FEED_KINDS
├── decider.py         serving_document(); Decider (base pass + per-tick rerun via SubgraphRunner); ServingExecutionPolicy
│                      (implements pipeline ExecutionPolicy); Proposer ABC; IntentRows,
│                      TargetPositions; RecordedOutputs (replayed gate / stat_test); PROPOSER_KINDS
├── guards.py          Guard ABC; Finding lattice; GuardChain; Limit; RangeGuard; Measure ABC + MEASURE_KINDS; windows; GUARD_KINDS
├── breaker.py         Breaker (active | reducing | halted), persisted; trips; kill-switch file; cooling-off
├── arming.py          authenticated proofs; ApprovalVerifier ABC + APPROVAL_KINDS; Arming value object + fold;
│                      scope application (minting lives in leg.py); NotArmed (internal)
├── executor.py        Executor ABC (read/query/cancel); SubmittingExecutor(Executor) adds submit(intent, Permit);
│                      fee strategies + FEE_KINDS;
│                      LiveExecutor; ShadowExecutor; PaperExecutor (+ fill/fee
│                      strategies); RecordedExecutor; executor_conformance_suite; EXECUTOR_KINDS
├── accounting.py      Accounting ABC; PaperAccounting; RecordedAccounting; ACCOUNTING_KINDS
├── coordination.py    Lease ABC; ProcessLease (non-live); LeasePermit; LEASE_KINDS
├── policy.py          ActionPolicy; TransitionPolicy; Rule + the named rule sets; the golden decision table
├── verifier.py        SubmissionVerifier (the final verify-and-call gate) — separate from policy.py because it performs
│                      I/O against feed/quote/accounting/lease state, while the policies are pure over closed vocabularies
├── resilience.py      Classifier ABC + HttpClassifier; Retry (+ budget); CircuitBreaker; RateLimiter;
│                      Transport ABC + UrllibTransport + TRANSPORT_KINDS
├── ledger.py          Ledger ABC + barrier; JsonlLedger; Checkpoint caches; ServeRoot + series genesis; envelope + chain + verify
├── state.py           SeriesState (the sole ledger fold); StateView; TickState; PositionBook; Recovery
├── reconcile.py       Reconciler; ReconReport; break classification; on_mismatch policy
├── monitors.py        Monitor ABC; Reference / Chunker / Threshold strategies (Response is a vocabulary); Operational,
│                      Stream, Distribution
│                      families (phase 2 adds Outcome and Parity); MONITOR_KINDS, REFERENCE_KINDS, CHUNKER_KINDS,
│                      THRESHOLD_KINDS
├── alerts.py          AlertSink ABC; LogSink, MemorySink, EmailSink, WebhookSink; AlertRouter; ALERT_SINK_KINDS
├── health.py          Health state machine; HealthProbe ABC + ProbeResult + PROBE_KINDS; HeartbeatEmitter ABC +
│                      emitters + HEARTBEAT_KINDS;
│                      single-instance lock; signal handling
├── metrics.py         Registry (counter/gauge/histogram); Prometheus naming + base units; closed label sets — an undeclared
│                      label VALUE drops to the reserved `other` and is counted, never raised on the hot path, while an
│                      undeclared label NAME refuses at declaration; JSONL flush per tick. Owns every `*_total` name §5.11 emits.
│                      Phase 3 prometheus/otel packs subscribe to it; nothing here imports either.
├── redact.py          Secrets resolution via dskit.pipeline.env.load_env; redact(text) applied to every log line, alert body
│                      and recorded `reason`; webhook URLs and proofs are credentials. No secret ever reaches a ledger record.
├── loop.py            ServeLoop (the scheduler; NOT the composition root); Tick (concrete, final run() walking TICK_PHASES,
│                      ten overridable phase methods); lifecycle states; exit codes
├── ids.py             IdSource ABC + ReleaseIdSource + RecordedIdSource — its own module because Recording holds an
│                      id_source, so compose.py would otherwise have to import loop.py while loop.py imports compose.py
├── leg.py             LegPipeline (concrete; final run() walking LEG_STEPS, eight step methods); LegBindings;
│                      LegEvaluation; LegResult; Authority ABC + SimulatedAuthority + LiveAuthority +
│                      ReductionAuthority (closed to core, no registry); ActPermit minting
├── bundles.py         the seven frozen collaborator dataclasses (Schedule, Data, Decision, Safety, Execution,
│                      Recording, Observability) — their own module because LegPipeline takes six of them as
│                      constructor arguments and is built before compose
├── compose.py         AuthorityTable; bundles_for(): the closed rung → collaborator table; the one module
│                      that may read a rung
├── outcomes.py        [phase 2] outcome join (settlements, strict forward as-of), supersede chain, as-of cut
├── report.py          [phase 2] attribution, calibration, drawdown, replay parity diff, markdown/JSON emitters
├── readiness.py       Readiness(document, release); ReadinessResult; readiness_digest; release-bound checklist →
│                      GO / NO-GO; required for live
├── libs/
│   ├── __init__.py
│   ├── sqlite.py      [phase 2] SqliteLedger (WAL + synchronous=FULL pinned; append-only triggers)
│   └── parquet.py     [phase 2] RunReference over the run's predictions parquet (pyarrow inside the method)
│                      (sqlite.py also introduces LEDGER_KINDS, the registry `Ledger` has no need of until then)
├── README.md          what it does, how to write a serve document, how to build an executor / proposer / measure / guard / monitor / sink; tree
├── AGENTS.md          package-scoped design, safety and testing instructions; tree
└── CLAUDE.md          conventions, extension points, gotchas; tree

tests/production/
├── conftest.py            a synthetic training run (the pipeline's synthetic kinds + an observations-reading data node over a temp
│                          onboarding root), a TestClock, a MemorySink — every test builds on these, no network anywhere
├── test_purity.py         static + behavioural: stdlib + dskit.pipeline + dskit.onboarding + dskit.assets + self; journal function-import only;
│                          no `mode ==` / `rung ==` branch in any module except compose.py
├── test_producers.py      PRODUCERS resolves by getattr chain against the built classes; and for every dataclass in
│                          every record §5.16 walks, PRODUCERS keys equal dataclasses.fields() exactly — the
│                          completeness half, which fails on a field added without a producer; plus: no module reads a
│                          document key except through ServeDocument
├── test_oop.py            §5.15 enforced: every seam ABC has ≥1 @abstractmethod and refuses instantiation; no member of a
│                          registry-resolved family is instantiated by name outside its registry (composites such as
│                          GuardChain, AlertRouter, Reconciler, the policies, Checkpoint, Tick and ServeLoop are exempt); ServeLoop/GuardChain/policies are never subclassed in-tree;
│                          the 20 seam ABCs and the 20 §4.3 registries are pinned against each other, name by name;
│                          (the every-selector-resolves assertion lives in test_main.py, after every registry exists)
│                          every SubmittingExecutor subclass accepts the base `submit(intent, Permit)` contract (LSP); no class
│                          both subclasses a seam and reaches a private name of another module
├── test_base.py           ProductionError accumulates every problem into one raise; canonical_bytes/record_hash pinned against
│                          the §6 envelope recipe; ms/UTC helpers reject naive datetimes
├── test_vocab.py          every vocabulary closed; the severity level map pinned; lattice order pinned; a completeness test scans
│                          §5–§7 for `∈ {…}` literals and fails if any closed set is not defined in vocab.py
├── test_document.py       default-deny; required series UUID/rung/execution scope; arm rung equality; golden identity; non-identity exclusion; round trip
├── test_release.py        all release inputs and expected execution scope bound; mutation/source/runtime/distribution drift refuses; exact non-identity paths
├── test_records.py        Decimal/non-finite; ExecutionScope; canonical Intent/ActPermit; readiness/input/quote/evidence/risk values; quantity invariant
├── test_clock.py          TestClock determinism; WallClock honours the stop flag; monotonic pacing survives a wall-clock jump
├── test_sessions.py       weekly sessions across spring-forward and fall-back; holidays, special closes, blackouts, buffers; DST-gap refusal
├── test_cadence.py        FixedInterval zero drift over 10^6 ticks with slow handlers; AlignedBar publish delay; AtTimes; overrun policies
├── test_feed.py           ServingContract evidence; entity ≠ dedupe keys; exact output digests/coverage; per-key min/staleness; drift; ReplayFeed
├── test_decider.py        serving_document derivation, one case per §5.3 rule: trainable mode flip, artifact pin, search winner
│                          applied and search node dropped, gate/stat_test replayed, cut to ancestors(heads), foreach and splits
│                          dropped, $prev refused; the no-restatement pin (nothing in the serve doc restates the run doc);
│                          closed effect metadata before source construction/fingerprint; no data_edge/splits/store scan; existing override path;
│                          registry classification; entry dominance; ReleaseReader/no-direct-I/O; frozen binding; evaluate → candidates/quotes/account/propose
├── test_guards.py         one refusal per knob; lattice composite = max; every finding carries value/bound/reason; include_working; calendar
│                          windows; amend never exceeds bound; hypothesis: day_loss halts before the period loss exceeds bound − max single loss;
│                          cancels bypass the chain
├── test_breaker.py        persisted trips with request/proof ids; authenticated/barriered reduce/reset; resume requires a fresh arm; halt cancel outcomes
├── test_arming.py         maker-checker; verifier construction/fingerprint; exact-intent scope application; reduction rights/use replay; tighten-only; expiry
├── test_verifier.py       frozen-input rehash with no reread; every bound version/digest mismatch returns not_sent;
│                          the native call cannot outlive the permit; a hung call disables further sends
├── test_policy.py         completeness: every rung/breaker/health/readiness/operation/risk-effect/origin combination is vetoed
│                          by a named rule or explicitly allowed, none falls through; the generated golden table matches
│                          the checked-in copy; disjoint classification; authority lifecycle; crash cuts
├── test_executor.py       the full conformance battery run against Shadow, Paper and Recorded (verifier behaviours belong
│                          to test_verifier.py, not here); paper determinism under seed;
│                          ioc/fok/gtd handling and day refused without session_end_ms; every fee kind against its closed form;
│                          LSP: every SubmittingExecutor subclass returns an Ack and never raises for a permission fact;
│                          permit-only live; no replace; frozen-input rehash/no reread;
│                          scope/readiness/input/quote/evidence/risk/fence; hung deadline
├── test_accounting.py     classify() returns exactly one RISK_EFFECTS member and a reduction is proven, not claimed;
│                          every duration/count/calendar × scope-key evidenced; monotonic source tokens; risk versions/digests; corrections; freshness; reducing proof
├── test_coordination.py   expected/authenticated/lease/gateway scope equality; cross-release contention; fencing; stale renewal disables submit
├── test_control.py        caller UUID retry vs repeat; sole writer; HALT; normal-exit journal rows; SIGKILL gap reported; flatten recovery
├── test_resilience.py     ambiguous writes; retry cap; reserved cancel lane; bounded cancel_all 429/timeout/query/reconcile behavior
├── test_ledger.py         chain/idempotency; genesis prev_hash of 64 zeros; torn tail recovered; an edit/delete/insert/reorder
│                          located by verify() -> first_bad_seq; rotation continuity across segments for each rotate.by;
│                          checkpoint atomicity under a crash subprocess mid-batch; no float in any money field;
│                          safety barriers; stale cache rebuilt; divergent refused; final-head journal order; writer lock
├── test_state.py          one fold and one owner: positions/working/pending/breaker/arming/readiness/guard-holds all
│                          derive from apply(); StateView is frozen; reversed fills undone once; recovery from the last
│                          snapshot reproduces the same view; no other module folds the ledger (AST)
├── test_reconcile.py      every break class; pending refs; automatic halt/refuse only; explicit authenticated adoption then re-reconcile
├── test_monitors.py       PSI = 0 on identical samples and χ² scaling; KS hand case; PageHinkley alarms on a shift, not on noise; tracking signal;
│                          insufficient below min_n; last partial chunk never ok; state round-trip; deterministic verdict records
├── test_alerts.py         sink failure never kills the loop; hanging sink bounded (never-replying local socket); construction refusal; dedup /
│                          group_wait / repeat / rate limit / critical bypass; resolved emitted
├── test_health.py         transitions/hysteresis; process/sequence heartbeat; dead_after stops it; second instance; SIGTERM
├── test_metrics.py        naming/base units pinned; undeclared label name refuses at declaration, undeclared value drops to
│                          `other` and increments the dropped counter; every §5.11 counter name exists; JSONL flush
├── test_redact.py         env-var values, webhook URLs and proof bytes never appear in a log line, alert body or ledger record
├── test_compose.py        every rung maps to exactly one collaborator set; the AuthorityTable answers (origin, breaker) for
│                          every rung and refuses a reduction outside reducing; paper cannot select LiveExecutor or
│                          LiveAuthority; an incompatible combination refuses at construction; only this module reads a rung
├── test_leg.py            every LegStep returns what the next one and DecisionPlan need (all eighteen fields reachable);
│                          the §5.4 rebuild check: a reduction leg's constructed Intent re-derives the signed
│                          reduction_intent_digest, so signed economic content and scope are what reach the venue;
│                          each step barriers before its effect; a crash cut after every barrier; cumulative reservations
│                          across legs; Authority polymorphism — shadow/paper mint without writing, live appends
│                          authorization, reduction appends authority_use first; no `rung ==` or `mode ==` branch in leg.py (AST) — the name
│                          appears, since LegBindings carries a rung; branching on it is what is forbidden
├── test_bundles.py        each bundle is frozen and validates presence and arity only — never types, since type validation
│                          would import fourteen later modules and re-create the cycle bundles.py exists to break
├── test_ids.py            deterministic tick/leg/plan/client ids from stable semantic inputs; independent of wall time and
│                          ledger sequence; RecordedIdSource replays the tape exactly
├── test_loop.py           per-key/quote/evidence deadlines; bound-state change at each barrier; cumulative risk; durable findings;
│                          final verify/call refusal, acknowledged external race, crash cuts; one action/client ref
├── test_main.py           release, control proofs, normal-exit mutating-verb journal rows, hard-kill gap reporting, queued receipts, recovery verbs, no semantic overrides
├── test_outcomes.py       [phase 2] forward join strictness (hypothesis: label asof > decided_at); vintage reproducibility; supersede chain
├── test_report.py         [phase 2] IS components sum; Murphy terms sum to Brier; BSS of baseline vs itself = 0; parity diff classes
└── test_readiness.py      release binding; unwaivable foundation items; live refuses without GO; NO-GO exits 5; waivers

examples/production/
├── serve-shadow.json      the synthetic run served at shadow — the 60-second path
├── serve-paper.json       the same with the paper executor and basic guards
└── calendar-weekly.json   a weekly-sessions calendar with holidays and buffers
```

## 9. Changes outside the package

### 9.1 Pipeline — ADR-0091: the subgraph re-execution seam becomes public

**The public API this ADR adds** (the seam the owner is asked to approve):

```
# dskit/pipeline/driver.py
class SubgraphRunner:
    def __init__(self, the_plan, needed, node_outputs, splits_info, prev, policy=None)
    def rerun(self, overrides, outputs, ctx, prev_bindings, *, guard_verdicts=False)
        -> (outputs, reran_keys, seconds)

# dskit/pipeline/policy.py  (new module)
class ExecutionPolicy(ABC):
    @abstractmethod
    def classify(self, key, cls, params, evidence) -> str   # a SERVING_EFFECTS member
    def defer(self, key) -> bool                            # concrete, default False
    def reader(self, key)                                   # concrete, default None
```

`SubgraphRunner` is the extraction of today's `_SearchSeam._execute(overrides,
outputs, ctx, bindings, *, guard_verdicts=False)` (`driver.py:498`). Three
details of that method are contract, not incidental, and the signature above
carries all three:

- **`needed` is a constructor argument, not derived.** `_SearchSeam` computes
  `self.needed = the_plan.ancestors(target) | {target}` from the objective ref
  it parses out of its own `key` (`driver.py:406–430`), and `_execute` filters
  with `k in self.needed and k in dirty` (`driver.py:526`). The runner cannot
  parse a search objective, so the caller supplies the set: search passes
  exactly what it computes today, serving passes `ancestors(heads) | heads`.
  Running all of `dirty` instead would execute dirty descendants outside the
  objective's ancestry on every trial — a behaviour change, which is why this
  is a parameter rather than a fallback.
- **`outputs` is passed in and written in place.** Trials pass a scratch copy
  (`scratch = dict(self._outputs)`, `driver.py:442`), the winner pass and
  serving pass the live dict (`driver.py:490`) because the driver reads it
  afterwards. The runner never decides which; it mutates what it is given and
  returns the same object for convenience.
- **`prev_bindings` is an OUT parameter, renamed to say so.** It is the dict
  `_materialize` records `$prev` resolutions into (`driver.py:298–331`,
  `472–473`) — it is not an input binding. Serving injects the frozen entry
  outputs by pre-seeding `outputs` with them before calling `rerun`, which is
  why §5.3 speaks of executing "from the frozen entry binding" rather than
  passing bindings in.

- **The search-only override validation stays with `_SearchSeam`.** `_execute`
  today calls `unsearchable_space_why(role, parts[1])` and refuses any override
  addressing an unsearchable role (`driver.py:508-522`), and `data` is one of
  them — "a trial would rebuild the source with params the run's identity never
  hashed" (`planner.py:87-104`). Serving's one override addresses exactly a
  `data`-role node's window param, so a verbatim extraction would refuse the
  only override serving needs. The check moves up into `_SearchSeam`, which is
  where it belongs: its reason is about *search trials*, and it does not hold
  for serving, because serving's window path must already exist in the run
  document (§5.3) — the identity did hash it.   `SubgraphRunner` validates only
  that an override addresses a declared node and an existing param path. The
  seam must keep the declared-node check first (`driver.py:507-511` today):
  `Plan.role_of` indexes `resolved[key]`, so testing unsearchability before
  declaredness would raise `KeyError` where today it raises `ValueError`.
- **`rerun` honours `policy.defer(key)`.** `_execute` computes `dirty` from the
  override targets plus their descendants, and serving's only override
  addresses the entry — so a naive extraction would re-run the entry and take a
  second mutable read, contradicting D3 and D6. `rerun` skips any key the
  policy defers and uses the output already seeded for it, which is what makes
  "no second mutable snapshot can occur" true rather than aspirational. The
  window override is still passed to `rerun`: it is what puts the entry — and
  therefore its descendants — into `dirty`, so the subgraph is non-empty. A
  builder who passes `{}` gets an empty `dirty` and executes nothing.

`_SearchSeam.__init__`'s remaining two arguments stay with the seam and do not
move to the runner: `key` (it is what the objective is parsed from) and
`trial_ctx` (the runner takes a `ctx` per call instead).
`_SearchSeam` is re-expressed as a thin caller that owns the objective float,
its own override rule and its `needed` computation, so
`__call__(overrides) -> float` and every search behaviour stay exactly as they
are. **No new params, no grammar change, no identity change**; the regression
guard is the existing `tests/pipeline/test_driver.py` and
`tests/pipeline/test_kinds_search.py` suites plus all 20 pinned sha256 literals
under `tests/` (17 of them document identities, per D24),
all of which must stay unmoved.

`ExecutionPolicy` lives in the pipeline because the structural planner calls it
and `dskit/pipeline` may not import `dskit.production` (below). `policy=None`
is today's behaviour exactly. `dskit/production/decider.py` supplies the
subclass `ServingExecutionPolicy`, whose `classify` returns the closed
`Node.serving_effect` result and whose `defer` marks the sole `entry_read`.
`ReleaseReader` is a production type (`dskit/production/release.py`) that the
planner receives through `policy.reader(key)` and calls only through that
handle, so the dependency arrow stays production → pipeline.

`dskit/pipeline/driver.py`: extract `_SearchSeam._execute` into public
`SubgraphRunner`, but do not reuse the ordinary LOAD → PLAN → RESOLVE path for
serving. Add a policy-aware structural planner that resolves registry classes and
graph topology without constructing/fingerprinting data nodes or materializing
`data_edge`/splits. `ServingExecutionPolicy` runs at this earliest boundary,
requires one source-root `entry_read` dominating all dynamic head paths, defers
that entry's construction and fingerprint out of the base pass, and rejects all
other mutable effects. Deferral is about *when the entry is constructed*, never
about skipping its descendants: after `read_entry` runs the entry, `rerun`
executes every descendant from the seeded output.
`dskit/pipeline/node.py` defines the closed `Node.serving_effect` classmethod
with a fail-closed default, adds the optional `NodeContext.release_reader`
field (default `None`, populated only for a node the policy classified
`release_read`, so every existing node and every ordinary run is unaffected),
and declares
`SERVING_EFFECTS = ("pure", "entry_read", "release_read", "forbidden")`
beside it — the vocabulary lives pipeline-side because pipeline may not
import production, and production reads it from there rather than restating
it in its own `vocab.py`. `TrainableNode` returns `release_read` only for a
manifest-pinned load; `libs/observations.py:ObservationRows` returns
`entry_read`; every audited deterministic built-in used by the serving e2e
explicitly returns `release_read` or `pure`. `RecordedOutputs` is production-owned
(§8) and is classified by the same closed API when the decider substitutes it,
never by an entry in the pipeline's own audit. **The audit is a sized work item, not a footnote.** Phase 1 classifies exactly
the classes the serving e2e needs plus every class in `fitted.py`,
`kinds_flow.py` and `libs/observations.py` — roughly a dozen — and every other
registered class in `kinds_*.py` and `libs/*.py` stays `forbidden` by the
fail-closed default until reviewed. That means a phase-1 serve document can
only use nodes from the audited set, which is a real limitation and is stated
in §2's scope. Widening the set is incremental and needs no ADR: each class
gains an override and a registry-enumeration test line. Registry-enumeration tests in `tests/pipeline/` pin
every classification and prove the classmethod performs no I/O. The derived
serving document drops splits. The configured entry-window override must address
an existing full param path; the standard override rule is unchanged.

The immutable base pass constructs only `pure` nodes and approved
`release_read` fingerprints. Such nodes receive a `ReleaseReader` capability
that can return only manifest-named digest-checked values; direct filesystem or
network APIs are rejected by static/conformance checks. This prevents accidental
I/O, not malicious release-bound child code—the operator explicitly trusts the
reviewed code fingerprint. After fetch, `read_entry` alone constructs/runs the
entry. Only after `ServingContract.snapshot` validates the exact frozen output
does the decider pre-seed `outputs` with the entry's outputs and call `rerun`,
executing only pure or capability-backed descendants from that frozen binding.
The existing `_SearchSeam` (`driver.py`) keeps its current override rule and is
not given a policy object by this ADR; it and ordinary RESOLVE remain
behavior-neutral, and existing search suites and all pinned identities must stay
unmoved — specifically `tests/pipeline/test_driver.py` and
`tests/pipeline/test_kinds_search.py` must pass untouched, and this ADR adds
**no new params and no grammar change** to the pipeline document.

`dskit/pipeline/libs/observations.py`: replace the locator-only proposal with
`ObservationRows.serving_contract(params, verified_run_evidence)`, a pure method
returning source binding, explicit entity-key projection, event-time extraction,
per-key digest recipe only — no universe, because the method is document-blind.
The required universe comes from the serve document's
`document.serving.required_universe`, which `plan` puts into the `FeedSpec` (§5.2); the
pipeline stores no such artifact and this ADR does not add one. It is never inferred from dedupe `key_fields`, which
may contain time. `ServingContract` is a frozen dataclass declared in `dskit/pipeline/node.py`
beside `SERVING_EFFECTS`, for the same reason: pipeline may not import
production, so a pipeline-side node author needs a declaration to implement
against rather than a duck-typed plain object. Production reads it from there. Other entry classes implement the same contract or
cannot serve. Tests prove structural planning never scans the mutable store,
splits never materialize, the window path already exists, and snapshot metadata
hashes the exact rows delivered to descendants.

### 9.2 Skeleton (pin updated in the same commit)

`children/_skeleton/yourproject/nodes.py` gains `serving_effect` on both sample
classes and `serving_contract` on the `data` one — without them the fail-closed
default (§9.1) refuses every node and `configs/serve-sample.json` could not
`plan`, which is exactly what `tests/test_production.py` is specified to prove.
`children/_skeleton/yourproject/execution.py` supplies a `LiveExecutor` template
with read/query/cancel available and permit-gated submit; `accounting.py`,
`approvals.py`, and `coordination.py` supply refusing live accounting,
proof-verification, and fenced-lease templates. `configs/serve-sample.json`
serves `run-sample.json` at shadow; `tests/test_execution.py` runs conformance
and `tests/test_production.py` proves the sample validates/plans while every
live-capable template remains fail-closed. `children/README.md` and the skeleton
`README.md` / `AGENTS.md` / `CLAUDE.md` gain a production section using
`python -m dskit.production serve configs/serve-sample.json --once`. Existing
children are not rewritten by this ADR (§12, item 4).

### 9.3 Documentation and configuration

Root `README.md` (a fifth pillar, "Serve", **and its own exit-code line at
`README.md:47-48`, which carries a third variant of the same sentence and must
be rewritten with the other two or it silently diverges**), root `CLAUDE.md`
and `AGENTS.md` (layout tree, commands, and the exit-code line — which gains 4 and 5 and
separates breaker-halted from readiness NO-GO per §5.13), `docs/architecture/README.md` (package table),
`TODO.md` (the "Long-term goal — a generic SERVING LOOP" section marked
superseded by ADR-0090 with its constraints listed as satisfied), `pyproject.toml`
(no new extras in phase 1; the new modules follow the docstring standard, so no
`per-file-ignores` entries), `docs/architecture/decision-log.md` (ADR-0090,
ADR-0091).

## 10. Test plan (TDD)

Order per module: the Opus test author writes `tests/production/test_<module>.py`
from §5–§7 (contracts, vocabularies, bounds, invariants) — red; Fable implements
`dskit/production/<module>.py` — green; Opus reviews the pair. Module order
follows dependencies: `vocab` → `base` → `redact` → `records` → `document` →
`release` → `clock` → `sessions` → `cadence` → `ledger` → `state` → `control` →
`accounting` → `guards` → `breaker` → `arming` → `coordination` → `ids` →
`bundles` → `policy` →
`resilience` → pipeline `SubgraphRunner` + `serving_contract` →
`feed` → `decider` → `reconcile` → `readiness` → `verifier` → `executor` →
`monitors` → `metrics` →
`alerts` → `health` → `leg` → `compose` → `loop` → `__main__` → docs → examples
→ skeleton.

The order is acyclic, and two placements are the reason `policy.py` was split:
`ActionPolicy`/`TransitionPolicy` are pure over closed vocabularies so they can
sit early, but `SubmissionVerifier` rehashes the `EntryBatch` and refreshes
quote, accounting, executor-scope and lease state — so it must follow `feed`,
`accounting`, `coordination` and `readiness`, and it gets its own module rather
than dragging two decision tables down the order behind it. `executor` then
follows `verifier`, because `LiveExecutor.submit` delegates to
`verify_and_call`; `test_executor.py` asserts the executor contract and leaves
the verifier's own behaviours to `test_verifier.py`. `compose` sits between `leg` and `loop` because it can only build a bundle once
every collaborator class exists, and `loop` receives bundles already built.
`readiness`
precedes `verifier` and `leg` because both take a GO as an input; `feed`
follows the pipeline change because `ServingContract` is produced by the entry
class, not by production. `document` is written before the registries exist, so
the "every selector names a registry" assertion of §4.3 belongs to
`test_main.py`, which runs after them — `test_document.py` asserts shape,
default-deny and identity only.

Invariants every phase must keep green: the four existing purity gates
(`tests/{assets,journal,onboarding,pipeline}/test_purity.py`) and the new one; every pinned identity hash unmoved (the 20 sha256 literals pinned
across `tests/`); the full
suite; `ruff` clean; the skeleton pin.

The e2e (in `conftest.py`): build a synthetic run with `run_document` over a temp
onboarding root filled by the `FakeConnector`; serve it for three ticks at
`shadow` with `TestClock`, then at `paper`; assert paper submits without an
`ActPermit` and never constructs `LiveExecutor`. A separate all-fake
`live_limited` case first records release-bound readiness GO, then uses
distinct maker/checker proofs, a matching authenticated execution scope and a
two-instance fenced lease to prove stale-token rejection. Across them, assert one terminal
`tick` and `decision` per `tick_start`, the chain and release verify, and the journal row
anchors the head. Replay must reproduce exact semantic decision payloads under
`RecordedIdSource`; ledger envelope timestamps/sequences/hashes are verified by
their own deterministic chain assertions.

## 11. Build phases and model assignment

| phase | lands | model |
|---|---|---|
| 1 — foundation | every module in §8 not marked phase 2, including the full authority stack (`arming.py`, `coordination.py`, `readiness.py`, `LiveExecutor`, `LiveAuthority`, `ReductionAuthority`) and all four rungs; ADR-0091, every §7 verb not marked phase 2, README/AGENTS/CLAUDE, examples, skeleton, doc updates | tests: Opus · code: Fable · review: Opus |
| 2 — evidence | `outcomes`, `report`, `replay` verb, outcome-readiness evidence, Outcome + Parity monitor families, DDM/ADWIN/JS/Linf, alert inhibition/silences/escalation/ack + sqlite state, systemd heartbeat, `libs/sqlite.py`, `libs/parquet.py`, `Signer`, the `approve` verb for `hold` | tests: Opus · code: Opus · review: Opus |
| 2b — audit | classify the remaining registered `kinds_*.py` / `libs/*.py` classes for `serving_effect`, widening what a serve document may reference | Opus |
| 3 — packs | `exchange_calendars`, `prometheus`/`opentelemetry` sinks, the stream seam, migrating onboarding packs onto `resilience.py` (own ADR) | Opus |

Per the owner: no Fable after the initial plan and build.

**Why the authority stack ships in phase 1 rather than waiting for a venue.**
A reviewer argued it should be deferred until a real adapter can exercise it,
since every object in it exists to gate `live` and no live executor exists at
the end of phase 1. The argument does not hold, for two reasons. First it is
not unexercised: §10's e2e runs an all-fake `live_limited` case with distinct
maker and checker proofs, a matching authenticated execution scope and a
two-instance fenced lease that proves stale-token rejection — the whole path
except the socket. Second, deferring it inverts the dependency: the first
venue adapter is a tier-3 child written against these seams, so the seams must
exist first or the child author invents them and the package inherits whatever
they chose. A safety stack is also the wrong thing to bolt on after the loop
is running. It ships whole.

## 12. Resolved choices in this resubmission

The corrected plan takes the prior recommended choices as its explicit defaults:

1. Cadence is serve-document runtime policy; pipeline schedule remains descriptive.
2. The package is `dskit.production`.
3. Full recorded gate/stat-test outputs are replayed; absent evidence refuses.
4. Existing child loops remain until a separate phase-2 port.
5. JSONL ships first; sqlite remains phase 2.
6. Authenticated, distinct maker/checker proofs are mandatory from
   `live_limited` upward; paper cannot reach a live executor.
7. The serve process is the sole ledger writer; control commands use a durable inbox.
8. Multi-leg proposals execute sequentially with cumulative risk reservations.

## 13. Traceability

| piece | grounded by |
|---|---|
| loop lifecycle, three timestamps, checkpoint-last, unknown-outcome rule, lock, signals, exit codes, replay tape | R7 |
| seven-box decomposition, swap-three symmetry, refusal as recorded status, restart amnesia | R9 |
| executor verbs, status vocabulary, fills not final, units, paper knobs, conformance battery | R8 |
| order state machine, gate taxonomy, reconciliation, authenticated control acts, postmortems | R1 |
| guard lattice, `Limit` = measure × window × bound × scope, mode ladder vs breaker, halt ≠ flatten | R5 |
| ledger kinds, envelope, hash chain + journal anchor, JSONL/sqlite contract, bitemporal outcomes, attribution, parity diff, venue reconciliation job | R6 |
| single-writer control inbox, barriered authority consumption, sequential cumulative risk | skeptic/Bugbot review |
| retry classification, ambiguous writes reconcile, breaker per scope, rate limiter, transport timeouts, live conjunction | R3 |
| symptom alerts, severity map, sink discipline, router, health model, heartbeat, one record per tick, readiness | R4 |
| monitor ABC and families, references/chunkers/thresholds, `insufficient`, release ladder | R2 |
| the constraints every piece must satisfy | the owner's `TODO.md` serving-loop section; root `CLAUDE.md` |

---

## Appendix A — ADR-0090 as it will appear in `decision-log.md`

````
## ADR-0090 — `dskit.production`: the production layer (serve, guard, act, record, monitor)

**Status:** proposed (2026-09-04; Opus-reviewed; awaiting owner approval)

**Context.** dskit runs documents in batch and has no seam for running a fitted
model forward on a cadence. Two hand-rolled tier-3 forward loops exist:
`children/intraday_poc/intraday_poc/live.py` (1,367 lines, where most of that
child's HIGH-severity defects live) and
`children/intraday_equities/intraday_equities/live.py` (191 lines, paper-only
limit-order intents). TODO.md records the constraints a generic loop must satisfy: read
the run's configs, fetch through the connector, decide with the same node objects,
one decision record per tick, gate on a supplied calendar, take an executor
object, and make moving money an explicit loud act. Nine research reports
(docs/new_package_proposals/research_reports/) ground the design.

**Decision.** A fifth package, `dskit/production/`, per
docs/new_package_proposals/production.md (D1–D24, invariant matrix §5.14,
structure §8): a serve document with a stable series UUID whose immutable release
binds the run, graph, artifacts, resolved code, complete runtime, adapter,
approval verifier, fenced lease and source config. The entry's pure
ServingContract derives source binding, explicit entity keys, event time, digest
recipe and manifest-bound universe. A structural policy defers its construction,
fingerprint, data edge and splits before fetch/gates. Startup, every tick and
pre-submit verify content/runtime and artifact age. One `ServeLoop` injects
closed policy, execution, accounting and coordination seams. The sole source-root
`entry_read` dominates every dynamic path; only pure or capability-backed
release reads surround it. Exact uniform coverage and the oldest key watermark
pass before descendants.
It evaluates heads once, derives candidate scope keys and quotes,
then snapshots correction-aware accounting before sizing. Legs run sequentially
with prior reservations folded. Closed matrices use disjoint risk effects,
preserve shadow/paper, forbid replace shortcuts, and make LiveExecutor accept
only ActPermit. After the final barrier, a bounded verify-and-call gate refreshes
input/quote/evidence/risk plus calendar, link, health, breaker, authority and
lease, then passes the full deadline/fence to the gateway. Mismatch is `not_sent`;
unavoidable external venue races are `unknown` and reconcile. Cancel uses
reserved bounded capacity. Reduction,
flatten execution, resume and adoption are
authenticated acts. A hash-chained ledger has one writer and barriers
`tick_start`, the complete decision plan and findings, intent, authority
use/authorization and safety transitions before effects. Flatten refs are
request/index/digest-derived; caller-UUID controls use a durable inbox and HALT
remains out-of-band. Normally completed mutating CLIs journal their result once;
hard-kill cross-store gaps are reported. Serve never journals consumed commands.
Recovery gives every started tick a terminal decision and every
reserved reduction right a query-first resolution. Core supplies
shadow/paper/recorded execution and accounting,
monitors, alerts, health and resilience. Purity: stdlib + pipeline + onboarding +
assets + self; journal function-import only.

**Contents (package).**

```
dskit/production/
  __init__.py       public surface (curated re-exports)
  __main__.py       validate | plan | serve | arm-request | approve-arm | disarm | halt |
                    reduce | flatten-request | approve-flatten | execute-flatten | resume |
                    status | verify | reconcile | adopt | ready | replay | outcomes | report
  base.py           ProductionError; assets checkers; ms/UTC; canonical record hashing
  vocab.py          every closed vocabulary, one module
  redact.py         Secrets resolution; redact() on logs, alerts and reasons
  document.py       ServeDocument; default-deny; the graded/excluded section partition
  release.py        ReleaseManifest; class/code/adapter/source/artifact/runtime fingerprints
  records.py        Quote/Candidate/Proposal/Finding/EntryBatch/DecisionPlan/Intent;
                    Permit (frozen dataclass base) + SimulatedPermit + ActPermit; TickResult;
                    AccountState; RiskVersion
  clock.py          Clock ABC; WallClock, TestClock, ReplayClock; CLOCK_KINDS
  sessions.py       Calendar ABC; AlwaysOpen, WeeklySessions, EventWindow, Composite
  cadence.py        Cadence ABC; FixedInterval, AlignedBar, AtTimes, OnData; Overrun
  control.py        ControlInbox; CommandProcessor; sole-writer dispatch
  feed.py           ServingContract + FeedSpec; EntrySourceFeed; ReplayFeed
  decider.py        serving_document(); Decider; Proposer ABC; IntentRows, TargetPositions
  guards.py         Guard ABC; GuardChain; Limit; RangeGuard; Measure ABC + registry
  breaker.py        Breaker (active | reducing | halted); trips; kill switch; cooling-off
  arming.py         ApprovalVerifier ABC; maker-checker proofs; Arming fold; scope application (minting is leg.py)
  executor.py       Executor ABC (read/query/cancel); SubmittingExecutor; Shadow, Paper,
                    Recorded, Live; executor_conformance_suite
  accounting.py     Accounting ABC; PaperAccounting; RecordedAccounting
  coordination.py   Lease ABC; ProcessLease; LeasePermit; fencing tokens
  policy.py         ActionPolicy; TransitionPolicy; Rule sets; the golden decision table
  verifier.py       SubmissionVerifier — the final verify-and-call gate
  resilience.py     Classifier ABC; Retry; CircuitBreaker; RateLimiter; Transport ABC
  ledger.py         Ledger ABC + barrier; JsonlLedger; Checkpoint; ServeRoot; chain + verify
  state.py          SeriesState (sole ledger fold); StateView; TickState; PositionBook; Recovery
  reconcile.py      Reconciler; ReconReport; break classification
  monitors.py       Monitor ABC; Reference/Chunker/Threshold strategies; monitor families
  metrics.py        counter/gauge/histogram registry; label cardinality cap; JSONL flush
  alerts.py         AlertSink ABC; Log/Memory/Email/Webhook; AlertRouter; ALERT_SINK_KINDS
  health.py         Health state machine; HealthProbe ABC; Heartbeat; instance lock; signals
  loop.py           ServeLoop (the scheduler, not the composition root); Tick template method; lifecycle
  ids.py            IdSource ABC + ReleaseIdSource + RecordedIdSource
  leg.py            LegPipeline (the eight submission steps); Authority ABC + the three kinds; permit minting
  bundles.py        the seven frozen collaborator dataclasses
  compose.py        AuthorityTable; bundles_for(): the closed rung to collaborator table
  readiness.py      release-bound checklist → GO / NO-GO
  outcomes.py       [phase 2] bitemporal outcome join, supersede chain, as-of cut
  report.py         [phase 2] attribution, calibration, drawdown, replay parity diff
  libs/__init__.py  pack namespace
  libs/sqlite.py    [phase 2] SqliteLedger
  libs/parquet.py   [phase 2] RunReference over the run's predictions parquet
  README.md / AGENTS.md / CLAUDE.md
tests/production/   purity + oop + every module above + a shadow/paper/live_limited e2e
examples/production/ serve-shadow.json, serve-paper.json, calendar-weekly.json
```

**Consequences.** Children stop owning loops: a child ships an executor subclass,
accounting, approval and fenced-lease implementations, optionally a
proposer/measures, and JSON.
The skeleton pin and README/AGENTS/CLAUDE trees update together. TODO's plan item
is superseded, but implementation remains unchecked until code lands. Phase 1
lands the foundation; phases 2–3 add evidence and packs. §12 records the resolved
defaults for this resubmission.
````

## Appendix B — ADR-0091 as it will appear

````
## ADR-0091 — The driver's subgraph re-execution is a public seam with a policy object

**Status:** proposed (2026-09-04)

**Context.** `_SearchSeam._execute` already re-executes `needed ∩ dirty` under
`"node.param.path"` overrides with the full node lifecycle, but it is private,
returns an objective float, and hardcodes the search override rule. A serving loop
needs the same mechanism with one different override rule and head outputs.

**Decision.** `SubgraphRunner` becomes public, with a policy-aware structural
planner that sees class metadata/topology before any source construction,
fingerprint, `data_edge` or split materialization. Ordinary search resolution is
unchanged. Serving drops splits, defers construction of the sole source-root `entry_read`
out of the base pass, and requires its existing-param window override. The immutable base
pass permits pure nodes plus approved release reads only through `ReleaseReader`;
after fetch, the entry alone runs and its contract validates the frozen snapshot
before descendants execute from that binding.

**Consequences.** Search behavior and pinned identities remain unchanged.
Observation entries expose pure `serving_contract(params, verified_run_evidence)`
metadata for source binding, explicit entity keys, event time and digest recipe — declaration only. The required universe is a
serve-document field pinned by `plan`, not contract output. Dedupe keys are not
treated as a universe. Pipeline never imports production. Release-bound child code remains a
declared trusted boundary, backed by code fingerprints and no-direct-I/O tests.
````
