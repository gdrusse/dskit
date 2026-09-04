# `dskit.production` — the production layer (proposal for ADR-0087 / ADR-0088)

**Status:** revised 2026-09-04 after Bugbot's fourth review; corrected re-review
pending. Owner approval is still required before any file under
`dskit/production/` exists.

**What the owner is asked to approve.** (1) The file-by-file structure in §8.
(2) The design decisions D1–D24 in §3. (3) The pipeline change in §9.1 (a public
subgraph-rerun seam, ADR-0088). (4) The build phases and model assignment in §11.
(5) The resolved choices in §12. Everything else is the reasoning behind those.

How to read: §1–§3 are the ruling; §4–§7 are the contracts a test can be written
from; §8–§11 are what lands where and in which order; §13 traces every piece to
the report that grounds it.

---

## 1. The one-paragraph model

A **serve document** (JSON, its own identity hash) declares a production process:
which immutable **release** it serves (run hash, artifact digests, resolved class
fingerprints, adapter digest, and source-config version are verified, never
restated), where live rows **enter** that run's own node graph and how
wide the window is, which node keys are the decision **heads**, how head outputs
become domain-neutral **proposals**, how ticks are **scheduled** (clock, calendar,
cadence), which **guards** every proposal must pass, which **executor** acts,
which **accounting** evidence limits use, which fenced **lease** owns submit,
which **monitors** watch the decision stream, and where the **ledger** lives. One
`ServeLoop` class runs it: each tick is a fixed phase order — record start,
barrier, calendar gate, verify release, fetch, read entry, watermark gate,
evaluate heads, identify candidate scope keys, extract quotes, snapshot account,
construct proposals, guard/scope, barrier the decision plan and findings,
durably record intent, authorize, act, record outcome, record the
terminal decision/tick, checkpoint last. Recovery closes any unfinished tick
before another begins, so every durable `tick_start` eventually
has one terminal `tick` and one `decision` in the hash-chained append-only
ledger, and one `dskit.journal` production row per process. Backtest,
shadow, paper, and live use the same decision document with validated injected
runtime objects; moving real money requires a recorded, expiring,
independently authenticated maker-checker **arming** bound to the immutable
release hash — never a config key or free-form principal name. The decision is
made only by re-executing serving-audited, read-only nodes from the training run
in `mode: load`, after the entry watermark passes freshness, so the number that
reaches the venue is the number the backtest
scored.

## 2. Scope

**Generic — belongs in `dskit/production/` (tier 1, stdlib + dskit siblings):**
the serve-document grammar, release manifest and identity; clock / calendar /
cadence; the feed seam over onboarding; the decider (serving-document derivation
and serving-safe subgraph re-execution); proposals, guards, limits, accounting /
valuation, the breaker, authenticated arming; the executor ABC with shadow /
paper / recorded executors and a conformance battery; the ledger, checkpoint,
and reconciliation; monitors; alerts, health, heartbeats; resilience policies
(retry, circuit breaker, rate limiter, transport); outcomes, attribution, replay
parity; the readiness checklist; the CLI.

**Child (tier 3) — never in dskit:** the venue executor subclass (translation of
units, order types, error codes, dedup and replace semantics), the proposer when a
head's output shape is bespoke, its accounting / valuation and exposure measures,
its authenticated-approval verifier, every threshold and limit *value*, the
fenced deployment lease, calendar contents, readiness evidence, credentials (env-var
names in config, values in the environment).

**Deferred (named, not built, not documented as available):** a streaming
(`websocket`) seam; a `prometheus`/`opentelemetry` metrics pack; an
`exchange_calendars` pack; migrating the onboarding packs' hand-rolled backoff
onto `resilience.py` (its own ADR); a `sqlite` ledger pack and a `parquet`
run-reference pack are phase 2 (§11).

## 3. Design decisions

Each is a ruling with its reason and the alternative rejected. Reports in
brackets.

- **D1 — One package, `dskit.production`, one loop class, injected seams.** The
  seven-box consensus decomposition (clock, feed, ledger, decision, guard,
  executor, recorder/control) maps one-to-one onto the owner's constraints; every
  mature framework has all seven and the ones that lack guard/ledger/reconcile
  are the ones with documented live failures [R9]. Rejected: growing
  `children/*/live.py` (the owner's audit found the generic loop sitting in tier
  3 with most of the child's HIGH defects).
- **D2 — Mode is validated object composition, never a loop branch.** Shadow,
  paper and live bundles select compatible executor, accounting, approval and
  coordination objects (and clock/feed for replay) before construction [R7, R9].
  There is no `if mode ==` in `loop.py`; policy objects implement the closed
  matrix and an AST test rejects `mode ==` / `rung ==` there.
- **D3 — The decision is a re-execution of the run's own subgraph.** The decider
  derives a *serving document* from `<run-dir>/config.json` (trainables flipped
  to `mode: "load"` pinned to `artifacts/<key>`; search winners applied and search
  nodes dropped; gate/stat_test verdicts replayed from the run's records).
  `ServingExecutionPolicy` recognizes only `pure`, `release_read` and
  `entry_read`; missing/legacy `read`, write, network and unknown effects refuse.
  A `release_read` may open only a manifest-named object through the verifying
  release loader, which content-checks bytes at the read and returns a frozen
  value. The designated entry is the only permitted `entry_read`; it must be a
  source root and dominate every non-release-bound, data-dependent path to every
  head. All tick descendants must be `pure` or verified `release_read`, so no
  second mutable source can execute before or after the watermark gate. A child
  cannot override these rules in JSON. Each tick re-executes the entry first
  under the one allowed window-bound override and returns
  `EntryBatch{outputs, data_asof_ms, inputs_digest}`; the loop applies freshness
  and skew gates before the runner may execute descendants through the heads.
  Rejected: executing an arbitrary ancestor before safety gates, or constructing
  nodes by hand in the loop (`intraday_poc/live.py` does this for two nodes and
  is 1,368 lines) [owner constraints 1, 3; R2 Rule #32; R6 §2.6].
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
- **D6 — Every tick keys on the data's as-of.** Three times on every decision:
  `tick_at` (scheduled boundary), `data_asof_ms` (watermark = newest instant the
  entry node emitted), `observed_at_ms` (wall). Only `tick_at` orders ticks;
  `time.monotonic()` paces and times out; a stale watermark refuses the tick
  [R7 §1.1, §2.1].
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
  reason. Before any proposal-shaped submit, a barriered `decision_plan` captures the original
  and final proposal, every initial and fresh-check finding, entry/head/quote
  provenance, risk-state digest, scope verdict and action-matrix result. The
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
- **D10 — Rung, breaker and health compose by an action matrix, never `min`.**
  Breaker is `active | reducing | halted`; health is `ready | degraded |
  unhealthy`. Every matrix cell is explicit:
  - `shadow + ready + {active | reducing}`: proposal-shaped calls route only
    to `ShadowExecutor` and return `not_sent`; no live permit exists.
  - `paper + ready + active`: new, risk-increasing and reducing proposals may
    submit to `PaperExecutor` without live authority. `paper + ready +
    reducing` permits only accounting-proven reductions. Paper can never select
    a `LiveExecutor`.
  - `{live_limited | live} + ready + active`: new, risk-increasing and reducing
    live submissions require a fresh exact-intent permit derived from the
    ordinary arm. The same rungs in `reducing` permit only
    accounting-proven intents named by a fresh `ReductionAuthorization`; that
    authority is converted to an exact-intent permit.
  - Any `halted`, `degraded` or `unhealthy` cell refuses submission.
    Query/reconcile/cancel remain available in every cell and use the priority
    transport policy from D9.
  The closed table covers `new | risk_increasing | reduce | cancel | query |
  reconcile` × rung × breaker × health and is exhaustively tested
  [R5 §2.4, R4 §2.1, R1 §1.4].
- **D11 — Arming is an authenticated maker-checker act, not a config key.**
  `arm-request` writes a signed canonical request bound to the `release_hash`,
  rung, bounded expiry, allowlist and limits overlay; `approve-arm` accepts a
  separately signed approval. The graded `arming.approval` seam resolves a
  child `ApprovalVerifier`, whose code/trust-root reference is release-bound and
  which derives principal ids from proofs — no free-form `--by` identity.
  For ≥ `live_limited`, maker and checker must differ; expiry may not exceed the
  graded `arming.max_duration_s`; an allowlist must be a subset and every limit
  overlay must prove it is at least as strict as the document. The final arming
  record binds the release hash and both proof digests. Serving live additionally
  requires `--armed` and `DSKIT_PRODUCTION_ARM=<release hash>`; absent or
  expired arming records `not_armed` and refuses submission; it never silently
  changes the configured executor or rung. Query, reconcile and cancel remain
  available without arming. Immediately before each ordinary live submit, the
  guard chain re-applies the current arming allowlist and overlay. `ActPermit`
  binds the barriered `decision_plan` digest, `intent_digest`, `client_ref`,
  instrument, scope/overlay digest, economic-risk-state digest, release hash,
  authority id and lease fencing token; the executor verifies that exact binding. A
  `ReductionAuthorization` is the sole alternative authority: it does not require
  a current ordinary arm, but permits only its pre-signed reduction intent digests
  under document limits; each digest is single-use and the whole authority has a short deadline
  [R5 §2.4, R1 §2.1, R3 §2.5].
- **D12 — Halt never flattens.** Halt = refuse submissions + best-effort cancel
  of working orders (outcome recorded: `none | submitted | failed | partial |
  unknown`). Flattening is a separate authenticated human act that enters
  `reducing`; the accounting strategy, not a model claim, must prove each
  proposal cannot increase absolute exposure, and every ordinary guard still
  runs. `reduce --proof` enters reducing but grants no submit authority.
  A verified `flatten-request --plan --proof` records a maker-signed
  `ReductionPlan` bound to the release, fresh risk-state digest, full canonical
  intents/digests and a short expiry; duplicate intent digests refuse. It then
  barrier-transitions `active | halted → reducing` without submit authority.
  `approve-flatten --request --proof` requires a distinct checker and creates a
  `ReductionAuthorization` with one single-use right per named intent digest.
  `execute-flatten --authorization --proof` verifies the closed execution
  purpose and durably queues those stored intents for the active loop, which
  processes them in canonical order through the same sequential pre-submit
  gates as model intents. Each client reference is
  `H("flatten-v1", release_hash, reduction_request_id, zero_based_intent_index,
  intent_digest)`, independent of CLI/process time, ledger sequence or retries.
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
  `Executor` owns `spec`, `capabilities`, `check`, `submit`, `cancel`, `order`,
  `open_orders`, `fills`, `balances`, positions and settlements. Read/query/cancel
  construction is always possible; `submit(intent, permit=None)` lets shadow/paper
  act without live authority, while `LiveExecutor` requires a valid `ActPermit`
  and matching fence token. A live document must configure a graded
  child `Lease`; acquire/renew yields monotonically fenced permits and lease loss
  immediately disables submit. The child executor must reject stale fence tokens,
  pinned by a two-instance conformance test. Eleven order statuses preserve terminality; fills carry
  `pending | final | reversed`; native units carry a declared label and money is
  `Decimal` at the boundary [R8 §2.1–2.3]. `Accounting.snapshot` receives every
  canonical measure/window/scope requirement declared by limits and returns an
  `AccountState` with timestamped, source-digested `MeasureEvidence`, including
  corrected/reversed-fill folds, window baselines and every candidate-derived
  scope key. Proposals act strictly sequentially with prior reservations/results
  folded, so separately safe legs cannot exceed a collective limit.
  There is no generic live default: a live-capable plan must supply a child
  accounting class, valuation freshness bound, approval verifier and fenced lease.
  Core ships shadow, paper and recorded executors plus paper/recorded accounting.
- **D15 — One append-only chain per serve series; state is its fold.** Process
  starts and stops are records in that continuing chain. The ledger assigns dense
  `seq`, `recorded_at_ms` and `prev_hash`; idempotency first looks up the
  caller's stable `id` and compares a `payload_digest` over caller-controlled
  fields only. JSONL uses one `O_APPEND` write, torn-tail recovery and segment
  continuity; `fsync` policy is graded, and safety records always cross a barrier.
  Corrections supersede, never mutate. `arming.json`, `breaker.json` and
  `checkpoint.json` are caches carrying the ledger head they project; startup
  rebuilds from the chain and refuses a mismatching cache. The head is anchored
  into the journal at every graceful stop and optionally to an external sink
  during long processes [R6 §2.1–2.4, R4 §1.3].
  The serving process is the sole ledger writer. Concurrent control CLIs use a
  durable content-addressed inbox; the writer verifies, records and barriers
  commands idempotently, while `HALT` remains an independent sentinel.
- **D16 — Monitors are strategy objects with a first-class `insufficient`.**
  `Monitor.fit/observe/verdict/state/restore`; families operational, stream,
  distribution, outcome; `Reference`, `Chunker`, `Threshold`, `Response`
  strategies; verdicts `ok | warn | alarm | insufficient`; below `min_n` never
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
  heartbeat worker emits `file` / `url` at `every_s` independently of tick
  cadence and stops on `unhealthy`. The package never pages on its own death
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
- **D22 — The journal keeps one process row plus one row per control act.** The loop calls
  `record_production` once at stop (head anchor and exception in `notes`). Every
  accepted mutating control act also writes a production row linked by its stable
  request id: `arm-request`, `approve-arm`, `disarm`, `halt`, `reduce`,
  `flatten-request`, `approve-flatten`, `execute-flatten`, `resume`, `reconcile`
  and `adopt`. The generic rule and complete verb set are pinned by tests; no
  journal schema change is required.
- **D23 — Decision phases are single-threaded; auxiliary work is isolated.**
  Tick phases run sequentially for deterministic ordering [R9 Nautilus]. Alert,
  probe and heartbeat workers never execute decision code or mutate folded state;
  they communicate through bounded queues or atomic snapshots. A stuck custom
  call consumes at most its one dedicated daemon thread and turns health degraded
  or unhealthy without blocking tick shutdown.
- **D24 — A content-bound release, not a mutable path, is what gets armed.**
  `doc_hash` follows the pipeline recipe with exact non-identity *paths*, not
  whole sections: alert/heartbeat endpoints, `ledger.root`, rotation placement
  and secret values are excluded; `ledger.fsync`, arming policy and every
  decision/guard/execution/accounting/approval/coordination knob are graded.
  Feed root/source/stream are not serve-document knobs: `FeedSpec` is derived
  from the resolved entry node and normalized before hashing. `plan` emits an immutable
  `ReleaseManifest{doc_hash, run_hash, serving_hash, artifact_digests,
  artifact_created_at_ms,
  resolved_classes, code_fingerprints, adapter_digest, feed_spec,
  source_config_hash, approval_fingerprint, lease_fingerprint}`.
  `release_hash = canonical_hash(manifest)`; arming, intents, process records and
  immutable release subdirectory use it. Startup and every tick re-verify
  all hashes plus artifact age. Immediately before submit, verification runs again;
  expiry records `artifact_expired`, degrades health, refuses action, and requires
  a new release/arming. Every pull proves the pinned source hash, and every submit
  checks manifest, arming, permit and lease bindings agree.

## 4. The serve document

### 4.1 Grammar (default-deny at every level; `notes` allowed everywhere)

```jsonc
{
  "name": "yourproject-serve",
  "notes": "Why this process exists and how to promote it — the 'why'.",
  "serving": {
    "run_dir": "pipeline_runs/train-2026-01-01-abcd1234",   // the run served; config.json + artifacts read from here
    "adapter": "yourproject",                                 // exact import, captured in the release fingerprint
    "entry": {"node": "bars", "param": "since_ms", "window_ms": 14400000},
    "heads": ["select"],                                      // node keys whose outputs form the proposals
    "proposer": {"uses": "intent-rows", "params": {"output": "picks", "fields": {"instrument": "symbol", "side": "side", "qty": "qty"}}},
    "replay": {"gate": "recorded", "survivors": "recorded"},  // gate/stat_test nodes replayed from the run's records
    "max_artifact_age": "P30D"                                // refuse a run older than this (FreqAI expiration_hours)
  },
  "feed": {"uses": "entry-source", "params": {"pull": "acquire"}}, // locator is derived from the resolved entry node
  "schedule": {
    "clock": {"uses": "wall"},
    "calendar": {"uses": "weekly-sessions", "params": {"tz": "America/New_York",
                 "sessions": [{"days": ["mon", "tue", "wed", "thu", "fri"], "open": "09:30", "close": "16:00"}],
                 "holidays": ["2026-11-26"], "after_open_s": 60, "before_close_s": 120}},
    "cadence": {"uses": "aligned-bar", "params": {"bar_ms": 60000, "publish_delay_ms": 5000}},
    "overrun": {"policy": "coalesce", "max_lag_ms": 30000},
    "dead_after_ms": 600000,
    "max_staleness_ms": 120000,
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
                "on_halt": {"cancel_open": true}},
  "accounting": {"uses": "paper", "params": {}, "max_valuation_age_ms": 60000},
  "arming": {"max_duration_s": 14400, "approval": {"uses": "deny-all", "params": {}}},
  "coordination": {"lease": {"uses": "process", "params": {}},
                   "ttl_ms": 30000, "renew_every_ms": 10000, "renew_timeout_ms": 2000},
  "reconcile": {"on_start": true, "every_s": 300, "on_mismatch": "halt", "lookback_ms": 86400000},
  "monitors": {
    "pred_shift": {"uses": "psi", "params": {"field": "prediction", "bins": 10,
                   "reference": {"uses": "leading", "n": 500}, "window": {"kind": "count", "n": 300},
                   "threshold": {"kind": "alpha", "alpha": 0.01}, "response": "warn"}},
    "coverage":   {"uses": "coverage", "params": {"window": {"kind": "count", "n": 50}, "threshold": {"kind": "constant", "min": 0.5}, "response": "warn"}}
  },
  "health": {"failure_threshold": 3, "success_threshold": 1, "probe_timeout_s": 1.0,
             "probes": {"disk": {"uses": "ledger-writable"}, "venue": {"uses": "executor-check", "scope": "dependency"}}},
  "heartbeat": {"every_s": 60, "emitters": {"file": {"uses": "file"}}},
  "alerts": {"sinks": {"ops": {"uses": "webhook", "params": {"url_env": "OPS_WEBHOOK_URL", "template": "slack", "timeout_s": 5}}},
             "routes": [{"severity": "critical", "sinks": ["ops"]}, {"severity": "warning", "sinks": ["ops"]}],
             "group_wait_s": 30, "repeat_interval_s": 14400, "rate_limit": {"max_per_hour": 20, "burst": 5}},
  "ledger": {"root": "./serve", "fsync": "every", "rotate": {"by": "day"}},
  "env": {"env_file": ".env", "require": ["OPS_WEBHOOK_URL"]}
}
```

Numbers above are illustrations. **Code holds no threshold**; every default is one
named constant read by `validate_params` and the run alike.

### 4.2 Identity

`doc_hash = canonical_hash(document with notes stripped and only these exact
non-identity paths removed: alert and heartbeat endpoint values, `ledger.root`,
ledger rotation placement, env-file paths, and secret values)`. Everything else
is graded, including `ledger.fsync`, source identity, accounting, arming, and coordination
policy. `plan` materialises the immutable `ReleaseManifest` from §5.3.1 and the
serve-series root is stable `<ledger.root>/<name>/`, with immutable
`releases/<release_hash>/` metadata beneath it. Changing anything that can
affect data, decisions, permissions, durability, valuation, or actions therefore
creates a new release; relocating storage or notification endpoints does not.

### 4.3 `uses` resolution

Every `uses` is a registered kind name or a `pkg.module:Class` reference, resolved
exactly as pipeline nodes and onboarding connectors are (import = registration);
each family has its own registry (`CALENDAR_KINDS`, `CADENCE_KINDS`,
`FEED_KINDS`, `PROPOSER_KINDS`, `GUARD_KINDS`, `MEASURE_KINDS`,
`EXECUTOR_KINDS`, `ACCOUNTING_KINDS`, `APPROVAL_KINDS`, `LEASE_KINDS`,
`MONITOR_KINDS`, `PROBE_KINDS`, `SINK_KINDS`, `HEARTBEAT_KINDS`); registering a name twice
refuses. A child registers nothing and references its classes by path.

## 5. The seams

Conventions for every class below: `_PARAMS` default-deny with
`reject_unknown_params` imported from `dskit.pipeline.node`; validation in
`__post_init__`/`validate_params` accumulating every problem into one
`ProductionError`; abstract hooks are `@abstractmethod`; `__all__` is the API;
NumPy docstrings with an instantiating example; no type hints in signatures.

### 5.1 `clock.py`, `sessions.py`, `cadence.py`

- `Clock(ABC)`: `now_ms()`, `monotonic()`, `sleep_until(epoch_ms, wake)` (returns
  early when `wake()` is true). `WallClock` (≤ 1 s sleep slices so a stop flag is
  honoured), `TestClock(set, advance)`, `ReplayClock` (a `TestClock` the replay
  feed advances). Invariant: nothing in the package compares wall stamps to order
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

`Feed(ABC)`: `pull(tick_at_ms) -> FeedResult{status ∈ {live, degraded, stale,
dead, closed}, acq_id, records_added, pulled_at_ms, data_asof_ms,
source_config_hash}`. `FeedSpec{root, source, stream, source_config_hash,
source_config_version}` is derived only from the resolved entry node's
`serving_source(params)`; root is canonicalized with the same path resolver used
by that node. The serve document may select `pull: acquire | store` but cannot
restate any locator. Missing `serving_source`, a different normalized locator,
or source alias/hash/version drift refuses at plan and pull. Zero new rows is
valid; `stale` and `dead` derive from watermark thresholds, while connector,
link, or identity failure is immediately `dead`. `EntrySourceFeed` either calls
`run_acquisition(..., mode="live")` with the derived spec or reads its store with
`scan_stream`. `ReplayFeed` uses the recorded spec/input digests. Tests assert
the feed and entry node receive the exact same normalized root/source/stream
object, so two data paths cannot diverge.

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
  to `ancestors(heads) ∪ heads`; `foreach` is dropped (already expanded); a
  needed node carrying `$prev` refuses; `env`/`tracking`/`outputs` are dropped.
  The derived document's hash is recorded beside the run's hash on every
  `process` record.
- `Decider.prepare(release, registry, asof, base_run_dir)` re-verifies the release,
  admits every reachable node through `ServingExecutionPolicy`, and performs the
  immutable base pass. That pass may execute only `pure` nodes and
  manifest-verified `release_read` operations. The configured serving entry must
  be the sole `entry_read`, be a source root after expansion, and dominate every
  non-release-bound data-dependent path to every head. A bypassing source or any
  other mutable read refuses at `plan` rather than being frozen in the base pass
  or fetched after the gate. `read_entry(tick_at_ms) -> EntryBatch` executes only the
  serving entry under the declared window override. The loop validates its
  watermark, source hash and `inputs_digest` before
  `evaluate(entry_batch) -> HeadOutputs` may execute the entry's descendants
  through the heads. Those descendants are limited to `pure` or verifying
  `release_read`; the runner accepts the frozen entry output as a binding, so it
  cannot read a second, newer mutable snapshot after the watermark gate.
  The configured proposer first returns stable, unsized
  `Candidate{id, instrument, scope_keys}` values and extracts quotes from those
  immutable head outputs. Accounting expands every declared requirement over
  those candidate scope keys and snapshots against the quotes; only then does
  `propose(head_outputs, candidates, account_state) -> Decision` construct an
  ordered proposal list. A proposal must preserve its candidate id, instrument
  and declared scope keys; missing/extra/changed keys or duplicate ids refuse.
  Candidate/scope derivation is release-bound proposer code and cannot depend on
  mutable account state.
  Candidate, quote and proposal construction may not rerun a node.
- `Proposer(ABC)`: `candidates(head_outputs) -> list[Candidate]`,
  `proposals(head_outputs, candidates, state) -> list[Proposal]`,
  `quotes(head_outputs) -> list[Quote]` is pure and state-independent (default:
  `MarketRecord`-shaped rows via
  the records module's accessors). `IntentRows` (`output`, `fields` map,
  `default_tif`), `TargetPositions` (`output`, `fields`, diff against
  `state.positions`).
- `RecordedOutputs` must satisfy the replaced role's planner rules. The resolved
  default is to replay full recorded gate/stat-test outputs; absent or summarised
  evidence refuses rather than recomputing a training-time verdict on live data.
- Refusals at `plan`: entry node absent or `param` not in its `_PARAMS`; a head
  absent; the entry not a source root or not dominating every dynamic path; any
  non-entry mutable read; a `release_read` outside the manifest; a trainable
  without an artifact dir; an artifact older
  than `max_artifact_age`; a needed search node without a recorded winner; a
  `$prev` reference; unsafe/unknown node effects; import or code-fingerprint
  failure; source, artifact, serving-document, run, or release mismatch.

### 5.3.1 `release.py`

`ReleaseManifest` is immutable canonical JSON containing `doc_hash`, `run_hash`,
`serving_hash`, every artifact digest, every resolved class reference and code
fingerprint, adapter module/package digest, each artifact's creation timestamp
from its content-bound run sidecar, derived `FeedSpec`, source-config
hash/version, approval-verifier fingerprint, and lease fingerprint. `plan`
resolves and verifies those inputs once, writes `release.json`, then
computes `release_hash = canonical_hash(manifest)`. `serve`, arming, process,
tick, decision, intent and adoption records all name that hash. Startup re-hashes
every local input; every tick and immediately-before-submit check repeats content
hash and artifact-age validation against the injected clock, while every pull
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

- `Quote{instrument, bid, ask, mid, asof_ms}`.
- `Candidate{id, instrument, scope_keys}`.
- `Proposal{id, instrument, side ∈ {buy, sell, none}, qty | notional, limit | None,
  tif ∈ TIFS, expires_ms, reference_price, exposure, direction, confidence,
  prediction, baseline, expected_value, inputs_asof_ms, extra}`.
- `Finding{guard, measure, value, bound, window, scope_key, verdict, reason}`.
- `Intent{client_ref, proposal (final, possibly amended), created_ms}`.
- `Ack{client_ref, venue_ref, status ∈ STATUSES, ts_ms, filled_qty, avg_price, fee,
  reason, native}`; `OrderState` = `Ack` + `instrument, side, qty, remaining_qty,
  limit, tif, created_ms, updated_ms`; `Fill{fill_id, venue_ref, client_ref,
  instrument, side, qty, price, fee, fee_currency, liquidity ∈ {maker, taker,
  unknown}, status ∈ {pending, final, reversed}, ts_ms, native}`;
  `Position{instrument, qty, avg_cost, source ∈ {derived, venue}, native}`;
  `Balance{currency, total, available, native}`; `Settlement{instrument, outcome,
  qty, payout, fee, settled_ms, native}`.
- `Alert{fingerprint, severity ∈ SEVERITIES, status ∈ {firing, resolved}, summary,
  source, tick_id, at_ms, labels}`; `Verdict{status ∈ {ok, warn, alarm,
  insufficient}, statistic, threshold, n_ref, n_cur, window, slice, provisional}`.
- `EntryBatch{outputs, data_asof_ms, inputs_digest, source_config_hash}`;
  `TickStart{tick_id, tick_at_ms, release_hash}`;
  `DecisionPlan{plan_id, provenance_digests, original, final, findings,
  gate_results, scope_verdict, action_verdict, risk_state_digest, result}`;
  `ReductionPlan{release_hash, risk_state_digest, intents, intent_digests,
  expires_ms}`;
  `ReductionAuthorization{authority_id, release_hash, request_id,
  intent_digests, expires_ms}`;
  `ActPermit{authority_id, decision_plan_digest, release_hash, intent_digest,
  client_ref, instrument, action_class, authority_scope_digest,
  risk_state_digest, lease_scope, fencing_token,
  safety_epoch_digest, valid_until_ms, checked_at_ms}`.
  The safety-epoch digest covers the verified release result, calendar
  open/next-close boundary, feed/source/data-asof state, executor link state,
  health, breaker, rung, pending-control flag and lease token at
  `checked_at_ms`; a changed component invalidates it. `valid_until_ms` is the
  minimum hard time boundary, never a freshness promise.
- `MeasureEvidence{requirement_digest, value, sample_count, window_start_ms,
  window_end_ms, scope_key, effective_at_ms, known_at_ms, source_digests}`;
  `AccountState{asof_ms, balances, positions, working, measure_evidence{
  requirement_digest: {scope_key: evidence}}, source_digests}`.
  `AccountState.risk_digest()` hashes canonical balances, positions, working
  orders and evidence values/window bounds/source digests, but excludes
  observation-only `asof/effective/known` timestamps; freshness is checked
  separately, and any economic correction changes the source digest.

### 5.5 `guards.py`

- `Guard(ABC)`: class attribute `_PARAMS`; `validate_params` classmethod;
  `@abstractmethod check(proposal, state) -> Finding`. `state` is read-only:
  the validated `AccountState`, decision history, feed status and ages, calendar,
  breaker and rung. Stale or incomplete accounting evidence refuses.
- `GuardChain(guards)`: evaluates every guard against the original proposal and
  records every finding. Verdicts use
  `allow < warn < amend < refuse < hold < halt`; amendments can only reduce one
  declared scalable field, compose by the strictest monotone reduction, and
  conflict by refusing. The final candidate is re-run through every hard guard
  with amendment disabled; any remaining breach refuses or halts. `hold` queues
  with `ttl` and expires as `refuse` (phase 2:
  an `approve` verb); `halt` trips the breaker.
- `Limit(Guard)`: `measure` (registered name or class ref), `window` ∈ `{}` |
  `{duration}` | `{count}` | `{calendar: session|day|event}`, `bound` (`max`
  and/or `min`, decimal strings or ints, inclusive), `warn_at ∈ (0,1)`, `scope`
  ∈ `aggregate | per_key | {group: field}`, `include_working` (default true),
  `on_breach ∈ {refuse, amend, pause, hold, halt}` (`amend` only for scalable
  measures; `pause` needs `pause: {duration | calendar}`; `hold` needs `hold:
  {ttl}`).
- `Measure(ABC)`: deterministic
  `requirements(candidate, window, scope_key) -> tuple[EvidenceRequirement]`
  plus `value(proposal, state, window, scope_key) -> Decimal | float`.
  Proposal-local measures such as quantity return no evidence requirement;
  history/account-dependent measures declare every source value accounting must
  snapshot before sizing. A guard refuses if declared evidence is absent.
  Registry (stdlib): `quantity, notional, exposure, exposure_after,
  price_deviation, pnl, drawdown, consecutive_losses, decision_count,
  identical_count, direction_changes, open_orders, input_age_ms, feed_age_ms,
  confidence, bankroll_fraction, error_vs_realised`. A child's exposure formula is
  a `Measure` subclass referenced by path.
- `RangeGuard(Guard)`: `field`, `min`, `max`, `nan ∈ {refuse, allow}`.
- Cancels never pass through the chain (a structural rule pinned by a test).
- After ordinary guards, `GuardChain.check_authority_scope` re-runs the active
  ordinary-arm or reduction-authorization allowlist and overlay against the exact
  final proposal immediately before permit.

### 5.6 `breaker.py` and `arming.py`

- `Breaker`: states `active | reducing | halted`; `trip(reason, actor)` from a
  guard `halt`, `feed.dead`, `executor.link_lost`, `reconcile.mismatch`,
  `operator`; `reduce(actor)`; `reset(actor, acknowledges_trip_id)` refused
  before `cooling_off_s` elapses or without a trip id; state persisted in
  `breaker.json` and reloaded before `READY`; every transition is a `trip`
  record; the kill-switch file `HALT` in the serve root is polled every tick;
  on entering `halted` the loop cancels working orders per `execution.on_halt`
  and records the outcome vocabulary.
- `ArmRequest{release_hash, rung, allowlist, limits_overlay, requested_until_ms,
  request_proof}` and `ArmApproval{request_digest, approval_proof}`.
  `ApprovalVerifier(ABC).verify(canonical_bytes, proof, purpose) ->
  VerifiedPrincipal{id, proof_digest}`. It is resolved from the graded
  `arming.approval {uses, params}` object; params may name trust-root env vars
  but never contain secret material. Trust roots are public, content-digested
  inputs in the release; only private-key/service credentials stay secret.
  `deny-all` is the core shadow/paper default;
  a live plan requires a child class path. Construction resolves secrets once,
  performs no network I/O, validates trust-root shape, and its class/code/params
  fingerprint is release-bound. Maker and checker differ for ≥ `live_limited`;
  purposes are closed: `arm_request | arm_approval | reduce | flatten_request |
  flatten_approval | execute_flatten | resume | adopt`; role authorization is verifier-owned.
  expiry is mandatory and bounded by `arming.max_duration_s`; allowlists may only
  narrow and overlays must be provably stricter. `Arming` binds the release and
  proofs. `current` folds the ledger; `arming.json` is only a head-bound cache.
  Immediately before submit, current scope is applied to the exact intent and a
  bound `ActPermit` is issued; the executor recomputes/compares its digest.
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

- `Executor(ABC)`: `spec()` (default-deny knobs; secret knobs name env vars),
  `capabilities()` (`tifs`, `market_orders`, `notional`, `positions ∈ {venue,
  derived}`, `settlements`, `stream`, `replace ∈ {native, cancel_submit, none}`,
  `dedupe ∈ {replays, rejects, window, none}`, `units {qty, price, cash}`,
  `position_model ∈ {netting, hedging}`,
  `fencing ∈ {none, submit_token}`); `@abstractmethod check(config)`,
  `submit(intent, permit=None) -> Ack`, `cancel(ref) -> Ack`, `order(ref) -> OrderState`,
  `open_orders()`, `fills(since_ms, cursor=None)`, `balances()`; concrete
  `positions()` (fill-derived `PositionBook`, reversed fills undone),
  `settlements(since_ms)` (empty), `replace(ref, intent)` (cancel → terminal →
  submit with a new client ref), `events()` (none), `venue_time_ms()` (None),
  `cancel_all()` (iterates only refs this executor owns).
- `ShadowExecutor`: records nothing itself; `submit` returns
  `Ack(status="not_sent", reason="shadow")`; any socket use raises (pinned by a
  monkeypatched test).
- `PaperExecutor`: fed `on_quote(Quote)` by the loop; knobs `fill_rule ∈ {touch,
  cross, mid}`, `slippage {bps, ticks, tick}`, `resting_rule ∈ {touch, through}`,
  `p_fill_on_touch`, `queue_frac`, `size_cap ∈ {none, quote_size, frac}`,
  `latency_ms {submit, cancel}`, `fees {kind ∈ none | per_unit | bps |
  maker_taker_bps | pxq_rate}` (a strategy class per kind), TIF handling (`ioc`,
  `fok`, `gtd`; `day` refused without `session_end_ms`), `seed`, `partial_fills`.
  Deterministic under `seed`; no wall clock, no network.
- `RecordedExecutor`: replays the tape's acks/fills for replay parity.
- `LiveExecutor` can always construct its read/query/cancel channel. Submission
  is a concrete wrapper that recomputes the intent digest, validates authority,
  release, accounting, safety-epoch, `valid_until_ms` and current fencing-token
  bindings immediately before network I/O, then calls the child's
  abstract `_submit_native(intent, fencing_token)`. Ordinary absent/stale arming
  raises `NotArmed`; an exact current `ReductionAuthorization` is accepted.
  Failures never disable reconciliation or cancellation.

- `executor_conformance_suite(cls, params, quotes)`: a pytest class builder (the
  node `conformance_suite` precedent) running the 15-item battery [R8 §2.5]:
  default-deny spec; `check` performs no submit; `client_ref` echoed; same
  `client_ref` twice ⇒ same `venue_ref` or `DuplicateRef`; terminal absorption;
  `filled_qty` monotone except reversed; capability gating before I/O; no
  duplicate `fill_id`; units pinned; derived vs venue positions agree; unarmed ⇒
  `NotArmed`; shadow has no network; paper deterministic; stale/mismatched intent
  and fence tokens refuse; two instances prove a stale fencing token cannot act.

### 5.7.1 `accounting.py`

`Accounting(ABC).snapshot(ledger_state, executor, quotes, at_ms, requirements,
calendar) -> AccountState` is independent of execution. `requirements` is the
deduplicated canonical union returned by every configured measure for every
candidate × window × scope, including all duration/count/session/day/event
boundaries, expanded over every candidate `scope_key`. Each required pair has one fresh
`MeasureEvidence`; missing evidence refuses. Implementations
fold timestamped fills, cash flows, marks and superseding corrections as-of the
tick, explicitly reversing busted fills, and include the starting baseline for
each window. Quotes must satisfy `max_valuation_age_ms`. Core
`PaperAccounting` and `RecordedAccounting` are deterministic. Live requires a
child implementation; ambiguous conversion, stale evidence or unsupported
requirements refuse at plan/tick.

### 5.7.2 `coordination.py`

`Lease(ABC)` has `acquire(scope, holder, ttl_ms) -> LeasePermit`,
`renew(permit) -> LeasePermit`, `current(scope) -> LeasePermit | None`, and
`release(permit)`; `LeasePermit` contains scope, holder, monotonic
`fencing_token` and expiry. The live scope is the canonical venue/account
ownership domain, not a release id, so old and new releases contend. Core
`ProcessLease` is valid only
for shadow/paper. Every live plan must resolve a child lease class from graded
`coordination.lease {uses, params}`, acquire it before reconciliation, renew it
independently of ticks, and pass the current token through `ActPermit`.
`LiveExecutor.capabilities().fencing` must be `submit_token` and the child
gateway must atomically reject stale tokens. Loss/renewal failure makes health
unhealthy and disables submit while preserving query/reconcile/cancel. Renewal
runs in a supervised worker with a bounded call timeout; the document requires
`ttl_ms > 2 * (renew_every_ms + renew_timeout_ms)`, and any missed renewal
deadline invalidates the local permit without waiting for nominal expiry.

### 5.8 `ledger.py`

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
  {every, batch:{n, ms}, none}` (`none` legal only at `shadow`); `flock` for the
  process lifetime; torn-tail recovery and segment continuity; directory fsync
  on segment creation; never copytruncate. `barrier()` flushes through fsync.
  Every `tick_start` before work, decision plan and intent before submit, and
  arming, breaker, authorization/use, reduction/reset or adoption transition crosses it regardless
  of batch policy. Only the process holding `serve.lock` may open the ledger for
  append; there is never a concurrent CLI ledger writer.
- `ControlInbox` (implemented in `control.py`) is the sole write path from a
  control CLI to a running serve process. The CLI canonicalizes the command,
  assigns a content-addressed `request_id`, writes a unique
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
  idempotent by `request_id` and payload digest. If no serve process owns the
  lock, non-executing commands may acquire it and run the same `CommandProcessor`
  synchronously. `execute-flatten` requires an active ready loop.
- `halt` additionally creates the out-of-band `HALT` sentinel atomically
  before queueing its audit command. The loop and its independent control worker
  poll that sentinel at subsecond cadence; the worker only sets an in-process
  stop flag and never appends. Thus stopping does not depend on the
  decision loop, inbox health, or ledger availability; startup observes it before
  reconciliation/action. Later processing records the halt when possible.
- The file lock protects only processes sharing that filesystem. Every live plan
  requires the configured fenced child `Lease` from §5.7.2; a local lock or
  unfenced lease can never be configured as sufficient.
- `Checkpoint` is an atomic cache of `release_hash, last_tick_at,
  last_completed_tick_at, pending[], positions_snapshot_at, schema_version`.
- Serve root layout:

```
<ledger.root>/<name>/   stable serve-series / deployment root
├── arming.json        optional head-bound cache; authority is the ledger fold
├── breaker.json       optional head-bound cache; authority is the ledger fold
├── checkpoint.json    optional head-bound cache; authority is the ledger fold
├── HALT               stable cross-release kill switch; absent = not halted by file
├── serve.lock         same-filesystem, cross-release writer lock
├── commands/
│   ├── inbox/         fsynced, content-addressed control requests
│   ├── applied/       terminal accepted command receipts
│   └── rejected/      terminal refused command receipts (ledger remains authoritative)
├── heartbeat.json     file heartbeat (mtime + status)
├── ledger/            ledger.0001.jsonl … (one chain across releases)
└── releases/<release_hash>/
    ├── document.json  the serve document verbatim
    ├── release.json   immutable ReleaseManifest
    └── process-<id>/base/  base-pass run dir (config/plan/resolved/nodes)
```

### 5.9 `reconcile.py`

`Reconciler.run(ledger_state, executor, scope) -> ReconReport{breaks[], status}`
resolves pending refs and compares open orders, fill-derived vs venue positions,
balances and settlements. Breaks are `timing | missing_in_ledger |
missing_at_venue | quantity | price | fee | state | settlement`, with severity
`info | warn | block`. Automatic policy is only `halt | refuse`; unknown venue
orders are `external`, never silently made ours. It runs before `READY`, on the
configured interval, and always appends a `recon` record without synthesising a
venue action. Adoption is a separate authenticated operator command naming the
break ids and release hash; after inspection it records the delta, crosses
`ledger.barrier()`, updates the fold, and immediately reconciles again.

### 5.10 `monitors.py`

- `Monitor(ABC)`: `_PARAMS`; `fit(reference)`; `@abstractmethod observe(record)`;
  `@abstractmethod verdict() -> Verdict`; `state()`/`restore(state)` (JSON-able,
  restored from the checkpoint).
- Strategies: `Reference` (`leading(n)`, `rolling(window)`, `snapshot(path)` — a
  saved `Profile`; phase 2 `run` over the run's predictions parquet via the
  parquet pack); `Chunker` (`count(n)`, `period(iso)`, `sliding(n, step)`);
  `Threshold` (`constant`, `reference_std(k)`, `alpha` — PSI benchmark
  `(1/n+1/m)·(B−1+z_α√(2(B−1)))` and the Kolmogorov series, both via
  `statistics.NormalDist`/`math`); `Response ∈ {log, warn, halt}` (phase 2:
  `fallback`, `rollback` as operator acts).
- Families (phase 1): `OperationalMonitor` → `Staleness`, `DecisionRate`,
  `Coverage` (abstention), `LatencyPercentiles`, `RefusalCount`;
  `StreamMonitor` → `PageHinkley`, `TrackingSignal`; `DistributionMonitor` →
  `PSI`, `KS` (bins from reference quantiles at `fit`). Phase 2: `DDM`, `ADWIN`,
  `JensenShannon`, `LInf`; `OutcomeMonitor` → `Calibration` (ECE), `Brier`
  (Murphy terms), `Skill` (BSS and Diebold–Mariano against the leg's stored
  `baseline`, reusing `dskit.pipeline` metrics/stats), `PredictionBias`;
  `ParityMonitor` (§5.13).
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
  one thread. Kinds: `log`, `memory`, `email` and `webhook`; endpoint values
  come from env-var names. Reachability is reported by supervised health probes,
  never constructor side effects.
- `AlertRouter`: fingerprint dedup; `group_wait_s` [0, 600] default 30;
  `repeat_interval_s` [60, 86400] default 14400; per-severity routes; token-bucket
  rate limit (`critical` bypasses the limit, not dedup); a bounded
  `queue.Queue` consumed by one worker thread; `put_nowait` overflow and every
  sink exception swallowed and counted (`alert_sink_failures_total`,
  `alerts_suppressed_total{why}`); `status: resolved` emitted on recovery. Phase
  2: inhibition, silences, escalation, `ack`, sqlite state across restarts.
- `SEVERITIES = ("info", "warning", "error", "critical")` pinned to PagerDuty,
  OTel `SeverityNumber` (9/13/17/21), syslog (6/4/3/2), `logging` (20/30/40/50).
- `Health`: `starting → ready | degraded | unhealthy → stopping`; `HealthProbe(ABC)`
  (`name`, `scope ∈ {local, dependency}`, `timeout_s`, `@abstractmethod check() ->
  ProbeResult`); kinds `ledger-writable` (local), `executor-check` (dependency),
  `feed-age` (dependency); `failure_threshold`/`success_threshold` hysteresis;
  transitions (not levels) raise alerts; `unhealthy` stops acting AND
  heartbeating; `degraded` observes and refuses acts.
- `Heartbeat` has its own supervised worker and cadence independent of tick
  duration; emitters are `file` and deadline-bound `url`. It is sent only in
  `ready` (or configured `degraded`) and stops in `unhealthy`. Phase 2 adds
  `systemd`.
- `flock(LOCK_EX | LOCK_NB)` prevents a second process on the same filesystem;
  the §5.8 deployment lease covers other hosts. Signals wake within 1 s;
  `ticking` finishes the phase and never stops between act and record-outcome;
  `shutdown_grace_s` [1, 300] must be under the supervisor's grace.

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
counted); `RateLimiter` (token buckets per scope, `max_in_flight` default 1 for
writes, `observe(headers)` capped by `MAX_BACKOFF_S`). It has distinct submit
and cancel lanes: cancel capacity is reserved and has priority, but is still
bounded. `cancel_all` sends sequentially; transient/429 outcomes retry only
within configured attempts/deadline while honoring capped Retry-After, ambiguous
outcomes query then reconcile, and exhaustion records `unknown` and makes health
unhealthy rather than flooding the venue. `Transport(ABC)`
(`send(method, url, headers, body, timeout{connect_s, read_s}) -> (status, headers,
body)`; `UrllibTransport`; `None` timeout refused). All take injected `clock`,
`sleeper`, `rng`. Phase 2: `Signer(ABC)` with `HmacSigner`, skew window, time
probe.

### 5.13 `loop.py`, `outcomes.py`, `report.py`, `readiness.py`

- `ServeLoop(document, release, clock, calendar, cadence, feed, decider, guards,
  breaker, arming, executor, accounting, lease, ledger, monitors, alerts, health,
  heartbeat, id_source)`: lifecycle `init → locked → leased → reconciling → ready →
  {waiting ⇄ ticking} → stopping → stopped`, plus persisted `halted` and
  restartable `faulted`. `IdSource` allocates deterministic tick ids before a
  `tick_start`; `ledger.barrier()` completes before any tick work. Phases are
  `gate`, `verify_release` (hashes plus artifact age), `fetch`, `read_entry`,
  `watermark`, `evaluate`, `candidates`, `quotes`, `account`, and
  `propose`. Proposals are sorted by stable candidate id and never
  pre-authorized as a batch. For each proposal, strictly sequentially:
  (1) run ordinary guards and any monotone amendment;
  (2) refresh the account snapshot with prior legs' working reservations/acks
  folded, and re-run hard guards and authority scope without amendment;
  (3) immediately re-evaluate release hashes/age, calendar, feed/link state,
  watermark age, venue skew, health, breaker, rung/action-matrix cell, a fresh
  risk snapshot, every hard guard, authority scope and lease—without rereading
  nodes or changing the recorded proposal;
  (4) append a `decision_plan` containing entry/head/quote/candidate digests,
  original/final proposals, every initial and fresh-check finding, gate/scope
  verdicts, action-matrix result and risk digest, then `ledger.barrier()`; a
  refusal terminalizes as `not_sent` without an intent;
  (5) for an allowed action, append the exact intent bound to that plan and
  `ledger.barrier()`;
  (6) for a live action, derive the permit from the current risk digest with
  `valid_until_ms` no later than proposal, data, calendar, applicable authority
  or lease expiry; for reduction first append/barrier `authority_use`, then
  append/barrier `authorization` (ordinary live appends/barriers authorization
  directly); shadow/paper create no live permit or authorization;
  (7) recheck the action-matrix cell and, for live, the safety epoch after that
  barrier, then call `submit`, whose live wrapper checks permit time/fence
  immediately before I/O;
  (8) record/fold the outcome before considering the next proposal.
  A changed risk digest alone is not a refusal: the unchanged intent is tested
  against the fresh state and the permit binds that state. A refusal terminates
  that intent as `not_sent`; an ambiguous outcome stops all later legs until
  reconciliation. Thus cumulative exposure, working orders, position/message
  limits and group scopes include every earlier leg. Finally the loop
  `observe`s and writes `checkpoint` last. Query,
  reconcile and cancel stay available in every rung/breaker/health state.
  A process crash cannot guarantee a `finally` write; startup folds unmatched
  plans/intents and `tick_start` records into terminal failed/recovered ticks and
  decisions, preserving recorded findings without rerunning them, before
  scheduling new work. Thus every started tick eventually has exactly one terminal
  `tick` and one `decision`, with status `decided | skipped:closed |
  skipped:stale | skipped:skew | skipped:halted | skipped:degraded |
  skipped:no_coverage | refused | failed` and all findings. Exit codes are
  0 stopped · 1 error · 3 halted · 4 already running. `--once` runs one tick;
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
- `readiness.py` (phase 2): a JSON checklist (`item`, `required`, `evidence`,
  `waiver`) evaluated to GO / NO-GO; NO-GO exits 3.

## 6. Ledger records

Envelope on every record: `kind, id, payload_digest, seq, process_id,
release_hash, recorded_at_ms, schema_version, prev_hash, hash`. Ledger-assigned
fields never enter `payload_digest`. `IdSource` derives tick, decision, leg and
model client ids from stable semantic inputs before append, independent of
sequence or wall time. Flatten refs are
`H("flatten-v1", release_hash, reduction_request_id, intent_index,
intent_digest)`; replay injects `RecordedIdSource`. Content uses the existing
sha256-canonical idiom.

| kind | one per | body |
|---|---|---|
| `process` | start / stop / recovered | `release_hash`, `doc_hash`, `serving_hash`, `run_hash`, `artifact_digests`, `source_config_hash`, `rung`, `executor_kind`, `code_version`, `journal_action_id`; on stop `head_seq`, `head_hash`, `exit_code` |
| `tick_start` | scheduled tick | `tick_id`, `tick_at_ms`, `release_hash` |
| `tick` | terminal tick | `tick_at`, `data_asof_ms`, `observed_at_ms`, `status`, `feed{status, acq_id, records_added, source_config_hash}`, `inputs_digest`, `calendar`, `latency_ms{fetch, evaluate, account, propose, guard, authorize, act}`, `health`, `breaker`, `rung`, `refusal_reason`, `error{class, text}` |
| `decision` | tick (exactly one) | `decision_plan_ids[]`, `decision_plan_digests[]`, `legs[]{leg_id, instrument, prediction, uncertainty, baseline, expected_value, decision_price, proposal, findings[], final, client_ref}` — a no-op tick has `final: none` per leg or zero legs with `reason` |
| `decision_plan` | proposal after complete pre-submit evaluation (barrier before proposal submit) | `plan_id`, entry/head/quote/candidate digests, original/final proposal, `findings[]`, `gate_results[]`, scope/action verdicts, `risk_state_digest`, `result ∈ {submit, not_sent}` |
| `intent` | proposal selected for possible submit | `decision_plan_id`, `decision_plan_digest`, `client_ref`, `leg_id`, `proposal (final)`, `created_ms`, `authority_id`, `release_hash`, `risk_state_digest` |
| `authorization` | permitted live intent (barrier before submit) | `decision_plan_digest`, `intent_digest`, `authority_id`, `authority_use_id | null`, `authority_scope_digest`, `risk_state_digest`, `lease_scope`, `fencing_token`, `safety_epoch_digest`, `valid_until_ms`, `checked_at_ms` |
| `control_request` | durable CLI request | `request_id`, `purpose`, canonical payload (full reduction intents when applicable), `release_hash`, derived principal/proof digest, expiry |
| `control_approval` | maker-checker approval | `request_id`, `purpose`, checker principal/proof digest, verified payload digest |
| `authority` | issue / disarm / revoke / expire | `authority_id`, `kind ∈ {ordinary, reduction}`, request/approval ids, release/rung, expiry, allowlist/overlay, reduction intent digests, reason |
| `authority_use` | consume/reserve one reduction right (barrier before authorization) | unique `(authority_id, intent_digest)`, `client_ref`, `reserved_at_ms`; recovery may reference this reservation but no second intent may |
| `order_event` | executor/loop report | `client_ref`, `venue_ref`, `event ∈ {not_sent, ack, reject, fill, partial_fill, cancel, expire, replace, unknown, status}`, `status`, `venue_ts_ms`, `recv_at_ms`, `reason` |
| `fill` | execution | the `Fill` record |
| `outcome` | label arrival / mark / correction | `leg_id`, `kind ∈ {settled, marked, voided, partial, corrected}`, `effective_at_ms`, `known_at_ms`, `value`, `weight`, `terminal`, `supersedes`, `source` |
| `recon` | reconciliation run | `scope`, `ours_digest`, `theirs_digest`, `breaks[]`, `status`, `action` |
| `trip` | breaker transition, including reduce/reset/halt | `from`, `to`, `reason`, `actor`, `control_request_id`, proof/principal digest, acknowledged trip id, `cancel_outcome` |
| `adoption` | authenticated venue-break adoption | `control_request_id`, principal/proof digest, named break ids, delta digest, before/after recon ids |
| `command_result` | consumed inbox request | `request_id`, `status ∈ {applied, rejected}`, emitted record ids, reason |
| `monitor` | verdict change / window close | `monitor`, `slice`, `window`, `statistic`, `threshold`, `status`, `provisional` |
| `alert` | firing / resolved | the `Alert` record + per-sink outcomes |
| `health` | transition | `from`, `to`, `cause`, `probe_evidence` |
| `snapshot` | every N records | `at_seq`, `state_digest`, `state` (positions, open orders, pending refs, monitor states) |

## 7. CLI — `python -m dskit.production`

| verb | does | exit |
|---|---|---|
| `validate <doc>` | shape and document identity | 0 / 1 |
| `plan <doc>` | derive/verify the serving document and emit the immutable release | 0 / 1 |
| `serve <doc> [--once] [--max-ticks N] [--armed]` | run the loop against the document's release | 0 / 1 / 3 / 4 |
| `arm-request <doc> --rung R --until TS --proof FILE [--allow I]…` | queue authenticated maker request | 0 / 1 |
| `approve-arm <doc> --request ID --proof FILE` / `disarm <doc>` | queue checker approval or safe demotion | 0 / 1 |
| `halt <doc> --reason` / `reduce <doc> --proof FILE` | set out-of-band halt and queue audit, or queue authenticated reducing transition | 0 / 1 |
| `flatten-request <doc> --plan FILE --proof FILE` / `approve-flatten <doc> --request ID --proof FILE` | queue maker-checker reduction plan/authorization | 0 / 1 / 3 |
| `execute-flatten <doc> --authorization ID --proof FILE` | queue authenticated execution of stored reduction intents by an active ready loop | 0 / 1 / 3 |
| `resume <doc> --acknowledge TRIP --proof FILE` | queue authenticated reset after cooling-off | 0 / 1 / 3 |
| `status <doc>` | rung, breaker, health, last tick, pending refs, control inbox/results, head hash | 0 |
| `verify <doc>` | walk the ledger chain; compare the head to the journal anchor | 0 / 1 |
| `reconcile <doc>` / `adopt <doc> --break ID… --proof FILE` | queue reconciliation, or queue authenticated adoption of named breaks | 0 / 1 / 3 |
| `replay <serve-dir>` | phase 2: parity report | 0 / 1 |
| `outcomes <doc>` / `report <doc> [--asof T]` | phase 2 | 0 |
| `ready <doc>` | phase 2: readiness GO / NO-GO | 0 / 3 |

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
│                      BREAK_CLASSES, DIVERGENCE_CLASSES, MONITOR_STATUSES, RESPONSES, FEED_STATUSES, OUTCOME_KINDS
├── document.py        ServeDocument sections including Accounting, Arming, Coordination; default-deny; exact non-identity paths
├── release.py         ReleaseManifest; class/code/adapter/source/artifact fingerprints; release verification
├── records.py         Quote, Candidate, Proposal, Finding, EntryBatch, TickStart, DecisionPlan, ReductionPlan, ReductionAuthorization,
│                      MeasureEvidence, AccountState, LeasePermit, ActPermit, Intent, execution/monitoring values
├── clock.py           Clock ABC; WallClock, TestClock, ReplayClock
├── sessions.py        Calendar ABC; AlwaysOpen, WeeklySessions, EventWindow, Composite; CALENDAR_KINDS
├── cadence.py         Cadence ABC; FixedInterval, AlignedBar, AtTimes, OnData; Overrun; CADENCE_KINDS
├── control.py         ControlInbox; CommandProcessor; atomic request/result spool; sole-writer dispatch
├── feed.py            FeedSpec from entry.serving_source; EntrySourceFeed; ReplayFeed; FeedResult; FEED_KINDS
├── decider.py         serving_document(); Decider (base pass + per-tick rerun via SubgraphRunner); Proposer ABC; IntentRows,
│                      TargetPositions; RecordedOutputs (replayed gate / stat_test); PROPOSER_KINDS
├── guards.py          Guard ABC; Finding lattice; GuardChain; Limit; RangeGuard; Measure ABC + MEASURE_KINDS; windows; GUARD_KINDS
├── breaker.py         Breaker (active | reducing | halted), persisted; trips; kill-switch file; cooling-off
├── arming.py          authenticated request/approval proofs; ApprovalVerifier; Arming fold; ActPermit; NotArmed
├── executor.py        Executor ABC; LiveExecutor submit permission; PositionBook; ShadowExecutor; PaperExecutor (+ fill/fee
│                      strategies); RecordedExecutor; executor_conformance_suite; EXECUTOR_KINDS
├── accounting.py      Accounting ABC; PaperAccounting; RecordedAccounting; ACCOUNTING_KINDS
├── coordination.py    Lease ABC; ProcessLease (non-live); LeasePermit; LEASE_KINDS
├── resilience.py      Classifier ABC + HttpClassifier; Retry (+ budget); CircuitBreaker; RateLimiter; Transport ABC + UrllibTransport
├── ledger.py          Ledger ABC + barrier; JsonlLedger; Checkpoint caches; ServeRoot; envelope + chain + verify
├── reconcile.py       Reconciler; ReconReport; break classification; on_mismatch policy
├── monitors.py        Monitor ABC; Reference / Chunker / Threshold / Response strategies; Operational, Stream, Distribution families
│                      (phase 2 adds Outcome and Parity); MONITOR_KINDS
├── alerts.py          AlertSink ABC; LogSink, MemorySink, EmailSink, WebhookSink; AlertRouter; SINK_KINDS
├── health.py          Health state machine; HealthProbe ABC + probes; Heartbeat emitters; single-instance lock; signal handling
├── loop.py            ServeLoop; Tick (phase hooks); lifecycle states; exit codes; journal row per process
├── outcomes.py        [phase 2] outcome join (settlements, strict forward as-of), supersede chain, as-of cut
├── report.py          [phase 2] attribution, calibration, drawdown, replay parity diff, markdown/JSON emitters
├── readiness.py       [phase 2] JSON checklist → GO / NO-GO
├── libs/
│   ├── __init__.py
│   ├── sqlite.py      [phase 2] SqliteLedger (WAL + synchronous=FULL pinned; append-only triggers)
│   └── parquet.py     [phase 2] RunReference over the run's predictions parquet (pyarrow inside the method)
├── README.md          what it does, how to write a serve document, how to build an executor / proposer / measure / guard / monitor / sink; tree
├── AGENTS.md          package-scoped design, safety and testing instructions; tree
└── CLAUDE.md          conventions, extension points, gotchas; tree

tests/production/
├── conftest.py            a synthetic training run (the pipeline's synthetic kinds + an observations-reading data node over a temp
│                          onboarding root), a TestClock, a MemorySink — every test builds on these, no network anywhere
├── test_purity.py         static + behavioural: stdlib + dskit.pipeline + dskit.onboarding + dskit.assets + self; journal function-import only;
│                          no `mode ==` / `rung ==` branch in loop.py
├── test_vocab.py          every vocabulary closed; the severity level map pinned; lattice order pinned
├── test_document.py       default-deny at every level; golden identity hash; non-identity exclusion; omission discipline; load/save round trip
├── test_release.py        all release inputs bound; mutation/source drift refuses; exact non-identity paths; code/adapter fingerprints
├── test_records.py        Decimal at the boundary; non-finite refused; DecisionPlan/provenance and other value round trips
├── test_clock.py          TestClock determinism; WallClock honours the stop flag; monotonic pacing survives a wall-clock jump
├── test_sessions.py       weekly sessions across spring-forward and fall-back; holidays, special closes, blackouts, buffers; DST-gap refusal
├── test_cadence.py        FixedInterval zero drift over 10^6 ticks with slow handlers; AlignedBar publish delay; AtTimes; overrun policies
├── test_feed.py           entry-derived normalized locator; no restatement; source drift; zero-row staleness; ReplayFeed
├── test_decider.py        serving_document derivation; entry dominance; sole entry_read; manifest-only release_read; pure descendants;
│                          exact FeedSpec equality; watermark before descendants; evaluate → candidates/scopes → quotes → account → propose
│                          ordering without a second mutable read
├── test_guards.py         one refusal per knob; lattice composite = max; every finding carries value/bound/reason; include_working; calendar
│                          windows; amend never exceeds bound; hypothesis: day_loss halts before the period loss exceeds bound − max single loss;
│                          cancels bypass the chain
├── test_breaker.py        persisted trips with request/proof ids; authenticated/barriered reduce/reset; resume requires a fresh arm; halt cancel outcomes
├── test_arming.py         maker-checker; verifier construction/fingerprint; exact-intent scope application; reduction rights/use replay; tighten-only; expiry
├── test_executor.py       conformance; unarmed recovery verbs; exact permit/intent and fencing-token checks; paper matrix; NotArmed
├── test_accounting.py     every duration/count/calendar × scope-key evidenced; stable risk digest vs timestamps; economic corrections; freshness; reducing proof
├── test_coordination.py   process lease rejected live; cross-release contention; two instances; monotonic fencing; stale token/renewal loss disables submit
├── test_control.py        concurrent CLI inbox writes; sole writer; HALT; journal coverage; deterministic flatten refs/retry; partial recovery
├── test_resilience.py     ambiguous writes; retry cap; reserved cancel lane; bounded cancel_all 429/timeout/query/reconcile behavior
├── test_ledger.py         chain/idempotency; tick_start/decision-plan/intent/authority-use/authorization/control barriers; caches; writer lock
├── test_reconcile.py      every break class; pending refs; automatic halt/refuse only; explicit authenticated adoption then re-reconcile
├── test_monitors.py       PSI = 0 on identical samples and χ² scaling; KS hand case; PageHinkley alarms on a shift, not on noise; tracking signal;
│                          insufficient below min_n; last partial chunk never ok; state round-trip; deterministic verdict records
├── test_alerts.py         sink failure never kills the loop; hanging sink bounded (never-replying local socket); construction refusal; dedup /
│                          group_wait / repeat / rate limit / critical bypass; resolved emitted
├── test_health.py         transitions and hysteresis; unhealthy stops heartbeating; heartbeat rid = tick id; second instance exits 4; SIGTERM
├── test_loop.py           full matrix; time-sensitive gates; cumulative multi-leg risk; durable findings/provenance before action;
│                          crash recovery from plan/intent/authorization; one action/client ref
├── test_main.py           release, control proofs, every mutating verb journaled, queued receipts, recovery verbs, no semantic overrides
├── test_outcomes.py       [phase 2] forward join strictness (hypothesis: label asof > decided_at); vintage reproducibility; supersede chain
├── test_report.py         [phase 2] IS components sum; Murphy terms sum to Brier; BSS of baseline vs itself = 0; parity diff classes
└── test_readiness.py      [phase 2] NO-GO exits 3; waivers

examples/production/
├── serve-shadow.json      the synthetic run served at shadow — the 60-second path
├── serve-paper.json       the same with the paper executor and basic guards
└── calendar-weekly.json   a weekly-sessions calendar with holidays and buffers
```

## 9. Changes outside the package

### 9.1 Pipeline — ADR-0088: the subgraph re-execution seam becomes public

`dskit/pipeline/driver.py`: extract `_SearchSeam._execute` into a public
`SubgraphRunner(plan, node_outputs, splits_info, prev, policy)` with
`rerun(overrides, ctx, bindings=None) -> (outputs, reran, seconds)`,
and a classmethod `prepare(document, asof, registry, run_dir, heads)` that runs
LOAD → PLAN → RESOLVE and calls policy before any reachable node can execute.
`ExecutionPolicy.refuse_node(node_class, phase)` rejects effects by class.
`ServingExecutionPolicy` recognizes class-level
`SERVING_EFFECTS ∈ {pure, release_read, entry_read}`; missing, legacy `read` and
all other effects refuse, and JSON cannot override it. `release_read` must use
the manifest-verifying loader. The sole `entry_read` must be a source root and
dominate every non-release-bound data-dependent path to every head. Its base
pass excludes that entry and descendants. Each tick `read_entry` executes only
the entry; after the caller validates its watermark, `rerun` executes descendants
using that frozen output in `bindings` and permits only `pure` or verified
`release_read` effects.
Serving-eligible built-ins are annotated in the same change:
`ObservationRows` is `entry_read`; deterministic transforms are `pure`;
load-capable trainables are `release_read` and are accepted only in effective
`load` mode with artifact access routed through the release loader;
`RecordedOutputs` is pure after its release-bound evidence is materialized.
Any class requiring other mutable I/O remains unannotated and therefore
refuses at plan.
`SearchExecutionPolicy` preserves `unsearchable_space_why` verbatim.
`_SearchSeam` keeps `needed`, `dirty`, the objective dig and `calls`, and
delegates execution — behaviour-neutral for search, pinned by the existing
`test_driver.py` / `test_kinds_search.py` suites and by every identity hash in
the repo (218 + 2 pinned) staying unmoved. No new params, no grammar change.

`dskit/pipeline/libs/observations.py`: `ObservationRows.serving_source(params)`
is the public, side-effect-free source-binding seam. It returns the raw
`{root, source, stream}` used by `stream()`; production resolves and
canonicalizes that binding, loads its source hash/version, and rejects any
disagreement. The pipeline module does not import `dskit.production`, avoiding
a dependency cycle. Any other serving entry class must declare the same method
or planning refuses. Its exact-binding tests sit beside the observation-kind and
production feed tests.

### 9.2 Skeleton (pin updated in the same commit)

`children/_skeleton/yourproject/execution.py` supplies a `LiveExecutor` template
with read/query/cancel available and permit-gated submit; `accounting.py`,
`approvals.py`, and `coordination.py` supply refusing live accounting,
proof-verification, and fenced-lease templates. `configs/serve-sample.json`
serves `run-sample.json` at shadow; `tests/test_execution.py` runs conformance
and `tests/test_production.py` proves the sample validates/plans while every
live-capable template remains fail-closed. `children/README.md` and the skeleton
`README.md` / `AGENTS.md` / `CLAUDE.md` gain a production section using
`python -m dskit.production serve configs/serve-sample.json --once`. Existing
children are not rewritten by this ADR (§12.4).

### 9.3 Documentation and configuration

Root `README.md` (a fifth pillar, "Serve"), root `CLAUDE.md` (layout tree,
commands, the exit-code line), `docs/architecture/README.md` (package table),
`TODO.md` (the "Long-term goal — a generic SERVING LOOP" section marked
superseded by ADR-0087 with its constraints listed as satisfied), `pyproject.toml`
(no new extras in phase 1; the new modules follow the docstring standard, so no
`per-file-ignores` entries), `docs/architecture/decision-log.md` (ADR-0087,
ADR-0088).

## 10. Test plan (TDD)

Order per module: the Opus test author writes `tests/production/test_<module>.py`
from §5–§7 (contracts, vocabularies, bounds, invariants) — red; Fable implements
`dskit/production/<module>.py` — green; Opus reviews the pair. Module order
follows dependencies: `vocab` → `base` → `records` → `document` → `release`
→ `clock` → `sessions` → `cadence` → `ledger` → `control` → `accounting` → `guards` →
`breaker` → `arming` → `coordination` → `executor` → `resilience` → `feed` → pipeline
`SubgraphRunner` → `decider` → `reconcile` → `monitors` → `alerts` →
`health` → `loop` → `__main__` → docs → examples → skeleton.

Invariants every phase must keep green: the three existing purity gates and the
new one; every pinned identity hash unmoved (218 pipeline + 2 pmquant); the full
suite; `ruff` clean; the skeleton pin.

The e2e (in `conftest.py`): build a synthetic run with `run_document` over a temp
onboarding root filled by the `FakeConnector`; serve it for three ticks at
`shadow` with `TestClock`, then at `paper`; assert paper submits without an
`ActPermit` and never constructs `LiveExecutor`. A separate all-fake
`live_limited` case uses distinct maker/checker proofs and a two-instance
fenced lease to prove stale-token rejection. Across them, assert one terminal
`tick` and `decision` per `tick_start`, the chain and release verify, and the journal row
anchors the head. Replay must reproduce exact semantic decision payloads under
`RecordedIdSource`; ledger envelope timestamps/sequences/hashes are verified by
their own deterministic chain assertions.

## 11. Build phases and model assignment

| phase | lands | model |
|---|---|---|
| 1 — foundation | every module in §8 not marked phase 2, ADR-0088, all §7 safety/control CLI verbs, README/AGENTS/CLAUDE, examples, skeleton, doc updates | tests: Opus · code: Fable · review: Opus |
| 2 — evidence | `outcomes`, `report`, `readiness`, `replay` verb, Outcome + Parity monitor families, DDM/ADWIN/JS/Linf, alert inhibition/silences/escalation/ack + sqlite state, systemd heartbeat, `libs/sqlite.py`, `libs/parquet.py`, `Signer`, the `approve` verb for `hold` | tests: Opus · code: Opus · review: Opus |
| 3 — packs | `exchange_calendars`, `prometheus`/`opentelemetry` sinks, the stream seam, migrating onboarding packs onto `resilience.py` (own ADR) | Opus |

Per the owner: no Fable after the initial plan and build.

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

## Appendix A — ADR-0087 as it will appear in `decision-log.md`

```
## ADR-0087 — `dskit.production`: the production layer (serve, guard, act, record, monitor)

**Status:** proposed (2026-09-04; Opus-reviewed; awaiting owner approval)

**Context.** dskit runs documents in batch and has no seam for running a fitted
model forward on a cadence. The only forward loop is `children/intraday_poc/
live.py` — 1,368 hand-rolled tier-3 lines where most of that child's HIGH-severity
defects live. TODO.md records the constraints a generic loop must satisfy: read
the run's configs, fetch through the connector, decide with the same node objects,
one decision record per tick, gate on a supplied calendar, take an executor
object, and make moving money an explicit loud act. Nine research reports
(docs/new_package_proposals/research_reports/) ground the design.

**Decision.** A fifth package, `dskit/production/`, per
docs/new_package_proposals/production.md (D1–D24, structure §8): a serve document
whose immutable release binds the run, serving graph, artifacts, resolved code,
adapter, approval verifier, fenced lease and source config. Feed identity is
derived from the resolved entry node rather than restated. Startup, every tick,
and pre-submit verify content and artifact age. One `ServeLoop` injects separate
execution, accounting and coordination seams. Serving requires one source-root
`entry_read` that dominates every dynamic path; only pure or manifest-verified
release reads can run around it. The loop checks its watermark before descendants.
It evaluates heads once, derives candidate scope keys and quotes,
then snapshots correction-aware accounting before sizing. Legs run sequentially
with prior reservations folded; every calendar/freshness/link/health/breaker/risk/
authority/lease gate is rechecked immediately before action. An explicit action
matrix preserves shadow and paper while live submits require an exact-intent
maker-checker permit and a fresh fencing token; cancel uses reserved bounded
transport capacity. Reduction, flatten execution, resume and adoption are
authenticated acts. A hash-chained ledger has one writer and barriers
`tick_start`, the complete decision plan and findings, intent, authority
use/authorization and safety transitions before effects. Flatten refs are
request/index/digest-derived; concurrent control CLIs use a durable inbox while HALT remains
out-of-band. Recovery gives every started tick a terminal decision and every
reserved reduction right a query-first resolution. Core supplies
shadow/paper/recorded execution and accounting,
monitors, alerts, health and resilience. Purity: stdlib + pipeline + onboarding +
assets + self; journal function-import only.

**Consequences.** Children stop owning loops: a child ships an executor subclass,
accounting, approval and fenced-lease implementations, optionally a
proposer/measures, and JSON.
The skeleton pin and README/AGENTS/CLAUDE trees update together. TODO's plan item
is superseded, but implementation remains unchecked until code lands. Phase 1
lands the foundation; phases 2–3 add evidence and packs. §12 records the resolved
defaults for this resubmission.
```

## Appendix B — ADR-0088 as it will appear

```
## ADR-0088 — The driver's subgraph re-execution is a public seam with a policy object

**Status:** proposed (2026-09-04)

**Context.** `_SearchSeam._execute` already re-executes `needed ∩ dirty` under
`"node.param.path"` overrides with the full node lifecycle, but it is private,
returns an objective float, and hardcodes the search override rule. A serving loop
needs the same mechanism with one different override rule and head outputs.

**Decision.** `SubgraphRunner` (public, `driver.py`) with `prepare(document, asof,
registry, run_dir, heads)` and `rerun(overrides, ctx, bindings)`.
`ExecutionPolicy` admits a node before every effect. `SearchExecutionPolicy`
preserves the current search restriction. `ServingExecutionPolicy` recognizes
`SERVING_EFFECTS ∈ {pure, release_read, entry_read}` and refuses every other
effect. The sole `entry_read` must be a source root and dominate every dynamic
path to the heads; `release_read` is limited to manifest-verified objects.
Entry-only execution is followed—only after the caller's watermark gate—by
descendant execution with the frozen entry binding, limited to pure and
verified release reads.
`_SearchSeam` delegates; no grammar, parameter, or hash changes.

**Consequences.** One mechanism, two callers; the search suites pin
behaviour-neutrality; every identity hash stays unmoved. Observation entry nodes
also expose side-effect-free `serving_source(params)`; production owns
normalization and verification, and the pipeline never imports production.
```
